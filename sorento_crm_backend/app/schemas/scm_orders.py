"""SCM M1 sales-order + purchase-order schemas.

Mirror the FE contract in ``services/salesOrderService.ts`` /
``purchaseOrderService.ts`` + ``types/scm.types.ts``. No UUIDs surface - SO by
so_number, PO by po_number; customer / product / supplier / warehouse resolved to
human codes/names. PO is read-only at M1 (create/confirm/receive land in M4).
"""
from __future__ import annotations

from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


# --- sales orders -----------------------------------------------------------

class SalesOrderLineInquiry(BaseModel):
    """The order inquiry covering one sales-order line, in the two words a person reads.

    The ROW's state, not the header's: "purchasing placed this line" is what the column
    answers, and a header sitting at `raised` while its row has been placed on a PO would
    say the opposite.
    """

    #: `OI-000123`. Null only on a row raised before inquiries were numbered.
    inquiry_no: Optional[str] = None
    state: str
    # AC-B6-8 (`PLAN-board-oi-mechanical-22sep.md`, S6): addressing only, never
    # rendered - the Order inquiry cell's own deep link resolves to
    # `/project-sales/order-inquiries/<inquiry_id>?row=<row_id>`.
    inquiry_id: Optional[str] = None
    row_id: Optional[str] = None


class SalesOrderLineSupplyComponent(BaseModel):
    """One piece of a line's supply, in the vocabulary the planning board reads
    (`PLAN-scm-cs-planning-uat.md` section 2).

    The COMPONENTS, never a sentence: the words ("Use shared stock 71 from BRW") are written
    in one place, `project-sales/_shared/lib/supplyVocabulary.ts`, and a sentence composed
    here would be a second implementation of the same vocabulary drifting against the board.

    `rung` is what decides the words - never the warehouse code, which reads `BRW-BB` and
    `BRW` as the same site when they are the agent's own location and the shared pool.
    """

    kind: str
    qty: str
    #: The warehouse this piece is drawn from, by CODE. `None` for a Buy, which is held
    #: nowhere yet.
    source_location: Optional[str] = None
    #: `pool` / `group_take` / `group_borrow` / `cross_group_borrow` / `incoming` / `buy`.
    #: `None` on a component frozen before the rung was recorded.
    rung: Optional[str] = None
    #: The sales order a borrow was taken FROM, when one was named. What tells a borrow from
    #: another order apart from a borrow of free stock elsewhere.
    donor_so_number: Optional[str] = None


class SalesOrderLineLink(BaseModel):
    """One link on the order inquiry row covering a sales order line (AC-I9).

    A subset of `OrderInquiryLinkOut`: what a LINE's column needs to say where its Buy
    sits, and nothing about who linked it or when, which the order inquiry screen owns.
    Never an id on screen - the document number and the line label are what a person
    reads, and `line_label` is absent rather than invented when the book numbered nothing.
    """

    #: `po` or `spo`. EITHER on any linkable row since R5 (27 Aug,
    #: `PLAN-scm-oi-draft-links.md`): SPO first, then PO. It was the order back alone
    #: under the 25 Aug rule, which no longer holds.
    kind: str
    document: Optional[str] = None
    line_label: Optional[str] = None
    #: The PO/SPO header this link points at (L2/L4, review round). Never rendered as text -
    #: the FE uses it to make `document` a link to `/scm/purchase-orders/{id}` - `None` when
    #: the caller building this row does not have the id at hand, in which case `document`
    #: reads as plain text same as before this field existed.
    purchase_order_id: Optional[str] = None
    qty: str
    location: Optional[str] = None
    expected_date: Optional[str] = None
    #: The document arrives AFTER the row's own required date (AC-P3-7). Read here as well
    #: as on the worklist, because "arrives late" has to say so wherever the link is shown.
    late: bool = False
    #: HOW late, in days (AC-D17). `None` when the document is not late, or when either
    #: date is missing. "Arrives late" without the number is a warning nobody can plan
    #: against, and this schema is a second `response_model` over the same link: a field it
    #: does not declare is dropped, so the badge here would say less than the identical
    #: badge on Order Inquiries.
    late_days: Optional[int] = None
    #: A derived SPO is an OPEN SPO allocation the row's linked PO was raised from (S5 of
    #: `PLAN-scm-oi-excel-parity.md`), read here so the SO detail can say "via PO" the same
    #: way the worklist does. Never a stored link - `order_inquiry_links` never gets a row
    #: for one.
    derived: bool = False


class SalesOrderLinePlanningChangeResult(BaseModel):
    """What Apply wrote for this row alone (`planning_change_service.row_out`'s own
    `result` key) - `executed_reallocations` says where a moved quantity actually went,
    `released_documents` names one given back rather than moved. Both empty on a row
    Apply has not touched yet."""

    executed_reallocations: List[str] = Field(default_factory=list)
    released_documents: List[str] = Field(default_factory=list)


class SalesOrderLinePlanningChange(BaseModel):
    """The latest planning-change row for THIS line, within the order's own newest batch.

    A cancelled line leaves the fulfilment board entirely (a closed line has no cell), so
    the board's own "Where it went" dialog is unreachable there right when CS needs it -
    this is the SO detail screen's own seam onto the same fact. `applied_state` is
    whatever the row's own is (pending, applied, failed, superseded), never filtered to
    one value: a superseded row is still worth reading if it is the newest one the batch
    carries for this line. `result` is `None` until Apply has run.
    """

    id: str
    kind: str
    applied_state: str
    result: Optional[SalesOrderLinePlanningChangeResult] = None


class SalesOrderLine(BaseModel):
    id: str
    #: AutoCount's own line number (`Seq`), kept as it arrives - the address the Lines tab
    #: sorts and labels by (PLAN-so-lines-autocount-order.md). `None` for a line AutoCount
    #: never numbered.
    line_no: Optional[int] = None
    #: Where THIS LINE came from - never inherited from the header (R2): a line with no
    #: provenance of its own reads `manual` even under an AutoCount or uploaded header.
    source: str = "manual"
    sku: str
    product_name: str
    qty_ordered: float
    qty_delivered: float
    #: What is still to go out on this line: `ordered - delivered`, floored at 0, and **0 on a
    #: CLOSED line whatever those two say**. A book re-upload closes a line by absence without
    #: knowing what shipped, so `qty_delivered` stays 0 and the subtraction alone would report
    #: the whole quantity as still owed on an order that is done. Stated here rather than
    #: recomputed on the client so the grid, its footer and the header cannot disagree.
    outstanding_qty: float = 0
    uom: str
    #: What the customer pays for this line, and what the sales book states about it.
    #: `Decimal`, never `float`: money read back through a float is how RM 985.00 becomes
    #: 984.9999999. All three are `None` when the file said nothing - a 0 discount claims a
    #: discount of nothing was given, and a 0 total claims the line was free.
    unit_price: Optional[Decimal] = None
    discount: Optional[Decimal] = None
    line_total: Optional[Decimal] = None
    #: Where this line ships from. Per line: one order can land in two locations.
    warehouse_code: str = ""
    #: `open` or `closed`. A closed line is not a commitment however much it still shows.
    line_status: str = "open"
    #: When this line's quantity is due. Per line, for the same reason as the location.
    required_date: Optional[str] = None
    #: What has already been PLANNED about this line, and how far purchasing has got with
    #: it: the inquiry row covering the line through the planning record's mirror
    #: (`projects.sales_order_lines.core_sales_order_line_id`). `None` when nobody has
    #: raised one - never an empty object, because "nobody was told about this line" and
    #: "told, about nothing" are different answers.
    #:
    #: Sent on the SINGLE order read only. The list has no column for it and would pay two
    #: more queries a page for a fact nothing there prints.
    order_inquiry: Optional[SalesOrderLineInquiry] = None
    #: Which confirmed revision decided supply for this line: the ACTIVE decision's
    #: `revision_no` when its `line_snapshots` name this core line, `None` otherwise.
    #:
    #: Per LINE and not per order, because since `PLAN-fulfilment-planning-from-autocount
    #: -so.md` 13.4 a confirmation covers the SUBSET the planner chose - an order with an
    #: active revision can still hold lines nobody has decided, and a header-level answer
    #: would report those as settled.
    decision_revision: Optional[int] = None
    #: What was DECIDED for this line and what the engine had SUGGESTED, as components
    #: (AC-D4). Both read off the active revision's snapshot for this line.
    #:
    #: `supply_decided` is `None` on a line no active revision covers - the same answer
    #: `decision_revision` gives, for the same reason. `supply_proposed` is `None` for that
    #: AND for a revision written before the proposal was frozen: "not recorded" and "the
    #: engine suggested nothing" are different answers, and an empty list would claim the
    #: second.
    #:
    #: An undecided line carries no suggestion here on purpose: the live ladder is the
    #: planning board's read, and running it per line on a detail page of 300 lines would be
    #: 300 engine walks for a column the board already answers.
    supply_decided: Optional[List[SalesOrderLineSupplyComponent]] = None
    supply_proposed: Optional[List[SalesOrderLineSupplyComponent]] = None
    #: A SAVED (unconfirmed) decision on this line - the planning board's own draft, not
    #: yet promoted by Confirm (D10, captain 3 Sep). `None` when no draft covers the line,
    #: OR the same line already carries `supply_decided`: Confirm deletes the draft it
    #: promotes in the same write, so the two never both answer for one line at once.
    #:
    #: Components, in the same vocabulary `supply_decided` is - `project_line_draft_
    #: service` stores the frontend's own `BoardDecision` JSON opaque, and this is the one
    #: place it is read. An approval with no edits carries none of the three kinds this
    #: reads (the engine's suggestion IS the decision, nothing was typed to state again),
    #: so it prints `[]` - only an amendment (a Buy, a Reserve add, a Borrow) has a
    #: composition to show.
    supply_saved: Optional[List[SalesOrderLineSupplyComponent]] = None
    #: Who saved it, by name - never an id - and when, ISO. Both `None` exactly when
    #: `supply_saved` is `None`.
    saved_by: Optional[str] = None
    saved_at: Optional[str] = None
    #: The engine has re-suggested this line since it was saved (the board's own AC-4.4
    #: predicate, `project_line_draft_service.is_stale`), judged on the LINE's own
    #: outstanding quantity and required date rather than the proposal - never true when
    #: `supply_saved` is `None`, since nothing was written down to compare.
    saved_stale: bool = False
    #: WHERE this line's Buy sits (AC-I9): every link on the order inquiry row covering
    #: the line, PO or SPO, with the quantity each holds. Read off the SAME child table
    #: (`projects.order_inquiry_links`) the order inquiry worklist's "Linked to" column
    #: and the PO occupancy panel read, through the one reader, so the three surfaces
    #: cannot answer differently.
    #:
    #: `[]` when a row covers the line and nothing has been linked yet; `None` when no row
    #: covers it at all. The two are different answers - "nothing has been linked" against
    #: "nobody was told about this line" - and the column prints each in its own words.
    linked_to: Optional[List[SalesOrderLineLink]] = None
    #: The latest planning-change row for this line, within the order's own newest batch
    #: (`_line_planning_changes`) - `None` when no batch has ever named this line. Sent on
    #: the SINGLE order read only, same gate as `order_inquiry` above: the list has no
    #: column for it.
    planning_change: Optional[SalesOrderLinePlanningChange] = None


class SalesOrder(BaseModel):
    id: str
    so_number: str
    order_type: str
    order_type_label: str
    customer_code: str
    customer_name: str
    market_segment: Optional[str] = None
    priority: str
    status: str
    order_date: str
    requested_delivery_date: Optional[str] = None
    #: Every DISTINCT date this order's LINES are due on (`sales_order_lines.required_date`),
    #: earliest first. Empty when no line names one, because an order nobody dated is not
    #: due today. This is what the list's "Delivery date" column shows - one order routinely
    #: ships on several days, so the header's own `requested_delivery_date` (a different
    #: figure, and blank on most of this book) could never answer "when is this due". That
    #: field is unchanged and still on the detail page.
    #:
    #: A LIST, not the `delivery_date_from`/`_to` span it replaced: an order due on the 12th
    #: of January and the 10th of March is due on two days, and printing it as a range
    #: claims a stretch of eight weeks nothing in the data says. The list's sort key keeps
    #: the old name (`min(required_date)` under `delivery_date_from`) because that is the
    #: column's id, and renaming it drops every saved column layout that names it.
    delivery_dates: List[str] = Field(default_factory=list)
    #: Who sold it, resolved from `sales_orders.sales_agent_id` (`sales_agents` master).
    #: The id is carried only so the detail page's edit select can pre-select the current
    #: agent - the code + label are what a person reads; never a bare UUID in the UI.
    sales_agent_id: Optional[str] = None
    sales_agent_code: Optional[str] = None
    #: `sales_agents.person_label` - the human this agent code belongs to. Absent when
    #: the agent has not been given one, which is most of the master today.
    sales_agent_label: Optional[str] = None
    total_qty: float
    committed_qty: float
    #: What the order is WORTH, summed from its own lines: each line's stated `line_total`
    #: where it has one, otherwise `unit_price * qty_ordered - discount`. `None` when not one
    #: line carries money, which is not the same as 0 - an order nobody priced is not an
    #: order worth nothing, and 15,000 of the absorbed rows are exactly that.
    total_amount: Optional[Decimal] = None
    #: What the order says, and how much of it is still open. Both, because a total that
    #: silently means "outstanding" reads as an empty order once everything has shipped.
    line_count: int = 0
    open_line_count: int = 0
    lines: List[SalesOrderLine]
    #: Where the order came from: `inquiry` (the Order Inquiry sheet created it), `upload`
    #: (CS's outstanding extract), or `manual`. It decides who may edit the figures.
    source: str = "manual"
    #: The project the sheet named when no customer of that name existed.
    internal_note: Optional[str] = None
    #: The human-readable project name (PLAN-so-project-label.md), resolved from whichever
    #: of the four sources ranked highest. Never a UUID; `None` when nothing has ever named
    #: one for this order.
    project_label: Optional[str] = None
    #: Which rule wrote `project_label` - `inquiry` / `note` / `ref` / `delivery`. Read
    #: alongside the label as a muted word (`Inquiry sheet` / `Note` / `AutoCount ref` /
    #: `Delivery address`), never shown alone.
    project_label_source: Optional[str] = None
    #: Every distinct location its lines ship from. Plural: one order can land in two.
    stock_locations: List[str] = Field(default_factory=list)
    #: The planning class this order was classified into (`project` / `retail`), or `None`
    #: when nobody has ever said. See `app.services.scm.demand_class`. Distinct from
    #: `order_type_label`, which names the ERP document type and is blank on almost every
    #: row - this is what the list's "Type" column actually shows.
    demand_class: Optional[str] = None
    #: AutoCount's `Transferable` flag (SO-TRANSFERABLE): `False` is an order AutoCount has
    #: not confirmed for the queue yet, which Stock Debt and the fulfilment ladder do not
    #: count; `None` means the source never said (counted). Read-only - AutoCount owns it and
    #: `SalesOrderUpdate` has no such input.
    is_transferable: Optional[bool] = None
    #: The purchase orders its lines wait on, each with whether the pairing is resolved.
    #: Present on the LIST (attached in one query per page); absent on a single read.
    linked_purchase_orders: List[LinkedPurchaseOrder] = Field(default_factory=list)
    awaiting_purchase_orders: int = 0
    #: The order inquiries raised against this order, on BOTH the list and the single read.
    #: Empty for an order nobody has planned - the business sees sales orders and order
    #: inquiries and nothing between them, so this is where an order says what has been
    #: done about it.
    order_inquiries: List[SalesOrderInquiry] = Field(default_factory=list)
    #: The PENDING planning-change batch this order is in (AC-P3-1), when a re-uploaded
    #: book moved one of its planned lines and nobody has applied the change yet. Present
    #: on the LIST, which is where the Changed badge is; `None` on nearly every order.
    planning_change_batch_id: Optional[str] = None
    #: How much of this order anybody has DECIDED, on BOTH the list and the single read.
    #:
    #: `plannable_lines` is how many of its lines the fulfilment board would admit - not
    #: cancelled, not marked no purchase needed, asking for something - and `planned_lines`
    #: how many of those an active supply decision or a live order inquiry row already
    #: settles. Counted by the board's own predicates, so the pill on the list and the board
    #: the reader opens next cannot disagree.
    #:
    #: NOT the delivery question. A Completed order whose stock shipped with nothing behind
    #: it reads 0 of 3, which is the whole point: it is the order nobody planned.
    #:
    #: Declared here because `response_model` silently drops what it does not declare - a
    #: field the service computes and the schema forgets reaches the screen as `undefined`.
    planned_lines: int = 0
    plannable_lines: int = 0
    created_at: str


class LinkedPurchaseOrder(BaseModel):
    """A pairing this sales order's lines claim, and whether both sides are present."""

    po_number: str
    item_code: Optional[str] = None
    resolved: bool = False


class SalesOrderInquiry(BaseModel):
    """One order inquiry raised against this sales order.

    By NUMBER (`OI-2609-0001`) for what the screen PRINTS - but `id` is what it now
    LINKS with (S3, AC-LK-01, `PLAN-oi-header-list-detail.md`): the header detail page
    is addressed by id in the URL, never shown, the same as every other `rowHref` in
    this product. `rows_placed` against `rows_total` is how far purchasing has got with
    it - the one fact a buyer looking at the sales order actually wants, and the reason
    the column is not just a list of numbers.
    """

    id: Optional[str] = None
    inquiry_no: Optional[str] = None
    state: str
    raised_at: Optional[str] = None
    #: Who raised it, resolved to a person's name. Null when nobody was recorded.
    raised_by_name: Optional[str] = None
    rows_total: int = 0
    rows_placed: int = 0


class SalesOrderLineInput(BaseModel):
    #: The existing line's id, when the caller has one (e.g. an in-place qty edit on an
    #: already-saved order). Optional - a brand-new line naturally has none yet, and the FE
    #: form used to create/replace a whole order never has one either. When present, `update`
    #: matches on it FIRST rather than falling back to SKU, so an edited line keeps its id
    #: (and therefore `qty_delivered` / `source_system` / any reconciled link) instead of
    #: being read as "delete this one, insert a new one".
    id: Optional[str] = None
    sku: str
    #: `ge=0`, not `gt=0` - a held line settling to 0 on a manual edit is new vocabulary
    #: this schema must accept (`documentation/plans/scm/PLAN-scm-change-management-one-
    #: engine.md`, Slice A rule 5, "qty-to-zero as cancelled"): the service reads it as a
    #: removal (`SalesOrderService._upsert_lines`/`_propagate_planning_change`), the same
    #: as the line being dropped from the payload entirely, never as a `qty_down`. Shared
    #: by `SalesOrderFormData` (create) too, but create is NOT left open at 0 - its own
    #: `_lines_are_all_positive` field_validator below (review round, R-S4) enforces a
    #: positive opening qty there instead of here, since a brand-new order has no line to
    #: settle.
    qty_ordered: float = Field(..., ge=0)
    #: Optional[str], read via `model_fields_set` (not `is not None`) in `_upsert_lines` -
    #: an omitted key leaves the line's stored override alone (falling back to the
    #: product's base UOM on read), while an explicit `null`/`""` clears the override.
    uom: Optional[str] = Field(None, max_length=100)
    #: Which warehouse this line ships from, by CODE - never the UUID, matching `sku` /
    #: `customer_code` elsewhere on this schema. Same `model_fields_set` semantics as
    #: `uom`: omitted leaves the line's warehouse alone, sent (including `null`/`""`)
    #: clears it, a known code resolves and sets it. An unknown code is a 404.
    warehouse_code: Optional[str] = None
    #: When this line's quantity is due - the SAME column the detail page shows and labels
    #: "Delivery date" (`sales_order_lines.required_date`). The table also carries a
    #: never-mapped `delivery_date` column that nothing reads or shows and is deliberately
    #: left that way here, so this edits the column the FE already displays under that
    #: label. ISO `yyyy-mm-dd`; same `model_fields_set` semantics as `uom` / `warehouse_code`.
    required_date: Optional[str] = None
    #: What the customer pays, and what came off it. Same `model_fields_set` semantics as
    #: `uom` above: a qty-only edit that never sends these keys must not wipe a price the
    #: book imported, while an explicit `null` clears the figure. `Decimal`, so a price the
    #: browser sent as `88.5` is stored as 88.50 rather than a float's near-miss.
    unit_price: Optional[Decimal] = None
    discount: Optional[Decimal] = None


class SalesOrderFormData(BaseModel):
    order_type: str
    customer_code: str
    priority: str = "normal"
    requested_delivery_date: Optional[str] = None
    #: Who sold it. Optional - most manual creates name no agent - applied as given.
    sales_agent_id: Optional[str] = None
    lines: List[SalesOrderLineInput] = Field(..., min_length=1)

    #: A brand-new order can never OPEN with a zero-qty line (review round, R-S4) - `0` is
    #: new vocabulary only an EDIT reads as "settle this held line" (AC-A4); creating one
    #: at 0 has no line to settle. `SalesOrderLineInput.qty_ordered` itself stays `ge=0` -
    #: it is shared with `SalesOrderUpdate` - so the positive-on-create rule lives here,
    #: on the smaller (create-only) schema, rather than splitting the shared line model.
    @field_validator("lines")
    @classmethod
    def _lines_are_all_positive(
        cls, lines: List[SalesOrderLineInput]
    ) -> List[SalesOrderLineInput]:
        for line in lines:
            if line.qty_ordered <= 0:
                raise ValueError(
                    f"Line {line.sku}: qty_ordered must be greater than 0 on a new order"
                )
        return lines


class SalesOrderUpdate(BaseModel):
    #: The ERP document type. Kept for anything that still sends it (n8n, the older form):
    #: it is written as given AND, when it says something the vocabulary recognises, it
    #: rewrites `demand_class` from it. The detail screen no longer uses it - see below.
    order_type: Optional[str] = None
    #: The planning class, under its own name. The detail page RENDERS this column
    #: (project / retail / Unclassified) and used to EDIT `order_type`, which is NULL on
    #: 96% of this book - so the value shown and the value written were different columns
    #: and the round trip could not be honest. Blank (omitted, `null` or `""`) leaves the
    #: stored classification alone rather than clearing it: "nobody said" is not a class,
    #: and a header edit must not un-rank an order as a side effect. A word outside the
    #: closed vocabulary is a 400 naming the words the fulfilment policy can weigh.
    demand_class: Optional[str] = None
    customer_code: Optional[str] = None
    priority: Optional[str] = None
    #: When the order was raised. Correctable because the absorbed book got it from a
    #: spreadsheet and the demand trend reads 24 months of this column. ISO `yyyy-mm-dd`.
    order_date: Optional[str] = None
    requested_delivery_date: Optional[str] = None
    #: Optional[str], but read via `model_fields_set` in the service rather than a plain
    #: `is not None` check: a field the caller never sent must leave the stored agent alone,
    #: while one sent as an explicit `null` (or `""`) must CLEAR it. A plain `is not None`
    #: check cannot tell those two apart - both arrive as `None`.
    sales_agent_id: Optional[str] = None
    lines: Optional[List[SalesOrderLineInput]] = None


class SalesOrderPagination(BaseModel):
    total: int
    page: int


class SalesOrderListResponse(BaseModel):
    data: List[SalesOrder]
    empty: bool
    pagination: SalesOrderPagination


class CreateDoResponse(BaseModel):
    sales_order: SalesOrder
    do_number: str


class ResetPlanningRequest(BaseModel):
    """Also put the lines a planning-change upload moved back to before the first upload."""
    rewind_book: bool = False


class ResetPlanningResponse(BaseModel):
    so_number: str
    planned: bool
    removed: dict[str, int]


class SalesAgentOption(BaseModel):
    """One `sales_agents` row, for the Agent filter and the detail page's Agent select.

    Served under `scm.dashboard.view` rather than `master_data.sales_agents.view` - the
    master's own CRUD permission - because a purchasing/SCM operator role can hold the
    former without the latter, and this route only ever reads.
    """

    id: str
    sales_agent: str
    person_label: Optional[str] = None
    location_group: Optional[str] = None


# --- purchase orders --------------------------------------------------------

class PurchaseOrderLine(BaseModel):
    id: str
    sku: str
    product_name: str
    qty_ordered: float
    qty_received: float
    #: What is still to ARRIVE on this line: `ordered - received`, floored at 0, and **0 on a
    #: CLOSED line whatever those two say**. A book re-upload closes a line by absence without
    #: knowing what arrived, so `qty_received` stays 0 and the subtraction alone would report
    #: the whole quantity as still coming on an order that is done. Distinct from the header's
    #: `open_qty`, which is SUPPLY and ignores receipts.
    outstanding_qty: float = 0
    #: The line's own override when the book stated one, otherwise the product's base unit.
    uom: str
    #: What we pay for this line, and what the supplier's document states about it.
    #: `Decimal`, never `float`: money read back through a float is how RM 985.00 becomes
    #: 984.9999999. All three are `None` when the file said nothing - a 0 discount claims a
    #: discount of nothing was given, and a 0 total claims the goods were free.
    #:
    #: `unit_price` is the `unit_cost` COLUMN under the name the sales-order screen uses for
    #: the same fact. The two grids are one click apart in the same menu, and two names for
    #: one figure is how two screens start disagreeing about the same number.
    unit_price: Optional[Decimal] = None
    discount: Optional[Decimal] = None
    line_total: Optional[Decimal] = None
    #: The currency the three figures above are in. The book is mostly USD.
    currency: Optional[str] = None
    #: Where this line lands. Per line: one order routinely arrives into two locations.
    #: The serializer has emitted this since the header Warehouse field was dropped, and
    #: `response_model` silently discarded it for want of this declaration.
    warehouse_code: Optional[str] = None
    #: `open` or otherwise. A line that has left the order book is not incoming supply
    #: however much quantity it still shows. Emitted by the serializer, likewise dropped.
    line_status: str = "open"
    #: When this line's goods are due. Per line, for the same reason as the location.
    expected_date: Optional[str] = None
    #: The AutoCount book's own SO linkage for this line (Slice A), read off the line's own
    #: `from_so_line_ref` column and resolved to a number through
    #: `order_link_service.book_so_numbers_by_ref`. ONE sales order, because the column is
    #: one value: the book records a purchase line as having been raised for a single
    #: sales-order line. Distinct from the allocations panel, which answers who RESERVED
    #: the line through our own order-inquiry flow.
    #:
    #: `None` when the book named a sales order this CRM does not hold, in which case
    #: `book_so_unresolved` is True - see below. The raw ref is NEVER serialized: it is a
    #: machine key (`AED_SORENTO:<DocKey>:<DtlKey>`), and no machine identifier reaches the
    #: UI, the same reason `from_po_line_ref` is not printed either.
    book_so_number: Optional[str] = None
    #: THREE wire values, not two (review of PR #764, F2). `False` - a ref is present and
    #: resolved, or there is no ref at all. `True` - a ref is present and the CRM does not
    #: hold the sales order it names ("linked, not held"). `None` - this response did NOT
    #: resolve the linkage at all: the PO list route passes
    #: `order_link_service.BOOK_SO_NOT_RESOLVED` because no list consumer reads the field
    #: and resolving it per page would be a full `sales_orders` scan per search keystroke.
    #: The detail/update routes (`get_one`, `update`) always resolve and never emit `None`.
    book_so_unresolved: Optional[bool] = None


class PurchaseOrderSpoLanding(BaseModel):
    """Where an SPO take's goods are landing, and how much at each (AC-G7)."""

    warehouse_code: str
    qty: float = 0.0


class PurchaseOrderPlacement(BaseModel):
    """One placement sitting on a purchase-order line (section 3.G, AC-G1; AC-G7).

    Every field is a NAME. The inquiry by its number, the sales order by its document
    number, the customer and the agent by the labels the order-inquiry worklist already
    prints for them - a UUID on this panel would tell the buyer nothing they could act on.

    TWO KINDS. `inquiry` is somebody's order inquiry linked to this line. `spo` is a CRM SPO
    that PULLED from it: the goods are on a container already, so the fields describing a
    customer are null and the ones describing the container are not.

    Those SPO fields are declared HERE and not only built in the service, because
    `response_model` drops what it does not declare - and it did, silently: the panel
    rendered the qty and printed "-" for the SPO number and the container beside it.
    """

    #: `inquiry` (the original panel) or `spo` (a CRM SPO that pulled from this line).
    kind: str = "inquiry"
    #: The SPO that took this quantity. `spo` rows only.
    spo_number: Optional[str] = None
    #: The SPO's own `purchase_orders.id` (L2, review round). Never rendered as text - the FE
    #: uses it to make `spo_number` a link to `/scm/purchase-orders/{id}`. `spo` rows only;
    #: `None` on an `inquiry` row, which has no purchase order of its own to point at.
    purchase_order_id: Optional[str] = None
    #: The container it is on, by container number or shipment number. `spo` rows only.
    packing_list: Optional[str] = None
    #: Where it lands, and how much at each. `spo` rows only.
    warehouses: List[PurchaseOrderSpoLanding] = Field(default_factory=list)
    #: When the container is due. `spo` rows only.
    arrival_date: Optional[str] = None
    #: `OI-000006`. Null only on a row raised before the numbering stamp existed.
    inquiry_no: Optional[str] = None
    #: The sales order this quantity is owed to - its AutoCount number where it has one.
    so_number: Optional[str] = None
    customer: Optional[str] = None
    #: Who sold it, by person label, falling back to the agent code.
    agent: Optional[str] = None
    qty: float = 0.0
    #: Where the demand needs it: `order_inquiry_rows.stock_location`.
    needed_at: Optional[str] = None
    #: True when `needed_at` is not the PO line's own location - the PO line says DC1 and
    #: the demand is at BRW-BB. That difference IS the split instruction for AutoCount,
    #: which is why it is a mark on the row rather than a filter that hides it.
    location_differs: bool = False


class PurchaseOrderDedication(BaseModel):
    """One sales order's claim on a purchase-order line (G7 dedication, AC-6.16)."""

    #: The sales order document number - never an id.
    so_number: str
    #: What the claiming sales order line still owes in full, its LIVE outstanding.
    reserved: float = 0.0
    #: What of that this document is holding, and therefore what comes off `free`: the
    #: claim's share after everything already placed under it and everything reserved on
    #: an earlier document of the same sales order line (B2).
    unplaced: float = 0.0
    #: Which feed stated it: the purchase book (`po_history` / `po_upload`), the buy that
    #: created the line (`crm_supply`), a person in the Link dialog (`manual`), or the
    #: placement's own audit row (`order_inquiry`).
    source: Optional[str] = None


class PurchaseOrderLineAllocation(BaseModel):
    """One purchase-order line's occupancy: the three figures, then who is on it (AC-G1)."""

    #: Which line this belongs to, matched against `PurchaseOrderLine.id`.
    line_id: str
    sku: str
    #: The PO LINE's own location, so the panel can say what the demand differs from.
    warehouse_code: Optional[str] = None
    #: What is still to ARRIVE on the line - the same rule `PurchaseOrderLine` prints.
    outstanding: float = 0.0
    #: The sum of every live order-inquiry link on this line.
    allocated: float = 0.0
    #: `outstanding - allocated - what other sales orders' CLAIMS still reserve`, floored
    #: at 0 (G7 dedication, B2). A claim reserves its sales order line's still-unplaced
    #: need, capped at this document's capacity, so a line the AutoCount book dedicates
    #: elsewhere is not free to buy against even where nothing is linked to it yet. A line
    #: promised more than it has left is over-committed, which is a finding for the buyer,
    #: not a credit to spend twice.
    free: float = 0.0
    #: WHO the line is dedicated to, in sales-order date order - the book, the buy that
    #: created the line, or a person. Empty on an ordinary undedicated line. DECLARED
    #: HERE or `response_model` drops it on the way out and the panel never sees it.
    dedicated_to: List[PurchaseOrderDedication] = Field(default_factory=list)
    placements: List[PurchaseOrderPlacement] = Field(default_factory=list)


class SpoPlanPull(BaseModel):
    """One SOURCE purchase-order line a CRM SPO line pulled from (R1, AC-H5)."""

    purchase_order_id: Optional[str] = None
    po_number: Optional[str] = None
    po_line_label: Optional[str] = None
    qty: float = 0.0


class SpoPlanCover(BaseModel):
    """One retail sales-order line a CRM SPO line covers (R1, AC-H5)."""

    so_number: Optional[str] = None
    customer: Optional[str] = None
    qty: float = 0.0
    warehouse: Optional[str] = None


class SpoPlan(BaseModel):
    """The PO detail's Plan card (R1): a `crm_spo` order's own pulls/covers, read off its
    lines' `source_ref`. Declared here or `response_model` drops it on the way out."""

    pulls: List[SpoPlanPull] = Field(default_factory=list)
    covers: List[SpoPlanCover] = Field(default_factory=list)


class PurchaseOrder(BaseModel):
    id: str
    po_number: str
    supplier_code: str
    supplier_name: str
    warehouse_code: Optional[str] = None
    warehouse_name: Optional[str] = None
    status: str
    order_date: str
    expected_date: Optional[str] = None
    total_qty: float
    line_count: int
    #: What the PO still contributes as incoming supply, as distinct from what the order
    #: says. Zero on a fully-received or historical order, which is the point.
    open_qty: float = 0.0
    open_line_count: int = 0
    #: What is still to ARRIVE across the order, summed off the per-line rule above. Not the
    #: same figure as `open_qty`: that one is supply and counts a whole open line however much
    #: of it has already been received.
    outstanding_qty: float = 0.0
    #: What the order is WORTH, summed from its own lines: each line's stated `line_total`
    #: where it has one, otherwise `unit_cost * qty_ordered - discount`. `None` when not one
    #: line carries money, which is not the same as 0 - an order nobody priced is not an
    #: order worth nothing. The same rule the sales book's own `total_amount` follows.
    total_amount: Optional[Decimal] = None
    #: The currency the order is written in, from the header or, failing that, its first
    #: priced line. Blank on rows predating the book having more than one.
    currency: Optional[str] = None
    lines: List[PurchaseOrderLine]
    #: What order inquiries have OCCUPIED on this order, one entry per line carrying at
    #: least one placement (section 3.G). Empty on the LIST, which does not pay for the
    #: placement query per row; populated on the single read, which is where the panel is.
    allocations: List[PurchaseOrderLineAllocation] = Field(default_factory=list)
    #: The sum of every placement across the whole order - on the list as well as the
    #: single read (AC-G4). Declared here or `response_model` drops it, which is exactly
    #: how a carefully built figure goes out missing.
    allocated_qty: float = 0.0
    created_at: str
    # M4 Slice B - draft→confirm→GR flow
    is_on_order: bool = False
    source: str = "manual"           # recommendation | import | manual
    gr_reference: Optional[str] = None
    #: A `crm_spo` order's own pulls/covers (R1, AC-H7); `None` on every other order.
    spo_plan: Optional[SpoPlan] = None


class PurchaseOrderLineInput(BaseModel):
    #: The existing line's id, when the caller has one. Optional - a brand-new line has
    #: none yet. When present, `update` matches on it FIRST rather than falling back to SKU,
    #: so an edited line keeps its id (and therefore its `qty_received`, its `source_system`
    #: and any goods receipt pointing at it) instead of being read as "delete this one,
    #: insert a new one".
    id: Optional[str] = None
    sku: str
    qty_ordered: float = Field(..., gt=0)
    #: Optional[str], read via `model_fields_set` (not `is not None`) in `_upsert_lines` -
    #: an omitted key leaves the line's stored override alone (falling back to the product's
    #: base UOM on read), while an explicit `null`/`""` clears the override.
    uom: Optional[str] = Field(None, max_length=100)
    #: Which warehouse this line lands in, by CODE - never the UUID, matching `sku` /
    #: `supplier_code` elsewhere on this schema. Same `model_fields_set` semantics as `uom`.
    #: An unknown code is a 404.
    warehouse_code: Optional[str] = None
    #: When this line's goods are due. ISO `yyyy-mm-dd`; same semantics again.
    expected_date: Optional[str] = None
    #: What we pay, and what came off it. Same `model_fields_set` semantics as `uom` above:
    #: a qty-only edit that never sends these keys must not wipe a cost the book imported,
    #: while an explicit `null` clears the figure. `unit_price` writes the `unit_cost`
    #: column - see `PurchaseOrderLine`. `line_total` is deliberately NOT writable: it is
    #: what the supplier's document charged.
    unit_price: Optional[Decimal] = None
    discount: Optional[Decimal] = None


class PurchaseOrderUpdate(BaseModel):
    #: When the order was raised (`purchase_orders.issue_date`), under the name the screen
    #: shows. Correctable because the imported book got it from a spreadsheet and
    #: `scm.receipt_lead_v` measures the supplier's lead time from this column, so a wrong
    #: one skews every safety stock computed from it. ISO `yyyy-mm-dd`.
    order_date: Optional[str] = None
    #: When the goods are expected. Read via `model_fields_set`: sent as `null` it CLEARS.
    expected_date: Optional[str] = None
    #: The supplier, by code. An unknown code is a 404 rather than a silent unlink.
    supplier_code: Optional[str] = None
    lines: Optional[List[PurchaseOrderLineInput]] = None


class SupplierOption(BaseModel):
    """One `suppliers` row, for the detail page's Supplier select.

    Served under `scm.dashboard.view` rather than the procurement master's own permission,
    for the same reason `SalesAgentOption` is: a purchasing/SCM operator role can hold the
    former without the latter, and this route only ever reads.
    """

    supplier_code: str
    supplier_name: str


class PurchaseOrderPagination(BaseModel):
    total: int
    page: int


class ProductLastCost(BaseModel):
    """What we last paid for a SKU, and where to check it.

    `unit_cost` of 0 is a price OF zero; the ABSENCE of this whole block is what "we have
    never bought it" looks like. Keeping those apart is the point of the block existing.
    """

    unit_cost: float
    currency: Optional[str] = None
    po_number: str
    issue_date: Optional[str] = None
    supplier_name: Optional[str] = None


class PurchaseOrderListResponse(BaseModel):
    data: List[PurchaseOrder]
    empty: bool
    pagination: PurchaseOrderPagination
    # Present only when the caller narrowed the list to one product. A field missing from
    # this model is DROPPED from the response however carefully the service builds it -
    # which is exactly how this one went out silently the first time.
    product_cost: Optional[ProductLastCost] = None
