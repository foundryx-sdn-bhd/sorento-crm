"""Recording what a chatbot turn spent on the LLM (SEC2).

Every other LLM call in this system reports into `ai_assistant_usage_logs`. The chatbot
parser runs once per live turn, at production volume, and was the one that did not - so
the bill existed and the table did not show it. `feature = "chatbot_parser"` keeps it
separable from the assistant's own rows.

**Live turns only.** A dry run is a clone or a console turn: it costs a real token spend,
but attributing it to the customer's usage would make every rehearsal look like traffic.
D14 says a test envelope writes nothing outside `chatbot.turns`, and this obeys that.

**`was_answered` is about the CALL, not about the turn.** The engine passes True as soon
as the parser returns a usable emission, even though access, routing, the lane and the
tail all still lie ahead and any of them can fail the turn. That is deliberate and it is
what every other producer on this table does (`ai_assistant_service`, the spec
understanding runs): the row describes one LLM call - its provider, its model, its tokens,
its latency - and the honest question about that call is whether the model answered it. A
turn that dies two stages later still bought and consumed this answer, so flipping the
flag would make the bill stop matching the spend. Whether the CUSTOMER got an answer is a
different question with its own record: `chatbot.turns.status` plus the trace, which is
where the trace screen reads it. Rewriting this row when the turn ends would also mean
holding it open across the rest of the turn, on a session the engine deliberately opens
and closes per stage.

Never raises. A telemetry write that can fail a customer's turn is worse than a missing
row, and the row it would have written is a number, not the answer.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

FEATURE = "chatbot_parser"


def record_parser_usage(
    db: Session,
    *,
    usage: dict[str, Any],
    response_time_ms: int,
    contact_respond_id: str,
    answered: bool,
    chatbot_turn_id: str | None = None,
) -> None:
    """One `ai_assistant_usage_logs` row for one parser call.

    `chatbot_turn_id` (chatbot memory lane A, contract section 7) is which
    `chatbot.turns` row this call billed - every chatbot-parser row carries one.
    """
    try:
        from app.models.access import RespondContact
        from app.models.ai_assistant import AIAssistantUsageLog

        # `contact_id` on this table is the INTERNAL respond_contacts.id, not the
        # respond.io id the envelope carries (the FK is to respond_contacts.id).
        # `respond_io_id` is not unique (`chatbot_allowed_service.is_allowed` reads every
        # row that carries one), and a bare `.scalar()` raised `MultipleResultsFound` on
        # such a contact, which the `except` below swallowed with the whole row
        # (PARSER-GEMINI-LITE). The oldest row stands for the contact.
        internal_id = (
            db.query(RespondContact.id)
            .filter(RespondContact.respond_io_id == contact_respond_id)
            .order_by(RespondContact.created_at, RespondContact.id)
            .limit(1)
            .scalar()
        )
        db.add(
            AIAssistantUsageLog(
                user_id=None,
                contact_id=internal_id,
                feature=FEATURE,
                provider=usage.get("provider"),
                model=usage.get("model"),
                prompt_tokens=int(usage.get("prompt_tokens") or 0),
                completion_tokens=int(usage.get("completion_tokens") or 0),
                total_tokens=int(usage.get("total_tokens") or 0),
                tool_calls_count=0,
                response_time_ms=int(response_time_ms),
                was_answered=bool(answered),
                chatbot_turn_id=chatbot_turn_id,
            )
        )
        db.commit()
    except Exception as exc:  # noqa: BLE001 - telemetry must never fail a customer's turn
        logger.warning("chatbot: parser usage log failed: %s", exc, exc_info=True)
        try:
            db.rollback()
        except Exception:  # pragma: no cover - defensive
            pass
