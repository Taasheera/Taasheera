import { useEffect, useState, type FormEvent } from 'react'
import { useOutletContext } from 'react-router'
import { toast } from 'sonner'
import Feature from '../Feature'
import RichText from '../help/RichText'
import NotFound from '../pages/NotFound'
import { AdminRequestError } from './api'
import type { AdminContext } from './AdminLayout'
import { readableDate } from './format'
import {
  addHelpEntry, deleteHelpEntry, editHelpEntry, helpLanguageLabels, helpLanguages, listHelpEntries, reorderHelpEntries,
  type HelpEntry, type HelpEntryInput, type HelpLanguage,
} from './helpApi'
import './support.css'

const EMPTY: HelpEntryInput = { question: '', answer: '', category: 'General', language: 'en', published: false }

export default function HelpContent() {
  return <Feature name="support_tools" fallback={<NotFound />}><Editor /></Feature>
}

function Editor() {
  const { leave } = useOutletContext<AdminContext>()
  const [language, setLanguage] = useState<HelpLanguage>('en')
  const [entries, setEntries] = useState<HelpEntry[] | null>(null)
  const [error, setError] = useState('')
  const [retry, setRetry] = useState(0)
  const [draft, setDraft] = useState<HelpEntryInput>(EMPTY)
  const [editing, setEditing] = useState<number | null>(null)
  const [deleting, setDeleting] = useState<HelpEntry | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let active = true
    listHelpEntries(language).then(found => { if (active) { setEntries(found); setError('') } }).catch(failure => {
      if (!active) return
      if (failure instanceof AdminRequestError && failure.status === 403) leave()
      else setError(failure instanceof Error ? failure.message : 'Could not load the help content.')
    })
    return () => { active = false }
  }, [language, retry, leave])

  async function run(action: () => Promise<unknown>, done: string) {
    if (busy) return false
    setBusy(true)
    try {
      await action()
      toast.success(done)
      setRetry(value => value + 1)
      return true
    } catch (failure) {
      if (failure instanceof AdminRequestError && failure.status === 403) { leave(); return false }
      toast.error(failure instanceof Error ? failure.message : 'Something went wrong. Please try again.')
      setRetry(value => value + 1)
      return false
    } finally {
      setBusy(false)
    }
  }

  function startEditing(entry: HelpEntry) {
    setEditing(entry.id)
    setDraft({ question: entry.question, answer: entry.answer, category: entry.category, language: entry.language, published: entry.published })
  }

  function stopEditing() {
    setEditing(null)
    setDraft({ ...EMPTY, language })
  }

  async function save(event: FormEvent) {
    event.preventDefault()
    const saved = await run(() => editing === null ? addHelpEntry(draft) : editHelpEntry(editing, draft),
      editing === null ? 'Entry added.' : 'Entry saved.')
    if (saved) stopEditing()
  }

  function move(index: number, by: -1 | 1) {
    if (!entries) return
    const ids = entries.map(entry => entry.id)
    const [moved] = ids.splice(index, 1)
    ids.splice(index + by, 0, moved)
    void run(() => reorderHelpEntries(language, ids), 'Order saved.')
  }

  function toggle(entry: HelpEntry) {
    const { question, answer, category } = entry
    void run(() => editHelpEntry(entry.id, { question, answer, category, language: entry.language, published: !entry.published }),
      entry.published ? 'Entry hidden from the FAQ.' : 'Entry published.')
  }

  const valid = draft.question.trim() && draft.answer.trim() && draft.category.trim()
  return <div className="admin-page">
    <h1>Help content</h1>
    <p className="admin-muted">The FAQ page and the home page’s questions show the published entries, in this order. Changes go live at once.</p>
    <section className="admin-panel" aria-labelledby="entries-heading">
      <h2 id="entries-heading">Entries</h2>
      <div className="admin-filters">
        <label className="admin-field">
          <span>Language</span>
          <select id="help-language" value={language} onChange={event => {
            const next = event.target.value as HelpLanguage
            setLanguage(next); setEntries(null); setDeleting(null)
            if (editing === null) setDraft(current => ({ ...current, language: next }))
          }}>
            {helpLanguages.map(option => <option key={option} value={option}>{helpLanguageLabels[option]}</option>)}
          </select>
        </label>
      </div>
      {error ? <div role="alert">
        <p className="admin-error">{error}</p>
        <button type="button" className="button button-secondary" onClick={() => { setError(''); setRetry(value => value + 1) }}>Try again</button>
      </div> : entries === null ? <p className="admin-muted" role="status">Loading…</p>
        : entries.length === 0 ? <p className="admin-muted">No {helpLanguageLabels[language]} entries yet. The FAQ page shows the built-in questions until one is published.</p>
          : <ol className="support-list">
            {entries.map((entry, index) => <li key={entry.id}>
              <strong>{entry.question}</strong>
              <p className="admin-muted">{entry.category} · {entry.published ? 'Published' : 'Hidden'} · updated {readableDate(entry.updated_at)}
                {entry.updated_by_name && ` by ${entry.updated_by_name}`}</p>
              {deleting?.id === entry.id ? <div className="admin-confirm">
                <p>Delete “{entry.question}”? This can’t be undone.</p>
                <div className="admin-actions">
                  <button type="button" className="button admin-danger" disabled={busy}
                    onClick={() => { setDeleting(null); void run(() => deleteHelpEntry(entry.id), 'Entry deleted.') }}>Yes, delete</button>
                  <button type="button" className="button button-secondary" onClick={() => setDeleting(null)}>Cancel</button>
                </div>
              </div> : <div className="admin-actions">
                <button type="button" className="button button-secondary" disabled={busy || index === 0}
                  aria-label={`Move up: ${entry.question}`} onClick={() => move(index, -1)}>Move up</button>
                <button type="button" className="button button-secondary" disabled={busy || index === entries.length - 1}
                  aria-label={`Move down: ${entry.question}`} onClick={() => move(index, 1)}>Move down</button>
                <button type="button" className="button button-secondary" disabled={busy}
                  aria-label={`Edit: ${entry.question}`} onClick={() => startEditing(entry)}>Edit</button>
                <button type="button" className="button button-secondary" disabled={busy}
                  aria-label={`${entry.published ? 'Unpublish' : 'Publish'}: ${entry.question}`} onClick={() => toggle(entry)}>
                  {entry.published ? 'Unpublish' : 'Publish'}</button>
                <button type="button" className="button button-secondary" disabled={busy}
                  aria-label={`Delete: ${entry.question}`} onClick={() => setDeleting(entry)}>Delete</button>
              </div>}
            </li>)}
          </ol>}
    </section>

    <section className="admin-panel" aria-labelledby="entry-form-heading">
      <h2 id="entry-form-heading">{editing === null ? 'Add an entry' : 'Edit the entry'}</h2>
      <form className="support-form" onSubmit={save}>
        <label className="admin-field">
          <span>Question</span>
          <input id="help-question" maxLength={300} value={draft.question} disabled={busy}
            onChange={event => setDraft({ ...draft, question: event.target.value })} />
        </label>
        <label className="admin-field">
          <span>Category</span>
          <input id="help-category" maxLength={60} value={draft.category} disabled={busy}
            onChange={event => setDraft({ ...draft, category: event.target.value })} />
        </label>
        <label className="admin-field">
          <span>Answer</span>
          <textarea id="help-answer" maxLength={4000} value={draft.answer} disabled={busy}
            onChange={event => setDraft({ ...draft, answer: event.target.value })} />
        </label>
        <p className="admin-muted">Plain text. A blank line starts a new paragraph, lines starting with “- ” make a list,
          **two stars** make bold, and [words](https://…) make a link.</p>
        <label className="admin-field">
          <span>Language</span>
          <select id="help-entry-language" value={draft.language} disabled={busy}
            onChange={event => setDraft({ ...draft, language: event.target.value as HelpLanguage })}>
            {helpLanguages.map(option => <option key={option} value={option}>{helpLanguageLabels[option]}</option>)}
          </select>
        </label>
        <label className="support-check">
          <input id="help-published" type="checkbox" checked={draft.published} disabled={busy}
            onChange={event => setDraft({ ...draft, published: event.target.checked })} />
          Published
        </label>
        {draft.answer.trim() && <div className="admin-card" lang={draft.language} dir="auto">
          <strong>{draft.question || 'Preview'}</strong>
          <RichText text={draft.answer} />
        </div>}
        <div className="admin-actions">
          <button type="submit" className="button button-primary" disabled={busy || !valid}>{editing === null ? 'Add entry' : 'Save entry'}</button>
          {editing !== null && <button type="button" className="button button-secondary" disabled={busy} onClick={stopEditing}>Cancel</button>}
        </div>
      </form>
    </section>
  </div>
}
