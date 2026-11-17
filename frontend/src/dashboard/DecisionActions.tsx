import { useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'
import { recordDecision, type ApplicationSummary, type RecordedStatus, type Status } from '../application/api'
import { applicationName } from './helpers'

type Target = RecordedStatus | 'ANSWERS_COMPLETE'
type Props = { application: ApplicationSummary; onChange: (application: ApplicationSummary) => void }

const done: Record<Target, (name: string) => string> = {
  SUBMITTED: name => `Recorded: ${name} submitted to the embassy.`,
  APPROVED: name => `Recorded: ${name} approved.`,
  REFUSED: name => `Recorded: ${name} refused.`,
  ANSWERS_COMPLETE: name => `${name} is no longer recorded as submitted.`,
}

// The traveler records handing the application to the embassy, then the embassy's decision (US 55).
// Taasheera never submits anything itself. Submitting locks the answers, so it asks first; the
// rest can be undone from the toast or taken back one step with the buttons here.
export default function DecisionActions({ application, onChange }: Props) {
  const [busy, setBusy] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const confirmButton = useRef<HTMLButtonElement>(null)
  const submitButton = useRef<HTMLButtonElement>(null)
  const name = applicationName(application)
  useEffect(() => { if (confirming) confirmButton.current?.focus() }, [confirming])

  if (application.archived_at !== null) return null
  const status = application.status
  if (status === 'DRAFT' || status === 'INELIGIBLE') return null

  async function record(target: Target, from: Status, undo = false) {
    setBusy(true)
    try {
      const updated = await recordDecision(application.id, target)
      setConfirming(false)
      onChange(updated)
      const message = done[target](name)
      // One toast per application: a newer change replaces it, so an older Undo can't act on a stale status.
      const id = `decision-${application.id}`
      if (undo) toast.success(message, { id, action: undefined })
      else toast.success(message, { id, action: { label: 'Undo', onClick: () => void record(from as Target, target, true) } })
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Something went wrong. Please try again.')
    } finally {
      setBusy(false)
    }
  }

  const small = (label: string, target: Target, activity: string, extra = 'button-secondary') =>
    <button type="button" className={`button ${extra} dash-small`} disabled={busy}
      data-activity-id={activity} onClick={() => void record(target, status)}>{label}</button>

  const cancel = () => {
    setConfirming(false)
    // After the render that brings the button back.
    setTimeout(() => submitButton.current?.focus())
  }

  if (status === 'ANSWERS_COMPLETE') {
    return <div className="dash-decision">
      {!confirming && <button ref={submitButton} type="button" className="button button-secondary dash-small" disabled={busy}
        data-activity-id="application-decision" onClick={() => setConfirming(true)}>I've submitted it</button>}
      {confirming && <div className="dash-confirm" role="alertdialog" aria-labelledby="dash-submit-question"
        onKeyDown={event => { if (event.key === 'Escape' && !busy) cancel() }}>
        <p id="dash-submit-question">Record that you handed {name} to the embassy yourself? Its answers are then
          locked, so this record matches what you sent. Taasheera does not submit anything for you.</p>
        <div className="dash-actions">
          <button ref={confirmButton} type="button" className="button button-primary dash-small" disabled={busy}
            aria-busy={busy} data-activity-id="application-decision-confirm"
            onClick={() => void record('SUBMITTED', status)}>{busy ? 'Recording…' : 'Yes, record it'}</button>
          <button type="button" className="button button-secondary dash-small" disabled={busy}
            data-activity-id="application-decision-cancel" onClick={cancel}>Cancel</button>
        </div>
      </div>}
    </div>
  }

  if (status === 'SUBMITTED') {
    return <div className="dash-decision" role="group" aria-label="The embassy's decision">
      {small('Visa approved', 'APPROVED', 'application-decision', 'button-primary')}
      {small('Visa refused', 'REFUSED', 'application-decision')}
      {small('Not submitted yet', 'ANSWERS_COMPLETE', 'application-decision')}
    </div>
  }

  return <div className="dash-decision" role="group" aria-label="The embassy's decision">
    {small(status === 'APPROVED' ? 'It was refused' : 'It was approved', status === 'APPROVED' ? 'REFUSED' : 'APPROVED',
      'application-decision')}
    {small('No decision yet', 'SUBMITTED', 'application-decision')}
  </div>
}
