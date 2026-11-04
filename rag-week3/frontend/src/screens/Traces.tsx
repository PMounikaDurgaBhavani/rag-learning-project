import { useEffect, useState } from 'react'
import { api, fmtMs, fmtUsd } from '../api'
import { Link, navigate } from '../router'
import { Badge, ErrorBox, JsonView, KV, Spinner, Ts } from '../ui'

export default function Traces({ traceId }: { traceId?: string }) {
  const [rows, setRows] = useState<any[]>([])
  const [error, setError] = useState<any>(null)
  const load = () => api.traces(80).then((d) => setRows(d.rows)).catch(setError)
  useEffect(() => { load() }, [])
  return (
    <>
      <div className="page-head">
        <div><h1>Agent execution traces</h1><p>One trace id per request, kept across backend → agent → MCP client → MCP server → reply. Spans carry durations, tokens, cost and errors.</p></div>
        <button className="btn btn-ghost btn-sm" onClick={load}>Refresh</button>
      </div>
      <ErrorBox error={error} onRetry={load} />
      <div className="traces">
        <div className="card" style={{ padding: 0, maxHeight: 'calc(100vh - 170px)', overflow: 'auto' }}>
          <table className="table">
            <thead><tr><th>When</th><th>Ticket</th><th>Status</th><th>Latency</th><th>Cost</th></tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.trace_id} className={`clickable ${r.trace_id === traceId ? 'selected' : ''}`} onClick={() => navigate(`traces/${r.trace_id}`)}>
                  <td className="small"><Ts ts={r.ts} /><div className="mono muted">{r.trace_id}</div></td>
                  <td className="mono small">{r.ticket_id || '—'}</td>
                  <td><Badge kind={r.status === 'error' ? 'danger' : r.refused ? 'warn' : 'ok'}>{r.refused ? 'escalated' : r.status}</Badge></td>
                  <td className="mono small">{fmtMs(r.latency_ms)}</td>
                  <td className="mono small">{fmtUsd(r.cost_usd)}</td>
                </tr>
              ))}
              {rows.length === 0 && <tr><td colSpan={5} className="muted">No deflection traces yet.</td></tr>}
            </tbody>
          </table>
        </div>
        <div>{traceId ? <TraceDetail traceId={traceId} /> : <div className="card muted">Select a trace, or paste its id from a ticket or chat turn.</div>}</div>
      </div>
    </>
  )
}

function TraceDetail({ traceId }: { traceId: string }) {
  const [d, setD] = useState<any>(null)
  const [error, setError] = useState<any>(null)
  useEffect(() => { setD(null); setError(null); api.trace(traceId).then(setD).catch(setError) }, [traceId])
  if (error) return <ErrorBox error={error} />
  if (!d) return <Spinner label="Loading trace…" />
  const trace = d.trace || {}
  const cap = d.capstone || {}
  const spans: any[] = d.spans || []
  const starts = spans.map((s) => Date.parse(s.start_ts || s.start)).filter((x) => !isNaN(x))
  const t0 = Math.min(...starts)
  const total = Math.max(1, ...spans.map((s) => (Date.parse(s.start_ts || s.start) - t0) + (s.duration_ms || 0)))
  const depth = (s: any): number => { let n = 0; let p = s.parent_id; const seen = new Set(); while (p && !seen.has(p)) { seen.add(p); const parent = spans.find((x) => x.span_id === p); if (!parent) break; n++; p = parent.parent_id } return n }
  return (
    <>
      <div className="card">
        <div className="page-head" style={{ marginBottom: 6 }}>
          <div><h2 style={{ fontSize: 16 }}>Trace <span className="mono">{trace.trace_id}</span></h2><div className="muted small">request <span className="mono">{trace.request_id || '—'}</span> · {trace.kind} · <Ts ts={trace.ts} /> · build {trace.app_version} · prompt {trace.prompt?.version}</div></div>
          <div className="toolbar">
            <Badge kind={trace.status === 'error' ? 'danger' : cap.outcome === 'escalated' ? 'warn' : 'ok'}>{cap.outcome || trace.status}</Badge>
            {cap.decision && <Badge kind="neutral">{cap.decision}</Badge>}
            {cap.policy_version && <Badge kind="info">{cap.policy_version}</Badge>}
            {trace.policy_check?.verdict && <Badge kind={trace.policy_check.verdict === 'contradicts' ? 'danger' : 'neutral'}>policy check: {trace.policy_check.verdict}</Badge>}
          </div>
        </div>
        <div className="grid-3">
          <KV rows={[['Ticket', <Link to={`inbox/${trace.ticket?.ticket_id}`} className="mono">{trace.ticket?.ticket_id}</Link>], ['Intent', cap.intent], ['Planner', cap.planner], ['Loop end', cap.loop_end], ['Terminated by', cap.terminated_by || 'none']]} />
          <KV rows={[['Total latency', fmtMs(trace.latency_ms?.total)], ['Model calls', trace.usage?.model_calls], ['Tokens in / out', `${trace.usage?.input_tokens ?? '—'} / ${trace.usage?.output_tokens ?? '—'}`], ['Total cost', <span>{fmtUsd(trace.cost?.total_usd)} <span className="muted small">{trace.cost?.complete ? 'estimate, stand-in rate card' : 'not computable'}</span></span>], ['Retries', trace.retries ?? '—']]} />
          <KV rows={[['MCP server', cap.mcp?.server || '—'], ['MCP transport', cap.mcp?.transport || '—'], ['MCP calls / latency', `${cap.mcp?.calls ?? 0} / ${fmtMs(cap.mcp?.latency_ms)}`], ['Trace id echoed by server', cap.mcp?.trace_id_echoed ? <Badge kind={cap.mcp.trace_id_echoed === trace.trace_id ? 'ok' : 'danger'}>{cap.mcp.trace_id_echoed === trace.trace_id ? 'identical' : cap.mcp.trace_id_echoed}</Badge> : '—'], ['Injection', cap.injection_detected ? <Badge kind="danger">detected</Badge> : 'no']]} />
        </div>
        {d.missing_fields?.length ? <div className="footnote">Not recorded: {d.missing_fields.join('; ')}</div> : null}
      </div>

      <div className="card">
        <h2>Spans ({spans.length})</h2>
        {spans.map((s) => {
          const start = Date.parse(s.start_ts || s.start) - t0
          const left = (start / total) * 100
          const width = Math.max(0.6, ((s.duration_ms || 0) / total) * 100)
          const a = s.attrs || {}
          return (
            <div className="span-row" key={s.span_id}>
              <div style={{ paddingLeft: depth(s) * 14 }}>
                <div className="span-name"><span className="mono">{s.name}</span><Badge kind={s.status === 'ok' ? 'ok' : 'danger'}>{s.status}</Badge>{a.forced_by && <Badge kind="warn">{String(a.forced_by)}</Badge>}</div>
                <div className="span-attrs mono">span {s.span_id}{s.parent_id ? ` ← ${s.parent_id}` : ''}{a.server ? ` · ${a.server}` : ''}{a.input_tokens != null ? ` · ${a.input_tokens}→${a.output_tokens} tok` : ''}{a.cost_usd != null ? ` · ${fmtUsd(a.cost_usd)}` : ''}{a.results != null ? ` · ${a.results} results` : ''}{a.policy_version ? ` · policy ${a.policy_version}` : ''}{a.trace_id_echoed ? ` · echoed ${a.trace_id_echoed}` : ''}{a.hard_failures ? ` · hard failures ${JSON.stringify(a.hard_failures)}` : ''}{s.error ? ` · ERROR ${s.error}` : ''}</div>
              </div>
              <div className="mono small">{fmtMs(s.duration_ms)}</div>
              <div className="span-bar"><i className={s.status === 'ok' ? '' : 'err'} style={{ left: `${left}%`, width: `${width}%` }} /></div>
            </div>
          )
        })}
      </div>

      <div className="card">
        <h2>MCP server-side log for this trace id</h2>
        {d.mcp_server_log?.lines?.length ? (
          <table className="table"><thead><tr><th>At</th><th>Tool</th><th>Arguments (query length only)</th><th>Latency</th><th>Results</th><th>pid</th></tr></thead>
            <tbody>{d.mcp_server_log.lines.map((l: any, i: number) => <tr key={i}><td className="small">{l.at}</td><td className="mono">{l.tool}</td><td className="mono small">{JSON.stringify(l.arguments)}</td><td className="mono">{fmtMs(l.latency_ms)}</td><td>{l.results}</td><td className="mono">{l.pid}</td></tr>)}</tbody></table>
        ) : <div className="muted small">No MCP call for this request (direct escalation, or the server was unavailable).</div>}
        <div className="footnote">{d.mcp_server_log?.note} — file {d.mcp_server_log?.file}</div>
      </div>

      <div className="card">
        <h2>Outcome</h2>
        <div className="reply-box">{trace.outcome?.answer}</div>
        {cap.citations?.cited?.length ? <div className="small" style={{ marginTop: 8 }}>Cited: {cap.citations.cited.map((c: any) => <span key={c.marker}>[{c.marker}] <Link to={`help-centre/${c.slug}`}>{c.slug}</Link> ({c.policy_version || c.article_id}) </span>)}</div> : null}
        {cap.draft_rejected && <div className="banner warn" style={{ marginTop: 8 }}>Draft {cap.draft_rejected.attempt} rejected by the output guardrail: {cap.draft_rejected.hard_failures.join(', ')}<div className="reply-box small" style={{ marginTop: 6 }}>{cap.draft_rejected.reply}</div></div>}
        {trace.evaluation && <div className="small" style={{ marginTop: 8 }}>Assertions: {trace.evaluation.passed}/{trace.evaluation.applicable} passed{trace.evaluation.failed_names?.length ? ` · failed: ${trace.evaluation.failed_names.join(', ')}` : ''}</div>}
        <JsonView data={trace} label="Full trace record (PII masked at write time)" />
      </div>
    </>
  )
}
