import assert from 'node:assert/strict'
import { afterEach, beforeEach, test } from 'node:test'
import { dom } from './dom-setup.mjs'
import { act, createElement } from 'react'
import { createRoot } from 'react-dom/client'

for (const name of ['Node', 'Element', 'HTMLInputElement', 'getComputedStyle']) {
  globalThis[name] ??= dom.window[name]
}
// The page builds its upload with jsdom's FormData, which only takes jsdom's own files.
globalThis.File = dom.window.File

const { default: DocumentChecklist } = await import('../src/documents/DocumentChecklist.tsx')
const { checkDocumentFile, formatSize } = await import('../src/documents/api.ts')

const APP = 'b'.repeat(32)
const BASE = `/applications/${APP}/documents`
const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
const file = (id, extra = {}) => ({
  id, requirement: 'bank_statements', filename: 'statement.pdf', content_type: 'application/pdf',
  size_bytes: 250_000, uploaded_at: '2026-11-16T10:00:00Z', ...extra,
})
const requirement = (id, label, documents = [], extra = {}) => ({ id, label, help: `${label} help`, required: true, documents, ...extra })
const checklist = (extra = {}) => ({
  requirements: [
    requirement('photo', 'Passport photo'),
    requirement('bank_statements', 'Bank statements', [file('d'.repeat(32))]),
    requirement('cover_letter', 'Cover letter', [], { required: false }),
  ],
  other: [], complete: false, read_only: false, ...extra,
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
  ...extra,
})

let root
let container
async function render(props = {}) {
  container = document.createElement('div')
  document.body.append(container)
  root = createRoot(container)
  await act(async () => root.render(createElement(DocumentChecklist, { applicationId: APP, ...props })))
  await settle()
}
const settle = () => act(() => new Promise(resolve => setTimeout(resolve, 30)))
const text = () => container.textContent
const button = label => [...container.querySelectorAll('button')].find(b => b.textContent.trim() === label)
async function click(element) {
  await act(async () => element.click())
  await settle()
}
async function choose(input, chosen) {
  Object.defineProperty(input, 'files', { value: [chosen], configurable: true })
  await act(async () => input.dispatchEvent(new dom.window.Event('change', { bubbles: true })))
  await settle()
}

beforeEach(() => { calls = []; handlers = {} })
afterEach(() => {
  if (root) act(() => root.unmount())
  container?.remove()
  root = container = undefined
})

test('the checklist shows each document, what is uploaded and how many required are left', async () => {
  handlers = signedIn({ [`GET ${BASE}`]: () => json(checklist()) })
  await render()
  assert.match(text(), /Supporting documents/)
  assert.match(text(), /1 of 2 required uploaded/)
  const items = [...container.querySelectorAll('.docs-item')]
  assert.equal(items.length, 3)
  assert.match(items[0].textContent, /Passport photo, not uploaded yet.*Passport photo help.*Upload/)
  assert.ok(items[1].classList.contains('is-done'))
  assert.match(items[1].textContent, /statement\.pdf.*244 KB.*Replace.*Delete.*Add another file/)
  assert.match(items[2].textContent, /Cover letter.*Optional/)
})

test('choosing a file uploads it for that document and reloads the checklist', async () => {
  let loads = 0
  handlers = signedIn({
    [`GET ${BASE}`]: () => { loads += 1; return json(checklist()) },
    [`POST ${BASE}`]: () => json(file('e'.repeat(32), { requirement: 'photo' }), 201),
  })
  await render()
  const input = container.querySelectorAll('.docs-item')[0].querySelector('input[type=file]')
  await choose(input, new File(['jpeg'], 'me.jpg', { type: 'image/jpeg' }))
  const sent = calls.find(call => call.key === `POST ${BASE}`).body
  assert.equal(sent.get('requirement'), 'photo')
  assert.equal(sent.get('file').name, 'me.jpg')
  assert.equal(loads, 2)
})

test('a wrong file type is refused before uploading, and server messages are shown', async () => {
  handlers = signedIn({
    [`GET ${BASE}`]: () => json(checklist()),
    [`POST ${BASE}`]: () => json({ detail: 'This PDF is password-protected. Upload a copy without a password.' }, 422),
  })
  await render()
  const input = container.querySelectorAll('.docs-item')[0].querySelector('input[type=file]')
  await choose(input, new File(['x'], 'notes.docx', { type: 'application/msword' }))
  assert.match(text(), /isn't a PDF, JPEG, PNG or WebP/)
  assert.ok(!calls.some(call => call.key === `POST ${BASE}`))

  await choose(input, new File(['%PDF'], 'locked.pdf', { type: 'application/pdf' }))
  assert.match(container.querySelector('[role=alert]').textContent, /password-protected/)
})

test('deleting asks first, then removes the file', async () => {
  const id = 'd'.repeat(32)
  handlers = signedIn({
    [`GET ${BASE}`]: () => json(checklist()),
    [`DELETE ${BASE}/${id}`]: () => new Response(null, { status: 204 }),
  })
  await render()
  await click(button('Delete'))
  assert.match(text(), /Delete this file\?/)
  await click(button('Keep it'))
  assert.ok(!calls.some(call => call.key.startsWith('DELETE')))
  await click(button('Delete'))
  await click(container.querySelector('[data-activity-id="document-delete-confirm"]'))
  assert.ok(calls.some(call => call.key === `DELETE ${BASE}/${id}`))
})

test('archived applications show their documents without changing them', async () => {
  handlers = signedIn({ [`GET ${BASE}`]: () => json(checklist({ read_only: true, complete: true })) })
  await render()
  assert.match(text(), /archived/)
  assert.match(text(), /All required documents uploaded/)
  assert.equal(button('Upload'), undefined)
  assert.equal(button('Delete'), undefined)
})

test('files for documents no longer needed are listed apart', async () => {
  handlers = signedIn({ [`GET ${BASE}`]: () => json(checklist({ other: [file('f'.repeat(32), { filename: 'old letter.pdf' })] })) })
  await render()
  assert.match(container.querySelector('.docs-other').textContent, /No longer needed.*old letter\.pdf/)
})

test('file checks and sizes', () => {
  assert.equal(checkDocumentFile(new File(['x'], 'a.pdf', { type: 'application/pdf' })), null)
  assert.match(checkDocumentFile(new File([new Uint8Array(10 * 1024 * 1024 + 1)], 'a.pdf', { type: 'application/pdf' })), /10 MB/)
  assert.equal(formatSize(300), '1 KB')
  assert.equal(formatSize(3 * 1024 * 1024), '3.0 MB')
})
