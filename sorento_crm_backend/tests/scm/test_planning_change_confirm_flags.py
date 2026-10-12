"""A planning change Confirm keeps the planner's order-back and investigation flag.

Follow-up to HOTFIX-CONFIRM-DATE (#1584). The board's Confirm on a planning-change batch posts
full `ConfirmLine`s, but `_validate_composition_shape` stored only the components, the reasons
and the quantities, and `_to_confirm_line` rebuilt the line from that. `order_back`,
`cited_document` and `suspected_system_issue` were dropped on the way, so the revision froze
`order_back: false` and `suspected_system_issue: false` whatever the planner ticked.

Reuses `test_planning_change_confirm_dates.py`'s world: an active Buy decision on a line, then
a qty change and its batch.
"""
from __future__ import annotations

from tests._pg_fixture import blank_session
from tests.test_planning_changes import BASE, _client, _line_payload, _restore
from tests.scm.test_planning_change_delta_seam import (
    _active_decision,
    _change_and_batch,
    _only_row,
)
from tests.scm.test_planning_change_plan_qty import _delivered_world

CITED = "SPO-ZZT-2026/10-0001"


def _flagged_buy(project_line_id: str) -> dict:
    return {
        **_line_payload(
            project_line_id, buy_qty="3200", buy_reason="ZZT order back the whole line",
            amend_reason="ZZT the availability reads wrong",
        ),
        "order_back": True,
        "cited_document": CITED,
        "suspected_system_issue": True,
    }


def _frozen_line(db, world) -> dict:
    db.expire_all()
    decision = _active_decision(db, world["order"].id)
    return next(
        s for s in decision.line_snapshots if s["project_line_id"] == str(world["line"].id)
    )


def _assert_flags_kept(snapshot: dict) -> None:
    assert snapshot["order_back"] is True, snapshot
    assert snapshot["cited_document"] == CITED, snapshot
    assert snapshot["suspected_system_issue"] is True, snapshot


def test_board_batch_confirm_keeps_order_back_and_the_investigation_flag():
    """`POST /sales-orders/{pso}/confirm` with the batch id: the revision it writes freezes
    the order-back, its cited document and the flag the planner ticked."""
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
                    "lines": [_flagged_buy(str(world["line"].id))],
                },
            )
        finally:
            _restore(originals)

        assert response.status_code == 200, response.text
        db.expire_all()
        stored = db.get(type(row), row.id).composition_json
        assert stored["order_back"] is True
        assert stored["cited_document"] == CITED
        assert stored["suspected_system_issue"] is True
        _assert_flags_kept(_frozen_line(db, world))


def test_row_amend_then_apply_keeps_order_back_and_the_investigation_flag():
    """`PUT .../planning-changes/{batch}/rows/{row}` with `decision=amend` and the flags, then
    Apply: the revision freezes them too."""
    with blank_session() as db:
        world = _delivered_world(db, qty_ordered="3210", qty_delivered="0")
        batch = _change_and_batch(db, world, new_qty="3200")
        row = _only_row(db, batch)

        client, originals = _client(db, world["actor"])
        try:
            response = client.put(
                f"{BASE}/planning-changes/{batch.id}/rows/{row.id}",
                json={"decision": "amend", "composition": _flagged_buy(str(world["line"].id))},
            )
            assert response.status_code == 200, response.text
            applied = client.post(f"{BASE}/planning-changes/{batch.id}/apply")
        finally:
            _restore(originals)

        assert applied.status_code == 200, applied.text
        assert applied.json()["failed_orders"] == [], applied.text
        _assert_flags_kept(_frozen_line(db, world))
