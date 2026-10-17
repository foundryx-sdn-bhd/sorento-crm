"""Pydantic schemas for dynamic list query (filter DSL) and export."""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator


class FilterCondition(BaseModel):
    field_key: str = Field(..., min_length=1)
    op: Literal[
        "eq",
        "ne",
        "contains",
        "starts_with",
        "in",
        "gt",
        "gte",
        "lt",
        "lte",
        "is_null",
    ]
    value: Optional[Any] = None


class FilterGroup(BaseModel):
    op: Literal["and", "or"]
    children: List[Union["FilterGroup", FilterCondition]] = Field(default_factory=list)

    @field_validator("children")
    @classmethod
    def non_empty(cls, v: list) -> list:
        if not v:
            raise ValueError("filter group must have at least one child")
        return v


FilterGroup.model_rebuild()


class ListSearchRequest(BaseModel):
    resource: str = Field(..., min_length=1)
    filter: Optional[FilterGroup] = None
    page: int = Field(1, ge=1)
    limit: int = Field(50, ge=1, le=1000)
    sort: Optional[str] = None
    dir: Optional[Literal["asc", "desc"]] = "asc"
    """Free-text search merged with AND (same behaviour as legacy list `query` param)."""
    quick_search: Optional[str] = None
    # Legacy list filters (merged with AND) - same as existing GET list endpoints
    customer_id: Optional[str] = None
    order_status_id: Optional[str] = None
    has_order_lines: Optional[str] = None
    category_id: Optional[str] = None
    brand_id: Optional[str] = None
    product_status: Optional[str] = None
    price_min: Optional[float] = None
    price_max: Optional[float] = None
    item_type: Optional[str] = None
    # Products "Discontinued at" range (issue #1287), Malaysia-day inclusive.
    discontinued_from: Optional[date] = None
    discontinued_to: Optional[date] = None
    # Workflow forms (list-query + UI quick filters)
    workflow_definition_is_active: Optional[bool] = None
    workflow_form_definition_id: Optional[str] = None
    workflow_submission_state_code: Optional[str] = None
    # Promotions (list-query + UI quick filters)
    promotion_status: Optional[str] = None
    promotion_access_level: Optional[str] = None

    @field_validator("resource")
    @classmethod
    def normalize_resource(cls, v: str) -> str:
        out = (v or "").strip()
        if not out:
            raise ValueError("resource is required")
        return out


class ExportFieldSelection(BaseModel):
    field_key: str = Field(..., min_length=1)


class ListExportRequest(BaseModel):
    resource: str = Field(..., min_length=1)
    filter: Optional[FilterGroup] = None
    quick_search: Optional[str] = None
    fields: List[ExportFieldSelection] = Field(..., min_length=1)
    """When set, export is limited to these ids (ANDed with filters - only rows matching both are returned)."""
    record_ids: Optional[List[str]] = Field(default=None, max_length=5000)
    customer_id: Optional[str] = None
    order_status_id: Optional[str] = None
    has_order_lines: Optional[str] = None
    category_id: Optional[str] = None
    brand_id: Optional[str] = None
    product_status: Optional[str] = None
    price_min: Optional[float] = None
    price_max: Optional[float] = None
    item_type: Optional[str] = None
    # Products "Discontinued at" range (issue #1287), Malaysia-day inclusive.
    discontinued_from: Optional[date] = None
    discontinued_to: Optional[date] = None
    workflow_definition_is_active: Optional[bool] = None
    workflow_form_definition_id: Optional[str] = None
    workflow_submission_state_code: Optional[str] = None
    promotion_status: Optional[str] = None
    promotion_access_level: Optional[str] = None
    # finance_billing_documents (AC-BILLING S2): the list's own filters; `document_type` is
    # comma separated. `customer_id` above is shared.
    document_type: Optional[str] = None
    status: Optional[str] = None
    agent_code: Optional[str] = None
    date_from: Optional[date] = None
    date_to: Optional[date] = None
    open_only: Optional[bool] = None

    @field_validator("resource")
    @classmethod
    def normalize_resource(cls, v: str) -> str:
        out = (v or "").strip()
        if not out:
            raise ValueError("resource is required")
        return out


class ListQueryFieldResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    field_key: str
    label: str
    data_type: str
    compile_key: str
    allowed_operators: List[str]
    filterable: bool
    exportable: bool
    export_column_name: Optional[str] = None
    is_line_field: bool = False
    sort_order: int = 0
    """UI grouping for export dialog: order | line (orders resource only)."""
    export_section: Optional[Literal["order", "line"]] = None
    """Nested group under line: product | warehouse (orders resource only)."""
    export_subgroup: Optional[Literal["product", "warehouse"]] = None
    """UI control: text | number | date | select | multiselect | foreign_key."""
    filter_ui_type: Optional[Literal["text", "number", "date", "select", "multiselect", "foreign_key"]] = None
    """Optional source for select/fk options used by frontend filter builder."""
    option_source: Optional[Dict[str, Any]] = None
    relation_resource_key: Optional[str] = None
    relation_label_field: Optional[str] = None
    is_generated: Optional[bool] = None
    managed_by: Optional[str] = None


class ListQueryResourceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    resource_key: str
    display_name: str
    description: Optional[str] = None


class ListSortEntry(BaseModel):
    """One TanStack sort entry, as persisted."""

    model_config = ConfigDict(extra="forbid")

    id: str
    desc: bool = False


class UserListColumnConfigPayload(BaseModel):
    """
    Per-user per-listing view preferences: columns, sort and filter.

    Stored as JSONB and merged with each listing's default column definitions on the frontend.

    The write is a PARTIAL update. Two independent writers share this one row (see the
    merge in `upsert_list_column_config`), so a key absent from the body is left alone
    and a key present and null is cleared.
    """

    version: int = 1
    columnOrder: Optional[List[str]] = None
    columnVisibility: Optional[Dict[str, bool]] = None
    # TanStack stores per-column widths in its columnSizing state.
    # Persisted as { [columnId]: width } in JSONB.
    columnSizing: Optional[Dict[str, float]] = None
    # Sort IS validated: it becomes an ORDER BY on the listing's next request.
    sorting: Optional[List[ListSortEntry]] = None
    # Filters are deliberately opaque. The shape belongs to the page that wrote it
    # (Stock Inquiries stores {"statuses": [...]}), so typing it here would defeat the
    # point and couple this endpoint to all 37 bespoke filter shapes.
    filters: Optional[Dict[str, Any]] = None
    # The page's own filter-shape version, so a page can detect and discard the blobs
    # its own previous shape wrote.
    filtersVersion: Optional[int] = None
    # S4 (PLAN-scm-reorder-oi-feedback-1sep.md, AC-4.4): the saved view (segment) THIS
    # user wants auto-applied on open, distinct from a listing's PUBLISHED default
    # (`saved_views.is_default`, everyone's). Opaque like `filters` - `SavedViewsMenu`
    # is the only reader/writer, and a listing key with no saved views never touches it.
    defaultSavedViewId: Optional[str] = None
    # PLAN-listing-page-size-memory: rows-per-page, owned by `useListingColumnPreferences`
    # like the three column keys above. A bounded int, NOT a fixed list of sizes - review
    # round 1 found ~45 listings that default their own Rows-per-page menu to 10/15/20 or
    # add 10 to the usual 25/50/100 (roles/role-list.tsx, ReorderPolicyGrid.tsx,
    # OrderInquiryLinesTab.tsx among them), and a fixed Literal[25, 50, 100] 422s every one
    # of them on open. 100 is `MAX_LIST_PAGE_SIZE` (`lib/listNavQuery.ts`), the largest page
    # any list route accepts. The bound only guards the WRITE: a row saved before this
    # bound existed (or restored from a backup) can still hold an out-of-range value, and
    # GET returns it as-is - the FE apply gate drops it on read, same predicate as here.
    pageSize: Optional[int] = Field(default=None, ge=1, le=100)


class UserListColumnConfigResponse(BaseModel):
    listing_key: str
    config: Optional[Dict[str, Any]] = None

