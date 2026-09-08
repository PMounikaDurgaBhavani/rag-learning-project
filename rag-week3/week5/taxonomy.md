# Failure taxonomy — CloudDesk support assistant

**Sample:** 20 traces · seed `20260907` · drawn from 99
**Read on:** 2026-09-08 · app build `efd07ba` · prompt `v1.0`
**Code changed while coding these traces: none.**
**Committed as:** `c152efb`

| # | Failure mode | Count | % of 20 | Severity | Example trace_id |
|---|---|---|---|---|---|
| 1 | Says "I don't have enough information" to a question the articles do answer | 4 | 20% | annoys the user | `5a50d54f665441d4` |
| 2 | A message that is not one direct question gets a flat refusal | 3 | 15% | annoys the user | `a7051c474be5456e` |
| 3 | The reply stops mid-sentence | 3 | 15% | embarrasses the client | `59b27d3b9fdd4225` |
| 4 | The reply contains text that is not an answer — source headers, invented steps | 2 | 10% | embarrasses the client | `7fe81eaa6cdf466a` |
| 5 | Answers "what should I do" without giving the steps | 1 | 5% | annoys the user | `52df90703dac44be` |

**Traces with at least one mode:** 11 / 20 (55%)
**Traces with nothing wrong:** 9 / 20 (45%) — 5 answered correctly, 4
correctly refused questions the corpus does not cover.

---

## Notes

**Modes 3 and 4 overlap.** `7fe81eaa6cdf466a` and `59b27d3b9fdd4225` each
carry both — a reply that contains text it should not, and which also gets
cut off. They are listed separately because the fixes are different, so
the column sums to 13 across 11 distinct traces.

**Mode 1 has more than one immediate trigger.** In `5a50d54f665441d4` the
answering chunk was ranked first at 0.7659 and the model was never called.
In `c0579a1772764098` and `4feb2225e1a746b4` the model did run and the
output was discarded. The user sees the same sentence either way, which is
why they are one mode here — the diagnosis is next week's job.

**Four modes have a count of 1–3.** At n=20 a mode seen once could be
anywhere from rare to common; only mode 1 has enough observations to rank
with any confidence. A second sample would be needed before trusting the
order below the top row.

**Not a failure mode:** the system refusing pricing, integration and
failed-attempt-count questions. Those facts are genuinely absent from the
six articles, and refusing is correct behaviour.
