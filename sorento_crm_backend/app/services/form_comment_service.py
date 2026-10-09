"""Comment thread on a submitted form (complaint, stock inquiry, purchase request, sponsorship form).

Plan: documentation/plans/forms/PLAN-form-comments-09oct.md ("Contract" + "Owner answers").

A comment is an ``entity_conversation_messages`` row with scope ``form_comment`` keyed by
(kind, form id). Staff comments WhatsApp the submitter; a submitter comment emails the form's
SLA handler only. The row is always committed BEFORE any notification, so a notification
failure never loses the comment. Status never changes on a comment.

Each attached file gets two links: one to the form (so it shows in the form's Attachments and
the portal download check covers it) and one to ('form_comment', comment id).
"""
from __future__ import annotations

import logging
import os
import uuid
from typing import Any, Optional, Sequence

from sqlalchemy.orm import Session

from app.config import settings
from app.models.access import RespondContact
from app.models.complaints import Complaint
from app.models.entity_attachment import EntityAttachmentLink
from app.models.entity_conversation import EntityConversationMessage
from app.models.procurement import PurchaseRequestHeader, StockInquiry
from app.models.resources import Attachment
from app.models.user import User
from app.services.error_handler import AppException, handle_not_found, handle_unprocessable

logger = logging.getLogger(__name__)

FORM_COMMENT_SCOPE = "form_comment"
FORM_COMMENT_KINDS = ("complaint", "stock_inquiry", "purchase_request", "sponsorship_form")
MAX_BODY_CHARS = 4000
MAX_FILES = 5

# (filename, bytes, content_type)
CommentFile = tuple[Optional[str], bytes, Optional[str]]

_LABELS = {
    "complaint": "complaint",
    "stock_inquiry": "stock inquiry",
    "purchase_request": "purchase request",
    "sponsorship_form": "sponsorship form",
}
_NUMBER_ATTR = {
    "complaint": "complaint_number",
    "stock_inquiry": "inquiry_number",
    "purchase_request": "request_number",
    "sponsorship_form": "request_number",
}


def form_entity_type(kind: str) -> str:
    """Attachment entity_type of the form side (sponsorship shares purchase_request)."""
    return "purchase_request" if kind == "sponsorship_form" else kind


def _normalize_mime(content_type: Optional[str]) -> Optional[str]:
    base = (content_type or "").split(";", 1)[0].strip()
    return base[:100] or None


def _ext(filename: Optional[str], content_type: Optional[str]) -> str:
    if filename and "." in filename:
        return filename.rsplit(".", 1)[-1].lower().strip()
    if content_type and "/" in content_type:
        return content_type.split("/", 1)[1].split(";", 1)[0].strip().lower()
    return ""


def _contact_display_name(contact: Optional[RespondContact]) -> str:
    if contact is None:
        return "Unknown"
    return (
        (contact.name or "").strip()
        or " ".join(p for p in [(contact.first_name or "").strip(), (contact.last_name or "").strip()] if p)
        or (contact.phone_number or "").strip()
        or "Unknown"
    )


class FormCommentService:
    def __init__(self, db: Session):
        self.db = db

    # ------------------------------------------------------------------ #
    # Row lookup and guards
    # ------------------------------------------------------------------ #

    def _check_kind(self, kind: str) -> None:
        if kind not in FORM_COMMENT_KINDS:
            raise handle_unprocessable(f"Unsupported form type: {kind!r}.")

    def _load_row(self, kind: str, entity_id: str, *, contact_id: Optional[str] = None, space_id: Optional[str] = None):
        """The form row, or 404. With ``contact_id`` the row must belong to that contact + space."""
        self._check_kind(kind)
        if kind == "complaint":
            model = Complaint
            q = self.db.query(Complaint)
        elif kind == "stock_inquiry":
            model = StockInquiry
            q = self.db.query(StockInquiry)
        else:
            model = PurchaseRequestHeader
            q = self.db.query(PurchaseRequestHeader).filter(PurchaseRequestHeader.request_type == kind)
        q = q.filter(model.id == entity_id)
        if contact_id is not None:
            q = q.filter(model.contact_id == contact_id, model.space_id == space_id)
        row = q.first()
        if row is None:
            raise handle_not_found(_LABELS[kind].capitalize(), entity_id)
        return row

    @staticmethod
    def _require_open(row) -> None:
        status = (getattr(row, "status", None) or "").strip().lower()
        if getattr(row, "portal_draft_at", None) is not None or status == "draft":
            raise handle_unprocessable("Comments open once the form is submitted.")
        if status == "voided":
            raise handle_unprocessable("This form is voided; it no longer takes comments.")

    @staticmethod
    def _clean(body: Optional[str], files: Sequence[CommentFile]) -> str:
        text = (body or "").strip()
        if len(text) > MAX_BODY_CHARS:
            raise handle_unprocessable(f"Comment is too long (max {MAX_BODY_CHARS} characters).")
        if len(files) > MAX_FILES:
            raise handle_unprocessable(f"At most {MAX_FILES} files per comment.")
        if not text and not files:
            raise handle_unprocessable("Write a comment or attach a file.")
        return text

    # ------------------------------------------------------------------ #
    # Listing / serialization
    # ------------------------------------------------------------------ #

    def list_comments(self, kind: str, entity_id: str, *, _checked: bool = False) -> list[dict[str, Any]]:
        """``_checked``: the caller already loaded the row with its own ownership filter."""
        if not _checked:
            self._load_row(kind, entity_id)
        rows = (
            self.db.query(EntityConversationMessage)
            .filter(
                EntityConversationMessage.entity_type == kind,
                EntityConversationMessage.entity_id == entity_id,
                EntityConversationMessage.scope == FORM_COMMENT_SCOPE,
            )
            .order_by(EntityConversationMessage.created_at.asc(), EntityConversationMessage.id.asc())
            .all()
        )
        return self._serialize_many(rows)

    def _serialize_many(self, rows: Sequence[EntityConversationMessage]) -> list[dict[str, Any]]:
        if not rows:
            return []
        user_ids = {r.author_user_id for r in rows if r.author_user_id}
        contact_ids = {r.author_contact_id for r in rows if r.author_contact_id}
        users = {u.id: u for u in self.db.query(User).filter(User.id.in_(user_ids)).all()} if user_ids else {}
        contacts = (
            {c.id: c for c in self.db.query(RespondContact).filter(RespondContact.id.in_(contact_ids)).all()}
            if contact_ids
            else {}
        )
        files: dict[str, list[dict[str, Any]]] = {}
        pairs = (
            self.db.query(EntityAttachmentLink, Attachment)
            .join(Attachment, Attachment.id == EntityAttachmentLink.attachment_id)
            .filter(
                EntityAttachmentLink.entity_type == FORM_COMMENT_SCOPE,
                EntityAttachmentLink.entity_id.in_([r.id for r in rows]),
            )
            .order_by(EntityAttachmentLink.sort_order.asc())
            .all()
        )
        for link, att in pairs:
            files.setdefault(str(link.entity_id), []).append(
                {
                    "attachment_id": str(att.id),
                    "filename": att.original_filename,
                    "size": att.file_size_bytes,
                    "content_type": att.mime_type,
                }
            )
        out: list[dict[str, Any]] = []
        for r in rows:
            if r.author_user_id:
                u = users.get(r.author_user_id)
                name = (u.name or "").strip() if u else ""
                kind_label = "staff"
            else:
                name = _contact_display_name(contacts.get(r.author_contact_id))
                kind_label = "submitter"
            out.append(
                {
                    "id": r.id,
                    "body": r.body_html or "",
                    "author_name": name or ("Sorento team" if r.author_user_id else "Unknown"),
                    "author_kind": kind_label,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                    "attachments": files.get(r.id, []),
                }
            )
        return out

    # ------------------------------------------------------------------ #
    # Writes
    # ------------------------------------------------------------------ #

    def _insert(self, kind, entity_id, text, *, user_id=None, contact_id=None) -> EntityConversationMessage:
        msg = EntityConversationMessage(
            id=str(uuid.uuid4()),
            entity_type=kind,
            entity_id=entity_id,
            scope=FORM_COMMENT_SCOPE,
            body_html=text,
            author_user_id=user_id,
            author_contact_id=contact_id,
        )
        self.db.add(msg)
        self.db.flush()
        return msg

    def _link_to_comment(self, comment_id: str, attachment_id: str) -> None:
        from app.services.entity_attachment_service import EntityAttachmentService

        EntityAttachmentService(self.db).link_existing_attachment(FORM_COMMENT_SCOPE, comment_id, attachment_id)

    def _store_staff_files(self, kind: str, entity_id: str, comment_id: str, user_id: str, files: Sequence[CommentFile]) -> None:
        from app.services.entity_attachment_service import create_response_attachment

        for filename, contents, content_type in files:
            saved = create_response_attachment(
                self.db,
                entity_type=form_entity_type(kind),
                entity_id=entity_id,
                contents=contents,
                filename=filename,
                content_type=content_type,
                uploaded_by=user_id,
                commit=False,
            )
            self._link_to_comment(comment_id, saved["attachment_id"])

    def _store_contact_files(self, kind: str, entity_id: str, comment_id: str, contact_id: str, files: Sequence[CommentFile]) -> None:
        """Portal upload path (portal_submission type, portal key prefix, uploader_kind contact)."""
        if not files:
            return
        from app.services.entity_attachment_service import EntityAttachmentService
        from app.services.image_thumbnailer import store_thumbnail
        from app.services.portal_service import PORTAL_ATTACHMENT_TYPE_CODE, PortalService
        from app.services.storage_router import default_provider, get_backend

        service = EntityAttachmentService(self.db)
        attachment_type = PortalService(self.db).get_portal_attachment_type()
        entity_type = form_entity_type(kind)
        for filename, contents, content_type in files:
            size = len(contents)
            ext = _ext(filename, content_type)
            service.check_quota(attachment_type, entity_type, entity_id, size, ext)
            key = f"portal/{contact_id}/{uuid.uuid4()}{'.' + ext if ext else ''}"
            provider = default_provider()
            backend = get_backend(provider)
            try:
                backend.upload_file(contents, key, content_type=content_type)
            except Exception as e:  # noqa: BLE001
                logger.warning("Form comment upload failed (provider=%s): %s", provider, e)
                raise AppException(status_code=502, message="File upload failed. Please try again.", code="UPLOAD_FAILED") from e
            thumb = store_thumbnail(backend, provider, key, contents, content_type)
            link = service.create_attachment_and_link(
                entity_type=entity_type,
                entity_id=entity_id,
                file_url=key,
                file_name=filename or os.path.basename(key),
                file_size_bytes=size,
                attachment_type_code=PORTAL_ATTACHMENT_TYPE_CODE,
                created_by=None,
                thumbnail_path=thumb,
                storage_provider=provider,
                mime_type=_normalize_mime(content_type),
                uploader_kind="contact",
                uploaded_by_contact_id=contact_id,
            )
            self._link_to_comment(comment_id, str(link.attachment_id))

    def _commit_comment(self, msg: EntityConversationMessage) -> dict[str, Any]:
        self.db.commit()
        self.db.refresh(msg)
        return self._serialize_many([msg])[0]

    def add_staff_comment(
        self,
        kind: str,
        entity_id: str,
        user: Any,
        body: Optional[str],
        files: Optional[Sequence[CommentFile]] = None,
    ) -> dict[str, Any]:
        """Returns ``{comment, delivery}``; delivery is queued | no_whatsapp | failed."""
        files = list(files or [])
        row = self._load_row(kind, entity_id)
        self._require_open(row)
        text = self._clean(body, files)
        user_id = str(user.id if hasattr(user, "id") else user["id"])
        try:
            msg = self._insert(kind, entity_id, text, user_id=user_id)
            self._store_staff_files(kind, entity_id, msg.id, user_id, files)
            comment = self._commit_comment(msg)
        except Exception:
            self.db.rollback()
            raise
        delivery = self._notify_submitter(kind, row, user, text, comment["attachments"])
        return {"comment": comment, "delivery": delivery}

    def add_contact_comment(
        self,
        kind: str,
        entity_id: str,
        contact_id: str,
        space_id: Optional[str],
        body: Optional[str],
        files: Optional[Sequence[CommentFile]] = None,
    ) -> dict[str, Any]:
        """Returns ``{comment, delivery}``; delivery is queued | no_recipients | failed."""
        files = list(files or [])
        row = self._load_row(kind, entity_id, contact_id=contact_id, space_id=space_id)
        self._require_open(row)
        text = self._clean(body, files)
        try:
            msg = self._insert(kind, entity_id, text, contact_id=contact_id)
            self._store_contact_files(kind, entity_id, msg.id, contact_id, files)
            comment = self._commit_comment(msg)
        except Exception:
            self.db.rollback()
            raise
        delivery = self._notify_handler(kind, row, contact_id, text, comment["attachments"])
        return {"comment": comment, "delivery": delivery}

    # ------------------------------------------------------------------ #
    # Notifications (after the commit; never raise)
    # ------------------------------------------------------------------ #

    def _number(self, kind: str, row) -> str:
        from app.services.form_sla_service import _resolve_entity_number

        return _resolve_entity_number(self.db, kind, str(row.id)) or getattr(row, _NUMBER_ATTR[kind], None) or ""

    @staticmethod
    def _identifier(db: Session, inbox_url: Optional[str]) -> Optional[str]:
        if not inbox_url or not inbox_url.strip():
            return None
        parts = [p for p in inbox_url.rstrip("/").split("/") if p]
        if not parts:
            return None
        from app.services.respond_identifier import resolve_send_identifier

        return resolve_send_identifier(db, parts[-1])

    def _notify_submitter(self, kind: str, row, user: Any, body: str, attachments: list[dict]) -> str:
        try:
            identifier = self._identifier(self.db, getattr(row, "respond_inbox_url", None))
            if not identifier:
                return "no_whatsapp"
            from app.services.portal_service import PortalService

            number = self._number(kind, row)
            portal_url = (PortalService(self.db).submission_link(getattr(row, "contact_id", None), kind, str(row.id)) or "").strip()
            names = ", ".join(a["filename"] for a in attachments if a.get("filename"))
            files_line = f"Attachments: {names}" if names else ""
            message = f"There is a new comment on your {_LABELS[kind]} {number}:\n{body}"
            if files_line:
                message += f"\n{files_line}"
            if portal_url:
                message += f"\n\n{portal_url}"
            update = "\n".join(p for p in (body, files_line) if p)
            sender_id = str(user.id if hasattr(user, "id") else user["id"])
            responder = (getattr(user, "respond_user_id", None) or "").strip() or identifier

            from app.services import queue_service
            from app.tasks.respond_io_tasks import send_form_comment_message

            queue_service.enqueue_job(
                send_form_comment_message,
                kind,
                str(row.id),
                identifier,
                message,
                responder,
                sender_id,
                getattr(row, "space_id", None),
                {"update": update, "portal_url": portal_url},
                queue_name="respond_io",
                job_timeout=180,
            )
            return "queued"
        except Exception:  # noqa: BLE001
            logger.exception("Form comment WhatsApp enqueue failed for %s %s", kind, getattr(row, "id", None))
            return "failed"

    def _handler_for(self, kind: str, entity_id: str) -> Optional[User]:
        """The handler of the form's open SLA stage; else of the newest stage for the form
        (a Responded inquiry has its stage resolved, and that handler is still the one to tell)."""
        from app.models.sla import ConversationSLATracking
        from app.services.handling_lock_service import _active_form_tracker

        tracking = _active_form_tracker(self.db, entity_id, kind)
        if tracking is None or not tracking.handled_by_id:
            tracking = (
                self.db.query(ConversationSLATracking)
                .filter(
                    ConversationSLATracking.source_entity_type == kind,
                    ConversationSLATracking.source_entity_id == entity_id,
                )
                .order_by(ConversationSLATracking.initiated_at.desc())
                .first()
            )
        if tracking is None or not tracking.handled_by_id:
            return None
        handler = self.db.query(User).filter(User.id == tracking.handled_by_id).first()
        if handler is None or not (handler.email or "").strip():
            return None
        return handler

    def _internal_url(self, kind: str, entity_id: str) -> str:
        from app.models.user import SystemSetting
        from app.services.form_sla_service import _form_detail_link

        base = (settings.frontend_base_url or "").strip().rstrip("/")
        if not base:
            sys_settings = self.db.query(SystemSetting).first()
            base = (getattr(sys_settings, "website_url", None) or "").strip().rstrip("/")
        return f"{base}{_form_detail_link(kind, entity_id)}"

    def _notify_handler(self, kind: str, row, contact_id: str, body: str, attachments: list[dict]) -> str:
        try:
            handler = self._handler_for(kind, str(row.id))
            if handler is None:
                logger.info("Form comment on %s %s: no SLA handler with an email; no mail sent.", kind, row.id)
                return "no_recipients"
            from datetime import datetime

            from app.models.notification import Notification, NotificationDelivery
            from app.services.email_template_service import EmailTemplateService

            contact = self.db.query(RespondContact).filter(RespondContact.id == contact_id).first()
            label = _LABELS[kind].title()
            number = self._number(kind, row)
            heading = f"New comment on {label} {number}".strip()
            intro = f'{_contact_display_name(contact)} commented on {label} {number}:\n"{body}"'
            names = ", ".join(a["filename"] for a in attachments if a.get("filename"))
            if names:
                intro += f"\nAttachments: {names}"
            rendered = EmailTemplateService(self.db).render_code(
                # The generic system template: a dedicated one would have to be frozen into the
                # eml_0002 seed migration (test_frozen_documents_match_registry).
                "notification_generic",
                {
                    "title": heading,
                    "body": intro,
                    "link": self._internal_url(kind, str(row.id)),
                    "link_label": f"Open {_LABELS[kind]}",
                },
            )
            notification = Notification(
                user_id=handler.id,
                type="form_comment_notification",
                title=rendered["subject"],
                body=rendered["body_text"],
                data={
                    "recipient_emails": [handler.email.strip()],
                    "single_email_to_all": True,
                    "body_html": rendered["body_html"],
                },
                source_entity_type=kind,
                source_entity_id=str(row.id),
                event_type="form_comment",
            )
            self.db.add(notification)
            self.db.flush()
            self.db.add(NotificationDelivery(notification_id=notification.id, channel="in_app", status="sent", sent_at=datetime.utcnow()))
            self.db.add(NotificationDelivery(notification_id=notification.id, channel="email", status="pending"))
            self.db.commit()
            self.db.refresh(notification)

            from app.services import queue_service
            from app.tasks import notification_tasks

            queue_service.enqueue_job(
                notification_tasks.send_notification_deliveries,
                str(notification.id),
                queue_name="notifications",
            )
            return "queued"
        except Exception:  # noqa: BLE001
            self.db.rollback()
            logger.exception("Form comment email failed for %s %s", kind, getattr(row, "id", None))
            return "failed"
