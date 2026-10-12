"""Phase 2 RED tests - REPORTS-STEP2 gate run 5 (12 Oct 2026).

(a) "only this exact model <code>" read by the parser as a PLAIN sales report verdict narrows the held
plain sales report (the parser's own refinement signal: `domain_in_message` false);
(b) a ranking restated with a new top N (same axis, same subject, no period) re-runs the held ranking.

Parser output fields only, never message text.
"""
from __future__ import annotations

from typing import Any

from tests.chatbot import test_report_ask_lane as lane
from tests.chatbot.test_report_ask_lane import PERIOD_Q, SEP, _e, _rank, console  # noqa: F401
from tests.chatbot.test_reports_step2_gate_run4 import (  # noqa: F401
    EMPTY_REPORT,
    EXACT,
    _hold,
    _present,
    _route_calls,
    _sales,
)
from tests.chatbot.test_reports_step2_lane import EARLIER, SEP_LAST, _refine as _refine_verdict, _s2_harness, _t1  # noqa: F401

NO_DATES = {"date_filter_start": None, "date_filter_end": None}


def _exact_verdict(*entities: dict[str, Any], **kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = dict(
        entities=list(entities) or [lane._hit(EXACT, "product")], domain_in_message=False, topic_reset=False,
        group_by=None, ranking_drill=None, **NO_DATES,
    )
    base.update(kw)
    return _sales(**base)


def _sent(seen: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    return [a for n, a in seen if n == "crm_sales_report"]


# ================================================================================ (a)


def test_a1_a_plain_sales_report_verdict_with_one_product_narrows_the_held_report(console, monkeypatch) -> None:
    _hold(console, monkeypatch)
    seen = _route_calls(console, monkeypatch, lambda args: _present(EMPTY_REPORT))
    text, _calls = console.say(_exact_verdict(), "only this exact model")
    (sent, *_rest) = _sent(seen) or [{}]
    assert sent.get("product_code") == EXACT and sent.get("product_code_exact") is True, (sent, text)
    assert sent.get("customer_ids") == [console.ids["customer"]], sent
    assert sent.get("date_from") == "2026-09-01" and sent.get("date_to") == "2026-09-30", sent
    assert "all products" not in text.lower(), text


def test_a2_guard_a_verdict_naming_the_domain_is_a_fresh_ask(console, monkeypatch) -> None:
    _hold(console, monkeypatch)
    seen = _route_calls(console, monkeypatch, lambda args: _present(EMPTY_REPORT))
    console.say(_exact_verdict(domain_in_message=True), "sales of this model")
    for sent in _sent(seen):
        assert not sent.get("product_code_exact"), sent
        assert not sent.get("customer_ids"), sent


def test_a3_guard_two_products_are_not_a_narrowing(console, monkeypatch) -> None:
    _hold(console, monkeypatch)
    seen = _route_calls(console, monkeypatch, lambda args: _present(EMPTY_REPORT))
    console.say(_exact_verdict(lane._hit(EXACT, "product"), lane._hit("SRT5674", "product")), "these models")
    for sent in _sent(seen):
        assert not sent.get("product_code_exact"), sent


def test_a4_guard_with_no_held_report_nothing_is_narrowed(console, monkeypatch) -> None:
    seen = _route_calls(console, monkeypatch, lambda args: _present(EMPTY_REPORT))
    console.say(_exact_verdict(), "only this exact model")
    for sent in _sent(seen):
        assert not sent.get("product_code_exact"), sent
        assert not sent.get("customer_ids"), sent


def test_a5_guard_a_topic_reset_is_not_a_narrowing(console, monkeypatch) -> None:
    """A topic reset already drops the held report earlier in the turn, so the `topic_reset` clause in
    `_narrows_held_sales_report` is a deliberate backstop; this pins the behaviour, not that clause."""
    _hold(console, monkeypatch)
    seen = _route_calls(console, monkeypatch, lambda args: _present(EMPTY_REPORT))
    console.say(_exact_verdict(topic_reset=True), "something else")
    for sent in _sent(seen):
        assert not sent.get("product_code_exact"), sent
        assert not sent.get("customer_ids"), sent


# ================================================================================ (b)


def _restated(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = dict(
        group_by="sales_agent", top_n=20, ranking_refine=False, ranking_drill="global", measure=None, basis=None,
        **NO_DATES,
    )
    base.update(kw)
    return _rank(_e("Sorento", "brand"), **base)


def _t1_printed(console) -> dict[str, Any]:
    _text, first = _t1(console)
    (before,) = first
    return before


def test_b1_a_restated_ranking_with_a_new_top_n_reruns_the_held_one(console) -> None:
    before = _t1_printed(console)
    text, calls = console.say(_restated(), "top 20")
    assert PERIOD_Q not in text, text
    (after,) = calls
    assert after["top_n"] == 20, after
    assert after["date_from"] == before["date_from"] and after["date_to"] == before["date_to"], after
    assert after["measure"] == before["measure"] and after["brand_ids"] == before["brand_ids"], after


def test_b2_the_same_with_no_drill_reading(console) -> None:
    before = _t1_printed(console)
    text, calls = console.say(_restated(ranking_drill=None), "top 20")
    assert PERIOD_Q not in text, text
    (after,) = calls
    assert after["top_n"] == 20 and after["date_from"] == before["date_from"], after


def test_b3_guard_a_different_subject_still_asks_the_period(console) -> None:
    _t1_printed(console)
    text, calls = console.say(_rank(_e("ZZT CATEGORY", "category"), top_n=20, ranking_drill="global",
                                    measure=None, basis=None, **NO_DATES), "top 20 for the category")
    assert calls == [] and PERIOD_Q in text, (calls, text)


def test_b4_guard_a_different_axis_is_unchanged(console) -> None:
    _t1_printed(console)
    text, calls = console.say(_restated(group_by="customer", top_n=5), "top 5 customers")
    assert calls == [] and PERIOD_Q in text, (calls, text)


def test_b5_guard_a_verdict_naming_its_own_period_uses_it(console) -> None:
    _t1_printed(console)
    _text, calls = console.say(_restated(measure="amount", basis="delivered", **SEP_LAST), "top 20 last month")
    (after,) = calls
    assert after["date_from"] == "2026-08-01" and after["date_to"] == "2026-08-31", after
    assert after["top_n"] == 20, after


# ================================================================================ direction flips


def test_b6_a_restatement_flipping_to_bottom_sorts_ascending(console) -> None:
    before = _t1_printed(console)
    assert before["sort"] == "desc", before
    text, calls = console.say(_restated(top_n=5, rank_direction="bottom"), "bottom 5")
    (after,) = calls
    assert after["sort"] == "asc" and after["top_n"] == 5, after
    assert after["date_from"] == before["date_from"] and "sort" not in after.get("carried", []), after
    assert PERIOD_Q not in text, text


def test_b7_a_restatement_flipping_back_to_top_sorts_descending(console) -> None:
    _text, first = _t1(console, rank_direction="bottom")
    assert first[0]["sort"] == "asc", first
    _text, calls = console.say(_restated(top_n=5, rank_direction="top"), "top 5")
    (after,) = calls
    assert after["sort"] == "desc" and after["top_n"] == 5, after


def test_b8_a_parser_refine_with_a_direction_flips_the_held_sort(console) -> None:
    before = _t1_printed(console)
    _text, calls = console.say(_refine_verdict(rank_direction="bottom"), "the bottom ones")
    (after,) = calls
    assert after["sort"] == "asc" and after["top_n"] == before["top_n"], after
    assert after["date_from"] == before["date_from"], after
