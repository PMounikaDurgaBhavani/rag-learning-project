"""Week 11 regression checks: observability, the index, the drill, the API.

    python tests/test_week11.py

What is covered, and what each check protects:

  spans      a request context collects named spans with measured durations,
             nested spans keep their parent, an exception marks the span and
             propagates, and a span outside any request is harmless
  record     attach() puts request id, spans, usage, cost (labelled estimate),
             status and the HC-008 policy verdict on a trace; a record with no
             token fields gets no cost, not a guessed one
  policy     the denial classifier and the new assertion no_denial_inside_window
             catch an in-window charge told it is outside the window, do not
             fire on a correct eligible reply, and stay out of ineligible cases
  index      a temp SQLite index: backfill is idempotent, FTS finds the
             complaint from the OUTPUT text alone, filters by prompt version,
             status, time and cost outliers, pagination is bounded, and an old
             record reports has_request_id/has_spans = 0 instead of fakes
  drill      the complaint window maths, the slices procedure finds a planted
             contradiction from "wrong answer about refunds yesterday
             afternoon", reports time-to-find, widens honestly when the
             window is empty, and the indexed procedure finds the same record
  cost/10x   stage costs are internally consistent (total = generation only;
             retrieval unmetered) and the 10x projection is 10 x the window
  api        the JSON routes over a real socket: search, trace, cost, tenx,
             evidence, drill; bad input -> 400; unknown trace -> 404; the
             eval-run and plant routes refuse bad parameters without running
             anything; the UI markup carries the Week 11 view and controls

Synthetic data only: RAG_TRACE_DIR and RAG_OBS_DB point at a temp folder, so
nothing here touches traces/ or week11/. No model is loaded.
"""

import json
import os
import socket
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="week11_test_"))
os.environ["RAG_TRACE_DIR"] = str(TMP)
os.environ["RAG_OBS_DB"] = str(TMP / "observability.db")
os.environ["RAG_LANGFUSE"] = "0"
os.environ.pop("RAG_TRACE_DB", None)
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "experiments"))
os.chdir(ROOT)

failures = []


def check(name, condition, detail=""):
    print(f"  {'PASS' if condition else 'FAIL'}  {name}{'' if condition else '  ' + str(detail)}")
    if not condition:
        failures.append(name)


import observability as obs  # noqa: E402
import tracing  # noqa: E402
import ticket_assertions as ta  # noqa: E402

check("traces and the index go to the temp dir", Path(tracing.TRACE_FILE).parent == TMP and obs.OBS_DB.parent == TMP,
      (tracing.TRACE_FILE, obs.OBS_DB))

# ===========================================================================
print("Spans — request context")

with obs.request("answer", source="test", input_text="q") as ctx:
    with obs.span("retrieval", mode="dense") as sp:
        time.sleep(0.01)
        sp.set(context_ids=["A", "B"])
    with obs.span("generation") as outer:
        with obs.span("validation.grounding"):
            pass
    try:
        with obs.span("tool:boom"):
            raise RuntimeError("tool failed")
    except RuntimeError:
        pass
names = [s.name for s in ctx.spans]
check("spans are collected in completion order", names == ["retrieval", "validation.grounding", "generation", "tool:boom"], names)
check("durations are measured", ctx.spans[0].duration_ms >= 9, ctx.spans[0].duration_ms)
check("a nested span keeps its parent", ctx.spans[1].parent_id == outer.span_id)
check("an exception marks the span and propagates", ctx.spans[3].status == "error" and "tool failed" in ctx.spans[3].error)
check("the request context closes", obs.current() is None)
with obs.span("orphan") as orphan:
    pass
check("a span outside a request measures and reports nowhere", orphan.duration_ms is not None and obs.current() is None)
with obs.request("outer") as a, obs.request("inner") as b:
    check("an inner request joins the outer one", a is b and "inner" in a.meta["inner_request_types"])

# ===========================================================================
print("\nRecord — attach() derives, never invents")

T0 = datetime(2026, 10, 24, 9, 30, tzinfo=timezone.utc)   # the planted 'yesterday afternoon' in IST is 13:30-17:30 local


def make_trace(trace_id, ts, query, answer, ticket=None, prompt="ticket-v1.2", input_tokens=500, output_tokens=100,
               kind="ticket_reply", refused=False):
    result = {"query": query, "answer": answer, "raw_answer": answer, "refused": refused, "retrieval_mode": "hybrid_rerank",
              "strategy": "markdown", "top_k": 5, "threshold": 0.75, "prompt_version": prompt, "prompt_sha": "abc",
              "prompt_messages": [{"role": "user", "content": query}] if not refused else None,
              "model_name": "Qwen/Qwen2.5-1.5B-Instruct", "model_params": {}, "input_tokens": input_tokens,
              "output_tokens": output_tokens, "retrieved": [{"rank": 1, "chunk_id": "HC-008-markdown-3"}]}
    trace = tracing.build_trace(result, {"total": 1234.5}, source="test", trace_id=trace_id)
    trace["ts"] = ts.isoformat(timespec="milliseconds")
    trace["kind"] = kind
    if ticket:
        trace["ticket"] = ticket
    return trace


eligible = {"ticket_id": "CD-T1", "tier": "Standard", "message": "refund please", "charge_amount": 120.0,
            "days_since_charge": 14, "duplicate_charge": False, "refund_requested": True}
late = {**eligible, "ticket_id": "CD-T2", "days_since_charge": 45}
DENY = "CD-T1: In this case, since the charge was made 14 days ago, it falls outside the eligibility window for a refund."
GRANT = "CD-T1: The $120.00 charge is 14 days old, inside the 30-day window, so a full refund of $120.00 will return to your card in 5-10 business days."
REFUSE_LATE = "CD-T2: The charge is 45 days old, outside the 30-day window, so it is not eligible for a refund."

with obs.request("ticket_reply", source="test") as ctx:
    with obs.span("generation") as sp:
        sp.set(input_tokens=500, output_tokens=100)
    bad = make_trace("bad0000000000001", T0, "I was charged $120 14 days ago, refund?", DENY, eligible,
                     input_tokens=2000, output_tokens=150)
    obs.attach(bad)
check("attach sets request_id and the spans", bad["request_id"] == ctx.request_id and len(bad["spans"]) == 1)
check("usage is application-counted with a stated basis", bad["usage"]["input_tokens"] == 2000 and "tokenizer" in bad["usage"]["basis"])
check("cost is the rate card on the tokens and labelled an estimate",
      abs(bad["cost"]["total_usd"] - (2000 * 0.15 + 150 * 0.60) / 1e6) < 1e-12 and bad["cost"]["basis"].startswith("estimate"))
check("retrieval cost is unmetered, not zero-by-fiat", bad["cost"]["retrieval_usd"] is None and "not separately metered" in bad["cost"]["retrieval_basis"])
check("policy verdict: an in-window charge told it is outside contradicts HC-008",
      bad["policy_check"]["verdict"] == "contradicts", bad["policy_check"])
good = obs.attach(make_trace("good000000000001", T0, "refund?", GRANT, eligible))
check("policy verdict: a correct eligible reply is consistent", good["policy_check"]["verdict"] == "consistent", good["policy_check"])
late_ok = obs.attach(make_trace("late000000000001", T0, "refund?", REFUSE_LATE, late))
check("policy verdict: a correct denial at 45 days is consistent", late_ok["policy_check"]["verdict"] == "consistent", late_ok["policy_check"])
plain = obs.attach(make_trace("plain00000000001", T0, "How long is a reset link valid?", "30 minutes [1]", None, prompt="v1.0", kind="answer"))
check("a plain question is unverifiable, not guessed", plain["policy_check"]["verdict"] == "unverifiable")
old = make_trace("old0000000000001", T0 - timedelta(days=40), "old question", "old answer", None, prompt="v1.0", kind="answer",
                 input_tokens=None, output_tokens=None)
old.pop("generation"); old["generation"] = {"raw_output": "old answer", "model_cited": [], "output_tokens": None}
obs.attach(old)
check("no token fields -> no cost (not an invented one)", old["cost"]["total_usd"] is None and "not computable" in old["cost"]["basis"])

# ===========================================================================
print("\nPolicy — the new assertion")

case = {"id": "S", "ticket": eligible}
rows = {r["name"]: r for r in ta.run_assertions(case, {"text": DENY, "refused": False, "output_tokens": 50, "max_new_tokens": 160})}
check("no_denial_inside_window fails a denial of a 14-day charge", rows["no_denial_inside_window"]["passed"] is False, rows["no_denial_inside_window"])
rows = {r["name"]: r for r in ta.run_assertions(case, {"text": GRANT, "refused": False, "output_tokens": 50, "max_new_tokens": 160})}
check("no_denial_inside_window passes a correct eligible reply", rows["no_denial_inside_window"]["passed"] is True, rows["no_denial_inside_window"])
rows = {r["name"]: r for r in ta.run_assertions({"id": "L", "ticket": late}, {"text": REFUSE_LATE, "refused": False, "output_tokens": 50, "max_new_tokens": 160})}
check("no_denial_inside_window does not apply to an ineligible charge", rows["no_denial_inside_window"]["applicable"] is False)
check("the existing window assertion still passes the correct 45-day denial", rows["no_refund_outside_window"]["passed"] is True)
check("a general policy sentence is not a denial of this case",
      ta.refund_denial_sentence("According to our policy, charges older than 30 days are not eligible for a refund.") is None)
check("7 assertions: the six Week 6 checks plus the Week 11 one", len(ta.ASSERTIONS) == 7 and ta.ASSERTIONS[-1][0] == "no_denial_inside_window")

# ===========================================================================
print("\nIndex — temp SQLite over a synthetic log")

for trace in (bad, good, late_ok, plain, old):
    with open(tracing.TRACE_FILE, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(trace, default=str) + "\n")
noise = []
for i in range(30):
    t = make_trace(f"noise{i:011d}", T0 - timedelta(days=3, minutes=i), f"question {i} about passwords",
                   f"answer {i}: a reset link is valid for 30 minutes", None, prompt="v1.0", kind="answer",
                   input_tokens=300 + i, output_tokens=40)
    obs.attach(t); noise.append(t)
    with open(tracing.TRACE_FILE, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(t, default=str) + "\n")
first = obs.backfill()
again = obs.backfill()
check("backfill indexes every line once", first["indexed"] == 35 and again["indexed"] == 0 and again["skipped"] == 35, (first, again))
check("FTS5 is available in this sqlite", obs.fts_available())
hit = obs.search(q="outside eligibility window", q_field="output")
check("the complaint is found from the OUTPUT text alone", [r["trace_id"] for r in hit["rows"]] == ["bad0000000000001"], hit["rows"])
check("searching the input alone does not find it", obs.search(q="outside eligibility window", q_field="input")["total"] == 0)
check("filter by prompt version", obs.search(prompt_version="ticket-v1.2")["total"] == 3)
check("filter by policy verdict", [r["trace_id"] for r in obs.search(policy_verdict_filter="contradicts")["rows"]] == ["bad0000000000001"])
page = obs.search(kind="answer", limit=10, offset=0)
check("pagination is bounded and counts the total", len(page["rows"]) == 10 and page["total"] == 32 and obs.search(limit=10000)["limit"] == obs.MAX_LIMIT)
window = obs.search(ts_from=(T0 - timedelta(hours=1)).isoformat(), ts_to=(T0 + timedelta(hours=1)).isoformat())
check("time range filter", window["total"] == 4, window["total"])
out = obs.search(outliers=True)
check("cost outliers are those above the p95 of complete costs", out["total"] >= 1 and all(r["cost_usd"] > out["cost_outlier_threshold_usd"] for r in out["rows"]), out["total"])
got = obs.get("old0000000000001")
check("an old record says what it never recorded", not got["summary"]["has_request_id"] and not got["summary"]["has_spans"]
      and any("request_id: not recorded" in m for m in got["missing_fields"]), got["missing_fields"])
got = obs.get("bad0000000000001")
check("a Week 11 record carries its spans and context ids", len(got["spans"]) == 1 and got["summary"]["context_ids"] == ["HC-008-markdown-3"])
check("unknown trace -> None", obs.get("nope") is None)

# ===========================================================================
print("\nDrill — vague complaint to the record")

import week11_ui as w11

# as_of is the day after the planted record; 'yesterday afternoon' in the local zone must contain T0.
local_t0 = T0.astimezone(w11.LOCAL_TZ)
as_of = (local_t0 + timedelta(days=1)).date().isoformat()
win = w11.complaint_window("A customer said it gave a wrong answer about refunds yesterday afternoon.", as_of)
check("the window is yesterday 12:00-18:00 local", win["local_start"].endswith("T12:00:00" + win["local_start"][-6:]) and win["local_end"][11:19] == "18:00:00", win)
in_window = win["utc_start"] <= T0.isoformat() <= win["utc_end"]
d = w11.drill("A customer said it gave a wrong answer about refunds yesterday afternoon.", as_of=as_of)
check("the drill finds the planted contradiction", d["match_count"] >= 1 and d["matches"][0]["trace_id"] == "bad0000000000001", [m["trace_id"] for m in d["matches"]])
check("time-to-find is measured and formatted mm:ss", d["measured"] and d["time_to_find_ms"] > 0 and len(d["time_to_find"]) == 5)
check("widening is reported honestly", (d["widened_to"] is None) == in_window, (d["widened_to"], in_window))
check("slices name the filter and count", all({"slice", "filter", "count", "ms"} <= set(s) for s in d["slices"]) and len(d["slices"]) >= 3)
check("the match shows the wrong answer, the reference facts and the fields that found it",
      d["matches"][0]["answer"] == DENY and d["matches"][0]["reference_policy"] and "outcome.answer (full text)" in d["matches"][0]["discovered_by"])
check("missing fields are listed (no customer id in the log)", any("customer" in m for m in d["missing_fields"]))
d2 = w11.drill("A customer said it gave a wrong answer about refunds yesterday afternoon.", as_of=as_of, procedure="indexed")
check("the indexed procedure finds the same record", [m["trace_id"] for m in d2["matches"]] == [m["trace_id"] for m in d["matches"]])
far = w11.drill("A customer said it gave a wrong answer about refunds yesterday afternoon.", as_of="2030-01-02")
check("an empty window widens and says so", far["widened_to"] is not None and far["match_count"] >= 1, far["widened_to"])

# ===========================================================================
print("\nCost and 10x — internally consistent")

cost = w11.cost_summary()
st = cost["stages"]
check("total attributable = generation; retrieval/validation/tools unmetered (0)",
      st["total_attributable_usd"] == st["generation"]["cost_usd"] and st["retrieval"]["metered"] is False and st["retrieval"]["cost_usd"] == 0.0)
expected_total = sum(r["cost_usd"] for r in obs.rows_between() if r["cost_complete"])
check("generation cost equals the sum of per-request costs", abs(st["generation"]["cost_usd"] - expected_total) < 1e-9)
check("requests without usage are counted, not priced", cost["window"]["requests_without_usage"] == 1)
check("pricing assumptions are stated", cost["pricing"]["rate_card"]["input_per_mtok"] == 0.15 and "stand-in" in cost["pricing"]["basis"])
tx = w11.tenx(days=3650)
check("10x cost is ten times the window cost", abs(tx["projected_10x"]["cost_usd"] - 10 * tx["current"]["cost_usd"]) < 1e-9)
check("10x names the bottleneck and the number behind it", tx["bottleneck"] and tx["evidence"] and tx["conclusion"])
check("a thin sample is labelled conditional", isinstance(tx["conditional"], bool) and (tx["conditional"] == (tx["window"]["model_backed_answered"] < 20)))
check("assumptions are disclosed", len(tx["assumptions"]) >= 3 and tx["mitigation"])

# ===========================================================================
print("\nAPI — over a socket, no model")

import server as srv

sock = socket.socket(); sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]; sock.close()
httpd = srv.StudioServer(("127.0.0.1", port), srv.RAGRequestHandler)
thread = threading.Thread(target=httpd.serve_forever, daemon=True); thread.start()
base = f"http://127.0.0.1:{port}"


def get(path):
    try:
        with urllib.request.urlopen(base + path, timeout=30) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def post(path, body):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


try:
    html = urllib.request.urlopen(base + "/").read().decode()
    check("the UI has the Week 11 view, nav entry and controls",
          'data-view="week11"' in html and 'id="view-week11"' in html and 'id="w11DrillRun"' in html
          and 'id="w11EvalRun"' in html and "/api/week11/requests" in html and "/api/week11/drill" in html)
    status, body = get("/api/week11")
    check("GET /api/week11 returns stats, prompt versions, fixtures and evidence",
          status == 200 and body["stats"]["requests"] == 35 and body["prompt_versions"]["active"] and body["fixtures"] and "evidence" in body, status)
    status, body = get("/api/week11/requests?q=outside+eligibility+window&field=output")
    check("GET /api/week11/requests searches the output", status == 200 and body["total"] == 1 and body["rows"][0]["trace_id"] == "bad0000000000001")
    status, body = get("/api/week11/requests?limit=abc")
    check("bad limit -> 400", status == 400 and body["status"] == "error", status)
    status, body = get("/api/week11/requests?from=yesterday")
    check("bad timestamp -> 400", status == 400, status)
    status, body = get("/api/week11/trace?trace_id=bad0000000000001")
    check("GET /api/week11/trace returns the record, spans, missing fields", status == 200 and body["spans"] and "missing_fields" in body and body["trace"]["trace_id"] == "bad0000000000001")
    status, body = get("/api/week11/trace?trace_id=nope")
    check("unknown trace -> 404", status == 404, status)
    status, body = get("/api/week11/cost?days=3650")
    check("GET /api/week11/cost", status == 200 and body["stages"]["total_attributable_usd"] == body["stages"]["generation"]["cost_usd"])
    status, body = get("/api/week11/cost?days=-1")
    check("bad window -> 400", status == 400, status)
    status, body = get("/api/week11/tenx?days=3650")
    check("GET /api/week11/tenx", status == 200 and body["conclusion"] and "assumptions" in body)
    status, body = get("/api/week11/evidence")
    check("GET /api/week11/evidence", status == 200 and "saved_runs" in body and "suite_command" in body)
    status, body = post("/api/week11/drill", {"complaint": "A customer said it gave a wrong answer about refunds yesterday afternoon.", "as_of": as_of})
    check("POST /api/week11/drill finds the record and measures", status == 200 and body["match_count"] >= 1 and body["measured"], (status, body.get("error")))
    status, body = post("/api/week11/drill", {"complaint": ""})
    check("empty complaint -> 400", status == 400, status)
    status, body = post("/api/week11/drill", {"complaint": "x", "as_of": "not-a-date"})
    check("bad as_of -> 400", status == 400, status)
    status, body = post("/api/week11/eval/run", {"judge": "rm -rf"})
    check("eval run refuses anything but judge none|auto (400, nothing ran)", status == 400, status)
    status, body = post("/api/week11/incident/plant", {"fixture_id": "../../etc/passwd"})
    check("plant refuses an unknown fixture id (400, nothing ran)", status == 400, status)
    status, body = post("/api/week11/drill", [1, 2, 3])
    check("a non-object body -> 400", status == 400, status)
finally:
    httpd.shutdown(); httpd.server_close()

# ===========================================================================
print()
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("All checks passed.")
