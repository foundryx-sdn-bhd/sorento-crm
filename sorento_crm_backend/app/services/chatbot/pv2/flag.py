"""Which parser a turn runs on (CONTRACT-PV2-13oct.md section 6). Default 'v1'.

'v2' when: the turn is a chat-console turn (`engine._chat_console`), or the contact is a
test contact (`respond_contacts.chatbot_parser_mode = 'v2'`), or the whole copy is switched
(`system_settings.chatbot_parser_mode = 'v2'`, dev copies only). A contact set to 'v1'
stays on v1 even on a v2 copy. Rollback = set 'v1'.
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

        if _chat_console(envelope):
            return "v2"
    contact_mode = getattr(contact, "chatbot_parser_mode", None) if contact is not None else None
    if contact_mode in _MODES:
        return contact_mode  # type: ignore[return-value]
    return "v2" if _system_mode(db) == "v2" else "v1"
