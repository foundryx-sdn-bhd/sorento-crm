"""Phase 2 RED tests - REPORTS-STEP2 gate run 4 (11 Oct 2026).

(a) narrowing a held plain sales report to one exact product code applies to that report;
(b) a ranking waiting on the metric question keeps its pending ask when the next parse has no metric.

Parser output fields only, never message text.
"""
from __future__ import annotations

from typing import Any

import pytest

from tests.chatbot import test_report_ask_lane as lane
from tests.chatbot.test_report_ask_lane import PERIOD_Q, SEP, _e, _parser_output, _rank, console  # noqa: F401
from tests.chatbot.test_reports_step2_lane import _s2_harness, _wire  # noqa: F401  (autouse fixture by name)

EXACT = "SRTWT7408"


def _sales(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = dict(
        domain_hint="order", intent_hint="check_order", order_status="sales_report",
        entities=[lane._hit(lane.CUSTOMER_NAME, "customer")], **SEP,
    )
    base.update(kw)
    return _parser_output(**base)


def _present(body: dict[str, Any]) -> str:
    """A route body through the real presenter, as production does."""
    import json

    from sorento_crm_mcp.presenters import present_response

    return present_response("crm_sales_report", json.dumps(body))


#: A real, empty sales report body: the route answered 200 and nothing was delivered.
EMPTY_REPORT: dict[str, Any] = {
    "status": "ok", "message": None, "basis": "delivered", "customer_name": lane.CUSTOMER_NAME, "customer_count": 1,
    "product_code": EXACT, "product_codes": [EXACT], "channel": None, "channel_shown": False,
    "location_token": None, "warehouse_codes": [], "date_from": "2026-09-01", "date_to": "2026-09-30",
    "grain": "month", "total": {"qty": 0, "amount": 0.0}, "periods": [], "group_by": None, "rows": [],
    "more": 0, "options": [],
}


def _route_calls(console, monkeypatch, answer):
    """Install after the report is held: every later `crm_sales_report` call goes to `answer(args)`,
    which returns the raw route string or raises. Returns the list of (name, args) the lane sent."""
    seen: list[tuple[str, dict[str, Any]]] = []
    real_factory = lane._REAL_FETCH_SERVICES

    def _factory(*, mcp_call):
        def _route(name: str, args: dict[str, Any]) -> Any:
            seen.append((name, dict(args)))
            if name == "crm_sales_report":
                return answer(args)
            return mcp_call(name, args)

        return real_factory(mcp_call=_route)

    monkeypatch.setattr(lane, "FetchServices", _factory)
    return seen


def _narrow(*entities: dict[str, Any], **kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = dict(
        domain_hint="order", intent_hint="check_order", order_status="sales_ranking", ranking_drill="scoped",
        entities=list(entities) or [lane._hit(EXACT, "product")], group_by=None, **SEP,
    )
    base.update(kw)
    return _parser_output(**base)


def _hold(console, monkeypatch, qf=None) -> None:
    text, log = lane._say_all(console, monkeypatch, qf or _sales(), "sales report for the customer")
    assert [n for n, _ in log] == ["crm_sales_report"], (log, text)


def test_a_a_narrowing_a_held_sales_report_applies_to_that_report(console, monkeypatch) -> None:
    """The narrowed product is a real row (the harness seeds SRTWT7408) and the route answers a real empty report (200, no periods),
    so "No sales found." comes through the report's own miss path, not a 404 or an error."""
    _hold(console, monkeypatch)
    seen = _route_calls(console, monkeypatch, lambda args: _present(EMPTY_REPORT))
    text, _calls = console.say(_narrow(), "only this one")
    assert "I don't have a ranking" not in text, text
    sent = [a for n, a in seen if n in ("crm_sales_report", "crm_report")]
    assert sent, (seen, text)
    assert sent[0].get("product_code") == EXACT, sent[0]
    assert sent[0].get("customer_ids") == [console.ids["customer"]], sent[0]  # the held report's own filter stays
    assert "No sales found" in text, text
    assert "all products" not in text.lower(), text  # the held total is never shown under the exact code


# ================================================================================ (b)

METRIC_Q = "By quantity or by amount?"


def _metricless(**kw: Any) -> dict[str, Any]:
    return _rank(_e("Sorento", "brand"), top_n=10, measure=None, basis="delivered", **{**SEP, **kw})


def _no_metric_reading(**kw: Any) -> dict[str, Any]:
    """A one-word answer the parser misread as a ranking ask with a stray, unconfident word and no metric."""
    base: dict[str, Any] = dict(
        domain_hint="order", intent_hint="check_order", order_status="sales_ranking",
        entities=[{"raw": "qty", "hint": "other", "canonical_code": None, "current_message": True, "confident": False}],
        group_by=None, top_n=None, measure=None, basis=None, ranking_refine=False,
        date_filter_start=None, date_filter_end=None,
    )
    base.update(kw)
    return _parser_output(**base)


def test_b_a_a_reply_with_no_metric_keeps_the_pending_ask_and_re_asks_the_metric(console) -> None:
    text, calls = console.say(_metricless(), "top 10 sorento")
    assert text.strip() == METRIC_Q and calls == [], (text, calls)
    text, calls = console.say(_no_metric_reading(), "qty")
    assert calls == [], calls
    assert lane.CATALOGUE_LINE not in text and "I captured" not in text, text
    assert METRIC_Q in text, text
    pending = _wire(console).get("required_ask") or {}
    assert pending.get("asking") == "metric", pending


def test_b_b_the_kept_ask_runs_with_its_held_values_on_the_next_answer(console) -> None:
    console.say(_metricless(), "top 10 sorento")
    console.say(_no_metric_reading(), "qty")
    _text, calls = console.say(_no_metric_reading(measure="qty", entities=[]), "quantity")
    (args,) = calls
    assert args["measure"] == "delivered_qty" and args["top_n"] == 10, args
    assert args["brand_ids"] == [console.ids["brand"]], args
    assert args["date_from"] == "2026-09-01", args


def test_b_c_the_re_ask_is_bounded_by_the_existing_give_up(console) -> None:
    console.say(_metricless(), "top 10 sorento")
    console.say(_no_metric_reading(), "qty")
    text, calls = console.say(_no_metric_reading(), "qty")
    assert calls == [], calls
    assert METRIC_Q not in text and lane.CATALOGUE_LINE not in text and "I captured" not in text, text
    assert "I still can't read" in text, text


def test_b_d_the_open_metric_question_reaches_the_parser_with_its_options(console) -> None:
    """`turn/question.of_required_ask` builds the `Open question:` object the parser answers."""
    from app.services.chatbot.turn import question

    console.say(_metricless(), "top 10 sorento")
    slot = _wire(console).get("required_ask")
    assert slot and slot.get("asking") == "metric", slot
    obj = question.of_required_ask(slot)
    assert obj is not None, "the sales ranking's open question is never stated to the parser"
    assert obj["owed"] == ["metric"], obj
    assert {o["code"] for o in obj.get("options") or []} == {"qty", "amount"}, obj


# ================================================================================ narrowing counts the exact code only


def test_c_the_narrowed_report_counts_the_exact_code_not_its_longer_sibling(console, monkeypatch) -> None:
    """The sales report route reads `product_code` as a PREFIX (S19), so a narrowing that only re-sends
    the typed code also counts a sibling that starts with it. The call the lane sends is run against the
    REAL report service (keys the service does not take are dropped, so an exact-match option the fix adds
    to the service is honoured and a call with none is not): CODEX-1 carries 3, CODEX-1-NEW carries 5."""
    import inspect
    from datetime import date

    from app.services.company_scope import DEFAULT_COMPANY_ID
    from app.services.sales_report_delivered import delivered_sales_report
    from tests._mc_lookup_seed import product, warehouse
    from tests._sales_report_do_seed import line, seed_do

    db = console.session_factory()
    try:
        exact = product(db, company_id=DEFAULT_COMPANY_ID, code="CODEX-1")
        sibling = product(db, company_id=DEFAULT_COMPANY_ID, code="CODEX-1-NEW")
        wh = warehouse(db, company_id=DEFAULT_COMPANY_ID, code="CDXWH")
        seed_do(db, customer_id=console.ids["customer"], order_date=date(2026, 9, 10),
                lines=[line(exact.id, wh.id, 3, price=10, total=30)])
        seed_do(db, customer_id=console.ids["customer"], order_date=date(2026, 9, 11),
                lines=[line(sibling.id, wh.id, 5, price=10, total=50)])
        db.commit()
    finally:
        db.close()

    lane._say_all(console, monkeypatch, _sales(), "sales report for the customer")
    narrow = _parser_output(
        domain_hint="order", intent_hint="check_order", order_status="sales_ranking", ranking_drill="scoped",
        entities=[lane._hit("CODEX-1", "product")], group_by=None, **SEP,
    )
    _text, log = lane._say_all(console, monkeypatch, narrow, "only this one")
    sent = [a for n, a in log if n == "crm_sales_report"]
    assert sent, log

    takes = set(inspect.signature(delivered_sales_report).parameters) - {"db"}
    kwargs = {k: v for k, v in sent[0].items() if k in takes}
    for key in ("date_from", "date_to"):
        if isinstance(kwargs.get(key), str):
            kwargs[key] = date.fromisoformat(kwargs[key])
    db = console.session_factory()
    db.info["company_scope"] = None  # all companies: the service is called outside a request
    try:
        body = delivered_sales_report(db, **kwargs)
    finally:
        db.close()
    assert body["product_codes"] == ["CODEX-1"], (body["product_codes"], sent[0])
    assert body["total"]["qty"] == 3, (body["total"], sent[0])


# ================================================================================ a dealer unlinked between turns


def test_d_a_dealer_unlinked_between_turns_gets_the_own_account_line_not_figures(console, monkeypatch) -> None:
    """Scope is re-checked by the route on every re-run: the narrowing turn re-sends the held customer, the
    contact is no longer linked to it, the route answers 403 `customer_not_permitted`, and the reply is that
    line with no figures (this may already pass; it pins the scope)."""
    import json

    from sqlalchemy import text as sql

    own = lane._link_dealer(console)
    hold = _parser_output(
        domain_hint="order", intent_hint="check_order", order_status="sales_report",
        entities=[lane._hit("ZZT OWN DEALER SDN BHD", "customer")], **SEP,
    )
    text, log = lane._say_all(console, monkeypatch, hold, "sales report for my account")
    assert [n for n, _ in log] == ["crm_sales_report"], (log, text)

    db = console.session_factory()
    try:
        db.execute(sql("DELETE FROM respond_contact_customers WHERE customer_id = :c"), {"c": own})
        db.commit()
    finally:
        db.close()

    real_factory = lane._REAL_FETCH_SERVICES
    seen: list[tuple[str, dict[str, Any]]] = []

    def _factory(*, mcp_call):
        def _route(name: str, args: dict[str, Any]) -> Any:
            seen.append((name, dict(args)))
            if name == "crm_sales_report" and own in (args.get("customer_ids") or []):
                return json.dumps({
                    "result_type": "crm_sales_report", "error": "GET /sales-report returned 403 customer_not_permitted",
                    "status_code": 403, "detail": {"code": "customer_not_permitted",
                                                   "message": "You can only see sales for your own account."},
                    "response": "", "answers": [], "attachments": [], "has_result": False,
                })
            return mcp_call(name, args)

        return real_factory(mcp_call=_route)

    monkeypatch.setattr(lane, "FetchServices", _factory)
    narrow = _parser_output(
        domain_hint="order", intent_hint="check_order", order_status="sales_ranking", ranking_drill="scoped",
        entities=[lane._hit(EXACT, "product")], group_by=None, **SEP,
    )
    text, _calls = console.say(narrow, "only this one")
    assert text.strip() in (
        "You can only see sales for your own account.",
        "Sorry, that isn't under your account. I can only check on ZZT OWN DEALER SDN BHD.",
    ) or "your own account" in text or "isn't under your account" in text, (text, seen)
    assert "No sales found" not in text and "Delivered" not in text, text


# ================================================================================ review of gate run 4 (R1 to R4)


def _route_404() -> str:
    import json

    from sorento_crm_mcp.http_client import http_error_body

    return _present(json.loads(http_error_body(
        json.dumps({"detail": "product_not_found"}), status_code=404, path="/api/v1/sales-report", method="GET",
    )))


def _route_500() -> str:
    import json

    from sorento_crm_mcp.http_client import http_error_body

    return _present(json.loads(http_error_body(
        json.dumps({"detail": "boom"}), status_code=500, path="/api/v1/sales-report", method="GET",
    )))


def test_r1_a_404_on_the_narrowing_turn_is_not_an_empty_report(console, monkeypatch) -> None:
    """An unknown code is a 404, not a sale of nothing: it keeps today's miss path (AC-1658, the
    escalate offer), never "No sales found." with a result."""
    _hold(console, monkeypatch)
    seen = _route_calls(console, monkeypatch, lambda args: _route_404())
    text, _calls = console.say(_narrow(), "only this one")
    assert [n for n, _ in seen].count("crm_sales_report") == 1, seen  # the narrowing did run the report
    assert "No sales found" not in text, text
    assert text.strip(), text


def test_r1_b_a_500_on_the_narrowing_turn_keeps_the_error_path(console, monkeypatch) -> None:
    _hold(console, monkeypatch)
    seen = _route_calls(console, monkeypatch, lambda args: _route_500())
    text, _calls = console.say(_narrow(), "only this one")
    assert [n for n, _ in seen].count("crm_sales_report") == 1, seen
    assert "No sales found" not in text, text


def test_r1_c_a_tool_that_raises_on_the_narrowing_turn_keeps_the_error_path(console, monkeypatch) -> None:
    _hold(console, monkeypatch)

    def _boom(args: dict[str, Any]) -> Any:
        raise TimeoutError("sales report timed out")

    seen = _route_calls(console, monkeypatch, _boom)
    text, _calls = console.say(_narrow(), "only this one")
    assert [n for n, _ in seen].count("crm_sales_report") == 1, seen
    assert "No sales found" not in text, text


@pytest.mark.parametrize(
    "result",
    [
        "Error executing tool crm_sales_report: GET /api/v1/sales-report returned HTTP 404",
        "Error executing tool crm_sales_report: GET /api/v1/sales-report returned HTTP 500",
        "Error executing tool crm_sales_report: timed out",
        {"error": "GET /sales-report returned HTTP 404", "status_code": 404, "has_result": False},
        {"has_result": False, "items": []},
    ],
    ids=["404_text", "500_text", "timeout_text", "404_dict_no_response", "no_envelope_dict"],
)
def test_r1_d_a_result_with_no_report_envelope_is_never_an_empty_report(console, monkeypatch, result) -> None:
    """The narrowing turn's tool answer is not a rendered report at all (a bare error string or a body with
    no `response`). That is a failure or a miss on an unknown code, never "the product sold nothing"."""
    _hold(console, monkeypatch)
    seen = _route_calls(console, monkeypatch, lambda args: result)
    text, _calls = console.say(_narrow(), "only this one")
    assert [n for n, _ in seen].count("crm_sales_report") == 1, seen
    assert "No sales found" not in text, text


def test_r2_a_report_held_for_one_location_keeps_it_when_narrowed(console, monkeypatch) -> None:
    lane._other_customer(console, "ZZT ALPHA SDN BHD")
    hold = _sales(entities=[lane._hit("ZZT ALPHA SDN BHD", "customer"), _e("BRW", "warehouse")])
    text, log = lane._say_all(console, monkeypatch, hold, "sales report for the customer")
    first = [a for n, a in log if n == "crm_sales_report"]
    assert first and first[0].get("warehouse_codes") == ["BRW"], (first, text)  # the held report is for BRW
    seen = _route_calls(console, monkeypatch, lambda args: _present(EMPTY_REPORT))
    console.say(_narrow(), "only this one")
    sent = [a for n, a in seen if n == "crm_sales_report"]
    assert sent, seen
    assert sent[0].get("product_code") == EXACT, sent[0]
    assert sent[0].get("warehouse_codes") == ["BRW"], sent[0]
    assert sent[0].get("location_token"), sent[0]


def test_r3_a_customer_typed_in_the_narrowing_turn_beats_the_held_one(console, monkeypatch) -> None:
    other = lane._other_customer(console, "ZZT BRAVO SDN BHD")
    _hold(console, monkeypatch)
    seen = _route_calls(console, monkeypatch, lambda args: _present(EMPTY_REPORT))
    narrow = _narrow(lane._hit(EXACT, "product"), lane._hit("ZZT BRAVO SDN BHD", "customer"))
    console.say(narrow, "only this one for the typed customer")
    sent = [a for n, a in seen if n in ("crm_sales_report", "crm_report")]
    assert sent, seen
    assert sent[0].get("customer_ids") == [other], (sent[0], console.ids["customer"])


def test_r4_a_channel_carried_through_a_detail_offer_survives_the_narrowing(console, monkeypatch) -> None:
    """Held after a detail-offer re-run: the channel rode in as `outstanding_carried_channel`, not as
    the turn's own `sales_channel`. Narrowing that report keeps it."""
    from tests.chatbot.test_sales_report_lane import SALES_REPORT_HIT

    hit = {**SALES_REPORT_HIT, "customer_name": lane.CUSTOMER_NAME, "channel": "dealer", "channel_shown": True,
           "date_from": "2026-09-01", "date_to": "2026-09-30"}
    seen: list[tuple[str, dict[str, Any]]] = []
    real_factory = lane._REAL_FETCH_SERVICES

    def _factory(*, mcp_call):
        def _route(name: str, args: dict[str, Any]) -> Any:
            if name == "crm_sales_report":
                seen.append((name, dict(args)))
                return _present(hit if len(seen) < 3 else EMPTY_REPORT)
            return mcp_call(name, args)

        return real_factory(mcp_call=_route)

    monkeypatch.setattr(lane, "FetchServices", _factory)
    console.say(_sales(sales_channel="dealer"), "dealer sales report for the customer")
    pick = _parser_output(
        message_type="casual", intent_hint=None, domain_hint=None, entities=[], reference_positions=[1],
    )
    console.say(pick, "1")  # the detail offer's re-run
    assert len(seen) == 2 and seen[1][1].get("channel") == "dealer", seen  # precondition: channel carried
    console.say(_narrow(), "only this one")
    assert len(seen) == 3, seen
    assert seen[2][1].get("product_code") == EXACT, seen[2]
    assert seen[2][1].get("channel") == "dealer", seen[2]
