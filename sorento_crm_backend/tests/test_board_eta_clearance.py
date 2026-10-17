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

import json
from datetime import date, timedelta

import pytest

from app.services.field_access import CLEARANCE_PERMISSION

from ._pg_fixture import blank_session
from .test_fulfilment_board import (
    BASE,
    MARKER,
    TODAY,
    VIEW,
    _adopt,
    _client,
    _confirm,
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
from .test_ladder_v5_proof import WHEN

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


# A line an active decision covers still carries a LIVE suggestion beside what was frozen
# (`FulfilmentBoardService._suggest_live_for_covered`), worked out by a second supply
# service. That suggestion prints the SPO's arrival as a field and inside its reason
# sentence, so it is held to the same rule as the board's own incoming refs.

COVERED_PUBLISHED = WHEN - timedelta(days=5)
COVERED_DELAYED = WHEN - timedelta(days=2)


def _human(day: date) -> str:
    """The reason sentence's form of a date, "1 Sep 2026"."""
    return f"{day.day} {day:%b %Y}"


def _covered_board(db, *, reveal_eta_delay: bool) -> str:
    """One line covered by timely SPO water at a "-BB" group site, the board as JSON text.

    The SPO is published before the line's required date and delayed to a date still
    before it, so the water is on time either way and the live ladder draws it whichever
    date it reads.
    """
    from app.models.procurement import InboundShipment
    from app.models.project_so import ProjectSalesOrderLine
    from app.services.project_fulfilment_board_service import FulfilmentBoardService

    product = _product(db, f"ZZT-{_uid()[:6]}")
    own = _warehouse(db, f"ZZTQ{_uid()[:5]}-BB"[:20])
    _stock(db, product, own, on_hand=0)
    spo = _incoming(
        db, product, own,
        spo_number="ZZT-SPO-ETA2", allocated=15, received=0, arrives=COVERED_PUBLISHED,
    )
    db.get(InboundShipment, spo.inbound_shipment_id).eta_delay_date = COVERED_DELAYED
    order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
    core = _line(db, order, product, qty="15", required_date=WHEN, warehouse=own)
    pso_id = _adopt(db, str(order.id))
    mirror = (
        db.query(ProjectSalesOrderLine)
        .filter(
            ProjectSalesOrderLine.project_sales_order_id == pso_id,
            ProjectSalesOrderLine.core_sales_order_line_id == core.id,
        )
        .first()
    )
    _confirm(
        db, pso_id, _user(db, f"{MARKER} planner"),
        [{"project_line_id": str(mirror.id), "timely_spo_qty": "15"}],
    )
    db.commit()

    board = FulfilmentBoardService(db, reveal_eta_delay=reveal_eta_delay).build(
        [order.so_number], granularity="week", as_of=TODAY
    )
    covered = [
        contribution
        for cell in board["cells"]
        for contribution in cell["contributions"]
        if contribution.get("covered")
    ]
    assert covered, "the world must produce a covered line"
    return json.dumps(board, default=str)


@pytest.mark.usefixtures("ladder_steps_on")
def test_a_covered_lines_live_suggestion_shows_the_delay_to_a_viewer_with_view_clearance():
    with blank_session() as db:
        text = _covered_board(db, reveal_eta_delay=True)

        # Proves the world reaches the path: with the field allowed, the delay is printed.
        assert COVERED_DELAYED.isoformat() in text
        assert _human(COVERED_DELAYED) in text


@pytest.mark.usefixtures("ladder_steps_on")
def test_a_covered_lines_live_suggestion_hides_the_delay_without_view_clearance():
    with blank_session() as db:
        text = _covered_board(db, reveal_eta_delay=False)

        assert COVERED_DELAYED.isoformat() not in text
        assert _human(COVERED_DELAYED) not in text


def _proposals(body):
    return [
        (
            contribution["so_number"],
            contribution["qty_proposed_reserve"],
            contribution["qty_proposed_incoming"],
            contribution["qty_proposed_buy"],
        )
        for contribution in body["contributions"]
    ]


@pytest.mark.usefixtures("ladder_steps_on")
def test_both_viewers_get_the_same_proposal_because_the_engine_plans_on_the_delay():
    """Crew answer (b), 17 Oct 2026: only the PRINTED date depends on the viewer.

    Published 25 Aug is before the line's 3 Sep required date, the delay to 10 Sep is after
    it, and the 15 on the water is the whole of what the line owes. Planned on the published
    date the water would read as timely for one viewer and late for the other, and that
    viewer's suggestion would be refused by the Save.
    """
    from app.models.procurement import InboundShipment

    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        warehouse = _warehouse(db, f"ZZT-{_uid()[:6]}"[:20])
        _stock(db, product, warehouse, on_hand=0)
        spo = _incoming(
            db, product, warehouse,
            spo_number="ZZT-SPO-ETA3", allocated=15, received=0, arrives=PUBLISHED,
        )
        db.get(InboundShipment, spo.inbound_shipment_id).eta_delay_date = DELAYED
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, order, product, qty="15", required_date=date(2026, 9, 3), warehouse=warehouse)
        db.flush()

        allowed = _board(db, order, [VIEW, CLEARANCE_PERMISSION])
        denied = _board(db, order, [VIEW])

        assert _proposals(allowed.json()) == _proposals(denied.json())
        assert _proposals(denied.json())[0][2] == "0"
        # The sentences are worded with the date each viewer may see.
        assert _human(DELAYED) in allowed.text
        assert _human(DELAYED) not in denied.text
        assert _human(PUBLISHED) in denied.text
