import { protectedFetch } from '../auth'

// Mirrors backend/app/generated/router.py (CMPS271-57 to 62). The server writes every document from the
// application's confirmed answers; the page only shows it, saves the traveler's edit or asks for a new one.
export type LanguageOption = { language: string; label: string; edited: boolean }
export type KindOption = { kind: string; label: string; languages: LanguageOption[] }
export type Overview = { documents: KindOption[]; read_only: boolean; notice: string }
export type GeneratedDocument = {
  kind: string
  language: string
  title: string
  body: string
  edited: boolean
  outdated: boolean
  saved_at: string | null
  read_only: boolean
  notice: string
}

async function detail(response: Response, fallback: string) {
  const problem = await response.json().catch(() => null) as { detail?: unknown } | null
  return typeof problem?.detail === 'string' ? problem.detail : fallback
}

const base = (applicationId: string) => `/applications/${applicationId}/generated`
const one = (applicationId: string, kind: string, language: string, suffix = '') =>
  `${base(applicationId)}/${encodeURIComponent(kind)}${suffix}?language=${encodeURIComponent(language)}`

export async function getOverview(applicationId: string): Promise<Overview> {
  const response = await protectedFetch(base(applicationId), { cache: 'no-store' })
  if (!response.ok) throw new Error('Could not load your documents.')
  return response.json()
}

export async function getDocument(applicationId: string, kind: string, language: string): Promise<GeneratedDocument> {
  const response = await protectedFetch(one(applicationId, kind, language), { cache: 'no-store' })
  if (!response.ok) throw new Error(await detail(response, 'Could not load this document.'))
  return response.json()
}

export async function saveDocument(applicationId: string, kind: string, language: string, body: string): Promise<GeneratedDocument> {
  const response = await protectedFetch(one(applicationId, kind, language), {
    method: 'PUT', cache: 'no-store', silent: true,
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ body }),
  })
  if (!response.ok) throw new Error(await detail(response, 'Could not save your changes.'))
  return response.json()
}

export async function regenerateDocument(applicationId: string, kind: string, language: string): Promise<GeneratedDocument> {
  const response = await protectedFetch(one(applicationId, kind, language), { method: 'DELETE', cache: 'no-store', silent: true })
  if (!response.ok) throw new Error(await detail(response, 'Could not write the document again.'))
  return response.json()
}

export async function downloadDocument(applicationId: string, kind: string, language: string): Promise<{ file: Blob; filename: string }> {
  const response = await protectedFetch(one(applicationId, kind, language, '/pdf'), { cache: 'no-store', silent: true })
  if (!response.ok) throw new Error(await detail(response, 'Could not prepare the PDF.'))
  const named = /filename="([^"]+)"/.exec(response.headers.get('Content-Disposition') ?? '')
  return { file: await response.blob(), filename: named?.[1] ?? `${kind}.pdf` }
}
