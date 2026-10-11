"""Phase 2 RED tests - REPORTS-STEP2 gate polish (11 Oct 2026).

(a) a ranking waiting on its period keeps the new values a restated ask carries and does not repeat
    the period question word for word, and the re-ask is bounded;
(b) a location word the parser typed as a warehouse is not reported as a product miss.

Driven through parser output fields only, never message text.
"""
from __future__ import annotations

from typing import Any

import pytest

from tests.chatbot import test_report_ask_lane as lane
from tests.chatbot.test_report_ask_lane import PERIOD_Q, SEP, _e, _rank, console  # noqa: F401
from tests.chatbot.test_reports_step2_lane import _s2_harness  # noqa: F401  (autouse fixture by name)

NO_PERIOD = {"date_filter_start": None, "date_filter_end": None}


# ================================================================================ (a)


def _restated(console, **overrides: Any) -> tuple[str, list[dict[str, Any]]]:
    return console.say(_rank(_e("Sorento", "brand"), **{**NO_PERIOD, **overrides}), "make it 20 for sorento")


def test_a_a_restated_ask_without_a_period_is_not_answered_word_for_word(console) -> None:
    text, calls = console.say(_rank(_e("Sorento", "brand"), top_n=10, **NO_PERIOD), "top 10 salesman for sorento")
    assert text.strip() == PERIOD_Q and calls == [], (text, calls)
    again, calls = _restated(console, top_n=20)
    assert calls == [], calls
    assert again.strip() != text.strip(), again
    assert "period" in again.lower(), again


def test_a_b_the_new_value_is_kept_when_the_period_finally_comes(console) -> None:
    console.say(_rank(_e("Sorento", "brand"), top_n=10, **NO_PERIOD), "top 10 salesman for sorento")
    _restated(console, top_n=20)
    _text, calls = console.say(lane._reply(**SEP), "last month")
    (args,) = calls
    assert args["top_n"] == 20, args
    assert args["brand_ids"] == [console.ids["brand"]], args
    assert args["date_from"] == "2026-09-01", args


def test_a_c_the_re_ask_is_bounded(console) -> None:
    console.say(_rank(_e("Sorento", "brand"), top_n=10, **NO_PERIOD), "top 10 salesman for sorento")
    _restated(console, top_n=20)
    text, calls = _restated(console, top_n=30)
    assert calls == [], calls
    assert "Which period?" not in text, text


# ================================================================================ (b)


def _outstanding_with_location(sf, mp, word: str):
    """The generic resolver places the product only; the warehouse word is one the LANE resolves
    itself (`resolve_warehouse_token`), so the resolver echoes it back unplaced."""
    import uuid

    from app.models.inventory import Warehouse
    from tests.chatbot import test_reports_step2_s5a as s5a
    from tests.chatbot.test_outstanding_lane import REPORT_HIT, _qf, _run_turn, _seed_contact

    _seed_contact(sf, variables={})
    db = sf()
    try:
        db.add(Warehouse(id=str(uuid.uuid4()), warehouse_code="ZZT-SENTUL", warehouse_name="ZZT SENTUL", is_active=True))
        db.commit()
    finally:
        db.close()
    ents = [
        {"raw": s5a.PRODUCT_CODE, "hint": "product", "canonical_code": None, "current_message": True, "confident": True},
        {"raw": word, "hint": "warehouse", "canonical_code": None, "current_message": True, "confident": True},
    ]
    return _run_turn(
        sf, mp, qf=_qf(order_status="so_outstanding", entities=ents), text_body="outstanding at location",
        msg_id="ZZT-polish-b", attributes=s5a.OUT_ATTRS, matches=s5a.PRODUCT_MATCH, mcp_response=REPORT_HIT,
    )


@pytest.mark.parametrize("word", ["Sentul", "ZZT-SENTUL"])
def test_b_a_location_the_lane_placed_is_not_reported_as_not_found(session_factory, monkeypatch, word) -> None:
    from tests.chatbot.test_reports_step2_s5a import _harness  # noqa: F401

    result, _calls = _outstanding_with_location(session_factory, monkeypatch, word)
    text = (result.reply or {}).get("text") or ""
    assert "Location: all" not in text, ("the word was not placed as the location", text)
    assert "could not find" not in text.lower() and "couldn't find" not in text.lower(), text


def test_b_b_a_location_word_nothing_holds_is_still_said(session_factory, monkeypatch) -> None:
    """Guard (passes now): a word that is no warehouse at all keeps the not-found line."""
    from tests.chatbot.test_reports_step2_s5a import _harness  # noqa: F401

    result, _calls = _outstanding_with_location(session_factory, monkeypatch, "Gudang")
    text = (result.reply or {}).get("text") or ""
    assert "could not find Gudang" in text, text
