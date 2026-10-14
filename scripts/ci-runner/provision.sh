#!/usr/bin/env bash
# Runs as root inside the colima VM "ci" (up.sh copies this directory to
# /opt/ci-runner first). Idempotent. Installs everything but the
# registration: the runner user, its rootless Docker, the network guard, the
# runner and its systemd unit.
set -euo pipefail
src=/opt/ci-runner
RUNNER_UID=1500
RUNNER_VERSION=2.337.0
RUNNER_SHA256=9b1dc70626422526e3c94767cf024896beb15da5342a3f4819bf2feac13e0393
GH_VERSION=2.102.0
dir=/home/runner/actions-runner

# What the jobs call directly on a GitHub-hosted runner (gh, psql/pg_isready,
# jq, zstd for actions/cache) and the libraries WeasyPrint loads (same list
# as the backend image). Rootless Docker needs the rest.
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq --no-install-recommends \
  docker-ce-rootless-extras uidmap dbus-user-session slirp4netns \
  git jq curl unzip zstd xz-utils python3 postgresql-client \
  libglib2.0-0 libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf-2.0-0 \
  libcairo2 libffi8 shared-mime-info fonts-dejavu-core >/dev/null

if [ "$(gh --version 2>/dev/null | awk 'NR==1 {print $3}')" != "$GH_VERSION" ]; then
  tmp="$(mktemp -d)" && cd "$tmp"
  gh_tar="gh_${GH_VERSION}_linux_arm64.tar.gz"
  gh_sums="gh_${GH_VERSION}_checksums.txt"
  curl -sSfLO "https://github.com/cli/cli/releases/download/v${GH_VERSION}/$gh_tar"
  curl -sSfLO "https://github.com/cli/cli/releases/download/v${GH_VERSION}/$gh_sums"
  grep " $gh_tar$" "$gh_sums" | sha256sum -c -
  tar -xzf "$gh_tar"
  install -m 0755 "gh_${GH_VERSION}_linux_arm64/bin/gh" /usr/local/bin/gh
  cd / && rm -rf "$tmp"
fi

# Jobs run as this user: no sudo, not in the docker group (that group is
# root on the VM's own Docker).
if ! id runner >/dev/null 2>&1; then
  useradd -m -u "$RUNNER_UID" -s /bin/bash runner
fi

# The guard first: nothing the runner user opens reaches the Mac or the LAN.
install -m 0644 "$src/ci-guard.nft" /etc/ci-guard.nft
install -m 0644 "$src/ci-guard.service" /etc/systemd/system/ci-guard.service
install -m 0644 "$src/sorento-runner.service" /etc/systemd/system/sorento-runner.service
systemctl daemon-reload
systemctl enable ci-guard.service >/dev/null 2>&1
nft -f /etc/ci-guard.nft
systemctl start ci-guard.service

# Rootless Docker for the runner user: even a --privileged container stays
# inside that user. Ubuntu 24.04 restricts unprivileged user namespaces
# through AppArmor; this lets rootlesskit, and only it, create one.
cat >/etc/apparmor.d/usr.bin.rootlesskit <<'PROFILE'
abi <abi/4.0>,
include <tunables/global>

"/usr/bin/rootlesskit" flags=(unconfined) {
  userns,

  include if exists <local/usr.bin.rootlesskit>
}
PROFILE
apparmor_parser -r /etc/apparmor.d/usr.bin.rootlesskit
loginctl enable-linger runner
for _ in $(seq 1 30); do [ -S "/run/user/$RUNNER_UID/bus" ] && break; sleep 1; done
as_runner() {
  sudo -u runner -H env XDG_RUNTIME_DIR="/run/user/$RUNNER_UID" \
    DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$RUNNER_UID/bus" "$@"
}
if [ ! -f /home/runner/.config/systemd/user/docker.service ]; then
  as_runner dockerd-rootless-setuptool.sh install
fi
as_runner systemctl --user enable --now docker.service >/dev/null 2>&1

if [ ! -x "$dir/run.sh" ]; then
  tmp="$(mktemp -d)" && cd "$tmp"
  runner_tar="actions-runner-linux-arm64-${RUNNER_VERSION}.tar.gz"
  curl -sSfLO "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/$runner_tar"
  echo "$RUNNER_SHA256  $runner_tar" | sha256sum -c -
  install -d -o runner -g runner "$dir"
  tar -xzf "$runner_tar" -C "$dir"
  chown -R runner:runner "$dir"
  "$dir/bin/installdependencies.sh" >/dev/null
  cd / && rm -rf "$tmp"
fi
install -m 0755 "$src/job-started.sh" /usr/local/bin/job-started.sh
# Read by the runner at start; every job inherits it.
cat >"$dir/.env" <<ENV
DOCKER_HOST=unix:///run/user/$RUNNER_UID/docker.sock
ACTIONS_RUNNER_HOOK_JOB_STARTED=/usr/local/bin/job-started.sh
LANG=C.UTF-8
ENV
chown runner:runner "$dir/.env"

systemctl enable sorento-runner.service >/dev/null 2>&1
if [ -f "$dir/.runner" ]; then systemctl restart sorento-runner.service; fi
