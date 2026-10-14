"""Issue #1669: the delivery-order read routes are gated on `order_management.orders.view`.

Before the fix these routes depended only on `get_current_user_or_api_key` plus the
`order` module guard, so any signed-in staff user could read every DO in their
company. Each route must now answer 403 to a principal without the grant, 200 to one
with it, and resolve an X-API-Key to its integration's act-as user and check THAT
user's grant (the MCP server and the analytics connector call these routes by key).

Postgres only (`tests/_pg_fixture.py`), every row seeded here and rolled back.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

# MUST be first app import - resolves a circular import in app.modules.runtime.guards
from app.main import app  # noqa: E402

from app.dependencies import get_current_user, get_current_user_or_api_key, get_db
from app.models.base import set_company_scope
from app.models.integration import Integration
from app.models.user import User
from app.services.company_scope import DEFAULT_COMPANY_ID
from app.services.company_scope_resolver import apply_company_scope
from app.services.integration_key_service import IntegrationKeyService

from tests._mc_lookup_seed import customer, order, product
from tests._orders_view_seed import ORDERS_VIEW as PERMISSION_SLUG, grant_orders_view, user as _user
from tests._pg_fixture import blank_session, unique_code

BASE = "/api/v1/order-management/orders"


@pytest.fixture
def db():
    with blank_session() as s:
        set_company_scope(s, frozenset({DEFAULT_COMPANY_ID}))
        yield s


def _key_for(db, user: User) -> str:
    integration = Integration(
        id=str(uuid.uuid4()), name=unique_code("zzt-do-key"), type="automation",
        act_as_user_id=user.id, is_active=True,
    )
    db.add(integration)
    db.flush()
    return IntegrationKeyService(db).issue_key(integration)


@pytest.fixture
def seeded(db):
    cust = customer(db, company_id=DEFAULT_COMPANY_ID)
    prod = product(db, company_id=DEFAULT_COMPANY_ID)
    do = order(db, company_id=DEFAULT_COMPANY_ID, customer_id=cust.id)
    no_grant = _user(db, "NG")
    granted = _user(db, "OK")
    grant_orders_view(db, granted)
    no_grant_key = _key_for(db, no_grant)
    granted_key = _key_for(db, granted)
    db.commit()
    return {
        "do": do, "product": prod, "no_grant": no_grant, "granted": granted,
        "no_grant_key": no_grant_key, "granted_key": granted_key,
    }


@pytest.fixture
def make_client(db):
    def _override_db():
        yield db

    async def _override_scope():
        scope = frozenset({DEFAULT_COMPANY_ID})
        set_company_scope(db, scope)
        return scope

    def _make(user: User | None) -> TestClient:
        app.dependency_overrides.clear()
        app.dependency_overrides[get_db] = _override_db
        app.dependency_overrides[apply_company_scope] = _override_scope
        if user is not None:
            principal = {"id": user.id, "email": user.email}
            app.dependency_overrides[get_current_user] = lambda: principal
            app.dependency_overrides[get_current_user_or_api_key] = lambda: principal
        return TestClient(app)

    try:
        yield _make
    finally:
        app.dependency_overrides.clear()


def _routes(s) -> list[tuple[str, str, dict]]:
    """(label, path, query) for every DO read route on the orders router."""
    do_id = s["do"].id
    return [
        ("list", f"{BASE}/", {}),
        ("debtors", f"{BASE}/debtors", {}),
        ("by-product", f"{BASE}/by-product", {"product_ids": s["product"].id}),
        ("analytics", f"{BASE}/analytics", {"metric": "count"}),
        ("fulfilled-complaints", f"{BASE}/{do_id}/fulfilled-complaints", {}),
        ("detail", f"{BASE}/{do_id}", {}),
    ]


ROUTE_LABELS = ["list", "debtors", "by-product", "analytics", "fulfilled-complaints", "detail"]


def _route(s, label):
    return next(r for r in _routes(s) if r[0] == label)


@pytest.mark.parametrize("label", ROUTE_LABELS)
def test_signed_in_user_without_view_grant_is_403(label, seeded, make_client):
    _, path, query = _route(seeded, label)
    res = make_client(seeded["no_grant"]).get(path, params=query)
    assert res.status_code == 403, res.text
    assert PERMISSION_SLUG in res.text


@pytest.mark.parametrize("label", ROUTE_LABELS)
def test_signed_in_user_with_view_grant_is_200(label, seeded, make_client):
    _, path, query = _route(seeded, label)
    res = make_client(seeded["granted"]).get(path, params=query)
    assert res.status_code == 200, res.text


@pytest.mark.parametrize("label", ROUTE_LABELS)
def test_api_key_act_as_user_without_grant_is_403(label, seeded, make_client):
    _, path, query = _route(seeded, label)
    res = make_client(None).get(path, params=query, headers={"X-API-Key": seeded["no_grant_key"]})
    assert res.status_code == 403, res.text


@pytest.mark.parametrize("label", ROUTE_LABELS)
def test_api_key_act_as_user_with_grant_is_200(label, seeded, make_client):
    _, path, query = _route(seeded, label)
    res = make_client(None).get(path, params=query, headers={"X-API-Key": seeded["granted_key"]})
    assert res.status_code == 200, res.text
