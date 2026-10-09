"""AVAIL-ONHAND-BASIS (owner ruling, 6 Oct 2026): the chatbot availability figure is ON HAND
in the contact's allowed warehouses, nothing subtracted. Outstanding sales orders are ignored.

Prod case (MOCHA-WH scope): "MWC7632-SH-P" then "10" was answered "No stock and no incoming"
while the staff stock summary at the same data timestamp read Total 6 (O/S 13), MOCHA-WH 6
(O/S 13). `_apply_stock_visibility` judged `on hand - open SO = 6 - 13 = -7`, and -7 is "no
stock". Owner (verbatim intent): "our basis should be the on hand, without considering the
outstanding, no wording change, just the calculation behind the stock change, need to ignore
the outstanding."

Only the availability mode's figure changes. The staff stock summary's O/S and sellable
(`app/api/v1/inventory/stock.py::_with_sellable`) are a different read and stay as they are:
that reply is MEANT to show the outstanding.

Plan: documentation/plans/chatbot/PLAN-avail-onhand-basis-06oct.md.
"""
from __future__ import annotations

import json

import pytest

from app.main import app  # noqa: F401  (first app import, see test_stock_availability_block)
from app.api.v1.inventory.stock import _with_sellable
from app.services.company_scope import DEFAULT_COMPANY_ID
from app.services.inventory_service import StockService

from tests._mc_lookup_seed import product, stock
from tests.test_stock_availability_block import (  # noqa: F401  (fixtures)
    _category_of,
    _contact,
    _entry,
    _policy_row,
    _so_line,
    _wh,
    client,
    db,
)

# The prod case's figures: 6 on hand, 13 outstanding, in the one warehouse the contact sees.
ON_HAND = 6
OPEN_SO = 13


def _seed(db, *, on_hand, open_so, x=100, mode="availability"):
    wh = _wh(db, "ZZTMWH")
    p = product(db, company_id=DEFAULT_COMPANY_ID)
    _category_of(db, p).chatbot_max_qty = x
    stock(db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=wh.id, on_hand=on_hand)
    if open_so:
        _so_line(db, product_id=p.id, warehouse_id=wh.id, ordered=open_so)
    contact = _contact(db)
    _policy_row(db, mode=mode, warehouse_ids=[wh.id], contact=contact)
    db.flush()
    return wh, p, contact


def _ask(db, p, contact, q):
    result = StockService(db).list_stock(
        product_ids=[p.id], contact_id=contact.id, requested_quantities={p.id: q}
    )
    return _entry(result, p.id)


# ------------------------------------------------------------- the availability figure


def test_prod_case_ask_10_with_6_on_hand_and_13_outstanding_is_in_stock_6_available(db):
    """MWC7632-SH-P x 10 -> "Only 6 got stock." (the `short_of` path), not "No stock
    and no incoming"."""
    _, p, contact = _seed(db, on_hand=ON_HAND, open_so=OPEN_SO)
    entry = _ask(db, p, contact, 10)
    assert entry["branch"] == "in_stock"
    assert entry["available_qty"] == ON_HAND


def test_prod_case_ask_5_with_6_on_hand_and_13_outstanding_is_covered(db):
    """MWC7632-SH-P x 5 -> "Got stock." with no count: 6 on hand covers 5."""
    _, p, contact = _seed(db, on_hand=ON_HAND, open_so=OPEN_SO)
    entry = _ask(db, p, contact, 5)
    assert entry["branch"] == "in_stock"
    assert entry["available_qty"] is None


def test_no_stock_no_outstanding_no_shipment_is_still_no_incoming(db):
    """0 on hand, 0 outstanding, nothing incoming -> "No stock and no incoming", unchanged."""
    _, p, contact = _seed(db, on_hand=0, open_so=0)
    entry = _ask(db, p, contact, 5)
    assert entry["branch"] == "no_incoming"
    assert entry["available_qty"] is None


@pytest.mark.parametrize("open_so", [0, 6, 13, 500])
def test_outstanding_never_moves_the_figure(db, open_so):
    """The same on hand answers the same whatever the open SO is: 6 on hand, ask 10 is
    always `in_stock` with "Only 6 got stock."."""
    _, p, contact = _seed(db, on_hand=ON_HAND, open_so=open_so)
    entry = _ask(db, p, contact, 10)
    assert entry["branch"] == "in_stock"
    assert entry["available_qty"] == ON_HAND


def test_the_route_answers_the_prod_case_on_the_wire(client, db):
    """The same case through `GET /inventory/stock/balance` (the path n8n calls)."""
    _, p, contact = _seed(db, on_hand=ON_HAND, open_so=OPEN_SO)
    res = client.get(
        "/api/v1/inventory/stock/balance",
        params={
            "product_ids": p.id,
            "contact_id": contact.id,
            "space_id": "364817",
            "requested_quantities": json.dumps({p.id: 10}),
        },
    )
    assert res.status_code == 200, res.text
    (entry,) = [e for e in res.json()["stock_availability"] if e["product_id"] == p.id]
    assert entry["branch"] == "in_stock"
    assert entry["available_qty"] == ON_HAND


# -------------------------------------------- the staff stock summary is NOT changed


def test_staff_stock_summary_keeps_os_and_sellable(db):
    """The staff reply at the same data: Total 6 (O/S 13), sellable -7. The outstanding is
    still subtracted THERE (`_with_sellable`), on the detailed rows and the compact
    summary alike - only the availability figure stopped subtracting it."""
    wh, p, _ = _seed(db, on_hand=ON_HAND, open_so=OPEN_SO)
    service = StockService(db)

    # Staff path: no contact, detailed rows.
    detailed = json.loads(_with_sellable(service, service.list_stock(product_ids=[p.id])).body)
    (row,) = [r for r in detailed["data"] if r["product_id"] == p.id]
    assert row["quantity_on_hand"] == ON_HAND
    assert row["open_so_qty"] == OPEN_SO
    assert row["sellable"] == ON_HAND - OPEN_SO

    # Compact contact on the same warehouse: the summary block the chatbot prints for staff.
    compact_contact = _contact(db)
    _policy_row(db, mode="compact", warehouse_ids=[wh.id], contact=compact_contact)
    db.flush()
    service = StockService(db)
    compact = json.loads(
        _with_sellable(
            service, service.list_stock(product_ids=[p.id], contact_id=compact_contact.id)
        ).body
    )
    (summary,) = [e for e in compact["stock_summary"] if e["product_id"] == p.id]
    assert summary["total_on_hand"] == ON_HAND
    assert summary["open_so_qty"] == OPEN_SO
    assert summary["sellable"] == ON_HAND - OPEN_SO
    (location,) = summary["locations"]
    assert location["quantity_on_hand"] == ON_HAND
    assert location["open_so_qty"] == OPEN_SO
