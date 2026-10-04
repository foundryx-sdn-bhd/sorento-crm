"""Shared helper for creating and sending attachment webhooks (used by upload API and bulk-import task)."""
import json
import logging
import threading
from typing import Optional
from urllib.parse import urlparse, unquote
from sqlalchemy.orm import Session

from app.services.integration_service import IntegrationLogService
from app.schemas.integration import IntegrationLogCreate
from app.services.n8n_webhook_settings import get_n8n_attachment_webhook_url

logger = logging.getLogger(__name__)


def _build_signed_attachment_url(
    file_path: Optional[str],
    provider: Optional[str] = None,
) -> Optional[str]:
    """Build a short-lived signed URL for webhook consumers, dispatched per provider."""
    if not file_path:
        return file_path
    try:
        from app.services.storage_router import resolve_signed_url

        return resolve_signed_url(file_path, provider=provider)
    except Exception as e:
        logger.warning("Could not generate signed attachment URL for webhook: %s", e)
        return file_path


def build_signed_attachment_url_for_webhook(
    file_path: Optional[str],
    provider: Optional[str] = None,
) -> Optional[str]:
    """Public alias: always generate a fresh signed URL from stored base URL or storage key."""
    return _build_signed_attachment_url(file_path, provider=provider)


def create_and_send_webhook(
    db: Session,
    attachment,
    attachment_type,
    access_levels_payload: Optional[list],
    current_user_id: str,
    event_type: str = "attachment_uploaded",
) -> None:
    """Create integration log for attachment and send webhook in background (same behaviour as single upload).

    `event_type` lets the dispatcher distinguish first-upload vs Google-Drive
    style replace-in-place so n8n intake can update the linked row instead of
    duplicate-rejecting (TCK-2026-000020).
    """
    # A type n8n does not intake must not be sent: it would never be answered,
    # and the unanswered log is what leaves the upload-activity drawer showing
    # "Processing" forever. Admin-controlled per type (migration 318). An
    # untyped attachment (e.g. the loading-plan stock list) is never sent either:
    # n8n has nothing to file it under (owner rule, 2 Oct 2026).
    if attachment_type is None:
        logger.debug(
            "Skipping n8n webhook for attachment %s: no attachment type",
            getattr(attachment, "id", None),
        )
        return
    if not getattr(attachment_type, "triggers_n8n_webhook", True):
        logger.debug(
            "Skipping n8n webhook for attachment %s: type %s does not trigger it",
            getattr(attachment, "id", None),
            getattr(attachment_type, "type_name", None),
        )
        return

    n8n_webhook_url = get_n8n_attachment_webhook_url(db)
    if not n8n_webhook_url:
        return
    integration_service = IntegrationLogService(db)
    integration_log_data = IntegrationLogCreate(
        integration_channel="n8n",
        business_table="attachments",
        business_id=attachment.id,
        direction="outbound",
        endpoint=n8n_webhook_url,
        http_method="POST",
        created_by=current_user_id,
        status="pending",
    )
    integration_log = integration_service.create_integration_log(integration_log_data)
    signed_attachment_url = _build_signed_attachment_url(
        getattr(attachment, "file_path", None),
        provider=getattr(attachment, "storage_provider", None),
    )
    webhook_payload = {
        "integration_log_id": integration_log.id,
        "event_type": event_type,
        "attachment_url": signed_attachment_url,
        "s3_url": signed_attachment_url,
        "file_path": attachment.file_path,
        "attachment_id": attachment.id,
        # User-facing name (stored_filename) so the downstream n8n record's filename column
        # tallies with what the user sees. At upload stored==original.
        "attachment_filename": attachment.stored_filename or attachment.original_filename,
        "attachment_mime_type": attachment.mime_type,
        "file_size": getattr(attachment, "file_size_bytes", None),
        "attachment_type": attachment_type.type_name if attachment_type else None,
        "access_levels": access_levels_payload,
        # Per-submit UUID; lets n8n optionally echo it back for correlation, and
        # lets the BE notification layer collapse the per-attachment callbacks
        # from one Create-Attachment submit into a single coalesced email.
        "upload_batch_id": getattr(attachment, "upload_batch_id", None),
    }
    integration_log.request_payload = json.dumps(webhook_payload)
    db.commit()
    db.refresh(integration_log)

    def send_webhook_async():
        try:
            from app.database import SessionLocal
            bg_db = SessionLocal()
            try:
                bg_service = IntegrationLogService(bg_db)
                bg_service.send_webhook_for_log(integration_log.id)
            finally:
                bg_db.close()
        except Exception as e:
            logger.error("Background webhook send failed for log %s: %s", integration_log.id, e, exc_info=True)

    threading.Thread(target=send_webhook_async, daemon=True).start()
    logger.info("Created integration log %s for attachment %s", integration_log.id, attachment.id)
