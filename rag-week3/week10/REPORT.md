# Week 10 — Single agent vs multi-agent on ten Week 6 tickets

Run of 2026-10-21, build `7e44499` plus the Week 10 working tree. Sources:
`runs/single_20261021-083852.json`, `runs/multi_20261021-082404.json`, judged into
`results_single.json` / `results_multi.json`, aggregated into `comparison.json` and
`race_table.md`. Every number below is re-derived from the per-case rows by
`python main.py week10 report`.

## 1. Method

**Task.** The Week 6 ticket-reply task: a customer ticket (id, tier, optional charge,
message) in, a customer-facing reply out. **Cases.** Ten of the 25 Week 6 cases,
chosen by a rule in code (`cases_10.json`): two per Week 5 mode, the first in file
order plus the first policy-backed one, skipping judge v2's two few-shot examples.
Five are refund/policy tickets (T03, T07, T16, T21 and the "billing" regression R02),
five are knowledge tickets, three are verbatim regressions of real Week 5 failures.
Expected answers are the untouched Week 6 reference facts, which neither arm sees.

**Arms.**

- *Single agent* — `src/ticket_reply.py`, unchanged except for two recorded
  numbers (prompt tokens, model latency). Hybrid retrieval on the message, the 0.75
  distance gate with the lexical-anchor bypass, one Qwen2.5-1.5B call under
  `TICKET_SYSTEM_PROMPT`, greedy, 160 new tokens max.
- *Multi-agent* — `src/multi_agent.py`. A manager makes one triage call, dispatches
  a ticket specialist (account facts decided in code from HC-008 via
  `ticket_policy`, HC-008 passages retrieved with an article filter, one model call
  for findings) and a knowledge specialist (the single agent's retrieval and gate on
  the message, one model call for source-backed findings), then one synthesis call
  under the same `TICKET_SYSTEM_PROMPT` with both specialists' passages as the
  numbered sources. A reply that contradicts the ticket specialist's determination
  is rewritten once. Every step is a task with a lifecycle; every dispatch and
  return is a hand-off (`handoffs.log`).

Same model, same decoding, same retrieval configuration, same reply rules. The one
capability the multi-agent arm has and the single agent lacks is the ticket
specialist's code-level eligibility determination, which is the architecture under
test, not a hint from the answers.

**Grading.** Pass = all six Week 6 deterministic assertions that apply pass AND
the Week 6 judge v2 (SmolLM2-1.7B, a different model family) says PASS on the
single criterion RESOLUTION. The judge is model-based: against 25 blind human
labels it agrees 76% (78.3% held out, Cohen's kappa 0.41, `week6/agreement.json`).
Every judge number here carries that caveat; the assertion numbers do not.

**Measurement.** Sequential, one case at a time, end to end (retrieval and every
model call) after a warm-up that loads the LLM, embedder, BM25 index and
cross-encoder; the warm-up (about 70 s) is recorded and charged to no case. Tokens
are counted per model call with the model's own tokenizer on the rendered chat
template and summed. Cost is tokens × the Week 7 stand-in rate card
($0.15 / $0.60 per Mtok); the models run locally, so no provider was charged.
p50 is the median; p95 and p99 are nearest-rank, which with n=10 is the slowest
case. n=10 supports no significance claim; wins and losses are listed per case.

## 2. Results

| | single agent | multi-agent |
|---|---|---|
| pass (assertions AND judge) | **2/10** | **3/10** |
| judge PASS (model-based) | 3/10 | 5/10 |
| assertions pass (deterministic) | 5/10 | 5/10 |
| refused / not found | 1 | 1 |
| p50 latency | 80.9 s | 80.2 s |
| p95 = p99 (slowest case, n=10) | 104.3 s | 216.2 s |
| mean latency | 67.8 s | 76.7 s |
| tokens, total (in / out) | 7,008 (5,665 / 1,343) | 20,327 (17,199 / 3,128) |
| tokens per case | 701 | 2,033 |
| cost per case / total (stand-in) | $0.000166 / $0.001657 | $0.000446 / $0.004456 |
| model calls (per case) | 9 (0.9) | 33 (3.3) |
| hand-offs (per case) | 0 | 28 (2.8) |
| specialist calls | 0 | 14 |
| conflict re-synthesis | — | 1 (T21) |
| context re-send multiplier (mean) | — | 38.8× |
| errors / retries / timeouts | 0 / 0 / 0 | 0 / 0 / 0 |

Per case (full table in `race_table.md`): both pass T01; multi alone passes R03 and
T21; single alone passes T11; neither passes T03, R02, T07, R04, T16, T18. By Week 5
mode the only difference is M5 (0/2 vs 1/2); M2 and M4 are 0/2 for both.

**Largest token-consuming hand-off:** the manager's synthesis on T21, 2,401 tokens
(2,188 in / 213 out), 151.6 s, because it carried both specialists' passages, both
findings, the ticket, and then the rejected first draft plus the rewrite request.

**Citations.** Neither arm put a `[n]` marker in any of the 18 customer-facing
replies, although the instruction asks for one, so "citations inside the reference
articles" is 0/0 for both: nothing to check. The knowledge specialist's internal
findings did cite on 2 of 7 knowledge tickets. The passages each reply was written
from are recorded per case (`retrieved`), which is the groundedness evidence that
exists; the 1.5B model does not reliably emit markers in a reply.

## 3. The eight questions

**1. Does multi-agent improve task success or answer quality?** Slightly, on this
sample, and not for free: 3/10 vs 2/10 on the strict pass, 5/10 vs 3/10 on the judge
alone, equal 5/10 on the deterministic assertions. Two cases flipped to multi (R03,
T21), one flipped to single (T11). With n=10 that is a difference of one case and
cannot be called significant. What the per-case records do show is *why* the two
multi wins happened: on T21 ("$30, 29 days ago") the synthesis first wrote that 29
days was outside the 30-day window; the manager's conflict check against the ticket
specialist's determination caught it and the rewrite was correct. That is a real
mechanism, visible in `resynthesis` on the row, not a lucky sample. On R03 the
knowledge specialist's findings gave the synthesis the exact HC-003 steps the single
agent's reply had buried under generic advice.

**2. What additional latency does it introduce?** Median latency was the same (80.2 s
vs 80.9 s) because most of a case is one long generation either way, but the tail
doubled (216 s vs 104 s): the policy tickets run four or five model calls, and the
re-synthesis on T21 cost a second full synthesis. Mean 76.7 s vs 67.8 s. Latency on
this 8 GB laptop is noisy: the same single-agent answers, byte-identical across two
runs, took a p50 of 79 s in a session run under heavier load and 81 s here, with
individual cases moving by ±50%. Token counts, which are deterministic, are the
reliable cost signal; latency ranks are not.

**3. How do token usage and cost change?** 2.9× the tokens (20,327 vs 7,008) and
2.7× the cost on the stand-in card ($0.000446 vs $0.000166 per case). Input tokens
grow 3.0× because the ticket is re-sent in every call and the synthesis receives both
specialists' passages: the mean context re-send multiplier is 38.8× the ticket's own
token count. Output grows 2.3× because the specialists write findings nobody sends.

**4. How many model calls and hand-offs are added?** 3.3 calls per case instead of
0.9 (triage + one or two specialists + synthesis, plus one rewrite), and 2.8 hand-offs
per case (a dispatch and a return per specialist; 28 in all, 14 specialist calls),
against 0 for the single agent.

**5. Which cases benefit from specialist delegation?** The policy tickets where the
code determination settles the answer: T21 passed only because the determination
overruled the model's arithmetic, and T03/T07 both got the judge's PASS on
resolution for the multi arm (they failed on the cut-off and escalation-tag
assertions, as did the single agent). R03 benefited from the knowledge specialist's
findings being more focused than raw passages.

**6. Which cases are better handled by a single agent?** Plain how-to tickets. T11
("walk me through resetting my password") passed single and failed multi: the
knowledge specialist paraphrased the steps without the source marker, the synthesis
then rewrote them from the paraphrase, and the judge found two steps missing. T18
and R04 went the same way in substance: the extra hop paraphrases twice, and twice
is where a 1.5B model drops a step, leaks a source header (T18 failed
`no_prompt_scaffolding` with "file article_02"), or runs to the 160-token limit
(R04). For a ticket the passages already answer, the hop adds cost and a second
chance to err.

**7. What happens when a specialist or upstream service fails?** Measured, not
designed on paper (`failure_case.md`): with the ticket specialist answering HTTP 500
for CD-10407 (T07) behind the real HTTP worker, the manager tried twice (the policy's
max), marked the task FAILED with the status, attempts and a safe message, recorded
one retry, kept the knowledge specialist's successful return, and did not
synthesise. The customer reply is the routed hand-off with the Priority escalation
tag; the API returns it as HTTP 503 `degraded`. The case fails on
`refund_amount_numeric`, and the judge criterion would fail it on resolution, which
is the honest price of not inventing a refund decision. The control ticket T01 went
through the same worker with the same fault switch and completed normally.

**8. Remaining limitations, and the recommendation.** The judge agrees with humans
76% of the time, and on T16 it called a correct refund denial wrong in both arms;
the assertion numbers are the firmer ground, and there the arms tie. Ten cases
is a demonstration. Latency on this machine is noisy. Neither arm cites in its
replies. The first multi-agent version (v1.0, kept under `v1.0/`) refused three
knowledge tickets because the manager treated uncited findings as no evidence; the
fix recovered the answers but not the passes, which is reported rather than hidden.

**Recommendation: do not adopt the multi-agent path as the default for this task.**
It costs 2.9× the tokens, 3.7× the model calls and a doubled tail latency for one
more pass in ten, and it loses a plain how-to ticket the single agent gets right.
The one component that earned its cost is the ticket specialist's code-level policy
determination plus the manager's conflict check: that is a deterministic tool and
a validation step, and both can be bolted onto the single agent for one extra
model call at most, without the triage call, the knowledge paraphrase, or the
hand-offs. Delegate when a specialist owns facts the model cannot derive; do not
delegate the reading of passages the single call already reads.

## 4. Reproduce

```bash
python main.py week10 manifest && python main.py week10 run --arm both
python main.py week10 judge && python main.py week10 report
python main.py week10 failure --case T07 --specialist ticket_specialist
python tests/test_week10.py
```

About 45 minutes end to end on this machine. `benchmark_output.txt` is the console
of the runs reported here; `v1.0/` holds the first multi-agent run and its report.
