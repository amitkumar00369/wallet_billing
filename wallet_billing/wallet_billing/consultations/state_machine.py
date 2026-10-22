from .exceptions import InvalidTransition
from .models import ConsultationSession

Status = ConsultationSession.Status

ALLOWED_TRANSITIONS = {
    Status.UNASSIGNED: {Status.ASSIGNED},
    Status.ASSIGNED: {Status.ACCEPTED},
    Status.ACCEPTED: {Status.IN_PROGRESS},
    Status.IN_PROGRESS: {Status.COMPLETED},
    Status.COMPLETED: set(),
}


def ensure_can_transition(session, target, action):
    if target not in ALLOWED_TRANSITIONS[session.status]:
        raise InvalidTransition(
            f"Cannot {action} session {session.pk}: it is '{session.status}' "
            f"and cannot move to '{target}'."
        )


def move(session_id, source, target, now, **fields):
    """Change status only if the row is still in `source`.

    This is a conditional UPDATE, so the check and the write are one statement in
    the database. If another request already moved the session, nothing is updated
    and False is returned.
    """
    updated = ConsultationSession.objects.filter(pk=session_id, status=source).update(
        status=target, updated_at=now, **fields
    )
    return updated == 1
