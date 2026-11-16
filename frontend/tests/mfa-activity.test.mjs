import './dom-setup.mjs'
import assert from 'node:assert/strict'
import { afterEach, test } from 'node:test'
import { act, createElement } from 'react'
import { createRoot } from 'react-dom/client'

const auth = await import('../src/auth.ts')
const { default: MfaPrompt } = await import('../src/security/MfaPrompt.tsx')
const { default: MfaSettings } = await import('../src/security/MfaSettings.tsx')
const { default: ActivityHistory } = await import('../src/activity/ActivityHistory.tsx')
const traveler = { id: 7, name: 'Synthetic', email: 'qa@example.com', password_enabled: true }
const challenge = 'a'.repeat(43)
let root
afterEach(async () => { if (root) await act(async () => root.unmount()); root = null })
async function mount(component, props) {
  root = createRoot(document.getElementById('root'))
  await act(async () => root.render(createElement(component, props)))
}
function fill(id, value) {
  const field = document.getElementById(id)
  act(() => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(field, value)
    field.dispatchEvent(new window.Event('input', { bubbles: true }))
  })
}
async function submit(form) {
  await act(async () => form.dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true })))
}
function button(label) { return [...document.querySelectorAll('button')].find(row => row.textContent === label) }
function serve(handler) {
  const calls = []
  globalThis.fetch = async (url, options = {}) => {
    calls.push({ url, ...options })
    if (url === '/features') return Response.json({ two_factor_signin: true, activity_history: true })
    if (url === '/auth/refresh') return Response.json({ access_token: 'synthetic-access' })
    if (url === '/auth/me') return Response.json(traveler)
    return handler(url, options)
  }
  return calls
}

test('primary password and Google exchanges require MFA without fetching a signed-in profile', async () => {
  const calls = serve(() => Response.json({ mfa_required: true, challenge_token: challenge }, { status: 202 }))
  await assert.rejects(auth.signIn('qa@example.com', 'synthetic-password'), error =>
    error instanceof auth.MfaRequiredError && error.challengeToken === challenge)
  await assert.rejects(auth.signInWithGoogle('synthetic-google'), auth.MfaRequiredError)
  assert.deepEqual(calls.map(row => row.url), ['/auth/login', '/auth/google'])
})

test('second-factor UI rejects safely, clears the code, retries and completes the normal session', async () => {
  let count = 0, owner
  const calls = serve(url => {
    assert.equal(url, '/auth/mfa/verify')
    return ++count === 1 ? Response.json({ detail: 'unsafe provider diagnostic' }, { status: 401 })
      : Response.json({ access_token: 'completed-access' })
  })
  await mount(MfaPrompt, { challengeToken: challenge, onSignedIn: value => { owner = value }, onCancel() {} })
  fill('mfa-signin-code', '000000')
  await submit(document.querySelector('form'))
  assert.match(document.body.textContent, /Use a new authenticator code/)
  assert.doesNotMatch(document.body.textContent, /unsafe provider diagnostic/)
  assert.equal(document.getElementById('mfa-signin-code').value, '')
  assert.equal(owner, undefined)
  fill('mfa-signin-code', 'ABCDEF-123456-ABCDEF-123456')
  await submit(document.querySelector('form'))
  assert.equal(owner.id, 7)
  assert.deepEqual(JSON.parse(calls[0].body), { challenge_token: challenge, code: '000000' })
  assert.equal(calls.at(-1).headers.Authorization, 'Bearer completed-access')
})

test('cancel burns the pending challenge and never signs the traveler in', async () => {
  let cancelled = false
  const calls = serve(() => new Response(null, { status: 204 }))
  await mount(MfaPrompt, { challengeToken: challenge, onSignedIn() { assert.fail('unexpected sign-in') },
    onCancel() { cancelled = true } })
  await act(async () => button('Cancel sign-in').click())
  assert.ok(cancelled)
  assert.equal(calls[0].url, '/auth/mfa/cancel')
  assert.doesNotMatch(window.location.href, new RegExp(challenge))
})

test('authenticator enrollment reauthenticates, confirms a code and shows backups once', async () => {
  const calls = serve(url => {
    if (url === '/auth/mfa') return Response.json({ enabled: false, backup_codes_remaining: 0 })
    if (url === '/auth/mfa/setup') return Response.json({ secret: 'SYNTHETIC-KEY', qr_data_url: 'data:image/png;base64,AA==', expires_in: 600 })
    if (url === '/auth/mfa/enable') return Response.json({ enabled: true, backup_codes: ['SYNTHETIC-BACKUP'] })
    assert.fail(url)
  })
  await auth.restoreSession()
  await mount(MfaSettings, { traveler, googleClientId: '' })
  assert.match(document.body.textContent, /Off by default/)
  await act(async () => button('Set up authenticator').click())
  fill('mfa-settings-password', 'synthetic-password')
  await submit(document.querySelector('form'))
  assert.match(document.body.textContent, /SYNTHETIC-KEY/)
  fill('mfa-enable-code', '123456')
  await submit(document.querySelector('form'))
  assert.doesNotMatch(document.body.textContent, /SYNTHETIC-KEY/)
  assert.match(document.body.textContent, /SYNTHETIC-BACKUP/)
  await act(async () => button('I saved my backup codes').click())
  assert.doesNotMatch(document.body.textContent, /SYNTHETIC-BACKUP/)
  assert.deepEqual(JSON.parse(calls.find(row => row.url === '/auth/mfa/setup').body),
    { expected_traveler_id: 7, password: 'synthetic-password' })
})

test('admin history sends Beirut wall times to the API and paginates without browser-zone conversion', async () => {
  const calls = serve(url => {
    const page = new URL(url, 'http://localhost').searchParams.get('page')
    return Response.json({ items: [{ id: Number(page), actor: 'You', event_type: 'document_access',
      identifier: 'passport', purpose: 'image_view', outcome: 'success', resource_id: 'a'.repeat(32),
      resource_type: 'passport', occurred_at: '2026-11-03T21:59:59Z' }], total: 26, page: Number(page), page_size: 25 })
  })
  await auth.restoreSession()
  await mount(ActivityHistory, { userId: 2 })
  assert.match(document.querySelector('.access-history').textContent, /03\/11\/2026, 23:59:59 Beirut/)
  const fields = document.querySelectorAll('input[type="datetime-local"]')
  fields[0].value = '2026-11-03T00:00:00'; fields[1].value = '2026-11-03T23:59:59'
  await submit(document.querySelector('form'))
  const query = new URL(calls.at(-1).url, 'http://localhost').searchParams
  assert.equal(query.get('from_at'), '2026-11-03T00:00')
  assert.equal(query.get('to_at'), '2026-11-03T23:59:59')
  await act(async () => button('Next').click())
  assert.equal(new URL(calls.at(-1).url, 'http://localhost').searchParams.get('page'), '2')
})
