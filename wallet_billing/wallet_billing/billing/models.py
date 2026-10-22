from django.db import models
from django.db.models import Q

from consultations.models import ConsultationSession


class BillingTransaction(models.Model):
    session = models.ForeignKey(ConsultationSession, on_delete=models.PROTECT, related_name="billing_transactions")
    minute_number = models.PositiveIntegerField()
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["session_id", "minute_number"]
        constraints = [
            models.UniqueConstraint(fields=["session", "minute_number"], name="one_charge_per_session_minute"),
            models.CheckConstraint(condition=Q(minute_number__gte=1), name="billing_minute_starts_at_one"),
            models.CheckConstraint(condition=Q(amount__gt=0), name="billing_amount_positive"),
        ]

    def __str__(self):
        return f"Session {self.session_id} minute {self.minute_number}: {self.amount}"
