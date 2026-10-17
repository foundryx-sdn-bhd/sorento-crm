"""What counts as incoming supply on an SPO allocation. ONE rule, four readers.

`scm.on_order_v`, the fulfilment ladder's question 1 (`project_supply_service._spo_rows` and
`_group_water`, which also feed the board's cell popover), the order inquiry's inbound pool
and the coverage screen's "already on order" figure all have to answer the same question, or
the planner is shown one number and the engine decides on another. The clauses live here so
there is one copy in Python; the view repeats them in SQL and names this module.

**TRUST THE BOOK** (captain's ruling, 26 August 2026). An SPO line is incoming supply while
it is OPEN and something is still to come on it - allocated minus received above zero, a
receipt status that is not `fully_received`, a line status that is not closed - and it stays
supply until the re-uploaded PO & SPO book says it arrived. A promised date that has passed
does NOT remove it. The book is the record of what was bought and what is still owed; a date
is a promise about when, and a supplier being late is not evidence that the goods stopped
existing. Dropping 715 open lines because their dates are old would tell the planner to buy
39,110 units a second time.

What a passed date DOES change is the wording, not the arithmetic. The row is stated as
OVERDUE wherever it is named - "SPO-2026/08-0061 arrives on 1 Aug 2026 (overdue 25 days)" -
so a buyer reading the popover or the order-inquiry row can see which promise is being
leaned on and go and chase it. That is the honest treatment: visible, not silently dropped
and not silently counted as if it were fresh.

LADDER V5 (section 1e, and the water ruling of 27 August 2026) reads these rows twice, and
the two readings are deliberately different:

* the ownership GROUP's net counts every open row with NO arrival-date term at all, because
  it states the group's position rather than any one line's promise;
* what a LINE may draw off that water is only the share arriving on or before its own
  required date. A past date always satisfies that test, so an overdue promise still covers
  its line and is named with its date; a row dated after the line, or carrying no date at
  all, is named in question 1's own sentence and drawn by nobody.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from sqlalchemy import and_, func, or_, select, text

from app.models.inventory import Warehouse
from app.models.procurement import (
    InboundShipment,
    PurchaseOrder,
    PurchaseOrderLine,
    SPOAllocation,
    Supplier,
)
from app.services.scm.pool_predicate import active_site_pool_sql

#: Receipt statuses that mean the goods are in. `fully_received` is the value the column's
#: own CHECK constraint allows; `received` is kept beside it because migration 337's view
#: was written against that spelling, which no row has ever carried - a `<> 'received'`
#: test that can never be false is a filter that looks like a guard and is not one.
RECEIVED_RECEIPT_STATUSES = ("fully_received", "received")


def open_incoming_clauses() -> tuple:
    """The three tests a row must pass to be supply, for a query that has outer-joined
    `InboundShipment` onto `SPOAllocation.inbound_shipment_id`.

    The quantity test (`allocated > received`) is deliberately NOT here: two of the four
    readers apply it in Python on the row they have already read, and one of those nets it
    against a running balance. It is stated once per reader, beside the arithmetic it
    belongs to.
    """
    return (
        # A closed line has left the book, the same way a closed purchase line has.
        or_(SPOAllocation.line_status.is_(None), SPOAllocation.line_status == "open"),
        or_(
            SPOAllocation.receipt_status.is_(None),
            SPOAllocation.receipt_status.notin_(RECEIVED_RECEIPT_STATUSES),
        ),
        # Landed is not incoming. Only a shipment can say a row has landed; an SPO with no
        # container booked has nothing that could have arrived.
        or_(InboundShipment.id.is_(None), InboundShipment.actual_arrival_date.is_(None)),
    )


def visible_line_clauses() -> tuple:
    """The one test a row must pass to be SHOWN in a listing (PLAN-hide-retired-spo-lines,
    R1/R2): NOT (retired AND never received) - `retired_at IS NOT NULL AND
    coalesce(quantity_received, 0) = 0` is the hidden set, everything else is visible.

    This is a different question from `open_incoming_clauses()` above. That one asks
    "is this line still supply a planner may count on" - closed, fully received or
    landed lines all answer no, and stay entirely correct rows that simply are not
    incoming anything more. This one asks "did AutoCount ever have this line, as far
    as the document is concerned" - R1: a retired line is one AutoCount stopped
    naming (the leftover sweep, or a DocKey change under the same `spo_number`), so it
    is hidden from the document rather than merely marked closed. R2: a retired line
    that carries a receipt (`quantity_received > 0`) stays visible regardless - stock
    physically arrived against it, and D28c freezes that receipt on retirement, so
    hiding the row would hide real goods that already landed.

    A row that is merely `closed` (fully received, or closed for any reason with no
    `retired_at`) is UNAFFECTED - it has no `retired_at`, so it is visible either way.

    Readers: `procurement_service.SPOAllocationService.list_allocations`,
    `list_allocations_grouped_by_shipment`, `list_allocations_grouped_by_spo_number`,
    `list_documents` (and its `total_allocated` / `total_received` rollups),
    `get_document` (its lines list and the same two rollups),
    `InboundShipmentService.compute_shipment_line_figures` (the stored shipment line
    STATUS, PL-STATUS-ROLLUP; its stored allocated figure stays unfiltered), and
    `spo_last_receipt_service.last_receipt_rows` (the chatbot's "last in" - a line the
    document no longer shows must not be able to answer as the newest one).
    """
    return (
        or_(
            SPOAllocation.retired_at.is_(None),
            func.coalesce(SPOAllocation.quantity_received, 0) > 0,
        ),
    )


def is_visible_allocation(allocation: "SPOAllocation") -> bool:
    """The Python twin of `visible_line_clauses()`, for a reader that already holds
    loaded `SPOAllocation` ORM objects rather than building a query (R7, round 2
    security review) - `spo_conversion_service.planner_state`'s DISPLAY, filtering the
    writer-facing, deliberately-unfiltered rows `_own_state` hands back. Kept as the
    one restatement of the same rule rather than a second inline copy, so the two can
    never drift the way `open_incoming_clauses()`'s SQL/Python halves are already
    warned about above.
    """
    return allocation.retired_at is None or float(allocation.quantity_received or 0) > 0


def supplier_id_for(allocation=SPOAllocation):
    """The supplier an order inquiry names for an SPO line (OI-SUPPLIER-FROM-PO, owner, 9 Oct
    2026: "the supplier always take from PO, not the SPO"): the purchase order's own
    supplier, the allocation's own `supplier_id` only when no purchase order can be found.

    The PO is the one the line draws down (`po_line_id`, a Sorento-raised SPO) or, for a
    book-fed line that carries only the AutoCount text, the one `from_po_number` names in
    the same company (PO numbers are unique per company). A correlated SQL expression, so
    a list query keeps one round trip; pass an alias when the query aliases the table.
    """
    via_line = (
        select(PurchaseOrder.supplier_id)
        .select_from(PurchaseOrderLine)
        .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderLine.purchase_order_id)
        .where(PurchaseOrderLine.id == allocation.po_line_id)
        .limit(1)
        .correlate(allocation)
        .scalar_subquery()
    )
    via_number = (
        select(PurchaseOrder.supplier_id)
        .where(
            PurchaseOrder.po_number == allocation.from_po_number,
            PurchaseOrder.company_id == allocation.company_id,
        )
        .limit(1)
        .correlate(allocation)
        .scalar_subquery()
    )
    return func.coalesce(via_line, via_number, allocation.supplier_id)


class MaskedEta(date):
    """An SPO's delayed ETA, for a viewer who may not see `eta_delay_date`.

    Owner ruling 17 Oct 2026: the delay date is a `procurement.packing_lists.view_clearance`
    field, but the engine plans on it for EVERY viewer, so what the board suggests is what a
    Save commits. This value therefore compares, sorts and subtracts as the delayed date
    (it IS that date to the engine) and prints as `published`, the ETA everyone may see.
    `shown_date` turns it into the published date wherever a response is assembled.

    Built only by a read that prints dates to such a viewer (`ProjectSupplyService` with
    `reveal_eta_delay=False`); a write never receives one.
    """

    published: Optional[date]

    def __new__(cls, delayed: date, published: Optional[date]):
        value = super().__new__(cls, delayed.year, delayed.month, delayed.day)
        value.published = published
        return value

    def __reduce__(self):
        return (MaskedEta, (date(self.year, self.month, self.day), self.published))

    def replace(self, *args, **kwargs) -> date:
        return date(self.year, self.month, self.day).replace(*args, **kwargs)

    def _plain(self) -> date:
        return date(self.year, self.month, self.day)

    # `date` rebuilds a subclass as `cls(y, m, d)` after arithmetic, which this one cannot
    # take: shifting by days keeps the mask, both dates moved alike.
    def __add__(self, other):
        if isinstance(other, timedelta):
            published = self.published + other if self.published else None
            return MaskedEta(self._plain() + other, published)
        return NotImplemented

    __radd__ = __add__

    def __sub__(self, other):
        if isinstance(other, timedelta):
            return self + (-other)
        if isinstance(other, date):
            return self._plain() - other
        return NotImplemented

    def _printed(self) -> str:
        return self.published.isoformat() if self.published else ""

    def isoformat(self) -> str:
        return self._printed()

    def __str__(self) -> str:
        return self._printed()

    def __repr__(self) -> str:
        return f"MaskedEta(published={self.published!r})"

    def __format__(self, spec: str) -> str:
        return format(self.published, spec) if self.published else ""

    def strftime(self, fmt: str) -> str:
        return self.published.strftime(fmt) if self.published else ""


def shown_date(value: Optional[date]) -> Optional[date]:
    """The date a viewer is shown: the published ETA in place of a masked delay."""
    if isinstance(value, MaskedEta):
        return value.published
    return value


def overdue_days(arrival_date: Optional[date], as_of: Optional[date] = None) -> int:
    """How many days late a promised arrival is. 0 when it is today, ahead, or unstated.

    The one copy of this arithmetic. It decides only how a row is WORDED - the row is supply
    either way - so a reader that does not name its rows on screen has no need to call it.
    """
    if arrival_date is None:
        return 0
    reference = as_of or date.today()
    return max((reference - arrival_date).days, 0)


def spo_history_for_product(db, run_id: str, product_id: str,
                            warehouse_id: Optional[str] = None) -> dict:
    """The SPO book behind a plan row's SPO cell: open first, then what has landed.

    PLAN-po-spo-site-pool-and-order-sheet-downloads.md, S2 (AC-7, AC-8): scoped to the
    ONE site-pool rule (`pool_predicate.active_site_pool_sql`). The old pool-id resolution
    (`COALESCE(w.pool_warehouse_id, w.id)` off the run's recommendation rows for this
    product) narrowed to whichever locations happened to be in THIS run's plan basis - a
    warehouse that holds a shipment but no committed demand of its own was invisible even
    though it is real site-pool supply the product-grain cell must sum.

    Review fix round B (reviewer S1, AC-7 amended): a LOCATION-grain row's modal must read
    that row's OWN warehouse only - the same grain rule `po_book_service._po_book_sql`
    already applies (its `pr.warehouse_id IS NOT NULL` branch). Resolution:

    1. `warehouse_id` given (the caller already knows which row/location it is opening -
       the route/FE passes the plan row's own `warehouse_id` for a location-grain line) -
       that warehouse alone, and only when it passes `active_site_pool_sql` (a project bin
       explicitly named answers empty, never falls back to a wider read).
    2. Omitted - every active site-pool warehouse, product-wide (unchanged since S2).

    A caller-supplied `warehouse_id` is what tells the two grains apart, mirroring
    `purchase_trend_service.purchase_trend_for_run`'s own `warehouse_id` kwarg - the same
    "the caller who already resolved the row's grain names its warehouse; the function
    itself does not re-derive it from `scm.reorder_recommendation`" shape. An
    auto-detecting third branch (infer location-grain from this run's OWN recommendation
    rows when no `warehouse_id` is given) was tried and dropped: no red test exercises it,
    and the one available signal - any rec for this run/product carrying a non-NULL
    `warehouse_id` - misclassifies `tests/scm/test_spo_history_to_pool.py`'s fixture
    (`_revamp_fixtures.recommendation()` always stamps a location, even on a run whose
    `decision_grain` is `'product'`), breaking 4 of its 5 already-green tests. Flagged to
    the captain rather than implemented against no evidence (PRINCIPLES.md, "simplest
    thing that works").

    `open` uses the one rule in this module (`open_incoming_clauses` plus "something is
    still to come"); everything else the site pool has ever been promised is history.
    Both are newest-promise-first, so a buyer reads the next arrival at the top.
    """
    wh_filter = [warehouse_id] if warehouse_id else None

    # ORM, not raw SQL, for two reasons. The company isolation filter runs on ORM
    # execution only, and this reads a company-owned table (`SPOAllocation` is
    # `CompanyScopedMixin`) - raw SQL here would have listed another company's shipping
    # orders. And the open/closed test is `open_incoming_clauses()` itself, evaluated in
    # SQL and returned per row, rather than a second Python copy of the same three
    # conditions that could drift from the module they are meant to share.
    query = (
        db.query(
            SPOAllocation.spo_number,
            Supplier.supplier_name,
            SPOAllocation.allocated_quantity,
            SPOAllocation.quantity_received,
            SPOAllocation.expected_date,
            InboundShipment.actual_arrival_date,
            and_(*open_incoming_clauses()).label("is_open"),
        )
        .join(Warehouse, Warehouse.id == SPOAllocation.warehouse_id)
        .outerjoin(Supplier, Supplier.id == SPOAllocation.supplier_id)
        .outerjoin(InboundShipment,
                   InboundShipment.id == SPOAllocation.inbound_shipment_id)
        .filter(
            SPOAllocation.product_id == product_id,
            text(active_site_pool_sql("warehouses")),
            # R7: a retired line is omitted from both legs below, not only labelled
            # by `open_incoming_clauses()` above - that tuple is a SELECTED LABEL
            # here (`is_open`), never a filter, so without this a retired line would
            # still land in "open" or "history".
            *visible_line_clauses(),
        )
    )
    if wh_filter is not None:
        query = query.filter(SPOAllocation.warehouse_id.in_(wh_filter))
    rows = query.order_by(SPOAllocation.expected_date.desc().nullslast(),
                          SPOAllocation.spo_number).all()

    open_rows: list[dict] = []
    history: list[dict] = []
    for r in rows:
        allocated = float(r.allocated_quantity or 0)
        received = float(r.quantity_received or 0)
        arrived = r.actual_arrival_date
        # The quantity test is the reader's own half of the rule (see
        # `open_incoming_clauses`' docstring): something still has to be to come.
        still_open = bool(r.is_open) and allocated > received
        entry = {
            "spo_number": r.spo_number,
            "supplier_name": r.supplier_name,
            "qty": allocated,
            "received_qty": received,
            "eta": r.expected_date.isoformat() if r.expected_date else None,
            "arrived_at": arrived.isoformat() if arrived else None,
            "status": _shipment_status(still_open, allocated, received, arrived),
        }
        (open_rows if still_open else history).append(entry)
    return {"open": open_rows, "history": history}


def _shipment_status(still_open: bool, allocated: float, received: float, arrived) -> str:
    """What the row IS, in the reader's words - never the raw column value.

    Two different columns say a shipment has landed (`receipt_status` and the shipment's
    own arrival date), and the book carries both spellings of "received"
    (`RECEIVED_RECEIPT_STATUSES`), so printing either one raw would show the same state
    under three different names in one table.
    """
    if still_open:
        return "Partly received" if received > 0 else "On the water"
    if arrived is not None and received <= 0:
        return "Arrived"
    if 0 < received < allocated:
        return "Partly received"
    return "Received"
