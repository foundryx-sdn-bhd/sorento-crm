"""PLANNING-WHY R1 + R2: a refused Confirm names each line (admin only) and is recorded.

Contract (documentation/plans/scm/planning-why-12oct-acceptance-criteria.md):
AC-W1 `SupplyFailingLine` gains optional `reason_code` and `change`.
AC-W2 admin: the "Nothing on this planning change could be confirmed." answer carries one
      `failing_lines` entry per batch row that explains it (not_decided / changed_again /
      already_confirmed / line_cancelled), with so_line_no, item_code, reason_code, reason, change.
AC-W3 admin, nothing pending at all: "Every change on this order is already confirmed (revision N)."
AC-W4 non-admin: today's answer, no failing_lines, no reason_code.
AC-W5 holds for confirm-all (preview true and false) and POST /sales-orders/{id}/confirm (422 body).
AC-W6 one `CONFIRM_REFUSED` audit row per refused order (admin and non-admin), on its own session.
AC-W7 none on success, none for simulate-confirm.

How a refusal is reached here: the press names NO line (`lines: []`) on an order whose batch
rows are still pending, so the apply has nothing to confirm. The order's rollback also reverts
any decision the press stored, which is why the row reads "Not decided" afterwards.
Real-DB fixture chain of tests/test_planning_change_apply_on_board.py.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.database import engine
from app.models.audit import AuditLog
from app.models.planning_change import PlanningChangeRow
from app.services.scm.outstanding_diff import DATE_MOVED, QTY_CHANGED

from .test_planning_change_apply_on_board import (  # noqa: F401 - fixtures
    BASE,
    NOW,
    WAS_1,
    _build,
    _change,
    _confirm,
    _core_line,
    _core_so,
    _line_payload,
    _project_line,
    _project_so,
    api,
    world,
)

ERROR = "Nothing on this planning change could be confirmed."
CONFIRM_ALL = f"{BASE}/fulfilment-planning/confirm-all"
SIMULATE = f"{BASE}/fulfilment-planning/simulate-confirm"


@pytest.fixture(autouse=True)
def _cleanup_refusal_audit_rows():
    """The refusal row is written on its own session and really commits; remove it."""
    seen: list[str] = []
    yield seen
    if not seen:
        return
    try:
        with engine.begin() as conn:
            # audit_logs is append-only: the cleanup runs under the maintenance flag.
            conn.execute(text("SET LOCAL sorento.audit_maintenance = 'on'"))
            conn.execute(
                text("DELETE FROM audit_logs WHERE event = 'CONFIRM_REFUSED' AND entity_id = ANY(:ids)"),
                {"ids": seen},
            )
    except DBAPIError:
        # A CREATEROLE non-superuser login (the local test DB's) is refused the flag by the
        # trigger; the rows carry fresh random order ids, so leaving them is harmless.
        pass


def _as_role(monkeypatch, *slugs):
    from app.services.user_service import UserPermissionService

    monkeypatch.setattr(
        UserPermissionService, "get_user_role_slugs", lambda self, user_id: set(slugs)
    )


def _order_with_change(api, *, kind=QTY_CHANGED, line_no=592, seen=None):
    """Line `line_no` confirmed as Buy 25 (revision 1), then the book moves it; one pending
    batch row with decision NULL."""
    client, world = api
    db = world.db
    core_so = _core_so(db, world.company_id)
    core = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="25",
                      required_date=WAS_1)
    order = _project_so(db, world.project, so_id=core_so.id,
                        autocount_doc_no=core_so.so_number)
    line = _project_line(db, order, line_no=line_no, product=world.product, core_line=core)
    db.commit()
    assert _confirm(client, order.id, [_line_payload(line.id, buy_qty="25")]).status_code == 200
    if kind == QTY_CHANGED:
        core.qty_ordered = Decimal("22")
        line.qty = Decimal("22")
        change = _change(kind, core, so_number=core_so.so_number, old_date=WAS_1,
                         new_date=WAS_1, old_qty="25", new_qty="22")
    else:
        core.required_date = NOW
        line.delivery_date = NOW
        change = _change(kind, core, so_number=core_so.so_number, old_date=WAS_1,
                         new_date=NOW, old_qty="25", new_qty="25")
    db.commit()
    batch = _build(world, [change], core_so, [str(core.id)])
    assert batch is not None
    if seen is not None:
        seen.append(str(order.id))
    row = db.query(PlanningChangeRow).filter(PlanningChangeRow.batch_id == batch.id).one()
    return {"client": client, "world": world, "order": order, "core": core, "line": line,
            "batch": batch, "row": row, "core_so": core_so}


def _press(fx, *, preview, batch=True, lines=None):
    entry = {"pso_id": str(fx["order"].id), "lines": lines or []}
    if batch:
        entry["batch_id"] = str(fx["batch"].id)
    return fx["client"].post(CONFIRM_ALL, json={"orders": [entry], "preview": preview})


def _result(response):
    assert response.status_code == 200, response.text
    return response.json()["results"][0]


def _audit_rows(fx):
    db = fx["world"].db
    db.expire_all()
    return (
        db.query(AuditLog)
        .filter(AuditLog.event == "CONFIRM_REFUSED", AuditLog.entity_id == str(fx["order"].id))
        .all()
    )


# --------------------------------------------------------------------------- AC-W1


def test_supply_failing_line_gains_optional_reason_code_and_change():
    from app.schemas.project_supply import SupplyFailingLine

    old = SupplyFailingLine(so_line_no=1, item_code="X", reason="r")
    assert old.reason_code is None and old.change is None
    new = SupplyFailingLine(so_line_no=1, item_code="X", reason="r",
                            reason_code="not_decided", change="Qty 25 -> 22")
    assert new.model_dump()["reason_code"] == "not_decided"
    assert new.model_dump()["change"] == "Qty 25 -> 22"


# --------------------------------------------------------------------------- AC-W2 / W5


@pytest.mark.parametrize("preview", [True, False])
def test_admin_refusal_names_the_undecided_line(api, monkeypatch, preview, _cleanup_refusal_audit_rows):
    _as_role(monkeypatch, "admin")
    fx = _order_with_change(api, seen=_cleanup_refusal_audit_rows)

    result = _result(_press(fx, preview=preview))

    assert result["ok"] is False
    assert result["error"] == ERROR
    lines = result["failing_lines"]
    assert len(lines) == 1, lines
    assert lines[0]["so_line_no"] == 592
    assert lines[0]["item_code"] == fx["row"].item_code
    assert lines[0]["reason_code"] == "not_decided"
    assert lines[0]["reason"] == "Not decided"
    assert lines[0]["change"] == "Qty 25 -> 22"


def test_superadmin_is_an_admin_too(api, monkeypatch, _cleanup_refusal_audit_rows):
    _as_role(monkeypatch, "superadmin")
    fx = _order_with_change(api, seen=_cleanup_refusal_audit_rows)
    result = _result(_press(fx, preview=True))
    assert result["failing_lines"][0]["reason_code"] == "not_decided"


def test_a_date_change_reads_as_a_dd_mm_yyyy_date(api, monkeypatch, _cleanup_refusal_audit_rows):
    _as_role(monkeypatch, "admin")
    fx = _order_with_change(api, kind=DATE_MOVED, seen=_cleanup_refusal_audit_rows)
    result = _result(_press(fx, preview=False))
    assert result["failing_lines"][0]["change"] == "Date 25/08/2026 -> 19/08/2026"


def test_a_superseded_row_reads_changed_again(api, monkeypatch, _cleanup_refusal_audit_rows):
    _as_role(monkeypatch, "admin")
    fx = _order_with_change(api, seen=_cleanup_refusal_audit_rows)
    fx["row"].applied_state = "superseded"
    fx["world"].db.commit()

    result = _result(_press(fx, preview=False))

    assert result["error"] == ERROR
    line = result["failing_lines"][0]
    assert line["reason_code"] == "changed_again"
    assert line["reason"] == "Changed again"
    assert line["change"] == "Qty 25 -> 22"


def test_a_row_overtaken_by_a_newer_revision_reads_already_confirmed(
    api, monkeypatch, _cleanup_refusal_audit_rows
):
    _as_role(monkeypatch, "admin")
    fx = _order_with_change(api, seen=_cleanup_refusal_audit_rows)
    # Held against revision 0 while the order is at revision 1: overtaken. A stored decision
    # makes the apply look at it (it is then superseded inside the order's rollback).
    fx["row"].held_json = {"revision_no": 0}
    fx["row"].decision = "confirm"
    fx["world"].db.commit()

    result = _result(_press(fx, preview=False))

    assert result["error"] == ERROR
    line = result["failing_lines"][0]
    assert line["reason_code"] == "already_confirmed"
    assert line["reason"] == "Already confirmed in revision 1"
    assert line["change"] is None


def test_a_line_cancelled_in_the_book_reads_line_cancelled(
    api, monkeypatch, _cleanup_refusal_audit_rows
):
    _as_role(monkeypatch, "admin")
    fx = _order_with_change(api, seen=_cleanup_refusal_audit_rows)
    fx["row"].decision = "confirm"
    fx["core"].line_status = "cancelled"
    fx["world"].db.commit()

    result = _result(_press(fx, preview=False))

    assert result["error"] == ERROR
    line = result["failing_lines"][0]
    assert line["reason_code"] == "line_cancelled"
    assert line["reason"] == "Line cancelled"
    assert line["change"] is None


# --------------------------------------------------------------------------- AC-W3


def test_admin_on_an_order_with_nothing_pending_hears_it_is_already_confirmed(
    api, monkeypatch, _cleanup_refusal_audit_rows
):
    _as_role(monkeypatch, "admin")
    fx = _order_with_change(api, seen=_cleanup_refusal_audit_rows)
    # The batch belongs to another order; this one has no row in it at all.
    other = _order_with_change(api, line_no=7, seen=_cleanup_refusal_audit_rows)
    fx["batch"] = other["batch"]

    result = _result(_press(fx, preview=False))

    assert result["ok"] is False
    assert result["error"] == "Every change on this order is already confirmed (revision 1)."
    assert not result.get("failing_lines")


# --------------------------------------------------------------------------- AC-W4


@pytest.mark.parametrize("preview", [True, False])
def test_non_admin_gets_todays_answer_and_no_line_detail(
    api, monkeypatch, preview, _cleanup_refusal_audit_rows
):
    _as_role(monkeypatch, "user")
    fx = _order_with_change(api, seen=_cleanup_refusal_audit_rows)

    result = _result(_press(fx, preview=preview))

    assert result["ok"] is False
    assert result["error"] == ERROR
    assert not result.get("failing_lines")


# --------------------------------------------------------------------------- AC-W5 single confirm


def test_single_confirm_422_body_names_the_line_for_an_admin(
    api, monkeypatch, _cleanup_refusal_audit_rows
):
    _as_role(monkeypatch, "admin")
    fx = _order_with_change(api, seen=_cleanup_refusal_audit_rows)

    response = _confirm(fx["client"], fx["order"].id, [], batch_id=str(fx["batch"].id))

    assert response.status_code == 422, response.text
    body = response.json()
    assert body["message"] == ERROR
    assert body["code"] == "planning_change_not_confirmed"
    assert body["failing_lines"][0]["reason_code"] == "not_decided"
    assert body["failing_lines"][0]["change"] == "Qty 25 -> 22"


def test_single_confirm_422_body_is_todays_for_a_non_admin(
    api, monkeypatch, _cleanup_refusal_audit_rows
):
    _as_role(monkeypatch, "user")
    fx = _order_with_change(api, seen=_cleanup_refusal_audit_rows)

    response = _confirm(fx["client"], fx["order"].id, [], batch_id=str(fx["batch"].id))

    assert response.status_code == 422, response.text
    body = response.json()
    assert body["message"] == ERROR
    assert not body.get("failing_lines")


# --------------------------------------------------------------------------- AC-W6


@pytest.mark.parametrize("role,preview", [("admin", True), ("admin", False), ("user", False)])
def test_a_refused_order_writes_exactly_one_confirm_refused_row(
    api, monkeypatch, role, preview, _cleanup_refusal_audit_rows
):
    _as_role(monkeypatch, role)
    fx = _order_with_change(api, seen=_cleanup_refusal_audit_rows)

    result = _result(_press(fx, preview=preview))
    assert result["ok"] is False

    rows = _audit_rows(fx)
    assert len(rows) == 1, rows
    row = rows[0]
    # prod's audit_logs_action_check admits only these actions: the name rides in `event`.
    from .test_planning_why_cancel_cause import allowed_audit_actions

    assert row.action == "EVENT"
    assert row.event == "CONFIRM_REFUSED"
    assert row.action in allowed_audit_actions()
    assert row.entity_type == "project_sales_orders"
    assert row.entity_id == str(fx["order"].id)
    assert str(row.user_id) == str(fx["world"].actor)
    assert row.root_entity_type == "project_sales_orders"
    assert str(row.root_entity_id) == str(fx["order"].id)
    assert row.reason == ERROR
    values = row.new_values
    assert values["preview"] is preview
    assert values["batch_id"] == str(fx["batch"].id)
    # Filled for admin AND non-admin: the gate only limits what the response shows.
    assert values["lines"] == [
        {
            "so_line_no": 592,
            "item_code": fx["row"].item_code,
            "reason_code": "not_decided",
            "reason": "Not decided",
            "change": "Qty 25 -> 22",
        }
    ]


def test_single_confirm_refusal_is_recorded_too(api, monkeypatch, _cleanup_refusal_audit_rows):
    _as_role(monkeypatch, "admin")
    fx = _order_with_change(api, seen=_cleanup_refusal_audit_rows)

    assert _confirm(fx["client"], fx["order"].id, [], batch_id=str(fx["batch"].id)).status_code == 422

    rows = _audit_rows(fx)
    assert len(rows) == 1
    assert rows[0].new_values["preview"] is False
    assert str(rows[0].user_id) == str(fx["world"].actor)


def test_two_refused_orders_write_two_rows(api, monkeypatch, _cleanup_refusal_audit_rows):
    _as_role(monkeypatch, "admin")
    first = _order_with_change(api, seen=_cleanup_refusal_audit_rows)
    second = _order_with_change(api, line_no=8, seen=_cleanup_refusal_audit_rows)

    response = first["client"].post(CONFIRM_ALL, json={"orders": [
        {"pso_id": str(first["order"].id), "batch_id": str(first["batch"].id), "lines": []},
        {"pso_id": str(second["order"].id), "batch_id": str(second["batch"].id), "lines": []},
    ]})

    assert [r["ok"] for r in response.json()["results"]] == [False, False]
    assert len(_audit_rows(first)) == 1
    assert len(_audit_rows(second)) == 1


# --------------------------------------------------------------------------- AC-W7


def test_a_successful_order_writes_no_confirm_refused_row(
    api, monkeypatch, _cleanup_refusal_audit_rows
):
    _as_role(monkeypatch, "admin")
    fx = _order_with_change(api, seen=_cleanup_refusal_audit_rows)

    response = _press(fx, preview=False, lines=[_line_payload(fx["line"].id, buy_qty="22")])

    assert _result(response)["ok"] is True
    assert _audit_rows(fx) == []


def test_simulate_confirm_writes_no_confirm_refused_row(
    api, monkeypatch, _cleanup_refusal_audit_rows
):
    _as_role(monkeypatch, "admin")
    fx = _order_with_change(api, seen=_cleanup_refusal_audit_rows)

    response = fx["client"].post(SIMULATE, json={"orders": [
        {"pso_id": str(fx["order"].id), "batch_id": str(fx["batch"].id), "lines": []}
    ]})

    assert response.status_code == 200, response.text
    assert response.json()["results"][0]["ok"] is False
    assert _audit_rows(fx) == []


def test_a_failure_to_write_the_row_never_changes_the_answer(
    api, monkeypatch, _cleanup_refusal_audit_rows
):
    _as_role(monkeypatch, "admin")
    fx = _order_with_change(api, seen=_cleanup_refusal_audit_rows)
    import app.api.v1.projects.fulfilment_planning as module

    # Whatever the recorder is called, breaking audit writes for CONFIRM_REFUSED must not leak.
    from app.services import audit_service

    def boom(*args, **kwargs):
        raise RuntimeError("audit store down")

    monkeypatch.setattr(audit_service, "log_audit", boom, raising=False)
    monkeypatch.setattr(module, "log_audit", boom, raising=False)

    result = _result(_press(fx, preview=False))

    assert result["ok"] is False
    assert result["error"] == ERROR


# --------------------------------------------------------------------------- fix round 1


def test_simulate_confirm_never_calls_the_refusal_recorder(
    api, monkeypatch, _cleanup_refusal_audit_rows
):
    """GUARD (passes while AC-W7 holds): the outcome test alone cannot tell "not called" from
    "called and wrote nothing"."""
    import app.api.v1.projects.fulfilment_planning as module

    _as_role(monkeypatch, "admin")
    fx = _order_with_change(api, seen=_cleanup_refusal_audit_rows)
    calls: list = []
    monkeypatch.setattr(module, "_record_refusals", lambda *a, **k: calls.append((a, k)))

    response = fx["client"].post(SIMULATE, json={"orders": [
        {"pso_id": str(fx["order"].id), "batch_id": str(fx["batch"].id), "lines": []}
    ]})

    assert response.status_code == 200, response.text
    assert calls == []


def test_a_refusal_row_carries_the_orders_company(api, monkeypatch, _cleanup_refusal_audit_rows):
    """AC-W7 company scope: a scoped user must see the order's refusals."""
    _as_role(monkeypatch, "admin")
    fx = _order_with_change(api, seen=_cleanup_refusal_audit_rows)

    assert _result(_press(fx, preview=False))["ok"] is False

    fx["world"].db.expire_all()
    rows = fx["world"].db.query(AuditLog).filter(
        AuditLog.entity_id == str(fx["order"].id),
        (AuditLog.event == "CONFIRM_REFUSED") | (AuditLog.action == "CONFIRM_REFUSED"),
    ).all()
    assert len(rows) == 1
    assert fx["order"].company_id is not None
    assert str(rows[0].company_id) == str(fx["order"].company_id)


def test_a_batch_id_that_is_not_a_uuid_is_never_written_to_the_audit_row():
    from app.api.v1.projects.fulfilment_planning import _record_refusals

    import uuid

    order_id = str(uuid.uuid4())
    try:
        _record_refusals(
            [{"pso_id": order_id, "error": ERROR, "batch_id": "not-a-uuid", "lines": []}],
            None, False,
        )
        with engine.connect() as conn:
            stored = conn.execute(
                text("SELECT new_values->>'batch_id' FROM audit_logs "
                     "WHERE (event = 'CONFIRM_REFUSED' OR action = 'CONFIRM_REFUSED') "
                     "AND entity_id = :id"),
                {"id": order_id},
            ).all()
        assert len(stored) == 1, "the refusal row was not written"
        assert stored[0][0] is None, stored
    finally:
        try:
            with engine.begin() as conn:
                conn.execute(text("SET LOCAL sorento.audit_maintenance = 'on'"))
                conn.execute(text("DELETE FROM audit_logs WHERE entity_id = :id "
                                  "AND (event = 'CONFIRM_REFUSED' OR action = 'CONFIRM_REFUSED')"),
                             {"id": order_id})
        except DBAPIError:
            pass


@pytest.mark.parametrize("role", ["admin", "user"])
def test_a_failing_explanation_still_answers_todays_refusal_on_confirm_all(
    api, monkeypatch, role, _cleanup_refusal_audit_rows
):
    from app.services import planning_change_service

    _as_role(monkeypatch, role)
    fx = _order_with_change(api, seen=_cleanup_refusal_audit_rows)

    def boom(*args, **kwargs):
        raise RuntimeError("explain failed")

    monkeypatch.setattr(planning_change_service, "explain_refusal", boom)

    result = _result(_press(fx, preview=False))

    assert result["ok"] is False
    assert result["error"] == ERROR
    assert not result.get("failing_lines")


@pytest.mark.parametrize("role", ["admin", "user"])
def test_a_failing_explanation_still_answers_422_on_single_confirm(
    api, monkeypatch, role, _cleanup_refusal_audit_rows
):
    from app.services import planning_change_service

    _as_role(monkeypatch, role)
    fx = _order_with_change(api, seen=_cleanup_refusal_audit_rows)

    def boom(*args, **kwargs):
        raise RuntimeError("explain failed")

    monkeypatch.setattr(planning_change_service, "explain_refusal", boom)

    response = _confirm(fx["client"], fx["order"].id, [], batch_id=str(fx["batch"].id))

    assert response.status_code == 422, response.text
    assert response.json()["message"] == ERROR
