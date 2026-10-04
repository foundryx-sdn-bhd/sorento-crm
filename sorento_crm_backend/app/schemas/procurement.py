"""Procurement schemas."""
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from typing import Optional, List
from datetime import datetime, date
from decimal import Decimal
import uuid
from app.schemas.regions import normalize_regions
from app.schemas.resources import AttachmentTypeSimple
from app.services.error_handler import AppException


def _validate_uuid_format(v: Optional[str]) -> Optional[str]:
    """N2: a malformed `country_id` is a 422 at the schema boundary, never a raw FK
    violation or a 500 from a query that could not cast it. Validates the FORMAT only and
    returns the string unchanged - `SupplierResponse` inherits `SupplierBase`, and a type
    change to `uuid.UUID` here would make its own `country_id` a `UUID` object that no
    longer compares equal to the ORM's plain string id.
    """
    if v is None:
        return v
    try:
        uuid.UUID(str(v))
    except (ValueError, AttributeError, TypeError):
        raise ValueError("country_id must be a valid UUID")
    return v


class SupplierBase(BaseModel):
    supplier_code: str
    supplier_name: str
    contact_name: Optional[str] = None
    email: Optional[str] = None
    phone_number: Optional[str] = None
    website: Optional[str] = None
    address_line1: Optional[str] = None
    address_line2: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    postal_code: Optional[str] = None
    # S2 (`PLAN-local-supplier-oi-routing.md`): replaces the free-text `country`.
    # 422 (never a raw FK violation) on an id `SupplierService` cannot resolve.
    country_id: Optional[str] = None
    payment_terms_days: Optional[int] = 30
    is_active: bool = True

    _validate_country_id = field_validator("country_id")(_validate_uuid_format)


class SupplierCreate(SupplierBase):
    pass


class SupplierUpdate(BaseModel):
    supplier_name: Optional[str] = None
    contact_name: Optional[str] = None
    email: Optional[str] = None
    phone_number: Optional[str] = None
    website: Optional[str] = None
    address_line1: Optional[str] = None
    address_line2: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    postal_code: Optional[str] = None
    country_id: Optional[str] = None
    payment_terms_days: Optional[int] = None
    is_active: Optional[bool] = None

    _validate_country_id = field_validator("country_id")(_validate_uuid_format)


class SupplierSimple(BaseModel):
    id: str
    supplier_code: str
    supplier_name: str
    
    class Config:
        from_attributes = True


class SupplierResponse(SupplierBase):
    id: str
    created_at: datetime
    updated_at: Optional[datetime] = None
    # Read-only, joined off `country_id` (`Supplier.country_code`/`country_name`
    # properties) - the client never computes or sends these back.
    country_code: Optional[str] = None
    country_name: Optional[str] = None

    class Config:
        from_attributes = True


class ProductSimple(BaseModel):
    """Simple product reference."""
    id: str
    product_code: str
    product_name: str
    is_discontinued: bool = False

    class Config:
        from_attributes = True


class PackingListLineSPOSimple(BaseModel):
    id: str
    spo_number: Optional[str] = None
    allocated_quantity: Optional[int] = None
    receipt_status: Optional[str] = None

    class Config:
        from_attributes = True


class PackingListLineGRNSimple(BaseModel):
    id: str
    picking_number: Optional[str] = None
    spo_number: Optional[str] = None
    picking_status: Optional[str] = None
    picking_date: Optional[date] = None

    class Config:
        from_attributes = True


def _normalize_currency_code(value: Optional[str]) -> Optional[str]:
    """Trim and upper-case a currency code; blank stays None (nothing on file)."""
    if value is None:
        return None
    code = value.strip().upper()
    return code or None


class ProductSupplierSourcingTerms(BaseModel):
    """What we buy this product from this supplier ON: the price, the money that price is
    in, and the quantities the supplier will accept.

    These live on the link rather than on the product because they are terms of a
    relationship - two suppliers quote different prices, in different currencies, with
    different minimums. The reorder plan reads every one of them, so a blank here is the
    difference between a buy the buyer can act on and one it can only describe.
    """

    moq: Optional[int] = Field(default=None, ge=0)
    order_multiple: Optional[int] = Field(default=None, ge=1)
    unit_cost: Optional[Decimal] = Field(default=None, ge=0)
    # No `max_length` here: it would run BEFORE the validator and reject " cny " for its
    # padding rather than trimming it. The validator owns the whole rule.
    currency: Optional[str] = None
    is_primary_supplier: Optional[bool] = None

    @field_validator("currency")
    @classmethod
    def _currency_code(cls, v: Optional[str]) -> Optional[str]:
        code = _normalize_currency_code(v)
        if code is not None and (len(code) != 3 or not code.isalpha()):
            raise ValueError("Currency must be a 3-letter code, for example MYR or CNY.")
        return code


class ProductSupplierBase(ProductSupplierSourcingTerms):
    product_id: str
    supplier_id: str
    # Required, because the column is NOT NULL with no default. Declaring it optional
    # turned a missing required field into a 500 at INSERT time; a caller that omits it
    # now gets a 422 that says which field.
    standard_lead_time_days: int = Field(ge=0)


class ProductSupplierCreate(ProductSupplierBase):
    pass


class ProductSupplierUpdate(ProductSupplierSourcingTerms):
    standard_lead_time_days: Optional[int] = None


class ProductSupplierCostCreate(BaseModel):
    """A hand-added cost list row (#1288, AC-CL-06). Typed so a missing currency or a text
    price is a 422, not a 500 from the database (Should fix 2 of the review at 232e5706)."""
    unit_cost: Decimal = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3)
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    # Round 8: the packaging this cost is for, free text; empty or absent is `standard`.
    packaging_method: Optional[str] = Field(default=None, max_length=255)


class ProductSupplierCostUpdate(BaseModel):
    """A partial edit: only the fields sent change; a date sent as null clears it."""
    unit_cost: Optional[Decimal] = Field(default=None, ge=0)
    currency: Optional[str] = Field(default=None, min_length=3, max_length=3)
    start_date: Optional[date] = None
    end_date: Optional[date] = None

    @model_validator(mode="after")
    def _price_and_currency_not_null(self):
        for name in ("unit_cost", "currency"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} cannot be empty")
        return self


class ProductSupplierResponse(ProductSupplierBase):
    id: str
    created_at: datetime
    # A link the cost upload created may have no lead time (#1288 round 6, R3).
    standard_lead_time_days: Optional[int] = None
    # Read off `scm.supplier_product_code_alias` (product + supplier, non-dismissed), not a
    # column on this table (S4, AC-D2): the alias is the single writer, so a manual match and
    # this field can never drift apart. Declared on the RESPONSE only - a create or update
    # payload naming it would be dropped in silence, since nothing here writes it back.
    supplier_item_code: Optional[str] = None
    product: Optional[ProductSimple] = None
    supplier: Optional[SupplierSimple] = None

    class Config:
        from_attributes = True


class InboundShipmentLineBase(BaseModel):
    product_id: str
    quantity_shipped: int
    # Whose line this is, when the caller knows. Left unset it falls back to the header's
    # supplier at write time, so an existing single-supplier payload is unchanged.
    supplier_id: Optional[str] = None
    uom_id: Optional[str] = None
    batch_number: Optional[str] = None
    serial_number_range_from: Optional[str] = None
    serial_number_range_to: Optional[str] = None
    carton_number: Optional[str] = None
    cartons_count: int = 1
    weight_per_carton: Optional[Decimal] = None
    unit_cost: Optional[Decimal] = None
    # What `unit_cost` is stated in. The column has existed since S3b; the packing-list
    # upload had no way to fill it, so a price parsed out of the file could only be stored
    # as a number with no meaning. Optional and never defaulted (AC-P5.1).
    #
    # On the BASE, not on `...Create` alone, so it travels back out with the price it
    # denominates: a read that returns `unit_cost` and no currency hands its caller the same
    # meaningless number the write path exists to prevent.
    currency: Optional[str] = None
    # Volume as the packing list stated it, and the supplier's own note on the line.
    cbm: Optional[Decimal] = None
    remarks: Optional[str] = None
    # The supplier's own wording for the item (migration 466, S9). Copied off the proforma
    # invoice line at convert; editable in the lines grid afterwards. Product name is the
    # reader's fallback when this is unset, never a value written here.
    description: Optional[str] = None
    # What the container workbook measures the line by (AC-F3.5). Editable on the packing
    # list, because the supplier's file is where they come from and it is not always right.
    # Lengths in centimetres, weights per CARTON - the sheet multiplies them by the carton
    # count itself rather than storing a line total that could disagree with its inputs.
    material: Optional[str] = None
    pcs_per_carton: Optional[Decimal] = None
    carton_length_cm: Optional[Decimal] = None
    carton_width_cm: Optional[Decimal] = None
    carton_height_cm: Optional[Decimal] = None
    net_weight_per_carton: Optional[Decimal] = None
    gross_weight_per_carton: Optional[Decimal] = None


class InboundShipmentLineCreate(InboundShipmentLineBase):
    # An EDIT names which existing line it means (S4, AC-D4) - two lines can now share
    # every other field (Kailu's own carton split), and only the id tells them apart.
    # Absent on a genuinely new line; `_upsert_shipment_lines` falls back to its
    # (product, supplier) matching when it is not given or does not resolve.
    id: Optional[str] = None


class InboundShipmentLineResponse(InboundShipmentLineBase):
    id: str
    shipment_id: str
    created_at: datetime
    updated_at: Optional[datetime] = None
    product: Optional[ProductSimple] = None
    spo_allocated_quantity: Optional[int] = None  # Total SPO allocated qty for this product on this shipment (all warehouses)
    quantity_received: Optional[int] = None  # Sum of quantity_received from SPO allocations for this line
    line_status: Optional[str] = None  # Stored in DB: in_transit, allocated, partially_allocated, received, partially_received (for n8n/API)
    related_spo_allocations: Optional[List[PackingListLineSPOSimple]] = None
    related_grns: Optional[List[PackingListLineGRNSimple]] = None

    class Config:
        from_attributes = True


class ClearanceFields(BaseModel):
    """Container status fields carried on every shipment payload.

    Mirrors `ClearanceFields` in
    packing-lists/types/packingList.types.ts one-for-one; a pytest parity check
    (tests/test_container_status_schema.py) fails if the two drift, because a
    field missing on one side does not error - it just never reaches the UI.

    Inherited by `InboundShipmentBase`, so the list response, the detail response
    and the create payload all get them from one place rather than from three
    hand-maintained field lists.
    """

    loc: Optional[str] = None
    liner_code: Optional[str] = None
    china_forwarder: Optional[str] = None
    malaysia_forwarder: Optional[str] = None
    consignee: Optional[str] = None
    free_days_available: Optional[int] = None
    stacked: Optional[str] = None

    loading_date: Optional[date] = None
    etc_date: Optional[date] = None
    etd_date: Optional[date] = None
    eta_delay_date: Optional[date] = None
    inspection_date: Optional[date] = None
    approval_date: Optional[date] = None
    gatepass_date: Optional[date] = None
    delivery_warehouse: Optional[str] = None
    warehouse_arrival_date: Optional[date] = None
    informed_collection_date: Optional[date] = None
    collection_date: Optional[date] = None

    coa_permit_no: Optional[str] = None
    source_sheet: Optional[str] = None


class ContainerWorkbookFields(BaseModel):
    """The container's own header lines and its costs, as the workbook prints them.

    Deliberately NOT on `ClearanceFields`: that interface is mirrored one-for-one by the
    frontend's and pinned by `tests/test_container_status_schema.py`, so a field added there
    that the Container Status sheet does not contribute would fail a parity check that is
    about a different document entirely.

    One class rather than two hand-kept field lists, because `InboundShipmentUpdate` does
    NOT inherit `InboundShipmentBase`: a field added to one and forgotten on the other is
    accepted by the PUT and silently dropped (`update_shipment` setattrs whatever
    `exclude_unset` yields), which reads as a save that did not work.
    """

    #: Printed as `SEAL NO :` and again as `封号:` in the footer.
    seal_number: Optional[str] = None
    #: Who shipped it, which is not always the factory that made it.
    shipper: Optional[str] = None
    #: The forwarder's booking reference (`SO :` on the sheet, `订单号:` in the footer).
    #: Named for what it is - "SO" in this codebase is a sales order.
    forwarder_order_ref: Optional[str] = None

    #: Typed per container. The footer splits clearance and China freight by each company's
    #: share of the volume, and insurance by its share of the amount.
    clearance_cost: Optional[Decimal] = None
    china_freight_cost: Optional[Decimal] = None
    insurance_rate: Optional[Decimal] = None


class InboundShipmentBase(ClearanceFields, ContainerWorkbookFields):
    shipment_number: Optional[str] = None
    supplier_id: Optional[str] = None
    shipment_date: date
    estimated_arrival_date: Optional[date] = None
    actual_arrival_date: Optional[date] = None
    bill_of_lading_number: Optional[str] = None
    shipping_container_number: Optional[str] = None
    invoice_number: Optional[str] = None
    shipment_status: str = "in_transit"
    total_items_shipped: Optional[int] = None
    total_cartons: Optional[int] = None
    notes: Optional[str] = None
    attachment_id: Optional[str] = None
    #: Which box this container is being loaded into (S5, migration 465). Capacity is a
    #: property of the CONTAINER, not any one proforma invoice that fed it - this used to
    #: live on `scm.proforma_invoice`, one level too low for a packing list that routinely
    #: consolidates several PIs. Null means the tenant's default size.
    container_size_id: Optional[str] = None
    #: 'west' / 'east'. Omitted on create = the column default (West only).
    regions: Optional[List[str]] = None

    @field_validator("regions")
    @classmethod
    def _check_regions(cls, v):
        return normalize_regions(v)


class InboundShipmentCreate(InboundShipmentBase):
    shipment_lines: Optional[List[InboundShipmentLineCreate]] = None


class InboundShipmentUpdate(ClearanceFields, ContainerWorkbookFields):
    """Everything editable on a packing list, INCLUDING the clearance fields.

    They are here because the workbook is not the only way these dates arrive:
    when the import has not run, or a liner publishes a revision between imports,
    someone has to be able to type the date in. Without them on this schema the
    PUT accepted the payload and silently dropped it - `update_shipment` setattrs
    whatever `exclude_unset` yields, so a field absent from the schema never
    reaches the row and the save looks successful.

    `extra="forbid"`: a mistyped or renamed key used to pass straight through as an
    unknown-but-ignored field and the save looked successful with nothing written - the
    same silent-drop failure mode `shipment_number` itself used to hit before it was
    declared here. Now a typo 422s loudly instead.
    """

    model_config = ConfigDict(extra="forbid")

    shipment_number: Optional[str] = None
    supplier_id: Optional[str] = None
    shipment_date: Optional[date] = None
    estimated_arrival_date: Optional[date] = None
    actual_arrival_date: Optional[date] = None
    bill_of_lading_number: Optional[str] = None
    shipping_container_number: Optional[str] = None
    invoice_number: Optional[str] = None
    shipment_status: Optional[str] = None
    total_items_shipped: Optional[int] = None
    total_cartons: Optional[int] = None
    notes: Optional[str] = None
    attachment_id: Optional[str] = None
    #: The Container card's own select (S5). Null means the tenant's default size, the same
    #: convention the field carries on `InboundShipmentBase`.
    container_size_id: Optional[str] = None
    #: Omitted = unchanged. Never nullable: an explicit null is dropped by `update_shipment`.
    regions: Optional[List[str]] = None
    shipment_lines: Optional[List[InboundShipmentLineCreate]] = None

    @field_validator("regions")
    @classmethod
    def _check_regions(cls, v):
        return normalize_regions(v)


class AttachmentSimple(BaseModel):
    """Simple attachment reference for InboundShipment."""
    id: str
    original_filename: str
    stored_filename: str
    file_path: str
    file_size_bytes: Optional[int] = None
    mime_type: Optional[str] = None
    attachment_type: Optional[AttachmentTypeSimple] = None
    
    class Config:
        from_attributes = True


class InboundShipmentResponse(InboundShipmentBase):
    id: str
    created_at: datetime
    updated_at: datetime
    created_by: Optional[str] = None
    synced_to_excel: bool = False
    last_synced_to_excel: Optional[datetime] = None
    supplier: Optional[SupplierSimple] = None
    attachment: Optional[AttachmentSimple] = None
    shipment_lines: Optional[List[InboundShipmentLineResponse]] = None
    lines_count: Optional[int] = 0
    spo_allocations_count: Optional[int] = 0
    display_total_items: Optional[int] = None
    display_total_cartons: Optional[int] = None
    # The fill gauge (S5, ruling 1) - computed onto the shipment ORM object by
    # `InboundShipmentService._attach_capacity`, never stored. `container_size_id` above is
    # the operator's choice (or null = the tenant default); these are what it resolves to.
    container_size_code: Optional[str] = None
    container_cbm: Optional[float] = None
    total_cbm: Optional[float] = None
    unmeasured_lines: int = 0
    fill_pct: Optional[float] = None
    over_by_cbm: Optional[float] = None

    @field_validator('created_by', mode='before')
    @classmethod
    def convert_created_by_uuid(cls, v):
        """Convert UUID objects to strings for created_by."""
        if v is None:
            return None
        if isinstance(v, uuid.UUID):
            return str(v)
        return str(v) if v else None
    
    class Config:
        from_attributes = True


class InboundShipmentListItemResponse(InboundShipmentBase):
    id: str
    created_at: datetime
    updated_at: datetime
    created_by: Optional[str] = None
    synced_to_excel: bool = False
    last_synced_to_excel: Optional[datetime] = None
    supplier: Optional[SupplierSimple] = None
    lines_count: Optional[int] = 0
    spo_allocations_count: Optional[int] = 0
    display_total_items: Optional[int] = None
    display_total_cartons: Optional[int] = None

    @field_validator('created_by', mode='before')
    @classmethod
    def convert_created_by_uuid(cls, v):
        """Convert UUID objects to strings for created_by."""
        if v is None:
            return None
        if isinstance(v, uuid.UUID):
            return str(v)
        return str(v) if v else None

    class Config:
        from_attributes = True


class SPOAllocationBase(BaseModel):
    spo_number: Optional[str] = None
    spo_line_number: Optional[int] = None
    # Both optional since migration 420: an imported shipping order has no shipment booked
    # yet, and 6,520 of the captain's SPO lines name a stock location we do not hold. The
    # raw code is carried in `location_code` so the destination is never simply lost.
    inbound_shipment_id: Optional[str] = None
    warehouse_id: Optional[str] = None
    location_code: Optional[str] = None
    storage_zone_id: Optional[str] = None
    allocated_quantity: int
    uom_id: Optional[str] = None
    receipt_status: str = "pending"
    quantity_received: int = 0
    quantity_rejected: int = 0
    allocation_notes: Optional[str] = None
    product_id: str
    # D6 (S3): AutoCount's `PO.Ref` / the SPO xlsx Loading Date cell, already
    # cleaned (`shipping_order_rules.extract_container_number`) by the caller.
    # `max_length` matches `spo_allocations.container_number VARCHAR(100)`
    # (security review advisory b) - unbounded otherwise, unlike every other
    # writer of this column.
    container_number: Optional[str] = Field(None, max_length=100)
    # Section 7 currency gap (S3): absent on every SPO xlsx row today, unlike
    # the PO side which has carried it since the outstanding upload's own
    # `DEFAULT_PO_CURRENCY` fill - added so the two writers' parity test can
    # stop excluding this column. `max_length=3` (security review advisory
    # b) - an ISO 4217 code, same width as every other currency column.
    currency: Optional[str] = Field(None, max_length=3)


class SPOAllocationCreate(SPOAllocationBase):
    # Which Supply PO line this allocation draws down (PO -> SPO -> GRN). The model column
    # exists; without it on the create schema the allocation is written with no link to the
    # ordered line, so the incoming cost has no currency to resolve from and no ordered cost
    # to be compared against (AC-C3.2).
    # Optional: 860 pre-existing allocations have no PO, and stock can arrive against none.
    po_line_id: Optional[str] = None
    # REQUIRED on the create path, where the base relaxed them. Only an IMPORTED shipping
    # order legitimately has no shipment or no warehouse (migration 420, and the import
    # writes those rows directly); somebody allocating a container through the API or the
    # screen is naming both, and accepting a blank would write a row that is supply nowhere
    # and belongs to no shipment, silently.
    inbound_shipment_id: str
    warehouse_id: str


class SPOAllocationUpdate(BaseModel):
    spo_number: Optional[str] = None
    inbound_shipment_id: Optional[str] = None
    warehouse_id: Optional[str] = None
    product_id: Optional[str] = None
    storage_zone_id: Optional[str] = None
    allocated_quantity: Optional[int] = None
    receipt_status: Optional[str] = None
    quantity_received: Optional[int] = None
    quantity_rejected: Optional[int] = None
    allocation_notes: Optional[str] = None
    # The document form view's Lines-tab editors (PLAN-spo-investigation-grid.md UAT
    # batch, AC-24 parts 2/3): the line's OWN ETA fallback and its OWN supplier,
    # never the shipment's - those stay read-only, booked against the shipment record.
    expected_date: Optional[date] = None
    supplier_id: Optional[str] = None


class BulkDeleteSPOAllocationsRequest(BaseModel):
    ids: List[str]


class BulkDeletePurchaseRequestsRequest(BaseModel):
    """Request body for bulk delete of purchase requests / sponsorship forms."""
    ids: List[str]


class InboundShipmentSimple(BaseModel):
    id: str
    shipment_number: Optional[str] = None
    shipping_container_number: Optional[str] = None

    class Config:
        from_attributes = True


class WarehouseSimple(BaseModel):
    id: str
    warehouse_code: str
    warehouse_name: str
    
    class Config:
        from_attributes = True


class SPOAllocationSimple(BaseModel):
    """Minimal SPO allocation for embedding in picking line responses."""
    id: str
    spo_number: Optional[str] = None
    spo_line_number: Optional[int] = None

    class Config:
        from_attributes = True


class LinkedGRNSimple(BaseModel):
    """Minimal GRN info for SPO → GRN navigation."""
    id: str
    picking_number: Optional[str] = None
    picking_status: Optional[str] = None
    picking_date: Optional[date] = None

    class Config:
        from_attributes = True


class SPOAllocationResponse(SPOAllocationBase):
    id: str
    # The document half, since the SPO itself lives in this table (migration 420). Declared
    # here or `response_model` drops them on the way out however faithfully the service
    # reads them.
    source_system: Optional[str] = None
    line_status: Optional[str] = None
    issue_date: Optional[date] = None
    expected_date: Optional[date] = None
    supplier_id: Optional[str] = None
    unit_cost: Optional[Decimal] = None
    currency: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    created_by: Optional[str] = None
    synced_to_excel: bool = False
    last_synced_to_excel: Optional[datetime] = None
    inbound_shipment: Optional[InboundShipmentSimple] = None
    warehouse: Optional[WarehouseSimple] = None
    product: Optional[ProductSimple] = None
    grn_lines_count: Optional[int] = 0
    linked_grns: Optional[List["LinkedGRNSimple"]] = None
    
    @field_validator('created_by', mode='before')
    @classmethod
    def convert_created_by_uuid(cls, v):
        """Convert UUID objects to strings for created_by."""
        if v is None:
            return None
        if isinstance(v, uuid.UUID):
            return str(v)
        return str(v) if v else None
    
    class Config:
        from_attributes = True


class ShipmentAllocationSummaryGroup(BaseModel):
    """Inbound shipment summary for grouped shipment search."""
    inbound_shipment: InboundShipmentSimple
    matched_spo_allocations_count: int = 0
    shipment_lines_count: int = 0


class SPOAllocationWithShippedResponse(SPOAllocationResponse):
    """SPO allocation with quantity_shipped from packing list (shipment lines)."""
    quantity_shipped: Optional[int] = None


class SPOWithAllocationsGroup(BaseModel):
    """SPO number with its allocations (grouped list view by SPO)."""
    spo_number: str
    spo_allocations: List[SPOAllocationWithShippedResponse]


# ---------------------------------------------------------------------------------------
# SPO document list + form view (PLAN-spo-investigation-grid.md). A DOCUMENT read of
# `spo_allocations`, grouped by `spo_number` at read time - no new table. Every computed
# field here is declared explicitly: `response_model` silently drops an undeclared one
# (LESSONS-LEARNT), and each is asserted present by a drop-guard test (AC-13).
# ---------------------------------------------------------------------------------------


class SPODocumentLinePO(BaseModel):
    """The purchase-order line an SPO line PULLED from, via `po_line_id` (R23, AC-J2)."""
    po_number: str
    purchase_order_id: str
    #: Always null - `purchase_order_lines` carries no per-line ordinal to name.
    line_no: Optional[int] = None


class SPODocumentLineSOCovered(BaseModel):
    """One sales order an allocation covers - `order_inquiry_links` (project) or
    `scm.order_link_claim` (`source='planner'`, retail) joined on the allocation (R23,
    AC-J2)."""
    document: Optional[str] = None
    customer: Optional[str] = None
    demand_class: str
    qty: float


class SPODocumentLine(BaseModel):
    """One allocation line, with the computed fields the Lines tab renders (AC-13)."""
    id: str
    spo_number: Optional[str] = None
    product_id: Optional[str] = None
    product: Optional[ProductSimple] = None
    warehouse_id: Optional[str] = None
    warehouse: Optional[WarehouseSimple] = None
    allocated_quantity: int
    quantity_received: int
    quantity_rejected: int
    #: `max(allocated - received, 0)`.
    balance: int
    #: The one coalesce: shipment `eta_delay_date` -> shipment `estimated_arrival_date` ->
    #: line `expected_date`. Rendered AS IS - no TBA masking (plan Q3).
    arrival_date: Optional[date] = None
    #: `spo_supply.overdue_days` - 0 when not late, unstated, or the line is not outstanding.
    overdue_days: int
    #: Shipment supplier when a shipment is booked, else the line's own supplier.
    supplier_name: Optional[str] = None
    #: The line's OWN supplier (editable, UAT AC-24 part 3) - distinct from
    #: `supplier_name`, which reads the shipment's supplier first when one is booked.
    supplier_id: Optional[str] = None
    #: The line's OWN expected date (editable, UAT AC-24 part 2) - the fallback arm of
    #: the `arrival_date` coalesce, never the shipment's own ETA fields.
    expected_date: Optional[date] = None
    #: `in_plan` | `pool` | `off` | `none` (plan Q4, UAC AC-14).
    planning_span: str
    receipt_status: str
    #: `open_incoming_clauses()` AND balance > 0 - the fifth reader of the one rule (AC-12).
    outstanding: bool
    inbound_shipment: Optional[InboundShipmentSimple] = None
    line_status: Optional[str] = None
    #: The GRNs whose picking lines name THIS allocation (`picking_lines.spo_allocation_id`).
    #: Sparse on live data (the weak-matcher backfill) - the Received cell falls back to the
    #: document's key-matched `linked_grns` when this is empty.
    grns: List[LinkedGRNSimple] = []
    #: R23, AC-J2 - the PO this line pulled from, null on a line with no `po_line_id`.
    po: Optional[SPODocumentLinePO] = None
    #: The AutoCount book's own source purchase-order NUMBER, straight off
    #: `spo_allocations.from_po_number` - text, never resolved into `po` above
    #: (`PLAN-scm-book-linkage-on-document-lines.md` Slice B). Written by the live ingest
    #: (`ShippingOrderIngestService`/`DocumentIngestService`, contract 2.2) whenever the
    #: book names one; `None` means the book itself named no source document for that
    #: line, never that nothing has written it yet.
    from_po_number: Optional[str] = None
    #: R23, AC-J2 - every sales order this allocation covers, empty when none.
    so_covered: List[SPODocumentLineSOCovered] = []

    class Config:
        from_attributes = True


class SPODocumentContainer(BaseModel):
    """One distinct container over a document's visible lines (PLAN-spo-list-container-
    number.md AC-1). `shipment_id` is null for a raw, unlinked
    `spo_allocations.container_number`."""
    container_number: str
    shipment_id: Optional[str] = None


class SPODocumentRow(BaseModel):
    """One SPO number's header row, as the document list renders it (AC-2, AC-15)."""
    #: The document's own key, echoed as `id` so the row satisfies the frontend pager's
    #: `{id: string}` contract without a second field meaning the same thing.
    id: str
    spo_number: str
    #: Earliest line `created_at` on the document.
    doc_date: Optional[date] = None
    #: The majority supplier across the document's lines.
    supplier_name: Optional[str] = None
    #: How many OTHER suppliers disagree with `supplier_name` - 0 when every line agrees.
    supplier_extra_count: int = 0
    status: str
    #: Earliest ETA across the document's OUTSTANDING lines, AS IS.
    earliest_eta: Optional[date] = None
    total_allocated: int
    total_received: int
    #: Sum of `balance` over OUTSTANDING lines only.
    balance: int
    line_count: int
    #: Max `overdue_days` over the document's OUTSTANDING lines; 0 when none are late.
    worst_overdue_days: int
    #: Distinct containers over the document's visible lines, sorted by container
    #: number (PLAN-spo-list-container-number.md AC-1/AC-2).
    containers: List[SPODocumentContainer] = []


class SPODocumentLinkagePackingList(BaseModel):
    id: str
    container: Optional[str] = None


class SPODocumentLinkage(BaseModel):
    """The header linkage strip (R23, AC-J3) - rollups across every line on this document."""
    po_count: int
    line_count: int
    so_count: int
    so_qty: float
    packing_list: Optional[SPODocumentLinkagePackingList] = None
    grn_count: int


class SPODocument(BaseModel):
    """The document form view's payload: header rollup + every line (AC-16)."""
    spo_number: str
    doc_date: Optional[date] = None
    supplier_name: Optional[str] = None
    supplier_extra_count: int = 0
    status: str
    total_allocated: int
    total_received: int
    balance: int
    line_count: int
    lines: List[SPODocumentLine]
    # The GRNs received against this SPO number (variant formats matched by key),
    # so a Completed document can answer "received on WHICH goods receipt" - the
    # capability the retired per-allocation page's Related Documents panel carried.
    linked_grns: List[LinkedGRNSimple] = []
    #: R23, AC-J3.
    linkage: Optional[SPODocumentLinkage] = None


class PickingLineBase(BaseModel):
    spo_allocation_id: Optional[str] = None
    # What the SHEET said this line was received against, unnormalised. Present
    # whether or not it matched an allocation, so a line the matcher could not
    # place reads as stated rather than as a dash; NULL when no single SPO was
    # named. On the BASE (not just the response) so a GRN edit can round-trip the
    # value it read instead of deleting it.
    spo_number_raw: Optional[str] = None
    product_id: str
    quantity_expected: int
    quantity_picked: int
    # quantity_discrepancy is a DB-generated column; do not include in create
    uom_id: Optional[str] = None
    picked_condition: str = "good"
    condition_remarks: Optional[str] = None
    batch_number_picked: Optional[str] = None
    expiry_date: Optional[date] = None
    unit_cost: Optional[Decimal] = None
    line_total: Optional[Decimal] = None
    source_warehouse_id: Optional[str] = None
    destination_warehouse_id: Optional[str] = None


class PickingLineCreate(PickingLineBase):
    pass


class PickingLineResponse(PickingLineBase):
    id: str
    picking_header_id: str
    quantity_discrepancy: int = 0  # from DB generated column
    created_at: datetime
    updated_at: Optional[datetime] = None
    product: Optional[ProductSimple] = None
    spo_allocation: Optional[SPOAllocationSimple] = None
    source_warehouse: Optional[WarehouseSimple] = None
    destination_warehouse: Optional[WarehouseSimple] = None

    class Config:
        from_attributes = True


class PickingHeaderBase(BaseModel):
    picking_number: str
    spo_number: Optional[str] = None
    picking_type: str
    source_entity_type: Optional[str] = None
    source_entity_id: Optional[str] = None
    picking_date: date
    picked_by_user_id: Optional[str] = None
    inspection_status: str = "pending"
    quality_remarks: Optional[str] = None
    inspected_by_user_id: Optional[str] = None
    inspection_date: Optional[datetime] = None
    picking_status: str = "draft"
    total_items_picked: Optional[int] = None
    total_items_discrepancy: Optional[int] = None
    total_cost: Optional[Decimal] = None
    notes: Optional[str] = None

    # source_entity_id is physically a uuid column on picking_headers (the model
    # declares String, but the live column is uuid), so SQLAlchemy hands back a
    # UUID object that strict str validation rejects - coerce like the user ids.
    @field_validator(
        "picked_by_user_id", "inspected_by_user_id", "source_entity_id", mode="before"
    )
    @classmethod
    def coerce_user_id_to_str(cls, v: object) -> Optional[str]:
        if v is None:
            return None
        return str(v)


class PickingHeaderCreate(PickingHeaderBase):
    picking_lines: Optional[List[PickingLineCreate]] = None


class PickingHeaderUpdate(BaseModel):
    spo_number: Optional[str] = None
    picking_date: Optional[date] = None
    picked_by_user_id: Optional[str] = None
    inspection_status: Optional[str] = None
    quality_remarks: Optional[str] = None
    inspected_by_user_id: Optional[str] = None
    inspection_date: Optional[datetime] = None
    picking_status: Optional[str] = None
    total_items_picked: Optional[int] = None
    total_items_discrepancy: Optional[int] = None
    total_cost: Optional[Decimal] = None
    notes: Optional[str] = None
    picking_lines: Optional[List[PickingLineCreate]] = None


class PickingHeaderResponse(PickingHeaderBase):
    id: str
    created_at: datetime
    updated_at: datetime
    picking_lines: Optional[List[PickingLineResponse]] = None
    lines_count: Optional[int] = 0
    items_count: Optional[int] = 0
    # Provenance: how this GRN got here. `source_system` is 'ui' | 'import' |
    # 'external_api'; the two labels are resolved server-side because the UI must
    # never print a UUID. All None for rows created before this was recorded,
    # which reads as "unknown" rather than guessing an author.
    source_system: Optional[str] = None
    created_by_label: Optional[str] = None
    import_filename: Optional[str] = None
    
    class Config:
        from_attributes = True


def _parse_date_string(v: Optional[str | date]) -> Optional[date]:
    """Parse date from string; accept dd/mm/yyyy, dd-mm-yyyy, yyyy-mm-dd."""
    if v is None:
        return None
    if isinstance(v, date):
        return v
    if not isinstance(v, str):
        return None
    s = v.strip()
    if not s:
        return None
    from datetime import datetime as dt
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return dt.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _coerce_numeric_or_text_to_string(v: object) -> Optional[str]:
    """Accept numeric/text values and normalize as trimmed string."""
    if v is None:
        return None
    if isinstance(v, str):
        s = v.strip()
        return s if s else None
    if isinstance(v, (int, float, Decimal)):
        return str(v)
    return None


def _coerce_scope_id_to_string(v: object) -> Optional[str]:
    if v is None:
        return None
    if isinstance(v, str):
        s = v.strip()
        return s if s else None
    if isinstance(v, (int, float, Decimal)):
        return str(int(v)) if isinstance(v, float) and v.is_integer() else str(v)
    s = str(v).strip()
    return s if s else None


class StockInquiryBase(BaseModel):
    salesperson: Optional[str] = None
    # Requestor FK (the salesman this inquiry is FOR) - drives CS pin routing.
    # `contact_id` stays the submitter. The resolved display name is a READ-ONLY
    # derived field and lives on the response schemas only: on a create/update
    # base it would ride into `StockInquiry(**data)` and 500 the whole route.
    salesperson_contact_id: Optional[str] = None
    product_code: Optional[str] = None
    item_description: Optional[str] = None
    project_customer: Optional[str] = None
    project_name: Optional[str] = None
    quantity: Optional[str] = None
    delivery_date: Optional[str] = None
    remark: Optional[str] = None
    additional_remark: Optional[str] = None
    purchasing_response: Optional[str] = None
    contact_id: Optional[str] = None
    space_id: Optional[str] = None
    status: Optional[str] = None
    last_responded_by: Optional[str] = None
    last_responded_at: Optional[datetime] = None

    @field_validator("quantity", mode="before")
    @classmethod
    def coerce_quantity_to_string(cls, v: object) -> Optional[str]:
        return _coerce_numeric_or_text_to_string(v)

    @field_validator("contact_id", "space_id", mode="before")
    @classmethod
    def coerce_scope_ids(cls, v: object) -> Optional[str]:
        return _coerce_scope_id_to_string(v)


class StockInquiryCreate(StockInquiryBase):
    """Create payload. Optional ``inquiry_number`` is used only for lookup: if a rejected inquiry
    with that number exists, that row is updated instead of inserting a new one."""

    inquiry_number: Optional[str] = Field(
        default=None,
        description="When set, if a rejected inquiry with this number exists it is updated with the rest of the payload and status; otherwise 409 if the number exists and is not rejected.",
    )
    user_confirmed: Optional[bool] = Field(
        default=None,
        description=(
            "External/MCP submissions only: must be true after the end user explicitly confirms "
            "the final summary (e.g. OK, YES, CONFIRM). Not required for in-app CRM creates."
        ),
    )

    @field_validator("quantity")
    @classmethod
    def reject_negative_quantity(cls, v: Optional[str]) -> Optional[str]:
        if v is None or v == "":
            return v
        try:
            n = float(v)
        except (TypeError, ValueError):
            return v  # non-numeric free-text legacy values pass through
        if n < 0:
            raise ValueError("quantity must be a non-negative number")
        return v


class StockInquiryUpdate(BaseModel):
    salesperson: Optional[str] = None
    salesperson_contact_id: Optional[str] = None
    product_code: Optional[str] = None
    item_description: Optional[str] = None
    project_customer: Optional[str] = None
    project_name: Optional[str] = None
    quantity: Optional[str] = None
    delivery_date: Optional[str] = None
    remark: Optional[str] = None
    additional_remark: Optional[str] = None
    purchasing_response: Optional[str] = None
    contact_id: Optional[str] = None
    space_id: Optional[str] = None
    # Declared, but NOT editable on ANY update path (the plain PUT and
    # update-and-reply alike): the lifecycle moves only through the workflow
    # actions. A value that would MOVE the record is refused with a 422; one that
    # echoes the current status is accepted and dropped, so a caller posting the
    # whole entity back still saves. See ``_pop_status_or_refuse_move`` in
    # procurement_service.
    status: Optional[str] = None
    last_responded_by: Optional[str] = None
    last_responded_at: Optional[datetime] = None

    @field_validator("quantity", mode="before")
    @classmethod
    def coerce_quantity_to_string(cls, v: object) -> Optional[str]:
        return _coerce_numeric_or_text_to_string(v)

    @field_validator("quantity")
    @classmethod
    def reject_negative_quantity(cls, v: Optional[str]) -> Optional[str]:
        if v is None or v == "":
            return v
        try:
            n = float(v)
        except (TypeError, ValueError):
            return v  # non-numeric free-text legacy values pass through
        if n < 0:
            raise ValueError("quantity must be a non-negative number")
        return v

    @field_validator("contact_id", "space_id", mode="before")
    @classmethod
    def coerce_scope_ids(cls, v: object) -> Optional[str]:
        return _coerce_scope_id_to_string(v)


class StockInquiryAttachmentResponse(BaseModel):
    id: str
    inquiry_id: str
    attachment_id: Optional[str] = None
    file_name: Optional[str] = None
    original_filename: Optional[str] = None
    file_url: Optional[str] = None
    file_size_bytes: Optional[int] = None
    uploaded_at: Optional[datetime] = None
    link_type: Optional[str] = None
    # Uploader attribution (UAC B2/B5) - omitting these here silently drops what
    # serialize_link computes and the panel renders "Unknown".
    mime_type: Optional[str] = None
    uploader_kind: Optional[str] = None
    uploaded_by_name: Optional[str] = None
    uploaded_by_role: Optional[str] = None
    can_unlink: Optional[bool] = None


class StockInquiryRejectReopenRequest(BaseModel):
    reason: Optional[str] = Field(
        default=None,
        description="Required (non-empty) for project-sales-reject and purchasing-reject; optional for reopen.",
    )


class StockInquiryResponse(StockInquiryBase):
    id: str
    # Requestor display name, resolved live from the FK so a contact rename
    # fixes every screen with no backfill. Read-only, response-only.
    salesperson_contact_name: Optional[str] = None
    system_id: Optional[str] = None
    form_type: Optional[str] = None
    inquiry_number: Optional[str] = None
    view_url: Optional[str] = None
    respond_inbox_url: Optional[str] = None
    status: Optional[str] = None
    last_responded_by: Optional[str] = None
    last_responded_by_name: Optional[str] = None
    last_responded_at: Optional[datetime] = None
    rejection_reason: Optional[str] = None
    rejected_at: Optional[datetime] = None
    rejected_by: Optional[str] = None
    rejected_by_name: Optional[str] = None
    rejected_by_wa_phone: Optional[str] = None  # rejecter's wa.me digits (banner link)
    rejected_from: Optional[str] = None
    reopen_reason: Optional[str] = None
    reopened_at: Optional[datetime] = None
    reopened_by: Optional[str] = None
    reopened_by_name: Optional[str] = None
    assigned_to_id: Optional[str] = None  # latest unresolved form-SLA assignee (users.id)
    assigned_to_name: Optional[str] = None  # resolved display name
    handled_by_name: Optional[str] = None  # form-handling-lock holder display name
    handled_by_wa_phone: Optional[str] = None  # holder's wa.me digits (banner link)
    # Void banner (BAN-1). voided_by is exposed as a resolved display name only (no UUID).
    void_reason: Optional[str] = None
    voided_at: Optional[datetime] = None
    voided_by_name: Optional[str] = None
    voided_by_wa_phone: Optional[str] = None
    # How many PDF exports the VIEWING user has taken of this record (list path only;
    # 0 on the detail path, which does not batch the count).
    print_count: Optional[int] = None
    # Portal submission revisions. The client echoes revision_no back in the
    # X-Revision-No header on every write and a stale value is refused with 409
    # (UAC CB1). Declared here because a response_model silently DROPS any field
    # it does not name, which would leave the fence nothing to compare.
    revision_no: Optional[int] = None
    last_revised_at: Optional[datetime] = None
    # Whether `purchasing_response` may be written at this status (UAC O1). Computed
    # from `response_gate`, the same module the write path raises from, so the client
    # gating an affordance and the server enforcing the rule read ONE source instead
    # of two status lists that drift. Same response_model rule as above.
    response_write_allowed: Optional[bool] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    attachments: Optional[List[StockInquiryAttachmentResponse]] = []

    class Config:
        from_attributes = True


# Purchase Request / Sponsorship Form
class PurchaseRequestLineBase(BaseModel):
    item_code: Optional[str] = None
    quantity: Optional[Decimal] = None
    remark: Optional[str] = None
    unit_price: Optional[Decimal] = None  # sponsorship form line
    total: Optional[Decimal] = None  # sponsorship form line (qty * unit_price)

    @field_validator("unit_price", mode="before")
    @classmethod
    def _blank_unit_price_is_missing(cls, v: object) -> object:
        # #1227: a blank string must read as "missing" (the same
        # `refuse_missing_sponsorship_unit_prices` refusal), not pydantic's own
        # decimal-parsing 422 - matches the external line's own blank coercion
        # (`PurchaseRequestExternalLine.coerce_decimal`).
        if isinstance(v, str) and not v.strip():
            return None
        return v


class PurchaseRequestLineCreate(PurchaseRequestLineBase):
    pass


class PurchaseRequestLineResponse(PurchaseRequestLineBase):
    id: str
    purchase_request_id: str
    sort_order: Optional[int] = None
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


def refuse_missing_sponsorship_unit_prices(request_type: Optional[str], products) -> None:
    """#1227: a sponsorship form line must carry a usable unit price on create and edit -
    purchase requests are unchanged (``products is None`` on an update that does not touch
    lines is also a no-op). Raises with the same ``{message, detail, code}`` shape
    ``price_tag_request_service`` uses for a line refusal (``detail=f"line:{index}"``), so a
    caller (the portal's external create, or the system form) can name the offending line
    exactly like it already does for a price tag request line.
    """
    if request_type != "sponsorship_form" or products is None:
        return
    for index, line in enumerate(products):
        price = getattr(line, "unit_price", None)
        if price is None or price < 0:
            raise AppException(
                status_code=422,
                message="Unit price is required.",
                detail=f"line:{index}",
                code="SPONSORSHIP_UNIT_PRICE_REQUIRED",
            )


class PurchaseRequestHeaderBase(BaseModel):
    request_type: str  # purchase_request | sponsorship_form
    request_number: Optional[str] = None  # User-assignable form number (e.g. PR-2026-001)
    request_date: Optional[date] = None
    customer_name: Optional[str] = None
    # Site contact, free text ("name and contact number"). Optional.
    pic: Optional[str] = None
    project_title: Optional[str] = None
    # AC-F3: the sponsorship-to-project link. Nullable everywhere, and project_title
    # stays as the display fallback for the rows that predate it (AC-F6).
    project_id: Optional[str] = None
    purpose: Optional[str] = None
    delivery_address: Optional[str] = None  # sponsorship form
    total_project_value: Optional[Decimal] = None  # sponsorship form
    total_project_value_text: Optional[str] = None  # sponsorship form (e.g. 800K)
    sponsor_subject: Optional[str] = None  # sponsorship: showroom/mockup/others (lookup-bound)
    sponsor_subject_other: Optional[str] = None  # sponsorship: free-text detail when sponsor_subject='others'
    sales_type: Optional[str] = None  # PR: project/cash_sales (lookup-bound); required for PR at service layer; SF ignores
    expected_delivery_date: Optional[date] = None
    expected_po_date: Optional[date] = None
    expected_po_date_text: Optional[str] = None
    requested_by: Optional[str] = None
    # Requestor FK (the person the request is FOR) - drives CS pin routing.
    # `contact_id` stays the submitter, who keeps receiving every update. The
    # resolved display name is a READ-ONLY derived field and lives on the
    # response schemas only (see StockInquiryBase for why it must not sit here).
    requested_by_contact_id: Optional[str] = None
    requested_at: Optional[date] = None  # DEPRECATED - see submitted_at + request_date
    submitted_at: Optional[datetime] = None  # auto-stamped on submit; top "Date" on the document (read-only)
    status: Optional[str] = None
    source: Optional[str] = None
    external_reference: Optional[str] = None
    contact_id: Optional[str] = None
    space_id: Optional[str] = None
    approver_user_id: Optional[str] = None
    approver_email: Optional[str] = None
    approval_status: Optional[str] = None  # pending | approved | rejected
    approved_at: Optional[datetime] = None
    approved_by: Optional[str] = None
    rejected_by_id: Optional[str] = None  # users.id of the rejecter (dedicated column)
    rejected_by_name: Optional[str] = None  # resolved rejecter display name (banner)
    rejected_by_wa_phone: Optional[str] = None  # rejecter's wa.me digits (banner link)
    approval_signature_ref: Optional[str] = None
    approval_comments: Optional[str] = None


class PurchaseRequestHeaderCreate(PurchaseRequestHeaderBase):
    products: Optional[List[PurchaseRequestLineCreate]] = []

    @field_validator("request_type")
    @classmethod
    def validate_request_type(cls, v: Optional[str]) -> Optional[str]:
        if v and v.strip() not in ("purchase_request", "sponsorship_form"):
            raise ValueError("request_type must be purchase_request or sponsorship_form")
        return v.strip() if v else None

    @field_validator("request_date", "expected_delivery_date", "expected_po_date", "requested_at", mode="before")
    @classmethod
    def parse_date(cls, v: Optional[str | date]) -> Optional[date]:
        return _parse_date_string(v)

    @field_validator("contact_id", "space_id", mode="before")
    @classmethod
    def coerce_scope_ids(cls, v: object) -> Optional[str]:
        return _coerce_scope_id_to_string(v)

    @model_validator(mode="after")
    def _require_sponsorship_unit_prices(self) -> "PurchaseRequestHeaderCreate":
        refuse_missing_sponsorship_unit_prices(self.request_type, self.products)
        return self


class PurchaseRequestHeaderUpdate(BaseModel):
    request_type: Optional[str] = None
    request_number: Optional[str] = None
    request_date: Optional[date] = None
    customer_name: Optional[str] = None
    # Site contact, free text ("name and contact number"). Optional.
    pic: Optional[str] = None
    project_title: Optional[str] = None
    # AC-F3: the sponsorship-to-project link. Nullable everywhere, and project_title
    # stays as the display fallback for the rows that predate it (AC-F6).
    project_id: Optional[str] = None
    purpose: Optional[str] = None
    delivery_address: Optional[str] = None
    total_project_value: Optional[Decimal] = None
    total_project_value_text: Optional[str] = None
    sponsor_subject: Optional[str] = None
    sponsor_subject_other: Optional[str] = None
    sales_type: Optional[str] = None  # PR sales type (project/cash_sales)
    expected_delivery_date: Optional[date] = None
    expected_po_date: Optional[date] = None
    expected_po_date_text: Optional[str] = None
    requested_by: Optional[str] = None
    requested_by_contact_id: Optional[str] = None
    requested_at: Optional[date] = None
    # Declared, but NOT editable on ANY update path (the plain PUT and
    # update-and-reply alike): the lifecycle moves only through the workflow
    # actions. A value that would MOVE the record is refused with a 422; one that
    # echoes the current status is accepted and dropped, so a caller posting the
    # whole entity back still saves. See ``_pop_status_or_refuse_move`` in
    # procurement_service.
    status: Optional[str] = None
    contact_id: Optional[str] = None
    space_id: Optional[str] = None
    approver_user_id: Optional[str] = None
    approver_email: Optional[str] = None
    products: Optional[List[PurchaseRequestLineCreate]] = None

    @field_validator("request_type", mode="before")
    @classmethod
    def validate_request_type(cls, v: Optional[str]) -> Optional[str]:
        if v is None or (isinstance(v, str) and not v.strip()):
            return None
        s = v.strip() if isinstance(v, str) else v
        if s not in ("purchase_request", "sponsorship_form"):
            raise ValueError("request_type must be purchase_request or sponsorship_form")
        return s

    @field_validator("request_date", "expected_delivery_date", "expected_po_date", "requested_at", mode="before")
    @classmethod
    def parse_date(cls, v: Optional[str | date]) -> Optional[date]:
        return _parse_date_string(v)

    @field_validator("contact_id", "space_id", mode="before")
    @classmethod
    def coerce_scope_ids(cls, v: object) -> Optional[str]:
        return _coerce_scope_id_to_string(v)

    @model_validator(mode="after")
    def _require_sponsorship_unit_prices(self) -> "PurchaseRequestHeaderUpdate":
        refuse_missing_sponsorship_unit_prices(self.request_type, self.products)
        return self


class PurchaseRequestUpdateAndReply(PurchaseRequestHeaderUpdate):
    """Update purchase request and send a reply to the conversation via Respond.io.
    Either reply_message or request_number must be set (form number is replied as assigned)."""
    reply_message: Optional[str] = None


class PurchaseRequestHeaderListResponse(PurchaseRequestHeaderBase):
    """Response for list endpoint (no lines)."""
    id: str
    # Requestor display name, resolved live from the FK (read-only, response-only).
    requested_by_contact_name: Optional[str] = None
    request_number: Optional[str] = None
    view_url: Optional[str] = None
    respond_inbox_url: Optional[str] = None
    assigned_to_id: Optional[str] = None  # latest unresolved form-SLA assignee (users.id)
    assigned_to_name: Optional[str] = None  # resolved display name
    handled_by_name: Optional[str] = None  # form-handling-lock holder display name
    handled_by_wa_phone: Optional[str] = None  # holder's wa.me digits (banner link)
    # Portal revisions: the list needs BOTH. `revision_no` drives the "Rev N" badge
    # and the -R{n} display suffix, and the frontend fence registry harvests it from
    # list rows so a row action taken without opening the detail page is still
    # fenced. Omitting it here does not merely hide a badge, it silently unfences
    # every PR/SF row action, because `response_model` strips undeclared fields.
    revision_no: Optional[int] = None
    last_revised_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class PurchaseRequestAttachmentResponse(BaseModel):
    id: str
    purchase_request_id: str
    attachment_id: Optional[str] = None
    file_name: Optional[str] = None
    original_filename: Optional[str] = None
    file_url: Optional[str] = None
    file_size_bytes: Optional[int] = None
    uploaded_at: Optional[datetime] = None
    link_type: Optional[str] = None
    # Uploader attribution (UAC B2/B5) - omitting these here silently drops what
    # serialize_link computes and the panel renders "Unknown".
    mime_type: Optional[str] = None
    uploader_kind: Optional[str] = None
    uploaded_by_name: Optional[str] = None
    uploaded_by_role: Optional[str] = None
    can_unlink: Optional[bool] = None


class PurchaseRequestAttachmentLinkRequest(BaseModel):
    attachment_id: str


class PurchaseRequestHeaderResponse(PurchaseRequestHeaderBase):
    id: str
    # Resolved for display (AC-L3): the office-side detail page shows a project CODE, never a
    # UUID, matching what the portal already resolves for contacts.
    project_code: Optional[str] = None
    # Requestor display name, resolved live from the FK (read-only, response-only).
    requested_by_contact_name: Optional[str] = None
    request_number: Optional[str] = None
    view_url: Optional[str] = None
    respond_inbox_url: Optional[str] = None
    approver_display_name: Optional[str] = None  # Resolved from User when approver_user_id is set
    assigned_to_id: Optional[str] = None  # latest unresolved form-SLA assignee (users.id)
    assigned_to_name: Optional[str] = None  # resolved display name
    handled_by_name: Optional[str] = None  # form-handling-lock holder display name
    handled_by_wa_phone: Optional[str] = None  # holder's wa.me digits (banner link)
    # Void banner (BAN-1). voided_by is exposed as a resolved display name only (no UUID).
    void_reason: Optional[str] = None
    voided_at: Optional[datetime] = None
    voided_by_name: Optional[str] = None
    voided_by_wa_phone: Optional[str] = None
    # Portal submission revisions - see StockInquiryResponse for why these are
    # declared explicitly (UAC CB1).
    revision_no: Optional[int] = None
    last_revised_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    lines: Optional[List[PurchaseRequestLineResponse]] = []
    attachments: Optional[List[PurchaseRequestAttachmentResponse]] = []
    grand_total: Optional[Decimal] = None  # Sum of line totals; set for sponsorship_form

    class Config:
        from_attributes = True


class SendApprovalLinkRequest(BaseModel):
    """Request to create approval token and get public link; optionally send link by email to approver."""
    approver_email: Optional[str] = None
    approver_user_id: Optional[str] = None
    expires_hours: Optional[int] = 24
    send_email: Optional[bool] = False
    base_url: Optional[str] = None  # Frontend origin (e.g. https://app.example.com) so approval link in email is absolute


class SendApprovalLinkResponse(BaseModel):
    approval_url: str
    expires_at: datetime
    token_id: str
    email_sent: Optional[bool] = None
    email_error: Optional[str] = None


class RejectSubmittedRequest(BaseModel):
    """Body for rejecting a purchase request / sponsorship form before sending for approval."""
    rejection_reason: str = Field(min_length=1, description="Mandatory reason; sent to the contact via Respond.io.")


class ViewLinkRequest(BaseModel):
    """Optional base URL to build full view URL (e.g. frontend origin)."""
    base_url: Optional[str] = None


class ViewLinkResponse(BaseModel):
    view_token: str
    view_url: str  # Full URL if base_url was provided, else relative path
    portal_url: Optional[str] = None  # Landing for the contact's submission portal (mintable when contact_id+space_id known)


class PublicApprovalLineSummary(BaseModel):
    """Single line for public approval/view. Remark for purchase_request; unit_price/total for sponsorship_form."""
    item_code: Optional[str] = None
    quantity: Optional[float] = None  # from Numeric in DB
    remark: Optional[str] = None
    unit_price: Optional[Decimal] = None  # sponsorship form
    total: Optional[Decimal] = None  # sponsorship form
    sort_order: Optional[int] = None

    class Config:
        from_attributes = True


class PublicApprovalSummaryResponse(BaseModel):
    """Summary of request for public approval page (no sensitive data)."""
    entity_type: str
    entity_id: str
    request_number: Optional[str] = None
    request_type: str
    customer_name: Optional[str] = None
    # Site contact, free text ("name and contact number"). Optional.
    pic: Optional[str] = None
    project_title: Optional[str] = None
    # AC-F3: the sponsorship-to-project link. Nullable everywhere, and project_title
    # stays as the display fallback for the rows that predate it (AC-F6).
    project_id: Optional[str] = None
    purpose: Optional[str] = None
    delivery_address: Optional[str] = None  # sponsorship form
    total_project_value: Optional[Decimal] = None  # sponsorship form (numeric)
    total_project_value_text: Optional[str] = None  # sponsorship form (descriptive)
    sponsor_subject: Optional[str] = None  # sponsorship form
    sales_type: Optional[str] = None  # PR sales type (project/cash_sales); null for SF
    sponsor_subject_other: Optional[str] = None  # sponsorship form: free-text when 'others'
    requested_by: Optional[str] = None
    requested_by_contact_id: Optional[str] = None
    requested_by_contact_name: Optional[str] = None
    request_date: Optional[date] = None
    submitted_at: Optional[datetime] = None  # top "Date" on the document (auto-stamped on submit)
    created_at: Optional[datetime] = None
    expected_delivery_date: Optional[date] = None
    expected_po_date: Optional[date] = None
    expected_po_date_text: Optional[str] = None
    expires_at: Optional[datetime] = None  # None when used for view (no expiry)
    lines: Optional[List[PublicApprovalLineSummary]] = []
    grand_total: Optional[Decimal] = None  # sponsorship form: sum of line totals
    approval_status: Optional[str] = None  # draft | pending | approved | rejected
    approver_display_name: Optional[str] = None  # Prefill "Your name" when approver is system user
    approver_email: Optional[str] = None  # Prefill fallback when no display name
    requested_at: Optional[date] = None  # Date next to Requested by (document footer)
    approved_at: Optional[datetime] = None  # Set when approved or rejected via link
    approved_by: Optional[str] = None  # Approver name/email (same field for approve/reject)
    approval_comments: Optional[str] = None  # Approval notes or rejection reason


class PublicApprovalSubmitRequest(BaseModel):
    action: str  # approved | rejected
    approved_by: Optional[str] = None
    approval_signature_ref: Optional[str] = None
    approval_comments: Optional[str] = Field(
        default=None,
        description="Notes for approval; must be non-empty when action is rejected.",
    )
