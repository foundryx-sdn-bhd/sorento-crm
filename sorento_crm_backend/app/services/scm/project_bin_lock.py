"""G12's own count: OPEN project-bin PO/SPO lines nobody has claimed at all
(`PLAN-scm-reorder-oi-feedback-1sep.md` S6, AC-6.11).

Company-wide and always current, never scoped to one upload's own rows: a re-export's
`FromSODocList` notes can resolve a claim onto a line an EARLIER upload wrote, so what
matters to Joey is what remains unclaimed after this pass runs, not what this pass alone
touched. Surfaced on the PO/SPO upload result and as a PO-view filter, so the backfill
has a number to chase to zero rather than a silent gap.

"Claimed" here is the EXACT COMPLEMENT of what the lock opens on (S4, review of PR #490),
and it did not use to be. The count asked "does any claim row name this line", while
`_dedication_for_target` - the lock itself - opens a line only for a claim that is
RESOLVED (it joins through `so_line_id`, so an unresolved claim is invisible to it) and
whose sales order line is still UNSETTLED (a delivered or cancelled order reserves nothing
and dedicates nothing, G7). The two disagreed in exactly the direction that wastes Joey's
time: he chased the count to zero in AutoCount and the lines stayed locked, because the
claims he had written had never resolved onto a sales order this database holds.

So a line counts as CLAIMED here only when a claim names it, that claim has found its
sales-order line, and that line is open. Anything else is what the automatic pass will
still refuse, which is what the number is for.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from app.models.inventory import Warehouse
from app.models.order import SalesOrderLine
from app.models.procurement import InboundShipment, PurchaseOrderLine, SPOAllocation
from app.models.scm import OrderLinkClaim
from app.services.scm import spo_supply
from app.services.scm.pool_predicate import PROJECT_SEGMENT


def _is_project_bin():
    """`segment = project` AND nobody's pool. A pool with `segment = project` is a PROJECT
    RESERVE (HQ, `pool_predicate.is_project_reserve`): general project stock, not one named
    project's bin, so its lines carry no G12 lock (`PLAN-hq-project-reserve-12oct.md`)."""
    member = aliased(Warehouse)
    return (Warehouse.segment == PROJECT_SEGMENT) & ~(
        select(member.id).where(member.pool_warehouse_id == Warehouse.id).exists()
    )


def _unlocking_claim_exists(target_clause):
    """EXISTS a claim that would actually OPEN this line to the automatic pass.

    The same three conditions `_dedication_for_target` applies, said in SQL: the claim
    names the line, it has RESOLVED onto a core sales-order line (dedication reads only
    claims it can join through `so_line_id`), and that line is still OPEN with something
    left to deliver (a settled order reserves nothing and dedicates nothing, G7).
    """
    return (
        select(OrderLinkClaim.id)
        .select_from(OrderLinkClaim)
        .join(SalesOrderLine, SalesOrderLine.id == OrderLinkClaim.so_line_id)
        .where(
            target_clause,
            SalesOrderLine.line_status == "open",
            func.coalesce(SalesOrderLine.qty_ordered, 0)
            - func.coalesce(SalesOrderLine.qty_delivered, 0)
            > 0,
        )
        .exists()
    )


def count_unclaimed_project_bin_lines(
    db: Session, company_id: Optional[str] = None
) -> int:
    """Every OPEN PO line or SPO allocation at a `segment = 'project'` warehouse that
    no claim names - the number G12's lock refuses to the automatic cascade until a
    claim exists, whether Joey's next re-export writes one or a buyer links it by hand.
    """
    po_query = (
        db.query(PurchaseOrderLine.id)
        .join(Warehouse, Warehouse.id == PurchaseOrderLine.warehouse_id)
        .filter(
            PurchaseOrderLine.line_status == "open",
            _is_project_bin(),
            ~_unlocking_claim_exists(OrderLinkClaim.po_line_id == PurchaseOrderLine.id),
        )
    )
    spo_query = (
        db.query(SPOAllocation.id)
        .join(Warehouse, Warehouse.id == SPOAllocation.warehouse_id)
        .outerjoin(
            InboundShipment, InboundShipment.id == SPOAllocation.inbound_shipment_id
        )
        .filter(
            *spo_supply.open_incoming_clauses(),
            _is_project_bin(),
            ~_unlocking_claim_exists(
                OrderLinkClaim.spo_allocation_id == SPOAllocation.id
            ),
        )
    )
    if company_id:
        po_query = po_query.filter(PurchaseOrderLine.company_id == company_id)
        spo_query = spo_query.filter(SPOAllocation.company_id == company_id)
    return po_query.count() + spo_query.count()


def has_unclaimed_project_bin_line(db: Session):
    """The PO-view filter's EXISTS clause (AC-6.11): does THIS purchase order carry at
    least one open line this same rule counts - the same predicate `count_unclaimed_
    project_bin_lines` sums company-wide, correlated instead to one `PurchaseOrder` row
    so `purchase_order_service.list` can filter on it the same way `allocated` already
    does.
    """
    from app.models.procurement import PurchaseOrder

    return (
        db.query(PurchaseOrderLine.id)
        .join(Warehouse, Warehouse.id == PurchaseOrderLine.warehouse_id)
        .filter(
            PurchaseOrderLine.purchase_order_id == PurchaseOrder.id,
            PurchaseOrderLine.line_status == "open",
            _is_project_bin(),
            ~_unlocking_claim_exists(OrderLinkClaim.po_line_id == PurchaseOrderLine.id),
        )
        .exists()
    )
