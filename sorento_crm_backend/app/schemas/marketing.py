"""Marketing management schemas."""
from __future__ import annotations

from pydantic import BaseModel, Field, field_serializer, field_validator
from typing import Generic, Optional, TypeVar
from datetime import date, datetime

from app.schemas.common import ListResponse

from app.schemas.promotion_dates import (
    normalize_promotion_start_end_optional,
    promotion_date_to_api_iso,
)
from decimal import Decimal
import uuid


T = TypeVar("T")


def _normalize_promotion_markers(v) -> list[str]:
    """Markers are compared case-insensitively; store them lowercase and unique."""
    if v is None:
        return []
    if isinstance(v, str):
        v = v.split(",")
    out: list[str] = []
    for marker in v:
        text = str(marker).strip().lower()
        if text and text not in out:
            out.append(text)
    return out


class PromotionServingListResponse(ListResponse[T], Generic[T]):
    """List payload for the three endpoints that accept `serving_policy=true`.

    `serving_policy_applied` says the rows were picked by the per-type promotion
    policy rather than the plain active gate. It lives here and not on the generic
    `ListResponse` so orders, stock and users don't carry a marketing concern.
    """
    serving_policy_applied: Optional[bool] = None


class PromotionTypeBase(BaseModel):
    type_code: str = Field(min_length=1)
    type_name: str
    description: Optional[str] = None
    show_expired: bool = False
    expired_valid_until_year_end: bool = False
    expired_max_age_days: Optional[int] = Field(default=None, ge=0)
    match_markers: list[str] = []
    match_priority: int = 100
    is_default: bool = False
    sort_order: int = 0

    @field_validator("match_markers", mode="before")
    @classmethod
    def _normalize_markers(cls, v):
        return _normalize_promotion_markers(v)

    @field_validator("type_code", mode="before")
    @classmethod
    def _normalize_code(cls, v):
        return str(v).strip().lower() if v is not None else v


class PromotionTypeCreate(PromotionTypeBase):
    pass


class PromotionTypeUpdate(BaseModel):
    type_code: Optional[str] = Field(default=None, min_length=1)
    type_name: Optional[str] = None
    description: Optional[str] = None
    show_expired: Optional[bool] = None
    expired_valid_until_year_end: Optional[bool] = None
    expired_max_age_days: Optional[int] = Field(default=None, ge=0)
    match_markers: Optional[list[str]] = None
    match_priority: Optional[int] = None
    is_default: Optional[bool] = None
    sort_order: Optional[int] = None

    @field_validator("match_markers", mode="before")
    @classmethod
    def _normalize_markers_optional(cls, v):
        if v is None:
            return None
        return _normalize_promotion_markers(v)

    @field_validator("type_code", mode="before")
    @classmethod
    def _normalize_code_optional(cls, v):
        return str(v).strip().lower() if v is not None else v


class PromotionTypeResponse(PromotionTypeBase):
    id: str
    created_at: datetime
    updated_at: datetime
    # How many promotions currently point at this type (list view only).
    promotions_count: Optional[int] = 0

    @field_validator("id", mode="before")
    @classmethod
    def convert_uuid_to_string(cls, v):
        if v is None:
            return None
        return str(v)

    class Config:
        from_attributes = True


class PromotionTypeSimple(BaseModel):
    id: str
    type_code: str
    type_name: str
    show_expired: bool = False

    @field_validator("id", mode="before")
    @classmethod
    def convert_uuid_to_string(cls, v):
        return str(v) if v is not None else None

    class Config:
        from_attributes = True


class PromotionBase(BaseModel):
    description: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    is_active: bool = True
    access_levels: Optional[list[str]] = None
    promotion_type_id: Optional[str] = None


class PromotionCreate(PromotionBase):
    @field_validator("start_date", "end_date", mode="before")
    @classmethod
    def _normalize_promotion_dates(cls, v):
        return normalize_promotion_start_end_optional(v)


class PromotionUpdate(BaseModel):
    description: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    is_active: Optional[bool] = None
    access_levels: Optional[list[str]] = None
    # Retyping a promotion is the one-click fix for a misclassified upload, and
    # the service stamps `promotion_type_source = "manual"` when it arrives. This
    # class does NOT inherit PromotionBase, so a field added there alone would be
    # dropped here and the edit would silently do nothing. Explicit null clears
    # the type (the service reads `model_dump(exclude_unset=True)`, so an omitted
    # key still means "leave it alone").
    promotion_type_id: Optional[str] = None

    @field_validator("start_date", "end_date", mode="before")
    @classmethod
    def _normalize_promotion_dates_optional(cls, v):
        return normalize_promotion_start_end_optional(v)


class FocTier(BaseModel):
    """One buy-N paid units, get M free combination within a group."""

    purchase_quantity: int = Field(ge=1, description="Paid units purchased (e.g. 10)")
    foc_quantity: int = Field(ge=0, description="Free units (e.g. 1)")


class PromotionGroupResponse(BaseModel):
    """FOC / bundle group within a promotion."""

    id: str
    promotion_id: str
    group_name: str
    sort_order: int = 0
    foc_tiers: Optional[list[FocTier]] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    promotion_products: Optional[list["PromotionProductResponse"]] = None

    @field_validator("id", "promotion_id", mode="before")
    @classmethod
    def convert_uuid_to_string(cls, v):
        if v is None:
            return None
        if isinstance(v, uuid.UUID):
            return str(v)
        return str(v)

    class Config:
        from_attributes = True


class PromotionGroupCreate(BaseModel):
    """Create a bundle / FOC group under a promotion."""

    group_name: str
    sort_order: Optional[int] = None
    foc_tiers: Optional[list[FocTier]] = None

    @field_validator("group_name", mode="before")
    @classmethod
    def strip_name(cls, v):
        if v is None:
            return ""
        return str(v).strip()


class PromotionGroupUpdate(BaseModel):
    """Update group name, sort order, or FOC tiers."""

    group_name: Optional[str] = None
    sort_order: Optional[int] = None
    foc_tiers: Optional[list[FocTier]] = None

    @field_validator("group_name", mode="before")
    @classmethod
    def strip_name(cls, v):
        if v is None:
            return None
        s = str(v).strip()
        return s if s else None


class PromotionResponse(PromotionBase):
    id: str
    created_by: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    # "auto" (classified from the file name) or "manual" (a human corrected it).
    promotion_type_source: Optional[str] = None
    promotion_type_code: Optional[str] = None
    promotion_type_name: Optional[str] = None
    products_count: Optional[int] = 0
    products: Optional[list["PromotionProductResponse"]] = None
    promotion_groups: Optional[list["PromotionGroupResponse"]] = None
    attachments: list["PromotionAttachmentResponse"] = []

    @field_serializer("start_date", "end_date")
    def _serialize_promotion_boundary_dates(self, v: Optional[date]) -> Optional[str]:
        return promotion_date_to_api_iso(v) if v is not None else None

    @field_validator('id', 'created_by', mode='before')
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


class PromotionListItemResponse(PromotionBase):
    id: str
    # Multi-company reply clarity: the owning company. ``company_name`` is
    # resolved ONLY when the lookup spanned more than one company
    # (`company_scope.stamp_lookup_companies`), and is null otherwise.
    company_id: Optional[str] = None
    company_name: Optional[str] = None
    created_by: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    products_count: Optional[int] = 0
    attachments: list["PromotionAttachmentResponse"] = []
    # True when the row is not currently live: is_active flag off OR today
    # outside [start_date, end_date]. Lets callers (n8n) answer "found but
    # expired" instead of presenting fallback rows as live promotions.
    is_expired: bool = False
    promotion_type_source: Optional[str] = None
    promotion_type_code: Optional[str] = None
    promotion_type_name: Optional[str] = None
    # Only ever true alongside `is_expired`: the promotion has ended, and its
    # TYPE says a salesman can still honour it (PP / focus item / standard until
    # the end of the year, an A3 flyer for 180 days). Say it has expired but
    # still applies. A `special` is never flagged this way -- it is not served at
    # all once it ends.
    expired_but_usable: bool = False

    @field_serializer("start_date", "end_date")
    def _serialize_promotion_boundary_dates_list(self, v: Optional[date]) -> Optional[str]:
        return promotion_date_to_api_iso(v) if v is not None else None

    @field_validator('id', 'created_by', 'company_id', mode='before')
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


class PromotionProductBase(BaseModel):
    promotion_id: str
    product_id: str
    promo_selling_price: Optional[Decimal] = None
    discount_amount: Optional[Decimal] = None
    discount_percent: Optional[Decimal] = None


class PromotionProductCreate(PromotionProductBase):
    promotion_group_id: Optional[str] = None
    dealer_discount_percent: Optional[Decimal] = None


class PromotionProductUpdate(BaseModel):
    promo_selling_price: Optional[Decimal] = None
    discount_amount: Optional[Decimal] = None
    discount_percent: Optional[Decimal] = None
    dealer_discount_percent: Optional[Decimal] = None
    list_price: Optional[Decimal] = Field(
        default=None,
        description="Updates the linked product master list price; recomputes line discount and dealer fields.",
    )


def _uuid_to_str(v):
    """ORM often returns uuid.UUID; API fields are str for JSON."""
    if v is None:
        return None
    if isinstance(v, uuid.UUID):
        return str(v)
    return str(v)


class ProductSimple(BaseModel):
    """Product reference enriched with traits used by promotion search/answers."""
    id: str
    product_code: str
    product_name: str
    description: Optional[str] = None
    item_type: Optional[str] = None
    list_price: Optional[Decimal] = None
    cost_price: Optional[Decimal] = None
    currency: str = "MYR"
    weight: Optional[Decimal] = None
    dimensions_length: Optional[Decimal] = None
    dimensions_width: Optional[Decimal] = None
    dimensions_height: Optional[Decimal] = None
    warranty_months: Optional[int] = None
    is_active: Optional[bool] = None
    is_discontinued: bool = False
    category_id: Optional[str] = None
    category_code: Optional[str] = None
    category_name: Optional[str] = None
    brand_id: Optional[str] = None
    brand_code: Optional[str] = None
    brand_name: Optional[str] = None

    @field_validator("id", "category_id", "brand_id", mode="before")
    @classmethod
    def _id_uuid(cls, v):
        return _uuid_to_str(v)

    class Config:
        from_attributes = True

    @classmethod
    def model_validate(cls, obj, **kwargs):
        """Flatten category/brand relations onto the response."""
        if hasattr(obj, "product_code") and hasattr(obj, "category_id"):
            data: dict = {}
            for key in (
                "id", "product_code", "product_name", "description", "item_type",
                "list_price", "cost_price", "currency", "weight",
                "dimensions_length", "dimensions_width", "dimensions_height",
                "warranty_months", "is_active", "is_discontinued",
                "category_id", "brand_id",
            ):
                if hasattr(obj, key):
                    data[key] = getattr(obj, key)
            cat = getattr(obj, "category", None)
            if cat is not None:
                data["category_code"] = getattr(cat, "category_code", None)
                data["category_name"] = getattr(cat, "category_name", None)
            brand = getattr(obj, "brand", None)
            if brand is not None:
                data["brand_code"] = getattr(brand, "brand_code", None)
                data["brand_name"] = getattr(brand, "brand_name", None)
            return cls(**data)
        return super().model_validate(obj, **kwargs)


class PromotionSimple(BaseModel):
    """Simple promotion reference."""
    id: str
    is_active: Optional[bool] = None
    description: Optional[str] = None

    @field_validator("id", mode="before")
    @classmethod
    def _id_uuid(cls, v):
        return _uuid_to_str(v)

    class Config:
        from_attributes = True


class PromotionProductResponse(BaseModel):
    id: str
    # Multi-company reply clarity: the owning company. ``company_name`` is
    # resolved ONLY when the lookup spanned more than one company
    # (`company_scope.stamp_lookup_companies`), and is null otherwise.
    company_id: Optional[str] = None
    company_name: Optional[str] = None
    promotion_id: str
    promotion_group_id: Optional[str] = None
    product_id: str
    promotion_price: Optional[Decimal] = None  # Maps from promo_selling_price
    discount_amount: Optional[Decimal] = None
    discount_percent: Optional[Decimal] = None
    dealer_discount_percent: Optional[Decimal] = None
    dealer_cost: Optional[Decimal] = None
    list_to_dealer_margin_amount: Optional[Decimal] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    product: Optional[ProductSimple] = None
    promotion: Optional[PromotionSimple] = None
    promotion_attachments: list["PromotionAttachmentResponse"] = []
    display_order: int = 0  # Default for compatibility
    # True when the PARENT promotion is not currently live: is_active off OR
    # today outside [start_date, end_date]. Mirrors the promotions list so
    # callers (n8n) can answer "found but expired" for fallback / historical rows.
    is_expired: bool = False
    promotion_type_code: Optional[str] = None
    promotion_type_name: Optional[str] = None
    # The parent promotion has ended and its TYPE says it still applies.
    expired_but_usable: bool = False

    @field_validator("id", "promotion_id", "promotion_group_id", "product_id", "company_id", mode="before")
    @classmethod
    def _uuid_id_fields(cls, v):
        """Nested from_attributes (e.g. under promotion_groups) bypasses custom model_validate."""
        return _uuid_to_str(v)

    class Config:
        from_attributes = True

    @classmethod
    def model_validate(cls, obj, **kwargs):
        """Custom validation to map promo_selling_price to promotion_price."""
        # If obj is a SQLAlchemy model, map promo_selling_price to promotion_price
        if hasattr(obj, 'promo_selling_price'):
            # Create a dict with all attributes
            data = {}
            for key in [
                'id', 'promotion_id', 'promotion_group_id', 'product_id', 'discount_amount', 'discount_percent',
                'dealer_discount_percent', 'dealer_cost', 'list_to_dealer_margin_amount',
                'created_at', 'updated_at', 'promotion', 'promotion_attachments', 'is_expired',
                'promotion_type_code', 'promotion_type_name', 'expired_but_usable',
            ]:
                if hasattr(obj, key):
                    value = getattr(obj, key)
                    if key in ('id', 'promotion_id', 'promotion_group_id', 'product_id') and isinstance(
                        value, uuid.UUID
                    ):
                        value = str(value)
                    data[key] = value
            product_obj = getattr(obj, 'product', None)
            if product_obj is not None:
                data['product'] = ProductSimple.model_validate(product_obj)
            # Map promo_selling_price to promotion_price
            data['promotion_price'] = getattr(obj, 'promo_selling_price', None)
            data['display_order'] = getattr(obj, 'display_order', 0)
            return cls(**data)
        return super().model_validate(obj, **kwargs)


class CampaignTypeBase(BaseModel):
    type_code: str
    type_name: str
    description: Optional[str] = None


class CampaignTypeCreate(CampaignTypeBase):
    pass


class CampaignTypeUpdate(BaseModel):
    type_name: Optional[str] = None
    description: Optional[str] = None


class CampaignTypeResponse(CampaignTypeBase):
    id: str
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True


class CampaignTypeSimple(BaseModel):
    id: str
    type_code: str
    type_name: str
    
    class Config:
        from_attributes = True


class MarketingCampaignBase(BaseModel):
    campaign_code: str
    campaign_name: str
    campaign_type_id: str
    description: Optional[str] = None
    start_date: datetime
    end_date: Optional[datetime] = None
    budget: Optional[Decimal] = None
    target_audience: Optional[str] = None
    status: str = "planning"


def _normalize_campaign_status(v):
    """Coerce campaign status to canonical LOWERCASE; reject values off-enum.

    The DB CHECK constraint `marketing_campaigns_status_check` only allows the
    LOWERCASE values planning/active/completed/cancelled. Normalising at the
    input boundary keeps stored data in agreement with the constraint + the FE
    badge/filter maps. None passes through (Update = no change).
    """
    from app.models.marketing import CampaignStatus

    if v is None:
        return v
    low = str(v).strip().lower()
    valid = {s.value for s in CampaignStatus}
    if low not in valid:
        raise ValueError(f"Invalid status '{v}'. Must be one of {sorted(valid)}")
    return low


class MarketingCampaignCreate(MarketingCampaignBase):
    @field_validator("status", mode="before")
    @classmethod
    def _coerce_status(cls, v):
        return _normalize_campaign_status(v)


class MarketingCampaignUpdate(BaseModel):
    campaign_name: Optional[str] = None
    campaign_type_id: Optional[str] = None
    description: Optional[str] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    budget: Optional[Decimal] = None
    target_audience: Optional[str] = None
    status: Optional[str] = None

    @field_validator("status", mode="before")
    @classmethod
    def _coerce_status(cls, v):
        return _normalize_campaign_status(v)


class MarketingCampaignResponse(MarketingCampaignBase):
    id: str
    created_by: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    campaign_type: Optional[CampaignTypeSimple] = None

    @field_validator("id", "campaign_type_id", "created_by", mode="before")
    @classmethod
    def _uuid_to_str(cls, v):
        # created_by may be a UUID object straight off a freshly-created row
        # (current_user["id"]); coerce so the str-typed response validates.
        return str(v) if v is not None else v

    class Config:
        from_attributes = True


# Import Attachment schemas for PromotionAttachment
from app.schemas.resources import AttachmentTypeSimple


class AttachmentSimple(BaseModel):
    """Simple attachment reference for PromotionAttachment."""
    id: str
    original_filename: str
    stored_filename: str
    file_path: str
    file_size_bytes: Optional[int] = None
    mime_type: Optional[str] = None
    uploaded_at: datetime
    created_at: datetime
    attachment_type: Optional[AttachmentTypeSimple] = None
    access_levels: Optional[list[str]] = None
    directory_id: Optional[str] = None  # for "Open in folder" link
    
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


class PromotionAttachmentBase(BaseModel):
    promotion_id: str
    attachment_id: str
    is_primary: Optional[bool] = False
    sort_order: Optional[int] = None


class PromotionAttachmentCreate(PromotionAttachmentBase):
    pass


class PromotionAttachmentUpdate(BaseModel):
    is_primary: Optional[bool] = None
    sort_order: Optional[int] = None


class PromotionAttachmentResponse(PromotionAttachmentBase):
    id: str
    created_at: Optional[datetime] = None
    created_by: Optional[str] = None
    synced_to_excel: Optional[bool] = False
    last_synced_to_excel: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    promotion: Optional[PromotionSimple] = None
    attachment: Optional[AttachmentSimple] = None
    # True when the PARENT promotion is not currently live: is_active off OR
    # today outside [start_date, end_date]. Mirrors the promotions / promotion-
    # products lists so callers (n8n) can answer "found but expired".
    is_expired: bool = False
    promotion_type_code: Optional[str] = None
    promotion_type_name: Optional[str] = None
    # The parent promotion has ended and its TYPE says it still applies.
    expired_but_usable: bool = False

    @field_validator('id', 'promotion_id', 'attachment_id', 'created_by', mode='before')
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


PromotionResponse.model_rebuild()
PromotionListItemResponse.model_rebuild()
PromotionProductResponse.model_rebuild()
