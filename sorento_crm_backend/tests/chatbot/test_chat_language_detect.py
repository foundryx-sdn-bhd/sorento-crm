"""CHAT-LANGUAGE slice 1 (stock), AC-CL05 and AC-CL06: per-message language detection.

Behaviour card `documentation/plans/chatbot/chat-language-behaviour-card.md`. Pure: no database,
no model. `app.services.chatbot.language` does not exist yet, so every test is red on import.
"""
from __future__ import annotations

import pytest

from app.services.chatbot import language
from app.services.chatbot.language import choose, detect, for_turn


# --------------------------------------------------------------------------- #
# AC-CL05: the detection table, exact
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "message, expected",
    [
        ("SRTSWT3001 有货吗", "zh"),
        ("ada stok SRTSWT3001?", "ms"),
        ("SRTSWT3001 ada stock tak", "ms"),
        ("berapa unit kat PKL?", "ms"),
        ("can check stock SRTSWT3001 ah", "en"),
        ("how many SRTSWT3001 in stock", "en"),
        ("SRTSWT3001", None),
        ("1", None),
        ("ok", None),
        ("stock ada?", None),
        ("ya", "ms"),
        ("bukan", "ms"),
        ("satu dua tiga", "ms"),
    ],
)
def test_ac_cl05_detection_table(message, expected):
    assert detect(message) == expected


def test_ac_cl05_strip_removes_a_customer_name_before_counting():
    assert detect("ADA HARDWARE stock please", strip=["ADA HARDWARE"]) == "en"


def test_ac_cl05_without_the_strip_the_name_would_tip_it():
    """A customer name that happens to be a marker word ("ADA") decides the language when it
    is not stripped, and decides nothing when it is."""
    assert detect("ADA HARDWARE", strip=["ADA HARDWARE"]) is None
    assert detect("ADA HARDWARE") == "ms"


def test_ac_cl05_chinese_beats_every_other_rule():
    assert detect("ada stok 有货吗") == "zh"


def test_ac_cl05_strip_accepts_a_tuple_and_defaults_to_empty():
    assert detect("hello SRTSWT3001", strip=()) is None
    assert detect("how many", strip=("nothing here",)) == "en"


# --------------------------------------------------------------------------- #
# AC-CL06: choose
# --------------------------------------------------------------------------- #


def test_ac_cl06_choose_takes_the_first_supported_in_order():
    assert choose("ms", "zh", "en") == "ms"
    assert choose(None, "zh", "ms") == "zh"
    assert choose(None, None, "ms") == "ms"
    assert choose(None, None, None) == "en"


def test_ac_cl06_choose_skips_unsupported_languages():
    assert choose(None, "fr", "zh") == "zh"
    assert choose("de", None, None) == "en"
    assert choose(None, "", "") == "en"


# --------------------------------------------------------------------------- #
# AC-CL06: for_turn, with a plain dict as session_vars
# --------------------------------------------------------------------------- #


def test_ac_cl06_example_e_the_carry_sequence():
    sv: dict = {}
    assert for_turn("stock SRTSWT3001", sv, None) == "en"
    assert sv["reply_language"] == "en"
    assert for_turn("berapa unit kat PKL?", sv, None) == "ms"
    assert sv["reply_language"] == "ms"
    # A pick decides nothing, so the conversation's last language carries.
    assert for_turn("1", sv, None) == "ms"
    assert sv["reply_language"] == "ms"


def test_ac_cl06_saved_language_is_the_fallback_when_nothing_carries():
    sv: dict = {}
    assert for_turn("1", sv, "zh") == "zh"
    assert sv["reply_language"] == "zh"


def test_ac_cl06_nothing_at_all_is_english():
    sv: dict = {}
    assert for_turn("1", sv, None) == "en"
    assert sv["reply_language"] == "en"


def test_ac_cl06_the_message_beats_the_carry_and_the_saved_language():
    sv = {"reply_language": "ms"}
    assert for_turn("how many SRTSWT3001 in stock", sv, "zh") == "en"
    assert sv["reply_language"] == "en"


def test_ac_cl06_the_carry_beats_the_saved_language():
    sv = {"reply_language": "ms"}
    assert for_turn("ok", sv, "zh") == "ms"


def test_ac_cl06_for_turn_passes_strip_through():
    sv: dict = {}
    assert for_turn("ADA HARDWARE stock please", sv, "ms", strip=["ADA HARDWARE"]) == "en"


def test_language_module_exposes_the_contract():
    assert callable(language.detect) and callable(language.choose) and callable(language.for_turn)
