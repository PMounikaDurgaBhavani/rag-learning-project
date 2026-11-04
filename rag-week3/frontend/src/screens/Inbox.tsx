import { useEffect, useState } from 'react'
import { api, fmtMs, fmtUsd } from '../api'
import { Link, navigate } from '../router'
import { AssertionList, Badge, Citations, Elapsed, ErrorBox, KV, Spinner, ToolTable, TraceLink, Ts, Verification, outcomeKind } from '../ui'

const STATUS_KIND: Record<string, any> = { new: 'neutral', awaiting_approval: 'info', answered: 'ok', escalated: 'warn', resolved: 'ok', ai_processed: 'info' }

export default function Inbox({ ticketId }: { ticketId?: string }) {
  const [list, setList] = useState<any[]>([])
  const [status, setStatus] = useState('')
  const [q, setQ] = useState('')
  const [error, setError] = useState<any>(null)
  const load = () => api.tickets({ status, q }).then((d) => setList(d.tickets)).catch(setError)
  useEffect(() => { load() }, [status, q])
  return (
    <>
      <div className="page-head">
        <div><h1>Ticket inbox</h1><p>Open a ticket, run the AI analysis, read the tool calls, then approve, edit, escalate or resolve.</p></div>
        <div className="toolbar">
          <input type="search" placeholder="Search id, subject, tags…" value={q} onChange={(e) => setQ(e.target.value)} />
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">All statuses</option>
            {['new', 'awaiting_approval', 'answered', 'escalated', 'resolved'].map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        </div>
      </div>
      <ErrorBox error={error} onRetry={load} />
      <div className="inbox">
        <div className="card ticket-list">
          {list.map((t) => (
            <div key={t.ticket_id} className={`ticket-row ${t.ticket_id === ticketId ? 'selected' : ''}`} onClick={() => navigate(`inbox/${t.ticket_id}`)}>
              <div className="t1"><span className="mono small">{t.ticket_id}</span><Badge kind={STATUS_KIND[t.status] || 'neutral'}>{t.status}</Badge></div>
              <div className="subject">{t.subject}</div>
              <div className="t3"><span>{t.customer_name}</span><span>· {t.tier}</span><span>· {t.priority}</span>{t.ai && <span>· AI: {t.ai.outcome}</span>}</div>
            </div>
          ))}
          {list.length === 0 && <div className="muted" style={{ padding: 14 }}>No tickets match.</div>}
        </div>
        <div>{ticketId ? <TicketDetail ticketId={ticketId} onChanged={load} /> : <div className="card muted">Select a ticket.</div>}</div>
      </div>
    </>
  )
}

function TicketDetail({ ticketId, onChanged }: { ticketId: string; onChanged: () => void }) {
  const [t, setT] = useState<any>(null)
  const [error, setError] = useState<any>(null)
  const [busy, setBusy] = useState<{ since: number; what: string } | null>(null)
  const [planner, setPlanner] = useState<'model' | 'code'>('model')
  const [policyFilter, setPolicyFilter] = useState(true)
  const [result, setResult] = useState<any>(null)
  const [draft, setDraft] = useState('')
  const [agent, setAgent] = useState('tier1-anna')
  const load = () => api.ticket(ticketId).then((d) => { setT(d.ticket); setDraft(d.ticket?.ai?.answer || ''); setResult(null) }).catch(setError)
  useEffect(() => { setError(null); load() }, [ticketId])
  if (error) return <ErrorBox error={error} onRetry={load} />
  if (!t) return <Spinner label="Loading ticket…" />

  const run = async () => {
    setBusy({ since: Date.now(), what: planner === 'model' ? 'agent loop (model picks tools)' : 'planned tools' }); setError(null)
    try {
      const d = await api.analyse(ticketId, { planner, policy_filter: policyFilter })
      setResult(d.result); setT(d.ticket); setDraft(d.result.answer || ''); onChanged()
    } catch (e) { setError(e) } finally { setBusy(null) }
  }
  const act = async (body: any) => {
    setError(null)
    try { const d = await api.action(ticketId, { ...body, agent }); setT(d.ticket); onChanged() } catch (e) { setError(e) }
  }
  const ai = t.ai
  const c = t.customer
  return (
    <>
      <div className="card">
        <div className="page-head" style={{ marginBottom: 8 }}>
          <div><h2 style={{ fontSize: 17 }}>{t.subject}</h2><div className="muted mono small">{t.ticket_id} · created <Ts ts={t.created_at} /> · {t.channel}</div></div>
          <div className="toolbar"><Badge kind={STATUS_KIND[t.status]}>{t.status}</Badge><Badge kind={t.priority === 'high' || t.priority === 'urgent' ? 'danger' : 'neutral'}>{t.priority}</Badge></div>
        </div>
        <div className="reply-box">{t.description}</div>
        <div className="grid-2" style={{ marginTop: 12 }}>
          <KV rows={[['Customer', <span>{c?.name} <span className="muted">({c?.customer_id})</span></span>], ['Email', <span className="mono">{c?.email} <Badge kind="violet" title={t.pii_scope}>authorised view</Badge></span>],
            ['Plan / tier', `${c?.plan} / ${c?.tier}`], ['Policy terms', c?.policy_terms_version], ['Assigned agent', t.assigned_agent || '—'], ['Tags', (t.tags || []).join(', ')]]} />
          <KV rows={[['Order on ticket', t.order_id ? <span className="mono">{t.order_id}</span> : '—'],
            ['Orders on account', t.orders?.length ? t.orders.map((o: any) => `${o.order_id} ($${o.amount}, ${o.order_status})`).join('; ') : 'none'],
            ['Entitlement', t.entitlement?.verified ? `${t.entitlement.refund_policy.label} · ${t.entitlement.refund_policy.window_days}-day window · ${t.entitlement.refund_policy.article_id}` : 'not found'],
            ['Source', t.source]]} />
        </div>
      </div>

      <div className="card">
        <h2>AI analysis</h2>
        <div className="toolbar">
          <select value={planner} onChange={(e) => setPlanner(e.target.value as any)} disabled={!!busy}>
            <option value="model">Agent loop — the model chooses the tools (slow on this machine)</option>
            <option value="code">Fast mode — planned tools, model writes the reply</option>
          </select>
          <label className="small"><input type="checkbox" checked={policyFilter} onChange={(e) => setPolicyFilter(e.target.checked)} disabled={!!busy} /> policy-version filter (capstone-v1.1; off = v1.0 behaviour)</label>
          <button className="btn" onClick={run} disabled={!!busy}>{busy ? 'Running…' : ai ? 'Run again' : 'Run AI analysis'}</button>
        </div>
        {busy && <div className="spinner-row"><span className="spinner" /> Processing: {busy.what} · <Elapsed since={busy.since} /> elapsed. The local 1.5B model answers one request at a time; a cold start loads it first (~2 min).</div>}
        <ErrorBox error={error} />
        {ai && !busy && (
          <>
            <div className="toolbar" style={{ margin: '10px 0' }}>
              <Badge kind={outcomeKind(ai.outcome)}>{ai.outcome}</Badge>
              <Badge kind="neutral">decision: {ai.decision}</Badge>
              <Badge kind="neutral">intent: {ai.intent}</Badge>
              {ai.policy_version && <Badge kind="info">policy: {ai.policy_label || ai.policy_version}</Badge>}
              {ai.injection_detected && <Badge kind="danger">prompt injection detected</Badge>}
              <span className="muted small">{fmtMs(ai.latency_ms)} · {ai.tokens} tokens · {fmtUsd(ai.cost_usd)} (stand-in rate card) · {ai.planner} planner · {ai.prompt_version}</span>
              <TraceLink id={ai.trace_id} />
            </div>
            {ai.escalation && <div className="banner warn">Escalated → queue <b>{ai.escalation.queue}</b>, assigned {ai.escalation.assigned_to}, reference <span className="mono">{ai.escalation.reference}</span>. {(result?.verification || {}).escalation_reason}</div>}
            <div className="section-label">{ai.outcome === 'answered' ? 'AI draft (edit before approving)' : 'Hand-off message'}</div>
            <textarea value={draft} onChange={(e) => setDraft(e.target.value)} />
            <Citations items={ai.citations} title="Cited sources" />
            {ai.consulted?.length > ai.citations?.length && <details><summary className="small muted">All {ai.consulted.length} passages consulted</summary><Citations items={ai.consulted} title="" /></details>}
            {result && <Verification v={result.verification} policyLabel={result.policy_label} />}
            <div className="section-label">Tool execution</div>
            <ToolTable steps={result ? result.timeline : ai.tools} />
            {result && <AssertionList rows={[...(result.assertions?.week6 || []), ...(result.assertions?.capstone || [])]} />}
            {result?.draft_rejected && <div className="banner warn">A first draft was rejected by the output guardrail ({result.draft_rejected.hard_failures.join(', ')}).</div>}
          </>
        )}
      </div>

      <div className="card">
        <h2>Agent actions</h2>
        <div className="toolbar">
          <select value={agent} onChange={(e) => setAgent(e.target.value)}>
            {['anna', 'ben', 'chloe', 'dev', 'ella', 'farid', 'gia', 'hugo', 'iris', 'jonas', 'kira', 'leo', 'maya', 'noor'].map((n) => <option key={n} value={`tier1-${n}`}>tier1-{n}</option>)}
          </select>
          <button className="btn btn-ok btn-sm" disabled={!draft || !!busy} onClick={() => act({ action: 'approve', reply: draft })}>{draft && ai && draft !== ai.answer ? 'Approve edited reply' : 'Approve AI draft'}</button>
          <button className="btn btn-ghost btn-sm" onClick={() => { const reason = window.prompt('Escalation reason'); if (reason) act({ action: 'escalate', reason }) }}>Escalate to human</button>
          <button className="btn btn-ghost btn-sm" onClick={() => act({ action: 'assign' })}>Assign to me</button>
          <button className="btn btn-ghost btn-sm" onClick={() => act({ action: 'resolve' })}>Mark resolved</button>
          {t.status !== 'new' && <button className="btn btn-ghost btn-sm" onClick={() => act({ action: 'reopen' })}>Reopen</button>}
        </div>
        {t.approved_reply && <><div className="section-label">Approved reply</div><div className="reply-box">{t.approved_reply}</div></>}
        {t.escalations?.length ? <><div className="section-label">Escalations</div>{t.escalations.map((e: any) => <div key={e.reference} className="small"><span className="mono">{e.reference}</span> · {e.queue} · {e.status} · {e.reason}</div>)}</> : null}
        {t.history?.length ? <><div className="section-label">History</div><div className="history">{t.history.map((h: any, i: number) => <div key={i}><Ts ts={h.at} /> · <b>{h.type}</b> by {h.by}{h.status ? ` → ${h.status}` : ''}{h.note ? ` — ${h.note}` : ''}{h.trace_id ? <> · <Link to={`traces/${h.trace_id}`} className="mono">{h.trace_id}</Link></> : null}</div>)}</div></> : null}
      </div>
    </>
  )
}
