"""Accessory <-> host compatibility: seat cover/WC, mirror/cabinet, shelf/cabinet."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class ProductCompatibilityCreate(BaseModel):
    other_product_id: str
    #: What the OTHER product is: `accessory` or `host`. `seat_cover` (other is the accessory)
    #: and `wc` (other is the host) are the legacy roles and mean kind `seat_cover`.
    other_role: Literal["accessory", "host", "seat_cover", "wc"]
    kind: Literal["seat_cover", "mirror", "shelf"] = "seat_cover"


class ProductCompatibilityRow(BaseModel):
    id: str
    product_id: str
    product_code: str
    product_name: str
    source: str
    is_discontinued: bool
    kind: str


class ProductCompatibilityList(BaseModel):
    covers: list[ProductCompatibilityRow]
    fits: list[ProductCompatibilityRow]


class ProductCompatibilityListOut(BaseModel):
    data: ProductCompatibilityList


class ProductCompatibilityRowOut(BaseModel):
    data: ProductCompatibilityRow
