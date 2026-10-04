"""Resource management models."""
from sqlalchemy import BigInteger, Boolean, CheckConstraint, Column, Date, DateTime, ForeignKey, Index, Integer, String, Text, event
from sqlalchemy.dialects.postgresql import ARRAY, UUID, JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base
from app.models.base import CompanyScopedMixin
import uuid


class AttachmentDirectory(Base, CompanyScopedMixin):
    """Hierarchical folder for organizing attachments."""
    __tablename__ = "attachment_directories"

    # Multi-company: a folder is shareable exactly like an attachment (PLAN
    # shared-brand-attachments R17). NULL = shared across every company; the
    # do_orm_execute filter reads it as `company_id IS NULL OR company_id IN
    # scope` and the before_insert auto-stamp never overwrites an explicit
    # NULL, so `AttachmentCompanyService` / the upload path can share a folder
    # on purpose. Migration 449 drops the NOT NULL + DEFAULT this table
    # carried since 305/306.
    __company_shared__ = True

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    parent_id = Column(UUID(as_uuid=False), ForeignKey("attachment_directories.id", ondelete="CASCADE"), nullable=True)
    name = Column(String(255), nullable=False)
    sort_order = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    is_deleted = Column(Boolean, default=False, nullable=False)
    deleted_at = Column(DateTime(timezone=False), nullable=True)
    deleted_by = Column(String, nullable=True)

    parent = relationship("AttachmentDirectory", remote_side="AttachmentDirectory.id", back_populates="children")
    children = relationship("AttachmentDirectory", back_populates="parent", cascade="all, delete-orphan")
    attachments = relationship("Attachment", back_populates="directory")

    __table_args__ = (Index("ix_attachment_directories_parent_id", "parent_id"), Index("ix_attachment_directories_is_deleted", "is_deleted"),)


class AttachmentType(Base):
    __tablename__ = "attachment_types"
    
    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    code = Column(String(50), unique=True, nullable=True)
    type_name = Column(String(100), unique=True, nullable=False)
    description = Column(Text, nullable=True)
    allowed_extensions = Column(String(255), nullable=False)
    max_file_size_mb = Column(Integer, default=10, nullable=False)
    max_count_per_entity = Column(Integer, nullable=True)
    # When true, uploads of this type expose the "Linked to / Linked fields"
    # section so the file can be tied to a target table + field keys (e.g. product
    # photos). Replaces the hardcoded product-photo name check.
    supports_field_linkage = Column(Boolean, default=False, nullable=False, server_default="false")
    # When true, attachments of this type are "direct access" documents a dealer
    # may download without further gating. crm_resource_attachments_list is pinned
    # to these via `direct_access_only`.
    is_direct_access = Column(Boolean, default=False, nullable=False, server_default="false")
    # Badge artwork for this document type. A product renders the badge because
    # it HOLDS a document of this type, so the mark is a claim about the product
    # and is never placed by hand.
    certification_logo_attachment_id = Column(
        UUID(as_uuid=False), ForeignKey("attachments.id", ondelete="SET NULL"), nullable=True
    )
    # When false, an upload of this type does NOT call the n8n intake webhook, and
    # the upload-activity drawer skips it. Without this the row waits forever on a
    # reply that is never coming, and shows "Processing" while it waits. Default
    # true: every type n8n already handles keeps behaving as it does today.
    triggers_n8n_webhook = Column(
        Boolean, default=True, nullable=False, server_default="true"
    )
    # When true, an upload of this type can mint a certificate register row. The
    # gate is on the TYPE, not the type's name, so a new cert kind is a checkbox
    # rather than a code change - and a Technical Specifications sheet quoting
    # "cert PPS 0119" can never mint one.
    is_certificate = Column(Boolean, default=False, nullable=False, server_default="false")
    # Longest plausible validity span, in months, for a certificate of this type.
    # NULL = unlimited. Backs the plausibility check that flags a hallucinated
    # extraction date instead of swallowing it.
    max_validity_months = Column(Integer, nullable=True)
    # When true, an upload of this type is written with company_id = NULL -
    # visible to every company (PLAN-shared-brand-attachments R11). Flipping
    # this later touches no existing row (R1); it only changes what the NEXT
    # upload of this type does.
    is_shared = Column(Boolean, default=False, nullable=False, server_default="false")
    # The folder an upload of this type files into by default (R4, purchasing
    # consolidation batch 6 Sep 2026). NULL = no default, same as today; the type form
    # and the generic Create Attachment dialog both let the user pick another one anyway.
    # SET NULL on the folder's own delete, never CASCADE - deleting a folder must not
    # take a type definition down with it.
    default_directory_id = Column(
        UUID(as_uuid=False),
        ForeignKey("attachment_directories.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)

    default_directory = relationship("AttachmentDirectory", foreign_keys=[default_directory_id])

    # Two FK paths now link these tables: attachments.attachment_type_id (a file
    # OF this type) and attachment_types.certification_logo_attachment_id (this
    # type's badge artwork). Both sides must name the column they mean, or
    # SQLAlchemy cannot pick a join.
    attachments = relationship(
        "Attachment",
        back_populates="attachment_type",
        foreign_keys="Attachment.attachment_type_id",
    )
    certification_logo = relationship(
        "Attachment",
        foreign_keys=[certification_logo_attachment_id],
        viewonly=True,
    )


class Attachment(Base, CompanyScopedMixin):
    __tablename__ = "attachments"

    # Multi-company: attachments carry a NULLABLE company_id. NULL = shared (form
    # attachments on global complaints/PR/SI); non-null = owned (resource /
    # product / promotion). The scope filter therefore uses
    # `company_id IS NULL OR company_id IN {scope}` (AC-H5). This one stays
    # nullable permanently - the later NOT-NULL flip skips it.
    __company_shared__ = True

    # Audit user upload activity across ALL create paths (resource upload, entity
    # attachments on complaint/PR/SI, product photos, form files) - model-level so it
    # is path-agnostic. Bulk ZIP import (worker) suppresses per-row via
    # session.info["skip_audit_entity_types"] and logs one coarse row instead.
    __audit_track__ = True
    __audit_entity_type__ = "attachment"
    # Meaningful columns only - skip churny/huge fields (file_path, thumbnail_path,
    # file_hash, access_levels) so the diff stays readable.
    __audit_columns__ = [
        "original_filename",
        "stored_filename",
        "attachment_type_id",
        "entity_type",
        "entity_id",
        "directory_id",
        "full_directory_path",
        "description",
        "uploaded_by",
        "uploaded_by_contact_id",
        "uploader_kind",
        "is_deleted",
        "deleted_by",
        # Certification expiry. Audited on purpose: letting an expiry change go
        # unrecorded turns a compliance question into an unanswerable one.
        "valid_until",
    ]

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    attachment_type_id = Column(UUID(as_uuid=False), ForeignKey("attachment_types.id", ondelete="SET NULL"), nullable=True)
    original_filename = Column(String(255), nullable=False)
    stored_filename = Column(String(255), nullable=False)
    file_path = Column(Text, nullable=False)  # URL or path; TEXT to support CloudFront signed URLs
    # CDN base URL of a ~320px thumbnail object ("{key}.thumb.jpg") for the Files
    # grid; NULL for non-images / pre-backfill rows. Signed on read like file_path.
    thumbnail_path = Column(Text, nullable=True)
    file_size_bytes = Column(BigInteger, nullable=True)
    mime_type = Column(String(100), nullable=True)
    file_hash = Column(String(64), nullable=True)
    entity_type = Column(String(100), nullable=True)
    entity_id = Column(UUID(as_uuid=False), nullable=True)
    uploaded_by = Column(UUID(as_uuid=False), nullable=True)
    # Uploader attribution. `uploaded_by` only ever held a users.id, and the portal
    # upload path passed created_by=None - so a NULL meant both "a contact uploaded
    # it" and "we don't know". These two columns make "by contact" vs "by user"
    # derivable. TEXT (not UUID) to match respond_contacts.id, which is TEXT.
    uploaded_by_contact_id = Column(Text, ForeignKey("respond_contacts.id", ondelete="SET NULL"), nullable=True)
    uploader_kind = Column(String(16), nullable=True)  # user | contact | system
    uploaded_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    # Certification expiry. Lives on the DOCUMENT, not on each product link, so
    # one edit updates every product holding it - and an expired certificate
    # stops rendering its badge everywhere at once (AC-E5, AC-E8).
    valid_until = Column(Date, nullable=True)
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    is_deleted = Column(Boolean, default=False, nullable=False)
    deleted_at = Column(DateTime(timezone=False), nullable=True)
    deleted_by = Column(UUID(as_uuid=False), nullable=True)
    directory_id = Column(UUID(as_uuid=False), ForeignKey("attachment_directories.id", ondelete="SET NULL"), nullable=True)
    full_directory_path = Column(Text, nullable=True)  # e.g. "SORENTO CABANA (DEALER) --> SORENTO --> Product Photo --> Angle Valve"
    description = Column(Text, nullable=True)  # User-editable description for search / n8n AI agent
    access_levels = Column(JSONB, nullable=False, server_default='["dealer","end_user"]')  # dealer / end_user visibility
    sort_order = Column(Integer, nullable=True)
    # 's3' (legacy AWS S3 + CloudFront) or 'r2' (Cloudflare R2 + CDN). Drives URL signing dispatch.
    storage_provider = Column(String(16), nullable=False, server_default="s3")
    # Storage accessibility audit (attachment_storage_audit scheduled task).
    # 'accessible' = key found in its provider bucket, 'missing' = key absent
    # (broken link / lost bytes), 'unchecked' = not yet audited. Used by the
    # Files page "Storage status" filter to surface broken rows for trashing.
    storage_status = Column(String(20), nullable=False, server_default="unchecked")
    storage_checked_at = Column(DateTime(timezone=False), nullable=True)
    # Field-linkage template: at upload time the user picks a target table
    # (product / promotion / packing_list / form) and the field keys this
    # document is expected to answer. When the attachment is later linked to a
    # specific row via any link API, AttachmentFieldLinkService.apply_template_to_row
    # fans these out into per-row attachment_field_links rows.
    target_entity_type = Column(String(50), nullable=True)
    target_field_keys = Column(JSONB, nullable=True)
    # Per-submit UUID generated by the FE Create-Attachment dialog so all rows
    # uploaded together share a tag. Notification helpers read this back to
    # coalesce per-attachment n8n callbacks into a single outbox email.
    upload_batch_id = Column(String(36), nullable=True)
    # Regions chosen at upload for a Packing List file ('west' / 'east'); NULL for every other
    # file. Read by the external packing-list create when its payload names none.
    regions = Column(ARRAY(Text), nullable=True)

    attachment_type = relationship(
        "AttachmentType",
        back_populates="attachments",
        foreign_keys=[attachment_type_id],
    )
    directory = relationship("AttachmentDirectory", back_populates="attachments")

    __table_args__ = (
        CheckConstraint(
            "regions IS NULL OR (cardinality(regions) >= 1 AND regions <@ ARRAY['west','east']::text[])",
            name="ck_attachments_regions",
        ),
        Index("ix_attachments_entity_type_entity_id", "entity_type", "entity_id"),
        Index("ix_attachments_uploaded_by", "uploaded_by"),
        Index("ix_attachments_is_deleted", "is_deleted"),
        Index("ix_attachments_file_hash", "file_hash"),
        Index("ix_attachments_directory_id", "directory_id"),
        Index("ix_attachments_access_levels", "access_levels", postgresql_using="gin"),
        Index("ix_attachments_storage_provider", "storage_provider"),
        Index("ix_attachments_storage_status", "storage_status"),
        Index("ix_attachments_upload_batch_id", "upload_batch_id"),
    )


@event.listens_for(Attachment, "before_insert")
def _attachment_sync_created_at_with_uploaded(_mapper, _connection, target: Attachment) -> None:
    """Keep created_at identical to uploaded_at on insert (single timestamp for both)."""
    from datetime import datetime

    now = datetime.utcnow()
    if target.uploaded_at is None:
        target.uploaded_at = now
    target.created_at = target.uploaded_at
