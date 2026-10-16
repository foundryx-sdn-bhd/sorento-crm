"""Sample request write payloads (SR-CORE). Staff and portal share them.

Every field is optional on a draft; the submit rules live in `sample_request_service`.
Responses are plain dicts built by the service (list rows and detail differ on purpose:
list rows never carry the site contact phone or the ship-to address, AC-SR-54).
"""
from __future__ import annotations

from datetime import date as Date
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class SampleRequestLineIn(BaseModel):
    product_id: str
    qty: int = Field(1, ge=1)
    remark: Optional[str] = Field(None, max_length=255)


class SampleRequestWrite(BaseModel):
    customer_id: Optional[str] = None
    customer_name: Optional[str] = Field(None, max_length=255)
    project_id: Optional[str] = None
    project_title: Optional[str] = Field(None, max_length=255)
    scope_note: Optional[str] = Field(None, max_length=255)
    delivery_date: Optional[Date] = None
    delivery_mode: Optional[Literal["driver", "self_deliver", "self_collect"]] = None
    ship_to_name: Optional[str] = Field(None, max_length=255)
    ship_to_address: Optional[str] = None
    site_contact_name: Optional[str] = Field(None, max_length=255)
    site_contact_phone: Optional[str] = Field(None, max_length=60)
    site_contact_note: Optional[str] = Field(None, max_length=255)
    loan_value: Optional[int] = Field(None, ge=1)
    loan_unit: Optional[Literal["days", "months"]] = None
    goods_from: Optional[Literal["demo", "new", "either"]] = None
    purpose: Optional[str] = None
    board_mounting: Optional[bool] = None
    board_label: Optional[str] = Field(None, max_length=255)
    lines: Optional[List[SampleRequestLineIn]] = None


class SampleRequestMove(BaseModel):
    to: str
    date: Optional[Date] = None
    reason: Optional[str] = None


class SampleRequestInternal(BaseModel):
    consignment_no: Optional[str] = Field(None, max_length=80)
    po_no: Optional[str] = Field(None, max_length=80)
