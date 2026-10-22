import uuid

from django.db import transaction
from django.db.models import F
from django.utils import timezone
from rest_framework.exceptions import NotFound

from .exceptions import RechargeConflict
from .models import RechargeTransaction, Wallet

Status = RechargeTransaction.Status


def get_wallet(user):
    return Wallet.objects.get(user=user)


def create_recharge(user, amount):
    wallet = get_wallet(user)
    return RechargeTransaction.objects.create(
        user=user, wallet=wallet, amount=amount, reference=f"rch_{uuid.uuid4().hex[:24]}"
    )


def process_recharge_callback(reference, outcome):

    now = timezone.now()
    with transaction.atomic():
        flipped = RechargeTransaction.objects.filter(reference=reference, status=Status.PENDING).update(
            status=outcome, processed_at=now, updated_at=now
        )
        try:
            recharge = RechargeTransaction.objects.get(reference=reference)
        except RechargeTransaction.DoesNotExist:
            raise NotFound("Unknown recharge reference.")

        if flipped:
            if outcome == Status.SUCCESS:
                Wallet.objects.filter(pk=recharge.wallet_id).update(
                    balance=F("balance") + recharge.amount, updated_at=now
                )
            return recharge, outcome == Status.SUCCESS

    if recharge.status == outcome:
        return recharge, False      
    raise RechargeConflict(f"Recharge {reference} is already '{recharge.status}' and cannot become '{outcome}'.")
