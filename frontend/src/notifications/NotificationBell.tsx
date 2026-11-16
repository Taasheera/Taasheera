import { useEffect, useId, useRef, useState } from 'react'
import { Link } from 'react-router'
import { formatDateTime } from '../dashboard/helpers'
import { loadNotifications, loadUnreadCount, markAllRead, markRead, type NotificationItem } from './api'
import './notifications.css'

function BellIcon() {
  return (
    <svg className="icon" viewBox="0 0 20 20" width="20" height="20" fill="none" aria-hidden="true">
      <path d="M5 8.5a5 5 0 0 1 10 0v3.2l1.4 2.3H3.6L5 11.7z M8 16a2 2 0 0 0 4 0" stroke="currentColor"
        strokeWidth="1.7" strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  )
}

type BellProps = {
  // The count is fetched again on every page change, so a new notification shows up as the traveler moves around.
  pathname: string
  // Same as the navigation panel: in-app links inside the site layout, plain links on the account screens.
  routed: boolean
}

// The notification centre (US 107): a bell with the unread count that opens the traveler's
// notifications, newest first, with mark-as-read and mark-all-read.
export default function NotificationBell({ pathname, routed }: BellProps) {
  const [open, setOpen] = useState(false)
  const [unread, setUnread] = useState(0)
  const [items, setItems] = useState<NotificationItem[] | null>(null)
  const [error, setError] = useState('')
  const panelId = useId()
  const root = useRef<HTMLDivElement>(null)
  const button = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    let active = true
    loadUnreadCount().then(count => { if (active) setUnread(count) }).catch(() => { /* The bell just shows no count. */ })
    return () => { active = false }
  }, [pathname])

  // Escape or a click elsewhere closes the list.
  useEffect(() => {
    if (!open) return
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { setOpen(false); button.current?.focus() }
    }
    const onPointer = (event: MouseEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false)
    }
    document.addEventListener('keydown', onKey)
    document.addEventListener('mousedown', onPointer)
    return () => {
      document.removeEventListener('keydown', onKey)
      document.removeEventListener('mousedown', onPointer)
    }
  }, [open])

  async function show() {
    setOpen(true)
    setError('')
    try {
      setItems(await loadNotifications())
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : 'We could not load your notifications. Please try again.')
    }
  }

  function markLocally(changed: (item: NotificationItem) => boolean) {
    setItems(current => current?.map(item => changed(item) ? { ...item, read: true } : item) ?? null)
  }

  async function read(item: NotificationItem) {
    if (item.read) return
    markLocally(other => other.id === item.id)
    setUnread(count => Math.max(0, count - 1))
    try {
      await markRead(item.id)
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : 'We could not mark it as read. Please try again.')
    }
  }

  async function readAll() {
    try {
      await markAllRead()
      markLocally(() => true)
      setUnread(0)
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : 'We could not mark them as read. Please try again.')
    }
  }

  const openItem = (item: NotificationItem) => {
    void read(item)
    setOpen(false)
  }

  return (
    <div className="notification-bell" ref={root}>
      <button ref={button} type="button" className="notification-bell-button" aria-expanded={open}
        aria-controls={open ? panelId : undefined} aria-label={unread ? `Notifications, ${unread} unread` : 'Notifications'}
        onClick={() => (open ? setOpen(false) : void show())}>
        <BellIcon />
        {unread > 0 && <span className="notification-count" aria-hidden="true">{unread > 99 ? '99+' : unread}</span>}
      </button>

      {open && <section id={panelId} className="notification-panel" aria-labelledby={`${panelId}-title`}>
        <header className="notification-panel-header">
          <h2 id={`${panelId}-title`}>Notifications</h2>
          {items?.some(item => !item.read) &&
            <button type="button" className="notification-action" onClick={() => void readAll()}>Mark all as read</button>}
        </header>
        {error && <p className="notification-error" role="alert">
          {error} <button type="button" className="notification-action" onClick={() => void show()}>Try again</button>
        </p>}
        {!error && items === null && <p className="notification-note" role="status">Loading your notifications…</p>}
        {!error && items?.length === 0 &&
          <p className="notification-note">No notifications yet. We will tell you here when an application changes.</p>}
        {items && items.length > 0 && <ul className="notification-list">
          {items.map(item => <li key={item.id} className={item.read ? 'is-read' : 'is-unread'}>
            {!item.read && <span className="visually-hidden">Unread: </span>}
            {item.link
              ? (routed
                ? <Link to={item.link} className="notification-message" onClick={() => openItem(item)}>{item.message}</Link>
                : <a href={item.link} className="notification-message" onClick={() => openItem(item)}>{item.message}</a>)
              : <span className="notification-message">{item.message}</span>}
            <span className="notification-meta">
              <time dateTime={item.created_at}>{formatDateTime(item.created_at)}</time>
              {!item.read && <button type="button" className="notification-action" onClick={() => void read(item)}>
                Mark as read</button>}
            </span>
          </li>)}
        </ul>}
      </section>}
    </div>
  )
}
