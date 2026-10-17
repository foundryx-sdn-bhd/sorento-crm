"""Form SLA orchestrator: starts/responds/resolves SLA trackers for forms.

Forms supported (source_entity_type values): stock_inquiry, purchase_request,
sponsorship_form, complaint. Configuration lives in form_sla_configs and is
queried by source_entity_type. Trackers themselves reuse conversation_sla_tracking
(same table) - a tracker for a form has source_entity_type set to the form's
type, source_entity_id set to the form row's id.

A single emit_event() call is dropped at every form-service state transition;
the orchestrator decides whether to start/respond/resolve based on the
configured event names. Multi-stage chains spawn the next stage on resolve.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.models.access import AccessAgent
from app.models.notification import Notification
from app.models.sla import (
    ConversationSLATracking,
    FormSLAConfig,
    SLAPolicyTier,
)
from app.services.document_number import suffix_revision
from app.services.error_handler import handle_validation_error
from app.services.notification_service import NotificationService

logger = logging.getLogger(__name__)


FORM_SLA_TYPES = (
    "stock_inquiry",
    "purchase_request",
    "sponsorship_form",
    "complaint",
    "ticket",
    # Has carried an active form_sla_configs row since 2026-08-01. Omitting it made
    # GET /form-sla-config 500 on response validation for every caller.
    "workflow_submission",
    # Price tag request: single marketing stage, configured via form_sla_configs.
    "price_tag_request",
    # SR-CORE: the detail page carries the SLA tab; no stage config ships, so it reads empty.
    "sample_request",
)


def form_sla_agent_codes(db: Session) -> set[str]:
    """Agent codes that own a form-SLA pipeline (any row in form_sla_configs).

    Data-driven classifier: an AccessAgent is a FORM-SLA agent iff its code
    appears in form_sla_configs.agent_code - its routing is driven by
    FormSLAConfig stages, never by assignee→team membership derivation. Every
    other agent is a CONVERSATION-SLA agent (routing derived from the assignee's
    tier-1 team membership).

    Used by the tier-1 membership invariant + assignee-team derivation so a user
    may sit in many form-SLA tier-1 teams but at most one CONVERSATION-SLA tier-1
    team (the only case where derivation would be ambiguous).
    """
    return {
        code
        for (code,) in db.query(FormSLAConfig.agent_code).distinct().all()
        if code
    }


def _utc_naive_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _working_clock_start_naive(db: Session, start_dt: datetime) -> datetime:
    """Normalize an SLA clock start to the next working-window open.

    A form submitted when nobody is working (weekend, public holiday, before open,
    after close) starts its clock at the next window open instead of the raw submit
    instant - so the responder gets the whole window the policy promises. Returns
    naive UTC to match the tracker columns; a start already inside a window is
    returned unchanged. Falls back to ``start_dt`` if the work calendar is
    unavailable/misconfigured."""
    try:
        from app.services.calendar_service import CalendarService

        out = CalendarService(db).next_working_window_open(start_dt)
        if out is not None:
            return out
    except Exception:  # pragma: no cover - defensive; never break form SLA start/escalate
        logger.warning(
            "form working clock-start normalization failed; using the raw start.",
            exc_info=True,
        )
    return start_dt


def _working_due_naive(db: Session, start_dt: datetime, hours: float) -> datetime:
    """Forward form-SLA due date in *working days*: convert the policy hours to days
    (÷24) and add that many working days (skipping weekends + KL public holidays),
    keeping the time-of-day. E.g. 72h → +3 working days. The start is first
    normalized to the next working-window open (see
    :func:`_working_clock_start_naive`), so Sat 09:01 + 24h is due Tue at the window
    open, not Mon 09:01. Returns naive UTC to match the tracker columns. Falls back
    to calendar hours if the work calendar is unavailable/misconfigured."""
    try:
        from app.services.calendar_service import CalendarService
        out = CalendarService(db).add_working_days_from_hours(start_dt, hours)
        if out is not None:
            return out
    except Exception:  # pragma: no cover - defensive; never break form SLA start/escalate
        logger.warning(
            "form working-days due calc failed; falling back to calendar hours.", exc_info=True
        )
    return start_dt + timedelta(hours=float(hours))


# (table, number column, carries portal revisions) per form type - for resolving a
# human-readable document number instead of showing the raw UUID in notifications.
# The third flag says whether the table has the denormalized `revision_no` column,
# so the number can be rendered at its revision (UAC N1/N5).
_ENTITY_NUMBER_SOURCE: dict = {
    "complaint": ("complaints", "complaint_number", False),
    "stock_inquiry": ("stock_inquiries", "inquiry_number", True),
    "purchase_request": ("purchase_requests", "request_number", True),
    "sponsorship_form": ("purchase_requests", "request_number", True),
    "ticket": ("tickets", "ticket_number", False),
    # r9 D14: without this the price tag SLA notification showed a uuid prefix,
    # which tells the reader nothing about which request is late.
    "price_tag_request": ("price_tag_requests", "doc_number", True),
    "sample_request": ("projects.sample_requests", "request_no", False),
}


def _resolve_entity_number(db: Session, source_entity_type: str, source_entity_id: str) -> Optional[str]:
    """Human-readable document number (e.g. CMP-2026-0001) for a form row; None if
    not resolvable (caller falls back to a generic label, never the UUID).

    Revisable types render at their revision (``SI-26-0184-R2``), so an SLA
    notification names the version the recipient is actually being asked to work.
    """
    src = _ENTITY_NUMBER_SOURCE.get(source_entity_type)
    if not src or not source_entity_id:
        return None
    table, col, has_revision = src
    try:
        from sqlalchemy import text as _text

        select_cols = f"{col}, revision_no" if has_revision else f"{col}, 0"
        row = db.execute(
            _text(f"SELECT {select_cols} FROM {table} WHERE id = CAST(:id AS uuid)"),
            {"id": str(source_entity_id)},
        ).fetchone()
        val = (row[0] if row else None)
        if not val:
            return None
        return suffix_revision(str(val), row[1] if row else 0) or None
    except Exception:  # pragma: no cover - never break notifications on lookup
        logger.warning("SLA notify: number lookup failed for %s/%s", source_entity_type, source_entity_id, exc_info=True)
        return None


def _full_form_link(source_entity_type: str, source_entity_id: str) -> str:
    """Absolute frontend URL for the form detail page (falls back to a relative path)."""
    from app.config import settings

    path = _form_detail_link(source_entity_type, source_entity_id)
    base = (getattr(settings, "frontend_base_url", None) or "").strip().rstrip("/")
    return f"{base}{path}" if base else path


def _ticket_deep_link(base_url: Optional[str], tracking_id) -> str:
    """The CRM deep link for a conversation ticket: ``{base}/?ticket=<id>``.

    Same target the dashboard "My Pending" drawer opens and the email / in-app
    notification links to (UAC AC-G1). Relative when no frontend base URL is
    configured, never empty and never the Respond inbox.
    """
    base = (base_url or "").strip().rstrip("/")
    return f"{base}/?ticket={tracking_id}"


def _fmt_due(due_at) -> Optional[str]:
    """Format a naive-UTC due_at as readable KL wall time, e.g. '22 May 2026, 10:00 AM'."""
    if due_at is None:
        return None
    try:
        from datetime import timezone as _tz
        from zoneinfo import ZoneInfo

        dt = due_at
        aware = dt.replace(tzinfo=_tz.utc) if dt.tzinfo is None else dt
        local = aware.astimezone(ZoneInfo("Asia/Kuala_Lumpur"))
        return local.strftime("%d %b %Y, %I:%M %p")
    except Exception:  # pragma: no cover
        return str(due_at)


# Per-stage human action verbs for the escalation reason, keyed by
# (source_entity_type, team_set_code) - the same pair that uniquely identifies a
# form-SLA stage. `response` is the action that stops the response clock
# (respond_event); `resolution` is the action that resolves the stage
# (resolve_event). Stages where a single action satisfies both clocks omit
# `response` (any breach maps to `resolution`). Grounded in form_sla_configs.
_STAGE_ACTION_LABELS: dict[tuple[str, str], dict[str, str]] = {
    ("complaint", "complaint"): {
        "response": "submit the technical team response",
        "resolution": "approve or reject",
    },
    ("complaint", "customer_service"): {"resolution": "process the complaint (CS)"},
    ("stock_inquiry", "project_sales"): {
        "resolution": "approve (send to purchasing) or reject",
    },
    ("stock_inquiry", "purchasing"): {
        "response": "send the purchasing response",
        "resolution": "respond or decide on purchasing",
    },
    ("purchase_request", "project_sales"): {"resolution": "send the request for approval"},
    ("purchase_request", "project_sales_manager"): {"resolution": "approve or reject"},
    ("purchase_request", "customer_service"): {"resolution": "process the request (CS)"},
    # SF mirrors PR in prod (main stage resolves on send_for_approval, no response
    # clock; the approve/reject happens at the project_sales_manager stage).
    ("sponsorship_form", "project_sales"): {"resolution": "send the form for approval"},
    ("sponsorship_form", "project_sales_manager"): {"resolution": "approve or reject"},
    ("sponsorship_form", "customer_service"): {"resolution": "process the form (CS)"},
    ("ticket", "it_admin"): {
        "response": "respond to the ticket",
        "resolution": "resolve the ticket",
    },
}


def _stage_action_verb(
    source_entity_type: Optional[str], team_set_code: Optional[str], clock: str
) -> Optional[str]:
    """Human verb for the breached action of a stage. `clock` in {response, resolution}.
    Falls back to the resolution verb when the stage has no distinct response action
    (response == resolution), then to None so callers can use a generic phrase."""
    actions = _STAGE_ACTION_LABELS.get((str(source_entity_type or ""), str(team_set_code or "")))
    if not actions:
        return None
    return actions.get(clock) or actions.get("resolution") or actions.get("response")


def _form_detail_link(source_entity_type: str, source_entity_id: str) -> str:
    """Build a frontend deep link for the form's detail page (consumed by notification UI)."""
    if source_entity_type == "stock_inquiry":
        return f"/procurement-management/stock-inquiries/{source_entity_id}"
    if source_entity_type == "sponsorship_form":
        return f"/procurement-management/sponsorship-forms/{source_entity_id}"
    if source_entity_type == "purchase_request":
        return f"/procurement-management/purchase-requests/{source_entity_id}"
    if source_entity_type == "complaint":
        return f"/complaint-management/complaints/{source_entity_id}"
    if source_entity_type == "ticket":
        return f"/ticket-management/tickets/{source_entity_id}"
    if source_entity_type == "price_tag_request":
        return f"/dealer-kit/price-tag-requests/{source_entity_id}"
    if source_entity_type == "sample_request":
        return f"/project-sales/sample-requests/{source_entity_id}"
    return f"/{source_entity_type.replace('_', '-')}/{source_entity_id}"


def build_sla_whatsapp_data(
    db: Session,
    tracking,
    recipient_id: Optional[str],
    body: str,
    *,
    use_case: str = "sla_assignment",
    reason: str = "",
    extra_vars: Optional[dict] = None,
) -> dict:
    """Build the ``whatsapp_*`` keys for a notification's ``data`` so the WhatsApp
    delivery (``_send_whatsapp_for_notification``) renders the approved template
    out-of-window and sends ``body`` as text in-window - identical to the canonical
    SLA-assignment path (``_notify_assignee``). Works for both form and conversation
    trackers (conversation rows have no entity number → falls back to a generic ref).
    """
    from app.config import settings as _settings
    from app.models.user import User as _User

    s_type = str(getattr(tracking, "source_entity_type", "") or "")
    s_id = str(getattr(tracking, "source_entity_id", "") or "")
    number = _resolve_entity_number(db, s_type, s_id) or (s_type.replace("_", " ").title() or "an SLA task")
    due_str = _fmt_due(getattr(tracking, "due_at", None))
    resolve_due_str = _fmt_due(getattr(tracking, "due_at_resolution", None))
    base_url = (getattr(_settings, "frontend_base_url", None) or "").strip().rstrip("/")
    today_date = datetime.now(timezone(timedelta(hours=8))).strftime("%d/%m/%Y")
    recipient = (
        db.query(_User).filter(_User.id == str(recipient_id)).first() if recipient_id else None
    )
    contact_name = ((recipient.name or recipient.email) if recipient else None) or "there"
    if s_type in FORM_SLA_TYPES:
        form_url = _full_form_link(s_type, s_id)
    else:
        # Conversation ticket: the same "/?ticket=<id>" deep link the email and
        # in-app notification carry (UAC AC-G1) - the assignee answers from the
        # CRM's My Pending drawer, not the Respond inbox. Form-SLA rows above are
        # untouched: for them {{form_url}} is, and stays, the form record.
        form_url = _ticket_deep_link(base_url, getattr(tracking, "id", ""))
    context_vars = {
        "contact_name": contact_name,
        "entity_number": number,
        "reason": reason or "",
        "respond_due_at": due_str or "",
        "resolve_due_at": resolve_due_str or "",
        "today_date": today_date,
        "system_url": base_url,
        "form_url": form_url,
        "portal_url": form_url,
        "message": body,
    }
    # Caller-supplied vars (e.g. takeover ``initiator`` name) override defaults.
    if extra_vars:
        context_vars.update({k: v for k, v in extra_vars.items() if v is not None})
    return {
        "whatsapp_use_case": use_case,
        "whatsapp_text": body,
        "whatsapp_context_vars": context_vars,
    }


class FormEscalationBlocked(Exception):
    """Escalation cannot proceed: no next tier, or no resolvable assignee.

    The auto-scan treats this as a skip; the manual endpoint maps it to 422.
    """

    def __init__(self, reason_code: str, message: str):
        self.reason_code = reason_code
        self.message = message
        super().__init__(message)


class FormSLAOrchestrator:
    """Single funnel for form-service transitions to drive SLA trackers."""

    def __init__(self, db: Session):
        self.db = db

    # ---------------- public API ----------------

    def emit_event(
        self,
        source_entity_type: str,
        source_entity_id: str,
        event_name: str,
        *,
        contact_id: Optional[str] = None,
        actor_user_id: Optional[str] = None,
    ) -> None:
        """Evaluate every active config for this form type and dispatch matching action.

        Errors are logged but never propagated - SLA orchestration must not block
        the underlying state transition.
        """
        if not source_entity_type or not source_entity_id or not event_name:
            return

        try:
            configs = (
                self.db.query(FormSLAConfig)
                .filter(
                    FormSLAConfig.source_entity_type == source_entity_type,
                    FormSLAConfig.is_active.is_(True),
                )
                .all()
            )
        except Exception as e:
            # Table may not exist yet (migrations behind) or session may be in a bad
            # state from a prior op - roll back so the parent transaction can keep
            # using this session for subsequent reads/writes.
            try:
                self.db.rollback()
            except Exception:
                pass
            logger.warning(
                "Form SLA config lookup failed for %s; skipping SLA emit: %s",
                source_entity_type,
                e,
            )
            return
        if not configs:
            return

        def _matches(field: Optional[str]) -> bool:
            if not field:
                return False
            for token in str(field).split(","):
                if token.strip() == event_name:
                    return True
            return False

        for config in configs:
            try:
                if _matches(config.start_event):
                    self._start_for_config(
                        config, source_entity_id, contact_id=contact_id
                    )
                if _matches(config.respond_event):
                    self._respond_for_active(
                        config, source_entity_id, actor_user_id=actor_user_id
                    )
                if _matches(config.resolve_event):
                    self._resolve_for_active(
                        config,
                        source_entity_id,
                        actor_user_id=actor_user_id,
                        contact_id=contact_id,
                        resolve_event=event_name,
                    )
            except Exception as e:
                logger.warning(
                    "Form SLA event '%s' on %s/%s for config %s failed: %s",
                    event_name,
                    source_entity_type,
                    source_entity_id,
                    config.id,
                    e,
                )

    def trackers_for_source(
        self,
        source_entity_type: str,
        source_entity_id: str,
    ) -> list[ConversationSLATracking]:
        """All trackers (active + closed) for one form row, ordered oldest first."""
        return (
            self.db.query(ConversationSLATracking)
            .filter(
                ConversationSLATracking.source_entity_type == source_entity_type,
                ConversationSLATracking.source_entity_id == str(source_entity_id),
            )
            .order_by(ConversationSLATracking.initiated_at.asc())
            .all()
        )

    def _open_form_trackers(
        self,
        source_entity_type: str,
        source_entity_id: str,
    ) -> list[ConversationSLATracking]:
        """The ONE query for "which stages of this form row are still open", newest
        first. Both the identify side (``active_trackers_for_source``) and the void
        side (``void_active_for_source``) go through it, so they cannot answer
        differently about the same rows.

        Pinned to FORM types AND passed through the negated conversation scope: that
        discriminator is the only thing separating the two SLA systems inside
        ``conversation_sla_tracking``, and a form row falsely matching a
        conversation-keyed query is a bug this repo has already had (UAC F4b).
        """
        from sqlalchemy import not_ as sql_not

        from app.services.sla_scope import open_tracker_scope
        from app.services.sla_service import conversation_tracking_scope

        entity_type = (source_entity_type or "").strip()
        if entity_type not in FORM_SLA_TYPES:
            return []

        return (
            self.db.query(ConversationSLATracking)
            .filter(
                ConversationSLATracking.source_entity_type == entity_type,
                ConversationSLATracking.source_entity_id == str(source_entity_id),
                sql_not(conversation_tracking_scope()),
                *open_tracker_scope(),
            )
            .order_by(ConversationSLATracking.initiated_at.desc())
            .all()
        )

    def active_trackers_for_source(
        self,
        source_entity_type: str,
        source_entity_id: str,
    ) -> list[ConversationSLATracking]:
        """Open (unresolved, not voided) form trackers for one form row, newest first.

        Several stages of one form can be open at once, so this returns them all.
        This is what ``revise()`` uses to name the stages it is about to void, so it
        MUST be the same query the void performs - see ``_open_form_trackers``.
        """
        return self._open_form_trackers(source_entity_type, source_entity_id)

    def void_active_for_source(
        self,
        source_entity_type: str,
        source_entity_id: str,
        *,
        reason: str,
        void_reason: str = "revised_by_contact",
        now: Optional[datetime] = None,
    ) -> list[ConversationSLATracking]:
        """Cancel every open stage of one form row and return the trackers voided.

        Mirrors ``_escalate_tracker``'s bookkeeping minus the reassignment: the stage
        stops where it is, the handling lock is released (nobody can claim a stage that
        no longer exists), and one ``void`` event log is written per tracker so the
        cancellation is readable in the timeline.

        ``is_resolved`` is deliberately left alone (UAC F4/F4a): a voided stage was
        never resolved, and marking it so would count it as completed in every KPI. The
        void marker is what dashboards and open-tracker queries filter on - see
        ``app/services/sla_scope.py``.

        **Conversation SLA is never touched** (UAC F4b). The two systems share this
        table and are discriminated only by ``source_entity_type``, so the selection
        runs through ``_open_form_trackers``, which pins the type to form types AND
        negates the conversation scope - the same query the caller used to name the
        stages it is voiding.

        Does NOT commit: the caller owns the transaction. Pending takeovers are voided
        by the caller after that commit, because ``void_for_tracking`` commits itself.
        """
        now = now or _utc_naive_now()
        trackers = self._open_form_trackers(source_entity_type, source_entity_id)
        if not trackers:
            return []

        for tracker in trackers:
            tracker.voided_at = now
            tracker.void_reason = void_reason
            # A voided stage cannot be held: clear the handling lock, exactly as an
            # escalation does when the tier changes. Smuggle the holder out on the
            # instance first - the caller still has to TELL them their work stopped,
            # and by then the column is empty. The marker dies on any re-query, so
            # read it off the returned objects.
            tracker._voided_handled_by_id = tracker.handled_by_id  # noqa: SLF001
            tracker.handled_by_id = None
            tracker.handled_at = None
        self.db.flush()

        for tracker in trackers:
            self._write_event_log(
                tracker_id=str(tracker.id),
                event_type="void",
                from_tier=tracker.current_tier,
                to_tier=tracker.current_tier,
                reason=reason,
                assigned_to_id=tracker.assigned_to_id,
                # Naive-UTC column into an event-log payload: _write_event_log wraps
                # every datetime through _to_aware_utc, or it lands shifted -8h.
                due_at=tracker.due_at,
                trigger="auto",
            )
        return trackers

    def scan_overdue_and_escalate(self) -> dict:
        """Find form trackers past due + not escalated this tier, escalate to next tier."""
        from app.services.sla_scope import open_tracker_scope
        from app.services.user_service import AccessAgentService

        now = _utc_naive_now()
        candidates = (
            self.db.query(ConversationSLATracking)
            .filter(
                *open_tracker_scope(),
                ConversationSLATracking.source_entity_type.in_(FORM_SLA_TYPES),
            )
            .all()
        )
        escalated_count = 0
        skipped_count = 0
        for tracker in candidates:
            try:
                due = tracker.due_at
                due_resolution = tracker.due_at_resolution
                # Split-clock breach rule (mirrors conversation SLA list_due_escalations):
                # the response clock STOPS on response, so once responded the response
                # due_at must NOT gate escalation - only the resolution clock does. Without
                # this guard a responded-on-time tracker whose response due_at has since
                # lapsed keeps escalating (and extend, which only moves due_at_resolution,
                # can't stop it). Pre-response -> response clock; post-response -> resolution.
                # Each escalation resets both clocks, so this stays self-idempotent across
                # ticks and progresses every tier (TCK-28 - removed the buggy escalated_at
                # guard that froze rows at tier 2).
                responded = bool(getattr(tracker, "is_responded", False))
                overdue = (
                    (not responded and due is not None and due < now)
                    or (due_resolution is not None and due_resolution < now)
                )
                if not overdue:
                    continue
                # Build the WHO-missed-WHAT-by-WHEN reason from the tracker's current
                # (about-to-fail) state BEFORE _escalate_tracker reassigns + resets clocks.
                reason = self._build_overdue_reason(tracker, now)
                self._escalate_tracker(
                    tracker,
                    trigger="auto",
                    reason=reason,
                    now=now,
                )
                escalated_count += 1
            except FormEscalationBlocked:
                # No next tier / no resolvable assignee - auto-scan skips silently.
                skipped_count += 1
            except Exception as e:
                logger.exception(
                    "Form SLA escalation failed for tracker %s: %s", tracker.id, e
                )

        if escalated_count:
            try:
                self.db.commit()
            except Exception as e:
                self.db.rollback()
                logger.exception("Commit failed after form SLA escalation: %s", e)

        return {
            "scanned": len(candidates),
            "escalated": escalated_count,
            "skipped": skipped_count,
        }

    def _escalate_tracker(
        self,
        tracker: ConversationSLATracking,
        *,
        trigger: str,
        reason: str,
        triggered_by_id: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> dict:
        """Advance a form-SLA tracker to the next tier. Shared by the overdue scan
        (trigger='auto') and the manual endpoint (trigger='manual'). Raises
        FormEscalationBlocked when there is no next tier or no resolvable assignee.
        """
        from app.services.user_service import AccessAgentService

        now = now or _utc_naive_now()
        target_tier = int(tracker.current_tier or 1) + 1

        agent_id = str(tracker.agent_id) if tracker.agent_id is not None else None
        if not agent_id:
            raise FormEscalationBlocked("no_agent", "Tracker has no agent; cannot resolve an escalation team.")
        agent_svc = AccessAgentService(self.db)
        # Tier fallback: escalate to target_tier, but if that tier has no team,
        # skip up to the next configured tier (target+1, +2…). Shared with the
        # initial-assignment fallback in _start_for_config.
        # AC-E3: the ladder is the tracker's own company's, not the ambient request's.
        # This runs from the overdue scan, which has no request company at all.
        from app.services.sla_service import _tracking_company_id

        resolved = agent_svc.resolve_team_with_tier_fallback(
            agent_id,
            target_tier,
            team_set_code=tracker.team_set_code,
            company_id=_tracking_company_id(tracker),
        )
        if not resolved:
            raise FormEscalationBlocked("top_tier", "No higher-tier team configured; cannot escalate further.")
        team_id, actual_tier = resolved
        next_tier = (
            self.db.query(SLAPolicyTier)
            .filter(
                SLAPolicyTier.policy_id == tracker.policy_id,
                SLAPolicyTier.tier_level == actual_tier,
            )
            .first()
        )
        if not next_tier:
            raise FormEscalationBlocked("top_tier", f"SLA policy has no tier {actual_tier}; cannot escalate.")
        assignee = agent_svc.get_next_assignee(agent_id, team_id)
        if not assignee:
            raise FormEscalationBlocked("no_assignee", f"No available assignee for tier {actual_tier}.")

        # Coverage redirect: route escalation to the covered user's coverer. RR cursor
        # already advanced to the covered user (fairness) - only swap the result.
        from app.services.coverage_subscription_service import (
            resolve_assignee_with_coverage,
        )

        assignee, covered_for_id = resolve_assignee_with_coverage(self.db, assignee)

        response_hrs = float(getattr(next_tier, "response_hours", 24) or 24)
        resolution_hrs = float(getattr(next_tier, "resolution_hours", 24) or 24)
        tracker.current_tier = actual_tier
        # The tier clock starts when work can actually begin; escalated_at keeps the
        # true escalation instant for audit.
        clock_start = _working_clock_start_naive(self.db, now)
        tracker.current_tier_started_at = clock_start
        tracker.escalated_at = now
        tracker.escalation_reason = reason
        tracker.due_at = _working_due_naive(self.db, clock_start, response_hrs)
        tracker.due_at_resolution = _working_due_naive(self.db, clock_start, resolution_hrs)
        # Snapshot the escalated-FROM owner BEFORE the assignee overwrite so the
        # escalation event log records who missed at the prior tier (banner link).
        prev_assigned_to_id = tracker.assigned_to_id
        tracker.assigned_to_id = assignee["id"]
        tracker.assigned_to = (
            str(assignee.get("respond_user_id")) if assignee.get("respond_user_id") else None
        )
        # Handling-lock reset (PLAN-form-handling-lock Q5c): a new tier means everyone
        # must re-claim. Clear any lock held on the prior tier.
        tracker.handled_by_id = None
        tracker.handled_at = None
        self.db.flush()

        log_reason = reason
        if covered_for_id:
            from app.services.coverage_subscription_service import coverage_note

            log_reason = f"{reason}{coverage_note(self.db, covered_for_id)}"
        self._write_event_log(
            tracker_id=str(tracker.id),
            event_type="escalation",
            from_tier=target_tier - 1,
            to_tier=target_tier,
            reason=log_reason,
            assigned_to_id=assignee["id"],
            from_assigned_to_id=prev_assigned_to_id,
            due_at=tracker.due_at,
            trigger=trigger,
            triggered_by_id=triggered_by_id,
        )
        # Escalation respects the stage's notify_on_escalation flag (silent stages
        # stay silent). Default to notifying when no matching config is found.
        if self._stage_notifies_on_escalation(tracker):
            self._notify_assignee(tracker, kind="escalated", reason=reason)
        return assignee

    def _stage_notifies_on_escalation(self, tracker: ConversationSLATracking) -> bool:
        """Whether this tracker's stage config opts into escalation notifications.
        Stage matched by (source_entity_type, team_set_code) - same key used for
        the next-action derivation. Defaults True when no config matches."""
        try:
            cfg = (
                self.db.query(FormSLAConfig)
                .filter(
                    FormSLAConfig.source_entity_type == tracker.source_entity_type,
                    FormSLAConfig.team_set_code == tracker.team_set_code,
                )
                .first()
            )
        except Exception:  # noqa: BLE001 - missing table in a partial schema, etc.
            self.db.rollback()
            return True
        if cfg is None:
            return True
        return bool(getattr(cfg, "notify_on_escalation", True))

    def _build_overdue_reason(
        self, tracker: ConversationSLATracking, now: datetime
    ) -> str:
        """Rich auto-escalation reason naming WHO missed WHICH action by WHEN.

        Reads the tracker's split clocks + its stage's action verbs so the banner
        says e.g. "Baser did not approve or reject by 10 Jul 2026, 9:00 AM
        (resolution overdue)" instead of the ambiguous "Response/resolution overdue".
        Called BEFORE `_escalate_tracker` mutates the tracker, so `assigned_to_id`
        and the due timestamps still reflect the tier that just failed.
        """
        from app.models.user import User as _User

        responded = bool(getattr(tracker, "is_responded", False))
        due_resp = getattr(tracker, "due_at", None)
        due_reso = getattr(tracker, "due_at_resolution", None)
        s_type = getattr(tracker, "source_entity_type", None)
        team = getattr(tracker, "team_set_code", None)
        actions = _STAGE_ACTION_LABELS.get((str(s_type or ""), str(team or "")), {})
        # Split-clock: pre-response failures are a response breach only when the stage
        # HAS a distinct response action; otherwise (response == resolution) any breach
        # is a resolution breach. Post-response failures are always resolution.
        if (not responded) and ("response" in actions) and due_resp is not None and due_resp < now:
            clock, due = "response", due_resp
        else:
            clock, due = "resolution", due_reso or due_resp

        verb = _stage_action_verb(s_type, team, clock) or "act on this form"

        who = None
        if getattr(tracker, "assigned_to_id", None):
            u = self.db.query(_User).filter(_User.id == str(tracker.assigned_to_id)).first()
            if u:
                who = (u.name or u.email or "").strip() or None

        due_str = _fmt_due(due)
        subject = who or "The assignee"
        by = f" by {due_str}" if due_str else ""
        return f"{subject} did not {verb}{by} ({clock} overdue)"

    def escalate_form_tracking(
        self,
        tracking_id: str,
        *,
        reason: str,
        actor_user_id: Optional[str] = None,
    ) -> ConversationSLATracking:
        """Manually force-escalate a form-SLA tracker to the next tier, pre-breach.

        Raises LookupError if the row is missing; FormEscalationBlocked for
        not-a-form / resolved / top-tier / no-assignee (router maps to 422).
        """
        tracker = (
            self.db.query(ConversationSLATracking)
            .filter(ConversationSLATracking.id == str(tracking_id))
            .first()
        )
        if tracker is None:
            raise LookupError("SLA tracking not found.")
        if str(tracker.source_entity_type or "") not in FORM_SLA_TYPES:
            raise FormEscalationBlocked("not_form", "This tracking row is not a form SLA stage.")
        if bool(tracker.is_resolved):
            raise FormEscalationBlocked("resolved", "SLA is already resolved; cannot escalate.")
        if getattr(tracker, "voided_at", None) is not None:
            raise FormEscalationBlocked(
                "voided", "This stage was voided when the form was revised; cannot escalate."
            )

        reason_text = f"manual: {reason}" if reason else "manual escalation"
        self._escalate_tracker(
            tracker,
            trigger="manual",
            reason=reason_text,
            triggered_by_id=actor_user_id,
        )
        try:
            self.db.commit()
            self.db.refresh(tracker)
        except Exception as e:
            self.db.rollback()
            logger.exception("Commit failed after manual form SLA escalation: %s", e)
            raise
        # Escalation changed owner/tier - void any pending takeover (AC-VOID-3).
        from app.services.sla_takeover_service import SlaTakeoverService

        SlaTakeoverService(self.db).void_for_tracking(str(tracker.id), "escalated")
        return tracker

    # ---------------- internals ----------------

    def _active_tracker(
        self, config: FormSLAConfig, source_entity_id: str
    ) -> Optional[ConversationSLATracking]:
        # Stage identity is (source_entity_type, team_set_code) - NOT policy_id.
        # Stages of one form intentionally share a policy_id, so keying solely on
        # policy_id makes one stage's lookup return another stage's tracker: the
        # submit stage's resolve grabs (and resolves) the approval tracker, then the
        # approval start re-creates it → duplicate assignment + duplicate notify.
        # team_set_code alone already separates stages, so scope to it (handling NULL
        # explicitly, since SQL `NULL = NULL` is never true). Do NOT filter on
        # policy_id: policy_id is a snapshot the tracker stored at create time, so
        # editing a stage's policy afterward would orphan the live tracker - its
        # resolve/respond events would no longer find it.
        # A VOIDED stage is not an active one (UAC F4): without this the revision
        # restart would find the stage it just voided and skip spawning stage 1.
        from app.services.sla_scope import open_tracker_scope

        q = (
            self.db.query(ConversationSLATracking)
            .filter(
                ConversationSLATracking.source_entity_type == config.source_entity_type,
                ConversationSLATracking.source_entity_id == str(source_entity_id),
                *open_tracker_scope(),
            )
        )
        if config.team_set_code is None:
            q = q.filter(ConversationSLATracking.team_set_code.is_(None))
        else:
            q = q.filter(
                ConversationSLATracking.team_set_code == config.team_set_code
            )
        return q.order_by(ConversationSLATracking.initiated_at.desc()).first()

    @staticmethod
    def _is_approval_stage(config: FormSLAConfig) -> bool:
        """The PR/SF stage whose resolution IS the approval decision (resolves on
        'approved'). Only this stage honours the form's default-approver routing."""
        return (
            str(getattr(config, "source_entity_type", "") or "")
            in ("purchase_request", "sponsorship_form")
            and "approved" in (str(getattr(config, "resolve_event", "") or ""))
        )

    def _form_default_approver_user_id(self, source_entity_type: Optional[str]) -> Optional[str]:
        """The configured default approver user id for PR / SF, from SystemSetting."""
        from app.models.user import SystemSetting

        row = self.db.query(SystemSetting).first()
        if not row:
            return None
        if source_entity_type == "purchase_request":
            uid = getattr(row, "purchase_request_default_approver_user_id", None)
        elif source_entity_type == "sponsorship_form":
            uid = getattr(row, "sponsorship_form_default_approver_user_id", None)
        else:
            uid = None
        return str(uid) if uid else None

    def _start_for_config(
        self,
        config: FormSLAConfig,
        source_entity_id: str,
        *,
        contact_id: Optional[str] = None,
    ) -> Optional[ConversationSLATracking]:
        """Idempotent: skip if active tracker for this stage already exists."""
        from app.services.user_service import AccessAgentService
        from app.models.user import User

        existing = self._active_tracker(config, source_entity_id)
        if existing:
            return existing

        # No CS outcome: the requestor's winning rule says this form needs no customer
        # service, so no CS stage starts (no tracker, no team lookup). The form stays
        # Approved: there is no status change.
        if self._reads_cs_pin(config) and self._winning_rule_is_no_cs(
            config, source_entity_id, contact_id
        ):
            return None

        agent = (
            self.db.query(AccessAgent)
            .filter(AccessAgent.code == config.agent_code)
            .first()
        )
        if not agent:
            raise handle_validation_error(
                f"No access agent with code '{config.agent_code}' for form SLA config {config.id}."
            )
        agent_svc = AccessAgentService(self.db)

        # AC-E4: form SLA takes its company from the spawning entity's CONTACT.
        # Complaints, purchase requests, sponsorship forms and stock inquiries all
        # carry one; tickets carry none and so always land on the default. A contact
        # in more than one company gets the default too - same "never pick
        # arbitrarily" rule as conversation routing.
        from app.services.company_routing_service import company_for_contact

        company_id = company_for_contact(self.db, contact_id=contact_id)

        # Default-approver override: for the PR/SF APPROVAL stage (resolves on
        # 'approved'), if the form's configured default approver is a member of the
        # approval team set, route the stage straight to them at THEIR tier (e.g. a
        # director pinned at tier 3) instead of tier-1 round-robin. Falls back to
        # the normal tier-1 routing when no default approver / not a team member.
        start_tier = 1
        override_assignee: Optional[dict] = None
        if self._is_approval_stage(config):
            approver_uid = self._form_default_approver_user_id(config.source_entity_type)
            if approver_uid:
                approver_tier = agent_svc.get_user_tier_in_team_set(
                    str(agent.id),
                    approver_uid,
                    team_set_code=config.team_set_code,
                    company_id=company_id,
                )
                if approver_tier:
                    approver = (
                        self.db.query(User).filter(User.id == str(approver_uid)).first()
                    )
                    if approver is not None:
                        start_tier = approver_tier
                        override_assignee = {
                            "id": approver.id,
                            "email": approver.email,
                            "name": approver.name or approver.email,
                            "respond_user_id": approver.respond_user_id,
                        }

        # Tier fallback: assign at start_tier, but if that tier has no team, skip
        # up to the next configured tier (start+1, +2…). Shared with escalation.
        resolved = agent_svc.resolve_team_with_tier_fallback(
            str(agent.id),
            start_tier,
            team_set_code=config.team_set_code,
            company_id=company_id,
        )
        if not resolved:
            raise handle_validation_error(
                f"Agent '{config.agent_code}' has no team at tier {start_tier} or above"
                + (
                    f" in set '{config.team_set_code}'"
                    if config.team_set_code
                    else ""
                )
                + ". Configure a tier team before activating this SLA config."
            )
        team_id, start_tier = resolved
        # Pin-point override: a salesman (respond_contact) can be pinned to a
        # specific CS PIC per procurement use_case. A valid pin assigns that user
        # directly; any miss falls back to round-robin. The round-robin cursor is
        # NOT advanced on an override. Only the procurement CS stage reads the pin
        # table: the PIC need not be a member of any team, so team membership can no
        # longer keep a pinned CS user off the submit or approval stage
        # (CS-ROUTING-TARGETS). Complaint and stock inquiry never read it.
        # See docs/plans/PLAN-procurement-cs-handoff-and-pinpoint-routing.md.
        if override_assignee is not None:
            assignee = override_assignee  # default-approver wins over round-robin
        elif not self._reads_cs_pin(config):
            assignee = agent_svc.get_next_assignee(str(agent.id), team_id)
        else:
            # Pin lookup keys on the REQUESTOR (who the form is for), not the
            # submitter, so a salesman submitting on someone else's behalf still
            # reaches that person's pinned CS. Falls back to the submitter when
            # there is no requestor FK (legacy rows, complaint, internal-created).
            # A requestor with no pin round-robins, never retries the submitter's
            # pin (that was the bug). See PLAN-requested-by-contact-routing.md D2/E2.
            routing_contact_id = (
                self._routing_contact_id(config.source_entity_type, source_entity_id)
                or contact_id
            )
            assignee = self._resolve_pinned_assignee(
                config.source_entity_type, routing_contact_id, company_id, source_entity_id
            )
            if not assignee:
                assignee = agent_svc.get_next_assignee(str(agent.id), team_id)
        if not assignee:
            raise handle_validation_error(
                f"No members in tier {start_tier} team for agent '{config.agent_code}'."
            )

        # Coverage redirect: if the resolved assignee is on leave (covered), route
        # the task to their coverer instead. RR cursor already advanced to the
        # covered user above (fairness, decision 4) - we only swap the result.
        from app.services.coverage_subscription_service import (
            resolve_assignee_with_coverage,
        )

        assignee, covered_for_id = resolve_assignee_with_coverage(self.db, assignee)

        tier_row = (
            self.db.query(SLAPolicyTier)
            .filter(
                SLAPolicyTier.policy_id == config.policy_id,
                SLAPolicyTier.tier_level == start_tier,
            )
            .first()
        )
        if not tier_row:
            raise handle_validation_error(
                f"SLA policy {config.policy_id} has no tier {start_tier}; cannot start tracker."
            )

        now = _utc_naive_now()
        clock_start = _working_clock_start_naive(self.db, now)
        response_hrs = float(getattr(tier_row, "response_hours", 24) or 24)
        resolution_hrs = float(getattr(tier_row, "resolution_hours", 24) or 24)
        tracker = ConversationSLATracking(
            policy_id=config.policy_id,
            current_tier=start_tier,
            assigned_to=(
                str(assignee.get("respond_user_id"))
                if assignee.get("respond_user_id")
                else None
            ),
            assigned_to_id=assignee["id"],
            # initiated_at is the true submit instant (audit); the tier clock starts
            # when work can actually begin - the next working-window open.
            initiated_at=now,
            current_tier_started_at=clock_start,
            due_at=_working_due_naive(self.db, clock_start, response_hrs),
            due_at_resolution=_working_due_naive(self.db, clock_start, resolution_hrs),
            is_responded=False,
            is_resolved=False,
            respond_contact_id=contact_id,
            company_id=company_id,
            source_entity_type=config.source_entity_type,
            source_entity_id=str(source_entity_id),
            agent_id=str(agent.id),
            team_set_code=config.team_set_code,
        )
        self.db.add(tracker)
        self.db.commit()
        self.db.refresh(tracker)

        # Coverage redirect → record why the task landed on the coverer (best-effort).
        if covered_for_id:
            from app.services.coverage_subscription_service import coverage_note

            self._write_event_log(
                tracker_id=str(tracker.id),
                event_type="assign",
                from_tier=start_tier,
                to_tier=start_tier,
                reason=f"Initial assignment{coverage_note(self.db, covered_for_id)}",
                assigned_to_id=assignee["id"],
                due_at=tracker.due_at,
                trigger="auto",
            )

        # Per-stage toggle: some stages route silently (no assignee notification).
        if getattr(config, "notify_assignee", True):
            self._notify_assignee(tracker, kind="assigned")
        return tracker

    def _load_form_field_values(
        self, source_entity_type: Optional[str], source_entity_id: Optional[str]
    ) -> dict:
        """Return the form header row's own fields as a plain dict (for predicate
        evaluation). Empty dict on any miss - never raises."""
        try:
            values = self._load_form_field_values_strict(source_entity_type, source_entity_id)
        except Exception:
            logger.warning(
                "CS pin: failed to load form fields for %s/%s; predicates see no fields.",
                source_entity_type,
                source_entity_id,
                exc_info=True,
            )
            try:
                self.db.rollback()
            except Exception:  # noqa: BLE001
                pass
            return {}
        return values

    def _load_form_field_values_strict(
        self, source_entity_type: Optional[str], source_entity_id: Optional[str]
    ) -> dict:
        """Same as `_load_form_field_values` but a load failure RAISES, so a caller that
        must tell "failed" from "loaded, empty" (the No CS check) can."""
        src = _ENTITY_NUMBER_SOURCE.get(source_entity_type or "")
        if not src or not source_entity_id:
            return {}
        table = src[0]
        from sqlalchemy import text as _sql_text

        row = self.db.execute(
            _sql_text(f"SELECT row_to_json(t) AS j FROM {table} t WHERE t.id = :id"),
            {"id": str(source_entity_id)},
        ).first()
        values = dict(row[0]) if row and row[0] else {}
        if values and source_entity_type in ("purchase_request", "sponsorship_form"):
            values.update(self._computed_request_values(str(source_entity_id), values))
        return values

    def _computed_request_values(self, request_id: str, header_values: dict) -> dict:
        """PR / SF routing values that are not header columns: `product_lines` (has / none,
        a line counts only with a non-blank item_code) and `customer_debtor_type` (the
        linked customer's CURRENT type, None when unlinked or blank)."""
        from sqlalchemy import text as _sql_text

        has_line = self.db.execute(
            _sql_text(
                "SELECT 1 FROM purchase_request_lines WHERE purchase_request_id = :id "
                "AND btrim(coalesce(item_code, '')) <> '' LIMIT 1"
            ),
            {"id": request_id},
        ).first()
        debtor_type = None
        customer_id = header_values.get("customer_id")
        if customer_id:
            row = self.db.execute(
                _sql_text("SELECT debtor_type FROM customers WHERE id = :cid"),
                {"cid": str(customer_id)},
            ).first()
            debtor_type = row[0] if row and row[0] else None
        return {"product_lines": "has" if has_line else "none", "customer_debtor_type": debtor_type}

    def _winning_rule_is_no_cs(
        self, config: FormSLAConfig, source_entity_id: str, contact_id: Optional[str]
    ) -> bool:
        """True when the requestor's winning CS routing rule for this form is "No CS"
        (cs_pic_user_id NULL). Same contact, same form values, same selection as
        `_resolve_pinned_assignee`. Never raises: any failure means "not No CS"."""
        try:
            from app.models.access import RespondContactCsRouting
            from app.services.cs_routing_match import select_pin_row

            routing_contact_id = (
                self._routing_contact_id(config.source_entity_type, source_entity_id) or contact_id
            )
            if not routing_contact_id:
                return False
            pins = (
                self.db.query(RespondContactCsRouting)
                .filter(
                    RespondContactCsRouting.respond_contact_id == routing_contact_id,
                    RespondContactCsRouting.use_case == config.source_entity_type,
                    RespondContactCsRouting.is_active.is_(True),
                )
                .all()
            )
            if not pins:
                return False
            # Strict load: a failure raises into the handler below (False), never "empty".
            form_values = self._load_form_field_values_strict(
                config.source_entity_type, source_entity_id
            )
            pin = select_pin_row(pins, form_values)
            return bool(pin) and pin.cs_pic_user_id is None
        except Exception:
            logger.warning("No CS check failed for %s; routing as usual.", source_entity_id, exc_info=True)
            try:
                self.db.rollback()
            except Exception:  # noqa: BLE001
                pass
            return False

    def _routing_contact_id(
        self,
        source_entity_type: Optional[str],
        source_entity_id: Optional[str],
    ) -> Optional[str]:
        """The contact the FORM WAS SUBMITTED FOR (the requestor), read from the
        header row's FK. `purchase_request` / `sponsorship_form` share the
        `purchase_requests` table (`requested_by_contact_id`); `stock_inquiry`
        reads `stock_inquiries.salesperson_contact_id`. Anything else (complaint,
        ticket) has no requestor FK and returns None.

        Never raises: a missing table, missing row or bad FK degrades to None so
        the caller falls back to the submitter (`contact_id`) exactly as before
        this feature existed. See PLAN-requested-by-contact-routing.md UAC E3/E9.
        """
        if not source_entity_type or not source_entity_id:
            return None
        try:
            from sqlalchemy import text as _sql_text

            if source_entity_type in ("purchase_request", "sponsorship_form"):
                table, column = "purchase_requests", "requested_by_contact_id"
            elif source_entity_type == "stock_inquiry":
                table, column = "stock_inquiries", "salesperson_contact_id"
            else:
                return None
            row = self.db.execute(
                _sql_text(f"SELECT {column} FROM {table} WHERE id = :id"),
                {"id": str(source_entity_id)},
            ).first()
            return str(row[0]) if row and row[0] else None
        except Exception:
            # A failed SELECT aborts the Postgres transaction, so without this
            # rollback the NEXT query in _start_for_config dies with
            # InFailedSqlTransaction and NO tracker is created at all - the
            # opposite of degrading gracefully. Same reason as the rollback in
            # _advance_on_event's handler.
            try:
                self.db.rollback()
            except Exception:  # noqa: BLE001
                pass
            logger.warning(
                "Routing contact lookup failed for %s/%s; falling back to submitter.",
                source_entity_type,
                source_entity_id,
                exc_info=True,
            )
            return None

    @staticmethod
    def _reads_cs_pin(config: FormSLAConfig) -> bool:
        """The procurement customer-service stage: the only stage whose assignment
        honours a salesman's CS routing pin."""
        from app.services.cs_routing_service import (
            CS_AGENT_CODE,
            CS_TEAM_SET_CODE,
            PINNABLE_USE_CASES,
        )

        return (
            getattr(config, "agent_code", None) == CS_AGENT_CODE
            and getattr(config, "team_set_code", None) == CS_TEAM_SET_CODE
            and getattr(config, "source_entity_type", None) in PINNABLE_USE_CASES
        )

    def _resolve_pinned_assignee(
        self,
        source_entity_type: Optional[str],
        contact_id: Optional[str],
        company_id: Optional[str],
        source_entity_id: Optional[str] = None,
    ) -> Optional[dict]:
        """Return the CS PIC pinned for (contact, use_case) whose match-conditions
        pass this form, or None to fall back to round-robin.

        Generic predicate routing (any use_case): among a contact's active pins for
        the use_case, keep those whose every ``{field, operator, value}`` predicate
        matches the form header's fields, then pick the lowest ``priority``
        (``created_at`` tiebreak); an empty condition list is a wildcard. Returns an
        assignee dict shaped like ``get_next_assignee`` so the caller is
        branch-agnostic. Returns None (→ round-robin) when there is no contact, no
        matching pin, or the chosen user is not an eligible PIC for ``company_id``
        (``cs_routing_service.is_eligible_pic``: active, able to process purchase
        requests, granted the company). Team membership is not required. Never
        raises - every failure degrades to round-robin so approval can't 500.
        """
        if not contact_id or not source_entity_type:
            return None
        try:
            from app.models.access import RespondContactCsRouting
            from app.models.user import User
            from app.services import cs_routing_service
            from app.services.cs_routing_match import select_pin_row

            pins = (
                self.db.query(RespondContactCsRouting)
                .filter(
                    RespondContactCsRouting.respond_contact_id == contact_id,
                    RespondContactCsRouting.use_case == source_entity_type,
                    RespondContactCsRouting.is_active.is_(True),
                )
                .all()
            )
            if not pins:
                return None
            form_values = self._load_form_field_values(source_entity_type, source_entity_id)
            pin = select_pin_row(pins, form_values)
            if not pin:
                return None
            user_id = pin.cs_pic_user_id
            if not cs_routing_service.is_eligible_pic(self.db, user_id, company_id=company_id):
                logger.warning(
                    "CS pin: contact %s use_case %s -> user %s is not an eligible PIC "
                    "for company %s; falling back to round-robin.",
                    contact_id,
                    source_entity_type,
                    user_id,
                    company_id,
                )
                return None
            user = self.db.query(User).filter(User.id == user_id).first()
            if not user:
                return None
            return {
                "id": user.id,
                "email": user.email,
                "name": user.name or user.email,
                "respond_user_id": user.respond_user_id,
            }
        except Exception as e:
            logger.warning(
                "CS pin lookup failed for contact %s use_case %s: %s; round-robin.",
                contact_id,
                source_entity_type,
                e,
            )
            return None

    def _respond_for_active(
        self,
        config: FormSLAConfig,
        source_entity_id: str,
        *,
        actor_user_id: Optional[str] = None,
    ) -> None:
        from app.schemas.sla import ConversationSLATrackingUpdate
        from app.services.sla_service import ConversationSLATrackingService

        tracker = self._active_tracker(config, source_entity_id)
        if not tracker or bool(getattr(tracker, "is_responded", False)):
            return
        svc = ConversationSLATrackingService(self.db)
        update_payload = {"is_responded": True}
        if actor_user_id:
            update_payload["responded_by"] = actor_user_id
        svc.update_tracking(
            str(tracker.id),
            ConversationSLATrackingUpdate(**update_payload),
        )

    def _resolve_for_active(
        self,
        config: FormSLAConfig,
        source_entity_id: str,
        *,
        actor_user_id: Optional[str] = None,
        contact_id: Optional[str] = None,
        resolve_event: Optional[str] = None,
    ) -> None:
        from app.schemas.sla import ConversationSLATrackingUpdate
        from app.services.sla_service import ConversationSLATrackingService

        tracker = self._active_tracker(config, source_entity_id)
        if not tracker or bool(getattr(tracker, "is_resolved", False)):
            return
        # resolve implies responded - set both if not yet responded
        update_payload = {"is_resolved": True}
        if not bool(getattr(tracker, "is_responded", False)):
            update_payload["is_responded"] = True
        if actor_user_id:
            update_payload["resolved_by"] = actor_user_id
            if not bool(getattr(tracker, "is_responded", False)):
                update_payload["responded_by"] = actor_user_id
        svc = ConversationSLATrackingService(self.db)
        carry_contact_id = contact_id or (
            str(tracker.respond_contact_id)
            if tracker.respond_contact_id is not None
            else None
        )
        svc.update_tracking(
            str(tracker.id),
            ConversationSLATrackingUpdate(**update_payload),
        )

        # Spawn the next stage only if this resolve should advance the chain.
        # advance_on_event set -> only the matching event advances (e.g. 'approved'
        # spawns customer service, 'rejected' just closes this stage). NULL -> any
        # resolve advances (backward-compatible).
        advance_on = (getattr(config, "advance_on_event", None) or "").strip()
        should_advance = (not advance_on) or (advance_on == (resolve_event or "").strip())

        if config.next_config_id and should_advance:
            next_cfg = (
                self.db.query(FormSLAConfig)
                .filter(
                    FormSLAConfig.id == config.next_config_id,
                    FormSLAConfig.is_active.is_(True),
                )
                .first()
            )
            if next_cfg:
                self._start_for_config(
                    next_cfg, source_entity_id, contact_id=carry_contact_id
                )

    def _notify_assignee(
        self,
        tracker: ConversationSLATracking,
        *,
        kind: str = "assigned",
        reason: Optional[str] = None,
    ) -> None:
        if not tracker.assigned_to_id:
            return
        s_type = str(tracker.source_entity_type or "")
        s_id = str(tracker.source_entity_id or "")
        # Relative path for in-app navigation; absolute URL for the email body.
        link = _form_detail_link(s_type, s_id)
        full_link = _full_form_link(s_type, s_id)
        type_label = (s_type or "form").replace("_", " ")
        # Human-readable document number, never the raw UUID.
        number = _resolve_entity_number(self.db, s_type, s_id) or type_label.capitalize()
        due_str = _fmt_due(tracker.due_at)
        resolve_due_str = _fmt_due(getattr(tracker, "due_at_resolution", None))
        # WhatsApp template params (out-of-window). Resolve the assignee name + the
        # generic date/url helpers so templates can read "Resolve by {{...}}" etc.
        from app.config import settings as _settings
        from app.models.user import User as _User

        _base_url = (getattr(_settings, "frontend_base_url", None) or "").strip().rstrip("/")
        _today_date = datetime.now(timezone(timedelta(hours=8))).strftime("%d/%m/%Y")
        _assignee = (
            self.db.query(_User).filter(_User.id == str(tracker.assigned_to_id)).first()
        )
        _contact_name = (
            (_assignee.name or _assignee.email or "there") if _assignee else "there"
        )
        # Destination link mirroring the pending-tasks row click: routed form types open
        # their in-system record; others (e.g. ticket) open the ticket in the CRM.
        # _FE_RECORD_ROUTES must match MyPendingSLAWidget.ENTITY_ROUTES.
        _FE_RECORD_ROUTES = {"stock_inquiry", "complaint", "purchase_request", "sponsorship_form"}
        if s_type in _FE_RECORD_ROUTES:
            form_url = full_link
        else:
            # Ticket-shaped rows deep-link to the ticket in the CRM (AC-G1), never
            # the Respond inbox.
            form_url = _ticket_deep_link(_base_url, tracker.id)

        if kind == "assigned":
            title = f"New SLA assignment: {number}"
            body = f"{type_label.capitalize()} {number} is assigned to you."
            if due_str:
                body += f" Respond by {due_str}."
            body += f"\n\nOpen: {full_link}"
        elif kind == "escalated":
            title = f"SLA escalated to you: {number}"
            body = (
                f"{type_label.capitalize()} {number} has been escalated to you. "
                f"Reason: {reason or 'overdue'}."
            )
            if due_str:
                body += f" New deadline: {due_str}."
            body += f"\n\nOpen: {full_link}"
        else:
            title = f"SLA update: {number}"
            body = f"{type_label.capitalize()} {number} status updated.\n\nOpen: {full_link}"

        try:
            # Per-event user opt-ins gate email + whatsapp independently. In-app is
            # always sent (the stage already decided to notify). The service skips a
            # channel silently when the user's per-event toggle is off.
            if kind == "escalated":
                email_pref = "notify_email_on_escalation"
                whatsapp_pref = "notify_whatsapp_on_escalation"
            else:  # "assigned"
                email_pref = "notify_email_on_assignment"
                whatsapp_pref = "notify_whatsapp_on_assignment"
            whatsapp_eligible = kind in ("escalated", "assigned")
            NotificationService(self.db).create_with_channel_preferences(
                user_id=str(tracker.assigned_to_id),
                type="form_sla",
                title=title,
                body=body,
                data={
                    "tracking_id": str(tracker.id),
                    "source_entity_type": tracker.source_entity_type,
                    "source_entity_id": tracker.source_entity_id,
                    "link": link,
                    "whatsapp_use_case": (
                        "sla_escalation" if kind == "escalated" else "sla_assignment"
                    ),
                    "whatsapp_text": body,
                    "whatsapp_context_vars": {
                        "contact_name": _contact_name,
                        "entity_number": number,
                        "reason": reason or "",
                        "respond_due_at": due_str or "",
                        "resolve_due_at": resolve_due_str or "",
                        "today_date": _today_date,
                        "system_url": _base_url,
                        "form_url": form_url,
                        "portal_url": full_link,
                        "message": body,
                    },
                },
                source_entity_type="form_sla_tracking",
                source_entity_id=str(tracker.id),
                event_type=kind,
                send_in_app=True,
                send_email=True,
                send_web_push=False,
                send_whatsapp=whatsapp_eligible,
                whatsapp_pref_attr=whatsapp_pref,
                email_pref_attr=email_pref,
            )
            # Coverage fan-out: copy this assignment/escalation to anyone covering
            # the assignee. Best-effort; gated by the subscriber's own toggles.
            if kind in ("assigned", "escalated"):
                from app.services.coverage_subscription_service import (
                    fan_out_coverage_copies,
                )

                fan_out_coverage_copies(
                    self.db,
                    target_user_id=str(tracker.assigned_to_id),
                    actor_user_id=None,
                    notification_type="form_sla",
                    title=title,
                    body=body,
                    data={
                        "tracking_id": str(tracker.id),
                        "source_entity_type": tracker.source_entity_type,
                        "source_entity_id": tracker.source_entity_id,
                        "link": link,
                    },
                    source_entity_type="form_sla_tracking",
                    source_entity_id=str(tracker.id),
                    event_type=kind,
                    email_pref_attr=email_pref,
                    whatsapp_pref_attr=whatsapp_pref,
                    send_whatsapp=whatsapp_eligible,
                )
        except Exception as e:
            logger.warning(
                "Failed to send form SLA notification for tracker %s: %s",
                tracker.id,
                e,
            )

    def _write_event_log(
        self,
        *,
        tracker_id: str,
        event_type: str,
        from_tier: Optional[int] = None,
        to_tier: Optional[int] = None,
        reason: Optional[str] = None,
        assigned_to_id: Optional[str] = None,
        from_assigned_to_id: Optional[str] = None,
        due_at: Optional[datetime] = None,
        trigger: Optional[str] = None,
        triggered_by_id: Optional[str] = None,
    ) -> None:
        from app.schemas.sla import ConversationSLAEventLogCreate
        from app.services.sla_service import ConversationSLATrackingService, _to_aware_utc

        try:
            # create_event_log treats NAIVE datetimes as Malaysia time (UTC+8); our
            # columns store naive UTC. Wrap in _to_aware_utc so event_at / due_at land
            # as true UTC instead of being shifted -8h (mirrors extend_tracking).
            ConversationSLATrackingService(self.db).create_event_log(
                ConversationSLAEventLogCreate(
                    sla_tracking_id=tracker_id,
                    event_type=event_type,
                    from_tier=from_tier,
                    to_tier=to_tier,
                    event_at=_to_aware_utc(_utc_naive_now()),
                    reason=reason,
                    assigned_to_id=assigned_to_id,
                    from_assigned_to_id=from_assigned_to_id,
                    due_at=_to_aware_utc(due_at) if isinstance(due_at, datetime) else due_at,
                    trigger=trigger,
                    triggered_by_id=triggered_by_id,
                )
            )
        except Exception as e:
            logger.warning("Failed to write event log for tracker %s: %s", tracker_id, e)


def emit_form_event(
    db: Session,
    source_entity_type: str,
    source_entity_id: str,
    event_name: str,
    *,
    contact_id: Optional[str] = None,
    actor_user_id: Optional[str] = None,
) -> None:
    """Module-level helper: thin wrapper for callers that don't hold an orchestrator."""
    FormSLAOrchestrator(db).emit_event(
        source_entity_type,
        source_entity_id,
        event_name,
        contact_id=contact_id,
        actor_user_id=actor_user_id,
    )
