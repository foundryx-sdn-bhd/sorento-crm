"""Request-entry company-scope resolver (multi-company data isolation).

Foundation slice shipped the four-state filter + the fail-closed ``UNSET`` default
on every session (``get_db``). THIS module wires the real resolver so the app works
again: a FastAPI dependency (``apply_company_scope``) that runs for every
``/api/v1/*`` request (attached as a router-level dependency in ``app/main.py``),
computes the caller's scope, and stamps it onto the SAME session the routes use
(``get_db`` is cached per-request, so the ``Depends(get_db)`` here and in the route
resolve to one session instance - the scope set here is visible to the route's
queries).

Decision table (PLAN §3.9 / §3.11, four-state - see ``app/models/base.py``):

  Principal                                   | Scope
  --------------------------------------------|-----------------------------------
  JWT / session user                          | frozenset({active_company})
    active = claim ∈ grants                    |   where active is resolved by:
           → users.last_active_company_id      |   1) JWT active_company_id claim if
           → sole grant                        |      present AND in the user's grants
           → last_active (backfill fallback)   |   2) else last_active if set (+in grants)
                                                |   3) else the sole grant
                                                |   4) else last_active (always resolves
                                                |      post-backfill: all users → Sorento)
    superadmin/admin                           | grants = ALL companies (switch anywhere)
  X-API-Key + contact_id&space_id resolved     | frozenset(contact's company_ids)
  X-API-Key + contact params, contact/none     | frozenset()  (empty → 0 rows, AC-F3)
  X-API-Key, no contact params                 | None (all companies, backward-compat)
  Portal / public view token (header or ?token) | frozenset(contact's companies)
                                                |   → incumbent when it has none
  no principal / public / unauth               | UNSET (fail-closed, 0 rows)

Resilient by construction: ANY resolver exception → ``UNSET`` (fail-closed) + a
logged warning. The resolver never 500s a request.
"""
from __future__ import annotations

import hmac
import logging
from contextlib import contextmanager, nullcontext
from datetime import datetime
from typing import Optional, Tuple

from fastapi import Depends, Request
from jose import jwt
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.base import (
    UNSET,
    CompanyScope,
    company_scope,
    get_company_scope,
    set_brand_scope,
    set_company_scope,
)
from app.models.company import Company, UserCompany
from app.models.user import User
from app.models.user_session import UserSession

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Principal extraction                                                          #
# --------------------------------------------------------------------------- #
def _bearer_token(request: Request) -> Optional[str]:
    auth = request.headers.get("Authorization")
    if auth and auth.startswith("Bearer "):
        return auth.split(" ", 1)[1].strip() or None
    return None


def _api_key_valid(api_key: Optional[str]) -> bool:
    valid = getattr(settings, "external_api_key", None)
    return bool(api_key) and bool(valid) and hmac.compare_digest(str(api_key), str(valid))


def _resolve_user_id_and_claim(db: Session, token: str) -> Tuple[Optional[str], Optional[str]]:
    """Best-effort → (user_id, active_company_id claim). Never raises, never mutates.

    Tries a JWT decode first (future NextAuth ``active_company_id`` claim path);
    falls back to a READ-ONLY opaque-session-token lookup (no sliding/commit, so we
    don't double-write what ``get_current_user`` already handles).
    """
    user_id: Optional[str] = None
    claim: Optional[str] = None
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        uid = payload.get("sub") or payload.get("id")
        user_id = str(uid) if uid is not None else None
        raw_claim = payload.get("active_company_id")
        claim = str(raw_claim) if raw_claim else None
    except Exception:
        pass
    if not user_id:
        row = (
            db.query(UserSession.user_id, UserSession.revoked_at, UserSession.expires_at)
            .filter(UserSession.token == token)
            .first()
        )
        if row is not None and row[1] is None:  # not revoked
            expires_at = row[2]
            if expires_at is None or expires_at >= datetime.utcnow():
                user_id = str(row[0])
    return user_id, claim


# --------------------------------------------------------------------------- #
# Scope computation                                                             #
# --------------------------------------------------------------------------- #
def _all_company_ids(db: Session) -> set[str]:
    return {r[0] for r in db.query(Company.id).all()}


def _user_grant_ids(db: Session, user_id: str) -> set[str]:
    return {
        r[0]
        for r in db.query(UserCompany.company_id).filter(UserCompany.user_id == user_id).all()
    }


def resolve_user_grant_ids(db: Session, user_id: str) -> set[str]:
    """The companies ``user_id`` may see, superadmin/admin included.

    Mirrors the ``grants`` computation inside ``_resolve_user_scope`` - a
    superadmin/admin can switch into any company, so their grant set is every
    company, not just what ``user_companies`` happens to list. Exposed so a
    shared-attachment's linked-entity widening (PLAN-shared-brand-attachments.md
    S5) can query product/certificate links across the SAME companies the
    active-company switcher would offer this user, never more.
    """
    from app.services.user_service import UserPermissionService

    perm = UserPermissionService(db)
    is_super = bool(
        perm.get_user_role_slugs(user_id) & {UserPermissionService.SUPERADMIN_ROLE_SLUG, "admin"}
    )
    return _all_company_ids(db) if is_super else _user_grant_ids(db, user_id)


def _impersonation_target_id(db: Session, request: Request, real_user_id: str) -> Optional[str]:
    """Return a VALIDATED impersonation target id, or None.

    Mirrors ``dependencies._maybe_apply_impersonation``: the real user must be
    admin/superadmin AND an active ``ImpersonationSession`` (real→target) must
    exist. Without this, the scope resolver (a router dependency that runs BEFORE
    ``get_current_user``) would keep the admin's own scope while the rest of the
    request acts as the target - so an admin impersonating a Mocha user would
    still see all companies' data. We re-validate here rather than trust the raw
    header.
    """
    target_id = request.headers.get("X-Impersonate-User-Id")
    if not target_id:
        return None
    from app.services.user_service import UserPermissionService

    role_slugs = UserPermissionService(db).get_user_role_slugs(real_user_id)
    if not (role_slugs & {UserPermissionService.SUPERADMIN_ROLE_SLUG, "admin"}):
        return None
    from app.models.impersonation import ImpersonationSession

    row = (
        db.query(ImpersonationSession.id)
        .filter(
            ImpersonationSession.admin_user_id == real_user_id,
            ImpersonationSession.target_user_id == target_id,
            ImpersonationSession.ended_at.is_(None),
        )
        .first()
    )
    return str(target_id) if row is not None else None


def _resolve_user_scope(db: Session, token: str, request: Optional[Request] = None) -> CompanyScope:
    user_id, claim = _resolve_user_id_and_claim(db, token)
    if not user_id:
        return UNSET

    # Impersonation: scope as the TARGET user, not the real admin. The admin's own
    # ``active_company_id`` claim does not apply to the impersonated identity.
    if request is not None:
        target_id = _impersonation_target_id(db, request, user_id)
        if target_id:
            user_id, claim = target_id, None

    from app.services.user_service import UserPermissionService

    perm = UserPermissionService(db)
    is_super = bool(
        perm.get_user_role_slugs(user_id) & {UserPermissionService.SUPERADMIN_ROLE_SLUG, "admin"}
    )
    # Superadmin/admin can switch into ANY company; regular users only their grants.
    grants = _all_company_ids(db) if is_super else _user_grant_ids(db, user_id)

    last_row = (
        db.query(User.last_active_company_id).filter(User.id == user_id).first()
    )
    last_active = last_row[0] if last_row else None

    active: Optional[str] = None
    if claim and claim in grants:
        active = claim
    elif last_active and (is_super or last_active in grants):
        active = last_active
    elif len(grants) == 1:
        active = next(iter(grants))
    elif grants:
        # Fail-closed fallback: NEVER pick a non-granted company. A stale
        # ``last_active`` (e.g. left over after a grant was revoked) must not be
        # selectable - deterministically pick the lowest granted id instead.
        active = sorted(grants)[0]
    # else: no grants at all -> leave UNSET (fail-closed).

    return frozenset({active}) if active else UNSET


def resolve_contact_company_scope(
    db: Session, contact_id: Optional[str], space_id: Optional[str]
) -> frozenset:
    """The companies a `(contact_id, space_id)` pair may see. ALWAYS a frozenset.

    The contact-identity half of the X-API-Key rule above, as a plain function so a
    caller with no `Request` uses the SAME rule rather than a second lookup that would
    drift from it. Two callers today: `_resolve_api_key_scope` (the router dependency)
    and the chatbot turn engine, which calls resolver/stock/promotion code IN PROCESS
    and so never runs that dependency.

    Fail-closed by construction: a blank identity, a contact the CRM does not know, or
    a contact with no `respond_contact_companies` row all resolve to an EMPTY frozenset
    (0 owned rows, AC-F3), never to `None` (which would mean every company).
    """
    from app.services.contact_access_type_service import ContactAccessTypeService

    company_ids = ContactAccessTypeService(db).resolve_contact_company_ids(
        (contact_id or "").strip(), (space_id or "").strip()
    )
    return frozenset(company_ids)


def _resolve_api_key_scope(db: Session, request: Request) -> CompanyScope:
    contact_id = (request.query_params.get("contact_id") or "").strip()
    space_id = (request.query_params.get("space_id") or "").strip()
    if not contact_id or not space_id:
        return None  # no contact identity → all companies (backward-compat, AC-F1)

    # Empty frozenset ⇒ no contact matched OR contact has no memberships → 0 owned
    # rows (fail-closed, AC-F3). Never falls through to "all".
    return resolve_contact_company_scope(db, contact_id, space_id)


def _resolve_api_key_brand_scope(db: Session, request: Request):
    """CONTACT-BRAND-SCOPE: the calling contact's accessible brands, or None (unscoped).

    Only the X-API-Key + contact_id + space_id shape carries a contact; a staff session, a
    portal token or a key with no contact identity is untouched."""
    api_key = request.headers.get("X-API-Key")
    if not api_key or not _api_key_valid(api_key):
        return None
    contact_id = (request.query_params.get("contact_id") or "").strip()
    space_id = (request.query_params.get("space_id") or "").strip()
    if not contact_id or not space_id:
        return None
    from app.services.contact_brand_scope import contact_brand_scope

    return contact_brand_scope(db, contact_id, space_id)


def _portal_token_value(request: Request) -> Optional[str]:
    """The portal/public-view token, wherever that surface puts it.

    Two shapes in the wild: the public ``/view`` + approval links pass
    ``?token=``, while the submission portal (``/portal/c/<slug>/...``) sends
    ``X-Portal-Token`` (the token lives in localStorage, never in the URL).
    Both must resolve a scope; checking only the query param left the whole
    slug portal fail-closed.
    """
    header = (request.headers.get("X-Portal-Token") or "").strip()
    if header:
        return header
    return (request.query_params.get("token") or "").strip() or None


def _portal_token_scope(request: Request, db: Session) -> Optional[CompanyScope]:
    """Scope for the contact-facing surfaces, which authenticate with a portal
    token instead of a JWT or an API key.

    Those requests carry no Bearer and no X-API-Key, so they used to fall through
    to ``UNSET`` (fail-closed) - which silently emptied every company-scoped read
    on the portal: the debtor lookup, the delivery-order picker and any owned
    table behind the submission form returned zero rows.

    The scope is the TOKEN'S OWN CONTACT companies (``respond_contact_companies``,
    the admin-managed M2M shown as "Companies" on Edit Contact) - the same
    identity rule the X-API-Key path uses, so a Sorento contact never sees another
    company's delivery orders. A contact with no company rows falls back to the
    incumbent company rather than to zero rows: every pre-multi-company row
    carries the incumbent id, and a contact-facing form that silently shows an
    empty customer list is worse than showing the legacy default.

    Returns None when this is not a portal request (caller keeps its own
    fallback).
    """
    path = request.url.path or ""
    if "/public/" not in path:
        return None
    raw = _portal_token_value(request)
    if not raw:
        return None

    from app.services.company_scope import DEFAULT_COMPANY_ID

    try:
        from app.models.company import RespondContactCompany
        from app.models.portal import PortalToken

        row = (
            db.query(PortalToken.contact_id)
            .filter(PortalToken.token == raw)
            .first()
        )
        if row and row[0]:
            company_ids = {
                str(cid)
                for (cid,) in db.query(RespondContactCompany.company_id)
                .filter(RespondContactCompany.respond_contact_id == str(row[0]))
                .all()
                if cid
            }
            if company_ids:
                return frozenset(company_ids)
    except Exception as exc:  # noqa: BLE001 - resolver must never 500
        logger.warning("portal token company resolve failed: %s", exc)

    # Unknown / unverified token, or a contact with no company membership: the
    # route's own auth still 401s an invalid token, so this is not an
    # authorisation decision - only which company's rows a valid one reads.
    return frozenset({DEFAULT_COMPANY_ID})


def resolve_company_scope(request: Request, db: Session) -> CompanyScope:
    """Pure resolver (no session mutation) - exposed for tests. Never raises."""
    api_key = request.headers.get("X-API-Key")
    if api_key:
        if _api_key_valid(api_key):
            return _resolve_api_key_scope(db, request)
        return UNSET  # invalid key - the route's own auth will 401; stay fail-closed
    token = _bearer_token(request)
    if token:
        return _resolve_user_scope(db, token, request)
    portal_scope = _portal_token_scope(request, db)
    if portal_scope is not None:
        return portal_scope
    return UNSET


# --------------------------------------------------------------------------- #
# FastAPI dependency                                                            #
# --------------------------------------------------------------------------- #
async def apply_company_scope(
    request: Request, db: Session = Depends(get_db)
) -> CompanyScope:
    """Router-level dependency: resolve + stamp the company scope onto the request
    session so the ``do_orm_execute`` filter enforces isolation for every route."""
    brand_scope = None
    try:
        scope = resolve_company_scope(request, db)
        brand_scope = _resolve_api_key_brand_scope(db, request)
    except Exception as exc:  # noqa: BLE001 - resolver must never 500 the request
        logger.warning("company scope resolver failed; falling back to UNSET (fail-closed): %s", exc)
        scope = UNSET
    # ``db`` is always a real Session in production (from get_db). Guard only for
    # test doubles that override get_db with a non-Session (a bare generator or
    # None): stamping is a no-op there, and this router-level dependency must never
    # 500 a request just because it could not attach the scope.
    if not hasattr(db, "info"):
        return scope
    set_company_scope(db, scope)
    set_brand_scope(db, brand_scope)
    return scope


# --------------------------------------------------------------------------- #
# Grants widening (CONTACT-COMPANYLESS)                                         #
# --------------------------------------------------------------------------- #
def grants_scope_value(db: Session, user_id: str) -> CompanyScope:
    """The active scope plus every company ``user_id`` is granted (never less than today)."""
    current = get_company_scope(db)
    if current is None:
        return None
    base = current if isinstance(current, frozenset) else frozenset()
    return base | frozenset(resolve_user_grant_ids(db, str(user_id)))


@contextmanager
def grants_scope(db: Session, user_id: str):
    """Run a read under the active scope widened to every company ``user_id`` is granted.

    For pages that are not about one company (the contact detail page). Restores the
    prior scope on exit. The active scope came from the same grants, so this never
    reaches beyond them.
    """
    with company_scope(db, grants_scope_value(db, user_id)):
        yield db


def grants_requested(request: Request, current_user: dict, flag: Optional[str]) -> bool:
    """True for a staff session that sent ``?company_scope=grants``.

    An integration / X-API-Key caller ignores the flag and keeps the scope it was resolved
    with. ``current_user`` is the EFFECTIVE user (the impersonated one during view-as), so
    the widening follows the grants of whoever the request acts as.
    """
    if flag != "grants" or request.headers.get("X-API-Key"):
        return False
    return current_user.get("auth_method") not in {"api_key", "integration_api_key"}


def widen_if_requested(db: Session, request: Request, current_user: dict, flag: Optional[str]):
    """``grants_scope`` when ``grants_requested``, else a no-op context."""
    if grants_requested(request, current_user, flag):
        return grants_scope(db, current_user["id"])
    return nullcontext(db)


def company_name_map(db: Session) -> dict[str, str]:
    """``company id -> name`` for tagging rows read under ``grants_scope``."""
    return {str(r[0]): r[1] for r in db.query(Company.id, Company.name).all()}


def tag_company(rows, names: dict[str, str]) -> None:
    """Give each ORM row or dict a ``company_name`` next to its ``company_id``."""
    for row in rows:
        cid = row.get("company_id") if isinstance(row, dict) else getattr(row, "company_id", None)
        name = names.get(str(cid)) if cid else None
        if isinstance(row, dict):
            row["company_name"] = name
        else:
            setattr(row, "company_name", name)
