"""PARSER-PER-AUDIENCE, AC-PA-11, AC-PA-12, AC-PA-13: the refusal half of the leak matrix.

Hiding a block from the parser prompt is a size change, never the gate (PLAN design 7).
This file proves the gate: a real `engine.run_turn` per case, the parser output FORCED
(`_run_turn`'s `qf=`), the contact's grants set through `attributes=`. For each audience
(the 16 subsets of {C, P, S, L} plus the unidentified contact, who holds `[]`) and each ask
below, a contact that lacks the ask's grant must get the refusal, with no restricted tool
in the captured MCP calls and none of the restricted fixture's values in the reply; a
contact that holds it must reach the tool (the positive control, so a gate that refuses
everyone cannot pass).

Full 17 audiences x every ask, not the reduced per-grant matrix: each case is one turn on a
blank-schema Postgres.

Expected state against the #1405 head (measured when written, see the report): cost and
the three sales statuses are ALREADY enforced and go green; low stock runs no tool but
answers a miss line, not the refusal; the direct `purchase_order` ask and the
`spo_allocation` ask (G2) are answered today, so their not-held cases are red until both
join the refusal table. The G1 case (an unlinked non-office
contact reads any customer's orders) is in `test_customer_scope_unlinked_fail_closed.py`.

No em or en dashes.
"""
from __future__ import annotations

import itertools
import uuid
from typing import Any

import pytest

from app.models.chatbot_turn import ChatbotTurn
from tests.chatbot.test_crossdomain_ladder import (
    _INCOMING_TOOL,
    _LADDER_WITH_PO,
    _PO_TOOL,
    _po_row,
    _run as _run_ladder,
)
from tests.chatbot.test_customer_scope_lane import (
    ORDERS,
    OUTSTANDING_KEY,
    OWN_A,
    REPORT,
    SALES,
    SALES_KEY,
    _ask,
    _calls,
    _ent,
    _hanlim_services,
    _link_customers,
    _spied,
    _turn,
    refusal,
)
from tests.chatbot.test_low_stock_lane import READY_ENVELOPE as LOW_STOCK_ENVELOPE
from tests.chatbot.test_outstanding_lane import (
    PRODUCT_CODE,
    PRODUCT_UUID,
    _qf,
    _run_turn,
    _seed_contact,
)
from tests.chatbot.test_sales_analysis_lane import _rendered as SALES_ANALYSIS_HIT
from tests.chatbot.test_sales_report_lane import SALES_REPORT_DENIAL, SALES_REPORT_HIT

C = "purchase_orders.cost"
P = "purchase_orders.placed"
S = "sales_orders.sales_report"
L = "scm.low_stock_report"
ALL = (C, P, S, L)
_LETTER = {C: "C", P: "P", S: "S", L: "L"}

COST_REFUSAL = "Sorry, you are not allowed to access purchase cost"
PO_REFUSAL_FRAGMENT = "not allowed to access purchase orders"
LOW_STOCK_REFUSAL = "Low stock report is not enabled for your account."

COST_TOOL = "crm_procurement_po_last_cost_list"
PO_TOOL = "crm_procurement_po_placed_list"

SUPPLIER = "ZZT Acme Supplies"
PO_NUMBER = "PO-2026/09-0013"
PO_ROW = {
    "items": [
        {
            "fields": [
                {"key": "po_number", "label": "PO Number", "value": PO_NUMBER},
                {"key": "product_code", "label": "Product Code", "value": PRODUCT_CODE},
                {"key": "po_quantity", "label": "PO Quantity", "value": 19},
                {"key": "unit_cost", "label": "Cost / unit", "value": "CNY 110.00"},
                {"key": "supplier", "label": "Supplier", "value": SUPPLIER},
            ],
        }
    ],
    "has_result": True,
    "intro": "Here is the last purchase cost per product and location.",
    "restricted_fields": {
        "unit_cost": "purchase_orders.cost",
        "supplier": "purchase_orders.supplier",
    },
}
PRODUCT_MATCH = {
    PRODUCT_CODE: {"uuid": PRODUCT_UUID, "entity_type": "product", "canonical_code": PRODUCT_CODE}
}


def _subset_id(subset: tuple[str, ...]) -> str:
    return "+".join(_LETTER[g] for g in subset) or "none"


AUDIENCES = [
    pytest.param(list(s), id=_subset_id(s))
    for n in range(len(ALL) + 1)
    for s in itertools.combinations(ALL, n)
] + [pytest.param([], id="unidentified")]


def _turn_for(session_factory, monkeypatch, *, qf, attributes, body, **kw):
    _seed_contact(session_factory, variables={})
    result, captured = _run_turn(
        session_factory, monkeypatch, qf=qf, text_body=body,
        msg_id=f"ZZT-pa-{uuid.uuid4().hex[:12]}", attributes=list(attributes), **kw,
    )
    reply = (result.reply or {}).get("text") or ""
    return result, reply, captured


def _names(captured) -> list[str]:
    return [name for name, _args in captured]


# --------------------------------------------------------------------------- #
# Cost (C): domain purchase_cost
# --------------------------------------------------------------------------- #


class TestCostMatrix:
    @pytest.mark.parametrize("grants", AUDIENCES)
    def test_purchase_cost_ask(self, session_factory, monkeypatch, grants) -> None:
        qf = _qf(domain_hint="purchase_cost", intent_hint="check_po_cost")
        _result, reply, captured = _turn_for(
            session_factory, monkeypatch, qf=qf, attributes=grants,
            body=f"last purchase cost for {PRODUCT_CODE}", matches=PRODUCT_MATCH, mcp_response=PO_ROW,
        )
        if C in grants:
            assert COST_TOOL in _names(captured), captured
            return
        assert reply.strip() == COST_REFUSAL, reply
        assert captured == [], captured
        assert "CNY" not in reply and "Cost / unit" not in reply, reply


# --------------------------------------------------------------------------- #
# Placed (P): domain purchase_order, and the stock ask that climbs to the PO rung
# --------------------------------------------------------------------------- #


class TestPurchaseOrderMatrix:
    @pytest.mark.parametrize("grants", AUDIENCES)
    def test_direct_purchase_order_ask(self, session_factory, monkeypatch, grants) -> None:
        qf = _qf(domain_hint="purchase_order", intent_hint="check_po")
        result, reply, captured = _turn_for(
            session_factory, monkeypatch, qf=qf, attributes=grants,
            body=f"PO for {PRODUCT_CODE}", matches=PRODUCT_MATCH, mcp_response=PO_ROW,
        )
        if P in grants:
            assert PO_TOOL in _names(captured), captured
            return
        assert PO_TOOL not in _names(captured), captured
        assert captured == [], captured
        assert PO_REFUSAL_FRAGMENT in reply, reply
        assert result.status == "done", result.error
        assert PO_NUMBER not in reply, reply
        trace = session_factory().query(ChatbotTurn).filter(ChatbotTurn.id == result.turn_id).first().trace or []
        assert any(
            isinstance(e, dict) and e.get("skipped") == "not_granted" and e.get("needs") == P
            for e in trace
        ), "the refusal must be recorded as a not_granted skip on the trace"

    @pytest.mark.parametrize("grants", AUDIENCES)
    def test_the_stock_ask_rung_is_probed_only_with_the_grant(self, grants) -> None:
        _result, calls = _run_ladder(
            ladder=_LADDER_WITH_PO,
            incoming_response={"answers": [], "has_result": False},
            po_response={"answers": [_po_row(50, "2026-07-01")], "has_result": True},
            granted=list(grants),
        )
        names = [name for name, _args in calls]
        assert (_PO_TOOL in names) == (P in grants), (grants, names)
        assert _INCOMING_TOOL in names


# --------------------------------------------------------------------------- #
# Sales (S): the three figure statuses
# --------------------------------------------------------------------------- #

_SALES_ASKS = [
    pytest.param(
        "sales_report", "crm_sales_report", SALES_REPORT_HIT, "sales report for SRTWT7445",
        id="sales_report",
    ),
    pytest.param(
        "sales_analysis", "crm_sales_analysis", SALES_ANALYSIS_HIT(), "total sales this year",
        id="sales_analysis",
    ),
    pytest.param(
        "top_selling", "crm_top_selling_report", None, "top 5 selling items by quantity",
        id="top_selling",
    ),
]


class TestSalesMatrix:
    @pytest.mark.parametrize("grants", AUDIENCES)
    @pytest.mark.parametrize("status,tool,hit,body", _SALES_ASKS)
    def test_sales_figure_ask(self, session_factory, monkeypatch, grants, status, tool, hit, body) -> None:
        entities = _qf()["entities"] if status == "sales_report" else []
        extra = (
            {"rank_by": "quantity", "basis": None, "rank_group": None, "top_n": 5}
            if status == "top_selling"
            else {}
        )
        qf = _qf(order_status=status, entities=entities, **extra)
        _result, reply, captured = _turn_for(
            session_factory, monkeypatch, qf=qf, attributes=grants, body=body,
            matches=PRODUCT_MATCH, mcp_response=hit,
        )
        if S in grants:
            assert tool in _names(captured), captured
            return
        assert reply.strip() == SALES_REPORT_DENIAL, reply
        assert captured == [], captured


# --------------------------------------------------------------------------- #
# Low stock (L): intent low_stock_report
# --------------------------------------------------------------------------- #


class TestLowStockMatrix:
    """Measured against the #1405 head: without the grant no tool runs and no file is sent
    (the control holds), but the reply is the miss composer's "Could not find inventory"
    line, not the refusal `run_fetch` builds (`lanes/business/__init__.py:1526-1527`). The
    UAC table names the refusal as the reply, so that half is its own test."""

    def _ask(self, session_factory, monkeypatch, grants):
        qf = _qf(
            domain_hint="inventory", intent_hint="low_stock_report", entities=[],
            low_stock={"all_categories": True, "categories": [], "brands": [], "suppliers": [], "group_by": None},
        )
        return _turn_for(
            session_factory, monkeypatch, qf=qf, attributes=grants,
            body="low stock report", mcp_response=LOW_STOCK_ENVELOPE,
        )

    @pytest.mark.parametrize("grants", AUDIENCES)
    def test_the_tool_runs_only_with_the_grant(self, session_factory, monkeypatch, grants) -> None:
        result, reply, captured = self._ask(session_factory, monkeypatch, grants)
        if L in grants:
            assert "crm_low_stock_report" in _names(captured), captured
            return
        assert captured == [], captured
        assert "low-stock-10092026" not in str(result.actions), result.actions
        assert "Low: 12 of 340" not in reply, reply

    @pytest.mark.parametrize("grants", [g for g in AUDIENCES if L not in g.values[0]])
    def test_the_reply_is_the_refusal_without_the_grant(self, session_factory, monkeypatch, grants) -> None:
        _result, reply, _captured = self._ask(session_factory, monkeypatch, grants)
        assert reply.strip().startswith(LOW_STOCK_REFUSAL), reply


# --------------------------------------------------------------------------- #
# G2: spo_allocation ("last in") rides the placed grant
# --------------------------------------------------------------------------- #

SPO_TOOL = "crm_procurement_spo_allocations_last_receipt_list"
SPO_ROW = {
    "items": [
        {
            "fields": [
                {"key": "spo_number", "label": "SPO Number", "value": "SPO-ZZT-0042"},
                {"key": "product_code", "label": "Product Code", "value": PRODUCT_CODE},
                {"key": "quantity", "label": "Quantity", "value": 120},
            ],
        }
    ],
    "has_result": True,
    "intro": "Here is the last receipt.",
}


class TestSpoAllocationMatrix:
    @pytest.mark.parametrize("grants", AUDIENCES)
    def test_last_in_ask(self, session_factory, monkeypatch, grants) -> None:
        qf = _qf(domain_hint="spo_allocation", intent_hint="check_spo")
        result, reply, captured = _turn_for(
            session_factory, monkeypatch, qf=qf, attributes=grants,
            body=f"last in for {PRODUCT_CODE}", matches=PRODUCT_MATCH, mcp_response=SPO_ROW,
        )
        if P in grants:
            assert SPO_TOOL in _names(captured), captured
            return
        assert SPO_TOOL not in _names(captured), captured
        assert captured == [], captured
        assert "not allowed to access" in reply, reply
        assert "SPO-ZZT-0042" not in reply, reply
        assert result.status == "done", result.error


# --------------------------------------------------------------------------- #
# AC-PA-12: supplier has its own grant, independent of cost and placed
# --------------------------------------------------------------------------- #


class TestSupplierNeverRidesAlongOnCostOrPlaced:
    @pytest.mark.parametrize("held", [[C], [P], [C, P], [C, P, S, L]], ids=["C", "P", "C+P", "all-four"])
    @pytest.mark.parametrize(
        "domain,intent,tool", [("purchase_cost", "check_po_cost", COST_TOOL), ("purchase_order", "check_po", PO_TOOL)]
    )
    def test_no_supplier_value_without_the_supplier_grant(
        self, session_factory, monkeypatch, held, domain, intent, tool
    ) -> None:
        grants_needed = C if domain == "purchase_cost" else P
        if grants_needed not in held:
            pytest.skip("the domain itself is refused for this audience; covered by the matrices above")
        _result, reply, captured = _turn_for(
            session_factory, monkeypatch, qf=_qf(domain_hint=domain, intent_hint=intent),
            attributes=held, body=f"{domain} for {PRODUCT_CODE}", matches=PRODUCT_MATCH,
            mcp_response=PO_ROW,
        )
        assert tool in _names(captured), captured
        assert SUPPLIER not in reply, reply

    def test_the_supplier_grant_does_show_it(self, session_factory, monkeypatch) -> None:
        _result, reply, captured = _turn_for(
            session_factory, monkeypatch, qf=_qf(domain_hint="purchase_cost", intent_hint="check_po_cost"),
            attributes=[C, "purchase_orders.supplier"], body=f"last purchase cost for {PRODUCT_CODE}",
            matches=PRODUCT_MATCH, mcp_response=PO_ROW,
        )
        assert COST_TOOL in _names(captured), captured
        assert SUPPLIER in reply, reply


# --------------------------------------------------------------------------- #
# AC-PA-13: other customers, whatever the audience
# --------------------------------------------------------------------------- #

_EVERY_GRANT = list(ALL)


class TestOtherCustomersAreRefused:
    @pytest.mark.parametrize("extra", [[], _EVERY_GRANT], ids=["own-grants-only", "all-four-too"])
    def test_outstanding_for_another_customer(self, session_factory, monkeypatch, extra) -> None:
        _seed_contact(session_factory, variables={})
        _link_customers(session_factory, OWN_A)
        services, _looked_up = _spied(_hanlim_services())
        reply, captured = _turn(
            session_factory, monkeypatch, _ask([_ent("hanlim")]), attributes=(OUTSTANDING_KEY, *extra),
            resolve_services=services,
        )
        assert reply.strip() == refusal(OWN_A), reply
        assert _calls(captured, REPORT) == [] and captured == [], captured

    @pytest.mark.parametrize("extra", [[], _EVERY_GRANT], ids=["own-grants-only", "all-four-too"])
    def test_orders_of_another_customer(self, session_factory, monkeypatch, extra) -> None:
        _seed_contact(session_factory, variables={})
        _link_customers(session_factory, OWN_A)
        services, _looked_up = _spied(_hanlim_services())
        reply, captured = _turn(
            session_factory, monkeypatch, _ask([_ent("hanlim")], order_status="all"),
            "orders for hanlim", attributes=tuple(extra), resolve_services=services,
        )
        assert reply.strip() == refusal(OWN_A), reply
        assert _calls(captured, ORDERS) == [] and captured == [], captured

    @pytest.mark.parametrize("extra", [[], _EVERY_GRANT], ids=["own-grants-only", "all-four-too"])
    def test_sales_report_of_another_customer(self, session_factory, monkeypatch, extra) -> None:
        _seed_contact(session_factory, variables={})
        _link_customers(session_factory, OWN_A)
        services, _looked_up = _spied(_hanlim_services())
        reply, captured = _turn(
            session_factory, monkeypatch, _ask([_ent("hanlim")], order_status="sales_report"),
            "sales report for hanlim", attributes=(SALES_KEY, *extra), resolve_services=services,
        )
        assert reply.strip() == refusal(OWN_A), reply
        assert _calls(captured, SALES) == [] and captured == [], captured
