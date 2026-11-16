# Sprint 4: authentication, activity and document audit

Branch: `mohammad_auth_activity_audit_sprint4`, extending dev commit
`653760976d7f89a4dabf39fcc75bf362198b718f`. No production release or configuration change is part of this work.

## Start locally

Use Python 3.12+ and Node 22.21+ (Node 24 recommended for the native test runner), and install Tesseract for OCR.
The preview never loads `.env.local` and uses its own database/files/secret under ignored `backend/storage/`.
It captures synthetic reset emails locally, never SES/Gmail. `--free-ports` stops only listeners on the three
chosen preview ports. Stop with Ctrl+C. Existing app configuration and databases are left alone.

Windows PowerShell, using a separate checkout so existing local edits are preserved:

```powershell
& {
  Set-Location 'C:\Users\Moka\Desktop\CMPS271\Taasheera' -ErrorAction Stop
  git fetch origin mohammad_auth_activity_audit_sprint4
  if ($LASTEXITCODE -ne 0) { throw 'Fetch failed' }
  git worktree add --detach '..\Taasheera-sprint4-qa' origin/mohammad_auth_activity_audit_sprint4
  if ($LASTEXITCODE -ne 0) { throw 'Worktree failed; use a new unused folder name' }
  Set-Location '..\Taasheera-sprint4-qa\frontend' -ErrorAction Stop
  npm ci
  if ($LASTEXITCODE -ne 0) { throw 'Frontend install failed' }
  Set-Location '..\backend'
  py -3 -m venv .venv
  if ($LASTEXITCODE -ne 0) { throw 'Install Python from python.org, then retry' }
  & .\.venv\Scripts\python.exe -m pip install -r requirements-dev.lock
  if ($LASTEXITCODE -ne 0) { throw 'Backend install failed' }
  & .\.venv\Scripts\python.exe preview.py run --seed-demo --mailbox --api-port 8101 --free-ports
}
```

Linux/macOS, from the feature checkout:

```bash
cd frontend
npm ci
cd ../backend
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.lock
.venv/bin/python preview.py run --seed-demo --mailbox --api-port 8101 --free-ports
```

App: http://127.0.0.1:5173/sign-in · local inbox: http://127.0.0.1:8025/ · API docs:
http://127.0.0.1:8101/docs. These are URLs on the machine running the preview; a cloud workspace
localhost is separate from your Windows localhost.

Synthetic accounts: `qa-admin@example.com`, `qa-traveler@example.com`, `qa-delete-me@example.com`.
Password: `Local-preview-only-123!`. `qa-google@example.com` starts Google-only with a synthetic
bound identity: test its email recovery using the local inbox; it cannot perform real Google sign-in.

## Manual acceptance

1. **CMPS271-233:** Forgot password for the traveler and Google-only demo. Read each code in the local
   inbox, verify it, set a password and sign in. Unknown addresses receive the same public response
   without a message. Wrong/expired/used codes fail; resending invalidates the old code. A reset signs
   out old sessions, preserves a Google link and still requires MFA if it was enabled.
2. **CMPS271-118:** Account → Two-factor sign-in starts Off. Confirm your password, scan the QR in an
   authenticator app (or enter its key), and confirm a current code. Save all ten backup codes privately.
   Other devices lose their sessions. Log out: password alone must show a second-factor prompt. Test a
   fresh authenticator code and a backup code; repeating a consumed code must fail. Generate new backups
   and turn off MFA with a fresh code. Enrolled Google sign-in also requires MFA (provider mocked only in
   backend tests). Enrollment cannot be completed with an unbound Google subject.
3. **CMPS271-234:** Admin → user table → View activity (also on user detail and /backoffice). Check newest
   first, actor, outcome, pagination, and From/To including seconds. Selections mean Beirut time regardless
   of your browser timezone. Both endpoints of the selected second are included; nonexistent DST times
   are rejected. A traveler/support/suspended admin cannot fetch the admin API. No passport fields appear.
4. **CMPS271-120:** Upload a specimen image, view it, confirm extracted fields, use passport prefill or ask
   the assistant (external Gemini is disabled in this preview). Account → Refresh access history shows
   each actual storage read, its purpose and actor. Admin history uses those same persisted events. Other
   users cannot see this history or load the image. Failed audit persistence blocks content access.
5. Recheck existing login/Google linking, refresh rotation, profile, sessions, application lifecycle,
   notifications, account erasure/data export and public policy pages. Mobile history should not overflow.

MFA secrets/challenges/backups remain in memory in the browser. Seeds are encrypted at rest, backup and
challenge values are hashed; no secrets are exported or logged. Already enrolled accounts continue to
require MFA even if the rollout flag is disabled. Securely preserve JWT_SECRET when backing up MFA data.

New flags (`activity_history`, `two_factor_signin`, `google_password_recovery`) default on in development
and off in production. No production flag was changed. Existing MFA management remains reachable on
flag rollback so an enrolled traveler can disable it safely.

## CMPS271-233: evidence and remaining AWS verification

Proven in the dev baseline before this patch: Google-only accounts are intentionally excluded by
`password_enabled`; they receive a generic acceptance but no email. The new flagged policy allows a
password only after verification of the already-bound provider email, keeping the same account/permissions.
The reset email contains a six-digit code, not a domain-built reset link; no link-domain defect was found.

Historical user-supplied AWS evidence (not a fresh check): `ProductionAccessEnabled: false`, one accepted
delivery for traveler 1, one failed delivery for traveler 6, and another accepted request without delivery.
The old failure logger does not identify the provider reason, so the individual recipient failure is not
proven to be sandbox rejection. This branch adds a fixed safe reason to delivery failures and tests an SES
unverified-identity rejection. It does not claim that code changes can remove SES sandbox restrictions.

**Live acceptance is pending.** This workspace has no AWS identity/credentials. The owner provided one
owned email; a single live forgot-password request at **2026-11-16 12:14:25 UTC** returned **202**, request ID
`09ef75e69fa54ecdadbbdefcbc145830`, with the existing Google-only exclusion message. Current account type,
inbox arrival and provider delivery outcome still require confirmation. No password was changed, SES identity
verified or production-access request submitted. The other two distinct account cases still need owned
test identities. Complete AWS URL reproduction from authenticated CloudShell, correlating the above ID.
Do not use real travelers' addresses for this check.

CloudShell read-only account check:

```bash
aws sesv2 get-account --region us-east-1 \
  --query '{SendingEnabled:SendingEnabled,ProductionAccessEnabled:ProductionAccessEnabled,Quota:SendQuota}'
read -r -p "Owned test recipient email: " taasheera_test_email
aws sesv2 get-email-identity --region us-east-1 --email-identity "$taasheera_test_email" \
  --query '{Verified:VerifiedForSendingStatus,Status:VerificationStatus}'
```

`ProductionAccessEnabled: false` means sandbox. A recipient identity lookup alone does not establish
whether the whole account is in sandbox; domain identities can also verify recipients. Preserve the exact
AWS error if a lookup fails. SES acceptance confirms provider acceptance, not inbox delivery.

For each owned live account, request reset in the browser on the AWS URL and record UTC time, account
type, HTTP status and `X-Request-ID` (Network tab), inbox/spam result and code verification success.
Never copy codes/passwords/reset grants into Jira or chat. Then inspect correlated server lines:

```bash
aws ssm start-session --region us-east-1 --target i-0a61d7aaf43052b29
```

On the host (read-only):

```bash
sudo docker logs --since 30m taasheera-api-1 2>&1 \
  | grep -E 'reset-request|reset-delivery|reset-verify' | tail -60
```

Record each request's delivery outcome and safe reason, if available in the deployed revision. An old
revision may have no reason field. Match request IDs before attributing failures. If sandbox plus an
unverified recipient is confirmed, verifying an owned test recipient or obtaining SES production access
is the infrastructure remedy, separate from this local-only branch. Do not deploy this branch to gather it.

## Automated checks

```bash
# backend
python -m pytest -q
# Optional: TEST_POSTGRES_URL must point to a disposable DB; tests create/drop schemas.
TEST_POSTGRES_URL=postgresql+psycopg://... python -m pytest -q tests/test_postgres_deployment.py tests/test_mfa_postgres.py
# frontend
npm test
npm run lint
npm run build
TAASHEERA_TEST_PYTHON=/path/to/python npm run test:e2e
```

Browser tests run a real isolated API/Vite/inbox on 8100/5273/8125. They do not deploy or contact SES.
See the committed tests for unit, feature, negative authorization, integration, migration, concurrency,
privacy/export/erasure and browser regression coverage. Execution results are recorded in `SPRINT4_QA_RESULTS.md`.
