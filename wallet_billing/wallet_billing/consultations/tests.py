from decimal import Decimal

from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import User
from billing.models import BillingTransaction
from config.factories import FakeClock, client_for, fund_wallet, make_user
from wallets.models import Wallet

from .models import ConsultationSession
from .state_machine import ALLOWED_TRANSITIONS

Status = ConsultationSession.Status


class SessionTestCase(TestCase):
    def setUp(self):
        self.clock = FakeClock()
        patcher = self.clock.patch()
        patcher.start()
        self.addCleanup(patcher.stop)

        self.user = make_user("asha@example.com")
        self.provider = make_user("dr.rao@example.com", User.Role.PROVIDER)
        self.user_api = self.api_for(self.user)
        self.provider_api = self.api_for(self.provider)

    def api_for(self, user):
        return client_for(user)

    def balance(self, user=None):
        return Wallet.objects.get(user=user or self.user).balance

    def reload(self, session_id):
        return ConsultationSession.objects.get(pk=session_id)

    def new_session(self):
        resp = self.user_api.post("/api/v1/sessions/private/create/")
        self.assertEqual(resp.status_code, 201, resp.content)
        return resp.data["id"]

    def assigned_session(self):
        sid = self.new_session()
        resp = self.user_api.post(f"/api/v1/sessions/private/{sid}/assign/", {"provider_id": self.provider.id}, format="json")
        self.assertEqual(resp.status_code, 200, resp.content)
        return sid

    def accepted_session(self):
        sid = self.assigned_session()
        self.assertEqual(self.provider_api.post(f"/api/v1/sessions/private/{sid}/accept/").status_code, 200)
        return sid

    def running_session(self):
        sid = self.accepted_session()
        resp = self.user_api.post(f"/api/v1/sessions/private/{sid}/start/")
        self.assertEqual(resp.status_code, 200, resp.content)
        return sid

    def post(self, api, sid, action):
        return api.post(f"/api/v1/sessions/private/{sid}/{action}/")


class MandatoryScenarioTests(SessionTestCase):
    def test_mandatory_1_balance_below_five_minutes_blocks_start(self):
        fund_wallet(self.user, "249.99")           # needs 5 x 50 = 250
        sid = self.accepted_session()

        resp = self.post(self.user_api, sid, "start")

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data["detail"].code, "insufficient_balance")
        session = self.reload(sid)
        self.assertEqual(session.status, Status.ACCEPTED)
        self.assertIsNone(session.started_at)
        self.assertIsNone(session.per_minute_rate)
        self.assertEqual(self.balance(), Decimal("249.99"))

    def test_mandatory_1_exactly_five_minutes_of_balance_is_enough(self):
        fund_wallet(self.user, "250.00")
        sid = self.accepted_session()
        self.assertEqual(self.post(self.user_api, sid, "start").status_code, 200)

    def test_mandatory_2_happy_path(self):

        reference = self.user_api.post("/api/v1/recharge/private/initiate/", {"amount": "300"}, format="json").data["reference"]
        callback = APIClient().post(
            "/api/v1/recharge/callback/", {"reference": reference, "status": "success"},
            format="json", HTTP_X_CALLBACK_SECRET="dev-callback-secret",
        )
        self.assertEqual(callback.status_code, 200)
        self.assertEqual(self.balance(), Decimal("300.00"))

        sid = self.accepted_session()
        started = self.post(self.user_api, sid, "start")
        self.assertEqual(started.data["status"], "in_progress")
        self.assertEqual(started.data["per_minute_rate"], "50.00")

        self.clock.advance(minutes=1)
        self.post(self.user_api, sid, "bill")
        self.clock.advance(minutes=1)
        billed = self.post(self.user_api, sid, "bill")
        self.assertEqual(billed.data["session"]["billed_minutes"], 2)
        self.assertEqual(self.balance(), Decimal("200.00"))

        ended = self.post(self.user_api, sid, "end")

        self.assertEqual(ended.status_code, 200)
        self.assertEqual(ended.data["status"], "completed")
        self.assertEqual(ended.data["ended_by"], "user")
        self.assertEqual(ended.data["end_reason"], "normal")
        self.assertEqual(ended.data["billed_minutes"], 2)
        self.assertEqual(ended.data["duration_seconds"], 120)
        self.assertEqual(self.balance(), Decimal("200.00"))          # 300 - 2 x 50
        self.assertEqual(BillingTransaction.objects.filter(session_id=sid).count(), 2)
        session = self.reload(sid)
        self.assertEqual((session.ended_at - session.started_at).total_seconds(), 120)

    def test_mandatory_4_ending_before_start_is_400_and_changes_nothing(self):
        fund_wallet(self.user, "500")
        sid = self.accepted_session()
        before = self.reload(sid)

        resp = self.post(self.user_api, sid, "end")

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data["detail"].code, "invalid_transition")
        self.assertIn("accepted_by_provider", str(resp.data["detail"]))
        after = self.reload(sid)
        self.assertEqual(after.status, before.status)
        self.assertIsNone(after.ended_at)
        self.assertEqual(after.ended_by, "")
        self.assertEqual(self.balance(), Decimal("500.00"))
        self.assertEqual(BillingTransaction.objects.count(), 0)


class StateMachineTests(SessionTestCase):
    def test_lifecycle_follows_the_required_order(self):
        fund_wallet(self.user, "1000")
        sid = self.new_session()
        self.assertEqual(self.reload(sid).status, "unassigned")
        self.user_api.post(f"/api/v1/sessions/private/{sid}/assign/", {"provider_id": self.provider.id}, format="json")
        self.assertEqual(self.reload(sid).status, "assigned")
        self.post(self.provider_api, sid, "accept")
        self.assertEqual(self.reload(sid).status, "accepted_by_provider")
        self.post(self.user_api, sid, "start")
        self.assertEqual(self.reload(sid).status, "in_progress")
        self.post(self.user_api, sid, "end")
        self.assertEqual(self.reload(sid).status, "completed")

    def test_transition_table_is_a_straight_line(self):
        self.assertEqual(ALLOWED_TRANSITIONS[Status.UNASSIGNED], {Status.ASSIGNED})
        self.assertEqual(ALLOWED_TRANSITIONS[Status.ASSIGNED], {Status.ACCEPTED})
        self.assertEqual(ALLOWED_TRANSITIONS[Status.ACCEPTED], {Status.IN_PROGRESS})
        self.assertEqual(ALLOWED_TRANSITIONS[Status.IN_PROGRESS], {Status.COMPLETED})
        self.assertEqual(ALLOWED_TRANSITIONS[Status.COMPLETED], set())

    def test_cannot_start_before_provider_accepts(self):
        fund_wallet(self.user, "1000")
        sid = self.assigned_session()
        resp = self.post(self.user_api, sid, "start")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(self.reload(sid).status, "assigned")

    def test_cannot_accept_before_assignment_or_start_unassigned(self):
        fund_wallet(self.user, "1000")
        sid = self.new_session()
        self.assertEqual(self.post(self.user_api, sid, "start").status_code, 400)
        self.assertEqual(self.post(self.user_api, sid, "end").status_code, 400)
        self.assertEqual(self.post(self.user_api, sid, "bill").status_code, 400)
        self.assertEqual(self.reload(sid).status, "unassigned")

    def test_cannot_assign_twice_or_accept_twice(self):
        sid = self.accepted_session()
        again = self.user_api.post(f"/api/v1/sessions/private/{sid}/assign/", {"provider_id": self.provider.id}, format="json")
        self.assertEqual(again.status_code, 400)
        self.assertEqual(self.post(self.provider_api, sid, "accept").status_code, 400)

    def test_completed_session_is_final(self):
        fund_wallet(self.user, "1000")
        sid = self.running_session()
        self.post(self.user_api, sid, "end")
        for api, action in [(self.user_api, "start"), (self.user_api, "end"), (self.provider_api, "end"),
                            (self.user_api, "bill"), (self.provider_api, "accept")]:
            with self.subTest(action=action):
                self.assertEqual(self.post(api, sid, action).status_code, 400)

    def test_user_cannot_have_two_sessions_in_progress(self):
        fund_wallet(self.user, "1000")
        first = self.running_session()
        second = self.accepted_session()

        resp = self.post(self.user_api, second, "start")

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data["detail"].code, "active_session_exists")
        self.assertEqual(self.reload(first).status, "in_progress")
        self.assertEqual(self.reload(second).status, "accepted_by_provider")

    def test_second_session_can_start_after_first_ends(self):
        fund_wallet(self.user, "1000")
        first = self.running_session()
        second = self.accepted_session()
        self.post(self.user_api, first, "end")
        self.assertEqual(self.post(self.user_api, second, "start").status_code, 200)

    def test_database_enforces_one_in_progress_session_per_user(self):
        fields = dict(user=self.user, status=Status.IN_PROGRESS, per_minute_rate=10, started_at=self.clock())
        ConsultationSession.objects.create(**fields)
        with self.assertRaises(IntegrityError), transaction.atomic():
            ConsultationSession.objects.create(**fields)

    def test_database_rejects_a_started_session_without_rate(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            ConsultationSession.objects.create(user=self.user, status=Status.IN_PROGRESS, started_at=self.clock())

    def test_rate_is_fixed_when_the_session_starts(self):
        fund_wallet(self.user, "1000")
        with override_settings(SESSION_RATE_PER_MINUTE=Decimal("20.00")):
            sid = self.running_session()
        with override_settings(SESSION_RATE_PER_MINUTE=Decimal("100.00")):   # default changes mid-session
            self.clock.advance(minutes=3)
            resp = self.post(self.user_api, sid, "end")
        self.assertEqual(resp.data["per_minute_rate"], "20.00")
        self.assertEqual(self.balance(), Decimal("940.00"))                  # 3 x 20, not 3 x 100

    def test_client_cannot_choose_the_rate(self):
        resp = self.user_api.post("/api/v1/sessions/private/create/", {"per_minute_rate": "1.00", "status": "completed"}, format="json")
        self.assertEqual(resp.status_code, 201)
        self.assertIsNone(resp.data["per_minute_rate"])
        self.assertEqual(resp.data["status"], "unassigned")

    def test_user_end_and_provider_end_are_recorded(self):
        fund_wallet(self.user, "1000")
        by_user = self.running_session()
        self.clock.advance(minutes=1, seconds=30)
        resp = self.post(self.user_api, by_user, "end")
        self.assertEqual((resp.data["ended_by"], resp.data["end_reason"]), ("user", "normal"))
        self.assertEqual(resp.data["duration_seconds"], 90)
        self.assertEqual(resp.data["billed_minutes"], 1)

        by_provider = self.running_session()
        self.clock.advance(minutes=2)
        resp = self.post(self.provider_api, by_provider, "end")
        self.assertEqual((resp.data["ended_by"], resp.data["end_reason"]), ("provider", "normal"))
        self.assertEqual(resp.data["billed_minutes"], 2)


class PermissionTests(SessionTestCase):
    def test_provider_cannot_create_assign_or_start(self):
        self.assertEqual(self.provider_api.post("/api/v1/sessions/private/create/").status_code, 403)
        sid = self.accepted_session()
        self.assertEqual(self.post(self.provider_api, sid, "start").status_code, 403)
        sid2 = self.new_session()
        resp = self.provider_api.post(f"/api/v1/sessions/private/{sid2}/assign/", {"provider_id": self.provider.id}, format="json")
        self.assertEqual(resp.status_code, 403)

    def test_user_cannot_accept(self):
        sid = self.assigned_session()
        self.assertEqual(self.post(self.user_api, sid, "accept").status_code, 403)
        self.assertEqual(self.reload(sid).status, "assigned")

    def test_only_the_assigned_provider_can_accept(self):
        other_provider = make_user("dr.other@example.com", User.Role.PROVIDER)
        sid = self.assigned_session()
        resp = self.post(self.api_for(other_provider), sid, "accept")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(self.reload(sid).status, "assigned")

    def test_other_users_cannot_see_or_change_a_session(self):
        fund_wallet(self.user, "1000")
        stranger = make_user("stranger@example.com")
        api = self.api_for(stranger)
        sid = self.accepted_session()
        self.assertEqual(api.get(f"/api/v1/sessions/private/{sid}/details/").status_code, 404)
        self.assertEqual(self.post(api, sid, "start").status_code, 404)
        self.assertEqual(self.post(api, sid, "end").status_code, 404)
        self.assertEqual(self.reload(sid).status, "accepted_by_provider")

    def test_participants_can_view_the_session(self):
        sid = self.assigned_session()
        self.assertEqual(self.user_api.get(f"/api/v1/sessions/private/{sid}/details/").status_code, 200)
        self.assertEqual(self.provider_api.get(f"/api/v1/sessions/private/{sid}/details/").status_code, 200)

    def test_assign_needs_an_existing_provider(self):
        sid = self.new_session()
        for bad in (self.user.id, 99999, "abc"):
            with self.subTest(provider_id=bad):
                resp = self.user_api.post(f"/api/v1/sessions/private/{sid}/assign/", {"provider_id": bad}, format="json")
                self.assertEqual(resp.status_code, 400)
        self.assertEqual(self.reload(sid).status, "unassigned")

    def test_provider_cannot_start_a_session_on_users_behalf_even_when_assigned(self):
        fund_wallet(self.user, "1000")
        sid = self.accepted_session()
        self.assertEqual(self.post(self.provider_api, sid, "start").status_code, 403)
