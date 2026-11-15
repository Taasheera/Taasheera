# Branch protection for `dev` and `main` (CMPS271-232)

Goal: a pull request into `dev` or `main` cannot merge unless lint, tests and the build pass and one
reviewer approves, so a red build never reaches either branch. `main` is production.

Only a repository **admin** can change these settings. Team members with write access can check
them (see "Verify") but not switch them on. The repository owner applies them once.

## Required checks

Use the four jobs of the `CI` workflow (`.github/workflows/ci.yml`). They run on every push and
on every pull request into `dev` or `main`, whatever files changed:

| Check | What it covers |
|---|---|
| `backend` | API, OCR and migration tests (pytest), and PostgreSQL schema drift (`alembic check`) |
| `frontend` | Unit tests, lint and the production build |
| `browser` | Playwright browser tests against the real API, including `e2e/critical-path.spec.ts` |
| `container` | Backend images build, migrate and pass the readiness check |

Do **not** require `validate` (the "Validate AWS deployment configuration" workflow). It only runs
when deployment or app files change, so a docs-only pull request would wait for it forever.

## Settings (GitHub website)

Settings → Branches → Add branch protection rule, once for `dev` and once for `main`:

- Require a pull request before merging
  - Require approvals: **1**
  - Dismiss stale pull request approvals when new commits are pushed
- Require status checks to pass before merging
  - Require branches to be up to date before merging
  - Status checks: `backend`, `frontend`, `browser`, `container` (each appears in the search box
    after it has run once on a pull request)
- Do not allow bypassing the above settings (applies the rules to admins too)
- Leave "Allow force pushes" and "Allow deletions" **off**

## Same settings from the command line

An admin signed in with the GitHub CLI can apply both rules at once:

```bash
for branch in dev main; do
  gh api --method PUT "repos/mhs86/Taasheera/branches/$branch/protection" --input - <<'JSON'
{
  "required_status_checks": {
    "strict": true,
    "checks": [
      {"context": "backend", "app_id": 15368},
      {"context": "frontend", "app_id": 15368},
      {"context": "browser", "app_id": 15368},
      {"context": "container", "app_id": 15368}
    ]
  },
  "enforce_admins": true,
  "required_pull_request_reviews": {
    "required_approving_review_count": 1,
    "dismiss_stale_reviews": true
  },
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false
}
JSON
done
```

`app_id` 15368 is GitHub Actions, so only a check reported by our workflows can satisfy the rule.

## Verify

Anyone with access to the repository can confirm the rules are in place:

```bash
gh api repos/mhs86/Taasheera/branches/dev/protection --jq '{checks: [.required_status_checks.checks[].context], approvals: .required_pull_request_reviews.required_approving_review_count, admins: .enforce_admins.enabled}'
```

Expected: the four checks, `approvals: 1`, `admins: true`. Run the same for `main`. A `404 Branch
not protected` answer means the rule is missing.

Then open a pull request with a deliberately failing test: the merge button must stay disabled
until the check is fixed and a reviewer approves.

## Secrets

CI needs no repository secrets: the PostgreSQL password in `ci.yml` belongs to a throwaway service
container that exists only for the job. Deployments authenticate to AWS with OIDC (no stored AWS
keys); the role ARNs and uptime URL are GitHub repository variables and the uptime alert webhook is
a GitHub secret, never files in the repository (`deploy/aws/README.md`). The Gemini key, JWT
secret and mail settings live only in each deployment's private environment.

## Merging when a check is stuck

If a required check cannot run (for example GitHub Actions is down), wait or rerun it. Don't
temporarily remove the protection to merge: that is exactly the red build the rule exists to stop.
