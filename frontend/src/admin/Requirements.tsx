import { useCallback, useEffect, useState } from 'react'
import { useOutletContext, useSearchParams } from 'react-router'
import { toast } from 'sonner'
import Feature from '../Feature'
import NotFound from '../pages/NotFound'
import { AdminRequestError } from './api'
import type { AdminContext } from './AdminLayout'
import { readableDate } from './format'
import GuideFactsForm from './GuideFactsForm'
import {
  checkContent, discardDraft, getDestination, getRevision, listDestinations, ProblemsError, publishDraft, rollBack,
  saveDraft, type Change, type CheckResult, type Content, type DestinationDetail, type DestinationSummary, type Json,
  type JsonObject, type Problem,
} from './requirementsApi'
import './backoffice.css'

const KIND_LABELS: Record<Change['kind'], string> = { added: 'Added', removed: 'Removed', changed: 'Changed', moved: 'Reordered' }

function short(value: Json): string {
  const shown = typeof value === 'string' ? value : JSON.stringify(value)
  return shown.length > 140 ? `${shown.slice(0, 140)}…` : shown
}

function parse(text: string): { value?: JsonObject; error?: string } {
  try {
    const value = JSON.parse(text) as Json
    if (value === null || typeof value !== 'object' || Array.isArray(value)) return { error: 'The questionnaire must be a JSON object.' }
    return { value }
  } catch (error) {
    return { error: `Not valid JSON: ${error instanceof Error ? error.message : 'check the commas and quotes'}.` }
  }
}

function revisionLabel(revision: { kind: string; restored_revision: number | null }): string {
  if (revision.kind === 'publish') return 'Published'
  return revision.restored_revision ? `Rolled back to revision ${revision.restored_revision}` : 'Returned to the packaged files'
}

export function Changes({ changes }: { changes: Change[] }) {
  if (changes.length === 0) return <p className="admin-muted">No changes from what travelers see now.</p>
  return <ul className="backoffice-changes">
    {changes.map((change, index) => <li key={index}>
      <span className={`backoffice-kind backoffice-kind--${change.kind}`}>{KIND_LABELS[change.kind]}</span>{' '}
      <code>{change.part === 'guide' ? `guide.${change.path}` : change.path}</code>
      {change.kind === 'changed' && <span className="backoffice-values"> {short(change.before)} → {short(change.after)}</span>}
      {change.kind === 'moved' && <span className="backoffice-values"> new order: {short(change.after)}</span>}
      {change.kind === 'added' && <span className="backoffice-values"> {short(change.after)}</span>}
    </li>)}
  </ul>
}

function Problems({ problems, label }: { problems: Problem[]; label: string }) {
  if (problems.length === 0) return null
  return <ul className="backoffice-errors" aria-label={label}>
    {problems.map((problem, index) => <li key={index}><code>{problem.path || '(whole file)'}</code> {problem.message}</li>)}
  </ul>
}

function Editor({ id, leave }: { id: string; leave: () => void }) {
  const [detail, setDetail] = useState<DestinationDetail | null>(null)
  const [text, setText] = useState('')
  const [guide, setGuide] = useState<JsonObject>({})
  const [base, setBase] = useState(0)
  const [result, setResult] = useState<CheckResult | null>(null)
  const [problems, setProblems] = useState<Problem[]>([])
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [status, setStatus] = useState('')
  const [target, setTarget] = useState<{ revision: number; result: CheckResult | null; note: string } | null>(null)

  const fail = useCallback((problem: unknown) => {
    if (problem instanceof AdminRequestError && problem.status === 403) return leave()
    if (problem instanceof ProblemsError) setProblems(problem.problems)
    setError(problem instanceof Error ? problem.message : 'Something went wrong. Please try again.')
  }, [leave])

  const show = useCallback((loaded: DestinationDetail, useDraft = true) => {
    const source: Content = useDraft && loaded.draft ? loaded.draft : loaded.current
    setDetail(loaded)
    setText(JSON.stringify(source.questionnaire, null, 2))
    setGuide(source.guide)
    setBase(useDraft && loaded.draft ? loaded.draft.base_revision : loaded.revision)
    setResult(null); setProblems([]); setTarget(null)
  }, [])

  useEffect(() => {
    let active = true
    getDestination(id).then(loaded => { if (active) show(loaded) }, problem => { if (active) fail(problem) })
    return () => { active = false }
  }, [id, show, fail])

  const parsed = parse(text)
  const content = (): Content | null => parsed.value ? { questionnaire: parsed.value, guide } : null

  async function run(action: () => Promise<void>) {
    setBusy(true); setError(''); setStatus('')
    try {
      await action()
    } catch (problem) {
      fail(problem)
    } finally {
      setBusy(false)
    }
  }

  const check = () => run(async () => {
    const edited = content()
    if (!edited) return
    const checked = await checkContent(id, edited)
    setResult(checked); setProblems(checked.problems)
    setStatus(checked.valid ? 'These changes pass every check.' : 'Fix the problems shown before publishing.')
  })

  const save = () => run(async () => {
    const edited = content()
    if (!edited) return
    const saved = await saveDraft(id, edited, base)
    setDetail(current => current && { ...current, draft: saved.draft, has_draft: true })
    setResult(saved.check); setProblems(saved.check.problems)
    setStatus(saved.check.valid ? 'Draft saved. It passes every check.' : 'Draft saved. Fix the problems shown before publishing.')
  })

  const publish = () => run(async () => {
    const edited = content()
    if (!edited || !note.trim()) return
    const saved = await saveDraft(id, edited, base)
    setResult(saved.check); setProblems(saved.check.problems)
    if (!saved.check.valid) {
      setError('Fix the problems shown before publishing.')
      return
    }
    const published = await publishDraft(id, base, note.trim())
    show(published); setNote('')
    toast.success(`Published revision ${published.revision}. Travelers see it now.`)
    setStatus(`Published revision ${published.revision} (questionnaire version ${published.questionnaire_version}).`)
  })

  const discard = () => run(async () => {
    await discardDraft(id)
    show(await getDestination(id))
    setStatus('Draft discarded.')
  })

  const review = (revision: number) => run(async () => {
    setTarget({ revision, result: null, note: '' })
    const checked = await checkContent(id, await getRevision(id, revision))
    setTarget(current => current && current.revision === revision ? { ...current, result: checked } : current)
  })

  const confirmRollback = () => run(async () => {
    if (!detail || !target || !target.note.trim()) return
    const restored = await rollBack(id, target.revision, detail.revision, target.note.trim())
    show(restored, false)
    toast.success(`Revision ${restored.revision} is live.`)
    setStatus(`Revision ${restored.revision} is live (questionnaire version ${restored.questionnaire_version}).`)
  })

  if (!detail) return error ? <p className="admin-error" role="alert">{error}</p> : <p role="status" className="admin-muted">Loading…</p>
  const questionnaireProblems = problems.filter(problem => problem.part === 'questionnaire')
  const guideProblems = problems.filter(problem => problem.part === 'guide')
  const staleDraft = detail.draft && detail.draft.base_revision !== detail.revision
  const newVersion = result?.valid && result.questionnaire_version !== detail.questionnaire_version

  return <>
    <section className="admin-panel" aria-labelledby="live-heading">
      <h2 id="live-heading">{detail.title}</h2>
      <p>{detail.revision === 0
        ? `Travelers see the packaged files (questionnaire version ${detail.questionnaire_version}).`
        : `Travelers see revision ${detail.revision} (questionnaire version ${detail.questionnaire_version}), published ${readableDate(detail.published_at)}.`}
      {' '}The packaged file is version {detail.packaged_version}.</p>
      <p className="admin-muted">{detail.applications_active} application(s) in progress follow this questionnaire and are
        re-checked on their next visit after a publish; {detail.applications_recorded} recorded as submitted keep their answers.</p>
      {detail.draft && <p role="note">Editing the saved draft by {detail.draft.author ?? 'a deleted account'} from {readableDate(detail.draft.updated_at)}.</p>}
      {staleDraft && <div className="admin-confirm" role="alert">
        <p>This draft started from revision {detail.draft?.base_revision}; revision {detail.revision} was published since,
          so publishing it would be refused.</p>
        <button type="button" className="button button-secondary" disabled={busy} onClick={() => show(detail, false)}>
          Start again from the published version</button>
      </div>}
    </section>

    <section className="admin-panel" aria-labelledby="guide-heading">
      <h2 id="guide-heading">Visa guide facts</h2>
      <p className="admin-muted">Fees, processing time and official sources shown on the public visa guide.</p>
      <GuideFactsForm guide={guide} problems={guideProblems} disabled={busy} onChange={setGuide} />
    </section>

    <section className="admin-panel" aria-labelledby="questionnaire-heading">
      <h2 id="questionnaire-heading">Questions and documents</h2>
      <p className="admin-muted">The questionnaire file as JSON: its steps, questions, rules and the documents it asks for.
        The server sets the version when you publish.</p>
      <label className="admin-field" htmlFor="questionnaire-json"><span>Questionnaire JSON</span>
        <textarea id="questionnaire-json" className="backoffice-json" spellCheck={false} rows={24} value={text} disabled={busy}
          aria-invalid={Boolean(parsed.error) || questionnaireProblems.length > 0 || undefined}
          aria-describedby="questionnaire-json-problems" onChange={event => setText(event.target.value)} />
      </label>
      <div id="questionnaire-json-problems">
        {parsed.error && <p className="admin-error">{parsed.error}</p>}
        <Problems problems={questionnaireProblems} label="Problems in the questionnaire" />
      </div>
    </section>

    <section className="admin-panel" aria-labelledby="publish-heading">
      <h2 id="publish-heading">Check and publish</h2>
      <div className="admin-actions">
        <button type="button" className="button button-secondary" disabled={busy || !parsed.value} onClick={check}>Check changes</button>
        <button type="button" className="button button-secondary" disabled={busy || !parsed.value} onClick={save}>Save draft</button>
        {detail.draft && <button type="button" className="button button-secondary" disabled={busy} onClick={discard}>Discard draft</button>}
      </div>
      {status && <p role="status">{status}</p>}
      {error && <p className="admin-error" role="alert">{error}</p>}
      {result && <div className="backoffice-result">
        <h3>Changes from what travelers see now</h3>
        <Changes changes={result.changes} />
        {result.valid && <p>{newVersion
          ? `Publishing creates questionnaire version ${result.questionnaire_version}: the questions or rules change, so applications in progress are re-checked against it.`
          : `Publishing keeps questionnaire version ${result.questionnaire_version}: only wording or guide facts change.`}</p>}
      </div>}
      <label className="admin-field backoffice-field" htmlFor="publish-note"><span>Change note</span>
        <textarea id="publish-note" rows={2} maxLength={500} value={note} disabled={busy}
          placeholder="What changed and where you checked it" onChange={event => setNote(event.target.value)} />
      </label>
      <button type="button" className="button button-primary" disabled={busy || !parsed.value || !note.trim()} onClick={publish}>
        Publish</button>
    </section>

    <section className="admin-panel" aria-labelledby="history-heading">
      <h2 id="history-heading">History</h2>
      <div className="admin-table-wrap"><table className="admin-table">
        <thead><tr><th scope="col">Revision</th><th scope="col">What</th><th scope="col">Version</th><th scope="col">Note</th>
          <th scope="col">By</th><th scope="col">When</th><th scope="col"><span className="visually-hidden">Action</span></th></tr></thead>
        <tbody>
          {detail.history.map(revision => <tr key={revision.revision}>
            <td>{revision.revision}{revision.revision === detail.revision && ' (live)'}</td>
            <td>{revisionLabel(revision)}</td>
            <td>{revision.questionnaire_version}</td>
            <td className="backoffice-wrap">{revision.note}</td>
            <td>{revision.author ?? 'Deleted account'}</td>
            <td>{readableDate(revision.created_at)}</td>
            <td>{revision.revision !== detail.revision && <button type="button" className="button button-secondary" disabled={busy}
              onClick={() => review(revision.revision)}>Roll back to {revision.revision}</button>}</td>
          </tr>)}
          <tr>
            <td>0{detail.revision === 0 && ' (live)'}</td><td>The packaged files</td><td>{detail.packaged_version}</td>
            <td className="backoffice-wrap">Shipped with the app</td><td>-</td><td>-</td>
            <td>{detail.revision !== 0 && <button type="button" className="button button-secondary" disabled={busy}
              onClick={() => review(0)}>Return to the packaged files</button>}</td>
          </tr>
        </tbody>
      </table></div>
      {target && <div className="admin-confirm" role="group" aria-label={`Roll back to revision ${target.revision}`}>
        <h3>{target.revision ? `Roll back to revision ${target.revision}` : 'Return to the packaged files'}</h3>
        {target.result ? <>
          <Changes changes={target.result.changes} />
          <Problems problems={target.result.problems} label="Problems in that version" />
          {target.result.valid && <p>Travelers would get questionnaire version {target.result.questionnaire_version}.</p>}
        </> : <p role="status" className="admin-muted">Comparing with what travelers see now…</p>}
        <label className="admin-field backoffice-field" htmlFor="rollback-note"><span>Why</span>
          <textarea id="rollback-note" rows={2} maxLength={500} value={target.note} disabled={busy}
            onChange={event => setTarget({ ...target, note: event.target.value })} />
        </label>
        <div className="admin-actions">
          <button type="button" className="button button-primary"
            disabled={busy || !target.result?.valid || !target.note.trim()} onClick={confirmRollback}>Confirm rollback</button>
          <button type="button" className="button button-secondary" disabled={busy} onClick={() => setTarget(null)}>Cancel</button>
        </div>
      </div>}
    </section>
  </>
}

function RequirementsEditor() {
  const { leave } = useOutletContext<AdminContext>()
  const [params, setParams] = useSearchParams()
  const selected = params.get('destination') ?? ''
  const [destinations, setDestinations] = useState<DestinationSummary[] | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    document.title = 'Destination requirements | Taasheera'
    let active = true
    listDestinations().then(found => { if (active) setDestinations(found) }, problem => {
      if (!active) return
      if (problem instanceof AdminRequestError && problem.status === 403) leave()
      else setError(problem instanceof Error ? problem.message : 'Could not load the destinations.')
    })
    return () => { active = false }
  }, [leave])

  const known = destinations?.some(destination => destination.id === selected)
  return <div className="admin-page">
    <h1>Destination requirements</h1>
    <p className="admin-muted">Change a destination's questions, documents and guide facts without a deploy. Every change is
      checked like the packaged data files, and every publish and rollback is recorded.</p>
    {error && <p className="admin-error" role="alert">{error}</p>}
    {destinations && <label className="admin-field backoffice-picker" htmlFor="destination-picker"><span>Destination</span>
      <select id="destination-picker" value={known ? selected : ''} onChange={event => setParams(
        event.target.value ? { destination: event.target.value } : {})}>
        <option value="">Choose a destination</option>
        {destinations.map(destination => <option key={destination.id} value={destination.id}>
          {destination.title} · {destination.revision ? `revision ${destination.revision}` : 'packaged'}
          {destination.has_draft ? ' · draft' : ''}</option>)}
      </select>
    </label>}
    {!destinations && !error && <p role="status" className="admin-muted">Loading…</p>}
    {known && <Editor key={selected} id={selected} leave={leave} />}
  </div>
}

export default function Requirements() {
  return <Feature name="requirements_editor" fallback={<NotFound />}><RequirementsEditor /></Feature>
}
