# PLAN: verified backups (HA-BACKUP-CHECK)

Status: building (small fix track: ops shell scripts + runbook + shell tests; no migration, no app
code, no RBAC change). Reviewer + security-reviewer before ready.

## Problem

Root cron `0 3 * * *` on the CRM box runs `/opt/sorento-crm2/backup-db.sh` (not in git):
pg_dump, gzip, `gzip -t`, `rclone copy` to `gdrive:db-backups/`, delete the local copy, prune Drive
older than `RETENTION_DAYS`.

- No alarm: a broken rclone push or a dead cron leaves zero fresh backups and nobody hears.
- A restore has never been tested.
- The n8n box DB and the shared-service box DB are probably not covered.
- Encryption at rest on Drive: none known.
- RPO 24 h (unchanged by this lane; see "Not in scope").

## Scope

1. `scripts/ops/backup-db.sh`: version-controlled successor, same Drive target and behaviour, plus
   Healthchecks.io pings (`HC_BACKUP_URL`: `/start`, success, `/fail`), optional client-side
   encryption with `age` (`BACKUP_ENCRYPT=age`, default off), `set -euo pipefail`, `flock`
   against overlap, timestamped log lines, distinct exit codes.
2. `scripts/ops/restore-test.sh`: on the prod host, newest Drive dump into a throwaway
   `pgvector/pgvector:pg<major>` container (major read from the dump header, so it always matches
   the server that wrote it), sanity counts, `HC_RESTORE_URL` ping, container removed on every exit
   path. Never connects to the live DB; nothing leaves the box.
3. Same scripts serve the n8n and shared-service boxes through env (`BACKUP_NAME`, `DB_CONTAINER`,
   `RCLONE_DEST`, `SANITY_TABLES`); the runbook lists what to check on each box first.
4. `documentation/runbooks/backups.md`: install, first restore by hand, rollback, monthly drill.

## Decisions

- **One script, env per box**, not three copies (simplest thing): the only differences are the
  container, DB name, Drive folder and which tables the restore test counts.
- **Dump source:** `docker exec <DB_CONTAINER> pg_dump` when `DB_CONTAINER` is set (prod DBs run in
  Compose), else host `pg_dump` with libpq env. The owner compares this with the old script's
  pg_dump line before switching (runbook step 1).
- **Format stays plain SQL + gzip** (`.sql.gz`), as today, so old and new dumps restore the same way.
- **Encryption: `age`**, not rclone crypt. `age` encrypts before rclone sees the file, the public
  recipient is the only thing on the box, and the private key lives offline with the owner. An
  rclone crypt remote would need its password on the box, next to the Drive token. Default OFF.
- **Prod Postgres major:** not in git (the prod compose is hand-edited on the box). The repo
  compose says `postgres:15-alpine`, CI uses pg16, a plan note says prod is pg17. So the restore
  test reads the major from the dump's `-- Dumped from database version` line instead of trusting
  any of them, with `PG_MAJOR` as an override.
- **The restore test must lose every resource fight** (crew audit, 14 Oct; the 16 GB CRM box
  OOM-killed live Postgres on 10 Sep): container `--memory`/`--memory-swap` `RESTORE_MEMORY`
  (default 2g), `--oom-score-adj 1000`, `--cpus 1`; the restore stream under `nice -n 19 ionice -c3`;
  a disk preflight (4x the downloaded dump free on the work dir and the docker root, else exit 4);
  a shared `flock` on `/opt/sorento-crm2/.deploy.lock`, retried for up to 60 min, else exit 7.
  Lane HA-MEM-DEPLOY-GUARD makes `blue_green_deploy.sh` hold the same lock exclusively. The lock
  file is created 644 and opened read-only so a non-root deploy user can lock it too.
- Healthchecks pings never fail the backup: `curl -fsS -m 10 --retry 3 ... || true`.

## Not in scope

- RPO below 24 h (WAL archiving / PITR). Trigger: owner decides the 24 h loss window is too big.
- Moving the Drive account off the owner's personal Google account.

## Tests

`scripts/ops/tests/test_backup_scripts.py` (pytest, no network): fakes for `docker`, `pg_dump`,
`rclone`, `curl`, `age` on PATH that log their argv. Written red first.
