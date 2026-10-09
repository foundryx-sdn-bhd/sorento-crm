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


def sweep_chat_facts(db: Any, *, since: datetime, until: datetime | None = None) -> int:
    """Fill the chat analytics fact rows of every closed live turn in `[since, until)` that
    has none (CA-1). Returns the rows written.

    A doorway for the two callers that sit outside the chatbot package, the nightly
    scheduler tick and `scripts/chat_facts_sweep.py`: neither may import
    `app.services.chatbot`, and neither needs more than this one call. The writer is
    idempotent, so a repeat over the same range writes nothing.
    """
    from app.services.chatbot.analytics import facts

    return facts.sweep(db, since=since, until=until)
