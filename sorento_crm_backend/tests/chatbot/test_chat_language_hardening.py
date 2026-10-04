"""CHAT-LANGUAGE fix round 1, items 8 and 9: footer-lead fallback, the session value, strip."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.chatbot import label_catalog, language, session_state
from app.services.chatbot.contracts import SessionVars
from app.services.chatbot.label_catalog import Localizer
from app.services.chatbot.turn.compose import compose

from tests.chatbot.test_chat_language_render import _env, _policy, _state


def test_item8_an_empty_localized_footer_lead_falls_back_to_the_english_lead():
    """A staff edit that blanks the footer lead must not make `rpartition("\\n_")` split at some
    other italic line: the English lead is used, finds no footer, and the line is appended."""
    table = {**label_catalog.defaults("ms"), "Data last updated: {ts}": "{ts}"}
    lane = "*Kod Produk:* SRTSWT3001-GM\n_an italic note_"
    ctx = SimpleNamespace(localizer=Localizer("ms", table))
    text = compose([_env(lane)], _state(), _policy(), ctx=ctx).text
    assert text.index("_an italic note_") < text.index("Tiada stok ditemui untuk SRTSWT3001.")


def test_item8_session_vars_reply_language_is_en_ms_zh_or_none():
    assert SessionVars(reply_language="ms").reply_language == "ms"
    assert SessionVars().reply_language is None
    with pytest.raises(Exception):
        SessionVars(reply_language="fr")


def test_item8_an_unknown_stored_language_is_dropped_on_read():
    block = {"session_vars": {"focus": None, "reply_language": "fr"}}
    assert session_state.five_keys(block)["reply_language"] is None
    block = {"session_vars": {"focus": None, "reply_language": "zh"}}
    assert session_state.five_keys(block)["reply_language"] == "zh"
    legacy = {"session_vars": {"variables": {"reply_language": "xx", "x": 1}}}
    assert session_state.five_keys(legacy)["reply_language"] is None


def test_item9_localizer_has_a_named_sentence_method():
    loc = Localizer("ms", label_catalog.defaults("ms"))
    assert loc.sentence("Here are the results.") == "Berikut ialah hasilnya."
    assert not hasattr(loc, "__call__")


def test_item9_strip_removes_whole_words_only():
    # "bil" inside the marker word "bila" is not the name: stripping it leaves "bila" alone.
    assert language.detect("bila", strip=["bil"]) == "ms"
    # A whole-word occurrence is stripped.
    assert language.detect("ADA HARDWARE", strip=["ADA"]) is None
    assert language.detect("tolong ADA", strip=["ADA"]) == "ms"
