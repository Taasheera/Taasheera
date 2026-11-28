import { createContext, useContext } from 'react'
import { en, type Text } from './en'

export type Language = 'en' | 'ar'

// Mark a translated route with handle: arabicPage (routes.tsx). Every other page stays English and
// left to right, so nobody sees half a page in Arabic (CMPS271-16).
export const arabicPage = { arabic: true }

export type LanguageState = {
  language: Language
  text: Text
  // True only on a translated page while the arabic flag is on.
  canSwitch: boolean
  choose: (language: Language) => void
}

// Outside the site layout (sign-in, admin) there is no provider: English.
export const LanguageContext = createContext<LanguageState>({ language: 'en', text: en, canSwitch: false, choose: () => {} })

export function useLanguage(): LanguageState {
  return useContext(LanguageContext)
}

export function useText(): Text {
  return useContext(LanguageContext).text
}

// The choice is a display setting like the theme, never account data. Tests replace read and
// write: their browser storage is locked so a sign-in token stored by mistake fails them.
export const savedChoice = {
  read(): Language {
    try {
      return localStorage.getItem('taasheera-language') === 'ar' ? 'ar' : 'en'
    } catch {
      return 'en'
    }
  },
  write(language: Language) {
    try {
      localStorage.setItem('taasheera-language', language)
    } catch {
      // The choice still applies to the open page.
    }
  },
}
