"""SS-signed connector calls: verification, the exact route list, the user checks and the audit row.

Contract: documentation/plans/analytics/CONTRACT-claude-connector-ss-09oct.md, sections 3, 5 and 7.
The shared service (SS) runs the OAuth server and the MCP endpoint; every call it makes here carries
``Authorization: Connector <JWT>`` signed with ``connector_ss_to_product_secret``. ``connector_call_gate``
(a router dependency on every ``/api/v1`` route, ahead of the company scope) verifies it and stamps the
call's user; ``app.dependencies`` then returns that user, with no ``user_sessions`` row and no
impersonation, so the route runs with its normal RBAC and company scope.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, AsyncIterator, Optional
from urllib.parse import parse_qsl, quote

from fastapi import Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.analytics_connector.reused_tools import REUSED_TOOLS
from app.services.error_handler import AppException

USE = "analytics.connector.use"
CHAT_VIEW = "system.chat_history.view"
BASE = "/api/v1/connector"
MANIFEST_PATH = f"{BASE}/manifest"

SS_ISSUER = "foundryx-ss"
CALL_TYP = "connector_call"
CALL_AUDIENCE = "sorento-connector-api"
CALL_MAX_LIFETIME = 60  # seconds, exp - iat (section 7.2)
CLOCK_SKEW = 5  # seconds, on iat / exp
REPLAY_TTL = 120  # seconds a call jti is remembered (section 3)
MAX_TOKEN_CHARS = 4096
_CALL_CLAIMS = (
    "typ", "iss", "aud", "connection_id", "sub", "grant_id", "consented_at", "tool", "req_hash",
    "iat", "exp", "jti",
)

# The chat data routes (the five chat tools). They also need CHAT_VIEW (owner ruling Q2 (a)).
CHAT_ROUTES: frozenset[tuple[str, str]] = frozenset({
    ("GET", f"{BASE}/datasets"),
    ("GET", f"{BASE}/datasets/{{key}}"),
    ("POST", f"{BASE}/query"),
    ("GET", f"{BASE}/messages/search"),
    ("GET", f"{BASE}/conversation"),
})

# Every (METHOD, route path) a connector call may reach: the manifest's tool routes plus the manifest
# itself. Matched against the route FastAPI resolved, so `{param}` is one segment and nothing under
# the same prefix (an export, a file, a write) gets through.
CONNECTOR_ROUTES: frozenset[tuple[str, str]] = (
    frozenset((t["method"], t["path"]) for t in REUSED_TOOLS) | CHAT_ROUTES | {("GET", MANIFEST_PATH)}
)

# Percent-escapes that could re-introduce a separator or a dot segment after decoding.
_SUSPECT_ESCAPES = ("%2f", "%5c", "%2e", "%00")


class ConnectorError(AppException):
    """``{"error": {"code", "message"}}`` with the section 5 codes (served by the AppException handler)."""

    def __init__(self, status_code: int, code: str, message: str):
        HTTPException.__init__(self, status_code=status_code, detail={"error": {"code": code, "message": message}})
        self.code = code
        self.message = message


def _bad_token(message: str = "The connector signature is not valid.") -> ConnectorError:
    return ConnectorError(401, "invalid_signature", message)


def _not_configured() -> ConnectorError:
    return ConnectorError(503, "upstream_error", "The Claude connector is not configured.")


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _same(a: Optional[str], b: Optional[str]) -> bool:
    if not isinstance(a, str) or not isinstance(b, str):
        return False
    return hmac.compare_digest(a.encode(), b.encode())


# ------------------------------------------------------------------ req_hash (section 7.1)


def canonical_query(query: str) -> str:
    """The query pairs in their SENT order (section 7.1 as amended in section 9): a repeated key
    cannot be reordered under one hash."""
    pairs = parse_qsl(query, keep_blank_values=True)
    return "&".join(f"{quote(k, safe='-_.~')}={quote(v, safe='-_.~')}" for k, v in pairs)


def request_hash(method: str, raw_path: str, query: str, body: bytes) -> str:
    material = f"{method.upper()}\n{raw_path}\n{canonical_query(query)}\n{hashlib.sha256(body).hexdigest()}"
    return base64.urlsafe_b64encode(hashlib.sha256(material.encode()).digest()).rstrip(b"=").decode()


# ------------------------------------------------------------------ the call token


def verify_token(token: str) -> dict:
    """The verified claims of a call token, or ConnectorError 401 / 503. Needs no body: the
    request's own binding (``req_hash``) is checked by ``check_request_hash`` once the body is read."""
    from app.services import runtime_settings

    config = runtime_settings.get_many(("connector_ss_to_product_secret", "connector_connection_id"))
    secret = config["connector_ss_to_product_secret"]
    connection_id = config["connector_connection_id"]
    if not secret or not connection_id:
        raise _not_configured()
    if not token or len(token) > MAX_TOKEN_CHARS:
        raise _bad_token()
    try:
        claims = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            options={
                "verify_aud": False, "verify_exp": False, "verify_iat": False, "verify_nbf": False,
                "verify_iss": False, "verify_sub": False, "verify_jti": False,
            },
        )
    except JWTError:
        raise _bad_token()
    if any(k not in claims for k in _CALL_CLAIMS):
        raise _bad_token()
    if claims["aud"] != CALL_AUDIENCE:
        raise ConnectorError(401, "wrong_audience", "The token was not issued for this API.")
    if claims["typ"] != CALL_TYP or claims["iss"] != SS_ISSUER or not _same(claims["connection_id"], connection_id):
        raise _bad_token()
    if not all(_is_int(claims[k]) for k in ("iat", "exp", "consented_at")):
        raise _bad_token()
    if not all(isinstance(claims[k], str) for k in ("sub", "grant_id", "tool", "req_hash", "jti")) or not claims["jti"]:
        raise _bad_token()
    now = time.time()
    if claims["exp"] < now - CLOCK_SKEW:
        raise ConnectorError(401, "token_expired", "The connector token has expired.")
    if not 0 < claims["exp"] - claims["iat"] <= CALL_MAX_LIFETIME or claims["iat"] > now + CLOCK_SKEW:
        raise _bad_token()
    return claims


def check_request_hash(claims: dict, *, method: str, raw_path: str, query: str, body: bytes) -> None:
    if not _same(claims["req_hash"], request_hash(method, raw_path, query, body)):
        raise _bad_token("The token does not match this request.")


def claim_jti(jti: str) -> None:
    """Remember a call jti for REPLAY_TTL seconds; a second use is ``replayed``. Fails closed."""
    try:
        from app.services.queue_service import redis_conn

        first = redis_conn.set(f"connector:call-jti:{hashlib.sha256(jti.encode()).hexdigest()}", "1",
                               nx=True, ex=REPLAY_TTL)
    except Exception:  # noqa: BLE001 - no replay cache, no call
        raise ConnectorError(503, "upstream_error", "The connector cannot check this call right now.")
    if not first:
        raise ConnectorError(401, "replayed", "This connector token was already used.")


# ------------------------------------------------------------------ routes and users


@lru_cache(maxsize=1)
def _tool_bindings() -> dict[str, tuple[str, str, frozenset[str]]]:
    """tool name -> (METHOD, path_template, the query keys that tool may send), from the manifest."""
    from app.services.analytics_connector.manifest import build_manifest

    return {
        t["name"]: (t["http"]["method"].upper(), t["http"]["path_template"], frozenset(t["http"]["query_params"]))
        for t in build_manifest()["tools"]
    }


def check_tool_binding(tool: str, method: str, route_path: Optional[str], query: str) -> None:
    """``claims.tool`` names the manifest tool being called (the manifest route itself: ``""``), and
    the query sends only that tool's own params (section 9.2). Refusals write no audit row."""
    if route_path == MANIFEST_PATH:
        allowed: frozenset[str] = frozenset()
        if tool != "":
            raise ConnectorError(403, "route_not_allowed", "The manifest is read with no tool.")
    else:
        binding = _tool_bindings().get(tool)
        if binding is None or binding[:2] != (method.upper(), route_path):
            raise ConnectorError(403, "route_not_allowed", "This token is not for this tool's route.")
        allowed = binding[2]
    if any(k not in allowed for k, _ in parse_qsl(query, keep_blank_values=True)):
        raise ConnectorError(403, "param_not_allowed", "The call sends a parameter this tool does not take.")


def route_allowed(method: str, route_path: Optional[str], raw_path: str) -> bool:
    """Whether a connector call may reach this resolved route (exact list, no encoded walk-outs)."""
    low = raw_path.lower()
    if any(esc in low for esc in _SUSPECT_ESCAPES) or "\\" in raw_path or "//" in raw_path:
        return False
    if any(seg in (".", "..") for seg in raw_path.split("/")):
        return False
    return (method.upper(), route_path) in CONNECTOR_ROUTES


def has_connector_permission(db: Session, user_id: str, slug: str = USE) -> bool:
    """Whether a role the user holds grants ``slug``. Deliberately NOT
    ``check_user_has_permission``: no admin bypass (Q1) and no 30 s cache."""
    from app.models.user import UserPermission, UserRoleAssignment, UserRolePermission

    hit = (
        db.query(UserPermission.id)
        .join(UserRolePermission, UserRolePermission.permission_id == UserPermission.id)
        .join(UserRoleAssignment, UserRoleAssignment.role_id == UserRolePermission.role_id)
        .filter(UserRoleAssignment.user_id == user_id, UserPermission.slug == slug)
        .first()
    )
    return hit is not None


def connector_user(db: Session, claims: dict, *, chat: bool) -> dict:
    """The user a call acts as, after the section 3 checks (403 codes from section 5)."""
    from app.dependencies import _load_user_and_contact
    from app.models.user import User
    from app.services.user_service import UserPermissionService

    sub = claims["sub"]
    if not sub:
        raise _bad_token("The connector token names no user.")
    user, _contact = _load_user_and_contact(db, sub)
    if user is None:
        raise ConnectorError(403, "connector_access_removed", "This user no longer exists.")
    if str(user.get("status") or "") != "ACTIVE":
        raise ConnectorError(403, "user_inactive", "This user's account is not active.")
    if not has_connector_permission(db, sub, USE):
        raise ConnectorError(403, "connector_access_removed", "This user may no longer use the Claude connector.")
    revoked = db.query(User.connector_revoked_at).filter(User.id == sub).scalar()
    if revoked is not None and claims["consented_at"] < _epoch(revoked):
        raise ConnectorError(403, "reconsent_required", "The user signed out everywhere; connect Claude again.")
    if chat and not UserPermissionService(db).check_user_has_permission(sub, CHAT_VIEW):
        raise ConnectorError(403, "chat_history_not_permitted", "This user may not read chat history.")
    return {**user, "auth_method": "connector"}


def _epoch(naive_utc) -> float:
    from datetime import timezone

    return naive_utc.replace(tzinfo=timezone.utc).timestamp()


# ------------------------------------------------------------------ the gate


@dataclass
class ConnectorCall:
    claims: dict
    user: Optional[dict]  # None only on the manifest


def connector_call(request: Request) -> Optional[ConnectorCall]:
    return getattr(request.state, "connector_call", None)


def _translate(exc: Exception) -> Optional[ConnectorError]:
    """A route's own refusal in the section 5 shape, so SS can tell it from a grant-ending one."""
    if isinstance(exc, ConnectorError):
        return exc
    if isinstance(exc, RequestValidationError):
        return ConnectorError(422, "validation_error", "The tool arguments are not valid.")
    if not isinstance(exc, HTTPException):
        return None
    code = exc.status_code
    if code == 403:
        return ConnectorError(403, "permission_required", "This user lacks the permission this tool needs.")
    if code == 404:
        return ConnectorError(404, "not_found", "Nothing was found.")
    if code in (400, 422):
        return ConnectorError(422, "validation_error", "The tool arguments are not valid.")
    if code == 429:
        return ConnectorError(429, "rate_limited", "Too many requests; try again later.")
    if code >= 500:
        return ConnectorError(code, "upstream_error", "The CRM could not answer; try again later.")
    return None


def record_tool_call(db: Session, call: ConnectorCall, *, status: Any, started: float) -> None:
    """One ``audit_logs`` EVENT per served call (contract section 4), actor = the call's user."""
    from app.services import audit_service

    grant_id = str(call.claims.get("grant_id") or "")
    audit_service.record(
        db,
        event="connector.tool_call",
        entity_type="connector_grant",
        entity_id=grant_id[:100] or "unknown",
        new_values={
            "tool": str(call.claims.get("tool") or "")[:100],
            "grant_id": grant_id[:100],
            "status": status,
            "ms": int((time.perf_counter() - started) * 1000),
        },
    )
    db.commit()


async def connector_call_gate(request: Request, db: Session = Depends(get_db)) -> AsyncIterator[None]:
    """Router dependency on every ``/api/v1`` route. A no-op unless ``Authorization: Connector``."""
    auth = request.headers.get("Authorization") or ""
    scheme, _, token = auth.partition(" ")
    if scheme.lower() != "connector":
        yield
        return
    started = time.perf_counter()
    route = request.scope.get("route")
    raw_path = (request.scope.get("raw_path") or request.url.path.encode()).decode("latin-1").split("?", 1)[0]
    query = (request.scope.get("query_string") or b"").decode("latin-1")
    # Verified before any body is read: by ConnectorPreflightMiddleware (which also caps the body),
    # or here when a caller reached the route without it.
    claims = getattr(request.state, "connector_claims", None) or verify_token(token.strip())
    check_request_hash(claims, method=request.method, raw_path=raw_path, query=query, body=await request.body())
    claim_jti(claims["jti"])
    route_path = getattr(route, "path", None)
    if not route_allowed(request.method, route_path, raw_path):
        raise ConnectorError(403, "route_not_allowed", "The connector cannot call this route.")
    check_tool_binding(claims["tool"], request.method, route_path, query)
    if route_path == MANIFEST_PATH:
        request.state.connector_call = ConnectorCall(claims=claims, user=None)
        yield
        return
    user = connector_user(db, claims, chat=(request.method.upper(), route_path) in CHAT_ROUTES)
    call = ConnectorCall(claims=claims, user=user)
    request.state.connector_call = call
    request.state.real_user = user
    from app.audit_context import AuditActor, stamp_actor

    stamp_actor(
        AuditActor(
            actor_type="user",
            user_id=str(user["id"]),
            real_user_id=str(user["id"]),
            auth_method="connector",
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        ),
        db=db,
        request=request,
    )
    try:
        yield
    except Exception as exc:
        translated = _translate(exc)
        db.rollback()
        # The status the caller receives: the translated one, the route's own, or 500.
        sent = translated or exc
        record_tool_call(db, call, status=sent.status_code if isinstance(sent, HTTPException) else 500,
                         started=started)
        if translated is not None and translated is not exc:
            raise translated from exc
        raise
    record_tool_call(db, call, status="ok", started=started)
