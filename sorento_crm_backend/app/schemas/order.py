"""Order management schemas."""
from pydantic import BaseModel, Field, field_validator
from typing import Optional, List
from datetime import datetime
from decimal import Decimal
import uuid


class OrderStatusBase(BaseModel):
    status_code: str
    status_name: str
    description: Optional[str] = None
    sequence: int = 0
    is_final_status: bool = False


class OrderStatusCreate(OrderStatusBase):
    pass


class OrderStatusUpdate(BaseModel):
    status_name: Optional[str] = None
    description: Optional[str] = None
    sequence: Optional[int] = None
    is_final_status: Optional[bool] = None


class OrderStatusResponse(OrderStatusBase):
    id: str
    created_at: datetime
    
    class Config:
        from_attributes = True


class CustomerBase(BaseModel):
    customer_code: str
    customer_name: str
    email: Optional[str] = None
    phone_number: Optional[str] = None
    is_active: bool = True


class CustomerCreate(CustomerBase):
    # Who sells to this customer (`sales_agents.id`). Optional: most of the 6,397 existing
    # rows have never had one set. `None` on create is the same as omitting it.
    sales_agent_id: Optional[str] = None
    account_level: Optional[int] = Field(default=None, ge=1, le=9)
    customer_group_id: Optional[str] = None


class CustomerUpdate(BaseModel):
    customer_name: Optional[str] = None
    email: Optional[str] = None
    phone_number: Optional[str] = None
    is_active: Optional[bool] = None
    # `null` clears the assignment (exclude_unset in the service keeps that distinct
    # from "field omitted, leave alone").
    sales_agent_id: Optional[str] = None
    # `null` clears the level; omitted leaves it alone.
    account_level: Optional[int] = Field(default=None, ge=1, le=9)
    # `null` clears the group; omitted leaves it alone.
    customer_group_id: Optional[str] = None


class CustomerSimple(BaseModel):
    id: str
    customer_code: str
    customer_name: str
    
    class Config:
        from_attributes = True


class CustomerResponse(CustomerBase):
    id: str
    created_at: datetime
    updated_at: Optional[datetime] = None
    # Who sells to this customer. `sales_agent_id` is the FK a write addresses; the code
    # + name are read-only, resolved off the `sales_agents` master by `Customer.sales_agent`
    # (`app/models/order.py`) so no screen ever has to print the id (Cursor rule).
    sales_agent_id: Optional[str] = None
    sales_agent_code: Optional[str] = None
    sales_agent_name: Optional[str] = None
    account_level: Optional[int] = None
    customer_group_id: Optional[str] = None
    customer_group_name: Optional[str] = None
    # Shown as columns on a sales agent's Customers tab.
    region: Optional[str] = None
    market_segment_code: Optional[str] = None

    class Config:
        from_attributes = True


class OrderBase(BaseModel):
    order_number: str
    order_date: Optional[datetime] = None
    estimated_delivery_date: Optional[datetime] = None
    actual_delivery_date: Optional[datetime] = None
    customer_id: Optional[str] = None
    order_status_id: Optional[str] = None
    billing_address_id: Optional[str] = None
    shipping_address_id: Optional[str] = None
    created_time: Optional[datetime] = None
    debtor_code: Optional[str] = None
    debtor_name: Optional[str] = None
    agent: Optional[str] = None
    is_cancelled: bool = False
    remarks_cs: Optional[str] = None
    order_type: Optional[str] = None
    pickup_time: Optional[str] = None
    checker: Optional[str] = None
    transporter: Optional[str] = None
    driver_name: Optional[str] = None
    lorry_plate: Optional[str] = None
    customer_ref: Optional[str] = None
    delivery_remarks_cs: Optional[str] = None
    delivery_remarks: Optional[str] = None
    salesman: Optional[str] = None
    trips: Optional[int] = None
    warehouse: Optional[str] = None
    delivery_days: Optional[int] = None
    kpi_warning: bool = False
    subtotal_amount: Decimal = Decimal("0")
    discount_amount: Decimal = Decimal("0")
    tax_amount: Decimal = Decimal("0")
    total_amount: Decimal = Decimal("0")
    remarks: Optional[str] = None


class OrderCreate(OrderBase):
    pass


class OrderUpdate(BaseModel):
    order_date: Optional[datetime] = None
    estimated_delivery_date: Optional[datetime] = None
    actual_delivery_date: Optional[datetime] = None
    customer_id: Optional[str] = None
    order_status_id: Optional[str] = None
    billing_address_id: Optional[str] = None
    shipping_address_id: Optional[str] = None
    created_time: Optional[datetime] = None
    debtor_code: Optional[str] = None
    debtor_name: Optional[str] = None
    agent: Optional[str] = None
    is_cancelled: Optional[bool] = None
    remarks_cs: Optional[str] = None
    order_type: Optional[str] = None
    pickup_time: Optional[str] = None
    checker: Optional[str] = None
    transporter: Optional[str] = None
    driver_name: Optional[str] = None
    lorry_plate: Optional[str] = None
    customer_ref: Optional[str] = None
    delivery_remarks_cs: Optional[str] = None
    delivery_remarks: Optional[str] = None
    salesman: Optional[str] = None
    trips: Optional[int] = None
    warehouse: Optional[str] = None
    delivery_days: Optional[int] = None
    kpi_warning: Optional[bool] = None
    subtotal_amount: Optional[Decimal] = None
    discount_amount: Optional[Decimal] = None
    tax_amount: Optional[Decimal] = None
    total_amount: Optional[Decimal] = None
    remarks: Optional[str] = None


class OrderStatusSimple(BaseModel):
    id: str
    status_code: str
    status_name: str
    
    class Config:
        from_attributes = True


class OrderLineProductMatch(BaseModel):
    id: str
    product_code: Optional[str] = None
    product_name: Optional[str] = None
    quantity: Optional[float] = None
    warehouse_code: Optional[str] = None
    warehouse_name: Optional[str] = None

    class Config:
        from_attributes = True


class OrderSimpleRef(BaseModel):
    id: str
    # Multi-company reply clarity: the owning company. ``company_name`` is
    # resolved ONLY when the lookup spanned more than one company
    # (`company_scope.stamp_lookup_companies`), and is null otherwise.
    company_id: Optional[str] = None
    company_name: Optional[str] = None
    order_number: str
    order_date: Optional[datetime] = None
    actual_delivery_date: Optional[datetime] = None
    debtor_name: Optional[str] = None
    matched_products: list[OrderLineProductMatch] = []

    @field_validator('company_id', mode='before')
    @classmethod
    def _company_id_to_str(cls, v):
        """Convert UUID objects to strings."""
        return str(v) if v else None

    class Config:
        from_attributes = True


class OrderResponse(OrderBase):
    id: str
    # Multi-company reply clarity: the owning company. ``company_name`` is
    # resolved ONLY when the lookup spanned more than one company
    # (`company_scope.stamp_lookup_companies`), and is null otherwise.
    company_id: Optional[str] = None
    company_name: Optional[str] = None
    # True when this DO is delivered AND linked to >=1 complaint, so Remarks CS is
    # frozen (its fulfilment is historical / already notified). Computed on the
    # single-order GET / PUT; defaults False on list rows (the edit form reads the
    # single GET). See PLAN-complaint-do-auto-fulfilment.md.
    remarks_cs_locked: bool = False
    # The fields an AutoCount-owned DO (`doc_key` set) takes only from AutoCount, so the edit
    # form shows them read-only (DO-OWNERSHIP-GUARD). Stamped on the single-order GET / PUT
    # like `remarks_cs_locked`; empty on list rows and on every row AutoCount does not own.
    autocount_owned_fields: list[str] = []
    created_at: datetime
    updated_at: datetime
    deleted_at: Optional[datetime] = None
    synced_to_excel: bool = False
    last_synced_to_excel: Optional[datetime] = None
    created_by: Optional[str] = None
    updated_by: Optional[str] = None
    customer: Optional[CustomerSimple] = None
    order_status: Optional[OrderStatusSimple] = None
    lines: Optional[list["OrderLineResponse"]] = []

    @field_validator('company_id', mode='before')
    @classmethod
    def _company_id_to_str(cls, v):
        """Convert UUID objects to strings."""
        return str(v) if v else None

    @field_validator('created_by', mode='before')
    @classmethod
    def convert_created_by_uuid(cls, v):
        """Convert UUID objects to strings for created_by."""
        if v is None:
            return None
        if isinstance(v, uuid.UUID):
            return str(v)
        return str(v) if v else None
    
    @field_validator('updated_by', mode='before')
    @classmethod
    def convert_updated_by_uuid(cls, v):
        """Convert UUID objects to strings for updated_by."""
        if v is None:
            return None
        if isinstance(v, uuid.UUID):
            return str(v)
        return str(v) if v else None
    
    class Config:
        from_attributes = True


class BulkImportRequest(BaseModel):
    """Request schema for bulk import."""
    orders: list[dict]  # List of order dictionaries from Excel


class BulkImportResponse(BaseModel):
    """Response schema for bulk import."""
    created: int = 0
    updated: int = 0
    errors: list[str] = []
    # Rows that updated an AutoCount-owned DO name the AutoCount fields left untouched.
    warnings: list[str] = []


class BulkDeleteOrdersRequest(BaseModel):
    """Request body for bulk delete: { ids: list[str] }."""
    ids: list[str]


class BulkDeleteOrderLinesRequest(BaseModel):
    """Request body for bulk line delete: { ids: list[str] }."""
    ids: list[str]


# Order lines (delivery order detail)
class OrderLineBase(BaseModel):
    product_id: str
    warehouse_id: str
    quantity: Optional[Decimal] = None
    unit_price: Optional[Decimal] = None
    discount: Optional[Decimal] = None
    total: Optional[Decimal] = None
    tax: Optional[Decimal] = None
    total_excluding_tax: Optional[Decimal] = None
    total_including_tax: Optional[Decimal] = None


class OrderLineCreate(OrderLineBase):
    pass


class OrderLineUpdate(BaseModel):
    quantity: Optional[Decimal] = None
    unit_price: Optional[Decimal] = None
    discount: Optional[Decimal] = None
    total: Optional[Decimal] = None
    tax: Optional[Decimal] = None
    total_excluding_tax: Optional[Decimal] = None
    total_including_tax: Optional[Decimal] = None


class ProductSimpleRef(BaseModel):
    id: str
    product_code: Optional[str] = None
    product_name: Optional[str] = None
    is_discontinued: bool = False

    class Config:
        from_attributes = True


class WarehouseSimpleRef(BaseModel):
    id: str
    warehouse_code: Optional[str] = None
    warehouse_name: Optional[str] = None

    class Config:
        from_attributes = True


class OrderLineResponse(OrderLineBase):
    id: str
    order_id: str
    line_sequence: int
    created_at: datetime
    updated_at: datetime
    product: Optional[ProductSimpleRef] = None
    warehouse: Optional[WarehouseSimpleRef] = None

    class Config:
        from_attributes = True


# Fix forward ref for OrderResponse.lines
OrderResponse.model_rebuild()
