"""
Upload Activity feed endpoint.

GET /api/v1/resources/upload-activity?scope=me&since=<iso>&limit=<int>

Joins `attachments` (filtered by `uploaded_by = current_user` + `created_at >= since`)
with the latest `integration_log` row per attachment (business_table='attachments').
Groups by `attachments.upload_batch_id`:
- batch_id == NULL                       → one "single" session per attachment
- batch_id matches an `import_jobs.job_id` → "bulk_zip" session, surfaces import_job_id
- otherwise (multi-file modal)            → "multi" session keyed on the batch UUID

Returns response shape defined in `app.schemas.upload_activity`.
See docs/plans/PLAN-upload-activity-drawer.md §4.3.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, desc
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models.integration import IntegrationLog
from app.models.job import ImportJob
from app.models.resources import Attachment, AttachmentType
from app.schemas.upload_activity import (
    LinkedEntity,
    SessionAggregate,
    UnlinkedReason,
    UploadActivityFile,
    UploadActivityResponse,
    UploadActivitySession,
)
from app.services.resources_service import AttachmentService

logger = logging.getLogger(__name__)

router = APIRouter()


# ---- helpers --------------------------------------------------------------


def _parse_response_payload(raw: Optional[str]) -> dict:
    """integration_log.response_payload is TEXT - defensive JSON parse."""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _file_status_from_log(
    log: Optional[IntegrationLog],
    linked: List[LinkedEntity],
) -> str:
    """Map integration_log.status + actual DB linkages to FE file status.

    Ground-truth precedence:
    - If any real linkages exist in DB → "linked" regardless of log state.
        Legacy n8n payloads frequently lack the v1 schema entirely; the row
        is still linked via product_attachments etc.
    - else fall back to the log's status / v1 outcome.
    """
    if linked:
        return "linked"
    if log is None:
        return "processing"
    s = (log.status or "").lower()
    if s == "success":
        payload = _parse_response_payload(log.response_payload)
        outcome = payload.get("outcome")
        if outcome == "unlinked":
            return "unlinked"
        if outcome == "failed":
            return "failed"
        return "linked"
    if s == "failed":
        return "failed"
    if s in ("pending", "processing", "sent"):
        return "processing"
    return "processing"


def _linked_entities_from_db(
    db: Session, attachment_id: str, *, log: Optional[IntegrationLog]
) -> List[LinkedEntity]:
    """
    Build the linked entity list from the canonical link tables - not from
    response_payload. The integration_log payload schema is unreliable in
    the wild; product_attachments / promotion_attachments / forms /
    inbound_shipments are the source of truth.

    `matched_by` defaults to "manual_or_n8n" because we can't tell which
    pipeline drew the line from the link tables alone.
    """
    service = AttachmentService(db)
    try:
        groups = service.get_linked_entities(attachment_id) or {}
    except Exception:  # noqa: BLE001
        return []

    out: List[LinkedEntity] = []
    for p in groups.get("linked_products") or []:
        out.append(
            LinkedEntity(
                entity_type="product",
                entity_id=str(p.get("id", "")),
                display_name=str(p.get("name") or p.get("id", "") or "product"),
                matched_by="manual_or_n8n",
            )
        )
    for pr in groups.get("linked_promotions") or []:
        out.append(
            LinkedEntity(
                entity_type="promotion",
                entity_id=str(pr.get("id", "")),
                display_name=str(pr.get("name") or pr.get("id", "") or "promotion"),
                matched_by="manual_or_n8n",
            )
        )
    form = groups.get("linked_form")
    if form:
        out.append(
            LinkedEntity(
                entity_type="form",
                entity_id=str(form.get("id", "")),
                display_name=str(form.get("name") or form.get("id", "") or "form"),
                matched_by="manual_or_n8n",
            )
        )
    for pl in groups.get("linked_packing_lists") or []:
        out.append(
            LinkedEntity(
                entity_type="packing_list",
                entity_id=str(pl.get("id", "")),
                display_name=str(pl.get("name") or pl.get("id", "") or "packing list"),
                matched_by="manual_or_n8n",
            )
        )
    # Without this a certificate PDF reads as "unlinked" in the upload drawer,
    # even though filing it created the register row - which is the whole point
    # of that upload.
    for c in groups.get("linked_certificates") or []:
        out.append(
            LinkedEntity(
                entity_type="certificate",
                entity_id=str(c.get("id", "")),
                display_name=str(c.get("name") or c.get("id", "") or "certificate"),
                matched_by="manual_or_n8n",
            )
        )
    return out


def _unlinked_reasons(log: Optional[IntegrationLog]) -> List[UnlinkedReason]:
    if log is None:
        return []
    payload = _parse_response_payload(log.response_payload)
    raw = payload.get("unlinked_reasons") or []
    out: List[UnlinkedReason] = []
    for item in raw:
        if isinstance(item, dict) and "reason" in item:
            out.append(UnlinkedReason(reason=str(item["reason"])))
    return out


def _summary(log: Optional[IntegrationLog], outcome_status: str) -> str:
    if log is None:
        return ""
    payload = _parse_response_payload(log.response_payload)
    return str(payload.get("summary") or "")


def _error_message(log: Optional[IntegrationLog]) -> Optional[str]:
    """Error text for the drawer, falling back to the payload.

    n8n's error branch posts ``{"status": "failed", "response_payload":
    {"error": "..."}}`` and never sets the ``error_message`` column - but every
    user-facing surface reads the column, so the reason ended up visible only on
    the raw-log page and the drawer showed a bare "Integration failed". Fall
    back to ``response_payload.error`` so any n8n failure carries its reason.
    """
    if log is None:
        return None
    if log.error_message:
        return log.error_message
    payload = _parse_response_payload(log.response_payload)
    fallback = payload.get("error")
    return fallback if isinstance(fallback, str) and fallback.strip() else None


def _build_file(
    db: Session, attachment: Attachment, log: Optional[IntegrationLog]
) -> UploadActivityFile:
    linked = _linked_entities_from_db(db, str(attachment.id), log=log)
    file_status = _file_status_from_log(log, linked)
    return UploadActivityFile(
        client_id=str(attachment.id),
        attachment_id=str(attachment.id),
        filename=attachment.original_filename,
        status=file_status,  # type: ignore[arg-type]
        summary=_summary(log, file_status),
        linked=linked,
        unlinked_reasons=_unlinked_reasons(log),
        error_code=log.error_code if log else None,
        error_message=_error_message(log),
        integration_log_id=str(log.id) if log else None,
        last_updated_at=(log.updated_at if log else attachment.created_at) or attachment.created_at,
    )


def _aggregate(files: List[UploadActivityFile]) -> SessionAggregate:
    return SessionAggregate(
        total=len(files),
        uploading=sum(1 for f in files if f.status == "uploading"),
        processing=sum(1 for f in files if f.status == "processing"),
        linked=sum(1 for f in files if f.status == "linked"),
        unlinked=sum(1 for f in files if f.status == "unlinked"),
        failed=sum(1 for f in files if f.status == "failed"),
    )


def _session_status(agg: SessionAggregate) -> str:
    if agg.uploading > 0:
        return "uploading"
    if agg.processing > 0:
        return "processing"
    if agg.total and agg.failed == agg.total:
        return "failed"
    if agg.unlinked > 0 or agg.failed > 0:
        return "partial"
    return "linked"


def _needs_action(files: List[UploadActivityFile]) -> bool:
    return any(f.status in ("failed", "unlinked") for f in files)


# ---- route ----------------------------------------------------------------


@router.get("", response_model=UploadActivityResponse, summary="Upload-activity drawer feed")
def get_upload_activity(
    scope: str = Query("me", pattern="^me$", description="Only 'me' is supported"),
    since: Optional[datetime] = Query(
        None,
        description="ISO-8601 cutoff. Default = now - 7 days.",
    ),
    limit: int = Query(50, ge=1, le=200),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UploadActivityResponse:
    """
    Aggregator for the Upload Activity drawer (top-nav icon).

    See docs/plans/PLAN-upload-activity-drawer.md §3 for the decision log.
    """
    user_id = str(current_user["id"])
    cutoff = since or (datetime.utcnow() - timedelta(days=7))

    # Types n8n does not intake never get a webhook reply, so their rows would
    # sit on "Processing" forever - the drawer cannot tell "still working" from
    # "never coming". Skip them.
    #
    # This was previously a hardcoded `type_name IN ('Stock List','Stock_List')`.
    # Same intent, but the property belongs to the TYPE: an admin can now retire
    # a type from n8n without a code change, and the same flag stops the pointless
    # webhook at source (migration 318).
    excluded_type_ids = [
        str(t.id)
        for t in db.query(AttachmentType.id)
        .filter(AttachmentType.triggers_n8n_webhook.is_(False))
        .all()
    ]

    # ---- pull user's recent attachments ------------------------------------
    # Untyped attachments are never sent to n8n (owner rule, 2 Oct 2026), so they
    # would sit on "Processing" for the same reason: leave them out too.
    attachments_filter = [
        Attachment.uploaded_by == user_id,
        Attachment.created_at >= cutoff,
        Attachment.is_deleted.is_(False),
        Attachment.attachment_type_id.isnot(None),
    ]
    if excluded_type_ids:
        attachments_filter.append(~Attachment.attachment_type_id.in_(excluded_type_ids))
    attachments: List[Attachment] = (
        db.query(Attachment)
        .filter(and_(*attachments_filter))
        .order_by(desc(Attachment.created_at))
        .limit(limit * 50)  # over-fetch to allow grouping into <= `limit` sessions
        .all()
    )
    # NOTE: no early-return on empty attachments - import_job sessions below
    # must still surface for users whose only recent activity is data imports.
    attachment_ids = [str(a.id) for a in attachments]

    # ---- pull latest integration_log per attachment ------------------------
    logs: List[IntegrationLog] = (
        db.query(IntegrationLog)
        .filter(
            and_(
                IntegrationLog.business_table == "attachments",
                IntegrationLog.business_id.in_(attachment_ids),
            )
        )
        .order_by(IntegrationLog.business_id, desc(IntegrationLog.created_at))
        .all()
        if attachment_ids
        else []
    )
    # Keep latest per business_id only.
    latest_log_by_attachment: Dict[str, IntegrationLog] = {}
    for log in logs:
        bid = str(log.business_id)
        if bid not in latest_log_by_attachment:
            latest_log_by_attachment[bid] = log

    # ---- pull import_jobs touched by the user in the window ---------------
    # attachment_bulk_import jobs merge with their attachment batch (bulk_zip
    # session); every other job type (stock/GRN/DO/product/... Excel imports)
    # becomes a standalone "import_job" session - the drawer replaces the
    # per-page LatestImportStatusPanel bar.
    import_jobs: List[ImportJob] = (
        db.query(ImportJob)
        .filter(
            and_(
                ImportJob.user_id == user_id,
                ImportJob.created_at >= cutoff,
            )
        )
        .order_by(desc(ImportJob.created_at))
        # Over-fetch ×2: bulk-import jobs are looked up by batch_id (not turned
        # into sessions 1:1), so a plain `limit` could starve bulk_zip merges.
        .limit(limit * 2)
        .all()
    )
    import_jobs_by_job_id: Dict[str, ImportJob] = {
        str(j.job_id): j for j in import_jobs if j.job_type == "attachment_bulk_import"
    }
    standalone_jobs: List[ImportJob] = [
        j for j in import_jobs if j.job_type != "attachment_bulk_import"
    ]

    # ---- group attachments by upload_batch_id ------------------------------
    by_batch: Dict[str, List[Attachment]] = {}
    singles: List[Attachment] = []
    for a in attachments:
        batch = (a.upload_batch_id or "").strip()
        if not batch:
            singles.append(a)
            continue
        by_batch.setdefault(batch, []).append(a)

    sessions: List[UploadActivitySession] = []

    # multi / bulk_zip sessions
    for batch_id, members in by_batch.items():
        members_sorted = sorted(members, key=lambda x: x.created_at)
        files = [
            _build_file(db, m, latest_log_by_attachment.get(str(m.id))) for m in members_sorted
        ]
        agg = _aggregate(files)
        import_job = import_jobs_by_job_id.get(batch_id)
        is_bulk = import_job is not None
        title = (
            (import_job.filename or "Bulk ZIP")
            if is_bulk
            else f"{len(files)} files"
        )
        started_at = members_sorted[0].created_at
        # `finished_at` only when no in-flight rows AND all logs settled.
        any_in_flight = agg.uploading + agg.processing > 0
        finished_at: Optional[datetime] = None
        if not any_in_flight:
            last_log_updated = [
                latest_log_by_attachment[str(m.id)].updated_at
                for m in members_sorted
                if str(m.id) in latest_log_by_attachment
            ]
            finished_at = max(last_log_updated) if last_log_updated else members_sorted[-1].created_at

        sessions.append(
            UploadActivitySession(
                session_id=batch_id,
                session_type="bulk_zip" if is_bulk else "multi",
                title=title,
                started_at=started_at,
                finished_at=finished_at,
                status=_session_status(agg),  # type: ignore[arg-type]
                aggregate=agg,
                files=files,
                import_job_id=batch_id if is_bulk else None,
                needs_action=_needs_action(files),
            )
        )

    # single sessions (one per attachment)
    for a in singles:
        log = latest_log_by_attachment.get(str(a.id))
        f = _build_file(db, a, log)
        agg = _aggregate([f])
        sessions.append(
            UploadActivitySession(
                session_id=str(a.id),  # no batch - key on attachment_id
                session_type="single",
                title=a.original_filename,
                started_at=a.created_at,
                finished_at=(log.updated_at if log and f.status in ("linked", "unlinked", "failed") else None),
                status=_session_status(agg),  # type: ignore[arg-type]
                aggregate=agg,
                files=[f],
                import_job_id=None,
                needs_action=_needs_action([f]),
            )
        )

    # import_job sessions (Excel/data imports - no attachment rows)
    for j in standalone_jobs:
        sessions.append(_import_job_session(j))

    sessions.sort(key=lambda s: s.started_at, reverse=True)
    sessions = sessions[:limit]

    return UploadActivityResponse(sessions=sessions)


# Human labels for job_type when the job has no filename (JSON-body imports
# like stock/products/warehouses don't always carry one).
_IMPORT_JOB_TYPE_LABELS: Dict[str, str] = {
    "stock_import": "Stock import",
    "product_import": "Product import",
    "warehouse_import": "Warehouse import",
    "order_tracking_import": "Order tracking import",
    "delivery_order_detail_import": "Delivery order lines import",
    "grn_listing_import": "GRN listing import",
    "grn_lines_import": "GRN lines import",
    "spo_import": "SPO allocations import",
    # Title case to match the two frontend job-type maps (import-jobs list + detail),
    # which is the name the operator reads; this map is only the no-filename fallback.
    "customer_import": "Customer Import",
    "outstanding_so_import": "Outstanding Sales Orders Import",
    "outstanding_po_import": "Outstanding Purchase Orders Import",
    "po_history_import": "Purchase History Import",
    "sales_history_import": "Sales History Import",
    "order_inquiry_import": "Order Inquiry Import",
}


def _import_job_session(j: ImportJob) -> UploadActivitySession:
    """Map one non-attachment ImportJob row to a drawer session."""
    job_status = (j.status or "").lower()
    if job_status == "finished":
        status = "partial" if (j.failed_rows or 0) > 0 else "linked"
    elif job_status == "failed":
        status = "failed"
    else:  # pending / queued / started
        status = "processing"

    label = _IMPORT_JOB_TYPE_LABELS.get(j.job_type, j.job_type.replace("_", " ").capitalize())
    title = j.filename or label

    return UploadActivitySession(
        session_id=str(j.job_id),
        session_type="import_job",
        title=title,
        started_at=j.created_at,
        finished_at=j.completed_at,
        status=status,  # type: ignore[arg-type]
        aggregate=SessionAggregate(total=0),
        files=[],
        import_job_id=str(j.job_id),
        needs_action=status == "failed",
        job_type=j.job_type,
        total_rows=j.total_rows,
        processed_rows=j.processed_rows,
        successful_rows=j.successful_rows,
        failed_rows=j.failed_rows,
        skipped_rows=j.skipped_rows,
        job_error=getattr(j, "error", None),
    )
