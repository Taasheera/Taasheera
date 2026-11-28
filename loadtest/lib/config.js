// Settings shared by every load test, from environment variables (k6 -e NAME=value or the shell).
// Synthetic staging accounts only: `python manage.py seed-load-test` creates them on the staging host.

export const BASE_URL = (__ENV.BASE_URL || 'http://127.0.0.1:8000').replace(/\/$/, '')
// check_origin() refuses writes without the site's own Origin header.
export const ORIGIN = __ENV.ORIGIN || BASE_URL
export const USER_PREFIX = __ENV.USER_PREFIX || 'loadtest'
export const USER_DOMAIN = __ENV.USER_DOMAIN || 'example.com'
export const USER_COUNT = Number(__ENV.USER_COUNT || 300)
export const PASSWORD = __ENV.LOADTEST_PASSWORD || ''
export const RUN_ID = (__ENV.RUN_ID || `run${Date.now().toString(36)}`).replace(/[^A-Za-z0-9-]/g, '').slice(0, 20)
// Seconds a simulated person pauses between actions; 0 turns pauses off (throughput tests).
export const THINK = Number(__ENV.THINK_SECONDS ?? 3)

// Share of iterations per area. Mirrors the four Sprint 5 areas so one run covers the whole app:
// public pages, sign-in and account (Hariri), the traveler journey (Dani), passport OCR,
// assistant and agent (Zeid). Heavy work is rarer, as it is for real travelers.
export const MIX = {
  browse: Number(__ENV.MIX_BROWSE ?? 30),
  account: Number(__ENV.MIX_ACCOUNT ?? 25),
  journey: Number(__ENV.MIX_JOURNEY ?? 35),
  documents: Number(__ENV.MIX_DOCUMENTS ?? 6),
  heavy: Number(__ENV.MIX_HEAVY ?? 4),
}

// Pass/fail targets for a capacity step (also used as k6 thresholds).
export const SLO = {
  p95Ms: Number(__ENV.SLO_P95_MS || 1500),
  serverErrorRate: Number(__ENV.SLO_ERROR_RATE || 0.01),
}

export function userEmail(index) {
  return `${USER_PREFIX}-${String(index).padStart(4, '0')}@${USER_DOMAIN}`
}
