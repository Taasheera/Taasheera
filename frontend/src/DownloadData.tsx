import { useRef, useState } from 'react'
import { protectedFetch } from './auth'

// Download all personal data (US 87): one ZIP of everything the service holds about this traveler.
export default function DownloadData() {
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const pending = useRef(false)

  async function download() {
    if (pending.current) return
    pending.current = true
    setBusy(true); setMessage(''); setError('')
    try {
      const response = await protectedFetch('/auth/account/export', { cache: 'no-store', silent: true })
      if (response.status === 429) throw new Error('You have downloaded your data several times recently. Please try again later.')
      if (!response.ok) throw new Error('We could not prepare your download. Please try again.')
      const name = /filename="([^"]+)"/.exec(response.headers.get('Content-Disposition') ?? '')?.[1] ?? 'taasheera-data.zip'
      const url = URL.createObjectURL(await response.blob())
      const link = document.createElement('a')
      link.href = url
      link.download = name
      document.body.append(link)
      link.click()
      link.remove()
      // Give the browser a moment to start the download before freeing the file.
      setTimeout(() => URL.revokeObjectURL(url), 10_000)
      setMessage(`Your download has started: ${name}.`)
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : 'We could not prepare your download. Please try again.')
    } finally {
      pending.current = false
      setBusy(false)
    }
  }

  return <section className="profile-section" aria-labelledby="download-data-heading">
    <h2 id="download-data-heading">Download your data</h2>
    <p>Get one ZIP file with everything we hold about you: your account and profile, passport photos and
      confirmed details, applications and answers, assistant conversations, notifications and account activity.
      Your password is never included.</p>
    <button type="button" className="google-retry" disabled={busy} onClick={() => void download()}>
      {busy ? 'Preparing your download…' : 'Download my data'}</button>
    {message && <p role="status">{message}</p>}
    {error && <p role="alert">{error}</p>}
  </section>
}
