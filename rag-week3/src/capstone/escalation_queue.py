"""A persistent local escalation queue. DEMO ADAPTER.

No external ticketing API exists in this repository, so escalations are
appended to data/capstone/state/escalations.jsonl: one JSON line per event,
latest status wins. The interface (create -> reference, queue, assignee;
list; update) is the one a helpdesk integration would offer.
"""

import json
import threading
from datetime import datetime, timezone

from capstone.config import INTEGRATION_LABELS, STATE_DIR, TIER2_QUEUES
from capstone.pii import mask_text, scrub

QUEUE_FILE = STATE_DIR / "escalations.jsonl"
_LOCK = threading.Lock()

ROUTING = [
    ("legal_privacy", ("lawyer", "legal", "gdpr", "privacy", "pii", "personal data", "chargeback",
                       "delete my", "data deletion", "complaint", "regulator")),
    ("security", ("injection", "prompt", "instruction override", "security", "suspicious")),
    ("shipping", ("shipping", "shipment", "delivery", "carrier", "lost", "damaged", "dead-on-arrival", "doa")),
    ("billing", ("refund", "charge", "invoice", "billing", "entitlement", "order", "policy", "duplicate")),
]


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def route(reason):
    text = (reason or "").lower()
    for queue, words in ROUTING:
        if any(w in text for w in words):
            return queue
    return "general"


def _read_all():
    if not QUEUE_FILE.exists():
        return []
    rows = []
    for line in QUEUE_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def _append(row):
    QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with QUEUE_FILE.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, default=str) + "\n")


def create(ticket_id, reason, priority="normal", evidence=None, notes=None, trace_id=None, by="deflection-assistant"):
    """Open an escalation. Returns the record (reference, queue, assignee, status)."""
    with _LOCK:
        existing = [r for r in _read_all() if r.get("event") == "created"]
        seq = len(existing) + 1
        queue = route(reason)
        record = {
            "event": "created",
            "reference": f"ESC-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{seq:04d}",
            "ticket_id": ticket_id, "reason": mask_text(reason), "priority": priority or "normal",
            "queue": queue, "assigned_to": TIER2_QUEUES.get(queue, TIER2_QUEUES["general"]),
            "status": "open", "evidence": scrub(evidence or {}), "notes": mask_text(notes or ""),
            "trace_id": trace_id, "created_by": by, "created_at": now_iso(),
            "integration": INTEGRATION_LABELS["escalation"],
        }
        _append(record)
    return record


def update(reference, status=None, notes=None, by="agent"):
    with _LOCK:
        row = {"event": "updated", "reference": reference, "status": status, "notes": mask_text(notes),
               "updated_by": by, "updated_at": now_iso()}
        _append(row)
    return row


def list_escalations(status=None, ticket_id=None):
    """Latest state per reference, newest first."""
    current = {}
    for row in _read_all():
        ref = row.get("reference")
        if row.get("event") == "created":
            current[ref] = dict(row)
        elif ref in current:
            if row.get("status"):
                current[ref]["status"] = row["status"]
            if row.get("notes"):
                current[ref]["notes"] = (current[ref].get("notes") or "") + f"\n[{row.get('updated_at')}] {row['notes']}"
            current[ref]["updated_at"] = row.get("updated_at")
    rows = list(current.values())
    if status:
        rows = [r for r in rows if r.get("status") == status]
    if ticket_id:
        rows = [r for r in rows if r.get("ticket_id") == ticket_id]
    rows.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return rows


def get(reference):
    for row in list_escalations():
        if row.get("reference") == reference:
            return row
    return None
