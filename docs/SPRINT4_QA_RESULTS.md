# Sprint 4 local QA results — 2026-11-16

Feature branch: `mohammad_auth_activity_audit_sprint4`.
Base: dev `653760976d7f89a4dabf39fcc75bf362198b718f`.
Environment: Linux, Python 3.12, Node 24, Chromium, SQLite and disposable PostgreSQL 17.
No production deployment, flag change, identity verification or SES production-access request was made.

| Check | Result |
|---|---|
| Complete backend suite, clean run | **828 passed, 40 skipped** (PostgreSQL requires its opt-in environment) |
| All PostgreSQL-named checks with disposable DB | **41 passed** |
| Final reset/audit regression after shared-logger attribution change | **53 passed** |
| PostgreSQL reset/session revocation after that change | **1 passed** |
| Frontend unit/DOM interaction suite | **259 passed** |
| Real browser end-to-end suite | **10 passed** |
| ESLint / TypeScript / Vite production build | **Passed** |
| Git whitespace check | **Passed** |
| Manual workspace preview app/API readiness/local inbox | **HTTP 200** on 5173 / 8101 / 8025 |
| Admin activity on 390px viewport | **No horizontal overflow**, screenshot inspected |

Backend coverage includes owner/admin/suspension checks, JWT and refresh/session behavior, OTP expiry,
single-use grants, provider failure redaction, Google-only recovery with MFA preserved, TOTP/backup
replays, challenge expiry/guess limits/cancellation, backup replacement, Google subject proof, feature
rollback enforcement, migrations/legacy adoption, erasure/export and document storage audit hooks.
PostgreSQL concurrency proves two attempts to consume the same MFA challenge/backup yield one session.

Browser checks cover real enrollment → backup save → password-only challenge → backup sign-in →
disable, Google-only recovery through the local inbox, admin activity navigation/Beirut filters/mobile
layout, passport access history, application creation, session reload/erasure, admin grants and policy pages.
Google provider verification is mocked in backend tests; no synthetic Google account bypass is shipped.

During overlapping resource-heavy runs, one SQLite CLI concurrency test hit its existing five-second
lock timeout and the long passport browser flow hit its previous 30-second total timeout. The CLI group
passed separately and the later complete backend run passed. Browser startup now waits for API readiness,
and total browser timeout is 60 seconds; the complete final browser run passed. Generated Playwright
folders are excluded from ESLint so concurrent test cleanup cannot break directory traversal.

Nonfatal tool output: existing Starlette/httpx deprecation notice and Vite's bundle-size advisory.
Private execution logs/screenshots remain in ignored `work/qa/`, never committed with credentials/data.

## Live reset delivery: partial evidence, acceptance still pending

At **2026-11-16 12:14:25 UTC**, one reset request for the owner's supplied address on the AWS site
returned **202**, request ID `09ef75e69fa54ecdadbbdefcbc145830`. The generic response retained the
deployed Google-only exclusion policy. A public generic response cannot establish the account type,
SES send outcome, recipient verification or inbox arrival. The owner was asked to check receipt without
sharing the code. No password was changed.

No AWS identity is connected to this workspace. Fresh server-log/SES account checks and the two other
owned live account cases remain **unverified**. Historical sandbox evidence is documented separately;
it is not presented as current AWS state. Follow `SPRINT4_QA.md` to correlate the request and finish that
acceptance check. Local delivery capture and simulated SES rejection tests do not prove Gmail delivery.

## Review/start

See [SPRINT4_QA.md](SPRINT4_QA.md) for isolated Windows PowerShell/Linux startup commands, demo accounts,
manual acceptance steps, flags and read-only AWS diagnostics. Cloud workspace localhost and Windows
localhost are different machines. This environment provides no desktop/port-forwarding tool, so Windows
QA requires running that separate checkout locally (or using a workspace port viewer when available).
