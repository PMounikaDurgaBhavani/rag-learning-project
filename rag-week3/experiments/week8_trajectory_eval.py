"""Week 8: score the ticket agent's path, not only its answer.

The outcome eval (Week 7's grade()) asks whether the decision and amount were
right. A right refund reached without ever opening the order passes it. This
eval scores the trajectory too, and reports the difference as a number.

    python experiments/week8_trajectory_eval.py run --config baseline
    python experiments/week8_trajectory_eval.py run --config mitigated
    python experiments/week8_trajectory_eval.py report      # rescore stored runs
    python experiments/week8_trajectory_eval.py show CD-7001 --config baseline

Running and scoring are separate. `run` stores every trajectory raw in
week8/runs/; `report` scores the newest stored run of each config. Changing a
scoring rule therefore never needs the model again, and a rule cannot be tuned
against a run without the rule's diff showing up in git.

Configs (exactly one difference between baseline and mitigated):

    baseline    the agent as first built: any well-formed answer is accepted
    mitigated   + the evidence guard (re-planning): an answer whose facts were
                never fetched is sent back with the missing tool named
    guarded     bonus: mitigated + tool-output sanitizing + output guardrail,
                to measure what the injection defences cost on clean tickets
"""

import argparse
import csv
import json
import os
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "experiments"))
os.chdir(ROOT)

from agent_runtime import MODEL_NAME, RATE_CARD, Budgets
from support_tools import TOOL_NAMES, store

W8 = ROOT / "week8"
RUNS = W8 / "runs"
RESULTS_CSV = W8 / "trajectory_results.csv"
SUMMARY_CSV = W8 / "trajectory_summary.csv"
REGRESSION_CSV = W8 / "regression.csv"
REPORT_JSON = W8 / "report.json"

FT, GO, LRP = "find_ticket", "get_order", "lookup_refund_policy"

# ---------------------------------------------------------------------------
# the 10 expected tool sequences
# ---------------------------------------------------------------------------
#
# Each case lists every path that is a correct way to resolve it, as a set.
# One exact sequence per case would score correct runs as failures and inflate
# the gap. The alternates here are optional final steps: once get_order says
# "refunded" or "not found", the decision is already fixed and the policy
# lookup only confirms it. Ticket-before-order is not optional in this domain:
# the order id is only known from the ticket record, so no correct path can
# pull the order first.

CASES = [
    {"ticket_id": "CD-7001", "accepted": [[FT, GO, LRP]], "alternate": None},
    {"ticket_id": "CD-7002", "accepted": [[FT, GO, LRP]], "alternate": None},
    {"ticket_id": "CD-7003", "accepted": [[FT, GO, LRP]], "alternate": None},
    {"ticket_id": "CD-7004", "accepted": [[FT, GO, LRP]], "alternate": None},
    {"ticket_id": "CD-7005", "accepted": [[FT, GO, LRP], [FT, GO]],
     "alternate": "order_status 'refunded' decides it; the policy lookup only confirms"},
    {"ticket_id": "CD-7006", "accepted": [[FT, GO, LRP]], "alternate": None},
    {"ticket_id": "CD-7007", "accepted": [[FT, GO, LRP], [FT, GO]],
     "alternate": "get_order returning 'not found' decides it; the policy lookup only confirms"},
    {"ticket_id": "CD-7008", "accepted": [[FT, LRP], [FT]],
     "alternate": "no order id on the ticket: get_order has nothing real to take, and the "
                  "policy lookup for 'missing' only confirms the escalation"},
    {"ticket_id": "CD-7009", "accepted": [[FT, GO, LRP]], "alternate": None},
    {"ticket_id": "CD-7010", "accepted": [[FT, GO, LRP]], "alternate": None},
]

CONFIGS = {
    "baseline": {"evidence_guard": False},
    "mitigated": {"evidence_guard": True},
    # Bonus, not the mitigation: the injection defences on top of mitigated,
    # re-run over the same 10 tickets to price them.
    "guarded": {"evidence_guard": True, "sanitize": True, "output_guardrail": True},
}

# Laps, tokens and cost decide a run here; the wall clock is enforced but set
# out of the way. Under greedy decoding the first three are deterministic, and
# the wall clock is not: the identical first lap of CD-7005 took 5.9 s in one
# run and 323.9 s in the next (machine stall), and at 300 s that turned ten
# tickets into budget terminations the mitigation did not cause. Those two
# confounded runs are kept in week8/runs/confounded/.
BUDGETS = {"max_iterations": 8, "max_tokens": 8000, "max_cost_usd": 0.01,
           "max_wall_seconds": 1800.0}

# The Week 8 failure-mode zoo, as detectors over one trajectory.
MODES = {
    "skipped_evidence": "shipped an answer while a required tool was never called (e.g. never opened the order)",
    "skipped_evidence_caught": "tried to answer before the path was complete; the evidence guard sent it back",
    "fabricated_argument": "an id argument that exists nowhere: not in the store, not on the ticket",
    "ungrounded_argument": "a real-looking argument that disagrees with what the tools returned",
    "out_of_order": "a tool called before the tool that supplies its inputs",
    "redundant_call": "the same tool with the same arguments again",
    "unknown_tool": "a tool name that does not exist",
    "prose_instead_of_action": "a lap with neither a tool call nor a parseable answer",
    "budget_exhausted": "the run ended on a budget, not an answer",
    "wrong_decision": "a final decision or amount that the outcome eval fails",
}


# The one mitigation, as the report and the UI describe it.
MITIGATION = (
    "**Re-planning on unsupported answers** (`evidence_guard=True`, "
    "`ticket_agent.unsupported_by_evidence`). When the model answers before the tools "
    "that answer depends on have returned, the loop does not accept it: it sends the "
    "answer back naming the missing tool (\"You have not looked up order ORD-5001 yet. "
    "Call get_order…\"), at most twice. Nothing else differs from the baseline: same "
    "model, prompt, tool descriptions, budgets and tickets."
)

MITIGATION_TARGET = "skipped_evidence"

# The right-answer-wrong-path ticket the write-up names, and the run it is in.
GAP_CASE = "CD-7010"
GAP_CONFIG = "week7_race1"

# Stored runs that are scored but never re-run: Week 7 race 1, the unguarded
# agent as it actually ran, rebuilt from its logs by `import-week7`.
HISTORICAL = ["week7_race1"]
WEEK7_RACE1 = ROOT / "week7" / "runs" / "race_20260921-115626.json"


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def expected_of(ticket_id):
    for line in (ROOT / "week7" / "race_set.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            if row["ticket_id"] == ticket_id:
                return row
    raise KeyError(ticket_id)


# ---------------------------------------------------------------------------
# argument grounding
# ---------------------------------------------------------------------------

def check_arguments(ticket_id, name, arguments, observed):
    """(valid, kind) for one call. kind is None, 'fabricated' or 'ungrounded'.

    Grounded means the value is the one the tools already returned for this
    ticket — not merely a value that looks right. ORD-5001 is a real order,
    and still a wrong argument on ticket CD-7101.
    """
    data = store()
    ticket = data["tickets"].get(ticket_id, {})
    arguments = arguments if isinstance(arguments, dict) else {}

    if name == FT:
        value = str(arguments.get("ticket_id") or "").strip().upper()
        if value == ticket_id:
            return True, None
        return False, "ungrounded" if value in data["tickets"] else "fabricated"

    if name == GO:
        value = str(arguments.get("order_id") or "").strip().upper()
        wanted = (ticket.get("order_id") or "").upper()
        if wanted and value == wanted:
            return True, None        # the ticket's own id, even if no such order exists
        if not value:
            return False, "fabricated"
        known = value in data["orders"] or any(
            (t.get("order_id") or "").upper() == value for t in data["tickets"].values())
        return False, "ungrounded" if known else "fabricated"

    if name == LRP:
        tier = str(arguments.get("tier") or "").strip().lower()
        status = str(arguments.get("order_status") or "").strip().lower()
        want_tier = (observed.get(FT) or {}).get("tier")
        order = observed.get(GO)
        if order is not None:
            want_status = order.get("order_status")
        elif not ticket.get("order_id"):
            want_status = "missing"
        else:
            want_status = None       # the order was never fetched: nothing to ground on
        if want_tier is None or want_status is None:
            return False, "ungrounded"
        if tier == want_tier and status == want_status:
            return True, None
        return False, "ungrounded"

    return False, "fabricated"


# ---------------------------------------------------------------------------
# scoring one trajectory
# ---------------------------------------------------------------------------

def score(case, run):
    """Every trajectory number and every failure mode for one ticket."""
    from week7_race import grade

    ticket_id = case["ticket_id"]
    accepted = case["accepted"]
    expected = expected_of(ticket_id)["expected"]
    outcome_pass, outcome_reason = grade(expected, run["result"])

    done = []                   # distinct tools completed with valid arguments, in order
    observed = {}               # tool -> result of its valid call
    seen_calls = set()
    choices = correct_choices = 0
    calls = valid_calls = 0
    modes = {mode: 0 for mode in MODES}
    events = []
    final_complete = None       # was the path complete when the accepted answer was given?

    def consistent_next():
        return {seq[len(done)] for seq in accepted
                if seq[:len(done)] == done and len(seq) > len(done)}

    for step in run["trajectory"]:
        kind = step["type"]
        if kind == "tool":
            name, arguments = step["name"], step.get("arguments") or {}
            calls += 1
            choices += 1
            right_tool = name in consistent_next()
            correct_choices += right_tool

            if name not in TOOL_NAMES:
                modes["unknown_tool"] += 1
                events.append(f"lap {step['lap']}: unknown tool {name}")
                continue

            key = (name, json.dumps(arguments, sort_keys=True))
            if key in seen_calls:
                modes["redundant_call"] += 1
                events.append(f"lap {step['lap']}: {name} repeated with the same arguments")
            seen_calls.add(key)

            if name == LRP and GO not in done and any(GO in seq for seq in accepted) \
                    and store()["tickets"].get(ticket_id, {}).get("order_id"):
                modes["out_of_order"] += 1
                events.append(f"lap {step['lap']}: {LRP} before {GO}")

            valid, why = check_arguments(ticket_id, name, arguments, observed)
            if valid:
                valid_calls += 1
                observed[name] = step.get("result") or {}
                if name not in done:
                    done.append(name)
            else:
                mode = "fabricated_argument" if why == "fabricated" else "ungrounded_argument"
                modes[mode] += 1
                events.append(f"lap {step['lap']}: {name}({json.dumps(arguments)}) — {why}")

        elif kind in ("answer", "answer_rejected"):
            choices += 1
            complete = done in accepted
            correct_choices += complete
            if kind == "answer":
                final_complete = complete
            if not complete:
                # A caught attempt is the guard working: counted, shown in the
                # regression table, but only a shipped one fails the path.
                # (Scoring rule split on 2026-10-06 after the mitigated run;
                # the strict rate below keeps the original rule.)
                modes["skipped_evidence_caught" if kind == "answer_rejected"
                      else "skipped_evidence"] += 1
                missing = sorted({tool for seq in accepted for tool in seq} - set(done))
                events.append(f"lap {step['lap']}: answered {step.get('decision')} without "
                              f"{', '.join(missing) or 'a complete path'}"
                              + (" (rejected by guard)" if kind == "answer_rejected" else ""))

        elif kind == "prose":
            choices += 1
            modes["prose_instead_of_action"] += 1

    if run["terminated_by"]:
        modes["budget_exhausted"] += 1
    if run["result"]["decision"] is not None and not outcome_pass:
        modes["wrong_decision"] += 1

    answered = any(step["type"] == "answer" for step in run["trajectory"])
    # The path passes when it ends in an answer, every distinct tool it
    # completed forms one accepted sequence, and no call along the way
    # invented, mis-grounded or mis-ordered anything. Repeats are allowed here
    # and cost step efficiency instead.
    trajectory_pass = (answered and done in accepted
                       and not any(modes[m] for m in ("fabricated_argument", "ungrounded_argument",
                                                      "out_of_order", "unknown_tool",
                                                      "skipped_evidence")))

    # Which mode cost this ticket its outcome. One per failed ticket, so the
    # harm ranking counts outcomes lost, not events.
    harm = None
    if not outcome_pass:
        if not answered:
            harm = "budget_exhausted" if run["terminated_by"] else "prose_instead_of_action"
        elif final_complete is False:
            harm = "skipped_evidence"
        elif modes["fabricated_argument"]:
            harm = "fabricated_argument"
        elif modes["ungrounded_argument"]:
            harm = "ungrounded_argument"
        else:
            harm = "wrong_decision"

    laps_needed = min(len(seq) for seq in accepted) + 1          # + the answer lap
    meter = run["meter"]
    return {
        "ticket_id": ticket_id,
        "alternate_paths": len(accepted) > 1,
        "outcome_pass": outcome_pass,
        "outcome_reason": "ok" if outcome_pass else outcome_reason,
        "trajectory_pass": trajectory_pass,
        "trajectory_pass_strict": trajectory_pass and not modes["skipped_evidence_caught"],
        "path": " > ".join(step["name"] if step["type"] == "tool" else step["type"]
                           for step in run["trajectory"]),
        "completed_tools": " > ".join(done),
        "decision": run["result"]["decision"],
        "tool_choices": choices,
        "correct_tool_choices": correct_choices,
        "tool_calls": calls,
        "valid_argument_calls": valid_calls,
        "laps": meter["iterations"],
        "laps_needed": laps_needed,
        "step_efficiency": round(meter["iterations"] / laps_needed, 2),
        "tokens": run["tokens"],
        "cost_usd": round(run["cost_usd"], 6),
        "latency_ms": run["latency_ms"],
        "terminated_by": run["terminated_by"] or "",
        "modes": modes,
        "harm": harm,
        "events": events,
    }


def summarise(scored):
    def pct(a, n):
        return round(100.0 * a / n, 1) if n else None

    n = len(scored)
    choices = sum(s["tool_choices"] for s in scored)
    calls = sum(s["tool_calls"] for s in scored)
    costs = [s["cost_usd"] for s in scored]
    tokens = [s["tokens"] for s in scored]
    outcome = sum(s["outcome_pass"] for s in scored)
    trajectory = sum(s["trajectory_pass"] for s in scored)
    return {
        "n": n,
        "outcome_pass_rate_pct": pct(outcome, n),
        "trajectory_pass_rate_pct": pct(trajectory, n),
        "gap_pp": round(pct(outcome, n) - pct(trajectory, n), 1),
        "trajectory_pass_rate_strict_pct": pct(sum(s["trajectory_pass_strict"] for s in scored), n),
        "gap_strict_pp": round(pct(outcome, n)
                               - pct(sum(s["trajectory_pass_strict"] for s in scored), n), 1),
        "right_answer_wrong_path": sum(s["outcome_pass"] and not s["trajectory_pass"] for s in scored),
        "tool_choice_accuracy_pct": pct(sum(s["correct_tool_choices"] for s in scored), choices),
        "argument_validity_pct": pct(sum(s["valid_argument_calls"] for s in scored), calls),
        "step_efficiency_mean": round(statistics.mean(s["step_efficiency"] for s in scored), 2),
        "step_efficiency_max": max(s["step_efficiency"] for s in scored),
        "cost_p50_usd": round(statistics.median(costs), 6),
        "cost_max_usd": max(costs),
        "cost_mean_usd": round(statistics.mean(costs), 6),
        "tokens_p50": statistics.median(tokens),
        "tokens_max": max(tokens),
        "latency_p50_ms": round(statistics.median(s["latency_ms"] for s in scored), 1),
        "latency_max_ms": max(s["latency_ms"] for s in scored),
        "tool_calls": calls,
        "tool_choices": choices,
        "budget_terminations": sum(1 for s in scored if s["terminated_by"]),
        "mode_tickets": {m: sum(1 for s in scored if s["modes"][m]) for m in MODES},
        "mode_events": {m: sum(s["modes"][m] for s in scored) for m in MODES},
        "mode_harm": {m: sum(1 for s in scored if s["harm"] == m) for m in MODES},
    }


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------

def latest_run(config):
    runs = sorted(RUNS.glob(f"traj_{config}_*.json"))
    return runs[-1] if runs else None


def run(args):
    import agent_runtime
    import ticket_agent

    config = CONFIGS[args.config]
    budgets = Budgets(**BUDGETS)
    tickets = args.tickets or [c["ticket_id"] for c in CASES]
    agent_runtime.warm_up()
    print(f"Week 8 trajectory eval · config {args.config} {config} · {MODEL_NAME}")

    runs = []
    for ticket_id in tickets:
        result = ticket_agent.resolve_ticket(ticket_id, budgets=budgets, **config)
        runs.append(result)
        print(f"  {ticket_id} laps={result['meter']['iterations']} tokens={result['tokens']} "
              f"decision={result['result']['decision']} terminated={result['terminated_by']}")

    RUNS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = RUNS / f"traj_{args.config}_{stamp}.json"
    path.write_text(json.dumps({
        "ran_at": now_iso(), "config": args.config, "settings": config, "model": MODEL_NAME,
        "budgets": budgets.as_dict(), "rate_card_usd_per_mtok": RATE_CARD, "runs": runs,
    }, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"Wrote {path.relative_to(ROOT)}")
    return report(args)


def scored_config(config):
    path = latest_run(config)
    if path is None:
        return None, None
    data = json.loads(path.read_text(encoding="utf-8"))
    by_ticket = {r["ticket_id"]: r for r in data["runs"]}
    scored = [score(case, by_ticket[case["ticket_id"]]) for case in CASES
              if case["ticket_id"] in by_ticket]
    return path, scored


def report(args=None):
    W8.mkdir(parents=True, exist_ok=True)
    out = {"generated_at": now_iso(), "cases": CASES, "modes": MODES, "configs": {}}

    rows = []
    for config in list(CONFIGS) + HISTORICAL:
        path, scored = scored_config(config)
        if not scored:
            continue
        summary = summarise(scored)
        out["configs"][config] = {"run": path.relative_to(ROOT).as_posix(),
                                  "summary": summary, "tickets": scored}
        for s in scored:
            rows.append({"config": config, **{k: v for k, v in s.items()
                                              if k not in ("modes", "events")},
                         **{f"mode:{m}": s["modes"][m] for m in MODES}})
        print_summary(config, summary, scored)

    if not rows:
        print("No stored runs. Run: python experiments/week8_trajectory_eval.py run --config baseline")
        return 1

    with open(RESULTS_CSV, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    keys = ["outcome_pass_rate_pct", "trajectory_pass_rate_pct", "gap_pp",
            "trajectory_pass_rate_strict_pct", "gap_strict_pp",
            "right_answer_wrong_path", "tool_choice_accuracy_pct", "argument_validity_pct",
            "step_efficiency_mean", "step_efficiency_max", "cost_p50_usd", "cost_max_usd",
            "cost_mean_usd", "tokens_p50", "tokens_max", "latency_p50_ms", "latency_max_ms",
            "budget_terminations"]
    with open(SUMMARY_CSV, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["config"] + keys)
        for config, block in out["configs"].items():
            writer.writerow([config] + [block["summary"][k] for k in keys])

    if {"baseline", "mitigated"} <= set(out["configs"]):
        before = out["configs"]["baseline"]["summary"]
        after = out["configs"]["mitigated"]["summary"]
        regression = []
        for mode in MODES:
            b_t, a_t = before["mode_tickets"][mode], after["mode_tickets"][mode]
            b_e, a_e = before["mode_events"][mode], after["mode_events"][mode]
            verdict = ("better" if (a_t, a_e) < (b_t, b_e) and a_t <= b_t and a_e <= b_e
                       else "worse" if a_t > b_t or a_e > b_e
                       else "same")
            if b_t == 0 and b_e == 0 and (a_t or a_e):
                verdict = "new"
            regression.append({"mode": mode, "tickets_before": b_t, "tickets_after": a_t,
                               "events_before": b_e, "events_after": a_e, "verdict": verdict})
        out["regression"] = regression
        with open(REGRESSION_CSV, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(regression[0]))
            writer.writeheader()
            writer.writerows(regression)
        # Two rankings, both published. By frequency the top mode is whatever
        # touches most tickets; by harm it is whatever cost most outcomes.
        # The mitigation targets the harm ranking's top: a mode the loop
        # already recovers from (prose -> re-prompt) is common but cheap.
        by_frequency = sorted(MODES, key=lambda m: (-before["mode_tickets"][m],
                                                    -before["mode_events"][m]))
        by_harm = sorted(MODES, key=lambda m: (-before["mode_harm"][m],
                                               -before["mode_tickets"][m]))
        top = by_harm[0]
        if top != MITIGATION_TARGET:
            print(f"WARNING: the harm ranking's top mode is {top}, but the mitigation "
                  f"targets {MITIGATION_TARGET}")
        out["mitigation"] = {
            "top_mode": top,
            "target": MITIGATION_TARGET,
            "ranking_by_frequency": [(m, before["mode_tickets"][m]) for m in by_frequency],
            "ranking_by_harm": [(m, before["mode_harm"][m]) for m in by_harm],
            "before_tickets": before["mode_tickets"][top], "after_tickets": after["mode_tickets"][top],
            "before_events": before["mode_events"][top], "after_events": after["mode_events"][top],
            "price": {f"{k}_{when}": block[k] for k in ("cost_p50_usd", "cost_max_usd",
                                                        "cost_mean_usd", "tokens_p50", "tokens_max",
                                                        "latency_p50_ms", "latency_max_ms")
                      for when, block in (("before", before), ("after", after))},
            "description": MITIGATION,
        }
        # Short aliases the UI reads.
        price = out["mitigation"]["price"]
        for k in ("cost_p50", "cost_max"):
            price[f"{k}_before"] = price[f"{k}_usd_before"]
            price[f"{k}_after"] = price[f"{k}_usd_after"]
        for k in ("latency_p50", "tokens_p50"):
            suffix = "_ms" if k.startswith("latency") else ""
            price[f"{k}_before"] = price[f"{k}{suffix}_before"]
            price[f"{k}_after"] = price[f"{k}{suffix}_after"]
        print(f"\nTOP MODE (baseline): {top}  tickets {before['mode_tickets'][top]} -> "
              f"{after['mode_tickets'][top]}, events {before['mode_events'][top]} -> "
              f"{after['mode_events'][top]}")
        print(f"PRICE: cost p50 ${before['cost_p50_usd']:.6f} -> ${after['cost_p50_usd']:.6f}, "
              f"max ${before['cost_max_usd']:.6f} -> ${after['cost_max_usd']:.6f}; "
              f"latency p50 {before['latency_p50_ms']/1000:.1f}s -> {after['latency_p50_ms']/1000:.1f}s; "
              f"tokens p50 {before['tokens_p50']} -> {after['tokens_p50']}")

        print("\nREGRESSION — per-mode, tickets (events) before -> after")
        for r in regression:
            print(f"  {r['mode']:<26} {r['tickets_before']:>2} ({r['events_before']:>2}) -> "
                  f"{r['tickets_after']:>2} ({r['events_after']:>2})  {r['verdict']}")

    out["gap_case"] = GAP_CASE
    out["gap_config"] = GAP_CONFIG
    REPORT_JSON.write_text(json.dumps(out, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"\nWrote {RESULTS_CSV.relative_to(ROOT)}, {SUMMARY_CSV.relative_to(ROOT)}, "
          f"{REPORT_JSON.relative_to(ROOT)}")
    return 0


def print_summary(config, s, scored):
    print(f"\n=== {config} ({s['n']} tickets)")
    print(f"  outcome pass {s['outcome_pass_rate_pct']}%  trajectory pass "
          f"{s['trajectory_pass_rate_pct']}%  GAP {s['gap_pp']} pp  "
          f"(right answer, wrong path: {s['right_answer_wrong_path']})")
    print(f"  tool-choice accuracy {s['tool_choice_accuracy_pct']}%  argument validity "
          f"{s['argument_validity_pct']}%  step efficiency mean {s['step_efficiency_mean']} "
          f"max {s['step_efficiency_max']}")
    print(f"  cost/ticket p50 ${s['cost_p50_usd']:.6f}  max ${s['cost_max_usd']:.6f}  "
          f"(mean ${s['cost_mean_usd']:.6f})  tokens p50 {s['tokens_p50']} max {s['tokens_max']}")
    for t in scored:
        print(f"  {t['ticket_id']} outcome={'P' if t['outcome_pass'] else 'F'} "
              f"traj={'P' if t['trajectory_pass'] else 'F'} eff={t['step_efficiency']:<5} "
              f"{t['path']}")
    print("  modes (tickets): " + ", ".join(f"{m}={c}" for m, c in s["mode_tickets"].items() if c))


def show(args):
    path, scored = scored_config(args.config)
    data = json.loads(path.read_text(encoding="utf-8"))
    run_ = next(r for r in data["runs"] if r["ticket_id"] == args.ticket)
    s = next(x for x in scored if x["ticket_id"] == args.ticket)
    case = next(c for c in CASES if c["ticket_id"] == args.ticket)
    print(f"{args.ticket} · {args.config} · {path.name}")
    print(f"accepted paths: {' | '.join(' > '.join(seq) for seq in case['accepted'])}")
    print(f"taken path:     {s['path']}")
    print(f"outcome {'PASS' if s['outcome_pass'] else 'FAIL'} ({s['outcome_reason']}) · "
          f"trajectory {'PASS' if s['trajectory_pass'] else 'FAIL'}")
    for event in s["events"]:
        print(f"  ! {event}")
    print("\n" + "\n".join(run_["log"]))
    return 0


def import_week7(args):
    """Rebuild Week 7 race 1's agent trajectories from its logs.

    Race 1 ran the agent with no evidence guard and passed 4/10 on outcome
    without calling get_order once. Its JSON kept the log, not a structured
    trajectory. The tools are pure functions over the frozen store, so each
    logged call is re-executed to recover its full result (the log truncates
    results at 160 characters); arguments are copied from the log verbatim.
    """
    import re
    from support_tools import call_tool

    data = json.loads(WEEK7_RACE1.read_text(encoding="utf-8"))
    tool_line = re.compile(r"^\[agent\]   tool (\S+)\((\{.*?\})\) -> ")
    lap_line = re.compile(r"^\[agent\] lap (\d+): ")
    runs = []
    for run_ in (r for r in data["runs"] if r["system"] == "agent"):
        lap, trajectory = 0, []
        for line in run_["log"]:
            if (m := lap_line.match(line)):
                lap = int(m.group(1))
            elif (m := tool_line.match(line)):
                arguments = json.loads(m.group(2))
                trajectory.append({"lap": lap, "type": "tool", "name": m.group(1),
                                   "arguments": arguments,
                                   "result": call_tool(m.group(1), arguments)})
            elif "unparseable reply" in line:
                trajectory.append({"lap": lap, "type": "prose", "text": line.split(": ", 1)[-1]})
            elif "] final: decision=" in line:
                trajectory.append({"lap": lap, "type": "answer",
                                   "decision": run_["result"]["decision"],
                                   "refund_amount": run_["result"]["refund_amount"]})
            elif "] BUDGET " in line:
                trajectory.append({"lap": lap, "type": "budget", "limit": run_["terminated_by"]})
        runs.append({k: run_[k] for k in ("ticket_id", "result", "terminated_by", "latency_ms",
                                          "tokens", "cost_usd", "meter", "log")}
                     | {"system": "agent", "trajectory": trajectory, "evidence_guard": False})

    RUNS.mkdir(parents=True, exist_ok=True)
    path = RUNS / "traj_week7_race1_20260921-115626.json"
    path.write_text(json.dumps({
        "ran_at": data["ran_at"], "config": "week7_race1",
        "settings": {"evidence_guard": False, "note": "rebuilt from Week 7 race 1 logs"},
        "source": WEEK7_RACE1.relative_to(ROOT).as_posix(), "model": data["model"],
        "budgets": data["budgets"], "rate_card_usd_per_mtok": data["rate_card_usd_per_mtok"],
        "runs": runs,
    }, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"Wrote {path.relative_to(ROOT)} from {WEEK7_RACE1.relative_to(ROOT)}")
    return report(args)


def ui_artefacts():
    """What the UI's Week 8 view renders: the stored report, rebuilt on read."""
    report_ = json.loads(REPORT_JSON.read_text(encoding="utf-8")) if REPORT_JSON.exists() else {}
    for config, block in (report_.get("configs") or {}).items():
        data = json.loads((ROOT / block["run"]).read_text(encoding="utf-8"))
        logs = {r["ticket_id"]: {"log": r["log"], "trajectory": r["trajectory"],
                                 "result": r["result"]} for r in data["runs"]}
        for ticket in block["tickets"]:
            ticket.update(logs.get(ticket["ticket_id"], {}))
    writeup = W8 / "README.md"
    report_["writeup"] = writeup.read_text(encoding="utf-8") if writeup.exists() else None
    injection = W8 / "injection_report.json"
    report_["injection"] = (json.loads(injection.read_text(encoding="utf-8"))
                            if injection.exists() else None)
    return report_


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    run_cmd = sub.add_parser("run")
    run_cmd.add_argument("--config", choices=list(CONFIGS), required=True)
    run_cmd.add_argument("--tickets", nargs="*")
    run_cmd.set_defaults(func=run)
    sub.add_parser("report").set_defaults(func=report)
    sub.add_parser("import-week7").set_defaults(func=import_week7)
    show_cmd = sub.add_parser("show")
    show_cmd.add_argument("ticket")
    show_cmd.add_argument("--config", default="baseline", choices=list(CONFIGS) + HISTORICAL)
    show_cmd.set_defaults(func=show)
    return parser


if __name__ == "__main__":
    arguments = build_parser().parse_args()
    sys.exit(arguments.func(arguments))
