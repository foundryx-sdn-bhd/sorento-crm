"""Phase 2 RED tests - MCP catalog entry and presenter for `crm_report_ask` (lane REPORT-ENGINE).

`documentation/plans/chatbot/PLAN-report-engine.md` section 10 "MCP".

`_report_ask(body) -> str` renders the WHOLE reply for a route body. Ambiguity flagged to the
captain: section 10 names the presenter but not whether `present_response` wires it through an
envelope; only the `_report_ask` text is pinned here, and the catalog entry.
"""
from __future__ import annotations

import pytest

from sorento_crm_mcp.catalog import CATALOG
from sorento_crm_mcp.presenters import _report_ask

TOOL = "crm_report_ask"
PERIOD = {"date_from": "2026-09-01", "date_to": "2026-09-30"}
BRAND = [{"key": "brand", "label": "Brand", "values": ["SORENTO"]}]


def _body(**over):
    body = {
        "status": "ok", "message": None,
        "basis": "delivered", "basis_label": "delivered sales", "measure": "amount",
        "group_by": "sales_agent", "group_label": "Sales agent",
        **PERIOD,
        "filters": BRAND,
        "rows": [
            {"rank": 1, "name": "SA01", "qty": 30, "amount": 1200.0},
            {"rank": 2, "name": "SA02", "qty": 15, "amount": 600.0},
            {"rank": 3, "name": "SA03", "qty": 5, "amount": 100.0},
        ],
        "more": 2, "total_count": 5,
        "total": {"qty": 55, "amount": 2100.0},
    }
    body.update(over)
    return body


def _spec():
    return next(s for s in CATALOG if s.name == TOOL)


# ------------------------------------------------------------------------- catalog


def test_catalog_wraps_the_report_ask_route():
    spec = _spec()
    assert spec.path == "/api/v1/order-management/report-ask", spec.path
    assert spec.method == "GET", spec.method


def test_catalog_declares_the_spec_params():
    params = set(_spec().query_params)
    wanted = {"date_from", "date_to", "basis", "measure", "group_by", "top_n", "sort", "product_code",
              "brand_ids", "category_ids", "sales_agent_ids", "customer_ids", "warehouse_codes",
              "channel", "contact_id", "space_id"}
    assert wanted <= params, sorted(wanted - params)


# ------------------------------------------------------------------------- presenter


def test_ranking_reply_lines():
    lines = _report_ask(_body()).split("\n")
    assert lines[0] == "Top 3 sales agents by delivered sales, Brand SORENTO, 2026-09-01 to 2026-09-30", lines
    assert "1. SA01 RM 1,200.00, 30 pcs" in lines, lines
    assert "2. SA02 RM 600.00, 15 pcs" in lines, lines
    assert "and 2 more" in lines, lines
    assert lines[-1] == "Total RM 2,100.00, 55 pcs", lines
    assert lines.index("and 2 more") < lines.index("Total RM 2,100.00, 55 pcs")


def test_no_more_line_when_nothing_is_cut():
    text = _report_ask(_body(more=0, total_count=3))
    assert "and 0 more" not in text and " more" not in text, text


def test_sort_asc_reads_bottom():
    first = _report_ask(_body(sort="asc")).split("\n")[0]
    assert first == "Bottom 3 sales agents by delivered sales, Brand SORENTO, 2026-09-01 to 2026-09-30", first


def test_ordered_basis_is_named_in_the_header():
    first = _report_ask(_body(basis="ordered", basis_label="ordered sales")).split("\n")[0]
    assert first == "Top 3 sales agents by ordered sales, Brand SORENTO, 2026-09-01 to 2026-09-30", first


def test_number_shape():
    body = _body(group_by=None, group_label=None, rows=[], more=0, total_count=0)
    assert _report_ask(body) == (
        "Sales by delivered sales, Brand SORENTO, 2026-09-01 to 2026-09-30: RM 2,100.00, 55 pcs"
    )


def test_refused_prints_the_message_only():
    msg = "Sorry, ZZT-REPAIR isn't one of the locations you can check."
    assert _report_ask(_body(status="refused", message=msg, rows=[], total=None)) == msg


@pytest.mark.parametrize(
    "group_by, label, plural",
    [
        ("customer", "Customer", "customers"),
        ("product", "Product", "products"),
        ("brand", "Brand", "brands"),
        ("category", "Category", "categories"),
        ("sales_agent", "Sales agent", "sales agents"),
        ("location", "Location", "locations"),
        ("channel", "Channel", "channels"),
        ("month", "Month", "months"),
    ],
)
def test_group_label_plurals(group_by, label, plural):
    first = _report_ask(_body(group_by=group_by, group_label=label)).split("\n")[0]
    assert first.startswith(f"Top 3 {plural} by delivered sales"), first


def test_filters_join_values_with_commas_and_filters_with_semicolons():
    filters = [
        {"key": "brand", "label": "Brand", "values": ["SORENTO", "MOCHA"]},
        {"key": "category", "label": "Category", "values": ["Chairs"]},
    ]
    first = _report_ask(_body(filters=filters)).split("\n")[0]
    assert first == ("Top 3 sales agents by delivered sales, Brand SORENTO, MOCHA; Category Chairs, "
                     "2026-09-01 to 2026-09-30"), first


def test_required_query_hints_force_the_contact_pair_and_the_period():
    from sorento_crm_mcp.server import TOOL_REQUIRED_QUERY_HINTS

    assert TOOL_REQUIRED_QUERY_HINTS["crm_report_ask"] == ("contact_id", "space_id", "date_from", "date_to")


def test_one_printed_row_reads_singular():
    """REPORT-ENGINE cloud pass: a dealer with one product read "Top 1 products"."""
    body = _body(group_by="product", group_label="Product",
                 rows=[{"rank": 1, "name": "CBN-200", "qty": 10, "amount": 1000.0}],
                 more=0, total_count=1, total={"qty": 10, "amount": 1000.0})
    assert _report_ask(body).splitlines()[0].startswith("Top 1 product by delivered sales")
