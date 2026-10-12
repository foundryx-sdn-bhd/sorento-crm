"""Board Simulate confirm v2 (owner change to Q3, 12 Oct 2026): the simulation runs the REAL
Confirm path, adoption included, and rolls everything back at the end.

Plan `documentation/plans/scm/PLAN-board-simulate-confirm-12oct.md` "Redesign (v2)".
Contract: an entry may name `sales_order_id` (the CORE sales order) instead of `pso_id`, its
lines carrying `project_line_id: "pending-adoption:<core line id>"`; the server adopts and
swaps each placeholder for the new mirror line's id; the result carries `adopted: true`.
Each ok order commits as a savepoint release so a later order sees it; all of it is rolled
back at the end; a real root commit on any connection raises `SimulationWouldCommit`.

Its own module only so the names stay apart from `test_fulfilment_simulate_confirm.py`
(same blank-schema `api` fixture, reused by import).
"""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.models.project_so import (
    OrderInquiryRow,
    ProjectSalesOrder,
    ProjectSalesOrderLine,
    SOSupplyDecision,
    SOSupplyDecisionDraft,
)

from .test_confirm_all_preview import (  # noqa: F401 - `api` is a fixture
    BASE,
    _core_line,
    _core_so,
    _line_payload,
    _project_line,
    _project_so,
    _stock,
    api,
)
from .test_fulfilment_simulate_confirm import _as_role

URL = f"{BASE}/fulfilment-planning/simulate-confirm"
PENDING = "pending-adoption:"


def _never_planned(world, *, pool=1000, qty="50"):
    """A core SO with two owed lines and NO planning record."""
    db = world.db
    _stock(db, world.product, world.pool_wh, on_hand=pool)
    core_so = _core_so(db, world.company_id)
    core_lines = [
        _core_line(db, core_so, world.product, world.own_wh, qty_ordered=qty) for _ in (1, 2)
    ]
    db.commit()
    return core_so, core_lines


def _placeholder_lines(world, core_lines):
    """Line 1 reserves from the pool, line 2 buys."""
    return [
        _line_payload(
            PENDING + str(core_lines[0].id),
            reserve=[{"warehouse_id": world.pool_wh.id, "qty": "50"}],
        ),
        _line_payload(PENDING + str(core_lines[1].id), buy_qty="50"),
    ]


def _sim_body(core_so, lines):
    return {"orders": [{"sales_order_id": str(core_so.id), "lines": lines}]}


def _everything(world, core_so):
    """Counts read through a SECOND session, so nothing pending in the API's session hides."""
    with Session(bind=world.db.get_bind(), join_transaction_mode="create_savepoint") as fresh:
        return {
            "planning_records": fresh.query(ProjectSalesOrder)
            .filter(ProjectSalesOrder.so_id == core_so.id)
            .count(),
            "mirror_lines": fresh.query(ProjectSalesOrderLine).count(),
            "decisions": fresh.query(SOSupplyDecision).count(),
            "inquiry_rows": fresh.query(OrderInquiryRow).count(),
            "drafts": fresh.query(SOSupplyDecisionDraft).count(),
        }


def _mirror_ids(world, core_so, core_lines):
    db = world.db
    db.expire_all()
    order = db.query(ProjectSalesOrder).filter(ProjectSalesOrder.so_id == core_so.id).one()
    by_core = {
        str(m.core_sales_order_line_id): m
        for m in db.query(ProjectSalesOrderLine).filter(
            ProjectSalesOrderLine.project_sales_order_id == order.id
        )
    }
    return order, [by_core[str(c.id)] for c in core_lines]


# --------------------------------------------------------------------------- T1


def test_t1_a_never_planned_order_simulates_fully_and_leaves_no_trace(api, monkeypatch):
    """T1: adoption included, ok/adopted/revision 1, and nothing of it persists."""
    client, world = api
    _as_role(monkeypatch, "admin")
    core_so, core_lines = _never_planned(world)
    before = _everything(world, core_so)
    assert before["planning_records"] == 0

    response = client.post(URL, json=_sim_body(core_so, _placeholder_lines(world, core_lines)))

    assert response.status_code == 200, response.text
    result = response.json()["results"][0]
    assert result["ok"] is True, result
    assert result["adopted"] is True
    assert result["pso_id"]
    assert result["preview"] is True
    assert result["decision_revision"] == 1
    assert len(result["lines_confirmed"]) == 2
    assert _everything(world, core_so) == before


# --------------------------------------------------------------------------- T2


def test_t2_the_simulation_matches_the_real_adopt_then_confirm(api, monkeypatch):
    """T2: simulate first, then the real journey on the same data; the outcome is equal."""
    client, world = api
    _as_role(monkeypatch, "admin")
    core_so, core_lines = _never_planned(world)

    sim_response = client.post(URL, json=_sim_body(core_so, _placeholder_lines(world, core_lines)))
    assert sim_response.status_code == 200, sim_response.text
    sim = sim_response.json()["results"][0]
    assert sim["ok"] is True, sim

    adopt = client.post(f"{BASE}/fulfilment-planning/adopt", json={"sales_order_id": str(core_so.id)})
    assert adopt.status_code == 200, adopt.text
    order, (m1, m2) = _mirror_ids(world, core_so, core_lines)
    real_response = client.post(
        f"{BASE}/fulfilment-planning/confirm-all",
        json={
            "orders": [
                {
                    "pso_id": str(order.id),
                    "lines": [
                        _line_payload(
                            m1.id, reserve=[{"warehouse_id": world.pool_wh.id, "qty": "50"}]
                        ),
                        _line_payload(m2.id, buy_qty="50"),
                    ],
                }
            ]
        },
    )
    assert real_response.status_code == 200, real_response.text
    real = real_response.json()["results"][0]
    assert real["ok"] is True, real

    assert sim["decision_revision"] == real["decision_revision"]
    assert [r["line_no"] for r in sim["lines_confirmed"]] == [
        r["line_no"] for r in real["lines_confirmed"]
    ]
    line_no = {str(m1.id): m1.line_no, str(m2.id): m2.line_no}
    world.db.expire_all()
    real_rows = sorted(
        (r.verb, Decimal(r.qty), line_no[str(r.so_line_id)])
        for r in world.db.query(OrderInquiryRow).filter(
            OrderInquiryRow.so_line_id.in_([m1.id, m2.id])
        )
    )
    sim_rows = sorted((r["verb"], Decimal(str(r["qty"])), r["line_no"]) for r in sim["inquiry_rows"])
    assert sim_rows == real_rows
    assert len(sim["transfers"]) == real["transfers_written"] == 1


# --------------------------------------------------------------------------- T3


def test_t3_a_later_order_sees_an_earlier_orders_holds(api, monkeypatch):
    """T3 (D2): pool 50, A reserves 50 then B reserves 50; the simulation answers what the
    real press answers - A clean, B short."""
    client, world = api
    _as_role(monkeypatch, "admin")
    db = world.db
    _stock(db, world.product, world.pool_wh, on_hand=50)
    orders = []
    for _ in (1, 2):
        order = _project_so(db, world.project)
        core_so = _core_so(db, world.company_id)
        core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="50")
        line = _project_line(db, order, line_no=10, product=world.product, core_line=core_line)
        orders.append((order, line))
    db.commit()
    body = {
        "orders": [
            {
                "pso_id": order.id,
                "lines": [
                    _line_payload(
                        line.id, reserve=[{"warehouse_id": world.pool_wh.id, "qty": "50"}]
                    )
                ],
            }
            for order, line in orders
        ]
    }

    sim_response = client.post(URL, json=body)
    assert sim_response.status_code == 200, sim_response.text
    sim = sim_response.json()["results"]
    real_response = client.post(f"{BASE}/fulfilment-planning/confirm-all", json=body)
    assert real_response.status_code == 200, real_response.text
    real = real_response.json()["results"]

    def shape(results):
        return [(r["ok"], bool(r.get("stock_short_notices"))) for r in results]

    assert shape(real) == [(True, False), (True, True)], real
    assert shape(sim) == shape(real), sim


# --------------------------------------------------------------------------- T4


def test_t4_nothing_escapes_a_simulation_with_a_stock_short_line(api, monkeypatch):
    """T4: no automation dispatch, no notification, no job enqueued."""
    import app.services.notification_service as ns
    import app.services.queue_service as qs
    from app.services.automation_service import AutomationService
    from app.services.project_order_inquiry_service import (
        register_order_inquiry_post_commit_dispatch,
    )

    client, world = api
    _as_role(monkeypatch, "admin")
    register_order_inquiry_post_commit_dispatch()
    calls = []
    monkeypatch.setattr(
        AutomationService, "dispatch_event", lambda self, *a, **k: calls.append("dispatch")
    )
    monkeypatch.setattr(
        ns.NotificationService,
        "create_with_channel_preferences",
        lambda self, **kw: calls.append("notify"),
    )
    monkeypatch.setattr(qs, "enqueue_job", lambda *a, **k: calls.append("enqueue"))
    core_so, core_lines = _never_planned(world, pool=30)

    response = client.post(
        URL,
        json=_sim_body(
            core_so,
            [
                _line_payload(
                    PENDING + str(core_lines[0].id),
                    reserve=[{"warehouse_id": world.pool_wh.id, "qty": "50"}],
                ),
                _line_payload(PENDING + str(core_lines[1].id), buy_qty="50"),
            ],
        ),
    )

    assert response.status_code == 200, response.text
    result = response.json()["results"][0]
    assert result["ok"] is True, result
    assert result.get("stock_short_notices"), result
    assert calls == []


def test_t4_a_real_commit_on_any_connection_raises_while_simulating(api):
    """T4 (engine-level guard, replaces the v1 session tripwire unit test): inside
    `simulating()` a raw `SessionLocal()` commit raises; outside it the same commit works."""
    from app.database import SessionLocal
    from app.services.confirm_simulation import SimulationWouldCommit, simulating

    with simulating():
        raw = SessionLocal()
        try:
            raw.execute(sa.text("select 1"))
            with pytest.raises(SimulationWouldCommit):
                raw.commit()
        finally:
            raw.rollback()
            raw.close()

    after = SessionLocal()
    try:
        after.execute(sa.text("select 1"))
        after.commit()  # outside a simulation: succeeds
    finally:
        after.close()


def test_t4_a_savepoint_release_on_the_simulations_own_session_does_not_raise(api):
    """T4 guard (may already be green): inside `simulating()`, a session joined to an outer
    transaction in savepoint mode commits (a release) without tripping, and the outer
    rollback leaves nothing."""
    from app.services.confirm_simulation import simulating

    _client, world = api
    with world.db.get_bind().engine.connect() as conn:
        outer = conn.begin()
        try:
            with simulating():
                session = Session(bind=conn, join_transaction_mode="create_savepoint")
                session.execute(sa.text("select 1"))
                session.commit()  # a savepoint release, not a root commit
                session.close()
        finally:
            outer.rollback()


# --------------------------------------------------------------------------- T5


def test_t5_a_non_admin_is_refused_before_anything_runs_adoption_included(api, monkeypatch):
    """T5: 403 `simulate_admin_only`, and no planning record is created."""
    client, world = api
    _as_role(monkeypatch, "user")
    core_so, core_lines = _never_planned(world)
    before = _everything(world, core_so)

    response = client.post(URL, json=_sim_body(core_so, _placeholder_lines(world, core_lines)))

    assert response.status_code == 403, response.text
    assert response.json()["code"] == "simulate_admin_only"
    assert _everything(world, core_so) == before
    assert before["planning_records"] == 0


# --------------------------------------------------------------------------- S2


def test_s2_a_placeholder_for_a_core_line_not_on_the_order_is_a_plain_refusal(api, monkeypatch):
    """v2 fix S2: `pending-adoption:<core line of another order>` answers that order ok:false
    with the plain sentence (no database text) and persists nothing."""
    client, world = api
    _as_role(monkeypatch, "admin")
    core_so, core_lines = _never_planned(world)
    other_so = _core_so(world.db, world.company_id)
    other_lines = [_core_line(world.db, other_so, world.product, world.own_wh, qty_ordered="50")]
    world.db.commit()
    foreign = str(other_lines[0].id)
    before = _everything(world, core_so)

    response = client.post(
        URL,
        json=_sim_body(core_so, [_line_payload(PENDING + foreign, buy_qty="50")]),
    )

    assert response.status_code == 200, response.text
    result = response.json()["results"][0]
    assert result["ok"] is False, result
    assert result["error"] == f"Line {PENDING}{foreign} is not on this sales order."
    assert _everything(world, core_so) == before
