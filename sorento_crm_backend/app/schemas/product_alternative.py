"""Product alternatives on the product page (PRODUCT-ALTERNATIVES, card Q5 (a))."""
from __future__ import annotations

from pydantic import BaseModel


class ProductAlternativeCreate(BaseModel):
    alternative_product_id: str


class ProductAlternativeRow(BaseModel):
    id: str
    alternative_product_id: str
    product_code: str
    product_name: str
    source: str
    is_discontinued: bool


class ProductAlternativeList(BaseModel):
    alternatives_manual: bool
    alternatives: list[ProductAlternativeRow]


class ProductAlternativeListOut(BaseModel):
    data: ProductAlternativeList


class ProductAlternativeRowOut(BaseModel):
    data: ProductAlternativeRow
