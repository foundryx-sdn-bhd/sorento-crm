"""CHAT-COMPOSER Lists slice red tests, L5: "Not checked: A, ... and N others" in ms / zh.

Spec: `documentation/plans/chatbot/chat-composer-seam-design-lists.md` section 5 (L5) and section 7.
A Localizer rule on the `{names}` value, keyed on a LANE_LABELS entry, so it exists gate on only.
"""
from __future__ import annotations

import pytest

from app.main import app  # noqa: F401  (first app import resolves the guards cycle)
from app.services.chatbot.label_catalog import DIRECT_ONLY, LANE_LABELS, Localizer, defaults
from tests.chatbot.test_composers_stock_xd import _set_gate

NAMES = "A, B, C, D, E, F, G, H, I, J"
ENGLISH = f"Not checked: {NAMES} and 3 others."
TAIL = " and {n} others"


def test_gate_off_names_tail_verbatim(monkeypatch):
    _set_gate(monkeypatch, False)
    assert Localizer("zh", defaults("zh")).reply(ENGLISH) == f"未检查：{NAMES} and 3 others。"
    assert Localizer("ms", defaults("ms")).reply(ENGLISH) == f"Tidak disemak: {NAMES} and 3 others."


@pytest.mark.parametrize(
    "lang,expected",
    [
        ("ms", f"Tidak disemak: {NAMES} dan 3 lagi."),
        ("zh", f"未检查：{NAMES} 等另外 3 个。"),
    ],
)
def test_names_tail_ms_zh(monkeypatch, lang, expected):
    _set_gate(monkeypatch, True)
    assert Localizer(lang, defaults(lang)).reply(ENGLISH) == expected


def test_names_tail_with_thousands_separator(monkeypatch):
    _set_gate(monkeypatch, True)
    out = Localizer("zh", defaults("zh")).reply(f"Not checked: {NAMES} and 1,204 others.")
    assert out == f"未检查：{NAMES} 等另外 1,204 个。"


@pytest.mark.parametrize("gate", [False, True])
@pytest.mark.parametrize("lang", ["ms", "zh"])
def test_no_tail_untouched(monkeypatch, gate, lang):
    """A sentence that merely says "and": the same output in both gate states."""
    _set_gate(monkeypatch, False)
    off = Localizer(lang, defaults(lang)).reply("Couldn't find A and B.")
    _set_gate(monkeypatch, gate)
    assert Localizer(lang, defaults(lang)).reply("Couldn't find A and B.") == off
    assert "and B" in off


def test_catalogue_entries(monkeypatch):
    assert TAIL in LANE_LABELS
    assert LANE_LABELS[TAIL]["ms"] == " dan {n} lagi"
    assert LANE_LABELS[TAIL]["zh"] == " 等另外 {n} 个"
    assert TAIL in DIRECT_ONLY
    from app.services.chatbot.label_catalog import tokens_match

    for lang in ("ms", "zh"):
        assert tokens_match(TAIL, LANE_LABELS[TAIL][lang])
    _set_gate(monkeypatch, False)
    for lang in ("ms", "zh"):
        assert TAIL not in defaults(lang)
    _set_gate(monkeypatch, True)
    for lang in ("ms", "zh"):
        assert TAIL in defaults(lang)
