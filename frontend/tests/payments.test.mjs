import assert from 'node:assert/strict'
import { afterEach, beforeEach, test } from 'node:test'
import { dom } from './dom-setup.mjs'
import { act, createElement } from 'react'
import { createRoot } from 'react-dom/client'

for (const name of ['Node', 'Element', 'HTMLElement', 'HTMLAnchorElement', 'getComputedStyle']) {
  globalThis[name] ??= dom.window[name]
}

const { default: PaymentStep } = await import('../src/payments/PaymentStep.tsx')
const { default: AgentPanel } = await import('../src/agent/AgentPanel.tsx')
const { formatMoney } = await import('../src/payments/api.ts')

const APP = 'b'.repeat(32)
const PAYMENT = 'd'.repeat(32)
const BASE = `/applications/${APP}/payments`
const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
const lines = [
  { kind: 'service_fee', label: 'Ta\'asheera service fee', amount_minor: 2700 },
  { kind: 'embassy_fee', label: 'Embassy fee: Adults and children aged 12 and over', amount_minor: 9000 },
]
const quote = (extra = {}) => ({
  currency: 'EUR', lines, total_minor: 11700, paid_at_embassy: null,
  fee_source: { label: 'German Federal Foreign Office: Schengen visa fee', url: 'https://www.auswaertiges-amt.de/en/-/2658682' },
  checked_on: '2026-11-25', ...extra,
})
const payment = (extra = {}) => ({
  id: PAYMENT, receipt_number: 'TA-DDDDDDDDDD', status: 'paid', amount_minor: 11700, currency: 'EUR', line_items: lines,
  provider: 'simulated', created_at: '2026-11-27T10:00:00Z', paid_at: '2026-11-27T10:00:00Z', ...extra,
})
const state = (extra = {}) => ({ quote: quote(), simulated: true, available: true, can_pay: true, reason: null, payments: [], ...extra })

let handlers
let calls
let bodies
globalThis.fetch = async (url, options = {}) => {
  const key = `${options.method ?? 'GET'} ${url}`
  calls.push(key)
  if (options.body) bodies.push(JSON.parse(options.body))
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
let paidCount
async function render(component = PaymentStep) {
  container = document.createElement('div')
  document.body.append(container)
  root = createRoot(container)
  await act(async () => root.render(createElement(component, { applicationId: APP, onPaid: () => { paidCount += 1 } })))
  await settle()
}
const settle = () => act(() => new Promise(resolve => setTimeout(resolve, 30)))
const text = () => container.textContent.replace(/\u00a0/g, ' ')
const button = label => [...container.querySelectorAll('button')].find(b => b.textContent.replace(/\u00a0/g, ' ').trim() === label)
async function click(element) {
  await act(async () => element.click())
  await settle()
}

beforeEach(() => { calls = []; bodies = []; handlers = {}; paidCount = 0 })
afterEach(() => {
  if (root) act(() => root.unmount())
  container?.remove()
  root = container = undefined
})

test('amounts are shown in their currency with its own decimals', () => {
  assert.equal(formatMoney(11700, 'EUR').replace(/\u00a0/g, ' '), 'EUR 117.00')
  assert.equal(formatMoney(-500, 'USD').replace(/\u00a0/g, ' '), '-USD 5.00')
  assert.equal(formatMoney(15000, 'JPY').replace(/\u00a0/g, ' '), 'JPY 15,000')
})

test('the breakdown shows the service fee, embassy fee, discount and total before paying, in test mode', async () => {
  handlers = signedIn({ [`GET ${BASE}`]: () => json(state()) })
  await render()
  const rows = [...container.querySelectorAll('.payment-breakdown tr')].map(row => row.textContent.replace(/\u00a0/g, ' '))
  assert.deepEqual(rows, [
    'Ta\'asheera service feeEUR 27.00',
    'Embassy fee: Adults and children aged 12 and overEUR 90.00',
    'DiscountNone',
    'TotalEUR 117.00',
  ])
  assert.match(text(), /Test mode/)
  assert.match(text(), /simulated payment: no card is needed, no card details are asked for and nothing is charged/)
  assert.match(text(), /print-ready packet are free/)
  assert.equal(container.querySelector('.payment a').getAttribute('href'), 'https://www.auswaertiges-amt.de/en/-/2658682')
  assert.equal(container.querySelectorAll('input').length, 0)
  assert.equal(button('Pay EUR 117.00').disabled, false)
})

test('an application that isn’t ready says why it can’t be paid for yet', async () => {
  handlers = signedIn({ [`GET ${BASE}`]: () => json(state({ can_pay: false, reason: 'Answer every question first.' })) })
  await render()
  assert.equal(button('Pay EUR 117.00').disabled, true)
  assert.match(text(), /Answer every question first/)
})

test('an embassy fee that isn’t charged here is named as paid at the embassy', async () => {
  handlers = signedIn({ [`GET ${BASE}`]: () => json(state({ quote: quote({ currency: 'USD', total_minor: 2900, paid_at_embassy: 'Single entry: About ¥15,000',
    lines: [{ kind: 'service_fee', label: 'Ta\'asheera service fee', amount_minor: 2900 }] }) })) })
  await render()
  assert.match(text(), /Not included: the embassy fee \(Single entry: About ¥15,000\), which you pay at the embassy/)
  assert.ok(button('Pay USD 29.00'))
})

test('paying sends only a checkout key, once, then shows the receipt', async () => {
  let paid = false
  handlers = signedIn({
    [`GET ${BASE}`]: () => json(paid ? state({ can_pay: false, payments: [payment()] }) : state()),
    [`POST ${BASE}`]: () => { paid = true; return json(payment(), 201) },
  })
  await render()
  const pay = button('Pay EUR 117.00')
  await act(async () => { pay.click(); pay.click() })
  await settle()
  assert.equal(calls.filter(call => call === `POST ${BASE}`).length, 1)
  assert.deepEqual(Object.keys(bodies[0]), ['idempotency_key'])
  assert.match(bodies[0].idempotency_key, /^[A-Za-z0-9-]{16,64}$/)
  assert.equal(paidCount, 1)
  assert.match(text(), /Paid EUR 117\.00/)
  assert.match(text(), /Receipt TA-DDDDDDDDDD/)
  assert.match([...container.querySelectorAll('.payment-breakdown tr')].at(-1).textContent, /Total paid/)
  assert.equal(button('Pay EUR 117.00'), undefined)
  assert.ok(button('Download receipt'))
})

test('a refused payment says so and the next attempt is a new checkout', async () => {
  let attempts = 0
  handlers = signedIn({
    [`GET ${BASE}`]: () => json(state()),
    [`POST ${BASE}`]: () => { attempts += 1; return json({ detail: 'The payment didn\'t go through and nothing was charged. Please try again.' }, 402) },
  })
  await render()
  await click(button('Pay EUR 117.00'))
  assert.match(container.querySelector('[role="alert"]').textContent, /nothing was charged/)
  await click(button('Pay EUR 117.00'))
  assert.equal(attempts, 2)
  assert.notEqual(bodies[0].idempotency_key, bodies[1].idempotency_key)
  assert.equal(paidCount, 0)
})

test('the receipt downloads from the owner’s receipt route', async () => {
  handlers = signedIn({
    [`GET ${BASE}`]: () => json(state({ can_pay: false, payments: [payment()] })),
    [`GET ${BASE}/${PAYMENT}/receipt`]: () => new Response(new Uint8Array([37, 80, 68, 70]), {
      headers: { 'Content-Type': 'application/pdf', 'Content-Disposition': 'attachment; filename="taasheera-receipt-TA-DDDDDDDDDD.pdf"' },
    }),
  })
  const saved = []
  URL.createObjectURL = () => 'blob:receipt'
  URL.revokeObjectURL = () => {}
  const original = dom.window.HTMLAnchorElement.prototype.click
  dom.window.HTMLAnchorElement.prototype.click = function () { saved.push(this.download) }
  try {
    await render()
    await click(button('Download receipt'))
  } finally {
    dom.window.HTMLAnchorElement.prototype.click = original
  }
  assert.ok(calls.includes(`GET ${BASE}/${PAYMENT}/receipt`))
  assert.deepEqual(saved, ['taasheera-receipt-TA-DDDDDDDDDD.pdf'])
})

test('destinations without a submission step show no payment', async () => {
  handlers = signedIn({ [`GET ${BASE}`]: () => json(state({ available: false, can_pay: false, reason: 'Payment opens once the agent can fill this application’s embassy form.' })) })
  await render()
  assert.equal(container.innerHTML, '')
})

test('the agent says payment unlocks it and its button waits', async () => {
  handlers = signedIn({ [`GET /applications/${APP}/agent`]: () => json({
    site: { id: 'schengen-harmonised', name: 'Harmonised Schengen visa application form' }, can_start: false,
    reason: 'Pay for this application to let the agent fill the embassy form. Everything before this step is free.',
    payment_required: true, runs: [],
  }) })
  await render(AgentPanel)
  assert.equal(button('Fill the embassy form').disabled, true)
  assert.match(text(), /Pay for this application to let the agent fill the embassy form/)
})
