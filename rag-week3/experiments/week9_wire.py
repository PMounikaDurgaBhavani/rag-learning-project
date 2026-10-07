"""Capture the raw JSON-RPC exchange with the ticket-history server.

    python experiments/week9_wire.py        # -> week9/wire_raw.json

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
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
os.chdir(ROOT)

from mcp_client import MCPConnection, load_config  # noqa: E402

CONFIG = ROOT / "week9" / "mcp_config.json"
OUT = ROOT / "week9" / "wire_raw.json"


def main():
    spec = load_config(CONFIG)["ticket-history"]
    wire = []
    connection = MCPConnection("ticket-history", spec, root=str(ROOT), wire=wire)
    try:
        connection.connect()                  # initialize + notifications/initialized
        connection.list_tools()               # tools/list
        connection.call_tool("lookup_ticket", {"ticket_id": "TCK-4471"})   # tools/call
    finally:
        connection.close()
    OUT.write_text(json.dumps(wire, indent=2) + "\n", encoding="utf-8")
    for item in wire:
        message = item["message"]
        print(f"{item['direction']:<4} {message.get('method') or ('result' if 'result' in message else 'error')}"
              f"  id={message.get('id')}")
    print(f"Wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
