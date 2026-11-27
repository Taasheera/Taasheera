import { useId, useState, type FormEvent } from 'react'
import { askForHelp, MESSAGE_LIMIT } from './api'
import './support.css'

export default function TalkToPerson({ applicationId, onClose }: { applicationId?: string; onClose: () => void }) {
  const id = useId()
  const [message, setMessage] = useState('')
  const [share, setShare] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [sent, setSent] = useState(false)

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (busy || !message.trim()) return
    setBusy(true)
    setError('')
    try {
      await askForHelp(message.trim(), applicationId, share ? 72 : undefined)
      setSent(true)
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : 'Could not send your message. Please try again.')
    } finally {
      setBusy(false)
    }
  }

  if (sent) return <div className="talk-to-person" role="status">
    <p><strong>Your message is with our support team.</strong></p>
    <p>We will notify you when someone replies. Follow it under Help requests on your <a href="/account#support-requests">account page</a>.</p>
    <button type="button" className="button button-secondary" onClick={onClose}>Back to the assistant</button>
  </div>

  return <form className="talk-to-person" onSubmit={submit} aria-labelledby={`${id}-title`}>
    <h3 id={`${id}-title`}>Talk to a person</h3>
    <p>Tell our support team what you need. Your conversation with the assistant is not shared with them.</p>
    <label htmlFor={`${id}-message`} className="visually-hidden">Your message to support</label>
    <textarea id={`${id}-message`} rows={5} maxLength={MESSAGE_LIMIT} value={message} disabled={busy}
      placeholder="What do you need help with?" onChange={event => setMessage(event.target.value)} />
    {applicationId && <label className="talk-share">
      <input type="checkbox" checked={share} disabled={busy} onChange={event => setShare(event.target.checked)} />
      <span>Let support read this application for 72 hours: answers, checks and timeline only, never your passport photo or documents. You can stop it at any time.</span>
    </label>}
    {error && <p className="assistant-error" role="alert">{error}</p>}
    <div className="talk-actions">
      <button type="button" className="button button-secondary" disabled={busy} onClick={onClose}>Cancel</button>
      <button type="submit" className="button button-primary" disabled={busy || !message.trim()}>{busy ? 'Sending…' : 'Send to support'}</button>
    </div>
  </form>
}
