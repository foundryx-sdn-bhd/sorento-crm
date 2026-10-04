"""Who writes which `orders` column once AutoCount DO ingest owns a delivery order.

A delivery order is AutoCount-owned when `orders.doc_key IS NOT NULL` (set by the DO ingest,
including adoption of an upload-created row by its number). On such a row:

- AutoCount owns every column the DO ingest writes into the header
  (`autocount_doc_ingest_service._apply_do`) plus its order lines. Order Tracking uploads, the
  JSON bulk import and manual edits never overwrite them.
- Order Tracking owns every other business column: delivery fields, Remarks CS, Type, and
  everything AutoCount never sends. AutoCount ingest never writes them.

A row with `doc_key IS NULL` (RMA, documents AutoCount never sends) is written by Order
Tracking and manual edits in full, as before.

Every `orders` column sits in exactly one of the three sets below
(tests/test_order_field_ownership.py checks it against the model), so a new column has to pick
an owner. Plan: documentation/plans/autocount/PLAN-do-ownership-guard.md.
"""
from __future__ import annotations

from typing import Iterable

from app.services.error_handler import AppException

AUTOCOUNT_OWNED_ORDER_COLUMNS = frozenset({
    # identity and provenance
    "order_number", "source_book", "doc_key", "source_modified_at", "source_vanished_at",
    "source_record",
    # DO header as AutoCount sends it
    "order_date", "created_time", "debtor_code", "debtor_name", "agent", "is_cancelled",
    "remarks", "branch_code", "branch_name", "deliver_address", "deliver_contact",
    "deliver_phone", "ship_via", "ship_info", "ref", "ref_doc_no", "description", "doc_status",
    "currency_code", "currency_rate", "local_net_total",
    "subtotal_amount", "tax_amount", "total_amount",
    # resolved from what AutoCount sends (DebtorCode, RefDocNo)
    "customer_id", "sales_order_id",
    # AutoCount never sends a discount, but its Total is already net of it and every writer
    # recomputes total_amount = subtotal - discount + tax, so a discount edit rewrites
    # AutoCount's total.
    "discount_amount",
})

ORDER_TRACKING_OWNED_ORDER_COLUMNS = frozenset({
    # Master sheet columns AutoCount does not send
    "remarks_cs", "order_type",
    # computed from the Master sheet Date (owner ruling 1 Oct 2026: keep as is)
    "estimated_delivery_date",
    # Overall Tracking sheet
    "actual_delivery_date", "pickup_time", "checker", "transporter", "transporter_id",
    "driver_name", "lorry_plate", "customer_ref", "delivery_remarks_cs", "delivery_remarks",
    "salesman", "trips", "warehouse", "delivery_days", "kpi_warning", "order_status_id",
    # written by nothing but a manual edit
    "billing_address_id", "shipping_address_id",
})

# Bookkeeping no business writer owns.
SYSTEM_ORDER_COLUMNS = frozenset({
    "id", "company_id", "created_by", "updated_by", "created_at", "updated_at", "deleted_at",
    "synced_to_excel", "last_synced_to_excel", "last_synced_at",
})

AUTOCOUNT_OWNED = "AUTOCOUNT_OWNED"


def is_autocount_owned(order) -> bool:
    return getattr(order, "doc_key", None) is not None


def autocount_owned_keys(order, keys: Iterable[str]) -> list[str]:
    """The keys AutoCount owns on `order`, in the order given. Empty for a row it does not own."""
    if not is_autocount_owned(order):
        return []
    return [k for k in keys if k in AUTOCOUNT_OWNED_ORDER_COLUMNS]


def assert_autocount_writes(columns: Iterable[str]) -> None:
    """The DO ingest's own check that its header write stays inside what AutoCount owns."""
    stray = sorted(set(columns) - AUTOCOUNT_OWNED_ORDER_COLUMNS)
    if stray:
        raise RuntimeError(f"AutoCount DO ingest would write Order Tracking columns: {stray}")


def reject_autocount_owned_edit(order, keys: Iterable[str]) -> None:
    """409 naming every AutoCount-owned field a manual edit of an AutoCount DO tries to set."""
    owned = autocount_owned_keys(order, keys)
    if owned:
        raise AppException(
            status_code=409,
            message=f"{', '.join(owned)}: owned by AutoCount, cannot be edited on this delivery order",
            detail=",".join(owned),
            code=AUTOCOUNT_OWNED,
        )


def reject_autocount_line_edit(order) -> None:
    """409 for any manual add, edit or delete of an AutoCount DO's lines (the ingest rewrites them)."""
    if is_autocount_owned(order):
        raise AppException(
            status_code=409,
            message="lines: owned by AutoCount, cannot be edited on this delivery order",
            detail="lines",
            code=AUTOCOUNT_OWNED,
        )
