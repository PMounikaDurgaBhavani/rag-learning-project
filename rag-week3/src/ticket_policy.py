"""Support-ticket policy constants and the one way a ticket is rendered.

Kept free of model imports: the drafting path, the deterministic assertions,
the labelling tool and the judge all read these, and only the first of those
needs a model loaded.

The numbers mirror HC-008 (samples/article_08_refunds_escalation.md). The
article is what the model is given; these constants are what code checks
the reply against.
"""

PRIORITY_TIER = "Priority"
ESCALATION_TAG = "[ESCALATED-PRIORITY]"
REFUND_WINDOW_DAYS = 30


def refund_eligible(ticket):
    """HC-008: within 30 days of the charge, or a duplicate charge at any age."""
    if ticket.get("duplicate_charge"):
        return True
    days = ticket.get("days_since_charge")
    return days is not None and days <= REFUND_WINDOW_DAYS


def ticket_block(ticket):
    """The ticket exactly as the model, the labeller and the judge all see it."""

    lines = [
        f"Ticket ID: {ticket['ticket_id']}",
        f"Support tier: {ticket['tier']}",
    ]

    amount = ticket.get("charge_amount")
    if amount is not None:
        charge = f"${amount:,.2f}, charged {ticket['days_since_charge']} days ago"
        if ticket.get("duplicate_charge"):
            charge += " (a duplicate of an earlier charge on the same invoice)"
        lines.append(f"Charge on account: {charge}")

    lines.append(f"Customer message: {ticket['message']}")
    return "\n".join(lines)


def routed_reply(ticket):
    """What the customer receives when the draft path cannot answer.

    A hand-off still quotes the ticket ID and carries the escalation tag:
    a refusal with no ticket reference is not sendable at all, and whether
    refusing was *right* is the judge's question, not a formatting one.
    """

    lines = []
    if ticket["tier"] == PRIORITY_TIER:
        lines.append(ESCALATION_TAG)
    lines.append(
        f"{ticket['ticket_id']}: Thanks for contacting CloudDesk support. "
        "I couldn't find the answer to this in our help centre, so I've "
        "passed your ticket to a specialist who will follow up."
    )
    return "\n".join(lines)
