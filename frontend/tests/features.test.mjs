import { dom } from './dom-setup.mjs'
import assert from 'node:assert/strict'
import { afterEach, beforeEach, test } from 'node:test'
import { act, createElement } from 'react'
import { createRoot } from 'react-dom/client'

globalThis.Node ??= dom.window.Node

const { default: Feature } = await import('../src/Feature.tsx')
const { resetFeatureFlags } = await import('../src/features.ts')

const realFetch = globalThis.fetch
let requests
let container
let root

beforeEach(() => {
  resetFeatureFlags()
  requests = []
  container = document.createElement('div')
  document.body.append(container)
  root = createRoot(container)
})

afterEach(async () => {
  await act(async () => root.unmount())
  container.remove()
  globalThis.fetch = realFetch
})

function serve(respond) {
  globalThis.fetch = async (path, options) => {
    requests.push(path)
    return respond(path, options)
  }
}

function json(body, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function page() {
  return createElement('div', null,
    createElement(Feature, { name: 'agent_form_filling' }, createElement('p', null, 'Agent form filling')),
    createElement(Feature, { name: 'supporting_documents', fallback: createElement('p', null, 'Page not found') },
      createElement('p', null, 'Supporting documents')))
}

async function render(element) {
  await act(async () => root.render(element))
  // Let the flag request settle and React re-render.
  await act(async () => {})
}

test('each flagged part follows its own flag, from one shared request', async () => {
  serve(() => json({ agent_form_filling: true, supporting_documents: false }))
  await render(page())
  assert.equal(container.textContent, 'Agent form fillingPage not found')
  assert.deepEqual(requests, ['/features'])
})

test('nothing flagged shows while the flags are still loading', async () => {
  let answer
  serve(() => new Promise(resolve => { answer = resolve }))
  await act(async () => root.render(page()))
  assert.equal(container.textContent, '')
  await act(async () => answer(json({ agent_form_filling: true, supporting_documents: true })))
  assert.equal(container.textContent, 'Agent form fillingSupporting documents')
})

test('a flag the server does not mention counts as off', async () => {
  serve(() => json({ supporting_documents: true }))
  await render(page())
  assert.equal(container.textContent, 'Supporting documents')
})

test('unreadable flags hide flagged work without a toast, and the next page asks again', async () => {
  serve(() => json({ detail: 'Service unavailable. Please try again.' }, 503))
  await render(page())
  assert.equal(container.textContent, 'Page not found')
  assert.equal(document.querySelector('[data-sonner-toast]'), null)

  serve(() => { throw new TypeError('Failed to fetch') })
  await act(async () => root.unmount())
  root = createRoot(container)
  await render(page())
  assert.equal(container.textContent, 'Page not found')

  serve(() => json({ agent_form_filling: true, supporting_documents: true }))
  await act(async () => root.unmount())
  root = createRoot(container)
  await render(page())
  assert.equal(container.textContent, 'Agent form fillingSupporting documents')
  assert.deepEqual(requests, ['/features', '/features', '/features'])
})
