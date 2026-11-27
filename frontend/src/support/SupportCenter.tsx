import { useEffect, useState } from 'react'
import { formatDateTime } from '../dashboard/helpers'
import {
  listAccessGrants, listHelpRequests, requestStatusLabels, stopSharing, type AccessGrant, type HelpRequest,
} from './api'
import './support.css'

export default function SupportCenter() {
  const [requests, setRequests] = useState<HelpRequest[] | null>(null)
  const [grants, setGrants] = useState<AccessGrant[] | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let active = true
    Promise.all([listHelpRequests(), listAccessGrants()]).then(([foundRequests, foundGrants]) => {
      if (active) { setRequests(foundRequests); setGrants(foundGrants) }
    }).catch(problem => {
      if (active) setError(problem instanceof Error ? problem.message : 'Could not load your help requests.')
    })
    return () => { active = false }
  }, [])

  async function stop(grant: AccessGrant) {
    if (busy) return
    setBusy(true)
    setError('')
    try {
      await stopSharing(grant.id)
      setGrants(current => current?.filter(item => item.id !== grant.id) ?? null)
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : 'Could not stop sharing. Please try again.')
    } finally {
      setBusy(false)
    }
  }

  return <section id="support-requests" className="profile-section" aria-labelledby="support-requests-heading">
    <h2 id="support-requests-heading">Help requests</h2>
    <p>Messages you sent to our support team, and their replies. To ask something new, open the assistant and choose Talk to a person.</p>
    {error && <p className="auth-error" role="alert">{error}</p>}
    {requests === null ? !error && <p role="status">Loading your help requests…</p>
      : requests.length === 0 ? <p>You haven't asked support anything yet.</p>
        : <ul className="support-items">
          {requests.map(item => <li key={item.id}>
            <strong>{requestStatusLabels[item.status]}</strong> · <time dateTime={item.created_at}>{formatDateTime(item.created_at)}</time>
            <p>{item.message}</p>
            {item.reply && <div className="support-reply">
              <small>Support replied{item.replied_at && <> on <time dateTime={item.replied_at}>{formatDateTime(item.replied_at)}</time></>}</small>
              <p>{item.reply}</p>
            </div>}
          </li>)}
        </ul>}

    <h3>Applications support can read</h3>
    {grants === null ? null : grants.length === 0 ? <p>You aren't sharing any application with support.</p>
      : <ul className="support-items">
        {grants.map(grant => <li key={grant.id}>
          <a href={`/dashboard/${grant.application_id}`}>{grant.title}</a>
          <p>Until <time dateTime={grant.expires_at}>{formatDateTime(grant.expires_at)}</time></p>
          <button type="button" className="google-retry" disabled={busy} onClick={() => void stop(grant)}>Stop sharing</button>
        </li>)}
      </ul>}
  </section>
}
