"""SLA service for business logic."""
import logging
import re
from sqlalchemy.orm import Session
from sqlalchemy import or_, func
from sqlalchemy.exc import IntegrityError
from typing import Iterable, Optional
from datetime import date, datetime, timezone, timedelta
from app.models.sla import SLAPolicy, SLAPolicyTier, ConversationSLATracking, ConversationSLAEventLog, FormSLAConfig
from app.models.access import RespondContact
from app.schemas.sla import (
    SLAPolicyCreate, SLAPolicyUpdate, SLAPolicyTierCreate, SLAPolicyTierUpdate,
    ConversationSLATrackingCreate, ConversationSLATrackingUpdate, ConversationSLAEventLogCreate
)
from app.services.document_number import suffix_revision
from app.services.error_handler import handle_not_found, handle_conflict, handle_validation_error
from app.services import conversation_event_bus
from app.services.sla_scope import open_tracker_scope

_module_logger = logging.getLogger(__name__)

# Malaysia timezone (UTC+8) for all SLA timestamps
MALAYSIA_TZ = timezone(timedelta(hours=8))

# Optional: set USE_REMOTE_TIME=1 to get "now" from a time API (avoids server clock drift)
_REMOTE_TIME_URL = "https://worldtimeapi.org/api/timezone/Etc/UTC"


def _utc_now_from_remote() -> Optional[datetime]:
    """Fetch current UTC from a time API. Returns None on any failure."""
    try:
        import urllib.request
        import json
        with urllib.request.urlopen(_REMOTE_TIME_URL, timeout=3) as r:
            data = json.loads(r.read().decode())
            # e.g. "2026-02-06T04:06:36.123456+00:00"
            s = data.get("datetime") or data.get("utc_datetime")
            if s:
                return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        pass
    return None


def _tracking_company_id(tracking) -> str:
    """The company a tracker escalates within (AC-E2, AC-E3).

    Read off the tracker, never re-derived from the request: escalation can be
    triggered from a scheduler tick with no company at all, or from inside another
    company's request, and either would resolve the wrong ladder. The column is NOT
    NULL, so the fallback only covers a detached / partially built object.
    """
    from app.services.company_routing_service import DEFAULT_COMPANY_ID

    value = getattr(tracking, "company_id", None) if tracking is not None else None
    return str(value) if value else DEFAULT_COMPANY_ID


def _respond_contact_phone_lookup_candidates(raw: str) -> list[str]:
    """
    Build possible respond_contacts.phone_number values for integration lookups.
    Tries exact/stripped input, compact form, with/without leading +, MY local 0-prefix → +60.
    """
    s = (raw or "").strip()
    if not s:
        return []
    compact = re.sub(r"[\s\-\(\)]", "", s)
    out: list[str] = []
    for c in (s, compact):
        if c and c not in out:
            out.append(c)
    if not compact:
        return out
    if compact.startswith("+"):
        no_plus = compact[1:]
        if no_plus.isdigit() and no_plus not in out:
            out.append(no_plus)
    elif compact.isdigit():
        if compact.startswith("0") and len(compact) >= 9:
            my = "+60" + compact[1:]
            if my not in out:
                out.append(my)
        else:
            with_plus = f"+{compact}"
            if with_plus not in out:
                out.append(with_plus)
    return out


def now_malaysia() -> datetime:
    """Current time in Malaysia (UTC+8). Use for all SLA 'now' timestamps so DB shows Malaysia time.
    If USE_REMOTE_TIME=1, fetches UTC from a time API first (use when server clock is wrong).
    """
    import os
    if os.environ.get("USE_REMOTE_TIME", "").strip() == "1":
        utc = _utc_now_from_remote()
        if utc is not None:
            return utc.astimezone()
    return datetime.now()


def to_aware_utc8(dt: Optional[datetime]) -> Optional[datetime]:
    """Convert datetime to timezone-aware Malaysia (UTC+8) for DB storage.
    Naive datetimes are treated as Malaysia time. Use before writing to timestamptz.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=MALAYSIA_TZ)
    return dt.astimezone(MALAYSIA_TZ)


def to_naive_datetime(dt: datetime) -> datetime:
    """Convert timezone-aware datetime to naive datetime (Malaysia UTC+8).
    For naive datetimes, returns as-is (assumes they're already Malaysia time).
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt
    dt_utc8 = dt.astimezone(MALAYSIA_TZ)
    return dt_utc8.replace(tzinfo=None)


class SLAPolicyService:
    """Service for SLA policy operations."""
    
    def __init__(self, db: Session):
        self.db = db
    
    def list_policies(
        self,
        page: int = 1,
        limit: int = 50,
        query: Optional[str] = None,
        status: Optional[str] = None,
        sort_field: str = "created_at",
        sort_dir: str = "asc"
    ):
        """List THIS COMPANY's SLA policies.

        Filtered explicitly rather than by making SLAPolicy a scoped model: the
        picker on an agent's team sets must only offer policies that can actually be
        bound (the agent_teams composite FK rejects the rest), but escalation,
        extension and the daily summary all read policies from contexts with no
        active company, so a blanket auto-filter would break them.

        A scope that is not a single company (a system / all-companies caller) is
        left unfiltered, matching how those callers already read every other table.
        """
        q = self.db.query(SLAPolicy)

        from app.models.base import get_company_scope

        scope = get_company_scope(self.db)
        if isinstance(scope, frozenset) and len(scope) == 1:
            q = q.filter(SLAPolicy.company_id == next(iter(scope)))

        filters = []
        if status and status != "all":
            filters.append(SLAPolicy.is_active == (status == "active"))
        
        if query:
            filters.append(
                or_(
                    SLAPolicy.code.ilike(f"%{query}%"),
                    SLAPolicy.name.ilike(f"%{query}%"),
                    SLAPolicy.description.ilike(f"%{query}%")
                )
            )
        
        if filters:
            from sqlalchemy import and_
            q = q.filter(and_(*filters))
        
        sort_map = {
            "code": SLAPolicy.code,
            "name": SLAPolicy.name,
            "created_at": SLAPolicy.created_at,
            "updated_at": SLAPolicy.updated_at,
        }
        sort_column = sort_map.get(sort_field, SLAPolicy.created_at)
        if sort_dir == "desc":
            q = q.order_by(sort_column.desc())
        else:
            q = q.order_by(sort_column.asc())
        
        total = q.count()
        offset = (page - 1) * limit
        policies = q.offset(offset).limit(limit).all()
        
        # Add counts
        result = []
        for policy in policies:
            tiers_count = self.db.query(func.count(SLAPolicyTier.id)).filter(
                SLAPolicyTier.policy_id == policy.id
            ).scalar() or 0
            
            tracking_count = self.db.query(func.count(ConversationSLATracking.id)).filter(
                ConversationSLATracking.policy_id == policy.id
            ).scalar() or 0
            
            policy_dict = {
                **{c.name: getattr(policy, c.name) for c in policy.__table__.columns},
                "tiers_count": tiers_count,
                "tracking_count": tracking_count
            }
            result.append(policy_dict)
        
        return {
            "data": result,
            "pagination": {"total": total, "page": page, "limit": limit},
            "empty": total == 0
        }
    
    def get_policy(self, policy_id: str):
        """Get an SLA policy by ID."""
        policy = self.db.query(SLAPolicy).filter(SLAPolicy.id == policy_id).first()
        if not policy:
            raise handle_not_found("SLA Policy", policy_id)
        return policy
    
    def _write_company_id(self) -> str:
        """The company a policy written now belongs to.

        `SLAPolicy` is deliberately NOT a `CompanyScopedMixin` (the auto-filter would
        reach every policy load in the app, including readers that hold a policy id
        and no company context), so nothing stamps `company_id` on insert for us and
        this has to be explicit. An X-API-Key caller has scope None (all companies)
        and no company to infer, so it keeps writing to the incumbent, which is where
        migration 320 put every pre-multi-company policy.
        """
        from app.models.base import get_company_scope
        from app.services.company_routing_service import DEFAULT_COMPANY_ID

        scope = get_company_scope(self.db)
        if isinstance(scope, frozenset) and len(scope) == 1:
            return next(iter(scope))
        if scope is None:
            return DEFAULT_COMPANY_ID
        raise handle_validation_error(
            "Cannot tell which company this SLA policy belongs to. "
            "Switch to a company and try again."
        )

    def create_policy(self, policy_data: SLAPolicyCreate):
        """Create a new SLA policy with tiers."""
        company_id = self._write_company_id()
        # The code is unique PER COMPANY, so this check must be too: a global one
        # blocked Mocha from having its own policy with the same code as Sorento's,
        # while still claiming the code "already exists in this company".
        existing = (
            self.db.query(SLAPolicy)
            .filter(SLAPolicy.code == policy_data.code, SLAPolicy.company_id == company_id)
            .first()
        )
        if existing:
            raise handle_conflict("SLA policy code already exists in this company.")

        policy_dict = policy_data.model_dump(exclude={"tiers"})
        policy = SLAPolicy(**policy_dict, company_id=company_id)
        self.db.add(policy)
        self.db.flush()
        
        # Create tiers if provided
        if policy_data.tiers:
            for tier_data in policy_data.tiers:
                # exclude policy_id: it's optional in the payload and we force it to
                # the new policy's id (avoids a duplicate-kwarg TypeError).
                tier = SLAPolicyTier(**tier_data.model_dump(exclude={"policy_id"}), policy_id=policy.id)
                self.db.add(tier)
        
        self.db.commit()
        self.db.refresh(policy)
        return policy
    
    def update_policy(self, policy_id: str, policy_data: SLAPolicyUpdate):
        """Update an SLA policy."""
        policy = self.get_policy(policy_id)
        
        update_data = policy_data.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(policy, key, value)
        
        self.db.commit()
        self.db.refresh(policy)
        return policy

    def delete_policy(self, policy_id: str):
        """Hard-delete an SLA policy. Tiers cascade at DB level; refuse when
        the policy is still referenced by conversation tracking, form-SLA configs,
        or team-set bindings (those FKs are RESTRICT / NO ACTION and would raise at commit)."""
        from app.models.access import AgentTeam

        policy = self.get_policy(policy_id)

        tracking_count = self.db.query(func.count(ConversationSLATracking.id)).filter(
            ConversationSLATracking.policy_id == policy_id
        ).scalar() or 0
        config_count = self.db.query(func.count(FormSLAConfig.id)).filter(
            FormSLAConfig.policy_id == policy_id
        ).scalar() or 0
        # Distinct team sets (agent, code) bound to this policy - every tier row of a
        # set shares the policy, so count distinct (agent_id, code) for a clean message.
        binding_count = self.db.query(
            func.count(func.distinct(func.concat(AgentTeam.agent_id, ':', AgentTeam.code)))
        ).filter(AgentTeam.policy_id == policy_id).scalar() or 0
        if tracking_count or config_count or binding_count:
            raise handle_conflict(
                "Cannot delete SLA policy: it is still referenced by "
                f"{tracking_count} conversation tracking record(s), "
                f"{config_count} form SLA config(s), and "
                f"{binding_count} team-set binding(s)."
            )

        self.db.delete(policy)
        self.db.commit()
        return {"message": "SLA policy deleted successfully"}


class SLAPolicyTierService:
    """Service for SLA policy tier operations."""
    
    def __init__(self, db: Session):
        self.db = db
    
    def list_tiers(self, policy_id: str):
        """List tiers for a policy."""
        tiers = self.db.query(SLAPolicyTier).filter(
            SLAPolicyTier.policy_id == policy_id
        ).order_by(SLAPolicyTier.tier_level).all()
        return tiers
    
    def get_tier(self, tier_id: str):
        """Get a tier by ID."""
        tier = self.db.query(SLAPolicyTier).filter(SLAPolicyTier.id == tier_id).first()
        if not tier:
            raise handle_not_found("SLA Policy Tier", tier_id)
        return tier
    
    def create_tier(self, tier_data: SLAPolicyTierCreate):
        """Create a new tier."""
        # Check unique constraint
        existing = self.db.query(SLAPolicyTier).filter(
            SLAPolicyTier.policy_id == tier_data.policy_id,
            SLAPolicyTier.tier_level == tier_data.tier_level
        ).first()
        if existing:
            raise handle_conflict("Tier level already exists for this policy.")
        
        tier = SLAPolicyTier(**tier_data.model_dump())
        self.db.add(tier)
        self.db.commit()
        self.db.refresh(tier)
        return tier
    
    def update_tier(self, tier_id: str, tier_data: SLAPolicyTierUpdate):
        """Update a tier."""
        tier = self.get_tier(tier_id)
        
        update_data = tier_data.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(tier, key, value)
        
        self.db.commit()
        self.db.refresh(tier)
        return tier
    
    def delete_tier(self, tier_id: str):
        """Delete a tier."""
        tier = self.get_tier(tier_id)
        self.db.delete(tier)
        self.db.commit()
        return {"message": "SLA policy tier deleted successfully"}


# Human labels for form-SLA workflow events, so the pending-task list mirrors the
# SLA config's state machine ("Send for approval", "Approve") instead of a generic
# "respond / resolve". Unknown events fall back to a title-cased code.
_FORM_SLA_ACTION_LABELS: dict = {
    "submit": "Submit",
    "send_for_approval": "Send for approval",
    "approved": "Approve",
    "approval_rejected": "Approve or reject",
    "rejected": "Approve or reject",
    "reject_submitted": "Review submission",
    "resolved": "Mark CS resolved",
    "technical_team_response": "Respond to complaint",
    "project_sales_approve": "Review and approve",
    "project_sales_reject": "Review and approve",
    "purchasing_respond": "Respond to salesperson",
    "purchasing_decide": "Make purchasing decision",
}


def _humanize_sla_event(event: str) -> str:
    """Map a form-SLA event code to a human action label."""
    key = (event or "").strip()
    return _FORM_SLA_ACTION_LABELS.get(key) or key.replace("_", " ").capitalize()


def _now_utc() -> datetime:
    """Current time as timezone-aware UTC for DB storage and duration math."""
    return datetime.now(timezone.utc)


def _to_aware_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Convert to timezone-aware UTC for DB storage and duration calculations. Naive treated as UTC."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def format_myt(dt: Optional[datetime]) -> Optional[str]:
    """An SLA timestamp as Malaysia wall clock WITH the day and the zone.

    "Mon 17 Aug 09:00 MYT". Both halves are load-bearing (AC-G2): staff read
    these notifications at 22:00, when the deadline is another day, so a bare
    "10:00" is worse than useless. Every SLA column is naive UTC, which
    ``_to_aware_utc`` is the single place that knows.

    Deliberately NOT ``form_sla_service._fmt_due`` ("18 Aug 2026, 10:00 AM"),
    which carries no weekday and no zone.
    """
    aware = _to_aware_utc(dt)
    if aware is None:
        return None
    local = aware.astimezone(MALAYSIA_TZ)
    # `local.day` rather than %-d: the no-pad strftime flag is platform-specific.
    return f"{local:%a} {local.day} {local:%b} {local:%H:%M} MYT"


# A clock recomputed from `now` a few microseconds after `initiated_at` is the
# SAME instant for a reader; only a real deferral to the next working window is
# "out of hours".
_CLOCK_DEFERRED_THRESHOLD = timedelta(seconds=1)


def sla_clock_line(tracking) -> Optional[str]:
    """The one-line clock statement every assignment notification carries (AC-G2).

    Out of hours (the clock start was pushed to the next working-window open):

        "Clock starts Mon 17 Aug 09:00 MYT · respond by Mon 17 Aug 10:00 MYT"

    In hours, unconditionally - a missing line reads as "there is no clock",
    which is the misreading this AC exists to remove:

        "Respond by Fri 14 Aug 15:00 MYT"

    Once the first response has landed the response clock has stopped, so on a
    reassign / takeover the clock that is actually running is the resolution
    one and the line says so ("Resolve by ..."). Returns None only when there is
    no deadline at all to state.

    One builder, used by the in-app / email / WhatsApp body alike: the same
    string is passed to ``build_sla_whatsapp_data``, so the three channels
    cannot disagree about the deadline.
    """
    responded = bool(getattr(tracking, "is_responded", False))
    if responded:
        due = format_myt(getattr(tracking, "due_at_resolution", None))
        return f"Resolve by {due}" if due else None

    due = format_myt(getattr(tracking, "due_at", None))
    if not due:
        return None
    start = _to_aware_utc(getattr(tracking, "current_tier_started_at", None))
    initiated = _to_aware_utc(getattr(tracking, "initiated_at", None))
    if start and initiated and start - initiated > _CLOCK_DEFERRED_THRESHOLD:
        return f"Clock starts {format_myt(start)} · respond by {due}"
    return f"Respond by {due}"


def append_clock_line(body: str, tracking) -> str:
    """``body`` with the AC-G2 clock line appended, when there is one to state."""
    line = sla_clock_line(tracking)
    return f"{body}\n\n{line}" if line else body


def _coerce_flag(value) -> bool:
    """Coerce a JSON-ish truthy flag (True / 1 / "true" / "1") to bool.

    Integration callers post `is_responded` / `is_resolved` as a real bool, an
    int, or a string, so a bare `bool(value)` would read the string "false" as
    True. Shared so the idempotency short-circuits and the field-application
    branches below agree on what "the caller asked for this" means.
    """
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1")
    return value is True or value == 1


def _working_clock_start(db, start_dt: Optional[datetime]) -> Optional[datetime]:
    """Normalize an SLA clock start to the next working-window open.

    A clock that would start when nobody is working (weekend, public holiday,
    before open, after close) starts at the next window open instead, so the
    responder gets the whole window the policy promises. Returns aware UTC (naive
    ``start_dt`` treated as UTC), matching the rest of this module. Falls back to
    ``start_dt`` if the work calendar is unavailable/misconfigured."""
    if start_dt is None:
        return None
    try:
        from app.services.calendar_service import CalendarService

        out = CalendarService(db).next_working_window_open(start_dt)
        if out is not None:
            return _to_aware_utc(out)
    except Exception:  # pragma: no cover - defensive; never break SLA create/escalate
        import logging
        logging.getLogger(__name__).warning(
            "working clock-start normalization failed; using the raw start.", exc_info=True
        )
    return _to_aware_utc(start_dt)


def _working_due(db, start_dt: Optional[datetime], hours: float) -> Optional[datetime]:
    """Forward SLA due date in *working days*: convert the policy hours to days
    (÷24) and add that many working days (skipping weekends + KL public holidays),
    keeping the time-of-day. E.g. 72h → +3 working days. Returns aware UTC (naive
    ``start_dt`` treated as UTC) so storage stays consistent with the rest of this
    module. Falls back to calendar arithmetic if the work calendar is unavailable."""
    if start_dt is None:
        return None
    try:
        from app.services.calendar_service import CalendarService
        return _to_aware_utc(CalendarService(db).add_working_days_from_hours(start_dt, hours))
    except Exception:  # pragma: no cover - defensive; never break SLA create/escalate
        import logging
        logging.getLogger(__name__).warning(
            "working-days due calc failed; falling back to calendar hours.", exc_info=True
        )
        return _to_aware_utc(start_dt) + timedelta(hours=float(hours))


def compute_tracking_timings(tracking, tier) -> dict:
    """
    Compute time-in-tier and time-remaining for response and resolution.
    Response timers stop when is_responded=True; resolution timers stop when is_resolved=True.
    Returns dict with time_in_tier_response_seconds, time_remaining_response_seconds,
    time_in_tier_resolution_seconds, time_remaining_resolution_seconds, resolution_due_at.
    """
    if tier is None:
        return {
            "time_in_tier_response_seconds": None,
            "time_remaining_response_seconds": None,
            "time_in_tier_resolution_seconds": None,
            "time_remaining_resolution_seconds": None,
            "resolution_due_at": None,
        }
    now = _now_utc()
    initiated_at = _to_aware_utc(tracking.initiated_at)
    current_tier_started_at = _to_aware_utc(tracking.current_tier_started_at)
    due_at = _to_aware_utc(tracking.due_at)
    responded_at = _to_aware_utc(tracking.responded_at)
    resolved_at = _to_aware_utc(tracking.resolved_at)
    due_at_resolution = _to_aware_utc(getattr(tracking, "due_at_resolution", None))
    # float() - resolution_hours is a Decimal column; timedelta rejects Decimal.
    resolution_hours = float(getattr(tier, "resolution_hours", None) or 24)
    resolution_due_at = due_at_resolution if due_at_resolution is not None else (
        (current_tier_started_at + timedelta(hours=resolution_hours)) if current_tier_started_at else None
    )

    # Time in tier (response): if responded = responded_at - initiated_at; else timer keeps counting
    if tracking.is_responded and responded_at and initiated_at:
        time_in_tier_response_seconds = (responded_at - initiated_at).total_seconds()
    elif current_tier_started_at:
        time_in_tier_response_seconds = (now - current_tier_started_at).total_seconds()
    else:
        time_in_tier_response_seconds = None

    # Time remaining for response (0 when is_responded)
    if tracking.is_responded:
        time_remaining_response_seconds = 0.0
    elif due_at:
        time_remaining_response_seconds = max(0.0, (due_at - now).total_seconds())
    else:
        time_remaining_response_seconds = None

    # Time in tier (resolution): if resolved = resolved_at - initiated_at; else timer keeps counting
    if tracking.is_resolved and resolved_at and initiated_at:
        time_in_tier_resolution_seconds = (resolved_at - initiated_at).total_seconds()
    elif initiated_at:
        time_in_tier_resolution_seconds = (now - initiated_at).total_seconds()
    else:
        time_in_tier_resolution_seconds = None

    # Time remaining for resolution (0 when is_resolved)
    if tracking.is_resolved:
        time_remaining_resolution_seconds = 0.0
    elif resolution_due_at:
        time_remaining_resolution_seconds = max(0.0, (resolution_due_at - now).total_seconds())
    else:
        time_remaining_resolution_seconds = None

    return {
        "time_in_tier_response_seconds": time_in_tier_response_seconds,
        "time_remaining_response_seconds": time_remaining_response_seconds,
        "time_in_tier_resolution_seconds": time_in_tier_resolution_seconds,
        "time_remaining_resolution_seconds": time_remaining_resolution_seconds,
        "resolution_due_at": resolution_due_at,
    }


def conversation_tracking_scope():
    """SQLAlchemy filter selecting conversation-SLA rows only.

    Conversation SLA (created by n8n; the invariant is now ONE open ticket
    per (contact, triggering message) - a contact may hold several open
    intervention tickets at once, per-enquiry, not one merged conversation)
    and form SLA stage rows (created by form_sla_service, per-entity,
    multi-active) share this table. Form rows are identified by
    source_entity_type in FORM_SLA_TYPES; everything contact-keyed on the
    conversation side must apply this filter or it can falsely match a form
    row (e.g. the source_message_id idempotency check, thread-assignee
    lookups).
    Conversation rows are never voided, so a query already carrying this scope
    reads `is_resolved = false` as "open" correctly and does NOT also need
    `sla_scope.not_voided()` / `open_tracker_scope()`. The only writer of
    `ConversationSLATracking.voided_at` is `form_sla_service` (one assignment,
    inside a loop over `_open_form_trackers`, which pins to FORM_SLA_TYPES and
    passes through the NEGATED conversation scope - UAC F4b, "conversation SLA
    is never touched"). The open-ticket unique index carries the same scope in
    its predicate. Reviewers keep flagging the conversation lane for a missing
    not_voided(); it is a no-op there by construction, and this note exists so
    the check is a read rather than a re-derivation. It stops being true the
    day something voids a conversation row - then every caller of this helper
    needs open_tracker_scope() beside it.
    """
    from app.services.form_sla_service import FORM_SLA_TYPES

    return or_(
        ConversationSLATracking.source_entity_type.is_(None),
        ConversationSLATracking.source_entity_type.notin_(FORM_SLA_TYPES),
    )


def event_log_assignee_fields(db: Session, user_ref: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """Resolve (display label, user id) for SLA event log assigned_to / assigned_to_id."""
    if user_ref is None or (isinstance(user_ref, str) and not str(user_ref).strip()):
        return None, None
    v = str(user_ref).strip()
    from app.models.user import User

    user = (
        db.query(User)
        .filter(
            (User.id == v) | (User.respond_user_id == v) | (User.email == v),
        )
        .first()
    )
    if not user:
        return v, None
    name = getattr(user, "name", None)
    email = getattr(user, "email", None)
    respond_user_id = getattr(user, "respond_user_id", None)
    label = (
        str(name).strip()
        if name is not None and str(name).strip()
        else str(email).strip()
        if email is not None and str(email).strip()
        else str(respond_user_id).strip()
        if respond_user_id is not None and str(respond_user_id).strip()
        else None
    )
    return label, str(getattr(user, "id"))


class ConversationSLATrackingService:
    """Service for conversation SLA tracking operations."""
    
    def __init__(self, db: Session):
        self.db = db

    def _resolve_tier_with_clamp(
        self, policy_id: str, tier_level: int
    ) -> Optional[SLAPolicyTier]:
        """Resolve the SLAPolicyTier for ``(policy_id, tier_level)`` with clamping (D7).

      - exact match wins
      - else the highest defined tier with ``tier_level <= requested`` (clamp up to ceiling)
      - else the lowest defined tier (requested below all defined tiers)
      - None only when the policy has zero tiers

        Logs a warning whenever the returned tier differs from the requested level so the
        operator sees that escalation overran the policy's defined tiers.
        """
        import logging

        logger = logging.getLogger(__name__)

        exact = (
            self.db.query(SLAPolicyTier)
            .filter(
                SLAPolicyTier.policy_id == policy_id,
                SLAPolicyTier.tier_level == tier_level,
            )
            .first()
        )
        if exact:
            return exact

        clamped = (
            self.db.query(SLAPolicyTier)
            .filter(
                SLAPolicyTier.policy_id == policy_id,
                SLAPolicyTier.tier_level <= tier_level,
            )
            .order_by(SLAPolicyTier.tier_level.desc())
            .first()
        )
        if not clamped:
            clamped = (
                self.db.query(SLAPolicyTier)
                .filter(SLAPolicyTier.policy_id == policy_id)
                .order_by(SLAPolicyTier.tier_level.asc())
                .first()
            )
        if clamped is not None:
            logger.warning(
                "conversation SLA: tier %s not defined for policy %s; clamping to tier %s hours",
                tier_level,
                policy_id,
                getattr(clamped, "tier_level", None),
            )
        return clamped

    def _resolve_tracking_assignee_user_id(self, tracking: ConversationSLATracking) -> Optional[str]:
        """Current assignee as users.id before clearing assignment (FK first, then legacy assigned_to text)."""
        assigned_to_id = getattr(tracking, "assigned_to_id", None)
        if assigned_to_id is not None and str(assigned_to_id).strip():
            return str(assigned_to_id)
        assigned_to = getattr(tracking, "assigned_to", None)
        if assigned_to is None or not str(assigned_to).strip():
            return None
        from app.models.user import User

        raw = str(assigned_to).strip()
        user = (
            self.db.query(User)
            .filter(
                (User.respond_user_id == raw) | (User.id == raw) | (User.email == raw),
            )
            .first()
        )
        return str(getattr(user, "id")) if user else None
    
    def _build_conversation_list_query(
        self,
        base_query,
        policy_id: Optional[str] = None,
        query: Optional[str] = None,
        sort_field: str = "created_at",
        sort_dir: str = "desc",
        assigned_to: Optional[str] = None,
        tracking_ids: Optional[list[str]] = None,
        contact: Optional[str] = None,
        is_resolved: Optional[bool] = None,
        resolved_by: Optional[str] = None,
    ):
        """Apply the conversation-scope filters + sort shared by ``list_tracking``
        (scope="conversation") and ``neighbours`` so the two can never drift.

        ``base_query`` is a ``ConversationSLATracking`` query (the list path adds
        joinedload options for serialization; the neighbours path selects ids only).
        The conversation scope filter (``conversation_tracking_scope``) is applied here
        so neighbours can never bleed into form SLA rows. The ORDER BY always appends
        ``ConversationSLATracking.id`` as a deterministic tie-breaker so offset position
        and prev/next neighbours are unambiguous when the sort column has equal values.

        ``contact`` / ``is_resolved`` / ``resolved_by`` back the AC-M2 history links
        (the drawer's "View history" for one contact, the widget's "Recently
        resolved" for one resolver). ``contact`` accepts whatever identifies a
        contact elsewhere in this service - CRM id, Respond.io id or phone - and an
        UNRESOLVABLE one filters the set to empty rather than being ignored: a link
        that silently shows every contact's tickets is worse than one that shows none.
        """
        from sqlalchemy import asc, desc, false
        from app.models.user import User

        q = base_query.filter(conversation_tracking_scope())

        if policy_id:
            q = q.filter(ConversationSLATracking.policy_id == policy_id)

        if contact and str(contact).strip():
            internal_contact_id = self.resolve_internal_respond_contact_id(str(contact).strip())
            if not internal_contact_id:
                return q.filter(false())
            q = q.filter(
                ConversationSLATracking.respond_contact_id == internal_contact_id
            )

        if is_resolved is not None:
            q = q.filter(ConversationSLATracking.is_resolved.is_(bool(is_resolved)))

        if resolved_by and str(resolved_by).strip():
            resolver_val = str(resolved_by).strip()
            resolver = (
                self.db.query(User)
                .filter(
                    or_(
                        User.id == resolver_val,
                        User.respond_user_id == resolver_val,
                        User.email == resolver_val,
                    )
                )
                .first()
            )
            if resolver is None:
                return q.filter(false())
            # resolved_by stores users.id (update_tracking normalizes it), so a
            # single equality is the honest predicate - no OR over stale shapes.
            q = q.filter(ConversationSLATracking.resolved_by == str(resolver.id))

        if tracking_ids is not None:
            q = q.filter(ConversationSLATracking.id.in_(tracking_ids))

        if query and query.strip():
            term = f"%{query.strip()}%"
            q = q.join(
                RespondContact, ConversationSLATracking.respond_contact_id == RespondContact.id
            ).filter(
                or_(
                    RespondContact.phone_number.ilike(term),
                    RespondContact.name.ilike(term),
                )
            )

        if assigned_to and assigned_to.strip():
            assignee_val = assigned_to.strip()
            # Only show trackings that have an assignee and that assignee matches (exclude unassigned)
            q = q.filter(ConversationSLATracking.assigned_to_id.isnot(None)).join(
                User, ConversationSLATracking.assigned_to_id == User.id
            ).filter(
                or_(
                    User.respond_user_id == assignee_val,
                    User.id == assignee_val,
                )
            )

        order_col = getattr(ConversationSLATracking, sort_field, None)
        if order_col is not None and hasattr(order_col, "desc"):
            primary = desc(order_col) if sort_dir == "desc" else asc(order_col)
        else:
            primary = ConversationSLATracking.created_at.desc()
        return q.order_by(primary, ConversationSLATracking.id.asc())

    def list_tracking(
        self,
        page: int = 1,
        limit: int = 50,
        policy_id: Optional[str] = None,
        query: Optional[str] = None,
        sort_field: str = "created_at",
        sort_dir: str = "desc",
        assigned_to: Optional[str] = None,
        tracking_ids: Optional[list[str]] = None,
        scope: str = "conversation",
        contact: Optional[str] = None,
        is_resolved: Optional[bool] = None,
        resolved_by: Optional[str] = None,
    ):
        """List SLA tracking records. query filters by contact phone or contact name.

        scope="conversation" (default) lists contact-keyed conversation SLA rows;
        scope="form" lists per-entity form SLA stage rows (source_entity_type in
        FORM_SLA_TYPES). Both live in the same table - see conversation_tracking_scope.
        """
        from app.services.form_sla_service import FORM_SLA_TYPES
        from sqlalchemy.orm import joinedload
        from sqlalchemy import asc, desc
        from app.models.sla import ConversationSLAEventLog
        from app.models.user import User

        base_q = self.db.query(ConversationSLATracking).options(
            joinedload(ConversationSLATracking.policy),
            joinedload(ConversationSLATracking.contact),
            joinedload(ConversationSLATracking.assigned_user),
            joinedload(ConversationSLATracking.agent),
            joinedload(ConversationSLATracking.event_logs).joinedload(ConversationSLAEventLog.assigned_user)
        )

        # Conversation SLA list excludes form trackers (stock_inquiry / purchase_request /
        # sponsorship_form / complaint). Those have their own per-form SLA Tracking tab and
        # detail flow; surfacing them in this list inflates contact-keyed rows and breaks
        # back-navigation to the originating form. The form scope is the inverse.
        if scope == "form":
            q = base_q.filter(ConversationSLATracking.source_entity_type.in_(FORM_SLA_TYPES))

            if policy_id:
                q = q.filter(ConversationSLATracking.policy_id == policy_id)

            if tracking_ids is not None:
                q = q.filter(ConversationSLATracking.id.in_(tracking_ids))

            if assigned_to and assigned_to.strip():
                assignee_val = assigned_to.strip()
                # Only show trackings that have an assignee and that assignee matches (exclude unassigned)
                q = q.filter(ConversationSLATracking.assigned_to_id.isnot(None)).join(
                    User, ConversationSLATracking.assigned_to_id == User.id
                ).filter(
                    or_(
                        User.respond_user_id == assignee_val,
                        User.id == assignee_val,
                    )
                )

            order_col = getattr(ConversationSLATracking, sort_field, None)
            if order_col is not None and hasattr(order_col, "desc"):
                q = q.order_by(desc(order_col) if sort_dir == "desc" else asc(order_col))
            else:
                q = q.order_by(ConversationSLATracking.created_at.desc())
        else:
            # Conversation scope: reuse the shared builder so list + neighbours can't
            # drift (filters, search, sort, deterministic id tie-breaker).
            q = self._build_conversation_list_query(
                base_q,
                policy_id=policy_id,
                query=query,
                sort_field=sort_field,
                sort_dir=sort_dir,
                assigned_to=assigned_to,
                tracking_ids=tracking_ids,
                contact=contact,
                is_resolved=is_resolved,
                resolved_by=resolved_by,
            )

        ref_map: dict = {}
        action_map: dict = {}
        if scope == "form":
            # Form rows carry an entity reference + stage next-action resolved here so
            # the list mirrors the conversation list (no UUIDs). Volume is modest, so
            # search/paginate in Python after resolution.
            all_rows = q.all()
            ref_map = self._resolve_my_pending_references(all_rows)
            action_map = self._form_next_actions(all_rows)
            if query and query.strip():
                ql = query.strip().lower()
                def _match(r):
                    ref = (ref_map.get(str(r.id)) or "")
                    act = (action_map.get(str(r.id)) or "")
                    pol = (r.policy.name if r.policy else "") or ""
                    et = (getattr(r, "source_entity_type", None) or "")
                    return any(ql in str(v).lower() for v in (ref, act, pol, et))
                all_rows = [r for r in all_rows if _match(r)]
            total = len(all_rows)
            offset = (page - 1) * limit
            tracking = all_rows[offset:offset + limit]
        else:
            total = q.count()
            offset = (page - 1) * limit
            tracking = q.offset(offset).limit(limit).all()

        # Convert to dict for proper validation with relationships
        result_data = []
        for track in tracking:
            # Get contact info from relationship
            contact_phone = track.contact.phone_number if track.contact else None
            contact_name = track.contact.name if track.contact else None
            
            # Get user info from relationship
            assigned_user_name = track.assigned_user.name if track.assigned_user else None
            assigned_user_email = track.assigned_user.email if track.assigned_user else None
            
            # Look up user names for responded_by and resolved_by
            responded_by_user_name = None
            resolved_by_user_name = None
            if track.responded_by is not None and str(track.responded_by).strip():
                from app.models.user import User
                responded_by_user = self.db.query(User).filter(
                    (User.id == track.responded_by) |
                    (User.respond_user_id == track.responded_by) |
                    (User.email == track.responded_by)
                ).first()
                responded_by_user_name = responded_by_user.name if responded_by_user else track.responded_by
            
            if track.resolved_by is not None and str(track.resolved_by).strip():
                from app.models.user import User
                resolved_by_user = self.db.query(User).filter(
                    (User.id == track.resolved_by) |
                    (User.respond_user_id == track.resolved_by) |
                    (User.email == track.resolved_by)
                ).first()
                resolved_by_user_name = resolved_by_user.name if resolved_by_user else track.resolved_by
            
            track_dict = {
                "id": str(track.id),
                "policy_id": str(track.policy_id),
                "current_tier": track.current_tier,
                "assigned_to": track.assigned_to,  # Keep for backward compatibility
                "assigned_to_id": track.assigned_to_id,
                "initiated_at": track.initiated_at,
                "current_tier_started_at": track.current_tier_started_at,
                "due_at": track.due_at,
                "due_at_resolution": getattr(track, "due_at_resolution", None),
                "escalated_at": track.escalated_at,
                "escalation_reason": track.escalation_reason,
                "is_responded": track.is_responded,
                "responded_at": track.responded_at,
                "responded_by": track.responded_by,
                "response_time": track.response_time,
                "is_resolved": track.is_resolved,
                "resolved_at": track.resolved_at,
                "resolved_by": track.resolved_by,
                "respond_contact_id": track.respond_contact_id,
                "created_at": track.created_at,
                "updated_at": track.updated_at,
                "synced_to_excel": track.synced_to_excel,
                "last_synced_to_excel": track.last_synced_to_excel,
                "resolution_duration": track.resolution_duration,
                "agent_id": getattr(track, "agent_id", None),
                "agent_code": track.agent.code if getattr(track, "agent", None) else None,
                "agent": {
                    "id": str(track.agent.id),
                    "code": track.agent.code,
                    "name": track.agent.name,
                } if getattr(track, "agent", None) else None,
                "team_set_code": getattr(track, "team_set_code", None),
                "message_id": getattr(track, "message_id", None),
                "policy": {
                    "id": str(track.policy.id),
                    "code": track.policy.code,
                    "name": track.policy.name
                } if track.policy else None,
                "policy_code": track.policy.code if track.policy else None,
                "policy_name": track.policy.name if track.policy else None,
                "contact": {
                    "id": track.contact.id,
                    "phone_number": track.contact.phone_number,
                    "name": track.contact.name
                } if track.contact else None,
                "assigned_user": {
                    "id": track.assigned_user.id,
                    "email": track.assigned_user.email,
                    "name": track.assigned_user.name
                } if track.assigned_user else None,
                "contact_phone": contact_phone,
                "contact_name": contact_name,
                "assigned_user_name": assigned_user_name,
                "assigned_user_email": assigned_user_email,
                "responded_by_user_name": responded_by_user_name,
                "resolved_by_user_name": resolved_by_user_name,
                "source_entity_type": getattr(track, "source_entity_type", None),
                "source_entity_id": str(track.source_entity_id) if getattr(track, "source_entity_id", None) else None,
                "reference": ref_map.get(str(track.id)),
                "next_action": action_map.get(str(track.id)),
                "event_logs": []  # Initialize as empty
            }
            # Compute time-in-tier and time-remaining (response stops when is_responded, resolution when is_resolved)
            tier = self.db.query(SLAPolicyTier).filter(
                SLAPolicyTier.policy_id == track.policy_id,
                SLAPolicyTier.tier_level == track.current_tier,
            ).first()
            track_dict.update(compute_tracking_timings(track, tier))
            track_dict["tier_response_hours"] = tier.response_hours if tier else None
            track_dict["tier_resolution_hours"] = getattr(tier, "resolution_hours", None) if tier else None

            # Try to load event_logs if relationship exists
            try:
                if hasattr(track, 'event_logs'):
                    event_logs_list = list(track.event_logs) if track.event_logs else []
                    track_dict["event_logs"] = [
                        {
                            "id": str(log.id),
                            "sla_tracking_id": str(log.sla_tracking_id),
                            "event_type": log.event_type,
                            "from_tier": log.from_tier,
                            "to_tier": log.to_tier,
                            "event_at": log.event_at,
                            "reason": log.reason,
                            "assigned_to": log.assigned_to,  # Keep for backward compatibility
                            "assigned_to_id": log.assigned_to_id,
                            "due_at": log.due_at,
                            "response_time": log.response_time,
                            "resolution_time": log.resolution_time,
                            "reminder_count": log.reminder_count,
                            "last_reminder_at": log.last_reminder_at,
                            "created_at": log.created_at,
                            "assigned_user": {
                                "id": log.assigned_user.id,
                                "email": log.assigned_user.email,
                                "name": log.assigned_user.name
                            } if log.assigned_user else None,
                            "assigned_user_name": log.assigned_user.name if log.assigned_user else None,
                            "assigned_user_email": log.assigned_user.email if log.assigned_user else None,
                        }
                        for log in event_logs_list
                    ]
            except Exception:
                track_dict["event_logs"] = []
            
            result_data.append(track_dict)
        
        return {
            "data": result_data,
            "pagination": {"total": total, "page": page, "limit": limit},
            "empty": total == 0
        }
    
    def get_tracking(self, tracking_id: str, *, load_event_logs: bool = True):
        """Get a tracking record by ID. Set load_event_logs=False for lighter reads (e.g. external summary)."""
        from sqlalchemy.orm import joinedload
        from app.models.sla import ConversationSLAEventLog
        from app.models.user import User

        opts = [
            joinedload(ConversationSLATracking.policy),
            joinedload(ConversationSLATracking.contact),
            joinedload(ConversationSLATracking.assigned_user).joinedload(User.superior),
            joinedload(ConversationSLATracking.agent),
        ]
        if load_event_logs:
            opts.append(
                joinedload(ConversationSLATracking.event_logs).joinedload(
                    ConversationSLAEventLog.assigned_user
                )
            )
        tracking = (
            self.db.query(ConversationSLATracking)
            .options(*opts)
            .filter(ConversationSLATracking.id == tracking_id)
            .first()
        )
        if not tracking:
            raise handle_not_found("SLA Tracking", tracking_id)

        if load_event_logs and tracking.event_logs:
            tracking.event_logs.sort(key=lambda x: x.event_at, reverse=True)

        return tracking

    def get_tracking_by_source_entity(self, source_entity_type: str, source_entity_id: str) -> Optional[ConversationSLATracking]:
        """Get a tracking record by source entity (e.g. stock_inquiry, complaint)."""
        return (
            self.db.query(ConversationSLATracking)
            .filter(
                ConversationSLATracking.source_entity_type == source_entity_type,
                ConversationSLATracking.source_entity_id == source_entity_id,
            )
            .first()
        )

    def _pending_row(
        self,
        r: ConversationSLATracking,
        *,
        reference: Optional[str],
        next_action: Optional[str],
        respond_io_id: Optional[str],
        contact_name: Optional[str],
        contact_phone: Optional[str],
    ) -> dict:
        """One row's contract SHARED by /my-pending and /team-pending, so a
        field that reaches one worklist reaches both - My Team fell behind My
        Pending (no `is_intervention_ticket`, no enquiry snippet) exactly
        because these used to be two independent builders that drifted.
        Team-only fields (assignee_id/name, team_id/label) and any caller-
        specific extra are layered on by each list_* method after this call.
        """
        from app.services.form_sla_service import FORM_SLA_TYPES

        is_form_sla = r.source_entity_type in FORM_SLA_TYPES
        # UAC B: a conversation row is an intervention TICKET only once it
        # carries the enquiry identity this feature introduced
        # (source_message_id, migration 321/S2a) - a pre-migration row with no
        # trigger message keeps its old worklist behaviour (Respond inbox deep
        # link, inline Escalate/Resolve) rather than opening a drawer with no
        # enquiry to show.
        is_ticket = (not is_form_sla) and bool(getattr(r, "source_message_id", None))
        row: dict = {
            "id": str(r.id),
            "source_entity_type": r.source_entity_type,
            "source_entity_id": r.source_entity_id,
            # Authoritative form-vs-conversation flag (single source of truth so
            # the widget never re-derives it and drifts - e.g. 'ticket' is a form
            # type the FE route map doesn't know). Conversation rows = false.
            "is_form_sla": is_form_sla,
            "reference": reference,
            "respond_io_id": respond_io_id,
            "due_at": r.due_at.isoformat() if r.due_at else None,
            # Resolution deadline - the Extend action targets this. Emitted so the
            # widget can gate the Extend button client-side (hidden when null) and
            # the dialog can show "Current due" without a preview round-trip.
            "due_at_resolution": (
                r.due_at_resolution.isoformat() if r.due_at_resolution else None
            ),
            # The deadline the assignee is actually racing: BEFORE responding it's
            # the response due; AFTER responding it's the resolution due (the one
            # Extend moves). Keyed purely on is_responded.
            "active_due_at": self._active_due_iso(r, bool(r.is_responded)),
            # Which clock the active deadline is, so the FE labels the badge:
            # "Respond by" until responded, "Resolve by" after.
            "due_kind": "resolve" if bool(r.is_responded) else "respond",
            "is_responded": bool(r.is_responded),
            "current_tier": r.current_tier,
            # How many times the resolution deadline has been moved. The
            # widget marks an extended row so a deadline somebody pushed out
            # (and then forgot for a week) is visible before it breaches -
            # the counter is already maintained by the extend service.
            "extension_count": int(getattr(r, "extension_count", 0) or 0),
            "policy_name": r.policy.name if r.policy else None,
            # SLA-config-driven next action for form rows (e.g. "Send for
            # approval", "Approve", "Mark resolved"); None for conversation rows.
            "next_action": next_action,
        }
        if is_ticket:
            # UAC AC-B1: never re-derived by the widget - explicit backend flag.
            row["is_intervention_ticket"] = True
            row["contact_name"] = contact_name
            row["contact_phone"] = contact_phone
            snippet = (getattr(r, "source_message_text", None) or "").strip()
            row["enquiry_snippet"] = (snippet[:140] or None) if snippet else None
            row["source_message_id"] = r.source_message_id
            row["initiated_at"] = (
                r.initiated_at.isoformat() if r.initiated_at else None
            )
            row["escalated_at"] = (
                r.escalated_at.isoformat() if r.escalated_at else None
            )
        return row

    def _contact_lookup_for_rows(
        self, rows: list[ConversationSLATracking]
    ) -> tuple[dict, dict, dict]:
        """Batched respond_io_id / name / phone lookup, keyed by
        `respond_contact_id`, for conversation rows on a worklist page.
        Shared by /my-pending and /team-pending so this query is written once.
        """
        respond_io_by_contact: dict[str, Optional[str]] = {}
        contact_name_by_contact: dict[str, Optional[str]] = {}
        contact_phone_by_contact: dict[str, Optional[str]] = {}
        contact_ids = {
            str(r.respond_contact_id)
            for r in rows
            if getattr(r, "respond_contact_id", None)
        }
        if not contact_ids:
            return respond_io_by_contact, contact_name_by_contact, contact_phone_by_contact
        try:
            for cid, rio, name, phone in (
                self.db.query(
                    RespondContact.id,
                    RespondContact.respond_io_id,
                    RespondContact.name,
                    RespondContact.phone_number,
                )
                .filter(RespondContact.id.in_(contact_ids))
                .all()
            ):
                respond_io_by_contact[str(cid)] = str(rio) if rio else None
                contact_name_by_contact[str(cid)] = name
                contact_phone_by_contact[str(cid)] = phone
        except Exception:  # noqa: BLE001
            self.db.rollback()
        return respond_io_by_contact, contact_name_by_contact, contact_phone_by_contact

    def list_my_pending(self, user_id: str, limit: int = 1000) -> list[dict]:
        """ALL unresolved SLA trackers assigned to ``user_id``, soonest-due first.

        Unlike ``list_tracking`` (the conversation list, which excludes form SLA
        types), this powers a per-user to-do widget and INCLUDES form trackers
        (stock_inquiry / complaint / purchase_request) since those are the items
        the assignee must action.

        Returns the user's FULL pending set (safety-capped at ``limit``) so the widget
        can show an honest total and search/paginate over everything client-side. It
        used to fetch only the soonest-50, which both under-counted the badge and hid
        any search match past that window (a user with 50+ overdue items could never
        find a later-due one). Row-building is fully batched (O(1) queries regardless
        of row count), so returning the whole set stays cheap.
        """
        from sqlalchemy.orm import joinedload

        rows = (
            self.db.query(ConversationSLATracking)
            .options(joinedload(ConversationSLATracking.policy))
            .filter(
                ConversationSLATracking.assigned_to_id == user_id,
                # A stage voided by a contact revision is off this user's plate.
                *open_tracker_scope(),
            )
            .order_by(ConversationSLATracking.due_at.asc())
            .limit(limit)
            .all()
        )
        reference_by_row = self._resolve_my_pending_references(rows)
        action_by_row = self._form_next_actions(rows)
        respond_io_by_contact, contact_name_by_contact, contact_phone_by_contact = (
            self._contact_lookup_for_rows(rows)
        )
        # Ticket-only: the ROUTING team (agent_id/team_set_code/tier), distinct
        # from Team Tasks' assignee-context team_label layered on in
        # list_team_pending - unused by the widget list today, kept for the
        # drawer/detail surfaces that do read a ticket's own team.
        team_label_by_row = self._ticket_team_labels(rows)

        result = []
        for r in rows:
            contact_key = (
                str(r.respond_contact_id)
                if getattr(r, "respond_contact_id", None)
                else None
            )
            row = self._pending_row(
                r,
                reference=reference_by_row.get(str(r.id)),
                next_action=action_by_row.get(str(r.id)),
                respond_io_id=(
                    respond_io_by_contact.get(contact_key) if contact_key else None
                ),
                contact_name=(
                    contact_name_by_contact.get(contact_key) if contact_key else None
                ),
                contact_phone=(
                    contact_phone_by_contact.get(contact_key) if contact_key else None
                ),
            )
            if row.get("is_intervention_ticket"):
                row["team_label"] = team_label_by_row.get(str(r.id))
            result.append(row)
        return result

    def _ticket_team_labels(
        self, rows: list[ConversationSLATracking]
    ) -> dict[str, Optional[str]]:
        """Tracker id -> the display name of the team bound to its
        (agent_id, team_set_code, current_tier) - the enquiry header's "team" line.

        Batched: one query for every distinct triple across the whole row set,
        never per row. Rows with no ``agent_id`` (legacy, pre-agent-routing) map
        to None; the FE renders "Unassigned team".
        """
        from app.models.access import AgentTeam, Team

        triples = {
            (str(r.agent_id), r.team_set_code or "", int(r.current_tier or 1))
            for r in rows
            if getattr(r, "agent_id", None)
        }
        if not triples:
            return {}
        agent_ids = {t[0] for t in triples}
        try:
            lookup: dict[tuple, str] = {}
            for agent_id, code, tier, name in (
                self.db.query(AgentTeam.agent_id, AgentTeam.code, AgentTeam.tier, Team.name)
                .join(Team, Team.id == AgentTeam.team_id)
                .filter(AgentTeam.agent_id.in_(agent_ids))
                .all()
            ):
                lookup[(str(agent_id), code or "", int(tier or 1))] = name
        except Exception:  # noqa: BLE001
            self.db.rollback()
            return {}
        out: dict[str, Optional[str]] = {}
        for r in rows:
            if not getattr(r, "agent_id", None):
                continue
            key = (str(r.agent_id), r.team_set_code or "", int(r.current_tier or 1))
            out[str(r.id)] = lookup.get(key)
        return out

    # ---- Team Tasks: visibility, listing, takeover, reassign ----------------

    def _is_admin(self, user_id: str) -> bool:
        """Admin / superadmin bypass the team-membership scope.

        Team scope is "permission == visibility": you may act on the tasks that
        show up in YOUR Team Tasks. An admin opening a form detail page can see
        the form and its open SLA task, so refusing the action there (and doing
        it with a "not found" message) reads as a bug. Mirrors the
        superadmin/admin short-circuit used by the module guards.
        """
        try:
            from app.services.user_service import UserPermissionService

            slugs = UserPermissionService(self.db).get_user_role_slugs(str(user_id))
            return bool({"admin", "superadmin"} & set(slugs or ()))
        except Exception:  # noqa: BLE001
            # Fail CLOSED: a role lookup failure must not widen anyone's scope.
            _module_logger.warning(
                "Role lookup failed for %s; treating as non-admin.", user_id
            )
            return False

    def _visible_team_ids(self, user_id: str) -> set:
        """Teams the user is a member of ∪ all their descendants (recursive)."""
        from app.models.access import TeamMember
        from app.services.user_service import descendant_team_ids

        my_team_ids = {
            str(tid)
            for (tid,) in self.db.query(TeamMember.team_id)
            .filter(TeamMember.user_id == str(user_id))
            .all()
        }
        if not my_team_ids:
            return set()
        return descendant_team_ids(self.db, my_team_ids)

    def _members_of_teams(self, team_ids) -> set:
        """Distinct user ids who are members of any of ``team_ids``."""
        from app.models.access import TeamMember

        ids = [str(t) for t in (team_ids or []) if t]
        if not ids:
            return set()
        return {
            str(uid)
            for (uid,) in self.db.query(TeamMember.user_id)
            .filter(TeamMember.team_id.in_(ids))
            .all()
        }

    def _team_label_by_member(self, team_ids, member_ids) -> dict[str, tuple]:
        """For each member id, a representative (team_id, team_name) within the
        visible set - context for the Team Tasks row (a user can be in several
        visible teams; the first by name is used for display)."""
        from app.models.access import Team, TeamMember

        tids = [str(t) for t in (team_ids or []) if t]
        mids = [str(m) for m in (member_ids or []) if m]
        if not tids or not mids:
            return {}
        rows = (
            self.db.query(TeamMember.user_id, Team.id, Team.name)
            .join(Team, Team.id == TeamMember.team_id)
            .filter(TeamMember.team_id.in_(tids), TeamMember.user_id.in_(mids))
            .order_by(Team.name.asc())
            .all()
        )
        out: dict[str, tuple] = {}
        for uid, team_id, team_name in rows:
            if str(uid) not in out:
                out[str(uid)] = (str(team_id), team_name)
        return out

    def can_user_act_on_tracking(
        self, user_id: str, tracking: ConversationSLATracking
    ) -> bool:
        """Permission == visibility: the user may takeover/reassign exactly the
        tasks visible in their Team Tasks (or their own tasks). True when the
        current assignee is a member of any of the user's visible teams, OR the
        task is the user's own (reassign from My Pending)."""
        assignee = getattr(tracking, "assigned_to_id", None)
        if assignee is not None and str(assignee) == str(user_id):
            return True
        if self._is_admin(user_id):
            return True
        # A conversation ticket keeps its assignee on resolve (owner ruling R5,
        # 22 Sep 2026), but a resolver who was never the assignee (e.g. an admin
        # or teammate resolving on someone else's behalf) still needs the drawer
        # AC-M1 keeps open in front of them (thread, comments, snippet picker, AI
        # draft) the instant they press Resolve. Read access also follows whoever
        # resolved it; the write paths (send, takeover, reassign, escalate) keep
        # their own is_resolved guards, so this widens reading only.
        resolved_by = getattr(tracking, "resolved_by", None)
        if resolved_by is not None and str(resolved_by) == str(user_id):
            return True
        if assignee is None:
            return False
        members = self._members_of_teams(self._visible_team_ids(user_id))
        return str(assignee) in members

    def can_user_resolve_tracking(
        self, user_id: str, tracking: ConversationSLATracking
    ) -> bool:
        """Only the current assignee (or an admin) may resolve a task.

        `can_user_act_on_tracking` is deliberately wider - it grants a whole
        team READ access to a teammate's task (My Team). Resolving it is a
        stricter, narrower gate: a viewer who merely has visibility into the
        task must not be able to close someone else's clock. Drives both
        `get_ticket_detail`'s `can_resolve` field and the resolve route's
        enforcement, so the drawer's disabled button and the 403 it would get
        anyway always agree.
        """
        if self._is_admin(user_id):
            return True
        assignee = getattr(tracking, "assigned_to_id", None)
        return assignee is not None and str(assignee) == str(user_id)

    def _picker_rows(self, member_ids: set) -> list[dict]:
        """Serialize a picker's user set. ONE builder for both branches below,
        so a field added for the picker cannot land on only one of them.

        ``respond_linked`` (UAC AC-N7) says whether a reply sent by this person
        can carry a real Respond sender identity - resolved by the SAME helper
        the send path uses, so the badge never promises a linkage the send would
        find unusable. Human-readable name, no UUIDs beyond the row id.
        """
        from app.models.user import User
        from app.services.crm_chat_outbound_webhook import usable_respond_user_id

        if not member_ids:
            return []
        rows = self.db.query(User).filter(User.id.in_(list(member_ids))).all()
        out = [
            {
                "id": str(u.id),
                "name": (u.name or u.email or "").strip() or None,
                "email": u.email,
                "respond_linked": usable_respond_user_id(u) is not None,
            }
            for u in rows
        ]
        out.sort(key=lambda x: (x["name"] or x["email"] or "").lower())
        return out

    def list_visible_users(self, user_id: str) -> list[dict]:
        """Reassign picker source: every user who belongs to at least one team,
        excluding myself. Not scoped to the actor's teams (hand-off may cross
        teams, decision 2026-09-03); users with no team have no SLA routing so
        they are excluded."""
        # Every user who belongs to at least one team, i.e. everyone who can
        # actually own an SLA task (22 people here, vs ~2.5k user rows). An
        # unfiltered user list would make the picker unusable.
        from app.models.access import TeamMember

        member_ids = {
            str(uid) for (uid,) in self.db.query(TeamMember.user_id).distinct().all()
        }
        member_ids.discard(str(user_id))
        return self._picker_rows(member_ids)

    def list_team_pending(
        self,
        user_id: str,
        assignee: Optional[str] = None,
        team: Optional[str] = None,
        page: int = 1,
        limit: int = 50,
        query: Optional[str] = None,
    ) -> dict:
        """Unresolved SLA tasks of teammates in the user's visible teams (peers +
        recursive child/grandchild teams), excluding the user's own (those live in
        My Pending). Includes BOTH conversation and form rows (whole workload).

        ``query`` is a free-text filter over the entity number / contact name
        (reference), assignee name, team label and type. References are resolved
        post-query, so when a query is given we resolve a capped candidate set then
        filter + paginate in Python; otherwise we paginate in the DB.

        Returns {"data": [...], "total": int, "page": int, "limit": int}.
        """
        from sqlalchemy.orm import joinedload
        from app.models.user import User

        visible_team_ids = self._visible_team_ids(user_id)
        member_ids = self._members_of_teams(visible_team_ids)
        member_ids.discard(str(user_id))  # exclude self
        if not member_ids:
            return {"data": [], "total": 0, "page": page, "limit": limit}

        # Optional team filter: restrict to members of that single team (must be in
        # the visible set, else it yields nothing).
        if team:
            team_member_ids = self._members_of_teams([str(team)])
            member_ids = {m for m in member_ids if m in team_member_ids}
        # Optional assignee filter.
        if assignee:
            member_ids = {m for m in member_ids if m == str(assignee)}
        if not member_ids:
            return {"data": [], "total": 0, "page": page, "limit": limit}

        base = (
            self.db.query(ConversationSLATracking)
            .options(
                joinedload(ConversationSLATracking.policy),
                joinedload(ConversationSLATracking.assigned_user),
            )
            .filter(
                *open_tracker_scope(),
                ConversationSLATracking.assigned_to_id.in_(list(member_ids)),
                ConversationSLATracking.assigned_to_id != str(user_id),
            )
        )
        q = (query or "").strip().lower()
        if q:
            # Resolve a capped candidate set, then filter on the resolved fields.
            rows = base.order_by(ConversationSLATracking.due_at.asc()).limit(1000).all()
        else:
            total = base.count()
            rows = (
                base.order_by(ConversationSLATracking.due_at.asc())
                .offset(max(0, (page - 1) * limit))
                .limit(limit)
                .all()
            )

        reference_by_row = self._resolve_my_pending_references(rows)
        action_by_row = self._form_next_actions(rows)

        # Resolve assignee names (no UUIDs) + a representative team label per row.
        row_assignee_ids = {
            str(r.assigned_to_id) for r in rows if getattr(r, "assigned_to_id", None)
        }
        name_by_id: dict[str, Optional[str]] = {}
        if row_assignee_ids:
            for u in self.db.query(User).filter(User.id.in_(row_assignee_ids)).all():
                name_by_id[str(u.id)] = (u.name or u.email or "").strip() or None
        team_by_member = self._team_label_by_member(visible_team_ids, row_assignee_ids)
        respond_io_by_contact, contact_name_by_contact, contact_phone_by_contact = (
            self._contact_lookup_for_rows(rows)
        )

        data = []
        for r in rows:
            aid = str(r.assigned_to_id) if getattr(r, "assigned_to_id", None) else None
            team_ctx = team_by_member.get(aid) if aid else None
            contact_key = (
                str(r.respond_contact_id)
                if getattr(r, "respond_contact_id", None)
                else None
            )
            # SAME row contract as /my-pending (is_intervention_ticket,
            # enquiry_snippet, respond_io_id, ...) - My Team used to be a
            # thinner, independently-built row and a ticket row there fell
            # through to the SLA detail page instead of opening the drawer.
            # Team-only fields (assignee/team context) are layered on after.
            row = self._pending_row(
                r,
                reference=reference_by_row.get(str(r.id)),
                next_action=action_by_row.get(str(r.id)),
                respond_io_id=(
                    respond_io_by_contact.get(contact_key) if contact_key else None
                ),
                contact_name=(
                    contact_name_by_contact.get(contact_key) if contact_key else None
                ),
                contact_phone=(
                    contact_phone_by_contact.get(contact_key) if contact_key else None
                ),
            )
            row["assignee_id"] = aid
            row["assignee_name"] = name_by_id.get(aid) if aid else None
            row["team_id"] = team_ctx[0] if team_ctx else None
            row["team_label"] = team_ctx[1] if team_ctx else None
            data.append(row)

        if q:
            def _hay(d: dict) -> str:
                return " ".join(
                    str(d.get(k) or "")
                    for k in ("reference", "assignee_name", "team_label",
                              "source_entity_type", "policy_name")
                ).lower()

            data = [d for d in data if q in _hay(d)]
            total = len(data)
            start = max(0, (page - 1) * limit)
            data = data[start : start + limit]

        return {"data": data, "total": total, "page": page, "limit": limit}

    def _push_respond_assignee(
        self, tracking: ConversationSLATracking, new_respond_user_id: Optional[str]
    ) -> None:
        """Enqueue a Respond.io conversation-assignee push (conversation rows only).

        Used by reassign, takeover, and escalate so the Respond conversation owner
        follows the CRM assignee. The actual Respond call + its outbox row (success
        AND failure, with the Respond HTTP status/body on 4xx/5xx) run on the
        ``respond_io`` worker queue - decoupled from the request. Form-SLA rows have
        no Respond conversation -> skipped; a missing assignee respond_user_id ->
        skipped. Enqueue is best-effort: never raises (post-commit side effect)."""
        import logging
        from app.services.form_sla_service import FORM_SLA_TYPES

        log = logging.getLogger(__name__)
        s_type = getattr(tracking, "source_entity_type", None)
        if s_type in FORM_SLA_TYPES:
            return  # form SLA -> no Respond conversation
        if not getattr(tracking, "respond_contact_id", None):
            log.warning(
                "Respond assignee push skipped for %s: no linked contact.",
                getattr(tracking, "id", "?"),
            )
            return
        if not new_respond_user_id or not str(new_respond_user_id).strip():
            log.warning(
                "Respond assignee push skipped for %s: assignee has no respond_user_id.",
                getattr(tracking, "id", "?"),
            )
            return
        try:
            from app.services.queue_service import enqueue_job
            from app.tasks.respond_io_tasks import set_respond_conversation_assignee

            enqueue_job(
                set_respond_conversation_assignee,
                str(tracking.id),
                str(new_respond_user_id).strip(),
                queue_name="respond_io",
                job_timeout=60,
            )
        except Exception as exc:  # noqa: BLE001 - enqueue is best-effort
            log.warning(
                "Respond assignee push enqueue failed for %s: %s",
                getattr(tracking, "id", "?"),
                exc,
            )

    def _notify_reassignment(
        self,
        tracking: ConversationSLATracking,
        *,
        actor_id: str,
        new_assignee_id: str,
        old_assignee_id: Optional[str],
    ) -> None:
        """Notify on takeover/reassign: new assignee always; old assignee only when
        the actor differs (no self-notify). In-app always; email/WhatsApp gated by
        the recipient's own assignment toggles. Best-effort. Fans out to coverage
        subscribers of the new assignee."""
        import logging

        log = logging.getLogger(__name__)
        try:
            from app.config import settings
            from app.models.user import User
            from app.services.notification_service import NotificationService
            from app.services.coverage_subscription_service import (
                fan_out_coverage_copies,
            )
            from app.services.form_sla_service import build_sla_whatsapp_data

            base_url = (getattr(settings, "frontend_base_url", None) or "").strip().rstrip("/")
            detail = (
                f"{base_url}/sla-management/conversation-sla-tracking/{tracking.id}"
                if base_url
                else ""
            )
            ref = (self._resolve_my_pending_references([tracking]) or {}).get(
                str(tracking.id)
            ) or "an SLA task"

            # New assignee - always. Same window-aware WhatsApp data as form SLA assign.
            new_title = "An SLA task was assigned to you"
            # AC-G2: the new owner is told which clock is running and until when.
            new_body = append_clock_line(f"{ref} has been assigned to you.", tracking)
            if detail:
                new_body += f"\n\nOpen: {detail}"
            new_data = {
                "tracking_id": str(tracking.id),
                **build_sla_whatsapp_data(self.db, tracking, str(new_assignee_id), new_body),
            }
            NotificationService(self.db).create_with_channel_preferences(
                user_id=str(new_assignee_id),
                type="conversation_sla",
                title=new_title,
                body=new_body,
                data=new_data,
                source_entity_type="conversation_sla_tracking",
                source_entity_id=str(tracking.id),
                event_type="reassigned",
                send_in_app=True,
                send_email=True,
                send_whatsapp=True,
                email_pref_attr="notify_email_on_assignment",
                whatsapp_pref_attr="notify_whatsapp_on_assignment",
            )
            fan_out_coverage_copies(
                self.db,
                target_user_id=str(new_assignee_id),
                actor_user_id=str(actor_id),
                notification_type="conversation_sla",
                title=new_title,
                body=new_body,
                data=new_data,
                source_entity_type="conversation_sla_tracking",
                source_entity_id=str(tracking.id),
                event_type="reassigned",
                email_pref_attr="notify_email_on_assignment",
                whatsapp_pref_attr="notify_whatsapp_on_assignment",
            )

            # Old assignee - only when actor != old assignee (someone moved your task).
            if (
                old_assignee_id
                and str(old_assignee_id) != str(actor_id)
                and str(old_assignee_id) != str(new_assignee_id)
            ):
                old_title = "Your SLA task was moved"
                old_body = f"{ref} was reassigned to someone else."
                if detail:
                    old_body += f"\n\nOpen: {detail}"
                old_data = {
                    "tracking_id": str(tracking.id),
                    **build_sla_whatsapp_data(
                        self.db, tracking, str(old_assignee_id), old_body,
                        use_case="sla_task_moved",
                    ),
                }
                NotificationService(self.db).create_with_channel_preferences(
                    user_id=str(old_assignee_id),
                    type="conversation_sla",
                    title=old_title,
                    body=old_body,
                    data=old_data,
                    source_entity_type="conversation_sla_tracking",
                    source_entity_id=str(tracking.id),
                    event_type="reassigned_away",
                    send_in_app=True,
                    send_email=True,
                    send_whatsapp=True,
                    email_pref_attr="notify_email_on_assignment",
                    whatsapp_pref_attr="notify_whatsapp_on_assignment",
                )
        except Exception as e:  # noqa: BLE001 - best-effort; mutation already committed
            log.warning(
                "reassignment notify failed for %s: %s", getattr(tracking, "id", "?"), e
            )

    def takeover(self, tracking_id: str, user_id: str, team_id: str) -> ConversationSLATracking:
        """Grab a visible task for myself (Team Tasks only). Clock-preserving:
        re-derive (agent_id, team_set_code, current_tier) so the task sits at MY OWN
        tier in the task's agent chain (a tier-3 approver takes it at tier 3, not the
        queue team's tier-1), set assignee to me, advance that team's RR cursor to me,
        write a 'reassignment' (takeover) event log, push Respond + notify. Does NOT
        touch due_at / due_at_resolution / current_tier_started_at.

        Resolution order for (agent_id, team, tier):
          1. The taker's own membership in the task's agent chain, preferring the
             tracking's own team set: a link they hold within ``team_set_code`` wins,
             and only when they hold none there does it fall back across sets (most
             senior tier first, then team set code) so escalation continues above
             them. This is what a user means by "take it onto my desk".
          2. Fall back to the passed queue ``team_id``'s AgentTeam link when the taker
             is not part of that agent's chain (pure cover-for-another-team case).
        """
        from app.models.access import AgentTeam, AgentTeamRoundRobinCursor, TeamMember
        from app.models.user import User

        tracking = self.get_tracking(tracking_id, load_event_logs=False)
        if bool(getattr(tracking, "is_resolved", False)):
            raise handle_validation_error("Cannot take over a resolved SLA task.")
        if not self.can_user_act_on_tracking(user_id, tracking):
            # An UNOWNED task is grabbable from a visible queue team (AC-INIT-3); an
            # assigned task requires assignee-visibility.
            if getattr(tracking, "assigned_to_id", None) is not None or str(
                team_id
            ) not in self._visible_team_ids(user_id):
                raise handle_not_found("SLA Tracking", tracking_id)

        me = self.db.query(User).filter(User.id == str(user_id)).first()
        if not me:
            raise handle_not_found("User", user_id)
        old_assignee_id = getattr(tracking, "assigned_to_id", None)

        # The queue team the row was shown under (assignee's team) - the fallback.
        passed_link = (
            self.db.query(AgentTeam)
            .filter(AgentTeam.team_id == str(team_id))
            .order_by(AgentTeam.tier.asc().nullslast())
            .first()
        )
        # Keep the task's existing agent chain when set; otherwise infer it from the
        # passed queue team.
        agent_id = getattr(tracking, "agent_id", None) or (
            str(passed_link.agent_id) if passed_link else None
        )

        # Prefer the taker's OWN standing in that agent chain (most senior tier held),
        # resolved inside the task's own team set when they belong to it (tier-1
        # membership is unique per set, not per agent).
        _tracking_set = getattr(tracking, "team_set_code", None)
        link = self._agent_link_for_user(
            agent_id,
            str(user_id),
            team_set_code=str(_tracking_set) if _tracking_set else None,
        )
        # Fall back to the passed queue team's link (cover for another team's chain).
        if not link:
            link = passed_link
        if not link:
            raise handle_validation_error(
                "The selected team has no agent/escalation configuration."
            )

        target_team_id = str(link.team_id)
        setattr(tracking, "assigned_to_id", str(user_id))
        setattr(tracking, "assigned_to", getattr(me, "respond_user_id", None))
        setattr(tracking, "agent_id", str(link.agent_id))
        setattr(tracking, "team_set_code", link.code)
        if link.tier is not None:
            setattr(tracking, "current_tier", int(link.tier))

        # Advance the resolved team's RR cursor to me (upsert).
        cursor = (
            self.db.query(AgentTeamRoundRobinCursor)
            .filter(
                AgentTeamRoundRobinCursor.agent_id == str(link.agent_id),
                AgentTeamRoundRobinCursor.team_id == target_team_id,
            )
            .with_for_update()
            .first()
        )
        if cursor:
            setattr(cursor, "last_assigned_user_id", str(user_id))
        else:
            self.db.add(
                AgentTeamRoundRobinCursor(
                    agent_id=str(link.agent_id),
                    team_id=target_team_id,
                    last_assigned_user_id=str(user_id),
                )
            )
        self.db.commit()
        self.db.refresh(tracking)

        # Event log (best-effort post-commit side effect).
        self._write_reassignment_log(
            tracking, assigned_to_id=str(user_id), triggered_by_id=str(user_id), reason="takeover"
        )
        # Respond push + notify (both best-effort).
        self._push_respond_assignee(tracking, getattr(me, "respond_user_id", None))
        self._notify_reassignment(
            tracking,
            actor_id=str(user_id),
            new_assignee_id=str(user_id),
            old_assignee_id=str(old_assignee_id) if old_assignee_id else None,
        )
        return tracking

    def _agent_link_for_user(self, agent_id, user_id, team_set_code: Optional[str] = None):
        """The AgentTeam link representing ``user_id``'s standing in agent
        ``agent_id``'s chain - the most senior tier they hold (so escalation
        continues above them). None when the user isn't part of that chain.

        Tier-1 membership is unique only PER TEAM SET, so a user can hold links to
        DIFFERENT teams at the same tier under one agent. ``team_set_code`` (the
        tracking's own set) is therefore preferred: links in that set are considered
        first, and only when the user holds none there do we fall back across sets.
        Either way the tie is broken deterministically by team set code and then team
        id, so the same user always resolves to the same link.
        Shared by takeover (taker) and reassign (target) for tier re-derivation."""
        from app.models.access import AgentTeam, TeamMember

        if not agent_id:
            return None
        my_team_ids = [
            str(t)
            for (t,) in self.db.query(TeamMember.team_id)
            .filter(TeamMember.user_id == str(user_id))
            .all()
        ]
        if not my_team_ids:
            return None

        def _query(code: Optional[str]):
            q = self.db.query(AgentTeam).filter(
                AgentTeam.agent_id == str(agent_id),
                AgentTeam.team_id.in_(my_team_ids),
            )
            if code:
                q = q.filter(AgentTeam.code == str(code))
            return q.order_by(
                AgentTeam.tier.desc().nullslast(),
                AgentTeam.code.asc(),
                # Equal tier within ONE code is legal at tier 2/3, so code alone is
                # not a total order - team_id makes the pick stable across calls.
                AgentTeam.team_id.asc(),
            ).first()

        if team_set_code:
            in_set = _query(team_set_code)
            if in_set is not None:
                return in_set
        return _query(None)

    def reassign(self, tracking_id: str, user_id: str, target_user_id: str) -> ConversationSLATracking:
        """Hand a task to a chosen person (My Pending or Team Tasks). Re-derives the
        tier/team_set_code to the TARGET's own standing in the task's agent chain
        (handing a tier-3 task to a tier-2 member moves it to tier 2), keeping the
        same agent and ALL clocks. The tracking's own team set is preferred: a link
        the target holds within ``team_set_code`` wins, and only when they hold none
        there does it fall back across sets by seniority (tier desc, then code asc).
        When the target isn't in that agent's chain, the
        existing team/tier are kept. Target must belong to at least one team
        (hand-off may cross teams, decision 2026-09-03). Any team member may
        reassign any unresolved task, regardless of whose scope it currently
        sits in (decision 2026-09-03): the earlier "actor must be in scope of
        the assignee" rule locked out an agent whose ticket got escalated to
        her manager's (parent) team mid-click, so it is gone. Admins pass
        regardless.
        Writes a 'reassignment' event log, pushes Respond + notifies.
        """
        from app.models.user import User
        from app.models.access import TeamMember

        tracking = self.get_tracking(tracking_id, load_event_logs=False)
        if bool(getattr(tracking, "is_resolved", False)):
            raise handle_validation_error("Cannot reassign a resolved SLA task.")

        # Actor gate (decision 2026-09-03): any team member may reassign any
        # unresolved task; only someone in no team at all is turned away.
        # Admins bypass. This replaced the visible-scope check that blocked an
        # agent reassigning her own escalated task the moment it moved to her
        # manager's (parent) team.
        if not self._is_admin(user_id):
            _actor_has_team = (
                self.db.query(TeamMember.user_id)
                .filter(TeamMember.user_id == str(user_id))
                .first()
                is not None
            )
            if not _actor_has_team:
                raise handle_validation_error(
                    "You are not in any team, so you cannot reassign SLA tasks."
                )

        # Target must be a member of at least one team, i.e. someone who can
        # actually own an SLA task. Cross-team hand-off is allowed; only a
        # target with no team routing at all is rejected.
        _has_team = (
            self.db.query(TeamMember.user_id)
            .filter(TeamMember.user_id == str(target_user_id))
            .first()
            is not None
        )
        if not _has_team:
            raise handle_validation_error(
                "This user is not in any team, so they cannot own an SLA task."
            )
        target = self.db.query(User).filter(User.id == str(target_user_id)).first()
        if not target:
            raise handle_not_found("User", target_user_id)
        old_assignee_id = getattr(tracking, "assigned_to_id", None)

        # Coverage redirect (manual reassign, decision 2): if the chosen target is
        # covered (on leave), route to their coverer instead. The scope-B check above
        # validated the actor's chosen target; the coverer inherits the assignment.
        # One hop. reassign_covered_for_id stamps the event log.
        from app.services.coverage_subscription_service import (
            resolve_assignee_with_coverage,
        )

        reassign_covered_for_id: Optional[str] = None
        _target_rid = getattr(target, "respond_user_id", None)
        _redirected, reassign_covered_for_id = resolve_assignee_with_coverage(
            self.db,
            {
                "id": str(target_user_id),
                "email": getattr(target, "email", None),
                "name": getattr(target, "name", None),
                "respond_user_id": _target_rid,
            },
        )
        if reassign_covered_for_id and _redirected:
            target_user_id = str(_redirected["id"])
            target = self.db.query(User).filter(User.id == target_user_id).first()
            if not target:
                raise handle_not_found("User", target_user_id)

        # Takeover-cooldown interaction: a third party cannot reassign a task that has a
        # pending takeover (soft lock, AC-VOID-4); the owner reassigning their own task
        # away IS allowed and voids the pending takeover (implicit veto, AC-VOID-2).
        from app.services.sla_takeover_service import SlaTakeoverService

        _tk = SlaTakeoverService(self.db)
        _pending = _tk.get_pending_for_tracking(tracking_id)
        if _pending is not None:
            _is_owner = old_assignee_id is not None and str(old_assignee_id) == str(user_id)
            if not _is_owner and not _tk._is_admin(user_id):
                raise handle_validation_error(
                    "A takeover is pending for this task; it can't be reassigned right now."
                )

        setattr(tracking, "assigned_to_id", str(target_user_id))
        setattr(tracking, "assigned_to", getattr(target, "respond_user_id", None))
        # Re-derive tier/team_set_code to the target's standing in this agent chain,
        # preferring the task's own team set; keep the agent and all clocks. No
        # membership in the chain -> leave as-is.
        _tracking_set = getattr(tracking, "team_set_code", None)
        link = self._agent_link_for_user(
            getattr(tracking, "agent_id", None),
            str(target_user_id),
            team_set_code=str(_tracking_set) if _tracking_set else None,
        )
        if link is not None:
            setattr(tracking, "team_set_code", link.code)
            if link.tier is not None:
                setattr(tracking, "current_tier", int(link.tier))
        self.db.commit()
        self.db.refresh(tracking)

        reassign_reason = "reassign"
        if reassign_covered_for_id:
            from app.services.coverage_subscription_service import coverage_note

            reassign_reason = f"reassign{coverage_note(self.db, reassign_covered_for_id)}"
        self._write_reassignment_log(
            tracking,
            assigned_to_id=str(target_user_id),
            triggered_by_id=str(user_id),
            reason=reassign_reason,
        )
        self._push_respond_assignee(tracking, getattr(target, "respond_user_id", None))
        self._notify_reassignment(
            tracking,
            actor_id=str(user_id),
            new_assignee_id=str(target_user_id),
            old_assignee_id=str(old_assignee_id) if old_assignee_id else None,
        )
        # Owner reassigned their own task away -> void the pending takeover (best-effort).
        if _pending is not None:
            _tk.void_for_tracking(tracking_id, "reassigned")
        # Two worklists changed: the task left one pending list and joined
        # another, so both owners are poked (AC-K3).
        self._publish_conversation_event(
            tracking,
            conversation_event_bus.EVENT_TICKET_UPDATED,
            user_ids=[old_assignee_id, target_user_id],
        )
        return tracking

    def _write_reassignment_log(
        self,
        tracking: ConversationSLATracking,
        *,
        assigned_to_id: str,
        triggered_by_id: str,
        reason: str,
    ) -> None:
        """Best-effort 'reassignment' event log for takeover/reassign (post-commit)."""
        import logging

        log = logging.getLogger(__name__)
        try:
            tier = int(getattr(tracking, "current_tier", 0) or 0)
            self.create_event_log(
                ConversationSLAEventLogCreate(
                    sla_tracking_id=str(getattr(tracking, "id")),
                    event_type="reassignment",
                    from_tier=tier if tier >= 1 else None,
                    to_tier=tier if tier >= 1 else None,
                    event_at=_now_utc(),
                    reason=reason,
                    assigned_to_id=str(assigned_to_id),
                    trigger="manual",
                    triggered_by_id=str(triggered_by_id),
                    due_at=(
                        _to_aware_utc(getattr(tracking, "due_at"))
                        if isinstance(getattr(tracking, "due_at", None), datetime)
                        else None
                    ),
                )
            )
        except Exception as e:  # noqa: BLE001 - mutation already committed
            log.warning(
                "reassignment event log failed for %s: %s",
                getattr(tracking, "id", "?"),
                e,
            )

    def _form_next_actions(
        self, rows: list[ConversationSLATracking]
    ) -> dict[str, Optional[str]]:
        """For each FORM SLA row, the concrete next action the assignee must take,
        derived from the stage's SLA config (not a generic "respond/resolve").

        The active stage is identified by (source_entity_type, team_set_code) - the
        tracker copies team_set_code from the config that spawned it, and that pair
        is unique per stage. The action humanizes the stage's respond_event (while
        unresponded) or its primary resolve_event (the advance_on_event, else the
        first), so the to-do mirrors the workflow: PR `main` -> "Send for approval",
        PR `project_sales_manager` -> "Approve", `customer_service` -> "Mark resolved".
        """
        from app.services.form_sla_service import FORM_SLA_TYPES
        from app.models.sla import FormSLAConfig

        form_rows = [
            r for r in rows
            if (getattr(r, "source_entity_type", None) in FORM_SLA_TYPES)
        ]
        if not form_rows:
            return {}

        ent_types = {str(r.source_entity_type) for r in form_rows}
        cfg_by_key: dict[tuple, FormSLAConfig] = {}
        try:
            for cfg in (
                self.db.query(FormSLAConfig)
                .filter(FormSLAConfig.source_entity_type.in_(ent_types))
                .all()
            ):
                cfg_by_key[(cfg.source_entity_type, cfg.team_set_code)] = cfg
        except Exception:  # noqa: BLE001
            self.db.rollback()
            return {}

        out: dict[str, Optional[str]] = {}
        for r in form_rows:
            cfg = cfg_by_key.get((str(r.source_entity_type), getattr(r, "team_set_code", None)))
            if cfg is None:
                continue
            if not bool(r.is_responded) and cfg.respond_event:
                event = cfg.respond_event
            else:
                primary = cfg.advance_on_event or (
                    (cfg.resolve_event or "").split(",")[0].strip()
                )
                event = primary or cfg.resolve_event
            if event:
                out[str(r.id)] = _humanize_sla_event(event)
        return out

    @staticmethod
    def _active_due_iso(r: ConversationSLATracking, resolution_phase: bool) -> Optional[str]:
        """The governing deadline as ISO: resolution due when responded (with response
        due as a fallback when resolution due is unset), else the response due."""
        resolution = getattr(r, "due_at_resolution", None)
        response = getattr(r, "due_at", None)
        chosen = (resolution or response) if resolution_phase else response
        return chosen.isoformat() if chosen else None

    def _resolve_my_pending_references(
        self, rows: list[ConversationSLATracking]
    ) -> dict[str, Optional[str]]:
        """Map each tracker id -> a human-readable reference number.

        complaint -> complaint_number, stock_inquiry -> inquiry_number,
        purchase_request/sponsorship_form -> request_number, ticket -> ticket_number,
        and conversation SLAs with no source entity -> contact phone/name.
        Batched per type to avoid per-row queries.
        """
        from app.models.complaints import Complaint
        from app.models.procurement import StockInquiry, PurchaseRequestHeader
        from app.models.tickets import Ticket

        # Collect source ids per type and contact ids for fallback.
        ids_by_type: dict[str, set[str]] = {}
        contact_ids: set[str] = set()
        for r in rows:
            et = (r.source_entity_type or "").strip()
            if et and r.source_entity_id:
                ids_by_type.setdefault(et, set()).add(str(r.source_entity_id))
            elif r.respond_contact_id:
                contact_ids.add(str(r.respond_contact_id))

        def _num_map(id_col, num_col, ids: set[str], revision_col=None) -> dict[str, str]:
            """Batched id->number lookup. Fail-safe: any DB error (e.g. table not
            present in a minimal test schema) yields no references rather than
            breaking the widget.

            ``revision_col`` is passed for the portal-revisable types so the
            reference reads at its revision (``SI-26-0184-R2``, UAC N1) - the
            stored column stays bare."""
            if not ids:
                return {}
            try:
                out: dict[str, str] = {}
                cols = [id_col, num_col] + ([revision_col] if revision_col is not None else [])
                for row in self.db.query(*cols).filter(id_col.in_(ids)).all():
                    rec_id, num = row[0], row[1]
                    if num:
                        revision = row[2] if revision_col is not None else 0
                        out[str(rec_id)] = suffix_revision(str(num), revision)
                return out
            except Exception:  # noqa: BLE001
                self.db.rollback()
                return {}

        complaint_map = _num_map(Complaint.id, Complaint.complaint_number, ids_by_type.get("complaint") or set())
        inquiry_map = _num_map(
            StockInquiry.id,
            StockInquiry.inquiry_number,
            ids_by_type.get("stock_inquiry") or set(),
            StockInquiry.revision_no,
        )
        pr_ids = (ids_by_type.get("purchase_request") or set()) | (ids_by_type.get("sponsorship_form") or set())
        pr_map = _num_map(
            PurchaseRequestHeader.id,
            PurchaseRequestHeader.request_number,
            pr_ids,
            PurchaseRequestHeader.revision_no,
        )
        ticket_map = _num_map(Ticket.id, Ticket.ticket_number, ids_by_type.get("ticket") or set())

        contact_map: dict[str, Optional[str]] = {}
        if contact_ids:
            try:
                for cid, name, phone in (
                    self.db.query(RespondContact.id, RespondContact.name, RespondContact.phone_number)
                    .filter(RespondContact.id.in_(contact_ids))
                    .all()
                ):
                    contact_map[str(cid)] = (name or phone or "").strip() or None
            except Exception:  # noqa: BLE001
                self.db.rollback()

        result: dict[str, Optional[str]] = {}
        for r in rows:
            et = (r.source_entity_type or "").strip()
            sid = str(r.source_entity_id) if r.source_entity_id else None
            ref: Optional[str] = None
            if et == "complaint" and sid:
                ref = complaint_map.get(sid)
            elif et == "stock_inquiry" and sid:
                ref = inquiry_map.get(sid)
            elif et in ("purchase_request", "sponsorship_form") and sid:
                ref = pr_map.get(sid)
            elif et == "ticket" and sid:
                ref = ticket_map.get(sid)
            elif r.respond_contact_id:
                ref = contact_map.get(str(r.respond_contact_id))
            result[str(r.id)] = ref
        return result

    def get_preferred_tracking_for_contact(
        self, contact: RespondContact
    ) -> Optional[ConversationSLATracking]:
        """The single "preferred" conversation-SLA row for a contact: open first, else
        most recent by created_at.

        AC-F1 (multi-open consumer audit): a contact can now hold several open
        tickets simultaneously (per-enquiry identity, not a contact singleton). This
        is a deliberate MOST-RECENT-OPEN reduction, not a bug - callers here are all
        "give me a representative snapshot for this contact" reads (external GET-by-
        contact summary, next-assignee's "is this contact already assigned"
        signal, legacy set-assignee-by-phone) that predate per-ticket identity and
        were never meant to enumerate every open ticket. Callers that need the FULL
        open set (the worklist) use ``list_my_pending`` / ``list_tracking`` instead.
        Pinned by tests/test_conversation_multi_open_consumer_audit.py.
        """
        from sqlalchemy.orm import joinedload
        from app.models.sla import ConversationSLAEventLog

        return (
            self.db.query(ConversationSLATracking)
            .options(
                joinedload(ConversationSLATracking.policy),
                joinedload(ConversationSLATracking.contact),
                joinedload(ConversationSLATracking.assigned_user),
                joinedload(ConversationSLATracking.event_logs).joinedload(ConversationSLAEventLog.assigned_user),
            )
            .filter(
                ConversationSLATracking.respond_contact_id == contact.id,
                conversation_tracking_scope(),
            )
            .order_by(
                ConversationSLATracking.is_resolved.asc(),
                ConversationSLATracking.created_at.desc(),
            )
            .first()
        )

    def count_open_tickets_for_contact(self, contact: RespondContact) -> int:
        """How many OPEN conversation-scope tickets this contact holds (AC-I2).

        The counterpart to ``get_preferred_tracking_for_contact``: that one
        reduces a multi-open contact to a single representative row, which
        cannot answer "does this contact still have anything unresolved". n8n
        gates the customer-facing "conversation closed and resolved" message on
        this number, so it counts rows rather than picking one, and form-SLA
        stage rows are excluded via ``conversation_tracking_scope()`` (they share
        the table and belong to a different family).
        """
        return (
            self.db.query(func.count(ConversationSLATracking.id))
            .filter(
                ConversationSLATracking.respond_contact_id == contact.id,
                ConversationSLATracking.is_resolved.is_(False),
                conversation_tracking_scope(),
            )
            .scalar()
            or 0
        )

    def resolve_respond_contact(
        self,
        *,
        phone_number: Optional[str] = None,
        contact_id: Optional[str] = None,
    ) -> tuple[Optional[RespondContact], Optional[str]]:
        """
        Resolve RespondContact from phone and/or contact_id.

        contact_id matches respond_io_id first, then respond_contacts.id.

        Returns (contact, conflict_error_message). conflict_error_message is set when both
        identifiers are provided but refer to different contacts.
        """
        phone = (phone_number or "").strip()
        cid = (contact_id or "").strip()
        if not phone and not cid:
            return None, None
        by_phone: Optional[RespondContact] = None
        by_cid: Optional[RespondContact] = None
        if phone:
            by_phone = (
                self.db.query(RespondContact)
                .filter(RespondContact.phone_number == phone)
                .first()
            )
        if cid:
            by_cid = (
                self.db.query(RespondContact)
                .filter(RespondContact.respond_io_id == cid)
                .first()
            )
            if not by_cid:
                by_cid = (
                    self.db.query(RespondContact)
                    .filter(RespondContact.id == cid)
                    .first()
                )
        if phone and cid:
            if (
                by_cid is not None
                and by_phone is not None
                and str(getattr(by_cid, "id")) != str(getattr(by_phone, "id"))
            ):
                return None, "contact_id and phone_number refer to different contacts."
            if by_cid is not None:
                return by_cid, None
            return by_phone, None
        if cid:
            return by_cid, None
        return by_phone, None

    def get_tracking_by_phone_or_contact_id(
        self,
        *,
        phone_number: Optional[str] = None,
        contact_id: Optional[str] = None,
    ) -> Optional[ConversationSLATracking]:
        """
        Latest SLA tracking for the contact (open first, else newest).
        Pass contact_id (Respond.io id or CRM respond_contacts.id) and/or phone_number.
        """
        contact, _err = self.resolve_respond_contact(
            phone_number=phone_number, contact_id=contact_id
        )
        if not contact:
            return None
        return self.get_preferred_tracking_for_contact(contact)

    def get_tracking_by_contact_phone(self, phone: str) -> Optional[ConversationSLATracking]:
        """
        Get the conversation SLA tracking for a contact by phone number.
        Prefers an unresolved (open) tracking; otherwise returns the most recent by created_at.
        """
        normalized = (phone or "").strip()
        if not normalized:
            return None
        contact = self.db.query(RespondContact).filter(
            RespondContact.phone_number == normalized
        ).first()
        if not contact:
            return None
        return self.get_preferred_tracking_for_contact(contact)

    def get_tracking_by_contact_and_policy(
        self,
        respond_contact_id: str,
        policy_id: str,
    ) -> Optional[ConversationSLATracking]:
        """
        Get the conversation SLA tracking for a contact and policy.
        Prefers an unresolved (open) tracking; otherwise returns the most recent by created_at.

        AC-F1: a contact can hold several open tickets on the SAME policy at once,
        so this is a MOST-RECENT-OPEN pick among possibly-several matches, not a
        singleton lookup. Used as the legacy fallback in ``escalate_tracking``'s
        internal contact+policy resolution path (only when the caller has no
        ``tracking_id`` - see that method). Callers that already hold a specific
        tracking_id must NOT go through here - pass it directly instead.
        """
        from sqlalchemy.orm import joinedload
        from app.models.sla import ConversationSLAEventLog

        tracking = (
            self.db.query(ConversationSLATracking)
            .options(
                joinedload(ConversationSLATracking.policy),
                joinedload(ConversationSLATracking.contact),
                joinedload(ConversationSLATracking.event_logs).joinedload(ConversationSLAEventLog.assigned_user),
            )
            .filter(
                ConversationSLATracking.respond_contact_id == respond_contact_id,
                ConversationSLATracking.policy_id == policy_id,
                conversation_tracking_scope(),
            )
            .order_by(
                ConversationSLATracking.is_resolved.asc(),  # False first
                ConversationSLATracking.created_at.desc(),
            )
            .first()
        )
        return tracking

    def get_open_tracking_by_contact(
        self,
        respond_contact_id: str,
    ) -> Optional[ConversationSLATracking]:
        """Get an OPEN conversation SLA tracking for a contact (policy-agnostic).

        AC-F1 (multi-open consumer audit): conversation SLA is NO LONGER max
        one-open-per-contact - a contact can hold several open tickets at once
        (per-enquiry identity). This is the primary resolution path for
        ``POST /integration/escalate`` (n8n's signal-only, contact-keyed escalation
        call, which carries no tracking_id): with 2+ open tickets for the contact,
        it returns only the MOST-RECENTLY-CREATED open one - a documented, tested,
        interim limitation, not a crash. A sibling open ticket on the same contact
        is simply not escalated by that call; it still surfaces separately via
        ``list_due_escalations`` (which is per-tracking_id) whenever ITS OWN due_at
        breaches. Precise per-ticket escalation requires n8n to send the ticket id
        (S3.2 cutover); until then this stays the accepted contact-only behavior
        (regression net 3 - do not change without updating the n8n contract).
        Prefers an unresolved row; falls back to the most recent overall.
        """
        from sqlalchemy.orm import joinedload
        from app.models.sla import ConversationSLAEventLog

        return (
            self.db.query(ConversationSLATracking)
            .options(
                joinedload(ConversationSLATracking.policy),
                joinedload(ConversationSLATracking.contact),
                joinedload(ConversationSLATracking.event_logs).joinedload(ConversationSLAEventLog.assigned_user),
            )
            .filter(
                ConversationSLATracking.respond_contact_id == respond_contact_id,
                conversation_tracking_scope(),
            )
            .order_by(
                ConversationSLATracking.is_resolved.asc(),  # False (open) first
                ConversationSLATracking.created_at.desc(),
            )
            .first()
        )

    def resolve_internal_respond_contact_id(self, respond_contact_id: str) -> Optional[str]:
        """Map API respond_contact_id to respond_contacts.id (CRM id, Respond.io id, or phone / E.164)."""
        from app.models.access import RespondContact

        raw = str(respond_contact_id or "").strip()
        if not raw:
            return None
        row = self.db.query(RespondContact).filter(RespondContact.id == raw).first()
        if row:
            return str(row.id)
        row = self.db.query(RespondContact).filter(RespondContact.respond_io_id == raw).first()
        if row:
            return str(row.id)
        candidates = _respond_contact_phone_lookup_candidates(raw)
        if candidates:
            row = (
                self.db.query(RespondContact)
                .filter(RespondContact.phone_number.in_(candidates))
                .first()
            )
            if row:
                return str(row.id)
        return None

    # Map source_entity_type (on tracking) to access agent code for tier-based escalation.
    ENTITY_TYPE_TO_AGENT_CODE = {
        "complaint": "complaint",
        "stock_inquiry": "lead_time_enquiries",
        "purchase_request": "purchase_request",
    }

    def get_escalation_assignee_for_tier(
        self,
        source_entity_type: Optional[str],
        target_tier: int,
        team_set_code: Optional[str] = None,
        agent_code_override: Optional[str] = None,
        agent_id_override: Optional[str] = None,
        contact_segments: Optional[set] = None,
        *,
        company_id: str,
        brand_code: Optional[str] = None,
    ) -> dict:
        """
        Resolve the next assignee for escalation to the given tier using agent tier-team and round-robin.
        Uses only that tier's round-robin cursor (no dependency on the previous tier's assignee).
        Priority: agent_id_override (UUID FK) > agent_code_override > source_entity_type mapping.
        Returns dict with id, email, name, respond_user_id. Raises if agent or tier team not configured.

        contact_segments: the contact's market segment codes (retail / project). When
        non-empty, the tier's round-robin pool is filtered to members serving those
        segments (members with a matching segment OR no segment set = serves all), and
        the cursor is segment-scoped. Empty / None -> unfiltered (round-robin over the
        whole tier team on the legacy cursor) - byte-identical to prior behaviour.

        brand_code: the tracker's stamped brand. The tier TEAM is the same whatever the
        brand is; the brand narrows the pool INSIDE it to members tagged with that
        brand plus untagged members, so a Mocha conversation escalating to tier 2
        reaches whoever on that tier handles Mocha. Nobody tagged -> the whole tier
        team, i.e. the pre-brand behaviour.
        """
        from app.services.user_service import AccessAgentService
        from app.models.access import AccessAgent

        agent_svc = AccessAgentService(self.db)

        if agent_id_override:
            agent_id = str(agent_id_override)
            # Resolve display code for error messages
            _agent = self.db.query(AccessAgent).filter(AccessAgent.id == agent_id).first()
            agent_code = _agent.code if _agent else agent_id
        elif agent_code_override and agent_code_override.strip():
            agent_code = agent_code_override.strip()
            agent_id = agent_svc.get_agent_id_by_code(agent_code)
        else:
            agent_code = self.ENTITY_TYPE_TO_AGENT_CODE.get(
                (source_entity_type or "").strip().lower()
            ) or "complaint"
            agent_id = agent_svc.get_agent_id_by_code(agent_code)

        if not agent_id:
            raise handle_validation_error(
                f"No access agent found with code '{agent_code}'. Cannot resolve escalation assignee. "
                "Create the Access Agent and assign tier teams (tier 1, 2, 3)."
            )
        team_id = agent_svc.get_team_id_by_tier(
            agent_id,
            target_tier,
            team_set_code=team_set_code,
            company_id=company_id,
        )
        if not team_id:
            suffix = (
                f" in team set '{team_set_code}'"
                if team_set_code
                else ""
            )
            raise handle_validation_error(
                f"No team assigned for agent '{agent_code}' with tier {target_tier}{suffix}. "
                f"Add a Team Assignment with Tier = {target_tier} for this agent."
            )
        # Opt-in filters: both default to "no filter", so a caller that knows about
        # neither segments nor brands gets the byte-identical pre-filter RR path.
        assignee = agent_svc.get_next_assignee(
            agent_id, team_id, contact_segments or None, brand_code=brand_code
        )
        if not assignee:
            raise handle_validation_error(
                f"No assignee in team for agent '{agent_code}' tier {target_tier}. Ensure the team has members."
            )
        # Coverage redirect: if the RR-resolved assignee is covered (on leave), route
        # to their coverer. The RR cursor already advanced to the covered user above
        # (fairness, decision 4); we only swap the returned dict. One hop. This is the
        # single resolution point for conversation-SLA escalation AND conversation-SLA
        # initial assignment (create_tracking's RR branch), so both redirect here.
        from app.services.coverage_subscription_service import (
            resolve_assignee_with_coverage,
        )

        assignee, _covered_for_id = resolve_assignee_with_coverage(self.db, assignee)
        return assignee

    def escalate_tracking(
        self,
        respond_contact_id: str,
        policy_id: str,
        current_tier: int,
        escalation_reason: Optional[str] = None,
        assigned_to_id: Optional[str] = None,
        assigned_to_respond_user_id: Optional[str] = None,
        tracking_id: Optional[str] = None,
    ) -> ConversationSLATracking:
        """
        Escalate a conversation SLA tracking: set new tier, timestamps, and recalculate
        due_at (response) and due_at_resolution from policy tier KPIs. Creates an
        escalation event log. Called by external system via integration API and the UI
        escalate action.

        escalation_reason None → auto-escalation default mentioning from_tier (signal-only
        callers like the scheduled n8n runner don't know the tier before the call).
        assigned_to_id / assigned_to_respond_user_id: the new tier assignee, applied BEFORE
        the event log is written so the escalation log records the new assignee, not the
        previous tier's (the caller resolves the assignee from the target tier first).

        tracking_id (AC-F1, multi-open consumer audit): when given, escalates THAT exact
        row via a direct id lookup. Callers that already resolved a specific ticket (the
        UI escalate route, keyed by tracking_id in the URL; the n8n integration route,
        which resolves "the" tracking before calling here) MUST pass it - a contact can
        now hold several open tickets on the same policy, so re-resolving by
        (respond_contact_id, policy_id) here can silently pick a DIFFERENT sibling than
        the one the caller intended (this was a real bug: the UI escalate action could
        escalate the wrong ticket for a contact with 2 open tickets on the same policy).
        Omit only for legacy/back-compat callers that never had an id (none remain in
        this codebase as of this audit, but the contact+policy fallback is kept for any
        external caller still on the old contract).
        """
        from datetime import timedelta

        if tracking_id:
            tracking = self.get_tracking(str(tracking_id), load_event_logs=False)
        else:
            tracking = self.get_tracking_by_contact_and_policy(respond_contact_id, policy_id)
        if not tracking:
            raise handle_not_found(
                "Conversation SLA tracking",
                f"tracking_id={tracking_id}, respond_contact_id={respond_contact_id}, policy_id={policy_id}",
            )
        if bool(getattr(tracking, "is_resolved", False)):
            raise handle_validation_error(
                "Cannot escalate a resolved conversation SLA tracking."
            )

        tier = self._resolve_tier_with_clamp(tracking.policy_id, current_tier)
        if not tier:
            raise handle_validation_error(
                f"SLA policy tier {current_tier} not found for policy {tracking.policy_id}."
            )

        from_tier = tracking.current_tier
        now_utc = _now_utc()
        initiated_at_raw = getattr(tracking, "initiated_at", None)
        initiated_at_utc = _to_aware_utc(
            initiated_at_raw if isinstance(initiated_at_raw, datetime) else None
        )
        response_hours_raw = getattr(tier, "response_hours", None)
        response_hours = float(response_hours_raw) if response_hours_raw is not None else 24.0
        resolution_hours_raw = getattr(tier, "resolution_hours", None)
        resolution_hours = float(resolution_hours_raw) if resolution_hours_raw is not None else 24.0

        reason = (escalation_reason or "").strip() or (
            f"Auto-escalation: tier {from_tier} response due time breached"
        )

        # The tier clock starts when work can actually begin (next working-window
        # open); escalated_at keeps the true escalation instant for audit.
        clock_start = _working_clock_start(self.db, now_utc)
        setattr(tracking, "current_tier", current_tier)
        setattr(tracking, "current_tier_started_at", clock_start)
        setattr(tracking, "escalated_at", now_utc)
        setattr(tracking, "escalation_reason", reason)
        setattr(tracking, "due_at", _working_due(self.db, clock_start, response_hours))
        # On escalation, due_at_resolution = clock start + resolution_hours (working hours)
        setattr(tracking, "due_at_resolution", _working_due(self.db, clock_start, resolution_hours))
        # Snapshot the escalated-FROM owner BEFORE the assignee is overwritten so the
        # escalation event log records who missed at the prior tier (banner link).
        prev_assigned_to_id = getattr(tracking, "assigned_to_id", None)
        if assigned_to_id is not None:
            setattr(tracking, "assigned_to_id", str(assigned_to_id))
            setattr(
                tracking,
                "assigned_to",
                str(assigned_to_respond_user_id)
                if assigned_to_respond_user_id is not None
                else None,
            )

        self.db.flush()
        self.create_event_log(
            ConversationSLAEventLogCreate(
                sla_tracking_id=str(getattr(tracking, "id")),
                event_type="escalation",
                from_tier=(
                    int(from_tier)
                    if isinstance(from_tier, (int, str, float))
                    else None
                ),
                to_tier=current_tier,
                event_at=now_utc,
                reason=reason,
                assigned_to_id=(
                    str(getattr(tracking, "assigned_to_id"))
                    if getattr(tracking, "assigned_to_id", None) is not None
                    else None
                ),
                from_assigned_to_id=(
                    str(prev_assigned_to_id)
                    if prev_assigned_to_id is not None
                    else None
                ),
                due_at=(
                    _to_aware_utc(getattr(tracking, "due_at"))
                    if isinstance(getattr(tracking, "due_at", None), datetime)
                    else None
                ),
            )
        )
        self.db.refresh(tracking)
        # Escalation changes the owner/tier - void any pending takeover (AC-VOID-3).
        from app.services.sla_takeover_service import SlaTakeoverService

        SlaTakeoverService(self.db).void_for_tracking(str(tracking.id), "escalated")
        # Both tiers refetch: the task left the breaching owner's list and
        # landed on the next tier's (AC-K3).
        self._publish_conversation_event(
            tracking,
            conversation_event_bus.EVENT_TICKET_UPDATED,
            user_ids=[prev_assigned_to_id, getattr(tracking, "assigned_to_id", None)],
        )
        return tracking

    # ---- Extend resolution deadline (PLAN-sla-extend-deadline) ---------------

    def _extension_base(self, tracking: ConversationSLATracking) -> datetime:
        """Base point for an extension = the current due_at_resolution (naive UTC).

        Always relative to the existing deadline, NEVER `now` - extending an overdue
        row adds working days onto its original due (e.g. due 13/05 + 1 wd = 14/05),
        not onto today. The increment is still strictly after the current due (days>=1
        / target_date guard), so 'can only extend' holds even when the result is still
        in the past."""
        now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
        due = getattr(tracking, "due_at_resolution", None)
        if not isinstance(due, datetime):
            return now_naive
        return due.replace(tzinfo=None) if due.tzinfo is not None else due

    def compute_extension(
        self,
        tracking: ConversationSLATracking,
        *,
        days: Optional[int] = None,
        target_date: Optional[str] = None,
    ) -> tuple[datetime, int]:
        """Resolve the new resolution deadline for an extension. Returns
        (new_due_at_resolution naive-UTC, working_days_added).

        Exactly one of ``days`` / ``target_date`` must be given. Base point is
        max(current due, now) so the result is always in the future; the current
        due's time-of-day is preserved (matches add_working_days_from_hours / the
        rest of the SLA module). ``target_date`` must yield a new due strictly after
        the current due, else a validation error.
        """
        from app.services.calendar_service import CalendarService
        from app.services.error_handler import AppException

        def _invalid(msg: str) -> AppException:
            # 422 to match the gate/reason checks (_assert_can_extend, reason) so the
            # whole extend/preview contract returns a single validation status.
            return AppException(status_code=422, message=msg, code="VALIDATION_ERROR")

        if (days is None) == (target_date is None):
            raise _invalid("Provide exactly one of 'days' or 'target_date'.")

        base = self._extension_base(tracking)
        cal = CalendarService(self.db)

        current_due_raw = getattr(tracking, "due_at_resolution", None)
        current_due = (
            (current_due_raw.replace(tzinfo=None) if current_due_raw.tzinfo is not None else current_due_raw)
            if isinstance(current_due_raw, datetime)
            else None
        )

        if days is not None:
            try:
                n = int(days)
            except (TypeError, ValueError):
                raise _invalid("Working days must be a whole number >= 1.")
            if n < 1:
                raise _invalid("Working days must be at least 1 (cannot reduce or no-op).")
            new_due = cal.add_working_days(base, n)
            if new_due is None:
                raise _invalid("Could not compute the new deadline.")
            # add_working_days preserves the base's time-of-day; on an overdue row the
            # base is `now`, so re-apply the current due's wall-clock time to keep the
            # deadline's time-of-day stable (UAC-8 / decision 4). Adds >=1 working day,
            # so the result is still strictly after the current due.
            if current_due is not None:
                new_due = datetime.combine(new_due.date(), current_due.time())
            return new_due, n

        # target_date mode: parse the date, apply the current due's time-of-day,
        # validate strictly after current due, derive the working-day count.
        try:
            d = date.fromisoformat(str(target_date).strip())
        except (TypeError, ValueError):
            raise _invalid("target_date must be an ISO date (YYYY-MM-DD).")
        time_of_day = (current_due or base).time()
        new_due = datetime.combine(d, time_of_day)
        if current_due is not None and new_due <= current_due:
            raise _invalid(
                "The chosen date must be after the current resolution deadline (can only extend)."
            )
        # Working days from base (max(current due, now)) up to the chosen date.
        working_days = cal.count_working_days(base, new_due)
        if working_days < 1:
            raise _invalid(
                "The chosen date does not add any working days to the deadline."
            )
        return new_due, working_days

    def evaluate_extension_warnings(
        self,
        tracking: ConversationSLATracking,
        policy,
        added_days: int,
    ) -> list[str]:
        """Soft-limit warnings for an extension (config-driven, never blocking).
        Null caps -> no warning. Mirrors the three sla_policies limit columns."""
        warnings: list[str] = []
        if policy is None:
            return warnings

        per_request = getattr(policy, "max_extension_days_per_request", None)
        if per_request is not None and added_days > int(per_request):
            warnings.append(
                f"This extension adds {added_days} working day(s), exceeding the "
                f"per-request limit of {int(per_request)}."
            )

        count_cap = getattr(policy, "max_extension_count", None)
        current_count = int(getattr(tracking, "extension_count", 0) or 0)
        if count_cap is not None and (current_count + 1) > int(count_cap):
            warnings.append(
                f"This would be extension #{current_count + 1}, exceeding the "
                f"limit of {int(count_cap)} extension(s) for this task."
            )

        total_cap = getattr(policy, "max_extension_days_total", None)
        current_total = float(getattr(tracking, "extension_days_total", 0) or 0)
        if total_cap is not None and (current_total + added_days) > float(total_cap):
            warnings.append(
                f"Cumulative extension would reach {current_total + added_days:g} working "
                f"day(s), exceeding the total limit of {float(total_cap):g}."
            )
        return warnings

    def _assert_can_extend(self, tracking: ConversationSLATracking, actor_user_id: str) -> None:
        """Gating for extend: assignee-only by default (conversation AND form SLA),
        unresolved, has a resolution deadline. 403 for non-assignee, 422 otherwise.

        Handling-lock override (PLAN-form-handling-lock Q5a): when the tracker is an
        escalated FORM tracker AND the lock is enabled for its type, extend is gated on
        the current HANDLER instead of the assignee (the assignee may not hold the lock).
        Not escalated / flag off / conversation SLA -> unchanged assignee-only.
        """
        from app.services.error_handler import AppException

        s_type = str(getattr(tracking, "source_entity_type", "") or "")
        escalated = int(getattr(tracking, "current_tier", 1) or 1) > 1
        handler_gated = False
        if escalated and s_type:
            from app.services.form_sla_service import FORM_SLA_TYPES

            if s_type in FORM_SLA_TYPES:
                from app.services.handling_lock_service import is_handling_lock_enabled

                handler_gated = is_handling_lock_enabled(self.db, s_type)

        if handler_gated:
            handler = getattr(tracking, "handled_by_id", None)
            is_holder = handler is not None and str(handler) == str(actor_user_id)
            if not is_holder:
                # Mirror the CTA guard: an admin/superadmin may act on an UNCLAIMED
                # escalated tracker; a held lock blocks even admin.
                from app.services.handling_lock_service import _actor_is_admin

                admin_on_unclaimed = handler is None and _actor_is_admin(
                    self.db, str(actor_user_id)
                )
                if not admin_on_unclaimed:
                    raise AppException(
                        status_code=403,
                        message="Only the current handler can extend this SLA deadline.",
                        code="FORBIDDEN",
                    )
        else:
            assignee = getattr(tracking, "assigned_to_id", None)
            if assignee is None or str(assignee) != str(actor_user_id):
                raise AppException(
                    status_code=403,
                    message="Only the current assignee can extend this SLA deadline.",
                    code="FORBIDDEN",
                )
        if bool(getattr(tracking, "is_resolved", False)):
            raise AppException(
                status_code=422,
                message="Cannot extend a resolved SLA task.",
                code="VALIDATION_ERROR",
            )
        if getattr(tracking, "due_at_resolution", None) is None:
            raise AppException(
                status_code=422,
                message="This SLA task has no resolution deadline to extend.",
                code="VALIDATION_ERROR",
            )

    def extend_tracking(
        self,
        tracking_id: str,
        actor_user_id: str,
        *,
        days: Optional[int] = None,
        target_date: Optional[str] = None,
        reason: str,
    ) -> ConversationSLATracking:
        """Push out the resolution deadline (due_at_resolution) for the current
        assignee. Recomputes authoritatively (ignores any client-previewed value),
        bumps the denormalized counters, resets the reminder cycle, writes one
        'extend' event log, then best-effort notifies the next escalation tier.
        Works for both conversation and form SLA rows (shared table).
        """
        from app.services.error_handler import AppException

        if not reason or not str(reason).strip():
            raise AppException(
                status_code=422,
                message="A reason is required to extend the deadline.",
                code="VALIDATION_ERROR",
            )
        reason = str(reason).strip()

        tracking = self.get_tracking(tracking_id, load_event_logs=False)
        self._assert_can_extend(tracking, actor_user_id)

        old_due = getattr(tracking, "due_at_resolution", None)
        new_due, added_days = self.compute_extension(
            tracking, days=days, target_date=target_date
        )

        tier = int(getattr(tracking, "current_tier", 0) or 0)
        setattr(tracking, "due_at_resolution", new_due)
        setattr(
            tracking,
            "extension_count",
            int(getattr(tracking, "extension_count", 0) or 0) + 1,
        )
        setattr(
            tracking,
            "extension_days_total",
            float(getattr(tracking, "extension_days_total", 0) or 0) + added_days,
        )
        # Fresh reminder cycle for the new deadline (UAC-22).
        # reminder_count / last_reminder_at live on the event log; the tracker has no
        # such columns, so the reset is reflected by resetting the latest reminder
        # bookkeeping on the tracker if present. Columns guarded with hasattr so this
        # stays correct whether or not the tracker carries reminder state.
        if hasattr(tracking, "reminder_count"):
            setattr(tracking, "reminder_count", 0)
        if hasattr(tracking, "last_reminder_at"):
            setattr(tracking, "last_reminder_at", None)
        self.db.commit()
        self.db.refresh(tracking)

        # One 'extend' event log. Wrap naive-UTC datetimes with _to_aware_utc so
        # create_event_log (which treats naive as Malaysia time, -8h) stores them
        # correctly. duration = working days added.
        try:
            self.create_event_log(
                ConversationSLAEventLogCreate(
                    sla_tracking_id=str(getattr(tracking, "id")),
                    event_type="extend",
                    from_tier=tier if tier >= 1 else None,
                    to_tier=tier if tier >= 1 else None,
                    event_at=_now_utc(),
                    from_time=(
                        _to_aware_utc(old_due)
                        if isinstance(old_due, datetime)
                        else None
                    ),
                    due_at=_to_aware_utc(new_due),
                    duration=added_days,
                    reason=reason,
                    assigned_to_id=(
                        str(getattr(tracking, "assigned_to_id"))
                        if getattr(tracking, "assigned_to_id", None) is not None
                        else None
                    ),
                    trigger="manual",
                    triggered_by_id=str(actor_user_id),
                )
            )
        except Exception as e:  # noqa: BLE001 - mutation already committed
            import logging

            logging.getLogger(__name__).warning(
                "extend event log failed for %s: %s", getattr(tracking, "id", "?"), e
            )

        self._audit_form_extension(
            tracking,
            actor_user_id=actor_user_id,
            old_due=old_due,
            new_due=new_due,
            added_days=added_days,
            tier=tier,
            reason=reason,
        )

        # Best-effort: notify the NEXT escalation tier only (notify-only - no tier /
        # clock mutation). Never raise (must not 500 a successful extend).
        self._notify_next_tier_deadline_extended(tracking, reason=reason)
        # The resolve-by countdown on the widget row just moved (AC-K3). Form
        # rows share this method and are filtered out by the publisher.
        self._publish_conversation_event(
            tracking,
            conversation_event_bus.EVENT_TICKET_UPDATED,
            user_ids=[getattr(tracking, "assigned_to_id", None)],
        )
        return tracking

    def _audit_form_extension(
        self,
        tracking: ConversationSLATracking,
        *,
        actor_user_id: str,
        old_due: Optional[datetime],
        new_due: datetime,
        added_days: int,
        tier: int,
        reason: str,
    ) -> None:
        """One audit row on the FORM for a form-SLA extension (#1326).

        The extension lives on the tracker, so the form's audit trail never showed it
        and the owner looked for it there. Same shape as the form-action audit
        (`form_action_service._audit`): an UPDATE against the form whose description
        is what the AuditTrail renders. Conversation SLA rows have no form to audit.
        Best-effort: the extension is already committed.
        """
        from app.services.form_sla_service import FORM_SLA_TYPES, _fmt_due

        s_type = str(getattr(tracking, "source_entity_type", "") or "")
        s_id = getattr(tracking, "source_entity_id", None)
        if s_type not in FORM_SLA_TYPES or s_id is None:
            return
        try:
            from app.services.audit_service import log_audit

            tier_part = f", tier {tier}" if tier >= 1 else ""
            log_audit(
                self.db,
                entity_type=s_type,
                entity_id=str(s_id),
                action="UPDATE",
                user_id=str(actor_user_id),
                description=(
                    f"SLA deadline extended to {_fmt_due(new_due)} "
                    f"(+{added_days} working days{tier_part}): {reason}"
                ),
                old_values={"sla_due_at_resolution": _fmt_due(old_due)},
                new_values={"sla_due_at_resolution": _fmt_due(new_due)},
            )
            self.db.commit()
        except Exception as e:  # noqa: BLE001 - mutation already committed
            self.db.rollback()
            import logging

            logging.getLogger(__name__).warning(
                "extend audit failed for %s: %s", getattr(tracking, "id", "?"), e
            )

    def _notify_next_tier_deadline_extended(
        self, tracking: ConversationSLATracking, *, reason: str
    ) -> None:
        """Notify the higher tiers that the deadline was extended.

        Fans UP every tier above the current one (current+1 .. 3, capped at tier 3)
        whose ``AgentTeam`` row has ``notify_on_extension = true`` - so the parent AND
        the grandparent tier are reached when both opt in, while an admin can untick a
        tier to silence it. The tier chain is the agent team config
        (``AgentTeam(agent_id, tier, team_set_code, team_id)``), not parent_team_id.

        Per-tier recipient = that tier team's next round-robin assignee, resolved by
        PEEKING the cursor (never advancing it - no tier/clock/RR mutation on extend).
        Recipients are deduped so a shared member isn't double-sent. No higher tier
        configured / opted-in -> skip silently. Never raises.
        """
        import logging

        log = logging.getLogger(__name__)
        try:
            from app.services.form_sla_service import FORM_SLA_TYPES
            from app.services.user_service import AccessAgentService

            from_tier = int(getattr(tracking, "current_tier", 0) or 0)
            if from_tier < 1:
                return
            target_tier = from_tier + 1
            if target_tier > 3:
                return  # already top tier -> no tier above

            s_type = getattr(tracking, "source_entity_type", None)
            agent_svc = AccessAgentService(self.db)

            # Resolve the agent that owns this row's tier chain. Form: the row's agent.
            # Conversation: the row's agent override, else the entity-type's agent.
            if s_type in FORM_SLA_TYPES:
                agent_id = (
                    str(getattr(tracking, "agent_id"))
                    if getattr(tracking, "agent_id", None) is not None
                    else None
                )
            else:
                agent_id_override = getattr(tracking, "agent_id", None) or None
                if agent_id_override:
                    agent_id = str(agent_id_override)
                else:
                    agent_code = self.ENTITY_TYPE_TO_AGENT_CODE.get(
                        (s_type or "").strip().lower()
                    ) or "complaint"
                    agent_id = agent_svc.get_agent_id_by_code(agent_code)
            if not agent_id:
                return
            team_set_code = getattr(tracking, "team_set_code", None) or None

            notified: set[str] = set()
            for tier in range(target_tier, 4):
                res = agent_svc.get_tier_team_and_notify(
                    agent_id,
                    tier,
                    team_set_code=team_set_code,
                    company_id=_tracking_company_id(tracking),
                )
                if not res:
                    continue  # no team configured at this tier
                tier_team_id, notify_flag = res
                if not notify_flag:
                    continue  # this tier opted out of extension notifications
                _last_id, next_id = agent_svc._peek_next_assignee(agent_id, tier_team_id)
                if not next_id or str(next_id) in notified:
                    continue
                notified.add(str(next_id))
                self._notify_user_deadline_extended(
                    tracking, recipient_user_id=str(next_id), reason=reason
                )
        except Exception as e:  # noqa: BLE001 - best-effort, extend already committed
            log.warning(
                "deadline-extended notify failed for %s: %s",
                getattr(tracking, "id", "?"),
                e,
            )

    def _notify_user_deadline_extended(
        self,
        tracking: ConversationSLATracking,
        *,
        recipient_user_id: str,
        reason: str,
    ) -> None:
        """Build + send the deadline_extended notification to a specific user
        (the next-tier assignee), reusing the same WhatsApp data builder as the
        SLA assignment/escalation path. In-app always; email/WhatsApp gated by the
        recipient's notify_*_on_deadline_extended toggles."""
        from app.services.notification_service import NotificationService
        from app.services.form_sla_service import (
            build_sla_whatsapp_data,
            _resolve_entity_number,
            _full_form_link,
            _fmt_due,
            FORM_SLA_TYPES,
        )
        from app.config import settings

        s_type = str(getattr(tracking, "source_entity_type", "") or "")
        s_id = str(getattr(tracking, "source_entity_id", "") or "")
        number = (
            _resolve_entity_number(self.db, s_type, s_id)
            or (s_type.replace("_", " ").title() if s_type else "an SLA task")
        )
        new_due_str = _fmt_due(getattr(tracking, "due_at_resolution", None))
        base_url = (getattr(settings, "frontend_base_url", None) or "").strip().rstrip("/")
        if s_type in FORM_SLA_TYPES:
            link = _full_form_link(s_type, s_id)
        else:
            link = (
                f"{base_url}/sla-management/conversation-sla-tracking/{tracking.id}"
                if base_url
                else ""
            )

        title = f"SLA deadline extended: {number}"
        body = (
            f"The resolution deadline for {number} was extended"
            + (f" to {new_due_str}" if new_due_str else "")
            + f". Reason: {reason}."
        )
        if link:
            body += f"\n\nOpen: {link}"

        data = {
            "tracking_id": str(tracking.id),
            "source_entity_type": tracking.source_entity_type,
            "source_entity_id": tracking.source_entity_id,
            **build_sla_whatsapp_data(
                self.db,
                tracking,
                str(recipient_user_id),
                body,
                use_case="sla_deadline_extended",
                reason=reason,
            ),
        }
        NotificationService(self.db).create_with_channel_preferences(
            user_id=str(recipient_user_id),
            type="conversation_sla" if s_type not in FORM_SLA_TYPES else "form_sla",
            title=title,
            body=body,
            data=data,
            source_entity_type=(
                "form_sla_tracking" if s_type in FORM_SLA_TYPES else "conversation_sla_tracking"
            ),
            source_entity_id=str(tracking.id),
            # Suffix with the extension occurrence so EACH extend re-notifies (the
            # dedup key is (user, source_type, source_id, event_type)). Without the
            # suffix the 2nd+ extension on the same tracking would dedup to the first
            # and send nothing. Retrying the SAME extension stays idempotent (same
            # count). The email-key resolver strips the ":N" so it still maps to the
            # sla_deadline_extended event.
            event_type=f"deadline_extended:{int(getattr(tracking, 'extension_count', 0) or 0)}",
            send_in_app=True,
            send_email=True,
            send_whatsapp=True,
            email_pref_attr="notify_email_on_deadline_extended",
            whatsapp_pref_attr="notify_whatsapp_on_deadline_extended",
        )

    def list_due_escalations(self) -> list[dict]:
        """Work-list for the scheduled escalation runner (n8n).

        Conversation-SLA rows (scope filter - never form rows) that are unresolved, in
        breach, and still escalatable (tier 1 or 2; tier 3 has nowhere to go). Split-clock
        breach rule - the response clock stops on response (compute_tracking_timings), so
        escalation must not fire on a stopped clock:
      - not responded → breach when due_at (response deadline) passes
      - responded     → breach when due_at_resolution passes; never before
        Each item carries everything the runner needs downstream - contact phone and
        Respond.io id included - so n8n needs no SQL nodes, plus `breach_type`
        ("response" | "resolution") for message templating. Datetime columns store naive
        UTC; compared against naive-UTC now and serialized as aware-UTC ISO.
        """
        from sqlalchemy import and_
        from app.models.access import RespondContact

        now_naive = _now_utc().replace(tzinfo=None)
        rows = (
            self.db.query(ConversationSLATracking, RespondContact)
            .outerjoin(
                RespondContact,
                RespondContact.id == ConversationSLATracking.respond_contact_id,
            )
            .filter(
                conversation_tracking_scope(),
                ConversationSLATracking.is_resolved.is_(False),
                ConversationSLATracking.current_tier.in_([1, 2]),
                or_(
                    and_(
                        ConversationSLATracking.is_responded.is_(False),
                        ConversationSLATracking.due_at.isnot(None),
                        ConversationSLATracking.due_at < now_naive,
                    ),
                    and_(
                        ConversationSLATracking.is_responded.is_(True),
                        ConversationSLATracking.due_at_resolution.isnot(None),
                        ConversationSLATracking.due_at_resolution < now_naive,
                    ),
                ),
            )
            .order_by(ConversationSLATracking.due_at.asc())
            .all()
        )

        items: list[dict] = []
        for tracking, contact in rows:
            due_at_utc = _to_aware_utc(getattr(tracking, "due_at", None))
            due_at_resolution_utc = _to_aware_utc(getattr(tracking, "due_at_resolution", None))
            is_responded = bool(getattr(tracking, "is_responded", False))
            items.append(
                {
                    "tracking_id": str(getattr(tracking, "id")),
                    "respond_contact_id": (
                        str(getattr(tracking, "respond_contact_id"))
                        if getattr(tracking, "respond_contact_id", None)
                        else None
                    ),
                    "policy_id": (
                        str(getattr(tracking, "policy_id"))
                        if getattr(tracking, "policy_id", None)
                        else None
                    ),
                    "current_tier": getattr(tracking, "current_tier", None),
                    "breach_type": "resolution" if is_responded else "response",
                    "is_responded": is_responded,
                    "due_at": due_at_utc.isoformat() if due_at_utc else None,
                    "due_at_resolution": (
                        due_at_resolution_utc.isoformat() if due_at_resolution_utc else None
                    ),
                    "message_id": getattr(tracking, "message_id", None),
                    "source_entity_type": getattr(tracking, "source_entity_type", None),
                    "team_set_code": getattr(tracking, "team_set_code", None),
                    "assigned_to_id": (
                        str(getattr(tracking, "assigned_to_id"))
                        if getattr(tracking, "assigned_to_id", None)
                        else None
                    ),
                    "assigned_to_respond_user_id": getattr(tracking, "assigned_to", None),
                    "phone_number": getattr(contact, "phone_number", None) if contact else None,
                    "respond_io_id": getattr(contact, "respond_io_id", None) if contact else None,
                    "contact_name": getattr(contact, "name", None) if contact else None,
                }
            )
        return items

    def get_existing_assignee_for_contact_phone(self, contact_phone: str) -> Optional[dict]:
        """
        If there is an OPEN conversation SLA tracking for this contact phone that
        already has an assignee, return that user's info (id, email, name,
        respond_user_id). Otherwise return None.
        Used by next-assignee API to avoid reassigning conversations that are already assigned.

        AC-F1: not currently wired into any route (kept for callers that may want a
        single "who owns this contact's thread" answer). A contact can hold several
        assigned open tickets at once, so this is a MOST-RECENT-OPEN pick - the
        explicit ``order_by`` below (matching ``get_preferred_tracking_for_contact``)
        replaces what used to be an undocumented, unordered ``.first()`` over a
        possibly-multi-row result.

        A resolved row is excluded outright (owner ruling R5, 22 Sep 2026, fix
        round 1 / S2): resolve keeps ``assigned_to_id`` for audit now, so without
        this filter a contact whose only ticket is resolved would read back its
        last resolver as the "existing assignee", the same bug ``next_assignee.
        _tracking_is_assigned`` had (AC-KA-11).
        """
        from sqlalchemy.orm import joinedload
        from app.models.access import RespondContact
        from app.models.user import User

        phone = (contact_phone or "").strip()
        if not phone:
            return None
        contact = self.db.query(RespondContact).filter(RespondContact.phone_number == phone).first()
        if not contact:
            return None
        tracking = (
            self.db.query(ConversationSLATracking)
            .options(joinedload(ConversationSLATracking.assigned_user))
            .filter(
                ConversationSLATracking.respond_contact_id == contact.id,
                ConversationSLATracking.assigned_to_id.isnot(None),
                ConversationSLATracking.is_resolved.is_(False),
                conversation_tracking_scope(),
            )
            .order_by(ConversationSLATracking.created_at.desc())
            .first()
        )
        if tracking is None or getattr(tracking, "assigned_to_id", None) is None:
            return None
        user = tracking.assigned_user
        if not user:
            user = self.db.query(User).filter(User.id == tracking.assigned_to_id).first()
        if not user:
            return None
        return {
            "id": user.id,
            "email": user.email,
            "name": user.name or user.email,
            "respond_user_id": user.respond_user_id,
        }

    def sync_assignee_from_respond(self, tracking_id: str) -> dict:
        """
        Fetch contact from Respond.io by phone, get assignee.id, match to user by respond_user_id,
        and update tracking assigned_to if different. Uses existing Respond.io config (base URL, API key).

        AC-F2 (multi-open consumer audit): deprecated no-op for conversation-family
        rows. CRM is now the assignee authority per-ticket (each open ticket has its
        own assignee); pulling "the" Respond.io conversation assignee back onto a
        SPECIFIC ticket is meaningless once a contact can hold several open tickets
        against one shared Respond conversation - there is no longer a 1:1 mapping to
        sync. Kept for form-SLA rows (unaffected; they never shared this ambiguity)
        and kept as a route (not retired to 404) so an existing caller gets a clear
        "deprecated, nothing changed" response instead of breaking.
        """
        import json
        import logging
        from app.models.user import User
        from app.services.integration_service import RespondClient, IntegrationLogService
        from app.schemas.integration import IntegrationLogCreate

        logger = logging.getLogger(__name__)
        tracking = self.get_tracking(tracking_id)

        from app.services.form_sla_service import FORM_SLA_TYPES

        if getattr(tracking, "source_entity_type", None) not in FORM_SLA_TYPES:
            return {
                "updated": False,
                "deprecated": True,
                "message": (
                    "Sync assignee from Respond.io is deprecated for conversation "
                    "tickets: CRM is now the assignee authority (per-ticket, "
                    "multi-open). No changes made."
                ),
            }

        phone = None
        if tracking.contact:
            phone = (getattr(tracking.contact, "phone_number", None) or "").strip()
        if not phone:
            raise handle_validation_error("No contact phone for this conversation SLA tracking; cannot sync assignee from Respond.io.")

        log_service = IntegrationLogService(self.db)
        endpoint_path = f"/v2/contact/phone:{phone}"

        try:
            client = RespondClient()
            payload = client.get_contact_by_phone(phone)
        except ValueError as e:
            logger.warning("Respond.io not configured or error: %s", e)
            log_service.create_integration_log(
                IntegrationLogCreate(
                    integration_channel="respond_io",
                    business_table="conversation_sla_tracking",
                    business_id=tracking_id,
                    direction="outbound",
                    endpoint=endpoint_path,
                    http_method="GET",
                    status="failed",
                    error_message=str(e),
                ),
                request_payload_dict={"action": "sync_assignee", "phone": phone},
            )
            raise handle_validation_error(f"Respond.io API is not configured or error: {e!s}")
        except Exception as e:
            logger.exception("Respond.io get_contact_by_phone failed for tracking %s", tracking_id)
            resp_payload = None
            response_obj = getattr(e, "response", None)
            response_text = getattr(response_obj, "text", None)
            if isinstance(response_text, str) and response_text:
                resp_payload = response_text[:2000] if len(response_text) > 2000 else response_text
            log_service.create_integration_log(
                IntegrationLogCreate(
                    integration_channel="respond_io",
                    business_table="conversation_sla_tracking",
                    business_id=tracking_id,
                    direction="outbound",
                    endpoint=endpoint_path,
                    http_method="GET",
                    status="failed",
                    error_message=str(e),
                    response_payload=resp_payload,
                ),
                request_payload_dict={"action": "sync_assignee", "phone": phone},
            )
            raise

        log_service.create_integration_log(
            IntegrationLogCreate(
                integration_channel="respond_io",
                business_table="conversation_sla_tracking",
                business_id=tracking_id,
                direction="outbound",
                endpoint=endpoint_path,
                http_method="GET",
                status="success",
                response_payload=json.dumps(payload, indent=2),
            ),
            request_payload_dict={"action": "sync_assignee", "phone": phone},
        )

        assignee = payload.get("assignee")
        if not assignee or assignee.get("id") is None:
            # Keep in sync with Respond.io: set assigned_to to null when there is no assignee there
            self.update_tracking(tracking_id, ConversationSLATrackingUpdate(assigned_to=None))
            return {"updated": True, "message": "Sync successful. No assignee in Respond.io; Assigned To cleared."}

        assignee_id = assignee.get("id")
        assignee_respond_id = str(assignee_id)
        user = self.db.query(User).filter(User.respond_user_id == assignee_respond_id).first()
        if not user:
            return {
                "updated": False,
                "message": f"Sync successful. No user in CRM with respond_user_id '{assignee_respond_id}'; Assigned To unchanged. Link Respond.io user ID in User Management to sync.",
            }

        if str(getattr(tracking, "assigned_to_id", "")) == str(getattr(user, "id")):
            return {"updated": False, "message": "Sync successful. Assignee already in sync."}

        self.update_tracking(tracking_id, ConversationSLATrackingUpdate(assigned_to=assignee_respond_id))
        # Assignee-driven routing correction (see apply_assignee_team_derivation).
        derivation = self.apply_assignee_team_derivation(
            tracking_id, str(user.id), source="sync-assignee"
        )
        tracking = self.get_tracking(tracking_id)
        return {
            "updated": True,
            "message": "Assignee synced from Respond.io.",
            "assigned_to_id": str(user.id),
            "assigned_to": user.name or user.email or assignee_respond_id,
            "routing": derivation,
        }

    def set_assignee_for_tracking(self, tracking_id: str, assignee_respond_user_id: str) -> dict:
        """
        Update the assignee on Conversation SLA Tracking in our system only (no Respond.io call).
        Used by external API: look up tracking by contact phone, then update assigned_to/assigned_to_id.

        After the assignee update, routing is re-derived from the new assignee's team
        membership (apply_assignee_team_derivation): a tier-1 member pulls the tracking to
        their (agent, team_set) at tier 1; a tier-2/3 member of the current team set moves
        the tier to match; otherwise agent/team/tier stay as-is. Tier/team changes restart
        the tier clock and log a 'reassignment' event.

        For resolved trackings, this still updates only assignee fields and keeps resolution status.
        assignee_respond_user_id: Respond.io user id (e.g. 1023495). Use empty string to unassign.
        """
        from app.models.user import User

        tracking = self.get_tracking(tracking_id)
        original_assignee = (
            (tracking.assigned_user.name or tracking.assigned_user.email)
            if tracking.assigned_user else (tracking.assigned_to or None)
        )
        original_assignee_id = getattr(tracking, "assigned_to_id", None)
        original_tier = tracking.current_tier

        assignee_id = (assignee_respond_user_id or "").strip()
        user = None
        if assignee_id:
            user = self.db.query(User).filter(User.respond_user_id == assignee_id).first()

        update_kw: dict = {"assigned_to": assignee_id if assignee_id else None}
        self.update_tracking(tracking_id, ConversationSLATrackingUpdate(**update_kw))

        # Assignee-driven routing correction: re-derive agent/team(/tier) from the new
        # assignee's team membership so escalation follows the correct team afterwards.
        # Unassign carries no routing signal - skip derivation.
        derivation = None
        if user is not None:
            derivation = self.apply_assignee_team_derivation(
                tracking_id, str(user.id), source="conversation-assignee"
            )

        tracking = self.get_tracking(tracking_id)

        # Notify the NEW assignee (in-app + email/WhatsApp per their toggles), same as
        # auto-assign on create. Only on a genuine change to a real user - unassign and
        # re-setting the same person carry no new assignment. Best-effort (never raises);
        # the occurrence-keyed event_type dedups a no-op repeat at the same tier clock.
        new_assignee_id = getattr(tracking, "assigned_to_id", None)
        if new_assignee_id and str(new_assignee_id) != str(original_assignee_id or ""):
            # Per-action occurrence (microsecond) so A→B→A re-notifies: a same-team
            # reassign does NOT restart the tier clock, so the default tier-start key
            # would collide with the earlier A→ notification and dedup the send away.
            self._notify_assignment_on_create(
                tracking, occurrence=int(_now_utc().timestamp() * 1_000_000)
            )

        phone = None
        if tracking.contact:
            phone = (getattr(tracking.contact, "phone_number", None) or "").strip()
        changed_at_utc = _now_utc()
        changed_at_malaysia = changed_at_utc.astimezone(MALAYSIA_TZ)
        assignee_changed_at = f"{changed_at_malaysia.strftime('%Y-%m-%d %H:%M:%S')} +08"
        return {
            "updated": True,
            "message": "Conversation SLA tracking assignee updated.",
            "tracking_id": tracking_id,
            "contact_phone": phone,
            "original_assignee": original_assignee,
            "original_tier": original_tier,
            "current_tier": tracking.current_tier,
            "team_set_code": getattr(tracking, "team_set_code", None),
            "routing": derivation,
            "assigned_to": (user.name or user.email or assignee_id) if user else (assignee_id or None),
            "assigned_to_id": str(user.id) if user else None,
            "assignee_respond_user_id": assignee_id or None,
            "assignee_changed_at": assignee_changed_at,
        }

    def derive_team_for_assignee(
        self,
        user_id: str,
        current_agent_id: Optional[str] = None,
        current_team_set_code: Optional[str] = None,
    ) -> Optional[dict]:
        """
        Resolve the (agent_id, team_set_code, tier, team_id) an assignee belongs to, for
        assignee-driven routing correction (PLAN-sla-assignee-team-derivation).

        Step 1 - tier-1 lookup: the user's tier-1-linked TEAM wins. The membership invariant
        (TeamService/AccessAgentService) is scoped PER TEAM SET: a user belongs to at most
        one tier-1 team per AgentTeam.code, so the tracking's `current_team_set_code` is what
        disambiguates a user who leads a team in two sets. With that context, only the links
        in that set are considered. Without usable context (no team_set_code, or the user
        holds no tier-1 link in it), a user spanning several tier-1 teams is ambiguous: log a
        warning and fall back deterministically rather than abandoning derivation. The same
        team is commonly linked at tier 1 under many agents (shared executive pools), so
        among the candidate links prefer the tracking's current agent (agent stays put, only
        the team set flips); otherwise pick the deterministic first link (code, then
        agent_id) - tier-2/3 configuration is expected to be equivalent across those agents.
        Step 2 - scoped tier-2/3 lookup: otherwise, if the user is in the tier-2/3 team of
        the tracking's current (agent_id, team_set_code), keep agent/team and return the
        matched tier (lowest when in both).
        Step 3 - None: unknown user, or a user with no link the two steps above can match
        (no tier-1 link, and no tier-2/3 link in the tracking's own agent/team set).
        """
        import logging
        from app.models.access import AccessAgent, AgentTeam, TeamMember
        from app.services.form_sla_service import form_sla_agent_codes

        uid = (user_id or "").strip()
        if not uid:
            return None

        # Only CONVERSATION-SLA agents' tier-1 links participate in derivation.
        # Form-SLA agents route via FormSLAConfig stages, so a user may sit in
        # many form tier-1 teams; counting them here would falsely read as
        # "multiple tier-1 teams" and abort derivation (plan decision 3c -
        # conversation scope only). Mirrors the relaxed membership invariant.
        form_codes = form_sla_agent_codes(self.db)
        tier1_q = (
            self.db.query(AgentTeam)
            .join(TeamMember, TeamMember.team_id == AgentTeam.team_id)
            .filter(TeamMember.user_id == uid, AgentTeam.tier == 1)
        )
        if form_codes:
            tier1_q = tier1_q.join(
                AccessAgent, AccessAgent.id == AgentTeam.agent_id
            ).filter(AccessAgent.code.notin_(form_codes))
        tier1_links = tier1_q.order_by(
            AgentTeam.code.asc(), AgentTeam.agent_id.asc()
        ).all()
        if tier1_links:
            # The invariant is per team set, so the tracking's team set is what picks
            # the right tier-1 team for a user who leads one in several sets.
            candidates = tier1_links
            restricted_to_set = False
            if current_team_set_code:
                in_set = [
                    l for l in tier1_links if str(l.code) == str(current_team_set_code)
                ]
                if in_set:
                    candidates = in_set
                    restricted_to_set = True
            distinct_teams = {str(l.team_id) for l in candidates}
            if len(distinct_teams) > 1:
                # Two different situations end up here. Restricted to one team set and
                # STILL spanning several teams means the per-set invariant itself is
                # broken (the data should not allow it). Otherwise the context was just
                # missing or matched no link of the user's, which is expected. Routing
                # has to land somewhere either way, so take the deterministic pick below.
                if restricted_to_set:
                    logging.getLogger(__name__).warning(
                        "User %s has ambiguous tier-1 membership across teams %s WITHIN "
                        "team set '%s'; the per-team-set tier-1 invariant looks violated. "
                        "Falling back deterministically.",
                        uid,
                        sorted(distinct_teams),
                        current_team_set_code,
                    )
                else:
                    logging.getLogger(__name__).warning(
                        "User %s has ambiguous tier-1 membership across teams %s in "
                        "several team sets, and no team set context applied "
                        "(given: %s); falling back deterministically.",
                        uid,
                        sorted(distinct_teams),
                        current_team_set_code or "none",
                    )
            link = next(
                (
                    l
                    for l in candidates
                    if current_agent_id and str(l.agent_id) == str(current_agent_id)
                ),
                candidates[0],
            )
            return {
                "agent_id": str(link.agent_id),
                "team_set_code": str(link.code),
                "tier": 1,
                "team_id": str(link.team_id),
            }

        if current_agent_id and current_team_set_code:
            scoped = (
                self.db.query(AgentTeam)
                .join(TeamMember, TeamMember.team_id == AgentTeam.team_id)
                .filter(
                    TeamMember.user_id == uid,
                    AgentTeam.agent_id == str(current_agent_id),
                    AgentTeam.code == str(current_team_set_code),
                    AgentTeam.tier.in_([2, 3]),
                )
                .order_by(AgentTeam.tier.asc())
                .first()
            )
            if scoped:
                return {
                    "agent_id": str(scoped.agent_id),
                    "team_set_code": str(scoped.code),
                    "tier": int(getattr(scoped, "tier")),
                    "team_id": str(scoped.team_id),
                }
        return None

    def apply_assignee_team_derivation(
        self, tracking_id: str, user_id: str, source: str
    ) -> Optional[dict]:
        """
        After an assignee change, re-derive (agent_id, team_set_code, current_tier) from the
        new assignee's team membership and apply when different.

      - Tier or team change restarts the tier clock (current_tier_started_at = now;
          due_at / due_at_resolution from the policy's matched-tier hours) and writes a
          'reassignment' event log row (including team-only changes at the same tier).
      - Team change advances the round-robin cursor of the new (agent, team) to the
          manually picked assignee so auto-assign continues fairly after them.
      - Returns a change summary dict, or None when no derivation / nothing changed.

        Because derivation now falls back deterministically instead of abandoning on
        ambiguity, invariant-violating legacy data gets its routing rewritten here and
        so has its deadlines moved where it previously kept them; the ambiguity WARNING
        logged by derive_team_for_assignee is the signal that happened.
        """
        from app.models.access import AgentTeamRoundRobinCursor

        from app.services.form_sla_service import FORM_SLA_TYPES

        tracking = self.get_tracking(tracking_id)
        # Resolved trackings clear escalation routing on resolve - never resurrect it.
        if bool(getattr(tracking, "is_resolved", False)):
            return None
        # Form SLA trackings own their routing via FormSLAConfig stages - assignee changes
        # must not flip stage-configured agent/team. Conversation trackings only.
        if (getattr(tracking, "source_entity_type", None) or "") in FORM_SLA_TYPES:
            return None
        current_agent_id = getattr(tracking, "agent_id", None)
        current_team_set = getattr(tracking, "team_set_code", None)
        derived = self.derive_team_for_assignee(
            user_id,
            current_agent_id=str(current_agent_id) if current_agent_id else None,
            current_team_set_code=str(current_team_set) if current_team_set else None,
        )
        if not derived:
            return None

        from_tier = int(getattr(tracking, "current_tier", 0) or 0)
        team_changed = (
            str(current_agent_id or "") != derived["agent_id"]
            or str(current_team_set or "") != derived["team_set_code"]
        )
        tier_changed = from_tier != derived["tier"]
        if not team_changed and not tier_changed:
            return None

        now_utc = _now_utc()
        setattr(tracking, "agent_id", derived["agent_id"])
        setattr(tracking, "team_set_code", derived["team_set_code"])
        setattr(tracking, "current_tier", derived["tier"])
        # brand_code is deliberately NOT touched here: it describes the ENQUIRY (the
        # brand the customer asked about), not who is handling it. Moving the work to
        # another person does not turn a Mocha question into a Cabana one.

        # Restart the tier clock from the matched tier's policy hours. If the tier row is
        # missing (misconfigured policy), keep existing clocks - routing fix still applies.
        # Clamp past the policy's top defined tier rather than fabricate 24h (D7).
        tier_row = self._resolve_tier_with_clamp(tracking.policy_id, derived["tier"])
        if tier_row is not None:
            response_hours = float(getattr(tier_row, "response_hours", None) or 24.0)
            resolution_hours = float(getattr(tier_row, "resolution_hours", None) or 24.0)
            setattr(tracking, "current_tier_started_at", now_utc)
            # Routing-correction clock restart (team flip / misroute fix), not a real
            # SLA escalation - keep calendar-hour math here. Working-hours math applies
            # to the SLA countdown itself (create_tracking + escalate_tracking).
            setattr(tracking, "due_at", now_utc + timedelta(hours=response_hours))
            setattr(tracking, "due_at_resolution", now_utc + timedelta(hours=resolution_hours))

        # Advance the new team's round-robin cursor to the manual pick before the event-log
        # commit so everything lands in one transaction.
        if team_changed:
            cursor = (
                self.db.query(AgentTeamRoundRobinCursor)
                .filter(
                    AgentTeamRoundRobinCursor.agent_id == derived["agent_id"],
                    AgentTeamRoundRobinCursor.team_id == derived["team_id"],
                )
                .with_for_update()
                .first()
            )
            if cursor:
                setattr(cursor, "last_assigned_user_id", str(user_id))
            else:
                self.db.add(
                    AgentTeamRoundRobinCursor(
                        agent_id=derived["agent_id"],
                        team_id=derived["team_id"],
                        last_assigned_user_id=str(user_id),
                    )
                )

        reason_parts = [f"assignee correction via {source}"]
        if team_changed:
            reason_parts.append(
                f"team_set: {current_team_set or ' - '} → {derived['team_set_code']}"
            )
        self.db.flush()
        self.create_event_log(
            ConversationSLAEventLogCreate(
                sla_tracking_id=str(getattr(tracking, "id")),
                event_type="reassignment",
                from_tier=from_tier if from_tier >= 1 else None,
                to_tier=derived["tier"],
                event_at=now_utc,
                reason="; ".join(reason_parts),
                assigned_to_id=str(user_id),
                due_at=(
                    _to_aware_utc(getattr(tracking, "due_at"))
                    if isinstance(getattr(tracking, "due_at", None), datetime)
                    else None
                ),
            )
        )
        return {
            "routing_updated": True,
            "team_changed": team_changed,
            "tier_changed": tier_changed,
            "from_tier": from_tier or None,
            "to_tier": derived["tier"],
            "from_team_set_code": current_team_set,
            "team_set_code": derived["team_set_code"],
            "agent_id": derived["agent_id"],
        }

    def _open_ticket_for_identity(
        self, respond_contact_id: str, source_message_id: str
    ) -> Optional[ConversationSLATracking]:
        """The OPEN ticket for this (contact, trigger message), if one exists.

        FINDING 6: a ticket's identity is the PAIR, not the message alone -
        WhatsApp message ids are not guaranteed globally unique across different
        contacts/threads, so a bare source_message_id lookup would hand contact B
        contact A's ticket on a coincidental collision. Mirrors the partial
        unique index exactly (open + conversation scope), which is what makes it
        usable both as the idempotency pre-query and as the recovery read after
        that index rejects a concurrent insert.
        """
        return (
            self.db.query(ConversationSLATracking)
            .filter(
                ConversationSLATracking.source_message_id == source_message_id,
                ConversationSLATracking.respond_contact_id == respond_contact_id,
                ConversationSLATracking.is_resolved.is_(False),
                conversation_tracking_scope(),
            )
            .first()
        )

    def _log_reused_ticket_brand_mismatch(
        self,
        existing: ConversationSLATracking,
        incoming_brand_code: Optional[str],
    ) -> None:
        """Note a create that reuses an OPEN ticket routed for a different brand.

        No behaviour change - the open tracking keeps the brand it was routed with -
        but a spine that has started resolving a different brand for the same
        conversation is worth seeing in the log. Called from both reuse paths: the
        per-enquiry retry (same trigger message) and the legacy one-open-per-contact
        singleton a payload without any trigger-message identity falls back to.
        """
        from app.services.user_service import normalise_brand_code

        incoming_brand = normalise_brand_code(incoming_brand_code)
        open_brand = normalise_brand_code(getattr(existing, "brand_code", None))
        if incoming_brand == open_brand:
            return
        _module_logger.info(
            "conversation SLA idempotent create carried brand %s while open "
            "tracking %s is routing brand %s; the open tracking's brand is kept.",
            incoming_brand or "all brands",
            getattr(existing, "id", "?"),
            open_brand or "all brands",
        )

    def create_tracking(self, tracking_data: ConversationSLATrackingCreate):
        """Create a new tracking record."""
        from datetime import timedelta, datetime, timezone
        from app.models.sla import SLAPolicy, SLAPolicyTier
        
        import logging

        logger = logging.getLogger(__name__)

        tracking_dict = tracking_data.model_dump()
        contact_phone_number = tracking_dict.pop("contact_phone_number", None)

        # Conversation SLA always starts at tier 1 - any n8n-supplied current_tier is
        # ignored (D2). Forced BEFORE the assignee/tier logic that reads current_tier.
        tracking_dict["current_tier"] = 1

        # contact_phone_number is required and validated in schema
        if not contact_phone_number:
            raise handle_validation_error("contact_phone_number is required")

        # Find contact by phone number
        normalized_phone = contact_phone_number.strip()
        contact = self.db.query(RespondContact).filter(
            RespondContact.phone_number == normalized_phone
        ).first()
        if not contact:
            raise handle_validation_error(
                f"Respond contact not found for phone number: {normalized_phone}"
            )
        tracking_dict["respond_contact_id"] = contact.id

        # AC-E2: stamp the company ONCE, here, from the contact. Every later read of
        # this tracker - escalation, extension notify, the handling lock - takes the
        # company from the row rather than re-deriving it, so a tier-2 escalation of a
        # Mocha conversation cannot land on Sorento even when it fires from a
        # scheduler tick with no request company at all.
        from app.services.company_routing_service import (
            company_for_contact,
            resolve_routing_company,
        )

        # An explicit company_id from n8n wins (it already resolved the product, so
        # it knows), but only when it names a real company - a stale or mistyped id
        # falls through to the contact rather than stranding the conversation.
        body_company_id = tracking_dict.pop("company_id", None)
        # Only resolved when the field is actually present - the normal path must not
        # pay for a lookup nobody asked for.
        body_company = (
            resolve_routing_company(self.db, company_id=body_company_id)
            if str(body_company_id or "").strip()
            else None
        )
        tracking_dict["company_id"] = (
            body_company.company_id
            if body_company is not None and body_company.source == "body"
            else company_for_contact(
                self.db, contact_id=str(contact.id), phone=normalized_phone
            )
        )

        # Brand: the second routing axis, stamped once here and read back by every
        # escalation. A legacy suffixed team-set code carries the brand in its name;
        # an explicit brand_code from an updated n8n wins over it.
        from app.services.user_service import (
            normalise_brand_code,
            split_legacy_team_set_code,
        )

        base_team_set_code, suffix_brand = split_legacy_team_set_code(
            tracking_dict.get("team_set_code")
        )
        if base_team_set_code is not None:
            tracking_dict["team_set_code"] = base_team_set_code
        tracking_dict["brand_code"] = (
            normalise_brand_code(tracking_dict.get("brand_code")) or suffix_brand
        )

        # Resolve agent_code → agent_id (FK). When no assignee is passed, pick via round-robin
        # for current_tier (same as escalation). When assigned_to / assigned_to_id is passed
        # (e.g. after external next-assignee), use it and do not advance the tier cursor again.
        raw_agent_code = tracking_dict.pop("agent_code", None)
        aid_raw = tracking_dict.get("assigned_to_id")
        ato_raw = tracking_dict.get("assigned_to")
        has_explicit_assignee = (
            (aid_raw is not None and str(aid_raw).strip() != "")
            or (ato_raw is not None and str(ato_raw).strip() != "")
        )
        # Set when the RR branch resolved the assignee via get_escalation_assignee_for_tier,
        # which already applied the coverage redirect - so the generic redirect below must
        # NOT run again (would be a 2nd hop, violating one-hop). Explicit-assignee paths are
        # NOT redirected yet → the block below handles them.
        coverage_applied_in_rr = False
        if raw_agent_code:
            from app.models.access import AccessAgent as _AccessAgent
            from app.models.user import User as _User

            _agent = self.db.query(_AccessAgent).filter(
                _AccessAgent.code == raw_agent_code.strip()
            ).first()
            if not _agent:
                raise handle_validation_error(
                    f"No access agent found with code '{raw_agent_code}'. "
                    "Create the Access Agent first."
                )
            tracking_dict["agent_id"] = str(_agent.id)

            # Resolve the SLA policy server-side from (agent, team_set) binding (D4).
            # The CRM owns the policy; any n8n-supplied policy_id is only a transition
            # fallback (D8) until every team set is bound, then unbound = 422.
            from app.services.user_service import AccessAgentService as _AccessAgentService

            team_set_code = tracking_dict.get("team_set_code")
            # resolve_policy_id_for raises 409 when the team set has inconsistent policies.
            resolved_policy_id = _AccessAgentService(self.db).resolve_policy_id_for(
                str(_agent.id),
                str(team_set_code or ""),
                company_id=tracking_dict["company_id"],
            )
            if resolved_policy_id:
                tracking_dict["policy_id"] = resolved_policy_id
            else:
                fallback_policy_id = tracking_dict.get("policy_id")
                if fallback_policy_id:
                    logger.warning(
                        "conversation SLA: no policy bound for agent=%s team_set=%s; "
                        "falling back to n8n-supplied policy_id %s",
                        raw_agent_code,
                        team_set_code,
                        fallback_policy_id,
                    )
                else:
                    raise handle_validation_error(
                        f"No SLA policy bound for agent '{raw_agent_code}' / "
                        f"team set '{team_set_code}'."
                    )

            if has_explicit_assignee:
                if aid_raw is not None and str(aid_raw).strip():
                    user = self.db.query(_User).filter(_User.id == str(aid_raw).strip()).first()
                    if not user:
                        raise handle_validation_error(
                            f"User not found for assigned_to_id: {aid_raw}"
                        )
                    tracking_dict["assigned_to_id"] = user.id
                    rid = getattr(user, "respond_user_id", None)
                    tracking_dict["assigned_to"] = (
                        str(rid).strip() if rid is not None and str(rid).strip() else None
                    )
                else:
                    assigned_to_value = str(ato_raw).strip()
                    user = self.db.query(_User).filter(
                        (_User.respond_user_id == assigned_to_value)
                        | (_User.id == assigned_to_value)
                        | (_User.email == assigned_to_value)
                    ).first()
                    if not user:
                        raise handle_validation_error(
                            f"User not found for respond_user_id: {assigned_to_value}"
                        )
                    tracking_dict["assigned_to_id"] = user.id
                    rid = getattr(user, "respond_user_id", None)
                    tracking_dict["assigned_to"] = (
                        str(rid).strip()
                        if rid is not None and str(rid).strip()
                        else assigned_to_value
                    )
            else:
                assignee = self.get_escalation_assignee_for_tier(
                    source_entity_type=None,
                    target_tier=tracking_dict["current_tier"],
                    team_set_code=tracking_dict.get("team_set_code") or None,
                    agent_id_override=str(_agent.id),
                    company_id=tracking_dict["company_id"],
                    brand_code=tracking_dict.get("brand_code"),
                )
                tracking_dict["assigned_to_id"] = assignee["id"]
                tracking_dict["assigned_to"] = (
                    str(assignee["respond_user_id"])
                    if assignee.get("respond_user_id") is not None
                    else None
                )
                # get_escalation_assignee_for_tier already applied the coverage redirect.
                coverage_applied_in_rr = True
        elif not tracking_dict.get("assigned_to_id") and tracking_dict.get("assigned_to"):
            # Fallback: resolve explicit assigned_to (respond_user_id / user id / email) to assigned_to_id
            from app.models.user import User

            assigned_to_value = str(tracking_dict["assigned_to"]).strip()
            user = self.db.query(User).filter(
                (User.respond_user_id == assigned_to_value) |
                (User.id == assigned_to_value) |
                (User.email == assigned_to_value)
            ).first()
            if not user:
                raise handle_validation_error(
                    f"User not found for respond_user_id: {assigned_to_value}"
                )
            tracking_dict["assigned_to_id"] = user.id

        # Coverage redirect (conversation-SLA initial assignment): if the resolved
        # assignee is covered (on leave), route the new tracking to their coverer.
        # The RR branch already redirected via get_escalation_assignee_for_tier; this
        # covers the explicit-assignee path (n8n routing posts assigned_to_id/_to).
        # Applied to the resolved tracking_dict so it flows into both the new-row and
        # overwrite-resolved branches; the idempotent-active branch returns earlier
        # without touching the assignee. One hop. covered_for_id stamped on the log.
        coverage_covered_for_id: Optional[str] = None
        if tracking_dict.get("assigned_to_id") and not coverage_applied_in_rr:
            from app.services.coverage_subscription_service import (
                resolve_assignee_with_coverage,
            )

            _cur_rid = tracking_dict.get("assigned_to")
            _coverer, coverage_covered_for_id = resolve_assignee_with_coverage(
                self.db,
                {
                    "id": str(tracking_dict["assigned_to_id"]),
                    "email": None,
                    "name": None,
                    "respond_user_id": _cur_rid,
                },
            )
            if coverage_covered_for_id and _coverer:
                tracking_dict["assigned_to_id"] = _coverer["id"]
                tracking_dict["assigned_to"] = (
                    str(_coverer["respond_user_id"])
                    if _coverer.get("respond_user_id")
                    else None
                )

        # Auto-populate initiated_at and current_tier_started_at to now (UTC)
        now_utc = _now_utc()
        if not tracking_dict.get("initiated_at"):
            tracking_dict["initiated_at"] = now_utc
        else:
            tracking_dict["initiated_at"] = _to_aware_utc(tracking_dict["initiated_at"])

        # AC-A4/AC-A9: whether THIS request arrived inside the working window.
        # Computed once here (not tied to whether current_tier_started_at ends up
        # auto-derived below) and stamped on every return path - including an
        # idempotent retry - because n8n reads it from every response to pick its
        # in-hours vs out-of-hours auto-reply, not only on the first insert.
        _normalized_start = _working_clock_start(self.db, now_utc)
        in_working_hours = _normalized_start == _to_aware_utc(now_utc)

        if not tracking_dict.get("current_tier_started_at"):
            # Automatic start: the clock begins when work can actually begin.
            # initiated_at above keeps the true event instant for audit.
            tracking_dict["current_tier_started_at"] = _normalized_start
        else:
            # Caller-supplied start is authoritative - stored verbatim, not normalized.
            tracking_dict["current_tier_started_at"] = _to_aware_utc(tracking_dict["current_tier_started_at"])

        # Reset escalation and resolution fields
        tracking_dict["escalated_at"] = None
        tracking_dict["escalation_reason"] = None
        tracking_dict["is_resolved"] = False
        tracking_dict["resolved_at"] = None
        tracking_dict["resolved_by"] = None
        tracking_dict["resolution_duration"] = None
        tracking_dict["is_responded"] = False
        tracking_dict["responded_at"] = None
        tracking_dict["responded_by"] = None
        tracking_dict["response_time"] = None

        # Get policy and tier to calculate due_at
        policy = self.db.query(SLAPolicy).filter(SLAPolicy.id == tracking_dict["policy_id"]).first()
        if not policy:
            raise handle_not_found("SLA Policy", tracking_dict["policy_id"])
        
        # Clamp to the policy's defined tiers (D7); only error when the policy has none.
        tier = self._resolve_tier_with_clamp(
            tracking_dict["policy_id"], tracking_dict["current_tier"]
        )
        if not tier:
            raise handle_validation_error(
                f"SLA policy tier {tracking_dict['current_tier']} not found for policy {tracking_dict['policy_id']}"
            )

        # Calculate due_at (response) and due_at_resolution from current_tier_started_at + tier hours
        current_tier_started_at = _to_aware_utc(tracking_dict["current_tier_started_at"])
        response_hours_raw = getattr(tier, "response_hours", None)
        response_hours = float(response_hours_raw) if response_hours_raw is not None else 24.0
        resolution_hours_raw = getattr(tier, "resolution_hours", None)
        resolution_hours = float(resolution_hours_raw) if resolution_hours_raw is not None else 24.0
        if current_tier_started_at:
            tracking_dict["due_at"] = _working_due(self.db, current_tier_started_at, response_hours)
            tracking_dict["due_at_resolution"] = _working_due(self.db, current_tier_started_at, resolution_hours)
        else:
            tracking_dict["due_at"] = None
            tracking_dict["due_at_resolution"] = None

        # Ticket identity (AC-A1/AC-A2): a conversation SLA row is "this enquiry",
        # keyed on the message that asked for a human. A contact may hold several
        # open tickets at once; only a retry of the SAME trigger message is
        # idempotent. Fall back to message_id (legacy n8n payloads carry no
        # source_message_id yet), and - with neither - to the old one-open-per-
        # contact singleton so a bare payload doesn't fan out.
        if not tracking_dict.get("source_message_id") and tracking_dict.get("message_id") is not None:
            tracking_dict["source_message_id"] = str(tracking_dict["message_id"])
        identity_key = tracking_dict.get("source_message_id")

        existing = None
        if identity_key:
            existing = self._open_ticket_for_identity(
                str(tracking_dict["respond_contact_id"]), identity_key
            )

            if existing:
                # AC-A2: retry of the same trigger message - the only case per-
                # enquiry idempotency covers. Nothing refreshes, not even
                # message_id: this is a no-op read of the ticket already opened.
                self._log_reused_ticket_brand_mismatch(
                    existing, tracking_dict.get("brand_code")
                )
                setattr(existing, "_already_active", True)
                setattr(existing, "_in_working_hours", in_working_hours)
                return existing
            # No open row for THIS message: always a fresh ticket (AC-A1), even
            # when the contact already holds other open tickets, and even when a
            # PAST ticket for the same message id is resolved - that row is
            # history now, never overwritten (per-enquiry identity, not a
            # contact-level singleton).
        else:
            # No trigger-message identity on the payload at all: keep the legacy
            # one-open-per-contact singleton so a bare call doesn't fan out.
            existing = self.db.query(ConversationSLATracking).filter(
                ConversationSLATracking.respond_contact_id == tracking_dict["respond_contact_id"],
                conversation_tracking_scope(),
            ).order_by(
                ConversationSLATracking.is_resolved.asc(),  # open first
                ConversationSLATracking.created_at.desc(),
            ).first()

            if existing:
                if not bool(getattr(existing, "is_resolved", False)):
                    self._log_reused_ticket_brand_mismatch(
                        existing, tracking_dict.get("brand_code")
                    )
                    setattr(existing, "_already_active", True)
                    setattr(existing, "_in_working_hours", in_working_hours)
                    return existing

                # Existing tracking is resolved - overwrite it for the new
                # conversation. History is carried by event logs (kept: FK by
                # tracking id), not by rows.
                preserve_fields = {"id", "created_at", "respond_contact_id"}
                for key, value in tracking_dict.items():
                    if key not in preserve_fields:
                        setattr(existing, key, value)

                self.db.commit()
                self.db.refresh(existing)
                self._write_assign_event_log(existing, covered_for_id=coverage_covered_for_id)
                self._notify_assignment_on_create(existing)
                self._fan_out_assignment_coverage(existing)
                self._publish_conversation_event(
                    existing,
                    conversation_event_bus.EVENT_TICKET_CREATED,
                    user_ids=[getattr(existing, "assigned_to_id", None)],
                )
                setattr(existing, "_overwrote_resolved", True)
                setattr(existing, "_in_working_hours", in_working_hours)
                return existing

        # Create new tracking record (set due_at_resolution explicitly so it is never omitted)
        tracking = ConversationSLATracking(**tracking_dict)
        if tracking_dict.get("due_at_resolution") is not None:
            setattr(tracking, "due_at_resolution", tracking_dict["due_at_resolution"])
        if tracking_dict.get("due_at") is not None:
            setattr(tracking, "due_at", tracking_dict["due_at"])
        self.db.add(tracking)
        try:
            self.db.commit()
        except IntegrityError:
            # The pre-query above is not a lock: two concurrent deliveries of the
            # same trigger message both miss it, and the partial unique index on
            # (respond_contact_id, source_message_id) rejects whichever INSERT
            # lands second. That conflict IS the idempotency question asked
            # again, so answer it the same way rather than 500-ing: n8n would
            # retry straight back into the race, and meanwhile report a failed
            # intervention for a ticket that exists. Any other integrity failure
            # (a bad FK, say) has nothing to hand back and must still raise.
            self.db.rollback()
            winner = (
                self._open_ticket_for_identity(
                    str(tracking_dict["respond_contact_id"]), identity_key
                )
                if identity_key
                else None
            )
            if winner is None:
                raise
            setattr(winner, "_already_active", True)
            setattr(winner, "_in_working_hours", in_working_hours)
            return winner
        self.db.refresh(tracking)
        self._write_assign_event_log(tracking, covered_for_id=coverage_covered_for_id)
        self._notify_assignment_on_create(tracking)
        self._fan_out_assignment_coverage(tracking)
        # AC-K3: the assignee's pending-tasks widget shows this ticket now, not
        # at the next poll. The idempotent-retry returns above deliberately do
        # NOT reach here - nothing changed, so nothing needs refetching.
        self._publish_conversation_event(
            tracking,
            conversation_event_bus.EVENT_TICKET_CREATED,
            user_ids=[getattr(tracking, "assigned_to_id", None)],
        )
        setattr(tracking, "_in_working_hours", in_working_hours)
        return tracking

    def _write_assign_event_log(
        self,
        tracking: ConversationSLATracking,
        *,
        covered_for_id: Optional[str] = None,
    ) -> None:
        """Write the initial 'assign' event log for a newly started conversation.

        Owned by the backend (n8n's flow no longer posts it) so an idempotent
        create hit cannot produce duplicate assign logs.

        Best-effort: the tracking row is already committed when this runs, so a
        failure here must not fail the create - n8n would get a 500 for a create
        that succeeded, and its retry would take the idempotent path which never
        backfills the log. Log a warning and continue instead.
        """
        import logging

        try:
            assignee_ref = (
                getattr(tracking, "assigned_to_id", None)
                or getattr(tracking, "assigned_to", None)
            )
            label, user_id = event_log_assignee_fields(self.db, assignee_ref)
            tier = getattr(tracking, "current_tier", 1)
            due_at = getattr(tracking, "due_at", None)
            base_reason = f"New Assignee {label}" if label else "New conversation tracking"
            if covered_for_id:
                from app.services.coverage_subscription_service import coverage_note

                base_reason = f"{base_reason}{coverage_note(self.db, covered_for_id)}"
            self.create_event_log(ConversationSLAEventLogCreate(
                sla_tracking_id=str(tracking.id),
                event_type="assign",
                from_tier=tier,
                to_tier=tier,
                event_at=_now_utc(),
                assigned_to=label,
                assigned_to_id=user_id,
                # DB stores naive UTC; pass aware UTC so create_event_log's
                # naive-means-MYT normalization doesn't shift it by -8h.
                due_at=_to_aware_utc(due_at) if isinstance(due_at, datetime) else None,
                reason=base_reason,
            ))
        except Exception:
            self.db.rollback()
            logging.getLogger(__name__).warning(
                "Failed to write assign event log for tracking %s; "
                "tracking exists without its initial assign log.",
                getattr(tracking, "id", None),
                exc_info=True,
            )

    def _notify_assignment_on_create(
        self,
        tracking: ConversationSLATracking,
        *,
        occurrence: Optional[int] = None,
    ) -> None:
        """Notify the PRIMARY assignee that a conversation SLA was assigned to them on
        create/overwrite. Conversation create previously delegated the assignee notify to
        n8n/Respond; the CRM now owns it so it fires regardless of the routing channel.

        In-app always; email/WhatsApp gated by the recipient's own assignment toggles
        (notify_email_on_assignment / notify_whatsapp_on_assignment) - same matrix as
        reassign/escalate. Best-effort: the tracking row is already committed, so a
        failure here must never fail the create (n8n would get a 500 for a create that
        succeeded, and its retry takes the idempotent path which never re-notifies).
        Coverage subscribers are handled separately by _fan_out_assignment_coverage.

        ``occurrence`` overrides the dedup-key suffix (event_type ``assigned:<occ>``).
        Create/overwrite pass None → the key derives from the tier-clock start, so a
        repeat inbound at the same clock dedups. Manual reassign passes the per-action
        change moment so re-assigning back to a prior assignee (A→B→A) still notifies -
        the tier clock does NOT restart on a same-team reassign, so a tier-start key
        would collide with the earlier A→ notification and silently drop the send."""
        import logging

        log = logging.getLogger(__name__)
        try:
            assignee_id = getattr(tracking, "assigned_to_id", None)
            if not assignee_id:
                return
            from app.config import settings
            from app.services.notification_service import NotificationService
            from app.services.form_sla_service import build_sla_whatsapp_data

            base_url = (getattr(settings, "frontend_base_url", None) or "").strip().rstrip("/")
            # UAC AC-G1: deep-link to the dashboard with the ticket targeted, not the
            # standalone SLA detail page - the assignee answers from the "My Pending"
            # drawer now, never Respond.io. The `(protected)` layout captures
            # pathname+search on an unauthenticated hit and replays it after login
            # (?callbackUrl=...), so the deep link survives sign-in.
            detail = f"{base_url}/?ticket={tracking.id}" if base_url else ""
            ref = (self._resolve_my_pending_references([tracking]) or {}).get(
                str(tracking.id)
            ) or "an SLA task"

            title = "An SLA task was assigned to you"
            # AC-G2: the clock statement sits BEFORE the link, so it survives a
            # notification surface that truncates, and so WhatsApp's flattened
            # single-line param reads "... assigned to you. Clock starts ...".
            body = append_clock_line(f"{ref} has been assigned to you.", tracking)
            if detail:
                body += f"\n\nOpen: {detail}"
            data = {
                "tracking_id": str(tracking.id),
                **build_sla_whatsapp_data(self.db, tracking, str(assignee_id), body),
            }
            # Distinct event_type PER assignment occurrence: the tracking id is reused
            # across conversations (overwrite-resolved), and the dedup key is
            # (user, source_type, source_id, event_type). A static "assigned" would
            # stale-dedup and NEVER re-notify on a fresh conversation reusing the row.
            # Key off the assignment start time (reset on each new conversation); the
            # email-key resolver strips the numeric suffix so it stays a registered key.
            if occurrence is not None:
                occ = occurrence
            else:
                started = (
                    getattr(tracking, "current_tier_started_at", None)
                    or getattr(tracking, "initiated_at", None)
                )
                occ = int(started.timestamp()) if isinstance(started, datetime) else 0
            NotificationService(self.db).create_with_channel_preferences(
                user_id=str(assignee_id),
                type="conversation_sla",
                title=title,
                body=body,
                data=data,
                source_entity_type="conversation_sla_tracking",
                source_entity_id=str(tracking.id),
                event_type=f"assigned:{occ}",
                send_in_app=True,
                send_email=True,
                send_whatsapp=True,
                email_pref_attr="notify_email_on_assignment",
                whatsapp_pref_attr="notify_whatsapp_on_assignment",
            )
        except Exception as e:  # noqa: BLE001 - best-effort; row already committed
            log.warning(
                "assignment notify failed for %s: %s", getattr(tracking, "id", "?"), e
            )

    def _fan_out_assignment_coverage(self, tracking: ConversationSLATracking) -> None:
        """Best-effort: notify the assignee's COVERAGE subscribers (notify-only coverage)
        that a conversation SLA was assigned to the person they cover. Conversation
        create previously had no assignment notify at all, so coverage subscribers never
        heard about INITIAL assignments (only reassign/escalate/takeover fanned out).
        Mirrors _notify_reassignment's fan-out. The assignee notification itself stays
        with n8n/Respond - we only add the coverage copies here. Never raises."""
        import logging

        log = logging.getLogger(__name__)
        try:
            assignee_id = getattr(tracking, "assigned_to_id", None)
            if not assignee_id:
                return
            from app.config import settings
            from app.services.coverage_subscription_service import fan_out_coverage_copies
            from app.services.form_sla_service import build_sla_whatsapp_data

            from app.models.user import User as _User

            base_url = (getattr(settings, "frontend_base_url", None) or "").strip().rstrip("/")
            # Deep link to the dashboard "My pending tasks" → My Team, with the colleague's
            # task highlighted so the coverer can take it over (?team_task=<id>). NOT the
            # detail page - the action they need (takeover) lives in the My Team widget.
            team_link = f"{base_url}/?team_task={tracking.id}" if base_url else ""
            ref = (self._resolve_my_pending_references([tracking]) or {}).get(
                str(tracking.id)
            ) or "an SLA task"
            covered = self.db.query(_User).filter(_User.id == str(assignee_id)).first()
            covered_name = (
                (covered.name or covered.email) if covered else str(assignee_id)
            ) or "a teammate"
            # NOTIFY-ONLY coverage: the task is assigned to the colleague, not the
            # subscriber. Word it that way (auto-assign uses the normal "assigned to you"
            # notification, since the coverer IS the assignee there).
            cover_title = f"SLA task assigned to {covered_name}"
            # AC-G2: a coverer decides whether to take over from the deadline, so
            # they get the same clock statement the assignee got.
            cover_body = append_clock_line(
                f"{ref} has been assigned to {covered_name}.", tracking
            )
            cover_body += f"\n\nYou're receiving this because you cover for {covered_name}."
            if team_link:
                cover_body += f"\n\nTake over: {team_link}"
            # Distinct event_type PER assignment occurrence so a re-assignment of the SAME
            # (reused) conversation tracker after a resolve re-notifies coverers - the
            # dedup key is (user, source_type, source_id, event_type) and the tracker id is
            # reused across conversations. Key off the assignment's start time (reset on
            # each new conversation). The email-key resolver strips the numeric suffix.
            started = (
                getattr(tracking, "current_tier_started_at", None)
                or getattr(tracking, "initiated_at", None)
            )
            occ = int(started.timestamp()) if isinstance(started, datetime) else 0
            fan_out_coverage_copies(
                self.db,
                target_user_id=str(assignee_id),
                actor_user_id=None,
                notification_type="conversation_sla",
                title=cover_title,
                body=cover_body,
                data={"tracking_id": str(tracking.id)},
                source_entity_type="conversation_sla_tracking",
                source_entity_id=str(tracking.id),
                event_type=f"assigned:{occ}",
                email_pref_attr="notify_email_on_assignment",
                whatsapp_pref_attr="notify_whatsapp_on_assignment",
                cover_title=cover_title,
                cover_body=cover_body,
                tracking=tracking,
                whatsapp_use_case="sla_assignment",
            )
        except Exception:
            self.db.rollback()
            log.warning(
                "coverage fan-out on conversation assignment failed for %s",
                getattr(tracking, "id", None),
                exc_info=True,
            )

    def update_tracking(
        self,
        tracking_id: str,
        tracking_data: ConversationSLATrackingUpdate,
        *,
        resolve_origin: str = "user",
    ):
        """Update a tracking record.

        ``resolve_origin`` says WHO decided this resolve, and exists for exactly
        one reason: the AC-M3 close-convo webhook must never answer n8n's own
        resolve. A Respond-side close makes n8n resolve the ticket through
        ``PUT /{tracking_id}`` with the API-key principal; firing our
        close-convo webhook back at n8n from there makes n8n send the customer a
        SECOND closing message. ``"api_key"`` suppresses that webhook (n8n
        already knows - it closed the conversation); ``"user"`` (the default, and
        what the widget's Resolve uses) fires it.

        Deliberately NOT applied to the RQ Respond-close job: that is the
        transport tidy-up (mark the conversation closed, idempotent), not a
        message to the contact, so it keeps running on every resolve.
        """
        from datetime import datetime, timezone
        from decimal import Decimal, ROUND_HALF_UP
        from app.models.user import User

        tracking = self.get_tracking(tracking_id)

        # Snapshot the owner BEFORE anything is applied: resolving a
        # conversation ticket deliberately UNSETS assigned_to_id (so n8n stops
        # looking at the row), and the person whose pending list the ticket is
        # leaving is exactly who needs to be told it left (S4.2, AC-K3).
        assignee_before_update = getattr(tracking, "assigned_to_id", None)

        update_data = tracking_data.model_dump(exclude_unset=True)

        # AC-E3 guard, enforced HERE (the shared update path both PUT
        # /{tracking_id} and PUT/POST /integration/{tracking_id} call) rather
        # than duplicated per-route: the sibling /integration/{tracking_id}
        # route has no auth dependency and used to stamp is_responded (plus
        # write a "response" event log) with no ambiguity check at all.
        #
        # Resolve the payload's `responded_by` (respond_user_id / email /
        # users.id) to the internal user id BEFORE checking ambiguity: it
        # identifies the ACTUAL replying user, which can differ from this
        # tracking's own assignee (the n8n fallback resolves `tracking` via a
        # separate contact-level "preferred" lookup) - see
        # is_ambiguous_fallback_response for why that distinction matters.
        # AC-I3: a duplicate respond is idempotent, not a refusal. Resolve has
        # short-circuited on `_already_resolved` for a while; respond raised a
        # 400 instead, and that asymmetry produced 53 refusals across 19 contacts
        # on production data (one contact hit 17 times). Under multi-open tickets
        # the 400 is actively harmful: it aborts n8n's Respond-app-reply fallback
        # before the genuinely unanswered sibling is stamped. Strip only the
        # responded-family fields (same shape as the AC-E3 ambiguity guard below,
        # FINDING 5) so a resolve / assignment riding in the same payload still
        # applies, and hand the caller a marker to branch on.
        already_responded_skipped = False
        if _coerce_flag(update_data.get("is_responded")) and bool(
            getattr(tracking, "is_responded", False)
        ):
            already_responded_skipped = True
            for _field in ("is_responded", "responded_at", "responded_by", "response_time"):
                update_data.pop(_field, None)

        ambiguous_responded_skipped = False
        if bool(update_data.get("is_responded")) and not bool(
            getattr(tracking, "is_responded", False)
        ):
            _raw_responded_by = update_data.get("responded_by")
            _resolved_responded_by = None
            if _raw_responded_by is not None and str(_raw_responded_by).strip():
                _responded_by_value = str(_raw_responded_by).strip()
                _responded_by_user = self.db.query(User).filter(
                    (User.respond_user_id == _responded_by_value)
                    | (User.id == _responded_by_value)
                    | (User.email == _responded_by_value)
                ).first()
                if not _responded_by_user:
                    raise handle_validation_error(
                        f"User not found for responded_by (respond_user_id): {_responded_by_value}"
                    )
                _resolved_responded_by = _responded_by_user.id
                # Reuse the already-resolved id below instead of re-querying it.
                update_data["responded_by"] = _resolved_responded_by

            if self.is_ambiguous_fallback_response(
                tracking, responded_by=_resolved_responded_by
            ):
                # Strip ONLY the responded-family fields - the rest of the
                # payload (assignment, tier, resolve, ...) still applies
                # below, in this SAME call, instead of the whole update being
                # silently discarded (the caller-visible marker is set on
                # `tracking` just before the final return, once every other
                # field this call touched has actually been applied).
                ambiguous_responded_skipped = True
                for _field in ("is_responded", "responded_at", "responded_by", "response_time"):
                    update_data.pop(_field, None)

        # Resolve agent_code → agent_id FK if caller passed a code string
        raw_agent_code = update_data.pop("agent_code", None)
        if raw_agent_code:
            from app.models.access import AccessAgent as _AccessAgent
            _agent = self.db.query(_AccessAgent).filter(
                _AccessAgent.code == raw_agent_code.strip()
            ).first()
            if not _agent:
                raise handle_validation_error(
                    f"No access agent found with code '{raw_agent_code}'. "
                    "Create the Access Agent first."
                )
            update_data["agent_id"] = str(_agent.id)
        elif "agent_code" in tracking_data.model_fields_set and raw_agent_code is None:
            # Explicitly set to null - clear the FK
            update_data["agent_id"] = None

        # Explicitly clear assignee when assigned_to is None (keep in sync with Respond.io)
        if "assigned_to" in update_data and update_data["assigned_to"] is None:
            update_data["assigned_to_id"] = None

        # Resolve assigned_to to assigned_to_id when it's a non-empty string
        if "assigned_to_id" not in update_data and (
            update_data.get("assigned_to") is not None and str(update_data.get("assigned_to")).strip()
        ):
            assigned_to_value = str(update_data["assigned_to"]).strip()
            user = self.db.query(User).filter(
                (User.respond_user_id == assigned_to_value) |
                (User.id == assigned_to_value) |
                (User.email == assigned_to_value)
            ).first()
            if not user:
                raise handle_validation_error(
                    f"User not found for respond_user_id: {assigned_to_value}"
                )
            update_data["assigned_to_id"] = user.id
        
        # Coerce flags to bool for consistent handling (e.g. JSON "true", 1, or string "true")
        is_responded = _coerce_flag(update_data.get("is_responded"))
        is_resolved = _coerce_flag(update_data.get("is_resolved"))
        # If client sent resolved_by or resolved_at without is_resolved, treat as marking resolved
        if not is_resolved and (update_data.get("resolved_by") or ("resolved_at" in update_data and update_data.get("resolved_at") is not None)):
            is_resolved = True

        # Short-circuit: caller tries to resolve an already-resolved tracking.
        # Return current state with a transient marker; let the route signal
        # "already resolved, not updated" via response header. n8n decides routing.
        if is_resolved and bool(getattr(tracking, "is_resolved", False)):
            setattr(tracking, "_already_resolved", True)
            return tracking

        # Smart handling for is_responded (same as responded_at / responded_by).
        # An already-responded tracking never reaches here: the AC-I3 short-circuit
        # above popped the responded-family fields out of update_data.
        if is_responded:
            _resp_by = update_data.get("responded_by")
            if _resp_by is None or (isinstance(_resp_by, str) and not str(_resp_by).strip()):
                _aid = self._resolve_tracking_assignee_user_id(tracking)
                if _aid:
                    update_data["responded_by"] = _aid
            update_data["is_responded"] = True
            # Auto-set responded_at to now (UTC)
            if "responded_at" not in update_data or update_data.get("responded_at") is None:
                update_data["responded_at"] = _now_utc()
            
            # Auto-calculate response_time from initiated_at to responded_at (UTC)
            if "response_time" not in update_data or update_data.get("response_time") is None:
                responded_at = update_data["responded_at"]
                if isinstance(responded_at, str):
                    responded_at = datetime.fromisoformat(responded_at.replace('Z', '+00:00'))
                responded_at_utc = _to_aware_utc(responded_at)
                initiated_at = getattr(tracking, "initiated_at", None)
                if isinstance(initiated_at, datetime) and responded_at_utc:
                    initiated_at_utc_val = _to_aware_utc(initiated_at)
                    duration = (
                        (responded_at_utc - initiated_at_utc_val).total_seconds() / 3600
                        if initiated_at_utc_val is not None
                        else 0.0
                    )
                    update_data["response_time"] = Decimal(str(max(0, duration))).quantize(
                        Decimal("0.01"), rounding=ROUND_HALF_UP
                    )
            
            # Resolve responded_by to user UUID (users.id); accept respond_user_id, email, or id
            if "responded_by" in update_data and update_data.get("responded_by"):
                responded_by_value = str(update_data["responded_by"]).strip()
                responded_by_user = self.db.query(User).filter(
                    (User.respond_user_id == responded_by_value) |
                    (User.id == responded_by_value) |
                    (User.email == responded_by_value)
                ).first()
                if not responded_by_user:
                    raise handle_validation_error(
                        f"User not found for responded_by (respond_user_id): {responded_by_value}"
                    )
                update_data["responded_by"] = responded_by_user.id
        elif update_data.get("is_responded") is False:
            # If setting is_responded to False, clear response fields
            update_data["responded_at"] = None
            update_data["response_time"] = None
            update_data["responded_by"] = None
        
        # Smart handling for is_resolved (same pattern: resolved_at, resolution_duration, resolved_by as user UUID)
        resolved_in_this_request = False
        # AC-M3: the close webhook names the team as the contact-facing fallback
        # when the resolver has no Respond mapping. Owner ruling R5 (22 Sep 2026):
        # resolve no longer clears team_set_code, so this could read it straight
        # off `tracking` after the commit - snapshotted here anyway to keep this
        # read on the same footing as the rest of the resolve block.
        close_team_label: Optional[str] = None
        if is_resolved:
            # Short-circuit above already returned for already-resolved case.
            resolved_in_this_request = True
            _res_by = update_data.get("resolved_by")
            if _res_by is None or (isinstance(_res_by, str) and not str(_res_by).strip()):
                _rid = self._resolve_tracking_assignee_user_id(tracking)
                if _rid:
                    update_data["resolved_by"] = _rid
            update_data["is_resolved"] = True
            # Owner ruling R5 (22 Sep 2026): both Conversation SLA and Form SLA keep
            # assigned_to / assigned_to_id / agent_id / team_set_code / message_id on
            # resolve, for the audit trail (agent / stage / assignee at resolution).
            # Every worklist (My Pending, My Team, the inbox Mine tab) already gates
            # on is_resolved rather than on the assignee, so this does not resurrect a
            # resolved ticket anywhere - see sla_scope.open_tracker_scope() and
            # conversation_inbox_service._open_conversation_ticket_clause().
            from app.services.form_sla_service import FORM_SLA_TYPES

            _is_form_tracker = (
                getattr(tracking, "source_entity_type", None) in FORM_SLA_TYPES
            )
            if not _is_form_tracker:
                close_team_label = (self._ticket_team_labels([tracking]) or {}).get(
                    str(tracking.id)
                )
            # Always set resolved_at when marking resolved (UTC)
            if "resolved_at" not in update_data or update_data.get("resolved_at") is None:
                update_data["resolved_at"] = _now_utc()
            
            # Auto-calculate resolution_duration from initiated_at to resolved_at (UTC)
            if "resolution_duration" not in update_data or update_data.get("resolution_duration") is None:
                resolved_at = update_data["resolved_at"]
                if isinstance(resolved_at, str):
                    resolved_at = datetime.fromisoformat(resolved_at.replace('Z', '+00:00'))
                resolved_at_utc = _to_aware_utc(resolved_at)
                initiated_at = getattr(tracking, "initiated_at", None)
                if isinstance(initiated_at, datetime) and resolved_at_utc:
                    initiated_at_utc_val = _to_aware_utc(initiated_at)
                    duration = (
                        (resolved_at_utc - initiated_at_utc_val).total_seconds() / 3600
                        if initiated_at_utc_val is not None
                        else 0.0
                    )
                    update_data["resolution_duration"] = Decimal(str(max(0, duration))).quantize(
                        Decimal("0.01"), rounding=ROUND_HALF_UP
                    )
            
            # Resolve resolved_by to user UUID (users.id); accept respond_user_id (e.g. 971724), email, or id
            if "resolved_by" in update_data and update_data.get("resolved_by") is not None:
                resolved_by_value = str(update_data["resolved_by"]).strip()
                resolved_by_user = self.db.query(User).filter(
                    (User.respond_user_id == resolved_by_value) |
                    (User.id == resolved_by_value) |
                    (User.email == resolved_by_value)
                ).first()
                if not resolved_by_user:
                    raise handle_validation_error(
                        f"User not found for resolved_by (respond_user_id): {resolved_by_value}"
                    )
                update_data["resolved_by"] = resolved_by_user.id
        elif update_data.get("is_resolved") is False:
            # If setting is_resolved to False, clear resolution fields
            update_data["resolved_at"] = None
            update_data["resolution_duration"] = None
            update_data["resolved_by"] = None
        
        # Convert all datetime fields to timezone-aware UTC before storing
        datetime_fields = ["initiated_at", "current_tier_started_at", "due_at", "due_at_resolution", "escalated_at",
                          "responded_at", "resolved_at"]
        for field in datetime_fields:
            if field in update_data and update_data[field] is not None:
                dt = update_data[field]
                if isinstance(dt, str):
                    dt = datetime.fromisoformat(dt.replace('Z', '+00:00'))
                if isinstance(dt, datetime):
                    update_data[field] = _to_aware_utc(dt)
        
        # Apply all updates
        for key, value in update_data.items():
            setattr(tracking, key, value)

        self.db.commit()
        self.db.refresh(tracking)

        # Resolving is the strongest "I'm on it" signal: void any pending takeover on
        # this tracking (implicit veto, AC-VOID-1). Best-effort - never raises.
        if resolved_in_this_request:
            from app.services.sla_takeover_service import SlaTakeoverService

            SlaTakeoverService(self.db).void_for_tracking(str(tracking.id), "resolved")

        # Best-effort: a resolved CONVERSATION SLA closes the matching Respond.io
        # conversation (Resolve = Respond close, per product decision). Form trackers
        # are excluded - their Respond conversation lifecycle is owned elsewhere.
        # Post-commit side effect: must never raise (the resolve already succeeded);
        # the retry path is idempotent and would not re-attempt the close otherwise.
        #
        # AC-C3/AC-F1 (multi-open consumer audit): a contact can now hold several
        # open tickets against ONE shared Respond conversation. Closing that shared
        # conversation because ONE ticket resolved would sever transport for the
        # sibling ticket's unfinished work - a real regression this predates only
        # because there used to be at most one open ticket per contact. Only close
        # Respond when this was the contact's LAST open conversation-scope ticket;
        # skip (byte-identical to "do nothing") while any sibling remains open. Full
        # retirement of this side effect even for the single-ticket case (the literal
        # reading of AC-C3: "no Respond API call is made" on ANY ticket resolve) is an
        # open product question for the dedicated ticket-resolve build - not applied
        # here; flagged for an orchestrator decision.
        from app.services.form_sla_service import FORM_SLA_TYPES as _FORM_TYPES

        if resolved_in_this_request and (
            getattr(tracking, "source_entity_type", None) not in _FORM_TYPES
        ):
            # One closing message per ticket, on every lane (user or api-key),
            # BEFORE the sibling gate: the contact hears about THIS enquiry
            # whether or not others are still open. The CRM is the only
            # sender; n8n's respond-close-convo no longer messages.
            self._enqueue_ticket_resolved_message_best_effort(tracking)
            if not self._has_other_open_conversation_siblings(tracking):
                self._close_respond_conversation_best_effort(tracking)
                # AC-M3: and tell n8n directly, so respond-close-convo runs with
                # a real resolver identity instead of inferring one from an API
                # close. Additive to the RQ job above, which stays as the
                # transport tidy-up.
                #
                # AC-M3 hardening: ONLY for a CRM-origin (user) resolve. An
                # API-key resolve came FROM n8n's respond-close-convo lane after
                # a Respond-side close, and answering it would have n8n send the
                # customer a second closing message - the loop.
                if resolve_origin != "api_key":
                    self._notify_close_convo_webhook_best_effort(tracking, close_team_label)

        # FINDING 5: caller-visible marker, set only once every other field
        # this call touched (assignment, tier, resolve, ...) has been applied
        # and committed above - routes read it to report
        # `ambiguous_responded_skipped` without re-deriving it.
        if ambiguous_responded_skipped:
            setattr(tracking, "_ambiguous_responded_skipped", True)
        # AC-I3: same contract as `_already_resolved` - a transient marker the
        # route reads and exposes as a response field. It dies on re-query, so
        # routes must read it BEFORE calling get_tracking() again.
        if already_responded_skipped:
            setattr(tracking, "_already_responded", True)

        # AC-K3: the shared write path for resolve, respond and assignment
        # changes - one poke covers every route that funnels through here.
        # Both owners: an assignment change replaces the assignee outright; a
        # resolve keeps the same assignee (owner ruling R5, 22 Sep 2026) but
        # still drops the row off that assignee's pending list, so both events
        # still need publishing even when before/after are the same id.
        self._publish_conversation_event(
            tracking,
            conversation_event_bus.EVENT_TICKET_UPDATED,
            user_ids=[assignee_before_update, getattr(tracking, "assigned_to_id", None)],
        )
        return tracking

    def _has_other_open_conversation_siblings(
        self, tracking: ConversationSLATracking
    ) -> bool:
        """True when another OPEN conversation-scope tracking exists for the same
        contact (AC-F1: multi-open tickets). Used to gate the Respond-close side
        effect on resolve - see the caller's comment."""
        contact_id = getattr(tracking, "respond_contact_id", None)
        if not contact_id:
            return False
        return (
            self.db.query(ConversationSLATracking.id)
            .filter(
                ConversationSLATracking.respond_contact_id == contact_id,
                ConversationSLATracking.id != tracking.id,
                ConversationSLATracking.is_resolved.is_(False),
                conversation_tracking_scope(),
            )
            .first()
            is not None
        )

    def _enqueue_ticket_resolved_message_best_effort(
        self, tracking: ConversationSLATracking
    ) -> None:
        """Queue the contact's per-ticket closing message
        (PLAN-ticket-resolved-closing-message). Post-commit, best-effort: the
        resolve already succeeded, so a queue that is away is logged, not raised."""
        import logging

        logger = logging.getLogger(__name__)
        try:
            from app.services.queue_service import enqueue_job
            from app.tasks.respond_io_tasks import send_ticket_resolved_message

            enqueue_job(
                send_ticket_resolved_message,
                str(tracking.id),
                queue_name="respond_io",
                job_timeout=120,
            )
        except Exception as exc:  # noqa: BLE001 - enqueue is best-effort
            logger.warning(
                "ticket-resolved message enqueue failed for %s: %s",
                getattr(tracking, "id", "?"),
                exc,
            )

    def _notify_close_convo_webhook_best_effort(
        self, tracking: ConversationSLATracking, team_name: Optional[str] = None
    ) -> None:
        """Fire the direct ``respond-close-convo`` webhook for a resolved ticket
        (UAC AC-M3). Same gate as the RQ close above: only when this was the
        contact's LAST open conversation-scope ticket.

        Post-commit side effect - the notify function already swallows its own
        failures, and this wrapper catches anything it cannot (an import error,
        a dead session) so a resolve that succeeded never reports a 500."""
        import logging

        logger = logging.getLogger(__name__)
        try:
            from app.services.crm_close_convo_webhook import notify_ticket_resolved_close

            notify_ticket_resolved_close(
                self.db,
                tracking_id=str(tracking.id),
                respond_contact_id=(
                    str(tracking.respond_contact_id)
                    if getattr(tracking, "respond_contact_id", None)
                    else None
                ),
                resolved_by_user_id=(
                    str(tracking.resolved_by)
                    if getattr(tracking, "resolved_by", None)
                    else None
                ),
                resolved_at=getattr(tracking, "resolved_at", None),
                team_name=team_name,
            )
        except Exception as exc:  # noqa: BLE001 - the resolve already committed
            logger.warning(
                "Resolve: respond-close-convo webhook failed for tracking %s: %s",
                getattr(tracking, "id", None),
                exc,
            )

    def _close_respond_conversation_best_effort(
        self, tracking: ConversationSLATracking
    ) -> None:
        """Enqueue the Respond.io conversation close for a resolved conversation SLA.

        The actual close + its Respond outbox row (success AND failure, with the
        Respond HTTP status/body on 4xx/5xx) run on the ``respond_io`` worker queue,
        decoupled from the request so a Respond failure never blocks the resolve,
        and so a prod failure is diagnosable via ``integration_logs``. Enqueue itself
        is best-effort (never raises; the resolve already committed)."""
        import logging

        logger = logging.getLogger(__name__)
        try:
            from app.services.queue_service import enqueue_job
            from app.tasks.respond_io_tasks import close_respond_conversation

            enqueue_job(
                close_respond_conversation,
                str(tracking.id),
                queue_name="respond_io",
                job_timeout=60,
            )
        except Exception as exc:  # noqa: BLE001 - enqueue is best-effort
            logger.warning(
                "Resolve: failed to enqueue Respond.io close for tracking %s: %s",
                getattr(tracking, "id", None),
                exc,
            )

    def admin_test_override_tracking(self, tracking_id: str, updates: dict):
        """
        Apply assignee / timestamp overrides for testing. Recalculates due_at when tier start changes.
        `updates` should be model_dump(exclude_unset=True) from ConversationSLATestOverrideRequest.
        """
        from app.models.user import User
        from app.schemas.sla import ConversationSLATrackingUpdate

        if not updates:
            raise handle_validation_error("No fields to update.")

        tracking = self.get_tracking(tracking_id)
        now_utc = _now_utc()
        old_assigned_to_id = tracking.assigned_to_id
        old_assigned_to = tracking.assigned_to
        old_current_tier_started_at = tracking.current_tier_started_at
        old_initiated_at = tracking.initiated_at
        assign_changed = False
        current_tier_started_changed = False
        initiated_at_changed = False

        if "assigned_to_id" in updates:
            aid = updates["assigned_to_id"]
            if aid is None or (isinstance(aid, str) and not str(aid).strip()):
                setattr(tracking, "assigned_to_id", None)
                setattr(tracking, "assigned_to", None)
                assign_changed = (
                    old_assigned_to_id is not None or old_assigned_to is not None
                )
            else:
                user = self.db.query(User).filter(User.id == str(aid).strip()).first()
                if not user:
                    raise handle_validation_error("User not found for assigned_to_id.")
                setattr(tracking, "assigned_to_id", str(getattr(user, "id")))
                setattr(
                    tracking,
                    "assigned_to",
                    str(user.respond_user_id) if user.respond_user_id is not None else None,
                )
                assign_changed = str(old_assigned_to_id or "") != str(user.id)

        if "current_tier_started_at" in updates:
            raw = updates["current_tier_started_at"]
            if isinstance(raw, str):
                raw = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if not isinstance(raw, datetime):
                raise handle_validation_error("current_tier_started_at must be a datetime.")
            started = _to_aware_utc(raw)
            if started is None:
                raise handle_validation_error("current_tier_started_at must be a valid datetime.")
            setattr(tracking, "current_tier_started_at", started)

            tier = self._resolve_tier_with_clamp(tracking.policy_id, tracking.current_tier)
            if not tier:
                raise handle_validation_error(
                    f"No tier {tracking.current_tier} for this policy."
                )
            response_hours_raw = getattr(tier, "response_hours", None)
            response_hours = float(response_hours_raw) if response_hours_raw is not None else 24.0
            resolution_hours_raw = getattr(tier, "resolution_hours", None)
            resolution_hours = float(resolution_hours_raw) if resolution_hours_raw is not None else 24.0
            setattr(tracking, "due_at", _working_due(self.db, started, response_hours))
            setattr(tracking, "due_at_resolution", _working_due(self.db, started, resolution_hours))
            current_tier_started_changed = (
                _to_aware_utc(
                    old_current_tier_started_at
                    if isinstance(old_current_tier_started_at, datetime)
                    else None
                )
                != started
            )

        if "initiated_at" in updates:
            raw = updates["initiated_at"]
            if isinstance(raw, str):
                raw = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if not isinstance(raw, datetime):
                raise handle_validation_error("initiated_at must be a datetime.")
            initiated = _to_aware_utc(raw)
            if initiated is None:
                raise handle_validation_error("initiated_at must be a valid datetime.")
            setattr(tracking, "initiated_at", initiated)
            initiated_at_changed = (
                _to_aware_utc(old_initiated_at if isinstance(old_initiated_at, datetime) else None)
                != initiated
            )

        # agent_code → agent_id FK. Null / blank clears it.
        routing_changed = False
        if "agent_code" in updates:
            raw_code = updates["agent_code"]
            if raw_code is None or (isinstance(raw_code, str) and not str(raw_code).strip()):
                routing_changed = routing_changed or tracking.agent_id is not None
                setattr(tracking, "agent_id", None)
            else:
                from app.models.access import AccessAgent as _AccessAgent
                _agent = self.db.query(_AccessAgent).filter(
                    _AccessAgent.code == str(raw_code).strip()
                ).first()
                if not _agent:
                    raise handle_validation_error(
                        f"No access agent found with code '{raw_code}'. Create the Access Agent first."
                    )
                routing_changed = routing_changed or str(tracking.agent_id or "") != str(_agent.id)
                setattr(tracking, "agent_id", str(_agent.id))

        # team_set_code stored verbatim. Null / blank clears it.
        if "team_set_code" in updates:
            raw_tsc = updates["team_set_code"]
            new_tsc = str(raw_tsc).strip() if isinstance(raw_tsc, str) and str(raw_tsc).strip() else None
            routing_changed = routing_changed or (getattr(tracking, "team_set_code", None) or None) != new_tsc
            setattr(tracking, "team_set_code", new_tsc)

        # Reopen (is_resolved explicitly False): recompute due dates from the (possibly just
        # overridden) current_tier_started_at so a previously-overdue row gets a clean clock.
        # Skip when current_tier_started_at was supplied in this same request - its branch
        # above already recomputed due_at / due_at_resolution.
        if updates.get("is_resolved") is False and "current_tier_started_at" not in updates:
            started = _to_aware_utc(
                tracking.current_tier_started_at
                if isinstance(tracking.current_tier_started_at, datetime)
                else None
            )
            tier = self._resolve_tier_with_clamp(tracking.policy_id, tracking.current_tier)
            if started and tier:
                response_hours_raw = getattr(tier, "response_hours", None)
                response_hours = float(response_hours_raw) if response_hours_raw is not None else 24.0
                resolution_hours_raw = getattr(tier, "resolution_hours", None)
                resolution_hours = float(resolution_hours_raw) if resolution_hours_raw is not None else 24.0
                setattr(tracking, "due_at", _working_due(self.db, started, response_hours))
                setattr(tracking, "due_at_resolution", _working_due(self.db, started, resolution_hours))

        # Delegate is_responded / is_resolved to the smart update logic (handles auto-calc,
        # field clearing on reopen, validation). Pass False through too so reopen works.
        status_updates = {}
        if "is_responded" in updates and updates["is_responded"] is not None:
            status_updates["is_responded"] = bool(updates["is_responded"])
        if "is_resolved" in updates and updates["is_resolved"] is not None:
            status_updates["is_resolved"] = bool(updates["is_resolved"])

        self.db.commit()
        self.db.refresh(tracking)

        if assign_changed:
            assignee_name = (
                tracking.assigned_user.name
                if getattr(tracking, "assigned_user", None)
                else None
            )
            reason = (
                f"New Assignee {assignee_name}"
                if assignee_name
                else "Assignee cleared"
            )
            self.create_event_log(
                ConversationSLAEventLogCreate(
                    sla_tracking_id=str(getattr(tracking, "id")),
                    event_type="assign",
                    from_tier=int(getattr(tracking, "current_tier", 0)),
                    to_tier=int(getattr(tracking, "current_tier", 0)),
                    event_at=now_utc,
                    reason=reason,
                    assigned_to=(
                        str(getattr(tracking, "assigned_to"))
                        if getattr(tracking, "assigned_to", None) is not None
                        else None
                    ),
                    assigned_to_id=(
                        str(getattr(tracking, "assigned_to_id"))
                        if getattr(tracking, "assigned_to_id", None) is not None
                        else None
                    ),
                    due_at=(
                        _to_aware_utc(getattr(tracking, "due_at"))
                        if isinstance(getattr(tracking, "due_at", None), datetime)
                        else None
                    ),
                )
            )

        if current_tier_started_changed:
            self.create_event_log(
                ConversationSLAEventLogCreate(
                    sla_tracking_id=str(getattr(tracking, "id")),
                    event_type="adjust",
                    from_tier=int(getattr(tracking, "current_tier", 0)),
                    to_tier=int(getattr(tracking, "current_tier", 0)),
                    event_at=now_utc,
                    reason="Adjusted current tier started at for testing.",
                    assigned_to=(
                        str(getattr(tracking, "assigned_to"))
                        if getattr(tracking, "assigned_to", None) is not None
                        else None
                    ),
                    assigned_to_id=(
                        str(getattr(tracking, "assigned_to_id"))
                        if getattr(tracking, "assigned_to_id", None) is not None
                        else None
                    ),
                    due_at=(
                        _to_aware_utc(getattr(tracking, "due_at"))
                        if isinstance(getattr(tracking, "due_at", None), datetime)
                        else None
                    ),
                )
            )

        if initiated_at_changed:
            self.create_event_log(
                ConversationSLAEventLogCreate(
                    sla_tracking_id=str(getattr(tracking, "id")),
                    event_type="adjust",
                    from_tier=int(getattr(tracking, "current_tier", 0)),
                    to_tier=int(getattr(tracking, "current_tier", 0)),
                    event_at=now_utc,
                    reason="Adjusted initiated at for testing.",
                    assigned_to=(
                        str(getattr(tracking, "assigned_to"))
                        if getattr(tracking, "assigned_to", None) is not None
                        else None
                    ),
                    assigned_to_id=(
                        str(getattr(tracking, "assigned_to_id"))
                        if getattr(tracking, "assigned_to_id", None) is not None
                        else None
                    ),
                    due_at=(
                        _to_aware_utc(getattr(tracking, "due_at"))
                        if isinstance(getattr(tracking, "due_at", None), datetime)
                        else None
                    ),
                )
            )

        if routing_changed:
            self.create_event_log(
                ConversationSLAEventLogCreate(
                    sla_tracking_id=str(getattr(tracking, "id")),
                    event_type="adjust",
                    from_tier=int(getattr(tracking, "current_tier", 0)),
                    to_tier=int(getattr(tracking, "current_tier", 0)),
                    event_at=now_utc,
                    reason="Adjusted agent / team set code for testing.",
                    assigned_to=(
                        str(getattr(tracking, "assigned_to"))
                        if getattr(tracking, "assigned_to", None) is not None
                        else None
                    ),
                    assigned_to_id=(
                        str(getattr(tracking, "assigned_to_id"))
                        if getattr(tracking, "assigned_to_id", None) is not None
                        else None
                    ),
                    due_at=(
                        _to_aware_utc(getattr(tracking, "due_at"))
                        if isinstance(getattr(tracking, "due_at", None), datetime)
                        else None
                    ),
                )
            )

        if status_updates:
            updated_tracking = self.update_tracking(
                tracking_id, ConversationSLATrackingUpdate(**status_updates)
            )

            if status_updates.get("is_responded") is True:
                alabel, aid = event_log_assignee_fields(
                    self.db,
                    (
                        str(getattr(updated_tracking, "responded_by"))
                        if getattr(updated_tracking, "responded_by", None) is not None
                        else None
                    ),
                )
                self.create_event_log(
                    ConversationSLAEventLogCreate(
                        sla_tracking_id=tracking_id,
                        event_type="response",
                        from_tier=int(getattr(updated_tracking, "current_tier", 0)),
                        to_tier=int(getattr(updated_tracking, "current_tier", 0)),
                        assigned_to=alabel,
                        assigned_to_id=aid,
                        reason="Responded",
                    )
                )

            if status_updates.get("is_resolved") is True:
                alabel, aid = event_log_assignee_fields(
                    self.db,
                    (
                        str(getattr(updated_tracking, "resolved_by"))
                        if getattr(updated_tracking, "resolved_by", None) is not None
                        else None
                    ),
                )
                self.create_event_log(
                    ConversationSLAEventLogCreate(
                        sla_tracking_id=tracking_id,
                        event_type="resolution",
                        from_tier=int(getattr(updated_tracking, "current_tier", 0)),
                        to_tier=int(getattr(updated_tracking, "current_tier", 0)),
                        assigned_to=alabel,
                        assigned_to_id=aid,
                        reason="Resolved",
                    )
                )

            return updated_tracking

        return self.get_tracking(tracking_id)

    def delete_tracking(self, tracking_id: str):
        """Delete a tracking record, and record that it happened.

        The integration log is written HERE rather than in the route because the delete
        now has TWO callers: the immediate `DELETE /sla/conversation-tracking/{id}` and
        the deferred `sla_tracking.delete` record action, which commits from the sweeper
        or from a poll ten seconds after the click (S6b). A log kept by one caller is a
        log the other silently stops keeping, and this channel is what the SLA
        integration is reconciled against.
        """
        from app.schemas.integration import IntegrationLogCreate
        from app.services.error_handler import AppException
        from app.services.integration_service import IntegrationLogService

        tracking_id = str(tracking_id)

        def _log(status: str, error: str | None = None) -> None:
            # Best-effort: a log that cannot be written must not turn a completed delete
            # into a 500 the caller retries against a row that is already gone.
            try:
                IntegrationLogService(self.db).create_integration_log(
                    IntegrationLogCreate(
                        integration_channel="sla_management",
                        business_table="conversation_sla_tracking",
                        business_id=tracking_id,
                        external_reference=tracking_id,
                        direction="inbound",
                        endpoint="/api/v1/sla/conversation-tracking/{tracking_id}",
                        http_method="DELETE",
                        status=status,
                        error_message=error,
                    ),
                )
            except Exception:
                _module_logger.warning(
                    "integration log for tracking delete %s failed", tracking_id, exc_info=True
                )

        try:
            tracking = self.get_tracking(tracking_id)
            self.db.delete(tracking)
            self.db.commit()
        except AppException:
            # A deliberate 4xx is a refusal, not an integration failure. Logging it as
            # `failed` is what once made every historical sla_management failure one
            # benign idempotency race.
            raise
        except Exception as exc:
            _log("failed", str(exc))
            raise
        _log("success")
        return tracking

    def delete_event_log(self, log_id: str):
        """Delete an event log entry."""
        from app.models.sla import ConversationSLAEventLog
        log = self.db.query(ConversationSLAEventLog).filter(
            ConversationSLAEventLog.id == log_id
        ).first()
        if not log:
            from app.services.error_handler import handle_not_found
            raise handle_not_found("Event Log", log_id)
        self.db.delete(log)
        self.db.commit()
        return {"message": "Event log deleted successfully"}

    def create_event_log(self, event_data: ConversationSLAEventLogCreate):
        """Create an SLA event log entry. Must not update the tracking record or recalculate due_at/due_at_resolution."""
        from app.models.user import User
        from decimal import Decimal, ROUND_HALF_UP
        
        log_dict = event_data.model_dump(exclude_unset=True)
        
        # If assigned_to_id is not provided but assigned_to is, try to find the user
        if not log_dict.get("assigned_to_id") and log_dict.get("assigned_to"):
            assigned_to_value = log_dict["assigned_to"]
            # Try to find user by ID, respond_user_id, or email
            user = self.db.query(User).filter(
                (User.id == assigned_to_value) |
                (User.respond_user_id == assigned_to_value) |
                (User.email == assigned_to_value)
            ).first()
            if user:
                log_dict["assigned_to_id"] = user.id
        
        def _normalize_api_datetime_to_utc(value):
            """
            Normalize API datetime input to timezone-aware UTC.
            Naive values are interpreted as Malaysia local time (UTC+8).
            """
            if value is None:
                return None
            dt = value
            if isinstance(dt, str):
                dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
            if not isinstance(dt, datetime):
                raise handle_validation_error("Invalid datetime payload in event log.")
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=MALAYSIA_TZ)
            return dt.astimezone(timezone.utc)

        # Auto-populate event_at to now (UTC)
        if not log_dict.get("event_at"):
            log_dict["event_at"] = _now_utc()
        else:
            log_dict["event_at"] = _normalize_api_datetime_to_utc(log_dict.get("event_at"))

        # Normalize optional datetime fields if supplied.
        for field in ("from_time", "due_at", "last_reminder_at"):
            if field in log_dict and log_dict.get(field) is not None:
                log_dict[field] = _normalize_api_datetime_to_utc(log_dict.get(field))
        
        # For response or resolution events, auto-populate from_time and duration
        event_type = log_dict.get("event_type", "").lower()
        if event_type in ["response", "resolution"]:
            # Get the tracking record to access initiated_at
            tracking = self.db.query(ConversationSLATracking).filter(
                ConversationSLATracking.id == log_dict["sla_tracking_id"]
            ).first()
            
            if tracking is not None and isinstance(getattr(tracking, "initiated_at", None), datetime):
                # Set from_time to initiated_at (UTC)
                initiated_at = getattr(tracking, "initiated_at", None)
                if isinstance(initiated_at, str):
                    initiated_at = datetime.fromisoformat(initiated_at.replace("Z", "+00:00"))
                log_dict["from_time"] = _to_aware_utc(initiated_at) if isinstance(initiated_at, datetime) else initiated_at
                
                # Calculate duration from initiated_at to event_at (UTC)
                event_at = log_dict["event_at"]
                if isinstance(event_at, str):
                    event_at = datetime.fromisoformat(str(event_at).replace("Z", "+00:00"))
                initiated_at_utc = _to_aware_utc(initiated_at if isinstance(initiated_at, datetime) else None)
                event_at_utc = _to_aware_utc(event_at)
                if initiated_at_utc and event_at_utc:
                    duration_seconds = (event_at_utc - initiated_at_utc).total_seconds()
                    duration_seconds = max(0.0, duration_seconds)  # clamp for bad legacy data
                    duration_hours = Decimal(str(duration_seconds / 3600)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                    log_dict["duration"] = duration_hours

            if not log_dict.get("assigned_to_id") and tracking:
                uid_ref = (
                    tracking.responded_by
                    if event_type == "response"
                    else tracking.resolved_by
                )
                if uid_ref is not None and str(uid_ref).strip():
                    label, uid = event_log_assignee_fields(self.db, str(uid_ref))
                    if uid:
                        log_dict["assigned_to_id"] = uid
                    if label:
                        log_dict["assigned_to"] = label
        
        log = ConversationSLAEventLog(**log_dict)
        self.db.add(log)
        self.db.commit()
        self.db.refresh(log)
        return log
    
    def list_event_logs(
        self,
        page: int = 1,
        limit: int = 50,
        sort_field: Optional[str] = "event_at",
        sort_dir: Optional[str] = "desc",
        tracking_id: Optional[str] = None,
        event_type: Optional[str] = None,
        assigned_to: Optional[str] = None,
        assigned_to_id: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ):
        """List SLA event logs with filtering. date_from/date_to filter on event_at (inclusive, UTC)."""
        from datetime import datetime, timezone
        from sqlalchemy.orm import joinedload
        from app.schemas.common import ListResponse, PaginationResponse
        from app.schemas.sla import ConversationSLAEventLogResponse

        q = self.db.query(ConversationSLAEventLog).options(
            joinedload(ConversationSLAEventLog.assigned_user)
        )

        if tracking_id:
            q = q.filter(ConversationSLAEventLog.sla_tracking_id == tracking_id)
        if event_type:
            q = q.filter(ConversationSLAEventLog.event_type == event_type)
        if assigned_to:
            q = q.filter(ConversationSLAEventLog.assigned_to == assigned_to)
        if assigned_to_id:
            q = q.filter(ConversationSLAEventLog.assigned_to_id == assigned_to_id)
        if date_from:
            try:
                dt = datetime.fromisoformat(date_from.replace("Z", "+00:00"))
                if dt.tzinfo is not None:
                    dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
                q = q.filter(ConversationSLAEventLog.event_at >= dt)
            except ValueError:
                pass
        if date_to:
            try:
                dt = datetime.fromisoformat(date_to.replace("Z", "+00:00"))
                if dt.tzinfo is not None:
                    dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
                end = dt.replace(hour=23, minute=59, second=59, microsecond=999999)
                q = q.filter(ConversationSLAEventLog.event_at <= end)
            except ValueError:
                pass

        total = q.count()

        sort_map = {
            "event_type": ConversationSLAEventLog.event_type,
            "from_tier": ConversationSLAEventLog.from_tier,
            "to_tier": ConversationSLAEventLog.to_tier,
            "event_at": ConversationSLAEventLog.event_at,
            "from_time": ConversationSLAEventLog.from_time,
            "duration": ConversationSLAEventLog.duration,
            "reason": ConversationSLAEventLog.reason,
            "assigned_user_name": ConversationSLAEventLog.assigned_to,
            "assigned_to": ConversationSLAEventLog.assigned_to,
            "created_at": ConversationSLAEventLog.created_at,
        }
        order_col = sort_map.get((sort_field or "").strip(), ConversationSLAEventLog.event_at)
        order_expr = order_col.desc() if str(sort_dir or "desc").lower() == "desc" else order_col.asc()
        logs = q.order_by(order_expr).offset((page - 1) * limit).limit(limit).all()

        # Build response items as dicts (same shape as get_tracking) to avoid ORM->schema validation edge cases
        data = []
        for log in logs:
            item = {
                "id": str(log.id),
                "sla_tracking_id": str(log.sla_tracking_id),
                "event_type": log.event_type,
                "from_tier": log.from_tier,
                "to_tier": log.to_tier,
                "event_at": log.event_at,
                "from_time": log.from_time,
                "duration": log.duration,
                "reason": log.reason,
                "assigned_to": log.assigned_to,
                "assigned_to_id": log.assigned_to_id,
                "due_at": log.due_at,
                "response_time": log.response_time,
                "resolution_time": log.resolution_time,
                "reminder_count": log.reminder_count or 0,
                "last_reminder_at": log.last_reminder_at,
                "created_at": log.created_at,
                "assigned_user": (
                    {
                        "id": log.assigned_user.id,
                        "email": log.assigned_user.email or "",
                        "name": getattr(log.assigned_user, "name", None),
                    }
                    if log.assigned_user
                    else None
                ),
                "assigned_user_name": log.assigned_user.name if log.assigned_user else None,
                "assigned_user_email": log.assigned_user.email if log.assigned_user else None,
            }
            data.append(ConversationSLAEventLogResponse.model_validate(item))

        return ListResponse(
            data=data,
            pagination=PaginationResponse(total=total, page=page, limit=limit),
        )
    
    def get_dashboard_metrics(self):
        """Get dashboard metrics for SLA tracking."""
        from datetime import datetime, timedelta
        from decimal import Decimal

        def _safe_log_hours(log: ConversationSLAEventLog, kind: str) -> Optional[float]:
            try:
                # Prefer duration; fallback to explicit fields for older data.
                duration = getattr(log, "duration", None)
                response_time = getattr(log, "response_time", None)
                resolution_time = getattr(log, "resolution_time", None)
                if isinstance(duration, (Decimal, int, float, str)):
                    return float(duration)
                if kind == "response" and isinstance(response_time, (Decimal, int, float, str)):
                    return float(response_time)
                if kind == "resolution" and isinstance(resolution_time, (Decimal, int, float, str)):
                    return float(resolution_time)
            except (TypeError, ValueError):
                return None
            return None

        now_utc = _now_utc()
        thirty_days_ago = now_utc - timedelta(days=30)

        # Conversation dashboard excludes form trackers (those have their own per-form view).
        all_trackings = (
            self.db.query(ConversationSLATracking)
            .filter(conversation_tracking_scope())
            .all()
        )
        all_logs = self.db.query(ConversationSLAEventLog).all()
        response_logs = [l for l in all_logs if (l.event_type or "").lower() == "response"]
        resolution_logs = [l for l in all_logs if (l.event_type or "").lower() == "resolution"]

        total_trackings = len(all_trackings)
        responded_count = sum(1 for t in all_trackings if bool(getattr(t, "is_responded", False)))
        resolved_count = sum(1 for t in all_trackings if bool(getattr(t, "is_resolved", False)))
        pending_count = total_trackings - responded_count  # not yet responded
        responded_not_resolved_count = sum(
            1
            for t in all_trackings
            if bool(getattr(t, "is_responded", False)) and not bool(getattr(t, "is_resolved", False))
        )
        escalated_count = sum(1 for t in all_trackings if t.escalated_at is not None)

        # Overdue: not responded and due_at passed; not resolved and due_at_resolution passed
        overdue_at_response_count = 0
        overdue_at_resolution_count = 0
        overdue_at_resolution_responded_count = 0
        for t in all_trackings:
            if not bool(getattr(t, "is_responded", False)) and getattr(t, "due_at", None) is not None:
                due_at_raw = getattr(t, "due_at", None)
                due_at_utc = _to_aware_utc(due_at_raw if isinstance(due_at_raw, datetime) else None)
                if due_at_utc and due_at_utc < now_utc:
                    overdue_at_response_count += 1
            if not bool(getattr(t, "is_resolved", False)) and getattr(t, "due_at_resolution", None) is not None:
                due_res_raw = getattr(t, "due_at_resolution", None)
                due_res_utc = _to_aware_utc(due_res_raw if isinstance(due_res_raw, datetime) else None)
                if due_res_utc and due_res_utc < now_utc:
                    overdue_at_resolution_count += 1
                    if bool(getattr(t, "is_responded", False)):
                        overdue_at_resolution_responded_count += 1

        # Average durations (hours) from event logs
        response_times = [_safe_log_hours(l, "response") for l in response_logs]
        response_times = [t for t in response_times if t is not None]
        average_response_time = sum(response_times) / len(response_times) if response_times else 0.0

        resolution_times = [_safe_log_hours(l, "resolution") for l in resolution_logs]
        resolution_times = [t for t in resolution_times if t is not None]
        average_resolution_time = sum(resolution_times) / len(resolution_times) if resolution_times else 0.0

        # Calculate escalation rate
        escalation_rate = float(escalated_count / total_trackings * 100) if total_trackings > 0 else 0.0

        # Trends (last 30 days) from event logs by event_at date.
        response_resolution_trends = []
        for i in range(30):
            date = now_utc - timedelta(days=29 - i)
            date_str = date.date().isoformat()

            day_response_logs = []
            for log in response_logs:
                event_at_raw = getattr(log, "event_at", None)
                event_at = _to_aware_utc(event_at_raw if isinstance(event_at_raw, datetime) else None)
                if event_at and event_at >= thirty_days_ago and event_at.date().isoformat() == date_str:
                    day_response_logs.append(log)
            day_resolution_logs = []
            for log in resolution_logs:
                event_at_raw = getattr(log, "event_at", None)
                event_at = _to_aware_utc(event_at_raw if isinstance(event_at_raw, datetime) else None)
                if event_at and event_at >= thirty_days_ago and event_at.date().isoformat() == date_str:
                    day_resolution_logs.append(log)

            day_response_times = [_safe_log_hours(l, "response") for l in day_response_logs]
            day_response_times = [v for v in day_response_times if v is not None]
            day_resolution_times = [_safe_log_hours(l, "resolution") for l in day_resolution_logs]
            day_resolution_times = [v for v in day_resolution_times if v is not None]

            response_resolution_trends.append({
                "date": date_str,
                "average_response_time": (sum(day_response_times) / len(day_response_times)) if day_response_times else 0.0,
                "average_resolution_time": (sum(day_resolution_times) / len(day_resolution_times)) if day_resolution_times else 0.0,
            })

        # Escalation rates by tier
        escalation_by_tier = {}
        for t in all_trackings:
            if getattr(t, "escalated_at", None) is not None and getattr(t, "current_tier", None) is not None:
                try:
                    tier_value = getattr(t, "current_tier", None)
                    if isinstance(tier_value, (int, str, float)):
                        tier_level = int(tier_value)
                    else:
                        tier_level = 0
                except (TypeError, ValueError):
                    tier_level = 0
                escalation_by_tier[tier_level] = escalation_by_tier.get(tier_level, 0) + 1
        
        escalation_rates_by_tier = [
            {"tier_level": tier_level, "escalation_count": count}
            for tier_level, count in escalation_by_tier.items()
        ]
        
        # Resolution time distribution
        resolution_time_distribution = {
            "resolved": resolved_count,
            "unresolved": total_trackings - resolved_count,
        }
        
        # Status breakdown
        status_breakdown = {
            "resolved": resolved_count,
            "responded_not_resolved": responded_not_resolved_count,
            "pending": pending_count,
        }

        pending_response_overdue_breakdown = {
            "not_yet_overdue": max(0, pending_count - overdue_at_response_count),
            "overdue_at_response": overdue_at_response_count,
        }
        responded_resolution_overdue_breakdown = {
            "not_yet_overdue": max(0, responded_not_resolved_count - overdue_at_resolution_responded_count),
            "overdue_at_resolution": overdue_at_resolution_responded_count,
        }
        
        return {
            "total_trackings": total_trackings,
            "pending_count": pending_count,
            "responded_count": responded_count,
            "responded_not_resolved_count": responded_not_resolved_count,
            "resolved_count": resolved_count,
            "escalated_count": escalated_count,
            "overdue_at_response_count": overdue_at_response_count,
            "overdue_at_resolution_count": overdue_at_resolution_count,
            "average_response_time": average_response_time,
            "average_resolution_time": average_resolution_time,
            "escalation_rate": escalation_rate,
            "response_time_trends": response_resolution_trends,
            "response_resolution_trends": response_resolution_trends,
            "escalation_rates_by_tier": escalation_rates_by_tier,
            "resolution_time_distribution": resolution_time_distribution,
            "status_breakdown": status_breakdown,
            "pending_response_overdue_breakdown": pending_response_overdue_breakdown,
            "responded_resolution_overdue_breakdown": responded_resolution_overdue_breakdown,
        }

    def _publish_conversation_event(
        self,
        tracking: ConversationSLATracking,
        event_type: str,
        *,
        user_ids: Iterable[Optional[str]] = (),
    ) -> None:
        """Poke the live-thread stream about this ticket (UAC AC-K3, slice S4.2).

        Conversation scope ONLY: form-SLA stage rows share this table and belong
        to the form detail pages, not to the ticket drawer or the conversation
        worklist, so they never reach this channel (same discrimination as
        ``conversation_tracking_scope``, applied on the row in hand).

        One event per distinct user whose worklist changed - a reassign or an
        escalation changes TWO pending lists, and poking only the new owner
        leaves the old one showing a task they no longer hold. Each event also
        carries the contact so an open drawer refetches.

        Post-commit and best-effort in every direction: the row is already
        saved, so a broker outage may cost liveness (the FE's slow poll takes
        over) but must never surface as a failure for work that succeeded.
        """
        from app.services.form_sla_service import FORM_SLA_TYPES

        try:
            if getattr(tracking, "source_entity_type", None) in FORM_SLA_TYPES:
                return
            contact_id = self._respond_io_identifier_for_tracking(tracking)
            entity_id = str(getattr(tracking, "id", "") or "") or None
            recipients = {str(u) for u in user_ids if u}
            for user_id in sorted(recipients) or [None]:
                conversation_event_bus.publish(
                    event_type,
                    contact_id=contact_id,
                    user_id=user_id,
                    entity_id=entity_id,
                )
        except Exception:  # noqa: BLE001 - the mutation already committed
            _module_logger.warning(
                "conversation event publish failed for tracking %s (%s).",
                getattr(tracking, "id", "?"),
                event_type,
                exc_info=True,
            )

    def _respond_io_identifier_for_tracking(self, tracking: ConversationSLATracking) -> Optional[str]:
        if tracking.contact and getattr(tracking.contact, "respond_io_id", None):
            s = str(tracking.contact.respond_io_id).strip()
            return s or None
        return None

    def fetch_respond_conversation_for_tracking(
        self, tracking_id: str, limit: int = 50, cursor: Optional[str] = None
    ) -> dict:
        """The ticket panel's page: the local-first thread page for the contact.

        Items run oldest to newest and carry ``source: local`` when stored rows
        exist; one live Respond page is read only when the contact has no stored
        rows. ``cursor`` maps to ``before`` (a message id, older rows).
        """
        tracking = self.get_tracking(tracking_id)
        return self._thread_page_for_contact(
            self._thread_contact_for_tracking(tracking),
            before=cursor,
            after=None,
            around=None,
            limit=limit,
        )

    def _thread_contact_for_tracking(self, tracking: ConversationSLATracking):
        """The contact descriptor the thread reads need, or None when unlinked."""
        from app.services.conversation_thread_service import thread_contact_for

        if not self._respond_io_identifier_for_tracking(tracking):
            return None
        return thread_contact_for(getattr(tracking, "contact", None))

    # -- contact reference resolution --------------------------------------

    def resolve_contact_by_ref(self, contact_ref: str) -> Optional[RespondContact]:
        """A ``RespondContact`` from whatever identifier the caller holds.

        Accepts a Respond.io contact id, a ``respond_contacts.id`` or a phone
        number in any of the shapes the integration lookups already tolerate -
        the SAME resolution order as ``resolve_internal_respond_contact_id``,
        which this delegates to rather than re-deriving.
        """
        internal_id = self.resolve_internal_respond_contact_id(contact_ref)
        if not internal_id:
            return None
        return self.db.query(RespondContact).filter(RespondContact.id == internal_id).first()

    def require_contact(self, contact_ref: str) -> RespondContact:
        contact = self.resolve_contact_by_ref(contact_ref)
        if contact is None:
            raise handle_not_found("Contact", str(contact_ref))
        return contact

    # -- thread reads, shared by the ticket-keyed and contact-keyed routes --

    def _thread_page_for_contact(
        self,
        contact,
        *,
        before: Optional[str],
        after: Optional[str],
        around: Optional[str],
        limit: int,
    ) -> dict:
        """The page read itself, given an already-authorised thread contact.

        Both entry points (ticket-keyed for the drawer, contact-keyed for the
        inbox) end here, so the response shape is one implementation and cannot
        drift between the two surfaces. Authorisation happens BEFORE this - the
        two surfaces have deliberately different gates (AC-N2).
        """
        from app.services import conversation_thread_service as thread_service
        from app.services.integration_service import RespondClient

        if contact is None:
            return thread_service.empty_page(limit=limit, error="No Respond.io contact linked")
        return thread_service.fetch_thread_page(
            self.db,
            contact,
            before=before,
            after=after,
            around=around,
            limit=limit,
            # Per CONTACT, not the deployment default: a contact on any other
            # Respond workspace 401s on the default key and the page silently
            # degrades to the text-only local lane, which is exactly what
            # AC-L7's lane order was revised to avoid.
            client=RespondClient.for_identifier(self.db, contact.respond_io_id),
        )

    def _thread_search_for_contact(self, contact, *, q: str, limit: int) -> dict:
        from app.services import conversation_thread_service as thread_service

        if contact is None:
            return thread_service.empty_search(q=q, error="No Respond.io contact linked")
        return thread_service.search_thread(self.db, contact, q=q, limit=limit)

    def fetch_contact_thread_page(
        self,
        contact_ref: str,
        *,
        before: Optional[str] = None,
        after: Optional[str] = None,
        around: Optional[str] = None,
        limit: int = 50,
    ) -> dict:
        """One scroll-back window of a contact's thread, keyed by the CONTACT
        (AC-N3).

        No ticket scope: the route already required
        ``sla_management.conversations.view``, which is the whole point of the
        inbox - reading a conversation is a permission, not an assignment.
        """
        from app.services.conversation_thread_service import thread_contact_for

        contact = self.require_contact(contact_ref)
        return self._thread_page_for_contact(
            thread_contact_for(contact),
            before=before,
            after=after,
            around=around,
            limit=limit,
        )

    def search_contact_thread(self, contact_ref: str, *, q: str, limit: int = 100) -> dict:
        """In-thread search keyed by the CONTACT (AC-N3)."""
        from app.services.conversation_thread_service import thread_contact_for

        contact = self.require_contact(contact_ref)
        return self._thread_search_for_contact(thread_contact_for(contact), q=q, limit=limit)

    def _ticket_tracking_in_scope(self, tracking_id: str, viewer_user_id: str):
        """This conversation ticket, or a 404.

        One 404 for three different refusals - no such row, a form-SLA stage
        (read from the form record's own chat panel), and a viewer outside the
        ticket's scope - because a 403 on the third would confirm the id to a
        stranger. Same rule and same shape as ``get_ticket_detail`` and
        ``TicketCommentService._tracking_in_scope``.
        """
        from app.services.form_sla_service import FORM_SLA_TYPES

        tracking = self.get_tracking(tracking_id, load_event_logs=False)
        if not tracking or getattr(tracking, "source_entity_type", None) in FORM_SLA_TYPES:
            raise handle_not_found("Conversation SLA tracking", tracking_id)
        if not self.can_user_act_on_tracking(viewer_user_id, tracking):
            raise handle_not_found("Conversation SLA tracking", tracking_id)
        return tracking

    def require_ticket_in_scope(self, tracking_id: str, viewer_user_id: str):
        """Public name for the ticket-scope gate, for routes that need the gate
        without a read attached (the media proxy)."""
        return self._ticket_tracking_in_scope(tracking_id, viewer_user_id)

    def fetch_conversation_thread_page(
        self,
        tracking_id: str,
        *,
        viewer_user_id: str,
        before: Optional[str] = None,
        after: Optional[str] = None,
        around: Optional[str] = None,
        limit: int = 50,
    ) -> dict:
        """One scroll-back window of this tracking's contact thread (AC-L7).

        Assignee-or-manager scoped like every sibling ticket read: this returns
        a contact's WhatsApp conversation, so an unscoped version let any
        authenticated user read any contact's thread from a guessed id. The
        contact-keyed twin (``fetch_contact_thread_page``) shares the read but
        NOT the gate - see AC-N2.
        """
        tracking = self._ticket_tracking_in_scope(tracking_id, viewer_user_id)
        return self._thread_page_for_contact(
            self._thread_contact_for_tracking(tracking),
            before=before,
            after=after,
            around=around,
            limit=limit,
        )

    def search_conversation_thread(
        self, tracking_id: str, *, viewer_user_id: str, q: str, limit: int = 100
    ) -> dict:
        """In-thread message search for this tracking's contact (AC-L8).

        Assignee-or-manager scoped: free text over a stranger's conversation is
        the worse half of an unscoped thread read.
        """
        tracking = self._ticket_tracking_in_scope(tracking_id, viewer_user_id)
        return self._thread_search_for_contact(
            self._thread_contact_for_tracking(tracking), q=q, limit=limit
        )

    def send_conversation_reply_for_tracking(
        self,
        tracking_id: str,
        message: str,
        respond_user_id: str,
        crm_sender_user_id: Optional[str] = None,
        request_url: str = "",
    ) -> dict:
        import logging

        logger = logging.getLogger(__name__)
        tracking = self.get_tracking(tracking_id)
        ident = self._respond_io_identifier_for_tracking(tracking)
        if not ident:
            raise handle_validation_error("No Respond.io contact linked for this tracking.")
        text = (message or "").strip()
        if not text:
            raise handle_validation_error("message is required.")
        from app.services.integration_service import RespondClient
        from app.services.crm_chat_outbound_webhook import (
            enqueue_crm_chat_outbound_webhook,
            resolve_assignee_respond_user_id_from_tracking,
        )

        from app.services.integration_service import log_respond_send

        client = RespondClient()
        request_payload = {"message": {"type": "text", "text": text}}
        try:
            response = client.send_message(ident, text)
        except Exception as e:
            logger.exception("Respond send failed for SLA tracking %s", tracking_id)
            # Record the failure in the Respond outbox before re-raising. This
            # path used to log to stderr only, so a failed conversation reply was
            # invisible in integration_logs.
            log_respond_send(
                self.db,
                business_table="conversation_sla_tracking",
                business_id=str(tracking_id),
                identifier=ident,
                request_payload=request_payload,
                exc=e,
            )
            raise
        log_respond_send(
            self.db,
            business_table="conversation_sla_tracking",
            business_id=str(tracking_id),
            identifier=ident,
            request_payload=request_payload,
            response=response,
        )
        enqueue_crm_chat_outbound_webhook(
            self.db,
            business_table="conversation_sla_tracking",
            business_id=str(tracking_id),
            contact_respond_io_id=ident,
            message_text=text,
            respond_api_response=response if isinstance(response, dict) else None,
            space_id=None,
            crm_sender_user_id=crm_sender_user_id,
            respond_user_id_fallback=respond_user_id,
            assignee_respond_user_id=resolve_assignee_respond_user_id_from_tracking(self.db, tracking),
        )
        return response if isinstance(response, dict) else {}

    def mark_ticket_responded(
        self,
        tracking: ConversationSLATracking,
        *,
        responded_by_user_id: Optional[str] = None,
        reason: str = "Responded from the CRM.",
        responded_at: Optional[datetime] = None,
    ) -> ConversationSLATracking:
        """Stamp is_responded/responded_at/responded_by/response_time on
        ``tracking`` ONLY (UAC AC-E1) - a CRM-authoritative ticket-context send,
        always unambiguous (the caller already picked this exact ticket in the
        drawer). Unlike the n8n Respond-app-reply fallback (the ambiguity guard
        in the ``PUT /{tracking_id}`` route, AC-E3), this never checks sibling
        tickets. No-op when the ticket is already responded - only the FIRST
        reply stops the response clock; later sends on the same ticket are
        ordinary conversation, not a new "first response". "Already responded"
        is decided by the WRITE, not by the read before it: the drawer send and
        n8n's Respond-app-reply fallback are separate requests that can both see
        an unanswered ticket, so the stamp is a conditional UPDATE and only the
        request that actually changed the row writes the response event log.

        ``responded_at`` lets a caller that knows WHEN the reply happened supply
        it (the Respond-app-reply endpoint forwards n8n's `replied_at`), so the
        response time reflects the reply rather than the moment the webhook was
        processed. Defaults to now.
        """
        from decimal import Decimal, ROUND_HALF_UP

        if bool(getattr(tracking, "is_responded", False)):
            return tracking

        responded_at = _to_aware_utc(responded_at) or _now_utc()
        response_time = None
        initiated_at = getattr(tracking, "initiated_at", None)
        if isinstance(initiated_at, datetime):
            initiated_at_utc = _to_aware_utc(initiated_at)
            if initiated_at_utc is not None:
                duration = (responded_at - initiated_at_utc).total_seconds() / 3600
                response_time = Decimal(str(max(0, duration))).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
        responded_by = responded_by_user_id or self._resolve_tracking_assignee_user_id(tracking)

        # The guard above is a read, and a read holds nothing: a concurrent
        # reply passes it too, then overwrites this stamp and adds a second
        # "response" event log for one enquiry. Let the database arbitrate -
        # rowcount 0 means somebody else stopped the clock first, so this call
        # is the no-op the docstring promises.
        won = (
            self.db.query(ConversationSLATracking)
            .filter(
                ConversationSLATracking.id == tracking.id,
                ConversationSLATracking.is_responded.is_(False),
            )
            .update(
                {
                    "is_responded": True,
                    "responded_at": responded_at,
                    "responded_by": responded_by,
                    "response_time": response_time,
                },
                synchronize_session=False,
            )
        )
        self.db.commit()
        self.db.refresh(tracking)
        if not won:
            return tracking

        alabel, aid = event_log_assignee_fields(self.db, responded_by)
        self.create_event_log(
            ConversationSLAEventLogCreate(
                sla_tracking_id=str(tracking.id),
                event_type="response",
                from_tier=int(getattr(tracking, "current_tier", 0) or 0),
                to_tier=int(getattr(tracking, "current_tier", 0) or 0),
                assigned_to=alabel,
                assigned_to_id=aid,
                reason=reason,
            )
        )
        # Only the request that actually stopped the clock pokes (the early
        # return above covers the loser of the race and every later send): the
        # response chip changed for this ticket, nothing else did.
        self._publish_conversation_event(
            tracking,
            conversation_event_bus.EVENT_TICKET_UPDATED,
            user_ids=[getattr(tracking, "assigned_to_id", None)],
        )
        return tracking

    def mark_ticket_responded_by_id(
        self,
        tracking_id: str,
        *,
        responded_by_user_id: Optional[str] = None,
        reason: str = "Responded from the CRM.",
        expect_respond_io_id: Optional[str] = None,
    ) -> Optional[ConversationSLATracking]:
        """``mark_ticket_responded`` for a caller that holds only the ticket id.

        Used by the manual-template worker send: the drawer's "Send template"
        goes through the shared ``/conversation/template-message`` route, which
        queues delivery, so the stamp happens in the worker AFTER the send
        succeeded (an out-of-window template reply must stop the response clock
        exactly like an in-window text reply - otherwise the ticket breaches
        while visibly answered).

        Returns the stamped tracking, or None when there is nothing to stamp.
        Never raises for "not applicable":

      - unknown id -> None (a stale queued job is not an error)
      - form-SLA stage row -> None (different family; form SLA owns its own
          clocks, see conversation_tracking_scope)
      - ``expect_respond_io_id`` set and the ticket's contact is somebody else
          -> None. The tracking id arrives from the client while the identifier
          is resolved server-side from the entity, so this pins the stamp to the
          contact who ACTUALLY received the template.
      - already responded -> ``mark_ticket_responded`` no-ops (only the FIRST
          reply stops the clock).
        """
        from app.services.form_sla_service import FORM_SLA_TYPES

        # Direct query, not get_tracking(): a stale queued job naming a deleted
        # row must be a no-op, not a 404 that fails the RQ job.
        tracking = (
            self.db.query(ConversationSLATracking)
            .filter(ConversationSLATracking.id == str(tracking_id))
            .first()
        )
        if not tracking:
            return None
        if getattr(tracking, "source_entity_type", None) in FORM_SLA_TYPES:
            return None
        if expect_respond_io_id:
            actual = self._respond_io_identifier_for_tracking(tracking)
            if str(actual or "") != str(expect_respond_io_id):
                return None
        return self.mark_ticket_responded(
            tracking, responded_by_user_id=responded_by_user_id, reason=reason
        )

    def apply_agent_reply(
        self,
        *,
        contact_identifier: str,
        replied_by: Optional[str] = None,
        replied_at: Optional[datetime] = None,
    ) -> dict:
        """A staff member replied in the Respond app: stop the right ticket's
        response clock, or say honestly why nothing was stamped (AC-I4).

        This owns the REVISED AC-E3 rule (2026-08-13) in ONE place, keyed on the
        CONTACT first and the replier second:

          1. the contact has exactly ONE open unanswered ticket -> stamp it,
             whoever replied. The response clock measures "did the contact get a
             human response", not "did the assigned person type it": a ticket
             raised on an already-assigned Respond conversation is owned by the
             CRM round-robin pick (AC-E6) while the Respond conversation stays
             with somebody else, so a replier-keyed rule found nothing and let
             the ticket breach while a human was actively answering it.
          2. 2+ open unanswered -> narrow by the replier. Stamp only when they
             own exactly one; otherwise there is no basis to guess which enquiry
             they answered, so change nothing ("ambiguous").
          3. zero open unanswered -> "no_open_ticket". Also the idempotent
             replay: a second delivery of the same reply finds the ticket
             already answered and lands here rather than re-stamping.

        Replaces the n8n `respond-send-user` raw SQL, which selected by
        (arbitrary first policy, is_responded=false, assigned_to=replier) with NO
        CONTACT PREDICATE and PUT once per row - so one reply to one contact
        stamped every unanswered ticket that agent owned across ALL contacts.

        Returns the caller-facing dict {matched, tracking_id, skipped_reason,
        open_ticket_count}; never raises for "nothing to do".
        """
        result = {
            "matched": False,
            "tracking_id": None,
            "skipped_reason": "no_open_ticket",
            "open_ticket_count": 0,
        }

        internal_contact_id = self.resolve_internal_respond_contact_id(contact_identifier)
        if not internal_contact_id:
            # An unknown contact and a contact with nothing open are the same
            # answer to the caller's question, and neither is an error.
            return result

        candidates = (
            self.db.query(ConversationSLATracking)
            .filter(
                ConversationSLATracking.respond_contact_id == internal_contact_id,
                ConversationSLATracking.is_resolved.is_(False),
                ConversationSLATracking.is_responded.is_(False),
                conversation_tracking_scope(),
            )
            .order_by(ConversationSLATracking.created_at.asc())
            .all()
        )
        result["open_ticket_count"] = len(candidates)
        if not candidates:
            return result

        replier_user_id = self._resolve_replier_user_id(replied_by)

        if len(candidates) == 1:
            target = candidates[0]
        else:
            owned = [
                t
                for t in candidates
                if replier_user_id
                and str(getattr(t, "assigned_to_id", "") or "") == str(replier_user_id)
            ]
            if len(owned) != 1:
                result["skipped_reason"] = "ambiguous"
                return result
            target = owned[0]

        self.mark_ticket_responded(
            target,
            responded_by_user_id=replier_user_id,
            reason="Replied from the Respond app.",
            responded_at=replied_at,
        )
        result["matched"] = True
        result["tracking_id"] = str(target.id)
        result["skipped_reason"] = None
        return result

    def _resolve_replier_user_id(self, replied_by: Optional[str]) -> Optional[str]:
        """Map a Respond user id / CRM users.id / email to the internal user id.

        Returns None when it maps to nobody - a Respond user with no CRM account
        is a real, recurring state and must NOT abort the reply signal (the
        single-open-ticket branch stamps regardless of who replied, falling back
        to the ticket's own assignee for attribution).
        """
        from app.models.user import User

        value = str(replied_by or "").strip()
        if not value:
            return None
        user = (
            self.db.query(User)
            .filter(
                (User.respond_user_id == value)
                | (User.id == value)
                | (User.email == value)
            )
            .first()
        )
        return str(user.id) if user else None

    def is_ambiguous_fallback_response(
        self, tracking: ConversationSLATracking, responded_by: Optional[str] = None
    ) -> bool:
        """UAC AC-E3: true when the REPLYING user holds 2+ OPEN, UNANSWERED
        conversation-scope tickets for the same contact - the n8n
        Respond-app-reply fallback has no way to tell which enquiry they
        actually answered, so it must change nothing (the CRM ticket-send
        path, ``mark_ticket_responded``, is authoritative instead).

        ``responded_by`` is the replying user's internal id when the caller
        identified one (e.g. resolved from the payload in ``update_tracking``
        before this call) - it takes priority over ``tracking``'s own
        assignee. This matters: the n8n fallback resolves ``tracking`` itself
        via a SEPARATE contact-level "preferred" lookup that can differ from
        who actually replied, so keying purely on ``tracking.assigned_to_id``
        can miss real ambiguity held by the true replier (FINDING 2a) - and
        with neither an assignee, checking against nobody is meaningless, so
        it is never ambiguous.

        Only UNANSWERED siblings count: one already responded to (still open,
        awaiting resolve) is no longer a candidate for "which ticket did this
        NEW reply answer", so a lone still-unanswered ticket is never
        ambiguous even with answered siblings around (FINDING 2b).

        Form-SLA rows are never ambiguous (per-entity, not contact-shared).
        """
        from app.services.form_sla_service import FORM_SLA_TYPES

        if getattr(tracking, "source_entity_type", None) in FORM_SLA_TYPES:
            return False
        if bool(getattr(tracking, "is_responded", False)):
            return False
        contact_id = getattr(tracking, "respond_contact_id", None)
        target_assignee_id = responded_by or getattr(tracking, "assigned_to_id", None)
        if not contact_id or not target_assignee_id:
            return False
        sibling_count = (
            self.db.query(ConversationSLATracking.id)
            .filter(
                ConversationSLATracking.respond_contact_id == contact_id,
                ConversationSLATracking.assigned_to_id == target_assignee_id,
                ConversationSLATracking.is_resolved.is_(False),
                ConversationSLATracking.is_responded.is_(False),
                conversation_tracking_scope(),
            )
            .count()
        )
        return sibling_count > 1

    def _window_and_template(
        self,
        *,
        identifier: Optional[str],
        respond_contact_id: Optional[str],
        sender_name: str,
        entity_id: str,
    ) -> dict:
        """The composer's two facts: is the 24h window open, and what does the
        out-of-window ``conversation_chat`` template look like filled in.

        ONE core for both surfaces - the ticket drawer reads it off
        ``get_ticket_detail`` (bundled so the drawer opens in one round trip)
        and the Conversations inbox reads it off ``get_contact_window``, so the
        two composers can never disagree about the window they are in. The
        window resolution is a live Respond.io call (15s timeout), so callers
        that are ``async def`` must run this in a threadpool.
        """
        from app.services.respond_chat_template_service import (
            get_chat_template_preview,
            get_window_state_for,
        )

        if not identifier:
            return {
                "window": {"open": False, "expires_at": None},
                "chat_template": {"configured": False, "reason": "no_contact"},
            }
        window = get_window_state_for(
            self.db, identifier=identifier, respond_contact_id=respond_contact_id
        )
        return {
            "window": {"open": bool(window.get("open")), "expires_at": None},
            "chat_template": get_chat_template_preview(
                self.db,
                identifier=identifier,
                respond_contact_id=respond_contact_id,
                chat_use_case="conversation_chat",
                sender_name=sender_name,
                entity_id=entity_id,
                context_builder=None,
            ),
        }

    def get_contact_window(self, contact_ref: str, *, sender_name: str) -> dict:
        """Composer state for a CONTACT with no ticket in hand (UAC AC-N3).

        Same ``{window, chat_template}`` pair the drawer reads off the ticket
        detail, so the inbox composer can smart-send identically: render the
        template inline with the message slot editable when the window is shut,
        a plain textbox when it is open.
        """
        contact = self.require_contact(contact_ref)
        return self._window_and_template(
            identifier=str(getattr(contact, "respond_io_id", "") or "").strip() or None,
            respond_contact_id=str(contact.id),
            sender_name=sender_name,
            entity_id=str(contact.id),
        )

    def get_ticket_detail(
        self,
        tracking_id: str,
        *,
        viewer_user_id: str,
        sender_name: str,
    ) -> dict:
        """Drawer header + composer state for one intervention ticket (UAC AC-C1).

        Assembles the window + out-of-window chat-template preview INLINE (the
        same DB-only helpers the shared composer's standalone endpoints use) so
        the drawer opens in one round trip instead of three. Raises
        ``handle_not_found`` both for a missing tracking and for a viewer
        outside its visibility scope (never leaks existence via a 403).
        """
        from app.services.form_sla_service import FORM_SLA_TYPES

        tracking = self.get_tracking(tracking_id, load_event_logs=False)
        if not tracking or getattr(tracking, "source_entity_type", None) in FORM_SLA_TYPES:
            raise handle_not_found("Conversation SLA tracking", tracking_id)
        if not self.can_user_act_on_tracking(viewer_user_id, tracking):
            raise handle_not_found("Conversation SLA tracking", tracking_id)

        contact = getattr(tracking, "contact", None)
        respond_contact_id = (
            str(tracking.respond_contact_id)
            if getattr(tracking, "respond_contact_id", None)
            else None
        )
        identifier = self._respond_io_identifier_for_tracking(tracking)
        is_resolved = bool(getattr(tracking, "is_resolved", False))
        assigned_user = getattr(tracking, "assigned_user", None)
        team_label = (self._ticket_team_labels([tracking]) or {}).get(str(tracking.id))

        assignee_id = str(assigned_user.id) if assigned_user else None
        is_assignee = assignee_id is not None and assignee_id == str(viewer_user_id)
        # Queue team id for the Takeover call (same value My Team's row passes,
        # `TeamPendingItem.team_id`) - resolved from the VIEWER's own visible
        # teams, since that is the scope Takeover checks against.
        assignee_team_ctx = (
            self._team_label_by_member(self._visible_team_ids(viewer_user_id), [assignee_id]).get(
                assignee_id
            )
            if assignee_id
            else None
        )

        composer = self._window_and_template(
            identifier=identifier,
            respond_contact_id=respond_contact_id,
            sender_name=sender_name,
            entity_id=str(tracking.id),
        )
        window_out = composer["window"]
        chat_template = composer["chat_template"]

        can_send = bool(identifier) and not is_resolved
        # Reading the drawer is wider than resolving it (My Team can open a
        # teammate's ticket) - can_resolve narrows to assignee-or-admin so the
        # button is honest about who may act, not just who may look.
        can_resolve = not is_resolved and self.can_user_resolve_tracking(
            viewer_user_id, tracking
        )

        return {
            "id": str(tracking.id),
            "contact_name": getattr(contact, "name", None),
            "contact_phone": getattr(contact, "phone_number", None),
            "respond_io_id": getattr(contact, "respond_io_id", None),
            "source_message_id": getattr(tracking, "source_message_id", None),
            "source_message_text": getattr(tracking, "source_message_text", None),
            # No separate trigger-message timestamp is stored; initiated_at IS the
            # moment the create request (fired by that message) reached the CRM.
            "source_message_at": (
                tracking.initiated_at.isoformat() if tracking.initiated_at else None
            ),
            "team_label": team_label,
            "assignee_name": (
                (assigned_user.name or assigned_user.email) if assigned_user else None
            ),
            # Viewer-relative, same rule as can_resolve: drives the drawer's
            # Takeover affordance (My Team opened it, so the viewer often is
            # NOT the assignee) without a second RBAC-permission round trip.
            "is_assignee": is_assignee,
            # The assignee's queue team id, resolved from the viewer's OWN
            # visible teams - the same value My Team's row Takeover passes.
            # None when the assignee is outside the viewer's visible teams
            # (an admin acting outside their own team hierarchy).
            "assignee_team_id": assignee_team_ctx[0] if assignee_team_ctx else None,
            "policy_name": tracking.policy.name if tracking.policy else None,
            "initiated_at": (
                tracking.initiated_at.isoformat() if tracking.initiated_at else None
            ),
            "current_tier": tracking.current_tier,
            "escalated_at": (
                tracking.escalated_at.isoformat() if tracking.escalated_at else None
            ),
            "escalation_reason": getattr(tracking, "escalation_reason", None),
            "due_at": tracking.due_at.isoformat() if tracking.due_at else None,
            "due_at_resolution": (
                tracking.due_at_resolution.isoformat()
                if tracking.due_at_resolution
                else None
            ),
            "is_responded": bool(tracking.is_responded),
            "responded_at": (
                tracking.responded_at.isoformat() if tracking.responded_at else None
            ),
            # Same counter the worklist row reads: the drawer's chips mark an
            # extended deadline too, so extending from the drawer shows there.
            "extension_count": int(getattr(tracking, "extension_count", 0) or 0),
            "is_resolved": is_resolved,
            "resolved_at": (
                tracking.resolved_at.isoformat() if tracking.resolved_at else None
            ),
            "can_send": can_send,
            "can_resolve": can_resolve,
            # Bounded by R1 (Respond.io OpenAPI): no sticker, no reply-to param.
            "send_capabilities": ["text", "attachment"],
            "window": window_out,
            "chat_template": chat_template,
        }

    def send_ticket_message(
        self,
        tracking_id: str,
        *,
        text: str,
        files: list,
        reply_to_message_id: Optional[str],
        reply_to_excerpt: Optional[str],
        sender_user_id: Optional[str],
        sender_name: str,
    ) -> dict:
        """CRM-native ticket reply (UAC AC-D1/D2/D3, AC-E1).

        Synchronous - the drawer needs the actually-attempted payload back
        immediately to render the delivered state. ``files`` is a list of
        ``(content: bytes, filename: str, mime: str)`` tuples. Text-only sends
        reuse ``send_chat_message_for`` VERBATIM (AC-D2, the same smart-send
        machinery the complaint/stock-inquiry/purchase-request chat panels
        already use - not forked). Attachments upload through CRM storage
        first, then ``RespondClient.send_attachment`` (no template fallback
        exists for media, R1 - a closed window is a hard refusal, not a
        silent drop). ``text`` is expected to already carry the composer's
        ">"-quote prefix when replying (R1); ``reply_to_*`` are audit-only and
        are never sent to Respond. On success, stamps THIS ticket's response
        clock only; sibling tickets for the same contact are untouched.

        FE CONTRACT - multi-attachment sends (FINDING 3 code-review fix):
        the window is resolved ONCE for the whole call (not re-checked per
        file); a caption ships as its own text turn WITH/BEFORE the first
        attachment attempt, so it is never lost to an unrelated later
        attachment failure; attachments are then sent SEQUENTIALLY, stopping
        at the FIRST failure (Respond delivers in order - nothing after a
        failure is attempted, so a caller retrying does not resend files that
        already landed). A per-file failure is NEVER raised as an exception -
        the whole send always returns 200 with a structured result:

            {
              "sent_as": "attachment",
              "rendered_text": str,
              "flattened": False,
              "window": {"open": bool, "expires_at": None},
              "attachments": {
                  "delivered": ["a.pdf", "b.pdf"],       # filenames, in order, that reached Respond
                  "failed": {"filename": "c.pdf", "error": "..."} | None,
              },
            }

        ``attachments`` is ``None`` on the text-only path. The response clock
        (``mark_ticket_responded``) is stamped whenever ANYTHING reached the
        contact - the caption, at least one attachment, or both - even when
        ``attachments.failed`` is set. The FE is expected to render the
        delivered files as sent and the failed one with a retry affordance
        (not implemented yet - tracked separately from this fix).
        """
        from app.services.crm_chat_outbound_webhook import notify_human_ticket_send
        from app.services.form_sla_service import FORM_SLA_TYPES

        tracking = self.get_tracking(tracking_id, load_event_logs=False)
        if not tracking:
            raise handle_not_found("Conversation SLA tracking", tracking_id)
        if getattr(tracking, "source_entity_type", None) in FORM_SLA_TYPES:
            raise handle_validation_error(
                "This is a form-SLA stage; reply from the form record's chat panel instead."
            )
        if bool(getattr(tracking, "is_resolved", False)):
            raise handle_validation_error("Cannot send a message on a resolved ticket.")
        identifier = self._respond_io_identifier_for_tracking(tracking)
        if not identifier:
            raise handle_validation_error("No Respond.io contact linked; cannot send a message.")
        respond_contact_id = (
            str(tracking.respond_contact_id)
            if getattr(tracking, "respond_contact_id", None)
            else None
        )

        clean_text = (text or "").strip()
        if not clean_text and not files:
            raise handle_validation_error("Provide message text or at least one attachment.")

        business_table = "conversation_sla_tracking"
        business_id = str(tracking.id)

        result = self._deliver_conversation_message(
            identifier=identifier,
            respond_contact_id=respond_contact_id,
            business_table=business_table,
            business_id=business_id,
            text=text,
            files=files,
            sender_user_id=sender_user_id,
            sender_name=sender_name,
        )
        anything_delivered = result.pop("_delivered")
        first_respond_response = result.pop("_first_response")
        sent_as = result["sent_as"]
        rendered_text = result["rendered_text"]

        # AC-E1: stamp THIS ticket's response clock only, and only if
        # something ACTUALLY reached the contact (a total attachment failure
        # with no caption stamps nothing). Best-effort - the message already
        # reached the contact; a stamping bug must never turn a delivered
        # send into a 500 for the assignee.
        if anything_delivered:
            try:
                reason_bits = [f"sent_as={sent_as}"]
                if reply_to_message_id:
                    reason_bits.append(f"reply_to_message_id={reply_to_message_id}")
                if reply_to_excerpt:
                    reason_bits.append("quoted_reply=true")
                self.mark_ticket_responded(
                    tracking,
                    responded_by_user_id=sender_user_id,
                    reason=f"CRM reply ({', '.join(reason_bits)})",
                )
            except Exception:  # noqa: BLE001
                _module_logger.warning(
                    "send_ticket_message: response-clock stamp failed for %s",
                    tracking_id,
                    exc_info=True,
                )

            # AC-J1: tell n8n a HUMAN answered, so the bot stops replying to this
            # contact (is_human_intervened + ht timeout lane), exactly as a manual
            # reply from the Respond app does. Once per drawer send, whatever it
            # carried; itself best-effort (notify_human_ticket_send never raises).
            notify_human_ticket_send(
                self.db,
                tracking_id=business_id,
                contact_respond_io_id=identifier,
                message_text=rendered_text,
                respond_api_response=first_respond_response,
                sender_user_id=sender_user_id,
            )

        return result

    def _deliver_conversation_message(
        self,
        *,
        identifier: str,
        respond_contact_id: Optional[str],
        business_table: str,
        business_id: str,
        text: str,
        files: list,
        sender_user_id: Optional[str],
        sender_name: str,
    ) -> dict:
        """Put a message (text and/or attachments) in front of a contact.

        The delivery half of a CRM-native reply, with nothing ticket-specific
        left in it: the ticket drawer (``send_ticket_message``) and the
        Conversations inbox (``send_contact_message``) both come through here,
        so the smart-send behaviour, the multi-attachment contract and the
        Respond outbox rows are ONE implementation rather than two that drift.
        Callers own their own authorisation, their own ``business_table`` /
        ``business_id`` for the outbox, and whatever they do afterwards (a
        response clock, a human-send signal).

        The multi-attachment FE contract is documented on
        ``send_ticket_message``; ``_delivered`` / ``_first_response`` are
        internal and never leave the two callers.
        """
        from app.services.error_handler import AppException
        from app.services.respond_chat_template_service import (
            send_chat_attachment_for,
            send_chat_message_for,
            upload_chat_attachment,
        )
        from app.services.respond_messaging_service import get_window_state
        from app.services.conversation_thread_service import (
            mirror_outgoing_send as _mirror_outgoing_send,
        )

        clean_text = (text or "").strip()
        if not clean_text and not files:
            raise handle_validation_error("Provide message text or at least one attachment.")

        attachments_result: Optional[dict] = None
        anything_delivered = False
        # Respond's acknowledgement for the FIRST message that actually landed.
        # The human-send webhook carries its messageId so the direct lane and
        # Respond's own outgoing-message trigger mirror the same id (AC-J5).
        first_respond_response: Optional[dict] = None
        if files:
            # FINDING 3: resolve the window ONCE for the whole send (each
            # resolution is a live Respond HTTP call, 15s timeout) instead of
            # once per file, and pass it down so send_chat_attachment_for
            # skips its own per-call lookup.
            window = get_window_state(self.db, identifier, respond_contact_id=respond_contact_id)
            window_state = {"open": window.get("open"), "last_incoming_at": window.get("last_incoming_at")}

            # A caption ships as its own text turn WITH/BEFORE the first
            # attachment - Respond's attachment message has no reliably-
            # supported caption param (R1) - so it is never lost to a LATER
            # attachment failing.
            if clean_text:
                caption_result = send_chat_message_for(
                    self.db,
                    identifier=identifier,
                    respond_contact_id=respond_contact_id,
                    text=clean_text,
                    chat_use_case="conversation_chat",
                    business_table=business_table,
                    business_id=business_id,
                    sender_name=sender_name,
                    created_by=sender_user_id,
                )
                first_respond_response = caption_result.get("response")
                anything_delivered = True
                _mirror_outgoing_send(
                    self.db,
                    identifier=identifier,
                    respond_contact_id=respond_contact_id,
                    response=caption_result.get("response"),
                    message={"type": "text", "text": caption_result.get("rendered_text") or clean_text},
                )

            # Sequential, never all-or-nothing: a failure on file N must not
            # undo files 1..N-1, which already reached the contact. Stop at
            # the FIRST failure (Respond delivers in order; a caller retrying
            # would otherwise resend files that already landed) and report
            # exactly what got through instead of raising.
            delivered: list[str] = []
            failed: Optional[dict] = None
            for content, filename, mime in files:
                try:
                    uploaded = upload_chat_attachment(
                        business_table=business_table,
                        business_id=business_id,
                        content=content,
                        filename=filename,
                        mime=mime,
                    )
                    sent = send_chat_attachment_for(
                        self.db,
                        identifier=identifier,
                        respond_contact_id=respond_contact_id,
                        attachment_type=uploaded["kind"],
                        url=uploaded["url"],
                        business_table=business_table,
                        business_id=business_id,
                        created_by=sender_user_id,
                        window=window,
                    )
                    if first_respond_response is None:
                        response = sent.get("response")
                        first_respond_response = (
                            response if isinstance(response, dict) else None
                        )
                    _mirror_outgoing_send(
                        self.db,
                        identifier=identifier,
                        respond_contact_id=respond_contact_id,
                        response=sent.get("response"),
                        message={
                            "type": "attachment",
                            "attachment": {
                                "type": uploaded["kind"],
                                "url": uploaded["url"],
                                "fileName": filename,
                            },
                        },
                    )
                except AppException as e:
                    # AppException.detail is always the {message, detail, code}
                    # dict (see error_handler.AppException.__init__) - prefer
                    # the underlying Respond error string (`detail`) over the
                    # generic user-facing `message`.
                    _detail = e.detail if isinstance(e.detail, dict) else {}
                    error_message = _detail.get("detail") or _detail.get("message") or str(e)
                    failed = {"filename": filename, "error": str(error_message)}
                    break
                except Exception as e:  # noqa: BLE001
                    # The per-file contract cannot depend on the failure being
                    # an AppException: the upload can die on a botocore
                    # ClientError or a corrupt-image error, and letting that
                    # escape is precisely what the contract forbids - the
                    # caption and files 1..N-1 are already with the contact,
                    # the response clock would never be stamped, and the
                    # caller's retry would re-send what already landed.
                    _module_logger.warning(
                        "send_ticket_message: attachment %s failed for %s",
                        filename,
                        business_id,
                        exc_info=True,
                    )
                    failed = {"filename": filename, "error": str(e) or e.__class__.__name__}
                    break
                delivered.append(filename)

            if delivered:
                anything_delivered = True
            attachments_result = {"delivered": delivered, "failed": failed}
            sent_as = "attachment"
            rendered_text = clean_text or f"{len(files)} attachment(s) sent"
            flattened = False
        else:
            result = send_chat_message_for(
                self.db,
                identifier=identifier,
                respond_contact_id=respond_contact_id,
                text=clean_text,
                chat_use_case="conversation_chat",
                business_table=business_table,
                business_id=business_id,
                sender_name=sender_name,
                created_by=sender_user_id,
            )
            sent_as = result["sent_as"]
            rendered_text = result["rendered_text"]
            flattened = result["flattened"]
            window_state = result["window_state"]
            first_respond_response = result.get("response")
            anything_delivered = True
            _mirror_outgoing_send(
                self.db,
                identifier=identifier,
                respond_contact_id=respond_contact_id,
                response=result.get("response"),
                message={"type": "text", "text": rendered_text or clean_text},
            )

        return {
            "sent_as": sent_as,
            "rendered_text": rendered_text,
            "flattened": flattened,
            "window": {
                "open": bool(window_state.get("open")),
                "expires_at": None,
            },
            "attachments": attachments_result,
            # Private to the two callers below and stripped before the route
            # sees it: "did anything actually reach the contact" is what gates
            # the response clock and the human-send signal, and the first
            # acknowledgement carries the messageId that signal mirrors (AC-J5).
            "_delivered": anything_delivered,
            "_first_response": first_respond_response,
        }

    def my_open_tickets_for_contact(
        self, respond_contact_id: str, user_id: Optional[str]
    ) -> list:
        """The caller's OPEN conversation-scope tickets for one contact.

        ``conversation_tracking_scope()`` is mandatory: form-SLA stage rows
        share this table, and a complaint stage assigned to the caller would
        otherwise look like an enquiry to stamp a WhatsApp reply onto.
        """
        if not user_id:
            return []
        return (
            self.db.query(ConversationSLATracking)
            .filter(
                ConversationSLATracking.respond_contact_id == str(respond_contact_id),
                ConversationSLATracking.assigned_to_id == str(user_id),
                ConversationSLATracking.is_resolved.is_(False),
                conversation_tracking_scope(),
            )
            .order_by(ConversationSLATracking.initiated_at.asc())
            .all()
        )

    def send_contact_message(
        self,
        contact_ref: str,
        *,
        text: str,
        files: list,
        reply_to_message_id: Optional[str] = None,
        reply_to_excerpt: Optional[str] = None,
        sender_user_id: Optional[str],
        sender_name: str,
    ) -> dict:
        """Reply to a CONTACT from the Conversations inbox (UAC AC-N2).

        Stamped onto the sender's own OPEN conversation ticket for this contact
        when they hold EXACTLY ONE - then it IS that ticket's first response,
        indistinguishable from a drawer send, and it goes through
        ``send_ticket_message`` so the response clock, the event log and the
        human-send signal all behave identically. Zero or several: the message
        still goes, but unstamped. Guessing which of two open enquiries a reply
        answers would corrupt both clocks, and refusing to send would make the
        inbox useless for exactly the colleague AC-N2 opened it for.

        Either way the Respond outbox is written (by the shared send helpers)
        and the AC-J human-intervention signal fires, so the bot stands down for
        this contact whichever lane carried the message.

        Response shape is the ticket send's, plus ``stamped_ticket_id`` (null on
        the unstamped lane) so the caller can tell which one happened.

        ``reply_to_*`` behave exactly as they do on the drawer send: audit-only,
        never sent to Respond (it has no reply-to parameter - the ">" quote
        prefix is composed by the caller and ``text`` goes verbatim). On the
        stamped lane they land on that ticket's response event log; on the
        unstamped lane there is no ticket to write them onto, so they are
        accepted and dropped rather than refused - the alternative is a reply
        the colleague cannot quote.
        """
        from app.services.crm_chat_outbound_webhook import notify_human_contact_send

        contact = self.require_contact(contact_ref)
        identifier = str(getattr(contact, "respond_io_id", "") or "").strip()
        if not identifier:
            raise handle_validation_error(
                "No Respond.io contact linked; cannot send a message."
            )

        mine = self.my_open_tickets_for_contact(str(contact.id), sender_user_id)
        if len(mine) == 1:
            result = self.send_ticket_message(
                str(mine[0].id),
                text=text,
                files=files,
                reply_to_message_id=reply_to_message_id,
                reply_to_excerpt=reply_to_excerpt,
                sender_user_id=sender_user_id,
                sender_name=sender_name,
            )
            result["stamped_ticket_id"] = str(mine[0].id)
            return result

        result = self._deliver_conversation_message(
            identifier=identifier,
            respond_contact_id=str(contact.id),
            # The outbox row is keyed on the CONTACT, because no one ticket owns
            # this send. `integration_log.business_id` is a uuid column and
            # `respond_contacts.id` holds a uuid, so it fits.
            business_table="respond_contacts",
            business_id=str(contact.id),
            text=text,
            files=files,
            sender_user_id=sender_user_id,
            sender_name=sender_name,
        )
        delivered = result.pop("_delivered")
        first_respond_response = result.pop("_first_response")
        if delivered:
            notify_human_contact_send(
                self.db,
                respond_contact_id=str(contact.id),
                contact_respond_io_id=identifier,
                message_text=result["rendered_text"],
                respond_api_response=first_respond_response,
                sender_user_id=sender_user_id,
            )
        result["stamped_ticket_id"] = None
        return result

    def send_contact_template_message(
        self,
        contact_ref: str,
        *,
        template_id: str,
        params: dict,
        sender_user_id: Optional[str],
        sender_name: str,
    ) -> dict:
        """Send an approved template to a CONTACT from the inbox (UAC AC-N2).

        The out-of-window half of ``send_contact_message``, and it stamps by the
        same rule: exactly one open conversation ticket of the sender's for this
        contact makes this that ticket's first response (response clock +
        human-intervention signal, indistinguishable from the drawer's template
        send); zero or several leaves it unstamped against the contact, because
        guessing which enquiry a template answers would corrupt both clocks.

        Delivery is synchronous through the shared
        ``deliver_manual_template_now``: the operator gets the real outcome and
        the Respond outbox row exists either way. The DB-only precheck runs
        first so a bad template id or a missing parameter is an inline 404/400
        rather than a delivered surprise.
        """
        from app.services.crm_chat_outbound_webhook import (
            notify_human_contact_send,
            notify_human_ticket_send,
        )
        from app.services.respond_chat_template_service import (
            deliver_manual_template_now,
            precheck_manual_template,
        )

        contact = self.require_contact(contact_ref)
        identifier = str(getattr(contact, "respond_io_id", "") or "").strip()
        if not identifier:
            raise handle_validation_error(
                "No Respond.io contact linked; cannot send a template."
            )

        pre = precheck_manual_template(self.db, template_id=template_id, params=params)

        mine = self.my_open_tickets_for_contact(str(contact.id), sender_user_id)
        stamped = mine[0] if len(mine) == 1 else None
        business_table = "conversation_sla_tracking" if stamped else "respond_contacts"
        business_id = str(stamped.id) if stamped else str(contact.id)

        sent = deliver_manual_template_now(
            self.db,
            identifier=identifier,
            template_id=template_id,
            params=params,
            business_table=business_table,
            business_id=business_id,
            sender_user_id=sender_user_id,
        )
        response = sent.get("response") if isinstance(sent, dict) else None

        # Post-send side effects: the contact already has the message, so each
        # one warns rather than raising (notify_human_* never raise by design).
        if stamped is not None:
            try:
                self.mark_ticket_responded(
                    stamped,
                    responded_by_user_id=sender_user_id,
                    reason="CRM reply (sent_as=template)",
                )
            except Exception:  # noqa: BLE001
                _module_logger.warning(
                    "send_contact_template_message: response-clock stamp failed for %s",
                    stamped.id,
                    exc_info=True,
                )
            notify_human_ticket_send(
                self.db,
                tracking_id=str(stamped.id),
                contact_respond_io_id=identifier,
                message_text=pre.get("rendered_body") or "",
                respond_api_response=response if isinstance(response, dict) else None,
                sender_user_id=sender_user_id,
            )
        else:
            notify_human_contact_send(
                self.db,
                respond_contact_id=str(contact.id),
                contact_respond_io_id=identifier,
                message_text=pre.get("rendered_body") or "",
                respond_api_response=response if isinstance(response, dict) else None,
                sender_user_id=sender_user_id,
            )

        return {
            "ok": True,
            "queued": False,
            "template_name": pre["template_name"],
            "rendered_body": pre["rendered_body"],
            "stamped_ticket_id": str(stamped.id) if stamped is not None else None,
        }
