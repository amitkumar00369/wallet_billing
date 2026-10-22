from django.conf import settings
from django.db import models
from django.db.models import Q


class ConsultationSession(models.Model):
    class Status(models.TextChoices):
        UNASSIGNED = "unassigned"
        ASSIGNED = "assigned"
        ACCEPTED = "accepted_by_provider"
        IN_PROGRESS = "in_progress"
        COMPLETED = "completed"

    class EndedBy(models.TextChoices):
        USER = "user"
        PROVIDER = "provider"
        SYSTEM = "system"

    class EndReason(models.TextChoices):
        NORMAL = "normal", "Ended normally"
        INSUFFICIENT_BALANCE = "insufficient_balance", "Insufficient Balance"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="consultation_sessions")
    provider = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="provider_sessions"
    )
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.UNASSIGNED)


    per_minute_rate = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)

    billed_minutes = models.PositiveIntegerField(default=0)

    duration_seconds = models.PositiveIntegerField(null=True, blank=True)

    ended_by = models.CharField(max_length=10, choices=EndedBy.choices, blank=True)
    end_reason = models.CharField(max_length=30, choices=EndReason.choices, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["user"], condition=Q(status="in_progress"), name="one_in_progress_session_per_user"
            ),
            models.CheckConstraint(
                condition=Q(per_minute_rate__isnull=True) | Q(per_minute_rate__gt=0),
                name="session_rate_positive",
            ),
            models.CheckConstraint(
                condition=~Q(status__in=["in_progress", "completed"])
                | (Q(started_at__isnull=False) & Q(per_minute_rate__isnull=False)),
                name="started_session_has_start_time_and_rate",
            ),
            models.CheckConstraint(
                condition=~Q(status="completed") | (Q(ended_at__isnull=False) & ~Q(ended_by="")),
                name="completed_session_has_end_info",
            ),
        ]

    def __str__(self):
        return f"Session {self.pk} ({self.status})"
