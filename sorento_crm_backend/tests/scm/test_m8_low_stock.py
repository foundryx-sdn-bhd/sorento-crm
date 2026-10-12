"""SCM M8 Slice B - low-stock on the SCM dashboard (M8-B1..B5).

Populates the previously-deferred ``below_rop_count`` rollup tile and the
``status=low`` product drill from the LATEST completed reorder run's frozen
``reorder_point`` (M3 engine output), read-only - the engine is never re-run here.

Drives CONTROLLED synthetic planning rows (a stock row makes a SKU×warehouse
appear in ``scm.net_position_v``; net = on_hand with no PO/SO) plus a directly
seeded ``completed`` reorder_run + recommendations carrying a chosen reorder_point,
inside the rolled-back SAVEPOINT the ``scm_app`` fixture provides. Proves:

  * ``_is_below_rop`` precedence - stocked & at/under ROP counts; stockout / null-ROP /
    net-above-ROP do NOT (M8-B2/B3),
  * ``below_rop_count`` rollup is the count of ``on_hand>0 AND net<=reorder_point`` rows,
  * ``GET /dashboard/products?status=low`` returns exactly that set (M8-B5), excluding
    a stockout SKU, and its size matches ``below_rop_count``,
  * ``stockout_count`` is unchanged by the new join (regression).
"""
from __future__ import annotations

import json
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import text

from datetime import date, timedelta

from app.services.scm.dashboard_service import _compute_status, _is_below_rop
from tests.scm.conftest import SORENTO_COMPANY_ID, as_user, requires_pg, seed_user

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
    ), {"id": pid, "code": code, "name": f"M8 {code}", "cat": cat, "uom": uom})
    return pid


def _mk_warehouse(db, code):
    wid = str(uuid.uuid4())
    db.execute(text(
        "INSERT INTO warehouses (id, warehouse_code, warehouse_name, is_active, created_at, updated_at) "
        "VALUES (:id, :code, :name, true, now(), now())"
    ), {"id": wid, "code": code, "name": f"M8 WH {code}"})
    return wid


def _mk_stock(db, pid, wid, qty):
    db.execute(text(
        "INSERT INTO stock (id, product_id, warehouse_id, quantity_on_hand, "
        "synced_to_excel, created_at, updated_at) "
        "VALUES (:id, :p, :w, :q, false, now(), now())"
    ), {"id": str(uuid.uuid4()), "p": pid, "w": wid, "q": qty})


def _mk_completed_run(db):
    """A completed run belonging to Sorento.

    ``company_id`` is explicit because these are raw INSERTs: the ORM auto-stamp never
    fires, `scm.reorder_run` has no DB default (unlike `products` / `warehouses` /
    `stock`, which default to Sorento), and the dashboard's latest-run picker is
    company-scoped - so a company-less run is simply never found.
    """
    rid = str(uuid.uuid4())
    db.execute(text(
        "INSERT INTO scm.reorder_run (id, status, buy_scope, started_at, finished_at, "
        "company_id, created_at) "
        "VALUES (:id, 'completed', 'warehouse', now(), now(), :co, now())"
    ), {"id": rid, "co": SORENTO_COMPANY_ID})
    return rid


def _mk_rec(db, run_id, pid, wid, rop, rec_type="buy"):
    db.execute(text(
        "INSERT INTO scm.reorder_recommendation (id, run_id, rec_type, product_id, warehouse_id, "
        "reorder_point, status, company_id, created_at) "
        "VALUES (:id, :run, :t, :p, :w, :rop, 'proposed', :co, now())"
    ), {"id": str(uuid.uuid4()), "run": run_id, "t": rec_type, "p": pid, "w": wid, "rop": rop,
        "co": SORENTO_COMPANY_ID})


def _mk_rec_with_inputs(db, run_id, pid, wid, rop, safety_stock, lead_time_days,
                        rec_type="buy"):
    """A rec whose frozen ``inputs`` JSONB carries the ROP inputs (M8-F10) - the same
    shape ``reorder_run_service`` freezes: ``safety_stock`` + ``lead_time_days``."""
    db.execute(text(
        "INSERT INTO scm.reorder_recommendation (id, run_id, rec_type, product_id, warehouse_id, "
        "reorder_point, inputs, status, company_id, created_at) "
        "VALUES (:id, :run, :t, :p, :w, :rop, CAST(:inp AS jsonb), 'proposed', :co, now())"
    ), {"id": str(uuid.uuid4()), "run": run_id, "t": rec_type, "p": pid, "w": wid, "rop": rop,
        "co": SORENTO_COMPANY_ID,
        "inp": json.dumps({"safety_stock": safety_stock, "lead_time_days": lead_time_days})})


def _client(scm_app, role_slug):
    app, db, gcu, gcuak = scm_app
    uid = seed_user(db, role_slug)
    as_user(app, gcu, gcuak, uid)
    return app, db


def _seed_rop_scenario(db):
    """A warehouse with four SKUs and a completed run's frozen reorder points:
      LOW - on_hand 10, net 10 <= ROP 50  → below reorder point.
      HIGH - on_hand 100, net 100 > ROP 50 → not below.
      OUT - on_hand 0 (stockout) even with ROP 50 → NEVER low (precedence).
      NULL - on_hand 10 but NO rec (ROP null) → not low.
    Returns the warehouse code."""
    wid = _mk_warehouse(db, "M8W-ROP")
    p_low = _mk_product(db, "M8P-LOW")
    p_high = _mk_product(db, "M8P-HIGH")
    p_out = _mk_product(db, "M8P-OUT")
    p_null = _mk_product(db, "M8P-NULL")
    _mk_stock(db, p_low, wid, 10)
    _mk_stock(db, p_high, wid, 100)
    _mk_stock(db, p_out, wid, 0)
    _mk_stock(db, p_null, wid, 10)
    run = _mk_completed_run(db)
    _mk_rec(db, run, p_low, wid, 50)
    _mk_rec(db, run, p_high, wid, 50)
    _mk_rec(db, run, p_out, wid, 50)
    # p_null: intentionally no recommendation → reorder_point stays null.
    db.flush()
    return "M8W-ROP"


# --- pure predicate (M8-B2/B3) ----------------------------------------------

def test_is_below_rop_pure_predicate():
    # stocked and at/under ROP → low
    assert _is_below_rop(on_hand=10, net=10, reorder_point=50)
    assert _is_below_rop(on_hand=10, net=50, reorder_point=50)  # boundary: net == ROP
    # net strictly above ROP → not low
    assert not _is_below_rop(on_hand=100, net=100, reorder_point=50)
    # stockout precedence: on_hand<=0 is NEVER low even at/under ROP
    assert not _is_below_rop(on_hand=0, net=0, reorder_point=50)
    assert not _is_below_rop(on_hand=-5, net=-5, reorder_point=50)
    # null reorder point (no completed run / no rec) never counts
    assert not _is_below_rop(on_hand=10, net=10, reorder_point=None)


# --- B4: below_rop_count rollup ---------------------------------------------

def test_below_rop_count_populated(scm_app):
    app, db = _client(scm_app, "purchasing")
    wh = _seed_rop_scenario(db)
    with TestClient(app) as c:
        body = c.get("/api/v1/scm/dashboard/rollups", params={"warehouse": wh}).json()
    # exactly one SKU (LOW) is stocked and at/under its reorder point; HIGH is above,
    # OUT is a stockout (precedence), NULL has no reorder point.
    assert body["below_rop_count"] == 1


# --- B5: status=low product drill -------------------------------------------

def test_products_status_low_filter(scm_app):
    app, db = _client(scm_app, "purchasing")
    wh = _seed_rop_scenario(db)
    with TestClient(app) as c:
        low = c.get(
            "/api/v1/scm/dashboard/products",
            params={"status": "low", "warehouse": wh, "limit": 200},
        ).json()
        rollups = c.get("/api/v1/scm/dashboard/rollups", params={"warehouse": wh}).json()
    skus = [r["sku"] for r in low["data"]]
    # the drill returns exactly the below-ROP set …
    assert skus == ["M8P-LOW"]
    # … the stockout SKU is excluded (precedence) …
    assert "M8P-OUT" not in skus
    # … and the drill size agrees with the tile count.
    assert low["total"] == rollups["below_rop_count"] == 1


# --- B7: `low` health STATUS from _compute_status (precedence) ---------------

def test_compute_status_emits_low():
    today = date(2026, 7, 17)
    recent = today - timedelta(days=1)  # moved recently → not dead

    # stocked, recently moved, net at/under ROP → `low`
    assert _compute_status(10, 0, 0, recent, dead_days=90, today=today,
                           net=10, reorder_point=50) == "low"
    # boundary net == ROP → still low
    assert _compute_status(10, 0, 0, recent, dead_days=90, today=today,
                           net=50, reorder_point=50) == "low"
    # net strictly above ROP → healthy (not low)
    assert _compute_status(100, 0, 0, recent, dead_days=90, today=today,
                           net=100, reorder_point=50) == "healthy"
    # low wins over incoming even with an inbound PO (net already folds on_order in)
    assert _compute_status(10, 5, 0, recent, dead_days=90, today=today,
                           net=10, reorder_point=50) == "low"
    # PRECEDENCE - stockout beats low
    assert _compute_status(0, 0, 0, recent, dead_days=90, today=today,
                           net=0, reorder_point=50) == "stockout"
    # PRECEDENCE - dead (no movement) beats low
    assert _compute_status(10, 0, 0, None, dead_days=90, today=today,
                           net=10, reorder_point=50) == "dead"
    # null ROP (un-planned SKU) never reads low
    assert _compute_status(10, 0, 0, recent, dead_days=90, today=today,
                           net=10, reorder_point=None) == "healthy"


# --- F10: ROP inputs (safety stock + lead time) on the low-stock drill -------

def test_products_low_carry_safety_stock_and_lead_time(scm_app):
    """M8-F10: the Low-stock drill rows surface ``safety_stock`` + ``lead_time_days``
    read from the SAME latest-run rec's frozen ``inputs`` that sourced reorder_point;
    a rec whose inputs never carried them (or an un-planned SKU) returns null."""
    app, db = _client(scm_app, "purchasing")
    wid = _mk_warehouse(db, "M8W-F10")
    p_full = _mk_product(db, "M8P-F10-FULL")   # rec inputs carry ss + lead
    p_bare = _mk_product(db, "M8P-F10-BARE")   # rec with ROP but no inputs
    _mk_stock(db, p_full, wid, 10)   # net 10 <= ROP 50 → low
    _mk_stock(db, p_bare, wid, 10)
    run = _mk_completed_run(db)
    _mk_rec_with_inputs(db, run, p_full, wid, rop=50, safety_stock=12, lead_time_days=7)
    _mk_rec(db, run, p_bare, wid, 50)  # no inputs JSONB → ss/lead null
    db.flush()
    with TestClient(app) as c:
        body = c.get(
            "/api/v1/scm/dashboard/products",
            params={"status": "low", "warehouse": "M8W-F10", "limit": 200},
        ).json()
    by_sku = {r["sku"]: r for r in body["data"]}
    full = by_sku["M8P-F10-FULL"]
    assert full["reorder_point"] == 50
    # sourced from the SAME rec's frozen inputs, not fabricated
    assert full["safety_stock"] == 12
    assert full["lead_time_days"] == 7
    # a rec with no inputs → null (FE renders a dash), never a made-up number
    bare = by_sku["M8P-F10-BARE"]
    assert bare["reorder_point"] == 50
    assert bare["safety_stock"] is None
    assert bare["lead_time_days"] is None


# --- regression: stockout tile untouched ------------------------------------

def test_stockout_count_unchanged_by_rop_join(scm_app):
    app, db = _client(scm_app, "purchasing")
    wh = _seed_rop_scenario(db)
    with TestClient(app) as c:
        body = c.get("/api/v1/scm/dashboard/rollups", params={"warehouse": wh}).json()
        out = c.get(
            "/api/v1/scm/dashboard/products",
            params={"status": "stockout", "warehouse": wh, "limit": 200},
        ).json()
    # the single zero-stock SKU is the only stockout; the ROP join does not disturb it.
    assert body["stockout_count"] == 1
    assert [r["sku"] for r in out["data"]] == ["M8P-OUT"]
