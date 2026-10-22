from decimal import Decimal

from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import User
from config.factories import client_for, fund_wallet, make_user

from . import services
from .models import RechargeTransaction, Wallet

SECRET = {"HTTP_X_CALLBACK_SECRET": "dev-callback-secret"}


class WalletTestCase(TestCase):
    def setUp(self):
        self.user = make_user("asha@example.com")
        self.api = APIClient()
        self.api = client_for(self.user)
        self.anonymous = APIClient()

    def balance(self):
        return Wallet.objects.get(user=self.user).balance

    def start_recharge(self, amount="500"):
        return self.api.post("/api/v1/recharge/private/initiate/", {"amount": amount}, format="json")

    def callback(self, reference, status, **payload):
        body = {"reference": reference, "status": status, **payload}
        return self.anonymous.post("/api/v1/recharge/callback/", body, format="json", **SECRET)


class WalletViewTests(WalletTestCase):
    def test_user_sees_own_balance(self):
        fund_wallet(self.user, "120.50")
        other = make_user("other@example.com")
        fund_wallet(other, "999")
        resp = self.api.get("/api/v1/wallet/private/balance/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["balance"], "120.50")

    def test_balance_cannot_be_set_through_the_api(self):
        self.assertEqual(self.api.put("/api/v1/wallet/private/balance/", {"balance": "9999"}, format="json").status_code, 405)
        self.assertEqual(self.api.patch("/api/v1/wallet/private/balance/", {"balance": "9999"}, format="json").status_code, 405)
        self.assertEqual(self.balance(), Decimal("0.00"))

    def test_database_refuses_negative_balance(self):
        wallet = Wallet.objects.get(user=self.user)
        wallet.balance = Decimal("-0.01")
        with self.assertRaises(IntegrityError), transaction.atomic():
            wallet.save()


class RechargeCreateTests(WalletTestCase):
    def test_creates_pending_recharge_without_touching_balance(self):
        resp = self.start_recharge("250.00")
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["status"], "pending")
        self.assertTrue(resp.data["reference"].startswith("rch_"))
        self.assertEqual(self.balance(), Decimal("0.00"))

    def test_rejects_zero_negative_and_garbage_amounts(self):
        for bad in ("0", "-10", "abc", "", "0.001"):
            with self.subTest(amount=bad):
                self.assertEqual(self.start_recharge(bad).status_code, 400)
        self.assertEqual(RechargeTransaction.objects.count(), 0)

    def test_amount_is_required(self):
        self.assertEqual(self.api.post("/api/v1/recharge/private/initiate/", {}, format="json").status_code, 400)

    def test_requires_authentication(self):
        resp = self.anonymous.post("/api/v1/recharge/private/initiate/", {"amount": "10"}, format="json")
        self.assertEqual(resp.status_code, 401)


class RechargeCallbackTests(WalletTestCase):
    def test_mandatory_5_duplicate_success_callback_credits_once(self):
        reference = self.start_recharge("500").data["reference"]

        first = self.callback(reference, "success")
        second = self.callback(reference, "success")
        third = self.callback(reference, "success")

        self.assertEqual((first.status_code, second.status_code, third.status_code), (200, 200, 200))
        self.assertTrue(first.data["credited"])
        self.assertFalse(second.data["credited"])
        self.assertFalse(third.data["credited"])
        self.assertEqual(self.balance(), Decimal("500.00"))
        self.assertEqual(RechargeTransaction.objects.get(reference=reference).status, "success")

    def test_service_level_replay_is_a_noop(self):
        recharge = services.create_recharge(self.user, Decimal("300"))
        _, first = services.process_recharge_callback(recharge.reference, "success")
        _, second = services.process_recharge_callback(recharge.reference, "success")
        self.assertEqual((first, second), (True, False))
        self.assertEqual(self.balance(), Decimal("300.00"))

    def test_failed_callback_does_not_credit(self):
        reference = self.start_recharge("500").data["reference"]
        resp = self.callback(reference, "failed")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.data["credited"])
        self.assertEqual(self.balance(), Decimal("0.00"))
        self.assertEqual(RechargeTransaction.objects.get(reference=reference).status, "failed")

    def test_repeated_failed_callback_is_harmless(self):
        reference = self.start_recharge().data["reference"]
        self.callback(reference, "failed")
        self.assertEqual(self.callback(reference, "failed").status_code, 200)

    def test_failed_recharge_cannot_be_turned_into_success(self):
        reference = self.start_recharge().data["reference"]
        self.callback(reference, "failed")
        resp = self.callback(reference, "success")
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(self.balance(), Decimal("0.00"))

    def test_successful_recharge_cannot_be_turned_into_failed(self):
        reference = self.start_recharge("100").data["reference"]
        self.callback(reference, "success")
        resp = self.callback(reference, "failed")
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(self.balance(), Decimal("100.00"))
        self.assertEqual(RechargeTransaction.objects.get(reference=reference).status, "success")

    def test_amount_in_callback_payload_is_ignored(self):
        reference = self.start_recharge("100").data["reference"]
        self.callback(reference, "success", amount="99999")
        self.assertEqual(self.balance(), Decimal("100.00"))

    def test_pending_is_not_a_valid_outcome(self):
        reference = self.start_recharge().data["reference"]
        self.assertEqual(self.callback(reference, "pending").status_code, 400)
        self.assertEqual(self.callback(reference, "bogus").status_code, 400)

    def test_unknown_reference_is_404(self):
        self.assertEqual(self.callback("rch_doesnotexist", "success").status_code, 404)

    def test_wrong_or_missing_secret_is_forbidden(self):
        reference = self.start_recharge().data["reference"]
        body = {"reference": reference, "status": "success"}
        bad = self.anonymous.post("/api/v1/recharge/callback/", body, format="json", HTTP_X_CALLBACK_SECRET="x")
        none = self.anonymous.post("/api/v1/recharge/callback/", body, format="json")
        self.assertEqual((bad.status_code, none.status_code), (403, 403))
        self.assertEqual(self.balance(), Decimal("0.00"))

    def test_credit_goes_to_the_wallet_that_started_the_recharge(self):
        other = make_user("other@example.com")
        reference = self.start_recharge("75").data["reference"]
        self.callback(reference, "success")
        self.assertEqual(Wallet.objects.get(user=other).balance, Decimal("0.00"))
        self.assertEqual(self.balance(), Decimal("75.00"))
