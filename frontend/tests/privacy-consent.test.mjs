import { dom } from './dom-setup.mjs'
import assert from 'node:assert/strict'
import { afterEach, beforeEach, test } from 'node:test'
import { act, createElement } from 'react'
import { createRoot } from 'react-dom/client'

for (const name of ['Node', 'Element', 'HTMLAnchorElement', 'MutationObserver', 'getComputedStyle', 'KeyboardEvent', 'Event']) {
  globalThis[name] ??= dom.window[name]
}

const { default: PrivacyConsent } = await import('../src/PrivacyConsent.tsx')
const { CONSENT_REQUIRED_EVENT, reportConsentRequired } = await import('../src/consent.ts')

// jsdom has the dialog element but does not implement the top-layer methods.
dom.window.HTMLDialogElement.prototype.showModal ??= function () { this.setAttribute('open', '') }
dom.window.HTMLDialogElement.prototype.close ??= function () { this.removeAttribute('open') }

const VERSION = '2026-11-25'
let root, container, requests, status

beforeEach(() => {
  requests = []
  status = { policy_version: VERSION, accepted: false, accepted_at: null }
})

afterEach(async () => {
  await act(async () => root?.unmount())
  container?.remove()
  root = null
})

function serve(t, { accept = () => Response.json({ ...status, accepted: true, accepted_at: '2026-11-25T10:00:00Z' }), read } = {}) {
  t.mock.method(globalThis, 'fetch', async (url, options = {}) => {
    requests.push({ url, method: options.method ?? 'GET', body: options.body,
                    authorization: new Headers(options.headers).get('Authorization') })
    if (url === '/auth/refresh') return Response.json({ access_token: 'test-access-token' })
    if (url === '/auth/me') return Response.json({ id: 1, name: 'Anna', email: 'anna@example.com' })
    if (url === '/profile/privacy-consent' && (options.method ?? 'GET') === 'GET') return read ? read() : Response.json(status)
    if (url === '/profile/privacy-consent') return accept()
    return new Response(null, { status: 204 })
  })
}

const settle = () => act(async () => { await new Promise(resolve => setTimeout(resolve, 20)) })

async function render() {
  container = document.createElement('div')
  document.body.append(container)
  root = createRoot(container)
  await act(async () => root.render(createElement(PrivacyConsent)))
  await settle()
}

const dialog = () => container.querySelector('dialog[open]')
const button = label => [...container.querySelectorAll('button')].find(item => item.textContent.trim() === label)
const press = async label => { await act(async () => button(label).click()); await settle() }

test('a traveler who has not accepted sees the policy summary, with focus on its title', async t => {
  serve(t)
  await render()
  assert.ok(dialog())
  assert.equal(container.querySelector('h2').textContent, 'Before you upload documents')
  assert.equal(document.activeElement, container.querySelector('h2'))
  assert.ok(container.querySelectorAll('.consent-summary li').length >= 5)
  const link = container.querySelector('a[href="/privacy.html"]')
  assert.equal(link.getAttribute('target'), '_blank')
  assert.match(link.getAttribute('rel'), /noopener/)
  assert.match(container.textContent, /never your real passport/)
  const read = requests.find(request => request.url === '/profile/privacy-consent')
  assert.equal(read.authorization, 'Bearer test-access-token')
})

test('a traveler who already accepted the current version is not asked', async t => {
  status = { policy_version: VERSION, accepted: true, accepted_at: '2026-11-20T09:00:00Z' }
  serve(t)
  await render()
  assert.equal(dialog(), null)
})

test('accepting sends the version that was shown and closes the prompt', async t => {
  serve(t)
  await render()
  await press('I accept')
  const post = requests.find(request => request.method === 'POST')
  assert.deepEqual(JSON.parse(post.body), { policy_version: VERSION })
  assert.equal(post.authorization, 'Bearer test-access-token')
  assert.equal(dialog(), null)
})

test('a policy that changed meanwhile asks for a reload and stays open', async t => {
  serve(t, { accept: () => Response.json({ detail: 'changed' }, { status: 409 }) })
  await render()
  await press('I accept')
  assert.match(container.querySelector('[role="alert"]').textContent, /Reload the page/)
  assert.ok(dialog())
  assert.equal(button('I accept').disabled, false)
})

test('Not now and Escape close it; a refused upload opens it again', async t => {
  serve(t)
  await render()
  await press('Not now')
  assert.equal(dialog(), null)

  await act(async () => window.dispatchEvent(new window.Event(CONSENT_REQUIRED_EVENT)))
  await settle()
  assert.ok(dialog(), 'shown again after an upload was refused')

  await act(async () => dialog().dispatchEvent(new window.Event('cancel', { cancelable: true })))
  assert.equal(dialog(), null)
})

test('when the status cannot be read, nothing is shown', async t => {
  serve(t, { read: () => new Response(null, { status: 404 }) })
  await render()
  assert.equal(dialog(), null)
})

test('only a refusal marked as missing consent opens the prompt', () => {
  let opened = 0
  const count = () => { opened += 1 }
  window.addEventListener(CONSENT_REQUIRED_EVENT, count)
  try {
    const marked = new Response(null, { status: 403, headers: { 'X-Privacy-Consent': 'required' } })
    assert.equal(reportConsentRequired(marked), true)
    assert.equal(reportConsentRequired(new Response(null, { status: 403 })), false)
    assert.equal(reportConsentRequired(new Response(null, { status: 422, headers: { 'X-Privacy-Consent': 'required' } })), false)
    assert.equal(opened, 1)
  } finally {
    window.removeEventListener(CONSENT_REQUIRED_EVENT, count)
  }
})
