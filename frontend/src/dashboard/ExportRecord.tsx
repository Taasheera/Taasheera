import { useState } from 'react'
import { toast } from 'sonner'
import { exportRecord, type ApplicationSummary } from '../application/api'
import { applicationLabel } from './helpers'

// Downloads the application's answers, status and timeline as a file (US 80). Fetched with the
// signed-in session, then saved from memory, so the file never sits at a URL anyone else could open.
export default function ExportRecord({ application }: { application: ApplicationSummary }) {
  const [busy, setBusy] = useState(false)

  async function download() {
    setBusy(true)
    try {
      const { file, filename } = await exportRecord(application.id)
      const url = URL.createObjectURL(file)
      const link = document.createElement('a')
      link.href = url
      link.download = filename
      document.body.append(link)
      link.click()
      link.remove()
      // After the browser has started saving it.
      setTimeout(() => URL.revokeObjectURL(url), 1000)
      toast.success(`Downloaded ${filename}. It holds your passport details, so keep it somewhere private.`)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Something went wrong. Please try again.')
    } finally {
      setBusy(false)
    }
  }

  return <button type="button" className="button button-secondary dash-small" disabled={busy} aria-busy={busy}
    data-activity-id="application-export" aria-label={`Download the record of ${applicationLabel(application)}`}
    onClick={() => void download()}>{busy ? 'Preparing…' : 'Download record'}</button>
}
