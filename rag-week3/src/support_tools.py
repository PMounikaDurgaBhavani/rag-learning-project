"""The three tools the refund-chase agent and the fixed workflow both call.

Same functions, same JSON schemas, same store for both systems — the race is
between the two control flows, not between two sets of tools.

Records and rules are deliberately separate jobs:

    find_ticket            one ticket record, by ticket id
    get_order              one order record, by order id
    lookup_refund_policy   the rule that applies to a (tier, order_status)
                           pair — it reads no ticket and no order

A tool never sees week7/race_set.jsonl: the expected outcomes live there, and
a tool that could read them would be answering its own exam.
"""

import json
from pathlib import Path

STORE_PATH = Path(__file__).resolve().parent.parent / "week7" / "store.json"

# Enum domains, shared by the schemas below and by the workflow's branching.
TIERS = ("standard", "priority")
ORDER_STATUSES = ("charged", "refunded", "cancelled", "duplicate", "missing")

# HC-008 gives Standard 30 days. Priority buys a longer window; a duplicate
# charge is refunded at any age; a cancelled order is a billing question.
REFUND_WINDOW_DAYS = {"standard": 30, "priority": 60}

_STORE = None


def store():
    global _STORE
    if _STORE is None:
        _STORE = json.loads(STORE_PATH.read_text(encoding="utf-8"))
    return _STORE


# ---------------------------------------------------------------------------
# the tools
# ---------------------------------------------------------------------------

def find_ticket(ticket_id):
    """One ticket record, or an error the caller has to handle."""
    ticket = store()["tickets"].get(str(ticket_id or "").strip().upper())
    if ticket is None:
        return {"error": "ticket_not_found", "ticket_id": ticket_id}
    return dict(ticket)


def get_order(order_id):
    """One order record. A missing order is a normal outcome, not a crash:
    it is what opens the escalation path."""
    key = str(order_id or "").strip().upper()
    if not key:
        return {"error": "order_id_missing", "order_status": "missing"}
    order = store()["orders"].get(key)
    if order is None:
        return {"error": "order_not_found", "order_id": order_id,
                "order_status": "missing"}
    return dict(order)


def lookup_refund_policy(tier, order_status):
    """The rule for a (tier, order_status) pair. Reads no ticket and no order."""
    tier = str(tier or "").strip().lower()
    order_status = str(order_status or "").strip().lower()

    if tier not in TIERS:
        return {"error": "bad_tier", "allowed": list(TIERS)}
    if order_status not in ORDER_STATUSES:
        return {"error": "bad_order_status", "allowed": list(ORDER_STATUSES)}

    if order_status == "refunded":
        return {"rule": "already_refunded", "window_days": None, "escalate": False,
                "explanation": "The order was already refunded; tell the customer when it was issued."}
    if order_status == "cancelled":
        return {"rule": "escalate_to_billing", "window_days": None, "escalate": True,
                "explanation": "A cancelled order is settled by billing, not by the refund window."}
    if order_status == "duplicate":
        return {"rule": "refund_full_any_age", "window_days": None, "escalate": False,
                "explanation": "A duplicate charge is refunded in full regardless of age."}
    if order_status == "missing":
        return {"rule": "escalate_order_not_found", "window_days": None, "escalate": True,
                "explanation": "Without an order the refund cannot be assessed; escalate to a human agent."}

    return {"rule": "refund_if_within_window", "window_days": REFUND_WINDOW_DAYS[tier],
            "escalate": False,
            "explanation": f"A {tier} tier charge is refundable within "
                           f"{REFUND_WINDOW_DAYS[tier]} days of the charge date."}


# ---------------------------------------------------------------------------
# schemas
# ---------------------------------------------------------------------------
#
# Enums rather than free strings for tier and order_status: they are the two
# parameters the model would otherwise invent values for ("Priority", "gold",
# "not_found"), and an enum turns that into a validation error the loop can
# hand back instead of a wrong answer nobody notices.

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "find_ticket",
            "description": (
                "Look up one support ticket by its ticket id. Returns the customer id, "
                "the order id the ticket refers to (null when the customer did not give "
                "one), the customer's tier and the ticket status. Does not read orders "
                "and does not decide anything."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ticket_id": {
                        "type": "string",
                        "description": "Ticket id such as CD-7001.",
                    },
                },
                "required": ["ticket_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_order",
            "description": (
                "Look up one order by its order id. Returns the amount charged, the "
                "charge date, how many days ago the charge was made, and the order "
                "status. Returns order_status 'missing' when no such order exists. "
                "Does not read tickets and does not decide anything."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {
                        "type": "string",
                        "description": "Order id such as ORD-5001.",
                    },
                },
                "required": ["order_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lookup_refund_policy",
            "description": (
                "Return the refund rule for a (tier, order_status) pair: the refund "
                "window in days, whether the charge must be escalated, and the rule "
                "name. Reads no ticket and no order — call find_ticket for the tier "
                "and get_order for the order_status first, then pass those two "
                "values here."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "tier": {
                        "type": "string",
                        "enum": list(TIERS),
                        "description": "The customer's support tier.",
                    },
                    "order_status": {
                        "type": "string",
                        "enum": list(ORDER_STATUSES),
                        "description": "The status of the order.",
                    },
                },
                "required": ["tier", "order_status"],
            },
        },
    },
]

TOOLS = {
    "find_ticket": find_ticket,
    "get_order": get_order,
    "lookup_refund_policy": lookup_refund_policy,
}

TOOL_NAMES = tuple(TOOLS)


def call_tool(name, arguments):
    """Run one tool call. Bad names and bad arguments come back as tool results,
    not exceptions: the loop has to be able to hand the error to the model."""

    if name not in TOOLS:
        return {"error": "unknown_tool", "name": name, "available": list(TOOL_NAMES)}

    arguments = arguments if isinstance(arguments, dict) else {}

    try:
        return TOOLS[name](**arguments)
    except TypeError as error:
        return {"error": "bad_arguments", "name": name, "detail": str(error)}
