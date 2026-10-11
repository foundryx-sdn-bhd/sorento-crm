#!/usr/bin/env bash
# Secret scan with the gitleaks CLI (CI fast gate, a step of the PR gate since
# CI-COST round 2). Rules in .gitleaks.toml, .gitleaksignore honoured.
#
# The CLI, not gitleaks/gitleaks-action: the action demands a paid license key
# once the repo belongs to an organization ("[foundryx-sdn-bhd] is an
# organization. License key is required.", 10 Oct 2026). The binary is pinned
# and its sha256 checked against the release's checksums file. Log opts match
# what the action passed (`--no-merges --first-parent`).
#
# Env: RANGE (git range to scan, e.g. base..head); needs full history.
set -euo pipefail
GITLEAKS_VERSION="${GITLEAKS_VERSION:-8.30.1}"
dir="${RUNNER_TEMP:-$(mktemp -d)}"
tarball="gitleaks_${GITLEAKS_VERSION}_linux_x64.tar.gz"
base="https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}"
(
  cd "$dir"
  curl -sSfLO "$base/$tarball"
  curl -sSfLO "$base/gitleaks_${GITLEAKS_VERSION}_checksums.txt"
  grep " ${tarball}\$" "gitleaks_${GITLEAKS_VERSION}_checksums.txt" | sha256sum -c -
  tar -xzf "$tarball" gitleaks
)
range="${RANGE:?RANGE is required}"
echo "Scanning $range"
# gitleaks reports "no leaks found" and exits 0 when git cannot resolve the
# range (e.g. a force-pushed base), so check it.
git rev-list --count "$range" >/dev/null
"$dir/gitleaks" git --no-banner -v --redact \
  --config .gitleaks.toml --gitleaks-ignore-path . \
  --log-opts="--no-merges --first-parent $range" .
