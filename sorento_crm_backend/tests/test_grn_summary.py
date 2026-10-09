"""MULTI-ASK-PLAN S3 (owner, 9 Oct 2026): received quantity in a window and the last
receipt, per product, from approved goods-received notes.

`GET /api/v1/procurement/grn/summary?product_ids=...&date_from=...&date_to=...`
answers `{data: [{product_id, product_code, received_qty, grn_count, last_receipt_date,
last_receipt_qty}]}`, one row per asked product that exists, in the asked order. Received
is the sum of `picking_lines.quantity_picked` over APPROVED, not cancelled,
`goods_received` headers whose `picking_date` is in the window (the precedent at
`procurement_service.py:1068-1080`); the last receipt is the newest such header ALL TIME,
its quantity summed over that date.

Card: documentation/plans/chatbot/CARD-multi-ask-plan-09oct.md (Acceptance 9).
Postgres only, blank schema, every row seeded here.
"""
from __future__ import annotations

import uuid
from datetime import date

import pytest
from fastapi.testclient import TestClient

# MUST be first app import - resolves a circular import in app.modules.runtime.guards
from app.main import app  # noqa: E402

from app.dependencies import get_current_user_or_api_key, get_db
from app.models.base import set_company_scope
from app.models.procurement import PickingHeader, PickingLine
from app.services.company_scope import DEFAULT_COMPANY_ID
from app.services.company_scope_resolver import apply_company_scope

from tests._mc_lookup_seed import product
from tests._pg_fixture import blank_session, unique_code

URL = "/api/v1/procurement/grn/summary"


@pytest.fixture
def db():
    with blank_session() as s:
        set_company_scope(s, frozenset({DEFAULT_COMPANY_ID}))
        yield s


@pytest.fixture
def client(db):
    def _override_db():
        yield db

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user_or_api_key] = lambda: {
        "id": str(uuid.uuid4()),
        "email": "zzt-grn-summary@test.com",
    }

    async def _override_scope():
        scope = frozenset({DEFAULT_COMPANY_ID})
        set_company_scope(db, scope)
        return scope

    app.dependency_overrides[apply_company_scope] = _override_scope
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _grn(db, product_id, qty, on, *, status="approved", kind="goods_received", cancelled=False):
    header = PickingHeader(
        id=str(uuid.uuid4()),
        picking_number=unique_code("GRN")[:50],
        picking_type=kind,
        picking_date=on,
        picking_status=status,
        is_cancelled=cancelled,
        company_id=DEFAULT_COMPANY_ID,
    )
    db.add(header)
    db.flush()
    db.add(
        PickingLine(
            id=str(uuid.uuid4()),
            picking_header_id=header.id,
            product_id=product_id,
            quantity_expected=qty,
            quantity_picked=qty,
            company_id=DEFAULT_COMPANY_ID,
        )
    )
    db.flush()
    return header


def _get(client, ids, date_from="2026-01-01", date_to="2026-12-31"):
    params = [("product_ids", i) for i in ids]
    if date_from:
        params.append(("date_from", date_from))
    if date_to:
        params.append(("date_to", date_to))
    resp = client.get(URL, params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def test_received_in_window_and_last_receipt_all_time(client, db):
    p = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("P"))
    _grn(db, p.id, 100, date(2025, 12, 31))  # before the window
    _grn(db, p.id, 200, date(2026, 3, 1))
    _grn(db, p.id, 300, date(2026, 9, 18))
    _grn(db, p.id, 50, date(2026, 9, 18))  # a second GRN the same day
    _grn(db, p.id, 999, date(2026, 10, 1), status="draft")  # not approved
    _grn(db, p.id, 999, date(2026, 10, 2), cancelled=True)  # cancelled
    _grn(db, p.id, 999, date(2026, 10, 3), kind="goods_issue")  # not a GRN

    [row] = _get(client, [p.id])
    assert row["product_id"] == str(p.id)
    assert row["product_code"] == p.product_code
    assert row["received_qty"] == 550
    assert row["grn_count"] == 3
    assert row["last_receipt_date"] == "2026-09-18"
    assert row["last_receipt_qty"] == 350


def test_last_receipt_ignores_the_window(client, db):
    p = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("P"))
    _grn(db, p.id, 70, date(2025, 5, 5))
    [row] = _get(client, [p.id])
    assert row["received_qty"] == 0
    assert row["grn_count"] == 0
    assert row["last_receipt_date"] == "2025-05-05"
    assert row["last_receipt_qty"] == 70


def test_a_product_never_received_answers_zero_and_no_date(client, db):
    p = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("P"))
    [row] = _get(client, [p.id])
    assert (row["received_qty"], row["grn_count"]) == (0, 0)
    assert row["last_receipt_date"] is None and row["last_receipt_qty"] is None


def test_one_row_per_product_in_asked_order(client, db):
    a = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("PA"))
    b = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("PB"))
    _grn(db, a.id, 10, date(2026, 2, 2))
    _grn(db, b.id, 20, date(2026, 2, 3))
    rows = _get(client, [b.id, a.id])
    assert [r["product_id"] for r in rows] == [str(b.id), str(a.id)]
    assert [r["received_qty"] for r in rows] == [20, 10]


def test_no_window_counts_every_receipt(client, db):
    p = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("P"))
    _grn(db, p.id, 10, date(2024, 1, 1))
    _grn(db, p.id, 20, date(2026, 1, 1))
    [row] = _get(client, [p.id], date_from=None, date_to=None)
    assert row["received_qty"] == 30


def test_product_ids_are_required(client):
    assert client.get(URL).status_code == 422


def test_an_inverted_window_is_422(client, db):
    p = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("P"))
    resp = client.get(URL, params={"product_ids": p.id, "date_from": "2026-12-31", "date_to": "2026-01-01"})
    assert resp.status_code == 422
