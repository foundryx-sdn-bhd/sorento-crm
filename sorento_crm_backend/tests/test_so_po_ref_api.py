"""SO-PO-REF API (U8, U9): list + detail carry `po_ref`; the list search matches it.

Same substrate as `test_project_label_api.py` (`scm_app`, rolled-back savepoint). The key is
seeded literally so a failure here is about the read path, not the fold helper.
"""
from __future__ import annotations

import uuid
from datetime import date

from fastapi.testclient import TestClient

from app.models.order import Customer, SalesOrder
from tests.scm.conftest import as_user, requires_pg, scm_app, seed_user  # noqa: F401

pytestmark = requires_pg

MARKER = "ZZTPOREF"


def _uid() -> str:
    return str(uuid.uuid4())


def _seed(scm_app, *, po_ref, key):
    app, db, gcu, gcuak = scm_app
    as_user(app, gcu, gcuak, seed_user(db, "purchasing"))
    customer = Customer(
        id=_uid(), customer_code=f"{MARKER}-C-{_uid()[:6]}".upper(),
        customer_name=f"{MARKER} Kitchens", is_active=True,
    )
    db.add(customer)
    db.flush()
    so = SalesOrder(
        id=_uid(), so_number=f"{MARKER}-SO-{_uid()[:8]}".upper(), customer_id=customer.id,
        status="open", priority="normal", order_date=date(2026, 7, 16),
        source_system="scm_upload", po_ref=po_ref, po_ref_key=key,
    )
    db.add(so)
    db.flush()
    return app, so


def _ids(res):
    assert res.status_code == 200, res.text
    return {r["id"] for r in res.json()["data"]}


def test_u8_detail_and_list_carry_the_raw_po_ref(scm_app):
    app, so = _seed(scm_app, po_ref="PO-4500 123", key="4500123")
    with TestClient(app) as c:
        detail = c.get(f"/api/v1/scm/sales-orders/{so.id}")
        listing = c.get("/api/v1/scm/sales-orders", params={"query": so.so_number})

    assert detail.status_code == 200, detail.text
    assert "po_ref" in detail.json()
    assert detail.json()["po_ref"] == "PO-4500 123"
    row = next(r for r in listing.json()["data"] if r["id"] == str(so.id))
    assert row["po_ref"] == "PO-4500 123"


def test_u8_blank_po_ref_is_null(scm_app):
    app, so = _seed(scm_app, po_ref=None, key=None)
    with TestClient(app) as c:
        detail = c.get(f"/api/v1/scm/sales-orders/{so.id}")

    assert "po_ref" in detail.json()
    assert detail.json()["po_ref"] is None


def test_u9_search_matches_the_raw_value_case_insensitively(scm_app):
    app, so = _seed(scm_app, po_ref="PO-4500 123", key="4500123")
    with TestClient(app) as c:
        assert str(so.id) in _ids(c.get("/api/v1/scm/sales-orders", params={"query": "4500"}))
        assert str(so.id) in _ids(c.get("/api/v1/scm/sales-orders", params={"query": "po-4500"}))


def test_u9_search_matches_the_folded_key_of_the_typed_text(scm_app):
    app, so = _seed(scm_app, po_ref="PO-4500 123", key="4500123")
    with TestClient(app) as c:
        ids = _ids(c.get("/api/v1/scm/sales-orders", params={"query": "po 4500-123"}))

    assert str(so.id) in ids


def test_u9_an_unrelated_search_does_not_match(scm_app):
    app, so = _seed(scm_app, po_ref="PO-4500 123", key="4500123")
    with TestClient(app) as c:
        ids = _ids(c.get("/api/v1/scm/sales-orders", params={"query": "9999888"}))

    assert str(so.id) not in ids


def test_u9_sort_by_po_ref_orders_the_list(scm_app):
    app, a = _seed(scm_app, po_ref="ZETA-1", key="ZETA1")
    _, b = _seed(scm_app, po_ref="ALPHA-1", key="ALPHA1")
    with TestClient(app) as c:
        res = c.get(
            "/api/v1/scm/sales-orders",
            params={"sort": "po_ref", "dir": "asc", "query": MARKER, "limit": 100},
        )

    assert res.status_code == 200, res.text
    order = [r["id"] for r in res.json()["data"] if r["id"] in {str(a.id), str(b.id)}]
    assert order == [str(b.id), str(a.id)]
