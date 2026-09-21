"""Week 7: race the refund-chase agent against the fixed workflow.

Same 10 tickets, same three tools, same model, same output contract, same
grader. The only difference is who decides the order of the calls.

    python main.py race                       # the race -> week7/race.csv
    python experiments/week7_race.py race
    python experiments/week7_race.py budget-demo --max-iterations 2
    python experiments/week7_race.py table    # reprint the last race

Four numbers per system, over the same inputs: pass rate, p50 latency, total
tokens, cost per ticket. Tokens are summed per model call, so the agent's
re-sent transcript is counted every lap rather than once at the end.
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
os.chdir(ROOT)

import tracing
from agent_runtime import MODEL_NAME, RATE_CARD, Budgets

W7 = ROOT / "week7"
RACE_SET = W7 / "race_set.jsonl"
RACE_CSV = W7 / "race.csv"
SUMMARY_CSV = W7 / "race_summary.csv"
RUNS = W7 / "runs"
BUDGET_LOG = W7 / "budget_termination.log"

SYSTEMS = ("agent", "workflow")


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_race_set():
    rows = [json.loads(line) for line in RACE_SET.read_text(encoding="utf-8").splitlines()
            if line.strip()]
    branchy = [row for row in rows if row["branch_after_step2"]]
    if len(rows) < 10:
        raise SystemExit(f"race_set.jsonl has {len(rows)} tickets; the brief asks for 10")
    if len(branchy) < 3:
        raise SystemExit(f"only {len(branchy)} tickets branch after step 2; the brief asks for 3+")
    return rows


# ---------------------------------------------------------------------------
# grading — identical for both systems
# ---------------------------------------------------------------------------

def grade(expected, result):
    """A ticket passes when the decision and the amount are both right.

    Reasons are kept apart so a wrong decision, an unparseable answer and a
    right decision with the wrong amount do not collapse into one number.
    """
    if result.get("decision") is None:
        return False, "no_decision"
    if result["decision"] != expected["decision"]:
        return False, f"decision {result['decision']} != {expected['decision']}"

    want, got = expected["refund_amount"], result.get("refund_amount")
    if want is None and got is not None:
        return False, f"amount {got} should be null"
    if want is not None and (got is None or abs(got - want) > 0.005):
        return False, f"amount {got} != {want}"
    if not (result.get("reply") or "").strip():
        return False, "empty reply"
    return True, "ok"


# ---------------------------------------------------------------------------
# running
# ---------------------------------------------------------------------------

def model_calls_of(meter):
    """Calls that actually went to the model.

    The agent's laps are model calls by definition. The workflow counts its
    own; an older run that predates that counter made exactly one call per
    ticket, and made it only if it got as far as generating the sentence.
    """
    if "model_calls" in meter:
        return meter["model_calls"]
    if "iterations" in meter:
        return meter["iterations"]
    return 1 if meter.get("input_tokens", 0) > 0 else 0


def run_one(system, ticket_id, budgets):
    if system == "agent":
        import ticket_agent
        return ticket_agent.resolve_ticket(ticket_id, budgets=budgets)
    import ticket_workflow
    return ticket_workflow.resolve_ticket(ticket_id)


def trace_to_langfuse(run, case, passed, reason, session_id):
    """One Langfuse trace per (system, ticket), so the race is inspectable
    next to every other run the app has made."""
    client = tracing.langfuse_client()
    if client is None:
        return
    try:
        from langfuse import propagate_attributes

        trace_id = tracing.langfuse_trace_id(
            f"week7-{run['system']}-{run['ticket_id']}-{session_id}"
        )
        meter = run["meter"]
        with client.start_as_current_observation(
            trace_context={"trace_id": trace_id},
            name=f"week7-{run['system']}",
            as_type="agent" if run["system"] == "agent" else "chain",
            input={"ticket_id": run["ticket_id"], "class": case["class"]},
            output=run["result"],
            metadata={"meter": meter, "budgets": run["budgets"],
                      "terminated_by": run["terminated_by"], "grade_reason": reason,
                      "model": MODEL_NAME, "log": run["log"]},
            level="WARNING" if not passed else "DEFAULT",
            status_message=None if passed else reason,
        ) as root:
            with propagate_attributes(
                session_id=session_id,
                tags=[f"system:{run['system']}", f"class:{case['class']}",
                      "week7", "pass" if passed else "fail"]
                + ([f"terminated:{run['terminated_by']}"] if run["terminated_by"] else []),
                trace_name=f"week7-{run['system']}",
            ):
                for name, count in (meter.get("calls_by_tool") or {}).items():
                    root.start_observation(name=f"tool:{name}", as_type="tool",
                                           input={"calls": count}).end()
        client.create_score(trace_id=trace_id, name="week7_pass",
                            value=1.0 if passed else 0.0, data_type="BOOLEAN", comment=reason)
        client.create_score(trace_id=trace_id, name="week7_tokens",
                            value=float(run["tokens"]), data_type="NUMERIC")
        client.create_score(trace_id=trace_id, name="week7_cost_usd",
                            value=float(run["cost_usd"]), data_type="NUMERIC")
    except Exception as error:
        print(f"  [langfuse] not recorded: {error}")


def race(args):
    cases = load_race_set()
    budgets = Budgets(max_iterations=args.max_iterations, max_tokens=args.max_tokens,
                      max_cost_usd=args.max_cost, max_wall_seconds=args.max_seconds)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    session_id = f"week7-race-{stamp}"

    print(f"Week 7 race · {len(cases)} tickets · model {MODEL_NAME}")
    print(f"budgets {budgets.as_dict()}")
    print(f"branchy tickets: "
          f"{', '.join(c['ticket_id'] for c in cases if c['branch_after_step2'])}\n")

    rows, runs = [], []
    for system in SYSTEMS:
        # Both systems start warm, so neither pays the ~290 s cold start on
        # its first ticket and the agent's wall budget measures only work.
        import agent_runtime
        agent_runtime.warm_up()

        for index, case in enumerate(cases, start=1):
            run = run_one(system, case["ticket_id"], budgets)
            passed, reason = grade(case["expected"], run["result"])
            meter = run["meter"]
            rows.append({
                "system": system,
                "ticket_id": case["ticket_id"],
                "class": case["class"],
                "branch_after_step2": case["branch_after_step2"],
                "pass": passed,
                "fail_reason": "" if passed else reason,
                "decision": run["result"]["decision"],
                "expected_decision": case["expected"]["decision"],
                "refund_amount": run["result"]["refund_amount"],
                "expected_amount": case["expected"]["refund_amount"],
                "latency_ms": run["latency_ms"],
                "tokens": run["tokens"],
                "cost_usd": round(run["cost_usd"], 6),
                "model_calls": model_calls_of(meter),
                "tool_steps": meter.get("steps", meter.get("tool_calls")),
                "tool_calls": meter["tool_calls"],
                "wrong_tool_calls": meter.get("wrong_tool_calls", 0),
                "tool_errors": meter["tool_errors"],
                "terminated_by": run["terminated_by"] or "",
            })
            runs.append({**run, "class": case["class"], "pass": passed, "reason": reason})
            trace_to_langfuse(run, case, passed, reason, session_id)
            print(f"  {system:<8} {index:>2}/{len(cases)} {case['ticket_id']} "
                  f"{'PASS' if passed else 'FAIL'} {('(' + reason + ')') if not passed else '':<34} "
                  f"{run['latency_ms']:>7.0f}ms {run['tokens']:>6} tok"
                  + (f"  BUDGET:{run['terminated_by']}" if run["terminated_by"] else ""))

        if system == "agent":
            # The workflow loads the same weights; freeing first keeps the two
            # halves of the race from competing for the same 8 GB.
            import agent_runtime
            agent_runtime.release_model()

    tracing.flush()
    write_outputs(rows, runs, cases, budgets, stamp, session_id)
    print_table(rows)
    return 0


def summarise(rows):
    summary = {}
    for system in SYSTEMS:
        subset = [row for row in rows if row["system"] == system]
        if not subset:
            continue
        passes = sum(1 for row in subset if row["pass"])
        summary[system] = {
            "n": len(subset),
            "passes": passes,
            "pass_rate_pct": round(100.0 * passes / len(subset), 1),
            "p50_latency_ms": round(statistics.median(row["latency_ms"] for row in subset), 1),
            "total_tokens": sum(row["tokens"] for row in subset),
            "mean_tokens_per_ticket": round(
                sum(row["tokens"] for row in subset) / len(subset), 1),
            "cost_per_ticket_usd": round(
                sum(row["cost_usd"] for row in subset) / len(subset), 6),
            "total_cost_usd": round(sum(row["cost_usd"] for row in subset), 6),
            "model_calls": sum(row["model_calls"] or 0 for row in subset),
            "tool_calls": sum(row["tool_calls"] for row in subset),
            "wrong_tool_calls": sum(row["wrong_tool_calls"] for row in subset),
            "budget_terminations": sum(1 for row in subset if row["terminated_by"]),
        }
    return summary


def write_outputs(rows, runs, cases, budgets, stamp, session_id):
    RUNS.mkdir(parents=True, exist_ok=True)

    with open(RACE_CSV, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    summary = summarise(rows)
    with open(SUMMARY_CSV, "w", newline="", encoding="utf-8") as handle:
        fields = ["system"] + list(next(iter(summary.values())))
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for system, values in summary.items():
            writer.writerow({"system": system, **values})

    (RUNS / f"race_{stamp}.json").write_text(json.dumps({
        "ran_at": now_iso(),
        "app_version": tracing.app_version(),
        "model": MODEL_NAME,
        "rate_card_usd_per_mtok": RATE_CARD,
        "budgets": budgets.as_dict(),
        "langfuse_session": session_id,
        "tickets": [case["ticket_id"] for case in cases],
        "summary": summary,
        "rows": rows,
        "runs": runs,
    }, indent=2, default=str) + "\n", encoding="utf-8")

    print(f"\nWrote {RACE_CSV.relative_to(ROOT)}, {SUMMARY_CSV.relative_to(ROOT)}, "
          f"{(RUNS / f'race_{stamp}.json').relative_to(ROOT)}")


def print_table(rows):
    summary = summarise(rows)
    print("\n" + "=" * 96)
    print("RACE — same 10 tickets, same tools, same model, same output contract")
    print("=" * 96)
    print(f"{'system':<10} {'pass rate':>12} {'p50 latency':>13} {'total tokens':>13} "
          f"{'cost/ticket':>13} {'model calls':>12} {'wrong tools':>12}")
    print("-" * 96)
    for system, values in summary.items():
        rate = "{}/{} = {}%".format(values["passes"], values["n"], values["pass_rate_pct"])
        latency = "{:.0f} ms".format(values["p50_latency_ms"])
        cost = "${:.6f}".format(values["cost_per_ticket_usd"])
        print(f"{system:<10} {rate:>12} {latency:>13} {values['total_tokens']:>13,} "
              f"{cost:>13} {values['model_calls']:>12} {values['wrong_tool_calls']:>12}")
    print("-" * 96)

    print("\nBY TICKET CLASS (pass rate)")
    classes = sorted({row["class"] for row in rows})
    print(f"{'class':<16} {'n':>3}  " + "  ".join(f"{system:>10}" for system in SYSTEMS))
    for klass in classes:
        cells = []
        for system in SYSTEMS:
            subset = [row for row in rows if row["class"] == klass and row["system"] == system]
            cells.append(f"{sum(1 for r in subset if r['pass'])}/{len(subset)}")
        n = len([row for row in rows if row["class"] == klass and row["system"] == SYSTEMS[0]])
        print(f"{klass:<16} {n:>3}  " + "  ".join(f"{cell:>10}" for cell in cells))

    terminations = [row for row in rows if row["terminated_by"]]
    if terminations:
        print(f"\nBudget terminations: "
              + ", ".join(f"{row['ticket_id']} ({row['terminated_by']})" for row in terminations))
    print(f"\nRate card: ${RATE_CARD['input_per_mtok']}/Mtok in, "
          f"${RATE_CARD['output_per_mtok']}/Mtok out, applied to measured tokens.")


def budget_demo(args):
    """Run the agent against a deliberately small budget and keep the log."""
    import ticket_agent

    budgets = Budgets(max_iterations=args.max_iterations, max_tokens=args.max_tokens,
                      max_cost_usd=args.max_cost, max_wall_seconds=args.max_seconds)
    print(f"Budget demo · ticket {args.ticket} · {budgets.as_dict()}\n")
    run = ticket_agent.resolve_ticket(args.ticket, budgets=budgets, verbose=True)

    header = [
        f"# Week 7 — budget termination, captured {now_iso()}",
        f"# command: python experiments/week7_race.py budget-demo --ticket {args.ticket} "
        f"--max-iterations {args.max_iterations} --max-tokens {args.max_tokens} "
        f"--max-cost {args.max_cost} --max-seconds {args.max_seconds}",
        f"# model: {MODEL_NAME}",
        f"# budgets: {json.dumps(budgets.as_dict())}",
        f"# terminated_by: {run['terminated_by']}",
        f"# spent: {run['meter']['iterations']} iterations, {run['tokens']} tokens, "
        f"${run['cost_usd']:.6f}, {run['latency_ms']:.0f}ms",
        f"# result at termination: {json.dumps(run['result'])}",
        "",
    ]
    BUDGET_LOG.write_text("\n".join(header + run["log"]) + "\n", encoding="utf-8")
    print(f"\nterminated_by={run['terminated_by']} -> {BUDGET_LOG.relative_to(ROOT)}")
    return 0 if run["terminated_by"] else 1


def table(args):
    latest = sorted(RUNS.glob("race_*.json"))
    if not latest:
        print("No race yet. Run: python main.py race")
        return 1
    data = json.loads(latest[-1].read_text(encoding="utf-8"))
    print(f"{latest[-1].name} · {data['ran_at']} · model {data['model']}")
    print_table(data["rows"])
    return 0


def rebuild(args):
    """Re-derive race.csv and race_summary.csv from a stored run.

    Used when a reported number was derived wrongly rather than measured
    wrongly: the workflow's model calls were being read from its tool-step
    counter, which reported three model calls a ticket where it makes one.
    Latency, tokens, cost and pass/fail are untouched measurements and are
    copied through.
    """
    stored = sorted(RUNS.glob("race_*.json"))
    if not stored:
        print("No race to rebuild from.")
        return 1

    path = Path(args.run) if args.run else stored[-1]
    data = json.loads(path.read_text(encoding="utf-8"))
    by_key = {(run["system"], run["ticket_id"]): run for run in data["runs"]}

    changed = 0
    for row in data["rows"]:
        run = by_key.get((row["system"], row["ticket_id"]))
        if not run:
            continue
        corrected = model_calls_of(run["meter"])
        if row.get("model_calls") != corrected:
            row["model_calls"] = corrected
            changed += 1
        row.setdefault("tool_steps", run["meter"].get("steps", run["meter"]["tool_calls"]))

    data["summary"] = summarise(data["rows"])
    data["rebuilt_at"] = now_iso()
    path.write_text(json.dumps(data, indent=2, default=str) + "\n", encoding="utf-8")

    with open(RACE_CSV, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(data["rows"][0]))
        writer.writeheader()
        writer.writerows(data["rows"])
    with open(SUMMARY_CSV, "w", newline="", encoding="utf-8") as handle:
        fields = ["system"] + list(next(iter(data["summary"].values())))
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for system, values in data["summary"].items():
            writer.writerow({"system": system, **values})

    print(f"Rebuilt from {path.name}: corrected model_calls on {changed} row(s)")
    print_table(data["rows"])
    return 0


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    race_cmd = sub.add_parser("race", help="run both systems over the 10 tickets")
    add_budget_arguments(race_cmd)
    race_cmd.set_defaults(func=race)

    demo = sub.add_parser("budget-demo", help="agent against a small budget, log the termination")
    demo.add_argument("--ticket", default="CD-7008")
    add_budget_arguments(demo, iterations=2, tokens=4000, cost=0.01, seconds=60.0)
    demo.set_defaults(func=budget_demo)

    table_cmd = sub.add_parser("table", help="reprint the last race table")
    table_cmd.set_defaults(func=table)

    rebuild_cmd = sub.add_parser("rebuild",
                                 help="re-derive race.csv/race_summary.csv from a stored run")
    rebuild_cmd.add_argument("--run", default=None, help="path to a week7/runs/race_*.json")
    rebuild_cmd.set_defaults(func=rebuild)
    return parser


def add_budget_arguments(parser, iterations=6, tokens=8000, cost=0.01, seconds=120.0):
    parser.add_argument("--max-iterations", type=int, default=iterations)
    parser.add_argument("--max-tokens", type=int, default=tokens)
    parser.add_argument("--max-cost", type=float, default=cost)
    parser.add_argument("--max-seconds", type=float, default=seconds)


if __name__ == "__main__":
    arguments = build_parser().parse_args()
    sys.exit(arguments.func(arguments))
