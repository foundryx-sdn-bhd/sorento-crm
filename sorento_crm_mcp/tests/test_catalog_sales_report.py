"""Phase 2 RED tests - MCP catalog + presenter wiring for the sales report (S3).

`documentation/plans/chatbot/PLAN-chatbot-sales-report.md` Slice S3;
`documentation/plans/chatbot/chatbot-sales-report-acceptance-criteria.md` AC-1640
(catalog + presenter wiring) and the MCP half of AC-1641 (the reveal-key pair
declared on THIS tool's own `ToolSpec.restricted_fields`, which is what
`mcp_tool_registry_service.sync_catalog` copies into `mcp_tools.restricted_fields` -
the backend half, `FIELD_REVEAL_KEYS`, is asserted in
`sorento_crm_backend/tests/chatbot/test_sales_report_lane.py`).

Written BEFORE the catalog entry exists: `next(s for s in CATALOG if s.name ==
"crm_sales_report")` fails with `StopIteration`, not an import error - the module
imports fine, there is simply no such tool yet. The presenter-wiring half fails for a
different, equally legitimate reason: `_sales_report` (S1)
already renders the WhatsApp text correctly (see `test_presenters_sales_report.py`),
but `present_response` has no `crm_sales_report` branch yet (its own module docstring
says so, verbatim, above `_sales_report`/`_sales_report_detail`), so the generic
item/field envelope runs instead and carries no `response` key at all - the assertion
below fails on a `None == <golden text>` comparison, not a crash.
"""
from __future__ import annotations

import copy
import inspect
import json
from pathlib import Path

from sorento_crm_mcp.catalog import CATALOG
from sorento_crm_mcp.presenters import PRESENTER_TOOLS, present_response
from sorento_crm_mcp.server import _compile_tool

_FIXTURES = Path(__file__).parent / "fixtures" / "sales_report"

REQUIRED_QUERY_PARAMS = (
    "product_code",
    "customer_query",
    "customer_ids",
    "channel",
    "warehouse_codes",
    "location_token",
    "date_from",
    "date_to",
    "group_by",
    "contact_id",
    "space_id",
)


def _mock(name: str) -> dict:
    return json.loads((_FIXTURES / f"sales-report-{name}.json").read_text())


def _golden(name: str) -> str:
    return (_FIXTURES / f"sales-report-{name}.txt").read_text().rstrip("\n")


def test_catalog_lists_sales_report_tool() -> None:
    spec = next(s for s in CATALOG if s.name == "crm_sales_report")

    assert spec.path == "/api/v1/order-management/sales-report", spec.path
    assert spec.method == "GET", spec.method
    assert spec.domain == "orders", spec.domain
    assert spec.restricted_fields == (("sales_orders.sales_report", "Sales report"),), (
        f"MCP half of AC-1641: sync_catalog copies THIS tuple straight into "
        f"mcp_tools.restricted_fields, which is what the Contacts > Access > Field "
        f"reveals checklist and FIELD_REVEAL_KEYS both key off: {spec.restricted_fields}"
    )
    missing = [p for p in REQUIRED_QUERY_PARAMS if p not in spec.query_params]
    assert not missing, f"crm_sales_report missing query params: {missing} (has {spec.query_params})"
    assert "detail" not in spec.query_params, (
        "`detail=so` is retired (the delivery order list replaces the sales order list): "
        f"{spec.query_params}"
    )


def test_present_response_wires_crm_sales_report_to_the_report_presenter() -> None:
    """AC-1640: `present_response("crm_sales_report", ...)` must dispatch to
    `_sales_report`, the same way `present_response("crm_outstanding_report", ...)`
    dispatches to `_outstanding_report` via `_outstanding_envelope` - a minimal
    envelope carrying `response` (the rendered text) and `has_result`, not the
    generic item/field shape."""
    raw = json.dumps(_mock("month"))
    envelope = json.loads(present_response("crm_sales_report", raw))
    assert envelope.get("response") == _golden("month"), (
        f"crm_sales_report is not wired into present_response yet - got envelope keys "
        f"{sorted(envelope)}, response={envelope.get('response')!r}"
    )
    assert envelope.get("has_result") is True, envelope


def test_present_response_wires_a_group_by_drill_through_the_same_presenter() -> None:
    """A body carrying `group_by` renders the drill (rows, `and N more`, `*Total:*`, the
    continued options) through the SAME `crm_sales_report` branch: no second tool, no `detail`
    key to swap on."""
    envelope = json.loads(present_response("crm_sales_report", json.dumps(_mock("drill-product"))))
    assert envelope.get("response") == _golden("drill-product"), (
        f"got envelope keys {sorted(envelope)}, response={envelope.get('response')!r}"
    )
    assert envelope.get("has_result") is True, envelope


# --------------------------------------------------------------------- R-B1
# reviewer finding, Phase 3 fix round: `crm_sales_report` is wired into
# `present_response` (S1/S3 already shipped) but was never added to
# `PRESENTER_TOOLS` - the frozenset `server._compile_tool` reads to decide
# whether to inject the `view` parameter into the compiled tool's signature
# (see that module's own comment, and `test_catalog_low_stock.py`'s sibling
# test for `crm_low_stock_report`, lines 96-111, which this test mirrors
# exactly). Without it the LLM has no `view` argument to send, `view=render`
# never reaches the dispatcher, and every call falls through to the raw route
# body - the same bug class AC-62 pins for the low stock report.


def test_sales_report_is_a_presenter_tool() -> None:
    spec = next(s for s in CATALOG if s.name == "crm_sales_report")
    assert "crm_sales_report" in PRESENTER_TOOLS, (
        "crm_sales_report must be a presenter tool or `view` is never injected "
        f"into its compiled signature: {sorted(PRESENTER_TOOLS)}"
    )
    params = inspect.signature(_compile_tool(spec)).parameters
    assert "view" in params, (
        f"the compiled tool must accept `view`: {sorted(params)}"
    )


def test_present_response_wires_crm_sales_report_miss() -> None:
    """A body with no periods must still ride the minimal envelope, `has_result`
    False - the presenter's own `has_result` fact the lane reads to route a total
    miss to the escalate offer (AC-1658), never guessed from the text."""
    raw = json.dumps(_mock("miss"))
    envelope = json.loads(present_response("crm_sales_report", raw))
    assert envelope.get("response") == _golden("miss"), envelope.get("response")
    assert envelope.get("has_result") is False, envelope
