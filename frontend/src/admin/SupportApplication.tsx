import { useEffect, useState } from 'react'
import { Link, useOutletContext, useParams } from 'react-router'
import Feature from '../Feature'
import NotFound from '../pages/NotFound'
import { formatAnswer } from '../application/format'
import { statusCopy } from '../dashboard/helpers'
import { AdminRequestError } from './api'
import type { AdminContext } from './AdminLayout'
import { readableDate } from './format'
import { getSharedApplication, type StaffApplicationView } from './supportApi'
import './support.css'

const NOT_SHARED = 'This application is not shared with support, or its access has ended.'

export default function SupportApplication() {
  const { applicationId = '' } = useParams()
  return <Feature name="support_tools" fallback={<NotFound />}><SharedPage key={applicationId} applicationId={applicationId} /></Feature>
}

function SharedPage({ applicationId }: { applicationId: string }) {
  const { leave } = useOutletContext<AdminContext>()
  const valid = /^[0-9a-f]{32}$/.test(applicationId)
  const [view, setView] = useState<StaffApplicationView | null>(null)
  const [problem, setProblem] = useState(valid ? '' : NOT_SHARED)

  useEffect(() => {
    if (!valid) return
    let active = true
    getSharedApplication(applicationId).then(found => { if (active) setView(found) }).catch(error => {
      if (!active) return
      if (error instanceof AdminRequestError && error.status === 403) leave()
      else setProblem(error instanceof AdminRequestError && error.status === 404 ? NOT_SHARED
        : error instanceof Error ? error.message : 'Could not load this application.')
    })
    return () => { active = false }
  }, [applicationId, valid, leave])

  const back = <Link to="/admin/support" className="admin-back">← Support requests</Link>
  if (problem) return <div className="admin-page">{back}<p className="admin-error" role="alert">{problem}</p></div>
  if (!view) return <div className="admin-page">{back}<p className="admin-muted" role="status">Loading the application…</p></div>

  const { application, check, timeline } = view
  const steps = [...new Set(view.answers.map(answer => answer.step))]
  return <div className="admin-page">
    {back}
    <h1>{application.place ?? application.destination_label} · {application.title}</h1>
    <p className="admin-muted">{view.traveler_name} shared this application until {readableDate(view.access_expires_at)}.
      Read only; passport images and documents are not shown. Every view is recorded in the traveler’s access history.</p>
    <dl className="admin-details">
      <div><dt>Status</dt><dd>{statusCopy[application.status]}</dd></div>
      <div><dt>Answered</dt><dd>{application.answered} of {application.total}</dd></div>
      <div><dt>Started</dt><dd>{readableDate(application.created_at)}</dd></div>
      <div><dt>Archived</dt><dd>{application.archived_at ? readableDate(application.archived_at) : 'No'}</dd></div>
    </dl>

    <section className="admin-panel" aria-labelledby="check-heading">
      <h2 id="check-heading">Completeness check</h2>
      <p>{check.ready ? 'Ready for review.' : 'Not ready yet.'} {check.readiness_reason}</p>
      {check.issues.length === 0 ? <p className="admin-muted">Nothing is missing.</p> : <ul className="support-list support-issues">
        {check.issues.map((issue, index) => <li key={index} className={issue.severity === 'blocking' ? 'is-blocking' : undefined}>
          <strong>{issue.severity === 'blocking' ? 'Blocking' : 'Warning'}</strong>
          <p>{issue.message}</p>
        </li>)}
      </ul>}
    </section>

    <section className="admin-panel" aria-labelledby="answers-heading">
      <h2 id="answers-heading">Answers</h2>
      {view.answers.length === 0 && <p className="admin-muted">No answers yet.</p>}
      {steps.map(step => <div key={step}>
        <h3>{step}</h3>
        <dl className="support-answers">
          {view.answers.filter(answer => answer.step === step).map(answer => <div key={answer.question_id}>
            <dt>{answer.prompt}</dt>
            <dd>{formatAnswer(answer, answer.value)}</dd>
          </div>)}
        </dl>
      </div>)}
    </section>

    <section className="admin-panel" aria-labelledby="timeline-heading">
      <h2 id="timeline-heading">Timeline</h2>
      <ol className="support-list">
        {timeline.events.map((event, index) => <li key={index}>
          <p className="admin-muted">{readableDate(event.at)}</p>
          <p>{event.kind === 'answered' ? `Answered: ${event.text}` : event.text}</p>
        </li>)}
      </ol>
      <p><strong>Next:</strong> {timeline.next.text}</p>
    </section>
  </div>
}
