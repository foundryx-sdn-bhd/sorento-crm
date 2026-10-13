#!/usr/bin/env bash
# Bring up the Mac mini's self-hosted Actions runners (label sorento-mac).
# Idempotent: starts the colima VM "ci" if needed, (re)installs the network
# guard, builds the image, registers each runner once, starts everything.
# After a reboot only the VM has to start (launchd, see README.md): the guard
# is a systemd unit in the VM and the containers restart with Docker.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
profile=ci
repo=jayson-odoo/sorento-crm   # private; never register against another repo
export DOCKER_HOST="unix://$HOME/.colima/$profile/docker.sock"
compose=(docker compose -f "$here/compose.yml")

if ! colima status -p "$profile" >/dev/null 2>&1; then
  colima start "$profile" --cpus 4 --memory 6 --disk 18 --root-disk 10 \
    --vm-type vz --mount none --dns 1.1.1.1 --dns 8.8.8.8 \
    --activate=false --ssh-config=false
fi

vm() { colima ssh -p "$profile" -- "$@"; }
vm sudo tee /etc/ci-guard.nft <"$here/ci-guard.nft" >/dev/null
vm sudo tee /etc/systemd/system/ci-guard.service <"$here/ci-guard.service" >/dev/null
vm sudo sh -c 'systemctl daemon-reload && systemctl enable --now ci-guard.service && nft -f /etc/ci-guard.nft'

"${compose[@]}" build
"${compose[@]}" up -d dind1 dind2
for n in 1 2; do
  if ! "${compose[@]}" run --rm --no-deps -T --entrypoint test "runner$n" -f /runner-state/.runner; then
    # One-hour token, used once here; the running container never sees it.
    RUNNER_TOKEN="$(gh api -X POST "repos/$repo/actions/runners/registration-token" --jq .token)"
    export RUNNER_TOKEN
    "${compose[@]}" run --rm --no-deps -T -e RUNNER_TOKEN \
      -e RUNNER_NAME="sorento-mac-$n" -e RUNNER_URL="https://github.com/$repo" \
      "runner$n" configure
    unset RUNNER_TOKEN
  fi
done
"${compose[@]}" up -d
"${compose[@]}" ps
