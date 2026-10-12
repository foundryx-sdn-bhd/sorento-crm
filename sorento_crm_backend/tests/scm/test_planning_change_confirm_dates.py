"""A planning change composition carrying a borrow's dates is storable (HOTFIX-CONFIRM-DATE).

Prod, main 7482bc81d: the board's Confirm on a planning-change batch failed the whole order
with `(builtins.TypeError) Object of type date is not JSON serializable` on the UPDATE of
`projects.planning_change_rows.composition_json`. Both routes that take a composition built
it with a bare `model_dump()` (`_confirm_a_planning_change` and `PUT .../rows/{row_id}`), so a
Borrow component's `donor_required_date` / `arrival_date` reached the JSONB column as Python
`date` objects.

Reuses `test_planning_change_plan_qty.py`'s world: an active Buy decision on a line, then a
qty change and its batch. The composition answers it with one unit borrowed off the group
pool and the rest bought, the borrow carrying both dates.
"""
from __future__ import annotations

from datetime import date, timedelta

from app.models.planning_change import PlanningChangeRow

from tests._pg_fixture import blank_session
from tests.test_planning_changes import BASE, _client, _line_payload, _restore
from tests.scm.test_planning_change_delta_seam import _change_and_batch, _only_row
from tests.scm.test_planning_change_plan_qty import _delivered_world

DONOR_DATE = date.today() + timedelta(days=40)
ARRIVAL_DATE = date.today() + timedelta(days=20)


def _borrow(world) -> dict:
    return {
        "source": "other_location",
        "warehouse_id": str(world["pool"].id),
        "qty": "1",
        "reason": "ZZT borrow with dates",
        "donor_required_date": DONOR_DATE.isoformat(),
        "arrival_date": ARRIVAL_DATE.isoformat(),
    }


def _stored_borrow(db, row_id) -> dict:
    db.expire_all()
    row = db.query(PlanningChangeRow).filter(PlanningChangeRow.id == row_id).one()
    return row.composition_json["borrow"][0]


def test_board_batch_confirm_stores_a_borrow_with_dates():
    """`POST /sales-orders/{pso}/confirm` with the batch id, the line borrowing with both
    dates set: the composition is stored, the dates as ISO strings."""
    with blank_session() as db:
        world = _delivered_world(db, qty_ordered="3210", qty_delivered="0")
        batch = _change_and_batch(db, world, new_qty="3200")
        row = _only_row(db, batch)

        client, originals = _client(db, world["actor"])
        try:
            response = client.post(
                f"{BASE}/sales-orders/{world['order'].id}/confirm",
                json={
                    "batch_id": str(batch.id),
                    "lines": [_line_payload(
                        str(world["line"].id), borrow=[_borrow(world)], buy_qty="3199",
                        buy_reason="ZZT no stock anywhere",
                    )],
                },
            )
        finally:
            _restore(originals)

        assert "not JSON serializable" not in response.text, response.text
        stored = _stored_borrow(db, row.id)
        assert stored["donor_required_date"] == DONOR_DATE.isoformat()
        assert stored["arrival_date"] == ARRIVAL_DATE.isoformat()


def test_row_amend_stores_a_borrow_with_dates():
    """`PUT .../planning-changes/{batch}/rows/{row}` with `decision=amend`: the same
    composition, the same storable dates."""
    with blank_session() as db:
        world = _delivered_world(db, qty_ordered="3210", qty_delivered="0")
        batch = _change_and_batch(db, world, new_qty="3200")
        row = _only_row(db, batch)

        client, originals = _client(db, world["actor"])
        try:
            response = client.put(
                f"{BASE}/planning-changes/{batch.id}/rows/{row.id}",
                json={
                    "decision": "amend",
                    "composition": _line_payload(
                        str(world["line"].id), borrow=[_borrow(world)], buy_qty="3199",
                        buy_reason="ZZT no stock anywhere",
                    ),
                },
            )
        finally:
            _restore(originals)

        assert response.status_code == 200, response.text
        borrow = response.json()["composition"]["borrow"][0]
        assert borrow["donor_required_date"] == DONOR_DATE.isoformat()
        assert borrow["arrival_date"] == ARRIVAL_DATE.isoformat()
        stored = _stored_borrow(db, row.id)
        assert stored["donor_required_date"] == DONOR_DATE.isoformat()
