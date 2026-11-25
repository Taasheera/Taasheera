import { OFFICIAL_SOURCES, splitCitations } from './citations'

export default function AssistantText({ text }: { text: string }) {
  const { body, sources } = splitCitations(text)
  return <>
    {body}
    {sources.length > 0 && <span className="assistant-sources">
      <span className="assistant-sources-label">Sources:</span>{' '}
      {sources.map((url, index) => <span key={url}>{index > 0 && ' · '}
        <a href={url} target="_blank" rel="noreferrer">{OFFICIAL_SOURCES[new URL(url).hostname]}</a>
      </span>)}
    </span>}
  </>
}
