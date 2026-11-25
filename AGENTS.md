# Ta'asheera (تأشيرة): shared project context

Coding agents read this first. Put personal preferences in gitignored `CLAUDE.local.md`.

You are the owner and long-term maintainer of this codebase, and its design is up to you

Keep project facts and decisions current in the same PR. Keep this file under about 200 lines.

## Product

Group 7 is building a web AI visa preparation agent inspired by Veeza AI. Travelers select a visa,
confirm passport fields, finish the guided intake, upload documents, check consistency and review a form.
The target also includes cover letters, payment, portal preparation, manual guidance and status tracking.

- Passport/document data is private; every data/file request checks the authenticated owner.
- Nothing is submitted without the traveler's explicit confirmation.
- Traveler-facing AI output carries a "not legal or immigration advice" notice.
- Gemini's free tier is for synthetic/specimen demos only: Google may use prompts for improvement
  and human review. Real traveler data requires a paid tier or another provider.

## Sprint 5 and workflow

Final sprint: **November 17–December 1, 2026**. Focus on a complete, reviewable visa
preparation workflow. Handle as much paperwork as each destination supports, with a clear handoff
for traveler actions. Verify work on `dev` before the production release.

| Member | Sprint 5 focus | Reviewer |
|---|---|---|
| Ahmad Zeid | Embassy forms, submission review, assistant, validation, deployment | Dani Salman |
| Dani Salman | Generated documents, print-ready packet, tracking, group applications | Mohammad Sharafeddin |
| Ahmad Al Hariri | Privacy, family profiles, notifications, Arabic, visa requirements | Ahmad Zeid |
| Mohammad Sharafeddin | Payments, support, admin, document security | Ahmad Al Hariri |

Coordinate shared interfaces with owners. Track scope in Jira. Ahmad Zeid leads AWS operations. GitHub is `Taasheera/Taasheera`.

- `main` is production; tested sprint work integrates on `dev` before release. Both branches require
  one approval and the four CI jobs in `docs/BRANCH_PROTECTION.md`. GitHub deletes merged feature branches.
- Branch from `dev` as `<name>_<feature>_sprint<N>` and PR back to `dev` with ticket keys and the assigned reviewer.
- Never commit secrets, `.env` files, local databases or real passport images; use synthetic data.
- **Alembic has one head.** Before merge, point a new migration at the current `dev` head.
- End other sessions through `revoke_other_sessions` in `backend/app/auth.py`. The suspended-user
  check belongs in the single current-traveler loader, not duplicate endpoint checks.

## Stack and layout

| Area | In use | Proposed / operational notes |
|---|---|---|
| Backend | Python, FastAPI router factories, SQLModel, Pydantic v2, uvicorn | Environment validated at startup in `app/config.py` |
| Auth | bcrypt, PyJWT access tokens in memory, rotating HttpOnly refresh cookies, google-auth | Roles/status read from the traveler row on every request |
| Database | SQLite on encrypted persistent AWS EBS; Alembic; psycopg driver | PostgreSQL tests need a disposable database; only tests use `create_all` |
| Files | Private owner-scoped local/EBS folders | Private S3-compatible passport storage proposed; never public/ephemeral |
| OCR | Tesseract/pytesseract plus our MRZ locator and checksum repair | Vision-model fallback proposed |
| Assistant | Backend-only official `google-genai` SDK, `gemini-3.5-flash-lite`, function calling | Provider behind adapters; no keys in browser |
| Frontend | React 19, TypeScript, Vite, React Router data router, sonner | TanStack Query proposed |
| Styling | Plain CSS/custom properties in `src/index.css`, DM Sans + Space Grotesk | No Tailwind; auth styles are scoped |
| Agent | Playwright headless Chromium in a separate `worker` container (same image as the API) | Browser starts per run; ~430 MB peak measured, 640 MB cap |
| Hosting | EC2 in `us-east-1`, Docker Compose API + worker + nginx, CloudFront HTTPS | Live: https://d3vh2ppjrlwxee.cloudfront.net; custom domain not configured |
| CI/deploy | GitHub Actions tests/builds, OIDC/ECR deploy-from-main workflow | AWS/GitHub setup required; immutable API/web images are pulled by digest; Render/Netlify/Lambda are alternatives |
| Audit/monitoring | `ActivityEvent`, private JSONL, document access log; Sentry/uptime tooling | Sentry/uptime opt in; metadata allowlists omit private data |
| Backups | Nightly systemd/S3 backup and restore tooling | Provision private bucket/host permissions; see `docs/BACKUPS.md` |

Layout: `backend/app/` (`applications/` pure engine/router and destination JSON questionnaires,
`documents/` uploads and cross-check, `agent/` form sites, filler, queue and worker), `backend/alembic/`,
`backend/tests/`, `backend/scripts/`, `backend/manage.py`, `backend/preview.py`, `backend/agent_worker.py`,
`backend/retry_erasures.py`, `frontend/src/`, `frontend/tests/`, `frontend/e2e/`, `deploy/aws/`, `docs/`.
Signed-in pages show one navigation panel, `SectionNav` (US 15): sections live in `frontend/src/sections.ts`
(the notification bell, `notifications/NotificationBell.tsx`, sits after them), and its Assistant button calls `openAssistant()`, which every
`AssistantPanel` listens for. It makes the sticky header about 60px taller; `sectionNav.css` adjusts offsets below it.

## Local commands and routing

From `backend/`: create `.venv`, install `requirements-dev.lock`, run `python dev.py setup` once,
then `python dev.py run` (migrates before API on port 8000). Agent runs need `python agent_worker.py` with the same
environment (once: `python -m playwright install chromium-headless-shell`). From `frontend/`: `npm ci`, `npm run dev`
(port 5173). Use **http://127.0.0.1:5173**; local auth rejects an unconfigured `localhost` origin.
Windows instructions: the backend/frontend READMEs. OCR requires Tesseract on `PATH`.

Assistant key: a `GEMINI_API_KEY` line in ignored `backend/.env.local` for `dev.py run`; deployments
supply environment secrets (Render names the key with `sync: false`). **Never commit it: this repo is
public.** Without a key, `/assistant/messages` returns 503 and other features remain available.

Checks: backend `python -m pytest -q`; frontend `npm test`, `npm run lint`, `npm run build`,
`npm run test:e2e` (install Playwright Chromium; set `TAASHEERA_TEST_PYTHON` if needed). `e2e/critical-path.spec.ts`
is the critical path (sign-in, passport, chat, dashboard); CI's browser job runs it on every push and PR.
`TEST_POSTGRES_URL` must name a disposable database with create/drop privileges. Browser tests use ports 8100/5273 and isolated synthetic storage.
Preview: `python preview.py run --seed-demo` uses ignored isolated storage and disables mail and Gemini.
See `docs/LOCAL_FEATURE_TESTING.md`. Only use `--free-ports` to intentionally stop those listeners.

API prefixes `/auth`, `/passports`, `/profile`, `/sessions`, `/activity`, `/assistant`, `/applications`,
`/features`, `/notifications` and `/admin-api` proxy through Vite/nginx/Netlify; `/admin-api` rewrites to backend `/admin`.
A new prefix must be added to `vite.config.ts`, `write-netlify-redirects.mjs`, `deploy/aws/nginx.conf`
and must not collide with page paths. Pages include `/apply`, `/dashboard`, `/dashboard/:id`,
`/passport/:uploadId`, `/account`, `/admin` and `/backoffice`.
Deployments run `python migrate.py upgrade`, `APP_ENV=production`, `SCHEMA_AUTO_CREATE=false`.
Operator role assignment: `python manage.py set-role --email <email> --role admin`.

## Conventions

- Match surrounding code; new backend features use router factories.
- Validate request bodies with Pydantic `extra="forbid"`; validate configuration at startup.
- Report application events through `backend/app/notifications.py` (US 28), in the same session as the change
  and before its commit: `notify_status_change(session, application, previous)` after setting a status, and
  `notify_agent_run(session, application, stopped_reason=None)` for the agent. Messages are fixed sentences
  (no passport or answer values); application deletion and account erasure delete them. Email copies wait on CMPS271-233.
- Never log prompts, document fields, credentials or sensitive error input. Main strips values from 422s.
- Keep owner checks on every data/file route. Use established helpers/libraries over duplicate code.
- A new table that refers to a traveler, application or session goes in `PERSONAL_DATA` (`backend/app/personal_data.py`)
  and in account erasure: the data download (US 87, `GET /auth/account/export`) reads that list, and
  `tests/test_data_export.py` fails until both include it. Secrets are listed in `omit`; left-out tables give a reason.
- Feature changes include meaningful tests; lint, tests and build pass before a PR merges.
- Every merge to `main` goes live, so unfinished work merges behind a feature flag (`backend/app/features.py`):
  add the name to `FLAGS` and to `FeatureName` in `frontend/src/features.ts`, guard its router with
  `dependencies=[Depends(require_feature("name"))]` (404 while off) and its UI with `<Feature name="name">`
  (pass `fallback={<NotFound />}` on a page route). Flags are on in development and off in production
  unless `FEATURE_<NAME>=true|false` is set; delete the flag once the feature is released.

## Decisions log

1. **Stack.** FastAPI/Python for API, OCR and assistant. React/Vite/TypeScript, plain CSS and sonner for web.
2. **Paths.** Real pages and 404/error boundaries. A same-origin proxy keeps auth cookies first-party.
3. **MRZ-first extraction.** Tesseract locates MRZ lines, retries rotations/scales and uses checksum repairs.
   Verified, needs_review and unreadable are normal outcomes. The traveler confirms each extracted field.
4. **Private uploads.** Apply rotation, strip metadata and re-encode JPEG under random owner-scoped IDs.
5. **Authentication.** Passport routes reuse the shared traveler loader. Local JWT secrets are ignored;
   the browser reads the public Google client ID from `/auth/config`.
6. **Activity audit.** Allowlisted IDs, authenticated owner and server time. Private JSONL mirrors the database.
7. **Production schema/builds.** Alembic migrations, pinned dependencies, non-root images and readiness checks.
8. **Alternate hosting.** Netlify proxies API prefixes; ephemeral Render disables uploads; see `docs/AWS_LAMBDA.md`.
9. **Assistant adapter.** One backend provider call per question, deterministic date arithmetic, 20-second
   timeout and one retry. Update `PRODUCT_FACTS` when capabilities change.
10. **Google linking.** Verified `sub` is identity; password accounts explicitly link with password confirmation.
11. **OTP recovery.** Ten-minute/five-attempt codes issue five-minute single-use reset grants; reset revokes
    sessions and cannot enable Google-only passwords. SES sandbox/owner setup: `docs/AUTH_OTP_AWS.md`.
12. **Password change (US 43).** Current password required; current session stays and all others are revoked.
13. **Traveler profile.** Separate owned row; `PUT` replaces fields, validates dates/codes/names and E.164 phone.
14. **Account page.** Profile, sign-in methods, password and sessions; country options use UN M49/ICAO codes.
15. **Sessions (US 119).** Owner-scoped list/revocation; device label and UTC timestamps; shared revocation helper.
16. **Roles (US 131).** Traveler/support/admin, active/suspended with CHECK constraints; no JWT role claims,
    seeded admin password or role sign-up input. Single loader enforces status; admin responses are `no-store`.
17. **Admin users (US 133).** Account data/counts only; ordered locks, actor recheck, no self-role/suspension;
    suspension revokes sessions, changes record audit events. No passport/profile fields in admin listings.
18. **Guided intake (US 9).** Pure deterministic engine, validated per-destination JSON, branching/date rules;
    server enforces order/reconciles every read/write. Answers are copies with provenance; AI never picks questions.
19. **Opening steps (US 4/12).** Destination/visa/group choose the questionnaire before creation and stay fixed.
20. **Pre-fill (US 10).** Server recomputes suggestions from the owner's confirmed passport/profile; strict
    confirmed fields are separate from loose OCR output. Editing an application never rewrites source data.
21. **Completeness (CMPS271-48/49/51/53/55).** The owner-scoped check and assistant share a deterministic report:
    missing items and eligibility stops block; identity and explicit booking-date conflicts warn. The UI links
    to questions/uploads. The percentage covers applicable answers and required uploads, never approval odds.
22. **Assistant actions (US 47/110).** Server loads answers/current step/reachable IDs/check report. Gemini
    proposes change/go-to/check; engine validates, traveler confirms, normal answer endpoint validates again.
    No submit tool; multi-field answers stay in the form. Archived applications permit checks but no changes.
23. **Chat history (US 111).** Successful question/reply saved atomically per owner/application (null outside one).
    Owner-scoped GET/DELETE `/assistant/history`; server supplies model history and ignores browser history.
    Account erasure removes all its chats before parent rows; application deletion removes only its own chats.
24. **Assistant anywhere (US 23).** Signed-in site, welcome/account and admin shells mount `GlobalAssistant`;
    passport/apply keep their own. Outside history reloads on first open; `general` has passport context only.
25. **Admin dashboard (US 132).** `/admin` pages call `/admin-api`; backend role/status checks protect data.
26. **Retryable account erasure.** Suspend/revoke/persist intent first, then remove files/rows: 204 complete,
    202 pending. Portable owner file locks serialize deletion with requests. Startup/retry command finish cleanup.
27. **Document audit/monitoring.** Storage hooks record content access before returning data; strict Sentry
    metadata allowlists and opt-in uptime checks. See `docs/MONITORING_RUNBOOK.md`.
28. **Public Google consent pages.** Standalone `/privacy.html`/`terms.html`, linked before JS startup;
    scoped publication tooling and rollback in `docs/GOOGLE_APP_DOMAIN.md`.
29. **Admin by email.** Lookup grants nothing; explicit promotion rechecks identity/status/locks. No allowlist
    or unverified invitations. Existing production release tooling preserves private config and backs up SQLite.
30. **Sequential host builds.** API then web, `COMPOSE_BAKE=false`; retry disconnected sessions once.
    Logs private, no Docker shutdown/prune recovery, startup uses `--no-build`.
31. **Application lifecycle (US 19/20/79).** Archive hides active lists and makes answers read-only until
    restored. Delete removes answers/chat, preserving passport uploads. Timeline derives from saved timestamps.
32. **Backups (US 126).** Online SQLite backup plus passports/activity logs (no private secrets), checksummed
    archive to private/versioned S3. Restore verifies first and preserves replaced data; `docs/BACKUPS.md`.
33. **Main release images (CMPS271-190).** Only current main with successful push CI can release, including
    manual runs. Actions publishes immutable SHA-tagged API/web images to private EC2-specific ECR repos;
    the host pulls digests, verifies revision labels, preserves secrets, backs up SQLite and migrates. Old
    manual dev/bootstrap still builds locally. An immutable pull with less than 2 GiB of Docker disk
    space clears unused build cache before pulling, keeping containers, images and volumes.
    The repo transfer changed the immutable OIDC owner ID.
    Update and verify the existing role stacks before releasing. See `deploy/aws/README.md`.
34. **Supporting documents (CMPS271-29/50).** Each questionnaire lists its documents with answer-driven `show_if`.
    Files sit in the owner's passport folder (erased with it); PDFs are kept as uploaded but only downloaded,
    images re-encoded. A pure cross-check compares name/birth date/passport number with document text; missing
    required uploads block and mismatches warn in the completeness check. Behind `supporting_documents`.
35. **Destinations.** Schengen, UK Standard Visitor, US B-2 and Canada visitor questionnaires; data files only.
36. **Form-filling agent (CMPS271-236/67/70).** API queues `agentrun` rows; `agent_worker.py` claims them, fills
    a site configured in `agent/site_configs/` and streams typed fields and screenshots by polling. Retries
    transient operations in the same browser and surfaces selector changes in admin monitoring. The local
    Schengen replica is the verified integration. It stops at CAPTCHA or the declaration. CMPS271-68 live
    CAPTCHA handoff is deferred with Ahmad Zeid’s approval. Behind `agent_form_filling`.
37. **Signed-in landing.** Signing in (or opening `/sign-in` while signed in) goes to `/dashboard`, or to a
    signed-in page named in `?return=` (`signedInHome`). `/account` uses the site shell and `SectionNav`.
    Staff see a Staff dashboard link on `/dashboard`.
38. **Feature flags (US 127).** One env-driven module read at startup: flags default on in development and
    off in production, overridden by strict `FEATURE_<NAME>=true|false`; other values stop startup. Public
    `no-store` `GET /features` feeds the frontend's single shared request. Off means 404 on the backend and
    nothing rendered on the frontend, including while loading or when flags can't be read (fail closed).
39. **Embassy decision (US 55).** Traveler-recorded SUBMITTED then APPROVED/REFUSED, each a notify call; the
    agent never submits. Recorded answers are frozen (not re-checked against today); one step back undoes.
40. **Duplicate (US 78).** `applications/records.py` copies only same-id, same-type answers into the chosen
    questionnaire/country, re-checked by the engine (dependent dates dropped too); accommodation/host answers are
    asked again (`COUNTRY_SPECIFIC`); provenance kept, original untouched.
41. **Export record (US 80).** Owner-only `GET /applications/{id}/export`: answers as shown, status and the shared
    `build_timeline` as `taasheera.application-record` v1 JSON; no-store, audited before the file leaves.
42. **Official PDF (CMPS271-64/65/66/124).** The versioned Schengen template and map in `applications/forms/`
    drive a side-by-side review. The server requires a current review and explicit warning acknowledgment
    when applicable. Download leaves the declaration for the traveler. Behind `embassy_pdf`.
43. **Scan quality and assistant.** Passport quality checks run before OCR behind `passport_scan_quality`.
    Application chat offers question explanations and supported browsers dictate into an editable draft.
    Policy citations resolve server-side to official guidance for the four supported visa types.
44. **Privacy consent (CMPS271-115).** `PrivacyConsent` keeps each accepted policy version and time. Passport and
    document uploads (POST/PUT) answer 403 with `X-Privacy-Consent: required` until the traveler accepts
    `PRIVACY_POLICY_VERSION` (`app/consent.py`, the date in `public/privacy.html`; bump both to ask everyone again).
    Signed-in pages show the prompt; demo seeds accept. Behind `privacy_consent`.
