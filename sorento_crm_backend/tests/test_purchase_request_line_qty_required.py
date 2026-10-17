"""NUMBER-FIELDS: an internal purchase request line must carry a quantity > 0.

Routes: POST /, PUT /{id}, POST /{id}/update-and-reply under /api/v1/procurement/purchase-requests.
A null, missing or <= 0 quantity is a 422 and nothing is written (on PUT the existing line keeps
its quantity). A stored NULL (the external intake can create one) must still READ fine.
Postgres only; every test seeds its own header and lines on a blank schema.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app import dependencies as deps
from app.main import app
from app.models.procurement import PurchaseRequestHeader, PurchaseRequestLine
from tests._pg_fixture import blank_session, unique_code

BASE = "/api/v1/procurement/purchase-requests"


@pytest.fixture
def client_and_db():
    with blank_session() as sess:
        def _override_db():
            yield sess

        principal = {"id": str(uuid.uuid4()), "email": "zzt-pr@test.com"}
        app.dependency_overrides[deps.get_db] = _override_db
        app.dependency_overrides[deps.get_current_user_or_api_key] = lambda: principal
        app.dependency_overrides[deps.get_current_user] = lambda: principal
        try:
            yield TestClient(app), sess
        finally:
            for dep in (deps.get_db, deps.get_current_user_or_api_key, deps.get_current_user):
                app.dependency_overrides.pop(dep, None)


def _header(db, qty="28"):
    pr = PurchaseRequestHeader(
        id=str(uuid.uuid4()), request_type="purchase_request",
        request_number=f"ZZTPR{unique_code()[:6].upper()}",
        customer_name="ZZT Customer", status="processed_by_cs", approval_status="approved",
    )
    db.add(pr)
    db.flush()
    line = PurchaseRequestLine(
        id=str(uuid.uuid4()), purchase_request_id=pr.id, item_code="ZZT BASIN",
        quantity=None if qty is None else Decimal(qty), sort_order=0,
    )
    db.add(line)
    db.commit()
    return pr, line


def _lines(db, request_id):
    db.expire_all()
    return db.query(PurchaseRequestLine).filter(PurchaseRequestLine.purchase_request_id == request_id).all()


BAD = [
    pytest.param({"item_code": "ZZT X", "quantity": None}, id="null"),
    pytest.param({"item_code": "ZZT X"}, id="missing"),
    pytest.param({"item_code": "ZZT X", "quantity": 0}, id="zero"),
    pytest.param({"item_code": "ZZT X", "quantity": -3}, id="negative"),
]


@pytest.mark.parametrize("line", BAD)
def test_create_with_bad_line_quantity_is_422_and_nothing_written(client_and_db, line):
    client, db = client_and_db
    number = f"ZZTNEW{unique_code()[:6].upper()}"
    resp = client.post(f"{BASE}/", json={
        "request_type": "purchase_request", "request_number": number,
        "customer_name": "ZZT Customer", "products": [line],
    })
    assert resp.status_code == 422, resp.text
    db.expire_all()
    assert db.query(PurchaseRequestHeader).filter(PurchaseRequestHeader.request_number == number).count() == 0


@pytest.mark.parametrize("line", BAD)
def test_put_with_bad_line_quantity_is_422_and_existing_line_keeps_28(client_and_db, line):
    client, db = client_and_db
    pr, existing = _header(db)
    resp = client.put(f"{BASE}/{pr.id}", json={"products": [line]})
    assert resp.status_code == 422, resp.text
    rows = _lines(db, pr.id)
    assert [(r.id, r.quantity) for r in rows] == [(existing.id, Decimal("28"))]


@pytest.mark.parametrize("line", BAD)
def test_update_and_reply_with_bad_line_quantity_is_422_and_unchanged(client_and_db, line):
    client, db = client_and_db
    pr, existing = _header(db)
    resp = client.post(f"{BASE}/{pr.id}/update-and-reply", json={"reply_message": "hi", "products": [line]})
    assert resp.status_code == 422, resp.text
    rows = _lines(db, pr.id)
    assert [(r.id, r.quantity) for r in rows] == [(existing.id, Decimal("28"))]


def test_put_with_valid_quantity_still_saves(client_and_db):
    client, db = client_and_db
    pr, _ = _header(db)
    resp = client.put(f"{BASE}/{pr.id}", json={"products": [{"item_code": "ZZT BASIN", "quantity": 28}]})
    assert resp.status_code == 200, resp.text
    rows = _lines(db, pr.id)
    assert len(rows) == 1 and rows[0].quantity == Decimal("28")


def test_create_with_valid_quantity_still_saves(client_and_db):
    client, db = client_and_db
    number = f"ZZTOK{unique_code()[:6].upper()}"
    resp = client.post(f"{BASE}/", json={
        "request_type": "purchase_request", "request_number": number,
        "customer_name": "ZZT Customer", "products": [{"item_code": "ZZT X", "quantity": 28}],
    })
    assert resp.status_code == 201, resp.text


def test_stored_null_quantity_line_still_reads_through_get(client_and_db):
    client, db = client_and_db
    pr, _ = _header(db, qty=None)
    resp = client.get(f"{BASE}/{pr.id}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["lines"][0]["quantity"] is None
