"""Write the Week 11 artefacts from the live index and the saved evidence.

    python experiments/week11_report.py            # drill.md, cost_by_stage.md, tenx.md, trace.json, drill_results.json

Everything numeric is measured at run time (the drill is executed and timed
here; cost and capacity are read from the index). The narrative sections are
text in this file so the artefacts can always be regenerated.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
W11 = ROOT / "week11"
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "experiments"))

import observability as obs  # noqa: E402
import week11_ui as w11  # noqa: E402


def fmt_usd(v):
    return "n/a" if v is None else f"${v:.6f}"


def fmt_ms(v):
    return "n/a" if v is None else (f"{v / 1000:.2f} s" if v >= 1000 else f"{v:.1f} ms")


def write(name, text):
    (W11 / name).write_text(text, encoding="utf-8")
    print(f"  wrote week11/{name}")


def drill_md(d1, d2, incidents, as_of):
    def match_block(m):
        t = m["ticket"] or {}
        lines = [
            f"- **{'Controlled incident' if m['fixture'] else 'Recorded in normal use'}** — trace `{m['trace_id']}`, "
            f"request `{m['request_id'] or 'not recorded (pre-Week 11 record)'}`, {m['ts_local']} local ({m['ts']})",
            f"  - path `{m['kind']}` · source `{m['source']}` · prompt `{m['prompt_version']}` · model `{m['model']}` · build `{m['app_version']}`",
            f"  - ticket {t.get('ticket_id')} · ${t.get('charge_amount')} charged {t.get('days_since_charge')} days ago"
            f"{' · duplicate' if t.get('duplicate_charge') else ''} · {t.get('tier')}",
            f"  - policy check: **{m['policy_check']['verdict']}** — {m['policy_check']['reason']}",
            f"  - retrieved context ids: {', '.join('`' + c + '`' for c in m['context_ids']) or 'none'}",
            f"  - discoverable through: {', '.join('`' + f + '`' for f in m['discovered_by'])}",
            "  - the incorrect generated answer:",
            "",
            "    ```",
        ] + ["    " + line for line in (m["answer"] or "").splitlines()] + ["    ```", ""]
        return "\n".join(lines)

    def slices(d):
        rows = ["| # | slice | filter | hits | time |", "|---|---|---|---|---|"]
        for i, s in enumerate(d["slices"], 1):
            rows.append(f"| {i} | {s['slice']} | `{s['filter']}` | {s['count']} | {s['ms']} ms{(' · ' + s['note']) if s.get('note') else ''} |")
        return "\n".join(rows)

    w = d1["window"]
    inc1 = incidents.get("refund-window-inverted") or {}
    inc2 = incidents.get("duplicate-charge-denied") or {}
    plan = next((h.get("deployment_plan") for h in json.loads((W11 / "prompt_history.json").read_text()) if h.get("deployment_plan")), {})
    return f"""# Week 11 — Support drill: "A customer said it gave a wrong answer about refunds yesterday afternoon."

Generated {datetime.now(timezone.utc).isoformat(timespec='seconds')} by `python experiments/week11_report.py`.
Every time below is a wall-clock measurement of the search procedure on this machine, taken when this file was
written. Re-run the script (or press **Find incident** in *Observability → Support drill*) to measure again.

## 1. Time to find

| | procedure 1 — slices | procedure 2 — indexed field (bonus) |
|---|---|---|
| time to find (server, mm:ss) | **{d1['time_to_find']}** ({d1['time_to_find_ms']} ms) | **{d2['time_to_find']}** ({d2['time_to_find_ms']} ms) |
| started → completed (UTC) | {d1['started_at']} → {d1['completed_at']} | {d2['started_at']} → {d2['completed_at']} |
| answers read and classified | {d1['candidates_classified']} | {d2['candidates_classified']} (verdict read from the column) |
| matches | {d1['match_count']} | {d2['match_count']} |
| window actually used | {d1['window_used']} | {d2['window_used']} |

Both are measured; neither is a "five-minute pass" claim. On a log of {obs.stats()['requests']} requests the whole
procedure takes milliseconds either way; what the second procedure removes is the per-candidate read-and-classify
step, which is the part that grows with traffic.

## 2. Search slice

- **Complaint handled on (as-of):** {w['as_of_local'][:10]} → "yesterday afternoon" = **{w['local_start']} to {w['local_end']}** ({w['tz']}),
  i.e. {w['utc_start']} to {w['utc_end']} UTC. The as-of date is an input to the procedure, so the window is reproducible.
- **Time:** the window above; if it holds no request the procedure widens to the 7 days before as-of, then to all recorded time, and says so.
- **Topic:** "refund" in the **input OR the generated output** (FTS5 over both columns). The complaint describes the answer, not the question.
- **User:** not available — the log has no customer field (listed under missing fields).
- **Prompt version / input type:** carried on every record and shown on each match; not needed to narrow this search.
- **Cost outlier:** available as a filter in the explorer; not needed here.
- **Wrongness:** for every candidate with charge facts, the answer is checked against HC-008 by code
  (`observability.policy_verdict`, the same classifier as the Week 6 assertions `no_refund_outside_window`
  and the new `no_denial_inside_window`): eligible → must not deny; ineligible → must not promise.

### Procedure 1 — slices, in order

{slices(d1)}

### Procedure 2 — indexed `policy_verdict` (the field added after drill 1)

{slices(d2)}

## 3. Incident source and findings

{(chr(10)).join(match_block(m) for m in d1['matches']) if d1['matches'] else '_No match in the searched windows._'}

**Real vs controlled.** {d1['real_vs_fixture']['real']} of the matches above were recorded in normal use (Week 6 drafts,
Week 10 benchmark runs and the Week 10 UI chat) and {d1['real_vs_fixture']['fixture']} are controlled test incidents
planted for this drill. The controlled incidents are real requests through the real drafter under the prompt version
active when they were planted; they carry `fixture={{...}}` on the record and are labelled in every view.

- Controlled incident 1 (`refund-window-inverted`): trace `{inc1.get('trace_id', 'not planted')}`, planted {inc1.get('planted_at', '—')}
  under `{inc1.get('prompt_version_at_planting', '—')}`; policy check **{(inc1.get('policy_check') or {}).get('verdict', '—')}**.
  Ticket: {inc1.get('ticket', {}).get('message', '')!r}
- Controlled incident 2 (`duplicate-charge-denied`, bonus): trace `{inc2.get('trace_id', 'not planted')}`, planted {inc2.get('planted_at', '—')}
  under `{inc2.get('prompt_version_at_planting', '—')}`; policy check **{(inc2.get('policy_check') or {}).get('verdict', '—')}**.

**Reference policy facts (HC-008, `samples/article_08_refunds_escalation.md`, mirrored in `src/ticket_policy.py`):**

{(chr(10)).join('- ' + r for r in w11.REFERENCE_POLICY)}

**The defect.** Under prompt `ticket-v1.2` the 1.5B drafter tells charges that are *inside* the 30-day window that
they fall *outside* it (12, 5 and 29 days in the frozen Week 6 replies T03, T13, T21; 14 days in controlled incident 1),
and refuses a duplicate charge because of its age (controlled incident 2). The six Week 6 assertions did not catch it:
they check that an amount is stated and that no refund is *promised* outside the window, never that one is *refused*
inside it. The hand labels (week6/labels_25.json) had marked those three replies FAIL on resolution, so the judge saw
it and the deterministic layer did not.

**The fix (`ticket-v1.3`, src/ticket_reply.py).** Stating the HC-008 rule in the system prompt was probed first and was
*not* enough: the model still wrote "14 days ago, which falls outside the 30-day window". The one comparison it kept
inverting (days since the charge against 30, or the duplicate flag) is therefore made in code by `refund_check_line()`
and appended to the user turn as a single line, e.g. *"Refund check: the charge is 14 days old, which is within the
30-day window, so it IS eligible for a full refund of $120.00."* The system prompt states the rule and tells the model
to follow that line. `ticket_block()` is unchanged, so the labeller and the judge still see the ticket exactly as before.
RED (v1.2) and GREEN (v1.3) full-suite runs, every reply drafted fresh, are under `week11/test_results/`; both use the
seven deterministic assertions (`--judge none`: on this 8 GB machine judge v2 scored 2 of 26 in 35 minutes after the
drafter and was aborted — `test_results/aborted_RED_with_judge_v2.txt`).

## 4. Which log fields made the incident discoverable

- `ts` (indexed) — the time slice.
- `outcome.answer` — full generated text, FTS-indexed: the complaint names the answer's topic, not the question.
- `ticket.charge_amount`, `ticket.days_since_charge`, `ticket.duplicate_charge`, `ticket.refund_requested` — the facts the policy check needs.
- `prompt.version` — to tell which prompt produced it, and to separate before/after the fix.
- `policy_verdict` (new in Week 11, indexed) — procedure 2 reads this directly.

## 5. Missing fields and limitations

{(chr(10)).join('- ' + m for m in d1['missing_fields'])}
{(chr(10)).join('- ' + m for m in d1['limitations'])}

## 6. Bonus — the gap closed, and the second drill

Drill 1 showed the wrongness check was a scan: every topical answer had to be read and classified. Week 11 therefore
writes a **`policy_verdict`** column at index time (decided by code from the ticket facts and the answer) with an index
on it, and backfills it for every historical record. Drill 2 ran procedure 2 on the same complaint through the same
panel, with a **second, distinct** controlled incident (a duplicate charge at 40 days, trace `{inc2.get('trace_id', 'not planted')}`)
in the log: it was {'among' if any(m['trace_id'] == inc2.get('trace_id') for m in d2['matches']) else 'NOT among'} drill 2's matches
(procedure 1 {'also found it' if any(m['trace_id'] == inc2.get('trace_id') for m in d1['matches']) else 'did not find it'}).
The two measured times are in §1; the field responsible is `policy_verdict`.

## 7. Canary / rollback (two lines)

1. **Canary:** {plan.get('canary', '')}
2. **Rollback:** {plan.get('rollback', '')}

{plan.get('status', '')}
"""


def cost_md(c):
    st = c["stages"]
    w = c["window"]
    pq = c["per_query"]

    def stat(o, f):
        return "no data" if not o.get("n") else f"mean {f(o['mean'])} · p50 {f(o['p50'])} · p95 {f(o['p95'])} · max {f(o['max'])} (n={o['n']})"

    def group(title, g):
        rows = [f"### {title}", "", "| | requests | with usage | cost mean | cost total | tokens in / out | latency p50 | latency p95 |",
                "|---|---|---|---|---|---|---|---|"]
        for k, v in g.items():
            rows.append(f"| `{k}` | {v['requests']} | {v['with_cost']} | {fmt_usd(v['cost_usd'].get('mean'))} | {fmt_usd(v['cost_usd'].get('sum'))} | "
                        f"{v['input_tokens']} / {v['output_tokens']} | {fmt_ms(v['latency_ms'].get('p50'))} | {fmt_ms(v['latency_ms'].get('p95'))} |")
        return "\n".join(rows) + "\n"

    return f"""# Week 11 — Cost by stage

Generated {datetime.now(timezone.utc).isoformat(timespec='seconds')} by `python experiments/week11_report.py` from the
Week 11 index over `traces/traces.jsonl`. The same numbers are live in *Observability → Cost & usage*.

**Measurement window:** {w['from']} → {w['to']} · {w['requests']} requests · {w['requests_with_complete_cost']} with complete token usage ·
{w['requests_without_usage']} without usage (counted, not priced) · controlled incidents {'included' if w['fixtures_included'] else 'excluded'}.

## What is measured and what is estimated

- **Tokens** are measured by the application with the model's own tokenizer (input on the rendered chat template, output
  re-tokenised from the reply). The models run in-process; there is no provider usage field and no provider bill.
- **Cost** is an **estimate**: tokens × the stand-in rate card the repo has used since Week 7
  (`agent_runtime.RATE_CARD`: ${c['pricing']['rate_card']['input_per_mtok']} per M input tokens, ${c['pricing']['rate_card']['output_per_mtok']} per M output tokens, USD).
- **Retrieval** (embedding, pgvector, BM25, cross-encoder) is **not separately metered**; it is local compute measured as time only, and enters the total as $0.
- **Validation** (distance gate, grounding, assertions) and **tools** (policy lookup, MCP tools) are code with no model call: $0.
- Records written before Week 11 carry an output token count but no input count; their cost is reported as *not computable*, never guessed.

## Cost per query

- cost: {stat(pq['cost_usd'], fmt_usd)}
- input tokens: {stat(pq['input_tokens'], lambda v: f"{v:.0f}")}
- output tokens: {stat(pq['output_tokens'], lambda v: f"{v:.0f}")}
- latency (all requests, end to end): {stat(pq['latency_ms'], fmt_ms)}
- retries recorded: {c['retries']['total']} ({c['retries']['note']})

## Stage breakdown

| stage | cost (USD) | metered? | basis | time from spans |
|---|---|---|---|---|
| retrieval | $0 (unmetered) | no | {st['retrieval']['basis']} | {fmt_ms(st['retrieval']['time_ms_total'])} over {st['retrieval'].get('spans', 0)} spans |
| generation | {fmt_usd(st['generation']['cost_usd'])} | estimate | {st['generation']['basis']} | {fmt_ms(st['generation']['time_ms_total'])} |
| validation | $0 | code only | {st['validation']['basis']} | {fmt_ms(st['validation']['time_ms_total'])} |
| tools | $0 | no charge | {st['tools']['basis']} | {fmt_ms(st['tools']['time_ms_total'])} |
| **total attributable** | **{fmt_usd(st['total_attributable_usd'])}** | | {st['formula']} | |

Generation tokens in the window: {st['generation']['input_tokens']} in / {st['generation']['output_tokens']} out.
Stage times come from the {c['spans_coverage']['requests_with_spans']} requests recorded with Week 11 spans; the other
{c['spans_coverage']['requests_without_spans']} carry end-to-end latency only.

**How the total is calculated:** total = Σ input_tokens × 0.15 / 1e6 + Σ output_tokens × 0.60 / 1e6, over requests with
both counts; retrieval, validation and tools add 0 because nothing meters them.

{group('By model / provider', c['by_model'])}
{group('By request type', c['by_kind'])}
{group('By prompt version', c['by_prompt_version'])}
Cost outlier threshold (p95 of complete costs): {fmt_usd(c['pricing']['outlier_threshold_usd'])}.
"""


def tenx_md(t):
    cur, p, cap, lat = t["current"], t["projected_10x"], t["capacity"], t["current"]["latency_ms"]
    return f"""# Week 11 — 10× capacity assessment

Generated {datetime.now(timezone.utc).isoformat(timespec='seconds')} by `python experiments/week11_report.py`; live in *Observability → 10× capacity*.

**Observed window:** last {t['window']['days']} days → {t['window']['first_request']} to {t['window']['last_request']}
({t['window']['span_days']} days spanned) · {t['window']['requests']} requests · {t['window']['model_backed_answered']} model-backed answered requests · controlled incidents excluded.

| | current | projected 10× |
|---|---|---|
| requests per day | {cur['requests_per_day']} | {p['requests_per_day']} |
| peak recorded hour ({cur['peak_hour_utc']}Z) | {cur['peak_hour_requests']} requests | {p['peak_hour_requests']} requests |
| tokens (in + out) | {cur['tokens_total']} | {p['tokens_total']} |
| cost over the window (estimate, stand-in rate card) | {fmt_usd(cur['cost_usd'])} | {fmt_usd(p['cost_usd'])} |

**Latency, answered model-backed requests (n={lat.get('n', 0)}):** p50 {fmt_ms(lat.get('p50'))} · p95 {fmt_ms(lat.get('p95'))} · p99 {fmt_ms(lat.get('p99'))} · mean {fmt_ms(lat.get('mean'))}.
{('> Conditional: ' + t['conditional_note']) if t['conditional'] else 'Sample size supports the percentiles (n ≥ 20).'}

**Capacity model:** {cap['model']}. Mean latency {cap['mean_latency_s']} s → ceiling {cap['requests_per_hour_ceiling']} requests/hour,
{cap['requests_per_day_ceiling']} requests/day. Utilisation now {cap['utilisation_now']} → at 10× {cap['utilisation_10x']}.
Provider request/token limits: {cap['provider_limits']}.

## Which breaks first

**{t['bottleneck']}**

{t['conclusion']}

Evidence:
{(chr(10)).join('- ' + e for e in t['evidence'])}

Assumptions:
{(chr(10)).join('- ' + a for a in t['assumptions'])}

**Mitigation:** {t['mitigation']}

**One line:** {t['conclusion']}
"""


def main():
    W11.mkdir(exist_ok=True)
    as_of = datetime.now().date().isoformat()
    incidents = w11.incidents()
    print("Running the drill (both procedures, measured)…")
    d1 = w11.drill(w11.DEFAULT_COMPLAINT, as_of=as_of, procedure="slices")
    d2 = w11.drill(w11.DEFAULT_COMPLAINT, as_of=as_of, procedure="indexed")
    (W11 / "drill_results.json").write_text(json.dumps({"as_of": as_of, "slices": d1, "indexed": d2}, indent=2, default=str) + "\n", encoding="utf-8")
    print("  wrote week11/drill_results.json")
    write("drill.md", drill_md(d1, d2, incidents, as_of))
    write("cost_by_stage.md", cost_md(w11.cost_summary(days=None)))
    write("tenx.md", tenx_md(w11.tenx(days=90)))

    # trace.json: the controlled incident's complete record with spans, plus
    # the newest live request through the chat route for comparison.
    records = {}
    inc = incidents.get("refund-window-inverted")
    if inc and inc.get("trace_id"):
        records["incident_refund_window_inverted"] = obs.get(inc["trace_id"])
    inc2 = incidents.get("duplicate-charge-denied")
    if inc2 and inc2.get("trace_id"):
        records["incident_duplicate_charge_denied"] = obs.get(inc2["trace_id"])
    latest = obs.search(kind="answer", source="ui", limit=1)["rows"]
    if latest:
        records["latest_chat_answer"] = obs.get(latest[0]["trace_id"])
    (W11 / "trace.json").write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "note": "Complete records from the Week 11 index: the trace as written to traces/traces.jsonl, its spans, "
                "usage, cost and the fields it never recorded. Nothing is reconstructed.",
        "records": records}, indent=2, default=str) + "\n", encoding="utf-8")
    print("  wrote week11/trace.json")


if __name__ == "__main__":
    main()
