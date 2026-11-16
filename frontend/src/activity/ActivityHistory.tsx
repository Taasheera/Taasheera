import { useEffect, useState } from 'react'
import { protectedFetch } from '../auth'
import './activityHistory.css'

type Event = { id: number; actor: string; occurred_at: string; beirut_time: string;
  event_type: string; identifier: string; outcome: string; resource_type: string | null;
  resource_id: string | null; purpose: string | null }
type Page = { items: Event[]; total: number; page: number; page_size: number; timezone: string }

const purposes: Record<string, string> = { image_view: 'Viewed passport image', extraction: 'Read for passport extraction',
  review_read: 'Read confirmed details', review_update: 'Read for review update', assistant_context: 'Read for assistant context',
  application_prefill: 'Read for application prefill', data_export: 'Included in your data download' }

function beirutTime(value: string): string {
  return new Intl.DateTimeFormat('en-GB', { timeZone: 'Asia/Beirut', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' }).format(new Date(value))
}

function description(event: Event): string {
  if (event.event_type === 'document_access') return purposes[event.purpose ?? ''] ?? 'Document access'
  if (event.event_type === 'api_action') return event.identifier
  return `${event.event_type.replaceAll('_', ' ')} · ${event.identifier}`
}

export default function ActivityHistory({ userId, documentsOnly = false, onDenied }: {
  userId?: number; documentsOnly?: boolean; onDenied?: () => void
}) {
  const path = userId ? `/admin-api/users/${userId}/activity` : '/activity/history'
  const [query, setQuery] = useState({ from: '', to: '', page: 1, refresh: 0 })
  const [opened, setOpened] = useState(Boolean(userId))
  const [result, setResult] = useState<Page | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => {
    if (!opened) return
    let active = true
    const params = new URLSearchParams({ page: String(query.page), page_size: '25', documents_only: String(documentsOnly) })
    if (query.from) params.set('from_at', query.from)
    if (query.to) params.set('to_at', query.to)
    // Selected values are Beirut wall-clock times. The API resolves their UTC
    // boundaries, including DST; the browser's own timezone never changes them.
    Promise.resolve().then(async () => {
      if (active) { setBusy(true); setError('') }
      const response = await protectedFetch(`${path}?${params}`, { cache: 'no-store', silent: true })
      if (response.status === 403 && onDenied) { if (active) onDenied(); return }
      if (response.status === 422) throw new Error('Check the From and To dates. Times use Beirut time, including daylight saving changes.')
      if (!response.ok) throw new Error('Could not load activity history. Please try again.')
      const page = await response.json() as Page
      if (active) setResult(page)
    }).catch(failure => { if (active) { setResult(null); setError(failure instanceof Error ? failure.message : 'Could not load activity history.') } })
      .finally(() => { if (active) setBusy(false) })
    return () => { active = false }
  }, [documentsOnly, onDenied, opened, path, query])
  const title = documentsOnly ? 'Document access history' : 'Full activity history'
  return <section className="activity-history profile-section" aria-label={title}>
    <h2>{title}</h2>
    <p>Stored in UTC; displayed and filtered in Beirut time (Asia/Beirut), to the second.</p>
    <form className="activity-filters" onSubmit={event => {
      event.preventDefault()
      const fields = new FormData(event.currentTarget)
      setOpened(true)
      setQuery(previous => ({ from: String(fields.get('from') ?? ''), to: String(fields.get('to') ?? ''), page: 1,
        refresh: previous.refresh + 1 }))
    }}>
      <label>From (Beirut time)<input type="datetime-local" name="from" step="1" disabled={busy} /></label>
      <label>To (Beirut time)<input type="datetime-local" name="to" step="1" disabled={busy} /></label>
      <button type="submit" disabled={busy} data-activity-id="activity-filter">Apply dates</button>
    </form>
    <button type="button" disabled={busy} data-activity-id="activity-refresh" onClick={() => {
      setOpened(true); setQuery(previous => ({ ...previous, page: 1, refresh: previous.refresh + 1 }))
    }}>{documentsOnly ? 'Refresh access history' : 'Refresh activity'}</button>
    {busy && <p role="status">Loading activity…</p>}
    {error && <p role="alert">{error}</p>}
    {result && !busy && <>
      <p role="status">{result.total} recorded {documentsOnly ? 'accesses' : 'actions'}</p>
      {result.items.length === 0 ? <p>No activity in the selected range.</p> : <ol className="access-history">
        {result.items.map(event => <li key={event.id}>
          <strong>{event.actor}</strong> · {description(event)} · <span>{event.outcome}</span>
          <br /><time dateTime={event.occurred_at}>{beirutTime(event.occurred_at)} Beirut</time>
          {event.resource_id && <><br /><small>{event.resource_type === 'passport' ? 'Passport' : 'Resource'} {event.resource_id.slice(0, 12)}</small></>}
        </li>)}
      </ol>}
      <nav className="activity-pagination" aria-label="Activity pages">
        <button type="button" disabled={busy || result.page <= 1} data-activity-id="activity-page"
          onClick={() => setQuery(previous => ({ ...previous, page: previous.page - 1 }))}>Previous</button>
        <span>Page {result.page} of {Math.max(1, Math.ceil(result.total / result.page_size))}</span>
        <button type="button" disabled={busy || result.page * result.page_size >= result.total} data-activity-id="activity-page"
          onClick={() => setQuery(previous => ({ ...previous, page: previous.page + 1 }))}>Next</button>
      </nav>
    </>}
  </section>
}
