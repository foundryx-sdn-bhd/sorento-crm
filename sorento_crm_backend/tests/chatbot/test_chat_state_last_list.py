"""CHAT-STATE S2 (red): the last list shown, `tail.last_list_after` and `Focus.last_list`.

Traces to UAC A2, A3, C8 and plan test list S2. A list item is
`{"position": int, "label": str, "code": str}` (plus an optional key); `last_list_after`
imports are inside the test bodies so each test fails on its own today.
"""
from __future__ import annotations

from uuid import uuid4

from app.services.chatbot import contracts
from app.services.chatbot.turn.compose import Section
from app.services.chatbot.turn.pending import ask
from app.services.chatbot.turn.state import Focus, focus_from_wire, focus_to_wire


def _row(code: str, stock: str = "4"):
    return {"fields": [{"label": "Product Code", "value": code}, {"label": "Stock", "value": stock}]}


def _last_list_after(previous, *, question=None, sections=None):
    from app.services.chatbot.turn.tail import last_list_after

    return last_list_after(previous, question=question, sections=sections if sections is not None else [])


def test_one_data_section_gives_positions_in_figure_order():
    codes = ["SRTWC286", "SRT5733", "MWB8051"]
    section = Section(domain="inventory", figures=[_row(c) for c in codes])
    result = _last_list_after(None, sections=[section])
    assert [item["position"] for item in result] == [1, 2, 3]
    assert [item["code"] for item in result] == codes
    assert all(item.get("label") for item in result)


def test_question_options_are_kept_with_their_positions_and_codes():
    question = ask(
        "customer_pick",
        [
            {"position": 1, "label": "Customer X", "code": "C0001", "name": "Customer X", "entity_type": "customer", "payload": {"uuid": str(uuid4())}},
            {"position": 2, "label": "Customer Y", "code": "C0002", "name": "Customer Y", "entity_type": "customer", "payload": {"uuid": str(uuid4())}},
        ],
        asked_at_turn=2,
    )
    result = _last_list_after(None, question=question)
    assert [item["position"] for item in result] == [1, 2]
    assert [item["code"] for item in result] == ["C0001", "C0002"]
    assert [item["label"] for item in result] == ["Customer X", "Customer Y"]


def test_no_list_shown_keeps_previous_and_a_new_list_replaces_it():
    previous = [{"position": 1, "label": "SRT5733", "code": "SRT5733"}, {"position": 2, "label": "MWB8051", "code": "MWB8051"}]
    empty = Section(domain="inventory", figures=[])
    assert _last_list_after(previous, sections=[empty]) == previous
    assert _last_list_after(previous, sections=[]) == previous
    assert _last_list_after(None, sections=[]) is None

    fresh = Section(domain="inventory", figures=[_row("SRTWC286")])
    result = _last_list_after(previous, sections=[fresh])
    assert [item["code"] for item in result] == ["SRTWC286"]


def test_two_sections_with_rows_give_none():
    one = Section(domain="inventory", figures=[_row("SRT5733")])
    two = Section(domain="incoming", figures=[_row("MWB8051")])
    assert _last_list_after(None, sections=[one, two]) is None


def test_stored_focus_without_the_key_reads_none_and_session_vars_accepts_it():
    assert focus_from_wire({"products": []}).last_list is None
    assert Focus().last_list is None
    assert contracts.SessionVars.model_validate({"focus": {"products": []}}).focus.last_list is None

    rows = [{"position": 1, "label": "SRT5733", "code": "SRT5733"}]
    parsed = contracts.SessionVars.model_validate({"focus": {"last_list": rows}})
    assert parsed.focus.last_list == rows


def test_round_trip_through_the_wire_keeps_the_list():
    rows = [
        {"position": 1, "label": "SRTWC286", "code": "SRTWC286"},
        {"position": 2, "label": "Customer X", "code": "C0001"},
    ]
    wire = focus_to_wire(Focus(last_list=rows))
    assert wire["last_list"] == rows
    assert focus_from_wire(wire).last_list == rows
    assert contracts.SessionVars.model_validate({"focus": wire}).focus.last_list == rows
