import { protectedFetch } from '../auth'

// One in-app notification (US 28), as GET /notifications returns it.
export type NotificationItem = {
  id: number
  kind: string
  message: string
  link: string | null
  application_id: string | null
  created_at: string
  read: boolean
}

async function checked(response: Response, action: string): Promise<Response> {
  if (!response.ok) throw new Error(`We could not ${action}. Please try again.`)
  return response
}

// Every call is silent: the notification panel shows its own message instead of a toast.
export async function loadNotifications(): Promise<NotificationItem[]> {
  const response = await protectedFetch('/notifications', { cache: 'no-store', silent: true })
  return (await checked(response, 'load your notifications')).json()
}

export async function loadUnreadCount(): Promise<number> {
  const response = await protectedFetch('/notifications/unread-count', { cache: 'no-store', silent: true })
  const body = await (await checked(response, 'load your notifications')).json() as { unread: number }
  return body.unread
}

// keepalive: marking read must finish even when the click also leaves the page.
export async function markRead(id: number): Promise<void> {
  await checked(await protectedFetch(`/notifications/${id}/read`, { method: 'POST', keepalive: true, silent: true }),
    'mark it as read')
}

export async function markAllRead(): Promise<void> {
  await checked(await protectedFetch('/notifications/read-all', { method: 'POST', silent: true }), 'mark them as read')
}
