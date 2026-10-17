"""The fulfilment board shows the DELAYED ETA only to a viewer allowed that field.

Owner ruling 17 Oct 2026 ("depends on the field allowed"): `eta_delay_date` is a
`procurement.packing_lists.view_clearance` field (permission registry: absent from API
responses without it). A board viewer who holds it sees an incoming SPO dated by the delay;
everyone else sees the published `estimated_arrival_date`, and the delay date appears nowhere
in what they are sent - not as a date, and not through the overdue count worked out from it.

Both reads the board's screen makes are pinned: the board itself and the cell's stock drawer.

Postgres, blank scratch schema, every FK target seeded here (PRINCIPLES).
"""
from __future__ import annotations

from datetime import date

from app.services.field_access import CLEARANCE_PERMISSION

from ._pg_fixture import blank_session
from .test_fulfilment_board import (
    BASE,
    MARKER,
    TODAY,
    VIEW,
    _client,
    _incoming,
    _line,
    _order,
    _product,
    _restore,
    _sorento,
    _stock,
    _uid,
    _user,
    _warehouse,
)

PUBLISHED = date(2026, 8, 25)
DELAYED = date(2026, 9, 10)


def _delayed_world(db):
    product = _product(db, f"ZZT-{_uid()[:6]}")
    warehouse = _warehouse(db, f"ZZT-{_uid()[:6]}"[:20])
    _stock(db, product, warehouse, on_hand=30, reserved=25)
    spo = _incoming(
        db, product, warehouse,
        spo_number="ZZT-SPO-ETA1", allocated=9, received=2, arrives=PUBLISHED,
    )
    from app.models.procurement import InboundShipment

    db.get(InboundShipment, spo.inbound_shipment_id).eta_delay_date = DELAYED
    order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
    _line(
        db, order, product, qty="28", delivered="8",
        required_date=date(2026, 9, 3), warehouse=warehouse,
    )
    db.flush()
    return order, product, warehouse


def _get(db, permissions, path, params):
    from app.models.base import company_scope

    company_id = _sorento(db)
    actor = _user(db, f"{MARKER} Eling")
    db.commit()
    client, originals = _client(db, actor, permissions)
    try:
        with company_scope(db, frozenset({company_id})):
            response = client.get(f"{BASE}{path}", params=params)
    finally:
        _restore(originals)
    assert response.status_code == 200, response.text
    return response


def _board(db, order, permissions):
    return _get(
        db, permissions, "/fulfilment-planning/board",
        {"orders": order.so_number, "granularity": "week", "as_of": TODAY.isoformat()},
    )


def _board_incoming(body):
    return [
        ref
        for cell in body["cells"]
        for location in cell["locations"]
        for ref in location.get("incoming") or []
        if ref["spo_number"] == "ZZT-SPO-ETA1"
    ]


def _drawer(db, product, warehouse, permissions):
    return _get(
        db, permissions, "/fulfilment-planning/stock-detail",
        {"product_id": str(product.id), "warehouse_id": str(warehouse.id)},
    )


def _drawer_incoming(body):
    return [ref for ref in body["incoming"] if ref["spo_number"] == "ZZT-SPO-ETA1"]


def test_a_board_viewer_with_view_clearance_sees_the_delayed_eta():
    with blank_session() as db:
        order, _product_row, _warehouse_row = _delayed_world(db)

        body = _board(db, order, [VIEW, CLEARANCE_PERMISSION]).json()

        refs = _board_incoming(body)
        assert refs and {ref["arrival_date"] for ref in refs} == {DELAYED.isoformat()}


def test_a_board_viewer_without_view_clearance_sees_the_published_eta_only():
    with blank_session() as db:
        order, _product_row, _warehouse_row = _delayed_world(db)

        response = _board(db, order, [VIEW])

        refs = _board_incoming(response.json())
        assert refs and {ref["arrival_date"] for ref in refs} == {PUBLISHED.isoformat()}
        # Absent, not merely re-dated in one place: the delay date is nowhere on the wire.
        assert DELAYED.isoformat() not in response.text


def test_the_stock_drawer_with_view_clearance_dates_the_spo_by_the_delay():
    with blank_session() as db:
        _order_row, product, warehouse = _delayed_world(db)

        body = _drawer(db, product, warehouse, [VIEW, CLEARANCE_PERMISSION]).json()

        refs = _drawer_incoming(body)
        assert [ref["expected_date"] for ref in refs] == [DELAYED.isoformat()]


def test_the_stock_drawer_without_view_clearance_dates_the_spo_by_the_published_eta():
    with blank_session() as db:
        _order_row, product, warehouse = _delayed_world(db)

        response = _drawer(db, product, warehouse, [VIEW])

        refs = _drawer_incoming(response.json())
        assert [ref["expected_date"] for ref in refs] == [PUBLISHED.isoformat()]
        assert DELAYED.isoformat() not in response.text
