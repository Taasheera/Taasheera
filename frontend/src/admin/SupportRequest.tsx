import { useEffect, useState, type FormEvent } from 'react'
import { Link, useOutletContext, useParams } from 'react-router'
import { toast } from 'sonner'
import Feature from '../Feature'
import NotFound from '../pages/NotFound'
import { AdminRequestError } from './api'
import type { AdminContext } from './AdminLayout'
import { readableDate } from './format'
import {
  assignToMe, getRequest, saveNotes, sendReply, setRequestStatus, supportStatuses, supportStatusLabels,
  type RequestDetail, type SupportStatus,
} from './supportApi'
import './support.css'

const REPLY_LIMIT = 2000

export default function SupportRequest() {
  const { requestId = '' } = useParams()
  return <Feature name="support_tools" fallback={<NotFound />}><RequestPage key={requestId} requestId={requestId} /></Feature>
}

function RequestPage({ requestId }: { requestId: string }) {
  const { leave } = useOutletContext<AdminContext>()
  const id = /^[1-9][0-9]{0,9}$/.test(requestId) ? Number(requestId) : null
  const [request, setRequest] = useState<RequestDetail | null>(null)
  const [problem, setProblem] = useState(id === null ? 'Support request not found.' : '')
  const [reply, setReply] = useState('')
  const [notes, setNotes] = useState('')
  const [status, setStatus] = useState<SupportStatus>('open')
  const [busy, setBusy] = useState(false)

  function show(found: RequestDetail) {
    setRequest(found)
    setReply(found.reply ?? '')
    setNotes(found.notes ?? '')
    setStatus(found.status)
  }

  useEffect(() => {
    if (id === null) return
    let active = true
    getRequest(id).then(found => {
      if (!active) return
      setRequest(found); setReply(found.reply ?? ''); setNotes(found.notes ?? ''); setStatus(found.status); setProblem('')
    }).catch(error => {
      if (!active) return
      if (error instanceof AdminRequestError && error.status === 403) leave()
      else setProblem(error instanceof Error ? error.message : 'Could not load this request.')
    })
    return () => { active = false }
  }, [id, leave])

  async function act(action: () => Promise<RequestDetail>, done: string) {
    if (busy) return
    setBusy(true)
    try {
      show(await action())
      toast.success(done)
    } catch (error) {
      if (error instanceof AdminRequestError && error.status === 403) { leave(); return }
      toast.error(error instanceof Error ? error.message : 'Something went wrong. Please try again.')
    } finally {
      setBusy(false)
    }
  }

  const back = <Link to="/admin/support" className="admin-back">← Support requests</Link>
  if (problem) return <div className="admin-page">{back}<p className="admin-error" role="alert">{problem}</p></div>
  if (!request || id === null) return <div className="admin-page">{back}<p className="admin-muted" role="status">Loading the request…</p></div>

  const submitReply = (event: FormEvent) => {
    event.preventDefault()
    if (reply.trim()) void act(() => sendReply(id, reply), 'Reply sent. The traveler is notified.')
  }
  const submitNotes = (event: FormEvent) => {
    event.preventDefault()
    void act(() => saveNotes(id, notes), 'Notes saved.')
  }

  return <div className="admin-page">
    {back}
    <h1>Help request from {request.traveler_name}</h1>
    <section className="admin-panel" aria-labelledby="request-heading">
      <h2 id="request-heading">Message</h2>
      <p className="support-message">{request.message}</p>
      <dl className="admin-details">
        <div><dt>Status</dt><dd>{supportStatusLabels[request.status]}</dd></div>
        <div><dt>Assignee</dt><dd>{request.assigned_to_me ? 'You' : request.assignee_name ?? 'Nobody yet'}</dd></div>
        <div><dt>From</dt><dd>{request.source === 'telegram' ? 'Telegram' : 'Website'}</dd></div>
        <div><dt>Opened</dt><dd>{readableDate(request.created_at)}</dd></div>
      </dl>
      {request.application_id && <p>
        {request.access_expires_at
          ? <Link to={`/admin/support/applications/${request.application_id}`}>Open the shared application (until {readableDate(request.access_expires_at)})</Link>
          : 'This request is about an application the traveler has not shared with support. Ask them to share it from the application’s page.'}
      </p>}
      <div className="admin-actions">
        {!request.assigned_to_me && <button type="button" className="button button-secondary" disabled={busy}
          onClick={() => void act(() => assignToMe(id), 'Assigned to you.')}>Assign to me</button>}
        <label className="admin-field">
          <span>Change status</span>
          <select id="support-request-status" value={status} disabled={busy} onChange={event => setStatus(event.target.value as SupportStatus)}>
            {supportStatuses.map(option => <option key={option} value={option}>{supportStatusLabels[option]}</option>)}
          </select>
        </label>
        <button type="button" className="button button-secondary" disabled={busy || status === request.status}
          onClick={() => void act(() => setRequestStatus(id, status), `Marked ${supportStatusLabels[status].toLowerCase()}.`)}>Update status</button>
      </div>
    </section>

    <section className="admin-panel" aria-labelledby="reply-heading">
      <h2 id="reply-heading">Reply to the traveler</h2>
      <p className="admin-muted">The traveler gets a notification and reads the reply on their account page.
        {request.replied_at && ` Last sent ${readableDate(request.replied_at)}.`}</p>
      <form className="support-form" onSubmit={submitReply}>
        <label htmlFor="support-reply" className="visually-hidden">Reply</label>
        <textarea id="support-reply" maxLength={REPLY_LIMIT} value={reply} disabled={busy} onChange={event => setReply(event.target.value)} />
        <div className="admin-actions"><button type="submit" className="button button-primary" disabled={busy || !reply.trim()}>Send reply</button></div>
      </form>
    </section>

    <section className="admin-panel" aria-labelledby="notes-heading">
      <h2 id="notes-heading">Staff notes</h2>
      <p className="admin-muted">Not shown on the traveler’s pages. Their data download includes them, so keep them factual.</p>
      <form className="support-form" onSubmit={submitNotes}>
        <label htmlFor="support-notes" className="visually-hidden">Staff notes</label>
        <textarea id="support-notes" maxLength={REPLY_LIMIT} value={notes} disabled={busy} onChange={event => setNotes(event.target.value)} />
        <div className="admin-actions"><button type="submit" className="button button-secondary" disabled={busy}>Save notes</button></div>
      </form>
    </section>
  </div>
}
