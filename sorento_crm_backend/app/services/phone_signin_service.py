"""Phone sign-in (identity S1, #1280): AC-21 to AC-24, AC-26 to AC-28.

Normalises/masks a typed phone number, resolves it to an eligible user
without ever revealing whether the number belongs to anyone, dispatches the
WhatsApp code (reusing the portal's own OTP machinery), and builds the shared
login response - including ``home_path`` - both ``/auth/login`` and
``/auth/phone/verify`` return.

The router (``app/api/v1/auth.py``) stays HTTP-only: it owns the per-IP/per-
number Redis rate limiting (the same pattern every other unauthenticated
route in that file already uses) and shapes the response bodies; every DB
query and the OTP/session/Respond.io logic live here.

See documentation/plans/identity/s1-contract.md.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Optional

from sqlalchemy.orm import Session

from app.models.access import RespondContact
from app.models.portal import PortalOtpCode
from app.models.user import User, UserRole, UserRoleAssignment
from app.models.user_session import UserSession
from app.schemas.auth import LoginResponse
from app.services.phone_utils import normalize_msisdn
from app.services.portal_service import (
    PortalService,
    _hash_otp,
    _utcnow,
)

logger = logging.getLogger(__name__)

# The in-window free text (S1 "First task"): names no portal, unlike the
# portal's own OTP text. Out of the window the code goes out under the approved
# ``portal_otp`` template (owner ruling, 27 Sep 2026); the optional override
# lives in the RQ task, `app.tasks.respond_io_tasks.send_login_otp_respond_message`.
SIGNIN_OTP_TEXT = (
    "Your Sorento sign-in code is {code}. It expires in 10 minutes. "
    "Please do not share it with anyone."
)

# Redis bookkeeping, separate from the portal's own (contact-keyed) machinery:
# these are keyed on the TYPED number so an unknown number counts down
# identically to a known one (no enumeration tell).
_REQ_MARKER_TTL_SECONDS = 600
_TRIES_LOCK_SECONDS = 900
MAX_WRONG_ATTEMPTS = 5


def _req_key(num: str) -> str:
    return f"phone_signin:req:{num}"


def _tries_key(num: str) -> str:
    return f"phone_signin:tries:{num}"


def _redis():
    try:
        from app.services.queue_service import redis_conn

        return redis_conn
    except Exception as e:  # noqa: BLE001 - infra
        logger.warning("Phone sign-in: Redis unavailable (%s); failing open.", e)
        return None


def normalize(phone: str) -> str:
    """E.164 digits (no ``+``), 8 to 15 of them, or a 422.

    AC-21: depends only on the typed text, never on whether the number
    belongs to anyone.
    """
    from fastapi import HTTPException

    num = normalize_msisdn(phone)
    if not num or not (8 <= len(num) <= 15):
        raise HTTPException(status_code=422, detail="Enter a valid phone number.")
    return num


def mask(num: str) -> Optional[str]:
    """Mask of the TYPED number (never a stored one) - AC-21 no-enumeration."""
    return PortalService._mask_phone(num)


def find_eligible(db: Session, num: str) -> Optional[tuple[User, RespondContact]]:
    """Resolve a normalised number to exactly one eligible user, or ``None``.

    Eligible = ACTIVE, not trashed, not an integration, with a linked WhatsApp
    contact whose OWN phone also normalises to ``num`` (a user whose phone was
    changed out from under a stale contact link is refused, plan 5.3 "lost
    phone"). Both branches - a user found and not - run the same two queries
    (a user lookup, then a contact lookup) so the timing carries no tell.

    Security round S5 (#1280): ``.limit(2).all()`` rather than ``.first()`` -
    a stray second row with the same number in a different format (``num``
    vs ``+num``) must not silently pick whichever one the query planner
    happens to return first; MORE than one match is treated as ineligible.
    """
    users = (
        db.query(User)
        .filter(
            User.contact_number.in_([num, f"+{num}"]),
            User.status == "ACTIVE",
            User.is_trashed.is_(False),
            User.is_integration.is_(False),
            User.respond_contact_id.isnot(None),
        )
        .limit(2)
        .all()
    )
    user = users[0] if len(users) == 1 else None
    if user is not None:
        contacts = (
            db.query(RespondContact)
            .filter(RespondContact.id == user.respond_contact_id)
            .limit(2)
            .all()
        )
    else:
        # Equivalent lookup so an unknown number pays for the same two
        # queries a known-but-ineligible one does.
        contacts = (
            db.query(RespondContact)
            .filter(RespondContact.phone_number.in_([num, f"+{num}"]))
            .limit(2)
            .all()
        )
    contact = contacts[0] if len(contacts) == 1 else None

    if user is None or contact is None:
        return None
    if normalize_msisdn(contact.phone_number) != num:
        return None
    return user, contact


def send_signin_code(db: Session, contact: RespondContact, *, dispatch_inline: bool = False) -> None:
    """Create + dispatch a sign-in OTP for an eligible contact.

    Swallows every failure (the contact's own DB cooldown/cap, or a
    dispatch/send failure) - AC-21's 200 answer never depends on whether a
    send actually went out. ``dispatch_inline`` (security round S1) is set by
    :func:`app.tasks.respond_io_tasks.dispatch_phone_signin_code`, which is
    already running inside the ``respond_io`` queue's own job.
    """
    from app.tasks.respond_io_tasks import send_login_otp_respond_message

    space_id = ""
    workspace = getattr(contact, "workspace", None)
    if workspace is not None:
        space_id = getattr(workspace, "space_id", None) or ""

    try:
        PortalService(db).create_and_dispatch_otp(
            contact, space_id, SIGNIN_OTP_TEXT, send_login_otp_respond_message,
            dispatch_inline=dispatch_inline,
        )
    except Exception as e:  # noqa: BLE001
        # Fix lane round 2 (reviewer Nit 3): the type only above DEBUG. A
        # Respond.io error body can echo the message it was sent, code included.
        logger.warning(
            "Phone sign-in OTP not sent for contact %s: %s", contact.id, type(e).__name__
        )
        logger.debug("Phone sign-in OTP send failure detail: %s", e)


def mark_code_requested(num: str) -> None:
    """AC-23: record "a code was requested for this number now" (10 min TTL).

    A fresh request must not lift an existing 5-wrong-attempt lock (AC-23:
    "a new request-code does not lift it"), so the tries counter is only
    cleared when the number isn't currently locked.
    """
    r = _redis()
    if r is None:
        return
    try:
        r.set(_req_key(num), "1", ex=_REQ_MARKER_TTL_SECONDS)
        if check_locked(num) is None:
            r.delete(_tries_key(num))
    except Exception as e:  # noqa: BLE001
        logger.warning("Phone sign-in: Redis marker write failed (%s)", e)


def code_was_requested(num: str) -> bool:
    """Fail-open: an unreadable Redis must not manufacture a false EXPIRED."""
    r = _redis()
    if r is None:
        return True
    try:
        return bool(r.exists(_req_key(num)))
    except Exception as e:  # noqa: BLE001
        logger.warning("Phone sign-in: Redis marker read failed (%s)", e)
        return True


def check_locked(num: str) -> Optional[int]:
    """``retry_after_seconds`` if this number is already locked out, else None.

    Checked before the submitted code is even looked at, so a locked number
    never gets a free correctness check (AC-23: a new request-code does not
    lift the lock).
    """
    r = _redis()
    if r is None:
        return None
    key = _tries_key(num)
    try:
        raw = r.get(key)
        count = int(raw) if raw is not None else 0
        if count < MAX_WRONG_ATTEMPTS:
            return None
        ttl = r.ttl(key)
        return int(ttl) if isinstance(ttl, int) and ttl > 0 else _TRIES_LOCK_SECONDS
    except Exception as e:  # noqa: BLE001
        logger.warning("Phone sign-in: Redis tries read failed (%s)", e)
        return None


@dataclass
class WrongAttemptResult:
    locked: bool
    # None when Redis is down (fail open - "no count", per the contract).
    attempts_left: Optional[int] = None
    retry_after_seconds: Optional[int] = None


def reserve_verify_attempt(num: str) -> WrongAttemptResult:
    """INCR the typed number's attempt counter BEFORE the code is compared
    (900s TTL from the first call), and report whether that reservation is
    already at the lock threshold.

    Security round B2 (#1280): this used to run only AFTER a wrong compare
    (``record_wrong_attempt``), which is exactly the race - two parallel
    verify calls could both read the pre-increment count, both decide they
    still had guesses left, and both compare, so parallel requests got far
    more than 5 tries per code. INCR happening first and unconditionally (on
    every call, right code or wrong) closes that: whichever call's INCR lands
    second sees the first one's result and can be turned away before it ever
    looks at the code. A caller whose compare then succeeds calls
    :func:`clear_redis_state` so a right answer does not count against the
    budget.

    Fix lane round 2 (reviewer B1, #1280): the lock used to trip at
    ``value >= MAX_WRONG_ATTEMPTS``, i.e. on the 5th call BEFORE its code was
    compared, so a user really got 4 tries and a right 5th code answered 429.
    Only a call PAST the cap is refused unseen now; the 5th is compared with
    ``attempts_left == 0``, and the router turns a wrong 5th into the 429.
    """
    r = _redis()
    if r is None:
        return WrongAttemptResult(locked=False)
    key = _tries_key(num)
    try:
        value = int(r.incr(key))
        if value == 1:
            r.expire(key, _TRIES_LOCK_SECONDS)
        if value > MAX_WRONG_ATTEMPTS:
            ttl = r.ttl(key)
            retry = int(ttl) if isinstance(ttl, int) and ttl > 0 else _TRIES_LOCK_SECONDS
            return WrongAttemptResult(locked=True, retry_after_seconds=retry)
        return WrongAttemptResult(locked=False, attempts_left=MAX_WRONG_ATTEMPTS - value)
    except Exception as e:  # noqa: BLE001
        logger.warning("Phone sign-in: Redis tries incr failed (%s)", e)
        return WrongAttemptResult(locked=False)


def clear_redis_state(num: str) -> None:
    """A successful verify (or a settled otherwise-terminal outcome) drops both keys."""
    r = _redis()
    if r is None:
        return
    try:
        r.delete(_req_key(num), _tries_key(num))
    except Exception as e:  # noqa: BLE001
        logger.warning("Phone sign-in: Redis cleanup failed (%s)", e)


def rate_limited_minutes_message(retry_after_seconds: int) -> str:
    """"Too many tries. Try again in N minutes." - N = ceil(seconds/60), min 1, singular at 1."""
    minutes = max(1, math.ceil(retry_after_seconds / 60))
    word = "minute" if minutes == 1 else "minutes"
    return f"Too many tries. Try again in {minutes} {word}."


def home_path_for_user(db: Session, user: User) -> str:
    """AC-28: a `callbackUrl` (frontend concern) always wins; otherwise -

    - `salesperson` role + a linked contact -> that contact's portal home;
    - any admin role or CRM permission -> the CRM home ("/");
    - a linked contact -> its portal home;
    - else -> "/".
    """
    from app.services.user_service import UserPermissionService

    uid = str(user.id)
    perm_service = UserPermissionService(db)
    role_slugs = perm_service.get_user_role_slugs(uid)

    contact: Optional[RespondContact] = None
    if user.respond_contact_id:
        contact = (
            db.query(RespondContact)
            .filter(RespondContact.id == user.respond_contact_id)
            .first()
        )

    if "salesperson" in role_slugs and contact is not None:
        return f"/portal/c/{PortalService(db).get_or_create_slug(contact)}"

    if role_slugs & {"admin", "superadmin"} or perm_service.get_user_permission_slugs(uid):
        return "/"

    if contact is not None:
        return f"/portal/c/{PortalService(db).get_or_create_slug(contact)}"

    return "/"


def build_login_response(db: Session, user: User, session_row: UserSession) -> LoginResponse:
    """The shared login shape both ``/auth/login`` and ``/auth/phone/verify`` return."""
    uid = str(user.id)
    assignments = (
        db.query(UserRoleAssignment)
        .filter(UserRoleAssignment.user_id == uid)
        .order_by(UserRoleAssignment.assigned_at.asc())
        .all()
    )
    role_ids = [str(a.role_id) for a in assignments if getattr(a, "role_id", None) is not None]
    role_id: Optional[str] = role_ids[0] if role_ids else None
    role: Optional[UserRole] = (
        db.query(UserRole).filter(UserRole.id == role_id).first() if role_id else None
    )
    role_name = str(getattr(role, "name", "") or "") if role is not None else None

    return LoginResponse(
        token=str(session_row.token),
        id=uid,
        email=getattr(user, "email", None) or None,
        name=str(getattr(user, "name", "") or "") or None,
        avatar=str(getattr(user, "avatar", "") or "") or None,
        status=str(getattr(user, "status", "") or ""),
        role_id=role_id if role_id is not None else "",
        role_name=role_name,
        role_ids=role_ids,
        home_path=home_path_for_user(db, user),
    )


def attempt_verify(
    db: Session,
    num: str,
    code: str,
    *,
    user_agent: Optional[str],
    ip_address: Optional[str],
) -> Optional[LoginResponse]:
    """AC-24: the right code for an eligible number mints a `user_sessions`
    row and returns the shared login shape; anything else (unknown number, an
    ineligible user, no outstanding code, a wrong code) answers ``None`` -
    the router's 401 carries no enumeration tell either way.

    Security round B2 (#1280): the attempt is RESERVED (an atomic
    ``UPDATE ... RETURNING`` on ``portal_otp_codes.attempts``,
    :meth:`PortalService.reserve_attempt`) before the code is ever compared,
    and a match is CONSUMED (:meth:`PortalService.consume_reserved`) with the
    same atomicity before a session is minted - closing the race where two
    parallel requests could each read a stale ``attempts`` value, or both
    read "not yet consumed" and each mint a session for the same one-time
    code. An unknown/ineligible number still pays for the same two UPDATE
    statements, against :data:`app.services.portal_service.NIL_OTP_ID` (which
    can never match a row), so the timing carries no eligibility tell.
    """
    import hmac

    from app.services.portal_service import NIL_OTP_ID
    from app.services.user_session_service import mint_session

    portal = PortalService(db)
    eligible = find_eligible(db, num)

    # Fix lane round 2 (reviewer Nit 2): the OTP lookup runs for every number,
    # keyed by the nil id when nobody is eligible (0 rows), so an unknown
    # number pays for the same SELECT an eligible one does.
    otp = (
        db.query(PortalOtpCode)
        .filter(
            PortalOtpCode.contact_id == (eligible[1].id if eligible else NIL_OTP_ID),
            PortalOtpCode.consumed_at.is_(None),
        )
        .order_by(PortalOtpCode.created_at.desc())
        .first()
    )
    if eligible is None:
        otp = None
    # No OTP row, or an expired one, reserves against the nil id instead (0
    # rows either way) - reserve_attempt's own WHERE clause already guards
    # expiry, so this pays for the identical two statements as the eligible
    # path without a separate early return.
    otp_id = otp.id if otp is not None else NIL_OTP_ID

    reserved = portal.reserve_attempt(otp_id)
    if reserved is None:
        # Timing parity even on the "can't reserve" path (locked out, no code,
        # expired, or nil) - still pays for the second UPDATE, a no-op either way.
        portal.consume_reserved(NIL_OTP_ID, _utcnow())
        return None
    _attempts_after, code_hash = reserved
    # A reservation only succeeds against a real row, which only an eligible
    # number's lookup can return.
    if eligible is None:
        return None
    user = eligible[0]

    if not hmac.compare_digest(code_hash, _hash_otp(code)):
        return None

    if not portal.consume_reserved(otp_id, _utcnow()):
        # A parallel verify consumed this exact code first - refuse rather
        # than mint a second session for a one-time code.
        return None

    now = _utcnow()
    user.phone_verified_at = now
    user.last_sign_in_at = now
    db.add(user)
    db.commit()

    session_row = mint_session(
        db,
        str(user.id),
        user_agent=user_agent,
        ip_address=ip_address,
        auth_method="phone_otp",
    )
    return build_login_response(db, user, session_row)
