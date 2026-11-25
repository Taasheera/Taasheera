import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Link } from 'react-router'
import { protectedFetch } from '../auth'
import type { CheckReport } from '../application/api'
import './formReview.css'

type FormReviewData = {
  title: string
  source: string
  fields: { field: string; source: string; value: string; source_value: string; manual: boolean }[]
  manual: string[]
  report: CheckReport
  confirmation: string | null
}

export default function FormReview({ applicationId, focused }: { applicationId: string; focused: boolean }) {
  const section = useRef<HTMLElement>(null)
  useEffect(() => {
    if (!focused) return
    section.current?.scrollIntoView?.({ block: 'start' })
    section.current?.focus({ preventScroll: true })
  }, [focused])
  const [opened, setOpened] = useState(false)
  const [reviewed, setReviewed] = useState(false)
  const [acknowledged, setAcknowledged] = useState(false)
  const [review, setReview] = useState<FormReviewData | null>(null)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')
  const endpoint = `/applications/${applicationId}/form`

  async function load() {
    setPending(true)
    setError('')
    setReviewed(false)
    setAcknowledged(false)
    try {
      const response = await protectedFetch(endpoint, { cache: 'no-store', silent: true })
      if (!response.ok) throw new Error('Could not load the embassy form.')
      setReview(await response.json() as FormReviewData | null)
      setOpened(true)
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : 'Could not load the embassy form.')
    } finally {
      setPending(false)
    }
  }

  async function download(event: FormEvent) {
    event.preventDefault()
    if (!review?.confirmation || !reviewed || pending) return
    setPending(true)
    setError('')
    try {
      const response = await protectedFetch(`${endpoint}/download`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ confirmation: review.confirmation, reviewed, acknowledged_warnings: acknowledged }),
        silent: true,
      })
      if (!response.ok) {
        const problem = await response.json().catch(() => null) as { detail?: string } | null
        if (response.status === 409) setReviewed(false)
        throw new Error(problem?.detail ?? 'Could not download the embassy form.')
      }
      const url = URL.createObjectURL(await response.blob())
      const link = document.createElement('a')
      link.href = url
      link.download = 'schengen-visa-form.pdf'
      link.click()
      window.setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : 'Could not download the embassy form.')
    } finally {
      setPending(false)
    }
  }

  const prefilled = review?.fields.filter(field => !field.manual && field.value).length ?? 0
  const blocking = review?.report.issues.filter(issue => issue.severity === 'blocking') ?? []
  const warnings = review?.report.issues.filter(issue => issue.severity === 'warning') ?? []
  return <section id="embassy-pdf" className="form-review" aria-labelledby="form-review-heading" ref={section} tabIndex={-1}>
    <div className="form-review-head">
      <div>
        <p className="eyebrow form-review-kicker">Official form</p>
        <h2 id="form-review-heading">Review your embassy PDF</h2>
        <p>Check how your saved answers will appear on the official form before you download it.</p>
      </div>
      <button type="button" className="button button-secondary dash-small" onClick={() => void load()}
        disabled={pending}>{pending ? 'Working…' : opened ? 'Refresh review' : 'Review fields'}</button>
    </div>
    {error && <p className="answer-error" role="alert">{error}</p>}
    {opened && !review && <p>PDF preparation is unavailable for this destination.</p>}
    {review && <>
      <p className="form-review-source"><a href={review.source} target="_blank" rel="noreferrer">{review.title} · European Commission</a></p>
      {!review.report.ready && <>
        <p>Finish these items before reviewing the form.</p>
        <ul>{blocking.map((issue, index) =>
          <li key={index}>{issue.message}</li>)}</ul>
        <Link to={`/apply/${applicationId}`}>Return to your application</Link>
      </>}
      <div className="form-review-body">
        {review.fields.length > 0 && <div className="form-review-table" tabIndex={0} aria-label="Form fields">
          <table>
            <thead><tr><th scope="col">Official field</th><th scope="col">Saved source</th><th scope="col">Value on PDF</th><th scope="col">Status</th></tr></thead>
            <tbody>{review.fields.map(field => <tr key={field.field}>
              <th scope="row">{field.field}</th>
              <td>{field.source_value}<small>{field.source}</small></td>
              <td>{field.manual ? 'Add this value by hand' : field.value}</td>
              <td><span className={`status-dot${field.manual || !field.value ? '' : ' status-dot--ok'}`}>
                {field.manual ? 'Manual' : field.value ? 'Prefilled' : 'Blank'}
              </span></td>
            </tr>)}</tbody>
          </table>
        </div>}
        <aside className="form-review-summary" aria-label="Check summary">
          <h3 className="eyebrow">Check summary</h3>
          <dl className="form-review-counts">
            <div><dt>Prefilled</dt><dd>{prefilled}</dd></div>
            <div><dt>Warnings</dt><dd>{warnings.length}</dd></div>
            <div><dt>To fix</dt><dd>{blocking.length}</dd></div>
          </dl>
          {review.confirmation && <form onSubmit={download} className="form-review-confirm">
            {review.manual.map(note => <p key={note}>{note}</p>)}
            {warnings.length > 0 && <>
              <ul>{warnings.map((issue, index) => <li key={index} className="status-dot status-dot--warn">{issue.message}</li>)}</ul>
              <label><input type="checkbox" checked={acknowledged}
                onChange={event => setAcknowledged(event.target.checked)} /> I reviewed the warnings and want to continue.</label>
            </>}
            <label><input type="checkbox" checked={reviewed}
              onChange={event => setReviewed(event.target.checked)} /> I reviewed these values and confirm the PDF download.</label>
            <button type="submit" className="button button-primary" disabled={pending || !reviewed || (!!warnings.length && !acknowledged)}>
              Download PDF
            </button>
            <p>Download saves a copy for you. You submit it through your consulate’s process.</p>
          </form>}
        </aside>
      </div>
    </>}
  </section>
}
