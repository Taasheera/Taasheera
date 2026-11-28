import { protectedFetch } from '../auth'
import { reportConsentRequired } from '../consent'

// Mirrors backend/app/documents/router.py (CMPS271-29). The server decides which documents an
// application needs from its answers and checks every file again; the page only shows what it is given.
export type UploadedDocument = {
  id: string
  requirement: string
  filename: string
  content_type: 'application/pdf' | 'image/jpeg'
  size_bytes: number
  uploaded_at: string
}
export type Requirement = { id: string; label: string; help: string | null; required: boolean; documents: UploadedDocument[] }
export type Checklist = { requirements: Requirement[]; other: UploadedDocument[]; complete: boolean; read_only: boolean }

export const MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
export const DOCUMENT_TYPES = ['application/pdf', 'image/jpeg', 'image/png', 'image/webp']

// Checked in the browser so a wrong file fails at once; the server checks the real content again.
export function checkDocumentFile(file: File): string | null {
  if (!DOCUMENT_TYPES.includes(file.type)) return "That file isn't a PDF, JPEG, PNG or WebP. Scan or save it as one of those."
  if (file.size > MAX_DOCUMENT_BYTES) return 'That file is larger than 10 MB. Choose a smaller one.'
  return null
}

export class DocumentError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'DocumentError'
  }
}

async function detail(response: Response, fallback: string) {
  const problem = await response.json().catch(() => null) as { detail?: unknown } | null
  return typeof problem?.detail === 'string' ? problem.detail : fallback
}

const base = (applicationId: string) => `/applications/${applicationId}/documents`

export async function getChecklist(applicationId: string): Promise<Checklist> {
  const response = await protectedFetch(base(applicationId), { cache: 'no-store' })
  if (!response.ok) throw new Error('Could not load your documents.')
  return response.json()
}

async function send(path: string, method: 'POST' | 'PUT', form: FormData, fallback: string): Promise<UploadedDocument> {
  // OCR of a scanned page can take a few seconds, so allow longer than an ordinary request.
  const response = await protectedFetch(path, { method, body: form, cache: 'no-store', silent: true, signal: AbortSignal.timeout(60000) })
  reportConsentRequired(response)
  if (response.status >= 400 && response.status < 500) throw new DocumentError(await detail(response, fallback))
  if (!response.ok) throw new DocumentError(fallback)
  return response.json()
}

export function uploadDocument(applicationId: string, requirement: string, file: File) {
  const form = new FormData()
  form.append('requirement', requirement)
  form.append('file', file)
  return send(base(applicationId), 'POST', form, 'Could not upload that file. Please try again.')
}

export function replaceDocument(applicationId: string, documentId: string, file: File) {
  const form = new FormData()
  form.append('file', file)
  return send(`${base(applicationId)}/${documentId}`, 'PUT', form, 'Could not replace that file. Please try again.')
}

export async function deleteDocument(applicationId: string, documentId: string): Promise<void> {
  const response = await protectedFetch(`${base(applicationId)}/${documentId}`, { method: 'DELETE', cache: 'no-store', silent: true })
  if (!response.ok && response.status !== 404) throw new DocumentError(await detail(response, 'Could not delete that file. Please try again.'))
}

// The file route needs the sign-in token, so a plain link can't open it: fetch it and hand the browser a blob.
export async function openDocument(applicationId: string, document: UploadedDocument): Promise<void> {
  const response = await protectedFetch(`${base(applicationId)}/${document.id}/file`, { cache: 'no-store' })
  if (!response.ok) throw new DocumentError('Could not open that file.')
  const url = URL.createObjectURL(await response.blob())
  const link = Object.assign(window.document.createElement('a'), { href: url, rel: 'noopener' })
  if (document.content_type === 'application/pdf') link.download = document.filename
  else link.target = '_blank'
  link.click()
  setTimeout(() => URL.revokeObjectURL(url), 60_000)
}

// One printable PDF (CMPS271-71): a cover checklist, then every upload in the embassy's order.
export async function downloadPacket(applicationId: string): Promise<void> {
  const response = await protectedFetch(`${base(applicationId)}/packet`, { cache: 'no-store', silent: true })
  if (!response.ok) throw new DocumentError('Could not prepare your packet. Please try again.')
  const url = URL.createObjectURL(await response.blob())
  const link = Object.assign(window.document.createElement('a'), { href: url, download: 'application-packet.pdf' })
  window.document.body.append(link)
  link.click()
  link.remove()
  setTimeout(() => URL.revokeObjectURL(url), 60_000)
}

export type PhotoCheck = { id: string; passed: boolean; message: string; guidance: string | null }
export type PhotoReport = { spec: string; passed: boolean; checks: PhotoCheck[]; source: { label: string; url: string }; notice: string }

export async function checkPhoto(applicationId: string, documentId: string): Promise<PhotoReport> {
  const response = await protectedFetch(`${base(applicationId)}/${documentId}/photo-check`, { cache: 'no-store', silent: true })
  if (!response.ok) throw new DocumentError(await detail(response, 'Could not check that photo. Please try again.'))
  return response.json()
}

export function formatSize(bytes: number): string {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

// The cross-check (CMPS271-50). Mirrors backend/app/documents/crosscheck.py.
export type Finding = {
  field: 'name' | 'birth_date' | 'document_number' | 'travel_dates'
  source: 'passport' | 'profile' | 'document'
  where: string
  expected: string
  found: string
  message: string
  document_id: string | null
  requirement: string | null
}
export type CrossCheck = { reference: 'application' | 'passport' | null; checked: string[]; unreadable: string[]; findings: Finding[] }

export async function getCrossCheck(applicationId: string): Promise<CrossCheck> {
  const response = await protectedFetch(`${base(applicationId)}/crosscheck`, { cache: 'no-store' })
  if (!response.ok) throw new Error('Could not cross-check your documents.')
  return response.json()
}
