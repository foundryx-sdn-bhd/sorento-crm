"""Staff session lifecycle - mint, resolve (with sliding extension), revoke.

Mirrors the contact portal's device-trust model (``portal_service.resolve_token``)
for internal CRM users. The opaque token is the credential; the DB row is the
source of truth for validity, so revocation and expiry are instant.

Lifetime (see PLAN-staff-rolling-sessions-fastapi-auth.md): every sign-in, email or
phone, mints a 30-day window re-extended on use, write throttled to ~once/day via the
29-day threshold. No remember-me choice and no absolute cap, exactly like the portal.
Rows minted before that rule carry ``rolling=False`` (fixed 8h) and simply lapse.

``last_seen_at`` is updated on a separate ~10-minute throttle so the "your devices"
list stays fresh without a write on every request.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.models.user_session import UserSession

REMEMBER_TTL = timedelta(days=30)
SLIDE_THRESHOLD = timedelta(days=29)  # throttle the rolling write to ~once/active-day
LAST_SEEN_THROTTLE = timedelta(minutes=10)

# Reason codes surfaced to the FE so the api-client 401 interceptor can decide to
# sign the user out (vs. an RBAC 403 or an incidental error).
REASON_INVALID = "session_invalid"
REASON_REVOKED = "session_revoked"
REASON_EXPIRED = "session_expired"

# How a session was minted (identity S0, AC-07). Mirrors `ck_user_sessions_auth_method`.
AUTH_METHODS = ("password", "phone_otp", "portal_link", "impersonation", "dev_login")


class SessionAuthError(Exception):
    """Raised by resolve_session; carries a reason code for the 401 body."""

    def __init__(self, reason: str, detail: str):
        self.reason = reason
        self.detail = detail
        super().__init__(detail)


def _utcnow() -> datetime:
    """Naive UTC - matches the timezone=False columns (same as portal_service)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def mint_session(
    db: Session,
    user_id: str,
    *,
    user_agent: Optional[str] = None,
    ip_address: Optional[str] = None,
    auth_method: str = "password",
) -> UserSession:
    """Create a new session row and return it. Caller commits via this function.

    ``auth_method`` records how the person signed in; the audit actor of every
    request on this session carries it. Raises ValueError for an unknown method.
    """
    if auth_method not in AUTH_METHODS:
        raise ValueError(f"Unknown session auth_method: {auth_method!r}")
    now = _utcnow()
    row = UserSession(
        token=secrets.token_urlsafe(48),
        user_id=user_id,
        rolling=True,
        expires_at=now + REMEMBER_TTL,
        last_seen_at=now,
        user_agent=(user_agent or None),
        ip_address=(ip_address or None),
        auth_method=auth_method,
        # Explicit, not the column's `server_default=func.now()`: Postgres's
        # `now()` is pinned to the TRANSACTION's start, so two sessions minted
        # a moment apart inside one transaction (a test, an impersonation
        # immediately after login) would otherwise share one `created_at` and
        # make "the newest session" (S3 1.7 `last_sign_in_method`) a coin
        # flip. Python's clock has real per-call resolution.
        created_at=now,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def resolve_session(
    db: Session,
    token: str,
    *,
    ip_address: Optional[str] = None,
) -> UserSession:
    """Validate a token and return its row, sliding the rolling window on use.

    Raises SessionAuthError(reason) for missing / revoked / expired tokens.
    """
    row: Optional[UserSession] = (
        db.query(UserSession).filter(UserSession.token == token).first()
    )
    if row is None:
        raise SessionAuthError(REASON_INVALID, "Session not found")
    if row.revoked_at is not None:
        raise SessionAuthError(REASON_REVOKED, "Session was revoked")

    now = _utcnow()
    if row.expires_at < now:
        raise SessionAuthError(REASON_EXPIRED, "Session expired")

    dirty = False
    # Sliding device trust: rolling sessions re-extend to now+30d on use, throttled.
    if row.rolling and row.expires_at < now + SLIDE_THRESHOLD:
        row.expires_at = now + REMEMBER_TTL
        dirty = True
    # Refresh last_seen on a shorter throttle so the device list isn't a day stale.
    if row.last_seen_at is None or row.last_seen_at < now - LAST_SEEN_THROTTLE:
        row.last_seen_at = now
        if ip_address and row.ip_address != ip_address:
            row.ip_address = ip_address
        dirty = True
    if dirty:
        db.commit()
    return row


def revoke_session(db: Session, *, session_id: Optional[str] = None, token: Optional[str] = None) -> bool:
    """Revoke a single session by id or token. Returns True if a row was revoked."""
    q = db.query(UserSession).filter(UserSession.revoked_at.is_(None))
    if session_id is not None:
        q = q.filter(UserSession.id == session_id)
    elif token is not None:
        q = q.filter(UserSession.token == token)
    else:
        return False
    row = q.first()
    if row is None:
        return False
    row.revoked_at = _utcnow()
    db.commit()
    return True


def revoke_all_for_user(
    db: Session,
    user_id: str,
    *,
    except_session_id: Optional[str] = None,
) -> int:
    """Revoke every active session for a user (optionally keeping the current one).

    Used by: password change/reset, admin force-logout, block/disable, and the
    self-service "log out all other devices". Returns the count revoked.
    """
    q = db.query(UserSession).filter(
        UserSession.user_id == user_id,
        UserSession.revoked_at.is_(None),
    )
    if except_session_id is not None:
        q = q.filter(UserSession.id != except_session_id)
    now = _utcnow()
    count = q.update({UserSession.revoked_at: now}, synchronize_session=False)
    db.commit()
    return count


def device_label_from_ua(user_agent: Optional[str]) -> str:
    """Best-effort human label from a User-Agent (browser on OS). Never expose raw UA in UI."""
    ua = (user_agent or "").lower()
    if not ua:
        return "Unknown device"
    if "edg/" in ua or "edge" in ua:
        browser = "Edge"
    elif "chrome" in ua and "chromium" not in ua:
        browser = "Chrome"
    elif "firefox" in ua:
        browser = "Firefox"
    elif "safari" in ua:
        browser = "Safari"
    else:
        browser = "Browser"
    if "iphone" in ua:
        os_name = "iPhone"
    elif "ipad" in ua:
        os_name = "iPad"
    elif "android" in ua:
        os_name = "Android"
    elif "windows" in ua:
        os_name = "Windows"
    elif "mac os" in ua or "macintosh" in ua:
        os_name = "macOS"
    elif "linux" in ua:
        os_name = "Linux"
    else:
        os_name = "Unknown OS"
    return f"{browser} on {os_name}"


def list_active_sessions(db: Session, user_id: str) -> list[UserSession]:
    """Active (non-revoked, non-expired) sessions for the devices UI, newest first."""
    now = _utcnow()
    return (
        db.query(UserSession)
        .filter(
            UserSession.user_id == user_id,
            UserSession.revoked_at.is_(None),
            UserSession.expires_at >= now,
        )
        .order_by(UserSession.last_seen_at.desc().nullslast())
        .all()
    )
