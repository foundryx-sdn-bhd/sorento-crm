#!/usr/bin/env bash
# `configure`: register this runner once (RUNNER_TOKEN, RUNNER_NAME, RUNNER_URL),
# keep its credentials in /runner-state, exit. Anything else: run the runner.
set -euo pipefail
cd /home/runner
state=/runner-state
files=(.runner .credentials .credentials_rsaparams)

if [ "${1:-}" = "configure" ]; then
  ./config.sh --unattended --replace \
    --url "${RUNNER_URL:?}" --token "${RUNNER_TOKEN:?}" \
    --name "${RUNNER_NAME:?}" --labels sorento-mac --work _work
  for f in "${files[@]}"; do cp "$f" "$state/$f"; done
  exit 0
fi

if [ ! -f "$state/.runner" ]; then
  echo "not registered: run scripts/ci-runner/up.sh on the Mac" >&2
  exit 1
fi
for f in "${files[@]}"; do cp "$state/$f" "$f"; done
exec ./run.sh
