"""NUMBER-FIELDS: PUT /api/v1/procurement/spo-allocations/{id} refuses explicit null quantities.

allocated_quantity, quantity_received and quantity_rejected are NOT NULL columns; an explicit null
is a 422 (today a 500) and the row is unchanged. Omitting them still works; 0 for received and
rejected is still accepted. Postgres only; own seed chain on a blank schema.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.dependencies import get_current_user, get_current_user_or_api_key, get_db
from app.main import app
from app.models.base import set_company_scope
from app.models.procurement import SPOAllocation
from app.services.company_scope import DEFAULT_COMPANY_ID
from app.services.company_scope_resolver import apply_company_scope

from tests._mc_lookup_seed import product, warehouse
from tests._pg_fixture import blank_session, unique_code

BASE = "/api/v1/procurement/spo-allocations"


@pytest.fixture
def env():
    with blank_session() as db:
        set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
        prod = product(db, company_id=DEFAULT_COMPANY_ID)
        wh = warehouse(db, company_id=DEFAULT_COMPANY_ID)
        row = SPOAllocation(
            id=str(uuid.uuid4()), company_id=DEFAULT_COMPANY_ID,
            spo_number=unique_code("ZZTSPO")[:50], spo_line_number=1,
            product_id=prod.id, warehouse_id=wh.id,
            allocated_quantity=10, quantity_received=4, quantity_rejected=1,
            receipt_status="pending",
        )
        db.add(row)
        db.commit()
        principal = {"id": str(uuid.uuid4()), "email": "zzt-spo@test.com"}

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
            yield TestClient(app, raise_server_exceptions=False), db, row.id
        finally:
            app.dependency_overrides.clear()


def _triple(db, row_id):
    db.rollback()
    db.expire_all()
    r = db.get(SPOAllocation, row_id)
    return (r.allocated_quantity, r.quantity_received, r.quantity_rejected)


@pytest.mark.parametrize("field", ["allocated_quantity", "quantity_received", "quantity_rejected"])
def test_explicit_null_is_422_and_row_unchanged(env, field):
    client, db, row_id = env
    resp = client.put(f"{BASE}/{row_id}", json={field: None})
    assert resp.status_code == 422, resp.text
    assert _triple(db, row_id) == (10, 4, 1)


def test_omitting_the_quantities_still_works(env):
    client, db, row_id = env
    resp = client.put(f"{BASE}/{row_id}", json={"allocation_notes": "ZZT note"})
    assert resp.status_code == 200, resp.text
    assert _triple(db, row_id) == (10, 4, 1)


def test_zero_received_and_rejected_still_accepted(env):
    client, db, row_id = env
    resp = client.put(f"{BASE}/{row_id}", json={"quantity_received": 0, "quantity_rejected": 0})
    assert resp.status_code == 200, resp.text
    assert _triple(db, row_id) == (10, 0, 0)
