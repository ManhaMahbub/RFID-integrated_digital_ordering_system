# RFID Canteen Kiosk Platform

Cashless canteen ordering with university RFID cards. Django + SQLite.

## Run it (Windows PowerShell / macOS / Linux)

```
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py seed_demo      # demo menu, 3 student accounts, staff and admin users
python manage.py test           # 41 tests should pass
python manage.py runserver
```

## Demo walk-through

| Who | Where | Login |
| --- | --- | --- |
| Customer | http://127.0.0.1:8000/kiosk/ | No login to browse. Card CARD0001, PIN 1111 (also CARD0002 / 2222, CARD0003 / 3333), 1000 BDT each |
| Kitchen staff | http://127.0.0.1:8000/staff/ | kitchen / kitchen1234 |
| Admin (data, ledger, history) | http://127.0.0.1:8000/admin/ | admin / admin1234 |

1. Open the kiosk. The menu is open to everyone; choose items with + and −.
2. Press Go to order and check the order.
3. At payment, tap the card (type the card number) and enter the PIN, then pay.
4. The receipt shows the order number and refreshes every 5 seconds.
5. On the kitchen page press Start preparing, Mark ready, Mark served. The customer's receipt follows.

Screenshots of this flow are in `docs/screenshots/`.

## Build status

| Block | What | Status |
| --- | --- | --- |
| 0 | Setup, database, seed data | done |
| 1 | Ledger core and `pay_order()` | done |
| 2 | Kiosk flow: open menu, order page, card and PIN at payment, receipt | basic version done |
| 3 | Kitchen screen with status buttons and history | basic version done |
| 4 | Admin / university console (sales, reports) | Django admin only; custom reports next |
| 5 | Daily rent statement | next |
| 6 | Peak simulation, test report, demo | after 5 |

## Rules the code enforces

- Balance is never stored; it is the sum of ledger entries. Entries cannot be edited or deleted.
- `core/services.py` is the only place money or order status changes.
- An order is created only when payment succeeds: a wrong PIN, unknown card or low balance leaves nothing behind.
- Paying twice with the same key charges once; paying a paid order with a new key is rejected.
- Order status moves one step forward only (Received, Preparing, Ready, Served), only by staff, and a stale click cannot skip a step.
- Every status change is recorded with time and staff user.

## Known gaps (documented, not hidden)

- No PIN lockout after repeated wrong PINs.
- Card tap is a typed card number (USB readers type the number and press Enter); no real reader integration yet.
- Demo passwords and the development secret key must be replaced before any real deployment.
