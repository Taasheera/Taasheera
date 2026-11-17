import assert from 'node:assert/strict'
import { test } from 'node:test'

const { signedInHome } = await import('../src/navigation.ts')
const ID = 'a'.repeat(32)

test('signing in lands on the applications by default', () => {
  assert.equal(signedInHome(''), '/dashboard')
  assert.equal(signedInHome('?notice=idle'), '/dashboard')
})

test('a return to one of the signed-in pages is honoured', () => {
  assert.equal(signedInHome(`?return=/apply/${ID}`), `/apply/${ID}`)
  assert.equal(signedInHome(`?return=/dashboard/${ID}`), `/dashboard/${ID}`)
  assert.equal(signedInHome('?return=/passport'), '/passport')
  assert.equal(signedInHome('?return=/account'), '/account')
})

test('anything else in return is ignored, so it can never send a traveler off the site', () => {
  for (const target of ['https://evil.example', '//evil.example', '/admin', '/apply/../admin', '/dashboard?x=1', 'javascript:alert(1)'])
    assert.equal(signedInHome(`?return=${encodeURIComponent(target)}`), '/dashboard')
})
