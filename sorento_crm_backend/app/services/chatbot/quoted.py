"""QUOTED-REPLY: link a bot reply to its engine turn, and resolve a quoted message back to it.

Owner rulings, 7 Oct 2026 (plan `PLAN-quoted-reply-07oct.md`, R1 and R2). Reads
`ChatHistory` and `ChatbotTurn` only. Every read is scoped to one contact; the resolver is
also scoped to the `is_test` world of the turn asking. Only the quoted turn's focus and
open question come back: never access levels, profile or reply language (R7).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session, load_only

from app.models.chat_history import ChatHistory
from app.models.chatbot_turn import ChatbotTurn

logger = logging.getLogger(__name__)

# R1 (iii): the text step only looks at turns that finished shortly before the send.
_LINK_WINDOW_BEFORE = timedelta(seconds=120)
_LINK_WINDOW_AFTER = timedelta(seconds=5)
# Bounds the Python scan of a contact's turns for the text route.
_SCAN_LIMIT = 200
_WS = re.compile(r"\s+")


@dataclass(frozen=True)
class QuotedTurn:
    turn_id: str
    via: str  # "id" | "text"
    focus: dict[str, Any] | None
    open_question: dict[str, Any] | None
    turn_no: int | None


def _squash(text: Any) -> str:
    return _WS.sub("", text) if isinstance(text, str) else ""


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
        if _reply_text(turn) == wanted:
            return str(turn.id)
    return None


def _stored_state(turn: ChatbotTurn) -> tuple[dict[str, Any] | None, dict[str, Any] | None, int | None] | None:
    """The state a turn wrote: its `state` event, else the older `memory` event; None if neither."""
    trace = turn.trace if isinstance(turn.trace, list) else []
    for event in trace:
        if isinstance(event, dict) and event.get("kind") == "state" and isinstance(event.get("focus"), dict):
            turn_no = event.get("turn_no")
            return event["focus"], event.get("open_question") or None, turn_no if isinstance(turn_no, int) else None
    for event in trace:
        if isinstance(event, dict) and event.get("kind") == "memory":
            focus = (event.get("focus") or {}).get("after")
            if isinstance(focus, dict):
                return focus, (event.get("open_question") or {}).get("after") or None, None
    return None


def _build(turn: ChatbotTurn, via: str) -> QuotedTurn | None:
    state = _stored_state(turn)
    if state is None:
        return None
    return QuotedTurn(turn_id=str(turn.id), via=via, focus=state[0], open_question=state[1], turn_no=state[2])


def resolve_quoted_turn(
    db: Session,
    *,
    contact_respond_id: str,
    reply_to_id: str | None,
    quoted_text: str | None,
    is_test: bool,
    before: datetime,
) -> QuotedTurn | None:
    """The engine turn a quoted bot message came from, with the state it wrote (R2)."""
    if reply_to_id:
        row = (
            db.query(ChatHistory.chatbot_turn_id)
            .filter(
                ChatHistory.contact_id == contact_respond_id,
                ChatHistory.message_id == str(reply_to_id),
                ChatHistory.type == "outgoing",
                ChatHistory.chatbot_turn_id.isnot(None),
            )
            .order_by(ChatHistory.id.desc())
            .first()
        )
        if row is not None:
            turn = (
                db.query(ChatbotTurn)
                .filter(
                    ChatbotTurn.id == row.chatbot_turn_id,
                    ChatbotTurn.contact_respond_id == contact_respond_id,
                    ChatbotTurn.is_test.is_(is_test),
                )
                .first()
            )
            if turn is not None:
                found = _build(turn, "id")
                if found is not None:
                    return found
    wanted = _squash(quoted_text)
    if not wanted:
        return None
    candidates = (
        db.query(ChatbotTurn)
        .options(load_only(ChatbotTurn.id, ChatbotTurn.response))
        .filter(
            ChatbotTurn.contact_respond_id == contact_respond_id,
            ChatbotTurn.is_test.is_(is_test),
            ChatbotTurn.created_at < _aware(before),
            ChatbotTurn.response.isnot(None),
        )
        .order_by(ChatbotTurn.created_at.desc())
        .limit(_SCAN_LIMIT)
        .all()
    )
    for turn in candidates:
        if _reply_text(turn) == wanted:
            return _build(db.get(ChatbotTurn, turn.id), "text")
    return None
