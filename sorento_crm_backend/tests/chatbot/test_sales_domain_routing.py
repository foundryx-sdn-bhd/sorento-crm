"""R7 of PROMPT-DYNAMIC: "sales" is its own chatbot domain (owner, 30 Sep 2026: "better
own sales domain otherwise jumble up with order").

The parser still teaches `domain_hint "order"` for a sales ask in the owner's wording, so
the CODE maps an order status of the sales kind (`contracts.SALES_FIGURE_STATUSES`:
`sales_report`, `sales_analysis`, `top_selling`) to the `sales` domain, at the one seam
every fresh verdict passes through (`turn_runtime.with_routing_agent_default`). Order
questions (outstanding, delivered, SO/DO outstanding, a plain DO list) stay `order`,
with the tools they always had. The grant refusal reads exactly as before.

Built on the harness the sales tests already share (`test_outstanding_lane.py`'s
`_run_turn` / `_capturing_mcp`, `test_top_selling_lane.py`'s `route` double).
"""
from __future__ import annotations

from typing import Any

import pytest

from app.services.chatbot.lanes.business.services import FetchServices
from tests.chatbot.test_outstanding_lane import (
    CUSTOMER_NAME,
    CUSTOMER_UUID,
    PRODUCT_CODE,
    PRODUCT_UUID,
    REPORT_HIT,
    _capturing_mcp,
    _qf,
    _run_turn,
    _seed_contact,
)
from tests.chatbot.test_sales_analysis_lane import _rendered as _sales_analysis_hit
from tests.chatbot.test_sales_report_lane import SALES_REPORT_HIT
from tests.chatbot.test_top_selling_lane import _calls, _ts, _turn, route  # noqa: F401 - fixture

GRANT = "sales_orders.sales_report"
DENIAL = "Sales report is not enabled for your account."
CUSTOMER = {"uuid": CUSTOMER_UUID, "entity_type": "customer", "canonical_code": CUSTOMER_NAME}
PRODUCT = {"uuid": PRODUCT_UUID, "entity_type": "product", "canonical_code": PRODUCT_CODE}
# A 31-day range, the most #1433 (DO-ASK-SIMPLIFY) lets an order list ask carry.
_A_MONTH = {"date_filter_start": "2026-09-01", "date_filter_end": "2026-09-30"}


def _normalised(**verdict: Any) -> dict[str, Any]:
    """The verdict as every reader downstream sees it (the routing-default seam)."""
    from app.services.chatbot.turn_runtime import with_routing_agent_default

    return with_routing_agent_default({"routing": {}, **verdict}, pending=None)


def _picked(domain: str, order_status: str | None, entities: list[dict[str, Any]], *,
            attributes=(GRANT, "sales_orders.outstanding"), response: Any = None, **qf: Any) -> list[tuple]:
    """`run_fetch` for one domain + status, returning every `(tool, args)` it called."""
    from app.services.chatbot.lanes.business import run_fetch

    call, captured = _capturing_mcp(response)
    run_fetch(
        {
            "gate": {"compatible_entities": entities},
            "tier_gate": None,
            "ctx": {
                # V1-DOC-SCOPE: an unlinked contact reads any customer's orders only as staff.
                "parse": {"output": {**_qf(domain_hint=domain, order_status=order_status, entities=[], **qf), "doc_staff": True}},
                "contact": {"id": 437264483},
                "access": {"attributes": list(attributes)},
            },
        },
        services=FetchServices(mcp_call=call),
    )
    return captured


class TestTheSalesStatusesMeanTheSalesDomain:
    @pytest.mark.parametrize("status", ["sales_report", "sales_analysis", "top_selling"])
    @pytest.mark.parametrize("prior", ["order", "master_products", None])
    def test_a_sales_status_is_the_sales_domain(self, status: str, prior: str | None) -> None:
        out = _normalised(domain_hint=prior, order_status=status)
        assert out["domain_hint"] == "sales", out

    def test_my_sales_this_month_is_the_sales_domain(self) -> None:
        out = _normalised(domain_hint="order", order_status="sales_analysis", self_reference=True)
        assert out["domain_hint"] == "sales", out

    def test_a_second_ask_keeps_its_own_domain_and_the_order_one_becomes_sales(self) -> None:
        asks = [{"domain": "inventory", "intent": None}, {"domain": "order", "intent": None}]
        out = _normalised(domain_hint="inventory", order_status="top_selling", asks=asks)
        assert out["domain_hint"] == "inventory", out
        assert [a["domain"] for a in out["asks"]] == ["inventory", "sales"], out

    @pytest.mark.parametrize("status", ["outstanding", "so_outstanding", "delivered", "do_outstanding"])
    def test_an_order_status_stays_the_order_domain(self, status: str) -> None:
        assert _normalised(domain_hint="order", order_status=status)["domain_hint"] == "order"

    @pytest.mark.parametrize("status", ["outstanding", "so_outstanding", "do_outstanding"])
    def test_an_outstanding_status_still_corrects_to_order(self, status: str) -> None:
        assert _normalised(domain_hint="inventory", order_status=status)["domain_hint"] == "order"

    def test_a_plain_order_ask_is_untouched(self) -> None:
        assert _normalised(domain_hint="order", order_status=None)["domain_hint"] == "order"


class TestTheLanePicksTheSalesTools:
    def test_sales_analysis_under_sales(self, session_factory) -> None:
        captured = _picked("sales", "sales_analysis", [], response=_sales_analysis_hit())
        assert [c[0] for c in captured] == ["crm_sales_analysis"]

    @pytest.mark.parametrize("entity", [CUSTOMER, PRODUCT])
    def test_sales_report_under_sales(self, session_factory, entity) -> None:
        captured = _picked("sales", "sales_report", [entity], response=SALES_REPORT_HIT)
        assert captured and captured[0][0] == "crm_sales_report", captured

    def test_top_selling_under_sales(self, session_factory) -> None:
        captured = _picked("sales", "top_selling", [], top_selling={"rank_by": "quantity", "basis": "delivered", "top_n": 5})
        assert captured and captured[0][0] == "crm_top_selling_report", captured

    @pytest.mark.parametrize("status", ["outstanding", "so_outstanding", "delivered"])
    def test_an_order_report_still_picks_the_outstanding_report(self, session_factory, status) -> None:
        # #1433 answers a dateless order list ask with "Which period?", so give it a range.
        captured = _picked("order", status, [PRODUCT], response=REPORT_HIT, **_A_MONTH)
        expected = "crm_outstanding_report" if status != "delivered" else "crm_order_management_orders_list"
        assert captured and captured[0][0] == expected, captured

    def test_a_plain_do_list_still_picks_the_orders_list(self, session_factory) -> None:
        # #1433 answers a dateless order list ask with "Which period?", so give it a range.
        captured = _picked("order", None, [CUSTOMER], **_A_MONTH)
        assert captured and captured[0][0] == "crm_order_management_orders_list", captured


class TestAWholeTurn:
    def test_my_sales_this_month_answers_from_the_sales_analysis(self, session_factory, monkeypatch) -> None:
        _seed_contact(session_factory, variables={})
        _result, captured = _run_turn(
            session_factory, monkeypatch,
            qf=_qf(order_status="sales_analysis", entities=[], self_reference=True),
            text_body="my sales this month", msg_id="ZZT-sales-domain-my-1",
            attributes=[GRANT], mcp_response=_sales_analysis_hit(),
        )
        assert [c[0] for c in captured] == ["crm_sales_analysis"]

    def test_a_top_selling_ask_answers_from_the_ranking(self, session_factory, monkeypatch, route) -> None:  # noqa: F811
        _seed_contact(session_factory, variables={})
        _text, captured = _turn(session_factory, monkeypatch, _ts(), "top 5 selling items")
        assert len(_calls(captured)) == 1, captured

    @pytest.mark.parametrize(
        "status,text",
        [("sales_report", "sales report for SRTWT7445"), ("sales_analysis", "total sales this year"),
         ("top_selling", "top 5 selling items")],
    )
    def test_no_grant_is_refused_exactly_as_before(self, session_factory, monkeypatch, status, text) -> None:
        _seed_contact(session_factory, variables={})
        entities = None if status == "sales_report" else []
        qf = _qf(order_status=status, **({} if entities is None else {"entities": entities}))
        result, captured = _run_turn(
            session_factory, monkeypatch, qf=qf, text_body=text,
            msg_id=f"ZZT-sales-domain-no-grant-{status}", attributes=[],
            matches={PRODUCT_CODE: PRODUCT},
        )
        assert captured == []
        assert ((result.reply or {}).get("text") or "").strip() == DENIAL

    def test_run_fetch_refuses_the_sales_domain_the_same_way(self, session_factory) -> None:
        from app.services.chatbot.lanes.business import run_fetch

        call, captured = _capturing_mcp(SALES_REPORT_HIT)
        out = run_fetch(
            {
                "gate": {"compatible_entities": [CUSTOMER]},
                "tier_gate": None,
                "ctx": {
                    "parse": {"output": _qf(domain_hint="sales", order_status="sales_report", entities=[])},
                    "contact": {"id": 437264483},
                    "access": {"attributes": []},
                },
            },
            services=FetchServices(mcp_call=call),
        )
        assert captured == []
        assert DENIAL in str(out)


class TestTheDomainRow:
    def test_coerce_keeps_sales(self) -> None:
        from app.services.chatbot.contracts import coerce_domain_hint

        assert coerce_domain_hint("sales") == "sales"

    def test_select_tool_reads_the_sales_row(self) -> None:
        from app.services.chatbot.lanes.business.fetch import select_tool

        assert select_tool("sales") == [{"name": "crm_sales_report", "similarity": 1.0}]

    def test_the_three_tools_are_on_the_allow_list(self) -> None:
        from app.services.chatbot.lanes.business.fetch import CHATBOT_READ_ONLY_TOOLS
        from app.services.mcp_tool_domains import CHATBOT_TOOL_DOMAINS

        for tool in ("crm_sales_report", "crm_sales_analysis", "crm_top_selling_report"):
            assert tool in CHATBOT_READ_ONLY_TOOLS
            assert CHATBOT_TOOL_DOMAINS[tool] == "sales"

    def test_the_seed_row_matches_the_migration(self) -> None:
        import importlib.util
        from pathlib import Path

        from app.services.chatbot.turn import policy_rows

        path = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "pdyn_0001_status_words_sales.py"
        spec = importlib.util.spec_from_file_location("_pdyn_0001", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        row = next(r for r in policy_rows.DEFAULT_DOMAIN_ROWS if r["name"] == "sales")
        for key, value in module.SALES_DOMAIN.items():
            assert row[key] == value, key
        order = next(r for r in policy_rows.DEFAULT_DOMAIN_ROWS if r["name"] == "order")
        assert row["narrowing"] == order["narrowing"]


class TestReviewPass2:
    """Second review of PROMPT-DYNAMIC (30 Sep 2026), should-fix 3 and 4."""

    def test_a_sales_hint_without_a_sales_status_is_the_order_domain(self) -> None:
        # "sales orders for hanlim": the parser can now pick `sales` off the switch word,
        # with no sales status. Without this the lane ran the sales report from tools[0].
        out = _normalised(domain_hint="sales", order_status=None)
        assert out["domain_hint"] == "order"
        out = _normalised(domain_hint="sales", order_status="outstanding")
        assert out["domain_hint"] == "order"

    def test_a_sales_hint_with_a_sales_status_stays_sales(self) -> None:
        assert _normalised(domain_hint="sales", order_status="sales_report")["domain_hint"] == "sales"

    def test_the_sales_report_override_runs_under_sales(self, session_factory, monkeypatch) -> None:
        import app.services.chatbot.lanes.business as lane

        calls: list[str] = []
        real = lane._resolve_report_product_and_location

        def spy(*args, **kwargs):
            calls.append("resolved")
            return real(*args, **kwargs)

        monkeypatch.setattr(lane, "_resolve_report_product_and_location", spy)
        captured = _picked("sales", "sales_report", [CUSTOMER], response=SALES_REPORT_HIT)
        assert captured and captured[0][0] == "crm_sales_report", captured
        assert calls == ["resolved"], "the sales report override (product + location resolution) did not run"
