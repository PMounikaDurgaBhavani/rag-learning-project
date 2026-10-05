"""Week 7 bonus: memory. A sliding window + summary over 30-turn threads, and
one fact (the customer's tier) that survives a full process restart.

    python experiments/week7_memory.py restart-demo   # two real processes
    python experiments/week7_memory.py long-race      # 3 long threads, both systems

restart-demo starts process A, which resolves CD-7003 and writes CUS-203's
tier to week7/memory/long_term.json from the find_ticket result, then exits.
Process B is a new interpreter with nothing in RAM, reads the file, and gets
the tier back. The log keeps both PIDs.

long-race feeds each thread turn by turn into agent_memory.ThreadMemory, then
runs both systems on the compressed context with the same long-term memory.
Two workflow-only controls separate the causes of a failure:

    workflow_full_thread    the same thread, uncompressed — if this passes and
                            the compressed run fails, summarisation broke it
    workflow_no_long_term   no long-term memory — if this fails on LT-2, the
                            remembered tier is what passed it
"""

import argparse
import csv
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "experiments"))
os.chdir(ROOT)

from agent_memory import LONG_TERM_PATH, LongTermMemory, ThreadMemory
from agent_runtime import MODEL_NAME, Budgets, cost_usd

W7 = ROOT / "week7"
THREADS = W7 / "long_threads.jsonl"
MEMORY_DIR = W7 / "memory"
RESTART_LOG = MEMORY_DIR / "restart_demo.log"
LONG_CSV = W7 / "long_race.csv"
LONG_SUMMARY_CSV = W7 / "long_race_summary.csv"
RUNS = W7 / "runs"

SEED_TICKET = "CD-7003"         # CUS-203, priority — the fact LT-2 needs
SEED_CUSTOMER = "CUS-203"


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# restart demo
# ---------------------------------------------------------------------------

def _process_a(args):
    """Resolve the seed ticket with long-term memory on, then exit."""
    import agent_runtime
    import ticket_workflow

    if LONG_TERM_PATH.exists():
        LONG_TERM_PATH.unlink()
    memory = LongTermMemory()
    print(f"[A pid={os.getpid()}] memory file before: "
          f"{'present' if LONG_TERM_PATH.exists() else 'absent'}")
    agent_runtime.warm_up()
    run = ticket_workflow.resolve_ticket(SEED_TICKET, memory=memory)
    print(f"[A pid={os.getpid()}] resolved {SEED_TICKET}: decision={run['result']['decision']}")
    print(f"[A pid={os.getpid()}] wrote {json.dumps(memory.recall(SEED_CUSTOMER))}")
    print(f"[A pid={os.getpid()}] exiting")
    return 0


def _process_b(args):
    """A fresh interpreter: nothing in RAM, only the file."""
    memory = LongTermMemory()
    tier = memory.tier_for(SEED_CUSTOMER)
    entry = memory.recall(SEED_CUSTOMER).get("tier", {})
    print(f"[B pid={os.getpid()}] read {LONG_TERM_PATH.relative_to(ROOT)}")
    print(f"[B pid={os.getpid()}] recall({SEED_CUSTOMER}).tier = {tier!r} "
          f"(written by pid {entry.get('written_by_pid')} at {entry.get('written_at')}, "
          f"source {entry.get('source')})")
    return 0 if tier == "priority" else 1


def restart_demo(args):
    lines = [f"# Week 7 bonus — long-term memory across a process restart, {now_iso()}",
             f"# command: python experiments/week7_memory.py restart-demo",
             f"# demo pid {os.getpid()} starts two child interpreters in turn", ""]
    for role in ("_a", "_b"):
        done = subprocess.run([sys.executable, __file__, role], capture_output=True,
                              text=True, cwd=ROOT)
        lines += [line for line in done.stdout.splitlines() if line.startswith("[")]
        lines.append(f"[demo] process {role[1:].upper()} exited with code {done.returncode}")
        if done.returncode != 0:
            lines.append(done.stderr[-2000:])
            break
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    RESTART_LOG.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


# ---------------------------------------------------------------------------
# long-thread race
# ---------------------------------------------------------------------------

def count_tokens(text):
    import agent_runtime
    _, tokenizer = agent_runtime.load_model()
    return len(tokenizer(text)["input_ids"])


def load_threads():
    return [json.loads(line) for line in THREADS.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def compress(thread, summarise):
    memory = ThreadMemory(summarise=summarise)
    for turn in thread["turns"]:
        memory.add(turn)
    return memory


def long_race(args):
    import agent_runtime
    import ticket_agent
    import ticket_workflow
    from week7_race import grade

    long_term = LongTermMemory()
    if long_term.tier_for(SEED_CUSTOMER) is None:
        raise SystemExit(f"No remembered tier for {SEED_CUSTOMER}. "
                         f"Run: python experiments/week7_memory.py restart-demo")

    budgets = Budgets(max_iterations=args.max_iterations, max_tokens=args.max_tokens,
                      max_cost_usd=args.max_cost, max_wall_seconds=args.max_seconds)
    agent_runtime.warm_up()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    rows, runs, audits = [], [], []

    for thread in load_threads():
        ticket_id = thread["ticket_id"]
        short = compress(thread, summarise=True)
        full = compress(thread, summarise=False)
        compressed_text, full_text = short.as_text(), full.as_text()

        # Which deciding details survived compression, by plain string match.
        audit = {
            "thread_id": thread["thread_id"], "ticket_id": ticket_id,
            "full_context_tokens": count_tokens(full_text),
            "compressed_context_tokens": count_tokens(compressed_text),
            "summary_calls": short.meter["model_calls"],
            "summary_tokens": short.meter["input_tokens"] + short.meter["output_tokens"],
            "final_summary": short.summary,
            "summaries": short.summaries,
            "window": [turn["text"] for turn in short.window],
            "details": {detail: {"in_summary": detail in short.summary,
                                 "in_window": any(detail in t["text"] for t in short.window)}
                        for detail in thread["critical_details"]},
        }
        audits.append(audit)
        print(f"\n{thread['thread_id']} {ticket_id}: context {audit['full_context_tokens']} -> "
              f"{audit['compressed_context_tokens']} tokens, {audit['summary_calls']} summary calls")
        print(f"  summary: {short.summary!r}")
        for detail, where in audit["details"].items():
            print(f"  detail {detail}: summary={where['in_summary']} window={where['in_window']}")

        summary_tokens = audit["summary_tokens"]
        summary_cost = cost_usd(short.meter["input_tokens"], short.meter["output_tokens"])
        variants = [
            ("agent", lambda: ticket_agent.resolve_ticket(
                ticket_id, budgets=budgets, thread_context=compressed_text,
                memory=LongTermMemory()), True),
            ("workflow", lambda: ticket_workflow.resolve_ticket(
                ticket_id, thread_context=compressed_text, memory=LongTermMemory()), True),
            ("workflow_full_thread", lambda: ticket_workflow.resolve_ticket(
                ticket_id, thread_context=full_text, memory=LongTermMemory()), False),
            ("workflow_no_long_term", lambda: ticket_workflow.resolve_ticket(
                ticket_id, thread_context=compressed_text, memory=None), True),
        ]
        for system, call, compressed in variants:
            run = call()
            passed, reason = grade(thread["expected"], run["result"])
            # Compression is paid for by whichever system reads the summary.
            memory_tokens = summary_tokens if compressed else 0
            memory_cost = summary_cost if compressed else 0.0
            rows.append({
                "system": system, "thread_id": thread["thread_id"], "ticket_id": ticket_id,
                "class": thread["class"], "pass": passed, "fail_reason": "" if passed else reason,
                "decision": run["result"]["decision"],
                "expected_decision": thread["expected"]["decision"],
                "refund_amount": run["result"]["refund_amount"],
                "expected_amount": thread["expected"]["refund_amount"],
                "latency_ms": run["latency_ms"],
                "resolve_tokens": run["tokens"], "memory_tokens": memory_tokens,
                "tokens": run["tokens"] + memory_tokens,
                "cost_usd": round(run["cost_usd"] + memory_cost, 6),
                "terminated_by": run["terminated_by"] or "",
            })
            runs.append({**run, "variant": system, "pass": passed, "reason": reason})
            print(f"  {system:<22} {'PASS' if passed else 'FAIL'} "
                  f"{('(' + reason + ')') if not passed else '':<36} "
                  f"{run['latency_ms']:>8.0f}ms {run['tokens'] + memory_tokens:>6} tok"
                  + (f"  BUDGET:{run['terminated_by']}" if run["terminated_by"] else ""))

    write_long_outputs(rows, runs, audits, budgets, stamp)
    return 0


def write_long_outputs(rows, runs, audits, budgets, stamp):
    import statistics

    with open(LONG_CSV, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    summary = []
    for system in dict.fromkeys(row["system"] for row in rows):
        subset = [row for row in rows if row["system"] == system]
        passes = sum(1 for row in subset if row["pass"])
        summary.append({
            "system": system, "n": len(subset), "passes": passes,
            "pass_rate_pct": round(100.0 * passes / len(subset), 1),
            "p50_latency_ms": round(statistics.median(r["latency_ms"] for r in subset), 1),
            "total_tokens": sum(r["tokens"] for r in subset),
            "cost_per_ticket_usd": round(sum(r["cost_usd"] for r in subset) / len(subset), 6),
            "budget_terminations": sum(1 for r in subset if r["terminated_by"]),
        })
    with open(LONG_SUMMARY_CSV, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(summary[0]))
        writer.writeheader()
        writer.writerows(summary)

    RUNS.mkdir(parents=True, exist_ok=True)
    path = RUNS / f"long_race_{stamp}.json"
    path.write_text(json.dumps({
        "ran_at": now_iso(), "model": MODEL_NAME, "budgets": budgets.as_dict(),
        "summary": summary, "audits": audits, "rows": rows, "runs": runs,
    }, indent=2, default=str) + "\n", encoding="utf-8")

    print("\n" + "=" * 90)
    print(f"{'system':<24} {'pass':>8} {'p50 latency':>13} {'tokens':>9} {'cost/ticket':>13} {'budget':>7}")
    for s in summary:
        print(f"{s['system']:<24} {s['passes']}/{s['n']:>6} {s['p50_latency_ms']:>11.0f}ms "
              f"{s['total_tokens']:>9,} {s['cost_per_ticket_usd']:>13.6f} {s['budget_terminations']:>7}")
    print(f"\nWrote {LONG_CSV.relative_to(ROOT)}, {LONG_SUMMARY_CSV.relative_to(ROOT)}, "
          f"{path.relative_to(ROOT)}")


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("restart-demo").set_defaults(func=restart_demo)
    sub.add_parser("_a").set_defaults(func=_process_a)
    sub.add_parser("_b").set_defaults(func=_process_b)
    race = sub.add_parser("long-race")
    race.add_argument("--max-iterations", type=int, default=8)
    race.add_argument("--max-tokens", type=int, default=8000)
    race.add_argument("--max-cost", type=float, default=0.01)
    race.add_argument("--max-seconds", type=float, default=300.0)
    race.set_defaults(func=long_race)
    return parser


if __name__ == "__main__":
    arguments = build_parser().parse_args()
    sys.exit(arguments.func(arguments))
