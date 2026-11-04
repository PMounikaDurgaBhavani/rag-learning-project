import { useEffect, useState } from 'react'
import { api } from './api'
import { Link, useRoute } from './router'
import { Badge } from './ui'
import Dashboard from './screens/Dashboard'
import Inbox from './screens/Inbox'
import Chat from './screens/Chat'
import Traces from './screens/Traces'
import Evaluation from './screens/Evaluation'
import Article from './screens/Article'

const NAV = [
  ['dashboard', '◫', 'Dashboard'],
  ['inbox', '☰', 'Ticket inbox'],
  ['chat', '✦', 'AI chatbot'],
  ['traces', '⇢', 'Agent traces'],
  ['evaluation', '◈', 'Evaluation & monitoring'],
]

export default function App() {
  const [section, ...rest] = useRoute()
  const [health, setHealth] = useState<any>(null)
  const [running, setRunning] = useState<any>(null)

  useEffect(() => {
    let alive = true
    const tick = async () => {
      try { const h = await api.health(); if (alive) setHealth(h) } catch (e) { if (alive) setHealth({ ok: false, error: String(e) }) }
      try { const r = await api.runs(); if (alive) setRunning(r) } catch { /* ignore */ }
    }
    tick()
    const t = setInterval(tick, 15000)
    return () => { alive = false; clearInterval(t) }
  }, [])

  let screen: React.ReactNode
  switch (section) {
    case 'inbox': screen = <Inbox ticketId={rest[0]} />; break
    case 'chat': screen = <Chat conversationId={rest[0]} />; break
    case 'traces': screen = <Traces traceId={rest[0]} />; break
    case 'evaluation': screen = <Evaluation />; break
    case 'help-centre': screen = <Article slug={rest[0]} />; break
    default: screen = <Dashboard />
  }

  const mcp = health?.mcp
  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <span>CloudDesk Deflection Assistant</span>
          <small>Tier-1 support · Week 12 capstone</small>
        </div>
        <nav className="nav">
          {NAV.map(([key, ic, label]) => (
            <Link key={key} to={key} className={section === key || (section === 'help-centre' && key === 'dashboard' && false) ? 'active' : ''}>
              <span className="ic">{ic}</span>{label}
            </Link>
          ))}
        </nav>
        <div className="sidebar-foot">
          <div className="health-row"><span className={`dot ${health?.store_reachable ? 'ok' : 'bad'}`} /> Postgres corpus {health?.documents != null ? `· ${health.documents} docs` : ''}</div>
          <div className="health-row"><span className={`dot ${mcp?.connected ? 'ok' : mcp?.http_server_up ? 'warn' : 'bad'}`} /> MCP help centre {mcp?.http_server_up ? '(HTTP)' : '(stdio on demand)'}{mcp?.tools?.length ? ` · ${mcp.tools.length} tools` : ''}</div>
          <div className="health-row"><span className={`dot ${health?.models?.drafter_loaded ? 'ok' : 'warn'}`} /> {health?.models?.drafter_loaded ? 'model loaded' : 'model loads on first request'}</div>
          {running?.running && <div className="health-row"><span className="dot warn" /> running: {running.running}</div>}
          <div className="health-row"><Badge kind={health?.mode === 'production' ? 'ok' : 'warn'}>{(health?.mode || 'demo').toUpperCase()} MODE</Badge> <span>as of {health?.as_of}</span></div>
          <a href="/" className="small">Legacy studio (Weeks 3–11) →</a>
        </div>
      </aside>
      <main className="main">{screen}</main>
    </div>
  )
}
