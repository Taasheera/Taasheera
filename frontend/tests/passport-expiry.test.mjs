import assert from 'node:assert/strict'
import { test } from 'node:test'

const { expiryWarning } = await import('../src/passport/expiry.ts')
const { splitCitations } = await import('../src/assistant/citations.ts')

test('the passport page warns about an expired or soon-expiring passport', () => {
  const today = new Date('2026-11-25T12:00:00Z')
  assert.equal(expiryWarning('2012-04-15', today).tone, 'danger')
  assert.equal(expiryWarning('2027-03-01', today).tone, 'warn')
  assert.equal(expiryWarning('2034-04-15', today), null)
  assert.equal(expiryWarning('', today), null)
})

test('official links become a source list and other links stay as text', () => {
  const { body, sources } = splitCitations('Check https://www.gov.uk/standard-visitor. Also https://example.org/x and https://www.gov.uk/standard-visitor.')
  assert.equal(body, 'Check. Also https://example.org/x and.')
  assert.deepEqual(sources, ['https://www.gov.uk/standard-visitor'])
})
