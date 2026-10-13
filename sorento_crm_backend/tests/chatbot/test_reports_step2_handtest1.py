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


# ================================================================================ owner hand test: filters on a held ranking


def _answer_turn(**kw: Any) -> dict[str, Any]:
    """The parser's reading of a short answer to one of the ranking's questions."""
    base: dict[str, Any] = dict(
        message_type="business_query", domain_hint="order", intent_hint="check_order", order_status="sales_ranking",
        ranking_refine=True, domain_in_message=False, group_by=None, top_n=None, basis=None, measure=None,
        entities=[], date_filter_start=None, date_filter_end=None,
    )
    base.update(kw)
    return _live(_parser_output(**base))


def _held_ranking(console) -> dict[str, Any]:
    """The owner's turns 1 to 4: "top 5 salesman for sorento", "2026", "qty", "delivered" -> a top-5 list."""
    console.say(
        _rank(_e("Sorento", "brand"), group_by="sales_agent", top_n=5, basis=None, measure=None,
              date_filter_start=None, date_filter_end=None),
        "top 5 salesman for sorento",
    )
    console.say(_answer_turn(date_filter_start="2026-01-01", date_filter_end="2026-12-31"), "2026")
    console.say(_answer_turn(measure="qty"), "qty")
    text, calls = console.say(_answer_turn(basis="delivered"), "delivered")
    (args,) = calls
    assert args["group_by"] == "sales_agent" and args["top_n"] == 5, (text, args)
    return args


def _analysis_filter(**kw: Any) -> dict[str, Any]:
    """Crew's turn 5 emission: the parser reads "dealer sales only" as a company analysis with a channel."""
    base: dict[str, Any] = dict(
        message_type="business_query", domain_hint="order", intent_hint="check_order", order_status="sales_analysis",
        domain_in_message=True, topic_reset=False, sales_channel="dealer", basis="delivered", sales_basis="delivered",
        entity_op="reuse", ranking_refine=None, group_by=None, top_n=None, entities=[],
        date_filter_start=None, date_filter_end=None,
    )
    base.update(kw)
    return _live(_parser_output(**base))


def _ranking_calls(log):
    return [a for n, a in log if n == "crm_report"]


import pytest  # noqa: E402


@pytest.mark.parametrize("in_message, refine", [(False, None), (True, False), (False, True), (True, None)])
def test_h31_dealer_only_re_runs_the_held_ranking_with_the_channel(console, monkeypatch, in_message, refine) -> None:
    from tests.chatbot.test_reports_step2_lane import _wire

    before = _held_ranking(console)
    qf = _analysis_filter(domain_in_message=in_message, ranking_refine=refine)
    text, log = lane._say_all(console, monkeypatch, qf, "i want to see dealer sales only")
    (after,) = _ranking_calls(log)
    assert "crm_sales_analysis" not in _names(log), log
    assert after["channel"] == "dealer" and after["group_by"] == "sales_agent" and after["top_n"] == 5, after
    for key in ("brand_ids", "date_from", "date_to", "measure"):
        assert after.get(key) == before.get(key), (key, after, before)
    held = _wire(console)["report"]
    assert held and not held.get("kind") and held["args"]["group_by"] == "sales_agent", held  # still held for turn 6


def test_h32_a_brand_swap_re_runs_the_held_ranking_for_the_other_brand(console, monkeypatch) -> None:
    import uuid as _uuid

    from app.models.product import Brand
    from app.services.company_scope import DEFAULT_COMPANY_ID

    before = _held_ranking(console)
    db = console.session_factory()
    try:
        other = Brand(id=str(_uuid.uuid4()), brand_code="ZZTMOCHI", brand_name="Mochi", company_id=DEFAULT_COMPANY_ID)
        db.add(other)
        db.commit()
        other_id = other.id
    finally:
        db.close()
    try:
        qf = _live(_parser_output(
            message_type="business_query", domain_hint="order", intent_hint="check_order", order_status="sales_ranking",
            group_by="sales_agent", top_n=5, ranking_refine=True, basis="delivered", rank_by="quantity", measure="qty",
            entities=[_e("Mochi", "brand")], entity_op="replace_combine", domain_in_message=False,
            broaden_axis="brand", broaden_to="all", date_filter_start=None, date_filter_end=None,
        ))
        text, log = lane._say_all(console, monkeypatch, qf, "mochi only")
        (after,) = _ranking_calls(log)
        assert after["brand_ids"] == [other_id] and after["group_by"] == "sales_agent", after
        assert after["date_from"] == before["date_from"] and after["measure"] == before["measure"], after
    finally:
        db = console.session_factory()
        try:
            db.query(Brand).filter(Brand.id == other_id).delete()
            db.commit()
        finally:
            db.close()


def test_h33_project_only_re_runs_the_held_ranking_with_that_channel(console, monkeypatch) -> None:
    _held_ranking(console)
    qf = _live(_parser_output(
        message_type="business_query", domain_hint="order", intent_hint="check_order", order_status="sales_ranking",
        sales_channel="project", basis="delivered", ranking_refine=True, domain_in_message=False, entities=[],
        group_by=None, top_n=None, date_filter_start=None, date_filter_end=None,
    ))
    _text, log = lane._say_all(console, monkeypatch, qf, "project sales only")
    (after,) = _ranking_calls(log)
    assert after["channel"] == "project" and after["group_by"] == "sales_agent", after


def test_h34_a_basis_change_re_runs_the_held_ranking_on_that_basis(console, monkeypatch) -> None:
    _held_ranking(console)
    qf = _live(_parser_output(
        message_type="business_query", domain_hint="order", intent_hint="check_order", order_status="sales_ranking",
        basis="ordered", group_by="sales_agent", top_n=5, ranking_refine=True, domain_in_message=True, entities=[],
        date_filter_start=None, date_filter_end=None,
    ))
    _text, log = lane._say_all(console, monkeypatch, qf, "ordered not delivered")
    (after,) = _ranking_calls(log)
    assert after["measure"].startswith("ordered_") and after["group_by"] == "sales_agent", after


def test_h34b_an_analysis_that_changes_no_filter_the_ranking_carries_is_not_a_refine(console, monkeypatch) -> None:
    _held_ranking(console)
    qf = _analysis_filter(sales_channel=None)  # basis delivered is the held basis: nothing changed
    _text, log = lane._say_all(console, monkeypatch, qf, "show me sales")
    assert not _ranking_calls(log), log


def test_h36a_guard_a_new_analysis_axis_is_not_a_refine_of_the_held_ranking(console, monkeypatch) -> None:
    _held_ranking(console)
    _text, log = lane._say_all(console, monkeypatch, _analysis_filter(group_by="month"), "sales by month for dealers")
    assert not _ranking_calls(log) and "crm_sales_analysis" in _names(log), log  # it runs as its own analysis


def test_h36b_guard_a_new_period_is_not_a_refine_of_the_held_ranking(console, monkeypatch) -> None:
    _held_ranking(console)
    qf = _analysis_filter(date_filter_start="2025-01-01", date_filter_end="2025-12-31")
    _text, log = lane._say_all(console, monkeypatch, qf, "dealer sales in 2025")
    assert not _ranking_calls(log), log


def test_h36c_guard_a_named_company_is_not_a_refine_of_the_held_ranking(console, monkeypatch) -> None:
    _held_ranking(console)
    _text, log = lane._say_all(console, monkeypatch, _analysis_filter(sales_company="Mochi"), "dealer sales for Mochi")
    assert not _ranking_calls(log), log


def test_h36d_guard_a_new_product_subject_is_not_a_refine_of_the_held_ranking(console, monkeypatch) -> None:
    _held_ranking(console)
    qf = _analysis_filter(entities=[lane._hit(EXACT, "product")])
    _text, log = lane._say_all(console, monkeypatch, qf, "dealer sales of the model")
    assert not [a for a in _ranking_calls(log) if a.get("group_by") == "sales_agent"], log


# ================================================================================ the company question is an open question


COMPANIES = ["Zorvex", "Mochi"]


def _analysis_route(console, monkeypatch):
    """Every `crm_sales_analysis` call goes to the real presenter: a company question until a company is named."""
    import json

    from sorento_crm_mcp.presenters import present_response

    seen: list[dict[str, Any]] = []
    real_factory = lane._REAL_FETCH_SERVICES

    def _factory(*, mcp_call):
        def _route(name: str, args: dict[str, Any]) -> Any:
            if name != "crm_sales_analysis":
                return mcp_call(name, args)
            seen.append(dict(args))
            if args.get("company"):
                body = {"status": "ready", "rows": [], "columns": [], "totals": [], "company": args["company"]}
            else:
                body = {"status": "clarify", "message": "Zorvex or Mochi?", "companies": COMPANIES}
            return present_response(name, json.dumps(body))

        return real_factory(mcp_call=_route)

    monkeypatch.setattr(lane, "FetchServices", _factory)
    return seen


def _company_answer(**kw: Any) -> dict[str, Any]:
    """The parser's emission for the reply to the company question: it picks an option."""
    base: dict[str, Any] = dict(
        domain_hint="order", intent_hint="check_order", order_status=None, domain_in_message=False,
        entities=[], sales_company=None,
    )
    base.update(kw)
    qf = _live(_parser_output(**base))
    qf["open_question_answer"] = {"mode": "pick", "picked": [1], "items": [], "qty_for_all": None}
    return qf


def _wire_ask(console) -> dict[str, Any]:
    from tests.chatbot.test_reports_step2_lane import _wire

    return _wire(console).get("required_ask") or {}


def test_h35_the_company_question_is_stored_and_the_next_reply_answers_it(console, monkeypatch) -> None:
    seen = _analysis_route(console, monkeypatch)
    text, _calls = console.say(_analysis_filter(), "dealer sales")
    assert "Zorvex or Mochi?" in text and not seen[0].get("company"), (text, seen)
    assert _wire_ask(console).get("ask") == "sales_analysis_company", _wire_ask(console)
    qf = _company_answer()
    qf["reference_positions"] = [2]
    qf["open_question_answer"] = {"mode": "pick", "picked": [2], "items": [], "qty_for_all": None}
    text, _calls = console.say(qf, "mochi")
    assert "how can I help" not in text.lower(), text
    assert len(seen) == 2 and seen[1].get("company") == "Mochi" and seen[1].get("channel") == "dealer", seen
    assert not _wire_ask(console), _wire_ask(console)


def test_h35b_the_company_named_in_the_parsers_own_field_answers_it_too(console, monkeypatch) -> None:
    seen = _analysis_route(console, monkeypatch)
    console.say(_analysis_filter(), "dealer sales")
    qf = _company_answer(sales_company="mochi")
    qf["open_question_answer"] = {"mode": "fill", "picked": [], "items": [], "qty_for_all": None}
    console.say(qf, "mochi")
    assert seen[-1].get("company") == "Mochi", seen


# ================================================================================ turn 4: the basis answer with a status key


def test_h36_the_basis_answer_with_a_delivered_status_key_runs_the_ranking(console) -> None:
    text, calls = console.say(
        _rank(_e("Sorento", "brand"), group_by="sales_agent", top_n=5, basis=None, measure="qty", **SEP),
        "top 5 salesman for sorento",
    )
    assert calls == [] and "Delivered" in text, (text, calls)
    qf = _live(_parser_output(
        message_type="business_query", domain_hint="order", intent_hint="check_order", order_status="sales_ranking",
        status="delivered", basis=None, group_by=None, top_n=None, ranking_refine=True, domain_in_message=False,
        entities=[], date_filter_start=None, date_filter_end=None,
    ))
    text, calls = console.say(qf, "delivered")
    (args,) = calls
    assert args["measure"] == "delivered_qty" and args["group_by"] == "sales_agent", args


def test_h37_guard_a_status_beside_top_selling_is_left_to_the_outstanding_path() -> None:
    from app.services.chatbot.lanes.business import report_ask

    held = {"order_status": "top_selling", "status": "outstanding", "basis": None}
    assert report_ask.as_ranking_basis_status(held) == held
    ranking = {"order_status": "sales_ranking", "status": "delivered", "basis": None}
    out = report_ask.as_ranking_basis_status(ranking)
    assert out["status"] is None and out["basis"] == "delivered", out
    assert report_ask.as_ranking_basis_status({"order_status": "sales_ranking", "status": "open", "basis": None})["status"] == "open"


# ================================================================================ review round on the owner hand test fixes


def test_h31c_a_basis_status_beside_a_channel_analysis_is_the_rankings_basis(console, monkeypatch) -> None:
    _held_ranking(console)
    qf = _analysis_filter(status="ordered", basis=None, sales_basis=None)
    text, log = lane._say_all(console, monkeypatch, qf, "dealer sales ordered")
    (after,) = _ranking_calls(log)
    assert after["measure"].startswith("ordered_") and after["channel"] == "dealer", (text, after)


def _asked_company(console, monkeypatch):
    seen = _analysis_route(console, monkeypatch)
    console.say(_analysis_filter(), "dealer sales")
    assert _wire_ask(console).get("ask") == "sales_analysis_company"
    return seen


def test_h35c_an_unknown_company_twice_re_asks_then_gives_up(console, monkeypatch) -> None:
    from app.services.chatbot.lanes.business import analysis_ask

    seen = _asked_company(console, monkeypatch)
    qf = _company_answer(sales_company="Nobody")
    qf["open_question_answer"] = {"mode": "fill", "picked": [], "items": [], "qty_for_all": None}
    text, _calls = console.say(qf, "nobody")
    assert "Zorvex or Mochi?" in text and _wire_ask(console).get("ask") == "sales_analysis_company", text
    text, _calls = console.say(qf, "nobody")
    assert text.startswith("I still can't place"), text
    assert not _wire_ask(console) and len(seen) == 1, (_wire_ask(console), seen)


def test_h35d_a_cancel_answer_ends_the_question(console, monkeypatch) -> None:
    from app.services.chatbot.lanes.business import analysis_ask

    seen = _asked_company(console, monkeypatch)
    qf = _company_answer()
    qf["open_question_answer"] = {"mode": "cancel", "picked": [], "items": [], "qty_for_all": None}
    text, _calls = console.say(qf, "never mind")
    assert text.strip() == analysis_ask.ANALYSIS_COMPANY_ASK.cancelled, text
    assert not _wire_ask(console) and len(seen) == 1, (_wire_ask(console), seen)


def test_h35e_an_all_answer_says_one_company_is_needed(console, monkeypatch) -> None:
    from app.services.chatbot.lanes.business import analysis_ask

    seen = _asked_company(console, monkeypatch)
    qf = _company_answer()
    qf["open_question_answer"] = {"mode": "all", "picked": [], "items": [], "qty_for_all": None}
    text, _calls = console.say(qf, "both")
    assert analysis_ask.ONE_COMPANY in text and "Zorvex or Mochi?" in text, text
    assert _wire_ask(console).get("ask") == "sales_analysis_company" and len(seen) == 1, seen


def test_h38_guard_a_new_analysis_after_a_stock_turn_stays_an_analysis(console, monkeypatch) -> None:
    _held_ranking(console)
    console.say(_live(_parser_output(
        domain_hint="inventory", intent_hint="check_stock", message_type="business_query", domain_in_message=True,
        entities=[lane._hit(EXACT, "product")], entity_op="replace_combine",
    )), "stock of the model")
    qf = _analysis_filter(group_by="month", date_filter_start="2026-01-01", date_filter_end="2026-12-31")
    _text, log = lane._say_all(console, monkeypatch, qf, "dealer sales this year by month")
    assert "crm_sales_analysis" in _names(log) and not _ranking_calls(log), log


def test_h39_guard_the_held_channel_restated_is_no_change(console, monkeypatch) -> None:
    _held_ranking(console)
    lane._say_all(console, monkeypatch, _analysis_filter(), "dealer sales only")  # refines to dealer
    _text, log = lane._say_all(console, monkeypatch, _analysis_filter(), "dealer sales only")
    assert not _ranking_calls(log) and "crm_sales_analysis" in _names(log), log


def test_h40_guard_a_fresh_ask_on_the_held_axis_after_a_stock_turn_uses_its_own_period(console) -> None:
    _held_ranking(console)
    console.say(_live(_parser_output(
        domain_hint="inventory", intent_hint="check_stock", message_type="business_query", domain_in_message=True,
        entities=[lane._hit(EXACT, "product")], entity_op="replace_combine",
    )), "stock of the model")
    _text, calls = console.say(
        _rank(_e("Sorento", "brand"), group_by="sales_agent", top_n=5, basis="delivered", measure="qty",
              date_filter_start="2025-01-01", date_filter_end="2025-12-31"),
        "top 5 salesman for sorento in 2025",
    )
    (args,) = calls
    assert args["date_from"] == "2025-01-01" and "carried" not in args, args


def test_h41a_a_held_ranking_means_the_order_list_is_not_open() -> None:
    from app.services.chatbot.order_list import is_open_order_list
    from app.services.chatbot.turn.state import Focus

    ranking = {"measure": "delivered_amount", "args": {"group_by": "sales_agent"}, "shown": None}
    assert is_open_order_list(Focus(domains=["order"], status=None, report=ranking)) is False


def test_h41b_without_a_held_ranking_an_order_list_is_still_open() -> None:
    from app.services.chatbot.order_list import is_open_order_list
    from app.services.chatbot.turn.state import Focus

    assert is_open_order_list(Focus(domains=["order"], status=None)) is True
    plain = {"kind": "sales_report", "measure": "delivered_sales", "args": {}, "shown": None}
    assert is_open_order_list(Focus(domains=["order"], status=None, report=plain)) is True


def test_h42_guard_a_basis_the_ranking_cannot_do_is_not_a_refine(console, monkeypatch) -> None:
    _held_ranking(console)
    qf = _analysis_filter(sales_channel=None, sales_basis="invoiced", basis=None)
    _text, log = lane._say_all(console, monkeypatch, qf, "invoiced sales")
    assert not _ranking_calls(log), log


def _do_list_turn(console, monkeypatch):
    import json

    from app.services.ai_assistant_service import MCPRuntimeClient
    from sorento_crm_mcp.presenters import present_response

    from tests.chatbot._turn_helpers import do_window

    def _fake_call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        return present_response(name, json.dumps({"data": [], "total": 0, "page": 1, "limit": 20}))

    monkeypatch.setattr(MCPRuntimeClient, "call_tool", _fake_call_tool)
    do_list = _live(_parser_output(
        domain_hint="order", intent_hint="check_order", message_type="business_query", order_status=None,
        domain_in_message=True, entities=[_e(lane.CUSTOMER_NAME, "customer")], **do_window(),
    ))
    console.say(do_list, "orders for the customer")


def test_h43_an_order_list_answer_after_a_ranking_is_noted_on_the_held_report(console, monkeypatch) -> None:
    from tests.chatbot.test_reports_step2_lane import _wire

    _held_ranking(console)
    assert not _wire(console)["report"].get("listed_after"), _wire(console)["report"]
    _do_list_turn(console, monkeypatch)
    held = _wire(console)["report"]
    assert held and not held.get("kind") and held.get("listed_after") is True, held


def test_h43c_a_new_ranking_replaces_the_note(console, monkeypatch) -> None:
    from tests.chatbot.test_reports_step2_lane import _wire

    _held_ranking(console)
    _do_list_turn(console, monkeypatch)
    console.say(_rank(_e("Sorento", "brand"), group_by="sales_agent", top_n=5, basis="delivered", measure="qty", **SEP),
                "top 5 salesman for sorento")
    assert not _wire(console)["report"].get("listed_after"), _wire(console)["report"]


def test_h43d_a_brand_word_alone_continues_the_order_list_when_the_list_was_the_latest_answer(console) -> None:
    from app.services.chatbot.order_list import order_list_verdict
    from app.services.chatbot.turn.state import Focus, State

    ranking = {"measure": "delivered_amount", "args": {"group_by": "sales_agent"}, "shown": None}
    verdict = _parser_output(domain_hint="order", intent_hint="check_order", order_status=None, entities=[])
    from app.services.company_scope import DEFAULT_COMPANY_ID, set_company_scope

    db = console.session_factory()
    set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
    try:
        after_list = State(focus=Focus(domains=["order"], status=None, report={**ranking, "listed_after": True}))
        _v, _s, rule = order_list_verdict(db, verdict, after_list, "sorento")
        assert rule == "order_list_brand_switched", rule
        after_ranking = State(focus=Focus(domains=["order"], status=None, report=ranking))
        _v, _s, rule = order_list_verdict(db, verdict, after_ranking, "sorento")
        assert rule is None, rule
    finally:
        db.close()


def test_h43b_a_report_entry_notes_an_order_list_answer_after_it() -> None:
    from app.services.chatbot.order_list import is_open_order_list
    from app.services.chatbot.turn.state import Focus

    ranking = {"measure": "delivered_amount", "args": {"group_by": "sales_agent"}, "shown": None}
    assert is_open_order_list(Focus(domains=["order"], status=None, report={**ranking, "listed_after": True})) is True
    assert is_open_order_list(Focus(domains=["order"], status=None, report=ranking)) is False
