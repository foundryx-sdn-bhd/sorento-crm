"""CHAT-COMPOSER: one reply composer per answer type, behind `CHAT_COMPOSER_ENABLED`.

Imports only `jsc`, `app.config` (lazily) and its own modules: never engine, head, tail,
dialogue, `turn.compose` or lanes, so `turn/` can import it without a cycle
(`tests/chatbot/test_composers_s0.py`).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.services.chatbot.turn.pending import Pending


@dataclass(frozen=True)
class Reply:
    """A composed sentence plus the question or offer it carries, as data."""

    text: str
    question: "Pending | None" = None
    offer: Any = None


def enabled() -> bool:
    """The lane's one gate, read at call time so a monkeypatch takes effect. Off = main's text."""
    from app.config import settings

    return bool(getattr(settings, "chat_composer_enabled", False))
