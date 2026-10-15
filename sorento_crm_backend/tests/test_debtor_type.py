"""RED tests - FORM-ROUTING-CONDITIONS group DT (customer debtor type) and the customer
lookup service behind PC-2 / PC-4.

Contract: documentation/plans/forms/form-routing-conditions-acceptance-criteria.md
(DT-2, DT-3, DT-4, PC-2). Seam: `app/services/debtor_type.py`.

Postgres only (tests/_pg_fixture.py), every row seeded here, fake names only.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

# MUST be first app import - resolves a circular import in app.modules.runtime.guards.
from app.main import app  # noqa: E402

from app.dependencies import get_current_user, get_current_user_or_api_key, get_db
from app.models.base import set_company_scope
from app.models.order import Customer
from app.schemas.order import CustomerCreate
from app.services.company_scope import DEFAULT_COMPANY_ID, register_company_scope_listeners
from app.services.company_scope_resolver import apply_company_scope
from app.services.order_service import CustomerService
from app.services.user_service import UserPermissionService

from ._pg_fixture import blank_session

EDIT = "order_management.customers.edit"
BASE = "/api/v1/order-management/customers"


def _sfx() -> str:
    return uuid.uuid4().hex[:6].upper()


@pytest.fixture(autouse=True)
def _listeners():
    register_company_scope_listeners()


@pytest.fixture(autouse=True)
def _clean():
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def db():
    with blank_session() as s:
        set_company_scope(s, frozenset({DEFAULT_COMPANY_ID}))
        yield s


def _customer(db, code: str, name: str, **kw) -> Customer:
    row = Customer(customer_code=code, customer_name=name, company_id=DEFAULT_COMPANY_ID, **kw)
    db.add(row)
    db.flush()
    return row


def _stored(db, cid) -> str | None:
    return db.execute(text("SELECT debtor_type FROM customers WHERE id = :i"), {"i": str(cid)}).scalar_one()


# ------------------------------------------------------------------ constants (one mapping)


def test_debtor_type_constants():
    from app.services import debtor_type as dt

    assert dt.DEBTOR_TYPES == ("project", "project_cash", "cash_sales")
    assert dt.DEBTOR_TYPE_LABELS == {
        "project": "Project",
        "project_cash": "Project cash",
        "cash_sales": "Cash sales",
    }
    assert dt.SALES_TYPE_FOR_DEBTOR_TYPE == {
        "project": "project",
        "project_cash": "cash_sales",
        "cash_sales": "cash_sales",
    }


# ------------------------------------------------------------------ DT-2 rule (python side)

DT2_CASES = [
    ("302-ZZT001", "ZZT EMPIRE RESOURCES (PROJECT-CASH)", "project_cash"),
    ("302-ZZT002", "ZZT CHARLES RENO", "project_cash"),
    ("300-ZZT001", "ZZT GLOBAL INGRESS SDN BHD (PROJECT)", "project"),
    ("301-ZZTC01", "CASH (PERSON A)", None),
    ("301-ZZTC02", "CASH SALES - CUSTOMER A", None),
    ("301-ZZTC03", "CASH - CUSTOMER A", None),
    ("301-ZZTC04", "PERSON B (PROJECT-CASH)", "project"),
    ("300-ZZT002", "ZZT MOCHA ACCOUNT SDN BHD", "project"),
    ("301-ZZTC05", "CASHEW TRADING", "project"),  # "the word CASH", not a prefix of CASHEW
]


@pytest.mark.parametrize("code,name,expected", DT2_CASES)
def test_default_debtor_type_follows_the_dt2_rule(code, name, expected):
    from app.services.debtor_type import default_debtor_type

    assert default_debtor_type(code, name) == expected


# ------------------------------------------------------------------ DT-3 ORM insert hook


def test_insert_without_debtor_type_fills_the_default(db):
    c302 = _customer(db, f"302-ZZT{_sfx()}", "ZZT PLAIN TRADING")
    c301 = _customer(db, f"301-ZZT{_sfx()}", "CASH (PERSON A)")
    c300 = _customer(db, f"300-ZZT{_sfx()}", "ZZT GLOBAL SDN BHD")
    db.expire_all()
    assert _stored(db, c302.id) == "project_cash"
    assert _stored(db, c301.id) is None
    assert _stored(db, c300.id) == "project"


def test_explicit_debtor_type_on_create_is_kept(db):
    row = _customer(db, f"302-ZZT{_sfx()}", "ZZT KEEP TRADING", debtor_type="cash_sales")
    db.expire_all()
    assert _stored(db, row.id) == "cash_sales"


def test_changing_the_code_later_does_not_rederive(db):
    row = _customer(db, f"300-ZZT{_sfx()}", "ZZT STABLE SDN BHD")
    db.expire_all()
    assert _stored(db, row.id) == "project"
    row.customer_code = f"302-ZZT{_sfx()}"
    db.flush()
    db.expire_all()
    assert _stored(db, row.id) == "project"


# ------------------------------------------------------------------ DT-4 API


def _client(db, monkeypatch) -> TestClient:
    principal = {"id": str(uuid.uuid4()), "email": "zzt-debtor-type@test.com"}

    def _override_db():
        yield db

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: principal
    app.dependency_overrides[get_current_user_or_api_key] = lambda: principal

    async def _override_scope():
        scope = frozenset({DEFAULT_COMPANY_ID})
        set_company_scope(db, scope)
        return scope

    app.dependency_overrides[apply_company_scope] = _override_scope
    monkeypatch.setattr(
        UserPermissionService, "check_user_has_permission", lambda self, uid, slug: True
    )
    return TestClient(app)


def _api_customer(db, code=None) -> str:
    row = CustomerService(db).create_customer(
        CustomerCreate(customer_code=code or f"300-ZZT{_sfx()}", customer_name="ZZT Api Trading")
    )
    return str(row.id)


def test_get_returns_debtor_type(db, monkeypatch):
    cid = _api_customer(db, code=f"302-ZZT{_sfx()}")
    resp = _client(db, monkeypatch).get(f"{BASE}/{cid}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["debtor_type"] == "project_cash"


def test_list_row_carries_debtor_type(db, monkeypatch):
    code = f"302-ZZT{_sfx()}"
    _api_customer(db, code=code)
    resp = _client(db, monkeypatch).get(BASE, params={"query": code})
    assert resp.status_code == 200, resp.text
    assert [r["debtor_type"] for r in resp.json()["data"]] == ["project_cash"]


@pytest.mark.parametrize("value", ["project", "project_cash", "cash_sales", None])
def test_put_accepts_the_three_values_and_null(db, monkeypatch, value):
    cid = _api_customer(db)
    resp = _client(db, monkeypatch).put(f"{BASE}/{cid}", json={"debtor_type": value})
    assert resp.status_code == 200, resp.text
    assert resp.json()["debtor_type"] == value
    db.expire_all()
    assert _stored(db, cid) == value


def test_put_invalid_value_is_422(db, monkeypatch):
    cid = _api_customer(db)
    resp = _client(db, monkeypatch).put(f"{BASE}/{cid}", json={"debtor_type": "wholesale"})
    assert resp.status_code == 422, resp.text


def test_create_accepts_debtor_type_and_rejects_invalid(db, monkeypatch):
    client = _client(db, monkeypatch)
    ok = client.post(
        BASE,
        json={"customer_code": f"300-ZZT{_sfx()}", "customer_name": "ZZT Created", "debtor_type": "cash_sales"},
    )
    assert ok.status_code in (200, 201), ok.text
    assert ok.json()["debtor_type"] == "cash_sales"
    bad = client.post(
        BASE,
        json={"customer_code": f"300-ZZT{_sfx()}", "customer_name": "ZZT Bad", "debtor_type": "wholesale"},
    )
    assert bad.status_code == 422, bad.text


# ------------------------------------------------------------------ PC-2/PC-4 lookup_customers


def test_lookup_customers_shape_filter_and_sales_type(db):
    from app.services.debtor_type import lookup_customers

    tag = f"ZZQ{_sfx()}"
    pc = _customer(db, f"302-{tag}A", f"{tag} PROJECT CASH SDN BHD")
    pj = _customer(db, f"300-{tag}B", f"{tag} PROJECT SDN BHD")
    cs = _customer(db, f"300-{tag}C", f"{tag} CASH SALES SDN BHD", debtor_type="cash_sales")
    blank = _customer(db, f"301-{tag}D", f"CASH ({tag} PERSON A)")
    inactive = _customer(db, f"300-{tag}E", f"{tag} INACTIVE SDN BHD", is_active=False)
    _customer(db, f"300-OTHER{_sfx()}", "UNRELATED TRADING")

    items = lookup_customers(db, tag.lower())  # case-insensitive substring on name or code
    by_code = {i["customer_code"]: i for i in items}

    assert set(by_code) == {pc.customer_code, pj.customer_code, cs.customer_code, blank.customer_code}
    assert inactive.customer_code not in by_code
    for i in items:
        assert set(i) >= {"customer_id", "debtor_name", "customer_code", "debtor_type", "sales_type"}
    a = by_code[pc.customer_code]
    assert a["customer_id"] == str(pc.id)
    assert a["debtor_name"] == pc.customer_name
    assert (a["debtor_type"], a["sales_type"]) == ("project_cash", "cash_sales")
    assert (by_code[pj.customer_code]["debtor_type"], by_code[pj.customer_code]["sales_type"]) == ("project", "project")
    assert (by_code[cs.customer_code]["debtor_type"], by_code[cs.customer_code]["sales_type"]) == ("cash_sales", "cash_sales")
    assert (by_code[blank.customer_code]["debtor_type"], by_code[blank.customer_code]["sales_type"]) == (None, None)


def test_lookup_customers_matches_by_code_and_honours_limit(db):
    from app.services.debtor_type import lookup_customers

    tag = f"ZZL{_sfx()}"
    for n in range(3):
        _customer(db, f"300-{tag}{n}", f"ZZT LIMIT {n}")
    assert len(lookup_customers(db, tag, limit=2)) == 2
    assert len(lookup_customers(db, tag)) == 3
