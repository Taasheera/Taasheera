import { useCallback, useEffect, useRef, useState } from 'react'
import { adminRequest, type RequestWindow, type SystemHealth } from '../admin'

const REFRESH_MS = 15_000
const LABELS = { ok: 'OK', warn: 'Warning', fail: 'Failing' } as const

function ms(value: number | null) { return value === null ? '—' : `${Math.round(value)} ms` }
function percent(value: number | null | undefined) { return value === null || value === undefined ? '—' : `${value}%` }
function age(seconds: number | null) {
  if (seconds === null) return 'none'
  if (seconds < 120) return `${seconds} s`
  return seconds < 7200 ? `${Math.round(seconds / 60)} min` : `${Math.round(seconds / 3600)} h`
}

function Traffic({ label, window: stats }: { label: string; window: RequestWindow }) {
  return <tr><th scope="row">{label}</th><td>{stats.requests}</td><td>{stats.requests_per_second}</td><td>{ms(stats.p50_ms)}</td>
    <td>{ms(stats.p95_ms)}</td><td>{ms(stats.p99_ms)}</td><td>{ms(stats.db_p95_ms)}</td>
    <td>{(stats.error_rate * 100).toFixed(2)}%</td><td>{stats.peak_in_flight}</td></tr>
}

/** Operator health (Sprint 5 infrastructure monitoring): resources, latency, queue, email, payments, storage. */
export default function SystemHealthPanel() {
  const [health, setHealth] = useState<SystemHealth | null>(null)
  const [error, setError] = useState('')
  const live = useRef(true)

  const load = useCallback(() => adminRequest<SystemHealth>('/system-health').then(result => {
    if (live.current) { setHealth(result); setError('') }
  }).catch(problem => {
    if (live.current) setError(problem instanceof Error ? problem.message : 'Could not load system health.')
  }), [])

  useEffect(() => {
    live.current = true
    void load()
    const timer = window.setInterval(() => { void load() }, REFRESH_MS)
    return () => { live.current = false; window.clearInterval(timer) }
  }, [load])

  return <section aria-labelledby="system-health-heading" className="system-health">
    <h2 id="system-health-heading">System health</h2>
    <p>Refreshes every 15 seconds. Numbers only: no traveler data. Errors and traces are in Sentry; host CPU credits and edge errors are on the CloudWatch dashboard.</p>
    {error && <p role="alert" className="auth-error">{error}</p>}
    {!health && !error && <p role="status">Loading system health…</p>}
    {health && <>
      <p className={`health-overall health-${health.status}`} role="status">
        {LABELS[health.status]} · {health.environment} · release {health.release.replace(/^taasheera@/, '').slice(0, 12)} · checked {new Date(health.checked_at).toLocaleTimeString()}
      </p>
      <ul className="health-checks">{health.checks.map(check =>
        <li key={check.component} className={`health-${check.status}`}><strong>{check.component}</strong> <span>{LABELS[check.status]}</span> {check.detail}</li>)}</ul>
      <h3>API traffic (this process)</h3>
      <div className="table-scroll"><table>
        <thead><tr><th>Window</th><th>Requests</th><th>Req/s</th><th>p50</th><th>p95</th><th>p99</th><th>DB p95</th><th>Errors</th><th>Peak in flight</th></tr></thead>
        <tbody><Traffic label="1 min" window={health.api.last_1m} /><Traffic label="5 min" window={health.api.last_5m} /><Traffic label="15 min" window={health.api.last_15m} /></tbody>
      </table></div>
      <div className="status-grid">
        <div className="status-card"><span>CPU</span><strong>{percent(health.host.cpu_percent)}</strong><small>{health.host.cpu_count ?? '—'} vCPU · load {health.host.load_average?.join(' / ') ?? '—'}</small></div>
        <div className="status-card"><span>Memory used</span><strong>{percent(health.host.memory.used_percent)}</strong><small>{health.host.memory.available_mb ?? '—'} MB free · API {health.process.rss_mb ?? '—'} MB</small></div>
        <div className="status-card"><span>Data disk</span><strong>{percent(health.storage.used_percent)}</strong><small>{health.storage.free_gb} GB free · {health.storage.writable ? 'writable' : 'NOT writable'}</small></div>
        <div className="status-card"><span>Root disk</span><strong>{percent(health.host.root_disk.used_percent)}</strong><small>{health.host.root_disk.free_gb} GB free</small></div>
        <div className="status-card"><span>Database</span><strong>{health.database.reachable ? ms(health.database.probe_ms) : 'down'}</strong><small>{health.database.backend} · {health.database.size_mb ?? '—'} MB</small></div>
        <div className="status-card"><span>Agent queue</span><strong>{health.queue.queued} waiting</strong><small>{health.queue.running} running · oldest {age(health.queue.oldest_queued_seconds)}</small></div>
        <div className="status-card"><span>Email backlog</span><strong>{health.notifications.email_pending}</strong><small>oldest {age(health.notifications.oldest_pending_seconds)} · {health.notifications.email_failed_last_day} failed today</small></div>
        {health.payments && <div className="status-card"><span>Payments (24 h)</span><strong>{String(health.payments.paid ?? 0)} paid</strong><small>{String(health.payments.pending ?? 0)} pending · {String(health.payments.failed ?? 0)} failed</small></div>}
        <div className="status-card"><span>API process</span><strong>{age(health.process.uptime_seconds)}</strong><small>{health.process.workers} worker(s) · {health.api.in_flight} in flight</small></div>
      </div>
    </>}
  </section>
}
