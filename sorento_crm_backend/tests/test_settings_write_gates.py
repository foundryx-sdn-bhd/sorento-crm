"""SETTINGS-PERM-GATE: the four system-settings writes that checked only "signed in".

`PUT /settings/social`, `PUT /settings/notifications`, `PUT /settings/smtp` and
`POST /settings/smtp/test` depended on `get_current_user` alone, so any staff or portal
session could repoint outbound SMTP (host, login, password, From) or rewrite who is
notified of what. Found by the DO-BACKFILL-SETTING security review (PR #1700).

`PUT /forms-management/revision-configs/{type}` (Settings > Portal Revisions) had the
same hole in another router; its own list read already carries `.view`.

The gates follow the slugs the same file already uses:

- social, notifications, portal revision configs: `user_management.settings.edit`, the
  slug `PUT /general`, `POST /general` and `POST /signin-background` carry (and the one
  the Portal Revisions globals already save through, via `/general`).
- smtp + smtp/test: `user_management.settings.secrets`, the slug the runtime secrets
  carry (`RUNTIME_SETTINGS_PERMISSION`): `.edit` is also held by warehouse_manager and
  the three integration API-key roles, and the SMTP password is a credential.

Admin is exercised through the REAL `check_user_has_permission` (its admin bypass), with
only the role lookup pinned, so the test fails if the bypass ever stops reaching these
routes. Every other case pins `check_user_has_permission` against an `allow` set, the
pattern in tests/test_settings_app_config_gate.py.

Postgres `blank_session()` only, never sqlite.

Run: pytest tests/test_settings_write_gates.py -v
"""
from __future__ import annotations

import uuid

import pytest

from tests._pg_fixture import TEST_PREFIX, blank_session

BASE = "/api/v1/user-management/settings"
EDIT = "user_management.settings.edit"
VIEW = "user_management.settings.view"
SECRETS = "user_management.settings.secrets"

ORIGINAL_SMTP_HOST = "zzt-smtp-original.example.test"
ORIGINAL_FACEBOOK = "https://facebook.example.test/zzt-original"

# (method, path, body, slug, (column, value written, value before))
SOCIAL = (
    "put",
    f"{BASE}/social",
    {"social_facebook": "https://facebook.example.test/zzt-changed"},
    EDIT,
    ("social_facebook", "https://facebook.example.test/zzt-changed", ORIGINAL_FACEBOOK),
)
NOTIFICATIONS = (
    "put",
    f"{BASE}/notifications",
    {"notify_stock_email": True},
    EDIT,
    ("notify_stock_email", True, False),
)
SMTP = (
    "put",
    f"{BASE}/smtp",
    {"smtp_host": "zzt-attacker.example.test", "smtp_password": "zzt-new-pass"},
    SECRETS,
    ("smtp_host", "zzt-attacker.example.test", ORIGINAL_SMTP_HOST),
)
SMTP_TEST = ("post", f"{BASE}/smtp/test", None, SECRETS, None)

WRITES = [SOCIAL, NOTIFICATIONS, SMTP, SMTP_TEST]
WRITE_IDS = ["social", "notifications", "smtp", "smtp-test"]


@pytest.fixture
def db():
    with blank_session() as s:
        yield s


@pytest.fixture
def caller(db):
    from app.models.user import User

    user = User(
        id=str(uuid.uuid4()),
        email=f"{TEST_PREFIX}-spg-caller-{uuid.uuid4().hex[:6]}@zzt.test".lower(),
        name=f"{TEST_PREFIX} SPG Caller",
        avatar_storage_provider="s3",
    )
    db.add(user)
    db.commit()
    return user


@pytest.fixture
def settings_row(db):
    from app.models.user import SystemSetting

    row = SystemSetting(
        id=str(uuid.uuid4()),
        name=f"{TEST_PREFIX} Co",
        smtp_host=ORIGINAL_SMTP_HOST,
        smtp_port="587",
        smtp_username="zzt-smtp-user",
        smtp_password="zzt-smtp-original-pass",
        smtp_from="zzt-noreply@example.test",
        social_facebook=ORIGINAL_FACEBOOK,
        notify_stock_email=False,
    )
    db.add(row)
    db.commit()
    return row


@pytest.fixture(autouse=True)
def no_smtp_network(monkeypatch):
    """The test send must never open a socket: pin the service it delegates to."""
    from app.services import notification_email

    calls: list[object] = []

    def _fake(settings):
        calls.append(settings)
        return True, "Connection successful"

    monkeypatch.setattr(notification_email, "test_smtp_connection", _fake)
    return calls


@pytest.fixture
def client(db, caller):
    from fastapi.testclient import TestClient

    from app.database import get_db
    from app.dependencies import get_current_user, get_current_user_or_api_key
    from app.main import app

    def _override_db():
        yield db

    who = {"id": caller.id, "email": caller.email}
    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: who
    # The forms router's module guard resolves the caller through this one.
    app.dependency_overrides[get_current_user_or_api_key] = lambda: who
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_current_user_or_api_key, None)


@pytest.fixture
def allow(monkeypatch):
    from app.services.user_service import UserPermissionService

    granted: set[str] = set()
    monkeypatch.setattr(
        UserPermissionService,
        "check_user_has_permission",
        lambda self, uid, slug: slug in granted,
    )
    return granted


def _send(client, method, path, body):
    if body is None:
        return getattr(client, method)(path)
    return getattr(client, method)(path, json=body)


def _column(db, name):
    from app.models.user import SystemSetting

    db.expire_all()
    return getattr(db.query(SystemSetting).first(), name)


@pytest.mark.parametrize("method, path, body, slug, effect", WRITES, ids=WRITE_IDS)
def test_signed_in_without_the_slug_is_refused_and_nothing_changes(
    client, allow, db, settings_row, no_smtp_network, method, path, body, slug, effect
):
    assert allow == set(), "a signed-in caller holding no permission at all"

    resp = _send(client, method, path, body)

    assert resp.status_code == 403, resp.text
    assert slug in resp.json()["detail"]
    if effect is not None:
        column, _written, before = effect
        assert _column(db, column) == before
    else:
        assert no_smtp_network == [], "the test send ran for a caller without the slug"


@pytest.mark.parametrize("method, path, body, slug, effect", [SMTP, SMTP_TEST], ids=["smtp", "smtp-test"])
def test_smtp_is_refused_to_settings_edit_holders(
    client, allow, db, settings_row, no_smtp_network, method, path, body, slug, effect
):
    """`.edit` (warehouse_manager, the integration roles) is not enough for the mail
    credentials: the same reason the runtime secrets sit on `.secrets`."""
    allow.update({EDIT, VIEW})

    resp = _send(client, method, path, body)

    assert resp.status_code == 403, resp.text
    assert SECRETS in resp.json()["detail"]
    assert _column(db, "smtp_host") == ORIGINAL_SMTP_HOST
    assert _column(db, "smtp_password") == "zzt-smtp-original-pass"
    assert no_smtp_network == []


@pytest.mark.parametrize("method, path, body, slug, effect", WRITES, ids=WRITE_IDS)
def test_holder_of_the_slug_succeeds(
    client, allow, db, settings_row, no_smtp_network, method, path, body, slug, effect
):
    allow.add(slug)

    resp = _send(client, method, path, body)

    assert resp.status_code == 200, resp.text
    if effect is not None:
        column, written, _before = effect
        assert _column(db, column) == written
    else:
        assert resp.json() == {"success": True, "message": "Connection successful"}
        assert len(no_smtp_network) == 1


@pytest.mark.parametrize("method, path, body, slug, effect", WRITES, ids=WRITE_IDS)
def test_admin_still_succeeds_through_the_real_bypass(
    client, db, caller, settings_row, no_smtp_network, monkeypatch, method, path, body, slug, effect
):
    from app.services.user_service import UserPermissionService

    # Only the role lookup is pinned; `check_user_has_permission` itself is real, so
    # this goes through the same admin bypass production does.
    monkeypatch.setattr(
        UserPermissionService,
        "get_user_role_slugs",
        lambda self, uid: {"admin"} if str(uid) == str(caller.id) else set(),
    )

    resp = _send(client, method, path, body)

    assert resp.status_code == 200, resp.text
    if effect is not None:
        column, written, _before = effect
        assert _column(db, column) == written
    else:
        assert len(no_smtp_network) == 1


# --------------------------------------------------------------------------- portal revisions

REVISION_CONFIG_PATH = "/api/v1/forms-management/revision-configs/complaint"
REVISION_CONFIG_BODY = {
    "is_enabled": True,
    "max_revisions": 3,
    "allowed_statuses": ["open"],
    "restart_stage_code": None,
}


def _complaint_config(db):
    from app.models.portal import PortalRevisionConfig

    db.expire_all()
    return (
        db.query(PortalRevisionConfig)
        .filter(PortalRevisionConfig.source_entity_type == "complaint")
        .first()
    )


@pytest.mark.parametrize("held", [set(), {VIEW}], ids=["nothing", "view-only"])
def test_revision_config_put_is_refused_without_the_edit_slug(client, allow, db, held):
    allow.update(held)

    resp = client.put(REVISION_CONFIG_PATH, json=REVISION_CONFIG_BODY)

    assert resp.status_code == 403, resp.text
    assert EDIT in resp.json()["detail"]
    assert _complaint_config(db) is None


def test_revision_config_put_succeeds_for_the_edit_slug_holder(client, allow, db):
    allow.add(EDIT)

    resp = client.put(REVISION_CONFIG_PATH, json=REVISION_CONFIG_BODY)

    assert resp.status_code == 200, resp.text
    row = _complaint_config(db)
    assert row is not None and row.is_enabled is True and row.max_revisions == 3


def test_revision_config_put_still_succeeds_for_admin(client, db, caller, monkeypatch):
    from app.services.user_service import UserPermissionService

    monkeypatch.setattr(
        UserPermissionService,
        "get_user_role_slugs",
        lambda self, uid: {"admin"} if str(uid) == str(caller.id) else set(),
    )

    resp = client.put(REVISION_CONFIG_PATH, json=REVISION_CONFIG_BODY)

    assert resp.status_code == 200, resp.text
    assert _complaint_config(db).is_enabled is True
