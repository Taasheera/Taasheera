import http from 'k6/http'
import { check, fail } from 'k6'
import { Counter, Rate, Trend } from 'k6/metrics'
import { BASE_URL, ORIGIN, PASSWORD, RUN_ID, USER_COUNT, userEmail } from './config.js'

// The capacity verdict uses these rather than http_req_failed: a 409/422 from the questionnaire is
// the app working correctly, while 5xx, timeouts and dropped connections are capacity failures.
export const serverErrors = new Rate('server_errors')
export const throttled = new Counter('throttled_responses')
export const businessRejections = new Counter('business_rejections')
export const loginDuration = new Trend('login_duration', true)

const ACCEPTED = http.expectedStatuses({ min: 200, max: 399 }, 402, 404, 409, 422)
let sequence = 0

function requestId() {
  sequence += 1
  return `lt-${RUN_ID}-${__VU}-${__ITER}-${sequence}`.slice(0, 64)
}

function record(response) {
  const failed = response.status === 0 || response.status >= 500
  serverErrors.add(failed)
  if (response.status === 429) throttled.add(1)
  if ([402, 409, 422].includes(response.status)) businessRejections.add(1)
  return response
}

/** One person's signed-in browser: its own cookies, access token and sign-in. */
export class Visitor {
  constructor() {
    // Spread VUs over the seeded accounts; the same account in two VUs behaves like two devices.
    this.index = ((__VU - 1) % USER_COUNT) + 1
    this.email = userEmail(this.index)
    this.token = null
    this.tokenAt = 0
  }

  headers(extra = {}) {
    const headers = { 'X-Request-ID': requestId(), Origin: ORIGIN, ...extra }
    if (this.token) headers.Authorization = `Bearer ${this.token}`
    return headers
  }

  signIn() {
    if (!PASSWORD) fail('Set LOADTEST_PASSWORD to the synthetic accounts\' password.')
    const response = record(http.post(`${BASE_URL}/auth/login`, JSON.stringify({ email: this.email, password: PASSWORD }), {
      headers: this.headers({ 'Content-Type': 'application/json' }), tags: { name: 'POST /auth/login' } }))
    loginDuration.add(response.timings.duration)
    if (!check(response, { 'signed in': r => r.status === 200 })) return false
    this.token = response.json('access_token')
    this.tokenAt = Date.now()
    return true
  }

  /** Access tokens last 15 minutes; refresh a little early with the HttpOnly cookie, like the app. */
  ensureSession() {
    if (!this.token) return this.signIn()
    if (Date.now() - this.tokenAt > 12 * 60 * 1000) {
      const response = record(http.post(`${BASE_URL}/auth/refresh`, null, {
        headers: this.headers(), tags: { name: 'POST /auth/refresh' } }))
      if (response.status === 200) {
        this.token = response.json('access_token'); this.tokenAt = Date.now(); return true
      }
      this.token = null
      return this.signIn()
    }
    return true
  }

  request(method, path, body, name, params = {}) {
    const isForm = body && typeof body === 'object' && Object.values(body).some(value => value && value.filename)
    const headers = this.headers(body && !isForm && typeof body !== 'string' ? { 'Content-Type': 'application/json' } : {})
    const payload = body && !isForm && typeof body !== 'string' ? JSON.stringify(body) : body
    const response = record(http.request(method, `${BASE_URL}${path}`, payload ?? null, {
      headers, tags: { name: name || `${method} ${path}` }, responseCallback: ACCEPTED, timeout: '60s', ...params }))
    if (response.status === 401 && this.token) {
      // Revoked or expired: sign in again once, then let the next action carry on.
      this.token = null
      this.signIn()
    }
    return response
  }

  get(path, name, params) { return this.request('GET', path, null, name, params) }
  post(path, body, name, params) { return this.request('POST', path, body, name, params) }
  put(path, body, name, params) { return this.request('PUT', path, body, name, params) }
}

export function anonymousGet(path, name) {
  return record(http.get(`${BASE_URL}${path}`, {
    headers: { 'X-Request-ID': requestId() }, tags: { name: name || `GET ${path}` }, responseCallback: ACCEPTED }))
}
