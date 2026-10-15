"""Phase 2 RED tests - chat fix PR-B S5 (AC-B7): a sales total whose parse carries a brand is
filtered to that brand (R15 dealer: "Mocha" entity, hint brand, confident), never silently
unfiltered.  Lane half here; route half in tests/test_sales_report_brand_prb.py; MCP catalog
half in sorento_crm_mcp/tests/test_catalog_sales_report_brand_prb.py."""
from __future__ import annotations

import pytest

from app.services.chatbot.lanes.business.services import FetchServices
from tests.chatbot.test_outstanding_lane import (
    CUSTOMER_NAME, CUSTOMER_UUID, _capturing_mcp, _qf, _run_turn, _seed_contact,
)
from tests.chatbot.test_sales_report_lane import SALES_REPORT_HIT

BRAND_UUID = "bbbbbbbb-1111-4000-8000-0000000000b1"
OTHER_BRAND_UUID = "bbbbbbbb-1111-4000-8000-0000000000b2"
GRANT = "sales_orders.sales_report"


def _payload(entities, parse_entities=()):
    return {
        "gate": {"compatible_entities": entities},
        "tier_gate": None,
        "ctx": {
            "parse": {"output": _qf(order_status="sales_report", entities=list(parse_entities))},
            "contact": {"id": 900000008},
            "access": {"attributes": [GRANT]},
        },
    }


def _run(entities, parse_entities=()):
    from app.services.chatbot.lanes.business import run_fetch

    call, captured = _capturing_mcp(SALES_REPORT_HIT)
    run_fetch(_payload(entities, parse_entities), services=FetchServices(mcp_call=call))
    assert captured, "no MCP tool was called"
    assert captured[0][0] == "crm_sales_report", captured
    return captured[0][1]


CUSTOMER = {"uuid": CUSTOMER_UUID, "entity_type": "customer", "canonical_code": CUSTOMER_NAME}
BRAND = {"uuid": BRAND_UUID, "entity_type": "brand", "canonical_code": "MOCHA"}


def test_b7_a_resolved_brand_rides_as_brand_ids() -> None:
    args = _run([CUSTOMER, BRAND])
    assert args.get("brand_ids") == [BRAND_UUID], args
    assert CUSTOMER_UUID in str(args.get("customer_ids")), args


def test_b7_two_brands_travel_together() -> None:
    args = _run([CUSTOMER, BRAND, {**BRAND, "uuid": OTHER_BRAND_UUID, "canonical_code": "SORENTO"}])
    assert sorted(args.get("brand_ids") or []) == sorted([BRAND_UUID, OTHER_BRAND_UUID]), args


def test_b7_near_miss_no_brand_entity_sends_no_brand_param() -> None:
    args = _run([CUSTOMER])
    assert "brand_ids" not in args, args


def test_b7_near_miss_a_customer_entity_is_never_sent_as_a_brand() -> None:
    args = _run([CUSTOMER])
    assert not args.get("brand_ids")


def test_b7_a_whole_turn_with_a_confident_brand_entity_calls_the_report_with_the_brand(
    session_factory, monkeypatch
) -> None:
    _seed_contact(session_factory, variables={})
    entities = [
        {"raw": "ZZT Customer", "hint": "customer", "canonical_code": None, "current_message": True, "confident": True},
        {"raw": "Mocha", "hint": "brand", "canonical_code": "MOCHA", "current_message": True, "confident": True},
    ]
    _result, captured = _run_turn(
        session_factory, monkeypatch,
        qf=_qf(order_status="sales_report", entities=entities),
        text_body="sales for mocha this month",
        msg_id="ZZT-b7-brand-turn",
        attributes=[GRANT],
        matches={
            "ZZT Customer": {"uuid": CUSTOMER_UUID, "entity_type": "customer", "canonical_code": CUSTOMER_NAME},
            "Mocha": BRAND,
        },
        mcp_response=SALES_REPORT_HIT,
    )
    calls = [a for n, a in captured if n == "crm_sales_report"]
    assert len(calls) == 1, captured
    assert calls[0].get("brand_ids") == [BRAND_UUID], calls
