"""CHAT-COMPOSER Sets slice red tests ("more"): the K/sets helpers, `turn_runtime.with_set_more`
(parser fields only), and the printed row numbers reaching `Focus.last_list` on page 2.
"""
from __future__ import annotations

import pytest

from app.main import app  # noqa: F401  (first app import resolves the guards cycle)
from tests.chatbot.test_composers_stock_xd import _set_gate

CARRY = {"set_key": {"shown": 50, "total": 182, "domain": "inventory"}}


# --------------------------------------------------------------------------- #
# K/sets helpers
# --------------------------------------------------------------------------- #


def test_more_line_and_next_count():
    from app.services.chatbot.composers import sets

    assert sets.more_line(50) == 'Reply "more" to see the next 50.'
    assert sets.more_line(1234) == 'Reply "more" to see the next 1,234.'
    assert sets.next_count(182, 0, 50, 50) == 50
    assert sets.next_count(182, 100, 50, 50) == 32
    assert sets.next_count(182, 150, 32, 50) == 0
    assert sets.next_count(182, 0, 200, 50) == 0


def test_page_size():
    from app.services.chatbot.composers import sets

    assert sets.page_size(None, 50) == 50
    assert sets.page_size(10, 50) == 10
    assert sets.page_size(80, 50) == 50


@pytest.mark.parametrize("named,expect", [(None, 50), (10, 10), (80, 50), (50, 50)])
def test_set_list_size_unchanged(monkeypatch, named, expect):
    """The fold: `_set_list_size` keeps its bytes in both gate states."""
    from app.services.chatbot.lanes.business import fetch

    for gate in (False, True):
        _set_gate(monkeypatch, gate)
        assert fetch._set_list_size({"top_n": named}) == expect


# --------------------------------------------------------------------------- #
# with_set_more
# --------------------------------------------------------------------------- #


def _verdict(**kw):
    base = {
        "continuation": True,
        "entities": [],
        "requested_attributes": [],
        "top_n": None,
        "reference_positions": [],
        "asks": [],
    }
    return {**base, **kw}


def test_with_set_more_pages(monkeypatch):
    from app.services.chatbot import turn_runtime

    _set_gate(monkeypatch, True)
    out = turn_runtime.with_set_more(_verdict(), carried=CARRY)
    assert out["top_n"] == 50
    assert out["set_continue"] is True
    assert out["reference_positions"] == []


def test_with_set_more_gate_off_identity(monkeypatch):
    from app.services.chatbot import turn_runtime

    _set_gate(monkeypatch, False)
    v = _verdict()
    assert turn_runtime.with_set_more(v, carried=CARRY) is v


@pytest.mark.parametrize(
    "overrides",
    [
        {"requested_attributes": ["eta"]},
        {"entities": [{"raw": "black", "hint": "colour", "current_message": True, "confident": True}]},
        {"entities": [{"raw": "SRTWC8608-RL", "hint": "product", "current_message": True, "confident": True}]},
        {"asks": [{"domain": "incoming"}]},
        {"sub_asks": [{"domain": "incoming"}]},
        {"continuation": False},
        {"continuation": None},
        {"top_n": 10},
        {"is_affirmative": True},
    ],
    ids=["attribute", "colour_entity", "product_named", "asks", "sub_asks", "not_continuation", "null_continuation", "named_count", "affirmative"],
)
def test_follow_up_is_not_more(monkeypatch, overrides):
    """TRAIN shapes: "got eta?", "Black also?", a bare code: continuation true is not enough."""
    from app.services.chatbot import turn_runtime

    _set_gate(monkeypatch, True)
    v = _verdict(**overrides)
    assert turn_runtime.with_set_more(v, carried=CARRY) == v


@pytest.mark.parametrize(
    "carried",
    [
        None,
        {},
        {"set_key": {"shown": 50, "total": 182, "offer_only": True}},
        {"set_key": {"shown": 0, "total": 182}},
    ],
    ids=["no_carry", "empty", "offer_only", "withheld"],
)
def test_more_without_a_pageable_carry_is_not_paged(monkeypatch, carried):
    """After a topic switch the carry is gone: "more" is parsed as any other message."""
    from app.services.chatbot import turn_runtime

    _set_gate(monkeypatch, True)
    v = _verdict()
    assert turn_runtime.with_set_more(v, carried=carried) == v


def test_a_reused_echo_entity_does_not_block_more(monkeypatch):
    """TR:1316-1335 drops a reused echo of the carried set; "more" does the same."""
    from app.services.chatbot import turn_runtime

    _set_gate(monkeypatch, True)
    v = _verdict(
        entities=[{"raw": "p trap", "hint": "product_type", "current_message": True, "confident": True}],
        entity_op="reuse",
    )
    out = turn_runtime.with_set_more(v, carried=CARRY)
    assert out["set_continue"] is True
    assert not [e for e in out["entities"] if e.get("current_message") is True]


# --------------------------------------------------------------------------- #
# Focus.last_list follows the printed numbers (D5, M5)
# --------------------------------------------------------------------------- #


def _figures(n):
    return [
        {"fields": [{"label": "Product Code", "value": f"SRTWC{i:03d}"}, {"label": "Description", "value": "d"}]}
        for i in range(n)
    ]


def test_last_list_follows_printed_numbers_on_page_two(monkeypatch):
    from app.services.chatbot.turn.compose import Section
    from app.services.chatbot.turn.tail import last_list_after

    _set_gate(monkeypatch, True)
    section = Section(domain="inventory", figures=_figures(50), first=51)
    rows = last_list_after(None, question=None, sections=[section])
    assert [r["position"] for r in rows] == list(range(51, 101))
    assert rows[0]["code"] == "SRTWC000"


def test_last_list_numbers_from_one_by_default(monkeypatch):
    from app.services.chatbot.turn.compose import Section
    from app.services.chatbot.turn.tail import last_list_after

    for gate in (False, True):
        _set_gate(monkeypatch, gate)
        rows = last_list_after(None, question=None, sections=[Section(domain="inventory", figures=_figures(3))])
        assert [r["position"] for r in rows] == [1, 2, 3]


def _envelope(window):
    env = {
        "domain": "inventory",
        "denied": False,
        "entities": [],
        "product_codes": [],
        "figures": _figures(50),
        "files": [],
        "miss": [],
        "has_result": True,
        "tool_has_result": True,
        "unresolved": [],
        "error": None,
        "lane_text": "Here's what you want: P trap water closets (182, showing 51 to 100)",
    }
    if window:
        env["set_window"] = {"first": 51, "last": 100, "total": 182, "next": 50}
    return env


def _compose(env):
    from app.services.chatbot.turn.compose import compose
    from app.services.chatbot.turn.policy import Policy
    from app.services.chatbot.turn.state import Focus, Profile, State
    from tests.chatbot._turn_helpers import TIER_ORDER_FIXTURE, _domain_row

    row = {**_domain_row("inventory", narrowing={"product": "list_all"}), "label": "stock"}
    policy = Policy.from_rows(domains=[row], kinds=[], tier_order=TIER_ORDER_FIXTURE)
    return compose([env], State(focus=Focus(), pending=None, profile=Profile(), turn_no=2), policy, ctx=None)


def test_compose_passes_the_window_start_to_the_section(monkeypatch):
    _set_gate(monkeypatch, True)
    [section] = _compose(_envelope(True)).sections
    assert section.first == 51


def test_compose_gate_off_section_numbers_from_one(monkeypatch):
    _set_gate(monkeypatch, False)
    [section] = _compose(_envelope(True)).sections
    assert getattr(section, "first", 1) == 1
