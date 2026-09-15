# Week 6 — Two disagreements, who was right, and how the prediction scored

_Fill this in after `judge --version v2`. Numbers come from
`week6/agreement.json`; verdicts and reasons come from
`week6/judge_results_v1.json` / `_v2.json`._

## Headline

- **agreement_before** (judge v1): __ % (__/25), Cohen's kappa __
- **agreement_after** (judge v2): __ % (__/25), held-out __ % (__/23)
- **assertions vs judged criteria:** 6 vs 1

The held-out figure excludes the two cases that became few-shot examples. Scoring
the judge on examples it was shown is leakage, so of the two numbers, the held-out
one says whether the judge actually improved.

## Disagreement 1 — `__` (mode __)

- **Ticket:** …
- **Reply (abridged):** …
- **Human:** PASS/FAIL, because …
- **Judge v1:** PASS/FAIL, because "…"
- **Who was right:** human / judge. After re-reading the reference facts: …
- **Judge v2 on this case:** …

## Disagreement 2 — `__` (mode __)

- **Ticket:** …
- **Reply (abridged):** …
- **Human:** …
- **Judge v1:** …
- **Who was right:** …
- **Judge v2 on this case:** …

## Prediction vs outcome

- **Prediction** (`prediction.txt`, commit `____`): "…"
- **What actually changed:** which v1 disagreements v2 fixed, which it did not, and any case v1 got right that v2 now gets wrong
- **Where the prediction was wrong:** …

## Pass rate by mode (from `python main.py eval`)

Paste the table here. Name any mode whose rate hides behind the overall average.
