# Week 10 — Single agent vs manager + two specialists

The Week 6 ticket-reply task, built twice and raced on the same ten tickets:

- **Single agent** — `src/ticket_reply.py`, unchanged: retrieve, one model call, reply.
- **Multi-agent** — `src/multi_agent.py`: a manager triages the ticket, dispatches a
  ticket specialist and a knowledge specialist, and writes the reply from their
  findings under the same reply rules.

Same model (Qwen2.5-1.5B-Instruct), same decoding, same retrieval
(hybrid RRF + cross-encoder, markdown index, top-5, 0.75 distance gate with the
lexical-anchor bypass), same judge (Week 6 judge v2 + the six assertions). Neither
path ever sees the reference facts.

Results: **[race_table.md](race_table.md)** · verdict: **[verdict.md](verdict.md)** ·
write-up: **[REPORT.md](REPORT.md)** · requirements: **[checklist.md](checklist.md)**.

## One command

```bash
python main.py week10 all                      # manifest, both arms, judge, report, failure experiment
```

or step by step:

```bash
python main.py week10 manifest                 # the 10 cases -> week10/cases_10.json (deterministic rule)
python main.py week10 run --arm both           # -> week10/runs/single_<stamp>.json, multi_<stamp>.json
python main.py week10 judge                    # assertions + judge v2 -> results_single.json, results_multi.json
python main.py week10 report                   # -> comparison.json/.csv, race_table.md, handoffs.log
python main.py week10 failure --case T07 --specialist ticket_specialist   # -> failure_case.md/.json
python tests/test_week10.py                    # 70 checks, no model, no database
```

`run --transport http` routes the specialists through `src/specialist_worker.py`
over HTTP instead of in-process (the failure experiment always does).

## Which ten cases, and why

Week 6 has 25 mode-tagged cases; the brief asks for ten. The rule, applied by code
and recorded in `cases_10.json`: for each Week 5 mode M1–M5, the first case in file
order, then the first policy-backed case in file order (or the next case when the
mode has none), skipping the two cases judge v2 carries as few-shot examples
(T02, T13), because the judge has seen those tickets with their human verdicts.

| case | mode | kind | ticket |
|---|---|---|---|
| T01 | M1 | knowledge | password requirements |
| T03 | M1 | policy | $49, 12 days: refund |
| R02 | M2 | knowledge, regression | "billing" |
| T07 | M2 | policy | duplicate $89, Priority |
| R03 | M3 | knowledge, regression | expired card, payment failed |
| T11 | M3 | knowledge | reset password step by step |
| R04 | M4 | knowledge, regression | "Account locked. Help." |
| T16 | M4 | policy | $75, 76 days, wants a discount |
| T18 | M5 | knowledge | account locked |
| T21 | M5 | policy | $30, 29 days: refund |

Expected answers are the Week 6 reference facts, untouched. Both arms read the
ids from the manifest; the manifest records the sha of `eval_cases.jsonl` and the
`run` command refuses if the file has changed since.

## The multi-agent path

```
manager        triage ─── one model call: {"ticket_specialist": bool, "knowledge_specialist": bool}
   │             rule floor: a charge or refund request on the account always needs the ticket specialist
   │             policy:     the knowledge specialist is always consulted (every reply must be grounded)
   ├─ dispatch ─> ticket specialist     account facts via ticket_policy (HC-008 as code):
   │   <─ return   tier, escalation tag, charge, days, duplicate, eligible / not eligible / missing;
   │               retrieves HC-008 passages (article filter, top-3); one model call writes findings
   ├─ dispatch ─> knowledge specialist  the single agent's retrieval and gate on the message;
   │   <─ return   one model call lists source-backed findings, citing [n]; cites nothing -> no evidence
   └─ synthesis   one model call under ticket_reply.TICKET_SYSTEM_PROMPT (the single agent's rules),
                  sources = both specialists' passages, findings attached; the ticket specialist's
                  determination is authoritative, and a reply that contradicts it is rewritten once
```

Every unit of work is a `Task` with a lifecycle `CREATED → ASSIGNED → RUNNING →
COMPLETED | FAILED | TIMED_OUT | CANCELLED`, parent/child ids, timestamps, tokens
and latency; every dispatch and return is a `Handoff`. `handoffs.log` is cut from
those records, never inferred from the answer.

**Not found.** If the knowledge specialist's retrieval fails the gate, or its
findings cite no source, and the account has nothing to assess, the manager returns
`ticket_policy.routed_reply` (ticket id, escalation tag when due, hand-off to a
person) without calling synthesis. Same text the single agent returns when its
gate refuses.

**Failure.** Specialists are reached through a transport. The HTTP transport
retries a 5xx once (`RETRY_POLICY`, max 2 attempts, no retry on timeout) and then
raises; the manager marks the task FAILED or TIMED_OUT, records the error, and
degrades to the routed hand-off. It never synthesises a refund decision without
the specialist that owns the account facts. The API returns that reply with
**HTTP 503, status `degraded`**, so a client cannot mistake it for a normal answer.

**The one capability difference.** The ticket specialist decides refund
eligibility in code (`ticket_policy.refund_eligible`, the HC-008 constants) and
hands the determination to the synthesis; the single agent has to do that
arithmetic from the passages. That is the architecture under test, not a hint
from the reference answers: the specialist reads the ticket fields both arms see.

## What is measured, and how

- **Pass** = every applicable deterministic assertion passes AND judge v2 says PASS
  on RESOLUTION. The judge is model-based; its agreement with 25 blind human labels
  is in `week6/agreement.json` (76%, 78.3% held out, kappa 0.41) and is reported
  next to every judge number.
- **Citations** (deterministic): the articles the reply cites are a subset of the
  articles the reference facts name. Denominator = replies that cite anything.
- **Latency**: sequential, per case, end to end (retrieval and every model call),
  after a warm-up that loads the LLM, embedder, BM25 index and cross-encoder; the
  warm-up is recorded as the cold start and charged to no case. p50 is the median
  (as Weeks 7–8); p95 and p99 are nearest-rank, which with n=10 are the slowest
  case, and are labelled as such.
- **Tokens**: counted per model call with the model's own tokenizer on the exact
  rendered chat template, summed per case. The context re-send multiplier is total
  prompt tokens divided by the ticket block's own tokens.
- **Cost**: tokens × the Week 7 stand-in rate card ($0.15 / $0.60 per Mtok). The
  models run locally; this is never a provider charge.
- Ten cases is a demonstration, not a sample large enough for significance claims.

## Files

| file | what |
|---|---|
| `cases_10.json` | the manifest: ids, rule, source sha, excluded few-shot ids |
| `runs/single_*.json`, `runs/multi_*.json` | every case in full: answer, retrieval, tasks, hand-offs, calls, tokens, trace ids |
| `results_single.json`, `results_multi.json` | the same rows plus assertions, judge verdict and reason, pass, citation check |
| `comparison.json`, `comparison.csv` | aggregates per arm and the per-case side-by-side; everything re-derived from the rows |
| `race_table.md` | the table |
| `handoffs.log` | one line per hand-off and per task, from the stored records |
| `failure_case.md`, `failure_case.json` | the HTTP 500 experiment: worker log, attempts, task states, the degraded reply, the control case |
| `verdict.md` | ten lines |
| `REPORT.md` | methodology, results, the eight questions, limitations |
| `checklist.md` | requirement by requirement, with evidence |
| `test_output.txt`, `benchmark_output.txt` | the test run and the benchmark console, as captured |
| `runs/live/` | what the UI's chatbot ran, one file per question |

## The UI

`python main.py ui` → sidebar **Multi-agent benchmark**. The **Chatbot** tab has a
mode selector (single, multi, compare both), the ticket fields the Week 6 tickets
carry (tier, charge, days, duplicate, refund requested), the ten benchmark questions
and two the help centre cannot answer as chips, and shows the answer, citations,
routing, every task with its lifecycle, hand-offs, tokens, cost, latency, errors and
the trace id. The **Failure experiment** tab runs a case against an injected HTTP 500
live. The other tabs render the files above. In the **User** role the view is the
agent chatbot alone.
