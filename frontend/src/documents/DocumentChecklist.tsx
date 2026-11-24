import { useEffect, useRef, useState, type ChangeEvent } from 'react'
import {
  checkDocumentFile, deleteDocument, DOCUMENT_TYPES, formatSize, getChecklist, openDocument, replaceDocument,
  uploadDocument, type Checklist, type Requirement, type UploadedDocument,
} from './api'
import CrossCheckPanel from './CrossCheckPanel'
import './documents.css'

// The supporting documents an application needs (CMPS271-29): a checklist the answers decide,
// with upload, replace and delete for each file. `version` reloads it when the answers change, and
// `onChange` tells the page after an upload, replacement or deletion (the questions page re-runs its check).
export default function DocumentChecklist({ applicationId, version, onChange, focusRequirement }:
  { applicationId: string; version?: string; onChange?: () => void; focusRequirement?: string | null }) {
  const [checklist, setChecklist] = useState<Checklist | Error | null>(null)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    let active = true
    getChecklist(applicationId)
      .then(result => { if (active) setChecklist(result) })
      .catch(failure => { if (active) setChecklist(failure instanceof Error ? failure : new Error('Could not load your documents.')) })
    return () => { active = false }
  }, [applicationId, version, attempt])

  useEffect(() => {
    if (!focusRequirement || !checklist || checklist instanceof Error) return
    const heading = document.getElementById(`doc-${focusRequirement}`)
    heading?.scrollIntoView?.({ block: 'center' })
    heading?.focus()
  }, [checklist, focusRequirement])

  const reload = () => {
    setAttempt(count => count + 1)
    onChange?.()
  }

  if (checklist === null) return <section className="docs" aria-labelledby="docs-title">
    <h2 id="docs-title">Supporting documents</h2>
    <p className="dash-status" role="status">Loading your documents…</p>
  </section>
  if (checklist instanceof Error) return <section className="docs" aria-labelledby="docs-title">
    <h2 id="docs-title">Supporting documents</h2>
    <div className="dash-status" role="alert">
      <p>{checklist.message}</p>
      <button type="button" className="button button-secondary dash-small" onClick={reload}>Try again</button>
    </div>
  </section>

  const required = checklist.requirements.filter(requirement => requirement.required)
  const done = required.filter(requirement => requirement.documents.length > 0).length
  return <section className="docs" aria-labelledby="docs-title">
    <div className="docs-head">
      <div>
        <h2 id="docs-title">Supporting documents</h2>
        <p className="docs-lede">What the embassy asks for with this visa, based on your answers. PDF, JPEG, PNG or WebP, up to 10 MB each.</p>
      </div>
      <p className={`docs-progress${checklist.complete ? ' is-complete' : ''}`} role="status">
        {checklist.complete ? 'All required documents uploaded' : `${done} of ${required.length} required uploaded`}
      </p>
    </div>
    {checklist.read_only && <p className="docs-note">This application is archived. Restore it to change its documents.</p>}
    <ul className="docs-list">
      {checklist.requirements.map(requirement => <RequirementRow key={requirement.id} applicationId={applicationId}
        requirement={requirement} readOnly={checklist.read_only} onChange={reload} />)}
    </ul>
    <CrossCheckPanel applicationId={applicationId} version={`${version ?? ''}-${attempt}`} />
    {checklist.other.length > 0 && <div className="docs-other">
      <h3>No longer needed</h3>
      <p className="docs-note">Your answers changed, so the embassy no longer asks for these. Delete them if they don't belong here.</p>
      <ul className="docs-files">
        {checklist.other.map(document => <FileRow key={document.id} applicationId={applicationId} document={document}
          readOnly={checklist.read_only} onChange={reload} />)}
      </ul>
    </div>}
  </section>
}

function RequirementRow({ applicationId, requirement, readOnly, onChange }:
  { applicationId: string; requirement: Requirement; readOnly: boolean; onChange: () => void }) {
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState('')
  const has = requirement.documents.length > 0

  async function add(file: File) {
    setBusy(true)
    setProblem('')
    try {
      await uploadDocument(applicationId, requirement.id, file)
      onChange()
    } catch (failure) {
      setProblem(failure instanceof Error ? failure.message : 'Could not upload that file.')
    } finally {
      setBusy(false)
    }
  }

  const titleId = `doc-${requirement.id}`
  return <li className={`docs-item${has ? ' is-done' : ''}`} aria-labelledby={titleId}>
    <span className="docs-mark" aria-hidden="true">{has ? '✓' : ''}</span>
    <div className="docs-body">
      <h3 id={titleId} tabIndex={-1}>
        {requirement.label}
        {!requirement.required && <span className="docs-tag">Optional</span>}
        <span className="visually-hidden">{has ? ', uploaded' : ', not uploaded yet'}</span>
      </h3>
      {requirement.help && <p className="docs-help">{requirement.help}</p>}
      {has && <ul className="docs-files">
        {requirement.documents.map(document => <FileRow key={document.id} applicationId={applicationId}
          document={document} readOnly={readOnly} onChange={onChange} />)}
      </ul>}
      {!readOnly && <FilePicker label={busy ? 'Uploading…' : has ? 'Add another file' : 'Upload'} busy={busy}
        primary={!has} onFile={add} onProblem={setProblem} activityId="document-upload" />}
      {problem && <p className="docs-error" role="alert">{problem}</p>}
    </div>
  </li>
}

function FileRow({ applicationId, document, readOnly, onChange }:
  { applicationId: string; document: UploadedDocument; readOnly: boolean; onChange: () => void }) {
  const [busy, setBusy] = useState<'replace' | 'delete' | null>(null)
  const [confirming, setConfirming] = useState(false)
  const [problem, setProblem] = useState('')

  async function run(kind: 'replace' | 'delete', action: () => Promise<unknown>) {
    setBusy(kind)
    setProblem('')
    try {
      await action()
      onChange()
    } catch (failure) {
      setProblem(failure instanceof Error ? failure.message : 'Something went wrong. Please try again.')
      setBusy(null)
    }
  }

  return <li className="docs-file">
    <span className="docs-file-kind" aria-hidden="true">{document.content_type === 'application/pdf' ? 'PDF' : 'IMG'}</span>
    <button type="button" className="docs-file-name" onClick={() => {
      setProblem('')
      openDocument(applicationId, document).catch(failure => setProblem(failure.message))
    }} data-activity-id="document-open">
      {document.filename}
    </button>
    <span className="docs-file-size">{formatSize(document.size_bytes)}</span>
    {!readOnly && !confirming && <span className="docs-file-actions">
      <FilePicker label={busy === 'replace' ? 'Replacing…' : 'Replace'} busy={busy !== null} small
        onFile={file => run('replace', () => replaceDocument(applicationId, document.id, file))}
        onProblem={setProblem} activityId="document-replace" />
      <button type="button" className="button button-secondary dash-small docs-delete" disabled={busy !== null}
        onClick={() => setConfirming(true)} data-activity-id="document-delete">Delete</button>
    </span>}
    {confirming && <span className="docs-confirm" role="group" aria-label={`Delete ${document.filename}?`}>
      <span>Delete this file?</span>
      <button type="button" className="button dash-small dash-danger" disabled={busy !== null}
        onClick={() => run('delete', () => deleteDocument(applicationId, document.id))} data-activity-id="document-delete-confirm">
        {busy === 'delete' ? 'Deleting…' : 'Delete'}
      </button>
      <button type="button" className="button button-secondary dash-small" disabled={busy !== null}
        onClick={() => setConfirming(false)}>Keep it</button>
    </span>}
    {problem && <p className="docs-error docs-file-error" role="alert">{problem}</p>}
  </li>
}

type PickerProps = {
  label: string
  busy: boolean
  onFile: (file: File) => void
  onProblem: (message: string) => void
  activityId: string
  primary?: boolean
  small?: boolean
}

function FilePicker({ label, busy, onFile, onProblem, activityId, primary = false, small = false }: PickerProps) {
  const input = useRef<HTMLInputElement>(null)
  const onChange = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    event.target.value = '' // choosing the same file again still triggers a change
    if (!file) return
    const problem = checkDocumentFile(file)
    onProblem(problem ?? '')
    if (!problem) onFile(file)
  }
  return <>
    <button type="button" className={`button ${primary ? 'button-primary' : 'button-secondary'} dash-small${small ? '' : ' docs-pick'}`}
      disabled={busy} aria-busy={busy || undefined} onClick={() => input.current?.click()} data-activity-id={activityId}>
      {label}
    </button>
    <input ref={input} type="file" accept={DOCUMENT_TYPES.join(',')} hidden onChange={onChange} />
  </>
}
