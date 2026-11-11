from decimal import Decimal

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.db import models
from django.db.models import Sum


class Account(models.Model):
    """A canteen account linked to one university RFID card.

    The balance is never stored. It is the sum of this account's ledger
    entries, so it cannot drift from the transaction history.
    """

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    card_uid = models.CharField(max_length=32, unique=True)
    pin_hash = models.CharField(max_length=128)
    active = models.BooleanField(default=True)

    def set_pin(self, raw_pin):
        self.pin_hash = make_password(raw_pin)

    def check_pin(self, raw_pin):
        return check_password(raw_pin, self.pin_hash)

    @property
    def balance(self):
        return self.entries.aggregate(total=Sum("amount"))["total"] or Decimal("0")

    def __str__(self):
        return f"{self.user} ({self.card_uid})"


class MenuItem(models.Model):
    name = models.CharField(max_length=80)
    price = models.DecimalField(max_digits=8, decimal_places=2)
    available = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.name} ({self.price})"


class Order(models.Model):
    PENDING_PAYMENT = "PENDING_PAYMENT"
    RECEIVED = "RECEIVED"
    PREPARING = "PREPARING"
    READY = "READY"
    SERVED = "SERVED"
    CANCELLED = "CANCELLED"
    STATUS_CHOICES = [
        (PENDING_PAYMENT, "Pending payment"),
        (RECEIVED, "Received"),
        (PREPARING, "Preparing"),
        (READY, "Ready"),
        (SERVED, "Served"),
        (CANCELLED, "Cancelled"),
    ]

    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="orders")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=PENDING_PAYMENT)
    total = models.DecimalField(max_digits=10, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def number(self):
        return f"A{self.pk:04d}"

    def __str__(self):
        return f"{self.number} [{self.status}]"


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    menu_item = models.ForeignKey(MenuItem, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField()
    unit_price = models.DecimalField(max_digits=8, decimal_places=2)


class LedgerEntry(models.Model):
    """Append-only money record. Positive = credit, negative = debit."""

    TOPUP = "TOPUP"
    PURCHASE = "PURCHASE"
    KIND_CHOICES = [(TOPUP, "Top-up"), (PURCHASE, "Purchase")]

    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="entries")
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    kind = models.CharField(max_length=10, choices=KIND_CHOICES)
    order = models.ForeignKey(Order, null=True, blank=True, on_delete=models.PROTECT, related_name="entries")
    idempotency_key = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise RuntimeError("Ledger entries are append-only and cannot be edited.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise RuntimeError("Ledger entries are append-only and cannot be deleted.")


class OrderStatusChange(models.Model):
    """History of every status an order has had, for audit and quality reports."""

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="history")
    status = models.CharField(max_length=20, choices=Order.STATUS_CHOICES)
    changed_at = models.DateTimeField(auto_now_add=True)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT
    )

    class Meta:
        ordering = ["changed_at", "pk"]
