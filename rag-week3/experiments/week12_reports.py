"""Write reports/*.md from the week12/*.json results. Tables only come from
the JSON; the hand-written readings live between HAND-WRITTEN markers and are
preserved when the file is regenerated."""

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
W12 = ROOT / "week12"
REPORTS = ROOT / "reports"
MARK_START, MARK_END = "<!-- HAND-WRITTEN: start -->", "<!-- HAND-WRITTEN: end -->"


def _json(name):
    p = W12 / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _keep_hand_written(path):
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8")
    m = re.search(re.escape(MARK_START) + r"(.*?)" + re.escape(MARK_END), text, re.S)
    return m.group(1) if m else None


def _write(path, generated, hand_default):
    hand = _keep_hand_written(path) or hand_default
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(generated.rstrip() + f"\n\n{MARK_START}{hand}{MARK_END}\n", encoding="utf-8")
    print(f"  wrote {path.relative_to(ROOT)}")


def pct(v):
    return "—" if v is None else f"{v}%"


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def integrated_report():
    e = _json("integrated_eval.json")
    loop = _json("closed_loop.json")
    if not e:
        print("  integrated_eval.json missing; skipped"); return
    out = [f"# Integrated evaluation report — Week 12\n",
           f"Generated {now()} by `python main.py capstone report` from `week12/integrated_eval.json` "
           f"(run `{e.get('run_file')}`, {e['ran_at']}, build `{e.get('app_version')}`, prompt `{e.get('prompt_version')}`, "
           f"planner `{e.get('planner')}`, model `{e.get('model')}`).\n",
           "## Cases\n",
           f"- **{e['n_cases']} cases** ({e['cases_source']}); {e['n_errors']} crashed.",
           f"- Assertions run first, the judge second; they are reported separately below.\n",
           "## Assertions (deterministic, run before the judge)\n",
           f"- Cases with every applicable assertion passing: **{e['assertions']['passed']}/{e['n_cases']} = {pct(e['assertions']['pass_rate_pct'])}**",
           f"- Individual assertions: {e['assertions']['individual_passed']}/{e['assertions']['individual_applicable']} passed",
           f"- Output-guardrail rejections (a first draft failed a hard check and was regenerated or escalated): {e['assertions']['guardrail_rejections']}",
           "- Failures by assertion: " + (", ".join(f"`{k}` × {v}" for k, v in sorted(e['assertions']['failures_by_name'].items(), key=lambda kv: -kv[1])) or "none") + "\n",
           "## Decisions and outcomes\n",
           f"- Decision correct (vs the expected decision per case): **{e['outcomes']['decision_correct']}/{e['n_cases']} = {pct(e['outcomes']['decision_accuracy_pct'])}**",
           f"- Outcome correct (answered vs escalated): {e['outcomes']['outcome_correct']}/{e['n_cases']}",
           f"- Answered {e['outcomes']['answered']}, escalated {e['outcomes']['escalated']} (expected escalations: {e['outcomes']['expected_escalations']})",
           f"- Deflection rate (answered ÷ cases): {pct(e['outcomes']['deflection_rate_pct'])}; **clean deflection rate** (answered, correct decision, all assertions pass): {pct(e['outcomes']['clean_deflection_rate_pct'])}\n",
           "## Pass rate by error-taxonomy mode\n",
           "| mode | what it covers | n | assertions all-pass | decision correct |", "|---|---|---|---|---|"]
    for mode, v in e["by_mode"].items():
        d = e["by_mode_decision"].get(mode, {})
        out.append(f"| {mode} | {v['label']} | {v['n']} | {v['passed']}/{v['n']} ({pct(v['pass_rate_pct'])}) | {d.get('passed')}/{d.get('n')} ({pct(d.get('pass_rate_pct'))}) |")
    p = e["policy"]
    out += ["", "## Refund policy version\n",
            f"- Policy cases: {p['n']}; policy version chosen correctly: {p['policy_version_correct']}/{p['n']}; decision correct: {p['decision_correct']}/{p['n']}",
            f"- **Superseded-policy citation rate: {pct(p['superseded_citation_rate_pct'])}** ({p['superseded_passages_in_prompt']} cases had a wrong-version passage in the prompt; "
            f"{p['superseded_cited_by_model']} had the model cite one)",
            f"- Definition: {p['definition']}"]
    if loop:
        out += ["", f"### Before / after the fix (closed loop, `week12/closed_loop.json`)\n",
                f"| | before (`{loop['before']['label']}`, {loop['before']['prompt_version']}) | after (`{loop['after']['label']}`, {loop['after']['prompt_version']}) |", "|---|---|---|",
                f"| policy cases | {loop['before']['n']} | {loop['after']['n']} |",
                f"| superseded-policy citation rate | **{pct(loop['before']['superseded_citation_rate_pct'])}** | **{pct(loop['after']['superseded_citation_rate_pct'])}** |",
                f"| wrong-version passage in prompt | {loop['before']['superseded_passages_in_prompt']} | {loop['after']['superseded_passages_in_prompt']} |",
                f"| decision correct | {loop['before']['decision_correct']} | {loop['after']['decision_correct']} |",
                "", f"- Failure: {loop['failure']}", f"- Root cause: {loop['root_cause']}", f"- Fix: {loop['fix']}",
                "- Regression tests: " + "; ".join(loop["regression_tests"]),
                "", "Before-run cases (trace ids are real records in `traces/traces.jsonl`):", "",
                "| case | trace | passages in prompt | decision (expected) |", "|---|---|---|---|"]
        for c in loop["before"]["cases"]:
            passages = ", ".join(f"{a}/{v or '-'}" for a, v, _ in c["consulted"]) or "none"
            out.append(f"| {c['id']} | `{c['trace_id']}` | {passages} | {c['decision']} ({c['expected']}) |")
    j = e.get("judge")
    out += ["", "## LLM judge (judge v2, separate process, after the assertions)\n"]
    if j:
        out += [f"- Judge: `{j['version']}` ({j['model']}, prompt sha `{j['prompt_sha']}`), {j['n_judged']} replies judged, {j['unparsed']} unparsed",
                f"- Judge PASS rate on integrated replies: {j['pass']}/{j['n_judged']} = {pct(j['pass_rate_pct'])}",
                f"- **Judge agreement with hand labels on the 25 Week 6 replies (the validation that exists): 76.0% (78.3% held out, κ 0.41) — below the 85% target.** Source: `week6/agreement.json`.",
                f"- Judge agreement with hand labels on the integrated replies: **{pct(j['agreement_with_labels_pct'])}** (n={j['labels_n']}). {j['note']}",
                f"- Judge agreement with the assertion verdict (a proxy, not a label): {pct(j['agreement_with_assertions_pct'])}",
                "", "| mode | judge PASS |", "|---|---|"] + [f"| {m} | {v['passed']}/{v['n']} ({pct(v['pass_rate_pct'])}) |" for m, v in j["by_mode"].items()]
    else:
        out += [f"- Status: {e.get('judge_status') or 'not run'}"] + [
            f"- Attempt {i}: started {a['started']}, stopped {a['stopped']}, verdicts {a['judged']} — {a['note']}"
            for i, a in enumerate(e.get("judge_attempts") or [], 1)] + [
            "- Re-run: `python main.py capstone eval judge --run <run>` (resumable; a separate process with the drafter unloaded).",
                "- The judge's only validation is the Week 6 one: 76.0% agreement with 25 blind hand labels (78.3% held out, κ 0.41), below the 85% target."]
    lat, tok, cost = e["latency"], e["tokens"], e["cost"]
    out += ["", "## Latency, tokens, cost\n",
            f"- Latency per request (end to end, n={lat['n']}): p50 {lat['p50_ms']/1000:.1f} s · p95 {lat['p95_ms']/1000:.1f} s · mean {lat['mean_ms']/1000:.1f} s · max {lat['max_ms']/1000:.1f} s; generation p50 {(lat.get('generation_p50_ms') or 0)/1000:.1f} s",
            f"- Tokens: {tok['total']} total ({tok['input']} in / {tok['output']} out), {tok['mean']} per request",
            f"- Cost: ${cost['total_usd']:.6f} total, ${cost['mean_usd']:.6f} per request — {cost['basis']}",
            f"- Agent loop: {e['loop']['laps_total']} model planning laps, {e['loop']['forced_calls_total']} tool calls made by code "
            f"({'the fast-mode planner' if e.get('planner') == 'code' else 'the evidence guard'}) in {e['loop']['runs_with_forced_calls']} runs, "
            f"{e['loop']['budget_terminations']} budget terminations",
            "", "## Failed cases\n", "| case | ticket | mode | outcome / decision (expected) | assertions failed | wrong-version passage | trace |", "|---|---|---|---|---|---|---|"]
    for f in e["failed_cases"]:
        out.append(f"| {f['id']} | {f['ticket_id']} | {f['mode']} | {f['outcome']} / {f['decision']} ({f['expected']}) | {', '.join(f['assertions_failed']) or '—'} | {', '.join(f['wrong_version']) or '—'} | `{f['trace_id']}` |")
    out += ["", "## What this evaluation does not cover\n"] + [f"- {x}" for x in e["non_coverage"]]
    _write(REPORTS / "integrated_eval_report.md", "\n".join(out),
           "\n\n## Reading of the results\n\n_Pending: written after the run._\n\n")


def taxonomy_report():
    t = _json("error_taxonomy.json")
    if not t:
        print("  error_taxonomy.json missing; skipped"); return
    out = [f"# Error taxonomy, re-scored on the integrated system — Week 12\n",
           f"Generated {now()} from `week12/error_taxonomy.json` ({t['ran_at']}). Runs read: " +
           ", ".join(f"`{r['label']}` ({r['n']} requests)" for r in t["runs"]) + f". Requests: {t['n_runs']}; with a failed assertion or a wrong decision: {t['n_failed']}; with at least one mode: {t['n_with_any_mode']}.\n",
           f"Method: {t['method']}\n",
           "| # | failure mode | count | % of requests | severity | integration-created? | example trace |", "|---|---|---|---|---|---|---|"]
    for i, m in enumerate(t["modes"], 1):
        out.append(f"| {i} | **{m['name']}** — {m['description']} | {m['count']} | {m['frequency_pct']}% | {m['severity']} | {'yes' if m['new_in_integration'] else 'no (Week 5 family)'} | `{m['example_trace_id']}` ({m['example_case']}, run {m['example_run']}) |")
    out += ["", "## Every trace behind the counts\n"]
    for m in t["modes"]:
        out.append(f"- **{m['name']}**: " + ", ".join(f"`{c['trace_id']}` ({c['id']}: {c['decision']} vs {c['expected']}{'; ' + ', '.join(c['assertions_failed']) if c['assertions_failed'] else ''})" for c in m["cases"]))
    _write(REPORTS / "error_taxonomy.md", "\n".join(out), "\n\n## Reading the traces\n\n_Pending._\n\n")


def trajectory_report():
    t = _json("trajectory_eval.json")
    if not t:
        print("  trajectory_eval.json missing; skipped"); return
    s = t["summary"]
    out = [f"# Trajectory evaluation on the integrated agent — Week 12\n",
           f"Generated {now()} from `week12/trajectory_eval.json` (run `{s['run']}`, planner `{s['planner']}`, scored {s['scored_at']}). "
           f"Scorer: `experiments/week12_eval.py::score_trajectory`, the Week 8 method over the new tools.\n",
           "## Numbers\n",
           f"| cases | tool choices (model) | correct | **tool-choice accuracy** | correct ordering | path complete | outcome pass | trajectory pass | **gap (outcome − trajectory)** |",
           "|---|---|---|---|---|---|---|---|---|",
           f"| {s['n']} | {s['tool_choices']} | {s['correct_tool_choices']} | **{pct(s['tool_choice_accuracy_pct'])}** | {pct(s['correct_order_pct'])} | {pct(s['path_complete_pct'])} | {pct(s['outcome_pass_rate_pct'])} | {pct(s['trajectory_pass_rate_pct'])} | **{s['gap_pp']:+} pp** |",
           "", f"- Right answer on the wrong path: {s['right_answer_wrong_path']} · wrong answer on the right path: {s['wrong_answer_right_path']}",
           f"- Model-chosen tool steps: {s['model_chosen_steps']} · guard-forced steps: {s['forced_steps']}",
           "- Mode totals: " + ", ".join(f"`{k}` {v}" for k, v in s["mode_totals"].items()),
           "", "Definitions: " + "; ".join(f"**{k}** — {v}" for k, v in s["definitions"].items()),
           "", "## Per case\n", "| case | expected sequence(s) | actual (`*` = forced by the guard) | tool choice | order | outcome | trajectory | laps | trace |", "|---|---|---|---|---|---|---|---|---|"]
    for c in t["cases"]:
        exp = " or ".join(" → ".join(seq) for seq in c["expected_sequences"])
        out.append(f"| {c['id']} | {exp} | {' → '.join(c['actual_sequence']) or '—'} | {c['correct_tool_choices']}/{c['tool_choices']} | {'✓' if c['order_correct'] else '✗'} | {'✓' if c['outcome_correct'] else '✗'} | {'✓' if c['trajectory_pass'] else '✗'} | {c['laps']} | `{c['trace_id']}` |")
    _write(REPORTS / "trajectory_eval.md", "\n".join(out), "\n\n## Reading\n\n_Pending._\n\n")


def cost_report():
    c = _json("cost_and_scaling.json")
    if not c:
        print("  cost_and_scaling.json missing; skipped"); return
    m, a, cap, ld = c["measured"], c["assumptions"], c["capacity"], c["load"]
    out = [f"# Cost per deflected ticket and the 10× plan — Week 12\n",
           f"Generated {now()} from `week12/cost_and_scaling.json` (run `{c['run']}`, {c['n_requests']} requests, {c['deflected_clean']} clean deflections = {pct(c['clean_deflection_rate_pct'])}).\n",
           "## Measured\n",
           f"- Machine: {m['machine']}",
           f"- Latency per request: p50 {m['latency_ms']['p50']/1000:.1f} s · p95 {m['latency_ms']['p95']/1000:.1f} s · mean {m['latency_ms']['mean']/1000:.1f} s; generation p50 {(m['generation_latency_ms'].get('p50') or 0)/1000:.1f} s; MCP search p50 {(m['mcp_latency_ms'].get('p50') or 0)/1000:.1f} s",
           f"- Tokens: {m['tokens_in']} in / {m['tokens_out']} out, {m['tokens_per_request']} per request, {m['model_calls_per_request']} model calls per request\n",
           "## Cost per request and per deflected ticket\n",
           "| basis | per request | per deflected ticket |", "|---|---|---|",
           f"| local compute ({a['machine_usd_per_hour']} USD/h × measured latency) + embedding + infra share | ${c['cost_per_request_usd']['local_compute_basis']:.5f} | **${c['per_deflected_ticket_usd']:.5f}** |",
           f"| hosted-API stand-in rate card ({a['api_rate_card']['input_per_mtok']}/{a['api_rate_card']['output_per_mtok']} USD per Mtok) + embedding + infra share | ${c['cost_per_request_usd']['api_rate_card_basis']:.5f} | ${c['per_deflected_ticket_usd_api_basis']:.5f} |",
           f"| human Tier-1 handling ({a['tier1_handle_minutes']} min at {a['agent_hourly_loaded_cost_usd']} USD/h loaded) | — | ${c['human_cost_per_ticket_usd']:.2f} |",
           "", f"- Breakdown (local basis, per request): compute ${c['cost_per_request_usd']['breakdown_local']['compute']:.5f}, embedding ${c['cost_per_request_usd']['breakdown_local']['embedding']:.6f}, "
           f"fixed infra share ${c['cost_per_request_usd']['breakdown_local']['fixed_infra_share']:.5f} (Postgres {a['postgres_usd_per_month']} + MCP {a['mcp_server_usd_per_month']} + observability {a['observability_usd_per_month']} USD/month over 2,000 tickets), tools $0 ({c['cost_per_request_usd']['breakdown_local']['tools_note']})",
           f"- Local models have no API charge, but they are not free: the compute line is the measured wall time of each request at the machine rate. {a['machine_note']}",
           f"- Retrieval cost: embedding of the query and the cross-encoder run inside the request's wall time (already in the compute line); pgvector and BM25 are sub-10 ms.\n",
           "## Load: 2,000 tickets/month vs Black Friday 20,000 tickets/week\n",
           "| | current | Black Friday |", "|---|---|---|",
           f"| tickets | {ld['current']['tickets']}/month | {ld['black_friday']['tickets']}/week |",
           f"| requests/s (average) | {ld['current']['requests_per_second_avg']} | {ld['black_friday']['requests_per_second_avg']} |",
           f"| requests/s (peak ×{a['peak_factor']}) | {ld['current']['requests_per_second_peak']} | {ld['black_friday']['requests_per_second_peak']} |",
           f"| concurrent requests at mean latency (peak) | {ld['current']['concurrent_requests_peak']} | {ld['black_friday']['concurrent_requests_peak']} |",
           f"| concurrent requests at p95 latency (peak) | {ld['current']['concurrent_at_p95_peak']} | {ld['black_friday']['concurrent_at_p95_peak']} |",
           "", "## Capacity and the first failure point\n",
           f"- Model: {cap['model']}; mean latency {cap['mean_latency_s']} s, p95 {cap['p95_latency_s']} s → serial ceiling **{cap['serial_requests_per_hour']} requests/hour** ({cap['serial_requests_per_day']}/day)",
           f"- Workers needed to keep up at peak: current {cap['current_peak_needs_workers']}, Black Friday {cap['black_friday_peak_needs_workers']} → run **{cap['black_friday_workers_recommended']} workers** (×1.5 headroom)",
           f"- Retrieval: pgvector {cap['retrieval']['pgvector']}; BM25 {cap['retrieval']['bm25']}; reranker {cap['retrieval']['reranker']}",
           f"- MCP server: {cap['mcp_server']}", f"- Rate limits: {cap['rate_limits']}",
           f"- **First expected failure point: {cap['first_failure_point']}**\n",
           "## The 10× plan\n"] + [f"{i}. {step}" for i, step in enumerate(c["tenx_plan"], 1)]
    _write(REPORTS / "cost_and_scaling.md", "\n".join(out), "\n\n## Notes\n\n_Pending._\n\n")


def main():
    print("Writing reports from week12/*.json")
    integrated_report(); taxonomy_report(); trajectory_report(); cost_report()
    return 0


if __name__ == "__main__":
    sys.exit(main())
