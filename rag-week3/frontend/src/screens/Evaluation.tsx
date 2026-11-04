import { useEffect, useState } from 'react'
import { api, fmtMs, fmtPct, fmtUsd } from '../api'
import { Markdown } from '../markdown'
import { Badge, Bars, ErrorBox, JsonView, Spinner, StatTile, TraceLink } from '../ui'

export default function Evaluation() {
  const [d, setD] = useState<any>(null)
  const [error, setError] = useState<any>(null)
  useEffect(() => { api.evaluation().then(setD).catch(setError) }, [])
  if (error) return <ErrorBox error={error} />
  if (!d) return <Spinner label="Reading evaluation artefacts…" />
  const integ = d.integrated
  const traj = d.trajectory
  const tax = d.taxonomy
  const cost = d.cost
  const loop = d.closed_loop
  const jv = d.judge_validation
  return (
    <>
      <div className="page-head">
        <div><h1>Evaluation &amp; monitoring</h1><p>Every number is read from a result file in week12/ or week6/. A missing file shows as missing, never as a placeholder number.</p></div>
      </div>
      {d.missing?.length ? <div className="banner warn">Not run yet: {d.missing.join(', ')} — see README “Evaluation command”.</div> : null}
      <div className="tiles">
        <StatTile label="Assertion pass rate (integrated)" value={fmtPct(integ?.assertions?.pass_rate_pct)} sub={integ ? `${integ.assertions.passed}/${integ.n_cases} cases · ${integ.ran_at?.slice(0, 10)}` : 'not run'} />
        <StatTile label="Judge v2 agreement vs hand labels" value={fmtPct(jv.agreement_after_pct)} sub={`Week 6, n=25 blind labels · held-out ${fmtPct(jv.held_out?.pct)} · κ ${jv.kappa} · target ${jv.target_pct}%`} kind={jv.agreement_after_pct >= jv.target_pct ? 'ok' : 'warn'} />
        <StatTile label="Judge on integrated outputs" value={integ?.judge?.agreement_with_labels_pct != null ? fmtPct(integ.judge.agreement_with_labels_pct) : (integ?.judge ? 'pending labels' : 'not completed')} sub={integ?.judge?.note || integ?.judge_status || 'hand labels on integrated replies are pending'} note={integ?.judge_status} />
        <StatTile label="Superseded-policy citation rate" value={loop ? `${fmtPct(loop.before.superseded_citation_rate_pct)} → ${fmtPct(loop.after.superseded_citation_rate_pct)}` : '—'} sub={loop ? `before/after on ${loop.after.n} refund cases` : 'closed loop not recorded'} kind={loop && loop.after.superseded_citation_rate_pct === 0 ? 'ok' : undefined} />
        <StatTile label="Trajectory tool-choice accuracy" value={fmtPct(traj?.summary?.tool_choice_accuracy_pct)} sub={traj ? `${traj.summary.n} cases · ordering ${fmtPct(traj.summary.correct_order_pct)}` : 'not run'} />
        <StatTile label="Outcome − trajectory gap" value={traj ? `${traj.summary.gap_pp > 0 ? '+' : ''}${traj.summary.gap_pp} pp` : '—'} sub={traj ? `outcome ${fmtPct(traj.summary.outcome_pass_rate_pct)} · trajectory ${fmtPct(traj.summary.trajectory_pass_rate_pct)}` : ''} />
        <StatTile label="Cost per deflected ticket" value={cost ? fmtUsd(cost.per_deflected_ticket_usd, 4) : '—'} sub={cost ? `${cost.basis_short}` : ''} />
        <StatTile label="Latency (integrated)" value={fmtMs(integ?.latency?.p50_ms)} sub={integ ? `p95 ${fmtMs(integ.latency.p95_ms)} · mean ${fmtMs(integ.latency.mean_ms)}` : ''} />
      </div>
      <div className="grid-2">
        <div className="card">
          <h2>Pass rate by error-taxonomy mode (assertions)</h2>
          {integ?.by_mode ? <Bars rows={Object.entries(integ.by_mode).map(([m, v]: any) => ({ label: `${m} — ${v.label || ''}`, value: v.pass_rate_pct, detail: `${v.passed}/${v.n}` }))} max={100} /> : <div className="muted">not run</div>}
          {integ?.judge?.by_mode && <><div className="section-label">Judge v2 PASS rate by mode</div><Bars rows={Object.entries(integ.judge.by_mode).map(([m, v]: any) => ({ label: m, value: v.pass_rate_pct, detail: `${v.passed}/${v.n}` }))} max={100} /></>}
          <div className="footnote">Assertions ran before the judge; the two are reported separately.</div>
        </div>
        <div className="card">
          <h2>Re-scored error taxonomy</h2>
          {tax ? (
            <table className="table"><thead><tr><th>Mode</th><th>Freq.</th><th>Severity</th><th>Trace</th></tr></thead>
              <tbody>{tax.modes.map((m: any) => <tr key={m.name}><td><b>{m.name}</b>{m.new_in_integration && <Badge kind="violet">new</Badge>}<div className="small muted">{m.description}</div></td><td className="mono">{m.count} ({fmtPct(m.frequency_pct, 0)})</td><td>{m.severity}</td><td><TraceLink id={m.example_trace_id} /></td></tr>)}</tbody></table>
          ) : <div className="muted">not run</div>}
          {tax && <div className="footnote">{tax.n_runs} runs read · {tax.n_failed} with at least one failure · {tax.ran_at?.slice(0, 16)}</div>}
        </div>
      </div>
      <div className="grid-2">
        <div className="card">
          <h2>Known weaknesses</h2>
          <Markdown text={d.docs?.weaknesses_md} />
        </div>
        <div className="card">
          <h2>What the suite does not cover</h2>
          {integ?.non_coverage ? <ul className="small">{integ.non_coverage.map((x: string) => <li key={x}>{x}</li>)}</ul> : <div className="muted">listed in reports/integrated_eval_report.md once the suite has run</div>}
          {integ && <JsonView data={integ} label="integrated_eval.json" />}
          {traj && <JsonView data={traj.summary} label="trajectory summary" />}
          {cost && <JsonView data={cost} label="cost_and_scaling.json" />}
        </div>
      </div>
    </>
  )
}
