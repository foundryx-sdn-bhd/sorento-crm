"""Every reply of the one-message ideation capture turn goes through `render_reply`.

The wording lives in ONE place, `chatbot_reply_copy.FALLBACK_REPLY_COPY` (`ideation_capture_*`
entries with en / ms / zh texts and declared `{{tokens}}`). The text is resolved through
`ai_prompt_registry.get_prompt`, so an owner edit wins and the shipped text is the fallback.
The idea number, title, links and the missing-field names are values filled by code; only the
sentence around them follows the user's language.

This is a core twin of `app/services/chatbot/copy.py` `CannedCopy.render_in`, kept here because
core must never import `app.services.chatbot` (AC-002, `tests/chatbot/test_import_boundary.py`).
CHAT-LANGUAGE should lift `render_in` and the language rule into core and delete this twin.
"""
from __future__ import annotations

import logging
from typing import Any

from app.services.chatbot_reply_copy import (
    CHATBOT_REPLY_COPY,
    FALLBACK_LANGUAGES,
    language_suffix,
)

logger = logging.getLogger(__name__)

_PREFIX = "ideation_capture_"
#: Reply kinds whose copy key differs from the kind.
_COPY_NAME = {"ask_idea_gave_up": "give_up"}


def _template(name: str, language: str, db: Any) -> str:
    """The `.ms` / `.zh` text for `name`, the registry's live text first, else the shipped one.
    No session (a caller outside a turn): the shipped text."""
    short = f"{_PREFIX}{name}{language_suffix(language)}"
    if short not in CHATBOT_REPLY_COPY:
        short = f"{_PREFIX}{name}"
    registry_key, shipped, _tokens = CHATBOT_REPLY_COPY[short]
    if db is None:
        return shipped
    try:
        from app.services.ai_prompt_registry import get_prompt

        return get_prompt(db, registry_key).text or shipped
    except Exception:  # noqa: BLE001 - a bot that cannot read its copy still answers
        logger.warning("ideation capture copy %s could not be resolved", registry_key, exc_info=True)
        return shipped


def _fill(text: str, **values: Any) -> str:
    """Substitute `{{token}}`; an unsupplied token is left alone (a loud, greppable defect)."""
    for name, value in values.items():
        text = text.replace("{{" + name + "}}", "" if value is None else str(value))
    return text


def render_reply(
    kind: str, facts: dict[str, Any], *, user_message: str, language: str | None, db: Any = None
) -> str:
    """The reply for `kind` built from `facts`, in `language` (en / ms / zh, else en).

    `user_message` is the message being answered; it is part of the seam so a later language
    mechanism can read it, and is not used by the shipped templates. `db` is the turn's session,
    used only to read the registry's live wording.
    """
    lang = language if language in FALLBACK_LANGUAGES else "en"

    if kind == "similar_offered":
        lines = [_template("similar_offered", lang, db)]
        for n, idea in enumerate(facts.get("similar") or [], start=1):
            line = f"{n}. {idea.get('title')}"
            if idea.get("link"):
                line += f" - {idea['link']}"
            lines.append(line)
        lines.append(_template("similar_offered_reply", lang, db))
        if facts.get("see_all"):
            lines.append(_fill(_template("similar_offered_see_all", lang, db), link=facts["see_all"]))
        return "\n".join(lines)

    if kind == "complete":
        name = "complete" if facts.get("link") else "complete_no_link"
        return _fill(
            _template(name, lang, db),
            idea_number=facts.get("idea_number"),
            title=facts.get("title"),
            missing=", ".join(facts.get("missing") or []),
            link=facts.get("link"),
        )

    if kind == "similar_picked":
        name = "similar_picked" if facts.get("link") else "similar_picked_no_link"
        return _fill(
            _template(name, lang, db),
            idea_number=facts.get("idea_number"),
            link=facts.get("link"),
        )

    return _template(_COPY_NAME.get(kind, kind), lang, db)
