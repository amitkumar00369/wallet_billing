from django.conf import settings
from django.db import models
from django.db.models import Q


class Wallet(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="wallet")
    balance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(balance__gte=0), name="wallet_balance_not_negative"),
        ]

    def __str__(self):
        return f"Wallet of user {self.user_id}: {self.balance}"


class RechargeTransaction(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending"
        SUCCESS = "success"
        FAILED = "failed"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="recharges")
    wallet = models.ForeignKey(Wallet, on_delete=models.PROTECT, related_name="recharges")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    reference = models.CharField(max_length=40, unique=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-id"]
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="recharge_amount_positive"),
        ]

    def __str__(self):
        return f"{self.reference} ({self.status})"
