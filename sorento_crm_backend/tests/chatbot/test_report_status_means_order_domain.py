"""Port of main's `test_output_exchange_unit.py::TestReportOrderStatusAlwaysReaches
TheReportDespiteAWrongDomainHint` onto the new seam (coder 25's own pins table, 20 Sep
2026: "main's `TestReportOrderStatusAlwaysRoutesToOrderDomain` (4 tests) is the one
thing worth porting: the same rule now lives in `turn_runtime._report_status_means_
order_domain`, plus a FIFTH case the port needed - `intent_hint == "low_stock_report"`
is left alone").

Main's own finding 3(b) (owner live testing, 19 Sep 2026, PLAN-chatbot-sales-report.md):
the measured emission for "Srt5674 August total sale quantity" parsed `order_status:
"sales_report"` alongside `domain_hint: "master_products"` - the parser knew the ask was
a report and still named the wrong domain, and `lanes/business/__init__.py`'s tool-pick
override for `sales_report` (like the outstanding one beside it) only fires when
`domain == "order"`, so the turn fell through to the product-master listing instead of
the report. The fix re-homed here reads ONLY the parser's own structured `order_status`/
`status` fields, never the message text.

Unit-level, no DB, no turn engine, no mocks beyond a plain dict - `_report_status_means_
order_domain(out: dict) -> None` mutates its argument in place and is the one seam every
verdict passes through (`turn_runtime.with_routing_agent_default`), so this is a direct,
honest test of the real function, not a re-implementation of it.
"""
from __future__ import annotations

from typing import Any

import pytest

from app.services.chatbot.turn_runtime import _report_status_means_order_domain


def _domain_out(
    *,
    domain_hint: str | None,
    order_status: str | None = None,
    status: str | None = None,
    intent_hint: str | None = None,
    asks: list[Any] | None = None,
) -> dict[str, Any]:
    """`_report_status_means_order_domain`'s output for a verdict with the given
    fields, a single product entity, nothing else about the turn varied - mirrors
    main's own `_report_status_domain_out` helper (dropped with the deleted module)."""
    out: dict[str, Any] = {
        "domain_hint": domain_hint,
        "order_status": order_status,
        "status": status,
        "intent_hint": intent_hint,
        "asks": asks,
        "entities": [
            {"raw": "SRT5674", "hint": "product", "canonical_code": None, "current_message": True}
        ],
    }
    _report_status_means_order_domain(out)
    return out


class TestReportOrderStatusAlwaysRoutesToOrderDomain:
    """Finding 3(b) (owner live testing, 19 Sep 2026, PLAN-chatbot-sales-report.md):
    a REPORT status word always routes to the `order` domain. Reads ONLY the parser's
    own structured fields, never the message text."""

    def test_master_products_domain_with_sales_report_status_is_corrected_to_sales(
        self,
    ) -> None:
        # R7 (PLAN-prompt-dynamic-30sep D9): a sales figure status is the `sales` domain.
        out = _domain_out(domain_hint="master_products", order_status="sales_report")
        assert out["domain_hint"] == "sales", out
        assert out.get("domain_corrected") == (
            "master_products->sales (order_status sales_report)"
        ), out

    @pytest.mark.parametrize("order_status", ["outstanding", "so_outstanding"])
    def test_a_non_order_domain_with_an_outstanding_status_is_also_corrected(
        self, order_status: str
    ) -> None:
        out = _domain_out(domain_hint="inventory", order_status=order_status)
        assert out["domain_hint"] == "order", out
        assert out.get("domain_corrected") == f"inventory->order (order_status {order_status})", out

    def test_domain_hint_order_with_sales_report_is_corrected_to_sales(self) -> None:
        # R7 (PLAN-prompt-dynamic-30sep D9): the parser still says `order` for a sales ask.
        out = _domain_out(domain_hint="order", order_status="sales_report")
        assert out["domain_hint"] == "sales", out
        assert out.get("domain_corrected") == "order->sales (order_status sales_report)", out

    def test_a_null_order_status_with_master_products_domain_is_left_alone(self) -> None:
        """Guard: this correction is keyed on `order_status`, never fired just because
        a product entity is present - a plain product-master ask must be untouched."""
        out = _domain_out(domain_hint="master_products", order_status=None)
        assert out["domain_hint"] == "master_products", out
        assert "domain_corrected" not in out, out

    def test_a_low_stock_report_intent_is_left_alone(self) -> None:
        """The FIFTH case the port needed (not in main's original 4, coder 25's own
        addendum): `_report_status_means_order_domain`'s new-engine home has an extra
        carve-out main's old seam never needed - `intent_hint == "low_stock_report"`
        returns early, because `lanes/business/run_fetch` already ranks that intent
        ahead of both report overrides. Without it, "reorder report" / "what is below
        level" (which record `order_status: "outstanding"` off their own words with
        `domain_hint: "inventory"`) would be flipped to the `order` domain and three
        recorded low-stock replay fixtures (`handbuilt-lsr-001`, `-lsr-002`,
        `handbuilt-rp-003`) would route to the wrong lane."""
        out = _domain_out(
            domain_hint="inventory", order_status="outstanding", intent_hint="low_stock_report"
        )
        assert out["domain_hint"] == "inventory", out
        assert "domain_corrected" not in out, out
