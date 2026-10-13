"""Which parser a turn runs on (CONTRACT-PV2-13oct.md section 6). Default 'v1'.

'v2' when: a chat-console turn (`engine._chat_console`) asks for it with the envelope
extra `parser_mode: "v2"`, or the contact is a test contact
(`respond_contacts.chatbot_parser_mode = 'v2'`), or the whole copy is switched
(`system_settings.chatbot_parser_mode = 'v2'`, dev copies only; console turns on such a
copy are v2 too). A contact set to 'v1' stays on v1 even on a v2 copy. Rollback = set 'v1'.

Contract deviation, decided by PV2-CORE (noted on the PR): the contract made EVERY console
turn v2. That would move production's console onto v2 the moment PV2-CARRY lands, against
"prod untouched", and silently re-route the v1 console regression suite. A console turn
therefore opts in per envelope; a dev copy switched to v2 needs no opt-in.
"""
from __future__ import annotations

import logging
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

ParserMode = Literal["v1", "v2"]
_MODES = ("v1", "v2")


def _system_mode(db: Session) -> str | None:
    try:
        # Savepoint: a failed read must not poison the turn's transaction.
        with db.begin_nested():
            row = db.execute(
                text("select chatbot_parser_mode from system_settings order by id limit 1")
            ).first()
    except Exception:  # noqa: BLE001 - a copy without the column is a v1 copy
        logger.warning("pv2 flag: system_settings.chatbot_parser_mode unreadable, using v1")
        return None
    return row[0] if row else None


def parser_mode(db: Session, contact: Any, envelope: Any) -> ParserMode:
    if envelope is not None:
        from app.services.chatbot.engine import _chat_console

        extra = getattr(envelope, "model_extra", None) or {}
        if _chat_console(envelope) and extra.get("parser_mode") in _MODES:
            return extra["parser_mode"]
    contact_mode = getattr(contact, "chatbot_parser_mode", None) if contact is not None else None
    if contact_mode in _MODES:
        return contact_mode  # type: ignore[return-value]
    return "v2" if _system_mode(db) == "v2" else "v1"


def contact_settings(
    db: Session, respond_io_id: str | None, *, contact_pk: str | None = None
) -> Any:
    """The contact's own parser mode as an object `parser_mode` reads, or None. Read off
    the turn's resolved contact row when there is one (`respond_io_id` is not unique)."""
    from types import SimpleNamespace

    if not respond_io_id and not contact_pk:
        return None
    if contact_pk:
        sql, params = "select chatbot_parser_mode from respond_contacts where id = :c", {"c": contact_pk}
    else:
        sql = (
            "select chatbot_parser_mode from respond_contacts where respond_io_id = :c "
            "order by id limit 1"
        )
        params = {"c": respond_io_id}
    try:
        with db.begin_nested():
            row = db.execute(text(sql), params).first()
    except Exception:  # noqa: BLE001 - a copy without the column is a v1 copy
        return None
    return SimpleNamespace(chatbot_parser_mode=row[0]) if row else None
