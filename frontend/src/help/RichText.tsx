import type { ReactNode } from 'react'

const INLINE = /\*\*([^*\n]+)\*\*|\[([^\]\n]+)\]\(([^)\s]+)\)/g

function safeHref(url: string): string | null {
  if (url.startsWith('/') && !url.startsWith('//')) return url
  try {
    return new URL(url).protocol === 'https:' ? url : null
  } catch {
    return null
  }
}

function inline(text: string): ReactNode[] {
  const parts: ReactNode[] = []
  let last = 0
  for (const match of text.matchAll(INLINE)) {
    if (match.index > last) parts.push(text.slice(last, match.index))
    const [whole, bold, label, url] = match
    const href = url === undefined ? null : safeHref(url)
    if (bold !== undefined) parts.push(<strong key={match.index}>{bold}</strong>)
    else if (href) parts.push(<a key={match.index} href={href} {...(href.startsWith('/') ? {} : { target: '_blank', rel: 'noreferrer' })}>{label}</a>)
    else parts.push(whole)
    last = match.index + whole.length
  }
  if (last < text.length) parts.push(text.slice(last))
  return parts
}

function lines(block: string): ReactNode[] {
  return block.split('\n').flatMap((line, index) => index === 0 ? inline(line) : [<br key={`br-${index}`} />, ...inline(line)])
}

export default function RichText({ text }: { text: string }) {
  const blocks = text.replace(/\r\n/g, '\n').split(/\n\s*\n/).map(block => block.trim()).filter(Boolean)
  return <>
    {blocks.map((block, index) => {
      const items = block.split('\n')
      return items.every(item => item.startsWith('- '))
        ? <ul key={index}>{items.map((item, position) => <li key={position}>{inline(item.slice(2))}</li>)}</ul>
        : <p key={index}>{lines(block)}</p>
    })}
  </>
}
