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

registerHooks({
  load(url, context, nextLoad) {
    if (url.endsWith('.svg')) return { format: 'module', source: `export default ${JSON.stringify(url)}`, shortCircuit: true }
    return nextLoad(url, context)
  },
})

const { routes } = await import('../src/routes.tsx')
const { resetFeatureFlags } = await import('../src/features.ts')
const { en: { faqs } } = await import('../src/i18n/en.ts')
const { ar } = await import('../src/i18n/ar.ts')
const { savedChoice } = await import('../src/i18n/language.ts')

const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
const ADMIN = { id: 1, name: 'Adam Admin', email: 'adam@example.com', role: 'admin' }
const entry = (id, extra = {}) => ({ id, question: `Question ${id}?`, answer: `Answer ${id}.`, category: 'General', language: 'en', ...extra })
const adminEntry = (id, extra = {}) => ({ ...entry(id), position: id, published: true, updated_at: '2026-11-27T09:00:00Z',
  updated_by_name: null, ...extra })

let handlers
let calls
beforeEach(() => {
  resetFeatureFlags()
  calls = []
  handlers = {
    'GET /features': () => json({ support_tools: true }),
    'POST /auth/refresh': () => json({ detail: 'Not signed in.' }, 401),
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
  document.body.querySelectorAll('[data-sonner-toaster]').forEach(node => node.remove())
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
const page = () => container.textContent
const questions = () => [...container.querySelectorAll('details.faq-item summary')].map(summary => summary.textContent.trim())
const button = label => [...container.querySelectorAll('button')].find(item => item.textContent.trim() === label)
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

test('the FAQ page shows the published entries from the API, with safe minimal formatting', async () => {
  handlers['GET /help-content?language=en'] = () => json([
    entry(1, { answer: 'First paragraph with **bold** words.\n\n- one\n- two\n\nSee [the guides](/visas) or [GOV.UK](https://www.gov.uk) but not [this](javascript:alert(1)).' }),
    entry(2, { answer: '<img src=x onerror=alert(1)>' }),
  ])
  await render('/faq')
  assert.deepEqual(questions(), ['Question 1?', 'Question 2?'])
  const first = container.querySelector('details.faq-item')
  assert.equal(first.querySelector('strong').textContent, 'bold')
  assert.deepEqual([...first.querySelectorAll('li')].map(item => item.textContent), ['one', 'two'])
  assert.deepEqual([...first.querySelectorAll('a')].map(link => [link.textContent, link.getAttribute('href')]),
    [['the guides', '/visas'], ['GOV.UK', 'https://www.gov.uk']])
  assert.equal(first.querySelector('a[href="https://www.gov.uk"]').getAttribute('rel'), 'noreferrer')
  assert.match(first.textContent, /\[this\]\(javascript:alert\(1\)\)/, 'an unsafe link stays plain text')
  assert.equal(container.querySelectorAll('.faq-list img').length, 0, 'HTML in an answer is text, never markup')
  assert.match(page(), /<img src=x onerror=alert\(1\)>/)
})

test('when the API fails or has nothing published the bundled questions stay', async () => {
  handlers['GET /help-content?language=en'] = () => json({ detail: 'Down' }, 503)
  await render('/faq')
  assert.deepEqual(questions(), faqs.map(item => item.question))
  await act(async () => root.unmount())
  container.remove()
  handlers['GET /help-content?language=en'] = () => json([])
  await render('/faq')
  assert.equal(questions().length, faqs.length)
})

test('entries in more than one category are grouped under headings', async () => {
  handlers['GET /help-content?language=en'] = () => json([entry(1), entry(2, { category: 'Payments' }), entry(3)])
  await render('/faq')
  assert.deepEqual([...container.querySelectorAll('.container h2')].map(heading => heading.textContent), ['General', 'Payments'])
  assert.deepEqual(questions(), ['Question 1?', 'Question 3?', 'Question 2?'])
})

test('an Arabic page asks for Arabic entries and shows the bundled Arabic questions when there are none', async (t) => {
  t.mock.method(savedChoice, 'read', () => 'ar')
  handlers['GET /features'] = () => json({ support_tools: true, arabic: true })
  handlers['GET /help-content?language=ar'] = () => json([])
  handlers['GET /help-content?language=en'] = () => json([entry(1)])
  await render('/faq')
  assert.deepEqual(sent('GET /help-content?language=en'), [], 'an Arabic page never lists English entries')
  assert.ok(sent('GET /help-content?language=ar').length)
  assert.deepEqual(questions(), ar.faqs.map(item => item.question))
  await act(async () => root.unmount())
  container.remove()
  handlers['GET /help-content?language=ar'] = () => json([entry(7, { question: 'هل تحفظ جوازي؟', answer: 'نعم.', language: 'ar' })])
  await render('/faq')
  assert.deepEqual(questions(), ['هل تحفظ جوازي؟'])
  const item = container.querySelector('details.faq-item')
  assert.equal(item.getAttribute('lang'), 'ar')
  assert.equal(item.getAttribute('dir'), 'auto')
})

test('the home page shows the first five published entries', async () => {
  handlers['GET /help-content?language=en'] = () => json([1, 2, 3, 4, 5, 6, 7].map(id => entry(id)))
  await render('/')
  assert.deepEqual(questions(), ['Question 1?', 'Question 2?', 'Question 3?', 'Question 4?', 'Question 5?'])
})

function adminBackend(entries) {
  Object.assign(handlers, {
    'POST /auth/refresh': () => json({ access_token: 'token' }),
    'GET /auth/me': () => json({ id: ADMIN.id, name: ADMIN.name, email: ADMIN.email }),
    'GET /admin-api/me': () => json(ADMIN),
    'GET /admin-api/help-content?language=en': () => json(entries()),
    'GET /admin-api/help-content?language=ar': () => json([]),
  })
}

test('admins add, publish, reorder and delete entries without a deploy', async () => {
  let entries = [adminEntry(1), adminEntry(2, { published: false })]
  adminBackend(() => entries)
  handlers['POST /admin-api/help-content'] = options => {
    const created = adminEntry(3, JSON.parse(options.body))
    entries = [...entries, created]
    return json(created, 201)
  }
  handlers['PUT /admin-api/help-content/2'] = options => {
    entries = entries.map(item => item.id === 2 ? { ...item, ...JSON.parse(options.body) } : item)
    return json(entries[1])
  }
  handlers['PUT /admin-api/help-content/order'] = options => {
    const { ids } = JSON.parse(options.body)
    entries = ids.map((id, index) => ({ ...entries.find(item => item.id === id), position: index + 1 }))
    return json(entries)
  }
  handlers['DELETE /admin-api/help-content/1'] = () => { entries = entries.filter(item => item.id !== 1); return new Response(null, { status: 204 }) }
  await render('/admin/help')
  assert.match(page(), /Question 1\?.*Published.*Question 2\?.*Hidden/s)

  await type(container.querySelector('#help-question'), 'Can I pay later?')
  await type(container.querySelector('#help-category'), 'Payments')
  await type(container.querySelector('#help-answer'), 'Yes, **before** you submit.')
  assert.equal(container.querySelector('.admin-card strong + p strong').textContent, 'before', 'the preview formats the answer')
  await act(async () => container.querySelector('#help-question').form.dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true })))
  await settle()
  assert.deepEqual(JSON.parse(sent('POST /admin-api/help-content')[0].body),
    { question: 'Can I pay later?', answer: 'Yes, **before** you submit.', category: 'Payments', language: 'en', published: false })

  await click(container.querySelector('button[aria-label="Publish: Question 2?"]'))
  assert.equal(JSON.parse(sent('PUT /admin-api/help-content/2')[0].body).published, true)

  await click(container.querySelector('button[aria-label="Move down: Question 1?"]'))
  assert.deepEqual(JSON.parse(sent('PUT /admin-api/help-content/order')[0].body), { language: 'en', ids: [2, 1, 3] })

  await click(container.querySelector('button[aria-label="Delete: Question 1?"]'))
  assert.equal(sent('DELETE /admin-api/help-content/1').length, 0, 'deleting asks first')
  await click(button('Yes, delete'))
  assert.equal(sent('DELETE /admin-api/help-content/1').length, 1)
  assert.doesNotMatch(page(), /Question 1\?/)
})

test('editing an entry sends the whole entry and switching language lists that language', async () => {
  let entries = [adminEntry(1)]
  adminBackend(() => entries)
  handlers['PUT /admin-api/help-content/1'] = options => {
    entries = [{ ...entries[0], ...JSON.parse(options.body) }]
    return json(entries[0])
  }
  await render('/admin/help')
  await click(container.querySelector('button[aria-label="Edit: Question 1?"]'))
  assert.equal(container.querySelector('#help-question').value, 'Question 1?')
  await type(container.querySelector('#help-answer'), 'A clearer answer.')
  await act(async () => container.querySelector('#help-question').form.dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true })))
  await settle()
  assert.deepEqual(JSON.parse(sent('PUT /admin-api/help-content/1')[0].body),
    { question: 'Question 1?', answer: 'A clearer answer.', category: 'General', language: 'en', published: true })
  const select = container.querySelector('#help-language')
  await act(async () => { select.value = 'ar'; select.dispatchEvent(new window.Event('change', { bubbles: true })) })
  await settle()
  assert.ok(sent('GET /admin-api/help-content?language=ar').length)
  assert.match(page(), /No Arabic entries yet/)
})

test('help content editing is an admin page that support staff and travelers cannot open', async () => {
  handlers['POST /auth/refresh'] = () => json({ access_token: 'token' })
  handlers['GET /auth/me'] = () => json({ id: 9, name: 'Sami', email: 'sami@example.com' })
  handlers['GET /admin-api/me'] = () => json({ detail: 'Admin access required.' }, 403)
  await render('/admin/help')
  assert.equal(router.state.location.pathname, '/')
  assert.equal(sent('GET /admin-api/help-content?language=en').length, 0)
})
