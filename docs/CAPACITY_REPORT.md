# Whole-app capacity report (Sprint 5)

Owner: Mohammad Sharafeddin. Tooling and how to rerun: [`loadtest/README.md`](../loadtest/README.md).

**Result: production as it runs today (one t3.micro, one API process) supports 62 simultaneous active
users, 35 requests a second at p95 1.1 s with no server errors, once the two fixes in this report are
released; before them it supported 17. A t3.small with two API processes and SQLite's write-ahead log
supports 112 users (57 requests a second). In every configuration the final limit is the host's 2 vCPUs.**
Recommended next steps are in "What to do, in order" below.

## What was tested

| | |
|---|---|
| Environment | `taasheera-staging`: same CloudFormation template and containers as production: Docker Compose api + agent worker + nginx, SQLite on encrypted gp3, CloudFront HTTPS. Runs 1 and 3 on production's size, a **t3.micro** (2 vCPU, 1 GiB) with one API process; runs 2, 2b and 4 on a **t3.small** (2 vCPU, 2 GiB) with two. CPU credits *unlimited* so the host bursts like the long-idle production host; see "CPU credits" at the end. |
| Load generator | `c7i-flex.large` in the same region (us-east-1), k6 v2.4, so results measure the server, not a home connection. |
| Data | 300 synthetic accounts (`loadtest-NNNN@example.com`) with the privacy policy accepted, synthetic SPECIMEN passport image, 1 KB synthetic booking PDF. No real traveler data. |
| Workload | `loadtest/mixed.js`: each virtual user is one person with 3 s think time (±50%) between actions. 30% public pages, 25% sign-in and account pages, 35% traveler journey (dashboard, start or open an application, answer up to 6 questions, completeness check, timeline), 6% supporting documents (checklist, upload, packet), 4% heavy work (passport upload + Tesseract OCR, or the assistant). Every person signs in once and refreshes the 15-minute token with the cookie. |
| External services | Gemini is replaced by a staging-only stand-in that answers after 800 ms (`ASSISTANT_FAKE_LATENCY_MS`), so the assistant costs the API a worker thread like a real call without sending traffic to Google. Email notifications off (as in production). |
| Profiles | Step loop: 3 minutes per level, stop at the first failing level, one bisection. Then a spike to the first failing level (2 min baseline at 20%, 15 s ramp, 2 min at peak, 3 min recovery). |
| Targets | p95 of successful requests < 1.5 s; server errors (5xx, timeouts, dropped connections) < 1%; k6 checks ≥ 95%. A 409/422 from the questionnaire is the app working and is not an error. |
| Evidence | k6 per-request CSV (per-endpoint p95), `/admin-api/system-health` sampled every 15 s (host CPU, memory, in-flight requests, server-side and database p95), CloudWatch CPU and CPU credits, Sentry traces (5% sampled during the runs) and the API's JSON request logs (`X-Request-ID: lt-<run>-<vu>-<iteration>-<n>`). |

## Run 1: baseline (release `fadc62d`, t3.micro)

Run `20261127-1838`. Three-minute steps; the loop stopped at 25 users and bisected to 17.

| Users | Req/s | p50 | p95 | p99 | Sign-in p95 | 5xx / timeouts | Host CPU max | Memory used max | DB p95 max | Result |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 10 | 6.6 | 16 ms | 63 ms | 670 ms | 2.5 s | 0.00% | 48% | 74% | 335 ms | pass |
| 17 | 10.9 | 18 ms | 75 ms | 835 ms | 4.5 s | 0.00% | 69% | 83% | 25 ms | pass |
| 25 | 16.1 | 20 ms | 129 ms | 1.86 s | 5.3 s | 0.16% | 61% | 77% | 647 ms | **fail** |
| spike 5 → 25 → 5 | 7.6 | 18 ms | 86 ms | 657 ms | 1.4 s | 0.00% | 62% | 83% | 122 ms | recovered |

Slowest endpoints at 25 users (p50 / p95): passport OCR `POST /passports/:id/extract` 6.5 s / 6.8 s,
`POST /passports` 55 ms / 5.5 s, **sign-in `POST /auth/login` 2.4 s / 5.3 s with 5 server errors**,
document upload 33 ms / 1.4 s, assistant (800 ms stand-in) 842 ms / 886 ms. Everything else was under 300 ms at p95.

### First bottleneck: sign-in held the database write lock during password hashing

All 5 server errors were `POST /auth/login` answering 500 after 5.2 s with
`sqlite3.OperationalError: database is locked` (API logs, request IDs `lt-…`; Sentry issue "Taasheera
service error", route `/auth/login`). Login took the traveler row lock first (a no-op `UPDATE`, to
serialize with password reset) and only then ran bcrypt, about 0.25 s of CPU on this host and much more
while other requests compete for the two vCPUs. On SQLite a row lock is the whole database's write
lock, so every concurrent sign-in, and every other write, queued behind each hash; past SQLite's 5 s
busy timeout the waiting request failed. That is also why sign-in p95 climbed from 2.5 s at 10 users
to 5.3 s at 25 while the rest of the app stayed fast.

**Fix (commit `8c24e0a`):** check the password first, with no lock; then take the lock, re-read the
row and refuse if the hash changed in between (a reset that commits in between still wins; one that
commits later revokes the new session). Regression tests: `test_sign_in_checks_the_password_without_holding_the_database_write_lock`
(fails on the old code) and `test_a_password_reset_between_checking_and_signing_in_still_wins`; the 170
existing authentication tests pass.

### Other findings from the baseline

- **Passport OCR is CPU-bound and slow on this host** (6.5 s median). It runs Tesseract in the API's
  thread pool; the synthetic SPECIMEN image is a worst case because an unreadable MRZ makes the engine
  try every rotation and scale. Two concurrent extractions keep both vCPUs busy, which raises every other
  request's latency.
- **Memory is tight.** 1 GiB host; 74–83% used under load (API process up to 282 MB). A second API
  process does not fit safely next to the agent worker's browser (up to 640 MB).
- **Building images on the host while the app runs exhausts a t3.micro.** A staging redeploy that
  rebuilt the web bundle beside the running API and worker stalled the host (SSM unresponsive, site
  down) until it was resized. Production is not affected: main releases pull images built in GitHub
  Actions. Keep it that way; never use host builds on the 1 GiB production host.
- **CPU credits** decide how long the host can run at these loads; see "CPU credits" at the end.
- Rate limits: from an address that is not exempt, `loadtest/rate-limits.js` against staging got 401 for
  30 wrong passwords and 429 from the 31st, with `Retry-After: 900` and no account details in the refusal
  (11/11 checks). Only staging's load generator skips the limit, and only on staging.
- Staging operations: a stack update that changed the instance type tried to replace the host (the template
  resolves the latest Amazon Linux AMI on every update) and rolled back because the data volume was still
  attached to the old host. `deploy.sh` now refuses such a change set; resize in place with
  `ec2 modify-instance-attribute` (see `deploy/aws/README.md`). After recreating the api container, nginx
  must be restarted or it answers 502 (it keeps the old container address); the README now says so.

## Run 2: two API processes on a t3.small (release `ac0d5ea`, sign-in fix included)

Run `20261127-1927`. Staging resized to **t3.small** (2 vCPU, 2 GiB) with `WEB_CONCURRENCY=2`, to find
the next limit once the sign-in fix was in. The loop failed at 100 users and bisected to 87, which failed too.

| Users | Req/s | p50 | p95 | p99 | Sign-in p95 | 5xx / timeouts | Host CPU max | Memory used max | DB p95 max | Result |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 25 | 16.8 | 19 ms | 88 ms | 841 ms | 3.5 s | 0.00% | 94% | 47% | 30 ms | pass |
| 50 | 31.6 | 28 ms | 243 ms | 2.87 s | 8.5 s | 0.00% | 87% | 53% | 162 ms | pass |
| 75 | 43.6 | 51 ms | 848 ms | 4.94 s | 11.6 s | 0.00% | 93% | 61% | 339 ms | pass |
| 87 | 12.7 | 41 ms | 18.1 s | 60 s | 30 s | 11.57% | 66% | 62% | 99 ms | **fail** |
| 100 | 16.6 | 141 ms | 11.5 s | 45 s | 30 s | 9.63% | 80% | 62% | 215 ms | **fail** |
| spike 17 → 87 → 17 | 24.8 | 34 ms | 808 ms | 2.19 s | 4.0 s | 0.00% | 97% | 64% | 856 ms | recovered |

The sign-in fix held: no "database is locked" errors at any level, and 75 users ran at 43.6 req/s with
no errors, three times run 1's load. Sign-in p95 still grows with the level (11.6 s at 75) because every
step starts all its users at the same moment and each sign-in is about 0.25 s of bcrypt on a shared vCPU;
that queue is CPU, and it empties within the first seconds of a step.

### Second bottleneck: the database connection pool was smaller than the request threads

At 100 users the API logs had 141 `sqlalchemy.exc.TimeoutError: QueuePool limit of size 5 overflow 10
reached` (500) and 198 "Account is busy" refusals (503); requests took up to 133 s while their database
time stayed under 0.2 s, and CPU peaked at 66–80%. The sequence, from the 10-second timeline of the logs:

1. The step starts 100 users at once and each signs in. A sign-in keeps its pooled connection while bcrypt
   runs, and 100 hashes on 2 vCPUs take several seconds.
2. Each API process runs up to 40 requests at a time (AnyIO's thread pool) but SQLAlchemy's default pool
   holds only 15 connections (5 + 10 overflow). The other threads waited for a connection and gave up
   after 30 s with a 500.
3. k6 abandons a request after 60 s and the user goes on; the abandoned request still holds that account's
   lock (which serializes requests with account erasure), so the user's next request waited 15 s for it
   and got 503. Threads stuck this way slowed everything else, and the 87-user bisection inherited the backlog.

The spike, which brings users in over 15 seconds instead of all at once, passed at 87 users with no errors.

**Fix (commit `c8a093b`):** for file SQLite the pool now holds `API_THREADS` connections (default 40, the
thread pool's size) plus 10 for background jobs, reused last in, first out, so a request never waits for
a connection; SQLite itself orders the writes, as before. `API_THREADS` is read in one place for both
pools. Regression test: `test_every_endpoint_thread_gets_its_own_sqlite_connection` (fails on the old pool).

## Run 2b: the pool fix on the same t3.small (release `c8a093b`)

Run `20261127-1954`, same host and settings as run 2.

| Users | Req/s | p50 | p95 | p99 | Sign-in p95 | 5xx / timeouts | Host CPU max | Memory used max | DB p95 max | Result |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 75 | 43.4 | 53 ms | 784 ms | 5.24 s | 14.2 s | 0.14% | 100% | 59% | 4.5 s | pass |
| 87 | 46.6 | 93 ms | 1.54 s | 6.85 s | 15.4 s | 0.45% | 100% | 67% | 2.4 s | **fail** (p95) |
| 100 | 45.4 | 215 ms | 2.96 s | 7.87 s | 17.1 s | 0.48% | 99% | 66% | 2.6 s | **fail** (p95) |
| spike 17 → 87 → 17 | 24.5 | 35 ms | 996 ms | 2.87 s | 7.6 s | 0.00% | 97% | 68% | 1.6 s | recovered |

The cliff is gone. At 100 users run 2 collapsed to 16.6 req/s with 9.6% errors; with the fix the host keeps
serving 45 req/s with 0.5% errors and simply gets slower. It now fails on latency because **the host CPU is
saturated** (99–100%): this is the real ceiling of 2 vCPUs for this workload, about 45 requests a second.
The 87-user step missed the p95 target by 35 ms.

What is left of the errors is **SQLite's single writer**: sign-ins (35 of the 87-user step's 5xx) and a few
answer saves wait more than SQLite's 5 s busy timeout for the write lock ("database is locked"). With every
thread now holding a connection, more requests reach SQLite at once, and on a saturated CPU the request that
holds the write lock waits for a turn on the CPU while the others wait for it. The database p95 (time inside
SQL calls, which includes waiting for the lock) rose to 2–4.5 s. `SQLITE_JOURNAL_MODE=wal` addresses exactly
this (readers stop blocking the writer's commit); run 4 measures it.
The spike's 10 refusals (429) were the assistant's per-person message limit, working as designed.

## Run 3: both fixes on the production size (release `c8a093b`, t3.micro, one API process)

Run `20261127-2015`. Staging back on a **t3.micro** with one API process and the default rollback journal:
production's configuration. Compare with run 1.

| Users | Req/s | p50 | p95 | p99 | Sign-in p95 | 5xx / timeouts | Host CPU max | Memory used max | DB p95 max | Result |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 25 | 16.2 | 20 ms | 108 ms | 858 ms | 4.3 s | 0.00% | 100% | 73% | 436 ms | pass |
| 35 | 23.4 | 22 ms | 145 ms | 931 ms | 6.7 s | 0.00% | 72% | 82% | 71 ms | pass |
| 50 | 30.9 | 31 ms | 372 ms | 1.72 s | 9.7 s | 0.00% | 92% | 87% | 94 ms | pass |
| 62 | 35.0 | 47 ms | 1.08 s | 6.15 s | 10.9 s | 0.00% | 95% | 95% | 536 ms | pass |
| 75 | 37.6 | 145 ms | 1.63 s | 6.78 s | 9.6 s | 0.00% | 99% | 92% | 666 ms | **fail** (p95) |
| spike 15 → 75 → 15 | 19.9 | 48 ms | 1.26 s | 3.21 s | 3.9 s | 0.00% | 99% | 92% | 406 ms | recovered |

- **62 users instead of 17 on the same host**, with no server errors at any level, the spike included.
  Run 1 failed at 25 users; run 3 passed 25 at p95 108 ms.
- The limit is now **CPU** (99% at 75 users). One API process runs Python code on one core at a time;
  bcrypt and Tesseract run outside Python's lock and use the other. The slowest work at 75 users:
  sign-in p50 7.5 s (every user of the step signing in at once) and passport OCR p50 7.4 s.
- **Memory is the next limit.** 95% of the 1 GiB was in use at 62 users; the API process reached 394 MB
  (282 MB in run 1) and only 75 MB was free at the spike's peak. The workload does not start form-filling
  agent runs, whose browser needs about 430 MB (measured peak; 640 MB cap): a busy hour plus an agent run
  does not fit on a t3.micro. If production stays on a t3.micro, `API_THREADS=20` caps the API's threads
  and connections at half the default (not measured here).
- No "database is locked" errors: with one process, fewer requests reach SQLite at the same moment.

## Run 4: SQLite's write-ahead log on the t3.small (release `c8a093b`, `SQLITE_JOURNAL_MODE=wal`)

Run `20261127-2042`. Run 2b's host and settings (t3.small, two API processes) plus `SQLITE_JOURNAL_MODE=wal`,
which also sets `synchronous=NORMAL`. Compare with run 2b.

| Users | Req/s | p50 | p95 | p99 | Sign-in p95 | 5xx / timeouts | Host CPU max | Memory used max | DB p95 max | Result |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 75 | 44.7 | 41 ms | 508 ms | 3.40 s | 13.0 s | 0.01% | 100% | 55% | 3.0 s | pass |
| 87 | 47.6 | 108 ms | 1.19 s | 3.77 s | 11.8 s | 0.00% | 100% | 63% | 146 ms | pass |
| 100 | 51.7 | 198 ms | 1.33 s | 5.15 s | 12.5 s | 0.00% | 100% | 65% | 152 ms | pass |
| 112 | 56.8 | 160 ms | 1.45 s | 7.91 s | 15.1 s | 0.00% | 100% | 68% | 170 ms | pass |
| 125 | 56.6 | 401 ms | 1.75 s | 8.70 s | 17.8 s | 0.11% | 100% | 67% | 164 ms | **fail** (p95) |
| spike 25 → 125 → 25 | 30.9 | 82 ms | 1.52 s | 3.48 s | 6.7 s | 0.00% | 100% | 69% | 160 ms | slow at the peak, no errors |

With the write-ahead log, readers no longer hold up a writer's commit, so the lock waits behind run 2b's
errors go away: server errors fell from 0.14–0.48% to 0.00–0.01% at 75–112 users, and database p95 from
2.4–4.5 s to about 0.15 s. Only at 125 users, past the limit, did 12 sign-ins fail in the step's opening
burst; those log lines had already rotated out (Docker keeps 2 × 5 MB per container, about 20 minutes at
this load), and the lines kept from there on have no 5xx at all. The same 2 vCPUs also serve 57 requests a
second instead of 45: a commit appends to the log instead of syncing the journal and the database file to
disk, and fewer requests sit waiting for the lock. **112 users instead of 75.** The limit is again CPU.

`synchronous=NORMAL` means a power loss or kernel crash can lose the last few committed transactions
(never corrupt the database); an API crash loses nothing. The backup and restore tooling works with it: a
snapshot taken through SQLite's backup API while the API holds the database open includes the rows still in
the `-wal` file, a snapshot after it closed reads cleanly (checked with `backup_tools.snapshot_database` and
`check_database`), and restore already keeps and removes `-wal` and `-shm` before swapping the file in.

## Whole-app capacity

| Configuration | Simultaneous active users | Req/s | p95 at that level | What stops it |
|---|---:|---:|---:|---|
| Run 1: t3.micro, one API process, before the fixes | 17 | 10.9 | 75 ms | sign-in held the database write lock |
| **Run 3: t3.micro, one API process, both fixes** (production once this merges) | **62** | 35.0 | 1.08 s | CPU, then memory |
| Run 2b: t3.small, two API processes, both fixes | 75 | 43.4 | 784 ms | CPU; sign-ins time out waiting for SQLite's write lock |
| **Run 4: t3.small, two API processes, both fixes, write-ahead log** | **112** | 56.8 | 1.45 s | CPU |

An "active user" is a person clicking through the app every 3 seconds on average, with the mix described
above. Real travelers pause far longer to read and type, so the number of people who merely have the site
open at once can be several times these figures. Every step also signs all its users in at the same moment,
a harsher start than real traffic; the spikes, which bring users in over 15 seconds, show sign-in p95 of
4–8 s against 10–17 s in the steps.

### What to do, in order

1. **Merge this PR.** The two fixes take production from 17 to 62 users on the same host, at no cost.
2. **After it is released, set `SQLITE_JOURNAL_MODE=wal` in production's private `app.env`** and recreate the
   api (then restart web). It removed the remaining lock errors and raised the t3.small from 75 to 112 users;
   on the t3.micro it was not measured separately, but its database p95 of 0.5–0.7 s at 62–75 users is the
   waiting it removes.
3. **When real traffic nears about 50 active users, or agent runs overlap busy periods, move production to a
   t3.small with `WEB_CONCURRENCY=2`.** A t3.small has the same 2 vCPUs as a t3.micro; the gain comes from its
   2 GiB, which leaves room for a second API process next to the agent worker's browser. It is eligible for
   the AWS free plan. Resize in place (stop, `modify-instance-attribute`, start: about two minutes down; see
   `deploy/aws/README.md`), never through a stack update.
4. **Watch CPU credits** (next section) and the `taasheera-cpu-credits-low` alarm.
5. **Code follow-ups, not done here:** run passport OCR in the agent worker one at a time instead of in the
   API's threads, so a burst of uploads cannot take the CPU every other request needs (OCR p50 was 7 s
   under load); keep bcrypt's cost (about 0.25 s per sign-in is the price of the password protection).
   Beyond about 110 active users the next step is not a bigger t3: it is PostgreSQL and a second API host,
   because SQLite on one EBS volume ties the app to one machine.

### CPU credits

The tests ran on *unlimited* credits so that results show what the hardware can do. Production uses
*standard* credits: a t3.micro earns 12 credits an hour (288 at most) and is held to 10% of each vCPU
when they run out; a t3.small earns 24 (576 at most) and is held to 20%. At 62 users the t3.micro used about
95% of both vCPUs, 1.9 credits a minute against 0.2 earned, so a full balance lasts about 2.8 hours of that
load; after that the host gets about a tenth of the CPU these figures need. A t3.small at the same load lasts
about 6.4 hours. Production's balance was full (288) during these tests.
For a launch or a demo day, `unlimited` removes the cliff for $0.05 per vCPU-hour above the baseline,
about $0.09 an hour at full load. Switch the running host with `aws ec2 modify-instance-credit-specification`
(no restart); the stack's `CpuCredits` parameter does the same for new hosts, but a stack update would also
try to replace the host (see run 1's findings). All the test runs on 27 November together used about 100
surplus credits, roughly $0.08.

Staging was left stopped (disks only) as a t3.small with the write-ahead log and two API processes; its stack
parameter still says t3.micro. Start it with `bash loadtest/staging.sh start`.

