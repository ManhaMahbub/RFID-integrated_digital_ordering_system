"""Money-moving operations. Every function here is the ONLY way balances change."""
from decimal import Decimal

from django.db import IntegrityError, transaction

from .models import Account, LedgerEntry, MenuItem, Order, OrderItem, OrderStatusChange


class PaymentError(Exception):
    """Base class for rejected payments."""


class InvalidPin(PaymentError):
    pass


class InsufficientBalance(PaymentError):
    pass


class AlreadyPaid(PaymentError):
    pass


class AccountInactive(PaymentError):
    pass


class UnknownCard(PaymentError):
    pass


class ItemUnavailable(Exception):
    pass


class StatusError(Exception):
    """A kitchen status change that is not allowed."""


FLOW = [Order.RECEIVED, Order.PREPARING, Order.READY, Order.SERVED]


def _record(order, status, user=None):
    OrderStatusChange.objects.create(order=order, status=status, changed_by=user)


@transaction.atomic
def create_order(account, lines):
    """lines = [(menu_item_id, quantity), ...]. Returns an unpaid Order."""
    if not lines:
        raise ValueError("An order needs at least one item.")
    total = Decimal("0")
    order = Order.objects.create(account=account, total=Decimal("0"))
    for menu_item_id, quantity in lines:
        item = MenuItem.objects.get(pk=menu_item_id)
        if not item.available:
            raise ItemUnavailable(f"{item.name} is not available.")
        OrderItem.objects.create(
            order=order, menu_item=item, quantity=quantity, unit_price=item.price
        )
        total += item.price * quantity
    order.total = total
    order.save(update_fields=["total"])
    return order


@transaction.atomic
def top_up(account, amount, idempotency_key):
    """Credit an account. Stands in for the university recharge process."""
    amount = Decimal(amount)
    if amount <= 0:
        raise ValueError("Top-up must be positive.")
    existing = LedgerEntry.objects.filter(idempotency_key=idempotency_key).first()
    if existing:
        return existing
    return LedgerEntry.objects.create(
        account=account, amount=amount, kind=LedgerEntry.TOPUP,
        idempotency_key=idempotency_key,
    )


@transaction.atomic
def pay_order(order_id, pin, idempotency_key):
    """Pay for an order from the account balance, exactly once.

    - Retrying with the same key returns the already-paid order (no second charge).
    - Paying an already-paid order with a different key is rejected.
    - Wrong PIN, inactive account or low balance leave everything unchanged.
    """
    order = Order.objects.select_for_update().get(pk=order_id)

    paid = LedgerEntry.objects.filter(order=order, kind=LedgerEntry.PURCHASE).first()
    if paid:
        if paid.idempotency_key == idempotency_key:
            return order
        raise AlreadyPaid(f"Order {order.number} is already paid.")

    if order.status != Order.PENDING_PAYMENT:
        raise PaymentError(f"Order {order.number} is not awaiting payment.")

    account = Account.objects.select_for_update().get(pk=order.account_id)
    if not account.active:
        raise AccountInactive("This account is deactivated.")
    if not account.check_pin(pin):
        raise InvalidPin("Wrong PIN.")
    if account.balance < order.total:
        raise InsufficientBalance("Balance is too low for this order.")

    LedgerEntry.objects.create(
        account=account, amount=-order.total, kind=LedgerEntry.PURCHASE,
        order=order, idempotency_key=idempotency_key,
    )
    order.status = Order.RECEIVED
    order.save(update_fields=["status"])
    _record(order, Order.RECEIVED)
    return order


@transaction.atomic
def advance_status(order_id, user, from_status):
    """Move an order one step forward: Received > Preparing > Ready > Served.

    - Only staff may do it.
    - `from_status` is the status the staff member saw on screen; if the order
      has moved since (another click, another screen) the change is refused,
      so a double-click can never skip a step.
    """
    if not (user and user.is_authenticated and user.is_staff):
        raise StatusError("Only canteen staff can change an order's status.")
    order = Order.objects.select_for_update().get(pk=order_id)
    if order.status not in FLOW:
        raise StatusError(f"Order {order.number} is not in the kitchen queue.")
    if order.status != from_status:
        raise StatusError(f"Order {order.number} has already moved to {order.get_status_display()}.")
    index = FLOW.index(order.status)
    if index == len(FLOW) - 1:
        raise StatusError(f"Order {order.number} is already served.")
    order.status = FLOW[index + 1]
    order.save(update_fields=["status"])
    _record(order, order.status, user)
    return order


def checkout(card_uid, pin, lines, idempotency_key):
    """Final kiosk step: tap card + PIN, create the order and pay for it in one go.

    Nothing is saved unless the payment succeeds, so a wrong PIN, an unknown
    card or a low balance leaves no half-made order behind. Submitting the
    same idempotency key again (double tap, retry) returns the same order and
    never charges twice.
    """
    card_uid = (card_uid or "").strip()

    def already_paid():
        entry = (
            LedgerEntry.objects.filter(idempotency_key=idempotency_key, kind=LedgerEntry.PURCHASE)
            .select_related("order", "account")
            .first()
        )
        if entry is None:
            return None
        if entry.account.card_uid != card_uid:
            raise PaymentError("This payment key belongs to another card.")
        return entry.order

    found = already_paid()
    if found:
        return found
    try:
        with transaction.atomic():
            account = Account.objects.filter(card_uid=card_uid, active=True).first()
            if account is None:
                raise UnknownCard("Card not recognised.")
            order = create_order(account, lines)
            return pay_order(order.pk, pin, idempotency_key)
    except IntegrityError:
        found = already_paid()
        if found:
            return found
        raise
