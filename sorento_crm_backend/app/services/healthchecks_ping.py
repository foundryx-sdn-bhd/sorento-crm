"""Heartbeat pings to Healthchecks.io (HA-MONITOR).

An outside service that alerts when a ping stops arriving. The ping must never be
able to hurt the caller: no URL means nothing happens, the HTTP call has a short
timeout, it runs on its own daemon thread (the caller never waits on it), and every
error is swallowed into a log line. The ping URL itself is never logged, because
anyone holding it can fake a heartbeat. That is why this uses urllib and not httpx:
httpx logs every request's full URL at INFO, and the worker logs INFO.

Runbook: documentation/runbooks/monitoring.md.
"""
from __future__ import annotations

import logging
import threading
import urllib.request

logger = logging.getLogger(__name__)

PING_TIMEOUT_SECONDS = 5.0


def _send(url: str) -> bool:
    """One GET to the ping URL. True on a 2xx answer; never raises."""
    try:
        with urllib.request.urlopen(url, timeout=PING_TIMEOUT_SECONDS) as response:
            status = response.status
    except Exception as exc:
        # Only the type: an HTTPError's text carries the URL.
        logger.warning("Healthchecks ping failed: %s", type(exc).__name__)
        return False
    if not 200 <= status < 300:
        logger.warning("Healthchecks ping answered HTTP %s", status)
        return False
    return True


def ping(url: str | None, *, fail: bool = False) -> threading.Thread | None:
    """Fire one ping in the background and return at once.

    ``fail=True`` hits ``<url>/fail``, which Healthchecks reports as down straight away
    instead of waiting for the grace period. Returns the thread (tests join it), or None
    when nothing was sent.
    """
    url = (url or "").strip()
    if not url:
        return None
    if fail:
        url = url.rstrip("/") + "/fail"
    try:
        thread = threading.Thread(target=_send, args=(url,), name="healthchecks-ping", daemon=True)
        thread.start()
    except Exception as exc:
        logger.warning("Healthchecks ping not started: %s", type(exc).__name__)
        return None
    return thread
