import { ArrowIcon } from '../icons'
import AssistantText from './AssistantText'
import VoiceDraft from './VoiceDraft'
import { useEffect, useId, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'
import { askAssistant, getHistory, type AssistantStep, type ChatTurn, type Proposal } from './api'
import ProposalCard from './ProposalCard'
import { saveAnswer } from '../application/api'
import { APPLICATION_CHANGED_EVENT, OPEN_ASSISTANT_EVENT } from './openAssistant'
import '../ui.css'
import './assistant.css'

const suggestions: Record<AssistantStep, string[]> = {
  passport_upload: ['What kind of photo works best?', 'Which page should I photograph?', 'Is my passport kept private?'],
  passport_review: ['When does my passport expire?', 'What is a check digit?', 'Why is a field marked Confirm?'],
  application: ['Why does the embassy ask this?', 'What if I am not sure yet?'],
  general: ['How does Ta\'asheera prepare my application?', 'When does my passport expire?', 'Is my data private?'],
}
const applicationSuggestions = ['Is my application ready?', 'What should I fix first?', 'What happens after I download the form?']
// Outside the passport page the same panel is the general assistant on every signed-in page (US 23).
const copy = (step: AssistantStep) => step === 'application'
  ? { title: 'Application assistant', intro: 'Ask about this application: what is missing, what a question means, or what happens next.', open: 'Ask the assistant' }
  : step === 'general'
  ? { title: 'Ta\'asheera assistant', intro: 'Ask about your passport, your applications or how this works.', open: 'Ask the assistant' }
  : { title: 'Passport assistant', intro: 'Ask about your passport or this step.', open: 'Ask about your passport' }
const MAX_LENGTH = 1000

// Earlier turns outside an application are kept by the server (US 111) and load when the panel first opens.
type Props = {
  step: AssistantStep
  // Controlled by the page, so other parts of it (e.g. the confirmation message) can open the chat.
  open: boolean
  onOpenChange: (open: boolean) => void
  // On an application's own page the chat is about that application: its answers, checks and history.
  applicationId?: string
}
type Turn = ChatTurn & { proposal?: Proposal | null }


export default function AssistantPanel({ step: pageStep, open, onOpenChange, applicationId }: Props) {
  const step: AssistantStep = applicationId ? 'application' : pageStep
  const [turns, setTurns] = useState<Turn[]>([])
  const [draft, setDraft] = useState('')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')
  const panelId = useId()
  const launcher = useRef<HTMLButtonElement>(null)
  const input = useRef<HTMLTextAreaElement>(null)
  const log = useRef<HTMLDivElement>(null)

  useEffect(() => { if (open) input.current?.focus() }, [open])
  // The navigation panel's Assistant button (US 15) opens this panel, whichever page it is on.
  useEffect(() => {
    const openFromNavigation = () => onOpenChange(true)
    window.addEventListener(OPEN_ASSISTANT_EVENT, openFromNavigation)
    return () => window.removeEventListener(OPEN_ASSISTANT_EVENT, openFromNavigation)
  }, [onOpenChange])
  const loaded = useRef(false)
  useEffect(() => {
    if (!open || loaded.current) return
    loaded.current = true
    getHistory(applicationId).then(earlier => { if (earlier.length) setTurns(current => current.length ? current : earlier) })
      .catch(() => { /* No history is just a fresh chat. */ })
  }, [open, applicationId])
  // Escape closes from anywhere: clicking a suggestion removes it, so focus may have left the panel.
  useEffect(() => {
    if (!open) return
    const onKey = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape') { onOpenChange(false); launcher.current?.focus() }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open, onOpenChange])
  useEffect(() => { log.current?.scrollTo?.({ top: log.current.scrollHeight }) }, [turns, pending])

  function close() {
    onOpenChange(false)
    launcher.current?.focus()
  }

  async function send(text: string) {
    const message = text.trim()
    if (!message || pending) return
    const history = turns.map(({ role, content }) => ({ role, content }))
    setDraft('')
    setError('')
    setPending(true)
    setTurns(current => [...current, { role: 'user', content: message }])
    try {
      const answer = await askAssistant(message, step, history, applicationId)
      setTurns(current => [...current, { role: 'assistant', content: answer.reply, proposal: answer.proposal }])
    } catch (problem) {
      // Undo the unsent question and give it back, so retrying is one keypress.
      setTurns(current => current.slice(0, -1))
      setDraft(message)
      setError(problem instanceof Error ? problem.message : 'The assistant could not answer. Please try again.')
    } finally {
      setPending(false)
      input.current?.focus()
    }
  }

  const submit = (event: FormEvent) => { event.preventDefault(); void send(draft) }
  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault()
      void send(draft)
    }
  }

  return <div className="assistant">
    {open && <section id={panelId} className="assistant-panel" aria-labelledby={`${panelId}-title`}>
      <header className="assistant-header">
        <div>
          <h2 id={`${panelId}-title`}>{copy(step).title}</h2>
          <p>Answers use the details you confirmed. Not legal or immigration advice.</p>
        </div>
        <button type="button" className="assistant-close" onClick={close} aria-label="Close assistant"
          data-activity-id="assistant-close">
          <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3.5 3.5l9 9M12.5 3.5l-9 9" /></svg>
        </button>
      </header>

      <div className="assistant-log" ref={log} aria-live="polite">
        {turns.length === 0 && <div className="assistant-empty">
          <p>{copy(step).intro} For example:</p>
          <ul>
            {(applicationId ? applicationSuggestions : suggestions[step]).map(question => <li key={question}>
              <button type="button" className="assistant-suggestion" onClick={() => void send(question)}
                disabled={pending} data-activity-id="assistant-suggestion">{question}</button>
            </li>)}
          </ul>
        </div>}
        {turns.map((turn, index) => <div key={index} className={`bubble bubble-${turn.role}`}>
          <span className="visually-hidden">{turn.role === 'user' ? 'You said' : 'Assistant said'}: </span>
          {turn.role === 'assistant' ? <AssistantText text={turn.content} /> : turn.content}
          {turn.proposal && applicationId && <ProposalCard proposal={turn.proposal}
            onGoTo={questionId => { window.location.assign(`/apply/${applicationId}#answer-${questionId}`) }}
            onConfirm={async () => {
              await saveAnswer(applicationId, turn.proposal!.question_id!, { value: turn.proposal!.value! })
              window.dispatchEvent(new CustomEvent(APPLICATION_CHANGED_EVENT, { detail: applicationId }))
            }} />}
        </div>)}
        {pending && <div className="bubble bubble-assistant bubble-pending" role="status">
          <span className="typing-dots" aria-hidden="true"><i /><i /><i /></span>
          <span className="visually-hidden">The assistant is answering</span>
        </div>}
      </div>

      <form className="assistant-form" onSubmit={submit}>
        {error && <p className="assistant-error" role="alert">{error}</p>}
        <label htmlFor={`${panelId}-input`} className="visually-hidden">Your question</label>
        <textarea id={`${panelId}-input`} ref={input} rows={2} maxLength={MAX_LENGTH} value={draft}
          placeholder="Ask a question…" onChange={event => setDraft(event.target.value)} onKeyDown={onKeyDown} />
        <div className="assistant-compose-actions">
          <VoiceDraft disabled={pending} onText={text =>
            setDraft(previous => [previous.trim(), text].filter(Boolean).join(' ').slice(0, MAX_LENGTH))} />
          <button type="submit" className="button button-primary assistant-icon-button" disabled={pending || !draft.trim()}
            data-activity-id="assistant-send"><ArrowIcon /><span className="visually-hidden">Send</span></button>
        </div>
      </form>
    </section>}

    <button ref={launcher} type="button" className={`assistant-launcher${open ? ' is-open' : ''}`}
      aria-expanded={open} aria-controls={open ? panelId : undefined}
      onClick={() => (open ? close() : onOpenChange(true))} data-activity-id="assistant-open">
      <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M4 4.5h12v8H9l-3.5 3v-3H4z" /></svg>
      <span className="assistant-launcher-text">{open ? 'Close assistant' : copy(step).open}</span>
    </button>
  </div>
}
