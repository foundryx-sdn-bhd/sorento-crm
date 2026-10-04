"""Generic per-key fixed-window request rate limiter (Redis-backed, fail-open).

Unlike `login_throttle` (which counts only *failed* logins per email+ip), this
counts EVERY request in a bucket and rejects once the count exceeds a limit
within the window. Use it to cap abuse-prone unauthenticated endpoints
(signup, password-reset, portal OTP) per client IP.

Fail-open by design: if Redis is unavailable the request is ALLOWED - an infra
outage must never take down signup/login. See PLAN-fix-security-cluster Sub-plan A.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

_KEY_PREFIX = "rate_limit:v1"


@dataclass
class RateResult:
    allowed: bool
    retry_after_seconds: Optional[int] = None


def _redis_conn():
    try:
        from app.services.queue_service import redis_conn
        return redis_conn
    except Exception as e:  # pragma: no cover - infra
        logger.warning("Rate limit: Redis unavailable (%s); failing open.", e)
        return None


def _key(bucket: str, ident: str) -> str:
    return f"{_KEY_PREFIX}:{bucket}:{ident or 'noip'}"


def hit(bucket: str, ident: Optional[str], *, limit: int, window_seconds: int) -> RateResult:
    """Register one request in (bucket, ident) and report whether it's allowed.

    limit<=0 disables the limiter (always allowed). The window is fixed: the
    counter is created on the first hit and expires after window_seconds, so the
    Nth+1 request within that window is rejected with a Retry-After.
    """
    if limit <= 0:
        return RateResult(allowed=True)
    r = _redis_conn()
    if r is None:
        return RateResult(allowed=True)
    key = _key(bucket, ident or "")
    try:
        pipe = r.pipeline()
        pipe.incr(key)
        pipe.ttl(key)
        count, ttl = pipe.execute()
        count = int(count)
        if count == 1 or (isinstance(ttl, int) and ttl < 0):
            # First request in the window (or no TTL set) - start the clock.
            r.expire(key, window_seconds)
            ttl = window_seconds
        if count > limit:
            retry = int(ttl) if isinstance(ttl, int) and ttl > 0 else window_seconds
            return RateResult(allowed=False, retry_after_seconds=retry)
    except Exception as e:  # pragma: no cover - infra
        logger.warning("Rate limit: backend error (%s); failing open.", e)
        return RateResult(allowed=True)
    return RateResult(allowed=True)


def peek(bucket: str, ident: Optional[str], *, limit: int, window_seconds: int) -> RateResult:
    """Would one more request be allowed in (bucket, ident)? Reads the counter, consumes nothing.

    For an endpoint that must charge a bucket only once the work is known to be legitimate (a
    comment on a real token): check with `peek`, do the work, then `record` on success. Same key
    scheme and fail-open behaviour as `hit`.
    """
    if limit <= 0:
        return RateResult(allowed=True)
    r = _redis_conn()
    if r is None:
        return RateResult(allowed=True)
    key = _key(bucket, ident or "")
    try:
        pipe = r.pipeline()
        pipe.get(key)
        pipe.ttl(key)
        raw, ttl = pipe.execute()
        if raw is not None and int(raw) >= limit:
            retry = int(ttl) if isinstance(ttl, int) and ttl > 0 else window_seconds
            return RateResult(allowed=False, retry_after_seconds=retry)
    except Exception as e:  # pragma: no cover - infra
        logger.warning("Rate limit: backend error (%s); failing open.", e)
    return RateResult(allowed=True)


def record(bucket: str, ident: Optional[str], *, window_seconds: int) -> None:
    """Count one request in (bucket, ident), starting the window if it has none."""
    r = _redis_conn()
    if r is None:
        return
    key = _key(bucket, ident or "")
    try:
        pipe = r.pipeline()
        pipe.incr(key)
        pipe.ttl(key)
        count, ttl = pipe.execute()
        if int(count) == 1 or (isinstance(ttl, int) and ttl < 0):
            r.expire(key, window_seconds)
    except Exception as e:  # pragma: no cover - infra
        logger.warning("Rate limit: backend error (%s); failing open.", e)
