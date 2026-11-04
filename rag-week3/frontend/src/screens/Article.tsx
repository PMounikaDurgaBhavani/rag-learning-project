import { useEffect, useState } from 'react'
import { api } from '../api'
import { Markdown } from '../markdown'
import { Link } from '../router'
import { Badge, ErrorBox, Spinner } from '../ui'

export default function Article({ slug }: { slug?: string }) {
  const [doc, setDoc] = useState<any>(null)
  const [error, setError] = useState<any>(null)
  useEffect(() => { setDoc(null); setError(null); if (slug) api.article(slug).then(setDoc).catch(setError) }, [slug])
  if (!slug) return <div className="muted">No article selected.</div>
  if (error) return <ErrorBox error={error} />
  if (!doc) return <Spinner label="Loading article…" />
  return (
    <div className="article">
      <div className="page-head"><div><h1>{doc.title}</h1><p className="mono">{doc.article_id} · {doc.slug} · updated {doc.last_updated} · {doc.product_area}</p></div>
        <div className="toolbar"><Badge kind={doc.status === 'superseded' ? 'danger' : 'ok'}>{doc.status}</Badge>{doc.policy_version && <Badge kind="info">policy version {doc.policy_version}</Badge>}{doc.doc_type !== 'article' && <Badge>{doc.doc_type}</Badge>}</div></div>
      {doc.status === 'superseded' && <div className="banner warn">This policy is retired. It applies only to accounts whose entitlement record names it. Current version: <Link to={`help-centre/${doc.superseded_by === 'HC-008' ? 'refund-returns-current-30-day' : doc.superseded_by}`}>{doc.superseded_by}</Link>.</div>}
      {doc.supersedes && <div className="banner info">Supersedes {doc.supersedes} (retired 14-day policy, kept in the corpus for legacy accounts).</div>}
      <div className="card"><Markdown text={doc.markdown} className="md article" /></div>
      <div className="footnote">Source file: {doc.source_file} · served from the Postgres corpus via /api/capstone/articles/{doc.slug}</div>
    </div>
  )
}
