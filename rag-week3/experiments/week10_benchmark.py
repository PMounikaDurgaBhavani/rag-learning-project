"""Week 10: the single agent against the manager + two specialists, on ten
frozen Week 6 cases, with the Week 6 judge and assertions grading both.

    python main.py week10 manifest                 # choose the 10 cases -> week10/cases_10.json
    python main.py week10 run --arm both           # both arms -> week10/runs/{single,multi}_*.json
    python main.py week10 judge                    # assertions + judge -> week10/results_{single,multi}.json
    python main.py week10 report                   # comparison.{json,csv}, race_table.md, handoffs.log
    python main.py week10 failure --case T07 --specialist ticket_specialist   # HTTP 500 experiment
    python main.py week10 all                      # manifest, run, judge, report, failure

Measurement rules, so the numbers mean what the table says:

- Both arms get the same ticket, the same retrieval configuration, the same
  model and decoding settings, and never the reference facts.
- Latency is sequential wall-clock per case, end to end (retrieval and every
  model call), on a model that was warmed up first; the warm-up is recorded
  separately as the cold start.
- Tokens are counted per model call with the model's own tokenizer and
  summed, so the manager's re-sent context is counted each time it is sent.
  Cost is tokens times the Week 7 stand-in rate card, never a provider charge.
- p50 is the median (as in Weeks 7 and 8); p95 and p99 are nearest-rank,
  which with n=10 are both the slowest case. They are reported because the
  brief asks, and labelled for what they are.
- Pass = every applicable deterministic assertion passes AND the Week 6
  judge says PASS on RESOLUTION (model-based, agreement with blind human
  labels recorded in week6/agreement.json).
"""

import argparse
import csv
import json
import os
import re
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "experiments"))
os.chdir(ROOT)

import tracing                                   # noqa: E402
from agent_runtime import RATE_CARD, cost_usd    # noqa: E402

W6 = ROOT / "week6"
W10 = ROOT / "week10"
RUNS = W10 / "runs"
CASES_FILE = W6 / "eval_cases.jsonl"
FEW_SHOT_FILE = W6 / "judge_v2_examples.json"
MANIFEST = W10 / "cases_10.json"
RESULTS = {"single": W10 / "results_single.json", "multi": W10 / "results_multi.json"}
COMPARISON_JSON = W10 / "comparison.json"
COMPARISON_CSV = W10 / "comparison.csv"
RACE_TABLE = W10 / "race_table.md"
HANDOFFS_LOG = W10 / "handoffs.log"
FAILURE_JSON = W10 / "failure_case.json"
FAILURE_MD = W10 / "failure_case.md"

ARMS = ("single", "multi")
MODES = ("M1", "M2", "M3", "M4", "M5")
ARTICLE_ID = re.compile(r"\bHC-\d{3}\b")

SELECTION_RULE = (
    "For each Week 5 mode M1..M5 in order: (1) the first case in week6/eval_cases.jsonl file "
    "order whose id is not one of judge v2's few-shot examples; (2) the first policy-backed case "
    "in file order not already chosen and not a few-shot example, or, if the mode has none, the "
    "next case in file order. Two cases per mode, ten in all. Expected answers are untouched."
)


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, indent=2, default=str) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# the ten cases
# ---------------------------------------------------------------------------

def load_week6_cases():
    return [json.loads(line) for line in CASES_FILE.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def few_shot_ids():
    if FEW_SHOT_FILE.exists():
        return list(read_json(FEW_SHOT_FILE).get("examples", []))
    return []


def select_cases(cases, excluded):
    """Deterministic: SELECTION_RULE applied to the file order."""
    by_mode = {mode: [c for c in cases if c["mode"] == mode] for mode in MODES}
    chosen = []
    for mode in MODES:
        pool = [c for c in by_mode[mode] if c["id"] not in excluded]
        if len(pool) < 2:
            raise SystemExit(f"mode {mode} has fewer than 2 eligible cases")
        first = pool[0]
        policy = next((c for c in pool[1:] if c.get("policy_backed")), None)
        second = policy or pool[1]
        chosen.extend([first, second])
    return chosen


def build_manifest():
    cases = load_week6_cases()
    excluded = few_shot_ids()
    chosen = select_cases(cases, excluded)
    return {
        "created_at": now_iso(),
        "app_version": tracing.app_version(),
        "source": "week6/eval_cases.jsonl",
        "source_sha": tracing.sha256(CASES_FILE.read_text(encoding="utf-8")),
        "selection_rule": SELECTION_RULE,
        "excluded_few_shot_ids": excluded,
        "excluded_reason": "judge_v2.txt shows these cases with their human verdict as worked examples",
        "n": len(chosen),
        "case_ids": [c["id"] for c in chosen],
        "cases": [{"id": c["id"], "mode": c["mode"], "policy_backed": c.get("policy_backed", False),
                   "regression": bool(c.get("regression")), "ticket_id": c["ticket"]["ticket_id"],
                   "tier": c["ticket"]["tier"], "message": c["ticket"]["message"],
                   "reference_articles": sorted(set(ARTICLE_ID.findall(" ".join(c["reference"]))))}
                  for c in chosen],
    }


def manifest_cases():
    """The ten full cases named by the committed manifest, in manifest order."""
    if not MANIFEST.exists():
        raise SystemExit("No manifest. Run: python main.py week10 manifest")
    manifest = read_json(MANIFEST)
    by_id = {c["id"]: c for c in load_week6_cases()}
    missing = [i for i in manifest["case_ids"] if i not in by_id]
    if missing:
        raise SystemExit(f"manifest names cases that are not in eval_cases.jsonl: {missing}")
    current_sha = tracing.sha256(CASES_FILE.read_text(encoding="utf-8"))
    if current_sha != manifest["source_sha"]:
        raise SystemExit("week6/eval_cases.jsonl changed since the manifest was written; "
                         "regenerate the manifest or restore the file")
    return manifest, [by_id[i] for i in manifest["case_ids"]]


def cmd_manifest(args):
    manifest = build_manifest()
    if MANIFEST.exists() and not args.force:
        existing = read_json(MANIFEST)
        if existing["case_ids"] == manifest["case_ids"] and existing["source_sha"] == manifest["source_sha"]:
            print(f"Manifest unchanged: {manifest['case_ids']}")
            return 0
        print("A different manifest exists. Re-run with --force to replace it.")
        return 1
    write_json(MANIFEST, manifest)
    print(f"Wrote {MANIFEST.relative_to(ROOT)}: {manifest['case_ids']}")
    for c in manifest["cases"]:
        print(f"  {c['id']:<4} {c['mode']} {'policy' if c['policy_backed'] else 'kb    '} "
              f"{'regression' if c['regression'] else '          '} {c['ticket_id']} {c['message'][:60]}")
    return 0


# ---------------------------------------------------------------------------
# running the arms
# ---------------------------------------------------------------------------

def warm_up():
    """Load the drafter, the embedder, the BM25 index and the cross-encoder, and
    run each once, so no case in either arm pays for loading weights."""
    from multi_agent import default_anchors, default_retriever
    from ticket_reply import get_ticket_pipeline
    started = time.perf_counter()
    generate = get_ticket_pipeline()
    generate([{"role": "user", "content": "ok"}], max_new_tokens=1, do_sample=False,
             return_full_text=False)
    chunks = default_retriever("warm up the retrievers", top_k=1)
    default_anchors("warm up the retrievers", chunks)
    return round((time.perf_counter() - started) * 1000.0, 1)


def citation_rows(markers, numbered):
    out = []
    for m in markers:
        if 0 < m <= len(numbered):
            c = numbered[m - 1]
            out.append({"marker": m, "chunk_id": c.get("chunk_id"), "article_id": c.get("article_id"),
                        "section": c.get("section")})
    return out


def run_single(case, session_id):
    from ticket_reply import draft_ticket_reply
    ticket = case["ticket"]
    started = time.perf_counter()
    r = draft_ticket_reply(ticket, source="week10-single", session_id=session_id,
                           tags=["week10", "arm:single", f"case:{case['id']}"])
    latency_ms = round((time.perf_counter() - started) * 1000.0, 1)
    answered = not r["refused"]
    input_tokens = r.get("input_tokens") if answered else 0
    output_tokens = r.get("output_tokens") if answered else 0
    reason = r.get("refusal_reason") or ""
    return {
        "arm": "single", "answer": r["answer"], "refused": r["refused"], "refusal_reason": reason or None,
        "not_found": bool(r["refused"]) and not reason.startswith("specialist"),
        "degraded": False, "status": "COMPLETED", "errors": [], "retries": 0,
        "retrieved": r.get("retrieved", []), "best_distance": r.get("best_distance"),
        "lexical_anchors": r.get("lexical_anchors", []), "gate_bypass": r.get("gate_bypass"),
        "model_cited": r.get("model_cited", []), "citations": citation_rows(r.get("model_cited", []), r.get("retrieved", [])),
        "output_tokens_reply": r.get("output_tokens"), "max_new_tokens": r.get("max_new_tokens"),
        "input_tokens": input_tokens or 0, "output_tokens": output_tokens or 0,
        "tokens": (input_tokens or 0) + (output_tokens or 0),
        "cost_usd": round(cost_usd(input_tokens or 0, output_tokens or 0), 6),
        "model_calls": 1 if answered else 0, "handoffs": 0, "specialist_calls": 0,
        "tool_calls": 1, "resynthesis": 0,
        "base_context_tokens": r.get("base_context_tokens"),
        "context_resend_multiplier": None,
        "latency_ms": latency_ms, "model_latency_ms": r.get("model_latency_ms"),
        "trace_id": r.get("trace_id"), "langfuse_trace_id": r.get("langfuse_trace_id"),
        "task_ids": [], "tasks": [], "handoff_records": [], "routing": None,
        "prompt_version": r.get("prompt_version"), "model_name": r.get("model_name"),
    }


def run_multi(case, session_id, manager):
    ticket = case["ticket"]
    r = manager.handle(ticket, case_id=case["id"], source="week10-multi", session_id=session_id,
                       tags=["week10", "arm:multi", f"case:{case['id']}"])
    meter = r["meter"]
    return {
        "arm": "multi", "answer": r["answer"], "refused": r["refused"], "refusal_reason": r["refusal_reason"],
        "not_found": r["not_found"], "degraded": r["degraded"], "status": r["status"],
        "errors": r["errors"], "retries": meter["retries"],
        "retrieved": r.get("retrieved", []),
        "best_distance": (r["specialists"].get("knowledge_specialist") or {}).get("best_distance"),
        "lexical_anchors": (r["specialists"].get("knowledge_specialist") or {}).get("lexical_anchors", []),
        "gate_bypass": None,
        "model_cited": r.get("model_cited", []), "citations": r.get("citations", []),
        "output_tokens_reply": r.get("output_tokens"), "max_new_tokens": r.get("max_new_tokens"),
        "input_tokens": meter["input_tokens"], "output_tokens": meter["output_tokens"],
        "tokens": meter["tokens"], "cost_usd": round(meter["cost_usd"], 6),
        "model_calls": meter["model_calls"], "handoffs": meter["handoffs"],
        "specialist_calls": meter["specialist_calls"], "tool_calls": meter["tool_calls"],
        "resynthesis": meter["resynthesis"], "base_context_tokens": meter["base_context_tokens"],
        "context_resend_multiplier": meter["context_resend_multiplier"],
        "latency_ms": r["latency_ms"], "model_latency_ms": sum(c.get("latency_ms") or 0 for c in r["calls"]),
        "trace_id": r.get("trace_id"), "langfuse_trace_id": r.get("langfuse_trace_id"),
        "task_ids": [t["id"] for t in r["tasks"]], "tasks": r["tasks"], "handoff_records": r["handoffs"],
        "calls": r["calls"], "routing": r["routing"], "specialists": r["specialists"],
        "resynthesis_detail": r.get("resynthesis", []), "transport": r["transport"],
        "prompt_version": PROMPT_VERSION_MULTI(), "model_name": r.get("model_name"),
    }


def PROMPT_VERSION_MULTI():
    from multi_agent import PROMPT_VERSION
    return PROMPT_VERSION


def case_row(case, arm_result):
    return {"case_id": case["id"], "mode": case["mode"], "policy_backed": case.get("policy_backed", False),
            "regression": bool(case.get("regression")), "ticket_id": case["ticket"]["ticket_id"],
            "question": case["ticket"]["message"],
            "reference_articles": sorted(set(ARTICLE_ID.findall(" ".join(case["reference"])))),
            **arm_result}


def cmd_run(args):
    manifest, cases = manifest_cases()
    if args.limit:
        cases = cases[:args.limit]
    arms = ARMS if args.arm == "both" else (args.arm,)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    session_id = f"week10-{stamp}"
    from multi_agent import Manager, retrieval_config
    config = retrieval_config()

    print(f"Week 10 · {len(cases)} cases · arms {arms} · transport {args.transport}")
    print(f"retrieval {config['retrieval_mode']} top_k={config['top_k']} threshold={config['threshold']} "
          f"· decoding {config['model_params']}")
    cold_start_ms = warm_up()
    print(f"cold start (model load + first token, not charged to any case): {cold_start_ms:.0f} ms\n")

    worker = None
    manager = None
    if "multi" in arms:
        if args.transport == "http":
            from multi_agent import HTTPTransport
            from specialist_worker import SpecialistWorker
            worker = SpecialistWorker().start()
            manager = Manager(transport=HTTPTransport(worker.url))
        else:
            manager = Manager()

    try:
        for arm in arms:
            rows = []
            print(f"== {arm} ==")
            for index, case in enumerate(cases, start=1):
                started = time.perf_counter()
                outcome = run_single(case, session_id) if arm == "single" else run_multi(case, session_id, manager)
                row = case_row(case, outcome)
                rows.append(row)
                print(f"  {index:>2}/{len(cases)} {case['id']:<4} {case['mode']} "
                      f"{'refused' if row['refused'] else 'answered':<8} "
                      f"{row['latency_ms']:>8.0f} ms  {row['tokens']:>5} tok  "
                      f"{row['model_calls']} call(s)  {row['handoffs']} handoff(s)"
                      + (f"  DEGRADED: {row['refusal_reason']}" if row["degraded"] else ""))
            out = RUNS / f"{arm}_{stamp}.json"
            write_json(out, {
                "ran_at": now_iso(), "app_version": tracing.app_version(), "arm": arm,
                "session_id": session_id, "manifest_sha": tracing.sha256(MANIFEST.read_text(encoding="utf-8")),
                "case_ids": [c["id"] for c in cases], "model": rows[0]["model_name"] if rows else None,
                "retrieval": {k: config[k] for k in ("retrieval_mode", "strategy", "top_k", "threshold")},
                "decoding": config["model_params"], "rate_card_usd_per_mtok": RATE_CARD,
                "transport": args.transport if arm == "multi" else "in-process (single model call)",
                "latency_method": "sequential, per case, end to end, model warm",
                "cold_start_ms": cold_start_ms,
                "langfuse": {"enabled": tracing.langfuse_enabled()},
                "rows": rows,
            })
            print(f"  -> {out.relative_to(ROOT)}\n")
    finally:
        if worker is not None:
            worker.stop()
    tracing.flush()
    return 0


# ---------------------------------------------------------------------------
# grading: the Week 6 assertions and judge, on both arms alike
# ---------------------------------------------------------------------------

def latest_run(arm):
    runs = sorted(RUNS.glob(f"{arm}_*.json"))
    if not runs:
        raise SystemExit(f"no {arm} run under week10/runs/; run: python main.py week10 run --arm {arm}")
    return runs[-1]


def cmd_judge(args):
    import week6_eval
    import ticket_assertions

    manifest, cases = manifest_cases()
    by_id = {c["id"]: c for c in cases}
    paths = {"single": Path(args.single) if args.single else latest_run("single"),
             "multi": Path(args.multi) if args.multi else latest_run("multi")}
    runs = {arm: read_json(path) for arm, path in paths.items()}
    for arm, data in runs.items():
        if data["case_ids"] != manifest["case_ids"]:
            raise SystemExit(f"{paths[arm].name} ran {data['case_ids']}, not the manifest's cases")

    version = args.judge or week6_eval.best_available_judge()
    if version == "none":
        raise SystemExit("The Week 6 labelling protocol does not allow a judge yet (see week6/README.md)")
    week6_eval.release_generator()                 # 8 GB will not hold drafter and judge together
    import judge
    prompt = judge.load_prompt(week6_eval.judge_prompt_path(version))
    agreement = read_json(W6 / "agreement.json") if (W6 / "agreement.json").exists() else {}
    print(f"Judge {version} ({judge.JUDGE_MODEL}, prompt sha {prompt['sha']}) · "
          f"agreement with blind labels: before {agreement.get('agreement_before')}% "
          f"after {agreement.get('agreement_after')}%")

    for arm in ARMS:
        data = runs[arm]
        print(f"== {arm} ==")
        for row in data["rows"]:
            case = by_id[row["case_id"]]
            reply = {"text": row["answer"] or "", "refused": row["refused"],
                     "output_tokens": row.get("output_tokens_reply"), "max_new_tokens": row.get("max_new_tokens")}
            assertions = ticket_assertions.run_assertions(case, reply)
            verdict = judge.judge_reply(prompt, case, reply["text"])
            row["assertions"] = assertions
            row["assertions_pass"] = all(a["passed"] for a in assertions if a["applicable"])
            row["judge"] = verdict["verdict"]
            row["judge_parsed"] = verdict["parsed"]
            row["judge_reason"] = verdict["reason"]
            row["judge_raw"] = verdict["raw"]
            row["pass"] = row["assertions_pass"] and verdict["verdict"] == "PASS"
            cited = {c["article_id"] for c in row.get("citations", []) if c.get("article_id")}
            expected = set(row.get("reference_articles") or [])
            row["citation_correct"] = (None if row["refused"] or not cited
                                       else bool(cited) and cited.issubset(expected))
            row["cited_articles"] = sorted(cited)
            row["retrieved_expected_article"] = any(
                c.get("article_id") in expected for c in row.get("retrieved", []))
            failed = [a["name"] for a in assertions if a["applicable"] and not a["passed"]]
            print(f"  {row['case_id']:<4} judge {verdict['verdict']:<4} assertions "
                  f"{'ok' if row['assertions_pass'] else 'FAIL ' + ','.join(failed):<40} "
                  f"-> {'PASS' if row['pass'] else 'FAIL'}")
            if row.get("trace_id"):
                try:
                    tracing.langfuse_score(row["trace_id"], "week10_pass", row["pass"],
                                           comment=f"judge {verdict['verdict']}; {verdict['reason']}")
                except Exception as error:
                    print(f"    [langfuse] score not recorded: {error}")
        data["judged_at"] = now_iso()
        data["judge"] = {"version": version, "model": judge.JUDGE_MODEL, "prompt_sha": prompt["sha"],
                         "prompt_file": f"week6/judge_{version}.txt",
                         "agreement_with_blind_labels": {
                             "before_pct": agreement.get("agreement_before"),
                             "after_pct": agreement.get("agreement_after"),
                             "held_out_pct": ((agreement.get("agreement_after_detail") or {}).get("held_out") or {}).get("pct"),
                             "cohen_kappa_after": (agreement.get("agreement_after_detail") or {}).get("cohen_kappa")}}
        data["source_run"] = paths[arm].relative_to(ROOT).as_posix()
        data["pass_rule"] = "every applicable deterministic assertion passes AND judge says PASS"
        write_json(RESULTS[arm], data)
        print(f"  -> {RESULTS[arm].relative_to(ROOT)}")
    tracing.flush()
    return 0


# ---------------------------------------------------------------------------
# aggregates, all re-derived from the per-case rows
# ---------------------------------------------------------------------------

def summarise_arm(rows):
    from multi_agent import percentile
    n = len(rows)
    lat = [r["latency_ms"] for r in rows]
    answered = [r for r in rows if not r["refused"]]
    cited = [r for r in rows if r.get("citation_correct") is not None]
    by_mode = {}
    for mode in MODES:
        sub = [r for r in rows if r["mode"] == mode]
        if sub:
            by_mode[mode] = {"n": len(sub), "passes": sum(1 for r in sub if r.get("pass"))}
    multipliers = [r["context_resend_multiplier"] for r in rows if r.get("context_resend_multiplier")]
    return {
        "n": n,
        "passes": sum(1 for r in rows if r.get("pass")),
        "pass_rate_pct": round(100.0 * sum(1 for r in rows if r.get("pass")) / n, 1) if n else None,
        "failures": sum(1 for r in rows if not r.get("pass")),
        "assertions_pass": sum(1 for r in rows if r.get("assertions_pass")),
        "judge_pass": sum(1 for r in rows if r.get("judge") == "PASS"),
        "answered": len(answered), "refused": n - len(answered),
        "not_found": sum(1 for r in rows if r.get("not_found")),
        "degraded": sum(1 for r in rows if r.get("degraded")),
        "errors": sum(len(r.get("errors") or []) for r in rows),
        "retries": sum(r.get("retries") or 0 for r in rows),
        "timeouts": sum(1 for r in rows if r.get("status") == "TIMED_OUT"
                        or any(e.get("kind") == "timeout" for e in (r.get("errors") or []))),
        "citation_correct": sum(1 for r in cited if r["citation_correct"]),
        "citation_checked": len(cited),
        "retrieved_expected_article": sum(1 for r in rows if r.get("retrieved_expected_article")),
        "latency_p50_ms": round(statistics.median(lat), 1) if lat else None,
        "latency_p95_ms": percentile(lat, 95), "latency_p99_ms": percentile(lat, 99),
        "latency_mean_ms": round(statistics.mean(lat), 1) if lat else None,
        "latency_max_ms": max(lat) if lat else None,
        "latency_sum_ms": round(sum(lat), 1),
        "input_tokens": sum(r["input_tokens"] for r in rows),
        "output_tokens": sum(r["output_tokens"] for r in rows),
        "total_tokens": sum(r["tokens"] for r in rows),
        "mean_tokens_per_case": round(sum(r["tokens"] for r in rows) / n, 1) if n else None,
        "cost_per_case_usd": round(sum(r["cost_usd"] for r in rows) / n, 6) if n else None,
        "total_cost_usd": round(sum(r["cost_usd"] for r in rows), 6),
        "model_calls": sum(r["model_calls"] for r in rows),
        "mean_model_calls": round(sum(r["model_calls"] for r in rows) / n, 2) if n else None,
        "handoffs": sum(r["handoffs"] for r in rows),
        "mean_handoffs": round(sum(r["handoffs"] for r in rows) / n, 2) if n else None,
        "specialist_calls": sum(r.get("specialist_calls") or 0 for r in rows),
        "resynthesis": sum(r.get("resynthesis") or 0 for r in rows),
        "context_resend_multiplier_mean": round(statistics.mean(multipliers), 2) if multipliers else None,
        "by_mode": by_mode,
    }


def largest_handoff(rows):
    """The specialist task that cost the most tokens across the multi arm."""
    best = None
    for r in rows:
        for t in r.get("tasks") or []:
            if t.get("kind") in ("specialist", "triage", "synthesis") and t.get("tokens"):
                if best is None or t["tokens"] > best["tokens"]:
                    best = {"case_id": r["case_id"], "task_id": t["id"], "agent": t["agent"],
                            "kind": t["kind"], "tokens": t["tokens"], "input_tokens": t["input_tokens"],
                            "output_tokens": t["output_tokens"], "latency_ms": t["latency_ms"]}
    return best


def cmd_report(args):
    results = {}
    for arm in ARMS:
        if not RESULTS[arm].exists():
            raise SystemExit(f"{RESULTS[arm].relative_to(ROOT)} missing; run: python main.py week10 judge")
        results[arm] = read_json(RESULTS[arm])
    if results["single"]["case_ids"] != results["multi"]["case_ids"]:
        raise SystemExit("the two arms did not run the same cases")
    rows = {arm: results[arm]["rows"] for arm in ARMS}
    summary = {arm: summarise_arm(rows[arm]) for arm in ARMS}
    per_case = []
    for s, m in zip(rows["single"], rows["multi"]):
        per_case.append({
            "case_id": s["case_id"], "mode": s["mode"], "policy_backed": s["policy_backed"],
            "regression": s["regression"], "question": s["question"],
            "single_pass": s.get("pass"), "multi_pass": m.get("pass"),
            "single_judge": s.get("judge"), "multi_judge": m.get("judge"),
            "single_assertions_pass": s.get("assertions_pass"), "multi_assertions_pass": m.get("assertions_pass"),
            "single_refused": s["refused"], "multi_refused": m["refused"],
            "multi_not_found": m.get("not_found"), "multi_degraded": m.get("degraded"),
            "single_latency_ms": s["latency_ms"], "multi_latency_ms": m["latency_ms"],
            "single_tokens": s["tokens"], "multi_tokens": m["tokens"],
            "single_input_tokens": s["input_tokens"], "multi_input_tokens": m["input_tokens"],
            "single_output_tokens": s["output_tokens"], "multi_output_tokens": m["output_tokens"],
            "single_cost_usd": s["cost_usd"], "multi_cost_usd": m["cost_usd"],
            "single_model_calls": s["model_calls"], "multi_model_calls": m["model_calls"],
            "multi_handoffs": m["handoffs"], "multi_specialists": (m.get("routing") or {}).get("dispatch"),
            "multi_resynthesis": m.get("resynthesis"),
            "multi_context_resend_multiplier": m.get("context_resend_multiplier"),
            "single_citation_correct": s.get("citation_correct"), "multi_citation_correct": m.get("citation_correct"),
            "winner": ("multi" if m.get("pass") and not s.get("pass") else
                       "single" if s.get("pass") and not m.get("pass") else
                       "both" if s.get("pass") else "neither"),
        })
    comparison = {
        "generated_at": now_iso(), "app_version": tracing.app_version(),
        "n_cases": len(per_case), "case_ids": results["single"]["case_ids"],
        "sources": {arm: results[arm]["source_run"] for arm in ARMS},
        "judge": results["single"]["judge"], "pass_rule": results["single"]["pass_rule"],
        "rate_card_usd_per_mtok": RATE_CARD, "latency_method": results["single"]["latency_method"],
        "cold_start_ms": {arm: results[arm]["cold_start_ms"] for arm in ARMS},
        "percentile_note": "p50 = median; p95/p99 = nearest-rank, which with n=10 is the slowest case",
        "summary": summary,
        "largest_handoff": largest_handoff(rows["multi"]),
        "per_case": per_case,
        "wins": {k: sum(1 for p in per_case if p["winner"] == k) for k in ("single", "multi", "both", "neither")},
    }
    write_json(COMPARISON_JSON, comparison)
    with open(COMPARISON_CSV, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(per_case[0]))
        writer.writeheader()
        writer.writerows(per_case)
    RACE_TABLE.write_text(race_table_md(comparison), encoding="utf-8")
    HANDOFFS_LOG.write_text(handoffs_log(results), encoding="utf-8")
    print(race_table_md(comparison))
    print(f"Wrote {COMPARISON_JSON.name}, {COMPARISON_CSV.name}, {RACE_TABLE.name}, {HANDOFFS_LOG.name}")
    return 0


def fmt_ms(v):
    return "—" if v is None else f"{v / 1000:.1f} s"


def race_table_md(c):
    s, m = c["summary"]["single"], c["summary"]["multi"]
    n = c["n_cases"]
    j = c["judge"]["agreement_with_blind_labels"]
    lines = [
        "# Week 10 — race table: single agent vs manager + two specialists",
        "",
        f"Generated {c['generated_at']} from `{c['sources']['single']}` and `{c['sources']['multi']}` "
        f"(build `{c['app_version']}`). Same {n} cases, same model, same retrieval, same judge.",
        "",
        "| metric | single agent | multi-agent | note |",
        "|---|---|---|---|",
        f"| pass rate | **{s['passes']}/{n} = {s['pass_rate_pct']}%** | **{m['passes']}/{n} = {m['pass_rate_pct']}%** | assertions AND judge |",
        f"| judge PASS (model-based) | {s['judge_pass']}/{n} | {m['judge_pass']}/{n} | judge {c['judge']['version']}, agreement with blind labels {j['after_pct']}% (held-out {j['held_out_pct']}%) |",
        f"| assertions pass (deterministic) | {s['assertions_pass']}/{n} | {m['assertions_pass']}/{n} | 6 code checks |",
        f"| citations inside the reference articles | {s['citation_correct']}/{s['citation_checked']} | {m['citation_correct']}/{m['citation_checked']} | deterministic; denominator = replies that cite |",
        f"| refused / not found | {s['refused']} / {s['not_found']} | {m['refused']} / {m['not_found']} | routed hand-off |",
        f"| degraded (specialist failure) | {s['degraded']} | {m['degraded']} | |",
        f"| p50 latency | {fmt_ms(s['latency_p50_ms'])} | {fmt_ms(m['latency_p50_ms'])} | median, sequential, warm |",
        f"| p95 latency | {fmt_ms(s['latency_p95_ms'])} | {fmt_ms(m['latency_p95_ms'])} | nearest-rank: the slowest case at n={n} |",
        f"| p99 latency | {fmt_ms(s['latency_p99_ms'])} | {fmt_ms(m['latency_p99_ms'])} | same case as p95 at n={n} |",
        f"| mean latency | {fmt_ms(s['latency_mean_ms'])} | {fmt_ms(m['latency_mean_ms'])} | |",
        f"| input tokens | {s['input_tokens']:,} | {m['input_tokens']:,} | summed per model call |",
        f"| output tokens | {s['output_tokens']:,} | {m['output_tokens']:,} | |",
        f"| total tokens | **{s['total_tokens']:,}** ({s['mean_tokens_per_case']}/case) | **{m['total_tokens']:,}** ({m['mean_tokens_per_case']}/case) | |",
        f"| cost per case | ${s['cost_per_case_usd']:.6f} | ${m['cost_per_case_usd']:.6f} | stand-in rate card ${c['rate_card_usd_per_mtok']['input_per_mtok']}/${c['rate_card_usd_per_mtok']['output_per_mtok']} per Mtok, not a provider charge |",
        f"| total cost | ${s['total_cost_usd']:.6f} | ${m['total_cost_usd']:.6f} | |",
        f"| model calls | {s['model_calls']} ({s['mean_model_calls']}/case) | {m['model_calls']} ({m['mean_model_calls']}/case) | |",
        f"| hand-offs | {s['handoffs']} | {m['handoffs']} ({m['mean_handoffs']}/case) | dispatch + return, manager ↔ specialist |",
        f"| specialist calls | {s['specialist_calls']} | {m['specialist_calls']} | |",
        f"| re-synthesis (conflict resolution) | — | {m['resynthesis']} | bounded to 1 per case |",
        f"| context re-send multiplier | — | {m['context_resend_multiplier_mean']}× | total prompt tokens / ticket tokens, mean |",
        f"| errors / retries / timeouts | {s['errors']} / {s['retries']} / {s['timeouts']} | {m['errors']} / {m['retries']} / {m['timeouts']} | |",
        "",
        "## Pass rate by Week 5 mode",
        "",
        "| mode | n | single | multi |",
        "|---|---|---|---|",
    ]
    for mode in MODES:
        sm, mm = s["by_mode"].get(mode), m["by_mode"].get(mode)
        if sm:
            lines.append(f"| {mode} | {sm['n']} | {sm['passes']}/{sm['n']} | {mm['passes']}/{mm['n']} |")
    lh = c.get("largest_handoff")
    lines += ["", "## Per case", "",
              "| case | mode | single | multi | single s / tok | multi s / tok | multi calls / hand-offs | winner |",
              "|---|---|---|---|---|---|---|---|"]
    for p in c["per_case"]:
        lines.append(f"| {p['case_id']} | {p['mode']} | {'PASS' if p['single_pass'] else 'FAIL'} | "
                     f"{'PASS' if p['multi_pass'] else 'FAIL'}{' (not found)' if p['multi_not_found'] else ''}"
                     f"{' (degraded)' if p['multi_degraded'] else ''} | "
                     f"{p['single_latency_ms'] / 1000:.1f} / {p['single_tokens']} | "
                     f"{p['multi_latency_ms'] / 1000:.1f} / {p['multi_tokens']} | "
                     f"{p['multi_model_calls']} / {p['multi_handoffs']} | {p['winner']} |")
    lines += ["", f"Wins: {c['wins']}.", ""]
    if lh:
        lines.append(f"Largest token-consuming hand-off: **{lh['agent']} ({lh['kind']}) on {lh['case_id']}**, "
                     f"{lh['tokens']} tokens (in {lh['input_tokens']} / out {lh['output_tokens']}), "
                     f"{fmt_ms(lh['latency_ms'])}. Task `{lh['task_id']}`.")
    lines.append(f"\nCold start (model load, charged to no case): single {fmt_ms(c['cold_start_ms']['single'])}, "
                 f"multi {fmt_ms(c['cold_start_ms']['multi'])}.")
    return "\n".join(lines) + "\n"


def handoffs_log(results):
    """One line per hand-off, in run order, from the stored task records."""
    lines = [f"# Week 10 hand-offs · generated {now_iso()} · one line per dispatch/return",
             "# ts | arm | case | handoff_id | direction | from_agent(task) -> to_agent(task) | status | in_tok | out_tok | context_chars | summary"]
    for arm in ARMS:
        for r in results[arm]["rows"]:
            if arm == "single":
                lines.append(f"{r.get('trace_id') or '-'} | single | {r['case_id']} | - | none | single agent, one model call, "
                             f"no hand-off | {'refused' if r['refused'] else 'answered'} | "
                             f"{r['input_tokens']} | {r['output_tokens']} | - | {summ(r['answer'])}")
                continue
            tasks = {t["id"]: t for t in r.get("tasks") or []}
            for h in r.get("handoff_records") or []:
                lines.append(f"{h['at']} | multi | {r['case_id']} | {h['id']} | {h['direction']} | "
                             f"{h['from_agent']}({h['from_task']}) -> {h['to_agent']}({h['to_task']}) | "
                             f"{h['status']} | {h.get('input_tokens') if h.get('input_tokens') is not None else '-'} | "
                             f"{h.get('output_tokens') if h.get('output_tokens') is not None else '-'} | "
                             f"{h.get('context_chars')} | {summ(h.get('summary'))}")
            for t in (r.get("tasks") or []):
                lines.append(f"{t['ended_at'] or t['started_at']} | multi | {r['case_id']} | {t['id']} | task | "
                             f"{t['agent']} {t['kind']} parent={t.get('parent_id') or '-'} | {t['status']} | "
                             f"{t['input_tokens']} | {t['output_tokens']} | - | "
                             f"{' -> '.join(x['state'] for x in t['transitions'])}"
                             f"{' error=' + json.dumps(t['error']) if t.get('error') else ''}")
    return "\n".join(lines) + "\n"


def summ(text, limit=160):
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit] + "…"


# ---------------------------------------------------------------------------
# the failure experiment: one specialist answers HTTP 500 for one case
# ---------------------------------------------------------------------------

def cmd_failure(args):
    from multi_agent import HTTPTransport, Manager, RETRY_POLICY
    from specialist_worker import SpecialistWorker
    import ticket_assertions

    manifest, cases = manifest_cases()
    by_id = {c["id"]: c for c in cases}
    if args.case not in by_id:
        raise SystemExit(f"{args.case} is not one of the manifest's cases: {manifest['case_ids']}")
    case = by_id[args.case]
    control = next(c for c in cases if c["id"] != args.case)
    fault = {"specialist": args.specialist, "ticket_id": case["ticket"]["ticket_id"]}
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    session_id = f"week10-failure-{stamp}"

    cold_start_ms = warm_up()
    worker = SpecialistWorker(fault=fault).start()
    try:
        transport = HTTPTransport(worker.url)
        manager = Manager(transport=transport)
        print(f"worker {worker.url} fault={fault} retry policy {RETRY_POLICY}")
        print(f"== faulted case {case['id']} ({case['ticket']['ticket_id']}) ==")
        faulted = manager.handle(case["ticket"], case_id=case["id"], source="week10-failure",
                                 session_id=session_id, tags=["week10", "failure", f"case:{case['id']}"])
        print(f"   status {faulted['status']} degraded={faulted['degraded']} errors={faulted['errors']}")
        print(f"   answer: {faulted['answer']!r}")
        print(f"== control case {control['id']} ({control['ticket']['ticket_id']}), same worker, same fault ==")
        unaffected = manager.handle(control["ticket"], case_id=control["id"], source="week10-failure-control",
                                    session_id=session_id, tags=["week10", "failure-control"])
        print(f"   status {unaffected['status']} degraded={unaffected['degraded']} errors={unaffected['errors']}")
        http_log = list(transport.log)
        worker_log = list(worker.requests)
    finally:
        worker.stop()
    tracing.flush()

    reply = {"text": faulted["answer"] or "", "refused": faulted["refused"],
             "output_tokens": faulted.get("output_tokens"), "max_new_tokens": faulted.get("max_new_tokens")}
    assertions = ticket_assertions.run_assertions(case, reply)
    # The specialist task that failed, not the root (which fails because of it).
    failed_task = next((t for t in faulted["tasks"] if t["kind"] == "specialist"
                        and t["status"] in ("FAILED", "TIMED_OUT")), None)
    record = {
        "ran_at": now_iso(), "app_version": tracing.app_version(), "cold_start_ms": cold_start_ms,
        "fault": fault, "retry_policy": {**RETRY_POLICY, "retry_on": list(RETRY_POLICY["retry_on"])},
        "transport": "http", "worker_requests": worker_log, "http_attempts": http_log,
        "faulted_case": {"case_id": case["id"], "ticket_id": case["ticket"]["ticket_id"],
                         "question": case["ticket"]["message"], "status": faulted["status"],
                         "degraded": faulted["degraded"], "refused": faulted["refused"],
                         "refusal_reason": faulted["refusal_reason"], "answer": faulted["answer"],
                         "errors": faulted["errors"], "retries": faulted["meter"]["retries"],
                         "tasks": faulted["tasks"], "handoffs": faulted["handoffs"], "meter": faulted["meter"],
                         "latency_ms": faulted["latency_ms"], "trace_id": faulted.get("trace_id"),
                         "assertions": assertions,
                         "assertions_pass": all(a["passed"] for a in assertions if a["applicable"]),
                         "failed_task": failed_task},
        "control_case": {"case_id": control["id"], "ticket_id": control["ticket"]["ticket_id"],
                         "status": unaffected["status"], "degraded": unaffected["degraded"],
                         "refused": unaffected["refused"], "errors": unaffected["errors"],
                         "answer": unaffected["answer"], "meter": unaffected["meter"],
                         "latency_ms": unaffected["latency_ms"], "trace_id": unaffected.get("trace_id")},
        "judge": "not run: the reply is the routed hand-off, which the judge criterion itself "
                 "calls FAIL whenever the reference facts answer the question",
    }
    write_json(FAILURE_JSON, record)
    FAILURE_MD.write_text(failure_md(record), encoding="utf-8")
    print(f"\nWrote {FAILURE_JSON.relative_to(ROOT)} and {FAILURE_MD.relative_to(ROOT)}")
    return 0


def failure_md(r):
    f, c = r["faulted_case"], r["control_case"]
    ft = f["failed_task"] or {}
    attempts_faulted = [a for a in r["http_attempts"] if a["name"] == r["fault"]["specialist"]]
    lines = [
        "# Week 10 — failure case: the policy worker answers HTTP 500",
        "",
        f"Run {r['ran_at']}, build `{r['app_version']}`, transport HTTP via `src/specialist_worker.py`.",
        "",
        "## What was broken, and only that",
        "",
        f"- Fault: `{r['fault']['specialist']}` answers **HTTP 500** for ticket `{r['fault']['ticket_id']}` "
        f"(case {f['case_id']}) before the specialist runs. Every other request is served normally.",
        f"- Retry policy: `{json.dumps(r['retry_policy'])}`.",
        "",
        "## What the manager did on the faulted case",
        "",
        f"- Question: {f['question']}",
        f"- Worker log for that ticket: " + ", ".join(
            f"{w['specialist']} -> {w['status']}{' (injected)' if w.get('fault') else ''}"
            for w in r["worker_requests"] if w.get("ticket_id") == f["ticket_id"]),
        f"- HTTP attempts to the faulted specialist: {len(attempts_faulted)} "
        f"({', '.join(str(a['status']) for a in attempts_faulted)}), then it stopped: retries are bounded "
        f"at max_attempts={r['retry_policy']['max_attempts']}.",
        f"- Task `{ft.get('id')}` ({ft.get('agent')}): "
        + " -> ".join(t["state"] for t in ft.get("transitions", [])) + f"; error recorded: `{json.dumps(ft.get('error'))}`.",
        f"- Root task status: **{f['status']}**, degraded={f['degraded']}, refused={f['refused']}, "
        f"reason `{f['refusal_reason']}`.",
        f"- Retries counted: {f['retries']}. Hand-offs recorded: {len(f['handoffs'])}. Model calls: {f['meter']['model_calls']}.",
        "",
        "## The reply the customer would have received",
        "",
        "```", f["answer"] or "", "```",
        "",
        "This is `ticket_policy.routed_reply`: the ticket id, the escalation tag when the tier needs it, "
        "and a hand-off to a person. The manager did **not** write a refund decision without the specialist "
        "that owns the account facts, and the reply does not claim that anything was checked.",
        "",
        "## Honest scoring",
        "",
        f"- Deterministic assertions on the degraded reply: {'all applicable pass' if f['assertions_pass'] else 'FAIL: ' + ', '.join(a['name'] for a in f['assertions'] if a['applicable'] and not a['passed'])}.",
        f"- Judge: {r['judge']}.",
        "- So the failure costs this case its pass. The alternative, synthesising a refund answer from the "
        "help-centre passages alone, would have hidden the outage behind a confident reply; that is the "
        "misleading synthesis the design refuses.",
        "",
        "## The control case, same worker, same fault switch",
        "",
        f"- Case {c['case_id']} (`{c['ticket_id']}`): status **{c['status']}**, degraded={c['degraded']}, "
        f"errors={len(c['errors'])}, {c['meter']['model_calls']} model calls, {c['latency_ms'] / 1000:.1f} s.",
        f"- Worker log: " + ", ".join(
            f"{w['specialist']} -> {w['status']}" for w in r["worker_requests"] if w.get("ticket_id") == c["ticket_id"]),
        "",
        f"Raw record: `week10/failure_case.json`. Traces: faulted `{f.get('trace_id')}`, control `{c.get('trace_id')}`.",
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cmd_all(args):
    for step in (cmd_manifest, cmd_run, cmd_judge, cmd_report, cmd_failure):
        code = step(args)
        if code:
            return code
    return 0


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    m = sub.add_parser("manifest", help="choose the 10 cases deterministically")
    m.add_argument("--force", action="store_true")
    m.set_defaults(func=cmd_manifest)

    r = sub.add_parser("run", help="run one or both arms over the 10 cases")
    r.add_argument("--arm", choices=("single", "multi", "both"), default="both")
    r.add_argument("--transport", choices=("inprocess", "http"), default="inprocess")
    r.add_argument("--limit", type=int, default=None, help="first N cases only (smoke)")
    r.set_defaults(func=cmd_run)

    j = sub.add_parser("judge", help="assertions + the Week 6 judge on the newest runs")
    j.add_argument("--single", default=None)
    j.add_argument("--multi", default=None)
    j.add_argument("--judge", choices=("v1", "v2"), default=None)
    j.set_defaults(func=cmd_judge)

    p = sub.add_parser("report", help="comparison.{json,csv}, race_table.md, handoffs.log")
    p.set_defaults(func=cmd_report)

    f = sub.add_parser("failure", help="one specialist answers HTTP 500 for one case")
    f.add_argument("--case", default="T07")
    f.add_argument("--specialist", choices=("ticket_specialist", "knowledge_specialist"),
                   default="ticket_specialist")
    f.set_defaults(func=cmd_failure)

    a = sub.add_parser("all", help="manifest, run, judge, report, failure")
    a.add_argument("--arm", default="both")
    a.add_argument("--transport", choices=("inprocess", "http"), default="inprocess")
    a.add_argument("--limit", type=int, default=None)
    a.add_argument("--force", action="store_true")
    a.add_argument("--single", default=None)
    a.add_argument("--multi", default=None)
    a.add_argument("--judge", default=None)
    a.add_argument("--case", default="T07")
    a.add_argument("--specialist", default="ticket_specialist")
    a.set_defaults(func=cmd_all)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
