# Deploy memory guard, migration timeouts, rollback (HA-MEM-DEPLOY-GUARD)

Status: in progress (2026-10-14). Track: small fix (no migration, no auth/RBAC change).
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
5. `/health` body gains Redis and scheduler state (status code unchanged); new `/health/deep`
   that returns 503 when DB or Redis is down, for monitors only.
6. Runbook `documentation/runbooks/memory-and-deploy-guards.md`.

## Progress

- 2026-10-14: plan written, lane opened.
