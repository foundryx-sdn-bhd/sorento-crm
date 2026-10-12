"""Board Simulate confirm (admin dry run): `POST .../fulfilment-planning/simulate-confirm`.

Plan `documentation/plans/scm/PLAN-board-simulate-confirm-12oct.md`, UAC
`board-simulate-confirm-12oct-acceptance-criteria.md` AC-S1, S2, S4, S5, S6, S7, S8
(AC-S3, the batch path, lives in `test_fulfilment_simulate_confirm_planning_change.py`
because its fixture chain clashes by name with this one).

RED today for the right reason: the route does not exist (404) and
`app.services.confirm_simulation` does not exist (ImportError).

Postgres via `tests/_pg_fixture.py`, fixture chain reused from
`tests/test_so_supply_confirmation.py` and `tests/test_confirm_all_preview.py`.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy.orm import Session

from app.models.audit import AuditLog

from .test_confirm_all_preview import (  # noqa: F401 - `api` is a fixture
    BASE,
    _counts,
    _line_payload,
    _seed_draft,
    _two_line_order,
    _warehouse,
    _stock,
    _project_so,
    _core_so,
    _core_line,
    _project_line,
    _suffix,
    api,
)

URL = f"{BASE}/fulfilment-planning/simulate-confirm"


def _as_role(monkeypatch, *slugs):
    from app.services.user_service import UserPermissionService

    monkeypatch.setattr(
        UserPermissionService, "get_user_role_slugs", lambda self, user_id: set(slugs)
    )


def _post(client, order, lines):
    return client.post(URL, json={"orders": [{"pso_id": order.id, "lines": lines}]})


def _audit_count(world):
    with Session(bind=world.db.get_bind(), join_transaction_mode="create_savepoint") as fresh:
        return fresh.query(AuditLog).count()


def _lines_of(payload):
    lines = payload.lines if hasattr(payload, "lines") else payload["lines"]
    return [
        str(l["project_line_id"] if isinstance(l, dict) else l.project_line_id) for l in lines
    ]


def _inject_failure(monkeypatch, bad_line_id, exc):
    from app.services.project_supply_service import ProjectSupplyService

    original = ProjectSupplyService.confirm

    def wrapper(self, order, payload, *args, **kwargs):
        if str(bad_line_id) in _lines_of(payload):
            raise exc
        return original(self, order, payload, *args, **kwargs)

    monkeypatch.setattr(ProjectSupplyService, "confirm", wrapper)


# --------------------------------------------------------------------------- AC-S1


def test_a_non_admin_is_refused_with_simulate_admin_only_and_nothing_runs(api, monkeypatch):
    """AC-S1: 403 `simulate_admin_only` even with the EDIT permission; nothing written."""
    client, world = api
    _as_role(monkeypatch, "user")
    order, core_so, (a, b) = _two_line_order(world)
    _seed_draft(world, core_so, a)
    before = _counts(world, order, core_so)

    response = _post(
        client, order, [_line_payload(a.id, buy_qty="50"), _line_payload(b.id, buy_qty="50")]
    )

    assert response.status_code == 403, response.text
    assert response.json()["code"] == "simulate_admin_only"
    assert _counts(world, order, core_so) == before


def test_an_admin_gets_200(api, monkeypatch):
    """AC-S1: an admin is let in."""
    client, world = api
    _as_role(monkeypatch, "admin")
    order, _core, (a, b) = _two_line_order(world)

    response = _post(
        client, order, [_line_payload(a.id, buy_qty="50"), _line_payload(b.id, buy_qty="50")]
    )

    assert response.status_code == 200, response.text
    assert "results" in response.json()


# --------------------------------------------------------------------------- AC-S2


def test_a_simulation_that_would_confirm_persists_nothing(api, monkeypatch):
    """AC-S2: revision, inquiry rows, a borrow transfer, the saved draft, audit rows: all
    unchanged; no job enqueued and nobody notified."""
    import app.services.notification_service as ns
    import app.services.queue_service as qs

    client, world = api
    _as_role(monkeypatch, "admin")
    enqueued, notified = [], []
    monkeypatch.setattr(qs, "enqueue_job", lambda *a, **k: enqueued.append((a, k)))
    monkeypatch.setattr(
        ns.NotificationService,
        "create_with_channel_preferences",
        lambda self, **kw: notified.append(kw),
    )
    db = world.db
    donor_wh = _warehouse(db, f"ZZT-DNR-{_suffix()}")
    _stock(db, world.product, donor_wh, on_hand=100)
    order = _project_so(db, world.project)
    core_so = _core_so(db, world.company_id)
    borrow_core = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="20")
    borrow_line = _project_line(
        db, order, line_no=10, product=world.product, core_line=borrow_core
    )
    buy_core = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="50")
    buy_line = _project_line(db, order, line_no=20, product=world.product, core_line=buy_core)
    db.commit()
    _seed_draft(world, core_so, borrow_line)
    before = _counts(world, order, core_so)
    audit_before = _audit_count(world)
    assert before["drafts"] == 1

    response = _post(
        client,
        order,
        [
            _line_payload(
                borrow_line.id,
                borrow=[
                    {
                        "source": "other_location",
                        "warehouse_id": donor_wh.id,
                        "qty": "20",
                        "reason": "Their hand-over is in December.",
                    }
                ],
            ),
            _line_payload(buy_line.id, buy_qty="50"),
        ],
    )

    assert response.status_code == 200, response.text
    result = response.json()["results"][0]
    assert result["ok"] is True, result
    assert result["preview"] is True
    assert len(result["transfers"]) == 1, result
    assert _counts(world, order, core_so) == before
    assert _audit_count(world) == audit_before
    assert enqueued == []
    assert notified == []


# --------------------------------------------------------------------------- AC-S4


def test_a_root_commit_inside_the_write_is_reported_as_that_orders_error(api, monkeypatch):
    """AC-S4: the route trips on a root commit: ok:false, SimulationWouldCommit, no rows."""
    from app.services.project_supply_service import ProjectSupplyService

    client, world = api
    _as_role(monkeypatch, "admin")
    original = ProjectSupplyService.confirm

    def commits(self, order, payload, *args, **kwargs):
        out = original(self, order, payload, *args, **kwargs)
        self.db.commit()
        return out

    monkeypatch.setattr(ProjectSupplyService, "confirm", commits)
    order, core_so, (a, b) = _two_line_order(world)
    before = _counts(world, order, core_so)

    response = _post(
        client, order, [_line_payload(a.id, buy_qty="50"), _line_payload(b.id, buy_qty="50")]
    )

    assert response.status_code == 200, response.text
    result = response.json()["results"][0]
    assert result["ok"] is False, result
    assert "SimulationWouldCommit" in (result["error"] or "")
    assert _counts(world, order, core_so) == before


def test_tripwire_blocks_a_root_commit_but_not_a_savepoint_commit(api):
    """AC-S4: `session.info["confirm_simulate"]` makes a root commit raise; a savepoint
    commit still works."""
    from app.services.confirm_simulation import SimulationWouldCommit, install_commit_tripwire

    _client_unused, world = api
    db = world.db
    install_commit_tripwire(db)
    db.info["confirm_simulate"] = True
    try:
        db.begin_nested().commit()  # must not raise
        with pytest.raises(SimulationWouldCommit):
            db.commit()
    finally:
        db.info.pop("confirm_simulate", None)
        db.rollback()


def test_enqueue_job_raises_while_simulating_and_behaves_normally_otherwise(monkeypatch):
    """AC-S4: enqueue_job raises inside `simulating()`; outside it still enqueues."""
    import app.services.queue_service as qs
    from app.services.confirm_simulation import (
        SimulationWouldCommit,
        is_simulating,
        simulating,
    )

    queued = []

    class _Queue:
        def enqueue(self, func, *args, **kwargs):
            queued.append(func)
            return SimpleNamespace(id="job-1")

    monkeypatch.setattr(qs, "get_queue", lambda name="imports": _Queue())

    assert is_simulating() is False
    with simulating():
        assert is_simulating() is True
        with pytest.raises(SimulationWouldCommit):
            qs.enqueue_job(print, "x")
    assert queued == []
    assert is_simulating() is False

    job = qs.enqueue_job(print, "x")
    assert job.id == "job-1"
    assert queued == [print]


# --------------------------------------------------------------------------- AC-S5


def test_an_ok_order_echoes_what_it_would_write(api, monkeypatch):
    """AC-S5: ok, preview, decision_revision, lines_confirmed, inquiry_rows, transfers."""
    client, world = api
    _as_role(monkeypatch, "admin")
    order, _core, (a, b) = _two_line_order(world)

    response = _post(
        client, order, [_line_payload(a.id, buy_qty="50"), _line_payload(b.id, buy_qty="50")]
    )

    assert response.status_code == 200, response.text
    result = response.json()["results"][0]
    assert result["ok"] is True, result
    assert result["preview"] is True
    assert result["decision_revision"] == 1
    assert [row["line_no"] for row in result["lines_confirmed"]] == [10, 20]
    assert {row["line_no"] for row in result["inquiry_rows"]} == {10, 20}
    assert result["transfers"] == []
    assert "simulated_errors" in result and result["simulated_errors"] is None


# --------------------------------------------------------------------------- AC-S6


def test_the_probe_names_only_the_line_that_fails_alone(api, monkeypatch):
    """AC-S6: two posted lines, one raises a non-AppException: ok:false, raw error, and
    `simulated_errors` lists only the bad line."""
    client, world = api
    _as_role(monkeypatch, "admin")
    order, _core, (a, b) = _two_line_order(world)
    text = "Object of type date is not JSON serializable"
    _inject_failure(monkeypatch, b.id, TypeError(text))

    response = _post(
        client, order, [_line_payload(a.id, buy_qty="50"), _line_payload(b.id, buy_qty="50")]
    )

    assert response.status_code == 200, response.text
    result = response.json()["results"][0]
    assert result["ok"] is False, result
    assert text in (result["error"] or "")
    errors = result["simulated_errors"]
    assert isinstance(errors, list) and len(errors) == 1, result
    assert errors[0]["line_no"] == 20
    assert errors[0]["item_code"] == world.product.product_code
    assert str(errors[0]["project_line_id"]) == str(b.id)
    assert errors[0]["error"].startswith("TypeError")
    assert text in errors[0]["error"]


# --------------------------------------------------------------------------- AC-S7


def test_the_probe_is_skipped_when_the_refusal_already_names_lines(api, monkeypatch):
    """AC-S7: a SupplyLinesRefused carries failing_lines: `simulated_errors` is null."""
    from app.services.project_supply_service import ProjectSupplyService, SupplyLinesRefused

    client, world = api
    _as_role(monkeypatch, "admin")
    order, _core, (a, b) = _two_line_order(world)

    def refuse(self, order, payload, *args, **kwargs):
        raise SupplyLinesRefused(
            422,
            "One line cannot be confirmed.",
            [{"project_line_id": str(b.id), "line_no": 20, "reason": "stale"}],
        )

    monkeypatch.setattr(ProjectSupplyService, "confirm", refuse)

    response = _post(
        client, order, [_line_payload(a.id, buy_qty="50"), _line_payload(b.id, buy_qty="50")]
    )

    assert response.status_code == 200, response.text
    result = response.json()["results"][0]
    assert result["ok"] is False, result
    assert result["failing_lines"], result
    assert result["simulated_errors"] is None


def test_a_real_refusal_naming_a_line_has_no_simulated_errors(api, monkeypatch):
    """AC-S7: a real "not on this sales order" refusal (names its line): null."""
    client, world = api
    _as_role(monkeypatch, "admin")
    order, _core, (a, _b) = _two_line_order(world)
    other = _project_so(world.db, world.project)
    world.db.commit()

    response = _post(client, other, [_line_payload(a.id, buy_qty="50")])

    assert response.status_code == 200, response.text
    result = response.json()["results"][0]
    assert result["ok"] is False, result
    assert result["simulated_errors"] is None


# --------------------------------------------------------------------------- AC-S8


def test_error_text_is_capped_at_1000_chars(api, monkeypatch):
    """AC-S8: both `error` and each `simulated_errors[].error` are at most 1,000 chars."""
    client, world = api
    _as_role(monkeypatch, "admin")
    order, _core, (a, b) = _two_line_order(world)
    _inject_failure(monkeypatch, b.id, TypeError("x" * 5000))

    response = _post(
        client, order, [_line_payload(a.id, buy_qty="50"), _line_payload(b.id, buy_qty="50")]
    )

    assert response.status_code == 200, response.text
    result = response.json()["results"][0]
    assert result["ok"] is False, result
    assert 0 < len(result["error"]) <= 1000
    errors = result["simulated_errors"]
    assert errors and len(errors) == 1, result
    assert 0 < len(errors[0]["error"]) <= 1000
