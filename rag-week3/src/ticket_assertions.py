"""Deterministic checks on a drafted ticket reply.

Every check here was a criterion in the judge prompt
(week6/judge_v0_presplit.txt). Each is a property code decides exactly: a
string is present, a number equals another number, a count reached a
limit. Asking a model to guess at those adds cost and an off day and buys
nothing, so they were deleted from the judge in week6/judge_v1.txt, which
now grades a single criterion.

An assertion returns None when it does not apply to the case (no refund
requested, no charge on the account), so a pass rate is never inflated by
checks that had nothing to check.

A reply is a dict: {"text", "refused", "output_tokens", "max_new_tokens"}.
"""

import re

from ticket_policy import (
    ESCALATION_TAG,
    PRIORITY_TIER,
    REFUND_WINDOW_DAYS,
    refund_eligible,
)

# A money-like number that is not part of an identifier such as CD-10403.
NUMBER = re.compile(r"(?<![\w-])\$?(\d{1,3}(?:,\d{3})+|\d+)(\.\d{1,2})?(?![\w-])")

REFUND_WORDS = re.compile(r"\b(refund\w*|money back|reimburs\w*)", re.I)
PROMISE_WORDS = re.compile(
    r"\b(will|we'll|i'll|shall|going to|has been|have been|is being|"
    r"approved|issued|processed|initiated|eligible|entitled|guarantee\w*)\b",
    re.I,
)
NEGATION_WORDS = re.compile(
    r"\bnot\b|n't\b|\bcannot\b|\bunable\b|\bineligible\b|\boutside\b|"
    r"\bonly (if|when|within)\b|\bno refund",
    re.I,
)

# Prompt scaffolding that should never reach a customer: source headers
# from build_context, the sentinel, and the ticket field labels.
SCAFFOLDING = re.compile(
    r"\(article [A-Z]+-\d+|\bfile article_\d+|\bupdated \d{4}-\d{2}-\d{2}\b|"
    r"NOT_IN_SOURCES|^\s*(Sources|Question|Customer message|Charge on account)\s*:",
    re.I | re.M,
)

# Re-tokenising decoded text can come out a token or two short of what was
# generated, so a reply within this margin of the limit counts as cut off.
TOKEN_LIMIT_MARGIN = 3


def _amounts(text):
    values = []
    for whole, fraction in NUMBER.findall(text):
        try:
            values.append(float(whole.replace(",", "") + (fraction or "")))
        except ValueError:
            pass
    return values


def ticket_id_echoed(case, reply):
    ticket_id = case["ticket"]["ticket_id"]
    found = ticket_id.lower() in reply["text"].lower()
    return found, f"{ticket_id} {'found' if found else 'missing'}"


def refund_amount_numeric(case, reply):
    ticket = case["ticket"]
    if (
        not ticket.get("refund_requested")
        or ticket.get("charge_amount") is None
        or not refund_eligible(ticket)
    ):
        return None

    expected = ticket["charge_amount"]
    found = _amounts(reply["text"])
    ok = any(abs(value - expected) < 0.005 for value in found)
    return ok, f"expected {expected:.2f}; numbers in reply: {found or 'none'}"


def escalation_tag_iff_priority(case, reply):
    """Tag present on Priority tickets — and absent on Standard ones, where
    it would page a Tier 2 agent for nothing."""
    tier = case["ticket"]["tier"]
    tagged = ESCALATION_TAG in reply["text"]
    return tagged == (tier == PRIORITY_TIER), (
        f"tier={tier}, tag {'present' if tagged else 'absent'}"
    )


def no_refund_outside_window(case, reply):
    ticket = case["ticket"]
    if not ticket.get("refund_requested") or refund_eligible(ticket):
        return None

    for sentence in re.split(r"(?<=[.!?])\s+|\n+", reply["text"]):
        if (
            REFUND_WORDS.search(sentence)
            and PROMISE_WORDS.search(sentence)
            and not NEGATION_WORDS.search(sentence)
        ):
            return False, f"promises a refund: {sentence.strip()[:120]!r}"

    return True, (
        f"no refund promised ({ticket['days_since_charge']} days "
        f"> {REFUND_WINDOW_DAYS})"
    )


def not_cut_off(case, reply):
    if reply.get("refused") or reply.get("output_tokens") is None:
        return None
    limit = reply["max_new_tokens"]
    ok = reply["output_tokens"] < limit - TOKEN_LIMIT_MARGIN
    return ok, f"{reply['output_tokens']} tokens of {limit}"


def no_prompt_scaffolding(case, reply):
    match = SCAFFOLDING.search(reply["text"])
    return match is None, (
        f"leaked: {match.group(0)!r}" if match else "clean"
    )


# (name, check, the judge_v0 criterion it replaced)
ASSERTIONS = [
    ("ticket_id_echoed", ticket_id_echoed, "1. Ticket ID"),
    ("refund_amount_numeric", refund_amount_numeric, "2. Refund amount"),
    ("escalation_tag_iff_priority", escalation_tag_iff_priority, "3. Escalation"),
    ("no_refund_outside_window", no_refund_outside_window, "4. Refund window"),
    ("not_cut_off", not_cut_off, "5. Complete"),
    ("no_prompt_scaffolding", no_prompt_scaffolding, "6. Clean"),
]


def run_assertions(case, reply):
    results = []
    for name, check, replaces in ASSERTIONS:
        outcome = check(case, reply)
        if outcome is None:
            results.append({"name": name, "applicable": False,
                            "passed": None, "detail": "n/a"})
        else:
            passed, detail = outcome
            results.append({"name": name, "applicable": True,
                            "passed": bool(passed), "detail": detail})
    return results
