# Deploy memory guard, migration timeouts, rollback (HA-MEM-DEPLOY-GUARD)

Status: in review (2026-10-14). Track: small fix (no migration, no auth/RBAC change).
Lane: `claude/deploy-memory-guard-05ussl` (crew-lane HA-MEM-DEPLOY-GUARD)
UAC: `deploy-memory-guard-14oct-acceptance-criteria.md`

## Why

- 10 Sep: blue/green overlap pushed the 16 GB CRM box out of memory; with no swap the kernel
  killed a Postgres backend and the pipeline still reported success
  (`PLAN-deploy-memory-and-ci-speed.md`).
- 13 Oct: a slow migration held the deploy.
- 26 Sep (shared service): new code ran on the old schema after a failed migration.

## Slices

1. `scripts/blue_green_deploy.sh`: free-memory guard before the new colour starts
   (`DEPLOY_MIN_FREE_MB`, default 6000), and a Postgres restart check that fails the deploy
   when the db container's RestartCount or StartedAt changed during the deploy. Shell tests
   (`scripts/tests/test_blue_green_deploy.sh`) with a stubbed `docker`/`free`/`sudo`.
2. `alembic/env.py`: `lock_timeout` 5s and `statement_timeout` default on every migration
   connection, env-overridable, and per-migration override with `SET LOCAL`. Tests.
3. Pre-migration snapshot: `pg_dump` (custom format) via the db container before the new
   colour starts, kept 48 h under `backups/pre-migrate/`; failure stops the deploy.
4. `.github/workflows/rollback.yml`: `workflow_dispatch` input `sha`, `environment: production`,
   re-promotes `<svc>-<sha>` to the release tag and runs the same script with `IMAGE_TAG=<sha>`.
5. `/health` body gains Redis and scheduler state (status code unchanged, probes bounded at
   2s); new `/health/deep` that returns 503 when the DB, Redis or the scheduler
   (`get_overdue_tasks`, the dashboard's rule) is not OK, for monitors only.
7. (crew, HA-BACKUP-CHECK coordination) exclusive flock on `/opt/sorento-crm2/.deploy.lock`
   for the whole deploy; the weekly restore test waits on the same file.
6. Runbook `documentation/runbooks/memory-and-deploy-guards.md`.

## Progress

- 2026-10-14: plan written, lane opened.
- 2026-10-14: measured in a sandbox Postgres 16: killing one backend with SIGKILL makes the
  postmaster log `all server processes terminated; reinitializing` and crash-recover, while
  `pg_postmaster_start_time()` does not move and no container would restart. So the Postgres
  check reads the db log since the deploy start as well as RestartCount/StartedAt.
- 2026-10-14: slices 1-7 landed with red-first tests and kill proofs; runbook written.
- 2026-10-14: reviewer and security-reviewer READY 2108066f. Round 1 applied: DEPLOY_LOCK_PATH
  (crew, shared with HA-BACKUP-CHECK), .deploy.env before the lock, numeric knobs fail closed,
  disk check + PGDMP magic for the dump, SSH budget 100m, rollback regex/branch, /health
  without error text, 10s single-flight probe cache, runbook steps 0 and 6 (allowlist +
  limit_req).
- 2026-10-14: crew audit of 4a123689 (fails safe everywhere), fix round (a)-(g): dump the app's
  DB (DEPLOY_DB_NAME or the active backend's DATABASE_URL; the repo template's db container
  POSTGRES_DB differs from the backend's), require TABLE DATA public alembic_version in the
  dump's TOC (checked against a real pg_dump/pg_restore 16 in the sandbox), `--preflight`,
  lock wait 600s, rollback promotes after the server step, runbook step 9 (lock_timeout now
  fails a release that used to queue).
