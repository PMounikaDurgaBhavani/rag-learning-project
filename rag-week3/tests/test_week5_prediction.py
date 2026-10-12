"""Checks for experiments/week5_prediction.py that need no model, no index
and no real trace log — synthetic traces in a temporary directory only.

    python tests/test_week5_prediction.py
"""

import argparse
import hashlib
import json
import random
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "experiments"))
sys.path.insert(0, str(ROOT / "src"))

import week5_prediction as wp

failures = []


def check(name, condition, detail=""):
    print(f"  {'PASS' if condition else 'FAIL'}  {name} {detail if not condition else ''}")
    if not condition:
        failures.append(name)


def expect_exit(name, fn, needle):
    try:
        fn()
        check(name, False, "no SystemExit raised")
    except SystemExit as error:
        check(name, needle in str(error), f"message was: {error}")


# ---------------------------------------------------------------------------
# synthetic plan, traces, logs and manifests
# ---------------------------------------------------------------------------

N = 92


def synthetic_plan():
    rng = random.Random(1)
    return [{"position": p, "question": f"question {p}", "kind": "golden",
             "mode": rng.choice(wp.TRAFFIC_MODES), "top_k": rng.choice(wp.TRAFFIC_TOP_K)}
            for p in range(1, N + 1)]


PLAN = synthetic_plan()
wp.traffic_plan = lambda: PLAN          # never touch evaluation files or the model here


BUILD = "testbuild"


def fake_trace(entry, threshold, refused=False, reason=None, best=0.5, raw="an answer",
               tokens=12, position=None, query=None, mode=None, top_k=None, source=None,
               build=BUILD):
    pos = position if position is not None else entry["position"]
    return {
        "trace_id": f"{random.Random(pos * 1000 + int(threshold * 100)).getrandbits(64):016x}",
        "app_version": build,
        "source": source or f"{wp.SOURCE_PREFIX}{entry['kind']}",
        "query": query or entry["question"],
        "tags": ["week5-prediction", f"threshold:{threshold}", f"position:{pos}"],
        "request": {"retrieval_mode": mode or entry["mode"], "top_k": top_k or entry["top_k"],
                    "threshold": threshold},
        "model": {"params": {"max_new_tokens": 160}},
        "retrieval": [{"chunk_id": "HC-001-markdown-1"}],
        "generation": {"raw_output": raw, "output_tokens": tokens},
        "outcome": {"refused": refused, "refusal_reason": reason,
                    "best_distance": best, "answer": None if refused else raw},
    }


def build_arms():
    low, high = [], []
    for entry in PLAN:
        pos = entry["position"]
        best = 0.70 + (pos % 5) * 0.03         # 0.70 .. 0.82
        low.append(fake_trace(entry, 0.75, best > 0.75,
                              f"distance_gate ({best} > 0.75)" if best > 0.75 else None, best))
        high.append(fake_trace(entry, 0.85, False, None, best,
                               tokens=159 if pos == 3 else 12))
    return low, high


class FakeTracing:
    """Just enough of src/tracing.py for validate_arm and cmd_sample."""

    @staticmethod
    def load_traces(path):
        path = Path(path)
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    @staticmethod
    def app_version():
        return "testbuild"

    @staticmethod
    def langfuse_enabled():
        return False


def write_log(path, traces):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(t) + "\n" for t in traces), encoding="utf-8")


def write_arm(threshold, traces, complete=None, failed=None, app_version="testbuild"):
    """Write a log and a manifest the way cmd_traffic would after a run."""
    log = wp.arm_log(threshold)
    write_log(log, traces)
    done, _ = wp.index_positions(traces, PLAN, threshold)
    failed = failed if failed is not None else sorted(set(range(1, N + 1)) - set(done))
    manifest = {
        "arm": wp.arm_key(threshold), "threshold": float(threshold), "production_threshold": 0.75,
        "app_version": app_version, "traffic_seed": wp.TRAFFIC_SEED, "runs": wp.TRAFFIC_RUNS,
        "pool_size": N // 2, "expected_traces": N, "successful_traces": len(done),
        "successful_positions": sorted(done),
        "failed_positions": [{"position": p, "error": "boom"} for p in failed],
        "complete": (len(done) == N and not failed) if complete is None else complete,
        "langfuse": False, "log": wp.portable(log), "log_sha256": wp.sha256_file(log),
        "sessions": [{"started_at": "t", "finished_at": "t", "elapsed_s": 1.0, "calls": len(done), "failed": failed}],
        "elapsed_s": 1.0, "written_at": "t",
    }
    wp.RUN_DIR.mkdir(parents=True, exist_ok=True)
    wp.arm_manifest_path(threshold).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def reset(tmp):
    """Point the module at a fresh temporary run dir and trace root."""
    wp.RUN_DIR = Path(tmp) / "prediction_run"
    wp.TRACE_ROOT = Path(tmp) / "traces"


def sample_args(seed=20260915, n=20, force=False):
    return argparse.Namespace(seed=seed, n=n, force=force)


# ---------------------------------------------------------------------------

with tempfile.TemporaryDirectory() as tmp:
    reset(tmp)
    low, high = build_arms()

    print("Plan and position indexing")
    check("synthetic plan has 92 positions", len(PLAN) == N)
    done, problems = wp.index_positions(high, PLAN, 0.85)
    check("complete log indexes every position with no problems",
          len(done) == N and problems == [])
    check("positions cover 1..92 exactly", set(done) == set(range(1, N + 1)))

    dup = high + [fake_trace(PLAN[4], 0.85)]
    _, problems = wp.index_positions(dup, PLAN, 0.85)
    check("duplicate position is reported", any("duplicate" in p for p in problems))

    wrong_q = high[:10] + [fake_trace(PLAN[10], 0.85, query="something else")] + high[11:]
    _, problems = wp.index_positions(wrong_q, PLAN, 0.85)
    check("question mismatch is reported", any("question differs" in p for p in problems))

    wrong_mode = high[:10] + [fake_trace(PLAN[10], 0.85, mode="nonexistent")] + high[11:]
    _, problems = wp.index_positions(wrong_mode, PLAN, 0.85)
    check("mode/top_k mismatch is reported", any("mode/top_k" in p for p in problems))

    wrong_thr = high[:10] + [fake_trace(PLAN[10], 0.75)] + high[11:]
    _, problems = wp.index_positions(wrong_thr, PLAN, 0.85)
    check("threshold mismatch is reported", any("request.threshold" in p for p in problems))

    out_of_range = high + [fake_trace(PLAN[0], 0.85, position=500)]
    _, problems = wp.index_positions(out_of_range, PLAN, 0.85)
    check("position outside the plan is reported", any("outside the plan" in p for p in problems))

    foreign = high[:10] + [fake_trace(PLAN[10], 0.85, source="ui")] + high[11:]
    _, problems = wp.index_positions(foreign, PLAN, 0.85)
    check("non-experiment source is reported", any("not experiment traffic" in p for p in problems))

    untagged = [dict(t, tags=[]) for t in high[:1]]
    _, problems = wp.index_positions(untagged, PLAN, 0.85)
    check("missing position tag is reported", any("no position tag" in p for p in problems))

    print("\nBuild identifier (app_version) checks")
    done, problems = wp.index_positions(high, PLAN, 0.85, BUILD)
    check("all records on the expected build are accepted", len(done) == N and problems == [])

    mixed = high[:10] + [fake_trace(PLAN[10], 0.85, build="otherbuild")] + high[11:]
    done, problems = wp.index_positions(mixed, PLAN, 0.85, BUILD)
    check("one record on a different build is rejected",
          any("app_version 'otherbuild' is not the arm's build 'testbuild'" in p for p in problems)
          and 11 not in done)

    nobuild = high[:10] + [dict(high[10], app_version=None)] + high[11:]
    done, problems = wp.index_positions(nobuild, PLAN, 0.85, BUILD)
    check("one record with no build identifier is rejected",
          any("no app_version" in p for p in problems) and 11 not in done)

    absent = high[:10] + [{k: v for k, v in high[10].items() if k != "app_version"}] + high[11:]
    _, problems = wp.index_positions(absent, PLAN, 0.85, BUILD)
    check("a record whose app_version key is absent is rejected", any("no app_version" in p for p in problems))

    _, problems = wp.index_positions(mixed, PLAN, 0.85)
    check("build check is skipped only when no expected build is given", problems == [])

    print("\nArm validation (validate_arm)")
    expect_exit("missing manifest is rejected",
                lambda: wp.validate_arm("0.85", FakeTracing, PLAN), "missing arm manifest")

    write_arm(0.85, high[:80])
    expect_exit("fewer than 92 traces is rejected (manifest incomplete)",
                lambda: wp.validate_arm("0.85", FakeTracing, PLAN), "incomplete")

    write_arm(0.85, high[:80], complete=True, failed=[])      # a lying manifest
    expect_exit("fewer than 92 traces is rejected even if the manifest claims complete",
                lambda: wp.validate_arm("0.85", FakeTracing, PLAN), "missing positions")

    write_arm(0.85, high, complete=False, failed=[7])
    expect_exit("failed positions are rejected",
                lambda: wp.validate_arm("0.85", FakeTracing, PLAN), "failed positions [7]")

    write_arm(0.85, high + [fake_trace(PLAN[4], 0.85)], complete=True, failed=[])
    expect_exit("duplicate positions are rejected",
                lambda: wp.validate_arm("0.85", FakeTracing, PLAN), "duplicate")

    write_arm(0.85, high)
    wp.arm_log(0.85).write_text(wp.arm_log(0.85).read_text() + "\n", encoding="utf-8")
    expect_exit("log changed after manifest is rejected",
                lambda: wp.validate_arm("0.85", FakeTracing, PLAN), "has changed")

    write_arm(0.85, high[:10] + [fake_trace(PLAN[10], 0.85, build="otherbuild")] + high[11:])
    expect_exit("mixed-build log cannot be sampled",
                lambda: wp.validate_arm("0.85", FakeTracing, PLAN), "is not the arm's build")

    write_arm(0.85, high[:10] + [dict(high[10], app_version=None)] + high[11:])
    expect_exit("log with a build-less record cannot be sampled",
                lambda: wp.validate_arm("0.85", FakeTracing, PLAN), "no app_version")

    write_arm(0.85, high, app_version=None)
    expect_exit("manifest without a build identifier cannot be sampled",
                lambda: wp.validate_arm("0.85", FakeTracing, PLAN), "manifest has no app_version")

    write_arm(0.85, high)
    traces, manifest = wp.validate_arm("0.85", FakeTracing, PLAN)
    check("complete arm validates", len(traces) == N and manifest["complete"] is True)

    print("\nPairing")
    pairs = wp.pair_arms(high, low)
    check("every 0.85 trace has a 0.75 twin", len(pairs) == len(high))
    check("twins share question, mode and top_k", all(
        pairs[t["trace_id"]]["query"] == t["query"]
        and pairs[t["trace_id"]]["request"]["retrieval_mode"] == t["request"]["retrieval_mode"]
        and pairs[t["trace_id"]]["request"]["top_k"] == t["request"]["top_k"] for t in high))
    bad = [dict(t) for t in low]
    bad[4] = dict(bad[4], query="different question")
    expect_exit("mismatched arms are rejected", lambda: wp.pair_arms(high, bad), "differ")

    print("\nSampling (cmd_sample)")
    write_arm(0.85, high)
    expect_exit("sample refuses when the 0.75 arm is missing",
                lambda: wp.cmd_sample(sample_args(), FakeTracing), "missing arm manifest")

    write_arm(0.75, low[:50])
    expect_exit("sample refuses when the 0.75 arm is incomplete",
                lambda: wp.cmd_sample(sample_args(), FakeTracing), "incomplete")

    low_other = [dict(t, app_version="otherbuild") for t in low]    # a consistent arm on another build
    write_arm(0.75, low_other, app_version="otherbuild")
    expect_exit("sample refuses when arms come from different builds",
                lambda: wp.cmd_sample(sample_args(), FakeTracing), "different builds")

    write_arm(0.75, low)
    rc = wp.cmd_sample(sample_args(), FakeTracing)
    sample_path = wp.RUN_DIR / "sample_20260915.json"
    notes_path = wp.RUN_DIR / "notes_20260915.md"
    check("sample succeeds on two complete arms", rc == 0 and sample_path.exists() and notes_path.exists())
    sample = json.loads(sample_path.read_text(encoding="utf-8"))
    check("20 traces drawn from a population of 92", sample["n"] == 20 and sample["population"] == N)
    expected_ids = [t["trace_id"] for t in wp.draw(high, 20260915, 20)]
    check("draw matches the seeded rule", sample["trace_ids"] == expected_ids)
    check("every sampled trace carries its 0.75 twin and position",
          all(r["twin_trace_id_0_75"] and r["position"] for r in sample["pairs"]))
    check("sample references both arm manifests with hashes and log hashes", all(
        sample["arms"][k]["manifest_sha256"] == wp.sha256_file(wp.arm_manifest_path(k))
        and sample["arms"][k]["log_sha256"] == wp.sha256_file(wp.arm_log(k))
        and sample["arms"][k]["complete"] is True for k in wp.ARMS))
    check("arm manifests live in the run dir, not under traces/",
          wp.arm_manifest_path("0.85").parent == wp.RUN_DIR)
    manifest = json.loads(wp.arm_manifest_path("0.85").read_text())
    check("arm manifest preserves provenance fields", all(k in manifest for k in (
        "traffic_seed", "runs", "pool_size", "elapsed_s", "threshold", "successful_positions",
        "failed_positions", "langfuse", "log", "log_sha256", "app_version")))

    print("\nSeeded draw")
    a = wp.draw(high, 20260915, 20)
    b = wp.draw(list(reversed(high)), 20260915, 20)
    check("draw is reproducible regardless of file order",
          [t["trace_id"] for t in a] == [t["trace_id"] for t in b])

    print("\nHashing")
    p = Path(tmp) / "x.jsonl"
    p.write_text("a\nb\n", encoding="utf-8")
    check("sha256 of a known file", wp.sha256_file(p) == hashlib.sha256(b"a\nb\n").hexdigest())

    print("\nTemplate and label parsing")
    template = notes_path.read_text(encoding="utf-8")
    ids = sample["trace_ids"]
    check("template has one heading per trace", template.count("\n### ") == 20)
    check("template has empty label lines",
          template.count("\nmodes: \n") == 20 and template.count("\nanswerable: \n") == 20)
    check("template never pre-fills a mode", "modes: M" not in template)
    check("template does not call a token count 'cut off'", "cut off" not in template)

    labels, problems = wp.parse_labels(template)
    check("unfilled template parses as unreviewed, not as an error",
          problems == [] and all(v["modes"] is None and v["answerable"] == "" for v in labels.values()))

    def fill(text, tid, answerable, modes):
        start = text.index(f"`{tid}`")
        end = text.find("\n### ", start)
        end = len(text) if end == -1 else end
        seg = text[start:end]
        seg = seg.replace("answerable: \n", f"answerable: {answerable}\n", 1)
        seg = seg.replace("modes: \n", f"modes: {modes}\n", 1)
        return text[:start] + seg + text[end:]

    filled = fill(template, ids[0], "yes", "M1")
    filled = fill(filled, ids[1], "no", "none")
    filled = fill(filled, ids[2], "yes", "M3, NEW:echoes-header")
    filled = fill(filled, ids[3], "Unsure", "m2")
    for tid in ids[4:]:
        filled = fill(filled, tid, "yes", "none")
    labels, problems = wp.parse_labels(filled)
    check("valid labels produce no problems", problems == [])
    check("M1 label parsed", labels[ids[0]]["modes"] == ["M1"])
    check("'none' parses as empty list", labels[ids[1]]["modes"] == [])
    check("new mode token kept with NEW: prefix", labels[ids[2]]["modes"] == ["M3", "NEW:echoes-header"])
    check("case-insensitive values are normalised",
          labels[ids[3]]["answerable"] == "unsure" and labels[ids[3]]["modes"] == ["M2"])

    _, problems = wp.parse_labels(fill(template, ids[0], "y", "M1"))
    check("answerable typo is rejected with the trace id",
          any(p.startswith(f"trace {ids[0]}:") and "answerable" in p for p in problems))
    _, problems = wp.parse_labels(fill(template, ids[0], "yes", "M6"))
    check("unknown mode M6 is rejected", any("unknown mode token 'M6'" in p for p in problems))
    _, problems = wp.parse_labels(fill(template, ids[0], "yes", "M!"))
    check("malformed mode token is rejected", any("unknown mode token" in p for p in problems))
    _, problems = wp.parse_labels(fill(template, ids[0], "yes", "echoes-header"))
    check("new mode without NEW: prefix is rejected", any("unknown mode token" in p for p in problems))
    _, problems = wp.parse_labels(fill(template, ids[0], "yes", "NEW:"))
    check("empty NEW: name is rejected", any("unknown mode token" in p for p in problems))
    _, problems = wp.parse_labels(fill(template, ids[0], "yes", "none, M1"))
    check("'none' combined with a mode is rejected", any("cannot be combined" in p for p in problems))
    labels, _ = wp.parse_labels(fill(template, ids[0], "yes", "M1 M1"))
    check("duplicate mode tokens are de-duplicated", labels[ids[0]]["modes"] == ["M1"])

    print("\nScoring (score / cmd_score)")
    labels, _ = wp.parse_labels(filled)
    rows = sample["pairs"]
    rows[1]["outcome_0_85"] = "refused:distance_gate"       # so its 'no' is a correct refusal
    report = wp.score(sample, labels, golden_hit_rate=83.3)
    c = report["criteria"]
    check("no unlabelled traces", report["unlabelled"] == [])
    check("M1 counted from human labels only", c["m1_count"] == 1)
    check("denominator is the sample size", report["n"] == 20 and c["m1_pct"] == 5.0)
    check("prediction holds on M1 but fails on refusals (< 4)",
          c["prediction_holds"] is False and any(f.startswith("2:") for f in c["falsified_by"]))
    check("'unsure' counts toward neither refusal criterion", report["correct_refusals"] == 1)
    check("new mode recorded", report["new_modes"] == {"NEW:echoes-header": 1})
    check("M3 human count separate from proxy",
          report["mode_counts"]["M3"] == 1 and "m3_truncation_proxy_count" in report["side_checks"]
          and "machine hint" in report["side_checks"]["m3_proxy_rule"])
    check("golden reference unchanged at 83.3", report["side_checks"]["golden_unchanged"] is True)
    check("baseline is 4/20 and predicted max is 1",
          c["baseline_m1_count"] == 4 and c["prediction_m1_at_most"] == 1)

    two = fill(fill(template, ids[0], "yes", "M1"), ids[1], "yes", "M1")
    for tid in ids[2:]:
        two = fill(two, tid, "no", "none")
    rows2 = [dict(r, outcome_0_85="refused:distance_gate") for r in rows]
    labels2, _ = wp.parse_labels(two)
    rep2 = wp.score({"seed": 1, "pairs": rows2}, labels2, 83.3)
    check("M1=2 falsifies via condition 1",
          rep2["criteria"]["prediction_holds"] is False and rep2["criteria"]["falsified_by"][0].startswith("1:"))

    three = fill(template, ids[0], "yes", "M1")
    for tid in ids[1:6]:
        three = fill(three, tid, "no", "NEW:loops")
    for tid in ids[6:]:
        three = fill(three, tid, "no", "none")
    labels3, _ = wp.parse_labels(three)
    rep3 = wp.score({"seed": 1, "pairs": rows2}, labels3, 83.3)
    check("a new mode more frequent than M1 falsifies via condition 3",
          any(f.startswith("3:") for f in rep3["criteria"]["falsified_by"]))

    partial = fill(template, ids[0], "yes", "M1")
    labels4, _ = wp.parse_labels(partial)
    rep4 = wp.score(sample, labels4, 83.3)
    check("partial labelling yields no verdict",
          rep4["criteria"]["prediction_holds"] is None and len(rep4["unlabelled"]) == 19)

    # cmd_score end to end: invalid labels block scoring; valid labels write a result
    notes_path.write_text(fill(template, ids[0], "maybe", "M1"), encoding="utf-8")
    rc = wp.cmd_score(sample_args())
    check("cmd_score refuses invalid labels and writes no result",
          rc == 1 and not (wp.RUN_DIR / "result_20260915.json").exists())
    notes_path.write_text(template, encoding="utf-8")
    rc = wp.cmd_score(sample_args())
    check("cmd_score on a blank review writes no result and exits non-zero",
          rc == 2 and not (wp.RUN_DIR / "result_20260915.json").exists())
    notes_path.write_text(partial, encoding="utf-8")
    rc = wp.cmd_score(sample_args())
    check("cmd_score on a partial review (1/20) writes no result and exits non-zero",
          rc == 2 and not (wp.RUN_DIR / "result_20260915.json").exists())
    notes_path.write_text(filled, encoding="utf-8")
    rc = wp.cmd_score(sample_args())
    check("cmd_score writes a result for valid labels",
          rc == 0 and (wp.RUN_DIR / "result_20260915.json").exists())

    # provenance: a sample drawn from an incomplete arm, or a changed manifest, cannot be scored
    tampered = dict(sample)
    tampered["arms"] = {k: dict(v, complete=False) for k, v in sample["arms"].items()}
    sample_path.write_text(json.dumps(tampered), encoding="utf-8")
    expect_exit("sample from an incomplete arm cannot be scored",
                lambda: wp.cmd_score(sample_args()), "incomplete")
    sample_path.write_text(json.dumps(sample), encoding="utf-8")
    mpath = wp.arm_manifest_path("0.85")
    mpath.write_text(mpath.read_text(encoding="utf-8").replace('"testbuild"', '"otherbuild"'),
                     encoding="utf-8")                      # manifest bytes change -> hash changes
    expect_exit("changed arm manifest blocks scoring",
                lambda: wp.cmd_score(sample_args()), "changed since the sample was drawn")

    print("\nIsolation constants")
    check("arm logs live under traces/<root>/t<threshold>",
          str(wp.arm_log(0.85)).endswith("traces/t0.85/traces.jsonl"))
    check("committed criteria constants", (wp.PREDICTED_M1_MAX, wp.FALSIFY_M1_AT,
                                          wp.MIN_CORRECT_REFUSALS, wp.BASELINE_M1) == (1, 2, 4, 4))
    check("traffic seed matches generate_traffic default", wp.TRAFFIC_SEED == 7)

print()
if failures:
    print(f"{len(failures)} check(s) failed: {failures}")
    sys.exit(1)
print("All checks passed.")
