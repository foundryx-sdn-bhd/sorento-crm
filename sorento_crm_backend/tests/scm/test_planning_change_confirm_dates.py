"""A planning change composition carrying a borrow's dates is storable (HOTFIX-CONFIRM-DATE).

Prod, main 7482bc81d: the board's Confirm on a planning-change batch failed the whole order
(SO422492, 0 of 1) with `(builtins.TypeError) Object of type date is not JSON serializable`
on the UPDATE of `projects.planning_change_rows.composition_json`. Both routes that take a
composition built it with a bare `model_dump()` (`_confirm_a_planning_change` and
`PUT .../rows/{row_id}`), so a Borrow component's `donor_required_date` / `arrival_date`
reached the JSONB column as Python `date` objects.

Both borrow shapes the owner found on SO422492 are covered: an ORDER borrow naming its donor
line with the donor's date (line 272, the row that failed), and a plain LOCATION borrow with no
donor SO that still carries `donor_required_date` (line 160's saved draft).

Reuses `test_planning_change_plan_qty.py`'s world: an active Buy decision on a line, then a
qty change and its batch.
"""
from __future__ import annotations

from datetime import date, timedelta

from app.models.inventory import Warehouse
from app.models.planning_change import PlanningChangeRow

from tests._pg_fixture import blank_session
from tests.test_planning_changes import BASE, _client, _line_payload, _restore
from tests.test_so_supply_confirmation import _stock, _uid, _warehouse
from tests.scm.test_project_supply_service_ladder import _seed_line
from tests.scm.test_planning_change_delta_seam import _change_and_batch, _only_row
from tests.scm.test_planning_change_plan_qty import _delivered_world

DONOR_DATE = date.today() + timedelta(days=40)
ARRIVAL_DATE = date.today() + timedelta(days=20)


def _location_borrow(warehouse, qty) -> dict:
    """Line 160's shape: a plain location borrow, no donor SO, the donor date still set."""
    return {
        "source": "other_location",
        "warehouse_id": str(warehouse.id),
        "qty": qty,
        "reason": "ZZT location borrow with a date",
        "donor_required_date": DONOR_DATE.isoformat(),
        "arrival_date": ARRIVAL_DATE.isoformat(),
    }


def _order_borrow(warehouse, qty, donor_line, donor_so) -> dict:
    """Line 272's shape: a borrow off a NAMED donor order line, carrying the donor's date."""
    return {
        "source": "other_location",
        "warehouse_id": str(warehouse.id),
        "qty": qty,
        "reason": "ZZT order borrow with a date",
        "donor_core_line_id": str(donor_line.id),
        "donor_so_number": donor_so.so_number,
        "donor_line_no": 1,
        "donor_required_date": DONOR_DATE.isoformat(),
    }


def _sibling_bin(db, world):
    """The MWH site `_group_sites` built in the SAME ownership group as the line's own BRW
    site (`ZZT<SITE>-<GROUP>`), so an order borrow may take another order's committed
    quantity there."""
    group = world["own"].warehouse_code.split("-", 1)[1]
    return db.query(Warehouse).filter(Warehouse.warehouse_code == f"ZZTMWH-{group}").one()


def _stored_borrow(db, row_id) -> list:
    db.expire_all()
    row = db.query(PlanningChangeRow).filter(PlanningChangeRow.id == row_id).one()
    return row.composition_json["borrow"]


def _batch_confirm(db, world, batch, borrow):
    client, originals = _client(db, world["actor"])
    try:
        return client.post(
            f"{BASE}/sales-orders/{world['order'].id}/confirm",
            json={
                "batch_id": str(batch.id),
                "lines": [_line_payload(
                    str(world["line"].id), borrow=borrow,
                    amend_reason="ZZT borrow the whole line",
                )],
            },
        )
    finally:
        _restore(originals)


def test_board_batch_confirm_stores_a_location_borrow_with_a_donor_date():
    """`POST /sales-orders/{pso}/confirm` with the batch id, the whole line borrowed off a
    stocked bin outside its pool, no donor SO but `donor_required_date` set: the order
    confirms and the stored composition carries the dates as ISO strings."""
    with blank_session() as db:
        world = _delivered_world(db, qty_ordered="3210", qty_delivered="0")
        donor_bin = _warehouse(db, f"ZZTDATE{_uid()[:4]}")
        _stock(db, world["product"], donor_bin, on_hand=3200)
        batch = _change_and_batch(db, world, new_qty="3200")
        row = _only_row(db, batch)

        response = _batch_confirm(db, world, batch, [_location_borrow(donor_bin, "3200")])

        assert response.status_code == 200, response.text
        [stored] = _stored_borrow(db, row.id)
        assert stored["donor_so_number"] is None
        assert stored["donor_required_date"] == DONOR_DATE.isoformat()
        assert stored["arrival_date"] == ARRIVAL_DATE.isoformat()


def test_board_batch_confirm_stores_an_order_borrow_with_a_donor_date():
    """The same press, the line split between a location borrow and an ORDER borrow off a
    real donor line at that bin (the donor's date round-tripped): the order confirms and
    both stored components carry their dates as ISO strings."""
    with blank_session() as db:
        world = _delivered_world(db, qty_ordered="3210", qty_delivered="0")
        donor_bin = _sibling_bin(db, world)
        _stock(db, world["product"], donor_bin, on_hand=3200)
        _donor_order, _donor_line, donor_so, donor_core_line = _seed_line(
            db, world["company_id"], world["project"], world["product"], donor_bin,
            qty_ordered="100", line_no=1, so_number=f"ZZT-SO-DON{_uid()[:5]}",
        )
        batch = _change_and_batch(db, world, new_qty="3200")
        row = _only_row(db, batch)

        response = _batch_confirm(db, world, batch, [
            _location_borrow(donor_bin, "3100"),
            _order_borrow(donor_bin, "100", donor_core_line, donor_so),
        ])

        assert response.status_code == 200, response.text
        location, order_borrow = _stored_borrow(db, row.id)
        assert location["donor_required_date"] == DONOR_DATE.isoformat()
        assert order_borrow["donor_so_number"] == donor_so.so_number
        assert order_borrow["donor_core_line_id"] == str(donor_core_line.id)
        assert order_borrow["donor_required_date"] == DONOR_DATE.isoformat()


def test_row_amend_stores_both_borrow_shapes_with_dates():
    """`PUT .../planning-changes/{batch}/rows/{row}` with `decision=amend` takes the same
    composition and stores the same ISO dates."""
    with blank_session() as db:
        world = _delivered_world(db, qty_ordered="3210", qty_delivered="0")
        donor_bin = _warehouse(db, f"ZZTDATE{_uid()[:4]}")
        _donor_order, _donor_line, donor_so, donor_core_line = _seed_line(
            db, world["company_id"], world["project"], world["product"], donor_bin,
            qty_ordered="100", line_no=1, so_number=f"ZZT-SO-DON{_uid()[:5]}",
        )
        batch = _change_and_batch(db, world, new_qty="3200")
        row = _only_row(db, batch)

        client, originals = _client(db, world["actor"])
        try:
            response = client.put(
                f"{BASE}/planning-changes/{batch.id}/rows/{row.id}",
                json={
                    "decision": "amend",
                    "composition": _line_payload(
                        str(world["line"].id),
                        borrow=[
                            _location_borrow(donor_bin, "3100"),
                            _order_borrow(donor_bin, "100", donor_core_line, donor_so),
                        ],
                        amend_reason="ZZT borrow the whole line",
                    ),
                },
            )
        finally:
            _restore(originals)

        assert response.status_code == 200, response.text
        returned = response.json()["composition"]["borrow"]
        assert [b["donor_required_date"] for b in returned] == [DONOR_DATE.isoformat()] * 2
        location, order_borrow = _stored_borrow(db, row.id)
        assert location["arrival_date"] == ARRIVAL_DATE.isoformat()
        assert order_borrow["donor_required_date"] == DONOR_DATE.isoformat()
