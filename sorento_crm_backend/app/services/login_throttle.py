"""Brute-force throttle for staff /login.

Counts failed attempts per (email, ip) in Redis and locks for a window after a
threshold. A second, larger per-email ceiling holds whatever the IP is
(SORENTO-CLIENT-IP): the IP comes from `X-Forwarded-For` behind the proxy, so a
caller who can vary it (many hosts, an IPv6 range, a proxy that passes the
header through) must still hit a floor. IPv6 addresses count per /64.

Fail-open: if Redis is unavailable we allow the attempt - an infra outage must
never lock every user out. Successful login clears the (email, ip) counter; the
per-email one expires with its window.
"""
from __future__ import annotations

import ipaddress
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 8           # failures before lock, per (email, ip)
EMAIL_MAX_ATTEMPTS = 40    # failures before lock, per email from any ip
LOCK_WINDOW_SECONDS = 900  # 15 minutes
_KEY_PREFIX = "login_throttle:v1"


@dataclass
class ThrottleResult:
    allowed: bool
    retry_after_seconds: Optional[int] = None


def _redis_conn():
    try:
        from app.services.queue_service import redis_conn
        return redis_conn
    except Exception as e:  # pragma: no cover - infra
        logger.warning("Login throttle: Redis unavailable (%s); failing open.", e)
        return None


def _ip_ident(ip: Optional[str]) -> str:
    if not ip:
        return "noip"
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    if addr.version == 6:
        return str(ipaddress.ip_network(f"{addr}/64", strict=False))
    return str(addr)


def _key(email: str, ip: Optional[str]) -> str:
    return f"{_KEY_PREFIX}:{(email or '').lower()}:{_ip_ident(ip)}"


def _email_key(email: str) -> str:
    return f"{_KEY_PREFIX}:email:{(email or '').lower()}"


def check(email: str, ip: Optional[str]) -> ThrottleResult:
    """Return allowed=False with retry_after when the (email, ip) pair is locked."""
    r = _redis_conn()
    if r is None:
        return ThrottleResult(allowed=True)
    try:
        for key, limit in ((_key(email, ip), MAX_ATTEMPTS), (_email_key(email), EMAIL_MAX_ATTEMPTS)):
            raw = r.get(key)
            count = int(raw) if raw is not None else 0
            if count >= limit:
                ttl = r.ttl(key)
                return ThrottleResult(allowed=False, retry_after_seconds=int(ttl) if ttl and ttl > 0 else LOCK_WINDOW_SECONDS)
    except Exception as e:  # pragma: no cover - infra
        logger.warning("Login throttle: read failed (%s); failing open.", e)
    return ThrottleResult(allowed=True)


def record_failure(email: str, ip: Optional[str]) -> None:
    r = _redis_conn()
    if r is None:
        return
    try:
        pipe = r.pipeline()
        for key in (_key(email, ip), _email_key(email)):
            pipe.incr(key)
            pipe.expire(key, LOCK_WINDOW_SECONDS)
        pipe.execute()
    except Exception as e:  # pragma: no cover - infra
        logger.warning("Login throttle: record_failure failed (%s).", e)


def clear(email: str, ip: Optional[str]) -> None:
    r = _redis_conn()
    if r is None:
        return
    try:
        r.delete(_key(email, ip))
    except Exception as e:  # pragma: no cover - infra
        logger.warning("Login throttle: clear failed (%s).", e)
