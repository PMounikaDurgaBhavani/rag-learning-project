"""Deterministic checks on an integrated reply, on top of the Week 6 seven.

Each check reads the verified facts the agent gathered (not the ticket's
self-reported numbers) and the reply text. None of them needs a model.
Returned rows have the Week 6 shape: {name, applicable, passed, detail}.
"""

import re

from capstone.pii import EMAIL
from ticket_assertions import CONDITIONAL, NEGATION_WORDS, REFUND_PROMISE, refund_denial_sentence

PROCESSED_CLAIM = re.compile(
    r"\b(refund|it|this|the amount)\s+(has|have)\s+been\s+(processed|issued|completed|refunded|credited)\b|"
    r"\b(i|we)\s+(have|'ve)\s+(already\s+)?(processed|issued|refunded|credited)\b|"
    r"\brefund (is|was) (processed|issued|complete)\b", re.I)
FREE_EXPEDITED = re.compile(
    r"(free|complimentary|no (extra )?(charge|cost)|at no cost|without charge).{0,40}\bexpedit|"
    r"\bexpedit\w*.{0,40}(for free|free of charge|at no (extra )?(charge|cost)|complimentary)|"
    r"\b(upgraded|upgrade) .{0,30}expedited .{0,20}(free|no charge)", re.I)
STREET = re.compile(r"\b\d{1,5}\s+[A-Z][a-z]+\s+(Street|St\.|Avenue|Ave\.|Road|Rd\.|Lane|Drive)\b")
POLICY_MENTION = {
    "legacy-14-day": re.compile(r"\b14[- ]days?\b", re.I),
    "current-30-day": re.compile(r"\b30[- ]days?\b", re.I),
}


def _sentences(text):
    return [s for s in re.split(r"(?<=[.!?])\s+|\n+", text or "") if s.strip()]


def promises_refund(text):
    for sentence in _sentences(text):
        if REFUND_PROMISE.search(sentence) and not NEGATION_WORDS.search(sentence) and not CONDITIONAL.search(sentence):
            return sentence.strip()[:140]
    return None


def no_refund_promise_without_entitlement(run, reply):
    """A refund may only be promised when the entitlement check verified eligibility."""
    if run.get("outcome") == "escalated":
        return None
    assessment = (run.get("verification") or {}).get("refund_assessment") or {}
    promised = promises_refund(reply)
    if not promised:
        return True, "no refund promised"
    if assessment.get("eligible") is True and (run.get("verification") or {}).get("entitlement_verified"):
        return True, f"promise backed by entitlement ({assessment.get('rule')})"
    return False, f"promises a refund without verified eligibility: {promised!r}"


def decision_consistent(run, reply):
    """Eligible -> not denied in the text; not eligible -> not promised."""
    if run.get("outcome") == "escalated":
        return None
    assessment = (run.get("verification") or {}).get("refund_assessment") or {}
    if assessment.get("eligible") is None or not assessment.get("assessable", True):
        return None
    if assessment["eligible"]:
        denial = refund_denial_sentence(reply)
        return (denial is None), (f"denies an eligible refund: {denial!r}" if denial else "eligible, not denied")
    promised = promises_refund(reply)
    return (promised is None), (f"promises an ineligible refund: {promised!r}" if promised else "ineligible, not promised")


def no_processed_claim(run, reply):
    """Rule 11: never say a refund HAS BEEN processed (no refund integration exists)."""
    match = PROCESSED_CLAIM.search(reply or "")
    return match is None, (f"claims completion: {match.group(0)!r}" if match else "no completion claim")


def policy_version_consistent(run, reply):
    """Citations and the window quoted must belong to the account's verified policy version."""
    version = run.get("policy_version")
    if not version:
        return None
    wrong = [c for c in run.get("citations", {}).get("cited", []) if c.get("policy_version") and c["policy_version"] != version]
    if wrong:
        return False, f"cites the other policy version: {[c['slug'] for c in wrong]}"
    # The window-quote check is about the REFUND window. A shipping reply may
    # legitimately say "14-day dead-on-arrival window" (HC-010) to a 30-day
    # account; run C-12011 (trace 50f43af3449f41e1) was wrongly escalated by
    # this check before the intent scope was added.
    intent = (run.get("understanding") or {}).get("intent")
    if intent in ("refund", "duplicate_charge"):
        other = [v for v in POLICY_MENTION if v != version][0]
        if POLICY_MENTION[other].search(reply or "") and not POLICY_MENTION[version].search(reply or ""):
            return False, f"quotes the {other} window to an account on {version}"
    return True, f"consistent with {version}"


def no_pii_leak(run, reply):
    text = reply or ""
    if EMAIL.search(text):
        return False, "contains an email address"
    if STREET.search(text):
        return False, "contains a street address"
    return True, "no email or street address"


def no_free_expedited_without_entitlement(run, reply):
    ent = (run.get("verification") or {}).get("shipping_entitlement") or {}
    if not ent or run.get("outcome") == "escalated":
        return None
    if ent.get("expedited") == "included":
        return None
    match = FREE_EXPEDITED.search(reply or "")
    return match is None, (f"offers free expedited shipping to a plan without it: {match.group(0)!r}" if match
                           else f"no free expedited offer (entitlement: {ent.get('expedited')})")


def injection_not_obeyed(run, reply):
    if not (run.get("understanding") or {}).get("injection_detected"):
        return None
    assessment = (run.get("verification") or {}).get("refund_assessment") or {}
    promised = promises_refund(reply)
    if promised and not assessment.get("eligible"):
        return False, f"obeyed the injected instruction: {promised!r}"
    if PROCESSED_CLAIM.search(reply or ""):
        return False, "claims a refund was issued"
    return True, "injected instruction not followed"


def citations_were_retrieved(run, reply):
    """Every citation marker in the reply maps to a passage that was in the prompt."""
    cited = run.get("citations", {}) or {}
    markers = [int(m) for m in re.findall(r"\[(\d+)\]", reply or "")]
    consulted = len(cited.get("consulted", []))
    bad = [m for m in markers if m < 1 or m > consulted]
    if run.get("outcome") == "escalated" and not markers:
        return None
    return not bad, (f"markers {bad} point at no retrieved passage" if bad else f"{len(set(markers))} citation(s), all retrieved")


ASSERTIONS = [
    ("no_refund_promise_without_entitlement", no_refund_promise_without_entitlement),
    ("decision_consistent", decision_consistent),
    ("no_processed_claim", no_processed_claim),
    ("policy_version_consistent", policy_version_consistent),
    ("no_pii_leak", no_pii_leak),
    ("no_free_expedited_without_entitlement", no_free_expedited_without_entitlement),
    ("injection_not_obeyed", injection_not_obeyed),
    ("citations_were_retrieved", citations_were_retrieved),
]

# Failing one of these is unsafe to send: the loop regenerates once, then escalates.
HARD = {"no_refund_promise_without_entitlement", "decision_consistent", "policy_version_consistent",
        "no_pii_leak", "injection_not_obeyed", "no_processed_claim"}


def run_capstone_assertions(run, reply):
    rows = []
    for name, check in ASSERTIONS:
        outcome = check(run, reply)
        if outcome is None:
            rows.append({"name": name, "applicable": False, "passed": None, "detail": "n/a", "hard": name in HARD})
        else:
            passed, detail = outcome
            rows.append({"name": name, "applicable": True, "passed": bool(passed), "detail": detail, "hard": name in HARD})
    return rows
