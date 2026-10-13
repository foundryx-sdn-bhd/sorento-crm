"""The Mac mini's self-hosted runners (scripts/ci-runner/): what keeps them safe.

Owner, 14 Oct 2026: "self hosted runner on this mac mini". The test jobs that
test_ci_runner_routing.py can route to `sorento-mac` run in two runner
containers inside a colima VM, each next to its own Docker daemon. Brief
CI-RUNNER sets the lines this file pins:

1. Registered to jayson-odoo/sorento-crm only (private), never a public repo,
   with a token from that repo's registration-token endpoint, and the running
   runner never holds that token.
2. Jobs stay inside the VM: no host directory is mounted into it, and its
   firewall drops every connection from a container to the Mac (lima maps
   192.168.5.2 to the Mac's localhost, where the dev Postgres and the lane
   servers listen), the LAN and the tailnet. Docker does not start without it.
3. The jobs' Postgres/Redis service containers publish fixed ports
   5432/6379, so each runner has its own Docker and nothing is published on
   the VM, and every job starts with that Docker empty.
4. The image carries what the jobs call directly (gh, psql, zstd, the
   WeasyPrint libraries) at pinned versions.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
DIR = ROOT / "scripts" / "ci-runner"
REPO = "jayson-odoo/sorento-crm"

pytestmark = pytest.mark.skipif(not (ROOT / "scripts").is_dir(), reason="scripts/ not present (backend-only checkout)")


def _read(name: str) -> str:
    path = DIR / name
    assert path.is_file(), f"scripts/ci-runner/{name} is missing"
    return path.read_text(encoding="utf-8")


def _compose() -> dict:
    return yaml.safe_load(_read("compose.yml"))


# 1. Registration ---------------------------------------------------------


def test_registers_against_the_private_repo_only():
    up = _read("up.sh")
    assert f"repo={REPO}" in up
    assert 'gh api -X POST "repos/$repo/actions/runners/registration-token"' in up
    assert 'RUNNER_URL="https://github.com/$repo"' in up
    # No other repo or org URL anywhere in the setup.
    for name in ("up.sh", "compose.yml", "entrypoint.sh", "Dockerfile"):
        for url in re.findall(r"github\.com/([\w.-]+/[\w.-]+)", _read(name)):
            assert url in {REPO, "cli/cli", "$repo"}, f"{name} points at github.com/{url}"


def test_running_runner_never_holds_the_registration_token():
    for name, svc in _compose()["services"].items():
        env = svc.get("environment") or {}
        assert "RUNNER_TOKEN" not in env, name
    up = _read("up.sh")
    # Passed by name from the environment, not on a command line (ps shows those).
    assert "-e RUNNER_TOKEN " in up and "-e RUNNER_TOKEN=" not in up
    assert "unset RUNNER_TOKEN" in up


def test_runners_carry_the_sorento_mac_label():
    assert "--labels sorento-mac" in _read("entrypoint.sh")


# 2. Jobs stay inside the VM -----------------------------------------------


def test_vm_mounts_nothing_from_the_mac():
    start = re.search(r"colima start \"\$profile\"(.*?)\n\s*fi", _read("up.sh"), re.S)
    assert start, "up.sh no longer starts the VM"
    assert "--mount none" in start.group(1)


@pytest.mark.parametrize("cidr", ["10.0.0.0/8", "100.64.0.0/10", "169.254.0.0/16", "172.16.0.0/12", "192.168.0.0/16"])
def test_guard_drops_container_traffic_to_private_ranges(cidr):
    nft = _read("ci-guard.nft")
    assert cidr in nft
    forward = re.search(r"chain forward \{(.*?)\n  \}", nft, re.S).group(1)
    assert "hook forward" in forward and "ip daddr @private4 counter drop" in forward


def test_guard_drops_the_vms_own_connections_to_the_mac():
    nft = _read("ci-guard.nft")
    output = re.search(r"chain output \{(.*?)\n  \}", nft, re.S).group(1)
    assert "hook output" in output and "ip daddr 192.168.5.2 counter drop" in output


def test_docker_does_not_start_without_the_guard():
    unit = _read("ci-guard.service")
    assert "Before=docker.service" in unit
    assert "RequiredBy=docker.service" in unit
    assert "ExecStart=/usr/sbin/nft -f /etc/ci-guard.nft" in unit


# 3. One Docker per runner, nothing published -------------------------------


def test_each_runner_shares_only_its_own_docker():
    services = _compose()["services"]
    runners = sorted(n for n in services if n.startswith("runner"))
    assert runners == ["runner1", "runner2"]
    for name in runners:
        n = name.removeprefix("runner")
        svc = services[name]
        assert svc["network_mode"] == f"service:dind{n}"
        assert f"sock{n}:/var/run/dind" in svc["volumes"]
        assert svc["environment"]["DOCKER_HOST"] == "unix:///var/run/dind/docker.sock"


def test_nothing_is_published_on_the_vm_and_dind_has_no_tcp_api():
    for name, svc in _compose()["services"].items():
        assert "ports" not in svc, name
        assert svc.get("network_mode", "") != "host", name
        if name.startswith("dind"):
            assert not any("tcp://" in a for a in svc["command"]), name
            assert svc["environment"]["DOCKER_TLS_CERTDIR"] == ""
            assert "--host=unix:///var/run/dind/docker.sock" in svc["command"]


def test_every_job_starts_with_an_empty_docker():
    for name, svc in _compose()["services"].items():
        if name.startswith("runner"):
            assert svc["environment"]["ACTIONS_RUNNER_HOOK_JOB_STARTED"] == "/usr/local/bin/job-started.sh"
    hook = _read("job-started.sh")
    assert "docker ps -aq | xargs -r docker rm -f" in hook
    assert "docker volume prune -af" in hook


# 4. The image ---------------------------------------------------------------


def test_image_is_pinned_and_carries_what_the_jobs_call():
    dockerfile = _read("Dockerfile")
    base = re.search(r"^FROM (\S+)", dockerfile, re.M).group(1)
    assert re.fullmatch(r"ghcr\.io/actions/actions-runner:\d+\.\d+\.\d+", base), base
    for pkg in ("postgresql-client", "zstd", "libpango-1.0-0", "libpangocairo-1.0-0", "libcairo2"):
        assert pkg in dockerfile, pkg
    assert re.search(r"ARG GH_VERSION=\d+\.\d+\.\d+", dockerfile)
    assert "sha256sum -c -" in dockerfile
    dind = {svc["image"] for n, svc in _compose()["services"].items() if n.startswith("dind")}
    assert len(dind) == 1 and re.fullmatch(r"docker:\d+\.\d+\.\d+-dind", dind.pop())
