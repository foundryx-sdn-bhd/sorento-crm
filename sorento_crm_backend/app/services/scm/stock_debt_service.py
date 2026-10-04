"""Stock Debt: every outstanding sales order without supply, as a month x product balance.

S2 of `PLAN-scm-borrow-ladder-v7-stock-debt.md` (section 3.4), rulings R6/R7, R15, R21, R23,
and R42 (section 3.4b, 28 Sep 2026): purchase orders are supply here again, parked on the PO
line's Delivery date, and a PO line naming a sales order covers that order first.

**The view SHOWS; the board DECIDES** (R23). Nothing here writes, proposes or reserves. It
reads the same book the ladder reads, hands it to `supply_assignment.assign()` - the one
piece of arithmetic both surfaces share - and prints the answer as a balance PER MONTH
(R37: what is debted in August stays in August) with the lines and documents behind each
cell. A cell and its drill are two readings of the same walk: free supply dated in the
month, less what the lines due in it went short of on their own dates.

**One read per input, never one per product.** The page is the whole flagged catalogue
(1,000-2,000 products on the live book), so every fact is fetched for the WHOLE set in one
query and then split by product in Python: on hand, open demand, SPO, PO, the decisions that
pin stock and the links that pin a document. A per-product query here is what would turn a
screen into a minute. The trigger for a persisted debt table is stated in plan 3.6 and it is
this cost - measured, not guessed.

**No debt table** (plan 3.6). Debt is computed from the book, and the parts of it that are
decisions (ORDER_BACK rows, placement links, confirmed allocations) are already persisted by
the board. A table would be a second copy of a derived figure, stale the moment anybody
confirms anything.
"""
from __future__ import annotations

from dataclasses import replace as dataclass_replace
from datetime import date
from decimal import Decimal
from io import BytesIO
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from app.models.base import get_company_scope
from app.models.inventory import Stock, Warehouse
from app.models.order import SalesOrder, SalesOrderLine
from app.models.procurement import (
    InboundShipment,
    ProductSupplier,
    PurchaseOrder,
    PurchaseOrderLine,
    SPOAllocation,
    Supplier,
)
from app.models.product import Product, ProductCategory
from app.models.project_so import (
    INQUIRY_CANCELLED,
    OrderInquiry,
    OrderInquiryLink,
    OrderInquiryRow,
    ProjectSalesOrderLine,
    SOLineAllocation,
)
from app.models.sales_agent import SalesAgent
from app.services.company_scope import build_company_predicate
from app.services.error_handler import AppException
from app.services.project_supply_service import ProjectSupplyService, held_qty_expr
from app.services.scm import order_link_service, sales_agent_service, spo_supply
from app.services.scm.demand import (
    demand_qty,
    is_open_demand,
    is_transferable_order,
    plan_qty,
)
from app.services.scm.front_planning_engine import (
    DEFAULT_LEAD_TIME_DAYS,
    later_order_can_wait,
    qty_text,
    reserve_window_end,
)
# Reused, not reinvented (AC-18): the low stock report's own cap. A read-only export off a
# bounded catalogue does not need a cap of its own; it needs the SAME reason that one has -
# "narrow it first" past a size nobody opens a workbook to page through.
from app.services.scm.low_stock_report_service import MAX_LOW_STOCK_ROWS
from app.services.scm.planning_predicate import fulfilment_planning_predicate
from app.services.scm.workbook_split import split_rows, unique_sheet_title
from app.services.scm.supply_assignment import (
    BUCKET_TBA,
    BUCKET_UNDATED,
    BUCKET_UNLOCATED,
    EPSILON,
    KIND_ON_HAND,
    KIND_PO,
    KIND_SPO,
    Assignment,
    DemandLine,
    Hold,
    SupplyEvent,
    assign,
    effective_date,
    month_axis,
    month_key,
    ownership_group,
    parse_supply_key,
    tone_for,
)

_ZERO = Decimal("0")

#: The month keys that are not months. Addressable exactly like a `YYYY-MM` cell, because
#: the screen's TBA, No date and No location columns are cells a reader clicks like any
#: other (R28).
BUCKET_KEYS = (BUCKET_TBA, BUCKET_UNDATED, BUCKET_UNLOCATED)

#: R44 (owner, 29 Sep 2026, #1359): the overdue numbers the stock debt VIEW walks. "I just
#: need to know what's my sold quantity (demand) and purchased quantity (supply), so I don't
#: really care about the fulfilment": a document past its date is still supply at its
#: outstanding quantity, landing today (grace 0, so in the month it arrives in, the axis
#: starting today) and never dead. The policy's own grace/dead stay the board's, the
#: ladder's, coverage's and front planning's (`assignments_for` never passes `view`).
VIEW_OVERDUE_GRACE_DAYS = 0
VIEW_OVERDUE_DEAD_DAYS = 10**9

EXPORT_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)

_MONTH_NAMES = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)

def _export_month_label(key: str) -> str:
    """`2026-09` -> `Sep 26` (AC-13). The export's own copy of the FE's `monthLabel` -
    the two are the same three lines twice, not a shared import, because one lives in
    Python and the other in TypeScript."""
    year, month = key.split("-")
    return f"{_MONTH_NAMES[int(month) - 1]} {year[2:]}"


def _float(value: Any) -> float:
    if value is None:
        return 0.0
    return float(value)


def _spo_ref(spo_number: Optional[str]) -> str:
    """`SPO-2026/08-0085` -> itself; `202607-S0105` -> `SPO 202607-S0105`.

    Every shipping order in the live book is already written `SPO-...`, and prefixing the
    word onto one of those reads "SPO SPO-2026/08-0085" - the exact sentence the captain
    flagged on SO418869 SRTWCX7405-RL-S-PJ (3 Sep 2026). `front_planning_engine.spo_reason`
    carries the same guard, for a document named in a WATER sentence rather than a hold.
    """
    if not spo_number:
        return "SPO"
    return spo_number if spo_number.upper().startswith("SPO") else f"SPO {spo_number}"


def _landed_qty(
    allocated: float, received: float, *, arrived: bool, receipt_status: Optional[str]
) -> float:
    """What an SPO line that is NO LONGER incoming brought into its bin (SPO-RECEIVED-PIN).

    The receipt when the book has written one; the whole allocation when only the
    shipment's arrival, or the line's own receipt status, says the goods are in; nothing for
    a line closed with nothing received and nothing landed - that document brought no stock
    anywhere, so a placement on it holds no stock either.
    """
    if received > EPSILON:
        return received
    if arrived or receipt_status in spo_supply.RECEIVED_RECEIPT_STATUSES:
        return allocated
    return 0.0


def book_so_pins(
    po_lines: Sequence[Tuple[str, SupplyEvent, str, float]],
    demand: Dict[str, Sequence[DemandLine]],
    holds: Sequence[Hold],
    *,
    tba_from: date,
    line_refs: Optional[Dict[str, str]] = None,
) -> List[Hold]:
    """R42/R43, pure: the holds a PO line's S/O makes on its sales order's lines.

    `po_lines` is `(product_id, PO event, sales_order_id the S/O names, qty placement links
    already hold on the PO line)`. Each PO line may pin its outstanding (`event.qty`) less
    that placed quantity, over the named order's lines for the SAME product, each up to what
    the line still needs once the confirmed holds (`holds`: allocations and placements) and
    earlier S/O pins have spoken. What is left over is not pinned, so it stays free supply
    exactly as an unpinned PO line does (R44: the view walks no overdue rule).

    WHICH line of the order, in three passes over every PO line (R43, owner, 28 Sep 2026:
    "the 1305 supposed to be for the 2nd line, 4 supposed to be for 1st line"):

    1. the line the S/O itself names - `line_refs[event.key]` (the PO line's
       `from_so_line_ref`) equal to the line's own `source_ref`, both trimmed;
    2. a line that needs exactly what the PO line has left - only for a PO line whose ref
       names no line held here, and only when ANOTHER such PO line names the same order
       for the product: quantity is what tells two PO lines of one order apart, and a
       lone PO line has nothing to be told apart from, so it keeps date order;
    3. the order's other lines, earliest required date first.

    Before R43 every PO line filled the order earliest-first on its own, so two PO lines
    naming one order landed by sort order: the 1,305 spilt over the 4-unit line and the 4
    was pinned to the 1,305 line.

    A line the walk will not draw for (TBA, undated, unlocated - R14) is never given one:
    `assign()` would drop the pin and the quantity would be lost for nothing. Nor is a line
    in ANOTHER ownership group (or a site pool against a project group): only a Confirm
    moves supply across a group (R40).

    R43 withdrew R42's "only a PO line the overdue rule counts": `po_lines` carries every PO
    line naming an order, dead, late or undated, and every pin `fulfils` (see `Hold`).
    """
    refs = {key: (ref or "").strip() for key, ref in (line_refs or {}).items()}
    already: Dict[str, float] = {}
    for hold in holds:
        already[hold.line_key] = already.get(hold.line_key, 0.0) + float(hold.qty)
    ordered = sorted(po_lines, key=lambda item: (item[1].at or date.max, item[1].key))
    budgets: Dict[str, float] = {}
    candidates: Dict[str, List[DemandLine]] = {}
    for product_id, event, sales_order_id, placed in ordered:
        budget = float(event.qty) - float(placed or 0.0)
        if budget <= 0:
            continue
        group = ownership_group(event.warehouse, event.is_pool)
        budgets[event.key] = budget
        candidates[event.key] = sorted(
            (
                line
                for line in demand.get(product_id, ())
                if line.sales_order_id == sales_order_id
                and line.required_date is not None
                and line.required_date < tba_from
                and (line.warehouse or line.is_pool)
                and ownership_group(line.warehouse, line.is_pool) == group
            ),
            key=lambda line: (
                line.required_date, line.core_line_no or 0, line.key,
            ),
        )

    #: PO lines (with quantity to pin) whose ref names none of their order's lines here,
    #: counted per (product, order) - the exact pass's own gate, see the docstring.
    unnamed: Dict[str, bool] = {}
    siblings: Dict[Tuple[str, str], int] = {}
    for product_id, event, sales_order_id, _placed in ordered:
        if event.key not in budgets:
            continue
        ref = refs.get(event.key)
        unnamed[event.key] = not any(
            ref and (line.source_ref or "").strip() == ref
            for line in candidates[event.key]
        )
        if unnamed[event.key]:
            siblings[(product_id, sales_order_id)] = (
                siblings.get((product_id, sales_order_id), 0) + 1
            )

    out: List[Hold] = []

    def need_of(line: DemandLine) -> float:
        return float(line.open_qty) - already.get(line.key, 0.0)

    def pin(event: SupplyEvent, line: DemandLine, take: float) -> None:
        already[line.key] = already.get(line.key, 0.0) + take
        budgets[event.key] -= take
        out.append(
            Hold(
                line_key=line.key,
                supply_key=event.key,
                qty=take,
                # R45: the event's own kind - an SPO line's S/O pins exactly as a PO line's.
                kind=event.kind,
                warehouse=event.warehouse,
                ref=event.ref,
                spo_number=event.spo_number,
                spo_line_number=event.spo_line_number,
                po_number=event.po_number,
                purchase_order_id=event.purchase_order_id,
                fulfils=True,
            )
        )

    def named(_key: Tuple[str, str], event: SupplyEvent, line: DemandLine) -> bool:
        ref = refs.get(event.key)
        return bool(ref) and (line.source_ref or "").strip() == ref

    def exact(key: Tuple[str, str], event: SupplyEvent, line: DemandLine) -> bool:
        return (
            unnamed.get(event.key, False)
            and siblings.get(key, 0) > 1
            and abs(need_of(line) - budgets[event.key]) <= EPSILON
        )

    def anywhere(_key: Tuple[str, str], _event: SupplyEvent, _line: DemandLine) -> bool:
        return True

    for matches in (named, exact, anywhere):
        for product_id, event, sales_order_id, _placed in ordered:
            for line in candidates.get(event.key, ()):
                if budgets[event.key] <= EPSILON:
                    break
                if not matches((product_id, sales_order_id), event, line):
                    continue
                take = min(budgets[event.key], need_of(line))
                if take <= EPSILON:
                    continue
                pin(event, line, take)
    return out


AUX_KEYS = ("pool_on_hand", "pool_po_qty", "pool_incoming_qty", "dealer_outstanding")
AUX_EXPORT_HEADERS = [
    "BRW On Hand Qty", "BRW PO Qty", "BRW Incoming Qty", "Dealer O/standing Qty",
]


class StockDebtService:
    def __init__(self, db: Session):
        self.db = db
        #: The reader that already knows how to read SPO, PO and lead times, and the one the
        #: board uses. Sharing it is what stops the view and the board disagreeing about
        #: what is on the water.
        self.supply = ProjectSupplyService(db)

    # ------------------------------------------------------------------ the two answers

    def list(
        self,
        *,
        query: Optional[str] = None,
        group: Optional[str] = None,
        only_debt: bool = True,
        page: int = 1,
        limit: int = 50,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
        supplier_ids: Optional[Sequence[str]] = None,
        book: str = "all",
    ) -> Dict[str, Any]:
        """The month x product board (AC-S2-6, extended AC-1 to AC-9; R14/R15 owner round).

        `date_from`/`date_to` (R14, replacing `cutoff` - REMOVED, not aliased) drop demand
        due before/after them, in `_demand()`; TBA reads 0 once the policy's
        `tba_date_from` sits after `date_to`, for free - every TBA line is dated on or
        after `tba_date_from`, so the same date filter drops the whole bucket without a
        second rule. `supplier_ids` (R15, replacing `supplier_id`) narrows to products
        whose LAST supplier (newest PO line, else the primary flag) is ANY of the values
        passed; `'none'` is one more value among the others, not a sentinel that excludes
        them. `book` (R1/A4) chooses the span `_warehouses` reads.

        `totals`, `suppliers` and `sheet_counts` travel on the ENVELOPE, over the WHOLE
        filtered set rather than the page, for the same reason the axis already does:
        derived per page, they would change under the reader as they page (AC-6/AC-7/AC-7b).
        """
        warehouses = self._warehouses(group, book)
        products = self._products(warehouses, query)
        product_ids = [product_id for product_id, _code, _name, _cat in products]
        assignments = self._assignments(
            [(pid, code, name) for pid, code, name, _cat in products],
            warehouses,
            date_from=date_from,
            date_to=date_to,
            view=True,
        )
        supplier_map = self._last_supplier_map(product_ids)

        pre_supplier = []
        for product_id, code, name, category_code in products:
            result = assignments[product_id]
            if only_debt and not self._in_debt(result):
                continue
            supplier = supplier_map.get(product_id) or {"id": None, "name": None}
            pre_supplier.append((product_id, code, name, category_code, supplier, result))

        # AC-7c: the supplier FACET is built from the set BEFORE the `supplier_ids` filter
        # narrows it - the select can then switch supplier without first clearing itself,
        # rather than a narrowed board silently dropping every option but the one chosen.
        suppliers = self._suppliers_list(row[4] for row in pre_supplier)

        wanted_suppliers = set(supplier_ids or [])
        filtered = []
        for entry in pre_supplier:
            supplier = entry[4]
            if wanted_suppliers:
                # R15: a product matches when its last supplier is ANY of the values
                # passed - `none` is one more value alongside real ids, never a sentinel
                # that excludes them.
                matches = ("none" in wanted_suppliers and supplier["id"] is None) or (
                    supplier["id"] is not None and supplier["id"] in wanted_suppliers
                )
                if not matches:
                    continue
            filtered.append(entry)

        axis = self._axis(
            (row[5] for row in filtered), date_from=date_from, date_to=date_to
        )
        filtered.sort(key=lambda row: self._sort_key(row[5], row[1]))

        # The four auxiliary columns reuse the board's own readers (no new arithmetic), once
        # over every filtered product; they read the pool and the open book, so `book` and
        # the supplier filter do not move them.
        aux = self._aux_maps([row[0] for row in filtered], date_from, date_to)

        data_rows = []
        for product_id, code, name, category_code, supplier, result in filtered:
            months = self._months_on_axis(result, axis, product_id)
            # R17: the row's `total` sums months + TBA ONLY - `undated`/`unlocated` are no
            # longer folded in (the "No date"/"No location" columns leave the screen and
            # the workbook both); the two still ride the row unchanged, just not here.
            total = sum(month["balance"] for month in months) + result.tba
            data_rows.append(
                {
                    "product_id": product_id,
                    "product_code": code,
                    # AC-9/R8: null when it equals the code, trimmed of surrounding
                    # whitespace, case-sensitively - done once here so the board and the
                    # export agree without each re-deriving it.
                    "product_name": (
                        None if name is not None and name.strip() == code else name
                    ),
                    "months": months,
                    "tba": result.tba,
                    "undated": result.undated,
                    "unlocated": result.unlocated,
                    "supplier_id": supplier["id"],
                    "supplier_name": supplier["name"],
                    "category_code": category_code,
                    "total": total,
                    "pool_on_hand": aux["on_hand"].get(product_id, 0.0),
                    "pool_po_qty": aux["po"].get(product_id, 0.0),
                    "pool_incoming_qty": aux["spo"].get(product_id, 0.0),
                    "dealer_outstanding": aux["dealer"].get(product_id, 0.0),
                }
            )

        totals = self._totals(data_rows, axis)
        sheet_counts = self._sheet_counts(data_rows)

        start = max(page - 1, 0) * limit
        page_rows = data_rows[start : start + limit]
        return {
            "data": page_rows,
            "pagination": {"total": len(data_rows), "page": page, "limit": limit},
            "months": axis,
            "tba_month": month_key(self._tba_from()),
            "groups": self._groups(),
            "totals": totals,
            "suppliers": suppliers,
            "sheet_counts": sheet_counts,
        }

    def _aux_maps(
        self,
        product_ids: List[str],
        date_from: Optional[date],
        date_to: Optional[date],
    ) -> Dict[str, Dict[str, float]]:
        """`{on_hand, po, spo, dealer}` -> `{product_id: qty}` for the four auxiliary
        columns, read by the same functions the summary order sheet uses."""
        from app.services.scm import site_pool_supply, summary_order_service

        if not product_ids:
            return {"on_hand": {}, "po": {}, "spo": {}, "dealer": {}}
        db = self.db
        demand = summary_order_service._demand_aggregates(
            db, product_ids, horizon_start=date_from, horizon=date_to
        )
        return {
            "on_hand": {
                k: float(v)
                for k, v in summary_order_service._pool_on_hand_map(db, product_ids).items()
            },
            "po": {
                k: float(v["qty"])
                for k, v in site_pool_supply.open_po_by_product(db, product_ids).items()
            },
            "spo": {
                k: float(v["qty"])
                for k, v in site_pool_supply.open_spo_by_product(db, product_ids).items()
            },
            # `dealer_qty` is the positive outstanding; shown NEGATIVE here, and 0 stays a
            # plain 0 (never -0).
            "dealer": {
                k: (-float(v["dealer_qty"]) if v["dealer_qty"] else 0.0)
                for k, v in demand.items()
            },
        }

    def cell(
        self,
        product_id: str,
        month: str,
        group: Optional[str] = None,
        *,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
        book: str = "all",
    ) -> Dict[str, Any]:
        """The demand and the supply behind one cell (AC-S2-7, R28; extended AC-11).

        The same reads as the board, narrowed to one product, so the two tables foot with the
        cell that opened them by construction rather than by agreement: the drill's
        `free_qty` less its `short_qty`, over the rows of one month, IS that month's balance
        (R37). `group` is the
        narrowing the BOARD was showing when the cell was pressed, and it is not optional
        detail: `group=BB` recomputes the balance from the BB span only, so a drill that read
        the whole book would answer a different question from the cell that opened it.
        `date_from`/`date_to` (R14, replacing `cutoff`) and `book` are the same narrowings
        the list route takes, threaded through for the same reason `group` already is
        (AC-11).
        """
        if month not in BUCKET_KEYS and not self._is_month_key(month):
            raise AppException(
                status_code=422,
                message="month must be YYYY-MM, 'tba', 'undated' or 'unlocated'.",
                code="stock_debt_bad_month",
            )
        warehouses = self._warehouses(group, book)
        product = (
            self.db.query(Product.id, Product.product_code, Product.product_name)
            .filter(Product.id == product_id)
            .first()
        )
        if product is None:
            raise AppException(
                status_code=404, message="Product not found.", code="NOT_FOUND"
            )
        products = [(str(product.id), product.product_code, product.product_name)]
        assignments = self._assignments(
            products, warehouses, keep_events=True, date_from=date_from, date_to=date_to,
            view=True,
        )
        result = assignments[str(product.id)]
        events = self._event_cache[str(product.id)]

        # R29: Supply's own "Assigned to" entries carry `line_no` (the CORE line's own
        # AutoCount `Seq`, `DemandLine.core_line_no`) beside `so_number`, so keyed on the
        # PAIR rather than `so_number` alone - two lines of the same SO could otherwise
        # merge into one entry.
        assigned_to: Dict[str, Dict[Tuple[str, Optional[int]], float]] = {}
        for line in result.lines:
            for item in line.assigned:
                key = (line.line.so_number, line.line.core_line_no)
                assigned_to.setdefault(item.event.key, {}).setdefault(key, 0.0)
                assigned_to[item.event.key][key] += item.qty
        # STOCK-DEBT-LENDABLE: a "Lent to" entry links the receiving order, off the same
        # read (the receiver is a line of this very assignment).
        order_ids = {
            line.line.key: line.line.sales_order_id for line in result.lines
        }

        demand = [
            {
                "so_number": line.line.so_number,
                "agent_code": line.line.agent_code,
                "warehouse_code": line.line.warehouse,
                "required_date": line.line.required_date,
                "open_qty": line.line.open_qty,
                # R22: Ordered/Delivered beside the existing Outstanding (`open_qty`,
                # unchanged) - `qty_ordered` is `plan_qty()` (CS's own `qty_required`
                # when stated, else the book's `qty_ordered`).
                "qty_ordered": line.line.qty_ordered,
                "qty_delivered": line.line.qty_delivered,
                "assigned_qty": round(sum(item.qty for item in line.assigned), 4),
                "assigned_source": self._source_text(line),
                "status": line.status,
                # What this line booked into the month it sits in (R37): what it was short
                # of ON ITS OWN DATE. A `late` line ends covered and still carries one,
                # which is why the drill states it rather than leaving the reader to
                # subtract Assigned from Open and get a different number from the cell.
                "short_qty": line.short_at_date,
                # R29: the Sales order cell's own link target.
                "sales_order_id": line.line.sales_order_id,
                # R29 + addendum: one LINKED entry per source, replacing `assigned_source`.
                # STOCK-DEBT-LENDABLE: plus one "Lent to" entry per receiver on a line that
                # lent its landed goods, and the receiver's on-hand entry names the lender.
                "assigned_from": self._assigned_from(line, order_ids=order_ids),
                # STOCK-DEBT-LENDABLE: what nearer lines took of this line's landed goods.
                "lent_qty": line.lent_qty,
            }
            for line in result.lines
            if line.bucket == month
        ]
        demand.sort(key=lambda row: (row["required_date"] or date.max, row["so_number"]))

        supply: List[Dict[str, Any]] = []
        if month not in BUCKET_KEYS:
            as_of = date.today()
            current = month_key(as_of)
            # The events AS THE WALK COUNTED THEM (R-O): a late-but-alive document was
            # admitted at an ASSUMED date, and the cell it is filed under has to be the one
            # its free quantity was credited to, or the drill and the cell that opened it
            # disagree about which month the goods are in.
            admitted = {event.key: event for event in result.supply}
            # R43 (#1346): a PO line pinned to a line of THIS bucket is listed here too,
            # whatever month its own date files it in, so "Supply (0)" never sits beside
            # "Assigned 1,309". Listed with no Free: its spare quantity (if any) is credited
            # to its own month, and counting it here as well would not foot with the cell.
            pinned_here = {
                item.event.key
                for line in result.lines
                if line.bucket == month
                for item in line.assigned
                if item.pinned and item.event.kind == KIND_PO
            }
            for event in events:
                # An uncounted document is listed in the CURRENT month: the axis starts
                # today. R44 (#1359): the view walks no overdue rule, so the only one left
                # is a document with no date at all.
                walked = admitted.get(event.key)
                counted = walked is not None
                arrival = walked.at if counted else event.at
                key = month_key(effective_date(arrival, as_of)) if counted else current
                home = key == month
                if not home and event.key not in pinned_here:
                    continue
                stated = walked.stated_at if counted else None
                is_document = event.kind in (KIND_SPO, KIND_PO)
                supply.append(
                    {
                        "kind": event.kind,
                        "ref": event.ref,
                        # R29: the Document cell's own link target.
                        "spo_number": event.spo_number,
                        "spo_line_number": event.spo_line_number,
                        # R42: the PO's own link target - the document, its line, and the
                        # two ids `OrderInquiryDocumentLink` opens a PO on.
                        **self._po_fields(event),
                        "warehouse_code": event.warehouse,
                        # THE ASSUMED date where there is one (R-O), because that is what
                        # the walk planned against; the paperwork's own date travels beside
                        # it rather than instead of it.
                        "date": arrival,
                        "stated_date": stated,
                        "days_late": int(getattr(walked, "days_late", 0) or 0)
                        if counted
                        else 0,
                        "bought_for": event.bought_for,
                        # R26: an SPO's own Qty is the RAW ordered quantity - `event.qty`
                        # stays the netted OUTSTANDING balance the walk itself assigns
                        # against, stated here as its own column instead. On hand has no
                        # such split (it is not a document with a received/outstanding
                        # history), so both are `None` - blank, never a fabricated 0.
                        #
                        # R42: a PO line states the same three, off its own
                        # `qty_ordered`/`qty_received`, Outstanding being what the walk counts.
                        "qty": event.ordered_qty
                        if is_document and event.ordered_qty is not None
                        else event.qty,
                        "received_qty": event.received_qty if is_document else None,
                        "outstanding_qty": event.qty if is_document else None,
                        # What nobody took, once the whole walk was over - the other half of
                        # the cell (R37). A DEAD document is free of nothing: it is not
                        # supply until somebody re-dates it (R31).
                        "free_qty": result.free.get(event.key, 0.0)
                        if counted and home
                        else 0.0,
                        "overdue": not counted and event.at is not None,
                        # R29 (owner ruling, 25 Sep): `line_no` is the CORE line's own
                        # AutoCount `Seq`, beside `so_number` - ALWAYS present, `None` when
                        # AutoCount has never numbered the line, the same "always present,
                        # null when unknown" discipline every other field on this contract
                        # already follows. Sorted on the pair, `line_no or 0` so a line
                        # with none sorts before a numbered one rather than raising on
                        # `None < int`.
                        "assigned_to": [
                            {
                                "so_number": so_number,
                                "qty": round(qty, 4),
                                "line_no": line_no,
                            }
                            for (so_number, line_no), qty in sorted(
                                assigned_to.get(event.key, {}).items(),
                                key=lambda item: (item[0][0], item[0][1] or 0),
                            )
                        ],
                    }
                )
            supply.sort(key=lambda row: (row["date"] or date.max, row["ref"] or ""))

        # R25: the envelope's own quantity totals, over the WHOLE tab (never the page - a
        # drill has no paging, but the same "sum here, not on the FE" reasoning the board's
        # own `totals` already applies). Demand sums Outstanding as it stood BEFORE this
        # walk's assignment (`open_qty`, the line's own full ask); Supply sums whatever
        # each row's own Qty column actually offers - Outstanding for an SPO, the on-hand
        # figure otherwise - so the two tab labels state what a reader would get by adding
        # the column up themselves.
        demand_total_qty = sum(row["open_qty"] for row in demand)
        supply_total_qty = sum(
            row["outstanding_qty"] if row["outstanding_qty"] is not None else row["qty"]
            for row in supply
        )
        return {
            "demand": demand,
            "supply": supply,
            "demand_total_qty": demand_total_qty,
            "supply_total_qty": supply_total_qty,
        }

    # ------------------------------------------------------------------ the reads

    def _warehouses(
        self, group: Optional[str], book: str = "all"
    ) -> Dict[str, Warehouse]:
        """The bins this read spans, narrowed to one ownership group and/or one `book`
        (R1/A4, AC-8).

        `group=BB` narrows the SPAN of every read below it rather than filtering finished
        rows (AC-S2-6): the balance asked for is the BB group's own, and a row filtered after
        the fact would still have let another group's stock cover a BB order. `group` only
        ever narrows the PROJECT half - a site pool is nobody's ownership group.

        `book`:
        * `project` - flagged bins only (the pre-24-Sep span), narrowed by `group`.
        * `retail` - site pools only; `group` is meaningless here and is ignored.
        * `all` (default) - both, in ONE span. `assign()` already seals a pool's own
          group (`POOL_GROUP`) off from every project group in both directions (A4), so
          adding the pools to this dict is the whole change - the ladder's
          `assignments_for` already relies on the same fact for its own pool step.
        """
        project_bins = self._project_bins(group)
        if book == "project":
            return project_bins
        pool_bins = dict(self.supply.site_pool_warehouses())
        if book == "retail":
            return pool_bins
        return {**project_bins, **pool_bins}

    def _project_bins(self, group: Optional[str]) -> Dict[str, Warehouse]:
        """The flagged bins alone, narrowed by `group` - `_warehouses`'s own project half,
        and the whole of `_groups`'s span (a site pool admits no ownership group)."""
        rows = self.db.query(Warehouse).filter(fulfilment_planning_predicate()).all()
        if group:
            wanted = group.strip().upper()
            rows = [
                row
                for row in rows
                if sales_agent_service.group_of_warehouse_code(row.warehouse_code)
                == wanted
            ]
        return {str(row.id): row for row in rows}

    def _groups(self) -> List[str]:
        """The ownership groups the flag currently admits, for the toolbar's select."""
        rows = (
            self.db.query(Warehouse.warehouse_code)
            .filter(fulfilment_planning_predicate())
            .all()
        )
        return sorted(
            {
                group
                for group in (
                    sales_agent_service.group_of_warehouse_code(row[0]) for row in rows
                )
                if group
            }
        )

    def _products(
        self, warehouses: Dict[str, Warehouse], query: Optional[str]
    ) -> List[Tuple[str, str, Optional[str], Optional[str]]]:
        """Every product with stock, demand or incoming at those bins: id, code, name and
        `category_code` (R4/AC-5) - one JOIN, not a second query, for the same reason the
        rest of this file reads once for the whole set.

        Four id reads and one product read, because a product with nothing at a flagged bin
        has no debt to state and no row to render. The demand read reaches one step further
        than the other three: an UNLOCATED sales-order line names no bin at all, and a screen
        that lists what is owed while silently dropping 2,312 open lines (30 Aug dev copy)
        is answering a narrower question than the one it is asked.
        """
        ids = set(warehouses)
        if not ids:
            return []
        candidates: set = set()
        candidates |= {
            str(row[0])
            for row in self.db.query(Stock.product_id)
            .filter(Stock.warehouse_id.in_(ids), Stock.quantity_on_hand > 0)
            .distinct()
            .all()
        }
        candidates |= {
            str(row[0])
            for row in self.db.query(SalesOrderLine.product_id)
            .join(SalesOrder, SalesOrder.id == SalesOrderLine.sales_order_id)
            .filter(
                self._demand_span(ids),
                SalesOrder.status == "open",
                is_open_demand(),
                is_transferable_order(),
            )
            .distinct()
            .all()
        }
        candidates |= {
            str(row[0])
            for row in self.db.query(SPOAllocation.product_id)
            .filter(
                SPOAllocation.warehouse_id.in_(ids),
                SPOAllocation.line_status == "open",
                SPOAllocation.allocated_quantity > SPOAllocation.quantity_received,
            )
            .distinct()
            .all()
        }
        candidates |= {
            str(row[0])
            for row in self.db.query(PurchaseOrderLine.product_id)
            .filter(
                PurchaseOrderLine.warehouse_id.in_(ids),
                PurchaseOrderLine.line_status == "open",
                PurchaseOrderLine.qty_ordered > PurchaseOrderLine.qty_received,
            )
            .distinct()
            .all()
        }
        if not candidates:
            return []

        rows = (
            self.db.query(
                Product.id,
                Product.product_code,
                Product.product_name,
                ProductCategory.category_code,
            )
            .outerjoin(ProductCategory, ProductCategory.id == Product.category_id)
            .filter(Product.id.in_(candidates))
        )
        if query:
            needle = f"%{query.strip()}%"
            rows = rows.filter(
                or_(
                    Product.product_code.ilike(needle),
                    Product.product_name.ilike(needle),
                )
            )
        return [
            (str(row.id), row.product_code or "", row.product_name, row.category_code)
            for row in rows.all()
        ]

    @staticmethod
    def _demand_span(warehouse_ids):
        """"A line this view is answerable for": at one of these bins, or at NO bin.

        One expression, used by the candidate read and by `_demand`, so the products that
        get a row and the lines that fill it can never come from two different rules.
        `assign()` gives an unlocated line its own bucket - it is in no group's pile, so it
        draws nothing (AC-S2-1b's sibling case) - but it is COUNTED and it is listed.
        """
        return or_(
            SalesOrderLine.warehouse_id.in_(list(warehouse_ids)),
            SalesOrderLine.warehouse_id.is_(None),
        )

    def assignments_for(
        self,
        product_ids: Sequence[str],
        warehouses: Dict[str, Warehouse],
        *,
        as_of: Optional[date] = None,
        include_po: bool = True,
    ) -> Dict[str, Assignment]:
        """The same assignment, for a caller that holds product ids and a span of its own.

        PUBLIC for the LADDER (S3, R21): the board reads the assignment this view reads, or
        the two surfaces disagree about what is free. The ladder's span is this one plus the
        site pools, because it has a pool step and the view has not (see
        `ProjectSupplyService.planning_assignments`).

        `include_po` defaults `True` here: the board and the ladder net a PO as supply at
        `issue + lead` (plan v7 R29). R42 (28 Sep 2026) is the VIEW's own reading - a PO
        parked on its Delivery date and pinned by its S/O - and never reaches this path:
        `view` stays `False`, so the board's answer is what it was before R42
        (AC-PO-8 pins it).
        """
        return self._assignments(
            [(str(pid), "", None) for pid in product_ids],
            warehouses,
            as_of=as_of,
            include_po=include_po,
        )

    def _assignments(
        self,
        products: Sequence[Tuple[str, str, Optional[str]]],
        warehouses: Dict[str, Warehouse],
        *,
        keep_events: bool = False,
        as_of: Optional[date] = None,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
        include_po: bool = True,
        view: bool = False,
    ) -> Dict[str, Assignment]:
        """One `assign()` per product, off ONE read per input for the whole set.

        `date_from`/`date_to` (R14, replacing `cutoff`; AC-1/AC-1b/AC-3) drop demand due
        before/after them in `_demand()` below - they touch DEMAND only, never supply
        (AC-3: supply landing after a line's own due date, but on or before `date_to`,
        still covers it - the walk itself is unchanged).

        `include_po` reads PO lines as supply on both paths (R42 retired the view's
        `False`, 28 Sep 2026). `view` is the Stock Debt VIEW's own reading of them (R42): a
        PO line is dated on its Delivery date (`expected_date`, else `issue + lead`), and a
        PO line whose S/O names a sales order is pinned to that order's lines before the
        walk (`_book_so_holds`). `list()`/`cell()` pass it; `assignments_for` (the board
        and the ladder) never does.
        """
        self._event_cache: Dict[str, List[SupplyEvent]] = {}
        self._lead_cache: Dict[str, int] = {}
        product_ids = [product_id for product_id, _code, _name in products]
        # The CALLER's `as_of` when it pins one (the board's simulation does), else today.
        as_of = as_of or date.today()
        tba_from = self._tba_from()
        if not product_ids:
            return {}

        warehouse_ids = list(warehouses)
        codes = {
            warehouse_id: warehouse.warehouse_code or warehouse_id
            for warehouse_id, warehouse in warehouses.items()
        }
        pools = set(self.supply.site_pool_warehouses())

        # FIRST, and deliberately: `lead_times` fills the memo the per-line fallback in
        # `_po_rows` reads. Called after `_supply`, every PO line whose supplier agreement
        # states no lead paid its own round trip - ~1,900 extra queries per list request on
        # the dev copy.
        leads = self.supply.lead_times(product_ids)
        supply_rows = self._supply(
            product_ids, warehouse_ids, codes, pools, as_of=as_of, include_po=include_po,
            delivery_dated=view,
            # R45: on the page a document is spent by its links and nothing else.
            documents_pin_only=view,
        )
        demand_rows = self._demand(
            product_ids, warehouse_ids, codes, pools, date_from=date_from, date_to=date_to,
        )
        # STOCK-DEBT-LENDABLE (owner, 30 Sep 2026, option B): in the VIEW alone, the lines
        # that CAN WAIT - due on or after `as_of + lead + 14`, the board's own borrow-donor
        # window, off the SAME batched lead read as the red horizon, and before the TBA
        # line (`later_order_can_wait`, shared with `_eligible_donor`). Their landed goods,
        # however they reach the assignment (a placement on the received SPO in `_holds`,
        # or R7's own-purchase read in `_landed_holds`), are `lendable`: nearer lines draw
        # them first and the far line reads `order_back`. The board and the ladder
        # (`assignments_for`) never lend; their Borrow step is where the same window turns
        # into a decision. This page stays read-only (owner, 30 Sep 2026: "this is a
        # dashboard view only") and points the planner at that board.
        lendable_lines: Optional[Set[str]] = None
        if view:
            lendable_lines = set()
            for product_id, lines in demand_rows.items():
                lead = leads.get(product_id)
                window = reserve_window_end(
                    as_of, DEFAULT_LEAD_TIME_DAYS if lead is None else lead
                )
                lendable_lines.update(
                    line.key
                    for line in lines
                    if later_order_can_wait(
                        line.required_date, window=window, tba_from=tba_from
                    )
                )
        holds = self._holds(
            product_ids,
            {line.key for lines in demand_rows.values() for line in lines},
            include_po=include_po,
            # SPO-RECEIVED-PIN: what a placement on a received SPO may pin on, see `_holds`.
            span=set(warehouse_ids),
            supply_rows=supply_rows,
            lendable_lines=lendable_lines,
        )
        # #1362 round 5 (owner ruling, 29 Sep 2026): goods that LANDED for a line stay with
        # that line, so they bind before anybody queues, exactly as a confirmed decision
        # does. After the decision and placement holds, so nothing is pinned twice.
        holds = holds + self._landed_holds(
            supply_rows, demand_rows, holds, lendable_lines=lendable_lines,
        )

        settings = self.supply._fulfilment_settings()
        grace = settings.get("overdue_grace_days")
        dead = settings.get("overdue_dead_days")
        if view:
            # R44 (#1359): the overdue rule stays out of the view, see the constants.
            grace, dead = VIEW_OVERDUE_GRACE_DAYS, VIEW_OVERDUE_DEAD_DAYS
        if view:
            # R43 (#1346): in the view a hold on a PO line fulfils its line whatever the PO's
            # date - a placement as much as the book's S/O below. The board never gets here.
            holds = [
                dataclass_replace(hold, fulfils=True) if hold.kind == KIND_PO else hold
                for hold in holds
            ]
            # R42: AFTER the confirmed holds, so a placement binds first and the book's S/O
            # takes only what is left of the PO line (and of the sales-order line). R45:
            # an SPO line's S/O is read the same way, and on this page it is the only way
            # an unplaced SPO reaches a line at all.
            holds = holds + self._book_so_holds(supply_rows, demand_rows, holds, tba_from)

        out: Dict[str, Assignment] = {}
        for product_id in product_ids:
            # The product's own lead, or the ladder's default - the SAME source the reserve
            # window uses (plan risk 5), so the red horizon here and the window there agree.
            # `is None`, never `or` - the same test `_po_rows` and `reserve_window_end`
            # make. A supplier who states a 0-day lead is stating something (stock off the
            # shelf), and reading that as "nobody says" turned it into 90 days, which paints
            # three months of a product that can be bought today red.
            lead = leads.get(product_id)
            self._lead_cache[product_id] = (
                DEFAULT_LEAD_TIME_DAYS if lead is None else lead
            )
            events = supply_rows.get(product_id, [])
            lines = demand_rows.get(product_id, [])
            line_keys = {line.key for line in lines}
            out[product_id] = assign(
                product_id,
                as_of=as_of,
                tba_from=tba_from,
                lead_days=self._lead_cache[product_id],
                supply=events,
                demand=lines,
                pinned=[hold for hold in holds if hold.line_key in line_keys],
                # R-O (3 Sep 2026): the grace a late document is counted under, off the
                # SAME active policy row `tba_from` above is read from - the board, this
                # view and the ladder all walk one assignment, so they cannot come to two
                # views of how late is too late.
                overdue_grace_days=grace,
                overdue_dead_days=dead,
            )
            if keep_events:
                self._event_cache[product_id] = events
        return out

    def _supply(
        self,
        product_ids: Sequence[str],
        warehouse_ids: Sequence[str],
        codes: Dict[str, str],
        pools: set,
        *,
        as_of: Optional[date] = None,
        include_po: bool = True,
        delivery_dated: bool = False,
        documents_pin_only: bool = False,
    ) -> Dict[str, List[SupplyEvent]]:
        """On hand and SPO for the whole page - two reads, neither of them per product.
        A THIRD, PO, joins them when `include_po` is set (the board/ladder's own path,
        `assignments_for` - plan v7 R29).

        On hand is `quantity_on_hand - quantity_reserved`, the same arithmetic
        `_free_stock` states: reserved stock is spoken for by a picking or despatch that is
        already under way, so offering it to a sales-order line here would promise the same
        units twice. The confirmed HOLDS are subtracted separately, by pinning them to the
        lines that hold them (`_holds`) - which is the more useful shape, because the drill
        can then say which order has them.

        `as_of` is the CALLER's day, not the clock: an on-hand event is stamped with the day
        the walk starts, and stamping it `date.today()` while the walk ran at a pinned
        earlier date put the stock after every line due between the two, so a board
        simulated at a past date read its own floor as arriving late.

        R23 (owner, 24 Sep, third red batch): "got PO doesn't mean got supply." Stock
        Debt's own reading (`include_po=False`, `list()`/`cell()`'s own default) counts on
        hand and SPO only - a PO is a plan to buy, not stock anybody has or a shipment
        already moving, and reading it as supply here let a line read `covered`/`pinned`
        off a document that could still fall through.

        Fix round (CI, 25 Sep): R23 is the VIEW's own reading, not the shared assignment's
        - the board and the ladder still net a PO as supply (plan v7 R29,
        `test_ladder_v7_po_never_supplies.py`/`test_ladder_v7_incoming_spo_only.py`'s own
        guards), which is `assignments_for`'s `include_po=True` default reaching here.

        R42 (owner, 28 Sep 2026, supersedes R23 for the view): "unless we follwo the PO line
        delivery date and park it as like a supply". The view reads PO again, and
        `delivery_dated` parks each line on its Delivery date (`expected_date`, the column
        the PO lines tab labels so), falling back to R29's `issue + lead` only when the line
        states none. The board keeps `issue + lead` (`delivery_dated=False`).

        R45 (owner, 30 Sep 2026, PO-NO-AUTO-ASSIGN): `documents_pin_only` (the view's own
        reading, `_assignments(view=True)`) stamps every SPO and PO event `pin_only`, so the
        walk never hands a document to a line: "we cannot distribute the PO quantity like
        that, cause the PO quantity is ordered for a reason, and the user is yet to do
        linking in AutoCount" - and, asked, "this applies for SPO also". A document covers
        a line only through a link (a placement, the book's S/O, landed goods); what no
        link took is free in its own month. On hand is walked as before.
        """
        as_of = as_of or date.today()
        out: Dict[str, List[SupplyEvent]] = {}

        rows = (
            self.db.query(
                Stock.product_id,
                Stock.warehouse_id,
                func.sum(
                    func.coalesce(Stock.quantity_on_hand, 0)
                    - func.coalesce(Stock.quantity_reserved, 0)
                ).label("qty"),
            )
            .filter(
                Stock.product_id.in_(product_ids),
                Stock.warehouse_id.in_(warehouse_ids),
            )
            .group_by(Stock.product_id, Stock.warehouse_id)
            .all()
        )
        for row in rows:
            qty = _float(row.qty)
            if qty <= 0:
                continue
            warehouse_id = str(row.warehouse_id)
            out.setdefault(str(row.product_id), []).append(
                SupplyEvent(
                    key=f"on_hand:{warehouse_id}",
                    kind=KIND_ON_HAND,
                    warehouse=codes.get(warehouse_id),
                    at=as_of,
                    qty=qty,
                    is_pool=warehouse_id in pools,
                )
            )

        for (product_id, warehouse_id), refs in self.supply.incoming_by_location(
            product_ids, warehouse_ids
        ).items():
            for ref in refs:
                out.setdefault(product_id, []).append(
                    SupplyEvent(
                        key=f"spo:{ref.allocation_id}",
                        kind=KIND_SPO,
                        warehouse=codes.get(warehouse_id),
                        at=ref.arrival_date,
                        # The WALK's own figure stays the netted outstanding balance
                        # (R26) - `ordered_qty`/`received_qty` below are display-only.
                        qty=_float(ref.qty),
                        ref=_spo_ref(ref.spo_number),
                        is_pool=warehouse_id in pools,
                        ordered_qty=_float(ref.ordered_qty),
                        received_qty=_float(ref.received_qty),
                        # R29: the wire fields the Document cell links off.
                        spo_number=ref.spo_number,
                        spo_line_number=ref.spo_line_no,
                        pin_only=documents_pin_only,
                    )
                )

        # Plan v7 R29 (board and ladder: `issue + lead`) / R42 (the view: the Delivery
        # date): see the docstring above.
        if include_po:
            for (product_id, warehouse_id), lines in self.supply.po_by_location(
                product_ids, warehouse_ids
            ).items():
                for line in lines:
                    parked = delivery_dated and line.bought_for is not None
                    out.setdefault(product_id, []).append(
                        SupplyEvent(
                            key=f"po:{line.line_id}",
                            kind=KIND_PO,
                            warehouse=codes.get(warehouse_id),
                            at=line.bought_for if parked else line.arrival_date,
                            qty=_float(line.qty),
                            ref=f"PO {line.po_number} line {line.po_line_no}",
                            # Parked on it, the delivery date IS the date; stating it again
                            # as "bought for" would print one date twice.
                            bought_for=None if parked else line.bought_for,
                            is_pool=warehouse_id in pools,
                            ordered_qty=_float(line.ordered_qty)
                            if line.ordered_qty is not None
                            else None,
                            received_qty=_float(line.received_qty)
                            if line.received_qty is not None
                            else None,
                            po_number=line.po_number,
                            po_line_number=line.po_line_no,
                            purchase_order_id=line.purchase_order_id,
                            pin_only=documents_pin_only,
                        )
                    )
        return out

    def _demand(
        self,
        product_ids: Sequence[str],
        warehouse_ids: Sequence[str],
        codes: Dict[str, str],
        pools: set,
        *,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
    ) -> Dict[str, List[DemandLine]]:
        """Every open sales-order line at those bins, plus the ones at NO bin - the same
        `is_open_demand()` rule the ladder and `scm.committed_v` share, so the debt and the
        plan count one book. `_demand_span` is why an unlocated line is here.

        `date_from`/`date_to` (R14, replacing `cutoff`; AC-1/AC-1b/AC-2) drop a line due
        before `date_from` or after `date_to`; an undated line has no date to test and
        always survives either bound. Every TBA line is dated on or after the policy's
        `tba_date_from`, so a `date_to` earlier than that date drops the whole TBA bucket
        for free, off this same clause - no second rule needed (AC-2).
        """
        extra_clauses = []
        if date_to is not None:
            extra_clauses.append(
                or_(
                    SalesOrderLine.required_date.is_(None),
                    SalesOrderLine.required_date <= date_to,
                )
            )
        if date_from is not None:
            extra_clauses.append(
                or_(
                    SalesOrderLine.required_date.is_(None),
                    SalesOrderLine.required_date >= date_from,
                )
            )
        rows = (
            self.db.query(
                SalesOrderLine.id,
                SalesOrderLine.product_id,
                SalesOrderLine.warehouse_id,
                SalesOrderLine.required_date,
                SalesOrderLine.sales_order_id,
                # R29: the CORE line's own `line_no` (AutoCount's `Seq`) - Supply's own
                # "Assigned to" entries name this, never the PROJECT mirror's `line_no`
                # selected below (a different number, the ladder's own).
                SalesOrderLine.line_no.label("core_line_no"),
                # R43: what a PO line's S/O quotes, so its pin lands on THIS line.
                SalesOrderLine.source_ref.label("line_source_ref"),
                demand_qty().label("qty"),
                SalesOrder.so_number,
                SalesAgent.sales_agent,
                # The PROJECT mirror's own line number. A core line nobody has adopted has
                # none, and a missing number is treated as absent everywhere it is printed.
                # Carried because a v7 borrow names the donor's line ("SO414285 line 4") and
                # the ladder reads its donors out of this very list (AC-S3-2, AC-S3-11).
                ProjectSalesOrderLine.line_no,
                # R22: the drill's own Ordered/Delivered columns - `plan_qty()` is the
                # SAME coalesce the board plans against (`coalesce(qty_required,
                # qty_ordered)`), reused rather than re-derived so the two screens cannot
                # come to disagree about what "Ordered" means for a line CS has stated.
                plan_qty().label("qty_ordered"),
                func.coalesce(SalesOrderLine.qty_delivered, 0).label("qty_delivered"),
            )
            .join(SalesOrder, SalesOrder.id == SalesOrderLine.sales_order_id)
            .outerjoin(SalesAgent, SalesAgent.id == SalesOrder.sales_agent_id)
            .outerjoin(
                ProjectSalesOrderLine,
                ProjectSalesOrderLine.core_sales_order_line_id == SalesOrderLine.id,
            )
            .filter(
                SalesOrderLine.product_id.in_(product_ids),
                self._demand_span(warehouse_ids),
                SalesOrder.status == "open",
                is_open_demand(),
                is_transferable_order(),
                *extra_clauses,
            )
            .all()
        )
        out: Dict[str, List[DemandLine]] = {}
        for row in rows:
            warehouse_id = str(row.warehouse_id) if row.warehouse_id else ""
            out.setdefault(str(row.product_id), []).append(
                DemandLine(
                    key=str(row.id),
                    so_number=row.so_number or "",
                    line_no=row.line_no,
                    # None for an unlocated line, which is what puts it in its own bucket.
                    warehouse=codes.get(warehouse_id),
                    agent_code=row.sales_agent,
                    required_date=row.required_date,
                    open_qty=_float(row.qty),
                    is_pool=warehouse_id in pools,
                    qty_ordered=_float(row.qty_ordered),
                    qty_delivered=_float(row.qty_delivered),
                    # R29: the Sales order cell's own link target.
                    sales_order_id=str(row.sales_order_id) if row.sales_order_id else None,
                    core_line_no=row.core_line_no,
                    source_ref=row.line_source_ref,
                )
            )
        return out

    def _holds(
        self,
        product_ids: Sequence[str],
        line_keys: set,
        *,
        include_po: bool = True,
        span: Optional[Set[str]] = None,
        supply_rows: Optional[Dict[str, List[SupplyEvent]]] = None,
        lendable_lines: Optional[Set[str]] = None,
    ) -> List[Hold]:
        """What is already promised: confirmed allocations and placement links (R21).

        `include_po` (fix round, CI, 25 Sep): `False` was the Stock Debt VIEW's own reading
        under R23 - a placement link naming a PO line pinned nothing. R42 (28 Sep 2026)
        retired that: both paths read the PO branch, so a placement on a PO line pins it in
        the view exactly as on the board.

        Two shapes, one meaning. A `so_line_allocations` row is a decision holding STOCK at a
        bin; an `order_inquiry_links` row is a placement holding a DOCUMENT. Both bind before
        anybody queues (AC-S2-2), and both are keyed to the CORE sales-order line, which is
        what the demand read above is keyed on.

        **The allocation half is `ProjectSupplyService._hold_query`, not a second spelling of
        it.** That is the one predicate for "this row is holding stock right now" - confirmed,
        located, not an ORDER source, and belonging to no decision or to an ACTIVE one - and
        it is shared with the free-stock arithmetic precisely so the two cannot come to
        disagree. Restated by hand here, it had already lost `confirmed_at IS NOT NULL`, so a
        decision saved but never confirmed held stock on this screen and nowhere else.

        **Each hold carries the bin or document it names.** `assign()` honours a hold whose
        supply is outside the read span (a site pool, an unflagged bin, another group under
        `group=`) off exactly these fields (AC-S2-1b), so the drill still says `On hand BRW`
        rather than leaving a pinned line looking unsourced.

        **A placement on an SPO line that is no longer incoming is an ON-HAND hold at the
        SPO's bin** (SPO-RECEIVED-PIN, owner 30 Sep 2026: "when the SPO is received, it is on
        hand already, so if we still link to the received SPO, it seems like there are more
        quantities than we should have"). "No longer incoming" is `open_incoming_clauses()`
        failing, evaluated in SQL on the row itself, plus the quantity test (`allocated <=
        received`) - the one rule `_supply` drops the same line from supply under, so the
        hold and the event agree. Handed on as an SPO hold, that placement met AC-S2-1b's
        stood-up branch in `assign()` (meant for supply OUTSIDE the span, not for a document
        that is not supply) and pinned an uncapped stand-in SPO to the line while the landed
        goods covered a second line as free on hand. Converted, it pins `min(placement,
        landed)` on the bin's floor, which `assign()` caps at what the bin still holds: a
        bin that has since shipped the goods pins nothing, and the line reads short.

        `span` / `supply_rows` (the warehouse ids this read covers and the events `_supply`
        read for them) decide what "the floor" means for that converted hold. A bin IN the
        span with no on-hand event is a bin the read looked at and found empty, so the hold
        is dropped rather than stood up out of nothing - `_landed_holds` pins under the same
        rule. A bin OUTSIDE the span (a site pool, an unflagged bin, another group under
        `group=`) is one the read cannot see, and the promise is honoured the way every other
        out-of-span hold is (AC-S2-1b). Neither given (a caller that only wants the holds
        listed), every converted hold is returned.

        `lendable_lines` (STOCK-DEBT-LENDABLE, the view only): the line keys that can wait
        for a re-buy. A converted received-SPO placement on such a line is `lendable` - it
        IS the line's landed goods (SO381065's 88 at BRW-BB reach the assignment this way,
        through the auto placement on SPO-2026/05-0001, and `_landed_holds` then nets its
        own read to nothing), so without the mark here the feature lent nothing on the real
        book. A decision hold (`so_line_allocations`) is never lendable: it is a Reserve
        somebody confirmed, not goods that landed for the line. `None` marks nothing.
        """
        if not line_keys:
            return []
        keys = list(line_keys)
        out: List[Hold] = []

        rows = (
            self.supply._hold_query(
                [str(pid) for pid in product_ids],
                exclude_line_ids=None,
                entities=(
                    ProjectSalesOrderLine.core_sales_order_line_id,
                    SOLineAllocation.warehouse_id,
                    held_qty_expr(),
                    Warehouse.warehouse_code,
                ),
            )
            .join(Warehouse, Warehouse.id == SOLineAllocation.warehouse_id)
            .filter(ProjectSalesOrderLine.core_sales_order_line_id.in_(keys))
            .all()
        )
        for core_line_id, warehouse_id, qty, warehouse_code in rows:
            if _float(qty) <= 0:
                continue
            out.append(
                Hold(
                    line_key=str(core_line_id),
                    supply_key=f"on_hand:{warehouse_id}",
                    qty=_float(qty),
                    kind=KIND_ON_HAND,
                    warehouse=warehouse_code,
                )
            )

        links = (
            self.db.query(
                ProjectSalesOrderLine.core_sales_order_line_id,
                OrderInquiryLink.spo_allocation_id,
                OrderInquiryLink.po_line_id,
                OrderInquiryLink.qty,
                SPOAllocation.spo_number,
                SPOAllocation.spo_line_number,
                PurchaseOrder.po_number,
                PurchaseOrder.id.label("purchase_order_id"),
                # R29 addendum: the order inquiry this placement came through - a
                # placement is part of an OI ROW's quantity on one document line, and
                # `_holds` already joins that row to get here.
                OrderInquiry.inquiry_no,
                OrderInquiry.id.label("order_inquiry_id"),
                # SPO-RECEIVED-PIN: is the placed SPO line still incoming supply? The
                # shared rule, evaluated in SQL on this very row (the way
                # `spo_supply.spo_history_for_product` labels it) rather than restated
                # in Python; the quantity half is applied below, beside the arithmetic.
                # NULL-safe for a PO link: every clause passes on NULL SPO columns, and
                # the PO branch below never reads it.
                and_(*spo_supply.open_incoming_clauses()).label("spo_incoming"),
                SPOAllocation.allocated_quantity.label("spo_allocated"),
                SPOAllocation.quantity_received.label("spo_received"),
                SPOAllocation.receipt_status.label("spo_receipt_status"),
                SPOAllocation.product_id.label("spo_product_id"),
                SPOAllocation.warehouse_id.label("spo_warehouse_id"),
                Warehouse.warehouse_code.label("spo_warehouse_code"),
                InboundShipment.actual_arrival_date.label("spo_arrived"),
            )
            .join(OrderInquiryRow, OrderInquiryRow.id == OrderInquiryLink.row_id)
            .join(OrderInquiry, OrderInquiry.id == OrderInquiryRow.order_inquiry_id)
            .join(
                ProjectSalesOrderLine,
                ProjectSalesOrderLine.id == OrderInquiryRow.so_line_id,
            )
            .outerjoin(
                SPOAllocation, SPOAllocation.id == OrderInquiryLink.spo_allocation_id
            )
            .outerjoin(
                InboundShipment,
                InboundShipment.id == SPOAllocation.inbound_shipment_id,
            )
            .outerjoin(Warehouse, Warehouse.id == SPOAllocation.warehouse_id)
            .outerjoin(
                PurchaseOrderLine, PurchaseOrderLine.id == OrderInquiryLink.po_line_id
            )
            .outerjoin(
                PurchaseOrder,
                PurchaseOrder.id == PurchaseOrderLine.purchase_order_id,
            )
            .filter(
                ProjectSalesOrderLine.core_sales_order_line_id.in_(keys),
                # A cancelled inquiry row holds nothing - the same filter every other
                # consumer of these links applies (`planning_change_service`,
                # `project_order_inquiry_service`). Without it a withdrawn placement went
                # on pinning a document to a line nobody is waiting on.
                OrderInquiryRow.state != INQUIRY_CANCELLED,
                # R7/AC-E12: SPOAllocation is OUTER-joined, so this passes a plain PO
                # link (its columns come back NULL) untouched and only excludes a
                # hold whose SPO side names a retired line.
                *spo_supply.visible_line_clauses(),
            )
            .all()
        )
        for row in links:
            qty = _float(row.qty)
            if qty <= 0:
                continue
            if not row.spo_allocation_id:
                # A placement on a PO line. `include_po=False` is kept for a caller that
                # wants on hand and SPO only; neither path passes it since R42.
                if not include_po or not row.po_line_id:
                    continue
                out.append(
                    Hold(
                        line_key=str(row[0]),
                        supply_key=f"po:{row.po_line_id}",
                        qty=qty,
                        kind=KIND_PO,
                        ref=f"PO {row.po_number}" if row.po_number else "PO",
                        oi_number=row.inquiry_no,
                        oi_id=str(row.order_inquiry_id),
                        po_number=row.po_number,
                        purchase_order_id=str(row.purchase_order_id)
                        if row.purchase_order_id
                        else None,
                    )
                )
                continue
            allocated = _float(row.spo_allocated)
            received = _float(row.spo_received)
            if not (bool(row.spo_incoming) and allocated > received):
                # SPO-RECEIVED-PIN (see the docstring): the document is not supply any
                # more, so the placement holds what it brought INTO THE BIN, and only
                # that. An on-hand hold is a decision on stock, never a placement, so it
                # names no order inquiry (`StockDebtAssignedFromOnHand` carries none).
                take = min(
                    qty,
                    _landed_qty(
                        allocated,
                        received,
                        arrived=row.spo_arrived is not None,
                        receipt_status=row.spo_receipt_status,
                    ),
                )
                if take <= EPSILON or not row.spo_warehouse_id:
                    continue
                bin_id = str(row.spo_warehouse_id)
                floor_key = f"on_hand:{bin_id}"
                if span is not None and bin_id in span:
                    floors = {
                        event.key
                        for event in (supply_rows or {}).get(str(row.spo_product_id), [])
                        if event.kind == KIND_ON_HAND
                    }
                    if floor_key not in floors:
                        # The read looked at this bin and it holds nothing of the product:
                        # the landed goods are gone, so the promise covers nobody (AC-3).
                        continue
                out.append(
                    Hold(
                        line_key=str(row[0]),
                        supply_key=floor_key,
                        qty=take,
                        kind=KIND_ON_HAND,
                        warehouse=row.spo_warehouse_code,
                        # STOCK-DEBT-LENDABLE: the line's landed goods, lendable when the
                        # line can wait (see the docstring).
                        lendable=str(row[0]) in (lendable_lines or ()),
                    )
                )
                continue
            supply_key = f"spo:{row.spo_allocation_id}"
            kind, ref = KIND_SPO, _spo_ref(row.spo_number)
            out.append(
                Hold(
                    line_key=str(row[0]),
                    supply_key=supply_key,
                    qty=qty,
                    kind=kind,
                    ref=ref,
                    spo_number=row.spo_number,
                    spo_line_number=row.spo_line_number,
                    # R29 addendum: this hold IS a placement (an `order_inquiry_links`
                    # row), so it always names the order inquiry it came through.
                    oi_number=row.inquiry_no,
                    oi_id=str(row.order_inquiry_id),
                )
            )
        return out

    # ------------------------------------------------------------------ small helpers

    def _tba_from(self) -> date:
        """The policy's TBA line, read once per request off the row the admin screen edits."""
        from app.services.scm.priority import DEFAULT_TBA_DATE_FROM

        return (
            self.supply._fulfilment_settings().get("tba_date_from")
            or DEFAULT_TBA_DATE_FROM
        )

    @staticmethod
    def _is_month_key(value: str) -> bool:
        try:
            year, month = value.split("-")
            return len(value) == 7 and 1 <= int(month) <= 12 and len(year) == 4
        except (ValueError, AttributeError):
            return False

    @staticmethod
    def _in_debt(result: Assignment) -> bool:
        """Anything owed and unsupplied - a negative month, or bucketed demand.

        The three buckets count, and the AC's "no negative month" wording is read that way
        deliberately: AC-S2-3 calls a TBA line's whole quantity DEBT, so a product whose only
        debt is 2030 demand, or an order nobody has given a location, would otherwise be
        dropped from the one screen that exists to list what is owed.
        """
        return (
            any(month.balance < 0 for month in result.months)
            or result.tba < 0
            or result.undated < 0
            or result.unlocated < 0
        )

    @staticmethod
    def _sort_key(result: Assignment, code: str) -> tuple:
        """Earliest red month, then product code; no red month sorts after every red one.

        Red is "cannot be bought in time", so the order is the order the work is urgent in.
        """
        red = next(
            (month.key for month in result.months if month.tone == "red"), None
        )
        return (red is None, red or "", code or "")

    def _axis(
        self,
        results: Iterable[Assignment],
        *,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
    ) -> List[str]:
        """The month columns of the whole filtered set: today (or `date_from`'s month, if
        later) to the last month anything is dated in (or `date_to`'s month, if earlier).
        One axis for every row, or the columns move as the reader pages.

        `date_from` (R14/AC-1b) raises the FIRST column to its own month, never below
        today's. `date_to` (A2/AC-1) caps the LAST column at its own month - demand past
        it is already dropped in `_demand()`, but a document arriving after `date_to` is
        still supply (AC-3) and could otherwise stretch the axis past a month nothing due
        survives in. Never below `first`: a `date_to` in the past still leaves the current
        month (or `date_from`'s) on screen.
        """
        first = month_key(date.today())
        if date_from is not None:
            first = max(first, month_key(date_from))
        last = first
        for result in results:
            for month in result.months:
                if month.key > last:
                    last = month.key
        if date_to is not None:
            cutoff_month = month_key(date_to)
            last = max(first, min(last, cutoff_month))
        return month_axis(first, last)

    def _months_on_axis(
        self, result: Assignment, axis: Sequence[str], product_id: str
    ) -> List[dict]:
        """One entry per axis key, in axis order. Past a row's own last event the months
        read 0, because nothing is due and nothing arrives in them (R37) - the balance does
        not carry, so a column states its own month or it states nothing owed."""
        as_of = date.today()
        lead = self._lead_cache.get(product_id, DEFAULT_LEAD_TIME_DAYS)
        by_key = {month.key: month for month in result.months}
        out: List[dict] = []
        for key in axis:
            month = by_key.get(key)
            balance = month.balance if month is not None else 0.0
            out.append(
                {
                    "key": key,
                    "balance": balance,
                    "tone": month.tone
                    if month is not None
                    else tone_for(balance, key, as_of=as_of, lead_days=lead),
                }
            )
        return out

    def _assigned_from(
        self, line, *, order_ids: Optional[Dict[str, Optional[str]]] = None
    ) -> List[Dict[str, Any]]:
        """R29 + addendum: one LINKED entry per source behind `line`'s `assigned_qty` -
        a document (SPO/PO) or an on-hand bin, each with its OWN quantity, so the FE can
        render "SPO-... line 4 (100)" / "On hand BRW-BB (14)" instead of one merged
        sentence.

        Grouped by event key - the same grouping `_source_text` already applied to its own
        text label - so a line drawing from the same document across more than one walk
        step still names it once, with the two quantities summed, rather than twice.

        `oi_number`/`oi_id` (addendum) ride on the TAKE (`item.oi_number`/`oi_id`), not the
        event: the event object is SHARED across every line that draws from it, and only a
        PINNED placement names an order inquiry at all - a plain walk draw of the same
        document, by another line, names none.

        STOCK-DEBT-LENDABLE: an on-hand take LENT by another line (`item.lent_from_so`) is
        its own entry - "On hand BRW-BB (from SO381065)" beside the plain "On hand BRW-BB"
        for the free part - and a line that lent gets one `lent` entry per receiver,
        "Lent to SO396071 (32)", after its own sources. `order_ids` links the receiver.
        """
        entries: Dict[Tuple[str, Optional[str]], Dict[str, Any]] = {}
        order: List[Tuple[str, Optional[str]]] = []
        for item in line.assigned:
            event = item.event
            key = (event.key, item.lent_from_line_key)
            if key not in entries:
                order.append(key)
                if event.kind == KIND_ON_HAND:
                    lent_from = item.lent_from_so if item.lent_from_line_key else None
                    entries[key] = {
                        "kind": KIND_ON_HAND,
                        "ref": f"On hand {event.warehouse}"
                        + (f" (from {lent_from})" if lent_from else ""),
                        "spo_number": None,
                        "spo_line_number": None,
                        "qty": 0.0,
                        "lent_from_so_number": lent_from,
                    }
                else:
                    entries[key] = {
                        "kind": event.kind,
                        "ref": event.ref or event.kind.upper(),
                        "spo_number": event.spo_number,
                        "spo_line_number": event.spo_line_number,
                        "qty": 0.0,
                        "oi_number": item.oi_number,
                        "oi_id": item.oi_id,
                        # R42: Covered by opens a PO on its own line, the way it opens an SPO.
                        **self._po_fields(event),
                    }
            entries[key]["qty"] += item.qty
        out = [{**entries[key], "qty": round(entries[key]["qty"], 4)} for key in order]
        for lent in getattr(line, "lent", ()):
            out.append(
                {
                    "kind": "lent",
                    "ref": f"Lent to {lent.so_number} ({qty_text(Decimal(str(lent.qty)))})",
                    "qty": round(lent.qty, 4),
                    "so_number": lent.so_number,
                    "sales_order_id": (order_ids or {}).get(lent.line_key),
                }
            )
        return out

    @staticmethod
    def _po_fields(event: SupplyEvent) -> Dict[str, Any]:
        """R42: a PO event's link target - `po_number`, `po_line_number`, `po_id` and
        `po_line_id` (the event key's own id). All four `None` for any other kind, so a
        supply row and a Covered by entry always carry the same keys."""
        if event.kind != KIND_PO:
            return {
                "po_number": None, "po_line_number": None, "po_id": None,
                "po_line_id": None,
            }
        _kind, line_id = parse_supply_key(event.key)
        return {
            "po_number": event.po_number,
            "po_line_number": event.po_line_number,
            "po_id": event.purchase_order_id,
            "po_line_id": line_id,
        }

    def _landed_holds(
        self,
        supply_rows: Dict[str, List[SupplyEvent]],
        demand_rows: Dict[str, List[DemandLine]],
        holds: Sequence[Hold],
        *,
        lendable_lines: Optional[Set[str]] = None,
    ) -> List[Hold]:
        """#1362 round 5 (owner ruling, 29 Sep 2026): goods ordered against a sales-order
        line stay with that line.

        Owner, verbatim: "we cannot snatch, what's ordered against the SO should stay
        belonged to it". What LANDED for a line - its own purchase (`purchase_order_lines
        .from_so_line_ref` = the line's `source_ref`), received on an SPO
        (`ProjectSupplyService._po_received_by_so_line_ref`, R7's tier 1) - is pinned to
        that line on its own bin's floor, before the walk. An earlier-due line of the same
        product at the same bin used to draw them first through the ordinary queue
        (SO382618's SRT357: 221 of 261 taken, 40 left of the 100 landed for the line), and
        every reader of this one assignment - the board walk, the confirm recheck, the
        order inquiry picker - read those units as free. Now only the truly free stock
        queues: on hand less what landed for other lines and is still owed to them.

        Pinned ONLY on the floor this read holds (`on_hand:<bin>` in the span): `assign()`
        caps a pin at what the event has left, so a bin that no longer holds the goods pins
        nothing, and a pin never stands stock up out of nothing. Less whatever this line's
        own decisions and placements already hold (`holds`), so nothing is pinned twice.
        A sibling's tier-2 SPARE is not pinned: it is not owed to the line it was bought
        for, so it stays free stock the order's own lines are credited from first (R7).

        `lendable_lines` (STOCK-DEBT-LENDABLE, the view only): the line keys that can wait
        for a re-buy (`later_order_can_wait` off the batched lead read, decided once in
        `_assignments`). A pin on such a line is `lendable` and `assign()` lets nearer
        lines draw it first. `None` (the board path) marks nothing. No extra read.
        """
        already: Dict[str, float] = {}
        for hold in holds:
            already[hold.line_key] = already.get(hold.line_key, 0.0) + float(hold.qty)
        out: List[Hold] = []
        for product_id, lines in demand_rows.items():
            floors = {
                str(event.warehouse): event.key
                for event in supply_rows.get(product_id, [])
                if event.kind == KIND_ON_HAND and event.warehouse and not event.is_pool
            }
            wanted = [
                line for line in lines
                if line.source_ref and line.warehouse and str(line.warehouse) in floors
                and not line.is_pool and float(line.open_qty) > EPSILON
            ]
            if not wanted:
                continue
            received = self.supply._po_received_by_so_line_ref(
                [line.source_ref for line in wanted], product_id=product_id, company_id=None
            )
            for line in wanted:
                landed, _document = received.get(
                    str(line.source_ref).strip(), (Decimal("0"), None)
                )
                qty = min(_float(landed), float(line.open_qty)) - already.get(line.key, 0.0)
                if qty <= EPSILON:
                    continue
                out.append(
                    Hold(
                        line_key=line.key,
                        supply_key=floors[str(line.warehouse)],
                        qty=qty,
                        kind=KIND_ON_HAND,
                        warehouse=str(line.warehouse),
                        landed=True,
                        lendable=line.key in (lendable_lines or ()),
                    )
                )
        return out

    def _book_so_holds(
        self,
        supply_rows: Dict[str, List[SupplyEvent]],
        demand_rows: Dict[str, List[DemandLine]],
        holds: Sequence[Hold],
        tba_from: date,
    ) -> List[Hold]:
        """R42: a PO line whose S/O names a sales order covers THAT order first.

        The S/O is the book's own `purchase_order_lines.from_so_line_ref`, resolved the one
        way the PO lines tab resolves it (`order_link_service.book_sales_orders_by_ref`,
        document level), and handed to `book_so_pins` beside the ref itself so the pin
        lands on the LINE the ref names (R43). Only PO lines already in this read's supply
        are asked about. Two reads for the whole page - the refs and the placements - never
        one per product.

        What the S/O may still pin is the PO line's outstanding LESS every live placement
        link on it (`order_inquiry_links.po_line_id`, any sales-order line): a placement is
        a decision somebody confirmed and it binds first (`_holds`), so nothing is counted
        twice. The arithmetic is `book_so_pins`, pure.

        R43 (owner, 28 Sep 2026, #1346): "i prefer it to be 0 days set, and when it is
        assigned, then it will fulfil the demand ady". Every PO line with an outstanding
        quantity is asked, whatever the overdue rule says of its date - dead, late or
        undated. R42 asked only the lines `counted_event` admitted, so at the shipped 0-day
        rule a PO past its Delivery date pinned nothing. R44 (#1359): the UNPINNED rest of
        the line is free supply in `assign()`, the view walking no overdue rule.
        """
        #: R45 (30 Sep 2026): the SPO line's own `from_so_line_ref` (`spo_allocations`,
        #: the same `{database}:{DocKey}:{DtlKey}` shape) is read beside the PO line's -
        #: "most SPO should have linkage already", and on this page that link is the only
        #: way an unplaced SPO reaches a line. Keyed by the EVENT key (`po:<id>` /
        #: `spo:<id>`), so the two document kinds never collide on an id.
        po_events: Dict[str, Tuple[str, SupplyEvent]] = {}
        spo_events: Dict[str, Tuple[str, SupplyEvent]] = {}
        for product_id, events in supply_rows.items():
            for event in events:
                if float(event.qty) <= EPSILON:
                    continue
                kind, line_id = parse_supply_key(event.key)
                if not line_id:
                    continue
                if kind == KIND_PO:
                    po_events[line_id] = (product_id, event)
                elif kind == KIND_SPO:
                    spo_events[line_id] = (product_id, event)
        if not po_events and not spo_events:
            return []
        refs: Dict[str, str] = {}
        if po_events:
            refs.update(
                {
                    po_events[str(line_id)][1].key: ref
                    for line_id, ref in self.db.query(
                        PurchaseOrderLine.id, PurchaseOrderLine.from_so_line_ref
                    )
                    .filter(
                        PurchaseOrderLine.id.in_(list(po_events)),
                        PurchaseOrderLine.from_so_line_ref.isnot(None),
                    )
                    .all()
                    if ref
                }
            )
        if spo_events:
            refs.update(
                {
                    spo_events[str(allocation_id)][1].key: ref
                    for allocation_id, ref in self.db.query(
                        SPOAllocation.id, SPOAllocation.from_so_line_ref
                    )
                    .filter(
                        SPOAllocation.id.in_(list(spo_events)),
                        SPOAllocation.from_so_line_ref.isnot(None),
                    )
                    .all()
                    if ref
                }
            )
        if not refs:
            return []
        orders = order_link_service.book_sales_orders_by_ref(
            self.db, sorted(set(refs.values()))
        )
        named = {key: orders[ref][0] for key, ref in refs.items() if ref in orders}
        if not named:
            return []
        events_by_key = {
            event.key: (product_id, event)
            for product_id, event in (*po_events.values(), *spo_events.values())
        }
        named_po_ids = [parse_supply_key(key)[1] for key in named if key.startswith("po:")]
        named_spo_ids = [parse_supply_key(key)[1] for key in named if key.startswith("spo:")]
        placed: Dict[str, float] = {}
        if named_po_ids:
            placed.update(
                {
                    f"po:{line_id}": _float(qty)
                    for line_id, qty in self.db.query(
                        OrderInquiryLink.po_line_id, func.sum(OrderInquiryLink.qty)
                    )
                    .join(OrderInquiryRow, OrderInquiryRow.id == OrderInquiryLink.row_id)
                    .filter(
                        OrderInquiryLink.po_line_id.in_(named_po_ids),
                        OrderInquiryRow.state != INQUIRY_CANCELLED,
                    )
                    .group_by(OrderInquiryLink.po_line_id)
                    .all()
                }
            )
        if named_spo_ids:
            placed.update(
                {
                    f"spo:{allocation_id}": _float(qty)
                    for allocation_id, qty in self.db.query(
                        OrderInquiryLink.spo_allocation_id, func.sum(OrderInquiryLink.qty)
                    )
                    .join(OrderInquiryRow, OrderInquiryRow.id == OrderInquiryLink.row_id)
                    .filter(
                        OrderInquiryLink.spo_allocation_id.in_(named_spo_ids),
                        OrderInquiryRow.state != INQUIRY_CANCELLED,
                    )
                    .group_by(OrderInquiryLink.spo_allocation_id)
                    .all()
                }
            )
        return book_so_pins(
            [
                (events_by_key[key][0], events_by_key[key][1], sales_order_id,
                 placed.get(key, 0.0))
                for key, sales_order_id in named.items()
            ],
            demand_rows,
            holds,
            tba_from=tba_from,
            line_refs={key: refs[key] for key in named},
        )

    def _source_text(self, line) -> Optional[str]:
        """What a demand row says it is covered FROM - `On hand BRW-BB`, `SPO ...`, `PO ...`.

        Human text, not an id: this is the sentence a planner reads before pressing Plan.
        """
        labels: List[str] = []
        for item in line.assigned:
            event = item.event
            label = (
                f"On hand {event.warehouse}"
                if event.kind == KIND_ON_HAND
                else (event.ref or event.kind.upper())
            )
            if label not in labels:
                labels.append(label)
        return ", ".join(labels) if labels else None

    # ------------------------------------------------------------------ last supplier (R3/A1)

    def _last_supplier_map(
        self, product_ids: Sequence[str]
    ) -> Dict[str, Dict[str, Optional[str]]]:
        """`{product_id: {"id": supplier_id | None, "name": supplier_name | None}}` -
        the LAST supplier (AC-4/AC-5): the supplier on the product's newest purchase-order
        LINE, falling back to the manually-flagged primary product supplier, else neither.

        The window (newest `PurchaseOrder.issue_date`, `PurchaseOrderLine.created_at`
        breaking a tie) is `po_last_cost_service.last_cost_rows`'s own pick key, reused
        rather than re-derived - the same "which PO line answers for this product" question,
        one PARTITION BY `product_id` instead of `(product_id, warehouse_id)` because the
        board asks per PRODUCT, not per bin. Cancelled POs are skipped (A1); a cancelled
        LINE is not, because A1 names only the PO's own status - an SPO's supplier is never
        consulted here at all (A1: "we should look at PO, not SPO").
        """
        out: Dict[str, Dict[str, Optional[str]]] = {
            str(pid): {"id": None, "name": None} for pid in product_ids
        }
        if not product_ids:
            return out

        rn = (
            func.row_number()
            .over(
                partition_by=PurchaseOrderLine.product_id,
                order_by=(
                    PurchaseOrder.issue_date.desc().nulls_last(),
                    PurchaseOrderLine.created_at.desc(),
                ),
            )
            .label("rn")
        )
        numbered = (
            self.db.query(
                PurchaseOrderLine.product_id,
                PurchaseOrder.supplier_id,
                rn,
            )
            .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderLine.purchase_order_id)
            .filter(
                PurchaseOrderLine.product_id.in_(product_ids),
                PurchaseOrder.status != "cancelled",
            )
        )
        # COMPANY SCOPE, EXPLICITLY, on BOTH tables named in this branch (belt-and-braces,
        # matching `spo_last_receipt_service`'s own windowed branch): `.subquery()` loses
        # the `with_loader_criteria` the session's `do_orm_execute` listener injects, and
        # the outer query below names only `Supplier`.
        for model in (PurchaseOrderLine, PurchaseOrder):
            predicate = build_company_predicate(model, get_company_scope(self.db))
            if predicate is not None:
                numbered = numbered.filter(predicate)
        numbered = numbered.subquery()
        po_supplier: Dict[str, Optional[str]] = {
            str(pid): (str(sid) if sid else None)
            for pid, sid in self.db.query(numbered.c.product_id, numbered.c.supplier_id)
            .filter(numbered.c.rn == 1)
            .all()
        }

        primary_rows = (
            self.db.query(ProductSupplier.product_id, ProductSupplier.supplier_id)
            .filter(
                ProductSupplier.product_id.in_(product_ids),
                ProductSupplier.is_primary_supplier.is_(True),
            )
            .all()
        )
        primary_supplier: Dict[str, str] = {
            str(pid): str(sid) for pid, sid in primary_rows
        }

        supplier_ids = {sid for sid in po_supplier.values() if sid} | set(
            primary_supplier.values()
        )
        names: Dict[str, str] = {}
        if supplier_ids:
            names = {
                str(row.id): row.supplier_name
                for row in self.db.query(Supplier.id, Supplier.supplier_name)
                .filter(Supplier.id.in_(supplier_ids))
                .all()
            }

        for pid in out:
            # The newest PO line's own answer wins WHENEVER one exists - even a PO line
            # with no supplier stated - and only a product with NO live PO at all falls
            # back to the primary flag (A1's "falling back to").
            supplier_id = po_supplier[pid] if pid in po_supplier else primary_supplier.get(pid)
            out[pid] = {
                "id": supplier_id,
                "name": names.get(supplier_id) if supplier_id else None,
            }
        return out

    # ------------------------------------------------------------------ envelope aggregates

    @staticmethod
    def _totals(data_rows: List[dict], axis: Sequence[str]) -> Dict[str, Any]:
        """The whole filtered set's totals (AC-6): summed here, over EVERY row already
        built for this request, before the page is sliced off - never the page's own rows,
        or the footer would change under the reader as they page."""
        months = {key: 0.0 for key in axis}
        tba = undated = unlocated = total = 0.0
        for row in data_rows:
            for month in row["months"]:
                months[month["key"]] += month["balance"]
            tba += row["tba"]
            undated += row["undated"]
            unlocated += row["unlocated"]
            total += row["total"]
        return {
            "months": months, "tba": tba, "undated": undated, "unlocated": unlocated,
            "total": total,
            **{key: sum(row[key] for row in data_rows) for key in AUX_KEYS},
        }

    @staticmethod
    def _suppliers_list(
        suppliers: Iterable[Dict[str, Optional[str]]],
    ) -> List[Dict[str, str]]:
        """Distinct last suppliers, sorted by name (AC-7). Takes the raw `{id, name}`
        entries directly - AC-7c: the caller passes the set BEFORE the `supplier_id`
        filter narrows it, not a page of already-built rows, so the facet never loses an
        option the reader could still pick."""
        seen: Dict[str, str] = {}
        for supplier in suppliers:
            supplier_id = supplier["id"]
            if supplier_id and supplier_id not in seen:
                seen[supplier_id] = supplier["name"] or ""
        return [
            {"id": supplier_id, "name": name}
            for supplier_id, name in sorted(seen.items(), key=lambda pair: pair[1])
        ]

    @staticmethod
    def _sheet_counts(data_rows: List[dict]) -> Dict[str, int]:
        """The EXACT export sheet counts for the whole filtered set (AC-7b): distinct
        supplier keys, distinct category keys and distinct (supplier, category) PAIRS
        actually present - a pair with no row does not get a sheet, so this is never
        `len(suppliers) * len(categories)`. `None`/blank folds into one "none" bucket each,
        the same bucket the workbook titles "No supplier" / "No category" (AC-13..AC-16)."""
        supplier_keys = {row["supplier_id"] or "" for row in data_rows}
        category_keys = {row["category_code"] or "" for row in data_rows}
        pair_keys = {
            (row["supplier_id"] or "", row["category_code"] or "") for row in data_rows
        }
        return {
            "supplier": len(supplier_keys),
            "category": len(category_keys),
            "supplier_category": len(pair_keys),
        }
    # ------------------------------------------------------------------ export (R5/R10, AC-12..AC-18)

    def export(
        self,
        *,
        query: Optional[str] = None,
        group: Optional[str] = None,
        only_debt: bool = True,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
        supplier_ids: Optional[Sequence[str]] = None,
        book: str = "all",
        split: str = "none",
        show_aux: bool = False,
    ) -> Tuple[bytes, str, str, Dict[str, int]]:
        """The workbook for the CURRENT filters (AC-17): `(bytes, content_type, filename,
        {"rows": n, "sheets": m})`, the same tuple shape `low_stock_report_service.
        export_low_stock` returns.

        Built off `list()` itself, unpaged (AC-17: the export's rows are exactly the
        list's, for the same filters) - `limit=MAX_LOW_STOCK_ROWS + 1` is enough to prove
        whether the filtered set fits under the cap (AC-18) without a second, differently-
        shaped read: `pagination["total"]` is always the WHOLE filtered set regardless of
        the limit used to slice `data`, so a set that fits is returned complete and a set
        that does not is refused before `data` is ever trusted to be complete.
        """
        listing = self.list(
            query=query, group=group, only_debt=only_debt, date_from=date_from,
            date_to=date_to, supplier_ids=supplier_ids, book=book, page=1,
            limit=MAX_LOW_STOCK_ROWS + 1,
        )
        if listing["pagination"]["total"] > MAX_LOW_STOCK_ROWS:
            raise AppException(422, "Narrow the filters first")
        return self._render_workbook(listing, split=split, show_aux=show_aux)

    def _render_workbook(
        self, listing: Dict[str, Any], *, split: str, show_aux: bool = False
    ) -> Tuple[bytes, str, str, Dict[str, int]]:
        from openpyxl import Workbook
        from openpyxl.utils import get_column_letter

        from app.services.scm.summary_order_service import write_sheet

        axis: List[str] = listing["months"]
        rows: List[dict] = listing["data"]

        # R17: "No date" and "No location" leave the workbook (they still leave the screen
        # too - the row itself still carries `undated`/`unlocated`, just not as columns
        # here or in `Total`).
        columns = (
            ["Product", "Name", "Category", "Supplier"]
            + (AUX_EXPORT_HEADERS if show_aux else [])
            + [_export_month_label(key) for key in axis]
            + ["TBA", "Total"]
        )
        widths = [16, 30, 14, 24] + ([12] * 4 if show_aux else []) + [10] * len(axis) + [10, 12]
        width_map = {
            get_column_letter(index + 1): width for index, width in enumerate(widths)
        }

        # R5/AC-13: `split == "none"` is one sheet, one fixed title - never derived from a
        # row's own data. Any other split groups by the shared `workbook_split.split_rows`
        # (AC-2, PLAN-low-stock-export-split-25sep) - the SAME grouping and sanitised-title
        # sort the low stock report's own split uses, lifted here rather than reinvented.
        ordered: List[Tuple[str, List[dict]]] = (
            [("Stock debt", rows)]
            if split == "none"
            else split_rows(
                rows, split,
                supplier=lambda r: r["supplier_name"],
                category=lambda r: r["category_code"],
            )
        )

        wb = Workbook()
        used_titles: Set[str] = set()
        sheet_count = 0
        for index, (key, group_rows) in enumerate(ordered):
            ws = wb.active if index == 0 else wb.create_sheet()
            ws.title = key if split == "none" else unique_sheet_title(key, used_titles)
            sheet_count += 1
            data = [self._export_row(row, axis, show_aux) for row in group_rows]
            data.append(self._export_total_row(group_rows, axis, show_aux))
            write_sheet(ws, columns, data, width_map)

        buf = BytesIO()
        wb.save(buf)
        return (
            buf.getvalue(),
            EXPORT_CONTENT_TYPE,
            f"stock-debt-{date.today().strftime('%d%m%Y')}.xlsx",
            {"rows": len(rows), "sheets": sheet_count},
        )

    @staticmethod
    def _export_row(row: dict, axis: Sequence[str], show_aux: bool = False) -> tuple:
        """One product, in the export's own column order (AC-13). `Name` prints blank when
        `list()` has already nulled it (AC-9); every other blank prints as `""`, never a
        bare 0 that would read as a fact somebody measured.

        Every text cell goes through `_xlsx_safe_text` (AC-13b, security review) - a
        supplier or a product code is free text off somebody else's document, and a
        leading `=`/`+`/`-`/`@` reaches openpyxl unescaped otherwise, which a spreadsheet
        reads as a formula the moment the file is opened. The same guard
        `low_stock_report_service._sheet_row` already applies to every one of ITS text
        cells.
        """
        from app.services.scm.proforma_invoice_service import _xlsx_safe_text

        balances = {month["key"]: month["balance"] for month in row["months"]}
        return (
            _xlsx_safe_text(row["product_code"]),
            _xlsx_safe_text(row["product_name"] or ""),
            _xlsx_safe_text(row["category_code"] or ""),
            _xlsx_safe_text(row["supplier_name"] or ""),
            *([row[key] for key in AUX_KEYS] if show_aux else []),
            *[balances.get(key, 0.0) for key in axis],
            row["tba"],
            row["total"],
        )

    @staticmethod
    def _export_total_row(
        rows: List[dict], axis: Sequence[str], show_aux: bool = False
    ) -> tuple:
        """The sheet's OWN Total footer (R5): summed over the rows THIS sheet carries, so
        a buyer reading one supplier's tab foots it without opening the others. R17: no
        "No date"/"No location" columns to foot any more."""
        month_sums = {key: 0.0 for key in axis}
        tba = total = 0.0
        for row in rows:
            for month in row["months"]:
                month_sums[month["key"]] += month["balance"]
            tba += row["tba"]
            total += row["total"]
        return (
            "Total", "", "", "",
            *([sum(row[key] for row in rows) for key in AUX_KEYS] if show_aux else []),
            *[month_sums[key] for key in axis],
            tba, total,
        )
