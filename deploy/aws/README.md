# Taasheera on AWS

Owner/account email: **mhs86@mail.aub.edu**. Expected AWS account:
`632630731308`, region `us-east-1`. All changes in this directory and the
associated workflow are deployment configuration. Application source is unchanged.
Only configuration, infrastructure, CI/CD, and deployment files may be pushed to
`main`; functional changes belong on a feature branch and go through `dev`.

## Current deployment

The app is deployed at **https://d3vh2ppjrlwxee.cloudfront.net**. The authenticated
CloudShell setup completed successfully and returned public API readiness.
GitHub Actions independently verified HTTPS readiness, SPA routing, JavaScript
and CSS assets, unauthenticated profile/refresh responses, and login validation.
The deployment uses the existing application code with persistent SQLite on EBS.
Public account creation, real Google sign-in, Gemini responses, and SMTP delivery
were not exercised by these read-only live checks. Authentication and specimen
passport OCR/persistence were exercised in local production-container checks.
Production releases now use the tested `main` workflow described below.

## Organization transfer (November 18, 2026)

Canonical repository: `Taasheera/Taasheera`; organization owners: `mhs86` and `Ahmad-Zeid`.
The existing AWS account, EC2 stack, ECR repositories and production role remain in use.
`AWS_MAIN_DEPLOY_ROLE_ARN` points to
`arn:aws:iam::632630731308:role/taasheera-github-main-role-DeployRole-zusFtGBPYSBG`.
The `production` environment allows only the `main` branch, with no tag deployment rules.
The production OIDC subject is
`repo:Taasheera@357495043/Taasheera@1387089236:environment:production`
and the audience is `sts.amazonaws.com`. Keep the IDs in the subject and do not use a wildcard.
Host bootstrap updates existing Git origins to the canonical repository.
Main releases still require a successful push CI run for the exact current commit.

## What this deploys

A single EC2 host runs the existing FastAPI image, Tesseract, and an Nginx image
containing the Vite build. CloudFront provides a free AWS hostname and viewer
HTTPS, forwards cookies and authorization headers, and disables caching.
SQLite and private passport images are on a separate encrypted EBS volume;
the API's port is private. Administration uses Systems Manager, without SSH.
The origin accepts CloudFront addresses and requires a private origin header.
Traffic between CloudFront and EC2 uses HTTP; viewer traffic uses HTTPS.
This is a course/demo deployment, with one host and no HA. A daily backup to a
private S3 bucket, and how to restore it, are in `docs/BACKUPS.md`.
Use specimen data. If stronger transport protection or availability is required,
plan those infrastructure changes before collecting real traveler documents.

The Lambda workflow remains separate. This deployment supports uploads without
changing application storage code or using Lambda's temporary filesystem.

## Cost boundary

This is **not permanently free hosting**. AWS's current Free account plan ends
after six months or when credits run out. The supplied $100 credit balance must
be checked in Billing, including expiry and eligible services, before creating
resources. EC2, encrypted gp3 EBS, one Elastic IP/public IPv4 address, CloudFront
requests/transfer, and any snapshots can consume credits or incur charges on
a Paid plan. There is no NAT Gateway, load balancer, RDS instance, purchased
domain, or public container registry. The main-release workflow uses two private ECR repositories. CPU credits are standard, avoiding surplus-credit
charges. The 8 GiB root plus 8 GiB data disks are intentional; Docker builds on a
1 GiB host may need a larger instance and its associated cost.

Set an AWS Billing budget with notifications to **mhs86@mail.aub.edu** and
monitor credit expiry. Budget notifications do not enforce a spending cap.
Do not upgrade to a Paid plan or add paid-only services to make this work without
checking the user's $0 requirement. See [AWS Free Tier](https://aws.amazon.com/free/)
and [account-plan documentation](https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/free-tier.html).

## Initial deployment in AWS CloudShell

These are real AWS operations. Initial deployment and recovery succeeded through
the user's authenticated CloudShell. Use the AWS account registered with **mhs86@mail.aub.edu**; do not
create another account. First publish/merge these configuration files so the
host can fetch the exact Git commit. A clean Git checkout is required.

```bash
git clone https://github.com/Taasheera/Taasheera.git
cd Taasheera
export AWS_REGION=us-east-1
export AWS_FREE_ELIGIBILITY_CONFIRMED=yes
bash deploy/aws/deploy.sh
```

The first run prepares a CloudFormation change set without executing it.
The scripts disable AWS CLI paging and interactive prompts. If an older script
shows a `:` or `(END)` after the change list, press `q` to close the pager and let
that deployment continue; do not restart it while the stack is being created.
Review it in CloudFormation, then either execute that change set there and rerun
the script, or run:

```bash
export TAASHEERA_EXECUTE_CHANGE_SET=yes
bash deploy/aws/deploy.sh
```

The default VPC must have DNS hostnames enabled; the selected subnet must have
an Internet Gateway route. Set `TAASHEERA_VPC_ID` and `TAASHEERA_SUBNET_ID` if the
account has no suitable default network. Keep `.aws-deploy-state/` private: it
contains the origin token and SSM parameters, never Git credentials.
Read IAM and EC2 errors before retrying. The CloudFront origin prefix list counts
as 55 security-group rules, so its quota must accommodate it.

The script mounts only the stack's data volume, builds images sequentially,
migrates the database before starting the API, and checks the public readiness
URL. JWT signing material and the document encryption key are generated privately on the data
volume and retained across redeployments. No secret is committed or printed.
Setup reports its current step. On failure the scripts retrieve the SSM exit
code and error output, excluding the private command parameters.
Amazon Linux's Docker package does not guarantee a compatible Buildx plugin.
Bootstrap installs checksum-verified Buildx v0.31.0 if the existing plugin is
missing or older than Compose's minimum v0.17.0, retaining compatible installations.

If infrastructure creation succeeded and application setup failed, fix the
reported prerequisite, pull the published fix, and retry application setup only:

```bash
git pull --ff-only
bash deploy/aws/redeploy.sh
```

This reuses the existing host, mounted data volume, and private settings; it does
not run CloudFormation resource creation. The initial setup must have written
`/srv/taasheera/private/app.env` before this recovery command can read its origin token.

## Manual GitHub Actions redeployment

After initial deployment, create the OIDC role from CloudShell:

```bash
aws cloudformation deploy --stack-name taasheera-github-role \
  --template-file deploy/aws/github-role.yaml --capabilities CAPABILITY_IAM
aws cloudformation describe-stacks --stack-name taasheera-github-role \
  --query 'Stacks[0].Outputs' --output table
```

If the GitHub OIDC provider already exists, pass
`--parameter-overrides CreateOidcProvider=false`.
Set repository variable `AWS_FULL_DEPLOY_ROLE_ARN` to the resulting role ARN
(an ARN is not a credential). The role trusts only this repository's `dev`
branch and allows redeployment only to the tagged stack host. Run **Deploy dev to existing AWS host** manually after verifying remaining credits.
It creates no infrastructure and preserves private local configuration.

## Automatic deployment from main (CMPS271-190)

`.github/workflows/deploy-main-aws.yml` builds the API and web images in Actions for the latest
`main` commit whose push CI passed, publishes immutable SHA tags to private ECR, and deploys their
exact digests through `redeploy.sh`. The host pulls instead of building, verifies both image revision
labels, backs up SQLite, migrates and checks readiness. `TAASHEERA_PRESERVE_CONFIG=1` keeps its private
environment. When there is less than 2 GiB free on the Docker filesystem, the host reclaims unused
build cache before pulling, preserving containers, images and volumes. If a pull still runs out of
space, check the EC2 root volume capacity before retrying. A concurrency group shared with manual
dev deploys prevents overlap. A manual main
run must also have passing push CI; a superseded commit is refused before publication and again
before host deployment. Ahmad Zeid leads deployment/AWS operations from November 2, 2026.
The following one-time setup needs AWS IAM/CloudFormation and GitHub repository-settings permissions;
verify the access handover first. The repository is now owned by the Taasheera organization.
These steps are not run automatically:

1. In GitHub, create the `production` environment (Settings → Environments). Optionally add a
   required reviewer, which turns each deployment into a one-click approval.
2. From CloudShell, create a second role that trusts only that environment:

   ```bash
   aws cloudformation deploy --region us-east-1 --stack-name taasheera-github-main-role \
     --template-file deploy/aws/github-role.yaml --capabilities CAPABILITY_NAMED_IAM \
     --parameter-overrides CreateOidcProvider=false TrustedSubject=repo:Taasheera@357495043/Taasheera@1387089236:environment:production
   aws cloudformation describe-stacks --stack-name taasheera-github-main-role \
     --query 'Stacks[0].Outputs' --output table
   ```

3. Create the two private repositories and grant the existing host read-only image access:

   ```bash
   host_role=$(aws cloudformation describe-stack-resource --region us-east-1 --stack-name taasheera \
     --logical-resource-id HostRole --query 'StackResourceDetail.PhysicalResourceId' --output text)
   aws cloudformation deploy --region us-east-1 --stack-name taasheera-release-images \
     --template-file deploy/aws/image-repositories.yaml --capabilities CAPABILITY_NAMED_IAM \
     --parameter-overrides HostRoleName="$host_role"
   ```

   The production role can push only `taasheera-ec2-api`/`taasheera-ec2-web`; the host can only pull.
   These are separate from the alternative Lambda repository. Images are tagged with the full
   commit SHA and pinned by digest on deployment. Re-running a release reuses an existing tag.
   Repositories are retained on stack deletion; tagged releases remain available for operator rollback.

4. Set repository variable `AWS_MAIN_DEPLOY_ROLE_ARN` to the role ARN from step 2. Restrict the
   GitHub `production` environment's deployment branches to `main` so another branch cannot use it.

Until setup is complete the workflow fails before touching running services. AWS changes and a live
deployment still need operator execution. Manual dev/bootstrap without image variables retain the
existing sequential host-build path. Release image variables are not written to `app.env`; subsequent
image releases supply both again. Local `taasheera-api`/`taasheera-web` tags also point at the deployed
images so existing operator/backup commands keep using the current release after setup exits.
See [AWS's repository-scoped push permissions](https://docs.aws.amazon.com/AmazonECR/latest/userguide/image-push-iam.html).

## Staging copy for load tests (Sprint 5)

`taasheera-staging` is a second stack from the same template: its own EC2 host, data volume and
CloudFront URL (https://d3125pr28zp4zv.cloudfront.net), synthetic data only. Created with:

```bash
export AWS_FREE_ELIGIBILITY_CONFIRMED=yes TAASHEERA_STACK=taasheera-staging TAASHEERA_INSTANCE_TYPE=t3.micro \
  TAASHEERA_CPU_CREDITS=unlimited TAASHEERA_DEPLOY_ENVIRONMENT=staging \
  TAASHEERA_SENTRY_DSN=<api dsn> TAASHEERA_SENTRY_FRONTEND_DSN=<browser dsn> TAASHEERA_SENTRY_TRACES_SAMPLE_RATE=1
bash deploy/aws/deploy.sh            # prepares the change set; execute it, then run again
bash deploy/aws/monitoring.sh taasheera-staging
```

Its state lives in `.aws-deploy-state/taasheera-staging/`, apart from production's. `unlimited` CPU credits
let a new t3 host burst like the long-idle production host (a new t3 starts with no saved credits);
production stays `standard`. `bash loadtest/staging.sh stop` stops it between test sessions (disks and
the address still cost a little); `start` brings it back. Delete the stack, then its retained data volume,
when it is no longer needed. From Windows Git Bash, give the AWS CLI native paths: set
`TAASHEERA_STATE_DIR` and `TMPDIR` to `C:/...` folders.

**Resizing a host.** Do not change `InstanceType` through `deploy.sh`: the template resolves the latest
Amazon Linux AMI on every update, so an update after a new AMI release replaces the host, the new host
cannot attach the data volume the old one still holds, and the update rolls back (found on staging on
November 27; `deploy.sh` now refuses such a change set). Stop the instance, then
`aws ec2 modify-instance-attribute --instance-id <id> --instance-type '{"Value":"t3.small"}'` and start it;
the Elastic IP and CloudFront origin stay the same. The stack parameter then lags the real size until a
planned host replacement (back up, detach and reattach the data volume by hand).

**After changing `app.env`, recreate api and restart web.** nginx resolves the api container's address
when it starts; a recreated api container gets a new address and nginx answers 502 until restarted:
`docker compose -f /opt/taasheera/deploy/aws/compose.yaml up -d --no-build --force-recreate api && docker compose -f /opt/taasheera/deploy/aws/compose.yaml restart web`.

## Form-filling agent worker (CMPS271-236)

`compose.yaml` runs a `worker` container from the API image with `python agent_worker.py`. It shares the
API's environment file and storage volume, polls the `agentrun` table, and starts headless Chromium only
while a run is in progress, so idle it is a ~40 MB Python loop. Agent routes stay hidden until
`FEATURE_AGENT_FORM_FILLING=true` is set in `app.env`, so no runs reach it before then.

Memory, measured with `backend/scripts/measure_agent_memory.py` and inside the production image:
about 230 MB with the browser idle and 380–430 MB at peak while filling. `mem_limit: 640m` keeps a
runaway browser from starving the API. The current `t3.micro` (1 GB) holds the idle worker beside the
API and nginx, but **switch the stack to `t3.small` (2 GB) before enabling the flag** so a run and normal
API traffic fit together. Change `InstanceType` in the `taasheera` stack; this restarts the instance.

Check it on the host with `docker compose -f deploy/aws/compose.yaml logs --tail 50 worker`. Logs carry
run ids and statuses only, never form values.

## External features and checks

Google SSO and email OTP recovery use the configuration described in
[authentication operations](../../docs/AUTH_OTP_AWS.md). Google OAuth administration
and SES sending use `sharafm032@gmail.com` only; AWS/GitHub ownership stays
`mhs86@mail.aub.edu`. SES is sandboxed, so recipients must be verified until
production access is granted. Apply `configure-auth.sh` once from CloudShell
before deploying `dev`; no real provider delivery is claimed by local tests.

Gemini remains an external dependency. Enter its key privately in
`/srv/taasheera/private/app.env` if needed; use specimen data with the free tier.

After a private settings change, run `docker compose -f
/opt/taasheera/deploy/aws/compose.yaml up -d --force-recreate api` on the host, then
`docker compose -f /opt/taasheera/deploy/aws/compose.yaml restart web` (nginx keeps the old api address otherwise).
Check HTTPS home page, `/health/ready`, register/login/refresh/logout, upload a
specimen passport, review/save/read it, and confirm data survives a container
restart. Verify assistant and reset-email delivery separately when configured.

## Persistence and deletion

The data volume has Retain policies. Stack deletion deletes the host and releases
the Elastic IP but **does not delete the data volume**; retained EBS continues
to consume credits or incur charges. Back up and explicitly remove it when it is
no longer needed. Host replacement creates a new volume: manually reattach and
restore the retained volume before traffic resumes; no automatic recovery is
claimed. Migration rollback is manual; daily backups and restore follow
`docs/BACKUPS.md`.
Keep a backup before deploying schema changes. Container updates rerun migrations
and keep the same database, JWT, images, and reviews.

## Document encryption key (CMPS271-114)

Passport images and confirmed details, supporting documents and the text read from them, and the
agent's screenshots are encrypted by the application (AES-256-GCM) on top of the encrypted EBS volume.
The key is `DOCUMENT_ENCRYPTION_KEY` in `/srv/taasheera/private/app.env`: 32 random bytes, base64.
`bootstrap.sh` writes one when it first creates `app.env` on a new host and never regenerates it.
Releases (`TAASHEERA_PRESERVE_CONFIG=1`) keep `app.env` exactly as it is, so a host set up before this
change has no key until an operator adds one. Without a key the API logs `DOCUMENT_ENCRYPTION_KEY is not
set` once at startup and stores files as before; with one, new files are encrypted and older plain files
still open.

**Losing the key loses every encrypted document.** Nightly backups leave `private/` out on purpose, so a
backup can't bring the key back. Keep a copy apart from the data and its backups (the team password
manager, or a SecureString parameter only operators can read), and never commit it, print it in Run
Command output, or paste it into chat or tickets. Changing the key isn't supported: files encrypted with
the old key would stop opening.

Add the key to an existing host from an interactive Session Manager shell:

1. Back up first: `sudo systemctl start taasheera-backup.service`.
2. Add a key only if none is set. This prints nothing secret and refuses to replace a key:

   ```bash
   sudo python3 - <<'PY'
   import base64, secrets
   from pathlib import Path
   path = Path('/srv/taasheera/private/app.env')
   text = path.read_text()
   if any(line.startswith('DOCUMENT_ENCRYPTION_KEY=') for line in text.splitlines()):
       raise SystemExit('A document key is already set. Never replace it.')
   with path.open('a') as env:
       env.write(('' if text.endswith('\n') else '\n') + 'DOCUMENT_ENCRYPTION_KEY='
                 + base64.b64encode(secrets.token_bytes(32)).decode() + '\n')
   print('Document key added.')
   PY
   ```

3. Store the copy now: `sudo grep '^DOCUMENT_ENCRYPTION_KEY=' /srv/taasheera/private/app.env` shows
   it in that interactive session only.
4. Restart the API and the agent worker, which both read files:
   `sudo docker compose -f /opt/taasheera/deploy/aws/compose.yaml up -d --no-build --force-recreate api worker`.
   Upload a specimen passport and open it to check.
5. Encrypt the files stored before the key:
   `sudo docker compose -f /opt/taasheera/deploy/aws/compose.yaml run --rm api python manage.py encrypt-documents`.
   It takes each traveler's account lock, replaces each file atomically, keeps file times and prints
   counts only. Run it again if it reports busy accounts; a second run reports 0 encrypted. It exits
   with an error if an already encrypted file doesn't open with the key in `app.env`.

To restore onto a replacement host, put the same key in its `app.env` before starting the API.

## Upload malware scanning (CMPS271-31)

Every passport and supporting-document upload is scanned before anything parses or stores it
(`backend/app/upload_scan.py`, flag `malware_scan`, released; `FEATURE_MALWARE_SCAN=false` switches it
off). The built-in scanner always runs: the EICAR test signature, Windows/Linux/macOS executables, PDFs
with JavaScript, launch actions, embedded files or additional actions, and content that isn't the type
the upload declared. A rejected file gets a fixed 422 message, nothing is written, and an
`upload_rejected` activity event records only the reason code.

ClamAV is optional: set `CLAMD_ADDRESS=host:port` in `app.env` for a clamd that accepts TCP `INSTREAM`
(for example a `clamav/clamav` container on the compose network as `clamav:3310`). clamd needs about
1 GB of memory for its signatures, so it is **off on the t3.micro host** and the built-in scanner is the
default; run it only after moving to a larger instance. Once it is set, an unreachable or failing clamd
makes uploads answer 503 ("Upload scanner unavailable") rather than store a file nobody scanned.

## Release the production sign-in/admin fixes

Use `python3 deploy/aws/release_production.py --execute` from an authenticated,
clean checkout. This preserves the exact existing private configuration and
verifies the frontend, new API behavior and public policies. Optional
`--admin-email` promotes a designated existing account through the operator CLI.
See [the release and acceptance guide](../../docs/PRODUCTION_AUTH_ADMIN_FIX.md).
