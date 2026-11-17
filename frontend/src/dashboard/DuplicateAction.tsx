import { useEffect, useId, useRef, useState } from 'react'
import { useNavigate } from 'react-router'
import { toast } from 'sonner'
import { duplicateApplication, getDuplicateChoices, type ApplicationSummary, type DuplicateChoice } from '../application/api'
import { applicationLabel, applicationName } from './helpers'

type Props = {
  application: ApplicationSummary
  disabled?: boolean
  // Open or copying: the other actions wait, so the original can't be deleted mid-copy.
  onActiveChange?: (active: boolean) => void
}

// A new application for another country with the answers that still apply (US 78). The server
// works out which answers carry over; the original is not changed. Opens the copy in the chat.
export default function DuplicateAction({ application, disabled = false, onActiveChange }: Props) {
  const navigate = useNavigate()
  const id = useId()
  // null while closed; 'loading' while the choices load.
  const [choices, setChoices] = useState<DuplicateChoice[] | 'loading' | null>(null)
  const [visa, setVisa] = useState(0)
  const [place, setPlace] = useState('')
  const [busy, setBusy] = useState(false)
  const openButton = useRef<HTMLButtonElement>(null)
  const firstField = useRef<HTMLSelectElement>(null)
  const cancelButton = useRef<HTMLButtonElement>(null)
  const label = applicationLabel(application)
  // The first field, or Cancel when there is nothing to choose, so focus never drops to the page.
  useEffect(() => { if (Array.isArray(choices)) (firstField.current ?? cancelButton.current)?.focus() }, [choices])
  useEffect(() => { onActiveChange?.(choices !== null || busy) }, [choices, busy, onActiveChange])

  async function open() {
    setChoices('loading')
    try {
      const found = await getDuplicateChoices(application.id)
      setVisa(0)
      setPlace('')
      setChoices(found)
    } catch (error) {
      setChoices(null)
      toast.error(error instanceof Error ? error.message : 'Something went wrong. Please try again.')
    }
  }
  const close = () => {
    setChoices(null)
    setTimeout(() => openButton.current?.focus())
  }

  async function create(choice: DuplicateChoice) {
    setBusy(true)
    try {
      const copy = await duplicateApplication(application.id, choice, choice.place_prompt ? place : null)
      toast.success(`Copied ${applicationName(application)} to ${applicationName(copy)}. Check the answers that carried over.`)
      navigate(`/apply/${copy.id}`)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Something went wrong. Please try again.')
      setBusy(false)
    }
  }

  if (!Array.isArray(choices)) {
    return <button ref={openButton} type="button" className="button button-secondary dash-small"
      disabled={disabled || choices === 'loading'} aria-busy={choices === 'loading'}
      data-activity-id="application-duplicate" aria-label={`Duplicate ${label} for another country`}
      onClick={() => void open()}>Duplicate</button>
  }

  const choice = choices[visa]
  const ready = choice && (!choice.place_prompt || place !== '')
  return <form className="dash-confirm dash-duplicate" aria-labelledby={`${id}-title`}
    onKeyDown={event => { if (event.key === 'Escape' && !busy) close() }}
    onSubmit={event => { event.preventDefault(); if (ready) void create(choice) }}>
    <p id={`${id}-title`}>Copy {label} to a new application for another country. Answers that still apply carry
      over; the original stays as it is.</p>
    {choices.length === 0 && <p role="status">There's no other country this application can be copied to yet.</p>}
    {choices.length > 1 && <label className="dash-field">
      <span>Visa</span>
      <select ref={firstField} value={visa} disabled={busy} onChange={event => { setVisa(Number(event.target.value)); setPlace('') }}>
        {choices.map((option, index) => <option key={`${option.destination}-${option.visa_type}`} value={index}>{option.title}</option>)}
      </select>
    </label>}
    {choice?.place_prompt && <label className="dash-field">
      <span>{choice.place_prompt}</span>
      <select ref={choices.length > 1 ? undefined : firstField} value={place} disabled={busy} required
        onChange={event => setPlace(event.target.value)}>
        <option value="" disabled>Choose a country</option>
        {choice.places.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
      </select>
    </label>}
    <div className="dash-actions">
      {choice && <button type="submit" className="button button-primary dash-small" disabled={busy || !ready} aria-busy={busy}
        data-activity-id="application-duplicate-confirm">{busy ? 'Copying…' : 'Create copy'}</button>}
      <button ref={cancelButton} type="button" className="button button-secondary dash-small" disabled={busy}
        data-activity-id="application-duplicate-cancel" onClick={close}>Cancel</button>
    </div>
  </form>
}
