"""The per-type revision config endpoints, over HTTP (UAC A2 / A6).

    GET /api/v1/forms-management/revision-configs
    PUT /api/v1/forms-management/revision-configs/{source_entity_type}

Two things this file exists to pin:

1. **The mount point.** The plan first wrote these as ``/api/v1/forms/...``; there is
   no such prefix - the forms router mounts at ``/forms-management``. A 404 here is
   the settings table rendering its empty state against nothing.
2. **The field list.** A ``response_model`` silently drops anything it does not name,
   and the symptom is a settings row that saves and comes back with the value
   missing. Every field the FE service declares is asserted on the wire.

Postgres only, on the blank scratch schema. The four seed rows live in the MIGRATION
BODY, so ``create_all`` gives the table and no rows - which is exactly the "missing
row means disabled" case GET has to survive.
"""
from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

# MUST be first app import - resolves a circular import in app.modules.runtime.guards
from app.main import app  # noqa: E402

from app.models.portal import PortalRevisionConfig
from tests._pg_fixture import blank_session

BASE = "/api/v1/forms-management/revision-configs"

CONFIG_KEYS = {
    "source_entity_type",
    "is_enabled",
    "max_revisions",
    "allowed_statuses",
    "restart_stage_code",
}

# AC-R6: price_tag_request is a fifth GRANTABLE_PORTAL_FORM_TYPES row (the
# route lists placeholders from that tuple, not the narrower SUPPORTED_TYPES
# the generic submissions CRUD uses - see app/api/v1/forms/revision_configs.py).
# sales_opportunity (plan S2) is NOT a seventh: it has no revision-engine wiring
# at all (no PortalRevisionService dispatch, no revise/save-draft route), so a
# settings row for it would configure nothing - the router excludes it via its
# own REVISABLE_PORTAL_FORM_TYPES (GRANTABLE_PORTAL_FORM_TYPES minus this kind).
# sample_request (SR-CORE #1718) is excluded for the same reason: the revise route
# refuses it (no RevisionAdapter in portal_revision_service.ADAPTERS).
PORTAL_TYPES = {
    "complaint",
    "stock_inquiry",
    "purchase_request",
    "sponsorship_form",
    "price_tag_request",
}


@pytest.fixture
def client(monkeypatch):
    from app.database import get_db
    from app.services.user_service import UserPermissionService

    # LIST-API-PERMS: GET /revision-configs carries `user_management.settings.view` now; the
    # office actor has no users row, so answer the role lookup as admin (the gate has its own tests).
    monkeypatch.setattr(UserPermissionService, "get_user_role_slugs", lambda self, uid: {"admin"})
    with blank_session() as db:

        def _override_get_db():
            yield db

        app.dependency_overrides[get_db] = _override_get_db
        try:
            with patch("app.services.queue_service.enqueue_job", return_value=None):
                with TestClient(app) as c:
                    yield c, db
        finally:
            app.dependency_overrides.clear()


def _as_office_user():
    """Authenticate as an office user, the way the neighbouring forms routes are."""
    from app.dependencies import get_current_user, get_current_user_or_api_key

    actor = {"id": str(uuid.uuid4()), "email": "office@example.test", "role": "admin"}
    app.dependency_overrides[get_current_user] = lambda: actor
    app.dependency_overrides[get_current_user_or_api_key] = lambda: actor
    return actor


def _seed_config(db, source_entity_type: str, **kwargs) -> PortalRevisionConfig:
    row = PortalRevisionConfig(
        id=str(uuid.uuid4()),
        source_entity_type=source_entity_type,
        is_enabled=kwargs.pop("is_enabled", True),
        max_revisions=kwargs.pop("max_revisions", None),
        allowed_statuses=kwargs.pop("allowed_statuses", ["pending_purchasing"]),
        restart_stage_code=kwargs.pop("restart_stage_code", None),
    )
    db.add(row)
    db.commit()
    return row


# --------------------------------------------------------------------------- #
# GET
# --------------------------------------------------------------------------- #


def test_get_lists_one_entry_per_portal_type_even_with_no_rows(client):
    """A create_all schema has the table and no seed rows. The settings table still
    has to render all four types - a missing row IS the disabled state (UAC A3),
    not an absent one."""
    c, _db = client
    _as_office_user()

    response = c.get(BASE)
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert {item["source_entity_type"] for item in items} == PORTAL_TYPES
    assert all(item["is_enabled"] is False for item in items)


@pytest.mark.parametrize("kind", ["sales_opportunity", "sample_request"])
def test_a_grantable_kind_with_no_revision_engine_is_not_listed_and_not_puttable(client, kind):
    """sales_opportunity (S2-4) and sample_request (SR-CORE #1718) are in
    GRANTABLE_PORTAL_FORM_TYPES but have no revision-engine wiring, so neither may
    offer a settings row that configures nothing (REVISABLE_PORTAL_FORM_TYPES
    excludes them)."""
    c, db = client
    _as_office_user()

    items = c.get(BASE).json()["items"]
    assert kind not in {i["source_entity_type"] for i in items}

    response = c.put(
        f"{BASE}/{kind}",
        json={
            "is_enabled": True,
            "max_revisions": None,
            "allowed_statuses": [],
            "restart_stage_code": None,
        },
    )
    assert response.status_code == 400
    assert db.query(PortalRevisionConfig).count() == 0


def test_get_declares_every_field_the_settings_table_reads(client):
    c, db = client
    _as_office_user()
    _seed_config(
        db,
        "stock_inquiry",
        max_revisions=3,
        allowed_statuses=["pending_purchasing", "responded"],
        restart_stage_code="project_sales",
    )

    items = c.get(BASE).json()["items"]
    row = next(i for i in items if i["source_entity_type"] == "stock_inquiry")
    assert set(row) == CONFIG_KEYS
    assert row["is_enabled"] is True
    assert row["max_revisions"] == 3
    assert row["allowed_statuses"] == ["pending_purchasing", "responded"]
    assert row["restart_stage_code"] == "project_sales"


def test_get_needs_authentication(client):
    c, _db = client
    assert c.get(BASE).status_code == 401


# --------------------------------------------------------------------------- #
# PUT
# --------------------------------------------------------------------------- #


def test_put_creates_the_row_when_the_seed_never_ran(client):
    c, db = client
    _as_office_user()

    response = c.put(
        f"{BASE}/purchase_request",
        json={
            "is_enabled": True,
            "max_revisions": 1,
            "allowed_statuses": ["submitted", "approved"],
            "restart_stage_code": None,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == CONFIG_KEYS
    assert body["source_entity_type"] == "purchase_request"
    assert body["is_enabled"] is True
    assert body["max_revisions"] == 1

    persisted = (
        db.query(PortalRevisionConfig)
        .filter(PortalRevisionConfig.source_entity_type == "purchase_request")
        .one()
    )
    assert persisted.allowed_statuses == ["submitted", "approved"]


def test_put_updates_in_place_rather_than_inserting_a_second_row(client):
    c, db = client
    _as_office_user()
    _seed_config(db, "stock_inquiry", is_enabled=False)

    response = c.put(
        f"{BASE}/stock_inquiry",
        json={
            "is_enabled": True,
            "max_revisions": None,
            "allowed_statuses": ["responded"],
            "restart_stage_code": "purchasing",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["max_revisions"] is None  # NULL inherits the global cap

    rows = (
        db.query(PortalRevisionConfig)
        .filter(PortalRevisionConfig.source_entity_type == "stock_inquiry")
        .all()
    )
    assert len(rows) == 1
    assert rows[0].is_enabled is True
    assert rows[0].restart_stage_code == "purchasing"


def test_put_normalises_the_status_list(client):
    """The policy resolver lowercases before comparing, so storing anything else
    only makes the settings table disagree with what it enforces."""
    c, _db = client
    _as_office_user()

    body = c.put(
        f"{BASE}/complaint",
        json={
            "is_enabled": False,
            "max_revisions": None,
            "allowed_statuses": [" Submitted ", "submitted", "RESPONDED", "  "],
            "restart_stage_code": "  ",
        },
    ).json()
    assert body["allowed_statuses"] == ["submitted", "responded"]
    assert body["restart_stage_code"] is None


def test_put_refuses_a_type_that_is_not_a_portal_submission(client):
    c, db = client
    _as_office_user()

    response = c.put(
        f"{BASE}/ticket",
        json={
            "is_enabled": True,
            "max_revisions": None,
            "allowed_statuses": [],
            "restart_stage_code": None,
        },
    )
    assert response.status_code == 400
    assert db.query(PortalRevisionConfig).count() == 0


def test_put_needs_authentication(client):
    """Denial writes nothing: an unauthenticated caller must not be able to turn
    revisions on for a type."""
    c, db = client

    response = c.put(
        f"{BASE}/stock_inquiry",
        json={
            "is_enabled": True,
            "max_revisions": None,
            "allowed_statuses": ["responded"],
            "restart_stage_code": None,
        },
    )
    assert response.status_code == 401
    assert db.query(PortalRevisionConfig).count() == 0


def test_the_saved_config_is_what_the_policy_then_enforces(client):
    """The round trip that matters: a save through the route changes the answer the
    portal gets, with no restart and no second source of truth."""
    from app.services.portal_revision_service import PortalRevisionService

    c, db = client
    _as_office_user()
    c.put(
        f"{BASE}/stock_inquiry",
        json={
            "is_enabled": True,
            "max_revisions": 2,
            "allowed_statuses": ["responded"],
            "restart_stage_code": None,
        },
    )

    service = PortalRevisionService(db)
    config = service.get_config("stock_inquiry")
    assert config is not None and config.allowed_statuses == ["responded"]

    c.put(
        f"{BASE}/stock_inquiry",
        json={
            "is_enabled": False,
            "max_revisions": 2,
            "allowed_statuses": ["responded"],
            "restart_stage_code": None,
        },
    )
    db.expire_all()
    assert service.get_config("stock_inquiry").is_enabled is False
