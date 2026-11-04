"""Week 11 — production observability: the support drill, cost by stage, the
tenfold projection, the failure -> evaluation -> fix evidence, and the two
actions the UI may run (the approved evaluation suite, the controlled test
incident).

Everything reads the Week 11 index (src/observability.py) over the real trace
log. Nothing here is a fixture of numbers: a count is a COUNT(*), a latency is
a recorded latency, a cost is recorded tokens times the documented rate card,
and anything the log never carried is reported as missing.

    week11_ui.drill("A customer said it gave a wrong answer about refunds yesterday afternoon.")
    week11_ui.cost_summary(days=30)
    week11_ui.tenx(days=30)
    week11_ui.run_suite(judge="none")          # the Week 6 command, output saved under week11/test_results/
    week11_ui.plant_incident("refund-window-inverted")
"""

import argparse
import contextlib
import io
import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
W11 = ROOT / "week11"
TEST_RESULTS = W11 / "test_results"
INCIDENTS = W11 / "incidents"
W6 = ROOT / "week6"
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "experiments"))

import observability as obs  # noqa: E402

LOCAL_TZ = datetime.now().astimezone().tzinfo

DEFAULT_COMPLAINT = "A customer said it gave a wrong answer about refunds yesterday afternoon."

# HC-008 as code knows it (src/ticket_policy.py mirrors the article). These are
# the reference facts a refund answer is checked against.
REFERENCE_POLICY = [
    "A charge is refundable in full only when the refund is requested within 30 days of the charge date (HC-008, Refund Eligibility Window).",
    "A charge older than 30 days is not eligible; agents must not promise or process a refund for it. The customer can turn off automatic renewal in Organization Settings > Billing.",
    "Duplicate charges on the same invoice are refunded in full regardless of the 30-day window (HC-008, Duplicate Charges).",
    "A refund is always for the exact amount charged and returns to the original payment method within 5 to 10 business days.",
    "CloudDesk does not offer discounts, credits or partial refunds in place of a refund.",
]

# The two controlled test incidents. Both are REAL requests through the real
# drafter (src/ticket_reply.py): nothing is injected into the answer. What is
# controlled is the ticket and the label, so the planted record can never be
# mistaken for customer traffic. The first reproduces the defect the drill
# found (an in-window charge told it is outside the window); the second is a
# distinct case for the bonus drill.
FIXTURES = {
    "refund-window-inverted": {
        "title": "Controlled incident 1 — eligible 14-day charge refused",
        "ticket": {"ticket_id": "CD-11901", "tier": "Standard",
                   "message": "I was charged $120 for the Team plan 14 days ago but we cancelled before the renewal date. Please refund it.",
                   "charge_amount": 120.0, "days_since_charge": 14, "duplicate_charge": False, "refund_requested": True},
        "expected": "14 days is inside the 30-day window: eligible for a full refund of $120.00 (HC-008).",
        "regression_case_id": "R05",
    },
    "duplicate-charge-denied": {
        "title": "Controlled incident 2 — duplicate charge at 40 days refused",
        "ticket": {"ticket_id": "CD-11902", "tier": "Standard",
                   "message": "We were charged $89 twice on the same invoice 40 days ago. Please refund the duplicate.",
                   "charge_amount": 89.0, "days_since_charge": 40, "duplicate_charge": True, "refund_requested": True},
        "expected": "A duplicate charge is refunded in full regardless of the 30-day window: eligible for $89.00 (HC-008).",
        "regression_case_id": None,
    },
}


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _text(path):
    return path.read_text(encoding="utf-8") if path.exists() else None


def _json(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")


def mmss(ms):
    seconds = int(round((ms or 0) / 1000.0))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def to_local(ts):
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(LOCAL_TZ).isoformat(timespec="seconds")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# prompt versions
# ---------------------------------------------------------------------------

def prompt_versions():
    """Active prompt versions, plus when each version was first and last seen in the log."""
    import generator
    import ticket_reply
    import tracing
    active = [
        {"path": "chat answers (src/generator.py)", "version": generator.PROMPT_VERSION,
         "sha": generator.prompt_sha(), "model": generator.MODEL_NAME},
        {"path": "ticket replies (src/ticket_reply.py)", "version": ticket_reply.TICKET_PROMPT_VERSION,
         "sha": tracing.sha256(ticket_reply.TICKET_SYSTEM_PROMPT), "model": ticket_reply.TICKET_MODEL_NAME},
    ]
    try:
        import multi_agent
        active.append({"path": "multi-agent synthesis (src/multi_agent.py)", "version": multi_agent.PROMPT_VERSION,
                       "sha": tracing.sha256(ticket_reply.TICKET_SYSTEM_PROMPT),
                       "model": ticket_reply.TICKET_MODEL_NAME,
                       "note": "synthesis reuses the ticket-reply system prompt; its own version tracks the orchestration"})
    except Exception:
        pass
    obs.ensure_index()
    connection = obs.connect()
    try:
        seen = [dict(r) for r in connection.execute(
            "SELECT prompt_version, COUNT(*) AS n, MIN(ts) AS first_seen, MAX(ts) AS last_seen, "
            "SUM(CASE WHEN policy_verdict = 'contradicts' THEN 1 ELSE 0 END) AS contradicts "
            "FROM requests GROUP BY prompt_version ORDER BY MIN(ts)")]
    finally:
        connection.close()
    history = _json(W11 / "prompt_history.json") or []
    return {"active": active, "seen_in_log": seen, "history": history}


# ---------------------------------------------------------------------------
# the support drill
# ---------------------------------------------------------------------------

PART_OF_DAY = {"morning": (6, 12), "afternoon": (12, 18), "evening": (18, 24), "night": (0, 6)}
TOPIC_TERMS = {
    "refund": ["refund", "refunds", "refunded", "refundable"],
    "password": ["password", "reset link"],
    "upload": ["upload"],
    "payment": ["payment", "charged", "billing"],
}


def complaint_window(complaint, as_of=None):
    """Turn 'yesterday afternoon' into a local window and a UTC window.

    as_of is the day the complaint is being handled (default: today, local).
    Reproducible: the same complaint and as_of always give the same window.
    """
    text = (complaint or "").lower()
    as_of_dt = (datetime.fromisoformat(as_of).replace(tzinfo=LOCAL_TZ) if as_of
                else datetime.now(LOCAL_TZ))
    day = as_of_dt.date()
    phrase = []
    if "yesterday" in text:
        day = day - timedelta(days=1); phrase.append("yesterday")
    elif "today" in text:
        phrase.append("today")
    elif (m := re.search(r"(\d+)\s+days?\s+ago", text)):
        day = day - timedelta(days=int(m.group(1))); phrase.append(m.group(0))
    elif "last week" in text:
        start = datetime.combine(day - timedelta(days=7), datetime.min.time(), LOCAL_TZ)
        end = datetime.combine(day, datetime.min.time(), LOCAL_TZ)
        return _window(start, end, ["last week"], as_of_dt)
    hours = (0, 24)
    for name, span in PART_OF_DAY.items():
        if name in text:
            hours = span; phrase.append(name); break
    start = datetime.combine(day, datetime.min.time(), LOCAL_TZ) + timedelta(hours=hours[0])
    end = datetime.combine(day, datetime.min.time(), LOCAL_TZ) + timedelta(hours=hours[1])
    return _window(start, end, phrase, as_of_dt)


def _window(start, end, phrase, as_of_dt):
    return {"phrase": " ".join(phrase) or "(no time phrase: whole day of as_of)",
            "as_of_local": as_of_dt.isoformat(timespec="seconds"),
            "local_start": start.isoformat(timespec="seconds"), "local_end": end.isoformat(timespec="seconds"),
            "utc_start": start.astimezone(timezone.utc).isoformat(timespec="seconds"),
            "utc_end": end.astimezone(timezone.utc).isoformat(timespec="seconds"),
            "tz": str(LOCAL_TZ)}


def complaint_topic(complaint):
    text = (complaint or "").lower()
    for topic, terms in TOPIC_TERMS.items():
        if any(term in text for term in terms):
            return topic, terms
    return None, []


def _match_record(row, trace, verdict, discovered_by):
    ticket = trace.get("ticket") or {}
    reference = list(REFERENCE_POLICY)
    for fixture in FIXTURES.values():
        if fixture["ticket"]["ticket_id"] == ticket.get("ticket_id"):
            reference.insert(0, fixture["expected"])
    return {
        "trace_id": row["trace_id"], "request_id": row["request_id"],
        "ts": row["ts"], "ts_local": to_local(row["ts"]),
        "source": row["source"], "kind": row["kind"], "prompt_version": row["prompt_version"],
        "model": row["model"], "app_version": row["app_version"],
        "ticket": ticket, "input": trace.get("query"),
        "answer": (trace.get("outcome") or {}).get("answer"),
        "policy_check": verdict, "reference_policy": reference,
        "context_ids": row["context_ids"], "cost_usd": row["cost_usd"], "latency_ms": row["latency_ms"],
        "fixture": row["fixture"], "has_request_id": bool(row["has_request_id"]), "has_spans": bool(row["has_spans"]),
        "discovered_by": discovered_by,
    }


def drill(complaint=DEFAULT_COMPLAINT, as_of=None, procedure="slices", max_candidates=500):
    """Find the incident behind a vague complaint, and measure how long it took.

    procedure "slices"  — time window -> topic in input OR output (FTS) ->
                          classify each candidate's answer against HC-008.
                          If the window holds nothing, widen (7 days, then
                          all time) and say so.
    procedure "indexed" — the same window, then the policy_verdict column
                          (the field the first drill showed was missing) with
                          its index; no text scan.
    Every slice records its filter, its hit count and its time.
    """
    obs.ensure_index()
    t0 = time.perf_counter()
    started_at = now_iso()
    window = complaint_window(complaint, as_of)
    topic, terms = complaint_topic(complaint)
    slices, widened_to = [], None

    def timed(name, filter_text, fn, note=None):
        s = time.perf_counter()
        result = fn()
        count = result["total"] if isinstance(result, dict) and "total" in result else len(result)
        slices.append({"slice": name, "filter": filter_text, "count": count,
                       "ms": round((time.perf_counter() - s) * 1000.0, 2), "note": note})
        return result

    windows = [("complaint window", window["utc_start"], window["utc_end"])]
    as_of_dt = datetime.fromisoformat(window["as_of_local"])
    # Widening runs up to now, not to the as-of midnight: a complaint handled
    # today must be able to find something recorded earlier today.
    windows.append(("widened: 7 days before as_of, up to now",
                    (as_of_dt - timedelta(days=7)).astimezone(timezone.utc).isoformat(timespec="seconds"), None))
    windows.append(("widened: all recorded time", None, None))

    matches, candidates_seen, used_window = [], 0, None
    for label, ts_from, ts_to in windows:
        in_window = timed(f"time: {label}", f"ts in [{ts_from or '-inf'}, {ts_to or 'now'}]",
                          lambda: obs.search(ts_from=ts_from, ts_to=ts_to, limit=1))
        if in_window["total"] == 0:
            slices[-1]["note"] = "no requests recorded in this window"
            continue
        if procedure == "indexed":
            hits = timed(f"policy_verdict = contradicts ({label})",
                         f"policy_verdict = 'contradicts' AND ts in window (indexed column, no text scan)",
                         lambda: obs.search(ts_from=ts_from, ts_to=ts_to, policy_verdict_filter="contradicts",
                                            limit=obs.MAX_LIMIT, order="asc"))
            for row in hits["rows"]:
                full = obs.get(row["trace_id"])
                matches.append(_match_record(row, full["trace"], full["policy_check"],
                                             ["ts", "policy_verdict (indexed)", "ticket.charge_amount",
                                              "ticket.days_since_charge", "outcome.answer"]))
            candidates_seen += hits["total"]
        else:
            query = " ".join(terms) if terms else complaint
            topical = timed(f"topic in input OR output ({label})",
                            f"FTS5 MATCH {obs.fts_query(query, 'or')!r} AND ts in window",
                            lambda: obs.search(q=query, q_mode="or", ts_from=ts_from, ts_to=ts_to,
                                               limit=min(max_candidates, obs.MAX_LIMIT), order="asc"))
            s = time.perf_counter()
            for row in topical["rows"]:
                candidates_seen += 1
                full = obs.get(row["trace_id"])
                verdict = obs.policy_verdict(full["trace"])      # classify the answer text now
                if verdict["verdict"] == "contradicts":
                    matches.append(_match_record(row, full["trace"], verdict,
                                                 ["ts", "outcome.answer (full text)", "ticket.charge_amount",
                                                  "ticket.days_since_charge", "ticket.duplicate_charge"]))
            slices.append({"slice": f"classify answers against HC-008 ({label})",
                           "filter": "for each candidate: eligible -> must not deny; ineligible -> must not promise",
                           "count": len([m for m in matches]), "ms": round((time.perf_counter() - s) * 1000.0, 2),
                           "note": f"{len(topical['rows'])} candidate answers read and classified"})
        used_window = label
        if matches:
            widened_to = None if label == "complaint window" else label
            break
        slices[-1]["note"] = (slices[-1].get("note") or "") + " · no contradiction in this window; widening"

    elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)
    missing = [
        "customer / user id: the log has no customer field, so the 'a customer said' slice cannot be used; only time, topic, ticket facts and prompt version were available",
        "complaint timestamp: 'yesterday afternoon' is the complainant's recollection; the window is derived from as_of, not recorded",
    ]
    if procedure == "slices":
        missing.append("policy_verdict: not an indexed field in the first procedure — every candidate answer had to be read and classified (closed by the indexed procedure)")
    if any(not m["has_request_id"] for m in matches):
        missing.append("request_id and spans: absent on records written before Week 11 (shown as 'not recorded', never back-filled)")
    stats = obs.stats()
    return {
        "complaint": complaint, "procedure": procedure, "topic": topic, "topic_terms": terms,
        "started_at": started_at, "completed_at": now_iso(),
        "time_to_find_ms": elapsed_ms, "time_to_find": mmss(elapsed_ms),
        "measured": True, "window": window, "window_used": used_window, "widened_to": widened_to,
        "slices": slices, "candidates_classified": candidates_seen,
        "matches": matches, "match_count": len(matches),
        "fields_used": ["ts (indexed)", "input + output text (FTS5)" if procedure == "slices" else "policy_verdict (indexed)",
                        "ticket.charge_amount / days_since_charge / duplicate_charge", "prompt_version", "source"],
        "missing_fields": missing,
        "limitations": [
            "The classifier is the same code as the Week 6 assertion no_denial_inside_window / no_refund_outside_window: a wrong answer phrased in a way neither regex covers would be missed.",
            "Only tickets with charge facts can be checked against the policy; a plain question about refunds has nothing to compare and is reported as unverifiable.",
            f"Log coverage: {stats['requests']} requests from {stats['first_ts']} to {stats['last_ts']}.",
        ],
        "real_vs_fixture": {"real": sum(1 for m in matches if not m["fixture"]),
                            "fixture": sum(1 for m in matches if m["fixture"])},
    }


# ---------------------------------------------------------------------------
# the controlled incident
# ---------------------------------------------------------------------------

def plant_incident(fixture_id, force=False):
    """Run one controlled ticket through the REAL drafter and record it as a fixture.

    The answer is whatever the current prompt produces; nothing is injected.
    The record carries fixture={...} so the UI, the drill and the cost views
    can keep it apart from customer traffic.
    """
    if fixture_id not in FIXTURES:
        raise ValueError(f"unknown fixture: {fixture_id}; known: {sorted(FIXTURES)}")
    fixture = FIXTURES[fixture_id]
    path = INCIDENTS / f"{fixture_id}.json"
    if path.exists() and not force:
        saved = _json(path)
        return {**saved, "already_planted": True}

    from ticket_reply import draft_ticket_reply, TICKET_PROMPT_VERSION
    label = {"id": fixture_id, "title": fixture["title"], "planted_at": now_iso(),
             "note": "controlled test incident: a real request through the real drafter, labelled so it is never mistaken for customer traffic",
             "expected": fixture["expected"]}
    with obs.request("ticket_reply", source="week11-incident", input_text=fixture["ticket"]["message"],
                     route="week11.plant_incident", fixture=label):
        result = draft_ticket_reply(dict(fixture["ticket"]), source="week11-incident",
                                    tags=["week11", "fixture", f"incident:{fixture_id}"])
    record = obs.get(result["trace_id"]) if result.get("trace_id") else None
    saved = {
        "fixture_id": fixture_id, "title": fixture["title"], "planted_at": label["planted_at"],
        "prompt_version_at_planting": TICKET_PROMPT_VERSION,
        "ticket": fixture["ticket"], "expected": fixture["expected"],
        "trace_id": result.get("trace_id"), "request_id": (result.get("observability") or {}).get("request_id"),
        "answer": result.get("answer"), "refused": result.get("refused"),
        "policy_check": (result.get("observability") or {}).get("policy_check"),
        "evaluation": result.get("evaluation"),
        "retrieved": [c["chunk_id"] for c in result.get("retrieved", [])],
        "spans": (result.get("observability") or {}).get("spans"),
        "wrong_as_expected": ((result.get("observability") or {}).get("policy_check") or {}).get("verdict") == "contradicts",
        "trace_record": record["trace"] if record else None,
    }
    _write_json(path, saved)
    return saved


def incidents():
    return {fid: _json(INCIDENTS / f"{fid}.json") for fid in FIXTURES if (INCIDENTS / f"{fid}.json").exists()}


# ---------------------------------------------------------------------------
# cost & usage
# ---------------------------------------------------------------------------

def _window_rows(days=None, kind=None, include_fixtures=True):
    ts_from = None
    if days:
        ts_from = (datetime.now(timezone.utc) - timedelta(days=float(days))).isoformat(timespec="seconds")
    rows = obs.rows_between(ts_from=ts_from, include_fixtures=include_fixtures)
    if kind:
        rows = [r for r in rows if r["kind"] == kind]
    return rows, ts_from


def _span_totals(trace_ids):
    """Sum of span durations by stage for the given traces (only traces with spans)."""
    if not trace_ids:
        return {}
    obs.ensure_index()
    connection = obs.connect()
    totals = {}
    try:
        marks = ",".join("?" * len(trace_ids))
        for name, n, total in connection.execute(
                f"SELECT name, COUNT(*), SUM(duration_ms) FROM spans WHERE trace_id IN ({marks}) GROUP BY name",
                list(trace_ids)):
            totals[name] = {"spans": n, "total_ms": round(total or 0, 2)}
    finally:
        connection.close()
    return totals


def cost_summary(days=None, kind=None, include_fixtures=True):
    rows, ts_from = _window_rows(days, kind, include_fixtures)
    complete = [r for r in rows if r["cost_complete"]]
    card = obs.rate_card()
    by = lambda key: sorted({r[key] for r in rows if r[key]})  # noqa: E731

    def group(key):
        out = {}
        for value in by(key):
            sub = [r for r in rows if r[key] == value]
            subc = [r for r in sub if r["cost_complete"]]
            out[value] = {"requests": len(sub), "with_cost": len(subc),
                          "cost_usd": obs.summarise([r["cost_usd"] for r in subc]),
                          "latency_ms": obs.summarise([r["latency_ms"] for r in sub]),
                          "input_tokens": sum(r["input_tokens"] or 0 for r in subc),
                          "output_tokens": sum(r["output_tokens"] or 0 for r in subc)}
        return out

    with_spans = [r["trace_id"] for r in rows if r["has_spans"]]
    stage_time = _span_totals(with_spans)
    retrieval_ms = sum(v["total_ms"] for k, v in stage_time.items() if k.startswith("retrieval"))
    generation_ms = sum(v["total_ms"] for k, v in stage_time.items() if k.startswith("generation"))
    validation_ms = sum(v["total_ms"] for k, v in stage_time.items() if k.startswith("validation"))
    tool_ms = sum(v["total_ms"] for k, v in stage_time.items() if k.startswith("tool") or k.startswith("mcp"))
    total_cost = round(sum(r["cost_usd"] or 0 for r in complete), 8)
    retries = sum(r["retries"] or 0 for r in rows if isinstance(r["retries"], (int, float)))
    return {
        "window": {"days": days, "from": ts_from or (rows[0]["ts"] if rows else None),
                   "to": rows[-1]["ts"] if rows else None, "requests": len(rows),
                   "requests_with_complete_cost": len(complete),
                   "requests_without_usage": len(rows) - len(complete),
                   "fixtures_included": include_fixtures},
        "per_query": {
            "cost_usd": obs.summarise([r["cost_usd"] for r in complete]),
            "input_tokens": obs.summarise([r["input_tokens"] for r in complete]),
            "output_tokens": obs.summarise([r["output_tokens"] for r in complete]),
            "latency_ms": obs.summarise([r["latency_ms"] for r in rows]),
        },
        "stages": {
            "retrieval": {"cost_usd": 0.0, "metered": False, "basis": obs.RETRIEVAL_COST_BASIS,
                          "time_ms_total": round(retrieval_ms, 2), "spans": stage_time.get("retrieval", {}).get("spans", 0)},
            "generation": {"cost_usd": total_cost, "metered": "estimate", "basis": obs.COST_BASIS,
                           "input_tokens": sum(r["input_tokens"] or 0 for r in complete),
                           "output_tokens": sum(r["output_tokens"] or 0 for r in complete),
                           "time_ms_total": round(generation_ms, 2)},
            "validation": {"cost_usd": 0.0, "metered": False, "basis": "gates, grounding and assertions are code; no model, no charge",
                           "time_ms_total": round(validation_ms, 2)},
            "tools": {"cost_usd": 0.0, "metered": False, "basis": obs.TOOLS_COST_BASIS, "time_ms_total": round(tool_ms, 2)},
            "total_attributable_usd": total_cost,
            "formula": "total = generation only; generation = Σ(input_tokens) x 0.15/1e6 + Σ(output_tokens) x 0.60/1e6",
        },
        "stage_time_from_spans": stage_time,
        "spans_coverage": {"requests_with_spans": len(with_spans), "requests_without_spans": len(rows) - len(with_spans),
                           "note": "stage times exist only for requests recorded with Week 11 spans; older records have end-to-end latency only"},
        "retries": {"total": retries, "note": "retries are recorded per request from Week 11 on; multi-agent records carry meter.retries"},
        "by_model": group("model"), "by_kind": group("kind"), "by_prompt_version": group("prompt_version"),
        "pricing": {"rate_card": card, "currency": "USD", "basis": obs.COST_BASIS,
                    "provider_usage": "none: local models report no usage; tokens are counted by the application",
                    "outlier_threshold_usd": obs.cost_outlier_threshold()},
    }


# ---------------------------------------------------------------------------
# 10x capacity
# ---------------------------------------------------------------------------

def tenx(days=30, include_fixtures=False):
    rows, ts_from = _window_rows(days, None, include_fixtures)
    complete = [r for r in rows if r["cost_complete"]]
    if rows:
        first = datetime.fromisoformat(rows[0]["ts"].replace("Z", "+00:00"))
        last = datetime.fromisoformat(rows[-1]["ts"].replace("Z", "+00:00"))
        span_days = max((last - first).total_seconds() / 86400.0, 1 / 24.0)
    else:
        span_days = None
    model_rows = [r for r in rows if r["kind"] in ("answer", "ticket_reply", "week10_multi_agent") and r["latency_ms"]]
    answered = [r for r in model_rows if not r["refused"]]
    latency = obs.summarise([r["latency_ms"] for r in answered])
    cost_total = round(sum(r["cost_usd"] or 0 for r in complete), 8)
    tokens_total = sum((r["input_tokens"] or 0) + (r["output_tokens"] or 0) for r in complete)
    per_day = (len(rows) / span_days) if span_days else None
    mean_latency_s = (latency.get("mean") or 0) / 1000.0 if latency.get("n") else None
    # The server answers one model-backed request at a time (RUN_LOCK): the
    # ceiling is one request per mean latency, serially.
    capacity_per_day = (86400.0 / mean_latency_s) if mean_latency_s else None
    projected_per_day = per_day * 10 if per_day else None
    utilisation_now = (per_day / capacity_per_day) if (per_day and capacity_per_day) else None
    utilisation_10x = utilisation_now * 10 if utilisation_now else None

    # Peak hour: the busiest recorded hour, x10, against the hourly ceiling.
    hours = {}
    for r in rows:
        hours[r["ts"][:13]] = hours.get(r["ts"][:13], 0) + 1
    peak_hour, peak_n = (max(hours.items(), key=lambda kv: kv[1]) if hours else (None, 0))
    capacity_per_hour = (3600.0 / mean_latency_s) if mean_latency_s else None

    sufficient = len(answered) >= 20
    bottleneck, evidence = None, []
    if capacity_per_hour and peak_n:
        evidence.append(f"peak recorded hour {peak_hour}Z: {peak_n} requests; x10 = {peak_n * 10}; serial ceiling at mean latency "
                        f"{mean_latency_s:.1f} s = {capacity_per_hour:.0f} requests/hour")
        if peak_n * 10 > capacity_per_hour:
            bottleneck = "latency / throughput (one in-process model behind a single lock)"
        else:
            bottleneck = "none at 10x of the recorded peak; latency is the first constraint to approach"
    if latency.get("p95"):
        evidence.append(f"p95 latency {latency['p95'] / 1000:.1f} s, p99 {latency['p99'] / 1000:.1f} s over n={latency['n']} answered requests")
    evidence.append(f"cost over the window: ${cost_total:.6f} ({len(complete)} requests with usage); x10 = ${cost_total * 10:.6f}")
    evidence.append("rate limits: none configured — the models run in-process; the only quota is the server's one-run-at-a-time lock (HTTP 409 to a second concurrent model request)")
    conclusion = ("Latency breaks first: the drafter answers one request at a time at roughly "
                  f"{mean_latency_s:.0f} s each, so a tenfold peak queues behind the lock long before the "
                  f"stand-in cost (${cost_total * 10:.4f}) or any provider quota matters."
                  if mean_latency_s else "insufficient data")
    return {
        "window": {"days": days, "from": ts_from, "first_request": rows[0]["ts"] if rows else None,
                   "last_request": rows[-1]["ts"] if rows else None, "span_days": round(span_days, 2) if span_days else None,
                   "requests": len(rows), "model_backed_answered": len(answered), "fixtures_included": include_fixtures},
        "current": {"requests_per_day": round(per_day, 2) if per_day else None, "peak_hour_utc": peak_hour,
                    "peak_hour_requests": peak_n, "tokens_total": tokens_total, "cost_usd": cost_total,
                    "latency_ms": latency},
        "projected_10x": {"requests_per_day": round(projected_per_day, 2) if projected_per_day else None,
                          "peak_hour_requests": peak_n * 10, "tokens_total": tokens_total * 10,
                          "cost_usd": round(cost_total * 10, 8),
                          "cost_basis": obs.COST_BASIS},
        "capacity": {"model": "serial: one model-backed request at a time (server RUN_LOCK)",
                     "mean_latency_s": round(mean_latency_s, 2) if mean_latency_s else None,
                     "requests_per_hour_ceiling": round(capacity_per_hour, 1) if capacity_per_hour else None,
                     "requests_per_day_ceiling": round(capacity_per_day, 1) if capacity_per_day else None,
                     "utilisation_now": round(utilisation_now, 4) if utilisation_now is not None else None,
                     "utilisation_10x": round(utilisation_10x, 4) if utilisation_10x is not None else None,
                     "provider_limits": "not applicable (local models); none configured or verified"},
        "bottleneck": bottleneck, "conclusion": conclusion, "evidence": evidence,
        "conditional": not sufficient,
        "conditional_note": (None if sufficient else
                             f"only {len(answered)} answered model-backed requests in the window: the percentiles are "
                             "not stable and the conclusion is conditional on the mean latency holding"),
        "assumptions": [
            "workload scales uniformly: 10x the requests with the same mix of chat answers, ticket replies and multi-agent runs",
            "latency stays at the recorded mean (no warm-up effects removed: the first request of a server session includes model loading)",
            "cost is the stand-in rate card on recorded tokens; the local deployment has no provider bill",
            "arrivals in the peak hour are served serially behind the single model lock",
        ],
        "mitigation": ("Run the drafter as a separate worker pool (2–4 processes, or a hosted endpoint with a real quota) "
                       "so requests are no longer serialised behind one lock, and cache the retrieval stage per query; "
                       "re-measure p95 before raising the ceiling."),
    }


# ---------------------------------------------------------------------------
# failure -> evaluation -> fix evidence
# ---------------------------------------------------------------------------

def _suite_summary(run):
    results = run.get("results") or {}
    failing = sorted(cid for cid, r in results.items() if not r["pass"])
    assertion_fail = {}
    for cid, r in results.items():
        for a in r["assertions"]:
            if a["applicable"] and a["passed"] is False:
                assertion_fail.setdefault(a["name"], []).append(cid)
    return {"ran_at": run.get("ran_at"), "app_version": run.get("app_version"), "judge": run.get("judge"),
            "replies": run.get("replies"), "fresh_cases": run.get("fresh_cases"),
            "ticket_prompt_version": run.get("ticket_prompt_version"), "ticket_prompt_sha": run.get("ticket_prompt_sha"),
            "n_cases": len(results), "passed": sum(1 for r in results.values() if r["pass"]),
            "failed": len(failing), "failing_cases": failing, "assertion_failures": assertion_fail,
            "regression": {cid: {"pass": r["pass"], "assertions_pass": r["assertions_pass"], "judge": r.get("judge"),
                                 "failed_assertions": [a["name"] for a in r["assertions"] if a["applicable"] and a["passed"] is False]}
                           for cid, r in results.items() if cid.startswith("R")}}


def evidence():
    """The saved RED/GREEN runs plus whatever the latest Week 6 runs say."""
    saved = []
    for path in sorted(TEST_RESULTS.glob("suite_*.json")):
        data = _json(path)
        if data:
            stdout = data.get("stdout") or ""
            saved.append({"file": path.relative_to(ROOT).as_posix(), **{k: v for k, v in data.items() if k != "stdout"},
                          "stdout_file": data.get("stdout_file"),
                          "stdout_tail": "\n".join(stdout.splitlines()[-80:])})
    red = next((s for s in saved if s.get("label") == "RED"), None)
    green = next((s for s in reversed(saved) if s.get("label") == "GREEN"), None)
    cases = [json.loads(l) for l in (W6 / "eval_cases.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    new_case = next((c for c in cases if c["id"] == "R05"), None)
    runs = sorted((W6 / "runs").glob("run_*.json"))
    latest = _json(runs[-1]) if runs else None
    return {"saved_runs": saved, "red": red, "green": green, "new_case": new_case,
            "assertion_added": {"name": "no_denial_inside_window", "file": "src/ticket_assertions.py",
                                "replaces": "4. Refund window (eligible side)"},
            "latest_week6_run": {"file": runs[-1].name, **_suite_summary(latest)} if latest else None,
            "suite_command": "python main.py eval --frozen        # 25 frozen labelled replies + R05 drafted live",
            "full_command": "python main.py eval                 # every case drafted fresh, then assertions (+ judge v2 when the protocol allows)",
            "prompt_versions": prompt_versions()}


def run_suite(judge="none", label=None, frozen=True):
    """The UI's Run Evaluation Suite: the fixed Week 6 command, output saved.

    judge is "none" or "auto" (the newest judge the protocol allows) — nothing
    else is accepted, and no part of the command comes from the browser.
    frozen=False is the terminal-only full run (every reply drafted fresh,
    `python main.py eval`), used for the RED and GREEN evidence.
    """
    if judge not in ("none", "auto"):
        raise ValueError("judge must be 'none' or 'auto'")
    import week6_eval
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    before = set(p.name for p in (W6 / "runs").glob("run_*.json"))
    buffer = io.StringIO()
    started = time.perf_counter()

    class Tee(io.TextIOBase):
        """Keep the console output for a long terminal run while capturing it."""
        def write(self, text):
            buffer.write(text)
            if not frozen:
                sys.__stdout__.write(text)
                sys.__stdout__.flush()
            return len(text)

    with contextlib.redirect_stdout(Tee()):
        code = week6_eval.cmd_run(argparse.Namespace(frozen=frozen, judge=None if judge == "auto" else "none"))
    elapsed = round(time.perf_counter() - started, 1)
    stdout = buffer.getvalue()
    new_runs = sorted(set(p.name for p in (W6 / "runs").glob("run_*.json")) - before)
    run_file = (W6 / "runs" / new_runs[-1]) if new_runs else None
    run = _json(run_file) if run_file else None
    TEST_RESULTS.mkdir(parents=True, exist_ok=True)
    txt = TEST_RESULTS / f"suite_{stamp}.txt"
    txt.write_text(stdout, encoding="utf-8")
    record = {"label": label or ("UI run" if judge == "none" else "UI run + judge"),
              "command": ("python main.py eval --frozen" if frozen else "python main.py eval")
                         + ("" if judge == "auto" else " --judge none"),
              "ran_at": now_iso(), "exit_code": code, "seconds": elapsed,
              "run_file": run_file.relative_to(ROOT).as_posix() if run_file else None,
              "stdout_file": txt.relative_to(ROOT).as_posix(),
              "summary": _suite_summary(run) if run else None, "stdout": stdout}
    _write_json(TEST_RESULTS / f"suite_{stamp}.json", record)
    return record


# ---------------------------------------------------------------------------
# the view
# ---------------------------------------------------------------------------

def ui_artefacts():
    stats = obs.stats()
    return {
        "stats": stats,
        "default_complaint": DEFAULT_COMPLAINT,
        "fixtures": {fid: {"title": f["title"], "ticket": f["ticket"], "expected": f["expected"],
                           "regression_case_id": f["regression_case_id"]} for fid, f in FIXTURES.items()},
        "incidents": incidents(),
        "prompt_versions": prompt_versions(),
        "docs": {"drill_md": _text(W11 / "drill.md"), "cost_md": _text(W11 / "cost_by_stage.md"),
                 "tenx_md": _text(W11 / "tenx.md"), "readme_md": _text(W11 / "README.md"),
                 "trace_json_exists": (W11 / "trace.json").exists()},
        "evidence": evidence(),
        "reference_policy": REFERENCE_POLICY,
        "tz": str(LOCAL_TZ),
    }
