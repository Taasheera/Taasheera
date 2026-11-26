import { useEffect, useState } from 'react'
import './appearance.css'
import { useText } from './i18n/language'

type Theme = 'light' | 'dark'

const storageKey = 'taasheera-appearance'
const darkQuery = '(prefers-color-scheme: dark)'

function current(): Theme {
  const chosen = document.documentElement.dataset.appearance
  if (chosen === 'light' || chosen === 'dark') return chosen
  return window.matchMedia?.(darkQuery).matches ? 'dark' : 'light'
}

// One button that flips between light and dark. Until the traveler presses it, the page follows
// the device setting (public/appearance.js applies a remembered choice before the app renders).
export default function ThemeToggle() {
  const text = useText().theme
  const [theme, setTheme] = useState<Theme>(current)

  // While no choice is saved, follow the device when it switches (for example at sunset).
  useEffect(() => {
    const media = window.matchMedia?.(darkQuery)
    if (!media) return
    const follow = () => setTheme(current())
    media.addEventListener('change', follow)
    return () => media.removeEventListener('change', follow)
  }, [])

  function toggle() {
    const next: Theme = theme === 'dark' ? 'light' : 'dark'
    document.documentElement.dataset.appearance = next
    setTheme(next)
    try {
      localStorage.setItem(storageKey, next)
    } catch {
      // The choice still applies to the open page.
    }
  }

  const label = theme === 'dark' ? text.toLight : text.toDark
  return (
    <button type="button" className="theme-toggle" aria-label={label} title={label} onClick={toggle}>
      {theme === 'dark'
        ? <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8"
            strokeLinecap="round" aria-hidden="true">
            <circle cx="12" cy="12" r="4" />
            <path d="M12 2.5v2M12 19.5v2M4.6 4.6l1.4 1.4M18 18l1.4 1.4M2.5 12h2M19.5 12h2M4.6 19.4 6 18M18 6l1.4-1.4" />
          </svg>
        : <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8"
            strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5Z" />
          </svg>}
    </button>
  )
}
