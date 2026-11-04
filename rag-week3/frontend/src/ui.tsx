import { useEffect, useState } from 'react'
import { Link, hrefToRoute } from './router'
import { fmtMs, fmtTs, fmtUsd } from './api'

export function Badge({ kind = 'neutral', children, title }: { kind?: 'ok' | 'warn' | 'danger' | 'info' | 'neutral' | 'violet'; children: React.ReactNode; title?: string }) {
  return <span className={`badge badge-${kind}`} title={title}>{children}</span>
}

export function outcomeKind(outcome?: string | null, status?: string | null): 'ok' | 'warn' | 'danger' | 'info' | 'neutral' {
  if (outcome === 'answered') return 'ok'
  if (outcome === 'escalated') return 'warn'
  if (status === 'error') return 'danger'
  return 'neutral'
}

export function StatTile({ label, value, sub, kind, note }: { label: string; value: React.ReactNode; sub?: React.ReactNode; kind?: string; note?: string }) {
  return (
    <div className={`tile ${kind ? 'tile-' + kind : ''}`} title={note}>
      <div className="tile-label">{label}</div>
      <div className="tile-value">{value}</div>
      {sub && <div className="tile-sub">{sub}</div>}
    </div>
  )
}

export function Bars({ rows, max, unit = '%' }: { rows: { label: string; value: number | null; detail?: string }[]; max?: number; unit?: string }) {
  const top = max ?? Math.max(1, ...rows.map((r) => r.value ?? 0))
  return (
    <div className="bars" role="table">
      {rows.map((r) => (
        <div className="bar-row" key={r.label} role="row" title={r.detail}>
          <div className="bar-label">{r.label}</div>
          <div className="bar-track"><div className="bar-fill" style={{ width: `${Math.max(0, Math.min(100, ((r.value ?? 0) / top) * 100))}%` }} /></div>
          <div className="bar-value mono">{r.value == null ? '—' : `${r.value}${unit}`}</div>
        </div>
      ))}
    </div>
  )
}

export function Spinner({ label }: { label?: string }) {
  return <div className="spinner-row"><span className="spinner" /> {label || 'Loading…'}</div>
}

export function Elapsed({ since }: { since: number }) {
  const [now, setNow] = useState(Date.now())
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 500); return () => clearInterval(t) }, [])
  return <span className="mono">{((now - since) / 1000).toFixed(0)} s</span>
}

export function ErrorBox({ error, onRetry }: { error: any; onRetry?: () => void }) {
  if (!error) return null
  const status = error.status
  return (
    <div className="error-box">
      <b>{status === 409 ? 'Server busy' : status === 503 ? 'Degraded' : 'Error'}</b> — {String(error.message || error)}
      {status === 409 && error.body?.running && <div className="muted">Running: {error.body.running} (started {error.body.started})</div>}
      {onRetry && <button className="btn btn-sm" onClick={onRetry}>Retry</button>}
    </div>
  )
}

export function Citations({ items, title = 'Sources' }: { items: any[]; title?: string }) {
  if (!items || !items.length) return null
  return (
    <div className="citations">
      <div className="section-label">{title}</div>
      {items.map((c) => (
        <div className="citation" key={`${c.marker}-${c.chunk_id}`}>
          <span className="cite-marker mono">[{c.marker}]</span>
          <div>
            <Link to={hrefToRoute(c.url)} className="cite-link">{c.title || c.slug}</Link>
            <span className="muted"> · {c.slug}</span>
            <div className="cite-meta">
              <span className="mono">{c.article_id}</span>
              {c.section && <span>§ {c.section}</span>}
              {c.last_updated && <span>updated {c.last_updated}</span>}
              {c.policy_version && <Badge kind={c.status === 'superseded' ? 'danger' : 'info'}>{c.policy_label || c.policy_version}{c.status === 'superseded' ? ' · RETIRED' : ''}</Badge>}
              {c.doc_type && c.doc_type !== 'article' && <Badge kind="neutral">{c.doc_type}</Badge>}
            </div>
          </div>
        </div>
      ))}
    </div>
  )
}

export function KV({ rows }: { rows: [string, React.ReactNode][] }) {
  return (
    <dl className="kv">
      {rows.map(([k, v]) => (<div key={k}><dt>{k}</dt><dd>{v ?? '—'}</dd></div>))}
    </dl>
  )
}

export function JsonView({ data, open = false, label = 'Raw JSON' }: { data: any; open?: boolean; label?: string }) {
  return (
    <details className="json" open={open}>
      <summary>{label}</summary>
      <pre className="mono">{JSON.stringify(data, null, 2)}</pre>
    </details>
  )
}

export function ToolTable({ steps }: { steps: any[] }) {
  if (!steps?.length) return <div className="muted">No tool execution recorded.</div>
  return (
    <table className="table">
      <thead><tr><th>#</th><th>Step</th><th>Kind</th><th>Status</th><th>Duration</th><th>Chosen by</th><th>Detail</th></tr></thead>
      <tbody>
        {steps.map((s) => (
          <tr key={s.step}>
            <td className="mono">{s.step}</td>
            <td className="mono">{s.name}{s.server ? <div className="muted small">{s.server}</div> : null}</td>
            <td>{s.kind}</td>
            <td><Badge kind={s.status === 'ok' ? 'ok' : 'danger'}>{s.status}</Badge></td>
            <td className="mono">{fmtMs(s.duration_ms)}</td>
            <td>{s.kind === 'tool' || s.kind === 'mcp' ? (s.forced_by ? <Badge kind="warn" title={s.forced_by}>guard/plan</Badge> : <Badge kind="info">model</Badge>) : <span className="muted">—</span>}</td>
            <td className="small">{s.summary}{s.cost_usd != null && <span className="muted"> · {fmtUsd(s.cost_usd)}</span>}{s.input_tokens != null && <span className="muted"> · {s.input_tokens}→{s.output_tokens} tok</span>}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

export function AssertionList({ rows }: { rows: any[] }) {
  if (!rows?.length) return null
  const applicable = rows.filter((r) => r.applicable)
  return (
    <details className="assertions">
      <summary>Assertions: {applicable.filter((r) => r.passed).length}/{applicable.length} passed {applicable.some((r) => !r.passed) && <Badge kind="danger">failures</Badge>}</summary>
      <ul>
        {rows.map((r) => (
          <li key={r.name} className={r.applicable ? (r.passed ? 'pass' : 'fail') : 'na'}>
            <span className="mono">{r.name}</span> — {r.applicable ? (r.passed ? 'pass' : 'FAIL') : 'n/a'} <span className="muted">{r.detail}</span>
          </li>
        ))}
      </ul>
    </details>
  )
}

export function Verification({ v, policyLabel }: { v: any; policyLabel?: string | null }) {
  if (!v) return null
  const o = v.order
  const a = v.refund_assessment
  const s = v.shipping_assessment
  return (
    <div className="verification">
      <div className="section-label">Verification (from the order and entitlement adapters)</div>
      <div className="ver-grid">
        <div className="ver-card">
          <div className="ver-title">Order lookup</div>
          {o ? (
            <KV rows={[['Order', <span className="mono">{o.order_id}</span>], ['Product', o.product], ['Amount', `$${Number(o.amount).toFixed(2)}`],
              ['Charged', `${o.charge_date} (${o.days_since_charge} days ago)`], ['Status', o.order_status],
              ...(o.shipping ? [['Shipping', `${o.shipping.status} · ${o.shipping.carrier || ''} ${o.shipping.service || ''} → ${o.shipping.destination || ''}`] as [string, React.ReactNode]] : [])]} />
          ) : <div className="muted">{v.order_lookup_error ? `not verified: ${v.order_lookup_error}` : 'no order looked up'}</div>}
        </div>
        <div className="ver-card">
          <div className="ver-title">Entitlement</div>
          {v.entitlement_verified ? (
            <KV rows={[['Plan / tier', `${v.plan} / ${v.tier}`], ['Policy version', <Badge kind="info">{policyLabel || v.policy_version}</Badge>],
              ...(a && a.assessable !== false ? [['Refund assessment', <span>{a.eligible === true ? <Badge kind="ok">eligible</Badge> : a.eligible === false ? <Badge kind="danger">not eligible</Badge> : <Badge kind="warn">needs billing</Badge>} <span className="small">{a.reason}</span></span>] as [string, React.ReactNode]] : []),
              ...(v.shipping_entitlement ? [['Shipping entitlement', `standard ${v.shipping_entitlement.standard}, expedited ${v.shipping_entitlement.expedited}, replacement ${v.shipping_entitlement.replacement}`] as [string, React.ReactNode]] : [])]} />
          ) : <div className="muted">not verified</div>}
          {s?.decision && <div className="small"><b>Shipping check:</b> {s.reason}</div>}
        </div>
      </div>
      {(v.conflicts?.length || v.missing?.length) ? (
        <div className="conflicts">
          {v.missing?.map((m: string) => <div key={m}><Badge kind="warn">missing</Badge> {m}</div>)}
          {v.conflicts?.map((m: string) => <div key={m}><Badge kind="danger">conflict</Badge> {m}</div>)}
        </div>
      ) : null}
      {v.passages_dropped_by_version?.length ? (
        <div className="small muted">Dropped by the policy-version filter: {v.passages_dropped_by_version.map((d: any) => d.slug).join(', ')}</div>
      ) : null}
    </div>
  )
}

export function TraceLink({ id }: { id?: string | null }) {
  if (!id) return null
  return <Link to={`traces/${id}`} className="mono trace-link" title="Open in the trace viewer">trace {id}</Link>
}

export function Ts({ ts }: { ts?: string | null }) {
  return <span title={ts || ''}>{fmtTs(ts)}</span>
}
