import assert from 'node:assert/strict'
import { afterEach, beforeEach, test } from 'node:test'
import { dom } from './dom-setup.mjs'
import { act, createElement } from 'react'
import { createRoot } from 'react-dom/client'

for (const name of ['Node', 'Element', 'HTMLElement', 'getComputedStyle']) {
  globalThis[name] ??= dom.window[name]
}

const { default: AgentPanel } = await import('../src/agent/AgentPanel.tsx')

const APP = 'b'.repeat(32)
const RUN = 'e'.repeat(32)
const BASE = `/applications/${APP}/agent`
const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
const site = { id: 'schengen-harmonised', name: 'Harmonised Schengen visa application form' }
const run = (extra = {}) => ({
  id: RUN, site: site.id, status: 'ready', message: 'The form is filled and waiting for you to review and submit it.',
  created_at: '2026-11-16T10:00:00Z', started_at: '2026-11-16T10:00:01Z', finished_at: '2026-11-16T10:00:09Z', ...extra,
})
const detail = (extra = {}) => ({
  ...run(), entries: [
    { page: 1, page_title: 'Personal details', field: '1. Surname', value: 'ERIKSSON', entered_at: '2026-11-16T10:00:02Z' },
    { page: 2, page_title: 'Travel document', field: '14. Number of travel document', value: 'L898902C3', entered_at: '2026-11-16T10:00:04Z' },
  ],
  screenshots: [{ id: 'f'.repeat(32), page: 1, page_title: 'Personal details', taken_at: '2026-11-16T10:00:03Z' }],
  ...extra,
})

let handlers
let calls
globalThis.fetch = async (url, options = {}) => {
  const key = `${options.method ?? 'GET'} ${url}`
  calls.push(key)
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
async function render() {
  container = document.createElement('div')
  document.body.append(container)
  root = createRoot(container)
  await act(async () => root.render(createElement(AgentPanel, { applicationId: APP })))
  await settle()
}
const settle = () => act(() => new Promise(resolve => setTimeout(resolve, 30)))
const text = () => container.textContent
const button = label => [...container.querySelectorAll('button')].find(b => b.textContent.trim() === label)
async function click(element) {
  await act(async () => element.click())
  await settle()
}

beforeEach(() => { calls = []; handlers = {} })
afterEach(() => {
  if (root) act(() => root.unmount())
  container?.remove()
  root = container = undefined
})

test('an incomplete application explains why the agent can’t start', async () => {
  handlers = signedIn({ [`GET ${BASE}`]: () => json({ site, can_start: false, reason: 'Answer every question first.', runs: [] }) })
  await render()
  assert.match(text(), /stops before submitting/)
  assert.equal(button('Fill the embassy form').disabled, true)
  assert.match(text(), /Answer every question first/)
  assert.match(text(), /Nothing is submitted to an embassy without you/)
})

test('starting the agent queues a run and shows it waiting', async () => {
  let started = false
  handlers = signedIn({
    [`GET ${BASE}`]: () => json({ site, can_start: !started, reason: null, runs: started ? [run({ status: 'queued', finished_at: null, message: null })] : [] }),
    [`POST ${BASE}/runs`]: () => { started = true; return json(run({ status: 'queued' }), 201) },
    [`GET ${BASE}/runs/${RUN}`]: () => json(detail({ status: 'queued', entries: [], screenshots: [] })),
  })
  await render()
  await click(button('Fill the embassy form'))
  assert.ok(calls.includes(`POST ${BASE}/runs`))
  assert.match(text(), /Waiting for the agent/)
  assert.ok(button('Cancel'))
})

test('a finished run shows every field it typed, page by page, with screenshots on request', async () => {
  handlers = signedIn({
    [`GET ${BASE}`]: () => json({ site, can_start: true, reason: null, runs: [run()] }),
    [`GET ${BASE}/runs/${RUN}`]: () => json(detail()),
    [`GET ${BASE}/runs/${RUN}/screenshots/${'f'.repeat(32)}`]: () => new Response(new Uint8Array([137, 80, 78, 71]), { headers: { 'Content-Type': 'image/png' } }),
  })
  URL.createObjectURL ??= () => 'blob:screenshot'
  URL.revokeObjectURL ??= () => {}
  await render()
  assert.match(text(), /Filled\. Waiting for you to review and submit/)
  const audit = container.querySelector('.agent-audit')
  assert.ok(audit.open)
  assert.match(audit.querySelector('summary').textContent, /2 fields on 2 pages/)
  const rows = [...audit.querySelectorAll('tbody tr')].map(row => row.textContent)
  assert.match(rows[0], /1\. Surname.*ERIKSSON/)
  assert.match(rows[1], /14\. Number of travel document.*L898902C3/)
  assert.deepEqual([...audit.querySelectorAll('h3')].map(h => h.textContent), ['Page 1: Personal details', 'Page 2: Travel document'])
  await click(button('Show screenshot of this page'))
  assert.match(audit.querySelector('.agent-shot img').alt, /page 1, Personal details/)
  assert.ok(button('Fill the form again'))
})

test('a stopped run says why', async () => {
  handlers = signedIn({
    [`GET ${BASE}`]: () => json({ site, can_start: true, reason: null, runs: [run({ status: 'stopped', message: 'the site asked for a CAPTCHA, which only you can answer' })] }),
    [`GET ${BASE}/runs/${RUN}`]: () => json(detail({ status: 'stopped', entries: [], screenshots: [] })),
  })
  await render()
  assert.match(container.querySelector('.agent-run').textContent, /The agent stopped: the site asked for a CAPTCHA/)
  assert.ok(container.querySelector('.agent-run').classList.contains('is-stopped'))
})

test('destinations without a known form say so and offer no button', async () => {
  handlers = signedIn({ [`GET ${BASE}`]: () => json({ site: null, can_start: false, reason: 'The agent can’t fill this destination’s online form yet.', runs: [] }) })
  await render()
  assert.match(text(), /can’t fill this destination’s online form yet/)
  assert.equal(button('Fill the embassy form'), undefined)
})
