"""RED tests - FORM-ROUTING-CONDITIONS PC-4 (internal side).

* GET /api/v1/procurement/purchase-requests/lookups/customers?q= returns the same item shape as
  the portal lookup, gated like the other PR routes (sign-in only; unauthenticated gets 401).
* Internal PR / SF create + update accept `customer_id`, return it, and refuse an unknown id.

Blank Postgres schema, every row seeded here.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.main import app  # noqa: E402

from app.dependencies import get_current_user, get_current_user_or_api_key, get_db
from app.models.base import set_company_scope
from app.models.order import Customer
from app.models.user import User
from app.services.company_scope import DEFAULT_COMPANY_ID, register_company_scope_listeners
from app.services.company_scope_resolver import apply_company_scope
from app.services.user_service import UserPermissionService
from tests._pg_fixture import blank_session

BASE = "/api/v1/procurement/purchase-requests"


def _sfx() -> str:
    return uuid.uuid4().hex[:6].upper()


@pytest.fixture(autouse=True)
def _clean():
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def db():
    register_company_scope_listeners()
    with blank_session() as s:
        set_company_scope(s, frozenset({DEFAULT_COMPANY_ID}))
        yield s


def _customer(db, code, name, **kw) -> Customer:
    row = Customer(customer_code=code, customer_name=name, company_id=DEFAULT_COMPANY_ID, **kw)
    db.add(row)
    db.flush()
    return row


def _client(db, monkeypatch, *, granted: bool = True) -> TestClient:
    uid = str(uuid.uuid4())
    db.add(User(id=uid, email=f"{uid}@t.zzt", name="CS PIC X", status="ACTIVE"))
    db.flush()
    principal = {"id": uid, "email": f"{uid}@t.zzt"}
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: principal
    app.dependency_overrides[get_current_user_or_api_key] = lambda: principal
    monkeypatch.setattr(
        UserPermissionService, "check_user_has_permission", lambda self, u, slug: granted
    )

    async def _override_scope():
        scope = frozenset({DEFAULT_COMPANY_ID})
        set_company_scope(db, scope)
        return scope

    app.dependency_overrides[apply_company_scope] = _override_scope
    return TestClient(app)


def test_lookup_returns_the_shared_item_shape(db, monkeypatch):
    tag = f"ZZQ{_sfx()}"
    pc = _customer(db, f"302-{tag}", f"{tag} PROJECT CASH SDN BHD")
    pj = _customer(db, f"300-{tag}", f"{tag} PROJECT SDN BHD")
    _customer(db, f"300-{tag}X", f"{tag} INACTIVE SDN BHD", is_active=False)

    res = _client(db, monkeypatch).get(f"{BASE}/lookups/customers", params={"q": tag})

    assert res.status_code == 200, res.text
    items = {i["customer_code"]: i for i in res.json()}
    assert set(items) == {pc.customer_code, pj.customer_code}
    assert items[pc.customer_code] == {
        "customer_id": str(pc.id),
        "debtor_name": pc.customer_name,
        "customer_code": pc.customer_code,
        "debtor_type": "project_cash",
        "sales_type": "cash_sales",
    }
    assert items[pj.customer_code]["sales_type"] == "project"


def test_lookup_unauthenticated_is_401(db):
    """Gate = the PR edit route's: sign-in only (no PR permission slug exists), so no
    get_current_user override means 401."""
    app.dependency_overrides[get_db] = lambda: db
    res = TestClient(app).get(f"{BASE}/lookups/customers", params={"q": "ZZT"})
    assert res.status_code == 401, res.text


def test_lookup_route_is_not_swallowed_by_the_id_route(db, monkeypatch):
    """`/lookups/customers` must not be read as GET /{request_id} (which 422s a non-uuid)."""
    res = _client(db, monkeypatch).get(f"{BASE}/lookups/customers", params={"q": ""})
    assert res.status_code == 200, res.text
    assert isinstance(res.json(), list)


@pytest.mark.parametrize("kind", ["purchase_request", "sponsorship_form"])
def test_create_accepts_and_returns_customer_id(db, monkeypatch, kind):
    cust = _customer(db, f"300-ZZT{_sfx()}", "ZZT INTERNAL CUSTOMER SDN BHD")
    body = {"request_type": kind, "customer_name": cust.customer_name, "customer_id": str(cust.id)}
    if kind == "sponsorship_form":
        body["products"] = []

    res = _client(db, monkeypatch).post(f"{BASE}/", json=body)

    assert res.status_code == 201, res.text
    assert res.json()["customer_id"] == str(cust.id)
    stored = db.execute(
        text("SELECT customer_id FROM purchase_requests WHERE id = :i"), {"i": res.json()["id"]}
    ).scalar_one()
    assert str(stored) == str(cust.id)


def test_update_can_set_and_clear_customer_id(db, monkeypatch):
    cust = _customer(db, f"300-ZZT{_sfx()}", "ZZT UPDATE CUSTOMER SDN BHD")
    client = _client(db, monkeypatch)
    created = client.post(f"{BASE}/", json={"request_type": "purchase_request", "customer_name": "FREE TEXT"})
    assert created.status_code == 201, created.text
    rid = created.json()["id"]

    put = client.put(f"{BASE}/{rid}", json={"customer_id": str(cust.id)})
    assert put.status_code == 200, put.text
    assert put.json()["customer_id"] == str(cust.id)

    cleared = client.put(f"{BASE}/{rid}", json={"customer_id": None})
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["customer_id"] is None


def test_create_with_unknown_customer_id_is_refused(db, monkeypatch):
    res = _client(db, monkeypatch).post(
        f"{BASE}/",
        json={"request_type": "purchase_request", "customer_name": "X", "customer_id": str(uuid.uuid4())},
    )
    assert res.status_code in (400, 422), res.text
