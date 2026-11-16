import { dom } from './dom-setup.mjs'
import assert from 'node:assert/strict'
import { registerHooks } from 'node:module'
import { afterEach, test } from 'node:test'
import { act, createElement } from 'react'
import { createRoot } from 'react-dom/client'
import { createMemoryRouter, RouterProvider } from 'react-router'

// Vite turns image imports into URLs; the shared test loader only stubs CSS.
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
const { currentSection } = await import('../src/sections.ts')

let root
let container
let router
let requests

function signedIn(t, extra = () => null) {
  requests = []
  t.mock.method(globalThis, 'fetch', async (url, options = {}) => {
    requests.push(`${options.method ?? 'GET'} ${url}`)
    const answer = extra(url, options)
    if (answer) return answer
    if (url === '/auth/refresh') return Response.json({ access_token: 'test-access-token' })
    if (url === '/auth/me') return Response.json({ id: 1, name: 'Anna', email: 'anna@example.com', password_enabled: true })
    if (url === '/auth/config') return Response.json({ google_client_id: '' })
    if (String(url).startsWith('/assistant/history')) return Response.json([])
    if (String(url).startsWith('/applications') || url === '/sessions') return Response.json([])
    if (url === '/profile') return Response.json({})
    return new Response(null, { status: 204 })
  })
}

async function render(path) {
  dom.reconfigure({ url: `http://127.0.0.1:5173${path}` })
  container = document.createElement('div')
  document.body.append(container)
  router = createMemoryRouter(routes, { initialEntries: [path] })
  root = createRoot(container)
  await act(async () => root.render(createElement(RouterProvider, { router })))
  await settle()
}
const settle = () => act(() => new Promise(resolve => setTimeout(resolve, 30)))
const click = el => act(async () => el.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true, cancelable: true, button: 0 })))
const panel = () => container.querySelector('nav[aria-label="Your sections"]')
const link = text => [...panel().querySelectorAll('a')].find(a => a.textContent === text)
const button = text => [...container.querySelectorAll('button')].find(b => b.textContent.trim() === text)

afterEach(async () => {
  if (!root) return
  await act(async () => root.unmount())
  container.remove()
  root = undefined
  dom.reconfigure({ url: 'http://127.0.0.1:5173/' })
})

test('the current section follows the path, including pages inside a section', () => {
  assert.equal(currentSection('/dashboard').id, 'applications')
  assert.equal(currentSection('/dashboard/abc').id, 'applications')
  assert.equal(currentSection('/apply/abc').id, 'applications')
  assert.equal(currentSection('/passport/').id, 'documents')
  assert.equal(currentSection('/account').id, 'account')
  assert.equal(currentSection('/applyx'), null)
  assert.equal(currentSection('/faq'), null)
})

for (const [path, current] of [['/dashboard', 'Applications'], ['/apply', 'Applications'],
  ['/passport', 'Passport & documents'], ['/faq', null]]) {
  test(`signed in on ${path}, one panel links every section and marks ${current ?? 'none'} as current`, async t => {
    signedIn(t)
    await render(path)
    assert.equal(container.querySelectorAll('nav[aria-label="Your sections"]').length, 1)
    assert.deepEqual([...panel().querySelectorAll('a')].map(a => [a.textContent, a.getAttribute('href')]), [
      ['Applications', '/dashboard'], ['Passport & documents', '/passport'], ['Account', '/account']])
    // Starting an application has no assistant yet (tests/application.test.mjs), so no button that does nothing.
    if (path === '/apply') assert.ok(!button('Assistant'), 'no Assistant button when starting an application')
    else assert.ok(button('Assistant'), 'the panel has an Assistant button')
    const marked = [...panel().querySelectorAll('[aria-current="page"]')].map(a => a.textContent)
    assert.deepEqual(marked, current ? [current] : [])
    assert.equal(panel().querySelector('.section-nav-toggle').textContent, current ?? 'Your sections')
  })
}

test('signed-out visitors get no panel', async t => {
  t.mock.method(globalThis, 'fetch', async () => new Response(null, { status: 401 }))
  await render('/faq')
  assert.equal(panel(), null)
})

test('the welcome and account screens have the same panel, with Account current there', async t => {
  signedIn(t)
  await render('/account')
  assert.equal(container.querySelectorAll('nav[aria-label="Your sections"]').length, 1)
  assert.equal(link('Account').getAttribute('aria-current'), 'page')
  assert.equal(link('Applications').getAttribute('href'), '/dashboard')
})

test('the Assistant button opens the general assistant, and the passport page its own', async t => {
  signedIn(t)
  await render('/faq')
  assert.equal(container.querySelector('.assistant-panel'), null)
  await click(button('Assistant'))
  await settle()
  assert.match(container.querySelector('.assistant-panel h2').textContent, /Ta'asheera assistant/)
  await act(async () => root.unmount())
  container.remove()

  await render('/passport')
  await click(button('Assistant'))
  await settle()
  assert.match(container.querySelector('.assistant-panel h2').textContent, /Passport assistant/)
})

test('switching sections keeps the assistant open with its conversation', async t => {
  signedIn(t, url => url === '/assistant/messages'
    ? Response.json({ reply: 'Yes, only you can see it.', used_passport: false }) : null)
  await render('/faq')
  await click(button('Assistant'))
  await settle()
  const input = container.querySelector('.assistant-panel textarea')
  await act(async () => {
    Object.getOwnPropertyDescriptor(dom.window.HTMLTextAreaElement.prototype, 'value').set.call(input, 'Is my data private?')
    input.dispatchEvent(new dom.window.Event('input', { bubbles: true }))
  })
  await click(button('Send'))
  await settle()
  assert.match(container.querySelector('.assistant-panel').textContent, /Is my data private\?.*Yes, only you can see it\./s)

  await click(link('Applications'))
  await settle()
  assert.equal(router.state.location.pathname, '/dashboard')
  assert.equal(link('Applications').getAttribute('aria-current'), 'page')
  assert.match(container.querySelector('.assistant-panel').textContent, /Is my data private\?.*Yes, only you can see it\./s)
  // The chat was kept in the page, not reloaded from the server.
  assert.equal(requests.filter(request => request.startsWith('GET /assistant/history')).length, 1)
})

test('on a phone the panel is one button that opens the list and closes on a link or Escape', async t => {
  signedIn(t)
  await render('/dashboard')
  const toggle = panel().querySelector('.section-nav-toggle')
  assert.equal(toggle.getAttribute('aria-expanded'), 'false')
  assert.equal(toggle.getAttribute('aria-controls'), 'section-nav-list')
  await click(toggle)
  assert.equal(toggle.getAttribute('aria-expanded'), 'true')
  assert.ok(panel().classList.contains('is-open'))
  await click(link('Passport & documents'))
  await settle()
  assert.equal(router.state.location.pathname, '/passport')
  assert.equal(panel().querySelector('.section-nav-toggle').getAttribute('aria-expanded'), 'false')

  await click(panel().querySelector('.section-nav-toggle'))
  await act(async () => panel().dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true })))
  assert.equal(panel().querySelector('.section-nav-toggle').getAttribute('aria-expanded'), 'false')
})
