"""MULTI-ASK-PLAN2, owner ruling 12 Oct 2026: in a 2+ ask turn an EXACT existing variant code
(ZZTVS7445-LV) skips the family scope question and answers that code; only a base / family
code (ZZTVS7445, with several variants) gets "only this one or all N?".

The catalogue has the hand-test shape: ZZTVS7445 is an exact row with eight variants.
"""
from __future__ import annotations

import pytest

from tests.chatbot import _r9_engine_console as r9
from tests.chatbot._turn_helpers import entity, verdict
from tests.chatbot.test_engine import stub_access  # noqa: F401 - a pytest fixture
from tests.chatbot.test_multi_ask_runner import _console, _ids, sub

FAMILY = ["ZZTVS7445"] + [f"ZZTVS7445-{s}" for s in ("BK", "BN", "CP", "GM", "LV", "SS", "WH", "NEW")]


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
    lines = c.say("ZZTVS7445-LV stock and last cost", _two_asks("ZZTVS7445-LV")).splitlines()
    assert lines[0] == "ZZTVS7445-LV"
    assert "Stock: 120" in lines
    assert any(ln.startswith("Last cost: ") for ln in lines)
    assert (c.stored_question or {}).get("kind") != "sub_ask_scope"
    lv = c.uuid_of["ZZTVS7445-LV"]
    assert c.tools.calls
    for name, args in c.tools.calls:
        assert _ids(args.get("product_ids")) in ([], [lv]), name


def test_a_base_code_with_variants_still_asks_only_this_one_or_all(session_factory, monkeypatch, stub_access):
    c = _console(session_factory, monkeypatch, stub_access, "+60000009932")
    text = c.say("ZZTVS7445 stock and last cost", _two_asks("ZZTVS7445"))
    assert text.splitlines() == ["ZZTVS7445 has 9 variants. Just ZZTVS7445, or all 9?"]
    assert (c.stored_question or {}).get("kind") == "sub_ask_scope"
    assert not c.tools.calls


def test_all_about_the_head_never_widens_a_variant_typed_beside_it(session_factory, monkeypatch, stub_access):
    """Review B1 (a4a02904): "all" answers the question about ZZTVS7446 only; ZZTVS7445-LV,
    never asked about, stays the exact code."""
    from tests.chatbot.test_multi_ask_runner import pick

    monkeypatch.setattr(r9, "OTHER_CODES", ["ELP3754", "ZZTVS7446", "ZZTVS7446-A"])
    c = _console(session_factory, monkeypatch, stub_access, "+60000009933")
    two = verdict(
        domain_hint="inventory",
        intent_hint="check_stock",
        entities=[entity("ZZTVS7445-LV", hint="product"), entity("ZZTVS7446", hint="product")],
        sub_asks=[sub("stock", "stock"), sub("last_cost", "last cost")],
        family=None,
    )
    asked = c.say("ZZTVS7445-LV and ZZTVS7446 stock and cost", two)
    assert asked.splitlines() == ["ZZTVS7446 has 2 variants. Just ZZTVS7446, or all 2?"]
    c.tools.calls.clear()
    c.say("all", pick(2))
    wanted = {c.uuid_of[code] for code in ("ZZTVS7445-LV", "ZZTVS7446", "ZZTVS7446-A")}
    for tool in ("crm_inventory_stock_balance_list", "crm_procurement_po_last_cost_list"):
        asked_ids = {i for args in c.tools.args(tool) for i in _ids(args.get("product_ids"))}
        assert asked_ids == wanted, tool
