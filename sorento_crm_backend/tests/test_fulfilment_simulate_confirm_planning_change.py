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


def test_a_raw_date_in_a_batch_composition_is_named_by_class_and_line(api, monkeypatch):
    """AC-S6 on the batch path (SO422492, #1584): a composition holding a raw `date` makes
    the JSONB flush raise `TypeError: Object of type date is not JSON serializable`
    (wrapped as a StatementError). The result names the exception class, `simulated_errors`
    names ONLY the bad line, and every batch row is still pending afterwards."""
    import re
    from datetime import date

    from app.models.planning_change import PlanningChangeRow
    from app.services import planning_change_service
    from app.services.user_service import UserPermissionService

    monkeypatch.setattr(
        UserPermissionService, "get_user_role_slugs", lambda self, user_id: {"admin"}
    )
    fixture = _form_three(api)
    db = fixture["world"].db
    line_1, line_2, _line_3 = fixture["lines"]
    original = planning_change_service.set_row_decision

    def poisoned(db_, batch_id, row_id, decision, composition=None):
        out = original(db_, batch_id, row_id, decision, composition)
        row = db_.query(PlanningChangeRow).filter(PlanningChangeRow.id == row_id).one()
        if str(row.project_line_id) == str(line_1.id):
            row.composition_json = {
                **(row.composition_json or {}),
                "bad_date": date(2026, 10, 12),
            }
        return out

    monkeypatch.setattr(planning_change_service, "set_row_decision", poisoned)
    before = _snapshot(fixture)
    entry = {
        "pso_id": str(fixture["order"].id),
        "batch_id": str(fixture["batch"].id),
        "lines": [_line_payload(line_1.id, buy_qty="25"), _line_payload(line_2.id)],
    }

    response = fixture["client"].post(URL, json={"orders": [entry]})

    assert response.status_code == 200, response.text
    result = response.json()["results"][0]
    assert result["ok"] is False, result
    assert "date is not JSON serializable" in (result["error"] or "")
    errors = result["simulated_errors"]
    assert isinstance(errors, list) and len(errors) == 1, result
    assert str(errors[0]["project_line_id"]) == str(line_1.id)
    assert re.match(r"^[A-Za-z_][A-Za-z0-9_.]*(Error|Exception): ", errors[0]["error"]), errors
    assert "date is not JSON serializable" in errors[0]["error"]
    assert _snapshot(fixture) == before


def _break_apply_one_order(monkeypatch):
    from app.services import planning_change_service

    def boom(*args, **kwargs):
        raise TypeError("Object of type date is not JSON serializable")

    monkeypatch.setattr(planning_change_service, "_apply_one_order", boom)


def _two_line_entry(fixture):
    line_1, line_2, _line_3 = fixture["lines"]
    return {
        "pso_id": str(fixture["order"].id),
        "batch_id": str(fixture["batch"].id),
        "lines": [_line_payload(line_1.id, buy_qty="25"), _line_payload(line_2.id)],
    }


def test_a_failure_inside_the_apply_savepoint_keeps_its_class_name_when_simulated(
    api, monkeypatch
):
    """AC-S6 on the batch path: `apply` catches the order's exception inside its savepoint
    and records `str(exc)`; a simulation must still say `TypeError: ...`."""
    from app.services.user_service import UserPermissionService

    monkeypatch.setattr(
        UserPermissionService, "get_user_role_slugs", lambda self, user_id: {"admin"}
    )
    fixture = _form_three(api)
    _break_apply_one_order(monkeypatch)
    before = _snapshot(fixture)

    response = fixture["client"].post(URL, json={"orders": [_two_line_entry(fixture)]})

    assert response.status_code == 200, response.text
    result = response.json()["results"][0]
    assert result["ok"] is False, result
    assert (result["error"] or "").startswith("TypeError: "), result["error"]
    assert "date is not JSON serializable" in result["error"]
    errors = result["simulated_errors"]
    assert errors, result
    for entry in errors:
        assert entry["error"].startswith("TypeError: "), entry
    assert _snapshot(fixture) == before


def test_the_same_failure_in_a_real_confirm_all_reports_the_bare_text(api, monkeypatch):
    """No behaviour change outside a simulation: a real press keeps the bare message."""
    fixture = _form_three(api)
    _break_apply_one_order(monkeypatch)

    response = fixture["client"].post(
        f"{BASE}/fulfilment-planning/confirm-all", json={"orders": [_two_line_entry(fixture)]}
    )

    assert response.status_code == 200, response.text
    result = response.json()["results"][0]
    assert result["ok"] is False, result
    assert "date is not JSON serializable" in (result["error"] or ""), result
    assert not (result["error"] or "").startswith("TypeError"), result["error"]
