"""CHAT-COMPOSER gate-off byte identity (AC-G1) and output hygiene (AC-G3).

Goldens are literals captured from main (HEAD 2dd7cf0e5e, 17 Oct) by running today's code.
Every test here must pass at red and after S0+S1.
"""
from __future__ import annotations

import re

import pytest

from app.config import settings
from app.services.chatbot.turn import compose as turn_compose
from tests.chatbot import _ht26_fixtures as ht
from tests.chatbot._turn_helpers import verdict
from tests.chatbot.test_composers_stock_xd import _render, _stock_item, _zs

EM = "\u2014"


@pytest.fixture(autouse=True)
def _gate_off(monkeypatch):
    from tests.chatbot.test_composers_stock_xd import _set_gate

    _set_gate(monkeypatch, False)


class _Spec:
    domain = "inventory"
    entities = [{"uuid": "x"}]
    filters: dict = {}


class _Plan:
    fetch = [_Spec()]


def _stock_reply(envelopes, asked):
    from app.services.chatbot.engine import _stock_ask_reply

    return _stock_ask_reply(
        turn_compose.Answer(text="How many units do you need?"),
        ht.state(availability_only=True),
        envelopes,
        _Plan(),
        verdict(domain_hint="inventory", entities=asked),
        turn_no=1,
    ).text


def test_golden_dealer_mix_with_a_miss():
    item = {
        "title": "CB890SS-BL x 10: ❌ No stock and no incoming. Please refer to your salesman.",
        "flags": {
            "branch": "no_stock",
            "discontinued": True,
            "alternative_lines": ["CB890SS-BL-DIY x 10: ❌ No stock, ETA 21/10/2026."],
        },
    }
    env = [
        {
            "stock_availability": [
                ht.row("CB890SS-BL", needs_quantity=False, requested_qty=10, branch="no_stock")
            ],
            "items": [item],
            "unresolved": ["cb890-bl", "cb890-GM- diy"],
        }
    ]
    assert _stock_reply(env, [ht.asked("CB890SS-BL", 10)]) == (
        "CB890SS-BL x 10: ❌ No stock and no incoming. Please refer to your salesman."
        "\n\nCouldn't find: cb890-bl, cb890-GM- diy."
    )


def test_golden_dealer_eta_with_a_miss():
    from app.services.chatbot.engine import _dealer_names_the_miss

    ans = turn_compose.Answer(
        text="SRTW2000: ✅ ETA 19/10/2026\n\nPlease refer to your salesman.\n\nI could not find FOO99."
    )
    assert _dealer_names_the_miss(ans, [{"unresolved": ["FOO99"]}]).text == (
        "SRTW2000: ✅ ETA 19/10/2026\n\nCouldn't find: FOO99.\n\nPlease refer to your salesman."
    )


def test_golden_staff_compact_eta_miss_stock_block():
    xd = _render([_stock_item()], _zs("incoming", "SRT5734-WH"))
    assert "rendered_codes" not in xd
    assert xd["block"] == (
        "No incoming for SRT5734-WH.\n\nBut here are the stock details for the requested products:"
        "\n\n- *Product Code:* SRT5734-WH\n*Total:* 60 (O/S: 0)\n*BRW:* 60 (O/S: 0)"
    )


def test_golden_staff_two_codes_incoming_origin():
    a, b = _stock_item("SRT5734-WH", 60, None), _stock_item("SRT5735-WH", 7, None)
    for it in (a, b):
        it["fields"].pop()
    xd = _render([a, b], _zs("incoming", "SRT5734-WH", "SRT5735-WH"))
    assert xd["block"] == (
        "No incoming for SRT5734-WH, SRT5735-WH.\n\nBut here are the stock details for the requested products:"
        "\n\n- *Product Code:* SRT5734-WH\n*Total:* 60\n\n- *Product Code:* SRT5735-WH\n*Total:* 7"
    )


def test_golden_staff_detailed_block():
    from app.services.chatbot.lanes.business.fetch import output_structurer

    det = [
        {
            "fields": [
                {"key": "product_code", "label": "Product Code", "value": "SRT1"},
                {"key": "w", "label": "Warehouse", "value": "BRW-BB"},
                {"key": "q", "label": "Quantity On Hand", "value": 12},
            ],
            "flags": {"discontinued": True},
        },
        {
            "fields": [
                {"key": "product_code", "label": "Product Code", "value": "SRT2"},
                {"key": "w", "label": "Warehouse", "value": None},
                {"key": "q", "label": "Quantity On Hand", "value": 5.5},
            ]
        },
    ]
    env = {
        "items": det,
        "answers": [{"text": "x"}],
        "has_result": True,
        "last_updated_at": "2026-10-02T09:15:30",
    }
    text = output_structurer(env, {"tool": "crm_inventory_stock_balance_list", "semantic_input": {}})["response"]
    assert text == (
        "Here are the results.\n\n1. *Product Code:* SRT1\n*Warehouse:* BRW-BB\n*Quantity On Hand:* 12"
        "\n⚠️  *(PRODUCT DISCONTINUED)*\n\n2. *Product Code:* SRT2\n*Warehouse:* " + EM
        + "\n*Quantity On Hand:* 5.5\n\n_Data last updated: 02/10/2026 09:15:30_"
    )


def test_golden_quantity_question_with_three_slots():
    from app.services.chatbot.turn import task as task_mod

    reply = task_mod.after_reply(
        (),
        ht.envelopes(ht.row("A1"), ht.row("B2"), ht.row("C3")),
        asked=[ht.asked("A1"), ht.asked("B2"), ht.asked("C3")],
    )
    assert reply.text == "How many units for each?\n1. A1 - \n2. B2 - \n3. C3 - "


# --------------------------------------------------------------------------- #
# Property: no composer output carries a dot separator, uuid or count line
# --------------------------------------------------------------------------- #

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
COUNT_RES = [
    re.compile(r"\(\d[\d,]* (products?|items?)\)"),
    re.compile(r"matches \d+ products"),
    re.compile(r"Showing \d+ of"),
    re.compile(r"and \d+ (more|others)"),
]
UUID = "123e4567-e89b-12d3-a456-426614174000"
ITEMS = [
    _stock_item(),
    {"fields": [{"key": "product_code", "label": "Product Code", "value": "SRT1"}], "title": "SRT1 name"},
    {"title": "fallback name only", "fields": []},
    {"fields": [{"key": "product_code", "label": "Product Code", "value": UUID[:0] + "A-B"}]},
]


@pytest.mark.parametrize("gate", [False, True])
@pytest.mark.parametrize("idx", range(len(ITEMS)))
def test_composer_output_clean(monkeypatch, gate, idx):
    from app.services.chatbot.composers import stock

    from tests.chatbot.test_composers_stock_xd import _set_gate

    _set_gate(monkeypatch, gate)
    item = ITEMS[idx]
    outs = [
        stock.block(1, item),
        stock.miss_line(["X", "cb890-GM- diy"]),
        stock.none_for("stock", ["X", "Y"]),
    ]
    for text in outs:
        assert " · " not in text
        assert not UUID_RE.search(text)
        for rx in COUNT_RES:
            assert not rx.search(text), (rx.pattern, text)
