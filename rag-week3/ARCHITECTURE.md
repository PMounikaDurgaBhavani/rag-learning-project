# Architecture — Tier-1 Deflection Assistant (Week 12 capstone)

One Python process serves the API and the React dashboard; one MCP server process serves help-centre
search; Postgres + pgvector holds the corpus. Every request carries one trace id from the HTTP route
through the agent, the MCP client, the MCP server and back into the trace record.

```
                         ┌──────────────────────────── browser ────────────────────────────┐
                         │  React + TypeScript dashboard  (frontend/dist, served at /app/)  │
                         │  Dashboard · Ticket inbox · AI chatbot · Trace viewer · Evaluation│
                         └───────────────┬───────────────────────────────────┬──────────────┘
                                         │ JSON /api/capstone/*              │ GET /app/help-centre/<slug>
┌────────────────────────────────────────▼───────────────────────────────────▼───────────────────────────┐
│ python main.py capstone up  →  src/server.py (stdlib ThreadingHTTPServer, one model-run lock, 409 when busy)│
│   src/capstone/api.py        routes: health, dashboard, tickets(+analyse,+action), chat, traces, trace, │
│                              articles, escalations, mcp, evaluation                                     │
│   src/capstone/ticket_store  inbox (seed + persisted state)   conversation_store  multi-turn threads     │
│   ─────────────────────────────────────────────────────────────────────────────────────────────────────  │
│   src/capstone/deflection_agent.handle_ticket(ticket)        ← one trace id (tracing.new_trace_id)       │
│     1 understand        intent · entities · injection flag · required evidence        (code, <5 ms)     │
│     2 mcp.discover      tools/list on the connected MCP server(s), every request      (mcp_session)     │
│     3 agent.loop        Week 7 runtime: Qwen2.5-1.5B picks one tool per lap under four budgets;          │
│                         evidence guard rejects "DONE" without the required tools, then forces them        │
│           ├─ tool:lookup_order        ─┐  src/capstone/business_tools.py                                 │
│           ├─ tool:check_entitlement   ─┤  DEMO ADAPTERS over data/capstone/seed/*.json                   │
│           ├─ tool:escalate_ticket     ─┘  persistent queue data/capstone/state/escalations.jsonl         │
│           └─ mcp:search_help_centre ───── JSON-RPC tools/call, params._meta.trace_id ──────────┐          │
│     4 validation.evidence   decision in code: order ⨯ entitlement ⨯ policy version ⨯ passages │          │
│                             missing / conflicting evidence, sensitive intent → escalate        │          │
│     5 generation            reply drafted from numbered passages + verified facts (Week 6 rules)│          │
│       validation.assertions 7 Week 6 + 8 capstone checks on the raw draft; hard failure →       │          │
│                             regenerate once, then escalate (Week 8 output guardrail)            │          │
│     6 decision.escalation   escalate_ticket → reference, queue, assignee                        │          │
│   tracing.append(record)  → traces/traces.jsonl (+ Week 11 spans, tokens, cost, policy verdict) │          │
│                           → traces/observability.db (SQLite, FTS)   PII masked before writing  │          │
└──────────────────────────────────────────────────────────────────────────────────────────────┼──────────┘
                                                                                               │
┌──────────────────────────────────────────────────────────────────────────────────────────────▼──────────┐
│ mcp_servers/helpcentre_server.py  (MCP 2025-06-18; stdio or streamable HTTP :8765/mcp; no model inside) │
│   search_help_centre(query, top_k, policy_version, product_area) → hybrid retrieval over Postgres:        │
│     dense (all-MiniLM-L6-v2, pgvector cosine) + BM25 → RRF → cross-encoder rerank → passages with slug,   │
│     article_id, title, section, last_updated, policy_version, status, url; drops the other policy version │
│   get_help_centre_article(slug_or_id) · resource helpcentre://articles/{slug}                              │
│   echoes _meta.trace_id; logs it (PII-free) to week12/logs/mcp_helpcentre_access.log                      │
└──────────────────────────────────────────────┬───────────────────────────────────────────────────────────┘
                                               │ SQL
                             ┌─────────────────▼─────────────────┐
                             │ PostgreSQL 5433 + pgvector         │  13 documents / 189 markdown chunks:
                             │ documents, chunks(embedding 384)   │  HC-001..006, HC-008 (current 30-day),
                             └────────────────────────────────────┘  HC-009 (RETIRED 14-day), HC-010 shipping
                                                                     SLA, HC-011 changelog, MACRO-001..003
```

## Reuse map (what each week contributes, unchanged unless noted)

| Layer | Week | Module | Change in Week 12 |
|---|---|---|---|
| Retrieval | 3–4 | `retriever.py`, `bm25_retriever.py`, `hybrid_retriever.py`, `reranker.py` | BM25 results now carry document metadata (slug, policy version) |
| Corpus | 3–6 | Postgres + `db.py`, `loader.py` | +6 seed documents in `samples/capstone/`; HC-008 frontmatter gained slug / policy version |
| Agent runtime | 7 | `agent_runtime.py` (chat, budgets, tool-call parsing) | shares one copy of the 1.5B weights with the drafter |
| Evidence guard / guardrail | 7–8 | `ticket_agent.unsupported_by_evidence`, `agent_guardrails` | same idea re-implemented over the new tools in `deflection_agent.py`; injection patterns reused |
| Drafting rules + assertions | 6, 11 | `ticket_reply.py` prompt rules, `ticket_assertions.py`, `ticket_policy.py` | `refund_eligible` honours `refund_window_days`; 8 capstone assertions added |
| Judge | 6 | `judge.py`, `week6/judge_v2.txt` | run in a separate process by `week12_eval.py judge` |
| MCP | 9 | `mcp_client.py`, `mcp_agent.Host` | `call_tool(..., meta=)` sends `params._meta`; new help-centre server |
| Observability | 11 | `observability.py`, `tracing.py` | new span names; `kind="deflection"` records with a `capstone` block |
| Evaluation method | 5, 6, 8 | sampling rule, pass-by-mode, trajectory scorer | adapted in `experiments/week12_eval.py` |

## Decision rules the code enforces (not the model)

1. No refund or shipping statement without a successful `lookup_order` **and** `check_entitlement`.
2. The refund window and the policy article come from the entitlement's `policy_version`; the other version is dropped from the search and refused as a citation. No passage of the right version → escalate.
3. Missing order, order of another customer, conflicting evidence (claimed duplicate vs single charge, amount mismatch) → escalate.
4. Sensitive intents (legal, PII disclosure, data deletion), out-of-scope → escalate without a model call.
5. The customer message is data: decisions never read it, the planner prompt labels it untrusted, and `injection_not_obeyed` fails any draft that complies.
6. A draft that contradicts the verified decision, promises an unverified refund, claims a refund "has been processed", cites the wrong policy version, follows an injected instruction or contains an email/address is regenerated once, then escalated (the six **hard** assertions). Two assertions are **soft** and only reported: a citation marker with no retrieved passage, and a free-expedited offer without entitlement.
7. A document with `status: superseded` is shown to the model only when the verified entitlement names its policy version; with no verified entitlement (knowledge questions) it is dropped before the prompt.
8. Everything written for the squad is masked at write time by `capstone/pii.py`: the ticket text, the prompt messages, the raw and final drafts, tool arguments, escalation reasons and notes (emails, street addresses, phone and card numbers). The customer record's email is read only by the inbox detail route. The MCP access log never carries the query text.

## Trust boundaries

- Browser → API: JSON only; parameters validated; model routes serialised by one lock.
- API → business adapters: in-process, labelled DEMO; a production deployment swaps the three functions for HTTP clients with the same return shapes.
- Agent → MCP: JSON-RPC over stdio or HTTP; tool results are data; `_meta` carries only the trace id.
- Anything written to disk for the squad (traces, logs, state) is PII-masked at write time; the inbox detail route is the single authorised view of a customer's email.
- Identity: the inbox acts for the ticket's customer; the chatbot takes a self-asserted customer id (demo). The agent never lets the model choose the customer: the authenticated id is injected into every order/entitlement call, and with no authenticated customer the entitlement cannot be verified and the request escalates. A production deployment adds authentication in front of the API.
- `policy_filter=false` (the capstone-v1.0 behaviour kept for the demo) is accepted only when `CAPSTONE_MODE=demo`.
