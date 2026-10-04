"""External procurement schemas."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, List, Optional, Union

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.regions import normalize_regions
from app.schemas.procurement import (
    InboundShipmentResponse,
    refuse_missing_sponsorship_unit_prices,
)

DateType = date


class PackingListHeader(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    shipment_number: Optional[str] = None
    supplier_id: Optional[str] = None
    shipment_date: str | date
    shipping_container_number: Optional[str] = None
    attachment_id: str
    estimated_arrival_date: Optional[str | date] = Field(
        default=None,
        validation_alias=AliasChoices("estimated_arrival_date", "eta", "expected_arrival_date"),
    )
    actual_arrival_date: Optional[str | date] = None
    bill_of_lading_number: Optional[str] = None
    invoice_number: Optional[str] = None
    shipment_status: Optional[str] = None
    total_items_shipped: Optional[int] = None
    total_cartons: Optional[int] = None
    notes: Optional[str] = None
    #: 'west' / 'east'. Precedence on create: this, else the attachment's, else West only.
    regions: Optional[List[str]] = None

    @field_validator("regions")
    @classmethod
    def _check_regions(cls, v):
        return normalize_regions(v)


class PackingListProduct(BaseModel):
    product_code: str
    quantity: int


class PackingListRequest(BaseModel):
    packing_list: PackingListHeader
    packing_list_products: List[PackingListProduct]
    notify_user_id: Optional[str] = None


class PackingListCreateResponse(BaseModel):
    shipment: InboundShipmentResponse
    skipped_product_codes: List[str] = []
    # TCK-2026-000019: alias for skipped_product_codes - explicit "unknown in master data"
    # signal that n8n and other consumers can read without diving into log warnings.
    unknown_product_codes: List[str] = []
    already_existed: bool = False
    #: Set when this update left the shipment's LINES alone (AC-D4b): a converted draft's
    #: lines come from its proforma invoices, and the forwarder's own upload updates the
    #: header only. Null on every other call, including a fresh create.
    lines_skipped_reason: Optional[str] = None
    message: Optional[str] = None


class SPOAllocationItem(BaseModel):
    spo_number: Optional[str] = None
    spo_line_number: Optional[int] = None
    inbound_shipment_id: Optional[str] = None
    shipping_container_number: Optional[str] = None
    warehouse_id: Optional[str] = None
    location: Optional[str] = None
    storage_zone_id: Optional[str] = None
    allocated_quantity: Optional[int] = None
    quantity: Optional[int] = None
    uom_id: Optional[str] = None
    receipt_status: Optional[str] = None
    quantity_received: Optional[int] = None
    quantity_rejected: Optional[int] = None
    allocation_notes: Optional[str] = None
    product_code: str

    @field_validator("spo_line_number", "quantity", "allocated_quantity", mode="before")
    @classmethod
    def coerce_int(cls, v: Any) -> Optional[int]:
        if v is None:
            return None
        if isinstance(v, str):
            s = v.strip()
            return int(s) if s else None
        return int(v) if v is not None else None


class SPOAllocationRequest(BaseModel):
    spo_allocations: List[SPOAllocationItem]


class GRNHeader(BaseModel):
    picking_number: str
    picking_type: Optional[str] = None
    picking_date: Optional[str | date] = None
    notes: Optional[str] = None


class GRNLine(BaseModel):
    spo_allocation: Optional[str] = None
    spo_allocation_line: Optional[int] = None
    warehouse_id: Optional[str] = None
    location: Optional[str] = None
    product_code: str
    quantity: int
    uom_id: Optional[str] = None

    @field_validator("spo_allocation_line", "quantity", mode="before")
    @classmethod
    def coerce_int(cls, v: Any) -> Any:
        if v is None:
            return None
        if isinstance(v, str):
            s = v.strip()
            return int(s) if s else None
        return int(v) if v is not None else None


class GRNRequest(BaseModel):
    goods_receive_notes: GRNHeader
    grn_lines: List[GRNLine]


class PurchaseRequestExternalLine(BaseModel):
    item_code: Optional[str] = None
    quantity: Optional[Decimal] = None
    remark: Optional[str] = None
    unit_price: Optional[Decimal] = None
    total: Optional[Decimal] = None

    @field_validator("quantity", mode="before")
    @classmethod
    def coerce_quantity(cls, v: Any) -> Optional[Decimal]:
        if v is None:
            return None
        if isinstance(v, str):
            s = v.strip()
            return Decimal(s) if s else None
        return Decimal(v)

    @field_validator("unit_price", "total", mode="before")
    @classmethod
    def coerce_decimal(cls, v: Any) -> Optional[Decimal]:
        if v is None:
            return None
        if isinstance(v, str):
            s = v.strip()
            return Decimal(s) if s else None
        return Decimal(v)


class PurchaseRequestExternalCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    request_type: str
    request_number: Optional[str] = None
    date: Optional[str | DateType] = None
    customer_name: Optional[str] = None
    project_title: Optional[str] = None
    purpose: Optional[str] = None
    delivery_address: Optional[str] = None
    total_project_value: Optional[Union[Decimal, str]] = None
    sponsor_subject: Optional[str] = None
    sponsor_subject_other: Optional[str] = None
    expected_delivery_date: Optional[str | DateType] = None
    expected_po_date: Optional[str | DateType] = None
    date_of_delivery: Optional[str | DateType] = None
    products: List[PurchaseRequestExternalLine] = []
    requested_by: Optional[str] = None
    requested_at: Optional[str | DateType] = None
    external_reference: Optional[str] = None
    contact_id: str
    space_id: str
    base_url: Optional[str] = None
    approval_status: Optional[str] = None
    user_confirmed: Optional[bool] = Field(
        default=None,
        description=(
            "Must be true after the end user explicitly confirms the final summary "
            "(e.g. OK, YES, CONFIRM) before this request is submitted."
        ),
    )

    @field_validator("request_type")
    @classmethod
    def validate_request_type(cls, v: str) -> str:
        normalized = (v or "").strip()
        if normalized not in {"purchase_request", "sponsorship_form"}:
            raise ValueError("request_type must be purchase_request or sponsorship_form")
        return normalized

    @field_validator("contact_id", "space_id", mode="before")
    @classmethod
    def validate_non_empty_scope_ids(cls, v: Any, info) -> str:
        s = str(v or "").strip()
        if not s:
            raise ValueError(f"{info.field_name} is required")
        return s

    @field_validator("approval_status", mode="before")
    @classmethod
    def validate_approval_status(cls, v: Any) -> Optional[str]:
        if v is None:
            return None
        if isinstance(v, str) and not v.strip():
            return None
        s = str(v).strip().lower()
        allowed = {"pending", "approved", "rejected", "draft"}
        if s not in allowed:
            raise ValueError(
                "approval_status must be one of: pending, approved, rejected, draft"
            )
        if s == "draft":
            return None
        return s

    @model_validator(mode="after")
    def _require_sponsorship_unit_prices(self) -> "PurchaseRequestExternalCreate":
        refuse_missing_sponsorship_unit_prices(self.request_type, self.products)
        return self


class PurchaseRequestExternalResponse(BaseModel):
    id: str
    system_id: Optional[str] = None
    form_type: Optional[str] = None
    request_type: str
    request_number: Optional[str] = None
    action: str = "created"
    date: Optional[DateType] = None
    customer_name: Optional[str] = None
    project_title: Optional[str] = None
    purpose: Optional[str] = None
    delivery_address: Optional[str] = None
    total_project_value: Optional[Decimal] = None
    total_project_value_text: Optional[str] = None
    sponsor_subject: Optional[str] = None
    sponsor_subject_other: Optional[str] = None
    expected_delivery_date: Optional[DateType] = None
    expected_po_date: Optional[str | DateType] = None
    products: List[PurchaseRequestExternalLine] = []
    requested_by: Optional[str] = None
    requested_at: Optional[DateType] = None
    grand_total: Optional[Decimal] = None
    already_existed: bool = False
    message: Optional[str] = None
    view_url: Optional[str] = None
    respond_inbox_url: Optional[str] = None
    approval_status: Optional[str] = None

