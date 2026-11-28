import { adminRequest, json } from './api'

export const helpLanguages = ['en', 'ar'] as const
export type HelpLanguage = typeof helpLanguages[number]
export const helpLanguageLabels: Record<HelpLanguage, string> = { en: 'English', ar: 'Arabic' }

export type HelpEntry = {
  id: number
  question: string
  answer: string
  category: string
  language: HelpLanguage
  position: number
  published: boolean
  updated_at: string
  updated_by_name: string | null
}

export type HelpEntryInput = Pick<HelpEntry, 'question' | 'answer' | 'category' | 'language' | 'published'>

export function listHelpEntries(language: HelpLanguage) {
  return adminRequest<HelpEntry[]>(`/help-content?language=${language}`)
}

export function addHelpEntry(entry: HelpEntryInput) {
  return adminRequest<HelpEntry>('/help-content', json('POST', entry))
}

export function editHelpEntry(id: number, entry: HelpEntryInput) {
  return adminRequest<HelpEntry>(`/help-content/${id}`, json('PUT', entry))
}

export function deleteHelpEntry(id: number) {
  return adminRequest<void>(`/help-content/${id}`, json('DELETE'))
}

export function reorderHelpEntries(language: HelpLanguage, ids: number[]) {
  return adminRequest<HelpEntry[]>('/help-content/order', json('PUT', { language, ids }))
}
