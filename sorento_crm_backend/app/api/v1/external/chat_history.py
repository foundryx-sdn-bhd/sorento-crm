"""External API for channel-based chat history ingestion and retrieval."""
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_external_api_user
from app.models.chat_history import CHAT_HISTORY_DEDUPE_PREDICATE
from app.schemas.external.chat_history import (
    ChatHistoryMessageIngestRequest,
    ChatHistoryMessageIngestResponse,
    ChatHistoryMessageItem,
    ChatHistoryMessagesRequest,
    ChatHistoryMessagesResponse,
)
from app.schemas.integration import IntegrationLogCreate
from app.schemas.ticket_comment import (
    TicketCommentIngestRequest,
    TicketCommentIngestResponse,
    TicketCommentExternalCreate,
    TicketCommentExternalResponse,
)
from app.models.access import RespondContact
from app.services import conversation_event_bus
from app.services.chat_message_resolver import respond_ts_from_message_id
from app.services.otp_redaction import mask_otp_text
from app.services.integration_service import (
    IntegrationLogService,
    sanitize_request_headers,
)
from app.services.ticket_comment_service import TicketCommentService

logger = logging.getLogger(__name__)

router = APIRouter()


def _coerce_result_list(raw) -> list | None:
    """Coerce a stored jsonb `result` value into a list (None when absent/malformed)."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return None
    return raw if isinstance(raw, list) else None


def _safe_utc_datetime_from_epoch_ms(epoch_ms: int) -> datetime:
    if epoch_ms <= 0:
        raise ValueError("sent_at must be a valid epoch milliseconds value.")
    return datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc).replace(tzinfo=None)


@router.post("/messages", response_model=ChatHistoryMessageIngestResponse, status_code=status.HTTP_201_CREATED)
def ingest_chat_message(
    payload: ChatHistoryMessageIngestRequest,
    request: Request,
    current_user: dict = Depends(get_external_api_user),
    db: Session = Depends(get_db),
):
    """Ingest one chat message from n8n into local storage.

    Idempotent on the Respond ``message_id`` for a given contact (AC-J5): a
    message mirrored twice resolves to the one row and answers with the same id
    and ``status: "duplicate"``. A payload with no ``message_id`` has nothing to
    dedupe on and always inserts, as it always did.
    """
    _ = current_user
    try:
        sent_at = _safe_utc_datetime_from_epoch_ms(payload.sent_at)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e

    # AC-J5: a CRM drawer send reaches this endpoint TWICE - once from the direct
    # respond-send-user webhook lane, once from Respond's own outgoing-message
    # trigger - so the same WhatsApp message must resolve to one row whichever
    # lane wins the race. The conflict target is the partial unique index from
    # migration 326; its predicate is repeated verbatim because Postgres infers
    # the arbiter index from it (see CHAT_HISTORY_DEDUPE_PREDICATE). Rows with no
    # message_id are outside the index and keep inserting exactly as before.
    #
    # DO UPDATE, not DO NOTHING: DO NOTHING returns no row (so the caller could
    # not be told which row its message is), and the losing lane often carries
    # context the winner lacked (turn_id, the quoted message). Fill-if-null only:
    # a mirror re-states a message, it never edits it.
    insert_query = text(
        f"""
        INSERT INTO chat_histories (
            channel, contact_id, phone_number, message, sent_at, first_name, last_name, type,
            message_id, result, reply_to_message_id, reply_to_message, turn_id, ingest_at,
            respond_ts, state_trace, chatbot_turn_id
        ) VALUES (
            :channel, :contact_id, :phone_number, :message, :sent_at, :first_name, :last_name, :type,
            :message_id, :result, :reply_to_message_id, :reply_to_message, :turn_id, :ingest_at,
            :respond_ts, :state_trace, :chatbot_turn_id
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
            chatbot_turn_id = COALESCE(chat_histories.chatbot_turn_id, EXCLUDED.chatbot_turn_id)
        RETURNING id, (xmax <> 0) AS already_existed
        """
    )

    # QUOTED-REPLY R1: an outgoing row names the engine turn it carries. Never fails the ingest.
    chatbot_turn_id: str | None = None
    if str(payload.type or "").lower() == "outgoing":
        try:
            from app.services.chatbot.quoted import link_outgoing_row

            chatbot_turn_id = link_outgoing_row(
                db,
                contact_id=payload.contact_id,
                turn_id=payload.turn_id,
                message=payload.message,
                sent_at=sent_at,
                explicit=payload.chatbot_turn_id,
            )
        except Exception as link_error:  # noqa: BLE001
            db.rollback()
            logger.warning("chatbot_turn_id link failed: %s", link_error, exc_info=True)
            chatbot_turn_id = None

    message_id: int | None = None
    already_existed = False
    status_code = status.HTTP_201_CREATED
    error_message: str | None = None

    try:
        result = db.execute(
            insert_query,
            {
                "channel": payload.channel,
                "contact_id": payload.contact_id,
                "phone_number": payload.phone_number,
                # Reviewer B2 (#1280): the mirror never stores an OTP code.
                "message": mask_otp_text(payload.message),
                "sent_at": sent_at,
                "first_name": payload.first_name,
                "last_name": payload.last_name,
                "type": payload.type,
                "message_id": payload.message_id,
                "result": json.dumps(payload.result) if payload.result is not None else None,
                "reply_to_message_id": payload.reply_to_message_id,
                "reply_to_message": mask_otp_text(payload.reply_to_message),
                "turn_id": payload.turn_id,
                # Our clock at ingest. Never the SLA clock - its only job is to make
                # webhook lag (ingest_at - respond_ts) separable from agent time.
                "ingest_at": datetime.now(tz=timezone.utc).replace(tzinfo=None),
                # Respond's `messageId` IS the message's epoch-microsecond timestamp,
                # so the SLA clock is already in this payload - no resolver round trip
                # needed for it. Null when the id isn't a plausible timestamp; the
                # resolver still backstops those rows.
                "respond_ts": respond_ts_from_message_id(
                    payload.message_id, sent_at=sent_at
                ),
                # `is not None`, NOT truthiness: a `{}` trace (or `{"after": null}`)
                # must round-trip, so the guard must not be what drops it. json.dumps
                # of {"after": None} emits "after": null - the signal the view keys on.
                "state_trace": json.dumps(payload.state_trace)
                if payload.state_trace is not None
                else None,
                "chatbot_turn_id": chatbot_turn_id,
            },
        )
        row = result.one()
        message_id = row.id
        already_existed = bool(row.already_existed)
        db.commit()
    except Exception as e:
        db.rollback()
        logger.exception("Failed to insert chat history: %s", e)
        status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        error_message = "Failed to ingest chat history message."

    try:
        # Never persist a credential in an integration log: one helper, one denylist.
        request_headers = sanitize_request_headers(dict(request.headers))

        IntegrationLogService(db).create_integration_log(
            IntegrationLogCreate(
                integration_channel="n8n",
                business_table="chat_histories",
                business_id=str(uuid.uuid4()),
                external_reference=str(payload.contact_id),
                direction="inbound",
                endpoint=str(request.url.path),
                http_method=request.method,
                request_headers=json.dumps(request_headers),
                # Exclude state_trace: it is already persisted as jsonb on the row.
                # integration_logs has no purge, so logging it here would store the
                # whole trace a second time, as text, write-only, forever.
                request_payload=payload.model_dump_json(exclude={"state_trace", "chatbot_turn_id"}),
                status_code=status_code,
                status="success" if status_code < 400 else "failed",
                error_message=error_message,
            )
        )
    except Exception as log_error:
        logger.warning("Failed to create integration log for chat history ingestion: %s", log_error, exc_info=True)

    if message_id is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to ingest chat history message.",
        )

    # AC-K1: poke every drawer open on this contact so the thread refetches
    # within seconds instead of waiting for its slow poll. NOT on the dedupe
    # path (AC-K4/AC-J5): the second mirror lane resolves the row the first one
    # already announced, so a second poke would be a wasted refetch on every
    # open drawer. `payload.contact_id` IS the Respond.io contact id the bus
    # keys on, so nothing is resolved here.
    if not already_existed:
        conversation_event_bus.publish(
            conversation_event_bus.EVENT_MESSAGE, contact_id=payload.contact_id
        )

        # AC-M20: buzz the phones of whoever asked to hear from this contact. On
        # the `notifications` queue, AFTER the row committed, and best-effort -
        # a post-commit side effect that raises hands the caller a 500 for an
        # operation that actually succeeded, and n8n answers a 500 by retrying
        # the lane this endpoint exists to stop. Gated on the same
        # `already_existed` as the poke above: the second mirror lane resolves a
        # row the first already announced, so a second job would only re-derive
        # a notification the dedup key throws away.
        try:
            from app.services.message_push_service import INBOUND_TYPE
            from app.services.queue_service import enqueue_job
            from app.tasks import message_push_tasks

            # Half of everything ingested is OUTGOING, and an outgoing message
            # buzzes nobody (AC-M11), so it is filtered here too rather than
            # paying for a job and a database session per agent reply. Off the
            # same constant the service compares against, so the two cannot
            # drift onto different spellings of "from the contact".
            if str(payload.type or "").lower() == INBOUND_TYPE:
                enqueue_job(
                    message_push_tasks.send_message_push,
                    message_id,
                    queue_name="notifications",
                )
        except Exception as push_error:
            logger.warning(
                "Failed to enqueue message push for chat_histories.id=%s: %s",
                message_id,
                push_error,
            )

    # 201 either way: the caller (n8n) treats anything else as a lane failure and
    # retries, which is exactly the loop this endpoint exists to end. "duplicate"
    # names the outcome in the body instead, mirroring the conversation-SLA
    # create's `already_active` marker.
    return ChatHistoryMessageIngestResponse(
        id=message_id, status="duplicate" if already_existed else "created"
    )


@router.post(
    "/comments",
    response_model=TicketCommentIngestResponse,
    status_code=status.HTTP_201_CREATED,
)
def ingest_respond_comment(
    payload: TicketCommentIngestRequest,
    request: Request,
    current_user: dict = Depends(get_external_api_user),
    db: Session = Depends(get_db),
):
    """Ingest one comment made in Respond's own inbox (UAC AC-L3).

    The CRM half of the n8n ``comment.created`` forward lane. Respond comments
    are CONTACT-scoped, not ticket-scoped, so the stored row carries the contact
    only and renders in every open ticket drawer for that contact.

    Idempotent on Respond's own ``comment_id``, which is REQUIRED: a replayed
    webhook answers with the id of the row it already created and
    ``status: "duplicate"`` - a 201 either way, mirroring the message ingest,
    because the forwarder treats anything else as a lane failure and retries.
    A payload with no ``comment_id`` is a 400: there would be nothing to
    recognise a replay by, so every retry would add another copy of the same
    note to every open drawer for that contact.

    Every call leaves an inbound ``integration_log`` row, success AND refusal,
    exactly as the message ingest above does. The refusal is the case worth
    having: "n8n says it forwarded the comment" versus "the CRM had never heard
    of that contact" is the whole diagnosis, and without the row neither side
    could show its work.
    """
    _ = current_user
    status_code = status.HTTP_201_CREATED
    error_message: Optional[str] = None
    try:
        contact_ref = (payload.contact_id or "").strip()
        phone = (payload.phone_number or "").strip()
        if not contact_ref and not phone:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Provide contact_id (Respond contact id) or phone_number.",
            )
        comment_id = (payload.comment_id or "").strip()
        if not comment_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "comment_id is required: it is the key a replayed comment.created "
                    "event is recognised by."
                ),
            )

        contact = None
        if contact_ref:
            contact = (
                db.query(RespondContact)
                .filter(RespondContact.respond_io_id == contact_ref)
                .first()
            )
        if contact is None and phone:
            contact = (
                db.query(RespondContact)
                .filter(RespondContact.phone_number == phone)
                .first()
            )
        if contact is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No CRM contact matches this Respond.io contact.",
            )

        comment, already_existed = TicketCommentService(db).ingest_respond_comment(
            contact=contact,
            body=payload.text,
            respond_comment_id=comment_id,
            author_respond_user_id=payload.author_respond_user_id,
            author_name=payload.author_name,
            created_at_ms=payload.created_at,
        )
    except HTTPException as exc:
        status_code = exc.status_code
        error_message = str(exc.detail)
        _log_comment_ingest(db, request, payload, status_code, error_message)
        raise

    _log_comment_ingest(db, request, payload, status_code, error_message)

    # AC-K1: poke every drawer open on this contact, but never on the replay
    # path - the first lane already announced this comment.
    if not already_existed and contact.respond_io_id:
        conversation_event_bus.publish(
            conversation_event_bus.EVENT_MESSAGE, contact_id=str(contact.respond_io_id)
        )

    return TicketCommentIngestResponse(
        id=str(comment.id), status="duplicate" if already_existed else "created"
    )


@router.post(
    "/{contact_ref}/comments",
    response_model=TicketCommentExternalResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_contact_comment_from_integration(
    contact_ref: str,
    payload: TicketCommentExternalCreate,
    current_user: dict = Depends(get_external_api_user),
    db: Session = Depends(get_db),
):
    """Write a flow-authored comment on a contact: CRM first, Respond second.

    This is what n8n's ``sub-add-comment-respond`` calls instead of Respond's
    own comment endpoint, so a bot / routing note lands in the CRM (and in
    every open ticket drawer for the contact) and is then mirrored to the
    Respond inbox best-effort (AC-L2). ``contact_ref`` is the Respond contact
    id, the phone number or the ``respond_contacts.id``.

    Mentions are Respond user ids (what the flow holds). Each maps to a CRM
    user where ``users.respond_user_id`` is set - those get the in-app mention
    notification - and every id is tagged in the mirror regardless, so the
    Respond agent is notified exactly as before. The ids that matched nobody
    come back as ``unmapped_respond_user_ids``.
    """
    comment, unmapped = TicketCommentService(db).create_for_contact_from_integration(
        contact_ref,
        author_user_id=str(current_user["id"]),
        body=payload.body,
        mentioned_respond_user_ids=payload.mentioned_respond_user_ids,
        author_name=payload.author_name,
    )
    return TicketCommentExternalResponse(
        **comment.model_dump(), unmapped_respond_user_ids=unmapped
    )


def _log_comment_ingest(
    db: Session,
    request: Request,
    payload: TicketCommentIngestRequest,
    status_code: int,
    error_message: Optional[str],
) -> None:
    """One inbound row per comment ingest call. Best-effort, like its sibling:
    logging must never be the reason an ingest fails."""
    try:
        request_headers = sanitize_request_headers(dict(request.headers))

        IntegrationLogService(db).create_integration_log(
            IntegrationLogCreate(
                integration_channel="n8n",
                business_table="conversation_ticket_comments",
                business_id=str(uuid.uuid4()),
                external_reference=str(payload.contact_id or payload.phone_number or ""),
                direction="inbound",
                endpoint=str(request.url.path),
                http_method=request.method,
                request_headers=json.dumps(request_headers),
                request_payload=payload.model_dump_json(),
                status_code=status_code,
                status="success" if status_code < 400 else "failed",
                error_message=error_message,
            )
        )
    except Exception as log_error:  # noqa: BLE001
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        logger.warning(
            "Failed to create integration log for comment ingestion: %s",
            log_error,
            exc_info=True,
        )


@router.post("", response_model=ChatHistoryMessagesResponse, status_code=status.HTTP_200_OK)
def get_chat_history_messages(
    payload: ChatHistoryMessagesRequest,
    current_user: dict = Depends(get_external_api_user),
    db: Session = Depends(get_db),
):
    """Return chat history from local high-volume channel-based chat storage."""
    _ = current_user
    limit = max(1, min(500, payload.limit))
    order = payload.order.lower()
    if order not in ("asc", "desc"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="order must be 'asc' or 'desc'.")

    # Fetch latest rows first using descending index order, then optionally reverse for ascending response.
    query = text(
        """
        SELECT id, channel, contact_id, phone_number, message, sent_at, first_name, last_name, type,
               message_id, result, reply_to_message_id, reply_to_message
        FROM chat_histories
        WHERE channel = :channel
          AND contact_id = :contact_id
        ORDER BY sent_at DESC, id DESC
        LIMIT :limit
        """
    )
    try:
        result = db.execute(
            query,
            {"channel": payload.channel, "contact_id": payload.contact_id, "limit": limit},
        )
        rows = result.fetchall()
    except Exception as e:
        logger.exception("Failed to query chat_histories: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to read chat history.",
        ) from e

    messages = [
        ChatHistoryMessageItem(
            id=row.id,
            channel=row.channel,
            contact_id=row.contact_id,
            phone_number=row.phone_number,
            message=row.message,
            sent_at=row.sent_at,
            first_name=row.first_name,
            last_name=row.last_name,
            type=row.type,
            message_id=row.message_id,
            result=_coerce_result_list(row.result),
            reply_to_message_id=row.reply_to_message_id,
            reply_to_message=row.reply_to_message,
        )
        for row in rows
    ]
    if order == "asc":
        messages.reverse()

    return ChatHistoryMessagesResponse(messages=messages)
