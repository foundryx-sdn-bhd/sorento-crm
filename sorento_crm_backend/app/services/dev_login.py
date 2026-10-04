"""DEV-LOGIN-BYPASS: passwordless sign-in for LOCAL test copies only.

Plan: documentation/plans/identity/PLAN-dev-login-bypass-03oct.md.

Every guard must hold or the dev-login routes answer a plain 404 (fail closed):

1. ``DEV_AUTO_LOGIN`` is on (default off).
2. ``ENVIRONMENT`` is in ``ALLOWED_ENVIRONMENTS``. Anything else, an empty value included,
   refuses; and the process refuses to START with the flag on outside that list, without an
   explicitly set ``ENVIRONMENT``, under gunicorn (the production entrypoint), or in a container.
3. The request carries ``X-Dev-Login-Secret`` equal to ``DEV_AUTO_LOGIN_SECRET``. Only the
   frontend's SERVER sends it; the browser never has it. This is what stops a browser request
   relayed by the ``next dev`` rewrite of ``/api/v1``: that proxy rewrites Host to the backend's
   own localhost address and connects from 127.0.0.1, so guards 4 and 5 alone pass for it.
4. No ``X-Forwarded-Host`` (the rewrite proxy always adds one; the server-side fetch never does).
5. The request ``Host`` hostname is ``localhost``, ``*.localhost`` or ``127.0.0.1``.
6. The TCP peer is loopback. Uvicorn only rewrites the peer from ``X-Forwarded-For`` when the
   real peer is itself 127.0.0.1, so the header cannot fake this from outside.
7. The email is in ``DEV_AUTO_LOGIN_USERS`` and that user is ACTIVE and not trashed.
"""
from __future__ import annotations

import hmac
import ipaddress
import logging
import os
import sys
from typing import Optional

logger = logging.getLogger(__name__)

ALLOWED_ENVIRONMENTS = frozenset({"development", "dev", "local", "test"})
MIN_SECRET_LENGTH = 16
SECRET_HEADER = "x-dev-login-secret"


def running_under_gunicorn() -> bool:
    """Production runs ``python -m gunicorn`` with preload_app (start.sh), so gunicorn is
    already imported when app.main is."""
    return "gunicorn" in sys.modules


def running_in_container() -> bool:
    return os.path.exists("/.dockerenv") or os.path.exists("/run/.containerenv")


def environment_allowed(environment: Optional[str]) -> bool:
    return (environment or "").strip().lower() in ALLOWED_ENVIRONMENTS


def secret_configured(secret: Optional[str]) -> bool:
    return len((secret or "").strip()) >= MIN_SECRET_LENGTH


def assert_safe_startup(
    *,
    enabled: bool,
    environment: Optional[str],
    environment_explicit: bool,
    secret: Optional[str],
    under_gunicorn: bool,
    in_container: bool,
) -> None:
    """Crash the process when the flag is on anywhere but a local dev run; warn when active."""
    if not enabled:
        return
    problems = []
    if not environment_explicit:
        problems.append("ENVIRONMENT is not set explicitly")
    if not environment_allowed(environment):
        problems.append(f"ENVIRONMENT={environment!r} is not one of {sorted(ALLOWED_ENVIRONMENTS)}")
    if not secret_configured(secret):
        problems.append(f"DEV_AUTO_LOGIN_SECRET is missing or shorter than {MIN_SECRET_LENGTH} characters")
    if under_gunicorn:
        problems.append("the process runs under gunicorn (the production entrypoint)")
    if in_container:
        problems.append("the process runs inside a container")
    if problems:
        raise RuntimeError(
            "DEV_AUTO_LOGIN is on but " + "; ".join(problems) + ". Passwordless dev sign-in is "
            "only for a local uvicorn run. Refusing to start."
        )
    logger.warning(
        "!!! DEV_AUTO_LOGIN is ACTIVE (ENVIRONMENT=%s): passwordless sign-in is open to the "
        "local frontend server for the DEV_AUTO_LOGIN_USERS allowlist. Never run this in production. !!!",
        environment,
    )


def is_local_host(host_header: Optional[str]) -> bool:
    """True for ``localhost``, ``*.localhost`` and ``127.0.0.1``, with an optional port."""
    if not host_header:
        return False
    host = host_header.strip().lower()
    if host.startswith("["):
        return False  # IPv6 literal: not on the allowlist
    name, sep, port = host.rpartition(":")
    if sep:
        if not port.isdigit():
            return False
        host = name
    if host in ("localhost", "127.0.0.1"):
        return True
    if host.endswith(".localhost"):
        label = host[: -len(".localhost")]
        return bool(label) and all(part and (part.replace("-", "").isalnum()) for part in label.split("."))
    return False


def is_loopback_peer(ip: Optional[str]) -> bool:
    if not ip:
        return False
    try:
        return ipaddress.ip_address(ip).is_loopback
    except ValueError:
        return False


def secret_matches(expected: Optional[str], presented: Optional[str]) -> bool:
    if not secret_configured(expected) or not presented:
        return False
    return hmac.compare_digest(expected.strip().encode("utf-8"), presented.strip().encode("utf-8"))


def allowed_emails(raw: Optional[str]) -> list[str]:
    """The allowlist in order (first = default), lowercased, de-duplicated."""
    seen: list[str] = []
    for part in (raw or "").split(","):
        email = part.strip().lower()
        if email and email not in seen:
            seen.append(email)
    return seen


def request_allowed(
    *,
    enabled: bool,
    environment: Optional[str],
    secret: Optional[str],
    presented_secret: Optional[str],
    forwarded_host: Optional[str],
    host_header: Optional[str],
    peer_ip: Optional[str],
) -> bool:
    return (
        bool(enabled)
        and environment_allowed(environment)
        and secret_matches(secret, presented_secret)
        and forwarded_host is None
        and is_local_host(host_header)
        and is_loopback_peer(peer_ip)
    )
