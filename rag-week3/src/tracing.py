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

    Langfuse on when LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are set
            (they are read from .env). Each record becomes a Langfuse
            trace — retrieval and the LLM call as child observations —
            so traces can be browsed, filtered by session/tag and scored.
            Set RAG_LANGFUSE=0 to keep a run local.

The JSONL file stays the source of truth; SQLite and Langfuse are mirrors
that can be rebuilt from it at any time (see backfill_sqlite and
backfill_langfuse).
"""

import atexit
import hashlib
import json
import os
import sqlite3
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path


def _load_dotenv():
    """Pick up LANGFUSE_* and RAG_* settings from the project .env.

    Non-overriding, like db._load_dotenv: a value exported in the shell wins.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)


_load_dotenv()

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
            # Re-tokenised length of the output: a count at the
            # max_new_tokens limit is a reply that was cut off.
            "output_tokens": result.get("output_tokens"),
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


def append(trace, session_id=None, tags=None):
    """Write one trace to every configured sink. Never raises.

    session_id and tags group traces in Langfuse (an eval run, a rerun of
    the Week 5 sample) and are kept on the JSONL record too, so a backfill
    reproduces the same grouping.
    """

    if not TRACING_ENABLED:
        return trace

    if session_id:
        trace["session_id"] = session_id
    if tags:
        trace["tags"] = list(tags)
    if langfuse_client() is not None:
        trace.setdefault("langfuse_trace_id", langfuse_trace_id(trace["trace_id"]))

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

    if trace.get("langfuse_trace_id"):
        try:
            write_langfuse(trace)
        except Exception as error:
            print(f"  [tracing] could not send to Langfuse: {error}")

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
# Langfuse sink
# ---------------------------------------------------------------------------
#
# The record is sent after the answer is complete, rebuilt from the same
# dict that went to JSONL. That keeps one definition of what a trace is:
# Langfuse shows exactly what the replay tooling reads, and any old trace
# can be pushed later with backfill_langfuse.

_LANGFUSE = None
_LANGFUSE_FAILED = False


def langfuse_enabled():
    return (
        TRACING_ENABLED
        and os.environ.get("RAG_LANGFUSE", "1") not in ("0", "false", "no")
        and bool(os.environ.get("LANGFUSE_PUBLIC_KEY"))
        and bool(os.environ.get("LANGFUSE_SECRET_KEY"))
    )


def langfuse_client():
    """The Langfuse client, or None when it is not configured or not installed."""
    global _LANGFUSE, _LANGFUSE_FAILED

    if _LANGFUSE is not None or _LANGFUSE_FAILED or not langfuse_enabled():
        return _LANGFUSE

    try:
        from langfuse import Langfuse
        _LANGFUSE = Langfuse()
        # Spans are exported in a background batch; a short CLI run exits
        # before the batch is sent unless it is flushed on the way out.
        atexit.register(flush)
    except Exception as error:
        _LANGFUSE_FAILED = True
        print(f"  [tracing] Langfuse disabled: {error}")

    return _LANGFUSE


def langfuse_trace_id(trace_id):
    """Langfuse wants 32 hex chars; derive it from our id so the two always match."""
    from langfuse import Langfuse
    return Langfuse.create_trace_id(seed=trace_id)


def write_langfuse(trace, session_id=None, tags=None):
    """Send one trace record to Langfuse. Returns the Langfuse trace id."""

    client = langfuse_client()
    if client is None:
        return None

    from langfuse import propagate_attributes

    outcome = trace["outcome"]
    request = trace.get("request") or {}
    prompt = trace.get("prompt") or {}
    model = trace.get("model") or {}
    generation = trace.get("generation") or {}
    kind = trace.get("kind", "answer")
    family = (outcome.get("refusal_reason") or "").split(" (")[0]
    lf_trace_id = langfuse_trace_id(trace["trace_id"])

    trace_tags = [
        f"source:{trace.get('source')}",
        f"mode:{request.get('retrieval_mode')}",
        f"prompt:{prompt.get('version')}",
        f"refused:{family}" if family else "answered",
    ] + list(trace.get("tags") or []) + list(tags or [])

    with client.start_as_current_observation(
        trace_context={"trace_id": lf_trace_id},
        name=kind,
        as_type="chain",
        input=trace.get("ticket") or trace.get("query"),
        output=outcome.get("answer"),
        version=trace.get("app_version"),
        level="WARNING" if outcome.get("refused") else "DEFAULT",
        status_message=outcome.get("refusal_reason"),
        metadata={
            "rag_trace_id": trace["trace_id"],
            "recorded_at": trace.get("ts"),
            "request": request,
            "refusal_reason": outcome.get("refusal_reason"),
            "best_distance": outcome.get("best_distance"),
            "gate_bypass": outcome.get("gate_bypass"),
            "sources": outcome.get("sources"),
            "latency_ms": trace.get("latency_ms"),
        },
    ) as root:
        with propagate_attributes(
            session_id=session_id or trace.get("session_id"),
            tags=trace_tags,
            trace_name=kind,
            version=trace.get("app_version"),
        ):
            root.start_observation(
                name="retrieval",
                as_type="retriever",
                input={"query": trace.get("query"), **request},
                output=trace.get("retrieval"),
            ).end()

            # A trace refused at the distance gate never called the model;
            # an empty generation would read as a model that said nothing.
            if prompt.get("messages"):
                tokens = generation.get("output_tokens")
                root.start_observation(
                    name="llm",
                    as_type="generation",
                    model=model.get("name"),
                    model_parameters=model.get("params"),
                    input=prompt.get("messages"),
                    output=generation.get("raw_output"),
                    usage_details={"output": tokens} if tokens is not None else None,
                    metadata={
                        "prompt_version": prompt.get("version"),
                        "prompt_sha": prompt.get("sha"),
                        "model_cited": generation.get("model_cited"),
                    },
                ).end()

    return lf_trace_id


def langfuse_score(trace_id, name, value, comment=None, data_type="BOOLEAN"):
    """Attach a score (assertion result, judge verdict, human label) to a trace."""

    client = langfuse_client()
    if client is None:
        return
    if data_type == "BOOLEAN":
        value = 1.0 if value else 0.0
    try:
        client.create_score(
            trace_id=langfuse_trace_id(trace_id),
            name=name,
            value=value,
            comment=comment,
            data_type=data_type,
        )
    except Exception as error:
        print(f"  [tracing] could not score in Langfuse: {error}")


def langfuse_evaluation(trace_id, name, input, output, metadata=None):
    """Record a judge call inside the trace of the reply it judged."""

    client = langfuse_client()
    if client is None:
        return
    try:
        client.start_observation(
            trace_context={"trace_id": langfuse_trace_id(trace_id)},
            name=name,
            as_type="evaluator",
            input=input,
            output=output,
            metadata=metadata,
        ).end()
    except Exception as error:
        print(f"  [tracing] could not record evaluation in Langfuse: {error}")


def flush():
    if _LANGFUSE is not None:
        try:
            _LANGFUSE.flush()
        except Exception as error:
            print(f"  [tracing] Langfuse flush failed: {error}")


def backfill_langfuse(trace_ids=None, jsonl_path=None, session_id=None, tags=None):
    """Push traces already in the JSONL log to Langfuse.

    The Langfuse id is derived from ours, so a trace sent twice lands in the
    same Langfuse trace — with its observations duplicated inside it.
    """
    if langfuse_client() is None:
        raise RuntimeError(
            "Langfuse is not configured: set LANGFUSE_PUBLIC_KEY, "
            "LANGFUSE_SECRET_KEY and LANGFUSE_BASE_URL in .env"
        )

    wanted = set(trace_ids) if trace_ids else None
    sent = 0
    for trace in load_traces(jsonl_path):
        if wanted is None or trace["trace_id"] in wanted:
            write_langfuse(trace, session_id=session_id, tags=tags)
            sent += 1
    flush()
    return sent


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
