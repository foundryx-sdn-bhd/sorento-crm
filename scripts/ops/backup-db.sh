#!/usr/bin/env bash
# Nightly Postgres backup to Google Drive (HA-BACKUP-CHECK).
#
# Version-controlled successor of /opt/sorento-crm2/backup-db.sh on the CRM box:
# pg_dump -> gzip -> gzip -t -> (optional age) -> rclone copy -> delete local -> prune Drive.
# Same script on the n8n and shared-service boxes; only the env differs.
# Owner runbook: documentation/runbooks/backups.md
#
# Config (env, or KEY=value lines in $BACKUP_ENV_FILE, default /etc/sorento-backup/backup.env):
#   BACKUP_NAME      file prefix, also the prune filter        (default sorento_crm)
#   DB_CONTAINER     docker container running Postgres; unset = host pg_dump with libpq env
#   DB_USER, DB_NAME role and database to dump                  (default postgres / postgres)
#   RCLONE_DEST      rclone remote:path                         (default gdrive:db-backups)
#   RETENTION_DAYS   prune dumps older than this on Drive       (default 30)
#   BACKUP_DIR       local scratch dir                          (default /var/backups/sorento-db)
#   LOCK_FILE        overlap lock                               (default /var/lock/backup-db-<name>.lock)
#   HC_BACKUP_URL    Healthchecks.io check URL; unset = no pings
#   BACKUP_ENCRYPT   empty (default, off) or "age"
#   AGE_RECIPIENT    age public key (age1...), required when BACKUP_ENCRYPT=age
#
# Exit codes: 0 ok, 2 config, 3 dump, 4 gzip verify, 5 encrypt, 6 upload, 7 prune, 75 already running.

set -euo pipefail
umask 077

BACKUP_ENV_FILE="${BACKUP_ENV_FILE:-/etc/sorento-backup/backup.env}"
if [ -f "$BACKUP_ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$BACKUP_ENV_FILE"
  set +a
fi

BACKUP_NAME="${BACKUP_NAME:-sorento_crm}"
DB_CONTAINER="${DB_CONTAINER:-}"
DB_USER="${DB_USER:-postgres}"
DB_NAME="${DB_NAME:-postgres}"
RCLONE_DEST="${RCLONE_DEST:-gdrive:db-backups}"
RETENTION_DAYS="${RETENTION_DAYS:-30}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/sorento-db}"
LOCK_FILE="${LOCK_FILE:-/var/lock/backup-db-${BACKUP_NAME}.lock}"
HC_BACKUP_URL="${HC_BACKUP_URL:-}"
BACKUP_ENCRYPT="${BACKUP_ENCRYPT:-}"
AGE_RECIPIENT="${AGE_RECIPIENT:-}"

log() { printf '%s backup-db[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$BACKUP_NAME" "$*"; }

hc_ping() {
  [ -n "$HC_BACKUP_URL" ] || return 0
  curl -fsS -m 10 --retry 3 -o /dev/null "${HC_BACKUP_URL}$1" || log "warn: healthchecks ping ${1:-success} failed"
}

STARTED=0
LOCAL_FILES=()
cleanup() {
  local rc=$?
  for f in "${LOCAL_FILES[@]+"${LOCAL_FILES[@]}"}"; do rm -f -- "$f"; done
  if [ "$rc" -ne 0 ] && [ "$rc" -ne 75 ] && [ "$STARTED" -eq 1 ]; then
    log "FAILED exit=$rc"
    hc_ping /fail
  fi
  exit "$rc"
}
trap cleanup EXIT

die() { local code="$1"; shift; log "error: $*"; exit "$code"; }

mkdir -p "$(dirname "$LOCK_FILE")"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  log "another backup holds $LOCK_FILE, skipping"
  exit 75
fi

STARTED=1
hc_ping /start
log "start dest=$RCLONE_DEST retention=${RETENTION_DAYS}d encrypt=${BACKUP_ENCRYPT:-off}"

[[ "$BACKUP_NAME" =~ ^[A-Za-z0-9_-]+$ ]] || die 2 "BACKUP_NAME must be letters, digits, _ or -"
[[ "$RETENTION_DAYS" =~ ^[0-9]+$ ]] && [ "$RETENTION_DAYS" -ge 1 ] || die 2 "RETENTION_DAYS must be a whole number >= 1"
case "$BACKUP_ENCRYPT" in
  "") ;;
  age)
    [ -n "$AGE_RECIPIENT" ] || die 2 "BACKUP_ENCRYPT=age needs AGE_RECIPIENT"
    command -v age >/dev/null || die 2 "BACKUP_ENCRYPT=age but age is not installed"
    ;;
  *) die 2 "BACKUP_ENCRYPT must be empty or age" ;;
esac

mkdir -p "$BACKUP_DIR"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
FILE="$BACKUP_DIR/${BACKUP_NAME}_${STAMP}.sql.gz"
LOCAL_FILES+=("$FILE")

log "dumping ${DB_NAME}${DB_CONTAINER:+ from container $DB_CONTAINER}"
if [ -n "$DB_CONTAINER" ]; then
  docker exec "$DB_CONTAINER" pg_dump -U "$DB_USER" "$DB_NAME" | gzip > "$FILE" || die 3 "pg_dump failed"
else
  pg_dump -U "$DB_USER" "$DB_NAME" | gzip > "$FILE" || die 3 "pg_dump failed"
fi

gzip -t "$FILE" || die 4 "gzip -t failed on $FILE"
log "dump ok size=$(wc -c < "$FILE") bytes"

UPLOAD="$FILE"
if [ "$BACKUP_ENCRYPT" = "age" ]; then
  UPLOAD="$FILE.age"
  LOCAL_FILES+=("$UPLOAD")
  age -r "$AGE_RECIPIENT" -o "$UPLOAD" "$FILE" || die 5 "age encryption failed"
  rm -f -- "$FILE"
  log "encrypted with age"
fi

rclone copy "$UPLOAD" "$RCLONE_DEST" || die 6 "rclone copy to $RCLONE_DEST failed"
log "uploaded $(basename "$UPLOAD")"
rm -f -- "$UPLOAD"

# Prune only this box's dumps (prefix filter, top level only), and only after a good upload.
rclone delete "$RCLONE_DEST" --max-depth 1 --min-age "${RETENTION_DAYS}d" --include "${BACKUP_NAME}_*" \
  || die 7 "rclone prune failed (today's upload is on Drive)"
log "pruned older than ${RETENTION_DAYS}d"

hc_ping ""
log "done"
