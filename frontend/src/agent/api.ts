import { protectedFetch } from '../auth'

// Mirrors backend/app/agent/router.py (CMPS271-67, CMPS271-70). The server decides whether the
// agent may start; the agent fills the embassy form and stops before submitting.
export type RunStatus = 'queued' | 'running' | 'ready' | 'stopped' | 'failed' | 'cancelled'
export type Run = {
  id: string
  site: string
  status: RunStatus
  message: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
}
export type Entry = { page: number; page_title: string; field: string; value: string; entered_at: string }
export type Screenshot = { id: string; page: number; page_title: string; taken_at: string }
export type RunDetail = Run & { entries: Entry[]; screenshots: Screenshot[] }
export type AgentState = { site: { id: string; name: string; is_demo?: boolean } | null; can_start: boolean; reason: string | null; payment_required?: boolean; runs: Run[] }

export const ACTIVE: RunStatus[] = ['queued', 'running']

async function detail(response: Response, fallback: string) {
  const problem = await response.json().catch(() => null) as { detail?: unknown } | null
  return typeof problem?.detail === 'string' ? problem.detail : fallback
}

const base = (applicationId: string) => `/applications/${applicationId}/agent`

export async function getAgentState(applicationId: string): Promise<AgentState> {
  const response = await protectedFetch(base(applicationId), { cache: 'no-store' })
  if (!response.ok) throw new Error('Could not load the form-filling agent.')
  return response.json()
}

export async function startRun(applicationId: string): Promise<Run> {
  const response = await protectedFetch(`${base(applicationId)}/runs`, { method: 'POST', cache: 'no-store', silent: true })
  if (!response.ok) throw new Error(await detail(response, 'Could not start the agent. Please try again.'))
  return response.json()
}

export async function cancelRun(applicationId: string, runId: string): Promise<Run> {
  const response = await protectedFetch(`${base(applicationId)}/runs/${runId}/cancel`, { method: 'POST', cache: 'no-store', silent: true })
  if (!response.ok) throw new Error('Could not cancel the agent. Please try again.')
  return response.json()
}

export async function getRun(applicationId: string, runId: string): Promise<RunDetail> {
  const response = await protectedFetch(`${base(applicationId)}/runs/${runId}`, { cache: 'no-store' })
  if (!response.ok) throw new Error('Could not load what the agent did.')
  return response.json()
}

// Screenshots need the sign-in token, so they're fetched and shown as object URLs.
export async function screenshotUrl(applicationId: string, runId: string, screenshotId: string): Promise<string> {
  const response = await protectedFetch(`${base(applicationId)}/runs/${runId}/screenshots/${screenshotId}`, { cache: 'no-store' })
  if (!response.ok) throw new Error('Could not load the screenshot.')
  return URL.createObjectURL(await response.blob())
}
