// Whole-app mixed workload (Sprint 5 whole-app capacity test).
//
//   k6 run -e BASE_URL=https://<staging>.cloudfront.net -e LOADTEST_PASSWORD=... -e PROFILE=ramp loadtest/mixed.js
//
// Each virtual user (VU) is one person using the site with pauses between actions, so VUs are
// simultaneous users. PROFILE picks the shape:
//   smoke  2 users for 1 minute (does the script work?)
//   step   VUS users for DURATION (one level of the capacity loop in capacity.py)
//   ramp   0 → VUS over 10 minutes in five steps, then 2 minutes back down
//   spike  VUS/5 baseline, a jump to VUS for 2 minutes, then baseline again (recovery)
//   soak   VUS users for DURATION (default 30 minutes): leaks, disk, credits
import { SLO, MIX } from './lib/config.js'
import { Visitor } from './lib/client.js'
import { account, browse, documents, heavy, journey } from './lib/journeys.js'

const PROFILE = __ENV.PROFILE || 'smoke'
const VUS = Number(__ENV.VUS || 20)
const DURATION = __ENV.DURATION || (PROFILE === 'soak' ? '30m' : '3m')

function scenario() {
  switch (PROFILE) {
    case 'smoke': return { executor: 'constant-vus', vus: 2, duration: '1m' }
    case 'step': return { executor: 'constant-vus', vus: VUS, duration: DURATION }
    case 'soak': return { executor: 'constant-vus', vus: VUS, duration: DURATION }
    case 'ramp': return { executor: 'ramping-vus', startVUs: 0, gracefulRampDown: '30s', stages: [
      { duration: '2m', target: Math.ceil(VUS * 0.2) }, { duration: '2m', target: Math.ceil(VUS * 0.4) },
      { duration: '2m', target: Math.ceil(VUS * 0.6) }, { duration: '2m', target: Math.ceil(VUS * 0.8) },
      { duration: '2m', target: VUS }, { duration: '2m', target: 0 }] }
    case 'spike': return { executor: 'ramping-vus', startVUs: Math.ceil(VUS / 5), gracefulRampDown: '30s', stages: [
      { duration: '2m', target: Math.ceil(VUS / 5) }, { duration: '15s', target: VUS }, { duration: '2m', target: VUS },
      { duration: '15s', target: Math.ceil(VUS / 5) }, { duration: '3m', target: Math.ceil(VUS / 5) }] }
    default: throw new Error(`Unknown PROFILE ${PROFILE}`)
  }
}

export const options = {
  scenarios: { mixed: scenario() },
  // Requests are tagged with route templates, so per-endpoint numbers stay readable.
  systemTags: ['status', 'method', 'name', 'group', 'scenario', 'expected_response', 'error_code'],
  summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max', 'count'],
  thresholds: {
    server_errors: [`rate<${SLO.serverErrorRate}`],
    'http_req_duration{expected_response:true}': [`p(95)<${SLO.p95Ms}`],
    checks: ['rate>0.98'],
  },
  discardResponseBodies: false,
  noConnectionReuse: false,
  userAgent: 'taasheera-loadtest/1.0 (synthetic staging traffic)',
}

const visitors = {}
const AREAS = Object.entries(MIX).filter(([, weight]) => weight > 0)
const TOTAL = AREAS.reduce((sum, [, weight]) => sum + weight, 0)
const RUN = { browse, account: v => account(v), journey: v => journey(v), documents: v => documents(v), heavy: v => heavy(v) }

export default function () {
  const visitor = visitors[__VU] || (visitors[__VU] = new Visitor())
  let roll = Math.random() * TOTAL
  for (const [area, weight] of AREAS) {
    roll -= weight
    if (roll <= 0) return RUN[area](visitor)
  }
  return browse()
}

export function handleSummary(data) {
  const out = __ENV.SUMMARY_FILE
  const files = { stdout: textSummary(data) }
  if (out) files[out] = JSON.stringify(data, null, 1)
  return files
}

function textSummary(data) {
  const metric = (name, stat) => data.metrics[name]?.values?.[stat]
  const shape = PROFILE === 'smoke' ? 'vus=2 duration=1m' : `vus=${VUS} duration=${PROFILE === 'step' || PROFILE === 'soak' ? DURATION : 'staged'}`
  const lines = [`profile=${PROFILE} ${shape}`,
    `requests=${metric('http_reqs', 'count')} rps=${metric('http_reqs', 'rate')?.toFixed(2)}`,
    `p95=${metric('http_req_duration{expected_response:true}', 'p(95)')?.toFixed(0) ?? metric('http_req_duration', 'p(95)')?.toFixed(0)}ms`,
    `server_errors=${((metric('server_errors', 'rate') || 0) * 100).toFixed(2)}% throttled=${metric('throttled_responses', 'count') || 0}`,
    `checks=${((metric('checks', 'rate') || 0) * 100).toFixed(2)}%`]
  return lines.join('\n') + '\n'
}
