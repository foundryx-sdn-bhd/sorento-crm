"""Shared dependencies for FastAPI routes."""
import time
from fastapi import Depends, HTTPException, status, Request, Header
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from jose import JWTError, jwt
from typing import Optional, List
from app.database import get_db
from app.config import settings
from app.services.user_service import UserPermissionService
from app.middleware.impersonation_ended_middleware import IMPERSONATION_ENDED_STATE
from app.services.user_session_service import (
    resolve_session,
    SessionAuthError,
    REASON_INVALID,
    REASON_REVOKED,
)

# OAuth2 scheme for JWT token extraction
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token", auto_error=False)

IMPERSONATE_HEADER = "X-Impersonate-User-Id"


def _decode_jwt_user(token: str) -> dict:
    """Decode a NextAuth-issued JWT into our standard user dict. Raises HTTPException on failure."""
    payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    user_id_raw = payload.get("sub") or payload.get("id")
    user_id: Optional[str] = str(user_id_raw) if user_id_raw is not None else None
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token: missing user ID",
        )
    email_raw = payload.get("email")
    role_raw = payload.get("roleId")
    return {
        "id": user_id,
        "email": str(email_raw) if email_raw is not None else None,
        "role_id": str(role_raw) if role_raw is not None else None,
        "name": payload.get("name"),
        "avatar": payload.get("avatar"),
        "status": payload.get("status"),
        "role_name": payload.get("roleName"),
    }


def _load_user_dict_from_db(db: Session, user_id: str) -> Optional[dict]:
    """Load a user dict (same shape as JWT-derived) from DB, including primary role slug."""
    return _load_user_and_contact(db, user_id)[0]


def _load_user_and_contact(db: Session, user_id: str) -> tuple[Optional[dict], Optional[str]]:
    """The user dict plus the user's linked WhatsApp contact id (for the audit actor)."""
    from app.models.user import User, UserRoleAssignment, UserRole

    row = db.query(User).filter(User.id == user_id, User.is_trashed.is_(False)).first()
    if not row:
        return None, None
    role_slug = (
        db.query(UserRole.slug)
        .join(UserRoleAssignment, UserRoleAssignment.role_id == UserRole.id)
        .filter(UserRoleAssignment.user_id == user_id)
        .order_by(UserRoleAssignment.assigned_at.asc())
        .first()
    )
    role_id = (
        db.query(UserRoleAssignment.role_id)
        .filter(UserRoleAssignment.user_id == user_id)
        .order_by(UserRoleAssignment.assigned_at.asc())
        .first()
    )
    return {
        "id": row.id,
        "email": row.email,
        "role_id": role_id[0] if role_id else None,
        "name": row.name,
        "avatar": row.avatar,
        "status": row.status,
        "role_name": role_slug[0] if role_slug else None,
    }, row.respond_contact_id


def _client_ip(request: Request) -> Optional[str]:
    return request.client.host if request.client else None


def _stamp_session_actor(request: Request, db: Session, real_user: dict) -> None:
    """Stamp a staff Bearer session as the audit actor (identity S0, plan 8.1)."""
    from app.audit_context import AuditActor, stamp_actor

    stamp_actor(
        AuditActor(
            actor_type="user",
            user_id=str(real_user["id"]),
            real_user_id=str(real_user["id"]),
            auth_method=getattr(request.state, "session_auth_method", None) or "password",
            session_id=getattr(request.state, "session_id", None),
            contact_id=getattr(request.state, "session_user_contact_id", None),
            ip_address=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        ),
        db=db,
        request=request,
    )


def _clean_tool_name(raw: Optional[str]) -> Optional[str]:
    """X-Tool-Name as it may land in an audit description: control characters
    (below 0x20, and 0x7f) stripped first, then capped at 128."""
    if not raw:
        return None
    cleaned = "".join(ch for ch in raw if ord(ch) >= 0x20 and ord(ch) != 0x7F).strip()
    return cleaned[:128] or None


def _stamp_integration_actor(request: Request, db: Session, user: dict) -> None:
    """Stamp an integration API key as the audit actor (identity S0, AC-09).

    Also records the request's channel (n8n, mcp, chatbot, external_api) and trusts the
    integration's X-Correlation-Id for the business action (#1281 S0).
    """
    from app.audit_context import AuditActor, mark_integration_request, stamp_actor

    mark_integration_request(user.get("integration_type"), request.url.path)

    stamp_actor(
        AuditActor(
            actor_type="integration",
            user_id=str(user["id"]),
            real_user_id=str(user["id"]),
            auth_method="api_key",
            integration_id=user.get("integration_id"),
            tool_name=_clean_tool_name(request.headers.get("X-Tool-Name")),
            ip_address=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        ),
        db=db,
        request=request,
    )


def _resolve_session_to_user(token: str, request: Request, db: Session) -> dict:
    """Resolve an opaque staff session token to a user dict, sliding the session.

    Replaces the old stateless ``_decode_jwt_user``: the DB row is the source of
    truth, so revocation/expiry are instant and role/status changes apply on the
    next request. Raises HTTPException(401, {code, message}) with a specific reason
    code so the FE api-client can sign the user out on session_revoked/expired/invalid
    (but not on RBAC 403s or incidental errors).
    """
    ip = request.client.host if request.client else None
    try:
        session_row = resolve_session(db, token, ip_address=ip)
    except SessionAuthError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": e.reason, "message": e.detail},
            headers={"WWW-Authenticate": "Bearer"},
        )
    user, contact_id = _load_user_and_contact(db, str(session_row.user_id))
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": REASON_INVALID, "message": "User not found"},
        )
    if str(user.get("status") or "") != "ACTIVE":
        # Blocked/disabled mid-session → boot immediately (no need to wait for revoke).
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": REASON_REVOKED, "message": "Account is not active"},
        )
    # Expose the current session for "this device" / revoke-others / logout.
    request.state.session_id = str(session_row.id)
    request.state.session_token = token
    # Read by the audit actor stamp (identity S0, AC-07 / AC-08).
    request.state.session_auth_method = getattr(session_row, "auth_method", None) or "password"
    request.state.session_user_contact_id = contact_id
    return user


def _maybe_apply_impersonation(
    request: Request,
    db: Session,
    real_user: dict,
) -> dict:
    """If real user is admin/superadmin AND active session matches header, swap to target user dict.

    Stash the real user on ``request.state.real_user`` regardless. A stale or invalid header
    is ignored - admin browses as themselves - and marked on ``request.state`` so
    ``ImpersonationEndedMiddleware`` tells the client its view-as is over.
    """
    request.state.real_user = real_user
    target_id = request.headers.get(IMPERSONATE_HEADER)
    if not target_id:
        return real_user

    def _ignored() -> dict:
        setattr(request.state, IMPERSONATION_ENDED_STATE, True)
        return real_user

    role_slugs = UserPermissionService(db).get_user_role_slugs(real_user["id"])
    if not (role_slugs & {UserPermissionService.SUPERADMIN_ROLE_SLUG, "admin"}):
        return _ignored()
    from app.models.impersonation import ImpersonationSession

    session_row = (
        db.query(ImpersonationSession)
        .filter(
            ImpersonationSession.admin_user_id == real_user["id"],
            ImpersonationSession.target_user_id == target_id,
            ImpersonationSession.ended_at.is_(None),
        )
        .first()
    )
    if not session_row:
        return _ignored()
    target_user = _load_user_dict_from_db(db, target_id)
    if not target_user or target_user.get("status") != "ACTIVE":
        return _ignored()
    request.state.impersonation_session_id = session_row.id
    # The target is the effective actor, the admin is at the keyboard (plan 8.1/8.2).
    from app.audit_context import AuditActor, stamp_actor

    stamp_actor(
        AuditActor(
            actor_type="user",
            user_id=str(target_user["id"]),
            real_user_id=str(real_user["id"]),
            auth_method="impersonation",
            session_id=getattr(request.state, "session_id", None),
            ip_address=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        ),
        db=db,
        request=request,
    )
    return target_user


def get_actor_user_id(request: Request, current_user: dict) -> str:
    """Return the *real* user id for audit / created_by / updated_by purposes.

    During impersonation ``current_user`` is the effective (target) user; the real admin
    is on ``request.state.real_user``. Outside impersonation both are the same.
    """
    real = getattr(request.state, "real_user", None)
    if isinstance(real, dict) and real.get("id"):
        return str(real["id"])
    return str(current_user["id"])


def extract_token_from_request(request: Request) -> Optional[str]:
    """Extract JWT token from Authorization header or cookies."""
    # Try Authorization header first
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        return auth_header.split(" ")[1]
    
    # Try cookies (NextAuth stores encrypted session in cookies, not raw JWT)
    # For now, we'll need to decode the session cookie or use a different approach
    # NextAuth cookie names: 'next-auth.session-token' or '__Secure-next-auth.session-token'
    # Note: NextAuth session cookies are encrypted, so we can't directly extract JWT
    # The frontend should send JWT in Authorization header instead
    # For cookie-based auth, we'll need to implement session validation differently
    return None


async def get_current_user(
    request: Request,
    token: Optional[str] = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> dict:
    """
    Validate JWT token from NextAuth and return user information.
    
    This dependency extracts and validates the JWT token created by NextAuth.
    The token can be sent in:
    1. Authorization header as: Bearer <token>
    2. Cookies (next-auth.session-token or __Secure-next-auth.session-token)
    """
    import logging
    logger = logging.getLogger(__name__)
    
    # Try to get token from request if not in header
    if not token:
        token = extract_token_from_request(request)
    
    # Debug logging
    auth_header = request.headers.get("Authorization")
    logger.debug(f"Authorization header present: {auth_header is not None}")
    logger.debug(f"Token extracted: {token is not None}")
    if token:
        logger.debug(f"Token length: {len(token)}, first 20 chars: {token[:20]}...")
    
    if not token:
        # Do NOT log request headers - they carry Authorization / X-API-Key / cookies.
        logger.warning("No authentication token found in request")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    try:
        real_user = _resolve_session_to_user(token, request, db)
        _stamp_session_actor(request, db, real_user)
        effective_user = _maybe_apply_impersonation(request, db, real_user)
        return effective_user
    except HTTPException:
        raise
    except JWTError as e:
        # Log the error for debugging
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"JWT validation failed: {str(e)}")
        # Never log token bytes (even a prefix) - log only length for debugging.
        logger.error(f"Token length: {len(token) if token else 0}")
        logger.error(f"JWT Secret configured: {bool(settings.jwt_secret)}")
        logger.error(f"JWT Secret length: {len(settings.jwt_secret) if settings.jwt_secret else 0}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {str(e)}"
        )
    except Exception as e:
        # Catch any other errors
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Unexpected error in JWT validation: {str(e)}")
        logger.error(f"Error type: {type(e).__name__}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Authentication error: {str(e)}"
        )


async def get_current_user_optional(
    request: Request,
    token: Optional[str] = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> Optional[dict]:
    """
    Same as get_current_user but returns None instead of raising.
    Use for read-only endpoints where we want to avoid 500 on auth issues.
    """
    if not token:
        token = extract_token_from_request(request)
    if not token:
        return None
    try:
        real_user = _resolve_session_to_user(token, request, db)
    except Exception:
        return None
    try:
        _stamp_session_actor(request, db, real_user)
        return _maybe_apply_impersonation(request, db, real_user)
    except Exception:
        return real_user


def get_api_key(
    x_api_key: Optional[str] = Header(None, alias="X-API-Key")
) -> Optional[str]:
    """Extract API key from X-API-Key header."""
    return x_api_key


async def get_real_user(
    request: Request,
    token: Optional[str] = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> dict:
    """Like get_current_user but always returns the session-resolved user, ignoring any
    X-Impersonate-User-Id header. Use for impersonation start/stop endpoints so
    an impersonated session cannot end its own impersonation.
    """
    if not token:
        token = extract_token_from_request(request)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    real_user = _resolve_session_to_user(token, request, db)
    _stamp_session_actor(request, db, real_user)
    request.state.real_user = real_user
    return real_user


def require_permission(permission_slug: str):
    """
    Dependency that requires the current user to have the given permission (or superadmin/admin role).
    Deny by default; raises 403 if the user lacks the permission.
    When MODULE_GUARD_STRICT is on, also requires the mapped installable module to be enabled (superadmin/admin bypass).
    """

    def _require(current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
        service = UserPermissionService(db)
        if not service.check_user_has_permission(current_user["id"], permission_slug):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission required: {permission_slug}",
            )
        if getattr(settings, "module_guard_strict", False):
            from app.modules.runtime.installer import (
                DEFAULT_TENANT_ID,
                is_module_enabled,
                tenant_has_any_module_row,
            )
            from app.modules.runtime.permission_module_map import module_for_permission

            uid = current_user["id"]
            if not (
                service.get_user_role_slugs(uid)
                & {UserPermissionService.SUPERADMIN_ROLE_SLUG, "admin"}
            ):
                mod = module_for_permission(permission_slug)
                if mod and tenant_has_any_module_row(db, DEFAULT_TENANT_ID):
                    if not is_module_enabled(db, DEFAULT_TENANT_ID, mod):
                        raise HTTPException(
                            status_code=status.HTTP_403_FORBIDDEN,
                            detail=f"Module not enabled: {mod}",
                        )
        return current_user

    return _require


def require_any_permission(permission_slugs: List[str]):
    """
    Dependency that requires the current user to have at least one of the given permissions
    (or superadmin/admin role). Deny by default; raises 403 if the user has none.
    Under strict module guard, user must satisfy at least one slug that is also allowed by module state.
    """

    def _require(current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
        service = UserPermissionService(db)
        uid = current_user["id"]
        if service.get_user_role_slugs(uid) & {UserPermissionService.SUPERADMIN_ROLE_SLUG, "admin"}:
            return current_user
        user_slugs = service.get_user_permission_slugs(uid)
        if getattr(settings, "module_guard_strict", False):
            from app.modules.runtime.installer import (
                DEFAULT_TENANT_ID,
                is_module_enabled,
                tenant_has_any_module_row,
            )
            from app.modules.runtime.permission_module_map import module_for_permission

            if tenant_has_any_module_row(db, DEFAULT_TENANT_ID):
                allowed = False
                for slug in permission_slugs:
                    if slug not in user_slugs:
                        continue
                    mod = module_for_permission(slug)
                    if not mod or is_module_enabled(db, DEFAULT_TENANT_ID, mod):
                        allowed = True
                        break
                if not allowed:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail=f"One of these permissions required (module may be disabled): {', '.join(permission_slugs)}",
                    )
                return current_user
        if not any(s in user_slugs for s in permission_slugs):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"One of these permissions required: {', '.join(permission_slugs)}",
            )
        return current_user

    return _require


def require_session_or_api_key_permission(permission_slug: str):
    """A shared lookup: any signed-in session reads it; an API key still needs ``permission_slug``.

    Owner ruling 1 Oct 2026 (never-stuck L10) opens pickers such as the master-data selects
    to every signed-in user. An integration, though, acts with its act-as user's grants
    (``integration_auth.py``), so a key whose act-as user lacks the slug stays refused.
    """

    def _require(
        current_user: dict = Depends(get_current_user_or_api_key),
        db: Session = Depends(get_db),
    ) -> dict:
        if current_user.get("auth_method") in {"api_key", "integration_api_key"}:
            if not UserPermissionService(db).check_user_has_permission(
                current_user["id"], permission_slug
            ):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Permission required: {permission_slug}",
                )
        return current_user

    return _require


def require_permission_with_api_key(permission_slug: str):
    """
    Same as require_permission but allows X-API-Key (with act-as user) for automation.
    The permission is still enforced against the resolved (act-as) user.

    Primarily for read endpoints. It is also used on a small set of AI-assistant
    write actions (complaint close, PR approve/reject) where execution runs as the
    act-as principal - those paths ALSO enforce the actual end-user's permission at
    the assistant layer (`_WRITE_TOOL_PERMISSIONS` in ai_assistant_service), so the
    check is applied twice. Do not add it to a write endpoint without that second,
    real-user gate.
    """

    def _require(
        current_user: dict = Depends(get_current_user_or_api_key),
        db: Session = Depends(get_db),
    ) -> dict:
        service = UserPermissionService(db)
        if not service.check_user_has_permission(current_user["id"], permission_slug):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission required: {permission_slug}",
            )
        if getattr(settings, "module_guard_strict", False):
            from app.modules.runtime.installer import (
                DEFAULT_TENANT_ID,
                is_module_enabled,
                tenant_has_any_module_row,
            )
            from app.modules.runtime.permission_module_map import module_for_permission

            uid = current_user["id"]
            if not (
                service.get_user_role_slugs(uid)
                & {UserPermissionService.SUPERADMIN_ROLE_SLUG, "admin"}
            ):
                mod = module_for_permission(permission_slug)
                if mod and tenant_has_any_module_row(db, DEFAULT_TENANT_ID):
                    if not is_module_enabled(db, DEFAULT_TENANT_ID, mod):
                        raise HTTPException(
                            status_code=status.HTTP_403_FORBIDDEN,
                            detail=f"Module not enabled: {mod}",
                        )
        return current_user

    return _require


def require_any_permission_with_api_key(permission_slugs: List[str]):
    """Like require_any_permission but uses get_current_user_or_api_key (read/automation only)."""

    def _require(
        current_user: dict = Depends(get_current_user_or_api_key),
        db: Session = Depends(get_db),
    ) -> dict:
        service = UserPermissionService(db)
        uid = current_user["id"]
        if service.get_user_role_slugs(uid) & {UserPermissionService.SUPERADMIN_ROLE_SLUG, "admin"}:
            return current_user
        user_slugs = service.get_user_permission_slugs(uid)
        if getattr(settings, "module_guard_strict", False):
            from app.modules.runtime.installer import (
                DEFAULT_TENANT_ID,
                is_module_enabled,
                tenant_has_any_module_row,
            )
            from app.modules.runtime.permission_module_map import module_for_permission

            if tenant_has_any_module_row(db, DEFAULT_TENANT_ID):
                allowed = False
                for slug in permission_slugs:
                    if slug not in user_slugs:
                        continue
                    mod = module_for_permission(slug)
                    if not mod or is_module_enabled(db, DEFAULT_TENANT_ID, mod):
                        allowed = True
                        break
                if not allowed:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail=f"One of these permissions required (module may be disabled): {', '.join(permission_slugs)}",
                    )
                return current_user
        if not any(s in user_slugs for s in permission_slugs):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"One of these permissions required: {', '.join(permission_slugs)}",
            )
        return current_user

    return _require


async def get_external_api_user(
    request: Request,
    api_key: Optional[str] = Depends(get_api_key),
    db: Session = Depends(get_db),
) -> dict:
    """Authenticate an integration API key and return the principal it acts as.

    Was: a plain ``!=`` against the shared ``EXTERNAL_API_KEY`` env var, returning
    a hardcoded ``{"id": "system"}`` that matched no row in the database. Callers
    could not be told apart, the key could not be rotated or revoked, and the
    comparison leaked timing.

    Now: the key resolves through ``integration_api_keys`` to the integration's
    ``act_as_user_id`` -- a real user, so writes attribute correctly and ordinary
    RBAC applies. Nothing reads the env var at runtime; the legacy shared key
    keeps working because its *hash* was seeded as an integration (AC-AC-09).
    """
    from app.services.integration_auth import resolve_integration_principal

    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "api_key_required", "message": "API key is required"},
        )

    user = resolve_integration_principal(db, api_key)
    _stamp_integration_actor(request, db, user)
    return user


async def get_current_user_or_api_key(
    request: Request,
    token: Optional[str] = Depends(oauth2_scheme),
    api_key: Optional[str] = Depends(get_api_key),
    db: Session = Depends(get_db)
) -> dict:
    """
    Validate either JWT token (from NextAuth) or API key and return user information.
    
    This dependency allows both authenticated users and external API key access.

    ``async def`` (identity S0, AC-12) so its audit actor stamp lands on the request's
    own context, not on a threadpool thread's copy that the flush never sees.
    """
    import logging
    logger = logging.getLogger(__name__)
    
    started = time.perf_counter()
    auth_mode = "unknown"

    logger.info("auth.get_current_user_or_api_key start has_api_key=%s has_token=%s", bool(api_key), bool(token))

    # If API key is provided, validate it
    if api_key:
        auth_mode = "api_key"
        # Resolves through integration_api_keys to the integration's principal.
        # The env var is no longer consulted at runtime -- the legacy shared key
        # keeps working because its hash was seeded as an integration, not
        # because anything reads EXTERNAL_API_KEY (AC-AC-01 / AC-AC-09).
        from app.services.integration_auth import resolve_integration_principal

        user = resolve_integration_principal(db, api_key)
        _stamp_integration_actor(request, db, user)

        elapsed_ms = (time.perf_counter() - started) * 1000
        logger.info("auth.get_current_user_or_api_key done mode=%s elapsed_ms=%.1f", auth_mode, elapsed_ms)
        return user

    # Otherwise, try JWT token authentication
    auth_mode = "jwt"
    if not token:
        token = extract_token_from_request(request)
    
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Provide either Bearer token or X-API-Key header.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    try:
        real_user = _resolve_session_to_user(token, request, db)
        _stamp_session_actor(request, db, real_user)
        effective_user = _maybe_apply_impersonation(request, db, real_user)
        effective_user["auth_method"] = "jwt"
        elapsed_ms = (time.perf_counter() - started) * 1000
        logger.info("auth.get_current_user_or_api_key done mode=%s elapsed_ms=%.1f", auth_mode, elapsed_ms)
        return effective_user
    except HTTPException:
        raise
    except JWTError as e:
        elapsed_ms = (time.perf_counter() - started) * 1000
        logger.warning("auth.get_current_user_or_api_key failed mode=%s elapsed_ms=%.1f error=%s", auth_mode, elapsed_ms, str(e))
        logger.error(f"JWT validation failed: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {str(e)}"
        )
    except Exception as e:
        elapsed_ms = (time.perf_counter() - started) * 1000
        logger.warning("auth.get_current_user_or_api_key failed mode=%s elapsed_ms=%.1f error=%s", auth_mode, elapsed_ms, str(e))
        logger.error(f"Unexpected error in authentication: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Authentication error: {str(e)}"
        )