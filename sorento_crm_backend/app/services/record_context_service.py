"""Deterministic record-context assembler for the in-system AI bubble.

Joins a record row + its approval/rejection decision + the form-SLA tracker +
the audit rows that set its state into one structured, AI-ready bundle. Pure
read assembly - NO writes, and NEVER touches ``respond_contacts.session_vars``
(that store is n8n/WhatsApp-only; see UAC §4.7).

Four entity types are supported, dispatched through ``_ADAPTERS``:

  * ``complaint`` - tracer; real approval gate, on-row rejection columns.
  * ``stock_inquiry`` - rejection-only (no approve), on-row rejection columns.
  * ``purchase_request`` - real approval gate (``request_type='purchase_request'``).
  * ``sponsorship_form`` - same model as PR (``request_type='sponsorship_form'``),
    shares the ``purchase_request`` audit entity_type but a distinct
    ``sponsorship_form`` SLA ``source_entity_type``.

All four are form-SLA, discriminated by ``conversation_sla_tracking.source_entity_type``.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.audit import AuditLog
from app.models.complaints import Complaint
from app.models.procurement import PurchaseRequestHeader, StockInquiry
from app.models.sla import ConversationSLATracking, SLAPolicyTier
from app.services.error_handler import AppException

# Asia/Kuala_Lumpur - fixed UTC+8 (no DST). Mirrors sla_service.MALAYSIA_TZ.
MALAYSIA_TZ = timezone(timedelta(hours=8))

# Approval-decision statuses that an approval-gated record can reach.
_APPROVAL_STATUSES = ("approved", "rejected")

# View permission required per entity type. None => any authenticated user.
# Centralized here so the HTTP route AND the bubble pre-route agree.
_VIEW_PERMISSION: dict[str, Optional[str]] = {
    "complaint": "complaint_management.complaints.view",
    "stock_inquiry": None,
    "purchase_request": None,
    "sponsorship_form": None,
}


def record_context_view_permission(entity_type: str) -> Optional[str]:
    """Return the view-permission slug required for an entity type.

    ``None`` means any authenticated user may read the record context. Unknown
    entity types also return ``None`` (the assembler still 400s on assemble).
    """
    return _VIEW_PERMISSION.get(entity_type)


def _to_kl_iso(dt: Any) -> Optional[str]:
    """Render a naive-UTC timestamp as an Asia/Kuala_Lumpur ISO-8601 string.

    All DB timestamp columns store naive UTC. We attach UTC, convert to +08,
    and emit ISO with the offset so the LLM never re-derives a timezone.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(MALAYSIA_TZ).isoformat()


def _elapsed_hours(start: Any, end: Any) -> Optional[float]:
    """Hours between two naive-UTC timestamps, rounded to 1dp. None if missing."""
    if start is None or end is None:
        return None
    delta = end - start
    return round(delta.total_seconds() / 3600.0, 1)


def _pr_display_status(status: Any, approval_status: Any) -> str:
    """Single user-facing status for PR / sponsorship_form.

    Users see ONE status pill that combines the lifecycle ``status`` and the
    ``approval_status`` - they do not distinguish the two internal columns.
    This MUST mirror the frontend ``getDisplayStatus`` (PurchaseRequestsList.tsx)
    so the bubble's answer matches the pill on screen: terminal CS states win,
    otherwise the approval decision, otherwise the lifecycle status.
    """
    s = (status or "").strip().lower()
    if s == "processed_by_cs":
        return "Processed by CS"
    if s == "closed":
        return "Closed"
    a = (approval_status or "").strip().lower()
    if a == "pending":
        return "Pending approval"
    if a == "approved":
        return "Approved"
    if a == "rejected":
        return "Rejected"
    if s == "submitted":
        return "Submitted"
    return "Draft"


class RecordContextService:
    """Assemble a deterministic record-context bundle for a viewed record."""

    def __init__(self, db: Session):
        self.db = db
        # entity_type -> bound adapter method. Built per-instance so each adapter
        # closes over ``self``; ``assemble`` dispatches through it.
        self._ADAPTERS: dict[str, Callable[[str], dict]] = {
            "complaint": self._assemble_complaint,
            "stock_inquiry": self._assemble_stock_inquiry,
            "purchase_request": self._assemble_purchase_request,
            "sponsorship_form": self._assemble_sponsorship_form,
        }

    # ---------------------------------------------------------------- dispatch

    def assemble(self, entity_type: str, entity_id: str) -> dict:
        """Return the structured record-context bundle for an entity.

        Unsupported types raise a 400 so the caller degrades gracefully; a
        missing row raises 404. New types register an adapter in ``_ADAPTERS``.
        """
        adapter = self._ADAPTERS.get(entity_type)
        if adapter is None:
            raise AppException(
                status_code=400,
                message=f"Unsupported record-context entity type: {entity_type}",
                code="UNSUPPORTED_ENTITY_TYPE",
            )
        return adapter(entity_id)

    # ------------------------------------------------------------- user names

    def _batch_user_display_names(self, user_ids: Iterable[str]) -> dict[str, str]:
        """Map {user id or respond_user_id -> display name} in one query.

        Mirrors ComplaintService._batch_user_display_names so a CRM id OR a
        respond_user_id both resolve. No N+1.
        """
        ids = {str(u).strip() for u in user_ids if u and str(u).strip()}
        if not ids:
            return {}
        from app.models.user import User

        users = (
            self.db.query(User)
            .filter(or_(User.id.in_(ids), User.respond_user_id.in_(ids)))
            .all()
        )
        out: dict[str, str] = {}
        for user in users:
            u: Any = user
            name = u.name or u.email or None
            if not name:
                continue
            if getattr(u, "id", None):
                out[str(u.id)] = name
            if getattr(u, "respond_user_id", None):
                out[str(u.respond_user_id)] = name
        return out

    # ---------------------------------------------------------- shared builders

    def _status_change_audit_rows(
        self, audit_entity_type: str, entity_id: str
    ) -> list[Any]:
        """Status-change audit rows for an entity, newest-first.

        Both PR and sponsorship_form use the SAME ``purchase_request`` audit
        entity_type, so callers pass it explicitly.
        """
        audit_rows = (
            self.db.query(AuditLog)
            .filter(
                AuditLog.entity_type == audit_entity_type,
                AuditLog.entity_id == str(entity_id),
                AuditLog.action == "UPDATE",
            )
            .order_by(AuditLog.changed_at.desc())
            .all()
        )
        return [row for row in audit_rows if _new_status(row) is not None]

    def _build_audit_trail(
        self, status_change_rows: list[Any], name_map: dict[str, str]
    ) -> list[dict[str, Any]]:
        """Status transitions newest-first, no-op rows filtered, cap 10."""
        audit_trail: list[dict[str, Any]] = []
        for row in status_change_rows:
            if len(audit_trail) >= 10:
                break
            old_status = _old_status(row)
            new_status = _new_status(row)
            if old_status == new_status:
                # Skip no-op rows (a save that didn't change status) - they add
                # noise like "approved → approved" without a real transition.
                continue
            audit_trail.append(
                {
                    "action": f"status: {old_status or '-'} → {new_status}",
                    "by": name_map.get(str(row.user_id)) if row.user_id else None,
                    "at": _to_kl_iso(row.changed_at),
                }
            )
        return audit_trail

    def _build_sla(
        self,
        tracker: Any,
        name_map: dict[str, str],
    ) -> Optional[dict]:
        """Derive the SLA block from the form-SLA tracker; None when absent."""
        if tracker is None:
            return None

        now = datetime.utcnow()
        due_at = tracker.due_at
        is_breached = bool(
            due_at is not None and now > due_at and not tracker.is_responded
        )

        tier_name = None
        target_hours = None
        if tracker.policy_id:
            tier_row = (
                self.db.query(SLAPolicyTier)
                .filter(
                    SLAPolicyTier.policy_id == tracker.policy_id,
                    SLAPolicyTier.tier_level == tracker.current_tier,
                )
                .first()
            )
            if tier_row is not None:
                tier: Any = tier_row
                tier_name = tier.tier_name
                target_hours = (
                    float(tier.response_hours)
                    if tier.response_hours is not None
                    else None
                )

        assignee = None
        if tracker.assigned_to_id:
            assignee = name_map.get(str(tracker.assigned_to_id))
        if assignee is None and tracker.assigned_to:
            assignee = name_map.get(str(tracker.assigned_to)) or tracker.assigned_to

        elapsed = _elapsed_hours(tracker.initiated_at, now)
        lead_breached = (
            elapsed is not None
            and target_hours is not None
            and elapsed > target_hours
        )

        return {
            "current_tier": tracker.current_tier,
            "tier_name": tier_name,
            "assignee": assignee,
            "due_at": _to_kl_iso(due_at),
            "is_breached": is_breached,
            "lead_time": {
                "elapsed_hours": elapsed,
                "target_hours": target_hours,
                "breached": bool(lead_breached),
            },
        }

    def _latest_tracker(
        self, source_entity_type: str, entity_id: str
    ) -> Optional[ConversationSLATracking]:
        """Latest form-SLA tracker for an entity (any resolution state).

        ``source_entity_type`` discriminates form-SLA stage rows: complaint /
        stock_inquiry / purchase_request / sponsorship_form (the latter distinct
        from purchase_request even though the audit entity_type is shared).
        """
        return (
            self.db.query(ConversationSLATracking)
            .filter(
                ConversationSLATracking.source_entity_type == source_entity_type,
                ConversationSLATracking.source_entity_id == str(entity_id),
            )
            .order_by(ConversationSLATracking.initiated_at.desc())
            .first()
        )

    # -------------------------------------------------------------- complaint

    def _assemble_complaint(self, complaint_id: str) -> dict:
        row = (
            self.db.query(Complaint).filter(Complaint.id == complaint_id).first()
        )
        if row is None:
            raise AppException(
                status_code=404,
                message="Complaint not found.",
                code="NOT_FOUND",
            )
        complaint: Any = row

        status_value = complaint.status

        # --- audit trail: status-change rows only, newest first --------------
        status_change_rows = self._status_change_audit_rows(
            "complaint", complaint_id
        )

        # --- approval decision -----------------------------------------------
        # Latest audit row that set status to approved/rejected = the decision.
        decision_row: Any = next(
            (
                row
                for row in status_change_rows
                if _new_status(row) in _APPROVAL_STATUSES
            ),
            None,
        )

        # Collect every user id we need to resolve, then one batched lookup.
        wanted_ids: set[str] = set()
        for row in status_change_rows[:10]:
            if row.user_id:
                wanted_ids.add(str(row.user_id))
        if decision_row is not None and decision_row.user_id:
            wanted_ids.add(str(decision_row.user_id))
        if complaint.rejected_by:
            wanted_ids.add(str(complaint.rejected_by))

        tracker: Any = self._latest_tracker("complaint", complaint_id)
        if tracker is not None and tracker.assigned_to_id:
            wanted_ids.add(str(tracker.assigned_to_id))
        if tracker is not None and tracker.assigned_to:
            wanted_ids.add(str(tracker.assigned_to))

        name_map = self._batch_user_display_names(wanted_ids)

        # --- current_state ---------------------------------------------------
        is_rejected = status_value == "rejected"
        set_by_id = decision_row.user_id if decision_row is not None else None
        set_at_dt = decision_row.changed_at if decision_row is not None else None
        if is_rejected and complaint.rejected_by:
            set_by_id = complaint.rejected_by
            set_at_dt = complaint.rejected_at or set_at_dt
        current_state = {
            "status": status_value,
            "set_by": name_map.get(str(set_by_id)) if set_by_id else None,
            "set_at": _to_kl_iso(set_at_dt),
            "reason": complaint.rejection_reason if is_rejected else None,
        }

        # --- approval block --------------------------------------------------
        approval = self._build_complaint_approval(
            complaint=complaint,
            decision_row=decision_row,
            name_map=name_map,
            is_rejected=is_rejected,
        )

        sla = self._build_sla(tracker, name_map)
        audit_trail = self._build_audit_trail(status_change_rows, name_map)

        return {
            "entity_type": "complaint",
            "id": str(complaint.id),
            "display_ref": complaint.complaint_number or "Complaint",
            "about": {
                "complaint_type": complaint.complaint_type,
                "product_type": complaint.product_type,
                "description": complaint.defect_description,
                "customer_name": complaint.customer_name,
            },
            "current_state": current_state,
            "approval": approval,
            "sla": sla,
            "audit_trail": audit_trail,
        }

    def _build_complaint_approval(
        self,
        *,
        complaint: Any,
        decision_row: Any,
        name_map: dict[str, str],
        is_rejected: bool,
    ) -> Optional[dict]:
        """Derive the complaint approval block.

        Null only when the complaint never reached approved/rejected. ``status``
        is "approved"/"rejected" when a terminal decision is recorded, else
        "pending" (a decision row exists but the current status moved on).
        """
        current = complaint.status
        if current not in _APPROVAL_STATUSES and decision_row is None:
            return None

        if current in _APPROVAL_STATUSES:
            decision_status = current
        else:
            decision_status = "pending"

        decided_by_id = decision_row.user_id if decision_row is not None else None
        decided_at_dt = decision_row.changed_at if decision_row is not None else None
        # When currently rejected, prefer the denormalized on-row columns.
        if is_rejected and complaint.rejected_by:
            decided_by_id = complaint.rejected_by
            decided_at_dt = complaint.rejected_at or decided_at_dt

        comments = complaint.rejection_reason if is_rejected else None
        elapsed = _elapsed_hours(complaint.created_at, decided_at_dt)

        return {
            "status": decision_status,
            "decided_by": name_map.get(str(decided_by_id)) if decided_by_id else None,
            "decided_at": _to_kl_iso(decided_at_dt),
            "comments": comments,
            "lead_time": {
                "elapsed_hours": elapsed,
                "target_hours": None,
                "breached": None,
            },
        }

    # ----------------------------------------------------------- stock_inquiry

    def _assemble_stock_inquiry(self, inquiry_id: str) -> dict:
        row = (
            self.db.query(StockInquiry)
            .filter(StockInquiry.id == inquiry_id)
            .first()
        )
        if row is None:
            raise AppException(
                status_code=404,
                message="Stock inquiry not found.",
                code="NOT_FOUND",
            )
        inquiry: Any = row
        status_value = inquiry.status
        is_rejected = status_value == "rejected"
        is_responded = status_value == "responded"

        status_change_rows = self._status_change_audit_rows(
            "stock_inquiry", inquiry_id
        )
        # Latest audit row that set the inquiry to rejected = the decision.
        decision_row: Any = next(
            (row for row in status_change_rows if _new_status(row) == "rejected"),
            None,
        )

        wanted_ids: set[str] = set()
        for row in status_change_rows[:10]:
            if row.user_id:
                wanted_ids.add(str(row.user_id))
        if decision_row is not None and decision_row.user_id:
            wanted_ids.add(str(decision_row.user_id))
        if inquiry.rejected_by:
            wanted_ids.add(str(inquiry.rejected_by))
        if inquiry.last_responded_by:
            wanted_ids.add(str(inquiry.last_responded_by))

        tracker: Any = self._latest_tracker("stock_inquiry", inquiry_id)
        if tracker is not None and tracker.assigned_to_id:
            wanted_ids.add(str(tracker.assigned_to_id))
        if tracker is not None and tracker.assigned_to:
            wanted_ids.add(str(tracker.assigned_to))

        name_map = self._batch_user_display_names(wanted_ids)

        # --- who/when set the current state ----------------------------------
        # Priority: rejection (rejected_*) > response (last_responded_*) > the
        # latest status-change audit row. A stock inquiry has NO approval gate - 
        # "responded" means purchasing has answered (not awaiting a decision).
        if is_rejected and (inquiry.rejected_by or inquiry.rejected_at):
            set_by_id = inquiry.rejected_by or (
                decision_row.user_id if decision_row is not None else None
            )
            set_at_dt = inquiry.rejected_at or (
                decision_row.changed_at if decision_row is not None else None
            )
        elif is_responded and (inquiry.last_responded_by or inquiry.last_responded_at):
            set_by_id = inquiry.last_responded_by
            set_at_dt = inquiry.last_responded_at
        else:
            latest = status_change_rows[0] if status_change_rows else None
            set_by_id = latest.user_id if latest is not None else None
            set_at_dt = latest.changed_at if latest is not None else None

        current_state = {
            "status": status_value,
            "set_by": name_map.get(str(set_by_id)) if set_by_id else None,
            "set_at": _to_kl_iso(set_at_dt),
            "reason": inquiry.rejection_reason if is_rejected else None,
        }

        # --- response block (the purchasing answer) --------------------------
        # Surfaces "who responded / when / what" so those questions are
        # answerable. None when no response has been sent yet.
        response = None
        if inquiry.last_responded_by or inquiry.last_responded_at or inquiry.purchasing_response:
            resp_text = (inquiry.purchasing_response or "").strip() or None
            response = {
                "responded_by": (
                    name_map.get(str(inquiry.last_responded_by))
                    if inquiry.last_responded_by
                    else None
                ),
                "responded_at": _to_kl_iso(inquiry.last_responded_at),
                "summary": resp_text[:500] if resp_text else None,
                "lead_time_hours": _elapsed_hours(
                    inquiry.created_at, inquiry.last_responded_at
                ),
            }

        # --- decision block (rejection-only; null when not rejected) ---------
        # Stock inquiry has no approve action, so there is no "pending approval"
        # decision - only a possible rejection. Null avoids implying the record
        # is awaiting a decision when it is actually new / in-progress / answered.
        approval = None
        if is_rejected:
            approval = {
                "status": "rejected",
                "decided_by": name_map.get(str(set_by_id)) if set_by_id else None,
                "decided_at": _to_kl_iso(set_at_dt),
                "comments": inquiry.rejection_reason,
                "lead_time": {
                    "elapsed_hours": _elapsed_hours(inquiry.created_at, set_at_dt),
                    "target_hours": None,
                    "breached": None,
                },
            }

        sla = self._build_sla(tracker, name_map)
        audit_trail = self._build_audit_trail(status_change_rows, name_map)

        return {
            "entity_type": "stock_inquiry",
            "id": str(inquiry.id),
            "display_ref": inquiry.inquiry_number or "Stock Inquiry",
            "about": {
                "title": inquiry.project_name,
                "description": inquiry.item_description,
                "customer": inquiry.project_customer,
                "salesperson": inquiry.salesperson,
                "product_code": inquiry.product_code,
            },
            "current_state": current_state,
            "response": response,
            "approval": approval,
            "sla": sla,
            "audit_trail": audit_trail,
        }

    # ----------------------------------------------- purchase_request / sponsor

    def _assemble_purchase_request(self, request_id: str) -> dict:
        return self._assemble_request(
            request_id,
            request_type="purchase_request",
            entity_type="purchase_request",
            sla_source_entity_type="purchase_request",
            display_fallback="Purchase Request",
            extra_about=False,
        )

    def _assemble_sponsorship_form(self, request_id: str) -> dict:
        return self._assemble_request(
            request_id,
            request_type="sponsorship_form",
            entity_type="sponsorship_form",
            sla_source_entity_type="sponsorship_form",
            display_fallback="Sponsorship Form",
            extra_about=True,
        )

    def _assemble_request(
        self,
        request_id: str,
        *,
        request_type: str,
        entity_type: str,
        sla_source_entity_type: str,
        display_fallback: str,
        extra_about: bool,
    ) -> dict:
        """Shared assembler for PurchaseRequestHeader (PR + sponsorship_form).

        Both rows live in ``purchase_requests`` discriminated by ``request_type``
        and share the ``purchase_request`` AUDIT entity_type, but each has its
        own SLA ``source_entity_type``.
        """
        row = (
            self.db.query(PurchaseRequestHeader)
            .filter(
                PurchaseRequestHeader.id == request_id,
                PurchaseRequestHeader.request_type == request_type,
            )
            .first()
        )
        if row is None:
            raise AppException(
                status_code=404,
                message=f"{display_fallback} not found.",
                code="NOT_FOUND",
            )
        req: Any = row
        approval_status = req.approval_status
        # Single user-facing status combining lifecycle + approval (mirrors the
        # FE pill). The bubble must answer with THIS, not the raw split.
        display_status = _pr_display_status(req.status, approval_status)
        is_rejected = (approval_status or "").strip().lower() == "rejected"

        # Both PR and sponsorship_form audit under "purchase_request".
        status_change_rows = self._status_change_audit_rows(
            "purchase_request", request_id
        )
        decision_row: Any = next(
            (
                r
                for r in status_change_rows
                if _new_status(r) in _APPROVAL_STATUSES
            ),
            None,
        )

        wanted_ids: set[str] = set()
        for r in status_change_rows[:10]:
            if r.user_id:
                wanted_ids.add(str(r.user_id))
        if decision_row is not None and decision_row.user_id:
            wanted_ids.add(str(decision_row.user_id))
        if req.approver_user_id:
            wanted_ids.add(str(req.approver_user_id))
        if req.approved_by:
            wanted_ids.add(str(req.approved_by))

        tracker: Any = self._latest_tracker(sla_source_entity_type, request_id)
        if tracker is not None and tracker.assigned_to_id:
            wanted_ids.add(str(tracker.assigned_to_id))
        if tracker is not None and tracker.assigned_to:
            wanted_ids.add(str(tracker.assigned_to))

        name_map = self._batch_user_display_names(wanted_ids)

        # Prefer the FK user id for the name, fall back to the (often email) text.
        decided_by_name = None
        if req.approver_user_id:
            decided_by_name = name_map.get(str(req.approver_user_id))
        if decided_by_name is None and req.approved_by:
            decided_by_name = name_map.get(str(req.approved_by)) or req.approved_by

        # --- current_state ---------------------------------------------------
        # set_by/set_at from the decision cols, falling back to the latest
        # status audit row when the decision cols are null.
        set_by_name = decided_by_name
        set_at_dt = req.approved_at
        if set_at_dt is None and decision_row is not None:
            set_at_dt = decision_row.changed_at
        if set_by_name is None and decision_row is not None and decision_row.user_id:
            set_by_name = name_map.get(str(decision_row.user_id))

        current_state = {
            "status": display_status,
            "set_by": set_by_name,
            "set_at": _to_kl_iso(set_at_dt),
            "reason": req.approval_comments if is_rejected else None,
        }

        # --- approval block --------------------------------------------------
        approval = self._build_request_approval(
            req=req,
            approval_status=approval_status,
            decided_by_name=decided_by_name,
        )

        sla = self._build_sla(tracker, name_map)
        audit_trail = self._build_audit_trail(status_change_rows, name_map)

        about: dict[str, Any] = {
            "title": req.project_title,
            "description": req.purpose,
            "customer_name": req.customer_name,
        }
        if extra_about:
            about["sponsor_subject"] = req.sponsor_subject
            about["project_value"] = req.total_project_value_text

        return {
            "entity_type": entity_type,
            "id": str(req.id),
            "display_ref": req.request_number or display_fallback,
            "about": about,
            "current_state": current_state,
            "approval": approval,
            "sla": sla,
            "audit_trail": audit_trail,
        }

    def _build_request_approval(
        self,
        *,
        req: Any,
        approval_status: Optional[str],
        decided_by_name: Optional[str],
    ) -> Optional[dict]:
        """Approval block for PR / sponsorship_form from the approval columns.

        Null only when no approval workflow has started (no approval_status and
        no decision timestamp).
        """
        if approval_status is None and req.approved_at is None:
            return None

        return {
            "status": approval_status or "pending",
            "decided_by": decided_by_name,
            "decided_at": _to_kl_iso(req.approved_at),
            "comments": req.approval_comments,
            "lead_time": {
                "elapsed_hours": _elapsed_hours(req.created_at, req.approved_at),
                "target_hours": None,
                "breached": None,
            },
        }


def _new_status(row: Any) -> Optional[str]:
    """Extract ``new_values->>'status'`` from an audit row, if present."""
    nv = row.new_values
    if isinstance(nv, dict):
        val = nv.get("status")
        return str(val) if val is not None else None
    return None


def _old_status(row: Any) -> Optional[str]:
    """Extract ``old_values->>'status'`` from an audit row, if present."""
    ov = row.old_values
    if isinstance(ov, dict):
        val = ov.get("status")
        return str(val) if val is not None else None
    return None
