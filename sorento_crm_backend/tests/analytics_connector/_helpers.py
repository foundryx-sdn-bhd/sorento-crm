"""Shared set-up for the CLAUDE-CONNECTOR tests (`documentation/plans/analytics/CARD-claude-connector-09oct.md`).

Every helper takes the test's own session: the OAuth tests run on `blank_session()` (an
empty scratch schema), the dataset tests on `pg_session()` (the bootstrapped database,
which holds the `analytics_connector` views).
"""
from __future__ import annotations

import base64
import hashlib
import secrets
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator, Optional

from fastapi.testclient import TestClient

from app.config import settings
from app.database import get_db
from app.main import app
from app.models.user import User, UserPermission, UserRole, UserRoleAssignment, UserRolePermission
from tests._pg_fixture import unique_code

INTERNAL_KEY = "zzt-internal-key-for-tests-only"
INTERNAL = {"X-Analytics-Connector-Key": INTERNAL_KEY}
PUBLIC_BASE = "https://crm.example.com/analytics"
RESOURCE = f"{PUBLIC_BASE}/mcp"
CLAUDE_CALLBACK = "https://claude.ai/api/mcp/auth_callback"
USE = "analytics.connector.use"
MANAGE = "analytics.connector.manage"
CHAT_VIEW = "system.chat_history.view"
BASE = "/api/v1/analytics-connector"


def now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def make_user(db, label: str, *, perms: tuple[str, ...] = (), role_slug: Optional[str] = None,
              status: str = "ACTIVE") -> User:
    """A user holding exactly ``perms`` through one private role (slug ``role_slug`` if given)."""
    user = User(
        id=str(uuid.uuid4()),
        email=f"{unique_code(label)}@example.com".lower(),
        name=f"ZZT {label}",
        status=status,
        is_trashed=False,
    )
    db.add(user)
    role = UserRole(
        id=str(uuid.uuid4()),
        slug=role_slug or unique_code(f"role-{label}").lower(),
        name=f"ZZT role {label}",
        is_protected=False,
        is_default=False,
    )
    db.add(role)
    db.flush()
    db.add(UserRoleAssignment(user_id=user.id, role_id=role.id))
    for slug in perms:
        grant_permission(db, role.id, slug)
    db.commit()
    return user


def grant_permission(db, role_id: str, slug: str) -> None:
    perm = db.query(UserPermission).filter(UserPermission.slug == slug).first()
    if perm is None:
        perm = UserPermission(id=str(uuid.uuid4()), slug=slug, name=slug, description=slug)
        db.add(perm)
        db.flush()
    db.add(UserRolePermission(id=str(uuid.uuid4()), role_id=role_id, permission_id=perm.id))
    db.flush()


def revoke_permission(db, user: User, slug: str) -> None:
    perm = db.query(UserPermission).filter(UserPermission.slug == slug).one()
    role_ids = [a.role_id for a in db.query(UserRoleAssignment).filter(UserRoleAssignment.user_id == user.id)]
    db.query(UserRolePermission).filter(
        UserRolePermission.permission_id == perm.id, UserRolePermission.role_id.in_(role_ids)
    ).delete(synchronize_session=False)
    db.commit()
    from app.services.user_service import invalidate_rbac_cache

    invalidate_rbac_cache()


def staff_headers(db, user: User) -> dict:
    from app.services.user_session_service import mint_session

    return {"Authorization": f"Bearer {mint_session(db, user.id).token}"}


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


@contextmanager
def client_for(db, monkeypatch) -> Iterator[TestClient]:
    """A TestClient bound to ``db``, with the connector's settings pinned."""
    monkeypatch.setattr(settings, "analytics_connector_internal_key", INTERNAL_KEY, raising=False)
    monkeypatch.setattr(settings, "analytics_connector_public_base_url", PUBLIC_BASE, raising=False)

    def _override_db():
        yield db

    app.dependency_overrides[get_db] = _override_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


def register(client: TestClient, redirect_uris=(CLAUDE_CALLBACK,)) -> str:
    resp = client.post(
        f"{BASE}/oauth/register",
        json={"redirect_uris": list(redirect_uris), "client_name": "Claude", "token_endpoint_auth_method": "none",
              "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"]},
        headers=INTERNAL,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["client_id"]


def start_authorize(client: TestClient, client_id: str, challenge: str, **overrides) -> dict:
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": CLAUDE_CALLBACK,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": "st-123",
        "scope": "analytics.read",
        "resource": RESOURCE,
    }
    params.update(overrides)
    return client.post(f"{BASE}/oauth/authorize", json=params, headers=INTERNAL)


def full_sign_in(client: TestClient, db, user: User) -> dict:
    """DCR -> authorize -> consent -> token. Returns the token response plus client_id."""
    client_id = register(client)
    verifier, challenge = pkce_pair()
    started = start_authorize(client, client_id, challenge)
    assert started.status_code == 200, started.text
    request_id = started.json()["request_id"]
    approved = client.post(f"{BASE}/authorize/{request_id}/approve", headers=staff_headers(db, user))
    assert approved.status_code == 200, approved.text
    code = parse_redirect(approved.json()["redirect_to"])["code"]
    tok = client.post(
        f"{BASE}/oauth/token",
        data={"grant_type": "authorization_code", "code": code, "code_verifier": verifier,
              "client_id": client_id, "redirect_uri": CLAUDE_CALLBACK, "resource": RESOURCE},
        headers=INTERNAL,
    )
    assert tok.status_code == 200, tok.text
    body = tok.json()
    body["client_id"] = client_id
    return body


def parse_redirect(url: str) -> dict:
    from urllib.parse import parse_qs, urlsplit

    parts = urlsplit(url)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == CLAUDE_CALLBACK, url
    return {k: v[0] for k, v in parse_qs(parts.query).items()}


def connector_headers(access_token: str) -> dict:
    return {"Authorization": f"Bearer {access_token}", **INTERNAL}
