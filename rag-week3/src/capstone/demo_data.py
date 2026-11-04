"""Read access to the seeded demo data (customers, orders, subscriptions,
tickets). The JSON files are built by seed.py and committed, so the app and
the tests read the same records.
"""

import json
from datetime import date
from functools import lru_cache

from capstone.config import SEED_DIR, as_of


def _load(name):
    path = SEED_DIR / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(f"{path} is missing; run: python main.py capstone seed")
    return json.loads(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=None)
def customers():
    return {c["customer_id"]: c for c in _load("customers")["customers"]}


@lru_cache(maxsize=None)
def orders():
    return {o["order_id"]: o for o in _load("orders")["orders"]}


@lru_cache(maxsize=None)
def subscriptions():
    return {s["customer_id"]: s for s in _load("subscriptions")["subscriptions"]}


@lru_cache(maxsize=None)
def tickets():
    return {t["ticket_id"]: t for t in _load("tickets")["tickets"]}


def reload():
    for fn in (customers, orders, subscriptions, tickets):
        fn.cache_clear()


def days_since(charge_date, reference=None):
    """Whole days between an ISO date and AS_OF (or a given date)."""
    if not charge_date:
        return None
    reference = reference or as_of()
    try:
        return (reference - date.fromisoformat(str(charge_date)[:10])).days
    except ValueError:
        return None
