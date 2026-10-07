"""Week 9 data for the UI's MCP view. Reads week9/; the one live action,
discover(), runs initialize + tools/list against real servers and calls no model.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
W9 = ROOT / "week9"
sys.path.insert(0, str(ROOT / "src"))

COMMITS = {"server_one": "1715ab5", "server_two": "d4e76e4"}

TOPICS = [
    {"topic": "What MCP is", "what": "One protocol between the host and any tool/data server.",
     "did": "Added Support Ops' server with 5 config lines and 0 agent lines.", "tab": "swap"},
    {"topic": "Host, client, server", "what": "Host runs the model; one client per server; servers expose capabilities.",
     "did": "mcp_agent.py (host), mcp_client.py (client), kb_server.py + ticket-history + gateway (servers).",
     "tab": "overview"},
    {"topic": "Where the AI runs", "what": "Only in the host; servers never call a model.",
     "did": "Qwen runs in the host between tools/list and tools/call; servers run plain functions.",
     "tab": "wire"},
    {"topic": "Tools, resources, prompts", "what": "Model-invoked, app-attached, user-picked.",
     "did": "Both servers expose all three; the handover query attaches tickets://…/escalations.",
     "tab": "gateway"},
    {"topic": "Transports (stdio, HTTP)", "what": "Child process pipes, or POSTs to one URL.",
     "did": "All runs over stdio; the same handshake captured over streamable HTTP (wire_http.json).",
     "tab": "wire"},
    {"topic": "JSON-RPC handshake", "what": "initialize → initialized → tools/list → tools/call.",
     "did": "Captured raw and annotated every top-level field (wire.json).", "tab": "wire"},
]


def _text(path):
    return path.read_text(encoding="utf-8") if path.exists() else None


def _json(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _lines(path):
    return [json.loads(line) for line in (_text(path) or "").splitlines() if line.strip()]


def _git_log():
    try:
        out = subprocess.run(["git", "log", "--oneline", "-12", "--", "week9", "src/mcp_agent.py",
                              "src/mcp_client.py", "mcp_servers", "third_party"],
                             cwd=ROOT, capture_output=True, text=True, timeout=10).stdout
        return [line.split(" ", 1) for line in out.splitlines() if line]
    except Exception:
        return []


def ui_artefacts():
    runs = W9 / "runs"
    return {
        "topics": TOPICS,
        "commits": COMMITS,
        "git_log": _git_log(),
        "tools_before": _json(W9 / "tools_before.json"),
        "tools_after": _json(W9 / "tools_after.json"),
        "tools_gateway": _json(W9 / "tools_gateway.json"),
        "agent_diff": _text(W9 / "agent_diff.txt"),
        "config_diff": _text(W9 / "config_diff.txt"),
        "agent_diff_gateway": _text(W9 / "agent_diff_gateway.txt"),
        "config_diff_gateway": _text(W9 / "config_diff_gateway.txt"),
        "wire": _json(W9 / "wire.json"),
        "wire_http": _json(W9 / "wire_http.json"),
        "query": _json(runs / "query_ticket_history.json"),
        "error_md": _text(W9 / "error_before_after.md"),
        "error_runs": {k: _json(runs / f"error_{k}.json")
                       for k in ("before", "after_v1", "after_v2", "after_v3")},
        "risk_note": _text(W9 / "risk_note.md"),
        "audit": _lines(W9 / "logs" / "gateway_audit.log"),
        "server_access_log": _lines(W9 / "logs" / "ticket_history_access.log")[-12:],
        "gateway_denial": _json(runs / "gateway_denial.json"),
        "gateway_resource": _json(runs / "gateway_resource_attach.json"),
        "readme": _text(W9 / "README.md"),
    }


def discover(which):
    """Live: initialize + tools/list against real servers. No model is loaded."""
    from mcp_agent import Host

    config = json.loads((W9 / "mcp_config.json").read_text(encoding="utf-8"))
    if which == "gateway":
        path, temp = W9 / "mcp_config_gateway.json", None
    else:
        servers = config["mcpServers"]
        if which == "server_one":
            servers = {"clouddesk-kb": servers["clouddesk-kb"]}
        # The host resolves relative paths against the config folder's parent,
        # so the temporary config lives in week9/ like the real ones.
        handle, temp = tempfile.mkstemp(dir=W9, prefix=".discover_", suffix=".json")
        with os.fdopen(handle, "w") as stream:
            json.dump({"mcpServers": servers}, stream)
        path = Path(temp)
    wire = []
    host = Host(str(path), wire=wire)
    try:
        inventory = host.connect()
    finally:
        host.close()
        if temp:
            os.unlink(temp)
    return {"which": which, "servers": inventory, "tool_count": len(host.tools),
            "tools": sorted(host.tools), "wire": wire}
