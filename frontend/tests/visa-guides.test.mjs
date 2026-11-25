import assert from 'node:assert/strict'
import { registerHooks } from 'node:module'
import { afterEach, beforeEach, test } from 'node:test'
import { dom } from './dom-setup.mjs'
import { act, createElement } from 'react'
import { createRoot } from 'react-dom/client'
import { createMemoryRouter, RouterProvider } from 'react-router'

for (const name of ['Node', 'Element', 'HTMLAnchorElement', 'HTMLSelectElement', 'MutationObserver',
  'getComputedStyle', 'Event', 'KeyboardEvent']) {
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

const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
const link = (label, url) => ({ label, url })
const guide = (extra = {}) => ({
  id: 'uk-standard-visitor', title: 'UK Standard Visitor visa', destination_label: 'United Kingdom',
  destination_description: 'England, Scotland, Wales and Northern Ireland', visa_label: 'Standard Visitor',
  visa_description: 'Up to 6 months for tourism, family visits or business.',
  official_page: link('UK Standard Visitor', 'https://www.gov.uk/standard-visitor'),
  need: { rule: "Citizens of countries on the UK's visa list need this visa.", notes: ['A note about ETAs.'],
          checker: link('GOV.UK: Check if you need a UK visa', 'https://www.gov.uk/check-uk-visa'),
          checked_nationalities: { LBN: 'visa' } },
  not_for: ["Visitors can't work in the UK."],
  documents: [
    { label: 'Bank statements', help: 'Your last 6 months of statements.', required: true, only_if: null },
    { label: 'Invitation from your host', help: null, required: true,
      only_if: 'Only if your answer to "Will you stay with family or friends?" is "Yes".' },
    { label: 'Travel insurance', help: null, required: false, only_if: null },
  ],
  fees: { items: [{ label: 'Standard Visitor visa (up to 6 months)', amount: '£135' }],
          note: 'Fees are not refunded if the visa is refused.',
          source: link('GOV.UK: Apply for a Standard Visitor visa', 'https://www.gov.uk/standard-visitor/apply-standard-visitor-visa') },
  time: { text: 'Usually about 3 weeks after your appointment.', source: link('GOV.UK: Visa processing times', 'https://www.gov.uk/visa-processing-times') },
  coverage: { level: 'guided', text: 'Ta\'asheera asks you every question in plain language.' },
  sources: [link('UK Standard Visitor', 'https://www.gov.uk/standard-visitor'),
            link('GOV.UK: Check if you need a UK visa', 'https://www.gov.uk/check-uk-visa')],
  checked_on: '2026-11-25', ...extra,
})

let handlers
beforeEach(() => {
  resetFeatureFlags()
  handlers = {
    '/features': () => json({ visa_guide: true }),
    '/auth/refresh': () => json({ detail: 'Not signed in.' }, 401),
    '/activity/events': () => new Response(null, { status: 204 }),
    '/visa-guides': () => json([guide(), guide({ id: 'schengen-short-stay', title: 'Schengen short-stay visa (type C)',
      fees: { ...guide().fees, items: [{ label: 'Adults', amount: '€90' }] }, coverage: { level: 'filled_for_you', text: 'x' } })]),
    '/visa-guides/uk-standard-visitor': () => json(guide()),
    '/visa-guides/mars-tourist': () => json({ detail: "We don't have a guide for that visa." }, 404),
  }
  globalThis.fetch = async url => {
    const handler = handlers[String(url)]
    return handler ? handler() : new Response(null, { status: 204 })
  }
})

let root
let container
afterEach(async () => {
  await act(async () => root?.unmount())
  container?.remove()
  root = null
})

const settle = () => act(async () => { await new Promise(resolve => setTimeout(resolve, 20)) })
async function render(path, ready) {
  container = document.createElement('div')
  document.body.append(container)
  root = createRoot(container)
  const router = createMemoryRouter(routes, { initialEntries: [path] })
  await act(async () => root.render(createElement(RouterProvider, { router })))
  for (let tries = 0; tries < 60 && !ready(); tries += 1) await settle()
}
const main = () => container.querySelector('main') ?? container
const heading = () => main().querySelector('h1')?.textContent

test('the list shows every guide with its starting fee and what Ta\'asheera does', async () => {
  await render('/visas', () => container.querySelector('.guide-card'))
  assert.equal(heading(), 'What each visa needs')
  const cards = [...container.querySelectorAll('.guide-card')]
  assert.equal(cards.length, 2)
  assert.match(cards[0].textContent, /From £135/)
  assert.match(cards[0].textContent, /Guided, you submit/)
  assert.match(cards[1].textContent, /Form filled for you/)
  assert.equal(cards[0].querySelector('a').getAttribute('href'), '/visas/uk-standard-visitor')
  assert.match(container.querySelector('.guide-checked').textContent, /25 November 2026/)
})

test('one guide shows the checklist with its conditions, fees, time and sources', async () => {
  await render('/visas/uk-standard-visitor', () => container.querySelector('#documents-heading'))
  assert.equal(heading(), 'UK Standard Visitor visa')
  const documents = [...container.querySelectorAll('.guide-documents li')].map(item => item.textContent)
  assert.match(documents[1], /Invitation from your host.*Only if your answer to "Will you stay with family or friends\?" is "Yes"\./)
  assert.match(documents[2], /\(optional\)/)
  assert.match(container.querySelector('.guide-fees').textContent, /£135/)
  assert.match(container.textContent, /Usually about 3 weeks/)
  assert.match(container.textContent, /Visitors can't work in the UK\./)
  const external = [...container.querySelectorAll('.guide-section a[target="_blank"]')]
  assert.ok(external.length >= 4)
  for (const anchor of external) {
    assert.match(anchor.getAttribute('href'), /^https:\/\//)
    assert.match(anchor.getAttribute('rel'), /noopener/)
  }
  assert.match(container.textContent, /Checked on 25 November 2026/)
  // Signed out, the button creates an account.
  assert.equal(container.querySelector('.guide-start .pill-label').textContent, 'Create a free account')
})

test('a checked nationality gets a definite answer, any other is sent to the official checker', async () => {
  await render('/visas/uk-standard-visitor', () => container.querySelector('select'))
  const select = container.querySelector('#need-heading').parentElement.querySelector('select')
  const choose = async code => {
    await act(async () => {
      select.value = code
      select.dispatchEvent(new window.Event('change', { bubbles: true }))
    })
  }
  await choose('LBN')
  let answer = container.querySelector('.guide-answer').textContent
  assert.match(answer, /Yes\. Citizens of Lebanon need a visa/)
  assert.match(answer, /Confirm it with the official checker/)

  await choose('FRA')
  answer = container.querySelector('.guide-answer').textContent
  assert.match(answer, /haven't checked the rules for citizens of France/)
  assert.doesNotMatch(answer, /Yes\.|No visa needed/)
  assert.equal(container.querySelector('.guide-answer a').getAttribute('href'), 'https://www.gov.uk/check-uk-visa')
})

test('an unknown guide looks like any unknown page', async () => {
  await render('/visas/mars-tourist', () => heading() === 'Page not found')
  assert.equal(heading(), 'Page not found')
  assert.equal(container.querySelector('#need-heading'), null)
})

test('with the flag off the guides do not exist', async () => {
  handlers['/features'] = () => json({ visa_guide: false })
  await render('/visas', () => heading())
  assert.equal(heading(), 'Page not found')
  assert.equal(container.querySelector('a[href="/visas"]'), null)
})

test('a server problem is explained, not a blank page', async () => {
  handlers['/visa-guides'] = () => json({ detail: 'Down' }, 503)
  await render('/visas', () => container.querySelector('[role="alert"]'))
  assert.match(container.querySelector('[role="alert"]').textContent, /could not load the visa guide/)
})
