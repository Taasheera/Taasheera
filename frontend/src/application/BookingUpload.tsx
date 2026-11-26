import { useRef, useState, type ChangeEvent } from 'react'
import { checkDocumentFile, DOCUMENT_TYPES, uploadDocument } from '../documents/api'
import type { UploadHint } from './api'

type Props = {
  applicationId: string
  hint: UploadHint
  // Reloads the application; true when the server now suggests an answer from the upload.
  onUploaded: () => Promise<boolean>
}

// Fill from your documents: a question the traveler's own booking can answer (trip dates, the hotel)
// offers to read it from the booking instead. The file is saved with the application's documents, and
// the answer it gives is still shown for confirming, like one from the passport or profile.
export default function BookingUpload({ applicationId, hint, onUploaded }: Props) {
  const input = useRef<HTMLInputElement>(null)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const name = hint.label.toLowerCase()

  async function choose(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    const problem = checkDocumentFile(file)
    if (problem) {
      setMessage(problem)
      return
    }
    setBusy(true)
    setMessage('')
    try {
      await uploadDocument(applicationId, hint.requirement, file)
      if (!await onUploaded()) {
        setMessage("Saved with your documents, but we couldn't read this answer from it. Type it below.")
      }
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Could not upload that file. Please try again.')
    } finally {
      setBusy(false)
    }
  }

  return <div className="booking-upload">
    <p><strong>Skip the typing.</strong> Upload your {name} and we'll read this from it. You check it before it's saved.</p>
    <button type="button" className="button button-secondary dash-small" disabled={busy} aria-busy={busy || undefined}
      onClick={() => input.current?.click()} data-activity-id="application-booking-upload">
      {busy ? 'Reading…' : `Upload ${name}`}
    </button>
    <input ref={input} type="file" accept={DOCUMENT_TYPES.join(',')} hidden aria-label={`Upload ${name}`}
      onChange={event => void choose(event)} />
    {message && <p className="answer-error" role="status">{message}</p>}
  </div>
}
