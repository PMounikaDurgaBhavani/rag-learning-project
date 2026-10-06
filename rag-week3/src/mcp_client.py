"""A minimal MCP client: JSON-RPC 2.0 over stdio or streamable HTTP.

Written by hand rather than taken from the SDK so that every message on the
wire is visible: pass a list as `wire` and each raw message sent or received
is appended to it, in order. That list is what week9/wire.json is cut from.

One MCPConnection = one server. The host (src/mcp_agent.py) opens one per
entry in its config and never needs to know what any server offers until
tools/list answers.

Nothing in this module calls a model. The client moves JSON between the host
and a server; deciding which tool to call is the host's model's job.

    stdio   the host spawns the server as a child process and speaks
            newline-delimited JSON-RPC on its stdin/stdout (stderr is logs)
    http    the server is already running; every message is a POST to one
            endpoint, and the reply is JSON or a short SSE stream
"""

import itertools
import json
import os
import subprocess
import threading
import time
import urllib.request

PROTOCOL_VERSION = "2025-06-18"
CLIENT_INFO = {"name": "clouddesk-host", "version": "0.9.0"}


class MCPError(Exception):
    """A JSON-RPC error object returned by a server."""

    def __init__(self, error):
        super().__init__(f"{error.get('code')}: {error.get('message')}")
        self.error = error


class MCPConnection:
    def __init__(self, name, spec, root=".", wire=None, timeout=60.0):
        self.name = name
        self.spec = spec
        self.root = root
        self.wire = wire
        self.timeout = timeout
        self._ids = itertools.count(1)
        self.process = None
        self.session_id = None
        self.server_info = None
        self.capabilities = None
        self.protocol_version = None
        self.stderr_lines = []

    # ---- transport -------------------------------------------------------

    @property
    def transport(self):
        return "http" if self.spec.get("url") else "stdio"

    def _record(self, direction, message):
        if self.wire is not None:
            self.wire.append({"server": self.name, "direction": direction,
                              "transport": self.transport,
                              "at": round(time.time(), 3), "message": message})

    def _start_stdio(self):
        command = self.spec["command"]
        # A relative command (venv/bin/python) is relative to the config root.
        if not os.path.isabs(command) and os.sep in command:
            command = os.path.join(self.root, command)
        env = {**os.environ, **(self.spec.get("env") or {})}
        self.process = subprocess.Popen(
            [command, *self.spec.get("args", [])], cwd=self.root, env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1)
        # stderr is the server's log channel; keep it, never parse it.
        threading.Thread(target=self._drain_stderr, daemon=True).start()

    def _drain_stderr(self):
        for line in self.process.stderr:
            self.stderr_lines.append(line.rstrip())

    def _send(self, message):
        self._record("send", message)
        if self.transport == "stdio":
            self.process.stdin.write(json.dumps(message) + "\n")
            self.process.stdin.flush()
            return None
        return self._post(message)

    def _post(self, message):
        headers = {"Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream",
                   "MCP-Protocol-Version": self.protocol_version or PROTOCOL_VERSION}
        headers.update(self.spec.get("headers") or {})
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        request = urllib.request.Request(self.spec["url"], data=json.dumps(message).encode(),
                                         headers=headers, method="POST")
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            self.session_id = response.headers.get("Mcp-Session-Id") or self.session_id
            body = response.read().decode("utf-8")
            kind = response.headers.get("Content-Type", "")
        if not body.strip():
            return []                                  # 202 to a notification
        if "text/event-stream" in kind:
            return [json.loads(line[5:].strip()) for line in body.splitlines()
                    if line.startswith("data:") and line[5:].strip()]
        payload = json.loads(body)
        return payload if isinstance(payload, list) else [payload]

    def _read_stdio(self):
        line = self.process.stdout.readline()
        if not line:
            raise ConnectionError(f"{self.name}: server closed stdout "
                                  f"(stderr: {' | '.join(self.stderr_lines[-3:])})")
        return json.loads(line)

    # ---- JSON-RPC ----------------------------------------------------------

    def request(self, method, params=None):
        message = {"jsonrpc": "2.0", "id": next(self._ids), "method": method}
        if params is not None:
            message["params"] = params
        replies = self._send(message)
        while True:
            if replies is None:                        # stdio: read until our id
                reply = self._read_stdio()
            elif replies:
                reply = replies.pop(0)
            else:
                raise ConnectionError(f"{self.name}: no reply to {method}")
            self._record("recv", reply)
            if reply.get("id") == message["id"] and ("result" in reply or "error" in reply):
                if "error" in reply:
                    raise MCPError(reply["error"])
                return reply["result"]
            # Anything else (a log notification, say) is recorded and skipped.

    def notify(self, method, params=None):
        message = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        self._send(message)

    # ---- the MCP lifecycle ---------------------------------------------------

    def connect(self):
        if self.transport == "stdio":
            self._start_stdio()
        result = self.request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": CLIENT_INFO,
        })
        self.protocol_version = result.get("protocolVersion")
        self.server_info = result.get("serverInfo")
        self.capabilities = result.get("capabilities") or {}
        self.notify("notifications/initialized")
        return result

    def list_tools(self):
        return self._paged("tools/list", "tools")

    def list_resources(self):
        if "resources" not in self.capabilities:
            return []
        return self._paged("resources/list", "resources")

    def list_resource_templates(self):
        if "resources" not in self.capabilities:
            return []
        return self._paged("resources/templates/list", "resourceTemplates")

    def list_prompts(self):
        if "prompts" not in self.capabilities:
            return []
        return self._paged("prompts/list", "prompts")

    def _paged(self, method, key):
        items, cursor = [], None
        while True:
            result = self.request(method, {"cursor": cursor} if cursor else {})
            items.extend(result.get(key, []))
            cursor = result.get("nextCursor")
            if not cursor:
                return items

    def call_tool(self, name, arguments):
        return self.request("tools/call", {"name": name, "arguments": arguments or {}})

    def read_resource(self, uri):
        return self.request("resources/read", {"uri": uri})

    def get_prompt(self, name, arguments=None):
        return self.request("prompts/get", {"name": name, "arguments": arguments or {}})

    def close(self):
        if self.process is not None:
            try:
                self.process.stdin.close()
                self.process.wait(timeout=5)
            except Exception:
                self.process.kill()
            self.process = None


def load_config(path):
    """The Claude-Desktop-style {"mcpServers": {name: spec}} file."""
    with open(path, encoding="utf-8") as handle:
        config = json.load(handle)
    return config.get("mcpServers", {})


def tool_text(result):
    """The text content of a tools/call result, joined."""
    return "\n".join(part.get("text", "") for part in result.get("content", [])
                     if part.get("type") == "text")
