"""Inventory management schemas."""
from pydantic import BaseModel, field_validator
from typing import Any, Dict, List, Optional
from datetime import datetime

from app.schemas.common import ListResponse


class WarehouseBase(BaseModel):
    warehouse_code: str
    warehouse_name: Optional[str] = None
    location: Optional[str] = None
    manager_id: Optional[str] = None
    is_active: bool = True
    # Planning configuration. Both drive what the reorder plan buys, so they belong on the
    # warehouse screen rather than in a migration only an engineer can change.
    #
    # counts_as_available - whether stock here may cover demand. Held, reserved, defective
    # and clearance locations hold real stock that is not sellable; counting it makes the
    # plan buy too little, and excluding a good location makes it buy too much.
    #
    # pool_warehouse_id - the shared pool this location draws on. A shortage in a customer
    # bin is covered from its site's pool before it is ever a purchase (ADR-0011). Empty
    # means the location is its own pool, which is the no-pooling default.
    #
    # segment - who this location sells to, `dealer` or `project`. The bare site code is
    # the dealer bin and its suffixed bins are project stock, which is why "last purchase
    # cost" is two different numbers depending on who is asking.
    #
    # fulfilment_planning - whether this bin takes part in fulfilment planning at all
    # (borrow ladder v7.1, R17). Off means outside it ENTIRELY: no on hand, no incoming and
    # no sales-order line of this location reaches the ladder, the board or the Stock Debt
    # view. Defaults to false, which is the column's own default: a location nobody has
    # decided about is not planned against.
    counts_as_available: bool = True
    pool_warehouse_id: Optional[str] = None
    segment: Optional[str] = None
    fulfilment_planning: bool = False


class WarehouseCreate(WarehouseBase):
    pass


class WarehouseUpdate(BaseModel):
    warehouse_code: Optional[str] = None
    warehouse_name: Optional[str] = None
    location: Optional[str] = None
    manager_id: Optional[str] = None
    is_active: Optional[bool] = None
    counts_as_available: Optional[bool] = None
    pool_warehouse_id: Optional[str] = None
    segment: Optional[str] = None
    fulfilment_planning: Optional[bool] = None


class WarehouseResponse(WarehouseBase):
    id: str
    created_at: datetime
    updated_at: Optional[datetime] = None
    zones_count: Optional[int] = 0
    stock_count: Optional[int] = 0
    # Resolved for display: the UI must never show a bare UUID.
    pool_warehouse_code: Optional[str] = None
    # Set only on a `company_scope=grants` read, so a picker can tag each row.
    company_id: Optional[str] = None
    company_name: Optional[str] = None
    
    class Config:
        from_attributes = True


class StorageZoneBase(BaseModel):
    warehouse_id: str
    zone_code: str
    zone_name: Optional[str] = None
    zone_type: str
    capacity: Optional[int] = 0


class StorageZoneCreate(StorageZoneBase):
    pass


class StorageZoneUpdate(BaseModel):
    zone_name: Optional[str] = None
    zone_type: Optional[str] = None
    capacity: Optional[int] = None


class StorageZoneResponse(StorageZoneBase):
    id: str
    created_at: datetime
    
    class Config:
        from_attributes = True


class WarehouseSimple(BaseModel):
    id: str
    warehouse_code: Optional[str] = None
    warehouse_name: Optional[str] = None
    location: Optional[str] = None

    class Config:
        from_attributes = True

class StorageZoneTreeItem(StorageZoneResponse):
    warehouse: Optional[WarehouseSimple] = None
    children: Optional[list["StorageZoneTreeItem"]] = None

    class Config:
        from_attributes = True


class StockBase(BaseModel):
    product_id: str
    warehouse_id: str
    zone_id: Optional[str] = None
    quantity_on_hand: int = 0
    quantity_reserved: int = 0
    quantity_available: int = 0
    quantity_damaged: int = 0
    reorder_point: Optional[int] = None


class StockCreate(StockBase):
    pass


class StockUpdate(BaseModel):
    zone_id: Optional[str] = None
    quantity_on_hand: Optional[int] = None
    quantity_reserved: Optional[int] = None
    quantity_available: Optional[int] = None
    quantity_damaged: Optional[int] = None
    reorder_point: Optional[int] = None


class ProductSimple(BaseModel):
    id: str
    product_code: str
    product_name: str
    reorder_level: Optional[int] = None
    is_discontinued: bool = False

    class Config:
        from_attributes = True


class StockLedgerResponse(BaseModel):
    id: str
    product_id: str
    warehouse_id: str
    transaction_type: str
    quantity_change: int
    previous_quantity: int
    new_quantity: int
    reference_type: Optional[str] = None
    reference_id: Optional[str] = None
    notes: Optional[str] = None
    created_by: Optional[str] = None
    created_at: datetime
    product: Optional[ProductSimple] = None
    warehouse: Optional[WarehouseSimple] = None
    created_by_name: Optional[str] = None

    class Config:
        from_attributes = True


class StockResponse(StockBase):
    id: str
    created_at: datetime
    updated_at: Optional[datetime] = None
    product: Optional[ProductSimple] = None
    warehouse: Optional[WarehouseSimple] = None
    status: Optional[str] = None  # 'low', 'critical', 'normal', 'overstock'
    # Multi-company reply clarity: the owning company. ``company_name`` is
    # resolved ONLY when the lookup spanned more than one company
    # (`company_scope.stamp_lookup_companies`), and is null otherwise.
    company_id: Optional[str] = None
    company_name: Optional[str] = None

    @field_validator('company_id', mode='before')
    @classmethod
    def _company_id_to_str(cls, v):
        """Convert UUID objects to strings."""
        return str(v) if v else None
    
    class Config:
        from_attributes = True


class StockVisibilityBlock(BaseModel):
    """Which policy answered this request, echoed so n8n can branch without
    re-deriving it (same passthrough habit as `field_access` / `lookup_companies`)."""

    mode: str
    #: The active, company-scoped codes the balance can cover under this policy:
    #: an include list resolved to codes, or every active warehouse minus an
    #: exclusion, or [] when nothing is left. null means the policy names NEITHER
    #: list at all (every active warehouse, no rule).
    warehouse_codes: Optional[List[str]] = None
    #: Whether the locations holding none of the product were withheld. Echoed so
    #: n8n can phrase the reply without re-deriving the policy.
    hide_zero_locations: bool = False
    source: str


class StockSummaryLocation(BaseModel):
    warehouse_code: Optional[str] = None
    quantity_on_hand: int


class StockSummaryEntry(BaseModel):
    """One `compact` block: a product, its total and its allowed locations."""

    product_id: str
    product_code: Optional[str] = None
    product_name: Optional[str] = None
    total_on_hand: int
    locations: List[StockSummaryLocation] = []
    flags: Dict[str, Any] = {}


class StockAvailabilityPackingList(BaseModel):
    """AC-SA311: the R5 shipment's own packing list attachment, present on an
    `incoming` entry only when the asking contact's `packing_list_allowed` is true."""

    filename: Optional[str] = None
    file_path: Optional[str] = None
    mime_type: Optional[str] = None


class StockAvailabilityEntry(BaseModel):
    """One `availability` answer (chatbot stock ask v2 S3, R6/R14). Deliberately
    carries NO quantity of ours - not the on-hand figure, not incoming, not a PO
    line: `requested_qty` is the contact's own number echoed back, `branch` is the
    one of four fixed answers (`too_big` / `in_stock` / `incoming` / `no_incoming`,
    `app.services.stock_ask_branch.branch`), `cap_unset` says whether X resolved
    from NULLs (the B1 "no cap set for <category>" agent-notification reason),
    `eta` is `estimated_arrival_date + Y` as dd/mm/yyyy (incoming only), and
    `packing_list` is the R5 shipment's own attachment (incoming only, AC-SA311).

    AC-SA310: `verdict` / `running_low` / `disclaimer` / `available` (#1118's dealer
    stock verdict shape) are gone - this is the v2 shape, not a #1118 read."""

    product_id: str
    product_code: Optional[str] = None
    product_name: Optional[str] = None
    needs_quantity: bool
    requested_qty: Optional[int] = None
    branch: Optional[str] = None
    cap_unset: Optional[bool] = None
    category_name: Optional[str] = None
    eta: Optional[str] = None
    packing_list: Optional[StockAvailabilityPackingList] = None
    # AVAIL-MODE-REPLIES rule 2 (owner, 2 Oct 2026): how many are available, set ONLY on
    # an `in_stock` answer short of `requested_qty` within the category max.
    available_qty: Optional[int] = None


class StockBalanceListResponse(ListResponse[StockResponse]):
    """`GET /inventory/stock/balance`.

    The three visibility blocks are declared HERE rather than left to the dict the
    service returns: `response_model` silently drops anything it does not declare,
    so an undeclared block reaches the chatbot as if the feature were never built.
    """

    stock_visibility: Optional[StockVisibilityBlock] = None
    stock_summary: Optional[List[StockSummaryEntry]] = None
    stock_availability: Optional[List[StockAvailabilityEntry]] = None
    #: System-wide latest BULK_IMPORT time. Rows carry it as `updated_at`; the
    #: summary modes have no rows, so it is stated on the payload too.
    last_updated_at: Optional[datetime] = None


class StockBatchBase(BaseModel):
    product_id: str
    warehouse_id: str
    batch_code: str
    quantity: int = 0
    manufactured_date: Optional[datetime] = None
    expiry_date: Optional[datetime] = None
    received_date: datetime
    status: str = "AVAILABLE"


class StockBatchCreate(StockBatchBase):
    pass


class StockBatchUpdate(BaseModel):
    quantity: Optional[int] = None
    manufactured_date: Optional[datetime] = None
    expiry_date: Optional[datetime] = None
    status: Optional[str] = None


class StockBatchResponse(StockBatchBase):
    id: str
    created_at: datetime
    
    class Config:
        from_attributes = True


class StockDashboardResponse(BaseModel):
    total_skus: int
    total_quantity: int
    stock_by_warehouse: list
    stock_movement_30_days: list
    latest_stock_list_attachment: Optional[dict] = None
    limit: int = 10


class BulkImportStockRequest(BaseModel):
    """Request schema for bulk import."""
    stock: list[dict]  # List of stock dictionaries from Excel
    validate_only: Optional[bool] = False  # If True, validate only (no import); return errors/warnings.


class BulkImportWarehousesRequest(BaseModel):
    """Warehouse bulk import payload (raw Excel rows; column mapping handled server-side)."""
    warehouses: list[dict]
    validate_only: Optional[bool] = False


class BulkImportWarehousesResponse(BaseModel):
    import_session_id: Optional[str] = None
    created: int = 0
    updated: int = 0
    skipped: int = 0
    errors: list[str] = []
    warnings: list[dict] = []


class BulkImportStockResponse(BaseModel):
    """Response schema for bulk import."""
    import_session_id: Optional[str] = None
    created: int = 0
    updated: int = 0
    skipped: int = 0
    errors: list[str] = []
    warnings: list[dict] = []
