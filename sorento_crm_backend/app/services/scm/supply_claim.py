"""Supply this codebase CREATES is born claimed (G12 write-time rule, captain 2 Sep 2026).

`PLAN-scm-reorder-oi-feedback-1sep.md` G12. A PO/SPO line destined for a PROJECT BIN is
auto-taken only by the sales order that claims it, and the claim may never be written by
the pass that wants to consume the line. On real data that hole cost PO 202607-S0067's 114
units of CB1178A-SS-NL at BRW-IB - bought for SO391853 per the AutoCount book - to
SO381895, which self-claimed them and took them.

So attribution arrives from one of three places, and this module is the third:

  1. the BOOK's own `FromSODocList` column (`po_history` / `po_upload` claims);
  2. a PERSON naming the line in the Link dialog (`manual`);
  3. the SUPPLY WRITER, here, at the moment it creates the line.

(3) exists because a purchase order this codebase raises off the reorder plan is not a
speculative buy: the plan row it came from IS the un-linked remainder of the order-inquiry
rows sitting at that `(product, location)` cell, so the buy is FOR those rows and their
sales orders, and the only honest moment to say so is the transaction that opens the line.
One PO line can be born claimed by SEVERAL sales orders - a 114 at BRW-IB sized by SO X (30)
and SO Y (84) writes two claims - which is ordinary G7 sharing, not a special case: a claim
carries no quantity of its own and reserves the claiming SO line's LIVE outstanding.

**PROJECT BINS ONLY.** A pool line is shared supply by definition; claiming one would
reserve capacity against every other order for no reason G12 gives, and G12's own AC-6.10
says a pool-destination line's candidacy is unchanged. `pool_predicate.is_site_pool` is the
one spelling of that test.
"""
from __future__ import annotations

import logging
from typing import Optional, Sequence

from sqlalchemy.orm import Session

from app.models.inventory import Warehouse
from app.models.procurement import PurchaseOrder, PurchaseOrderLine
from app.services.scm import order_link_service
from app.services.scm.pool_predicate import is_project_reserve, is_site_pool

logger = logging.getLogger(__name__)

#: What a claim written here is stamped with. Held apart from `order_inquiry` (the audit
#: echo of a link) so the netting can tell a standalone reservation from a double count,
#: and so the one-shot repair of the withdrawn born-claimed mechanism can tell a real
#: attribution from the cascade's own helping itself.
SOURCE = order_link_service.SOURCE_CRM_SUPPLY


def claim_created_supply(
    db: Session,
    *,
    row_ids: Sequence[str],
    document: str,
    company_id: Optional[str] = None,
    po_line_id: Optional[str] = None,
    spo_allocation_id: Optional[str] = None,
) -> int:
    """Claim ONE created supply line for the order-inquiry rows it was created for.

    Returns how many order-inquiry ROWS were attributed to it - which is not the same as
    the number of claim rows written, because two rows of one sales order share a claim
    (the identity is company + SO + PO + item). It is a report of work done, not a count of
    inserts: `order_link_service.claim_placed_on_po` is get-or-create on
    `uq_scm_order_link_claim_identity`, so a re-run writes nothing new, and a pairing the
    BOOK already states keeps its own `po_history` / `po_upload` source.

    The rows are read through `ProjectOrderInquiryService`, which owns the (SO number, item
    code, core sales-order line) identity a claim is written under. Imported inside the
    function because that service imports this package at module level.
    """
    if not row_ids or not document:
        return 0
    from app.models.project_so import OrderInquiryRow
    from app.services.project_order_inquiry_service import ProjectOrderInquiryService

    service = ProjectOrderInquiryService(db)
    rows = (
        db.query(OrderInquiryRow)
        .filter(OrderInquiryRow.id.in_([str(rid) for rid in row_ids]))
        .all()
    )
    attributed = 0
    for row in rows:
        so_number, item_code, core_line_id = service.claim_identity(row)
        if not so_number:
            continue
        order_link_service.claim_placed_on_po(
            db,
            company_id=row.company_id if company_id is None else company_id,
            so_number=so_number,
            po_number=document,
            item_code=item_code,
            so_line_id=core_line_id,
            po_line_id=po_line_id,
            spo_allocation_id=spo_allocation_id,
            source=SOURCE,
        )
        attributed += 1
    return attributed


def claim_purchase_order_for_sizing_rows(db: Session, po: PurchaseOrder) -> int:
    """Every PROJECT-BIN line of a purchase order this codebase raised is claimed by the
    order-inquiry rows that sized its plan cell.

    The `(product, warehouse)` cell is the plan row, and `rows_needed_at_by_cell` is the
    same reader the confirm's own first cascade pass already uses to decide who the buy was
    for (`PLAN-scm-purchasing-uat-journey.md` P7) - so the claim and the placement agree by
    construction rather than by two lookups that happen to match today.

    Returns the number of order-inquiry ROWS attributed across every line of the order -
    a report of work done rather than a count of claim inserts, for the reason
    `claim_created_supply` gives.

    A line at a pool is skipped (AC-6.10) and a line whose cell nothing sized writes no
    claim: an unattributed project-bin line is a real state, counted by
    `project_bin_lock.count_unclaimed_project_bin_lines` for Joey to backfill, and inventing
    an owner for it here would be the very thing G12 forbids.
    """
    lines = [ln for ln in po.lines if ln.product_id]
    if not lines or not po.po_number:
        return 0
    warehouse_ids = {str(ln.warehouse_id) for ln in lines if ln.warehouse_id}
    if not warehouse_ids:
        return 0
    segments = {
        str(wid): segment
        for wid, segment in db.query(Warehouse.id, Warehouse.segment).filter(
            Warehouse.id.in_(list(warehouse_ids))
        )
    }
    # A PROJECT RESERVE (HQ: a pool with `segment = project`) is general project stock,
    # claimed by nobody, exactly like an ordinary pool line (`pool_predicate`).
    pools = {
        str(row[0])
        for row in db.query(Warehouse.pool_warehouse_id).filter(
            Warehouse.pool_warehouse_id.in_(list(warehouse_ids))
        )
    }
    bins = [
        ln
        for ln in lines
        if ln.warehouse_id
        and not is_site_pool(segments.get(str(ln.warehouse_id)))
        and not is_project_reserve(
            segments.get(str(ln.warehouse_id)), is_pool=str(ln.warehouse_id) in pools
        )
    ]
    if not bins:
        return 0

    from app.services.project_order_inquiry_service import ProjectOrderInquiryService

    cells = sorted({(str(ln.product_id), str(ln.warehouse_id)) for ln in bins})
    by_cell = ProjectOrderInquiryService(db).rows_needed_at_by_cell(cells)
    attributed = 0
    for line in bins:
        row_ids = by_cell.get((str(line.product_id), str(line.warehouse_id))) or []
        attributed += claim_created_supply(
            db,
            row_ids=row_ids,
            document=po.po_number,
            company_id=line.company_id,
            po_line_id=str(line.id),
        )
    return attributed


__all__ = [
    "SOURCE",
    "claim_created_supply",
    "claim_purchase_order_for_sizing_rows",
]
