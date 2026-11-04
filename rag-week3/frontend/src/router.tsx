import { useEffect, useState } from 'react'

export const BASE = '/app'

function current(): string {
  const p = window.location.pathname
  const rest = p.startsWith(BASE) ? p.slice(BASE.length) : p
  return rest.replace(/^\/+/, '').replace(/\/+$/, '') || 'dashboard'
}

export function navigate(path: string) {
  const target = `${BASE}/${path.replace(/^\/+/, '')}`
  if (window.location.pathname !== target) {
    window.history.pushState({}, '', target)
    window.dispatchEvent(new PopStateEvent('popstate'))
  }
}

export function useRoute(): string[] {
  const [route, setRoute] = useState(current())
  useEffect(() => {
    const onPop = () => setRoute(current())
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])
  return route.split('/').map(decodeURIComponent)
}

export function Link({ to, children, className, title }: { to: string; children: React.ReactNode; className?: string; title?: string }) {
  return (
    <a href={`${BASE}/${to.replace(/^\/+/, '')}`} className={className} title={title}
      onClick={(e) => { if (!e.metaKey && !e.ctrlKey) { e.preventDefault(); navigate(to) } }}>
      {children}
    </a>
  )
}

/** A citation url like /app/help-centre/slug becomes an in-app route. */
export function hrefToRoute(url: string): string {
  return url.startsWith(BASE) ? url.slice(BASE.length).replace(/^\/+/, '') : url
}
