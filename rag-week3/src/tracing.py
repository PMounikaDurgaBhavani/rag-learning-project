"""Trace every answer with enough detail to replay it from the trace alone.

Week 5 asks for traces that can be replayed without the live system, which
sets the bar for what a record has to carry:

    prompt version      so a later prompt edit is visible as a version bump
    the exact messages  the literal text sent to the model, not a recipe
                        for rebuilding it — an index that has been
                        re-chunked since cannot reproduce the old prompt
    retrieved chunks    ids AND every score that ordered them
    model + params      name and decoding settings
    raw output          what the model said before any gate touched it

Anything reconstructed at replay time rather than read from the trace is a
thing the trace was not carrying, so all of it is stored verbatim.

Storage
-------
Two sinks, both off a single append call:

    JSONL   always on. One line per trace, append-only, greppable, and it
            diffs sanely in git.
    SQLite  optional, off by default. Set RAG_TRACE_DB=path/to/traces.db
            and the same record is also written to a real table, which is
            what you want once "read every trace about billing" becomes a
            query rather than a scroll.

The JSONL file stays the source of truth; SQLite is a queryable mirror
that can be rebuilt from it at any time (see backfill_sqlite).
"""

import hashlib
import json
import os
import sqlite3
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

TRACE_DIR = Path(os.environ.get("RAG_TRACE_DIR", "traces"))
TRACE_FILE = TRACE_DIR / "traces.jsonl"

# Optional second sink. Unset = JSONL only.
TRACE_DB = os.environ.get("RAG_TRACE_DB")

# Tracing is on unless explicitly disabled — a trace you forgot to turn on
# is a week of traffic you cannot read.
TRACING_ENABLED = os.environ.get("RAG_TRACE", "1") not in ("0", "false", "no")

SCHEMA_VERSION = 1


def _utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def sha256(text):
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


_APP_VERSION = None


def app_version():
    """Short git sha of the code that produced the trace.

    A taxonomy is only meaningful against a known build; without this a
    trace from before a change and one from after are indistinguishable.
    """
    global _APP_VERSION

    if _APP_VERSION is None:
        try:
            _APP_VERSION = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                capture_output=True, text=True, timeout=5,
                cwd=Path(__file__).resolve().parent,
            ).stdout.strip() or "unknown"
        except Exception:
            _APP_VERSION = "unknown"

    return _APP_VERSION


def new_trace_id():
    return uuid.uuid4().hex[:16]


# ---------------------------------------------------------------------------
# writing
# ---------------------------------------------------------------------------

def build_trace(result, latency_ms, source="cli", trace_id=None):
    """Turn a generate_answer result into a replayable trace record."""

    prompt_messages = result.get("prompt_messages")

    return {
        "schema_version": SCHEMA_VERSION,
        "trace_id": trace_id or new_trace_id(),
        "ts": _utc_now(),
        "app_version": app_version(),
        "source": source,
        "query": result.get("query"),
        "request": {
            "retrieval_mode": result.get("retrieval_mode"),
            "strategy": result.get("strategy"),
            "top_k": result.get("top_k"),
            "threshold": result.get("threshold"),
            "min_coverage": result.get("min_coverage"),
            "filter": result.get("filter"),
        },
        "prompt": {
            "version": result.get("prompt_version"),
            "sha": result.get("prompt_sha"),
            # The literal messages sent. Replay reads these rather than
            # rebuilding them, so a re-chunked index cannot silently
            # change what "the same trace" means.
            "messages": prompt_messages,
        },
        "model": {
            "name": result.get("model_name"),
            "params": result.get("model_params"),
        },
        "retrieval": result.get("retrieved", []),
        "generation": {
            "raw_output": result.get("raw_answer"),
            "model_cited": result.get("model_cited", []),
        },
        "outcome": {
            "answer": result.get("answer"),
            "refused": result.get("refused"),
            "refusal_reason": result.get("refusal_reason"),
            "best_distance": result.get("best_distance"),
            "gate_bypass": result.get("gate_bypass"),
            "lexical_anchors": result.get("lexical_anchors", []),
            "sources": [
                {
                    "marker": s.get("marker"),
                    "chunk_id": s.get("chunk_id"),
                    "coverage": s.get("coverage"),
                }
                for s in result.get("sources", [])
            ],
            "grounding_scores": result.get("grounding_scores", []),
        },
        "latency_ms": latency_ms,
    }


def append(trace):
    """Write one trace to every configured sink. Never raises."""

    if not TRACING_ENABLED:
        return trace

    try:
        TRACE_DIR.mkdir(parents=True, exist_ok=True)
        with open(TRACE_FILE, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(trace, default=str) + "\n")
    except Exception as error:            # tracing must never break answering
        print(f"  [tracing] could not write JSONL: {error}")

    if TRACE_DB:
        try:
            write_sqlite(trace, TRACE_DB)
        except Exception as error:
            print(f"  [tracing] could not write SQLite: {error}")

    return trace


# ---------------------------------------------------------------------------
# SQLite sink
# ---------------------------------------------------------------------------

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS traces (
    trace_id        TEXT PRIMARY KEY,
    ts              TEXT NOT NULL,
    app_version     TEXT,
    source          TEXT,
    query           TEXT NOT NULL,
    retrieval_mode  TEXT,
    strategy        TEXT,
    top_k           INTEGER,
    threshold       REAL,
    prompt_version  TEXT,
    prompt_sha      TEXT,
    model_name      TEXT,
    refused         INTEGER,
    refusal_reason  TEXT,
    best_distance   REAL,
    answer          TEXT,
    raw_output      TEXT,
    n_sources       INTEGER,
    latency_ms      REAL,
    -- the full record, so nothing is lost to the flattened columns
    payload         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_traces_ts       ON traces(ts);
CREATE INDEX IF NOT EXISTS idx_traces_refusal  ON traces(refusal_reason);
CREATE INDEX IF NOT EXISTS idx_traces_mode     ON traces(retrieval_mode);
"""


def connect(db_path=None):
    """Open (and if needed create) the trace database."""
    path = Path(db_path or TRACE_DB or "traces/traces.db")
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path))
    connection.row_factory = sqlite3.Row
    connection.executescript(CREATE_SQL)
    return connection


def write_sqlite(trace, db_path=None, connection=None):
    """Insert one trace. Re-writing the same trace_id is a no-op."""

    own = connection is None
    connection = connection or connect(db_path)

    try:
        connection.execute(
            """INSERT OR IGNORE INTO traces VALUES
               (:trace_id,:ts,:app_version,:source,:query,:retrieval_mode,
                :strategy,:top_k,:threshold,:prompt_version,:prompt_sha,
                :model_name,:refused,:refusal_reason,:best_distance,:answer,
                :raw_output,:n_sources,:latency_ms,:payload)""",
            {
                "trace_id": trace["trace_id"],
                "ts": trace["ts"],
                "app_version": trace.get("app_version"),
                "source": trace.get("source"),
                "query": trace.get("query"),
                "retrieval_mode": trace["request"].get("retrieval_mode"),
                "strategy": trace["request"].get("strategy"),
                "top_k": trace["request"].get("top_k"),
                "threshold": trace["request"].get("threshold"),
                "prompt_version": trace["prompt"].get("version"),
                "prompt_sha": trace["prompt"].get("sha"),
                "model_name": trace["model"].get("name"),
                "refused": 1 if trace["outcome"].get("refused") else 0,
                "refusal_reason": trace["outcome"].get("refusal_reason"),
                "best_distance": trace["outcome"].get("best_distance"),
                "answer": trace["outcome"].get("answer"),
                "raw_output": trace["generation"].get("raw_output"),
                "n_sources": len(trace["outcome"].get("sources", [])),
                "latency_ms": (trace.get("latency_ms") or {}).get("total"),
                "payload": json.dumps(trace, default=str),
            },
        )
        if own:
            connection.commit()
    finally:
        if own:
            connection.close()


def backfill_sqlite(db_path=None, jsonl_path=None):
    """Rebuild the database from the JSONL log.

    The JSONL is the source of truth, so the table can always be dropped
    and regenerated — useful after adding a column, and the reason the
    SQLite sink can stay optional without risking data loss.
    """
    rows = load_traces(jsonl_path)
    connection = connect(db_path)
    try:
        for trace in rows:
            write_sqlite(trace, connection=connection)
        connection.commit()
    finally:
        connection.close()
    return len(rows)


# ---------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------

def load_traces(path=None):
    """Every trace in the log, oldest first. Bad lines are skipped loudly."""

    path = Path(path or TRACE_FILE)
    if not path.exists():
        return []

    traces = []
    with open(path, "r", encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                traces.append(json.loads(line))
            except json.JSONDecodeError:
                print(f"  [tracing] skipping unparseable line {number}")
    return traces


def get_trace(trace_id, path=None):
    for trace in load_traces(path):
        if trace.get("trace_id") == trace_id:
            return trace
    return None
