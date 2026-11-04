"""Ticket understanding: intent, entities, sensitivity, the required evidence.

Deliberately code, not a model call. The classifier decides which tools MUST
run before an answer is allowed (the "required evidence" plan the agent loop
enforces), so it has to be deterministic and testable. The model still
chooses the order and the arguments in the loop; this module only sets the
floor, as the Week 10 manager's rule floor did.
"""

import re

from agent_guardrails import INSTRUCTION_PATTERNS

ORDER_ID = re.compile(r"\bORD-\d{3,6}\b", re.I)
CUSTOMER_ID = re.compile(r"\bCUS-\d{3,6}\b", re.I)
INVOICE_ID = re.compile(r"\bINV-\d{3,6}\b", re.I)
AMOUNT = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})+|\d+)(\.\d{1,2})?")
DAYS = re.compile(r"\b(\d{1,3})\s+days?\b", re.I)

INJECTION = re.compile("|".join(INSTRUCTION_PATTERNS + [
    r"ignore (all |any )?(of )?(your|the) (instructions|rules|guidelines|policies)",
    r"\bnew instructions?\b", r"\bact as\b", r"\bpretend (to be|you are)\b",
    r"\boverride\b.*\b(policy|instruction)", r"\bdeveloper mode\b", r"\bjailbreak\b",
]), re.I)

INTENT_RULES = [
    # order matters: the first match wins
    ("pii_request", r"(what|which) (email|e-mail|address|phone|card)\b.*(on file|do you have)|paste (them|it) here|"
                    r"(email|address) (do you have|you have) on file|send me (the|my) (address|email)"),
    ("data_deletion", r"delete (all )?(my|our) (personal )?data|close my account|right to (be forgotten|erasure)|gdpr (request|erasure)"),
    ("legal_sensitive", r"\blawyer\b|\battorney\b|\blegal action\b|\bsue\b|\bchargeback\b|\bgdpr complaint\b|\bregulator\b|"
                        r"\bsmall claims\b|\bfraud\b|\bpolice\b"),
    ("duplicate_charge", r"charged (me |us )?twice|double[- ]charged|two charges|charged two times|duplicate (charge|invoice)|"
                         r"charged .{0,30}twice|second charge"),
    ("hardware_doa", r"(will not|won't|does not|doesn't|not) (power|turn|switch) on|dead on arrival|\bdoa\b|arrived (broken|damaged|dead)"),
    ("shipping", r"\bship(ped|ping|ment)?\b|\bdeliver(y|ed)?\b|\bexpedite\b|\bcarrier\b|\btracking\b|not arrived|hasn.t arrived|"
                 r"\barrive\b|\blate\b.*\border\b"),
    ("refund", r"\brefund\w*\b|money back|get (that|it|this) back|\bcharge(d)? back\b|want (my|our) .{0,20}back|reimburse"),
    ("account_access", r"locked out|account (is )?locked|\block(ed)?\b|password|reset link|sign[- ]in|log ?in|2fa|two-factor"),
    ("billing_question", r"\binvoice\b|\bpayment (method|failed)\b|\bcard\b|\bbilling\b|\bcharge\b|\brenewal\b|\bprice\b|\bcost\b"),
    ("out_of_scope", r"\bcompetitor\b|recommend (a|another|a cheaper)|switch to|instead of clouddesk|\bweather\b|\bjoke\b|"
                     r"capital of|write (me )?(a poem|code)"),
    ("knowledge", r".*"),
]

SENSITIVE_INTENTS = ("pii_request", "data_deletion", "legal_sensitive")
EVIDENCE_INTENTS = ("refund", "duplicate_charge", "shipping", "hardware_doa")

# What must have run (successfully) before the loop may answer.
REQUIRED_EVIDENCE = {
    "refund": ["lookup_order", "check_entitlement", "search_help_centre"],
    "duplicate_charge": ["lookup_order", "check_entitlement", "search_help_centre"],
    "shipping": ["lookup_order", "check_entitlement", "search_help_centre"],
    "hardware_doa": ["lookup_order", "check_entitlement", "search_help_centre"],
    "billing_question": ["search_help_centre"],
    "account_access": ["search_help_centre"],
    "knowledge": ["search_help_centre"],
    "pii_request": ["escalate_ticket"],
    "data_deletion": ["escalate_ticket"],
    "legal_sensitive": ["escalate_ticket"],
    "out_of_scope": ["escalate_ticket"],
}


def classify_intent(text):
    lowered = (text or "").lower()
    for intent, pattern in INTENT_RULES:
        if re.search(pattern, lowered, re.I | re.S):
            return intent
    return "knowledge"


def entities(text, history_texts=()):
    """Ids and numbers in this message, falling back to earlier turns for ids."""
    found = {"order_ids": [], "customer_ids": [], "invoice_ids": [], "amounts": [], "days": []}
    for source, weight in ((text, "current"), *((t, "earlier") for t in history_texts)):
        for oid in ORDER_ID.findall(source or ""):
            oid = oid.upper()
            if oid not in found["order_ids"]:
                found["order_ids"].append(oid)
        for cid in CUSTOMER_ID.findall(source or ""):
            if cid.upper() not in found["customer_ids"]:
                found["customer_ids"].append(cid.upper())
        for inv in INVOICE_ID.findall(source or ""):
            if inv.upper() not in found["invoice_ids"]:
                found["invoice_ids"].append(inv.upper())
    for whole, frac in AMOUNT.findall(text or ""):
        try:
            found["amounts"].append(float(whole.replace(",", "") + (frac or "")))
        except ValueError:
            pass
    found["days"] = [int(d) for d in DAYS.findall(text or "")]
    return found


def understand(message, ticket=None, history=()):
    """The understanding record the agent loop and the trace both carry."""
    ticket = ticket or {}
    history_texts = [h.get("text", "") for h in history if h.get("role") == "customer"]
    intent = classify_intent(message)
    inherited = None
    if intent in ("knowledge", "billing_question", "account_access") and history_texts:
        # "And how long until the money is back on my card?" after a refund turn is still the
        # refund conversation: a weak intent inherits the last account intent of the thread,
        # so the order and entitlement are checked again instead of a bare article search.
        earlier = [classify_intent(t) for t in history_texts]
        for prior in reversed(earlier):
            if prior in EVIDENCE_INTENTS:
                inherited, intent = prior, prior
                break
    injected = bool(INJECTION.search(message or ""))
    ents = entities(message, history_texts)
    if ticket.get("order_id") and ticket["order_id"].upper() not in ents["order_ids"]:
        ents["order_ids"].append(ticket["order_id"].upper())
    required = list(REQUIRED_EVIDENCE.get(intent, ["search_help_centre"]))
    # A refund or shipping question with no order id anywhere still needs the
    # order: lookup_order by customer lists what exists, and the decision
    # step escalates when nothing matches.
    return {
        "intent": intent,
        "sensitive": intent in SENSITIVE_INTENTS,
        "needs_account_evidence": intent in EVIDENCE_INTENTS,
        "injection_detected": injected,
        "entities": ents,
        "required_evidence": required,
        "escalate_directly": intent in SENSITIVE_INTENTS or intent == "out_of_scope",
        "intent_inherited_from_history": inherited,
        "classifier": "rule-based (src/capstone/understanding.py), not a model",
    }
