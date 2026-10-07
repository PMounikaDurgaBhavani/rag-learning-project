"""Capture the raw JSON-RPC exchange with the ticket-history server.

    python experiments/week9_wire.py          # stdio -> week9/wire_raw.json
    python experiments/week9_wire.py --http   # streamable HTTP -> week9/wire_http.json

initialize -> notifications/initialized -> tools/list -> tools/call, exactly
as the host's client sends and receives them over stdio, with nothing
reformatted. week9/wire.json is this capture with a hand-written annotation
on every top-level field.

No model is involved: this script plays the host's part with a fixed
tools/call, which is the point. Everything on this wire is plain JSON-RPC;
the model only ever chooses the arguments, back in the host.
"""

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
os.chdir(ROOT)

from mcp_client import MCPConnection, load_config  # noqa: E402

CONFIG = ROOT / "week9" / "mcp_config.json"
OUT = ROOT / "week9" / "wire_raw.json"
HTTP_OUT = ROOT / "week9" / "wire_http.json"
HTTP_PORT = 8931


def start_http_server(spec):
    """Same server, other transport: it listens; the client POSTs."""
    process = subprocess.Popen([str(ROOT / spec["command"]), *spec["args"], "--http", str(HTTP_PORT)],
                               cwd=ROOT, env={**os.environ, **spec.get("env", {})},
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", HTTP_PORT)) == 0:
                return process
        time.sleep(0.5)
    process.kill()
    raise RuntimeError("HTTP server did not start")


def main():
    spec = load_config(CONFIG)["ticket-history"]
    http = "--http" in sys.argv
    server_process = None
    if http:
        server_process = start_http_server(spec)
        spec = {"url": f"http://127.0.0.1:{HTTP_PORT}/mcp"}
    out = HTTP_OUT if http else OUT
    wire = []
    connection = MCPConnection("ticket-history", spec, root=str(ROOT), wire=wire)
    try:
        connection.connect()                  # initialize + notifications/initialized
        connection.list_tools()               # tools/list
        connection.call_tool("lookup_ticket", {"ticket_id": "TCK-4471"})   # tools/call
    finally:
        connection.close()
        if server_process:
            server_process.terminate()
    out.write_text(json.dumps(wire, indent=2) + "\n", encoding="utf-8")
    for item in wire:
        message = item["message"]
        print(f"{item['direction']:<4} {message.get('method') or ('result' if 'result' in message else 'error')}"
              f"  id={message.get('id')}")
    if http:
        print(f"Mcp-Session-Id issued by the server: {connection.session_id}")
    print(f"Wrote {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
