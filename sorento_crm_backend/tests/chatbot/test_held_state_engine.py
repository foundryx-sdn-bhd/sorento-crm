"""STUCK-QTY-LOOP: the owner's live sequence (4 Oct 2026, 02:56-03:00), through the real
engine (`engine.run_turn`, `tests/chatbot/_r9_engine_console.py`: parser verdict and the
tool reads stubbed, everything else real).

An availability contact with an open "How many units for each?" question asked for a low
stock report, was switched to compact, and sent "clear": every reply was the same stored
quantity question. Each test below is one leg of that sequence.
"""
from __future__ import annotations

import json

import pytest

from app.services.chatbot import turn_runtime

from tests.chatbot._r9_engine_console import EngineConsole, product, reply, stock
from tests.chatbot._turn_helpers import entity, verdict
from tests.chatbot.test_engine import stub_access  # noqa: F401 - a pytest fixture

pytestmark = pytest.mark.usefixtures("stub_access")

ASK = "How many units"
A, B = "SRTWC286-SH-150", "SRTWC286-SH-200"


@pytest.fixture
def console(session_factory, monkeypatch, stub_access):
    return EngineConsole(session_factory, monkeypatch, stub_access, phone="+60000009471")


def _open_the_question(c: EngineConsole) -> None:
    text = c.say(f"check stock {A} {B}", stock(product(A), product(B)))
    assert text.startswith("How many units for each?"), text


def _low_stock_ask():
    return verdict(
        intent_hint="low_stock_report",
        domain_hint="inventory",
        entities=[entity("Sorento", hint="brand"), entity("water tap", hint="category")],
    )


def test_a_low_stock_report_is_never_answered_with_the_quantity_question(console):
    _open_the_question(console)
    first = console.say("low stock report for sorento water tap", _low_stock_ask())
    again = console.say("low stock report for sorento water tap", _low_stock_ask())
    assert ASK not in first, first
    assert ASK not in again, again


def test_the_switch_to_compact_drops_the_question_built_under_availability(console, monkeypatch):
    _open_the_question(console)
    monkeypatch.setattr(turn_runtime, "_stock_availability_only", lambda *a, **k: False)
    console.stock_on_hand = 40
    text = console.say("stock?", reply())
    assert ASK not in text, text


def test_clear_as_a_reset_ends_the_question(console):
    _open_the_question(console)
    text = console.say("clear", verdict(topic_reset=True))
    assert ASK not in text, text
    again = console.say("low stock report for sorento water tap", _low_stock_ask())
    assert ASK not in again, again


def test_clear_the_parser_did_not_flag_never_replays_the_stored_question(console):
    """"clear" with no `topic_reset` and no intent reads, by the parser's own fields, as
    "stock?" over the carried products. The engine no longer replays the STORED question
    (the resume needs a stock intent); it re-reads stock under the contact's current
    access, which is the ordinary carry. Ending the conversation on "clear" is the
    parser's `topic_reset` (`test_clear_as_a_reset_ends_the_question`)."""
    _open_the_question(console)
    calls = len(console.tool_calls)
    console.say("clear", verdict(domain_hint="inventory"))
    assert "task_resumed_stock_qty" not in console.last_trace.rules_fired
    assert [name for name, _args in console.tool_calls[calls:]] == ["crm_inventory_stock_balance_list"]


def test_a_genuine_quantity_still_answers_the_question(console):
    _open_the_question(console)
    console.say("10", reply(demand_qty=10))
    calls = [args for name, args in console.tool_calls if name == "crm_inventory_stock_balance_list"]
    assert len(calls) == 2
    sent = calls[-1].get("requested_quantities")
    sent = json.loads(sent) if isinstance(sent, str) else sent
    assert sorted(sent.values()) == [10, 10]


def test_a_canned_turn_after_a_business_turn_writes_its_session(console):
    """Reviewer B1: the focus keys this lane adds must pass the tail's `SessionVars`
    check, or every casual / escalation / clarify turn after a business turn fails."""
    _open_the_question(console)
    console.say("thanks", verdict(message_type="casual", intent_hint=None))
    assert (console.state or {}).get("focus", {}).get("intent") == "check_stock"


def test_the_engine_tells_the_held_rule_which_reader_answered(console, monkeypatch, stub_access):
    """AC-6 at the engine seam: the turn that answers the low stock category question
    hands `turn_held.consume` that answer, so it can never be dropped as a new question
    whatever intent the parser put on the reply."""
    from app.services.chatbot import engine as engine_mod

    stub_access(attributes=["scm.low_stock_report"])
    asked = console.say("low stock report", verdict(intent_hint="low_stock_report", domain_hint="inventory"))
    assert "Which product category?" in asked, asked
    seen: list = []
    real = engine_mod.turn_held.consume

    def spy(state, verdict_in, **kw):
        seen.append(list(kw.get("answered") or []))
        return real(state, verdict_in, **kw)

    monkeypatch.setattr(engine_mod.turn_held, "consume", spy)
    console.say("water tap", verdict(intent_hint="check_stock", domain_hint="inventory", entities=[entity("water tap", hint="category")]))
    assert seen and "required_ask" in seen[-1], seen


def test_a_stock_mode_change_alone_drops_the_held_question(console, monkeypatch):
    """AC-2, the mode itself (reviewer round 2, N2): detailed and compact are both "not
    availability", so the switch is read off the resolved stock mode, not the bool."""
    from types import SimpleNamespace

    from app.services import stock_visibility

    monkeypatch.setattr(stock_visibility, "resolve_policy", lambda *a, **k: SimpleNamespace(mode="availability"))
    _open_the_question(console)
    monkeypatch.setattr(stock_visibility, "resolve_policy", lambda *a, **k: SimpleNamespace(mode="detailed"))
    calls = len(console.tool_calls)
    text = console.say("stock?", reply())
    assert ASK not in text or len(console.tool_calls) > calls, text
    assert "task_resumed_stock_qty" not in console.last_trace.rules_fired


def test_a_failed_access_read_neither_drops_nor_restamps(console, monkeypatch):
    from app.services import stock_visibility

    _open_the_question(console)
    stamp = console.state["focus"]["held_access"]

    def broken(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(stock_visibility, "resolve_policy", broken)
    console.say("10", reply(demand_qty=10))
    assert console.state["focus"].get("held_access") in (None, stamp)
