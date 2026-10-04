#!/usr/bin/env bash
# Never-stuck smoke (guard G4): boot the stack against a THROWAWAY database, seed the three
# personas, and open every app route as admin, restricted user and expired session.
# The nightly workflow (.github/workflows/never-stuck-nightly.yml) runs exactly this.
#
#   DATABASE_URL=postgresql://sorento:sorento@localhost:5432/sorento_smoke \
#   REDIS_URL=redis://localhost:6379/0 ./scripts/never-stuck-smoke.sh [playwright args]
#
# Needs: Postgres with pgvector and Redis reachable, the backend venv (or BACKEND_PYTHON)
# with requirements installed, frontend node_modules, Playwright's chromium.
# The database must be local, EMPTY, and named *_smoke or *_ci: bootstrap stamps alembic at
# head and the seed adds an admin with a published password, so a dev or prod-copy
# database must never be the target. The backend reads no .env (SORENTO_ENV_FILE points at
# an empty file), the build goes to its own .next-never-stuck, and the ports must be free.
# SKIP_BUILD=1 reuses an existing .next-never-stuck build. Exit code is Playwright's.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BE="$ROOT/sorento_crm_backend"
FE="$ROOT/sorento_crm_frontend"
PY="${BACKEND_PYTHON:-$BE/venv/bin/python}"
BE_PORT="${NEVER_STUCK_BE_PORT:-8000}"
FE_PORT="${NEVER_STUCK_FE_PORT:-3000}"
STATE="$FE/e2e/.never-stuck"
SECRET="${JWT_SECRET:-never-stuck-smoke-secret}"

: "${DATABASE_URL:?set DATABASE_URL to a throwaway local database}"
: "${REDIS_URL:?set REDIS_URL}"
if ! [[ "$DATABASE_URL" =~ ^postgres(ql)?://([^@/]*@)?(localhost|127\.0\.0\.1)(:[0-9]+)?/[A-Za-z0-9_]+_(smoke|ci)$ ]]; then
  echo "refusing: DATABASE_URL must be a local database named *_smoke or *_ci" >&2
  exit 2
fi

port_busy() { curl -s -o /dev/null --max-time 2 "http://127.0.0.1:$1/"; }
for p in "$BE_PORT" "$FE_PORT"; do
  if port_busy "$p"; then
    echo "refusing: port $p is already serving; stop it or set NEVER_STUCK_BE_PORT / NEVER_STUCK_FE_PORT" >&2
    exit 2
  fi
done

mkdir -p "$STATE"
: > "$STATE/empty.env"
export SORENTO_ENV_FILE="$STATE/empty.env" DIRECT_URL="${DIRECT_URL:-$DATABASE_URL}" \
  JWT_SECRET="$SECRET" JWT_ALGORITHM=HS256 NEXT_DIST_DIR=.next-never-stuck

# Each server runs in its own process group where `setsid` exists (Linux), so the whole tree
# goes on exit: `npx` and uvicorn's worker processes do not forward a plain kill.
SETSID=""
command -v setsid >/dev/null && SETSID=setsid
pids=()
cleanup() {
  for p in ${pids[@]+"${pids[@]}"}; do kill -- "-$p" 2>/dev/null || kill "$p" 2>/dev/null || true; done
}
trap cleanup EXIT

wait_http() { # url, seconds, label, pid
  for _ in $(seq 1 "$2"); do
    kill -0 "$4" 2>/dev/null || { echo "$3 exited before answering (see $STATE/*.log)" >&2; return 1; }
    curl -s -o /dev/null "$1" && return 0
    sleep 1
  done
  echo "$3 did not answer on $1 within $2s" >&2
  return 1
}

echo "== bootstrap + seed"
(cd "$BE" && "$PY" -m scripts.bootstrap_env)
(cd "$BE" && "$PY" -m scripts.seed_never_stuck_smoke --out "$STATE/seed.json" >/dev/null)

echo "== backend :$BE_PORT"
(cd "$BE" && CORS_ORIGINS="http://localhost:$FE_PORT" exec $SETSID "$PY" -m uvicorn app.main:app \
  --host 127.0.0.1 --port "$BE_PORT" --workers 2) > "$STATE/backend.log" 2>&1 &
pids+=($!)
wait_http "http://127.0.0.1:$BE_PORT/docs" 120 backend "${pids[0]}"

export FASTAPI_INTERNAL_URL="http://127.0.0.1:$BE_PORT" NEXTAUTH_URL="http://localhost:$FE_PORT" \
  NEXTAUTH_SECRET="$SECRET" NEXT_TELEMETRY_DISABLED=1
if [ "${SKIP_BUILD:-0}" != "1" ]; then
  echo "== frontend build"
  # NEVER_STUCK_API_PROXY bakes the /api/v1 rewrite into this build (next.config.mjs);
  # NEXT_SKIP_TYPECHECK matches the Docker build.
  (cd "$FE" && NEVER_STUCK_API_PROXY=1 NEXT_SKIP_TYPECHECK=1 NODE_OPTIONS="--max-old-space-size=6144" npx next build)
  # A build into NEXT_DIST_DIR rewrites next-env.d.ts and tsconfig.json to point at it;
  # put them back so a checkout without .next-never-stuck still type-checks.
  git -C "$ROOT" checkout -- sorento_crm_frontend/next-env.d.ts sorento_crm_frontend/tsconfig.json 2>/dev/null || true
fi

echo "== frontend :$FE_PORT"
(cd "$FE" && exec $SETSID npx next start -p "$FE_PORT") > "$STATE/frontend.log" 2>&1 &
pids+=($!)
wait_http "http://localhost:$FE_PORT/signin" 120 frontend "${pids[1]}"

echo "== smoke"
set +e
(cd "$FE" && NEVER_STUCK_SEED="$STATE/seed.json" PORTAL_E2E_BASE_URL="http://localhost:$FE_PORT" \
  npx playwright test -c playwright.never-stuck.config.ts "$@")
code=$?
set -e
(cd "$FE" && node e2e/never-stuck/summarize.mjs test-results/never-stuck/results.json) || true
exit $code
