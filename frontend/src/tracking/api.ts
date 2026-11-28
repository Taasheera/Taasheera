import { protectedFetch } from '../auth'

// Mirrors backend/app/applications/tracking.py (CMPS271-72/73/77/79).
export type Step = { id: string; title: string; text: string; link: { label: string; url: string } | null; done: boolean }
export type ChecklistItem = { label: string; detail: string | null; status: 'ready' | 'missing' | 'bring'; optional: boolean }
export type Submission = {
  stage: string
  steps: Step[]
  current: string | null
  appointment: { needed: boolean; at: string | null; location: string | null }
  checklist: ChecklistItem[]
  read_only: boolean
}

async function detail(response: Response, fallback: string) {
  const problem = await response.json().catch(() => null) as { detail?: unknown } | null
  return typeof problem?.detail === 'string' ? problem.detail : fallback
}

export async function getSubmission(applicationId: string): Promise<Submission> {
  const response = await protectedFetch(`/applications/${applicationId}/submission`, { cache: 'no-store' })
  if (!response.ok) throw new Error('Could not load the submission steps.')
  return response.json()
}

export async function saveAppointment(applicationId: string, at: Date, location: string): Promise<Submission> {
  const response = await protectedFetch(`/applications/${applicationId}/appointment`, {
    method: 'PUT', cache: 'no-store', silent: true, headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ at: at.toISOString(), location: location.trim() || null }),
  })
  if (!response.ok) throw new Error(await detail(response, 'Could not save the appointment.'))
  return response.json()
}

export async function removeAppointment(applicationId: string): Promise<Submission> {
  const response = await protectedFetch(`/applications/${applicationId}/appointment`, { method: 'DELETE', cache: 'no-store', silent: true })
  if (!response.ok) throw new Error(await detail(response, 'Could not remove the appointment.'))
  return response.json()
}
