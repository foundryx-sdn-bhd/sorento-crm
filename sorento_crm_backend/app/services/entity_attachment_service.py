"""Reusable service for linking attachments to any entity."""
from __future__ import annotations

import logging
import os
import uuid
from typing import Optional, Any
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.entity_attachment import EntityAttachmentLink
from app.models.resources import Attachment, AttachmentType
from app.services.error_handler import (
    AppException,
    handle_not_found,
    handle_conflict,
    handle_validation_error,
)

logger = logging.getLogger(__name__)

# Staff response attachments (stock inquiry purchasing response, complaint
# technical team response) use their own attachment type so staff uploads
# never share a quota with the contact's portal_submission uploads. See
# documentation/plans/PLAN-response-attachments-and-portal-nav.md decision D9.
RESPONSE_ATTACHMENT_TYPE_CODE = "response_attachment"

# Distinct from None, which is a legitimate cached result (type not seeded).
_UNRESOLVED = object()


class EntityAttachmentService:
    """Generic attachment link operations (create/link/list/unlink) across entity types."""

    def __init__(self, db: Session):
        self.db = db

    def _resolve_attachment_url(self, file_path: Optional[str]) -> Optional[str]:
        """Return a usable URL for the stored file_path. Provider-agnostic.

        For full URLs (already a CDN base URL) we pass through; raw keys are
        signed via the storage_router using URL-host sniffing or the default
        provider since this helper does not have row context.
        """
        if not file_path:
            return None
        if file_path.startswith(("http://", "https://")):
            return file_path
        try:
            from app.services.storage_router import resolve_signed_url

            return resolve_signed_url(file_path)
        except Exception:
            return file_path

    def _normalize(self, entity_type: str, entity_id: str) -> tuple[str, str]:
        et = (entity_type or "").strip()
        eid = (entity_id or "").strip()
        if not et:
            raise handle_validation_error("entity_type is required")
        if not eid:
            raise handle_validation_error("entity_id is required")
        return et, eid

    def _attachment_type_id_by_code(self, code: str) -> str:
        row = self.db.query(AttachmentType.id).filter(AttachmentType.code == code).first()
        if not row:
            raise handle_not_found("Attachment type", code)
        return row[0]

    def _response_attachment_type_id(self) -> Optional[str]:
        """Id of the ``response_attachment`` type, resolved once per service
        instance (serialize_link runs per row, a 50-row list must not fire 50
        lookups). Returns None on installs that predate the type seed.
        """
        cached = getattr(self, "_response_type_id_cache", _UNRESOLVED)
        if cached is _UNRESOLVED:
            row = (
                self.db.query(AttachmentType.id)
                .filter(AttachmentType.code == RESPONSE_ATTACHMENT_TYPE_CODE)
                .first()
            )
            cached = str(row[0]) if row else None
            self._response_type_id_cache = cached
        return cached

    def get_attachment_type_by_code(self, code: str) -> AttachmentType:
        """Full AttachmentType row (allowed_extensions / max_file_size_mb /
        max_count_per_entity), used by upload paths that must enforce quota
        before storing bytes."""
        row = self.db.query(AttachmentType).filter(AttachmentType.code == code).first()
        if not row:
            raise handle_not_found("Attachment type", code)
        return row

    def check_quota(
        self,
        attachment_type: AttachmentType,
        entity_type: str,
        entity_id: str,
        incoming_size: int,
        incoming_ext: str,
    ) -> None:
        """Enforce ``attachment_type``'s allowed_extensions / max_file_size_mb /
        max_count_per_entity for a new upload onto entity_type/entity_id.

        The per-record count is scoped to THIS attachment type only (joins
        Attachment.attachment_type_id), so a type with its own cap (e.g.
        response_attachment, D9) never counts against another type's budget on
        the same entity. Mirrors the portal upload path's quota check
        (app/api/v1/public/portal.py::_check_quota).
        """
        allowed_exts = {
            e.strip().lower().lstrip(".")
            for e in (attachment_type.allowed_extensions or "").split(",")
            if e.strip()
        }
        if allowed_exts and incoming_ext not in allowed_exts:
            raise handle_validation_error(
                f"Unsupported file type. Allowed: {', '.join(sorted(allowed_exts))}."
            )
        max_bytes = (attachment_type.max_file_size_mb or 0) * 1024 * 1024
        if max_bytes and incoming_size > max_bytes:
            raise handle_validation_error(
                f"File exceeds {attachment_type.max_file_size_mb} MB limit."
            )
        if attachment_type.max_count_per_entity is not None:
            existing = (
                self.db.query(EntityAttachmentLink)
                .join(Attachment, Attachment.id == EntityAttachmentLink.attachment_id)
                .filter(
                    EntityAttachmentLink.entity_type == entity_type,
                    EntityAttachmentLink.entity_id == entity_id,
                    Attachment.attachment_type_id == attachment_type.id,
                )
                .count()
            )
            if existing >= attachment_type.max_count_per_entity:
                raise handle_validation_error(
                    f"Attachment limit reached ({attachment_type.max_count_per_entity})."
                )

    def list_links(self, entity_type: str, entity_id: str) -> list[EntityAttachmentLink]:
        et, eid = self._normalize(entity_type, entity_id)
        return (
            self.db.query(EntityAttachmentLink)
            .filter(EntityAttachmentLink.entity_type == et, EntityAttachmentLink.entity_id == eid)
            .order_by(
                EntityAttachmentLink.sort_order.asc().nulls_last(),
                EntityAttachmentLink.created_at.asc(),
            )
            .all()
        )

    def list_links_for_entities(
        self, entity_type: str, entity_ids: list[str]
    ) -> dict[str, list[EntityAttachmentLink]]:
        if not entity_ids:
            return {}
        et = (entity_type or "").strip()
        rows = (
            self.db.query(EntityAttachmentLink)
            .filter(
                EntityAttachmentLink.entity_type == et,
                EntityAttachmentLink.entity_id.in_([str(i) for i in entity_ids if i is not None]),
            )
            .order_by(
                EntityAttachmentLink.entity_id.asc(),
                EntityAttachmentLink.sort_order.asc().nulls_last(),
                EntityAttachmentLink.created_at.asc(),
            )
            .all()
        )
        out: dict[str, list[EntityAttachmentLink]] = {}
        for row in rows:
            out.setdefault(str(row.entity_id), []).append(row)
        return out

    def link_existing_attachment(
        self,
        entity_type: str,
        entity_id: str,
        attachment_id: str,
        created_by: Optional[str] = None,
    ) -> EntityAttachmentLink:
        et, eid = self._normalize(entity_type, entity_id)
        att_id = (attachment_id or "").strip()
        if not att_id:
            raise handle_validation_error("attachment_id is required")

        attachment = self.db.query(Attachment).filter(Attachment.id == att_id).first()
        if not attachment:
            raise handle_not_found("Attachment", att_id)

        existing = (
            self.db.query(EntityAttachmentLink)
            .filter(
                EntityAttachmentLink.entity_type == et,
                EntityAttachmentLink.entity_id == eid,
                EntityAttachmentLink.attachment_id == att_id,
            )
            .first()
        )
        if existing:
            raise handle_conflict("Attachment is already linked to this record.")

        count = (
            self.db.query(EntityAttachmentLink)
            .filter(EntityAttachmentLink.entity_type == et, EntityAttachmentLink.entity_id == eid)
            .count()
        )
        # `sort_order` is MAX(existing) + 1, not the count (review round 1, item 5's
        # nit on `shipment_line_photos`, fixed here since every caller shares this
        # method): once a middle link has been deleted, `count()` repeats an existing
        # value and two links tie for the same position instead of the new one always
        # landing last.
        max_sort_order = (
            self.db.query(func.max(EntityAttachmentLink.sort_order))
            .filter(EntityAttachmentLink.entity_type == et, EntityAttachmentLink.entity_id == eid)
            .scalar()
        )
        next_sort_order = 0 if max_sort_order is None else max_sort_order + 1
        link = EntityAttachmentLink(
            entity_type=et,
            entity_id=eid,
            attachment_id=att_id,
            is_primary=(count == 0),
            sort_order=next_sort_order,
            created_by=created_by,
        )
        self.db.add(link)
        self.db.flush()
        self.db.refresh(link)
        return link

    def create_attachment_and_link(
        self,
        entity_type: str,
        entity_id: str,
        file_url: str,
        file_name: Optional[str] = None,
        file_size_bytes: Optional[int] = None,
        attachment_type_code: str = "complaint_document",
        created_by: Optional[str] = None,
        storage_provider: Optional[str] = None,
        thumbnail_path: Optional[str] = None,
        mime_type: Optional[str] = None,
        uploaded_by: Optional[str] = None,
        uploader_kind: Optional[str] = None,
        uploaded_by_contact_id: Optional[str] = None,
    ) -> EntityAttachmentLink:
        """``uploaded_by`` / ``uploader_kind`` are additive attribution params
        (default None, no behaviour change for existing callers that don't pass
        them): an internal (JWT) upload path passes ``uploaded_by=<users.id>``
        and ``uploader_kind='user'`` so the row is distinguishable from a
        contact/system upload (UAC B2). ``uploaded_by_contact_id`` (chatbot
        media-into-turn, S4) is the same idea for a CONTACT-sourced upload (a
        WhatsApp photo/voice note) - pass it with ``uploader_kind='contact'``
        rather than ``uploaded_by``, which is a ``users.id`` FK."""
        et, eid = self._normalize(entity_type, entity_id)
        url = (file_url or "").strip()
        if not url:
            raise handle_validation_error("file_url is required")

        type_id = self._attachment_type_id_by_code((attachment_type_code or "").strip())
        name = (file_name or url.split("/")[-1].split("?")[0] or "document").strip()[:255]
        stored = name[:255]
        path = url[:500]

        from app.services.storage_router import default_provider, normalize_provider

        provider = normalize_provider(storage_provider) if storage_provider else default_provider()

        attachment = Attachment(
            attachment_type_id=type_id,
            original_filename=name,
            stored_filename=stored,
            file_path=path,
            thumbnail_path=thumbnail_path,
            file_size_bytes=file_size_bytes,
            storage_provider=provider,
            mime_type=mime_type,
            uploaded_by=uploaded_by,
            uploader_kind=uploader_kind,
            uploaded_by_contact_id=uploaded_by_contact_id,
        )
        self.db.add(attachment)
        self.db.flush()
        return self.link_existing_attachment(
            entity_type=et,
            entity_id=eid,
            attachment_id=str(attachment.id),
            created_by=created_by,
        )

    def delete_link(self, link_id: str, entity_type: Optional[str] = None) -> EntityAttachmentLink:
        q = self.db.query(EntityAttachmentLink).filter(EntityAttachmentLink.id == link_id)
        if entity_type:
            q = q.filter(EntityAttachmentLink.entity_type == entity_type.strip())
        link = q.first()
        if not link:
            raise handle_not_found("Entity attachment link", link_id)
        self.db.delete(link)
        self.db.flush()
        return link

    def delete_links_for_entity(self, entity_type: str, entity_id: str) -> int:
        et, eid = self._normalize(entity_type, entity_id)
        deleted = (
            self.db.query(EntityAttachmentLink)
            .filter(EntityAttachmentLink.entity_type == et, EntityAttachmentLink.entity_id == eid)
            .delete(synchronize_session=False)
        )
        self.db.flush()
        return int(deleted or 0)

    def serialize_link(
        self,
        link: EntityAttachmentLink,
        entity_key: str,
        link_type: Optional[str] = None,
    ) -> dict[str, Any]:
        """Serialize one generic link into API response shape with a custom entity id key.

        Carries uploader attribution so every surface that renders an attachment
        list can say WHO added it: the CRM "Linked Attachments" panels and the
        public /view pages both read this shape. `can_unlink` is false for
        staff-uploaded rows so a contact-facing surface can hide the control
        (the portal DELETE also rejects those server-side, UAC F1/F2).
        """
        att = link.attachment
        kind = getattr(att, "uploader_kind", None)
        # A staff response upload must report link_type='response_attachment'
        # regardless of the per-entity label the caller passes, or the FE cannot
        # tell it apart from a manually linked file and routes its unlink to the
        # wrong endpoint (UAC F4).
        resolved_link_type = link_type or link.entity_type
        response_type_id = self._response_attachment_type_id()
        if response_type_id and str(
            getattr(att, "attachment_type_id", "") or ""
        ) == response_type_id:
            resolved_link_type = RESPONSE_ATTACHMENT_TYPE_CODE
        return {
            "id": link.id,
            entity_key: link.entity_id,
            "attachment_id": link.attachment_id,
            "file_name": getattr(att, "original_filename", None),
            "original_filename": getattr(att, "original_filename", None),
            "file_url": self._resolve_attachment_url(getattr(att, "file_path", None)),
            "file_size_bytes": getattr(att, "file_size_bytes", None),
            "mime_type": getattr(att, "mime_type", None),
            "uploaded_at": getattr(att, "uploaded_at", None) or link.created_at,
            "created_at": getattr(att, "created_at", None)
            or getattr(att, "uploaded_at", None)
            or link.created_at,
            "link_type": resolved_link_type,
            "uploader_kind": kind,
            "uploaded_by_name": self._resolve_uploader_name(att),
            "uploaded_by_role": (
                "staff" if kind == "user" else "contact" if kind == "contact" else "unknown"
            ),
            "can_unlink": kind != "user",
        }

    def _resolve_uploader_name(self, att: Any) -> str:
        """Human name of whoever uploaded this file. Never a UUID.

        Staff rows resolve from `users`, contact rows from `respond_contacts`
        (name, else first + last, else phone). Legacy rows carry neither and
        read "Unknown" rather than a guessed name (UAC B5).

        Memoised per service instance on (kind, id): serialize_link runs once
        per row, and a 50-row attachment page is usually a handful of distinct
        uploaders - without the memo this is one query per row on every list.
        """
        if att is None:
            return "Unknown"
        kind = getattr(att, "uploader_kind", None)
        principal_id = (
            getattr(att, "uploaded_by_contact_id", None)
            if kind == "contact"
            else getattr(att, "uploaded_by", None)
        )
        cache_key = (str(kind or ""), str(principal_id or ""))
        cache = getattr(self, "_uploader_name_cache", None)
        if cache is None:
            cache = {}
            self._uploader_name_cache = cache
        if cache_key in cache:
            return cache[cache_key]
        resolved = self._lookup_uploader_name(att, kind)
        cache[cache_key] = resolved
        return resolved

    def _lookup_uploader_name(self, att: Any, kind: Optional[str]) -> str:
        try:
            if kind == "contact":
                from app.models.access import RespondContact

                contact_id = getattr(att, "uploaded_by_contact_id", None)
                if contact_id:
                    row = (
                        self.db.query(RespondContact)
                        .filter(RespondContact.id == str(contact_id))
                        .first()
                    )
                    if row is not None:
                        return (
                            (row.name or "").strip()
                            or " ".join(
                                p for p in [row.first_name, row.last_name] if p
                            ).strip()
                            or (row.phone_number or "").strip()
                            or "Unknown"
                        )
            elif kind == "user":
                from app.models.user import User

                user_id = getattr(att, "uploaded_by", None)
                if user_id:
                    row = (
                        self.db.query(User).filter(User.id == str(user_id)).first()
                    )
                    if row is not None:
                        return (row.name or "").strip() or (row.email or "").strip() or "Unknown"
        except Exception:  # noqa: BLE001
            # Attribution is decoration: a lookup failure must never break the
            # attachment list it decorates.
            logger.warning("Uploader name lookup failed for attachment %s", getattr(att, "id", None))
        return "Unknown"


# ---------------------------------------------------------------------------
# Staff response attachments (stock inquiry purchasing response, complaint
# technical team response). See PLAN-response-attachments-and-portal-nav.md
# S2 and UAC-response-attachments.md groups C/D/F4.
# ---------------------------------------------------------------------------


def count_staff_attachments(db: Session, entity_type: str, entity_id: str) -> int:
    """Count staff RESPONSE attachments linked to an entity. Used to size the
    outgoing-message attachment sentence (UAC D1/D3).

    Scoped to the ``response_attachment`` type, NOT merely to
    ``uploader_kind='user'``: a staff member who links an internal spec sheet
    through the Linked Attachments panel is also `user`-stamped (the uploader
    backfill stamps every historical staff upload that way), and counting those
    would tell a customer "1 attachment added" pointing at an internal document
    nobody chose to send. The type exists precisely to draw that line.
    The contact's own uploads never count either.
    """
    et = (entity_type or "").strip()
    eid = (entity_id or "").strip()
    if not et or not eid:
        return 0
    type_row = (
        db.query(AttachmentType.id)
        .filter(AttachmentType.code == RESPONSE_ATTACHMENT_TYPE_CODE)
        .first()
    )
    if not type_row:
        return 0
    return (
        db.query(EntityAttachmentLink)
        .join(Attachment, Attachment.id == EntityAttachmentLink.attachment_id)
        .filter(
            EntityAttachmentLink.entity_type == et,
            EntityAttachmentLink.entity_id == eid,
            Attachment.uploader_kind == "user",
            Attachment.attachment_type_id == type_row[0],
        )
        .count()
    )


def compose_response_attachment_sentence(count: int) -> str:
    """Attachment sentence appended to the outgoing Respond.io text AT SEND TIME
    only (UAC D1/D2/D3/D10) - never persisted to ``purchasing_response`` /
    ``technical_team_response``. Empty string when count is 0 (never say
    "0 attachments").
    """
    if count <= 0:
        return ""
    # "attachment", not "image": staff can reply with a PDF or spreadsheet too.
    # Singular / plural is spelled out because this text goes to a customer.
    if count == 1:
        return (
            "\U0001F4CE Attachment response: 1 attachment added, "
            "open the link below to view it."
        )
    return (
        f"\U0001F4CE Attachment response: {count} attachments added, "
        "open the link below to view them."
    )


def _response_attachment_ext(filename: Optional[str], content_type: Optional[str]) -> str:
    """File extension for quota checks. Mirrors app/api/v1/public/portal.py::_ext."""
    if filename and "." in filename:
        return filename.rsplit(".", 1)[-1].lower().strip()
    if content_type and "/" in content_type:
        return content_type.split("/", 1)[1].split(";", 1)[0].strip().lower()
    return ""


def _safe_presigned_url(file_path: Optional[str], provider: Optional[str] = None) -> Optional[str]:
    """Sign a response-attachment URL against its storage provider. Mirrors
    app/api/v1/public/portal.py::_safe_presigned_url; best-effort, never raises."""
    if not file_path:
        return None
    try:
        from app.services.storage_router import resolve_signed_url

        return resolve_signed_url(file_path, provider=provider)
    except Exception as e:  # noqa: BLE001
        logger.warning("Response attachment presigned URL failed for %s: %s", file_path, e)
        return None


def list_attachments_for_entity(db: Session, entity_type: str, entity_id: str) -> list[dict]:
    """Attachments linked to one entity, in the portal's attachment shape:
    ``link_id``, ``attachment_id``, ``filename``, ``size``, ``url``,
    ``content_type``, ``uploaded_at``, ``uploader_kind``, ``uploaded_by_name``,
    ``uploaded_by_role``, ``can_unlink``.

    One implementation for every surface that reads this shape - moved here
    (was ``app/api/v1/public/portal.py::_list_attachments_for``) so the price
    tag request's CRM detail route can call it too, alongside the portal's own
    legacy-kind detail routes, without a service module reaching up into a
    route module (PLAN-price-tag-feedback-r2 S1 / D49).
    """
    rows = (
        db.query(EntityAttachmentLink, Attachment)
        .join(Attachment, Attachment.id == EntityAttachmentLink.attachment_id)
        .filter(
            EntityAttachmentLink.entity_type == entity_type,
            EntityAttachmentLink.entity_id == entity_id,
        )
        .order_by(
            EntityAttachmentLink.sort_order.asc().nulls_last(),
            EntityAttachmentLink.created_at.asc(),
        )
        .all()
    )

    # Batch-resolve uploader names in two queries rather than one per row - a
    # submission can carry up to the type's per-record cap (10-20) attachments.
    from app.models.access import RespondContact
    from app.models.user import User

    contact_ids = {att.uploaded_by_contact_id for _, att in rows if att.uploaded_by_contact_id}
    user_ids = {att.uploaded_by for _, att in rows if att.uploaded_by}
    contacts_by_id: dict[str, RespondContact] = {}
    if contact_ids:
        contacts_by_id = {
            c.id: c
            for c in db.query(RespondContact).filter(RespondContact.id.in_(contact_ids)).all()
        }
    users_by_id: dict[str, User] = {}
    if user_ids:
        users_by_id = {
            u.id: u for u in db.query(User).filter(User.id.in_(user_ids)).all()
        }

    out: list[dict] = []
    for link, att in rows:
        uploader_kind = att.uploader_kind
        uploaded_by_name = "Unknown"
        uploaded_by_role = "unknown"
        if uploader_kind == "contact" and att.uploaded_by_contact_id:
            contact = contacts_by_id.get(att.uploaded_by_contact_id)
            name = (
                (
                    (contact.name or "").strip()
                    or " ".join(
                        p for p in [(contact.first_name or "").strip(), (contact.last_name or "").strip()] if p
                    ).strip()
                    or (contact.phone_number or "").strip()
                )
                if contact is not None
                else ""
            )
            if name:
                uploaded_by_name = name
                uploaded_by_role = "contact"
        elif uploader_kind == "user" and att.uploaded_by:
            user = users_by_id.get(att.uploaded_by)
            name = ((user.name or "").strip() or (user.email or "").strip()) if user is not None else ""
            if name:
                uploaded_by_name = name
                uploaded_by_role = "staff"
        out.append(
            {
                "link_id": str(link.id),
                "attachment_id": str(att.id),
                "filename": att.original_filename,
                "size": att.file_size_bytes,
                "url": _safe_presigned_url(att.file_path, getattr(att, "storage_provider", None)) or att.file_path,
                "content_type": att.mime_type if hasattr(att, "mime_type") else None,
                "uploaded_at": att.uploaded_at.isoformat() if att.uploaded_at else None,
                "uploader_kind": uploader_kind,
                "uploaded_by_name": uploaded_by_name,
                "uploaded_by_role": uploaded_by_role,
                # A staff (`user`) upload has no unlink control in the portal -
                # server-enforced in portal_delete_attachment, this just matches
                # the FE's gating so it never renders a control that would 403.
                "can_unlink": uploader_kind != "user",
            }
        )
    return out


def create_response_attachment(
    db: Session,
    *,
    entity_type: str,
    entity_id: str,
    contents: bytes,
    filename: Optional[str],
    content_type: Optional[str],
    uploaded_by: Optional[str],
    commit: bool = True,
) -> dict[str, Any]:
    """Upload + link a staff response attachment (``response_attachment`` type, D9).

    Mirrors the portal upload path (app/api/v1/public/portal.py::portal_upload_attachment)
    but scoped to its own attachment type so staff and contact uploads never
    share a per-record quota, and stamps ``uploader_kind='user'`` +
    ``uploaded_by`` (UAC B2/C4). Returns the fixed upload-and-link response
    shape shared by the stock-inquiry and complaint response-attachment routes.
    """
    service = EntityAttachmentService(db)
    attachment_type = service.get_attachment_type_by_code(RESPONSE_ATTACHMENT_TYPE_CODE)

    # `attachments.uploaded_by` / `entity_attachment_links.created_by` are UUID
    # columns while `users.id` is a String, and this route also accepts an API-key
    # principal (id "system"). A non-uuid id would blow up the INSERT with
    # "invalid input syntax for type uuid" -> 500 on upload, so drop it the same
    # way every neighbouring call site does and keep the file.
    uploader_id = (
        str(uploaded_by)
        if isinstance(uploaded_by, str) and len(str(uploaded_by)) == 36
        else None
    )

    incoming_size = len(contents or b"")
    extension = _response_attachment_ext(filename, content_type)
    service.check_quota(attachment_type, entity_type, entity_id, incoming_size, extension)

    safe_ext = f".{extension}" if extension else ""
    key = f"response_attachments/{entity_type}/{entity_id}/{uuid.uuid4()}{safe_ext}"

    from app.services.storage_router import default_provider, get_backend

    provider = default_provider()
    backend = get_backend(provider)
    try:
        backend.upload_file(contents, key, content_type=content_type)
    except Exception as e:  # noqa: BLE001
        logger.warning("Response attachment upload failed (provider=%s): %s", provider, e)
        raise AppException(
            status_code=502,
            message="File upload failed. Please try again.",
            code="UPLOAD_FAILED",
        ) from e

    # Grid thumbnail (images only), best-effort - never blocks the upload.
    from app.services.image_thumbnailer import store_thumbnail

    thumbnail_path = store_thumbnail(backend, provider, key, contents, content_type)

    link = service.create_attachment_and_link(
        entity_type=entity_type,
        entity_id=entity_id,
        file_url=key,
        file_name=filename or os.path.basename(key),
        file_size_bytes=incoming_size,
        attachment_type_code=RESPONSE_ATTACHMENT_TYPE_CODE,
        created_by=uploader_id,
        thumbnail_path=thumbnail_path,
        storage_provider=provider,
        mime_type=content_type,
        uploaded_by=uploader_id,
        uploader_kind="user",
    )
    if commit:
        db.commit()
        db.refresh(link)
    attachment = db.query(Attachment).filter(Attachment.id == link.attachment_id).first()
    file_path = attachment.file_path if attachment else key
    return {
        "link_id": str(link.id),
        "attachment_id": str(link.attachment_id),
        "filename": attachment.original_filename if attachment else (filename or key),
        "size": incoming_size,
        "url": _safe_presigned_url(file_path, provider) or file_path,
        "content_type": (attachment.mime_type if attachment else content_type) or None,
    }

