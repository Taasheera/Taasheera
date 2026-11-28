import assert from 'node:assert/strict'
import { registerHooks } from 'node:module'
import { afterEach, beforeEach, test } from 'node:test'
import { dom } from './dom-setup.mjs'
import { act, createElement } from 'react'
import { createRoot } from 'react-dom/client'
import { createMemoryRouter, RouterProvider } from 'react-router'

for (const name of ['Node', 'Element', 'HTMLAnchorElement', 'HTMLTextAreaElement', 'HTMLSelectElement',
  'MutationObserver', 'getComputedStyle', 'File', 'Event', 'KeyboardEvent']) {
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
const { safePage } = await import('../src/activityPage.ts')
const { resetFeatureFlags } = await import('../src/features.ts')
const { default: CompletenessCheck } = await import('../src/application/CompletenessCheck.tsx')
const { default: DocumentChecklist } = await import('../src/documents/DocumentChecklist.tsx')

const APP = 'b'.repeat(32)
const OTHER = 'c'.repeat(32)
const FILE = 'd'.repeat(32)
const USE = 'e'.repeat(32)
const ALL_ON = { supporting_documents: true, document_vault: true, document_translation: true, passport_rules: true, photo_check: true }
const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
const use = (extra = {}) => ({
  id: FILE, application_id: APP, application_title: 'Schengen short-stay visa (type C)', place: 'France',
  requirement: 'bank_statements', requirement_label: 'Bank statements', archived: false, ...extra,
})
const vaultBody = (extra = {}) => ({
  documents: [{
    id: FILE, label: 'Bank statements', filename: 'statement.pdf', content_type: 'application/pdf', size_bytes: 250_000,
    uploaded_at: '2026-11-16T10:00:00Z', uses: [use()],
    languages: [{ code: 'en', label: 'English' }, { code: 'fr', label: 'French' }],
  }],
  targets: [
    { application_id: APP, title: 'Schengen short-stay visa (type C)', place: 'France',
      requirements: [{ id: 'photo', label: 'Passport photo' }, { id: 'bank_statements', label: 'Bank statements' }] },
    { application_id: OTHER, title: 'Japan short-term stay visa', place: null,
      requirements: [{ id: 'photo', label: 'Passport photo' }, { id: 'bank_statements', label: 'Bank statements' }] },
  ],
  ...extra,
})
const passportBody = (extra = {}) => ({
  blank_pages: null,
  rules: [{
    questionnaire_id: 'schengen-short-stay', title: 'Schengen short-stay visa (type C)',
    requirements: ['Valid until 3 months after you leave the Schengen area', 'At least 2 blank visa pages'],
    blank_pages: 2, note: null, checked_on: '2026-11-27',
    sources: [{ label: 'EU Visa Code, article 12', url: 'https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:02009R0810-20200202' }],
  }],
  ...extra,
})

let handlers
let calls
globalThis.fetch = async (url, options = {}) => {
  const key = `${options.method ?? 'GET'} ${url}`
  calls.push({ key, body: options.body })
  const handler = handlers[key]
  assert.ok(handler, `Unexpected request: ${key}`)
  return handler(options)
}
const signedIn = extra => ({
  'POST /auth/refresh': () => json({ access_token: 'token' }),
  'GET /auth/me': () => json({ id: 1, name: 'Anna', email: 'anna@example.com' }),
  'POST /activity/events': () => new Response(null, { status: 204 }),
  'GET /features': () => json(ALL_ON),
  'GET /vault/documents': () => json(vaultBody()),
  'GET /vault/passport': () => json(passportBody()),
  ...extra,
})

let root
let container
async function render(path, routeList = routes) {
  container = document.createElement('div')
  document.body.append(container)
  const router = createMemoryRouter(routeList, { initialEntries: [path] })
  root = createRoot(container)
  await act(async () => root.render(createElement(RouterProvider, { router })))
  await settle()
  return router
}
const settle = () => act(() => new Promise(resolve => setTimeout(resolve, 40)))
const text = () => container.textContent
const button = label => [...container.querySelectorAll('button, a')].find(b => b.textContent.trim() === label)
async function click(element) {
  await act(async () => element.click())
  await settle()
}
async function choose(select, value) {
  await act(async () => {
    select.value = value
    select.dispatchEvent(new dom.window.Event('change', { bubbles: true }))
  })
}
async function type(input, value) {
  const setter = Object.getOwnPropertyDescriptor(dom.window.HTMLInputElement.prototype, 'value').set
  await act(async () => {
    setter.call(input, value)
    input.dispatchEvent(new dom.window.Event('input', { bubbles: true }))
  })
}
const sent = key => JSON.parse(calls.find(call => call.key === key).body)

beforeEach(() => { calls = []; handlers = {}; resetFeatureFlags() })
afterEach(() => {
  if (root) act(() => root.unmount())
  container?.remove()
  root = container = undefined
  document.body.replaceChildren()
})

test('the vault page has its own activity page id', () => {
  assert.equal(safePage('/documents', ''), 'documents')
  assert.equal(safePage('/documents/extra', ''), 'not-found')
})

test('the vault lists each file, where it is used, and the passport rules with official sources', async () => {
  handlers = signedIn()
  await render('/documents')
  assert.match(text(), /Your documents/)
  const card = container.querySelector('.vault-card')
  assert.match(card.textContent, /Bank statements.*statement\.pdf.*244 KB/)
  assert.match(card.textContent, /Used in.*Schengen short-stay visa \(type C\), France · Bank statements/)
  assert.match(text(), /Your passport's blank pages/)
  assert.match(text(), /At least 2 blank visa pages/)
  const source = [...container.querySelectorAll('.vault-rules a')].find(a => a.textContent === 'EU Visa Code, article 12')
  assert.ok(source.href.startsWith('https://eur-lex.europa.eu/') && source.rel.includes('noopener'))
})

test('a file can be added to another application without uploading it again', async () => {
  handlers = signedIn({ [`POST /vault/documents/${FILE}/attachments`]: () => json(use({ id: USE, application_id: OTHER }), 201) })
  await render(`/documents?application=${OTHER}`)
  const [application, requirement] = container.querySelectorAll('.vault-card select')
  assert.equal(application.value, OTHER)
  assert.equal(requirement.value, 'bank_statements')
  await choose(requirement, 'photo')
  await click(button('Add to application'))
  assert.deepEqual(sent(`POST /vault/documents/${FILE}/attachments`), { application_id: OTHER, requirement: 'photo' })
  assert.equal(calls.filter(call => call.key === 'GET /vault/documents').length, 2)
})

test('requirements a file already fills are not offered again', async () => {
  handlers = signedIn()
  await render('/documents')
  const [application, requirement] = container.querySelectorAll('.vault-card select')
  assert.equal(application.value, APP)
  assert.deepEqual([...requirement.options].map(option => option.value), ['photo'])
})

test('deleting asks first, says how many applications lose it, then deletes it everywhere', async () => {
  handlers = signedIn({
    'GET /vault/documents': () => json(vaultBody({ documents: [{ ...vaultBody().documents[0], uses: [use(), use({ id: USE, application_id: OTHER })] }] })),
    [`DELETE /vault/documents/${FILE}`]: () => new Response(null, { status: 204 }),
  })
  await render('/documents')
  await click(container.querySelector('[data-activity-id="vault-delete"]'))
  assert.match(text(), /Delete this file everywhere\? 2 applications will no longer have it, and it can't be undone\./)
  await click(button('Keep it'))
  assert.ok(!calls.some(call => call.key.startsWith('DELETE')))
  await click(container.querySelector('[data-activity-id="vault-delete"]'))
  await click(button('Delete everywhere'))
  assert.ok(calls.some(call => call.key === `DELETE /vault/documents/${FILE}`))
  assert.equal(calls.filter(call => call.key === 'GET /vault/documents').length, 2)
})

test('a translation is shown with the machine translation notice', async () => {
  handlers = signedIn({
    [`POST /vault/documents/${FILE}/translation`]: () => json({
      document_id: FILE, language: 'fr', language_label: 'French', text: 'Relevé de compte', truncated: true,
      notice: 'It is not a certified translation. This is not legal or immigration advice.',
    }),
  })
  await render('/documents')
  assert.match(text(), /sends the text we read from this file to Google's Gemini AI service/)
  await choose(container.querySelector(`#translate-${FILE}`), 'fr')
  await click(button('Translate'))
  assert.deepEqual(sent(`POST /vault/documents/${FILE}/translation`), { language: 'fr' })
  const figure = container.querySelector('.vault-translation')
  assert.match(figure.textContent, /Machine translation, not a certified translation\..*not legal or immigration advice/)
  assert.match(figure.textContent, /only its first part was translated.*Relevé de compte/)
})

test('translation errors from the server are shown', async () => {
  handlers = signedIn({
    [`POST /vault/documents/${FILE}/translation`]: () => json({ detail: "Translation isn't set up on this server yet." }, 503),
  })
  await render('/documents')
  await click(button('Translate'))
  assert.match(container.querySelector('.vault-translate [role=alert]').textContent, /Translation isn't available right now/)
})

test('the blank page count is checked in the browser and saved', async () => {
  handlers = signedIn({ 'PUT /vault/passport': () => json(passportBody({ blank_pages: 3 })) })
  await render('/documents#passport')
  const input = container.querySelector('#blank-pages')
  assert.equal(input.value, '')
  await click(button('Save'))
  assert.match(text(), /Enter a whole number of pages/)
  assert.ok(!calls.some(call => call.key === 'PUT /vault/passport'))
  await type(input, '3')
  await click(button('Save'))
  assert.deepEqual(sent('PUT /vault/passport'), { blank_pages: 3 })
  assert.equal(input.value, '3')
})

test('the page is unknown while the vault is switched off', async () => {
  handlers = signedIn({ 'GET /features': () => json({ supporting_documents: true, document_vault: false }) })
  await render('/documents')
  assert.match(text(), /Page not found/)
  assert.ok(!calls.some(call => call.key.startsWith('GET /vault')))
})

test('completeness issues link to the fix and to the official rule', async () => {
  handlers = signedIn({
    [`GET /applications/${APP}/check`]: () => json({
      ready: false, answered: 18, total: 18, readiness_score: 90, readiness_reason: 'Almost there.',
      issues: [{ kind: 'ineligible', severity: 'blocking', message: 'Needs 2 blank visa pages.', question_id: null,
        step_id: null, document_id: null, link: '/documents#passport', source: 'https://eur-lex.europa.eu/rule' }],
    }),
  })
  await render('/', [{ path: '/', element: createElement(CompletenessCheck, { applicationId: APP }) },
    { path: '/documents', element: createElement('p', null, 'Vault page') }])
  await click(button('Check my application'))
  const official = button('Official rule')
  assert.equal(official.getAttribute('href'), 'https://eur-lex.europa.eu/rule')
  assert.equal(official.getAttribute('target'), '_blank')
  await click(button('Update'))
  assert.match(text(), /Vault page/)
})

test('a passport photo can be checked against the destination rules from the checklist', async () => {
  const photo = { id: USE, requirement: 'photo', filename: 'me.jpg', content_type: 'image/jpeg', size_bytes: 80_000, uploaded_at: '2026-11-16T10:00:00Z' }
  handlers = signedIn({
    [`GET /applications/${APP}/documents`]: () => json({
      requirements: [{ id: 'photo', label: 'Passport photo', help: null, required: true, documents: [photo] }],
      other: [], complete: true, read_only: false,
    }),
    [`GET /applications/${APP}/documents/crosscheck`]: () => json({ reference: null, checked: [], unreadable: [], findings: [] }),
    [`GET /applications/${APP}/documents/${USE}/photo-check`]: () => json({
      spec: 'Schengen visa photo (35 x 45 mm, ICAO standard)', passed: false,
      checks: [
        { id: 'size', passed: true, message: 'It is 413 x 531 pixels; at least 413 x 531 are needed.', guidance: null },
        { id: 'colour', passed: false, message: 'It looks black and white.', guidance: 'Use a recent colour photo.' },
      ],
      source: { label: 'European Commission', url: 'https://home-affairs.ec.europa.eu/' },
      notice: "They don't recognise faces or check your pose or expression.",
    }),
  })
  await render('/', [{ path: '/', element: createElement(DocumentChecklist, { applicationId: APP }) },
    { path: '/documents', element: createElement('p', null, 'Vault page') }])
  await click(button('Check me.jpg against the photo rules'))
  const result = container.querySelector('.docs-photo-check [role=status]')
  assert.match(result.textContent, /May not meet the Schengen visa photo/)
  assert.match(result.textContent, /Needs attention: It looks black and white\. Use a recent colour photo\./)
  assert.match(result.textContent, /don't recognise faces/)
  await click(button("Use a file you've already uploaded"))
  assert.match(text(), /Vault page/)
})
