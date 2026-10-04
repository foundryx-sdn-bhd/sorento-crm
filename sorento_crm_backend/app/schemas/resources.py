"""Resource management schemas."""
from pydantic import BaseModel, ConfigDict, field_validator, model_validator
from typing import Optional, List, Literal
from datetime import datetime
import uuid


class AttachmentDirectoryBase(BaseModel):
    name: str
    parent_id: Optional[str] = None
    sort_order: Optional[int] = None


class AttachmentDirectoryCreate(AttachmentDirectoryBase):
    pass


class AttachmentDirectoryUpdate(BaseModel):
    name: Optional[str] = None
    parent_id: Optional[str] = None
    sort_order: Optional[int] = None


class AttachmentDirectoryMoveRequest(BaseModel):
    """Reparent + reorder a folder in one shot. position=null appends; position re-sequences siblings."""
    parent_id: Optional[str] = None
    position: Optional[int] = None


class AttachmentDirectoryResponse(AttachmentDirectoryBase):
    model_config = ConfigDict(from_attributes=True)

    id: str
    created_at: datetime
    # Owning company (R14 / AC-E3): same __company_shared__ contract as
    # DriveFolderItem below - NULL is a legitimate Shared folder, not a
    # missing value. Populated for free from the ORM row wherever a route
    # returns one directly (from_attributes); the tree endpoint stamps it
    # explicitly since it builds nodes from raw field values, not an ORM row.
    company_id: Optional[str] = None
    company_name: Optional[str] = None


class AttachmentDirectoryTreeNode(AttachmentDirectoryResponse):
    """Directory with nested children for tree API."""
    children: List["AttachmentDirectoryTreeNode"] = []


# Allow self-reference for tree
AttachmentDirectoryTreeNode.model_rebuild()


class AttachmentTypeBase(BaseModel):
    type_name: str
    description: Optional[str] = None
    allowed_extensions: str
    max_file_size_mb: int = 10
    # Max attachments of this type per entity row. NULL = unlimited. Enforced by
    # the portal upload quota check; admin-editable (was DB-only until 2026-07-28).
    max_count_per_entity: Optional[int] = None
    supports_field_linkage: bool = False
    # Cert-bearing gate: when true, an upload of this type may file a certificate
    # (identity + revision + coverage). Without it the ingest guard drops extracted
    # certificate fields, so the register stays empty.
    is_certificate: bool = False
    # Plausibility cap for the extracted validity span, in months. NULL = no cap.
    # Backs the needs_review rule that catches a hallucinated expiry date.
    max_validity_months: Optional[int] = None
    # When false, an upload of this type does not call the n8n intake webhook and
    # is left out of the upload-activity drawer. A type n8n never answers would
    # otherwise show "Processing" forever, waiting on a reply that is not coming.
    triggers_n8n_webhook: bool = True
    # When true, an upload of this type is written with company_id = NULL -
    # visible to every company. Flipping this later touches no existing row.
    is_shared: bool = False
    # Folder an upload of this type files into by default (R4). None = no default,
    # same as today.
    default_directory_id: Optional[str] = None


class AttachmentTypeCreate(AttachmentTypeBase):
    pass


class AttachmentTypeUpdate(BaseModel):
    type_name: Optional[str] = None
    description: Optional[str] = None
    allowed_extensions: Optional[str] = None
    max_file_size_mb: Optional[int] = None
    max_count_per_entity: Optional[int] = None
    supports_field_linkage: Optional[bool] = None
    is_certificate: Optional[bool] = None
    max_validity_months: Optional[int] = None
    triggers_n8n_webhook: Optional[bool] = None
    is_shared: Optional[bool] = None
    default_directory_id: Optional[str] = None


class AttachmentTypeResponse(AttachmentTypeBase):
    model_config = ConfigDict(from_attributes=True)

    id: str
    created_at: datetime
    # Stable machine key (e.g. 'packing_list'), distinct from the editable
    # `type_name` label. Read-only: not on AttachmentTypeBase, so create/update
    # payloads cannot set it. NULL for types that predate the column.
    code: Optional[str] = None

    @field_validator('id', mode='before')
    @classmethod
    def convert_uuid_to_string(cls, v):
        """Convert UUID objects to strings."""
        if v is None:
            return None
        if isinstance(v, uuid.UUID):
            return str(v)
        return str(v)
    
class AttachmentBase(BaseModel):
    attachment_type_id: Optional[str] = None
    original_filename: str
    stored_filename: str
    file_path: str
    file_size_bytes: Optional[int] = None
    mime_type: Optional[str] = None
    file_hash: Optional[str] = None
    entity_type: Optional[str] = None
    entity_id: Optional[str] = None
    directory_id: Optional[str] = None
    full_directory_path: Optional[str] = None  # e.g. "SORENTO CABANA (DEALER) --> SORENTO --> Product Photo --> Angle Valve"
    description: Optional[str] = None
    access_levels: Optional[list[str]] = None  # e.g. ["dealer", "end_user"]
    sort_order: Optional[int] = None
    storage_provider: Optional[str] = None  # 's3' or 'r2'; controls URL signing dispatch
    thumbnail_path: Optional[str] = None  # CDN base URL of the grid thumbnail; NULL for non-images
    # Field-linkage template applied when the attachment is later linked to a
    # specific row via any link API. See app.services.field_linkage.registry.
    target_entity_type: Optional[str] = None
    target_field_keys: Optional[list[str]] = None
    upload_batch_id: Optional[str] = None
    #: Packing List uploads only ('west' / 'east'); NULL otherwise.
    regions: Optional[list[str]] = None

    @field_validator("regions")
    @classmethod
    def _check_regions(cls, v):
        from app.schemas.regions import normalize_regions

        return normalize_regions(v)


def _validate_field_linkage_template(target_entity_type, target_field_keys):
    """Shared validator used on both AttachmentCreate and AttachmentUpdate.

    Empty inputs are tolerated: the template is opt-in. When a target_entity_type
    is given without keys (or vice versa) we leave the row in a partial state - 
    it just won't fan out anything until a future PUT completes the pair.
    """
    from app.services.field_linkage import (
        SUPPORTED_ENTITY_TYPES,
        validate_field_keys,
    )

    et = (target_entity_type or "").strip() or None
    if et and et not in SUPPORTED_ENTITY_TYPES:
        raise ValueError(
            f"Unsupported target_entity_type '{et}'. "
            f"Expected one of: {', '.join(SUPPORTED_ENTITY_TYPES)}."
        )
    if target_field_keys and et:
        ok, unknown = validate_field_keys(et, list(target_field_keys))
        if unknown:
            raise ValueError(
                f"Unknown target_field_keys for {et}: {', '.join(unknown)}"
            )
        return et, ok
    if target_field_keys and not et:
        raise ValueError(
            "target_entity_type is required when target_field_keys are provided."
        )
    return et, list(target_field_keys or [])


class AttachmentCreate(AttachmentBase):
    # Optional caller-supplied PK so the upload path can embed the same uuid in the object key
    # before the row is flushed. None → DB default generates one.
    id: Optional[str] = None

    @model_validator(mode="after")
    def _check_field_linkage_template(self):
        et, keys = _validate_field_linkage_template(
            self.target_entity_type, self.target_field_keys
        )
        object.__setattr__(self, "target_entity_type", et)
        object.__setattr__(self, "target_field_keys", keys or None)
        return self


class AttachmentUpdate(BaseModel):
    attachment_type_id: Optional[str] = None
    entity_type: Optional[str] = None
    entity_id: Optional[str] = None
    directory_id: Optional[str] = None
    description: Optional[str] = None
    access_levels: Optional[list[str]] = None
    sort_order: Optional[int] = None
    # The user-facing, RENAMEABLE name. This is what rename edits, what the UI shows, what the
    # download Content-Disposition and the n8n webhook use. Editing it is DB-only - the object
    # key is uuid-segregated and derived from the IMMUTABLE original_filename, so the object
    # never moves. original_filename is intentionally NOT updatable here.
    # See PLAN-attachment-key-uuid-segregation.md.
    stored_filename: Optional[str] = None
    target_entity_type: Optional[str] = None
    target_field_keys: Optional[list[str]] = None

    @model_validator(mode="after")
    def _check_field_linkage_template(self):
        # Only validate when one of the two is explicitly provided. PATCH-style:
        # both None means "don't touch the template".
        if self.target_entity_type is None and self.target_field_keys is None:
            return self
        et, keys = _validate_field_linkage_template(
            self.target_entity_type, self.target_field_keys
        )
        self.target_entity_type = et
        self.target_field_keys = keys or None
        return self

    @field_validator("stored_filename", mode="before")
    @classmethod
    def sanitize_stored_filename(cls, v):
        """Trim, reject path separators / control chars, cap at 255 chars. None means 'no change'."""
        if v is None:
            return None
        if not isinstance(v, str):
            v = str(v)
        s = v.strip()
        if not s:
            raise ValueError("stored_filename cannot be empty.")
        if any(ch in s for ch in ("/", "\\", "\x00")) or any(ord(ch) < 32 for ch in s):
            raise ValueError("stored_filename cannot contain path separators or control characters.")
        if len(s) > 255:
            raise ValueError("stored_filename must not exceed 255 characters.")
        return s


class AttachmentBulkDeleteRequest(BaseModel):
    """Request body for mass-deleting attachments."""
    attachment_ids: list[str]

    @field_validator("attachment_ids")
    @classmethod
    def at_least_one(cls, v: list[str]) -> list[str]:
        if not v or len(v) == 0:
            raise ValueError("At least one attachment ID is required")
        return v


class AttachmentReorderRequest(BaseModel):
    """Request body for reordering attachments within a folder."""
    directory_id: Optional[str] = None
    attachment_ids: list[str]

    @field_validator("attachment_ids")
    @classmethod
    def at_least_one(cls, v: list[str]) -> list[str]:
        if not v or len(v) == 0:
            raise ValueError("At least one attachment ID is required")
        return v


class AttachmentsBulkMoveRequest(BaseModel):
    """Move many attachments into the same target folder in one transaction."""
    attachment_ids: list[str]
    directory_id: Optional[str] = None

    @field_validator("attachment_ids")
    @classmethod
    def at_least_one(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("At least one attachment ID is required")
        return v


class AttachmentsBulkMoveResponse(BaseModel):
    updated: int


class UploadedByUser(BaseModel):
    """User who uploaded the attachment (for display)."""
    id: str
    name: Optional[str] = None
    email: Optional[str] = None

    @field_validator('id', mode='before')
    @classmethod
    def convert_uuid_to_string(cls, v):
        if v is None:
            return None
        if isinstance(v, uuid.UUID):
            return str(v)
        return str(v)


class AttachmentTypeSimple(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    type_name: str
    description: Optional[str] = None
    
    @field_validator('id', mode='before')
    @classmethod
    def convert_uuid_to_string(cls, v):
        """Convert UUID objects to strings."""
        if v is None:
            return None
        if isinstance(v, uuid.UUID):
            return str(v)
        return str(v)
    
class LinkedEntityRef(BaseModel):
    """Reference to a linked entity (product, promotion, or form) resolved from junction/parent tables."""
    id: str
    name: str
    description: Optional[str] = None
    link_id: Optional[str] = None  # ProductAttachment/PromotionAttachment id for unlink; forms use id as form_id
    # Populated for linked_products / linked_certificates on a SHARED
    # attachment (PLAN-shared-brand-attachments.md S5, UAC group G): the
    # entity's own company and whether it is the viewer's ACTIVE company.
    # Left at the defaults (None / None / True) for every other linked list,
    # so a single-company attachment's payload is unchanged (AC-G3).
    company_id: Optional[str] = None
    company_name: Optional[str] = None
    in_scope: bool = True


class AttachmentResponse(AttachmentBase):
    model_config = ConfigDict(from_attributes=True)

    id: str
    uploaded_by: Optional[str] = None
    uploaded_by_user: Optional[UploadedByUser] = None
    uploaded_at: datetime
    created_at: datetime
    is_deleted: bool = False
    deleted_at: Optional[datetime] = None
    deleted_by: Optional[str] = None
    storage_status: Optional[str] = None  # accessible | missing | unchecked (read-only audit flag)
    storage_checked_at: Optional[datetime] = None
    # Owning company. NULL is legitimate (attachments are company-SHARED: a form
    # attachment belongs to no one company). The name is resolved by the list /
    # detail serializers, since the model carries no relationship to Company.
    company_id: Optional[str] = None
    company_name: Optional[str] = None
    thumbnail_url: Optional[str] = None  # freshly-signed thumbnail URL for the grid (set by list serializer)
    attachment_type: Optional[AttachmentTypeSimple] = None
    entity_display_name: Optional[str] = None
    linked_products: list[LinkedEntityRef] = []
    linked_promotions: list[LinkedEntityRef] = []
    linked_form: Optional[LinkedEntityRef] = None
    linked_packing_lists: list[LinkedEntityRef] = []
    # Certificates this file is a filed revision of. Read-only on the FE: the
    # link is created by filing the document, not by a user linking two rows.
    linked_certificates: list[LinkedEntityRef] = []

    @field_validator('id', 'uploaded_by', 'deleted_by', 'attachment_type_id', 'entity_id', 'directory_id', 'company_id', mode='before')
    @classmethod
    def convert_uuid_to_string(cls, v):
        """Convert UUID objects to strings."""
        if v is None:
            return None
        if isinstance(v, uuid.UUID):
            return str(v)
        if isinstance(v, str):
            return v
        return str(v)


class AccessPropagationTarget(BaseModel):
    """Linked entity that can receive propagated access_levels (with human-facing code)."""
    kind: Literal["product", "promotion", "form", "packing_list"]
    entity_id: str
    code: str
    name: Optional[str] = None


class BulkAccessLevelsPreviewRequest(BaseModel):
    """Preview propagation targets: pass either directory_id (folder subtree) or attachment_ids (bulk selection)."""
    attachment_ids: Optional[list[str]] = None
    directory_id: Optional[str] = None

    @model_validator(mode="after")
    def exactly_one_scope(self):
        has_attachments = bool(self.attachment_ids and len(self.attachment_ids) > 0)
        has_dir = bool(self.directory_id and str(self.directory_id).strip())
        if has_attachments == has_dir:
            raise ValueError("Provide exactly one of: non-empty attachment_ids or directory_id")
        return self


class BulkAccessLevelsPreviewResponse(BaseModel):
    attachment_count: int
    targets: list[AccessPropagationTarget]


class BulkAccessLevelsApplyRequest(BaseModel):
    attachment_ids: Optional[list[str]] = None
    directory_id: Optional[str] = None
    access_levels: list[str]
    propagate_to_linked: bool = False

    @model_validator(mode="after")
    def exactly_one_scope(self):
        has_attachments = bool(self.attachment_ids and len(self.attachment_ids) > 0)
        has_dir = bool(self.directory_id and str(self.directory_id).strip())
        if has_attachments == has_dir:
            raise ValueError("Provide exactly one of: non-empty attachment_ids or directory_id")
        return self


class BulkAccessLevelsApplyResponse(BaseModel):
    updated_attachments: int
    propagated: Optional[dict] = None


class BulkAttachmentTypeRequest(BaseModel):
    """Bulk-edit (or single-edit) attachment_type_id on N attachments.
    Does NOT trigger any webhook resubmission - purely a metadata change."""

    attachment_ids: list[str]
    attachment_type_id: str

    @field_validator("attachment_ids")
    @classmethod
    def at_least_one(cls, v: list[str]) -> list[str]:
        if not v or len(v) == 0:
            raise ValueError("At least one attachment ID is required.")
        return v


class BulkAttachmentTypeResponse(BaseModel):
    updated_attachments: int
    attachment_type_id: str


class BulkCompanyRequest(BaseModel):
    """`Set company…` (PLAN-shared-brand-attachments R4/S2). At least one id
    overall; `company_id: null` means Shared."""

    attachment_ids: list[str] = []
    directory_ids: list[str] = []
    company_id: Optional[str] = None

    @field_validator("attachment_ids", "directory_ids")
    @classmethod
    def _ids_are_uuids(cls, v: list[str]) -> list[str]:
        # A malformed id reaching the ORM `IN (...)` filter raises a raw
        # psycopg `invalid input syntax for type uuid` - an unhandled 500.
        # Caught here instead, it is a 422 like every other bad request body.
        for item in v:
            try:
                uuid.UUID(str(item))
            except (ValueError, AttributeError, TypeError):
                raise ValueError(f"{item!r} is not a valid UUID.")
        return v

    @field_validator("company_id")
    @classmethod
    def _company_id_is_uuid(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        try:
            uuid.UUID(str(v))
        except (ValueError, AttributeError, TypeError):
            raise ValueError(f"{v!r} is not a valid UUID.")
        return v

    @model_validator(mode="after")
    def at_least_one_id(self):
        if not self.attachment_ids and not self.directory_ids:
            raise ValueError("At least one attachment or directory ID is required.")
        return self


class BulkCompanyResponse(BaseModel):
    updated_directories: int
    updated_attachments: int
    company_id: Optional[str] = None
    links_added: int
    links_removed: int
    certificates_updated: int


# ---------------------------------------------------------------------------
# Unified Drive (folders + files in one server-sorted, server-paginated stream)
# See docs/plans/PLAN-unified-drive-files.md (D11) + UAC D1/D2.
# ---------------------------------------------------------------------------


class DriveFolderItem(BaseModel):
    """A folder row in the unified Drive listing (kind='folder')."""
    model_config = ConfigDict(from_attributes=True)

    kind: Literal["folder"] = "folder"
    id: str
    name: str
    parent_id: Optional[str] = None
    sort_order: Optional[int] = None
    created_at: Optional[datetime] = None
    # Human-readable path to THIS folder's parent (shown as Location during search).
    directory_path: Optional[str] = None
    # Owning company (R14 / AC-E3): a folder is __company_shared__ same as an
    # attachment, so NULL is legitimate - both keys are explicit None, which
    # the FE renders as "Shared", the same convention _stamp_company already
    # uses for file rows.
    company_id: Optional[str] = None
    company_name: Optional[str] = None

    @field_validator("id", "parent_id", "company_id", mode="before")
    @classmethod
    def _uuid_to_str(cls, v):
        if v is None:
            return None
        return str(v)


class DriveFileItem(AttachmentResponse):
    """A file row in the unified Drive listing (kind='file').

    Inherits the full AttachmentResponse contract so file rows behave exactly
    like the existing Files list, plus a discriminator and a resolved
    human-readable ``directory_path`` (Location column) computed in-query - 
    no N+1 (UAC D2).
    """
    kind: Literal["file"] = "file"
    directory_path: Optional[str] = None


class DriveListResponse(BaseModel):
    """Discriminated, server-sorted, server-paginated Drive page (UAC D1)."""
    data: list[dict]  # serialized DriveFolderItem | DriveFileItem dicts
    pagination: "PaginationResponse"
    empty: bool = False
    recursive: bool = False


from app.schemas.common import PaginationResponse  # noqa: E402

DriveListResponse.model_rebuild()
