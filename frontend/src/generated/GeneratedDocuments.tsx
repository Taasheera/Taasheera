import { useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'
import { downloadDocument, getDocument, getOverview, regenerateDocument, saveDocument, type GeneratedDocument,
  type Overview } from './api'
import './generated.css'

// Cover letter, itinerary and letter templates written from the traveler's answers (CMPS271-57 to 62).
// The traveler reads each one, edits it in place and saves, or has it written again from the answers.
// `version` changes when the answers do, so the documents are loaded again.
export default function GeneratedDocuments({ applicationId, version }: { applicationId: string; version: string }) {
  const [overview, setOverview] = useState<Overview | null | Error>(null)
  const [kind, setKind] = useState('cover_letter')
  const [language, setLanguage] = useState('en')
  // The open editor has changes it hasn't saved: switching document or language asks first.
  const unsaved = useRef(false)
  const leave = () => !unsaved.current || window.confirm('Leave this document? Your unsaved changes will be lost.')
  const chooseKind = (next: string) => { if (next !== kind && leave()) { unsaved.current = false; setKind(next) } }
  const chooseLanguage = (next: string) => { if (next !== language && leave()) { unsaved.current = false; setLanguage(next) } }

  useEffect(() => {
    let active = true
    getOverview(applicationId)
      .then(data => { if (active) setOverview(data) })
      .catch(problem => { if (active) setOverview(problem instanceof Error ? problem : new Error('Could not load your documents.')) })
    return () => { active = false }
  }, [applicationId, version])

  if (overview === null) return <section className="gen" aria-labelledby="gen-title">
    <h2 id="gen-title">Letters and itinerary</h2>
    <p className="gen-note" role="status">Loading…</p>
  </section>
  if (overview instanceof Error) return <section className="gen" aria-labelledby="gen-title">
    <h2 id="gen-title">Letters and itinerary</h2>
    <p className="gen-note" role="alert">{overview.message}</p>
  </section>

  const current = overview.documents.find(item => item.kind === kind) ?? overview.documents[0]
  const languages = current?.languages ?? []
  const chosenLanguage = languages.some(option => option.language === language) ? language : 'en'

  return <section className="gen" aria-labelledby="gen-title">
    <div className="gen-head">
      <div>
        <h2 id="gen-title">Letters and itinerary</h2>
        <p className="gen-note">Written from your answers. Edit anything before you print it.</p>
      </div>
    </div>
    <div className="gen-tabs" role="tablist" aria-label="Documents">
      {overview.documents.map(item => <button key={item.kind} type="button" role="tab" id={`gen-tab-${item.kind}`}
        aria-selected={item.kind === current?.kind} aria-controls="gen-panel"
        className={`gen-tab${item.kind === current?.kind ? ' gen-tab-active' : ''}`}
        data-activity-id="generated-document-tab" onClick={() => chooseKind(item.kind)}>
        {item.label}
        {item.languages.some(option => option.edited) && <span className="gen-badge">Edited</span>}
      </button>)}
    </div>
    {current && <Editor key={`${current.kind}-${chosenLanguage}-${version}`} applicationId={applicationId}
      kind={current.kind} language={chosenLanguage} languages={languages} onLanguage={chooseLanguage}
      onDirty={dirty => { unsaved.current = dirty }}
      readOnly={overview.read_only}
      onSaved={edited => setOverview(previous => previous instanceof Error || previous === null ? previous : {
        ...previous, documents: previous.documents.map(item => item.kind !== current.kind ? item : {
          ...item, languages: item.languages.map(option => option.language === chosenLanguage ? { ...option, edited } : option),
        }),
      })} />}
    <p className="gen-notice">{overview.notice}</p>
  </section>
}

type EditorProps = {
  applicationId: string
  kind: string
  language: string
  languages: { language: string; label: string }[]
  onLanguage: (language: string) => void
  readOnly: boolean
  onSaved: (edited: boolean) => void
  onDirty: (dirty: boolean) => void
}

function Editor({ applicationId, kind, language, languages, onLanguage, readOnly, onSaved, onDirty }: EditorProps) {
  const [document_, setDocument] = useState<GeneratedDocument | null | Error>(null)
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState<'save' | 'regenerate' | 'download' | null>(null)

  useEffect(() => {
    let active = true
    getDocument(applicationId, kind, language)
      .then(data => { if (active) { setDocument(data); setDraft(data.body) } })
      .catch(problem => { if (active) setDocument(problem instanceof Error ? problem : new Error('Could not load this document.')) })
    return () => { active = false }
  }, [applicationId, kind, language])

  const dirtyNow = document_ !== null && !(document_ instanceof Error) && draft !== document_.body
  useEffect(() => { onDirty(dirtyNow) }, [dirtyNow, onDirty])

  if (document_ === null) return <div id="gen-panel" role="tabpanel" className="gen-panel"><p className="gen-note" role="status">Writing your document…</p></div>
  if (document_ instanceof Error) return <div id="gen-panel" role="tabpanel" className="gen-panel"><p className="gen-note" role="alert">{document_.message}</p></div>

  const loaded = document_
  const dirty = draft !== loaded.body
  const locked = readOnly || loaded.read_only

  async function save() {
    if (!draft.trim()) { toast.error("The document can't be empty. Write it again from your answers instead."); return }
    setBusy('save')
    try {
      const saved = await saveDocument(applicationId, kind, language, draft)
      setDocument(saved)
      setDraft(saved.body)
      onSaved(true)
      toast.success('Your changes are saved.')
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Could not save your changes.')
    } finally {
      setBusy(null)
    }
  }

  async function regenerate() {
    if ((loaded.edited || dirty) && !window.confirm('Write this document again from your answers? Your edits will be lost.')) return
    setBusy('regenerate')
    try {
      const fresh = await regenerateDocument(applicationId, kind, language)
      setDocument(fresh)
      setDraft(fresh.body)
      onSaved(false)
      toast.success('Written again from your current answers.')
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Could not write the document again.')
    } finally {
      setBusy(null)
    }
  }

  async function download() {
    if (dirty && !window.confirm('You have unsaved changes. Download the last saved version?')) return
    setBusy('download')
    try {
      const { file, filename } = await downloadDocument(applicationId, kind, language)
      const url = URL.createObjectURL(file)
      const link = window.document.createElement('a')
      link.href = url
      link.download = filename
      window.document.body.append(link)
      link.click()
      link.remove()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Could not prepare the PDF.')
    } finally {
      setBusy(null)
    }
  }

  return <div id="gen-panel" role="tabpanel" aria-labelledby={`gen-tab-${kind}`} className="gen-panel">
    <div className="gen-toolbar">
      {languages.length > 1 && <label className="gen-language">
        <span>Language</span>
        <select value={language} onChange={event => onLanguage(event.target.value)} data-activity-id="generated-document-language">
          {languages.map(option => <option key={option.language} value={option.language}>{option.label}</option>)}
        </select>
      </label>}
      <span className="gen-state" role="status">
        {loaded.outdated ? 'Your answers changed after you edited this. Check it, or write it again.'
          : loaded.edited ? 'Your edited version' : 'Written from your answers'}
      </span>
    </div>
    <label className="gen-editor-label" htmlFor="gen-editor">{loaded.title}</label>
    <textarea id="gen-editor" className="gen-editor" value={draft} readOnly={locked} spellCheck
      maxLength={20000} rows={Math.min(28, Math.max(12, draft.split('\n').length + 1))}
      onChange={event => setDraft(event.target.value)} />
    <div className="gen-actions">
      {!locked && <button type="button" className="button button-primary dash-small" disabled={!dirty || busy !== null}
        aria-busy={busy === 'save'} data-activity-id="generated-document-save" onClick={() => void save()}>
        {busy === 'save' ? 'Saving…' : 'Save changes'}</button>}
      {!locked && <button type="button" className="button button-secondary dash-small" disabled={busy !== null}
        aria-busy={busy === 'regenerate'} data-activity-id="generated-document-regenerate" onClick={() => void regenerate()}>
        {busy === 'regenerate' ? 'Writing…' : 'Write again from my answers'}</button>}
      <button type="button" className="button button-secondary dash-small" disabled={busy !== null}
        aria-busy={busy === 'download'} data-activity-id="generated-document-download" onClick={() => void download()}>
        {busy === 'download' ? 'Preparing…' : 'Download PDF'}</button>
    </div>
    {locked && <p className="gen-note">This application is archived. Restore it to edit its documents.</p>}
  </div>
}
