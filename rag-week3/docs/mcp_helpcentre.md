# Connecting to the CloudDesk help-centre MCP server

The help-centre search is an MCP server (`mcp_servers/helpcentre_server.py`, server name
`clouddesk-helpcentre`, version 1.0.0, protocol `2025-06-18`). Any MCP client can discover and
call it; the deflection agent is just one of them.

## What it exposes

| Primitive | Name | What it does |
|---|---|---|
| tool | `search_help_centre(query, top_k=5, policy_version=None, product_area=None)` | hybrid retrieval (dense + BM25 → RRF → cross-encoder) over the Postgres corpus; returns passages with `slug`, `article_id`, `title`, `section`, `last_updated`, `policy_version`, `status` (current / superseded), `url`, scores and the `trace_id` you sent |
| tool | `get_help_centre_article(slug_or_id)` | one article's markdown and metadata by slug (`refund-returns-current-30-day`) or id (`HC-008`) |
| resource | `helpcentre://articles/{slug}` | the article as `text/markdown`, for clients that attach context instead of calling tools |

`policy_version` is `current-30-day` or `legacy-14-day`. Pass it when the caller has verified the
account's terms: the server then drops passages of the other refund-policy version and lists them
under `dropped`, so the caller can show what was excluded and why. Any other value is a data error
(`bad_policy_version`), not an exception.

No model runs in the server. It returns passages; the caller's model writes.

## Start it

```bash
source venv/bin/activate
python main.py capstone mcp              # streamable HTTP on http://127.0.0.1:8765/mcp
# or, for a stdio client:
venv/bin/python mcp_servers/helpcentre_server.py
```

`python main.py capstone up` starts the HTTP server for you (port `CAPSTONE_MCP_PORT`, default 8765).
The first search in a fresh process loads the embedder and the cross-encoder (~20 s); later searches
take 0.2–1 s. Postgres must be reachable (`DATABASE_URL`, see README).

## Call it from your own client

### Claude-Desktop-style config (stdio)

```json
{ "mcpServers": { "clouddesk-helpcentre": {
    "command": "/path/to/rag-week3/venv/bin/python",
    "args": ["/path/to/rag-week3/mcp_servers/helpcentre_server.py"] } } }
```

### Streamable HTTP, raw JSON-RPC (any language)

```bash
# 1. initialize (note the Mcp-Session-Id header in the response; send it back on every call)
curl -s -D - http://127.0.0.1:8765/mcp \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -H 'MCP-Protocol-Version: 2025-06-18' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"your-squad","version":"0.1"}}}'

# 2. notifications/initialized (no id), then 3. tools/list, then 4. tools/call
curl -s http://127.0.0.1:8765/mcp -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -H 'MCP-Protocol-Version: 2025-06-18' -H 'Mcp-Session-Id: <id from step 1>' \
  -d '{"jsonrpc":"2.0","method":"notifications/initialized"}'
curl -s http://127.0.0.1:8765/mcp -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -H 'MCP-Protocol-Version: 2025-06-18' -H 'Mcp-Session-Id: <id>' \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'
curl -s http://127.0.0.1:8765/mcp -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -H 'MCP-Protocol-Version: 2025-06-18' -H 'Mcp-Session-Id: <id>' \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"search_help_centre","arguments":{"query":"can I get a refund after 20 days?","top_k":3,"policy_version":"current-30-day"},"_meta":{"trace_id":"your-trace-id"}}}'
```

### The repository's own client (Python, no SDK)

```python
import sys; sys.path.insert(0, "src")
from mcp_agent import Host
host = Host("week12/mcp_config_http.json")        # or week12/mcp_config.json for stdio
host.connect()                                     # initialize + tools/list on every server in the config
print(sorted(host.tools))                          # ['get_help_centre_article', 'search_help_centre']
result, server = host.call("search_help_centre", {"query": "refund after 20 days", "top_k": 3},
                           meta={"trace_id": "your-trace-id"})
payload = result["structuredContent"]
print(payload["trace_id"], [p["slug"] for p in payload["results"]])
host.close()
```

## Trace propagation

Put your request's id in `params._meta.trace_id` on `tools/call`. The server reads it from the MCP
request context, echoes it in the result (`trace_id`) and writes it to its access log
`week12/logs/mcp_helpcentre_access.log` (one JSON line per call: tool, argument shape, trace id,
latency, result count, pid). The log never contains the query text or any customer data, so it is
safe to share across squads. The trace viewer at `/app/traces/<id>` shows the server-side lines next
to the client-side span, which is how the same id is proven on both sides of the boundary.

## Failure behaviour

- Server not running (HTTP): the client's `initialize` fails with a connection error; the deflection
  agent records `mcp.discover` as an error span and escalates the ticket ("help-centre search
  unavailable"). It never answers without a citation.
- Empty query → `{"error": "empty_query"}`; bad `policy_version` → `{"error": "bad_policy_version"}`.
- Unknown article → `{"found": false, "error": "article_not_found", "known": [...]}`.
- Postgres down → the tool raises inside the server and the client receives a JSON-RPC error; the
  agent treats it as `mcp_call_failed` and escalates.

## Limits

One uvicorn process, stateless HTTP sessions, `top_k ≤ 8`, passage text capped at 900 characters.
Scale horizontally (several replicas behind one URL); the server holds no per-session state that
matters.
