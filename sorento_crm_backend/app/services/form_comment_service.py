"""Comment thread on a submitted form (complaint, stock inquiry, purchase request, sponsorship form).

Plan: documentation/plans/forms/PLAN-form-comments-09oct.md ("Contract" + "Owner answers").

A comment is an ``entity_conversation_messages`` row with scope ``form_comment`` keyed by
(kind, form id). Staff comments WhatsApp the submitter; a submitter comment emails the form's
SLA handler only. The row is always committed BEFORE any notification, so a notification
failure never loses the comment. Status never changes on a comment.

Each attached file gets ONE link, to ('form_comment', comment id). It is deliberately not linked
to the form: the form's response-attachment count, quota and the portal's "from purchasing" box
must not see comment files. The portal download check resolves the comment back to its form.
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
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
from app.services.user_service import UserPermissionService

logger = logging.getLogger(__name__)

FORM_COMMENT_SCOPE = "form_comment"
FORM_COMMENT_KINDS = (
    "complaint",
    "stock_inquiry",
    "purchase_request",
    "sponsorship_form",
    "sample_request",
)
MAX_BODY_CHARS = 4000
VIEW_PERMISSION = {
    "complaint": "complaint_management.complaints.view",
    "stock_inquiry": "procurement.stock_inquiries.view",
    "purchase_request": "procurement.purchase_requests.view",
    "sponsorship_form": "procurement.sponsorship_forms.view",
    "sample_request": "projects.sample_requests.view",
}
MAX_FILES = 5

# (filename, bytes, content_type)
CommentFile = tuple[Optional[str], bytes, Optional[str]]

_LABELS = {
    "complaint": "complaint",
    "stock_inquiry": "stock inquiry",
    "purchase_request": "purchase request",
    "sponsorship_form": "sponsorship form",
    "sample_request": "sample request",
}
_NUMBER_ATTR = {
    "complaint": "complaint_number",
    "stock_inquiry": "inquiry_number",
    "purchase_request": "request_number",
    "sponsorship_form": "request_number",
    "sample_request": "request_no",
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


def _iso_utc(value: Optional[datetime]) -> Optional[str]:
    """The columns hold naive UTC; stamp the offset so no reader takes it for local time."""
    if value is None:
        return None
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).isoformat()


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
        elif kind == "sample_request":
            from app.models.sample_request import SampleRequest

            model = SampleRequest
            q = self.db.query(SampleRequest)
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

    def list_comments(
        self, kind: str, entity_id: str, viewer: Optional[dict] = None, *, row=None
    ) -> list[dict[str, Any]]:
        """The thread, oldest first, led by the form's reply as a synthetic ``response`` entry.

        ``row``: the caller already loaded the form with its own ownership filter.
        ``viewer``: ``{"user_id": ..., "is_admin": bool}`` for staff, ``{"contact_id": ...}`` for
        the portal; None gives an entry with every ``can_*`` flag off.
        """
        if row is None:
            row = self._load_row(kind, entity_id)
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
        items = self._serialize_many(rows, viewer)
        first = self._response_entry(kind, row)
        return [first, *items] if first else items

    def staff_viewer(self, user: Any) -> dict:
        uid = str(user.id if hasattr(user, "id") else user["id"])
        roles = UserPermissionService(self.db).get_user_role_slugs(uid)
        return {"user_id": uid, "is_admin": bool(roles & {UserPermissionService.SUPERADMIN_ROLE_SLUG, "admin"})}

    @staticmethod
    def _staff_name(user: Optional[User]) -> str:
        return ((user.name or "").strip() if user else "") or "Sorento team"

    def _response_entry(self, kind: str, row) -> Optional[dict[str, Any]]:
        """The purchasing / technical reply as the first entry of the thread (None when blank)."""
        if kind == "stock_inquiry":
            text = row.purchasing_response
        elif kind == "complaint":
            text = row.technical_team_response
        else:
            return None
        text = (text or "").strip()
        if text.startswith("There has been an update regarding your") and ": " in text:
            text = text[text.rfind(": ") + 2 :].strip()
        if not text:
            return None
        responder = None
        if row.last_responded_by:
            responder = self.db.query(User).filter(User.respond_user_id == row.last_responded_by).first()
        from app.models.resources import AttachmentType

        files = (
            self.db.query(Attachment)
            .join(EntityAttachmentLink, EntityAttachmentLink.attachment_id == Attachment.id)
            .join(AttachmentType, AttachmentType.id == Attachment.attachment_type_id)
            .filter(
                EntityAttachmentLink.entity_type == kind,
                EntityAttachmentLink.entity_id == str(row.id),
                AttachmentType.code == "response_attachment",
                Attachment.uploader_kind == "user",
            )
            .order_by(EntityAttachmentLink.sort_order.asc())
            .all()
        )
        return {
            "id": "response",
            "kind": "response",
            "body": text,
            "author_name": self._staff_name(responder),
            "author_kind": "staff",
            "posted_by_name": None,
            "created_at": _iso_utc(row.last_responded_at),
            "attachments": [self._file_dict(a) for a in files],
            "parent_id": None,
            "edited_at": None,
            "deleted": False,
            "can_edit": False,
            "can_delete": False,
            "can_reply": False,
        }

    @staticmethod
    def _file_dict(att: Attachment) -> dict[str, Any]:
        return {
            "attachment_id": str(att.id),
            "filename": att.original_filename,
            "size": att.file_size_bytes,
            "content_type": att.mime_type,
        }

    @staticmethod
    def _can_modify(r: EntityConversationMessage, viewer: Optional[dict]) -> bool:
        """Author-or-admin. Staff author = a user with no contact; portal author = the contact."""
        if not viewer or r.deleted_at is not None:
            return False
        if "contact_id" in viewer:
            return r.author_contact_id is not None and r.author_contact_id == viewer["contact_id"]
        if viewer.get("is_admin"):
            return True
        return r.author_contact_id is None and r.author_user_id is not None and r.author_user_id == viewer.get("user_id")

    def _serialize_many(self, rows: Sequence[EntityConversationMessage], viewer: Optional[dict] = None) -> list[dict[str, Any]]:
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
            files.setdefault(str(link.entity_id), []).append(self._file_dict(att))
        out: list[dict[str, Any]] = []
        for r in rows:
            posted_by = None
            if r.author_contact_id:
                # Contact-authored; author_user_id is the admin acting on their behalf, if any.
                name = _contact_display_name(contacts.get(r.author_contact_id))
                kind_label = "submitter"
                if r.author_user_id:
                    posted_by = self._staff_name(users.get(r.author_user_id))
            elif r.author_user_id:
                name = self._staff_name(users.get(r.author_user_id))
                kind_label = "staff"
            else:
                name, kind_label = "Unknown", "submitter"
            deleted = r.deleted_at is not None
            can = self._can_modify(r, viewer)
            out.append(
                {
                    "id": r.id,
                    "kind": "comment",
                    "body": "" if deleted else (r.body_html or ""),
                    "author_name": name,
                    "author_kind": kind_label,
                    "posted_by_name": posted_by,
                    "created_at": _iso_utc(r.created_at),
                    "attachments": [] if deleted else files.get(r.id, []),
                    "parent_id": r.parent_id,
                    "edited_at": _iso_utc(r.edited_at),
                    "deleted": deleted,
                    "can_edit": can,
                    "can_delete": can,
                    "can_reply": bool(viewer) and r.parent_id is None and not deleted,
                }
            )
        return out

    # ------------------------------------------------------------------ #
    # Writes
    # ------------------------------------------------------------------ #

    def _insert(self, kind, entity_id, text, *, user_id=None, contact_id=None, parent_id=None) -> EntityConversationMessage:
        msg = EntityConversationMessage(
            id=str(uuid.uuid4()),
            entity_type=kind,
            entity_id=entity_id,
            scope=FORM_COMMENT_SCOPE,
            body_html=text,
            author_user_id=user_id,
            author_contact_id=contact_id,
            parent_id=parent_id,
        )
        self.db.add(msg)
        self.db.flush()
        return msg

    def _store_staff_files(self, kind: str, entity_id: str, comment_id: str, user_id: str, files: Sequence[CommentFile]) -> None:
        from app.services.entity_attachment_service import create_response_attachment

        for filename, contents, content_type in files:
            saved = create_response_attachment(
                self.db,
                entity_type=FORM_COMMENT_SCOPE,
                entity_id=comment_id,
                contents=contents,
                filename=filename,
                content_type=content_type,
                uploaded_by=user_id,
                commit=False,
            )

    def _store_contact_files(self, kind: str, entity_id: str, comment_id: str, contact_id: str, files: Sequence[CommentFile], acting_user_id: Optional[str] = None) -> None:
        """Portal upload path (portal_submission type, portal key prefix, uploader_kind contact)."""
        if not files:
            return
        from app.services.entity_attachment_service import EntityAttachmentService
        from app.services.image_thumbnailer import store_thumbnail
        from app.services.portal_service import PORTAL_ATTACHMENT_TYPE_CODE, PortalService
        from app.services.storage_router import default_provider, get_backend

        service = EntityAttachmentService(self.db)
        attachment_type = PortalService(self.db).get_portal_attachment_type()
        for filename, contents, content_type in files:
            size = len(contents)
            ext = _ext(filename, content_type)
            service.check_quota(attachment_type, FORM_COMMENT_SCOPE, comment_id, size, ext)
            key = f"portal/{contact_id}/{uuid.uuid4()}{'.' + ext if ext else ''}"
            provider = default_provider()
            backend = get_backend(provider)
            try:
                backend.upload_file(contents, key, content_type=content_type)
            except Exception as e:  # noqa: BLE001
                logger.warning("Form comment upload failed (provider=%s): %s", provider, e)
                raise AppException(status_code=502, message="File upload failed. Please try again.", code="UPLOAD_FAILED") from e
            thumb = store_thumbnail(backend, provider, key, contents, content_type)
            service.create_attachment_and_link(
                entity_type=FORM_COMMENT_SCOPE,
                entity_id=comment_id,
                file_url=key,
                file_name=filename or os.path.basename(key),
                file_size_bytes=size,
                attachment_type_code=PORTAL_ATTACHMENT_TYPE_CODE,
                created_by=None,
                thumbnail_path=thumb,
                storage_provider=provider,
                mime_type=_normalize_mime(content_type),
                uploader_kind="contact",
                uploaded_by=acting_user_id,
                uploaded_by_contact_id=contact_id,
            )

    def _commit_comment(self, msg: EntityConversationMessage, viewer: Optional[dict] = None) -> dict[str, Any]:
        self.db.commit()
        self.db.refresh(msg)
        return self._serialize_many([msg], viewer)[0]

    def _check_parent(self, kind: str, entity_id: str, parent_id: Optional[str]) -> Optional[str]:
        """A reply must hang off a live top-level comment of this same thread (one level only)."""
        if not parent_id:
            return None
        parent = (
            self.db.query(EntityConversationMessage)
            .filter(
                EntityConversationMessage.id == parent_id,
                EntityConversationMessage.entity_type == kind,
                EntityConversationMessage.entity_id == entity_id,
                EntityConversationMessage.scope == FORM_COMMENT_SCOPE,
                EntityConversationMessage.parent_id.is_(None),
                EntityConversationMessage.deleted_at.is_(None),
            )
            .first()
        )
        if parent is None:
            raise handle_unprocessable("You can only reply to a comment that is still there.")
        return parent.id

    def _load_comment(self, kind: str, entity_id: str, comment_id: str) -> EntityConversationMessage:
        msg = (
            self.db.query(EntityConversationMessage)
            .filter(
                EntityConversationMessage.id == comment_id,
                EntityConversationMessage.entity_type == kind,
                EntityConversationMessage.entity_id == entity_id,
                EntityConversationMessage.scope == FORM_COMMENT_SCOPE,
            )
            .first()
        )
        if msg is None:
            raise handle_not_found("Comment", comment_id)
        return msg

    # ------------------------------------------------------------------ #
    # Edit / delete (author or admin, enforced here)
    # ------------------------------------------------------------------ #

    def _require_modifier(self, msg: EntityConversationMessage, viewer: dict) -> None:
        if not self._can_modify(msg, {**viewer}) and msg.deleted_at is None:
            raise AppException(status_code=403, message="Only the author can change this comment.", code="FORBIDDEN")

    def edit_comment(self, kind: str, entity_id: str, comment_id: str, viewer: dict, body: Optional[str], *, row=None) -> dict[str, Any]:
        if row is None:
            row = self._load_row(kind, entity_id)
        self._require_open(row)
        msg = self._load_comment(kind, entity_id, comment_id)
        self._require_modifier(msg, viewer)
        if msg.deleted_at is not None:
            raise handle_unprocessable("This comment was deleted.")
        text = (body or "").strip()
        if not text:
            raise handle_unprocessable("A comment can't be empty.")
        if len(text) > MAX_BODY_CHARS:
            raise handle_unprocessable(f"Comment is too long (max {MAX_BODY_CHARS} characters).")
        msg.body_html = text
        msg.edited_at = datetime.utcnow()
        return self._commit_comment(msg, viewer)

    def delete_comment(self, kind: str, entity_id: str, comment_id: str, viewer: dict, *, row=None) -> None:
        """With live replies the row stays as a placeholder; otherwise it goes with its files."""
        if row is None:
            self._load_row(kind, entity_id)
        msg = self._load_comment(kind, entity_id, comment_id)
        self._require_modifier(msg, viewer)
        if msg.deleted_at is not None:
            return
        from app.services.entity_attachment_service import EntityAttachmentService

        EntityAttachmentService(self.db).delete_links_for_entity(FORM_COMMENT_SCOPE, msg.id)
        has_replies = (
            self.db.query(EntityConversationMessage.id).filter(EntityConversationMessage.parent_id == msg.id).first()
            is not None
        )
        if has_replies:
            msg.body_html = ""
            msg.deleted_at = datetime.utcnow()
        else:
            self.db.delete(msg)
        self.db.commit()

    def _gate_delete(self, comment_id: str, requested_by_id: Optional[str], *, hide: bool = False):
        """The comment, its form row and the requester's viewer, or a refusal.

        The form is loaded (a form that cannot be loaded refuses), the requester must hold the
        kind's view slug unless admin, and be the author or an admin. ``hide`` turns every
        refusal into a 404 (park time: a comment the requester cannot delete does not exist).
        """
        try:
            msg = (
                self.db.query(EntityConversationMessage)
                .filter(EntityConversationMessage.id == comment_id, EntityConversationMessage.scope == FORM_COMMENT_SCOPE)
                .first()
            )
            if msg is None:
                raise handle_not_found("Comment", comment_id)
            if not requested_by_id:
                raise AppException(status_code=403, message="Sign in to do that.", code="FORBIDDEN")
            row = self._load_row(msg.entity_type, msg.entity_id)
            viewer = self.staff_viewer({"id": requested_by_id})
            if not viewer["is_admin"] and VIEW_PERMISSION[msg.entity_type] not in set(
                UserPermissionService(self.db).get_user_permission_slugs(viewer["user_id"])
            ):
                raise AppException(status_code=403, message="You can't change this comment.", code="FORBIDDEN")
            if msg.deleted_at is None:
                self._require_modifier(msg, viewer)
            return msg, row, viewer
        except AppException:
            if hide:
                raise handle_not_found("Comment", comment_id)
            raise

    def assert_staff_may_delete(self, comment_id: str, requested_by_id: Optional[str]) -> None:
        self._gate_delete(comment_id, requested_by_id, hide=True)

    def delete_by_id(self, comment_id: str, requested_by_id: Optional[str]) -> None:
        """The pending-action executor: the comment row says which form it is on."""
        msg, row, viewer = self._gate_delete(comment_id, requested_by_id)
        self.delete_comment(msg.entity_type, msg.entity_id, msg.id, viewer, row=row)

    def add_staff_comment(
        self,
        kind: str,
        entity_id: str,
        user: Any,
        body: Optional[str],
        files: Optional[Sequence[CommentFile]] = None,
        parent_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Returns ``{comment, delivery}``; delivery is queued | no_whatsapp | failed."""
        files = list(files or [])
        row = self._load_row(kind, entity_id)
        self._require_open(row)
        text = self._clean(body, files)
        parent_id = self._check_parent(kind, entity_id, parent_id)
        user_id = str(user.id if hasattr(user, "id") else user["id"])
        try:
            msg = self._insert(kind, entity_id, text, user_id=user_id, parent_id=parent_id)
            self._store_staff_files(kind, entity_id, msg.id, user_id, files)
            comment = self._commit_comment(msg, self.staff_viewer(user))
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
        acting_user_id: Optional[str] = None,
        parent_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Returns ``{comment, delivery}``; delivery is queued | no_recipients | failed.

        ``acting_user_id``: an admin posting through a portal impersonation session. The comment
        stays the contact's (author_contact_id) and the admin is kept in author_user_id as the
        audit "acted by".
        """
        files = list(files or [])
        row = self._load_row(kind, entity_id, contact_id=contact_id, space_id=space_id)
        self._require_open(row)
        text = self._clean(body, files)
        parent_id = self._check_parent(kind, entity_id, parent_id)
        try:
            msg = self._insert(kind, entity_id, text, user_id=acting_user_id, contact_id=contact_id, parent_id=parent_id)
            self._store_contact_files(kind, entity_id, msg.id, contact_id, files, acting_user_id)
            comment = self._commit_comment(msg, {"contact_id": contact_id})
        except Exception:
            self.db.rollback()
            raise
        delivery = self._notify_handler(kind, row, contact_id, text, comment["attachments"], acting_user_id)
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

    def _recipients(self, kind: str, row, acting_user_id: Optional[str]) -> list[User]:
        """Who is emailed when the submitter comments: the SLA handler, the staff who commented in
        this thread and the responder of the staff reply; failing all of those, the users already
        notified about this form. Active users with an email only; never the acting admin."""
        from app.models.notification import Notification

        perms = UserPermissionService(self.db)
        admin_slugs = {UserPermissionService.SUPERADMIN_ROLE_SLUG, "admin"}

        def usable(u: Optional[User]) -> bool:
            return (
                u is not None
                and str(u.status) == "ACTIVE"
                and not u.is_trashed
                and bool((u.email or "").strip())
                and str(u.id) != str(acting_user_id or "")
                # Mail carries the comment text: only staff who can still open the form get it.
                and (
                    bool(perms.get_user_role_slugs(u.id) & admin_slugs)
                    or perms.check_user_has_permission(u.id, VIEW_PERMISSION[kind])
                )
            )

        found: dict[str, User] = {}

        def add(u: Optional[User]) -> None:
            if usable(u):
                found.setdefault(str(u.id), u)

        add(self._handler_for(kind, str(row.id)))
        author_ids = {
            a
            for (a,) in self.db.query(EntityConversationMessage.author_user_id).filter(
                EntityConversationMessage.entity_type == kind,
                EntityConversationMessage.entity_id == str(row.id),
                EntityConversationMessage.scope == FORM_COMMENT_SCOPE,
                EntityConversationMessage.author_user_id.isnot(None),
                EntityConversationMessage.author_contact_id.is_(None),
                EntityConversationMessage.deleted_at.is_(None),
            )
        }
        if author_ids:
            for u in self.db.query(User).filter(User.id.in_(author_ids)).all():
                add(u)
        responded_by = getattr(row, "last_responded_by", None)
        if responded_by and kind in ("stock_inquiry", "complaint"):
            # respond_user_id is the stored value's meaning; users.id only when nobody has it.
            add(
                self.db.query(User).filter(User.respond_user_id == responded_by).first()
                or self.db.query(User).filter(User.id == responded_by).first()
            )
        if not found:
            team_ids = {
                uid
                for (uid,) in self.db.query(Notification.user_id).filter(
                    # Sponsorship notifications are stored under purchase_request.
                    Notification.source_entity_type.in_(
                        ("sponsorship_form", "purchase_request") if kind == "sponsorship_form" else (kind,)
                    ),
                    Notification.source_entity_id == str(row.id),
                    Notification.type != "form_comment_notification",
                    Notification.user_id.isnot(None),
                )
            }
            if team_ids:
                for u in self.db.query(User).filter(User.id.in_(team_ids)).all():
                    add(u)
        return list(found.values())

    def _notify_handler(
        self, kind: str, row, contact_id: str, body: str, attachments: list[dict], acting_user_id: Optional[str] = None
    ) -> str:
        try:
            recipients = self._recipients(kind, row, acting_user_id)
            if not recipients:
                logger.warning(
                    "Form comment on %s %s: no recipient (no SLA handler, staff commenter, responder or earlier "
                    "notified user with an active account and an email); no mail sent.",
                    kind,
                    row.id,
                )
                return "no_recipients"
            handler = recipients[0]
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
            if acting_user_id:
                admin = self.db.query(User).filter(User.id == acting_user_id).first()
                intro += f"\nPosted by {self._staff_name(admin)} on behalf of {_contact_display_name(contact)}."
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
                    "recipient_emails": [u.email.strip() for u in recipients],
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


def resolve_impersonating_admin(db: Session, token) -> str:
    """The admin behind an impersonation portal token, re-checked now, or a 403.

    The session must still be open, and the admin must exist, be active and not trashed, and
    still hold an admin role (the same rule the impersonation start route applies).
    """
    from app.models.impersonation import ContactImpersonationSession
    from app.services.user_service import UserPermissionService

    refused = AppException(
        status_code=403, message="Comments can't be posted while viewing as a contact.", code="FORBIDDEN"
    )
    session = (
        db.query(ContactImpersonationSession)
        .filter(
            ContactImpersonationSession.portal_token_id == token.id,
            ContactImpersonationSession.ended_at.is_(None),
        )
        .first()
    )
    if session is None:
        raise refused
    admin = db.query(User).filter(User.id == session.admin_user_id).first()
    if admin is None or admin.is_trashed or str(admin.status) != "ACTIVE":
        raise refused
    roles = UserPermissionService(db).get_user_role_slugs(admin.id)
    if not roles & {UserPermissionService.SUPERADMIN_ROLE_SLUG, "admin"}:
        raise refused
    return str(admin.id)
