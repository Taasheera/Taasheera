# Load and capacity tests (Sprint 5)

Whole-app capacity test: how many people can use Ta'asheera at once before it gets slow or starts
failing, what runs out first, and what to change. Results and the bottleneck analysis are in
[`docs/CAPACITY_REPORT.md`](../docs/CAPACITY_REPORT.md).

**Only against staging, only with synthetic accounts.** Never point these scripts at production.

## What a virtual user does

`mixed.js` makes each k6 virtual user behave like one person, with think time between actions (3 s
by default), so *virtual users = simultaneous users*. Each iteration picks one area:

| Area | Share | Requests |
|---|---:|---|
| browse | 30% | home page (and, 1 in 5, its script), feature flags, visa guide list and one guide |
| account | 25% | `/auth/me`, unread count, profile, notifications, sessions, email preferences; 1 in 5 saves the profile |
| journey | 35% | applications list; start one (25%) or open a draft; answer up to 6 questions; completeness check; timeline |
| documents | 6% | documents checklist, upload a 1 KB synthetic PDF, packet 1 time in 3 |
| heavy | 4% | passport upload + Tesseract OCR, or the assistant (staging stand-in with fixed latency) |

Everyone signs in once and refreshes the 15-minute access token with the cookie, as the app does.
Requests carry `X-Request-ID: lt-<run>-<vu>-<iteration>-<n>`, so any request can be found in the
API's logs and Sentry. A 409/422 from the questionnaire is the app working and is not an error;
**server errors** are 5xx responses, timeouts and dropped connections.

## Running it

1. Staging (`taasheera-staging`, same template and instance size as production):
   ```bash
   bash loadtest/staging.sh seed 300                       # LOADTEST_PASSWORD, LOADTEST_ADMIN_PASSWORD
   bash loadtest/staging.sh env THROTTLE_EXEMPT_IPS=<generator IP> ASSISTANT_FAKE_LATENCY_MS=800 SENTRY_TRACES_SAMPLE_RATE=0.05
   ```
2. Load generator in the same region, so the numbers measure the server:
   ```bash
   bash loadtest/generator.sh create
   bash loadtest/generator.sh run mohammad_monitoring_capacity_sprint5 -- \
     --base-url https://<staging>.cloudfront.net --instance-id <staging instance> --levels 10,25,50,75,100,150,200 --spike
   bash loadtest/generator.sh status        # until "Report:" appears
   bash loadtest/generator.sh fetch report.md
   bash loadtest/generator.sh delete
   ```
3. Smoke-test the scripts locally first: `k6 run -e BASE_URL=http://127.0.0.1:8100 -e LOADTEST_PASSWORD=... loadtest/mixed.js`.

`capacity.py` steps through the levels (3 minutes each), stops at the first level that misses a
target, bisects once, and writes `report.md`/`report.json`. Targets: p95 of successful requests below
1.5 s, server errors below 1%, checks above 98%. During each level it samples `/admin-api/system-health`
every 15 s (CPU, memory, in-flight requests, server-side and database p95) and afterwards reads
CloudWatch CPU and CPU credits.

Other profiles: `-e PROFILE=ramp|spike|soak -e VUS=<n>` (soak: 30 minutes). `rate-limits.js`, run from
a machine that is *not* exempt, proves sign-in throttling still answers 429 after 30 attempts.

## Staging-only switches

Each is refused at startup unless `DEPLOY_ENVIRONMENT=staging`:
`THROTTLE_EXEMPT_IPS` (the generator's own address skips sign-in limits, at most 256 addresses),
`ASSISTANT_FAKE_LATENCY_MS` (assistant answers after a fixed delay instead of calling Gemini) and
`manage.py seed-load-test` (synthetic `loadtest-NNNN@example.com` accounts plus a synthetic admin).
Scaling settings tried after the baseline, all off by default: `SQLITE_JOURNAL_MODE=wal`,
`WEB_CONCURRENCY` (API processes; one runs the notification timer), `API_THREADS`.
