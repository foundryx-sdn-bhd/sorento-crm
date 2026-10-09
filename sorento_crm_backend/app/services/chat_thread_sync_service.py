"""Delta sync between Respond.io and ``chat_histories`` (lane CHAT-LOCAL-FIRST, R2 + R4).

``documentation/plans/sla/PLAN-chat-local-first-30sep.md``. The thread renders from
``chat_histories`` (``conversation_thread_service.fetch_thread_page``); this module is
the only place that asks Respond.io for messages once a contact has local rows, and it
asks for exactly one page per call:

``sync_newer``
    One ``list_messages(cursorId=-<watermark>)`` read: everything Respond holds that is
    newer than the last message a Respond read confirmed (``newest_synced_message_id`` on
    the state row; the newest page with no cursor when there is none yet). The watermark
    is advanced ONLY here, never by a row n8n, the webhook or a CRM send wrote, so a
    message one of those lanes missed is still behind it and gets recovered, and rows they
    wrote after it are re-read and have their media / sender filled in. Stored through
    ``persist_messages``, then the open threads are poked on the event bus and a phone push
    is queued for a fresh incoming message (the same push the n8n ingest queues; deduped on
    the message id). Runs (a) in the background after a thread page was served, at most
    once per :data:`SYNC_MIN_INTERVAL_SECONDS` per contact, and (b) from the reconcile
    for every contact with recent activity.

``sync_older``
    One ``list_messages(cursorId=<oldest stored id>)`` read, when a reader scrolls past
    the oldest stored row. A short page marks ``oldest_reached`` so that contact is never
    asked for again. Runs inline in the scroll-back request: the page being asked for IS
    the data being fetched, so there is nothing to serve until it lands.

``run_reconcile``
    The body of the ``chat_history_reconcile`` scheduled task (seeded by migration
    clf_0001, driven by ``app/scheduler/task_scheduler.py``). The handler only enqueues it
    on the ``respond_io`` RQ queue (``app/tasks/chat_reconcile_tasks.py``): the scheduler
    heartbeat runs handlers one after another, and a tick of a few hundred Respond calls
    inline there would stall every other scheduled task behind it. ``sync_newer`` for every
    contact whose ``last_activity_at`` is within ``activity_days``, spaced out by how long
    the contact has been quiet (``_sync_due``), ``concurrency`` calls in flight per
    workspace key, backing off through ``respond_rate_limit``. Respond load therefore
    follows message activity, never the number of people looking.

The oldest stored id is read from ``chat_histories`` (a scroll-back continues from
whatever is oldest locally); the newest-side watermark lives on the state row because it
must NOT move when another lane writes a row.
"""
from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.chat_history import ChatHistory
from app.models.chat_thread_sync_state import ChatThreadSyncState
from app.services import conversation_event_bus, respond_rate_limit
from app.services import conversation_thread_service as thread_service
from app.services.conversation_thread_service import ThreadContact
from app.services.otp_redaction import otp_template_code_slots, redact_otp_payload

logger = logging.getLogger(__name__)

# A thread poll inside this window schedules nothing: the previous delta read is the
# answer. Respond's own inbox refreshes on a similar cadence.
SYNC_MIN_INTERVAL_SECONDS = 30
# Respond.io caps GET /v2/contact/{id}/message/list at 50 per page.
RESPOND_PAGE = 50
DEFAULT_ACTIVITY_DAYS = 7
DEFAULT_CONCURRENCY = 2
DEFAULT_BATCH_LIMIT = 500
# Reconcile spacing: a contact is re-read no more often than a quarter of the time it has
# been quiet (a contact active 10 min ago every tick, one quiet for 2 h every 30 min, one
# quiet for 6 days every 36 h), so a 7-day window does not cost one call per contact per
# tick. Only new activity, from any lane, brings a contact back to every tick.
RECONCILE_SPACING_DIVISOR = 4
# A message the sync stored first (n8n late or missing) gets the phone push n8n would have
# queued, but only when it is this fresh: a recovered week-old message buzzes nobody.
PUSH_FRESHNESS = timedelta(hours=1)
# Background sync threads per API process. Two is plenty: a job is one HTTP call and
# one small insert, and anything queued behind them is the same contact's next poll.
REQUEST_SYNC_WORKERS = 2


def _now() -> datetime:
    return datetime.now(tz=timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# What the rows say
# ---------------------------------------------------------------------------


def _with_id(db: Session, contact: ThreadContact):
    return thread_service._base_query(db, contact).filter(ChatHistory.message_id.isnot(None))


def oldest_stored(db: Session, contact: ThreadContact) -> Optional[ChatHistory]:
    return (
        _with_id(db, contact)
        .order_by(ChatHistory.sent_at.asc(), ChatHistory.id.asc())
        .first()
    )


def has_local_rows(db: Session, contact: ThreadContact) -> bool:
    return thread_service._base_query(db, contact).limit(1).first() is not None


# ---------------------------------------------------------------------------
# Sync state row
# ---------------------------------------------------------------------------


def get_state(db: Session, contact: ThreadContact) -> Optional[ChatThreadSyncState]:
    return (
        db.query(ChatThreadSyncState)
        .filter(
            ChatThreadSyncState.channel == contact.channel,
            ChatThreadSyncState.contact_id == contact.respond_io_id,
        )
        .first()
    )


def _ensure_state(db: Session, channel: str, contact_id: str) -> None:
    db.execute(
        text(
            """
            INSERT INTO chat_thread_sync_state (channel, contact_id)
            VALUES (:channel, :contact_id)
            ON CONFLICT (channel, contact_id) DO NOTHING
            """
        ),
        {"channel": channel, "contact_id": contact_id},
    )


def touch_activity(db: Session, channel: str, contact_id: str, at: Optional[datetime]) -> None:
    """A message landed for this contact (any lane). Keeps ``last_activity_at`` at the
    newest value seen; the caller owns the transaction."""
    at = at or _now()
    db.execute(
        text(
            """
            INSERT INTO chat_thread_sync_state (channel, contact_id, last_activity_at)
            VALUES (:channel, :contact_id, :at)
            ON CONFLICT (channel, contact_id) DO UPDATE SET
                last_activity_at = GREATEST(
                    COALESCE(chat_thread_sync_state.last_activity_at, EXCLUDED.last_activity_at),
                    EXCLUDED.last_activity_at
                ),
                updated_at = NOW()
            """
        ),
        {"channel": channel, "contact_id": contact_id, "at": at},
    )


def _mark_synced(db: Session, contact: ThreadContact, *, error: Optional[str] = None) -> None:
    _ensure_state(db, contact.channel, contact.respond_io_id)
    params = {
        "channel": contact.channel,
        "contact_id": contact.respond_io_id,
        "now": _now(),
        "error": (error or "")[:2000] or None,
    }
    if error:
        sql = """
            UPDATE chat_thread_sync_state
            SET last_error = :error, last_error_at = :now, updated_at = NOW()
            WHERE channel = :channel AND contact_id = :contact_id
        """
    else:
        sql = """
            UPDATE chat_thread_sync_state
            SET last_synced_at = :now, last_error = NULL, last_error_at = NULL,
                updated_at = NOW()
            WHERE channel = :channel AND contact_id = :contact_id
        """
    db.execute(text(sql), params)


def watermark(db: Session, contact: ThreadContact) -> Optional[str]:
    state = get_state(db, contact)
    return state.newest_synced_message_id if state is not None else None


def _advance_watermark(db: Session, contact: ThreadContact, items: list[dict]) -> None:
    """Move the Respond-side watermark to the newest id a Respond read returned. Only a
    Respond read may call this; a monotonic GREATEST so an older page never moves it back."""
    ids = [thread_service._message_id_to_ms(i.get("messageId")) for i in items]
    newest = max((int(str(i.get("messageId"))) for i, ms in zip(items, ids) if ms is not None), default=None)
    if newest is None:
        return
    _ensure_state(db, contact.channel, contact.respond_io_id)
    db.execute(
        text(
            """
            UPDATE chat_thread_sync_state
            SET newest_synced_message_id = CASE
                    WHEN newest_synced_message_id IS NULL
                      OR newest_synced_message_id::numeric < :newest THEN :newest_text
                    ELSE newest_synced_message_id END,
                updated_at = NOW()
            WHERE channel = :channel AND contact_id = :contact_id
            """
        ),
        {
            "channel": contact.channel,
            "contact_id": contact.respond_io_id,
            "newest": newest,
            "newest_text": str(newest),
        },
    )


def _set_oldest_reached(db: Session, contact: ThreadContact, reached: bool) -> None:
    _ensure_state(db, contact.channel, contact.respond_io_id)
    db.execute(
        text(
            """
            UPDATE chat_thread_sync_state
            SET oldest_reached = :reached, updated_at = NOW()
            WHERE channel = :channel AND contact_id = :contact_id
            """
        ),
        {"channel": contact.channel, "contact_id": contact.respond_io_id, "reached": reached},
    )


def note_first_page(
    db: Session, contact: ThreadContact, *, oldest_reached: bool, items: Optional[list[dict]] = None
) -> None:
    """The live page that filled an empty thread counts as its first sync: the watermark
    moves to its newest id, no delta read for the next interval, and a short newest page
    means the start was reached. Best-effort, commits its own work."""
    try:
        _mark_synced(db, contact)
        if items:
            _advance_watermark(db, contact, items)
        if oldest_reached:
            _set_oldest_reached(db, contact, True)
        db.commit()
    except Exception:  # noqa: BLE001 - bookkeeping never fails a read
        logger.debug("note_first_page failed for %s", contact.respond_io_id, exc_info=True)
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass


def oldest_reached(db: Session, contact: ThreadContact) -> bool:
    state = get_state(db, contact)
    return bool(state and state.oldest_reached)


def older_read_allowed(db: Session, contact: ThreadContact, now: Optional[datetime] = None) -> bool:
    """May a scroll-back make its inline older read now?

    Not once the start was reached, and not within :data:`SYNC_MIN_INTERVAL_SECONDS` of
    the last failed read: the newer side is throttled by the claim, and without this the
    older side of a contact whose key 404s or 401s on Respond would cost one live call per
    scroll-back, at whatever rate the viewer scrolls (security review, finding 4).
    """
    state = get_state(db, contact)
    if state is None:
        return True
    if state.oldest_reached:
        return False
    if state.last_error_at is None:
        return True
    now = now or _now()
    return (now - state.last_error_at) >= timedelta(seconds=SYNC_MIN_INTERVAL_SECONDS)


def should_sync_newer(db: Session, contact: ThreadContact, now: Optional[datetime] = None) -> bool:
    """False while the last delta read is younger than :data:`SYNC_MIN_INTERVAL_SECONDS`."""
    state = get_state(db, contact)
    if state is None or state.last_synced_at is None:
        return True
    now = now or _now()
    return (now - state.last_synced_at) >= timedelta(seconds=SYNC_MIN_INTERVAL_SECONDS)


def _claim_sync(db: Session, contact: ThreadContact, now: Optional[datetime] = None) -> bool:
    """Atomically take the next sync slot for this contact.

    The race guard between two API processes (or two tabs) that both decided to sync
    at once: the UPDATE only lands for the first one past the interval. Commits.
    """
    now = now or _now()
    _ensure_state(db, contact.channel, contact.respond_io_id)
    result = db.execute(
        text(
            """
            UPDATE chat_thread_sync_state
            SET last_synced_at = :now, updated_at = NOW()
            WHERE channel = :channel AND contact_id = :contact_id
              AND (last_synced_at IS NULL OR last_synced_at <= :cutoff)
            """
        ),
        {
            "channel": contact.channel,
            "contact_id": contact.respond_io_id,
            "now": now,
            "cutoff": now - timedelta(seconds=SYNC_MIN_INTERVAL_SECONDS),
        },
    )
    db.commit()
    return bool(result.rowcount)


# ---------------------------------------------------------------------------
# One Respond read
# ---------------------------------------------------------------------------


def _read_page(
    client: Any, contact: ThreadContact, *, cursor: Optional[str], otp_slots: Optional[dict]
) -> list[dict]:
    payload = respond_rate_limit.call(
        client, client.list_messages, contact.respond_io_id, limit=RESPOND_PAGE, cursor=cursor
    )
    redact_otp_payload(payload, otp_slots or {})
    items = payload.get("items") if isinstance(payload, dict) else None
    return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []


def _record_failure(db: Session, contact: ThreadContact, exc: Exception, what: str) -> dict:
    try:
        db.rollback()
    except Exception:  # noqa: BLE001
        pass
    try:
        _mark_synced(db, contact, error=f"{what}: {exc}")
        db.commit()
    except Exception:  # noqa: BLE001
        logger.debug("could not record sync error for %s", contact.respond_io_id, exc_info=True)
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
    rate_limited = isinstance(exc, respond_rate_limit.RateLimited)
    if not rate_limited:
        logger.warning(
            "chat thread %s failed for contact %s: %s", what, contact.respond_io_id, exc
        )
    return {"fetched": 0, "written": 0, "error": str(exc), "rate_limited": rate_limited}


def sync_newer(
    db: Session, contact: ThreadContact, client: Any, *, otp_slots: Optional[dict] = None
) -> dict:
    """ONE delta read newer than the Respond-side watermark. Never raises.

    No watermark yet (a contact whose rows all came from n8n before this lane, or whose
    rows were purged) reads the newest page with no cursor: that also fills the media and
    sender of the last 50 rows and heals any gap inside them.
    """
    if otp_slots is None:
        otp_slots = otp_template_code_slots(db)
    try:
        mark = watermark(db, contact)
        cursor = f"-{mark}" if mark else None
        items = _read_page(client, contact, cursor=cursor, otp_slots=otp_slots)
        written = thread_service.persist_messages(db, contact, items)
        _advance_watermark(db, contact, items)
        _mark_synced(db, contact)
        to_push = _fresh_incoming_ids(db, contact, items) if written else []
        db.commit()
    except Exception as exc:  # noqa: BLE001 - a sync must never break a read or a tick
        return _record_failure(db, contact, exc, "delta sync")
    if written:
        conversation_event_bus.publish(
            conversation_event_bus.EVENT_MESSAGE, contact_id=contact.respond_io_id
        )
        for row_id in to_push:
            _enqueue_push(row_id)
    return {"fetched": len(items), "written": written, "error": None, "rate_limited": False}


def _fresh_incoming_ids(db: Session, contact: ThreadContact, items: list[dict]) -> list[int]:
    """`chat_histories.id` of the incoming rows among ``items`` that are younger than
    :data:`PUSH_FRESHNESS` and were never pushed. The push itself dedupes on the Respond
    message id, so a row n8n already announced is queued again harmlessly."""
    ids = [str(i.get("messageId")) for i in items if i.get("messageId") is not None]
    if not ids:
        return []
    since = _now() - PUSH_FRESHNESS
    rows = (
        db.query(ChatHistory.id)
        .filter(
            ChatHistory.channel == contact.channel,
            ChatHistory.contact_id == contact.respond_io_id,
            ChatHistory.message_id.in_(ids),
            ChatHistory.type == "incoming",
            ChatHistory.sent_at >= since,
        )
        .all()
    )
    return [int(r[0]) for r in rows]


def _enqueue_push(row_id: int) -> None:
    """The same AC-M20 push the n8n ingest queues (best-effort, post-commit)."""
    try:
        from app.services.queue_service import enqueue_job
        from app.tasks import message_push_tasks

        enqueue_job(message_push_tasks.send_message_push, row_id, queue_name="notifications")
    except Exception as push_error:  # noqa: BLE001
        logger.warning("sync: failed to enqueue message push for chat_histories.id=%s: %s", row_id, push_error)


def sync_older(
    db: Session, contact: ThreadContact, client: Any, *, otp_slots: Optional[dict] = None
) -> dict:
    """ONE read older than the oldest stored row. Never raises.

    A short page is the start of the Respond thread: ``oldest_reached`` is set and no
    later scroll-back asks Respond for this contact again.
    """
    if otp_slots is None:
        otp_slots = otp_template_code_slots(db)
    try:
        oldest = oldest_stored(db, contact)
        if oldest is None:
            return {"fetched": 0, "written": 0, "error": None, "oldest_reached": False}
        items = _read_page(client, contact, cursor=str(oldest.message_id), otp_slots=otp_slots)
        written = thread_service.persist_messages(db, contact, items)
        reached = len(items) < RESPOND_PAGE
        _set_oldest_reached(db, contact, reached)
        _mark_synced(db, contact)
        db.commit()
    except Exception as exc:  # noqa: BLE001
        out = _record_failure(db, contact, exc, "older sync")
        out["oldest_reached"] = False
        return out
    return {"fetched": len(items), "written": written, "error": None, "oldest_reached": reached}


# ---------------------------------------------------------------------------
# Request-path scheduling (after the local page was served)
# ---------------------------------------------------------------------------


_executor: Optional[ThreadPoolExecutor] = None
_executor_lock = threading.Lock()
_inflight: set[tuple[str, str]] = set()
_inflight_lock = threading.Lock()
_runner: Optional[Callable[[ThreadContact], Any]] = None


def set_runner(runner: Optional[Callable[[ThreadContact], Any]]) -> None:
    """Swap how a scheduled sync runs. Test seam: pass a callable that runs inline (or
    just records the contact); None restores the thread pool."""
    global _runner
    _runner = runner


def _pool() -> ThreadPoolExecutor:
    global _executor
    with _executor_lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(
                max_workers=REQUEST_SYNC_WORKERS, thread_name_prefix="chat-sync"
            )
        return _executor


def _release(contact: ThreadContact) -> None:
    with _inflight_lock:
        _inflight.discard((contact.channel, contact.respond_io_id))


def run_sync_newer_job(contact: ThreadContact) -> dict:
    """The background job: own session, own client, the claim as the race guard."""
    from app.database import SessionLocal
    from app.models.base import set_company_scope
    from app.services.integration_service import RespondClient

    try:
        db = SessionLocal()
        try:
            # No principal here: fail-open on company scope like every scheduler tick.
            set_company_scope(db, None)
            if not _claim_sync(db, contact):
                return {"skipped": "claimed elsewhere"}
            client = RespondClient.for_identifier(db, contact.respond_io_id)
            if not getattr(client, "api_key", None):
                return {"skipped": "no api key"}
            return sync_newer(db, contact, client)
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001 - a thread must never die loudly
        logger.warning("chat thread sync job failed for %s: %s", contact.respond_io_id, exc)
        return {"error": str(exc)}
    finally:
        _release(contact)


def schedule_sync_newer(db: Session, contact: ThreadContact) -> bool:
    """Queue ONE delta read for this contact unless one ran within the interval or one
    is already queued in this process. Returns True when queued. Never raises."""
    try:
        if not should_sync_newer(db, contact):
            return False
    except Exception:  # noqa: BLE001 - the page is already answered
        logger.debug("sync state read failed for %s", contact.respond_io_id, exc_info=True)
        return False
    key = (contact.channel, contact.respond_io_id)
    with _inflight_lock:
        if key in _inflight:
            return False
        _inflight.add(key)
    runner = _runner
    try:
        if runner is not None:
            try:
                runner(contact)
            finally:
                _release(contact)
        else:
            _pool().submit(run_sync_newer_job, contact)
    except Exception:  # noqa: BLE001
        _release(contact)
        logger.warning("could not schedule chat thread sync for %s", contact.respond_io_id)
        return False
    return True


# ---------------------------------------------------------------------------
# Reconcile (scheduled task `chat_history_reconcile`)
# ---------------------------------------------------------------------------


def _task_int(metadata: Any, key: str, default: int, *, minimum: int = 1) -> int:
    try:
        value = int((metadata or {}).get(key, default))
    except (TypeError, ValueError, AttributeError):
        value = default
    return max(minimum, value)


def _sync_due(state: ChatThreadSyncState, now: datetime) -> bool:
    """Is this contact's next reconcile read due? Never synced: yes. Otherwise the gap
    since the last sync must be at least a quarter of the time the contact has been quiet
    (see RECONCILE_SPACING_DIVISOR)."""
    if state.last_synced_at is None:
        return True
    quiet = now - (state.last_activity_at or now)
    return (now - state.last_synced_at) >= quiet / RECONCILE_SPACING_DIVISOR


def active_contacts(
    db: Session, *, since: datetime, limit: int, now: Optional[datetime] = None
) -> list[ThreadContact]:
    """Contacts with a message in the window whose next read is due, newest activity
    first, as thread contacts.

    Phone and names come from ``respond_contacts`` where a row exists, else from the
    contact's newest chat row (``chat_histories`` carries them on every row).
    """
    from app.models.access import RespondContact

    now = now or _now()
    states = [
        s
        for s in (
            db.query(ChatThreadSyncState)
            .filter(ChatThreadSyncState.last_activity_at >= since)
            .order_by(ChatThreadSyncState.last_activity_at.desc())
            .all()
        )
        if _sync_due(s, now)
    ][:limit]
    if not states:
        return []
    ids = sorted({s.contact_id for s in states})
    by_id: dict[str, Any] = {}
    for row in db.query(RespondContact).filter(RespondContact.respond_io_id.in_(ids)).all():
        by_id[str(row.respond_io_id)] = row
    out: list[ThreadContact] = []
    for state in states:
        row = by_id.get(state.contact_id)
        if row is not None:
            out.append(
                ThreadContact(
                    respond_io_id=state.contact_id,
                    phone_number=str(getattr(row, "phone_number", "") or ""),
                    first_name=getattr(row, "first_name", None),
                    last_name=getattr(row, "last_name", None),
                    channel=state.channel,
                )
            )
            continue
        latest = (
            db.query(ChatHistory)
            .filter(
                ChatHistory.channel == state.channel,
                ChatHistory.contact_id == state.contact_id,
            )
            .order_by(ChatHistory.sent_at.desc(), ChatHistory.id.desc())
            .first()
        )
        if latest is None:
            continue
        out.append(
            ThreadContact(
                respond_io_id=state.contact_id,
                phone_number=latest.phone_number or "",
                first_name=latest.first_name,
                last_name=latest.last_name,
                channel=state.channel,
            )
        )
    return out


def _reconcile_one(contact: ThreadContact, client: Any, session_factory: Callable[[], Session]) -> dict:
    from app.models.base import set_company_scope

    db = session_factory()
    try:
        set_company_scope(db, None)
        if respond_rate_limit.blocked_for(client) > 0:
            return {"fetched": 0, "written": 0, "error": "rate limited", "rate_limited": True}
        return sync_newer(db, contact, client)
    finally:
        db.close()


def run_reconcile(
    db: Session,
    task: Any = None,
    *,
    client_for: Optional[Callable[[Session, ThreadContact], Any]] = None,
    session_factory: Optional[Callable[[], Session]] = None,
    now: Optional[datetime] = None,
) -> dict:
    """The tick body. ``client_for`` and ``session_factory`` are test seams."""
    metadata = getattr(task, "metadata_", None) if task is not None else None
    days = _task_int(metadata, "activity_days", DEFAULT_ACTIVITY_DAYS)
    concurrency = _task_int(metadata, "concurrency", DEFAULT_CONCURRENCY)
    batch_limit = _task_int(metadata, "batch_limit", DEFAULT_BATCH_LIMIT)
    now = now or _now()

    if client_for is None:
        from app.services.integration_service import RespondClient

        def client_for(session: Session, contact: ThreadContact):  # type: ignore[no-redef]
            return RespondClient.for_identifier(session, contact.respond_io_id)

    if session_factory is None:
        from app.database import SessionLocal

        session_factory = SessionLocal

    contacts = active_contacts(db, since=now - timedelta(days=days), limit=batch_limit, now=now)

    # Group by API key so the concurrency cap is per workspace token, not global.
    groups: dict[str, tuple[Any, list[ThreadContact]]] = {}
    no_key = 0
    for contact in contacts:
        client = client_for(db, contact)
        if not getattr(client, "api_key", None):
            no_key += 1
            continue
        key = respond_rate_limit.key_for(client)
        groups.setdefault(key, (client, []))[1].append(contact)

    summary = {
        "selected": len(contacts),
        "synced": 0,
        "written": 0,
        "errors": 0,
        "rate_limited": 0,
        "no_api_key": no_key,
        "activity_days": days,
        "concurrency": concurrency,
    }
    for _key, (client, members) in groups.items():
        with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="chat-reconcile") as pool:
            results = list(
                pool.map(lambda c: _reconcile_one(c, client, session_factory), members)
            )
        for result in results:
            if result.get("rate_limited"):
                summary["rate_limited"] += 1
            elif result.get("error"):
                summary["errors"] += 1
            else:
                summary["synced"] += 1
                summary["written"] += int(result.get("written") or 0)
    return summary
