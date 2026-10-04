"""FULFIL-CONFIRM-SCOPE round 3: a preview on an order with a PENDING planning change.

Today the server answers `ok: false` "Preview is not available for a pending planning
change". Contract: it previews (`ok: true`, `preview: true`, `inquiry_rows` non-empty) and
writes nothing: batch rows keep their `applied_state`, the batch stays unapplied, no
`so_supply_decisions` row exists, and purchasing is not notified.

Its own module because the fixture chain (`api`, `world`, `_form_three`) is the real-DB one
of `tests/test_planning_change_apply_on_board.py`, which clashes by name with the blank-schema
`api` used in `tests/test_confirm_all_preview.py`.
"""
from __future__ import annotations

from app.models.planning_change import PlanningChangeRow
from app.models.project_so import OrderInquiryLink, SOSupplyDecision

from .test_confirm_all_preview import spy_on_purchasing_notifications
from .test_planning_change_apply_on_board import (  # noqa: F401 - fixtures
    BASE,
    _form_three,
    _line_payload,
    api,
    world,
)

URL = f"{BASE}/fulfilment-planning/confirm-all"


def _snapshot(fixture):
    db = fixture["world"].db
    db.expire_all()
    batch = fixture["batch"]
    return {
        "applied_at": batch.applied_at,
        "row_states": sorted(
            (str(r.id), r.applied_state)
            for r in db.query(PlanningChangeRow).filter(PlanningChangeRow.batch_id == batch.id)
        ),
        # A leaked `set_row_decision` write would move these without touching applied_state.
        "row_decisions": sorted(
            (str(r.id), r.decision, repr(r.composition_json), repr(r.result_json), r.applied_reason)
            for r in db.query(PlanningChangeRow).filter(PlanningChangeRow.batch_id == batch.id)
        ),
        "links": db.query(OrderInquiryLink).count(),
        "decisions": db.query(SOSupplyDecision)
        .filter(SOSupplyDecision.project_sales_order_id == fixture["order"].id)
        .count(),
    }


def _preview(fixture, *, per_order):
    order = fixture["order"]
    entry = {
        "pso_id": str(order.id),
        "lines": [_line_payload(fixture["lines"][0].id, buy_qty="25")],
    }
    body = {"orders": [entry], "preview": True}
    if per_order:
        entry["batch_id"] = str(fixture["batch"].id)
    else:
        body["batch_id"] = str(fixture["batch"].id)
    return fixture["client"].post(URL, json=body)


def _check(api, monkeypatch, *, per_order):
    fixture = _form_three(api)
    calls = spy_on_purchasing_notifications(fixture["world"], monkeypatch)
    before = _snapshot(fixture)
    assert before["applied_at"] is None

    response = _preview(fixture, per_order=per_order)

    assert response.status_code == 200, response.text
    result = response.json()["results"][0]
    assert result["ok"] is True, result
    assert result["preview"] is True
    assert result["inquiry_rows"], result
    assert _snapshot(fixture) == before, "a preview applies nothing"
    assert calls == [], f"a preview notifies nobody: {[c.get('title') for c in calls]}"


def test_a_pending_planning_change_previews_with_the_per_order_batch_id(api, monkeypatch):
    _check(api, monkeypatch, per_order=True)


def test_a_pending_planning_change_previews_with_the_body_level_batch_id(api, monkeypatch):
    _check(api, monkeypatch, per_order=False)
