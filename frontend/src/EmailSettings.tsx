import { useEffect, useRef, useState } from 'react'
import { protectedFetch } from './auth'

// Which notification emails to get (CMPS271-99). The bell in the app always shows everything;
// these switches only stop the email copies. Each change is saved straight away.
type Preferences = { application_updates: boolean; reminders: boolean; passport_expiry: boolean }

const SWITCHES: { key: keyof Preferences; label: string; hint: string }[] = [
  { key: 'application_updates', label: 'Application updates',
    hint: 'When an application changes status, or the agent finishes or stops filling a form.' },
  { key: 'reminders', label: 'Reminders about unfinished applications',
    hint: 'When an application has been waiting for a week without changes.' },
  { key: 'passport_expiry', label: 'Passport expiry reminders',
    hint: 'Six months and one month before your saved passport expires.' },
]

export default function EmailSettings() {
  const [preferences, setPreferences] = useState<Preferences | null>(null)
  const [loadError, setLoadError] = useState('')
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const saved = useRef<Preferences | null>(null)

  useEffect(() => {
    let active = true
    protectedFetch('/profile/notification-preferences', { silent: true, cache: 'no-store' })
      .then(async response => {
        if (!response.ok) throw new Error()
        const loaded: Preferences = await response.json()
        if (!active) return
        saved.current = loaded
        setPreferences(loaded)
      })
      .catch(() => { if (active) setLoadError('We could not load your email settings. Reload the page to try again.') })
    return () => { active = false }
  }, [])

  async function change(key: keyof Preferences, on: boolean) {
    if (!preferences || saving) return
    const next = { ...preferences, [key]: on }
    setPreferences(next)
    setSaving(true); setMessage(''); setError('')
    try {
      const response = await protectedFetch('/profile/notification-preferences', {
        method: 'PUT', silent: true, cache: 'no-store',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(next),
      })
      if (!response.ok) throw new Error()
      const stored: Preferences = await response.json()
      saved.current = stored
      setPreferences(stored)
      setMessage('Saved.')
    } catch {
      // Show the switches as they really are.
      setPreferences(saved.current)
      setError('We could not save that change. Please try again.')
    } finally {
      setSaving(false)
    }
  }

  return <section className="profile-section" aria-labelledby="email-settings-heading">
    <h2 id="email-settings-heading">Email notifications</h2>
    <p>Choose which emails we send you. You still see every notification under the bell in the app.</p>
    {loadError && <p role="alert">{loadError}</p>}
    {preferences && <fieldset className="email-settings" disabled={saving}>
      <legend className="visually-hidden">Emails to receive</legend>
      {SWITCHES.map(({ key, label, hint }) => (
        <label key={key} className="email-setting">
          <input type="checkbox" checked={preferences[key]} aria-describedby={`${key}-hint`}
            data-activity-id={`email-setting-${key}`}
            onChange={event => void change(key, event.target.checked)} />
          <span>
            <strong>{label}</strong>
            <span id={`${key}-hint`} className="email-setting-hint">{hint}</span>
          </span>
        </label>
      ))}
    </fieldset>}
    {message && <p role="status">{message}</p>}
    {error && <p role="alert">{error}</p>}
  </section>
}
