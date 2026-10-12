"""Board Simulate confirm, AC-S3: an order answering a pending planning-change batch is
simulated through the batch apply and nothing sticks.

RED today for the right reason: `POST .../fulfilment-planning/simulate-confirm` is a 404.

Its own module because the fixture chain (`api`, `world`, `_form_three`) is the real-DB one of
`tests/test_planning_change_apply_on_board.py`, which clashes by name with the blank-schema
`api` of `tests/test_fulfilment_simulate_confirm.py`.
"""
from __future__ import annotations

from .test_confirm_all_preview import spy_on_purchasing_notifications
from .test_confirm_all_preview_planning_change import _snapshot
from .test_planning_change_apply_on_board import (  # noqa: F401 - fixtures
    BASE,
    _form_three,
    _line_payload,
    api,
    world,
)

URL = f"{BASE}/fulfilment-planning/simulate-confirm"


def _simulate(fixture, *, per_order):
    order = fixture["order"]
    entry = {
        "pso_id": str(order.id),
        "lines": [_line_payload(fixture["lines"][0].id, buy_qty="25")],
    }
    body = {"orders": [entry]}
    if per_order:
        entry["batch_id"] = str(fixture["batch"].id)
    else:
        body["batch_id"] = str(fixture["batch"].id)
    return fixture["client"].post(URL, json=body)


def _check(api, monkeypatch, *, per_order):
    from app.services.user_service import UserPermissionService

    monkeypatch.setattr(
        UserPermissionService, "get_user_role_slugs", lambda self, user_id: {"admin"}
    )
    fixture = _form_three(api)
    calls = spy_on_purchasing_notifications(fixture["world"], monkeypatch)
    before = _snapshot(fixture)
    assert before["applied_at"] is None

    response = _simulate(fixture, per_order=per_order)

    assert response.status_code == 200, response.text
    result = response.json()["results"][0]
    assert result["ok"] is True, result
    assert result["preview"] is True
    assert _snapshot(fixture) == before, "a simulation applies nothing; every row stays pending"
    assert calls == [], f"a simulation notifies nobody: {[c.get('title') for c in calls]}"


def test_a_simulated_batch_order_leaves_every_batch_row_pending_per_order_id(api, monkeypatch):
    """AC-S3 (per-order batch_id)."""
    _check(api, monkeypatch, per_order=True)


def test_a_simulated_batch_order_leaves_every_batch_row_pending_body_level_id(api, monkeypatch):
    """AC-S3 (body-level batch_id)."""
    _check(api, monkeypatch, per_order=False)
