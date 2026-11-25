import assert from 'node:assert/strict'
import { afterEach, beforeEach, test } from 'node:test'
import { dom } from './dom-setup.mjs'
import { act, createElement } from 'react'
import { createRoot } from 'react-dom/client'

for (const name of ['Node', 'Element', 'HTMLAnchorElement', 'MutationObserver', 'getComputedStyle']) {
  globalThis[name] ??= dom.window[name]
}

const { default: PortalGuide } = await import('../src/submission/PortalGuide.tsx')

const GUIDE = {
  title: 'GOV.UK: Apply for a Standard Visitor visa',
  portal: { label: 'Start your application on GOV.UK', url: 'https://www.gov.uk/standard-visitor/apply-standard-visitor-visa' },
  before: ['Apply online from outside the UK.'],
  sections: [{ title: 'Your name and contact details', intro: null, yourself: ['Whether you have used other names'], fields: [
    { label: 'Family name', value: 'ERIKSSON', kind: 'text', source: 'passport', note: null },
    { label: 'Nationality', value: 'LBN', kind: 'country', source: 'passport', note: null },
    { label: 'Home address', value: '1 Example Street, Beirut, LBN', kind: 'address', source: 'home', note: null },
    { label: 'Telephone number', value: null, kind: 'text', source: null, note: null },
  ] }],
  after: ['Pay the £135 fee.'],
  sources: [{ label: 'GOV.UK: Standard Visitor visa', url: 'https://www.gov.uk/standard-visitor' }],
  filled: 3, total: 4,
}

let container, root
beforeEach(() => { container = document.createElement('div'); document.body.append(container); root = createRoot(container) })
afterEach(() => { act(() => root.unmount()); container.remove() })
const settle = () => act(() => new Promise(resolve => setTimeout(resolve, 20)))

test('the guide lays out the portal page by page with values to copy', async (t) => {
  const copied = []
  Object.defineProperty(navigator, 'clipboard', { value: { writeText: async text => { copied.push(text) } }, configurable: true })
  t.mock.method(globalThis, 'fetch', async url => {
    if (url === '/auth/refresh') return Response.json({ access_token: 'test-access-token' })
    if (url === '/auth/me') return Response.json({ id: 1, name: 'Anna', email: 'anna@example.com' })
    if (url === `/applications/${'c'.repeat(32)}/portal-guide`) return Response.json(GUIDE)
    return new Response(null, { status: 404 })
  })
  await act(async () => root.render(createElement(PortalGuide, { applicationId: 'c'.repeat(32) })))
  await settle()
  const text = container.textContent
  assert.match(text, /Fill in GOV\.UK/)
  assert.match(text, /3 of 4 fields are ready/)
  assert.match(text, /Lebanon/, 'country codes show as names')
  assert.match(text, /1 Example Street, Beirut, Lebanon/)
  assert.match(text, /Not in your answers/)
  assert.match(text, /Answer these yourself.*used other names/s)
  assert.equal(container.querySelector('a.button').getAttribute('href'), GUIDE.portal.url)
  const copy = container.querySelector('button[aria-label="Copy Family name"]')
  await act(async () => copy.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true })))
  assert.deepEqual(copied, ['ERIKSSON'])
  assert.equal(copy.textContent, 'Copied')
})

test('a visa without a portal guide shows nothing', async (t) => {
  t.mock.method(globalThis, 'fetch', async url => url === '/auth/refresh' ? Response.json({ access_token: 'test-access-token' })
    : url === '/auth/me' ? Response.json({ id: 1, name: 'Anna', email: 'anna@example.com' }) : new Response(null, { status: 404 }))
  await act(async () => root.render(createElement(PortalGuide, { applicationId: 'd'.repeat(32) })))
  await settle()
  assert.equal(container.textContent, '')
})
