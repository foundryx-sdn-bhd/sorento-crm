"""INTENT-FRAMES: the owner's real sequences (3-4 Oct 2026, masked) through the real engine.

Each test is one sequence from the behaviour card
(`documentation/plans/chatbot/intent-frames-behaviour-card.md`), replayed with the parser
verdict stubbed to the reading the live parser gives and the tool reads stubbed; the
engine, the resolver seam, the lanes and the presenters are real.

The contract under every replay: a lane reads entities only from its own frame. The
entities handed to the resolver (`turn_runtime.lane_parse_output`) and every tool call
never carry a word of another intent's frame.
"""
from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from app.services.chatbot import turn_runtime

from tests.chatbot._r9_engine_console import EngineConsole, product, reply, stock
from tests.chatbot._turn_helpers import entity, verdict
from tests.chatbot.test_engine import stub_access  # noqa: F401 - a pytest fixture
from tests.chatbot.test_low_stock_filter_ask import (  # noqa: F401 - pytest fixtures
    GRANT,
    SUPPLIER_KEY,
    _ask as _low_stock_ask,
    _console as _low_stock_console,
    _filter_line,
    _ls,
    _refine,
    _stock_ask,
)

ASK = "How many units"
A, B = "SRTWC286-SH-150", "SRTWC286-SH-200"
#: The masked sales agent of crew report 2.
AGENT = "WILLMASK"


def _spy_resolver_input(monkeypatch) -> list[list[str]]:
    """Every entity raw the engine handed the resolver, one list per call."""
    seen: list[list[str]] = []
    real = turn_runtime.lane_parse_output

    def spy(*args: Any, **kwargs: Any):
        out = real(*args, **kwargs)
        seen.append([str(e.get("raw") or "") for e in out.get("entities") or [] if isinstance(e, dict)])
        return out

    monkeypatch.setattr(turn_runtime, "lane_parse_output", spy)
    return seen


def _flat(seen: list[list[str]]) -> str:
    return " ".join(raw for call in seen for raw in call).casefold()


# --------------------------------------------------------------------------- #
# 1. Availability stock check -> low stock report -> compact -> "stock?"        #
# --------------------------------------------------------------------------- #


@pytest.fixture
def dealer(session_factory, monkeypatch, stub_access):
    return EngineConsole(session_factory, monkeypatch, stub_access, phone="+60000014731")


def _low_stock_verdict():
    return verdict(
        intent_hint="low_stock_report",
        domain_hint="inventory",
        domain_in_message=True,
        entity_op="replace_combine",
        entities=[],
        low_stock=_ls(categories=["water tap"], brands=["sorento"]),
    )


def test_the_stock_check_products_never_reach_the_low_stock_report(dealer, monkeypatch, stub_access):
    """Sequence 1: the 2 products of the availability stock check are the stock check's
    frame. The low stock ask is a new intent with no carry signal: no product reaches the
    resolver or the report, and the quantity question is never replayed."""
    stub_access(attributes=["scm.low_stock_report"])
    text = dealer.say(f"check stock {A} {B}", stock(product(A), product(B)))
    assert text.startswith("How many units for each?"), text
    seen = _spy_resolver_input(monkeypatch)
    since = len(dealer.tool_calls)
    text = dealer.say("low stock report for sorento water tap", _low_stock_verdict())
    assert ASK not in text, text
    assert A.casefold() not in _flat(seen) and B.casefold() not in _flat(seen), seen
    for _name, args in dealer.tool_calls[since:]:
        assert not args.get("product_ids"), args
    assert "frame_new_intent" in dealer.last_trace.rules_fired
    assert (dealer.state or {}).get("focus", {}).get("products") == []


def test_after_the_switch_to_compact_stock_asks_for_its_own_subject(dealer, monkeypatch, stub_access):
    """Sequence 1, last leg: "stock?" after the low stock report, the contact switched to
    compact. The access change drops the frame; "stock?" names nothing, so nothing is
    replayed and no old product is looked up."""
    stub_access(attributes=["scm.low_stock_report"])
    dealer.say(f"check stock {A} {B}", stock(product(A), product(B)))
    dealer.say("low stock report for sorento water tap", _low_stock_verdict())
    monkeypatch.setattr(turn_runtime, "_stock_availability_only", lambda *a, **k: False)
    dealer.stock_on_hand = 40
    seen = _spy_resolver_input(monkeypatch)
    since = len(dealer.tool_calls)
    text = dealer.say("stock?", reply(entity_op="replace_combine"))
    assert ASK not in text, text
    assert A.casefold() not in _flat(seen), seen
    for _name, args in dealer.tool_calls[since:]:
        assert not args.get("product_ids"), args


def test_an_access_change_alone_drops_the_frame(dealer, monkeypatch):
    """Owner ruling Q1: no clock; the access change is what ends the frame."""
    dealer.say(f"check stock {A} {B}", stock(product(A), product(B)))
    assert (dealer.state or {}).get("focus", {}).get("frame_access")
    monkeypatch.setattr(turn_runtime, "_stock_availability_only", lambda *a, **k: False)
    dealer.stock_on_hand = 40
    dealer.say("thanks", verdict(message_type="casual", intent_hint=None))
    focus = (dealer.state or {}).get("focus", {})
    assert focus.get("products") == [] and not focus.get("tasks"), focus


def test_a_frame_lives_through_many_turns_under_the_same_access(dealer):
    """No turn clock: the stock question is still the stock question ten casual turns on."""
    dealer.say(f"check stock {A} {B}", stock(product(A), product(B)))
    for _ in range(8):
        dealer.say("ok", verdict(message_type="casual", intent_hint=None))
    focus = (dealer.state or {}).get("focus", {})
    assert [p.get("raw") for p in focus.get("products") or []] == [A, B], focus
    assert focus.get("tasks"), focus


# --------------------------------------------------------------------------- #
# 2. A ranking by sales agent -> "taiyang only" read as a stock ask              #
# --------------------------------------------------------------------------- #


@pytest.fixture
def report(session_factory, monkeypatch):
    return _low_stock_console(session_factory, monkeypatch, grants=[GRANT, SUPPLIER_KEY])


def _ranking_frame() -> dict[str, Any]:
    """The frame a top selling ranking by agent WILLMASK left (crew report 2), as stored."""
    agent = {"raw": AGENT, "hint": "sales_agent", "canonical_code": AGENT, "current_message": False}
    return {
        "focus": {
            "products": [], "customers": [], "warehouse": [], "brands": [], "outstanding_brand_ids": [],
            "tier": [], "domains": ["order"], "document": [], "status": "top_selling",
            "sales_channel": None, "date_window": {"start": "2026-09-01", "end": "2026-09-30"},
            "sort": None, "extra": {"sales_agent": [agent]}, "intent": "check_order",
            "top_selling": None, "tasks": [],
        },
        "open_question": None,
    }


def test_the_old_rankings_agent_never_reaches_the_stock_ask(report, monkeypatch):
    """Crew report 2: "taiyang only" after a ranking by WILLMASK answered "Could not find
    inventory for WILLMASK". The agent is the ranking's filter: a new intent drops it, so
    it is never resolved, never fetched and never said."""
    report.session_vars = copy.deepcopy(_ranking_frame())
    seen = _spy_resolver_input(monkeypatch)
    v = _stock_ask()
    v.update(entities=[{"raw": "taiyang", "hint": "brand", "canonical_code": None, "current_message": True, "confident": True}],
             domain_in_message=False, entity_op="replace_combine")
    text, _calls = report.say(v, "taiyang only")
    assert AGENT.casefold() not in text.casefold(), text
    assert AGENT.casefold() not in _flat(seen), seen
    focus = report.session_vars.get("focus") or {}
    assert not (focus.get("extra") or {}).get("sales_agent"), focus
    assert focus.get("status") != "top_selling" and focus.get("date_window") is None, focus


# --------------------------------------------------------------------------- #
# 3. Low stock report -> "taiyang only": the same intent narrows its frame       #
# --------------------------------------------------------------------------- #


def test_a_low_stock_refinement_narrows_the_report_just_shown(report):
    text, calls = report.say(_low_stock_ask(low_stock=_ls(categories=["water tap"])), "low stock report water tap")
    assert _filter_line(text).startswith("water tap"), text
    text, calls = report.say(_refine(suppliers=["taiyang"]), "taiyang only")
    (args,) = calls
    assert sorted(args.get("categories") or []) == ["CB-FT", "SRT-FT"], args
    assert args.get("suppliers"), args
    assert "taiyang" in _filter_line(text).casefold(), text
    assert "Could not find" not in text, text


def test_a_stock_ask_between_never_leaks_into_the_low_stock_report(report, monkeypatch):
    """The stock ask's CB100 and BRW are its own frame: the low stock report after it is
    run with only the report's own filters."""
    report.say(_stock_ask(), "stock CB100 BRW")
    seen = _spy_resolver_input(monkeypatch)
    text, calls = report.say(_low_stock_ask(low_stock=_ls(categories=["water tap"])), "low stock report water tap")
    (args,) = calls
    assert not args.get("product_ids") and not args.get("warehouse_ids"), args
    assert "cb100" not in _flat(seen) and "brw" not in _flat(seen), seen
    assert " · " not in text


# --------------------------------------------------------------------------- #
# 4. incoming -> "4" -> "check stock" -> "7"                                    #
# --------------------------------------------------------------------------- #


def test_check_stock_carries_the_picked_product_and_7_still_picks(session_factory, monkeypatch, stub_access):
    """#1352 under frames: "check stock" is a new intent the parser reads as the same
    subject (`entity_op: reuse`, `continuation: true`), so the picked product carries;
    the incoming roster survives (pick-only) and "7" picks off it, as incoming."""
    from tests.chatbot.test_picker_domain_judgement_1352 import (
        INCOMING,
        STOCK,
        _answered_positions,
        _bare_pick,
        _check_stock,
        _console,
        _picked_roster,
        _say,
    )

    c = _console(session_factory, monkeypatch, stub_access, "+60000014734")
    _picked_roster(c)
    text, calls = _say(c, "check stock", _check_stock())
    assert calls == [(STOCK, ["SRTWC286-SH-NEW"])], calls
    assert "frame_carries_subject" in c.last_trace.rules_fired
    text, calls = _say(c, "7", _bare_pick(7, carried_domain="inventory"))
    assert calls and calls[0] == (INCOMING, ["SRTWC286-SH-NEW-P"]), calls
    assert _answered_positions(c) == [4, 7]


def test_check_stock_without_a_carry_signal_never_carries_the_old_product(session_factory, monkeypatch, stub_access):
    """The same sequence, the parser reading "check stock" as a fresh ask with no carry
    signal: no old product is looked up (owner rule: only explicitly carried things)."""
    from tests.chatbot.test_picker_domain_judgement_1352 import STOCK, _console, _picked_roster, _say

    c = _console(session_factory, monkeypatch, stub_access, "+60000014735")
    _picked_roster(c)
    fresh = verdict(domain_hint="inventory", intent_hint="check_stock", domain_in_message=True, entity_op="replace_combine")
    _text, calls = _say(c, "check stock", fresh)
    assert not [codes for name, codes in calls if name == STOCK and codes], calls


# --------------------------------------------------------------------------- #
# 5. Picker -> "1" -> empty -> escalation offer                                 #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("stale_intent", [None, "check_stock"], ids=["bare", "stale_other_intent"])
def test_a_picker_answer_that_finds_nothing_ends_with_the_escalation_offer(session_factory, monkeypatch, stale_intent):
    """PICKER-ESCALATION (#1467) under frames: "1" answers the customer picker, so it is
    inside the picker's frame even when the parser carries a stale other intent; the empty
    order fetch ends with the escalation offer for an escalation-allowed contact."""
    from tests.chatbot.test_picker_escalation_offer import ESCALATE_WORDS, _chat, _yoo_ask
    from tests.chatbot.test_samantha_27sep_r5_do_list_carry import ORDERS_LIST, _pick

    chat, app = _chat(session_factory, monkeypatch, escalation_allowed=True)
    try:
        # A period on the ask, so the DO list runs rather than asking "Which period?"
        # (`do_ask.range_reply`; the undated shape is date-dependent on main too).
        ask = _yoo_ask()
        ask.update(date_filter_start="2026-09-01", date_filter_end="2026-09-30")
        picker, _calls, _ = chat.turn("delivery for yoo living in september", ask)
        assert "YOO LIVING HOUSE" in picker, picker
        one = _pick(1)
        if stale_intent:
            one.update(intent_hint=stale_intent, domain_hint="inventory")
        reply_text, calls, _ = chat.turn("1", one)
    finally:
        app.dependency_overrides.clear()
    assert [a for n, a in calls if n == ORDERS_LIST], calls
    assert any(w in reply_text.casefold() for w in ESCALATE_WORDS), reply_text
    assert " · " not in reply_text
