"""CHAT-COMPOSER S4 red tests (files): the attached sentence once per reply, one de-dup rule,
one missing-file sentence on a hit (R4), the fit-line guard for ms and zh.

`lane_intro` is the data key the design (seam design S4, K3) reads instead of searching the text.
Goldens are literals captured from main code on 17 Oct (S3a landed, gate off).
"""
from __future__ import annotations

import pytest

from app.services.chatbot.label_catalog import Localizer, defaults
from app.services.chatbot.turn.compose import compose
from app.services.chatbot.turn.policy import Policy
from app.services.chatbot.turn.state import Focus, Profile, State
from tests.chatbot._turn_helpers import TIER_ORDER_FIXTURE, _domain_row
from tests.chatbot.test_attachment_multi_type import PHOTOS, SH, SH200, _attachment_envelope
from tests.chatbot.test_composers_stock_xd import _set_gate

SENT = "I have attached the file(s) below."
MS_SENT = "Saya telah melampirkan fail di bawah."


def _policy():
    rows = [
        {**_domain_row("product_attachment", narrowing={"product": "must_narrow_one"}), "label": "product attachments"},
        {**_domain_row("promotion", narrowing={}), "label": "promotions"},
    ]
    return Policy.from_rows(domains=rows, kinds=[], tier_order=TIER_ORDER_FIXTURE)


def _state():
    return State(focus=Focus(), pending=None, profile=Profile(), turn_no=2)


def _promo(files=None):
    return {
        "domain": "promotion",
        "denied": False,
        "entities": [],
        "product_codes": [],
        "figures": [{"fields": [{"label": "Promotion", "value": "CABANA"}, {"label": "End Date", "value": "2026-12-25"}]}],
        "files": files or [{"url": "https://example.test/promo.pdf", "filename": "promo.pdf"}],
        "miss": [],
        "has_result": True,
        "tool_has_result": True,
        "unresolved": [],
        "error": None,
        "lane_text": f"{SENT}\n\n1. *Promotion:* CABANA\n*End Date:* 2026-12-25",
        "lane_intro": SENT,
    }


def _attach(files=None):
    env = _attachment_envelope([SH], [(SH, PHOTOS)], [PHOTOS])
    env["lane_intro"] = SENT
    if files is not None:
        env["files"] = files
    return env


class _Ctx:
    def __init__(self, lang):
        self.localizer = Localizer(lang, defaults(lang))


def _ms_envelope():
    return {
        "domain": "product_attachment",
        "denied": False,
        "entities": [SH],
        "product_codes": [SH],
        "attachment_types": [],
        "figures": [],
        "files": [{"url": "https://example.test/c.pdf", "filename": "c.pdf"}],
        "miss": [],
        "has_result": True,
        "tool_has_result": True,
        "unresolved": [],
        "error": None,
        "lane_text": f"{MS_SENT}\n\n1. *Kod Produk:* X",
        "lane_intro": MS_SENT,
    }


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def test_files_helpers():
    from app.services.chatbot.composers import files
    from app.services.chatbot.composers import format as fmt

    assert files.ATTACHED_SENTENCE == SENT
    entries = [{"url": "u1", "fileHash": "h"}, {"url": "u2", "fileHash": "h"}, {"url": None}, {"url": "u1"}]
    assert files.sendable(entries) == [{"url": "u1", "fileHash": "h"}]
    assert fmt.missing_line("Product Photos", "X") == "No Product Photos on record for X."
    seen = []

    def fill(key, **kw):
        seen.append((key, kw))
        return "filled"

    assert fmt.missing_line("Product Photos", "X", fill=fill) == "filled"
    assert seen == [("No {what} on record for {product}.", {"what": "Product Photos", "product": "X"})]


def test_place_attached():
    from app.services.chatbot.composers import files

    a, b = f"{SENT}\n\nA", f"{SENT}\n\nB"
    assert files.place_attached([a, b], [True, True], SENT, True) == [a, "B"]
    assert files.place_attached(["A", "B"], [False, False], SENT, True) == ["A", "B", SENT]
    assert files.place_attached(["A", "B"], [False, False], SENT, False) == ["A", "B"]
    ms = f"{MS_SENT}\n\nA"
    assert files.place_attached([ms], [True], MS_SENT, True) == [ms]


# --------------------------------------------------------------------------- #
# F1: the attached sentence once
# --------------------------------------------------------------------------- #


def test_attached_once_two_sections_gate_on(monkeypatch):
    _set_gate(monkeypatch, True)
    text = compose([_promo(), _attach()], _state(), _policy(), ctx=None).text
    assert text.count(SENT) == 1, text
    assert text.startswith(f"{SENT}\n\n1. *Promotion:* CABANA"), text


def test_attached_two_sections_gate_off_golden(monkeypatch):
    _set_gate(monkeypatch, False)
    text = compose([_promo(), _attach()], _state(), _policy(), ctx=None).text
    assert text == f"{SENT}\n\n1. *Promotion:* CABANA\n*End Date:* 2026-12-25\n\n{SENT}"


def test_attached_once_localized_gate_on(monkeypatch):
    _set_gate(monkeypatch, True)
    ctx = _Ctx("ms")
    text = compose([_ms_envelope()], _state(), _policy(), ctx=ctx).text
    assert text == f"{MS_SENT}\n\n1. *Kod Produk:* X"
    assert SENT not in ctx.localizer.reply(text)
    assert ctx.localizer.reply(text).count(MS_SENT) == 1


def test_attached_localized_gate_off_golden(monkeypatch):
    _set_gate(monkeypatch, False)
    ctx = _Ctx("ms")
    text = compose([_ms_envelope()], _state(), _policy(), ctx=ctx).text
    assert text == f"{MS_SENT}\n\n1. *Kod Produk:* X\n\n{SENT}"
    assert ctx.localizer.reply(text).count(MS_SENT) == 2


@pytest.mark.parametrize("gate", [False, True])
def test_attached_single_section_byte_identical(monkeypatch, gate):
    """cp0-d1977a0368 shape: one section opening with the sentence."""
    _set_gate(monkeypatch, gate)
    env = _attachment_envelope([SH], [(SH, PHOTOS)], [PHOTOS])
    env["lane_intro"] = SENT
    env["lane_text"] = (
        f"{SENT}\n\n1. *Product Code:* SRTWC286-SH\n*Attachment Type:* Technical Specifications"
        "\n*File Name:* SRTWC286-SH.jpg\n\n_Data last updated: 02/10/2026 09:47:43_"
    )
    assert compose([env], _state(), _policy(), ctx=None).text == env["lane_text"]


@pytest.mark.parametrize("gate", [False, True])
def test_sentence_appended_when_no_section_opened_with_it(monkeypatch, gate):
    _set_gate(monkeypatch, gate)
    env = _attach()
    env["lane_intro"] = None
    env["lane_text"] = "1. *Product Code:* SRTWC286-SH"
    assert compose([env], _state(), _policy(), ctx=None).text == f"1. *Product Code:* SRTWC286-SH\n\n{SENT}"


# --------------------------------------------------------------------------- #
# F2: one de-dup rule
# --------------------------------------------------------------------------- #


def test_compose_dedupe_matches_send_gate_on(monkeypatch):
    p = _promo([{"url": "u1", "filename": "a", "fileHash": "h"}])
    q = _attach([{"url": "u1", "filename": "b"}])
    _set_gate(monkeypatch, False)
    assert len(compose([p, q], _state(), _policy(), ctx=None).files) == 2
    _set_gate(monkeypatch, True)
    files = compose([p, q], _state(), _policy(), ctx=None).files
    assert len(files) == 1 and files[0]["url"] == "u1"


# --------------------------------------------------------------------------- #
# F3: the missing-file sentence on a hit
# --------------------------------------------------------------------------- #


def _gap_env():
    env = _attachment_envelope([SH, SH200], [(SH, PHOTOS)], [PHOTOS])
    env["lane_text"] += "\n\n_Data last updated: 03/10/2026 16:24:40_"
    env["lane_intro"] = SENT
    return env


def test_gap_line_gate_off_golden(monkeypatch):
    _set_gate(monkeypatch, False)
    text = compose([_gap_env()], _state(), _policy(), ctx=None).text
    assert text == f"{SENT}\n\nSRTWC286-SH-200 has no Product Photos.\n\n_Data last updated: 03/10/2026 16:24:40_"


def test_gap_line_gate_on(monkeypatch):
    _set_gate(monkeypatch, True)
    text = compose([_gap_env()], _state(), _policy(), ctx=None).text
    assert text == f"{SENT}\n\nNo Product Photos on record for SRTWC286-SH-200.\n\n_Data last updated: 03/10/2026 16:24:40_"
    assert "has no" not in text


@pytest.mark.parametrize(
    "lang,expected",
    [
        ("ms", "Tiada Product Photos dalam rekod untuk SRTWC286-SH-200."),
        ("zh", "SRTWC286-SH-200 没有Product Photos记录。"),
    ],
)
def test_gap_line_ms_zh_gate_on(monkeypatch, lang, expected):
    _set_gate(monkeypatch, True)
    ctx = _Ctx(lang)
    env = _gap_env()
    text = compose([env], _state(), _policy(), ctx=ctx).text
    assert expected in ctx.localizer.reply(text), ctx.localizer.reply(text)


# --------------------------------------------------------------------------- #
# OI-1 guard: the fit line must not be re-translated by the new key (gate OFF literals)
# --------------------------------------------------------------------------- #


def test_fit_line_not_retranslated():
    line = "No seat cover is on record for SRTWC286-SH."
    assert Localizer("ms", defaults("ms")).reply(line) == "Tiada penutup tempat duduk direkodkan untuk SRTWC286-SH."
    assert Localizer("zh", defaults("zh")).reply(line) == "没有 SRTWC286-SH 的马桶盖记录。"


def test_new_missing_line_translates_via_the_catalogue(monkeypatch):
    _set_gate(monkeypatch, True)
    line = "No Product Photos on record for SRTWC286-SH."
    assert Localizer("ms", defaults("ms")).reply(line) == "Tiada Product Photos dalam rekod untuk SRTWC286-SH."
    assert Localizer("zh", defaults("zh")).reply(line) == "SRTWC286-SH 没有Product Photos记录。"
