"""Shared builders for the WA-CONCISE red tests (card v4).

Envelopes come from the REAL MCP presenter (`present_response`) over a raw tool payload, the
way `test_stock_ask_availability_rendered_reply.py` does, then through the real
`fetch.output_structurer`. Placeholder data only (public repo).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from app.services.chatbot.lanes.business import fetch

STOCK_TOOL = "crm_inventory_stock_balance_list"
INCOMING_TOOL = "crm_incoming_stock_list"
ORDERS_TOOL = "crm_order_management_orders_list"
PRODUCTS_TOOL = "crm_master_products_list"
PO_TOOL = "crm_procurement_po_placed_list"

#: Fixed so the expected footer is exact: `_Updated 11/09/2026 17:26_`.
LAST_UPDATED_RAW = "2026-09-11T17:26:05"
FOOTER = "_Updated 11/09/2026 17:26_"
OLD_FOOTER = "_Data last updated: 11/09/2026 17:26:05_"

#: The contact holds `inventory.sellable`, so a `granted_value` (the O/S suffix) swaps in.
GRANTED = {"semantic_input": {}, "access": {"attributes": ["inventory.sellable"]}}
UNGRANTED = {"semantic_input": {}}


def _present():
    repo_root = Path(__file__).resolve().parents[3]
    mcp_root = repo_root / "sorento_crm_mcp"
    if str(mcp_root) not in sys.path:
        sys.path.append(str(mcp_root))
    try:
        from sorento_crm_mcp.presenters import present_response
    except ImportError:  # pragma: no cover
        pytest.skip("sorento_crm_mcp is not importable in this environment")
    return present_response


def envelope_json(tool: str, payload: dict[str, Any]) -> str:
    """The presenter's JSON string, i.e. what the MCP client hands the lane."""
    return _present()(tool, json.dumps(payload))


def render(tool: str, payload: dict[str, Any], ctx: dict[str, Any] | None = None) -> str:
    """Presenter then `output_structurer`: the exact WhatsApp text of a list reply."""
    env = json.loads(envelope_json(tool, payload))
    out = fetch.output_structurer(env, ctx if ctx is not None else UNGRANTED)
    return out["response"]


# ---- stock ---------------------------------------------------------------- #


def compact_entry(code: str, locs: list[tuple[str, int, int | None]], *, total: int | None = None) -> dict:
    """One compact entry. `locs` = (warehouse_code, on_hand, open_so or None). With any
    open_so the entry carries `sellable` (the include_sellable shape)."""
    with_os = any(o is not None for _, _, o in locs)
    entry: dict[str, Any] = {
        "product_code": code,
        "total_on_hand": sum(q for _, q, _ in locs) if total is None else total,
        "locations": [
            {
                "warehouse_code": w,
                "quantity_on_hand": q,
                **({"open_so_qty": o} if o is not None else {}),
            }
            for w, q, o in locs
        ],
    }
    if with_os:
        entry["sellable"] = {}
        entry["open_so_qty"] = sum(o or 0 for _, _, o in locs)
    return entry


def compact_payload(entries: list[dict]) -> dict:
    return {
        "data": [],
        "pagination": {"total": len(entries), "page": 1, "limit": 50},
        "stock_visibility": {"mode": "compact", "warehouse_codes": None, "source": "contact"},
        "stock_summary": entries,
        "last_updated_at": LAST_UPDATED_RAW,
    }


def detailed_row(
    company: str, code: str, loc: str, qty: int, os_qty: int | None = None, name: str | None = None
) -> dict:
    row: dict[str, Any] = {
        "company_name": company,
        "product_code": code,
        "product_name": name or code,
        "system_location": loc,
        "warehouse": "WAREHOUSE X",
        "quantity_on_hand": qty,
    }
    if os_qty is not None:
        row["sellable"] = {}
        row["open_so_qty"] = os_qty
    return row


def detailed_payload(rows: list[dict]) -> dict:
    return {
        "data": rows,
        "pagination": {"total": len(rows), "page": 1, "limit": 50},
        "last_updated_at": LAST_UPDATED_RAW,
    }


# ---- incoming ------------------------------------------------------------- #


def incoming_payload(rows: list[dict]) -> dict:
    return {"data": rows, "pagination": {"total": len(rows), "page": 1, "limit": 50}}


def incoming_shipment(
    code: str, container: str, eta: str, qty: int, allocs: list[tuple[str, int]]
) -> dict:
    return {
        "shipping_container_number": container,
        "estimated_arrival_date": eta,
        "lines": [
            {
                "product_code": code,
                "product_name": code,
                "remaining_incoming_quantity": qty,
                "warehouse_allocations": [
                    {"warehouse_code": w, "allocated_quantity": q} for w, q in allocs
                ],
                "unallocated_quantity": 0,
            }
        ],
    }


EMPTY_PAYLOAD = {"data": [], "pagination": {"total": 0, "page": 1, "limit": 50}, "empty": True}
