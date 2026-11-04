import { useEffect, useState } from 'react'
import { api, fmtMs, fmtPct } from '../api'
import { Link } from '../router'
import { Badge, Bars, ErrorBox, Spinner, StatTile, TraceLink, Ts } from '../ui'

export default function Dashboard() {
  const [data, setData] = useState<any>(null)
  const [error, setError] = useState<any>(null)
  const load = () => api.dashboard().then(setData).catch(setError)
  useEffect(() => { load() }, [])
  if (error) return <ErrorBox error={error} onRetry={load} />
  if (!data) return <Spinner label="Reading the inbox and the trace index…" />
  const t = data.totals
  const statusRows = Object.entries(data.by_status || {}).map(([k, v]) => ({ label: k, value: v as number }))
  return (
    <>
      <div className="page-head">
        <div><h1>Dashboard</h1><p>Live counts from the demo inbox, the escalation queue and every recorded AI run. Nothing here is typed in.</p></div>
        <button className="btn btn-ghost btn-sm" onClick={load}>Refresh</button>
      </div>
      <div className="tiles">
        <StatTile label="Total tickets" value={t.tickets} sub="demo inbox" />
        <StatTile label="Handled by AI" value={t.handled_by_ai} sub={`${t.ai_runs_recorded} runs recorded`} />
        <StatTile label="Deflected" value={t.deflected} sub="answered, not escalated" kind="ok" />
        <StatTile label="Escalated by AI" value={t.escalated_by_ai} sub={`${t.open_escalations} open in queue`} kind="warn" />
        <StatTile label="Deflection rate" value={fmtPct(t.deflection_rate_pct)} sub="deflected ÷ handled by AI" />
        <StatTile label="Simulated reopen rate" value={fmtPct(data.reopen?.simulated_reopen_rate_pct)} sub={data.reopen?.label} note="Simulated second-contact evaluation, not production data" />
        <StatTile label="Avg AI response time" value={fmtMs(data.latency.ai_mean_ms)} sub={`p50 ${fmtMs(data.latency.ai_p50_ms)} · p95 ${fmtMs(data.latency.ai_p95_ms)} · n=${data.latency.n}`} />
        <StatTile label="Human escalation response" value="n/a" sub={data.latency.human_escalation_first_response.note} />
      </div>
      <div className="grid-2">
        <div className="card">
          <h2>Tickets by status</h2>
          <Bars rows={statusRows} unit="" />
        </div>
        <div className="card">
          <h2>Where the numbers come from</h2>
          <ul className="small">
            {Object.entries(data.data_sources).map(([k, v]) => <li key={k}><b>{k}</b>: {v as string}</li>)}
          </ul>
          <div className="footnote">Generated {data.generated_at} · demo data frozen to {data.as_of}</div>
        </div>
      </div>
      <div className="card">
        <h2>Recent AI activity</h2>
        {data.recent.length === 0 ? <div className="muted">No AI runs yet. Open a ticket in the inbox and run the analysis, or ask the chatbot.</div> : (
          <table className="table">
            <thead><tr><th>When</th><th>Ticket</th><th>Outcome</th><th>Policy check</th><th>Latency</th><th>Input</th><th>Trace</th></tr></thead>
            <tbody>
              {data.recent.map((r: any) => (
                <tr key={r.trace_id}>
                  <td><Ts ts={r.ts} /></td>
                  <td>{r.ticket_id ? <Link to={`inbox/${r.ticket_id}`} className="mono">{r.ticket_id}</Link> : <span className="muted">chat</span>}</td>
                  <td><Badge kind={r.refused ? 'warn' : 'ok'}>{r.refused ? 'escalated' : 'answered'}</Badge></td>
                  <td><Badge kind={r.policy_verdict === 'contradicts' ? 'danger' : r.policy_verdict === 'consistent' ? 'ok' : 'neutral'}>{r.policy_verdict || '—'}</Badge></td>
                  <td className="mono">{fmtMs(r.latency_ms)}</td>
                  <td className="small">{(r.input || '').slice(0, 80)}</td>
                  <td><TraceLink id={r.trace_id} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  )
}
