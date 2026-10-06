"""List and detail GETs in marketing, complaints, SLA, forms and resources carry a permission.

Crew lane LIST-API-PERMS (`documentation/plans/security/PLAN-list-api-perms.md`). Every route
below used to depend only on `get_current_user` / `get_current_user_or_api_key`, so any signed-in
account - a retail salesperson holding nothing but `ideation.board.view` - could read every
promotion, every complaint (customer names, phone numbers, free text), every SLA record, every
form and the attachment catalogue. Each one now requires the module's EXISTING view slug.

Three shapes, from `app/dependencies.py`:

- P  `require_permission_with_api_key(slug)`: a session or an API key, the slug checked against
     the resolved principal (an API key's act-as user).
- S  `require_permission(slug)`: session only.
- L  `require_session_or_api_key_permission(slug)`: a lookup (types, folders). Any signed-in
     session reads it (owner ruling 1 Oct 2026, never-stuck L10); an API key still needs the slug.

Real app, real `UserPermissionService`, roles seeded on a blank schema. The API-key cases run
through the real `get_current_user_or_api_key` and `resolve_integration_principal` with a real
`integration_api_keys` row - no dependency override on the principal - so they prove the act-as
user's grants are what the gate reads.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
import app.database as app_database
from app.dependencies import get_current_user, get_current_user_or_api_key
from app.models.integration import Integration
from app.models.user import (
    User,
    UserPermission,
    UserRole,
    UserRoleAssignment,
    UserRolePermission,
)
from app.services.integration_key_service import IntegrationKeyService
from tests._pg_fixture import blank_session

P, S, L = "P", "S", "L"
# Fixed, so xdist workers collect identical ids; it matches no row on a blank schema.
RID = "00000000-0000-4000-8000-00000000c0de"

PROMOTIONS = "marketing.promotions.view"
PROMOTION_PRODUCTS = "marketing.promotion_products.view"
PROMOTION_ATTACHMENTS = "marketing.promotion_attachments.view"
PROMOTION_TYPES = "marketing.promotion_types.view"
CAMPAIGNS = "marketing.campaigns.view"
COMPLAINTS = "complaint_management.complaints.view"
SLA_POLICIES = "sla_management.sla_policies.view"
SLA_TRACKING = "sla_management.conversation_sla_tracking.view"
SLA_EVENT_LOGS = "sla_management.escalation_logs.view"
FORMS = "forms.forms.view"
ATTACHMENTS = "resource.attachments.view"
DIRECTORIES = "resource.attachment_directories.view"
ATTACHMENT_TYPES = "resource.attachment_types.view"

SETTINGS = "user_management.settings.view"
# Other modules' screens that read a route as their own feature (the plan's any-of gates).
DEALER_KIT = "dealer_kit.page.view"
PRODUCTS = "master_data.products.view"
STOCK_INQUIRIES = "procurement.stock_inquiries.view"
PACKING_LISTS = "procurement.packing_lists.view"
TICKETS = "tickets.tickets.view"
UPLOAD = "resource.attachments.upload"
STOCK = "inventory.stock.view"

TRACKING = "/api/v1/sla-management/conversation-sla-tracking"
ATT = "/api/v1/resource-management/attachments"

# (path, slug, shape, kind) - `slug` is the module's own view slug for the route (the one the
# 403 body must name). kind "list" must answer 200 on an empty schema; "detail" reads one record
# by a random id, so 404 is the proof that the gate let the handler run.
ROUTES = [
    # marketing
    ("/api/v1/marketing/promotions/", PROMOTIONS, P, "list"),
    (f"/api/v1/marketing/promotions/{RID}", PROMOTIONS, P, "detail"),
    (f"/api/v1/marketing/promotions/{RID}/products", PROMOTIONS, P, "detail"),
    ("/api/v1/marketing/promotion-products/", PROMOTION_PRODUCTS, P, "list"),
    ("/api/v1/marketing/promotion-attachments/", PROMOTION_ATTACHMENTS, P, "list"),
    (f"/api/v1/marketing/promotion-attachments/{RID}", PROMOTION_ATTACHMENTS, P, "detail"),
    (f"/api/v1/marketing/promotion-attachments/promotion/{RID}", PROMOTION_ATTACHMENTS, P, "detail"),
    ("/api/v1/marketing/campaigns/", CAMPAIGNS, P, "list"),
    (f"/api/v1/marketing/campaigns/{RID}", CAMPAIGNS, P, "detail"),
    ("/api/v1/marketing/promotion-types/", PROMOTION_TYPES, L, "list"),
    (f"/api/v1/marketing/promotion-types/{RID}", PROMOTION_TYPES, L, "detail"),
    ("/api/v1/marketing/campaign-types/", CAMPAIGNS, L, "list"),
    (f"/api/v1/marketing/campaign-types/{RID}", CAMPAIGNS, L, "detail"),
    # complaints
    ("/api/v1/complaints-management/complaints/", COMPLAINTS, P, "list"),
    ("/api/v1/complaints-management/complaints/analytics", COMPLAINTS, P, "list"),
    (f"/api/v1/complaints-management/complaints/{RID}", COMPLAINTS, P, "detail"),
    (f"/api/v1/complaints-management/complaints/{RID}/fulfilment-orders", COMPLAINTS, P, "detail"),
    (f"/api/v1/complaints-management/complaints/{RID}/conversation", COMPLAINTS, S, "detail"),
    # sla
    ("/api/v1/sla-management/sla-policies/", SLA_POLICIES, L, "list"),
    (f"/api/v1/sla-management/sla-policies/{RID}", SLA_POLICIES, P, "detail"),
    (f"/api/v1/sla-management/sla-policies/{RID}/tiers", SLA_POLICIES, P, "detail"),
    (f"{TRACKING}/dashboard", SLA_TRACKING, P, "list"),
    (f"{TRACKING}/", SLA_TRACKING, P, "list"),
    (f"{TRACKING}/by-source?source_entity_type=complaint&source_entity_id={RID}", SLA_TRACKING, P, "list"),
    (f"{TRACKING}/event-logs", SLA_EVENT_LOGS, P, "list"),
    (f"{TRACKING}/event-logs?tracking_id={RID}", SLA_EVENT_LOGS, P, "list"),
    (f"{TRACKING}/{RID}", SLA_TRACKING, P, "detail"),
    (f"{TRACKING}/{RID}/conversation", SLA_TRACKING, P, "detail"),
    # forms
    ("/api/v1/forms-management/forms/", FORMS, P, "list"),
    (f"/api/v1/forms-management/forms/{RID}", FORMS, P, "detail"),
    ("/api/v1/forms-management/revision-configs", SETTINGS, P, "list"),
    # resources
    (f"{ATT}/", ATTACHMENTS, P, "list"),
    (f"{ATT}/drive", ATTACHMENTS, P, "list"),
    (f"{ATT}/collision-check?filename=zz.pdf", ATTACHMENTS, P, "list"),
    (f"{ATT}/current-stock-list", ATTACHMENTS, P, "detail"),
    ("/api/v1/resource-management/directories/", DIRECTORIES, P, "list"),
    ("/api/v1/resource-management/directories/tree", DIRECTORIES, L, "list"),
    (f"/api/v1/resource-management/directories/{RID}", DIRECTORIES, P, "detail"),
    ("/api/v1/resource-management/attachment-types/", ATTACHMENT_TYPES, L, "list"),
    (f"/api/v1/resource-management/attachment-types/{RID}", ATTACHMENT_TYPES, P, "detail"),
]
IDS = [f"{shape}:{path.removeprefix('/api/v1/')}" for path, _, shape, _ in ROUTES]

# Routes another module's screen reads as its own feature: (path, that screen's slug, kind).
# Each admits with that slug alone (the plan's any-of gates, `*_READERS` in the routers).
CROSS_MODULE_READERS = [
    ("/api/v1/marketing/promotions/", DEALER_KIT, "list"),
    ("/api/v1/marketing/promotion-products/", PRODUCTS, "list"),
    (f"/api/v1/marketing/promotion-attachments/promotion/{RID}", PROMOTIONS, "detail"),
    (f"{TRACKING}/by-source?source_entity_type=complaint&source_entity_id={RID}", COMPLAINTS, "list"),
    (f"{TRACKING}/event-logs?tracking_id={RID}", COMPLAINTS, "list"),
    (f"{TRACKING}/{RID}", STOCK_INQUIRIES, "detail"),
    (f"{TRACKING}/{RID}/conversation", TICKETS, "detail"),
    (f"{ATT}/", COMPLAINTS, "list"),
    (f"{ATT}/", PACKING_LISTS, "list"),
    (f"{ATT}/collision-check?filename=zz.pdf", UPLOAD, "list"),
    (f"{ATT}/current-stock-list", STOCK, "detail"),
]
CROSS_IDS = [f"{path.removeprefix('/api/v1/')}<-{slug}" for path, slug, _ in CROSS_MODULE_READERS]

# A per-tracker slug does NOT open the cross-tracker lists: (path, slug that must be refused).
NOT_ENOUGH = [
    (f"{TRACKING}/", COMPLAINTS),
    (f"{TRACKING}/dashboard", COMPLAINTS),
    (f"{TRACKING}/event-logs", COMPLAINTS),
    (f"{TRACKING}/event-logs", SLA_TRACKING),
    (f"{ATT}/drive", COMPLAINTS),
    ("/api/v1/marketing/promotions/", PRODUCTS),
]
NOT_ENOUGH_IDS = [f"{path.removeprefix('/api/v1/')}<-{slug}" for path, slug in NOT_ENOUGH]

# The bystander: a retail salesperson, as in the lane brief.
BYSTANDER_SLUG = "ideation.board.view"


def _permission(db, slug: str) -> UserPermission:
    perm = db.query(UserPermission).filter_by(slug=slug).one_or_none()
    if perm is None:
        perm = UserPermission(id=str(uuid.uuid4()), slug=slug, name=slug, description="")
        db.add(perm)
        db.flush()
    return perm


def _user(db, label: str, role_slug: str | None, permission_slugs=()) -> dict:
    """A user holding EXACTLY `permission_slugs` through one role (never superadmin unless named)."""
    uid = str(uuid.uuid4())
    db.add(User(id=uid, email=f"zz-lap-{label}-{uid[:8]}@example.com", name=label, status="ACTIVE"))
    db.flush()
    if role_slug is not None:
        role = UserRole(
            id=str(uuid.uuid4()), slug=f"{role_slug}-{uid[:8]}" if role_slug.startswith("zz") else role_slug,
            name=role_slug, description="", is_protected=False, is_default=False,
        )
        db.add(role)
        db.flush()
        for slug in permission_slugs:
            db.add(UserRolePermission(
                id=str(uuid.uuid4()), role_id=role.id, permission_id=_permission(db, slug).id,
            ))
        db.add(UserRoleAssignment(user_id=uid, role_id=role.id))
    db.flush()
    return {"id": uid, "email": f"zz-lap-{label}-{uid[:8]}@example.com", "name": label}


def _api_key(db, label: str, permission_slugs=()) -> str:
    """A real integration key whose act-as user holds exactly `permission_slugs`."""
    principal = _user(db, f"int-{label}", "zz_integration", permission_slugs)
    db.query(User).filter(User.id == principal["id"]).update({"is_integration": True})
    integration = Integration(
        name=f"zz-lap-{label}-{uuid.uuid4().hex[:8]}", type="mcp", act_as_user_id=principal["id"],
    )
    db.add(integration)
    db.flush()
    return IntegrationKeyService(db).issue_key(integration)


class _Env:
    def __init__(self, db):
        self.db = db
        self.caller: dict | None = None

    def session(self, user: dict) -> TestClient:
        """A signed-in session: the principal dependencies answer `user`."""
        self.caller = user
        app.dependency_overrides[get_current_user] = lambda: self.caller
        app.dependency_overrides[get_current_user_or_api_key] = lambda: self.caller
        return TestClient(app)

    def key(self) -> TestClient:
        """No principal override: the real key resolver runs against the blank schema."""
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_current_user_or_api_key, None)
        return TestClient(app)


@pytest.fixture
def env():
    with blank_session() as db:
        e = _Env(db)

        def _override_db():
            yield db

        app.dependency_overrides[app_database.get_db] = _override_db
        try:
            yield e
        finally:
            app.dependency_overrides.clear()


def _assert_let_through(r, kind: str, path: str) -> None:
    assert r.status_code != 403, f"{path} refused: {r.text}"
    assert r.status_code != 401, f"{path} unauthenticated: {r.text}"
    if kind == "list":
        assert r.status_code == 200, f"{path}: {r.status_code} {r.text}"
    else:
        # A random id: 404 (or 200 for a route that returns an empty list) proves the handler
        # ran, which is what the gate decides. 5xx is a defect in the route, not the gate.
        assert r.status_code in (200, 404), f"{path}: {r.status_code} {r.text}"


# --- UAC1: a session without the slug ----------------------------------------------------------

@pytest.mark.parametrize("path,slug,shape,kind", ROUTES, ids=IDS)
def test_session_without_the_slug(env, path, slug, shape, kind):
    bystander = _user(env.db, "bystander", "zz_retail", [BYSTANDER_SLUG])
    env.db.commit()
    r = env.session(bystander).get(path)
    if shape == L:
        # UAC3.1: a lookup stays open to every signed-in session.
        _assert_let_through(r, kind, path)
    else:
        # UAC1.1: refused, and the body names the slug the caller is missing.
        assert r.status_code == 403, f"{path}: {r.status_code} {r.text}"
        assert slug in r.text, r.text


# --- UAC1.2 / 1.3: a session with the slug, and the admin bypass ---------------------------------

@pytest.mark.parametrize("path,slug,shape,kind", ROUTES, ids=IDS)
def test_session_with_exactly_the_slug(env, path, slug, shape, kind):
    holder = _user(env.db, "holder", "zz_holder", [slug])
    env.db.commit()
    _assert_let_through(env.session(holder).get(path), kind, path)


_ADMIN_SAMPLE = [r for r in ROUTES if r[0] in (
    "/api/v1/marketing/promotions/",
    "/api/v1/complaints-management/complaints/",
    f"{TRACKING}/event-logs",
    f"{ATT}/",
)]


@pytest.mark.parametrize("path,slug,shape,kind", _ADMIN_SAMPLE, ids=[IDS[ROUTES.index(r)] for r in _ADMIN_SAMPLE])
def test_admin_bypass_still_holds(env, path, slug, shape, kind):
    admin = _user(env.db, "admin", "admin")
    env.db.commit()
    _assert_let_through(env.session(admin).get(path), kind, path)


# --- the any-of gates: another module's screen reads the route with its own slug ---------------

@pytest.mark.parametrize("path,slug,kind", CROSS_MODULE_READERS, ids=CROSS_IDS)
def test_cross_module_reader_is_admitted_with_its_own_slug(env, path, slug, kind):
    reader = _user(env.db, "reader", "zz_reader", [slug])
    env.db.commit()
    _assert_let_through(env.session(reader).get(path), kind, path)


@pytest.mark.parametrize("path,slug", NOT_ENOUGH, ids=NOT_ENOUGH_IDS)
def test_a_per_record_slug_does_not_open_the_cross_record_list(env, path, slug):
    reader = _user(env.db, "narrow", "zz_narrow", [slug])
    env.db.commit()
    r = env.session(reader).get(path)
    assert r.status_code == 403, f"{path}: {r.status_code} {r.text}"


# --- UAC2: the API-key path reads the act-as user's grants ---------------------------------------

@pytest.mark.parametrize("path,slug,shape,kind", [r for r in ROUTES if r[2] != S], ids=[i for i, r in zip(IDS, ROUTES) if r[2] != S])
def test_api_key_whose_act_as_user_holds_the_slug(env, path, slug, shape, kind):
    key = _api_key(env.db, "granted", [slug])
    env.db.commit()
    _assert_let_through(env.key().get(path, headers={"X-API-Key": key}), kind, path)


@pytest.mark.parametrize("path,slug,shape,kind", [r for r in ROUTES if r[2] != S], ids=[i for i, r in zip(IDS, ROUTES) if r[2] != S])
def test_api_key_whose_act_as_user_lacks_the_slug(env, path, slug, shape, kind):
    # UAC2.2 and UAC3.2: a key is not a bypass, on the lookups included.
    key = _api_key(env.db, "bare", [BYSTANDER_SLUG])
    env.db.commit()
    r = env.key().get(path, headers={"X-API-Key": key})
    assert r.status_code == 403, f"{path}: {r.status_code} {r.text}"
    assert slug in r.text, r.text


# --- UAC4.1 (REG): a session-only route opens no key door -----------------------------------------

@pytest.mark.parametrize("path,slug,shape,kind", [r for r in ROUTES if r[2] == S], ids=[i for i, r in zip(IDS, ROUTES) if r[2] == S])
def test_session_only_route_refuses_an_api_key(env, path, slug, shape, kind):
    key = _api_key(env.db, "sessiononly", [slug])
    env.db.commit()
    r = env.key().get(path, headers={"X-API-Key": key})
    assert r.status_code == 401, f"{path}: {r.status_code} {r.text}"
