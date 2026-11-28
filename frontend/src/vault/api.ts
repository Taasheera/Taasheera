import { ApiError } from '../api'
import { protectedFetch } from '../auth'

export type VaultUse = {
  id: string
  application_id: string
  application_title: string
  place: string | null
  requirement: string
  requirement_label: string
  archived: boolean
}
export type LanguageOption = { code: string; label: string }
export type VaultDocument = {
  id: string
  label: string
  filename: string
  content_type: 'application/pdf' | 'image/jpeg'
  size_bytes: number
  uploaded_at: string
  uses: VaultUse[]
  languages: LanguageOption[]
}
export type AttachTarget = { application_id: string; title: string; place: string | null; requirements: { id: string; label: string }[] }
export type Vault = { documents: VaultDocument[]; targets: AttachTarget[] }
export type Translation = {
  document_id: string
  language: string
  language_label: string
  text: string
  truncated: boolean
  notice: string
}
export type RuleSummary = {
  questionnaire_id: string
  title: string
  requirements: string[]
  blank_pages: number | null
  note: string | null
  sources: { label: string; url: string }[]
  checked_on: string
}
export type PassportCheck = { blank_pages: number | null; rules: RuleSummary[] }

export class VaultError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'VaultError'
  }
}

async function detail(response: Response, fallback: string) {
  const problem = await response.json().catch(() => null) as { detail?: unknown } | null
  return typeof problem?.detail === 'string' ? problem.detail : fallback
}

async function send<T>(path: string, method: 'POST' | 'PUT', body: unknown, fallback: string, timeout = 15000): Promise<T> {
  const response = await protectedFetch(path, {
    method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), cache: 'no-store', silent: true,
    signal: AbortSignal.timeout(timeout),
  })
  if (!response.ok) throw new VaultError(await detail(response, fallback))
  return response.json()
}

export async function getVault(): Promise<Vault> {
  const response = await protectedFetch('/vault/documents', { cache: 'no-store' })
  if (!response.ok) throw new VaultError('Could not load your documents.')
  return response.json()
}

export function attachDocument(documentId: string, applicationId: string, requirement: string): Promise<VaultUse> {
  return send(`/vault/documents/${documentId}/attachments`, 'POST', { application_id: applicationId, requirement },
    'Could not add that file to the application. Please try again.')
}

export async function deleteVaultDocument(documentId: string): Promise<void> {
  const response = await protectedFetch(`/vault/documents/${documentId}`, { method: 'DELETE', cache: 'no-store', silent: true })
  if (!response.ok && response.status !== 404) throw new VaultError(await detail(response, 'Could not delete that file. Please try again.'))
}

export async function translateDocument(documentId: string, language: string): Promise<Translation> {
  try {
    return await send<Translation>(`/vault/documents/${documentId}/translation`, 'POST', { language },
      'Could not translate that document. Please try again.', 60000)
  } catch (failure) {
    if (failure instanceof ApiError && failure.status === 503) {
      throw new VaultError("Translation isn't available right now. Please try again later.")
    }
    throw failure
  }
}

export async function getPassportCheck(): Promise<PassportCheck> {
  const response = await protectedFetch('/vault/passport', { cache: 'no-store' })
  if (!response.ok) throw new VaultError('Could not load your passport checks.')
  return response.json()
}

export function savePassportPages(blankPages: number): Promise<PassportCheck> {
  return send('/vault/passport', 'PUT', { blank_pages: blankPages }, 'Could not save the number of pages. Please try again.')
}
