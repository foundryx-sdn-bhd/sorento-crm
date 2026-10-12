"""MULTI-ASK-PLAN2, owner ruling 12 Oct 2026: in a 2+ ask turn an EXACT existing variant code
(SRTWT7445-LV) skips the family scope question and answers that code; only a base / family
code (SRTWT7445, with several variants) gets "only this one or all N?".

The catalogue has the hand-test shape: SRTWT7445 is an exact row with eight variants.
"""
from __future__ import annotations

import pytest

from tests.chatbot import _r9_engine_console as r9
from tests.chatbot._turn_helpers import entity, verdict
from tests.chatbot.test_engine import stub_access  # noqa: F401 - a pytest fixture
from tests.chatbot.test_multi_ask_runner import _console, _ids, sub

FAMILY = ["SRTWT7445"] + [f"SRTWT7445-{s}" for s in ("BK", "BN", "CP", "GM", "LV", "SS", "WH", "NEW")]


@pytest.fixture(autouse=True)
def hand_test_catalogue(monkeypatch):
    monkeypatch.setattr(r9, "OWNER_FAMILY", FAMILY)
    monkeypatch.setattr(r9, "OTHER_CODES", ["ELP3754"])


def _two_asks(code: str) -> dict:
    return verdict(
        domain_hint="inventory",
        intent_hint="check_stock",
        entities=[entity(code, hint="product")],
        sub_asks=[sub("stock", "stock"), sub("last_cost", "last cost")],
        family=None,
    )


def test_an_exact_variant_code_answers_that_code_with_no_scope_question(session_factory, monkeypatch, stub_access):
    c = _console(session_factory, monkeypatch, stub_access, "+60000009931")
    lines = c.say("SRTWT7445-LV stock and last cost", _two_asks("SRTWT7445-LV")).splitlines()
    assert lines[0] == "SRTWT7445-LV"
    assert "Stock: 120" in lines
    assert any(ln.startswith("Last cost: ") for ln in lines)
    assert (c.stored_question or {}).get("kind") != "sub_ask_scope"
    lv = c.uuid_of["SRTWT7445-LV"]
    assert c.tools.calls
    for name, args in c.tools.calls:
        assert _ids(args.get("product_ids")) in ([], [lv]), name


def test_a_base_code_with_variants_still_asks_only_this_one_or_all(session_factory, monkeypatch, stub_access):
    c = _console(session_factory, monkeypatch, stub_access, "+60000009932")
    text = c.say("SRTWT7445 stock and last cost", _two_asks("SRTWT7445"))
    assert text.splitlines() == ["SRTWT7445 has 9 variants. Just SRTWT7445, or all 9?"]
    assert (c.stored_question or {}).get("kind") == "sub_ask_scope"
    assert not c.tools.calls


def test_all_about_the_head_never_widens_a_variant_typed_beside_it(session_factory, monkeypatch, stub_access):
    """Review B1 (a4a02904): "all" answers the question about SRTWT7446 only; SRTWT7445-LV,
    never asked about, stays the exact code."""
    from tests.chatbot.test_multi_ask_runner import pick

    monkeypatch.setattr(r9, "OTHER_CODES", ["ELP3754", "SRTWT7446", "SRTWT7446-A"])
    c = _console(session_factory, monkeypatch, stub_access, "+60000009933")
    two = verdict(
        domain_hint="inventory",
        intent_hint="check_stock",
        entities=[entity("SRTWT7445-LV", hint="product"), entity("SRTWT7446", hint="product")],
        sub_asks=[sub("stock", "stock"), sub("last_cost", "last cost")],
        family=None,
    )
    asked = c.say("SRTWT7445-LV and SRTWT7446 stock and cost", two)
    assert asked.splitlines() == ["SRTWT7446 has 2 variants. Just SRTWT7446, or all 2?"]
    c.tools.calls.clear()
    c.say("all", pick(2))
    wanted = {c.uuid_of[code] for code in ("SRTWT7445-LV", "SRTWT7446", "SRTWT7446-A")}
    for tool in ("crm_inventory_stock_balance_list", "crm_procurement_po_last_cost_list"):
        asked_ids = {i for args in c.tools.args(tool) for i in _ids(args.get("product_ids"))}
        assert asked_ids == wanted, tool
