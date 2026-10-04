"""The mirror of the order trend, on the buy side.

> "similar to SO - what is the trend of purchase, then the list of supplier, purchase
>  date, purchase quantity, purchase cost" (user markup, 2026-08-11)

What is pinned: the trend windows are the last N months against the N before them, a
draft this plan itself proposed is never read back as a purchase, the recent-lines list
is capped and ordered newest first, and the endpoint never leaks a purchase order that
belongs to a different company even when the product code collides.
"""
from __future__ import annotations

import uuid
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.services.scm import purchase_trend_service
from tests.scm.conftest import requires_pg, seed_user
from tests.scm.test_outstanding_import_routes import as_company_user

pytestmark = requires_pg

MARKER = "ZZTPTR"
AS_OF = date(2026, 8, 10)


def _u() -> str:
    return str(uuid.uuid4())


def _first_of(as_of: date, months_back: int) -> date:
    """The first of the month `months_back` before `as_of`'s month."""
    total = as_of.year * 12 + as_of.month - 1 - months_back
    return date(total // 12, total % 12 + 1, 1)


def _world(db, company_id=None, as_of=AS_OF):
    from app.models.product import Product, ProductCategory, UnitOfMeasure
    from tests._pg_fixture import unique_code

    cat = ProductCategory(id=_u(), category_code=unique_code(MARKER),
                          category_name=f"{MARKER} cat")
    uom = UnitOfMeasure(id=_u(), uom_code=unique_code("U")[:20], uom_name=f"{MARKER} u")
    db.add_all([cat, uom])
    db.flush()
    product = Product(id=_u(), product_code=unique_code("P"), product_name=f"{MARKER} p",
                      category_id=cat.id, base_uom_id=uom.id, list_price=0,
                      is_active=True, is_discontinued=False)
    db.add(product)
    db.flush()
    pid = str(product.id)

    supplier_id = _u()
    db.execute(text(
        "INSERT INTO suppliers (id, supplier_code, supplier_name, is_active) "
        "VALUES (:id, :c, :n, true)"),
        {"id": supplier_id, "c": unique_code("S"), "n": f"{MARKER} supplier"})

    def add_po(number, day, qty, cost, status="closed", company=None):
        poid = _u()
        db.execute(text(
            "INSERT INTO purchase_orders (id, po_number, supplier_id, issue_date, status, "
            "currency, source_system" + (", company_id" if company else "")
            + ") VALUES (:id, :n, :s, :d, :st, 'USD', 'test'"
            + (", :co" if company else "") + ")"),
            {"id": poid, "n": f"{MARKER}-{number}", "s": supplier_id, "d": day,
             "st": status, **({"co": company} if company else {})})
        db.execute(text(
            "INSERT INTO purchase_order_lines (id, purchase_order_id, product_id, "
            "qty_ordered, qty_received, unit_cost, currency, line_status" +
            (", company_id" if company else "") +
            ") VALUES (:id, :po, :p, :q, 0, :c, 'USD', 'open'" +
            (", :co" if company else "") + ")"),
            {"id": _u(), "po": poid, "p": pid, "q": qty, "c": cost,
             **({"co": company} if company else {})})

    # Dated off `as_of` (the endpoint reads the clock, so its test passes today). With
    # AS_OF = 2026-08-10 the window is 3 months: recent = May..Jul (R1 Jul 1, R2 Jun 1),
    # previous = Feb..Apr (P1 Mar 1).
    add_po("R1", _first_of(as_of, 1), 100, 72.0, company=company_id)
    add_po("R2", _first_of(as_of, 2), 50, 70.0, company=company_id)
    add_po("P1", _first_of(as_of, 5), 300, 60.0, company=company_id)
    # This run's own proposal - never a purchase.
    add_po("DRAFT", _first_of(as_of, 0), 999, 1.0, status="draft_recommendation",
           company=company_id)

    run_id = _u()
    db.execute(text(
        "INSERT INTO scm.reorder_run (id, status, include_market"
        + (", company_id" if company_id else "")
        + ", created_at) VALUES (:id, 'completed', false"
        + (", :co" if company_id else "")
        + ", now())"), {"id": run_id, **({"co": company_id} if company_id else {})})
    db.execute(text(
        "INSERT INTO scm.reorder_recommendation "
        "(id, run_id, product_id, rec_type, rounded_qty, status"
        + (", company_id) VALUES (:id, :r, :p, 'buy', 10, 'proposed', :co)" if company_id
           else ") VALUES (:id, :r, :p, 'buy', 10, 'proposed')")),
        {"id": _u(), "r": run_id, "p": pid,
         **({"co": company_id} if company_id else {})})
    db.flush()
    return {"run_id": run_id, "product_id": pid, "supplier_code": None}


def test_the_trend_compares_the_recent_window_against_the_one_before_it():
    from tests._pg_fixture import pg_session
    with pg_session() as db:
        w = _world(db)

        out = purchase_trend_service.purchase_trend_for_run(db, w["run_id"], as_of=AS_OF)
        entry = out["products"][w["product_id"]]

        assert out["window_months"] == 3
        assert entry["recent_qty"] == 150.0   # R1 (100) + R2 (50), both inside May..Jul
        assert entry["previous_qty"] == 300.0  # P1, inside Feb..Apr


def test_a_draft_the_plan_itself_proposed_is_never_a_purchase():
    from tests._pg_fixture import pg_session
    with pg_session() as db:
        w = _world(db)

        out = purchase_trend_service.purchase_trend_for_run(db, w["run_id"], as_of=AS_OF)
        entry = out["products"][w["product_id"]]

        assert all(l["po_number"] != f"{MARKER}-DRAFT" for l in entry["lines"])
        # And it never inflates the trend either - August is excluded regardless.
        assert entry["recent_qty"] == 150.0


def test_the_lines_are_newest_first_with_their_supplier_and_cost():
    from tests._pg_fixture import pg_session
    with pg_session() as db:
        w = _world(db)

        out = purchase_trend_service.purchase_trend_for_run(db, w["run_id"], as_of=AS_OF)
        lines = out["products"][w["product_id"]]["lines"]

        assert [l["po_number"].split("-")[-1] for l in lines] == ["R1", "R2", "P1"]
        assert lines[0]["unit_cost"] == 72.0
        assert lines[0]["currency"] == "USD"
        assert lines[0]["supplier_name"] == f"{MARKER} supplier"


def test_the_line_list_is_capped():
    from tests._pg_fixture import pg_session
    from tests._pg_fixture import unique_code
    with pg_session() as db:
        w = _world(db)
        supplier_id = str(uuid.uuid4())
        db.execute(text(
            "INSERT INTO suppliers (id, supplier_code, supplier_name, is_active) "
        "VALUES (:id, :c, :n, true)"),
            {"id": supplier_id, "c": unique_code("S2"), "n": f"{MARKER} extra"})
        # 10 more real lines, pushing the total for this product well past the cap of 8.
        for i in range(10):
            poid = str(uuid.uuid4())
            db.execute(text(
                "INSERT INTO purchase_orders (id, po_number, supplier_id, issue_date, status, "
                "currency, source_system) VALUES (:id, :n, :s, :d, 'closed', 'USD', 'test')"),
                {"id": poid, "n": f"{MARKER}-EXTRA-{i}", "s": supplier_id,
                 "d": date(2020, 1, i + 1)})
            db.execute(text(
                "INSERT INTO purchase_order_lines (id, purchase_order_id, product_id, "
                "qty_ordered, qty_received, unit_cost, currency, line_status) "
                "VALUES (:id, :po, :p, 5, 0, 1.0, 'USD', 'open')"),
                {"id": str(uuid.uuid4()), "po": poid, "p": w["product_id"]})
        db.flush()

        out = purchase_trend_service.purchase_trend_for_run(db, w["run_id"], as_of=AS_OF)
        lines = out["products"][w["product_id"]]["lines"]

        assert len(lines) == purchase_trend_service.LINE_CAP


def test_a_product_with_no_purchases_reports_an_honest_empty_answer():
    from tests._pg_fixture import pg_session
    from app.models.product import Product, ProductCategory, UnitOfMeasure
    from tests._pg_fixture import unique_code

    with pg_session() as db:
        cat = ProductCategory(id=_u(), category_code=unique_code(MARKER),
                              category_name=f"{MARKER} cat")
        uom = UnitOfMeasure(id=_u(), uom_code=unique_code("U")[:20], uom_name=f"{MARKER} u")
        db.add_all([cat, uom])
        db.flush()
        product = Product(id=_u(), product_code=unique_code("P"), product_name=f"{MARKER} p",
                          category_id=cat.id, base_uom_id=uom.id, list_price=0,
                          is_active=True, is_discontinued=False)
        db.add(product)
        db.flush()

        run_id = _u()
        db.execute(text(
            "INSERT INTO scm.reorder_run (id, status, include_market, created_at) "
            "VALUES (:id, 'completed', false, now())"), {"id": run_id})
        db.execute(text(
            "INSERT INTO scm.reorder_recommendation "
            "(id, run_id, product_id, rec_type, rounded_qty, status) "
            "VALUES (:id, :r, :p, 'buy', 10, 'proposed')"),
            {"id": _u(), "r": run_id, "p": product.id})
        db.flush()

        out = purchase_trend_service.purchase_trend_for_run(db, run_id, as_of=AS_OF)
        entry = out["products"][str(product.id)]

        assert entry == {"recent_qty": 0.0, "previous_qty": 0.0, "lines": []}


# --------------------------------------------------------------------------- #
# company scoping
# --------------------------------------------------------------------------- #

def test_a_purchase_order_under_a_different_company_is_never_counted():
    """The production shape: two companies can hold a purchase order for what is, by
    product id, the SAME row in `pairs` (the run's own product). Company A's read must
    never pick up B's purchase order lines, even though the join key (product_id) matches
  - only `po.company_id` in the caller's scope may contribute to the trend or the lines.
    """
    from app.models.base import set_company_scope
    from app.models.company import Company
    from tests._pg_fixture import pg_session

    with pg_session() as db:
        set_company_scope(db, None)
        a = Company(id=_u(), code=f"{MARKER}-CA"[:20], name=f"{MARKER} company A")
        b = Company(id=_u(), code=f"{MARKER}-CB"[:20], name=f"{MARKER} company B")
        db.add_all([a, b])
        db.flush()

        w = _world(db, company_id=a.id)
        # Company B's own purchase order, for the SAME product row (a real cross-tenant
        # shape only if the catalogue is shared; the predicate must hold regardless).
        supplier_id = _u()
        db.execute(text(
            "INSERT INTO suppliers (id, supplier_code, supplier_name, is_active) "
        "VALUES (:id, :c, :n, true)"),
            {"id": supplier_id, "c": f"{MARKER}-SB-{uuid.uuid4().hex[:6]}",
             "n": f"{MARKER} company B supplier"})
        poid = _u()
        db.execute(text(
            "INSERT INTO purchase_orders (id, po_number, supplier_id, issue_date, status, "
            "currency, source_system, company_id) VALUES "
            "(:id, :n, :s, :d, 'closed', 'USD', 'test', :co)"),
            {"id": poid, "n": f"{MARKER}-B-LINE", "s": supplier_id,
             "d": date(2026, 7, 15), "co": b.id})
        db.execute(text(
            "INSERT INTO purchase_order_lines (id, purchase_order_id, product_id, "
            "qty_ordered, qty_received, unit_cost, currency, line_status, company_id) "
            "VALUES (:id, :po, :p, 500, 0, 999.0, 'USD', 'open', :co)"),
            {"id": _u(), "po": poid, "p": w["product_id"], "co": b.id})
        db.flush()

        set_company_scope(db, frozenset({a.id}))
        out = purchase_trend_service.purchase_trend_for_run(db, w["run_id"], as_of=AS_OF)
        entry = out["products"][w["product_id"]]

        assert all(l["po_number"] != f"{MARKER}-B-LINE" for l in entry["lines"])
        assert all(l["unit_cost"] != 999.0 for l in entry["lines"])
        # The recent window is unaffected by B's 500 units.
        assert entry["recent_qty"] == 150.0


def test_the_endpoint_serves_it_and_rbac_holds(scm_app):
    app, db, gcu, gcuk = scm_app
    scope = as_company_user(app, db, gcu, gcuk)
    # The endpoint takes no `as_of`: it windows off `date.today()`, so the world does too.
    w = _world(db, company_id=next(iter(scope)), as_of=date.today())

    with TestClient(app) as c:
        r = c.get(f"/api/v1/scm/reorder-runs/{w['run_id']}/purchase-trend")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["window_months"] == 3
        assert body["products"][w["product_id"]]["recent_qty"] == 150.0

    from app.dependencies import get_current_user, get_current_user_or_api_key
    nobody = seed_user(db, None)
    for dep in (gcu, gcuk):
        app.dependency_overrides[dep] = lambda: {"id": nobody, "email": "x@y", "roles": []}
    with TestClient(app) as c:
        denied = c.get(f"/api/v1/scm/reorder-runs/{w['run_id']}/purchase-trend")
    assert denied.status_code == 403


def test_a_run_from_another_company_is_a_404_not_a_leak(scm_app):
    """`assert_run_visible` is the gate every sibling endpoint relies on - a run stamped
    to a different company must read as not found, never served with someone else's
    purchase history."""
    from app.models.base import set_company_scope
    from app.models.company import Company

    app, db, gcu, gcuk = scm_app
    as_company_user(app, db, gcu, gcuk)
    set_company_scope(db, None)
    other = Company(id=_u(), code=f"{MARKER}-OTH"[:20], name=f"{MARKER} other company")
    db.add(other)
    db.flush()
    w = _world(db, company_id=other.id)

    with TestClient(app) as c:
        r = c.get(f"/api/v1/scm/reorder-runs/{w['run_id']}/purchase-trend")
    assert r.status_code == 404
