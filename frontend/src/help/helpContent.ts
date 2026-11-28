import { useEffect, useState } from 'react'
import { apiFetch } from '../api'
import { faqs } from '../content'

export type HelpLanguage = 'en' | 'ar'
export type HelpItem = { id?: number; question: string; answer: string; category?: string; language?: HelpLanguage }

function pageLanguage(): HelpLanguage {
  return document.documentElement.lang.toLowerCase().startsWith('ar') ? 'ar' : 'en'
}

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

export async function loadHelpEntries(language = pageLanguage()): Promise<HelpItem[]> {
  const found = await published(language).catch(() => [])
  if (found.length || language === 'en') return found
  return published('en').catch(() => [])
}

export function useHelpEntries(): HelpItem[] {
  const [items, setItems] = useState<HelpItem[]>(faqs)
  useEffect(() => {
    let active = true
    void loadHelpEntries().then(found => { if (active && found.length) setItems(found) })
    return () => { active = false }
  }, [])
  return items
}

export function byCategory(items: HelpItem[]): { category: string; items: HelpItem[] }[] {
  const groups = new Map<string, HelpItem[]>()
  for (const item of items) {
    const category = item.category ?? ''
    groups.set(category, [...groups.get(category) ?? [], item])
  }
  return [...groups].map(([category, grouped]) => ({ category, items: grouped }))
}
