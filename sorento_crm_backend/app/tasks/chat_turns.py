"""The chatbot turn as an RQ job (AC-703).

`CHATBOT_TURN_ON_WORKER` moves the turn off the API thread and onto the `chat` queue; the
request still waits for it and still answers n8n with the finished turn. Nothing about the
turn changes here - this module is the seam that lets the work run in a different process,
which is why it is twenty lines and holds no logic of its own.

**One of the three files allowed to import `app.services.chatbot`**
(`tests/chatbot/test_import_boundary.py`). It is a doorway, not a caller with opinions.

`offload=False` on the call below is load-bearing: without it the worker would read the
same flag the API read, decide to offload, and enqueue the turn to the queue it is
currently draining - forever, at whatever rate redis can accept.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

# The queue name lives on the engine (`engine.CHAT_QUEUE`) so the enqueue site and the
# worker's registry cannot drift; re-exported here for anything reading the task module.
CHAT_QUEUE = "chat"

#: The nightly topic labeller's queue (chat analytics CA-2), drained by the batch worker.
ANALYTICS_QUEUE = "analytics"


def run_turn_job(envelope: dict[str, Any]) -> dict[str, Any]:
    """Run one turn on the worker and return what the waiting request will answer with.

    The argument is the envelope as JSON (RQ pickles the arguments; a plain dict travels
    where a pydantic model's version-coupled pickle would be a deploy hazard), and the
    return is `TurnResult.as_dict()` plus the three fields the endpoint reads off the
    result object rather than the response body.
    """
    from app.database import SessionLocal
    from app.services.chatbot import run_turn
    from app.services.chatbot.contracts import Envelope

    result = run_turn(Envelope(**envelope), session_factory=SessionLocal, offload=False)
    payload = result.as_dict()
    # `as_dict` is the RESPONSE shape; the waiting request rebuilds a full `TurnResult`,
    # and the endpoint's own logging reads `status` / `stage` / `error` off it. Sending
    # them keeps an offloaded failure as legible as an in-process one.
    payload.update({"status": result.status, "stage": result.stage, "error": result.error})
    logger.info(
        "chatbot turn %s ran on the worker: status=%s stage=%s",
        payload.get("turn_id"),
        result.status,
        result.stage,
    )
    return payload


def sweep_chat_facts(
    db: Any, *, since: datetime, until: datetime | None = None, rebuild: bool = False
) -> int:
    """Fill the chat analytics fact rows of every closed live turn in `[since, until)` that
    has none (CA-1). Returns the rows written.

    A doorway for the two callers that sit outside the chatbot package, the nightly
    scheduler tick and `scripts/chat_facts_sweep.py`: neither may import
    `app.services.chatbot`, and neither needs more than this one call. The writer is
    idempotent, so a repeat over the same range writes nothing. `rebuild` first deletes the
    range's fact rows and reloads them under the current rules (after a rule change).
    """
    from app.services.chatbot.analytics import facts

    return facts.sweep(db, since=since, until=until, rebuild=rebuild)


def label_chat_topics_job(limit: int | None = None) -> dict[str, Any]:
    """The nightly topic labeller as an RQ job on `analytics` (CA-2): label every ask with no
    topic or a stale taxonomy version. A doorway like the sweep above; the rules live in
    `app.services.chatbot.analytics.labeller`. Does nothing until taxonomy v1 has leaves."""
    from app.database import SessionLocal
    from app.services.chatbot.analytics import labeller

    db = SessionLocal()
    try:
        chat, model = labeller.production_chat(db)
        result = labeller.run(
            db, chat=chat, resolve=labeller.production_resolve(SessionLocal), model=model, limit=limit
        )
    finally:
        db.close()
    logger.info("chat topic labeller: %s", result)
    return result


def backfill_chat_history_job(
    era: str, *, limit: int | None = None, estimate: bool = False, relabel: bool = False
) -> dict[str, Any]:
    """The CA-4 history backfill of one era, in this process (no schedule: run by hand from
    `scripts/chat_backfill.py`). `estimate` counts and projects tokens and calls no model. A
    doorway like the labeller above; the rules live in
    `app.services.chatbot.analytics.backfill`."""
    from app.database import SessionLocal
    from app.services.chatbot.analytics import backfill, labeller

    db = SessionLocal()
    try:
        if estimate:
            result = backfill.estimate(db, era, relabel=relabel)
        else:
            chat, model = labeller.production_call(
                db, schema_name="chat_history_backfill", cache_key="chat-history-backfill"
            )
            result = backfill.run(
                db,
                era=era,
                chat=chat,
                resolve=labeller.production_resolve(SessionLocal),
                model=model,
                limit=limit,
                relabel=relabel,
            )
    finally:
        db.close()
    logger.info("chat history backfill: %s", result)
    return result


def enqueue_chat_topic_labeller() -> Any:
    """Queue `label_chat_topics_job` on `analytics` (the scheduler's 02:15 tick)."""
    from app.services.queue_service import enqueue_job

    return enqueue_job(label_chat_topics_job, queue_name=ANALYTICS_QUEUE, job_timeout=3 * 3600)
