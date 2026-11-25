import { useState } from 'react'
import './appearance.css'

type Appearance = 'system' | 'light' | 'dark'

export default function AppearanceSelect() {
  const [appearance, setAppearance] = useState<Appearance>(() => {
    const saved = document.documentElement.dataset.appearance
    return saved === 'light' || saved === 'dark' ? saved : 'system'
  })

  function choose(value: Appearance) {
    setAppearance(value)
    const root = document.documentElement
    if (value === 'system') delete root.dataset.appearance
    else root.dataset.appearance = value
    try {
      if (value === 'system') localStorage.removeItem('taasheera-appearance')
      else localStorage.setItem('taasheera-appearance', value)
    } catch {
      // The choice still applies to the open page.
    }
  }

  return (
    <label className="appearance-select">
      <span className="visually-hidden">Appearance</span>
      <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true">
        <circle cx="12" cy="12" r="8" />
        <path d="M12 4a8 8 0 0 1 0 16Z" fill="currentColor" stroke="none" />
      </svg>
      <select value={appearance} onChange={event => choose(event.target.value as Appearance)}>
        <option value="system">Use device setting</option>
        <option value="light">Light mode</option>
        <option value="dark">Dark mode</option>
      </select>
    </label>
  )
}
