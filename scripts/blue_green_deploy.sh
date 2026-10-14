#!/usr/bin/env bash
# Blue/green orchestration. Run from the server checkout containing docker-compose.yml.
#
# Public endpoints stay stable across deploys:
#   n8n  -> http://<CRM_ORIGIN>:8765/mcp                (host nginx -> sorento_mcp upstream)
#   API  -> https://<CRM_DOMAIN>/api/v1/*     (Cloudflare -> host nginx -> sorento_backend upstream)
#   UI   -> https://<CRM_DOMAIN>/             (Cloudflare -> host nginx -> sorento_frontend upstream)
# 8765 is not a Cloudflare port, so it is direct to the origin: allow the n8n host only
# (documentation/runbooks/prod-edge-lockdown.md, step 2).
#
# Container loopback ports alternate per color:
#   blue:  backend 127.0.0.1:8000 | frontend 127.0.0.1:3000 | mcp 127.0.0.1:8766
#   green: backend 127.0.0.1:8010 | frontend 127.0.0.1:3010 | mcp 127.0.0.1:8776
#
# Required env: IMAGE_TAG (git SHA, set by CI).
# Tracks active color in .active_color (default blue on first run).
#
# Guards (HA-MEM-DEPLOY-GUARD, documentation/runbooks/memory-and-deploy-guards.md):
#   - memory: refuse to start the new colour when MemAvailable is below
#     DEPLOY_MIN_AVAILABLE_MB (default 4500), so the overlap cannot OOM the box (10 Sep).
#   - snapshot: pg_dump of the database before the new colour runs `alembic upgrade`;
#     no snapshot, no migration. Kept SNAPSHOT_KEEP_MINUTES (default 48 h).
#   - postgres: fail the deploy when the db container restarted, or Postgres crash-recovered
#     (a killed backend makes the postmaster reinitialize WITHOUT a container restart), at
#     any point during the deploy.
#   - lock: an exclusive flock on DEPLOY_LOCK_PATH (default /opt/sorento-crm2/.deploy.lock)
#     for the whole deploy. The weekly restore test (HA-BACKUP-CHECK) waits on the same file,
#     so the two never overlap.
# Tunables may be set on the box in .deploy.env next to docker-compose.yml (sourced below).
# `blue_green_deploy.sh --preflight` checks all of it and changes nothing (runbook step 3).

set -euo pipefail

# `--preflight`: run every check a release depends on, print PASS/FAIL per item and exit
# 0/1. Changes nothing: no lock file, no snapshot dir, no pull, no container touched.
MODE=deploy
case "${1:-}" in
  "") ;;
  --preflight) MODE=preflight ;;
  *) echo "usage: $0 [--preflight]"; exit 2 ;;
esac

cd "$(dirname "$0")/.."   # repo root containing docker-compose.yml
if [ "$MODE" = deploy ]; then
  : "${IMAGE_TAG:?must be set to the git SHA pushed by CI}"
  export IMAGE_TAG
fi

# Owner-tunable knobs live on the box, never in the workflow: see the runbook. Read before
# the lock so DEPLOY_LOCK_PATH / DEPLOY_LOCK_WAIT_SECONDS can be set there too.
if [ -f .deploy.env ]; then
  # shellcheck disable=SC1091
  . ./.deploy.env
fi

# DEPLOY_LOCK_PATH and its default are shared with HA-BACKUP-CHECK's restore test, which
# waits on the same file; the runbook gives the /var/lib/sorento-deploy fallback for a box
# where /opt/sorento-crm2 is not root-owned.
DEPLOY_LOCK_PATH="${DEPLOY_LOCK_PATH:-/opt/sorento-crm2/.deploy.lock}"
# 600s keeps the worst case inside the SSH step's 100m: 60m worker stop grace + this +
# pull, dump, health wait, drain and checks.
DEPLOY_LOCK_WAIT_SECONDS="${DEPLOY_LOCK_WAIT_SECONDS:-600}"
UPSTREAMS_FILE="/etc/nginx/conf.d/sorento_upstreams.conf"
DRAIN_SECONDS="${DRAIN_SECONDS:-30}"
HEALTH_WAIT_TICKS="${HEALTH_WAIT_TICKS:-120}"   # 120 * 2s = 4 min max
TICK_SECONDS=2
# A new colour (backend + frontend + mcp) measured ~3.8 GB with gunicorn preload; the
# default leaves ~0.7 GB headroom on top (PLAN-deploy-memory-and-ci-speed.md).
DEPLOY_MIN_AVAILABLE_MB="${DEPLOY_MIN_AVAILABLE_MB:-4500}"
MEMINFO_FILE="${MEMINFO_FILE:-/proc/meminfo}"
DB_SERVICE="${DB_SERVICE:-db}"
# The database the APP uses. Empty: parsed from the active backend's DATABASE_URL.
DEPLOY_DB_NAME="${DEPLOY_DB_NAME:-}"
SNAPSHOT_DIR="${SNAPSHOT_DIR:-backups/pre-migrate}"
SNAPSHOT_KEEP_MINUTES="${SNAPSHOT_KEEP_MINUTES:-2880}"   # 48 h
# Free space the dump may not eat into: Postgres PANICs when its own disk fills (lesson 95).
SNAPSHOT_MIN_FREE_MB="${SNAPSHOT_MIN_FREE_MB:-10240}"
NUMERIC_KNOBS="DEPLOY_LOCK_WAIT_SECONDS DEPLOY_MIN_AVAILABLE_MB SNAPSHOT_KEEP_MINUTES SNAPSHOT_MIN_FREE_MB"

ACTIVE=$(cat .active_color 2>/dev/null || echo blue)
if [ "$ACTIVE" = "blue" ]; then
  NEW=green; OLD=blue
  NEW_BE_PORT=8010; NEW_FE_PORT=3010; NEW_MCP_PORT=8776
else
  NEW=blue;  OLD=green
  NEW_BE_PORT=8000; NEW_FE_PORT=3000; NEW_MCP_PORT=8766
fi

# --- helpers --------------------------------------------------------------------

# A non-number would make `[ "$x" -lt "$y" ]` an error, which an `if` reads as false: the
# guard would silently pass. Fail closed instead.
is_number() {
  case "${!1}" in ''|*[!0-9]*) return 1;; esac
}
require_number() {
  is_number "$1" || { echo "ERROR: $1='${!1}' must be a whole number (check .deploy.env)"; exit 1; }
}

# MemAvailable in MB (the kernel's own estimate of what can be allocated without swapping).
mem_available_mb() {
  awk '/^MemAvailable:/ { printf "%d", $2 / 1024; found=1 } END { if (!found) exit 1 }' "$MEMINFO_FILE"
}

# Room a new dump needs: twice the newest one, and never below SNAPSHOT_MIN_FREE_MB.
snapshot_need_mb() {
  local need=$SNAPSHOT_MIN_FREE_MB newest newest_mb
  newest=$(ls -t "$SNAPSHOT_DIR"/pre-migrate-*.dump 2>/dev/null | head -1 || true)
  if [ -n "$newest" ]; then
    newest_mb=$(( ($(stat -c %s "$newest") + 1048575) / 1048576 ))
    if [ $((newest_mb * 2)) -gt "$need" ]; then need=$((newest_mb * 2)); fi
  fi
  echo "$need"
}

# Free MB on the filesystem that holds (or will hold) SNAPSHOT_DIR.
snapshot_free_mb() {
  local d=$SNAPSHOT_DIR
  while [ ! -d "$d" ]; do d=$(dirname "$d"); done
  df -Pm "$d" | awk 'NR==2 { print $4 }'
}

# Sets DB_NAME (and DB_NAME_SOURCE) to the database the app uses, or returns 1. The URL
# carries the password: it is parsed here and never printed.
resolve_db_name() {
  local name="" url cid
  if [ -n "$DEPLOY_DB_NAME" ]; then
    name=$DEPLOY_DB_NAME; DB_NAME_SOURCE="DEPLOY_DB_NAME"
  else
    # -a: a stopped or crashed active backend still carries its env (recovery deploys).
    cid=$(docker compose ps -a -q "backend_${ACTIVE}" 2>/dev/null || true)
    if [ -n "$cid" ]; then
      url=$(docker inspect --format='{{range .Config.Env}}{{println .}}{{end}}' "$cid" 2>/dev/null \
        | sed -n 's/^DATABASE_URL=//p' | head -1 || true)
      if [ -n "$url" ]; then
        name=${url%%\?*}; name=${name##*/}   # query first: it may hold a '/'
      fi
    fi
    DB_NAME_SOURCE="DATABASE_URL of backend_${ACTIVE}"
  fi
  case "$name" in ''|*[!A-Za-z0-9_-]*) return 1;; esac
  DB_NAME=$name
}

# Rows in public.alembic_version of the app's database (an empty answer: not reachable).
alembic_rows() {
  docker compose exec -T -e DUMP_DB="$DB_NAME" "$DB_SERVICE" \
    sh -c 'psql -U "$POSTGRES_USER" -d "$DUMP_DB" -Atc "select count(*) from public.alembic_version"' 2>/dev/null || true
}

# --- preflight ------------------------------------------------------------------

preflight() {
  local fails=0 knob avail need free mode owner lockdir rows
  pf() {  # pf PASS|FAIL|WARN <item> <detail>
    printf '%-4s %-16s %s\n' "$1" "$2" "$3"
    if [ "$1" = FAIL ]; then fails=$((fails + 1)); fi
  }
  echo "==> Preflight (changes nothing)"
  for knob in $NUMERIC_KNOBS; do
    if is_number "$knob"; then pf PASS "$knob" "${!knob}"; else pf FAIL "$knob" "'${!knob}' is not a whole number"; fi
  done

  if [ -e "$DEPLOY_LOCK_PATH" ]; then
    if [ ! -r "$DEPLOY_LOCK_PATH" ]; then
      pf FAIL lock "${DEPLOY_LOCK_PATH} exists but this user cannot open it"
    elif ( exec 8<"$DEPLOY_LOCK_PATH"; flock -n 8 ); then
      pf PASS lock "${DEPLOY_LOCK_PATH} free"
    else
      pf WARN lock "${DEPLOY_LOCK_PATH} is held right now (a deploy or the restore test is running)"
    fi
  else
    lockdir=$(dirname "$DEPLOY_LOCK_PATH")
    if [ -d "$lockdir" ] && [ -w "$lockdir" ]; then
      pf PASS lock "${DEPLOY_LOCK_PATH} will be created on the first deploy"
    else
      pf FAIL lock "${lockdir} missing or not writable, so ${DEPLOY_LOCK_PATH} cannot be created (runbook step 0)"
    fi
  fi

  if [ -n "$(docker compose ps -q "$DB_SERVICE" 2>/dev/null || true)" ]; then
    pf PASS "db service" "${DB_SERVICE} running"
  else
    pf FAIL "db service" "no running container for service '${DB_SERVICE}' (set DB_SERVICE)"
  fi

  if resolve_db_name; then
    pf PASS "db name" "${DB_NAME} (from ${DB_NAME_SOURCE})"
    rows=$(alembic_rows)
    if is_number rows && [ "$rows" -ge 1 ]; then
      pf PASS alembic_version "${DB_NAME} has alembic_version (${rows} row(s))"
    else
      pf FAIL alembic_version "cannot read public.alembic_version in '${DB_NAME}' inside ${DB_SERVICE}: wrong DB name? set DEPLOY_DB_NAME"
    fi
  else
    pf FAIL "db name" "not resolved from ${DB_NAME_SOURCE}; set DEPLOY_DB_NAME in .deploy.env"
    pf FAIL alembic_version "skipped: no db name"
  fi

  if is_number DEPLOY_MIN_AVAILABLE_MB && avail=$(mem_available_mb); then
    if [ "$avail" -ge "$DEPLOY_MIN_AVAILABLE_MB" ]; then
      pf PASS memory "${avail} MB available, minimum ${DEPLOY_MIN_AVAILABLE_MB} MB"
    else
      pf FAIL memory "${avail} MB available, below DEPLOY_MIN_AVAILABLE_MB=${DEPLOY_MIN_AVAILABLE_MB}"
    fi
  else
    pf FAIL memory "cannot compare MemAvailable from ${MEMINFO_FILE}"
  fi

  if is_number SNAPSHOT_MIN_FREE_MB; then
    need=$(snapshot_need_mb); free=$(snapshot_free_mb)
    if [ "${free:-0}" -ge "$need" ] 2>/dev/null; then
      pf PASS disk "${free} MB free for snapshots, need ${need} MB"
    else
      pf FAIL disk "${free:-?} MB free for snapshots, need ${need} MB (10 GB floor or twice the newest dump)"
    fi
  else
    pf FAIL disk "SNAPSHOT_MIN_FREE_MB is not a number"
  fi

  if [ -d "$SNAPSHOT_DIR" ]; then
    mode=$(stat -c %a "$SNAPSHOT_DIR"); owner=$(stat -c %U "$SNAPSHOT_DIR")
    if [ "$mode" = 700 ] && [ "$owner" = "$(id -un)" ]; then
      pf PASS "snapshot dir" "${SNAPSHOT_DIR} mode 700, owner ${owner}"
    else
      pf FAIL "snapshot dir" "${SNAPSHOT_DIR} is mode ${mode} owner ${owner}; want 700 owned by $(id -un) (customer data)"
    fi
  else
    lockdir=$(dirname "$SNAPSHOT_DIR")
    while [ ! -d "$lockdir" ]; do lockdir=$(dirname "$lockdir"); done
    if [ -w "$lockdir" ]; then
      pf PASS "snapshot dir" "${SNAPSHOT_DIR} will be created with mode 700"
    else
      pf FAIL "snapshot dir" "${lockdir} is not writable, ${SNAPSHOT_DIR} cannot be created"
    fi
  fi

  if [ "$fails" -eq 0 ]; then
    echo "==> Preflight PASSED (nothing was changed)"; return 0
  fi
  echo "==> Preflight FAILED: ${fails} item(s), see FAIL lines (nothing was changed)"; return 1
}

if [ "$MODE" = preflight ]; then
  if preflight; then exit 0; else exit 1; fi
fi

for knob in $NUMERIC_KNOBS; do require_number "$knob"; done

# Held from here to exit, before anything is changed. Released by the trap as well as by
# exit, because a child that inherited fd 9 would otherwise keep it held.
if [ -e "$DEPLOY_LOCK_PATH" ] && [ ! -w "$DEPLOY_LOCK_PATH" ]; then
  exec 9<"$DEPLOY_LOCK_PATH"    # created by another user: flock works on a read-only fd
elif ! exec 9>>"$DEPLOY_LOCK_PATH"; then
  echo "ERROR: cannot open the deploy lock ${DEPLOY_LOCK_PATH}; see the runbook, step 0"
  exit 1
fi
echo "==> Waiting up to ${DEPLOY_LOCK_WAIT_SECONDS}s for ${DEPLOY_LOCK_PATH}"
if ! flock -w "$DEPLOY_LOCK_WAIT_SECONDS" 9; then
  echo "ERROR: ${DEPLOY_LOCK_PATH} is still held (another deploy or the restore test); nothing was changed"
  exit 1
fi
trap 'flock -u 9' EXIT

DEPLOY_STARTED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)

# --- guards -------------------------------------------------------------------

check_memory() {
  local avail
  if ! avail=$(mem_available_mb); then
    echo "ERROR: memory guard could not read MemAvailable from ${MEMINFO_FILE}"; exit 1
  fi
  if [ "$avail" -lt "$DEPLOY_MIN_AVAILABLE_MB" ]; then
    echo "ERROR: memory guard: ${avail} MB available, below DEPLOY_MIN_AVAILABLE_MB=${DEPLOY_MIN_AVAILABLE_MB}."
    echo "       Refusing to start the ${NEW} colour: the blue/green overlap would push the box into the OOM killer."
    echo "       Nothing was changed; ${ACTIVE} keeps serving. See documentation/runbooks/memory-and-deploy-guards.md."
    exit 1
  fi
  echo "    memory guard: ${avail} MB available (minimum ${DEPLOY_MIN_AVAILABLE_MB} MB)"
}

DB_CID=""
DB_BASELINE=""
db_state() {
  docker inspect --format='{{.RestartCount}} {{.State.StartedAt}}' "$1" 2>/dev/null || echo "gone"
}

record_postgres_baseline() {
  DB_CID=$(docker compose ps -q "$DB_SERVICE" 2>/dev/null || true)
  if [ -z "$DB_CID" ]; then
    echo "ERROR: Postgres container '${DB_SERVICE}' not found; set DB_SERVICE in .deploy.env"; exit 1
  fi
  DB_BASELINE=$(db_state "$DB_CID")
  echo "    postgres baseline: ${DB_SERVICE} restarts/started=${DB_BASELINE}"
}

# Fails (returns 1) when Postgres went down at any point since the deploy started.
postgres_survived() {
  local cid now crash
  cid=$(docker compose ps -q "$DB_SERVICE" 2>/dev/null || true)
  if [ "$cid" != "$DB_CID" ]; then
    echo "ERROR: Postgres container ${DB_SERVICE} was replaced during the deploy"; return 1
  fi
  now=$(db_state "$cid")
  if [ "$now" != "$DB_BASELINE" ]; then
    echo "ERROR: Postgres container ${DB_SERVICE} restarted during the deploy (before: ${DB_BASELINE}, now: ${now})"
    return 1
  fi
  # A killed backend (the 10 Sep OOM) makes the postmaster terminate every session and
  # crash-recover in place: no container restart, same start time. Only the log shows it.
  crash=$(docker logs --since "$DEPLOY_STARTED_AT" "$cid" 2>&1 \
    | grep -E "all server processes terminated; reinitializing|was terminated by signal|database system was interrupted" || true)
  if [ -n "$crash" ]; then
    echo "ERROR: Postgres crash-recovered during the deploy:"
    echo "$crash" | tail -5
    return 1
  fi
  return 0
}

# Custom-format pg_dump of the APP's database, taken inside the db container and written
# atomically. pg_dump's exit status decides completeness; the PGDMP magic catches output
# that is not an archive; the TOC must list alembic_version's data, so a dump of the wrong
# database (or one without the schema version) never passes. Owner-only files.
take_snapshot() {
  local ts file need_mb free_mb toc
  ts=$(date -u +%Y%m%dT%H%M%SZ)
  file="${SNAPSHOT_DIR}/pre-migrate-${ts}-${IMAGE_TAG}.dump"
  mkdir -p "$SNAPSHOT_DIR"
  chmod 700 "$SNAPSHOT_DIR"
  find "$SNAPSHOT_DIR" -maxdepth 1 \( -name 'pre-migrate-*.dump' -o -name 'pre-migrate-*.dump.partial' \) \
    -mmin "+${SNAPSHOT_KEEP_MINUTES}" -delete || true

  need_mb=$(snapshot_need_mb)
  free_mb=$(snapshot_free_mb)
  if ! [ "${free_mb:-0}" -ge "$need_mb" ] 2>/dev/null; then
    echo "ERROR: snapshot disk check: ${free_mb:-?} MB free in ${SNAPSHOT_DIR}, need ${need_mb} MB; not starting ${NEW} (${ACTIVE} keeps serving)"
    exit 1
  fi

  if ! ( umask 077; docker compose exec -T -e DUMP_DB="$DB_NAME" "$DB_SERVICE" \
           sh -c 'pg_dump -U "$POSTGRES_USER" -d "$DUMP_DB" -Fc' > "${file}.partial" ) \
     || [ "$(head -c 5 "${file}.partial")" != "PGDMP" ]; then
    rm -f "${file}.partial"
    echo "ERROR: pre-migration snapshot failed; not starting ${NEW} (no migration has run, ${ACTIVE} keeps serving)"
    exit 1
  fi
  # pg_restore -l reads only the header and TOC; `cat` drains the rest so docker never
  # writes into a closed pipe and reports that as a failure. The grep reads a here-string,
  # not a pipe: under pipefail `printf | grep -q` fails once the TOC outgrows the pipe
  # buffer (a real one is ~240 KB), because grep exits early and printf gets SIGPIPE.
  if ! toc=$(docker compose exec -T "$DB_SERVICE" \
               sh -c 'out=$(pg_restore -l); rc=$?; cat > /dev/null; printf "%s\n" "$out"; exit $rc' < "${file}.partial") \
     || ! grep -Eq '^[0-9]+; [0-9]+ [0-9]+ TABLE DATA public alembic_version( |$)' <<< "$toc"; then
    rm -f "${file}.partial"
    echo "ERROR: pre-migration snapshot of '${DB_NAME}' does not list TABLE DATA public alembic_version; not starting ${NEW} (${ACTIVE} keeps serving)"
    exit 1
  fi
  mv "${file}.partial" "$file"
  echo "    snapshot: ${file} of ${DB_NAME} ($(du -h "$file" | cut -f1))"
}

echo "==> Active=${ACTIVE} New=${NEW} IMAGE_TAG=${IMAGE_TAG}"

echo "==> Recording Postgres state"
record_postgres_baseline

echo "==> Resolving the app's database"
if ! resolve_db_name; then
  echo "ERROR: cannot tell which database the app uses (${DB_NAME_SOURCE}); set DEPLOY_DB_NAME in .deploy.env. Nothing was changed."
  exit 1
fi
echo "    database: ${DB_NAME} (from ${DB_NAME_SOURCE})"

# 1. Pull new images (SHA-tagged) + worker + worker_fast (both use backend image)
echo "==> Pulling images"
docker compose --profile "${NEW}" pull "backend_${NEW}" "frontend_${NEW}" "mcp_${NEW}"
docker compose pull worker worker_fast

# 1b. Memory guard first (cheap), so a box that cannot take the overlap is not also put
#     through a full dump.
echo "==> Memory guard"
check_memory

# 1c. Snapshot before anything can migrate (start.sh runs `alembic upgrade head`).
echo "==> Pre-migration snapshot"
take_snapshot

# 1d. Memory again, right before the overlap starts: the dump took time.
check_memory

# 2. Bring up new color. start.sh runs `alembic upgrade head` before gunicorn
#    so the new container only goes healthy after schema is at HEAD. If alembic
#    fails the container exits → healthcheck fails → this script aborts at
#    step 3 and OLD color keeps serving traffic. Set SKIP_MIGRATIONS=1 in
#    backend env for manual two-phase (expand-contract) rollouts.
echo "==> Starting ${NEW} color"
docker compose --profile "${NEW}" up -d --no-deps \
  "backend_${NEW}" "frontend_${NEW}" "mcp_${NEW}"

# 3. Wait for all three new containers to be healthy
echo "==> Waiting for healthchecks"
for svc in backend frontend mcp; do
  cid=$(docker compose ps -q "${svc}_${NEW}")
  if [ -z "$cid" ]; then
    echo "ERROR: container ${svc}_${NEW} not found"; exit 1
  fi
  i=0
  while [ $i -lt $HEALTH_WAIT_TICKS ]; do
    state=$(docker inspect --format='{{.State.Health.Status}}' "$cid" 2>/dev/null || echo starting)
    if [ "$state" = "healthy" ]; then
      echo "    ${svc}_${NEW} healthy after $((i * TICK_SECONDS))s"
      break
    fi
    sleep $TICK_SECONDS
    i=$((i + 1))
  done
  if [ "$state" != "healthy" ]; then
    echo "ERROR: ${svc}_${NEW} not healthy after $((HEALTH_WAIT_TICKS * TICK_SECONDS))s"
    docker compose logs --tail=200 "${svc}_${NEW}" || true
    # Stop the new color so a hung `alembic upgrade` in start.sh is ended rather than left
    # running (and committing) after the release has already been reported failed. OLD keeps
    # serving; nginx was never swapped.
    echo "==> Stopping ${NEW} color after the failed health check"
    docker compose stop "backend_${NEW}" "frontend_${NEW}" "mcp_${NEW}" || true
    exit 1
  fi
done

# 3b. Never swap traffic onto a colour that came up while Postgres went down.
if ! postgres_survived; then
  echo "==> Stopping ${NEW} color: Postgres went down during startup; ${OLD} keeps serving"
  docker compose stop "backend_${NEW}" "frontend_${NEW}" "mcp_${NEW}" || true
  exit 1
fi

# 4. Atomic upstream swap + graceful nginx reload
echo "==> Swapping nginx upstreams to ${NEW}"
TMP_UPSTREAM=$(mktemp)
cat > "$TMP_UPSTREAM" <<EOF
upstream sorento_backend  { server 127.0.0.1:${NEW_BE_PORT};  keepalive 32; }
upstream sorento_frontend { server 127.0.0.1:${NEW_FE_PORT};  keepalive 32; }
upstream sorento_mcp      { server 127.0.0.1:${NEW_MCP_PORT}; keepalive 16; }
EOF
sudo install -m 0644 "$TMP_UPSTREAM" "$UPSTREAMS_FILE"
rm -f "$TMP_UPSTREAM"
sudo nginx -t
sudo nginx -s reload

# 5. Drain — let nginx finish in-flight requests on OLD color
echo "==> Draining ${DRAIN_SECONDS}s"
sleep "$DRAIN_SECONDS"

# 6. Recreate worker + worker_fast on new image (small background-job blip;
#    safe — RQ pulls atomically)
echo "==> Recreating worker + worker_fast on new image"
docker compose up -d --force-recreate --no-deps worker worker_fast

# 6b. Verify the worker's APScheduler actually started. The worker has no HTTP
#     healthcheck; historically a circular import could kill the scheduler while
#     the RQ loop kept running, leaving email_outbox draining dead with a
#     "running" container. worker.py now exits(1) on scheduler failure, so we
#     watch for either the success log line or a restart/exit.
echo "==> Verifying worker scheduler startup"
WORKER_WAIT_TICKS="${WORKER_WAIT_TICKS:-45}"   # 45 * 2s = 90s max
worker_cid=$(docker compose ps -q worker)
if [ -z "$worker_cid" ]; then
  echo "ERROR: worker container not found after recreate"; exit 1
fi
i=0
worker_ok=""
while [ $i -lt "$WORKER_WAIT_TICKS" ]; do
  if docker logs "$worker_cid" 2>&1 | grep -q "APScheduler started in worker process"; then
    worker_ok=1
    echo "    worker scheduler started after $((i * TICK_SECONDS))s"
    break
  fi
  restarts=$(docker inspect --format='{{.RestartCount}}' "$worker_cid" 2>/dev/null || echo 0)
  status=$(docker inspect --format='{{.State.Status}}' "$worker_cid" 2>/dev/null || echo unknown)
  if [ "$restarts" -gt 0 ] || [ "$status" != "running" ]; then
    echo "ERROR: worker unhealthy (status=${status} restarts=${restarts}) — scheduler failed to start"
    docker logs --tail=100 "$worker_cid" || true
    exit 1
  fi
  sleep $TICK_SECONDS
  i=$((i + 1))
done
if [ -z "$worker_ok" ]; then
  echo "ERROR: worker scheduler did not log startup within $((WORKER_WAIT_TICKS * TICK_SECONDS))s"
  docker logs --tail=100 "$worker_cid" || true
  exit 1
fi

# 6c. Verify worker_fast came up on the role it owns. It runs no scheduler
#     (ENABLE_SCHEDULER=false, unlike worker) so there is no scheduler line to
#     wait for - just the RQ startup line naming its role. This greps the ROLE
#     label (`role=fast`), never a queue list: worker.py's QUEUES registry owns
#     which queues that role drains, so adding a queue never touches this
#     script. A wrong or missing WORKER_ROLE fails worker.py at startup
#     (exit 1 on an unknown role, see resolve_queue_names), which the
#     restart-count / status check in the loop below catches.
echo "==> Verifying worker_fast role startup"
worker_fast_cid=$(docker compose ps -q worker_fast)
if [ -z "$worker_fast_cid" ]; then
  echo "ERROR: worker_fast container not found after recreate"; exit 1
fi
i=0
worker_fast_ok=""
while [ $i -lt "$WORKER_WAIT_TICKS" ]; do
  if docker logs "$worker_fast_cid" 2>&1 | grep -q "Starting RQ worker role=fast"; then
    worker_fast_ok=1
    echo "    worker_fast role started after $((i * TICK_SECONDS))s"
    break
  fi
  restarts=$(docker inspect --format='{{.RestartCount}}' "$worker_fast_cid" 2>/dev/null || echo 0)
  status=$(docker inspect --format='{{.State.Status}}' "$worker_fast_cid" 2>/dev/null || echo unknown)
  if [ "$restarts" -gt 0 ] || [ "$status" != "running" ]; then
    echo "ERROR: worker_fast unhealthy (status=${status} restarts=${restarts})"
    docker logs --tail=100 "$worker_fast_cid" || true
    exit 1
  fi
  sleep $TICK_SECONDS
  i=$((i + 1))
done
if [ -z "$worker_fast_ok" ]; then
  echo "ERROR: worker_fast did not log its role startup line within $((WORKER_WAIT_TICKS * TICK_SECONDS))s"
  docker logs --tail=100 "$worker_fast_cid" || true
  exit 1
fi

# 7. Stop and remove old color (tolerate first-deploy where OLD doesn't exist)
echo "==> Stopping ${OLD} color"
docker compose stop "backend_${OLD}" "frontend_${OLD}" "mcp_${OLD}" || true
docker compose rm -f "backend_${OLD}" "frontend_${OLD}" "mcp_${OLD}" || true

# 8. Persist new active color, clean up.
# -a is required: every image is SHA-tagged, so plain `prune -f` (dangling only)
# never removes anything and old generations pile up ~4.7GB per deploy until the
# disk fills (hit 90% on 31 Aug 2026, lesson 95). The until filter keeps the
# previous generation around for a quick same-day rollback; running containers
# are always kept regardless of age.
echo "${NEW}" > .active_color
docker image prune -af --filter "until=48h" >/dev/null || true

# 9. A deploy during which Postgres went down is a failed deploy, even though traffic
#    is on the new colour: the 10 Sep OOM reported success.
echo "==> Checking Postgres stayed up"
if ! postgres_survived; then
  echo "ERROR: deploy FAILED: Postgres went down during the deploy. ${NEW} is serving; check the app and the db logs."
  exit 1
fi

echo "==> Deploy complete. Active=${NEW}"
