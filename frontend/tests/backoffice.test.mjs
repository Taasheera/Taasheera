import assert from 'node:assert/strict'
import { registerHooks } from 'node:module'
import { afterEach, test } from 'node:test'
import { dom } from './dom-setup.mjs'
import { act, createElement } from 'react'
import { createRoot } from 'react-dom/client'
import { createMemoryRouter, RouterProvider } from 'react-router'

for (const name of ['Node', 'Element', 'HTMLAnchorElement', 'HTMLTextAreaElement', 'HTMLSelectElement',
  'MutationObserver', 'getComputedStyle', 'Event', 'Blob']) {
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
const { kpisToCsv } = await import('../src/admin/kpisApi.ts')

const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
const ADMIN = { id: 1, name: 'Adam Admin', email: 'adam@example.com', role: 'admin' }
const link = (label, url) => ({ label, url })
const GUIDE = {
  checked_on: '2026-11-25',
  need: { rule: 'Most passports need a visa.', notes: ['A note.'], checker: link('EU list', 'https://home-affairs.ec.europa.eu/visa'),
          checked_nationalities: { LBN: 'visa' } },
  fees: { items: [{ label: 'Adults', amount: '€90' }], note: 'Standard fees.', source: link('Fees', 'https://www.auswaertiges-amt.de/fees') },
  time: { text: 'About 15 days.', source: link('Visa Code', 'https://mzv.gov.cz/visa') },
  coverage: { level: 'filled_for_you', text: 'We fill the form.' },
  extra_sources: [],
}
const QUESTIONNAIRE = { id: 'schengen-short-stay', version: 2, title: 'Schengen short-stay visa (type C)', steps: [] }
const SUMMARY = {
  id: 'schengen-short-stay', title: 'Schengen short-stay visa (type C)', destination_label: 'Schengen area',
  visa_label: 'Short stay (type C)', revision: 2, questionnaire_version: 2, packaged_version: 1,
  published_at: '2026-11-26T09:00:00Z', has_draft: false, applications_active: 4, applications_recorded: 1,
}
const HISTORY = [
  { revision: 2, kind: 'publish', restored_revision: null, questionnaire_version: 2, note: 'Fee update', author: 'Adam Admin', created_at: '2026-11-26T09:00:00Z' },
  { revision: 1, kind: 'publish', restored_revision: null, questionnaire_version: 2, note: 'New question', author: null, created_at: '2026-11-25T09:00:00Z' },
]
const DETAIL = { ...SUMMARY, current: { questionnaire: QUESTIONNAIRE, guide: GUIDE }, draft: null, history: HISTORY }
const VALID = { valid: true, problems: [], questionnaire_version: 2,
  changes: [{ part: 'guide', path: 'fees.items[0].amount', kind: 'changed', before: '€90', after: '€95' }] }
const KPIS = {
  start: '2026-11-01', end: '2026-11-14', days: 14,
  usage: { active_users: 2, average_daily_active: 0.36, daily_active: [{ day: '2026-11-01', count: 1 }, { day: '2026-11-02', count: 0 }],
    weekly_active: [{ week_start: '2026-10-26', count: 1 }], registrations: 2, registrations_by_method: { email_password: 1, google: 1 },
    sign_ins: 6, sign_in_successes: 3, sign_in_failures: 2, sign_ins_suspended: 1, sign_in_success_rate: 0.5, sign_in_failure_rate: 0.3333 },
  applications: { started: 5, completed: 3, submitted: 2, decided: 1, approved: 1, refused: 0, ineligible: 1,
    funnel: [{ stage: 'started', count: 5, conversion: null }, { stage: 'completed', count: 3, conversion: 0.6 },
      { stage: 'submitted', count: 2, conversion: 0.6667 }, { stage: 'decided', count: 1, conversion: 0.5 }],
    median_hours_to_complete: 6 },
  agent: { runs: 6, ready: 1, stopped: 2, site_changed: 1, failed: 1, cancelled: 1, in_progress: 1, success_rate: 0.25,
    failure_rate: 0.25, site_changed_rate: 0.25 },
  assistant: { messages: 2, answered: 1, proposals: 0, drafts: 0, unavailable: 1, unavailable_rate: 0.5, users: 1 },
  api_errors: 2, api_errors_daily: [{ day: '2026-11-01', count: 0 }, { day: '2026-11-02', count: 2 }],
  destinations: [{ questionnaire_id: 'schengen-short-stay', title: 'Schengen, "short" stay', started: 3, completed: 2, submitted: 1,
    decided: 1, agent_runs: 6, agent_ready: 1 }],
}

let handlers
let calls
globalThis.fetch = async (url, options = {}) => {
  const key = `${options.method ?? 'GET'} ${url}`
  calls.push({ key, body: options.body })
  const handler = handlers[key]
  assert.ok(handler, `Unexpected request: ${key}`)
  return handler(options)
}

function backend(extra = {}, flags = { requirements_editor: true, kpi_dashboard: true }) {
  calls = []
  resetFeatureFlags()
  handlers = {
    'POST /auth/refresh': () => json({ access_token: 'token' }),
    'GET /auth/me': () => json({ id: ADMIN.id, name: ADMIN.name, email: ADMIN.email }),
    'GET /auth/config': () => json({ google_client_id: null }),
    'POST /activity/events': () => new Response(null, { status: 204 }),
    'GET /features': () => json(flags),
    'GET /admin-api/me': () => json(ADMIN),
    'GET /admin-api/requirements': () => json([SUMMARY]),
    'GET /admin-api/requirements/schengen-short-stay': () => json(DETAIL),
    ...extra,
  }
}

let root
let container
let router
async function render(path) {
  container = document.createElement('div')
  document.body.append(container)
  router = createMemoryRouter(routes, { initialEntries: [path] })
  root = createRoot(container)
  await act(async () => root.render(createElement(RouterProvider, { router })))
  await settle()
}
const settle = () => act(() => new Promise(resolve => setTimeout(resolve, 30)))
const $ = selector => container.querySelector(selector)
const page = () => container.textContent
const button = label => [...container.querySelectorAll('button')].find(item => item.textContent.trim() === label)
const requested = prefix => calls.filter(call => call.key.startsWith(prefix))

async function click(element) {
  assert.ok(element, 'Missing element to click')
  await act(async () => element.dispatchEvent(new window.MouseEvent('click', { bubbles: true })))
  await settle()
}

async function type(element, value) {
  const prototype = element.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype
    : element.tagName === 'SELECT' ? window.HTMLSelectElement.prototype : window.HTMLInputElement.prototype
  await act(async () => {
    Object.getOwnPropertyDescriptor(prototype, 'value').set.call(element, value)
    element.dispatchEvent(new window.Event(element.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true }))
  })
  await settle()
}

afterEach(async () => {
  await act(async () => root?.unmount())
  container?.remove()
  document.body.querySelectorAll('[data-sonner-toaster]').forEach(node => node.remove())
})

test('the admin menu links to requirements and KPIs while their flags are on', async () => {
  backend()
  await render('/admin/requirements')
  assert.ok($('a[href="/admin/requirements"]') && $('a[href="/admin/kpis"]'))
  assert.match(page(), /Destination requirements/)
  assert.match($('#destination-picker').textContent, /Schengen short-stay visa \(type C\) · revision 2/)
})

test('picking a destination shows what travelers see, the guide form and the history', async () => {
  backend()
  await render('/admin/requirements?destination=schengen-short-stay')
  assert.match(page(), /Travelers see revision 2 \(questionnaire version 2\)/)
  assert.match(page(), /4 application\(s\) in progress/)
  assert.equal($('#guide-fees\\.items\\.0\\.amount').value, '€90')
  assert.equal($('#guide-checked_on').value, '2026-11-25')
  assert.deepEqual(JSON.parse($('#questionnaire-json').value), QUESTIONNAIRE)
  assert.match(page(), /Fee update/)
  assert.match(page(), /Deleted account/)
  assert.ok(button('Roll back to 1') && button('Return to the packaged files'))
  assert.equal(button('Roll back to 2'), undefined)
})

test('checking shows the changes, inline problems and the version publishing would give', async () => {
  backend({ 'POST /admin-api/requirements/schengen-short-stay/check': () => json({
    valid: false, questionnaire_version: 2, changes: VALID.changes,
    problems: [{ part: 'guide', loc: ['fees', 'items', 0, 'amount'], path: 'fees.items[0].amount', message: 'String should have at most 20 characters' },
      { part: 'questionnaire', loc: [], path: '', message: 'Question id \'purpose\' is used twice.' }] }) })
  await render('/admin/requirements?destination=schengen-short-stay')
  await type($('#guide-fees\\.items\\.0\\.amount'), '€95')
  await click(button('Check changes'))
  const [sent] = requested('POST /admin-api/requirements/schengen-short-stay/check')
  assert.equal(JSON.parse(sent.body).guide.fees.items[0].amount, '€95')
  assert.deepEqual(JSON.parse(sent.body).questionnaire, QUESTIONNAIRE)
  assert.match(page(), /Changed guide\.fees\.items\[0\]\.amount €90 → €95/)
  const amount = $('#guide-fees\\.items\\.0\\.amount')
  assert.equal(amount.getAttribute('aria-invalid'), 'true')
  assert.match($(`#${amount.getAttribute('aria-describedby').replaceAll('.', '\\.')}`).textContent, /at most 20 characters/)
  assert.match($('#questionnaire-json-problems').textContent, /used twice/)
})

test('a questionnaire that is not JSON is caught before anything is sent', async () => {
  backend()
  await render('/admin/requirements?destination=schengen-short-stay')
  await type($('#questionnaire-json'), '{"id": "schengen-short-stay",')
  assert.match(page(), /Not valid JSON/)
  assert.equal(button('Check changes').disabled, true)
  assert.equal(button('Publish').disabled, true)
})

test('publishing saves the draft, then publishes it with the note', async () => {
  backend({
    'PUT /admin-api/requirements/schengen-short-stay/draft': () => json({ draft: { ...DETAIL.current, base_revision: 2, author: 'Adam Admin',
      updated_at: '2026-11-27T09:00:00Z' }, check: VALID }),
    'POST /admin-api/requirements/schengen-short-stay/publish': () => json({ ...DETAIL, revision: 3,
      history: [{ ...HISTORY[0], revision: 3, note: 'Fee is €95' }, ...HISTORY] }),
  })
  await render('/admin/requirements?destination=schengen-short-stay')
  await type($('#guide-fees\\.items\\.0\\.amount'), '€95')
  assert.equal(button('Publish').disabled, true)
  await type($('#publish-note'), 'Fee is €95')
  await click(button('Publish'))
  assert.deepEqual(requested('PUT /admin-api').concat(requested('POST /admin-api')).map(call => call.key), [
    'PUT /admin-api/requirements/schengen-short-stay/draft', 'POST /admin-api/requirements/schengen-short-stay/publish'])
  const draft = JSON.parse(requested('PUT /admin-api')[0].body)
  assert.equal(draft.base_revision, 2)
  assert.equal(draft.guide.fees.items[0].amount, '€95')
  assert.deepEqual(JSON.parse(requested('POST /admin-api')[0].body), { base_revision: 2, note: 'Fee is €95' })
  assert.match(page(), /Published revision 3/)
})

test('a refused publish lists the problems', async () => {
  backend({
    'PUT /admin-api/requirements/schengen-short-stay/draft': () => json({ draft: { ...DETAIL.current, base_revision: 2, author: 'Adam Admin',
      updated_at: '2026-11-27T09:00:00Z' }, check: VALID }),
    'POST /admin-api/requirements/schengen-short-stay/publish': () => json({ detail: { message: 'Fix these problems before publishing.',
      problems: [{ part: 'questionnaire', loc: ['steps'], path: 'steps', message: 'List should have at least 1 item' }] } }, 422),
  })
  await render('/admin/requirements?destination=schengen-short-stay')
  await type($('#publish-note'), 'Try')
  await click(button('Publish'))
  assert.match(page(), /Fix these problems before publishing\./)
  assert.match($('#questionnaire-json-problems').textContent, /steps List should have at least 1 item/)
})

test('a rollback shows the comparison first and needs a reason', async () => {
  backend({
    'GET /admin-api/requirements/schengen-short-stay/revisions/1': () => json({ questionnaire: QUESTIONNAIRE, guide: GUIDE }),
    'POST /admin-api/requirements/schengen-short-stay/check': () => json(VALID),
    'POST /admin-api/requirements/schengen-short-stay/rollback': () => json({ ...DETAIL, revision: 3 }),
  })
  await render('/admin/requirements?destination=schengen-short-stay')
  await click(button('Roll back to 1'))
  assert.match(page(), /Travelers would get questionnaire version 2/)
  assert.equal(button('Confirm rollback').disabled, true)
  await type($('#rollback-note'), 'The old fee was right')
  await click(button('Confirm rollback'))
  assert.deepEqual(JSON.parse(requested('POST /admin-api/requirements/schengen-short-stay/rollback')[0].body),
    { revision: 1, base_revision: 2, note: 'The old fee was right' })
  assert.match(page(), /Revision 3 is live/)
})

test('with the flags off the pages are not found and the links are hidden', async () => {
  backend({}, { requirements_editor: false, kpi_dashboard: false })
  await render('/admin/requirements')
  assert.equal($('a[href="/admin/requirements"]'), null)
  assert.equal($('a[href="/admin/kpis"]'), null)
  assert.deepEqual(requested('GET /admin-api/requirements'), [])
  assert.doesNotMatch(page(), /Destination requirements/)
})

test('the KPI page shows the numbers for the chosen range and moves between ranges', async () => {
  backend({
    'GET /admin-api/kpis?start=2026-11-01&end=2026-11-14': () => json(KPIS),
    'GET /admin-api/kpis?start=2026-11-08&end=2026-11-14': () => json({ ...KPIS, start: '2026-11-08', days: 7 }),
  })
  await render('/admin/kpis?start=2026-11-01&end=2026-11-14')
  for (const value of ['Active users2', 'Sign-in success50.0%', 'Median time to complete6.0 h', 'Agent success25.0%',
    'Assistant unavailable50.0%', '60.0% of the stage before', 'Schengen, "short" stay']) {
    assert.ok(page().includes(value), value)
  }
  assert.match(page(), /2026-11-01 to 2026-11-14 \(14 days, UTC\)/)
  await type($('#kpi-start'), '2026-11-08')
  await act(async () => $('form[aria-label="Date range"]').dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true })))
  await settle()
  assert.equal(router.state.location.search, '?start=2026-11-08&end=2026-11-14')
  assert.match(page(), /7 days, UTC/)
})

test('a refused range explains the limits', async () => {
  backend({ 'GET /admin-api/kpis?start=2026-01-01&end=2026-11-14': () => json({ detail: [] }, 422) })
  await render('/admin/kpis?start=2026-01-01&end=2026-11-14')
  assert.match(page(), /at most 180 days apart/)
})

test('the CSV download holds the aggregates, one figure per row', async () => {
  backend({ 'GET /admin-api/kpis?start=2026-11-01&end=2026-11-14': () => json(KPIS) })
  const saved = []
  URL.createObjectURL = blob => { saved.push(blob); return 'blob:kpis' }
  URL.revokeObjectURL = () => {}
  await render('/admin/kpis?start=2026-11-01&end=2026-11-14')
  await click(button('Download CSV'))
  assert.equal(saved.length, 1)
  const csv = kpisToCsv(KPIS)
  const lines = csv.trim().split('\r\n')
  assert.equal(lines[0], 'section,item,metric,value')
  for (const line of ['usage,,active_users,2', 'usage,google,registrations,1', 'daily_active,2026-11-01,users,1',
    'funnel,completed,conversion,0.6', 'funnel,started,conversion,', 'agent,,site_changed_rate,0.25',
    'api_errors_daily,2026-11-02,errors,2', 'destination,schengen-short-stay,agent_ready,1']) {
    assert.ok(lines.includes(line), line)
  }
  assert.ok(!csv.includes('short" stay'), 'titles stay out of the CSV')
  assert.equal(await saved[0].text(), csv)
})
