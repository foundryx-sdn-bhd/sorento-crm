"""RQ body of the ``chat_history_reconcile`` scheduled task (lane CHAT-LOCAL-FIRST, R4).

The scheduler heartbeat (``scheduled_task_service.run_due_tasks``) runs handlers one after
another every 10 s, so a tick of a few hundred Respond.io calls inline there would stall
the deferred-delete commit, the takeover commit and the notification drainer behind it.
The handler therefore only enqueues this job on the ``respond_io`` queue (the worker drains
it, see CLAUDE.md "Worker is required") and returns at once.

One run at a time: a Redis lock (``SET NX EX``) makes an overlapping enqueue a no-op, so a
slow Respond never stacks ticks. Its TTL is the job timeout, so a killed run frees itself.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

QUEUE_NAME = "respond_io"
LOCK_KEY = "sorento:chat-history-reconcile:lock"
JOB_TIMEOUT_SECONDS = 20 * 60


def enqueue_reconcile(metadata: Optional[dict]) -> dict:
    """Called by the scheduled-task handler. Returns what the run log shows."""
    from app.services.queue_service import enqueue_job

    job = enqueue_job(
        run_chat_history_reconcile,
        dict(metadata or {}),
        queue_name=QUEUE_NAME,
        job_timeout=JOB_TIMEOUT_SECONDS,
    )
    return {"enqueued": getattr(job, "id", None), "queue": QUEUE_NAME}


def run_chat_history_reconcile(metadata: Optional[dict] = None) -> dict:
    """RQ entry point. Owns its session; never raises past the lock release."""
    from types import SimpleNamespace

    from app.database import SessionLocal
    from app.models.base import set_company_scope
    from app.services.chat_thread_sync_service import run_reconcile
    from app.services.queue_service import redis_conn

    try:
        acquired = redis_conn.set(LOCK_KEY, "1", nx=True, ex=JOB_TIMEOUT_SECONDS)
    except Exception as exc:  # noqa: BLE001 - no broker, no lock, but the run still happens
        logger.warning("chat reconcile: lock unavailable (%s); running unguarded", exc)
        acquired = True
    if not acquired:
        logger.info("chat reconcile: a run is still in progress; skipping this tick")
        return {"skipped": "in progress"}

    db = SessionLocal()
    try:
        set_company_scope(db, None)
        summary = run_reconcile(db, SimpleNamespace(metadata_=metadata or {}))
        logger.info("chat reconcile: %s", summary)
        return summary
    finally:
        db.close()
        try:
            redis_conn.delete(LOCK_KEY)
        except Exception:  # noqa: BLE001
            pass


_ = Any
