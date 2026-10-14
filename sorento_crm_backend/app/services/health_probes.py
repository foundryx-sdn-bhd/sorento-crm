"""Redis and scheduler probes for `/health` and `/health/deep` (HA-MEM-DEPLOY-GUARD).

`/health` only REPORTS them: its status code is blue/green's readiness gate and stays on the
database alone. `/health/deep` (monitors) fails on them. Every probe is bounded, and the
bodies carry states, never error text (no hostnames or URLs on an unauthenticated route).
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import Optional

import anyio
import redis
from sqlalchemy import func

from app.config import settings

# Per probe, end to end. Well inside the image HEALTHCHECK's 10s timeout.
PROBE_TIMEOUT_SECONDS = 2.0
# One refresh per window per process, however often the routes are called: the routes are
# unauthenticated, and a flood must not turn into Redis connections and DB sessions.
CACHE_SECONDS = 10.0

_cache: dict = {"at": 0.0, "value": None}
_lock: Optional[asyncio.Lock] = None


def redis_state() -> str:
    client = redis.from_url(settings.redis_url, socket_timeout=1, socket_connect_timeout=1)
    try:
        client.ping()
        return "ok"
    except Exception:  # noqa: BLE001 - a probe reports, it never raises
        return "down"
    finally:
        client.close()


def scheduler_state(db, now: Optional[datetime] = None) -> dict:
    """The worker's scheduler, judged by the same overdue rule as the health dashboard and
    the watchdog (`get_overdue_tasks`): if the heartbeat stops, enabled tasks go overdue."""
    from app.models.scheduled_task import ScheduledTask
    from app.services.scheduled_task_service import get_overdue_tasks

    overdue = get_overdue_tasks(db, now)
    last = (
        db.query(func.max(ScheduledTask.last_run_at))
        .filter(ScheduledTask.enabled.is_(True))
        .scalar()
    )
    return {
        "status": "overdue" if overdue else "ok",
        "overdue_count": len(overdue),
        "last_task_run_at": last.isoformat() if last else None,
    }


def database_state() -> str:
    from sqlalchemy import text

    from app import database

    try:
        db = database.SessionLocal()
        try:
            db.execute(text("SELECT 1"))
        finally:
            db.close()
        return "ok"
    except Exception:  # noqa: BLE001
        logging.warning("health: database probe failed", exc_info=True)
        return "down"


def _scheduler_with_own_session() -> dict:
    from app import database

    db = database.SessionLocal()
    try:
        return scheduler_state(db)
    finally:
        db.close()


async def _bounded(fn, failed):
    # abandon_on_cancel: a hung probe's thread is left to finish on its own instead of being
    # waited for, so the response goes out at the deadline.
    try:
        with anyio.fail_after(PROBE_TIMEOUT_SECONDS):
            return await anyio.to_thread.run_sync(fn, abandon_on_cancel=True)
    except TimeoutError:
        return failed("timeout")
    except Exception:  # noqa: BLE001
        return failed("error")


async def bounded_database_state() -> str:
    return await _bounded(lambda: database_state(), lambda s: s)


async def collect() -> dict:
    """{"redis": state, "scheduler": {...}}, each bounded by PROBE_TIMEOUT_SECONDS.

    Looked up on the module at call time so tests can swap a probe out."""
    redis_result, scheduler_result = await asyncio.gather(
        _bounded(lambda: redis_state(), lambda s: s),
        _bounded(
            lambda: _scheduler_with_own_session(),
            lambda s: {"status": s, "overdue_count": None, "last_task_run_at": None},
        ),
    )
    return {"redis": redis_result, "scheduler": scheduler_result}


async def snapshot() -> dict:
    """{"database", "redis", "scheduler"}, refreshed at most once per CACHE_SECONDS. The lock
    makes concurrent callers wait for the one refresh (at most ~PROBE_TIMEOUT_SECONDS)."""
    global _lock
    if _lock is None:
        _lock = asyncio.Lock()
    async with _lock:
        if _cache["value"] is not None and time.monotonic() - _cache["at"] < CACHE_SECONDS:
            return _cache["value"]
        database, probes = await asyncio.gather(bounded_database_state(), collect())
        _cache.update(at=time.monotonic(), value={"database": database, **probes})
        return _cache["value"]


def reset_cache() -> None:
    global _lock
    _cache.update(at=0.0, value=None)
    _lock = None
