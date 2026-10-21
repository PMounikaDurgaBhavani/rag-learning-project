# CloudDesk RAG — Live Demo Guide, Weeks 3–9

One application, one browser tab, every week's evidence. This guide says how to
start it, which screen to open, what to click, what to ask, what should appear,
and what to say. Every number here is read from a file in this repository; the
path is given next to it. Nothing is invented.

The Weeks 3–4 deep dive (chunking sweep, golden set, the one retrieval change)
is kept in full in [DEMO_GUIDE_WEEKS_3_4.md](DEMO_GUIDE_WEEKS_3_4.md).

---

## 1. What the system is, in plain language

Two things share one stack:

**The CloudDesk Help Centre RAG (Weeks 3–5).** Seven mock help-centre articles
(`HC-001`–`HC-006` plus `HC-008`, refunds and escalation) live in PostgreSQL
with pgvector. A question is embedded (`all-MiniLM-L6-v2`), retrieved densely,
retrieved lexically (BM25), fused by Reciprocal Rank Fusion, reranked by a
cross-encoder, then answered by `Qwen2.5-0.5B-Instruct` behind three refusal
gates (distance, model, grounding). Citations are verified by code against the
retrieved chunks, never copied from the model. Every answer writes a trace
(`traces/traces.jsonl`) with the prompt sha, every retrieved chunk and score,
the raw model output and the outcome.

**The ticket-reply agent (Weeks 6–9).** A separate customer-support task on
its own fixtures (`week7/store.json`, `week6/eval_cases.jsonl`): draft a reply
or chase a refund using three tools (`find_ticket`, `get_order`,
`lookup_refund_policy`) and `Qwen2.5-1.5B-Instruct`. Week 6 validates the LLM
judge that grades replies; Week 7 races the agent loop against a fixed
workflow; Week 8 scores the agent's path, not only its answer; Week 9 turns the
tools into MCP servers the agent discovers at runtime.

Everything runs locally on CPU/MPS. No API key is needed. The models:

| Role | Model |
|---|---|
| Embeddings | `sentence-transformers/all-MiniLM-L6-v2` |
| Reranker | `cross-encoder/ms-marco-MiniLM-L-6-v2` |
| Chat generator (Weeks 3–5) | `Qwen/Qwen2.5-0.5B-Instruct` |
| Ticket drafter / agent (Weeks 6–9) | `Qwen/Qwen2.5-1.5B-Instruct` |
| Judge (Week 6) | `HuggingFaceTB/SmolLM2-1.7B-Instruct` |

### The UI, one sidebar

| Sidebar entry | What it is | Backed by |
|---|---|---|
| **Chat** | the real RAG pipeline, with verified citations, refusal reasons and trace ids | `POST /api/ask` → `src/generator.py` |
| **Library** | the documents in Postgres; upload, re-index, delete | `/api/documents`, `/api/upload` |
| **Retrieval inspector** | dense vs BM25 vs fused+reranked stages for the last question | `POST /api/inspect` |
| **Golden set** | Week 4: hit-rate@3 baseline vs hybrid, run live | `POST /api/golden/evaluate` → `src/golden_eval.py` |
| **Error analysis** | Week 5: taxonomy, the 20 sampled traces, replay, prediction | `/api/week5`, `/api/week5/rerun` |
| **Judge validation** | Week 6: cases, assertions vs judge, protocol evidence, agreement | `/api/week6`, `/api/week6/run` → `experiments/week6_eval.py` |
| **Agent vs workflow** | Week 7: the four numbers, per-ticket transcripts, budgets, live runs | `/api/week7`, `/api/week7/workflow`, `/api/week7/agent` → `experiments/week7_ui.py` |
| **Trajectory eval** | Week 8: sequences, gap, mitigation, regression, injection | `/api/week8`, `/api/week8/rescore` → `experiments/week8_trajectory_eval.py` |
| **MCP servers** | Week 9: discovery 2→4, zero-diff swap, wire, errors, risk, gateway | `/api/week9`, `/api/week9/discover`, `/api/week9/wire` → `experiments/week9_ui.py` |
| **Multi-agent benchmark** | Week 10: single vs multi-agent chatbot, the 10 cases, race table, hand-offs, HTTP 500 experiment, verdict | `/api/week10`, `/api/week10/chat` → `experiments/week10_ui.py`, `src/multi_agent.py` |
| **Reports & demo** | this guide and every README, rendered from the files | `/api/reports` |
| **Console** | raw JSON of the last response or trace | — |

Any view and tab opens directly from the URL hash, e.g.
`http://localhost:8000/#week8/regression` or `#week7/budgets`.

One model-backed action runs at a time. While one runs, the header shows
`● running: …` and a second expensive request is refused with HTTP 409 and the
name of the run in progress (so a double click cannot start a duplicate race).
Nothing expensive runs when the app starts.

---

## 2. Prerequisites

- macOS with the project's `venv/` (Python 3.11, `pip install -r requirements.txt`).
- The Postgres cluster at `~/pgdata/clouddesk`, port 5433, database
  `clouddesk_rag`, with pgvector ([SETUP_DB.md](SETUP_DB.md)). `.env` holds
  `DATABASE_URL` (copy `.env.example`).
- The five models above in the Hugging Face cache (they download on first use;
  do this before the demo, not during it).
- Optional: Langfuse keys in `.env`. Without them traces still go to
  `traces/traces.jsonl`. `RAG_LANGFUSE=0` turns the sink off per run.

---

## 3. Start it

```bash
cd rag-week3
LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8 pg_ctl -D ~/pgdata/clouddesk -o "-p 5433" -l ~/pgdata/clouddesk/server.log start
pg_isready -p 5433                     # "accepting connections"
source venv/bin/activate
python main.py status                  # 7 documents · markdown 138 chunks (default)
python main.py ui                      # opens http://localhost:8000
```

`python main.py ui --no-browser --port 8000` if you want to open the tab yourself.
The locale variables are required: without them the cluster fails to start
("postmaster became multithreaded during startup").

Warm the models before anyone is watching: ask one question in Chat (loads
the 0.5B generator, embeddings and reranker, ~30 s), and on *Agent vs workflow
→ Run live* run the workflow once (loads the 1.5B agent model, ~1 min).

---

## 4. The demo script (about 25 minutes)

Each step: **open** → **click** → **what appears** → **say**.

### Step 1 — Chat: an answerable question (Weeks 3–4)

Open **Chat**. Click the preset chip **Reset link validity** (or type
`How long is a CloudDesk password reset link valid?`) and press ↵.

What appears (measured 2026-10-11, build `374d717`):

- pill `✓ Grounded · 3 citations`, mode `hybrid_rerank`, `best distance 0.375 · gate 0.75`
- answer **`30 minutes [1] [2] [3]`**
- *Verified sources*: `HC-001-markdown-15`, `HC-001-markdown-5`, `HC-001-markdown-11`, each with coverage 100%
- a `trace <id>` pill and a **Trace … ↗** button

Say: "Citations are not the model's: each retrieved chunk is scored against
the answer for content-word coverage and for numbers it asserts; only chunks
that pass are cited, then the markers are renumbered. The answer came from
HC-001 and the three cited chunks are three of the five retrieved."

Click **Retrieval stages →**: the inspector shows the dense list, the BM25 list
and the fused + reranked list side by side, with `d`/`b` ranks and `rrf`
scores. Click **Trace … ↗**: the Console shows the full trace record (prompt
version and sha, every retrieved chunk with distance/bm25/rrf/rerank, the raw
model output, the outcome, the build).

### Step 2 — Chat: the trap (unsupported question)

Click **Refusal test** (`What is the maximum file size limit for CloudDesk uploads in megabytes?`).

What appears: `✕ Refused`, reason **`model_reported_not_in_sources`**,
`best distance 0.2729`, and a collapsible *What the retrieved sources actually
say* with the HC-006 chunks.

Say: "This retrieves *closer* than every answerable question in the set —
0.27 against 0.375 for the last one. A distance gate can say the topic is in
the corpus; it cannot say the fact is. The model gate and the grounding gate
catch it. Six of six unsupported questions are refused, zero hallucinated."

### Step 3 — Chat: ambiguous and out-of-scope

Type `How do I fix it?` → `✕ Refused`, **`distance_gate (1.4422 > 0.75)`**.
Say: "Too vague to retrieve anything close. Open ⚙ Settings, set *Product
area* to Billing and ask again, or use the Library's filters: the retrieval
is filtered at query time, which the Week 3 wrong-filter run proved (0/8 hits
with a deliberately wrong area while still returning five chunks)."

Type `What is the capital of France?` → refused by the distance gate at ≈1.79.

Type `My card expired and payment failed in HC-003. What do I do?` → the exact
identifier `HC-003` is a *lexical anchor* that bypasses the distance gate
(`gate_bypass` pill), HC-003 chunks are retrieved, and the 0.5B model's answer
is then refused as **`ungrounded_answer`**. Say so plainly: "This is the
generation ceiling, not retrieval — the right chunks are in context (open
*Retrieval stages*), the 0.5B model paraphrases them beyond what any single
chunk supports, and the grounding gate refuses rather than ship it. That
ceiling is why the ticket work in Weeks 6–9 uses the 1.5B model."

### Step 4 — Golden set (Week 4)

Open **Golden set**. The 12 questions with pre-declared `expected_chunk_id`
are listed (6 with an exact identifier). Click **Run evaluation**.

What appears: hit-rate@3 **66.7% → 83.3%** (8/12 → 10/12), 2 of 4 retrieval
failures fixed (GS-01, GS-06), 0 regressions, 3 promoted to rank 1, and
per-question before/after ranks. Latency p50 on this Postgres backend is
≈28 ms for both arms; the authoritative Week 4 figure is 12.63 → 13.09 ms
(commit `efd07ba`, inside the ±0.63 ms noise band).

Say: "One change — BM25 fused with the dense list by RRF, k = 60. Everything
else is held constant: embedding model, chunking, chunk size, overlap, index,
the 25-candidate pool, k = 3, the golden set. Four labels were corrected on
2026-10-11 by a rule that never looked at retrieval output; the revision is in
[results.md](results.md) §2.1 and the original numbers are in git."

Source: `evaluation/golden_ab_results.json`, `evaluation/golden_set.jsonl`.

### Step 5 — Error analysis (Week 5)

Open **Error analysis**.

- *Taxonomy* tab: five modes from 20 open-coded traces (`week5/taxonomy.md`).
- *The 20 traces* tab: seed `20260907`, 20 of 99, selection rule shown; click
  any row for the full trace. **↻ Rerun these 20 on the current build**
  re-asks every one and shows then/now outcomes (20 model calls, ~1–2 min;
  the run is saved under `week5/reruns/`).
- *Prediction* tab: `week5/prediction.md`, committed `c152efb` on 2026-09-08
  **before** any fix: "raising the distance gate 0.75 → 0.85 drops mode 1
  from 20% to under 10% on a fresh seeded sample (seed 20260915)".

Say: "The prediction is dated and committed before the change, with the three
things that would prove it wrong written down."

**Prediction experiment status (2026-10-11).** The two traffic arms were run
today on build `374d717` by `experiments/week5_prediction.py traffic`:
92 traces each at thresholds 0.75 and 0.85, isolated under
`traces/week5_prediction/`, manifests in `week5/prediction_run/arm_t0.75.json`
and `arm_t0.85.json` (sha-bound, 0 failures). The seeded sample was drawn
(`week5/prediction_run/sample_20260915.json`: 20 of 92; 1 of the 20 differs
from its 0.75 twin at a gate). **The production threshold is unchanged at
0.75**; the arm passes 0.85 per call. What remains is the human step the tool
refuses to do: open-code the 20 traces in
`week5/prediction_run/notes_20260915.md` (mode 1 present/absent, exactly as
in the original pass), then `python experiments/week5_prediction.py score`
evaluates the committed criteria. Until that is done, say "the arms are run
and sampled; the verdict needs my 20 labels", not a number.

### Step 6 — Judge validation (Week 6)

Open **Judge validation**.

- *Overview*: 25 cases, **6 assertions vs 1 judged criterion**, latest pass
  rate, agreement before → after.
- *Pass rate by mode*: click **Run eval** with *Assertions only* (seconds; it
  checks the 25 frozen replies and prints pass rate per mode M1–M5). The
  terminal equivalent is `python main.py eval --frozen`. The warning box
  "the average hides M5" is the point: per-mode rates, not one number.
- *The 25 cases*: each tagged with one Week 5 mode (M1 7 · M2 5 · M3 5 · M4 4 ·
  M5 4). **R01–R04 are regression cases replayed verbatim from real failed
  Week 5 traces**; each shows its `source_trace_id` (e.g. R01 ←
  `5a50d54f665441d4`, refused by the distance gate at 0.7659) and the
  original failure. The other 21 are clearly synthetic.
- *Judge prompts*: **v0 → v1** diff — the six deterministic criteria (ticket
  ID, refund amount, escalation tag, refund window, cut off, leaked headers)
  deleted from the prompt and moved to `src/ticket_assertions.py`; v1 judges
  one binary criterion, RESOLUTION, and tells the model not to grade the rest.
- *Protocol evidence*: computed live from git. Today it shows `replies_25.json`
  frozen at `b594458` (2026-09-15) and `labels_25.json` **not on disk**.

Say what is true: "The protocol is built and enforced by the tool — `label`
refuses once a judge result exists, `judge` refuses until the labels are
committed and sha-bound to the frozen replies, `make-judge-v2` refuses examples
v1 did not get wrong and refuses before `prediction.txt` is committed. The
blind labelling is mine to do and is not done yet, so there is no
agreement_before/after number to show; I will not quote one."

**To complete Week 6 (your steps, in this order, each committed):**

```bash
python experiments/week6_eval.py label                 # 25 blind labels, shuffled, mode hidden
git add week6/labels_25.json && git commit -m "week6: blind labels"
python experiments/week6_eval.py judge --version v1    # agreement_before -> judge_results_v1.json
# write week6/prediction.txt (one falsifiable sentence) and commit it
python experiments/week6_eval.py make-judge-v2 --examples ID,ID   # two of v1's own disagreements
python experiments/week6_eval.py judge --version v2    # agreement_after, same labels
python experiments/week6_eval.py status                # protocol_evidence.md with commit ordering
# then write week6/disagreements.md (who was right, with evidence) and score the prediction
```

After that the *Agreement*, *Protocol evidence* and *Write-up* tabs fill in
from the files, with no code change.

Bonus tabs already wired: *RAGAS* (local re-implementation, policy-section
check for the 7 policy-backed cases, "faithfully wrong" detection) and
*G-Eval*, both gated on the labels like the judge; their `--smoke` outputs on
3 hand-written replies are shown meanwhile.

### Step 7 — Agent vs workflow (Week 7)

Open **Agent vs workflow**.

- *The four numbers* (`week7/race_summary.csv`, run `race_20260921-134848`,
  budgets 8 laps / 8000 tokens / $0.01 / 300 s):

| | agent (loop) | workflow (3 fixed steps) |
|---|---|---|
| pass rate | **0/10 = 0.0%** | **10/10 = 100.0%** |
| p50 latency | **338.6 s** | **3.8 s** |
| total tokens | **44,748** (4,475/ticket) | **1,793** (179/ticket) |
| cost per ticket | **$0.000746** | **$0.000042** |
| model calls | 49 | 10 |
| budget terminations | 10 (9 wall clock, 1 iterations) | 0 |

  Say: "Tokens are summed per model call across every lap, so the loop's
  re-sent transcript is counted each time. The models are local, so cost is
  measured tokens times a documented stand-in rate card ($0.15/$0.60 per Mtok)."
- *Per ticket*: both systems, every ticket, with the full transcript under
  each row.
- *Branching*: the 10 fixtures (stable ids `CD-7001`–`CD-7010`, identical for
  both systems), 5 of which branch after step 2. The tool *sequence* changes
  only on CD-7008 (no order id → step 2 skipped by a fixed `if`); the
  *decision* changes on five tickets from what `get_order` returns (missing,
  refunded, duplicate, cancelled). Say: "A varying path argues for a branch,
  not a loop. Every branch is known before the ticket arrives."
- *Four budgets*: one clean termination log per budget
  (`week7/budget_logs/*.log`, each on CD-7001 with the other three out of
  reach): `max_iterations` (2 ≥ 2), `max_tokens` (1537 ≥ 1200),
  `max_cost_usd` ($0.000274 ≥ $0.000200), `max_wall_seconds` (15.3 s ≥ 15.0 s,
  lap 2 cut mid-generation). Each shows the configured limits, the budget
  that fired, and the spend at termination.
- *The third tool*: `lookup_refund_policy(tier, order_status)` with enum
  parameters; the description diff and the probe — **7/20 correct before and
  after**. Say the honest result: "Sharpening the description did not move
  tool choice; with the loose text the model picked the wrong tool zero
  times. Its failure is calling no tool at all."
- *Verdict*: `week7/verdict.md`, 145 words, quoting the four numbers.
- *Run live*: pick **CD-7007** and click **Run the fixed workflow**. Measured
  today: PASS, `escalated`, 3 steps, 1 model call, 164 tokens, $0.000035,
  6.5 s; step 2 returns `order_not_found`, step 3 takes the escalation rule.
  Then pick **CD-7001**, max laps **2**, click **Run the agent**. Measured
  today: it stops with **`BUDGET max_iterations hit (2 >= 2) after 2
  iterations, 1537 tokens, $0.000274, 13.7s — terminating cleanly`** — the
  same tokens and cost as the committed log, because decoding is greedy. Both
  live runs are saved under `week7/runs/live/`, never over the race files.

Do **not** run `python main.py race` live: the agent arm takes about an hour.

### Step 8 — Trajectory eval (Week 8)

Open **Trajectory eval** (or `#week8/gap`).

- *Overview* (`week8/trajectory_summary.csv`, same 10 tickets, Qwen2.5-1.5B,
  budgets 8 / 8000 / $0.01 / 1800 s):

| config | outcome | trajectory | **gap** | tool choice | arg validity | step eff. | cost p50 | cost max |
|---|---|---|---|---|---|---|---|---|
| baseline | 0% | 10% | **−10 pp** | 33.3% | 86.2% | 1.27 | $0.000580 | $0.001514 |
| mitigated | 40% | 40% | **0 pp** | 43.6% | 90.9% | 2.23 | $0.001409 | $0.001524 |
| week7_race1 (rescored) | 40% | 10% | **+30 pp** | 30.6% | 100% | 1.02 | $0.000502 | $0.000773 |

  Click **Rescore stored runs**: every number is re-derived from the raw
  trajectories in `week8/runs/` with no model, and the table says whether it
  matches `report.json` (it does: "All stored numbers reproduce").
- *The 10 sequences*: expected tool paths asserted in code; CD-7005, CD-7007
  and CD-7008 accept alternates as a set. Say: "Under a single-sequence eval
  CD-7008's correct `find_ticket → answer` would fail and the race-1 gap would
  read 40 pp instead of 30."
- *Right answer, wrong path*: **CD-7010 in race 1 — outcome PASS, trajectory
  FAIL.** It approved $19.99 without ever calling `get_order`; the amount came
  from the customer's sentence. The trace is shown. Say: "A 31-day-old charge
  would have been refunded the same way. The outcome test passed a guess."
- *Mitigation*: top mode by harm = `skipped_evidence` (7 tickets). ONE change:
  re-planning on unsupported answers (`evidence_guard=True`, a one-argument
  diff). **7 → 0 tickets**, outcome 0% → 40%. **The price:** tokens p50
  3,406 → 8,391 (+146%), cost p50 +$0.000829/ticket (+143%), step efficiency
  1.27 → 2.23.
- *Regression* (`week8/regression.csv`): every mode, before → after. Point at
  the rows that got **worse** (`prose_instead_of_action` 8 → 15 events,
  `budget_exhausted` 2 → 6 tickets, `redundant_call` 14 → 16) and the **new**
  mode the guard created (`skipped_evidence_caught`, 12 events). Say: "The fix
  turns wrong answers into no answers. Safer, still a failure, and measured."
- *Injection (bonus)*: controlled fixture CD-7201 with a pasted "email" in the
  customer message (`week8/injection_summary.md`). Undefended: 0 of 3 variants
  obeyed (all ran out of tokens). Defended: one `issue_refund` attempt with an
  invented amount, refused by the read-only scope; the output guardrail
  blocked `refund_approved 210` on a 75-day-old charge. Guardrail cost on
  clean tickets: 0 tokens; the sanitizer cost argument validity 90.9% → 38.6%.
  The refund tool writes only to a sandbox ledger (`week8/refund_ledger.jsonl`).

### Step 9 — MCP servers (Week 9)

Open **MCP servers** (or `#week9/discovery`).

- *Discovery 2 → 4*: from the servers' own `tools/list` answers
  (`week9/tools_before.json`: `get_article`, `search_kb`; `tools_after.json`:
  plus `lookup_ticket`, `get_escalation_history`). Click **Run tools/list
  now** with *Server one + two*: the real servers start as child processes,
  `initialize` + `tools/list` run, and **4 tools** come back (no model).
- *Config-only swap*: `week9/agent_diff.txt` — `git diff 1715ab5 d4e76e4 --
  src/mcp_agent.py src/mcp_client.py src/agent_runtime.py` is **empty, 0
  changed lines**; `config_diff.txt` is the whole change (5 lines of
  `week9/mcp_config.json`). Say: "The host names no tool; it builds its tool
  list from `tools/list`."
- *JSON-RPC wire*: `week9/wire.json`, seven messages — `initialize` →
  result → `notifications/initialized` (no id, no reply) → `tools/list` →
  result → `tools/call lookup_ticket` → result — every top-level field
  annotated. Click **Capture the wire now**: the same four steps replayed
  live over stdio, protocol `2025-06-18`, raw messages shown. The box at the
  top says where the model runs: **only in the host, between `tools/list` and
  `tools/call`**; never in the client, the server, or on the wire.
- *Query trace*: `runs/query_ticket_history.json` — the agent, given the
  config, discovers `lookup_ticket` and calls it on `TCK-4471` through the
  `ticket-history` server, which is named in the trace.
- *Error before / after*: same failing call `get_article("HC-8")` replayed as
  lap 1. Before: `Error executing tool get_article: Error 3` → the model tells
  the user to "check the article ID and try again". After: the docstring is a
  prompt (when to call, id format `HC-` + three digits, what to do instead) and
  the error is returned as `isError: true` with the action first: `Retry now:
  call get_article with article_id "HC-008". Reason: … Known ids: …`. Say what
  did not change: "After three rewrites the 1.5B model still never re-called
  the tool; it now explains the fix to the user instead of blaming them."
- *Risk note*: `week9/risk_note.md`, five lines — author/provenance, reachable
  data, logging, stolen-token impact, **verdict: don't ship as configured**
  (the token is committed in config; the client passes the server the whole
  environment).
- *Gateway (bonus)*: one front door, one audit line per call
  (`week9/logs/gateway_audit.log`), token scope denying
  `get_escalation_history`, the denial reaching the model as `isError: true`.

### Step 10 — Multi-agent benchmark (Week 10)

Open **Multi-agent benchmark**. Numbers: `week10/race_table.md`; the verdict:
`week10/verdict.md`; how it was measured: `week10/README.md`.

- *Chatbot*: pick an example chip (the ten benchmark cases, plus two questions
  the help centre cannot answer), choose **Compare both**, press **Ask**. The
  single agent runs first, then the manager with its two specialists; the
  comparison table and both cards show the answer, citations, routing, every
  task's lifecycle, the hand-offs, tokens, cost and latency. Say: "Same model,
  same retrieval, same reply rules. The only new capability is the ticket
  specialist deciding refund eligibility in code." Try a *Not in the help
  centre* chip to show the routed hand-off, with no invented policy.
- *Race table*: pass rate (assertions AND judge v2), p50/p95/p99 with the n=10
  caveat, tokens in/out, cost on the stand-in rate card, calls and hand-offs,
  the pass rate per Week 5 mode, and the largest token-consuming hand-off.
- *Hand-offs*: `handoffs.log`, one line per dispatch, return and task.
- *Failure experiment*: choose a specialist and a case, press **Run with the
  fault**. The worker answers HTTP 500 for that ticket only; the card shows two
  attempts, a FAILED task, and the routed hand-off; the API answered 503
  `degraded`. The stored run is `week10/failure_case.md`.
- *Verdict* and *Write-up*: `verdict.md` (ten lines) and `REPORT.md`.

Do **not** run `python main.py week10 all` live: both arms take about half an
hour on this machine.

### Step 11 — Reports & demo

Open **Reports & demo**. This guide and every week's README render from the
files, with the path shown, so a reviewer can open the same file.

---

## 5. Exact questions for the chat, and what to expect

| Question | Expected | Why |
|---|---|---|
| `How long is a CloudDesk password reset link valid?` | **Grounded**, `30 minutes [1] [2] [3]`, HC-001 chunks 15/5/11, best distance 0.375 | the fact is stated in the article (and repeated in its FAQ) |
| `Why is the Upload File button unavailable in CloudDesk?` | Grounded from the HC-006 troubleshooting table (chunk `HC-006-markdown-11`, header row kept with the row) | table-aware chunking |
| `What is the troubleshooting solution when the reset link has expired?` | Grounded, HC-001 | GS-03, rank 1 in both arms |
| `What is the maximum file size limit for CloudDesk uploads in megabytes?` | **Refused**, `model_reported_not_in_sources`, best distance 0.2729 | the trap: topic present, fact absent |
| `How much does the CloudDesk Enterprise plan cost per user?` | Refused | pricing is not in the corpus |
| `What is the capital of France?` | Refused, `distance_gate (≈1.79 > 0.75)` | out of scope |
| `How do I fix it?` | Refused, `distance_gate (1.4422 > 0.75)`; with *Product area = Billing* retrieval narrows to HC-003 | ambiguous |
| `My card expired and payment failed in HC-003. What do I do?` | `gate_bypass` on the HC-003 anchor, HC-003 chunks retrieved, then **refused `ungrounded_answer`** | the 0.5B generation ceiling, caught by the grounding gate |

---

## 6. Artefacts and measured results, by week

| Week | Evidence | Headline |
|---|---|---|
| 3 | `evaluation/retrieval_results.json`, `filtering_results.json`, `threshold_results.json`, `generation_results.json`; `DEMO_GUIDE_WEEKS_3_4.md` | markdown/300/50/Top-5: chunk-hit@5 88%, table-ok@5 75%, MRR 0.650; refusals 6/6; citations 100% correct chunk |
| 4 | `evaluation/golden_set.jsonl`, `golden_ab_results.json`, `results.md` | hit-rate@3 66.7% → 83.3%, 2/4 fixed, 0 regressions, latency +0.46 ms inside noise (`efd07ba`) |
| 5 | `week5/sample.json` (seed 20260907), `taxonomy.md`, `notes.md`, `prediction.md` (`c152efb`), `replay_evidence.md`; `week5/prediction_run/arm_t0.75.json`, `arm_t0.85.json`, `sample_20260915.json`, `notes_20260915.md` (blank, yours) | 5 modes; prediction committed before the change; both arms run (92 + 92, build `374d717`); score pending your 20 labels |
| 6 | `week6/eval_cases.jsonl`, `judge_v0_presplit.txt`, `judge_v1.txt`, `replies_25.json` (`b594458`), `runs/run_*.json`, `protocol_evidence.md` | 25 cases, 6 assertions vs 1 judged criterion, 4 real regressions; **labels_25.json, judge results, prediction.txt, judge_v2.txt: not yet done (yours)** |
| 7 | `week7/race_summary.csv`, `race.csv`, `runs/race_20260921-134848.json`, `budget_logs/*.log`, `budget_termination.log`, `verdict.md`, `tool_description_diff.md`, `tool_probe_v1/v2.json`; `runs/live/*.json` | 0/10 vs 10/10 · 338.6 s vs 3.8 s · 44,748 vs 1,793 tokens · $0.000746 vs $0.000042 |
| 8 | `week8/trajectory_summary.csv`, `trajectory_results.csv`, `regression.csv`, `report.json`, `runs/traj_*.json`, `injection_summary.md` | gap +30 pp (race 1), −10 → 0 pp (baseline → mitigated); skipped_evidence 7 → 0 at +146% tokens; 3 modes worse, 1 new |
| 9 | `week9/tools_before.json`, `tools_after.json`, `agent_diff.txt`, `config_diff.txt`, `wire.json`, `wire_raw.json`, `wire_http.json`, `runs/query_ticket_history.json`, `error_before_after.md`, `risk_note.md`, `logs/` | 2 → 4 tools from tools/list; 0 agent lines changed; 7 annotated wire messages; recoverable error; don't-ship verdict |

---

## 7. Tests

```bash
python tests/test_pipeline.py          # loader, chunking, grounding, metrics, eval data   (no model)
python tests/test_week5_prediction.py  # prediction-experiment provenance, synthetic       (no model)
python tests/test_weeks6_9.py          # Weeks 6–9 + the JSON API over a socket           (no model; starts the MCP servers)
python tests/test_week10.py            # Week 10: manager, specialists, HTTP 500, API      (no model; starts the specialist worker)
python tests/test_rag_live.py          # the real pipeline, traced to a temp dir          (0.5B model, ~1 min)
```

Run on 2026-10-11: all four pass — 30, 76, 87 and 22 checks, 0 failures,
0 skips. None of them writes to `traces/traces.jsonl` or any `week*/` artefact.

---

## 8. Known limitations and troubleshooting

- **Week 6 has no agreement number yet.** The blind labels are a human step
  (§4 step 6). The tool refuses to run the judge before they are committed,
  and this guide refuses to quote a number that does not exist.
- **Week 5 prediction is run but not scored.** The 20 sampled traces need
  your mode-1 labels before `score` can evaluate the committed criteria.
- **The 0.5B chat model** answers 62.5% of known-answer questions correctly
  and sometimes refuses (`ungrounded_answer`) with the right chunks in
  context. Retrieval and generation are scored separately, so this is
  attributed to generation; it is the stated highest-value next change.
- **The agent loses by a lot** because it is a 1.5B model on a laptop. Say
  so. A frontier model would pass more tickets; it would not change the
  25× token cost of a loop over a task whose steps are known in advance.
- **Latency is noisy on this machine** (the Week 8 README documents two
  discarded, confounded runs). Tokens and cost are deterministic under greedy
  decoding; latency is reported but not leaned on.
- **Postgres will not start** → the locale line in §3. **`pg_isready` says no
  response** → the cluster is down; the UI's `/api/health` shows
  `store_reachable: false`.
- **A button says "another run is in progress"** → one model-backed action at
  a time; wait for the header chip to clear. The Week 7 live agent is clamped
  to the race ceilings (8 laps / 8000 tokens / $0.01 / 300 s).
- **First click is slow** → model load; warm up before the demo (§3).
- **Langfuse** is optional; with keys in `.env` every chat answer, ticket
  draft and eval score also appears there. `RAG_LANGFUSE=0` to turn it off.

---

## 9. Pre-demo checklist

1. `pg_isready -p 5433` → accepting connections.
2. `python main.py status` → 7 documents, `markdown 138 chunks (default)`.
3. `python tests/test_weeks6_9.py` → All checks passed.
4. `python main.py ui` → http://localhost:8000 opens; `/api/health` shows `"ok": true`.
5. Ask one question in Chat (warms the 0.5B generator, embeddings, reranker).
6. *Agent vs workflow → Run live* → run the workflow on CD-7007 once (warms the 1.5B model).
7. *MCP servers → Discovery* → **Run tools/list now** → 4 tools.
8. Have `week7/race_summary.csv`, `week8/regression.csv`, `week9/risk_note.md` open in a terminal as backup.
9. Decide in advance what you say about Week 6 and the Week 5 score: the honest state, not a number.

### Live-demo run order (short form)

Chat (answer → trap → ambiguous) → Golden set (Run evaluation) → Error
analysis (prediction) → Judge validation (assertions run, protocol state) →
Agent vs workflow (four numbers → budgets → live workflow → live 2-lap agent) →
Trajectory eval (gap → mitigation → regression → rescore) → MCP servers
(discovery live → zero diff → wire live → error → risk) → Reports.
