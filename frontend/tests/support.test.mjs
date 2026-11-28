import assert from 'node:assert/strict'
import { registerHooks } from 'node:module'
import { afterEach, beforeEach, test } from 'node:test'
import { dom } from './dom-setup.mjs'
import { act, createElement } from 'react'
import { createRoot } from 'react-dom/client'
import { createMemoryRouter, RouterProvider } from 'react-router'

for (const name of ['Node', 'Element', 'HTMLAnchorElement', 'HTMLTextAreaElement', 'HTMLSelectElement',
  'MutationObserver', 'getComputedStyle', 'Event', 'KeyboardEvent']) {
  globalThis[name] ??= dom.window[name]
}
window.matchMedia ??= () => ({ matches: false, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} })
window.scrollTo = () => {}
Element.prototype.scrollTo ??= () => {}

registerHooks({
  load(url, context, nextLoad) {
    if (url.endsWith('.svg')) return { format: 'module', source: `export default ${JSON.stringify(url)}`, shortCircuit: true }
    return nextLoad(url, context)
  },
})

const { routes } = await import('../src/routes.tsx')
const { resetFeatureFlags } = await import('../src/features.ts')
const { default: SupportAccess } = await import('../src/support/SupportAccess.tsx')

const APP = 'a'.repeat(32)
const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
const TRAVELER = { id: 3, name: 'Maya Haddad', email: 'maya@example.com', password_enabled: true }
const SUPPORT = { id: 9, name: 'Sami Support', email: 'sami@example.com', role: 'support' }
const LATER = '2026-11-30T10:00:00Z'
const REQUEST = {
  id: 4, traveler_name: 'Maya Haddad', source: 'website', status: 'open', excerpt: 'I am not sure which passport to use.',
  application_id: APP, assignee_name: null, assigned_to_me: false, created_at: '2026-11-27T09:00:00Z', updated_at: '2026-11-27T09:00:00Z',
}
const DETAIL = { ...REQUEST, message: 'I am not sure which passport to use.', reply: null, notes: null, replied_at: null,
  resolved_at: null, access_expires_at: LATER }

let handlers
let calls
beforeEach(() => {
  resetFeatureFlags()
  calls = []
  handlers = {
    'GET /features': () => json({ support_tools: true }),
    'POST /auth/refresh': () => json({ access_token: 'token' }),
    'GET /auth/me': () => json(TRAVELER),
    'GET /auth/config': () => json({ google_client_id: null }),
  }
  globalThis.fetch = async (url, options = {}) => {
    const key = `${options.method ?? 'GET'} ${url}`
    calls.push({ key, body: options.body })
    const handler = handlers[key]
    return handler ? handler(options) : new Response(null, { status: 404 })
  }
})

let root
let container
let router
afterEach(async () => {
  await act(async () => root?.unmount())
  container?.remove()
  root = null
  document.documentElement.lang = ''
})

const settle = () => act(() => new Promise(resolve => setTimeout(resolve, 30)))
async function render(path) {
  container = document.createElement('div')
  document.body.append(container)
  router = createMemoryRouter(routes, { initialEntries: [path] })
  root = createRoot(container)
  await act(async () => root.render(createElement(RouterProvider, { router })))
  await settle()
}
async function mount(element) {
  container = document.createElement('div')
  document.body.append(container)
  root = createRoot(container)
  await act(async () => root.render(element))
  await settle()
}
const page = () => container.textContent
const button = label => [...container.querySelectorAll('button, a')].find(item => item.textContent.trim() === label)
const sent = key => calls.filter(call => call.key === key)
async function click(element) {
  assert.ok(element, 'Missing element to click')
  await act(async () => element.dispatchEvent(new window.MouseEvent('click', { bubbles: true })))
  await settle()
}
async function type(element, value) {
  const prototype = element.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype
  await act(async () => {
    Object.getOwnPropertyDescriptor(prototype, 'value').set.call(element, value)
    element.dispatchEvent(new window.Event('input', { bubbles: true }))
  })
}
async function choose(element, value) {
  await act(async () => {
    element.value = value
    element.dispatchEvent(new window.Event('change', { bubbles: true }))
  })
  await settle()
}
async function submit(form) {
  await act(async () => form.dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true })))
  await settle()
}

test('the assistant opens a request for a person with only the typed message, never the chat', async () => {
  handlers['GET /assistant/history'] = () => json([{ role: 'user', content: 'My passport number is X123' },
    { role: 'assistant', content: 'Thanks.' }])
  handlers['POST /support/requests'] = () => json({ id: 1, application_id: null, source: 'website', message: 'Please call me back',
    status: 'open', reply: null, created_at: LATER, updated_at: LATER, replied_at: null, resolved_at: null }, 201)
  await render('/faq')
  await click(button('Ask the assistant'))
  await click(button('Talk to a person'))
  assert.match(page(), /Your conversation with the assistant is not shared/)
  assert.equal(container.querySelector('.talk-share'), null, 'no application to share outside an application page')
  await type(container.querySelector('.talk-to-person textarea'), '  Please call me back ')
  await submit(container.querySelector('.talk-to-person'))
  const [request] = sent('POST /support/requests')
  assert.deepEqual(JSON.parse(request.body), { message: 'Please call me back' })
  assert.doesNotMatch(request.body, /X123|Thanks/)
  assert.match(page(), /Your message is with our support team/)
  await click(button('Back to the assistant'))
  assert.ok(container.querySelector('.assistant-panel textarea[placeholder="Ask a question…"]'))
})

test("on an application's page the traveler can let support read it in one click while asking", async () => {
  handlers[`GET /assistant/history?application_id=${APP}`] = () => json([])
  handlers['POST /support/requests'] = () => json({ id: 2, application_id: APP, source: 'website', message: 'Help',
    status: 'open', reply: null, created_at: LATER, updated_at: LATER, replied_at: null, resolved_at: null }, 201)
  await render(`/dashboard/${APP}`)
  await click(button('Ask the assistant'))
  await click(button('Talk to a person'))
  const share = container.querySelector('.talk-share input')
  assert.equal(share.checked, false, 'consent is never pre-ticked')
  await type(container.querySelector('.talk-to-person textarea'), 'Help')
  await click(share)
  await submit(container.querySelector('.talk-to-person'))
  assert.deepEqual(JSON.parse(sent('POST /support/requests')[0].body), { message: 'Help', application_id: APP, share_hours: 72 })
})

test("the application page's own assistant offers a person too, with the same one-click share", async () => {
  const purpose = { id: 'purpose', step_id: 'eligibility', type: 'choice', prompt: "What's the main purpose of your trip?",
    help: null, required: true, options: [{ value: 'tourism', label: 'Tourism' }], max_length: 200, date_min: null, date_max: null }
  const summary = { id: APP, title: 'Schengen short-stay visa (type C)', destination: 'SCHENGEN', visa_type: 'short_stay',
    destination_label: 'Schengen area', visa_label: 'Short stay (type C)', status: 'DRAFT', group_size: 1, place: null,
    trip_date: null, answered: 0, total: 4, created_at: LATER, updated_at: LATER, archived_at: null, submitted_at: null, decided_at: null }
  handlers['GET /applications'] = () => json([summary])
  handlers[`GET /applications/${APP}`] = () => json({ ...summary, steps: [{ id: 'eligibility', title: 'Eligibility' }],
    progress: { step_number: 1, step_count: 1, step_title: 'Eligibility', answered: 0, total: 4 },
    answers: [], current: purpose, stopped: null, cleared: [] })
  handlers[`GET /assistant/history?application_id=${APP}`] = () => json([])
  handlers['POST /support/requests'] = () => json({ id: 3, application_id: APP, source: 'website', message: 'Stuck',
    status: 'open', reply: null, created_at: LATER, updated_at: LATER, replied_at: null, resolved_at: null }, 201)
  await render(`/apply/${APP}`)
  await click(button('Talk to a person'))
  assert.equal(container.querySelector('#chat-ask-input'), null, 'the question box gives way to the message to support')
  await type(container.querySelector('.talk-to-person textarea'), 'Stuck')
  await click(container.querySelector('.talk-share input'))
  await submit(container.querySelector('.talk-to-person'))
  assert.deepEqual(JSON.parse(sent('POST /support/requests')[0].body), { message: 'Stuck', application_id: APP, share_hours: 72 })
  await click(button('Back to the assistant'))
  assert.ok(container.querySelector('#chat-ask-input'))
})

test('with the flag off the assistant has no Talk to a person button', async () => {
  handlers['GET /features'] = () => json({ support_tools: false })
  handlers['GET /assistant/history'] = () => json([])
  await render('/faq')
  await click(button('Ask the assistant'))
  assert.ok(container.querySelector('.assistant-panel'))
  assert.equal(button('Talk to a person'), undefined)
})

test('a server refusal is shown in the escalation form and nothing is lost', async () => {
  handlers['GET /assistant/history'] = () => json([])
  handlers['POST /support/requests'] = () => json({ detail: 'Too many attempts. Please try again later.' }, 429)
  await render('/faq')
  await click(button('Ask the assistant'))
  await click(button('Talk to a person'))
  await type(container.querySelector('.talk-to-person textarea'), 'Hello')
  await submit(container.querySelector('.talk-to-person'))
  assert.match(container.querySelector('.talk-to-person [role="alert"]').textContent, /Too many requests/)
  assert.equal(container.querySelector('.talk-to-person textarea').value, 'Hello')
})

test('the traveler shares one application for a chosen time and can stop at any time', async () => {
  let grants = []
  handlers['GET /support/access'] = () => json(grants)
  handlers['POST /support/access'] = options => {
    const body = JSON.parse(options.body)
    grants = [{ id: 5, application_id: body.application_id, title: 'Schengen short-stay visa (type C)', created_at: LATER, expires_at: LATER }]
    return json(grants[0], 201)
  }
  handlers['DELETE /support/access/5'] = () => { grants = []; return new Response(null, { status: 204 }) }
  await mount(createElement(SupportAccess, { applicationId: APP }))
  assert.match(page(), /never see your passport photo or documents/)
  await choose(container.querySelector('select'), '168')
  await click(button('Share with support'))
  assert.deepEqual(JSON.parse(sent('POST /support/access')[0].body), { application_id: APP, hours: 168 })
  assert.match(page(), /can read this application until/)
  await click(button('Stop sharing now'))
  assert.equal(sent('DELETE /support/access/5').length, 1)
  assert.ok(button('Share with support'))
})

test("the account page lists the traveler's requests with support's reply and what they share", async () => {
  handlers['GET /profile'] = () => json({})
  handlers['GET /sessions'] = () => json([])
  handlers['GET /assistant/history'] = () => json([])
  handlers['GET /support/requests'] = () => json([{ id: 1, application_id: null, source: 'website', message: 'Can I use my old passport?',
    status: 'resolved', reply: 'Use the passport you will travel on.', created_at: LATER, updated_at: LATER, replied_at: LATER, resolved_at: LATER }])
  let grants = [{ id: 5, application_id: APP, title: 'Schengen short-stay visa (type C)', created_at: LATER, expires_at: LATER }]
  handlers['GET /support/access'] = () => json(grants)
  handlers['DELETE /support/access/5'] = () => { grants = []; return new Response(null, { status: 204 }) }
  window.history.replaceState(null, '', '/account')
  await render('/account')
  await settle()
  const section = container.querySelector('#support-requests')
  assert.ok(section, 'Help requests section on the account page')
  assert.match(section.textContent, /Resolved.*Can I use my old passport\?.*Support replied.*Use the passport you will travel on\./s)
  assert.match(section.textContent, /Schengen short-stay visa \(type C\)/)
  await click([...section.querySelectorAll('button')].find(item => item.textContent === 'Stop sharing'))
  assert.equal(sent('DELETE /support/access/5').length, 1)
  assert.match(section.textContent, /You aren't sharing any application with support/)
  window.history.replaceState(null, '', '/')
})

function staffBackend(profile = SUPPORT, extra = {}) {
  Object.assign(handlers, {
    'GET /admin-api/support/me': () => json(profile),
    'GET /admin-api/support/requests?page=1&page_size=20': () => json({ items: [REQUEST], total: 1, page: 1, page_size: 20 }),
    'GET /admin-api/support/shared': () => json([{ application_id: APP, title: 'Schengen short-stay visa (type C)',
      traveler_name: 'Maya Haddad', expires_at: LATER }]),
    'GET /admin-api/support/requests/4': () => json(DETAIL),
    ...extra,
  })
}

test('support staff get the queue in the back-office, without the admin-only pages', async () => {
  staffBackend()
  await render('/admin/support')
  assert.equal(container.querySelector('.admin-badge').textContent, 'Support')
  const nav = [...container.querySelectorAll('.admin-nav a')].map(link => link.textContent)
  assert.deepEqual(nav, ['Support requests'])
  assert.match(page(), /1 request/)
  assert.match(page(), /Maya Haddad.*I am not sure which passport to use\..*Open.*Nobody yet.*Website/s)
  assert.ok(container.querySelector(`a[href="/admin/support/applications/${APP}"]`), 'shared applications are listed')
  await choose(container.querySelector('#support-status'), 'resolved')
  assert.equal(router.state.location.search, '?status=resolved')
  assert.ok(sent('GET /admin-api/support/requests?status=resolved&page=1&page_size=20').length)
})

test('support staff find the queue from their applications page; travelers do not', async () => {
  handlers['GET /applications'] = () => json([])
  handlers['GET /admin-api/me'] = () => json({ detail: 'Admin access required.' }, 403)
  handlers['GET /admin-api/support/me'] = () => json(SUPPORT)
  await render('/dashboard')
  assert.equal(container.querySelector('.dash-head a[href="/admin/support"]')?.textContent, 'Support requests')
  assert.equal(container.querySelector('.dash-head a[href="/backoffice"]'), null)
  await act(async () => root.unmount())
  container.remove()
  handlers['GET /admin-api/support/me'] = () => json({ detail: 'Admin access required.' }, 403)
  await render('/dashboard')
  assert.equal(container.querySelector('.dash-head a[href="/admin/support"]'), null)
})

test('admins see support requests and help content next to their own pages', async () => {
  staffBackend({ ...SUPPORT, role: 'admin', name: 'Adam Admin' })
  await render('/admin/support')
  const nav = [...container.querySelectorAll('.admin-nav a')].map(link => link.textContent)
  assert.deepEqual(nav, ['Users', 'Monitoring', 'Support requests', 'Help content'])
})

test('a request can be assigned, answered, noted and resolved', async () => {
  let current = { ...DETAIL }
  const respond = changes => { current = { ...current, ...changes }; return json(current) }
  staffBackend(SUPPORT, {
    'GET /admin-api/support/requests/4': () => json(current),
    'POST /admin-api/support/requests/4/assign': () => respond({ assigned_to_me: true, assignee_name: 'Sami Support', status: 'in_progress' }),
    'PUT /admin-api/support/requests/4/reply': options => respond({ reply: JSON.parse(options.body).reply, replied_at: LATER }),
    'PUT /admin-api/support/requests/4/notes': options => respond({ notes: JSON.parse(options.body).notes }),
    'PUT /admin-api/support/requests/4/status': options => respond({ status: JSON.parse(options.body).status }),
  })
  await render('/admin/support/requests/4')
  assert.match(page(), /Help request from Maya Haddad/)
  assert.ok(container.querySelector(`a[href="/admin/support/applications/${APP}"]`))
  await click(button('Assign to me'))
  assert.equal(button('Assign to me'), undefined)
  await type(container.querySelector('#support-reply'), 'Use the passport you will travel on.')
  await submit(container.querySelector('#support-reply').form)
  assert.deepEqual(JSON.parse(sent('PUT /admin-api/support/requests/4/reply')[0].body), { reply: 'Use the passport you will travel on.' })
  await type(container.querySelector('#support-notes'), 'Explained passports.')
  await submit(container.querySelector('#support-notes').form)
  assert.deepEqual(JSON.parse(sent('PUT /admin-api/support/requests/4/notes')[0].body), { notes: 'Explained passports.' })
  await choose(container.querySelector('#support-request-status'), 'resolved')
  await click(button('Update status'))
  assert.deepEqual(JSON.parse(sent('PUT /admin-api/support/requests/4/status')[0].body), { status: 'resolved' })
  assert.match(page(), /Resolved/)
})

test('without a grant the request says the application is not shared, and there is no link', async () => {
  staffBackend(SUPPORT, { 'GET /admin-api/support/requests/4': () => json({ ...DETAIL, access_expires_at: null }) })
  await render('/admin/support/requests/4')
  assert.equal(container.querySelector(`a[href="/admin/support/applications/${APP}"]`), null)
  assert.match(page(), /not shared with support/)
})

test('a shared application is read only: answers as shown, the check and the timeline', async () => {
  staffBackend(SUPPORT, {
    [`GET /admin-api/support/applications/${APP}`]: () => json({
      application: { id: APP, title: 'Schengen short-stay visa (type C)', destination: 'SCHENGEN', visa_type: 'short_stay',
        destination_label: 'Schengen area', visa_label: 'Short stay (type C)', status: 'DRAFT', group_size: 1, place: 'FRA',
        trip_date: null, answered: 2, total: 20, created_at: LATER, updated_at: LATER, archived_at: null, submitted_at: null, decided_at: null },
      traveler_name: 'Maya Haddad', access_expires_at: LATER,
      answers: [{ question_id: 'purpose', step: 'Your trip', prompt: 'What is the purpose of your trip?', type: 'choice',
        options: [{ value: 'tourism', label: 'Tourism' }], value: 'tourism', source: 'traveler' },
      { question_id: 'stay_over_90_days', step: 'Your trip', prompt: 'Will you stay more than 90 days?', type: 'yes_no',
        options: null, value: false, source: 'traveler' }],
      check: { ready: false, answered: 2, total: 20, issues: [{ kind: 'missing', severity: 'blocking',
        message: 'Not answered yet: Where were you born?', question_id: 'place_of_birth', step_id: 'about_you' }],
      readiness_score: 10, readiness_reason: '2 of 20 applicable questions answered.' },
      timeline: { id: APP, archived_at: null, events: [{ kind: 'started', at: LATER, text: 'Started the Schengen application',
        question_id: null, step_title: null }], next: { kind: 'answer', text: 'Next question: Where were you born?' } },
    }),
  })
  await render(`/admin/support/applications/${APP}`)
  assert.match(page(), /Maya Haddad shared this application until/)
  assert.match(page(), /passport images and documents are not shown/)
  assert.match(page(), /What is the purpose of your trip\?Tourism/)
  assert.match(page(), /Will you stay more than 90 days\?No/)
  assert.match(page(), /Blocking.*Not answered yet: Where were you born\?/s)
  assert.match(page(), /Started the Schengen application/)
  assert.equal(container.querySelectorAll('#admin-main img').length, 0)
  assert.equal(container.querySelectorAll('#admin-main form, #admin-main input, #admin-main textarea').length, 0, 'nothing to edit')
})

test('an expired or revoked grant reads as not shared', async () => {
  staffBackend(SUPPORT, { [`GET /admin-api/support/applications/${APP}`]: () => json({ detail: 'Application not found.' }, 404) })
  await render(`/admin/support/applications/${APP}`)
  assert.match(page(), /not shared with support, or its access has ended/)
})

test('a traveler is sent home from the support pages with a notice', async () => {
  handlers['GET /admin-api/support/me'] = () => json({ detail: 'Admin access required.' }, 403)
  await render('/admin/support')
  assert.equal(router.state.location.pathname, '/')
  assert.match(document.body.textContent, /Support or admin access required\./)
  assert.deepEqual(calls.filter(call => call.key.startsWith('GET /admin-api')).map(call => call.key), ['GET /admin-api/support/me'])
})

test('with the flag off the support pages do not exist', async () => {
  handlers['GET /admin-api/support/me'] = () => json({ detail: 'Not Found' }, 404)
  await render('/admin/support')
  assert.match(page(), /Page not found/)
})
