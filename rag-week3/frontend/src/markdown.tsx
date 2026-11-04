// A small markdown renderer for help-centre articles and report excerpts (no dependency).
function inline(s: string): string {
  return s
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/\*([^*]+)\*/g, '<em>$1</em>')
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
}

export function markdownToHtml(md: string): string {
  const lines = (md || '').replace(/\r/g, '').split('\n')
  const out: string[] = []
  const block = /^(#{1,6})\s|^\s*\||^\s*>|^\s*[-*]\s+|^\s*\d+\.\s+|^\s*```/
  let i = 0
  while (i < lines.length) {
    const line = lines[i]
    if (/^\s*$/.test(line)) { i++; continue }
    const h = /^(#{1,6})\s+(.*)$/.exec(line)
    if (h) { out.push(`<h${h[1].length}>${inline(h[2])}</h${h[1].length}>`); i++; continue }
    if (/^\s*\|/.test(line)) {
      const rows: string[] = []
      while (i < lines.length && /^\s*\|/.test(lines[i])) { rows.push(lines[i]); i++ }
      const cells = (r: string) => r.trim().replace(/^\||\|$/g, '').split('|').map((c) => inline(c.trim()))
      const body = rows.filter((r) => !/^\s*\|?\s*:?-{2,}/.test(r))
      const [head, ...rest] = body
      out.push('<table><thead><tr>' + cells(head || '').map((c) => `<th>${c}</th>`).join('') + '</tr></thead><tbody>' +
        rest.map((r) => '<tr>' + cells(r).map((c) => `<td>${c}</td>`).join('') + '</tr>').join('') + '</tbody></table>')
      continue
    }
    if (/^\s*>/.test(line)) {
      const q: string[] = []
      while (i < lines.length && /^\s*>/.test(lines[i])) { q.push(lines[i].replace(/^\s*>\s?/, '')); i++ }
      out.push(`<blockquote>${inline(q.join(' '))}</blockquote>`); continue
    }
    if (/^\s*[-*]\s+/.test(line) || /^\s*\d+\.\s+/.test(line)) {
      const ordered = /^\s*\d+\./.test(line)
      const items: string[] = []
      while (i < lines.length && (/^\s*[-*]\s+/.test(lines[i]) || /^\s*\d+\.\s+/.test(lines[i]))) {
        items.push(lines[i].replace(/^\s*([-*]|\d+\.)\s+/, '')); i++
      }
      const tag = ordered ? 'ol' : 'ul'
      out.push(`<${tag}>` + items.map((t) => `<li>${inline(t)}</li>`).join('') + `</${tag}>`); continue
    }
    if (/^\s*```/.test(line)) {
      const code: string[] = []; i++
      while (i < lines.length && !/^\s*```/.test(lines[i])) { code.push(lines[i]); i++ }
      i++
      out.push(`<pre><code>${code.join('\n').replace(/&/g, '&amp;').replace(/</g, '&lt;')}</code></pre>`); continue
    }
    const para: string[] = []
    while (i < lines.length && !/^\s*$/.test(lines[i]) && !block.test(lines[i])) { para.push(lines[i]); i++ }
    if (!para.length) { para.push(lines[i]); i++ }
    out.push(`<p>${inline(para.join(' '))}</p>`)
  }
  return out.join('\n')
}

export function Markdown({ text, className = 'md' }: { text?: string | null; className?: string }) {
  if (!text) return <div className="muted">Not available.</div>
  return <div className={className} dangerouslySetInnerHTML={{ __html: markdownToHtml(text) }} />
}
