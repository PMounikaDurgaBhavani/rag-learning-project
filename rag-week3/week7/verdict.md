# Verdict

**The workflow wins, and none of these ten tickets needs an agent.**

The decision rule asks whether the path varies by input. It does: five of the
ten branch after step 2 — missing order, no order id, cancelled, already
refunded, duplicate charge. But a varying path argues for a branch, not a
loop. The fixed workflow takes those branches with an `if` and scores 10/10,
p50 3.8 s, 179 tokens per ticket, $0.000042. The agent scores 0/10, p50
338.6 s, 4,475 tokens, $0.000746 — 89× slower, 25× more tokens, 18× dearer —
and all ten runs ended on a budget, nine on wall clock.

No ticket class here forces an agent. One would: a chase whose steps are not
enumerable in advance, where each result names the next lookup. All five
branches here are known before the ticket arrives.

*(137 words. Numbers: `week7/race_summary.csv`, run `race_20260921-134848.json`.)*
