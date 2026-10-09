"""Contact-thread scroll-back pagination and in-thread search (UAC AC-L7 / AC-L8).

The drawer used to load exactly one page of a contact's conversation and had no
way to reach anything older ("i scroll up already then can't find any older").
This module adds the two reads that fix it, both scoped to ONE contact:

``fetch_thread_page``
    A window of the thread addressed by a cursor that IS a Respond message id:
    ``before`` (older), ``after`` (newer) or ``around`` (jump to a match, the
    union of a before-half and an after-half centred on the anchor). Whatever
    the direction, **items come back oldest-to-newest** so the caller never
    reverses anything.

``search_thread``
    ILIKE over the locally stored message text, newest-first and capped. v1
    searches ``chat_histories.message`` only (the message body); contact
    name/phone are not part of an in-thread search.

Two lanes serve the page (lane CHAT-LOCAL-FIRST, 30 Sep 2026, reversed the order):

1. **``chat_histories`` keyset (primary, and always for search).** A contact with
   stored rows is served from the local table and the request path makes NO
   Respond.io call: with ~100 dealers polling every 10 s, Respond was the
   bottleneck and the outage. Keyset on ``(sent_at, id)`` under a
   ``(channel, contact_id)`` equality prefix, which is exactly what
   ``ix_chat_histories_channel_contact_sent_id`` indexes - never OFFSET, and
   ``sent_at`` rather than ``created_at`` because ``created_at`` is INGEST
   order: a backfilled 2026-05 message is written today and would sort as the
   newest thing in the thread. Both leading columns must be in the predicate
   for that index to be usable at all; see ``_base_query``. Media and sender
   source are stored columns now (migration clf_0001), so the local page renders
   the same bubbles the live one did.
2. **Respond.io cursor (first open of an empty thread, and delta sync).** A
   contact with no stored row still gets one live page (then stored). Everything
   else Respond-side goes through ``chat_thread_sync_service``: one delta read
   newer than the newest stored row, in the background after the page was
   served and at most every 30 s per contact; one read older than the oldest
   stored row when a reader scrolls past it; and the reconcile tick. Verified
   against the live API 2026-08-15: ``cursorId=<id>`` returns messages OLDER
   than the anchor, newest-first; ``cursorId=-<id>`` returns messages NEWER than
   the anchor, oldest-first.

Every Respond page we fetch is written into ``chat_histories`` (idempotent,
best-effort, never mark-read and never a window-cache write; a row already held
gets its media / sender filled if it lacked them). A failure to persist is
logged and the read still answers.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Optional
from urllib.parse import unquote

from sqlalchemy import or_, text
from sqlalchemy.orm import Session

from app.models.chat_history import CHAT_HISTORY_DEDUPE_PREDICATE, ChatHistory
from app.models.user import User
from app.services.otp_redaction import (
    mask_otp_text,
    otp_template_code_slots,
    redact_otp_item,
    redact_otp_payload,
)

logger = logging.getLogger(__name__)

DEFAULT_LIMIT = 50
MAX_LIMIT = 200
# Respond.io caps GET /v2/contact/{id}/message/list at 50 per page.
RESPOND_MAX_PAGE = 50
SEARCH_DEFAULT_LIMIT = 100
SEARCH_MAX_LIMIT = 200
SNIPPET_RADIUS = 60


@dataclass(frozen=True)
class ThreadContact:
    """Everything the two lanes need about the contact whose thread this is.

    ``respond_io_id`` is BOTH the Respond API identifier and the value stored in
    ``chat_histories.contact_id`` - the two happen to agree, which is why one
    field serves both lanes. It is never surfaced to the UI.
    """

    respond_io_id: str
    phone_number: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    channel: str = "whatsapp"


def thread_contact_for(contact: Any) -> Optional[ThreadContact]:
    """Descriptor for a ``RespondContact`` row, or None when it has no
    Respond.io id.

    The single place that turns a CRM contact row into a thread descriptor:
    both the ticket-keyed reads (via the tracking's contact) and the
    contact-keyed inbox reads go through here, so the two can never disagree
    about what a thread is scoped to.
    """
    if contact is None:
        return None
    ident = str(getattr(contact, "respond_io_id", "") or "").strip()
    if not ident:
        return None
    return ThreadContact(
        respond_io_id=ident,
        phone_number=str(getattr(contact, "phone_number", "") or ""),
        first_name=getattr(contact, "first_name", None),
        last_name=getattr(contact, "last_name", None),
    )


def empty_page(*, limit: int, error: str) -> dict:
    """The page a caller gets when there is no thread to read.

    Not a 404: the ticket / contact exists, it simply has no Respond.io link,
    and the drawer still has to render. Shape-identical to a real page so the
    client needs no special case.
    """
    payload = _page_payload(
        [], has_more_older=False, has_more_newer=False, limit=limit, source="none", error=error
    )
    payload["backfilled"] = 0
    return payload


def empty_search(*, q: str, error: str) -> dict:
    return {
        "items": [],
        "total": 0,
        "truncated": False,
        "query": (q or "").strip(),
        "error": error,
    }


# ---------------------------------------------------------------------------
# Shaping
# ---------------------------------------------------------------------------


def _message_id_to_ms(message_id: Any) -> Optional[int]:
    """Respond message ids ARE epoch microseconds (see chat_message_resolver)."""
    try:
        raw = int(str(message_id))
    except (TypeError, ValueError):
        return None
    if raw > 1e14:
        return raw // 1000
    if raw > 1e11:
        return raw
    return None


def _epoch_ms(dt: datetime) -> int:
    return int(dt.replace(tzinfo=timezone.utc).timestamp() * 1000)


def _local_status(row: ChatHistory) -> list[dict]:
    """Delivery receipts reconstructed from the stored columns.

    The frontend reads the bubble clock off ``status[].timestamp`` first and
    falls back to ``messageId``; a row with neither renders with no date pill,
    so a bare timestamp entry is always emitted as the floor.
    """
    entries: list[dict] = []
    if row.delivery_status:
        entries.append(
            {
                "value": str(row.delivery_status),
                "timestamp": _epoch_ms(row.respond_ts or row.sent_at),
            }
        )
    if row.delivered_ts:
        entries.append({"value": "delivered", "timestamp": _epoch_ms(row.delivered_ts)})
    if row.read_ts:
        entries.append({"value": "read", "timestamp": _epoch_ms(row.read_ts)})
    if not entries:
        entries.append({"timestamp": _epoch_ms(row.respond_ts or row.sent_at)})
    return entries


_PLACEHOLDER_RE = re.compile(r"^\[[a-z_]+\](?:\s+(?P<name>.*))?$", re.IGNORECASE)


def _is_media_placeholder(text_value: str, file_name: Optional[str]) -> bool:
    """True when the stored text is the ``[image] name.jpg`` placeholder the ingest
    writes for a media message with no caption (see ``_respond_item_text``), so it
    is not shown as a caption beside the attachment."""
    match = _PLACEHOLDER_RE.match((text_value or "").strip())
    if not match:
        return False
    name = (match.group("name") or "").strip()
    return not name or name == unquote(str(file_name or "")).strip()


def _local_message(row: ChatHistory) -> dict:
    """The ``message`` block: an attachment when the row holds one (R5), else text."""
    body = mask_otp_text(row.message or "") or ""
    media_url = getattr(row, "media_url", None)
    media_type = getattr(row, "media_type", None)
    if not media_url and not media_type:
        return {"type": "text", "text": body}
    file_name = getattr(row, "media_file_name", None)
    message: dict = {
        "type": "attachment",
        "attachment": {
            "type": media_type or "file",
            "url": media_url,
            "fileName": file_name,
        },
    }
    if body and not _is_media_placeholder(body, file_name):
        message["text"] = body
    return message


def _local_sender(row: ChatHistory) -> dict:
    source = getattr(row, "sender_source", None)
    if not source:
        source = "contact" if row.type == "incoming" else None
    sender: dict = {"source": source}
    user_id = getattr(row, "sender_user_id", None)
    if user_id:
        sender["userId"] = user_id
    return sender


def _row_to_item(row: ChatHistory) -> dict:
    """A `chat_histories` row in the shape the chat list already renders.

    Media (``media_url`` / ``media_type`` / ``media_file_name``) and the sender
    (``sender_source`` / ``sender_user_id``) are stored columns since migration
    clf_0001, so a local page renders the same bubbles a live Respond page did. A row
    written before that lane renders as text with ``sender.source`` inferred from the
    traffic direction, until the delta / reconcile read fills it.
    """
    numeric_id = None
    try:
        numeric_id = int(str(row.message_id)) if row.message_id is not None else None
    except (TypeError, ValueError):
        numeric_id = None
    return {
        "messageId": numeric_id,
        "traffic": row.type,
        "message": _local_message(row),
        "sender": _local_sender(row),
        "status": _local_status(row),
        "replyTo": (
            {
                "messageId": row.reply_to_message_id,
                "message": {"text": mask_otp_text(row.reply_to_message)},
            }
            if row.reply_to_message_id
            else None
        ),
        "source": "local",
    }


def _respond_item(item: dict) -> dict:
    out = dict(item)
    out["source"] = "respond"
    return out


def _attach_sender_names(db: Session, items: list[dict]) -> None:
    """Name the colleague behind each human send, in place.

    Respond identifies the sender of a staff reply by its OWN user id and gives
    us no name with it, so the thread could only ever label a bubble by the
    transport that carried it. ``users.respond_user_id`` is the bridge back to a
    CRM user, and the name goes on ``sender.name`` where the chat list reads it.

    Machine sends (n8n, api, workflow) are skipped: they carry no user id, and
    a bot send is deliberately unlabelled in the UI. Best-effort - a thread
    still renders when this lookup fails, just without the names.
    """
    wanted: set[str] = set()
    for item in items:
        sender = item.get("sender")
        if not isinstance(sender, dict):
            continue
        if str(sender.get("source") or "").strip().lower() != "user":
            continue
        user_id = sender.get("userId")
        if user_id not in (None, ""):
            wanted.add(str(user_id).strip())
    if not wanted:
        return

    try:
        rows = (
            db.query(User.respond_user_id, User.name)
            .filter(User.respond_user_id.in_(sorted(wanted)))
            .all()
        )
    except Exception:  # noqa: BLE001 - a nameless bubble beats a failed thread
        logger.warning("sender name lookup failed", exc_info=True)
        return

    by_respond_id = {str(r[0]): (r[1] or "").strip() for r in rows}
    for item in items:
        sender = item.get("sender")
        if not isinstance(sender, dict):
            continue
        name = by_respond_id.get(str(sender.get("userId") or "").strip())
        if name:
            sender["name"] = name


def _respond_item_text(item: dict) -> str:
    """Storable text for a Respond message.

    The body when there is one, else a typed placeholder carrying the filename -
    "[file] quotation.pdf" is something a person can actually search for, where a
    bare "[attachment]" is not.
    """
    message = item.get("message") if isinstance(item.get("message"), dict) else {}
    body = (message.get("text") or "").strip()
    if body:
        return body
    # A `quick_reply` keeps its prose under `title` and leaves `text` unset, so
    # reading `text` alone stored the whole option prompt as "[quick_reply]" and
    # put its words beyond the reach of in-thread search.
    title = (message.get("title") or "").strip()
    if title:
        return title
    kind = (message.get("type") or "").strip() or "attachment"
    attachment = _first_attachment(message)
    if attachment:
        name = unquote(str(attachment.get("fileName") or attachment.get("filename") or "")).strip()
        sub = (attachment.get("type") or "").strip()
        label = sub or kind
        return f"[{label}] {name}".strip() if name else f"[{label}]"
    return f"[{kind}]"


def _first_attachment(message: dict) -> Optional[dict]:
    """The attachment block of a Respond message, whichever of the shapes Respond v2
    emits it in: ``message.attachment`` (object or list), ``message.attachments[]``,
    or a bare ``url`` on a typed (image / video / audio / file) message."""
    raw = message.get("attachment")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, list):
        for entry in raw:
            if isinstance(entry, dict):
                return entry
    raw = message.get("attachments")
    if isinstance(raw, list):
        for entry in raw:
            if isinstance(entry, dict):
                return entry
    kind = str(message.get("type") or "").strip().lower()
    url = message.get("url") or message.get("link")
    if url and kind and kind not in ("text", "quick_reply", "whatsapp_template", "template"):
        return {"type": kind, "url": url, "fileName": message.get("fileName") or message.get("filename")}
    return None


def _respond_item_media(item: dict) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """``(media_url, media_type, media_file_name)`` of a Respond message, or Nones."""
    message = item.get("message") if isinstance(item.get("message"), dict) else {}
    attachment = _first_attachment(message)
    if not attachment:
        return None, None, None
    url = attachment.get("url") or attachment.get("link")
    url = str(url).strip() if url else None
    kind = (attachment.get("type") or message.get("type") or "").strip().lower() or None
    name = unquote(str(attachment.get("fileName") or attachment.get("filename") or "")).strip() or None
    if not url and not kind:
        return None, None, None
    return url, (kind[:32] if kind else None), (name[:512] if name else None)


def _respond_item_sender(item: dict) -> tuple[Optional[str], Optional[str]]:
    """``(sender_source, sender_user_id)`` of a Respond message, or Nones."""
    sender = item.get("sender") if isinstance(item.get("sender"), dict) else {}
    source = str(sender.get("source") or "").strip().lower() or None
    user_id = sender.get("userId")
    user_id = str(user_id).strip() if user_id not in (None, "") else None
    return (source[:32] if source else None), (user_id[:64] if user_id else None)


# ---------------------------------------------------------------------------
# Local keyset lane
# ---------------------------------------------------------------------------


def _base_query(db: Session, contact: ThreadContact):
    """Every local-lane read, scoped to one contact ON ONE CHANNEL.

    The channel predicate is not cosmetic: the only composite index over this
    table is ``ix_chat_histories_channel_contact_sent_id (channel, contact_id,
    sent_at, id)``, which leads on ``channel`` - a contact-only filter cannot
    use it and the keyset walk degrades to a scan of the whole table. It is also
    correct: ``persist_messages`` writes ``contact.channel``, so a thread is a
    per-channel conversation.
    """
    return db.query(ChatHistory).filter(
        ChatHistory.channel == contact.channel,
        ChatHistory.contact_id == contact.respond_io_id,
    )


def _anchor_row(db: Session, contact: ThreadContact, message_id: str) -> Optional[ChatHistory]:
    return (
        _base_query(db, contact)
        .filter(ChatHistory.message_id == str(message_id))
        .order_by(ChatHistory.sent_at.asc(), ChatHistory.id.asc())
        .first()
    )


def _older_than(q, anchor: ChatHistory):
    return q.filter(
        or_(
            ChatHistory.sent_at < anchor.sent_at,
            (ChatHistory.sent_at == anchor.sent_at) & (ChatHistory.id < anchor.id),
        )
    )


def _newer_than(q, anchor: ChatHistory):
    return q.filter(
        or_(
            ChatHistory.sent_at > anchor.sent_at,
            (ChatHistory.sent_at == anchor.sent_at) & (ChatHistory.id > anchor.id),
        )
    )


def _local_page(
    db: Session,
    contact: ThreadContact,
    *,
    before: Optional[str],
    after: Optional[str],
    around: Optional[str],
    limit: int,
) -> dict:
    base = _base_query(db, contact)

    if around:
        anchor = _anchor_row(db, contact, around)
        if anchor is not None:
            older_n = max(0, (limit - 1) // 2)
            newer_n = max(0, limit - 1 - older_n)
            older = (
                _older_than(_base_query(db, contact), anchor)
                .order_by(ChatHistory.sent_at.desc(), ChatHistory.id.desc())
                .limit(older_n)
                .all()
            )
            newer = (
                _newer_than(_base_query(db, contact), anchor)
                .order_by(ChatHistory.sent_at.asc(), ChatHistory.id.asc())
                .limit(newer_n)
                .all()
            )
            rows = list(reversed(older)) + [anchor] + newer
            return _page_payload(
                [_row_to_item(r) for r in rows],
                has_more_older=len(older) == older_n and older_n > 0,
                has_more_newer=len(newer) == newer_n and newer_n > 0,
                limit=limit,
                source="local",
                anchor_message_id=str(around),
            )
        # Unknown anchor: fall through to the newest page rather than 404 - the
        # thread still has to render something.

    if after:
        anchor = _anchor_row(db, contact, after)
        if anchor is not None:
            rows = (
                _newer_than(base, anchor)
                .order_by(ChatHistory.sent_at.asc(), ChatHistory.id.asc())
                .limit(limit)
                .all()
            )
            return _page_payload(
                [_row_to_item(r) for r in rows],
                has_more_older=True,
                has_more_newer=len(rows) == limit,
                limit=limit,
                source="local",
            )

    if before:
        anchor = _anchor_row(db, contact, before)
        if anchor is not None:
            rows = (
                _older_than(base, anchor)
                .order_by(ChatHistory.sent_at.desc(), ChatHistory.id.desc())
                .limit(limit)
                .all()
            )
            rows.reverse()
            return _page_payload(
                [_row_to_item(r) for r in rows],
                has_more_older=len(rows) == limit,
                has_more_newer=True,
                limit=limit,
                source="local",
            )

    rows = (
        base.order_by(ChatHistory.sent_at.desc(), ChatHistory.id.desc()).limit(limit).all()
    )
    rows.reverse()
    return _page_payload(
        [_row_to_item(r) for r in rows],
        has_more_older=len(rows) == limit,
        has_more_newer=False,
        limit=limit,
        source="local",
    )


def _page_payload(
    items: list[dict],
    *,
    has_more_older: bool,
    has_more_newer: bool,
    limit: int,
    source: str,
    anchor_message_id: Optional[str] = None,
    error: Optional[str] = None,
) -> dict:
    def _first_cursor(candidates: list[dict]) -> Optional[str]:
        """The edge id the caller pages from.

        Walks inward past any item with no message id: an item that cannot be a
        cursor must not become one, or the next request silently restarts from
        the newest page.
        """
        for item in candidates:
            raw = item.get("messageId")
            if raw is not None:
                return str(raw)
        return None

    return {
        "items": items,
        "has_more_older": bool(has_more_older),
        "has_more_newer": bool(has_more_newer),
        "oldest_message_id": _first_cursor(items),
        "newest_message_id": _first_cursor(list(reversed(items))),
        "anchor_message_id": anchor_message_id,
        "limit": limit,
        "source": source,
        "error": error,
    }


# ---------------------------------------------------------------------------
# Respond.io lane
# ---------------------------------------------------------------------------


def _respond_call(
    client,
    identifier: str,
    *,
    limit: int,
    cursor: Optional[str],
    otp_slots: Optional[dict] = None,
) -> list[dict]:
    payload = client.list_messages(identifier, limit=min(limit, RESPOND_MAX_PAGE), cursor=cursor)
    # Reviewer B2 (#1280): no sign-in or portal code reaches the thread or the
    # chat_histories cache, whichever client produced the page.
    redact_otp_payload(payload, otp_slots or {})
    items = payload.get("items") if isinstance(payload, dict) else None
    return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []


def _respond_page(
    client,
    contact: ThreadContact,
    *,
    before: Optional[str],
    after: Optional[str],
    around: Optional[str],
    limit: int,
    otp_slots: Optional[dict] = None,
) -> dict:
    ident = contact.respond_io_id

    if around:
        older_n = max(0, (limit - 1) // 2)
        newer_n = max(0, limit - 1 - older_n)
        older = list(reversed(_respond_call(client, ident, limit=older_n, cursor=str(around), otp_slots=otp_slots))) if older_n else []
        newer = _respond_call(client, ident, limit=newer_n, cursor=f"-{around}", otp_slots=otp_slots) if newer_n else []
        anchor = redact_otp_item(client.get_message(ident, around), otp_slots or {})
        anchor_items = [anchor] if isinstance(anchor, dict) and anchor.get("messageId") is not None else []
        items = older + anchor_items + newer
        return _page_payload(
            [_respond_item(i) for i in items],
            has_more_older=len(older) == older_n and older_n > 0,
            has_more_newer=len(newer) == newer_n and newer_n > 0,
            limit=limit,
            source="respond",
            anchor_message_id=str(around),
        )

    if after:
        # cursorId=-<id> yields NEWER messages, already ascending.
        items = _respond_call(
            client, ident, limit=limit, cursor=f"-{after}", otp_slots=otp_slots
        )
        return _page_payload(
            [_respond_item(i) for i in items],
            has_more_older=True,
            has_more_newer=len(items) == limit,
            limit=limit,
            source="respond",
        )

    # Newest page (no cursor) and the older walk both come back newest-first.
    items = _respond_call(
        client, ident, limit=limit, cursor=str(before) if before else None, otp_slots=otp_slots
    )
    items = list(reversed(items))
    return _page_payload(
        [_respond_item(i) for i in items],
        has_more_older=len(items) == limit,
        has_more_newer=bool(before),
        limit=limit,
        source="respond",
    )


# ---------------------------------------------------------------------------
# Persistence (best-effort cache fill for search)
# ---------------------------------------------------------------------------


def persist_messages(db: Session, contact: ThreadContact, items: Iterable[dict]) -> int:
    """Store Respond messages in `chat_histories`, skipping ones already held.

    Deliberately NOT the external ingest's ON CONFLICT alone: that arbiter is a
    PARTIAL unique index (``created_at >= 2026-08-14``), so a row ingested before
    the cutover is invisible to it and a naive upsert would insert a duplicate.
    The explicit "which of these ids do we already hold" probe covers both eras;
    the ON CONFLICT clause stays as the race guard between concurrent readers.
    """
    rows: list[dict] = []
    for item in items:
        message_id = item.get("messageId")
        if message_id is None:
            continue
        ms = _message_id_to_ms(message_id)
        if ms is None:
            continue
        sent_at = datetime.fromtimestamp(ms / 1000, tz=timezone.utc).replace(tzinfo=None)
        traffic = str(item.get("traffic") or "").strip().lower()
        if traffic not in ("incoming", "outgoing"):
            continue
        reply_to = item.get("replyTo") if isinstance(item.get("replyTo"), dict) else None
        # AC-L6: the quoted EXCERPT is persisted alongside the quoted id. Without
        # it the fallback (local) lane renders a "replying to" block with nothing
        # in it - the id alone cannot be shown to a reader.
        reply_to_text = _respond_item_text(reply_to) if reply_to else None
        media_url, media_type, media_file_name = _respond_item_media(item)
        sender_source, sender_user_id = _respond_item_sender(item)
        rows.append(
            {
                "channel": contact.channel,
                "contact_id": contact.respond_io_id,
                "phone_number": contact.phone_number,
                "message": _respond_item_text(item),
                "sent_at": sent_at,
                "first_name": contact.first_name,
                "last_name": contact.last_name,
                "type": traffic,
                "message_id": str(message_id),
                "reply_to_message_id": (
                    str(reply_to.get("messageId")) if reply_to and reply_to.get("messageId") else None
                ),
                "reply_to_message": reply_to_text,
                "respond_ts": sent_at,
                "ingest_at": datetime.now(tz=timezone.utc).replace(tzinfo=None),
                "media_url": media_url,
                "media_type": media_type,
                "media_file_name": media_file_name,
                "sender_source": sender_source,
                "sender_user_id": sender_user_id,
            }
        )
    if not rows:
        return 0

    existing = {
        str(mid)
        for (mid,) in db.query(ChatHistory.message_id)
        .filter(
            ChatHistory.contact_id == contact.respond_io_id,
            ChatHistory.message_id.in_([r["message_id"] for r in rows]),
        )
        .all()
    }
    fresh = [r for r in rows if r["message_id"] not in existing]

    # R5 / AC-RF3: a row already held (written by n8n, or before the media columns
    # existed) gets the attachment and sender filled in from this read, fill-if-null.
    # This is the ONLY backfill for old rows - no one-shot script.
    held = [
        r
        for r in rows
        if r["message_id"] in existing
        and (r["media_url"] or r["media_type"] or r["sender_source"] or r["sender_user_id"])
    ]
    if held:
        db.execute(
            text(
                """
                UPDATE chat_histories SET
                    media_url = COALESCE(media_url, :media_url),
                    media_type = COALESCE(media_type, :media_type),
                    media_file_name = COALESCE(media_file_name, :media_file_name),
                    sender_source = COALESCE(sender_source, :sender_source),
                    sender_user_id = COALESCE(sender_user_id, :sender_user_id)
                WHERE contact_id = :contact_id AND message_id = :message_id
                  AND ((media_url IS NULL AND CAST(:media_url AS TEXT) IS NOT NULL)
                       OR (media_type IS NULL AND CAST(:media_type AS TEXT) IS NOT NULL)
                       OR (media_file_name IS NULL AND CAST(:media_file_name AS TEXT) IS NOT NULL)
                       OR (sender_source IS NULL AND CAST(:sender_source AS TEXT) IS NOT NULL)
                       OR (sender_user_id IS NULL AND CAST(:sender_user_id AS TEXT) IS NOT NULL))
                """
            ),
            held,
        )

    if not fresh:
        return 0

    insert_sql = text(
        f"""
        INSERT INTO chat_histories (
            channel, contact_id, phone_number, message, sent_at, first_name, last_name,
            type, message_id, reply_to_message_id, reply_to_message, respond_ts, ingest_at,
            media_url, media_type, media_file_name, sender_source, sender_user_id
        ) VALUES (
            :channel, :contact_id, :phone_number, :message, :sent_at, :first_name, :last_name,
            :type, :message_id, :reply_to_message_id, :reply_to_message, :respond_ts, :ingest_at,
            :media_url, :media_type, :media_file_name, :sender_source, :sender_user_id
        )
        ON CONFLICT (contact_id, message_id) WHERE {CHAT_HISTORY_DEDUPE_PREDICATE}
        DO NOTHING
        """
    )
    db.execute(insert_sql, fresh)

    # The reconcile selects on this (R4): activity from any lane keeps the contact in
    # its window. Same transaction as the rows; the caller commits.
    from app.services import chat_thread_sync_service

    chat_thread_sync_service.touch_activity(
        db, contact.channel, contact.respond_io_id, max(r["sent_at"] for r in fresh)
    )
    return len(fresh)


def _persist_best_effort(db: Session, contact: ThreadContact, items: list[dict]) -> int:
    """A read must never fail because the cache fill did.

    Committed here rather than left pending: the caller is a GET with nothing
    else in flight, and an uncommitted backfill would be rolled back by the
    request teardown, so search would never see it.
    """
    try:
        written = persist_messages(db, contact, items)
        if written:
            db.commit()
        return written
    except Exception:  # noqa: BLE001 - the page is already answerable
        logger.warning(
            "chat history backfill failed for contact %s", contact.respond_io_id, exc_info=True
        )
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return 0


def mirror_outgoing_send(
    db: Session,
    *,
    identifier: str,
    respond_contact_id: Optional[str],
    response: Any,
    message: dict,
) -> int:
    """Write the row for a message the CRM itself just sent (PLAN-optimistic-send).

    The thread renders from ``chat_histories``, and until this existed a CRM
    reply only reached it once Respond's outbound webhook had crossed n8n and
    been mirrored back - seconds at best, never when that lane is down. The CRM
    already holds everything the mirror would write (Respond's ``messageId``
    acknowledgement, the text, the contact), so it writes the row itself and the
    later mirror lands on it through the same ``(contact_id, message_id)``
    dedupe, filling only what is still null.

    ``message`` is the Respond-shaped ``message`` block (``{"type": "text",
    "text": ...}`` or an attachment block) so the stored text is the same
    placeholder the read lane's backfill would have produced.

    Best-effort by construction: the message has reached the contact, so a
    failure here is logged and the send's result is untouched. An
    acknowledgement whose id is not a Respond timestamp writes nothing (the
    dedupe would have nothing to key on). Returns the number of rows written.
    """
    from app.models.access import RespondContact
    from app.services import conversation_event_bus

    message_id = None
    if isinstance(response, dict):
        message_id = response.get("messageId") or response.get("message_id") or response.get("id")
    if message_id is None or _message_id_to_ms(message_id) is None:
        return 0

    contact_row = None
    try:
        if respond_contact_id:
            contact_row = (
                db.query(RespondContact).filter(RespondContact.id == str(respond_contact_id)).first()
            )
        if contact_row is None and identifier:
            contact_row = (
                db.query(RespondContact)
                .filter(RespondContact.respond_io_id == str(identifier).strip())
                .first()
            )
    except Exception:  # noqa: BLE001 - the send already succeeded
        logger.warning("outgoing mirror: contact lookup failed for %s", identifier, exc_info=True)
        return 0
    contact = thread_contact_for(contact_row)
    if contact is None:
        return 0

    written = _persist_best_effort(
        db,
        contact,
        [{"messageId": message_id, "traffic": "outgoing", "message": message}],
    )
    if written:
        conversation_event_bus.publish(
            conversation_event_bus.EVENT_MESSAGE, contact_id=contact.respond_io_id
        )
    return written


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _cursor(name: str, value: Optional[str]) -> Optional[str]:
    """A thread cursor is a Respond message id: digits, nothing else.

    Security review (ASKS-UX, 30 Sep 2026): `around` is spliced into the Respond URL path by
    `RespondClient.get_message`, and httpx normalises `..`, so a crafted cursor could read another
    contact's message (and, through the best-effort backfill, write it into this contact's
    history) or probe a phone number as a workspace contact. Refused here, once, for every route
    that pages a thread; the caller maps the ValueError to its own 4xx.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if not text.isdigit():
        raise ValueError(f"{name} must be a message id.")
    return text


def fetch_thread_page(
    db: Session,
    contact: ThreadContact,
    *,
    before: Optional[str] = None,
    after: Optional[str] = None,
    around: Optional[str] = None,
    limit: int = DEFAULT_LIMIT,
    client: Any = None,
) -> dict:
    """One window of a contact's thread, ALWAYS oldest-to-newest.

    At most one of ``before`` / ``after`` / ``around`` is honoured, in that
    precedence order (the route rejects more than one before we get here).

    Local first (R1): a contact with stored rows is answered from ``chat_histories``
    and this call makes no Respond request. With a client, the newest-window and
    ``after`` reads then queue ONE background delta read newer than the newest stored
    row (R2, at most every 30 s per contact), and a ``before`` read that runs past the
    oldest stored row makes ONE inline read older than it (the page being asked for is
    the data being fetched). A contact with no stored row gets today's live page, then
    stored; Respond failing there still answers the (empty) local page.
    """
    from app.services import chat_thread_sync_service as sync_service

    limit = max(1, min(int(DEFAULT_LIMIT if limit is None else limit), MAX_LIMIT))
    before = _cursor("before", before)
    after = _cursor("after", after)
    around = _cursor("around", around)

    # A client with an explicitly empty key can only raise; anything else is tried.
    usable_client = client if client is not None and getattr(client, "api_key", "set") else None

    if sync_service.has_local_rows(db, contact):
        if usable_client is not None and before:
            _fill_older_if_needed(db, contact, usable_client, before=before, limit=limit)
        page = _local_page(db, contact, before=before, after=after, around=around, limit=limit)
        _attach_sender_names(db, page["items"])
        page["backfilled"] = 0
        # A short local page is the start of the thread only once Respond said so: a
        # contact with three n8n rows and a thousand messages on Respond must keep
        # offering older history, or the scroll-back that fills it never runs (review B1).
        if not page["has_more_older"] and usable_client is not None and not after:
            page["has_more_older"] = sync_service.older_read_allowed(db, contact)
        if usable_client is not None and not before and not around:
            sync_service.schedule_sync_newer(db, contact)
        return page

    if usable_client is not None:
        try:
            page = _respond_page(
                usable_client,
                contact,
                before=before,
                after=after,
                around=around,
                limit=limit,
                otp_slots=otp_template_code_slots(db),
            )
        except Exception:  # noqa: BLE001 - the local lane still answers
            logger.warning(
                "Respond thread page failed for contact %s; serving local history",
                contact.respond_io_id,
                exc_info=True,
            )
        else:
            _attach_sender_names(db, page["items"])
            page["backfilled"] = _persist_best_effort(db, contact, page["items"])
            if page["backfilled"]:
                # The live page IS this contact's first sync: the watermark moves to its
                # newest id, no delta read for 30 s, and a short newest page means the
                # whole thread is already here.
                sync_service.note_first_page(
                    db,
                    contact,
                    oldest_reached=not before and not around and not page["has_more_older"],
                    items=page["items"] if not before and not around else None,
                )
            return page

    page = _local_page(db, contact, before=before, after=after, around=around, limit=limit)
    _attach_sender_names(db, page["items"])
    page["backfilled"] = 0
    return page


def _fill_older_if_needed(
    db: Session, contact: ThreadContact, client: Any, *, before: str, limit: int
) -> None:
    """A scroll-back that would run past the oldest stored row pulls ONE older Respond
    page first (R2, AC-DS3). Nothing happens when enough rows are already stored or
    the start of the Respond thread was reached before; a failure leaves the local
    page to answer with what it has."""
    from app.services import chat_thread_sync_service as sync_service

    anchor = _anchor_row(db, contact, before)
    if anchor is None:
        return
    stored_older = (
        _older_than(_base_query(db, contact), anchor)
        .order_by(ChatHistory.sent_at.desc(), ChatHistory.id.desc())
        .limit(limit)
        .count()
    )
    if stored_older >= limit or not sync_service.older_read_allowed(db, contact):
        return
    sync_service.sync_older(db, contact, client)


def _like_pattern(q: str) -> str:
    """`%`, `_` and the escape character itself are literals in a user query."""
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _snippet(body: str, needle: str) -> str:
    idx = body.lower().find(needle.lower())
    if idx < 0:
        return body[: SNIPPET_RADIUS * 2].strip()
    start = max(0, idx - SNIPPET_RADIUS)
    end = min(len(body), idx + len(needle) + SNIPPET_RADIUS)
    out = body[start:end].strip()
    if start > 0:
        out = "…" + out
    if end < len(body):
        out = out + "…"
    return out


def search_thread(
    db: Session,
    contact: ThreadContact,
    *,
    q: str,
    limit: int = SEARCH_DEFAULT_LIMIT,
) -> dict:
    """Newest-first matches for `q` inside ONE contact's stored messages.

    Side-effect free. Rows with no ``message_id`` are skipped: the thread jumps
    to a match by message id, so a match it cannot jump to is not a result.

    Coverage is whatever is in ``chat_histories`` - the live ingest plus every
    page a reader has already scrolled back through (see ``fetch_thread_page``).
    """
    limit = max(1, min(int(SEARCH_DEFAULT_LIMIT if limit is None else limit), SEARCH_MAX_LIMIT))
    needle = (q or "").strip()
    if not needle:
        return {"items": [], "total": 0, "truncated": False, "query": ""}

    rows = (
        _base_query(db, contact)
        .filter(ChatHistory.message_id.isnot(None))
        .filter(ChatHistory.message.ilike(_like_pattern(needle), escape="\\"))
        .order_by(ChatHistory.sent_at.desc(), ChatHistory.id.desc())
        .limit(limit)
        .all()
    )
    # Reviewer B2 (#1280): search reads the MASKED text, so a row the mirror
    # stored with a code in it neither shows the code nor matches on it (no
    # digit-by-digit oracle).
    masked = [(row, mask_otp_text(row.message or "")) for row in rows]
    items = [
        {
            "message_id": str(row.message_id),
            "sent_at": row.sent_at.isoformat() if row.sent_at else None,
            "direction": row.type,
            "snippet": _snippet(body, needle),
        }
        for row, body in masked
        if needle.lower() in body.lower()
    ]
    return {
        "items": items,
        "total": len(items),
        "truncated": len(rows) == limit,
        "query": needle,
    }
