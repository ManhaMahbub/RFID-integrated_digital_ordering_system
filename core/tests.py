from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from .models import Account, LedgerEntry, MenuItem, Order
from .services import (
    AlreadyPaid, InsufficientBalance, InvalidPin, ItemUnavailable,
    create_order, pay_order, top_up,
)


class LedgerTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user("s1")
        self.account = Account(user=user, card_uid="CARD-T1")
        self.account.set_pin("1234")
        self.account.save()
        top_up(self.account, "500", "topup-1")
        self.biryani = MenuItem.objects.create(name="Biryani", price=Decimal("180"))
        self.tea = MenuItem.objects.create(name="Tea", price=Decimal("20"))

    def make_order(self):
        return create_order(self.account, [(self.biryani.pk, 2), (self.tea.pk, 1)])  # 380

    def test_order_total_is_calculated(self):
        self.assertEqual(self.make_order().total, Decimal("380"))

    def test_payment_deducts_once_and_marks_received(self):
        order = self.make_order()
        pay_order(order.pk, "1234", "pay-1")
        order.refresh_from_db()
        self.assertEqual(order.status, Order.RECEIVED)
        self.assertEqual(self.account.balance, Decimal("120"))

    def test_same_key_retry_charges_only_once(self):
        order = self.make_order()
        pay_order(order.pk, "1234", "pay-1")
        pay_order(order.pk, "1234", "pay-1")  # network retry / double tap
        self.assertEqual(self.account.balance, Decimal("120"))
        self.assertEqual(LedgerEntry.objects.filter(kind="PURCHASE").count(), 1)

    def test_different_key_on_paid_order_is_rejected(self):
        order = self.make_order()
        pay_order(order.pk, "1234", "pay-1")
        with self.assertRaises(AlreadyPaid):
            pay_order(order.pk, "1234", "pay-2")
        self.assertEqual(self.account.balance, Decimal("120"))

    def test_insufficient_balance_is_rejected_and_nothing_changes(self):
        order = create_order(self.account, [(self.biryani.pk, 3)])  # 540 > 500
        with self.assertRaises(InsufficientBalance):
            pay_order(order.pk, "1234", "pay-1")
        order.refresh_from_db()
        self.assertEqual(order.status, Order.PENDING_PAYMENT)
        self.assertEqual(self.account.balance, Decimal("500"))

    def test_wrong_pin_is_rejected(self):
        order = self.make_order()
        with self.assertRaises(InvalidPin):
            pay_order(order.pk, "0000", "pay-1")
        self.assertEqual(self.account.balance, Decimal("500"))

    def test_unavailable_item_cannot_be_ordered(self):
        self.tea.available = False
        self.tea.save()
        with self.assertRaises(ItemUnavailable):
            create_order(self.account, [(self.tea.pk, 1)])

    def test_ledger_entries_cannot_be_edited_or_deleted(self):
        entry = LedgerEntry.objects.first()
        entry.amount = Decimal("999999")
        with self.assertRaises(RuntimeError):
            entry.save()
        with self.assertRaises(RuntimeError):
            entry.delete()

    def test_topup_retry_credits_once(self):
        top_up(self.account, "500", "topup-1")  # same key as setUp
        self.assertEqual(self.account.balance, Decimal("500"))


from django.test import Client

from .models import OrderStatusChange
from .services import PaymentError, StatusError, advance_status


class StatusTests(TestCase):
    def setUp(self):
        User = get_user_model()
        user = User.objects.create_user("s1")
        self.account = Account(user=user, card_uid="CARD-T1")
        self.account.set_pin("1234")
        self.account.save()
        top_up(self.account, "500", "topup-1")
        self.item = MenuItem.objects.create(name="Biryani", price=Decimal("180"))
        self.staff = User.objects.create_user("kitchen", is_staff=True)
        self.customer = user
        self.order = create_order(self.account, [(self.item.pk, 1)])
        pay_order(self.order.pk, "1234", "pay-1")

    def test_paid_order_starts_as_received_with_history(self):
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.RECEIVED)
        self.assertEqual(self.order.history.count(), 1)

    def test_staff_moves_order_forward_step_by_step(self):
        for current, expected in [("RECEIVED", "PREPARING"), ("PREPARING", "READY"), ("READY", "SERVED")]:
            advance_status(self.order.pk, self.staff, current)
            self.order.refresh_from_db()
            self.assertEqual(self.order.status, expected)
        self.assertEqual(self.order.history.count(), 4)
        self.assertEqual(self.order.history.last().changed_by, self.staff)

    def test_stale_click_cannot_skip_a_step(self):
        advance_status(self.order.pk, self.staff, "RECEIVED")
        with self.assertRaises(StatusError):
            advance_status(self.order.pk, self.staff, "RECEIVED")  # double click
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.PREPARING)

    def test_served_order_cannot_move_again(self):
        for current in ["RECEIVED", "PREPARING", "READY"]:
            advance_status(self.order.pk, self.staff, current)
        with self.assertRaises(StatusError):
            advance_status(self.order.pk, self.staff, "SERVED")

    def test_unpaid_order_cannot_enter_kitchen_flow(self):
        unpaid = create_order(self.account, [(self.item.pk, 1)])
        with self.assertRaises(StatusError):
            advance_status(unpaid.pk, self.staff, "PENDING_PAYMENT")

    def test_customer_cannot_change_status(self):
        with self.assertRaises(StatusError):
            advance_status(self.order.pk, self.customer, "RECEIVED")


from .services import UnknownCard, checkout


class CheckoutServiceTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user("s1")
        self.account = Account(user=user, card_uid="CARD-T1")
        self.account.set_pin("1234")
        self.account.save()
        top_up(self.account, "500", "topup-1")
        self.item = MenuItem.objects.create(name="Biryani", price=Decimal("180"))

    def lines(self, qty=2):
        return [(self.item.pk, qty)]

    def test_checkout_creates_and_pays_in_one_step(self):
        order = checkout("CARD-T1", "1234", self.lines(), "k1")
        self.assertEqual(order.status, Order.RECEIVED)
        self.assertEqual(self.account.balance, Decimal("140"))

    def test_wrong_pin_leaves_no_order_and_no_charge(self):
        with self.assertRaises(InvalidPin):
            checkout("CARD-T1", "0000", self.lines(), "k1")
        self.assertEqual(Order.objects.count(), 0)
        self.assertEqual(self.account.balance, Decimal("500"))

    def test_unknown_card_leaves_no_order(self):
        with self.assertRaises(UnknownCard):
            checkout("NOPE", "1234", self.lines(), "k1")
        self.assertEqual(Order.objects.count(), 0)

    def test_inactive_card_is_treated_as_unknown(self):
        self.account.active = False
        self.account.save()
        with self.assertRaises(UnknownCard):
            checkout("CARD-T1", "1234", self.lines(), "k1")

    def test_insufficient_balance_leaves_no_order(self):
        with self.assertRaises(InsufficientBalance):
            checkout("CARD-T1", "1234", self.lines(3), "k1")  # 540 > 500
        self.assertEqual(Order.objects.count(), 0)
        self.assertEqual(self.account.balance, Decimal("500"))

    def test_same_key_twice_charges_once_and_returns_same_order(self):
        first = checkout("CARD-T1", "1234", self.lines(), "same")
        second = checkout("CARD-T1", "1234", self.lines(), "same")
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(self.account.balance, Decimal("140"))

    def test_key_cannot_be_reused_from_another_card(self):
        checkout("CARD-T1", "1234", self.lines(), "same")
        with self.assertRaises(PaymentError):
            checkout("CARD-OTHER", "1234", self.lines(), "same")

    def test_unavailable_item_leaves_no_order(self):
        self.item.available = False
        self.item.save()
        with self.assertRaises(ItemUnavailable):
            checkout("CARD-T1", "1234", self.lines(), "k1")
        self.assertEqual(Order.objects.count(), 0)


class PageTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.item = MenuItem.objects.create(name="Biryani", price=Decimal("180"))
        self.tea = MenuItem.objects.create(name="Tea", price=Decimal("20"))
        self.accounts = []
        for name, uid, pin in [("s1", "CARD-T1", "1234"), ("s2", "CARD-T2", "5678")]:
            acct = Account(user=User.objects.create_user(name), card_uid=uid)
            acct.set_pin(pin)
            acct.save()
            top_up(acct, "500", f"seed-{uid}")
            self.accounts.append(acct)
        self.staff = User.objects.create_user("kitchen", password="pw", is_staff=True)

    def choose(self, client, qty="2"):
        return client.post("/kiosk/", {f"qty_{self.item.pk}": qty})

    def pay(self, client, uid="CARD-T1", pin="1234", key="k1"):
        return client.post("/kiosk/order/", {"card_uid": uid, "pin": pin, "idempotency_key": key})

    def buy(self, client=None, **kwargs):
        client = client or Client()
        self.choose(client)
        self.pay(client, **kwargs)
        return Order.objects.latest("pk"), client

    # --- the menu is open to everyone ---
    def test_menu_is_open_and_asks_for_no_card(self):
        page = Client().get("/kiosk/")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Biryani")
        self.assertNotContains(page, "card_uid")

    def test_root_goes_to_menu(self):
        self.assertRedirects(Client().get("/"), "/kiosk/")

    def test_unavailable_items_are_not_listed(self):
        self.tea.available = False
        self.tea.save()
        page = Client().get("/kiosk/")
        self.assertNotContains(page, "Tea")

    def test_empty_choice_is_rejected(self):
        page = Client().post("/kiosk/", {f"qty_{self.item.pk}": "0"})
        self.assertContains(page, "Pick at least one item")

    def test_quantity_is_capped_at_20(self):
        c = Client()
        c.post("/kiosk/", {f"qty_{self.tea.pk}": "999"})
        self.assertContains(c.get("/kiosk/order/"), "400")  # 20 x 20

    # --- order page: review, then card and PIN ---
    def test_order_page_without_choices_goes_back_to_menu(self):
        self.assertRedirects(Client().get("/kiosk/order/"), "/kiosk/")

    def test_choosing_leads_to_order_page_with_card_and_pin_fields(self):
        c = Client()
        self.assertRedirects(self.choose(c), "/kiosk/order/")
        page = c.get("/kiosk/order/")
        self.assertContains(page, "Biryani")
        self.assertContains(page, "360")
        self.assertContains(page, 'name="card_uid"')
        self.assertContains(page, 'name="pin"')

    def test_change_items_keeps_choices(self):
        c = Client()
        self.choose(c)
        self.assertContains(c.get("/kiosk/"), 'value="2"')

    # --- paying ---
    def test_full_customer_flow(self):
        c = Client()
        self.choose(c)
        r = self.pay(c)
        order = Order.objects.get()
        self.assertRedirects(r, f"/kiosk/receipt/{order.pk}/")
        page = c.get(f"/kiosk/receipt/{order.pk}/")
        self.assertContains(page, order.number)
        self.assertContains(page, "Received")
        self.assertEqual(self.accounts[0].balance, Decimal("140"))

    def test_wrong_pin_shows_error_keeps_cart_and_makes_no_order(self):
        c = Client()
        self.choose(c)
        page = self.pay(c, pin="0000")
        self.assertContains(page, "Wrong PIN")
        self.assertEqual(Order.objects.count(), 0)
        self.assertEqual(self.accounts[0].balance, Decimal("500"))
        self.assertContains(c.get("/kiosk/order/"), "Biryani")

    def test_unknown_card_shows_error(self):
        c = Client()
        self.choose(c)
        self.assertContains(self.pay(c, uid="NOPE"), "Card not recognised")
        self.assertEqual(Order.objects.count(), 0)

    def test_double_submit_with_same_key_charges_once(self):
        c = Client()
        self.choose(c)
        self.pay(c, key="same")
        self.pay(c, key="same")
        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(self.accounts[0].balance, Decimal("140"))

    def test_item_that_becomes_unavailable_blocks_payment(self):
        c = Client()
        self.choose(c)
        self.item.available = False
        self.item.save()
        self.assertContains(self.pay(c), "not available")
        self.assertEqual(self.accounts[0].balance, Decimal("500"))

    def test_receipt_is_only_visible_to_the_session_that_paid(self):
        order, _ = self.buy()
        self.assertRedirects(Client().get(f"/kiosk/receipt/{order.pk}/"), "/kiosk/")

    def test_finish_clears_the_session(self):
        order, c = self.buy()
        c.get("/kiosk/finish/")
        self.assertRedirects(c.get(f"/kiosk/receipt/{order.pk}/"), "/kiosk/")

    # --- kitchen ---
    def test_kitchen_page_requires_staff_login(self):
        r = Client().get("/staff/")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/staff/login/", r["Location"])

    def test_customer_account_is_not_staff(self):
        c = Client()
        c.force_login(self.accounts[0].user)
        self.assertEqual(c.get("/staff/").status_code, 302)

    def test_kitchen_lists_paid_orders_only_and_advances_them(self):
        paid, _ = self.buy()
        unpaid = create_order(self.accounts[1], [(self.item.pk, 1)])
        staff = Client()
        staff.force_login(self.staff)
        page = staff.get("/staff/")
        self.assertContains(page, paid.number)
        self.assertNotContains(page, unpaid.number)
        staff.post(f"/staff/order/{paid.pk}/advance/", {"from_status": "RECEIVED"})
        paid.refresh_from_db()
        self.assertEqual(paid.status, Order.PREPARING)
