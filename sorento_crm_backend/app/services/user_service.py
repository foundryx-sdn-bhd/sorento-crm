"""User management service for business logic."""
import secrets
from fastapi import HTTPException
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import or_, func
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.exc import IntegrityError
from typing import Optional
from datetime import datetime
from app.models.user import (
    User,
    UserStatus,
    UserRole,
    UserRoleAssignment,
    UserPermission,
    UserRolePermission,
    SystemLog,
    UserQuickAccess,
    SystemSetting,
)
from app.models.access import (
    AccessAgent,
    ContactAgentAccess,
    Team,
    TeamMember,
    AgentTeam,
    AgentTeamRoundRobinCursor,
)
from app.schemas.user import (
    UserCreate, UserUpdate, UserRoleCreate, UserRoleUpdate,
    UserPermissionCreate, UserPermissionUpdate, AccessAgentCreate, AccessAgentUpdate,
    ContactAgentAccessCreate, ContactAgentAccessUpdate,
    TeamCreate, TeamUpdate,
)
from app.services.error_handler import AppException, handle_not_found, handle_conflict, handle_validation_error

# NOTE: RespondClient is imported lazily inside the two methods that use it.
# A module-level `from app.services.integration_service import RespondClient`
# creates a circular import when the worker entrypoint imports
# app.scheduler.task_scheduler -> integration_service -> app.models ->
# modules.runtime.guards -> app.dependencies -> user_service -> (back to
# partially-initialized integration_service), which silently killed APScheduler
# in the worker container and left email_outbox rows pending forever.


def _normalize_respond_user_id(value: Optional[str]) -> Optional[str]:
    """Return stripped string or None if empty."""
    if value is None:
        return None
    s = (str(value)).strip()
    return s if s else None


def _normalize_email_for_storage(value: Optional[str]) -> str:
    """Lowercase trimmed email for comparison and storage."""
    if value is None:
        return ""
    return str(value).strip().lower()


def normalize_email(value: Optional[str]) -> Optional[str]:
    """The stored form of an email (identity S0, AC-03): trimmed, lowercased; blank is NULL."""
    return _normalize_email_for_storage(value) or None


def user_label(user) -> str:
    """A person's name for a message: name, else email, else "another user".
    Never an id, and never a phone number (a 409 must not disclose one)."""
    for value in (getattr(user, "name", None), getattr(user, "email", None)):
        if value and str(value).strip():
            return str(value).strip()
    return "another user"


def _rr_user_id_key(value: Optional[object]) -> str:
    """
    Canonical string key for round-robin user id comparisons.
    Cursor.last_assigned_user_id is a string FK; TeamMember.user_id may come back as UUID or str
    from the driver - mixing them breaks list.index() and stuck rotation on index 0.
    """
    if value is None:
        return ""
    return str(value).strip()


def _rr_next_index(user_ids: list, last_assigned_user_id: Optional[object]) -> int:
    """Which member the cursor points at NEXT: after `last_assigned_user_id`, wrapping.

    Pure arithmetic over the two values, extracted so the LIVE draw and the DRY-RUN preview
    run the same rotation and cannot answer differently. An unknown or absent last id starts
    the pool from the top, which is what a fresh cursor means.
    """
    last_key = _rr_user_id_key(last_assigned_user_id) if last_assigned_user_id is not None else ""
    try:
        idx = user_ids.index(last_key) if last_key else -1
    except ValueError:
        idx = -1
    return (idx + 1) % len(user_ids)


# --------------------------------------------------------------- brand routing
#
# Brand is the second routing axis, orthogonal to company: Cabana and Mocha are
# brands INSIDE the Sorento company. ONE team set per function with ONE team per
# tier; the brand narrows the MEMBER POOL inside that team, exactly the way market
# segments already do. A member tagged with no brand serves every brand, and when
# no member carries the resolved brand the whole team round-robins - so routing
# never dead-ends on a brand nobody tagged.


# The suffixed team-set codes that used to encode the brand. One release of
# compatibility for an n8n workflow that has not been updated yet - migration 371
# rewrites the stored trackers, this map keeps the WIRE working while it rolls out.
LEGACY_BRAND_TEAM_SET_CODES = {
    "marketing_promotion_sorento": ("marketing_promotion", "sorento"),
    "marketing_promotion_mocha": ("marketing_promotion", "mocha"),
    "marketing_promotion_cabana": ("marketing_promotion", "cabana"),
}


def normalise_brand_code(value: Optional[object]) -> Optional[str]:
    """Canonical brand handle: trimmed lower-case, blank -> None (= all brands).

    Every read and write goes through this, so a row saved as "Mocha" and a
    request carrying "MOCHA" are the same brand.
    """
    if value is None:
        return None
    s = str(value).strip().lower()
    return s or None


def split_legacy_team_set_code(code: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """``("marketing_promotion_mocha")`` -> ``("marketing_promotion", "mocha")``.

    Anything not in the legacy map is returned unchanged with no brand, so this is
    safe to call on every incoming team-set code.
    """
    if code is None:
        return None, None
    c = str(code).strip()
    # Matched case-insensitively: the suffix is a handle typed into an n8n workflow,
    # and "Marketing_Promotion_Mocha" is the same routing key as the lower-case one.
    if c.lower() in LEGACY_BRAND_TEAM_SET_CODES:
        return LEGACY_BRAND_TEAM_SET_CODES[c.lower()]
    return c, None


def brand_pool_key(brand_code: Optional[str]) -> str:
    """Round-robin cursor suffix for a brand-narrowed pool ('' when it did not narrow).

    Appended to the market-segment key so the two axes compose into ONE cursor key:
    a mocha pool and a retail pool and a mocha+retail pool each rotate on their own
    cursor, and the legacy '' cursor is untouched when neither filter applied.
    """
    code = normalise_brand_code(brand_code)
    return f"~b:{code}" if code else ""


def member_serves_brand(member_brand_codes, brand_code: Optional[str]) -> bool:
    """The member-level matching rule: tagged with this brand, or tagged with nothing.

    Mirrors the market-segment rule one-for-one - an untagged member serves every
    brand, which is what keeps a minimally-configured team routable.
    """
    wanted = normalise_brand_code(brand_code)
    if not wanted:
        return True
    codes = {normalise_brand_code(c) for c in (member_brand_codes or [])}
    codes.discard(None)
    return not codes or wanted in codes



# Roles that do not make someone staff: a portal contact signs in with `portal_user` (or no
# role at all, `user_contact_link.py`), and `guest` grants nothing. The shared people lookup
# answers staff and lists staff (NS-SHARED-LOOKUPS).
NON_STAFF_ROLE_SLUGS = frozenset({"portal_user", "guest"})


def _holds_staff_role():
    return User.role_assignments.any(
        UserRoleAssignment.role.has(UserRole.slug.notin_(NON_STAFF_ROLE_SLUGS))
    )

class UserService:
    """Service for user operations."""
    
    def __init__(self, db: Session):
        self.db = db

    def _resolve_crm_base_url(self) -> str:
        """Public CRM URL for sign-in links (env frontend_base_url, else system website_url)."""
        from app.config import settings as app_settings

        base = (getattr(app_settings, "frontend_base_url", None) or "").strip().rstrip("/")
        if base:
            return base
        row = self.db.query(SystemSetting).first()
        if row and getattr(row, "website_url", None):
            return str(row.website_url).strip().rstrip("/")
        return ""

    def _queue_email_address_change_notification(
        self,
        *,
        user_id: str,
        old_email: str,
        new_email: str,
    ) -> None:
        """Queue outgoing mail to the new address via notification deliveries."""
        base = self._resolve_crm_base_url()
        from app.services.email_template_service import EmailTemplateService

        rendered = EmailTemplateService(self.db).render_code(
            "account_email_changed",
            {"old_email": old_email, "new_email": new_email, "sign_in_link": base or ""},
        )
        from app.services.notification_service import NotificationService

        NotificationService(self.db).create_with_channel_preferences(
            user_id=user_id,
            type="account_email_changed",
            title=rendered["subject"],
            body=rendered["body_text"],
            data={"body_html": rendered["body_html"]},
            source_entity_type="user",
            source_entity_id=f"email_change:{user_id}:{secrets.token_hex(8)}",
            event_type="email_changed",
            send_in_app=False,
            send_email=True,
            send_web_push=False,
        )

    def _has_table(self, table_name: str) -> bool:
        """Return True if table exists in current DB schema."""
        try:
            bind = self.db.get_bind()
            return sa_inspect(bind).has_table(table_name)
        except Exception:
            # If we can't inspect for any reason, assume it exists and let SQL errors surface elsewhere.
            return True

    def _prepare_user_for_hard_delete(self, user_id: str) -> None:
        """
        Best-effort cleanup before hard delete.

        Some tables reference users without ON DELETE CASCADE (e.g. system_logs, self-referential superior_id),
        so we clean those up explicitly to avoid 500 IntegrityError.
        """
        # Break self-referential FK (users.superior_id -> users.id)
        self.db.query(User).filter(User.superior_id == user_id).update(
            {"superior_id": None}, synchronize_session=False
        )

        # Remove non-cascading refs
        if self._has_table(SystemLog.__tablename__):
            self.db.query(SystemLog).filter(SystemLog.user_id == user_id).delete(
                synchronize_session=False
            )

        # Defensive deletes even if FK cascades exist (keeps behavior consistent if DB constraints differ)
        if self._has_table(UserRoleAssignment.__tablename__):
            self.db.query(UserRoleAssignment).filter(UserRoleAssignment.user_id == user_id).delete(
                synchronize_session=False
            )
        if self._has_table(TeamMember.__tablename__):
            self.db.query(TeamMember).filter(TeamMember.user_id == user_id).delete(
                synchronize_session=False
            )
        if self._has_table(UserQuickAccess.__tablename__):
            self.db.query(UserQuickAccess).filter(UserQuickAccess.user_id == user_id).delete(
                synchronize_session=False
            )

        # SET NULL modeled, but update defensively (cursor table might be missing FK constraint in some envs)
        if self._has_table(AgentTeamRoundRobinCursor.__tablename__):
            self.db.query(AgentTeamRoundRobinCursor).filter(
                AgentTeamRoundRobinCursor.last_assigned_user_id == user_id
            ).update({"last_assigned_user_id": None}, synchronize_session=False)
    
    def list_users(
        self,
        page: int = 1,
        limit: int = 50,
        query: Optional[str] = None,
        status: Optional[str] = None,
        role_id: Optional[str] = None,
        respond_synced: Optional[str] = None,
        trashed: Optional[str] = None,
        tier: Optional[str] = None,
        sort_field: str = "created_at",
        sort_dir: str = "asc"
    ):
        """List users. trashed: 'exclude' (default), 'only', or 'all'. tier: comma-separated tier levels, e.g. '1,2,3'."""
        q = self.db.query(User)
        if trashed == "only":
            q = q.filter(User.is_trashed == True)
        elif trashed != "all":
            q = q.filter(User.is_trashed == False)
        filters = []
        if status and status != "all":
            filters.append(User.status == status)
        if role_id and role_id != "all":
            q = q.join(UserRoleAssignment, UserRoleAssignment.user_id == User.id)
            q = q.filter(UserRoleAssignment.role_id == role_id)
            q = q.distinct()

        if respond_synced:
            filters.append(User.respond_synced == respond_synced)

        if tier and tier.strip():
            tier_values = []
            for s in tier.strip().split(","):
                s = s.strip()
                if s:
                    try:
                        tier_values.append(int(s))
                    except ValueError:
                        pass
            if tier_values:
                filters.append(User.tier.in_(tier_values))

        if query:
            filters.append(
                or_(
                    User.name.ilike(f"%{query}%"),
                    User.email.ilike(f"%{query}%")
                )
            )
        
        if filters:
            from sqlalchemy import and_
            q = q.filter(and_(*filters))
        
        sort_map = {
            "name": User.name,
            "status": User.status,
            "created_at": User.created_at,
            "createdAt": User.created_at,  # Support camelCase from frontend
            "last_sign_in_at": User.last_sign_in_at,
            "lastSignInAt": User.last_sign_in_at,  # Support camelCase from frontend
        }
        sort_column = sort_map.get(sort_field, User.created_at)
        if sort_dir == "desc":
            q = q.order_by(sort_column.desc())
        else:
            q = q.order_by(sort_column.asc())
        
        # Count total (joinedload doesn't affect count)
        total = q.count()
        
        # Apply pagination and fetch with eager loading
        offset = (page - 1) * limit
        users = q.offset(offset).limit(limit).all()
        
        return {
            "data": users,
            "pagination": {"total": total, "page": page, "limit": limit},
            "empty": total == 0
        }

    def is_staff(self, user_id: str) -> bool:
        """Holds a role other than the portal / guest ones, and is not an integration account."""
        return (
            self.db.query(User.id)
            .filter(User.id == str(user_id), User.is_integration == False, _holds_staff_role())  # noqa: E712
            .first()
            is not None
        )

    def list_user_lookup(
        self,
        query: Optional[str] = None,
        respond_synced: bool = False,
        include_inactive: bool = False,
    ) -> list:
        """Active, non-trashed users for the shared people picker, ordered by name.

        Staff only: a portal contact (no role, or only ``portal_user`` / ``guest``) and an
        integration act-as account are not people anyone assigns work to.
        ``query`` matches the NAME only: this list is open to every signed-in user, and
        matching email would let anyone probe whether an address belongs to a colleague.
        ``respond_synced`` narrows to users linked to a Respond.io agent.
        ``include_inactive`` is for filters over past records (who raised it, who it was
        assigned to): someone who has left still owns those rows. Trashed users stay out.
        """
        q = self.db.query(User).filter(
            User.is_trashed == False,  # noqa: E712
            User.is_integration == False,  # noqa: E712
            _holds_staff_role(),
        )
        if not include_inactive:
            q = q.filter(User.status == UserStatus.ACTIVE.value)
        if query:
            q = q.filter(User.name.ilike(f"%{query}%"))
        if respond_synced:
            q = q.filter(User.respond_synced == "successful", User.respond_user_id.isnot(None))
        return q.order_by(User.name.asc().nullslast(), User.id.asc()).all()

    def list_users_select(
        self,
        query: Optional[str] = None,
        respond_synced: Optional[str] = None,
        status: Optional[str] = None,
        trashed: str = "exclude",
        company_id: Optional[str] = None,
        phone: Optional[str] = None,
        respond_contact_id: Optional[str] = None,
        unlinked: Optional[bool] = None,
    ):
        """List users for select dropdowns. Defaults to non-trashed only.

        ``company_id`` narrows to users holding a grant for that company. Users are
        SHARED across companies, so the default stays unfiltered; the team-member
        picker passes it because team membership requires the grant (AC-G1) - and
        offering a user who cannot be added is just an error waiting to happen.

        ``phone``/``respond_contact_id``/``unlinked`` (S3 1.7): resolve the OTHER
        user a 409 named ("Open user" / "Link this contact to <name> instead"),
        or offer users with no linked contact ("Link existing user").
        """
        q = self.db.query(User)

        if company_id:
            from app.models.company import UserCompany

            q = q.join(UserCompany, UserCompany.user_id == User.id).filter(
                UserCompany.company_id == str(company_id)
            )

        if trashed == "only":
            q = q.filter(User.is_trashed == True)
        elif trashed != "all":
            q = q.filter(User.is_trashed == False)

        filters = []
        if status and status != "all":
            filters.append(User.status == status)
        if respond_synced:
            filters.append(User.respond_synced == respond_synced)
        if query:
            filters.append(
                or_(
                    User.name.ilike(f"%{query}%"),
                    User.email.ilike(f"%{query}%")
                )
            )
        if phone:
            from app.services.phone_utils import normalize_msisdn

            normalized_phone = normalize_msisdn(phone)
            if normalized_phone is None:
                # Junk input normalises to nothing usable - answer no rows.
                # `User.contact_number == None` would otherwise compile to
                # `IS NULL` and match every PHONELESS user instead.
                return []
            filters.append(User.contact_number == normalized_phone)
        if respond_contact_id:
            filters.append(User.respond_contact_id == respond_contact_id)
        if unlinked:
            filters.append(User.respond_contact_id.is_(None))

        if filters:
            from sqlalchemy import and_
            q = q.filter(and_(*filters))

        return q.order_by(User.name.asc()).all()
    
    def get_user(self, user_id: str):
        """Get a user by ID."""
        user = self.db.query(User).options(
            joinedload(User.superior)
        ).filter(User.id == user_id).first()
        if not user:
            raise handle_not_found("User", user_id)
        return user

    def _users_with_respond_user_id(
        self, respond_user_id: str, exclude_user_id: Optional[str] = None
    ) -> list:
        """
        Return list of users (other than exclude_user_id) that have this respond_user_id.
        Compares using trimmed value so "971724" matches " 971724 " in DB.
        Each item is a dict with keys: id, name, email.
        """
        q = self.db.query(User).filter(
            User.respond_user_id.isnot(None),
            func.trim(User.respond_user_id) == respond_user_id,
        )
        if exclude_user_id:
            q = q.filter(User.id != exclude_user_id)
        users = q.all()
        return [
            {
                "id": u.id,
                "name": getattr(u, "name", None) or "",
                "email": getattr(u, "email", None) or "",
            }
            for u in users
        ]

    def _check_respond_user_id_unique(
        self, respond_user_id: str, exclude_user_id: Optional[str] = None
    ) -> None:
        """
        Raise handle_conflict if respond_user_id is already used by another user.
        Message includes which users have it (name and email).
        """
        existing = self._users_with_respond_user_id(respond_user_id, exclude_user_id=exclude_user_id)
        if not existing:
            return
        parts = [
            (f"{u['name']} ({u['email']})" if u["name"] and u["email"] else (u["name"] or u["email"] or "another user"))
            for u in existing
        ]
        msg = "Respond User ID is already used by: " + "; ".join(parts)
        raise handle_conflict(msg)

    def _check_contact_number_unique(self, contact_number: str, exclude_user_id: Optional[str]) -> None:
        """409 PHONE_BELONGS_TO_USER when another user already holds this phone
        (S3 1.2/1.3). Expects normalised E.164 digits. Names the holder
        (`user_label`), never their id or the phone itself (AC-42)."""
        q = self.db.query(User).filter(User.contact_number == contact_number)
        if exclude_user_id:
            q = q.filter(User.id != exclude_user_id)
        existing = q.first()
        if existing:
            raise AppException(
                status_code=409,
                message=f"This phone already belongs to {user_label(existing)}",
                code="PHONE_BELONGS_TO_USER",
            )

    def _check_email_free(self, email: Optional[str], exclude_user_id: Optional[str]) -> None:
        """409 EMAIL_TAKEN when another user holds this email in any case (AC-03)."""
        if not email:
            return
        q = self.db.query(User).filter(func.lower(User.email) == email.lower())
        if exclude_user_id:
            q = q.filter(User.id != exclude_user_id)
        other = q.first()
        if other is not None:
            raise AppException(
                status_code=409,
                message=f"Email already belongs to {user_label(other)}",
                code="EMAIL_TAKEN",
            )

    def _check_contact_free(self, respond_contact_id: Optional[str], exclude_user_id: Optional[str]) -> None:
        """409 CONTACT_ALREADY_LINKED when another user holds this WhatsApp contact (AC-01)."""
        if not respond_contact_id:
            return
        q = self.db.query(User).filter(User.respond_contact_id == respond_contact_id)
        if exclude_user_id:
            q = q.filter(User.id != exclude_user_id)
        other = q.first()
        if other is not None:
            raise AppException(
                status_code=409,
                message=f"WhatsApp contact already linked to {user_label(other)}",
                code="CONTACT_ALREADY_LINKED",
            )

    def _audit_contact_link(self, user_id: str, description: str) -> None:
        """One `log_audit` UPDATE row for a create-from-contact, link, or unlink
        (S3 1.2 step 10, 1.3, 1.4), its description naming the contact. Best-effort
        in its OWN transaction, after the caller's own commit: an audit-write failure
        must never undo the link itself.

        `log_audit` derives `real_user_id`/`actor_type` from the ambient actor
        automatically, but NOT its `user_id` column - that is only ever whatever
        a caller passes explicitly. Read here so the row's `user_id` also names
        whoever is stamped (the deferred `user.unlink_contact` executor runs
        inside `actor_scope`, so this is the requester, not the sweep/commit).
        """
        import logging

        from app.audit_context import get_actor

        logger = logging.getLogger(__name__)
        try:
            from app.services.audit_service import log_audit

            actor = get_actor(self.db)
            log_audit(
                self.db,
                "user",
                user_id,
                "UPDATE",
                description=description,
                user_id=actor.user_id if actor else None,
            )
            self.db.commit()
        except Exception as e:
            self.db.rollback()
            logger.warning("Failed to write the contact-link audit row for user %s: %s", user_id, e)

    def _other_user(self, condition, exclude_user_id: Optional[str]):
        q = self.db.query(User).filter(condition)
        if exclude_user_id:
            q = q.filter(User.id != exclude_user_id)
        return q.first()

    def _raise_identity_conflict(self, exc: IntegrityError, *, email: Optional[str],
                                 respond_contact_id: Optional[str], exclude_user_id: Optional[str]) -> None:
        """A write that passed the pre-checks but lost the race to the unique index
        (a concurrent write in between) is the same 409, not a 500. Re-raises
        anything else."""
        self.db.rollback()
        detail = str(getattr(exc, "orig", exc))
        if "uq_users_email_lower" in detail and email:
            other = self._other_user(func.lower(User.email) == email.lower(), exclude_user_id)
            raise AppException(
                status_code=409,
                message=f"Email already belongs to {user_label(other)}",
                code="EMAIL_TAKEN",
            ) from exc
        if "uq_users_respond_contact_id" in detail and respond_contact_id:
            other = self._other_user(User.respond_contact_id == respond_contact_id, exclude_user_id)
            raise AppException(
                status_code=409,
                message=f"WhatsApp contact already linked to {user_label(other)}",
                code="CONTACT_ALREADY_LINKED",
            ) from exc
        raise exc

    def _user_create_data(self, user_data: UserCreate) -> dict:
        """Build User model dict from UserCreate, excluding role_ids/company_ids."""
        d = user_data.model_dump(exclude={"role_ids", "company_ids"})
        return d

    def _grant_companies(self, user, company_ids: Optional[list[str]]) -> None:
        """Grant the user access to the given companies (skipping unknown ids).

        The FIRST id becomes the user's landing default (last_active_company_id) -
        S3 1.2 widens this from "only when there is exactly one". Caller commits.
        """
        from app.models.company import Company, UserCompany
        cids = [
            c
            for c in (company_ids or [])
            if self.db.query(Company).filter(Company.id == c).first()
        ]
        for cid in cids:
            self.db.add(UserCompany(company_id=cid, user_id=user.id))
        if cids:
            user.last_active_company_id = cids[0]

    def create_user(self, user_data: UserCreate):
        """Create a new user and assign roles via user_role_assignments (S3 1.2).

        The owner may create from a WhatsApp contact (`respond_contact_id`): the
        contact's own phone wins over anything in the body, the server alone
        decides status/password, and nothing is ever sent (AC-41, AC-56).
        """
        from app.models.access import RespondContact
        from app.services.phone_utils import normalize_msisdn

        data = self._user_create_data(user_data)
        data["email"] = normalize_email(data.get("email"))

        respond_contact_id = data.get("respond_contact_id")
        contact = None
        if respond_contact_id:
            contact = (
                self.db.query(RespondContact)
                .filter(RespondContact.id == respond_contact_id)
                .first()
            )
            if contact is None:
                raise handle_not_found("Contact", respond_contact_id)
            self._check_contact_free(respond_contact_id, exclude_user_id=None)
            # The contact's own phone wins; a phone in the body is ignored.
            data["contact_number"] = normalize_msisdn(contact.phone_number)

        if not data.get("email") and not data.get("contact_number"):
            raise AppException(
                status_code=422,
                message="Enter an email or a phone number",
                code="EMAIL_OR_PHONE_REQUIRED",
            )

        self._check_email_free(data["email"], exclude_user_id=None)
        rid = _normalize_respond_user_id(data.get("respond_user_id"))
        if rid:
            self._check_respond_user_id_unique(rid, exclude_user_id=None)
            data["respond_user_id"] = rid
        if data.get("contact_number"):
            self._check_contact_number_unique(data["contact_number"], exclude_user_id=None)

        # The server decides status and password; anything the body sent for
        # either is ignored (AC-41).
        data["status"] = "ACTIVE" if data.get("contact_number") else "INACTIVE"
        data["password"] = None

        try:
            user = User(**data)
            self.db.add(user)
            self.db.flush()
            role_ids = user_data.role_ids
            if not role_ids:
                default_role = self.db.query(UserRole).filter(UserRole.is_default == True).first()
                if default_role:
                    role_ids = [default_role.id]
            for role_id in role_ids or []:
                role = self.db.query(UserRole).filter(UserRole.id == role_id).first()
                if role:
                    self.db.add(UserRoleAssignment(user_id=user.id, role_id=role_id))
            self._grant_companies(user, user_data.company_ids)
            self.db.commit()
        except IntegrityError as exc:
            self._raise_identity_conflict(
                exc, email=data.get("email"), respond_contact_id=data.get("respond_contact_id"), exclude_user_id=None
            )
        self.db.refresh(user)

        if contact is not None:
            self._audit_contact_link(
                user.id,
                f"Created from WhatsApp contact {contact.name or contact.phone_number}",
            )
        return user

    def invite_user(self, user_data: UserCreate, invited_by_user_id: str):
        """Create a user without a password and mark them as invited.

        The onboarding provisioning task (`app/tasks/onboarding_tasks.py`) is the
        one caller left: the plan removed the `POST /users/invite` ROUTE and its
        Next proxy (AC-58, the create-and-email path from the Add user modal is
        gone), not this method - onboarding still creates-and-invites in one step
        for an approved person the reviewer already saw.
        """
        data = self._user_create_data(user_data)
        data["email"] = normalize_email(data.get("email"))
        self._check_email_free(data["email"], exclude_user_id=None)
        self._check_contact_free(data.get("respond_contact_id"), exclude_user_id=None)
        data["password"] = None
        data["invited_by_user_id"] = invited_by_user_id
        data["status"] = "INACTIVE"
        try:
            user = User(**data)
            self.db.add(user)
            self.db.flush()
            role_ids = user_data.role_ids
            if not role_ids:
                default_role = self.db.query(UserRole).filter(UserRole.is_default == True).first()
                if default_role:
                    role_ids = [default_role.id]
            for role_id in role_ids or []:
                role = self.db.query(UserRole).filter(UserRole.id == role_id).first()
                if role:
                    self.db.add(UserRoleAssignment(user_id=user.id, role_id=role_id))
            self._grant_companies(user, user_data.company_ids)
            self.db.commit()
        except IntegrityError as exc:
            self._raise_identity_conflict(
                exc, email=data.get("email"), respond_contact_id=data.get("respond_contact_id"), exclude_user_id=None
            )
        self.db.refresh(user)
        return user

    def update_user(self, user_id: str, user_data: UserUpdate):
        """Update a user, including the S3 1.3 contact-link and phone rules."""
        import logging
        logger = logging.getLogger(__name__)

        user = self.get_user(user_id)

        # Get all fields that were explicitly set in the request
        update_data = user_data.model_dump(exclude_unset=True)
        logger.info(f"Updating user {user_id} with data: {update_data}")

        # Lost phone (identity S1, plan 5.3): captured BEFORE the field loop
        # below overwrites it, so a genuine change can be told apart from a
        # PUT that merely repeats the same number.
        old_contact_number = user.contact_number

        # Product-discontinued scopes are rows, not a column: pulled out of the
        # field loop below and replaced wholesale. Omitted leaves them untouched;
        # an empty list clears them (and the user then hears nothing).
        scopes_provided = "product_discontinued_scopes" in update_data
        scope_items = update_data.pop("product_discontinued_scopes", None)

        old_email_for_notification: Optional[str] = None
        email_changed = False
        if "email" in update_data:
            new_email = update_data.pop("email")
            new_email = normalize_email(new_email)
            if new_email is None:
                pass  # treat as no email field
            else:
                current_norm = _normalize_email_for_storage(user.email)
                if new_email != current_norm:
                    self._check_email_free(new_email, exclude_user_id=user_id)
                    old_email_for_notification = (user.email or "").strip() or user.email
                    user.email = new_email
                    user.email_verified_at = None
                    email_changed = True

        # --- WhatsApp contact link / unlink / switch (S3 1.3) ---------------
        # Handled entirely here and popped out of update_data so the generic
        # field loop below never touches it a second time.
        contact_audit: Optional[str] = None  # the audit description
        should_unlink = False
        if "respond_contact_id" in update_data:
            from app.models.access import RespondContact

            raw_new_id = update_data.pop("respond_contact_id")
            new_contact_id = raw_new_id or None  # "" and None both mean "unlink"
            current_contact_id = user.respond_contact_id

            if new_contact_id and new_contact_id != current_contact_id:
                contact = (
                    self.db.query(RespondContact)
                    .filter(RespondContact.id == new_contact_id)
                    .first()
                )
                if contact is None:
                    raise handle_not_found("Contact", new_contact_id)
                self._check_contact_free(new_contact_id, exclude_user_id=user_id)
                if current_contact_id:
                    # Already linked to a DIFFERENT contact - the owner unlinks
                    # first (AC-43). Nothing changes.
                    existing = (
                        self.db.query(RespondContact)
                        .filter(RespondContact.id == current_contact_id)
                        .first()
                    )
                    raise AppException(
                        status_code=409,
                        message=(
                            f"{user_label(user)} is already linked to WhatsApp contact "
                            f"{existing.name if existing else 'another contact'}"
                        ),
                        code="USER_ALREADY_LINKED",
                    )
                # First link: saved, no role added, nothing sent, sessions NOT
                # revoked (owner ruling, AC-54).
                user.respond_contact_id = new_contact_id
                contact_audit = f"Linked WhatsApp contact {contact.name or contact.phone_number}"
            elif not new_contact_id and current_contact_id:
                # `unlink_contact` is the one implementation (S3 1.4) - it does
                # its own commit, session revoke and audit row, run AFTER the
                # rest of this PUT's fields commit below.
                should_unlink = True
            # else: unchanged value, or unlink of an already-unlinked user - no-op.

        # Enforce Respond User ID uniqueness before applying any updates
        if "respond_user_id" in update_data:
            rid = _normalize_respond_user_id(update_data["respond_user_id"])
            if rid:
                self._check_respond_user_id_unique(rid, exclude_user_id=user_id)

        # Enforce phone uniqueness (one phone == one user). Value is already
        # E.164-normalised by the schema validator (blank/null both come through
        # as None). Only a value that actually DIFFERS from today's counts as a
        # change (AC-54 ruling: unchanged does nothing extra) - clears
        # phone_verified_at and revokes sessions. That includes CLEARING the
        # phone: `None` still differs from a previously-held number, and the
        # uniqueness check only makes sense for a truthy new value.
        phone_changed = False
        if "contact_number" in update_data:
            new_phone = update_data["contact_number"]
            if new_phone:
                self._check_contact_number_unique(new_phone, exclude_user_id=user_id)
            phone_changed = new_phone != (user.contact_number or None)

        # Convert empty strings to None for optional fields to avoid foreign key violations
        optional_fields = ['superior_id', 'respond_user_id', 'country', 'timezone', 'avatar', 'tier', 'contact_number']

        # Log what we received
        logger.info(f"Received update_data keys: {list(update_data.keys())}")
        logger.info(f"Received update_data: {update_data}")

        for key, value in update_data.items():
            logger.info(f"Processing field '{key}' with value: {repr(value)} (type: {type(value).__name__})")
            if key in optional_fields and value == '':
                setattr(user, key, None)
                logger.info(f"✓ Set {key} = None (converted from empty string)")
            elif value is None and key in optional_fields:
                setattr(user, key, None)
                logger.info(f"✓ Set {key} = None (explicit None)")
            elif value is None:
                logger.warning(f"⚠ Skipping {key} (None value for non-optional field)")
            elif key == "respond_user_id":
                setattr(user, key, _normalize_respond_user_id(value))
                logger.info(f"✓ Set {key} = {repr(_normalize_respond_user_id(value))} (normalized)")
            else:
                setattr(user, key, value)
                logger.info(f"✓ Set {key} = {repr(value)}")

        if phone_changed:
            user.phone_verified_at = None

        if scopes_provided:
            from app.services.product_discontinued_scope_service import replace_scopes

            replace_scopes(self.db, user_id, scope_items or [])

        logger.info(f"Before commit - respond_user_id: {user.respond_user_id}, superior_id: {user.superior_id}")
        # Read before the commit: after a failed flush the instance is expired.
        email_for_conflict = user.email
        # From the instance, not update_data: the S3 link branch above pops the key.
        contact_for_conflict = user.respond_contact_id
        try:
            self.db.commit()
        except IntegrityError as exc:
            self._raise_identity_conflict(
                exc,
                email=email_for_conflict,
                respond_contact_id=contact_for_conflict,
                exclude_user_id=user_id,
            )
        self.db.refresh(user)
        logger.info(f"After commit - respond_user_id: {user.respond_user_id}, superior_id: {user.superior_id}")

        # Lost phone (identity S1, plan 5.3): a changed contact_number can no
        # longer be trusted to be the same person who verified it, so the
        # verification clears and every session ends - the next phone sign-in
        # to the NEW number re-verifies it from scratch.
        if "contact_number" in update_data and update_data["contact_number"] != old_contact_number:
            user.phone_verified_at = None
            self.db.commit()
            from app.services.user_session_service import revoke_all_for_user

            revoke_all_for_user(self.db, user_id)

        if email_changed and old_email_for_notification is not None:
            try:
                self._queue_email_address_change_notification(
                    user_id=str(user.id),
                    old_email=old_email_for_notification,
                    new_email=user.email or "",
                )
            except Exception as e:
                logger.warning("Failed to queue email change notification: %s", e)

        # Side effects that need the row committed - each best-effort (S3 1.3/1.4).
        if contact_audit is not None:
            self._audit_contact_link(user.id, contact_audit)
        if should_unlink:
            self.unlink_contact(user_id)
            self.db.refresh(user)
        if phone_changed:
            from app.services import user_session_service

            try:
                user_session_service.revoke_all_for_user(self.db, user_id)
            except Exception as e:
                logger.warning("Failed to revoke sessions for user %s: %s", user_id, e)

        return user

    def unlink_contact(self, user_id: str) -> None:
        """Unlink a user's WhatsApp contact - the one implementation the PUT
        `respond_contact_id: null` path and the deferred `user.unlink_contact`
        action both call (S3 1.4). A no-op when nothing is linked."""
        from app.models.access import RespondContact
        from app.services import user_session_service

        user = self.get_user(user_id)
        if not user.respond_contact_id:
            return
        contact = (
            self.db.query(RespondContact)
            .filter(RespondContact.id == user.respond_contact_id)
            .first()
        )
        user.respond_contact_id = None
        self.db.commit()

        user_session_service.revoke_all_for_user(self.db, user_id)
        self._audit_contact_link(
            user_id,
            f"Unlinked WhatsApp contact {(contact.name or contact.phone_number) if contact else 'contact'}",
        )

    def delete_user(self, user_id: str) -> None:
        """Soft-delete a user (set is_trashed=True)."""
        user = self.get_user(user_id)
        setattr(user, "is_trashed", True)
        self.db.commit()

    def restore_user(self, user_id: str) -> None:
        """Restore a trashed user (set is_trashed=False)."""
        user = self.get_user(user_id)
        setattr(user, "is_trashed", False)
        self.db.commit()

    def permanent_delete_user(self, user_id: str) -> None:
        """Permanently delete a user. Only allowed when user is trashed."""
        user = self.get_user(user_id)
        if not bool(getattr(user, "is_trashed", False)):
            raise handle_conflict("Only trashed users can be permanently deleted. Trash the user first.")
        if getattr(user, "is_protected", False):
            raise handle_conflict("Protected users cannot be permanently deleted.")
        self._prepare_user_for_hard_delete(str(getattr(user, "id")))
        self.db.delete(user)
        try:
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            raise handle_conflict("Cannot permanently delete user because it is referenced by other records.")

    def bulk_delete_users(self, user_ids: list[str]) -> int:
        """Soft-delete multiple users. Returns count of deleted."""
        if not user_ids:
            return 0
        count = self.db.query(User).filter(
            User.id.in_(user_ids),
            User.is_trashed == False,
        ).update({"is_trashed": True}, synchronize_session=False)
        self.db.commit()
        return count

    def bulk_update_user_status(self, user_ids: list[str], status: str) -> int:
        """Set status for multiple users. Returns count updated."""
        if not user_ids:
            return 0
        count = self.db.query(User).filter(
            User.id.in_(user_ids),
            User.is_trashed == False,
        ).update({"status": status}, synchronize_session=False)
        self.db.commit()
        return count

    def bulk_permanent_delete_users(self, user_ids: list[str]) -> int:
        """Permanently delete users that are trashed. Returns count deleted."""
        if not user_ids:
            return 0
        users = self.db.query(User).filter(
            User.id.in_(user_ids),
            User.is_trashed == True,
        ).all()
        count = 0
        for user in users:
            if getattr(user, "is_protected", False):
                continue
            self._prepare_user_for_hard_delete(str(getattr(user, "id")))
            self.db.delete(user)
            count += 1
        try:
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            raise handle_conflict("Cannot permanently delete one or more users because they are referenced by other records.")
        return count

    def sync_respond_user(self, user_id: str, respond_user_id: Optional[str] = None) -> dict:
        """Sync user with Respond.io and update respond_synced status."""
        user = self.get_user(user_id)
        # Use provided respond_user_id or fall back to database value
        respond_id = _normalize_respond_user_id(respond_user_id) or _normalize_respond_user_id(
            getattr(user, "respond_user_id", None)
        )
        # Check for None, empty string, or falsy values
        if respond_id is None or respond_id.strip() == "":
            raise handle_conflict("Respond user ID is required for sync.")
        
        # If respond_user_id was provided and different from database, check uniqueness then save
        if respond_user_id and respond_user_id != user.respond_user_id:
            rid = _normalize_respond_user_id(respond_user_id)
            if rid:
                self._check_respond_user_id_unique(rid, exclude_user_id=user_id)
            setattr(user, "respond_user_id", rid or respond_user_id)
            self.db.commit()
            self.db.refresh(user)

        if not (user.email or "").strip():
            # A phone-only user has nothing to compare against (identity S0, 9.1 Q6).
            setattr(user, "respond_synced", "failed")
            self.db.commit()
            self.db.refresh(user)
            return {"status": "failed", "message": "User has no email."}

        from app.services.integration_service import RespondClient

        client = RespondClient()
        payload = client.get_user_by_id(respond_id)
        email = (
            payload.get("email")
            or payload.get("data", {}).get("email")
            or payload.get("user", {}).get("email")
        )

        if not email:
            setattr(user, "respond_synced", "failed")
            self.db.commit()
            self.db.refresh(user)
            return {"status": "failed", "message": "Respond user email not found."}

        if email.strip().lower() == (user.email or "").strip().lower():
            setattr(user, "respond_synced", "successful")
            self.db.commit()
            self.db.refresh(user)
            return {"status": "successful", "message": "Respond user synced successfully."}

        setattr(user, "respond_synced", "failed")
        self.db.commit()
        self.db.refresh(user)
        return {"status": "failed", "message": "Respond email does not match system email."}

    def list_user_roles(self, user_id: str) -> list:
        """List roles assigned to a user (from user_role_assignments)."""
        self.get_user(user_id)  # raise if not found
        assignments = (
            self.db.query(UserRoleAssignment)
            .filter(UserRoleAssignment.user_id == user_id)
            .all()
        )
        if not assignments:
            return []
        role_ids = [a.role_id for a in assignments]
        return self.db.query(UserRole).filter(UserRole.id.in_(role_ids)).all()

    def get_roles_for_user_ids(self, user_ids: list[str]) -> dict:
        """Return map user_id -> list of UserRole for the given user ids."""
        if not user_ids:
            return {}
        assignments = (
            self.db.query(UserRoleAssignment)
            .filter(UserRoleAssignment.user_id.in_(user_ids))
            .all()
        )
        role_ids = list({a.role_id for a in assignments})
        roles_by_id = {r.id: r for r in self.db.query(UserRole).filter(UserRole.id.in_(role_ids)).all()}
        out = {uid: [] for uid in user_ids}
        for a in assignments:
            r = roles_by_id.get(a.role_id)
            a_user_id = str(getattr(a, "user_id"))
            if r and a_user_id in out:
                out[a_user_id].append(r)
        return out

    def set_user_roles(self, user_id: str, role_ids: list[str]) -> dict:
        """Replace user's role assignments with the given role_ids."""
        self.get_user(user_id)
        self.db.query(UserRoleAssignment).filter(UserRoleAssignment.user_id == user_id).delete(
            synchronize_session=False
        )
        self.db.flush()
        for role_id in role_ids or []:
            role = self.db.query(UserRole).filter(UserRole.id == role_id).first()
            if role:
                self.db.add(UserRoleAssignment(user_id=user_id, role_id=role_id))
        self.db.commit()
        return {"message": "User roles updated successfully"}

    def list_user_companies(self, user_id: str) -> list[dict]:
        """Return the companies granted to a user as ``[{id,name,code}]``."""
        from app.models.company import Company, UserCompany
        self.get_user(user_id)
        rows = (
            self.db.query(Company)
            .join(UserCompany, UserCompany.company_id == Company.id)
            .filter(UserCompany.user_id == user_id)
            .order_by(Company.name.asc())
            .all()
        )
        return [{"id": str(c.id), "name": c.name, "code": c.code} for c in rows]

    def set_user_companies(self, user_id: str, company_ids: list[str]) -> dict:
        """Replace a user's company grants with the given ids (unknown ids skipped).

        If the user's ``last_active_company_id`` is no longer in the resulting set,
        repoint it to the first remaining grant (deterministic) or null it.
        """
        from app.models.company import Company, UserCompany
        user = self.get_user(user_id)
        self.db.query(UserCompany).filter(UserCompany.user_id == user_id).delete(
            synchronize_session=False
        )
        self.db.flush()
        granted: list[str] = []
        for cid in company_ids or []:
            if cid in granted:
                continue
            if self.db.query(Company).filter(Company.id == cid).first():
                self.db.add(UserCompany(company_id=cid, user_id=user_id))
                granted.append(cid)
        if str(user.last_active_company_id or "") not in granted:
            user.last_active_company_id = granted[0] if granted else None
        self.db.commit()
        return {"message": "User companies updated successfully"}


class UserRoleService:
    """Service for user role operations."""
    
    def __init__(self, db: Session):
        self.db = db
    
    def get_all_roles(self, query: Optional[str] = None):
        """Get all roles for select dropdowns (no pagination)."""
        q = self.db.query(UserRole).filter(UserRole.is_trashed == False)
        
        if query:
            q = q.filter(
                (UserRole.name.ilike(f"%{query}%")) |
                (UserRole.slug.ilike(f"%{query}%"))
            )
        
        roles = q.order_by(UserRole.name).all()
        return roles
    
    def list_roles(self, page: int = 1, limit: int = 50, query: Optional[str] = None):
        """List user roles with permissions loaded."""
        q = self.db.query(UserRole).options(
            joinedload(UserRole.permissions).joinedload(UserRolePermission.permission)
        )

        if query:
            q = q.filter(
                (UserRole.name.ilike(f"%{query}%")) |
                (UserRole.slug.ilike(f"%{query}%"))
            )

        total = q.count()
        offset = (page - 1) * limit
        roles = q.offset(offset).limit(limit).all()
        return {
            "data": roles,
            "pagination": {"total": total, "page": page, "limit": limit},
            "empty": total == 0
        }

    def get_role(self, role_id: str, with_permissions: bool = True):
        """Get a role by ID, optionally loading permissions."""
        query = self.db.query(UserRole)
        if with_permissions:
            query = query.options(
                joinedload(UserRole.permissions).joinedload(UserRolePermission.permission)
            )
        role = query.filter(UserRole.id == role_id).first()
        if not role:
            raise handle_not_found("User Role", role_id)
        return role
    
    def create_role(self, role_data: UserRoleCreate, created_by: str):
        """Create a new role with permissions."""
        # Check uniqueness
        existing_slug = self.db.query(UserRole).filter(UserRole.slug == role_data.slug).first()
        existing_name = self.db.query(UserRole).filter(UserRole.name == role_data.name).first()
        if existing_slug or existing_name:
            raise handle_conflict("Name and slug must be unique")
        
        role_dict = role_data.model_dump(exclude={"permissions"})
        role_dict["created_by_user_id"] = created_by
        role = UserRole(**role_dict)
        self.db.add(role)
        self.db.flush()
        
        # Create permission associations if provided
        if role_data.permissions:
            for permission_id in role_data.permissions:
                role_permission = UserRolePermission(
                    role_id=role.id,
                    permission_id=permission_id
                )
                self.db.add(role_permission)
        
        self.db.commit()
        self.db.refresh(role)
        return role
    
    def update_role(self, role_id: str, role_data: UserRoleUpdate):
        """Update a role and optionally replace its permission assignments."""
        # Avoid loading permission relationship into session before bulk replace,
        # which can trigger stale row-count errors on flush.
        role = self.get_role(role_id, with_permissions=False)
        update_data = role_data.model_dump(exclude_unset=True)
        permission_ids = update_data.pop("permissions", None)

        for key, value in update_data.items():
            setattr(role, key, value)

        if permission_ids is not None:
            existing_role_permissions = (
                self.db.query(UserRolePermission)
                .filter(UserRolePermission.role_id == role_id)
                .all()
            )
            for role_permission in existing_role_permissions:
                self.db.delete(role_permission)
            self.db.flush()
            for perm_id in permission_ids:
                self.db.add(UserRolePermission(role_id=role_id, permission_id=perm_id))

        self.db.commit()
        self.db.refresh(role)
        return role

    def delete_role(self, role_id: str):
        """Delete a role when it is allowed by business rules."""
        # Do not eager-load permissions here; it can leave stale child rows in session
        # when deleting and trigger SQLAlchemy rowcount mismatch errors.
        role = self.get_role(role_id, with_permissions=False)

        if bool(getattr(role, "is_default", False)):
            raise handle_conflict("Default role cannot be deleted. Set another role as default first.")

        assigned_users = (
            self.db.query(UserRoleAssignment)
            .filter(UserRoleAssignment.role_id == role_id)
            .count()
        )
        if assigned_users > 0:
            raise handle_conflict(
                f"Cannot delete role: {assigned_users} user(s) are still assigned to this role."
            )

        self.db.delete(role)
        self.db.commit()
        return {"message": "Role deleted successfully", "deleted": True, "deleted_count": 1}
    
    def set_default_role(self, role_id: str):
        """Set a role as the default role."""
        role = self.get_role(role_id)
        
        # Reset all roles to is_default = False
        self.db.query(UserRole).filter(UserRole.is_default == True).update({"is_default": False})
        
        # Set the specified role to is_default = True
        setattr(role, "is_default", True)
        self.db.commit()
        self.db.refresh(role)
        return {"message": "Role successfully set as the default"}


import os as _os
import threading as _threading
import time as _time


class _RbacCache:
    """Per-process TTL cache for permission/role lookups.

    Each gunicorn worker has its own instance - keys never cross workers.
    RBAC writes must call ``invalidate_rbac_cache(user_id)`` (or pass None
    to clear all). Default TTL = 30s, override via RBAC_CACHE_TTL_SECONDS.
    """

    def __init__(self, ttl_seconds: float = 30.0, max_entries: int = 50000):
        self._ttl = ttl_seconds
        self._max = max_entries
        self._store: dict = {}
        self._lock = _threading.RLock()

    def get(self, key):
        now = _time.monotonic()
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            value, expiry = entry
            if now >= expiry:
                self._store.pop(key, None)
                return None
            return value

    def set(self, key, value):
        now = _time.monotonic()
        with self._lock:
            if len(self._store) >= self._max:
                # Drop the 10% oldest entries by expiry time.
                items = sorted(self._store.items(), key=lambda kv: kv[1][1])
                for k, _v in items[: max(1, self._max // 10)]:
                    self._store.pop(k, None)
            self._store[key] = (value, now + self._ttl)

    def invalidate(self, prefix=None):
        with self._lock:
            if prefix is None:
                self._store.clear()
                return
            for k in [k for k in self._store if isinstance(k, tuple) and k[: len(prefix)] == prefix]:
                self._store.pop(k, None)


_RBAC_CACHE_TTL = float(_os.environ.get("RBAC_CACHE_TTL_SECONDS", "30"))
_RBAC_CACHE_ENABLED = _os.environ.get("RBAC_CACHE_ENABLED", "1") == "1"
_rbac_cache = _RbacCache(ttl_seconds=_RBAC_CACHE_TTL)


def invalidate_rbac_cache(user_id=None):
    """Drop cached permission/role lookups. Call after RBAC writes.

    With ``user_id=None`` clear entire cache (use for bulk RBAC migrations).
    With a specific user_id, drop only that user's entries - other users keep
    their warm cache.
    """
    if user_id is None:
        _rbac_cache.invalidate()
    else:
        _rbac_cache.invalidate(("perm", str(user_id)))
        _rbac_cache.invalidate(("any_perm", str(user_id)))
        _rbac_cache.invalidate(("role_slugs", str(user_id)))
        _rbac_cache.invalidate(("perm_slugs", str(user_id)))


class UserPermissionService:
    """Service for user permission operations."""

    def __init__(self, db: Session):
        self.db = db
    
    def get_all_permissions(self, query: Optional[str] = None):
        """Get all permissions for select dropdowns (no pagination)."""
        q = self.db.query(UserPermission)
        if query:
            q = q.filter(
                (UserPermission.name.ilike(f"%{query}%")) |
                (UserPermission.slug.ilike(f"%{query}%"))
            )
        return q.order_by(UserPermission.name).all()

    def list_permissions(self, page: int = 1, limit: int = 50):
        """List user permissions."""
        q = self.db.query(UserPermission)
        
        total = q.count()
        offset = (page - 1) * limit
        permissions = q.offset(offset).limit(limit).all()
        
        return {
            "data": permissions,
            "pagination": {"total": total, "page": page, "limit": limit},
            "empty": total == 0
        }
    
    def get_permission(self, permission_id: str):
        """Get a permission by ID."""
        permission = self.db.query(UserPermission).filter(UserPermission.id == permission_id).first()
        if not permission:
            raise handle_not_found("User Permission", permission_id)
        return permission
    
    def create_permission(self, permission_data: UserPermissionCreate, created_by: str):
        """Create a new permission."""
        existing = self.db.query(UserPermission).filter(
            UserPermission.slug == permission_data.slug
        ).first()
        if existing:
            raise handle_conflict("Permission slug already exists.")
        
        permission_dict = permission_data.model_dump()
        permission_dict["created_by_user_id"] = created_by
        permission = UserPermission(**permission_dict)
        self.db.add(permission)
        self.db.commit()
        self.db.refresh(permission)
        return permission
    
    def update_permission(self, permission_id: str, permission_data: UserPermissionUpdate):
        """Update a permission."""
        permission = self.get_permission(permission_id)
        
        update_data = permission_data.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(permission, key, value)
        
        self.db.commit()
        self.db.refresh(permission)
        return permission
    
    def bulk_delete_permissions(self, permission_ids: list[str]):
        """Bulk delete permissions."""
        # Delete linked role permissions first
        self.db.query(UserRolePermission).filter(
            UserRolePermission.permission_id.in_(permission_ids)
        ).delete(synchronize_session=False)
        
        # Delete the permissions
        deleted_count = self.db.query(UserPermission).filter(
            UserPermission.id.in_(permission_ids)
        ).delete(synchronize_session=False)
        
        self.db.commit()
        return {"message": f"Successfully deleted {deleted_count} permission(s)"}

    # --- RBAC: effective permission resolution (multi-role + legacy role_id) ---
    SUPERADMIN_ROLE_SLUG = "superadmin"

    def get_user_role_ids(self, user_id: str) -> list[str]:
        """Return all role IDs for a user (from user_role_assignments)."""
        assignments = (
            self.db.query(UserRoleAssignment.role_id)
            .filter(UserRoleAssignment.user_id == user_id)
            .all()
        )
        return [r.role_id for r in assignments]

    def get_user_role_slugs(self, user_id: str) -> set[str]:
        """Return all role slugs for a user (for superadmin bypass)."""
        if _RBAC_CACHE_ENABLED:
            cached = _rbac_cache.get(("role_slugs", str(user_id)))
            if cached is not None:
                return cached
        role_ids = self.get_user_role_ids(user_id)
        if not role_ids:
            result: set[str] = set()
        else:
            roles = self.db.query(UserRole.slug).filter(UserRole.id.in_(role_ids)).all()
            result = {r.slug for r in roles}
        if _RBAC_CACHE_ENABLED:
            _rbac_cache.set(("role_slugs", str(user_id)), result)
        return result

    def get_user_permission_slugs(self, user_id: str) -> set[str]:
        """Return effective permission slugs for a user (union of all assigned roles).
        Users with role slug 'superadmin' or 'admin' receive all known permissions (for frontend menu/actions)."""
        if _RBAC_CACHE_ENABLED:
            cached = _rbac_cache.get(("perm_slugs", str(user_id)))
            if cached is not None:
                return cached
        role_slugs = self.get_user_role_slugs(user_id)
        if role_slugs & {self.SUPERADMIN_ROLE_SLUG, "admin"}:
            rows = self.db.query(UserPermission.slug).all()
            result = {r.slug for r in rows}
        else:
            role_ids = self.get_user_role_ids(user_id)
            if not role_ids:
                result = set()
            else:
                rows = (
                    self.db.query(UserPermission.slug)
                    .join(UserRolePermission, UserRolePermission.permission_id == UserPermission.id)
                    .filter(UserRolePermission.role_id.in_(role_ids))
                    .distinct()
                    .all()
                )
                result = {r.slug for r in rows}
        if _RBAC_CACHE_ENABLED:
            _rbac_cache.set(("perm_slugs", str(user_id)), result)
        return result

    def check_user_has_permission(self, user_id: str, permission_slug: str) -> bool:
        """True if user has the permission or is superadmin.

        Cached for ``RBAC_CACHE_TTL_SECONDS`` (default 30s). Cache miss runs a
        single targeted query that joins user_role_assignments → role_permissions
        → permissions WHERE permissions.slug = ? - no full-permission-set fetch.
        """
        cache_key = ("perm", str(user_id), permission_slug)
        if _RBAC_CACHE_ENABLED:
            cached = _rbac_cache.get(cache_key)
            if cached is not None:
                return cached

        if self.get_user_role_slugs(user_id) & {self.SUPERADMIN_ROLE_SLUG, "admin"}:
            result = True
        else:
            role_ids = self.get_user_role_ids(user_id)
            if not role_ids:
                result = False
            else:
                hit = (
                    self.db.query(UserPermission.id)
                    .join(UserRolePermission, UserRolePermission.permission_id == UserPermission.id)
                    .filter(UserRolePermission.role_id.in_(role_ids))
                    .filter(UserPermission.slug == permission_slug)
                    .first()
                )
                result = hit is not None

        if _RBAC_CACHE_ENABLED:
            _rbac_cache.set(cache_key, result)
        return result

    def check_user_has_any_permission(self, user_id: str, permission_slugs: list[str]) -> bool:
        """True if user has at least one of the permissions or is superadmin."""
        cache_key = ("any_perm", str(user_id), tuple(sorted(permission_slugs)))
        if _RBAC_CACHE_ENABLED:
            cached = _rbac_cache.get(cache_key)
            if cached is not None:
                return cached

        if self.get_user_role_slugs(user_id) & {self.SUPERADMIN_ROLE_SLUG, "admin"}:
            result = True
        else:
            role_ids = self.get_user_role_ids(user_id)
            if not role_ids:
                result = False
            else:
                hit = (
                    self.db.query(UserPermission.id)
                    .join(UserRolePermission, UserRolePermission.permission_id == UserPermission.id)
                    .filter(UserRolePermission.role_id.in_(role_ids))
                    .filter(UserPermission.slug.in_(permission_slugs))
                    .first()
                )
                result = hit is not None

        if _RBAC_CACHE_ENABLED:
            _rbac_cache.set(cache_key, result)
        return result


class AccessAgentService:
    """Service for access agent operations."""
    
    def __init__(self, db: Session):
        self.db = db
    
    def _build_list_query(self, query: Optional[str] = None):
        """Build the filtered + sorted access-agent query shared by ``list_agents``
        and ``neighbours`` so the two can never drift.

        The ORDER BY always appends ``AccessAgent.id`` as a deterministic tie-breaker
        so offset position and prev/next neighbours are unambiguous when the primary
        sort column has equal values.
        """
        q = self.db.query(AccessAgent)

        if query:
            q = q.filter(
                or_(
                    AccessAgent.code.ilike(f"%{query}%"),
                    AccessAgent.name.ilike(f"%{query}%")
                )
            )

        return q.order_by(AccessAgent.code.asc(), AccessAgent.id.asc())

    def list_agents(self, page: int = 1, limit: int = 50, query: Optional[str] = None):
        """List access agents."""
        q = self._build_list_query(query=query)

        total = q.count()
        offset = (page - 1) * limit
        agents = q.offset(offset).limit(limit).all()
        
        data = []
        for agent in agents:
            data.append({
                "id": str(agent.id),
                "code": agent.code,
                "name": agent.name,
                "description": agent.description,
                "is_active": agent.is_active,
                "assign_to_new_internal_contacts": agent.assign_to_new_internal_contacts,
                "created_at": agent.created_at,
                "updated_at": agent.updated_at,
                "synced_to_excel": agent.synced_to_excel,
                "last_synced_to_excel": agent.last_synced_to_excel,
            })
        
        return {
            "data": data,
            "pagination": {"total": total, "page": page, "limit": limit},
            "empty": total == 0
        }

    def get_agent(self, agent_id: str):
        """Get an access agent by ID."""
        agent = self.db.query(AccessAgent).filter(AccessAgent.id == agent_id).first()
        if not agent:
            raise handle_not_found("Access Agent", agent_id)

        return {
            "id": str(agent.id),
            "code": agent.code,
            "name": agent.name,
            "description": agent.description,
            "is_active": agent.is_active,
            "assign_to_new_internal_contacts": agent.assign_to_new_internal_contacts,
            "created_at": agent.created_at,
            "updated_at": agent.updated_at,
            "synced_to_excel": agent.synced_to_excel,
            "last_synced_to_excel": agent.last_synced_to_excel,
        }
    
    def create_agent(self, agent_data: AccessAgentCreate):
        """Create a new access agent."""
        existing = self.db.query(AccessAgent).filter(AccessAgent.code == agent_data.code).first()
        if existing:
            raise handle_conflict("Access agent code already exists.")
        
        agent = AccessAgent(**agent_data.model_dump())
        self.db.add(agent)
        self.db.commit()
        self.db.refresh(agent)
        return agent
    
    def update_agent(self, agent_id: str, agent_data: AccessAgentUpdate):
        """Update an access agent."""
        agent = self.db.query(AccessAgent).filter(AccessAgent.id == agent_id).first()
        if not agent:
            raise handle_not_found("Access Agent", agent_id)
        
        update_data = agent_data.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(agent, key, value)
        
        self.db.commit()
        self.db.refresh(agent)
        return agent

    def delete_agent(self, agent_id: str) -> None:
        """Delete an access agent. Related rows cascade (contact access, agent_teams, cursors, user_agent_access)."""
        agent = self.db.query(AccessAgent).filter(AccessAgent.id == agent_id).first()
        if not agent:
            raise handle_not_found("Access Agent", agent_id)
        self.db.delete(agent)
        self.db.commit()

    def list_agents_assign_to_new_internal_contacts(self):
        """Return access agents that should be assigned to newly created internal contacts (from sync)."""
        return (
            self.db.query(AccessAgent)
            .filter(
                AccessAgent.is_active == True,
                AccessAgent.assign_to_new_internal_contacts == True,
            )
            .all()
        )

    def list_contact_accesses(self, agent_id: str):
        """List contact access entries for an agent."""
        return self.db.query(ContactAgentAccess).filter(
            ContactAgentAccess.agent_id == agent_id
        ).order_by(ContactAgentAccess.created_at.desc()).all()
    
    def list_all_contact_accesses(
        self,
        page: int = 1,
        limit: int = 50,
        query: Optional[str] = None,
        agent_id: Optional[str] = None,
        contact_id: Optional[str] = None,
        respond_contact_id: Optional[str] = None,  # New parameter for filtering by respond_contact_id
        sort_field: str = "created_at",
        sort_dir: str = "asc"
    ):
        """List all contact access entries with filtering."""
        from sqlalchemy.orm import joinedload

        from app.schemas.common import ListResponse, PaginationResponse
        from app.schemas.user import ContactAgentAccessResponse

        # The contact is eager-loaded because every row reports its outbound
        # switch; lazy loading would be one extra SELECT per grant.
        q = (
            self.db.query(ContactAgentAccess)
            .join(AccessAgent)
            .options(joinedload(ContactAgentAccess.contact))
        )
        
        # Filter by respond_contact_id if provided
        if respond_contact_id:
            q = q.filter(ContactAgentAccess.respond_contact_id == respond_contact_id)
        
        if agent_id and agent_id != "all":
            q = q.filter(ContactAgentAccess.agent_id == agent_id)
        
        if contact_id:
            q = q.filter(ContactAgentAccess.respond_contact_phone.ilike(f"%{contact_id}%"))
        
        if query:
            # The agent is already joined above. Joining it a second time here
            # emitted the same table twice and Postgres refused the statement
            # ("table name access_agents specified more than once"), so ANY
            # search on this list was a 500.
            q = q.filter(
                or_(
                    ContactAgentAccess.respond_contact_phone.ilike(f"%{query}%"),
                    ContactAgentAccess.respond_contact_name.ilike(f"%{query}%"),
                    AccessAgent.code.ilike(f"%{query}%"),
                    AccessAgent.name.ilike(f"%{query}%")
                )
            )
        
        total = q.count()
        
        # Handle sorting - map frontend field names to model attributes
        sort_map = {
            "respond_contact_phone": ContactAgentAccess.respond_contact_phone,
            "respond_contact_name": ContactAgentAccess.respond_contact_name,
            "agent_code": AccessAgent.code,
            "agent_name": AccessAgent.name,
            "created_at": ContactAgentAccess.created_at,
            "updated_at": ContactAgentAccess.updated_at,
        }
        sort_attr = sort_map.get(sort_field, ContactAgentAccess.created_at)
        if sort_dir == "desc":
            sort_attr = sort_attr.desc()
        else:
            sort_attr = sort_attr.asc()
        
        accesses = q.order_by(sort_attr).offset((page - 1) * limit).limit(limit).all()
        
        # Build response with agent details
        result_data = []
        for access in accesses:
            access_dict = {
                'id': str(access.id),
                'respond_contact_id': (
                    str(access.respond_contact_id)
                    if access.respond_contact_id is not None
                    else None
                ),
                'respond_contact_phone': access.respond_contact_phone,
                'respond_contact_name': access.respond_contact_name,
                'agent_id': str(access.agent_id),
                'is_allowed': access.is_allowed,
                'valid_from': access.valid_from,
                'valid_to': access.valid_to,
                'created_at': access.created_at,
                'created_by': access.created_by,
                'synced_to_excel': access.synced_to_excel,
                'last_synced_to_excel': access.last_synced_to_excel,
                'updated_at': access.updated_at,
                'agent_code': access.agent.code if access.agent else None,
                'agent_name': access.agent.name if access.agent else None,
                # The CONTACT's kill switch, repeated on each of that contact's
                # grant rows. None when the row predates the respond_contacts FK.
                'outbound_enabled': (
                    bool(access.contact.outbound_enabled)
                    if access.contact is not None
                    else None
                ),
            }
            result_data.append(ContactAgentAccessResponse.model_validate(access_dict))
        
        return ListResponse(
            data=result_data,
            pagination=PaginationResponse(total=total, page=page, limit=limit),
        )

    def create_contact_access(self, agent_id: str, contact_data: ContactAgentAccessCreate):
        """Create a contact access entry for an agent."""
        from app.services.contact_service import ContactService
        from app.models.access import RespondContact
        from sqlalchemy.exc import IntegrityError
        
        # Get agent model object directly (not the dict response)
        agent = self.db.query(AccessAgent).filter(AccessAgent.id == agent_id).first()
        if not agent:
            raise handle_not_found("Access Agent", agent_id)
        
        # Get or create the contact
        contact_service = ContactService(self.db)
        contact = contact_service.get_or_create_contact(
            phone_number=contact_data.respond_contact_phone,
            name=contact_data.respond_contact_name
        )
        
        # Check if access entry already exists for this contact and agent
        existing_access = self.db.query(ContactAgentAccess).filter(
            ContactAgentAccess.respond_contact_id == contact.id,
            ContactAgentAccess.agent_id == agent.id
        ).first()
        
        if existing_access:
            raise handle_conflict("An access agent entry already exists for this contact and agent combination.")
        
        # Create access entry with contact_id
        access_dict = contact_data.model_dump()
        access_dict['agent_id'] = agent.id
        access_dict['respond_contact_id'] = contact.id
        access = ContactAgentAccess(**access_dict)
        
        try:
            self.db.add(access)
            self.db.commit()
            self.db.refresh(access)
            return access
        except IntegrityError as e:
            self.db.rollback()
            # Check if it's a unique constraint violation
            error_str = str(e.orig) if hasattr(e, 'orig') else str(e)
            if 'unique' in error_str.lower() or 'duplicate' in error_str.lower() or 'uq_contact_agent_access_respond_contact_id_agent_id' in error_str:
                raise handle_conflict("An access agent entry already exists for this contact and agent combination.")
            raise

    def update_contact_access(self, contact_id: str, contact_data: ContactAgentAccessUpdate):
        """Update a contact access entry."""
        access = self.db.query(ContactAgentAccess).filter(ContactAgentAccess.id == contact_id).first()
        if not access:
            raise handle_not_found("Contact Agent Access", contact_id)

        update_data = contact_data.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(access, key, value)

        self.db.commit()
        self.db.refresh(access)
        return access

    def delete_contact_access(self, contact_id: str):
        """Delete a contact access entry."""
        access = self.db.query(ContactAgentAccess).filter(ContactAgentAccess.id == contact_id).first()
        if not access:
            raise handle_not_found("Contact Agent Access", contact_id)
        self.db.delete(access)
        self.db.commit()

    def lookup_respond_contact_name(self, identifier: str) -> Optional[str]:
        """Lookup contact name from Respond.io by identifier."""
        from app.services.integration_service import RespondClient

        client = RespondClient()
        payload = client.get_contact_by_identifier(identifier)
        return (
            payload.get("name")
            or payload.get("data", {}).get("name")
            or payload.get("contact", {}).get("name")
        )

    def sync_contact_name(self, contact_id: str) -> ContactAgentAccess:
        """Sync contact name from Respond.io and update record."""
        access = self.db.query(ContactAgentAccess).filter(ContactAgentAccess.id == contact_id).first()
        if not access:
            raise handle_not_found("Contact Agent Access", contact_id)
        # Using respond_contact_phone for lookup
        name = self.lookup_respond_contact_name(str(getattr(access, "respond_contact_phone")))
        # Update respond_contact_name field
        setattr(access, "respond_contact_name", name)
        self.db.commit()
        self.db.refresh(access)
        return access
    
    def _brand_codes_by_member(self, member_ids) -> dict[str, set[str]]:
        """``{team_member_id: {brand codes}}`` for the members given, tags only.

        One query, and only ever called when a brand was actually requested: the
        no-brand path must never touch ``team_member_brands``, exactly as the
        no-segment path never touches ``team_member_market_segments``.
        """
        ids = [str(m) for m in (member_ids or [])]
        if not ids:
            return {}
        from app.models.access import team_member_brands

        rows = self.db.query(
            team_member_brands.c.team_member_id, team_member_brands.c.brand_code
        ).filter(team_member_brands.c.team_member_id.in_(ids)).all()
        out: dict[str, set[str]] = {}
        for member_id, code in rows:
            normalised = normalise_brand_code(code)
            if normalised:
                out.setdefault(str(member_id), set()).add(normalised)
        return out

    def _rr_pool(
        self,
        agent_id: str,
        team_id: str,
        contact_segments: Optional[set[str]] = None,
        *,
        brand_code: Optional[str] = None,
    ) -> Optional[tuple]:
        """The eligible pool and the cursor key for one (agent, team) draw. PURE READ.

        Returns ``(members, user_ids, segment_key, brands_by_member, wanted_brand)``, or
        ``None`` when the agent is not linked to the team or nobody is eligible.

        Extracted so the LIVE draw and the DRY-RUN preview select from the SAME pool with
        the SAME cursor key: a preview that filtered differently would name an assignee the
        live turn was never going to pick, which is worse than naming none.
        """
        from sqlalchemy import and_
        from sqlalchemy.orm import selectinload

        from app.models.base import company_scope
        from app.services.market_segment_service import segment_key_for
        # Check agent is linked to this team. Read scope-free: `team_id` is passed in
        # already resolved for the work item's company (by get_team_id_by_tier), so
        # the ambient company must not veto it - otherwise a caller switched to
        # another company sees "no available assignee" for a fully staffed team.
        # The team_id predicate pins one team, hence one company.
        with company_scope(self.db, None):
            link = (
                self.db.query(AgentTeam)
                .filter(
                    and_(
                        AgentTeam.agent_id == agent_id,
                        AgentTeam.team_id == team_id,
                    )
                )
                .first()
            )
        if not link:
            return None
        # Get team members (user_ids) in order. Per-team RR opt-out: members with
        # include_in_round_robin=false are skipped for AUTO distribution (manual
        # takeover/reassign can still target them). All members excluded -> treated
        # the same as an empty team (no eligible assignee), no silent misassign.
        members_q = (
            self.db.query(TeamMember)
            .filter(
                TeamMember.team_id == team_id,
                TeamMember.include_in_round_robin.is_(True),
            )
            .order_by(TeamMember.sort_order.asc().nullslast(), TeamMember.user_id.asc())
        )
        # Only eager-load / touch segments when a filter is requested, so the
        # normal RR path never queries team_member_market_segments (no regression).
        if contact_segments:
            members_q = members_q.options(selectinload(TeamMember.market_segments))
        members = members_q.all()
        if not members:
            return None
        wanted_brand = normalise_brand_code(brand_code)
        brands_by_member = (
            self._brand_codes_by_member([m.id for m in members]) if wanted_brand else {}
        )
        # Opt-in scoping on both axes, ANDed, each dropped on its own when it empties
        # the pool - the rule market segments already established, applied to the brand
        # as well and composed rather than collapsed.
        segment_key = ""
        brand_matched = False
        pool = members
        if contact_segments:
            segment_pool = [
                m
                for m in pool
                if not m.market_segments
                or {str(s.code) for s in m.market_segments} & contact_segments
            ]
            # Nobody serves this segment -> the whole team on the legacy '' cursor.
            if segment_pool:
                pool = segment_pool
                segment_key = segment_key_for(contact_segments)
        if wanted_brand:
            brand_pool = [
                m
                for m in pool
                if member_serves_brand(brands_by_member.get(str(m.id)), wanted_brand)
            ]
            if brand_pool:
                # The cursor splits whenever the brand actually NARROWED the pool, match
                # or no match: a brand nobody carries still rotates over the untagged
                # members alone, and writing that draw onto the shared cursor would park
                # the unfiltered rotation on them and starve everybody else. A brand that
                # excluded nobody keeps the cursor it always had.
                if len(brand_pool) != len(pool):
                    segment_key = f"{segment_key}{brand_pool_key(wanted_brand)}"
                pool = brand_pool
        members = pool
        user_ids = [_rr_user_id_key(m.user_id) for m in members]
        return members, user_ids, segment_key, brands_by_member, wanted_brand

    def get_next_assignee(
        self,
        agent_id: str,
        team_id: str,
        contact_segments: Optional[set[str]] = None,
        *,
        brand_code: Optional[str] = None,
    ) -> Optional[dict]:
        """
        Return the next assignee for (agent_id, team_id) using round-robin.
        Uses SELECT ... FOR UPDATE on the cursor for concurrency safety.
        Returns dict with id, email, name or None if no eligible members.

        ``contact_segments`` (opt-in): when a non-empty set is passed, the round-robin
        pool is restricted to members whose served segments intersect it, plus untagged
        members (serve all). The rotation then uses a segment-scoped cursor
        (``segment_key`` = sorted '|'-joined codes) so each segment rotates independently.
        When ``None`` / empty (the normal path, incl. every non-CS agent), the pool and
        the ``segment_key=''`` cursor are exactly as before - no behaviour change.
        An empty filtered pool falls back to the full team on the '' cursor.

        ``brand_code`` (opt-in): the SECOND axis, same rule and ANDed with the first -
        members tagged with that brand plus members tagged with none of them. The
        returned dict carries ``brand_matched``, true only when the member DRAWN is
        tagged with that brand, so n8n can tell the specialist taking it from an
        untagged serve-all member taking it.

        The two filters fall back ONE AXIS AT A TIME: a brand nobody serves drops the
        brand and keeps whatever the segment left, and only a segment nobody serves
        goes back to the whole team. Dropping straight to the team would hand a retail
        conversation to a project-only member because of an unrelated brand.
        """
        from sqlalchemy import and_

        pooled = self._rr_pool(
            agent_id, team_id, contact_segments, brand_code=brand_code
        )
        if pooled is None:
            return None
        members, user_ids, segment_key, brands_by_member, wanted_brand = pooled
        brand_matched = False
        # Get or create cursor and lock it (scoped by segment_key)
        cursor = (
            self.db.query(AgentTeamRoundRobinCursor)
            .filter(
                and_(
                    AgentTeamRoundRobinCursor.agent_id == agent_id,
                    AgentTeamRoundRobinCursor.team_id == team_id,
                    AgentTeamRoundRobinCursor.segment_key == segment_key,
                )
            )
            .with_for_update()
            .first()
        )
        if not cursor:
            cursor = AgentTeamRoundRobinCursor(
                agent_id=agent_id,
                team_id=team_id,
                segment_key=segment_key,
                last_assigned_user_id=None,
            )
            self.db.add(cursor)
            self.db.flush()
        # Find next index: after last_assigned_user_id, wrap around
        next_idx = _rr_next_index(user_ids, getattr(cursor, "last_assigned_user_id", None))
        next_user_id = user_ids[next_idx]
        setattr(cursor, "last_assigned_user_id", next_user_id)
        # Per assignee, not per pool: the drawn member carrying the tag is what makes
        # this a BRAND routing. An untagged serve-all member drawn from the same pool
        # reports false, which is what n8n needs to tell the specialist taking it from
        # the fallback taking it.
        if wanted_brand:
            drawn = members[next_idx]
            brand_matched = wanted_brand in (brands_by_member.get(str(drawn.id)) or set())
        self.db.commit()
        return self._rr_assignee_payload(next_user_id, brand_matched, segment_key)

    def _rr_assignee_payload(
        self, user_id: object, brand_matched: bool, cursor_key: Optional[str] = None
    ) -> dict:
        """The drawn member as the caller's dict. Shared by the live draw and the preview,
        so a preview cannot answer in a different shape from the turn it previews.

        `cursor_key` is the round-robin cursor the draw used (the segment key, plus a
        `~b:<brand>` suffix when the pool narrowed by brand). Echoed so a caller can record
        WHICH rotation it drew from; the chatbot's escalation trace does (#865)."""
        user = self.db.query(User).filter(User.id == user_id).first()
        if not user:
            return {
                "id": user_id,
                "email": None,
                "name": None,
                "brand_matched": brand_matched,
                "cursor_key": cursor_key,
            }
        return {
            "id": user.id,
            "email": user.email,
            "name": user.name or user.email,
            "respond_user_id": user.respond_user_id,
            "brand_matched": brand_matched,
            "cursor_key": cursor_key,
        }

    def preview_next_assignee(
        self,
        agent_id: str,
        team_id: str,
        contact_segments: Optional[set[str]] = None,
        *,
        brand_code: Optional[str] = None,
    ) -> Optional[dict]:
        """Who ``get_next_assignee`` WOULD draw, without drawing.

        Same pool, same cursor key, same arithmetic - and no write of any kind: no row lock,
        no cursor row created, nothing advanced, nothing committed. A dry-run escalation has
        to name the real next assignee (a preview that says "someone" tells the owner
        nothing), and it shares the cursor with live traffic, so moving it would hand the
        next real customer to the member after the one a test just consumed (H37 / H57).

        Returns ``None`` when there is no eligible member, exactly as the live draw does.
        """
        from sqlalchemy import and_

        pooled = self._rr_pool(
            agent_id, team_id, contact_segments, brand_code=brand_code
        )
        if pooled is None:
            return None
        members, user_ids, segment_key, brands_by_member, wanted_brand = pooled
        cursor = (
            self.db.query(AgentTeamRoundRobinCursor)
            .filter(
                and_(
                    AgentTeamRoundRobinCursor.agent_id == agent_id,
                    AgentTeamRoundRobinCursor.team_id == team_id,
                    AgentTeamRoundRobinCursor.segment_key == segment_key,
                )
            )
            .first()
        )
        next_idx = _rr_next_index(
            user_ids, getattr(cursor, "last_assigned_user_id", None) if cursor else None
        )
        brand_matched = False
        if wanted_brand:
            drawn = members[next_idx]
            brand_matched = wanted_brand in (brands_by_member.get(str(drawn.id)) or set())
        return self._rr_assignee_payload(user_ids[next_idx], brand_matched, segment_key)

    def list_active_team_members_detail(
        self,
        team_id: str,
        contact_segments: Optional[set[str]] = None,
        *,
        brand_code: Optional[str] = None,
    ) -> list[dict]:
        """
        Active members of a team joined to User, ordered by sort_order, user_id.
        Returns [{user_id, name, respond_user_id, email, sort_order}] for n8n (so it can
        store ids/names and later pass a preferred_assignee_id to next-assignee).

        When ``contact_segments`` is a non-empty set, the roster is filtered to members
        whose served market segments intersect it - plus untagged members (no segments =
        serves all). If that filter yields nobody, fall back to the full active roster so
        a conversation always resolves to someone. ``None`` / empty set = no filter
        (byte-identical to the pre-segment behaviour).

        ``brand_code`` applies the same rule on the brand axis and is ANDed with the
        segment one, so this roster is exactly the pool ``get_next_assignee`` draws
        from - an id returned here is always one next-assignee could return.
        """
        from sqlalchemy.orm import selectinload

        query = (
            self.db.query(TeamMember, User)
            .join(User, User.id == TeamMember.user_id)
            .filter(
                TeamMember.team_id == team_id,
                User.status == UserStatus.ACTIVE.value,
            )
            .order_by(TeamMember.sort_order.asc().nullslast(), TeamMember.user_id.asc())
        )
        # Only touch the segment relationship when a filter is actually requested,
        # so the no-filter path is byte-identical to the pre-segment behaviour and
        # never queries team_member_market_segments.
        if contact_segments:
            query = query.options(selectinload(TeamMember.market_segments))
        rows = query.all()
        wanted_brand = normalise_brand_code(brand_code)
        brands_by_member = (
            self._brand_codes_by_member([m.id for m, _u in rows]) if wanted_brand else {}
        )
        # One axis at a time, exactly as get_next_assignee narrows the pool: an empty
        # brand match drops the brand and keeps the segment rows, and only an empty
        # segment match goes back to the full roster (never return nobody).
        if contact_segments:
            segment_rows = [
                (member, user)
                for member, user in rows
                if not member.market_segments
                or {str(s.code) for s in member.market_segments} & contact_segments
            ]
            if segment_rows:
                rows = segment_rows
        if wanted_brand:
            brand_rows = [
                (member, user)
                for member, user in rows
                if member_serves_brand(
                    brands_by_member.get(str(member.id)), wanted_brand
                )
            ]
            if brand_rows:
                rows = brand_rows
        return [
            {
                "user_id": user.id,
                "name": user.name or user.email,
                "respond_user_id": user.respond_user_id,
                "email": user.email,
                "sort_order": member.sort_order,
            }
            for member, user in rows
        ]

    def get_member_assignee(
        self, team_id: str, user_id: str, *, brand_code: Optional[str] = None
    ) -> Optional[dict]:
        """
        Return a specific team member as an assignee dict (same shape as get_next_assignee),
        WITHOUT advancing the round-robin cursor. None if the user is not a member of the team.
        Used by the preferred_assignee_id override path.

        ``brand_matched`` follows the same per-assignee rule as the round-robin draw:
        true only when a brand was requested and this member is tagged with it.
        """
        member = (
            self.db.query(TeamMember)
            .filter(TeamMember.team_id == team_id, TeamMember.user_id == user_id)
            .first()
        )
        if not member:
            return None
        wanted_brand = normalise_brand_code(brand_code)
        brand_matched = False
        if wanted_brand:
            tags = self._brand_codes_by_member([member.id]).get(str(member.id)) or set()
            brand_matched = wanted_brand in tags
        user = self.db.query(User).filter(User.id == user_id).first()
        if not user:
            return {
                "id": user_id,
                "email": None,
                "name": None,
                "respond_user_id": None,
                "brand_matched": brand_matched,
            }
        return {
            "id": user.id,
            "email": user.email,
            "name": user.name or user.email,
            "respond_user_id": user.respond_user_id,
            "brand_matched": brand_matched,
        }

    def get_next_assignee_after(
        self, agent_id: str, team_id: str, current_respond_user_id: str
    ) -> Optional[dict]:
        """
        Return the next assignee after the given current assignee (by respond_user_id) in
        round-robin order. Updates the cursor to that next user so "next in line" stays consistent.
        Returns None if the agent/team link or team members are missing, or if
        current_respond_user_id is not in the team.
        """
        from sqlalchemy import and_

        from app.models.base import company_scope

        # Same reasoning as get_next_assignee: team_id already names the company.
        with company_scope(self.db, None):
            link = (
                self.db.query(AgentTeam)
                .filter(
                    and_(
                        AgentTeam.agent_id == agent_id,
                        AgentTeam.team_id == team_id,
                    )
                )
                .first()
            )
        if not link:
            return None
        members = (
            self.db.query(TeamMember)
            .filter(TeamMember.team_id == team_id)
            .order_by(TeamMember.sort_order.asc().nullslast(), TeamMember.user_id.asc())
            .all()
        )
        if not members:
            return None
        user_ids = [_rr_user_id_key(m.user_id) for m in members]
        users = self.db.query(User).filter(User.id.in_(user_ids)).all()
        user_by_id = {str(u.id): u for u in users}
        # Preserve member order and get respond_user_id
        ordered_respond_ids = []
        for uid in user_ids:
            u = user_by_id.get(uid)
            ordered_respond_ids.append(_normalize_respond_user_id(getattr(u, "respond_user_id", None)) if u else None)
        current_norm = _normalize_respond_user_id(str(current_respond_user_id))
        try:
            idx = ordered_respond_ids.index(current_norm)
        except ValueError:
            return None
        next_idx = (idx + 1) % len(user_ids)
        next_user_id = user_ids[next_idx]
        # Update cursor so "next in line" stays consistent
        cursor = (
            self.db.query(AgentTeamRoundRobinCursor)
            .filter(
                and_(
                    AgentTeamRoundRobinCursor.agent_id == agent_id,
                    AgentTeamRoundRobinCursor.team_id == team_id,
                )
            )
            .with_for_update()
            .first()
        )
        if not cursor:
            cursor = AgentTeamRoundRobinCursor(
                agent_id=agent_id,
                team_id=team_id,
                last_assigned_user_id=next_user_id,
            )
            self.db.add(cursor)
        else:
            setattr(cursor, "last_assigned_user_id", next_user_id)
        self.db.commit()
        user = user_by_id.get(str(next_user_id))
        if not user:
            return {"id": next_user_id, "email": None, "name": None, "respond_user_id": None}
        return {
            "id": user.id,
            "email": user.email,
            "name": user.name or user.email,
            "respond_user_id": user.respond_user_id,
        }

    def list_agent_teams(self, agent_id: str) -> list[dict]:
        """Return list of {code, team_id, tier, policy_id} assignments for this agent."""
        rows = (
            self.db.query(AgentTeam.code, AgentTeam.team_id, AgentTeam.tier, AgentTeam.policy_id)
            .filter(AgentTeam.agent_id == agent_id)
            .all()
        )
        return [
            {"code": r[0], "team_id": str(r[1]), "tier": r[2], "policy_id": str(r[3]) if r[3] else None}
            for r in rows
        ]

    def _user_info(self, user: Optional[User]) -> Optional[dict]:
        """Return {id, name, email, respond_user_id, respond_synced} for display; None if user is None."""
        if not user:
            return None
        rid = getattr(user, "respond_user_id", None)
        rid_s = (str(rid).strip() if rid is not None else "") or None
        sync = (getattr(user, "respond_synced", None) or "pending").strip() or "pending"
        return {
            "id": user.id,
            "name": user.name or user.email or user.id,
            "email": user.email,
            "respond_user_id": rid_s,
            "respond_synced": sync,
        }

    def _peek_next_assignee(self, agent_id: str, team_id: str) -> tuple[Optional[str], Optional[str]]:
        """Return (last_assigned_user_id, next_user_id) without updating the cursor.
        Next-in-line considers only RR-eligible members (include_in_round_robin),
        matching get_next_assignee - excluded members are never auto-assigned."""
        members = (
            self.db.query(TeamMember)
            .filter(
                TeamMember.team_id == team_id,
                TeamMember.include_in_round_robin.is_(True),
            )
            .order_by(TeamMember.sort_order.asc().nullslast(), TeamMember.user_id.asc())
            .all()
        )
        if not members:
            return None, None
        user_ids = [_rr_user_id_key(m.user_id) for m in members]
        cursor = (
            self.db.query(AgentTeamRoundRobinCursor)
            .filter(
                AgentTeamRoundRobinCursor.agent_id == agent_id,
                AgentTeamRoundRobinCursor.team_id == team_id,
            )
            .first()
        )
        last_id = str(getattr(cursor, "last_assigned_user_id")) if cursor and getattr(cursor, "last_assigned_user_id", None) is not None else None
        last_key = _rr_user_id_key(last_id) if last_id is not None else ""
        try:
            idx = user_ids.index(last_key) if last_key else -1
        except ValueError:
            idx = -1
        next_idx = (idx + 1) % len(user_ids)
        return last_id, user_ids[next_idx]

    def list_agent_teams_with_round_robin_state(self, agent_id: str) -> list[dict]:
        """This company's assignments, with team name, tier, members (ordered),
        last_assigned and next_in_line (read-only peek).

        Company isolation here comes from the scope filter, not an explicit
        predicate: verified against the running stack that this returns the agent's
        7 Sorento rows under Sorento and nothing under Mocha. Note the filter DOES
        reach this query even though it selects bare columns rather than loading
        AgentTeam entities.
        """
        rows = (
            self.db.query(
                AgentTeam.code,
                AgentTeam.team_id,
                AgentTeam.tier,
                AgentTeam.policy_id,
                AgentTeam.notify_on_extension,
            )
            .filter(
                AgentTeam.agent_id == agent_id,
            )
            .all()
        )
        result = []
        for code, team_id, tier, policy_id, notify_on_extension in rows:
            team_id_str = str(team_id)
            team = self.db.query(Team).filter(Team.id == team_id).first()
            team_name = team.name if team else team_id_str
            members = (
                self.db.query(TeamMember)
                .filter(TeamMember.team_id == team_id)
                .order_by(TeamMember.sort_order.asc().nullslast(), TeamMember.user_id.asc())
                .all()
            )
            member_infos = []
            for m in members:
                u = self.db.query(User).filter(User.id == m.user_id).first()
                info = self._user_info(u) or {
                    "id": m.user_id,
                    "name": m.user_id,
                    "email": None,
                    "respond_user_id": None,
                    "respond_synced": None,
                }
                # Expose RR eligibility so the UI can mark excluded members and the
                # round-robin order reads true to who actually gets auto-assigned.
                info = {**info, "include_in_round_robin": bool(m.include_in_round_robin)}
                member_infos.append(info)
            last_id, next_id = self._peek_next_assignee(agent_id, team_id_str)
            last_user = self.db.query(User).filter(User.id == last_id).first() if last_id else None
            next_user = self.db.query(User).filter(User.id == next_id).first() if next_id else None
            result.append({
                "code": code,
                "team_id": team_id_str,
                "tier": tier,
                "policy_id": str(policy_id) if policy_id else None,
                "notify_on_extension": bool(notify_on_extension),
                "team_name": team_name,
                "members": member_infos,
                "last_assigned": self._user_info(last_user) if last_id else None,
                "next_in_line": self._user_info(next_user) if next_id else None,
            })
        return result

    def _validate_tier1_invariant_for_assignments(self, agent_id: str, assignments: list[dict]) -> None:
        """
        Tier-1 membership invariant (PLAN-tier1-teamset-invariant), TEAM-level: linking
        teams at tier 1 must not leave any user a member of two DIFFERENT tier-1-linked
        teams WITHIN THE SAME TEAM SET (AgentTeam.code). Tier-1 membership in two different
        team sets is legal: the tracking's team_set_code tells derivation which one applies.
        The same team linked at tier 1 under many agents is fine (shared executive pools).
        Cross-tier reuse of a tier-1 team (same team at tier 2/3 elsewhere) does not break
        routing derivation, so it is warn only.
        """
        import logging

        from app.services.form_sla_service import form_sla_agent_codes

        logger = logging.getLogger(__name__)
        tier1_assignments: list[tuple[str, str]] = []  # (code, team_id)
        for a in assignments:
            raw_code = a.get("code")
            code = str(raw_code).strip() if raw_code is not None else ""
            team_id = a.get("team_id")
            tier = a.get("tier")
            if code and team_id and tier == 1:
                tier1_assignments.append((code, str(team_id)))
        if not tier1_assignments:
            return

        # Only CONVERSATION-SLA agents' tier-1 links count (see membership-invariant
        # docstring). If THIS agent owns a form-SLA pipeline, its tier-1 links never
        # constrain membership - skip the whole check.
        form_codes = form_sla_agent_codes(self.db)
        this_agent = self.db.query(AccessAgent).filter(AccessAgent.id == agent_id).first()
        if this_agent and form_codes and this_agent.code in form_codes:
            return

        # member user_ids per tier-1 team in this payload
        team_ids = [tid for _, tid in tier1_assignments]
        member_rows = (
            self.db.query(TeamMember.team_id, TeamMember.user_id)
            .filter(TeamMember.team_id.in_(team_ids))
            .all()
        )
        members_by_team: dict[str, set[str]] = {}
        for tid, uid in member_rows:
            members_by_team.setdefault(str(tid), set()).add(str(uid))

        # Other tier-1-linked teams each user belongs to. Exclude the teams in this
        # payload (same team under many agents is allowed); exclude this agent's links
        # (being replaced wholesale).
        all_user_ids = {u for users in members_by_team.values() for u in users}
        other_links = []
        if all_user_ids:
            other_links_q = (
                self.db.query(TeamMember.user_id, AgentTeam.agent_id, AgentTeam.code, AgentTeam.team_id, Team.name)
                .join(AgentTeam, AgentTeam.team_id == TeamMember.team_id)
                .join(Team, Team.id == AgentTeam.team_id)
                .join(AccessAgent, AccessAgent.id == AgentTeam.agent_id)
                .filter(
                    TeamMember.user_id.in_(all_user_ids),
                    AgentTeam.tier == 1,
                    AgentTeam.agent_id != agent_id,
                    AgentTeam.team_id.notin_(team_ids),
                    # Per company (AC-H4). The invariant exists so escalation can
                    # derive ONE tier-1 team for a user; across companies there is no
                    # ambiguity, since the two ladders are separate. Filtered
                    # explicitly rather than left to the scope filter: this is a
                    # multi-entity join selecting bare columns, and it was observed
                    # matching a Sorento membership while the active company was
                    # Mocha, blocking a legitimate Mocha tier-1 assignment.
                    AgentTeam.company_id == self._active_company_id(),
                )
            )
            # A conflict against a FORM-SLA agent's tier-1 team must NOT block - 
            # only conversation-SLA tier-1 membership is unique.
            if form_codes:
                other_links_q = other_links_q.filter(AccessAgent.code.notin_(form_codes))
            other_links = other_links_q.all()

        # Teams each user belongs to within this payload's tier-1 assignments, keyed
        # per team set: the invariant is scoped per code, so the same user leading two
        # teams in two DIFFERENT sets is legal.
        local_teams_by_user_code: dict[tuple[str, str], set[str]] = {}
        for code, tid in tier1_assignments:
            for uid in members_by_team.get(tid, set()):
                local_teams_by_user_code.setdefault((uid, str(code)), set()).add(tid)

        # An existing tier-1 link only conflicts when it sits in a team set this
        # payload also puts the user at tier 1 in. That filtering is the lookup below:
        # the map is keyed per (user, code) and only ever read at the keys the payload
        # produced, so entries for other team sets are simply never consulted. The
        # tuple carries only what the key does not.
        conflict_by_user_code: dict[tuple[str, str], tuple[str, str]] = {}
        for uid, other_agent_id, code, _tid, team_name in other_links:
            conflict_by_user_code.setdefault(
                (str(uid), str(code)), (str(other_agent_id), str(team_name))
            )

        for (uid, code), local_teams in local_teams_by_user_code.items():
            other = conflict_by_user_code.get((uid, code))
            if len(local_teams) <= 1 and not other:
                continue
            user = self.db.query(User).filter(User.id == uid).first()
            user_label = (user.name or user.email) if user else uid
            if other:
                other_agent_id, other_team = other
                agent = self.db.query(AccessAgent).filter(AccessAgent.id == other_agent_id).first()
                agent_label = agent.code if agent else other_agent_id
                raise handle_validation_error(
                    f"User '{user_label}' is already in tier-1 team '{other_team}' "
                    f"(agent '{agent_label}', team set '{code}'). "
                    "A user may only belong to one tier-1 team per team set."
                )
            raise handle_validation_error(
                f"User '{user_label}' would belong to multiple tier-1 teams "
                f"({sorted(local_teams)}) in team set '{code}'. "
                "A user may only belong to one tier-1 team per team set."
            )

        # Warn-only: tier-1 team reused at other tiers (here or under other agents).
        reused = (
            self.db.query(AgentTeam.team_id, AgentTeam.agent_id, AgentTeam.code, AgentTeam.tier)
            .filter(
                AgentTeam.team_id.in_(team_ids),
                AgentTeam.tier.in_([2, 3]),
                AgentTeam.agent_id != agent_id,
            )
            .all()
        )
        local_reuse = [
            (a.get("code"), str(a.get("team_id")), a.get("tier"))
            for a in assignments
            if a.get("tier") in (2, 3) and str(a.get("team_id")) in team_ids
        ]
        if reused or local_reuse:
            logger.warning(
                "Tier-1 team(s) %s for agent %s are also linked at tier 2/3 (%s); allowed, "
                "but tier-1 members will implicitly appear at higher tiers.",
                team_ids,
                agent_id,
                [(str(t), str(aid), str(c), int(tr)) for t, aid, c, tr in reused] + local_reuse,
            )

    # ------------------------------------------------ field-level access

    def list_field_access(self, agent_id: str, contact_id: str | None = None) -> dict:
        """The complete tick-list of fields this agent may reveal.

        Built from the CODE registry, not from whatever rows happen to exist, so a
        field added after the migration ran still appears (unticked) instead of
        being invisible and therefore ungrantable.

        With `contact_id`, each field also carries what THIS contact actually gets:
        `override` (None = follows the agent) and `effective`. An admin deciding
        "should this dealer see the gatepass" needs to see the inherited value in
        the same row, or they cannot tell an explicit deny from an untouched
        default - and those are different intentions.
        """
        from app.models.access import AccessAgent, AgentFieldAccess, RespondContact
        from app.services.field_access import GATED_FIELDS, field_label

        agent = self.db.query(AccessAgent).filter(AccessAgent.id == agent_id).one_or_none()
        if agent is None:
            raise HTTPException(status_code=404, detail="Access agent not found")

        rows = (
            self.db.query(AgentFieldAccess)
            .filter(AgentFieldAccess.agent_code == agent.code)
            .all()
        )
        defaults = {
            (r.resource, r.field_key): bool(r.is_allowed)
            for r in rows
            if r.contact_id is None
        }

        overrides_for_contact = {
            (r.resource, r.field_key): bool(r.is_allowed)
            for r in rows
            if contact_id and r.contact_id == str(contact_id)
        }

        fields = []
        for resource, owned in GATED_FIELDS.items():
            for field_key, owner_code in owned.items():
                if owner_code != agent.code:
                    continue
                # No row means denied, so the tick is empty rather than absent.
                default = defaults.get((resource, field_key), False)
                entry = {
                    "resource": resource,
                    "field_key": field_key,
                    "label": field_label(field_key),
                    "is_allowed": default,
                }
                if contact_id:
                    override = overrides_for_contact.get((resource, field_key))
                    entry["override"] = override
                    entry["effective"] = default if override is None else override
                fields.append(entry)

        override_rows = [r for r in rows if r.contact_id is not None]
        names = {}
        if override_rows:
            names = dict(
                self.db.query(RespondContact.id, RespondContact.name)
                .filter(RespondContact.id.in_({r.contact_id for r in override_rows}))
                .all()
            )
        overrides = [
            {
                "resource": r.resource,
                "field_key": r.field_key,
                "label": field_label(r.field_key),
                "contact_id": r.contact_id,
                "contact_name": names.get(r.contact_id),
                "is_allowed": bool(r.is_allowed),
            }
            for r in override_rows
        ]

        return {"agent_code": agent.code, "fields": fields, "overrides": overrides}

    def set_field_access(
        self, agent_id: str, entries: list[dict], actor: str | None = None
    ) -> None:
        """Upsert the given ticks. Entries not sent are left alone.

        Deliberately not a replace-all: the screen may be showing one resource
        while another admin edits a second, and a blanket delete would silently
        revoke fields nobody touched.
        """
        import uuid as _uuid

        from app.models.access import AccessAgent, AgentFieldAccess
        from app.services.field_access import GATED_FIELDS

        agent = self.db.query(AccessAgent).filter(AccessAgent.id == agent_id).one_or_none()
        if agent is None:
            raise HTTPException(status_code=404, detail="Access agent not found")

        for entry in entries or []:
            resource = entry.get("resource")
            field_key = entry.get("field_key")
            owner = GATED_FIELDS.get(resource, {}).get(field_key)
            if owner is None:
                # An unregistered field gates nothing, so a row for it would be a
                # tick that silently does not work. Reject rather than store it.
                raise HTTPException(
                    status_code=422,
                    detail=f"{resource}.{field_key} is not a gated field",
                )
            if owner != agent.code:
                raise HTTPException(
                    status_code=422,
                    detail=f"{resource}.{field_key} belongs to agent '{owner}', not '{agent.code}'",
                )

            contact_id = entry.get("contact_id") or None
            existing = (
                self.db.query(AgentFieldAccess)
                .filter(
                    AgentFieldAccess.agent_code == agent.code,
                    AgentFieldAccess.resource == resource,
                    AgentFieldAccess.field_key == field_key,
                    AgentFieldAccess.contact_id.is_(None)
                    if contact_id is None
                    else AgentFieldAccess.contact_id == contact_id,
                )
                .one_or_none()
            )
            wanted = entry.get("is_allowed", True)
            if wanted is None:
                # Clear the override: this contact goes back to following the agent.
                # Only meaningful per-contact - a null agent-wide would mean "deny
                # by absence", which is what is_allowed=False already says.
                if contact_id is None:
                    raise HTTPException(
                        status_code=422,
                        detail="is_allowed=null only clears a per-contact override",
                    )
                if existing is not None:
                    self.db.delete(existing)
                continue

            if existing is not None:
                existing.is_allowed = bool(wanted)
                # Attribution goes on updated_by, not created_by: the bootstrap
                # pre-seeds every row denied, so this branch is the one a real
                # grant takes and created_by would be NULL forever.
                existing.updated_by = actor
            else:
                self.db.add(
                    AgentFieldAccess(
                        id=str(_uuid.uuid4()),
                        agent_code=agent.code,
                        resource=resource,
                        field_key=field_key,
                        contact_id=contact_id,
                        is_allowed=bool(wanted),
                        created_by=actor,
                        updated_by=actor,
                    )
                )

        self.db.commit()

    def _active_company_id(self) -> str:
        """The single company this write applies to.

        An agent's Team Sets screen edits ONE company at a time (the active one), so
        every write here is scoped to it. Falls back to the incumbent when the scope
        is not a single company - a system / all-companies caller editing team sets
        means Sorento, never "all of them at once".
        """
        from app.models.base import get_company_scope
        from app.services.company_routing_service import DEFAULT_COMPANY_ID

        scope = get_company_scope(self.db)
        if isinstance(scope, frozenset) and len(scope) == 1:
            return str(next(iter(scope)))
        return DEFAULT_COMPANY_ID

    def set_agent_teams(self, agent_id: str, assignments: list[dict]) -> None:
        """Replace THIS COMPANY's team links for the agent with the given assignments.

        Scoped to one company deliberately. The old unscoped delete would wipe the
        other company's team sets every time an admin saved this screen, because the
        payload only ever contains the company they are looking at.
        """
        company_id = self._active_company_id()
        seen_keys: set[tuple[str, str | int]] = set()
        for a in assignments or []:
            raw_code = a.get("code")
            code = str(raw_code).strip() if raw_code is not None else ""
            team_id = a.get("team_id")
            tier = a.get("tier")
            if tier is not None and (tier < 1 or tier > 3):
                tier = None
            if not code or not team_id:
                continue
            # Matches partial unique indexes: one row per (agent, code) when tier is null;
            # one row per (agent, code, tier) when tier is set.
            key: tuple[str, str | int] = (code, tier if tier is not None else "__null_tier__")
            if key in seen_keys:
                raise handle_validation_error(
                    f"cannot have duplicate code {code} in different groups"
                )
            seen_keys.add(key)

        self._validate_tier1_invariant_for_assignments(agent_id, assignments or [])

        # One SLA policy per team-set code: cast the first non-null policy_id seen for
        # each code onto every tier row of that code so new tier rows inherit it (D3).
        policy_by_code: dict[str, str] = {}
        for a in assignments or []:
            raw_code = a.get("code")
            code = str(raw_code).strip() if raw_code is not None else ""
            policy_id = a.get("policy_id")
            policy_id = str(policy_id).strip() if policy_id else None
            if code and policy_id and code not in policy_by_code:
                policy_by_code[code] = policy_id

        # company_id is filtered EXPLICITLY, not left to the scope filter: whether the
        # auto-filter reaches a bulk DELETE is a SQLAlchemy detail, and being wrong
        # about it here deletes the other company's routing.
        self.db.query(AgentTeam).filter(
            AgentTeam.agent_id == agent_id, AgentTeam.company_id == company_id
        ).delete(synchronize_session=False)
        for a in assignments or []:
            raw_code = a.get("code")
            code = str(raw_code).strip() if raw_code is not None else ""
            team_id = a.get("team_id")
            tier = a.get("tier")
            if tier is not None and (tier < 1 or tier > 3):
                tier = None
            if code and team_id:
                self.db.add(AgentTeam(
                    agent_id=agent_id,
                    code=code,
                    team_id=team_id,
                    tier=tier,
                    company_id=company_id,
                    policy_id=policy_by_code.get(code),
                    notify_on_extension=bool(a.get("notify_on_extension", True)),
                ))
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            # Not every integrity error here is a duplicate code. Picking another
            # company's SLA policy trips the (policy_id, company_id) composite FK,
            # and reporting that as "duplicate code" sends the admin looking at the
            # wrong field entirely.
            detail = str(getattr(exc, "orig", exc))
            if "fk_agent_teams_policy_company" in detail:
                raise handle_validation_error(
                    "That SLA policy belongs to another company. Pick a policy from "
                    "this company, or create one for it."
                ) from None
            if "fk_agent_teams_team_company" in detail:
                raise handle_validation_error(
                    "That team belongs to another company. Pick a team from this company."
                ) from None
            raise handle_validation_error(
                "cannot have duplicate code in different groups"
            ) from None

    def get_team_id_by_code(
        self, agent_id: str, code: str, *, company_id: str
    ) -> str | None:
        """Resolve team_id for agent+code+company. If several tiers share this code, returns one row (undefined which). Prefer get_team_id_by_tier + list_team_ids_for_agent_code for round-robin."""
        row = (
            self.db.query(AgentTeam.team_id)
            .filter(
                AgentTeam.agent_id == agent_id,
                AgentTeam.code == code,
                AgentTeam.company_id == str(company_id),
            )
            .first()
        )
        return str(row[0]) if row else None

    def list_team_ids_for_agent_code(
        self, agent_id: str, code: str, *, company_id: str
    ) -> list[str]:
        """All team_ids for this agent+company with the given assignment code (e.g. one per SLA tier)."""
        from sqlalchemy import and_

        c = str(code).strip() if code is not None else ""
        if not c:
            return []
        rows = (
            self.db.query(AgentTeam.team_id)
            .filter(
                and_(
                    AgentTeam.agent_id == agent_id,
                    AgentTeam.code == c,
                    AgentTeam.company_id == str(company_id),
                )
            )
            .all()
        )
        return [str(r[0]) for r in rows]

    def get_team_id_by_tier(
        self,
        agent_id: str,
        tier: int,
        team_set_code: Optional[str] = None,
        *,
        company_id: str,
    ) -> str | None:
        """Resolve team_id for agent+tier+company, optionally constrained to one team set code.

        ``company_id`` is keyword-only and required on purpose: a positional optional
        would let a call site silently keep the old cross-company behaviour, which is
        the bug class this exists to close. A missed caller is a TypeError at import
        or test time, not a wrong assignment in production.
        """
        if tier is None or tier < 1 or tier > 3:
            return None

        # Read the ladder under the company the CALLER named, not the company the
        # request happens to be switched to. `AgentTeam` is company-scoped, so the
        # ambient filter would be ANDed on top and silently win: an admin switched to
        # company B acting on a company-A tracker got an empty ladder and a manual
        # escalate that 422'd with "No higher-tier team configured" on a ladder that
        # was fully configured (AC-E3 - the ladder is the tracker's company's).
        # Safe scope-free: the explicit `company_id` predicate below pins exactly one
        # company, so suspending the ambient filter cannot widen the result.
        from app.models.base import company_scope

        with company_scope(self.db, None):
            query = self.db.query(AgentTeam.team_id).filter(
                AgentTeam.agent_id == agent_id,
                AgentTeam.tier == tier,
                AgentTeam.company_id == str(company_id),
            )
            if team_set_code:
                query = query.filter(AgentTeam.code == team_set_code)

            rows = query.all()
        if not rows:
            return None
        if len(rows) > 1:
            # Previously this returned rows[0] whenever a team_set_code was given, so
            # the FIRST duplicate row won silently. With company in the key that
            # duplicate could be another company's team, i.e. a silent wrong-company
            # escalation. Ambiguity is now always an error (AC-C6).
            detail = (
                f"Multiple team sets found for tier {tier}. Provide team_set_code to "
                "resolve escalation target."
                if not team_set_code
                else (
                    f"Multiple teams found for tier {tier} in team set "
                    f"{team_set_code!r} for this company. Remove the duplicate team-set row."
                )
            )
            raise handle_conflict(detail)
        return str(rows[0][0])

    def get_tier_team_and_notify(
        self,
        agent_id: str,
        tier: int,
        team_set_code: Optional[str] = None,
        *,
        company_id: str,
    ) -> Optional[tuple[str, bool]]:
        """``(team_id, notify_on_extension)`` for agent+tier+company (constrained to a
        team set), or None when no team is configured at that exact tier. Used by the
        extension notify fan-up to decide, per tier, whether that tier's team is notified."""
        if tier is None or tier < 1 or tier > 3:
            return None
        query = self.db.query(AgentTeam.team_id, AgentTeam.notify_on_extension).filter(
            AgentTeam.agent_id == agent_id,
            AgentTeam.tier == tier,
            AgentTeam.company_id == str(company_id),
        )
        if team_set_code:
            query = query.filter(AgentTeam.code == team_set_code)
        rows = query.all()
        if not rows:
            return None
        if len(rows) > 1 and not team_set_code:
            raise handle_conflict(
                f"Multiple team sets found for tier {tier}. Provide team_set_code to resolve the notify target."
            )
        return str(rows[0][0]), bool(rows[0][1])

    def get_agent_id_by_code(self, code: str) -> str | None:
        """Resolve agent_id from access agent code. Returns None if not found."""
        agent = self.db.query(AccessAgent.id).filter(AccessAgent.code == code).first()
        return str(agent[0]) if agent else None

    def resolve_policy_id_for(
        self, agent_id: str, team_set_code: str, *, company_id: str
    ) -> Optional[str]:
        """Resolve the single SLA policy bound to ``(agent_id, team_set_code, company)``.

        Company-scoped because policies are now per company: without it, a team set
        configured in both companies returns two distinct policy ids and this raises
        a bogus "inconsistent binding" 409.

        Distinct non-null ``policy_id`` over the team-set rows:
      - zero rows  -> None (caller decides: rollout fallback vs 422 end-state)
      - exactly one -> that policy_id
      - more than one distinct -> 409 misconfig (inconsistent binding)
        """
        c = str(team_set_code).strip() if team_set_code is not None else ""
        if not agent_id or not c:
            return None
        rows = (
            self.db.query(AgentTeam.policy_id)
            .filter(
                AgentTeam.agent_id == agent_id,
                AgentTeam.code == c,
                AgentTeam.company_id == str(company_id),
                AgentTeam.policy_id.isnot(None),
            )
            .distinct()
            .all()
        )
        policy_ids = {str(r[0]) for r in rows if r[0] is not None}
        if not policy_ids:
            return None
        if len(policy_ids) > 1:
            raise handle_conflict(
                f"SLA policy is inconsistent across the '{c}' team set; rebind a single policy."
            )
        return next(iter(policy_ids))

    def resolve_team_with_tier_fallback(
        self,
        agent_id: str,
        start_tier: int,
        team_set_code: Optional[str] = None,
        *,
        company_id: str,
    ) -> Optional[tuple]:
        """Find the first existing team at or ABOVE ``start_tier`` for this agent's
        team set, returning ``(team_id, actual_tier)`` or None.

        Reusable for BOTH initial SLA assignment (start_tier=1) and escalation
        (start_tier=current_tier+1): a missing intermediate tier is skipped instead
        of blocking - e.g. assign at tier 1 but only tier 2 exists -> use tier 2;
        escalate to tier 2 but only tier 3 exists -> use tier 3. Caps at tier 3.
        """
        try:
            s = int(start_tier)
        except (TypeError, ValueError):
            return None
        if s < 1:
            s = 1
        for tier in range(s, 4):
            team_id = self.get_team_id_by_tier(
                agent_id, tier, team_set_code=team_set_code, company_id=company_id
            )
            if team_id:
                return team_id, tier
        return None

    def get_user_tier_in_team_set(
        self,
        agent_id: str,
        user_id: str,
        team_set_code: Optional[str] = None,
        *,
        company_id: str,
    ) -> Optional[int]:
        """Return the tier (1-3) at which ``user_id`` is a member of this agent's
        team set, or None if not a member of any tier. Used to route a form's
        configured default approver to their own tier in the approval team set
        (e.g. a director sitting at tier 3) instead of the tier-1 default."""
        for tier in (1, 2, 3):
            team_id = self.get_team_id_by_tier(
                agent_id, tier, team_set_code=team_set_code, company_id=company_id
            )
            if not team_id:
                continue
            member = (
                self.db.query(TeamMember)
                .filter(TeamMember.team_id == team_id, TeamMember.user_id == str(user_id))
                .first()
            )
            if member:
                return tier
        return None


def descendant_team_ids(db: Session, team_ids) -> set:
    """All team ids at-or-below ``team_ids`` (the seeds themselves + every
    descendant at any depth via teams.parent_team_id).

    Uses a recursive CTE on Postgres (and SQLite, which also supports
    WITH RECURSIVE) with a Python BFS fallback so it works in both prod and the
    sqlite-backed test fixtures regardless of dialect quirks.
    """
    seeds = {str(t) for t in (team_ids or []) if t}
    if not seeds:
        return set()

    # On Postgres, use a recursive CTE (single round-trip, any depth). On other
    # dialects (sqlite test fixtures) the pg UUID column strips hyphens in raw SQL,
    # so use a dialect-agnostic Python BFS over the parent edges via the ORM (which
    # applies the column's bind/result processors correctly).
    dialect = getattr(getattr(db, "bind", None), "dialect", None)
    dialect_name = getattr(dialect, "name", "")
    if dialect_name == "postgresql":
        try:
            from sqlalchemy import text as _text

            placeholders = ", ".join(f":s{i}" for i in range(len(seeds)))
            params = {f"s{i}": v for i, v in enumerate(seeds)}
            sql = _text(
                f"""
                WITH RECURSIVE descendants(id) AS (
                    SELECT id FROM teams WHERE id IN ({placeholders})
                    UNION
                    SELECT t.id FROM teams t
                    JOIN descendants d ON t.parent_team_id = d.id
                )
                SELECT id FROM descendants
                """
            )
            rows = db.execute(sql, params).fetchall()
            return {str(r[0]) for r in rows}
        except Exception:  # noqa: BLE001 - fall back to the ORM BFS
            db.rollback()

    edges = db.query(Team.id, Team.parent_team_id).all()
    children: dict = {}
    for tid, parent in edges:
        if parent is not None:
            children.setdefault(str(parent), []).append(str(tid))
    out: set = set()
    stack = list(seeds)
    while stack:
        cur = stack.pop()
        if cur in out:
            continue
        out.add(cur)
        stack.extend(children.get(cur, []))
    return out


class TeamService:
    """Service for team and team member operations."""

    def __init__(self, db: Session):
        self.db = db

    def descendant_team_ids(self, team_ids) -> set:
        """Instance wrapper around the module-level recursive descendants helper."""
        return descendant_team_ids(self.db, team_ids)

    def _guard_parent_team_cycle(
        self, team_id: Optional[str], parent_team_id: Optional[str]
    ) -> None:
        """Reject a parent assignment that would create a cycle: a team cannot be
        its own parent, nor can it pick one of its own descendants as parent."""
        if parent_team_id is None:
            return
        if team_id is not None and str(parent_team_id) == str(team_id):
            raise handle_validation_error("A team cannot be its own parent (cannot create a cycle).")
        if team_id is not None:
            # The new parent must not be at-or-below this team in the hierarchy.
            if str(parent_team_id) in self.descendant_team_ids([str(team_id)]):
                raise handle_validation_error(
                    "Cannot set that parent: it is a descendant of this team (cannot create a cycle)."
                )

    def _guard_parent_team_company(
        self, company_id: Optional[str], parent_team_id: Optional[str]
    ) -> None:
        """A team's parent must belong to the same company (AC-C7).

        Not cosmetic: ``descendant_team_ids`` grants a parent team's members
        visibility and act rights over EVERY descendant at any depth, so a
        cross-company parent hands one brand's staff the other brand's work. This is
        the write-side half of the check migration 320 performs before locking the
        column in.
        """
        if not parent_team_id:
            return
        # Read the parent WITHOUT the company filter. Teams are company-scoped now, so
        # a scoped read of another company's team returns None, and the guard would
        # report "not found" for the very case it exists to catch.
        from app.models.base import company_scope

        with company_scope(self.db, None):
            parent = self.db.query(Team).filter(Team.id == str(parent_team_id)).first()
        if parent is None:
            raise handle_validation_error("Parent team not found.")
        parent_company = str(getattr(parent, "company_id", "") or "")
        if company_id and parent_company and parent_company != str(company_id):
            raise handle_validation_error(
                "A team's parent must belong to the same company. A parent team's "
                "members can act on every team below it, so the hierarchy cannot "
                "cross companies."
            )

    def _guard_member_company_grant(self, team_id: str, user_id: str) -> None:
        """A user may only join a team in a company they are granted (AC-G1).

        Membership drives assignment, so a member with no grant for the team's
        company would be handed work in a company they cannot even open.
        """
        from app.models.base import company_scope
        from app.models.company import Company, UserCompany

        # Scope-free for the same reason as the parent guard: a scoped read of another
        # company's team returns None, and returning early on None would make this
        # guard fail OPEN in exactly the cross-company case it is meant to block.
        with company_scope(self.db, None):
            team = self.db.query(Team).filter(Team.id == str(team_id)).first()
        company_id = str(getattr(team, "company_id", "") or "") if team else ""
        if not company_id:
            return
        granted = (
            self.db.query(UserCompany.id)
            .filter(
                UserCompany.user_id == str(user_id),
                UserCompany.company_id == company_id,
            )
            .first()
        )
        if granted is None:
            company = (
                self.db.query(Company.name, Company.code)
                .filter(Company.id == company_id)
                .first()
            )
            label = (company[0] or company[1]) if company else company_id
            raise handle_validation_error(
                f"That user has no access to {label}. Grant them the company before "
                "adding them to one of its teams."
            )

    def _member_previews_for(self, team_ids) -> dict:
        """Grouped member preview ({user_id, name}) per team id. One query, no N+1.

        Names resolve to ``User.name or User.email`` so the UI never renders a UUID.
        """
        ids = [str(t) for t in (team_ids or []) if t]
        out: dict = {}
        if not ids:
            return out
        rows = (
            self.db.query(TeamMember.team_id, TeamMember.user_id, User.name, User.email)
            .join(User, User.id == TeamMember.user_id)
            .filter(TeamMember.team_id.in_(ids))
            .order_by(TeamMember.sort_order.asc().nullslast(), User.name.asc())
            .all()
        )
        for team_id, user_id, name, email in rows:
            out.setdefault(str(team_id), []).append(
                {"user_id": str(user_id), "name": name or email or str(user_id)}
            )
        return out

    def _serialize_team(self, team: Team, members: Optional[list] = None) -> dict:
        """Shape a Team ORM row to the ``TeamResponse`` dict.

        Always return a dict (never the ORM object) so Pydantic's ``from_attributes``
        does not coerce the ``Team.members`` relationship (List[TeamMember], no
        ``name``) into ``TeamMemberPreview`` and 500.
        """
        if members is None:
            members = self._member_previews_for([team.id]).get(str(team.id), [])
        return {
            "id": str(team.id),
            "name": team.name,
            "description": team.description,
            "parent_team_id": str(team.parent_team_id) if team.parent_team_id else None,
            "created_at": team.created_at,
            "member_count": len(members),
            "members": members,
        }

    def list_teams(self):
        """List all teams with a member preview (count + human-readable names)."""
        teams = self.db.query(Team).order_by(Team.name.asc()).all()
        members_by_team = self._member_previews_for([t.id for t in teams])
        return [
            self._serialize_team(t, members_by_team.get(str(t.id), [])) for t in teams
        ]

    def get_team(self, team_id: str) -> Team:
        """Get team ORM row by ID (raw; callers that serialize use ``get_team_view``)."""
        t = self.db.query(Team).filter(Team.id == team_id).first()
        if not t:
            raise handle_not_found("Team", team_id)
        return t

    def get_team_view(self, team_id: str) -> dict:
        """Get a single team shaped to ``TeamResponse`` (with member preview)."""
        return self._serialize_team(self.get_team(team_id))

    def create_team(self, data: TeamCreate) -> dict:
        """Create a team. Returns the ``TeamResponse`` dict shape."""
        payload = data.model_dump()
        # New team has no id yet, so only the self-parent case is possible here;
        # descendant cycles are impossible until children exist.
        self._guard_parent_team_cycle(None, payload.get("parent_team_id"))
        # company_id is auto-stamped from the request scope by CompanyScopedMixin's
        # before_insert, so read it back from the scope rather than the payload.
        from app.models.base import get_company_scope

        scope = get_company_scope(self.db)
        stamped = next(iter(scope)) if isinstance(scope, frozenset) and len(scope) == 1 else None
        self._guard_parent_team_company(stamped, payload.get("parent_team_id"))
        t = Team(**payload)
        self.db.add(t)
        self.db.commit()
        self.db.refresh(t)
        return self._serialize_team(t)

    def update_team(self, team_id: str, data: TeamUpdate) -> dict:
        """Update a team. Returns the ``TeamResponse`` dict shape."""
        t = self.get_team(team_id)
        payload = data.model_dump(exclude_unset=True)
        if "parent_team_id" in payload:
            self._guard_parent_team_cycle(team_id, payload.get("parent_team_id"))
            self._guard_parent_team_company(
                str(getattr(t, "company_id", "") or "") or None,
                payload.get("parent_team_id"),
            )
        for k, v in payload.items():
            setattr(t, k, v)
        self.db.commit()
        self.db.refresh(t)
        return self._serialize_team(t)

    def delete_team(self, team_id: str) -> None:
        """Delete a team (cascades to members and agent_teams)."""
        t = self.get_team(team_id)
        self.db.delete(t)
        self.db.commit()

    def list_team_members(self, team_id: str):
        """List members of a team ordered by sort_order, user_id."""
        self.get_team(team_id)
        return (
            self.db.query(TeamMember)
            .filter(TeamMember.team_id == team_id)
            .order_by(TeamMember.sort_order.asc().nullslast(), TeamMember.user_id.asc())
            .all()
        )

    def _validate_tier1_membership_invariant(self, team_id: str, user_id: str) -> None:
        """
        Tier-1 membership invariant (PLAN-tier1-teamset-invariant), TEAM-level: a user may
        belong to at most ONE team that is linked at tier 1 under a CONVERSATION-SLA agent
        PER TEAM SET (AgentTeam.code), so assignee-driven routing derivation is unambiguous
        once the tracking's team_set_code is known. Tier-1 membership in two DIFFERENT team
        sets is legal (e.g. Marketing - Promotion and Marketing - Product under the same
        agent). FORM-SLA agents (their code owns rows in form_sla_configs) route via
        FormSLAConfig stages, not membership derivation, so their tier-1 teams do NOT count
        (a user may sit in many form-SLA tier-1 teams plus one conversation-SLA tier-1 team
        per team set). The same team linked at tier 1 under many agents is fine (shared
        executive pools); derivation prefers the tracking's current agent and falls back to
        the deterministic first link.
        """
        from app.services.form_sla_service import form_sla_agent_codes

        form_codes = form_sla_agent_codes(self.db)

        # Which team SETS does the target team carry a CONVERSATION-SLA tier-1 link
        # under? A team linked at tier 1 ONLY under form-SLA agents can never conflict.
        new_links_q = (
            self.db.query(AgentTeam)
            .join(AccessAgent, AccessAgent.id == AgentTeam.agent_id)
            .filter(AgentTeam.team_id == team_id, AgentTeam.tier == 1)
        )
        if form_codes:
            new_links_q = new_links_q.filter(AccessAgent.code.notin_(form_codes))
        new_links = new_links_q.all()
        new_codes = {str(l.code) for l in new_links if getattr(l, "code", None) is not None}
        if not new_codes:
            return

        # Other tier-1 teams the user is in that carry a CONVERSATION-SLA link UNDER
        # ONE OF THOSE TEAM SETS. A tier-1 team in a different set is not a conflict:
        # the tracking's team_set_code tells derivation which one applies.
        existing_q = (
            self.db.query(AgentTeam, Team.name)
            .join(TeamMember, TeamMember.team_id == AgentTeam.team_id)
            .join(Team, Team.id == AgentTeam.team_id)
            .join(AccessAgent, AccessAgent.id == AgentTeam.agent_id)
            .filter(
                TeamMember.user_id == user_id,
                AgentTeam.tier == 1,
                AgentTeam.team_id != team_id,
                AgentTeam.code.in_(sorted(new_codes)),
            )
        )
        if form_codes:
            existing_q = existing_q.filter(AccessAgent.code.notin_(form_codes))
        existing = existing_q.all()
        if not existing:
            return

        user = self.db.query(User).filter(User.id == user_id).first()
        user_label = (user.name or user.email) if user else user_id
        link, team_name = existing[0]
        agent = (
            self.db.query(AccessAgent).filter(AccessAgent.id == link.agent_id).first()
        )
        agent_label = agent.code if agent else str(link.agent_id)
        raise handle_validation_error(
            f"User '{user_label}' is already in tier-1 team '{team_name}' "
            f"(agent '{agent_label}', team set '{link.code}'). "
            "A user may only belong to one tier-1 team per team set."
        )

    def add_team_member(
        self,
        team_id: str,
        user_id: str,
        sort_order: Optional[int] = None,
        include_in_round_robin: bool = True,
    ) -> TeamMember:
        """Add a user to a team."""
        self.get_team(team_id)
        existing = (
            self.db.query(TeamMember)
            .filter(TeamMember.team_id == team_id, TeamMember.user_id == user_id)
            .first()
        )
        if existing:
            raise handle_conflict("User is already a member of this team.")
        self._guard_member_company_grant(team_id, user_id)
        self._validate_tier1_membership_invariant(team_id, user_id)
        m = TeamMember(
            team_id=team_id,
            user_id=user_id,
            sort_order=sort_order,
            include_in_round_robin=bool(include_in_round_robin),
        )
        self.db.add(m)
        self.db.commit()
        self.db.refresh(m)
        return m

    def update_team_member(
        self,
        team_id: str,
        user_id: str,
        *,
        include_in_round_robin: Optional[bool] = None,
        sort_order: Optional[int] = None,
    ) -> TeamMember:
        """Update a team member's RR eligibility / sort order."""
        m = (
            self.db.query(TeamMember)
            .filter(TeamMember.team_id == team_id, TeamMember.user_id == user_id)
            .first()
        )
        if not m:
            raise handle_not_found("Team member", f"{team_id}/{user_id}")
        if include_in_round_robin is not None:
            setattr(m, "include_in_round_robin", bool(include_in_round_robin))
        if sort_order is not None:
            setattr(m, "sort_order", sort_order)
        self.db.commit()
        self.db.refresh(m)
        return m

    def remove_team_member(self, team_id: str, user_id: str) -> None:
        """Remove a user from a team."""
        m = (
            self.db.query(TeamMember)
            .filter(TeamMember.team_id == team_id, TeamMember.user_id == user_id)
            .first()
        )
        if not m:
            raise handle_not_found("Team member", f"{team_id}/{user_id}")
        self.db.delete(m)
        self.db.commit()
