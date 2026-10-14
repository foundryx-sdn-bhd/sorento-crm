#!/usr/bin/env bash
# Restore test for the nightly Drive backup (HA-BACKUP-CHECK).
#
# Runs ON the box that makes the backup. Downloads the newest ${BACKUP_NAME}_* dump from
# $RCLONE_DEST, restores it into a throwaway container (no network, no published port), counts
# rows in $SANITY_TABLES, prints the latest $SANITY_LATEST timestamp, pings $HC_RESTORE_URL, and
# removes the container and the downloaded file on every exit path.
# It never connects to the live database: it does not read DB_CONTAINER or DATABASE_URL.
# Owner runbook: documentation/runbooks/backups.md
#
# Config (env, or $BACKUP_ENV_FILE, the same file backup-db.sh reads):
#   BACKUP_NAME, RCLONE_DEST   which dumps to look at (same meaning as backup-db.sh)
#   PG_MAJOR                   Postgres major for the throwaway image; default = read from the dump
#   RESTORE_IMAGE              image template, %s = major          (default pgvector/pgvector:pg%s)
#   RESTORE_DB_USER            superuser inside the throwaway      (default postgres)
#   RESTORE_ROLES              roles to pre-create so OWNER TO / GRANT lines restore (space separated)
#   SANITY_TABLES              tables that must hold rows          (default users customers orders products audit_logs)
#   SANITY_LATEST              table.column whose max() is printed (default audit_logs.changed_at; empty = skip)
#   RESTORE_WORK_DIR           download dir, must be a real dir owned by the running user (default /var/lib/sorento-restore-test)
#   RESTORE_LOCK_FILE          overlap lock                        (default /var/lib/sorento-backup/restore-test-<name>.lock)
#   RESTORE_READY_TIMEOUT      seconds to wait for Postgres        (default 120)
#   RESTORE_MEMORY             memory cap (and swap cap) of the throwaway container (default 2g)
#   RESTORE_DISK_FACTOR        free space needed on the work dir and on the docker root, as a
#                              multiple of the downloaded dump      (default 4; never less than
#                              2x the plain SQL inside it)
#   DEPLOY_LOCK_PATH           shared with blue_green_deploy.sh, same name and default there
#                              (default /opt/sorento-crm2/.deploy.lock; skipped when its folder does
#                              not exist, as on the n8n box; otherwise the folder must not be
#                              group/other writable and the file must already exist)
#   DEPLOY_LOCK_WAIT           seconds to wait for a running deploy (default 3600)
#   DEPLOY_LOCK_RETRY          seconds between lock attempts        (default 30)
#   AGE_IDENTITY               age identity file, needed only for .age dumps
#   HC_RESTORE_URL             Healthchecks.io check URL; unset = no pings
#
# The box is shared with live Postgres (16 GB; live Postgres was OOM-killed on 10 Sep), so the
# throwaway container is memory, swap and CPU capped and is the kernel's first OOM victim, the
# restore stream runs at nice 19 / idle IO, and the run waits out any deploy.
#
# Exit codes: 0 ok, 2 config, 3 no dump / download, 4 container or not enough disk, 5 restore,
# 6 sanity, 7 a deploy held the lock for DEPLOY_LOCK_WAIT, 75 already running.

set -euo pipefail
umask 077

BACKUP_ENV_FILE="${BACKUP_ENV_FILE:-/etc/sorento-backup/backup.env}"
if [ -e "$BACKUP_ENV_FILE" ]; then
  # Sourced as shell code, so only a file nobody else can write.
  if [ ! -f "$BACKUP_ENV_FILE" ] || [ ! -O "$BACKUP_ENV_FILE" ] \
    || [ -n "$(find "$BACKUP_ENV_FILE" -maxdepth 0 -perm /022)" ]; then
    echo "error: $BACKUP_ENV_FILE must be a regular file owned by $(id -un), not writable by group or others" >&2
    exit 2
  fi
  set -a
  # shellcheck disable=SC1090
  . "$BACKUP_ENV_FILE"
  set +a
fi

BACKUP_NAME="${BACKUP_NAME:-sorento_crm}"
[[ "$BACKUP_NAME" =~ ^[A-Za-z0-9_-]+$ ]] || { echo "error: BACKUP_NAME must be letters, digits, _ or -" >&2; exit 2; }
RCLONE_DEST="${RCLONE_DEST:-gdrive:db-backups}"
PG_MAJOR="${PG_MAJOR:-}"
RESTORE_IMAGE="${RESTORE_IMAGE:-pgvector/pgvector:pg%s}"
RESTORE_DB_USER="${RESTORE_DB_USER:-postgres}"
RESTORE_ROLES="${RESTORE_ROLES:-}"
SANITY_TABLES="${SANITY_TABLES:-users customers orders products audit_logs}"
# No colon: an explicitly empty SANITY_LATEST means "skip", not "use the default".
SANITY_LATEST="${SANITY_LATEST-audit_logs.changed_at}"
RESTORE_WORK_DIR="${RESTORE_WORK_DIR:-/var/lib/sorento-restore-test}"
RESTORE_LOCK_FILE="${RESTORE_LOCK_FILE:-/var/lib/sorento-backup/restore-test-${BACKUP_NAME}.lock}"
RESTORE_READY_TIMEOUT="${RESTORE_READY_TIMEOUT:-120}"
RESTORE_MEMORY="${RESTORE_MEMORY:-2g}"
RESTORE_DISK_FACTOR="${RESTORE_DISK_FACTOR:-4}"
DEPLOY_LOCK_PATH="${DEPLOY_LOCK_PATH:-/opt/sorento-crm2/.deploy.lock}"
DEPLOY_LOCK_WAIT="${DEPLOY_LOCK_WAIT:-3600}"
DEPLOY_LOCK_RETRY="${DEPLOY_LOCK_RETRY:-30}"
AGE_IDENTITY="${AGE_IDENTITY:-}"
HC_RESTORE_URL="${HC_RESTORE_URL:-}"

RESTORE_DB="restore_test"
CONTAINER="restore-test-${BACKUP_NAME}-$(date -u +%Y%m%dT%H%M%SZ)"

log() { printf '%s restore-test[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$BACKUP_NAME" "$*"; }

hc_ping() {
  [ -n "$HC_RESTORE_URL" ] || return 0
  curl -fsS -m 10 --retry 3 -o /dev/null "${HC_RESTORE_URL}$1" || log "warn: healthchecks ping ${1:-success} failed"
}

STARTED=0
CONTAINER_STARTED=0
WORK=""
cleanup() {
  local rc=$?
  if [ "$CONTAINER_STARTED" -eq 1 ]; then
    docker rm -f -v "$CONTAINER" >/dev/null 2>&1 || log "warn: could not remove $CONTAINER, run: docker rm -f -v $CONTAINER"
  fi
  [ -n "$WORK" ] && rm -rf -- "$WORK"
  if [ "$rc" -ne 0 ] && [ "$rc" -ne 75 ] && [ "$STARTED" -eq 1 ]; then
    log "FAILED exit=$rc"
    hc_ping /fail
  fi
  exit "$rc"
}
trap cleanup EXIT

die() { local code="$1"; shift; log "error: $*"; exit "$code"; }

IDENT_RE='^[a-z_][a-z0-9_]*$'
QUAL_RE='^[a-z_][a-z0-9_]*(\.[a-z_][a-z0-9_]*)?$'

mkdir -p "$(dirname "$RESTORE_LOCK_FILE")"
exec 9>"$RESTORE_LOCK_FILE"
if ! flock -n 9; then
  log "another restore test holds $RESTORE_LOCK_FILE, skipping"
  exit 75
fi

STARTED=1
hc_ping /start
log "start source=$RCLONE_DEST"

[[ "$RESTORE_DB_USER" =~ $IDENT_RE ]] || die 2 "RESTORE_DB_USER is not a plain identifier"
for t in $SANITY_TABLES; do [[ "$t" =~ $QUAL_RE ]] || die 2 "SANITY_TABLES entry is not a plain table name: $t"; done
for r in $RESTORE_ROLES; do [[ "$r" =~ $IDENT_RE ]] || die 2 "RESTORE_ROLES entry is not a plain identifier: $r"; done
if [ -n "$SANITY_LATEST" ]; then
  [[ "$SANITY_LATEST" =~ ^[a-z_][a-z0-9_]*(\.[a-z_][a-z0-9_]*)?\.[a-z_][a-z0-9_]*$ ]] \
    || die 2 "SANITY_LATEST must be table.column"
fi
[[ "$RESTORE_READY_TIMEOUT" =~ ^[0-9]+$ ]] || die 2 "RESTORE_READY_TIMEOUT must be seconds"
[[ "$RESTORE_MEMORY" =~ ^[1-9][0-9]*[kmg]?$ ]] || die 2 "RESTORE_MEMORY must look like 2g or 1536m"
[[ "$RESTORE_DISK_FACTOR" =~ ^[1-9][0-9]*$ ]] || die 2 "RESTORE_DISK_FACTOR must be a whole number >= 1"
[[ "$DEPLOY_LOCK_WAIT" =~ ^[0-9]+$ ]] && [[ "$DEPLOY_LOCK_RETRY" =~ ^[1-9][0-9]*$ ]] \
  || die 2 "DEPLOY_LOCK_WAIT and DEPLOY_LOCK_RETRY must be seconds"

# Shared lock: restore tests may overlap each other, a deploy (exclusive) may not overlap either.
# The fd stays open, so the lock is held until this script exits.
DEPLOY_LOCK_DIR="$(dirname "$DEPLOY_LOCK_PATH")"
if [ -d "$DEPLOY_LOCK_DIR" ]; then
  # Never create the lock, and never open it in a folder that group or others can write: a
  # planted symlink or FIFO there would make this root process open any path. The folder may be
  # owned by root or by the release user (runbook step 0, option b); the release user runs
  # docker compose, so it is root-equivalent on the box already. The file is created once by
  # hand and opened read-only, so a non-root deploy can lock it too.
  if [ -L "$DEPLOY_LOCK_DIR" ] || [ -n "$(find "$DEPLOY_LOCK_DIR" -maxdepth 0 -perm /022)" ]; then
    die 2 "$DEPLOY_LOCK_DIR is a symlink or writable by group or others. Follow runbook step 0 option (b): run once 'install -d -o <release user> -g root -m 755 /var/lib/sorento-deploy' and 'install -m 644 -o <release user> -g root /dev/null /var/lib/sorento-deploy/.deploy.lock', then set DEPLOY_LOCK_PATH=/var/lib/sorento-deploy/.deploy.lock in BOTH the restore-test env and the deploy env"
  fi
  if [ -L "$DEPLOY_LOCK_PATH" ] || [ ! -f "$DEPLOY_LOCK_PATH" ]; then
    die 2 "$DEPLOY_LOCK_PATH is missing or not a regular file; create it: install -m 644 /dev/null $DEPLOY_LOCK_PATH"
  fi
  exec 8<"$DEPLOY_LOCK_PATH"
  waited=0
  until flock -s -n 8; do
    [ "$waited" -lt "$DEPLOY_LOCK_WAIT" ] || die 7 "a deploy held $DEPLOY_LOCK_PATH for ${DEPLOY_LOCK_WAIT}s"
    [ "$waited" -eq 0 ] && log "deploy in progress, waiting up to ${DEPLOY_LOCK_WAIT}s"
    sleep "$DEPLOY_LOCK_RETRY"
    waited=$((waited + DEPLOY_LOCK_RETRY))
  done
  log "deploy lock held (shared): $DEPLOY_LOCK_PATH"
else
  log "no deploy lock folder $(dirname "$DEPLOY_LOCK_PATH") on this box, not waiting for deploys"
fi

# The work dir holds a plaintext dump: it must be ours, not a symlink, and closed to others.
mkdir -p "$RESTORE_WORK_DIR"
if [ ! -d "$RESTORE_WORK_DIR" ] || [ -L "$RESTORE_WORK_DIR" ] || [ ! -O "$RESTORE_WORK_DIR" ]; then
  die 2 "RESTORE_WORK_DIR $RESTORE_WORK_DIR must be a real directory owned by $(id -un)"
fi
chmod 700 "$RESTORE_WORK_DIR"

# A killed run (SIGKILL, OOM, reboot) skips the EXIT trap: clear what THIS job left behind.
# Names cannot contain ".", and the stamp is matched exactly, so another job's run is never hit.
rm -rf -- "$RESTORE_WORK_DIR/run.${BACKUP_NAME}."*
LEFTOVERS="$(docker ps -a --filter "name=restore-test-${BACKUP_NAME}-" --format '{{.Names}}')" \
  || die 4 "docker ps failed (is the docker daemon running?)"
for old in $LEFTOVERS; do
  if [[ "$old" =~ ^restore-test-${BACKUP_NAME}-[0-9]{8}T[0-9]{6}Z$ ]]; then
    log "removing leftover container $old"
    docker rm -f -v "$old" >/dev/null || log "warn: could not remove $old"
  fi
done

# Newest dump: names carry a sortable UTC stamp, so the last one in name order is the newest.
NEWEST="$(rclone lsf --files-only --max-depth 1 --include "${BACKUP_NAME}_[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]T[0-9][0-9][0-9][0-9][0-9][0-9]Z.sql.gz" --include "${BACKUP_NAME}_[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]T[0-9][0-9][0-9][0-9][0-9][0-9]Z.sql.gz.age" "$RCLONE_DEST" | LC_ALL=C sort | tail -n 1)" \
  || die 3 "rclone lsf $RCLONE_DEST failed"
[ -n "$NEWEST" ] || die 3 "no ${BACKUP_NAME}_*.sql.gz dump found in $RCLONE_DEST"
case "$NEWEST" in
  *.age) [ -n "$AGE_IDENTITY" ] && [ -f "$AGE_IDENTITY" ] || die 2 "$NEWEST is encrypted; set AGE_IDENTITY to the age identity file" ;;
esac
log "newest dump: $NEWEST"

WORK="$(mktemp -d "$RESTORE_WORK_DIR/run.${BACKUP_NAME}.XXXXXX")"
rclone copyto "$RCLONE_DEST/$NEWEST" "$WORK/$NEWEST" || die 3 "download of $NEWEST failed"

DUMP="$WORK/$NEWEST"
if [[ "$NEWEST" == *.age ]]; then
  age -d -i "$AGE_IDENTITY" -o "${DUMP%.age}" "$DUMP" || die 3 "age decrypt failed"
  rm -f -- "$DUMP"
  DUMP="${DUMP%.age}"
fi
# Full decompress pass: checks the gzip and measures the plain SQL in one go.
PLAIN_BYTES="$(nice -n 19 ionice -c3 gzip -dc "$DUMP" | wc -c)" || die 3 "gzip check failed on $NEWEST"

# Disk preflight: the restored DB lands on the docker root, which shares the disk with live Postgres.
# Need the larger of RESTORE_DISK_FACTOR x the gzipped dump and 2x the plain SQL (tables + indexes);
# the gzipped size alone underestimates a dump that compresses well.
free_bytes() { df -PB1 "$1" | awk 'NR==2 {print $4}'; }
DUMP_BYTES="$(wc -c < "$DUMP")"
NEED=$((DUMP_BYTES * RESTORE_DISK_FACTOR))
[ $((PLAIN_BYTES * 2)) -le "$NEED" ] || NEED=$((PLAIN_BYTES * 2))
DOCKER_ROOT="$(docker info --format '{{.DockerRootDir}}')" || die 4 "docker info failed"
for d in "$RESTORE_WORK_DIR" "$DOCKER_ROOT"; do
  have="$(free_bytes "$d")" || die 4 "df $d failed"
  [[ "$have" =~ ^[0-9]+$ ]] || die 4 "could not read free space on $d"
  [ "$have" -ge "$NEED" ] || die 4 "only $have bytes free on $d, need $NEED (max of ${RESTORE_DISK_FACTOR}x the dump, 2x its plain SQL)"
  log "disk ok: $have bytes free on $d, need $NEED"
done

if [ -z "$PG_MAJOR" ]; then
  PG_MAJOR="$({ gzip -dc "$DUMP" || true; } | head -n 30 | sed -n 's/^-- Dumped from database version \([0-9][0-9]*\).*/\1/p' | head -n 1)"
  [ -n "$PG_MAJOR" ] || die 2 "could not read the Postgres version from the dump header; set PG_MAJOR"
fi
[[ "$PG_MAJOR" =~ ^[0-9]+$ ]] || die 2 "PG_MAJOR must be a number"
# shellcheck disable=SC2059
IMAGE="$(printf "$RESTORE_IMAGE" "$PG_MAJOR")"
log "throwaway container $CONTAINER from $IMAGE"

# The password never leaves this process: there is no network and no published port.
PW="$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')"
CONTAINER_STARTED=1
docker run -d --name "$CONTAINER" --network none \
  --memory "$RESTORE_MEMORY" --memory-swap "$RESTORE_MEMORY" --oom-score-adj 1000 --cpus 1 \
  -e POSTGRES_USER="$RESTORE_DB_USER" -e POSTGRES_PASSWORD="$PW" -e POSTGRES_DB="$RESTORE_DB" \
  "$IMAGE" >/dev/null || die 4 "docker run $IMAGE failed"

psql_c() { docker exec "$CONTAINER" psql -X -A -t -q -v ON_ERROR_STOP=1 -U "$RESTORE_DB_USER" -d "$RESTORE_DB" -c "$1"; }

# TCP, not the socket: the image's init-time server listens on the socket only, then restarts.
waited=0
until docker exec "$CONTAINER" pg_isready -q -h 127.0.0.1 -U "$RESTORE_DB_USER" -d "$RESTORE_DB" >/dev/null 2>&1; do
  [ "$waited" -lt "$RESTORE_READY_TIMEOUT" ] || die 4 "Postgres in $CONTAINER not ready after ${RESTORE_READY_TIMEOUT}s"
  sleep 1
  waited=$((waited + 1))
done

for r in $RESTORE_ROLES; do
  psql_c "CREATE ROLE \"$r\"" >/dev/null || die 5 "could not create role $r"
done

log "restoring"
nice -n 19 ionice -c3 gzip -dc "$DUMP" \
  | nice -n 19 ionice -c3 docker exec -i "$CONTAINER" psql -X -q -v ON_ERROR_STOP=1 -U "$RESTORE_DB_USER" -d "$RESTORE_DB" >/dev/null \
  || die 5 "restore of $NEWEST failed (see the psql error above)"
log "restore ok"

failed=0
for t in $SANITY_TABLES; do
  n="$(psql_c "SELECT count(*) FROM $t")" || die 6 "count on $t failed"
  log "rows $t=$n"
  if ! [[ "$n" =~ ^[0-9]+$ ]] || [ "$n" -eq 0 ]; then
    log "error: $t has no rows"
    failed=1
  fi
done
if [ -n "$SANITY_LATEST" ]; then
  col="${SANITY_LATEST##*.}"
  tbl="${SANITY_LATEST%.*}"
  latest="$(psql_c "SELECT max($col) FROM $tbl")" || die 6 "max($col) on $tbl failed"
  log "latest $SANITY_LATEST=$latest"
fi
[ "$failed" -eq 0 ] || die 6 "sanity counts failed"

hc_ping ""
log "done: $NEWEST restores cleanly"
