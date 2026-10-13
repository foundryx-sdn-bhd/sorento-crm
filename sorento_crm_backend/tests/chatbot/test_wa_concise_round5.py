"""WA-CONCISE round 5 (red-first): no rerun offer when no memory line is printed."""
from __future__ import annotations

from app.services.chatbot import copy as copy_mod
from app.services.chatbot.lanes import fallback
from tests.chatbot.test_memory_s4_fallback_units import _ctx


def test_fallback_with_a_last_conversation_offers_the_plain_menu_not_a_rerun():
    copy = copy_mod.fallback_copy()

    text = fallback.compose(
        "Hi!", _ctx(last_time="Thu 25 Sep, Stock: Asked about stock for X and got an answer"), copy, "en"
    )

    assert "any of that again" not in text, text
    assert text == f"Hi! {copy.render('fallback_offer')}", text
