import { useCallback, useEffect, useState } from 'react'
import { formatDateTime } from '../dashboard/helpers'
import {
  ACTIVE, cancelRun, getAgentState, getRun, screenshotUrl, startRun,
  type AgentState, type Run, type RunDetail, type Screenshot,
} from './api'
import './agent.css'

const POLL_MS = 2500
const STATUS_TEXT: Record<Run['status'], string> = {
  queued: 'Waiting for the agent…',
  running: 'The agent is filling the form…',
  ready: 'Filled. Waiting for you to review and submit.',
  stopped: 'The agent stopped',
  failed: 'The agent couldn’t finish',
  cancelled: 'Cancelled',
}

// The form-filling agent on the application page (CMPS271-67): start it, follow it, and see every
// field it typed with a screenshot of each page (CMPS271-70). It never submits; the traveler does.
export default function AgentPanel({ applicationId, version }: { applicationId: string; version?: string }) {
  const [state, setState] = useState<AgentState | Error | null>(null)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState('')

  const load = useCallback(() => getAgentState(applicationId)
    .then(setState)
    .catch(failure => setState(failure instanceof Error ? failure : new Error('Could not load the agent.'))), [applicationId])

  useEffect(() => { void load() }, [load, version])

  const latest = state && !(state instanceof Error) ? state.runs[0] : undefined
  const active = latest ? ACTIVE.includes(latest.status) : false
  useEffect(() => {
    if (!active) return
    const timer = setInterval(() => { void load() }, POLL_MS)
    return () => clearInterval(timer)
  }, [active, load])

  async function act(action: () => Promise<unknown>) {
    setBusy(true)
    setProblem('')
    try {
      await action()
      await load()
    } catch (failure) {
      setProblem(failure instanceof Error ? failure.message : 'Something went wrong. Please try again.')
    } finally {
      setBusy(false)
    }
  }

  if (state === null) return null
  if (state instanceof Error) return <section className="agent" aria-labelledby="agent-title">
    <h2 id="agent-title">Embassy form</h2>
    <p className="agent-error" role="alert">{state.message}</p>
  </section>

  return <section className="agent" aria-labelledby="agent-title">
    <div className="agent-head">
      <div>
        <h2 id="agent-title">Embassy form</h2>
        <p className="agent-lede">
          {state.site?.is_demo
            ? 'Preview how the agent fills a local practice form using your saved answers.'
            : state.site
            ? <>The agent types your confirmed answers into the {state.site.name}, saves a screenshot of every page and stops before submitting. You review and submit it yourself.</>
            : 'The agent can’t fill this destination’s online form yet.'}
        </p>
      </div>
      {state.site && <div className="agent-actions">
        {active && latest
          ? <button type="button" className="button button-secondary dash-small" disabled={busy}
              onClick={() => act(() => cancelRun(applicationId, latest.id))} data-activity-id="agent-cancel">Cancel</button>
          : <button type="button" className="button button-primary dash-small" disabled={busy || !state.can_start}
              aria-describedby={state.reason ? 'agent-reason' : undefined}
              onClick={() => act(() => startRun(applicationId))} data-activity-id="agent-start">
              {state.site.is_demo ? 'Run form preview' : latest ? 'Fill the form again' : 'Fill the embassy form'}
            </button>}
      </div>}
    </div>
    {!state.can_start && state.reason && !active && <p id="agent-reason" className="agent-note">{state.reason}</p>}
    {problem && <p className="agent-error" role="alert">{problem}</p>}
    {latest && <RunView applicationId={applicationId} run={latest} demo={state.site?.is_demo} />}
    <p className="agent-disclaimer">Nothing is submitted to an embassy without you. Ta’asheera doesn’t give legal or immigration advice.</p>
  </section>
}

function RunView({ applicationId, run, demo }: { applicationId: string; run: Run; demo?: boolean }) {
  const [detail, setDetail] = useState<RunDetail | Error | null>(null)

  useEffect(() => {
    let active = true
    let timer: number | undefined
    async function refresh() {
      try {
        const result = await getRun(applicationId, run.id)
        if (active) setDetail(result)
      } catch (failure) {
        if (active) setDetail(failure instanceof Error ? failure : new Error('Could not load what the agent did.'))
      }
      if (active && ACTIVE.includes(run.status)) timer = window.setTimeout(refresh, POLL_MS)
    }
    void refresh()
    return () => { active = false; window.clearTimeout(timer) }
  }, [applicationId, run.id, run.status])

  const ended = !ACTIVE.includes(run.status)
  const runDetail = detail && !(detail instanceof Error) ? detail : null
  const lastEntry = runDetail?.entries.at(-1)
  const lastShot = runDetail?.screenshots.at(-1)
  const currentPage = (lastEntry?.page ?? 0) > (lastShot?.page ?? 0) ? lastEntry : lastShot
  return <div className={`agent-run is-${run.status}`}>
    <p className="agent-status status-dot" role="status">
      <strong>{demo && run.status === 'ready' ? 'Preview complete. Review the filled fields below.' : STATUS_TEXT[run.status]}</strong>
      {run.status !== 'ready' && run.message && ended && <>: {run.message}</>}
      {run.finished_at && <span className="agent-when"> · {formatDateTime(run.finished_at)}</span>}
    </p>
    {!ended && currentPage && <p className="agent-note" role="status">
      Page {currentPage.page} · {currentPage.page_title} · {runDetail?.entries.length} fields filled
    </p>}
    {!ended && lastShot && <ScreenshotView key={lastShot.id} applicationId={applicationId} runId={run.id} shot={lastShot} auto />}
    {detail instanceof Error && <p className="agent-error" role="alert">{detail.message}</p>}
    {detail && !(detail instanceof Error) && detail.entries.length > 0 && <AuditTrail applicationId={applicationId} detail={detail} />}
  </div>
}

function AuditTrail({ applicationId, detail }: { applicationId: string; detail: RunDetail }) {
  const pages = [...new Set(detail.entries.map(entry => entry.page).concat(detail.screenshots.map(shot => shot.page)))].sort((a, b) => a - b)
  return <details className="agent-audit" open={detail.status === 'ready' || ACTIVE.includes(detail.status)}>
    <summary>What the agent typed: {detail.entries.length} {detail.entries.length === 1 ? 'field' : 'fields'} on {pages.length} {pages.length === 1 ? 'page' : 'pages'}</summary>
    <p className="agent-note">Run {detail.id.slice(0, 8)} · Check every value against your documents before you submit.</p>
    {pages.map(page => {
      const entries = detail.entries.filter(entry => entry.page === page)
      const shot = detail.screenshots.find(screenshot => screenshot.page === page)
      const title = entries[0]?.page_title ?? shot?.page_title ?? `Page ${page}`
      return <div key={page} className="agent-page">
        <h3>Page {page}: {title}</h3>
        {entries.length > 0 && <table className="agent-table">
          <thead><tr><th scope="col">Field</th><th scope="col">Value typed</th><th scope="col">Time</th></tr></thead>
          <tbody>
            {entries.map((entry, index) => <tr key={`${entry.field}-${index}`}>
              <th scope="row">{entry.field}</th>
              <td className="agent-value">{entry.value}</td>
              <td><time dateTime={entry.entered_at}>{formatDateTime(entry.entered_at)}</time></td>
            </tr>)}
          </tbody>
        </table>}
        {shot && <ScreenshotView applicationId={applicationId} runId={detail.id} shot={shot} />}
      </div>
    })}
  </details>
}

function ScreenshotView({ applicationId, runId, shot, auto = false }:
  { applicationId: string; runId: string; shot: Screenshot; auto?: boolean }) {
  const [url, setUrl] = useState<string | null>(null)
  const [failed, setFailed] = useState(false)

  async function show() {
    try {
      setUrl(await screenshotUrl(applicationId, runId, shot.id))
    } catch {
      setFailed(true)
    }
  }
  useEffect(() => {
    if (!auto) return
    let active = true
    void screenshotUrl(applicationId, runId, shot.id).then(result => {
      if (active) setUrl(result)
      else URL.revokeObjectURL(result)
    }).catch(() => { /* The latest screenshot may still be writing. Keep the manual retry. */ })
    return () => { active = false }
  }, [auto, applicationId, runId, shot.id])

  useEffect(() => () => { if (url) URL.revokeObjectURL(url) }, [url])

  if (failed) return <p className="agent-error">Could not load the screenshot.</p>
  if (!url) return <button type="button" className="button button-secondary dash-small" onClick={show}
    data-activity-id="agent-screenshot">Show screenshot of this page</button>
  return <figure className="agent-shot">
    <img src={url} alt={`Screenshot of page ${shot.page}, ${shot.page_title}, after the agent filled it`} />
    <figcaption>Taken {formatDateTime(shot.taken_at)}</figcaption>
  </figure>
}
