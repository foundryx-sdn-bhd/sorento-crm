"""Who may create and qualify a lead (owner ruling, 13 Oct, PR #1618).

A new project now needs its lead, so whoever may register a project must be able to create
that lead: the lead create and qualify routes take ``projects.projects.edit``, the same grant
``POST /projects`` takes. They used to take ``projects.projects.create``, a slug no permission
registry defines, which left every non-admin salesperson with a 403 and no way to register.

The route-level gate is exercised for real here (not stubbed to True): the permission check
answers from the slugs this test hands the user, and the user holds no admin role.
"""
from __future__ import annotations

import uuid

import pytest

from app.services import project_seed_service

from .test_project_form_lead_link import (
    LEADS_BASE,
    MARKER,
    _restore,
    _sorento,
    _user,
)
from ._pg_fixture import blank_session

EDIT_ONLY = ["projects.projects.view", "projects.projects.edit"]
VIEW_ONLY = ["projects.projects.view"]


def _gated_client(db, user_id: str, perms):
    """A TestClient whose route gate checks `perms` for real; no admin bypass applies."""
    from fastapi.testclient import TestClient

    from app.database import get_db
    from app.dependencies import get_current_user, get_current_user_or_api_key
    from app.main import app
    from app.services.company_scope_resolver import apply_company_scope
    from app.services.user_service import UserPermissionService

    actor = {"id": user_id, "email": f"{user_id}@zzt.test", "role": "salesperson"}
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: dict(actor)
    app.dependency_overrides[get_current_user_or_api_key] = lambda: dict(actor)
    app.dependency_overrides[apply_company_scope] = lambda: None

    originals = (
        UserPermissionService.check_user_has_permission,
        UserPermissionService.get_user_permission_slugs,
    )
    UserPermissionService.check_user_has_permission = lambda self, uid, slug: slug in perms
    UserPermissionService.get_user_permission_slugs = lambda self, uid: list(perms)
    return TestClient(app), originals


@pytest.fixture()
def session():
    from app.models.base import company_scope

    with blank_session() as db:
        company_id = _sorento(db)
        project_seed_service.run(db, company_id=company_id)
        with company_scope(db, frozenset({company_id})):
            yield db


def _title() -> str:
    return f"{MARKER} perm {uuid.uuid4().hex}"


def test_a_salesperson_with_edit_can_create_a_lead(session):
    user_id = _user(session, f"{MARKER} Sales")
    client, originals = _gated_client(session, user_id, EDIT_ONLY)
    try:
        response = client.post(LEADS_BASE, json={"title": _title()})
        assert response.status_code == 201, response.text
    finally:
        _restore(originals)


def test_a_salesperson_with_edit_can_qualify_a_lead(session):
    user_id = _user(session, f"{MARKER} Sales")
    client, originals = _gated_client(session, user_id, EDIT_ONLY)
    try:
        lead = client.post(LEADS_BASE, json={"title": _title()}).json()
        response = client.post(
            f"{LEADS_BASE}/{lead['id']}/qualify", json={"title": _title()}
        )
        assert response.status_code == 201, response.text
    finally:
        _restore(originals)


def test_view_only_still_cannot_create_a_lead(session):
    user_id = _user(session, f"{MARKER} Reader")
    client, originals = _gated_client(session, user_id, VIEW_ONLY)
    try:
        response = client.post(LEADS_BASE, json={"title": _title()})
        assert response.status_code == 403, response.text
    finally:
        _restore(originals)


MANAGER = EDIT_ONLY + ["projects.projects.manage"]


def test_an_edit_holder_cannot_qualify_a_project_onto_someone_else(session):
    user_id = _user(session, f"{MARKER} Sales")
    other_id = _user(session, f"{MARKER} Other")
    client, originals = _gated_client(session, user_id, EDIT_ONLY)
    try:
        lead = client.post(LEADS_BASE, json={"title": _title()}).json()
        response = client.post(
            f"{LEADS_BASE}/{lead['id']}/qualify",
            json={"title": _title(), "owner_user_id": other_id},
        )
        assert response.status_code == 403, response.text
        assert response.json()["code"] == "project_owner_assign_forbidden"
    finally:
        _restore(originals)


def test_a_manager_may_qualify_onto_someone_else(session):
    user_id = _user(session, f"{MARKER} Manager")
    other_id = _user(session, f"{MARKER} Other")
    client, originals = _gated_client(session, user_id, MANAGER)
    try:
        lead = client.post(LEADS_BASE, json={"title": _title()}).json()
        response = client.post(
            f"{LEADS_BASE}/{lead['id']}/qualify",
            json={"title": _title(), "owner_user_id": other_id},
        )
        assert response.status_code == 201, response.text
        assert response.json()["owner_user_id"] == other_id
    finally:
        _restore(originals)


def test_an_edit_holder_cannot_create_a_lead_owned_by_someone_else(session):
    user_id = _user(session, f"{MARKER} Sales")
    other_id = _user(session, f"{MARKER} Other")
    client, originals = _gated_client(session, user_id, EDIT_ONLY)
    try:
        # Naming oneself stays fine. First: the refusal below rolls the request's session
        # back, and that would take this test's own user rows with it.
        own = client.post(LEADS_BASE, json={"title": _title(), "owner_user_id": user_id})
        assert own.status_code == 201, own.text
        response = client.post(
            LEADS_BASE, json={"title": _title(), "owner_user_id": other_id}
        )
        assert response.status_code == 403, response.text
        assert response.json()["code"] == "lead_owner_assign_forbidden"
    finally:
        _restore(originals)
