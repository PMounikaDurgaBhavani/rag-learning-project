# Week 9 demo — topics, what we did, where to show it

Open the UI with `python main.py ui` and choose **MCP servers** in the sidebar, or go straight to a tab with `http://localhost:8000/#week9/<tab>`. Tab names: `overview`, `discovery`, `swap`, `wire`, `trace`, `error`, `risk`, `gateway`, `writeup`.

## Opening line

"Support Ops shipped an MCP server for ticket history. My agent learns its tools at runtime, so I added it with five lines of config and zero lines of agent code, and I can prove both."

## Topics, one line each

| # | Topic | What it is | What we did | Show |
|---|---|---|---|---|
| 1 | What MCP is | One standard protocol between an AI app and any tool or data server. | Added Support Ops' server with 5 config lines; the agent module changed by 0 lines. | *Config-only swap* |
| 2 | Host, client, server | The host runs the model; one client per server; servers expose capabilities. | `mcp_agent.py` (host), `mcp_client.py` (client), our KB server, Support Ops' ticket-history server. | *Overview* diagram |
| 3 | Where the AI runs | Only in the host; servers never call a model. | Qwen runs in the host between `tools/list` and `tools/call`; the servers run plain functions. | *JSON-RPC wire*, top box |
| 4 | Tools, resources, prompts | Tools: the model calls them. Resources: the app attaches them. Prompts: the user picks them. | Both servers expose all three; the handover query attaches `tickets://TCK-4471/escalations` as a resource. | *Discovery* table, *Gateway* tab |
| 5 | Transports (stdio, HTTP) | stdio: the server is a child process on pipes. HTTP: the server listens, and the client POSTs. | Every run over stdio; the same handshake captured over streamable HTTP, with a session id. | *JSON-RPC wire*, HTTP toggle |
| 6 | JSON-RPC handshake | `initialize` → `initialized` → `tools/list` → `tools/call`. | Captured raw and annotated every top-level field. | *JSON-RPC wire* |

## Rubric walk-through

1. **Config-only swap (30):** *Config-only swap* tab: the agent diff is empty (0 lines); the config diff is the whole change.
2. **Wire, annotated (25):** *JSON-RPC wire* tab: seven messages, each with every field explained, plus the one line on where the model runs.
3. **Docstring + recoverable error (20):** *Error before / after* tab: the same failing call `get_article("HC-8")`, before vs after. The model's handling improved; it still never retried, and we say so.
4. **Tool count from tools/list (15):** *Discovery* tab: **2 → 4**, with names. Press **Run tools/list now** to do it live.
5. **Risk note (10):** *Risk note* tab: five lines. Verdict: don't ship as configured.
6. **Bonus:** *Gateway* tab: one front door, one audit line per call, `get_escalation_history` denied by token scope, and the denial reaching the model as a readable message.

## Closing line

"Adding the server was config, not code. Trusting it is a separate decision: it can read every customer's tickets, and its token was sitting in git."
