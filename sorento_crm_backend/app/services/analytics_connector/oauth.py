"""The OAuth 2.1 authorization server behind the claude.ai connector (card section 3).

Public client only (PKCE S256, no secret). Codes and refresh tokens are stored as SHA-256 hashes.
The access token is a ``user_sessions`` row with ``auth_method='claude_connector'``.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlencode, urlsplit

from sqlalchemy.orm import Session

from app.audit_context import AuditActor, actor_scope
from app.config import settings
from app.models.analytics_connector import (
    AnalyticsConnectorGrant,
    AnalyticsOAuthClient,
    AnalyticsOAuthRequest,
)
from app.models.user import User
from app.services import audit_service
from app.services.analytics_connector.access import USE, has_connector_permission
from app.services.user_session_service import mint_session, revoke_session

ALLOWED_REDIRECT_URIS = frozenset({"https://claude.ai/api/mcp/auth_callback"})
SCOPE = "analytics.read"
REQUEST_TTL = timedelta(minutes=10)
CODE_TTL = timedelta(seconds=60)
ACCESS_TTL = timedelta(hours=1)
GRANT_TTL = timedelta(days=90)
GRANT_ENTITY = "analytics_connector_grant"


class OAuthError(Exception):
    """An RFC 6749 error. ``redirect_to`` is set only when the redirect URI was verified."""

    def __init__(self, error: str, description: str = "", status_code: int = 400, redirect_to: Optional[str] = None):
        self.error = error
        self.description = description
        self.status_code = status_code
        self.redirect_to = redirect_to
        super().__init__(error)

    def body(self) -> dict:
        out = {"error": self.error}
        if self.description:
            out["error_description"] = self.description
        if self.redirect_to:
            out["redirect_to"] = self.redirect_to
        return out


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def public_base() -> str:
    return (settings.analytics_connector_public_base_url or "").rstrip("/")


def resource_url() -> str:
    return f"{public_base()}/mcp"


def _with_query(url: str, params: dict) -> str:
    return f"{url}{'&' if '?' in url else '?'}{urlencode({k: v for k, v in params.items() if v is not None})}"


# ------------------------------------------------------------------ registration


def register_client(db: Session, body: dict) -> dict:
    uris = body.get("redirect_uris")
    if not isinstance(uris, list) or not uris or not all(isinstance(u, str) for u in uris):
        raise OAuthError("invalid_redirect_uri", "redirect_uris is required")
    if any(u not in ALLOWED_REDIRECT_URIS for u in uris):
        raise OAuthError("invalid_redirect_uri", "redirect_uri is not allowed")
    if body.get("token_endpoint_auth_method", "none") != "none":
        raise OAuthError("invalid_client_metadata", "only public clients are supported")
    name = body.get("client_name")
    client = AnalyticsOAuthClient(
        id=f"claude-{secrets.token_urlsafe(18)}",
        client_name=str(name)[:200] if isinstance(name, str) else None,
        redirect_uris=list(uris),
    )
    db.add(client)
    db.commit()
    return {
        "client_id": client.id,
        "client_name": client.client_name,
        "redirect_uris": client.redirect_uris,
        "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
    }


# ------------------------------------------------------------------ authorize


def start_authorize(db: Session, p: dict) -> dict:
    client = db.get(AnalyticsOAuthClient, str(p.get("client_id") or "")[:64])
    if client is None:
        raise OAuthError("invalid_client", "unknown client")
    redirect_uri = p.get("redirect_uri") or ""
    if redirect_uri not in (client.redirect_uris or []):
        raise OAuthError("invalid_redirect_uri", "redirect_uri is not registered")
    state = p.get("state")

    def back(error: str, description: str) -> OAuthError:
        return OAuthError(
            error, description, redirect_to=_with_query(redirect_uri, {"error": error, "state": state, "iss": public_base()})
        )

    if p.get("response_type") != "code":
        raise back("unsupported_response_type", "response_type must be code")
    challenge = p.get("code_challenge") or ""
    if p.get("code_challenge_method") != "S256" or not challenge:
        raise back("invalid_request", "PKCE with S256 is required")
    scope = p.get("scope") or SCOPE
    if scope != SCOPE:
        raise back("invalid_scope", "unsupported scope")
    resource = p.get("resource") or resource_url()
    if resource != resource_url():
        raise back("invalid_target", "unknown resource")
    row = AnalyticsOAuthRequest(
        id=str(uuid.uuid4()),
        client_id=client.id,
        redirect_uri=redirect_uri,
        code_challenge=challenge[:128],
        state=state,
        scope=scope,
        resource=resource,
        status="pending",
        expires_at=_now() + REQUEST_TTL,
    )
    db.add(row)
    db.commit()
    return {"request_id": row.id}


def load_pending_request(db: Session, request_id: str) -> AnalyticsOAuthRequest:
    row = db.get(AnalyticsOAuthRequest, request_id[:64])
    if row is None:
        raise OAuthError("not_found", "unknown request", 404)
    if row.status != "pending":
        raise OAuthError("invalid_request", "request already answered", 409)
    if row.expires_at < _now():
        raise OAuthError("expired", "request expired", 410)
    return row


def consent_details(db: Session, request_id: str, user: dict) -> dict:
    row = load_pending_request(db, request_id)
    client = db.get(AnalyticsOAuthClient, row.client_id)
    return {
        "request_id": row.id,
        "client_name": (client.client_name if client else None) or "Claude",
        "redirect_host": urlsplit(row.redirect_uri).hostname,
        "user_name": user.get("name"),
        "scope": row.scope,
        "expires_at": row.expires_at.isoformat(),
        "allowed": has_connector_permission(db, str(user["id"]), USE),
    }


def approve(db: Session, request_id: str, user: dict) -> dict:
    row = load_pending_request(db, request_id)
    code = secrets.token_urlsafe(32)
    row.status = "approved"
    row.user_id = str(user["id"])
    row.code_hash = sha256_hex(code)
    row.code_expires_at = _now() + CODE_TTL
    db.commit()
    return {
        "redirect_to": _with_query(row.redirect_uri, {"code": code, "state": row.state, "iss": public_base()})
    }


def deny(db: Session, request_id: str) -> dict:
    row = load_pending_request(db, request_id)
    row.status = "denied"
    db.commit()
    return {
        "redirect_to": _with_query(
            row.redirect_uri, {"error": "access_denied", "state": row.state, "iss": public_base()}
        )
    }


# ------------------------------------------------------------------ token


def _pkce_ok(verifier: str, challenge: str) -> bool:
    digest = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return hmac.compare_digest(digest, challenge)


def _still_allowed(db: Session, user_id: str) -> bool:
    user = db.get(User, user_id)
    return bool(user and not user.is_trashed and user.status == "ACTIVE" and has_connector_permission(db, user_id, USE))


def _issue_access(db: Session, grant: AnalyticsConnectorGrant) -> str:
    if grant.current_session_id:
        revoke_session(db, session_id=grant.current_session_id)
    session = mint_session(db, grant.user_id, auth_method="claude_connector", ttl=ACCESS_TTL, rolling=False)
    grant.current_session_id = str(session.id)
    grant.last_used_at = _now()
    return session.token


def _token_response(access_token: str, refresh_token: str) -> dict:
    return {
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": int(ACCESS_TTL.total_seconds()),
        "refresh_token": refresh_token,
        "scope": SCOPE,
    }


def _audit(db: Session, grant: AnalyticsConnectorGrant, event: str, new_values: Optional[dict] = None) -> None:
    actor = AuditActor(
        actor_type="user",
        user_id=grant.user_id,
        real_user_id=grant.user_id,
        auth_method="claude_connector",
        session_id=grant.current_session_id,
    )
    with actor_scope(actor, db=db):
        audit_service.record(
            db, event=f"analytics_connector.{event}", entity_type=GRANT_ENTITY, entity_id=grant.id,
            new_values=new_values or {"client_id": grant.client_id},
        )


def exchange_code(db: Session, form: dict) -> dict:
    bad = OAuthError("invalid_grant", "code is invalid or expired")
    code = form.get("code") or ""
    row = (
        db.query(AnalyticsOAuthRequest)
        .filter(AnalyticsOAuthRequest.code_hash == sha256_hex(code), AnalyticsOAuthRequest.status == "approved")
        .first()
        if code
        else None
    )
    if row is None:
        raise bad
    row.status = "used"  # single use, whatever happens next
    db.commit()
    resource = form.get("resource")
    if (
        row.code_expires_at is None
        or row.code_expires_at < _now()
        or form.get("client_id") != row.client_id
        or form.get("redirect_uri") != row.redirect_uri
        or (resource and resource != row.resource)
        or not _pkce_ok(form.get("code_verifier") or "", row.code_challenge)
        or not row.user_id
        or not _still_allowed(db, row.user_id)
    ):
        raise bad
    refresh = secrets.token_urlsafe(48)
    grant = AnalyticsConnectorGrant(
        id=str(uuid.uuid4()),
        user_id=row.user_id,
        client_id=row.client_id,
        refresh_token_hash=sha256_hex(refresh),
        expires_at=_now() + GRANT_TTL,
    )
    db.add(grant)
    db.flush()
    access = _issue_access(db, grant)
    _audit(db, grant, "grant_created")
    db.commit()
    return _token_response(access, refresh)


def refresh_tokens(db: Session, form: dict) -> dict:
    bad = OAuthError("invalid_grant", "refresh token is invalid or expired")
    token = form.get("refresh_token") or ""
    if not token:
        raise bad
    digest = sha256_hex(token)
    grant = db.query(AnalyticsConnectorGrant).filter(AnalyticsConnectorGrant.refresh_token_hash == digest).first()
    if grant is None:
        stale = db.query(AnalyticsConnectorGrant).filter(AnalyticsConnectorGrant.previous_refresh_hash == digest).first()
        if stale is not None and stale.client_id == form.get("client_id") and stale.revoked_at is None:
            # A rotated-out token came back: assume theft and end the grant (OAuth 2.1).
            revoke_grant(db, stale, "refresh_reuse_detected")
        raise bad
    if (
        grant.revoked_at is not None
        or grant.expires_at < _now()
        or grant.client_id != form.get("client_id")
    ):
        raise bad
    if not _still_allowed(db, grant.user_id):
        revoke_grant(db, grant, "revoked", {"reason": "access removed"})
        raise bad
    new_refresh = secrets.token_urlsafe(48)
    grant.previous_refresh_hash = grant.refresh_token_hash
    grant.refresh_token_hash = sha256_hex(new_refresh)
    access = _issue_access(db, grant)
    _audit(db, grant, "refreshed")
    db.commit()
    return _token_response(access, new_refresh)


def revoke_grant(db: Session, grant: AnalyticsConnectorGrant, event: str = "revoked", new_values: Optional[dict] = None) -> None:
    """End a grant and its live access token at once."""
    grant.revoked_at = _now()
    if grant.current_session_id:
        revoke_session(db, session_id=grant.current_session_id)
    _audit(db, grant, event, new_values)
    db.commit()
