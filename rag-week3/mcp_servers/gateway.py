"""The MCP gateway: one front door for the agent, fanning out to N servers.

To the host this is one MCP server on stdio. Behind it, it is an MCP client
to every server in GATEWAY_UPSTREAMS (an mcpServers config). It:

    - answers tools/list with the union of every upstream's tools
    - routes each tools/call to the upstream that listed the tool
    - checks the caller's token scope first; a denied call comes back as a
      tool result with isError true and a message the model can act on,
      not a protocol error and not a dropped connection
    - writes exactly one audit line per tools/call: caller, tool, server,
      ticket id, decision

The server side is hand-written JSON-RPC rather than the SDK, so the gateway
can answer tools/list with tools it only learns about at start-up.

    env MCP_GATEWAY_TOKEN   the caller's token (from the host's config)
    env GATEWAY_UPSTREAMS   path to the upstream mcpServers config
    week9/gateway_tokens.json   token -> caller name + denied tools
    week9/logs/gateway_audit.log  one JSON line per tools/call
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from mcp_client import MCPConnection, MCPError, load_config  # noqa: E402

TOKENS = ROOT / "week9" / "gateway_tokens.json"
AUDIT = ROOT / "week9" / "logs" / "gateway_audit.log"
SUPPORTED_VERSIONS = ("2025-06-18", "2025-03-26")


def log(message):
    print(f"[gateway] {message}", file=sys.stderr, flush=True)


class Gateway:
    def __init__(self):
        scopes = json.loads(TOKENS.read_text(encoding="utf-8"))["tokens"]
        token = os.environ.get("MCP_GATEWAY_TOKEN", "")
        self.scope = scopes.get(token)
        self.caller = self.scope["caller"] if self.scope else "unknown-token"
        self.upstreams = {}
        self.routes = {}          # tool name -> upstream name
        self.tools = []
        self.templates = {}       # uri scheme -> upstream name
        upstream_config = ROOT / os.environ.get("GATEWAY_UPSTREAMS", "week9/mcp_config.json")
        for name, spec in load_config(upstream_config).items():
            connection = MCPConnection(name, spec, root=str(ROOT))
            connection.connect()
            self.upstreams[name] = connection
            for tool in connection.list_tools():
                if tool["name"] in self.routes:
                    log(f"tool name collision on {tool['name']}; keeping {self.routes[tool['name']]}")
                    continue
                self.routes[tool["name"]] = name
                self.tools.append(tool)
            for template in connection.list_resource_templates():
                self.templates[template["uriTemplate"].split("://", 1)[0]] = name
        log(f"caller={self.caller} upstreams={list(self.upstreams)} tools={sorted(self.routes)}")

    # ---- policy and audit ------------------------------------------------------

    def denied(self, tool):
        if self.scope is None:
            return "this gateway token is not recognised"
        if tool in self.scope.get("deny_tools", []):
            return f"caller '{self.caller}' is not allowed to call {tool} (token scope)"
        return None

    def audit(self, tool, arguments, server, decision, is_error):
        AUDIT.parent.mkdir(parents=True, exist_ok=True)
        line = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "caller": self.caller, "tool": tool, "server": server,
                "ticket_id": (arguments or {}).get("ticket_id"),
                "decision": decision, "is_error": is_error}
        with AUDIT.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(line) + "\n")

    # ---- JSON-RPC methods ----------------------------------------------------

    def initialize(self, params):
        asked = params.get("protocolVersion")
        return {"protocolVersion": asked if asked in SUPPORTED_VERSIONS else SUPPORTED_VERSIONS[0],
                "capabilities": {"tools": {"listChanged": False},
                                 "resources": {"listChanged": False}},
                "serverInfo": {"name": "clouddesk-gateway", "version": "0.1.0"},
                "instructions": "One front door to the CloudDesk MCP servers. Calls are audited "
                                "and scoped to the caller's token."}

    def tools_call(self, params):
        tool, arguments = params.get("name"), params.get("arguments") or {}
        server = self.routes.get(tool)
        reason = self.denied(tool)
        if reason:
            self.audit(tool, arguments, server, "deny", True)
            alternatives = [t for t in sorted(self.routes) if not self.denied(t) and t != tool]
            return {"isError": True, "content": [{"type": "text", "text": (
                f"Permission denied: {reason}. This is not an outage, and retrying will "
                f"not help. You can still use: {', '.join(alternatives)}. If the user "
                f"needs {tool}, tell them it needs a supervisor's access.")}]}
        if server is None:
            self.audit(tool, arguments, None, "unknown_tool", True)
            return {"isError": True, "content": [{"type": "text", "text":
                    f"Unknown tool {tool!r}. Available: {', '.join(sorted(self.routes))}."}]}
        try:
            result = self.upstreams[server].call_tool(tool, arguments)
        except MCPError as error:
            result = {"isError": True, "content": [{"type": "text", "text": str(error)}]}
        self.audit(tool, arguments, server, "allow", bool(result.get("isError")))
        return result

    def resources_read(self, params):
        uri = params.get("uri", "")
        server = self.templates.get(uri.split("://", 1)[0])
        if server is None:
            raise KeyError(f"no upstream serves {uri}")
        # The app attached this, not the model: allowed, but audited like a
        # call, since it reads the same data a denied tool would.
        ticket = uri.split("://", 1)[1].split("/", 1)[0] if "://" in uri else None
        self.audit(f"resources/read {uri}", {"ticket_id": ticket}, server, "allow", False)
        return self.upstreams[server].read_resource(uri)

    def handle(self, message):
        method, params = message.get("method"), message.get("params") or {}
        if method == "initialize":
            return self.initialize(params)
        if method == "tools/list":
            return {"tools": self.tools}
        if method == "tools/call":
            return self.tools_call(params)
        if method == "resources/list":
            return {"resources": []}
        if method == "resources/templates/list":
            return {"resourceTemplates": [t for c in self.upstreams.values()
                                          for t in c.list_resource_templates()]}
        if method == "resources/read":
            return self.resources_read(params)
        if method == "ping":
            return {}
        raise LookupError(method)

    def serve(self):
        for line in sys.stdin:
            if not line.strip():
                continue
            message = json.loads(line)
            if "id" not in message:            # a notification: never answered
                continue
            reply = {"jsonrpc": "2.0", "id": message["id"]}
            try:
                reply["result"] = self.handle(message)
            except LookupError as missing:
                reply["error"] = {"code": -32601, "message": f"Method not found: {missing}"}
            except Exception as error:         # noqa: BLE001
                reply["error"] = {"code": -32603, "message": f"Internal error: {error}"}
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()
        for connection in self.upstreams.values():
            connection.close()


if __name__ == "__main__":
    Gateway().serve()
