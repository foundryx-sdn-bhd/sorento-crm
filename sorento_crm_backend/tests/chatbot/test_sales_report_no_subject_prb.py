"""Phase 2 RED tests - chat fix PR-B S10 (AC-B16): a sales report parse with no product and no
customer never goes to `crm_sales_report` (422 `subject_required`, "I could not fetch sales
just now", live R03/R23). A STAFF contact gets the company sales analysis for the period; a
customer-scoped contact keeps its links as the subject."""
from __future__ import annotations

import pytest

from tests.chatbot.test_customer_scope_lane import (
    HANLIM_CODES, HANLIM_LINKS, SALES_KEY, _calls, _link_customers, _run,
)
from tests.chatbot.test_outstanding_lane import PRODUCT_CODE, PRODUCT_UUID, _qf, _seed_contact
from tests.chatbot.test_sales_analysis_lane import ROUTE_HIT, _rendered
from tests.chatbot.test_sales_report_lane import SALES_REPORT_HIT

PERIOD = dict(date_filter_start="2026-10-01", date_filter_end="2026-10-31")


def _no_subject(**over):
    return _qf(**{"order_status": "sales_report", "entities": [], "self_reference": False, **PERIOD, **over})


def test_b16_a_staff_sales_report_with_no_subject_runs_the_sales_analysis(session_factory, monkeypatch) -> None:
    _seed_contact(session_factory, variables={})
    _res, reply, captured = _run(
        session_factory, monkeypatch, _no_subject(), "sales report this month", mcp_response=_rendered(ROUTE_HIT)
    )
    assert _calls(captured, "crm_sales_report") == [], captured
    (args,) = _calls(captured, "crm_sales_analysis")
    assert args["date_from"] == "2026-10-01" and args["date_to"] == "2026-10-31", args
    assert "could not fetch" not in reply.lower(), reply


def test_b16_a_staff_sales_report_with_a_channel_keeps_it_on_the_analysis(session_factory, monkeypatch) -> None:
    _seed_contact(session_factory, variables={})
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
