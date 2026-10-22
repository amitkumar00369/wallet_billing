from datetime import timedelta
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Sum
from rest_framework.test import APIClient

from accounts.models import User
from config.factories import fund_wallet, make_user
from consultations import services as session_services
from consultations.models import ConsultationSession
from consultations.exceptions import InvalidTransition
from consultations.tests import SessionTestCase
from wallets.models import RechargeTransaction, Wallet

from . import services
from .models import BillingTransaction

Status = ConsultationSession.Status


class BillingTestCase(SessionTestCase):
    def started_at(self, sid):
        return self.reload(sid).started_at

    def charges(self, sid=None):
        qs = BillingTransaction.objects.all()
        return qs.filter(session_id=sid) if sid else qs


class MandatoryBillingScenarios(BillingTestCase):
    def test_mandatory_3_balance_runs_out_and_session_ends(self):
        fund_wallet(self.user, "260.00")        # five minutes cost 250, the sixth cannot be paid
        sid = self.running_session()

        self.clock.advance(minutes=5)
        resp = self.post(self.user_api, sid, "bill")
        self.assertEqual(resp.data["session"]["status"], "in_progress")
        self.assertEqual(self.balance(), Decimal("10.00"))

        self.clock.advance(minutes=1, seconds=10)
        resp = self.post(self.user_api, sid, "bill")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["minutes_charged"], 0)
        session = resp.data["session"]
        self.assertEqual(session["status"], "completed")
        self.assertEqual(session["ended_by"], "system")
        self.assertEqual(session["end_reason"], "insufficient_balance")
        self.assertEqual(session["billed_minutes"], 5)
        self.assertEqual(session["duration_seconds"], 300)
        self.assertEqual(self.balance(), Decimal("10.00"))          # unaffordable minute not deducted
        self.assertGreaterEqual(self.balance(), 0)
        self.assertEqual(self.charges(sid).count(), 5)
        obj = self.reload(sid)
        self.assertEqual(obj.ended_at, obj.started_at + timedelta(minutes=5))

    def test_mandatory_6_duplicate_billing_does_not_double_charge(self):
        fund_wallet(self.user, "500")
        sid = self.running_session()
        self.clock.advance(minutes=2)

        for _ in range(4):
            self.assertEqual(self.post(self.user_api, sid, "bill").status_code, 200)
        self.clock.advance(seconds=30)                              # still inside minute 3
        self.post(self.provider_api, sid, "bill")

        self.assertEqual(self.balance(), Decimal("400.00"))
        self.assertEqual(self.reload(sid).billed_minutes, 2)
        self.assertEqual(self.charges(sid).count(), 2)

    def test_mandatory_6_same_at_service_level(self):
        fund_wallet(self.user, "500")
        sid = self.running_session()
        now = self.started_at(sid) + timedelta(minutes=2)

        first = services.bill_session(sid, now=now)
        second = services.bill_session(sid, now=now)

        self.assertEqual((first.minutes_charged, second.minutes_charged), (2, 0))
        self.assertEqual(second.amount_charged, Decimal("0"))
        self.assertEqual(self.balance(), Decimal("400.00"))


class BillingPolicyTests(BillingTestCase):
    def test_minute_boundaries(self):
        cases = [(0, 0), (59, 0), (60, 1), (61, 1), (119, 1), (120, 2), (179, 2), (180, 3)]
        for elapsed_seconds, expected_minutes in cases:
            with self.subTest(elapsed_seconds=elapsed_seconds):
                user = make_user(f"u{elapsed_seconds}@example.com")
                fund_wallet(user, "1000")
                session = ConsultationSession.objects.create(
                    user=user, provider=self.provider, status=Status.IN_PROGRESS,
                    per_minute_rate=Decimal("50.00"), started_at=self.clock(),
                )
                result = services.bill_session(session.pk, now=self.clock() + timedelta(seconds=elapsed_seconds))
                self.assertEqual(result.minutes_charged, expected_minutes)
                self.assertEqual(result.session.billed_minutes, expected_minutes)
                self.assertEqual(Wallet.objects.get(user=user).balance, Decimal("1000.00") - 50 * expected_minutes)

    def test_billable_minutes_helper_never_goes_negative(self):
        now = self.clock()
        self.assertEqual(services.billable_minutes(now, now - timedelta(seconds=5)), 0)

    def test_example_from_the_brief_three_minutes_at_fifty_is_150(self):
        fund_wallet(self.user, "500")
        sid = self.running_session()
        self.clock.advance(minutes=3)
        resp = self.post(self.user_api, sid, "bill")
        self.assertEqual(resp.data["amount_charged"], "150.00")
        self.assertEqual(self.balance(), Decimal("350.00"))

    def test_incomplete_minute_is_not_charged_but_real_duration_is_kept(self):
        fund_wallet(self.user, "500")
        sid = self.running_session()
        self.clock.advance(minutes=2, seconds=59)
        resp = self.post(self.user_api, sid, "end")
        self.assertEqual(resp.data["billed_minutes"], 2)
        self.assertEqual(resp.data["duration_seconds"], 179)
        self.assertEqual(self.balance(), Decimal("400.00"))

    def test_late_billing_catches_up_every_missed_minute(self):
        fund_wallet(self.user, "1000")
        sid = self.running_session()
        self.clock.advance(minutes=7)
        resp = self.post(self.user_api, sid, "bill")
        self.assertEqual(resp.data["minutes_charged"], 7)
        self.assertEqual(self.balance(), Decimal("650.00"))
        self.assertEqual(list(self.charges(sid).values_list("minute_number", flat=True)), list(range(1, 8)))

    def test_late_billing_stops_at_last_affordable_minute_and_backdates_the_end(self):
        fund_wallet(self.user, "260")
        sid = self.running_session()
        self.clock.advance(minutes=30)
        resp = self.post(self.user_api, sid, "bill")
        self.assertEqual(resp.data["minutes_charged"], 5)
        self.assertEqual(resp.data["session"]["status"], "completed")
        obj = self.reload(sid)
        self.assertEqual(obj.ended_at, obj.started_at + timedelta(minutes=5))
        self.assertEqual(self.balance(), Decimal("10.00"))

    def test_end_after_wallet_ran_dry_reports_a_system_end(self):
        fund_wallet(self.user, "250")
        sid = self.running_session()
        self.clock.advance(minutes=6)
        resp = self.post(self.user_api, sid, "end")        # no bill call in between
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["ended_by"], "system")
        self.assertEqual(resp.data["end_reason"], "insufficient_balance")
        self.assertEqual(self.balance(), Decimal("0.00"))

    def test_wallet_balance_matches_recharges_minus_charges(self):
        fund_wallet(self.user, "400")
        fund_wallet(self.user, "123.45")
        sid = self.running_session()
        self.clock.advance(minutes=4, seconds=20)
        self.post(self.user_api, sid, "end")
        credited = RechargeTransaction.objects.filter(status="success").aggregate(t=Sum("amount"))["t"]
        charged = BillingTransaction.objects.aggregate(t=Sum("amount"))["t"]
        self.assertEqual(self.balance(), credited - charged)


class BillingGuardTests(BillingTestCase):
    def test_completed_session_cannot_be_billed(self):
        fund_wallet(self.user, "500")
        sid = self.running_session()
        self.clock.advance(minutes=1)
        self.post(self.user_api, sid, "end")
        balance = self.balance()

        self.clock.advance(minutes=10)
        resp = self.post(self.user_api, sid, "bill")

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(self.balance(), balance)
        self.assertEqual(self.charges(sid).count(), 1)

    def test_session_that_never_started_cannot_be_billed(self):
        fund_wallet(self.user, "500")
        sid = self.accepted_session()
        self.clock.advance(minutes=5)
        self.assertEqual(self.post(self.user_api, sid, "bill").status_code, 400)
        self.assertEqual(self.balance(), Decimal("500.00"))

    def test_service_raises_for_non_in_progress_session(self):
        sid = self.accepted_session()
        with self.assertRaises(InvalidTransition):
            services.bill_session(sid)

    def test_only_participants_can_trigger_billing(self):
        fund_wallet(self.user, "500")
        sid = self.running_session()
        self.clock.advance(minutes=2)
        stranger = self.api_for(make_user("stranger@example.com"))
        self.assertEqual(self.post(stranger, sid, "bill").status_code, 404)
        self.assertEqual(self.post(APIClient(), sid, "bill").status_code, 401)
        self.assertEqual(self.balance(), Decimal("500.00"))
        self.assertEqual(self.post(self.provider_api, sid, "bill").status_code, 200)
        self.assertEqual(self.balance(), Decimal("400.00"))

    def test_charge_minute_with_a_stale_session_object_cannot_double_charge(self):
        fund_wallet(self.user, "500")
        sid = self.running_session()
        stale = self.reload(sid)                         # billed_minutes == 0 in memory
        now = stale.started_at + timedelta(minutes=1)

        self.assertIs(services.charge_minute(stale, 1, now), services.ChargeOutcome.CHARGED)
        self.assertIs(services.charge_minute(stale, 1, now), services.ChargeOutcome.ALREADY_BILLED)

        self.assertEqual(self.balance(), Decimal("450.00"))
        self.assertEqual(self.charges(sid).count(), 1)

    def test_charge_minute_without_funds_leaves_no_trace(self):
        fund_wallet(self.user, "250")
        sid = self.running_session()
        Wallet.objects.filter(user=self.user).update(balance=Decimal("10.00"))
        session = self.reload(sid)

        outcome = services.charge_minute(session, 1, session.started_at + timedelta(minutes=1))

        self.assertIs(outcome, services.ChargeOutcome.INSUFFICIENT_FUNDS)
        self.assertEqual(self.balance(), Decimal("10.00"))
        self.assertEqual(self.charges(sid).count(), 0)
        self.assertEqual(self.reload(sid).billed_minutes, 0)     

    def test_database_rejects_duplicate_minute_rows(self):
        fund_wallet(self.user, "500")
        sid = self.running_session()
        BillingTransaction.objects.create(session_id=sid, minute_number=1, amount=Decimal("50"))
        with self.assertRaises(IntegrityError), transaction.atomic():
            BillingTransaction.objects.create(session_id=sid, minute_number=1, amount=Decimal("50"))
