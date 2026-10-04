"""MULTI-CODE-DYM through `engine.run_turn`, real resolver and real trigram neighbours
(`_r9_engine_console`: the owner's SRTWC286 family seeded, "stwc2867" misses and the
resolver offers SRTWC286-SH / SRTWC286-SH-P). Owner rulings 4 Oct 2026: a missing code
beside a found one gets its own did-you-mean (Q1-Q2), and a pick answers what it picks,
several at once when the reply names several (Q3).
"""
from __future__ import annotations

from typing import Any

from app.services.chatbot import turn_runtime

from tests.chatbot._r9_engine_console import EngineConsole, answer, product, stock
from tests.chatbot._turn_helpers import verdict
from tests.chatbot.test_engine import stub_access  # noqa: F401 - a pytest fixture

STOCK = "crm_inventory_stock_balance_list"


def _console(session_factory, monkeypatch, stub_access, phone: str) -> EngineConsole:
    c = EngineConsole(session_factory, monkeypatch, stub_access, phone=phone)
    monkeypatch.setattr(turn_runtime, "_stock_availability_only", lambda *a, **k: False)
    c.stock_on_hand = 12
    return c


def _codes(c: EngineConsole, since: int) -> list[tuple[str, list[str]]]:
    return [(name, [c.codes[p] for p in args.get("product_ids") or []]) for name, args in c.tool_calls[since:]]


def _pick(*positions: int) -> dict[str, Any]:
    return verdict(
        message_type="business_query",
        domain_hint="inventory",
        domain_in_message=False,
        reference_positions=list(positions),
        open_question_answer=answer("pick", picked=list(positions)),
    )


def _partial(c: EngineConsole) -> str:
    text = c.say("SRTWC287-S-150 stwc2867 stock", stock(product("SRTWC287-S-150"), product("stwc2867")))
    assert "SRTWC287-S-150" in text, text
    assert 'Couldn\'t find "stwc2867" (product). Did you mean:' in text, text
    # The stock presenter numbers its found block "1.", so the suggestions run on from 2
    # (Q1: no number printed twice).
    assert "1. *Product Code:* SRTWC287-S-150" in text, text
    assert "Did you mean:\n2. SRTWC286-SH" in text, text
    assert "I could not find stwc2867" not in text, text
    question = c.stored_question
    assert question is not None and question["kind"] == "product_pick", question
    return text


def test_partial_miss_lists_the_missing_codes_suggestions_and_one_pick_answers_it(
    session_factory, monkeypatch, stub_access
):
    c = _console(session_factory, monkeypatch, stub_access, "+60000014651")
    _partial(c)
    first = next(o for o in c.stored_question["options"] if o["position"] == 2)

    since = len(c.tool_calls)
    c.say("2", _pick(2))
    assert _codes(c, since) == [(STOCK, [first["code"]])], _codes(c, since)


def test_a_reply_picking_two_suggestions_answers_both(session_factory, monkeypatch, stub_access):
    """Q3 (owner): the parser decides what was picked; "2 and 3" answers both codes."""
    c = _console(session_factory, monkeypatch, stub_access, "+60000014652")
    _partial(c)
    options = {o["position"]: o["code"] for o in c.stored_question["options"]}
    assert len(options) >= 2, options

    since = len(c.tool_calls)
    c.say("2 and 3", _pick(2, 3))
    calls = _codes(c, since)
    assert calls and calls[0][0] == STOCK, calls
    assert sorted(calls[0][1]) == sorted([options[2], options[3]]), calls


def test_a_reply_picking_one_and_typing_another_code_answers_both(session_factory, monkeypatch, stub_access):
    """Q3 (owner): "2 and SRTWC286-SH-150" - a pick plus a code of its own, each answered."""
    c = _console(session_factory, monkeypatch, stub_access, "+60000014653")
    _partial(c)
    options = {o["position"]: o["code"] for o in c.stored_question["options"]}

    since = len(c.tool_calls)
    reading = _pick(2)
    reading["entities"] = [product("SRTWC286-SH-150")]
    c.say("2 and SRTWC286-SH-150", reading)
    fetched = [code for _name, codes in _codes(c, since) for code in codes]
    assert options[2] in fetched and "SRTWC286-SH-150" in fetched, _codes(c, since)


def test_a_typed_code_ending_in_punctuation_is_still_answered(session_factory, monkeypatch, stub_access):
    """Review S2: "2 and SRTWC286-SH-150?" - the question mark does not drop the code."""
    c = _console(session_factory, monkeypatch, stub_access, "+60000014654")
    _partial(c)
    options = {o["position"]: o["code"] for o in c.stored_question["options"]}

    since = len(c.tool_calls)
    reading = _pick(2)
    reading["entities"] = [product("SRTWC286-SH-150")]
    c.say("2 and SRTWC286-SH-150?", reading)
    fetched = [code for _name, codes in _codes(c, since) for code in codes]
    assert options[2] in fetched and "SRTWC286-SH-150" in fetched, _codes(c, since)


def test_a_pick_named_by_part_of_its_code_fetches_only_the_pick(session_factory, monkeypatch, stub_access):
    """Review S1: "the srtwc286 one" picks SRTWC286-SH; "srtwc286" is how it was named,
    not a second subject, so nothing else is fetched."""
    c = _console(session_factory, monkeypatch, stub_access, "+60000014655")
    _partial(c)
    options = {o["position"]: o["code"] for o in c.stored_question["options"]}

    since = len(c.tool_calls)
    reading = _pick(2)
    reading["entities"] = [product("srtwc286")]
    c.say("the srtwc286 one", reading)
    assert _codes(c, since) == [(STOCK, [options[2]])], _codes(c, since)
