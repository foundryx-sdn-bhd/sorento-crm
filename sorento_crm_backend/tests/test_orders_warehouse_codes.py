"""AC-1121 (backend half) - `warehouse_codes` on `GET /api/v1/order-management/orders`.

`documentation/plans/chatbot/PLAN-chatbot-outstanding-report.md` Slice S3;
`documentation/plans/chatbot/chatbot-outstanding-report-acceptance-criteria.md` AC-1121:
"the last one is new on the backend route too and filters on `order_lines.warehouse_id`".

Postgres only, blank schema, every row seeded here (CI's database has none). Written before
the query param exists - the assertions below must fail because `warehouse_codes` is either
ignored (both DOs come back) or rejected as an unknown param, not because of a fixture bug.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

# MUST be first app import - resolves a circular import in app.modules.runtime.guards
from app.main import app  # noqa: E402

from app.dependencies import get_current_user, get_current_user_or_api_key, get_db
from app.models.base import set_company_scope
from app.services.company_scope import DEFAULT_COMPANY_ID
from app.services.company_scope_resolver import apply_company_scope

from tests._mc_lookup_seed import customer, order, order_line, product, warehouse
from tests._orders_view_seed import orders_view_principal
from tests._pg_fixture import blank_session, unique_code

BASE = "/api/v1/order-management/orders"


@pytest.fixture
def db():
    with blank_session() as s:
        set_company_scope(s, frozenset({DEFAULT_COMPANY_ID}))
        yield s


@pytest.fixture
def client(db):
    def _override_db():
        yield db

    # A real user holding the grant: the DO read routes are gated on it (#1669).
    principal = orders_view_principal(db)
    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: principal
    app.dependency_overrides[get_current_user_or_api_key] = lambda: principal

    async def _override_scope():
        scope = frozenset({DEFAULT_COMPANY_ID})
        set_company_scope(db, scope)
        return scope

    app.dependency_overrides[apply_company_scope] = _override_scope
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_warehouse_codes_filters_orders_to_the_matching_line(client, db):
    cust = customer(db, company_id=DEFAULT_COMPANY_ID, name="ZZT WH Filter Customer")
    prod = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("SKU"))
    wh_match = warehouse(db, company_id=DEFAULT_COMPANY_ID, code="ZZT-BRW-IB")
    wh_other = warehouse(db, company_id=DEFAULT_COMPANY_ID, code="ZZT-MWH-OB")

    o_match = order(db, company_id=DEFAULT_COMPANY_ID, customer_id=cust.id, number=unique_code("DOA"))
    order_line(db, company_id=DEFAULT_COMPANY_ID, order_id=o_match.id, product_id=prod.id, warehouse_id=wh_match.id)

    o_other = order(db, company_id=DEFAULT_COMPANY_ID, customer_id=cust.id, number=unique_code("DOB"))
    order_line(db, company_id=DEFAULT_COMPANY_ID, order_id=o_other.id, product_id=prod.id, warehouse_id=wh_other.id)
    db.commit()

    resp = client.get(BASE, params={"customer_ids": cust.id, "warehouse_codes": "ZZT-BRW-IB"})
    assert resp.status_code == 200, resp.text
    numbers = {row["order_number"] for row in resp.json()["data"]}
    assert numbers == {o_match.order_number}

    unfiltered = client.get(BASE, params={"customer_ids": cust.id})
    assert unfiltered.status_code == 200, unfiltered.text
    all_numbers = {row["order_number"] for row in unfiltered.json()["data"]}
    assert all_numbers == {o_match.order_number, o_other.order_number}
