// Typed access to /api/capstone/*. Every number the screens show comes from here.

export type Json = Record<string, any>

async function request<T = Json>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, { headers: { 'Content-Type': 'application/json' }, ...init })
  const text = await res.text()
  let body: any = {}
  try { body = text ? JSON.parse(text) : {} } catch { body = { error: text } }
  if (!res.ok) {
    const err: any = new Error(body.error || `${res.status} ${res.statusText}`)
    err.status = res.status
    err.body = body
    throw err
  }
  return body as T
}

export const api = {
  health: () => request('/api/capstone/health'),
  dashboard: () => request('/api/capstone/dashboard'),
  tickets: (params: { status?: string; q?: string } = {}) => {
    const qs = new URLSearchParams()
    if (params.status) qs.set('status', params.status)
    if (params.q) qs.set('q', params.q)
    return request(`/api/capstone/tickets${qs.toString() ? '?' + qs : ''}`)
  },
  ticket: (id: string) => request(`/api/capstone/tickets/${encodeURIComponent(id)}`),
  analyse: (id: string, body: Json) => request(`/api/capstone/tickets/${encodeURIComponent(id)}/analyse`, { method: 'POST', body: JSON.stringify(body) }),
  action: (id: string, body: Json) => request(`/api/capstone/tickets/${encodeURIComponent(id)}/action`, { method: 'POST', body: JSON.stringify(body) }),
  chat: (body: Json) => request('/api/capstone/chat', { method: 'POST', body: JSON.stringify(body) }),
  conversations: () => request('/api/capstone/conversations'),
  conversation: (id: string) => request(`/api/capstone/conversations/${encodeURIComponent(id)}`),
  traces: (limit = 60) => request(`/api/capstone/traces?limit=${limit}`),
  trace: (id: string) => request(`/api/capstone/trace?trace_id=${encodeURIComponent(id)}`),
  article: (slug: string) => request(`/api/capstone/articles/${encodeURIComponent(slug)}`),
  escalations: () => request('/api/capstone/escalations'),
  evaluation: () => request('/api/capstone/evaluation'),
  mcp: (discover = false) => request(`/api/capstone/mcp${discover ? '?discover=1' : ''}`),
  runs: () => request('/api/runs'),
}

export function fmtMs(ms?: number | null): string {
  if (ms == null) return '—'
  if (ms < 1000) return `${Math.round(ms)} ms`
  return `${(ms / 1000).toFixed(ms < 10000 ? 1 : 0)} s`
}

export function fmtUsd(v?: number | null, digits = 6): string {
  if (v == null) return '—'
  return `$${v.toFixed(digits)}`
}

export function fmtPct(v?: number | null, digits = 1): string {
  if (v == null) return '—'
  return `${v.toFixed(digits)}%`
}

export function fmtTs(ts?: string | null): string {
  if (!ts) return '—'
  const d = new Date(ts)
  if (isNaN(d.getTime())) return ts
  return d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}
