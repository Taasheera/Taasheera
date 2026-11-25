import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { JSDOM } from 'jsdom'

for (const [filename, title, updated] of [['privacy.html', 'Privacy policy', '2026-11-25'], ['terms.html', 'Terms of Service', '2026-10-31']]) {
  test(`${filename} is a complete public document without authentication or JavaScript`, () => {
    const html = readFileSync(new URL(`../public/${filename}`, import.meta.url), 'utf8')
    const document = new JSDOM(html).window.document
    assert.equal(document.documentElement.lang, 'en')
    assert.equal(document.querySelector('h1').textContent, title)
    assert.match(document.title, /Taasheera/)
    assert.equal(document.querySelectorAll('script, iframe, form, input').length, 0)
    assert.ok(document.querySelector('main'))
    assert.equal(document.querySelector('link[rel="stylesheet"]').getAttribute('href'), '/legal.css')
    for (const path of ['/', '/privacy.html', '/terms.html', 'mailto:sharafm032@gmail.com']) {
      assert.ok(document.querySelector(`a[href="${path}"]`), path)
    }
    assert.equal(document.querySelectorAll('h1').length, 1)
    assert.equal(document.querySelector('time').getAttribute('datetime'), updated)
    assert.equal(/localhost|127\.0\.0\.1|YOUR_DOMAIN|example\.com|TODO/.test(html), false)
  })
}

test('the public homepage shell links the exact policy URLs without requiring app startup', () => {
  const document = new JSDOM(readFileSync(new URL('../index.html', import.meta.url), 'utf8')).window.document
  const policies = document.querySelector('footer[aria-label="Taasheera policies"]')
  assert.ok(policies)
  assert.equal(policies.closest('#root'), null)
  assert.ok(policies.querySelector('a[href="/privacy.html"]'))
  assert.ok(policies.querySelector('a[href="/terms.html"]'))
  assert.match(policies.textContent, /Google Sign-In.*authenticate/)
})

test('the privacy policy opens with a plain-language summary that mentions accepting it', () => {
  const document = new JSDOM(readFileSync(new URL('../public/privacy.html', import.meta.url), 'utf8')).window.document
  const summary = document.querySelector('#summary')
  assert.equal(summary.tagName, 'H2')
  const points = [...summary.nextElementSibling.querySelectorAll('li')].map(item => item.textContent)
  assert.ok(points.length >= 5)
  assert.ok(points.some(point => /accept this policy before uploading/.test(point)))
  assert.ok(points.some(point => /download everything we hold about you, or delete your account/.test(point)))
  // The summary comes before the detailed sections.
  assert.equal(document.querySelector('main h2'), summary)
})
