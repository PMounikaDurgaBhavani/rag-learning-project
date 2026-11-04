"""The inbox: seeded tickets plus a persisted state overlay.

Seed records never change. Status, assignment, the AI result and every
agent action go to data/capstone/state/tickets_state.json, written
atomically, so the inbox survives a restart and the seed stays diffable.
"""

import json
import os
import tempfile
import threading
from datetime import datetime, timezone

from capstone.config import INTEGRATION_LABELS, STATE_DIR
from capstone.business_tools import customer_summary
from capstone.demo_data import tickets as seed_tickets

STATE_FILE = STATE_DIR / "tickets_state.json"
_LOCK = threading.Lock()

STATUSES = ("new", "ai_processed", "awaiting_approval", "answered", "escalated", "resolved")


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _read_state():
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _write_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(STATE_FILE.parent), prefix=".tickets_", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=1, default=str)
    os.replace(tmp, STATE_FILE)


def _merge(seed, state, include_pii=False):
    row = dict(seed)
    overlay = state.get(seed["ticket_id"], {})
    row["status"] = overlay.get("status", seed.get("status", "new"))
    row["assigned_agent"] = overlay.get("assigned_agent", seed.get("assigned_agent"))
    row["ai"] = overlay.get("ai")
    row["approved_reply"] = overlay.get("approved_reply")
    row["history"] = overlay.get("history", [])
    row["updated_at"] = overlay.get("updated_at")
    row["customer"] = customer_summary(seed["customer_id"], include_pii=include_pii)
    return row


def list_tickets(status=None, q=None, include_pii=False):
    state = _read_state()
    rows = [_merge(t, state, include_pii) for t in seed_tickets().values()]
    if status:
        rows = [r for r in rows if r["status"] == status]
    if q:
        needle = q.lower()
        rows = [r for r in rows if needle in json.dumps(
            {k: r[k] for k in ("ticket_id", "subject", "description", "customer_id", "tags")}).lower()]
    rows.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return rows


def get_ticket(ticket_id, include_pii=False):
    seed = seed_tickets().get(str(ticket_id or "").strip().upper())
    if not seed:
        return None
    return _merge(seed, _read_state(), include_pii)


def _history(entry_type, by, **detail):
    return {"at": now_iso(), "type": entry_type, "by": by, **detail}


def update_ticket(ticket_id, by="agent", status=None, assigned_agent=None, approved_reply=None,
                  note=None, action=None):
    ticket_id = str(ticket_id or "").strip().upper()
    if ticket_id not in seed_tickets():
        raise KeyError(f"no ticket {ticket_id}")
    if status is not None and status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    with _LOCK:
        state = _read_state()
        row = state.setdefault(ticket_id, {"history": []})
        if status is not None:
            row["status"] = status
        if assigned_agent is not None:
            row["assigned_agent"] = assigned_agent or None
        if approved_reply is not None:
            row["approved_reply"] = approved_reply
        row["history"].append(_history(action or "update", by, status=status, assigned_agent=assigned_agent,
                                       note=note, approved_reply_chars=len(approved_reply) if approved_reply else None))
        row["updated_at"] = now_iso()
        _write_state(state)
    return get_ticket(ticket_id)


def record_ai_result(ticket_id, summary, by="deflection-assistant"):
    """Attach the latest AI run (trace id, outcome, draft, citations, tools) to the ticket."""
    ticket_id = str(ticket_id or "").strip().upper()
    if ticket_id not in seed_tickets():
        raise KeyError(f"no ticket {ticket_id}")
    with _LOCK:
        state = _read_state()
        row = state.setdefault(ticket_id, {"history": []})
        row["ai"] = summary
        row["status"] = "escalated" if summary.get("outcome") == "escalated" else "awaiting_approval"
        row["history"].append(_history("ai_run", by, trace_id=summary.get("trace_id"),
                                       outcome=summary.get("outcome"), decision=summary.get("decision")))
        row["updated_at"] = now_iso()
        _write_state(state)
    return get_ticket(ticket_id)


def counts():
    rows = list_tickets()
    by_status = {}
    for r in rows:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    return {"total": len(rows), "by_status": by_status, "integration": INTEGRATION_LABELS["inbox"]}
