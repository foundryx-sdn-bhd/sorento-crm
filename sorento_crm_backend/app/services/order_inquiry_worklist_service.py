"""Purchasing's own order inquiry: every raised row, across every subject.

**Why this exists next to `project_order_inquiry_service`.** That one reads the rows of
ONE project. Purchasing does not work a project at a time, and more to the point it
cannot: an order adopted from the AutoCount book has `project_id` NULL by design
(`PLAN-fulfilment-planning-from-autocount-so.md` section 2), so its rows belong to no
project and no per-project query will ever return them. Confirming supply on an adopted
order raised rows that were reachable only from the one sales order that raised them.

**The shape is not invented here.** It is the workbook purchasing already works from -
`JAN - DEC 2026 ORDER.xlsx`, sheet title `ORDER INQUIRY`, columns SO DATE / S/O NO / ITEM
CODE / QTY / TOTAL QTY / DELIVERY DATE / PROJECT/CUSTOMER / SUPPLIER / PO NO, one sheet
per delivery MONTH plus a tab per day showing what was raised that day. The columns are
read off that file rather than retyped, the month is the primary axis rather than one
filter among many, and the export writes the same workbook back out.

**SUPPLIER and PO NO are never guessed.** On their sheet a blank in those two columns
means "not placed yet", and that is exactly what the system knows: the only link the
schema holds between an inquiry row and a placed purchase order is the row's SPO
reference, through `spo_allocations.po_line_id`. A row with no such link prints blank
rather than the product's usual supplier, because purchasing reads that column as a
statement that an order exists.

**Each of the screen's own controls is computed ignoring its own filter** (the month
strip, the supplier list, the project list, the people who raised them). A control that
empties itself the moment it is used cannot be used a second time. The visible TOTALS
honour every filter, month included, because that strip is a statement about what is on
screen.
"""
from __future__ import annotations

import io
import logging
from datetime import date, datetime
from decimal import Decimal
from itertools import groupby
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy import Date, String, case, cast, func, or_, select, text
from sqlalchemy.dialects.postgresql import aggregate_order_by
from sqlalchemy.orm import Session, aliased

from app.models.base import get_company_scope
from app.models.inventory import Warehouse
from app.models.order import Customer, SalesOrder, SalesOrderLine
from app.models.procurement import (
    InboundShipment,
    PurchaseOrder,
    PurchaseOrderLine,
    SPOAllocation,
    Supplier,
)
from app.models.product import Product
from app.models.project_so import (
    ACK_ACKNOWLEDGED,
    ACK_AWAITING,
    ACK_CHANGED,
    ACK_LINKABLE,
    ACK_REJECTED,
    ACK_STATES,
    INQUIRY_ACTIONED,
    INQUIRY_CANCELLED,
    INQUIRY_PLACED,
    INQUIRY_RAISED,
    INQUIRY_PARTLY_LINKED,
    IV_ORDER,
    IV_ORDER_BACK,
    IV_RESERVE_AND_ORDER,
    OrderInquiry,
    OrderInquiryLink,
    OrderInquiryRaise,
    OrderInquiryReserveRequest,
    OrderInquiryReserveRequestRow,
    OrderInquiryRow,
    ProjectSalesOrder,
    ProjectSalesOrderLine,
    SOSupplyDecision,
)
from app.models.projects import Project, ProjectParty, ProjectPurchaseOrder
from app.models.sales_agent import SalesAgent
from app.models.user import User
from app.services.company_scope import build_company_predicate
from app.services.error_handler import AppException
from app.services.product_companion_service import (
    bundled_with_item_codes_map,
    resolve_bundled_item_codes,
)
from app.services.project_order_inquiry_service import (
    ProjectOrderInquiryService,
    arrives_outside_window,
    derived_spo_open_clauses,
    project_customer_label,
    project_title_with_note,
)
from app.services.project_supply_service import ProjectSupplyService
from app.services.scm import order_link_service, priority
from app.services.scm.front_planning_engine import DEFAULT_LEAD_TIME_DAYS
from app.services.scm.raise_event_matching import nearest_raise_event

logger = logging.getLogger(__name__)

_ZERO = Decimal("0")

#: The states pre-seeded at zero on the summary strip: every state a row can be in,
#: `partly_linked` and `placed` included (section 3.I). Declared on the schema too
#: (`OrderInquiryStateCounts`), because `response_model` silently drops a key it has not
#: been told about - the strip reported four states while the rows carried six.
INQUIRY_STATES = (
    INQUIRY_RAISED,
    INQUIRY_PARTLY_LINKED,
    INQUIRY_ACTIONED,
    INQUIRY_CANCELLED,
    INQUIRY_PLACED,
)

#: The four acknowledgement states, pre-seeded at zero on the facet so a state nothing is
#: in still offers itself in the filter (`PLAN-scm-oi-handshake.md` section 4). Read off
#: the model's own tuple rather than retyped, so a fifth one cannot arrive here unnoticed.
ACK_FILTER_STATES = ACK_STATES

#: The page's DEFAULT view (R3, `PLAN-scm-oi-draft-links.md`): the rows purchasing still
#: has to answer. NOT a stored state - no row is ever `to_confirm` - so it is a filter
#: value and a facet count over the two states that make it up, and nothing else in the
#: system has to learn a fifth word.
ACK_TO_CONFIRM = "to_confirm"
ACK_TO_CONFIRM_STATES = (ACK_AWAITING, ACK_CHANGED)
ACK_FILTER_VALUES = tuple(ACK_FILTER_STATES) + (ACK_TO_CONFIRM,)

#: Every column the list renders is sortable, and this is the CLOSED SET. An unknown
#: value is a 422 rather than a silent fall back to the default, for the same reason it
#: is on the fulfilment worklist: a grid drawing a sort arrow on a column the server
#: ignored is a screen lying about what it is showing.
#:
#: The route declares the same names as a `Literal` (FastAPI cannot build one from a
#: runtime set) and a test asserts the two agree.
SORTABLE_FIELDS = frozenset(
    {
        "inquiry_no",
        "so_date",
        "so_number",
        "item_code",
        "product_name",
        "qty",
        "delivery_date",
        "project_customer",
        "customer_name",
        "project_title",
        "supplier",
        "po_number",
        "state",
        "raised_at",
        "raised_by_name",
        "location",
        "agent",
        # The three columns the worklist grid draws a sort arrow on under a DIFFERENT
        # id than an existing key, or under no key at all (18 Sep 2026 bug report): the
        # FE sends its own column id verbatim as `sort`, so the id is what has to be
        # accepted, not a renaming of it.
        "spo_number",
        "agent_code",
        "verb",
    }
)

#: Delivery date ascending, because that is the order the work is due in and the order
#: their own sheets are in. Sorting is an override of this, never a replacement for
#: having one.
DEFAULT_SORT_FIELD = "delivery_date"

#: Their tab names, not the browser's: `JAN 26`, `JUNE 26`, `JULY 26`, `SEPT 26`. Read
#: off the workbook, because a renamed tab is a tab their own habits stop finding.
MONTH_WORD = (
    "JAN",
    "FEB",
    "MAR",
    "APR",
    "MAY",
    "JUNE",
    "JULY",
    "AUG",
    "SEPT",
    "OCT",
    "NOV",
    "DEC",
)

EXPORT_TITLE = "ORDER INQUIRY"
#: Where the rows that have no delivery date go. They are not dropped: an instruction
#: with no date is still an instruction, and one that vanishes from the file is one
#: nobody places.
EXPORT_UNDATED_SHEET = "NO DATE"
#: Read off `JAN - DEC 2026 ORDER.xlsx` row 2, trailing space and all. Retyping it
#: tidier is how a column their filters no longer find gets introduced.
EXPORT_HEADINGS = (
    "SO DATE",
    "S/O NO",
    "ITEM CODE",
    "QTY",
    "TOTAL QTY",
    "DELIVERY DATE",
    "PROJECT/CUSTOMER",
    "SUPPLIER",
    "PO NO ",
    "LOCATION",
    # APPENDED, never inserted: their own filters and habits are keyed on the columns
    # above being where they have always been (`PLAN-scm-oi-handshake.md` section 4).
    "ACKNOWLEDGED",
    # Fix round (22 Sep): parity with the grid's own S3 columns (AC-D15) - the SAME
    # row-level Taken/Remaining the grid has shown since S3, never the retired
    # LINE-scoped `taken_from_po`/`remaining_open` pair. APPENDED for the same reason
    # ACKNOWLEDGED was (review round, 22 Sep): they first landed BETWEEN Location and
    # Acknowledged, which pushed a column purchasing's own filters already point at one
    # place to the right.
    "TAKEN",
    "REMAINING",
    # AC-LT-39 (G9, `PLAN-oi-links-autocount-truth-24sep.md` 3.5): the cascade's own
    # guess, beside PO and SPO on every other surface - PO NO above stays real-links
    # only. APPENDED for the same reason ACKNOWLEDGED/TAKEN/REMAINING were: their own
    # filters are keyed on the columns before it being where they have always been.
    "SUGGESTED",
)

# The two routes a row can be attributed by, joined ONCE through a coalesce rather than
# twice through two aliases of `customers`. The precedence is the same either way - the
# billed project party first, the core sales order's own customer when there is none -
# and one join keeps the company-scope predicate correct: `with_loader_criteria` emits an
# UNALIASED `customers.company_id` into each aliased ON clause, which Postgres rejects
# outright ("invalid reference to FROM-clause entry").
_CUSTOMER_ID = func.coalesce(ProjectParty.customer_id, SalesOrder.customer_id)

# The purchase order this row's FIRST link sits on - what the Supplier column reads and
# what the row's `po_id` addresses. Off the LINKS since section 3.I: a row holds many now,
# and `order_inquiry_rows.po_line_id` is the derived display of the first one rather than
# the record. Ordered by when the link was made, so "first" is an order in time.
#
# Correlated EXPLICITLY - an auto-correlated `exists`/scalar subquery loses its FROM
# clauses the moment this query is reshaped (the `shipment_supplier_predicate` lesson).
_LINKED_PO_ID = (
    select(PurchaseOrderLine.purchase_order_id)
    .select_from(OrderInquiryLink)
    .join(PurchaseOrderLine, PurchaseOrderLine.id == OrderInquiryLink.po_line_id)
    .where(OrderInquiryLink.row_id == OrderInquiryRow.id)
    .order_by(OrderInquiryLink.linked_at.asc(), OrderInquiryLink.id.asc())
    .limit(1)
    .correlate(OrderInquiryRow)
    .scalar_subquery()
)
# The row's OWN link on an SPO, and through it the purchase order that shipping order
# draws down (when the book ever names one - `po_line_id` is NULL on every migrated SPO
# allocation, so this is the path a system-raised SPO takes and no other).
_SPO_LINKED_PO_ID = (
    select(PurchaseOrderLine.purchase_order_id)
    .select_from(OrderInquiryLink)
    .join(SPOAllocation, SPOAllocation.id == OrderInquiryLink.spo_allocation_id)
    .join(PurchaseOrderLine, PurchaseOrderLine.id == SPOAllocation.po_line_id)
    .where(OrderInquiryLink.row_id == OrderInquiryRow.id)
    .order_by(OrderInquiryLink.linked_at.asc(), OrderInquiryLink.id.asc())
    .limit(1)
    .correlate(OrderInquiryRow)
    .scalar_subquery()
)
# The row's OWN `spo_ref` - the coverage reference the netting engine writes on an
# ALREADY INBOUND / PRE-ORDERED row (`ProjectOrderInquiryService._write`), which is not a
# placement at all and never became a link. Kept as the last leg: those rows are traceable
# to a purchase order through the shipping order that covers them, and dropping the leg
# when placements moved to the links table would have blanked their Supplier and PO no
# columns for a reason that has nothing to do with linking.
_SPO_REF_PLACED_PO_ID = (
    select(PurchaseOrderLine.purchase_order_id)
    .select_from(SPOAllocation)
    .join(PurchaseOrderLine, PurchaseOrderLine.id == SPOAllocation.po_line_id)
    .where(SPOAllocation.spo_number == OrderInquiryRow.spo_ref)
    .limit(1)
    .correlate(OrderInquiryRow)
    .scalar_subquery()
)
_PLACED_PO_ID = func.coalesce(
    _LINKED_PO_ID, _SPO_LINKED_PO_ID, _SPO_REF_PLACED_PO_ID
)
# AC-FB-52: the row's OWN first SPO link's own `supplier_id` - a genuine book-chain SPO
# allocation the ESB raised straight off the shipping-order feed (`follow_book_for_rows`,
# S1-S3) never resolves back to a `po_line_id` at all, so `_PLACED_PO_ID` (and through it
# the Supplier join below) comes back NULL for it even though the allocation states its
# own supplier - measured at 5,156 such rows on the prod copy, every one reading "Not
# linked" under Supplier. Same "first link, ordered by when it was made" rule as
# `_SPO_LINKED_PO_ID` above, read directly off the allocation rather than through a
# purchase order line that, for this shape, does not exist.
_SPO_LINKED_SUPPLIER_ID = (
    select(SPOAllocation.supplier_id)
    .select_from(OrderInquiryLink)
    .join(SPOAllocation, SPOAllocation.id == OrderInquiryLink.spo_allocation_id)
    .where(
        OrderInquiryLink.row_id == OrderInquiryRow.id,
        SPOAllocation.supplier_id.isnot(None),
    )
    .order_by(OrderInquiryLink.linked_at.asc(), OrderInquiryLink.id.asc())
    .limit(1)
    .correlate(OrderInquiryRow)
    .scalar_subquery()
)

# The row's OWN first linked SPO number - a REAL link only
# (`OrderInquiryLink.spo_allocation_id`), ordered the same way every other "first link"
# reader here is: earliest `linked_at` then `id`. The sort key for `spo_number` (18 Sep
# 2026 bug report).
#
# This does NOT match what the SPO cell itself prints (measured against a prod copy, 18
# Sep 2026: 33 rows differ one way, 10 the other). The cell also shows a SYNTHETIC
# `derived: true` entry - a PO link whose PO carries its own open SPO allocation for the
# same product, marked "via PO" (`OrderInquiryLinkOut.derived`, S5/R-E) - which this key
# ignores, and the cell never reads a bare `spo_ref` at all, which this key falls back to
# when the row has no own link. Both are ACCEPTED, KNOWN differences, not a bug to fix
# here: folding the derived leg in would sort the row by a placement never actually made
# ON it (`_SPO_LINKED_PO_ID`'s sibling reasoning), and dropping the `spo_ref` fallback
# would sort a row raised before links existed as blank. The rule is "own SPO link
# first, then `spo_ref`, blanks last" - stated on its own terms, not as a match to the
# cell.
_OWN_LINKED_SPO_NUMBER = (
    select(SPOAllocation.spo_number)
    .select_from(OrderInquiryLink)
    .join(SPOAllocation, SPOAllocation.id == OrderInquiryLink.spo_allocation_id)
    .where(OrderInquiryLink.row_id == OrderInquiryRow.id)
    .order_by(OrderInquiryLink.linked_at.asc(), OrderInquiryLink.id.asc())
    .limit(1)
    .correlate(OrderInquiryRow)
    .scalar_subquery()
)
_SPO_SORT_KEY = func.coalesce(_OWN_LINKED_SPO_NUMBER, OrderInquiryRow.spo_ref)

#: Does this row hold a link of each kind? The "Linked" filter's own predicates (AC-I5),
#: stated once so the filter and the column cannot disagree about what "linked to a PO"
#: means.
_HAS_PO_LINK = (
    select(OrderInquiryLink.id)
    .where(
        OrderInquiryLink.row_id == OrderInquiryRow.id,
        OrderInquiryLink.po_line_id.isnot(None),
    )
    .correlate(OrderInquiryRow)
    .exists()
)
_HAS_SPO_LINK = (
    select(OrderInquiryLink.id)
    .where(
        OrderInquiryLink.row_id == OrderInquiryRow.id,
        OrderInquiryLink.spo_allocation_id.isnot(None),
    )
    .correlate(OrderInquiryRow)
    .exists()
)
_HAS_ANY_LINK = (
    select(OrderInquiryLink.id)
    .where(OrderInquiryLink.row_id == OrderInquiryRow.id)
    .correlate(OrderInquiryRow)
    .exists()
)


def _row_has_link(*where) -> Any:
    """An EXISTS on `order_inquiry_links`, correlated to the row, for whatever extra
    clauses the caller names (S1, R-K: `po_number`/`spo_number` narrow to a link of one
    kind whose own `document` starts with the typed text). The same shape `_HAS_PO_LINK`
    / `_HAS_SPO_LINK` already are, generalised so a filter does not need its own copy.
    """
    return (
        select(OrderInquiryLink.id)
        .where(OrderInquiryLink.row_id == OrderInquiryRow.id, *where)
        .correlate(OrderInquiryRow)
        .exists()
    )


def _linked_qty(*where) -> Any:
    """How much of this row sits on documents, correlated to the row itself.

    One builder for the three sums the cards need (SPO, PO, everything), so the three
    cannot come apart from each other or from `_HAS_PO_LINK` / `_HAS_SPO_LINK` above.
    """
    return (
        select(func.coalesce(func.sum(OrderInquiryLink.qty), 0))
        .where(OrderInquiryLink.row_id == OrderInquiryRow.id, *where)
        .correlate(OrderInquiryRow)
        .scalar_subquery()
    )


#: The three quantities the strip's cards are (section 3.I2, AC-I11): what sits on SPO
#: allocations, what sits on purchase order lines, and the remainder nobody has put
#: anywhere - which is what still flows to reorder planning.
#:
#: NEVER NEGATIVE. A row linked beyond its own quantity is a data question, and a negative
#: Buy would quietly cancel out a real one somewhere else in the same total.
#:
#: `_UNLINKED_QTY` is a per-row remainder and says nothing about whether that remainder is
#: still OWED - `_NOT_OWED_STATES` below is what answers that, and every reader of this
#: column applies it.
_SPO_LINKED_QTY = _linked_qty(OrderInquiryLink.spo_allocation_id.isnot(None))
_PO_LINKED_QTY = _linked_qty(OrderInquiryLink.po_line_id.isnot(None))
#: PLAN-oi-request-cs-reserve.md 3.4 (AC-RS-12): what CS has reserved off this row - a
#: THIRD link target beside PO/SPO, never counted into either of the two above (a reserve
#: link's `po_line_id`/`spo_allocation_id` are both null by the widened CHECK, so this is
#: purely additive, not a re-split of the same links).
_RESERVED_LINKED_QTY = _linked_qty(OrderInquiryLink.reserve_request_row_id.isnot(None))
#: AC-RS-20: an OPEN reserve request row exists for THIS row - its own answer still
#: unset AND its parent request still `requested` - the chip reads `requested` while
#: this is true, whatever `_RESERVED_LINKED_QTY` above already holds from an earlier
#: cycle (R5: reserved then requested again on the balance still reads `requested`).
#:
#: Round 4 fix (`PLAN-oi-request-cs-reserve.md` 6e.1, AC-RS-76): the row's own
#: `qty_reserved IS NULL` check is NEW here - round 1-3's per-row route answered every
#: row of a request in lockstep (the request's own `state` alone was an accurate proxy
#: for "this row's own answer is still open"), but `commit_request` can now answer PART
#: of a request in one click while the parent stays `requested` until its LAST row is
#: done - without this, an ALREADY-answered row in that same still-open request kept
#: reading `requested` (and the Lines grid kept offering `Reserve`/`Edit reserve`
#: instead of `Amend reserve`/`History`) until every sibling row was also answered.
_HAS_OPEN_RESERVE_REQUEST = (
    select(OrderInquiryReserveRequestRow.id)
    .select_from(OrderInquiryReserveRequestRow)
    .join(
        OrderInquiryReserveRequest,
        OrderInquiryReserveRequest.id == OrderInquiryReserveRequestRow.request_id,
    )
    .where(
        OrderInquiryReserveRequestRow.row_id == OrderInquiryRow.id,
        OrderInquiryReserveRequestRow.qty_reserved.is_(None),
        OrderInquiryReserveRequest.state == "requested",
    )
    .correlate(OrderInquiryRow)
    .exists()
)
#: Round 4 (`PLAN-oi-request-cs-reserve.md` 6e.2): the OPEN request row's own
#: `qty_requested` for this row - the Lines grid's `Request to reserve N` pill and the
#: tick action's default stage both need the AMOUNT asked, not only the fact one is
#: open. Same join as `_HAS_OPEN_RESERVE_REQUEST` above, one column instead of `exists`.
_OPEN_REQUEST_QTY = (
    select(OrderInquiryReserveRequestRow.qty_requested)
    .select_from(OrderInquiryReserveRequestRow)
    .join(
        OrderInquiryReserveRequest,
        OrderInquiryReserveRequest.id == OrderInquiryReserveRequestRow.request_id,
    )
    .where(
        OrderInquiryReserveRequestRow.row_id == OrderInquiryRow.id,
        # Round 4 fix - same reason `_HAS_OPEN_RESERVE_REQUEST` above needs it: an
        # ALREADY-answered row in a request that stays `requested` because a SIBLING
        # row is still open must not keep reading its own `qty_requested` back as if
        # it were still open too.
        OrderInquiryReserveRequestRow.qty_reserved.is_(None),
        OrderInquiryReserveRequest.state == "requested",
    )
    .correlate(OrderInquiryRow)
    # 6e.4: one open request row per line is `create_request`'s own rule (no DB
    # constraint spans the two tables), so this reads one row rather than trusting it.
    .order_by(OrderInquiryReserveRequest.ordinal.desc())
    .limit(1)
    .scalar_subquery()
)
#: 6e.4 (AC-RS-78c): the LATEST answered request row's own `qty_reserved` for this
#: row - `0` means CS declined it (`Reserve 0`, or amended down to 0), which the Lines
#: grid reads as `Not reserved` with Amend + History. NULL when nothing was answered.
_LATEST_ANSWERED_QTY = (
    select(OrderInquiryReserveRequestRow.qty_reserved)
    .select_from(OrderInquiryReserveRequestRow)
    .join(
        OrderInquiryReserveRequest,
        OrderInquiryReserveRequest.id == OrderInquiryReserveRequestRow.request_id,
    )
    .where(
        OrderInquiryReserveRequestRow.row_id == OrderInquiryRow.id,
        OrderInquiryReserveRequestRow.qty_reserved.isnot(None),
    )
    .correlate(OrderInquiryRow)
    .order_by(OrderInquiryReserveRequest.ordinal.desc())
    .limit(1)
    .scalar_subquery()
)
#: PLAN-oi-cancelled-line-used-confirm.md (AC-CL-4/5): whether the row's own sales order
#: line is cancelled, NULL-safe - a row whose mirror names no core line reads False here,
#: not NULL, so `~_LINE_CANCELLED` in a WHERE clause still matches it instead of silently
#: dropping it.
_LINE_CANCELLED = case(
    (SalesOrderLine.line_status == "cancelled", True), else_=False
)
#: The row's own quantity, UNCAPPED (owner ruling R1, 23 Sep 2026,
#: `PLAN-oi-order-rows-uncapped.md`): "this is delivered already and we want to replenish,
#: I don't mind order back or order, as long as it needs to order". SO421985 raised three
#: ORDER rows of 493, the AutoCount pull then marked the lines delivered 493/493 and closed
#: the order, and the worklist's Remaining still read 493 while `scm.committed_v` read 0 -
#: a raised, unlinked row is buy demand until purchasing links it, whatever the line's own
#: delivered column says. That retires the 7.3 / 14 Sep cap (SO368872 / SRTWC286-SH, "Buy
#: never exceeds what the line still owes") this used to apply to a plain ORDER row, and the
#: `verb == ORDER_BACK` exception #1114 (22 Sep) carved out of it - both verbs are now the
#: SAME expression, `OrderInquiryRow.qty` itself, so this no longer reads `SalesOrderLine`
#: at all. ONE expression, used by the Buy card, the `kind=buy` filter and the Remaining
#: column, so the three cannot answer differently for one row; `scm.committed_v` and the
#: plan's horizon SQL carry the same rule as `demand._OWED_SQL`.
_ROW_QTY = OrderInquiryRow.qty
#: PLAN-scm-supplied-with-companions.md ruling 7 excludes only a row's OWN `bundled_qty`
#: from the cards - the item it rides ON (the host) still needs buying independently of
#: whether a companion happens to ride inside its line: CKS1050 unlinked qty 1 is Buy 1
#: whether or not CKSW015 rides on it. No cross-row subtraction here (UAC D5, corrected).
_UNLINKED_QTY = func.greatest(
    _ROW_QTY - _linked_qty() - OrderInquiryRow.bundled_qty, 0
)
#: The three STAGES a unit passes through, left to right (R-F): not yet on any document,
#: on a purchase order line but not yet on a shipment, already on an SPO (own link or a
#: derived one via its linked PO). A unit counts once, the furthest stage it reached.
#:
#: The other two are built PER CALL, on the service - `OrderInquiryWorklistService.
#: _incoming_qty()` and `_purchased_qty()` - because the derived leg they read is
#: company-scoped and the scope lives on the session (AC-D14), which no module-level
#: expression has.

#: S1b (`PLAN-oi-replan-received-links.md`): the SAME verb set the cascade's own
#: linkable-row predicate reads (`project_order_inquiry_service._LINKABLE_VERBS`,
#: `auto_place_for_products`) - a repoint suggestion never names a row the cascade
#: itself could not have drafted onto.
_SUGGESTION_LINKABLE_VERBS = (IV_ORDER, IV_RESERVE_AND_ORDER, IV_ORDER_BACK)


#: The ANCHOR row's own item code, for a bundled row with no document of its own
#: (export D8: "the bundled row's document column names its host, not a blank").
_BundleAnchor = aliased(OrderInquiryRow)
_BUNDLE_ANCHOR_ITEM_CODE = (
    select(_BundleAnchor.item_code)
    .where(_BundleAnchor.id == OrderInquiryRow.bundled_with_row_id)
    .correlate(OrderInquiryRow)
    .scalar_subquery()
)

#: The two states whose quantity is NOT OWED any more, so neither the three cards nor the
#: `kind` filter counts them: `cancelled` was called off, and `actioned` has already been
#: answered somewhere else. `_quantity_flow_by_so_line` below and `scm.committed_v` both
#: drop `actioned` for exactly this reason, so counting an actioned row's remainder here
#: would put a quantity on the Buy card that reorder planning itself does not carry - and
#: purchasing would buy it twice.
_NOT_OWED_STATES = (INQUIRY_CANCELLED, INQUIRY_ACTIONED)

# `SO DATE` is the date on the DOCUMENT. For an adopted order that is the core sales
# order's own order date; an authored one that has never been to AutoCount falls back to
# when it published, and then to when it was written, so the column is never empty for a
# row that plainly has a date somewhere.
_SO_DATE = func.coalesce(
    SalesOrder.order_date,
    cast(ProjectSalesOrder.published_at, Date),
    cast(ProjectSalesOrder.created_at, Date),
)
_SO_NUMBER = func.coalesce(
    ProjectSalesOrder.autocount_doc_no, ProjectSalesOrder.provisional_ref
)

#: How many words of the search box are actually applied. Ten is far past what anybody
#: types and far short of what a pasted paragraph would cost: each token is its own OR
#: across eleven columns of a joined query.
_MAX_QUERY_TOKENS = 10
#: The LIKE escape character. Backslash, declared to Postgres per predicate rather than
#: relied on: `standard_conforming_strings` decides whether a bare one is even special.
_LIKE_ESCAPE = "\\"


def _escape_like(token: str) -> str:
    """A typed word as a LITERAL. `%` and `_` are SQL wildcards, not search syntax: typed
    into the box they answered a different question from the one that was asked - `50%`
    matched anything with 50 in it, `_` matched any character at all."""
    return (
        token.replace(_LIKE_ESCAPE, _LIKE_ESCAPE * 2)
        .replace("%", f"{_LIKE_ESCAPE}%")
        .replace("_", f"{_LIKE_ESCAPE}_")
    )


_CUSTOMER_NAME = Customer.customer_name
# The Project column's text. A registered project wins; an adopted AutoCount order
# (`project_id` NULL by design) falls back to the free-text label the core sales order's
# detail page already prints (`SalesOrder.project_label`, `app/services/
# project_label_rules.py`). PLAN-oi-project-label-from-so.md.
_PROJECT_TITLE = func.coalesce(Project.title, SalesOrder.project_label)
# What `PROJECT/CUSTOMER` sorts and filters on. The printed label starts with the
# customer when there is one and with the project when there is not, so this is the same
# ordering the eye reads down the column.
_PROJECT_CUSTOMER = func.coalesce(_CUSTOMER_NAME, _PROJECT_TITLE)
_RAISED_AT = OrderInquiryRow.created_at
# The per-day tab is a MALAYSIAN day. `created_at` is stored naive UTC (the session runs
# with `timezone=utc`), so a row raised at 00:30 MYT belongs to the tab of the day that
# is still, in UTC, the evening before.
_RAISED_DAY = cast(
    func.timezone("Asia/Kuala_Lumpur", func.timezone("UTC", _RAISED_AT)), Date
)
# WHO told purchasing to buy THIS ROW. Per ROW, not per header, and the difference is the
# whole point: the header's `raised_by` is RE-STAMPED on every reconfirm (AC-H4), so
# reading it here would print the latest reconfirmer's name beside an older row's own
# clock - a row A raised on 12 Aug would read "B, 12/08 10:25" the moment B reconfirmed
# one other line. The row's own answer is the revision that raised it:
# `supply_decision_id` -> `so_supply_decisions.confirmed_by`, the same person the header
# stamps at that moment (PLAN section 3.H).
#
# S2 (AC-OH-20..23, `PLAN-oi-worklist-one-header.md`): a row with no decision at all is
# NOT necessarily the header's own answer either. Every row born since G4 is born
# acknowledged by its raiser - a confirm's own raise, `_write`'s amendment path, the
# importer's migration - so `OrderInquiryRow.acknowledged_by` is that row's own person,
# read before the header falls back to whoever last re-stamped it. Only a row with
# NEITHER a decision NOR an acknowledger (there is none once G4 shipped, but the column
# is nullable) reaches the header's `raised_by`.
#
# Accepted edge case (Opus review round 1): `acknowledge_rows` stamps `acknowledged_by`
# with the ACKNOWLEDGER, not the raiser, on a row it finds still `awaiting` -
# reachable today only by a pre-G4 row nobody has taken on yet (there is no FE press
# onto that route any more). Once acknowledged, this column reads as "raised by" the
# person who took it on rather than whoever actually raised it. Narrow and one-way
# (a born-acknowledged row never reaches that branch), so left as a known quirk of the
# handful of legacy rows still in that state rather than a reason to add a second
# column to tell the two apart.
#
# The id never leaves the service: a screen printing a UUID at a buyer is a screen they
# cannot use, so the filter takes an id and every read gives a name.
_RAISED_BY_ID = func.coalesce(
    SOSupplyDecision.confirmed_by, OrderInquiryRow.acknowledged_by, OrderInquiry.raised_by
)
_RAISED_BY_NAME = User.name

# `raise_history` (PLAN-oi-worklist-split-customer-project.md, Slice 2, owner 18 Sep): on
# a re-confirm the carry site cancels the old row and raises a fresh one under the SAME
# `order_inquiry_id` (`project_order_inquiry_service._write`, "the inquiry is deliberately
# reused") - so Raised at jumps to the re-confirm time and the row that actually carries
# the FIRST raise is the one this call just cancelled. ONLY a CANCELLED predecessor is
# history (Opus review round 1, B1): an OPEN sibling row on the same SO line is a second
# LIVE instruction, not a superseded one, and reading it as history marked 168 live
# duplicates as "previously raised" against 1 genuine supersede on a look at prod data.
# So this is the cancelled carry-predecessor today, and whatever IT in turn cancelled
# before that - never an open row. Each entry's raiser reads the same rule `_RAISED_BY_ID`
# reads for the row itself, aliased so it is answered per HISTORICAL row rather than the
# page row.
_RAISE_HISTORY_ROW = aliased(OrderInquiryRow)
_RAISE_HISTORY_INQUIRY = aliased(OrderInquiry)
_RAISE_HISTORY_DECISION = aliased(SOSupplyDecision)
_RAISE_HISTORY_USER = aliased(User)
_RAISE_HISTORY_RAISED_BY_ID = func.coalesce(
    _RAISE_HISTORY_DECISION.confirmed_by,
    _RAISE_HISTORY_ROW.acknowledged_by,
    _RAISE_HISTORY_INQUIRY.raised_by,
)
# One correlated `json_agg` per page row (never N+1): a row with no SO line (`so_line_id`
# IS NULL) matches nothing on either side of that equality - not even another null, SQL's
# usual rule - so it answers `[]` for free, with no separate branch needed.
_RAISE_HISTORY = (
    select(
        func.coalesce(
            func.json_agg(
                aggregate_order_by(
                    func.json_build_object(
                        "raised_at",
                        _RAISE_HISTORY_ROW.created_at,
                        "raised_by_name",
                        _RAISE_HISTORY_USER.name,
                    ),
                    _RAISE_HISTORY_ROW.created_at.desc(),
                )
            ),
            text("'[]'::json"),
        )
    )
    .select_from(_RAISE_HISTORY_ROW)
    .join(
        _RAISE_HISTORY_INQUIRY,
        _RAISE_HISTORY_INQUIRY.id == _RAISE_HISTORY_ROW.order_inquiry_id,
    )
    .outerjoin(
        _RAISE_HISTORY_DECISION,
        _RAISE_HISTORY_DECISION.id == _RAISE_HISTORY_ROW.supply_decision_id,
    )
    .outerjoin(
        _RAISE_HISTORY_USER, _RAISE_HISTORY_USER.id == _RAISE_HISTORY_RAISED_BY_ID
    )
    .where(
        _RAISE_HISTORY_ROW.order_inquiry_id == OrderInquiryRow.order_inquiry_id,
        _RAISE_HISTORY_ROW.so_line_id == OrderInquiryRow.so_line_id,
        _RAISE_HISTORY_ROW.created_at < OrderInquiryRow.created_at,
        _RAISE_HISTORY_ROW.state == INQUIRY_CANCELLED,
    )
    .correlate(OrderInquiryRow)
    .scalar_subquery()
)
_RAISE_HISTORY_COLUMN = _RAISE_HISTORY.label("raise_history")
# Where the PO gets placed for, not where the item is bought TO. `stock_location` on the
# row is stamped once, at raise time: the DONOR the take left oversold for an order-back
# row, or the confirmed allocation's warehouse for a plan/confirmed row
# (`project_order_inquiry_service._stock_location`). A plain Buy row raised straight off
# the board has neither, so it falls back to the fulfilment warehouse already on the
# line's own core sales order line - still a real answer, just not one purchasing
# confirmed themselves. Blank only when the row traces to no line at all.
_LOCATION = func.coalesce(OrderInquiryRow.stock_location, Warehouse.warehouse_code)

#: WHO acknowledged the row and who rejected it, by name. Two aliases of `users`, because
#: the same query already joins that table once for the person who RAISED the row and the
#: three answers are different people. Both OUTER and both on a primary key, so a row
#: nobody has acknowledged - or one whose acknowledger has since been removed - still
#: reaches the list.
_ACK_USER = aliased(User)
_REJECT_USER = aliased(User)

_SORT_EXPRESSIONS = {
    "inquiry_no": OrderInquiry.inquiry_no,
    "so_date": _SO_DATE,
    "so_number": _SO_NUMBER,
    "item_code": OrderInquiryRow.item_code,
    "product_name": Product.product_name,
    "qty": OrderInquiryRow.qty,
    "delivery_date": OrderInquiryRow.delivery_date,
    "project_customer": _PROJECT_CUSTOMER,
    "customer_name": _CUSTOMER_NAME,
    "project_title": _PROJECT_TITLE,
    "supplier": Supplier.supplier_name,
    "po_number": PurchaseOrder.po_number,
    "state": OrderInquiryRow.state,
    "raised_at": _RAISED_AT,
    "raised_by_name": _RAISED_BY_NAME,
    "location": _LOCATION,
    "agent": SalesAgent.sales_agent,
    # The FE column ids these three sort as - `agent_code` reads the same column
    # `agent` already does, `verb` and `spo_number` are new (18 Sep 2026 bug report).
    "agent_code": SalesAgent.sales_agent,
    "verb": OrderInquiryRow.verb,
    "spo_number": _SPO_SORT_KEY,
}

_COLUMNS = (
    OrderInquiryRow.id.label("id"),
    # `OI-000123`, off the header this row already joins to - no second query, and no
    # second opinion about which inquiry a row belongs to. The S/O no cannot stand in for
    # it: an amendment raises a SECOND inquiry on the same sales order.
    OrderInquiry.inquiry_no.label("inquiry_no"),
    # PLAN-oi-bundled-row-host-change.md: the key `_host_changes_for_rows` groups a
    # bundled row's HOST rows by, on the SAME order inquiry header.
    OrderInquiryRow.order_inquiry_id.label("order_inquiry_id"),
    OrderInquiryRow.so_line_id.label("so_line_id"),
    OrderInquiryRow.item_code.label("item_code"),
    OrderInquiryRow.qty.label("qty"),
    OrderInquiryRow.delivery_date.label("delivery_date"),
    OrderInquiryRow.state.label("state"),
    OrderInquiryRow.verb.label("verb"),
    OrderInquiryRow.note.label("note"),
    OrderInquiryRow.cited_document.label("cited_document"),
    # S3 (`PLAN-oi-cascade-skip-early-arrival.md`): the last of the three fields
    # `ProjectOrderInquiryService._cited_documents` reads, so `_attach_link_
    # suggestions` can call that SAME reader on the row it already holds rather than
    # re-deriving what "cited" means a second time.
    OrderInquiryRow.spo_ref.label("spo_ref"),
    # PLAN-scm-supplied-with-companions.md S5.
    OrderInquiryRow.bundled_qty.label("bundled_qty"),
    OrderInquiryRow.bundled_with_row_id.label("bundled_with_row_id"),
    OrderInquiryRow.company_id.label("company_id"),
    _BUNDLE_ANCHOR_ITEM_CODE.label("bundled_with_item_code"),
    _RAISED_AT.label("raised_at"),
    _RAISED_BY_NAME.label("raised_by_name"),
    _SO_DATE.label("so_date"),
    _SO_NUMBER.label("so_number"),
    Product.id.label("product_id"),
    Product.product_name.label("product_name"),
    _CUSTOMER_NAME.label("customer_name"),
    Project.id.label("project_id"),
    _PROJECT_TITLE.label("project_title"),
    ProjectSalesOrder.id.label("project_sales_order_id"),
    ProjectSalesOrder.is_pre_order.label("is_pre_order"),
    SalesOrder.id.label("core_sales_order_id"),
    # AC-B6-7 (`PLAN-board-oi-mechanical-22sep.md`, S6): the "SO line" cell resolves
    # `/scm/sales-orders/<core_sales_order_id>?tab=lines&line=<core_line_id>`, so the only
    # NEW column the deep link needs is the core LINE's own id - the sales-order half is
    # `core_sales_order_id` above, which the cell already reads. A second label for that
    # same column (review round, 22 Sep) put one fact on the wire under two names. Null
    # when the mirror has no core line.
    SalesOrderLine.id.label("core_line_id"),
    # Fix round (22 Sep): AutoCount's own line number, for the S/O no cell's `SO402757 ·
    # L5` label (`orderInquirySoLineLabel`) - the SAME `SalesOrderLine` join `core_line_id`
    # above already reads, so this adds no join of its own.
    SalesOrderLine.line_no.label("line_no"),
    # `PLAN-oi-no-double-count-25sep.md` S1 (AC-ND-21): the SO Qty and No. the sales
    # order's own Lines grid shows, off the same two line joins; the mirror's own figures
    # only when it has no core line.
    func.coalesce(SalesOrderLine.qty_ordered, ProjectSalesOrderLine.qty).label("so_line_qty"),
    func.coalesce(SalesOrderLine.line_no, ProjectSalesOrderLine.line_no).label("so_line_no"),
    Supplier.id.label("supplier_id"),
    Supplier.supplier_name.label("supplier"),
    PurchaseOrder.id.label("po_id"),
    PurchaseOrder.po_number.label("po_number"),
    _LOCATION.label("location"),
    SalesAgent.sales_agent.label("agent_code"),
    SalesAgent.person_label.label("agent_label"),
    # The handshake (`PLAN-scm-oi-handshake.md`). Every one of them on the wire, because
    # the column prints a different sentence per state and the filter counts all four.
    OrderInquiryRow.ack_state.label("ack_state"),
    OrderInquiryRow.acknowledged_at.label("acknowledged_at"),
    OrderInquiryRow.rejected_at.label("rejected_at"),
    OrderInquiryRow.rejected_reason.label("rejected_reason"),
    OrderInquiryRow.changed_at.label("changed_at"),
    # The Was half of a CHANGED row's Was / Now table, as figures. The row's note carries
    # the same fact as a sentence for a person; nothing parses that sentence.
    OrderInquiryRow.previous_qty.label("previous_qty"),
    OrderInquiryRow.previous_delivery_date.label("previous_delivery_date"),
    OrderInquiryRow.previous_item_code.label("previous_item_code"),
    # AC-RL-16 (`PLAN-oi-replan-received-links.md` S3): reaches the wire so the Qty
    # cell's `redirected` mark can read it.
    OrderInquiryRow.redirected_to_pool.label("redirected_to_pool"),
    # PLAN-oi-cancelled-line-used-confirm.md (AC-CL-1): off the SAME `SalesOrderLine`
    # outer join the location fallback already uses - no second join for this.
    _LINE_CANCELLED.label("line_cancelled"),
    _ACK_USER.name.label("acknowledged_by_name"),
    _REJECT_USER.name.label("rejected_by_name"),
    # PLAN-oi-request-cs-reserve.md 3.4/3.5 (AC-RS-12/AC-RS-20).
    _RESERVED_LINKED_QTY.label("reserved_qty"),
    _HAS_OPEN_RESERVE_REQUEST.label("has_open_reserve_request"),
    # PLAN-oi-request-cs-reserve.md 6e.2: the open request row's own `qty_requested`.
    _OPEN_REQUEST_QTY.label("requested_qty"),
    # 6e.4 (AC-RS-78c): the latest answer, so a declined line reads `declined`.
    _LATEST_ANSWERED_QTY.label("latest_answered_qty"),
    # PLAN-oi-worklist-split-customer-project.md, Slice 2: the Raised at column's own
    # tooltip. `_write_sheet` never reads this key, but `_EXPORT_COLUMNS` below drops the
    # label outright (S2, review round 1) - the export runs this `json_agg` for every row
    # of the whole unpaged set otherwise, a cost nobody behind that column asked for.
    _RAISE_HISTORY_COLUMN,
)
# The export's own column set (S2, review round 1): everything `_COLUMNS` selects EXCEPT
# `raise_history` - `_write_sheet` never reads that key, so the export ran a `json_agg`
# per row of the whole unpaged set for nothing. Identity comparison (`is not`), not `!=`:
# a `Label` has no meaningful equality of its own to compare by value.
_EXPORT_COLUMNS = tuple(
    column for column in _COLUMNS if column is not _RAISE_HISTORY_COLUMN
)


def _dec(value: Any) -> Decimal:
    if value is None:
        return _ZERO
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except Exception:  # noqa: BLE001 - a malformed stored number is data, not a crash
        return _ZERO


def _qty_str(value: Decimal) -> str:
    """`600`, not `600.0000`. ``normalize()`` alone turns 100 into `1E+2`."""
    return format(_dec(value).normalize(), "f")


#: Fix round (22 Sep, AC-D15 parity): the export's own Taken/Remaining, read off the SAME
#: serialized row dict the grid's own `linked_qty`/`qty`/`bundled_qty` come from - so the
#: file and the screen can never print two different numbers for one row. Mirrors the FE's
#: `inquiryRowTaken`/`inquiryRowRemaining` (`orderInquiryWorklist.ts`) exactly, including
#: the buy-verb gate (`_SUGGESTION_LINKABLE_VERBS`, the same set the FE's
#: `TAKEN_REMAINING_VERBS` names): `-` on a notice row (it never carries a link of its
#: own), `0` on a row or line already cancelled.
#:
#: Review round 2, B1 (`PLAN-oi-request-cs-reserve.md` section 7): `linked_qty`
#: deliberately excludes a reserve link (`links_for_rows`'s own AC-RS-12 note), so
#: `reserved_qty` (the serializer's own separate field) is the only other place that
#: quantity can come from - both readers add it in, the same way the FE's own
#: `inquiryRowTakenQty` does.
def _export_taken_qty(row: Dict[str, Any]) -> Decimal:
    return _dec(row.get("linked_qty")) + _dec(row.get("reserved_qty"))


def _export_taken(row: Dict[str, Any]) -> str:
    if row.get("verb") not in _SUGGESTION_LINKABLE_VERBS:
        return "-"
    return _qty_str(_export_taken_qty(row))


def _export_remaining(row: Dict[str, Any]) -> str:
    if row.get("verb") not in _SUGGESTION_LINKABLE_VERBS:
        return "-"
    if row.get("state") == "cancelled" or row.get("line_cancelled"):
        return "0"
    remaining = (
        _dec(row.get("qty")) - _export_taken_qty(row) - _dec(row.get("bundled_qty"))
    )
    return _qty_str(max(remaining, _ZERO))


def _export_suggested(row: Dict[str, Any]) -> str:
    """AC-LT-39 (G9): the export's own Suggested cell - the same document/qty the
    worklist's Suggested column would print, kept simple for a spreadsheet cell
    rather than the badge the screen shows. `-` on a row nothing has been guessed
    for, several entries joined with `; ` on the rare row the walk offered more
    than one document to.
    """
    entries = row.get("suggested_links") or []
    if not entries:
        return "-"
    return "; ".join(
        f"{entry.get('document') or entry.get('kind')} {_qty_str(_dec(entry.get('qty')))}"
        for entry in entries
    )


def ack_label(row: Dict[str, Any]) -> str:
    """The handshake as one printed phrase, for the export (AC-H14).

    The same four readings the column on screen carries, written out rather than coloured:
    a spreadsheet has no badge, and "Awaiting" beside a blank name is what tells a buyer
    reading the file that nobody has taken this instruction on yet.
    """
    state = row.get("ack_state") or ACK_AWAITING
    if state == ACK_ACKNOWLEDGED:
        who = row.get("acknowledged_by_name")
        when = row.get("acknowledged_at")
        stamp = when.strftime("%Y-%m-%d %H:%M") if when else ""
        return " ".join(part for part in ("Acknowledged", who, stamp) if part)
    if state == ACK_CHANGED:
        when = row.get("changed_at")
        return f"Changed {when.strftime('%Y-%m-%d')}" if when else "Changed"
    if state == ACK_REJECTED:
        reason = (row.get("rejected_reason") or "").strip()
        who = row.get("rejected_by_name")
        head = f"Rejected by {who}" if who else "Rejected"
        return f"{head}: {reason}" if reason else head
    return "Awaiting"


def month_label(month: str) -> str:
    """`2026-01` to `JAN 26`, spelled the way their tab is."""
    year, _, number = month.partition("-")
    return f"{MONTH_WORD[int(number) - 1]} {year[2:]}"


def _month_bounds(month: str) -> Tuple[date, date]:
    """The half-open range a `YYYY-MM` covers. Refuses anything that is not a month."""
    try:
        first = datetime.strptime(month.strip(), "%Y-%m").date()
    except (ValueError, AttributeError):
        raise AppException(
            422,
            f"'{month}' is not a delivery month. Use YYYY-MM.",
            code="invalid_delivery_month",
        )
    following = (
        date(first.year + 1, 1, 1) if first.month == 12 else date(first.year, first.month + 1, 1)
    )
    return first, following


def _as_day(value: str, code: str = "invalid_raised_date") -> date:
    """A `YYYY-MM-DD` parameter, refused by the NAME OF THE PARAMETER IT CAME FROM: three
    filters parse a day now, and answering a malformed `delivery_from` with
    `invalid_raised_date` sends whoever reads the code to the wrong control."""
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except (ValueError, AttributeError):
        raise AppException(
            422,
            f"'{value}' is not a date. Use YYYY-MM-DD.",
            code=code,
        )


class OrderInquiryWorklistService:
    """Reads, totals and exports every raised instruction, whoever it belongs to."""

    #: Lane C, PLAN-order-sheet-oi-reports-22sep.md (AC-C6): the OI worksheet's OWN cap,
    #: separate from `summary_order_service.MAX_EXPORT_ROWS` - the worksheet's row set is
    #: OI rows, not order-sheet rows, and the two must never share a limit that happens
    #: to describe a different population.
    MAX_WORKSHEET_ROWS = 5000

    def __init__(self, db: Session):
        self.db = db

    # -------------------------------------------------------------- derived SPO

    def _derived_spo_query(self, *columns: Any, extra_where: tuple = ()) -> Any:
        """S5 (R-D/R-E/R-F): every OPEN SPO allocation this row's own PO links reach -
        `from_po_number = po_number AND product_id = po_line.product_id`, open per
        `derived_spo_open_clauses()` (shared with `links_for_rows`'s display entries).

        ONE ROW PER ALLOCATION. The links are reached through an EXISTS rather than a
        join, which is the whole of AC-D12: a row holding two PO links on the SAME
        purchase order and product reaches ONE allocation, and a join would have
        returned it once per link and summed its quantity twice.

        COMPANY-SCOPED BY HAND (AC-D14), AND THE PREDICATE IS NOT REDUNDANT. The session
        listener (`company_scope.do_orm_execute`) injects `with_loader_criteria` for the
        entities a statement names at its TOP level; `SPOAllocation` here is inside a
        correlated sub-select spliced into the worklist's own query, which names
        `OrderInquiryRow` and its joins, so nothing scopes this leg on the list path.
        `from_po_number` is plain text off the AutoCount feed and `product_id` is not
        company-scoped either, so without the predicate another company's allocation
        naming the same PO number string reads as this row's incoming stock - measured,
        5 of an 8 row. Do not delete it as duplicated by the listener.

        Built PER CALL rather than at import time, because the scope lives on the
        session. Every reader wraps it - an `EXISTS` (`kind=spo`, `linked=spo`,
        `spo_number`) or a `SUM` (`_derived_cover_qty`) - so the filters and the cards
        can never disagree about what counts as incoming.
        """
        reached_by_a_po_link = (
            select(OrderInquiryLink.id)
            .select_from(OrderInquiryLink)
            .join(PurchaseOrderLine, PurchaseOrderLine.id == OrderInquiryLink.po_line_id)
            .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderLine.purchase_order_id)
            .where(
                OrderInquiryLink.row_id == OrderInquiryRow.id,
                SPOAllocation.from_po_number == PurchaseOrder.po_number,
                SPOAllocation.product_id == PurchaseOrderLine.product_id,
            )
            .correlate(OrderInquiryRow, SPOAllocation)
            .exists()
        )
        company_predicate = build_company_predicate(
            SPOAllocation, get_company_scope(self.db)
        )
        return (
            select(*(columns or (SPOAllocation.id,)))
            .select_from(SPOAllocation)
            .outerjoin(
                InboundShipment, InboundShipment.id == SPOAllocation.inbound_shipment_id
            )
            .where(
                reached_by_a_po_link,
                *derived_spo_open_clauses(),
                *((company_predicate,) if company_predicate is not None else ()),
                *extra_where,
            )
            .correlate(OrderInquiryRow)
        )

    def _has_derived_spo(self) -> Any:
        """Does this row's own PO link have an open derived SPO cover at all (S5,
        R-D/R-E, round 2 AC-D6b)? The one existence test `kind=spo` and `linked=spo`
        both filter on, so a PO-linked row with a derived cover answers to either
        without a real spo link."""
        return self._derived_spo_query().exists()

    def _derived_cover_qty(self) -> Any:
        """The derived SPO quantity this row may still COUNT, before the cap below.

        Two exclusions the display entries do not make (S5, corrected 16 Sep after
        review - the first formula double counted):

        * an allocation the row ALREADY links to for real is left out (AC-D10), or the
          same five units would be counted once as the row's own SPO link and once
          again as its PO's derived cover;
        * each allocation counts ONCE per row (AC-D12), which the EXISTS shape above
          gives for nothing.

        A row with no PO link answers 0 - the subquery finds nothing to sum, never a
        null that would poison the `+` in `_incoming_qty`.
        """
        already_linked_for_real = (
            select(OrderInquiryLink.id)
            .where(
                OrderInquiryLink.row_id == OrderInquiryRow.id,
                OrderInquiryLink.spo_allocation_id == SPOAllocation.id,
            )
            .correlate(OrderInquiryRow, SPOAllocation)
            .exists()
        )
        return func.coalesce(
            self._derived_spo_query(
                func.sum(
                    SPOAllocation.allocated_quantity
                    - func.coalesce(SPOAllocation.quantity_received, 0)
                ),
                extra_where=(~already_linked_for_real,),
            ).scalar_subquery(),
            0,
        )

    def _incoming_qty(self) -> Any:
        """Stage 3: already on a shipping order, own link or derived via its linked PO.

        `least(qty, spo_real + least(po_linked, cover))`. The derived cover is CAPPED AT
        THE ROW'S OWN PO-LINKED QUANTITY (AC-D11): an allocation of 500 on the purchase
        order covers at most the three units this row actually put on that purchase
        order, and the rest of the row is demand nobody has placed.
        """
        derived_cover = func.least(_PO_LINKED_QTY, self._derived_cover_qty())
        return func.least(OrderInquiryRow.qty, _SPO_LINKED_QTY + derived_cover)

    def _purchased_qty(self) -> Any:
        """Stage 2: on a purchase order line, not yet on a shipment.

        `least(qty - incoming, greatest(0, po_linked - derived_cover))`, spelled the way
        the PLAN states it (S5) - what the row put on purchase orders, net of the part
        its own derived cover has already carried to Incoming, so a unit is counted once
        and at the furthest stage it reached. Both legs are non-negative by construction
        (`incoming` is capped at `qty`, `derived_cover` at `po_linked`), so the
        `greatest` is a guard on the arithmetic rather than a clamp anything reaches.
        """
        derived_cover = func.least(_PO_LINKED_QTY, self._derived_cover_qty())
        return func.least(
            OrderInquiryRow.qty - self._incoming_qty(),
            func.greatest(0, _PO_LINKED_QTY - derived_cover),
        )

    # ------------------------------------------------------------------ query

    def _base(
        self,
        *,
        query: Optional[str] = None,
        delivery_month: Optional[str] = None,
        raised_date: Optional[str] = None,
        state: Optional[str] = None,
        project_id: Optional[str] = None,
        # S5 (`PLAN-oi-project-label-from-so.md` section 5): the filter follows the
        # COLUMN, which is `_PROJECT_TITLE` - text, exact match, never a uuid. Separate
        # from `project_id` (which stays UUID-validated, for any existing deep link):
        # an adopted order has no `Project` row to filter by id, only the label the
        # column prints.
        project: Optional[str] = None,
        supplier_id: Optional[str] = None,
        raised_by: Optional[str] = None,
        linked: Optional[str] = None,
        kind: Optional[str] = None,
        ack: Optional[str] = None,
        # S1, R-K (`PLAN-scm-oi-worklist-excel-parity.md`).
        location: Optional[str] = None,
        agent: Optional[str] = None,
        so_month: Optional[str] = None,
        po_number: Optional[str] = None,
        spo_number: Optional[str] = None,
        delivery_from: Optional[str] = None,
        delivery_to: Optional[str] = None,
        # S3: a Schedule CELL's own rows, named the way the cell itself is (its axis and
        # the key it groups on) rather than by whatever label happened to be printed.
        axis: Optional[str] = None,
        axis_key: Optional[str] = None,
        # S5/AC-OH-52: the ONE caller that must see a `cancelled` row even with no
        # explicit `state` - the State facet's own count, so the filter can offer
        # "Cancelled (n)" to ask for it. Never set by a route param; `summary()`'s
        # `by_state` grouping is the only caller that passes it.
        include_cancelled: bool = False,
        # S3 (`PLAN-oi-header-list-detail.md`): the OI detail page's own scope - every
        # row of exactly this header, nothing outside it. Feeds the page's Lines tab,
        # the whole-OI Confirm (`AcknowledgeFilter.inquiry_id`) and gear > Auto link
        # (`AutoPlaceRequest.filter.inquiry_id`).
        inquiry_id: Optional[str] = None,
        # Lane C, PLAN-order-sheet-oi-reports-22sep.md (AC-C3): the OI worksheet's own
        # row set - a run's `run_scope_oi_rows` result, fed in as the row ids to print
        # rather than as a new set of filter clauses. `None` (the default) means "every
        # filter above decides", unchanged for the list page's own export; an EMPTY list
        # means "no rows" (AC-C5), not "no filter".
        row_ids: Optional[Sequence[str]] = None,
    ):
        """Every inquiry row in the company, with everything a column needs beside it.

        Every join is OUTER except the two that define a row's existence (its inquiry and
        the sales order the inquiry was raised on). An inner join to `Project` is what
        made the PROJECT/CUSTOMER column blank for adopted rows - worse, it dropped them
        from the list entirely, which is why nobody noticed the blank.
        """
        base = (
            self.db.query(OrderInquiryRow.id)
            .select_from(OrderInquiryRow)
            .join(OrderInquiry, OrderInquiry.id == OrderInquiryRow.order_inquiry_id)
            .join(
                ProjectSalesOrder,
                ProjectSalesOrder.id == OrderInquiry.project_sales_order_id,
            )
            # The revision that raised this row, and the person who confirmed it. Both
            # OUTER and both on a primary key, so a row with no decision (the amendment
            # path) or a decision whose user has since been removed still reaches the
            # list rather than dropping out of it.
            .outerjoin(
                SOSupplyDecision,
                SOSupplyDecision.id == OrderInquiryRow.supply_decision_id,
            )
            .outerjoin(User, User.id == _RAISED_BY_ID)
            .outerjoin(_ACK_USER, _ACK_USER.id == OrderInquiryRow.acknowledged_by)
            .outerjoin(_REJECT_USER, _REJECT_USER.id == OrderInquiryRow.rejected_by)
            .outerjoin(
                ProjectSalesOrderLine,
                ProjectSalesOrderLine.id == OrderInquiryRow.so_line_id,
            )
            .outerjoin(Product, Product.id == ProjectSalesOrderLine.product_id)
            # The LOCATION fallback: the line's own core sales order line, then the
            # fulfilment warehouse on that line. Joined on primary keys throughout, so
            # neither join can multiply a row.
            .outerjoin(
                SalesOrderLine,
                SalesOrderLine.id == ProjectSalesOrderLine.core_sales_order_line_id,
            )
            .outerjoin(Warehouse, Warehouse.id == SalesOrderLine.warehouse_id)
            .outerjoin(SalesOrder, SalesOrder.id == ProjectSalesOrder.so_id)
            # Who sold it. The same core sales order the SO DATE / S/O NO columns already
            # read off - `core_sales_order_line_id` is only ever set to a line of THIS
            # order (`project_so_reconciliation_service`), so this is the one join, not a
            # per-row lookup.
            .outerjoin(SalesAgent, SalesAgent.id == SalesOrder.sales_agent_id)
            .outerjoin(Project, Project.id == ProjectSalesOrder.project_id)
            .outerjoin(
                ProjectPurchaseOrder,
                ProjectPurchaseOrder.id == ProjectSalesOrder.purchase_order_id,
            )
            .outerjoin(
                ProjectParty, ProjectParty.id == ProjectPurchaseOrder.issuing_party_id
            )
            .outerjoin(Customer, Customer.id == _CUSTOMER_ID)
            .outerjoin(PurchaseOrder, PurchaseOrder.id == _PLACED_PO_ID)
            # AC-FB-52: the purchase order's own supplier first, the row's first SPO
            # link's own supplier when there is no purchase order to read one off -
            # the SAME fallback shape `_PLACED_PO_ID` itself is built from. The
            # Supplier filter and sort read `Supplier.id`/`Supplier.supplier_name`
            # off this ONE join, so both keep working unchanged.
            .outerjoin(
                Supplier,
                Supplier.id == func.coalesce(
                    PurchaseOrder.supplier_id, _SPO_LINKED_SUPPLIER_ID
                ),
            )
        )
        if delivery_month:
            first, following = _month_bounds(delivery_month)
            base = base.filter(
                OrderInquiryRow.delivery_date >= first,
                OrderInquiryRow.delivery_date < following,
            )
        if raised_date:
            base = base.filter(_RAISED_DAY == _as_day(raised_date))
        if state:
            base = base.filter(OrderInquiryRow.state == state)
        elif not include_cancelled:
            # S5/AC-OH-50..51 (R2): a `cancelled` row is a revision that called the line
            # off - not owed, and not a row purchasing needs to see unless the State
            # filter specifically asks for it.
            base = base.filter(OrderInquiryRow.state != INQUIRY_CANCELLED)
        if project_id:
            base = base.filter(ProjectSalesOrder.project_id == project_id)
        if project:
            base = base.filter(_PROJECT_TITLE == project)
        if inquiry_id:
            base = base.filter(OrderInquiry.id == inquiry_id)
        if row_ids is not None:
            base = base.filter(OrderInquiryRow.id.in_(list(row_ids)))
        if supplier_id:
            base = base.filter(Supplier.id == supplier_id)
        if raised_by:
            # By id, off the ROW's own raiser (its revision's confirmer, the header only
            # when there is no revision). The screen picks the person from the summary's
            # own list, so this never has to guess which "Cindy" was meant.
            base = base.filter(_RAISED_BY_ID == raised_by)
        if linked:
            # WHERE the row is linked (AC-I5), which is a different question from what
            # STATE it is in: a buyer asking "what have I still not put on anything"
            # wants `none`, and one chasing shipping orders wants `spo`. `po` and `spo`
            # mean "holds at least one link of that kind", so a row split across both
            # answers to either - it genuinely is on both.
            # `any` is internal - "Unlink all" asks for every row that holds a link, and
            # the route's own whitelist offers po / spo / none to a caller.
            if linked == "any":
                base = base.filter(_HAS_ANY_LINK)
            elif linked == "po":
                base = base.filter(_HAS_PO_LINK)
            elif linked == "spo":
                # S5, R-E (round 2, AC-D6b): widened to a row whose only real link is
                # on a PO, but whose PO carries a derived SPO cover - it genuinely is
                # on both books now, one of them derived.
                base = base.filter(or_(_HAS_SPO_LINK, self._has_derived_spo()))
            elif linked == "none":
                base = base.filter(~_HAS_ANY_LINK)
            else:
                raise AppException(
                    422,
                    f"'{linked}' is not a link filter. Use po, spo or none.",
                    code="invalid_linked_filter",
                )
        if kind:
            # WHAT the row still needs, which is what the three cards above both views
            # ask (section 3.I2) and a different question from `linked`: a row linked 5
            # of 8 to a purchase order carries `po` AND `buy`, so it answers to either
            # card, while `linked=po` only asks where its links point. A row in one of
            # `_NOT_OWED_STATES` carries no kind at all - its quantity is not owed any
            # more, and its links are history (`links_for_rows` already hides them), so
            # counting it would tell purchasing to buy something somebody has called off
            # or already answered.
            if kind not in ("spo", "po", "buy"):
                raise AppException(
                    422,
                    f"'{kind}' is not a supply kind. Use spo, po or buy.",
                    code="invalid_kind_filter",
                )
            base = base.filter(OrderInquiryRow.state.notin_(_NOT_OWED_STATES))
            # S5, R-F: `spo` is the SAME `_has_derived_spo()`-widened test `linked=spo`
            # uses (round 2, "one rule at one seam") - a row whose own PO link has a
            # derived SPO cover answers to it even with no real spo link at all. `po`
            # stays the STAGE amount, since `_purchased_qty()` is what nets out the
            # portion `spo` already claimed.
            if kind == "spo":
                base = base.filter(or_(_HAS_SPO_LINK, self._has_derived_spo()))
            elif kind == "po":
                base = base.filter(self._purchased_qty() > 0)
            else:
                # AC-RL-16c (17 Sep review round): a SEPARATE query from `_kinds`' own
                # summary, so a redirected row's own unlinked remainder needs its own
                # exclusion here too - the goods it names already shipped elsewhere
                # (AC-RL-10), and listing the row under `kind=buy` would show purchasing
                # a row the card's own number has already excluded.
                # PLAN-oi-cancelled-line-used-confirm.md (AC-CL-4): same for a row on a
                # cancelled line - nobody will buy it.
                base = base.filter(
                    _UNLINKED_QTY > 0,
                    OrderInquiryRow.redirected_to_pool.is_(False),
                    ~_LINE_CANCELLED,
                )
        if ack:
            # WHERE THE HANDSHAKE STANDS (`PLAN-scm-oi-handshake.md` section 4), which is
            # a third question beside `state` and `linked`: purchasing's own worklist is
            # "what have I not acknowledged yet", and CS's reading of the same list is
            # "what has purchasing refused". A closed set, refused rather than ignored,
            # for the same reason `state` and `kind` are - a filter nothing can equal
            # reads on screen as "no work to do".
            if ack not in ACK_FILTER_VALUES:
                raise AppException(
                    422,
                    f"'{ack}' is not an acknowledgement state. Use "
                    f"{', '.join(ACK_FILTER_VALUES)}.",
                    code="invalid_ack_filter",
                )
            if ack == ACK_TO_CONFIRM:
                # The page's own default again (R3, `PLAN-oi-confirm-per-so.md` S3 -
                # reversing G4/S1's retirement of it): awaiting AND changed, which is one
                # question - "what has purchasing not confirmed yet" - asked of two
                # stored states.
                base = base.filter(
                    OrderInquiryRow.ack_state.in_(ACK_TO_CONFIRM_STATES)
                )
            else:
                # The literal `ack_state`, including `changed`: `_handshake_for_raise`
                # and `_settle_row_in_place` (`PLAN-oi-confirm-per-so.md` S1) stamp
                # `changed` and leave it there until purchasing genuinely re-confirms -
                # there is no auto re-ack any more to make `changed_at IS NOT NULL` a
                # truer read than the column itself, and a row later re-acknowledged
                # keeps its old `changed_at` (history) while reading `acknowledged`
                # again, which `changed_at IS NOT NULL` would have miscounted.
                base = base.filter(OrderInquiryRow.ack_state == ack)
        # S1, R-K: Location, Agent, SO month, PO number, SPO number - the five filters
        # the Excel parity batch adds to the Filters popover.
        if location:
            base = base.filter(_LOCATION == location)
        if agent:
            base = base.filter(SalesAgent.id == agent)
        if so_month:
            first, following = _month_bounds(so_month)
            base = base.filter(_SO_DATE >= first, _SO_DATE < following)
        if po_number:
            like = f"{_escape_like(po_number)}%"
            base = base.filter(
                or_(
                    _row_has_link(
                        OrderInquiryLink.po_line_id.isnot(None),
                        OrderInquiryLink.document.ilike(like, escape=_LIKE_ESCAPE),
                    ),
                    # An SPO link the book named a source purchase order for (AC-F5b) -
                    # the row's only document is the SHIPPING order, and `po_number` has
                    # to reach the purchase order it came from all the same.
                    select(OrderInquiryLink.id)
                    .join(
                        SPOAllocation,
                        SPOAllocation.id == OrderInquiryLink.spo_allocation_id,
                    )
                    .where(
                        OrderInquiryLink.row_id == OrderInquiryRow.id,
                        SPOAllocation.from_po_number.ilike(like, escape=_LIKE_ESCAPE),
                    )
                    .correlate(OrderInquiryRow)
                    .exists(),
                )
            )
        if spo_number:
            like = f"{_escape_like(spo_number)}%"
            base = base.filter(
                or_(
                    _row_has_link(
                        OrderInquiryLink.spo_allocation_id.isnot(None),
                        OrderInquiryLink.document.ilike(like, escape=_LIKE_ESCAPE),
                    ),
                    # AC-F6b (round 2): the row's only REAL link is a PO, but that PO
                    # carries a derived SPO cover (S5, R-E) whose own number starts
                    # with the typed text - `_has_derived_spo()`'s own read, narrowed to
                    # this prefix rather than "any open allocation at all".
                    self._derived_spo_query(
                        extra_where=(
                            SPOAllocation.spo_number.ilike(like, escape=_LIKE_ESCAPE),
                        )
                    ).exists(),
                )
            )
        if delivery_from:
            base = base.filter(
                OrderInquiryRow.delivery_date
                >= _as_day(delivery_from, code="invalid_delivery_from")
            )
        if delivery_to:
            base = base.filter(
                OrderInquiryRow.delivery_date
                <= _as_day(delivery_to, code="invalid_delivery_to")
            )
        if axis and axis_key:
            # S3: the Schedule cell's drilldown. EQUALITY on the very column the matrix
            # grouped by, off the same `_MATRIX_AXES` map, rather than the cell's printed
            # label through the search box - two products can share a name, and an
            # unknown axis is refused here the way it is refused there.
            column, _label = self._matrix_axis(axis)
            base = base.filter(column == axis_key)
        if query:
            # ONE FILTER PER WORD (S2, AC-2.1): an order has forty lines and a product sits
            # on twenty orders, so "SO366990 SRTWT6801" typed as one phrase matched nothing
            # and either word alone answers the wrong question. Each token may still hit any
            # of the columns below (the OR), and every token has to hit something (the AND),
            # which is what makes the pair of them name one row. No tokens - a blank or
            # all-space box - filters nothing, the same as no query at all.
            #
            # Capped at ten, because every token is another OR across a joined query's
            # worth of columns: a pasted paragraph would be a hundred of them. Dropped rather
            # than refused - a clumsy paste deserves a search result, not a 422 - and the
            # route caps the string's own length beside this.
            for token in str(query).split()[:_MAX_QUERY_TOKENS]:
                like = f"%{_escape_like(token)}%"
                base = base.filter(
                    or_(
                        OrderInquiryRow.item_code.ilike(like, escape=_LIKE_ESCAPE),
                        OrderInquiryRow.spo_ref.ilike(like, escape=_LIKE_ESCAPE),
                        # Purchasing is asked about "OI-000123" by name; without this the row
                        # is reachable only by knowing which sales order raised it.
                        OrderInquiry.inquiry_no.ilike(like, escape=_LIKE_ESCAPE),
                        cast(_SO_NUMBER, String).ilike(like, escape=_LIKE_ESCAPE),
                        Product.product_name.ilike(like, escape=_LIKE_ESCAPE),
                        Product.product_code.ilike(like, escape=_LIKE_ESCAPE),
                        Customer.customer_name.ilike(like, escape=_LIKE_ESCAPE),
                        Project.title.ilike(like, escape=_LIKE_ESCAPE),
                        Project.project_code.ilike(like, escape=_LIKE_ESCAPE),
                        # An adopted AutoCount order has no registered Project - the
                        # label above is blank, so the box has to reach the SO's own
                        # free-text label instead (PLAN-oi-project-label-from-so.md).
                        SalesOrder.project_label.ilike(like, escape=_LIKE_ESCAPE),
                        # The CS who raised it. By name, and by the FRONT of the email
                        # address rather than anywhere inside it: a buyer types "cindy",
                        # and matching `%cindy%` across a whole address would also return
                        # every row whose raiser happens to work at cindy.com. The rule
                        # belongs to the TOKEN, not to the whole box.
                        User.name.ilike(like, escape=_LIKE_ESCAPE),
                        User.email.ilike(f"{_escape_like(token)}%", escape=_LIKE_ESCAPE),
                        # S1, R-K: a link document (PO or SPO), an SPO link's own source
                        # purchase order, and the sales agent's code or name - the search
                        # box reaches everything the five new filters can name by hand.
                        _row_has_link(
                            OrderInquiryLink.document.ilike(like, escape=_LIKE_ESCAPE)
                        ),
                        select(OrderInquiryLink.id)
                        .join(
                            SPOAllocation,
                            SPOAllocation.id == OrderInquiryLink.spo_allocation_id,
                        )
                        .where(
                            OrderInquiryLink.row_id == OrderInquiryRow.id,
                            SPOAllocation.from_po_number.ilike(
                                like, escape=_LIKE_ESCAPE
                            ),
                        )
                        .correlate(OrderInquiryRow)
                        .exists(),
                        SalesAgent.sales_agent.ilike(like, escape=_LIKE_ESCAPE),
                        SalesAgent.person_label.ilike(like, escape=_LIKE_ESCAPE),
                    )
                )
        return base

    def acknowledge_scope(self, **filters) -> Tuple[List[str], int]:
        """Every row `filter` matches, split into what a Confirm press may actually take
        on and what it must leave alone (AC-CF-8b, S2 `PLAN-oi-confirm-per-so.md`).

        Built off the SAME `_base` the list route reads, so "Select all N matching"
        always confirms exactly the scope the worklist itself is filtered to, never a
        client-rebuilt copy of it. Eligible is `ack_state` awaiting or changed and
        `state` not cancelled - the same gate `acknowledge_rows` already enforces one row
        at a time; everything else the filter matched (rejected, already acknowledged)
        is reported back as `skipped`, never silently dropped and never silently taken
        on.

        #992 (one-header-per-SO) taught `_base` to hide a `cancelled` row from every
        filter that does not explicitly ask `state=cancelled` (S5/AC-OH-50..51) -
        because THIS reads `_base` too, a query that used to match a cancelled row no
        longer does, so that row is absent from `matched` entirely rather than present
        and then subtracted into `skipped`. That is the honest rule (review round,
        S8): `skipped` counts what the filter actually surfaced and a row's own state
        then refused, never a row the filter never showed the buyer - the dialog's
        "Skipped N" and the toast have to agree with what was on screen. A filter that
        DOES ask for `state=cancelled` still counts a cancelled row as skipped, same
        as any other ineligible state the filter surfaced.
        """
        matched = [str(row_id) for (row_id,) in self._base(**filters).all()]
        if not matched:
            return [], 0
        eligible = (
            self.db.query(OrderInquiryRow.id)
            .filter(
                OrderInquiryRow.id.in_(matched),
                OrderInquiryRow.ack_state.in_((ACK_AWAITING, ACK_CHANGED)),
                OrderInquiryRow.state != INQUIRY_CANCELLED,
            )
            .all()
        )
        eligible_ids = [str(row_id) for (row_id,) in eligible]
        return eligible_ids, len(matched) - len(eligible_ids)

    def list_rows(
        self,
        *,
        page: int = 1,
        limit: int = 50,
        sort: Optional[str] = None,
        direction: Optional[str] = "asc",
        **filters,
    ) -> Dict[str, Any]:
        """One page of the worklist, in a TOTAL and STABLE order.

        Three rules hold for every sortable column, the same three the fulfilment
        worklist holds to:

        * nulls sort LAST in BOTH directions. Postgres would put them last ascending and
          first descending, so reversing "delivery date" would answer with the rows that
          have no date at all. A missing value is not an extreme value;
        * every sort ends on the row id, so a tie breaks the same way on page 2 as on
          page 1 and a paged read neither repeats a row nor drops one;
        * an unknown column is refused here as well as at the route, so a service caller
          (a test, a script, the MCP) gets the same answer an HTTP caller gets.
        """
        field = sort or DEFAULT_SORT_FIELD
        if field not in SORTABLE_FIELDS:
            raise AppException(
                422,
                f"'{field}' is not a column this list can be sorted by.",
                code="unsortable_field",
            )
        if direction not in (None, "asc", "desc"):
            raise AppException(
                422,
                f"'{direction}' is not a sort direction. Use asc or desc.",
                code="invalid_sort_direction",
            )
        descending = direction == "desc"
        page = max(page, 1)

        base = self._base(**filters)
        total = int(
            base.with_entities(func.count(OrderInquiryRow.id)).order_by(None).scalar() or 0
        )
        column = _SORT_EXPRESSIONS[field]
        ordering = column.desc().nulls_last() if descending else column.asc().nulls_last()
        rows = (
            base.with_entities(*_COLUMNS)
            .order_by(ordering, OrderInquiryRow.id.asc())
            .offset((page - 1) * limit)
            .limit(limit)
            .all()
        )
        product_by_row, link_candidates = self._link_candidate_context(rows)
        flow = self._quantity_flow_by_so_line(rows)
        links = ProjectOrderInquiryService(self.db).links_for_rows(
            [row.id for row in rows]
        )
        suggested_links = ProjectOrderInquiryService(self.db).suggested_links_for_rows(
            [row.id for row in rows]
        )
        self._attach_link_suggestions(rows, links, product_by_row)
        bundle_map = self._bundle_map_for_rows(rows)
        anchor_headline_by_id = self._anchor_headline_by_id(rows, links)
        host_changes_by_row_id = self._host_changes_for_rows(rows, bundle_map)
        raise_events_by_row = self._raise_events_by_row(rows)
        return {
            "data": [
                self._serialize(
                    row,
                    product_by_row,
                    link_candidates,
                    flow,
                    links,
                    bundle_map,
                    anchor_headline_by_id,
                    host_changes_by_row_id,
                    suggested_links,
                    raise_events_by_row,
                )
                for row in rows
            ],
            "pagination": {"total": total, "page": page, "limit": limit},
            "empty": total == 0,
        }

    def _link_candidate_context(
        self, rows
    ) -> Tuple[Dict[str, Optional[str]], Dict[str, set]]:
        """`has_link_candidate` for a whole PAGE, bulk-answered once. Row id -> product id
        (the line's own reconciled product first, the item code second - the same
        precedence `ProjectOrderInquiryService._resolve_product_id` uses per row), and the
        and the products that still have something to link to, by kind."""
        by_code = {row.item_code for row in rows if not row.product_id and row.item_code}
        code_products = (
            dict(
                self.db.query(Product.product_code, Product.id)
                .filter(Product.product_code.in_(list(by_code)))
                .all()
            )
            if by_code
            else {}
        )
        product_by_row: Dict[str, Optional[str]] = {
            row.id: row.product_id or code_products.get(row.item_code) for row in rows
        }
        candidates = ProjectOrderInquiryService(self.db).link_candidate_products(
            set(product_by_row.values())
        )
        return product_by_row, candidates

    def _attach_link_suggestions(
        self,
        rows,
        links: Dict[str, List[Dict[str, Any]]],
        product_by_row: Dict[str, Optional[str]],
    ) -> None:
        """S1b (`PLAN-oi-replan-received-links.md`, AC-RL-20 to AC-RL-23): a concrete
        instruction on an open link that has drifted outside its product's lead-time
        window - never a reason, never "early" (owner ruling 16 Sep).

        Mutates each link dict IN PLACE with `suggestion`: `{"kind": "reallocate",
        "candidates": [...]}` naming EVERY OTHER linkable row of the same product with
        open need (never a row on the SAME SO line), delivery date ascending then open
        need descending - the first candidate is the suggested target (ruling 17 Sep:
        list all, earliest first) - `{"kind": "unlink"}` when there is none, or `None`
        on a received link, one still inside the window, or one of the two S3
        exemptions below.

        S3 (review round 1, `PLAN-oi-cascade-skip-early-arrival.md`): a link the
        automatic pass was TOLD to honour regardless of the window earns no pill
        either - a link THIS row's own SO claims live, or one whose document the row
        cites (`ProjectOrderInquiryService._cited_documents`, the same reader the walk
        uses). Flagging what the pass was just instructed to keep is the same noise the
        owner complained about ("kinda redundant"), one door over; it holds for a
        HAND-placed link exactly as for an automatic one (AC-EA-14/15) - the exemption
        is the evidence, not who pressed the button.

        Review round 2 (F1): "claims live" is read through the WALK's own claim reader
        (`ProjectOrderInquiryService._prime_claims` / `_dedication_for_target`), not a
        second predicate - a first cut here filtered `scm.order_link_claim.resolved_at
        IS NOT NULL`, which is neither necessary (the walk links an unresolved but live
        claim regardless, AC-EA-17) nor sufficient (the walk refuses a RESOLVED claim
        whose sales-order line has since SETTLED, AC-EA-16 - `resolved_at` says the
        pairing was found, not that the order still wants it). One reader, never a
        second spelling of "this row's own SO claims it".

        ONE grouped query for the whole page's candidates (AC-RL-23), and ONE priming
        read for the page's triggered claims (S3/F1, `_prime_claims`) - never one per
        link: every triggered link's product, and every triggered link's target, is
        collected first.
        """
        delivery_by_row = {row.id: row.delivery_date for row in rows}
        so_line_by_row = {row.id: row.so_line_id for row in rows}
        so_number_by_row = {row.id: row.so_number for row in rows}
        row_by_id = {row.id: row for row in rows}
        product_ids = {pid for pid in product_by_row.values() if pid}
        lead_times = (
            ProjectSupplyService(self.db).lead_times(product_ids) if product_ids else {}
        )

        triggered: List[Tuple[str, Dict[str, Any], str]] = []
        for row_id, row_links in links.items():
            product_id = product_by_row.get(row_id)
            delivery_date = delivery_by_row.get(row_id)
            for link in row_links:
                link["suggestion"] = None
                if link.get("received"):
                    continue
                expected_date = link.get("expected_date")
                if not product_id or not delivery_date or not expected_date:
                    continue
                lead_days = lead_times.get(product_id)
                if lead_days is None:
                    lead_days = DEFAULT_LEAD_TIME_DAYS
                if not arrives_outside_window(expected_date, delivery_date, lead_days):
                    continue
                triggered.append((row_id, link, product_id))

        if not triggered:
            return

        target_ids = {
            link.get("po_line_id") or link.get("spo_allocation_id")
            for _row_id, link, _product_id in triggered
            if link.get("po_line_id") or link.get("spo_allocation_id")
        }
        inquiry_service = ProjectOrderInquiryService(self.db)
        # F1: the walk's OWN claim cache, primed for the page's triggered targets - not
        # a second query with a second predicate.
        inquiry_service._prime_claims(list(target_ids))
        triggered = [
            (row_id, link, product_id)
            for row_id, link, product_id in triggered
            if not self._exempt_from_window(
                link,
                row=row_by_id.get(row_id),
                own_so_number=so_number_by_row.get(row_id),
                inquiry_service=inquiry_service,
            )
        ]
        if not triggered:
            return
        candidates_by_product = self._repoint_candidates_by_product(
            {product_id for _row_id, _link, product_id in triggered}
        )
        for row_id, link, product_id in triggered:
            own_so_line = so_line_by_row.get(row_id)
            delivery_date = delivery_by_row.get(row_id)
            eligible = [
                candidate
                for candidate in candidates_by_product.get(product_id, [])
                if candidate["so_line_id"] != own_so_line
                and candidate["delivery_date"] is not None
                and candidate["delivery_date"] < delivery_date
            ]
            eligible.sort(key=lambda c: (c["delivery_date"], -c["open_qty"]))
            if eligible:
                link["suggestion"] = {
                    "kind": "reallocate",
                    "candidates": [
                        {
                            "inquiry_no": candidate["inquiry_no"],
                            "item_code": candidate["item_code"],
                            "so_number": candidate["so_number"],
                            "delivery_date": candidate["delivery_date"].isoformat(),
                            "open_qty": _qty_str(candidate["open_qty"]),
                        }
                        for candidate in eligible
                    ],
                }
            else:
                link["suggestion"] = {"kind": "unlink"}

    @staticmethod
    def _exempt_from_window(
        link: Dict[str, Any],
        *,
        row: Optional[Any],
        own_so_number: Optional[str],
        inquiry_service: ProjectOrderInquiryService,
    ) -> bool:
        """S3's two exemptions - the SAME two `auto_place_for_products` reads (S2): a
        target THIS row's own SO still claims LIVE, or a document the row cites. Either
        is a person's or the book's word, and the window does not overrule it, on the
        pass or on the pill.

        F1 (review round 2): "claims live" is `_dedication_for_target`'s own `own_claim`
        element (index 2 of its `(reserved, dedicated_to, own_claim)` return) - the
        SAME reader `_candidates_for_row` builds `own_so_claim` from. That is a claim
        whose SO LINE still has outstanding, never `resolved_at`: a claim written before
        the purchase side is named is unresolved and still live (AC-EA-17); a resolved
        claim whose sales order has since settled is no longer live (AC-EA-16). Caller
        must have already primed `inquiry_service._prime_claims` for `target_id`, or
        this falls back to priming it alone (`_claims_of`'s own guard).
        """
        target_id = link.get("po_line_id") or link.get("spo_allocation_id")
        if target_id and own_so_number is not None:
            _reserved, _dedicated_to, own_claim = inquiry_service._dedication_for_target(
                target_id, own_so_number
            )
            if own_claim:
                return True
        document = str(link.get("document") or "").strip().upper()
        if row is not None and document:
            # F4: `row` is the worklist's OWN `_COLUMNS` tuple, not an
            # `OrderInquiryRow` ORM instance - `_cited_documents` may read only the
            # three fields `_COLUMNS` carries for it (`cited_document`, `note`,
            # `spo_ref`). A fourth field added to that reader with no matching column
            # here fails loudly (`AttributeError`), not silently; AC-EA-15 is the test
            # that goes red first.
            return document in inquiry_service._cited_documents(row)
        return False

    def _repoint_candidates_by_product(
        self, product_ids: set
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Every OTHER linkable row for these products, with open need - the cascade's
        own linkable-row predicate (`raised`/`partly_linked`, `_SUGGESTION_LINKABLE_
        VERBS`, `ACK_LINKABLE`), so a suggestion never names a row the cascade itself
        could not have drafted onto. `open_qty` is the SAME `_UNLINKED_QTY` the Buy
        card reads, so the figure on the popover and the figure on that row's own Qty
        cell can never disagree.
        """
        if not product_ids:
            return {}
        rows = (
            self.db.query(
                OrderInquiryRow.id,
                OrderInquiryRow.so_line_id,
                OrderInquiryRow.delivery_date,
                OrderInquiryRow.item_code,
                ProjectSalesOrderLine.product_id,
                OrderInquiry.inquiry_no,
                _SO_NUMBER.label("so_number"),
                _UNLINKED_QTY.label("open_qty"),
            )
            .select_from(OrderInquiryRow)
            .join(OrderInquiry, OrderInquiry.id == OrderInquiryRow.order_inquiry_id)
            .join(
                ProjectSalesOrder,
                ProjectSalesOrder.id == OrderInquiry.project_sales_order_id,
            )
            .join(
                ProjectSalesOrderLine,
                ProjectSalesOrderLine.id == OrderInquiryRow.so_line_id,
            )
            # R1 (23 Sep 2026): `_UNLINKED_QTY` (this query's own `open_qty`) no longer
            # reads `SalesOrderLine` - it is `_ROW_QTY`, the row's own `qty`, uncapped -
            # so the join a 17 Sep review finding once needed here to avoid a cartesian
            # product against `OrderInquiry` (SAWarning,
            # `test_link_suggests_reallocate_to_every_sooner_open_row`) is retired with
            # the cap that was its only reason to exist.
            .filter(
                ProjectSalesOrderLine.product_id.in_(product_ids),
                OrderInquiryRow.state.in_((INQUIRY_RAISED, INQUIRY_PARTLY_LINKED)),
                OrderInquiryRow.verb.in_(_SUGGESTION_LINKABLE_VERBS),
                OrderInquiryRow.ack_state.in_(ACK_LINKABLE),
            )
            .all()
        )
        out: Dict[str, List[Dict[str, Any]]] = {}
        for (
            row_id,
            so_line_id,
            delivery_date,
            item_code,
            product_id,
            inquiry_no,
            so_number,
            open_qty,
        ) in rows:
            qty = _dec(open_qty)
            if qty <= _ZERO:
                continue
            out.setdefault(str(product_id), []).append(
                {
                    "row_id": str(row_id),
                    "so_line_id": str(so_line_id) if so_line_id else None,
                    "delivery_date": delivery_date,
                    "item_code": item_code,
                    "inquiry_no": inquiry_no,
                    "so_number": so_number,
                    "open_qty": qty,
                }
            )
        return out

    def _quantity_flow_by_so_line(self, rows) -> Dict[str, Dict[str, Decimal]]:
        """"Taken from PO" and "Remaining" for a whole PAGE, bulk-answered once (the
        captain, 20 Aug: "show the quantity, quantity taken from PO, and the remaining
        quantity, cause this is what flows to reorder planning").

        Read off the LINKS since section 3.I, which is what makes the two figures true
        again. Before it, a cascade SPLIT a line's rows and the pair was "sum the placed
        siblings" against "sum the raised siblings" - correct only because the split had
        moved the arithmetic into the row count. A row now keeps its full quantity and
        carries links, so per `so_line_id`:

        * `taken` - the sum of every LINK on the line's rows, whatever document it names;
        * `remaining` - the sum of `qty - linked` across them, floored at zero. NOT the
          SAME expression as `scm.committed_v`'s confirmed leg - `bundled_qty` is not
          subtracted here, unlike the view's own `- oir.bundled_qty`. UNCAPPED either way
          (owner ruling R1, 23 Sep 2026, `PLAN-oi-order-rows-uncapped.md`): SO421985
          raised three ORDER rows of 493, the AutoCount pull then marked the lines
          delivered 493/493, and a Remaining that capped at the line's outstanding read 0
          for a row nobody had linked - the 14 Sep cap this replaced fixed the opposite
          defect (SO368872 / SRTWC286-SH, Remaining 302 beside a Buy card of 0) but the
          owner has since ruled the row is owed regardless of what the line's delivered
          column says.

        Scoped to the verbs `committed_v` counts: `ORDER` and, since part 2 section 4b,
        `ORDER_BACK`. Rows in `actioned` / `cancelled` are out, as they always were.

        A row a planning change REDIRECTED to replenish the shared pool
        (`planning_change_service._apply_placed_redirect`, the captain's ruling 21 Aug
        2026) is excluded from both: it is still real linked quantity, just not this
        line's anymore, and counting it here would read as this line's need being covered
        by a purchase order that is actually bound for the pool.

        One grouped query plus one aggregate over the links, not one query per row.
        """
        so_line_ids = {row.so_line_id for row in rows if row.so_line_id}
        if not so_line_ids:
            return {}
        # R1 (23 Sep 2026): uncapped, so this no longer reads any core sales order line -
        # `_ROW_QTY` is the row's own `qty`, nothing else. The joins that used to reach it
        # (`ProjectSalesOrderLine` -> `SalesOrderLine`) are gone with the cap they existed
        # for; `OrderInquiryRow.so_line_id` needs neither to group or filter.
        linked = (
            func.coalesce(
                select(func.sum(OrderInquiryLink.qty))
                .where(OrderInquiryLink.row_id == OrderInquiryRow.id)
                .correlate(OrderInquiryRow)
                .scalar_subquery(),
                0,
            )
        ).label("linked")
        agg = (
            self.db.query(
                OrderInquiryRow.so_line_id,
                func.coalesce(func.sum(linked), 0),
                func.coalesce(func.sum(func.greatest(_ROW_QTY - linked, 0)), 0),
            )
            .select_from(OrderInquiryRow)
            .filter(
                OrderInquiryRow.so_line_id.in_(so_line_ids),
                OrderInquiryRow.verb.in_((IV_ORDER, IV_ORDER_BACK)),
                OrderInquiryRow.state.in_(
                    (INQUIRY_PLACED, INQUIRY_PARTLY_LINKED, INQUIRY_RAISED)
                ),
                OrderInquiryRow.redirected_to_pool.is_(False),
            )
            .group_by(OrderInquiryRow.so_line_id)
            .all()
        )
        return {
            so_line_id: {"taken": _dec(taken), "remaining": _dec(remaining)}
            for so_line_id, taken, remaining in agg
        }

    def _bundle_map_for_rows(self, rows) -> Dict[str, List[str]]:
        """`bundled_with_item_codes_map`, built ONCE for every bundled row on a page
        (review round 1 item 10) - grouped by `company_id` because the map itself is
        one company's rules, though in practice one page is always one company
        (multi-tenant is stubbed, CLAUDE.md)."""
        codes_by_company: Dict[str, set] = {}
        for row in rows:
            if row.bundled_with_row_id:
                codes_by_company.setdefault(row.company_id, set()).add(row.item_code)
        if not codes_by_company:
            return {}
        merged: Dict[str, List[str]] = {}
        for company_id, codes in codes_by_company.items():
            merged.update(
                bundled_with_item_codes_map(
                    self.db, company_id=company_id, companion_item_codes=codes
                )
            )
        return merged

    def _host_changes_for_rows(
        self, rows, bundle_map: Dict[str, List[str]]
    ) -> Dict[str, List[Dict[str, Any]]]:
        """`bundled_host_changes` (`PLAN-oi-bundled-row-host-change.md`): for a bundled
        row, one entry per host item code, IN RULE ORDER, read from that HOST's own
        LIVE row on the SAME order inquiry header - never written onto the companion
        row itself (owner ruling, 19 Sep 2026: "it comes with the X and Y, so it should
        follow them, to have the same delay"). A host with no live row still gets an
        entry, with every row field null, so the (i) always names every host the rule
        requires.

        A host's own LIVE row (review round 1 BLOCKER, 19 Sep 2026): state not
        cancelled, `redirected_to_pool` false, AND `verb` in `(IV_ORDER, IV_ORDER_BACK)`
        - the SAME set `_settle_row_in_place` treats as a line's real instruction,
        never an ADVANCE/DELAY exception row that happens to share the host's item
        code and would otherwise read as the host's own change. When a host carries
        MORE than one live ORDER row, the OLDEST wins (`created_at`, then `id`) - and
        that choice is made the SAME WAY whichever page the row happened to load on:
        the in-page pass collects every page candidate for a key and picks the oldest
        exactly as the fallback query's own `ORDER BY` does, so `sort=item_code&dir=
        desc` (or any other sort) can never answer differently from the default.

        Built from rows already on THIS page where possible; the rest costs ONE extra
        query for the whole page (never per row), keyed by `(order_inquiry_id,
        item_code)`.
        """
        hosts_by_row: Dict[str, Tuple[str, List[str]]] = {}
        wanted: set = set()
        for row in rows:
            if not row.bundled_with_row_id:
                continue
            codes = resolve_bundled_item_codes(
                bundle_map or {},
                companion_item_code=row.item_code,
                anchor_item_code=row.bundled_with_item_code,
            )
            if not codes:
                continue
            hosts_by_row[row.id] = (row.order_inquiry_id, codes)
            for code in codes:
                wanted.add((row.order_inquiry_id, code))
        if not hosts_by_row:
            return {}

        def _live_host_row(candidate) -> bool:
            return (
                candidate.state != INQUIRY_CANCELLED
                and not candidate.redirected_to_pool
                and candidate.verb in (IV_ORDER, IV_ORDER_BACK)
            )

        def _created_key(candidate) -> Tuple[Any, str]:
            return (candidate.raised_at, candidate.id)

        # Every page candidate per key, not just the first ENCOUNTERED - `rows` is in
        # the page's own sort order (whatever column the caller sorted by), so "first
        # in the list" used to answer a different host row depending on the sort.
        page_candidates: Dict[Tuple[str, str], List[Any]] = {}
        for row in rows:
            key = (row.order_inquiry_id, row.item_code)
            if key in wanted and _live_host_row(row):
                page_candidates.setdefault(key, []).append(row)

        values_by_key: Dict[Tuple[str, str], Any] = {
            key: min(candidates, key=_created_key)
            for key, candidates in page_candidates.items()
        }

        missing = wanted - set(values_by_key)
        if missing:
            inquiry_ids = {key[0] for key in missing}
            item_codes = {key[1] for key in missing}
            extra_rows = (
                self.db.query(
                    OrderInquiryRow.id,
                    OrderInquiryRow.order_inquiry_id,
                    OrderInquiryRow.item_code,
                    OrderInquiryRow.qty,
                    OrderInquiryRow.delivery_date,
                    OrderInquiryRow.previous_qty,
                    OrderInquiryRow.previous_delivery_date,
                    OrderInquiryRow.created_at,
                )
                .filter(
                    OrderInquiryRow.order_inquiry_id.in_(inquiry_ids),
                    OrderInquiryRow.item_code.in_(item_codes),
                    OrderInquiryRow.state != INQUIRY_CANCELLED,
                    OrderInquiryRow.redirected_to_pool.is_(False),
                    OrderInquiryRow.verb.in_((IV_ORDER, IV_ORDER_BACK)),
                )
                .order_by(OrderInquiryRow.created_at.asc(), OrderInquiryRow.id.asc())
                .all()
            )
            for candidate in extra_rows:
                key = (candidate.order_inquiry_id, candidate.item_code)
                if key in missing and key not in values_by_key:
                    values_by_key[key] = candidate

        result: Dict[str, List[Dict[str, Any]]] = {}
        for row_id, (order_inquiry_id, codes) in hosts_by_row.items():
            entries = []
            for code in codes:
                source = values_by_key.get((order_inquiry_id, code))
                entries.append(
                    {
                        "item_code": code,
                        "qty": (
                            _qty_str(_dec(source.qty)) if source is not None else None
                        ),
                        "delivery_date": (
                            source.delivery_date if source is not None else None
                        ),
                        "previous_qty": (
                            _qty_str(_dec(source.previous_qty))
                            if source is not None and source.previous_qty is not None
                            else None
                        ),
                        "previous_delivery_date": (
                            source.previous_delivery_date
                            if source is not None
                            else None
                        ),
                    }
                )
            result[row_id] = entries
        return result

    def _anchor_headline_by_id(
        self, rows, links: Dict[str, List[Dict[str, Any]]]
    ) -> Dict[str, str]:
        """The ANCHOR row's own coverage headline ("1 of 1"), computed server-side once
        per page (review round 1 item 8) - the client used to scan `table.options.data`
        for a row matching `bundled_with.row_id`, which is only ever right when the
        anchor happens to be loaded on the SAME page as its companion.

        Keyed by row id, for every row that has at least one link of its own; a row
        with none is simply absent, matching the client's own "Not found (new order)"
        fallback for a host nobody has placed anything for yet.

        SO314594 (prod): `links` also carries the SYNTHETIC `derived` entries
        `_append_derived_spo_entries` appends for a linked PO's own open SPO
        allocations - real coverage, but not a link on THIS row, so a host with one
        182-qty PO link and a ~100-qty derived SPO entry must read "182 of 214", never
        "282 of 214". Only real (non-derived) links count toward the headline; a row
        left with none once the derived entries are excluded is absent, same as today.
        """
        result: Dict[str, str] = {}
        for row in rows:
            row_links = [
                link for link in (links.get(row.id) or []) if not link.get("derived")
            ]
            if not row_links:
                continue
            linked_qty = sum((_dec(link["qty"]) for link in row_links), _ZERO)
            result[row.id] = f"{_qty_str(linked_qty)} of {_qty_str(_dec(row.qty))}"
        return result

    @staticmethod
    def _reserve_state(row) -> Optional[str]:
        """PLAN-oi-request-cs-reserve.md 3.5 + 6e.4 (AC-RS-20, AC-RS-78c): `requested`
        while an open request row exists (it always wins, R5), else `reserved` once
        something is actually reserved, else `declined` when the latest answer was 0,
        else null. A line still holding stock from an earlier request reads `reserved`
        even if a later answer was 0 - the pill never hides a live reservation."""
        if getattr(row, "has_open_reserve_request", False):
            return "requested"
        if _dec(getattr(row, "reserved_qty", None)) > _ZERO:
            return "reserved"
        latest = getattr(row, "latest_answered_qty", None)
        if latest is not None and _dec(latest) == _ZERO:
            return "declined"
        return None

    def _bundled_po_number(
        self, row, bundle_map: Optional[Dict[str, List[str]]] = None
    ) -> Optional[str]:
        """D8: the export's document column for a bundled row with no document of its
        own - "Included with CKS1050" rather than a blank, naming every item the rule
        requires when there is more than one (never the word "host")."""
        if not row.bundled_with_row_id:
            return None
        codes = resolve_bundled_item_codes(
            bundle_map or {},
            companion_item_code=row.item_code,
            anchor_item_code=row.bundled_with_item_code,
        )
        if not codes:
            return None
        return f"Included with {' + '.join(codes)}"

    def _raise_events_by_row(self, rows: Sequence[Any]) -> Dict[str, Dict[str, Any]]:
        """AC-DT-3 (`PLAN-oi-decision-trail-ui.md`): the `order_inquiry_raises` EVENT
        each page row traces to - the confirm or reconfirm that actually raised it,
        never the row's own coalesced "current owner" (`raised_by_name`/`raised_at`
        above already answer that question).

        Rows and their raise event are written in the SAME call
        (`ProjectOrderInquiryService._write` alongside `OrderInquiryRaise`'s own
        writer), so the match is the event of the SAME inquiry with the smallest
        `raised_at` inside `[row.created_at - 1s, row.created_at + 10 min]` - the prod
        gap measured 1.3 seconds (SO390524 / OI-2609-0731, 25 Sep 2026). The UPPER
        bound is what keeps a row that has no event of its own from latching onto the
        next reconfirm on the same inquiry (reviewer B1, round 1: 2,070 sheet-migrated
        rows read "Reconfirmed by Jayson Foundryx" off an event 1 to 23 hours later).
        Measured on the 24 Sep prod copy: 10,851 real matches within 1.8s, 85 between
        2s and 67s, then nothing until 1h 14m - ten minutes sits in the empty stretch.
        `None` on all three when nothing falls inside the window.

        ONE grouped query for the whole PAGE, never one per row: every raise event of
        every inquiry the page's rows belong to, read once and matched in Python. The
        window itself is `nearest_raise_event` (`app.services.scm.raise_event_matching`),
        shared with `decision_trail_service.py` so the two never drift apart.
        """
        inquiry_ids = {row.order_inquiry_id for row in rows if row.order_inquiry_id}
        if not inquiry_ids:
            return {}
        events = (
            self.db.query(
                OrderInquiryRaise.order_inquiry_id,
                OrderInquiryRaise.kind,
                OrderInquiryRaise.raised_at,
                User.name,
            )
            .outerjoin(User, User.id == OrderInquiryRaise.raised_by)
            .filter(OrderInquiryRaise.order_inquiry_id.in_(inquiry_ids))
            .order_by(OrderInquiryRaise.raised_at.asc())
            .all()
        )
        events_by_inquiry: Dict[str, List[Tuple[datetime, str, Optional[str]]]] = {}
        for inquiry_id, kind, raised_at, name in events:
            events_by_inquiry.setdefault(str(inquiry_id), []).append(
                (raised_at, kind, name)
            )

        out: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            candidates = events_by_inquiry.get(str(row.order_inquiry_id or ""), [])
            # `row.raised_at` IS `OrderInquiryRow.created_at` (`_RAISED_AT` above) -
            # this row's own birth, the moment the window is measured around.
            match = nearest_raise_event(row.raised_at, candidates)
            out[row.id] = (
                {"at": match[0], "kind": match[1], "by_name": match[2]}
                if match
                else {"at": None, "kind": None, "by_name": None}
            )
        return out

    def _serialize(
        self,
        row,
        product_by_row: Optional[Dict[str, Optional[str]]] = None,
        link_candidates: Optional[Dict[str, set]] = None,
        flow: Optional[Dict[str, Dict[str, Decimal]]] = None,
        links: Optional[Dict[str, List[Dict[str, Any]]]] = None,
        bundle_map: Optional[Dict[str, List[str]]] = None,
        anchor_headline_by_id: Optional[Dict[str, str]] = None,
        host_changes_by_row_id: Optional[Dict[str, List[Dict[str, Any]]]] = None,
        suggested_links: Optional[Dict[str, List[Dict[str, Any]]]] = None,
        raise_events_by_row: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        line_flow = (flow or {}).get(row.so_line_id, {})
        row_links = (links or {}).get(row.id, [])
        # SO314594: `linked_qty` is real coverage on THIS row - a synthetic derived-SPO
        # entry (`_append_derived_spo_entries`) never wrote an `order_inquiry_links`
        # row, so it stays out of the sum the same way `_anchor_headline_by_id` now
        # excludes it; the full `row_links` (derived entries included) still goes out
        # under `"links"` below for the chip that reads them.
        linked_qty = sum(
            (_dec(link["qty"]) for link in row_links if not link.get("derived")), _ZERO
        )
        return {
            "id": row.id,
            "inquiry_no": row.inquiry_no,
            "so_date": row.so_date,
            "so_number": row.so_number,
            "item_code": row.item_code,
            # PLAN-oi-request-cs-reserve.md section 6 item 1: the stock grid keys on the
            # product's id, never the item code string - two products share one code on
            # the live book. Already on `row` (`_COLUMNS` selects `Product.id`);
            # `response_model` drops what it is not told about.
            "product_id": row.product_id,
            "product_name": row.product_name,
            "qty": _qty_str(_dec(row.qty)),
            "delivery_date": row.delivery_date,
            "project_customer": project_customer_label(
                row.customer_name, row.project_title, row.is_pre_order
            ),
            # PLAN-oi-worklist-split-customer-project.md: the two columns Customer and
            # Project print from now on, `project_customer` staying on the row for the
            # Excel export and search that still read it. Project carries the PRE-ORDER
            # note the combined label appends, so a pre-order row still reads as one
            # once the two are apart.
            "customer_name": row.customer_name,
            "project_title": project_title_with_note(row.project_title, row.is_pre_order),
            "supplier": row.supplier,
            "supplier_id": row.supplier_id,
            # D8: a bundled row with no document of its own names its anchor instead of
            # a blank cell.
            "po_number": row.po_number or self._bundled_po_number(row, bundle_map),
            "po_id": row.po_id,
            "location": row.location,
            "taken_from_po": _qty_str(line_flow.get("taken", _ZERO)),
            "remaining_open": _qty_str(line_flow.get("remaining", _ZERO)),
            # PLAN-oi-request-cs-reserve.md 3.4/3.5 (AC-RS-12/AC-RS-20): `requested` while
            # an open request row exists, else `reserved` once something has actually
            # been reserved, else null - an open request always wins (R5: reserved then
            # requested again on the balance reads `requested`, never `reserved`).
            "reserve_state": self._reserve_state(row),
            "reserved_qty": _qty_str(_dec(getattr(row, "reserved_qty", None))),
            # 6e.2: "0" when there is no open request row, same default shape as
            # `reserved_qty` above.
            "requested_qty": _qty_str(_dec(getattr(row, "requested_qty", None))),
            # WHERE this row's quantity sits (AC-I5), off the ONE reader the per-project
            # list and the SCM sales-order detail also use.
            "links": row_links,
            # AC-LT-33: the cascade's own guesses, kept separate from `links` above,
            # which carries nothing suggested.
            "suggested_links": (suggested_links or {}).get(row.id, []),
            "linked_qty": _qty_str(linked_qty),
            "cited_document": row.cited_document,
            # PLAN-scm-supplied-with-companions.md S5. `response_model` drops what it is
            # not told about - both asserted in `test_order_inquiry_bundles.py::test_d7`.
            "bundled_qty": _qty_str(_dec(row.bundled_qty)),
            "bundled_with": (
                {
                    "row_id": row.bundled_with_row_id,
                    "item_code": row.bundled_with_item_code,
                    "item_codes": resolve_bundled_item_codes(
                        bundle_map or {},
                        companion_item_code=row.item_code,
                        anchor_item_code=row.bundled_with_item_code,
                    ),
                    # Review round 1 item 8: the anchor's OWN coverage, resolved here
                    # rather than the client scanning `table.options.data` for a row
                    # that might not even be on the same page.
                    "anchor_headline": (anchor_headline_by_id or {}).get(
                        row.bundled_with_row_id
                    ),
                }
                if row.bundled_with_row_id
                else None
            ),
            # PLAN-oi-bundled-row-host-change.md: each HOST's own change, read from the
            # host rows at display time - null on a non-bundled row, never an empty
            # list. `response_model` drops what it is not told about.
            "bundled_host_changes": (
                (host_changes_by_row_id or {}).get(row.id)
                if row.bundled_with_row_id
                else None
            ),
            "has_link_candidate": (
                ProjectOrderInquiryService.has_link_candidate(
                    row.verb, product_by_row.get(row.id), link_candidates
                )
                if product_by_row and link_candidates
                else False
            ),
            "agent_code": row.agent_code,
            "agent_label": row.agent_label,
            "state": row.state,
            "ack_state": row.ack_state,
            "acknowledged_by_name": row.acknowledged_by_name,
            "acknowledged_at": row.acknowledged_at,
            "rejected_by_name": row.rejected_by_name,
            "rejected_at": row.rejected_at,
            "rejected_reason": row.rejected_reason,
            "changed_at": row.changed_at,
            "previous_qty": (
                _qty_str(_dec(row.previous_qty)) if row.previous_qty is not None else None
            ),
            "previous_delivery_date": row.previous_delivery_date,
            "previous_item_code": row.previous_item_code,
            # AC-RL-16: a replan could not carry this row's coverage forward - it is
            # history now, and the FE marks it and excludes it from the cards.
            "redirected_to_pool": bool(row.redirected_to_pool),
            # PLAN-oi-cancelled-line-used-confirm.md (AC-CL-1): true only when the line
            # itself is cancelled - `closed` and `open` both read false.
            "line_cancelled": bool(row.line_cancelled),
            "raised_at": row.raised_at,
            "raised_by_name": row.raised_by_name,
            # AC-DT-3 (`PLAN-oi-decision-trail-ui.md`): the actual `order_inquiry_raises`
            # EVENT this row traces to - Raised or Reconfirmed, by whom, when - distinct
            # from `raised_by_name`/`raised_at` above (WHO currently owns the row, a
            # coalesce of the decision/acknowledger/header). `None` on all three when no
            # event matches - a row migrated before raises were recorded.
            "raise_event_kind": (raise_events_by_row or {}).get(row.id, {}).get("kind"),
            "raise_event_by_name": (
                (raise_events_by_row or {}).get(row.id, {}).get("by_name")
            ),
            "raise_event_at": (raise_events_by_row or {}).get(row.id, {}).get("at"),
            # PLAN-oi-worklist-split-customer-project.md, Slice 2: the Raised at cell's
            # own tooltip - the CANCELLED predecessor(s) of this row on the same SO line,
            # newest first. `[]` on a row with no SO line, or nothing prior. `getattr`,
            # not `row.raise_history`: `_all_rows` (the export) selects `_EXPORT_COLUMNS`,
            # which drops this column outright, so the export's own `row` namedtuple
            # never carries the attribute at all.
            "raise_history": getattr(row, "raise_history", None) or [],
            "verb": row.verb,
            "note": row.note,
            "project_id": row.project_id,
            "project_sales_order_id": row.project_sales_order_id,
            "core_sales_order_id": row.core_sales_order_id,
            # AC-B6-7 (`PLAN-board-oi-mechanical-22sep.md`, S6): the core LINE's own id,
            # which the "SO line" cell puts on
            # `/scm/sales-orders/<core_sales_order_id>?tab=lines&line=<core_line_id>`
            # beside `core_sales_order_id` above - null when the mirror has no core line.
            "core_line_id": row.core_line_id,
            # Fix round (22 Sep): AutoCount's own line number, beside the id above - the
            # S/O line cell's own `SO402757 · L5` label reads this.
            "line_no": row.line_no,
            # S1 (AC-ND-21): the sales order line's own Qty and No.
            "so_line_qty": (
                _qty_str(_dec(row.so_line_qty)) if row.so_line_qty is not None else None
            ),
            "so_line_no": row.so_line_no,
            # PR #1266 review S1: the mirror line, the Lines tab's fold key and the header
            # Lines count's own (G10). Present before AutoCount reconciles the line, when
            # `core_line_id` above is still null.
            "so_line_id": str(row.so_line_id) if row.so_line_id else None,
            # An adopted record is a mirror of a core sales order and has no project
            # registration; that pair is the whole distinction and the screen links on it.
            "is_adopted": bool(row.core_sales_order_id) and row.project_id is None,
        }

    # -------------------------------------------------------------- po detail

    def get_po_detail(self, po_id: str) -> Dict[str, Any]:
        """The "PO no" cell's popup: this purchase order's own header and every one of
        its lines (the captain, 20 Aug).

        Purchasing on this worklist holds `projects.projects.view`, not
        `scm.dashboard.view` - the same permission gotcha "Place on PO" already worked
        around - so this reads `purchase_orders` / `purchase_order_lines` off the
        PROJECTS router by plain ORM query rather than calling the SCM purchase-orders
        route. Both tables are `CompanyScopedMixin`, so the session-level company scope
        listener (`app.services.company_scope`) already restricts every query here to the
        caller's own company - a foreign company's PO id resolves to nothing, same as an
        unknown one.
        """
        po = self.db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first()
        if po is None:
            raise AppException(
                status_code=404,
                message="That purchase order no longer exists.",
                code="order_inquiry_po_not_found",
            )
        supplier = (
            self.db.query(Supplier).filter(Supplier.id == po.supplier_id).first()
            if po.supplier_id
            else None
        )
        lines = (
            self.db.query(
                PurchaseOrderLine.id,
                Product.product_code,
                Product.product_name,
                PurchaseOrderLine.qty_ordered,
                PurchaseOrderLine.qty_received,
                Warehouse.warehouse_code,
                PurchaseOrderLine.from_so_line_ref,
            )
            .select_from(PurchaseOrderLine)
            .outerjoin(Product, Product.id == PurchaseOrderLine.product_id)
            .outerjoin(Warehouse, Warehouse.id == PurchaseOrderLine.warehouse_id)
            .filter(PurchaseOrderLine.purchase_order_id == po.id)
            .order_by(Product.product_code.asc().nulls_last())
            .all()
        )
        line_ids = [str(line[0]) for line in lines]
        # ONE query for the whole document (AC-A5's rule, on this THIRD surface), never one
        # per line - the same reader the SCM purchase-order detail's Lines tab already
        # calls, so the book's linkage reads identically wherever a line is shown.
        book_so_by_ref = order_link_service.book_so_numbers_by_ref(
            self.db, [line[-1] for line in lines if line[-1]]
        )
        # Issue #1215 point 2: which LINE an order inquiry's placement sits on, not only
        # which document - a PO with two lines of the same item made the "Allocated to"
        # panel's Item column ambiguous. Summed here (never per-row in the loop below) so
        # the grid's own Allocated column and the panel agree about what "linked" means,
        # and left out of `_allocations_on`'s own real-time read of who holds a document
        # (that reader answers a different question, per row).
        allocated_by_line = {
            str(po_line_id): _dec(total)
            for po_line_id, total in self.db.query(
                OrderInquiryLink.po_line_id, func.sum(OrderInquiryLink.qty)
            )
            .join(OrderInquiryRow, OrderInquiryRow.id == OrderInquiryLink.row_id)
            .filter(
                OrderInquiryLink.po_line_id.in_(line_ids),
                OrderInquiryRow.state != INQUIRY_CANCELLED,
            )
            .group_by(OrderInquiryLink.po_line_id)
            .all()
        }
        return {
            "id": po.id,
            "po_number": po.po_number,
            "supplier_code": supplier.supplier_code if supplier else None,
            "supplier_name": supplier.supplier_name if supplier else None,
            "expected_date": po.expected_date,
            "status": po.status,
            "lines": [
                {
                    # Issue #1215 point 2: the line's own identity, so the FE can
                    # highlight the one line the opening row's link actually sits on -
                    # the SKU alone is ambiguous the moment a PO carries two lines of the
                    # same item.
                    "id": str(line_id),
                    "sku": sku,
                    "product_name": product_name,
                    "qty_ordered": _qty_str(_dec(qty_ordered)),
                    "qty_received": _qty_str(_dec(qty_received)),
                    "remaining": _qty_str(_dec(qty_ordered) - _dec(qty_received)),
                    # Every order inquiry row's own placement on THIS line, summed -
                    # never netted against anything else, unlike the "Place on PO"
                    # candidate walk's own `remaining`. Zero rather than absent when
                    # nothing is linked here yet.
                    "allocated": _qty_str(allocated_by_line.get(str(line_id), _ZERO)),
                    "location": warehouse_code,
                    # The book's own SO linkage, read off the line's OWN
                    # `from_so_line_ref` - three states, and the ref itself never leaves
                    # the server (it is a machine key). Identical to what the SCM
                    # purchase-order detail's Lines tab serves, so one fact reads one way
                    # on both screens - through the SAME shared function
                    # (`order_link_service.book_so_fields`, review of PR #764, F5) rather
                    # than a second copy of the derivation.
                    **order_link_service.book_so_fields(from_so_line_ref, book_so_by_ref),
                }
                for (
                    line_id,
                    sku,
                    product_name,
                    qty_ordered,
                    qty_received,
                    warehouse_code,
                    from_so_line_ref,
                ) in lines
            ],
            # WHO is holding this document's quantity (AC-D18). Drafts included and marked
            # as such: they occupy the quantity, so a panel that hid them would tell the
            # buyer a line is free when the next Confirm is going to take it. Real links
            # only - a suggestion never appears here (AC-LT-34).
            "allocations": self._allocations_on(po_line_ids=line_ids),
        }

    # -------------------------------------------------------------- spo detail

    def get_spo_detail(self, spo_number: str) -> Dict[str, Any]:
        """The "SPO no" cell's lightbox: one shipping order, every allocation line it has.

        Addressed by NUMBER, because that is what a shipping order IS here: a set of
        `spo_allocations` rows sharing a number, with no header table behind it and no id
        for a person to quote. Gated the same as the PO lightbox next door
        (`projects.projects.view`), and company-scoped by the session listener the same
        way - a foreign company's document resolves to nothing, exactly as an unknown
        number does.

        EVERY VISIBLE line is listed, including one at a location outside the pool set that
        the cascade will never draft onto (R11). Hiding it would leave the buyer reading a
        document that says 50 while the page offers none of it, with nothing on screen to
        explain the difference. A retired line (PLAN-hide-retired-everywhere R7) is not
        listed at all, and the header's eta, supplier and container below are derived from
        the visible lines only, so a retired line cannot set any of the three.
        """
        from app.services.scm import spo_supply

        wanted = (spo_number or "").strip()
        rows = (
            self.db.query(
                SPOAllocation,
                Product.product_code,
                Product.product_name,
                Warehouse.warehouse_code,
            )
            .select_from(SPOAllocation)
            .outerjoin(Product, Product.id == SPOAllocation.product_id)
            .outerjoin(Warehouse, Warehouse.id == SPOAllocation.warehouse_id)
            .filter(
                SPOAllocation.spo_number == wanted,
                *spo_supply.visible_line_clauses(),
            )
            .order_by(
                SPOAllocation.spo_line_number.asc().nulls_last(),
                SPOAllocation.id.asc(),
            )
            .all()
        )
        if not rows:
            raise AppException(
                status_code=404,
                message="That shipping order could not be found.",
                code="order_inquiry_spo_not_found",
            )
        allocations = [allocation for allocation, *_rest in rows]
        supplier_ids = {
            str(allocation.supplier_id)
            for allocation in allocations
            if allocation.supplier_id
        }
        supplier = (
            self.db.query(Supplier)
            .filter(Supplier.id.in_(list(supplier_ids)))
            .order_by(Supplier.id)
            .first()
            if supplier_ids
            else None
        )
        shipment_ids = {
            str(allocation.inbound_shipment_id)
            for allocation in allocations
            if allocation.inbound_shipment_id
        }
        shipment = None
        if shipment_ids:
            from app.models.procurement import InboundShipment

            shipment = (
                self.db.query(InboundShipment)
                .filter(InboundShipment.id.in_(list(shipment_ids)))
                .order_by(InboundShipment.id)
                .first()
            )
        # The EARLIEST date the document's own lines state: what a person means by "when
        # does this land". Absent when the book named none, rather than today.
        etas = [
            allocation.expected_date
            for allocation in allocations
            if allocation.expected_date
        ]
        return {
            "spo_number": wanted,
            "supplier_name": supplier.supplier_name if supplier else None,
            "eta": min(etas) if etas else None,
            "shipment_ref": shipment.shipment_number if shipment else None,
            # The container the goods travel in, by its own column name
            # (`inbound_shipments.shipping_container_number`).
            "container_no": shipment.shipping_container_number if shipment else None,
            "lines": [
                {
                    # R15 (owner rulings, 25 Sep 2026, hand test on stack C): the line's
                    # own identity, the same reason `get_po_detail` sends one for its own
                    # lines (issue #1215 point 2) - without it the FE has no field to
                    # highlight this exact allocation by.
                    "id": str(allocation.id),
                    "sku": product_code,
                    "product_name": product_name,
                    # R31b (stock debt lane): the document dialog's own `highlightLines`
                    # names which line a demand line drew from - off this, not a second
                    # lookup.
                    "spo_line_number": allocation.spo_line_number,
                    "allocated": _qty_str(_dec(allocation.allocated_quantity)),
                    "received": _qty_str(_dec(allocation.quantity_received)),
                    "remaining": _qty_str(
                        _dec(allocation.allocated_quantity)
                        - _dec(allocation.quantity_received)
                    ),
                    # The warehouse we hold, else the code the book printed, else nothing
                    # - and the screen says "no location" rather than inventing one.
                    "location": warehouse_code or allocation.location_code,
                    # Owner's 9 Sep feedback: "if we link by SPO, where do we see the PO
                    # number of this SPO?" - the raw AutoCount pass-through
                    # (`from_po_number`, migration 493 / contract 2.2), never
                    # `from_po_line_ref` (the resolver key, not a thing a buyer reads).
                    # Same wire name as `links_for_rows`' own `source_po_number` (the
                    # worklist's backing-documents dialog) - one fact, one field name,
                    # wherever a buyer meets an SPO. Null when the book named no source.
                    "source_po_number": allocation.from_po_number,
                }
                for allocation, product_code, product_name, warehouse_code in rows
            ],
            "allocations": self._allocations_on(
                spo_allocation_ids=[str(allocation.id) for allocation in allocations]
            ),
        }

    # ------------------------------------------------------- who holds a document

    def _allocations_on(
        self,
        *,
        po_line_ids: Optional[Sequence[str]] = None,
        spo_allocation_ids: Optional[Sequence[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Every order inquiry row holding quantity on these document lines (AC-D18).

        ONE reader for both lightboxes, because it is one question asked of two books. The
        ROW's `ack_state` travels with each entry and is what makes the panel read Proposed
        or Confirmed: a link carries no state of its own (R1), so the row is the answer.

        A cancelled row's links are history rather than a claim on the document, and are
        left out for the same reason `links_for_rows` leaves them out.
        """
        targets = []
        if po_line_ids:
            targets.append(OrderInquiryLink.po_line_id.in_(list(po_line_ids)))
        if spo_allocation_ids:
            targets.append(OrderInquiryLink.spo_allocation_id.in_(list(spo_allocation_ids)))
        if not targets:
            return []
        rows = (
            self.db.query(
                OrderInquiryLink.qty,
                OrderInquiryLink.linked_at,
                # Issue #1215 point 2: which LINE this allocation sits on - the panel
                # named only the document before, and a PO with two lines of the same
                # item could not say which one. `spo_allocation_id` is never sent: the
                # SPO lightbox already addresses its own lines by number, not by id.
                OrderInquiryLink.po_line_id,
                OrderInquiryRow.item_code,
                OrderInquiryRow.ack_state,
                OrderInquiry.inquiry_no,
                ProjectSalesOrder.autocount_doc_no,
                ProjectSalesOrder.provisional_ref,
            )
            .select_from(OrderInquiryLink)
            .join(OrderInquiryRow, OrderInquiryRow.id == OrderInquiryLink.row_id)
            .join(OrderInquiry, OrderInquiry.id == OrderInquiryRow.order_inquiry_id)
            .outerjoin(
                ProjectSalesOrder,
                ProjectSalesOrder.id == OrderInquiry.project_sales_order_id,
            )
            .filter(
                or_(*targets),
                OrderInquiryRow.state != INQUIRY_CANCELLED,
            )
            .order_by(OrderInquiryLink.linked_at.asc(), OrderInquiryLink.id.asc())
            .all()
        )
        return [
            {
                "inquiry_no": inquiry_no,
                # The AutoCount number where the order has one, the reference this system
                # minted where it does not. Never the id.
                "so_number": autocount_doc_no or provisional_ref,
                "item_code": item_code,
                "qty": _qty_str(_dec(qty)),
                "ack_state": ack_state,
                "linked_at": linked_at,
                "po_line_id": str(po_line_id) if po_line_id else None,
            }
            for (
                qty,
                linked_at,
                po_line_id,
                item_code,
                ack_state,
                inquiry_no,
                autocount_doc_no,
                provisional_ref,
            ) in rows
        ]

    # ---------------------------------------------------------------- summary

    def summary(self, **filters) -> Dict[str, Any]:
        """The strip above the list, and the controls beside it.

        Totals honour EVERY filter, month included, because they describe what is on
        screen. Each axis drops its OWN filter, because a control that empties itself the
        moment you use it cannot be used a second time.
        """
        visible = self._base(**filters)
        state_rows = (
            visible.with_entities(
                OrderInquiryRow.state,
                func.count(OrderInquiryRow.id),
                func.coalesce(func.sum(OrderInquiryRow.qty), 0),
            )
            .group_by(OrderInquiryRow.state)
            .all()
        )
        by_state = {state: 0 for state in INQUIRY_STATES}
        total_rows = 0
        total_qty = _ZERO
        for state, count, qty in state_rows:
            by_state[state] = int(count)
            total_rows += int(count)
            total_qty += _dec(qty)
        # S5/AC-OH-52: `visible` above already hides `cancelled` by default (AC-OH-50), so
        # `by_state["cancelled"]` would otherwise read 0 the moment the State filter most
        # needs to offer its real count. The State facet is the one reader that must see
        # it regardless - a second grouped count, `total_rows`/`total_qty` untouched.
        cancelled_count = (
            self._base(**filters, include_cancelled=True)
            .filter(OrderInquiryRow.state == INQUIRY_CANCELLED)
            .with_entities(func.count(OrderInquiryRow.id))
            .scalar()
            or 0
        )
        by_state[INQUIRY_CANCELLED] = int(cancelled_count)
        by_state["total"] = total_rows

        return {
            "total_rows": total_rows,
            "total_qty": _qty_str(total_qty),
            "by_state": by_state,
            "by_month": self._by_month({**filters, "delivery_month": None}),
            "suppliers": self._suppliers({**filters, "supplier_id": None}),
            "projects": self._projects({**filters, "project_id": None, "project": None}),
            "raised_by": self._raised_by({**filters, "raised_by": None}),
            # S1, R-K: the Location and Agent filters' own lists, same shape as
            # `suppliers` (`[{id,label,rows}]`), each with its own filter dropped.
            "locations": self._locations({**filters, "location": None}),
            "agents": self._agents({**filters, "agent": None}),
            # The three cards, computed with the CARD FILTER ITSELF DROPPED, for the
            # reason every other axis here drops its own: a card that empties the two
            # beside it the moment it is pressed cannot be pressed a second time.
            "kinds": self._kinds({**filters, "kind": None}),
            # The four acknowledgement counts, computed with the ACK FILTER ITSELF
            # DROPPED, for the reason every other axis here drops its own.
            "ack": self._acks({**filters, "ack": None}),
            # Where the page's "Link up to" date starts (AC-LH5). The latest completed
            # reorder run's own "Plan until", read off the run rather than guessed at on
            # the page: one date, so a plan that netted to October and a buyer linking to
            # 2030 cannot both be right.
            "link_up_to_default": priority.plan_link_horizon(self.db),
        }

    def _acks(self, filters: Dict[str, Any]) -> Dict[str, int]:
        """How many rows sit at each acknowledgement state (AC-H4).

        ROWS, not quantity: acknowledging is a decision per instruction, and "three rows
        awaiting" is what the press says. Pre-seeded at zero so a state nothing is in is
        still offered - a filter value that disappears the moment it empties cannot be
        used to check that it emptied.
        """
        counts = {state: 0 for state in ACK_FILTER_STATES}
        rows = (
            self._base(**filters)
            .with_entities(OrderInquiryRow.ack_state, func.count(OrderInquiryRow.id))
            .group_by(OrderInquiryRow.ack_state)
            .order_by(None)
            .all()
        )
        for state, count in rows:
            if state in counts:
                counts[state] = int(count)
        # The grouped `ack_state` above is trusted for `changed` too now
        # (`PLAN-oi-confirm-per-so.md` S1): there is no auto re-ack left to leave a row
        # reading `changed_at IS NOT NULL` while its `ack_state` says something else, and
        # reading `changed_at` here would OVER-count a row genuinely re-acknowledged
        # since (its `changed_at` stays as history; the facet, the filter and the cell
        # all have to agree, and the filter and the cell both read `ack_state`).
        # The default view's own count (R3), summed from the two states rather than
        # queried again: a second query could disagree with the chip beside it.
        counts[ACK_TO_CONFIRM] = sum(
            counts[state] for state in ACK_TO_CONFIRM_STATES
        )
        return counts

    def _stage_rows(
        self,
        filters: Dict[str, Any],
        *,
        extra_columns: Sequence[Any] = (),
        extra_filters: Sequence[Any] = (),
    ) -> Any:
        """ONE ROW PER INQUIRY ROW - its quantity and its three stage amounts, computed
        ONCE - as a subquery to aggregate over.

        The cards (`_kinds`) and the Schedule matrix both read it, which is the whole
        reason it exists: a cell and the card above it are two GROUP BYs over the same
        per-row arithmetic rather than two copies of the formula, so the Schedule view
        cannot answer differently from the strip over it (AC-X6).

        S8 (AC-OH-80..81, measured on `sorento_ai_automation_0915_1900`): `_kinds`, which
        reads this over the WHOLE matching row set unpaginated, was the page's slowest
        request by a wide margin (~1.2s of summary()'s ~1.5s). `EXPLAIN ANALYZE` on the
        old shape showed why - `_purchased_qty()` called `_incoming_qty()` fresh inside
        its own formula, so the correlated subqueries under `_incoming_qty` (SPO-linked,
        PO-linked, the derived-cover EXISTS+scalar-subquery) were embedded TWICE in the
        generated SQL, and `_UNLINKED_QTY` added a third, separate `_linked_qty()`
        correlated subquery on top - up to nine correlated-subquery evaluations per row.
        Fixed at this one seam, not by touching `_incoming_qty`/`_purchased_qty`
        themselves (S4's `kind=po` filter still calls `_purchased_qty()` alone, over a
        WHERE clause rather than a company-wide aggregate, where the duplication never
        showed up): an INNER subquery computes each correlated piece exactly ONCE per
        row, and the three stage columns are then plain arithmetic over those already-
        materialized inner columns.
        """
        inner = (
            self._base(**filters)
            .with_entities(
                OrderInquiryRow.id.label("row_id"),
                OrderInquiryRow.qty.label("qty"),
                OrderInquiryRow.bundled_qty.label("bundled_qty"),
                _SPO_LINKED_QTY.label("spo_linked"),
                _PO_LINKED_QTY.label("po_linked"),
                self._derived_cover_qty().label("derived_cover"),
                _linked_qty().label("linked_any"),
                # PLAN-oi-cancelled-line-used-confirm.md (AC-CL-4): so `buy` alone can
                # zero out below - `incoming`/`purchased` stay real (a cancelled-line
                # row that already holds a link still counts in Purchased/Incoming, C4).
                _LINE_CANCELLED.label("line_cancelled"),
                *extra_columns,
            )
            .filter(*extra_filters)
            .order_by(None)
            .cte("order_inquiry_stage_rows")
            .prefix_with("MATERIALIZED")
        )
        capped_derived_cover = func.least(inner.c.po_linked, inner.c.derived_cover)
        incoming = func.least(inner.c.qty, inner.c.spo_linked + capped_derived_cover)
        purchased = func.least(
            inner.c.qty - incoming,
            func.greatest(0, inner.c.po_linked - capped_derived_cover),
        )
        # AC-CL-4: a row on a cancelled line owes nothing to Buy - nobody will buy it -
        # while `incoming`/`purchased` above stay real for a row that already holds a
        # link (C4). R1 (23 Sep 2026): uncapped, so Buy reads off `inner.c.qty` - the
        # row's own quantity, the SAME column `incoming`/`purchased` already read above -
        # rather than a separate "capped_qty" that would now just equal it.
        buy = case(
            (inner.c.line_cancelled, 0),
            else_=func.greatest(
                inner.c.qty - inner.c.linked_any - inner.c.bundled_qty, 0
            ),
        )
        return select(
            inner.c.row_id,
            inner.c.qty,
            incoming.label("incoming"),
            purchased.label("purchased"),
            buy.label("buy"),
            *[getattr(inner.c, column.name) for column in extra_columns],
        ).subquery()

    def _kinds(self, filters: Dict[str, Any]) -> Dict[str, str]:
        """Quantity per STAGE over every matching row (AC-I11, S5/R-F): incoming (on an
        SPO allocation, own link or derived via its linked PO), purchased (on a purchase
        order line but not yet on a shipment), and the unlinked remainder that still has
        to be bought. A unit counts once, the furthest stage it reached.

        SUMMED SERVER-SIDE OVER THE WHOLE MATCHING SET, never over a page, so a card
        cannot claim less than pressing it reveals. Cancelled and actioned rows are
        dropped by the same rule the `kind` filter drops them (`_NOT_OWED_STATES`), so
        the cards and the rows agree.

        A REDIRECTED row is dropped entirely too (AC-RL-16, `PLAN-oi-replan-received-
        links.md` S3): a replan could not carry its coverage forward, so the document it
        still shows as history is not owed here any more than a cancelled row's is - the
        fresh row raised in its place is what actually counts toward Buy.
        """
        stages = self._stage_rows(
            filters,
            extra_filters=(
                OrderInquiryRow.state.notin_(_NOT_OWED_STATES),
                OrderInquiryRow.redirected_to_pool.is_(False),
            ),
        )
        incoming, purchased, buy = self.db.query(
            func.coalesce(func.sum(stages.c.incoming), 0),
            func.coalesce(func.sum(stages.c.purchased), 0),
            func.coalesce(func.sum(stages.c.buy), 0),
        ).one()
        return {
            "spo": _qty_str(_dec(incoming)),
            "po": _qty_str(_dec(purchased)),
            "buy": _qty_str(_dec(buy)),
        }

    def _by_month(self, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        month = func.to_char(OrderInquiryRow.delivery_date, "YYYY-MM")
        rows = (
            self._base(**filters)
            .with_entities(
                month.label("month"),
                func.count(OrderInquiryRow.id),
                func.coalesce(func.sum(OrderInquiryRow.qty), 0),
            )
            .filter(OrderInquiryRow.delivery_date.isnot(None))
            .group_by(month)
            .order_by(month.asc())
            .all()
        )
        return [
            {
                "month": value,
                "label": month_label(value),
                "rows": int(count),
                "qty": _qty_str(_dec(qty)),
            }
            for value, count, qty in rows
        ]

    def _suppliers(self, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        rows = (
            self._base(**filters)
            .with_entities(
                Supplier.id, Supplier.supplier_name, func.count(OrderInquiryRow.id)
            )
            .filter(Supplier.id.isnot(None))
            .group_by(Supplier.id, Supplier.supplier_name)
            .order_by(Supplier.supplier_name.asc())
            .all()
        )
        return [
            {"id": supplier_id, "label": name, "rows": int(count)}
            for supplier_id, name, count in rows
        ]

    def _locations(self, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        """S1, R-K: the Location filter's own list. `_LOCATION` is a plain warehouse
        code, not an FK, so the code IS the id - the same shape a picker built off a real
        id-bearing table offers, with no id of its own to leak into the UI."""
        rows = (
            self._base(**filters)
            .with_entities(_LOCATION, func.count(OrderInquiryRow.id))
            .filter(_LOCATION.isnot(None))
            .group_by(_LOCATION)
            .order_by(_LOCATION.asc())
            .all()
        )
        return [
            {"id": location, "label": location, "rows": int(count)}
            for location, count in rows
        ]

    def _agents(self, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        """S1, R-K: the Agent filter's own list, off the same core sales order the
        Agent column already reads."""
        rows = (
            self._base(**filters)
            .with_entities(
                SalesAgent.id,
                SalesAgent.sales_agent,
                SalesAgent.person_label,
                func.count(OrderInquiryRow.id),
            )
            .filter(SalesAgent.id.isnot(None))
            .group_by(SalesAgent.id, SalesAgent.sales_agent, SalesAgent.person_label)
            .order_by(SalesAgent.sales_agent.asc())
            .all()
        )
        return [
            {"id": agent_id, "label": label or code, "rows": int(count)}
            for agent_id, code, label, count in rows
        ]

    def _projects(self, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        """The Project filter's own list, off `_PROJECT_TITLE` - the SAME text the
        column prints - not `Project.id`/`Project.title` alone (S5,
        `PLAN-oi-project-label-from-so.md` section 5). An adopted order has no
        `Project` row at all, so grouping on the registered project only left the
        dropdown empty: 515 distinct labels across 12,716 rows and zero registered
        projects, measured on the prod-copy clone. The option's own `id` is the text
        itself - there is no second, id-shaped concept to encode - and `project`
        (like every other axis here) filters exact-match on that same text.
        """
        rows = (
            self._base(**filters)
            .with_entities(_PROJECT_TITLE, func.count(OrderInquiryRow.id))
            .filter(_PROJECT_TITLE.isnot(None))
            .group_by(_PROJECT_TITLE)
            .order_by(_PROJECT_TITLE.asc())
            .all()
        )
        return [
            {"id": title, "label": title, "rows": int(count)} for title, count in rows
        ]

    def _raised_by(self, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        """The people who have actually raised an inquiry, and how many rows each.

        Scoped to the rows in view rather than to `users`, deliberately: a picker built
        from the user table would list hundreds of names, almost all of which return
        nothing, and the one question this filter answers is "show me what CS raised".
        Bounded by the same fact - a company has a handful of people who confirm supply -
        so it ships whole with the summary rather than as a searched endpoint.
        """
        rows = (
            self._base(**filters)
            .with_entities(
                _RAISED_BY_ID, User.name, func.count(OrderInquiryRow.id)
            )
            .filter(_RAISED_BY_ID.isnot(None))
            .group_by(_RAISED_BY_ID, User.name)
            .order_by(User.name.asc().nulls_last())
            .all()
        )
        return [
            {"id": user_id, "label": name or "Unnamed user", "rows": int(count)}
            for user_id, name, count in rows
        ]

    # -------------------------------------------------------------------- matrix

    #: The vertical axis, each naming its own grouping column and its own human label
    #: (S3, R-I second half). Product first, in the order the captain named them.
    #:
    #: The sales order key falls back to the PROJECT sales order's own id, the way the
    #: `_SO_NUMBER` label beside it already falls back to `provisional_ref`: an AUTHORED
    #: order that was never adopted from the book has `so_id` NULL, and keying on the
    #: core order alone dropped every one of its rows off the axis entirely (SF-3).
    _MATRIX_AXES = {
        "product": (Product.id, Product.product_code),
        "sales_order": (
            func.coalesce(SalesOrder.id, ProjectSalesOrder.id),
            _SO_NUMBER,
        ),
        "customer": (Customer.id, Customer.customer_name),
        "agent": (SalesAgent.id, SalesAgent.sales_agent),
    }
    #: Postgres `date_trunc` unit per granularity. `week` truncates to Monday under
    #: Postgres's own ISO-8601 week reckoning - no manual weekday arithmetic needed for
    #: AC-X3, the same fact the FE's date-fns build used to compute client-side.
    _MATRIX_TRUNC = {"day": "day", "week": "week", "month": "month", "year": "year"}

    def _matrix_axis(self, axis: str) -> Tuple[Any, Any]:
        """The grouping column and its label, refused rather than guessed at. The list's
        own `axis`/`axis_key` drilldown filter reads the same map, so a cell and the rows
        it opens cannot group by two different columns."""
        if axis not in self._MATRIX_AXES:
            raise AppException(
                422,
                f"'{axis}' is not a matrix axis. Use "
                f"{', '.join(self._MATRIX_AXES)}.",
                code="invalid_matrix_axis",
            )
        return self._MATRIX_AXES[axis]

    def _matrix_trunc(self, by: str) -> str:
        """REFUSED, never silently bucketed by week: a Schedule view headed "by
        fortnight" while the server cut the data by week is a screen lying about what it
        is showing, the same reason an unknown `axis` or sort column is a 422."""
        if by not in self._MATRIX_TRUNC:
            raise AppException(
                422,
                f"'{by}' is not a matrix granularity. Use "
                f"{', '.join(self._MATRIX_TRUNC)}.",
                code="invalid_matrix_granularity",
            )
        return self._MATRIX_TRUNC[by]

    def matrix(self, *, axis: str, by: str = "week", **filters) -> List[Dict[str, Any]]:
        """The Schedule matrix's own read (S3): one GROUP BY over the SAME filtered set
        the list reads, axis by date bucket, no page and no cap - the old Schedule view
        fetched the list once with `limit=1000` and grouped client-side, which a
        delivery-filtered worklist has already exceeded on prod (PLAN section 0).

        `qty`/`rows`/the stage sums drop a CANCELLED row (AC-X5, round 2) - its
        quantity is not owed any more, and counting it would inflate a cell nobody
        can act on. An ACTIONED row still counts, unlike `_kinds`/`kind=` (which drops
        both via `_NOT_OWED_STATES`): it has been answered somewhere else, but the
        matrix is a read of what was DELIVERY-DUE in a period, not of what is still
        outstanding, and an actioned row was still due then.

        The stage sums are the SAME per-row arithmetic the cards read (AC-X6), computed
        once per row in `_stage_rows` and grouped over here - not a second copy of the
        formula, which is how the uncapped derived cover reached the Schedule view.

        AC-RL-16b addendum (`PLAN-oi-replan-received-links.md` S3): a REDIRECTED row is
        dropped too, the same way `_kinds` drops it - its coverage already shipped to
        another order, so its history row must not inflate this matrix's `po`/`spo`
        cells or its own `rows`/`qty` either.
        """
        axis_key, axis_label = self._matrix_axis(axis)
        period = cast(
            func.date_trunc(self._matrix_trunc(by), OrderInquiryRow.delivery_date), Date
        )
        stages = self._stage_rows(
            filters,
            extra_columns=(
                axis_key.label("axis_key"),
                axis_label.label("axis_label"),
                period.label("period"),
            ),
            extra_filters=(
                OrderInquiryRow.delivery_date.isnot(None),
                axis_key.isnot(None),
                # AC-X5: cancelled is out, actioned stays - narrower than
                # `_NOT_OWED_STATES` (which `_kinds`/`kind=` drop both by).
                OrderInquiryRow.state != INQUIRY_CANCELLED,
                # AC-RL-16b: a redirected row is history, never a cell's own quantity.
                OrderInquiryRow.redirected_to_pool.is_(False),
            ),
        )
        rows = (
            self.db.query(
                stages.c.axis_key,
                stages.c.axis_label,
                stages.c.period,
                func.coalesce(func.sum(stages.c.qty), 0),
                func.coalesce(func.sum(stages.c.buy), 0),
                func.coalesce(func.sum(stages.c.purchased), 0),
                func.coalesce(func.sum(stages.c.incoming), 0),
                func.count(stages.c.row_id),
            )
            .group_by(stages.c.axis_key, stages.c.axis_label, stages.c.period)
            .order_by(stages.c.axis_label.asc(), stages.c.period.asc())
            .all()
        )
        return [
            {
                "axis_key": str(axis_key_value),
                "axis_label": axis_label_value,
                "period": period_value.isoformat(),
                "qty": _qty_str(_dec(qty)),
                "buy": _qty_str(_dec(buy)),
                "po": _qty_str(_dec(po)),
                "spo": _qty_str(_dec(spo)),
                "rows": int(count),
            }
            for (
                axis_key_value,
                axis_label_value,
                period_value,
                qty,
                buy,
                po,
                spo,
                count,
            ) in rows
        ]

    # ------------------------------------------------------------- unplace all

    def unplace_all_preview(self, **filters) -> Dict[str, Any]:
        """The confirm dialog's own numbers (the captain, 21 Aug: "why i cannot unplace
        all" - the answer was the count and the scope were wrong, not that the action
        should be blocked), resolved server-side against the SAME filters `list_rows`
        reads - `state` is never one of them, because this is always about LINKED rows,
        whatever else is filtered. Since section 3.I that is a link test rather than a
        state test: a partly linked row holds links too, and leaving it out would have made
        "Unlink all" quietly refuse to give back the half a cascade had covered.

        `product_code`/`product_name` are best-effort labelling only, not a second scope:
        when every matching row resolves to the SAME product, the dialog can say which
        one; when they do not (or none resolves any), it says nothing rather than picking
        one arbitrarily. `LIMIT 2` is enough to tell "one" from "more than one" without
        pulling the whole matching set.
        """
        # `linked` is forced, so whatever the caller sent for it is dropped rather than
        # passed twice - the same treatment `state` gets at the route, and for the same
        # reason: this action is about LINKED rows however the list happens to be
        # filtered. Sending both is a TypeError, which the route turns into a 500 and the
        # dialog into "could not check linked rows".
        filters.pop("linked", None)
        visible = self._base(**filters, linked="any")
        count = int(
            visible.with_entities(func.count(OrderInquiryRow.id)).order_by(None).scalar()
            or 0
        )
        product_code: Optional[str] = None
        product_name: Optional[str] = None
        if count:
            distinct_products = (
                visible.with_entities(Product.product_code, Product.product_name)
                .distinct()
                .limit(2)
                .all()
            )
            if len(distinct_products) == 1 and distinct_products[0][0]:
                product_code, product_name = distinct_products[0]
        return {
            "count": count,
            "product_code": product_code,
            "product_name": product_name,
        }

    def unplace_all(self, *, actor_user_id: str, **filters) -> int:
        """"Unplace all" for the CURRENT worklist scope - the SAME filters `list_rows`
        reads, forced to placed. Resolved as a fresh id list against the full matching
        set, never against whatever page happened to be loaded: the worklist paginates
        server-side (`list_rows`'s own `offset`/`limit`), so a client-derived scope would
        silently miss every row behind page 1. No filters at all means every linked row
        in the company - "unlink all" with nothing narrowing it is exactly that.

        The actual write is `ProjectOrderInquiryService.unplace_rows` - this method's own
        job stops at resolving WHICH rows are in scope; the two can never disagree about
        what a linked row is because both read off the same "holds a link" predicate.
        """
        filters.pop("linked", None)
        visible = self._base(**filters, linked="any")
        row_ids = [row_id for (row_id,) in visible.all()]
        return ProjectOrderInquiryService(self.db).unplace_rows(row_ids)

    # ----------------------------------------------------------------- export

    def export_xlsx(
        self, *, columns: Sequence[str] = EXPORT_HEADINGS, **filters
    ) -> Tuple[str, bytes]:
        """The filtered set as their own workbook: one sheet per delivery month.

        Within a sheet the rows go SUPPLIER then ITEM CODE, which is the order their own
        sheets are in and the order a buyer places in - one purchase order per factory.
        TOTAL QTY prints on the last row of each supplier-and-item-code run and totals
        that run, which is the one thing on their sheet that is arithmetic rather than
        typing. The run is the pair, not the item code alone: the same item bought from
        two factories is two purchase orders, and a total across both is a quantity
        nobody places.

        Generated per request rather than stored, exactly as the per-project export is: a
        stored file goes stale the moment supply is reconfirmed, and a stale instruction
        is the thing this replaces.

        `columns` (Lane C, AC-C3): the heading tuple to print, defaulting to the full
        `EXPORT_HEADINGS` so the list page's own export is unchanged. The OI worksheet
        passes `EXPORT_HEADINGS[:10]` (no ACKNOWLEDGED / TAKEN / REMAINING) - a prefix,
        so the row values below can be cut to the same length.
        """
        import openpyxl

        rows = self._all_rows(**filters)
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for row in rows:
            key = (
                row["delivery_date"].strftime("%Y-%m")
                if row["delivery_date"]
                else ""
            )
            grouped.setdefault(key, []).append(row)

        workbook = openpyxl.Workbook()
        # A new workbook opens with one blank sheet; every sheet here is named for a
        # month, so the blank one goes rather than sitting in front of them called
        # "Sheet".
        default_sheet = workbook.active
        if default_sheet is not None:
            workbook.remove(default_sheet)
        # Dated months in order, undated last: a row with no date is still an instruction
        # and is never dropped from the file.
        for key in sorted(month for month in grouped if month):
            self._write_sheet(workbook, month_label(key), grouped[key], columns=columns)
        if "" in grouped:
            self._write_sheet(
                workbook, EXPORT_UNDATED_SHEET, grouped[""], columns=columns
            )
        if not workbook.sheetnames:
            # An empty result is still a workbook a person can open and see the headings
            # of, rather than a file their spreadsheet refuses.
            self._write_sheet(workbook, EXPORT_TITLE, [], columns=columns)

        buffer = io.BytesIO()
        workbook.save(buffer)
        filename = f"order-inquiry-{date.today().isoformat()}.xlsx"
        return filename, buffer.getvalue()

    def _all_rows(self, **filters) -> List[Dict[str, Any]]:
        """The same set the list serves, unpaged, in the workbook's own order.

        `_EXPORT_COLUMNS`, not `_COLUMNS` (S2, review round 1): the sheet never prints
        `raise_history`, so there is no reason to run that `json_agg` for the whole
        unpaged set here.
        """
        rows = (
            self._base(**filters)
            .with_entities(*_EXPORT_COLUMNS)
            .order_by(
                OrderInquiryRow.delivery_date.asc().nulls_last(),
                Supplier.supplier_name.asc().nulls_last(),
                OrderInquiryRow.item_code.asc().nulls_last(),
                OrderInquiryRow.id.asc(),
            )
            .all()
        )
        bundle_map = self._bundle_map_for_rows(rows)
        # Fix round (22 Sep, AC-D15 parity): the SAME per-row links the paged list reads
        # (`list_rows` above) - without this, `_serialize`'s own `linked_qty` sums an
        # empty `links` dict for every row, and the export's new Taken/Remaining columns
        # would print "0"/the bare qty regardless of what is actually linked.
        links = ProjectOrderInquiryService(self.db).links_for_rows([row.id for row in rows])
        suggested_links = ProjectOrderInquiryService(self.db).suggested_links_for_rows(
            [row.id for row in rows]
        )
        return [
            self._serialize(
                row, bundle_map=bundle_map, links=links, suggested_links=suggested_links
            )
            for row in rows
        ]

    def _write_sheet(
        self,
        workbook,
        title: str,
        rows: Sequence[Dict[str, Any]],
        *,
        columns: Sequence[str] = EXPORT_HEADINGS,
    ) -> None:
        sheet = workbook.create_sheet(title=title[:31])
        sheet.append([EXPORT_TITLE])
        sheet.append(list(columns))
        # `￿` sorts after every real string, so a missing supplier or item code
        # lands at the end rather than at the top where a buyer would read it first.
        ordered = sorted(
            rows,
            key=lambda row: (
                (row.get("supplier") or "￿"),
                (row.get("item_code") or "￿"),
                row.get("delivery_date") or date.max,
            ),
        )
        runs = groupby(
            ordered, key=lambda row: (row.get("supplier"), row.get("item_code"))
        )
        for (_supplier, code), members in runs:
            run = list(members)
            total = sum((_dec(row.get("qty")) for row in run), _ZERO)
            for index, row in enumerate(run):
                last_of_run = index == len(run) - 1
                # Only where it says something the QTY column does not: a single-row
                # run has its own quantity as its total, and printing it twice is noise.
                run_total = float(total) if last_of_run and len(run) > 1 else None
                values = [
                    row.get("so_date"),
                    row.get("so_number") or "",
                    code or "",
                    float(_dec(row.get("qty"))),
                    run_total,
                    row.get("delivery_date"),
                    row.get("project_customer") or "",
                    # Blank means nobody has placed it, exactly as it does on their
                    # sheet.
                    row.get("supplier") or "",
                    row.get("po_number") or "",
                    row.get("location") or "",
                    ack_label(row),
                    _export_taken(row),
                    _export_remaining(row),
                    _export_suggested(row),
                ]
                # `columns` may be a PREFIX of `EXPORT_HEADINGS` (Lane C's worksheet, no
                # ACKNOWLEDGED / TAKEN / REMAINING) - cut the row to match so the sheet
                # never carries more cells than it has headings for.
                sheet.append(values[: len(columns)])
