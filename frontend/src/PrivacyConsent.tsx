import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { toast } from 'sonner'
import { acceptPolicy, CONSENT_REQUIRED_EVENT, loadConsent } from './consent'
import './privacyConsent.css'

// The same points as "The short version" in public/privacy.html.
const SUMMARY = [
  'We use your account, passport and travel details only to help you prepare your visa application.',
  'Your passport and documents are private. Only you can open them, plus authorized staff when they need to fix a problem, and every opening is recorded.',
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

  useEffect(() => { if (open) heading.current?.focus() }, [open])

  async function accept() {
    if (!version) return
    setSaving(true)
    setError('')
    try {
      await acceptPolicy(version)
      setOpen(false)
      toast.success('Thank you. You can now upload your passport and documents.')
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : 'Could not save your answer.')
    } finally {
      setSaving(false)
    }
  }

  function later() {
    setOpen(false)
    setError('')
  }

  function closeOnEscape(event: KeyboardEvent) {
    if (event.key === 'Escape' && !saving) later()
  }

  if (!open) return null
  return (
    <div className="consent-backdrop" onKeyDown={closeOnEscape}>
      <section className="consent-dialog" role="dialog" aria-modal="true"
        aria-labelledby="consent-title" aria-describedby="consent-intro">
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
          <button type="button" className="consent-later" onClick={later} disabled={saving}
            data-activity-id="privacy-later-button">
            Not now
          </button>
        </div>
        <p className="consent-note">If you choose Not now, you can keep using Taasheera, but uploads stay locked until you accept.</p>
      </section>
    </div>
  )
}
