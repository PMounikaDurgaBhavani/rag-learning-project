# Week 10 — requirements checklist

Status: COMPLETE, PARTIAL or BLOCKED, with the file that proves it. Dated 2026-10-21.

## Cases and fairness

| requirement | status | evidence |
|---|---|---|
| Ten frozen cases, provenance recorded | COMPLETE | `cases_10.json`: ids, rule, `eval_cases.jsonl` sha, excluded few-shot ids; `run` refuses if the source file changed |
| Dataset ambiguity resolved explicitly | COMPLETE | 25 Week 6 cases vs 10 Week 7 tickets: the brief names Week 6; the deterministic rule is in `README.md` and `cases_10.json`; expected answers untouched |
| Same cases, prompts, reference facts, criteria for both arms | COMPLETE | both arms read the manifest; `results_*.json` `case_ids` are equal (checked by `report` and `tests/test_week10.py`); reference facts never enter a prompt (tested) |
| No special hints for either mode | COMPLETE | the ticket specialist reads the same ticket fields; the one capability difference (code-level eligibility) is documented in `README.md` and `REPORT.md` |

## Multi-agent architecture

| requirement | status | evidence |
|---|---|---|
| Manager: receive, decide specialists, dispatch, collect, resolve conflicts, synthesise, cite, track state, avoid unnecessary calls | COMPLETE | `src/multi_agent.py` `Manager`: `triage` (model call + rule floor + recorded fallback), `dispatch`, `synthesise` with bounded conflict re-synthesis, `routing.decided_by`; knowledge-only tickets skip the ticket specialist (T01, R02, R03, T11, R04, T18) |
| Ticket / support specialist: ticket facts, tier, policy, required actions, known vs missing, never invents | COMPLETE | `TicketSpecialist` + `account_facts` (ticket_policy = HC-008 as code), HC-008 passages via `build_where(article_id)`; findings carry "Missing:" when the charge is absent |
| Knowledge / RAG specialist: existing retrieval, citations, filters, reports missing evidence, no hardcoded content | COMPLETE | `KnowledgeSpecialist` uses `generator.retrieve_for_mode` (hybrid_rerank, markdown, top-5), the 0.75 gate and lexical anchors; "No supporting evidence" when the gate fails |
| Task lifecycle with failure, timeout, cancellation states | COMPLETE | `Task`: CREATED → ASSIGNED → RUNNING → COMPLETED / FAILED / TIMED_OUT / CANCELLED, guarded transitions; TIMED_OUT exercised in tests |
| Recorded parent/child ids, agents, roles, inputs/outputs, hand-off source/destination, timestamps, status, errors, trace ids | COMPLETE | every row in `results_multi.json` carries `tasks`, `handoff_records`, `trace_id`, `langfuse_trace_id`; `handoffs.log` |
| Framework: use the project's own architecture | COMPLETE | plain Python over the existing modules; the HTTP boundary is `http.server`, like the app; no new dependency |

## Benchmark

| requirement | status | evidence |
|---|---|---|
| Reproducible runner, one documented command | COMPLETE | `python main.py week10 all` (`experiments/week10_benchmark.py`), greedy decoding; single-agent answers were byte-identical across two runs |
| Per case: id, mode, question, answer, correctness, groundedness/citations, action completeness, failure/timeout, latency, tokens in/out, cost, calls, hand-offs, trace/task ids | COMPLETE | `results_single.json`, `results_multi.json` rows; action completeness is the judge's RESOLUTION criterion + the six assertions |
| Success/failure rate, quality comparison with explicit criteria | COMPLETE | `race_table.md`, `comparison.json`: pass rule, judge pass, assertions pass, per-mode |
| p50, p95/p99 where meaningful | COMPLETE | reported with the n=10 nearest-rank caveat in every file |
| Tokens total/average, cost per case/overall | COMPLETE | `race_table.md`; stand-in rate card named, never a provider charge |
| Average calls and hand-offs; timeouts, errors, retries | COMPLETE | `race_table.md` |
| Cold start separated from steady state; latency method stated | COMPLETE | warm-up recorded as `cold_start_ms`; "sequential, per case, end to end, model warm" in every run file |
| Honest partial results where a dependency is unavailable | COMPLETE | citations 0/0 reported as "nothing to check"; v1.0 run kept and explained |

## Failure handling

| requirement | status | evidence |
|---|---|---|
| Deterministic automated HTTP 500 test at a real dependency boundary | COMPLETE | `tests/test_week10.py` "Failure" section: real worker thread, injected 500 for one ticket, 200 for others |
| Error detected and recorded; no invented answer; bounded retries; status and traces updated | COMPLETE | tests + `failure_case.md`: 2 attempts, task FAILED with error, root FAILED, routed hand-off, 1 retry counted |
| Timeouts propagate | COMPLETE | HTTP timeout → TIMED_OUT, not retried (tested) |
| API returns a safe response and status | COMPLETE | `POST /api/week10/chat` → 503 `status: degraded` with the routed reply (tested over a socket) |
| Frontend handles failure gracefully | COMPLETE | `w10Post` keeps the 503 payload, shows the card with the error block; other errors show inline + toast; abort after 20 min |
| No secrets or internal exception details leak | COMPLETE | worker returns "`<name> failed`" only; tested with a fake secret in the exception |
| Does not break production services | COMPLETE | fault switch on one specialist + one ticket id in `src/specialist_worker.py`; control case unaffected |
| Test run and result saved | COMPLETE | `test_output.txt`; live experiment in `failure_case.json` |

## Chatbot

| requirement | status | evidence |
|---|---|---|
| Mode selector, interactive questions, real backend path | COMPLETE | `src/static/index.html` Week 10 view (`w10Mode`, `w10Send`), `/api/week10/chat` → `experiments/week10_ui.py` → the same code the benchmark runs |
| Reuses RAG, uploaded documents, ticket context, tools | COMPLETE | same retriever over the Postgres corpus (uploads included), ticket fields in the form |
| Final answer, citations, mode, agent activity and hand-offs | COMPLETE | result card: answer, citations, routing, task table with lifecycle, hand-offs, specialist findings |
| Loading, error, timeout, empty states | COMPLETE | spinner card, inline error + toast, 20-minute abort, empty-state text |
| Example questions from project data | COMPLETE | the 10 manifest cases + 2 not-found questions, served by `/api/week10` |
| Compare the two modes on equivalent questions | COMPLETE | "Compare both" runs single then multi and renders a comparison table |
| Grounded "not found": says so, no hallucinated citation, no manufactured specialist result | COMPLETE | routed hand-off from `ticket_policy.routed_reply`; R02 in the benchmark; tested with far-distance retrieval |
| No hardcoded answers | COMPLETE | every chip goes through `run_chat` |
| Existing tabs preserved | COMPLETE | `tests/test_weeks6_9.py` server checks pass; the pre-existing uncommitted `index.html` changes are intact |

## Observability

| requirement | status | evidence |
|---|---|---|
| Request, mode, retrieval/tool activity, calls, hand-offs, timing, tokens, outcome, errors reconstructible | COMPLETE | run files + `tracing.append` records (`kind: week10_multi_agent`) in `traces/traces.jsonl` (gitignored) |
| Existing Langfuse integration preserved; failures reported accurately | COMPLETE | `langfuse.enabled: true` in the run files; every row has a `langfuse_trace_id`; a tracing error is stored as `trace_error` on the result, never as a success |
| Readable hand-off/trace report from actual execution | COMPLETE | `handoffs.log` is cut from the stored task and hand-off records |

## Deliverables

| requirement | status | evidence |
|---|---|---|
| Checklist | COMPLETE | this file |
| Frozen manifest | COMPLETE | `cases_10.json` |
| Per-case results, both arms | COMPLETE | `results_single.json`, `results_multi.json` |
| Combined comparison, aggregates | COMPLETE | `comparison.json`, `comparison.csv`, `race_table.md` |
| Hand-off/trace evidence | COMPLETE | `handoffs.log`, run files |
| HTTP 500 test output | COMPLETE | `test_output.txt`, `failure_case.md/.json` |
| Comparison report (methodology, results, trade-offs, limitations, eight questions) | COMPLETE | `REPORT.md` |
| Reproduction and demo instructions | COMPLETE | `README.md`, `DEMO_GUIDE.md` step 10 |
| Verdict ≤ 10 lines | COMPLETE | `verdict.md` (checked by the test) |

## Tests

| suite | result |
|---|---|
| `tests/test_week10.py` | all checks pass (82 at the time of the recorded run, incl. the HTTP 500, timeout, API and UI markup checks) |
| `tests/test_pipeline.py` | all 34 pass |
| `tests/test_weeks6_9.py` | all 95 pass (existing routes and views unaffected) |
| `tests/test_rag_live.py` | not run this session (needs the 0.5B chat model; unrelated to Week 10) |

## Known gaps

- Citation correctness could not be measured: neither arm emits `[n]` in customer replies.
- The judge is 76% agreement with humans; its verdicts are reported as model-based.
- In-process specialist timeouts are measured after the fact; only the HTTP transport pre-empts.
- A2A AgentCard / task-lifecycle bonus from the earlier brief: not implemented (the lifecycle exists; no AgentCard JSON).
