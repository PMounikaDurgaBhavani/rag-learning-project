"""Week 5 Task Set A tooling: sample traces, replay one, count what is there.

Three subcommands, matching the three things the brief asks you to *prove*
rather than assert:

    sample   a seeded random draw, so the selection is reproducible by
             anyone who has the seed and the trace file
    replay   re-run one trace from the trace alone and diff the output
             against what was originally recorded
    stats    raw counts over the log

What this tool deliberately does NOT do is write your observation
sentences or name your failure modes. Those are the two things the rubric
actually scores (25 + 30 of 100), they require reading, and a tool that
guessed at them would hand you exactly the categories you already expected
— which is the failure this week exists to prevent.

Usage
-----
    python experiments/error_analysis.py stats
    python experiments/error_analysis.py sample --seed 20260907 --n 20
    python experiments/error_analysis.py replay --trace-id <id>
    python experiments/error_analysis.py replay --seed 20260907   # pick one at random
"""

import argparse
import difflib
import json
import os
import random
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(
    0,
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
)

import tracing

OUT_DIR = Path("week5")


# ---------------------------------------------------------------------------
# sample
# ---------------------------------------------------------------------------

def cmd_sample(args):
    traces = tracing.load_traces(args.trace_file)

    if not traces:
        print("No traces found. Generate traffic first — see "
              "experiments/generate_traffic.py, or just use the app.")
        return 1

    if len(traces) < args.n:
        print(f"WARNING: only {len(traces)} traces exist but {args.n} were "
              f"requested. A sample that is most of the population is not a "
              f"sample; collect more traffic before trusting the frequencies.")

    # Sort by trace_id first so the draw does not depend on file order.
    # Same seed + same trace set == same 20, on any machine.
    pool = sorted(traces, key=lambda t: t["trace_id"])
    rng = random.Random(args.seed)
    picked = rng.sample(pool, min(args.n, len(pool)))

    print("=" * 78)
    print(f"SEEDED RANDOM SAMPLE — seed={args.seed} · n={len(picked)} "
          f"· population={len(pool)}")
    print(f"selection: random.Random({args.seed}).sample(sorted_by_trace_id, {args.n})")
    print("=" * 78)

    for index, trace in enumerate(picked, start=1):
        outcome = trace["outcome"]
        state = f"REFUSED:{outcome['refusal_reason']}" if outcome["refused"] else "answered"
        print(f"{index:>3}. {trace['trace_id']}  {state}")
        print(f"     Q: {trace['query']}")

    OUT_DIR.mkdir(exist_ok=True)

    sample_path = OUT_DIR / "sample.json"
    sample_path.write_text(json.dumps({
        "seed": args.seed,
        "n": len(picked),
        "population": len(pool),
        "selection_rule": (
            f"random.Random({args.seed}).sample("
            f"traces sorted by trace_id, {args.n})"
        ),
        "trace_ids": [t["trace_id"] for t in picked],
    }, indent=2), encoding="utf-8")

    notes_path = OUT_DIR / "notes_template.md"
    if notes_path.exists() and not args.force:
        print(f"\n{notes_path} already exists — not overwriting your notes. "
              f"Pass --force to regenerate the blank template.")
    else:
        notes_path.write_text(_notes_template(args.seed, picked), encoding="utf-8")
        print(f"\nBlank open-coding template: {notes_path}")

    print(f"Sample manifest: {sample_path}")
    print("\nNext: read each trace and write ONE sentence describing what you")
    print("saw. Not a category, not a fix. Change no code while you read.")
    return 0


def _notes_template(seed, picked):
    """A blank sheet with one slot per trace. The sentences are yours."""

    lines = [
        "# Week 5 — Open coding notes",
        "",
        f"Seed: `{seed}` · sample size: {len(picked)}",
        "",
        "One sentence per trace describing **what you saw**. Not a category, ",
        "not a diagnosis, not a fix. \"I don't know why this failed\" is a ",
        "permitted and valuable sentence.",
        "",
        "---",
        "",
    ]

    for index, trace in enumerate(picked, start=1):
        outcome = trace["outcome"]
        state = (
            f"refused ({outcome['refusal_reason']})"
            if outcome["refused"] else "answered"
        )
        lines += [
            f"### {index}. `{trace['trace_id']}`",
            "",
            f"- **Question:** {trace['query']}",
            f"- **System did:** {state}",
            f"- **Answer shown:** {(outcome.get('answer') or '').strip()[:200]}",
            f"- **Raw model output:** "
            f"{str(trace['generation'].get('raw_output') or '').strip()[:200]}",
            f"- **Top chunks:** "
            + ", ".join(c["chunk_id"] for c in trace["retrieval"][:3]),
            "",
            "**Observation:** _(write one sentence here)_",
            "",
        ]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# replay
# ---------------------------------------------------------------------------

def cmd_replay(args):
    traces = tracing.load_traces(args.trace_file)
    if not traces:
        print("No traces found.")
        return 1

    if args.trace_id:
        trace = next((t for t in traces if t["trace_id"] == args.trace_id), None)
        if trace is None:
            print(f"No trace with id {args.trace_id}")
            return 1
        how = f"--trace-id {args.trace_id}"
    else:
        pool = sorted(traces, key=lambda t: t["trace_id"])
        trace = random.Random(args.seed).choice(pool)
        how = f"random.Random({args.seed}).choice(sorted_by_trace_id)"

    print("=" * 78)
    print(f"REPLAY — {trace['trace_id']}")
    print(f"selected by: {how}")
    print("=" * 78)

    messages = (trace.get("prompt") or {}).get("messages")
    params = (trace.get("model") or {}).get("params")
    model_name = (trace.get("model") or {}).get("name")

    missing = [
        name for name, value in [
            ("prompt.messages", messages),
            ("model.params", params),
            ("model.name", model_name),
            ("generation.raw_output", trace["generation"].get("raw_output")),
        ] if not value
    ]

    print(f"\nQuery          : {trace['query']}")
    print(f"Recorded at    : {trace['ts']}  (app {trace.get('app_version')})")
    print(f"Prompt version : {trace['prompt'].get('version')} "
          f"· sha {trace['prompt'].get('sha')}")
    print(f"Model          : {model_name}")
    print(f"Params         : {params}")
    print(f"Retrieved      : "
          + ", ".join(f"{c['chunk_id']}"
                      + (f"(d={c['distance']})" if c.get("distance") is not None else "")
                      for c in trace["retrieval"]))

    if missing:
        print(f"\nCANNOT FULLY REPLAY — fields absent from the trace: "
              f"{', '.join(missing)}")
        if "generation.raw_output" in missing and messages:
            print("(The model never ran for this trace — it was refused at a "
                  "gate before generation. That IS the recorded behaviour, so "
                  "the replay below reproduces the gate, not a model call.)")
        if not messages:
            return 1

    # The replay uses ONLY what the trace carries. It does not re-retrieve,
    # so a corpus that has changed since cannot quietly alter the result.
    from transformers import pipeline
    from generator import extract_text

    print(f"\nReplaying with the stored prompt (no retrieval, no index)…")
    generator = pipeline("text-generation", model=model_name)
    replayed = extract_text(generator(messages, **params))

    original = trace["generation"].get("raw_output")

    print("\n" + "-" * 78)
    print("ORIGINAL raw output")
    print("-" * 78)
    print(original)
    print("\n" + "-" * 78)
    print("REPLAYED raw output")
    print("-" * 78)
    print(replayed)

    identical = (original or "").strip() == (replayed or "").strip()
    print("\n" + "=" * 78)
    print(f"IDENTICAL: {identical}")
    if not identical:
        print("\nDiff (original → replayed):")
        for line in difflib.unified_diff(
            (original or "").splitlines(),
            (replayed or "").splitlines(),
            fromfile="original", tofile="replayed", lineterm="",
        ):
            print("  " + line)
        print("\nGreedy decoding (do_sample=False) should be deterministic; a "
              "difference here means something outside the trace changed — "
              "model weights, transformers version, or hardware.")
    print("=" * 78)

    OUT_DIR.mkdir(exist_ok=True)
    evidence = OUT_DIR / "replay_evidence.md"
    evidence.write_text("\n".join([
        "# Replay evidence",
        "",
        f"- **trace_id:** `{trace['trace_id']}`",
        f"- **selected by:** `{how}`",
        f"- **recorded:** {trace['ts']} (app `{trace.get('app_version')}`)",
        f"- **prompt version:** `{trace['prompt'].get('version')}` "
        f"(sha `{trace['prompt'].get('sha')}`)",
        f"- **model:** `{model_name}`",
        f"- **params:** `{params}`",
        f"- **retrieved chunk_ids + scores:**",
        "",
        "```json",
        json.dumps(trace["retrieval"], indent=2),
        "```",
        "",
        f"- **fields that had to be added to make replay possible:** "
        + (", ".join(missing) if missing else "none — the trace was complete"),
        "",
        "## Original raw output",
        "",
        "```",
        str(original),
        "```",
        "",
        "## Replayed raw output (from the trace alone, no retrieval)",
        "",
        "```",
        str(replayed),
        "```",
        "",
        f"**Identical:** {identical}",
        "",
    ]), encoding="utf-8")
    print(f"\nWritten to {evidence}")
    return 0


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------

def cmd_stats(args):
    traces = tracing.load_traces(args.trace_file)
    if not traces:
        print("No traces found.")
        return 1

    print(f"Traces          : {len(traces)}")
    print(f"First           : {traces[0]['ts']}")
    print(f"Last            : {traces[-1]['ts']}")

    refused = [t for t in traces if t["outcome"]["refused"]]
    print(f"Refused         : {len(refused)} "
          f"({len(refused) / len(traces) * 100:.1f}%)")

    print("\nBy refusal_reason:")
    # distance_gate carries its measured distance, so group on the family
    # rather than reporting twenty buckets of one.
    for reason, count in Counter(
        (t["outcome"]["refusal_reason"] or "(answered)").split(" (")[0]
        for t in traces
    ).most_common():
        print(f"  {count:>4}  {reason}")

    print("\nBy prompt version / app build:")
    for key, count in Counter(
        f"{t['prompt'].get('version')} @ {t.get('app_version')}" for t in traces
    ).most_common():
        print(f"  {count:>4}  {key}")

    print("\n" + "!" * 74)
    print("These are reasons the SYSTEM emitted, not failure modes you found.")
    print("'ungrounded_answer' names a gate, not a thing your manager can act")
    print("on, and three different real problems can share one reason string.")
    print("Do not paste this list into taxonomy.md — read the traces.")
    print("!" * 74)
    return 0


# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--trace-file", default=None,
                        help="defaults to traces/traces.jsonl")
    subparsers = parser.add_subparsers(dest="command", required=True)

    sample = subparsers.add_parser("sample", help="seeded random sample")
    sample.add_argument("--seed", type=int, required=True)
    sample.add_argument("--n", type=int, default=20)
    sample.add_argument("--force", action="store_true",
                        help="overwrite an existing notes template")
    sample.set_defaults(func=cmd_sample)

    replay = subparsers.add_parser("replay", help="replay one trace")
    replay.add_argument("--trace-id", default=None)
    replay.add_argument("--seed", type=int, default=20260907)
    replay.set_defaults(func=cmd_replay)

    stats = subparsers.add_parser("stats", help="raw counts over the log")
    stats.set_defaults(func=cmd_stats)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
