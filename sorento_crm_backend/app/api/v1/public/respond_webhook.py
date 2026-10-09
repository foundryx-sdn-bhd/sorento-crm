"""Respond.io's direct message webhook (lane CHAT-LOCAL-FIRST, R3).

``POST /api/v1/public/respond/webhook``. Respond.io posts every ``message.received`` /
``message.sent`` event here and the row lands in ``chat_histories`` through the SAME
writer the n8n mirror uses (``chat_history_ingest_service``), so the two feeds dedupe on
``(contact_id, message_id)`` and either one alone keeps the thread current.

Auth is the webhook's own: the signing key entered on the Respond.io webhook
(``RESPOND_WEBHOOK_SECRET``) verifies an HMAC-SHA256 over the RAW request body against
the signature header (``x-respond-signature``, or ``x-webhook-signature``; hex or base64
accepted), or, for a webhook configured with a custom header instead of a signing key,
``X-Respond-Webhook-Secret`` must equal the secret. Unset secret = 503, so a deploy that
has not been configured cannot be written to by anyone. A bad signature is a 401 that
writes nothing. Owner-side steps: ``documentation/reference/RESPOND-WEBHOOK-SETUP.md``.

Answers 200 on success AND on a duplicate (``status: duplicate``): Respond retries
anything else, and a retry of a stored message is the loop the dedupe exists to end.
Events that are not messages answer 200 ``ignored`` for the same reason.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.config import settings
from app.database import get_db
from app.models.access import RespondContact
from app.schemas.integration import IntegrationLogCreate
from app.services import conversation_thread_service as thread_service
from app.services.chat_history_ingest_service import (
    ChatMessageRow,
    announce_new_row,
    upsert_message_row,
)
from app.services.integration_service import IntegrationLogService, sanitize_request_headers
from app.services.media_proxy_service import allowed_hosts
from app.services.otp_redaction import otp_template_code_slots, redact_otp_item

logger = logging.getLogger(__name__)

router = APIRouter()

SIGNATURE_HEADERS = ("x-respond-signature", "x-webhook-signature")
SHARED_SECRET_HEADER = "x-respond-webhook-secret"
MESSAGE_EVENTS = frozenset({"message.received", "message.sent"})
INTEGRATION_CHANNEL = "respond_webhook"
# A Respond message event is a few KB. The body is buffered before the signature can be
# checked, so the cap is what stops an unauthenticated caller from spending API memory.
MAX_BODY_BYTES = 256 * 1024


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def _signatures_for(secret: str, raw: bytes) -> set[bytes]:
    digest = hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).digest()
    return {
        digest.hex().encode("ascii"),
        base64.b64encode(digest),
        base64.urlsafe_b64encode(digest).rstrip(b"="),
    }


def _same(presented: str, expected: bytes) -> bool:
    # Bytes on both sides: `compare_digest` refuses non-ASCII str, and a header value is
    # whatever the caller sent (security review: a stray byte must be a 401, not a 500).
    return hmac.compare_digest(presented.encode("utf-8"), expected)


def verify_request(raw: bytes, headers: Any, secret: Optional[str]) -> bool:
    """True when the request proves it came from the configured Respond.io webhook."""
    if not secret:
        return False
    presented = None
    for name in SIGNATURE_HEADERS:
        value = headers.get(name)
        if value:
            presented = str(value).strip()
            break
    if presented:
        # `sha256=<hex>` is a common prefix convention; accept it too.
        if presented.lower().startswith("sha256="):
            presented = presented.split("=", 1)[1]
        return any(_same(presented, expected) for expected in _signatures_for(secret, raw))
    shared = headers.get(SHARED_SECRET_HEADER)
    if shared:
        return _same(str(shared).strip(), secret.encode("utf-8"))
    return False


def _allowed_media_url(url: Optional[str]) -> Optional[str]:
    """Only a URL on a known Respond media host is kept (security review, finding 7): the
    thread renders `media_url` straight into an image tag, and the proxy would refuse any
    other host anyway, so a forged host is dropped rather than stored."""
    if not url:
        return None
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return None
    return url if host and host in allowed_hosts() else None


# ---------------------------------------------------------------------------
# Payload mapping
# ---------------------------------------------------------------------------


def _normalise_channel(source: Any) -> str:
    """`chat_histories.channel` is `whatsapp` for every WhatsApp flavour Respond names
    (whatsapp_cloud, whatsapp_twilio ...): the thread is read per channel, and n8n
    writes `whatsapp`, so the two lanes must agree or one lane's rows are invisible."""
    value = str(source or "").strip().lower()
    if not value or "whatsapp" in value:
        return "whatsapp"
    return value[:32]


def map_webhook_message(payload: dict, db: Session) -> Optional[ChatMessageRow]:
    """The ``ChatMessageRow`` for a Respond.io message event, or None when the body
    carries no message (an unrelated event, or a shape with no message id)."""
    event_type = str(payload.get("event_type") or payload.get("event") or "").strip().lower()
    message = payload.get("message") if isinstance(payload.get("message"), dict) else None
    if message is None:
        return None
    # The envelope is {contact, message, channel}; the message object itself carries
    # messageId / traffic / timestamp / sender and its own `message` body block.
    inner = message.get("message") if isinstance(message.get("message"), dict) else None
    if inner is None:
        return None
    message_id = message.get("messageId") or message.get("message_id")
    if message_id in (None, ""):
        return None
    traffic = str(message.get("traffic") or "").strip().lower()
    if traffic not in ("incoming", "outgoing"):
        traffic = {"message.received": "incoming", "message.sent": "outgoing"}.get(event_type, "")
    if not traffic:
        return None

    contact = payload.get("contact") if isinstance(payload.get("contact"), dict) else {}
    contact_id = str(contact.get("id") or message.get("contactId") or "").strip()
    if not contact_id:
        return None
    phone = str(contact.get("phone") or contact.get("phoneNumber") or "").strip()
    first_name = contact.get("firstName") or contact.get("first_name")
    last_name = contact.get("lastName") or contact.get("last_name")
    if not phone or (not first_name and not last_name):
        row = db.query(RespondContact).filter(RespondContact.respond_io_id == contact_id).first()
        if row is not None:
            phone = phone or str(row.phone_number or "")
            first_name = first_name or row.first_name
            last_name = last_name or row.last_name

    # The message id IS Respond's clock (epoch microseconds), the same source the n8n
    # ingest's `respond_ts` and the delta read's `sent_at` use, so rows from every lane
    # sort alike. `timestamp` is the fallback, normalised by its size: seconds below
    # 1e11, milliseconds below 1e14, microseconds above (review S8).
    ms = thread_service._message_id_to_ms(message_id)
    stamp = message.get("timestamp")
    try:
        stamp_ms = int(stamp) if stamp not in (None, "") else None
    except (TypeError, ValueError):
        stamp_ms = None
    if stamp_ms is not None:
        if stamp_ms > 1e14:
            stamp_ms //= 1000
        elif stamp_ms < 1e11:
            stamp_ms *= 1000
    when_ms = ms or stamp_ms
    sent_at = (
        datetime.fromtimestamp(when_ms / 1000, tz=timezone.utc).replace(tzinfo=None)
        if when_ms
        else datetime.now(tz=timezone.utc).replace(tzinfo=None)
    )

    item = {"message": inner, "sender": message.get("sender"), "messageId": message_id}
    media_url, media_type, media_file_name = thread_service._respond_item_media(item)
    sender_source, sender_user_id = thread_service._respond_item_sender(item)
    reply_to = message.get("replyTo") if isinstance(message.get("replyTo"), dict) else None
    channel_block = payload.get("channel") if isinstance(payload.get("channel"), dict) else {}

    return ChatMessageRow(
        channel=_normalise_channel(channel_block.get("source") or channel_block.get("type")),
        contact_id=contact_id,
        phone_number=phone[:32],
        message=thread_service._respond_item_text(item),
        sent_at=sent_at,
        type=traffic,
        message_id=str(message_id)[:64],
        first_name=first_name,
        last_name=last_name,
        reply_to_message_id=(
            str(reply_to.get("messageId"))[:64] if reply_to and reply_to.get("messageId") else None
        ),
        reply_to_message=thread_service._respond_item_text(reply_to) if reply_to else None,
        media_url=_allowed_media_url(media_url),
        media_type=media_type,
        media_file_name=media_file_name,
        sender_source=sender_source,
        sender_user_id=sender_user_id,
    )


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


def _log(db: Session, request: Request, body: str, reference: str, status_code: int, error: Optional[str]) -> None:
    """One inbound integration_log row per call, success and refusal alike, exactly as the
    n8n ingest does. Best-effort: logging must never be the reason an ingest fails."""
    try:
        IntegrationLogService(db).create_integration_log(
            IntegrationLogCreate(
                integration_channel=INTEGRATION_CHANNEL,
                business_table="chat_histories",
                business_id=str(uuid.uuid4()),
                external_reference=reference,
                direction="inbound",
                endpoint=str(request.url.path),
                http_method=request.method,
                request_headers=json.dumps(sanitize_request_headers(dict(request.headers))),
                request_payload=body[:20000],
                status_code=status_code,
                status="success" if status_code < 400 else "failed",
                error_message=error,
            )
        )
    except Exception:  # noqa: BLE001
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        logger.warning("Failed to create integration log for the Respond webhook", exc_info=True)


def _handle(db: Session, request: Request, raw: bytes) -> dict:
    text_body = raw.decode("utf-8", errors="replace")
    try:
        payload = json.loads(text_body) if raw else {}
    except ValueError:
        _log(db, request, text_body, "", status.HTTP_400_BAD_REQUEST, "Body is not JSON.")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Body is not JSON.")
    if not isinstance(payload, dict):
        _log(db, request, text_body, "", status.HTTP_400_BAD_REQUEST, "Body is not a JSON object.")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Body is not a JSON object.")

    # Reviewer B2 (#1280), security review of this lane (finding 1): no sign-in code
    # reaches chat_histories OR integration_logs from this lane either. The OTP template's
    # parameters are scrubbed in place BEFORE anything is mapped or logged, exactly as the
    # Respond-read lane does through `redact_otp_payload`.
    if isinstance(payload.get("message"), dict):
        redact_otp_item(payload["message"], otp_template_code_slots(db))
    logged = json.dumps(payload)

    event_type = str(payload.get("event_type") or payload.get("event") or "").strip().lower()
    if event_type and event_type not in MESSAGE_EVENTS:
        return {"status": "ignored", "event_type": event_type}

    row = map_webhook_message(payload, db)
    if row is None:
        _log(db, request, logged, "", status.HTTP_400_BAD_REQUEST, "No message in payload.")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Payload carries no message (messageId, traffic, contact).",
        )

    try:
        message_pk, already_existed = upsert_message_row(db, row)
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.exception("Respond webhook: failed to store message %s", row.message_id)
        _log(db, request, logged, row.contact_id, 500, "Failed to store the message.")
        raise HTTPException(status_code=500, detail="Failed to store the message.")

    _log(db, request, logged, row.contact_id, status.HTTP_200_OK, None)
    if not already_existed:
        announce_new_row(db, message_pk=message_pk, contact_id=row.contact_id, traffic=row.type)
    return {"id": message_pk, "status": "duplicate" if already_existed else "created"}


@router.post("/webhook", status_code=status.HTTP_200_OK)
async def respond_message_webhook(request: Request, db: Session = Depends(get_db)):
    """Store one Respond.io message event (see the module docstring)."""
    secret = settings.respond_webhook_secret
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Respond.io webhook is not configured (RESPOND_WEBHOOK_SECRET).",
        )
    raw = await _read_capped_body(request)
    if not verify_request(raw, request.headers, secret):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Bad webhook signature.")
    return await run_in_threadpool(lambda: _handle(db, request, raw))


async def _read_capped_body(request: Request) -> bytes:
    """The raw body, refused with 413 past MAX_BODY_BYTES. The header is checked first
    and the stream is capped as it arrives, so a chunked request cannot get around it."""
    too_large = HTTPException(
        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
        detail=f"Body exceeds {MAX_BODY_BYTES} bytes.",
    )
    declared = (request.headers.get("content-length") or "").strip()
    if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise too_large
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            raise too_large
        chunks.append(chunk)
    return b"".join(chunks)
