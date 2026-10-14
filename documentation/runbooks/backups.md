# Runbook: verified database backups (HA-BACKUP-CHECK)

Owner-run. Nothing in the repo touches prod. Plan:
`documentation/plans/ops/PLAN-ha-backup-check-14oct.md`.

Today the CRM box runs `/opt/sorento-crm2/backup-db.sh` from root cron at 03:00: pg_dump, gzip,
`gzip -t`, `rclone copy` to `gdrive:db-backups/`, delete the local copy, prune Drive. That script
is not in git, nobody hears when it fails, and nobody has restored from it.

This runbook swaps it for two scripts from the repo:

| Script | What it does |
| --- | --- |
| `scripts/ops/backup-db.sh` | Same backup, plus a Healthchecks.io alarm, an overlap lock, log lines, exit codes, and optional `age` encryption (off by default) |
| `scripts/ops/restore-test.sh` | Downloads the newest dump from Drive, restores it into a throwaway container with no network, counts rows, alarms on failure, removes the container. Never touches the live DB; nothing leaves the box |

The same two scripts serve the n8n box and the shared-service box; only the env file differs.

## Placeholders

Never write the real values into the repo, a PR or a comment.

| Placeholder | Meaning |
| --- | --- |
| `<CRM_ORIGIN>` | the CRM box (the address you SSH to) |
| `<N8N_HOST>` | the n8n box |
| `<SS_HOST>` | the shared-service box |
| `<DB_CONTAINER>` | the Postgres container name on that box (`docker ps --format '{{.Names}}\t{{.Image}}' \| grep -i postgres`) |
| `<DB_USER>`, `<DB_NAME>` | the role and database the old script dumps |
| `<HC_BACKUP_URL>` | the Healthchecks.io ping URL of that box's backup check (`https://hc-ping.com/<uuid>`) |
| `<HC_RESTORE_URL>` | the ping URL of that box's restore-test check |

The ping URLs are secrets in the sense that anyone holding one can mark a check green. They live
only in the env file on the box (mode 600).

## Exit codes (both scripts)

| Code | backup-db.sh | restore-test.sh |
| --- | --- | --- |
| 0 | ok | ok |
| 2 | bad config | bad config, or encrypted dump without `AGE_IDENTITY` |
| 3 | pg_dump failed | no dump on Drive, download or gzip check failed |
| 4 | `gzip -t` failed | throwaway container did not start or never got ready |
| 5 | `age` failed | restore failed (psql prints the line) |
| 6 | rclone upload failed | a sanity table is empty, or a count failed |
| 7 | prune failed (today's upload is on Drive) | |
| 75 | another run holds the lock; skipped, no ping | same |

Every non-zero code except 75 pings `/fail`.

## Step 0. Healthchecks.io (5 min, from your laptop)

1. Sign in at healthchecks.io (free plan is enough), project "Sorento backups".
2. Add one check per box per job. Name, schedule, grace:
   - `crm-backup`: Cron `0 3 * * *`, time zone = the box's cron time zone (`timedatectl` on the box), grace 2 hours.
   - `crm-restore-test`: Cron `30 4 * * 0`, same time zone, grace 6 hours.
   - Later, the same pair for n8n and shared-service (steps 6 and 7).
3. Integrations: email to you (and WhatsApp or Telegram if you want the page on your phone).
4. Copy each check's ping URL. It goes into the box's env file in step 2.

- Check: each check shows "New" (never pinged).
- Rollback: delete the checks. Nothing on the boxes depends on them existing.

## Step 1. Read the old script on the CRM box (read-only, 5 min)

```bash
ssh root@<CRM_ORIGIN>
sudo crontab -l | grep -n backup
sudo cat /opt/sorento-crm2/backup-db.sh
rclone listremotes
rclone lsf gdrive:db-backups | sort | tail -n 5
docker ps --format '{{.Names}}\t{{.Image}}' | grep -iE 'postgres|pgvector'
df -h / /var/lib/docker
```

Write down, for step 2:

- the `pg_dump` line: is it `docker exec <container> pg_dump -U <user> <db>` or a host `pg_dump`?
  Any extra flags (`--no-owner`, `-Fc`, `--exclude-table`)? The new script runs plain
  `pg_dump -U <DB_USER> <DB_NAME>` piped to gzip. If the old line has extra flags, stop and tell crew.
- `RETENTION_DAYS` and the rclone remote and folder (expected `gdrive:db-backups`).
- the file name pattern of the newest dumps on Drive. New dumps are named
  `sorento_crm_<YYYYMMDD>T<HHMMSS>Z.sql.gz`, and both prune and restore test only look at
  `<BACKUP_NAME>_*`. Old files with another name are never pruned by the new script; delete them by
  hand once `RETENTION_DAYS` of new dumps exist (step 8).
- the Postgres image tag (`postgres:17...` or `pgvector/pgvector:pg17`). The restore test reads the
  major version from the dump itself, so this is only a cross-check.
- free disk: the restore test needs about 3x the gzipped dump size free on `/var/tmp` and
  `/var/lib/docker` while it runs.

- Check: you have every value above. If `rclone lsf` errors, the Drive token is broken today:
  `rclone config reconnect gdrive:` first (it prints a URL; finish it from your laptop).
- Rollback: nothing changed.

## Step 2. Install the scripts on the CRM box (10 min)

From your laptop, in the repo checkout on `main` after this PR merges:

```bash
ssh root@<CRM_ORIGIN> 'mkdir -p /opt/sorento-backup /etc/sorento-backup && chmod 700 /etc/sorento-backup'
scp scripts/ops/backup-db.sh scripts/ops/restore-test.sh root@<CRM_ORIGIN>:/opt/sorento-backup/
ssh root@<CRM_ORIGIN> 'chmod 750 /opt/sorento-backup/*.sh && command -v flock gzip curl rclone docker'
```

On the box, write the env file (fill the values from step 1):

```bash
sudo tee /etc/sorento-backup/backup.env >/dev/null <<'EOF'
BACKUP_NAME=sorento_crm
DB_CONTAINER=<DB_CONTAINER>
DB_USER=<DB_USER>
DB_NAME=<DB_NAME>
RCLONE_DEST=gdrive:db-backups
RETENTION_DAYS=<same as the old script>
HC_BACKUP_URL=<HC_BACKUP_URL>
HC_RESTORE_URL=<HC_RESTORE_URL>
# restore test: role the dump objects belong to, plus any role named in GRANT lines (step 4)
RESTORE_DB_USER=<DB_USER>
RESTORE_ROLES=
SANITY_TABLES=users customers orders products audit_logs
SANITY_LATEST=audit_logs.changed_at
EOF
sudo chmod 600 /etc/sorento-backup/backup.env
```

`rclone` reads its config from root's home (`/root/.config/rclone/rclone.conf`), so run both
scripts as root, as the old cron does.

- Check: `sudo ls -l /etc/sorento-backup/backup.env` shows `-rw-------  root root`.
- Rollback: `sudo rm -rf /opt/sorento-backup /etc/sorento-backup`.

## Step 3. First backup by hand, then switch cron (10 min)

```bash
sudo /opt/sorento-backup/backup-db.sh; echo "exit=$?"
rclone lsf gdrive:db-backups | sort | tail -n 3
```

- Check: `exit=0`, the last log line is `done`, a new `sorento_crm_<stamp>.sql.gz` is on Drive, and
  `crm-backup` on Healthchecks.io is green. No `.sql.gz` is left in `/var/backups/sorento-db`.

Switch cron (keep the old line commented, so rollback is one edit):

```bash
sudo crontab -l > /root/crontab.bak.$(date +%F)
sudo crontab -e
```

Comment out the old line and add the new pair:

```
# 0 3 * * * /opt/sorento-crm2/backup-db.sh ...   <- old line, keep it commented
0 3 * * * /opt/sorento-backup/backup-db.sh >> /var/log/sorento-backup.log 2>&1
30 4 * * 0 /opt/sorento-backup/restore-test.sh >> /var/log/sorento-restore-test.log 2>&1
```

Add log rotation:

```bash
sudo tee /etc/logrotate.d/sorento-backup >/dev/null <<'EOF'
/var/log/sorento-backup.log /var/log/sorento-restore-test.log {
  monthly
  rotate 6
  compress
  missingok
  notifempty
}
EOF
```

- Check: the next morning `tail -n 8 /var/log/sorento-backup.log` ends with `done`, and
  Healthchecks.io shows the 03:00 ping.
- Rollback: `sudo crontab /root/crontab.bak.<date>` (puts the old script back exactly).

## Step 4. First restore test by hand (20 min)

```bash
sudo /opt/sorento-backup/restore-test.sh; echo "exit=$?"
docker ps -a --filter name=restore-test- --format '{{.Names}}'
```

The first run pulls `pgvector/pgvector:pg<major>` (about 160 MB).

- Check: `exit=0`; the log shows `rows users=<n>` and the other tables with plausible numbers (compare
  with the CRM screens), `latest audit_logs.changed_at=` within the last day, and
  `done: ... restores cleanly`. The `docker ps -a` line prints nothing. `crm-restore-test` is green.
- If it fails with `ERROR: role "<x>" does not exist`: add `<x>` to `RESTORE_ROLES` in the env file
  and run again. To list every role the dump needs at once:

  ```bash
  F=$(rclone lsf gdrive:db-backups | grep '^sorento_crm_' | sort | tail -n 1)
  rclone cat "gdrive:db-backups/$F" | gzip -dc \
    | grep -E '^(ALTER .* OWNER TO|GRANT |REVOKE |ALTER DEFAULT PRIVILEGES )' \
    | sed -E 's/.* (TO|FROM) //; s/;$//' | tr ',' '\n' | tr -d ' "' | sort -u
  ```

  Leave out `<DB_USER>` and `PUBLIC`; the rest go in `RESTORE_ROLES` (space separated). A role
  name with capitals or other characters than `a-z 0-9 _` is refused by the script: tell crew.
- If it fails with `extension "<x>" is not available`: tell crew (the throwaway image is
  `pgvector/pgvector`, which ships pgvector and the contrib extensions only).
- Rollback: nothing to roll back; the live DB is never touched. A container left behind by a killed
  run: `docker rm -f -v $(docker ps -aq --filter name=restore-test-)`.

## Step 5. Encryption (optional, default OFF, owner decision)

Dumps on Drive are plain gzip today: anyone with access to that Google account can read every
customer row. `age` encryption fixes that. The box holds only the public key; the private key stays
offline with you, so a stolen box or Drive token cannot read old dumps. The cost: restoring, and the
weekly restore test, need the private key.

To switch on:

1. On your laptop: `age-keygen -o sorento-backup.agekey`. Store the file in your password manager
   and on paper or a second offline device. Losing it makes every encrypted dump unreadable.
2. Copy the `# public key: age1...` line from that file.
3. On the box: `sudo apt install -y age`, then add to `/etc/sorento-backup/backup.env`:

   ```
   BACKUP_ENCRYPT=age
   AGE_RECIPIENT=age1...
   ```

4. Run `sudo /opt/sorento-backup/backup-db.sh` and check the new file ends in `.sql.gz.age`.

The weekly restore test then fails with exit 2 ("is encrypted; set AGE_IDENTITY") unless the box has
the private key. Pick one:

- (a) keep the key off the box and run the restore test only in the monthly drill, with the key
  copied in for that run (`AGE_IDENTITY=/root/drill.agekey`, then `shred -u /root/drill.agekey`), and
  remove the weekly restore-test cron line and its Healthchecks check; or
- (b) put the key on the box (`/etc/sorento-backup/restore.agekey`, mode 600) and set
  `AGE_IDENTITY` in the env file. Weaker: the box can then read old dumps, but Drive alone cannot.

- Check: `rclone cat gdrive:db-backups/<newest> | head -c 22` prints `age-encryption.org/v1`.
- Rollback: remove the two lines from the env file. Old `.age` dumps stay readable with the key.

## Step 6. n8n box (check first, then same pattern)

Check on the box before installing anything:

```bash
ssh root@<N8N_HOST>
docker ps --format '{{.Names}}\t{{.Image}}'
docker inspect <n8n container> --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -E '^DB_TYPE|^DB_POSTGRESDB_(DATABASE|USER)'
rclone listremotes; sudo crontab -l
```

- `DB_TYPE=postgresdb`: same pattern. Env file `/etc/sorento-backup/backup.env` with
  `BACKUP_NAME=n8n`, `RCLONE_DEST=gdrive:db-backups/n8n`, the n8n Postgres container, user and DB,
  and for the restore test `SANITY_TABLES=workflow_entity credentials_entity` and `SANITY_LATEST=`
  (empty: n8n's timestamp columns are camelCase, which the script does not accept). If
  `DB_TABLE_PREFIX` is set, put the prefix in front of both table names.
- No `DB_TYPE`, or `sqlite`: n8n keeps everything in `database.sqlite` in its data volume. These
  scripts do not cover that. Tell crew; the backup is then a `sqlite3 .backup` of that file.
- **Back up `N8N_ENCRYPTION_KEY` separately**, in your password manager: `docker exec <n8n container>
  cat /home/node/.n8n/config` or the compose env. Every n8n credential in the dump is encrypted with
  it; a restored DB without the key has workflows but no working credentials.
- rclone: if `listremotes` has no `gdrive:`, copy the CRM box's `/root/.config/rclone/rclone.conf`
  (mode 600) or run `rclone config` there. The `n8n` subfolder keeps prune and restore test apart
  from the CRM's dumps.
- Healthchecks: `n8n-backup` and `n8n-restore-test` checks, as in step 0.
- Then steps 2, 3 and 4 with these values. Use `03:20` for the cron so the boxes do not upload at
  the same minute.

## Step 7. Shared-service box (check first, then same pattern)

```bash
ssh root@<SS_HOST>
docker ps --format '{{.Names}}\t{{.Image}}' | grep -iE 'postgres|pgvector|mysql|mariadb|mongo'
docker exec <pg container> psql -U <user> -Atc "select datname from pg_database where not datistemplate"
```

- One Postgres DB: same pattern, `BACKUP_NAME=shared`, `RCLONE_DEST=gdrive:db-backups/shared`,
  `SANITY_TABLES=` the two or three busiest tables of that service (`\dt+` sorted by size).
- Several DBs in one Postgres: one env file per DB (`/etc/sorento-backup/<db>.env`, each with its own
  `BACKUP_NAME`, folder and Healthchecks URLs) and one cron line per DB:
  `20 3 * * * BACKUP_ENV_FILE=/etc/sorento-backup/<db>.env /opt/sorento-backup/backup-db.sh >> /var/log/sorento-backup.log 2>&1`.
- A non-Postgres store (MySQL, Mongo, a SQLite file): not covered here; tell crew which.
- Healthchecks: one backup and one restore-test check per DB.

## Step 8. Clean up old-style dumps (after RETENTION_DAYS)

Once Drive holds `RETENTION_DAYS` days of `sorento_crm_<stamp>Z.sql.gz` files and the weekly restore
test has been green twice, delete the old-style files the new prune never matches:

```bash
rclone lsf gdrive:db-backups --max-depth 1 | grep -v '^sorento_crm_[0-9]\{8\}T[0-9]\{6\}Z' | grep -v /$
# review that list, then for each:  rclone deletefile "gdrive:db-backups/<name>"
```

Then `sudo rm /opt/sorento-crm2/backup-db.sh` and the commented cron line.

- Rollback: none needed for the delete; it only removes files older than the retention window.

## Monthly restore drill (15 min, first Monday of the month)

The weekly cron proves a dump restores. The drill proves a human can do it under pressure.

- [ ] Healthchecks.io: every backup check pinged in the last 26 h, every restore-test check in the last 8 days. Nothing red in the last month that nobody looked at.
- [ ] `rclone lsf gdrive:db-backups | sort | tail -n 3` on the CRM box: newest is from this morning.
- [ ] `rclone size gdrive:db-backups`: total roughly `RETENTION_DAYS` x one dump (prune works, Drive is not filling up).
- [ ] Run `sudo /opt/sorento-backup/restore-test.sh` by hand on each box. Exit 0.
- [ ] Compare `rows orders=` and `rows customers=` with last month's drill. They only grow.
- [ ] `latest audit_logs.changed_at=` is from the last day.
- [ ] Time the run. Note it here: that is the minimum time to recover the DB.
- [ ] Encryption on: the private key opens this month's dump on your laptop:
      `rclone copy gdrive:db-backups/<newest> . && age -d -i sorento-backup.agekey <newest> | gzip -t`.
- [ ] n8n: `N8N_ENCRYPTION_KEY` is still in the password manager and matches the box.
- [ ] `docker ps -a --filter name=restore-test-` prints nothing on every box.
- [ ] Note the date and result in the ops log.

## Restoring for real (the drill's reason to exist)

Do not restore over the live DB from this runbook. The restore-test container shows the dump is good;
a real recovery (new box, or the live DB is gone) is: stop the backend, worker and pgbouncer,
create an empty database, `gzip -dc <dump> | docker exec -i <DB_CONTAINER> psql -v ON_ERROR_STOP=1
-U <DB_USER> -d <new db>`, point `DATABASE_URL` at it, start the stack. Ask crew to walk it with you;
it is a separate runbook when it is needed.

## Known gaps (not fixed here)

- RPO is still 24 h: a crash at 02:59 loses a day. Fix is WAL archiving (PITR); trigger it when a
  day of lost orders is no longer acceptable.
- `pg_dump` of one DB does not carry roles or passwords (`pg_dumpall --globals-only`). Recovery
  recreates the roles by hand (the role list from step 4).
- Drive sits in the owner's personal Google account; a lost account loses every backup. Encryption
  (step 5) protects confidentiality, not availability. A second copy elsewhere is a follow-up.
