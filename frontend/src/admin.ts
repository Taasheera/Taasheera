import { protectedFetch } from './auth'

export type Staff = { id: number; name: string; email: string; role: 'admin' }
export type AdminUser = { id: number; name: string; email: string; role: string; status: string }
export type UserPage = { items: AdminUser[]; total: number; page: number; page_size: number }
export type Monitoring = {
  applications_by_status: Record<string, number>; total_applications: number
  recent_errors: { id: number; occurred_at: string; event_type: string; source: string; outcome: string }[]
  agent_problems?: { site: string; kind: 'site_changed' | 'failed'; occurred_at: string }[]
  agent_failures_available: boolean; pending_erasures: number
}

export type HealthLevel = 'ok' | 'warn' | 'fail'
export type RequestWindow = { window_seconds: number; requests: number; requests_per_second: number; error_rate: number
  throttled: number; p50_ms: number | null; p95_ms: number | null; p99_ms: number | null; db_p95_ms: number | null; peak_in_flight: number }
export type SystemHealth = {
  status: HealthLevel; checked_at: string; release: string; environment: string
  process: { pid: number; uptime_seconds: number; rss_mb: number | null; workers: number }
  api: { in_flight: number; last_1m: RequestWindow; last_5m: RequestWindow; last_15m: RequestWindow }
  host: { cpu_count: number | null; cpu_percent: number | null; load_average: number[] | null
    memory: { total_mb: number | null; available_mb: number | null; used_percent: number | null; swap_used_mb: number | null }
    root_disk: { total_gb: number; free_gb: number; used_percent: number | null } }
  database: { reachable: boolean; probe_ms: number; backend: string; size_mb: number | null; schema_revision: string | null }
  queue: { queued: number; running: number; oldest_queued_seconds: number | null; finished_last_hour: Record<string, number> }
  notifications: { email_pending: number; email_failed_last_day: number; oldest_pending_seconds: number | null }
  payments: Record<string, unknown> | null
  storage: { writable: boolean; total_gb: number; free_gb: number; used_percent: number | null }
  checks: { component: string; status: HealthLevel; detail: string }[]
}

export class AdminAccessError extends Error {}

export async function adminRequest<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await protectedFetch(`/admin-api${path}`, { ...options, cache: 'no-store', silent: true })
  if ([401, 403].includes(response.status)) throw new AdminAccessError('An active admin account is required. Sign in with an authorized account.')
  if (!response.ok) {
    const messages: Record<number, string> = { 404: 'This user no longer exists. Refresh the list.',
      409: 'This action is unavailable. Admin accounts must transfer their role before deletion, and pending erasures cannot be changed.' }
    throw new Error(messages[response.status] ?? 'Could not complete the admin request. Please try again.')
  }
  if ([202, 204].includes(response.status)) return { pending: response.status === 202 } as T
  return response.json()
}

export function deleteUser(id: number): Promise<{ pending: boolean }> {
  return adminRequest(`/users/${id}`, { method: 'DELETE', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ confirmation: 'DELETE', expected_user_id: id }) })
}
