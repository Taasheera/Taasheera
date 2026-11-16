import { dom } from './dom-setup.mjs'
import assert from 'node:assert/strict'
import { registerHooks } from 'node:module'
import { afterEach, test } from 'node:test'
import { act, createElement } from 'react'
import { createRoot } from 'react-dom/client'
import { createMemoryRouter, RouterProvider } from 'react-router'

registerHooks({
  load(url, context, nextLoad) {
    if (url.endsWith('.svg')) return { format: 'module', source: `export default ${JSON.stringify(url)}`, shortCircuit: true }
    return nextLoad(url, context)
  },
})

for (const name of ['Node', 'Element', 'HTMLAnchorElement', 'MutationObserver', 'getComputedStyle', 'KeyboardEvent']) {
  globalThis[name] ??= dom.window[name]
}
window.matchMedia ??= () => ({ matches: false, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} })
window.scrollTo = () => {}
Element.prototype.scrollTo ??= () => {}

const { routes } = await import('../src/routes.tsx')

const NEWEST = { id: 2, kind: 'status_changed', message: 'Every question in your Schengen application is answered.',
  link: '/dashboard/a1', application_id: 'a1', created_at: '2026-11-14T17:49:02Z', read: false }
const OLDER = { id: 1, kind: 'status_changed', message: 'One of your answers stops your Schengen application.',
  link: '/dashboard/a1', application_id: 'a1', created_at: '2026-11-14T17:48:47Z', read: false }
const SEEN = { ...OLDER, id: 0, message: 'An older, already read notification.', read: true }

let root, container, router, requests, server

// A small fake backend: the bell's endpoints keep their own state, everything else answers like a signed-in session.
function backend(t, { notifications = [NEWEST, OLDER, SEEN], failList = false } = {}) {
  requests = []
  server = { notifications: notifications.map(item => ({ ...item })) }
  t.mock.method(globalThis, 'fetch', async (url, options = {}) => {
    const method = options.method ?? 'GET'
    requests.push(`${method} ${url}`)
    if (url === '/auth/refresh') return Response.json({ access_token: 'test-access-token' })
    if (url === '/auth/me') return Response.json({ id: 1, name: 'Anna', email: 'anna@example.com' })
    if (url === '/notifications/unread-count') return Response.json({ unread: server.notifications.filter(n => !n.read).length })
    if (url === '/notifications') return failList ? new Response(null, { status: 503 }) : Response.json(server.notifications)
    if (url === '/notifications/read-all') { server.notifications.forEach(n => { n.read = true }); return new Response(null, { status: 204 }) }
    const one = /^\/notifications\/(\d+)\/read$/.exec(url)
    if (one) {
      server.notifications.find(n => n.id === Number(one[1])).read = true
      return new Response(null, { status: 204 })
    }
    if (String(url).startsWith('/assistant/history')) return Response.json([])
    if (String(url).startsWith('/applications')) return Response.json([])
    return new Response(null, { status: 204 })
  })
}

async function render(path) {
  container = document.createElement('div')
  document.body.append(container)
  router = createMemoryRouter(routes, { initialEntries: [path] })
  root = createRoot(container)
  await act(async () => root.render(createElement(RouterProvider, { router })))
  await settle()
}
const settle = () => act(() => new Promise(resolve => setTimeout(resolve, 30)))
const click = async el => { await act(async () => el.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true, cancelable: true, button: 0 }))); await settle() }
const bell = () => container.querySelector('.notification-bell-button')
const panel = () => container.querySelector('.notification-panel')
const byText = (selector, text) => [...container.querySelectorAll(selector)].find(el => el.textContent.trim() === text)

afterEach(async () => {
  if (!root) return
  await act(async () => root.unmount())
  container.remove()
  root = undefined
})

test('the bell in the navigation panel shows how many notifications are unread', async t => {
  backend(t)
  await render('/dashboard')
  assert.ok(container.querySelector('nav[aria-label="Your sections"] .notification-bell'), 'the bell is in the panel')
  assert.equal(bell().getAttribute('aria-label'), 'Notifications, 2 unread')
  assert.equal(bell().querySelector('.notification-count').textContent, '2')
  assert.equal(bell().getAttribute('aria-expanded'), 'false')
})

test('with nothing unread the bell shows no count', async t => {
  backend(t, { notifications: [SEEN] })
  await render('/dashboard')
  assert.equal(bell().getAttribute('aria-label'), 'Notifications')
  assert.equal(bell().querySelector('.notification-count'), null)
})

test('opening the bell lists notifications newest first, unread ones marked', async t => {
  backend(t)
  await render('/dashboard')
  await click(bell())
  assert.equal(bell().getAttribute('aria-expanded'), 'true')
  const items = [...panel().querySelectorAll('.notification-list li')]
  assert.deepEqual(items.map(li => li.querySelector('.notification-message').textContent), [NEWEST.message, OLDER.message, SEEN.message])
  assert.deepEqual(items.map(li => li.className), ['is-unread', 'is-unread', 'is-read'])
  assert.match(items[0].textContent, /^Unread: /)
  assert.equal(items[0].querySelector('time').getAttribute('datetime'), NEWEST.created_at)
  assert.equal(items[2].querySelector('button'), null, 'a read notification has no Mark as read button')
})

test('mark as read updates the item and the count, on the server too', async t => {
  backend(t)
  await render('/dashboard')
  await click(bell())
  await click(panel().querySelectorAll('.notification-list li')[1].querySelector('button'))
  assert.ok(requests.includes('POST /notifications/1/read'))
  assert.deepEqual([...panel().querySelectorAll('.notification-list li')].map(li => li.className), ['is-unread', 'is-read', 'is-read'])
  assert.equal(bell().getAttribute('aria-label'), 'Notifications, 1 unread')
  assert.equal(server.notifications.find(n => n.id === 1).read, true)
})

test('mark all as read clears the count and the button', async t => {
  backend(t)
  await render('/dashboard')
  await click(bell())
  await click(byText('.notification-panel button', 'Mark all as read'))
  assert.ok(requests.includes('POST /notifications/read-all'))
  assert.ok([...panel().querySelectorAll('.notification-list li')].every(li => li.className === 'is-read'))
  assert.equal(bell().getAttribute('aria-label'), 'Notifications')
  assert.equal(byText('.notification-panel button', 'Mark all as read'), undefined)
})

test('opening a notification goes to its application, marks it read and closes the list', async t => {
  backend(t)
  await render('/faq')
  await click(bell())
  await click(byText('.notification-message', NEWEST.message))
  assert.equal(router.state.location.pathname, '/dashboard/a1')
  assert.ok(requests.includes('POST /notifications/2/read'))
  assert.equal(panel(), null)
  // The count is asked for again on the new page, and the server now says 1.
  assert.equal(bell().getAttribute('aria-label'), 'Notifications, 1 unread')
})

test('the count is refreshed on every page change', async t => {
  backend(t)
  await render('/dashboard')
  const before = requests.filter(r => r === 'GET /notifications/unread-count').length
  server.notifications.unshift({ ...NEWEST, id: 3, read: false })
  await click(byText('nav[aria-label="Your sections"] a', 'Passport & documents'))
  assert.equal(requests.filter(r => r === 'GET /notifications/unread-count').length, before + 1)
  assert.equal(bell().getAttribute('aria-label'), 'Notifications, 3 unread')
})

test('with no notifications the list says so', async t => {
  backend(t, { notifications: [] })
  await render('/dashboard')
  await click(bell())
  assert.match(panel().textContent, /No notifications yet/)
})

test('a failed load shows its own message with a retry, and no toast', async t => {
  backend(t, { failList: true })
  await render('/dashboard')
  await click(bell())
  assert.match(panel().querySelector('[role="alert"]').textContent, /Something went wrong on our side/)
  assert.equal(document.querySelector('[data-sonner-toast]'), null)
  assert.ok(byText('.notification-panel button', 'Try again'))
})

test('Escape and a click outside close the list', async t => {
  backend(t)
  await render('/dashboard')
  await click(bell())
  await act(async () => document.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true })))
  assert.equal(panel(), null)
  await click(bell())
  await act(async () => document.body.dispatchEvent(new dom.window.MouseEvent('mousedown', { bubbles: true })))
  assert.equal(panel(), null)
})
