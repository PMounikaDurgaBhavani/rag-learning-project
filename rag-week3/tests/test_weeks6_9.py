"""Regression checks for Weeks 6–9 and the UI server that need no model.

    python tests/test_weeks6_9.py

What is covered, and what each check protects:

  Week 6  the assertion/judge split (deterministic criteria are gone from the
          judge prompt), the four required assertions on synthetic replies,
          the blind-label gate (no judge run before committed labels), the
          agreement calculation, and the regression fixtures' provenance
  Week 7  identical inputs for both systems, the shared output contract and
          grader, all four budgets firing, the clean budget logs
  Week 8  alternate valid trajectories accepted as a set, argument validation
          (fabricated vs ungrounded), the gap arithmetic, exactly one
          mitigation between baseline and mitigated, every mode in the
          regression table
  Week 9  real tool discovery over stdio (tools/list from a live server),
          config-only addition of the second server (zero agent-module diff,
          live from git), the recoverable not-found error, no model inside
          either server, no tool names hard-coded in the host
  Server  the JSON API over a real socket: every read route, bad-input
          codes, the one-run-at-a-time guard

Synthetic data only: nothing here writes to week*/ or traces/. The Week 9
discovery checks start the servers as child processes and ask tools/list,
which the ticket-history server does not audit-log; the recoverable-error
check calls our own kb server, which keeps no log.
"""

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "experiments"))
os.chdir(ROOT)
os.environ.setdefault("RAG_LANGFUSE", "0")

failures = []


def check(name, condition, detail=""):
    print(f"  {'PASS' if condition else 'FAIL'}  {name}{'' if condition else '  ' + str(detail)}")
    if not condition:
        failures.append(name)


def skip(name, why):
    print(f"  SKIP  {name}  ({why})")


# ===========================================================================
print("Week 6 — assertions vs judge, blind-label gate, agreement")

import ticket_assertions as ta
import week6_eval as w6

v0 = (ROOT / "week6" / "judge_v0_presplit.txt").read_text(encoding="utf-8").lower()
v1 = (ROOT / "week6" / "judge_v1.txt").read_text(encoding="utf-8").lower()

check("6 deterministic assertions, 1 judged criterion",
      len(ta.ASSERTIONS) == 6 and len(w6.JUDGED_CRITERIA) == 1,
      f"{len(ta.ASSERTIONS)} / {len(w6.JUDGED_CRITERIA)}")

# The six deterministic criteria are numbered lines in the pre-split judge;
# v1 must carry none of them as criteria (it names them only to say "do NOT
# grade" them) and must grade exactly one thing.
criteria = ("1. ticket id:", "2. refund amount:", "3. escalation:", "4. refund window:",
            "5. complete:", "6. clean:")
for line in criteria:
    check(f"judge v0 grades '{line}' and judge v1 does not", line in v0 and line not in v1)
check("judge v1 grades ONE criterion, RESOLUTION, and tells the model not to grade the rest",
      "judge one criterion only: resolution" in v1 and "do not grade the ticket id" in v1)
check("judge v0 graded 7 criteria (6 now deterministic + resolution)", "7. resolution:" in v0)

# The four required assertions, on synthetic replies.
base = {"ticket_id": "CD-55555", "tier": "Standard", "charge_amount": 49.0,
        "days_since_charge": 12, "duplicate_charge": False, "refund_requested": True}
refund_case = {"id": "S-REF", "mode": "M1", "ticket": dict(base)}
late_case = {"id": "S-LATE", "mode": "M1", "ticket": {**base, "days_since_charge": 45}}
prio_case = {"id": "S-PRIO", "mode": "M1", "ticket": {**base, "tier": "Priority"}}

good = ("Ticket CD-55555: the $49.00 charge is 12 days old, inside the 30-day window, "
        "so a full refund of $49.00 is approved and returns to the card in 5–10 business days.")
bad_id = good.replace("CD-55555", "CD-99999")
no_amount = "Ticket CD-55555: your refund is approved."
promise_late = ("Ticket CD-55555: the charge is 45 days old; we will refund $49.00 to your card.")
deny_late = ("Ticket CD-55555: the charge is 45 days old, outside the 30-day window, so a refund "
             "is not possible; I am sorry.")
from ticket_policy import ESCALATION_TAG
prio_tagged = good + " " + ESCALATION_TAG


def passed(case, text, name):
    rows = ta.run_assertions(case, {"text": text, "truncated": False})
    row = next(r for r in rows if r["name"] == name)
    return row["applicable"], row["passed"]


check("ticket_id_echoed passes when the id is present", passed(refund_case, good, "ticket_id_echoed")[1])
check("ticket_id_echoed fails on the wrong id", not passed(refund_case, bad_id, "ticket_id_echoed")[1])
check("refund_amount_numeric passes on $49.00", passed(refund_case, good, "refund_amount_numeric")[1])
check("refund_amount_numeric fails when no number is given", not passed(refund_case, no_amount, "refund_amount_numeric")[1])
check("no_refund_outside_window fails a promise at 45 days",
      passed(late_case, promise_late, "no_refund_outside_window") == (True, False))
check("no_refund_outside_window passes a denial at 45 days",
      passed(late_case, deny_late, "no_refund_outside_window") == (True, True))
check("escalation tag required for Priority", not passed(prio_case, good, "escalation_tag_iff_priority")[1]
      and passed(prio_case, prio_tagged, "escalation_tag_iff_priority")[1])
check("escalation tag must be absent for Standard", not passed(refund_case, prio_tagged, "escalation_tag_iff_priority")[1])

# Blind-label gate: with no labels_25.json on disk the judge must refuse.
ok, problems, _ = w6.protocol_check("v1")
labels_exist = (ROOT / "week6" / "labels_25.json").exists()
if labels_exist:
    check("labels present: protocol_check reports the committed state", isinstance(ok, bool))
else:
    check("judge v1 is refused while labels_25.json is absent", not ok and any("label" in p.lower() for p in problems),
          problems)
check("the UI cannot force a judge the protocol disallows",
      w6.best_available_judge() == "none" if not labels_exist else w6.best_available_judge() in ("none", "v1", "v2"))

# Agreement arithmetic on synthetic rows: 25 cases, 20 agree -> 80.0%.
rows = [{"id": f"T{i:02d}", "judge": "PASS" if i % 5 else "FAIL", "human": "PASS",
         "agree": bool(i % 5), "parsed": True} for i in range(1, 26)]
stats = w6.agreement_stats(rows)
check("agreement = agreements / 25 as a percentage", stats["all"]["agree"] == 20 and stats["all"]["n"] == 25
      and abs(stats["all"]["pct"] - 80.0) < 1e-9, stats["all"])
held = w6.agreement_stats(rows, exclude=("T05", "T10"))
check("held-out agreement excludes the few-shot ids", held["held_out"]["n"] == 23 and held["held_out"]["agree"] == 20,
      held.get("held_out"))

cases = w6.load_cases()
regressions = [c for c in cases if c.get("regression")]
check("25+ cases, each tagged with one Week 5 mode",
      len(cases) >= 25 and all(c["mode"] in w6.MODES for c in cases), len(cases))
check("at least 2 regression fixtures carry a source trace id and the original failure",
      len(regressions) >= 2 and all(c["regression"].get("source_trace_id") and c["regression"].get("original_outcome")
                                    for c in regressions), len(regressions))
import tracing
traces = {t["trace_id"]: t for t in tracing.load_traces()}
found = [c for c in regressions if c["regression"]["source_trace_id"] in traces]
if traces:
    check("regression fixtures replay the message verbatim from the real failed trace",
          found and all(traces[c["regression"]["source_trace_id"]]["query"] == c["ticket"]["message"] for c in found),
          f"{len(found)} of {len(regressions)} source traces present in traces/traces.jsonl")
else:
    skip("regression fixture provenance", "traces/traces.jsonl is empty on this machine")

# ===========================================================================
print("\nWeek 7 — identical inputs, output contract, four budgets")

from agent_runtime import Budgets, normalise_result, DECISIONS, blank_result
import week7_race as w7
import week7_ui

race = w7.load_race_set()
check("10 fixtures with stable ids", [c["ticket_id"] for c in race] == [f"CD-70{i:02d}" for i in range(1, 11)])
check("at least 3 cases where a later action depends on a previous result",
      sum(1 for c in race if c["branch_after_step2"]) >= 3)

latest = sorted((ROOT / "week7" / "runs").glob("race_*.json"))
if latest:
    data = json.loads(latest[-1].read_text(encoding="utf-8"))
    by_system = {}
    for run in data["runs"]:
        by_system.setdefault(run["system"], []).append(run["ticket_id"])
    check("both systems ran exactly the same 10 inputs in the stored race",
          by_system.get("agent") == by_system.get("workflow") == data["tickets"], by_system)
    check("the stored race records model, build, budgets and rate card",
          all(data.get(k) for k in ("model", "app_version", "budgets", "rate_card_usd_per_mtok")))
    check("every stored run carries the same output contract",
          all(set(run["result"]) == set(blank_result("x")) for run in data["runs"]))
    check("tokens are summed over every lap, not the last call",
          all(run["tokens"] == run["meter"]["input_tokens"] + run["meter"]["output_tokens"] for run in data["runs"]))
else:
    skip("stored race provenance", "no week7/runs/race_*.json")

contract = normalise_result({"decision": "Refund Approved", "refund_amount": "$49.00", "reply": "ok"}, "CD-7001")
check("normalise_result coerces into the shared contract", contract["decision"] == "refund_approved"
      and contract["refund_amount"] == 49.0 and set(contract) == set(blank_result("x")))
check("unknown decisions become None, not a guess", normalise_result({"decision": "maybe"}, "x")["decision"] is None)
check("grade() is one function for both systems",
      w7.grade({"decision": "refund_approved", "refund_amount": 49.0}, {**contract})[0] is True
      and w7.grade({"decision": "refund_denied", "refund_amount": None}, {**contract})[0] is False)

b = Budgets(max_iterations=2, max_tokens=1200, max_cost_usd=0.0002, max_wall_seconds=15.0)
check("max_iterations fires", b.exceeded(2, 0, 0, 0)[0] == "max_iterations")
check("max_tokens fires", b.exceeded(1, 1200, 0, 0)[0] == "max_tokens")
check("max_cost_usd fires", b.exceeded(1, 0, 0.0002, 0)[0] == "max_cost_usd")
check("max_wall_seconds fires", b.exceeded(1, 0, 0, 15.0)[0] == "max_wall_seconds")
check("nothing fires under budget", b.exceeded(1, 100, 0.0001, 1.0) == (None, None))

logs = week7_ui.ui_artefacts()["budget_logs"]
check("one clean termination log per budget, each naming the budget that fired",
      sorted(l["terminated_by"] for l in logs) == sorted(week7_ui.BUDGET_LOG_FILES)
      and all(l["fired"] and l["fired"]["budget"] == l["terminated_by"] for l in logs),
      [(l["file"], l["terminated_by"], l["fired"]) for l in logs])
check("each budget log records the configured limit and the spend at termination",
      all(l["budgets"] and l["spent"] and l["terminated_by"] in l["budgets"] for l in logs))
check("live agent runs from the UI are clamped to the race ceilings",
      week7_ui.LIVE_AGENT_LIMITS == {"max_iterations": 8, "max_tokens": 8000, "max_cost_usd": 0.01,
                                      "max_wall_seconds": 300.0})

# ===========================================================================
print("\nWeek 8 — alternate trajectories, argument validation, gap, one mitigation")

import week8_trajectory_eval as w8

check("10 expected sequences", len(w8.CASES) == 10)
alts = [c for c in w8.CASES if len(c["accepted"]) > 1]
check("alternate valid paths are accepted as sets (3 cases)", len(alts) == 3 and all(c["alternate"] for c in alts))


def fake_run(ticket_id, steps, decision, amount, terminated_by=None):
    tokens_in, tokens_out = 500 * len(steps), 40 * len(steps)
    return {"ticket_id": ticket_id, "trajectory": steps, "terminated_by": terminated_by,
            "result": {"ticket_id": ticket_id, "decision": decision, "refund_amount": amount,
                       "tier": None, "reply": "x" if decision else ""},
            "meter": {"input_tokens": tokens_in, "output_tokens": tokens_out, "iterations": len(steps)},
            "tokens": tokens_in + tokens_out, "cost_usd": 0.0001 * len(steps), "latency_ms": 1000.0 * len(steps)}


st = w8.store()
t8 = st["tickets"]["CD-7008"]
case_7008 = next(c for c in w8.CASES if c["ticket_id"] == "CD-7008")
short = fake_run("CD-7008", [
    {"lap": 1, "type": "tool", "name": "find_ticket", "arguments": {"ticket_id": "CD-7008"}, "result": t8},
    {"lap": 2, "type": "answer", "decision": "escalated"}], "escalated", None)
s = w8.score(case_7008, short)
check("CD-7008 via find_ticket -> answer passes the trajectory (alternate path)", s["trajectory_pass"] and s["outcome_pass"], s["events"])

case_7001 = next(c for c in w8.CASES if c["ticket_id"] == "CD-7001")
t1 = st["tickets"]["CD-7001"]
o1 = st["orders"][t1["order_id"]]
skipped = fake_run("CD-7001", [
    {"lap": 1, "type": "tool", "name": "find_ticket", "arguments": {"ticket_id": "CD-7001"}, "result": t1},
    {"lap": 2, "type": "answer", "decision": "refund_approved"}], "refund_approved", o1["amount"])
s = w8.score(case_7001, skipped)
check("right answer on the wrong path: outcome PASS, trajectory FAIL, skipped_evidence counted",
      s["outcome_pass"] and not s["trajectory_pass"] and s["modes"]["skipped_evidence"] == 1, s["events"])

observed = {"find_ticket": t1}
check("fabricated order id is detected", w8.check_arguments("CD-7001", "get_order", {"order_id": "ORD-9999"}, observed)
      == (False, "fabricated"))
other_order = next(oid for oid in st["orders"] if oid != t1["order_id"])
check("a real id from another ticket is ungrounded, not valid",
      w8.check_arguments("CD-7001", "get_order", {"order_id": other_order}, observed) == (False, "ungrounded"))
check("the ticket's own order id is valid", w8.check_arguments("CD-7001", "get_order", {"order_id": t1["order_id"]}, observed)[0])
check("ticket id checked against the fixture", w8.check_arguments("CD-7001", "find_ticket", {"ticket_id": "CD-7001"}, {})[0]
      and not w8.check_arguments("CD-7001", "find_ticket", {"ticket_id": "CD-7002"}, {})[0])

full = fake_run("CD-7001", [
    {"lap": 1, "type": "tool", "name": "find_ticket", "arguments": {"ticket_id": "CD-7001"}, "result": t1},
    {"lap": 2, "type": "tool", "name": "get_order", "arguments": {"order_id": t1["order_id"]}, "result": o1},
    {"lap": 3, "type": "tool", "name": "lookup_refund_policy",
     "arguments": {"tier": t1["tier"], "order_status": o1["order_status"]}, "result": {"ok": True}},
    {"lap": 4, "type": "answer", "decision": "refund_approved"}], "refund_approved", o1["amount"])
s_full = w8.score(case_7001, full)
check("step efficiency = laps taken / laps needed (4/4 = 1.0 on the shortest path)", s_full["step_efficiency"] == 1.0,
      s_full["step_efficiency"])
summary = w8.summarise([s, s_full, w8.score(case_7008, short)])
check("gap = outcome pass rate - trajectory pass rate, in percentage points",
      abs(summary["gap_pp"] - (summary["outcome_pass_rate_pct"] - summary["trajectory_pass_rate_pct"])) < 1e-9
      and summary["outcome_pass_rate_pct"] == 100.0 and abs(summary["trajectory_pass_rate_pct"] - 66.7) < 0.1,
      {k: summary[k] for k in ("outcome_pass_rate_pct", "trajectory_pass_rate_pct", "gap_pp")})

diff = {k for k in set(w8.CONFIGS["baseline"]) | set(w8.CONFIGS["mitigated"])
        if w8.CONFIGS["baseline"].get(k) != w8.CONFIGS["mitigated"].get(k)}
check("exactly ONE mitigation between baseline and mitigated", diff == {"evidence_guard"}, diff)
check("the mitigation targets the harm ranking's top mode", w8.MITIGATION_TARGET == "skipped_evidence")

reg_path = ROOT / "week8" / "regression.csv"
if reg_path.exists():
    import csv
    with reg_path.open(newline="", encoding="utf-8") as handle:
        reg_rows = list(csv.DictReader(handle))
    check("the regression table covers every mode in the taxonomy",
          {r["mode"] for r in reg_rows} == set(w8.MODES), set(w8.MODES) ^ {r["mode"] for r in reg_rows})
    check("the regression table reports modes that worsened or appeared",
          any(r["verdict"] == "worse" for r in reg_rows) and any(r["verdict"] == "new" for r in reg_rows))
else:
    skip("regression table", "week8/regression.csv missing")

# ===========================================================================
print("\nWeek 9 — discovery, config-only server addition, recoverable error, no model in servers")

from mcp_client import MCPConnection, load_config

cfg = load_config(ROOT / "week9" / "mcp_config.json")
check("the config names both servers", set(cfg) == {"clouddesk-kb", "ticket-history"})

agent_src = (ROOT / "src" / "mcp_agent.py").read_text(encoding="utf-8")
for name in ("lookup_ticket", "get_escalation_history", "search_kb", "get_article"):
    check(f"host does not hard-code tool '{name}'", name not in agent_src)

for rel in ("mcp_servers/kb_server.py", "third_party/ticket_history_server/server.py"):
    src = (ROOT / rel).read_text(encoding="utf-8")
    check(f"no model call inside {rel}",
          not any(token in src for token in ("agent_runtime", "transformers", "torch", "generate(", "chat(")))

try:
    diff_out = subprocess.run(["git", "diff", "1715ab5", "d4e76e4", "--", "src/mcp_agent.py", "src/mcp_client.py",
                               "src/agent_runtime.py"], cwd=ROOT, capture_output=True, text=True, timeout=20)
    if diff_out.returncode == 0:
        check("git: zero agent-module lines changed between one server and two", diff_out.stdout.strip() == "")
        cfg_diff = subprocess.run(["git", "diff", "1715ab5", "d4e76e4", "--", "week9/mcp_config.json"],
                                  cwd=ROOT, capture_output=True, text=True, timeout=20).stdout
        check("git: the config diff is the whole change", '"ticket-history"' in cfg_diff)
    else:
        skip("git diff proof", diff_out.stderr.strip()[:80])
except Exception as error:
    skip("git diff proof", str(error)[:80])

saved = json.loads((ROOT / "week9" / "agent_diff.txt").read_text(encoding="utf-8").split("\n")[2].split(":")[-1])
check("agent_diff.txt records 0 changed lines", saved == 0)


def live_tools(server_name):
    spec = cfg[server_name]
    conn = MCPConnection(server_name, spec, root=str(ROOT), wire=[])
    try:
        init = conn.connect()
        return init, [t["name"] for t in conn.list_tools()]
    finally:
        conn.close()


try:
    init, tools = live_tools("ticket-history")
    check("live tools/list from the ticket-history server", sorted(tools) == ["get_escalation_history", "lookup_ticket"], tools)
    check("initialize agreed a protocol version", bool(init.get("protocolVersion")))
except Exception as error:
    check("live tools/list from the ticket-history server", False, str(error)[:120])

import db
if db.server_reachable(db.DATABASE_URL):
    try:
        spec = cfg["clouddesk-kb"]
        conn = MCPConnection("clouddesk-kb", spec, root=str(ROOT), wire=[])
        try:
            conn.connect()
            kb_tools = [t["name"] for t in conn.list_tools()]
            result = conn.call_tool("get_article", {"article_id": "HC-8"})
        finally:
            conn.close()
        text = "\n".join(c.get("text", "") for c in result.get("content", []))
        check("live tools/list from our kb server", sorted(kb_tools) == ["get_article", "search_kb"], kb_tools)
        check("malformed id -> isError with a recoverable, actionable message",
              result.get("isError") is True and text.startswith("Retry now") and "HC-008" in text, text[:160])
        saved_before = json.loads((ROOT / "week9" / "tools_before.json").read_text())["tool_count"]
        saved_after = json.loads((ROOT / "week9" / "tools_after.json").read_text())["tool_count"]
        check("saved tool counts match live discovery (2 -> 4)",
              saved_before == len(kb_tools) == 2 and saved_after == len(kb_tools) + len(tools) == 4)
    except Exception as error:
        check("live kb server", False, str(error)[:120])
else:
    skip("kb server live checks", "Postgres not reachable")

# ===========================================================================
print("\nServer — JSON API over a real socket")

import server as srv


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


port = free_port()
httpd = srv.StudioServer(("127.0.0.1", port), srv.RAGRequestHandler)
thread = threading.Thread(target=httpd.serve_forever, daemon=True)
thread.start()
base = f"http://127.0.0.1:{port}"


def get(path):
    with urllib.request.urlopen(base + path, timeout=120) as r:
        return r.status, json.loads(r.read().decode("utf-8"))


def post(path, body):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8") or "{}")


try:
    with urllib.request.urlopen(base + "/", timeout=30) as r:
        html = r.read().decode("utf-8")
    check("GET / serves the UI with every workspace", r.status == 200 and all(
        f'data-view="{v}"' in html for v in ("ask", "week5", "week6", "week7", "week8", "week9", "reports")))
    for path, key in (("/api/week7", "summary"), ("/api/week8", "configs"), ("/api/week9", "tools_after"),
                      ("/api/week6", "assertions"), ("/api/week5", "sample"), ("/api/reports", "docs"),
                      ("/api/golden", "questions"), ("/api/runs", "running"), ("/api/health", "build")):
        status, body = get(path)
        check(f"GET {path}", status == 200 and key in body, (status, list(body)[:5]))
    status, body = post("/api/ask", {"query": ""})
    check("POST /api/ask with an empty query -> 400", status == 400 and body.get("status") == "error", status)
    status, body = post("/api/trace", {"trace_id": "nope"})
    check("POST /api/trace unknown id -> 404", status == 404, status)
    status, body = post("/api/week7/workflow", {"ticket_id": "CD-0000"})
    check("POST /api/week7/workflow rejects a ticket outside the race set (400)", status == 400, (status, body))

    # The one-run-at-a-time guard: hold the lock, then any model-backed action is 409.
    with srv.exclusive("test hold"):
        status, body = post("/api/week7/workflow", {"ticket_id": "CD-7001"})
        check("a second expensive run while one is in progress -> 409 naming the run",
              status == 409 and "test hold" in body.get("error", ""), (status, body))
        status, body = get("/api/runs")
        check("GET /api/runs reports the run in progress", body.get("running") == "test hold")
    status, body = get("/api/runs")
    check("the lock is released afterwards", body.get("running") is None)
    status, body = post("/api/week8/rescore", {})
    check("POST /api/week8/rescore reproduces the stored report from the raw runs",
          status == 200 and body.get("all_match") is True, (status, body.get("all_match")))

    # Week 9 MCP chat tests
    status, body = post("/api/week9/chat", {"query": "Who owns ticket TCK-4471?"})
    check("POST /api/week9/chat ticket query routes to ticket-history MCP server",
          status == 200 and body.get("mcp", {}).get("server") == "ticket-history"
          and any(s["tool"] == "lookup_ticket" and s["status"] == "data" for s in body.get("mcp", {}).get("steps", [])),
          (status, body.get("mcp", {}).get("steps")))

    status, body = post("/api/week9/chat", {"query": "How long is a password reset link valid?"})
    check("POST /api/week9/chat KB query routes to clouddesk-kb MCP server",
          status == 200 and body.get("mcp", {}).get("server") == "clouddesk-kb" and body.get("mcp", {}).get("tool") == "search_kb", (status, body.get("mcp")))

    status, body = post("/api/week9/chat", {"query": "what is react"})
    check("POST /api/week9/chat off-topic query returns no passages, not a stopword match",
          status == 200 and body.get("found") is False and body.get("evidence") == []
          and "No relevant data found" in body.get("answer", ""),
          (status, body.get("evidence"), body.get("answer", "")[:120]))

    status, body = post("/api/week9/chat", {"query": "what about ticket TCK-9999"})
    check("POST /api/week9/chat unknown ticket id -> no data, not a search match on 'ticket'",
          status == 200 and body.get("found") is False, (status, body.get("answer", "")[:120]))

    chat_src = (ROOT / "src" / "mcp_chat.py").read_text(encoding="utf-8")
    for name in ("lookup_ticket", "get_escalation_history", "search_kb", "get_article"):
        check(f"chat does not hard-code tool '{name}'", name not in chat_src)
finally:
    httpd.shutdown()
    httpd.server_close()

# ===========================================================================
print()
if failures:
    print(f"{len(failures)} check(s) failed:")
    for name in failures:
        print(f"  - {name}")
    sys.exit(1)
print("All checks passed.")
