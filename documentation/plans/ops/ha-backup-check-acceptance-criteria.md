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
  `restore-test-<ts>` from `pgvector/pgvector:pg<major>` with no published port, restores, counts
  `SANITY_TABLES`, pings `$HC_RESTORE_URL` (start/success), and removes the container.
- AC-7 Restore test on any failure pings `$HC_RESTORE_URL/fail`, exits non-zero, and still removes
  the container. It never names the live DB container.
- AC-8 A sanity table with 0 rows fails the restore test.
