"""Phase 2 RED tests - REPORTS-STEP2 S3a, the lane halves: AC-29 (two locations), AC-30 (the DO list asked
in other words), AC-31 (not-supported topics), AC-32 (a compound message answers both halves).

Reuses the S5a harness (a fake MCP client behind the real `services._mcp_call`), so the recorded calls are
`crm_report` calls and the replies are the real presenter's. The parser is a stub: each test pins the
emission shape it needs, and the lane has to act on the emission, never on the message text.
Postgres only (blank schema), fake data.
"""
from __future__ import annotations

import copy
from typing import Any

import pytest

from tests.chatbot import test_reports_step2_s5a as s5a
from tests.chatbot.test_engine import _parser_output
from tests.chatbot.test_outstanding_lane import (
    CUSTOMER_NAME,
    CUSTOMER_UUID,
    PRODUCT_CODE,
    PRODUCT_UUID,
    REPORT_HIT,
    _qf,
    _run_turn,
    _seed_contact,
    _session_of,
)
from tests.chatbot.test_reports_step2_s5a import _harness  # noqa: F401  (autouse fixture by name)
from tests.chatbot.test_sales_report_drill_lane import _typed
from tests.reports_step2.test_s3a_shapes import COPY

TWO_LOCATIONS = {
    **REPORT_HIT,
    "warehouse_codes": ["BRW-IB", "BRW-AM"],
    "so_by_location": [
        {"code": "BRW-IB", "ordered_qty": 10, "outstanding_qty": 7},
        {"code": "BRW-AM", "ordered_qty": 4, "outstanding_qty": 2},
    ],
    "do_by_location": [
        {"code": "BRW-IB", "do_qty": 7, "pending_qty": 7},
        {"code": "BRW-AM", "do_qty": 2, "pending_qty": 2},
    ],
}


def _seed_two_warehouses(session_factory) -> None:
    import uuid

    from app.models.inventory import Warehouse

    db = session_factory()
    db.add_all([Warehouse(id=str(uuid.uuid4()), warehouse_code=c, warehouse_name=c, is_active=True)
                for c in ("BRW-IB", "BRW-AM")])
    db.commit()


def _reply(result) -> str:
    return (result.reply or {}).get("text") or ""


# ------------------------------------------------------------------ AC-29


def test_ac29_an_outstanding_ask_naming_two_locations_sends_both_and_shows_both(session_factory, monkeypatch):
    _seed_contact(session_factory, variables={})
    _seed_two_warehouses(session_factory)
    entities = [
        {"raw": PRODUCT_CODE, "hint": "product", "canonical_code": None, "current_message": True, "confident": True},
        {"raw": "BRW-IB", "hint": "warehouse", "canonical_code": None, "current_message": True, "confident": True},
        {"raw": "BRW-AM", "hint": "warehouse", "canonical_code": None, "current_message": True, "confident": True},
    ]
    result, calls = _run_turn(
        session_factory, monkeypatch, qf=_qf(order_status="so_outstanding", entities=entities),
        text_body="SRTWT7445 sales order outstanding for location BRW-IB & BRW-AM", msg_id="ZZT-s3a-29",
        attributes=s5a.OUT_ATTRS, matches=s5a.PRODUCT_MATCH, mcp_response=TWO_LOCATIONS)
    (name, args), = [c for c in calls if c[0] == "crm_report"]
    assert sorted(args.get("warehouse_codes") or []) == ["BRW-AM", "BRW-IB"], args
    text = _reply(result)
    assert "BRW-IB" in text and "BRW-AM" in text, text


# ------------------------------------------------------------------ AC-30


def test_ac30_the_do_list_asked_in_other_words_returns_the_do_rows_of_the_held_report(session_factory, monkeypatch):
    """The held report offered "Sales order list / Delivery order list"; the customer types the second
    label back. The parser reads it as an entity reusing the open offer (the shape the sales drill uses);
    the lane re-runs the SAME report with detail do, never a fresh summary."""
    s5a._seed_open_outstanding_detail(session_factory)
    _result, calls = _run_turn(
        session_factory, monkeypatch,
        qf=_parser_output(
            message_type="casual", intent_hint=None, domain_hint=None,
            entities=[{"raw": "Delivery order list", "hint": "order", "canonical_code": None,
                       "current_message": True, "confident": True}],
            reference_positions=[], entity_op="reuse"),
        text_body="Delivery order list", msg_id="ZZT-s3a-30", attributes=s5a.OUT_ATTRS,
        matches=s5a.PRODUCT_MATCH, mcp_response=REPORT_HIT)
    sent = [c for c in calls if c[0] == "crm_report"]
    assert sent, calls
    assert sent[0][1].get("detail") == "do", sent[0][1]
    assert sent[0][1].get("product_code") == PRODUCT_CODE, sent[0][1]


def test_ac30_a_do_list_ask_with_no_entity_under_the_offer_returns_the_do_rows_of_the_held_report(
    session_factory, monkeypatch
):
    """The other emission a parser can give "Delivery order list" under the open offer: a fresh
    `do_outstanding` outstanding ask that names no product (the held report is the subject). The lane
    must run the HELD report with detail do, never a new summary of nothing."""
    s5a._seed_open_outstanding_detail(session_factory)
    _result, calls = _run_turn(
        session_factory, monkeypatch, qf=_qf(order_status="do_outstanding", entities=[]),
        text_body="Delivery order list", msg_id="ZZT-s3a-30b", attributes=s5a.OUT_ATTRS,
        matches=s5a.PRODUCT_MATCH, mcp_response=REPORT_HIT)
    sent = [c for c in calls if c[0] == "crm_report"]
    assert sent, calls
    assert sent[0][1].get("detail") == "do" and sent[0][1].get("product_code") == PRODUCT_CODE, sent[0][1]


# ------------------------------------------------------------------ AC-31


def _unsupported(domain: str):
    return _parser_output(message_type="business_query", intent_hint=None, domain_hint=domain, entities=[],
                          escalation={"is_escalation_confirmation": False, "company_pick": None})


@pytest.mark.parametrize(
    "domain, key",
    [("receivables", "not_supported_receivables"), ("dealer_locator", "not_supported_dealer_locator")],
)
@pytest.mark.parametrize("language, index", [(None, 0), ("zh", 1), ("ms", 2)])
def test_ac31_an_unsupported_topic_gets_its_own_line_in_the_turn_language(
    session_factory, monkeypatch, domain, key, language, index
):
    """Pinned emission: `message_type: business_query`, `domain_hint` one of `receivables` (overdue,
    payment balance, statement) or `dealer_locator` (nearby dealer); both are unsupported domains, so the
    turn is a `not_supported` answer with that topic's line and no tool call, no figures."""
    _seed_contact(session_factory, variables={"reply_language": language} if language else {})
    result, calls = _run_turn(
        session_factory, monkeypatch, qf=_unsupported(domain), text_body="overdue balance?",
        msg_id=f"ZZT-s3a-31-{domain}-{language}", attributes=[], mcp_response=REPORT_HIT)
    assert calls == [], calls
    assert _reply(result).strip() == COPY[key][index], _reply(result)


# ------------------------------------------------------------------ AC-32

CHEAP = "CB88SS-DIY"


def test_ac32_a_compound_ask_answers_both_halves_and_filters_the_outstanding_by_the_customer(
    session_factory, monkeypatch
):
    """Pinned emission: `domain_hint` null, `asks` = inventory + order, `order_status` outstanding, one
    product entity (the typed code) and one customer entity. Both halves run; the outstanding half
    carries the customer (a resolved `customer_ids`) and the one product the customer typed."""
    _seed_contact(session_factory, variables={})
    entities = [
        {"raw": CHEAP, "hint": "product", "canonical_code": None, "current_message": True, "confident": True},
        {"raw": CUSTOMER_NAME, "hint": "customer", "canonical_code": None, "current_message": True,
         "confident": True},
    ]
    qf = _qf(order_status="outstanding", entities=entities, domain_hint=None, intent_hint="check_stock",
             asks=[{"domain": "inventory"}, {"domain": "order"}])
    matches = {
        CHEAP: {"uuid": PRODUCT_UUID, "entity_type": "product", "canonical_code": CHEAP},
        CUSTOMER_NAME: {"uuid": CUSTOMER_UUID, "entity_type": "customer", "canonical_code": CUSTOMER_NAME},
    }
    result, calls = _run_turn(
        session_factory, monkeypatch, qf=qf, text_body="Cb88ss/diy got stock? got outstanding for Dealer A",
        msg_id="ZZT-s3a-32", attributes=s5a.OUT_ATTRS, matches=matches, mcp_response=REPORT_HIT)
    names = [n for n, _a in calls]
    assert "crm_report" in names, names
    report_args = next(a for n, a in calls if n == "crm_report")
    assert report_args.get("customer_ids") == [CUSTOMER_UUID], report_args
    assert report_args.get("product_code") == CHEAP, report_args
    assert any(n.startswith("crm_inventory") for n in names), ("the stock half did not run", names)
