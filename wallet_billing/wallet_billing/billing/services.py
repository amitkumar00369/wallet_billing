from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from enum import Enum

from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone
from rest_framework.exceptions import NotFound

from consultations import state_machine
from consultations.exceptions import InvalidTransition
from consultations.models import ConsultationSession
from wallets.models import Wallet

from .models import BillingTransaction

Status = ConsultationSession.Status
SECONDS_PER_MINUTE = 60


class ChargeOutcome(Enum):
    CHARGED = "charged"
    ALREADY_BILLED = "already_billed"
    INSUFFICIENT_FUNDS = "insufficient_funds"


class _CannotPay(Exception):
    """Raised inside the savepoint so the minute's bookkeeping is rolled back."""


@dataclass
class BillingResult:
    session: ConsultationSession
    minutes_charged: int
    amount_charged: Decimal


def billable_minutes(started_at, now):
    """Complete minutes elapsed. 59s -> 0, 60s -> 1, 119s -> 1, 120s -> 2."""
    elapsed_seconds = (now - started_at).total_seconds()
    return max(int(elapsed_seconds // SECONDS_PER_MINUTE), 0)


def charge_minute(session, minute, now):
 
    rate = session.per_minute_rate
    try:
        with transaction.atomic():
            claimed = ConsultationSession.objects.filter(
                pk=session.pk, status=Status.IN_PROGRESS, billed_minutes=minute - 1
            ).update(billed_minutes=minute, updated_at=now)
            if claimed != 1:
                return ChargeOutcome.ALREADY_BILLED

            BillingTransaction.objects.create(session_id=session.pk, minute_number=minute, amount=rate)

            paid = Wallet.objects.filter(user_id=session.user_id, balance__gte=rate).update(
                balance=F("balance") - rate, updated_at=now
            )
            if paid != 1:
                raise _CannotPay
    except IntegrityError:
        return ChargeOutcome.ALREADY_BILLED
    except _CannotPay:
        return ChargeOutcome.INSUFFICIENT_FUNDS
    return ChargeOutcome.CHARGED


def bill_session(session_id, now=None):

    now = now or timezone.now()
    with transaction.atomic():
        try:
            session = ConsultationSession.objects.get(pk=session_id)
        except ConsultationSession.DoesNotExist:
            raise NotFound("Session not found.")
        if session.status != Status.IN_PROGRESS:
            raise InvalidTransition(
                f"Cannot bill session {session.pk}: it is '{session.status}', not 'in_progress'."
            )

        due = billable_minutes(session.started_at, now)
        minutes_charged = 0
        for minute in range(session.billed_minutes + 1, due + 1):
            outcome = charge_minute(session, minute, now)
            if outcome is ChargeOutcome.CHARGED:
                minutes_charged += 1
            elif outcome is ChargeOutcome.INSUFFICIENT_FUNDS:
                paid_minutes = minute - 1
                state_machine.move(
                    session.pk, Status.IN_PROGRESS, Status.COMPLETED, now,
                    ended_at=session.started_at + timedelta(minutes=paid_minutes),
                    duration_seconds=paid_minutes * SECONDS_PER_MINUTE,
                    ended_by=ConsultationSession.EndedBy.SYSTEM,
                    end_reason=ConsultationSession.EndReason.INSUFFICIENT_BALANCE,
                )
                break

        session.refresh_from_db()
    return BillingResult(session, minutes_charged, minutes_charged * session.per_minute_rate)
