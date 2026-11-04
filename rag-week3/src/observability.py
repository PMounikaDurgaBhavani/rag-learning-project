"""Week 11 — production observability for every request the app answers.

What Week 5's trace did not carry, and a support engineer needs at 3 a.m.:

    request id + spans   where the time went: retrieval, the gates, the model
                         call, the grounding or assertion check — each with a
                         UTC start, a measured duration, a status and an error
    usage + cost         tokens in and out, and what they would cost on the
                         rate card, labelled as the ESTIMATE it is (the models
                         run locally; there is no provider invoice)
    searchable index     a SQLite table over the JSONL log with real indexes
                         (time, prompt version, status, cost, policy verdict)
                         and FTS5 over BOTH the input and the generated output,
                         so "it gave a wrong answer about refunds" can be found
                         from the answer text when nobody knows the question
    policy verdict       for a ticket with charge facts: does the reply agree
                         with HC-008 (eligible -> not denied, ineligible -> not
                         promised)? Decided by code at write time, so a wrong
                         refund answer is one indexed column away

The JSONL log (traces/traces.jsonl) stays the source of truth. The index is a
mirror that can be rebuilt from it with backfill(); records written before
Week 11 are indexed too, with has_request_id / has_spans = 0 rather than
invented values.

Instrumentation is a thread-local request context. The pipeline entry points
(generator.generate_answer, ticket_reply.draft_ticket_reply, the server's
routes) open one with request(); the stages inside wrap themselves in span().
When no request is open, span() still measures and simply has nowhere to
report — nothing in the answering path depends on this module working.
"""

import json
import math
import os
import re
import sqlite3
import statistics
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

TRACE_DIR = Path(os.environ.get("RAG_TRACE_DIR", "traces"))
TRACE_FILE = TRACE_DIR / "traces.jsonl"
OBS_DB = Path(os.environ.get("RAG_OBS_DB", str(TRACE_DIR / "observability.db")))
ENABLED = os.environ.get("RAG_TRACE", "1") not in ("0", "false", "no")

SCHEMA_VERSION = 1
PROVIDER = "local (transformers, in-process)"

# The one cost model the repo already uses (Weeks 7 and 10): tokens are the
# measurement, the rate card is a documented stand-in constant.
COST_BASIS = ("estimate: locally counted tokens x stand-in rate card "
              "(USD 0.15 per M input, 0.60 per M output; GPT-4o-mini class). "
              "The model runs in-process, so no provider charge exists.")
RETRIEVAL_COST_BASIS = ("not separately metered: embedding, pgvector, BM25 and the "
                        "cross-encoder run locally; measured as time only")
TOOLS_COST_BASIS = "local function calls (policy lookup, MCP tools); no per-call charge"


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _epoch(ts):
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def rate_card():
    try:
        from agent_runtime import RATE_CARD
        return dict(RATE_CARD)
    except Exception:
        return {"input_per_mtok": 0.15, "output_per_mtok": 0.60}


def cost_usd(input_tokens, output_tokens):
    card = rate_card()
    return ((input_tokens or 0) * card["input_per_mtok"]
            + (output_tokens or 0) * card["output_per_mtok"]) / 1_000_000


# ---------------------------------------------------------------------------
# request context and spans
# ---------------------------------------------------------------------------

_local = threading.local()


class Span:
    def __init__(self, name, parent_id=None, **attrs):
        self.span_id = uuid.uuid4().hex[:10]
        self.parent_id = parent_id
        self.name = name
        self.start_ts = utc_now()
        self._t0 = time.perf_counter()
        self.end_ts = None
        self.duration_ms = None
        self.status = "ok"
        self.error = None
        self.attrs = {k: v for k, v in attrs.items() if v is not None}

    def set(self, **attrs):
        self.attrs.update({k: v for k, v in attrs.items() if v is not None})
        return self

    def close(self, error=None):
        self.end_ts = utc_now()
        self.duration_ms = round((time.perf_counter() - self._t0) * 1000.0, 2)
        if error is not None:
            self.status = "error"
            self.error = f"{type(error).__name__}: {error}"[:500]

    def to_dict(self):
        return {"span_id": self.span_id, "parent_id": self.parent_id, "name": self.name,
                "start": self.start_ts, "end": self.end_ts, "duration_ms": self.duration_ms,
                "status": self.status, "error": self.error, "attrs": self.attrs}


class RequestContext:
    def __init__(self, request_type, source=None, input_text=None, **meta):
        self.request_id = "req-" + uuid.uuid4().hex[:12]
        self.request_type = request_type
        self.source = source
        self.input_text = input_text
        self.started_at = utc_now()
        self._t0 = time.perf_counter()
        self.spans = []
        self.stack = []
        self.retries = 0
        self.errors = []
        self.evaluation = None
        self.meta = {k: v for k, v in meta.items() if v is not None}

    def elapsed_ms(self):
        return round((time.perf_counter() - self._t0) * 1000.0, 2)

    def to_dict(self):
        return {"request_id": self.request_id, "request_type": self.request_type,
                "source": self.source, "started_at": self.started_at,
                "elapsed_ms": self.elapsed_ms(), "spans": [s.to_dict() for s in self.spans],
                "retries": self.retries, "errors": self.errors,
                "evaluation": self.evaluation, "meta": self.meta}


def current():
    return getattr(_local, "ctx", None)


@contextmanager
def request(request_type, source=None, input_text=None, **meta):
    """Open a request context, or join the one already open on this thread.

    The outermost caller owns the context and closes it; an inner entry point
    (generate_answer called from the server route) just annotates it.
    """
    ctx = current()
    owner = ctx is None
    if owner:
        ctx = RequestContext(request_type, source=source, input_text=input_text, **meta)
        _local.ctx = ctx
    else:
        ctx.meta.setdefault("inner_request_types", []).append(request_type)
        if input_text and not ctx.input_text:
            ctx.input_text = input_text
        ctx.meta.update({k: v for k, v in meta.items() if v is not None})
    try:
        yield ctx
    except Exception as error:
        ctx.errors.append(f"{type(error).__name__}: {error}"[:500])
        raise
    finally:
        if owner:
            _local.ctx = None


@contextmanager
def span(name, **attrs):
    """Measure one stage. Reports into the open request, if any."""
    ctx = current()
    parent = ctx.stack[-1].span_id if ctx and ctx.stack else None
    sp = Span(name, parent_id=parent, **attrs)
    if ctx:
        ctx.stack.append(sp)
    try:
        yield sp
        sp.close()
    except Exception as error:
        sp.close(error)
        raise
    finally:
        if ctx:
            if ctx.stack and ctx.stack[-1] is sp:
                ctx.stack.pop()
            ctx.spans.append(sp)


def annotate(**meta):
    ctx = current()
    if ctx:
        ctx.meta.update({k: v for k, v in meta.items() if v is not None})


def add_retry(reason=None):
    ctx = current()
    if ctx:
        ctx.retries += 1
        if reason:
            ctx.errors.append(f"retry: {reason}"[:300])


def set_evaluation(evaluation):
    ctx = current()
    if ctx:
        ctx.evaluation = evaluation


# ---------------------------------------------------------------------------
# usage, cost, policy verdict — derived from a trace record, never invented
# ---------------------------------------------------------------------------

def usage_of(trace):
    """Tokens in/out with the honest basis. None where never recorded."""
    generation = trace.get("generation") or {}
    meter = trace.get("meter") or {}
    if meter.get("input_tokens") is not None or meter.get("output_tokens") is not None:
        return {"input_tokens": meter.get("input_tokens"), "output_tokens": meter.get("output_tokens"),
                "model_calls": meter.get("model_calls"),
                "basis": "application-counted per model call with the model's own tokenizer "
                         "(summed over every call of the multi-agent run)"}
    inp, out = generation.get("input_tokens"), generation.get("output_tokens")
    if inp is None and out is None:
        if not (trace.get("prompt") or {}).get("messages"):
            return {"input_tokens": 0, "output_tokens": 0, "model_calls": 0,
                    "basis": "no model call: the request was refused before generation"}
        return {"input_tokens": None, "output_tokens": None, "model_calls": 1,
                "basis": "not recorded (trace written before Week 11 added token fields)"}
    basis = "application-counted with the model's own tokenizer; no provider usage field exists for a local model"
    if inp is None:
        basis = "output tokens re-tokenised from the reply; input tokens not recorded for this trace"
    return {"input_tokens": inp, "output_tokens": out, "model_calls": 1, "basis": basis}


def cost_of(trace):
    usage = usage_of(trace)
    inp, out = usage.get("input_tokens"), usage.get("output_tokens")
    complete = inp is not None and out is not None
    generation_usd = round(cost_usd(inp, out), 8) if complete else None
    return {
        "currency": "USD",
        "generation_usd": generation_usd,
        "retrieval_usd": None,
        "tools_usd": 0.0 if (trace.get("meter") or {}).get("tool_calls") else None,
        "total_usd": generation_usd,
        "complete": complete,
        "basis": COST_BASIS if complete else "not computable: token usage incomplete for this trace",
        "retrieval_basis": RETRIEVAL_COST_BASIS,
        "tools_basis": TOOLS_COST_BASIS,
        "rate_card": rate_card(),
        "formula": "total = input_tokens x 0.15/1e6 + output_tokens x 0.60/1e6; retrieval and tools add 0 (unmetered)",
    }


def denies_refund(text):
    """A sentence that refuses THIS customer's refund, or None.

    One definition, shared with the Week 6 assertion no_denial_inside_window
    (src/ticket_assertions.py), so the index and the test suite can never
    disagree about what a denial is.
    """
    from ticket_assertions import refund_denial_sentence
    sentence = refund_denial_sentence(text)
    return sentence[:200] if sentence else None


def policy_verdict(trace):
    """Does the reply agree with HC-008 for the charge on the ticket?

    Returns a dict with verdict in {consistent, contradicts, unverifiable}
    and the reason. Only tickets with charge facts can be checked; a plain
    question has no facts to compare against.
    """
    ticket = trace.get("ticket") or {}
    text = (trace.get("outcome") or {}).get("answer") or ""
    if not ticket or not ticket.get("refund_requested") or ticket.get("charge_amount") is None:
        return {"verdict": "unverifiable", "reason": "no charge facts on the request", "expected": None}
    if (trace.get("outcome") or {}).get("refused"):
        return {"verdict": "unverifiable", "reason": "routed hand-off, no policy statement", "expected": None}
    try:
        from ticket_policy import refund_eligible
        from ticket_assertions import run_assertions
    except Exception as error:
        return {"verdict": "unverifiable", "reason": f"policy module unavailable: {error}", "expected": None}

    eligible = refund_eligible(ticket)
    expected = ("eligible: full refund of the exact amount" if eligible
                else "not eligible: older than 30 days and not a duplicate")
    if eligible:
        sentence = denies_refund(text)
        if sentence:
            return {"verdict": "contradicts", "expected": expected,
                    "reason": f"denies a refund the policy grants: {sentence!r}"}
        return {"verdict": "consistent", "expected": expected, "reason": "no denial of an eligible refund"}

    reply = {"text": text, "refused": False, "output_tokens": None, "max_new_tokens": None}
    rows = run_assertions({"id": "live", "ticket": ticket}, reply)
    row = next((r for r in rows if r["name"] == "no_refund_outside_window"), None)
    if row and row["applicable"] and row["passed"] is False:
        return {"verdict": "contradicts", "expected": expected,
                "reason": f"promises a refund the policy denies: {row['detail']}"}
    return {"verdict": "consistent", "expected": expected, "reason": "no refund promised outside the window"}


def context_ids(trace):
    return [c.get("chunk_id") for c in (trace.get("retrieval") or []) if c.get("chunk_id")]


def status_of(trace):
    if trace.get("errors"):
        return "error"
    if (trace.get("outcome") or {}).get("refused"):
        return "refused"
    return "ok"


def derived_spans(trace):
    """Spans for a record that recorded none itself.

    The multi-agent run carries a task lifecycle with timestamps; one span per
    task is read from it and marked derived. Anything else has no span data,
    and gets none.
    """
    spans = []
    for task in trace.get("tasks") or []:
        transitions = task.get("transitions") or []
        start = next((t["at"] for t in transitions if t.get("state") in ("RUNNING", "ASSIGNED", "CREATED")), None)
        end = transitions[-1]["at"] if transitions else None
        duration = None
        if start and end and _epoch(start) and _epoch(end):
            duration = round((_epoch(end) - _epoch(start)) * 1000.0, 2)
        spans.append({"span_id": task.get("id"), "parent_id": task.get("parent_id"),
                      "name": f"task:{task.get('agent')}", "start": start, "end": end,
                      "duration_ms": task.get("latency_ms") or duration,
                      "status": "ok" if task.get("status") == "COMPLETED" else "error",
                      "error": (task.get("error") or {}).get("message") if task.get("error") else None,
                      "attrs": {"derived_from": "task lifecycle (week10 multi-agent record)",
                                "kind": task.get("kind"), "input_tokens": task.get("input_tokens"),
                                "output_tokens": task.get("output_tokens"), "attempts": task.get("attempts")}})
    return spans


def attach(trace):
    """Add the Week 11 fields to a trace record before it is written.

    Reads the open request context when there is one; everything else is
    derived from the record itself. Never raises.
    """
    try:
        ctx = current()
        trace["observability_version"] = SCHEMA_VERSION
        trace["provider"] = PROVIDER
        if ctx is not None:
            trace["request_id"] = ctx.request_id
            trace["request_type"] = ctx.request_type
            trace["spans"] = [s.to_dict() for s in ctx.spans]
            trace["retries"] = ctx.retries
            trace["errors"] = list(ctx.errors) if ctx.errors else trace.get("errors") or []
            if ctx.evaluation is not None:
                trace["evaluation"] = ctx.evaluation
            if ctx.meta.get("fixture"):
                trace["fixture"] = ctx.meta["fixture"]
            if ctx.meta.get("route"):
                trace["http"] = {"route": ctx.meta["route"], "request_started_at": ctx.started_at}
        trace["usage"] = usage_of(trace)
        trace["cost"] = cost_of(trace)
        trace["status"] = status_of(trace)
        trace["policy_check"] = policy_verdict(trace)
    except Exception as error:                     # never break the answer path
        print(f"  [observability] attach failed: {error}")
    return trace


# ---------------------------------------------------------------------------
# the index
# ---------------------------------------------------------------------------

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS requests (
    trace_id        TEXT PRIMARY KEY,
    request_id      TEXT,
    ts              TEXT NOT NULL,
    ts_epoch        REAL,
    kind            TEXT,
    source          TEXT,
    request_type    TEXT,
    app_version     TEXT,
    input           TEXT,
    output          TEXT,
    prompt_version  TEXT,
    prompt_sha      TEXT,
    model           TEXT,
    provider        TEXT,
    status          TEXT,
    error           TEXT,
    refused         INTEGER,
    refusal_reason  TEXT,
    latency_ms      REAL,
    input_tokens    INTEGER,
    output_tokens   INTEGER,
    cost_usd        REAL,
    cost_complete   INTEGER,
    retries         INTEGER,
    context_ids     TEXT,
    n_spans         INTEGER,
    has_request_id  INTEGER,
    has_spans       INTEGER,
    fixture         TEXT,
    ticket_id       TEXT,
    policy_verdict  TEXT,
    evaluation      TEXT,
    payload         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_req_ts        ON requests(ts_epoch);
CREATE INDEX IF NOT EXISTS idx_req_prompt    ON requests(prompt_version);
CREATE INDEX IF NOT EXISTS idx_req_status    ON requests(status);
CREATE INDEX IF NOT EXISTS idx_req_kind      ON requests(kind);
CREATE INDEX IF NOT EXISTS idx_req_cost      ON requests(cost_usd);
CREATE INDEX IF NOT EXISTS idx_req_policy    ON requests(policy_verdict);
CREATE INDEX IF NOT EXISTS idx_req_ticket    ON requests(ticket_id);
CREATE INDEX IF NOT EXISTS idx_req_request   ON requests(request_id);
CREATE TABLE IF NOT EXISTS spans (
    trace_id    TEXT NOT NULL,
    span_id     TEXT,
    parent_id   TEXT,
    name        TEXT,
    start_ts    TEXT,
    end_ts      TEXT,
    duration_ms REAL,
    status      TEXT,
    error       TEXT,
    attrs       TEXT
);
CREATE INDEX IF NOT EXISTS idx_spans_trace ON spans(trace_id);
"""
FTS_SQL = "CREATE VIRTUAL TABLE IF NOT EXISTS requests_fts USING fts5(trace_id UNINDEXED, input, output)"

_DB_LOCK = threading.Lock()
_FTS = None


def connect(path=None):
    global _FTS
    path = Path(path or OBS_DB)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), timeout=10)
    connection.row_factory = sqlite3.Row
    connection.executescript(CREATE_SQL)
    if _FTS is None:
        try:
            connection.execute(FTS_SQL)
            _FTS = True
        except sqlite3.OperationalError:
            _FTS = False
    return connection


def fts_available():
    if _FTS is None:
        connect().close()
    return bool(_FTS)


def flatten(trace):
    prompt = trace.get("prompt") or {}
    model = trace.get("model") or {}
    outcome = trace.get("outcome") or {}
    usage = trace.get("usage") or usage_of(trace)
    cost = trace.get("cost") or cost_of(trace)
    policy = trace.get("policy_check") or policy_verdict(trace)
    spans = trace.get("spans") or derived_spans(trace)
    ticket = trace.get("ticket") or {}
    errors = trace.get("errors") or []
    return {
        "trace_id": trace["trace_id"],
        "request_id": trace.get("request_id"),
        "ts": trace["ts"], "ts_epoch": _epoch(trace["ts"]),
        "kind": trace.get("kind", "answer"), "source": trace.get("source"),
        "request_type": trace.get("request_type"), "app_version": trace.get("app_version"),
        "input": trace.get("query"), "output": outcome.get("answer"),
        "prompt_version": prompt.get("version"), "prompt_sha": prompt.get("sha"),
        "model": model.get("name"), "provider": trace.get("provider") or PROVIDER,
        "status": trace.get("status") or status_of(trace),
        "error": "; ".join(e if isinstance(e, str) else json.dumps(e, default=str) for e in errors)[:500] or None,
        "refused": 1 if outcome.get("refused") else 0, "refusal_reason": outcome.get("refusal_reason"),
        "latency_ms": (trace.get("latency_ms") or {}).get("total"),
        "input_tokens": usage.get("input_tokens"), "output_tokens": usage.get("output_tokens"),
        "cost_usd": cost.get("total_usd"), "cost_complete": 1 if cost.get("complete") else 0,
        "retries": trace.get("retries", (trace.get("meter") or {}).get("retries")),
        "context_ids": json.dumps(context_ids(trace)),
        "n_spans": len(spans), "has_request_id": 1 if trace.get("request_id") else 0,
        "has_spans": 1 if trace.get("spans") else 0,
        "fixture": json.dumps(trace["fixture"]) if trace.get("fixture") else None,
        "ticket_id": ticket.get("ticket_id"),
        "policy_verdict": policy.get("verdict"),
        "evaluation": json.dumps(trace["evaluation"], default=str) if trace.get("evaluation") else None,
        "payload": json.dumps(trace, default=str),
    }, spans


def index_trace(trace, connection=None, db_path=None):
    """Insert or replace one record (and its spans, and its FTS row)."""
    if not ENABLED:
        return
    own = connection is None
    with _DB_LOCK:
        connection = connection or connect(db_path)
        try:
            row, spans = flatten(trace)
            columns = ", ".join(row)
            placeholders = ", ".join(":" + k for k in row)
            connection.execute(f"INSERT OR REPLACE INTO requests ({columns}) VALUES ({placeholders})", row)
            connection.execute("DELETE FROM spans WHERE trace_id = ?", (trace["trace_id"],))
            for sp in spans:
                connection.execute(
                    "INSERT INTO spans VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (trace["trace_id"], sp.get("span_id"), sp.get("parent_id"), sp.get("name"),
                     sp.get("start"), sp.get("end"), sp.get("duration_ms"), sp.get("status"),
                     sp.get("error"), json.dumps(sp.get("attrs") or {}, default=str)))
            if fts_available():
                connection.execute("DELETE FROM requests_fts WHERE trace_id = ?", (trace["trace_id"],))
                connection.execute("INSERT INTO requests_fts (trace_id, input, output) VALUES (?,?,?)",
                                   (trace["trace_id"], row["input"] or "", row["output"] or ""))
            if own:
                connection.commit()
        finally:
            if own:
                connection.close()


def backfill(jsonl_path=None, db_path=None, only_missing=True):
    """Index every record in the JSONL log. Idempotent; returns counts."""
    path = Path(jsonl_path or TRACE_FILE)
    if not path.exists():
        return {"indexed": 0, "skipped": 0, "total": 0}
    connection = connect(db_path)
    try:
        known = {r[0] for r in connection.execute("SELECT trace_id FROM requests")} if only_missing else set()
        indexed = skipped = total = 0
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                total += 1
                try:
                    trace = json.loads(line)
                except json.JSONDecodeError:
                    skipped += 1
                    continue
                if trace.get("trace_id") in known:
                    skipped += 1
                    continue
                index_trace(trace, connection=connection)
                indexed += 1
        connection.commit()
    finally:
        connection.close()
    return {"indexed": indexed, "skipped": skipped, "total": total}


_BACKFILLED = False


def ensure_index():
    """Backfill once per process; cheap after that (one SELECT of ids)."""
    global _BACKFILLED
    if _BACKFILLED or not ENABLED:
        return {"indexed": 0, "skipped": 0, "total": 0, "cached": True}
    result = backfill()
    _BACKFILLED = True
    return result


# ---------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------

SLIM_COLUMNS = ("trace_id", "request_id", "ts", "kind", "source", "request_type", "input", "output",
                "prompt_version", "model", "provider", "status", "error", "refused", "refusal_reason",
                "latency_ms", "input_tokens", "output_tokens", "cost_usd", "cost_complete", "retries",
                "context_ids", "n_spans", "has_request_id", "has_spans", "fixture", "ticket_id",
                "policy_verdict", "evaluation", "app_version")

MAX_LIMIT = 200


def _slim(row, snippet=240):
    out = {k: row[k] for k in SLIM_COLUMNS}
    out["context_ids"] = json.loads(out["context_ids"] or "[]")
    out["fixture"] = json.loads(out["fixture"]) if out["fixture"] else None
    out["evaluation"] = json.loads(out["evaluation"]) if out["evaluation"] else None
    out["input_preview"] = (out["input"] or "")[:snippet]
    out["output_preview"] = (out["output"] or "")[:snippet]
    out["output_len"] = len(out["output"] or "")
    del out["input"], out["output"]
    return out


def fts_query(text, mode="and"):
    """Turn free text into a safe FTS5 query: every token quoted, prefix-matched."""
    tokens = [t for t in re.findall(r"[\w$.-]+", text or "") if t]
    if not tokens:
        return None
    joiner = " AND " if mode == "and" else " OR "
    return joiner.join('"' + t.replace('"', '""') + '"*' for t in tokens)


def search(q=None, q_mode="and", q_field="both", trace_id=None, request_id=None, ts_from=None, ts_to=None,
           prompt_version=None, kind=None, source=None, status=None, policy_verdict_filter=None,
           ticket_id=None, min_cost=None, outliers=False, include_fixtures=True, fixtures_only=False,
           limit=50, offset=0, order="desc"):
    """Bounded, indexed search over the recorded requests. Returns rows + total."""
    ensure_index()
    limit = max(1, min(int(limit or 50), MAX_LIMIT))
    offset = max(0, int(offset or 0))
    where, params = [], []
    joins = ""
    if q:
        if fts_available():
            query = fts_query(q, q_mode)
            if query:
                column = {"input": "input", "output": "output"}.get(q_field)
                joins = " JOIN requests_fts f ON f.trace_id = r.trace_id"
                where.append(f"f.{column} MATCH ?" if column else "requests_fts MATCH ?")
                params.append(query)
        else:
            like = f"%{q}%"
            if q_field == "input":
                where.append("r.input LIKE ?"); params.append(like)
            elif q_field == "output":
                where.append("r.output LIKE ?"); params.append(like)
            else:
                where.append("(r.input LIKE ? OR r.output LIKE ?)"); params += [like, like]
    if trace_id:
        where.append("r.trace_id LIKE ?"); params.append(trace_id + "%")
    if request_id:
        where.append("r.request_id LIKE ?"); params.append(request_id + "%")
    if ts_from:
        where.append("r.ts_epoch >= ?"); params.append(_epoch(ts_from) or 0)
    if ts_to:
        where.append("r.ts_epoch <= ?"); params.append(_epoch(ts_to) or 0)
    if prompt_version:
        where.append("r.prompt_version = ?"); params.append(prompt_version)
    if kind:
        where.append("r.kind = ?"); params.append(kind)
    if source:
        where.append("r.source = ?"); params.append(source)
    if status:
        where.append("r.status = ?"); params.append(status)
    if policy_verdict_filter:
        where.append("r.policy_verdict = ?"); params.append(policy_verdict_filter)
    if ticket_id:
        where.append("r.ticket_id = ?"); params.append(ticket_id)
    if min_cost is not None:
        where.append("r.cost_usd >= ?"); params.append(float(min_cost))
    if fixtures_only:
        where.append("r.fixture IS NOT NULL")
    elif not include_fixtures:
        where.append("r.fixture IS NULL")

    connection = connect()
    try:
        threshold = None
        if outliers:
            threshold = cost_outlier_threshold(connection)
            if threshold is not None:
                where.append("r.cost_usd > ?"); params.append(threshold)
            else:
                where.append("0")
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        total = connection.execute(f"SELECT COUNT(*) FROM requests r{joins}{clause}", params).fetchone()[0]
        direction = "ASC" if order == "asc" else "DESC"
        rows = connection.execute(
            f"SELECT r.* FROM requests r{joins}{clause} ORDER BY r.ts_epoch {direction} LIMIT ? OFFSET ?",
            params + [limit, offset]).fetchall()
        return {"rows": [_slim(r) for r in rows], "total": total, "limit": limit, "offset": offset,
                "fts": fts_available(), "cost_outlier_threshold_usd": threshold}
    finally:
        connection.close()


def cost_outlier_threshold(connection=None):
    """p95 of the complete costs: an outlier is a request above it."""
    own = connection is None
    connection = connection or connect()
    try:
        values = [r[0] for r in connection.execute(
            "SELECT cost_usd FROM requests WHERE cost_complete = 1 AND cost_usd IS NOT NULL ORDER BY cost_usd")]
    finally:
        if own:
            connection.close()
    if len(values) < 5:
        return None
    return percentile(values, 95)


def percentile(sorted_values, pct):
    """Nearest-rank percentile, the same rule week10_benchmark uses."""
    if not sorted_values:
        return None
    values = sorted(sorted_values)
    rank = max(1, math.ceil(pct / 100.0 * len(values)))
    return values[rank - 1]


def get(trace_id):
    """One trace with its spans and an honest list of what it never recorded."""
    ensure_index()
    connection = connect()
    try:
        row = connection.execute("SELECT * FROM requests WHERE trace_id = ?", (trace_id,)).fetchone()
        if row is None:
            return None
        spans = [dict(s, attrs=json.loads(s["attrs"] or "{}"))
                 for s in connection.execute("SELECT * FROM spans WHERE trace_id = ? ORDER BY start_ts", (trace_id,))]
    finally:
        connection.close()
    trace = json.loads(row["payload"])
    missing = []
    if not row["has_request_id"]:
        missing.append("request_id: not recorded (trace written before Week 11)")
    if not row["has_spans"]:
        missing.append("spans: not recorded" + (" (derived from the task lifecycle instead)" if spans else ""))
    if row["input_tokens"] is None and row["prompt_version"]:
        missing.append("input_tokens: not recorded for this trace")
    if row["output_tokens"] is None and not row["refused"]:
        missing.append("output_tokens: not recorded")
    if row["cost_usd"] is None:
        missing.append("cost: not computable (token usage incomplete)")
    if not row["evaluation"]:
        missing.append("evaluation: this request was not evaluated")
    if row["retries"] is None:
        missing.append("retries: not recorded")
    return {"trace": trace, "summary": _slim(row, snippet=100000), "spans": spans, "missing_fields": missing,
            "usage": trace.get("usage") or usage_of(trace), "cost": trace.get("cost") or cost_of(trace),
            "policy_check": trace.get("policy_check") or policy_verdict(trace)}


def stats():
    ensure_index()
    connection = connect()
    try:
        total = connection.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
        with_spans = connection.execute("SELECT COUNT(*) FROM requests WHERE has_spans = 1").fetchone()[0]
        with_req = connection.execute("SELECT COUNT(*) FROM requests WHERE has_request_id = 1").fetchone()[0]
        span_rows = connection.execute("SELECT COUNT(*) FROM spans").fetchone()[0]
        bounds = connection.execute("SELECT MIN(ts), MAX(ts) FROM requests").fetchone()
        kinds = {r[0]: r[1] for r in connection.execute("SELECT kind, COUNT(*) FROM requests GROUP BY kind")}
        prompts = {r[0]: r[1] for r in connection.execute(
            "SELECT prompt_version, COUNT(*) FROM requests GROUP BY prompt_version")}
        verdicts = {r[0]: r[1] for r in connection.execute(
            "SELECT policy_verdict, COUNT(*) FROM requests GROUP BY policy_verdict")}
        fixtures = connection.execute("SELECT COUNT(*) FROM requests WHERE fixture IS NOT NULL").fetchone()[0]
    finally:
        connection.close()
    return {"db": str(OBS_DB), "jsonl": str(TRACE_FILE), "fts": fts_available(), "requests": total,
            "with_request_id": with_req, "with_spans": with_spans, "span_rows": span_rows,
            "first_ts": bounds[0], "last_ts": bounds[1], "by_kind": kinds, "by_prompt_version": prompts,
            "by_policy_verdict": verdicts, "fixtures": fixtures}


def rows_between(ts_from=None, ts_to=None, include_fixtures=True):
    """Every indexed row in a window, slim, oldest first (for cost and capacity maths)."""
    ensure_index()
    where, params = [], []
    if ts_from:
        where.append("ts_epoch >= ?"); params.append(_epoch(ts_from) or 0)
    if ts_to:
        where.append("ts_epoch <= ?"); params.append(_epoch(ts_to) or 0)
    if not include_fixtures:
        where.append("fixture IS NULL")
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    connection = connect()
    try:
        rows = connection.execute(f"SELECT * FROM requests{clause} ORDER BY ts_epoch ASC", params).fetchall()
        return [_slim(r, snippet=80) for r in rows]
    finally:
        connection.close()


def summarise(values):
    values = [v for v in values if isinstance(v, (int, float))]
    if not values:
        return {"n": 0}
    values.sort()
    return {"n": len(values), "min": values[0], "max": values[-1], "mean": round(statistics.fmean(values), 4),
            "p50": percentile(values, 50), "p95": percentile(values, 95), "p99": percentile(values, 99),
            "sum": round(sum(values), 8)}
