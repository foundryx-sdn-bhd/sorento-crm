#!/usr/bin/env bash
# Bring up the Mac mini's self-hosted Actions runner (sorento-mac-1, label
# sorento-mac). Idempotent: starts the colima VM "ci" if needed, provisions
# it (provision.sh), registers the runner once, starts it. REGISTER=0 stops
# after provisioning. After a reboot only the VM has to start (launchd, see
# README.md): the guard, the runner's Docker and the runner are systemd units.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
profile=ci
repo=jayson-odoo/sorento-crm   # private; never register against another repo

if ! colima status -p "$profile" >/dev/null 2>&1; then
  colima start "$profile" --cpus 4 --memory 6 --root-disk 16 --disk 2 \
    --vm-type vz --mount none --port-forwarder none --dns 1.1.1.1 --dns 8.8.8.8 \
    --activate=false --ssh-config=false
fi

vm() { colima ssh -p "$profile" -- "$@"; }
tar -C "$here" -cf - . | vm sudo sh -c 'rm -rf /opt/ci-runner && mkdir -p /opt/ci-runner && tar -C /opt/ci-runner -xf -'
vm sudo bash /opt/ci-runner/provision.sh
[ "${REGISTER:-1}" = 1 ] || exit 0

if ! vm sudo test -f /home/runner/actions-runner/.runner; then
  [ "$(gh api "repos/$repo" --jq .private)" = true ] || { echo "$repo is not private: refusing" >&2; exit 1; }
  # One-hour token, through stdin only: never on a command line on either side.
  gh api -X POST "repos/$repo/actions/runners/registration-token" --jq .token |
    vm sudo -u runner -H bash -c '
      set -euo pipefail
      repo=$1
      read -r ACTIONS_RUNNER_INPUT_TOKEN
      export ACTIONS_RUNNER_INPUT_TOKEN
      cd ~/actions-runner
      ./config.sh --unattended --replace --url "https://github.com/$repo" \
        --name sorento-mac-1 --labels sorento-mac --work _work' _ "$repo"
fi
vm sudo systemctl restart sorento-runner.service
vm systemctl is-active sorento-runner.service
