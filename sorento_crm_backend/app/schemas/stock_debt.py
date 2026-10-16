"""The Stock Debt payloads (S2, AC-S2-6 / AC-S2-7).

Field for field the same shapes as `app/(protected)/project-sales/stock-debt/types/
stockDebt.types.ts`; the two are one contract written twice, once per language, and neither
restates the other's reasoning - the route contract lives in the FE service's header and the
arithmetic in `supply_assignment`.

A `response_model` DROPS what it does not declare, so every field the service computes is
declared here and asserted by name in `tests/scm/test_stock_debt_routes.py`.
"""
from __future__ import annotations

#: Aliased, because `StockDebtSupplyEvent` has a FIELD called `date` (the arrival, which is
#: what the FE column is called) and a bare `date` annotation inside that class body resolves
#: to the field, not to the type - which pydantic reads as "this must be None" and every
#: dated event then fails response validation.
from datetime import date as DateType
from typing import Annotated, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator

from app.schemas.export_split import ExportSplit as ExportSplit

Tone = Literal["red", "amber", "green"]
#: `order_back` (STOCK-DEBT-LENDABLE, 30 Sep 2026): the line lent its landed goods to
#: nearer lines and is owed a re-buy for them at its own date.
DemandStatus = Literal["covered", "late", "short", "pinned", "order_back"]
SupplyKind = Literal["on_hand", "spo", "po"]
#: `book` (R1/AC-8): `all` (default) spans flagged project bins AND the site pools in one
#: read; `project` reproduces the pre-24-Sep view (flagged bins only); `retail` is pools
#: only and ignores `group`.
Book = Literal["all", "project", "retail"]
#: The export workbook's split (R5/AC-13..AC-16). One sheet for `none`. Imported from the
#: shared `app.schemas.export_split` (PLAN-low-stock-export-split-25sep) now that the low
#: stock report's own export accepts the same four values - re-exported under this name
#: (`as ExportSplit`) so every other import site in this module is unchanged.


class StockDebtMonth(BaseModel):
    """One month of a product row, on its own (R37).

    `balance` is the supply dated in the month that is still free when the assignment walk
    is over, less what the lines due in the month were short of on their own dates. It does
    NOT carry: a month with nothing due and nothing arriving reads 0.
    """

    key: str
    balance: float
    tone: Tone


class StockDebtRow(BaseModel):
    product_id: str
    product_code: str
    product_name: Optional[str] = None
    #: One entry per axis month, in axis order.
    months: List[StockDebtMonth]
    #: Demand dated on or after the policy's `tba_date_from`, demand with no date, and
    #: demand booked at no warehouse at all. None of the three draws supply (R14, AC-S2-1b's
    #: sibling case), so all three are plain signed totals and carry no tone.
    tba: float
    undated: float
    unlocated: float
    #: The row's LAST supplier (R3/A1, AC-4/AC-5): the supplier on the product's newest
    #: purchase-order line, falling back to the primary-flagged product supplier, else
    #: `None`. Never rendered as an id on the FE - `supplier_name` is what prints.
    supplier_id: Optional[str] = None
    supplier_name: Optional[str] = None
    #: `products.category_id` is mandatory, so "no category" is a BLANK code (`""`), the
    #: same convention `low_stock_report_service._master_map` already uses - never `None`.
    category_code: Optional[str] = None
    #: Sum of every month's balance plus `tba` ONLY (R17: "No date"/"No location" leave
    #: the screen and the workbook both, so `undated`/`unlocated` are no longer folded in
    #: here - they still ride on the row, unchanged, just not in this figure).
    total: float = 0.0
    #: Auxiliary columns: BRW pool on hand, open PO, open SPO, and dealer outstanding.
    pool_on_hand: float = 0.0
    pool_po_qty: float = 0.0
    pool_incoming_qty: float = 0.0
    dealer_outstanding: float = 0.0


class StockDebtPagination(BaseModel):
    total: int
    page: int
    limit: int


class StockDebtTotals(BaseModel):
    """The WHOLE filtered set's own totals (AC-6), never the page's - so the footer
    prints the same figures on page 1 and on page 2.

    `total` sums `months` + `tba` ONLY (R17), the same rule each row's own `total`
    follows - `undated`/`unlocated` still ride here for the two columns that still show
    them, just not folded into `total`.
    """

    months: Dict[str, float]
    tba: float
    undated: float
    unlocated: float
    total: float
    pool_on_hand: float = 0.0
    pool_po_qty: float = 0.0
    pool_incoming_qty: float = 0.0
    dealer_outstanding: float = 0.0


class StockDebtSupplier(BaseModel):
    """One entry of the toolbar's supplier select (AC-7) - never an id on screen."""

    id: str
    name: str


class StockDebtSheetCounts(BaseModel):
    """The exact export sheet counts for the CURRENT filtered set (AC-7b), none-buckets
    ("No supplier" / "No category") included - so the export popover's preview never has
    to guess at a bucket it cannot see from one page."""

    supplier: int
    category: int
    supplier_category: int


class StockDebtLocation(BaseModel):
    """One warehouse of the book's span, for the toolbar's Location select."""

    id: str
    code: Optional[str] = None
    name: Optional[str] = None


class StockDebtList(BaseModel):
    """The list envelope: the repo's `{data, pagination}` plus the column axis.

    The axis, `totals`, `suppliers` and `sheet_counts` are envelope-level because each is a
    property of the whole FILTERED SET: derived per page, they would change under the
    reader as they page (AC-6/AC-7/AC-7b).
    """

    data: List[StockDebtRow]
    pagination: StockDebtPagination
    months: List[str]
    tba_month: str
    groups: List[str]
    locations: List[StockDebtLocation]
    totals: StockDebtTotals
    suppliers: List[StockDebtSupplier]
    sheet_counts: StockDebtSheetCounts


class StockDebtAssignedFromOnHand(BaseModel):
    """One `assigned_from` entry sourced from a bin - never a document, so it carries no
    `oi_number`/`oi_id` at all (R29 addendum): an on-hand hold is a DECISION
    (`so_line_allocations`), never a placement, and there is no order inquiry to name."""

    kind: Literal["on_hand"]
    ref: str
    spo_number: Optional[str] = None
    spo_line_number: Optional[int] = None
    qty: float
    #: STOCK-DEBT-LENDABLE: the sales order whose landed goods this take was LENT from
    #: ("On hand BRW-BB (from SO381065)"). `None` on free stock at the same bin, which is
    #: then a separate entry.
    lent_from_so_number: Optional[str] = None


class StockDebtAssignedFromLent(BaseModel):
    """STOCK-DEBT-LENDABLE: one receiver of this line's lendable landed goods - "Lent to
    SO396071 (32)". Not a source (nothing was assigned to this line by it); listed under
    Covered by so the far line says where its goods went and the reader can open the
    receiving order."""

    kind: Literal["lent"]
    ref: str
    qty: float
    so_number: str
    sales_order_id: Optional[str] = None


class StockDebtAssignedFromDocument(BaseModel):
    """One `assigned_from` entry sourced from an SPO/PO document (R29). `oi_number`/
    `oi_id` name the order inquiry a PINNED placement came through - both `None` for a
    plain WALK draw, which came through no placement at all (R29 addendum)."""

    kind: Literal["spo", "po"]
    ref: str
    spo_number: Optional[str] = None
    spo_line_number: Optional[int] = None
    qty: float
    oi_number: Optional[str] = None
    oi_id: Optional[str] = None
    #: R42 (28 Sep 2026): a PO's own link target - its number, its line within the
    #: document, and the two ids the document dialog opens a PO on. All `None` for any
    #: other kind.
    po_number: Optional[str] = None
    po_line_number: Optional[int] = None
    po_id: Optional[str] = None
    po_line_id: Optional[str] = None


#: Discriminated on `kind` (the same idiom `price_tag.TagLayerPropsDoc` already uses) so an
#: on-hand entry's wire shape never grows the two OI keys a document entry always carries.
StockDebtAssignedFrom = Annotated[
    Union[
        StockDebtAssignedFromOnHand,
        StockDebtAssignedFromDocument,
        StockDebtAssignedFromLent,
    ],
    Field(discriminator="kind"),
]


class StockDebtDemandLine(BaseModel):
    so_number: str
    agent_code: Optional[str] = None
    #: The bin the line is booked in - the drill's Bin column (AC-S2-7).
    warehouse_code: Optional[str] = None
    required_date: Optional[DateType] = None
    open_qty: float
    #: R22: the drill's own Ordered/Delivered columns, beside Outstanding (`open_qty`,
    #: unchanged). `qty_ordered` is `plan_qty()` - CS's own `qty_required` when the Order
    #: Inquiry sheet states one, else the sales-order book's `qty_ordered`.
    qty_ordered: float
    qty_delivered: float
    assigned_qty: float
    #: Retired by R29 for `assigned_from` below - kept declared only because
    #: `_source_text` still computes it and nothing is served by dropping a harmless field.
    assigned_source: Optional[str] = None
    status: DemandStatus
    #: What the line went short of ON ITS OWN DATE - the quantity its month books (R37).
    #: A `late` line ends covered and still carries one.
    short_qty: float
    #: R29: the sales order this line belongs to - the FE's own link target for the Sales
    #: order cell (`/scm/sales-orders/<id>`).
    sales_order_id: Optional[str] = None
    #: R29: `assigned_source` (free text) replaced by one LINKED entry per source.
    assigned_from: List[StockDebtAssignedFrom] = []
    #: STOCK-DEBT-LENDABLE: what nearer lines took of this line's lendable landed goods -
    #: the "order back N" the status pill prints. 0 on every line that lent nothing.
    lent_qty: float = 0.0


class StockDebtAssignedTo(BaseModel):
    so_number: str
    #: R29: the SO LINE's own number (the project mirror's `line_no`), beside `so_number` -
    #: "SO382618 line 2". `None` when the core line has no project-line number of its own.
    line_no: Optional[int] = None
    qty: float


class StockDebtSupplyEvent(BaseModel):
    kind: SupplyKind
    ref: Optional[str] = None
    #: R29: the SPO's own `spo_number`/`spo_line_number` off `spo_allocations` - the
    #: Document cell's link target. `None` for on hand and for PO.
    spo_number: Optional[str] = None
    spo_line_number: Optional[int] = None
    #: R42 (28 Sep 2026): a PO's own link target - its number, its line within the
    #: document, and the two ids the document dialog opens a PO on. All `None` for any
    #: other kind.
    po_number: Optional[str] = None
    po_line_number: Optional[int] = None
    po_id: Optional[str] = None
    po_line_id: Optional[str] = None
    warehouse_code: Optional[str] = None
    #: Arrival: today for on hand, the SPO's arrival, and for a PO line its Delivery date
    #: (`expected_date`, R42), else `issue + lead` (R29).
    date: Optional[DateType] = None
    #: PO only: the SO delivery date the line was typed against. Display only (R30).
    bought_for: Optional[DateType] = None
    qty: float
    #: What nobody took by the end of the walk - the quantity its month credits (R37).
    #: Zero for a DEAD document, which is not supply until somebody re-dates it (R31).
    free_qty: float
    #: Arrival passed and the grace period has given up on it (later than
    #: `overdue_dead_days`): listed, and counted as nothing (R31, as R-O leaves it). The
    #: row reads "not counted".
    overdue: bool
    #: The date the DOCUMENT states, when `date` above is the ASSUMED one the grace period
    #: gave a late arrival (R-O). `None` when the two are the same, so a reader can print
    #: "assumed 17 Sep 2026, stated 24 Jul" only where there is something to say.
    #: `response_model` drops an undeclared field, so this is named explicitly.
    stated_date: Optional[DateType] = None
    #: How late the paperwork is, in days, on the day the walk was taken (R-O). 0 when the
    #: arrival is the one the document states.
    days_late: int = 0
    assigned_to: List[StockDebtAssignedTo]
    #: R26: an SPO's own Received (`quantity_received`) and Outstanding (the walk's own
    #: netted balance, what `qty` used to state before this ruling split it out) - `qty`
    #: above is now the SPO line's RAW ordered quantity. R42: a PO line states the same
    #: three off its own `qty_ordered`/`qty_received`. Both `None` for on hand
    #: (on hand has no received/outstanding history to state), so the drill prints those
    #: two columns blank rather than a fabricated 0.
    received_qty: Optional[float] = None
    outstanding_qty: Optional[float] = None


class StockDebtReserveSource(BaseModel):
    """One location and kind of project-reserve supply in a month (`HQ reserve PO`)."""

    label: str
    qty: float


class StockDebtProjection(BaseModel):
    """STOCK-DEBT-PROJECTION (owner, 12 Oct 2026, option D): how one month's projected
    balance is made up. `projected = month_balance + carried_in + reserve_supply`, where
    `month_balance` is the project lines' own figure (pool supply counts only through a
    link, as Covered by shows) and `reserve_supply` a project reserve's unlinked incoming
    documents; `carried_out` is the reserve surplus the next month starts with."""

    carried_in: float
    month_balance: float
    reserve_supply: float
    reserve_sources: List[StockDebtReserveSource] = []
    projected: float
    carried_out: float


class StockDebtCell(BaseModel):
    """The two tables behind one cell. `sum(supply.free_qty) - sum(demand.short_qty)` is the
    balance the cell that opened them prints (R37)."""

    demand: List[StockDebtDemandLine]
    supply: List[StockDebtSupplyEvent]
    #: R25: the tab labels' own quantity totals, over the WHOLE tab - `demand_total_qty`
    #: sums `open_qty` over `demand`; `supply_total_qty` sums each row's own Qty column
    #: (Outstanding for an SPO, the on-hand figure otherwise), never recomputed by the FE.
    demand_total_qty: float
    supply_total_qty: float
    #: Set only when the drill was opened with `projected=true` on a month column.
    projection: Optional[StockDebtProjection] = None


class StockDebtFilters(BaseModel):
    """Every list filter except `page`/`limit`: the board's own current filters. The export
    body adds `split` to it; a snapshot stores it as taken. Every field
    optional/defaulted, the same shape `list_stock_debt`'s own query params default to."""

    query: Optional[str] = None
    group: Optional[str] = None
    location: List[str] = []
    exclude_location: List[str] = []
    only_debt: bool = True
    date_from: Optional[DateType] = None
    date_to: Optional[DateType] = None
    supplier_ids: List[str] = []
    book: Book = "all"
    #: STOCK-DEBT-PROJECTION: the board's "Projected balance" switch.
    projected: bool = False
    #: The workbook's auxiliary columns (the board's "Show more" toggle). Not a filter, but
    #: a snapshot keeps it so its page shows the columns the board was showing.
    show_aux: bool = False

    @field_validator("location", "exclude_location", mode="before")
    @classmethod
    def _location_as_list(cls, value):
        """A bare string wraps and None is empty."""
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        return value


class StockDebtExportIn(StockDebtFilters):
    """The export route's body (AC-12): the list filters plus the workbook `split`, so
    `{"split": "none"}` alone is a valid request.

    `date_from`/`date_to` replace `cutoff` and `supplier_ids` replaces `supplier_id`
    (R14/R15): both are REMOVED, not aliased - a caller still sending the old names sends
    them into nothing, exactly as `group` sends into a param the export never reads (R16
    leaves `group` itself alone on the BACKEND; the export body's own field is unaffected
    by that ruling and stays for parity with `list()`'s own signature).
    """

    split: ExportSplit = "none"
