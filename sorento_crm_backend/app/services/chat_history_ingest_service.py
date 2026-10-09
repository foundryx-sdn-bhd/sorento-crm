"""The one row writer every mirror lane uses (lane CHAT-LOCAL-FIRST, R3).

Three lanes now write a Respond message into ``chat_histories`` as it happens: n8n
(``POST /api/v1/external/chat-history/messages``), Respond.io's own webhook
(``POST /api/v1/public/respond/webhook``) and the CRM's own send mirror. The first
two land here, so the dedupe, the fill-if-null merge and the post-commit announce
(event bus poke, phone push) are one implementation - a second copy of the ON
CONFLICT clause is how a dedupe silently stops working.

``upsert_message_row`` commits. ``announce_new_row`` runs AFTER that commit and never
raises: a post-commit side effect that fails must not turn a stored message into a
500 that makes the caller retry the lane this endpoint exists to end.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.chat_history import CHAT_HISTORY_DEDUPE_PREDICATE
from app.services import conversation_event_bus
from app.services.chat_message_resolver import respond_ts_from_message_id
from app.services.otp_redaction import mask_otp_text

logger = logging.getLogger(__name__)


@dataclass
class ChatMessageRow:
    channel: str
    contact_id: str
    phone_number: str
    message: str
    sent_at: datetime
    type: str
    message_id: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    result: Optional[list] = None
    reply_to_message_id: Optional[str] = None
    reply_to_message: Optional[str] = None
    turn_id: Optional[str] = None
    state_trace: Optional[dict] = None
    media_url: Optional[str] = None
    media_type: Optional[str] = None
    media_file_name: Optional[str] = None
    sender_source: Optional[str] = None
    sender_user_id: Optional[str] = None
    # QUOTED-REPLY R1: the engine turn an outgoing row carries (chat_history_turn_link).
    chatbot_turn_id: Optional[str] = None


# AC-J5: a CRM drawer send reaches the ingest TWICE (the direct respond-send-user
# webhook lane and Respond's own outgoing-message trigger), and with the direct
# webhook a WhatsApp message reaches it from two feeds, so the same message must
# resolve to one row whichever lane wins the race. The conflict target is the partial
# unique index from migration 326; its predicate is repeated verbatim because Postgres
# infers the arbiter index from it (CHAT_HISTORY_DEDUPE_PREDICATE). Rows with no
# message_id are outside the index and keep inserting exactly as before.
#
# DO UPDATE, not DO NOTHING: DO NOTHING returns no row (so the caller could not be
# told which row its message is), and the losing lane often carries context the
# winner lacked (turn_id, the quoted message, the attachment). Fill-if-null only: a
# mirror re-states a message, it never edits it.
_UPSERT_SQL = text(
    f"""
    INSERT INTO chat_histories (
        channel, contact_id, phone_number, message, sent_at, first_name, last_name, type,
        message_id, result, reply_to_message_id, reply_to_message, turn_id, ingest_at,
        respond_ts, state_trace, media_url, media_type, media_file_name, sender_source,
        sender_user_id, chatbot_turn_id
    ) VALUES (
        :channel, :contact_id, :phone_number, :message, :sent_at, :first_name, :last_name, :type,
        :message_id, :result, :reply_to_message_id, :reply_to_message, :turn_id, :ingest_at,
        :respond_ts, :state_trace, :media_url, :media_type, :media_file_name, :sender_source,
        :sender_user_id, :chatbot_turn_id
    )
    ON CONFLICT (contact_id, message_id) WHERE {CHAT_HISTORY_DEDUPE_PREDICATE}
    DO UPDATE SET
        first_name = COALESCE(chat_histories.first_name, EXCLUDED.first_name),
        last_name = COALESCE(chat_histories.last_name, EXCLUDED.last_name),
        result = COALESCE(chat_histories.result, EXCLUDED.result),
        reply_to_message_id = COALESCE(
            chat_histories.reply_to_message_id, EXCLUDED.reply_to_message_id
        ),
        reply_to_message = COALESCE(
            chat_histories.reply_to_message, EXCLUDED.reply_to_message
        ),
        turn_id = COALESCE(chat_histories.turn_id, EXCLUDED.turn_id),
        respond_ts = COALESCE(chat_histories.respond_ts, EXCLUDED.respond_ts),
        state_trace = COALESCE(chat_histories.state_trace, EXCLUDED.state_trace),
        media_url = COALESCE(chat_histories.media_url, EXCLUDED.media_url),
        media_type = COALESCE(chat_histories.media_type, EXCLUDED.media_type),
        media_file_name = COALESCE(chat_histories.media_file_name, EXCLUDED.media_file_name),
        sender_source = COALESCE(chat_histories.sender_source, EXCLUDED.sender_source),
        sender_user_id = COALESCE(chat_histories.sender_user_id, EXCLUDED.sender_user_id),
        chatbot_turn_id = COALESCE(chat_histories.chatbot_turn_id, EXCLUDED.chatbot_turn_id)
    RETURNING id, (xmax <> 0) AS already_existed
    """
)


def upsert_message_row(db: Session, row: ChatMessageRow) -> tuple[int, bool]:
    """Write (or resolve) the row. Returns ``(chat_histories.id, already_existed)``.
    Commits; raises on a database failure (the caller rolls back and maps it)."""
    from app.services import chat_thread_sync_service

    result = db.execute(
        _UPSERT_SQL,
        {
            "channel": row.channel,
            "contact_id": row.contact_id,
            "phone_number": row.phone_number,
            # Reviewer B2 (#1280): the mirror never stores an OTP code.
            "message": mask_otp_text(row.message) or "",
            "sent_at": row.sent_at,
            "first_name": row.first_name,
            "last_name": row.last_name,
            "type": row.type,
            "message_id": row.message_id,
            "result": json.dumps(row.result) if row.result is not None else None,
            "reply_to_message_id": row.reply_to_message_id,
            "reply_to_message": mask_otp_text(row.reply_to_message),
            "turn_id": row.turn_id,
            # Our clock at ingest. Never the SLA clock - its only job is to make
            # webhook lag (ingest_at - respond_ts) separable from agent time.
            "ingest_at": datetime.now(tz=timezone.utc).replace(tzinfo=None),
            # Respond's `messageId` IS the message's epoch-microsecond timestamp,
            # so the SLA clock is already in this payload. Null when the id isn't a
            # plausible timestamp; the resolver still backstops those rows.
            "respond_ts": respond_ts_from_message_id(row.message_id, sent_at=row.sent_at),
            # `is not None`, NOT truthiness: a `{}` trace (or `{"after": null}`)
            # must round-trip, so the guard must not be what drops it.
            "state_trace": json.dumps(row.state_trace) if row.state_trace is not None else None,
            "media_url": row.media_url,
            "media_type": row.media_type,
            "media_file_name": row.media_file_name,
            "sender_source": row.sender_source,
            "sender_user_id": row.sender_user_id,
            "chatbot_turn_id": row.chatbot_turn_id,
        },
    )
    out = result.one()
    message_pk = int(out.id)
    already_existed = bool(out.already_existed)
    # R4: activity from this lane keeps the contact in the reconcile window.
    chat_thread_sync_service.touch_activity(db, row.channel, row.contact_id, row.sent_at)
    db.commit()
    return message_pk, already_existed


def announce_new_row(db: Session, *, message_pk: int, contact_id: str, traffic: str) -> None:
    """Post-commit side effects for a row that did NOT exist before. Never raises.

    AC-K1: poke every drawer open on this contact so the thread refetches within
    seconds instead of waiting for its slow poll. NOT on the dedupe path (AC-K4/AC-J5):
    the second lane resolves a row the first one already announced. `contact_id` IS
    the Respond.io contact id the bus keys on.

    AC-M20: buzz the phones of whoever asked to hear from this contact, on the
    `notifications` queue, best-effort. Only for INCOMING traffic (AC-M11): an
    outgoing message buzzes nobody, so it is filtered here rather than paying for a
    job and a database session per agent reply.
    """
    _ = db
    conversation_event_bus.publish(conversation_event_bus.EVENT_MESSAGE, contact_id=contact_id)
    try:
        # Off the same constant the push service compares against, so the two cannot
        # drift onto different spellings of "from the contact".
        from app.services.message_push_service import INBOUND_TYPE
        from app.services.queue_service import enqueue_job
        from app.tasks import message_push_tasks

        if str(traffic or "").lower() == INBOUND_TYPE:
            enqueue_job(message_push_tasks.send_message_push, message_pk, queue_name="notifications")
    except Exception as push_error:  # noqa: BLE001
        logger.warning(
            "Failed to enqueue message push for chat_histories.id=%s: %s", message_pk, push_error
        )


def sent_at_from_epoch_ms(epoch_ms: Any) -> datetime:
    raw = int(epoch_ms)
    if raw <= 0:
        raise ValueError("sent_at must be a valid epoch milliseconds value.")
    return datetime.fromtimestamp(raw / 1000, tz=timezone.utc).replace(tzinfo=None)
