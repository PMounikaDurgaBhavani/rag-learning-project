# Fresh-eyes test — independent review of the Week 12 capstone

**Reviewer:** an independent review agent (Claude, Sonnet) with no prior context on this repository,
instructed to read only the docs and code, run no servers, tests or models (an evaluation was running
on the same 8 GB machine), and report as a repository reviewer would. This is an automated independent
review, not a human colleague; it is recorded here because the human review has not happened yet.

**Started:** Wednesday 2026-11-04 13:57:09 IST · **finished:** 13:59:48 IST · **scope:** README,
ARCHITECTURE, `docs/*`, `src/capstone/*`, `mcp_servers/helpcentre_server.py`, `tests/test_week12.py`,
`frontend/src/screens/*`, `experiments/week12_eval.py`; verified referenced paths with `ls`.

The reviewer's findings are quoted or paraphrased below with what was done about each
(**fixed** / **accepted** / **disputed**) and the timestamp of the fix. Items marked *pending* were
still open when this file was written.

## 1. Could a stranger start the app from README alone?

| Finding | Status |
|---|---|
| `cp .env.example .env` leaves a literal `USER` in `DATABASE_URL`; README said the default was `$USER` | **fixed** 14:04 — README now says to replace `USER` with `whoami` and explains 5432 (code default) vs 5433 (the dedicated server) |
| The one-command block started Postgres with `pg_ctl` but never ran `initdb`/`createdb`; `LC_ALL` unexplained; `pg_ctl` may not be on PATH | **fixed** 14:04 — `brew install`, PATH, `initdb` with the locale note, then `pg_ctl`, then `db setup` |
| No mention that the first run downloads ~3 GB of model weights from Hugging Face; "2 min" is a warm-cache figure | **fixed** 14:04 — paragraph added, with `--warm` |
| Setup imported from `export/` (git-ignored) while `capstone up` imported `samples/helpcentre/` | **fixed** 14:05 (seam #19, found minutes before the review) — both paths now use the committed `samples/helpcentre/` |
| `capstone eval --help` does not list the pass-through flags | **accepted** — README states the complete flag list and that `--help` only shows the pass-through |
| README linked five `reports/*.md` files that did not exist; `week12/*.json` results absent; the only before-run directory was empty (in progress) | **resolved 18:02** — all `week12/*.json` results and the four generated reports exist now; the judge result file records that the judge did not complete on this machine |
| Everything is uncommitted | **accepted** — committing is the author's decision; `git status` in the final hand-over lists every file |
| `week12/mcp_config.json` uses the relative `venv/bin/python`, so stdio only works from the repo root | **accepted** — documented in `docs/mcp_helpcentre.md` (absolute paths in the external-client example) |

## 2. Code vs docs

| Finding | Status |
|---|---|
| "Traces never carry email/addresses" was false for text a customer *types*: `prompt_messages`, `raw_output`, `answer` were written unmasked, and `mask_text` had no street-address pattern | **fixed** 14:03 — masked at record time; street pattern; test with a typed email + address (seam #20) |
| Rule 2 (other policy version dropped) only held after `check_entitlement`; knowledge intents had no version, so HC-009 (`status: superseded`) could be cited | **fixed** 14:03 — superseded documents are dropped unless the verified entitlement names their version; test added (seam #21) |
| ARCHITECTURE listed only the hard assertions; two are soft (`citations_were_retrieved`, `no_free_expedited_without_entitlement`) | **fixed** 14:04 — rules 6 and README now say which are hard and which are soft |
| "100+ checks" | **fixed** — "~105" (the file has 105 `check(` calls after the additions) |
| Model planner could pass any customer id when the chat had none | **fixed** 14:03 — dropped; no authenticated customer → escalate (seam #22) |
| `mcp==2.3.0` / `mcp.server.mcpserver.MCPServer` unverified by the reviewer | **verified by the author** — the import is the one the Week 9 servers already use; `tests/test_week12.py` discovers the server over stdio and the HTTP transport was exercised with curl (see `docs/mcp_helpcentre.md`) |
| `docs/mcp_helpcentre.md` says "stateless HTTP sessions" yet asks for `Mcp-Session-Id` | **accepted / clarified** — the server issues a session id (observed in the curl capture) and holds no state that matters; the doc keeps the header instruction |
| DEMO_GUIDE and weaknesses cite `closed_loop.json`, `labels_template.json`, `reports/*` that did not exist yet | **resolved 18:02** — all produced (`closed_loop.json` 83.3% → 0.0%; the labelling sheet has 40 verdict-free entries) |

## 3. Safety rules

- **Refund without entitlement:** reviewer found no code path; noted the regex promise detector can be
  paraphrased around ("send the money back to your card") and that identity is self-asserted in chat.
  **Accepted** — recorded in README "Honesty notes" and ARCHITECTURE trust boundaries; the detector is
  the Week 6 one and its limits are a known weakness.
- **`policy_filter:false` reachable from any client:** **fixed** 14:03 — accepted only when
  `CAPSTONE_MODE=demo` (test added).
- **Retired policy to a current account via knowledge intents:** **fixed** (above).
- **PII to traces/logs via typed text:** **fixed** (above). The reviewer confirmed `lookup_order` strips
  street addresses, the email is only on the inbox detail route, and the MCP access log redacts the query.
- **Prompt injection:** reviewer found no path where the decision obeys it; noted the detector is regex
  (paraphrases are not flagged) and that `injection_not_obeyed` only checks refund promises/"processed"
  claims. **Accepted** — the other hard assertions still apply; listed under non-coverage in the
  evaluation report.

## 4. The reviewer's top three, and what happened

1. PII-in-traces → fixed within the hour (14:03), with a test.
2. Retired policy for non-account intents → fixed within the hour (14:03), with a test.
3. Missing deliverables and README setup gaps → README fixed at 14:04; the result files and reports
   were produced at 18:02 (`reports/integrated_eval_report.md` carries the run timestamps).

## 5. Over-claims the reviewer flagged

- Links to reports before they existed: addressed by producing them (pending at review time).
- "Tuesday" deliverable: the file states that no Tuesday session happened and carries real
  Wednesday timestamps; the title is kept because the brief names the file.
- Timing figures in `docs/wednesday_weaknesses.md` were quoted from smoke runs; the measured figures
  with sample sizes are in `reports/integrated_eval_report.md` and `reports/cost_and_scaling.md`.

## What this review did not do

It did not start the application, click through the dashboard, or run the test suite (constraints
above). The author ran a scripted end-to-end walkthrough through the real server at 18:03–18:13 IST
(HTTP MCP discovery, inbox analysis of CD-12002, two chat turns, trace with the server-side log line,
approval); it found two more defects, fixed as seams #26 and #27. A human click-through of the five
screens remains pending.
