import { protectedFetch } from '../auth'

export const MESSAGE_LIMIT = 1000
export const shareDurations = [
  { hours: 24, label: '24 hours' },
  { hours: 72, label: '72 hours' },
  { hours: 168, label: '7 days' },
] as const
export type ShareHours = typeof shareDurations[number]['hours']

export type SupportRequestStatus = 'open' | 'in_progress' | 'resolved'
export const requestStatusLabels: Record<SupportRequestStatus, string> = {
  open: 'Waiting for support', in_progress: 'Support is on it', resolved: 'Resolved',
}

export type HelpRequest = {
  id: number
  application_id: string | null
  source: 'website' | 'telegram'
  message: string
  status: SupportRequestStatus
  reply: string | null
  created_at: string
  updated_at: string
  replied_at: string | null
  resolved_at: string | null
}

export type AccessGrant = { id: number; application_id: string; title: string; created_at: string; expires_at: string }

async function detail(response: Response, fallback: string): Promise<never> {
  const payload = await response.json().catch(() => null) as { detail?: unknown } | null
  throw new Error(response.status === 429 ? 'Too many requests. Please try again in a few minutes.'
    : typeof payload?.detail === 'string' ? payload.detail : fallback)
}

function send(method: string, body?: unknown): RequestInit & { silent: true } {
  return { method, silent: true, cache: 'no-store',
    ...(body === undefined ? {} : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }) }
}

export async function listHelpRequests(): Promise<HelpRequest[]> {
  const response = await protectedFetch('/support/requests', send('GET'))
  if (!response.ok) return detail(response, 'Could not load your help requests.')
  return response.json()
}

export async function askForHelp(message: string, applicationId?: string, shareHours?: ShareHours): Promise<HelpRequest> {
  const response = await protectedFetch('/support/requests', send('POST', {
    message, ...(applicationId ? { application_id: applicationId } : {}), ...(applicationId && shareHours ? { share_hours: shareHours } : {}),
  }))
  if (!response.ok) return detail(response, 'Could not send your message. Please try again.')
  return response.json()
}

export async function listAccessGrants(): Promise<AccessGrant[]> {
  const response = await protectedFetch('/support/access', send('GET'))
  if (!response.ok) return detail(response, 'Could not load what you share with support.')
  return response.json()
}

export async function shareWithSupport(applicationId: string, hours: ShareHours): Promise<AccessGrant> {
  const response = await protectedFetch('/support/access', send('POST', { application_id: applicationId, hours }))
  if (!response.ok) return detail(response, 'Could not share this application. Please try again.')
  return response.json()
}

export async function stopSharing(grantId: number): Promise<void> {
  const response = await protectedFetch(`/support/access/${grantId}`, send('DELETE'))
  if (!response.ok && response.status !== 404) return detail(response, 'Could not stop sharing. Please try again.')
}
