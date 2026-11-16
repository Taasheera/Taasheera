import { useState, type KeyboardEvent } from 'react'
import { Link } from 'react-router'
import { openAssistant } from './assistant/openAssistant'
import { MenuIcon } from './icons'
import { currentSection, sections } from './sections'
import './sectionNav.css'

type SectionNavProps = {
  pathname: string
  // Inside the site layout, links change page without reloading, so the assistant keeps its chat.
  // The sign-in and account screens are separate documents and use plain links.
  routed?: boolean
}

// The navigation panel on every signed-in page (US 15). It shows where the traveler is and,
// below 700px, collapses into one button that opens the list.
export default function SectionNav({ pathname, routed = false }: SectionNavProps) {
  const [open, setOpen] = useState(false)
  const current = currentSection(pathname)
  // Starting an application (/apply) has no assistant yet: the side panel stays off the application
  // pages, and the ask box only appears once an application is open (/apply/<id>).
  const hasAssistant = !/^\/apply\/?$/.test(pathname)
  const close = () => setOpen(false)
  const closeOnEscape = (event: KeyboardEvent) => {
    if (event.key === 'Escape') setOpen(false)
  }

  return (
    <nav className={`section-nav${open ? ' is-open' : ''}`} aria-label="Your sections" onKeyDown={closeOnEscape}>
      <button type="button" className="section-nav-toggle" aria-expanded={open} aria-controls="section-nav-list"
        data-activity-id="menu-toggle" onClick={() => setOpen(value => !value)}>
        <MenuIcon open={open} />
        <span>{current ? current.label : 'Your sections'}</span>
      </button>
      <ul id="section-nav-list" className="section-nav-list">
        {sections.map(section => {
          const here = current?.id === section.id
          const props = {
            className: `section-nav-link${here ? ' is-current' : ''}`,
            'aria-current': here ? 'page' as const : undefined,
            'data-activity-id': section.activityId,
            onClick: close,
          }
          return <li key={section.id}>
            {routed ? <Link to={section.href} {...props}>{section.label}</Link>
              : <a href={section.href} {...props}>{section.label}</a>}
          </li>
        })}
        {hasAssistant && <li>
          <button type="button" className="section-nav-link section-nav-assistant" data-activity-id="assistant-open"
            onClick={() => { close(); openAssistant() }}>Assistant</button>
        </li>}
      </ul>
    </nav>
  )
}
