#!/usr/bin/env bash
# Blue/green orchestration. Run from the server checkout containing docker-compose.yml.
#
# Public endpoints stay stable across deploys:
#   n8n  -> http://<prod-host>:8765/mcp                 (host nginx -> sorento_mcp upstream)
#   API  -> https://fe-sorento.foundryx.my/api/v1/*     (host nginx -> sorento_backend upstream)
#   UI   -> https://fe-sorento.foundryx.my/             (host nginx -> sorento_frontend upstream)
#
# Container loopback ports alternate per color:
#   blue:  backend 127.0.0.1:8000 | frontend 127.0.0.1:3000 | mcp 127.0.0.1:8766
#   green: backend 127.0.0.1:8010 | frontend 127.0.0.1:3010 | mcp 127.0.0.1:8776
#
# Required env: IMAGE_TAG (git SHA, set by CI).
# Tracks active color in .active_color (default blue on first run).

set -euo pipefail

cd "$(dirname "$0")/.."   # repo root containing docker-compose.yml
: "${IMAGE_TAG:?must be set to the git SHA pushed by CI}"
export IMAGE_TAG

UPSTREAMS_FILE="/etc/nginx/conf.d/sorento_upstreams.conf"
DRAIN_SECONDS="${DRAIN_SECONDS:-30}"
HEALTH_WAIT_TICKS="${HEALTH_WAIT_TICKS:-120}"   # 120 * 2s = 4 min max
TICK_SECONDS=2

ACTIVE=$(cat .active_color 2>/dev/null || echo blue)
if [ "$ACTIVE" = "blue" ]; then
  NEW=green; OLD=blue
  NEW_BE_PORT=8010; NEW_FE_PORT=3010; NEW_MCP_PORT=8776
else
  NEW=blue;  OLD=green
  NEW_BE_PORT=8000; NEW_FE_PORT=3000; NEW_MCP_PORT=8766
fi

echo "==> Active=${ACTIVE} New=${NEW} IMAGE_TAG=${IMAGE_TAG}"

# 1. Pull new images (SHA-tagged) + worker + worker_fast (both use backend image)
echo "==> Pulling images"
docker compose --profile "${NEW}" pull "backend_${NEW}" "frontend_${NEW}" "mcp_${NEW}"
docker compose pull worker worker_fast

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

echo "==> Deploy complete. Active=${NEW}"
