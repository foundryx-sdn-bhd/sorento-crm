"""DEV-LOGIN-BYPASS routes: passwordless sign-in for LOCAL test copies only.

Every guard lives in ``app/services/dev_login.py``; when any fails both routes answer the same
plain 404 an unmounted route gives. A dev sign-in mints the SAME ``user_sessions`` row and
returns the SAME body as ``POST /auth/login``, with ``auth_method = dev_login``.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.user import User, UserRole, UserRoleAssignment
from app.schemas.auth import LoginResponse
from app.services import dev_login

router = APIRouter()


class DevLoginRequest(BaseModel):
    email: str


class DevLoginUser(BaseModel):
    email: str
    name: str | None = None
    role_name: str | None = None


class DevLoginUsersResponse(BaseModel):
    users: list[DevLoginUser]


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")


def require_dev_login(request: Request) -> None:
    peer = request.client.host if request.client else None
    if not dev_login.request_allowed(
        enabled=settings.dev_auto_login,
        environment=settings.environment,
        secret=settings.dev_auto_login_secret,
        presented_secret=request.headers.get(dev_login.SECRET_HEADER),
        forwarded_host=request.headers.get("x-forwarded-host"),
        host_header=request.headers.get("host"),
        peer_ip=peer,
    ):
        raise _not_found()


def _signable_users(db: Session) -> list[User]:
    """Allowlisted users that may sign in, in allowlist order."""
    emails = dev_login.allowed_emails(settings.dev_auto_login_users)
    if not emails:
        return []
    rows = db.query(User).filter(func.lower(User.email).in_(emails)).all()
    by_email = {str(u.email).lower(): u for u in rows}
    out: list[User] = []
    for email in emails:
        user = by_email.get(email)
        if user is None or bool(getattr(user, "is_trashed", False)):
            continue
        if str(getattr(user, "status", "") or "") != "ACTIVE":
            continue
        out.append(user)
    return out


def _role_name(db: Session, user_id: str) -> str | None:
    role = (
        db.query(UserRole)
        .join(UserRoleAssignment, UserRoleAssignment.role_id == UserRole.id)
        .filter(UserRoleAssignment.user_id == user_id)
        .order_by(UserRoleAssignment.assigned_at.asc())
        .first()
    )
    return str(role.name) if role is not None and role.name else None


@router.get("/dev-login/users", response_model=DevLoginUsersResponse, dependencies=[Depends(require_dev_login)])
def list_dev_login_users(db: Session = Depends(get_db)) -> DevLoginUsersResponse:
    users = _signable_users(db)
    if not users:
        raise _not_found()
    return DevLoginUsersResponse(
        users=[
            DevLoginUser(email=str(u.email), name=u.name or None, role_name=_role_name(db, str(u.id)))
            for u in users
        ]
    )


@router.post("/dev-login", response_model=LoginResponse, dependencies=[Depends(require_dev_login)])
def dev_login_sign_in(payload: DevLoginRequest, request: Request, db: Session = Depends(get_db)) -> LoginResponse:
    from app.services.phone_signin_service import build_login_response
    from app.services.user_session_service import mint_session

    wanted = payload.email.strip().lower()
    user = next((u for u in _signable_users(db) if str(u.email).lower() == wanted), None)
    if user is None:
        raise _not_found()

    user.last_sign_in_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.add(user)
    db.commit()

    session_row = mint_session(
        db,
        str(user.id),
        user_agent=request.headers.get("user-agent"),
        ip_address=request.client.host if request.client else None,
        auth_method="dev_login",
    )
    return build_login_response(db, user, session_row)
