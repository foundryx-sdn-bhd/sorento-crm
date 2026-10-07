"""Link an OUTGOING chat row to the engine turn that wrote it (QUOTED-REPLY R1, AC-QR-24).

Lives in core, not in the chatbot package: the ingest route calls it, and AC-002 forbids core
importing `app.services.chatbot`. It reads only `ChatHistory` and `ChatbotTurn`, both core
models; the chatbot package's resolver imports the shared matcher from here.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session, load_only

from app.models.chat_history import ChatHistory
from app.models.chatbot_turn import ChatbotTurn

# R1 (iii): the text step only looks at turns that finished shortly before the send.
_LINK_WINDOW_BEFORE = timedelta(seconds=120)
_LINK_WINDOW_AFTER = timedelta(seconds=5)
# Bounds the Python scan of a contact's turns for the text route.
_SCAN_LIMIT = 200
_WS = re.compile(r"\s+")




def _squash(text: Any) -> str:
    return _WS.sub("", text) if isinstance(text, str) else ""


# AC-QR-24: n8n splits one long bot reply into several messages, so a quoted or linked
# message may be a CHUNK of the stored reply. The floor keeps a short fragment from matching
# every reply that happens to contain it.
_CHUNK_FLOOR = 40


def _reply_matches(stored: str, candidate: str) -> bool:
    """Squashed `candidate` is the squashed stored reply, or (long enough) a slice of it."""
    if not stored or not candidate:
        return False
    return candidate == stored or (len(candidate) >= _CHUNK_FLOOR and candidate in stored)


def _reply_text(turn: ChatbotTurn) -> str:
    reply = (turn.response or {}).get("reply") if isinstance(turn.response, dict) else None
    return _squash(reply.get("text")) if isinstance(reply, dict) else ""


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def link_outgoing_row(
    db: Session,
    *,
    contact_id: str,
    turn_id: str | None,
    message: str,
    sent_at: datetime,
    explicit: str | None,
) -> str | None:
    """The engine turn id an OUTGOING chat row carries, or None (R1)."""
    if explicit:
        return explicit
    if turn_id:
        incoming = (
            db.query(ChatHistory.message_id)
            .filter(
                ChatHistory.contact_id == contact_id,
                ChatHistory.type == "incoming",
                ChatHistory.turn_id == turn_id,
                ChatHistory.message_id.isnot(None),
            )
            .order_by(ChatHistory.id.desc())
            .first()
        )
        if incoming is not None:
            turn = (
                db.query(ChatbotTurn.id)
                .filter(
                    ChatbotTurn.contact_respond_id == contact_id,
                    ChatbotTurn.message_id == incoming.message_id,
                    ChatbotTurn.is_test.is_(False),
                )
                .order_by(ChatbotTurn.created_at.desc())
                .first()
            )
            if turn is not None:
                return str(turn.id)
    wanted = _squash(message)
    if not wanted:
        return None
    at = _aware(sent_at)
    candidates = (
        db.query(ChatbotTurn)
        .options(load_only(ChatbotTurn.id, ChatbotTurn.response))
        .filter(
            ChatbotTurn.contact_respond_id == contact_id,
            ChatbotTurn.is_test.is_(False),
            ChatbotTurn.finished_at >= at - _LINK_WINDOW_BEFORE,
            ChatbotTurn.finished_at <= at + _LINK_WINDOW_AFTER,
        )
        .order_by(ChatbotTurn.finished_at.desc())
        .limit(_SCAN_LIMIT)
        .all()
    )
    for turn in candidates:
        if _reply_matches(_reply_text(turn), wanted):
            return str(turn.id)
    return None
