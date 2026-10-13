"""REPORTS-STEP2 hand test 1 (head 5a3f6f27), step 3 and step 14.

(h1/h2) "bottom 5" after a printed sales ranking is a refine of that ranking, not a top selling ask;
(h3/h4) a fresh top selling restatement with the same filters keeps the held metric, basis and period.

Parser output fields only, never message text; the live `open_question_answer` shape on every verdict.
"""
from __future__ import annotations

from typing import Any

from tests.chatbot import test_report_ask_lane as lane
from tests.chatbot.test_report_ask_lane import PERIOD_Q, SEP, _e, _parser_output, _rank, console  # noqa: F401
from tests.chatbot.test_reports_step2_gate_run7 import _live
from tests.chatbot.test_reports_step2_lane import _s2_harness, _t1  # noqa: F401  (autouse fixture by name)
from tests.chatbot.test_top_selling_round5 import _ask, _answer


def _names(log):
    return [n for n, _a in log]


# ================================================================================ step 3


def test_h1_bottom_5_after_a_held_ranking_refines_it(console) -> None:
    _text, first = _t1(console)
    (before,) = first
    qf = _live(_answer(rank_direction="bottom", top_n=5, domain_in_message=False))
    text, calls = console.say(qf, "bottom 5")
    (after,) = calls
    assert after["sort"] == "asc" and after["top_n"] == 5, after
    assert after["brand_ids"] == before["brand_ids"] and after["date_from"] == before["date_from"], after
    assert PERIOD_Q not in text, text


def test_h2_guard_a_top_selling_ask_naming_its_domain_stays_top_selling(console, monkeypatch) -> None:
    _t1(console)
    text, log = lane._say_all(console, monkeypatch, _live(_ask(top_n=10)), "top 10 selling items")
    assert "crm_report" not in _names(log), log
    assert "By quantity or by amount?" in text or "crm_top_selling_report" in _names(log), (text, log)


# ================================================================================ step 14

from tests.chatbot.test_reports_step2_gate_run7 import _brand_ranking  # noqa: E402


def _ts_calls(log):
    return [a for n, a in log if n == "crm_top_selling_report"]


def _ran_top_20(console, monkeypatch):
    """T1 the project brand ranking (relabelled top selling, metric asked), T2 the metric, T3 the basis."""
    text, log = lane._say_all(console, monkeypatch, _brand_ranking(top_n=20, rank_by=None, measure=None),
                              "top 20 project sales Sorento products")
    assert "By quantity or by amount?" in text and not _ts_calls(log), (text, log)
    text, log = lane._say_all(console, monkeypatch, _live(_answer(rank_by="quantity")), "quantity")
    if not _ts_calls(log):
        text, log = lane._say_all(console, monkeypatch, _live(_answer(basis="delivered")), "delivered")
    (args,) = _ts_calls(log)
    assert args["n"] == 20 and args["rank_by"] == "quantity", args
    return args


def test_h3_a_restated_top_selling_ask_with_the_same_filters_keeps_the_held_metric(console, monkeypatch) -> None:
    held = _ran_top_20(console, monkeypatch)
    text, log = lane._say_all(console, monkeypatch, _brand_ranking(top_n=40, rank_by=None, measure=None),
                              "project sales top 40 Sorento products")
    assert "By quantity or by amount?" not in text, text
    (args,) = _ts_calls(log)
    assert args["n"] == 40 and args["rank_by"] == "quantity" and args.get("channel") == "project", args
    assert args.get("brand_ids") == held.get("brand_ids"), (args, held)
    assert args.get("basis") == held.get("basis"), (args, held)


def test_h4_guard_a_different_brand_is_a_fresh_ask_and_the_metric_is_asked(console, monkeypatch) -> None:
    _ran_top_20(console, monkeypatch)
    qf = _brand_ranking(top_n=40, rank_by=None, measure=None)
    qf["entities"] = [_e("ZZT CATEGORY", "category")]
    text, log = lane._say_all(console, monkeypatch, qf, "project sales top 40 ZZT CATEGORY products")
    assert "By quantity or by amount?" in text and not _ts_calls(log), (text, log)


def test_h7_guard_the_same_brand_for_another_channel_is_a_fresh_ask(console, monkeypatch) -> None:
    _ran_top_20(console, monkeypatch)
    text, log = lane._say_all(
        console, monkeypatch, _brand_ranking(top_n=40, rank_by=None, measure=None, sales_channel="dealer"),
        "dealer sales top 40 Sorento products",
    )
    assert "By quantity or by amount?" in text and not _ts_calls(log), (text, log)


def test_h8_guard_a_fresh_ask_with_no_filter_of_its_own_asks_the_metric(console, monkeypatch) -> None:
    text, log = lane._say_all(console, monkeypatch, _live(_ask(top_n=20)), "top 20 selling items")
    assert "By quantity or by amount?" in text, text
    text, log = lane._say_all(console, monkeypatch, _live(_answer(rank_by="quantity")), "quantity")
    assert _ts_calls(log), (text, log)
    text, log = lane._say_all(console, monkeypatch, _live(_ask(top_n=10)), "top 10 selling items")
    assert "By quantity or by amount?" in text and not _ts_calls(log), (text, log)


# ================================================================================ v92 shapes (made-up values)


def _v92_project_brand(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = dict(
        domain_hint="sales", intent_hint="check_order", order_status="sales_ranking", domain_in_message=True,
        group_by="brand", top_n=20, rank_group="item", rank_direction="top", rank_by=None, basis=None, measure=None,
        sales_channel="project", ranking_refine=False, entities=[_e("Sorento", "brand")],
        date_filter_start=None, date_filter_end=None,
    )
    base.update(kw)
    return _live(_parser_output(**base))


def test_h13_a_project_brand_item_ranking_with_the_brand_as_group_is_the_top_selling_ask(console, monkeypatch) -> None:
    text, log = lane._say_all(console, monkeypatch, _v92_project_brand(), "top 20 project sales Sorento products")
    assert "By quantity or by amount?" in text and "crm_report" not in _names(log), (text, log)
    text, log = lane._say_all(console, monkeypatch, _live(_answer(rank_by="quantity")), "quantity")
    if not _ts_calls(log):
        text, log = lane._say_all(console, monkeypatch, _live(_answer(basis="delivered")), "delivered")
    (args,) = _ts_calls(log)
    assert args["n"] == 20 and args.get("channel") == "project", args
    assert console.ids["brand"] in (args.get("brand_ids") or []), args
    assert args.get("group") is None, args  # top selling ranks items; a brand axis is never sent


def test_h14_guard_a_real_brand_ranking_stays_on_the_ranking_lane(console, monkeypatch) -> None:
    # rank_group "item" with no brand named: it reaches the "brand named" check and stays on the ranking lane.
    qf = _v92_project_brand(rank_group="item", entities=[], top_n=5, sales_channel=None)
    text, log = lane._say_all(console, monkeypatch, qf, "top 5 brands")
    assert "crm_top_selling_report" not in _names(log), log
    assert PERIOD_Q in text or "crm_report" in _names(log), (text, log)


def _v92_outstanding(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = dict(
        domain_hint="order", intent_hint="check_order", order_status="outstanding", domain_in_message=True,
        group_by="customer", top_n=5, rank_group=None, rank_by="amount", rank_direction=None, basis="outstanding",
        measure=None, entities=[_e("Sorento", "brand"), _e("BRW", "warehouse")], report_ask_words=None, **SEP,
    )
    base.update(kw)
    return _live(_parser_output(**base))


def test_h15_an_outstanding_verdict_with_a_ranking_shape_is_a_sales_ranking(console, monkeypatch) -> None:
    text, log = lane._say_all(console, monkeypatch, _v92_outstanding(), "top 5 customers for Sorento outstanding at BRW")
    assert "crm_outstanding_report" not in _names(log), (text, log)
    (args,) = [a for n, a in log if n == "crm_report"]
    assert args["group_by"] == "customer" and args["top_n"] == 5, args
    assert args["measure"] == "outstanding_amount" and args["warehouse_codes"] == ["BRW"], args
    assert args["date_from"] == "2026-09-01" and args["date_to"] == "2026-09-30", args


def test_h16_guard_a_plain_outstanding_ask_stays_the_outstanding_report(console, monkeypatch) -> None:
    qf = _v92_outstanding(group_by=None, top_n=None, rank_by=None, basis=None, entities=[_e(lane.CUSTOMER_NAME, "customer")])
    _text, log = lane._say_all(console, monkeypatch, qf, "outstanding for the customer")
    assert "crm_report" not in _names(log), log


def test_h17_guard_an_outstanding_verdict_with_an_axis_but_no_count_or_direction_stays_outstanding(
    console, monkeypatch
) -> None:
    qf = _v92_outstanding(top_n=None, rank_direction=None)
    _text, log = lane._say_all(console, monkeypatch, qf, "outstanding by customer for Sorento at BRW")
    assert "crm_report" not in _names(log) and "crm_outstanding_report" in _names(log), log


# ================================================================================ live check #3 (made-up values)

from tests.chatbot.test_reports_step2_gate_run4 import EXACT, _present, _route_calls  # noqa: E402
from tests.chatbot.test_reports_step2_gate_run5 import _sales, _sent  # noqa: E402
from tests.chatbot.test_reports_step2_gate_run7 import FAMILY  # noqa: E402

FAMILY_NO_OFFER = {**FAMILY, "options": []}


def _hold_plain(console, monkeypatch, **kw):
    """A plain sales report held (no drill offer open), then the seen-calls list."""
    seen = _route_calls(console, monkeypatch, lambda args: _present(FAMILY_NO_OFFER))
    console.say(_sales(**kw), "sales report for the customer")
    assert (console.session_vars.get("open_question") or {}).get("kind") != "sales_report_detail", console.session_vars
    return seen


# ------------------------------------------------------------------ A. step 3, "bottom 5" with the parser's extras


def _bottom_5(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = dict(
        order_status="top_selling", ranking_refine=True, rank_group="unclear", group_by="sales_agent",
        rank_direction="bottom", top_n=5, domain_in_message=False, entities=[],
    )
    base.update(kw)
    return _live(_answer(**base))


def test_h18_bottom_5_with_the_held_axis_echoed_still_refines_the_held_ranking(console) -> None:
    _text, first = _t1(console)
    (before,) = first
    text, calls = console.say(_bottom_5(), "bottom 5")
    (after,) = calls
    assert after["group_by"] == "sales_agent" and after["top_n"] == 5 and after["sort"] == "asc", after
    assert after["date_from"] == before["date_from"], after
    assert "category" not in text.lower() and "items" not in text.lower(), text


def test_h19_guard_another_axis_than_the_held_one_is_not_a_refine_of_it(console, monkeypatch) -> None:
    _t1(console)
    text, log = lane._say_all(console, monkeypatch, _bottom_5(group_by="customer"), "bottom 5 customers")
    kept = [a for n, a in log if n == "crm_report" and a.get("group_by") == "sales_agent" and a.get("sort") == "asc"]
    assert not kept, log
    assert "top items inside one category" in text, text  # it stays the top selling ask, not a ranking refine or drill


# ------------------------------------------------------------------ B. step 7, "only <code>" with no status


def _only_code(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "order_status": None, "status": None, "domain_hint": "order", "domain_in_message": False,
        "topic_reset": False, "entity_op": "replace", "message_type": "business_query",
        "entities": [{"raw": "ZZT100", "hint": "product", "current_message": True}],
    }
    base.update(kw)
    return base


def test_h20_unit_a_status_less_only_code_narrows() -> None:
    from app.services.chatbot.turn_runtime import _narrows_held_sales_report

    assert _narrows_held_sales_report(_only_code()) is True


def test_h21_a_status_less_only_code_narrows_the_held_report(console, monkeypatch) -> None:
    seen = _hold_plain(console, monkeypatch)
    qf = _live(_parser_output(
        domain_hint="order", intent_hint="check_order", order_status=None, status=None, domain_in_message=False,
        topic_reset=False, entity_op="replace", entities=[lane._hit(EXACT, "product")], group_by=None,
        date_filter_start=None, date_filter_end=None,
    ))
    text, _calls = console.say(qf, "only this model")
    assert "Which period?" not in text, text
    sent = [a for a in _sent(seen) if a.get("product_code_exact")]
    assert sent and sent[0].get("product_code") == EXACT, [a.get("product_code") for a in _sent(seen)]


def test_h22a_guard_a_domain_that_is_not_a_sales_one_does_not_narrow() -> None:
    from app.services.chatbot.turn_runtime import _narrows_held_sales_report

    assert _narrows_held_sales_report(_only_code(domain_hint="dealer_locator")) is False


def test_h22b_guard_a_message_naming_its_domain_does_not_narrow() -> None:
    from app.services.chatbot.turn_runtime import _narrows_held_sales_report

    assert _narrows_held_sales_report(_only_code(domain_in_message=True)) is False


def test_h22c_guard_two_products_do_not_narrow() -> None:
    from app.services.chatbot.turn_runtime import _narrows_held_sales_report

    two = [{"raw": "ZZT100", "hint": "product", "current_message": True},
           {"raw": "ZZT200", "hint": "product", "current_message": True}]
    assert _narrows_held_sales_report(_only_code(entities=two)) is False
    other = [*two[:1], {"raw": "Zorvex", "hint": "brand", "current_message": True}]
    assert _narrows_held_sales_report(_only_code(entities=other)) is False


# ------------------------------------------------------------------ D. step 15, status "outstanding" beside sales_ranking


def _outstanding_ranking_with_status(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = dict(
        order_status="sales_ranking", status="outstanding", domain_hint="order", intent_hint="check_order",
        domain_in_message=True, group_by="customer", top_n=5, basis="outstanding", rank_by="amount", measure=None,
        entities=[_e("BRW", "warehouse")], **SEP,
    )
    base.update(kw)
    return _live(_parser_output(**base))


def _assert_outstanding_ranking(log) -> None:
    names = _names(log)
    assert "crm_outstanding_report" not in names, log
    (args,) = [a for n, a in log if n == "crm_report"]
    assert args["measure"] == "outstanding_amount" and args["group_by"] == "customer", args
    assert args["warehouse_codes"] == ["BRW"] and not args.get("product_code"), args


def test_h25_a_sales_ranking_with_an_outstanding_status_key_stays_a_ranking(console, monkeypatch) -> None:
    seen = _route_calls(console, monkeypatch, lambda args: _present(FAMILY_NO_OFFER))
    console.say(_sales(entities=[lane._hit(EXACT, "product")]), f"sales of {EXACT}")  # a product in focus
    text, log = lane._say_all(console, monkeypatch, _outstanding_ranking_with_status(), "top 5 customers outstanding at BRW")
    _assert_outstanding_ranking(log)


def test_h26_the_outstanding_order_status_backstop_with_a_status_key_too(console, monkeypatch) -> None:
    seen = _route_calls(console, monkeypatch, lambda args: _present(FAMILY_NO_OFFER))
    console.say(_sales(entities=[lane._hit(EXACT, "product")]), f"sales of {EXACT}")
    qf = _outstanding_ranking_with_status(order_status="outstanding")
    text, log = lane._say_all(console, monkeypatch, qf, "top 5 customers outstanding at BRW")
    _assert_outstanding_ranking(log)


# ------------------------------------------------------------------ review round on the status-less arm


def test_h27_guard_a_one_product_follow_up_that_is_not_an_only_does_not_narrow() -> None:
    from app.services.chatbot.turn_runtime import _narrows_held_sales_report

    assert _narrows_held_sales_report(_only_code(entity_op="replace_combine")) is False


def test_h28_guard_a_help_request_does_not_narrow() -> None:
    from app.services.chatbot.turn_runtime import _narrows_held_sales_report

    assert _narrows_held_sales_report(_only_code(message_type="request_for_help")) is False


def test_h29_guard_a_parser_status_key_does_not_narrow() -> None:
    from app.services.chatbot.turn_runtime import _narrows_held_sales_report

    assert _narrows_held_sales_report(_only_code(status="outstanding")) is False


def test_h30_guard_a_topic_reset_does_not_narrow() -> None:
    from app.services.chatbot.turn_runtime import _narrows_held_sales_report

    assert _narrows_held_sales_report(_only_code(topic_reset=True)) is False
