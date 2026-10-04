"""Guardrail: every field-reveal key a presenter's `b.restrict()` call declares must also
be a static `(key, label)` pair on that tool's OWN `ToolSpec.restricted_fields`, and vice
versa.

Found live, 8 Sep 2026: `crm_inventory_stock_balance_list` and
`crm_procurement_po_placed_list` both call `b.restrict(...)` in their
presenter to hide a field at render time, but neither `ToolSpec` declared
`restricted_fields` - so `mcp_tool_registry_service.sync_catalog` (which reads ONLY the
ToolSpec, never calls the tool) wrote an empty `mcp_tools.restricted_fields` for both, and
the Contacts > Access > Field reveals checklist (Slice C) had nothing to list or grant. The
render-time drop (`output_structurer`) worked; the admin surface that is supposed to let
someone grant the field back never saw it exist.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from sorento_crm_mcp.catalog import CATALOG

PRESENTERS_PATH = Path(__file__).resolve().parent.parent / "sorento_crm_mcp" / "presenters.py"

# tool name -> the presenter function(s) that render it, so a `.restrict()` call found
# inside one of them can be attributed to the right ToolSpec. Hand-maintained because
# presenters.py has no "this function renders tool X" marker of its own - a new restricted
# field on a tool not listed here is still caught by the reverse-direction test below,
# which needs no such mapping and would fail loudly asking for this one to be extended.
_PRESENTER_FUNCTIONS_BY_TOOL = {
    # both stock presenters restrict their own fields (D1, 8 Sep 2026): the detailed
    # row's Outstanding and the compact block's Total / warehouse suffixes
    "crm_inventory_stock_balance_list": ("_stock", "_stock_compact"),
    "crm_procurement_po_placed_list": ("_purchase_orders_placed",),
    # DO-ASK-SIMPLIFY rule 2: the DO list's logistics fields, plus the SO-outstanding
    # bucket's own row shape the same tool renders.
    "crm_order_management_orders_list": ("_orders_list", "_orders_so_outstanding"),
    "crm_order_management_orders_by_product_list": ("_orders_by_product",),
}

_RESTRICT_CALL_RE = re.compile(r'b\.restrict\([^,]+,\s*"([^"]+)"\)')

# Keys a CRM LANE enforces rather than a presenter: the field never reaches an envelope
# because the lane does not make the call without the grant. They are declared on the
# ToolSpec (so the grant card lists them) and enforced where named here, so the reverse
# test below does not read them as orphans. Add a row only with the enforcing seam.
_LANE_GATED_KEYS = {
    # 8 Sep 2026: on-order info is per contact; `lanes/business/answer.
    # _apply_crossdomain_rung` skips the PO rung entirely without it.
    "purchase_orders.placed": "app/services/chatbot/lanes/business/answer.py",
    # D13 (PLAN-chatbot-outstanding-report.md): SO figures are per contact; `lanes/
    # business/__init__.py::run_fetch` forces scope to `do` and skips the SO query
    # entirely without it.
    "sales_orders.outstanding": "app/services/chatbot/lanes/business/__init__.py",
    # PLAN-chatbot-sales-report.md S4 wiring point 4 (AC-1651): the whole TOOL is
    # per contact, no fallback scope - `lanes/business/__init__.py::run_fetch`
    # refuses before any fetch and arms nothing without the grant.
    "sales_orders.sales_report": "app/services/chatbot/lanes/business/__init__.py",
    # AC-64 (PLAN-low-stock-report.md): the whole TOOL is per contact, not one field of
    # it - the report is a workbook, so there is nothing for a presenter to mask. Two
    # seams enforce it and both refuse before any work: `lanes/business/__init__.py::
    # run_fetch` answers the refusal line without fetching, and the route itself re-reads
    # the key and 403s (AC-41), because that fetch CREATES a reorder run and sends a file.
    "scm.low_stock_report": "app/services/chatbot/lanes/business/__init__.py",
}


def _function_body(source: str, func_name: str) -> str:
    match = re.search(rf"^def {re.escape(func_name)}\(.*?(?=^def |\Z)", source, re.S | re.M)
    assert match, f"no function {func_name!r} found in presenters.py - is the mapping stale?"
    return match.group(0)


def test_every_presenter_restrict_key_is_declared_on_its_toolspec():
    source = PRESENTERS_PATH.read_text(encoding="utf-8")
    specs_by_name = {spec.name: spec for spec in CATALOG}
    for tool_name, functions in _PRESENTER_FUNCTIONS_BY_TOOL.items():
        spec = specs_by_name[tool_name]
        declared = {key for key, _label in spec.restricted_fields}
        used: set[str] = set()
        for func_name in functions:
            used |= set(_RESTRICT_CALL_RE.findall(_function_body(source, func_name)))
        assert used, f"{tool_name}: no b.restrict() call found in {functions} - is the mapping stale?"
        missing = used - declared
        assert not missing, (
            f"{tool_name}: the presenter restricts {sorted(missing)} but ToolSpec."
            f"restricted_fields does not declare it - sync_catalog writes an empty list and "
            f"the Field reveals checklist has nothing to grant"
        )


def test_every_declared_restricted_field_is_actually_used_by_a_presenter():
    """The reverse direction: an orphan declaration would let someone grant a key that
    changes nothing, which reads as the grant screen being broken rather than unused."""
    source = PRESENTERS_PATH.read_text(encoding="utf-8")
    all_used_keys = set(_RESTRICT_CALL_RE.findall(source))
    for spec in CATALOG:
        for key, _label in spec.restricted_fields:
            if key in _LANE_GATED_KEYS:
                continue
            assert key in all_used_keys, (
                f"{spec.name}: restricted_fields declares {key!r} but no presenter calls "
                f"b.restrict(..., {key!r}) - granting it would change nothing"
            )


def test_the_two_growth_r1_keys_are_declared_exactly_where_expected():
    """Pins the two keys this plan ships, so a future rename is caught here rather than as
    a silent "No restricted field exists yet" on the Contacts screen."""
    specs_by_name = {spec.name: spec for spec in CATALOG}
    assert dict(specs_by_name["crm_inventory_stock_balance_list"].restricted_fields) == {
        "inventory.sellable": "Outstanding SO on stock answers",
    }
    assert dict(specs_by_name["crm_procurement_po_placed_list"].restricted_fields) == {
        "purchase_orders.supplier": "PO supplier",
        "purchase_orders.placed": "PO placed (on order) on stock answers",
    }


def test_every_lane_gated_key_names_a_seam_that_exists():
    """Cross-tree on purpose (the seam is backend code), so it runs in the monorepo only:
    the MCP docker image holds `sorento_crm_mcp` alone and CI's "Run MCP test suite"
    executes there (PR #735, 8 Sep 2026). The backend side pins the same key from its
    own tree - `tests/chatbot/test_crossdomain_ladder.py::TestThePORungGrantKey`."""
    backend = Path(__file__).resolve().parents[2] / "sorento_crm_backend"
    if not backend.is_dir():
        pytest.skip("backend tree not present (MCP image)")
    for key, path in _LANE_GATED_KEYS.items():
        seam = backend / path
        assert seam.is_file(), f"{key}: enforcing seam {path} not found"
        assert key in seam.read_text(encoding="utf-8"), f"{key}: {path} does not name the key"
