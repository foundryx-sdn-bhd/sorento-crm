"""The Mac mini's self-hosted runner (scripts/ci-runner/): what keeps it safe.

Owner, 14 Oct 2026: "self hosted runner on this mac mini". The test jobs that
test_ci_runner_routing.py can route to `sorento-mac` run on one runner inside
a colima VM. Brief CI-RUNNER sets the lines this file pins, and the PR #1644
review (14 Oct) measured why the first layout did not hold them: a
privileged Docker-in-Docker gives any job root in the VM, which can drop any
firewall in it, and its entrypoint opened an unauthenticated TCP API.

1. Registered to jayson-odoo/sorento-crm only, after checking it is private,
   with a token from that repo's endpoint that never sits on a command line.
2. Jobs never get root in the VM: the runner is an ordinary user (no sudo,
   not in the docker group) and its Docker is rootless, so even a
   `--privileged` container stays inside that user.
3. Jobs stay off the Mac: no Mac directory is mounted, no VM port is
   forwarded to the Mac, and the firewall drops every connection the runner
   user (its jobs, its Docker's network) opens to the Mac (lima maps
   192.168.5.2 to the Mac's localhost, where the dev Postgres trusts every
   local connection), the LAN or the tailnet. The runner does not start
   without it, and a job cannot change it.
4. One runner (a 6 GB VM holds one tsc at 4 GB heap or one xdist shard, not
   two), every job starting with its Docker empty, pinned downloads.

Comment lines are stripped before any check, so a rule kept only in a comment
does not pass.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DIR = ROOT / "scripts" / "ci-runner"
REPO = "jayson-odoo/sorento-crm"
PRIVATE4 = ["10.0.0.0/8", "100.64.0.0/10", "169.254.0.0/16", "172.16.0.0/12", "192.168.0.0/16"]

pytestmark = pytest.mark.skipif(not (ROOT / "scripts").is_dir(), reason="scripts/ not present (backend-only checkout)")


def _read(name: str) -> str:
    path = DIR / name
    assert path.is_file(), f"scripts/ci-runner/{name} is missing"
    lines = path.read_text(encoding="utf-8").splitlines()
    return "\n".join(line for line in lines if not line.lstrip().startswith("#"))


def _chain(nft: str, name: str) -> str:
    match = re.search(rf"chain {name} \{{(.*?)\n  \}}", nft, re.S)
    assert match, f"ci-guard.nft has no `{name}` chain"
    return match.group(1)


# 1. Registration ---------------------------------------------------------


def test_registers_against_the_private_repo_only():
    up = _read("up.sh")
    assert f"repo={REPO}" in up
    assert '[ "$(gh api "repos/$repo" --jq .private)" = true ]' in up
    assert 'gh api -X POST "repos/$repo/actions/runners/registration-token" --jq .token' in up
    assert '--url "https://github.com/$repo"' in up
    for name in ("up.sh", "provision.sh"):
        for url in re.findall(r"github\.com/([\w.$-]+/[\w.-]+)", _read(name)):
            assert url in {REPO, "$repo", "cli/cli", "actions/runner"}, f"{name} points at github.com/{url}"


def test_the_registration_token_never_sits_on_a_command_line():
    up = _read("up.sh")
    assert "--token" not in up and "--token" not in _read("provision.sh")
    # Read from stdin inside the VM into the variable config.sh reads itself.
    assert "read -r ACTIONS_RUNNER_INPUT_TOKEN" in up
    assert "export ACTIONS_RUNNER_INPUT_TOKEN" in up


def test_one_runner_with_the_sorento_mac_label():
    up = _read("up.sh")
    assert re.findall(r"--name (\S+)", up) == ["sorento-mac-1"]
    assert "--labels sorento-mac" in up


# 2. No root for jobs ----------------------------------------------------------


def test_the_runner_user_has_no_sudo_and_no_docker_group():
    prov = _read("provision.sh")
    useradd = re.findall(r"^\s*useradd .*$", prov, re.M)
    assert len(useradd) == 1 and " runner" in useradd[0], useradd
    assert " -G" not in useradd[0] and "--groups" not in useradd[0]
    assert not re.search(r"usermod\b.*\brunner\b", prov)
    assert "sudoers" not in prov
    unit = _read("sorento-runner.service")
    assert "User=runner" in unit


def test_jobs_get_rootless_docker_only():
    prov = _read("provision.sh")
    assert "docker-ce-rootless-extras" in prov and "uidmap" in prov
    assert re.search(r"as_runner dockerd-rootless-setuptool\.sh install", prov)
    assert "loginctl enable-linger runner" in prov
    # AppArmor (Ubuntu 24.04 restricts unprivileged user namespaces) allows rootlesskit only.
    assert '"/usr/bin/rootlesskit" flags=(unconfined)' in prov
    assert "DOCKER_HOST=unix:///run/user/$RUNNER_UID/docker.sock" in prov
    assert "privileged" not in prov


# 3. Off the Mac -----------------------------------------------------------------


def test_vm_mounts_nothing_and_forwards_no_port_to_the_mac():
    start = re.search(r'colima start "\$profile"(.*?)\n\s*fi', _read("up.sh"), re.S)
    assert start, "up.sh no longer starts the VM"
    assert "--mount none" in start.group(1)
    assert "--port-forwarder none" in start.group(1)


@pytest.mark.parametrize("cidr", PRIVATE4)
def test_guard_drops_the_runner_users_connections_to_private_ranges(cidr):
    nft = _read("ci-guard.nft")
    elements = re.search(r"set private4 \{.*?elements = \{(.*?)\}", nft, re.S)
    assert elements and cidr in elements.group(1), cidr
    output = _chain(nft, "output")
    assert "hook output" in output
    rules = [line.strip() for line in output.splitlines() if line.strip()]
    drop = 'meta skuid "runner" ip daddr @private4 counter drop'
    assert drop in rules
    # Replies, 127.0.0.0/8 (the job's own service containers) and DNS on the
    # VM's own address pass first; nothing else on the VM itself (its sshd,
    # any other VM service) is reachable. Review, 14 Oct: an `oif "lo"`
    # accept also let the runner reach the VM's own sshd at 192.168.5.1:22.
    for allow in (
        "ct state established,related accept",
        'meta skuid "runner" ip daddr 127.0.0.0/8 accept',
        'meta skuid "runner" fib daddr type local th dport 53 accept',
    ):
        assert rules.index(allow) < rules.index(drop), allow
    assert not any('oif "lo"' in r for r in rules), rules
    local_drop = 'meta skuid "runner" fib daddr type local counter drop'
    assert local_drop in rules and rules.index(local_drop) < rules.index(drop)
    assert 'meta skuid "runner" ip6 daddr != ::1 counter drop' in rules


def test_the_runner_does_not_start_without_the_guard():
    guard = _read("ci-guard.service")
    assert "Before=network-pre.target" in guard and "Wants=network-pre.target" in guard
    assert "ExecStart=/usr/sbin/nft -f /etc/ci-guard.nft" in guard
    assert "RequiredBy=sorento-runner.service" in guard
    unit = _read("sorento-runner.service")
    assert "Requires=ci-guard.service" in unit and re.search(r"After=.*ci-guard\.service", unit)


# 4. One runner, clean jobs, pinned downloads ------------------------------------


def test_every_job_starts_with_an_empty_docker():
    prov = _read("provision.sh")
    assert "ACTIONS_RUNNER_HOOK_JOB_STARTED=/usr/local/bin/job-started.sh" in prov
    hook = _read("job-started.sh")
    assert "docker ps -aq | xargs -r docker rm -f" in hook
    assert "docker volume prune -af" in hook


def test_downloads_are_pinned_and_checksummed():
    prov = _read("provision.sh")
    assert re.search(r"^RUNNER_VERSION=\d+\.\d+\.\d+$", prov, re.M)
    assert re.search(r"^RUNNER_SHA256=[0-9a-f]{64}$", prov, re.M)
    assert 'echo "$RUNNER_SHA256  $runner_tar" | sha256sum -c -' in prov
    assert re.search(r"^GH_VERSION=\d+\.\d+\.\d+$", prov, re.M)
    assert 'grep " $gh_tar$" "$gh_sums" | sha256sum -c -' in prov
    for pkg in ("postgresql-client", "zstd", "jq", "libpango-1.0-0", "libpangocairo-1.0-0", "libcairo2"):
        assert pkg in prov, pkg
