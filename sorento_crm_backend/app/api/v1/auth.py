"""Authentication API routes."""
import logging
from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func
from sqlalchemy.orm import Session
import bcrypt
import secrets
import hashlib
from datetime import datetime, timezone, timedelta

from app.config import settings as app_settings
from app.database import get_db
from app.models.user import User, UserRole, SystemSetting
from app.models.auth import VerificationToken
from app.schemas.auth import (
    LoginRequest, LoginResponse, SignupRequest, SignupResponse,
    ResetPasswordRequest, ResetPasswordResponse,
    ChangePasswordRequest, ChangePasswordResponse,
    VerifyEmailRequest, VerifyEmailResponse,
    VerifyResetTokenRequest, VerifyResetTokenResponse,
    SessionInfo, MessageResponse,
    PhoneRequestCodeRequest, PhoneRequestCodeResponse, PhoneVerifyRequest,
    SetPasswordRequest,
)
from app.dependencies import get_current_user, get_actor_user_id
from app.services import user_session_service
from app.services.error_handler import handle_internal_error

logger = logging.getLogger(__name__)
router = APIRouter()

# A throwaway bcrypt check against this (module-level, computed once) hash
# equalises the timing of "no such user" / "trashed user" with a real wrong-
# password check, so neither is distinguishable from a wrong password by
# response time (AC-26 no-enumeration).
_DUMMY_PASSWORD_HASH = bcrypt.hashpw(b"identity-s1-timing-dummy", bcrypt.gensalt()).decode("utf-8")


def _verification_token_expired(token_row: VerificationToken, now_naive: datetime) -> bool:
    exp = getattr(token_row, "expires", None)
    if not isinstance(exp, datetime):
        return True
    return exp < now_naive


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)) -> LoginResponse:
    from app.services import login_throttle
    from app.services.user_session_service import mint_session

    ip = request.client.host if request.client else None

    # Brute-force throttle (per email+ip). Fails open if Redis is down.
    gate = login_throttle.check(payload.email, ip)
    if not gate.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed attempts. Please try again later.",
            headers={"Retry-After": str(gate.retry_after_seconds or login_throttle.LOCK_WINDOW_SECONDS)},
        )

    # Case-insensitive (identity S0, AC-03): every stored email is lowercased and
    # `uq_users_email_lower` keeps at most one match.
    user: User | None = (
        db.query(User)
        .filter(func.lower(User.email) == str(payload.email).strip().lower())
        .first()
    )

    # AC-26: an unknown email must not be distinguishable from a wrong
    # password (previously a 404 - a direct "does this email exist" oracle),
    # and a trashed user must never sign in by any method (plan 5.3). Both
    # take a throwaway bcrypt check for timing, then the SAME 401 body a
    # wrong password gets.
    if not user or bool(getattr(user, "is_trashed", False)):
        login_throttle.record_failure(payload.email, ip)
        try:
            bcrypt.checkpw(payload.password.encode("utf-8"), _DUMMY_PASSWORD_HASH.encode("utf-8"))
        except Exception:
            pass
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials.",
        )

    pw_raw = getattr(user, "password", None)
    if pw_raw is None or str(pw_raw).strip() == "":
        # Security round S2 (#1280): a phone-only/OAuth-only user with no
        # password hash at all must not answer any faster than a wrong
        # password does - same throwaway check as the unknown/trashed branch.
        login_throttle.record_failure(payload.email, ip)
        try:
            bcrypt.checkpw(payload.password.encode("utf-8"), _DUMMY_PASSWORD_HASH.encode("utf-8"))
        except Exception:
            pass
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials.",
        )

    try:
        ok = bcrypt.checkpw(
            payload.password.encode("utf-8"),
            str(pw_raw).encode("utf-8"),
        )
    except Exception:
        ok = False

    if not ok:
        login_throttle.record_failure(payload.email, ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials.",
        )

    if str(getattr(user, "status", "") or "") != "ACTIVE":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account not activated. Please verify your email.",
        )

    # Successful auth - clear the throttle counter.
    login_throttle.clear(payload.email, ip)

    # Store naive UTC (DB columns are timezone=False)
    setattr(user, "last_sign_in_at", datetime.now(timezone.utc).replace(tzinfo=None))
    db.add(user)
    db.commit()

    uid = str(getattr(user, "id", "") or "")

    # Mint the opaque 30-day sliding session (same rule as phone sign-in and the portal).
    session_row = mint_session(
        db,
        uid,
        user_agent=request.headers.get("user-agent"),
        ip_address=ip,
        auth_method="password",
    )

    from app.services.phone_signin_service import build_login_response

    return build_login_response(db, user, session_row)


@router.post("/signup", response_model=SignupResponse, status_code=status.HTTP_201_CREATED)
async def signup(
    payload: SignupRequest,
    request: Request,
    db: Session = Depends(get_db)
):
    """Register a new user."""
    # Generic, enumeration-safe response used for BOTH new and already-registered
    # emails so an attacker cannot probe which emails exist (see PLAN-fix-security
    # -cluster A). Same message + 201 either way.
    _GENERIC_SIGNUP_MSG = (
        "If this email is available, a verification link has been sent. "
        "Please check your inbox to verify your account."
    )
    try:
        ip = request.client.host if request.client else None
        from app.services import rate_limit
        gate = rate_limit.hit(
            "signup", ip,
            limit=app_settings.rate_limit_signup_max,
            window_seconds=app_settings.rate_limit_signup_window_seconds,
        )
        if not gate.allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many sign-up attempts. Please try again later.",
                headers={"Retry-After": str(gate.retry_after_seconds or app_settings.rate_limit_signup_window_seconds)},
            )

        # Already-registered email: do NOT reveal it (no 409). Equalise timing with
        # a throwaway hash so the response is indistinguishable from a fresh signup,
        # then return the same generic message without creating a duplicate.
        signup_email = str(payload.email).strip().lower()
        existing = db.query(User).filter(func.lower(User.email) == signup_email).first()
        if existing:
            try:
                bcrypt.hashpw(payload.password.encode("utf-8"), bcrypt.gensalt())
            except Exception:
                pass
            return SignupResponse(
                id="",
                email=str(payload.email),
                name=str(payload.name),
                message=_GENERIC_SIGNUP_MSG,
            )

        # Get default role
        default_role = db.query(UserRole).filter(UserRole.is_default.is_(True)).first()
        if not default_role:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Default role not found."
            )
        
        # Hash password
        hashed_password = bcrypt.hashpw(payload.password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        
        from app.models.user import UserRoleAssignment
        user = User(
            email=signup_email,
            password=hashed_password,
            name=payload.name,
            status="INACTIVE"
        )
        db.add(user)
        db.flush()
        db.add(
            UserRoleAssignment(
                user_id=str(getattr(user, "id", "") or ""),
                role_id=str(getattr(default_role, "id", "") or ""),
            )
        )
        
        # Create verification token
        uid = str(getattr(user, "id", "") or "")
        token = hashlib.sha256(f"{uid}{datetime.now(timezone.utc).isoformat()}".encode()).hexdigest()
        verification_token = VerificationToken(
            identifier=uid,
            token=token,
            expires=datetime.now(timezone.utc) + timedelta(hours=1)
        )
        db.add(verification_token)
        db.commit()
        db.refresh(user)
        
        # TODO: Send verification email (can be done via integration service)
        
        # Return the SAME shape as the already-registered branch (empty id, echoed
        # email/name) so the two are byte-identical - no enumeration tell. The FE
        # only checks response.ok then redirects; it never reads these fields.
        return SignupResponse(
            id="",
            email=str(payload.email),
            name=str(payload.name),
            message=_GENERIC_SIGNUP_MSG,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post("/reset-password", response_model=ResetPasswordResponse)
async def reset_password(
    payload: ResetPasswordRequest,
    request: Request,
    db: Session = Depends(get_db)
):
    """Request password reset."""
    # Generic, enumeration-safe response regardless of whether the email exists
    # (see PLAN-fix-security-cluster A). A reset link is only actually sent when an
    # account is found, but the caller can't tell the difference.
    _GENERIC_RESET_MSG = "If an account exists for this email, a password reset link has been sent."
    try:
        ip = request.client.host if request.client else None
        from app.services import rate_limit
        gate = rate_limit.hit(
            "reset", ip,
            limit=app_settings.rate_limit_reset_max,
            window_seconds=app_settings.rate_limit_reset_window_seconds,
        )
        if not gate.allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many password-reset requests. Please try again later.",
                headers={"Retry-After": str(gate.retry_after_seconds or app_settings.rate_limit_reset_window_seconds)},
            )

        user = (
            db.query(User)
            .filter(func.lower(User.email) == str(payload.email).strip().lower())
            .first()
        )
        if not user:
            # Don't reveal that the account is missing - same response as success.
            return ResetPasswordResponse(message=_GENERIC_RESET_MSG)

        # Generate reset token
        token = secrets.token_urlsafe(32)
        
        # Create verification token
        verification_token = VerificationToken(
            identifier=str(getattr(user, "id", "") or ""),
            token=token,
            expires=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        db.add(verification_token)
        db.commit()

        # Send password reset email (branded layout, visible URL, system disclaimer)
        base_url = (app_settings.frontend_base_url or "").strip().rstrip("/")
        reset_path = "/change-password"
        reset_link = f"{base_url}{reset_path}?token={token}" if base_url else f"{reset_path}?token={token}"
        try:
            from app.services.email_outbox_service import enqueue as enqueue_email
            from app.services.email_template_service import EmailTemplateService

            user_email = str(getattr(user, "email", "") or "")
            rendered = EmailTemplateService(db).render_code(
                "auth_password_reset",
                {"recipient": {"name": user.name or "", "email": user_email}, "reset_link": reset_link},
            )
            enqueue_email(
                db,
                event_key="password_reset",
                to=user_email,
                subject=rendered["subject"],
                body_text=rendered["body_text"],
                body_html=rendered["body_html"],
                from_name="Sorento AI System",
                metadata={"user_id": str(getattr(user, "id", "") or "")},
            )
            db.commit()
        except Exception as e:
            logger.warning("Password reset email enqueue failed: %s", e)

        return ResetPasswordResponse(message=_GENERIC_RESET_MSG)
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post("/verify-reset-token", response_model=VerifyResetTokenResponse)
async def verify_reset_token(
    payload: VerifyResetTokenRequest,
    db: Session = Depends(get_db)
):
    """Validate a reset/invitation token without consuming it. Used by the change-password page."""
    verification_token = db.query(VerificationToken).filter(
        VerificationToken.token == payload.token
    ).first()
    now_utc_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    if verification_token is None or _verification_token_expired(verification_token, now_utc_naive):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired token.",
        )
    user = db.query(User).filter(User.id == verification_token.identifier).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )
    # Optionally return masked email for display (e.g. "j***@example.com")
    parts = str(getattr(user, "email", "") or "").split("@")
    masked = f"{parts[0][:1]}***@{parts[1]}" if len(parts) == 2 and parts[0] else None
    return VerifyResetTokenResponse(valid=True, email=masked)


@router.post("/change-password", response_model=ChangePasswordResponse)
async def change_password(
    payload: ChangePasswordRequest,
    db: Session = Depends(get_db)
):
    """Change password using reset token."""
    try:
        # Validate token
        verification_token = db.query(VerificationToken).filter(
            VerificationToken.token == payload.token
        ).first()
        
        now_utc_naive = datetime.now(timezone.utc).replace(tzinfo=None)
        if verification_token is None or _verification_token_expired(
            verification_token, now_utc_naive
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or expired token.",
            )
        
        # Get user
        user = db.query(User).filter(User.id == verification_token.identifier).first()
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found."
            )
        
        # Hash new password
        hashed_password = bcrypt.hashpw(payload.new_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

        # Update password and activate account so they can log in (invitation or password reset)
        setattr(user, "password", hashed_password)
        setattr(user, "status", "ACTIVE")
        # Accepting invite / reset link proves they received the email, so mark verified
        # Store naive UTC (DB columns are timezone=False)
        setattr(user, "email_verified_at", datetime.now(timezone.utc).replace(tzinfo=None))
        db.add(user)

        # Delete used token
        db.delete(verification_token)
        db.commit()

        # A password change boots every existing device (stolen-session kill switch).
        from app.services.user_session_service import revoke_all_for_user

        revoke_all_for_user(db, str(getattr(user, "id", "") or ""))

        return ChangePasswordResponse(message="Password set successfully. You can now sign in.")
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post("/verify-email", response_model=VerifyEmailResponse)
async def verify_email(
    payload: VerifyEmailRequest,
    db: Session = Depends(get_db)
):
    """Verify email using token."""
    try:
        # Validate token
        verification_token = db.query(VerificationToken).filter(
            VerificationToken.token == payload.token
        ).first()
        
        now_utc_naive = datetime.now(timezone.utc).replace(tzinfo=None)
        if verification_token is None or _verification_token_expired(
            verification_token, now_utc_naive
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or expired token",
            )
        
        # Update user
        user = db.query(User).filter(User.id == verification_token.identifier).first()
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )
        
        setattr(user, "status", "ACTIVE")
        # Store naive UTC (DB columns are timezone=False)
        setattr(user, "email_verified_at", datetime.now(timezone.utc).replace(tzinfo=None))
        db.add(user)

        # Delete used token
        db.delete(verification_token)
        db.commit()

        return VerifyEmailResponse(message="Email verified successfully!")
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


def _iso(dt) -> str | None:
    return dt.isoformat() if dt is not None else None


@router.post("/logout", response_model=MessageResponse)
def logout(request: Request, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)) -> MessageResponse:
    """Revoke the current session (the device calling this). Idempotent."""
    session_id = getattr(request.state, "session_id", None)
    if session_id:
        user_session_service.revoke_session(db, session_id=session_id)
    return MessageResponse(message="Logged out.")


@router.get("/sessions", response_model=list[SessionInfo])
def list_sessions(request: Request, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)) -> list[SessionInfo]:
    """Active sessions for the signed-in user - the "your devices" list.

    Keyed on the REAL user (not the impersonated target), so an admin browsing as
    someone else still sees and manages their own devices.
    """
    current_id = getattr(request.state, "session_id", None)
    rows = user_session_service.list_active_sessions(db, get_actor_user_id(request, current_user))
    return [
        SessionInfo(
            id=str(r.id),
            device_label=user_session_service.device_label_from_ua(r.user_agent),
            ip_address=r.ip_address,
            last_seen_at=_iso(r.last_seen_at),
            created_at=_iso(r.created_at),
            current=(str(r.id) == str(current_id)),
        )
        for r in rows
    ]


@router.delete("/sessions/{session_id}", response_model=MessageResponse)
def revoke_one_session(session_id: str, request: Request, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)) -> MessageResponse:
    """Revoke a single session - only if it belongs to the signed-in (real) user."""
    from app.models.user_session import UserSession

    row = (
        db.query(UserSession)
        .filter(UserSession.id == session_id, UserSession.user_id == get_actor_user_id(request, current_user))
        .first()
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found.")
    user_session_service.revoke_session(db, session_id=session_id)
    return MessageResponse(message="Device signed out.")


@router.post("/sessions/revoke-others", response_model=MessageResponse)
def revoke_other_sessions(request: Request, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)) -> MessageResponse:
    """Log out every other device, keeping the current session alive."""
    current_id = getattr(request.state, "session_id", None)
    count = user_session_service.revoke_all_for_user(
        db, get_actor_user_id(request, current_user), except_session_id=str(current_id) if current_id else None
    )
    return MessageResponse(message="Signed out other devices.", count=count)


# --------------------------------------------------------------------------- #
# Phone sign-in (identity S1, #1280) - AC-20 to AC-29.                        #
#                                                                              #
# Every DB query and the OTP/session/home_path logic live in                 #
# app.services.phone_signin_service; this file only rate-limits (the same    #
# per-IP pattern every other unauthenticated route above already uses) and   #
# shapes the response bodies. See documentation/plans/identity/s1-contract.md.
# --------------------------------------------------------------------------- #

def _rate_limited_response(retry_after_seconds: int) -> JSONResponse:
    from app.services.phone_signin_service import rate_limited_minutes_message

    return JSONResponse(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        content={
            "code": "RATE_LIMITED",
            "message": rate_limited_minutes_message(retry_after_seconds),
            "retry_after_seconds": retry_after_seconds,
        },
        headers={"Retry-After": str(retry_after_seconds)},
    )


@router.post("/phone/request-code", response_model=PhoneRequestCodeResponse)
def phone_request_code(
    payload: PhoneRequestCodeRequest, request: Request, db: Session = Depends(get_db)
):
    """Always answers the same 200 for every number, known or not (AC-21).

    Security round S1 (#1280): the eligibility check used to run HERE, so an
    eligible number paid for `find_eligible`'s two queries plus an
    `enqueue_job` call and an ineligible one paid for neither - a timing
    side-channel. Now every normalised number, known or not, gets the exact
    same one `enqueue_job` call for `dispatch_phone_signin_code`; that job
    (running on the worker, not this request) is where eligibility is
    actually decided.
    """
    from app.services import phone_signin_service as svc
    from app.services import rate_limit
    from app.services.queue_service import enqueue_job
    from app.tasks.respond_io_tasks import dispatch_phone_signin_code

    ip = request.client.host if request.client else None
    num = svc.normalize(payload.phone)

    ip_gate = rate_limit.hit(
        "phone_signin_otp", ip,
        limit=app_settings.rate_limit_portal_otp_max,
        window_seconds=app_settings.rate_limit_portal_otp_window_seconds,
    )
    if not ip_gate.allowed:
        return _rate_limited_response(ip_gate.retry_after_seconds or app_settings.rate_limit_portal_otp_window_seconds)

    cooldown_gate = rate_limit.hit("phone_signin_otp_cooldown", num, limit=1, window_seconds=60)
    if not cooldown_gate.allowed:
        return _rate_limited_response(cooldown_gate.retry_after_seconds or 60)

    daily_gate = rate_limit.hit("phone_signin_otp_daily", num, limit=10, window_seconds=86400)
    if not daily_gate.allowed:
        return _rate_limited_response(daily_gate.retry_after_seconds or 86400)

    try:
        enqueue_job(
            dispatch_phone_signin_code, num, queue_name="respond_io", job_timeout=180
        )
    except Exception as e:  # noqa: BLE001 - Redis down: swallow, answer 200 anyway
        logger.warning("Phone sign-in dispatch enqueue failed: %s", e)

    svc.mark_code_requested(num)

    return PhoneRequestCodeResponse(
        sent_to=svc.mask(num) or "",
        expires_in_seconds=600,
        resend_in_seconds=60,
    )


@router.post("/phone/verify", response_model=LoginResponse)
def phone_verify(payload: PhoneVerifyRequest, request: Request, db: Session = Depends(get_db)):
    """The right code for an eligible number mints a session (AC-24).

    Security round S4 (#1280): no per-IP rate limit here. NextAuth's
    `phone-otp` provider calls this route server-to-server, so
    `request.client.host` is the Next.js server's own address for EVERY
    signed-in user - a shared bucket an attacker could exhaust to lock every
    real sign-in out at once. The per-number atomic reservation (B2,
    `reserve_verify_attempt` + `PortalService.reserve_attempt`) is the real
    bound: it caps guesses per TYPED NUMBER regardless of which IP they came
    from, which is the bound that actually matters here.
    """
    from app.services import phone_signin_service as svc

    ip = request.client.host if request.client else None
    num = svc.normalize(payload.phone)

    reservation = svc.reserve_verify_attempt(num)
    if reservation.locked:
        return _rate_limited_response(reservation.retry_after_seconds or 900)

    if not svc.code_was_requested(num):
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={
                "code": "CODE_EXPIRED",
                "message": "That code has expired. Send a new one.",
            },
        )

    login_response = svc.attempt_verify(
        db, num, payload.code,
        user_agent=request.headers.get("user-agent"),
        ip_address=ip,
    )
    if login_response is not None:
        svc.clear_redis_state(num)
        return login_response

    if reservation.attempts_left == 0:
        # Fix lane round 2 (reviewer B1): the 5th wrong answer spends the
        # budget, so it answers the lock itself, not "0 tries left".
        return _rate_limited_response(svc.check_locked(num) or 900)

    if reservation.attempts_left is not None:
        tries_word = "try" if reservation.attempts_left == 1 else "tries"
        content = {
            "code": "CODE_WRONG",
            "message": f"That code is not right. {reservation.attempts_left} {tries_word} left.",
            "attempts_left": reservation.attempts_left,
        }
    else:
        # Redis down: fail open on the count, not on the refusal itself.
        content = {"code": "CODE_WRONG", "message": "That code is not right."}
    return JSONResponse(status_code=status.HTTP_401_UNAUTHORIZED, content=content)


@router.post("/password", response_model=MessageResponse)
def set_password(
    payload: SetPasswordRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Set/change the signed-in user's password (plan 5.3).

    A phone-only user sets one with no ``current_password`` to prove; a user
    who already has one must supply and pass it. Every OTHER session of
    ``current_user`` is revoked on success - the current one stays (mirrors
    `/sessions/revoke-others`).

    Security round B3 (#1280): refused outright while impersonating. Under
    impersonation, ``current_user`` is the TARGET (see
    ``dependencies._maybe_apply_impersonation``), so this would otherwise let
    an admin set the target's password with no current-password check at all
    (an account takeover, not just a support action), while revoking the
    ADMIN's own other sessions instead of the target's - the wrong person's
    devices, for the wrong person's password change.
    """
    if getattr(request.state, "impersonation_session_id", None):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not available while impersonating.",
        )

    user = db.query(User).filter(User.id == current_user["id"]).first()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

    existing_hash = getattr(user, "password", None)
    has_password = bool(existing_hash and str(existing_hash).strip())
    if has_password:
        if not payload.current_password:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Current password is required.",
            )
        try:
            ok = bcrypt.checkpw(
                payload.current_password.encode("utf-8"), str(existing_hash).encode("utf-8")
            )
        except Exception:
            ok = False
        if not ok:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Current password is not right.",
            )

    new_hash = bcrypt.hashpw(payload.new_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    user.password = new_hash
    db.add(user)
    db.commit()

    # current_user["id"], not get_actor_user_id: impersonation is refused
    # above, so the acting principal and the user whose password just changed
    # are always the same person here - this revokes THEIR other devices.
    current_session_id = getattr(request.state, "session_id", None)
    user_session_service.revoke_all_for_user(
        db,
        current_user["id"],
        except_session_id=str(current_session_id) if current_session_id else None,
    )

    return MessageResponse(message="Password saved.")
