"""ticket-history MCP server — maintained by Support Ops (support-ops@clouddesk.example).

Third-party to the CloudDesk agent team: we consume it, we do not own it.
Version 0.3.1. Transport: stdio by default, streamable HTTP with --http.

Exposes ticket lookup by id and the escalation history behind it.

    tools      lookup_ticket, get_escalation_history
    resources  tickets://{ticket_id}/escalations
    prompts    shift_handover

Auth: requires TICKET_HISTORY_TOKEN in the environment. Any valid token can
read every ticket in the store; there is no per-customer scope.
Logging: every tool call is appended to week9/logs/ticket_history_access.log
with the arguments and the first 8 characters of the token.
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

HERE = Path(__file__).resolve().parent
STORE = HERE / "tickets.json"
LOG = HERE.parent.parent / "week9" / "logs" / "ticket_history_access.log"
VALID_TOKENS = {"ops-handover-7f3a9c21"}

server = MCPServer("ticket-history", instructions=(
    "Support Ops ticket store. Ticket ids look like TCK-nnnn."))


def _audit(tool, arguments):
    token = os.environ.get("TICKET_HISTORY_TOKEN", "")
    LOG.parent.mkdir(parents=True, exist_ok=True)
    line = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "tool": tool, "arguments": arguments, "token_prefix": token[:8],
            "pid": os.getpid()}
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(line) + "\n")
    print(f"[ticket-history] {tool} {arguments}", file=sys.stderr)
    if token not in VALID_TOKENS:
        raise ToolError("unauthorized: TICKET_HISTORY_TOKEN missing or invalid")


def _ticket(ticket_id):
    tickets = json.loads(STORE.read_text(encoding="utf-8"))["tickets"]
    key = str(ticket_id or "").strip().upper()
    if key not in tickets:
        raise ToolError(f"ticket {ticket_id} not found; ticket ids look like TCK-nnnn")
    return tickets[key]


@server.tool()
def lookup_ticket(ticket_id: str) -> dict:
    """Look up one support ticket by its id (format TCK-nnnn). Returns status,
    owner, subject, when it was opened, and how many times it was escalated.
    Does not return the escalation details; use get_escalation_history."""
    _audit("lookup_ticket", {"ticket_id": ticket_id})
    ticket = _ticket(ticket_id)
    return {k: v for k, v in ticket.items() if k != "escalations"} | {
        "escalation_count": len(ticket["escalations"])}


@server.tool()
def get_escalation_history(ticket_id: str) -> list[dict]:
    """Every escalation on one ticket (format TCK-nnnn), oldest first: when, from
    which team to which, who escalated it and why."""
    _audit("get_escalation_history", {"ticket_id": ticket_id})
    return _ticket(ticket_id)["escalations"]


@server.resource("tickets://{ticket_id}/escalations", mime_type="application/json")
def escalations_resource(ticket_id: str) -> str:
    """Escalation history for one ticket, for the host to attach as context."""
    _audit("resource:escalations", {"ticket_id": ticket_id})
    return json.dumps(_ticket(ticket_id)["escalations"], indent=2)


@server.prompt()
def shift_handover(ticket_id: str) -> str:
    """Template for a shift-handover note on one ticket."""
    return (f"Write a shift-handover note for ticket {ticket_id}: current status, owner, "
            f"what was escalated and why, and what the next shift must do. Look the ticket "
            f"up first; do not guess.")


if __name__ == "__main__":
    if "--http" in sys.argv:
        port = int(sys.argv[sys.argv.index("--http") + 1]) if len(sys.argv) > sys.argv.index("--http") + 1 else 8931
        server.run("streamable-http", host="127.0.0.1", port=port)
    else:
        server.run("stdio")
