import { useCallback, useEffect, useRef, useState } from 'react'
import type { Traveler } from '../auth'
import GoogleSignIn from '../GoogleSignIn'
import { useFeature } from '../features'
import { disableMfa, enableMfa, mfaStatus, replaceBackupCodes, setupMfa, type Enrollment, type MfaStatus } from './api'

export default function MfaSettings({ traveler, googleClientId }: { traveler: Traveler; googleClientId: string }) {
  const enrollmentOn = useFeature('two_factor_signin')
  const [status, setStatus] = useState<MfaStatus | null>(null)
  const [confirming, setConfirming] = useState(false)
  const [enrollment, setEnrollment] = useState<Enrollment | null>(null)
  const [backups, setBackups] = useState<string[] | null>(null)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const mounted = useRef(false)
  const pending = useRef(false)
  useEffect(() => {
    mounted.current = true
    mfaStatus().then(value => { if (mounted.current) setStatus(value) })
      .catch(() => { if (mounted.current) setMessage('Could not load two-factor settings. Reload to try again.') })
    return () => { mounted.current = false }
  }, [])
  const begin = useCallback(async (proof: { password?: string; google_id_token?: string }) => {
    if (pending.current) return
    pending.current = true; setBusy(true); setMessage('')
    try {
      const setup = await setupMfa({ expected_traveler_id: traveler.id, ...proof })
      if (mounted.current) { setEnrollment(setup); setConfirming(false) }
    } catch (error) { if (mounted.current) setMessage(error instanceof Error ? error.message : 'Could not start setup.') }
    finally { pending.current = false; if (mounted.current) setBusy(false) }
  }, [traveler.id])
  const googleProof = useCallback((credential: string) => { void begin({ google_id_token: credential }) }, [begin])
  // Turning off rollout never hides recovery controls from an enrolled account.
  if (!enrollmentOn && !status?.enabled) return null
  return <section className="profile-section" aria-labelledby="mfa-settings-heading">
    <h2 id="mfa-settings-heading">Two-factor sign-in</h2>
    <p>{status === null ? 'Loading security settings…' : status.enabled ? 'On — an authenticator or backup code is required after your sign-in method.'
      : 'Off by default. Add an authenticator-app code to protect password and Google sign-in.'}</p>
    {status && !status.enabled && !enrollment && !confirming && <button type="button" className="google-retry"
      data-activity-id="mfa-setup" onClick={() => { setConfirming(true); setMessage('') }}>Set up authenticator</button>}
    {confirming && <>
      <p>Confirm your existing sign-in method to start setup.</p>
      {traveler.password_enabled ? <form onSubmit={async event => {
        event.preventDefault()
        const form = event.currentTarget
        try { await begin({ password: String(new FormData(form).get('password') ?? '') }) }
        finally { form.reset() }
      }}>
        <label htmlFor="mfa-settings-password">Confirm account password</label>
        <input id="mfa-settings-password" name="password" type="password" autoComplete="current-password" disabled={busy} required />
        <button type="submit" className="primary-button" disabled={busy}>Confirm password and get QR code</button>
      </form> : <GoogleSignIn clientId={googleClientId} disabled={busy} onCredential={googleProof} onMessage={setMessage} />}
      <button type="button" className="google-retry" disabled={busy} onClick={() => setConfirming(false)}>Cancel setup</button>
    </>}
    {enrollment && <>
      <p>Scan this QR code in your authenticator app, or enter the setup key manually. Setup expires in ten minutes.</p>
      <img src={enrollment.qr_data_url} alt="Authenticator setup QR code" width="200" height="200" style={{ maxWidth: '100%', height: 'auto' }} />
      <p>Setup key: <code style={{ overflowWrap: 'anywhere' }}>{enrollment.secret}</code></p>
      <form onSubmit={async event => {
        event.preventDefault()
        if (pending.current) return
        const form = event.currentTarget
        const code = String(new FormData(form).get('code') ?? '')
        pending.current = true; setBusy(true); setMessage('')
        try {
          const value = await enableMfa(traveler.id, code)
          if (mounted.current) {
            setStatus({ enabled: true, backup_codes_remaining: value.backup_codes.length })
            setBackups(value.backup_codes); setEnrollment(null)
            setMessage('Two-factor sign-in is enabled. Other devices have been signed out.')
          }
        } catch (error) { if (mounted.current) setMessage(error instanceof Error ? error.message : 'Could not enable two-factor sign-in.') }
        finally { form.reset(); pending.current = false; if (mounted.current) setBusy(false) }
      }}>
        <label htmlFor="mfa-enable-code">Code from authenticator app</label>
        <input id="mfa-enable-code" name="code" type="text" inputMode="numeric" autoComplete="one-time-code"
          pattern="[0-9]{6}" minLength={6} maxLength={6} disabled={busy} required />
        <button type="submit" className="primary-button" disabled={busy} data-activity-id="mfa-enable">Enable two-factor sign-in</button>
      </form>
      <button type="button" className="google-retry" disabled={busy} onClick={() => { setEnrollment(null); setConfirming(false) }}>Cancel setup</button>
    </>}
    {backups && <section aria-label="Your backup codes">
      <h3>Save your backup codes</h3>
      <p>These are shown once. Store them privately outside this browser. Each code works once if your authenticator is unavailable.</p>
      <ul>{backups.map(code => <li key={code}><code>{code}</code></li>)}</ul>
      <button type="button" className="google-retry" onClick={() => setBackups(null)}>I saved my backup codes</button>
    </section>}
    {status?.enabled && <>
      <p>{status.backup_codes_remaining} unused backup codes.</p>
      <form onSubmit={async event => {
        event.preventDefault()
        if (pending.current) return
        const form = event.currentTarget
        const code = String(new FormData(form).get('code') ?? '')
        const action = (event.nativeEvent as SubmitEvent).submitter?.getAttribute('value') ?? 'disable'
        pending.current = true; setBusy(true); setMessage('')
        try {
          if (action === 'backup') {
            const result = await replaceBackupCodes(traveler.id, code)
            if (mounted.current) { setBackups(result.backup_codes); setStatus({ enabled: true, backup_codes_remaining: result.backup_codes.length }); setMessage('New backup codes replace all previous codes.') }
          } else {
            await disableMfa(traveler.id, code)
            if (mounted.current) { setStatus({ enabled: false, backup_codes_remaining: 0 }); setBackups(null); setMessage('Two-factor sign-in is off.') }
          }
        } catch (error) { if (mounted.current) setMessage(error instanceof Error ? error.message : 'Could not change two-factor sign-in.') }
        finally { form.reset(); pending.current = false; if (mounted.current) setBusy(false) }
      }}>
        <label htmlFor="mfa-manage-code">Current authenticator or unused backup code</label>
        <input id="mfa-manage-code" name="code" autoComplete="one-time-code" spellCheck={false} maxLength={32} minLength={6} disabled={busy} required />
        <p>A code cannot be reused. Wait for a new authenticator code after enabling or signing in.</p>
        <button type="submit" name="action" value="backup" className="google-retry" disabled={busy} data-activity-id="mfa-backup-regenerate">Generate new backup codes</button>{' '}
        <button type="submit" name="action" value="disable" className="google-retry" disabled={busy} data-activity-id="mfa-disable">Turn off two-factor sign-in</button>
      </form>
    </>}
    <p role="status" className="auth-status">{message}</p>
  </section>
}
