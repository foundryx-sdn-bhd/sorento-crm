"""Shared TestClient set-up for the MODEL-PRICES tests (usage cost + model price routes)."""
from __future__ import annotations

import uuid
from contextlib import contextmanager

VIEW = "system.ai_assistant_settings.view"
EDIT = "system.ai_assistant_settings.edit"


@contextmanager
def api_client(db, permissions):
    """A TestClient bound to ``db`` whose caller holds exactly ``permissions``."""
    from fastapi.testclient import TestClient

    from app.database import get_db
    from app.dependencies import get_current_user, get_current_user_or_api_key
    from app.main import app
    from app.services.company_scope_resolver import apply_company_scope
    from app.services.user_service import UserPermissionService

    user_id = str(uuid.uuid4())
    actor = {"id": user_id, "email": f"{user_id}@zzt.test", "role": "user"}
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: dict(actor)
    app.dependency_overrides[get_current_user_or_api_key] = lambda: dict(actor)
    app.dependency_overrides[apply_company_scope] = lambda: None
    originals = (
        UserPermissionService.check_user_has_permission,
        UserPermissionService.get_user_permission_slugs,
    )
    granted = list(permissions)
    UserPermissionService.check_user_has_permission = lambda self, uid, slug: slug in granted
    UserPermissionService.get_user_permission_slugs = lambda self, uid: list(granted)
    try:
        client = TestClient(app)
        client.actor = actor
        yield client
    finally:
        UserPermissionService.check_user_has_permission = originals[0]
        UserPermissionService.get_user_permission_slugs = originals[1]
        app.dependency_overrides.clear()
