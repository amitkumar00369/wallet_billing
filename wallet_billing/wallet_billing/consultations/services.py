from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied

from billing import services as billing_services
from wallets.models import Wallet

from . import state_machine
from .exceptions import ActiveSessionExists, InsufficientBalance, InvalidTransition
from .models import ConsultationSession

Status = ConsultationSession.Status


def get_session_for_actor(session_id, actor):
    """Sessions are only visible to the user who booked them and the assigned provider."""
    session = ConsultationSession.objects.filter(Q(user=actor) | Q(provider=actor), pk=session_id).first()
    if session is None:
        raise NotFound("Session not found.")
    return session


def create_session(user):
    return ConsultationSession.objects.create(user=user)


def assign_provider(session_id, actor, provider, now=None):
    now = now or timezone.now()
    with transaction.atomic():
        session = get_session_for_actor(session_id, actor)
        if session.user_id != actor.id:
            raise PermissionDenied("Only the user who created the session can assign a provider.")
        state_machine.ensure_can_transition(session, Status.ASSIGNED, "assign")
        if not state_machine.move(session.pk, Status.UNASSIGNED, Status.ASSIGNED, now, provider=provider):
            raise InvalidTransition("The session was changed by another request. Please retry.")
    return ConsultationSession.objects.get(pk=session_id)


def accept_session(session_id, actor, now=None):
    now = now or timezone.now()
    with transaction.atomic():
        session = get_session_for_actor(session_id, actor)
        if session.provider_id != actor.id:
            raise PermissionDenied("Only the assigned provider can accept this session.")
        state_machine.ensure_can_transition(session, Status.ACCEPTED, "accept")
        if not state_machine.move(session.pk, Status.ASSIGNED, Status.ACCEPTED, now):
            raise InvalidTransition("The session was changed by another request. Please retry.")
    return ConsultationSession.objects.get(pk=session_id)


def start_session(session_id, actor, now=None):
    now = now or timezone.now()
    with transaction.atomic():
        session = get_session_for_actor(session_id, actor)
        if session.user_id != actor.id:
            raise PermissionDenied("Only the user who created the session can start it.")
        state_machine.ensure_can_transition(session, Status.IN_PROGRESS, "start")

        if ConsultationSession.objects.filter(user=actor, status=Status.IN_PROGRESS).exists():
            raise ActiveSessionExists("You already have a session in progress. End it before starting another.")


        rate = settings.SESSION_RATE_PER_MINUTE
        required = rate * settings.MIN_START_MINUTES
        balance = Wallet.objects.get(user=actor).balance
        if balance < required:
            raise InsufficientBalance(
                f"Wallet balance {balance} is below the {required} required to start "
                f"({settings.MIN_START_MINUTES} minutes at {rate} per minute). Please recharge."
            )

        try:
            with transaction.atomic(): 
                moved = state_machine.move(
                    session.pk, Status.ACCEPTED, Status.IN_PROGRESS, now, started_at=now, per_minute_rate=rate
                )
        except IntegrityError:
            raise ActiveSessionExists("You already have a session in progress. End it before starting another.")
        if not moved:
            raise InvalidTransition("The session was changed by another request. Please retry.")
    return ConsultationSession.objects.get(pk=session_id)


def end_session(session_id, actor, now=None):

    now = now or timezone.now()
    with transaction.atomic():
        session = get_session_for_actor(session_id, actor)
        state_machine.ensure_can_transition(session, Status.COMPLETED, "end")

        session = billing_services.bill_session(session.pk, now=now).session
        if session.status == Status.COMPLETED:
            return session    

        ended_by = (
            ConsultationSession.EndedBy.USER if actor.id == session.user_id else ConsultationSession.EndedBy.PROVIDER
        )
        moved = state_machine.move(
            session.pk, Status.IN_PROGRESS, Status.COMPLETED, now,
            ended_at=now,
            duration_seconds=int((now - session.started_at).total_seconds()),
            ended_by=ended_by,
            end_reason=ConsultationSession.EndReason.NORMAL,
        )
        if not moved:
            raise InvalidTransition("The session was changed by another request. Please retry.")
    return ConsultationSession.objects.get(pk=session_id)
