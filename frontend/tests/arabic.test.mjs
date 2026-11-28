import assert from 'node:assert/strict'
import { registerHooks } from 'node:module'
import { afterEach, beforeEach, test } from 'node:test'
import { dom } from './dom-setup.mjs'
import { act, createElement } from 'react'
import { createRoot } from 'react-dom/client'
import { createMemoryRouter, RouterProvider } from 'react-router'

// Switch the home page, FAQ and site header between English and Arabic (CMPS271-16).
registerHooks({
  load(url, context, nextLoad) {
    if (url.endsWith('.svg')) return { format: 'module', source: `export default ${JSON.stringify(url)}`, shortCircuit: true }
    return nextLoad(url, context)
  },
})

const { routes } = await import('../src/routes.tsx')
const { resetFeatureFlags } = await import('../src/features.ts')
const { destinations } = await import('../src/content.ts')
const { en } = await import('../src/i18n/en.ts')
const { ar } = await import('../src/i18n/ar.ts')
const { savedChoice } = await import('../src/i18n/language.ts')

for (const name of ['Node', 'Element', 'HTMLAnchorElement', 'MutationObserver', 'getComputedStyle']) {
  globalThis[name] ??= dom.window[name]
}
window.matchMedia ??= () => ({ matches: false, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} })
window.scrollTo = () => {}

const html = document.documentElement
let root
let container
let saved   // stands in for browser storage, locked in tests (dom-setup.mjs)
function remember(t) {
  t.mock.method(savedChoice, 'read', () => saved ?? 'en')
  t.mock.method(savedChoice, 'write', language => { saved = language })
}

function serve(t, flags = { arabic: true, visa_guide: true }) {
  remember(t)
  t.mock.method(globalThis, 'fetch', async (url) => {
    if (url === '/features') return Response.json(flags)
    if (url === '/visa-guides') return Response.json([])
    if (url === '/auth/refresh') return new Response(null, { status: 401 })
    return new Response(null, { status: 204 })
  })
}

async function render(path) {
  container = document.createElement('div')
  document.body.append(container)
  const router = createMemoryRouter(routes, { initialEntries: [path] })
  root = createRoot(container)
  await act(async () => root.render(createElement(RouterProvider, { router })))
  await act(() => new Promise(resolve => setTimeout(resolve, 20)))
  return router
}
const click = el => act(async () => el.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true, cancelable: true, button: 0 })))
const toggle = () => container.querySelector('.language-toggle')
const heading = () => container.querySelector('h1').textContent

beforeEach(() => {
  saved = undefined
  resetFeatureFlags()
})

afterEach(async () => {
  await act(async () => root?.unmount())
  container?.remove()
  root = undefined
})

// Every string, in order, with where it is: functions are called with a sample value.
function strings(value, path = 'text') {
  if (typeof value === 'string') return [[path, value]]
  if (typeof value === 'function') return [[`${path}()`, value('X')]]
  return Object.entries(value).flatMap(([key, item]) => strings(item, `${path}.${key}`))
}

test('the Arabic text has every English entry, translated', () => {
  const english = strings(en)
  const arabic = strings(ar)
  assert.deepEqual(arabic.map(([path]) => path), english.map(([path]) => path), 'same keys, lists and lengths')
  const arabicLetter = /[؀-ۿ]/
  for (const [path, value] of arabic) {
    assert.match(value, arabicLetter, `${path} is not Arabic`)
  }
  assert.deepEqual(Object.keys(ar.destinations).sort(), destinations.map(item => item.id).sort())
})

test('the home page reads in English by default, with a button to switch to Arabic', async (t) => {
  serve(t)
  await render('/')
  assert.equal(html.lang, 'en')
  assert.equal(html.dir, 'ltr')
  assert.match(heading(), /Visa paperwork/)
  assert.equal(toggle().textContent, 'العربية')
  assert.equal(toggle().lang, 'ar', 'screen readers read the button name in Arabic')
})

test('switching to Arabic turns the page right to left and is remembered', async (t) => {
  serve(t)
  await render('/')
  await click(toggle())
  assert.equal(html.lang, 'ar')
  assert.equal(html.dir, 'rtl')
  assert.equal(saved, 'ar')
  assert.match(heading(), /أوراق التأشيرة/)
  assert.equal(document.title, ar.home.title)
  assert.equal(container.querySelector('.login-link').textContent, ar.site.logIn)
  assert.equal(container.querySelector('#destinations h3').textContent, ar.destinations.schengen.name)
  // The main button shows its words through CSS; its name for screen readers stays the call to action.
  assert.equal(container.querySelector('.landing-start .pill-label').dataset.label, ar.home.startYours)
  assert.equal(container.querySelector('.landing-start .visually-hidden').textContent, ar.home.getStarted)
  assert.equal(container.querySelectorAll('.faq-item').length, 5)
  assert.match(container.querySelector('.footer-legal').textContent, /السفارة أو القنصلية وحدها/)
  // Passport specimen data stays in English, left to right.
  assert.equal(container.querySelector('.preview-mrz').lang, 'en')
  assert.equal(toggle().textContent, 'English')

  await click(toggle())
  assert.equal(html.dir, 'ltr')
  assert.equal(saved, 'en')
  assert.match(heading(), /Visa paperwork/)
})

test('the saved choice opens the FAQ and the not-found page in Arabic', async (t) => {
  serve(t)
  saved = 'ar'
  await render('/faq')
  assert.equal(heading(), ar.faqPage.heading)
  assert.equal(container.querySelectorAll('.faq-item').length, ar.faqs.length)
  assert.equal(html.dir, 'rtl')
  await act(async () => root.unmount())
  container.remove()

  await render('/no-such-page')
  assert.equal(heading(), ar.notFound.heading)
  assert.equal(document.title, ar.notFound.title)
})

test('pages that are not translated yet stay English and left to right', async (t) => {
  serve(t)
  saved = 'ar'
  const router = await render('/')
  assert.equal(html.dir, 'rtl')
  await act(() => router.navigate('/visas'))
  await act(() => new Promise(resolve => setTimeout(resolve, 20)))
  assert.equal(html.lang, 'en')
  assert.equal(html.dir, 'ltr')
  assert.equal(heading(), 'What each visa needs')
  assert.equal(container.querySelector('.login-link').textContent, 'Log in', 'no Arabic header on an English page')
  assert.equal(toggle(), null)
  assert.equal(saved, 'ar', 'the choice waits for the next translated page')
})

test('with the flag off the site is English and has no switch, whatever was saved', async (t) => {
  serve(t, { visa_guide: true })
  saved = 'ar'
  await render('/')
  assert.equal(html.dir, 'ltr')
  assert.equal(html.lang, 'en')
  assert.match(heading(), /Visa paperwork/)
  assert.equal(toggle(), null)
})

test('the switch still works when the browser blocks storage', async (t) => {
  serve(t)
  t.mock.restoreAll()   // the real read and write, against the locked test storage
  t.mock.method(globalThis, 'fetch', async url => url === '/features' ? Response.json({ arabic: true }) : new Response(null, { status: 401 }))
  await render('/')
  assert.equal(html.dir, 'ltr')
  await click(toggle())
  assert.equal(html.dir, 'rtl')
  assert.match(heading(), /أوراق التأشيرة/)
})
