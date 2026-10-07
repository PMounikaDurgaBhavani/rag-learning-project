# Week 9 — bolt on the ticket-history server without touching the agent

Support Ops shipped an MCP server for ticket lookup and escalation history. The claim to prove: the agent discovers its tools at runtime, so adding a second server is a config change, not a code release.

## Commands

```bash
python src/mcp_agent.py --config week9/mcp_config.json --list                     # discovery only, no model
python src/mcp_agent.py --config week9/mcp_config.json "Who owns ticket TCK-4471 and what is its status?"
python experiments/week9_wire.py                                                   # raw JSON-RPC -> wire_raw.json
python experiments/week9_error_transcript.py before|after                          # same failing call
python src/mcp_agent.py --config week9/mcp_config_gateway.json "Show the escalation history for ticket TCK-4471."
python main.py ui                                                                  # sidebar -> MCP (Week 9)
```

## The pieces

| piece | file | role |
|---|---|---|
| host (the agent) | `src/mcp_agent.py` | runs the model; builds its tool list from `tools/list`; names no tool |
| client | `src/mcp_client.py` | one connection per server; JSON-RPC 2.0 over stdio or streamable HTTP; records the raw wire |
| server one (ours) | `mcp_servers/kb_server.py` | the RAG corpus: `search_kb`, `get_article`, resource `kb://articles/{id}`, prompt `answer_from_kb` |
| server two (Support Ops) | `third_party/ticket_history_server/server.py` | `lookup_ticket`, `get_escalation_history`, resource `tickets://{id}/escalations`, prompt `shift_handover` |
| gateway (bonus) | `mcp_servers/gateway.py` | one front door; fans out; audits; enforces token scopes |

**Where the AI runs:** only in the host process (Qwen2.5-1.5B via `agent_runtime.chat`). Clients and servers move JSON and run plain functions. `search_kb` is BM25 retrieval; it returns passages, and the host's model writes the answer.

## 1. Config-only swap: zero agent lines changed

Commit `1715ab5` has server one only. Commit `d4e76e4` adds server two. `week9/agent_diff.txt`: `git diff 1715ab5 d4e76e4 -- src/mcp_agent.py src/mcp_client.py src/agent_runtime.py` is **empty, 0 changed lines**. What did change (`week9/config_diff.txt`) is 5 lines of `week9/mcp_config.json`, plus Support Ops' own files:

```diff
+    "ticket-history": {
+      "command": "venv/bin/python",
+      "args": ["third_party/ticket_history_server/server.py"],
+      "env": {"TICKET_HISTORY_TOKEN": "ops-handover-7f3a9c21"}
```

The gateway swap is config-only too: `week9/agent_diff_gateway.txt` is 0 lines from `1715ab5` to the gateway commit.

## 2. Tools discovered, from tools/list

**2 → 4.** Both lists come from the servers' `tools/list` answers, saved by `--list --out`:

- **Before** (`tools_before.json`): `get_article`, `search_kb`
- **After** (`tools_after.json`): `get_article`, `get_escalation_history`, `lookup_ticket`, `search_kb`

Resources and prompts are discovered the same way: `kb://articles/{article_id}` and `answer_from_kb` from server one, then `tickets://{ticket_id}/escalations` and `shift_handover` from server two.

**One query that provably calls the new server** (`runs/query_ticket_history.json`):

```
[host] ticket-history (stdio) tools/list -> ['lookup_ticket', 'get_escalation_history']
[host] lap 1: model in=564 out=27
[host]   tools/call lookup_ticket -> server ticket-history args={"ticket_id": "TCK-4471"}
[host] answer: 'The ticket TCK-4471 is owned by tier2-meera and its current status is escalated. ... escalated 3 times so far.'
```

## 3. The wire, annotated

`week9/wire.json` is the raw exchange with the new server over stdio, with every top-level field annotated:

1. `initialize` → result (capabilities, agreed `protocolVersion` 2025-06-18, `serverInfo`, `instructions`)
2. `notifications/initialized` (no id, so no reply)
3. `tools/list` → result (2 tools with `inputSchema`)
4. `tools/call lookup_ticket` → result (`content`, `isError: false`)

**Where the model call happens:** only in the host, between `tools/list` and `tools/call`, where the model chooses `lookup_ticket` and its arguments. Never on the wire, in the client, or in the server.

## 4. Docstring as prompt, recoverable error

On our own server, `get_article`'s docstring went from `Get article.` to a prompt that says when to call it, the id format (HC- plus three digits), and what to do instead. Its not-found error went from `Error 3` to `Retry now: call get_article with article_id "HC-008". Reason: …; ids are HC- plus three digits. Known ids: …`. It is now returned with `isError: true` instead of raised, so the SDK no longer prefixes it with "Error executing tool".

Same failing call, `get_article("HC-8")`, replayed in every run (`error_before_after.md`):

- **Before:** "Please check the article ID and try again. If the issue persists, please contact our support team."
- **After:** the model names the valid ids and the format.
- **What did not change:** after three rewrites, the model never re-called the tool. This 1.5B model treats any tool error as the end of the task. The message now gives a model that does retry everything it needs in its first sentence.

## 5. Risk note

`week9/risk_note.md`, five lines: who wrote it, what it can reach, what it logs, what a stolen token could do, ship or don't. **Verdict: don't ship as configured.** Two findings were about us, not Support Ops:

- our client passes every server the host's entire environment
- the server's token is committed to git in our own config

## Bonus — the gateway

`week9/mcp_config_gateway.json` points the agent at one process, `mcp_servers/gateway.py`, which connects to both servers. The agent discovers the same 4 tools through it.

- **Audit:** one JSON line per `tools/call` in `week9/logs/gateway_audit.log`, with caller, tool, server, ticket id and decision. `resources/read` is audited too, since it reads the same data.
- **Scope:** token `gw-handover-2b91` (caller `handover-bot`) denies `get_escalation_history`.
- **The denial reaches the model** as `isError: true`: "Permission denied: caller 'handover-bot' is not allowed to call get_escalation_history (token scope). This is not an outage, and retrying will not help. You can still use: get_article, lookup_ticket, search_kb…". The model did not retry the denied call, and it told the user it lacks permission. It also said "try again later", which the message says will not help, and it did not use the offered `lookup_ticket`.
- **Tool vs resource, observed:** with the escalation history attached as a resource (`--attach tickets://TCK-4471/escalations`), the model still spent a call on the denied `get_escalation_history` before answering from the attached context. A tool the model can see, it calls. The stricter fix is to filter denied tools out of `tools/list`, so the model never sees them; this gateway denies at call time so the denial could be shown reaching the model.

## Topic map

| topic | here |
|---|---|
| What MCP is | one protocol between the host and any number of tool/data servers; adding one is config (section 1) |
| Host, client, server | `mcp_agent.py` / `mcp_client.py` / `kb_server.py` + `ticket_history_server` + `gateway.py` |
| Where the AI runs | only in the host process (section 3's one line) |
| Tools, resources, prompts | tools model-invoked (`tools/call`); resources app-attached (`--attach`, `resources/read`); prompts user-picked (`--prompt`, `prompts/get`) |
| Transports | stdio for all runs; streamable HTTP tested against the ticket server (`--http 8931`) with the same client |
| JSON-RPC handshake | `wire.json`: `initialize` → result → `notifications/initialized` → `tools/list` → `tools/call` |
