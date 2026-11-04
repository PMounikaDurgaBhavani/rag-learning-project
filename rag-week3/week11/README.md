# Week 11 — Production observability, cost, and the failure → test loop

The same CloudDesk application as Weeks 3–10, with every request it answers now recorded as a
**traceable, searchable, costed record**, and a **support drill** that goes from a vague complaint to the
wrong answer, to a regression case, to a RED → GREEN fix — all inside the existing chatbot UI.

Open it: `python main.py ui` → sidebar **Observability** (or `http://localhost:8000/#week11`).
Demo script: [DEMO_GUIDE.md § Step 12](../DEMO_GUIDE.md). Artefacts: this folder (table at the end).

## What was built (and where)

| Piece | Where | What it does |
|---|---|---|
| Request context + spans | `src/observability.py` | A thread-local request with named spans (`retrieval`, `validation.distance_gate`, `generation`, `validation.grounding`, `validation.assertions`, `mcp.connect`, `tool:<name>`), each with a UTC start, measured duration, status and error. Opened by the server routes and the pipeline entry points; stages wrap themselves in `obs.span(...)`. |
| Instrumented paths | `src/generator.py`, `src/ticket_reply.py`, `src/mcp_chat.py`, `src/server.py` | The real answer path (0.5B, three gates), the ticket drafter (1.5B), the MCP customer chat and the HTTP routes. The ticket drafter also runs the Week 6 assertions on every live draft and stores the result as the request's evaluation. |
| Record fields | `src/tracing.py` → `observability.attach()` | `request_id`, `request_type`, `spans`, `usage` (tokens with their basis), `cost` (labelled estimate), `status`, `retries`, `evaluation`, `policy_check`, `provider`, `fixture`, `http.route`, and `generation.input_tokens` — added to the same `traces/traces.jsonl` record before it is written. |
| Searchable index | `traces/observability.db` (SQLite) | Mirror of the JSONL with indexes on time, prompt version, status, cost, policy verdict, ticket id, and **FTS5 over input and output**. Rebuilt from the JSONL at any time (`observability.backfill()`); the server brings it up to date at start. Historical records are indexed with `has_request_id = 0`, `has_spans = 0` — nothing is back-filled or invented. |
| Policy verdict | `observability.policy_verdict()` | For a ticket with charge facts: does the answer agree with HC-008 (eligible → not denied; ineligible → not promised)? Same classifier as the Week 6 assertions. Stored as an indexed column (the field the first drill showed was missing). |
| Seventh assertion | `src/ticket_assertions.py` → `no_denial_inside_window` | The eligible side of the refund window. Found missing by the drill; added before the fix. |
| Regression case | `week6/eval_cases.jsonl` → `R05` | Verbatim from controlled incident 1's trace; `python main.py eval --frozen` keeps the 25 labelled replies frozen and drafts R05 live. |
| Prompt fix | `src/ticket_reply.py` → `ticket-v1.3` | The HC-008 rule in the system prompt **and** the window comparison made in code (`refund_check_line()`, one line appended to the user turn). The rule alone was probed and was not enough. See `prompt_history.json` and `drill.md §3`. |
| Backend | `experiments/week11_ui.py`, routes in `src/server.py` | Drill, cost by stage, 10× assessment, evidence, the fixed-command suite run, the two controlled incidents. |
| UI | `src/static/index.html` → view `week11` | Live chat · Trace explorer · Support drill · Failure → fix · Cost & usage · 10× capacity · Prompt & deployment. The Chat view's answers carry a **View trace · observability** button; the Agent Chatbot's execution details link to the same record. |
| Artefacts | `experiments/week11_report.py` | Writes `drill.md`, `cost_by_stage.md`, `tenx.md`, `trace.json`, `drill_results.json` from the live index (the drill is executed and timed when the report is written). |
| Tests | `tests/test_week11.py` | Spans, record derivation, the policy classifier, the index (temp SQLite), the drill, cost/10× consistency, every API route over a socket, bad-input codes. No model, nothing written to `traces/` or `week11/`. |

**Judge caveat.** The RED and GREEN runs use the seven deterministic assertions (`--judge none`). Judge v2 was started
for the RED run and aborted after 35 minutes at 2 of 26 (the 1.7B judge after the 1.5B drafter does not fit this 8 GB
machine's memory in a workable time; `test_results/aborted_RED_with_judge_v2.txt`). The regression case and the new
assertion live in the deterministic layer, so the before/after evidence is complete for what the fix changes; the
judge can be added with `python main.py eval` on a larger machine.

## Routes

```
GET  /api/week11                      index stats, prompt versions, fixtures, saved evidence, docs
GET  /api/week11/requests?q=&field=both|input|output&mode=and|or&from=&to=&prompt_version=&kind=&source=
                                      &status=&policy_verdict=&trace_id=&request_id=&min_cost=&outliers=1
                                      &no_fixtures=1|fixtures_only=1&limit=≤200&offset=&order=asc|desc
GET  /api/week11/trace?trace_id=      the record, its spans, usage, cost, policy check, missing_fields
GET  /api/week11/cost?days=7|30|90|all&kind=&no_fixtures=1
GET  /api/week11/tenx?days=30
GET  /api/week11/evidence             RED / GREEN suite runs, the new case, prompt versions
POST /api/week11/drill                {complaint, as_of?: YYYY-MM-DD, procedure?: slices|indexed}
POST /api/week11/eval/run             {judge: none|auto}   — fixed command, one at a time (409), output saved
POST /api/week11/incident/plant       {fixture_id: refund-window-inverted|duplicate-charge-denied, force?}
```

Parameters are validated (400 on bad input), result sizes are bounded (`limit` ≤ 200), the two actions that
load a model take the server's one-run-at-a-time lock, and no browser parameter ever reaches a shell.

## Terminal equivalents

```bash
python main.py week11 drill                                   # the complaint → the record, timed
python main.py week11 drill --procedure indexed --as-of 2026-10-23
python main.py week11 plant refund-window-inverted            # controlled incident 1 through the real drafter
python main.py week11 eval --label "UI run"                   # Week 6 suite: 25 frozen + R05 live, saved under test_results/
python main.py eval                                           # the full suite, every reply drafted fresh (+ judge v2)
python main.py week11 report                                  # regenerate drill.md, cost_by_stage.md, tenx.md, trace.json
python tests/test_week11.py                                   # 70+ checks, no model
```

## What is measured, estimated, or missing — the rules this week follows

- **Measured by the application:** latency per request and per span, tokens (counted with the model's own tokenizer),
  retrieved context ids, retries, the assertion result, the policy verdict.
- **Estimated and labelled as such:** cost = tokens × the stand-in rate card the repo has used since Week 7
  (`agent_runtime.RATE_CARD`, USD 0.15 / 0.60 per M tokens). The models run in-process; no provider bill exists.
- **Unmetered and said so:** retrieval (embedding, pgvector, BM25, cross-encoder), validation code, tool calls — time
  only, $0 in the total.
- **Missing and shown as missing:** request ids and spans on records written before Week 11; input tokens on old
  ticket records (so their cost is *not computable*, not zero); a customer id (the log never had one, so the drill
  cannot slice by user).
- **Controlled incidents** are real requests through the real drafter, labelled `fixture` on the record and in every view.

## Artefacts in this folder

| File | What |
|---|---|
| `drill.md` | the support drill: measured time-to-find for both procedures, the search slice, the incident source, findings, missing fields, the bonus drill, canary/rollback |
| `drill_results.json` | the two drill runs behind `drill.md`, verbatim |
| `trace.json` | complete records (trace + spans + usage + cost + missing fields) for the controlled incidents and the latest chat answer |
| `cost_by_stage.md` | retrieval / generation / validation / tools, per-query stats, by model, type and prompt version, pricing assumptions |
| `tenx.md` | the tenfold projection and the one-line bottleneck conclusion |
| `prompt_history.json` | `ticket-v1.0 → v1.3`, what changed and why, and the two-line canary/rollback plan |
| `incidents/*.json` | the two controlled incidents: ticket, expected policy, the answer actually produced, trace id, spans |
| `test_results/suite_*.{json,txt}` | every suite run started from the UI or `main.py week11 eval`, plus the labelled RED and GREEN full-suite runs |
