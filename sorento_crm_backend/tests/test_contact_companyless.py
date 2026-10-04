"""Lane CONTACT-COMPANYLESS, Phase 2 red tests (AC1-AC6, AC8, BE half of AC7).

UAC ``documentation/plans/multi-company/contact-companyless-acceptance-criteria.md``.

Contract pinned here:

* ``?company_scope=grants`` (the only accepted value, anything else 422) on
  ``GET /inventory/warehouses/``, ``GET /order-management/customers/select``,
  ``GET /master-data/products/select`` and ``GET /master-data/brands/``. A Bearer (staff)
  caller then sees every company it is GRANTED (admin = all), and every row carries
  ``company_id`` + ``company_name``. Without the flag nothing changes (active company only).
  An X-API-Key caller ignores the flag.
* The contact customer endpoints always read under the caller's grants: list (rows carry
  ``company_name``), add (link.company_id = the customer's company; a customer outside the
  grants is 404) and the ``contact_customer_link.unlink`` pending action.
* ``PUT /contacts/{id}/chatbot/facts/usual_brands|usual_sites`` accepts a brand / warehouse
  name of a granted non-active company.
* ``GET /inventory/stock-visibility/contacts/{id}``: resolved warehouses carry ``company_name``.

Real Bearer sessions and the REAL scope resolver run (only ``get_db`` is overridden), so the
switcher is simulated the way production does it: ``users.last_active_company_id``.
Postgres only (``blank_session``); every row is seeded here.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

# MUST be first app import - resolves a circular import in app.modules.runtime.guards
from app.main import app  # noqa: E402

from app.config import settings
from app.dependencies import get_current_user_or_api_key, get_db
from app.models.access import RespondContact, RespondContactCustomer
from app.models.base import set_company_scope
from app.models.company import UserCompany
from app.models.inventory import Warehouse
from app.models.order import Customer
from app.models.product import Brand
from app.models.user import User, UserRole, UserRoleAssignment
from app.services.company_scope import DEFAULT_COMPANY_ID
from app.services.user_service import UserPermissionService
from app.services.user_session_service import mint_session

from tests._mc_lookup_seed import MOCHA_ID, product as seed_product, seed_mocha
from tests._pg_fixture import blank_session, unique_code

SORENTO = DEFAULT_COMPANY_ID
BOTH = frozenset({SORENTO, MOCHA_ID})
CONTACTS = "/api/v1/user-management/contacts"
WAREHOUSES = "/api/v1/inventory/warehouses/"
CUSTOMER_SELECT = "/api/v1/order-management/customers/select"
PRODUCT_SELECT = "/api/v1/master-data/products/select"
BRANDS = "/api/v1/master-data/brands/"
STOCK_VIS = "/api/v1/inventory/stock-visibility/contacts"
GRANTS = {"company_scope": "grants"}
SHARED_WH = "ZZTSHARED-WH"
SHARED_CUSTOMER = "ZZT Shared Customer Name"


# --------------------------------------------------------------------- fixtures


@pytest.fixture
def db():
    with blank_session() as session:
        seed_mocha(session)
        set_company_scope(session, BOTH)
        yield session


@pytest.fixture
def client(db, monkeypatch):
    def _override_db():
        yield db

    app.dependency_overrides[get_db] = _override_db
    # RBAC slugs are not what is under test; company grants are.
    monkeypatch.setattr(UserPermissionService, "check_user_has_permission", lambda self, uid, slug: True)
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _user(db, *, admin: bool, grants: tuple[str, ...], active: str | None) -> dict:
    """A real user + a real Bearer session. `active` is what the header switcher stored."""
    row = User(
        id=str(uuid.uuid4()),
        email=f"zzt-{uuid.uuid4().hex[:8]}@test.com",
        name="ZZT User",
        status="ACTIVE",
        last_active_company_id=active,
    )
    db.add(row)
    db.flush()
    if admin:
        role = db.query(UserRole).filter(UserRole.slug == "admin").first()
        if role is None:
            role = UserRole(id=str(uuid.uuid4()), slug="admin", name="Admin", is_protected=False, is_default=False)
            db.add(role)
            db.flush()
        db.add(UserRoleAssignment(user_id=row.id, role_id=role.id))
    for company_id in grants:
        db.add(UserCompany(user_id=row.id, company_id=company_id))
    db.flush()
    token = mint_session(db, row.id).token
    db.commit()
    return {"id": row.id, "headers": {"Authorization": f"Bearer {token}"}}


@pytest.fixture
def admin_on_sorento(db):
    return _user(db, admin=True, grants=(), active=SORENTO)


@pytest.fixture
def sorento_only(db):
    return _user(db, admin=False, grants=(SORENTO,), active=SORENTO)


def _warehouse(db, company_id, code, name="Warehouse", segment=None) -> Warehouse:
    row = Warehouse(
        id=str(uuid.uuid4()),
        warehouse_code=code,
        warehouse_name=name,
        is_active=True,
        company_id=company_id,
        segment=segment,
    )
    db.add(row)
    db.flush()
    return row


def _customer(db, company_id, name=SHARED_CUSTOMER) -> Customer:
    row = Customer(
        id=str(uuid.uuid4()),
        customer_code=unique_code("ZZTC"),
        customer_name=name,
        company_id=company_id,
    )
    db.add(row)
    db.flush()
    return row


def _brand(db, company_id, name) -> Brand:
    row = Brand(id=str(uuid.uuid4()), brand_code=unique_code("ZZTB")[:50], brand_name=name, company_id=company_id)
    db.add(row)
    db.flush()
    return row


def _contact(db) -> RespondContact:
    row = RespondContact(
        id=str(uuid.uuid4()),
        phone_number=f"60{uuid.uuid4().int % 10**8 + 10**8}",
        name=f"ZZT contact {unique_code('c')}",
    )
    db.add(row)
    db.flush()
    return row


def _link(db, contact, customer) -> RespondContactCustomer:
    row = RespondContactCustomer(
        contact_id=contact.id,
        customer_id=customer.id,
        company_id=customer.company_id,
        source="manual",
        created_at=datetime(2026, 1, 1) + timedelta(minutes=1),
    )
    db.add(row)
    db.flush()
    return row


def _rows(response) -> list[dict]:
    assert response.status_code == 200, response.text
    body = response.json()
    return body["data"] if isinstance(body, dict) else body


def _domain_404(response) -> bool:
    return response.status_code == 404 and response.json() != {"detail": "Not Found"}


# ============================================================ AC1 locations picker


def test_ac1_warehouses_grants_lists_every_granted_company_with_company_fields(client, db, admin_on_sorento):
    _warehouse(db, SORENTO, SHARED_WH, "Sorento site")
    _warehouse(db, MOCHA_ID, SHARED_WH, "Mocha site")
    db.commit()

    rows = _rows(
        client.get(WAREHOUSES, params={**GRANTS, "query": SHARED_WH}, headers=admin_on_sorento["headers"])
    )

    assert len(rows) == 2
    assert {r["company_id"]: r["company_name"] for r in rows} == {SORENTO: "Sorento", MOCHA_ID: "Mocha"}


# ============================================================ AC2 dealer pool


def test_ac2_dealer_segment_with_grants_adds_other_companys_dealer_locations(client, db, admin_on_sorento):
    _warehouse(db, SORENTO, "ZZTDLR", segment="dealer")
    _warehouse(db, MOCHA_ID, "ZZTDLR", segment="dealer")
    _warehouse(db, MOCHA_ID, "ZZTPRJ", segment="project")
    db.commit()

    rows = _rows(
        client.get(
            WAREHOUSES,
            params={**GRANTS, "segment": "dealer", "query": "ZZT"},
            headers=admin_on_sorento["headers"],
        )
    )

    assert sorted(r["warehouse_code"] for r in rows) == ["ZZTDLR", "ZZTDLR"]
    assert {r["company_id"]: r["company_name"] for r in rows} == {SORENTO: "Sorento", MOCHA_ID: "Mocha"}


# ============================================================ AC3 customer picker + link


def test_ac3_customers_select_grants_finds_every_granted_company_with_company_fields(client, db, admin_on_sorento):
    _customer(db, SORENTO)
    _customer(db, MOCHA_ID)
    db.commit()

    rows = _rows(
        client.get(CUSTOMER_SELECT, params={**GRANTS, "query": SHARED_CUSTOMER}, headers=admin_on_sorento["headers"])
    )

    assert len(rows) == 2
    assert {r["company_id"]: r["company_name"] for r in rows} == {SORENTO: "Sorento", MOCHA_ID: "Mocha"}


def test_ac3_adding_a_customer_of_a_non_active_granted_company_links_its_own_company(client, db, admin_on_sorento):
    contact = _contact(db)
    mocha_customer = _customer(db, MOCHA_ID)
    db.commit()

    response = client.post(
        f"{CONTACTS}/{contact.id}/customers",
        json={"customer_ids": [mocha_customer.id]},
        headers=admin_on_sorento["headers"],
    )

    assert response.status_code == 201, response.text
    set_company_scope(db, BOTH)
    db.expire_all()
    link = db.query(RespondContactCustomer).filter(RespondContactCustomer.contact_id == contact.id).one()
    assert str(link.company_id) == MOCHA_ID
    assert link.customer_id == mocha_customer.id


# ============================================================ AC4 list + unlink


def test_ac4_contact_customers_lists_links_of_every_granted_company_with_company_name(client, db, admin_on_sorento):
    contact = _contact(db)
    _link(db, contact, _customer(db, SORENTO, "ZZT Sorento Cust"))
    _link(db, contact, _customer(db, MOCHA_ID, "ZZT Mocha Cust"))
    db.commit()

    rows = _rows(client.get(f"{CONTACTS}/{contact.id}/customers", headers=admin_on_sorento["headers"]))

    assert {r["customer_name"]: r["company_name"] for r in rows} == {
        "ZZT Sorento Cust": "Sorento",
        "ZZT Mocha Cust": "Mocha",
    }


def _park_unlink(client, link_id, headers):
    return client.post(
        "/api/v1/pending-actions",
        json={
            "action_key": "contact_customer_link.unlink",
            "entity_type": "contact_customer_link",
            "entity_id": str(link_id),
            "payload": {},
        },
        headers=headers,
    )


def _lapse_and_commit(client, db, headers, link_id):
    from app.models.sla import SlaFormAction

    db.query(SlaFormAction).filter(SlaFormAction.source_entity_id == str(link_id)).update(
        {"commit_at": datetime.utcnow() - timedelta(seconds=1)}, synchronize_session=False
    )
    db.commit()
    client.get(
        "/api/v1/pending-actions/current",
        params={"entity_type": "contact_customer_link", "entity_id": str(link_id)},
        headers=headers,
    )


def test_ac4_unlink_works_on_a_non_active_granted_company_link(client, db, admin_on_sorento):
    contact = _contact(db)
    link = _link(db, contact, _customer(db, MOCHA_ID))
    db.commit()
    link_id = link.id

    parked = _park_unlink(client, link_id, admin_on_sorento["headers"])
    assert parked.status_code == 202, parked.text
    _lapse_and_commit(client, db, admin_on_sorento["headers"], link_id)

    set_company_scope(db, BOTH)
    db.expire_all()
    assert db.query(RespondContactCustomer).filter(RespondContactCustomer.id == link_id).first() is None


# ============================================================ AC5 chatbot facts


def test_ac5_usual_brands_accepts_a_brand_of_a_granted_non_active_company(client, db, admin_on_sorento):
    contact = _contact(db)
    _brand(db, MOCHA_ID, "ZZT Mocha Only Brand")
    db.commit()

    response = client.put(
        f"{CONTACTS}/{contact.id}/chatbot/facts/usual_brands",
        json={"value": ["ZZT Mocha Only Brand"]},
        headers=admin_on_sorento["headers"],
    )

    assert response.status_code == 200, response.text


def test_ac5_usual_sites_accepts_a_warehouse_of_a_granted_non_active_company(client, db, admin_on_sorento):
    contact = _contact(db)
    _warehouse(db, MOCHA_ID, "ZZTMOCHA-SITE", "ZZT Mocha Only Site")
    db.commit()

    response = client.put(
        f"{CONTACTS}/{contact.id}/chatbot/facts/usual_sites",
        json={"value": ["ZZT Mocha Only Site"]},
        headers=admin_on_sorento["headers"],
    )

    assert response.status_code == 200, response.text


def test_ac5_products_and_brands_grants_rows_carry_company_fields(client, db, admin_on_sorento):
    seed_product(db, company_id=MOCHA_ID, code="ZZTMOCHA-SKU")
    _brand(db, MOCHA_ID, "ZZT Mocha Brand Two")
    db.commit()

    products = _rows(
        client.get(PRODUCT_SELECT, params={**GRANTS, "query": "ZZTMOCHA-SKU"}, headers=admin_on_sorento["headers"])
    )
    brands = _rows(
        client.get(BRANDS, params={**GRANTS, "query": "ZZT Mocha Brand Two"}, headers=admin_on_sorento["headers"])
    )

    assert [(p["company_id"], p["company_name"]) for p in products] == [(MOCHA_ID, "Mocha")]
    assert [(b["company_id"], b["company_name"]) for b in brands] == [(MOCHA_ID, "Mocha")]


# ============================================================ AC6 RBAC grants hold


def test_ac6_sorento_only_user_sees_only_sorento_warehouses_and_customers(client, db, sorento_only):
    _warehouse(db, SORENTO, SHARED_WH)
    _warehouse(db, MOCHA_ID, SHARED_WH)
    _customer(db, SORENTO)
    _customer(db, MOCHA_ID)
    db.commit()

    warehouses = _rows(
        client.get(WAREHOUSES, params={**GRANTS, "query": SHARED_WH}, headers=sorento_only["headers"])
    )
    customers = _rows(
        client.get(CUSTOMER_SELECT, params={**GRANTS, "query": SHARED_CUSTOMER}, headers=sorento_only["headers"])
    )

    assert [r["company_id"] for r in warehouses] == [SORENTO]
    assert warehouses[0]["company_name"] == "Sorento"
    assert [r["company_id"] for r in customers] == [SORENTO]
    assert customers[0]["company_name"] == "Sorento"


def test_ac6_sorento_only_user_lists_only_sorento_links(client, db, sorento_only):
    contact = _contact(db)
    _link(db, contact, _customer(db, SORENTO, "ZZT Sorento Cust"))
    _link(db, contact, _customer(db, MOCHA_ID, "ZZT Mocha Cust"))
    db.commit()

    rows = _rows(client.get(f"{CONTACTS}/{contact.id}/customers", headers=sorento_only["headers"]))

    assert [r["customer_name"] for r in rows] == ["ZZT Sorento Cust"]
    assert rows[0]["company_name"] == "Sorento"


def test_ac6_adding_a_mocha_customer_by_id_is_404_for_a_sorento_only_user(client, db, sorento_only):
    contact = _contact(db)
    mocha_customer = _customer(db, MOCHA_ID)
    db.commit()

    response = client.post(
        f"{CONTACTS}/{contact.id}/customers",
        json={"customer_ids": [mocha_customer.id]},
        headers=sorento_only["headers"],
    )

    assert _domain_404(response), response.text
    set_company_scope(db, BOTH)
    assert db.query(RespondContactCustomer).filter(RespondContactCustomer.contact_id == contact.id).count() == 0


def test_ac6_unlinking_a_mocha_link_is_404_for_a_sorento_only_user(client, db, sorento_only):
    contact = _contact(db)
    link = _link(db, contact, _customer(db, MOCHA_ID))
    db.commit()
    link_id = link.id

    parked = _park_unlink(client, link_id, sorento_only["headers"])

    assert parked.status_code == 404, parked.text
    set_company_scope(db, BOTH)
    assert db.query(RespondContactCustomer).filter(RespondContactCustomer.id == link_id).first() is not None


def test_ac6_a_mocha_brand_fact_is_refused_for_a_sorento_only_user(client, db, sorento_only):
    contact = _contact(db)
    _brand(db, MOCHA_ID, "ZZT Mocha Only Brand")
    db.commit()

    response = client.put(
        f"{CONTACTS}/{contact.id}/chatbot/facts/usual_brands",
        json={"value": ["ZZT Mocha Only Brand"]},
        headers=sorento_only["headers"],
    )

    assert response.status_code == 422, response.text


# ============================================================ AC7 (BE) stock visibility policy


def test_ac7_policy_warehouses_carry_company_name(client, db, admin_on_sorento):
    contact = _contact(db)
    mocha_wh = _warehouse(db, MOCHA_ID, "ZZTMOCHA-WH")
    db.commit()
    put = client.put(
        f"{STOCK_VIS}/{contact.id}",
        json={"mode": "detailed", "warehouse_ids": [mocha_wh.id], "excluded_warehouse_ids": None},
        headers=admin_on_sorento["headers"],
    )
    assert put.status_code == 200, put.text

    response = client.get(f"{STOCK_VIS}/{contact.id}", headers=admin_on_sorento["headers"])

    assert response.status_code == 200, response.text
    chips = response.json()["effective"]["warehouses"]
    assert [(c["code"], c["company_name"]) for c in chips] == [("ZZTMOCHA-WH", "Mocha")]


# ============================================================ AC8 unchanged without flag


def test_ac8_without_the_flag_every_shared_endpoint_follows_the_switcher(client, db, admin_on_sorento):
    sorento_wh = _warehouse(db, SORENTO, SHARED_WH)
    _warehouse(db, MOCHA_ID, SHARED_WH)
    _customer(db, SORENTO)
    _customer(db, MOCHA_ID)
    db.commit()
    headers = admin_on_sorento["headers"]

    warehouses = _rows(client.get(WAREHOUSES, params={"query": SHARED_WH}, headers=headers))
    customers = _rows(client.get(CUSTOMER_SELECT, params={"query": SHARED_CUSTOMER}, headers=headers))

    assert len(warehouses) == 1
    assert len(customers) == 1
    assert warehouses[0]["warehouse_code"] == SHARED_WH
    assert warehouses[0]["id"] == sorento_wh.id


def test_ac8_any_other_company_scope_value_is_422(client, db, admin_on_sorento):
    for url in (WAREHOUSES, CUSTOMER_SELECT, PRODUCT_SELECT, BRANDS):
        response = client.get(url, params={"company_scope": "all"}, headers=admin_on_sorento["headers"])
        assert response.status_code == 422, f"{url}: {response.status_code} {response.text}"


def test_ac8_api_key_caller_ignores_the_flag(client, db, admin_on_sorento, monkeypatch):
    """A contact-scoped X-API-Key (Sorento-only contact) keeps its scope under the flag,
    even though the acting principal is an admin granted both companies."""
    monkeypatch.setattr(settings, "external_api_key", "zzt-ccl-key")
    suffix = uuid.uuid4().hex[:8]
    ws_id, contact_pk, space_id, rid = str(uuid.uuid4()), str(uuid.uuid4()), f"zzt-space-{suffix}", f"zzt-rid-{suffix}"
    db.execute(
        text(
            "INSERT INTO respond_workspaces (id, space_id, name, api_key_ciphertext, created_at, updated_at) "
            "VALUES (:id, :sid, :name, :ck, now(), now())"
        ),
        {"id": ws_id, "sid": space_id, "name": f"zzt ws {suffix}", "ck": "zzt-cipher"},
    )
    db.execute(
        text(
            "INSERT INTO respond_contacts (id, phone_number, respond_io_id, workspace_id, created_at, updated_at) "
            "VALUES (:id, :phone, :rid, :ws, now(), now())"
        ),
        {"id": contact_pk, "phone": f"+60{suffix}", "rid": rid, "ws": ws_id},
    )
    db.execute(
        text(
            "INSERT INTO respond_contact_companies (id, respond_contact_id, company_id, created_at) "
            "VALUES (:id, :cid, :company, now())"
        ),
        {"id": str(uuid.uuid4()), "cid": contact_pk, "company": SORENTO},
    )
    _warehouse(db, SORENTO, SHARED_WH)
    _warehouse(db, MOCHA_ID, SHARED_WH)
    db.commit()
    app.dependency_overrides[get_current_user_or_api_key] = lambda: {"id": admin_on_sorento["id"]}

    rows = _rows(
        client.get(
            WAREHOUSES,
            params={**GRANTS, "query": SHARED_WH, "contact_id": rid, "space_id": space_id},
            headers={"X-API-Key": "zzt-ccl-key"},
        )
    )

    assert len(rows) == 1
    assert rows[0]["company_id"] == SORENTO


# ============================================================ memory: crm_view ignores the switcher


def _primary_link(db, contact, customer, minutes=1) -> RespondContactCustomer:
    row = RespondContactCustomer(
        contact_id=contact.id,
        customer_id=customer.id,
        company_id=customer.company_id,
        source="manual",
        is_primary=True,
        created_at=datetime(2026, 1, 1) + timedelta(minutes=minutes),
    )
    db.add(row)
    db.flush()
    return row


def _crm_facts(client, contact_id, headers) -> dict:
    response = client.get(f"{CONTACTS}/{contact_id}/chatbot/memory", headers=headers)
    assert response.status_code == 200, response.text
    facts = response.json().get("facts") or []
    return {f["key"]: f["value"] for f in facts if f.get("source") == "crm"}


def test_memory_crm_facts_of_a_sorento_primary_show_with_the_switcher_on_mocha(client, db):
    contact = _contact(db)
    customer = _customer(db, SORENTO, "ZZT Memory Cust")
    customer.market_segment_code = None
    _primary_link(db, contact, customer)
    on_sorento = _user(db, admin=True, grants=(), active=SORENTO)
    db.commit()
    expected = _crm_facts(client, contact.id, on_sorento["headers"])
    assert expected.get("customer", "").startswith("ZZT Memory Cust")  # control: works on Sorento

    on_mocha = _user(db, admin=True, grants=(), active=MOCHA_ID)
    db.commit()

    assert _crm_facts(client, contact.id, on_mocha["headers"]) == expected


def test_memory_primary_is_deterministic_across_the_switcher_when_both_companies_have_one(client, db):
    contact = _contact(db)
    earliest = _customer(db, MOCHA_ID, "ZZT Earliest Primary")
    later = _customer(db, SORENTO, "ZZT Later Primary")
    _primary_link(db, contact, earliest, minutes=1)
    _primary_link(db, contact, later, minutes=5)
    on_sorento = _user(db, admin=True, grants=(), active=SORENTO)
    on_mocha = _user(db, admin=True, grants=(), active=MOCHA_ID)
    db.commit()

    seen = [
        _crm_facts(client, contact.id, who["headers"])
        for who in (on_sorento, on_mocha, on_sorento, on_mocha)
    ]

    assert all(f == seen[0] for f in seen)
    assert seen[0]["customer"].startswith("ZZT Earliest Primary")
