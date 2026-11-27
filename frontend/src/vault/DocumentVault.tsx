import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Link, useLocation } from 'react-router'
import { toast } from 'sonner'
import Feature from '../Feature'
import NotFound from '../pages/NotFound'
import { DISCLAIMER, formatDateTime, useTraveler } from '../dashboard/helpers'
import { SignedOut } from '../dashboard/shared'
import { formatSize, openDocument } from '../documents/api'
import {
  attachDocument, deleteVaultDocument, getPassportCheck, getVault, savePassportPages, translateDocument,
  type AttachTarget, type PassportCheck, type Translation, type Vault, type VaultDocument,
} from './api'
import '../ui.css'
import '../dashboard/dashboard.css'
import '../documents/documents.css'
import './vault.css'

export default function DocumentVault() {
  return <Feature name="supporting_documents" fallback={<NotFound />}>
    <Feature name="document_vault" fallback={<NotFound />}><VaultPage /></Feature>
  </Feature>
}

function VaultPage() {
  const traveler = useTraveler()
  const { search, hash } = useLocation()
  const preferred = new URLSearchParams(search).get('application')
  const [vault, setVault] = useState<Vault | Error | null>(null)
  const [attempt, setAttempt] = useState(0)
  const heading = useRef<HTMLHeadingElement>(null)

  useEffect(() => { document.title = 'Your documents | Taasheera' }, [])

  useEffect(() => {
    if (!traveler) return
    let active = true
    getVault()
      .then(found => { if (active) setVault(found) })
      .catch(failure => { if (active) setVault(failure instanceof Error ? failure : new Error('Could not load your documents.')) })
    return () => { active = false }
  }, [traveler, attempt])

  const reload = () => setAttempt(count => count + 1)

  if (traveler === undefined) return <section className="dash-page container"><p className="dash-status" role="status">Checking your session…</p></section>
  if (traveler === null) return <section className="dash-page container"><SignedOut /></section>

  return <section className="dash-page container vault-page" aria-labelledby="vault-title">
    <div className="dash-head">
      <div>
        <h1 id="vault-title" ref={heading} tabIndex={-1}>Your documents</h1>
        <p className="dash-lede">Every supporting document you've uploaded and the applications that use it. Add a file to
          another application without uploading it again, or delete it everywhere.</p>
      </div>
      <Link to="/passport" className="button button-secondary dash-small">Your passport</Link>
    </div>

    <Feature name="passport_rules"><PassportPanel focus={hash === '#passport'} /></Feature>

    <section className="vault-files" aria-labelledby="vault-files-title">
      <h2 id="vault-files-title">Uploaded files</h2>
      {vault === null && <p className="dash-status" role="status">Loading your documents…</p>}
      {vault instanceof Error && <div className="dash-status" role="alert">
        <p>{vault.message}</p>
        <button type="button" className="button button-secondary dash-small" onClick={reload}>Try again</button>
      </div>}
      {vault && !(vault instanceof Error) && (vault.documents.length === 0
        ? <p className="dash-status">You haven't uploaded any documents yet. Upload them from an application's checklist,
            and they'll be kept here to reuse.</p>
        : <ul className="vault-list">
            {vault.documents.map(document => <DocumentCard key={document.id} document={document} targets={vault.targets}
              preferred={preferred} onChange={reload} onDeleted={() => { reload(); heading.current?.focus() }} />)}
          </ul>)}
    </section>

    <p className="dash-disclaimer">{DISCLAIMER}</p>
  </section>
}

function DocumentCard({ document, targets, preferred, onChange, onDeleted }:
  { document: VaultDocument; targets: AttachTarget[]; preferred: string | null; onChange: () => void; onDeleted: () => void }) {
  const [problem, setProblem] = useState('')
  const titleId = `vault-${document.id}`
  const first = document.uses[0]

  function open() {
    setProblem('')
    openDocument(first.application_id, {
      id: first.id, requirement: first.requirement, filename: document.filename, content_type: document.content_type,
      size_bytes: document.size_bytes, uploaded_at: document.uploaded_at,
    }).catch(failure => setProblem(failure instanceof Error ? failure.message : 'Could not open that file.'))
  }

  return <li className="vault-card" aria-labelledby={titleId}>
    <div className="vault-card-head">
      <span className="docs-file-kind" aria-hidden="true">{document.content_type === 'application/pdf' ? 'PDF' : 'IMG'}</span>
      <div>
        <h3 id={titleId}>{document.label}</h3>
        <p className="docs-note vault-meta">
          <button type="button" className="docs-file-name" onClick={open} data-activity-id="document-open">{document.filename}</button>
          {' · '}{formatSize(document.size_bytes)} · Uploaded {formatDateTime(document.uploaded_at)}
        </p>
      </div>
    </div>
    <h4 className="vault-subhead">Used in</h4>
    <ul className="vault-uses">
      {document.uses.map(use => <li key={use.id}>
        <Link to={`/dashboard/${use.application_id}`}>{use.place ? `${use.application_title}, ${use.place}` : use.application_title}</Link>
        {' · '}{use.requirement_label}
        {use.archived && <span className="docs-tag">Archived</span>}
      </li>)}
    </ul>
    <AttachForm document={document} targets={targets} preferred={preferred} onAttached={onChange} />
    <Feature name="document_translation"><TranslatePanel document={document} /></Feature>
    <DeleteControl document={document} onDeleted={onDeleted} />
    {problem && <p className="docs-error" role="alert">{problem}</p>}
  </li>
}

function AttachForm({ document, targets, preferred, onAttached }:
  { document: VaultDocument; targets: AttachTarget[]; preferred: string | null; onAttached: () => void }) {
  const options = targets.map(target => ({
    ...target,
    requirements: target.requirements.filter(requirement => !document.uses.some(use =>
      use.application_id === target.application_id && use.requirement === requirement.id)),
  })).filter(target => target.requirements.length > 0)
  const initial = options.find(target => target.application_id === preferred) ?? options[0]
  const [applicationId, setApplicationId] = useState(initial?.application_id ?? '')
  const chosen = options.find(target => target.application_id === applicationId) ?? initial
  const matching = chosen?.requirements.find(requirement => requirement.label === document.label) ?? chosen?.requirements[0]
  const [requirement, setRequirement] = useState(matching?.id ?? '')
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState('')
  if (!chosen) return null
  const requirementId = chosen.requirements.some(item => item.id === requirement) ? requirement : matching?.id ?? ''

  async function submit(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setProblem('')
    try {
      await attachDocument(document.id, chosen!.application_id, requirementId)
      toast.success(`Added ${document.filename} to ${chosen!.title}.`)
      onAttached()
    } catch (failure) {
      setProblem(failure instanceof Error ? failure.message : 'Could not add that file.')
    } finally {
      setBusy(false)
    }
  }

  const ids = { application: `attach-app-${document.id}`, requirement: `attach-req-${document.id}` }
  return <form className="vault-attach" onSubmit={submit} aria-label={`Use ${document.filename} in another application`}>
    <label htmlFor={ids.application}>Use it in</label>
    <select id={ids.application} value={chosen.application_id} onChange={event => setApplicationId(event.target.value)}>
      {options.map(target => <option key={target.application_id} value={target.application_id}>
        {target.place ? `${target.title}, ${target.place}` : target.title}
      </option>)}
    </select>
    <label htmlFor={ids.requirement}>as</label>
    <select id={ids.requirement} value={requirementId} onChange={event => setRequirement(event.target.value)}>
      {chosen.requirements.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}
    </select>
    <button type="submit" className="button button-secondary dash-small" disabled={busy || !requirementId}
      aria-busy={busy || undefined} data-activity-id="document-attach">{busy ? 'Adding…' : 'Add to application'}</button>
    {problem && <p className="docs-error" role="alert">{problem}</p>}
  </form>
}

function TranslatePanel({ document }: { document: VaultDocument }) {
  const [language, setLanguage] = useState(document.languages[0]?.code ?? 'en')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<Translation | null>(null)
  const [problem, setProblem] = useState('')

  async function run() {
    setBusy(true)
    setProblem('')
    try {
      setResult(await translateDocument(document.id, language))
    } catch (failure) {
      setResult(null)
      setProblem(failure instanceof Error ? failure.message : 'Could not translate that document.')
    } finally {
      setBusy(false)
    }
  }

  const id = `translate-${document.id}`
  return <div className="vault-translate">
    <div className="vault-attach">
      <label htmlFor={id}>Translate into</label>
      <select id={id} value={language} onChange={event => setLanguage(event.target.value)}>
        {document.languages.map(option => <option key={option.code} value={option.code}>{option.label}</option>)}
      </select>
      <button type="button" className="button button-secondary dash-small" onClick={() => void run()} disabled={busy}
        aria-busy={busy || undefined} data-activity-id="document-translate">{busy ? 'Translating…' : 'Translate'}</button>
    </div>
    <p className="docs-note">Translating sends the text we read from this file to Google's Gemini AI service.</p>
    {problem && <p className="docs-error" role="alert">{problem}</p>}
    {result && <figure className="vault-translation" aria-label={`${result.language_label} translation of ${document.filename}`}>
      <p className="vault-notice" role="note"><strong>Machine translation, not a certified translation.</strong> {result.notice}</p>
      {result.truncated && <p className="docs-note">This document is long, so only its first part was translated.</p>}
      <pre className="vault-translation-text">{result.text}</pre>
    </figure>}
  </div>
}

function DeleteControl({ document, onDeleted }: { document: VaultDocument; onDeleted: () => void }) {
  const [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState('')
  const count = new Set(document.uses.map(use => use.application_id)).size

  async function remove() {
    setBusy(true)
    setProblem('')
    try {
      await deleteVaultDocument(document.id)
      toast.success(`Deleted ${document.filename}.`)
      onDeleted()
    } catch (failure) {
      setProblem(failure instanceof Error ? failure.message : 'Could not delete that file.')
      setBusy(false)
    }
  }

  if (!confirming) return <div className="vault-delete">
    <button type="button" className="button button-secondary dash-small docs-delete" onClick={() => setConfirming(true)}
      data-activity-id="vault-delete">Delete</button>
  </div>
  return <div className="docs-confirm vault-delete" role="group" aria-label={`Delete ${document.filename}?`}>
    <span>Delete this file everywhere? {count === 1 ? 'One application' : `${count} applications`} will no longer have it,
      and it can't be undone.</span>
    <button type="button" className="button dash-small dash-danger" disabled={busy} onClick={() => void remove()}
      data-activity-id="vault-delete-confirm">{busy ? 'Deleting…' : 'Delete everywhere'}</button>
    <button type="button" className="button button-secondary dash-small" disabled={busy}
      onClick={() => setConfirming(false)}>Keep it</button>
    {problem && <p className="docs-error" role="alert">{problem}</p>}
  </div>
}

function PassportPanel({ focus }: { focus: boolean }) {
  const [check, setCheck] = useState<PassportCheck | Error | null>(null)
  const [pages, setPages] = useState('')
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState('')
  const heading = useRef<HTMLHeadingElement>(null)

  useEffect(() => {
    let active = true
    getPassportCheck()
      .then(found => {
        if (!active) return
        setCheck(found)
        setPages(found.blank_pages === null ? '' : String(found.blank_pages))
      })
      .catch(failure => { if (active) setCheck(failure instanceof Error ? failure : new Error('Could not load your passport checks.')) })
    return () => { active = false }
  }, [])

  useEffect(() => {
    if (focus && check && !(check instanceof Error)) {
      heading.current?.scrollIntoView?.({ block: 'center' })
      heading.current?.focus()
    }
  }, [focus, check])

  async function save(event: FormEvent) {
    event.preventDefault()
    const count = Number(pages)
    if (pages.trim() === '' || !Number.isInteger(count) || count < 0 || count > 100) {
      setProblem('Enter a whole number of pages, from 0 to 100.')
      return
    }
    setBusy(true)
    setProblem('')
    try {
      setCheck(await savePassportPages(count))
      toast.success('Saved. Your applications check it from now on.')
    } catch (failure) {
      setProblem(failure instanceof Error ? failure.message : 'Could not save the number of pages.')
    } finally {
      setBusy(false)
    }
  }

  return <section id="passport" className="vault-passport" aria-labelledby="vault-passport-title">
    <h2 id="vault-passport-title" ref={heading} tabIndex={-1}>Your passport's blank pages</h2>
    <p className="docs-note">Visa stickers and entry stamps need empty pages. Count the blank visa pages in the passport you'll
      travel on (not the last page or the observation pages), and we'll check it against each destination.</p>
    {check instanceof Error && <p className="docs-error" role="alert">{check.message}</p>}
    <form className="vault-attach" onSubmit={save}>
      <label htmlFor="blank-pages">Blank visa pages</label>
      <input id="blank-pages" type="number" inputMode="numeric" min={0} max={100} step={1} value={pages}
        onChange={event => setPages(event.target.value)} />
      <button type="submit" className="button button-secondary dash-small" disabled={busy} aria-busy={busy || undefined}
        data-activity-id="passport-pages-save">{busy ? 'Saving…' : 'Save'}</button>
    </form>
    {problem && <p className="docs-error" role="alert">{problem}</p>}
    {check && !(check instanceof Error) && check.rules.length > 0 && <ul className="vault-rules">
      {check.rules.map(rule => <li key={rule.questionnaire_id}>
        <h3>{rule.title}</h3>
        <ul>{rule.requirements.map(requirement => <li key={requirement}>{requirement}</li>)}</ul>
        {rule.note && <p className="docs-note">{rule.note}</p>}
        <p className="docs-note">Official {rule.sources.length === 1 ? 'source' : 'sources'}:{' '}
          {rule.sources.map((source, index) => <span key={source.url}>
            {index > 0 && '; '}<a href={source.url} target="_blank" rel="noopener noreferrer">{source.label}</a>
          </span>)}. Checked {rule.checked_on}; embassies can change their rules, so check before you apply.</p>
      </li>)}
    </ul>}
  </section>
}
