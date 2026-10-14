# UAC: verified backups (HA-BACKUP-CHECK)

- AC-1 A successful backup pings `$HC_BACKUP_URL/start`, then `$HC_BACKUP_URL`, uploads one
  `<BACKUP_NAME>_<UTC ts>.sql.gz` to `$RCLONE_DEST`, deletes the local copy, prunes Drive with
  `--min-age <RETENTION_DAYS>d`, exits 0.
- AC-2 A failing pg_dump, a corrupt gzip, or a failing rclone push pings `$HC_BACKUP_URL/fail`,
  exits non-zero, and does not prune Drive.
- AC-3 A second run while one holds the lock exits 75 without dumping.
- AC-4 `BACKUP_ENCRYPT=age` uploads `.sql.gz.age` (encrypted to `AGE_RECIPIENT`); missing recipient
  fails before dumping. Unset = no encryption (default).
- AC-5 Without `HC_BACKUP_URL` the backup still runs (no ping, no error).
- AC-6 Restore test picks the newest dump on Drive, starts a container named
  `restore-test-<BACKUP_NAME>-<ts>` from `pgvector/pgvector:pg<major>` with no published port, restores, counts
  `SANITY_TABLES`, pings `$HC_RESTORE_URL` (start/success), and removes the container.
- AC-7 Restore test on any failure pings `$HC_RESTORE_URL/fail`, exits non-zero, and still removes
  the container. It never names the live DB container.
- AC-8 A sanity table with 0 rows fails the restore test.
- AC-9 The runbook's env file, as written, works for both scripts; an env file writable by others is refused.
- AC-10 `SANITY_LATEST=` (empty) skips the max() check.
- AC-11 Prune and restore only match `<BACKUP_NAME>_<stamp>Z.sql.gz[.age]`, so a longer name sharing the prefix is never touched.
- AC-12 Restore refuses a symlinked or foreign work dir, and removes containers and work dirs left by a killed run before starting.
- AC-13 The restore container runs with `--memory` = `--memory-swap` = `RESTORE_MEMORY` (default 2g),
  `--oom-score-adj 1000` and `--cpus 1`; the host side of the gzip | psql stream runs under
  `nice -n 19 ionice -c3` (the container's Postgres is bounded by the CPU cap only).
- AC-14 The restore test holds a shared flock on `DEPLOY_LOCK_PATH` (default `/opt/sorento-crm2/.deploy.lock`, same name and default as the deploy script) for its whole run;
  while a deploy holds it exclusively the test retries, and after `DEPLOY_LOCK_WAIT` (default 3600 s)
  exits 7 with `/fail` before downloading anything. No lock folder = no wait (n8n, shared-service).
  It never creates the lock file and refuses (exit 2, `/fail`) a lock folder that is a symlink or
  group/other writable, with a message naming runbook step 0 option (b), and a lock file that is
  missing, a symlink or not a regular file. A folder owned by root or by a `docker` group member
  (the release user) is accepted; any other owner is refused (exit 2).
- AC-15 Less than max(4x the downloaded dump, 2x its plain SQL) free on the work dir or on the docker root = exit 4 with
  `/fail`, before any container starts.
