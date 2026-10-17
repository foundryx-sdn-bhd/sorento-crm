"""NUMBER-FIELDS: an order-management order line refuses an explicit null quantity.

POST /orders/{id}/lines and PUT /orders/{id}/lines/{line_id}. Explicit `"quantity": null` is a
422 and the row is untouched; quantity 0 is still accepted (existing data has 0-qty lines); a PUT
that omits quantity leaves it unchanged. Postgres only; own seed chain on a blank schema.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.dependencies import get_current_user, get_current_user_or_api_key, get_db
from app.main import app
from app.models.base import set_company_scope
from app.models.order import OrderLine
from app.services.company_scope import DEFAULT_COMPANY_ID
from app.services.company_scope_resolver import apply_company_scope

from tests._mc_lookup_seed import customer, order, order_line, product, warehouse
from tests._orders_view_seed import orders_view_principal
from tests._pg_fixture import blank_session

BASE = "/api/v1/order-management/orders"


@pytest.fixture
def env():
    with blank_session() as db:
        set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
        cust = customer(db, company_id=DEFAULT_COMPANY_ID)
        prod = product(db, company_id=DEFAULT_COMPANY_ID)
        wh = warehouse(db, company_id=DEFAULT_COMPANY_ID)
        do = order(db, company_id=DEFAULT_COMPANY_ID, customer_id=cust.id)
        line = order_line(
            db, company_id=DEFAULT_COMPANY_ID, order_id=do.id, product_id=prod.id,
            warehouse_id=wh.id, quantity=7,
        )
        principal = orders_view_principal(db)
        db.commit()

        def _override_db():
            yield db

        async def _override_scope():
            scope = frozenset({DEFAULT_COMPANY_ID})
            set_company_scope(db, scope)
            return scope

        app.dependency_overrides[get_db] = _override_db
        app.dependency_overrides[apply_company_scope] = _override_scope
        app.dependency_overrides[get_current_user] = lambda: principal
        app.dependency_overrides[get_current_user_or_api_key] = lambda: principal
        try:
            yield {"client": TestClient(app), "db": db, "do": do, "prod": prod, "wh": wh, "line": line}
        finally:
            app.dependency_overrides.clear()


def _qty(db, line_id):
    db.expire_all()
    return db.get(OrderLine, line_id).quantity


def _count(db, order_id):
    db.expire_all()
    return db.query(OrderLine).filter(OrderLine.order_id == order_id).count()


def test_post_line_with_null_quantity_is_422_and_no_row_added(env):
    e = env
    resp = e["client"].post(f"{BASE}/{e['do'].id}/lines", json={
        "product_id": e["prod"].id, "warehouse_id": e["wh"].id, "quantity": None,
    })
    assert resp.status_code == 422, resp.text
    assert _count(e["db"], e["do"].id) == 1


def test_put_line_with_null_quantity_is_422_and_quantity_unchanged(env):
    e = env
    resp = e["client"].put(f"{BASE}/{e['do'].id}/lines/{e['line'].id}", json={"quantity": None})
    assert resp.status_code == 422, resp.text
    assert _qty(e["db"], e["line"].id) == Decimal("7")


def test_post_line_with_zero_quantity_is_still_accepted(env):
    e = env
    resp = e["client"].post(f"{BASE}/{e['do'].id}/lines", json={
        "product_id": e["prod"].id, "warehouse_id": e["wh"].id, "quantity": 0,
    })
    assert resp.status_code == 201, resp.text
    assert _count(e["db"], e["do"].id) == 2


def test_put_line_with_zero_quantity_is_still_accepted(env):
    e = env
    resp = e["client"].put(f"{BASE}/{e['do'].id}/lines/{e['line'].id}", json={"quantity": 0})
    assert resp.status_code == 200, resp.text
    assert _qty(e["db"], e["line"].id) == Decimal("0")


def test_put_line_omitting_quantity_leaves_it_unchanged(env):
    e = env
    resp = e["client"].put(f"{BASE}/{e['do'].id}/lines/{e['line'].id}", json={"unit_price": 12})
    assert resp.status_code == 200, resp.text
    assert _qty(e["db"], e["line"].id) == Decimal("7")
