"""Phase 2 RED tests - chat fix PR-B S10 (AC-B16): a sales report parse with no product and no
customer never goes to `crm_sales_report` (422 `subject_required`, "I could not fetch sales
just now", live R03/R23). A STAFF contact gets the company sales analysis for the period; a
customer-scoped contact keeps its links as the subject."""
from __future__ import annotations

import pytest

from tests.chatbot.test_customer_scope_lane import (
    HANLIM_CODES, HANLIM_LINKS, SALES_KEY, _calls, _give_access_type, _link_customers, _run,
)
from tests.chatbot.test_outstanding_lane import PRODUCT_CODE, PRODUCT_UUID, _qf, _seed_contact
from tests.chatbot.test_sales_analysis_lane import ROUTE_HIT, _rendered
from tests.chatbot.test_sales_report_lane import SALES_REPORT_HIT

PERIOD = dict(date_filter_start="2026-10-01", date_filter_end="2026-10-31")


def _no_subject(**over):
    return _qf(**{"order_status": "sales_report", "entities": [], "self_reference": False, **PERIOD, **over})


def test_b16_a_staff_sales_report_with_no_subject_runs_the_sales_analysis(session_factory, monkeypatch) -> None:
    _seed_contact(session_factory, variables={})
    _give_access_type(session_factory, "Sorento Office")
    _res, reply, captured = _run(
        session_factory, monkeypatch, _no_subject(), "sales report this month", mcp_response=_rendered(ROUTE_HIT)
    )
    assert _calls(captured, "crm_sales_report") == [], captured
    (args,) = _calls(captured, "crm_sales_analysis")
    assert args["date_from"] == "2026-10-01" and args["date_to"] == "2026-10-31", args
    assert "could not fetch" not in reply.lower(), reply


def test_b16_a_staff_sales_report_with_a_channel_keeps_it_on_the_analysis(session_factory, monkeypatch) -> None:
    _seed_contact(session_factory, variables={})
    _give_access_type(session_factory, "Sorento Office")
    _res, _reply, captured = _run(
        session_factory, monkeypatch, _no_subject(sales_channel="dealer"), "dealer sales this month",
        mcp_response=_rendered(ROUTE_HIT),
    )
    (args,) = _calls(captured, "crm_sales_analysis")
    assert args["channel"] == "dealer", args


def test_b16_near_miss_a_product_subject_still_runs_the_sales_report(session_factory, monkeypatch) -> None:
    _seed_contact(session_factory, variables={})
    qf = _qf(order_status="sales_report", self_reference=False, **PERIOD, entities=[
        {"raw": PRODUCT_CODE, "hint": "product", "canonical_code": None, "current_message": True, "confident": True}])
    _res, _reply, captured = _run(
        session_factory, monkeypatch, qf, f"sales for {PRODUCT_CODE}", mcp_response=SALES_REPORT_HIT,
        matches={PRODUCT_CODE: {"uuid": PRODUCT_UUID, "entity_type": "product", "canonical_code": PRODUCT_CODE}},
    )
    assert len(_calls(captured, "crm_sales_report")) == 1 and _calls(captured, "crm_sales_analysis") == [], captured


def test_b16_near_miss_a_customer_scoped_contact_keeps_its_links_as_the_subject(session_factory, monkeypatch) -> None:
    _seed_contact(session_factory, variables={})
    links = _link_customers(session_factory, *HANLIM_LINKS, codes=HANLIM_CODES)
    _res, _reply, captured = _run(
        session_factory, monkeypatch, _no_subject(self_reference=True), "sales this month",
        mcp_response=SALES_REPORT_HIT,
    )
    assert _calls(captured, "crm_sales_analysis") == [], captured
    (args,) = _calls(captured, "crm_sales_report")
    assert args["customer_ids"] == links, args


def test_b16_security_a_non_staff_contact_with_no_links_is_never_rerouted_to_the_company_analysis(
    session_factory, monkeypatch
) -> None:
    """Review: the reroute gates on 'is office staff', not on 'has no customer links'. A contact
    with no office access type and no links keeps today's behaviour: never the company totals."""
    _seed_contact(session_factory, variables={})
    import json

    from sorento_crm_mcp.http_client import http_error_body

    # Today's real answer to a subject-less crm_sales_report: HTTP 422 subject_required.
    err = http_error_body(
        json.dumps({"message": "A product or a customer is required", "code": "subject_required"}),
        status_code=422, path="/api/v1/order-management/sales-report", method="GET",
    )
    _res, reply, captured = _run(
        session_factory, monkeypatch, _no_subject(), "sales report this month", mcp_response=err
    )
    assert _calls(captured, "crm_sales_analysis") == [], captured
    assert "Project team: RM" not in reply, reply  # never the company totals


def test_b16_a_staff_brand_only_sales_never_ends_in_could_not_fetch(session_factory, monkeypatch) -> None:
    """Review: staff 'sales for <brand> this month' (no product, no customer) must not send
    `crm_sales_report` with brand_ids alone (the route answers 422 subject_required)."""
    import uuid

    from app.models.product import Brand
    from app.services.company_scope import DEFAULT_COMPANY_ID

    _seed_contact(session_factory, variables={})
    _give_access_type(session_factory, "Sorento Office")
    db = session_factory()
    db.add(Brand(id=str(uuid.uuid4()), brand_code="ZZTMOCHA", brand_name="Mocha", company_id=DEFAULT_COMPANY_ID))
    db.commit()
    db.close()
    qf = _no_subject(entities=[{"raw": "Mocha", "hint": "brand", "canonical_code": "MOCHA",
                                "current_message": True, "confident": True}])
    _res, reply, captured = _run(
        session_factory, monkeypatch, qf, "sales for mocha this month", mcp_response=_rendered(ROUTE_HIT)
    )
    for args in _calls(captured, "crm_sales_report"):
        assert args.get("customer_ids") or args.get("product_code") or args.get("customer_query"), (
            f"a subject-less crm_sales_report the route will 422: {args}"
        )
    assert "could not fetch" not in reply.lower() and "just now" not in reply.lower(), reply


def test_r23_guard_a_carried_product_subject_rides_in_the_args_of_a_dealer_only_refinement(session_factory, monkeypatch) -> None:
    """GREEN GUARD (could not reproduce the live args-only-channel call on HEAD with a linked dealer, a
    staff or an unlinked contact: the product_code rides when the turn stays on crm_sales_report). Live replay R23: after a product ask, 'dealer only' (sales_analysis, ranking_refine,
    sales_channel dealer) stays on crm_sales_report because of the carried product, so the
    product must be in the args or the route answers 422 subject_required."""
    _seed_contact(session_factory, variables={})
    _link_customers(session_factory, *HANLIM_LINKS[:1], codes=HANLIM_CODES)  # a dealer contact
    first = _qf(order_status="sales_report", self_reference=False, entities=[
        {"raw": PRODUCT_CODE, "hint": "product", "canonical_code": None, "current_message": True, "confident": True}])
    _run(session_factory, monkeypatch, first, f"sales for {PRODUCT_CODE}", mcp_response=SALES_REPORT_HIT,
         matches={PRODUCT_CODE: {"uuid": PRODUCT_UUID, "entity_type": "product", "canonical_code": PRODUCT_CODE}})
    second = _qf(order_status="sales_analysis", entities=[], ranking_refine=True, sales_channel="dealer",
                 self_reference=False)
    _res, _reply, captured = _run(session_factory, monkeypatch, second, "dealer only", mcp_response=SALES_REPORT_HIT)
    calls = _calls(captured, "crm_sales_report")
    assert len(calls) == 1, captured
    assert calls[0].get("channel") == "dealer", calls
    assert calls[0].get("product_code") == PRODUCT_CODE, calls

