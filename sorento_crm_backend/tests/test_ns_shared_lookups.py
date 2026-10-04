"""Shared lookups are readable by any signed-in user; writes and admin reads stay gated.

Lane NS-SHARED-LOOKUPS, never-stuck lever L10. Plan + UAC:
`documentation/plans/never-stuck/PLAN-ns-shared-lookups.md`,
`documentation/plans/never-stuck/ns-shared-lookups-acceptance-criteria.md`.

Owner ruling 1 Oct 2026: the people picker, the project / task / lead status flow, contact
access types, market segments and the unit / brand / category / country selects open read-only
to every signed-in user, with the fields a picker needs. The roles list stays locked. The caller
here is a "restricted salesperson": a real active user whose permission set is the `allow` set
each test controls (empty by default), holding no admin role.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

from app.database import get_db
from app.dependencies import get_current_user, get_current_user_or_api_key
from app.main import app
from app.models.access import ContactAccessType, MarketSegment
from app.models.base import set_company_scope
from app.models.country import Country
from app.models.product import Brand, ProductCategory, UnitOfMeasure
from app.models.user import User, UserRole, UserRoleAssignment, UserStatus
from app.services import project_seed_service
from app.services.company_scope_resolver import apply_company_scope
from tests._pg_fixture import blank_session, unique_code

BASE = "/api/v1"
PROJECTS_VIEW = "projects.projects.view"
MARKER = "ZZT-NSL"


def _uid() -> str:
    return str(uuid.uuid4())


@pytest.fixture
def db():
    with blank_session() as s:
        yield s


def _role(db, slug: str) -> str:
    row = db.query(UserRole).filter(UserRole.slug == slug).first()
    if row is None:
        row = UserRole(id=_uid(), slug=slug, name=f"{MARKER} {slug}")
        db.add(row)
        db.flush()
    return row.id


def _assign(db, user_id: str, slug: str) -> None:
    db.add(UserRoleAssignment(user_id=user_id, role_id=_role(db, slug)))
    db.flush()


@pytest.fixture
def api(db, monkeypatch):
    """(client, allow, caller). `allow` is the caller's whole permission set; no admin role."""
    from app.services.user_service import UserPermissionService

    caller_id = _uid()
    db.add(
        User(
            id=caller_id,
            email=f"{caller_id}@zzt.test",
            name=f"{MARKER} Salesperson",
            status=UserStatus.ACTIVE.value,
        )
    )
    db.flush()
    _assign(db, caller_id, "salesperson")
    caller = {"id": caller_id, "email": f"{caller_id}@zzt.test"}
    allow: set[str] = set()

    def _override_db():
        yield db

    def _override_scope(_db=Depends(get_db)):
        set_company_scope(_db, None)
        return None

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: dict(caller)
    app.dependency_overrides[get_current_user_or_api_key] = lambda: dict(caller)
    app.dependency_overrides[apply_company_scope] = _override_scope
    monkeypatch.setattr(
        UserPermissionService, "check_user_has_permission", lambda self, uid, slug: slug in allow
    )
    monkeypatch.setattr(
        UserPermissionService, "get_user_permission_slugs", lambda self, uid: sorted(allow)
    )
    monkeypatch.setattr(UserPermissionService, "get_user_role_slugs", lambda self, uid: set())
    try:
        yield TestClient(app), allow, caller
    finally:
        for dep in (get_db, get_current_user, get_current_user_or_api_key, apply_company_scope):
            app.dependency_overrides.pop(dep, None)


def _user(
    db,
    name: str,
    *,
    status=UserStatus.ACTIVE,
    trashed: bool = False,
    email=None,
    role: str | None = "salesperson",
    integration: bool = False,
) -> str:
    user_id = _uid()
    db.add(
        User(
            id=user_id,
            email=email or f"{user_id}@zzt.test",
            name=name,
            status=getattr(status, "value", status),
            is_trashed=trashed,
            is_integration=integration,
        )
    )
    db.flush()
    if role:
        _assign(db, user_id, role)
    return user_id


# ----------------------------------------------------------------- people picker (UAC1)


def test_people_lookup_answers_a_caller_with_no_slugs_with_id_and_name_only(api, db):
    client, _allow, _caller = api
    tag = unique_code("PPL", alpha=True)
    active = _user(db, f"{MARKER} {tag} Aida")

    response = client.get(f"{BASE}/user-management/users/lookup", params={"query": tag})

    assert response.status_code == 200, response.text
    rows = response.json()
    assert rows == [{"id": active, "name": f"{MARKER} {tag} Aida"}]


def test_people_lookup_lists_only_active_untrashed_users(api, db):
    client, *_ = api
    tag = unique_code("ACT", alpha=True)
    _user(db, f"{MARKER} {tag} Active")
    _user(db, f"{MARKER} {tag} Inactive", status=UserStatus.INACTIVE)
    _user(db, f"{MARKER} {tag} Blocked", status=UserStatus.BLOCKED)
    _user(db, f"{MARKER} {tag} Trashed", trashed=True)

    response = client.get(f"{BASE}/user-management/users/lookup", params={"query": tag})

    assert response.status_code == 200, response.text
    assert [row["name"] for row in response.json()] == [f"{MARKER} {tag} Active"]


def test_people_lookup_filters_over_past_records_can_include_inactive_people(api, db):
    """A filter over historical rows (SLA trackers, complaints, order inquiries) must still offer
    someone who has since left; an assignment picker must not. Trashed stays out, as on
    `/users/select`'s default."""
    client, *_ = api
    tag = unique_code("INA", alpha=True)
    _user(db, f"{MARKER} {tag} Active")
    _user(db, f"{MARKER} {tag} Inactive", status=UserStatus.INACTIVE)
    _user(db, f"{MARKER} {tag} Trashed", trashed=True)

    response = client.get(
        f"{BASE}/user-management/users/lookup",
        params={"query": tag, "include_inactive": "true"},
    )

    assert response.status_code == 200, response.text
    assert sorted(row["name"] for row in response.json()) == [
        f"{MARKER} {tag} Active",
        f"{MARKER} {tag} Inactive",
    ]
    assert all(set(row) == {"id", "name"} for row in response.json())


def test_people_lookup_lists_staff_only(api, db):
    """Portal contacts (no role, or only `portal_user` / `guest`) and integration act-as
    accounts are not people a salesperson assigns work to."""
    client, *_ = api
    tag = unique_code("STF", alpha=True)
    _user(db, f"{MARKER} {tag} Staff")
    _user(db, f"{MARKER} {tag} Roleless", role=None)
    _user(db, f"{MARKER} {tag} Portal", role="portal_user")
    _user(db, f"{MARKER} {tag} Guest", role="guest")
    _user(db, f"{MARKER} {tag} Robot", integration=True)

    response = client.get(f"{BASE}/user-management/users/lookup", params={"query": tag})

    assert response.status_code == 200, response.text
    assert [row["name"] for row in response.json()] == [f"{MARKER} {tag} Staff"]


@pytest.mark.parametrize("caller_role", [None, "portal_user", "guest"])
def test_people_lookup_refuses_a_portal_caller(api, db, caller_role):
    """A dealer signed in through the portal holds a session too; it must not list staff."""
    client, _allow, caller = api
    db.query(UserRoleAssignment).filter(UserRoleAssignment.user_id == caller["id"]).delete()
    if caller_role:
        _assign(db, caller["id"], caller_role)
    db.flush()

    response = client.get(f"{BASE}/user-management/users/lookup")

    assert response.status_code == 403, response.text
    assert response.json()["code"] == "people_lookup_staff_only"


def test_people_lookup_query_does_not_match_email(api, db):
    """A salesperson may find a colleague by name; the lookup must not become an email probe."""
    client, *_ = api
    secret = unique_code("mailonly", alpha=True).lower()
    _user(db, f"{MARKER} Plain Name", email=f"{secret}@zzt.test")

    response = client.get(f"{BASE}/user-management/users/lookup", params={"query": secret})

    assert response.status_code == 200, response.text
    assert response.json() == []


def test_people_lookup_respond_ids_are_opt_in_and_only_for_synced_users(api, db):
    """The SLA and complaint assignee filters key on the Respond.io agent id, so they ask for it
    explicitly; the plain picker never carries it, and the opt-in lists synced users only."""
    client, *_ = api
    tag = unique_code("RSP", alpha=True)
    synced = _user(db, f"{MARKER} {tag} Synced")
    unsynced = _user(db, f"{MARKER} {tag} Unsynced")
    row = db.get(User, synced)
    row.respond_synced = "successful"
    row.respond_user_id = "4242"
    db.flush()

    plain = client.get(f"{BASE}/user-management/users/lookup", params={"query": tag})
    assert plain.status_code == 200, plain.text
    assert {r["id"] for r in plain.json()} == {synced, unsynced}
    assert all(set(r) == {"id", "name"} for r in plain.json())

    respond = client.get(
        f"{BASE}/user-management/users/lookup",
        params={"query": tag, "respond_synced": "true"},
    )
    assert respond.status_code == 200, respond.text
    assert respond.json() == [
        {"id": synced, "name": f"{MARKER} {tag} Synced", "respond_user_id": "4242"}
    ]


def test_people_lookup_without_a_session_is_401(db):
    def _override_db():
        yield db

    app.dependency_overrides[get_db] = _override_db
    try:
        response = TestClient(app).get(f"{BASE}/user-management/users/lookup")
    finally:
        app.dependency_overrides.pop(get_db, None)
    assert response.status_code == 401, response.text


def test_users_select_stays_on_the_admin_slug(api):
    client, *_ = api
    response = client.get(f"{BASE}/user-management/users/select")
    assert response.status_code == 403, response.text
    assert "user_management.users.view" in response.json()["detail"]


# ------------------------------------------------------------------ status flow (UAC2)


@pytest.fixture
def seeded_graphs(db):
    from sqlalchemy import text

    company_id = str(db.execute(text("select id from companies where code = 'SRT'")).scalar())
    project_seed_service.run(db, company_id=company_id)
    db.flush()


@pytest.mark.parametrize("entity_type", ["project", "project_task", "project_lead"])
def test_project_status_graphs_open_on_project_view(api, seeded_graphs, entity_type):
    client, allow, _ = api
    allow.add(PROJECTS_VIEW)

    response = client.get(f"{BASE}/project-sales/status-graph/{entity_type}")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["entity_type"] == entity_type
    assert body["statuses"], f"the {entity_type} graph came back with no statuses"
    assert body["is_fork"] is False
    assert all(row.get("record_count") is None for row in body["statuses"]), (
        "live record counts are an admin read; the salesperson route never computes them"
    )


@pytest.mark.parametrize("entity_type", ["project", "project_task", "project_lead"])
def test_project_status_graphs_refuse_without_project_view(api, entity_type):
    client, *_ = api
    response = client.get(f"{BASE}/project-sales/status-graph/{entity_type}")
    assert response.status_code == 403, response.text
    assert response.json()["detail"] == f"Permission required: {PROJECTS_VIEW}"


@pytest.mark.parametrize("scope_id", ["unknown-uuid", "not-a-uuid"])
def test_unknown_scope_falls_back_to_the_default_graph(api, seeded_graphs, scope_id):
    """A scope with no fork resolves the default; a malformed one too, never a 500 from the
    uuid cast."""
    client, allow, _ = api
    allow.add(PROJECTS_VIEW)
    scope = _uid() if scope_id == "unknown-uuid" else scope_id

    response = client.get(f"{BASE}/project-sales/status-graph/project", params={"scope_id": scope})

    assert response.status_code == 200, response.text
    assert response.json()["is_fork"] is False
    assert response.json()["statuses"]


@pytest.mark.parametrize(
    "entity_type", ["quotation", "sales_order", "inbound_shipment", "nonsense"]
)
def test_status_graph_route_serves_only_the_project_sales_graphs(api, entity_type):
    client, allow, _ = api
    allow.add(PROJECTS_VIEW)
    response = client.get(f"{BASE}/project-sales/status-graph/{entity_type}")
    assert response.status_code == 404, response.text


def test_admin_status_graph_route_stays_admin(api):
    client, allow, _ = api
    allow.add(PROJECTS_VIEW)
    response = client.get(f"{BASE}/system/statuses/graph/project")
    assert response.status_code == 403, response.text
    assert "system.statuses.view" in response.json()["detail"]


# ------------------------------------- contact access types and market segments (UAC3)


def test_contact_access_types_read_opens_to_any_signed_in_user(api, db):
    client, *_ = api
    code = unique_code("cat", alpha=True).lower()
    db.add(ContactAccessType(code=code, name=f"{MARKER} Dealer", is_active=True))
    db.flush()

    response = client.get(f"{BASE}/user-management/contact-access-types/")

    assert response.status_code == 200, response.text
    assert code in {row["code"] for row in response.json()}


def test_market_segments_read_opens_to_any_signed_in_user(api, db):
    client, *_ = api
    code = unique_code("seg", alpha=True).lower()
    db.add(MarketSegment(code=code, name=f"{MARKER} Retail", is_active=True))
    db.flush()

    response = client.get(
        f"{BASE}/user-management/market-segments/", params={"active_only": "true"}
    )

    assert response.status_code == 200, response.text
    assert code in {row["code"] for row in response.json()}


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("get", "/user-management/contact-access-types/all", None),
        ("get", "/user-management/contact-access-types/dealer", None),
        ("post", "/user-management/contact-access-types/", {"code": "zzt_x", "name": "X"}),
        ("put", "/user-management/contact-access-types/dealer", {"name": "X"}),
        ("delete", "/user-management/contact-access-types/dealer", None),
        ("post", "/user-management/market-segments/", {"code": "zzt_x", "name": "X"}),
        ("put", "/user-management/market-segments/retail", {"name": "X"}),
        ("delete", "/user-management/market-segments/retail", None),
    ],
)
def test_catalog_admin_reads_and_writes_stay_gated(api, method, path, body):
    client, *_ = api
    kwargs = {"json": body} if body is not None else {}
    response = getattr(client, method)(f"{BASE}{path}", **kwargs)
    assert response.status_code == 403, f"{method.upper()} {path}: {response.text}"


# ---------------------------------------------- units, brands, categories, countries (UAC4)


def _seed_master_data(db) -> dict:
    tag = unique_code("", alpha=True)
    uom = UnitOfMeasure(id=_uid(), uom_code=f"ZZ{tag[:6]}", uom_name=f"{MARKER} Piece")
    brand = Brand(
        id=_uid(),
        brand_code=f"ZZT{tag[:8]}",
        brand_name=f"{MARKER} Brand",
        chatbot_weight=7,
        access_levels=["dealer"],
    )
    category = ProductCategory(
        id=_uid(),
        category_code=f"ZZT{tag[:8]}",
        category_name=f"{MARKER} Category",
        chatbot_max_qty=5,
    )
    country = Country(id=_uid(), code=tag[:2].upper(), name=f"{MARKER} Land {tag}")
    db.add_all([uom, brand, category, country])
    db.flush()
    return {"uom": uom, "brand": brand, "category": category, "country": country}


# What each select returns, and nothing more (UAC4.2). Kept in step with the frontend
# consumers; the plan lists which screen reads which field.
SELECT_FIELDS = {
    "units-of-measure": {"id", "uom_code", "uom_name", "decimal_places"},
    "brands": {"id", "brand_code", "brand_name", "is_active"},
    "product-categories": {"id", "category_code", "category_name", "is_active"},
    "countries": {"id", "code", "name"},
}
SEARCH = {
    "units-of-measure": lambda s: s["uom"].uom_code,
    "brands": lambda s: s["brand"].brand_code,
    "product-categories": lambda s: s["category"].category_code,
    "countries": lambda s: s["country"].name,
}


@pytest.mark.parametrize("resource", sorted(SELECT_FIELDS))
def test_master_data_select_opens_with_select_fields_only(api, db, resource):
    client, *_ = api
    seeded = _seed_master_data(db)

    response = client.get(
        f"{BASE}/master-data/{resource}/select", params={"query": SEARCH[resource](seeded)}
    )

    assert response.status_code == 200, response.text
    rows = response.json()
    assert rows, f"{resource}/select returned nothing for the seeded row"
    for row in rows:
        assert set(row) == SELECT_FIELDS[resource], (
            f"{resource}/select leaks or drops fields: {sorted(set(row) ^ SELECT_FIELDS[resource])}"
        )


@pytest.mark.parametrize("resource", sorted(SELECT_FIELDS))
def test_master_data_select_still_needs_the_slug_for_an_api_key(api, monkeypatch, resource):
    """Integrations act with their act-as user's grants (integration_auth.py). Only signed-in
    sessions get the open read; a key whose act-as user lacks the `.view` slug is refused."""
    client, allow, caller = api
    api_caller = {**caller, "auth_method": "integration_api_key"}
    app.dependency_overrides[get_current_user_or_api_key] = lambda: dict(api_caller)

    denied = client.get(f"{BASE}/master-data/{resource}/select")
    assert denied.status_code == 403, denied.text

    slug = {
        "units-of-measure": "master_data.units_of_measure.view",
        "brands": "master_data.brands.view",
        "product-categories": "master_data.product_categories.view",
        "countries": "master_data.countries.view",
    }[resource]
    allow.add(slug)
    assert client.get(f"{BASE}/master-data/{resource}/select").status_code == 200


@pytest.mark.parametrize(
    "path",
    [
        "/master-data/units-of-measure",
        "/master-data/brands",
        "/master-data/product-categories",
        "/master-data/countries",
    ],
)
def test_master_data_admin_lists_stay_gated(api, path):
    client, *_ = api
    response = client.get(f"{BASE}{path}")
    assert response.status_code == 403, f"{path}: {response.text}"


@pytest.mark.parametrize(
    "path,body",
    [
        ("/master-data/units-of-measure", {"uom_code": "ZZX", "uom_name": "X"}),
        ("/master-data/brands", {"brand_code": "ZZX", "brand_name": "X"}),
        ("/master-data/product-categories", {"category_code": "ZZX", "category_name": "X"}),
        ("/master-data/countries", {"code": "ZX", "name": "X"}),
    ],
)
def test_master_data_writes_stay_gated(api, path, body):
    client, *_ = api
    response = client.post(f"{BASE}{path}", json=body)
    assert response.status_code == 403, f"POST {path}: {response.text}"


# ------------------------------------------------------------ roles stay locked (UAC5)


def test_roles_select_stays_locked(api):
    client, *_ = api
    response = client.get(f"{BASE}/user-management/roles/select")
    assert response.status_code == 403, response.text
    assert "user_management.roles.view" in response.json()["detail"]
