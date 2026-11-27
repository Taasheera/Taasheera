import { protectedFetch } from '../auth'
import { ADMIN_REQUIRED, AdminRequestError } from './api'

const BASE = '/admin-api/requirements'

export type Json = null | boolean | number | string | Json[] | { [key: string]: Json }
export type JsonObject = { [key: string]: Json }

export type Problem = { part: 'questionnaire' | 'guide'; loc: (string | number)[]; path: string; message: string }
export type Change = { part: 'questionnaire' | 'guide'; path: string; kind: 'added' | 'removed' | 'changed' | 'moved'; before: Json; after: Json }
export type CheckResult = { valid: boolean; problems: Problem[]; changes: Change[]; questionnaire_version: number }
export type Content = { questionnaire: JsonObject; guide: JsonObject }
export type Revision = {
  revision: number; kind: 'publish' | 'rollback'; restored_revision: number | null; questionnaire_version: number
  note: string; author: string | null; created_at: string
}
export type Draft = Content & { base_revision: number; author: string | null; updated_at: string }
export type DestinationSummary = {
  id: string; title: string; destination_label: string; visa_label: string; revision: number
  questionnaire_version: number; packaged_version: number; published_at: string | null; has_draft: boolean
  applications_active: number; applications_recorded: number
}
export type DestinationDetail = DestinationSummary & { current: Content; draft: Draft | null; history: Revision[] }

export class ProblemsError extends AdminRequestError {
  readonly problems: Problem[]

  constructor(message: string, problems: Problem[]) {
    super(message, 422)
    this.name = 'ProblemsError'
    this.problems = problems
  }
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  let response: Response
  try {
    response = await protectedFetch(`${BASE}${path}`, { ...options, cache: 'no-store', silent: true })
  } catch (error) {
    throw new AdminRequestError(error instanceof Error ? error.message : 'Could not reach Taasheera.', null)
  }
  if (response.status === 204) return undefined as T
  if (response.ok) return await response.json() as T
  const payload = await response.json().catch(() => null) as { detail?: unknown } | null
  const detail = payload?.detail
  if (detail && typeof detail === 'object' && !Array.isArray(detail) && 'problems' in detail) {
    const refusal = detail as { message: string; problems: Problem[] }
    throw new ProblemsError(refusal.message, refusal.problems)
  }
  const fallback = response.status === 403 ? ADMIN_REQUIRED
    : response.status === 422 ? 'That request was not valid. Check the fields and try again.'
      : 'Something went wrong. Please try again.'
  throw new AdminRequestError(typeof detail === 'string' ? detail : fallback, response.status)
}

function send(method: string, body?: unknown): RequestInit {
  return body === undefined ? { method } : { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }
}

export const listDestinations = () => request<DestinationSummary[]>('')
export const getDestination = (id: string) => request<DestinationDetail>(`/${id}`)
export const getRevision = (id: string, revision: number) => request<Content>(`/${id}/revisions/${revision}`)
export const checkContent = (id: string, content: Content) => request<CheckResult>(`/${id}/check`, send('POST', content))
export const saveDraft = (id: string, content: Content, baseRevision: number) =>
  request<{ draft: Draft; check: CheckResult }>(`/${id}/draft`, send('PUT', { ...content, base_revision: baseRevision }))
export const discardDraft = (id: string) => request<void>(`/${id}/draft`, send('DELETE'))
export const publishDraft = (id: string, baseRevision: number, note: string) =>
  request<DestinationDetail>(`/${id}/publish`, send('POST', { base_revision: baseRevision, note }))
export const rollBack = (id: string, revision: number, baseRevision: number, note: string) =>
  request<DestinationDetail>(`/${id}/rollback`, send('POST', { revision, base_revision: baseRevision, note }))
