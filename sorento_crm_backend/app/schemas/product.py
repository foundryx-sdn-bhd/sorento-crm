"""Product schemas."""
from pydantic import BaseModel, ConfigDict, Field, field_validator
from typing import Optional, List
from datetime import date, datetime
from decimal import Decimal
import uuid


class ProductCategoryBase(BaseModel):
    category_code: str
    category_name: str
    description: Optional[str] = None
    parent_category_id: Optional[str] = None
    is_active: bool = True
    # False for categories with no class meaning (MISC, PROJECT ...): every product
    # in them is hidden from the chatbot whatever its own flag says (issue #300).
    is_searchable: bool = True
    display_order: Optional[int] = 0
    # Chatbot stock ask v2 S1 (PLAN-chatbot-stock-ask-v2-24sep.md, R2): X and Y for
    # every product in this category, unless the product overrides them. None means
    # not opted in (resolves to 0, app.services.stock_ask_limits.effective()).
    chatbot_max_qty: Optional[int] = Field(None, ge=0)
    chatbot_eta_offset_days: Optional[int] = Field(None, ge=0)


class ProductCategoryCreate(ProductCategoryBase):
    pass


class ProductCategoryUpdate(BaseModel):
    category_name: Optional[str] = None
    description: Optional[str] = None
    parent_category_id: Optional[str] = None
    is_active: Optional[bool] = None
    is_searchable: Optional[bool] = None
    display_order: Optional[int] = None
    chatbot_max_qty: Optional[int] = Field(None, ge=0)
    chatbot_eta_offset_days: Optional[int] = Field(None, ge=0)


class ProductCategoryResponse(ProductCategoryBase):
    id: str
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class BrandBase(BaseModel):
    brand_code: str
    brand_name: str
    manufacturer: Optional[str] = None
    website: Optional[str] = None
    description: Optional[str] = None
    logo_url: Optional[str] = None
    is_active: bool = True
    # Visibility codes overlapping `contact_access_types.code`. Used by the
    # resolver's promotion-domain product fallback to scope product search
    # to brands the active contact can see.
    access_levels: list[str] = []
    # PLAN-brand-flows-to-purchasing.md: false means every product on this brand
    # is bought locally by CS and never raises an Order Inquiry. Default true so
    # nothing changes until an admin flips it.
    flows_to_purchasing: bool = True
    # Owner ruling R1 on PR #833: the brand's chatbot weight (0 = no preference).
    chatbot_weight: float = Field(0, ge=0, le=9999)
    # "Customers can ask for this brand" (#1286, D3). False keeps a placeholder brand
    # (OTHERS, NO LOGO) out of what the chatbot offers and binds on a single word.
    is_searchable: bool = True


class BrandCreate(BrandBase):
    pass


class BrandUpdate(BaseModel):
    brand_name: Optional[str] = None
    manufacturer: Optional[str] = None
    website: Optional[str] = None
    description: Optional[str] = None
    logo_url: Optional[str] = None
    is_active: Optional[bool] = None
    access_levels: Optional[list[str]] = None
    flows_to_purchasing: Optional[bool] = None
    chatbot_weight: Optional[float] = Field(None, ge=0, le=9999)
    is_searchable: Optional[bool] = None


class BrandResponse(BrandBase):
    id: str
    created_at: datetime
    updated_at: Optional[datetime] = None
    product_count: Optional[int] = None
    # Set only on a `company_scope=grants` read, so a picker can tag each row.
    company_id: Optional[str] = None
    company_name: Optional[str] = None

    class Config:
        from_attributes = True


class UnitOfMeasureBase(BaseModel):
    uom_code: str
    uom_name: str
    base_uom_id: Optional[str] = None
    conversion_factor: Optional[Decimal] = None
    # Canonical divisibility, 0..4 (front-planning plan 6.4, AC-F12). Omitted on CREATE
    # resolves to 0 - the same fallback a missing rollout value takes - so a unit is
    # whole-units until someone says otherwise.
    decimal_places: int = Field(0, ge=0, le=4)
    description: Optional[str] = None
    is_active: bool = True


class UnitOfMeasureCreate(UnitOfMeasureBase):
    pass


class UnitOfMeasureUpdate(BaseModel):
    uom_name: Optional[str] = None
    base_uom_id: Optional[str] = None
    conversion_factor: Optional[Decimal] = None
    # Omitted PRESERVES the stored value (the update applies `exclude_unset`), so a
    # partial edit cannot silently reset a measure unit back to whole numbers.
    decimal_places: Optional[int] = Field(None, ge=0, le=4)
    description: Optional[str] = None
    is_active: Optional[bool] = None


class UnitOfMeasureResponse(UnitOfMeasureBase):
    id: str
    created_at: datetime
    updated_at: Optional[datetime] = None
    product_count: Optional[int] = None
    base_uom: Optional["UnitOfMeasureSimple"] = None

    class Config:
        from_attributes = True


class ProductBase(BaseModel):
    product_code: str
    product_name: str

    @field_validator("product_code", "product_name", "barcode", mode="before")
    @classmethod
    def strip_string_fields(cls, v):
        """Trim leading/trailing whitespace to avoid dirty data."""
        if isinstance(v, str):
            return v.strip()
        return v

    @field_validator("list_price", "cost_price", "invoice_price", mode="after")
    @classmethod
    def prices_non_negative(cls, v):
        """Reject negative prices."""
        if v is not None and v < 0:
            raise ValueError("Price cannot be negative")
        return v

    description: Optional[str] = None
    category_id: str
    brand_id: Optional[str] = None
    base_uom_id: str
    item_type: Optional[str] = None
    # CRM-owned (PLAN D14): manual entry here, or a non-empty AutoCount sync
    # value. Never unique - a placeholder product may carry none at all.
    barcode: Optional[str] = Field(None, max_length=100)
    list_price: Decimal
    cost_price: Optional[Decimal] = None
    invoice_price: Optional[Decimal] = None
    currency: str = "MYR"
    weight: Optional[Decimal] = None
    dimensions_length: Optional[Decimal] = None
    dimensions_width: Optional[Decimal] = None
    dimensions_height: Optional[Decimal] = None
    warranty_months: Optional[int] = None
    has_serial_tracking: bool = False
    has_batch_tracking: bool = False
    reorder_level: Optional[int] = None
    reorder_quantity: Optional[int] = None
    # Chatbot stock ask v2 S1 (PLAN-chatbot-stock-ask-v2-24sep.md, R2): X and Y for
    # this product; overrides the category value when set. None falls back to the
    # product's own category, then to 0 (app.services.stock_ask_limits.effective()).
    chatbot_max_qty: Optional[int] = Field(None, ge=0)
    chatbot_eta_offset_days: Optional[int] = Field(None, ge=0)
    is_active: bool = True
    # Whether the chatbot may answer with this product. Independent of is_active:
    # an order placeholder stays active and is still not a chat answer (#300).
    is_searchable: bool = True
    # D2 (ingest-parity-standardisation): an explicit flag wins over the
    # description-derived **** convention every channel otherwise falls back
    # to - None means "not sent", not "false". See product_rules.is_discontinued.
    is_discontinued: Optional[bool] = None
    # S5 (PLAN-reorder-feedback-9sep.md): the buyer's own switch, no backfill (G3) - every
    # existing product defaults false and is flipped by hand.
    exclude_from_planning: bool = False
    # PLAN-price-tag-r10.md S4: staff-authored copy for the price tag - CRM-owned,
    # never sent by the AutoCount masters push (AC-S4-9, `CanonicalProduct`
    # deliberately has no field of this name).
    price_tag_description: Optional[str] = None

    @field_validator("currency", mode="before")
    @classmethod
    def normalize_currency(cls, v):
        """Normalize ISO 4217 currency: trim, uppercase, default MYR when empty."""
        if v is None:
            return "MYR"
        s = str(v).strip().upper()
        return s or "MYR"

    @field_validator("barcode", mode="before")
    @classmethod
    def normalize_barcode(cls, v):
        """Blank normalizes to None, same rule `normalize_currency` uses for an
        empty value - a cleared field is unset, not a stored empty string."""
        if v is None:
            return None
        s = str(v).strip()
        return s or None


class ProductCreate(ProductBase):
    pass


class ProductUpdate(BaseModel):
    product_name: Optional[str] = None
    description: Optional[str] = None
    category_id: Optional[str] = None
    brand_id: Optional[str] = None
    base_uom_id: Optional[str] = None
    item_type: Optional[str] = None
    barcode: Optional[str] = Field(None, max_length=100)
    list_price: Optional[Decimal] = None
    cost_price: Optional[Decimal] = None
    invoice_price: Optional[Decimal] = None
    currency: Optional[str] = None
    weight: Optional[Decimal] = None
    dimensions_length: Optional[Decimal] = None
    dimensions_width: Optional[Decimal] = None
    dimensions_height: Optional[Decimal] = None
    warranty_months: Optional[int] = None
    has_serial_tracking: Optional[bool] = None
    has_batch_tracking: Optional[bool] = None
    reorder_level: Optional[int] = None
    reorder_quantity: Optional[int] = None
    chatbot_max_qty: Optional[int] = Field(None, ge=0)
    chatbot_eta_offset_days: Optional[int] = Field(None, ge=0)
    is_active: Optional[bool] = None
    is_searchable: Optional[bool] = None
    # D2: explicit flag wins over the description-derived value (only recomputed
    # when this is absent AND description is being changed - see update_product).
    is_discontinued: Optional[bool] = None
    # S5: None means "not sent", same convention as the other planning flags on this schema.
    exclude_from_planning: Optional[bool] = None
    # PLAN-price-tag-r10.md S4: None = untouched by this PUT (AC-S4-2/S4-9).
    price_tag_description: Optional[str] = None

    @field_validator("currency", mode="before")
    @classmethod
    def normalize_currency(cls, v):
        """Normalize ISO 4217 currency when provided: trim + uppercase. None means 'no change'."""
        if v is None:
            return None
        s = str(v).strip().upper()
        return s or None

    @field_validator("product_name", "description", "barcode", mode="before")
    @classmethod
    def strip_optional_strings(cls, v):
        """Trim leading/trailing whitespace when provided."""
        if isinstance(v, str):
            return v.strip()
        return v

    @field_validator("barcode", mode="before")
    @classmethod
    def normalize_barcode(cls, v):
        """Blank normalizes to None, same rule `normalize_currency` uses for an
        empty value - a cleared field is unset, not a stored empty string."""
        if v is None:
            return None
        s = str(v).strip()
        return s or None

    @field_validator("list_price", "cost_price", "invoice_price", mode="after")
    @classmethod
    def prices_non_negative(cls, v):
        """Reject negative prices."""
        if v is not None and v < 0:
            raise ValueError("Price cannot be negative")
        return v


class ProductCategorySimple(BaseModel):
    id: str
    category_code: str
    category_name: str
    # A category with no class meaning hides every product in it from the chatbot
    # (#300). Carried on the product so the UI can show the EFFECTIVE visibility,
    # not just the product's own flag.
    is_searchable: bool = True

    class Config:
        from_attributes = True


class ProductSimple(BaseModel):
    """Simple product reference for ProductAttachment."""
    id: str
    product_code: str
    product_name: str
    is_discontinued: bool = False

    class Config:
        from_attributes = True


class BrandSimple(BaseModel):
    id: str
    brand_code: str
    brand_name: str
    
    class Config:
        from_attributes = True


class UnitOfMeasureSimple(BaseModel):
    id: str
    uom_code: str
    uom_name: str
    
    class Config:
        from_attributes = True


class ProductSetRef(BaseModel):
    """A set this product belongs to.

    Human-readable on purpose: the FE links by `set_code`, never by UUID. Always
    a LIST on the response - one cistern serves both the S-trap and the P-trap
    assembly, so a single field would silently show one and hide the other.
    """
    id: str
    set_code: str
    name: str


class ProductVariantRef(BaseModel):
    """Lightweight product reference for the variant graph (parent / children).

    Deliberately human-readable: exposes `product_code`/`product_name` so the FE
    never renders a raw UUID. Used for `ProductResponse.variant_of` and
    `ProductResponse.variants`.
    """
    id: str
    product_code: str
    product_name: str

    @field_validator("id", mode="before")
    @classmethod
    def _uuid_to_str(cls, v):
        if v is None:
            return None
        if isinstance(v, uuid.UUID):
            return str(v)
        return str(v)

    class Config:
        from_attributes = True


class ProductResponse(ProductBase):
    id: str
    is_discontinued: bool = False
    # When the discontinue-notify scheduler last noticed this product (issue #1287).
    # Null while available or before the first tick after discontinuing.
    discontinued_at: Optional[datetime] = Field(default=None, validation_alias="discontinued_notified_at")
    # --- Variant graph (see PLAN-suggest-on-miss-variant-graph.md §1) ---
    # `is_variant` is derived from the (always-loaded) `variant_of_id` column, so
    # it is cheap on LIST rows too (no extra query). `variant_of` / `variants`
    # are read from stashed attrs populated ONLY by the detail getter
    # (product_service.get_product); on LIST rows those attrs are absent so the
    # relationships are never touched (no N+1) and both default to null / [].
    is_variant: bool = Field(default=False, validation_alias="variant_of_id")
    variant_of: Optional["ProductVariantRef"] = Field(default=None, validation_alias="_variant_of_ref")
    variants: List["ProductVariantRef"] = Field(default_factory=list, validation_alias="_variant_children")
    # Manual-curation flag (plain column; cheap on list rows). Detail page reads it
    # for the "Manual" badge + Reset-to-auto button.
    variant_link_manual: bool = False
    # Direct-child count. On list rows populated by two bounded IN-queries (no N+1);
    # on detail rows set from the loaded children by `_populate_variant_graph`.
    variant_child_count: int = Field(default=0, validation_alias="_variant_child_count")
    #: Populated by `ProductService._populate_product_sets`. Declared here because
    #: `response_model` drops anything it was not told about, and a field that is
    #: populated but undeclared looks exactly like a backend that never sent it.
    product_sets: List["ProductSetRef"] = Field(
        default_factory=list, validation_alias="_product_sets"
    )

    @field_validator("is_variant", mode="before")
    @classmethod
    def _coerce_is_variant(cls, v):
        """`variant_of_id IS NOT NULL` -> is_variant. Serializer reads the column
        via validation_alias; here we collapse it to a bool."""
        return v is not None
    created_at: datetime
    updated_at: Optional[datetime] = None
    created_by: Optional[str] = None
    updated_by: Optional[str] = None
    category: Optional[ProductCategorySimple] = None
    brand: Optional[BrandSimple] = None
    base_uom: Optional[UnitOfMeasureSimple] = None
    # Map of `field_key` -> linked attachments. Populated only when an
    # attachment_field_links row exists for this product. Used by the
    # detail-page Specifications tooltip and by the AI agent (so it can
    # answer "how big is product X" without a second tool call).
    field_attachments: Optional[dict] = None
    # Multi-company reply clarity: the owning company. ``company_name`` is
    # resolved ONLY when the lookup spanned more than one company
    # (`company_scope.stamp_lookup_companies`), and is null otherwise.
    company_id: Optional[str] = None
    company_name: Optional[str] = None

    @field_validator('created_by', 'updated_by', 'company_id', mode='before')
    @classmethod
    def convert_uuid_to_str(cls, v):
        """Convert UUID objects to strings for created_by/updated_by/company_id."""
        if v is None:
            return None
        if isinstance(v, uuid.UUID):
            return str(v)
        return str(v) if v else None

    class Config:
        from_attributes = True


# Import Attachment schemas for ProductAttachment
from app.schemas.resources import AttachmentResponse, AttachmentTypeSimple


class AttachmentSimple(BaseModel):
    """Simple attachment reference for ProductAttachment."""
    id: str
    original_filename: str
    stored_filename: str
    file_path: str
    file_size_bytes: Optional[int] = None
    mime_type: Optional[str] = None
    uploaded_at: datetime
    created_at: datetime
    attachment_type: Optional[AttachmentTypeSimple] = None
    full_directory_path: Optional[str] = None  # e.g. "SORENTO CABANA (DEALER) --> SORENTO --> Product Photo --> Angle Valve"
    access_levels: Optional[list[str]] = None  # e.g. ["dealer", "end_user"]; source of truth at attachment level

    @field_validator('id', mode='before')
    @classmethod
    def convert_uuid_to_string(cls, v):
        """Convert UUID objects to strings."""
        if v is None:
            return None
        if isinstance(v, uuid.UUID):
            return str(v)
        return str(v)
    
    class Config:
        from_attributes = True


class ProductAttachmentBase(BaseModel):
    product_id: str
    attachment_id: str
    is_primary: Optional[bool] = False
    sort_order: Optional[int] = None
    access_levels: Optional[list[str]] = None
    #: Set only when a PRODUCT SET code fanned this link out. NULL means a person
    #: or an exact product code made it.
    linked_via_set_id: Optional[str] = None
    # company_id is deliberately NOT a field here: this schema is the body of
    # POST /master-data/product-attachments/, a client-facing route, and a
    # client-settable company would let a signed-in user stamp a link into a
    # company they hold no grant for. The one internal caller that needs an
    # explicit company (the n8n twin linker, under an all-companies scope)
    # passes it as a keyword to ProductService.create_product_attachment
    # instead - never through this schema.


class ProductAttachmentCreate(ProductAttachmentBase):
    pass


class ProductAttachmentUpdate(BaseModel):
    is_primary: Optional[bool] = None
    sort_order: Optional[int] = None
    access_levels: Optional[list[str]] = None


# --- Brochure image: which photo of a product a catalogue tile shows (S7.0) ---
#
# camelCase on the wire, because the picker screen reads these keys directly and
# a snake_case response is a blank screen rather than an error. The field alias
# covers both directions, so the service's already-camelCase dicts validate
# straight into these models.


class BrochureImageCandidate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    attachment_id: str = Field(alias="attachmentId")
    # The only thing telling two thumbnails apart when one of them turns out to
    # be a different product entirely.
    filename: Optional[str] = None
    url: Optional[str] = None
    access_levels: Optional[List[str]] = Field(default=None, alias="accessLevels")


class BrochureImageRow(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    product_id: str = Field(alias="productId")
    product_code: Optional[str] = Field(default=None, alias="productCode")
    product_name: Optional[str] = Field(default=None, alias="productName")
    chosen_attachment_id: Optional[str] = Field(default=None, alias="chosenAttachmentId")
    candidates: List[BrochureImageCandidate] = Field(default_factory=list)


class BrochureImageList(BaseModel):
    items: List[BrochureImageRow] = Field(default_factory=list)
    total: int = 0
    # Products in this filter still without a chosen image. The number the screen
    # leads with, so it is counted over the whole filter and not the page.
    remaining: int = 0
    shown: int = 0
    # Products in this filter with at least one image somebody could pick,
    # chosen or not. Zero means the screen has nothing to offer at all, which is
    # a different answer from "you have finished" and needs a different empty
    # state. Counted over the filter, never over the page.
    choosable: int = 0


class BrochureImageSet(BaseModel):
    """The body of a choose-this-photo call.

    Requests are snake_case and responses are camelCase, which reads as
    inconsistent but matches the rest of this API: bodies go in as the database
    spells them and come out as the screen needs them.

    One spelling only, deliberately. Accepting ``attachmentId`` as well was
    tried and removed: nothing sent it, so it was a second code path kept alive
    on speculation, and an untested alias is how a real casing mismatch passes
    unnoticed. A client using the wrong key gets a 422 naming the field.
    """

    attachment_id: str


class BrochureImageChoice(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    product_id: str = Field(alias="productId")
    chosen_attachment_id: Optional[str] = Field(default=None, alias="chosenAttachmentId")


class BrochureImageAdoptSingle(BaseModel):
    """Which products to answer, where the answer is not in doubt.

    A list rather than "everything matching the filter": the screen knows which
    products it is showing, and a filter re-evaluated on the server could take
    in rows the user never saw.
    """

    product_ids: List[str]


class BrochureImageAdopted(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    # The ones actually answered. Products with no candidate, or with a choice
    # to make, are absent - so the screen can say what it did rather than what
    # it tried.
    product_ids: List[str] = Field(default_factory=list, alias="productIds")


# Max rows per product import (queued job); kept reasonable to avoid huge request payloads
BULK_IMPORT_MAX_ROWS_PER_REQUEST = 50_000


class BulkDeleteProductsRequest(BaseModel):
    """Request schema for bulk delete. Body: { ids: string[] }."""
    ids: List[str]


class ProductBulkUpdates(BaseModel):
    """What a bulk edit may change. One field today (#300); the shape leaves room
    for the next one without a second endpoint."""
    is_searchable: bool


class BulkUpdateProductsRequest(BaseModel):
    """Request schema for bulk update. Body: { ids: string[], updates: {...} }."""
    ids: List[str] = Field(min_length=1)
    updates: ProductBulkUpdates


class BulkImportProductsRequest(BaseModel):
    """Request schema for product bulk import. Each item is a row (Excel headers as keys). Processed in background as an import job."""
    products: List[dict]
    validate_only: Optional[bool] = False  # If True, run validation only (no import); return errors/warnings.

    @field_validator("products")
    @classmethod
    def products_batch_limit(cls, v: List[dict]) -> List[dict]:
        if len(v) > BULK_IMPORT_MAX_ROWS_PER_REQUEST:
            raise ValueError(
                f"Maximum {BULK_IMPORT_MAX_ROWS_PER_REQUEST} rows per import. Reduce the file size and try again."
            )
        return v


class BulkImportProductsResponse(BaseModel):
    """Response schema for product bulk import."""
    created: int = 0
    updated: int = 0
    errors: List[str] = []


class ProductAttachmentCertificate(BaseModel):
    """Certificate-register facts for an attachment that IS a filed certificate.

    Present only when the attachment is a certificate revision; a brochure or a
    spec sheet carries ``certificate: null``. ``validity_state`` is derived on
    every read from this revision's own window (never stored), so a consumer can
    say "expired" / "expiring soon" / "valid" without doing date arithmetic and
    without a scheduler having had to run.
    """
    certificate_id: str
    scheme: Optional[str] = None
    certificate_number: Optional[str] = None
    certifying_body: Optional[str] = None
    valid_from: Optional[date] = None
    valid_until: Optional[date] = None
    validity_state: str
    is_expired: bool = False
    days_until_expiry: Optional[int] = None
    # False once a renewal has been filed: this file is a superseded issue.
    is_current_revision: bool = True


class ProductAttachmentResponse(ProductAttachmentBase):
    id: str
    created_at: Optional[datetime] = None
    created_by: Optional[str] = None
    synced_to_excel: Optional[bool] = False
    last_synced_to_excel: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    product: Optional[ProductSimple] = None
    attachment: Optional[AttachmentSimple] = None
    certificate: Optional[ProductAttachmentCertificate] = None
    # Multi-company reply clarity: the owning company. ``company_name`` is
    # resolved ONLY when the lookup spanned more than one company
    # (`company_scope.stamp_lookup_companies`), and is null otherwise.
    company_id: Optional[str] = None
    company_name: Optional[str] = None

    @field_validator('id', 'product_id', 'attachment_id', 'created_by', 'company_id', mode='before')
    @classmethod
    def convert_uuid_to_string(cls, v):
        """Convert UUID objects to strings."""
        if v is None:
            return None
        if isinstance(v, uuid.UUID):
            return str(v)
        return str(v)
    
    class Config:
        from_attributes = True


# ---------------------------------------------------------------- select rows
# The four master-data `/select` routes answer ANY signed-in user (owner ruling 1 Oct
# 2026, never-stuck L10): quotation / SO / PO line editors, the SCM filter bar, series,
# price floors and the dealer-kit picker read them. So they carry only what a picker
# shows, never the admin configuration on the full row (chatbot weights and limits, brand
# access levels, purchasing flag, timestamps, product counts).


class UnitOfMeasureSelectItem(BaseModel):
    id: str
    uom_code: str
    uom_name: str
    # How many decimals a quantity in this unit takes (front-planning plan 6.4): line
    # editors need it to accept 1.5 kg but not 1.5 pieces.
    decimal_places: int = 0

    class Config:
        from_attributes = True


class BrandSelectItem(BaseModel):
    id: str
    brand_code: str
    brand_name: str
    is_active: bool = True

    class Config:
        from_attributes = True


class ProductCategorySelectItem(BaseModel):
    id: str
    category_code: str
    category_name: str
    is_active: bool = True

    class Config:
        from_attributes = True
