
from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from unittest import mock

from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import User
from wallets import services as wallet_services


class FakeClock:


    def __init__(self):
        self.current = datetime(2026, 1, 1, 12, 0, 0, tzinfo=dt_timezone.utc)

    def __call__(self):
        return self.current

    def advance(self, **kwargs):
        self.current += timedelta(**kwargs)

    def patch(self):
        return mock.patch("django.utils.timezone.now", self)


def make_user(email, role=User.Role.USER, password="pass12345"):
    return User.objects.create_user(email=email, password=password, role=role)


def fund_wallet(user, amount):

    recharge = wallet_services.create_recharge(user, Decimal(str(amount)))
    wallet_services.process_recharge_callback(recharge.reference, "success")
    return recharge


def client_for(user):

    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(user)}")
    return client
