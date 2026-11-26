import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router'
import type { Traveler } from './auth'

type AccountMenuProps = { traveler: Traveler; loggingOut: boolean; onLogOut: () => void }

function initials(name: string) {
  return name.trim().split(/\s+/).slice(0, 2).map(part => part[0]).join('').toUpperCase() || '?'
}

// The signed-in traveler's avatar opens their account, FAQ and Log out. Keeping these here leaves
// the bar for the sections themselves.
export default function AccountMenu({ traveler, loggingOut, onLogOut }: AccountMenuProps) {
  const [open, setOpen] = useState(false)
  const root = useRef<HTMLDivElement>(null)
  const button = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (!open) return
    const closeOutside = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false)
    }
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      setOpen(false)
      button.current?.focus()
    }
    document.addEventListener('pointerdown', closeOutside)
    document.addEventListener('keydown', closeOnEscape)
    return () => {
      document.removeEventListener('pointerdown', closeOutside)
      document.removeEventListener('keydown', closeOnEscape)
    }
  }, [open])

  return (
    <div className="account-menu" ref={root}>
      <button ref={button} type="button" className="nav-avatar" aria-label="Account menu" aria-expanded={open}
        aria-controls="account-menu-panel" data-activity-id="menu-toggle" onClick={() => setOpen(value => !value)}>
        {initials(traveler.name)}
      </button>
      {open && (
        <div id="account-menu-panel" className="account-menu-panel"
          onClick={event => { if ((event.target as Element).closest('a')) setOpen(false) }}>
          <div className="account-menu-who">
            <strong>{traveler.name}</strong>
            <span>{traveler.email}</span>
          </div>
          <Link to="/account">Account settings</Link>
          <Link to="/faq" data-activity-id="faq-link">Help and FAQ</Link>
          <button type="button" className="logout-button" data-activity-id="logout-button" disabled={loggingOut}
            onClick={onLogOut}>{loggingOut ? 'Logging out…' : 'Log out'}</button>
        </div>
      )}
    </div>
  )
}
