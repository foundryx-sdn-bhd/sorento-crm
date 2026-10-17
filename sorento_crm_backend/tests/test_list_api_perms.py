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
from app.models.sla import ConversationSLATracking, SLAPolicy
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
# Fixed, so xdist workers collect identical ids. RID matches no row on a blank schema; the two
# tracker ids are seeded by `env` (a conversation ticket with an assignee, and a complaint's
# form-SLA tracker) so the typed per-tracker gates can be read against real rows.
RID = "00000000-0000-4000-8000-00000000c0de"
TRACK_CONV = "00000000-0000-4000-8000-0000000000c1"
TRACK_FORM = "00000000-0000-4000-8000-0000000000f1"

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
SAMPLE_REQUESTS = "projects.sample_requests.view"
KPI = "sla.kpi.view"
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
    (f"/api/v1/complaints-management/complaints/{RID}/conversation/window-state", COMPLAINTS, S, "detail"),
    (f"/api/v1/complaints-management/complaints/{RID}/conversation/chat-template", COMPLAINTS, S, "detail"),
    # sla
    ("/api/v1/sla-management/sla-policies/", SLA_POLICIES, L, "list"),
    (f"/api/v1/sla-management/sla-policies/{RID}", SLA_POLICIES, P, "detail"),
    (f"/api/v1/sla-management/sla-policies/{RID}/tiers", SLA_POLICIES, P, "detail"),
    (f"{TRACKING}/dashboard", SLA_TRACKING, P, "list"),
    (f"{TRACKING}/", SLA_TRACKING, P, "list"),
    (f"{TRACKING}/by-source?source_entity_type=complaint&source_entity_id={RID}", SLA_TRACKING, P, "list"),
    (f"{TRACKING}/event-logs", SLA_EVENT_LOGS, P, "list"),
    (f"{TRACKING}/event-logs?tracking_id={TRACK_FORM}", SLA_EVENT_LOGS, P, "list"),
    (f"{TRACKING}/{TRACK_FORM}", SLA_TRACKING, P, "list"),
    (f"{TRACKING}/{TRACK_CONV}", SLA_TRACKING, P, "list"),
    (f"{TRACKING}/integration/due-escalations", SLA_TRACKING, P, "list"),
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
    (f"{TRACKING}/by-source?source_entity_type=stock_inquiry&source_entity_id={RID}", STOCK_INQUIRIES, "list"),
    # SR-CORE (#1718): the sample request detail page's SLA tab reads its own trackers.
    (f"{TRACKING}/by-source?source_entity_type=sample_request&source_entity_id={RID}", SAMPLE_REQUESTS, "list"),
    (f"{TRACKING}/event-logs?tracking_id={TRACK_FORM}", COMPLAINTS, "list"),
    (f"{TRACKING}/{TRACK_FORM}", COMPLAINTS, "list"),
    (f"{TRACKING}/{TRACK_FORM}", KPI, "list"),
    (f"{TRACKING}/{TRACK_CONV}", KPI, "list"),
    (f"{ATT}/", COMPLAINTS, "list"),
    (f"{ATT}/", PACKING_LISTS, "list"),
    (f"{ATT}/drive", DIRECTORIES, "list"),
    (f"{ATT}/collision-check?filename=zz.pdf", UPLOAD, "list"),
    (f"{ATT}/current-stock-list", STOCK, "detail"),
]
CROSS_IDS = [f"{path.removeprefix('/api/v1/')}<-{slug}" for path, slug, _ in CROSS_MODULE_READERS]

# A per-tracker slug does NOT open the cross-tracker lists, and a record's slug opens only
# trackers of THAT type: (path, slug that must be refused).
NOT_ENOUGH = [
    (f"{TRACKING}/", COMPLAINTS),
    (f"{TRACKING}/dashboard", COMPLAINTS),
    (f"{TRACKING}/event-logs", COMPLAINTS),
    (f"{TRACKING}/event-logs", SLA_TRACKING),
    (f"{TRACKING}/{TRACK_FORM}", STOCK_INQUIRIES),
    (f"{TRACKING}/{TRACK_CONV}", COMPLAINTS),
    (f"{TRACKING}/event-logs?tracking_id={TRACK_FORM}", TICKETS),
    (f"{TRACKING}/by-source?source_entity_type=complaint&source_entity_id={RID}", TICKETS),
    (f"{TRACKING}/by-source?source_entity_type=complaint&source_entity_id={RID}", SAMPLE_REQUESTS),
    (f"{TRACKING}/{TRACK_FORM}", SAMPLE_REQUESTS),
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


def _seed_trackers(db) -> dict:
    """A conversation ticket assigned to `assignee`, and a complaint's form-SLA tracker."""
    from datetime import datetime, timedelta

    assignee = _user(db, "assignee", None)
    policy = SLAPolicy(id=str(uuid.uuid4()), code=f"ZZT-{uuid.uuid4().hex[:6]}", name="evidence", is_active=True)
    db.add(policy)
    db.flush()
    due = datetime.utcnow() + timedelta(hours=4)
    db.add(ConversationSLATracking(
        id=TRACK_CONV, policy_id=policy.id, current_tier=1, due_at=due,
        assigned_to_id=assignee["id"], source_entity_type=None,
    ))
    db.add(ConversationSLATracking(
        id=TRACK_FORM, policy_id=policy.id, current_tier=1, due_at=due,
        assigned_to_id=assignee["id"], source_entity_type="complaint", source_entity_id=RID,
    ))
    db.flush()
    return assignee


class _Env:
    def __init__(self, db):
        self.db = db
        self.caller: dict | None = None
        self.assignee: dict | None = None

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
        e.assignee = _seed_trackers(db)
        db.commit()

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


# --- scoped per-record reads on a REAL conversation ticket: 404 to an outsider ---------------------

SCOPED = [f"{TRACKING}/{TRACK_CONV}/conversation", f"{TRACKING}/{TRACK_CONV}/takeover-state"]
SCOPED_IDS = [p.removeprefix("/api/v1/") for p in SCOPED]


@pytest.fixture
def no_respond(monkeypatch):
    # The thread read ends in a Respond.io HTTP call; the gate under test runs before it.
    from app.services.sla_service import ConversationSLATrackingService

    monkeypatch.setattr(
        ConversationSLATrackingService,
        "fetch_respond_conversation_for_tracking",
        lambda self, tracking_id, limit=50, cursor=None: {"items": [], "pagination": {}},
    )


@pytest.mark.parametrize("path", SCOPED, ids=SCOPED_IDS)
def test_a_scoped_ticket_read_answers_an_outsider_with_404(env, no_respond, path):
    # `can_user_act_on_tracking`: the row exists, the viewer is neither its assignee nor a
    # manager of one, and the module slugs do not change that. 404, never 403 (the id is
    # not confirmed to a stranger).
    outsider = _user(env.db, "outsider", "zz_outsider", [TICKETS, SLA_TRACKING, COMPLAINTS])
    env.db.commit()
    r = env.session(outsider).get(path)
    assert r.status_code == 404, f"{path}: {r.status_code} {r.text}"


@pytest.mark.parametrize("path", SCOPED, ids=SCOPED_IDS)
def test_a_scoped_ticket_read_admits_its_assignee_without_any_slug(env, no_respond, path):
    r = env.session(env.assignee).get(path)
    assert r.status_code == 200, f"{path}: {r.status_code} {r.text}"


@pytest.mark.parametrize("path", SCOPED, ids=SCOPED_IDS)
def test_a_scoped_ticket_read_is_session_only(env, path):
    key = _api_key(env.db, "scoped", [SLA_TRACKING])
    env.db.commit()
    r = env.key().get(path, headers={"X-API-Key": key})
    assert r.status_code == 401, f"{path}: {r.status_code} {r.text}"


def test_a_form_trackers_thread_is_read_with_the_records_slug(env, no_respond):
    # The form-SLA tracker has no assignee rule for reading (the sibling reads admit by the
    # record's slug); a complaints viewer reads its chat records, a bystander is refused.
    path = f"{TRACKING}/{TRACK_FORM}/conversation"
    reader = _user(env.db, "reader", "zz_reader", [COMPLAINTS])
    bystander = _user(env.db, "bystander", "zz_retail", [BYSTANDER_SLUG])
    env.db.commit()
    assert env.session(reader).get(path).status_code == 200
    r = env.session(bystander).get(path)
    assert r.status_code == 403, f"{path}: {r.status_code} {r.text}"
    assert COMPLAINTS in r.text


# --- the reader lists are the contract: every entry admits alone, and nothing else is in them ----

def _reader_lists():
    from app.api.v1.marketing import promotion_attachments, promotion_products, promotions
    from app.api.v1.resources import attachments
    from app.api.v1.sla import sla_tracking

    # getattr with a default, so a tree without the constants (the red run) fails these
    # tests instead of erroring the whole module at collection.
    def const(module, name):
        return getattr(module, name, [])

    def readers(source_type):
        fn = getattr(sla_tracking, "tracker_reader_slugs", None)
        return fn(source_type) if fn else []

    return {
        "/api/v1/marketing/promotions/": (const(promotions, "PROMOTION_LIST_READERS"), [PROMOTIONS, DEALER_KIT]),
        "/api/v1/marketing/promotion-products/": (
            const(promotion_products, "PROMOTION_PRODUCT_READERS"), [PROMOTION_PRODUCTS, PRODUCTS]),
        f"/api/v1/marketing/promotion-attachments/promotion/{RID}": (
            const(promotion_attachments, "PROMOTION_ATTACHMENT_READERS"), [PROMOTION_ATTACHMENTS, PROMOTIONS]),
        f"{ATT}/": (const(attachments, "ATTACHMENT_CATALOGUE_READERS"), [
            ATTACHMENTS, COMPLAINTS, STOCK_INQUIRIES, "procurement.purchase_requests.view",
            "procurement.sponsorship_forms.view", PACKING_LISTS, PRODUCTS, "projects.projects.view",
            PROMOTIONS, FORMS, DEALER_KIT,
        ]),
        f"{ATT}/collision-check?filename=zz.pdf": (const(attachments, "ATTACHMENT_UPLOAD_READERS"), [ATTACHMENTS, UPLOAD]),
        f"{ATT}/current-stock-list": (const(attachments, "STOCK_LIST_READERS"), [ATTACHMENTS, STOCK]),
        f"{ATT}/drive": (const(attachments, "DRIVE_READERS"), [ATTACHMENTS, DIRECTORIES]),
        f"{TRACKING}/{TRACK_CONV}": (readers(None), [SLA_TRACKING, SLA_EVENT_LOGS, KPI]),
        f"{TRACKING}/{TRACK_FORM}": (readers("complaint"), [SLA_TRACKING, SLA_EVENT_LOGS, KPI, COMPLAINTS]),
    }


def test_each_reader_list_holds_exactly_the_plans_slugs():
    for path, (actual, expected) in _reader_lists().items():
        assert list(actual) == expected, path


def test_source_type_readers_cover_every_form_sla_type():
    from app.api.v1.sla.sla_tracking import SOURCE_TYPE_READERS
    from app.services.form_sla_service import FORM_SLA_TYPES

    assert set(SOURCE_TYPE_READERS) == set(FORM_SLA_TYPES)
    # The whole map is the contract: a dropped or widened row is a broken SLA tab somewhere.
    assert SOURCE_TYPE_READERS == {
        "complaint": [COMPLAINTS],
        "stock_inquiry": [STOCK_INQUIRIES],
        "purchase_request": ["procurement.purchase_requests.view"],
        "sponsorship_form": ["procurement.sponsorship_forms.view"],
        "ticket": [TICKETS],
        "workflow_submission": ["workflow_forms.submissions.view"],
        "price_tag_request": ["dealer_kit.price_tag_requests.view"],
        "sample_request": [SAMPLE_REQUESTS],
    }


_EACH_ENTRY = [(path, slug) for path, (actual, _) in _reader_lists().items() for slug in actual]


@pytest.mark.parametrize("path,slug", _EACH_ENTRY, ids=[f"{p.removeprefix('/api/v1/')}<-{s}" for p, s in _EACH_ENTRY])
def test_every_reader_list_entry_admits_on_its_own(env, path, slug):
    reader = _user(env.db, "entry", "zz_entry", [slug])
    env.db.commit()
    r = env.session(reader).get(path)
    assert r.status_code in (200, 404), f"{path}: {r.status_code} {r.text}"
    # 404 is the handler's own answer only for a random id, or for the stock list, which a
    # blank schema has no attachment type for; a seeded tracker must answer 200.
    assert r.status_code != 404 or RID in path or path.endswith("current-stock-list"), (
        f"{path}: a seeded row answered 404: {r.text}"
    )


# --- the guard against the next bare GET -----------------------------------------------------------

GATED_PREFIXES = (
    "/api/v1/marketing/",
    "/api/v1/complaints-management/",
    "/api/v1/sla-management/",
    "/api/v1/forms-management/",
    "/api/v1/resource-management/",
)
_PERMISSION_FACTORIES = (
    "require_permission.",
    "require_permission_with_api_key.",
    "require_any_permission.",
    "require_any_permission_with_api_key.",
    "require_session_or_api_key_permission.",
)
# Every GET under the five routers that carries no permission dependency, with the scoping
# that stands in for one (the plan's "Left as they are"). A new bare GET fails this test until
# it is gated or listed here with its reason.
UNGATED_GET_ALLOWLIST = {
    # self-scoped in the service: keyed on the caller's own id or visible teams
    f"{TRACKING}/my-pending": "caller's own tasks",
    f"{TRACKING}/team-pending": "caller's visible teams",
    f"{TRACKING}/visible-users": "caller's visible teams",
    f"{TRACKING}/{{tracking_id}}/takeover-state": "can_user_act_on_tracking, else 404",
    f"{TRACKING}/{{tracking_id}}/ticket": "require_ticket_in_scope, else 404",
    f"{TRACKING}/{{tracking_id}}/comments": "TicketCommentService._tracking_in_scope, else 404",
    f"{TRACKING}/{{tracking_id}}/conversation": "require_ticket_in_scope, else 404",
    f"{TRACKING}/{{tracking_id}}/conversation/page": "fetch_conversation_thread_page viewer scope",
    f"{TRACKING}/{{tracking_id}}/conversation/search": "viewer scope, as the page",
    f"{TRACKING}/{{tracking_id}}/media": "require_ticket_in_scope, else 404",
    "/api/v1/sla-management/conversation-events/stream": "entitled_contacts only",
    # gated inline, not by a dependency, because the slug set follows the record's type
    # (`tracker_reader_slugs`); each is tested above
    f"{TRACKING}/event-logs": "_require_event_log_reader",
    f"{TRACKING}/by-source": "_require_tracker_reader on the named source type",
    f"{TRACKING}/{{tracking_id}}": "_require_tracker_reader on the row's source type, after the 404",
    # the shared chat panel routes on the SLA mount: the ticket drawer's composer reads them for
    # the assignee, who may hold no module slug; the ticket scope is a follow-up (complaints
    # mount is gated through `read_dependencies`)
    f"{TRACKING}/{{entity_id}}/conversation/window-state": "follow-up: ticket scope (shared chat panel)",
    f"{TRACKING}/{{entity_id}}/conversation/chat-template": "follow-up: ticket scope (shared chat panel)",
    "/api/v1/resource-management/upload-activity": "uploaded_by == caller",
    # per entity, read by that entity's own detail page; the entity-type slug is a follow-up
    "/api/v1/sla-management/form-sla-tracking": "follow-up: entity-type slug (security review 4)",
    "/api/v1/sla-management/form-actions/current": "follow-up: entity-type slug (security review 14)",
    "/api/v1/sla-management/form-actions/eligibility": "follow-up: entity-type slug (security review 14)",
    # per-attachment reads serve every module's linked files; the linked record's slug is a follow-up
    f"{ATT}/{{attachment_id}}": "follow-up: linked-record slug (security review 6)",
    f"{ATT}/{{attachment_id}}/download": "follow-up: linked-record slug (security review 6)",
    f"{ATT}/{{attachment_id}}/metadata": "follow-up: linked-record slug (security review 6)",
    f"{ATT}/{{attachment_id}}/preview-url": "follow-up: linked-record slug (security review 6)",
    # four booleans of configuration, no record data
    "/api/v1/forms-management/revision-configs/enabled": "config flags only",
}


def _has_permission_dependency(route) -> bool:
    def walk(dependant) -> bool:
        for dep in dependant.dependencies:
            call = getattr(dep, "call", None)
            qualname = getattr(call, "__qualname__", "") or ""
            if qualname.startswith(_PERMISSION_FACTORIES):
                return True
            if walk(dep):
                return True
        return False

    return walk(route.dependant)


def test_every_get_under_the_five_routers_is_gated_or_listed():
    from fastapi.routing import APIRoute

    bare = {}
    for route in app.routes:
        if not isinstance(route, APIRoute) or "GET" not in route.methods:
            continue
        if not route.path.startswith(GATED_PREFIXES):
            continue
        if _has_permission_dependency(route):
            continue
        bare[route.path] = route.endpoint.__name__
    unexpected = {p: n for p, n in bare.items() if p not in UNGATED_GET_ALLOWLIST}
    stale = sorted(set(UNGATED_GET_ALLOWLIST) - set(bare))
    assert not unexpected, f"GET routes with no permission dependency and no listed reason: {unexpected}"
    assert not stale, f"allowlist names routes that are gated or gone now: {stale}"


# --- UAC4.1 (REG): a session-only route opens no key door -----------------------------------------

@pytest.mark.parametrize("path,slug,shape,kind", [r for r in ROUTES if r[2] == S], ids=[i for i, r in zip(IDS, ROUTES) if r[2] == S])
def test_session_only_route_refuses_an_api_key(env, path, slug, shape, kind):
    key = _api_key(env.db, "sessiononly", [slug])
    env.db.commit()
    r = env.key().get(path, headers={"X-API-Key": key})
    assert r.status_code == 401, f"{path}: {r.status_code} {r.text}"
