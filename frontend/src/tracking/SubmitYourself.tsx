import { useEffect, useState } from 'react'
import { toast } from 'sonner'
import { getSubmission, removeAppointment, saveAppointment, watchSlots, type Submission } from './api'
import './tracking.css'

// For "local" <input type="datetime-local"> values: the traveler's own time zone, to the minute.
function localValue(iso: string | null) {
  if (!iso) return ''
  const date = new Date(iso)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`
}

const formatWhen = (iso: string) => new Date(iso).toLocaleString(undefined, {
  weekday: 'long', day: 'numeric', month: 'long', year: 'numeric', hour: '2-digit', minute: '2-digit',
})

const STATUS = { ready: 'Uploaded', missing: 'Not uploaded yet', bring: 'Bring' } as const

// How to hand the application in yourself, the appointment with its reminders, and what to bring
// (CMPS271-72/73/79). The stage at the top moves on by itself as things happen (CMPS271-77).
export default function SubmitYourself({ applicationId, version, onChange }:
  { applicationId: string; version: string; onChange?: () => void }) {
  const [data, setData] = useState<Submission | null | Error>(null)
  const [when, setWhen] = useState('')
  const [where, setWhere] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let active = true
    getSubmission(applicationId)
      .then(loaded => {
        if (!active) return
        setData(loaded)
        setWhen(localValue(loaded.appointment.at))
        setWhere(loaded.appointment.location ?? '')
      })
      .catch(problem => { if (active) setData(problem instanceof Error ? problem : new Error('Could not load the submission steps.')) })
    return () => { active = false }
  }, [applicationId, version])

  if (data === null) return <section id="submit-yourself" className="track" aria-labelledby="track-title">
    <h2 id="track-title">Submit it yourself</h2><p className="track-note" role="status">Loading…</p>
  </section>
  if (data instanceof Error) return <section id="submit-yourself" className="track" aria-labelledby="track-title">
    <h2 id="track-title">Submit it yourself</h2><p className="track-note" role="alert">{data.message}</p>
  </section>

  async function save(event: React.FormEvent) {
    event.preventDefault()
    const at = new Date(when)
    if (!when || Number.isNaN(at.getTime())) { toast.error('Choose the date and time of your appointment.'); return }
    setBusy(true)
    try {
      const saved = await saveAppointment(applicationId, at, where)
      setData(saved)
      toast.success("Appointment saved. We'll remind you a week and a day before.")
      onChange?.()
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Could not save the appointment.')
    } finally {
      setBusy(false)
    }
  }

  async function remove() {
    setBusy(true)
    try {
      const saved = await removeAppointment(applicationId)
      setData(saved)
      setWhen('')
      setWhere('')
      toast.success('Appointment removed.')
      onChange?.()
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Could not remove the appointment.')
    } finally {
      setBusy(false)
    }
  }

  async function toggleWatch(watch: boolean) {
    setBusy(true)
    try {
      setData(await watchSlots(applicationId, watch))
      toast.success(watch ? "We'll tell you when an appointment slot opens." : 'Slot alerts are off.')
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Could not change the slot alerts.')
    } finally {
      setBusy(false)
    }
  }

  const { appointment, slots } = data
  return <section id="submit-yourself" className="track" aria-labelledby="track-title">
    <div className="track-head">
      <div>
        <h2 id="track-title">Submit it yourself</h2>
        <p className="track-note">Ta'asheera never submits for you. These are the steps to hand it in, in order.</p>
      </div>
      <p className="track-stage"><span className="track-stage-label">Stage</span> {data.stage}</p>
    </div>

    <ol className="track-steps">
      {data.steps.map((step, index) => <li key={step.id}
        className={`track-step${step.done ? ' track-step-done' : ''}${step.id === data.current ? ' track-step-current' : ''}`}
        aria-current={step.id === data.current ? 'step' : undefined}>
        <span className="track-step-mark" aria-hidden="true">{step.done ? '✓' : index + 1}</span>
        <div>
          <h3>{step.title}{step.done && <span className="visually-hidden"> (done)</span>}</h3>
          <p>{step.text}</p>
          {step.link && <a href={step.link.url} target="_blank" rel="noopener noreferrer">{step.link.label}</a>}
        </div>
      </li>)}
    </ol>

    {appointment.needed && <div className="track-card" aria-labelledby="track-appointment">
      <h3 id="track-appointment">Your appointment</h3>
      {appointment.at
        ? <p>{formatWhen(appointment.at)}{appointment.location && <> · {appointment.location}</>}</p>
        : <p className="track-note">Record it once you've booked, and we'll remind you a week and a day before.</p>}
      {!appointment.at && slots.available && <div className="track-slots">
        <p>{slots.earliest
          ? <>Earliest open slot: <strong>{new Date(`${slots.earliest.day}T00:00:00`).toLocaleDateString(undefined, {
              weekday: 'long', day: 'numeric', month: 'long' })}</strong> · {slots.earliest.centre}</>
          : 'No open slot before your trip right now.'}</p>
        {!data.read_only && <button type="button" className="button button-secondary dash-small" disabled={busy}
          aria-pressed={slots.watching} data-activity-id="slot-watch" onClick={() => void toggleWatch(!slots.watching)}>
          {slots.watching ? 'Stop slot alerts' : 'Tell me when a slot opens'}</button>}
        <p className="track-note">{slots.note}</p>
      </div>}
      {!data.read_only && <form className="track-form" onSubmit={event => void save(event)}>
        <label>
          <span>Date and time</span>
          <input type="datetime-local" value={when} required onChange={event => setWhen(event.target.value)} />
        </label>
        <label>
          <span>Place (optional)</span>
          <input type="text" value={where} maxLength={200} placeholder="For example: VFS Global, Beirut"
            onChange={event => setWhere(event.target.value)} />
        </label>
        <div className="track-actions">
          <button type="submit" className="button button-primary dash-small" disabled={busy}
            data-activity-id="appointment-save">{appointment.at ? 'Update appointment' : 'Save appointment'}</button>
          {appointment.at && <button type="button" className="button button-secondary dash-small" disabled={busy}
            data-activity-id="appointment-remove" onClick={() => void remove()}>Remove</button>}
        </div>
      </form>}
    </div>}

    <div className="track-card track-checklist" aria-labelledby="track-checklist">
      <div className="track-checklist-head">
        <h3 id="track-checklist">Appointment day checklist</h3>
        <button type="button" className="button button-secondary dash-small" data-activity-id="checklist-print"
          onClick={() => window.print()}>Print</button>
      </div>
      <ul>
        {data.checklist.map(item => <li key={item.label} className={`track-item track-item-${item.status}`}>
          <span className="track-box" aria-hidden="true" />
          <div>
            <strong>{item.label}</strong>{item.optional && <span className="track-optional"> (optional)</span>}
            {item.detail && <p>{item.detail}</p>}
          </div>
          <span className="track-status">{STATUS[item.status]}</span>
        </li>)}
      </ul>
    </div>
  </section>
}
