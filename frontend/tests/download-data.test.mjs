import { dom } from './dom-setup.mjs'
import assert from 'node:assert/strict'
import { afterEach, beforeEach, test } from 'node:test'
import { act, createElement } from 'react'
import { createRoot } from 'react-dom/client'

for (const name of ['Node', 'Element', 'HTMLAnchorElement', 'MutationObserver', 'getComputedStyle']) {
  globalThis[name] ??= dom.window[name]
}

const { default: DownloadData } = await import('../src/DownloadData.tsx')

let root, container, requests, clicked, created, revoked

beforeEach(() => {
  requests = []; clicked = []; created = 0; revoked = []
  URL.createObjectURL = () => { created += 1; return 'blob:taasheera-test' }
  URL.revokeObjectURL = url => revoked.push(url)
  dom.window.HTMLAnchorElement.prototype.click = function () { clicked.push({ href: this.href, download: this.download }) }
})

afterEach(async () => {
  await act(async () => root.unmount())
  container.remove()
})

function serve(t, exportResponse) {
  t.mock.method(globalThis, 'fetch', async (url, options = {}) => {
    requests.push({ url, authorization: new Headers(options.headers).get('Authorization') })
    if (url === '/auth/refresh') return Response.json({ access_token: 'test-access-token' })
    if (url === '/auth/me') return Response.json({ id: 1, name: 'Anna', email: 'anna@example.com' })
    if (url === '/auth/account/export') return exportResponse()
    return new Response(null, { status: 204 })
  })
}

async function render() {
  container = document.createElement('div')
  document.body.append(container)
  root = createRoot(container)
  await act(async () => root.render(createElement(DownloadData)))
}
const button = () => container.querySelector('button')
const press = () => act(async () => { button().click(); await new Promise(resolve => setTimeout(resolve, 20)) })

test('the button downloads the ZIP under the name the server gives it', async t => {
  serve(t, () => new Response(new Blob(['PK zip bytes']), { status: 200, headers: {
    'Content-Type': 'application/zip', 'Content-Disposition': 'attachment; filename="taasheera-data-2026-11-14.zip"' } }))
  await render()
  assert.match(container.querySelector('h2').textContent, /Download your data/)
  assert.match(container.textContent, /password is never included/)
  await press()
  const call = requests.find(request => request.url === '/auth/account/export')
  assert.equal(call.authorization, 'Bearer test-access-token')
  assert.deepEqual(clicked, [{ href: 'blob:taasheera-test', download: 'taasheera-data-2026-11-14.zip' }])
  assert.equal(created, 1)
  assert.match(container.querySelector('[role="status"]').textContent, /taasheera-data-2026-11-14\.zip/)
  assert.equal(button().disabled, false)
  assert.equal(document.querySelector('a[download]'), null, 'the temporary link is removed')
})

test('too many downloads get their own message, and nothing is saved', async t => {
  serve(t, () => new Response(null, { status: 429 }))
  await render()
  await press()
  assert.match(container.querySelector('[role="alert"]').textContent, /downloaded your data several times recently/)
  assert.deepEqual(clicked, [])
})

test('a server error shows a message and the button works again', async t => {
  serve(t, () => new Response(null, { status: 503 }))
  await render()
  await press()
  assert.match(container.querySelector('[role="alert"]').textContent, /Something went wrong on our side/)
  assert.deepEqual(clicked, [])
  assert.equal(button().disabled, false)
})

test('a second click while preparing does not ask twice', async t => {
  let finish
  serve(t, () => new Promise(resolve => { finish = () => resolve(new Response(new Blob(['PK']), { status: 200 })) }))
  await render()
  await act(async () => { button().click(); button().click(); await new Promise(resolve => setTimeout(resolve, 20)) })
  assert.equal(button().textContent, 'Preparing your download…')
  assert.equal(button().disabled, true)
  await act(async () => { finish(); await new Promise(resolve => setTimeout(resolve, 20)) })
  assert.equal(requests.filter(request => request.url === '/auth/account/export').length, 1)
  assert.deepEqual(clicked.map(item => item.download), ['taasheera-data.zip'])
})
