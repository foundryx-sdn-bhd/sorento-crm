"""Lane CUSTOMER-BULK-OPS, slice 6 (U5): contacts list `customers=none` filter and `customer_codes`.

UAC ``documentation/plans/customers/customer-bulk-ops-acceptance-criteria.md`` U5.1, U5.2.
Red tests written before the implementation. Postgres only (``blank_session``); every row is
seeded here. Assertions go through the HTTP response (``response_model`` drops undeclared fields).
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

# MUST be first app import - resolves a circular import in app.modules.runtime.guards
from app.main import app  # noqa: E402

from app.dependencies import get_current_user, get_current_user_or_api_key, get_db
from app.models.access import RespondContact, RespondContactCustomer
from app.models.base import set_company_scope
from app.models.order import Customer
from app.services.company_scope import DEFAULT_COMPANY_ID
from app.services.company_scope_resolver import apply_company_scope
from app.services.user_service import UserPermissionService

from tests._mc_lookup_seed import MOCHA_ID, seed_mocha
from tests._pg_fixture import blank_session, unique_code

SORENTO = DEFAULT_COMPANY_ID
CONTACTS = "/api/v1/user-management/contacts/"
CONTACT_VIEW = "user_management.contacts.view"


@pytest.fixture
def db():
    with blank_session() as session:
        seed_mocha(session)
        set_company_scope(session, frozenset({SORENTO, MOCHA_ID}))
        yield session


@pytest.fixture
def state():
    """Mutable scope the request override reads on every call."""
    return {"scope": frozenset({SORENTO})}


@pytest.fixture
def client(db, state, monkeypatch):
    from app.models.user import User

    actor = User(id=str(uuid.uuid4()), email=f"zzt-{uuid.uuid4().hex[:8]}@test.com", name="ZZT Actor")
    db.add(actor)
    db.flush()
    principal = {"id": actor.id, "email": actor.email}

    def _override_db():
        yield db

    async def _override_scope():
        set_company_scope(db, state["scope"])
        return state["scope"]

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: principal
    app.dependency_overrides[get_current_user_or_api_key] = lambda: principal
    app.dependency_overrides[apply_company_scope] = _override_scope
    monkeypatch.setattr(
        UserPermissionService,
        "check_user_has_permission",
        lambda self, uid, slug: slug == CONTACT_VIEW,
    )
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _contact(db, tag: str) -> RespondContact:
    row = RespondContact(
        id=str(uuid.uuid4()),
        phone_number=f"60{uuid.uuid4().int % 10**8 + 10**8}",
        name=f"ZZT {tag} {unique_code('c')}",
    )
    db.add(row)
    db.flush()
    return row


def _customer(db, code: str, company_id=SORENTO) -> Customer:
    row = Customer(customer_code=code, customer_name=f"ZZT customer {code}", company_id=company_id)
    db.add(row)
    db.flush()
    return row


def _link(db, contact, customer) -> None:
    db.add(
        RespondContactCustomer(
            contact_id=contact.id, customer_id=customer.id, company_id=customer.company_id, source="manual"
        )
    )
    db.flush()


@pytest.fixture
def seeded(db):
    """Two linked contacts (one with two customers, codes inserted out of order) and two unlinked."""
    tag = unique_code("T")
    linked_two = _contact(db, f"{tag}-two")
    linked_one = _contact(db, f"{tag}-one")
    free_a = _contact(db, f"{tag}-freeA")
    free_b = _contact(db, f"{tag}-freeB")
    zeta = _customer(db, f"ZZT-Z-{unique_code('z')}")
    alpha = _customer(db, f"ZZT-A-{unique_code('a')}")
    _link(db, linked_two, zeta)
    _link(db, linked_two, alpha)
    _link(db, linked_one, alpha)
    return dict(tag=tag, two=linked_two, one=linked_one, free_a=free_a, free_b=free_b, zeta=zeta, alpha=alpha)


def _list(client, **params):
    return client.get(CONTACTS, params={"limit": 1000, **params})


def test_u5_2_customers_none_returns_only_unlinked_contacts(client, seeded):
    resp = _list(client, customers="none", query=seeded["tag"])
    assert resp.status_code == 200, resp.text
    body = resp.json()
    ids = {row["id"] for row in body["data"]}
    assert ids == {seeded["free_a"].id, seeded["free_b"].id}
    assert body["pagination"]["total"] == 2


def test_u5_2_without_filter_returns_linked_and_unlinked(client, seeded):
    body = _list(client, query=seeded["tag"]).json()
    assert {row["id"] for row in body["data"]} == {
        seeded["two"].id,
        seeded["one"].id,
        seeded["free_a"].id,
        seeded["free_b"].id,
    }
    assert body["pagination"]["total"] == 4


def test_u5_2_customers_none_total_and_paging_match(client, seeded):
    body = _list(client, customers="none", query=seeded["tag"], limit=1, page=2).json()
    assert body["pagination"]["total"] == 2
    assert len(body["data"]) == 1


def test_u5_1_rows_carry_sorted_customer_codes_and_empty_list_when_none(client, seeded):
    body = _list(client, query=seeded["tag"]).json()
    by_id = {row["id"]: row for row in body["data"]}
    expected_two = sorted([seeded["zeta"].customer_code, seeded["alpha"].customer_code])
    assert by_id[seeded["two"].id]["customer_codes"] == expected_two
    assert by_id[seeded["one"].id]["customer_codes"] == [seeded["alpha"].customer_code]
    assert by_id[seeded["free_a"].id]["customer_codes"] == []


def test_u5_2_invalid_customers_value_is_422(client, seeded):
    resp = _list(client, customers="foo")
    assert resp.status_code == 422


def test_u5_2_customers_none_is_company_scoped(client, db, state):
    """A contact linked only to another company's customer has no customer in the caller's company."""
    tag = unique_code("S")
    contact_x = _contact(db, f"{tag}-x")
    other_company_customer = _customer(db, f"ZZT-M-{unique_code('m')}", company_id=MOCHA_ID)
    _link(db, contact_x, other_company_customer)

    state["scope"] = frozenset({SORENTO})
    body = _list(client, customers="none", query=tag).json()
    by_id = {row["id"]: row for row in body["data"]}
    assert contact_x.id in by_id
    assert by_id[contact_x.id]["customer_codes"] == []
    assert body["pagination"]["total"] == 1

    state["scope"] = frozenset({SORENTO, MOCHA_ID})
    body = _list(client, customers="none", query=tag).json()
    assert contact_x.id not in {row["id"] for row in body["data"]}
