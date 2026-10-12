"""Seat cover <-> WC compatibility (WC-SEAT-COMPAT)."""
from __future__ import annotations

from typing import Annotated, Literal, Optional

from pydantic import BaseModel, Field, StringConstraints

from app.services.uuid_path_param import UUID_PATTERN


class ProductCompatibilityCreate(BaseModel):
    other_product_id: str
    #: What the OTHER product is. `seat_cover`: this page is the WC.
    other_role: Literal["seat_cover", "wc"]


class ProductCompatibilityRow(BaseModel):
    id: str
    product_id: str
    product_code: str
    product_name: str
    source: str
    is_discontinued: bool


class ProductCompatibilityList(BaseModel):
    covers: list[ProductCompatibilityRow]
    fits: list[ProductCompatibilityRow]


class ProductCompatibilityListOut(BaseModel):
    data: ProductCompatibilityList


class ProductCompatibilityRowOut(BaseModel):
    data: ProductCompatibilityRow


class FlyerCover(BaseModel):
    code: str
    found: bool


class FlyerPrinted(BaseModel):
    flyer_row: int
    wc_code: str
    covers: list[FlyerCover]


class FlyerSku(BaseModel):
    product_id: str
    product_code: str
    product_name: str
    company_name: Optional[str] = None
    is_active: bool
    #: ready | linked | unresolved | cover_missing
    status: str
    #: For `unresolved`: not_printed | ambiguous | finish_not_printed | sub_model_not_printed
    reason: Optional[str] = None
    printed_code: Optional[str] = None
    flyer_row: Optional[int] = None
    cover_codes: list[str]
    extra_segments: list[str]


class FlyerModel(BaseModel):
    family_code: str
    printed: list[FlyerPrinted]
    skus: list[FlyerSku]


class FlyerCounts(BaseModel):
    models: int
    models_missing: int
    covers_missing: int
    ready: int
    linked: int
    unresolved: int
    cover_missing: int


class SeatCoverSpecState(BaseModel):
    registry_id: str
    is_active: bool
    value_count: int
    can_retire: bool


class FlyerReview(BaseModel):
    models: list[FlyerModel]
    counts: FlyerCounts
    seat_cover_spec: Optional[SeatCoverSpecState] = None


class FlyerReviewOut(BaseModel):
    data: FlyerReview


class FlyerApply(BaseModel):
    # Bounded: the flyer's models hold a few hundred SKUs at most.
    wc_product_ids: list[Annotated[str, StringConstraints(pattern=UUID_PATTERN)]] = Field(
        max_length=2000
    )


class FlyerSkipped(BaseModel):
    product_id: str
    reason: str


class FlyerApplyResult(BaseModel):
    linked: int
    already_linked: int
    skipped: list[FlyerSkipped]


class FlyerApplyOut(BaseModel):
    data: FlyerApplyResult
