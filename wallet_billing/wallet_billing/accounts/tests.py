from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from rest_framework.test import APIClient

from config.factories import make_user

from .models import User


class JwtAuthTests(TestCase):
    def setUp(self):
        self.user = make_user("asha@example.com")

    def test_login_with_email_returns_tokens_and_they_work(self):
        client = APIClient()
        resp = client.post("/api/v1/auth/token/", {"email": "asha@example.com", "password": "pass12345"}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("access", resp.data)
        self.assertIn("refresh", resp.data)

        client.credentials(HTTP_AUTHORIZATION=f"Bearer {resp.data['access']}")
        self.assertEqual(client.get("/api/v1/wallet/private/balance/").status_code, 200)

    def test_refresh_token_gives_new_access_token(self):
        client = APIClient()
        tokens = client.post(
            "/api/v1/auth/token/", {"email": "asha@example.com", "password": "pass12345"}, format="json"
        ).data
        resp = client.post("/api/v1/auth/token/refresh/", {"refresh": tokens["refresh"]}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("access", resp.data)

    def test_wrong_password_is_rejected(self):
        resp = APIClient().post("/api/v1/auth/token/", {"email": "asha@example.com", "password": "nope"}, format="json")
        self.assertEqual(resp.status_code, 401)

    def test_endpoints_require_authentication(self):
        client = APIClient()
        self.assertEqual(client.get("/api/v1/wallet/private/balance/").status_code, 401)
        self.assertEqual(client.post("/api/v1/sessions/private/create/").status_code, 401)


class UserModelTests(TestCase):
    def test_email_is_unique_and_role_defaults_to_user(self):
        user = make_user("a@example.com")
        self.assertEqual(user.role, User.Role.USER)
        with self.assertRaises(Exception):
            make_user("a@example.com")

    def test_password_is_hashed(self):
        user = make_user("a@example.com", password="plain-text-pw")
        self.assertNotEqual(user.password, "plain-text-pw")
        self.assertTrue(user.check_password("plain-text-pw"))

    def test_wallet_is_created_with_zero_balance(self):
        user = make_user("a@example.com")
        self.assertEqual(user.wallet.balance, 0)


class CreateUserCommandTests(TestCase):
    def test_creates_provider(self):
        out = StringIO()
        call_command("create_user", email="dr@example.com", password="pass12345", role="PROVIDER", stdout=out)
        self.assertEqual(User.objects.get(email="dr@example.com").role, User.Role.PROVIDER)

    def test_duplicate_email_fails(self):
        make_user("dup@example.com")
        with self.assertRaises(CommandError):
            call_command("create_user", email="dup@example.com", password="pass12345")


class PrivateRouteTests(TestCase):
    """Every URI with a `private` segment must verify the JWT; public ones must not need it."""

    def setUp(self):
        from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

        self.user = make_user("asha@example.com")
        self.access = str(AccessToken.for_user(self.user))
        self.refresh = str(RefreshToken.for_user(self.user))
        self.client = APIClient()

    PRIVATE = [
        ("get", "/api/v1/wallet/private/balance/"),
        ("post", "/api/v1/recharge/private/initiate/"),
        ("post", "/api/v1/sessions/private/create/"),
        ("get", "/api/v1/sessions/private/1/details/"),
        ("post", "/api/v1/sessions/private/1/start/"),
        ("post", "/api/v1/sessions/private/1/bill/"),
    ]

    def test_private_uris_reject_requests_without_a_token(self):
        for method, url in self.PRIVATE:
            with self.subTest(url=url):
                resp = getattr(self.client, method)(url)
                self.assertEqual(resp.status_code, 401)
                self.assertEqual(resp.json()["detail"], "Authentication credentials were not provided.")

    def test_private_uris_reject_garbage_and_refresh_tokens(self):
        for token in ("not-a-jwt", self.refresh):  
            with self.subTest(token=token[:12]):
                resp = self.client.get("/api/v1/wallet/private/balance/", HTTP_AUTHORIZATION=f"Bearer {token}")
                self.assertEqual(resp.status_code, 401)
                self.assertEqual(resp.json()["detail"], "Token is invalid or expired.")

    def test_private_uris_accept_a_valid_token(self):
        resp = self.client.get("/api/v1/wallet/private/balance/", HTTP_AUTHORIZATION=f"Bearer {self.access}")
        self.assertEqual(resp.status_code, 200)

    def test_token_for_a_deleted_user_is_rejected(self):
        self.user.delete()
        resp = self.client.get("/api/v1/wallet/private/balance/", HTTP_AUTHORIZATION=f"Bearer {self.access}")
        self.assertEqual(resp.status_code, 401)

    def test_public_uris_do_not_need_a_token(self):
        login = self.client.post("/api/v1/auth/token/", {"email": "asha@example.com", "password": "pass12345"}, format="json")
        self.assertEqual(login.status_code, 200)
    
        callback = self.client.post("/api/v1/recharge/callback/", {"reference": "x", "status": "success"}, format="json")
        self.assertEqual(callback.status_code, 403)
        self.assertEqual(callback.json()["detail"], "Invalid callback secret.")

    def test_old_unversioned_paths_are_gone(self):
        self.assertEqual(self.client.get("/api/wallet/").status_code, 404)
