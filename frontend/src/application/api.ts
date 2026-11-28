import { protectedFetch } from '../auth'
import type { PassportFields } from '../passport/fields'

// Mirrors backend/app/applications/router.py. The server decides which question
// comes next and checks every answer; the page only shows what it is given.
export type AnswerType = 'choice' | 'yes_no' | 'text' | 'date' | 'country' | 'phone' | 'address' | 'passport'
export type Option = { value: string; label: string }
export type Address = { line1: string; line2: string; city: string; region: string; postal_code: string; country: string }
export type AnswerValue = string | boolean | Address | PassportFields

export type Question = {
  id: string
  step_id: string
  type: AnswerType
  prompt: string
  help: string | null
  required: boolean
  options: Option[] | null
  max_length: number
  date_min: string | null
  date_max: string | null
  // What the saved passport or profile already says (US 10), for the traveler to confirm or change.
  suggestion: Suggestion | null
  // A booking the traveler could upload so this answer is read from it (fill from your documents).
  upload_hint?: UploadHint | null
}

// For a document: what it is and the file name, such as "Flight reservation (booking.pdf)".
export type Suggestion = { value: AnswerValue; source: 'passport' | 'profile' | 'document'; document?: string | null }
export type UploadHint = { requirement: string; label: string }
export type AnswerSource = 'traveler' | 'passport_upload' | 'passport' | 'profile' | 'document'
export type Answer = { question: Question; value: AnswerValue; source: AnswerSource }
// The last three are recorded by the traveler (US 55); the first three follow from the answers.
export type Status = 'DRAFT' | 'INELIGIBLE' | 'ANSWERS_COMPLETE' | 'SUBMITTED' | 'APPROVED' | 'REFUSED'
export type RecordedStatus = 'SUBMITTED' | 'APPROVED' | 'REFUSED'
export const isRecorded = (status: Status): status is RecordedStatus =>
  status === 'SUBMITTED' || status === 'APPROVED' || status === 'REFUSED'
export type Progress = { step_number: number; step_count: number; step_title: string; answered: number; total: number }

// What the applications sidebar shows (US 12).
export type ApplicationSummary = {
  id: string
  title: string
  destination: string
  visa_type: string
  destination_label: string
  visa_label: string
  status: Status
  group_size: number
  // From the answers that name the trip, once given: "France", "2027-01-10".
  place: string | null
  trip_date: string | null
  answered: number
  total: number
  created_at: string
  updated_at: string
  // Set while archived (US 79): hidden from the dashboard and sidebar until restored.
  archived_at: string | null
  // When the traveler recorded submitting it, and the embassy's decision (US 55).
  submitted_at: string | null
  decided_at: string | null
}
export type ApplicationState = ApplicationSummary & {
  steps: { id: string; title: string }[]
  progress: Progress
  answers: Answer[]
  current: Question | null
  stopped: string | null
  cleared: string[]
}
export type VisaOption = {
  destination: string
  visa_type: string
  title: string
  destination_label: string
  destination_description: string
  visa_label: string
  visa_description: string
}

// A rejected answer: the server's message is written for the traveler, so show it as is.
export class AnswerError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'AnswerError'
  }
}

async function detail(response: Response, fallback: string) {
  const problem = await response.json().catch(() => null) as { detail?: unknown } | null
  return typeof problem?.detail === 'string' ? problem.detail : fallback
}

const json = { 'Content-Type': 'application/json' }

export async function getVisaOptions(): Promise<VisaOption[]> {
  const response = await protectedFetch('/applications/options', { cache: 'no-store' })
  if (!response.ok) throw new Error('Could not load the visas we can prepare.')
  return response.json()
}

export async function listApplications(archived = false): Promise<ApplicationSummary[]> {
  const response = await protectedFetch(archived ? '/applications?archived=true' : '/applications', { cache: 'no-store' })
  if (!response.ok) throw new Error('Could not load your applications.')
  return response.json()
}

export async function startApplication(option: VisaOption, groupSize: number): Promise<ApplicationState> {
  const response = await protectedFetch('/applications', {
    method: 'POST', headers: json, cache: 'no-store',
    body: JSON.stringify({ destination: option.destination, visa_type: option.visa_type, group_size: groupSize }),
  })
  if (!response.ok) throw new Error(await detail(response, 'Could not start the application. Please try again.'))
  return response.json()
}

export async function getApplication(id: string): Promise<ApplicationState | null> {
  const response = await protectedFetch(`/applications/${id}`, { cache: 'no-store' })
  if (response.status === 404) return null
  if (!response.ok) throw new Error('Could not load your application.')
  return response.json()
}

// A value; the id of a passport upload the traveler confirmed on the passport page; or a yes to
// the suggestion, which the server works out again itself.
export type AnswerInput = { value: AnswerValue } | { passport_upload_id: string } | { use_suggestion: true }

export async function saveAnswer(id: string, questionId: string, answer: AnswerInput): Promise<ApplicationState> {
  const response = await protectedFetch(`/applications/${id}/answers/${questionId}`, {
    method: 'PUT', headers: json, cache: 'no-store', silent: true, body: JSON.stringify(answer),
  })
  if (response.status === 422 || response.status === 404 || response.status === 409) {
    throw new AnswerError(await detail(response, 'Check your answer and try again.'))
  }
  if (!response.ok) throw new Error('Could not save your answer. Please try again.')
  return response.json()
}

// The completeness check (US 13). Mirrors backend/app/applications/completeness.py.
export type CheckIssue = {
  kind: 'missing' | 'ineligible' | 'inconsistent' | 'risk'
  severity: 'blocking' | 'warning'
  message: string
  question_id: string | null
  step_id: string | null
  document_id?: string | null
  link?: string | null
  source?: string | null
}
export type CheckReport = { ready: boolean; answered: number; total: number; issues: CheckIssue[]; readiness_score?: number; readiness_reason?: string }

export async function checkApplication(id: string): Promise<CheckReport> {
  const response = await protectedFetch(`/applications/${id}/check`, { cache: 'no-store' })
  if (!response.ok) throw new Error('Could not check your application. Please try again.')
  return response.json()
}
// Archive, delete and the timeline (US 79, US 20). Mirrors backend/app/applications/lifecycle.py.
export type TimelineEvent = {
  kind: 'started' | 'answered' | 'stopped' | 'complete' | 'submitted' | 'approved' | 'refused' | 'archived'
  at: string
  text: string
  question_id: string | null
  step_title: string | null
}
export type NextStep = { kind: 'answer' | 'change' | 'complete' | 'submitted' | 'approved' | 'refused' | 'archived'; text: string }
export type Timeline = { id: string; archived_at: string | null; events: TimelineEvent[]; next: NextStep }

export async function getTimeline(id: string): Promise<Timeline | null> {
  const response = await protectedFetch(`/applications/${id}/timeline`, { cache: 'no-store' })
  if (response.status === 404) return null
  if (!response.ok) throw new Error('Could not load the history of this application.')
  return response.json()
}

export async function setArchived(id: string, archived: boolean): Promise<ApplicationSummary> {
  const response = await protectedFetch(`/applications/${id}/${archived ? 'archive' : 'unarchive'}`, {
    method: 'POST', cache: 'no-store', silent: true,
  })
  if (!response.ok) throw new Error(await detail(response, archived ? 'Could not archive the application.' : 'Could not restore the application.'))
  return response.json()
}

// The traveler records handing it to the embassy, then the decision (US 55). ANSWERS_COMPLETE takes
// back a submission recorded by mistake. Mirrors POST /applications/{id}/decision.
export async function recordDecision(id: string, status: RecordedStatus | 'ANSWERS_COMPLETE'): Promise<ApplicationSummary> {
  const response = await protectedFetch(`/applications/${id}/decision`, {
    method: 'POST', headers: json, cache: 'no-store', silent: true, body: JSON.stringify({ record: status }),
  })
  if (!response.ok) throw new Error(await detail(response, 'Could not record that. Please try again.'))
  return response.json()
}

// The application's answers, status and timeline as a JSON file the traveler keeps (US 80).
export async function exportRecord(id: string): Promise<{ file: Blob; filename: string }> {
  const response = await protectedFetch(`/applications/${id}/export`, { cache: 'no-store', silent: true })
  if (!response.ok) throw new Error(await detail(response, 'Could not export the application.'))
  const named = /filename="([^"]+)"/.exec(response.headers.get('Content-Disposition') ?? '')
  return { file: await response.blob(), filename: named?.[1] ?? 'taasheera-application.json' }
}

// Duplicating for another country (US 78). Mirrors backend/app/applications/records.py.
export type DuplicateChoice = {
  destination: string
  visa_type: string
  title: string
  destination_label: string
  visa_label: string
  place_prompt: string | null
  places: Option[]
}

export async function getDuplicateChoices(id: string): Promise<DuplicateChoice[]> {
  const response = await protectedFetch(`/applications/${id}/duplicate`, { cache: 'no-store', silent: true })
  if (!response.ok) throw new Error(await detail(response, 'Could not load where the copy can be for.'))
  return response.json()
}

export async function duplicateApplication(id: string, choice: DuplicateChoice, place: string | null): Promise<ApplicationSummary> {
  const response = await protectedFetch(`/applications/${id}/duplicate`, {
    method: 'POST', headers: json, cache: 'no-store', silent: true,
    body: JSON.stringify({ destination: choice.destination, visa_type: choice.visa_type, place }),
  })
  if (!response.ok) throw new Error(await detail(response, 'Could not duplicate the application.'))
  return response.json()
}

export async function deleteApplication(id: string): Promise<void> {
  const response = await protectedFetch(`/applications/${id}`, { method: 'DELETE', cache: 'no-store', silent: true })
  // Already gone counts as deleted: the traveler wanted it removed.
  if (!response.ok && response.status !== 404) throw new Error(await detail(response, 'Could not delete the application.'))
}
