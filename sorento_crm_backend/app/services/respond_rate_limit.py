"""Per-key backoff for Respond.io reads (lane CHAT-LOCAL-FIRST, R4).

Respond.io answers a burst with 429 and, usually, a ``Retry-After``. Before this lane
nothing in the codebase read that header: every caller retried on its own clock, and the
thread poll was the caller, so a rate-limited workspace was hammered harder the more
people were looking at it.

State is per API key (hashed, never logged) and lives in this process: the reconcile
runs on one worker and the request-path sync on each API process, and each protects
Respond from its own calls. A restart forgets the backoff, which is the right failure
mode - the first call after it either succeeds or re-arms it.

``call`` wraps ONE Respond call:

- a key inside its backoff window is refused with :class:`RateLimited` before any HTTP
  happens (the caller records "skipped", it does not wait);
- a 429 arms the window from ``Retry-After`` (seconds or an HTTP date), else doubles from
  1 s per consecutive limit, capped at :data:`MAX_BACKOFF_SECONDS`;
- a 5xx or a transport error arms the same doubling window, because a Respond that is
  down needs the same protection as one that is throttling;
- any other 4xx is the contact's problem (404, 401), not the key's, and does not back off;
- a success clears the window.
"""
from __future__ import annotations

import hashlib
import logging
import threading
import time
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Optional

import httpx

logger = logging.getLogger(__name__)

MAX_BACKOFF_SECONDS = 60.0
BASE_BACKOFF_SECONDS = 1.0

_lock = threading.Lock()
# key -> {"blocked_until": epoch seconds, "consecutive": failures in a row}
_state: dict[str, dict[str, float]] = {}


class RateLimited(Exception):
    """The key is inside its backoff window; nothing was sent."""

    def __init__(self, key: str, retry_after: float):
        super().__init__(f"Respond.io key {key} rate limited; retry in {retry_after:.0f}s")
        self.key = key
        self.retry_after = retry_after


def key_for(client: Any) -> str:
    """A short, non-reversible id for the client's API key (safe to log and to key on)."""
    api_key = str(getattr(client, "api_key", "") or "")
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:12]


def reset() -> None:
    """Forget every window. Test seam."""
    with _lock:
        _state.clear()


def blocked_for(client: Any, now: Optional[float] = None) -> float:
    """Seconds left in the key's backoff window; 0 when it may call."""
    now = time.time() if now is None else now
    with _lock:
        entry = _state.get(key_for(client))
        if not entry:
            return 0.0
        return max(0.0, entry.get("blocked_until", 0.0) - now)


def note_success(client: Any) -> None:
    with _lock:
        _state.pop(key_for(client), None)


def _retry_after_seconds(response: Any, now: float) -> Optional[float]:
    header = None
    try:
        header = response.headers.get("Retry-After") if response is not None else None
    except Exception:  # noqa: BLE001 - a fake response in a test may have no headers
        header = None
    if not header:
        return None
    raw = str(header).strip()
    if raw.isdigit():
        return float(raw)
    try:
        when = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    return max(0.0, when.timestamp() - now)


def _arm(key: str, wait: Optional[float], now: float) -> float:
    with _lock:
        entry = _state.setdefault(key, {"blocked_until": 0.0, "consecutive": 0.0})
        entry["consecutive"] = entry.get("consecutive", 0.0) + 1
        if wait is None:
            wait = min(
                MAX_BACKOFF_SECONDS,
                BASE_BACKOFF_SECONDS * (2 ** (int(entry["consecutive"]) - 1)),
            )
        wait = max(0.0, min(float(wait), MAX_BACKOFF_SECONDS))
        entry["blocked_until"] = max(entry.get("blocked_until", 0.0), now + wait)
        return wait


def note_429(client: Any, response: Any = None, now: Optional[float] = None) -> float:
    """Arm the window from a 429. Returns the wait in seconds."""
    now = time.time() if now is None else now
    key = key_for(client)
    wait = _arm(key, _retry_after_seconds(response, now), now)
    logger.warning("Respond.io 429 for key %s; backing off %.0fs", key, wait)
    return wait


def note_failure(client: Any, now: Optional[float] = None) -> float:
    """Arm the doubling window from a 5xx or a transport error."""
    now = time.time() if now is None else now
    key = key_for(client)
    wait = _arm(key, None, now)
    logger.warning("Respond.io unreachable for key %s; backing off %.0fs", key, wait)
    return wait


def call(client: Any, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Run one Respond call under the key's backoff window (see the module docstring)."""
    remaining = blocked_for(client)
    if remaining > 0:
        raise RateLimited(key_for(client), remaining)
    try:
        result = fn(*args, **kwargs)
    except httpx.HTTPStatusError as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status == 429:
            wait = note_429(client, exc.response)
            raise RateLimited(key_for(client), wait) from exc
        if status is not None and status >= 500:
            note_failure(client)
        raise
    except (httpx.TransportError, TimeoutError, OSError):
        note_failure(client)
        raise
    note_success(client)
    return result
