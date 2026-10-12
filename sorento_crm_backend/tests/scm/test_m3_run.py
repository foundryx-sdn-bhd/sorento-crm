"""SCM M3 - reorder RUN JOB + endpoints (AC-M3.6/3.8/3.10/3.11 + auth).

Drives ``reorder_run_service`` over CONTROLLED synthetic planning rows (a stock row
makes a SKU×warehouse appear in ``scm.net_position_v``; a demand_stat row sets its
rate) inside the rolled-back SAVEPOINT the ``scm_app`` fixture provides. Proves:

  * create_run → run_reorder writes recommendations + sets 'completed' + run-log counts,
  * running→completed and running→failed transitions (failure recorded, worker survives),
  * frozen-input reproducibility - a stored rec reproduces its ROP/qty via the pure engine,
  * a network run writes an allocation breakdown summing to the buy qty,
  * a no-supplier planning SKU that would buy emits an 'exception' rec (never skipped),
  * auth (POST needs scm.reorder.run; GET needs scm.dashboard.view).

``run_reorder`` is called SYNCHRONOUSLY (no live worker required).
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.services.scm import reorder_engine as eng
from app.services.scm import reorder_run_service as svc
from tests.scm.conftest import as_user, requires_pg, seed_user

pytestmark = requires_pg


# --- controlled fixture builders (all inside the savepoint) -----------------

def _mk_product(db, code):
    cat, uom = db.execute(text(
        "SELECT category_id, base_uom_id FROM products WHERE category_id IS NOT NULL LIMIT 1"
    )).fetchone()
    pid = str(uuid.uuid4())
    db.execute(text(
        "INSERT INTO products (id, product_code, product_name, category_id, base_uom_id, "
        "list_price, cost_price, is_active, is_discontinued, currency, created_at, updated_at) "
        "VALUES (:id, :code, :name, :cat, :uom, 100, 60, true, false, 'MYR', now(), now())"
    ), {"id": pid, "code": code, "name": f"M3 Run {code}", "cat": cat, "uom": uom})
    return pid


def _mk_warehouse(db, code, *, pool_warehouse_id=None, segment=None):
    wid = str(uuid.uuid4())
    db.execute(text(
        "INSERT INTO warehouses (id, warehouse_code, warehouse_name, is_active, "
        "pool_warehouse_id, segment, created_at, updated_at) "
        "VALUES (:id, :code, :name, true, :pool, :segment, now(), now())"
    ), {"id": wid, "code": code, "name": f"M3 WH {code}", "pool": pool_warehouse_id,
        "segment": segment})
    return wid


def _mk_stock(db, pid, wid, qty):
    # `synced_to_excel` is NOT NULL with only a Python-side ORM default (no
    # server_default) - a raw INSERT that omits it violates the constraint on a
    # from-zero database (a real one already has every row backfilled).
    db.execute(text(
        "INSERT INTO stock (id, product_id, warehouse_id, quantity_on_hand, "
        "synced_to_excel, created_at, updated_at) "
        "VALUES (:id, :p, :w, :q, false, now(), now())"
    ), {"id": str(uuid.uuid4()), "p": pid, "w": wid, "q": qty})


def _mk_demand(db, pid, wid, add, cv=0.2, sample=13):
    db.execute(text(
        "INSERT INTO scm.demand_stat (id, product_id, warehouse_id, avg_daily_demand, "
        "baseline_rate, demand_cv, sample_days, window_days, method, source_system, source_ref, created_at) "
        "VALUES (:id, :p, :w, :add, :add, :cv, :s, 90, 'mean', 'm3test', 't', now())"
    ), {"id": str(uuid.uuid4()), "p": pid, "w": wid, "add": add, "cv": cv, "s": sample})


def _mk_committed(db, pid, wid, qty=1):
    """One LOCATED, open `retail` SO line - G1 (`PLAN-scm-reorder-oi-feedback-1sep.md`)
    admits a product x warehouse to the daily run ONLY when it has committed demand, so
    every M3 fixture that wants the engine to evaluate a SKU (rather than exercise the
    G10 named-product bypass) needs one of these alongside its forecast/stock rows. `qty`
    is kept small on purpose: these tests size the buy off `avg_daily_demand`/stock, not
    off the committed figure, and the arithmetic assertions read the FROZEN `inputs` back
    off the stored rec rather than recomputing net independently."""
    from app.models.order import SalesOrder, SalesOrderLine
    so = SalesOrder(id=str(uuid.uuid4()), so_number=f"M3TESTSO-{uuid.uuid4().hex[:8]}",
                    status="open", demand_class="retail")
    db.add(so)
    db.flush()
    db.add(SalesOrderLine(
        id=str(uuid.uuid4()), sales_order_id=so.id, product_id=pid, warehouse_id=wid,
        qty_ordered=qty, qty_delivered=0, line_status="open",
    ))
    db.flush()


def _mk_supplier(db, name):
    sid = str(uuid.uuid4())
    db.execute(text(
        "INSERT INTO suppliers (id, supplier_code, supplier_name, is_active, created_at, updated_at) "
        "VALUES (:id, :code, :name, true, now(), now())"
    ), {"id": sid, "code": f"M3R-{sid[:8]}", "name": name})
    return sid


def _mk_movement(db, pid, wid, qty, days_ago):
    """A stale sales movement so scm.consumption_v reports a last-movement date
    ``days_ago`` in the past (drives the dead-stock disposition)."""
    oid = str(uuid.uuid4())
    # kpi_warning/subtotal_amount/discount_amount/tax_amount/total_amount/synced_to_excel
    # are all NOT NULL with only a Python-side ORM default (no server_default) - a raw
    # INSERT that omits them violates the constraint on a from-zero database.
    db.execute(text(
        "INSERT INTO orders (id, order_number, order_date, is_cancelled, kpi_warning, "
        "subtotal_amount, discount_amount, tax_amount, total_amount, synced_to_excel, "
        "created_at, updated_at) "
        "VALUES (:id, :num, (now() - make_interval(days => :d)), false, false, "
        "0, 0, 0, 0, false, now(), now())"
    ), {"id": oid, "num": f"M3ORD-{oid[:8]}", "d": days_ago})
    db.execute(text(
        "INSERT INTO order_lines (id, line_sequence, order_id, product_id, warehouse_id, "
        "quantity, created_at, updated_at) VALUES (:id, 1, :o, :p, :w, :q, now(), now())"
    ), {"id": str(uuid.uuid4()), "o": oid, "p": pid, "w": wid, "q": qty})


def _link(db, pid, sid, *, lead=30, moq=100, mult=50, cost=60, primary=True):
    db.execute(text(
        "INSERT INTO product_suppliers (id, product_id, supplier_id, standard_lead_time_days, "
        "moq, order_multiple, unit_cost, currency, is_primary_supplier, created_at) "
        "VALUES (:id, :pid, :sid, :lead, :moq, :mult, :cost, 'MYR', :prim, now())"
    ), {"id": str(uuid.uuid4()), "pid": pid, "sid": sid, "lead": lead, "moq": moq,
        "mult": mult, "cost": cost, "prim": primary})


def _client(scm_app, role_slug):
    app, db, gcu, gcuak = scm_app
    uid = seed_user(db, role_slug)
    as_user(app, gcu, gcuak, uid)
    return app, db


# --- run job ----------------------------------------------------------------

def test_run_writes_recommendations_and_completes(scm_app):
    """AC-M3.10: create_run → run_reorder writes recs, flips running→completed, and
    the run-log carries the {buy, disposition, exceptions, total_cash_impact,
    recommendation_count, duration_ms} counts."""
    _, db, _, _ = scm_app
    wid = _mk_warehouse(db, "M3W-DONE")
    pid = _mk_product(db, "M3P-DONE")
    _mk_stock(db, pid, wid, 10)          # low stock → triggers a buy
    _mk_demand(db, pid, wid, 10.0)       # demand·lead = 300 ≫ net 10
    _mk_committed(db, pid, wid)          # G1: committed demand admits the SKU to the run
    _link(db, pid, _mk_supplier(db, "M3 Done Supplier"))
    db.flush()

    created = svc.create_run(db, ["M3W-DONE"], "warehouse", enqueue=False)
    assert created["status"] == "running"
    result = svc.run_reorder(created["run_id"], db=db)
    assert result["status"] == "completed"

    run = db.execute(text(
        "SELECT status, started_at, finished_at, run_log FROM scm.reorder_run WHERE id = :id"
    ), {"id": created["run_id"]}).mappings().first()
    assert run["status"] == "completed"
    assert run["started_at"] is not None and run["finished_at"] is not None
    log_obj = run["run_log"]
    for k in ("buy", "disposition", "exceptions", "total_cash_impact",
              "recommendation_count", "duration_ms"):
        assert k in log_obj, f"missing run-log count {k}"
    assert log_obj["buy"] >= 1

    recs = db.execute(text(
        "SELECT rec_type, rounded_qty FROM scm.reorder_recommendation WHERE run_id = :id"
    ), {"id": created["run_id"]}).mappings().all()
    buys = [r for r in recs if r["rec_type"] == "buy"]
    assert buys and all(float(b["rounded_qty"]) > 0 for b in buys)


def test_rerun_creates_new_run_immutable_history(scm_app):
    """A re-run creates a NEW run_id - prior runs are immutable history, never overwritten."""
    _, db, _, _ = scm_app
    wid = _mk_warehouse(db, "M3W-IMMU")
    pid = _mk_product(db, "M3P-IMMU")
    _mk_stock(db, pid, wid, 5)
    _mk_demand(db, pid, wid, 8.0)
    _link(db, pid, _mk_supplier(db, "M3 Immu Supplier"))
    db.flush()

    r1 = svc.create_run(db, ["M3W-IMMU"], "warehouse", enqueue=False)
    svc.run_reorder(r1["run_id"], db=db)
    r2 = svc.create_run(db, ["M3W-IMMU"], "warehouse", enqueue=False)
    svc.run_reorder(r2["run_id"], db=db)
    assert r1["run_id"] != r2["run_id"]
    both = db.execute(text(
        "SELECT id FROM scm.reorder_run WHERE id::text = ANY(:ids)"
    ), {"ids": [r1["run_id"], r2["run_id"]]}).fetchall()
    assert len(both) == 2


def test_run_failure_is_recorded_and_worker_survives(scm_app, monkeypatch):
    """running→failed: an engine failure sets status='failed' + error_text and
    run_reorder returns (does NOT raise) so the worker survives."""
    _, db, _, _ = scm_app
    created = svc.create_run(db, [], "warehouse", enqueue=False)

    def _boom(*a, **k):
        raise RuntimeError("injected planning failure")

    monkeypatch.setattr(svc, "_planning_rows", _boom)
    result = svc.run_reorder(created["run_id"], db=db)  # must not raise
    assert result["status"] == "failed"

    run = db.execute(text(
        "SELECT status, error_text, finished_at FROM scm.reorder_run WHERE id = :id"
    ), {"id": created["run_id"]}).mappings().first()
    assert run["status"] == "failed"
    assert run["error_text"] and "injected planning failure" in run["error_text"]
    assert run["finished_at"] is not None


def test_frozen_inputs_reproduce_rop_and_qty(scm_app):
    """AC-M3.11: a completed buy rec's stored frozen inputs reproduce its ROP and
    rounded qty via the pure engine - reproducible without stat versioning."""
    _, db, _, _ = scm_app
    wid = _mk_warehouse(db, "M3W-FROZE")
    pid = _mk_product(db, "M3P-FROZE")
    _mk_stock(db, pid, wid, 20)
    _mk_demand(db, pid, wid, 12.0)
    _mk_committed(db, pid, wid)          # G1: committed demand admits the SKU to the run
    _link(db, pid, _mk_supplier(db, "M3 Froze Supplier"), lead=30, moq=100, mult=50)
    db.flush()

    created = svc.create_run(db, ["M3W-FROZE"], "warehouse", enqueue=False)
    svc.run_reorder(created["run_id"], db=db)

    rec = db.execute(text(
        "SELECT reorder_point, rounded_qty, inputs FROM scm.reorder_recommendation "
        "WHERE run_id = :id AND rec_type = 'buy' AND product_id = :p"
    ), {"id": created["run_id"], "p": pid}).mappings().first()
    assert rec is not None
    inp = rec["inputs"]

    rop_check = eng.reorder_point(inp["demand_rate"], inp["lead_time_days"], inp["safety_stock"])
    assert rop_check == pytest.approx(float(rec["reorder_point"]), abs=1e-3)

    _, qty_check = eng.order_qty(
        True, net=inp["net"], oup=inp["order_up_to"],
        moq=inp["moq"], order_multiple=inp["order_multiple"])
    assert qty_check == pytest.approx(float(rec["rounded_qty"]), abs=1e-6)


def test_network_run_allocation_sums_to_buy_qty(scm_app):
    """AC-M3.8: a network run writes ONE buy rec (warehouse null) whose allocation
    breakdown sums EXACTLY to the buy qty."""
    _, db, _, _ = scm_app
    wa = _mk_warehouse(db, "M3W-NETA")
    wb = _mk_warehouse(db, "M3W-NETB")
    pid = _mk_product(db, "M3P-NET")
    _mk_stock(db, pid, wa, 10)
    _mk_stock(db, pid, wb, 15)
    _mk_demand(db, pid, wa, 9.0)
    _mk_demand(db, pid, wb, 6.0)
    # G1: committed demand admits a SKU x warehouse to the run - both members need it, or
    # the network aggregate loses the other location's position entirely.
    _mk_committed(db, pid, wa)
    _mk_committed(db, pid, wb)
    _link(db, pid, _mk_supplier(db, "M3 Net Supplier"), lead=20, moq=100, mult=25)
    db.flush()

    created = svc.create_run(db, ["M3W-NETA", "M3W-NETB"], "network", enqueue=False)
    svc.run_reorder(created["run_id"], db=db)

    rec = db.execute(text(
        "SELECT warehouse_id, rounded_qty, allocation FROM scm.reorder_recommendation "
        "WHERE run_id = :id AND rec_type = 'buy' AND product_id = :p"
    ), {"id": created["run_id"], "p": pid}).mappings().first()
    assert rec is not None
    assert rec["warehouse_id"] is None            # aggregated network row
    alloc = rec["allocation"]
    assert alloc and len(alloc) == 2
    assert sum(a["qty"] for a in alloc) == pytest.approx(float(rec["rounded_qty"]), abs=1e-6)
    assert all(a.get("warehouse_code") for a in alloc)  # codes frozen, no bare UUIDs


def _pin_reorder_point_global(db):
    """Make the run resolve the policy this file's arithmetic assumes: one active global
    `reorder_point`, safety 7 days. The run only seeds that default when the database has
    no global row at all (`ensure_reorder_policy_defaults`), so on a database that holds
    its own global the result depended on whatever that row said."""
    db.execute(text("UPDATE scm.reorder_policy SET is_active = false WHERE scope_type = 'global'"))
    db.execute(text(
        "INSERT INTO scm.reorder_policy (id, scope_type, scope_ref, policy_type, "
        "safety_stock_method, safety_days, is_active, priority, source_system, source_ref, "
        "created_at, updated_at) VALUES (:id, 'global', NULL, 'reorder_point', 'fixed_days', "
        "7, true, 0, 'test', 'pin', now(), now())"
    ), {"id": str(uuid.uuid4())})


def test_network_run_no_buy_when_net_above_rop_below_oup(scm_app):
    """#1/#4: a network reorder_point aggregate whose net sits ABOVE ROP but BELOW OUP
    must NOT emit a buy (sizing OUP-net>0 is not a trigger). agg_demand=10, safety 7,
    lead 30 → ROP 370, OUP 670; agg_net=498 is in the band → no buy rec.

    G1: committed demand admits the SKU to the run (like its six siblings above) - 1 unit
    at EACH location, so agg_net drops from the uncommitted 500 to 498 and stays inside
    the band; the arithmetic under test is otherwise untouched.

    GH #495 marked this xfail(strict) on 2 Sep, but the test never pinned its policy, so
    the outcome was whatever global `reorder_policy` the database held. On a bootstrapped
    database (the default reorder_point) it XPASSed from the day the marker went in. The
    policy is pinned now. The SKU is in the run (the same cells below ROP do emit a buy),
    and the aggregate trigger in `_plan_network` keeps the band quiet.
    """
    _, db, _, _ = scm_app
    wa = _mk_warehouse(db, "M3W-BANDA")
    wb = _mk_warehouse(db, "M3W-BANDB")
    pid = _mk_product(db, "M3P-BAND")
    _mk_stock(db, pid, wa, 300)          # net 300, demand 6
    _mk_stock(db, pid, wb, 200)          # net 200, demand 4  → agg_net 500 in (370, 670)
    _mk_demand(db, pid, wa, 6.0)
    _mk_demand(db, pid, wb, 4.0)
    _mk_committed(db, pid, wa)            # agg_net 500 -> 498, still inside the band
    _mk_committed(db, pid, wb)
    _link(db, pid, _mk_supplier(db, "M3 Band Supplier"), lead=30)
    db.flush()

    _pin_reorder_point_global(db)
    db.flush()

    created = svc.create_run(db, ["M3W-BANDA", "M3W-BANDB"], "network", enqueue=False)
    svc.run_reorder(created["run_id"], db=db)

    buys = db.execute(text(
        "SELECT count(*) FROM scm.reorder_recommendation "
        "WHERE run_id = :id AND product_id = :p AND rec_type = 'buy'"
    ), {"id": created["run_id"], "p": pid}).scalar()
    assert buys == 0, "net above ROP but below OUP must not trigger a network buy"


def test_dead_cell_does_not_also_emit_a_buy(scm_app):
    """#8, superseded by G2 (`PLAN-scm-reorder-oi-feedback-1sep.md`, 1 Sep 2026):
    disposition/dead-stock rows leave the plan ENTIRELY now (no `disposition` rec at
    all), and the #8 gate that used to suppress a contradictory buy on that cell is
    unchanged underneath - a dead cell still emits no buy. But the cell here still
    carries committed demand (AC-2.3's amended sentence, 2 Sep): silently dropping
    demand somebody is owed would be a worse outcome than #8's contradiction, so
    `_emit_cell` emits `covered` instead of nothing - "use this stock instead of
    buying", the same suggestion any other untriggered cell states."""
    _, db, _, _ = scm_app
    wid = _mk_warehouse(db, "M3W-DEAD")
    pid = _mk_product(db, "M3P-DEAD")
    _mk_stock(db, pid, wid, 5)           # low net → would trigger a buy
    _mk_demand(db, pid, wid, 10.0)
    _mk_movement(db, pid, wid, 1, days_ago=400)   # stale movement > 180d → dead
    _mk_committed(db, pid, wid)          # G1: committed demand admits the SKU to the run
    _link(db, pid, _mk_supplier(db, "M3 Dead Supplier"))
    db.flush()

    created = svc.create_run(db, ["M3W-DEAD"], "warehouse", enqueue=False)
    svc.run_reorder(created["run_id"], db=db)

    rows = db.execute(text(
        "SELECT rec_type FROM scm.reorder_recommendation WHERE run_id = :id AND product_id = :p"
    ), {"id": created["run_id"], "p": pid}).mappings().all()
    types = [r["rec_type"] for r in rows]
    assert "disposition" not in types, "G2: disposition rows leave the plan entirely"
    assert "buy" not in types, "a dead cell must not also emit a buy rec"
    assert "covered" in types, (
        "the cell's committed demand must not vanish silently - it states covered instead"
    )


def test_no_supplier_sku_emits_exception(scm_app):
    """AC-M3.6: a planning SKU that would trigger a buy but has NO linked supplier
    emits a flagged 'exception' rec (not silently skipped)."""
    _, db, _, _ = scm_app
    wid = _mk_warehouse(db, "M3W-NOSUP")
    pid = _mk_product(db, "M3P-NOSUP")
    _mk_stock(db, pid, wid, 5)
    _mk_demand(db, pid, wid, 10.0)        # triggers, but no product_suppliers link
    _mk_committed(db, pid, wid)           # G1: committed demand admits the SKU to the run
    db.flush()

    created = svc.create_run(db, ["M3W-NOSUP"], "warehouse", enqueue=False)
    svc.run_reorder(created["run_id"], db=db)

    rec = db.execute(text(
        "SELECT rec_type, supplier_id, inputs FROM scm.reorder_recommendation "
        "WHERE run_id = :id AND product_id = :p AND rec_type = 'exception'"
    ), {"id": created["run_id"], "p": pid}).mappings().first()
    assert rec is not None, "expected a no-supplier exception rec"
    assert rec["supplier_id"] is None
    assert rec["inputs"]["is_exception"] is True


# --- endpoints + auth -------------------------------------------------------

def test_endpoints_full_flow(scm_app):
    """POST launch → GET status (completed + summary) → GET recommendations (paged,
    frozen-input row shape, no UUIDs in display fields). The request no longer carries
    ``buy_scope`` (M8-D5) - the run defaults to network, so a single-warehouse plan
    emits an aggregate (network) buy whose allocation names the warehouse."""
    app, db = _client(scm_app, "purchasing")
    wid = _mk_warehouse(db, "M3W-API")
    pid = _mk_product(db, "M3P-API")
    _mk_stock(db, pid, wid, 8)
    _mk_demand(db, pid, wid, 11.0)
    _mk_committed(db, pid, wid)          # G1: committed demand admits the SKU to the run
    _link(db, pid, _mk_supplier(db, "M3 Api Supplier"))
    db.flush()

    with TestClient(app) as c:
        launch = c.post("/api/v1/scm/reorder-runs",
                        json={"warehouse_codes": ["M3W-API"]})
        assert launch.status_code == 202, launch.text
        run_id = launch.json()["run_id"]
        assert launch.json()["status"] == "running"
        # buy_scope left the request (M8-D5) and defaults to per-warehouse planning
        # (`create_run`, 1dc247982), not the aggregated network this used to expect.
        assert launch.json()["buy_scope"] == "warehouse"

        # the enqueue path is a no-op here (no worker); drive the job synchronously
        svc.run_reorder(run_id, db=db)

        status = c.get(f"/api/v1/scm/reorder-runs/{run_id}")
        assert status.status_code == 200, status.text
        body = status.json()
        assert body["status"] == "completed"
        assert body["summary"]["recommendation_count"] >= 1

        recs = c.get(f"/api/v1/scm/reorder-runs/{run_id}/recommendations",
                     params={"page": 1, "limit": 50, "type": "buy"})
        assert recs.status_code == 200, recs.text
        page = recs.json()
        assert set(page["pagination"]) == {"page", "limit", "total", "total_pages"}
        assert page["data"], "expected at least one buy recommendation"
        row = next(r for r in page["data"] if r["sku"] == "M3P-API")
        assert row["type"] == "buy"
        assert row["order_qty"] and row["order_qty"] > 0
        assert row["supplier"] and row["supplier"]["supplier_code"]
        # a per-warehouse buy (the default scope): the row names its own warehouse
        assert row["is_network"] is False
        assert row["warehouse_code"] == "M3W-API"
        # no UUIDs surface in display fields
        assert "-" not in str(row["sku"]) or not _looks_uuid(row["sku"])

        # frozen derivation inputs power the plain-language explanation popup - the
        # endpoint surfaces them read-only from `inputs` (never recomputed on the FE).
        assert row["forecast_daily_demand"] is not None and row["forecast_daily_demand"] > 0
        assert row["lead_time_days"] is not None and row["lead_time_days"] > 0
        assert row["lead_time_source"] in ("measured", "declared", "default")
        assert row["safety_stock"] is not None
        assert row["safety_stock_method"] in ("fixed_days", "statistical", "manual")
        assert row["review_days"] is not None and row["review_days"] > 0
        assert row["policy_type"] in ("reorder_point", "periodic_review", "min_max")
        assert row["supplier_selection"] in ("primary", "best_score", "lowest_cost")
        # the qty arithmetic reproduces: order-up-to − net = recommended → rounded order_qty
        assert row["order_up_to"] is not None
        assert row["recommended_qty"] is not None and row["recommended_qty"] > 0


def _looks_uuid(v) -> bool:
    try:
        uuid.UUID(str(v))
        return True
    except (ValueError, AttributeError, TypeError):
        return False


def test_list_runs_newest_first_with_summary_and_pagination(scm_app):
    """AC - run history: GET /reorder-runs returns runs newest-first, each with its
    resolved warehouse codes/count + (once completed) the run-log summary counts, and
    a page envelope. Two completed runs → the second-created is listed first."""
    app, db = _client(scm_app, "purchasing")
    wid = _mk_warehouse(db, "M3W-HIST")
    pid = _mk_product(db, "M3P-HIST")
    _mk_stock(db, pid, wid, 8)
    _mk_demand(db, pid, wid, 11.0)
    _mk_committed(db, pid, wid)          # G1: committed demand admits the SKU to the run
    _link(db, pid, _mk_supplier(db, "M3 Hist Supplier"))
    db.flush()

    r1 = svc.create_run(db, ["M3W-HIST"], "warehouse", enqueue=False)
    svc.run_reorder(r1["run_id"], db=db)
    r2 = svc.create_run(db, ["M3W-HIST"], "warehouse", enqueue=False)
    svc.run_reorder(r2["run_id"], db=db)

    with TestClient(app) as c:
        res = c.get("/api/v1/scm/reorder-runs", params={"page": 1, "limit": 50})
        assert res.status_code == 200, res.text
        body = res.json()
        assert set(body["pagination"]) == {"page", "limit", "total", "total_pages"}
        assert body["pagination"]["total"] >= 2

        ids = [row["run_id"] for row in body["data"]]
        # newest-first: r2 (created last) precedes r1
        assert ids.index(r2["run_id"]) < ids.index(r1["run_id"])

        row = next(r for r in body["data"] if r["run_id"] == r2["run_id"])
        assert row["status"] == "completed"
        assert row["buy_scope"] == "warehouse"
        assert row["warehouse_codes"] == ["M3W-HIST"]
        assert row["warehouse_count"] == 1
        assert row["started_at"] and row["finished_at"]
        assert row["summary"]["recommendation_count"] >= 1
        assert row["summary"]["buy_count"] >= 1


def test_list_runs_denied_without_dashboard_view(scm_app):
    """Auth: the run-history list needs scm.dashboard.view; a bare user is denied."""
    app, _ = _client(scm_app, None)
    with TestClient(app) as c:
        res = c.get("/api/v1/scm/reorder-runs")
    assert res.status_code == 403


def test_post_denied_without_reorder_run_permission(scm_app):
    """Auth: launching a run needs scm.reorder.run; a bare user is denied."""
    app, _ = _client(scm_app, None)
    with TestClient(app) as c:
        res = c.post("/api/v1/scm/reorder-runs",
                     json={"warehouse_codes": [], "buy_scope": "network"})
    assert res.status_code == 403


def test_get_status_denied_without_dashboard_view(scm_app):
    """Auth: reading a run's status needs scm.dashboard.view; a bare user is denied."""
    app, _ = _client(scm_app, None)
    with TestClient(app) as c:
        res = c.get(f"/api/v1/scm/reorder-runs/{uuid.uuid4()}")
    assert res.status_code == 403


def test_get_recommendations_denied_without_dashboard_view(scm_app):
    """Auth: the results grid needs scm.dashboard.view; a bare user is denied."""
    app, _ = _client(scm_app, None)
    with TestClient(app) as c:
        res = c.get(f"/api/v1/scm/reorder-runs/{uuid.uuid4()}/recommendations")
    assert res.status_code == 403
