"""SLA takeover cooldown: pending-intent takeover with a veto window.

A peer clicks Takeover -> instead of an instant reassignment, a `pending`
``sla_takeover_request`` is created with ``commit_at = now + cooldown``. Nothing about
the SLA assignment changes until commit. During the window the contested assignee can
Reject, the initiator can Cancel, and any owner terminal action (resolve / reassign /
escalate) voids it. The scheduler sweep (``commit_due``) commits unchallenged rows past
``commit_at`` after re-validating the task is still takeable.

cooldown == 0 OR an unassigned task -> commit instantly inline (pre-feature behavior).

See PLAN-takeover-cooldown.md and UAC-takeover-cooldown.md.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.models.sla import (
    SlaTakeoverRequest,
    TAKEOVER_PENDING,
    TAKEOVER_COMMITTED,
    TAKEOVER_CANCELLED,
    TAKEOVER_REJECTED,
    TAKEOVER_VOIDED,
)
from app.models.user import SystemSetting, User
from app.services.error_handler import (
    AppException,
    handle_not_found,
    handle_validation_error,
)

logger = logging.getLogger(__name__)


def _now_naive() -> datetime:
    return datetime.utcnow()


class SlaTakeoverService:
    """Lifecycle (initiate / cancel / reject / commit / void) for takeover requests."""

    def __init__(self, db: Session):
        self.db = db

    # ----- config ------------------------------------------------------------
    def cooldown_seconds(self) -> int:
        row = self.db.query(SystemSetting).first()
        val = getattr(row, "takeover_cooldown_seconds", 60) if row else 60
        try:
            return max(0, int(val))
        except (TypeError, ValueError):
            return 60

    def _is_admin(self, user_id: str) -> bool:
        from app.services.user_service import UserPermissionService

        return bool(
            UserPermissionService(self.db).get_user_role_slugs(str(user_id))
            & {UserPermissionService.SUPERADMIN_ROLE_SLUG, "admin"}
        )

    def _sla(self):
        from app.services.sla_service import ConversationSLATrackingService

        return ConversationSLATrackingService(self.db)

    def _user_name(self, user_id: Optional[str]) -> str:
        if not user_id:
            return "a teammate"
        u = self.db.query(User).filter(User.id == str(user_id)).first()
        return ((u.name or u.email) if u else None) or "a teammate"

    # ----- serialization -----------------------------------------------------
    def serialize(
        self,
        req: SlaTakeoverRequest,
        viewer_id: Optional[str] = None,
        viewer_is_admin: bool = False,
    ) -> dict:
        d = {
            "request_id": str(req.id),
            "tracking_id": str(req.tracking_id),
            "initiator_id": str(req.initiator_id),
            "initiator_name": self._user_name(req.initiator_id),
            "contested_assignee_id": (
                str(req.contested_assignee_id) if req.contested_assignee_id else None
            ),
            "contested_assignee_name": (
                self._user_name(req.contested_assignee_id)
                if req.contested_assignee_id
                else None
            ),
            "team_id": str(req.team_id) if req.team_id else None,
            "status": req.status,
            "commit_at": req.commit_at.isoformat() if req.commit_at else None,
            # Total cooldown window (seconds) so the FE bar has a fixed denominator that
            # survives remounts (tab switches). Derived from the two stored UTC stamps.
            "window_seconds": (
                max(1, int((req.commit_at - req.created_at).total_seconds()))
                if (req.commit_at and req.created_at)
                else None
            ),
            "resolution_reason": req.resolution_reason,
        }
        if viewer_id is not None:
            # Viewer-relative affordances. Cancel is the INITIATOR's action, Reject is
            # the CONTESTED assignee's - never both for the same person:
            # - the initiator only Cancels (rejecting your own takeover is nonsense),
            # - the contested assignee only Rejects,
            # - a pure admin bystander (neither) may do either.
            is_initiator = str(req.initiator_id) == str(viewer_id)
            is_contested = (
                req.contested_assignee_id is not None
                and str(req.contested_assignee_id) == str(viewer_id)
            )
            d["can_cancel"] = is_initiator or (viewer_is_admin and not is_contested)
            d["can_reject"] = is_contested or (viewer_is_admin and not is_initiator)
        return d

    def get_pending_for_tracking(self, tracking_id: str) -> Optional[SlaTakeoverRequest]:
        return (
            self.db.query(SlaTakeoverRequest)
            .filter(
                SlaTakeoverRequest.tracking_id == str(tracking_id),
                SlaTakeoverRequest.status == TAKEOVER_PENDING,
            )
            .first()
        )

    def pending_by_tracking_ids(
        self, tracking_ids, viewer_id: Optional[str] = None
    ) -> dict[str, dict]:
        """Map tracking_id -> serialized pending request, for list/widget rendering.
        When ``viewer_id`` is given, each row carries can_cancel/can_reject flags."""
        ids = [str(t) for t in (tracking_ids or []) if t]
        if not ids:
            return {}
        rows = (
            self.db.query(SlaTakeoverRequest)
            .filter(
                SlaTakeoverRequest.tracking_id.in_(ids),
                SlaTakeoverRequest.status == TAKEOVER_PENDING,
            )
            .all()
        )
        admin = self._is_admin(viewer_id) if viewer_id else False
        return {
            str(r.tracking_id): self.serialize(r, viewer_id=viewer_id, viewer_is_admin=admin)
            for r in rows
        }

    def latest_for_tracking(self, tracking_id: str) -> Optional[SlaTakeoverRequest]:
        """Most recent request for a tracking (pending or terminal) - for the banner's
        terminal-state display (AC-LINK-4)."""
        return (
            self.db.query(SlaTakeoverRequest)
            .filter(SlaTakeoverRequest.tracking_id == str(tracking_id))
            .order_by(SlaTakeoverRequest.created_at.desc())
            .first()
        )

    def get_takeover_row(self, tracking_id: str, user_id: str) -> dict:
        """Pin-fetch a single contested task + its latest takeover, for the My Pending
        banner. Visible to the assignee or anyone who can act on it."""
        sla = self._sla()
        tracking = sla.get_tracking(str(tracking_id), load_event_logs=False)
        # One 404 for a missing row and for a viewer outside the task's scope (the assignee,
        # or anyone whose Team Tasks show it): the reference below is the contact's phone or
        # name, and a 403 would confirm the id to a stranger. LIST-API-PERMS.
        if tracking is None or not sla.can_user_act_on_tracking(user_id, tracking):
            raise handle_not_found("SLA Tracking", str(tracking_id))
        from app.services.form_sla_service import FORM_SLA_TYPES

        ref = (sla._resolve_my_pending_references([tracking]) or {}).get(
            str(getattr(tracking, "id"))
        )
        latest = self.latest_for_tracking(tracking_id)
        admin = self._is_admin(user_id) if user_id else False
        return {
            "id": str(getattr(tracking, "id")),
            "source_entity_type": getattr(tracking, "source_entity_type", None),
            "source_entity_id": getattr(tracking, "source_entity_id", None),
            "is_form_sla": getattr(tracking, "source_entity_type", None) in FORM_SLA_TYPES,
            "reference": ref,
            "due_at": (
                getattr(tracking, "due_at").isoformat()
                if getattr(tracking, "due_at", None)
                else None
            ),
            "current_tier": getattr(tracking, "current_tier", None),
            "is_resolved": bool(getattr(tracking, "is_resolved", False)),
            "assigned_to_id": (
                str(getattr(tracking, "assigned_to_id"))
                if getattr(tracking, "assigned_to_id", None)
                else None
            ),
            "takeover": (
                self.serialize(latest, viewer_id=user_id, viewer_is_admin=admin)
                if latest
                else None
            ),
        }

    # ----- initiate ----------------------------------------------------------
    def initiate(self, tracking_id: str, initiator_id: str, team_id: str) -> dict:
        """Create a pending takeover (or commit instantly for cooldown 0 / unassigned).

        Returns ``{committed: bool, request?: {...}}``. On an existing pending row,
        raises AppException(409) carrying the existing request so the UI shows the bar.
        """
        sla = self._sla()
        tracking = sla.get_tracking(tracking_id, load_event_logs=False)
        if bool(getattr(tracking, "is_resolved", False)):
            raise handle_validation_error("Cannot take over a resolved SLA task.")
        contested_id = getattr(tracking, "assigned_to_id", None)
        # Permission == visibility for an assigned task. An UNOWNED task is grabbable by
        # anyone who can see its queue team (nothing to protect, AC-INIT-3).
        if not sla.can_user_act_on_tracking(initiator_id, tracking):
            if contested_id is not None or str(team_id) not in sla._visible_team_ids(
                initiator_id
            ):
                raise handle_not_found("SLA Tracking", tracking_id)

        cooldown = self.cooldown_seconds()

        # Instant path: cooldown disabled OR no owner to protect.
        if cooldown <= 0 or contested_id is None:
            committed = sla.takeover(tracking_id, initiator_id, team_id)
            return {"committed": True, "tracking_id": str(getattr(committed, "id"))}

        # One pending per tracking (FCFS). Surface the existing one so the route can
        # return 409 with the request payload (UI shows the running bar, not an error).
        existing = self.get_pending_for_tracking(tracking_id)
        if existing is not None:
            return {
                "committed": False,
                "already_pending": True,
                "request": self.serialize(existing),
            }

        started = _now_naive()
        req = SlaTakeoverRequest(
            tracking_id=str(tracking_id),
            initiator_id=str(initiator_id),
            contested_assignee_id=str(contested_id) if contested_id else None,
            team_id=str(team_id),
            status=TAKEOVER_PENDING,
            # created_at forced to UTC (matches commit_at) so the window length is
            # server-timezone-independent.
            created_at=started,
            commit_at=started + timedelta(seconds=cooldown),
        )
        self.db.add(req)
        self.db.commit()
        self.db.refresh(req)

        self._notify_start(req, tracking)
        return {"committed": False, "request": self.serialize(req)}

    # ----- cancel / reject ---------------------------------------------------
    def _load_pending_request(self, request_id: str) -> SlaTakeoverRequest:
        req = (
            self.db.query(SlaTakeoverRequest)
            .filter(SlaTakeoverRequest.id == str(request_id))
            .first()
        )
        if not req:
            raise handle_not_found("Takeover request", request_id)
        if req.status != TAKEOVER_PENDING:
            raise handle_validation_error(
                f"This takeover is no longer pending (status: {req.status})."
            )
        return req

    def cancel(self, request_id: str, actor_id: str) -> dict:
        """Initiator (or admin) withdraws the pending takeover."""
        req = self._load_pending_request(request_id)
        if str(req.initiator_id) != str(actor_id) and not self._is_admin(actor_id):
            raise handle_not_found("Takeover request", request_id)
        self._finalize(req, TAKEOVER_CANCELLED, actor_id, "cancel")
        self._notify_cancel(req)
        return self.serialize(req)

    def reject(self, request_id: str, actor_id: str) -> dict:
        """Contested assignee (or admin) vetoes the pending takeover."""
        req = self._load_pending_request(request_id)
        is_owner = (
            req.contested_assignee_id is not None
            and str(req.contested_assignee_id) == str(actor_id)
        )
        if not is_owner and not self._is_admin(actor_id):
            raise handle_not_found("Takeover request", request_id)
        self._finalize(req, TAKEOVER_REJECTED, actor_id, "reject")
        self._notify_reject(req, actor_id)
        return self.serialize(req)

    def _finalize(
        self,
        req: SlaTakeoverRequest,
        status: str,
        actor_id: Optional[str],
        reason: str,
    ) -> None:
        setattr(req, "status", status)
        setattr(req, "resolution_reason", reason)
        setattr(req, "resolved_by_id", str(actor_id) if actor_id else None)
        setattr(req, "resolved_at", _now_naive())
        self.db.commit()
        self.db.refresh(req)

    # ----- active void (owner terminal actions) ------------------------------
    def void_for_tracking(self, tracking_id: str, reason: str) -> None:
        """Best-effort: void any pending takeover on this tracking (owner resolved /
        reassigned / escalated). Never raises - the owner action already committed."""
        try:
            req = self.get_pending_for_tracking(tracking_id)
            if req is None:
                return
            self._finalize(req, TAKEOVER_VOIDED, None, reason)
            self._notify_void(req, reason)
        except Exception as e:  # noqa: BLE001 - post-commit side effect
            self.db.rollback()
            logger.warning("void_for_tracking(%s) failed: %s", tracking_id, e)

    # ----- commit sweep ------------------------------------------------------
    def commit_due(self) -> dict:
        """Commit pending requests past ``commit_at`` after re-validation. Voids those
        whose premise changed. Returns counts. Each request runs in its own try so one
        bad row never blocks the rest."""
        now = _now_naive()
        due = (
            self.db.query(SlaTakeoverRequest)
            .filter(
                SlaTakeoverRequest.status == TAKEOVER_PENDING,
                SlaTakeoverRequest.commit_at <= now,
            )
            .all()
        )
        committed = voided = 0
        for req in due:
            try:
                if self._commit_one(req):
                    committed += 1
                else:
                    voided += 1
            except Exception as e:  # noqa: BLE001
                self.db.rollback()
                logger.warning("takeover commit failed for %s: %s", req.id, e)
        return {"due": len(due), "committed": committed, "voided": voided}

    def _commit_one(self, req: SlaTakeoverRequest) -> bool:
        """Re-validate then commit one request. Returns True if committed, else voided."""
        sla = self._sla()
        # Fresh read of the task (re-validation, Q14).
        tracking = sla.get_tracking(str(req.tracking_id), load_event_logs=False)
        # (1) resolved meanwhile
        if bool(getattr(tracking, "is_resolved", False)):
            self._finalize(req, TAKEOVER_VOIDED, None, "resolved")
            self._notify_void(req, "resolved")
            return False
        # (2) owner changed since the snapshot
        current_owner = getattr(tracking, "assigned_to_id", None)
        if (
            req.contested_assignee_id is not None
            and str(current_owner or "") != str(req.contested_assignee_id)
        ):
            self._finalize(req, TAKEOVER_VOIDED, None, "reassigned")
            self._notify_void(req, "reassigned")
            return False
        # (3) initiator still eligible - reuse the synchronous takeover (re-derives
        # tier/team/agent at commit, flips assignee, RR cursor, event log, Respond push,
        # AND notifies new+old). Ineligible -> takeover raises not-found -> void.
        try:
            sla.takeover(str(req.tracking_id), str(req.initiator_id), str(req.team_id))
        except AppException:
            self._finalize(req, TAKEOVER_VOIDED, None, "ineligible")
            self._notify_void(req, "ineligible")
            return False
        self._finalize(req, TAKEOVER_COMMITTED, str(req.initiator_id), "committed")
        return True

    # ----- notifications -----------------------------------------------------
    def _link(self, tracking_id: str) -> str:
        from app.config import settings

        base = (getattr(settings, "frontend_base_url", None) or "").strip().rstrip("/")
        return f"{base}/?takeover={tracking_id}" if base else ""

    def _ref(self, tracking) -> str:
        try:
            return (self._sla()._resolve_my_pending_references([tracking]) or {}).get(
                str(getattr(tracking, "id"))
            ) or "an SLA task"
        except Exception:  # noqa: BLE001
            return "an SLA task"

    def _load_tracking(self, req: SlaTakeoverRequest):
        try:
            return self._sla().get_tracking(str(req.tracking_id), load_event_logs=False)
        except Exception:  # noqa: BLE001
            return None

    def _notify_start(self, req: SlaTakeoverRequest, tracking) -> None:
        """Contested assignee: 'X wants to take over, reject by T' + deep link.
        In-app always; email/WhatsApp gated by THEIR assignment toggles. Same WhatsApp
        mechanism as SLA assignment (window-aware template/text + outbox log) and the
        same coverage fan-out."""
        if not req.contested_assignee_id:
            return
        try:
            from app.services.notification_service import NotificationService
            from app.services.coverage_subscription_service import fan_out_coverage_copies
            from app.services.form_sla_service import build_sla_whatsapp_data

            initiator = self._user_name(req.initiator_id)
            ref = self._ref(tracking)
            link = self._link(str(req.tracking_id))
            title = "Someone wants to take over your task"
            body = (
                f"{initiator} wants to take over {ref}. It will be reassigned unless you "
                f"reject it in time."
            )
            if link:
                body += f"\n\nReview / reject: {link}"
            wa = build_sla_whatsapp_data(
                self.db, tracking, str(req.contested_assignee_id), body,
                use_case="sla_takeover_pending",
                extra_vars={"initiator": initiator},
            )
            data = {"tracking_id": str(req.tracking_id), "request_id": str(req.id), **wa}
            NotificationService(self.db).create_with_channel_preferences(
                user_id=str(req.contested_assignee_id),
                type="conversation_sla",
                title=title,
                body=body,
                data=data,
                source_entity_type="sla_takeover",
                source_entity_id=str(req.id),
                event_type="takeover_pending",
                send_in_app=True,
                send_email=True,
                send_whatsapp=True,
                email_pref_attr="notify_email_on_assignment",
                whatsapp_pref_attr="notify_whatsapp_on_assignment",
            )
            # Same fan-out as SLA assignment: copy to anyone covering the contested
            # assignee (they cover this person's queue, so a contested task concerns them).
            fan_out_coverage_copies(
                self.db,
                target_user_id=str(req.contested_assignee_id),
                actor_user_id=str(req.initiator_id),
                notification_type="conversation_sla",
                title=title,
                body=body,
                data=data,
                source_entity_type="sla_takeover",
                source_entity_id=str(req.id),
                event_type="takeover_pending",
                email_pref_attr="notify_email_on_assignment",
                whatsapp_pref_attr="notify_whatsapp_on_assignment",
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("takeover start-notify failed for %s: %s", req.id, e)

    def _notify_reject(self, req: SlaTakeoverRequest, actor_id: str) -> None:
        """Initiator: the owner kept the task. In-app + assignment toggles; same
        window-aware WhatsApp mechanism + outbox log."""
        try:
            from app.services.notification_service import NotificationService
            from app.services.form_sla_service import build_sla_whatsapp_data

            owner = self._user_name(req.contested_assignee_id)
            body = f"{owner} kept the task - your takeover was rejected."
            wa = build_sla_whatsapp_data(
                self.db, self._load_tracking(req), str(req.initiator_id), body,
                use_case="sla_takeover_cancelled",
            )
            NotificationService(self.db).create_with_channel_preferences(
                user_id=str(req.initiator_id),
                type="conversation_sla",
                title="Takeover rejected",
                body=body,
                data={"tracking_id": str(req.tracking_id), "request_id": str(req.id), **wa},
                source_entity_type="sla_takeover",
                source_entity_id=str(req.id),
                event_type="takeover_rejected",
                send_in_app=True,
                send_email=True,
                send_whatsapp=True,
                email_pref_attr="notify_email_on_assignment",
                whatsapp_pref_attr="notify_whatsapp_on_assignment",
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("takeover reject-notify failed for %s: %s", req.id, e)

    def _notify_cancel(self, req: SlaTakeoverRequest) -> None:
        """Contested assignee: initiator withdrew. In-app ONLY (low stakes)."""
        if not req.contested_assignee_id:
            return
        try:
            from app.services.notification_service import NotificationService

            initiator = self._user_name(req.initiator_id)
            NotificationService(self.db).create_with_channel_preferences(
                user_id=str(req.contested_assignee_id),
                type="conversation_sla",
                title="Takeover withdrawn",
                body=f"{initiator} withdrew the takeover of your task.",
                data={"tracking_id": str(req.tracking_id), "request_id": str(req.id)},
                source_entity_type="sla_takeover",
                source_entity_id=str(req.id),
                event_type="takeover_cancelled",
                send_in_app=True,
                send_email=False,
                send_whatsapp=False,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("takeover cancel-notify failed for %s: %s", req.id, e)

    def _notify_void(self, req: SlaTakeoverRequest, reason: str) -> None:
        """Initiator: the task moved/closed out from under the pending takeover."""
        try:
            from app.services.notification_service import NotificationService

            from app.services.form_sla_service import build_sla_whatsapp_data

            reason_text = {
                "resolved": "the task was resolved",
                "reassigned": "the task was reassigned",
                "escalated": "the task was escalated",
                "ineligible": "you are no longer eligible to take it",
            }.get(reason, "the task changed")
            body = f"Your takeover was cancelled because {reason_text}."
            wa = build_sla_whatsapp_data(
                self.db, self._load_tracking(req), str(req.initiator_id), body,
                use_case="sla_takeover_cancelled",
            )
            NotificationService(self.db).create_with_channel_preferences(
                user_id=str(req.initiator_id),
                type="conversation_sla",
                title="Takeover cancelled",
                body=body,
                data={"tracking_id": str(req.tracking_id), "request_id": str(req.id), **wa},
                source_entity_type="sla_takeover",
                source_entity_id=str(req.id),
                event_type="takeover_voided",
                send_in_app=True,
                send_email=True,
                send_whatsapp=True,
                email_pref_attr="notify_email_on_assignment",
                whatsapp_pref_attr="notify_whatsapp_on_assignment",
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("takeover void-notify failed for %s: %s", req.id, e)
