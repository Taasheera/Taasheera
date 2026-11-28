# Taasheera monitoring runbook

Sprint 5 infrastructure monitoring (owner: Mohammad Sharafeddin). Four places to look, all
free-tier, none holding traveler data:

| Where | What it answers |
|---|---|
| `/backoffice` → **System health** (admin) | Is the app healthy right now? Live API rate/p50/p95/p99/errors, DB latency and size, CPU, memory, disks, agent queue, email backlog, payments, storage, release. |
| **Sentry** (org `taasheera`) | What broke, where, since which release? Errors, traces, Web Vitals, job check-ins, uptime, alerts. |
| **CloudWatch** dashboard `<stack>-health` | Host CPU, **CPU credits**, network, data-disk I/O, CloudFront requests and 4xx/5xx; four email alarms. |
| Container logs on the host | One JSON line per request with `request_id`, route template, status, `duration_ms`, `db_ms`, `db_queries` and Sentry `trace_id`. |

## Sentry

Projects: `taasheera-api` (Python: API, agent worker and notification jobs, told apart by the
`component` tag) and `taasheera-web` (browser). Configuration is by environment variable; no DSN
means no reporting, and tests never send anything.

| Variable | Where | Meaning |
|---|---|---|
| `SENTRY_DSN` | host `app.env` | API/worker project DSN. |
| `SENTRY_TRACES_SAMPLE_RATE` | host `app.env` | 0–1, default 0 (tracing off). Production 0.2, staging 1 (lower it during load tests). |
| `DEPLOY_ENVIRONMENT` | host `app.env` | `staging` or `production`; becomes the Sentry environment. |
| `SENTRY_RELEASE` | baked into the API image | `taasheera@<commit>`; set by `publish_images.py` and host builds. |
| `SENTRY_FRONTEND_DSN`, `SENTRY_FRONTEND_TRACES_SAMPLE_RATE` | host `app.env` (host builds) or repository variables (main releases) | Browser DSN (public by design) and sample rate. |

Privacy is by allowlist, not by scrubbing: errors carry only a fixed message plus `component`,
`route` (template), `job`, `request_id` and the trace ID. Traces carry route templates
(`/applications/{application_id}`), timings, response status, SQL text with `?` placeholders and Web
Vitals. Requests, headers, cookies, users, URLs, query strings, breadcrumbs, exception messages,
stack variables and SQL values are never sent. Unmatched paths become `unmatched route`. Browser
transactions replace IDs in paths with `:id`. `backend/tests/test_infra_monitoring.py` and
`frontend/tests/privacy-monitoring.test.mjs` enforce this.

**Releases**: every event and trace carries `taasheera@<commit>`, so a regression shows the release
that introduced it. **Jobs**: the notification timer checks in to the `notification-jobs` cron monitor
on every run; two failed or missing check-ins open an issue. The agent worker reports crashes with
`component=worker`.

**Alerts** (configured in Sentry, sent to mhs86@mail.aub.edu): a new issue in production; more than 10
errors in 5 minutes; p95 transaction duration above 2 s for 10 minutes; the uptime monitor on
`/health/ready` failing; the `notification-jobs` cron monitor missing or failing.

## Request logs

`taasheera.requests` writes one JSON line per request to stderr (Docker keeps 2 × 5 MB per container):
`{"request_id","method","route","status","duration_ms","db_ms","db_queries","trace_id"}`. A caller may
send its own `X-Request-ID` (8–64 letters, digits, hyphens; load tests do) and gets it back; anything
else is replaced. Find a request: `docker compose -f deploy/aws/compose.yaml logs api | grep <request_id>`.
Never enable raw request body, authorization-header, cookie or query-string logging. Security events
(`taasheera.security`) use the same request ID.

## System health page

`GET /admin/system-health` (admin only, `no-store`), shown on `/backoffice` and refreshed every 15 s.
Request numbers are per API process (health probes excluded) over 1, 5 and 15 minutes; CPU, memory
and load are the host's (read from `/proc`); disks are the data volume and the root volume. Checks
turn **warning** at 1% errors or p95 above 2 s, DB probe above 200 ms, memory above 85%, disks
above 85%, a form-filling run waiting over 10 minutes, emails waiting over an hour or failing, or a
payment pending over 30 minutes; **failing** at 5% errors, unreachable DB, memory or disk above 95%,
or unwritable storage.

## CloudWatch (host and edge)

`bash deploy/aws/monitoring.sh taasheera` (and `taasheera-staging`) creates the `<stack>-health`
dashboard and four alarms that email the owner: CPU above 80% for 15 minutes, **CPU credit balance
below 20**, status check failing, CloudFront 5xx above 5% for 10 minutes. Confirm the SNS
subscription email once. Free metrics only (no agent, no detailed monitoring): 5-minute EC2 data.
A t3 host with standard credits that runs out of credits drops to its baseline CPU share, so the
credit alarm is the earliest capacity warning.

## Uptime

Sentry uptime monitor on `https://<site>/health/ready` (every minute, from several regions). The
GitHub Actions check stays available: repository variable `UPTIME_URL` and secret
`UPTIME_ALERT_WEBHOOK` enable `.github/workflows/uptime.yml` (three probes, ten-second timeout, alert
without URL or body). Local probe:

```bash
UPTIME_URL=http://127.0.0.1:8000/health/ready python backend/scripts/monitor_uptime.py
```

## Incident response

1. `/backoffice` System health: which check is failing; the release; in-flight requests.
2. Sentry: newest issues for that release (`component`, `route`), the slow transaction's spans.
3. CloudWatch: CPU credits and host CPU; CloudFront 5xx.
4. Logs: the request ID from the error or the user's report.

Check database connectivity/current Alembic revision, free disk space, file permissions, Tesseract and
recent releases. Do not change JWT secrets or cookie security to troubleshoot availability. If
erasures are pending, correct storage/DB access and run `python retry_erasures.py` in the backend
container with the existing database/JWT environment and explicit `PASSPORT_STORAGE_DIR` and
`ACTIVITY_LOG_DIR`. Run this retry on an operator schedule as well as startup; repeated failure exits
nonzero. An account remains suspended until cleanup completes. The local equivalent is
`python preview.py retry-erasures`. All workers sharing local files must share their activity-parent
`.account-locks` directory; a future multi-host/S3 storage adapter needs distributed locking/erasure
coordination before enabling uploads.

**Admin view (older panels):** `GET /admin/monitoring` returns application counts, latest backend failure
metadata, agent problems and pending erasure count. Activity is authoritative in `activityevent`;
private JSONL mirrors live under `$ACTIVITY_LOG_DIR/<traveler_id>/YYYY-MM-DD.jsonl`. Passport access
records live in `documentaccess`; travelers see only their own entries at `/account`.

**Retention and release:** deletion removes current application DB rows and managed local owner
folders. Historical infrastructure logs and existing private backups follow the operator's retention
policy. Never restore an erased account into active service.

Synthetic local delivery test: export DSNs and start preview with `--enable-monitoring`; trigger the
unavailable assistant with specimen data for a backend 503; a browser `error` event exercises frontend
reporting. Confirm the event contains metadata only.
