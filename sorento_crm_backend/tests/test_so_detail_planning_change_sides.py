"""SO-LINE-CHANGE-HISTORY F1, AC-1 and AC-2 (red-first).

`GET /api/v1/scm/sales-orders/{id}` carries, on each line's `planning_change`, the batch
row's two sides (`from` / `to`) so the SO detail's What-changed dialog can print a Was | Now
table. Asserted on the JSON on the wire: `response_model` silently drops undeclared fields,
so a service return value would prove nothing.
"""
from __future__ import annotations

from tests.test_planning_change_apply_on_board import (  # noqa: F401
    _form_three,
    api,
    world,
)


def _row_of(world, core_line):
    from app.models.planning_change import PlanningChangeRow

    return (
        world.db.query(PlanningChangeRow)
        .filter(PlanningChangeRow.core_line_id == core_line.id)
        .one()
    )


def _read_line(fixture, core_line) -> dict:
    client = fixture["client"]
    detail = client.get(f"/api/v1/scm/sales-orders/{fixture['core_so'].id}")
    assert detail.status_code == 200, detail.text
    lines = {ln["id"]: ln for ln in detail.json()["lines"]}
    return lines[str(core_line.id)]


def test_ac1_planning_change_carries_from_and_to_on_the_wire(api):
    fixture = _form_three(api)
    world = fixture["world"]
    core_1 = fixture["core_lines"][0]
    row = _row_of(world, core_1)
    row.from_json = {"qty": "182", "required_date": "2026-06-01"}
    row.to_json = {"qty": "280", "required_date": "2027-03-01"}
    world.db.commit()

    change = _read_line(fixture, core_1)["planning_change"]
    assert change["id"] == str(row.id)
    assert change["kind"] == row.kind
    assert change["applied_state"] == row.applied_state
    assert "result" in change
    assert set(change["from"]) == {"qty", "required_date", "status", "item_code"}, change
    assert set(change["to"]) == {"qty", "required_date", "status", "item_code"}, change
    assert change["from"]["qty"] == "182"
    assert change["from"]["required_date"] == "2026-06-01"
    assert change["to"]["qty"] == "280"
    assert change["to"]["required_date"] == "2027-03-01"


def test_ac2_null_and_empty_sides_read_as_null_with_other_keys_intact(api):
    fixture = _form_three(api)
    world = fixture["world"]
    core_1, core_2 = fixture["core_lines"][0], fixture["core_lines"][1]
    row_null = _row_of(world, core_1)
    row_empty = _row_of(world, core_2)
    row_null.from_json = None
    row_null.to_json = None
    row_empty.from_json = {}
    row_empty.to_json = {}
    world.db.commit()

    for core, row in ((core_1, row_null), (core_2, row_empty)):
        change = _read_line(fixture, core)["planning_change"]
        assert "from" in change and "to" in change, change
        assert change["from"] is None, change
        assert change["to"] is None, change
        assert change["id"] == str(row.id)
        assert change["kind"] == row.kind
        assert change["applied_state"] == row.applied_state
