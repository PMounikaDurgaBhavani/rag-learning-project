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
                 r"\barrive\b|\blate\b.*\border\b|where('s| is| are) (my|the|our) (order|package|parcel|shipment|phones?|devices?)|"
                 r"status of (my|the|our) (order|package|parcel|shipment)"),
    ("refund", r"\brefund\w*\b|money back|get (that|it|this) back|\bcharge(d)? back\b|want (my|our) .{0,20}back|reimburse"),
    ("account_access", r"locked out|account (is )?locked|\block(ed)?\b|password|reset link|sign[- ]in|log ?in|2fa|two-factor"),
    ("billing_question", r"\binvoice\b|\bpayment (method|failed)\b|\bcard\b|\bbilling\b|\bcharge\b|\brenewal\b|\bprice\b|\bcost\b"),
    ("out_of_scope", r"\bcompetitor\b|recommend (a|another|a cheaper)|switch to|instead of clouddesk|\bweather\b|\bjoke\b|"
                     r"capital of|write (me )?(a poem|code)"),
    # Conversational turns that need no tool and no model: answered from a template.
    ("identity", r"what('s| is| are) your (name|names)\b|who are you\b|who am i (talking|speaking|chatting) (to|with)|"
                 r"are you (a |an )?(bot|robot|human|real person|person|machine|ai|assistant|chatbot)\b|"
                 r"what can you (do|help( me)? with)\b|how can you help\b|what are you\b"),
    ("greeting", r"^\W*(hi|hello|hey|hiya|good (morning|afternoon|evening)|thanks|thank you|thank you very much|thanks a lot|"
                 r"ok|okay|great|perfect|cheers|bye|goodbye|good night)(\W+(there|team|clouddesk|again|so much|very much))?\W*$"),
    # An order id with no other intent: the customer is asking about that order.
    ("order_inquiry", r"\bORD-\d{3,6}\b"),
    ("knowledge", r".*"),
]

# Words that make a message a CloudDesk support question. A message with none of them, no
# id and no specific intent is "unsupported": it is answered with a clarification, never
# routed to the order, entitlement or help-centre tools (bug report 2026-11-04: "what is
# react" after a refund turn was answered with a refund denial).
SUPPORT_VOCAB = re.compile(
    r"clouddesk|\baccount\b|\border(s|ed)?\b|\brefund|\binvoice|\bbill|\bcharg|\bpay|\bcard\b|subscri|\bplan\b|\bseat|"
    r"licen[cs]e|renew|cancel|upgrade|downgrade|\btrial\b|\bprice|\bcost|\bship|\bdeliver|\btrack|\bcarrier|\bphone|\bdevice|"
    r"hardware|headset|\bdesk\b|badge|reader|password|\blog ?in\b|sign[- ]?in|\block(ed|out)?\b|\breset\b|\b2fa\b|two-factor|"
    r"\bmfa\b|\bemail\b|\bupload|\bfile\b|\bpermission|\badmin|\brole\b|\buser\b|\bteam\b|workspace|organi[sz]ation|"
    r"\bsetting|dashboard|\bapp\b|mobile|desktop|\bcall(s|ing)?\b|\bvoip\b|\bsip\b|\bnumber\b|\bport(ing)?\b|extension|"
    r"voicemail|recording|integration|\bapi\b|webhook|\bexport\b|\bdata\b|\bdelete|privacy|\bgdpr\b|\bsupport\b|\bticket|"
    r"\bagent\b|\bhelp\b|\bpolicy|\bterms\b|warranty|\breturn|\breplace|\bbroken|not working|\berror|\bbug\b|\bcrash|"
    r"\bissue|\bproblem|\bfeature|how (do|can|should) (i|we)\b|how to\b|\bcan'?t\b|\bcannot\b|\bunable\b|doesn'?t work|"
    r"notification|credential|\bmy (order|refund|money|invoice|account|plan|phone|device|subscription)\b|\bORD-|\bCUS-|\bINV-|\bHC-\d",
    re.I)

# Does this turn point back at the thing the thread is about? Only such a turn inherits the
# earlier turn's refund / shipping / order context; a new, unrelated question never does.
CONTINUATION = re.compile(
    r"\b(the|my|our|that|this) (money|refund|order|charge|payment|shipment|package|parcel|delivery|replacement|phones?|devices?|headsets?)\b|"
    r"\b(money|refund) (is |will be |gets? )?back\b|back (on|to) my (card|account)|"
    r"\b(is|was|has|have|did|does|will|would|can|could|track|refund|cancel|return|replace|expedite|about|ship|deliver|receive|get|got|send|sent)"
    r"\s+(it|that|this|them|those)\b(?!\s+(possible|ok|okay|true|normal|safe|allowed|fine|free|necessary|required|better|worth|mean))|"
    r"\b(it|that|this|they)\s+(is|was|has|hasn'?t|isn'?t|still|yet|arrived|arrive|shipped|back|refundable|late|delayed|here|coming|eligible|"
    r"on its way|in transit)\b|"
    r"^\W*(and|also|so|then|what about|any (update|news)|update\??|status\??|eta\??)\b|\b(still|yet|again)\b",
    re.I)

SENSITIVE_INTENTS = ("pii_request", "data_deletion", "legal_sensitive")
EVIDENCE_INTENTS = ("refund", "duplicate_charge", "shipping", "hardware_doa", "order_inquiry")
DIRECT_REPLY_INTENTS = ("identity", "greeting", "unsupported")     # template answer, no tools, no model
INHERITING_INTENTS = ("knowledge", "unsupported", "billing_question")  # weak intents that MAY be follow-ups

# What must have run (successfully) before the loop may answer.
REQUIRED_EVIDENCE = {
    "refund": ["lookup_order", "check_entitlement", "search_help_centre"],
    "duplicate_charge": ["lookup_order", "check_entitlement", "search_help_centre"],
    "shipping": ["lookup_order", "check_entitlement", "search_help_centre"],
    "hardware_doa": ["lookup_order", "check_entitlement", "search_help_centre"],
    "order_inquiry": ["lookup_order", "check_entitlement", "search_help_centre"],
    "billing_question": ["search_help_centre"],
    "account_access": ["search_help_centre"],
    "knowledge": ["search_help_centre"],
    "pii_request": ["escalate_ticket"],
    "data_deletion": ["escalate_ticket"],
    "legal_sensitive": ["escalate_ticket"],
    "out_of_scope": ["escalate_ticket"],
    "identity": [],
    "greeting": [],
    "unsupported": [],
}


def classify_intent(text):
    lowered = (text or "").lower()
    for intent, pattern in INTENT_RULES:
        if re.search(pattern, lowered, re.I | re.S):
            if intent == "knowledge" and not SUPPORT_VOCAB.search(lowered):
                return "unsupported"
            return intent
    return "knowledge"


# Intent-aware query expansion (seam #29). A customer's informal wording ("when i gave wrong
# password") sits far from the article wording in embedding space; the agent re-searches ONCE
# with the canonical help-centre terms for the concepts the message itself mentions. Nothing
# is added for a concept the customer did not raise, so a 2FA or SSO question gets no expansion.
QUERY_SYNONYMS = {
    "account_access": [
        (r"wrong password|password (is |was )?wrong|incorrect password|password (is )?incorrect|"
         r"(typed|entered|gave|put|used) .{0,25}password|password (doesn't|does not|won't|will not|isn't|not) (work|accepted|working)|"
         r"invalid (password|credentials|login)|password (rejected|refused|keeps getting rejected)", "incorrect password login failed"),
        (r"\bforgot|forgotten|don'?t (remember|know) (my|the) password|lost (my|the) password|can'?t remember", "forgot password reset password"),
        (r"\block", "account locked after failed login attempts"),
        (r"reset (link|email|mail|e-mail)|(didn'?t|did not|never|haven'?t|have not) (get|got|receive|received|arrive|arrived)|not receiv|no email",
         "password reset email not received"),
        (r"log ?in|sign ?in|logging in|signing in|can'?t get (in|back in)|get back in", "login sign in"),
        (r"\breset\b", "password reset"),
    ],
}


def expand_query(message, intent):
    """(expanded query, canonical terms added) for the intent, or (None, []) when nothing applies."""
    terms = []
    for pattern, term in QUERY_SYNONYMS.get(intent, []):
        if re.search(pattern, message or "", re.I) and term not in terms:
            terms.append(term)
    if not terms:
        return None, []
    return f"{(message or '').strip()} ({'; '.join(terms)})", terms


def is_follow_up(text):
    """True when the message refers back to the thread's subject rather than opening a new one."""
    text = (text or "").strip()
    if re.match(r"\W*what time\b", text, re.I):      # "what time is it" is not about the order
        return False
    return bool(CONTINUATION.search(text))


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
    inherited, follow_up = None, False
    if intent in INHERITING_INTENTS and history_texts:
        # "And how long until the money is back on my card?" after a refund turn is still the
        # refund conversation, so the order and entitlement are checked again. Only a turn that
        # points back at the thread's subject inherits; "what is react" after the same refund
        # turn is a new question and keeps its own (unsupported) intent.
        follow_up = is_follow_up(message)
        if follow_up:
            earlier = [classify_intent(t) for t in history_texts]
            for prior in reversed(earlier):
                if prior in EVIDENCE_INTENTS:
                    inherited, intent = prior, prior
                    break
    injected = bool(INJECTION.search(message or ""))
    needs_account = intent in EVIDENCE_INTENTS
    # Ids from earlier turns are carried only into a turn that needs the account evidence
    # (a direct refund/shipping/order question, or an inherited follow-up); an identity or
    # general question never drags an earlier order along.
    ents = entities(message, history_texts if needs_account else ())
    if ticket.get("order_id") and ticket["order_id"].upper() not in ents["order_ids"]:
        ents["order_ids"].append(ticket["order_id"].upper())
    required = list(REQUIRED_EVIDENCE.get(intent, ["search_help_centre"]))
    # A refund or shipping question with no order id anywhere still needs the
    # order: lookup_order by customer lists what exists, and the decision
    # step escalates when nothing matches.
    return {
        "intent": intent,
        "sensitive": intent in SENSITIVE_INTENTS,
        "needs_account_evidence": needs_account,
        "injection_detected": injected,
        "entities": ents,
        "required_evidence": required,
        "escalate_directly": intent in SENSITIVE_INTENTS or intent == "out_of_scope",
        "direct_reply": intent in DIRECT_REPLY_INTENTS,
        "follow_up": follow_up,
        "intent_inherited_from_history": inherited,
        "classifier": "rule-based (src/capstone/understanding.py), not a model",
    }
