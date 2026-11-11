from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from core.models import Account, MenuItem
from core.services import top_up

MENU = [
    ("Chicken Biryani", "180"), ("Beef Tehari", "170"), ("Khichuri", "120"),
    ("Chicken Burger", "140"), ("Egg Fried Rice", "110"), ("Singara (2 pcs)", "30"),
    ("Samosa (2 pcs)", "30"), ("Tea", "20"), ("Coffee", "50"), ("Mineral Water", "20"),
]
USERS = [("student1", "CARD0001", "1111"), ("student2", "CARD0002", "2222"),
         ("student3", "CARD0003", "3333")]


class Command(BaseCommand):
    help = "Create demo menu, three demo accounts with balance, and an admin user."

    def handle(self, *args, **options):
        User = get_user_model()
        for name, price in MENU:
            MenuItem.objects.get_or_create(name=name, defaults={"price": Decimal(price)})
        for username, uid, pin in USERS:
            user, _ = User.objects.get_or_create(username=username)
            acct, created = Account.objects.get_or_create(user=user, defaults={"card_uid": uid})
            if created:
                acct.set_pin(pin)
                acct.save()
                top_up(acct, "1000", f"seed-{uid}")
        if not User.objects.filter(username="admin").exists():
            User.objects.create_superuser("admin", password="admin1234")
        if not User.objects.filter(username="kitchen").exists():
            User.objects.create_user("kitchen", password="kitchen1234", is_staff=True)
        self.stdout.write(self.style.SUCCESS(
            "Demo data ready. Admin: admin / admin1234. Kitchen staff: kitchen / kitchen1234"))
