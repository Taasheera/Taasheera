// Sign-in rate limits still protect everyone except staging's own load generator.
//
//   k6 run -e BASE_URL=https://<staging>.cloudfront.net loadtest/rate-limits.js
//
// Run from a machine whose address is NOT in THROTTLE_EXEMPT_IPS. Wrong passwords for addresses
// that do not exist: 30 attempts per address per 15 minutes are answered 401, then 429 with
// Retry-After. Uses one 15-minute window of the caller's own allowance, nothing else.
import http from 'k6/http'
import { check } from 'k6'
import { BASE_URL, ORIGIN } from './lib/config.js'

export const options = { vus: 1, iterations: 1, thresholds: { checks: ['rate==1'] } }

export default function () {
  const statuses = []
  for (let attempt = 0; attempt < 34; attempt += 1) {
    const reply = http.post(`${BASE_URL}/auth/login`,
      JSON.stringify({ email: `ratelimit-probe-${attempt % 8}@example.com`, password: 'Wrong-password-123' }),
      { headers: { 'Content-Type': 'application/json', Origin: ORIGIN, 'X-Request-ID': `ratelimit-${attempt}` },
        responseCallback: http.expectedStatuses(401, 429) })
    statuses.push(reply.status)
    if (reply.status === 429) {
      check(reply, { 'Retry-After is sent': r => r.headers['Retry-After'] === '900',
        'no account details in the refusal': r => !/ratelimit-probe/.test(r.body) })
    }
  }
  const firstRefusal = statuses.indexOf(429)
  check(statuses, {
    'wrong passwords are refused with 401 until the limit': s => s.slice(0, Math.max(firstRefusal, 0)).every(code => code === 401),
    'the address is throttled within 31 attempts': () => firstRefusal >= 0 && firstRefusal <= 30,
    'once throttled it stays throttled': s => firstRefusal >= 0 && s.slice(firstRefusal).every(code => code === 429),
  })
  console.log(`first 429 after ${firstRefusal} attempts; statuses: ${statuses.join(',')}`)
}
