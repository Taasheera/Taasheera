import { useEffect, useState, type FormEvent } from 'react'
import { useOutletContext, useSearchParams } from 'react-router'
import Feature from '../Feature'
import NotFound from '../pages/NotFound'
import { AdminRequestError } from './api'
import type { AdminContext } from './AdminLayout'
import { getKpis, kpisToCsv, MAX_DAYS, type DayCount, type Kpis } from './kpisApi'
import './backoffice.css'

const DAY = 86_400_000
const ISO_DAY = /^\d{4}-\d{2}-\d{2}$/
const STAGES = { started: 'Started', completed: 'Answers complete', submitted: 'Recorded as submitted', decided: 'Decision recorded' }

const isoDay = (date: Date) => date.toISOString().slice(0, 10)
const shift = (day: string, days: number) => isoDay(new Date(Date.parse(`${day}T00:00:00Z`) + days * DAY))
const percent = (rate: number | null) => (rate === null ? '–' : `${(rate * 100).toFixed(1)}%`)

function duration(hours: number | null): string {
  if (hours === null) return '–'
  return hours < 48 ? `${hours.toFixed(1)} h` : `${(hours / 24).toFixed(1)} days`
}

function Columns({ title, data, unit }: { title: string; data: DayCount[]; unit: string }) {
  const highest = Math.max(1, ...data.map(item => item.count))
  return <figure className="backoffice-chart">
    <figcaption>{title}</figcaption>
    <div className="backoffice-columns-frame">
      <span className="backoffice-axis" aria-hidden="true">{highest}</span>
      <ol className="backoffice-columns">
        {data.map(item => <li key={item.day} title={`${item.day}: ${item.count} ${unit}`}>
          <span className="backoffice-column" style={{ height: `${(item.count / highest) * 100}%` }} />
          <span className="visually-hidden">{item.day}: {item.count} {unit}</span>
        </li>)}
      </ol>
    </div>
    <div className="backoffice-columns-dates" aria-hidden="true"><span>{data[0]?.day}</span><span>{data.at(-1)?.day}</span></div>
  </figure>
}

function Bars({ title, rows }: { title: string; rows: { label: string; value: number; note?: string }[] }) {
  const highest = Math.max(1, ...rows.map(row => row.value))
  return <figure className="backoffice-chart">
    <figcaption>{title}</figcaption>
    <dl className="backoffice-bars">
      {rows.map(row => <div key={row.label} className="backoffice-bar-row">
        <dt>{row.label}</dt>
        <dd>
          <span className="backoffice-track"><span className="backoffice-bar" style={{ width: `${(row.value / highest) * 100}%` }} /></span>
          <span className="backoffice-bar-value">{row.value}{row.note && <span className="admin-muted"> · {row.note}</span>}</span>
        </dd>
      </div>)}
    </dl>
  </figure>
}

function Cards({ cards }: { cards: [string, string | number][] }) {
  return <dl className="admin-cards">
    {cards.map(([label, value]) => <div key={label} className="admin-card"><dt>{label}</dt><dd>{value}</dd></div>)}
  </dl>
}

function download(kpis: Kpis) {
  const url = URL.createObjectURL(new Blob([kpisToCsv(kpis)], { type: 'text/csv;charset=utf-8' }))
  const link = document.createElement('a')
  link.href = url
  link.download = `taasheera-kpis-${kpis.start}-to-${kpis.end}.csv`
  document.body.append(link)
  link.click()
  link.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

function KpiDashboard() {
  const { leave } = useOutletContext<AdminContext>()
  const [params, setParams] = useSearchParams()
  const today = isoDay(new Date())
  const [endParam, startParam] = [params.get('end') ?? '', params.get('start') ?? '']
  const end = ISO_DAY.test(endParam) ? endParam : today
  const start = ISO_DAY.test(startParam) ? startParam : shift(end, -29)
  const [draft, setDraft] = useState({ start, end })
  const [kpis, setKpis] = useState<Kpis | null>(null)
  const [error, setError] = useState('')
  const [loaded, setLoaded] = useState('')
  const loading = loaded !== `${start}/${end}`

  useEffect(() => {
    document.title = 'KPIs | Taasheera'
    let active = true
    getKpis(start, end).then(found => { if (active) { setKpis(found); setError('') } }, problem => {
      if (!active) return
      if (problem instanceof AdminRequestError && problem.status === 403) leave()
      else setError(problem instanceof Error ? problem.message : 'Could not load the KPIs.')
    }).finally(() => { if (active) setLoaded(`${start}/${end}`) })
    return () => { active = false }
  }, [start, end, leave])

  function choose(range: { start: string; end: string }) {
    setDraft(range)
    setParams(range)
  }

  function submit(event: FormEvent) {
    event.preventDefault()
    choose(draft)
  }

  return <div className="admin-page">
    <h1>KPIs</h1>
    <p className="admin-muted">Usage, completion and agent success from the activity log. Totals only: no names, emails,
      accounts or answers. Traveler accounts only; staff activity is left out.</p>
    <form className="admin-filters" aria-label="Date range" onSubmit={submit}>
      <label className="admin-field" htmlFor="kpi-start"><span>From</span>
        <input id="kpi-start" type="date" value={draft.start} max={draft.end}
          onChange={event => setDraft({ ...draft, start: event.target.value })} /></label>
      <label className="admin-field" htmlFor="kpi-end"><span>To</span>
        <input id="kpi-end" type="date" value={draft.end} min={draft.start}
          onChange={event => setDraft({ ...draft, end: event.target.value })} /></label>
      <button type="submit" className="button button-primary">Show</button>
      {[7, 30, 90, MAX_DAYS].map(days => <button key={days} type="button" className="button button-secondary"
        onClick={() => choose({ start: shift(today, 1 - days), end: today })}>Last {days} days</button>)}
    </form>
    <p className="admin-muted" role="status" aria-live="polite">{loading ? 'Loading…'
      : kpis && `${kpis.start} to ${kpis.end} (${kpis.days} days, UTC)`}</p>
    {error && <p className="admin-error" role="alert">{error}</p>}
    {kpis && <>
      <div className="admin-actions"><button type="button" className="button button-secondary" onClick={() => download(kpis)}>
        Download CSV</button></div>

      <section className="admin-panel" aria-labelledby="kpi-usage-heading">
        <h2 id="kpi-usage-heading">Usage</h2>
        <Cards cards={[
          ['Active users', kpis.usage.active_users],
          ['Average daily active', kpis.usage.average_daily_active],
          ['Registrations', kpis.usage.registrations],
          ['Sign-in success', percent(kpis.usage.sign_in_success_rate)],
        ]} />
        <Columns title="Daily active users" data={kpis.usage.daily_active} unit="users" />
        <Bars title="Weekly active users (by week starting)" rows={kpis.usage.weekly_active.map(week =>
          ({ label: week.week_start, value: week.count }))} />
        <Bars title="Sign-ins" rows={[
          { label: 'Succeeded', value: kpis.usage.sign_in_successes, note: percent(kpis.usage.sign_in_success_rate) },
          { label: 'Wrong password', value: kpis.usage.sign_in_failures, note: percent(kpis.usage.sign_in_failure_rate) },
          { label: 'Suspended account', value: kpis.usage.sign_ins_suspended },
        ]} />
        <p className="admin-muted">Attempts for emails without an account are not in the activity log.</p>
      </section>

      <section className="admin-panel" aria-labelledby="kpi-applications-heading">
        <h2 id="kpi-applications-heading">Applications</h2>
        <Cards cards={[
          ['Started', kpis.applications.started],
          ['Answers complete', kpis.applications.completed],
          ['Median time to complete', duration(kpis.applications.median_hours_to_complete)],
          ['Stopped as ineligible', kpis.applications.ineligible],
        ]} />
        <Bars title="Funnel: applications started in the range and how far they have got" rows={kpis.applications.funnel.map(stage =>
          ({ label: STAGES[stage.stage], value: stage.count, note: stage.conversion === null ? undefined : `${percent(stage.conversion)} of the stage before` }))} />
        <p className="admin-muted">Decisions: {kpis.applications.approved} approved, {kpis.applications.refused} refused.</p>
      </section>

      <section className="admin-panel" aria-labelledby="kpi-agent-heading">
        <h2 id="kpi-agent-heading">Form-filling agent and assistant</h2>
        <Cards cards={[
          ['Agent success', percent(kpis.agent.success_rate)],
          ['Agent failures', percent(kpis.agent.failure_rate)],
          ['Site changed', percent(kpis.agent.site_changed_rate)],
          ['Assistant unavailable', percent(kpis.assistant.unavailable_rate)],
        ]} />
        <Bars title={`Agent runs (${kpis.agent.runs})`} rows={[
          { label: 'Filled, ready for the traveler', value: kpis.agent.ready },
          { label: 'Stopped (CAPTCHA, unknown field)', value: kpis.agent.stopped - kpis.agent.site_changed },
          { label: 'Stopped: the site changed', value: kpis.agent.site_changed },
          { label: 'Failed', value: kpis.agent.failed },
          { label: 'Cancelled', value: kpis.agent.cancelled },
          { label: 'Queued or running', value: kpis.agent.in_progress },
        ]} />
        <Bars title={`Assistant messages (${kpis.assistant.messages}, from ${kpis.assistant.users} travelers)`} rows={[
          { label: 'Answered', value: kpis.assistant.answered },
          { label: 'Proposed a change', value: kpis.assistant.proposals },
          { label: 'Drafted an answer', value: kpis.assistant.drafts },
          { label: 'Unavailable', value: kpis.assistant.unavailable },
        ]} />
        <p className="admin-muted">Agent rates are of finished runs: ready, stopped or failed.</p>
      </section>

      <section className="admin-panel" aria-labelledby="kpi-errors-heading">
        <h2 id="kpi-errors-heading">API errors</h2>
        <Cards cards={[['Server errors on signed-in requests', kpis.api_errors]]} />
        <Columns title="API errors per day" data={kpis.api_errors_daily} unit="errors" />
      </section>

      <section className="admin-panel" aria-labelledby="kpi-destinations-heading">
        <h2 id="kpi-destinations-heading">By destination</h2>
        <div className="admin-table-wrap"><table className="admin-table">
          <thead><tr><th scope="col">Destination</th><th scope="col">Started</th><th scope="col">Complete</th>
            <th scope="col">Submitted</th><th scope="col">Decided</th><th scope="col">Agent runs</th><th scope="col">Agent ready</th></tr></thead>
          <tbody>{kpis.destinations.map(destination => <tr key={destination.questionnaire_id}>
            <th scope="row">{destination.title}</th><td>{destination.started}</td><td>{destination.completed}</td>
            <td>{destination.submitted}</td><td>{destination.decided}</td><td>{destination.agent_runs}</td>
            <td>{destination.agent_ready}</td>
          </tr>)}</tbody>
        </table></div>
      </section>
    </>}
  </div>
}

export default function KpiPage() {
  return <Feature name="kpi_dashboard" fallback={<NotFound />}><KpiDashboard /></Feature>
}
