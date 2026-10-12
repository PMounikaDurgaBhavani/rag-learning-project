"""Week 7 data for the UI's "Agent vs workflow" view, plus the two live actions.

Everything in ui_artefacts() is read from week7/ (the race CSVs, the stored
run JSON with every transcript, the budget logs, the verdict, the tool
description diff and probe). Nothing is computed from scratch here, so the
view shows exactly the files that are submitted.

The live actions run ONE ticket through one system and write their output
under week7/runs/live/ — never over race.csv, race_summary.csv or the race
runs. They load Qwen2.5-1.5B into the UI process (~3 GB on MPS), so the
server serialises them behind its model lock.

    run_workflow("CD-7007")                         # three fixed steps, ~4 s warm
    run_agent("CD-7001", max_iterations=2, ...)     # the loop under a small budget
"""

import csv
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
W7 = ROOT / "week7"
RUNS = W7 / "runs"
LIVE = RUNS / "live"
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "experiments"))

BUDGET_LOG_FILES = ("max_iterations", "max_tokens", "max_cost_usd", "max_wall_seconds")

# Hard ceilings for a live agent run started from the browser. The race used
# 8 laps / 8000 tokens / $0.01 / 300 s per ticket and took about an hour for
# ten tickets; a demo run must not be able to ask for more than one race lap.
LIVE_AGENT_LIMITS = {"max_iterations": 8, "max_tokens": 8000, "max_cost_usd": 0.01,
                     "max_wall_seconds": 300.0}

WORKFLOW_STEP = re.compile(r"^\[workflow\] step \d+: (\w+)\(")
AGENT_TOOL = re.compile(r"^\[agent\]\s+tool (\w+)\(")


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _text(path):
    return path.read_text(encoding="utf-8") if path.exists() else None


def _json(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _csv(path):
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _num(value):
    """CSV cells back to numbers where they are numbers."""
    if value is None or value == "":
        return None
    if value in ("True", "False"):
        return value == "True"
    try:
        return int(value)
    except ValueError:
        try:
            return float(value)
        except ValueError:
            return value


def _rows(path):
    return [{k: _num(v) for k, v in row.items()} for row in _csv(path)]


def race_set():
    return [json.loads(line) for line in (_text(W7 / "race_set.jsonl") or "").splitlines()
            if line.strip()]


def tool_path(log_lines):
    """The tools a run actually called, in order, read from its own transcript."""
    path = []
    for line in log_lines or []:
        match = WORKFLOW_STEP.match(line) or AGENT_TOOL.match(line)
        if match:
            path.append(match.group(1))
    return path


def parse_budget_log(path):
    """The header a budget-demo run writes: command, budgets, what fired, what was spent."""
    text = _text(path)
    if text is None:
        return None
    header, body = {}, []
    for line in text.splitlines():
        if line.startswith("# "):
            captured = re.search(r"captured (\S+)", line)
            if captured and "budget termination" in line:
                header["captured"] = captured.group(1)
                continue
            key, _, value = line[2:].partition(":")
            header[key.strip()] = value.strip()
        elif line.startswith("#"):
            continue
        else:
            body.append(line)
    budgets = None
    try:
        budgets = json.loads(header.get("budgets", "")) if header.get("budgets") else None
    except json.JSONDecodeError:
        budgets = None
    fired = None
    for line in body:
        match = re.search(r"BUDGET (\w+) hit \(([^)]*)\)", line)
        if match:
            fired = {"budget": match.group(1), "check": match.group(2), "line": line}
    return {"file": path.relative_to(ROOT).as_posix(), "captured": header.get("captured"),
            "command": header.get("command"), "model": header.get("model"), "budgets": budgets,
            "terminated_by": header.get("terminated_by"), "spent": header.get("spent"),
            "result_at_termination": header.get("result at termination"),
            "fired": fired, "log": [line for line in body if line.strip()]}


def latest_race():
    stored = sorted(RUNS.glob("race_*.json"))
    return stored[-1] if stored else None


def race_history():
    rows = []
    for path in sorted(RUNS.glob("race_*.json")):
        data = _json(path)
        rows.append({"file": path.name, "ran_at": data.get("ran_at"), "model": data.get("model"),
                     "app_version": data.get("app_version"), "budgets": data.get("budgets"),
                     "summary": data.get("summary")})
    return rows


def ui_artefacts():
    cases = race_set()
    by_id = {c["ticket_id"]: c for c in cases}
    latest = latest_race()
    run = _json(latest) if latest else None
    transcripts = {}
    if run:
        for item in run.get("runs", []):
            transcripts[(item["system"], item["ticket_id"])] = item

    rows = _rows(W7 / "race.csv")
    for row in rows:
        item = transcripts.get((row["system"], row["ticket_id"]))
        row["log"] = item.get("log", []) if item else []
        row["result"] = item.get("result") if item else None
        row["meter"] = item.get("meter") if item else None
        row["path"] = tool_path(row["log"])

    # Branching: did the path actually change with the input or a tool result?
    branching = []
    for case in cases:
        wf = next((r for r in rows if r["system"] == "workflow" and r["ticket_id"] == case["ticket_id"]), None)
        ag = next((r for r in rows if r["system"] == "agent" and r["ticket_id"] == case["ticket_id"]), None)
        branching.append({
            "ticket_id": case["ticket_id"], "class": case["class"],
            "branch_after_step2": case["branch_after_step2"], "why": case["why"],
            "expected": case["expected"],
            "workflow_path": wf["path"] if wf else [], "workflow_pass": wf["pass"] if wf else None,
            "workflow_decision": wf["decision"] if wf else None,
            "workflow_tool_errors": wf["tool_errors"] if wf else None,
            "agent_path": ag["path"] if ag else [], "agent_pass": ag["pass"] if ag else None,
            "agent_terminated_by": ag["terminated_by"] if ag else None,
        })
    straight = [b["workflow_path"] for b in branching if not b["branch_after_step2"]]
    straight_path = straight[0] if straight else []
    for b in branching:
        b["workflow_path_differs_from_straight_line"] = b["workflow_path"] != straight_path

    budget_logs = [parse_budget_log(W7 / "budget_logs" / f"{name}.log") for name in BUDGET_LOG_FILES]

    return {
        "race_set": cases,
        "summary": {r["system"]: r for r in _rows(W7 / "race_summary.csv")},
        "rows": rows,
        "latest_run": {"file": latest.name, "ran_at": run.get("ran_at"), "model": run.get("model"),
                       "app_version": run.get("app_version"), "budgets": run.get("budgets"),
                       "rate_card": run.get("rate_card_usd_per_mtok"),
                       "tickets": run.get("tickets")} if run else None,
        "history": race_history(),
        "budget_120s": {r["system"]: r for r in _rows(W7 / "race_summary_budget120s.csv")},
        "branching": branching,
        "straight_line_path": straight_path,
        "budget_termination": parse_budget_log(W7 / "budget_termination.log"),
        "budget_logs": [b for b in budget_logs if b],
        "verdict_md": _text(W7 / "verdict.md"),
        "verdict_words": len(re.findall(r"\b\w+\b", re.sub(r"\*\(.*?\)\*", "", _text(W7 / "verdict.md") or ""))),
        "tool_diff_md": _text(W7 / "tool_description_diff.md"),
        "tool_probe": {v: {k: val for k, val in (_json(W7 / f"tool_probe_{v}.json") or {}).items() if k != "rows"}
                       for v in ("v1", "v2")},
        "tool_schemas": _tool_schemas(),
        "long_race": {"summary": _rows(W7 / "long_race_summary.csv"),
                      "rows": _rows(W7 / "long_race.csv"),
                      "restart_log": _text(W7 / "memory" / "restart_demo.log")},
        "readme_md": _text(W7 / "README.md"),
        "live_runs": sorted(p.name for p in LIVE.glob("*.json")) if LIVE.exists() else [],
    }


def _tool_schemas():
    from support_tools import TOOL_SCHEMAS
    out = []
    for schema in TOOL_SCHEMAS:
        fn = schema.get("function", schema)
        out.append({"name": fn.get("name"), "description": fn.get("description"),
                    "parameters": fn.get("parameters")})
    return out


# ---------------------------------------------------------------------------
# live actions — one ticket, one system, written under week7/runs/live/
# ---------------------------------------------------------------------------

def _check_ticket(ticket_id):
    ids = [c["ticket_id"] for c in race_set()]
    ticket_id = str(ticket_id or "").strip().upper()
    if ticket_id not in ids:
        raise ValueError(f"ticket must be one of the race set: {', '.join(ids)}")
    return ticket_id


def _save(run, grade_result, system, ticket_id):
    LIVE.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = LIVE / f"{system}_{ticket_id}_{stamp}.json"
    passed, reason = grade_result
    payload = {"ran_at": now_iso(), "source": "ui", "system": system, "ticket_id": ticket_id,
               "pass": passed, "reason": reason, "run": run}
    path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    return path.relative_to(ROOT).as_posix()


def run_workflow(ticket_id):
    """The fixed workflow on one race ticket. Same tools, model and grader as the race."""
    from week7_race import grade
    import agent_runtime
    import ticket_workflow

    ticket_id = _check_ticket(ticket_id)
    expected = next(c for c in race_set() if c["ticket_id"] == ticket_id)["expected"]
    agent_runtime.warm_up()
    run = ticket_workflow.resolve_ticket(ticket_id)
    graded = grade(expected, run["result"])
    saved = _save(run, graded, "workflow", ticket_id)
    return {"system": "workflow", "ticket_id": ticket_id, "expected": expected,
            "pass": graded[0], "reason": graded[1], "path": tool_path(run["log"]),
            "model": agent_runtime.MODEL_NAME, "saved_to": saved, "run": run}


def run_agent(ticket_id, max_iterations=4, max_tokens=4000, max_cost_usd=0.01,
              max_wall_seconds=120.0, evidence_guard=True):
    """The agent loop on one race ticket, under four budgets the caller sets.

    The budgets are clamped to the race's own ceilings, so a browser cannot
    start a run longer than one race lap. Returns the full transcript, what
    terminated it (if a budget did), and the same grade the race applies.
    """
    from week7_race import grade
    from agent_runtime import Budgets
    import agent_runtime
    import ticket_agent

    ticket_id = _check_ticket(ticket_id)
    budgets = Budgets(
        max_iterations=max(1, min(int(max_iterations), LIVE_AGENT_LIMITS["max_iterations"])),
        max_tokens=max(200, min(int(max_tokens), LIVE_AGENT_LIMITS["max_tokens"])),
        max_cost_usd=max(0.0001, min(float(max_cost_usd), LIVE_AGENT_LIMITS["max_cost_usd"])),
        max_wall_seconds=max(5.0, min(float(max_wall_seconds), LIVE_AGENT_LIMITS["max_wall_seconds"])),
    )
    expected = next(c for c in race_set() if c["ticket_id"] == ticket_id)["expected"]
    agent_runtime.warm_up()           # as the race did: the weight load is not the agent's time
    run = ticket_agent.resolve_ticket(ticket_id, budgets=budgets, evidence_guard=bool(evidence_guard))
    graded = grade(expected, run["result"])
    saved = _save(run, graded, "agent", ticket_id)
    fired = None
    for line in run.get("log", []):
        match = re.search(r"BUDGET (\w+) hit \(([^)]*)\)", line)
        if match:
            fired = {"budget": match.group(1), "check": match.group(2), "line": line}
    return {"system": "agent", "ticket_id": ticket_id, "expected": expected,
            "pass": graded[0], "reason": graded[1], "path": tool_path(run["log"]),
            "budgets": budgets.as_dict(), "terminated_by": run.get("terminated_by"),
            "fired": fired, "evidence_guard": bool(evidence_guard),
            "model": agent_runtime.MODEL_NAME, "saved_to": saved, "run": run}
