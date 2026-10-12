"""Seat cover <-> WC compatibility (WC-SEAT-COMPAT)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


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
