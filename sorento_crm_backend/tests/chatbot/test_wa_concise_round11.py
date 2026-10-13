"""WA-CONCISE round 11 (red-first): the card-v4 block labels follow the reply language.

The English reply comes from a real whole turn (the round 2 / round 9 cross-domain harness);
it then goes through the localizer exactly as the turn's final pass does
(`Localizer.reply`, as `test_chat_language_incoming.py`). Values (codes, numbers, dates) are
never translated. Placeholder data only.
"""
from __future__ import annotations

import pytest

from app.services.chatbot import label_catalog
from app.services.chatbot.label_catalog import LABELS, Localizer
from tests.chatbot import test_wa_concise_crossdomain as xd
from tests.chatbot import test_wa_concise_review_round2 as r2
from tests.chatbot._wa_concise_helpers import (
    INCOMING_TOOL,
    PO_TOOL,
    STOCK_TOOL,
    detailed_row,
    envelope_json,
)
from tests.chatbot.test_engine import (  # noqa: F401 - fixtures re-exported by name
    seeded,
    stub_access,
    stub_parser,
)


def _loc(lang: str) -> Localizer:
    return Localizer(lang, label_catalog.defaults(lang))


def _english(po_rows, session_factory, stub_parser, stub_access, monkeypatch) -> str:
    def po(args):
        if not po_rows:
            return envelope_json(PO_TOOL, xd.EMPTY_PAYLOAD)
        return envelope_json(PO_TOOL, {"data": po_rows, "pagination": {"total": len(po_rows)}})

    tools = {
        INCOMING_TOOL: xd._incoming({}),
        STOCK_TOOL: r2._detailed_resp([detailed_row("Sorento", "SRTX2", "BRW", 0, 0)], ["Sorento"]),
        PO_TOOL: po,
    }
    said, _ = xd._ask_incoming(
        **r2._fx(session_factory, stub_parser, stub_access, monkeypatch), codes=["SRTX2"], tools=tools
    )
    return said


_PO = {
    "po_number": "PO-0001", "product_code": "SRTX2", "ordered_qty": 10, "outstanding_qty": 10,
    "po_date": "2026-09-11", "location": "BRW",
}


def test_d_catalog_has_the_card_block_labels():
    assert LABELS["Incoming"] == {"ms": "Barang masuk", "zh": "到货"}
    assert LABELS["Stock"] == {"ms": "Stok", "zh": "库存"}
    assert LABELS["PO"] == {"ms": "PO", "zh": "PO"}
    assert LABELS["SPO"] == {"ms": "SPO", "zh": "SPO"}
    assert LABELS["none"] == {"ms": "tiada", "zh": "无"}


def test_e_guard_english_reply_is_unchanged(
    session_factory, seeded, stub_parser, stub_access, system_settings_row, monkeypatch
):
    said = _english([], session_factory, stub_parser, stub_access, monkeypatch)

    lines = said.split("\n")
    assert lines[:4] == ["*Product Code:* SRTX2", "*Incoming:* none", "*Stock:* 0", "*PO:* none"], said


@pytest.mark.parametrize(
    ("lang", "code", "incoming", "stock", "po"),
    [
        ("ms", "*Kod Produk:* SRTX2", "*Barang masuk:* tiada", "*Stok:* 0", "*PO:* tiada"),
        ("zh", "*产品代码:* SRTX2", "*到货:* 无", "*库存:* 0", "*PO:* 无"),
    ],
)
def test_ab_zero_stock_block_labels_and_none_follow_the_reply_language(
    lang, code, incoming, stock, po,
    session_factory, seeded, stub_parser, stub_access, system_settings_row, monkeypatch,
):
    english = _english([], session_factory, stub_parser, stub_access, monkeypatch)

    out = _loc(lang).reply(english)

    assert out.split("\n")[:4] == [code, incoming, stock, po], out


def test_c_zh_placed_po_keeps_the_number_and_uses_the_existing_labels(
    session_factory, seeded, stub_parser, stub_access, system_settings_row, monkeypatch
):
    english = _english([_PO], session_factory, stub_parser, stub_access, monkeypatch)

    lines = _loc("zh").reply(english).split("\n")

    assert lines[:8] == [
        "*产品代码:* SRTX2", "*到货:* 无", "*库存:* 0", "*PO:* PO-0001",
        "*订购:* 10", "*未交货:* 10", "*PO 日期:* 2026-09-11", "*位置:* BRW",
    ], lines
