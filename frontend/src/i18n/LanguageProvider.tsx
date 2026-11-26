import { useLayoutEffect, useState, type ReactNode } from 'react'
import { useMatches } from 'react-router'
import { useFeature } from '../features'
import { ar } from './ar'
import { en } from './en'
import { LanguageContext, savedChoice, type Language } from './language'

function isArabicPage(handle: unknown) {
  return (handle as { arabic?: unknown } | undefined)?.arabic === true
}

// Chooses the language of the open page and sets <html lang dir> to match.
export default function LanguageProvider({ children }: { children: ReactNode }) {
  const translated = useMatches().some(match => isArabicPage(match.handle))
  const flag = useFeature('arabic')
  const [chosen, setChosen] = useState<Language>(() => savedChoice.read())
  // While the flags load, keep the saved choice so Arabic does not flash English first.
  // With the flag off, the page is English whatever was saved.
  const language: Language = translated && flag !== false && chosen === 'ar' ? 'ar' : 'en'

  useLayoutEffect(() => {
    document.documentElement.lang = language
    document.documentElement.dir = language === 'ar' ? 'rtl' : 'ltr'
  }, [language])

  function choose(next: Language) {
    setChosen(next)
    savedChoice.write(next)
  }

  const state = { language, text: language === 'ar' ? ar : en, canSwitch: translated && flag === true, choose }
  return <LanguageContext.Provider value={state}>{children}</LanguageContext.Provider>
}
