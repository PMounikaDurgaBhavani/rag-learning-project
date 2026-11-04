"""Week 12: the integrated evaluation, trajectory eval, taxonomy, reopen, cost.

    python main.py capstone sample                       # the uncurated random sample (Week 5 rule)
    python main.py capstone eval run --label v1.1 [--planner model|code] [--no-policy-filter] [--subset refund|capstone|all|ID,ID]
    python main.py capstone eval judge --run week12/runs/<file>.json      # judge v2, separate process
    python main.py capstone eval report --run <after> [--before <before>] # integrated_eval.json, closed_loop.json
    python main.py capstone trajectory --run <run>       # Week 8 scorer over the integrated trajectories
    python main.py capstone taxonomy --run <run> [--run <run> ...]
    python main.py capstone reopen --run <run>           # SIMULATED second contact, 10 deflected tickets
    python main.py capstone cost --run <run>             # cost per deflected ticket, Black Friday maths
    python main.py capstone report                       # reports/*.md from week12/*.json

Running and scoring are separate (Week 8's rule): `run` stores every case
raw; everything else re-reads the stored runs and needs no model, except
`judge` and `reopen`, which load one model each in their own process.
"""

import argparse
import json
import os
import random
import re
import statistics
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "experiments"))
os.chdir(ROOT)
os.environ.setdefault("RAG_LANGFUSE", "0")

W12 = ROOT / "week12"
RUNS = W12 / "runs"
REPORTS = ROOT / "reports"
CASES = W12 / "eval_cases.jsonl"
LABELS = W12 / "labels.json"
LABELS_TEMPLATE = W12 / "labels_template.json"

MODE_LABELS = {
    "M1": "refuses an answerable question", "M2": "messy / non-question input", "M3": "reply cut off",
    "M4": "non-answer text (scaffolding, invented steps)", "M5": "answers without the steps",
    "I1-policy-version": "refund policy version (current vs retired)", "I2-evidence-missing": "missing or conflicting evidence",
    "I3-sensitive-escalation": "sensitive / out-of-scope escalation", "I4-shipping-entitlement": "shipping entitlement and SLA",
    "I5-injection-pii": "prompt injection and PII probes",
}


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def load_cases():
    return [json.loads(l) for l in CASES.read_text(encoding="utf-8").splitlines() if l.strip()]


def _json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, indent=1, default=str) + "\n", encoding="utf-8")
    return Path(path)


def pct(a, n):
    return round(100.0 * a / n, 1) if n else None


def summarise(values):
    values = sorted(v for v in values if isinstance(v, (int, float)))
    if not values:
        return {"n": 0}
    def p(q):
        return values[min(len(values) - 1, max(0, int(round(q * len(values) + 0.5)) - 1))]
    return {"n": len(values), "mean": round(statistics.mean(values), 1), "p50": round(statistics.median(values), 1),
            "p95": round(p(0.95), 1), "max": round(values[-1], 1), "min": round(values[0], 1)}


# ---------------------------------------------------------------------------
# sample
# ---------------------------------------------------------------------------

def cmd_sample(args):
    """Week 5's rule, applied to the inbox: random.Random(seed).sample(sorted ids, n)."""
    from capstone.demo_data import tickets
    ids = sorted(tickets())
    seed, n = int(args.seed), int(args.n)
    chosen = random.Random(seed).sample(ids, n)
    out = {"seed": seed, "n": n, "population": len(ids),
           "selection_rule": f"random.Random({seed}).sample(sorted(ticket ids in data/capstone/seed/tickets.json), {n})",
           "drawn_at": now_iso(), "ticket_ids": chosen,
           "note": "uncurated: the sample is drawn from every ticket in the demo inbox, not from a chosen subset"}
    _write(W12 / "sample.json", out)
    print(json.dumps(out, indent=1))
    return 0


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------

def _row(case, result):
    consulted = result["citations"]["consulted"]
    cited = result["citations"]["cited"]
    version = result.get("policy_version")
    exp = case["expected"]
    assertions = result["assertions"].get("week6", []) + result["assertions"].get("capstone", [])
    applicable = [a for a in assertions if a["applicable"]]
    return {
        "id": case["id"], "ticket_id": case["ticket_id"], "mode": case["mode"], "mode_family": case["mode_family"],
        "week6_id": case.get("week6_id"), "expected": exp,
        "trace_id": result["trace_id"], "outcome": result["outcome"], "decision": result["decision"],
        "intent": result["intent"], "policy_version": version, "injection_detected": result["injection_detected"],
        "outcome_correct": result["outcome"] == exp["outcome"],
        "decision_correct": result["decision"] == exp["decision"],
        "policy_version_correct": (version == exp.get("policy_version")) if exp.get("policy_version") else None,
        "consulted": [(c["article_id"], c.get("policy_version"), c.get("status")) for c in consulted],
        "cited_markers": [c["marker"] for c in cited],
        "superseded_consulted": [c["slug"] for c in consulted if c.get("status") == "superseded"],
        "wrong_version_consulted": [c["slug"] for c in consulted if version and c.get("policy_version") and c["policy_version"] != version],
        "wrong_version_cited": [c["slug"] for c in cited if version and c.get("policy_version") and c["policy_version"] != version],
        "assertions": assertions, "assertions_applicable": len(applicable),
        "assertions_passed": sum(1 for a in applicable if a["passed"]),
        "assertions_failed": [a["name"] for a in applicable if not a["passed"]],
        "assertions_pass": all(a["passed"] for a in applicable), "hard_failures": result["assertions"].get("hard_failures", []),
        "draft_rejected": result.get("draft_rejected"), "escalation": result.get("escalation"),
        "escalation_reason": (result.get("verification") or {}).get("escalation_reason"),
        "answer": result.get("answer"), "latency_ms": result["latency_ms"],
        "generation_latency_ms": (result.get("generation") or {}).get("latency_ms"),
        "tokens": result["meter"]["tokens"], "input_tokens": result["meter"]["input_tokens"],
        "output_tokens": result["meter"]["output_tokens"], "cost_usd": result["meter"]["cost_usd"],
        "model_calls": result["meter"]["model_calls"], "laps": result["meter"]["iterations"],
        "forced_calls": result["meter"]["forced_calls"], "tool_calls": result["meter"]["tool_calls"],
        "terminated_by": result.get("terminated_by"), "loop_end": result.get("loop_end"),
        "trajectory": result["trajectory"],
        "timeline": [{k: s.get(k) for k in ("step", "name", "status", "duration_ms", "kind", "forced_by", "server")} for s in result["timeline"]],
        "mcp": {k: result["mcp"].get(k) for k in ("available", "server", "transport", "calls", "latency_ms", "trace_id_echoed", "error")},
        "verification": {k: v for k, v in (result.get("verification") or {}).items()
                         if k in ("order_found", "entitlement_verified", "conflicts", "missing", "passages_retrieved", "passages_usable",
                                  "refund_assessment", "shipping_assessment")},
    }


def cmd_run(args):
    from capstone import deflection_agent as da
    from capstone import mcp_session, ticket_store
    import agent_runtime
    cases = load_cases()
    if args.subset and args.subset != "all":
        if args.subset == "refund":
            cases = [c for c in cases if c["expected"].get("policy_version")]
        elif args.subset == "capstone":
            cases = [c for c in cases if c["source"] == "capstone"]
        else:
            wanted = set(args.subset.split(","))
            cases = [c for c in cases if c["id"] in wanted or c["ticket_id"] in wanted]
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    label = re.sub(r"[^A-Za-z0-9._-]+", "-", args.label or "run")
    out_dir = RUNS / f"{label}_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"Week 12 integrated eval · {len(cases)} cases · planner={args.planner} policy_filter={not args.no_policy_filter} "
          f"· label {label}", flush=True)
    t0 = time.perf_counter()
    agent_runtime.warm_up()
    print(f"  model warm in {time.perf_counter() - t0:.0f} s", flush=True)
    rows, errors = [], []
    for i, case in enumerate(cases, 1):
        t = ticket_store.get_ticket(case["ticket_id"])
        ticket = {"ticket_id": t["ticket_id"], "customer_id": t["customer_id"], "message": t["description"],
                  "order_id": t.get("order_id"), "subject": t.get("subject")}
        started = time.perf_counter()
        try:
            result = da.handle_ticket(ticket, source=f"week12-eval:{label}", planner=args.planner,
                                      policy_filter=not args.no_policy_filter, tags=["week12-eval", label, case["id"]])
        except Exception as error:                 # a crash is a result too
            import traceback
            errors.append({"id": case["id"], "error": f"{type(error).__name__}: {error}", "traceback": traceback.format_exc()})
            print(f"  [{i}/{len(cases)}] {case['id']} CRASHED {type(error).__name__}: {error}", flush=True)
            continue
        (out_dir / f"{case['id']}.json").write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
        row = _row(case, result)
        rows.append(row)
        print(f"  [{i}/{len(cases)}] {case['id']} {case['ticket_id']} -> {row['outcome']}/{row['decision']} "
              f"{'OK ' if row['decision_correct'] else 'WRONG'} policy={row['policy_version']} "
              f"assert {row['assertions_passed']}/{row['assertions_applicable']} "
              f"{'' if not row['wrong_version_consulted'] else 'WRONG-VERSION-PASSAGE '}"
              f"{round((time.perf_counter() - started))}s laps={row['laps']} forced={row['forced_calls']} trace={row['trace_id']}", flush=True)
    mcp_session.close()
    from capstone.deflection_agent import PROMPT_VERSION
    import tracing
    run = {"label": label, "ran_at": now_iso(), "app_version": tracing.app_version(), "planner": args.planner,
           "policy_filter": not args.no_policy_filter,
           "prompt_version": PROMPT_VERSION if not args.no_policy_filter else "capstone-v1.0 (policy filter off)",
           "model": agent_runtime.MODEL_NAME, "n_cases": len(cases), "n_completed": len(rows), "errors": errors,
           "case_dir": out_dir.relative_to(ROOT).as_posix(), "rows": rows}
    path = _write(RUNS / f"{label}_{stamp}.json", run)
    print(f"\nSaved {path.relative_to(ROOT)}  ({len(rows)} rows, {len(errors)} errors)")
    _print_quick(run)
    return 0 if not errors else 1


def _print_quick(run):
    rows = run["rows"]
    n = len(rows)
    print(f"  decision correct: {sum(r['decision_correct'] for r in rows)}/{n} · outcome correct: {sum(r['outcome_correct'] for r in rows)}/{n} · "
          f"assertions all-pass: {sum(r['assertions_pass'] for r in rows)}/{n} · wrong-version passages: "
          f"{sum(1 for r in rows if r['wrong_version_consulted'])} · latency p50 {summarise([r['latency_ms'] for r in rows]).get('p50')} ms")


# ---------------------------------------------------------------------------
# judge (separate process: the 1.7B judge after the 1.5B drafter does not fit in 8 GB)
# ---------------------------------------------------------------------------

def _week6_case(case, row):
    from capstone.demo_data import days_since, orders, subscriptions, tickets
    t = tickets()[case["ticket_id"]]
    sub = subscriptions().get(t["customer_id"], {})
    o = orders().get(t.get("order_id") or "", {})
    refund = row["intent"] in ("refund", "duplicate_charge")
    ticket = {"ticket_id": t["ticket_id"], "tier": sub.get("tier") or "Standard", "message": t["description"],
              "charge_amount": o.get("amount") if (refund and o) else None,
              "days_since_charge": days_since(o.get("charge_date")) if (refund and o) else None,
              "duplicate_charge": bool(o and o.get("order_status") == "duplicate") if refund else False,
              "refund_requested": refund, "refund_window_days": sub.get("refund_window_days")}
    return {"id": case["id"], "ticket": ticket, "reference": case["reference"]}


def cmd_judge(args):
    import judge as judge_module
    run = _json(args.run)
    cases = {c["id"]: c for c in load_cases()}
    prompt = judge_module.load_prompt(ROOT / "week6" / "judge_v2.txt")
    todo = [r for r in run["rows"] if not (r.get("judge") and r["judge"].get("prompt_sha") == prompt["sha"])]
    print(f"Judge v2 ({judge_module.JUDGE_MODEL}, prompt sha {prompt['sha']}) over {len(todo)} of {len(run['rows'])} replies "
          f"from {args.run} ({len(run['rows']) - len(todo)} already judged)", flush=True)
    t0 = time.perf_counter()
    limit = int(args.limit) if getattr(args, "limit", None) else None
    for i, row in enumerate(todo, 1):
        if limit and i > limit:
            break
        case = cases[row["id"]]
        started = time.perf_counter()
        verdict = judge_module.judge_reply(prompt, _week6_case(case, row), row.get("answer") or "")
        row["judge"] = {"version": "v2", "verdict": verdict["verdict"], "parsed": verdict["parsed"],
                        "reason": verdict["reason"], "prompt_sha": prompt["sha"], "model": judge_module.JUDGE_MODEL,
                        "seconds": round(time.perf_counter() - started, 1), "judged_at": now_iso()}
        judged = [r for r in run["rows"] if r.get("judge")]
        # Resumable: the file is rewritten after every verdict, so a killed run keeps its progress.
        run["judge"] = {"version": "v2", "prompt": "week6/judge_v2.txt", "prompt_sha": prompt["sha"], "model": judge_module.JUDGE_MODEL,
                        "judged_at": now_iso(), "n_judged": len(judged), "n_rows": len(run["rows"]),
                        "complete": len(judged) == len(run["rows"]),
                        "calibration": "week6/agreement.json: 76.0% agreement with 25 blind hand labels (78.3% held out, kappa 0.41)"}
        _write(args.run, run)
        print(f"  [{i}/{len(todo)}] {row['id']} judge={verdict['verdict']}{'' if verdict['parsed'] else ' (unparsed)'} "
              f"assertions={'pass' if row['assertions_pass'] else 'fail'} {time.perf_counter() - started:.0f}s "
              f"(total {time.perf_counter() - t0:.0f}s)", flush=True)
    _write_labels_template(run)
    print(f"Saved judge verdicts into {args.run}; labelling sheet: {LABELS_TEMPLATE.relative_to(ROOT)}")
    return 0


def _write_labels_template(run):
    """A blind labelling sheet for the integrated replies: ticket + reference + reply, no judge result, shuffled."""
    cases = {c["id"]: c for c in load_cases()}
    rows = list(run["rows"])
    random.Random(12).shuffle(rows)
    sheet = {"criterion": "resolution (binary PASS/FAIL), exactly as week6/judge_v1.txt defines it",
             "protocol": "blind: label from ticket + reference facts + reply only; judge verdicts are NOT in this file",
             "run": run["label"], "replies_count": len(rows), "created_at": now_iso(),
             "labels": {r["id"]: {"ticket": cases[r["id"]]["ticket_id"], "reference": cases[r["id"]]["reference"],
                                  "reply": r.get("answer"), "label": None, "note": None} for r in rows}}
    _write(LABELS_TEMPLATE, sheet)


# ---------------------------------------------------------------------------
# report (integrated_eval.json + closed_loop.json)
# ---------------------------------------------------------------------------

def _by_mode(rows, key):
    out = {}
    for mode in sorted({r["mode"] for r in rows}):
        sub = [r for r in rows if r["mode"] == mode]
        passed = sum(1 for r in sub if key(r))
        out[mode] = {"label": MODE_LABELS.get(mode, mode), "n": len(sub), "passed": passed, "pass_rate_pct": pct(passed, len(sub))}
    return out


def _policy_stats(rows):
    policy = [r for r in rows if r["expected"].get("policy_version")]
    n = len(policy)
    return {"n": n,
            "superseded_citation_rate_pct": pct(sum(1 for r in policy if r["wrong_version_consulted"]), n),
            "superseded_passages_in_prompt": sum(1 for r in policy if r["wrong_version_consulted"]),
            "superseded_cited_by_model": sum(1 for r in policy if r["wrong_version_cited"]),
            "policy_version_correct": sum(1 for r in policy if r["policy_version_correct"]),
            "decision_correct": sum(1 for r in policy if r["decision_correct"]),
            "definition": "share of policy cases whose prompt contained a passage of the OTHER policy version than the "
                          "verified entitlement (counted on passages placed in the prompt, which is stricter than counting "
                          "only the markers the model wrote)"}


def _case_view(r):
    return {"id": r["id"], "trace_id": r["trace_id"], "policy_version": r["policy_version"], "consulted": r["consulted"],
            "wrong_version_consulted": r["wrong_version_consulted"], "decision": r["decision"],
            "expected": r["expected"]["decision"], "assertions_failed": r["assertions_failed"]}


def cmd_report(args):
    after = _json(args.run)
    rows = after["rows"]
    n = len(rows)
    labels = _json(LABELS) if LABELS.exists() else None
    judge_block = None
    judged = [r for r in rows if r.get("judge")]
    if judged:
        agree_assert = sum(1 for r in judged if (r["judge"]["verdict"] == "PASS") == r["assertions_pass"])
        judge_block = {
            "version": after.get("judge", {}).get("version", "v2"), "model": after.get("judge", {}).get("model"),
            "prompt_sha": after.get("judge", {}).get("prompt_sha"), "n_judged": len(judged),
            "pass": sum(1 for r in judged if r["judge"]["verdict"] == "PASS"),
            "pass_rate_pct": pct(sum(1 for r in judged if r["judge"]["verdict"] == "PASS"), len(judged)),
            "unparsed": sum(1 for r in judged if not r["judge"]["parsed"]),
            "by_mode": _by_mode(judged, lambda r: r["judge"]["verdict"] == "PASS"),
            "agreement_with_assertions_pct": pct(agree_assert, len(judged)),
            "agreement_with_assertions_note": "judge PASS/FAIL vs the deterministic assertion verdict on the same reply; a PROXY, not hand labels",
            "calibration": after.get("judge", {}).get("calibration"),
            "complete": after.get("judge", {}).get("complete", True), "n_rows": len(rows),
            "agreement_with_labels_pct": None, "labels_n": 0,
            "note": "hand labels on the integrated replies are pending: fill week12/labels_template.json -> week12/labels.json and re-run report",
        }
        if labels:
            lab = {k: v.get("label") for k, v in labels.get("labels", {}).items() if v.get("label") in ("PASS", "FAIL")}
            comparable = [r for r in judged if lab.get(r["id"])]
            if comparable:
                agree = sum(1 for r in comparable if r["judge"]["verdict"] == lab[r["id"]])
                judge_block.update(agreement_with_labels_pct=pct(agree, len(comparable)), labels_n=len(comparable),
                                   note=f"judge v2 vs {len(comparable)} hand labels on the integrated replies (week12/labels.json)")
    latencies = [r["latency_ms"] for r in rows]
    answered = [r for r in rows if r["outcome"] == "answered"]
    deflected_correct = [r for r in answered if r["decision_correct"] and r["assertions_pass"]]
    integrated = {
        "label": after["label"], "ran_at": after["ran_at"], "app_version": after.get("app_version"),
        "prompt_version": after.get("prompt_version"), "planner": after.get("planner"), "policy_filter": after.get("policy_filter"),
        "model": after.get("model"), "n_cases": n, "n_errors": len(after.get("errors", [])),
        "run_file": Path(args.run).resolve().relative_to(ROOT).as_posix(),
        "cases_source": "week12/eval_cases.jsonl: 26 Week 6 cases (reference facts untouched) + 14 capstone cases",
        "assertions": {"suite": "7 Week 6 assertions + 8 capstone assertions (src/ticket_assertions.py, src/capstone/capstone_assertions.py); run before the judge",
                       "passed": sum(1 for r in rows if r["assertions_pass"]), "pass_rate_pct": pct(sum(1 for r in rows if r["assertions_pass"]), n),
                       "individual_applicable": sum(r["assertions_applicable"] for r in rows),
                       "individual_passed": sum(r["assertions_passed"] for r in rows),
                       "failures_by_name": dict(Counter(a for r in rows for a in r["assertions_failed"])),
                       "guardrail_rejections": sum(1 for r in rows if r.get("draft_rejected"))},
        "outcomes": {"answered": len(answered), "escalated": n - len(answered),
                     "expected_escalations": sum(1 for r in rows if r["expected"]["outcome"] == "escalated"),
                     "outcome_correct": sum(1 for r in rows if r["outcome_correct"]), "decision_correct": sum(1 for r in rows if r["decision_correct"]),
                     "decision_accuracy_pct": pct(sum(1 for r in rows if r["decision_correct"]), n),
                     "deflected_and_correct": len(deflected_correct),
                     "deflection_rate_pct": pct(len(answered), n),
                     "clean_deflection_rate_pct": pct(len(deflected_correct), n)},
        "by_mode": _by_mode(rows, lambda r: r["assertions_pass"]),
        "by_mode_decision": _by_mode(rows, lambda r: r["decision_correct"]),
        "policy": _policy_stats(rows),
        "judge": judge_block,
        "judge_status": (after.get("judge") or {}).get("status") if not judge_block else "judged",
        "judge_attempts": (after.get("judge") or {}).get("attempts"),
        "latency": {**{k + "_ms": v for k, v in summarise(latencies).items() if k != "n"}, "n": len(latencies),
                    "generation_p50_ms": summarise([r["generation_latency_ms"] for r in rows if r.get("generation_latency_ms")]).get("p50")},
        "tokens": {"total": sum(r["tokens"] for r in rows), "mean": round(sum(r["tokens"] for r in rows) / n, 1) if n else None,
                   "input": sum(r["input_tokens"] for r in rows), "output": sum(r["output_tokens"] for r in rows)},
        "cost": {"total_usd": round(sum(r["cost_usd"] for r in rows), 6), "mean_usd": round(sum(r["cost_usd"] for r in rows) / n, 6) if n else None,
                 "basis": "stand-in rate card on locally counted tokens (agent_runtime.RATE_CARD); no provider bill exists"},
        "loop": {"laps_total": sum(r["laps"] for r in rows), "forced_calls_total": sum(r["forced_calls"] for r in rows),
                 "budget_terminations": sum(1 for r in rows if r["terminated_by"]),
                 "runs_with_forced_calls": sum(1 for r in rows if r["forced_calls"])},
        "failed_cases": [{"id": r["id"], "ticket_id": r["ticket_id"], "mode": r["mode"], "trace_id": r["trace_id"],
                          "outcome": r["outcome"], "decision": r["decision"], "expected": r["expected"]["decision"],
                          "assertions_failed": r["assertions_failed"], "wrong_version": r["wrong_version_consulted"]}
                         for r in rows if not (r["assertions_pass"] and r["decision_correct"])],
        "non_coverage": [
            "Hand labels exist for the 25 frozen Week 6 replies only; the integrated replies have a labelling sheet (week12/labels_template.json) and no labels yet, so judge agreement on integrated outputs is not measured",
            "The judge grades one criterion (resolution); tone, grammar and brand voice are not evaluated",
            "Only one model family (Qwen2.5-1.5B) and one hardware profile (8 GB M1) were measured; latency numbers do not transfer",
            "The corpus is 13 documents, not the 120 articles / 40 macros / 300 resolved tickets of the brief; recall over a large corpus is untested",
            "Order, entitlement and escalation are local demo adapters; production API failures, timeouts and stale data are not exercised",
            "Multi-turn conversations are smoke-tested, not evaluated as a suite",
            "One prompt-injection case and one PII probe; paraphrased injections are not covered (Week 8 showed the pattern list is bypassable)",
            "Shipping decisions use an approximate business-day calendar (weekends only, no public holidays)",
        ],
    }
    _write(W12 / "integrated_eval.json", integrated)
    print(f"Wrote week12/integrated_eval.json: assertions {integrated['assertions']['pass_rate_pct']}% · decisions "
          f"{integrated['outcomes']['decision_accuracy_pct']}% · superseded-citation rate {integrated['policy']['superseded_citation_rate_pct']}%")
    if args.before:
        before = _json(args.before)
        b_rows = [r for r in before["rows"] if r["expected"].get("policy_version")]
        a_rows = [r for r in rows if r["expected"].get("policy_version") and r["id"] in {x["id"] for x in b_rows}]
        loop = {
            "written_at": now_iso(),
            "failure": "capstone-v1.0 searched the help centre without the account's policy version; for a current-terms customer whose "
                       "message echoed the old 14-day window, every passage placed in the prompt came from the RETIRED policy (HC-009)",
            "root_cause": "retrieval ranks by similarity; the retired article is the closest text to a question phrased in its own terms. "
                          "The policy version is an account fact (entitlement), not a retrieval signal, so similarity alone cannot choose it",
            "fix": "capstone-v1.1: once check_entitlement has verified the account, the agent passes policy_version to search_help_centre "
                   "(the server drops the other version), a citation filter refuses passages of the other version, and a case with no "
                   "passage of the right version escalates instead of guessing",
            "regression_tests": ["tests/test_week12.py: 'current customer, filter on: refund_approved, only HC-008 consulted, policy_version enforced on the MCP call'",
                                 "tests/test_week12.py: 'filter off (v1.0): the retired policy can be cited and the guardrail catches the denial'",
                                 "tests/test_week12.py: 'legacy customer: refund_denied under legacy-14-day, cites HC-009 only'",
                                 "tests/test_week12.py: 'retired policy cited to a current account fails' (capstone_assertions.policy_version_consistent)"],
            "before": {"run": Path(args.before).resolve().relative_to(ROOT).as_posix(), "label": before["label"], "ran_at": before["ran_at"],
                       "prompt_version": before.get("prompt_version"), **_policy_stats(b_rows), "cases": [_case_view(r) for r in b_rows]},
            "after": {"run": Path(args.run).resolve().relative_to(ROOT).as_posix(), "label": after["label"], "ran_at": after["ran_at"],
                      "prompt_version": after.get("prompt_version"), **_policy_stats(a_rows), "cases": [_case_view(r) for r in a_rows]},
        }
        _write(W12 / "closed_loop.json", loop)
        print(f"Wrote week12/closed_loop.json: superseded-citation rate {loop['before']['superseded_citation_rate_pct']}% -> "
              f"{loop['after']['superseded_citation_rate_pct']}% (n={loop['after']['n']})")
    return 0


# ---------------------------------------------------------------------------
# trajectory (Week 8 scorer adapted to the integrated tools)
# ---------------------------------------------------------------------------

def score_trajectory(case, row):
    accepted = case["expected"]["tool_sequences"]
    steps = [s for s in row["trajectory"] if s["type"] == "tool"]
    model_steps = [s for s in steps if not s.get("forced_by")]
    done = []
    choices = correct = 0
    events, modes = [], Counter()
    seen = set()

    def consistent_next():
        return {seq[len(done)] for seq in accepted if seq[:len(done)] == done and len(seq) > len(done)}

    for s in steps:
        name = s["name"]
        if not s.get("forced_by"):
            choices += 1
            if name in consistent_next():
                correct += 1
            elif name in done:
                modes["redundant_call"] += 1
                events.append(f"lap {s['lap']}: {name} again")
            else:
                modes["wrong_or_out_of_order"] += 1
                events.append(f"lap {s['lap']}: {name} when {sorted(consistent_next())} expected")
        else:
            modes["forced_by_guard"] += 1
            events.append(f"lap {s['lap']}: {name} forced ({s['forced_by']})")
        key = (name, json.dumps(s.get("arguments"), sort_keys=True))
        if key in seen:
            modes["redundant_call"] += 1
        seen.add(key)
        if s.get("error") and name != "lookup_order":
            modes["tool_error"] += 1
        if name not in done:
            done.append(name)
    for s in row["trajectory"]:
        if s["type"] == "prose":
            modes["prose_instead_of_action"] += 1
        if s["type"] == "answer_rejected":
            modes["done_said_early_caught"] += 1
        if s["type"] == "budget":
            modes["budget_exhausted"] += 1
    required_tools = {t for seq in accepted for t in seq}
    completed = [d for d in done if d in required_tools or d != "escalate_ticket"]
    path_ok = any(completed[:len(seq)] == seq and set(seq) <= set(completed) for seq in accepted)
    ordered = any(all(completed.index(a) < completed.index(b) for a, b in zip(seq, seq[1:]) if a in completed and b in completed)
                  for seq in accepted)
    outcome_ok = row["decision_correct"] and row["outcome_correct"]
    trajectory_pass = path_ok and not modes["wrong_or_out_of_order"] and not modes["forced_by_guard"]
    return {"id": row["id"], "ticket_id": row["ticket_id"], "trace_id": row["trace_id"],
            "expected_sequences": accepted, "actual_sequence": [s["name"] + ("*" if s.get("forced_by") else "") for s in steps],
            "model_chosen_steps": len(model_steps), "forced_steps": len(steps) - len(model_steps),
            "tool_choices": choices, "correct_tool_choices": correct,
            "tool_choice_accuracy_pct": pct(correct, choices), "path_complete": path_ok, "order_correct": ordered,
            "outcome_correct": outcome_ok, "trajectory_pass": trajectory_pass,
            "right_answer_wrong_path": outcome_ok and not trajectory_pass, "wrong_answer_right_path": trajectory_pass and not outcome_ok,
            "laps": row["laps"], "modes": dict(modes), "events": events, "terminated_by": row["terminated_by"], "loop_end": row["loop_end"]}


def cmd_trajectory(args):
    run = _json(args.run)
    cases = {c["id"]: c for c in load_cases()}
    rows = run["rows"] if args.all else [r for r in run["rows"] if r["expected"]["tool_sequences"] != [["escalate_ticket"]]]
    scored = [score_trajectory(cases[r["id"]], r) for r in rows]
    n = len(scored)
    choices = sum(s["tool_choices"] for s in scored)
    correct = sum(s["correct_tool_choices"] for s in scored)
    outcome = sum(1 for s in scored if s["outcome_correct"])
    traj = sum(1 for s in scored if s["trajectory_pass"])
    summary = {"n": n, "run": Path(args.run).resolve().relative_to(ROOT).as_posix(), "planner": run.get("planner"), "scored_at": now_iso(),
               "tool_choices": choices, "correct_tool_choices": correct, "tool_choice_accuracy_pct": pct(correct, choices),
               "correct_order_pct": pct(sum(1 for s in scored if s["order_correct"]), n),
               "path_complete_pct": pct(sum(1 for s in scored if s["path_complete"]), n),
               "outcome_pass_rate_pct": pct(outcome, n), "trajectory_pass_rate_pct": pct(traj, n),
               "gap_pp": round((pct(outcome, n) or 0) - (pct(traj, n) or 0), 1),
               "right_answer_wrong_path": sum(1 for s in scored if s["right_answer_wrong_path"]),
               "wrong_answer_right_path": sum(1 for s in scored if s["wrong_answer_right_path"]),
               "model_chosen_steps": sum(s["model_chosen_steps"] for s in scored), "forced_steps": sum(s["forced_steps"] for s in scored),
               "mode_totals": dict(sum((Counter(s["modes"]) for s in scored), Counter())),
               "definitions": {
                   "tool_choice_accuracy": "model-chosen tool calls that were the next step of an accepted sequence given what was already done; guard-forced calls are not the model's choices and are excluded from numerator and denominator",
                   "trajectory_pass": "the completed tools form an accepted sequence, nothing was out of order, and no required tool had to be forced by the guard",
                   "outcome_pass": "decision and outcome equal the expected ones in week12/eval_cases.jsonl",
                   "gap": "outcome pass rate minus trajectory pass rate (Week 8 definition)"}}
    _write(W12 / "trajectory_eval.json", {"summary": summary, "cases": scored})
    print(json.dumps(summary, indent=1))
    return 0


# ---------------------------------------------------------------------------
# taxonomy (rule-based detectors over stored runs; the narrative is written by hand in the report)
# ---------------------------------------------------------------------------

DETECTORS = [
    ("wrong_policy_version_in_prompt", "a passage of the other refund-policy version (vs the verified entitlement) reached the model",
     "wrong answer to the customer (money)", True, lambda r: bool(r["wrong_version_consulted"])),
    ("decision_contradicted_by_draft", "the verified decision was right but the drafted text denied an eligible refund or promised an ineligible one; the guardrail rejected or escalated it",
     "embarrasses the client / blocks a deflection", True,
     lambda r: bool(r.get("draft_rejected")) or any(a in ("decision_consistent", "no_refund_outside_window", "no_denial_inside_window") for a in r["assertions_failed"])),
    ("missing_amount_or_ticket_id", "the reply omits the exact amount or the ticket id the policy requires", "annoys the user", False,
     lambda r: any(a in ("refund_amount_numeric", "ticket_id_echoed") for a in r["assertions_failed"])),
    ("escalation_tag_wrong", "Priority tag missing, or present on a Standard ticket", "pages Tier 2 for nothing / misses a Priority SLA", False,
     lambda r: "escalation_tag_iff_priority" in r["assertions_failed"]),
    ("reply_cut_off_or_scaffolding", "the reply hit the token limit or leaked prompt scaffolding", "embarrasses the client", False,
     lambda r: any(a in ("not_cut_off", "no_prompt_scaffolding") for a in r["assertions_failed"])),
    ("guard_forced_tool_call", "the model did not call a required tool and the evidence guard ran it", "cost and latency; not a customer-visible error", True,
     lambda r: r["forced_calls"] > 0 and r.get("laps", 0) > 0),
    ("wrong_decision_or_outcome", "the decision or outcome differs from the expected one for the case", "wrong answer or an unnecessary escalation", False,
     lambda r: not r["decision_correct"]),
    ("mcp_or_tool_error", "the help-centre MCP server or a business tool failed during the request", "no citation possible; forced escalation", True,
     lambda r: (r["mcp"].get("error") is not None) or any(t["status"] == "error" and t["name"] not in ("lookup_order", "validation.assertions") for t in r["timeline"])),
    ("wall_clock_or_budget", "the agent loop ended on a budget rather than on complete evidence", "latency; partial evidence", True,
     lambda r: bool(r["terminated_by"])),
]
SEVERITY_RANK = {"wrong answer to the customer (money)": 1, "wrong answer or an unnecessary escalation": 2,
                 "embarrasses the client / blocks a deflection": 2, "embarrasses the client": 3,
                 "pages Tier 2 for nothing / misses a Priority SLA": 3, "no citation possible; forced escalation": 3,
                 "annoys the user": 4, "cost and latency; not a customer-visible error": 5, "latency; partial evidence": 5}


def cmd_taxonomy(args):
    runs = [_json(p) for p in args.run]
    rows = [dict(r, run_label=run["label"]) for run in runs for r in run["rows"]]
    n = len(rows)
    modes = []
    for name, description, severity, integration_created, detect in DETECTORS:
        hits = [r for r in rows if detect(r)]
        if not hits:
            continue
        modes.append({"name": name, "description": description, "severity": severity, "severity_rank": SEVERITY_RANK.get(severity, 9),
                      "new_in_integration": integration_created, "count": len(hits), "frequency_pct": pct(len(hits), n),
                      "example_trace_id": hits[0]["trace_id"], "example_case": hits[0]["id"], "example_run": hits[0]["run_label"],
                      "trace_ids": [h["trace_id"] for h in hits][:12],
                      "cases": [{"id": h["id"], "run": h["run_label"], "trace_id": h["trace_id"], "decision": h["decision"],
                                 "expected": h["expected"]["decision"], "assertions_failed": h["assertions_failed"]} for h in hits][:12]})
    modes.sort(key=lambda m: (-m["count"], m["severity_rank"]))
    failed = [r for r in rows if not (r["assertions_pass"] and r["decision_correct"])]
    out = {"ran_at": now_iso(), "runs": [{"label": run["label"], "file": p, "n": len(run["rows"])} for run, p in zip(runs, args.run)],
           "n_runs": n, "n_failed": len(failed), "n_with_any_mode": len([r for r in rows if any(d[4](r) for d in DETECTORS)]),
           "modes": modes,
           "method": "rule-based detectors over the stored rows (assertion names, decision vs expected, forced calls, MCP errors, budgets); "
                     "every count points at real trace ids in traces/traces.jsonl. The reading of each trace is in reports/error_taxonomy.md"}
    _write(W12 / "error_taxonomy.json", out)
    for m in modes:
        print(f"  {m['count']:>3} ({m['frequency_pct']:>5}%)  {m['name']:<36} {m['severity']:<45} e.g. {m['example_trace_id']} {'NEW' if m['new_in_integration'] else ''}")
    return 0


# ---------------------------------------------------------------------------
# reopen (SIMULATED second contact)
# ---------------------------------------------------------------------------

CONCERNS = {
    "timing": (r"\b(when|how long|how soon|days|business days|timeframe)\b", r"\b(business days|within \d+|5 to 10|7 to 14|days)\b"),
    "amount": (r"\$|\bamount\b|\bhow much\b|\bfull\b", r"\$\s?\d"),
    "next_step": (r"\b(what (do|should) i|next|do i need|anything else|how do i)\b", r"\b(you do not need|no further action|nothing else|next step|we (have|will) (open|request|raise))\b"),
    "confirmation": (r"\b(confirm|confirmation|email|notified|reference)\b", r"\b(reference|confirmation|email you|notify)\b"),
    "method": (r"\b(card|original payment|bank|account)\b", r"\b(original payment method|card|account)\b"),
}


ACKNOWLEDGEMENT = re.compile(r"^(thank(s| you)|great|perfect|ok(ay)?|got it|understood|appreciate)", re.I)


def is_reopen(follow):
    """A follow-up counts as a reopen only when the customer still asks for something:
    a question mark, or one of the concern patterns. A thank-you is not a second contact."""
    text = (follow or "").strip()
    if not text or text.upper().startswith("NO FOLLOW-UP"):
        return False
    asks = "?" in text or any(re.search(a, text, re.I) for a, _ in CONCERNS.values())
    return asks and not (ACKNOWLEDGEMENT.match(text) and "?" not in text)


def rescore_reopen(path=None):
    """Re-derive reopened/preventable from the saved follow-ups (no model)."""
    path = Path(path or W12 / "reopen_eval.json")
    out = _json(path)
    for x in out["results"]:
        x["reopened"] = is_reopen(x["simulated_follow_up"])
        x["preventable"] = x["reopened"] and bool(x["missing_information"])
    reopened = sum(1 for x in out["results"] if x["reopened"])
    out.update(simulated_reopen_rate_pct=pct(reopened, len(out["results"])), reopened=reopened,
               preventable=sum(1 for x in out["results"] if x["preventable"]),
               preventable_rate_pct=pct(sum(1 for x in out["results"] if x["preventable"]), len(out["results"])),
               reopen_rule="reopened = the follow-up asks something (a question mark or a timing/amount/next-step/confirmation/"
                           "payment-method concern); a thank-you or acknowledgement is not a second contact",
               rescored_at=now_iso())
    _write(path, out)
    print(f"rescored: simulated reopen rate {out['simulated_reopen_rate_pct']}% ({reopened}/{len(out['results'])}), preventable {out['preventable_rate_pct']}%")
    return 0


def cmd_reopen(args):
    if getattr(args, "rescore", False):
        return rescore_reopen()
    import agent_runtime
    from capstone.demo_data import tickets
    run = _json(args.run)
    deflected = [r for r in run["rows"] if r["outcome"] == "answered"]
    chosen = random.Random(int(args.seed)).sample(deflected, min(int(args.n), len(deflected)))
    print(f"Simulated second contact: {len(chosen)} deflected replies from {args.run} (seed {args.seed})", flush=True)
    agent_runtime.warm_up()
    results = []
    for i, r in enumerate(chosen, 1):
        t = tickets()[r["ticket_id"]]
        messages = [{"role": "system", "content": "You are the customer who wrote the support ticket below. You just received the support reply. "
                     "Write the ONE follow-up message you would still send, in one or two sentences, as the customer. If the reply left you nothing to ask, reply exactly: NO FOLLOW-UP."},
                    {"role": "user", "content": f"Your ticket: {t['description']}\n\nSupport reply:\n{r['answer']}\n\nYour follow-up:"}]
        resp = agent_runtime.chat(messages, max_new_tokens=60)
        follow = resp["text"].strip()
        none = not is_reopen(follow)
        concerns, preventable = [], False
        if not none:
            for name, (asks, answered_by) in CONCERNS.items():
                if re.search(asks, follow, re.I):
                    covered = bool(re.search(answered_by, r["answer"] or "", re.I))
                    concerns.append({"concern": name, "covered_in_first_reply": covered})
                    preventable = preventable or not covered
        results.append({"id": r["id"], "ticket_id": r["ticket_id"], "trace_id": r["trace_id"], "decision": r["decision"],
                        "first_reply": r["answer"], "simulated_follow_up": follow, "reopened": not none,
                        "concerns": concerns, "missing_information": [x["concern"] for x in concerns if not x["covered_in_first_reply"]],
                        "preventable": preventable, "follow_up_tokens": resp["output_tokens"], "latency_ms": resp["latency_ms"]})
        print(f"  [{i}/{len(chosen)}] {r['id']} reopened={not none} preventable={preventable} :: {follow[:100]!r}", flush=True)
    reopened = sum(1 for x in results if x["reopened"])
    out = {"label": "SIMULATED second-contact evaluation (not production reopen data)", "ran_at": now_iso(), "run": args.run,
           "seed": int(args.seed), "n": len(results), "deflected_in_run": len(deflected), "n_cases_in_run": len(run["rows"]),
           "deflection_rate_pct": pct(len(deflected), len(run["rows"])),
           "simulated_reopen_rate_pct": pct(reopened, len(results)), "reopened": reopened,
           "preventable": sum(1 for x in results if x["preventable"]), "preventable_rate_pct": pct(sum(1 for x in results if x["preventable"]), len(results)),
           "method": "the drafter model (Qwen2.5-1.5B) role-plays the customer and writes one follow-up to the first reply; code checks whether the "
                     "concern it raises (timing, amount, next step, confirmation, payment method) was already covered by the first reply. "
                     "Preventable = the follow-up asks for something the first reply could have included.",
           "results": results}
    _write(W12 / "reopen_eval.json", out)
    print(f"simulated reopen rate {out['simulated_reopen_rate_pct']}% ({reopened}/{len(results)}), preventable {out['preventable_rate_pct']}%")
    return 0


# ---------------------------------------------------------------------------
# cost and 10x (numbers from the run; assumptions stated)
# ---------------------------------------------------------------------------

ASSUMPTIONS = {
    "machine_usd_per_hour": 0.35,
    "machine_note": "0.35 USD/h stands in for an on-prem Apple-silicon box or a small GPU VM (A10-class spot ~0.3-0.6 USD/h); the measured latency is from an 8 GB M1 laptop, which is slower than either",
    "api_rate_card": {"input_per_mtok": 0.15, "output_per_mtok": 0.60, "note": "GPT-4o-mini class stand-in; used since Week 7"},
    "embedding_api_per_mtok": 0.02, "embedding_tokens_per_query": 60,
    "postgres_usd_per_month": 15.0, "mcp_server_usd_per_month": 10.0, "observability_usd_per_month": 5.0,
    "agent_hourly_loaded_cost_usd": 28.0, "tier1_handle_minutes": 8,
    "peak_factor": 3.0,
}


def cmd_cost(args):
    run = _json(args.run)
    rows = run["rows"]
    n = len(rows)
    answered = [r for r in rows if r["outcome"] == "answered"]
    clean = [r for r in answered if r["decision_correct"] and r["assertions_pass"]]
    lat = summarise([r["latency_ms"] for r in rows])
    gen = summarise([r["generation_latency_ms"] for r in rows if r.get("generation_latency_ms")])
    mcp = summarise([r["mcp"]["latency_ms"] for r in rows if r["mcp"].get("calls")])
    tokens_in = sum(r["input_tokens"] for r in rows)
    tokens_out = sum(r["output_tokens"] for r in rows)
    a = ASSUMPTIONS
    api_cost_total = tokens_in * a["api_rate_card"]["input_per_mtok"] / 1e6 + tokens_out * a["api_rate_card"]["output_per_mtok"] / 1e6
    embed_cost_total = n * a["embedding_tokens_per_query"] * a["embedding_api_per_mtok"] / 1e6
    compute_seconds = sum(r["latency_ms"] for r in rows) / 1000.0
    compute_cost_total = compute_seconds / 3600.0 * a["machine_usd_per_hour"]
    monthly_fixed = a["postgres_usd_per_month"] + a["mcp_server_usd_per_month"] + a["observability_usd_per_month"]
    fixed_per_ticket = monthly_fixed / 2000.0
    per_request_local = compute_cost_total / n + embed_cost_total / n + fixed_per_ticket
    per_request_api = api_cost_total / n + embed_cost_total / n + fixed_per_ticket
    deflect_rate = len(clean) / n if n else 0
    per_deflected_local = per_request_local / deflect_rate if deflect_rate else None
    per_deflected_api = per_request_api / deflect_rate if deflect_rate else None
    human_cost_per_ticket = a["agent_hourly_loaded_cost_usd"] * a["tier1_handle_minutes"] / 60.0
    mean_s = lat["mean"] / 1000.0
    p95_s = lat["p95"] / 1000.0

    def load(tickets, seconds, label):
        rps_avg = tickets / seconds
        rps_peak = rps_avg * a["peak_factor"]
        return {"label": label, "tickets": tickets, "window_seconds": seconds,
                "requests_per_second_avg": round(rps_avg, 4), "requests_per_second_peak": round(rps_peak, 4),
                "concurrent_requests_avg": round(rps_avg * mean_s, 2), "concurrent_requests_peak": round(rps_peak * mean_s, 2),
                "concurrent_at_p95_peak": round(rps_peak * p95_s, 2)}
    current = load(2000, 30 * 86400, "current: 2,000 tickets/month")
    black_friday = load(20000, 7 * 86400, "Black Friday: 20,000 tickets/week")
    serial_capacity_per_hour = 3600.0 / mean_s if mean_s else None
    workers_needed_bf = black_friday["requests_per_second_peak"] * mean_s
    out = {
        "ran_at": now_iso(), "run": args.run, "n_requests": n, "deflected_clean": len(clean), "clean_deflection_rate_pct": pct(len(clean), n),
        "measured": {"latency_ms": lat, "generation_latency_ms": gen, "mcp_latency_ms": mcp, "tokens_in": tokens_in, "tokens_out": tokens_out,
                     "tokens_per_request": round((tokens_in + tokens_out) / n, 1) if n else None,
                     "model_calls_per_request": round(sum(r["model_calls"] for r in rows) / n, 2) if n else None,
                     "machine": "8 GB Apple M1 laptop, MPS fp16, one request at a time; drafter + embedder + reranker in process"},
        "assumptions": a,
        "cost_per_request_usd": {"local_compute_basis": round(per_request_local, 5), "api_rate_card_basis": round(per_request_api, 5),
                                 "breakdown_local": {"compute": round(compute_cost_total / n, 5), "embedding": round(embed_cost_total / n, 6),
                                                     "fixed_infra_share": round(fixed_per_ticket, 5), "tools_mcp": 0.0,
                                                     "tools_note": "order/entitlement/escalation are local calls (<5 ms); the MCP server is in the fixed share"}},
        "per_deflected_ticket_usd": round(per_deflected_local, 5) if per_deflected_local else None,
        "per_deflected_ticket_usd_api_basis": round(per_deflected_api, 5) if per_deflected_api else None,
        "basis_short": f"local compute at {a['machine_usd_per_hour']} USD/h x measured latency + infra share; clean deflections only",
        "human_cost_per_ticket_usd": round(human_cost_per_ticket, 2),
        "load": {"current": current, "black_friday": black_friday},
        "capacity": {"model": "one in-process model behind the server's single lock (serial)", "mean_latency_s": round(mean_s, 1),
                     "p95_latency_s": round(p95_s, 1), "serial_requests_per_hour": round(serial_capacity_per_hour, 1) if serial_capacity_per_hour else None,
                     "serial_requests_per_day": round(serial_capacity_per_hour * 24, 0) if serial_capacity_per_hour else None,
                     "current_peak_needs_workers": round(current["requests_per_second_peak"] * mean_s, 2),
                     "black_friday_peak_needs_workers": round(workers_needed_bf, 2),
                     "black_friday_workers_recommended": int(workers_needed_bf * 1.5 + 0.999),
                     "retrieval": {"pgvector": "13 docs / 189 chunks: exact scan < 10 ms; at 10x corpus still < 50 ms with HNSW",
                                   "bm25": "in-memory index rebuilt per process", "reranker": "cross-encoder ~100-300 ms per query on CPU"},
                     "mcp_server": "one uvicorn process; the embedder and reranker load once; one search ~0.2-1 s warm; stateless HTTP scales horizontally",
                     "rate_limits": "none for local models; on the API basis 20k tickets/week x ~1.3k tokens = ~26M tokens/week, inside typical tier limits but needs retries/backoff",
                     "first_failure_point": "the serial model lock: at the Black Friday peak the queue grows faster than one worker drains it"},
        "tenx_plan": [
            "Run the drafter as a worker pool (N = peak RPS x mean latency x 1.5) behind a queue; the server already returns 409 instead of queueing, so add a job queue (Redis/RQ or Celery) and poll for the result",
            "Move generation off the 1.5B local model to a hosted endpoint or a GPU box; measured mean latency is the lever, not token cost",
            "Keep the MCP help-centre server stateless and run 2+ replicas; cache search results per (query, policy_version) for 5 minutes",
            "Postgres: HNSW index on chunks.embedding, pooled connections, read replica for retrieval",
            "Observability: rotate traces.jsonl daily; the SQLite index becomes Postgres once >1 writer exists",
            "Pre-check the cheap deterministic paths (sensitive intents escalate in <50 ms with no model) so they never wait behind generation",
        ],
    }
    _write(W12 / "cost_and_scaling.json", out)
    print(json.dumps({k: out[k] for k in ("per_deflected_ticket_usd", "per_deflected_ticket_usd_api_basis", "load", "capacity")}, indent=1))
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description="Week 12 evaluation")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("sample"); p.add_argument("--seed", default=20261104); p.add_argument("--n", default=10); p.set_defaults(func=cmd_sample)
    e = sub.add_parser("eval"); es = e.add_subparsers(dest="eval_cmd", required=True)
    p = es.add_parser("run"); p.add_argument("--label", default="run"); p.add_argument("--planner", choices=["model", "code"], default="model")
    p.add_argument("--no-policy-filter", action="store_true"); p.add_argument("--subset", default="all"); p.set_defaults(func=cmd_run)
    p = es.add_parser("judge"); p.add_argument("--run", required=True); p.add_argument("--limit", default=None); p.set_defaults(func=cmd_judge)
    p = es.add_parser("report"); p.add_argument("--run", required=True); p.add_argument("--before"); p.set_defaults(func=cmd_report)
    p = sub.add_parser("trajectory"); p.add_argument("--run", required=True); p.add_argument("--all", action="store_true"); p.set_defaults(func=cmd_trajectory)
    p = sub.add_parser("taxonomy"); p.add_argument("--run", action="append", required=True); p.set_defaults(func=cmd_taxonomy)
    p = sub.add_parser("reopen"); p.add_argument("--run", required=False); p.add_argument("--n", default=10); p.add_argument("--seed", default=20261104)
    p.add_argument("--rescore", action="store_true", help="re-derive reopened/preventable from week12/reopen_eval.json without the model"); p.set_defaults(func=cmd_reopen)
    p = sub.add_parser("cost"); p.add_argument("--run", required=True); p.set_defaults(func=cmd_cost)
    p = sub.add_parser("report"); p.set_defaults(func=lambda a: _reports_md())
    args = parser.parse_args(argv)
    return args.func(args)


def _reports_md():
    import week12_reports
    return week12_reports.main()


if __name__ == "__main__":
    sys.exit(main())
