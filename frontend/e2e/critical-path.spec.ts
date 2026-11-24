// Critical path (US 59, CMPS271-133): sign-in, passport upload, the application chat and the
// applications dashboard, against the real API and database with synthetic data only.
// Accounts are registered here with no second factor, so a future two-factor step (CMPS271-118)
// does not change these tests.
import { readFile } from 'node:fs/promises'
import { expect, request as playwrightRequest, test, type Page } from '@playwright/test'
import type { Checklist } from '../src/documents/api'

const password = 'Local-preview-only-123!'
const day = (offset: number) => new Date(Date.now() + offset * 86_400_000).toISOString().slice(0, 10)

async function register(page: Page, name: string) {
  const email = `critical-${Date.now()}-${Math.random().toString(36).slice(2, 8)}@example.com`
  const response = await page.request.post('/auth/register', { data: { name, email, password } })
  expect(response.status()).toBe(201)
  return email
}

async function signIn(page: Page, email: string, name: string) {
  await page.goto('/sign-in')
  await page.getByLabel('Email', { exact: true }).fill(email)
  await page.getByLabel('Password', { exact: true }).fill(password)
  await page.getByRole('button', { name: 'Sign in', exact: true }).click()
  // Signing in lands on the dashboard, which greets the traveler by first name.
  await expect(page).toHaveURL('/dashboard')
  await expect(page.getByText(new RegExp(`^Welcome( back)?, ${name.split(' ')[0]}$`))).toBeVisible()
}

test('sign-in refuses a wrong password, then signs in, survives a reload and logs out', async ({ page, context }) => {
  const email = await register(page, 'Critical Sign In')
  await page.goto('/sign-in')
  await page.getByLabel('Email', { exact: true }).fill(email)
  await page.getByLabel('Password', { exact: true }).fill('Not-the-password-1!')
  await page.getByRole('button', { name: 'Sign in', exact: true }).click()
  // The refusal arrives first, so the "not signed in" check after it means something.
  await expect(page.locator('#auth-status')).toHaveText('Invalid email or password.')
  await expect(page.getByText(/^Welcome( back)?, Critical$/)).toHaveCount(0)
  await expect(page).toHaveURL(/\/sign-in/)

  await signIn(page, email, 'Critical Sign In')
  await page.reload()
  await expect(page.getByText(/^Welcome( back)?, Critical$/)).toBeVisible()
  await page.getByRole('button', { name: 'Log out', exact: true }).first().click()
  // Logging out from the site header goes to the home page, signed out.
  await expect(page).toHaveURL('/')
  await expect(page.getByRole('link', { name: 'Log in', exact: true }).first()).toBeVisible()
  expect((await context.cookies()).some(cookie => cookie.name === 'taasheera_refresh')).toBe(false)
  await page.goto('/dashboard')
  await expect(page.getByText('Please sign in to see your applications.')).toBeVisible()
})

test('passport upload, the application chat and the dashboard work end to end', async ({ page }) => {
  test.setTimeout(120_000)
  const name = 'Critical Journey'
  const email = await register(page, name)
  await signIn(page, email, name)

  // Passport: a generated image, never a real passport. OCR can't read it, so the traveler types
  // the ICAO specimen's details and confirms them.
  await page.goto('/passport')
  const bytes = await page.evaluate(async () => {
    const canvas = document.createElement('canvas'); canvas.width = 800; canvas.height = 500
    const ctx = canvas.getContext('2d')!; ctx.fillStyle = 'white'; ctx.fillRect(0, 0, 800, 500)
    ctx.fillStyle = 'black'; ctx.font = '32px sans-serif';
    for (let row = 1; row < 10; row++) ctx.fillText('SPECIMEN TEST ONLY — NO PASSPORT DATA', 35, row * 45)
    return Array.from(new Uint8Array(await (await new Promise<Blob>(resolve => canvas.toBlob(value => resolve(value!), 'image/jpeg'))).arrayBuffer()))
  })
  await page.locator('input[type="file"]').first().setInputFiles({ name: 'synthetic.jpg', mimeType: 'image/jpeg', buffer: Buffer.from(bytes) })
  await expect(page).toHaveURL(/\/passport\/[a-f0-9]{32}/)
  await expect(page.getByLabel('Surname', { exact: true })).toBeVisible({ timeout: 30_000 })
  for (const [label, value] of [['Surname', 'ERIKSSON'], ['Given names', 'ANNA MARIA'], ['Passport number', 'L898902C3'],
    ['Nationality code', 'UTO'], ['Issuing country code', 'UTO'], ['Date of birth', '1974-08-12'], ['Expiry date', day(2000)]]) {
    await page.getByLabel(label, { exact: true }).fill(value)
  }
  await page.getByLabel('Sex', { exact: true }).selectOption('F')
  await page.getByRole('button', { name: 'Confirm these details' }).click()
  await expect(page.getByText('Your details are confirmed.', { exact: false })).toBeVisible({ timeout: 15_000 })

  // The application chat: the opening steps, then the first questions one at a time.
  await page.goto('/apply')
  await page.getByRole('button', { name: /Schengen area/ }).click()
  await page.getByRole('button', { name: /Short stay \(type C\)/ }).click()
  await page.getByRole('button', { name: 'Just me', exact: true }).click()
  await expect(page).toHaveURL(/\/apply\/[a-f0-9]{32}/)
  const applicationId = page.url().split('/').pop()!
  await page.getByLabel('Which Schengen country is your main destination?').selectOption('FRA')
  await page.getByRole('button', { name: 'Continue', exact: true }).click()
  await page.getByRole('button', { name: 'Tourism', exact: true }).click()
  await page.getByRole('group', { name: /more than 90 days/ }).getByRole('button', { name: 'No', exact: true }).click()
  // The confirmed passport is offered, not typed again.
  await expect(page.getByText('L898902C3')).toBeVisible()
  await page.getByRole('button', { name: "Yes, that's right" }).click()
  await page.getByLabel('When was your passport issued?').fill(day(-700))
  await page.getByRole('button', { name: 'Continue', exact: true }).click()
  await expect(page.getByLabel(/Did you have a different surname at birth\?/)).toBeVisible()

  // Every answer is saved as it is given: a reload resumes at the same question.
  await page.reload()
  await expect(page.locator('.chat-log')).toContainText('France')
  await expect(page.locator('.chat-log')).toContainText('Tourism')
  await expect(page.getByLabel(/Did you have a different surname at birth\?/)).toBeVisible()

  // The remaining answers through the same API the chat uses, signed in as the same traveler.
  const api = await playwrightRequest.newContext({ baseURL: 'http://127.0.0.1:5273' })
  try {
    const login = await api.post('/auth/login', { data: { email, password } })
    expect(login.status()).toBe(200)
    const headers = { Authorization: `Bearer ${(await login.json()).access_token}` }
    const answers: Record<string, unknown> = {
      surname_at_birth: '', place_of_birth: 'Zenith', country_of_birth: 'UTO', marital_status: 'single',
      home_address: { line1: '1 Example Street', city: 'Beirut', country: 'LBN' }, phone: '+961 71 123 456',
      occupation: 'retired', arrival_date: day(30), departure_date: day(40), entries: 'single',
      accommodation: 'host', host_details: 'Lina Example, 2 Rue Exemple, Paris', funding: 'self', previous_fingerprints: false,
    }
    let state = await (await api.get(`/applications/${applicationId}`, { headers })).json()
    while (state.current) {
      const response = await api.put(`/applications/${applicationId}/answers/${state.current.id}`,
        { headers, data: { value: answers[state.current.id] } })
      expect(response.status(), state.current.id).toBe(200)
      state = await response.json()
    }
    expect(state.status).toBe('ANSWERS_COMPLETE')
    expect(state.answers.map((answer: { question: { id: string } }) => answer.question.id)).not.toContain('accommodation_details')

    const docs = `/applications/${applicationId}/documents`
    const checklist: Checklist = await (await api.get(docs, { headers })).json()
    for (const item of checklist.requirements.filter(item => item.required)) {
      const uploaded = await api.post(docs, { headers, multipart: {
        requirement: item.id, file: { name: 'specimen.jpg', mimeType: 'image/jpeg', buffer: Buffer.from(bytes) },
      } })
      expect(uploaded.status(), item.id).toBe(201)
    }
  } finally {
    await api.dispose()
  }

  // The dashboard lists it with its status, and the detail page carries the rest of the journey.
  await page.goto('/dashboard')
  const card = page.locator('.dash-card').filter({ hasText: 'France' })
  await expect(card).toContainText('Answers complete')
  await expect(card.getByRole('link', { name: /Review answers for France/ })).toHaveAttribute('href', `/apply/${applicationId}`)
  await card.getByRole('link', { name: 'France', exact: true }).click()
  await expect(page).toHaveURL(`/dashboard/${applicationId}`)
  await expect(page.getByRole('heading', { name: 'Timeline' })).toBeVisible()
  await expect(page.locator('.dash-timeline')).toContainText('Every question answered')

  // Review the saved source values, explicitly confirm and download the commission form.
  await page.getByRole('button', { name: 'Review fields' }).click()
  const review = page.getByRole('region', { name: 'Review your embassy PDF' })
  await expect(review.getByRole('row', { name: /1\. Surname.*ERIKSSON/ })).toBeVisible()
  const desktop = page.viewportSize()!
  for (const width of [320, 375, 414, 768]) {
    await page.setViewportSize({ width, height: 900 })
    await expect(review.getByRole('heading', { name: 'Review your embassy PDF' })).toBeVisible()
    expect(await review.evaluate(region => region.getBoundingClientRect().right)).toBeLessThanOrEqual(width + 1)
  }
  await page.setViewportSize(desktop)
  const downloadPdf = review.getByRole('button', { name: 'Download PDF' })
  await expect(downloadPdf).toBeDisabled()
  const warnings = review.getByRole('checkbox', { name: /I reviewed the warnings/ })
  if (await warnings.isVisible()) await warnings.check()
  await review.getByRole('checkbox', { name: /I reviewed these values/ }).check()
  const [form] = await Promise.all([page.waitForEvent('download'), downloadPdf.click()])
  expect(form.suggestedFilename()).toBe('schengen-visa-form.pdf')
  expect((await readFile((await form.path())!)).subarray(0, 5).toString()).toBe('%PDF-')

  // The traveler records submitting it themself, then the embassy's decision (US 55).
  await page.getByRole('button', { name: "I've submitted it" }).click()
  await page.getByRole('button', { name: 'Yes, record it' }).click()
  await expect(page.locator('.dash-detail-head .dash-chip').first()).toHaveText('Submitted')
  await expect(page.locator('.dash-timeline')).toContainText('You recorded submitting it to the embassy')
  await page.getByRole('button', { name: 'Visa approved' }).click()
  await expect(page.locator('.dash-detail-head .dash-chip').first()).toHaveText('Approved')
  await expect(page.locator('.dash-timeline')).toContainText('You recorded that the embassy approved it')

  // Export (US 80): a JSON file the traveler keeps.
  const [download] = await Promise.all([page.waitForEvent('download'), page.getByRole('button', { name: /Download record of France/ }).click()])
  expect(download.suggestedFilename()).toMatch(/^taasheera-france-\d{4}-\d{2}-\d{2}\.json$/)
  const record = JSON.parse(await readFile((await download.path())!, 'utf-8'))
  expect(record.application.status).toBe('APPROVED')
  expect(record.answers.find((answer: { question_id: string }) => answer.question_id === 'main_destination').label).toBe('France')
  expect(record.timeline.events.map((event: { kind: string }) => event.kind)).toContain('approved')

  // Duplicate (US 78): a copy for Italy opens in the chat; the original keeps its record.
  await page.getByRole('button', { name: /Duplicate France/ }).click()
  await page.getByLabel('Which Schengen country is your main destination?').selectOption('ITA')
  await page.getByRole('button', { name: 'Create copy' }).click()
  await expect(page).toHaveURL(/\/apply\/[a-f0-9]{32}/)
  expect(page.url()).not.toContain(applicationId)
  await expect(page.locator('.chat-log')).toContainText('Italy')
  await expect(page.locator('.chat-log')).toContainText('Tourism')
  await expect(page.getByRole('group', { name: 'Where will you stay?' })).toBeVisible()

  await page.goto('/dashboard')
  await expect(page.locator('.dash-card')).toHaveCount(2)
  await expect(page.locator('.dash-card').filter({ hasText: 'France' })).toContainText('Approved')
  const copy = page.locator('.dash-card').filter({ hasText: 'Italy' })
  // Where the traveler stays belongs to France, so the copy asks it again.
  await expect(copy).toContainText('In progress')

  // Archive hides the copy until it is restored (US 79).
  await copy.getByRole('button', { name: /^Archive Italy/ }).click()
  await expect(page.locator('.dash-card')).toHaveCount(1)
  await page.getByRole('button', { name: 'Archived', exact: true }).click()
  await page.locator('.dash-card').filter({ hasText: 'Italy' }).getByRole('button', { name: /^Restore Italy/ }).click()
  // Saved before switching tabs, so the active list is read after the restore.
  await expect(page.locator('.dash-card')).toHaveCount(0)
  await page.getByRole('button', { name: 'Active', exact: true }).click()
  await expect(page.locator('.dash-card')).toHaveCount(2)
})
