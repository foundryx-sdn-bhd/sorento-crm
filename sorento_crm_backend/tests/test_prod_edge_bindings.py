"""HA-SEC-EDGE: the repo's deploy files publish nothing on a public interface.

Docker writes its own iptables rules for a published port, ahead of ufw, so `"5432:5432"` in a
compose file is reachable from the internet whatever the host firewall says. The only binding
that holds is an explicit `127.0.0.1:` host address. These tests pin that for every compose file
in the repo, keep raw public addresses out of the deploy scripts, and stop `start.sh` falling
back to a hard-coded database host.

Runbook: documentation/runbooks/prod-edge-lockdown.md.
"""
from __future__ import annotations

import ipaddress
import os
import re
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]

# (compose file relative to REPO, service) pairs that are MEANT to face the network: the
# all-in-one compose's frontend nginx is that stack's edge. Everything else binds loopback.
PUBLIC_BY_DESIGN = {("sorento_crm/docker-compose.yml", "frontend")}

LOOPBACK_HOSTS = {"127.0.0.1", "::1", "[::1]"}


def _compose_files() -> list[Path]:
    found = []
    for pattern in ("**/docker-compose*.yml", "**/docker-compose*.yaml", "**/compose*.yml"):
        for path in REPO.glob(pattern):
            rel = path.relative_to(REPO).as_posix()
            if "node_modules" in rel or rel.startswith(".git/"):
                continue
            found.append(path)
    return sorted(set(found))


def _host_ip(entry) -> str | None:
    """The host address a compose `ports` entry binds, or None when it binds every interface."""
    if isinstance(entry, dict):  # long syntax
        return entry.get("host_ip")
    text = re.sub(r"\$\{[^}]*\}", "X", str(entry))  # `${PORT:-5432}` holds a colon of its own
    if text.startswith("["):  # `[::1]:5432:5432`
        return text[: text.index("]") + 1]
    parts = text.split(":")
    return parts[0] if len(parts) == 3 else None


def _published(compose: Path):
    doc = yaml.safe_load(compose.read_text()) or {}
    for name, service in (doc.get("services") or {}).items():
        for entry in (service or {}).get("ports") or []:
            yield name, entry


def test_the_repo_has_compose_files_to_check():
    rels = {p.relative_to(REPO).as_posix() for p in _compose_files()}
    assert "sorento_crm/docker-compose.yml" in rels
    assert "sorento_crm_backend/docker-compose.yml" in rels


@pytest.mark.parametrize("compose", _compose_files(), ids=lambda p: p.relative_to(REPO).as_posix())
def test_every_published_port_binds_loopback(compose: Path):
    rel = compose.relative_to(REPO).as_posix()
    open_ports = [
        f"{service}: {entry!r}"
        for service, entry in _published(compose)
        if (rel, service) not in PUBLIC_BY_DESIGN and _host_ip(entry) not in LOOPBACK_HOSTS
    ]
    assert not open_ports, (
        f"{rel} publishes on every interface (docker bypasses ufw): {open_ports}. "
        "Prefix the host port with 127.0.0.1:"
    )


@pytest.mark.parametrize(
    "entry, expected",
    [
        ("5432:5432", None),
        ("${POSTGRES_PORT:-5432}:5432", None),
        ("127.0.0.1:${POSTGRES_PORT:-5432}:5432", "127.0.0.1"),
        ("127.0.0.1:6379:6379", "127.0.0.1"),
        ("[::1]:6379:6379", "[::1]"),
        ("8765", None),
        ({"target": 5432, "published": 5432, "host_ip": "127.0.0.1"}, "127.0.0.1"),
        ({"target": 5432, "published": 5432}, None),
    ],
)
def test_host_ip_reads_every_ports_syntax(entry, expected):
    assert _host_ip(entry) == expected


# Files that reach a server. A public address in one of them is either an origin (never in the
# repo) or a hard-coded dependency that a firewall change silently breaks.
_DEPLOY_FILES = [
    "sorento_crm_backend/start.sh",
    "sorento_crm/deploy.sh",
    "scripts/blue_green_deploy.sh",
    ".github/workflows/deploy.yml",
    "sorento_crm/.env.example",
    "sorento_crm_backend/.env.example",
]
_IPV4 = re.compile(r"(?<![\d.])(\d{1,3}(?:\.\d{1,3}){3})(?:/\d{1,2})?(?![\d.])")


@pytest.mark.parametrize("rel", _DEPLOY_FILES + [p.relative_to(REPO).as_posix() for p in _compose_files()])
def test_deploy_files_carry_no_public_address(rel):
    text = (REPO / rel).read_text()
    public = []
    for match in _IPV4.finditer(text):
        try:
            ip = ipaddress.ip_address(match.group(1))
        except ValueError:
            continue
        if ip.is_global:
            public.append(match.group(0))
    assert not public, f"{rel} names public address(es) {public}; use a placeholder or env var"


def test_start_sh_without_a_database_url_stops_instead_of_guessing_a_host(tmp_path):
    """An empty or unparseable DATABASE_URL used to send pg_isready to a hard-coded public
    address and loop there for ever. It now stops at once and says why."""
    calls = tmp_path / "pg_isready.calls"
    stub = tmp_path / "pg_isready"
    stub.write_text(f'#!/bin/sh\necho "$@" >> "{calls}"\nexit 0\n')
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
    env["PATH"] = f"{tmp_path}:{env.get('PATH', '/usr/bin:/bin')}"
    env["DATABASE_URL"] = ""

    result = subprocess.run(
        ["sh", str(REPO / "sorento_crm_backend/start.sh"), "true"],
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert result.returncode != 0
    assert "DATABASE_URL" in result.stdout + result.stderr
    assert not calls.exists(), f"pg_isready was called with {calls.read_text()!r}"


def test_start_sh_waits_on_the_host_its_database_url_names(tmp_path):
    calls = tmp_path / "pg_isready.calls"
    stub = tmp_path / "pg_isready"
    stub.write_text(f'#!/bin/sh\necho "$@" >> "{calls}"\nexit 0\n')
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    env = dict(os.environ)
    env["PATH"] = f"{tmp_path}:{env.get('PATH', '/usr/bin:/bin')}"
    env["DATABASE_URL"] = "postgresql://u:p@pgbouncer:6432/sorento"

    result = subprocess.run(
        ["sh", str(REPO / "sorento_crm_backend/start.sh"), "true"],
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "-h pgbouncer -p 6432" in calls.read_text()
