"""Week 5 prediction experiment: does raising the distance gate 0.75 -> 0.85
drop failure mode M1 below 10% on a fresh seeded sample?

The prediction under test is week5/prediction.md, committed 2026-09-08 as
c152efb. That file is never edited by this tool. The production constant
RELEVANCE_THRESHOLD in src/generator.py is never edited by this tool either:
the threshold is passed explicitly per call, through the same gate code
(generator._generate_answer, gate 1), and recorded on every trace as
request.threshold.

Design
------
Two arms, same build, same questions in the same order, same retrieval mode
and top_k per call (generate_traffic's pool and seed), differing only in the
threshold. The full call plan — position, question, mode, top_k — is computed
up front without touching the model, so an arm can be resumed after a
failure without repeating a successful call or shifting a position. Each arm
writes to its own trace directory, so the experiment never touches
traces/traces.jsonl and the Week 5 sampler's population is unchanged.

    traffic   run (or resume) one arm into an isolated log; write the arm
              manifest to week5/prediction_run/arm_t<threshold>.json
    sample    validate both arms against the plan, draw seed 20260915 over
              the 0.85 log, pair each trace with its 0.75 twin by position,
              write a blank review template
    score     validate the human-reviewed labels strictly and evaluate the
              committed criteria. Nothing in `score` labels a trace as M1 —
              that is the reviewer's call, as it was in the original pass.

Usage
-----
    python experiments/week5_prediction.py traffic --threshold 0.75
    python experiments/week5_prediction.py traffic --threshold 0.85
    python experiments/week5_prediction.py sample --seed 20260915
    # ... hand-code week5/prediction_run/notes_20260915.md ...
    python experiments/week5_prediction.py score

External services: tracing sends every record to Langfuse when LANGFUSE_*
keys are set in .env. The traffic command sets RAG_LANGFUSE=0 before the
tracing module loads, so experimental traffic stays local. Pass
--langfuse to opt back in.
"""

import argparse
import hashlib
import json
import os
import random
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "experiments"))

RUN_DIR = ROOT / "week5" / "prediction_run"
TRACE_ROOT = ROOT / "traces" / "week5_prediction"

ARMS = ("0.75", "0.85")
TRAFFIC_SEED = 7            # generate_traffic.py's default: same order, modes, top_k
TRAFFIC_RUNS = 2            # the original 92-trace population was 2 passes over the pool
TRAFFIC_MODES = ("dense", "hybrid", "hybrid_rerank")
TRAFFIC_TOP_K = (3, 5, 5, 7)
SOURCE_PREFIX = "week5-prediction:"
SAMPLE_SEED = 20260915      # the seed prediction.md commits to
SAMPLE_N = 20

# From week5/prediction.md, verbatim criteria. Baseline M1 was 4/20 at 0.75.
BASELINE_M1 = 4
PREDICTED_M1_MAX = 1        # "under 10% ... at most 1 of 20"
FALSIFY_M1_AT = 2           # "Mode 1 stays at or above 10% (2+ of 20)"
MIN_CORRECT_REFUSALS = 4    # "Correct refusals fall below 4 / 20"

# Reference for the side check prediction.md names. The Week 4 corrected
# report (results.md, 2026-10-11) moved this from 75.0% to 83.3%; the
# prediction's "must stay at 75.0%" was written against the old labels.
GOLDEN_RESULTS = ROOT / "evaluation" / "golden_ab_results.json"

# M3 proxy: generator.count_tokens re-tokenises the decoded text, which can
# differ from the generated count by a token or two (src/generator.py:271),
# so the cut-off test allows that margin. This is a machine hint reported
# alongside the human M3 count, never in place of it.
TRUNCATION_MARGIN = 2

MODE_NAMES = {
    "M1": "says 'not enough information' to a question the articles do answer",
    "M2": "a message that is not one direct question gets a flat refusal",
    "M3": "the reply stops mid-sentence",
    "M4": "the reply contains text that is not an answer",
    "M5": "answers 'what should I do' without giving the steps",
}
ANSWERABLE_VALUES = ("yes", "no", "unsure")


# ---------------------------------------------------------------------------
# paths and small helpers
# ---------------------------------------------------------------------------

def arm_key(threshold):
    """'0.75' / '0.85' from a float or string."""
    return f"{float(threshold):.2f}"


def arm_dir(threshold):
    return TRACE_ROOT / f"t{arm_key(threshold)}"


def arm_log(threshold):
    return arm_dir(threshold) / "traces.jsonl"


def arm_manifest_path(threshold):
    return RUN_DIR / f"arm_t{arm_key(threshold)}.json"


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 16), b""):
            digest.update(block)
    return digest.hexdigest()


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def portable(path):
    """Path as stored in manifests: relative to the repo when inside it."""
    path = Path(path).resolve()
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def resolve(stored):
    path = Path(stored)
    return path if path.is_absolute() else ROOT / path


def position_of(trace):
    for tag in trace.get("tags") or []:
        if tag.startswith("position:"):
            try:
                return int(tag.split(":", 1)[1])
            except ValueError:
                return None
    return None


# ---------------------------------------------------------------------------
# the call plan — deterministic, computed without the model
# ---------------------------------------------------------------------------

def traffic_plan():
    """Every call an arm makes, in order: identical to generate_traffic.main
    at seed 7 with --mode unset, so both arms see the same (question, mode,
    top_k) at every position."""
    from generate_traffic import build_question_pool

    pool = build_question_pool()
    rng = random.Random(TRAFFIC_SEED)
    plan = []
    position = 0
    for _ in range(TRAFFIC_RUNS):
        order = pool[:]
        rng.shuffle(order)
        for question, kind in order:
            mode = rng.choice(TRAFFIC_MODES)
            top_k = rng.choice(TRAFFIC_TOP_K)
            position += 1
            plan.append({"position": position, "question": question,
                         "kind": kind, "mode": mode, "top_k": top_k})
    return plan


def index_positions(traces, plan, threshold, expected_build=None):
    """Map position -> trace for a log, checking every record against the plan.

    Returns (done, problems). `problems` is a list of human-readable strings;
    a log with any problem must not be sampled from and cannot be resumed
    without --force, because a misaligned record would pair with the wrong
    twin.

    `expected_build` is the git sha every record must carry as app_version.
    An arm must be generated on one build: a resumed arm that straddles a
    commit, or a record with no build at all, fails here.
    """
    by_position = {entry["position"]: entry for entry in plan}
    done = {}
    problems = []
    threshold = float(threshold)

    for trace in traces:
        tid = trace.get("trace_id", "?")
        pos = position_of(trace)
        if pos is None:
            problems.append(f"trace {tid}: no position tag")
            continue
        if expected_build is not None:
            build = trace.get("app_version")
            if not build:
                problems.append(f"position {pos}: record has no app_version (build) identifier")
                continue
            if build != expected_build:
                problems.append(f"position {pos}: app_version {build!r} is not the arm's build "
                                f"{expected_build!r}")
                continue
        if pos not in by_position:
            problems.append(f"trace {tid}: position {pos} outside the plan (1..{len(plan)})")
            continue
        if pos in done:
            problems.append(f"position {pos}: duplicate traces {done[pos]['trace_id']} and {tid}")
            continue
        entry = by_position[pos]
        request = trace.get("request") or {}
        if trace.get("query") != entry["question"]:
            problems.append(f"position {pos}: question differs from the plan "
                            f"({str(trace.get('query'))[:40]!r} vs {entry['question'][:40]!r})")
            continue
        if request.get("retrieval_mode") != entry["mode"] or request.get("top_k") != entry["top_k"]:
            problems.append(f"position {pos}: mode/top_k {request.get('retrieval_mode')}/"
                            f"{request.get('top_k')} differ from the plan {entry['mode']}/{entry['top_k']}")
            continue
        if request.get("threshold") is None or abs(float(request["threshold"]) - threshold) > 1e-9:
            problems.append(f"position {pos}: request.threshold {request.get('threshold')} "
                            f"is not the arm's {threshold}")
            continue
        if not str(trace.get("source") or "").startswith(SOURCE_PREFIX):
            problems.append(f"position {pos}: source {trace.get('source')!r} is not experiment traffic")
            continue
        done[pos] = trace

    return done, problems


# ---------------------------------------------------------------------------
# traffic
# ---------------------------------------------------------------------------

def write_arm_manifest(threshold, log, plan, done, failures, session, tracing_module,
                       production_threshold, previous=None):
    successful = sorted(done)
    failed = sorted(set(entry["position"] for entry in plan) - set(successful))
    manifest = {
        "arm": arm_key(threshold),
        "threshold": float(threshold),
        "production_threshold": production_threshold,
        "app_version": tracing_module.app_version(),
        "traffic_seed": TRAFFIC_SEED,
        "runs": TRAFFIC_RUNS,
        "pool_size": len(plan) // TRAFFIC_RUNS,
        "expected_traces": len(plan),
        "successful_traces": len(successful),
        "successful_positions": successful,
        "failed_positions": [
            {"position": pos, "error": failures.get(pos, "no trace written")} for pos in failed
        ],
        "complete": len(successful) == len(plan) and not failed,
        "langfuse": tracing_module.langfuse_enabled(),
        "log": portable(log),
        "log_sha256": sha256_file(log) if log.exists() else None,
        "sessions": (previous or {}).get("sessions", []) + [session],
        "elapsed_s": round(sum(s["elapsed_s"] for s in (previous or {}).get("sessions", []))
                           + session["elapsed_s"], 1),
        "written_at": utc_now(),
    }
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    arm_manifest_path(threshold).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def cmd_traffic(args):
    threshold = float(args.threshold)
    out_dir = arm_dir(threshold)
    log = arm_log(threshold)
    manifest_path = arm_manifest_path(threshold)

    # Isolation. These must be set before tracing is imported: it reads
    # RAG_TRACE_DIR at import time. RAG_LANGFUSE=0 keeps experimental
    # traffic off the shared Langfuse project unless asked for.
    os.environ["RAG_TRACE_DIR"] = str(out_dir)
    os.environ.pop("RAG_TRACE_DB", None)
    if not args.langfuse:
        os.environ["RAG_LANGFUSE"] = "0"

    import tracing
    assert Path(tracing.TRACE_FILE).resolve() == log.resolve(), (
        f"tracing is writing to {tracing.TRACE_FILE}, not {log}; "
        f"was tracing imported before RAG_TRACE_DIR was set?"
    )
    assert tracing.TRACING_ENABLED, "RAG_TRACE=0 is set; the experiment needs traces"

    from generator import generate_answer, RELEVANCE_THRESHOLD

    plan = traffic_plan()

    if args.force:
        if log.exists():
            log.unlink()
        if manifest_path.exists():
            manifest_path.unlink()
    out_dir.mkdir(parents=True, exist_ok=True)

    previous = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else None
    existing = tracing.load_traces(log) if log.exists() else []
    # Resume only onto records from this same build: a log started before a
    # commit cannot be finished after it.
    done, problems = index_positions(existing, plan, threshold, tracing.app_version())
    if problems:
        print(f"Existing log {log} does not match the plan or build — refusing to resume onto it:")
        for problem in problems[:20]:
            print(f"  - {problem}")
        print("Pass --force to discard this arm and start it again.")
        return 1

    todo = [entry for entry in plan if entry["position"] not in done]

    print("=" * 78)
    print(f"WEEK 5 PREDICTION — traffic arm threshold={threshold} "
          f"(production constant stays {RELEVANCE_THRESHOLD})")
    print(f"pool {len(plan) // TRAFFIC_RUNS} × {TRAFFIC_RUNS} runs = {len(plan)} positions, "
          f"traffic seed {TRAFFIC_SEED}, build {tracing.app_version()}")
    print(f"log: {log}   langfuse: {'on' if tracing.langfuse_enabled() else 'off'}")
    if done:
        print(f"RESUMING: {len(done)} positions already in the log, {len(todo)} to run")
    if not todo:
        print("Nothing to do: every position already has a trace.")
    print("=" * 78)

    failures = {}
    started = time.perf_counter()
    started_at = utc_now()
    for entry in todo:
        pos = entry["position"]
        try:
            result = generate_answer(
                entry["question"],
                top_k=entry["top_k"],
                retrieval_mode=entry["mode"],
                threshold=threshold,
                source=f"{SOURCE_PREFIX}{entry['kind']}",
                session_id=f"week5-prediction-t{arm_key(threshold)}",
                tags=["week5-prediction", f"threshold:{arm_key(threshold)}", f"position:{pos}"],
            )
            state = (f"refused({result['refusal_reason']})"
                     if result["refused"] else "answered")
            print(f"  [{pos:>3}] {entry['mode']:<14} k={entry['top_k']} {state:<42} "
                  f"{entry['question'][:40]}")
        except Exception as error:           # keep the plan aligned; record, do not shift
            failures[pos] = f"{type(error).__name__}: {error}"
            print(f"  [{pos:>3}] FAILED — {failures[pos][:80]}")

    tracing.flush()
    elapsed = time.perf_counter() - started

    # The log is the truth: a call that raised, or whose trace failed to
    # write, has no record at its position and is counted as failed.
    traces = tracing.load_traces(log) if log.exists() else []
    done, problems = index_positions(traces, plan, threshold, tracing.app_version())
    if problems:
        print("The log is inconsistent after this run — not writing a manifest:")
        for problem in problems[:20]:
            print(f"  - {problem}")
        return 1

    session = {"started_at": started_at, "finished_at": utc_now(),
               "elapsed_s": round(elapsed, 1), "calls": len(todo),
               "failed": sorted(failures)}
    manifest = write_arm_manifest(threshold, log, plan, done, failures, session, tracing,
                                  RELEVANCE_THRESHOLD, previous)

    print(f"\n{manifest['successful_traces']}/{manifest['expected_traces']} positions traced "
          f"→ {log}")
    print(f"manifest: {manifest_path}")
    if not manifest["complete"]:
        print(f"ARM INCOMPLETE: {len(manifest['failed_positions'])} position(s) failed — "
              f"re-run the same command to retry only those.")
        return 1
    print("arm complete")
    return 0


# ---------------------------------------------------------------------------
# sample
# ---------------------------------------------------------------------------

def validate_arm(threshold, tracing_module, plan=None):
    """Refuse to sample from anything but a complete, plan-aligned, unchanged arm."""
    manifest_path = arm_manifest_path(threshold)
    if not manifest_path.exists():
        raise SystemExit(f"missing arm manifest {manifest_path}: run "
                         f"`week5_prediction.py traffic --threshold {arm_key(threshold)}` first")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    log = resolve(manifest.get("log") or "")
    if not manifest.get("log") or not log.exists():
        raise SystemExit(f"arm {arm_key(threshold)}: log {manifest.get('log')} is missing")
    if sha256_file(log) != manifest.get("log_sha256"):
        raise SystemExit(f"arm {arm_key(threshold)}: {log} has changed since its manifest was "
                         f"written — re-run `traffic --threshold {arm_key(threshold)}`")
    if not manifest.get("complete") or manifest.get("failed_positions"):
        failed = [f["position"] for f in manifest.get("failed_positions", [])]
        raise SystemExit(f"arm {arm_key(threshold)}: incomplete "
                         f"({manifest.get('successful_traces')}/{manifest.get('expected_traces')} "
                         f"traced; failed positions {failed}) — re-run traffic to finish it")

    build = manifest.get("app_version")
    if not build:
        raise SystemExit(f"arm {arm_key(threshold)}: manifest has no app_version (build) — "
                         f"re-run traffic so the arm records the build it was generated on")

    plan = plan or traffic_plan()
    traces = tracing_module.load_traces(log)
    done, problems = index_positions(traces, plan, threshold, build)
    expected = set(entry["position"] for entry in plan)
    missing = sorted(expected - set(done))
    if problems or missing or len(traces) != len(plan):
        lines = problems[:20]
        if missing:
            lines.append(f"missing positions: {missing[:20]}")
        if len(traces) != len(plan):
            lines.append(f"{len(traces)} records in the log, {len(plan)} expected")
        raise SystemExit(f"arm {arm_key(threshold)}: log fails validation:\n  - "
                         + "\n  - ".join(lines))
    return traces, manifest


def pair_arms(high, low):
    """Pair every 0.85 trace with the 0.75 trace at the same traffic position.

    Position is the tag written at generation time, not file order, so a
    retried or interleaved write cannot silently shift the pairing. Both
    sides are checked to carry the same question, mode and top_k.
    """
    by_pos = {position_of(t): t for t in low}
    pairs = {}
    for trace in high:
        pos = position_of(trace)
        twin = by_pos.get(pos)
        if twin is None:
            raise SystemExit(f"no 0.75 twin at position {pos} for {trace['trace_id']}")
        same = (
            twin["query"] == trace["query"]
            and twin["request"].get("retrieval_mode") == trace["request"].get("retrieval_mode")
            and twin["request"].get("top_k") == trace["request"].get("top_k")
        )
        if not same:
            raise SystemExit(
                f"position {pos}: arms differ in question/mode/top_k — "
                f"{trace['query'][:40]!r} vs {twin['query'][:40]!r}; "
                f"the arms were not generated with the same traffic seed")
        pairs[trace["trace_id"]] = twin
    return pairs


def draw(traces, seed, n):
    """The Week 5 selection rule, unchanged: seeded sample over trace_id order."""
    pool = sorted(traces, key=lambda t: t["trace_id"])
    return random.Random(seed).sample(pool, min(n, len(pool)))


def outcome_label(trace):
    outcome = trace["outcome"]
    if not outcome["refused"]:
        return "answered"
    return "refused:" + (outcome["refusal_reason"] or "").split(" (")[0]


def gate_delta(high_trace, low_trace):
    """What changed between the arms for this question, in gate terms."""
    a, b = outcome_label(low_trace), outcome_label(high_trace)
    if a == b:
        return "same"
    return f"{a} -> {b}"


def review_template(seed, rows):
    lines = [
        "# Week 5 prediction — open coding notes (seed %d)" % seed,
        "",
        "Fill in every trace. One sentence describing what you saw; then the",
        "fields below it. `modes` takes any of M1 M2 M3 M4 M5, or NEW:<short-name>",
        "for a mode not in the Week 5 taxonomy, or `none`. `answerable` is",
        "whether the six articles contain the answer — exactly yes / no / unsure.",
        "",
        "The 0.75 twin is shown for context only. Code the 0.85 trace.",
        "Change no code while you read.",
        "",
        "Mode definitions (week5/taxonomy.md):",
    ] + [f"- {k}: {v}" for k, v in MODE_NAMES.items()] + ["", "---", ""]

    for index, row in enumerate(rows, start=1):
        lines += [
            f"### {index}. `{row['trace_id']}`",
            "",
            f"- **Question:** {row['query']}",
            f"- **Source kind:** {row['source_kind']} · mode `{row['retrieval_mode']}` · k={row['top_k']}",
            f"- **0.85 arm did:** {row['outcome_0_85']} · best distance {row['best_distance_0_85']}",
            f"- **0.75 twin did:** {row['outcome_0_75']} · best distance {row['best_distance_0_75']}",
            f"- **Changed by the threshold:** {row['gate_delta']}",
            f"- **Answer shown (0.85):** {(row['answer_0_85'] or '').strip()[:240]}",
            f"- **Raw model output (0.85):** {str(row['raw_output_0_85'] or '').strip()[:240]}",
            f"- **Output tokens (0.85):** {row['output_tokens_0_85']} "
            f"(generation limit {row['max_new_tokens']})",
            f"- **Top chunks (0.85):** {', '.join(row['top_chunks_0_85'])}",
            "",
            "**Observation:** _(one sentence)_",
            "",
            "answerable: ",
            "modes: ",
            "",
        ]
    return "\n".join(lines)


def cmd_sample(args, tracing_module=None):
    if tracing_module is None:
        import tracing as tracing_module

    plan = traffic_plan()
    high, high_manifest = validate_arm("0.85", tracing_module, plan)
    low, low_manifest = validate_arm("0.75", tracing_module, plan)
    if high_manifest.get("app_version") != low_manifest.get("app_version"):
        raise SystemExit(f"arms were generated on different builds "
                         f"({low_manifest.get('app_version')} vs {high_manifest.get('app_version')}); "
                         f"re-run one of them so the comparison is build-for-build")
    pairs = pair_arms(high, low)

    picked = draw(high, args.seed, args.n)

    rows = []
    for trace in picked:
        twin = pairs[trace["trace_id"]]
        params = trace.get("model", {}).get("params") or {}
        rows.append({
            "trace_id": trace["trace_id"],
            "twin_trace_id_0_75": twin["trace_id"],
            "position": position_of(trace),
            "query": trace["query"],
            "source_kind": str(trace.get("source") or "").split(":", 1)[-1],
            "retrieval_mode": trace["request"].get("retrieval_mode"),
            "top_k": trace["request"].get("top_k"),
            "threshold_0_85": trace["request"].get("threshold"),
            "threshold_0_75": twin["request"].get("threshold"),
            "outcome_0_85": outcome_label(trace),
            "outcome_0_75": outcome_label(twin),
            "best_distance_0_85": trace["outcome"].get("best_distance"),
            "best_distance_0_75": twin["outcome"].get("best_distance"),
            "gate_delta": gate_delta(trace, twin),
            "answer_0_85": trace["outcome"].get("answer"),
            "raw_output_0_85": trace["generation"].get("raw_output"),
            "output_tokens_0_85": trace["generation"].get("output_tokens"),
            "max_new_tokens": params.get("max_new_tokens"),
            "top_chunks_0_85": [c["chunk_id"] for c in trace["retrieval"][:3]],
        })

    def arm_ref(key, manifest):
        path = arm_manifest_path(key)
        return {
            "manifest": portable(path),
            "manifest_sha256": sha256_file(path),
            "log": manifest["log"],
            "log_sha256": manifest["log_sha256"],
            "traces": manifest["successful_traces"],
            "complete": manifest["complete"],
            "app_version": manifest["app_version"],
            "threshold": manifest["threshold"],
        }

    RUN_DIR.mkdir(parents=True, exist_ok=True)
    manifest = {
        "seed": args.seed,
        "n": len(picked),
        "population": len(high),
        "selection_rule": f"random.Random({args.seed}).sample(traces sorted by trace_id, {args.n})",
        "sampled_arm": "0.85",
        "arms": {"0.85": arm_ref("0.85", high_manifest), "0.75": arm_ref("0.75", low_manifest)},
        "app_version": high_manifest.get("app_version"),
        "drawn_at": utc_now(),
        "trace_ids": [r["trace_id"] for r in rows],
        "pairs": rows,
    }
    sample_path = RUN_DIR / f"sample_{args.seed}.json"
    sample_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    notes_path = RUN_DIR / f"notes_{args.seed}.md"
    if notes_path.exists() and not args.force:
        print(f"{notes_path} exists — not overwriting your notes (--force to regenerate)")
    else:
        notes_path.write_text(review_template(args.seed, rows), encoding="utf-8")
        print(f"review template: {notes_path}")

    changed = sum(1 for r in rows if r["gate_delta"] != "same")
    print(f"sample: {sample_path}")
    print(f"{len(rows)} traces drawn from {len(high)} (seed {args.seed}); "
          f"{changed} of them differ from their 0.75 twin at a gate")
    for index, r in enumerate(rows, start=1):
        print(f"  {index:>2}. {r['trace_id']}  {r['outcome_0_85']:<40} {r['gate_delta']}")
    print("\nNext: read the 20 and fill in observation / answerable / modes.")
    return 0


# ---------------------------------------------------------------------------
# score
# ---------------------------------------------------------------------------

LABEL_RE = re.compile(r"^(answerable|modes):\s*(.*)$", re.IGNORECASE)
HEAD_RE = re.compile(r"^###\s+\d+\.\s+`([0-9a-f]+)`")
NEW_MODE_RE = re.compile(r"^NEW:[A-Za-z0-9][A-Za-z0-9_-]*$", re.IGNORECASE)


def parse_labels(text):
    """Pull per-trace labels out of the reviewed notes file.

    Returns (labels, problems). labels is
    {trace_id: {"answerable": str, "modes": list | None, "observation": str}};
    an empty `modes:` or `answerable:` line leaves the trace unreviewed
    (None / ""), which `score` reports. Anything else that is not exactly a
    permitted value is a problem naming the trace, and `score` refuses to
    run while any exist.
    """
    labels = {}
    problems = []
    current = None
    for line in text.splitlines():
        head = HEAD_RE.match(line.strip())
        if head:
            current = head.group(1)
            if current in labels:
                problems.append(f"trace {current}: appears twice in the notes")
            labels[current] = {"answerable": "", "modes": None, "observation": ""}
            continue
        if current is None:
            continue
        if line.startswith("**Observation:**"):
            labels[current]["observation"] = line.split("**Observation:**", 1)[1].strip()
            continue
        match = LABEL_RE.match(line.strip())
        if not match:
            continue
        key, value = match.group(1).lower(), match.group(2).strip()

        if key == "answerable":
            if not value:
                continue                                   # unreviewed, not an error
            if value.lower() not in ANSWERABLE_VALUES:
                problems.append(f"trace {current}: answerable must be one of "
                                f"{'/'.join(ANSWERABLE_VALUES)}, got {value!r}")
                continue
            labels[current]["answerable"] = value.lower()
            continue

        if not value:
            continue                                       # unreviewed, not an error
        tokens = [t for t in value.replace(",", " ").split() if t]
        if [t.lower() for t in tokens] == ["none"]:
            labels[current]["modes"] = []
            continue
        modes = []
        bad = False
        for token in tokens:
            upper = token.upper()
            if upper in MODE_NAMES:
                modes.append(upper)
            elif NEW_MODE_RE.match(token):
                modes.append("NEW:" + token.split(":", 1)[1])
            elif upper == "NONE":
                bad = True
                problems.append(f"trace {current}: 'none' cannot be combined with other modes")
            else:
                bad = True
                problems.append(f"trace {current}: unknown mode token {token!r} "
                                f"(use M1..M5, NEW:<name>, or none)")
        if bad:
            continue
        seen = []
        for mode in modes:
            if mode not in seen:
                seen.append(mode)
        labels[current]["modes"] = seen
    return labels, problems


def truncated(output_tokens, max_new_tokens):
    if output_tokens is None or not max_new_tokens:
        return None
    return output_tokens >= max_new_tokens - TRUNCATION_MARGIN


def score(sample, labels, golden_hit_rate, golden_baseline=83.3):
    rows = sample["pairs"]
    n = len(rows)
    unlabelled = [r["trace_id"] for r in rows
                  if r["trace_id"] not in labels
                  or labels[r["trace_id"]]["modes"] is None
                  or not labels[r["trace_id"]]["answerable"]]

    per_trace = []
    mode_counts = {k: 0 for k in MODE_NAMES}
    new_modes = {}
    m1 = correct_refusals = refused_answerable = answered_unanswerable = 0
    m3_proxy = 0
    m3_proxy_known = 0

    for row in rows:
        lab = labels.get(row["trace_id"], {"answerable": "", "modes": [], "observation": ""})
        modes = lab["modes"] or []
        refused = row["outcome_0_85"] != "answered"
        answerable = lab["answerable"]
        for mode in modes:
            if mode in mode_counts:
                mode_counts[mode] += 1
            else:
                new_modes[mode] = new_modes.get(mode, 0) + 1
        if "M1" in modes:
            m1 += 1
        if refused and answerable == "no":
            correct_refusals += 1
        if refused and answerable == "yes":
            refused_answerable += 1
        if (not refused) and answerable == "no":
            answered_unanswerable += 1
        cut = truncated(row["output_tokens_0_85"], row["max_new_tokens"])
        if cut is not None:
            m3_proxy_known += 1
            m3_proxy += int(cut)
        per_trace.append({
            "trace_id": row["trace_id"], "position": row["position"],
            "outcome_0_85": row["outcome_0_85"], "outcome_0_75": row["outcome_0_75"],
            "gate_delta": row["gate_delta"], "answerable": answerable, "modes": modes,
            "truncation_proxy": cut, "observation": lab["observation"],
        })

    worst_new = max(new_modes.items(), key=lambda kv: kv[1]) if new_modes else None
    criteria = {
        "prediction_m1_at_most": PREDICTED_M1_MAX,
        "m1_count": m1,
        "m1_pct": round(100.0 * m1 / n, 1) if n else None,
        "baseline_m1_count": BASELINE_M1,
        "baseline_m1_pct": round(100.0 * BASELINE_M1 / 20, 1),
        "prediction_holds": None,
        "falsified_by": [],
    }
    if not unlabelled:
        if m1 >= FALSIFY_M1_AT:
            criteria["falsified_by"].append(
                f"1: M1 at {m1}/{n} (>= {FALSIFY_M1_AT})")
        if correct_refusals < MIN_CORRECT_REFUSALS:
            criteria["falsified_by"].append(
                f"2: correct refusals {correct_refusals}/{n} (< {MIN_CORRECT_REFUSALS})")
        if worst_new and worst_new[1] > m1:
            criteria["falsified_by"].append(
                f"3: new mode {worst_new[0]} at {worst_new[1]}/{n} exceeds M1 at {m1}/{n}")
        criteria["prediction_holds"] = not criteria["falsified_by"]

    side_checks = {
        "m3_human_count": mode_counts["M3"],
        "m3_truncation_proxy_count": m3_proxy,
        "m3_truncation_proxy_known": m3_proxy_known,
        "m3_proxy_rule": f"output_tokens >= max_new_tokens - {TRUNCATION_MARGIN} (machine hint, not a label)",
        "golden_hit_rate_at_3": golden_hit_rate,
        "golden_reference": golden_baseline,
        "golden_unchanged": (abs(golden_hit_rate - golden_baseline) < 0.05
                            if golden_hit_rate is not None else None),
        "golden_note": ("prediction.md pinned 75.0%; the Week 4 label correction "
                        "(results.md, 2026-10-11) makes 83.3% the current reference. "
                        "Retrieval-only metric: the gate cannot move it."),
        "answered_but_labelled_unanswerable": answered_unanswerable,
        "refused_but_labelled_answerable": refused_answerable,
    }
    return {
        "scored_at": utc_now(),
        "seed": sample["seed"],
        "n": n,
        "unlabelled": unlabelled,
        "criteria": criteria,
        "mode_counts": mode_counts,
        "new_modes": new_modes,
        "correct_refusals": correct_refusals,
        "side_checks": side_checks,
        "per_trace": per_trace,
    }


def golden_reference():
    if not GOLDEN_RESULTS.exists():
        return None
    data = json.loads(GOLDEN_RESULTS.read_text(encoding="utf-8"))
    return data.get("improved", {}).get("hit_rate")


def check_sample_provenance(sample):
    """A sample is scorable only if it was drawn from two complete arms whose
    manifests still exist and are unchanged."""
    arms = sample.get("arms")
    if not arms or set(arms) != set(ARMS):
        raise SystemExit("sample manifest has no arm provenance — re-run `sample`")
    for key, ref in arms.items():
        if not ref.get("complete"):
            raise SystemExit(f"sample was drawn from an incomplete {key} arm — re-run traffic and sample")
        path = resolve(ref["manifest"])
        if not path.exists():
            raise SystemExit(f"arm manifest {ref['manifest']} is missing")
        if sha256_file(path) != ref.get("manifest_sha256"):
            raise SystemExit(f"arm manifest {ref['manifest']} changed since the sample was drawn — "
                             f"re-run `sample`")


def cmd_score(args):
    sample_path = RUN_DIR / f"sample_{args.seed}.json"
    notes_path = RUN_DIR / f"notes_{args.seed}.md"
    for path in (sample_path, notes_path):
        if not path.exists():
            raise SystemExit(f"missing {path}: run `sample` and review the notes first")

    sample = json.loads(sample_path.read_text(encoding="utf-8"))
    check_sample_provenance(sample)

    labels, problems = parse_labels(notes_path.read_text(encoding="utf-8"))
    missing = [tid for tid in sample["trace_ids"] if tid not in labels]
    for tid in missing:
        problems.append(f"trace {tid}: heading not found in the notes file")
    if problems:
        print(f"{notes_path} has {len(problems)} label problem(s); fix them before scoring:")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    report = score(sample, labels, golden_reference())

    out = RUN_DIR / f"result_{args.seed}.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")

    c = report["criteria"]
    print("=" * 78)
    print(f"WEEK 5 PREDICTION — scored over {report['n']} traces, seed {report['seed']}")
    print("=" * 78)
    if report["unlabelled"]:
        print(f"UNLABELLED: {len(report['unlabelled'])} trace(s) have an empty modes/answerable "
              f"line — no verdict until every trace is coded:")
        for tid in report["unlabelled"]:
            print(f"   {tid}")
    print(f"M1            : {c['m1_count']}/{report['n']} ({c['m1_pct']}%)  "
          f"baseline {c['baseline_m1_count']}/20 ({c['baseline_m1_pct']}%)  "
          f"predicted <= {c['prediction_m1_at_most']}")
    print(f"correct refusals: {report['correct_refusals']}/{report['n']}  (must be >= {MIN_CORRECT_REFUSALS})")
    print(f"modes         : " + ", ".join(f"{k}={v}" for k, v in report["mode_counts"].items())
          + (f"  new: {report['new_modes']}" if report["new_modes"] else ""))
    s = report["side_checks"]
    print(f"M3 human {s['m3_human_count']} · truncation proxy {s['m3_truncation_proxy_count']}"
          f"/{s['m3_truncation_proxy_known']}")
    print(f"golden hit@3  : {s['golden_hit_rate_at_3']} (reference {s['golden_reference']}, "
          f"unchanged={s['golden_unchanged']})")
    if c["prediction_holds"] is None:
        print("\nVERDICT: not yet — finish labelling")
    elif c["prediction_holds"]:
        print("\nVERDICT: prediction holds on this sample")
    else:
        print("\nVERDICT: prediction FAILED — " + "; ".join(c["falsified_by"]))
    print(f"\nWritten to {out}")
    return 0


# ---------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    traffic = sub.add_parser("traffic", help="run or resume one arm into an isolated log")
    traffic.add_argument("--threshold", type=float, required=True, choices=[0.75, 0.85])
    traffic.add_argument("--force", action="store_true", help="discard the arm's log and manifest and start again")
    traffic.add_argument("--langfuse", action="store_true",
                         help="also send this arm's traces to Langfuse (off by default)")
    traffic.set_defaults(func=cmd_traffic)

    sample = sub.add_parser("sample", help="validate both arms, draw the 20 from 0.85, pair with 0.75")
    sample.add_argument("--seed", type=int, default=SAMPLE_SEED)
    sample.add_argument("--n", type=int, default=SAMPLE_N)
    sample.add_argument("--force", action="store_true", help="overwrite an existing notes file")
    sample.set_defaults(func=cmd_sample)

    scored = sub.add_parser("score", help="validate the reviewed notes and evaluate the committed criteria")
    scored.add_argument("--seed", type=int, default=SAMPLE_SEED)
    scored.set_defaults(func=cmd_score)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
