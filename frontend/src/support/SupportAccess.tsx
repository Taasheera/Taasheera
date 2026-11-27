import { useEffect, useState } from 'react'
import { formatDateTime } from '../dashboard/helpers'
import { listAccessGrants, shareDurations, shareWithSupport, stopSharing, type AccessGrant, type ShareHours } from './api'
import './support.css'

export default function SupportAccess({ applicationId }: { applicationId: string }) {
  const [grants, setGrants] = useState<AccessGrant[] | null>(null)
  const [hours, setHours] = useState<ShareHours>(72)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    let active = true
    listAccessGrants().then(found => { if (active) setGrants(found) }).catch(() => { if (active) setGrants([]) })
    return () => { active = false }
  }, [])

  const grant = grants?.find(item => item.application_id === applicationId)
  const others = grants?.filter(item => item.application_id !== applicationId) ?? []

  async function change(action: () => Promise<AccessGrant | null>) {
    if (busy) return
    setBusy(true)
    setError('')
    try {
      const updated = await action()
      setGrants(updated ? [...others, updated] : others)
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : 'Something went wrong. Please try again.')
    } finally {
      setBusy(false)
    }
  }

  return <section className="support-access" aria-labelledby="support-access-title">
    <h2 id="support-access-title">Support access</h2>
    {grants === null ? <p className="dash-status" role="status">Checking support access…</p> : grant ? <>
      <p>Our support team can read this application until <strong>{formatDateTime(grant.expires_at)}</strong>: answers, checks and
        timeline, read only. Each time they open it shows as Support in your document access history.</p>
      <div className="support-actions">
        <button type="button" className="button button-secondary dash-small" disabled={busy}
          onClick={() => void change(async () => { await stopSharing(grant.id); return null })}>Stop sharing now</button>
      </div>
    </> : <>
      <p>Need a person to look at this application? Let our support team read it, read only, for as long as you choose.
        They never see your passport photo or documents, and you can stop sharing at any time.</p>
      <div className="support-actions">
        <label className="visually-hidden" htmlFor={`support-hours-${applicationId}`}>How long support may read it</label>
        <select id={`support-hours-${applicationId}`} value={hours} disabled={busy}
          onChange={event => setHours(Number(event.target.value) as ShareHours)}>
          {shareDurations.map(option => <option key={option.hours} value={option.hours}>{option.label}</option>)}
        </select>
        <button type="button" className="button button-secondary dash-small" disabled={busy}
          onClick={() => void change(() => shareWithSupport(applicationId, hours))}>Share with support</button>
      </div>
    </>}
    {error && <p className="auth-error" role="alert">{error}</p>}
  </section>
}
