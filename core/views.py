import uuid
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import user_passes_test
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from . import services
from .models import MenuItem, Order

CART_KEY = "cart"          # {"<menu item id>": quantity}
ORDERS_KEY = "my_orders"   # ids of orders paid on this kiosk session


def _cart(request):
    return {int(k): int(v) for k, v in request.session.get(CART_KEY, {}).items()}


# ---------- Customer (kiosk) ----------

def menu(request):
    """Choosing page. Open to everyone: no card is needed to look and choose."""
    items = list(MenuItem.objects.filter(available=True).order_by("name"))
    chosen = _cart(request)
    error = None
    if request.method == "POST":
        chosen = {}
        for item in items:
            try:
                quantity = int(request.POST.get(f"qty_{item.pk}") or 0)
            except ValueError:
                quantity = 0
            quantity = max(0, min(quantity, 20))
            if quantity > 0:
                chosen[item.pk] = quantity
        if not chosen:
            error = "Pick at least one item."
        else:
            request.session[CART_KEY] = {str(k): v for k, v in chosen.items()}
            return redirect("order")
    for item in items:
        item.qty = chosen.get(item.pk, 0)
    return render(request, "core/menu.html", {"items": items, "error": error})


def order(request):
    """Order page: review the cart, then tap the card and enter the PIN to pay."""
    cart = _cart(request)
    items = {i.pk: i for i in MenuItem.objects.filter(pk__in=cart)}
    lines = [
        {"item": items[pk], "qty": qty, "subtotal": items[pk].price * qty}
        for pk, qty in cart.items() if pk in items
    ]
    if not lines:
        return redirect("menu")
    total = sum((line["subtotal"] for line in lines), Decimal("0"))
    error = None
    if request.method == "POST":
        key = request.POST.get("idempotency_key") or str(uuid.uuid4())
        try:
            paid = services.checkout(
                request.POST.get("card_uid", ""),
                request.POST.get("pin", ""),
                [(line["item"].pk, line["qty"]) for line in lines],
                key,
            )
        except (services.PaymentError, services.ItemUnavailable) as exc:
            error = str(exc)
        else:
            request.session[CART_KEY] = {}
            mine = request.session.get(ORDERS_KEY, [])
            mine.append(paid.pk)
            request.session[ORDERS_KEY] = mine
            return redirect("receipt", order_id=paid.pk)
    return render(request, "core/order.html", {
        "lines": lines, "total": total, "key": uuid.uuid4(), "error": error,
    })


def receipt(request, order_id):
    if order_id not in request.session.get(ORDERS_KEY, []):
        return redirect("menu")
    paid = get_object_or_404(Order, pk=order_id)
    return render(request, "core/receipt.html", {
        "order": paid, "balance": paid.account.balance,
        "refresh": paid.status != Order.SERVED,
    })


def kiosk_finish(request):
    request.session.pop(CART_KEY, None)
    request.session.pop(ORDERS_KEY, None)
    return redirect("menu")


# ---------- Staff (kitchen) ----------

def staff_required(view):
    return user_passes_test(lambda u: u.is_authenticated and u.is_staff)(view)


@staff_required
def kitchen(request):
    queue = (
        Order.objects.filter(status__in=services.FLOW[:-1])
        .select_related("account__user")
        .prefetch_related("items__menu_item")
        .order_by("created_at")
    )
    labels = {
        Order.RECEIVED: "Start preparing",
        Order.PREPARING: "Mark ready",
        Order.READY: "Mark served",
    }
    columns = [
        {
            "title": dict(Order.STATUS_CHOICES)[code],
            "status": code,
            "button": labels[code],
            "orders": [o for o in queue if o.status == code],
        }
        for code in services.FLOW[:-1]
    ]
    return render(request, "core/kitchen.html", {"columns": columns, "refresh": True})


@staff_required
@require_POST
def advance(request, order_id):
    try:
        services.advance_status(order_id, request.user, request.POST.get("from_status", ""))
    except services.StatusError as exc:
        messages.error(request, str(exc))
    return redirect("kitchen")
