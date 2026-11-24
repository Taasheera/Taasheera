const official = new Set(['home-affairs.ec.europa.eu', 'www.gov.uk', 'travel.state.gov', 'www.canada.ca'])

export default function AssistantText({ text }: { text: string }) {
  return <>{text.split(/(https:\/\/[^\s<>\])]+)/g).map((part, index) => {
    if (!part.startsWith('https://')) return part
    const url = part.replace(/[.,;]+$/, '')
    try {
      if (official.has(new URL(url).hostname)) {
        return <span key={index}><a href={url} target="_blank" rel="noreferrer">Official guidance</a>{part.slice(url.length)}</span>
      }
    } catch { /* Keep malformed URLs as text. */ }
    return part
  })}</>
}
