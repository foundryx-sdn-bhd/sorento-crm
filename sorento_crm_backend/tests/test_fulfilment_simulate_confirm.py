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


def test_a_real_commit_inside_the_write_is_reported_as_that_orders_error(api, monkeypatch):
    """AC-S4 (v2): the write opens a FRESH `SessionLocal()` and commits a row; the engine-level
    guard raises, the order answers ok:false with SimulationWouldCommit, and the row is not
    there afterwards."""
    import sqlalchemy as sa

    from app.database import SessionLocal, engine
    from app.services.project_supply_service import ProjectSupplyService

    client, world = api
    _as_role(monkeypatch, "admin")
    table = f"zzt_sim_probe_{_suffix().replace('-', '_').lower()}"
    with SessionLocal() as setup:
        setup.execute(sa.text(f"create table public.{table} (id text)"))
        setup.commit()
    try:
        original = ProjectSupplyService.confirm

        def commits_elsewhere(self, order, payload, *args, **kwargs):
            out = original(self, order, payload, *args, **kwargs)
            other = SessionLocal()
            try:
                other.execute(sa.text(f"insert into public.{table} values ('leak')"))
                other.commit()
            finally:
                other.close()
            return out

        monkeypatch.setattr(ProjectSupplyService, "confirm", commits_elsewhere)
        order, core_so, (a, b) = _two_line_order(world)
        before = _counts(world, order, core_so)

        response = _post(
            client, order, [_line_payload(a.id, buy_qty="50"), _line_payload(b.id, buy_qty="50")]
        )

        assert response.status_code == 200, response.text
        result = response.json()["results"][0]
        assert result["ok"] is False, result
        assert "SimulationWouldCommit" in (result["error"] or "")
        with engine.connect() as probe:
            assert probe.execute(sa.text(f"select count(*) from public.{table}")).scalar() == 0
        assert _counts(world, order, core_so) == before
    finally:
        with SessionLocal() as cleanup:
            cleanup.execute(sa.text(f"drop table if exists public.{table}"))
            cleanup.commit()


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


# --------------------------------------------------------------------------- fix round (R1..R6)


def _raise_for_every_payload(monkeypatch, exc_factory, seen=None):
    from app.services.project_supply_service import ProjectSupplyService

    def wrapper(self, order, payload, *args, **kwargs):
        if seen is not None:
            seen.append(
                (_lines_of(payload), [str(x) for x in (getattr(payload, "only_line_ids", None) or [])])
            )
        raise exc_factory()

    monkeypatch.setattr(ProjectSupplyService, "confirm", wrapper)


def test_r1_an_order_level_409_is_not_blamed_on_lines(api, monkeypatch):
    """R1: the same refusal on every line is the ORDER's, so `simulated_errors` is null."""
    from app.services.error_handler import AppException

    client, world = api
    _as_role(monkeypatch, "admin")
    order, _core, (a, b) = _two_line_order(world)
    message = "Somebody else confirmed this sales order a moment ago."
    _raise_for_every_payload(
        monkeypatch, lambda: AppException(status_code=409, message=message, code="stale")
    )

    response = _post(
        client, order, [_line_payload(a.id, buy_qty="50"), _line_payload(b.id, buy_qty="50")]
    )

    assert response.status_code == 200, response.text
    result = response.json()["results"][0]
    assert result["ok"] is False, result
    assert message in (result["error"] or "")
    assert result["simulated_errors"] is None


def test_r1_the_same_type_error_on_every_line_is_not_attributable(api, monkeypatch):
    """R1: a non-AppException identical on every line: error keeps its class, no line blamed."""
    client, world = api
    _as_role(monkeypatch, "admin")
    order, _core, (a, b) = _two_line_order(world)
    _raise_for_every_payload(monkeypatch, lambda: TypeError("same everywhere"))

    response = _post(
        client, order, [_line_payload(a.id, buy_qty="50"), _line_payload(b.id, buy_qty="50")]
    )

    assert response.status_code == 200, response.text
    result = response.json()["results"][0]
    assert result["ok"] is False, result
    assert (result["error"] or "").startswith("TypeError: ")
    assert result["simulated_errors"] is None


def _second_order(world):
    """A second two-line order on the same product; the pool stock is already seeded."""
    db = world.db
    order = _project_so(db, world.project)
    core_so = _core_so(db, world.company_id)
    lines = []
    for line_no in (10, 20):
        core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="50")
        lines.append(
            _project_line(db, order, line_no=line_no, product=world.product, core_line=core_line)
        )
    db.commit()
    return order, lines[0], lines[1]


def test_r3_the_probe_budget_is_per_request_and_an_unprobed_order_says_so(api, monkeypatch):
    """R3: budget 3 over two orders each failing on one of two lines: the first order is
    probed (2 runs), the second runs out and is marked as not probed."""
    import app.api.v1.projects.fulfilment_planning as fp

    client, world = api
    _as_role(monkeypatch, "admin")
    monkeypatch.setattr(fp, "_SIMULATE_PROBE_BUDGET", 3, raising=False)
    order1, _c1, (a1, b1) = _two_line_order(world)
    order2, a2, b2 = _second_order(world)
    from app.services.project_supply_service import ProjectSupplyService

    original = ProjectSupplyService.confirm
    bad = {str(b1.id), str(b2.id)}

    def wrapper(self, order, payload, *args, **kwargs):
        if bad & set(_lines_of(payload)):
            raise TypeError("Object of type date is not JSON serializable")
        return original(self, order, payload, *args, **kwargs)

    monkeypatch.setattr(ProjectSupplyService, "confirm", wrapper)

    response = client.post(
        URL,
        json={
            "orders": [
                {
                    "pso_id": order1.id,
                    "lines": [_line_payload(a1.id, buy_qty="50"), _line_payload(b1.id, buy_qty="50")],
                },
                {
                    "pso_id": order2.id,
                    "lines": [_line_payload(a2.id, buy_qty="50"), _line_payload(b2.id, buy_qty="50")],
                },
            ]
        },
    )

    assert response.status_code == 200, response.text
    first, second = response.json()["results"]
    assert first["ok"] is False and second["ok"] is False
    assert [e["line_no"] for e in first["simulated_errors"]] == [20]
    assert not first["error"].endswith("(not probed: the simulation's per-line budget was spent)")
    assert second["simulated_errors"] is None
    assert second["error"].endswith(" (not probed: the simulation's per-line budget was spent)")


def test_r4_more_than_fifty_orders_is_refused_before_anything_runs(api, monkeypatch):
    """R4: 51 orders -> 422 `simulate_too_many_orders`; confirm-all is not refused by it."""
    import uuid

    client, world = api
    _as_role(monkeypatch, "admin")
    order, core_so, (a, _b) = _two_line_order(world)
    before = _counts(world, order, core_so)
    body = {
        "orders": [{"pso_id": order.id, "lines": [_line_payload(a.id, buy_qty="50")]}]
        + [
            {"pso_id": str(uuid.uuid4()), "lines": [_line_payload(str(uuid.uuid4()), buy_qty="1")]}
            for _ in range(50)
        ]
    }
    assert len(body["orders"]) == 51

    response = client.post(URL, json=body)

    assert response.status_code == 422, response.text
    assert response.json()["code"] == "simulate_too_many_orders"
    assert _counts(world, order, core_so) == before

    # The cap is Simulate's own: confirm-all still takes 51 (unknown ids, so each just fails).
    unknown = body["orders"][1:] + [
        {"pso_id": str(uuid.uuid4()), "lines": [_line_payload(str(uuid.uuid4()), buy_qty="1")]}
    ]
    assert len(unknown) == 51
    real = client.post(f"{BASE}/fulfilment-planning/confirm-all", json={"orders": unknown})
    assert real.status_code == 200, real.text
    assert len(real.json()["results"]) == 51


def test_r5_a_single_posted_line_that_fails_is_named_without_probing(api, monkeypatch):
    """R5: one posted line failing with no named line: `simulated_errors` names it."""
    client, world = api
    _as_role(monkeypatch, "admin")
    order, _core, (a, _b) = _two_line_order(world)
    seen = []
    _raise_for_every_payload(monkeypatch, lambda: TypeError("only line broke"), seen)

    response = _post(client, order, [_line_payload(a.id, buy_qty="50")])

    assert response.status_code == 200, response.text
    result = response.json()["results"][0]
    assert result["ok"] is False, result
    errors = result["simulated_errors"]
    assert errors and len(errors) == 1, result
    assert errors[0]["line_no"] == 10
    assert errors[0]["item_code"] == world.product.product_code
    assert errors[0]["error"].startswith("TypeError: ")
    assert len(seen) == 1, "no per-line probe run for a one-line order"


def test_r6_the_probe_never_runs_a_line_outside_only_line_ids(api, monkeypatch):
    """R6: only_line_ids=[A] with lines [A, B]: B is never run alone."""
    client, world = api
    _as_role(monkeypatch, "admin")
    order, _core, (a, b) = _two_line_order(world)
    seen = []
    _raise_for_every_payload(monkeypatch, lambda: TypeError("broke"), seen)

    response = client.post(
        URL,
        json={
            "orders": [
                {
                    "pso_id": order.id,
                    "lines": [_line_payload(a.id, buy_qty="50"), _line_payload(b.id, buy_qty="50")],
                    "only_line_ids": [str(a.id)],
                }
            ]
        },
    )

    assert response.status_code == 200, response.text
    result = response.json()["results"][0]
    assert result["ok"] is False, result
    for lines, only in seen:
        assert only != [str(b.id)] and lines != [str(b.id)], f"line B was run alone: {seen}"
    for entry in result["simulated_errors"] or []:
        assert str(entry["project_line_id"]) == str(a.id)


# --------------------------------------------------------------------------- stock-short warnings


def test_a_simulation_names_a_stock_short_reserve_and_persists_nothing(api, monkeypatch):
    """Owner rule 12 Oct 2026 (OI-CLOSED-LINE): a Reserve past the free stock confirms as
    decided with a warning; the simulation shows `stock_short_notices` and saves nothing."""
    from app.services.automation_service import AutomationService
    from app.services.project_order_inquiry_service import (
        register_order_inquiry_post_commit_dispatch,
    )

    client, world = api
    _as_role(monkeypatch, "admin")
    register_order_inquiry_post_commit_dispatch()
    dispatched = []
    monkeypatch.setattr(
        AutomationService,
        "dispatch_event",
        lambda self, trigger, *, context, source_kind, source_id: dispatched.append(trigger),
    )
    order, core_so, (a, _b) = _two_line_order(world, pool=30, qty_each="50")
    before = _counts(world, order, core_so)

    response = _post(
        client,
        order,
        [_line_payload(a.id, reserve=[{"warehouse_id": world.pool_wh.id, "qty": "50"}])],
    )

    assert response.status_code == 200, response.text
    result = response.json()["results"][0]
    assert result["ok"] is True, result
    assert result["preview"] is True
    notices = result["stock_short_notices"]
    assert notices and len(notices) == 1, result
    assert notices[0]["line_no"] == 10
    assert "20 short" in notices[0]["reason"], notices
    assert _counts(world, order, core_so) == before
    assert dispatched == [], f"a simulation sends no handover email: {dispatched}"


# --------------------------------------------------------------------------- lock holding


def test_s1_the_simulation_runs_under_short_lock_and_statement_timeouts_that_do_not_leak(
    api, monkeypatch
):
    """v2 fix S1: inside the write `lock_timeout` is 5s and `statement_timeout` 30s (SET LOCAL
    in the simulation's savepoint); afterwards the caller's session shows its old values."""
    import sqlalchemy as sa

    from app.services.project_supply_service import ProjectSupplyService

    client, world = api
    _as_role(monkeypatch, "admin")
    seen = {}
    original = ProjectSupplyService.confirm

    def recording(self, order, payload, *args, **kwargs):
        seen["lock"] = self.db.execute(sa.text("SHOW lock_timeout")).scalar()
        seen["statement"] = self.db.execute(sa.text("SHOW statement_timeout")).scalar()
        return original(self, order, payload, *args, **kwargs)

    monkeypatch.setattr(ProjectSupplyService, "confirm", recording)
    order, _core, (a, b) = _two_line_order(world)
    before = (
        world.db.execute(sa.text("SHOW lock_timeout")).scalar(),
        world.db.execute(sa.text("SHOW statement_timeout")).scalar(),
    )

    response = _post(
        client, order, [_line_payload(a.id, buy_qty="50"), _line_payload(b.id, buy_qty="50")]
    )

    assert response.status_code == 200, response.text
    assert seen == {"lock": "5s", "statement": "30s"}
    after = (
        world.db.execute(sa.text("SHOW lock_timeout")).scalar(),
        world.db.execute(sa.text("SHOW statement_timeout")).scalar(),
    )
    assert after == before
