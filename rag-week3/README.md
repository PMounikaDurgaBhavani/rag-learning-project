# CloudDesk Tier-1 Deflection Assistant — Week 12 capstone

A customer-support deflection assistant built on the Weeks 3–11 stack of this repository: it reads
a ticket, discovers its tools at runtime (MCP), looks up the order and the account's entitlement,
searches the help centre with the account's **refund policy version**, answers with clickable
citations only when the evidence allows it, otherwise escalates to a human — and records one trace
per request with spans, tokens and cost.

Everything runs locally (Qwen2.5-1.5B-Instruct, all-MiniLM-L6-v2, a cross-encoder, Postgres +
pgvector). No API key is needed. **Order, entitlement and escalation are clearly labelled local
demo adapters** (`src/capstone/config.py::INTEGRATION_LABELS`); there is no external ticketing or
billing system behind them.

- Architecture: [ARCHITECTURE.md](ARCHITECTURE.md) · 10-minute demo: [DEMO_GUIDE.md](DEMO_GUIDE.md) (§ Week 12)
- Reports: [reports/integrated_eval_report.md](reports/integrated_eval_report.md) · [reports/error_taxonomy.md](reports/error_taxonomy.md) · [reports/trajectory_eval.md](reports/trajectory_eval.md) · [reports/cost_and_scaling.md](reports/cost_and_scaling.md) · [reports/fresh_eyes_test.md](reports/fresh_eyes_test.md)
- Docs: [docs/tuesday_seam_list.md](docs/tuesday_seam_list.md) · [docs/wednesday_weaknesses.md](docs/wednesday_weaknesses.md) · [docs/mcp_helpcentre.md](docs/mcp_helpcentre.md) (connect your own MCP client)
- Weeks 3–11 (the legacy studio UI, every earlier README): [README_WEEKS_3_11.md](README_WEEKS_3_11.md)

## Prerequisites

| Need | Version used | Notes |
|---|---|---|
| macOS/Linux, 8 GB RAM minimum | 8 GB Apple M1 | 16 GB makes the model runs several times faster |
| Python | 3.11 | `python3.11 -m venv venv` |
| PostgreSQL with the `pgvector` extension | PG 16, `brew install postgresql@16 pgvector` | see [SETUP_DB.md](SETUP_DB.md) for the from-scratch walkthrough |
| Node.js + npm | 24 / 11 | only to build the dashboard once (`frontend/dist`) |
| ~4 GB disk for model weights | | downloaded from Hugging Face on first use |

## One command

```bash
# 0. once: clone, create the environment, install
git clone <this repo> && cd rag-week3
python3.11 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env: replace USER in DATABASE_URL with your login (`whoami`). The app's built-in default is
# port 5432; the steps below run a dedicated server on 5433, which is what .env.example points at.

# 1. once: a Postgres server for this app (macOS/Homebrew shown; see SETUP_DB.md for Linux / an existing server)
brew install postgresql@16 pgvector && export PATH="$(brew --prefix postgresql@16)/bin:$PATH"
LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8 initdb -D ~/pgdata/clouddesk        # LC_ALL avoids a macOS locale error in initdb/pg_ctl
LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8 pg_ctl -D ~/pgdata/clouddesk -o "-p 5433" -l ~/pgdata/clouddesk/server.log start
python main.py db setup           # creates the database, the pgvector extension and the tables

# 2. once: the corpus (13 help-centre documents, 3 chunking indexes; downloads the embedding model, ~90 MB)
python main.py import samples/helpcentre --no-reindex          # HC-001..HC-006 (Weeks 3-5 corpus, committed copy)
python main.py import samples/article_08_refunds_escalation.md --no-reindex   # HC-008, current 30-day policy
python main.py import samples/capstone --no-reindex             # HC-009 retired policy, HC-010, HC-011, 3 macros
python main.py ingest                                           # embed + index (3 strategies)

# 3. every time: the whole application
python main.py capstone up      # → http://localhost:8000/app/
```

**First run downloads models from Hugging Face** (internet needed once): Qwen2.5-1.5B-Instruct
(~3 GB), all-MiniLM-L6-v2 and the cross-encoder (~90 MB each), and SmolLM2-1.7B only for the judge.
Until they are cached the first AI request can look stuck for several minutes; `--warm` loads them at
startup so the wait is visible in the terminal instead of the browser.

`capstone up` checks Postgres and the corpus (imports the Week 12 seed articles if missing), writes
the demo data if missing (`python main.py capstone seed` rebuilds it), builds the React dashboard if
`frontend/dist` is missing (needs npm), starts the help-centre **MCP server over HTTP on port 8765**
as a child process, and serves the API + dashboard on port 8000. `Ctrl+C` stops both. Flags:
`--port`, `--no-browser`, `--no-mcp` (stdio fallback), `--no-ui`, `--warm` (load the models at start,
~2 min), `--reseed`.

The legacy Weeks 3–11 studio stays at `http://localhost:8000/`.

### What you can do in the dashboard

| Screen | URL | What it does |
|---|---|---|
| Dashboard | `/app/dashboard` | tickets, handled by AI, deflected, escalated, deflection rate, simulated reopen rate (labelled), AI response time, recent runs — all from the inbox state, the escalation queue and the trace index |
| Ticket inbox | `/app/inbox` | 50 seeded tickets (26 Week 6 cases + 10 Week 7 tickets + 14 capstone cases). Open one, **Run AI analysis** (agent loop or fast mode), read the tool calls, edit/approve the draft, escalate, resolve. Customer email is visible here only. |
| AI chatbot | `/app/chat` | multi-turn conversation against the real agent; shows the processing state, the answer, cited articles (clickable), the policy version used, order/entitlement verification, answered vs escalated, and the trace link |
| Agent traces | `/app/traces` | one trace per request: spans with duration, tool/server, tokens, cost, errors; the MCP server's own log lines for the same trace id |
| Evaluation & monitoring | `/app/evaluation` | assertion pass rate, judge agreement, pass rate by mode, superseded-policy citation rate before/after, trajectory accuracy and gap, cost per deflected ticket, latency, known weaknesses — read from `week12/*.json`; missing files are shown as missing |
| Help centre | `/app/help-centre/<slug>` | the article behind every citation, with its status (current / **retired**) and policy version |

**Timing on an 8 GB laptop.** The first model-backed request loads the 1.5B model (~2 min). Fast
mode (code-planned tools, model writes the reply) takes 30–90 s per request; the agent loop (model
picks the tools) takes 2–5 min. The server runs one model-backed request at a time and answers
`409` to a second one.

## The evaluation command(s)

```bash
python tests/test_week12.py                                   # ~108 checks, no model (~1 min; starts the MCP server over stdio)
python main.py capstone sample --seed 20261104 --n 10         # the uncurated random sample (Week 5 rule)
python main.py capstone eval run --label v1.1 --planner model # 40 cases through the integrated system (~1–2 h here)
python main.py capstone eval run --label v1.0-before --no-policy-filter --subset refund --planner code
python main.py capstone eval judge --run week12/runs/v1.1_<stamp>.json      # judge v2, separate process; resumable (saves after every verdict; --limit N)
python main.py capstone eval report --run week12/runs/v1.1_<stamp>.json --before week12/runs/v1.0-before_<stamp>.json
python main.py capstone trajectory --run week12/runs/v1.1_<stamp>.json
python main.py capstone taxonomy --run week12/runs/v1.1_<stamp>.json --run week12/runs/v1.0-before_<stamp>.json
python main.py capstone reopen --run week12/runs/v1.1_<stamp>.json         # SIMULATED second contact
python main.py capstone cost --run week12/runs/v1.1_<stamp>.json
python main.py capstone report                                             # reports/*.md from week12/*.json
```

`python main.py capstone eval --help` only shows the pass-through; the flags above are the complete
list (`experiments/week12_eval.py`). Run the model-backed commands one at a time with the UI server
stopped: two model processes do not fit in 8 GB. The `week12/*.json` results and `reports/*.md` are
committed outputs of these commands; the Evaluation screen reads them and says "not run yet" when one
is missing. The earlier weeks' suites still pass: `python tests/test_pipeline.py`,
`tests/test_week5_prediction.py`, `tests/test_week10.py`, `tests/test_week11.py`, `tests/test_weeks6_9.py`.

## Layout (Week 12 additions)

```
src/capstone/            the integration layer (no retrieval, drafting or tracing re-implemented here)
  deflection_agent.py    understand → MCP discover → Week 7 loop → evidence decision → cited reply / escalation → trace
  understanding.py       rule-based intent, entities, injection flag, required evidence
  business_tools.py      lookup_order · check_entitlement (policy version!) · escalate_ticket  — DEMO ADAPTERS
  capstone_assertions.py 8 checks on top of the Week 6 seven (policy version, PII, injection, "processed" claims…)
  mcp_session.py         one connected MCP host per process; tools/list on every request
  ticket_store.py / conversation_store.py / escalation_queue.py   persisted state under data/capstone/state/
  pii.py                 masking applied to everything that leaves the request
  api.py                 /api/capstone/* routes (mounted by src/server.py)
  seed.py                builds data/capstone/seed/*.json from the Week 6 cases and the Week 7 store
mcp_servers/helpcentre_server.py   the help-centre search MCP server (stdio + streamable HTTP)
frontend/                React + TypeScript dashboard (Vite); built into frontend/dist and served at /app/
experiments/week12_eval.py         run · judge · report · trajectory · taxonomy · reopen · cost
experiments/week12_reports.py      reports/*.md from week12/*.json
week12/                  eval_cases.jsonl (40), sample.json, runs/, *.json results, logs/
tests/test_week12.py     the Week 12 regression suite
```

## Environment and configuration

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | code default `postgresql://$USER@localhost:5432/clouddesk_rag`; `.env.example` points at the dedicated 5433 server | the corpus |
| `CAPSTONE_MODE` | `demo` | shown as a badge; `production` only once the three adapters point at real services |
| `CAPSTONE_AS_OF` | `2026-11-04` | the date the demo data is frozen to (days-since-charge is computed from it) |
| `CAPSTONE_MCP_PORT` / `CAPSTONE_MCP_URL` | `8765` / `http://127.0.0.1:8765/mcp` | the HTTP MCP server |
| `CAPSTONE_MCP_CONFIG` | auto (HTTP if up, else `week12/mcp_config.json` stdio) | force a config |
| `RAG_AGENT_LLM`, `RAG_TICKET_LLM` | `Qwen/Qwen2.5-1.5B-Instruct` | the planner/drafter (one shared copy) |
| `RAG_JUDGE_MODEL` | `HuggingFaceTB/SmolLM2-1.7B-Instruct` | the judge |
| `RAG_TRACE_DIR`, `RAG_OBS_DB` | `traces/` | trace log and index |

Never commit `.env`. The repository contains no API keys; the Week 9 third-party token in
`week9/mcp_config.json` is a fixture for a local demo server and was already flagged in
`week9/risk_note.md`.

## Honesty notes

- The brief's 120 articles / 40 macros / 300 resolved tickets are **not** in this repository. The
  corpus is 13 documents (7 from earlier weeks + 6 labelled Week 12 seeds). The random sample is
  drawn from the 50 seeded inbox tickets with the Week 5 rule.
- Cost is an estimate: tokens × a stand-in rate card, plus a stated machine rate × measured wall time.
  Local models have no API bill; they are not free.
- Judge v2 agrees with blind hand labels 76% of the time on the 25 Week 6 replies, below the 85%
  target. Hand labels for the integrated replies are pending (a blind sheet is generated).
- Reopen rate is a **simulation** (the model role-plays the customer); it is labelled as such everywhere it appears.
- Identity in the chatbot is self-asserted (you type a customer id). There is no login; the inbox is the
  authorised console. A real deployment puts authentication in front of `/api/capstone/*`.
- Two assertions are soft (reported, never block): `citations_were_retrieved` and
  `no_free_expedited_without_entitlement`. The six hard ones regenerate once, then escalate.
