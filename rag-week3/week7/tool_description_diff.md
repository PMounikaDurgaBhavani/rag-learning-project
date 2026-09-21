# The third tool: `lookup_refund_policy`

Added to the loop alongside `find_ticket` and `get_order`. One job: given a
tier and an order status, return the rule. It reads no ticket and no order.

## The description, before and after

```diff
-Get the refund information for a customer's order, including the refund
-window, whether the charge can be refunded and whether it needs to be
-escalated.
+Return the refund rule for a (tier, order_status) pair: the refund window in
+days, whether the charge must be escalated, and the rule name. Reads no
+ticket and no order — call find_ticket for the tier and get_order for the
+order_status first, then pass those two values here.
```

**What was wrong with v1.** "Refund information for a customer's order" is a
description of `get_order`, which returns the amount, the charge date, the age
of the charge and the order status. Two tools whose descriptions both claim
"information about the order" give the model no way to tell them apart from
the text alone. v2 names the inputs it takes, the fields it returns, and —
explicitly — what it does not do.

## Parameters: enums, not free strings

```json
"tier":         {"type": "string", "enum": ["standard", "priority"]},
"order_status": {"type": "string", "enum": ["charged", "refunded", "cancelled",
                                            "duplicate", "missing"]}
```

These are the two parameters a model otherwise invents values for — "Priority",
"gold", "not_found". With the enum, `lookup_refund_policy("gold", "charged")`
returns `{"error": "bad_tier", "allowed": ["standard", "priority"]}`, which the
loop hands back as a tool result. Without it, an unknown tier would have to be
guessed at or silently defaulted, and a wrong refund window is a wrong refund.

No overlap with the other two descriptions:

| tool | one job | reads |
|---|---|---|
| `find_ticket` | one ticket record by ticket id | tickets |
| `get_order` | one order record by order id | orders |
| `lookup_refund_policy` | the rule for a (tier, order_status) pair | neither |

## What the measurement says — and does not say

`experiments/week7_tool_probe.py` puts the same fixed transcript to the same
model with only this description changed, at 20 decision points (10 tickets ×
2 points), each with exactly one correct next tool.

| description | correct tool | wrong tool | no tool call at all |
|---|---|---|---|
| v1 (overlapping) | 7/20 (35.0%) | 0 | 13 |
| v2 (sharpened) | 7/20 (35.0%) | 2 | 11 |

**The sharpened description did not improve tool choice.** It is still the
right description — v1 genuinely described `get_order`'s job — but the honest
reading of these numbers is that the overlap was never what was costing the
agent anything: with v1 the model picked the *wrong* tool zero times out of
twenty. Its failure mode is different and worse — at 13 of 20 decision points
it called **no tool at all** and wrote prose instead, and at every one of the
10 `after_ticket` points it failed to go on and fetch the order.

The 0 → 2 wrong picks under v2 (both `lookup_refund_policy` called before
`get_order`) is two events out of twenty. At that sample size it is noise, and
tuning the wording against it would be fitting to it.

**What actually moved the agent's numbers** was not a description at all: it
was the loop refusing a final answer whose facts were never fetched
(`unsupported_by_evidence` in `src/ticket_agent.py`), which names the missing
tool and sends the model back for it. In race 1 the agent called `get_order`
and `lookup_refund_policy` zero times across all 10 tickets and read refund
amounts out of the customer's own sentence.
