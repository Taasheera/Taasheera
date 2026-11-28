import { protectedFetch } from '../auth'
import { ADMIN_REQUIRED, AdminRequestError } from './api'

export type DayCount = { day: string; count: number }
export type Kpis = {
  start: string; end: string; days: number
  usage: {
    active_users: number; average_daily_active: number; daily_active: DayCount[]
    weekly_active: { week_start: string; count: number }[]
    registrations: number; registrations_by_method: Record<string, number>
    sign_ins: number; sign_in_successes: number; sign_in_failures: number; sign_ins_suspended: number
    sign_in_success_rate: number | null; sign_in_failure_rate: number | null
  }
  applications: {
    started: number; completed: number; submitted: number; decided: number; approved: number; refused: number
    ineligible: number; funnel: { stage: 'started' | 'completed' | 'submitted' | 'decided'; count: number; conversion: number | null }[]
    median_hours_to_complete: number | null
  }
  agent: {
    runs: number; ready: number; stopped: number; site_changed: number; failed: number; cancelled: number; in_progress: number
    success_rate: number | null; failure_rate: number | null; site_changed_rate: number | null
  }
  assistant: { messages: number; answered: number; proposals: number; drafts: number; unavailable: number; unavailable_rate: number | null; users: number }
  api_errors: number
  api_errors_daily: DayCount[]
  destinations: { questionnaire_id: string; title: string; started: number; completed: number; submitted: number; decided: number; agent_runs: number; agent_ready: number }[]
}

export const MAX_DAYS = 180

export async function getKpis(start: string, end: string): Promise<Kpis> {
  const query = new URLSearchParams({ start, end })
  let response: Response
  try {
    response = await protectedFetch(`/admin-api/kpis?${query}`, { cache: 'no-store', silent: true })
  } catch (error) {
    throw new AdminRequestError(error instanceof Error ? error.message : 'Could not reach Taasheera.', null)
  }
  if (response.ok) return await response.json() as Kpis
  const fallback = response.status === 403 ? ADMIN_REQUIRED
    : response.status === 422 ? `Choose a start on or before the end, at most ${MAX_DAYS} days apart.`
      : 'Something went wrong. Please try again.'
  throw new AdminRequestError(fallback, response.status)
}

function cell(value: string | number | null): string {
  const text = value === null ? '' : String(value)
  return /[",\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text
}

export function kpisToCsv(kpis: Kpis): string {
  const rows: (string | number | null)[][] = [['section', 'item', 'metric', 'value'], ['range', '', 'start', kpis.start],
    ['range', '', 'end', kpis.end], ['range', '', 'days', kpis.days]]
  const add = (section: string, item: string, values: object) => {
    for (const [metric, value] of Object.entries(values)) {
      if (value === null || typeof value !== 'object') rows.push([section, item, metric, value as string | number | null])
    }
  }
  add('usage', '', kpis.usage)
  for (const [method, count] of Object.entries(kpis.usage.registrations_by_method)) rows.push(['usage', method, 'registrations', count])
  for (const day of kpis.usage.daily_active) rows.push(['daily_active', day.day, 'users', day.count])
  for (const week of kpis.usage.weekly_active) rows.push(['weekly_active', week.week_start, 'users', week.count])
  add('applications', '', kpis.applications)
  for (const stage of kpis.applications.funnel) {
    rows.push(['funnel', stage.stage, 'count', stage.count], ['funnel', stage.stage, 'conversion', stage.conversion])
  }
  add('agent', '', kpis.agent)
  add('assistant', '', kpis.assistant)
  rows.push(['api', '', 'errors', kpis.api_errors])
  for (const day of kpis.api_errors_daily) rows.push(['api_errors_daily', day.day, 'errors', day.count])
  for (const { questionnaire_id, started, completed, submitted, decided, agent_runs, agent_ready } of kpis.destinations) {
    add('destination', questionnaire_id, { started, completed, submitted, decided, agent_runs, agent_ready })
  }
  return rows.map(row => row.map(cell).join(',')).join('\r\n') + '\r\n'
}
