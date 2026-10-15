"""Phase 2 RED tests - chat fix PR-B S7 (AC-B9): the sales analysis company question
("Sorento or Mocha?", route `status: clarify`) is an OPEN QUESTION whose options are the company
names, so the next turn naming one answers it and re-runs the held analysis for that company.

Pinned (tester's choice, flagged): the armed question lives at the session's `open_question`
(`options` carry the labels), the same place the sales report drill offer arms. The answering
parse picks a position (`reference_positions` and the declared `open_question_answer` pick);
only parser fields are read.
"""
from __future__ import annotations

from tests.chatbot.test_outstanding_lane import _qf, _run_turn, _seed_contact, _session_of
from tests.chatbot.test_sales_analysis_lane import GRANT, ROUTE_HIT, TOOL, _rendered


def _first_turn(session_factory, monkeypatch, tag: str):
    _seed_contact(session_factory, variables={})
    result, captured = _run_turn(
        session_factory, monkeypatch,
        qf=_qf(order_status="sales_analysis", entities=[], date_filter_start="2026-01-01", date_filter_end="2026-10-15"),
        text_body="sales this year", msg_id=f"ZZT-b9-{tag}-1", attributes=[GRANT],
        mcp_response=_rendered({"status": "clarify", "message": "Sorento or Mocha?", "options": ["Sorento", "Mocha"]}),
    )
    assert "Sorento or Mocha?" in ((result.reply or {}).get("text") or "")
    return captured


def _pick(position: int):
    return _qf(
        order_status=None, entities=[], domain_hint=None, intent_hint=None,
        reference_positions=[position],
        open_question_answer={"mode": "pick", "picked": [position], "items": [], "qty_for_all": None},
    )


def test_b9_the_company_question_is_registered_as_an_open_question_with_company_options(session_factory, monkeypatch):
    _first_turn(session_factory, monkeypatch, "open")
    question = _session_of(session_factory).get("open_question") or {}
    assert question, "no open question armed after 'Sorento or Mocha?'"
    labels = [str(o.get("label") or o.get("name") or o) if isinstance(o, dict) else str(o) for o in question.get("options") or []]
    assert labels == ["Sorento", "Mocha"], question


def test_b9_picking_position_one_reruns_the_held_analysis_for_that_company(session_factory, monkeypatch):
    _first_turn(session_factory, monkeypatch, "p1")
    _result, captured = _run_turn(
        session_factory, monkeypatch, qf=_pick(1), text_body="sorento", msg_id="ZZT-b9-p1-2",
        attributes=[GRANT], mcp_response=_rendered(),
    )
    calls = [a for n, a in captured if n == TOOL]
    assert len(calls) == 1, captured
    assert str(calls[0].get("company")).lower() == "sorento", calls
    assert calls[0]["date_from"] == "2026-01-01" and calls[0]["date_to"] == "2026-10-15", calls


def test_b9_picking_position_two_means_the_second_company(session_factory, monkeypatch):
    _first_turn(session_factory, monkeypatch, "p2")
    _result, captured = _run_turn(
        session_factory, monkeypatch, qf=_pick(2), text_body="mocha", msg_id="ZZT-b9-p2-2",
        attributes=[GRANT], mcp_response=_rendered(),
    )
    calls = [a for n, a in captured if n == TOOL]
    assert len(calls) == 1 and str(calls[0].get("company")).lower() == "mocha", captured


def test_b9_near_miss_a_new_ask_naming_a_product_is_a_new_ask_not_the_answer(session_factory, monkeypatch):
    from tests.chatbot.test_outstanding_lane import PRODUCT_CODE, PRODUCT_UUID

    _first_turn(session_factory, monkeypatch, "new")
    _result, captured = _run_turn(
        session_factory, monkeypatch,
        qf=_qf(order_status="sales_report", entities=[{"raw": PRODUCT_CODE, "hint": "product", "canonical_code": None,
                                                       "current_message": True, "confident": True}]),
        text_body=f"sales report for {PRODUCT_CODE}", msg_id="ZZT-b9-new-2", attributes=[GRANT],
        matches={PRODUCT_CODE: {"uuid": PRODUCT_UUID, "entity_type": "product", "canonical_code": PRODUCT_CODE}},
        mcp_response=_rendered(),
    )
    assert not [a for n, a in captured if n == TOOL and a.get("company")], captured


def test_b9_a_reply_picking_both_companies_reasks_and_never_falls_to_the_roster(session_factory, monkeypatch):
    """Review: 'both' is not a single pick. The company question is asked again (or the reply says
    to pick one); never the generic roster path (crm_sales_report + 'Couldn't find Sorento ...')."""
    _first_turn(session_factory, monkeypatch, "both")
    qf = _qf(
        order_status=None, entities=[], domain_hint=None, intent_hint=None, reference_positions=[1, 2],
        open_question_answer={"mode": "all", "picked": [1, 2], "items": [], "qty_for_all": None},
    )
    result, captured = _run_turn(
        session_factory, monkeypatch, qf=qf, text_body="both", msg_id="ZZT-b9-both-2",
        attributes=[GRANT], mcp_response=_rendered(),
    )
    text = (result.reply or {}).get("text") or ""
    assert not [n for n, _a in captured if n == "crm_sales_report"], captured
    assert "escalate" not in text.lower() and "couldn't find" not in text.lower() and "could not find" not in text.lower(), text
    assert "Sorento" in text and "Mocha" in text, text
