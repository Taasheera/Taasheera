import { useEffect, useState } from 'react'
import { apiFetch } from '../api'
import { useLanguage } from '../i18n/language'

export type HelpLanguage = 'en' | 'ar'
export type HelpItem = { id?: number; question: string; answer: string; category?: string; language?: HelpLanguage }

function isItem(value: unknown): value is HelpItem {
  const item = value as HelpItem | null
  return typeof item?.question === 'string' && typeof item.answer === 'string'
}

async function published(language: HelpLanguage): Promise<HelpItem[]> {
  const response = await apiFetch(`/help-content?language=${language}`, { silent: true, cache: 'no-cache' })
  if (!response.ok) return []
  const items: unknown = await response.json()
  return Array.isArray(items) ? items.filter(isItem) : []
}

export async function loadHelpEntries(language: HelpLanguage = 'en'): Promise<HelpItem[]> {
  return published(language).catch(() => [])
}

// The published entries in the page's language (CMPS271-16). With none published in it, or the API
// down, the bundled questions of that language show, so an Arabic page never lists English entries.
export function useHelpEntries(): HelpItem[] {
  const { language, text } = useLanguage()
  const [found, setFound] = useState<{ language: HelpLanguage; items: HelpItem[] } | null>(null)
  useEffect(() => {
    let active = true
    void loadHelpEntries(language).then(items => { if (active) setFound({ language, items }) })
    return () => { active = false }
  }, [language])
  return found?.language === language && found.items.length ? found.items : text.faqs
}

export function byCategory(items: HelpItem[]): { category: string; items: HelpItem[] }[] {
  const groups = new Map<string, HelpItem[]>()
  for (const item of items) {
    const category = item.category ?? ''
    groups.set(category, [...groups.get(category) ?? [], item])
  }
  return [...groups].map(([category, grouped]) => ({ category, items: grouped }))
}
