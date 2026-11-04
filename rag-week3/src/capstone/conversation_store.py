"""Multi-turn conversations for the chatbot screen. Persisted to
data/capstone/state/conversations.json so a page reload keeps the thread.

A conversation remembers the customer, the ticket it was opened for, every
turn with its trace id, and the entities (order ids) seen so far; the next
turn's understanding step reads them, so "and what about ORD-5001?" followed
by "is it refundable?" still resolves the order.
"""

import json
import os
import tempfile
import threading
import uuid
from datetime import datetime, timezone

from capstone.config import STATE_DIR

FILE = STATE_DIR / "conversations.json"
_LOCK = threading.Lock()
WINDOW = 6          # verbatim turns handed to the next request


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _read():
    if not FILE.exists():
        return {}
    try:
        return json.loads(FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _write(state):
    FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(FILE.parent), prefix=".conv_", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=1, default=str)
    os.replace(tmp, FILE)


def create(customer_id=None, ticket_id=None, channel="chat"):
    with _LOCK:
        state = _read()
        cid = "conv-" + uuid.uuid4().hex[:10]
        state[cid] = {"conversation_id": cid, "customer_id": customer_id, "ticket_id": ticket_id,
                      "channel": channel, "created_at": now_iso(), "turns": []}
        _write(state)
        return state[cid]


def get(conversation_id):
    return _read().get(conversation_id)


def list_conversations(limit=50):
    rows = sorted(_read().values(), key=lambda c: c.get("created_at") or "", reverse=True)
    return rows[:limit]


def append_turn(conversation_id, role, text, **extra):
    with _LOCK:
        state = _read()
        conv = state.get(conversation_id)
        if conv is None:
            raise KeyError(conversation_id)
        conv["turns"].append({"role": role, "text": text, "at": now_iso(), **extra})
        _write(state)
        return conv


def recent_turns(conversation, window=WINDOW):
    return (conversation or {}).get("turns", [])[-window:]
