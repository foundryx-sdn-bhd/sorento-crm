"""New product application write payloads (NEW-PRODUCT-APP). Staff and portal share them.

Every field is optional on a draft; the submit rules live in
`new_product_application_service`. Number fields accept null (a cleared box is NULL, never 0).
"""
from __future__ import annotations

from datetime import date as Date
from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel, Field


class NewProductApplicationLineIn(BaseModel):
    proposed_code: Optional[str] = Field(None, max_length=255)
    description: Optional[str] = None
    further_description: Optional[str] = None
    category_id: Optional[str] = None
    base_uom_id: Optional[str] = None
    brand_id: Optional[str] = None
    item_type_id: Optional[str] = None
    supplier_id: Optional[str] = None
    supplier_item_code: Optional[str] = Field(None, max_length=100)
    cost_price: Optional[Decimal] = Field(None, ge=0, max_digits=12, decimal_places=2)
    selling_price: Optional[Decimal] = Field(None, ge=0, max_digits=12, decimal_places=2)
    expected_qty: Optional[Decimal] = Field(None, gt=0, max_digits=12, decimal_places=3)
    remark: Optional[str] = None


class NewProductApplicationWrite(BaseModel):
    stock_inquiry_id: Optional[str] = None
    customer_id: Optional[str] = None
    customer_name: Optional[str] = Field(None, max_length=255)
    project_name: Optional[str] = Field(None, max_length=255)
    needed_by: Optional[Date] = None
    remark: Optional[str] = None
    lines: Optional[List[NewProductApplicationLineIn]] = Field(None, max_length=100)


class ReasonIn(BaseModel):
    reason: Optional[str] = None


class LinkIn(BaseModel):
    product_id: str
