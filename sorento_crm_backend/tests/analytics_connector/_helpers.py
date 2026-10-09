"""Shared set-up for the CLAUDE-CONNECTOR r3 tests: sorento's side of the SS connector.

Contract: `documentation/plans/analytics/CONTRACT-claude-connector-ss-09oct.md` (sections 2, 3, 5).
Card (data, views, OTP, phone, tools): `documentation/plans/analytics/CARD-claude-connector-09oct.md`.

The OAuth server, grants and MCP endpoint moved to the shared service (SS-CONNECTOR). sorento keeps:
the consent hop (`/api/v1/connector/consent/*`), verification of SS-signed calls
(`Authorization: Connector <JWT>`), the manifest and the chat data API under `/api/v1/connector/*`.

Tokens are minted here with python-jose HS256, the ideation embed pattern
(`app/services/ideation_embed_service.py`). Settings pinned per test (raising=False, so a missing
setting is the coder's to add, not a fixture crash):
`connector_ss_to_product_secret`, `connector_product_to_ss_secret`, `connector_connection_id`,
`connector_ss_callback_url`.

Contract gaps resolved here (relayed to SS on #117):
* `req_hash` = base64url, NO padding, of SHA-256 over the UTF-8 string
  `METHOD\\nPATH\\nCANONICAL_QUERY\\nBODY_SHA256_HEX`.
  - METHOD upper case; PATH = the request path as sent, percent-encoding intact, no query string.
  - CANONICAL_QUERY = the decoded query pairs IN THEIR SENT ORDER (r3 review round: no sorting, so
    `limit=1&limit=500` and `limit=500&limit=1` hash differently), each re-encoded with
    `urllib.parse.quote(x, safe="-_.~")` as `k=v`, joined by `&`; empty string when there is none.
* `tool` must name the manifest tool whose (method, path_template) is the called route
  (`route_not_allowed` otherwise); ``signed``/``call_token`` default it from the manifest via
  ``tool_for`` so a test only passes ``tool=`` to pin or break that binding.
  - BODY_SHA256_HEX = lowercase hex SHA-256 of the raw body bytes (empty bytes for a GET).
* `consented_at` is an int unix timestamp in seconds, like `iat`/`exp`.
"""
from __future__ import annotations

import base64
import hashlib
import json
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator, Optional
from urllib.parse import parse_qsl, quote, urlsplit

from fastapi.testclient import TestClient
from jose import jwt

from app.config import settings
from app.database import get_db
from app.main import app
from app.models.user import User, UserPermission, UserRole, UserRoleAssignment, UserRolePermission
from tests._pg_fixture import unique_code

SS_TO_PRODUCT = "zzt-ss-to-product-secret-for-tests-only-0123456789"
PRODUCT_TO_SS = "zzt-product-to-ss-secret-for-tests-only-9876543210"
CONNECTION_ID = "zzt-conn-sorento"
SS_CALLBACK = "https://ss.example.com/connector/consent/callback"

USE = "analytics.connector.use"
CHAT_VIEW = "system.chat_history.view"
BRANDS_VIEW = "master_data.brands.view"
BASE = "/api/v1/connector"
CONSENT = f"{BASE}/consent"
OLD_BASE = "/api/v1/analytics-connector"
BRANDS = "/api/v1/master-data/brands/"  # the real route path: a redirect would break req_hash

CHAT_TOOL_ROUTES = {
    "chat_list_datasets": ("GET", f"{BASE}/datasets"),
    "chat_describe_dataset": ("GET", f"{BASE}/datasets/{{key}}"),
    "chat_query": ("POST", f"{BASE}/query"),
    "chat_search_messages": ("GET", f"{BASE}/messages/search"),
    "chat_get_conversation": ("GET", f"{BASE}/conversation"),
}


def now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def epoch() -> int:
    return int(datetime.now(timezone.utc).timestamp())


# ------------------------------------------------------------------ users + RBAC


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


def make_admin_impersonating(db, label: str, *, admin_perms: tuple[str, ...] = (USE,),
                             target_perms: tuple[str, ...] = (USE,)) -> tuple[User, User]:
    """An `admin`-role user with an open ImpersonationSession onto a target. blank_session only."""
    from app.models.impersonation import ImpersonationSession

    admin = make_user(db, f"{label}-admin", perms=admin_perms)
    target = make_user(db, f"{label}-target", perms=target_perms)
    role = db.query(UserRole).filter(UserRole.slug == "admin").first()
    if role is None:
        role = UserRole(id=str(uuid.uuid4()), slug="admin", name="ZZT admin", is_protected=False, is_default=False)
        db.add(role)
        db.flush()
    db.add(UserRoleAssignment(user_id=admin.id, role_id=role.id))
    db.add(ImpersonationSession(id=str(uuid.uuid4()), admin_user_id=admin.id, target_user_id=target.id))
    db.commit()
    return admin, target


def staff_headers(db, user: User) -> dict:
    from app.services.user_session_service import mint_session

    return {"Authorization": f"Bearer {mint_session(db, user.id).token}"}


# ------------------------------------------------------------------ client


CONNECTOR_SETTINGS = {
    "connector_ss_to_product_secret": SS_TO_PRODUCT,
    "connector_product_to_ss_secret": PRODUCT_TO_SS,
    "connector_connection_id": CONNECTION_ID,
    "connector_ss_callback_url": SS_CALLBACK,
}


def pin_settings(monkeypatch, **overrides: Any) -> None:
    """Pin the connector settings. A field the Settings model does not declare yet is set on the
    instance dict (pydantic refuses setattr of an undeclared field), so a red run fails on the
    missing feature, not in this fixture."""
    for name, value in {**CONNECTOR_SETTINGS, **overrides}.items():
        if name in type(settings).model_fields:
            monkeypatch.setattr(settings, name, value, raising=False)
        else:
            monkeypatch.setitem(settings.__dict__, name, value)


@contextmanager
def client_for(db, monkeypatch) -> Iterator[TestClient]:
    """A TestClient bound to ``db``, with the connector's settings pinned."""
    pin_settings(monkeypatch)

    def _override_db():
        yield db

    app.dependency_overrides[get_db] = _override_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


def error_code(resp) -> Optional[str]:
    """`{"error": {"code", "message"}}` (contract section 5)."""
    try:
        body = resp.json()
    except ValueError:
        return None
    err = body.get("error") if isinstance(body, dict) else None
    return err.get("code") if isinstance(err, dict) else None


def assert_error(resp, status: int, code: str) -> None:
    assert resp.status_code == status, (resp.status_code, resp.text)
    body = resp.json()
    assert error_code(resp) == code, resp.text
    assert isinstance(body["error"].get("message"), str) and body["error"]["message"], resp.text


# ------------------------------------------------------------------ consent requests (SS -> browser -> sorento)


def consent_request(*, secret: str = SS_TO_PRODUCT, drop: tuple[str, ...] = (), **overrides: Any) -> tuple[str, dict]:
    """A signed `connector_consent_request`; returns (jwt, claims)."""
    t = epoch()
    claims: dict[str, Any] = {
        "typ": "connector_consent_request",
        "iss": "foundryx-ss",
        "aud": "sorento-connector-consent",
        "connection_id": CONNECTION_ID,
        "request_id": f"req-{uuid.uuid4().hex}",
        "client_name": "Claude",
        "redirect_host": "claude.ai",
        "return_url": SS_CALLBACK,
        "iat": t,
        "exp": t + 600,
        "jti": uuid.uuid4().hex,
    }
    claims.update(overrides)
    for k in drop:
        claims.pop(k, None)
    return jwt.encode(claims, secret, algorithm="HS256"), claims


# ------------------------------------------------------------------ signed calls (SS -> sorento)


def canonical_query(query: str) -> str:
    pairs = parse_qsl(query, keep_blank_values=True)
    return "&".join(f"{quote(k, safe='-_.~')}={quote(v, safe='-_.~')}" for k, v in pairs)


def req_hash(method: str, path: str, query: str = "", body: bytes = b"") -> str:
    material = f"{method.upper()}\n{path}\n{canonical_query(query)}\n{hashlib.sha256(body).hexdigest()}"
    return base64.urlsafe_b64encode(hashlib.sha256(material.encode()).digest()).rstrip(b"=").decode()


_UNSET: Any = object()


def tool_for(method: str, path: str) -> str:
    """The manifest tool bound to ``method path`` (first match), or ``"zzt_tool"`` for a route outside
    the manifest (route-guard tests, the manifest itself)."""
    import re

    from app.services.analytics_connector.manifest import build_manifest

    for t in build_manifest()["tools"]:
        h = t["http"]
        pattern = "^" + re.sub(r"\\\{[^/]+?\\\}", "[^/]+", re.escape(h["path_template"])) + "$"
        if h["method"].upper() == method.upper() and re.match(pattern, path):
            return t["name"]
    return "zzt_tool"


def call_token(sub: str, method: str, path: str, query: str = "", body: bytes = b"", *,
               secret: str = SS_TO_PRODUCT, tool: Any = _UNSET, grant_id: Optional[str] = None,
               consented_at: Optional[int] = None, drop: tuple[str, ...] = (), **overrides: Any) -> str:
    t = epoch()
    claims: dict[str, Any] = {
        "typ": "connector_call",
        "iss": "foundryx-ss",
        "aud": "sorento-connector-api",
        "connection_id": CONNECTION_ID,
        "sub": sub,
        "grant_id": grant_id or f"grant-{uuid.uuid4().hex[:12]}",
        "consented_at": consented_at if consented_at is not None else t - 60,
        "tool": tool_for(method, path) if tool is _UNSET else tool,
        "req_hash": req_hash(method, path, query, body),
        "iat": t,
        "exp": t + 60,
        "jti": uuid.uuid4().hex,
    }
    claims.update(overrides)
    for k in drop:
        claims.pop(k, None)
    return jwt.encode(claims, secret, algorithm="HS256")


def signed(client: TestClient, method: str, url: str, sub: Optional[str], *, json_body: Any = None,
           token: Optional[str] = None, extra_headers: Optional[dict] = None, **claims: Any):
    """Send ``method url`` with a fresh `Authorization: Connector <JWT>` bound to it.

    ``url`` may carry a query string; the body (if any) is serialised here so the bytes hashed
    are the bytes sent.
    """
    parts = urlsplit(url)
    body = b"" if json_body is None else json.dumps(json_body, separators=(",", ":")).encode()
    tok = token or call_token(sub or "", method, parts.path, parts.query, body, **claims)
    headers = {"Authorization": f"Connector {tok}", **(extra_headers or {})}
    if body:
        headers["Content-Type"] = "application/json"
    return client.request(method, url, content=body or None, headers=headers)


def tool_call_rows(db, user_id: str):
    from app.models.audit import AuditLog

    db.expire_all()
    return (
        db.query(AuditLog)
        .filter(AuditLog.event == "connector.tool_call", AuditLog.user_id == user_id)
        .order_by(AuditLog.changed_at.asc())
        .all()
    )
