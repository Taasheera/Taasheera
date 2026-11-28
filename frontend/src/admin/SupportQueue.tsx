import { useEffect, useState } from 'react'
import { Link, useOutletContext, useSearchParams } from 'react-router'
import Feature from '../Feature'
import NotFound from '../pages/NotFound'
import { AdminRequestError } from './api'
import type { AdminContext } from './AdminLayout'
import { readableDate } from './format'
import {
  listRequests, listShared, SUPPORT_PAGE_SIZE, supportStatuses, supportStatusLabels,
  type RequestPage, type SharedApplication, type SupportStatus,
} from './supportApi'
import './support.css'

export default function SupportQueue() {
  return <Feature name="support_tools" fallback={<NotFound />}><Queue /></Feature>
}

function Shared() {
  const [shared, setShared] = useState<SharedApplication[] | null>(null)
  useEffect(() => {
    let active = true
    listShared().then(found => { if (active) setShared(found) }).catch(() => { if (active) setShared([]) })
    return () => { active = false }
  }, [])
  return <section className="admin-panel" aria-labelledby="shared-heading">
    <h2 id="shared-heading">Shared with support</h2>
    <p className="admin-muted">Applications travelers let support read right now. Access ends on its own; travelers can stop it at any time.</p>
    {shared === null ? <p className="admin-muted" role="status">Loading…</p>
      : shared.length === 0 ? <p className="admin-muted">No application is shared right now.</p>
        : <ul className="support-list">
          {shared.map(item => <li key={item.application_id}>
            <Link to={`/admin/support/applications/${item.application_id}`}>{item.title}</Link>
            <p className="admin-muted">{item.traveler_name} · until {readableDate(item.expires_at)}</p>
          </li>)}
        </ul>}
  </section>
}

function Queue() {
  const { leave } = useOutletContext<AdminContext>()
  const [params, setParams] = useSearchParams()
  const status: SupportStatus | '' = supportStatuses.find(value => value === params.get('status')) ?? ''
  const mine = params.get('mine') === 'true'
  const page = Math.max(1, Number.parseInt(params.get('page') ?? '1', 10) || 1)
  const [result, setResult] = useState<RequestPage | null>(null)
  const [error, setError] = useState('')
  const [retry, setRetry] = useState(0)

  useEffect(() => {
    let active = true
    listRequests({ status, mine, page }).then(found => {
      if (active) { setResult(found); setError('') }
    }).catch(failure => {
      if (!active) return
      if (failure instanceof AdminRequestError && failure.status === 403) leave()
      else setError(failure instanceof Error ? failure.message : 'Could not load support requests.')
    })
    return () => { active = false }
  }, [status, mine, page, retry, leave])

  function update(changes: Record<string, string>) {
    const next = new URLSearchParams(params)
    for (const [key, value] of Object.entries(changes)) {
      if (value) next.set(key, value)
      else next.delete(key)
    }
    setParams(next)
  }

  const pages = result ? Math.max(1, Math.ceil(result.total / SUPPORT_PAGE_SIZE)) : 1
  return <div className="admin-page">
    <h1>Support requests</h1>
    <section className="admin-panel" aria-labelledby="queue-heading">
      <h2 id="queue-heading">Queue</h2>
      <div className="admin-filters">
        <label className="admin-field">
          <span>Status</span>
          <select id="support-status" value={status} onChange={event => update({ status: event.target.value, page: '' })}>
            <option value="">Any status</option>
            {supportStatuses.map(option => <option key={option} value={option}>{supportStatusLabels[option]}</option>)}
          </select>
        </label>
        <label className="support-check">
          <input id="support-mine" type="checkbox" checked={mine}
            onChange={event => update({ mine: event.target.checked ? 'true' : '', page: '' })} />
          Assigned to me
        </label>
      </div>
      {error ? <div role="alert">
        <p className="admin-error">{error}</p>
        <button type="button" className="button button-secondary" onClick={() => { setError(''); setRetry(value => value + 1) }}>Try again</button>
      </div> : result === null ? <p className="admin-muted" role="status">Loading support requests…</p> : <>
        <p className="admin-muted" role="status">{result.total} {result.total === 1 ? 'request' : 'requests'}</p>
        {result.items.length > 0 && <div className="admin-table-wrap">
          <table className="admin-table">
            <thead><tr>
              <th scope="col">Traveler</th><th scope="col">Message</th><th scope="col">Status</th>
              <th scope="col">Assignee</th><th scope="col">From</th><th scope="col">Opened</th>
            </tr></thead>
            <tbody>
              {result.items.map(item => <tr key={item.id}>
                <td><Link to={`/admin/support/requests/${item.id}`}>{item.traveler_name}</Link></td>
                <td>{item.excerpt}</td>
                <td>{supportStatusLabels[item.status]}</td>
                <td>{item.assigned_to_me ? 'You' : item.assignee_name ?? 'Nobody yet'}</td>
                <td>{item.source === 'telegram' ? 'Telegram' : 'Website'}</td>
                <td>{readableDate(item.created_at)}</td>
              </tr>)}
            </tbody>
          </table>
        </div>}
        {result.total > SUPPORT_PAGE_SIZE && <nav className="admin-pagination" aria-label="Pages">
          <button type="button" className="button button-secondary" disabled={page <= 1}
            onClick={() => update({ page: String(page - 1) })}>Previous</button>
          <span>Page {page} of {pages}</span>
          <button type="button" className="button button-secondary" disabled={page >= pages}
            onClick={() => update({ page: String(page + 1) })}>Next</button>
        </nav>}
      </>}
    </section>
    <Shared />
  </div>
}
