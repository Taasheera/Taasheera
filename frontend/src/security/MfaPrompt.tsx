import { useEffect, useRef, useState } from 'react'
import { cancelMfa, finishMfa, type Traveler } from '../auth'

export default function MfaPrompt({ challengeToken, onSignedIn, onCancel }: {
  challengeToken: string; onSignedIn: (traveler: Traveler) => void; onCancel: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const pending = useRef(false)
  const mounted = useRef(false)
  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])
  return <section className="sign-in-card" aria-labelledby="mfa-signin-heading">
    <h1 id="mfa-signin-heading">Two-factor sign-in</h1>
    <p>Enter a new six-digit code from your authenticator app or one unused backup code.
      This sign-in check expires after five minutes.</p>
    <form aria-busy={busy} onSubmit={async event => {
      event.preventDefault()
      if (pending.current) return
      const form = event.currentTarget
      const code = String(new FormData(form).get('code') ?? '')
      pending.current = true; setBusy(true); setMessage('')
      try {
        const traveler = await finishMfa(challengeToken, code)
        if (mounted.current) onSignedIn(traveler)
      } catch (error) {
        if (mounted.current) setMessage(error instanceof Error ? error.message : 'Could not verify the code.')
      } finally {
        form.reset(); pending.current = false
        if (mounted.current) setBusy(false)
      }
    }}>
      <label htmlFor="mfa-signin-code">Authenticator or backup code</label>
      <input id="mfa-signin-code" name="code" autoComplete="one-time-code" autoCapitalize="characters"
        spellCheck={false} minLength={6} maxLength={32} required disabled={busy} />
      <button type="submit" className="primary-button" disabled={busy}>{busy ? 'Checking code…' : 'Verify and sign in'}</button>
    </form>
    <button type="button" className="google-retry" disabled={busy} onClick={() => {
      void cancelMfa(challengeToken); onCancel()
    }}>Cancel sign-in</button>
    <p role="status" className="auth-status">{message}</p>
  </section>
}
