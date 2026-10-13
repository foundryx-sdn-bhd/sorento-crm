"""MULTI-ASK-PLAN2: the owner's hand-test script (laneboard/scripts/1542.md, steps 4, 5, 6, 8)
replayed through the engine with the parser's RECORDED live emissions of the new prompt
(`fixtures/multi_ask_live_20261012.json`, gpt-5.4-mini on the maskp_0001 template), so the
replies the owner accepted on r7 are proven on the trimmed prompt without a live call.

The catalogue has the hand-test shape: SRTWT7445 is an exact row with eight variants.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.chatbot import _r9_engine_console as r9
from tests.chatbot.test_engine import stub_access  # noqa: F401 - a pytest fixture
from tests.chatbot.test_multi_ask_runner import _console, _ids

FAMILY = ["SRTWT7445"] + [f"SRTWT7445-{s}" for s in ("BK", "BN", "CP", "GM", "GD", "SS", "WH", "NEW")]
STEPS = json.loads((Path(__file__).parent / "fixtures" / "multi_ask_live_20261012.json").read_text(encoding="utf-8"))["steps"]


@pytest.fixture(autouse=True)
def hand_test_catalogue(monkeypatch):
    monkeypatch.setattr(r9, "OWNER_FAMILY", FAMILY)
    monkeypatch.setattr(r9, "OTHER_CODES", ["ELP3754"])


def _say(c, step: str) -> str:
    return c.say(STEPS[step]["message"], STEPS[step]["verdict"])


def test_steps_4_5_6_the_six_asks_scope_then_basis(session_factory, monkeypatch, stub_access):
    c = _console(session_factory, monkeypatch, stub_access, "+60000009901")
    step4 = _say(c, "step4")
    assert step4.splitlines() == ["SRTWT7445 有 9 个款式。只要 SRTWT7445，还是全部 9 款？"]
    assert not c.tools.calls

    step5 = _say(c, "step5").splitlines()
    assert step5[0] == "SRTWT7445"
    assert step5[1:] == [
        "2026 入库数量: 1,200",
        "供应商: ABC SDN BHD（PO-2026-0412，03/06/2026）",
        "采购价: RM 45.00 / 件（PO-2026-0412，03/06/2026）",
        "最后一次进库: 18/09/2026（300，SPO-2026-0101）",
        "卖出按已出货还是按下单算？",
    ]
    uuid_7445 = c.uuid_of["SRTWT7445"]
    for _name, args in c.tools.calls:
        assert _ids(args.get("product_ids")) in ([], [uuid_7445])

    step6 = _say(c, "step6").splitlines()
    assert step6[0] == "SRTWT7445"
    assert "2026 总共卖出（已出货）: 860" in step6
    assert "2026 每位业务员卖出（已出货）:" in step6
    assert any(ln.startswith("1. AGENT01") for ln in step6)
    assert (c.stored_question or {}).get("kind") not in ("sub_ask_scope", "sub_ask_basis")


def test_step_8_the_whole_family_sold_and_cost_with_no_question(session_factory, monkeypatch, stub_access):
    c = _console(session_factory, monkeypatch, stub_access, "+60000009902")
    lines = _say(c, "step8").splitlines()
    assert lines[0] == "SRTWT7445（全部 9 款）"
    assert "2026 总共卖出（已出货）: 860" in lines
    assert any(ln.startswith("采购价: RM 45.00 / 件（PO-2026-0412") for ln in lines)
    assert (c.stored_question or {}).get("kind") not in ("sub_ask_scope", "sub_ask_basis")


def test_step_9_a_missing_variant_is_never_answered_with_a_sibling(session_factory, monkeypatch, stub_access):
    """Review B1 on the live emission: SRTWT7445-ZZ does not exist."""
    c = _console(session_factory, monkeypatch, stub_access, "+60000009903")
    text = _say(c, "step9")
    sibling_ids = {pid for code, pid in c.uuid_of.items() if code in FAMILY}
    for _name, args in c.tools.calls:
        assert not sibling_ids & set(_ids(args.get("product_ids"))), _name
    assert 'Couldn\'t find "SRTWT7445-ZZ"' in text
