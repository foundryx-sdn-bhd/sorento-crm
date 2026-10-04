"""SLA tracking API routes."""
from fastapi import APIRouter, Depends, Query, HTTPException, status, Request, Response
from starlette.datastructures import UploadFile as StarletteUploadFile
from datetime import datetime, timezone, timedelta
from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID
from sqlalchemy.orm import Session
from typing import Optional
import httpx
from app.database import get_db
from pydantic import BaseModel, Field
from app.dependencies import get_current_user_or_api_key, require_permission, get_current_user
from app.services.sla_service import (
    ConversationSLATrackingService,
    append_clock_line,
    compute_tracking_timings,
    event_log_assignee_fields,
)
from app.services.integration_service import IntegrationLogService
from app.services.ticket_comment_service import TicketCommentService
from app.services.uuid_list_param import parse_uuid_list
from app.schemas.sla import (
    ConversationSLATrackingCreate,
    ConversationSLATrackingUpdate,
    ConversationSLATrackingResponse,
    ConversationSLAEventLogCreate,
    ConversationSLAEventLogResponse,
    ConversationSLATrackingStatusUpdate,
    ConversationSLAEscalateRequest,
    ConversationSLATestOverrideRequest,
)
from app.schemas.integration import IntegrationLogCreate
from app.schemas.ticket_comment import TicketCommentCreate, TicketCommentResponse
from app.schemas.common import ListResponse, MAX_PAGE_LIMIT
from app.services.error_handler import (
    AppException,
    handle_internal_error,
    handle_validation_error,
    handle_not_found,
)
from app.models.sla import ConversationSLATracking, ConversationSLAEventLog, SLAPolicyTier
from app.models.user import User
from app.services.banner_person_service import (
    wa_phone_for_user_id,
    name_and_wa_phone_for_user_id,
)

router = APIRouter()


def _log_integration_best_effort(log_service, payload, **kwargs) -> None:
    """Write an integration_log for work that is ALREADY committed.

    Post-commit side effects are best-effort (PRINCIPLES.md): re-raising here
    answers 500 for a create/stamp that actually succeeded, so n8n reports a
    failed intervention and retries into the idempotent path - which never
    backfills the missing log. Warn and carry on instead.
    """
    import logging

    try:
        log_service.create_integration_log(payload, **kwargs)
    except Exception:  # noqa: BLE001 - the logged work already committed
        logging.getLogger(__name__).warning(
            "integration_log write failed for %s %s; the operation itself succeeded.",
            getattr(payload, "business_table", "?"),
            getattr(payload, "business_id", "?"),
            exc_info=True,
        )


def _write_event_log_best_effort(service, payload) -> None:
    """Write an SLA event log for a state change that is ALREADY committed.

    Same rule as `_log_integration_best_effort`, and the same reason: the
    responded / resolved stamp landed before this runs, so raising reports a
    failure for work that succeeded - and the caller's retry takes the
    already-responded / already-resolved short-circuit, which deliberately
    writes no event log at all. Rolls back first: the failed insert leaves the
    session unusable for the response the route still has to build.
    """
    import logging

    try:
        service.create_event_log(payload)
    except Exception:  # noqa: BLE001 - the state change already committed
        try:
            service.db.rollback()
        except Exception:  # noqa: BLE001
            pass
        logging.getLogger(__name__).warning(
            "SLA %s event log failed for tracking %s; the state change itself stands.",
            getattr(payload, "event_type", "?"),
            getattr(payload, "sla_tracking_id", "?"),
            exc_info=True,
        )


class SlaTrackingConversationReplyBody(BaseModel):
    message: str = Field(..., min_length=1)


class TicketAIDraftBody(BaseModel):
    """AI assist request (UAC AC-L5).

    Everything is optional: the useful call is an empty body. The thread is read
    server-side, so the browser never posts the conversation back to us.
    """

    instruction: Optional[str] = Field(
        default=None,
        max_length=500,
        description="What the agent wants the draft to do ('offer Tuesday delivery').",
    )
    tail: Optional[int] = Field(
        default=None, ge=1, le=50, description="How many recent messages to ground on."
    )


def _resolved_by_param(value: Optional[str], current_user: dict) -> Optional[str]:
    """Expand the ``resolved_by=me`` sentinel to the caller's users.id (AC-M2).

    The widget's "Recently resolved" link is "what I resolved", and a UUID in a
    URL the user can see is exactly what the no-UUIDs-in-the-UI rule forbids.
    Anything else is passed through untouched (users.id / respond_user_id / email).
    """
    raw = (value or "").strip()
    if not raw:
        return None
    if raw.lower() != "me":
        return raw
    uid = (current_user or {}).get("id")
    # An api-key principal with no acting user has no "me": filter to nothing
    # rather than silently widening to everyone's resolutions.
    return str(uid).strip() if uid and str(uid).strip() else "__no_such_user__"


def _sla_reply_respond_user_id(current_user: dict) -> str:
    rid = (current_user or {}).get("respond_user_id") or (current_user or {}).get("respondUserId")
    if rid and str(rid).strip():
        return str(rid).strip()
    uid = (current_user or {}).get("id")
    if uid and str(uid).strip():
        return str(uid).strip()
    raise HTTPException(
        status_code=400,
        detail="User respond_user_id or id is required to send a message.",
    )


def build_conversation_sla_tracking_response(
    db: Session,
    tracking: ConversationSLATracking,
    *,
    include_event_logs: bool = True,
) -> ConversationSLATrackingResponse:
    """Build ConversationSLATrackingResponse (shared by GET-by-id and external lookup)."""
    contact_phone = tracking.contact.phone_number if tracking.contact else None
    contact_name = tracking.contact.name if tracking.contact else None

    assigned_user_name = tracking.assigned_user.name if tracking.assigned_user else None
    assigned_user_email = tracking.assigned_user.email if tracking.assigned_user else None

    responded_by_user_name = None
    if tracking.responded_by is not None and str(tracking.responded_by).strip():
        responded_by_user = db.query(User).filter(
            (User.id == tracking.responded_by)
            | (User.respond_user_id == tracking.responded_by)
            | (User.email == tracking.responded_by)
        ).first()
        responded_by_user_name = responded_by_user.name if responded_by_user else tracking.responded_by

    resolved_by_user_name = None
    if tracking.resolved_by is not None and str(tracking.resolved_by).strip():
        resolved_by_user = db.query(User).filter(
            (User.id == tracking.resolved_by)
            | (User.respond_user_id == tracking.resolved_by)
            | (User.email == tracking.resolved_by)
        ).first()
        resolved_by_user_name = resolved_by_user.name if resolved_by_user else tracking.resolved_by

    event_logs_data = []
    response_durations = []
    resolution_durations = []

    if include_event_logs and tracking.event_logs:
        for log in tracking.event_logs:
            log_data = {
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
                "reminder_count": log.reminder_count,
                "last_reminder_at": log.last_reminder_at,
                "created_at": log.created_at,
                "assigned_user": {
                    "id": log.assigned_user.id,
                    "email": log.assigned_user.email,
                    "name": log.assigned_user.name,
                }
                if log.assigned_user
                else None,
                "assigned_user_name": log.assigned_user.name if log.assigned_user else None,
                "assigned_user_email": log.assigned_user.email if log.assigned_user else None,
            }
            event_logs_data.append(log_data)

            d = float(log.duration) if log.duration is not None else None
            if log.event_type and log.event_type.lower() == "response" and d is not None and d > 0:
                response_durations.append(d)
            elif log.event_type and log.event_type.lower() == "resolution" and d is not None and d > 0:
                resolution_durations.append(d)

    average_response_time = None
    average_resolution_time = None

    if response_durations:
        avg = sum(response_durations) / len(response_durations)
        average_response_time = Decimal(str(avg)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    if resolution_durations:
        avg = sum(resolution_durations) / len(resolution_durations)
        average_resolution_time = Decimal(str(avg)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    tier = (
        db.query(SLAPolicyTier)
        .filter(
            SLAPolicyTier.policy_id == tracking.policy_id,
            SLAPolicyTier.tier_level == tracking.current_tier,
        )
        .first()
    )
    timings = compute_tracking_timings(tracking, tier)
    tier_response_hours = tier.response_hours if tier else None
    tier_resolution_hours = getattr(tier, "resolution_hours", None) if tier else None

    # Banner person links (PLAN-form-banner-person-links): current assignee's wa.me
    # phone (extension banner) + the escalated-FROM owner name/phone from the latest
    # escalation event (escalation banner). All resolve to None when unavailable.
    assigned_user_wa_phone = wa_phone_for_user_id(db, tracking.assigned_to_id)
    latest_escalation = (
        db.query(ConversationSLAEventLog)
        .filter(
            ConversationSLAEventLog.sla_tracking_id == tracking.id,
            ConversationSLAEventLog.event_type == "escalation",
        )
        .order_by(ConversationSLAEventLog.event_at.desc())
        .first()
    )
    escalated_from_id = (
        getattr(latest_escalation, "from_assigned_to_id", None)
        if latest_escalation is not None
        else None
    )
    escalated_from_name, escalated_from_wa_phone = name_and_wa_phone_for_user_id(
        db, escalated_from_id
    )

    response_dict = {
        "id": str(tracking.id),
        "policy_id": str(tracking.policy_id),
        "current_tier": tracking.current_tier,
        "assigned_to": tracking.assigned_to,
        "assigned_to_id": tracking.assigned_to_id,
        "initiated_at": tracking.initiated_at,
        "current_tier_started_at": tracking.current_tier_started_at,
        "due_at": tracking.due_at,
        "due_at_resolution": tracking.due_at_resolution,
        "escalated_at": tracking.escalated_at,
        "escalation_reason": tracking.escalation_reason,
        "is_responded": tracking.is_responded,
        "responded_at": tracking.responded_at,
        "responded_by": tracking.responded_by,
        "response_time": tracking.response_time,
        "is_resolved": tracking.is_resolved,
        "resolved_at": tracking.resolved_at,
        "resolved_by": tracking.resolved_by,
        "respond_contact_id": tracking.respond_contact_id,
        "respond_io_id": getattr(tracking.contact, "respond_io_id", None) if tracking.contact else None,
        "created_at": tracking.created_at,
        "updated_at": tracking.updated_at,
        "synced_to_excel": tracking.synced_to_excel,
        "last_synced_to_excel": tracking.last_synced_to_excel,
        "resolution_duration": tracking.resolution_duration,
        "policy": {
            "id": str(tracking.policy.id),
            "code": tracking.policy.code,
            "name": tracking.policy.name,
        }
        if tracking.policy
        else None,
        "policy_code": tracking.policy.code if tracking.policy else None,
        "policy_name": tracking.policy.name if tracking.policy else None,
        "contact": {
            "id": tracking.contact.id,
            "phone_number": tracking.contact.phone_number,
            "name": tracking.contact.name,
            "respond_io_id": getattr(tracking.contact, "respond_io_id", None),
        }
        if tracking.contact
        else None,
        "assigned_user": {
            "id": tracking.assigned_user.id,
            "email": tracking.assigned_user.email,
            "name": tracking.assigned_user.name,
            "superior": {
                "name": tracking.assigned_user.superior.name if tracking.assigned_user.superior else None,
                "email": tracking.assigned_user.superior.email if tracking.assigned_user.superior else None,
            }
            if getattr(tracking.assigned_user, "superior", None)
            else None,
        }
        if tracking.assigned_user
        else None,
        "contact_phone": contact_phone,
        "contact_name": contact_name,
        "assigned_user_name": assigned_user_name,
        "assigned_user_email": assigned_user_email,
        "assigned_user_wa_phone": assigned_user_wa_phone,
        "escalated_from_name": escalated_from_name,
        "escalated_from_wa_phone": escalated_from_wa_phone,
        "assigned_user_superior_name": tracking.assigned_user.superior.name
        if (tracking.assigned_user and getattr(tracking.assigned_user, "superior", None))
        else None,
        "assigned_user_superior_email": tracking.assigned_user.superior.email
        if (tracking.assigned_user and getattr(tracking.assigned_user, "superior", None))
        else None,
        "responded_by_user_name": responded_by_user_name,
        "resolved_by_user_name": resolved_by_user_name,
        "average_response_time": average_response_time,
        "average_resolution_time": average_resolution_time,
        "event_logs": event_logs_data,
        "tier_response_hours": tier_response_hours,
        "tier_resolution_hours": tier_resolution_hours,
        "agent_id": getattr(tracking, "agent_id", None),
        "agent_code": tracking.agent.code if getattr(tracking, "agent", None) else None,
        "agent": {
            "id": str(tracking.agent.id),
            "code": tracking.agent.code,
            "name": tracking.agent.name,
        }
        if getattr(tracking, "agent", None)
        else None,
        "team_set_code": getattr(tracking, "team_set_code", None),
        "brand_code": getattr(tracking, "brand_code", None),
        "message_id": getattr(tracking, "message_id", None),
        **timings,
    }

    return ConversationSLATrackingResponse.model_validate(response_dict)


@router.get("/dashboard")
async def get_sla_tracking_dashboard(
    current_user: dict = Depends(get_current_user_or_api_key),
    db: Session = Depends(get_db)
):
    """Get dashboard metrics for SLA tracking."""
    import logging
    import traceback
    logger = logging.getLogger(__name__)
    try:
        service = ConversationSLATrackingService(db)
        metrics = service.get_dashboard_metrics()
        return metrics
    except Exception as e:
        tb = traceback.format_exc()
        logger.error(f"Error in get_sla_tracking_dashboard: {str(e)}")
        logger.error(tb)
        # Include detail in response for debugging (remove in production if desired)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"message": str(e), "type": type(e).__name__, "traceback": tb},
        )


@router.get("/my-pending")
async def get_my_pending_sla_tracking(
    limit: int = Query(1000, ge=1, le=MAX_PAGE_LIMIT),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Full unresolved SLA set assigned to the current user (to-do widget). Returns
    everything so the widget shows an honest total and searches/paginates client-side
    over the complete set (browse and search see the same rows)."""
    from app.services.sla_takeover_service import SlaTakeoverService

    try:
        service = ConversationSLATrackingService(db)
        data = service.list_my_pending(current_user["id"], limit=limit)
        # Attach the inline "being taken over" indicator per row (AC-LINK-5).
        pending = SlaTakeoverService(db).pending_by_tracking_ids(
            [r["id"] for r in data], viewer_id=current_user["id"]
        )
        for r in data:
            r["takeover"] = pending.get(r["id"])
        return {"data": data, "empty": len(data) == 0}
    except Exception as e:
        raise handle_internal_error(str(e))


@router.get("/{tracking_id}/takeover-state")
async def get_takeover_state(
    tracking_id: str,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Pin-fetch one contested task + its latest takeover for the My Pending banner."""
    from app.services.sla_takeover_service import SlaTakeoverService

    try:
        return SlaTakeoverService(db).get_takeover_row(tracking_id, current_user["id"])
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.get("/team-pending")
async def get_team_pending_sla_tracking(
    assignee: Optional[str] = Query(None),
    team: Optional[str] = Query(None),
    query: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=MAX_PAGE_LIMIT),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Unresolved SLA tasks of teammates in the current user's visible teams
    (peers + recursive child/grandchild teams), excluding the user's own.
    ``query`` free-text search over entity number / contact name / assignee / team."""
    try:
        service = ConversationSLATrackingService(db)
        result = service.list_team_pending(
            current_user["id"],
            assignee=assignee,
            team=team,
            page=page,
            limit=limit,
            query=query,
        )
        # Attach pending-takeover state per row (running bar / observer-locked state).
        from app.services.sla_takeover_service import SlaTakeoverService

        rows = result.get("data", [])
        pending = SlaTakeoverService(db).pending_by_tracking_ids(
            [r["id"] for r in rows], viewer_id=current_user["id"]
        )
        for r in rows:
            r["takeover"] = pending.get(r["id"])
        result["empty"] = len(rows) == 0
        return result
    except Exception as e:
        raise handle_internal_error(str(e))


@router.get("/visible-users")
async def get_visible_users(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Scope-B picker source: users in the caller's visible teams (peers + child
    teams), excluding self. Used by Reassign + Coverage pickers."""
    try:
        service = ConversationSLATrackingService(db)
        data = service.list_visible_users(current_user["id"])
        return {"data": data, "empty": len(data) == 0}
    except Exception as e:
        raise handle_internal_error(str(e))


class _TakeoverRequest(BaseModel):
    team_id: str


@router.post("/{tracking_id}/takeover")
async def takeover_sla_tracking(
    tracking_id: str,
    payload: _TakeoverRequest,
    response: Response,
    current_user: dict = Depends(get_current_user),
    _perm: dict = Depends(require_permission("sla_management.conversation_sla_tracking.takeover")),
    db: Session = Depends(get_db),
):
    """Initiate a takeover of a visible team task. With a cooldown configured this
    creates a PENDING request (veto window); cooldown 0 or an unassigned task commits
    instantly. Returns `{committed, request?}`. An already-pending task returns 409
    carrying the existing request so the UI shows the running countdown."""
    from app.services.sla_takeover_service import SlaTakeoverService

    try:
        result = SlaTakeoverService(db).initiate(
            tracking_id, current_user["id"], payload.team_id
        )
        if result.get("already_pending"):
            response.status_code = status.HTTP_409_CONFLICT
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post("/takeover-requests/{request_id}/cancel")
async def cancel_takeover_request(
    request_id: str,
    current_user: dict = Depends(get_current_user),
    _perm: dict = Depends(require_permission("sla_management.conversation_sla_tracking.takeover")),
    db: Session = Depends(get_db),
):
    """Initiator (or admin) withdraws a pending takeover."""
    from app.services.sla_takeover_service import SlaTakeoverService

    try:
        return SlaTakeoverService(db).cancel(request_id, current_user["id"])
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post("/takeover-requests/{request_id}/reject")
async def reject_takeover_request(
    request_id: str,
    current_user: dict = Depends(get_current_user),
    _perm: dict = Depends(require_permission("sla_management.conversation_sla_tracking.takeover")),
    db: Session = Depends(get_db),
):
    """Contested assignee (or admin) vetoes a pending takeover."""
    from app.services.sla_takeover_service import SlaTakeoverService

    try:
        return SlaTakeoverService(db).reject(request_id, current_user["id"])
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


class _ReassignRequest(BaseModel):
    user_id: str


@router.post("/{tracking_id}/reassign")
async def reassign_sla_tracking(
    tracking_id: str,
    payload: _ReassignRequest,
    current_user: dict = Depends(get_current_user),
    _perm: dict = Depends(require_permission("sla_management.conversation_sla_tracking.reassign")),
    db: Session = Depends(get_db),
):
    """Reassign a task to a chosen person (keeps team + clocks). Actor and target
    each need at least one team membership (or actor is an admin); hand-off may
    cross teams in either direction (decision 2026-09-03)."""
    try:
        service = ConversationSLATrackingService(db)
        tracking = service.reassign(tracking_id, current_user["id"], payload.user_id)
        return build_conversation_sla_tracking_response(db, tracking)
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


class _ConversationEscalateRequest(BaseModel):
    reason: str


def _notify_conversation_sla_escalation(db, tracking, assignee: dict, reason: str) -> None:
    """Notify the new assignee of a manual conversation-SLA escalation.

    Mirrors form-SLA escalation (`form_sla_service._notify_assignee`): in-app always,
    email/WhatsApp gated by the per-event escalation toggles. The "form_url" deep link
    is the Respond inbox (or the SLA detail when no contact). Best-effort - never raises.
    """
    import logging
    from datetime import datetime, timezone, timedelta

    log = logging.getLogger(__name__)
    try:
        from app.config import settings
        from app.services.notification_service import NotificationService
        from app.services.respond_identifier import format_respond_inbox_url
        from app.services.form_sla_service import _fmt_due

        user_id = str(assignee.get("id") or "")
        if not user_id:
            return
        staff_name = (assignee.get("name") or "there").strip() or "there"
        base_url = (getattr(settings, "frontend_base_url", None) or "").strip().rstrip("/")
        rio = getattr(getattr(tracking, "contact", None), "respond_io_id", None)
        inbox = format_respond_inbox_url(
            getattr(settings, "respond_app_base_url", None),
            getattr(settings, "respond_space_id", None),
            str(rio) if rio else None,
        )
        detail = (
            f"{base_url}/sla-management/conversation-sla-tracking/{tracking.id}"
            if base_url
            else ""
        )
        link = inbox or detail
        respond_due = _fmt_due(getattr(tracking, "due_at", None))
        resolve_due = _fmt_due(getattr(tracking, "due_at_resolution", None))
        today_date = datetime.now(timezone(timedelta(hours=8))).strftime("%d/%m/%Y")
        new_tier = int(getattr(tracking, "current_tier", 0) or 0)

        title = "Conversation SLA escalated to you"
        # AC-G2: the same clock line as assignment / reassignment, in Malaysia
        # time with the day and the zone. `_fmt_due` above still feeds the
        # WhatsApp template's own due-date variables, which have their own
        # format; the BODY the human reads uses the one shared builder.
        body = append_clock_line(
            f"A conversation SLA (tier {new_tier}) has been escalated to you. Reason: {reason}.",
            tracking,
        )
        if link:
            body += f"\n\nOpen: {link}"

        NotificationService(db).create_with_channel_preferences(
            user_id=user_id,
            type="conversation_sla",
            title=title,
            body=body,
            data={
                "tracking_id": str(tracking.id),
                "whatsapp_use_case": "sla_escalation",
                "whatsapp_text": body,
                "whatsapp_context_vars": {
                    "contact_name": staff_name,
                    "reason": reason,
                    "respond_due_at": respond_due or "",
                    "resolve_due_at": resolve_due or "",
                    "today_date": today_date,
                    "system_url": base_url,
                    "form_url": link,
                    "portal_url": link,
                    "message": body,
                },
            },
            source_entity_type="conversation_sla_tracking",
            source_entity_id=str(tracking.id),
            event_type="escalated",
            send_in_app=True,
            send_email=True,
            send_whatsapp=True,
            email_pref_attr="notify_email_on_escalation",
            whatsapp_pref_attr="notify_whatsapp_on_escalation",
        )
        # Coverage fan-out: copy escalation to anyone covering the new assignee.
        from app.services.coverage_subscription_service import fan_out_coverage_copies

        fan_out_coverage_copies(
            db,
            target_user_id=user_id,
            actor_user_id=None,
            notification_type="conversation_sla",
            title=title,
            body=body,
            data={"tracking_id": str(tracking.id)},
            source_entity_type="conversation_sla_tracking",
            source_entity_id=str(tracking.id),
            event_type="escalated",
            email_pref_attr="notify_email_on_escalation",
            whatsapp_pref_attr="notify_whatsapp_on_escalation",
        )
    except Exception as e:  # noqa: BLE001 - best-effort, escalation already committed
        log.warning("conversation SLA escalate notify failed for %s: %s", getattr(tracking, "id", "?"), e)


class _ExtendPreviewRequest(BaseModel):
    days: Optional[int] = Field(None, ge=1)
    target_date: Optional[str] = None  # YYYY-MM-DD


class _ExtendRequest(BaseModel):
    days: Optional[int] = Field(None, ge=1)
    target_date: Optional[str] = None  # YYYY-MM-DD
    reason: str = Field(..., min_length=1)


def _assert_extend_assignee(tracking, current_user: dict) -> None:
    """Gate the extend endpoints to the current assignee (403 otherwise). Mirrors
    the service-side gate so the preview endpoint enforces the same scope without a
    mutation."""
    assignee = getattr(tracking, "assigned_to_id", None)
    if assignee is None or str(assignee) != str(current_user.get("id")):
        raise HTTPException(
            status_code=403,
            detail="Only the current assignee can extend this SLA deadline.",
        )


@router.post("/{tracking_id}/extend/preview")
async def preview_extend_sla_tracking(
    tracking_id: UUID,
    payload: _ExtendPreviewRequest,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Preview a resolution-deadline extension (no mutation). Returns the recomputed
    new due, the working-days delta, and any soft-limit warnings. Exactly one of
    `days` / `target_date` must be supplied. Assignee-only (same scope as extend)."""
    if (payload.days is None) == (payload.target_date is None):
        raise HTTPException(
            status_code=422,
            detail="Provide exactly one of 'days' or 'target_date'.",
        )
    try:
        service = ConversationSLATrackingService(db)
        tracking = service.get_tracking(str(tracking_id), load_event_logs=False)
        _assert_extend_assignee(tracking, current_user)
        # S3 (fix round 1, PLAN-keep-assignee-on-resolve-22sep): the real extend
        # already 422s a resolved tracker (`_assert_can_extend`,
        # sla_service.py); the preview must reject it the same way rather than
        # computing a working-days delta for a task nobody can actually extend.
        if bool(getattr(tracking, "is_resolved", False)):
            raise HTTPException(
                status_code=422,
                detail="Cannot extend a resolved SLA task.",
            )
        if getattr(tracking, "due_at_resolution", None) is None:
            raise HTTPException(
                status_code=422,
                detail="This SLA task has no resolution deadline to extend.",
            )
        new_due, working_days = service.compute_extension(
            tracking, days=payload.days, target_date=payload.target_date
        )
        warnings = service.evaluate_extension_warnings(
            tracking, getattr(tracking, "policy", None), working_days
        )
        current_due = getattr(tracking, "due_at_resolution", None)
        return {
            "current_due_at": current_due.isoformat() if current_due else None,
            "new_due_at": new_due.isoformat() if new_due else None,
            "working_days": working_days,
            "warnings": warnings,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post("/{tracking_id}/extend", response_model=ConversationSLATrackingResponse)
async def extend_sla_tracking(
    tracking_id: UUID,
    payload: _ExtendRequest,
    current_user: dict = Depends(get_current_user),
    _perm: dict = Depends(require_permission("sla_management.conversation_sla_tracking.extend")),
    db: Session = Depends(get_db),
):
    """Extend the resolution deadline (current assignee only). Recomputes the new due
    authoritatively (ignores any client-previewed value), bumps the extension
    counters, resets the reminder cycle, writes an 'extend' event log, and
    best-effort notifies the next escalation tier. Works for both conversation and
    form SLA rows."""
    if (payload.days is None) == (payload.target_date is None):
        raise HTTPException(
            status_code=422,
            detail="Provide exactly one of 'days' or 'target_date'.",
        )
    try:
        service = ConversationSLATrackingService(db)
        tracking = service.extend_tracking(
            str(tracking_id),
            current_user["id"],
            days=payload.days,
            target_date=payload.target_date,
            reason=payload.reason,
        )
        return build_conversation_sla_tracking_response(db, tracking)
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.get("/", response_model=ListResponse[ConversationSLATrackingResponse])
async def get_sla_tracking(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=MAX_PAGE_LIMIT),
    policy_id: Optional[str] = Query(None),
    query: Optional[str] = Query(None),
    tracking_ids: Optional[list[str]] = Query(
        None,
        description="Filter by canonical SLA tracking UUIDs (repeated / csv / JSON array).",
    ),
    sort: Optional[str] = Query(None),
    dir: Optional[str] = Query(None),
    assigned_to: Optional[str] = Query(None),
    contact: Optional[str] = Query(
        None,
        description=(
            "Filter to ONE contact's conversation tickets. Accepts the Respond.io "
            "contact id, the CRM respond_contacts.id or a phone number (AC-M2)."
        ),
    ),
    is_resolved: Optional[bool] = Query(
        None, description="true = resolved only, false = open only, omitted = both (AC-M2)."
    ),
    resolved_by: Optional[str] = Query(
        None,
        description=(
            "Filter to tickets resolved by one person: users.id, respond_user_id, "
            "email, or the literal 'me' for the caller (AC-M2)."
        ),
    ),
    scope: str = Query("conversation", description="conversation (contact-keyed) or form (per-entity stage rows)"),
    current_user: dict = Depends(get_current_user_or_api_key),
    db: Session = Depends(get_db)
):
    """Get SLA tracking records with pagination. query searches contact phone and contact name."""
    try:
        service = ConversationSLATrackingService(db)
        result = service.list_tracking(
            page=page,
            limit=limit,
            policy_id=policy_id,
            query=query,
            tracking_ids=parse_uuid_list(tracking_ids, param_name="tracking_ids"),
            sort_field=sort or "created_at",
            sort_dir=dir or "desc",
            assigned_to=assigned_to,
            scope="form" if scope == "form" else "conversation",
            contact=contact,
            is_resolved=is_resolved,
            resolved_by=_resolved_by_param(resolved_by, current_user),
        )
        return result
    except Exception as e:
        import logging
        import traceback
        logger = logging.getLogger(__name__)
        logger.error(f"Error in get_sla_tracking: {str(e)}")
        logger.error(traceback.format_exc())
        raise handle_internal_error(str(e))


@router.post("/", response_model=ConversationSLATrackingResponse, status_code=status.HTTP_201_CREATED)
async def create_sla_tracking(
    tracking_data: ConversationSLATrackingCreate,
    request: Request,
    current_user: dict = Depends(get_current_user_or_api_key),
    db: Session = Depends(get_db)
):
    """Create a new SLA tracking record."""
    log_service = IntegrationLogService(db)
    try:
        service = ConversationSLATrackingService(db)
        tracking = service.create_tracking(tracking_data)
        tracking_id_str = str(getattr(tracking, "id"))
        already_active = bool(getattr(tracking, "_already_active", False))
        log_service.create_integration_log(
            IntegrationLogCreate(
                integration_channel="sla_management",
                business_table="conversation_sla_tracking",
                business_id=tracking_id_str,
                external_reference=tracking_data.contact_phone_number.strip() if getattr(tracking_data, "contact_phone_number", None) else tracking_id_str,
                direction="inbound",
                endpoint=str(request.url),
                http_method="POST",
                status="success" if not already_active else "idempotent_already_active",
            ),
            request_payload_dict=tracking_data.model_dump(),
        )
        fresh = service.get_tracking(str(tracking.id))
        setattr(fresh, "already_active", already_active)
        return fresh
    except HTTPException:
        raise
    except AppException:
        # A deliberate 4xx (e.g. "Conversation is already responded.") is a refusal,
        # not an integration failure. `handle_validation_error` returns AppException,
        # NOT HTTPException, so without this arm it fell through to the generic
        # handler below and was logged as status="failed" - which is why every
        # historical sla_management "failure" is one benign idempotency race.
        # The global handler in app/main.py still serialises it to the caller.
        raise
    except Exception as e:
        try:
            log_service.create_integration_log(
                IntegrationLogCreate(
                    integration_channel="sla_management",
                    business_table="conversation_sla_tracking",
                    business_id="00000000-0000-0000-0000-000000000000",
                    external_reference=tracking_data.contact_phone_number.strip() if getattr(tracking_data, "contact_phone_number", None) else "",
                    direction="inbound",
                    endpoint=str(request.url),
                    http_method="POST",
                    status="failed",
                    error_message=str(e),
                ),
                request_payload_dict=tracking_data.model_dump(),
            )
        except Exception:
            pass
        raise handle_internal_error(str(e))


@router.get("/integration/due-escalations", status_code=status.HTTP_200_OK)
async def list_due_escalations_integration(
    db: Session = Depends(get_db),
):
    """Work-list for the scheduled escalation runner (n8n).

    Returns unresolved conversation-SLA rows past their response due time at tier 1 or 2
    (tier 3 has nowhere to escalate to; form-SLA rows are excluded by scope). Each item
    includes `phone_number` and `respond_io_id` so the runner needs no DB access.

    Feed `tracking_id` (plus `respond_contact_id`) straight into POST
    /integration/escalate, signal-only (omit current_tier). This is ONE ITEM PER
    ROW, so the id is the honest thing to escalate on: dropping it and sending
    only the contact makes the server fall back to a most-recent-open pick, which
    under multi-open tickets escalates a sibling that has not breached while the
    one listed here stays at its tier (AC-I5).
    """
    try:
        service = ConversationSLATrackingService(db)
        items = service.list_due_escalations()
        return {
            "status": "success",
            "count": len(items),
            "items": items,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post("/integration/escalate", status_code=status.HTTP_200_OK)
async def escalate_sla_tracking_integration(
    body: ConversationSLAEscalateRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Escalate a conversation SLA tracking (for external systems).

    **Which ticket (AC-I5):** pass `tracking_id` and that exact row escalates - it is
    validated against `respond_contact_id` and takes precedence over everything else.
    Omit it and the server falls back to the contact-scoped path (most-recent-open for
    the contact, then the legacy contact+policy exact match), kept for back-compat.
    Under multi-open intervention tickets only the id is precise, and
    GET /integration/due-escalations hands one out per breaching row.

    Preferred (signal-only) mode: omit body.current_tier - the server escalates to the row's
    current tier + 1. When already at tier 3, no escalation happens and the response carries
    `escalated: false` with `from_tier = to_tier = 3` (n8n branches on the flag, e.g. keeps
    reminding). Legacy mode: pass body.current_tier as an explicit target (1 - 3, must be greater
    than the row's current tier; supports multi-step jumps, e.g. 1→3).

    Resolves the next assignee from the target tier's team under the tracking's stored
    agent_id / team_set_code (fallback: body.team_set_code, then source_entity_type→agent
    mapping), using that tier's round-robin cursor only (not the previous tier's assignee).
    Returns `escalated`, `from_tier`, `to_tier` for n8n message templating, the assignee's
    Respond user ID for n8n to update Respond.io, plus tracking message_id when set.

    respond_contact_id may be respond_contacts.id, RespondContact.respond_io_id, or contact
    phone (E.164 such as +60160000509, optional spacing; MY local 0-prefix also resolved).
    """
    log_service = IntegrationLogService(db)
    try:
        service = ConversationSLATrackingService(db)
        internal_contact_id = service.resolve_internal_respond_contact_id(body.respond_contact_id)
        if not internal_contact_id:
            raise handle_not_found("Respond contact", body.respond_contact_id)

        # AC-I5: an explicit tracking_id names the ticket and WINS. The scheduler
        # gets one item PER ROW from GET /integration/due-escalations, so it always
        # knows which ticket breached; re-resolving by contact here would let it
        # escalate a different open sibling (bumping a ticket that is inside its
        # SLA and notifying that assignee, while the breaching one stays at tier 1).
        # The id is checked against the resolved contact so a mis-paired body can
        # never escalate somebody else's ticket.
        tracking = None
        if body.tracking_id:
            tracking = service.get_tracking(str(body.tracking_id), load_event_logs=False)
            if str(getattr(tracking, "respond_contact_id", "") or "") != str(
                internal_contact_id
            ):
                raise handle_validation_error(
                    "tracking_id does not belong to respond_contact_id "
                    f"{body.respond_contact_id}."
                )
        else:
            # Back-compat contact-scoped path. Source of truth = the open conversation
            # tracking for this contact; its stored policy_id is the agent-team-tied
            # policy. body.policy_id is only a fallback - used when no open row is
            # found (legacy exact-match lookup).
            tracking = service.get_open_tracking_by_contact(internal_contact_id)
            if not tracking and body.policy_id:
                tracking = service.get_tracking_by_contact_and_policy(
                    internal_contact_id,
                    body.policy_id,
                )
        if not tracking:
            raise handle_not_found(
                "Conversation SLA tracking",
                f"respond_contact_id={body.respond_contact_id}, policy_id={body.policy_id or '(none)'}",
            )
        # Prefer the row's own policy; fall back to body only if the row somehow lacks one.
        effective_policy_id = str(getattr(tracking, "policy_id", None) or body.policy_id or "")
        if bool(getattr(tracking, "is_resolved", False)):
            raise handle_validation_error(
                "Cannot escalate a resolved conversation SLA tracking."
            )

        from_tier = int(getattr(tracking, "current_tier", 0) or 0)
        if from_tier < 1:
            raise handle_validation_error("Current SLA tier is invalid for escalation.")
        if body.current_tier is None:
            # Signal-only escalation: the server owns tier math (n8n keeps no tier state,
            # which would go stale once assignee changes can move the tier server-side).
            if from_tier >= 3:
                assigned_user = getattr(tracking, "assigned_user", None)
                return {
                    "status": "success",
                    "escalated": False,
                    "message": "Already at max tier (3); no escalation performed.",
                    "tracking_id": str(getattr(tracking, "id")),
                    "from_tier": from_tier,
                    "to_tier": from_tier,
                    "current_tier": from_tier,
                    "assigned_to_id": (
                        str(getattr(tracking, "assigned_to_id"))
                        if getattr(tracking, "assigned_to_id", None)
                        else None
                    ),
                    "assigned_to_name": (
                        (assigned_user.name or assigned_user.email) if assigned_user else None
                    ),
                    "assigned_to_respond_user_id": getattr(tracking, "assigned_to", None),
                    "message_id": getattr(tracking, "message_id", None),
                }
            target_tier = from_tier + 1
        else:
            target_tier = int(body.current_tier)
            if target_tier < 1 or target_tier > 3:
                raise handle_validation_error("current_tier must be between 1 and 3.")
            if target_tier <= from_tier:
                raise handle_validation_error(
                    f"Escalation target tier must be greater than current tier {from_tier}. "
                    f"Received current_tier={target_tier}."
                )

        # Prefer agent_id FK / team_set_code stored on the tracking record.
        # Fall back to body.team_set_code if not yet persisted, and to source_entity_type→agent mapping.
        resolved_team_set_code = (
            getattr(tracking, "team_set_code", None)
            or body.team_set_code
            or None
        )
        resolved_agent_id = getattr(tracking, "agent_id", None) or None
        # Segment-scoped escalation: round-robin only among the tier's members serving
        # the contact's market segment(s) (retail / project); untagged contact -> empty
        # set -> unfiltered RR over the whole tier team (unchanged).
        from app.services.market_segment_service import MarketSegmentService

        contact_segments = MarketSegmentService(db).resolve_segments_for_contact_id(
            internal_contact_id
        )
        # AC-E3: the company comes off the tracker, so n8n needs no new field - it
        # already resolves the tracker before escalating.
        from app.services.sla_service import _tracking_company_id

        assignee = service.get_escalation_assignee_for_tier(
            getattr(tracking, "source_entity_type", None),
            target_tier,
            resolved_team_set_code,
            agent_id_override=resolved_agent_id,
            contact_segments=contact_segments,
            company_id=_tracking_company_id(tracking),
            # AC-T3: the brand comes off the tracker, like the company - the ladder
            # this escalation climbs must be the one the assignment came off.
            brand_code=getattr(tracking, "brand_code", None),
        )

        # Assignee is passed into the service so the escalation event log (written inside
        # escalate_tracking) records the NEW tier assignee, not the previous tier's.
        # AC-F1: pass the id we already resolved above (via get_open_tracking_by_contact /
        # get_tracking_by_contact_and_policy) so the mutation targets that EXACT row -
        # never re-resolve by (contact, policy) here, which could now match a different
        # open sibling ticket for the same contact/policy. The contact+policy resolution
        # above stays a documented "most recent open" pick (known interim limitation for
        # this contact-only n8n signal path; a contact with 2+ open tickets on the same
        # policy has only its most-recently-created one escalated per call - closed by a
        # future per-ticket n8n contract, S3.2).
        tracking = service.escalate_tracking(
            respond_contact_id=internal_contact_id,
            policy_id=effective_policy_id,
            current_tier=target_tier,
            escalation_reason=body.escalation_reason,
            assigned_to_id=str(assignee["id"]),
            assigned_to_respond_user_id=(
                str(assignee["respond_user_id"])
                if assignee.get("respond_user_id") is not None
                else None
            ),
            tracking_id=str(getattr(tracking, "id")),
        )
        tracking_id_str = str(getattr(tracking, "id"))

        log_service.create_integration_log(
            IntegrationLogCreate(
                integration_channel="sla_escalation",
                business_table="conversation_sla_tracking",
                business_id=tracking_id_str,
                external_reference=internal_contact_id,
                direction="inbound",
                endpoint=str(request.url),
                http_method="POST",
                status="success",
            ),
            request_payload_dict=body.model_dump(),
        )
        # Notify the new-tier assignee through our existing notification path -
        # in-app always, email/WhatsApp gated by the per-event escalation toggles
        # (same as the UI escalate route + form-SLA). n8n does NOT send its own
        # escalation notification, so the backend owns it for ALL escalations.
        # Best-effort: the escalation already committed (helper swallows + warns).
        _notify_conversation_sla_escalation(
            db, tracking, assignee, getattr(body, "escalation_reason", None) or ""
        )

        assigned_to_respond_user_id = str(assignee.get("respond_user_id") or "")
        assigned_to_id = str(assignee.get("id") or "")
        assigned_to_name = assignee.get("name")

        # Return new due dates and remaining hours (based on SLA policy)
        now_utc = datetime.now(timezone.utc)

        def _to_aware_utc(dt):
            if dt is None:
                return None
            if getattr(dt, "tzinfo", None) is not None:
                return dt.astimezone(timezone.utc)
            return dt.replace(tzinfo=timezone.utc)

        due_at_utc = _to_aware_utc(tracking.due_at)
        due_at_resolution_utc = _to_aware_utc(getattr(tracking, "due_at_resolution", None))

        remaining_response_hours = None
        if due_at_utc:
            secs = (due_at_utc - now_utc).total_seconds()
            remaining_response_hours = int(round(max(0.0, secs / 3600.0)))  # full hours

        remaining_resolution_hours = None
        if due_at_resolution_utc:
            secs = (due_at_resolution_utc - now_utc).total_seconds()
            remaining_resolution_hours = int(round(max(0.0, secs / 3600.0)))  # full hours

        def _iso(dt):
            if dt is None:
                return None
            d = _to_aware_utc(dt) if getattr(dt, "tzinfo", None) is None else dt.astimezone(timezone.utc)
            if d is None:
                return None
            return d.isoformat()

        return {
            "status": "success",
            "escalated": True,
            "message": "SLA tracking escalated successfully.",
            "tracking_id": tracking_id_str,
            "from_tier": from_tier,
            "to_tier": target_tier,
            "assigned_to_id": assigned_to_id,
            "assigned_to_name": assigned_to_name,
            "assigned_to_respond_user_id": assigned_to_respond_user_id,
            "message_id": getattr(tracking, "message_id", None),
            "current_tier": tracking.current_tier,
            "escalated_at": _iso(getattr(tracking, "escalated_at", None)),
            "escalation_reason": getattr(tracking, "escalation_reason", None),
            "due_at": _iso(tracking.due_at),
            "due_at_resolution": _iso(tracking.due_at_resolution),
            "remaining_response_hours": remaining_response_hours,
            "remaining_resolution_hours": remaining_resolution_hours,
        }
    except HTTPException:
        raise
    except Exception as e:
        try:
            log_service.create_integration_log(
                IntegrationLogCreate(
                    integration_channel="sla_escalation",
                    business_table="conversation_sla_tracking",
                    business_id="",
                    external_reference=body.respond_contact_id,
                    direction="inbound",
                    endpoint=str(request.url),
                    http_method="POST",
                    status="failed",
                    error_message=str(e),
                ),
                request_payload_dict=body.model_dump(),
            )
        except Exception:
            pass
        raise handle_internal_error(str(e))


# NOTE: declared AFTER /integration/escalate on purpose. FastAPI matches routes in
# declaration order; a parametric "/{tracking_id}/escalate" declared earlier would
# capture the literal "/integration/escalate" path (tracking_id="integration") and
# shadow the n8n integration endpoint. Keep all static "/integration/*" routes above
# this parametric one.
@router.post("/{tracking_id}/escalate")
async def escalate_conversation_sla_tracking(
    tracking_id: str,
    payload: _ConversationEscalateRequest,
    current_user: dict = Depends(get_current_user),
    _perm: dict = Depends(require_permission("sla_management.conversation_sla_tracking.escalate")),
    db: Session = Depends(get_db),
):
    """Manually escalate a CONVERSATION SLA tracker to the next tier (with reason).

    UI counterpart of POST /integration/escalate: keyed by tracking_id, target tier
    is always current+1, capped at tier 3. Form-SLA rows must use the form-SLA
    escalate endpoint. The reason is stored as ``manual: <reason>`` and the
    escalation event log records the new-tier assignee.
    """
    from app.services.form_sla_service import FORM_SLA_TYPES

    reason = (payload.reason or "").strip()
    if not reason:
        raise HTTPException(status_code=422, detail="A reason is required to escalate.")

    service = ConversationSLATrackingService(db)
    tracking = service.get_tracking(tracking_id, load_event_logs=False)
    if not tracking:
        raise handle_not_found("Conversation SLA tracking", tracking_id)
    # Permission == visibility: actor may only escalate tasks they own or that are
    # assigned within their visible teams (mirrors reassign / takeover scope).
    if not service.can_user_act_on_tracking(current_user["id"], tracking):
        raise handle_not_found("Conversation SLA tracking", tracking_id)
    if getattr(tracking, "source_entity_type", None) in FORM_SLA_TYPES:
        raise handle_validation_error(
            "This is a form-SLA stage; escalate it from the form record instead."
        )
    if bool(getattr(tracking, "is_resolved", False)):
        raise handle_validation_error("Cannot escalate a resolved conversation SLA tracking.")

    from_tier = int(getattr(tracking, "current_tier", 0) or 0)
    if from_tier < 1:
        raise handle_validation_error("Current SLA tier is invalid for escalation.")
    if from_tier >= 3:
        raise handle_validation_error(
            "Already at the maximum tier (3); cannot escalate further."
        )
    contact_id = getattr(tracking, "respond_contact_id", None)
    if not contact_id:
        raise handle_validation_error(
            "This SLA tracking has no linked contact and cannot be escalated."
        )

    target_tier = from_tier + 1
    # Resolve the new-tier assignee BEFORE escalating so the event log records the
    # correct assignee (mirrors POST /integration/escalate). Raises 422 with a clear
    # message when the agent / tier team / membership is not configured.
    # Segment-scoped like the integration route: RR only among tier members serving the
    # contact's market segment(s); untagged contact -> unfiltered (unchanged).
    from app.services.market_segment_service import MarketSegmentService

    contact_segments = MarketSegmentService(db).resolve_segments_for_contact_id(
        str(contact_id)
    )
    from app.services.sla_service import _tracking_company_id

    assignee = service.get_escalation_assignee_for_tier(
        getattr(tracking, "source_entity_type", None),
        target_tier,
        getattr(tracking, "team_set_code", None) or None,
        agent_id_override=getattr(tracking, "agent_id", None) or None,
        contact_segments=contact_segments,
        company_id=_tracking_company_id(tracking),
        brand_code=getattr(tracking, "brand_code", None),
    )
    # AC-F1: escalate the EXACT row this route was called for (tracking_id is the URL
    # param). Without this, escalate_tracking's internal (respond_contact_id, policy_id)
    # resolution would silently pick whichever open ticket for this contact+policy was
    # created most recently - which can be a DIFFERENT ticket than the one in the URL
    # when the contact holds 2+ open tickets on the same policy. Fixed here, not just
    # documented, because this is a manual admin action, not an interim n8n contract.
    tracking = service.escalate_tracking(
        respond_contact_id=str(contact_id),
        policy_id=str(getattr(tracking, "policy_id")),
        current_tier=target_tier,
        escalation_reason=f"manual: {reason}",
        assigned_to_id=str(assignee["id"]),
        tracking_id=str(tracking_id),
        assigned_to_respond_user_id=(
            str(assignee["respond_user_id"])
            if assignee.get("respond_user_id") is not None
            else None
        ),
    )
    # Notify the new assignee - same channels + per-event toggles as form-SLA
    # escalation, so ALL escalations behave the same. Best-effort: the escalation
    # already committed. Kept out of escalate_tracking so both routes (UI here,
    # n8n /integration/escalate) call it explicitly - n8n does NOT send its own.
    _notify_conversation_sla_escalation(db, tracking, assignee, reason)
    # Escalation is a reassignment: push the new-tier owner to the Respond.io
    # conversation too (async + outbox-logged via the respond_io worker), mirroring
    # reassign. Best-effort - the escalation already committed. (The n8n integration
    # escalate owns its own Respond routing, so this lives in the UI route only.)
    service._push_respond_assignee(tracking, assignee.get("respond_user_id"))
    return build_conversation_sla_tracking_response(db, tracking)


@router.post("/{tracking_id}/resolve", response_model=ConversationSLATrackingResponse)
async def resolve_sla_tracking(
    tracking_id: UUID,
    current_user: dict = Depends(get_current_user),
    _perm: dict = Depends(require_permission("sla_management.conversation_sla_tracking.resolve")),
    db: Session = Depends(get_db),
):
    """Mark a conversation SLA task as resolved (stops the clock, closes the Respond
    conversation). UI counterpart of the My Pending "Resolve" button - permission +
    actor-scope gated. The legacy ``PUT /{id}`` remains for the n8n integration path
    (API key) and is NOT used by the widget."""
    service = ConversationSLATrackingService(db)
    tracking = service.get_tracking(str(tracking_id))
    if not tracking:
        raise handle_not_found("Conversation SLA tracking", str(tracking_id))
    if not service.can_user_act_on_tracking(current_user["id"], tracking):
        raise handle_not_found("Conversation SLA tracking", str(tracking_id))
    if not service.can_user_resolve_tracking(current_user["id"], tracking):
        # The row is visible (My Team), just not resolvable by this viewer -
        # a "not found" here would be a lie, so this is the validation error
        # sibling reassign already uses for its own out-of-scope case.
        raise handle_validation_error(
            "This task is assigned to someone else, so you cannot resolve it."
        )
    updated = service.update_tracking(
        str(tracking_id), ConversationSLATrackingUpdate(is_resolved=True)
    )
    return build_conversation_sla_tracking_response(db, updated)


@router.get("/{tracking_id}/ticket")
async def get_intervention_ticket(
    tracking_id: str,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Drawer header + composer state for one intervention ticket (UAC AC-C1).

    Assignee-or-manager scoped, same as resolve/send. Bundles the 24h window
    state and the out-of-window chat-template preview inline - NOT DB-only:
    the window state is resolved via ``get_window_state_for`` ->
    ``RespondClient.list_messages``, a real Respond.io HTTP call (15s
    timeout) - so this ``async def`` route runs it via ``run_in_threadpool``
    (mirrors the sibling ``POST .../ticket/send`` route) instead of blocking
    the event loop for the duration of that call.
    """
    from starlette.concurrency import run_in_threadpool

    service = ConversationSLATrackingService(db)
    sender_name = (current_user.get("name") or "").strip() or "Customer Service"
    return await run_in_threadpool(
        service.get_ticket_detail,
        tracking_id,
        viewer_user_id=current_user["id"],
        sender_name=sender_name,
    )


@router.post("/{tracking_id}/ai-draft")
async def draft_intervention_ticket_reply(
    tracking_id: str,
    body: TicketAIDraftBody,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Draft a reply INTO the composer using the existing CRM AI assistant (AC-L5).

    Never sends: the answer is text the assignee reads, edits and then sends
    themselves, so this route touches no Respond path at all. The thread tail is
    fetched server-side (one less contract surface than posting it up, and a
    client cannot ask for a draft grounded on a conversation it is not looking
    at). Assignee-or-manager scoped like every sibling ticket route - an
    outsider gets a 404 BEFORE any model call, so a stranger cannot spend our
    tokens by guessing ids.
    """
    from starlette.concurrency import run_in_threadpool

    from app.services import conversation_reply_draft_service as draft_service

    service = ConversationSLATrackingService(db)
    tracking = service.get_tracking(tracking_id, load_event_logs=False)
    if not tracking:
        raise handle_not_found("Conversation SLA tracking", tracking_id)
    if not service.can_user_act_on_tracking(current_user["id"], tracking):
        raise handle_not_found("Conversation SLA tracking", tracking_id)

    result = await run_in_threadpool(
        draft_service.draft_reply,
        db,
        tracking,
        instruction=body.instruction,
        tail=body.tail or draft_service.DEFAULT_TAIL,
        user_id=current_user.get("id"),
    )
    return {
        "draft": result.draft,
        "model": result.model,
        "grounded_on": result.grounded_on,
        "elapsed_ms": result.elapsed_ms,
    }


@router.post("/{tracking_id}/ticket/send")
async def send_intervention_ticket_message(
    tracking_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """CRM-native reply from an intervention ticket drawer (UAC AC-D1/D2/D3, E1).

    Synchronous - unlike the generic ``.../conversation/send-message`` smart-send
    route (which queues delivery on the ``respond_io`` worker), the drawer needs
    the actually-attempted payload back immediately to render the delivered
    state and stamp its own response clock. Accepts JSON
    (``{text, reply_to_message_id?, reply_to_excerpt?}``) when there are no
    files, or ``multipart/form-data``
    (``text, reply_to_message_id?, reply_to_excerpt?, files[]``) when there
    are. ``text`` is expected pre-quoted by the composer (the ">" prefix, R1
    quote-reply emulation) - sent verbatim; ``reply_to_*`` fields are
    audit-only and are never sent to Respond.io. Assignee-or-manager scoped,
    same as resolve/escalate.

    A multi-file send never raises on a per-file failure - see the
    ``attachments: {delivered, failed}`` FE contract documented on
    ``ConversationSLATrackingService.send_ticket_message``'s docstring.
    """
    service = ConversationSLATrackingService(db)
    tracking = service.get_tracking(tracking_id, load_event_logs=False)
    if not tracking:
        raise handle_not_found("Conversation SLA tracking", tracking_id)
    if not service.can_user_act_on_tracking(current_user["id"], tracking):
        raise handle_not_found("Conversation SLA tracking", tracking_id)

    content_type = (request.headers.get("content-type") or "").lower()
    files: list[tuple[bytes, str, str]] = []
    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        text = str(form.get("text") or "")
        reply_to_message_id = form.get("reply_to_message_id") or None
        reply_to_excerpt = form.get("reply_to_excerpt") or None
        for item in form.getlist("files"):
            # StarletteUploadFile, NOT fastapi.UploadFile: request.form() yields
            # the starlette class, and fastapi.UploadFile is a strict SUBCLASS
            # of it - so an isinstance check against the FastAPI class NEVER
            # matched, files stayed empty, and every attachment send silently
            # degraded to a text-only send (the outbox logged {"type": "text"}).
            if isinstance(item, StarletteUploadFile) and item.filename:
                content = await item.read()
                files.append((content, item.filename, item.content_type or ""))
    else:
        raw: dict = {}
        try:
            raw = await request.json()
        except Exception:
            raw = {}
        text = str((raw or {}).get("text") or "")
        reply_to_message_id = (raw or {}).get("reply_to_message_id")
        reply_to_excerpt = (raw or {}).get("reply_to_excerpt")

    sender_name = (current_user.get("name") or "").strip() or "Customer Service"

    from starlette.concurrency import run_in_threadpool

    return await run_in_threadpool(
        service.send_ticket_message,
        tracking_id,
        text=text,
        files=files,
        reply_to_message_id=reply_to_message_id,
        reply_to_excerpt=reply_to_excerpt,
        sender_user_id=current_user.get("id"),
        sender_name=sender_name,
    )


@router.post("/integration", status_code=status.HTTP_200_OK)
async def create_sla_tracking_integration(
    tracking_data: ConversationSLATrackingCreate,
    request: Request,
    db: Session = Depends(get_db)
):
    """Create or update SLA tracking from integration and log the request.
    
    If a tracking record already exists for the contact (based on contact_phone_number),
    it will be updated with the new data. Otherwise, a new tracking record will be created.
    """
    try:
        service = ConversationSLATrackingService(db)
        contact_phone_number = tracking_data.contact_phone_number.strip()

        tracking = service.create_tracking(tracking_data)

        log_service = IntegrationLogService(db)
        tracking_id_str = str(getattr(tracking, "id"))
        already_active = bool(getattr(tracking, "_already_active", False))
        # "Update" = create_tracking reused an existing row: idempotent hit on an
        # active one, or overwrite of a resolved one. Markers come from the service -
        # no duplicate pre-query of the singleton check here.
        is_update = already_active or bool(getattr(tracking, "_overwrote_resolved", False))
        if already_active:
            integration_channel = "sla_tracking_idempotent_hit"
        elif is_update:
            integration_channel = "sla_tracking_update"
        else:
            integration_channel = "sla_tracking_creation"
        _log_integration_best_effort(
            log_service,
            IntegrationLogCreate(
                integration_channel=integration_channel,
                business_table="conversation_sla_tracking",
                business_id=tracking_id_str,
                external_reference=contact_phone_number,
                direction="inbound",
                endpoint=str(request.url),
                http_method="POST",
                status="success"
            ),
            request_payload_dict=tracking_data.model_dump()
        )

        if already_active:
            message = "Active SLA tracking already exists for this contact; returned existing tracking."
        elif is_update:
            message = "SLA tracking updated successfully."
        else:
            message = "SLA tracking created successfully."
        return {
            "status": "success",
            "message": message,
            "tracking_id": tracking_id_str,
            "is_update": is_update,
            "already_active": already_active,
            # AC-A2/AC-A4: n8n reads these on EVERY response - insert and retry
            # alike - to keep the ticket's clocks/assignee and to pick the
            # in-hours vs out-of-hours auto-reply copy. `in_working_hours` comes
            # from the service marker; it is not a column.
            "in_working_hours": getattr(tracking, "_in_working_hours", None),
            "initiated_at": getattr(tracking, "initiated_at", None),
            "due_at": getattr(tracking, "due_at", None),
            "due_at_resolution": getattr(tracking, "due_at_resolution", None),
            "assigned_to": getattr(tracking, "assigned_to", None),
            "assigned_to_id": getattr(tracking, "assigned_to_id", None),
        }
    except HTTPException:
        raise
    except AppException:
        # A deliberate refusal - "Respond contact not found for phone number: X" -
        # is a 400 about the caller's input, not a server fault. `handle_validation_error`
        # returns AppException, NOT HTTPException, so the bare arm below re-wrapped it
        # into 500 / INTERNAL_ERROR and the real message survived only as a string
        # stuffed inside the 500 body. Same fix as create_/update_/delete_sla_tracking;
        # this handler is a separate function and was missed.
        # The global handler in app/main.py still serialises it to the caller.
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.put("/{tracking_id}", response_model=ConversationSLATrackingResponse)
async def update_sla_tracking(
    tracking_id: UUID,
    tracking_data: ConversationSLATrackingUpdate,
    request: Request,
    response: Response,
    current_user: dict = Depends(get_current_user_or_api_key),
    db: Session = Depends(get_db)
):
    """Update an SLA tracking record.

    Dual principal: the n8n integration calls this with an API key (bypasses the
    per-action gate - it owns the SLA lifecycle). A human principal flipping
    ``is_resolved`` here must hold the resolve slug, mirroring POST /{id}/resolve
    (defense-in-depth so the PUT path can't bypass the widget's Resolve gate).
    """
    tracking_id_str = str(tracking_id)
    log_service = IntegrationLogService(db)
    if (
        current_user.get("auth_method") != "api_key"
        and bool(getattr(tracking_data, "is_resolved", None))
    ):
        from app.services.user_service import UserPermissionService

        if not UserPermissionService(db).check_user_has_permission(
            current_user["id"], "sla_management.conversation_sla_tracking.resolve"
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Permission required: sla_management.conversation_sla_tracking.resolve",
            )
    try:
        service = ConversationSLATrackingService(db)

        # AC-E3: the n8n Respond-app-reply fallback resolves a contact-level
        # "preferred" tracking id (see external/conversation_sla_tracking.py) and
        # PUTs is_responded=true here. The guard now lives in update_tracking
        # (the shared path this route and PUT/POST /integration/{tracking_id}
        # both call - FINDING 1) so it is enforced for every caller, not
        # duplicated here. When ambiguous, ONLY the responded-family fields
        # (is_responded/responded_at/responded_by/response_time) are dropped
        # from the payload - the rest (assignment, tier, resolve, ...) still
        # applies normally (FINDING 5): a caller sending is_responded alongside
        # other fields must not have the whole update silently discarded.
        # AC-M3 hardening: an API-key resolve here IS n8n's respond-close-convo
        # lane reacting to a Respond-side close. Fire our close-convo webhook
        # back at it and n8n sends the customer a second closing message, so the
        # origin travels with the resolve and suppresses it.
        tracking = service.update_tracking(
            tracking_id_str,
            tracking_data,
            resolve_origin=(
                "api_key" if current_user.get("auth_method") == "api_key" else "user"
            ),
        )
        already_resolved = bool(getattr(tracking, "_already_resolved", False))
        ambiguous = bool(getattr(tracking, "_ambiguous_responded_skipped", False))
        # AC-I3: a duplicate respond is idempotent, reported as a body field
        # rather than the 400 it used to raise.
        already_responded = bool(getattr(tracking, "_already_responded", False))
        tracking_id_result_str = str(getattr(tracking, "id"))
        external_reference = (
            str(getattr(getattr(tracking, "contact", None), "phone_number"))
            if getattr(getattr(tracking, "contact", None), "phone_number", None) is not None
            else tracking_id_result_str
        )
        log_service.create_integration_log(
            IntegrationLogCreate(
                integration_channel="sla_management",
                business_table="conversation_sla_tracking",
                business_id=tracking_id_result_str,
                external_reference=external_reference,
                direction="inbound",
                endpoint=str(request.url),
                http_method="PUT",
                status=(
                    "skipped_ambiguous_response"
                    if ambiguous
                    else (
                        "skipped_already_responded"
                        if already_responded
                        else ("success" if not already_resolved else "skipped_already_resolved")
                    )
                ),
            ),
            request_payload_dict=tracking_data.model_dump(exclude_unset=True),
        )
        if already_resolved:
            response.headers["X-SLA-Already-Resolved"] = "true"
            response.headers["X-SLA-Updated"] = "false"
        fresh = service.get_tracking(tracking_id_result_str)
        # Attach indicators so they flow through `from_attributes` serialization
        # of ConversationSLATrackingResponse. Header forwarding is unreliable
        # through the Next.js proxy, so the body is the source of truth.
        # FINDING 5: an ambiguous response stamp no longer blanks the whole
        # update - `updated_in_request` must say True when the payload also
        # carried non-responded fields that DID apply, and only False when
        # the ambiguous responded-family fields were the entire payload.
        _responded_family_fields = {"is_responded", "responded_at", "responded_by", "response_time"}
        _non_responded_fields_requested = bool(
            set(tracking_data.model_fields_set) - _responded_family_fields
        )
        # AC-I3 rides the same rule: when the responded-family fields were the
        # ENTIRE payload and they were dropped (ambiguous OR already responded),
        # nothing applied.
        _responded_family_dropped = ambiguous or already_responded
        setattr(fresh, "already_resolved", already_resolved)
        setattr(fresh, "already_responded", already_responded)
        setattr(
            fresh,
            "updated_in_request",
            not already_resolved
            and (not _responded_family_dropped or _non_responded_fields_requested),
        )
        setattr(fresh, "ambiguous_responded_skipped", ambiguous)
        return fresh
    except HTTPException:
        raise
    except AppException:
        # A deliberate 4xx (e.g. "Conversation is already responded.") is a refusal,
        # not an integration failure. `handle_validation_error` returns AppException,
        # NOT HTTPException, so without this arm it fell through to the generic
        # handler below and was logged as status="failed" - which is why every
        # historical sla_management "failure" is one benign idempotency race.
        # The global handler in app/main.py still serialises it to the caller.
        raise
    except Exception as e:
        try:
            log_service.create_integration_log(
                IntegrationLogCreate(
                    integration_channel="sla_management",
                    business_table="conversation_sla_tracking",
                    business_id=tracking_id_str,
                    external_reference="",
                    direction="inbound",
                    endpoint=str(request.url),
                    http_method="PUT",
                    status="failed",
                    error_message=str(e),
                ),
                request_payload_dict=tracking_data.model_dump(exclude_unset=True),
            )
        except Exception:
            pass
        raise handle_internal_error(str(e))


@router.post("/{tracking_id}/sync-assignee")
async def sync_assignee_from_respond(
    tracking_id: UUID,
    current_user: dict = Depends(get_current_user_or_api_key),
    db: Session = Depends(get_db),
):
    """Sync assignee from Respond.io: fetch contact by phone, match assignee.id to user respond_user_id, update assigned_to if different.

    AC-F2 (multi-open consumer audit): deprecated no-op for conversation-family
    tickets - see ``ConversationSLATrackingService.sync_assignee_from_respond``.
    Returns 200 with ``deprecated: true`` rather than erroring, so the existing UI
    button keeps working (shows the deprecation message via the toast) without a
    frontend change.
    """
    try:
        service = ConversationSLATrackingService(db)
        result = service.sync_assignee_from_respond(str(tracking_id))
        return result
    except HTTPException:
        raise
    except httpx.HTTPStatusError as e:
        code = e.response.status_code
        if code == 404:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"message": "Contact not found in Respond.io", "code": "CONTACT_NOT_FOUND"})
        if code == 401:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail={"message": "Respond.io API unauthorized", "code": "UNAUTHORIZED"})
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail={"message": f"Respond.io API error: HTTP {code}", "code": f"HTTP_{code}"})
    except httpx.TimeoutException:
        raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail={"message": "Respond.io API timed out", "code": "TIMEOUT"})
    except httpx.ConnectError:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail={"message": "Failed to connect to Respond.io", "code": "CONNECTION_ERROR"})
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post("/{tracking_id}/test-overrides", status_code=status.HTTP_200_OK)
async def post_sla_tracking_test_overrides(
    tracking_id: UUID,
    body: ConversationSLATestOverrideRequest,
    _current_user: dict = Depends(
        require_permission("sla_management.conversation_sla_tracking.test_override")
    ),
    db: Session = Depends(get_db),
):
    """Override assignee or SLA timestamps (permission-gated; for testing workflows)."""
    try:
        service = ConversationSLATrackingService(db)
        service.admin_test_override_tracking(
            str(tracking_id), body.model_dump(exclude_unset=True)
        )
        return {"message": "Updated"}
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.delete("/{tracking_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_sla_tracking(
    tracking_id: UUID,
    current_user: dict = Depends(get_current_user_or_api_key),
    db: Session = Depends(get_db)
):
    """Delete an SLA tracking record.

    The integration log is the SERVICE's, not this route's: since S6b the same delete
    is also reached by the deferred `sla_tracking.delete` record action, which commits
    from a sweep or a poll with no request in sight, and a log written here would have
    stopped being written for every deletion started from the UI.
    """
    tracking_id_str = str(tracking_id)
    try:
        ConversationSLATrackingService(db).delete_tracking(tracking_id_str)
        return None
    except HTTPException:
        raise
    except AppException:
        # A deliberate 4xx (e.g. "Conversation is already responded.") is a refusal,
        # not an integration failure. `handle_validation_error` returns AppException,
        # NOT HTTPException, so without this arm it fell through to the generic
        # handler below and was logged as status="failed" - which is why every
        # historical sla_management "failure" is one benign idempotency race.
        # The global handler in app/main.py still serialises it to the caller.
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


def _to_aware_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Normalize an inbound timestamp to aware UTC, matching the column convention.

    These columns hold naive **UTC** (create_tracking stamps them from
    `_now_utc()`, update_tracking runs every datetime field through the
    service's own `_to_aware_utc`), so a naive value already means UTC and an
    aware one converts to it. The route previously ran aware values through
    `to_naive_datetime`, which produces naive **Malaysia** wall clock - storing
    an n8n-reported reply eight hours after it happened.
    """
    if not isinstance(dt, datetime):
        return dt
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _calculate_duration_hours(start_at, end_at) -> Decimal:
    """Calculate duration in hours with two-decimal precision.

    Handles both naive and timezone-aware datetimes. Naive means UTC - that is
    what the tracking columns store, so reading `initiated_at` as UTC+8 (as
    this used to) inflated every duration computed here by eight hours.
    """
    start_at_utc = _to_aware_utc(start_at)
    end_at_utc = _to_aware_utc(end_at)
    if start_at_utc is None or end_at_utc is None:
        # No clock start to measure from (same fallback update_tracking uses).
        return Decimal("0.00")

    hours = Decimal(str((end_at_utc - start_at_utc).total_seconds() / 3600))
    return hours.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@router.post("/integration/{tracking_id}", status_code=status.HTTP_200_OK)
@router.put("/integration/{tracking_id}", status_code=status.HTTP_200_OK)
async def update_sla_tracking_status_integration(
    tracking_id: UUID,
    update_data: ConversationSLATrackingStatusUpdate,
    request: Request,
    response: Response,
    db: Session = Depends(get_db)
):
    """Update SLA tracking status fields from integration and log the request.

    AC-E4 / AC-F1 (multi-open consumer audit): already ticket_id-scoped (the caller
    supplies the exact tracking_id in the URL - there is no "resolve by contact"
    variant), so it is NOT ambiguous under multi-open by itself. The audit risk is
    entirely in HOW n8n picks the id: if a Respond "conversation closed" webhook
    resolves a contact-keyed GET (e.g. the external preferred-tracking endpoint) and
    feeds that id straight into `is_resolved=true` here, it can resolve the wrong
    (or an arbitrary) open ticket for a contact holding several. Per AC-E4, resolution
    driven purely by a Respond close event should stop once n8n moves to per-ticket
    ids (S3.2) - kept working as-is until then (regression net 3).
    """
    try:
        tracking_id_str = str(tracking_id)
        service = ConversationSLATrackingService(db)
        tracking = service.get_tracking(tracking_id_str)

        if update_data.is_responded and not update_data.responded_at:
            raise handle_validation_error("responded_at is required when is_responded is true.")
        # resolved_at is optional when is_resolved is true; service will set it to now if missing

        update_dict = update_data.model_dump(exclude_unset=True)
        update_dict.pop("response_time", None)
        update_dict.pop("resolution_duration", None)
        update_dict.pop("resolution_time", None)

        # Normalize timestamps to UTC before storing (the columns are naive UTC)
        if update_data.responded_at:
            update_dict["responded_at"] = _to_aware_utc(update_data.responded_at)
            update_dict["response_time"] = _calculate_duration_hours(
                tracking.initiated_at, update_dict["responded_at"]
            )
        if update_data.resolved_at:
            update_dict["resolved_at"] = _to_aware_utc(update_data.resolved_at)
            update_dict["resolution_duration"] = _calculate_duration_hours(
                tracking.initiated_at, update_dict["resolved_at"]
            )

        # This route has no auth dependency at all - it IS the n8n integration
        # lane, so a resolve arriving here never fires the close-convo webhook
        # (AC-M3 hardening; see update_tracking's `resolve_origin`).
        tracking = service.update_tracking(
            tracking_id_str,
            ConversationSLATrackingUpdate(**update_dict),
            resolve_origin="api_key",
        )
        already_resolved = bool(getattr(tracking, "_already_resolved", False))
        # FINDING 1: update_tracking is the shared path PUT /{tracking_id} also
        # calls, so the AC-E3 ambiguity guard now applies here too - a stamp it
        # skipped must not get a "response" event log written on top of it.
        ambiguous_responded_skipped = bool(getattr(tracking, "_ambiguous_responded_skipped", False))
        # AC-I3: a duplicate respond short-circuits in the service now (it used to
        # raise 400). The stamp did not happen, so no "response" event log is owed.
        already_responded = bool(getattr(tracking, "_already_responded", False))
        if already_resolved:
            response.headers["X-SLA-Already-Resolved"] = "true"
            response.headers["X-SLA-Updated"] = "false"
        if already_responded:
            response.headers["X-SLA-Already-Responded"] = "true"

        # Create event logs for responded/resolved when applicable
        if (
            update_data.is_responded
            and not already_resolved
            and not ambiguous_responded_skipped
            and not already_responded
        ):
            # Aware UTC for the event log too: create_event_log reads a NAIVE
            # datetime as Malaysia time, which would shift the logged instant.
            responded_at_utc = _to_aware_utc(update_data.responded_at)

            responded_by_ref = (
                str(getattr(tracking, "responded_by"))
                if getattr(tracking, "responded_by", None) is not None
                else None
            )
            alabel, aid = event_log_assignee_fields(db, responded_by_ref)

            _write_event_log_best_effort(service, ConversationSLAEventLogCreate(
                sla_tracking_id=tracking_id_str,
                event_type="response",
                event_at=responded_at_utc,
                response_time=update_dict.get("response_time"),
                assigned_to=alabel,
                assigned_to_id=aid,
            ))

        if update_data.is_resolved and not already_resolved:
            # Use tracking.resolved_at (set by service if not sent) for event log
            resolved_at_utc = _to_aware_utc(
                update_data.resolved_at or getattr(tracking, "resolved_at", None)
            )

            resolved_by_ref = (
                str(getattr(tracking, "resolved_by"))
                if getattr(tracking, "resolved_by", None) is not None
                else None
            )
            rlabel, rid = event_log_assignee_fields(db, resolved_by_ref)

            _write_event_log_best_effort(service, ConversationSLAEventLogCreate(
                sla_tracking_id=tracking_id_str,
                event_type="resolution",
                event_at=resolved_at_utc,
                resolution_time=update_dict.get("resolution_duration"),
                assigned_to=rlabel,
                assigned_to_id=rid,
            ))

        log_service = IntegrationLogService(db)
        tracking_id_result_str = str(getattr(tracking, "id"))
        external_reference = (
            str(getattr(getattr(tracking, "contact", None), "phone_number"))
            if getattr(getattr(tracking, "contact", None), "phone_number", None) is not None
            else tracking_id_result_str
        )
        _log_integration_best_effort(
            log_service,
            IntegrationLogCreate(
                integration_channel="sla_tracking_update",
                business_table="conversation_sla_tracking",
                business_id=tracking_id_result_str,
                external_reference=external_reference,
                direction="inbound",
                endpoint=str(request.url),
                http_method="PUT",
                status=(
                    "skipped_ambiguous_response"
                    if ambiguous_responded_skipped
                    else (
                        "skipped_already_responded"
                        if already_responded
                        else ("success" if not already_resolved else "skipped_already_resolved")
                    )
                ),
            ),
            request_payload_dict=update_data.model_dump(exclude_unset=True)
        )

        if already_resolved:
            return {
                "status": "skipped",
                "message": "Conversation is already resolved; no update applied.",
                "tracking_id": tracking_id_result_str,
                "already_resolved": True,
                "already_responded": already_responded,
                "updated": False,
            }
        if already_responded:
            # AC-I3: 200 + marker, mirroring the already-resolved arm. n8n branches
            # on `updated` and must never see a 4xx for a benign duplicate.
            return {
                "status": "skipped",
                "message": "Conversation is already responded; response clocks untouched.",
                "tracking_id": tracking_id_result_str,
                "already_responded": True,
                "updated": False,
            }
        return {
            "status": "success",
            "message": "SLA tracking updated successfully.",
            "tracking_id": tracking_id_result_str,
            "ambiguous_responded_skipped": ambiguous_responded_skipped,
            "already_responded": False,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post("/event-logs", response_model=ConversationSLAEventLogResponse, status_code=status.HTTP_201_CREATED)
async def create_event_log(
    event_data: ConversationSLAEventLogCreate,
    current_user: dict = Depends(get_current_user_or_api_key),
    db: Session = Depends(get_db)
):
    """Create a new SLA event log entry."""
    try:
        payload = event_data
        if event_data.assigned_to and not event_data.assigned_to_id:
            from app.models.user import User

            assigned_to_value = str(event_data.assigned_to).strip()
            user = db.query(User).filter(
                (User.respond_user_id == assigned_to_value) |
                (User.id == assigned_to_value) |
                (User.email == assigned_to_value)
            ).first()
            if not user:
                raise handle_validation_error(
                    f"User not found for respond_user_id: {assigned_to_value}"
                )
            payload = ConversationSLAEventLogCreate(
                **{**event_data.model_dump(exclude_unset=True), "assigned_to_id": user.id}
            )

        service = ConversationSLATrackingService(db)
        log = service.create_event_log(payload)
        return log
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.delete("/event-logs/{log_id}", status_code=status.HTTP_200_OK)
async def delete_event_log(
    log_id: UUID,
    current_user: dict = Depends(get_current_user_or_api_key),
    db: Session = Depends(get_db)
):
    """Delete an event log entry. Only admins can delete logs."""
    try:
        # Check if user is admin
        user_role = current_user.get("role") if isinstance(current_user, dict) else None
        if user_role != "admin":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only administrators can delete event logs"
            )
        
        log_id_str = str(log_id)
        service = ConversationSLATrackingService(db)
        result = service.delete_event_log(log_id_str)
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.get("/by-source", response_model=list[ConversationSLATrackingResponse])
async def list_trackers_by_source(
    source_entity_type: str = Query(..., description="stock_inquiry | purchase_request | sponsorship_form | complaint | ticket"),
    source_entity_id: str = Query(..., description="Form row id"),
    current_user: dict = Depends(get_current_user_or_api_key),
    db: Session = Depends(get_db),
):
    """All SLA trackers attached to a form row, ordered oldest first (multi-stage chain)."""
    try:
        rows = (
            db.query(ConversationSLATracking)
            .filter(
                ConversationSLATracking.source_entity_type == source_entity_type,
                ConversationSLATracking.source_entity_id == source_entity_id,
            )
            .order_by(ConversationSLATracking.initiated_at.asc())
            .all()
        )
        return [
            build_conversation_sla_tracking_response(db, t, include_event_logs=True)
            for t in rows
        ]
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.get("/event-logs", response_model=ListResponse[ConversationSLAEventLogResponse])
async def get_event_logs(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=MAX_PAGE_LIMIT),
    sort: Optional[str] = Query("event_at"),
    dir: Optional[str] = Query("desc"),
    tracking_id: Optional[str] = Query(None),
    event_type: Optional[str] = Query(None),
    assigned_to: Optional[str] = Query(None),
    assigned_to_id: Optional[str] = Query(None, description="Filter by user id (assigned_to_id)"),
    date_from: Optional[str] = Query(None, description="Filter event_at >= date (ISO date or datetime)"),
    date_to: Optional[str] = Query(None, description="Filter event_at <= date (ISO date or datetime, end of day)"),
    current_user: dict = Depends(get_current_user_or_api_key),
    db: Session = Depends(get_db)
):
    """Get SLA event logs with pagination and filtering."""
    try:
        service = ConversationSLATrackingService(db)
        result = service.list_event_logs(
            page=page,
            limit=limit,
            sort_field=sort,
            sort_dir=dir,
            tracking_id=tracking_id,
            event_type=event_type,
            assigned_to=assigned_to,
            assigned_to_id=assigned_to_id,
            date_from=date_from,
            date_to=date_to,
        )
        return result
    except Exception as e:
        raise handle_internal_error(str(e))


@router.get("/{tracking_id}/conversation")
async def get_sla_tracking_conversation(
    tracking_id: UUID,
    limit: int = Query(50, ge=1, le=MAX_PAGE_LIMIT),
    cursor: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user_or_api_key),
    db: Session = Depends(get_db),
):
    """List Respond.io messages for the contact linked to this SLA tracking (respond_io_id on respond_contacts).

    The listing is a real Respond.io HTTP call (``RespondClient.list_messages``,
    15s timeout) behind a synchronous service method, so this ``async def``
    route hands it to ``run_in_threadpool`` rather than blocking the event loop
    for its duration - same treatment as its siblings ``GET .../ticket`` and
    ``POST .../ticket/send``. The drawer opens both, milliseconds apart.
    """
    from starlette.concurrency import run_in_threadpool

    try:
        service = ConversationSLATrackingService(db)
        return await run_in_threadpool(
            service.fetch_respond_conversation_for_tracking,
            str(tracking_id),
            limit=limit,
            cursor=cursor,
        )
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise handle_internal_error(str(e))


@router.get("/{tracking_id}/conversation/page")
async def get_sla_tracking_conversation_page(
    tracking_id: UUID,
    before: Optional[str] = Query(
        None, description="Message id to page OLDER than (exclusive)"
    ),
    after: Optional[str] = Query(
        None, description="Message id to page NEWER than (exclusive)"
    ),
    around: Optional[str] = Query(
        None, description="Message id to centre the window on (jump to a search match)"
    ),
    limit: int = Query(
        50, ge=1, le=200, description="Messages per thread page (a chat window, not a grid page)"
    ),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """One scroll-back window of the contact thread (UAC AC-L7).

    Assignee-or-manager scoped like the sibling ``GET .../ticket``: this serves
    a contact's WhatsApp conversation, so an outsider gets a 404 (never a 403,
    which would confirm the ticket exists).

    Items always come back **oldest-to-newest**, whichever direction was asked
    for, so the caller never reverses. ``has_more_older`` is true whenever the
    page came back full; the client keeps asking until it is false, which is the
    true start of the conversation.

    Read-only from the user's point of view: it never marks anything read and
    never touches the conversation-window cache. It does opportunistically write
    fetched Respond messages into ``chat_histories`` so in-thread search reaches
    history that predates our ingest - best-effort, and a failure there does not
    fail the read.
    """
    from starlette.concurrency import run_in_threadpool

    cursors = [c for c in (before, after, around) if c]
    if len(cursors) > 1:
        raise HTTPException(
            status_code=422,
            detail="Pass at most one of before, after, around.",
        )

    try:
        service = ConversationSLATrackingService(db)
        return await run_in_threadpool(
            lambda: service.fetch_conversation_thread_page(
                str(tracking_id),
                viewer_user_id=current_user["id"],
                before=before,
                after=after,
                around=around,
                limit=limit,
            )
        )
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise handle_internal_error(str(e))


@router.get("/{tracking_id}/media")
async def proxy_ticket_media(
    tracking_id: UUID,
    url: str = Query(..., description="Attachment URL on an allowlisted media host"),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Viewer-scoped byte proxy for an attachment in THIS ticket's thread (AC-N4).

    Scoped exactly like the sibling thread reads - an outsider gets a 404, never
    a 403 - and the URL itself must be on the media allowlist
    (``media_proxy_service``), so this can never be used as a general URL
    fetcher. The contact-keyed twin lives under ``/conversations/{ref}/media``
    and differs only in its gate.
    """
    from app.services import media_proxy_service

    ConversationSLATrackingService(db).require_ticket_in_scope(
        str(tracking_id), current_user["id"]
    )
    return await media_proxy_service.stream(url)


@router.get("/{tracking_id}/conversation/search")
async def search_sla_tracking_conversation(
    tracking_id: UUID,
    q: str = Query("", description="Free text searched inside this contact's messages"),
    limit: int = Query(
        100, ge=1, le=200, description="Search matches returned (a chat window, not a grid page)"
    ),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """In-thread message search for this tracking's contact (UAC AC-L8).

    ILIKE over the stored message body, newest-first and capped. Side-effect
    free. Each hit carries the message id the thread jumps to via
    ``GET .../conversation/page?around=<message_id>``. Assignee-or-manager
    scoped like the page read - an outsider gets a 404, never a 403.
    """
    from starlette.concurrency import run_in_threadpool

    try:
        service = ConversationSLATrackingService(db)
        return await run_in_threadpool(
            lambda: service.search_conversation_thread(
                str(tracking_id),
                viewer_user_id=current_user["id"],
                q=q,
                limit=limit,
            )
        )
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post("/{tracking_id}/conversation/reply")
async def post_sla_tracking_conversation_reply(
    tracking_id: UUID,
    body: SlaTrackingConversationReplyBody,
    request: Request,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Send a text message to the Respond.io contact for this SLA tracking."""
    try:
        respond_user_id = _sla_reply_respond_user_id(current_user)
        service = ConversationSLATrackingService(db)
        service.send_conversation_reply_for_tracking(
            str(tracking_id),
            body.message,
            respond_user_id=respond_user_id,
            crm_sender_user_id=current_user.get("id"),
            request_url=str(request.url) if request else "",
        )
        db.commit()
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.get("/{tracking_id}/comments", response_model=list[TicketCommentResponse])
async def list_ticket_comments(
    tracking_id: str,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Internal comments on this ticket, oldest first (UAC AC-L1).

    Assignee-or-manager scoped, same as resolve/escalate/send: an out-of-scope
    viewer gets a 404, never a 403 (no existence leak). The list also carries
    the CONTACT-scoped comments ingested from Respond's own inbox (AC-L3),
    which belong to every open ticket for that contact rather than to one.
    """
    return TicketCommentService(db).list_for_tracking(
        tracking_id, viewer_user_id=current_user["id"]
    )


@router.post(
    "/{tracking_id}/comments",
    response_model=TicketCommentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_ticket_comment(
    tracking_id: str,
    payload: TicketCommentCreate,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Write an internal note on this ticket (UAC AC-L1).

    NEVER reaches the contact: this path touches no Respond send route at all.
    The only outbound call is the AC-L2 comment mirror, which runs post-commit,
    best-effort, against Respond's internal comment endpoint. Mentioned users
    get an in-app notification with a deep link back to the ticket.
    """
    return TicketCommentService(db).create_comment(
        tracking_id,
        author_user_id=current_user["id"],
        body=payload.body,
        mentioned_user_ids=payload.mentioned_user_ids,
    )


@router.get("/{tracking_id}", response_model=ConversationSLATrackingResponse)
async def get_sla_tracking_record(
    tracking_id: UUID,
    current_user: dict = Depends(get_current_user_or_api_key),
    db: Session = Depends(get_db)
):
    """Get a single SLA tracking record by ID."""
    try:
        tracking_id_str = str(tracking_id)
        service = ConversationSLATrackingService(db)
        tracking = service.get_tracking(tracking_id_str)
        return build_conversation_sla_tracking_response(db, tracking)
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


def _resolve_sla_conversation_contact(db: Session, tracking_id: str):
    """(respond_io identifier, internal respond_contact_id) for a conversation SLA
    tracking. Identifier is the linked RespondContact's respond_io_id (not the
    internal UUID) - see CLAUDE.md respond_contact_id ≠ respond_io_id."""
    tracking = (
        db.query(ConversationSLATracking)
        .filter(ConversationSLATracking.id == str(tracking_id))
        .first()
    )
    if tracking is None:
        return None, None
    respond_contact_id = getattr(tracking, "respond_contact_id", None)
    identifier = None
    contact = getattr(tracking, "contact", None)
    if contact is not None:
        identifier = (str(getattr(contact, "respond_io_id", "") or "").strip()) or None
    return identifier, (str(respond_contact_id) if respond_contact_id else None)


# Conversation SLA is form-less: no view-link, no prefill buttons. It inherits the
# same shared smart-send route as the entity chat panels via conversation_chat.
from app.api.v1._respond_chat_template_routes import build_chat_template_router

router.include_router(
    build_chat_template_router(
        business_table="conversation_sla_tracking",
        resolver=_resolve_sla_conversation_contact,
        chat_use_case="conversation_chat",
        context_builder=None,
    )
)
