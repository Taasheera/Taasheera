import { useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'
import { acceptPolicy, CONSENT_REQUIRED_EVENT, loadConsent } from './consent'
import './privacyConsent.css'

// The same points as "The short version" in public/privacy.html.
const SUMMARY = [
  'We use your account, passport and travel details only to help you prepare your visa application.',
  'Your passport and documents are private. The app checks that you own them and records access to passport files.',
  'If you use the AI assistant, your question and the relevant details of your application are sent to Google Gemini.',
  'We never send an application to an embassy without your confirmation.',
  'You can download all your data or delete your account at any time from your account page.',
]

// Asks a signed-in traveler to accept the current privacy policy (CMPS271-115): on their first visit after
// signing in, again when the policy changes, and whenever an upload is refused because they haven't.
// "Not now" closes it; uploads stay locked on the server until they accept.
export default function PrivacyConsent() {
  const [version, setVersion] = useState<string | null>(null)
  const [open, setOpen] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const dialog = useRef<HTMLDialogElement>(null)
  const heading = useRef<HTMLHeadingElement>(null)

  useEffect(() => {
    let active = true
    const check = () => loadConsent().then(status => {
      if (!active) return
      setVersion(status.policy_version)
      setOpen(!status.accepted)
      setError('')
    }, () => {
      // Unknown status: say nothing now. The server still refuses uploads, which asks again.
    })
    check()
    window.addEventListener(CONSENT_REQUIRED_EVENT, check)
    return () => {
      active = false
      window.removeEventListener(CONSENT_REQUIRED_EVENT, check)
    }
  }, [])

  useEffect(() => {
    const modal = dialog.current
    if (!open || !modal) return
    modal.showModal()
    heading.current?.focus()
    return () => { modal.close() }
  }, [open])

  function dismiss() {
    dialog.current?.close()
    setOpen(false)
    setError('')
  }

  async function accept() {
    if (!version) return
    setSaving(true)
    setError('')
    try {
      await acceptPolicy(version)
      dismiss()
      toast.success('Thank you. You can now upload your passport and documents.')
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : 'Could not save your answer.')
    } finally {
      setSaving(false)
    }
  }

  if (!open) return null
  return (
    <dialog className="consent-dialog" ref={dialog} aria-labelledby="consent-title" aria-describedby="consent-intro"
      onCancel={event => {
        event.preventDefault()
        if (!saving) dismiss()
      }}>
      <h2 id="consent-title" ref={heading} tabIndex={-1}>Before you upload documents</h2>
      <p id="consent-intro">
        Please read how we handle your passport and travel details. You need to accept this before
        uploading a passport or any other document.
      </p>
      <ul className="consent-summary">
        {SUMMARY.map(point => <li key={point}>{point}</li>)}
      </ul>
      <p className="consent-demo">This is a student project: use specimen or test documents, never your real passport.</p>
      <p>
        <a href="/privacy.html" target="_blank" rel="noopener" data-activity-id="privacy-policy-link">
          Read the full privacy policy</a> (opens in a new tab)
      </p>
      {error && <p className="consent-error" role="alert">{error}</p>}
      <div className="consent-actions">
        <button type="button" className="consent-accept" onClick={accept} disabled={saving}
          data-activity-id="privacy-accept-button">
          {saving ? 'Saving…' : 'I accept'}
        </button>
        <button type="button" className="consent-later" onClick={dismiss} disabled={saving}
          data-activity-id="privacy-later-button">
          Not now
        </button>
      </div>
      <p className="consent-note">If you choose Not now, you can keep using Taasheera, but uploads stay locked until you accept.</p>
    </dialog>
  )
}
