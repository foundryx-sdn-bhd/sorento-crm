#!/bin/sh
set -e

# Ensure PATH includes Python user binaries
export PATH=/home/appuser/.local/bin:$PATH

# Extract database host and port from DATABASE_URL
# Format: postgresql://user:password@host:port/database
DB_HOST_PORT=$(echo "${DATABASE_URL}" | sed -n 's|.*@\([^/]*\)/.*|\1|p')
DB_HOST=$(echo "${DB_HOST_PORT}" | cut -d: -f1)
DB_PORT=$(echo "${DB_HOST_PORT}" | cut -d: -f2)

# No guessed fallback host: an empty or unparseable DATABASE_URL is a config error.
if [ -z "$DB_HOST" ]; then
  echo "DATABASE_URL is empty or has no host (expected postgresql://user:pass@host:port/db)" >&2
  exit 1
fi

echo "Waiting for database to be ready..."
echo "Connecting to database at ${DB_HOST}:${DB_PORT}..."
until pg_isready -h "${DB_HOST}" -p "${DB_PORT:-5432}" -U ${POSTGRES_USER:-postgres}; do
  echo "Database not ready, waiting..."
  sleep 2
done

echo "${DB_HOST}:${DB_PORT:-5432} - accepting connections"

# Worker entrypoint reuses this script via the `python worker.py` override; skip
# migrations there so blue/green orchestration alone owns schema upgrades and we
# don't double-run from the worker container.
if [ $# -gt 0 ]; then
  echo "Running override command: $@"
  exec "$@"
fi

# Run migrations before serving. Alembic uses `alembic_version` + an advisory
# lock, so concurrent blue/green starts are safe (the loser no-ops). Failure
# here aborts the container start → healthcheck fails → blue/green swap aborts
# → old color keeps serving. Set SKIP_MIGRATIONS=1 to bypass when manually
# managing migrations (e.g. expand-contract two-phase rollouts).
if [ "${SKIP_MIGRATIONS:-0}" != "1" ]; then
  echo "Running database migrations..."
  alembic upgrade head
else
  echo "SKIP_MIGRATIONS=1; skipping alembic upgrade head"
fi

API_HOST="${API_HOST:-0.0.0.0}"
API_PORT="${API_PORT:-8000}"
# gunicorn.conf.py reads these from os.environ (it can't see shell argv
# substitution the way the old inline --bind flag could), so they must be
# exported even when the container never set them explicitly.
export API_HOST API_PORT

echo "Starting FastAPI server on ${API_HOST}:${API_PORT}..."
# Workers, bind, timeout, keep-alive and logging all now live in
# gunicorn.conf.py (same env vars, same defaults) alongside preload_app and
# its post_fork engine-dispose hook - see that file for the keep-alive /
# nginx upstream rationale and the preload/OOM rationale.
exec python -m gunicorn app.main:app -c gunicorn.conf.py
