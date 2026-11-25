import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router'
import { adminRequest } from '../admin'
import { isRecorded, listApplications, type ApplicationSummary } from '../application/api'
import { ArrowIcon } from '../icons'
import ApplicationActions from './ApplicationActions'
import { applicationLabel, applicationName, DISCLAIMER, formatDateTime, tripWhen, useTraveler } from './helpers'
import { SignedOut, StatusChip } from './shared'
import '../ui.css'
import './dashboard.css'

type View = 'active' | 'archived'

const destinationCodes: Record<string, string> = {
  SCHENGEN: 'EU', UK: 'GB', US: 'US', CANADA: 'CA', GBR: 'GB', USA: 'US', CAN: 'CA', CHN: 'CN', JPN: 'JP', IND: 'IN', TUR: 'TR',
}

// Every application the traveler has started, with where it stands (US 19). Archived ones sit
// behind their own tab until restored or deleted (US 79).
export default function Dashboard() {
  const traveler = useTraveler()
  const [view, setView] = useState<View>('active')
  // null while loading; an Error when the list could not be loaded.
  const [applications, setApplications] = useState<ApplicationSummary[] | Error | null>(null)
  const [attempt, setAttempt] = useState(0)
  const heading = useRef<HTMLHeadingElement>(null)

  useEffect(() => { document.title = 'Your applications | Taasheera' }, [])

  // Staff also get a way into the staff dashboard. The server decides; anyone else gets a quiet 403.
  const [staff, setStaff] = useState(false)
  useEffect(() => {
    if (!traveler) return
    let active = true
    adminRequest('/me').then(() => { if (active) setStaff(true) }).catch(() => {})
    return () => { active = false }
  }, [traveler])

  useEffect(() => {
    if (!traveler) return
    let active = true
    listApplications(view === 'archived')
      .then(found => { if (active) setApplications(found) })
      .catch(() => { if (active) setApplications(new Error('Could not load your applications.')) })
    return () => { active = false }
  }, [traveler, view, attempt])

  // Read when a toast's Undo runs, which can be after the traveler switched tabs.
  const viewRef = useRef(view)
  useEffect(() => { viewRef.current = view }, [view])

  // A removed card takes the focus with it, so it moves to the page heading instead of being lost.
  const drop = useCallback((id: string) => {
    setApplications(current => Array.isArray(current) ? current.filter(application => application.id !== id) : current)
    heading.current?.focus()
  }, [])
  // Archiving moves an application to the other tab; Undo brings it back to this one.
  const place = useCallback((updated: ApplicationSummary) => {
    const belongsHere = (updated.archived_at !== null) === (viewRef.current === 'archived')
    setApplications(current => {
      if (!Array.isArray(current)) return current
      const others = current.filter(application => application.id !== updated.id)
      return belongsHere ? [...others, updated].sort((a, b) => b.updated_at.localeCompare(a.updated_at)) : others
    })
    if (!belongsHere) heading.current?.focus()
  }, [])

  if (traveler === undefined) return <section className="dash-page container"><p className="dash-status" role="status">Checking your session…</p></section>
  if (traveler === null) return <section className="dash-page container"><SignedOut /></section>

  const firstName = traveler.name.trim().split(/\s+/)[0]
  const firstVisit = view === 'active' && Array.isArray(applications) && applications.length === 0
  const nextApplication = view === 'active' && Array.isArray(applications)
    ? applications.find(application => application.status === 'ANSWERS_COMPLETE')
      ?? applications.find(application => !isRecorded(application.status))
    : undefined
  const today = new Date()
  const dayLabel = new Intl.DateTimeFormat('en-GB', { weekday: 'long', day: 'numeric', month: 'long' }).format(today)
  return <section className="dash-page container dash-overview-page" aria-labelledby="dash-title">
    <div className="dash-head">
      <div>
        <p className="eyebrow dash-date"><time dateTime={today.toISOString()}>{dayLabel}</time></p>
        <h1 id="dash-title" ref={heading} tabIndex={-1}>
          {firstName ? <span className="dash-welcome">{firstVisit ? 'Welcome' : 'Welcome back'}, {firstName}</span> : 'Your applications'}
        </h1>
      </div>
      <div className="dash-head-actions">
        {staff && <a href="/backoffice" className="button button-secondary">Staff dashboard</a>}
        {!firstVisit && <Link to="/apply" className="button button-primary button-with-icon" data-activity-id="application-new">New application <span className="action-icon" aria-hidden="true">+</span></Link>}
      </div>
    </div>

    {nextApplication && <div className="dash-overview">
      <Link className="dash-overview-next" to={`/apply/${nextApplication.id}`} data-activity-id="application-resume">
        <div className="dash-overview-top"><span className="eyebrow">Next up</span><span aria-hidden="true">↗</span></div>
        <div><strong>{nextApplication.status === 'ANSWERS_COMPLETE' ? 'Review your answers' : 'Continue your application'}</strong>
          <span>{nextApplication.visa_label} · {applicationName(nextApplication)}</span></div>
      </Link>
      <Link className="dash-overview-passport" to="/passport" data-activity-id="passport-link">
        <span className="eyebrow">Passport</span>
        <div><strong>Passport &amp; documents</strong><span>Upload a passport photo or check your details.</span></div>
      </Link>
    </div>}

    <div className="dash-list-heading"><h2>Your applications</h2>
      {Array.isArray(applications) && <span>{applications.length} {view}</span>}
    </div>
    <div className="dash-tabs" role="group" aria-label="Show">
      {(['active', 'archived'] as const).map(option =>
        <button key={option} type="button" className="dash-tab" aria-pressed={view === option}
          data-activity-id="dashboard-filter" onClick={() => { if (option !== view) { setApplications(null); setView(option) } }}>
          {option === 'active' ? 'Active' : 'Archived'}
        </button>)}
    </div>

    {applications === null && <p className="dash-status" role="status">Loading your applications…</p>}
    {applications instanceof Error && <div className="dash-status" role="alert">
      <p>{applications.message}</p>
      <button type="button" className="button button-secondary dash-small" onClick={() => { setApplications(null); setAttempt(count => count + 1) }}>Try again</button>
    </div>}
    {firstVisit && <div className="dash-start">
      <h2>Start your visa application</h2>
      <p>Pick your destination, upload your passport and answer a few questions. We fill in the embassy
        form and list the documents to bring. You review everything before anything is sent.</p>
      <Link to="/apply" className="button button-primary dash-start-cta" data-activity-id="application-new">
        <span className="mini-circle" aria-hidden="true"><ArrowIcon /></span>Start your first application
      </Link>
      <Link to="/passport" className="dash-ready" data-activity-id="passport-link">
        <strong>Not travelling yet?</strong>
        <span>Save your passport now, and your next application starts with it filled in.</span>
      </Link>
    </div>}
    {view === 'archived' && Array.isArray(applications) && applications.length === 0 && <div className="dash-empty">
      <p>Nothing archived. Applications you archive wait here until you restore or delete them.</p>
    </div>}
    {Array.isArray(applications) && applications.length > 0 && <ul className="dash-list">
      {applications.map(application => <li key={application.id} className="dash-card">
        <span className="dash-destination-code" aria-hidden="true">
          {destinationCodes[application.destination] ?? application.destination.slice(0, 2)}
        </span>
        <div className="dash-card-body">
          <h2 className="dash-card-title">
            <Link to={`/dashboard/${application.id}`} data-activity-id="application-details">{applicationName(application)}</Link>
          </h2>
          <p className="dash-meta">
            {application.visa_label}
            {' · '}{tripWhen(application)}
            {application.group_size > 1 && <> · Group of {application.group_size}</>}
          </p>
        </div>
        <div className="dash-row-status">
          {view === 'active' && application.total > 0 && <div className="dash-progress">
            <progress className="question-progress" value={application.answered} max={application.total} aria-label="Questions answered" />
            <span aria-hidden="true">{Math.round(100 * application.answered / application.total)}%</span>
          </div>}
          <div><StatusChip application={application} />
            <span className="dash-updated">Last updated <time dateTime={application.updated_at}>{formatDateTime(application.updated_at)}</time></span>
          </div>
        </div>
        <div className="dash-card-side">
          {view === 'active' && isRecorded(application.status) && <Link to={`/dashboard/${application.id}`}
            className="button button-primary dash-small" data-activity-id="application-details"
            aria-label={`View ${applicationLabel(application)}`}>View</Link>}
          {view === 'active' && !isRecorded(application.status) && <Link to={`/apply/${application.id}`} className="button button-primary dash-small"
            data-activity-id="application-resume" aria-label={`${application.status === 'ANSWERS_COMPLETE' ? 'Review answers for' : 'Resume'} ${applicationLabel(application)}`}>
            {application.status === 'ANSWERS_COMPLETE' ? 'Review answers' : 'Resume'}
          </Link>}
          <ApplicationActions application={application} onArchiveChange={place} onDeleted={drop} />
        </div>
      </li>)}
    </ul>}
    <p className="dash-disclaimer">{DISCLAIMER}</p>
  </section>
}
