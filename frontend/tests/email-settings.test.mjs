import { dom } from './dom-setup.mjs'
import assert from 'node:assert/strict'
import { afterEach, test } from 'node:test'
import { act, createElement } from 'react'
import { createRoot } from 'react-dom/client'

for (const name of ['Node', 'Element', 'HTMLInputElement', 'MutationObserver', 'getComputedStyle']) {
  globalThis[name] ??= dom.window[name]
}

const { default: EmailSettings } = await import('../src/EmailSettings.tsx')

const ALL_ON = { application_updates: true, reminders: true, passport_expiry: true }
let root, container, requests

afterEach(async () => {
  await act(async () => root?.unmount())
  container?.remove()
  root = null
})

function serve(t, { load = () => Response.json(ALL_ON), save = body => Response.json(body) } = {}) {
  requests = []
  t.mock.method(globalThis, 'fetch', async (url, options = {}) => {
    const method = options.method ?? 'GET'
    requests.push({ url, method, body: options.body, authorization: new Headers(options.headers).get('Authorization') })
    if (url === '/auth/refresh') return Response.json({ access_token: 'test-access-token' })
    if (url === '/auth/me') return Response.json({ id: 1, name: 'Anna', email: 'anna@example.com' })
    if (url === '/profile/notification-preferences' && method === 'GET') return load()
    if (url === '/profile/notification-preferences') return save(JSON.parse(options.body))
    return new Response(null, { status: 204 })
  })
}

const settle = () => act(async () => { await new Promise(resolve => setTimeout(resolve, 20)) })

async function render() {
  container = document.createElement('div')
  document.body.append(container)
  root = createRoot(container)
  await act(async () => root.render(createElement(EmailSettings)))
  // Wait until loading finished: switches or a message.
  for (let tries = 0; tries < 50 && !container.querySelector('input, [role="alert"]'); tries += 1) await settle()
}

const box = label => [...container.querySelectorAll('label')].find(item => item.textContent.includes(label)).querySelector('input')
const toggle = async label => { await act(async () => box(label).click()); await settle() }

test('shows the three switches as saved, each described', async t => {
  serve(t, { load: () => Response.json({ ...ALL_ON, reminders: false }) })
  await render()
  assert.equal(container.querySelector('h2').textContent, 'Email notifications')
  assert.equal(box('Application updates').checked, true)
  assert.equal(box('Reminders about unfinished applications').checked, false)
  assert.equal(box('Passport expiry reminders').checked, true)
  const described = box('Passport expiry reminders').getAttribute('aria-describedby')
  assert.match(container.querySelector(`#${described}`).textContent, /Six months and one month/)
  const read = requests.find(request => request.url === '/profile/notification-preferences')
  assert.equal(read.authorization, 'Bearer test-access-token')
})

test('a change is saved straight away with all three switches', async t => {
  serve(t)
  await render()
  await toggle('Reminders about unfinished applications')
  const put = requests.find(request => request.method === 'PUT')
  assert.deepEqual(JSON.parse(put.body), { ...ALL_ON, reminders: false })
  assert.equal(box('Reminders about unfinished applications').checked, false)
  assert.equal(container.querySelector('[role="status"]').textContent, 'Saved.')
})

test('a change that could not be saved is undone and explained', async t => {
  serve(t, { save: () => new Response(null, { status: 500 }) })
  await render()
  await toggle('Application updates')
  assert.equal(box('Application updates').checked, true)
  assert.match(container.querySelector('[role="alert"]').textContent, /could not save/)
})

test('settings that cannot be loaded say so instead of showing wrong switches', async t => {
  serve(t, { load: () => new Response(null, { status: 404 }) })
  await render()
  assert.equal(container.querySelector('input'), null)
  assert.match(container.querySelector('[role="alert"]').textContent, /could not load/)
})
