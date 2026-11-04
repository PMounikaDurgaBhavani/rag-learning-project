"""The three business tools the deflection agent calls, and their schemas.

    lookup_order        one order (or a customer's orders): status, dates, product,
                        shipping status. Never returns a street address.
    check_entitlement   plan, tier, the refund policy VERSION that governs this
                        account, the window, shipping entitlement, and - when an
                        order is given - the refund assessment made in code
    escalate_ticket     opens an escalation in the persistent local queue

Each result carries an `integration` label saying it came from a local demo
adapter. Records and rules stay separate, as in Week 7: lookup_order reads
no policy and check_entitlement decides nothing about the reply text.
"""

from capstone import escalation_queue
from capstone.config import INTEGRATION_LABELS, POLICY_VERSIONS
from capstone.demo_data import customers, days_since, orders, subscriptions

ORDER_STATUSES = ("charged", "refunded", "cancelled", "duplicate")


def _norm(value):
    return str(value or "").strip().upper()


def _present_order(order):
    shipping = None
    if order.get("shipping"):
        s = order["shipping"]
        address = s.get("address") or {}
        shipping = {k: v for k, v in s.items() if k != "address"}
        # The destination is city + country only. The street address exists
        # in the record and is never returned by this tool.
        shipping["destination"] = ", ".join(x for x in (address.get("city"), address.get("country")) if x)
    return {
        "found": True, "order_id": order["order_id"], "customer_id": order["customer_id"],
        "product": order.get("product"), "kind": order.get("kind", "subscription"),
        "amount": order.get("amount"), "currency": order.get("currency", "USD"),
        "charge_date": order.get("charge_date"), "days_since_charge": days_since(order.get("charge_date")),
        "order_status": order.get("order_status"), "invoice_id": order.get("invoice_id"),
        "shipping": shipping,
        "integration": INTEGRATION_LABELS["orders"],
    }


def lookup_order(order_id=None, customer_id=None):
    """One order by id (optionally checked against the customer), or a customer's orders."""
    oid, cid = _norm(order_id), _norm(customer_id)
    if not oid and not cid:
        return {"found": False, "error": "missing_identifier",
                "detail": "give an order_id or a customer_id", "integration": INTEGRATION_LABELS["orders"]}
    if oid:
        order = orders().get(oid)
        if order is None:
            return {"found": False, "error": "order_not_found", "order_id": oid,
                    "integration": INTEGRATION_LABELS["orders"]}
        if cid and order["customer_id"] != cid:
            # Week 7's long-thread audit: a real order belonging to another
            # customer must not be acted on.
            return {"found": False, "error": "order_customer_mismatch", "order_id": oid, "customer_id": cid,
                    "integration": INTEGRATION_LABELS["orders"]}
        return _present_order(order)
    mine = sorted((o for o in orders().values() if o["customer_id"] == cid),
                  key=lambda o: o.get("charge_date") or "", reverse=True)
    return {"found": bool(mine), "customer_id": cid, "orders": [_present_order(o) for o in mine],
            "error": None if mine else "no_orders_for_customer", "integration": INTEGRATION_LABELS["orders"]}


def refund_assessment(subscription, order):
    """Eligibility decided in code from the verified policy version and the order.

    Mirrors ticket_policy.refund_eligible (Weeks 6-11) with the window taken
    from the account's policy version instead of the HC-008 constant.
    """
    window = subscription["refund_window_days"]
    days = days_since(order.get("charge_date"))
    status = order.get("order_status")
    duplicate = status == "duplicate"
    out = {"order_id": order["order_id"], "assessable": True, "days_since_charge": days,
           "window_days": window, "policy_version": subscription["refund_policy_version"],
           "policy_article_id": subscription["policy_article_id"], "order_status": status,
           "duplicate_charge": duplicate, "amount": order.get("amount")}
    if status == "refunded":
        out.update(eligible=False, rule="already_refunded",
                   reason="the order was already refunded; tell the customer when it was issued")
    elif status == "cancelled":
        out.update(eligible=None, rule="escalate_to_billing",
                   reason="a cancelled order is settled by billing, not by the refund window")
    elif duplicate:
        out.update(eligible=True, rule="refund_full_any_age",
                   reason=f"duplicate charge: refunded in full regardless of the {window}-day window")
    elif days is None:
        out.update(eligible=None, rule="escalate_missing_charge_date", reason="no charge date on the order")
    elif days <= window:
        out.update(eligible=True, rule="refund_if_within_window",
                   reason=f"charged {days} days ago, within the {window}-day window of the {subscription['refund_policy_version']} policy")
    else:
        out.update(eligible=False, rule="refund_if_within_window",
                   reason=f"charged {days} days ago, outside the {window}-day window of the {subscription['refund_policy_version']} policy")
    return out


def check_entitlement(customer_id, order_id=None):
    """The account's plan, tier, refund policy version and shipping entitlement."""
    cid, oid = _norm(customer_id), _norm(order_id)
    sub = subscriptions().get(cid)
    if sub is None:
        return {"verified": False, "error": "customer_not_found", "customer_id": cid,
                "integration": INTEGRATION_LABELS["entitlement"]}
    version = POLICY_VERSIONS.get(sub["refund_policy_version"], {})
    result = {
        "verified": True, "customer_id": cid, "plan": sub["plan"], "tier": sub["tier"],
        "subscription_status": sub["status"], "term": sub["term"], "renewal_date": sub["renewal_date"],
        "refund_policy": {
            "version": sub["refund_policy_version"], "label": version.get("label"),
            "article_id": sub["policy_article_id"], "window_days": sub["refund_window_days"],
            "duplicate_charge_rule": "a duplicate charge is refunded in full at any age",
            "terms_effective": sub["policy_terms_effective"],
        },
        "shipping_entitlement": {**sub["shipping_entitlement"], "hardware_included": sub["hardware_included"]},
        "integration": INTEGRATION_LABELS["entitlement"],
    }
    if oid:
        order = orders().get(oid)
        if order is None:
            result["refund_assessment"] = {"order_id": oid, "assessable": False, "reason": "order_not_found"}
        elif order["customer_id"] != cid:
            result["refund_assessment"] = {"order_id": oid, "assessable": False, "reason": "order_customer_mismatch"}
        else:
            result["refund_assessment"] = refund_assessment(sub, order)
    return result


def escalate_ticket(ticket_id, reason, priority="normal", evidence=None, notes=None, trace_id=None):
    """Open an escalation in the local queue and return its reference."""
    if not ticket_id or not reason:
        return {"escalated": False, "error": "missing_arguments", "detail": "ticket_id and reason are required",
                "integration": INTEGRATION_LABELS["escalation"]}
    if priority not in ("low", "normal", "high", "urgent"):
        priority = "normal"
    record = escalation_queue.create(ticket_id, reason, priority=priority, evidence=evidence,
                                     notes=notes, trace_id=trace_id)
    return {"escalated": True, "reference": record["reference"], "queue": record["queue"],
            "assigned_to": record["assigned_to"], "status": record["status"],
            "integration": INTEGRATION_LABELS["escalation"]}


def customer_summary(customer_id, include_pii=False):
    """What the authorised UI may show about a customer. PII only when asked for."""
    c = customers().get(_norm(customer_id))
    if not c:
        return None
    out = {"customer_id": c["customer_id"], "name": c["name"], "company": c.get("company"),
           "tier": c["tier"], "plan": c["plan"], "region": c.get("region"),
           "policy_terms_version": c.get("policy_terms_version")}
    if include_pii:
        out["email"] = c.get("email")
    return out


TOOL_SCHEMAS = [
    {"type": "function", "function": {
        "name": "lookup_order",
        "description": ("Look up one order by its order id (ORD-nnnn), or list a customer's orders by customer id. "
                        "Returns status, amount, charge date, days since the charge, product and shipping status. "
                        "Reads no policy and decides nothing."),
        "parameters": {"type": "object", "properties": {
            "order_id": {"type": "string", "description": "Order id such as ORD-5001."},
            "customer_id": {"type": "string", "description": "Customer id such as CUS-201."}}}}},
    {"type": "function", "function": {
        "name": "check_entitlement",
        "description": ("Verify the customer's plan, tier, which refund policy version governs the account "
                        "(current 30-day or legacy 14-day) and the shipping entitlement. Pass the order id too and "
                        "it returns the refund eligibility assessment for that order. Required before any refund "
                        "or shipping promise."),
        "parameters": {"type": "object", "properties": {
            "customer_id": {"type": "string", "description": "Customer id such as CUS-201."},
            "order_id": {"type": "string", "description": "Order id to assess, such as ORD-5001."}},
            "required": ["customer_id"]}}},
    {"type": "function", "function": {
        "name": "escalate_ticket",
        "description": ("Hand the ticket to a human agent. Use when evidence is missing or conflicting, the request is "
                        "legal, security or privacy sensitive, or outside the help centre's scope."),
        "parameters": {"type": "object", "properties": {
            "ticket_id": {"type": "string"},
            "reason": {"type": "string", "description": "Why a person must handle it."},
            "priority": {"type": "string", "enum": ["low", "normal", "high", "urgent"]},
            "notes": {"type": "string"}},
            "required": ["ticket_id", "reason"]}}},
]

TOOLS = {"lookup_order": lookup_order, "check_entitlement": check_entitlement, "escalate_ticket": escalate_ticket}
TOOL_NAMES = tuple(TOOLS)


def call_tool(name, arguments):
    """Run one business tool. Bad names and arguments come back as results, not exceptions."""
    if name not in TOOLS:
        return {"error": "unknown_tool", "name": name, "available": list(TOOLS)}
    arguments = arguments if isinstance(arguments, dict) else {}
    try:
        return TOOLS[name](**arguments)
    except TypeError as error:
        return {"error": "bad_arguments", "name": name, "detail": str(error)}
