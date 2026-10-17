"""The audit read API is superadmin/admin only (issue #1281, section 6.2).

`GET /api/v1/audit/logs/` and `GET /api/v1/audit/activity` depended only on
`get_current_user_or_api_key`: any staff login or API key could read every audited
change, its before/after values and the actor's IP address. The superadmin gate
lived only in the FE menu. Both now require the `superadmin` or `admin` role on the
resolved principal (a login, or an API key's act-as user).

One narrow exception keeps the detail pages' Audit Trail panels working: a read
filtered to ONE record (`entity_type` + `entity_id`) of a type with a detail-page
panel is allowed with that record's view permission (the packing list Timeline tab
included). Every other shape 403s. Both routes also keep to the request's company:
a row stamped for another company is not returned, on either path. A non-admin on
the per-record path with no active company (UNSET, or an empty set) is refused,
since the admin listing filter treats that state as "every company".

Real roles, permissions and assignments are seeded on a blank schema, so the check
runs through the real `UserPermissionService`, not a stub.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from fastapi.testclient import TestClient

from app.main import app
import app.database as app_database
from app.dependencies import get_current_user_or_api_key
from app.models.audit import AuditLog
from app.models.base import UNSET, set_company_scope
from app.services.company_scope_resolver import apply_company_scope
from app.models.user import (
    User,
    UserPermission,
    UserRole,
    UserRoleAssignment,
    UserRolePermission,
)
from tests._pg_fixture import blank_session

COMPLAINT_VIEW = "complaint_management.complaints.view"
PACKING_LIST_VIEW = "procurement.packing_lists.view"
SPEC_REGISTRY_VIEW = "master_data.spec_registry.view"
COMPANY_A = str(uuid.uuid4())
COMPANY_B = str(uuid.uuid4())


def _role(db, slug, permission_slugs=()):
    role = UserRole(
        id=str(uuid.uuid4()), slug=slug, name=slug, description="",
        is_protected=False, is_default=False,
    )
    db.add(role)
    db.flush()
    for p in permission_slugs:
        perm = UserPermission(id=str(uuid.uuid4()), slug=p, name=p, description="")
        db.add(perm)
        db.flush()
        db.add(UserRolePermission(id=str(uuid.uuid4()), role_id=role.id, permission_id=perm.id))
    return role


def _user(db, label, role=None):
    uid = str(uuid.uuid4())
    db.add(User(id=uid, email=f"zz-gate-{label}-{uid[:8]}@example.com", name=label, status="ACTIVE"))
    db.flush()
    if role is not None:
        db.add(UserRoleAssignment(user_id=uid, role_id=role.id))
    return uid


@pytest.fixture
def env():
    with blank_session() as db:
        ids = {
            "superadmin": _user(db, "superadmin", _role(db, "superadmin")),
            "admin": _user(db, "admin", _role(db, "admin")),
            "staff": _user(db, "staff", _role(db, "zz_complaint_staff", [COMPLAINT_VIEW])),
            "plain": _user(db, "plain"),
            "packing": _user(db, "packing", _role(db, "zz_packing_staff", [PACKING_LIST_VIEW])),
            "specreg": _user(db, "specreg", _role(db, "zz_specreg_staff", [SPEC_REGISTRY_VIEW])),
        }
        db.commit()
        # The seed users' own CREATE rows are not under test. audit_logs is append-only
        # (#1281 S0), so the wipe runs under the maintenance flag.
        db.execute(text("SET LOCAL sorento.audit_maintenance = 'on'"))
        db.query(AuditLog).delete()
        complaint_id = str(uuid.uuid4())
        db.add(AuditLog(
            id=str(uuid.uuid4()), entity_type="complaint", entity_id=complaint_id,
            action="UPDATE", old_values={"status": "pending"}, new_values={"status": "resolved"},
            ip_address="10.0.0.9",
        ))
        db.commit()

        caller = {"id": None, "scope": None}

        def _override_db():
            yield db

        async def _override_scope():
            # None = unscoped (every company); a frozenset = the request's active company.
            set_company_scope(db, caller["scope"])
            return caller["scope"]

        app.dependency_overrides[app_database.get_db] = _override_db
        app.dependency_overrides[apply_company_scope] = _override_scope
        app.dependency_overrides[get_current_user_or_api_key] = lambda: {"id": caller["id"]}

        def as_(who, scope=None):
            caller["id"] = ids[who]
            caller["scope"] = scope
            return TestClient(app)

        as_.db = db
        as_.ids = ids

        try:
            yield as_, complaint_id
        finally:
            app.dependency_overrides.clear()


# --- the full listing and the activity feed ------------------------------------

@pytest.mark.parametrize("who", ["plain", "staff"])
def test_audit_logs_listing_is_forbidden_to_a_non_admin(env, who):
    as_, _ = env
    r = as_(who).get("/api/v1/audit/logs/")
    assert r.status_code == 403, r.text


@pytest.mark.parametrize("who", ["plain", "staff"])
def test_activity_feed_is_forbidden_to_a_non_admin(env, who):
    as_, _ = env
    r = as_(who).get("/api/v1/audit/activity")
    assert r.status_code == 403, r.text


@pytest.mark.parametrize("who", ["superadmin", "admin"])
def test_superadmin_and_admin_read_both(env, who):
    as_, complaint_id = env
    client = as_(who)
    logs = client.get("/api/v1/audit/logs/")
    assert logs.status_code == 200, logs.text
    assert [row["entity_id"] for row in logs.json()["data"]] == [complaint_id]
    feed = client.get("/api/v1/audit/activity")
    assert feed.status_code == 200, feed.text


# --- the per-record Audit Trail panel ------------------------------------------

def test_one_record_history_is_readable_with_that_records_view_permission(env):
    as_, complaint_id = env
    r = as_("staff").get(f"/api/v1/audit/logs/?entity_type=complaint&entity_id={complaint_id}")
    assert r.status_code == 200, r.text
    assert [row["entity_id"] for row in r.json()["data"]] == [complaint_id]


def test_one_record_history_is_forbidden_without_that_view_permission(env):
    as_, complaint_id = env
    r = as_("plain").get(f"/api/v1/audit/logs/?entity_type=complaint&entity_id={complaint_id}")
    assert r.status_code == 403, r.text


def test_a_view_permission_does_not_open_another_entity_types_history(env):
    as_, _ = env
    # `users` history carries IP addresses and account changes; no detail-page panel reads it.
    r = as_("staff").get(f"/api/v1/audit/logs/?entity_type=users&entity_id={uuid.uuid4()}")
    assert r.status_code == 403, r.text


def test_an_entity_type_without_an_entity_id_is_a_listing_not_a_record(env):
    as_, _ = env
    r = as_("staff").get("/api/v1/audit/logs/?entity_type=complaint")
    assert r.status_code == 403, r.text


def test_forbidden_is_an_app_exception_body(env):
    # CLAUDE.md: routes raise AppException, which serialises {message, detail, code}.
    as_, _ = env
    r = as_("plain").get("/api/v1/audit/logs/")
    assert r.status_code == 403, r.text
    body = r.json()
    assert body["code"] == "audit_admin_required"
    assert body["message"]


# --- the packing list Timeline tab (R17) ----------------------------------------

def test_packing_list_timeline_is_readable_with_the_packing_list_view_permission(env):
    # PackingListTimelineTab reads `entity_type=inbound_shipments` (usePackingLists.ts)
    # for any user who can open the packing list; a17f4b36 answered that with a 403.
    as_, _ = env
    shipment_id = str(uuid.uuid4())
    as_.db.add(AuditLog(
        id=str(uuid.uuid4()), entity_type="inbound_shipments", entity_id=shipment_id,
        action="UPDATE", old_values={"status": "draft"}, new_values={"status": "confirmed"},
        description="Over capacity: carrier confirmed the extra pallet",
    ))
    as_.db.commit()
    r = as_("packing").get(
        f"/api/v1/audit/logs/?entity_type=inbound_shipments&entity_id={shipment_id}"
    )
    assert r.status_code == 200, r.text
    assert [row["entity_id"] for row in r.json()["data"]] == [shipment_id]


def test_the_packing_list_permission_does_not_open_a_complaints_history(env):
    as_, complaint_id = env
    r = as_("packing").get(f"/api/v1/audit/logs/?entity_type=complaint&entity_id={complaint_id}")
    assert r.status_code == 403, r.text


# --- company scope ---------------------------------------------------------------

def _row_in(db, company_id, entity_type="complaint", user_id=None):
    entity_id = str(uuid.uuid4())
    db.add(AuditLog(
        id=str(uuid.uuid4()), entity_type=entity_type, entity_id=entity_id,
        action="UPDATE", old_values={"status": "pending"}, new_values={"status": "resolved"},
        company_id=company_id, user_id=user_id,
    ))
    db.commit()
    return entity_id


def test_one_record_history_does_not_cross_companies(env):
    # The per-record path is the one open to non-admin staff: a staff user whose
    # request is scoped to company A gets nothing for a company-B record's history.
    as_, _ = env
    in_b = _row_in(as_.db, COMPANY_B)
    r = as_("staff", frozenset({COMPANY_A})).get(
        f"/api/v1/audit/logs/?entity_type=complaint&entity_id={in_b}"
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"] == []
    # The same staff user scoped to B sees it, so the empty answer above is the scope.
    r = as_("staff", frozenset({COMPANY_B})).get(
        f"/api/v1/audit/logs/?entity_type=complaint&entity_id={in_b}"
    )
    assert [row["entity_id"] for row in r.json()["data"]] == [in_b]


@pytest.mark.parametrize("scope", [UNSET, frozenset()], ids=["unset", "empty"])
def test_one_record_history_is_closed_to_a_caller_with_no_company(env, scope):
    # A JWT user with no company grant resolves to UNSET (offboarding clears the
    # grants, a deleted company cascades them away). The admin listing filter reads
    # that as "every company", so on 468b2cc6 this staff user got company B's row.
    as_, _ = env
    in_b = _row_in(as_.db, COMPANY_B)
    r = as_("staff", scope).get(f"/api/v1/audit/logs/?entity_type=complaint&entity_id={in_b}")
    assert r.status_code == 403, r.text
    assert r.json()["code"] == "audit_company_required"


def test_an_unscoped_principal_keeps_the_per_record_read(env):
    # None is the deliberate all-companies principal (an X-API-Key with no contact
    # identity, AC-F1); only UNSET and empty fail closed.
    as_, _ = env
    in_b = _row_in(as_.db, COMPANY_B)
    r = as_("staff", None).get(f"/api/v1/audit/logs/?entity_type=complaint&entity_id={in_b}")
    assert r.status_code == 200, r.text
    assert [row["entity_id"] for row in r.json()["data"]] == [in_b]


@pytest.mark.parametrize("scope", [UNSET, frozenset()], ids=["unset", "empty"])
def test_an_admin_with_no_company_keeps_reading(env, scope):
    # Admins keep the admin listing filter's four-state behaviour.
    as_, _ = env
    in_b = _row_in(as_.db, COMPANY_B)
    r = as_("admin", scope).get(f"/api/v1/audit/logs/?entity_type=complaint&entity_id={in_b}")
    assert r.status_code == 200, r.text
    assert [row["entity_id"] for row in r.json()["data"]] == [in_b]


def test_the_logs_listing_does_not_cross_companies(env):
    as_, _ = env
    in_a = _row_in(as_.db, COMPANY_A)
    in_b = _row_in(as_.db, COMPANY_B)
    r = as_("admin", frozenset({COMPANY_A})).get("/api/v1/audit/logs/")
    assert r.status_code == 200, r.text
    ids = {row["entity_id"] for row in r.json()["data"]}
    assert in_a in ids
    assert in_b not in ids


def test_the_activity_feed_does_not_cross_companies(env):
    # a17f4b36: /audit/logs/ hid the company-B row and /audit/activity showed it.
    as_, _ = env
    actor_a, actor_b = as_.ids["plain"], as_.ids["packing"]
    in_a = _row_in(as_.db, COMPANY_A, user_id=actor_a)
    in_b = _row_in(as_.db, COMPANY_B, user_id=actor_b)
    r = as_("admin", frozenset({COMPANY_A})).get("/api/v1/audit/activity")
    assert r.status_code == 200, r.text
    body = r.json()
    ids = {item["entity_id"] for item in body["items"]}
    assert in_a in ids
    assert in_b not in ids
    assert body["pagination"]["total"] == len(body["items"])
    # The actors dropdown keeps to the same company as the feed.
    actor_ids = {a["id"] for a in body["actors"]}
    assert actor_a in actor_ids
    assert actor_b not in actor_ids


# --- the Product Specifications History tab -------------------------------------

def _spec_row_history(as_):
    row_id = str(uuid.uuid4())
    as_.db.add(AuditLog(
        id=str(uuid.uuid4()), entity_type="product_spec_registry", entity_id=row_id,
        action="UPDATE", old_values={"label": "A"}, new_values={"label": "B"},
    ))
    as_.db.commit()
    return row_id


def test_spec_registry_history_is_readable_with_spec_registry_view(env):
    as_, _ = env
    row_id = _spec_row_history(as_)
    r = as_("specreg").get(f"/api/v1/audit/logs/?entity_type=product_spec_registry&entity_id={row_id}")
    assert r.status_code == 200, r.text
    assert [row["entity_id"] for row in r.json()["data"]] == [row_id]


def test_spec_registry_history_is_forbidden_without_spec_registry_view(env):
    as_, _ = env
    row_id = _spec_row_history(as_)
    for who in ("plain", "staff", "packing"):
        r = as_(who).get(f"/api/v1/audit/logs/?entity_type=product_spec_registry&entity_id={row_id}")
        assert r.status_code == 403, (who, r.text)
