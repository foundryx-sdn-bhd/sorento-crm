"""Count every Respond.io HTTP call per minute (lane CHAT-LOCAL-FIRST, R6).

The lane's whole claim is "a thread open costs at most one Respond call and a poll costs
none", and a claim like that needs a number, not a log grep. Every request
``RespondClient`` sends passes through :func:`httpx_request_hook` (installed by
``RespondClient._http``), which logs the method and path at INFO and bumps a per-minute
counter.

The counter lives in Redis (``sorento:respond-calls:<UTC minute>``, 15 minute TTL) so the
API processes and the worker add into one figure the admin endpoint can read; a process
also keeps its own in-memory copy, which is what answers when Redis is not there (tests,
a broken broker). Neither write may ever fail a Respond call.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import urlsplit

from app.config import settings

logger = logging.getLogger(__name__)

KEY_PREFIX = "sorento:respond-calls:"
TTL_SECONDS = 15 * 60

_lock = threading.Lock()
_local: dict[str, int] = {}
_redis: list = []  # [client] or [None] once resolved; a list so tests can reset it


def minute_key(when: Optional[datetime] = None) -> str:
    when = when or datetime.now(tz=timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M")


def reset() -> None:
    """Forget the in-process counts and the cached Redis client. Test seam."""
    with _lock:
        _local.clear()
        _redis.clear()


def _redis_client():
    if not _redis:
        try:
            import redis as _redis_lib

            _redis.append(
                _redis_lib.from_url(
                    settings.redis_url,
                    decode_responses=True,
                    # On the path of every Respond call, sends included: a hung broker
                    # must cost a second, not a request.
                    socket_timeout=1,
                    socket_connect_timeout=1,
                )
            )
        except Exception:  # noqa: BLE001 - counting is best-effort
            _redis.append(None)
    return _redis[0]


def record(method: str, url: str, when: Optional[datetime] = None) -> None:
    """One Respond.io call happened. Never raises."""
    path = urlsplit(str(url)).path or str(url)
    logger.info("respond.io call %s %s", str(method).upper(), path)
    key = minute_key(when)
    with _lock:
        _local[key] = _local.get(key, 0) + 1
        # Keep the in-process map small: anything older than the TTL window goes.
        if len(_local) > 30:
            for old in sorted(_local)[:-20]:
                _local.pop(old, None)
    client = _redis_client()
    if client is None:
        return
    try:
        redis_key = KEY_PREFIX + key
        pipe = client.pipeline()
        pipe.incr(redis_key)
        pipe.expire(redis_key, TTL_SECONDS)
        pipe.execute()
    except Exception:  # noqa: BLE001
        # The next call retries the broker; the local count still answers.
        _redis.clear()


def httpx_request_hook(request: Any) -> None:
    """``httpx`` request event hook: count the call the client is about to send."""
    try:
        record(getattr(request, "method", "?"), getattr(request, "url", ""))
    except Exception:  # noqa: BLE001 - a counter must never fail a call
        logger.debug("respond call counter failed", exc_info=True)


def local_count(when: Optional[datetime] = None) -> int:
    """This process's count for one minute (the current one by default)."""
    with _lock:
        return _local.get(minute_key(when), 0)


def counts(minutes: int = 10, now: Optional[datetime] = None) -> list[dict]:
    """Per-minute counts for the last ``minutes`` minutes, oldest first.

    Redis answers when reachable (the figure across every process); otherwise this
    process's own counts. Each entry: ``{"minute": "<UTC minute>Z", "calls": n}``.
    """
    now = now or datetime.now(tz=timezone.utc)
    # Never further back than the Redis TTL keeps: a minute past it would read as 0.
    minutes = max(1, min(int(minutes), TTL_SECONDS // 60))
    keys = [minute_key(now - timedelta(minutes=i)) for i in range(minutes - 1, -1, -1)]
    values: Optional[list] = None
    client = _redis_client()
    if client is not None:
        try:
            values = client.mget([KEY_PREFIX + k for k in keys])
        except Exception:  # noqa: BLE001
            values = None
            _redis.clear()
    out = []
    for i, key in enumerate(keys):
        if values is not None:
            raw = values[i]
            calls = int(raw) if raw not in (None, "") else 0
        else:
            with _lock:
                calls = _local.get(key, 0)
        out.append({"minute": key + "Z", "calls": calls})
    return out
