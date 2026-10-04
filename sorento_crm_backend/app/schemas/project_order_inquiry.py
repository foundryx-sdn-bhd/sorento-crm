"""Order inquiry schemas (P10, AC-I1 to AC-I7).

A row is one instruction to purchasing. It carries the sales order NUMBER rather than its
id, the item CODE rather than the product id, and the warehouse CODE rather than the
warehouse id, so nothing on this screen is a UUID a person has to resolve for themselves.

``remark`` is the same field the client's own spreadsheet calls REMARK: the verb in their
spelling, or the SPO reference itself for a row already on the water. It ships beside the
raw ``verb`` so the screen can colour by verb while printing what purchasing reads.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.services.uuid_path_param import UUID_PATTERN

#: The longest free-text search string the worklist routes accept
#: (`api/v1/projects/order_inquiries.py` imports this rather than retyping it, so the
#: list route and `AcknowledgeFilter` below share one cap).
WORKLIST_QUERY_MAX_LENGTH = 200
#: The same cap on every other free-text filter (location, PO number, SPO number, a
#: matrix cell's key). They reach an `ilike` or an equality over a joined query, and a
#: megabyte of "x" is not a search anybody typed.
WORKLIST_FILTER_MAX_LENGTH = 200

#: The closed state set the worklist list route accepts on `state` - shared so
#: `AcknowledgeFilter.state` cannot name a value the list route itself would refuse.
WorklistState = Literal["raised", "partly_linked", "actioned", "cancelled", "placed"]


class OrderInquiryBundledWithOut(BaseModel):
    """PLAN-scm-supplied-with-companions.md S5. `row_id` addresses the ANCHOR row (the
    rule's first matching item, plan 3.2); `item_codes` names every item the rule
    requires, in rule order - length 1 for the common case, 2+ for a pair rule (SC-RL
    with X + Y). The UI never says "host" (UAC D10): one item names its own code, two
    or more read "N items", and the lightbox is where `item_codes` is shown in full."""

    row_id: str
    item_code: Optional[str] = None
    item_codes: List[str] = []
    #: The anchor row's OWN coverage, e.g. "1 of 1" - resolved server-side (review round
    #: 1 item 8) so the cell never has to scan a page's own loaded rows for a match that
    #: is only ever right when the anchor happens to be on the SAME page. Null when the
    #: anchor has no links of its own (nothing placed for it yet); the reader's own
    #: "Not found (new order)" fallback covers that case.
    anchor_headline: Optional[str] = None


class OrderInquiryBundledHostChangeOut(BaseModel):
    """PLAN-oi-bundled-row-host-change.md. One HOST's own change, read from that host's
    own live row at display time - never written onto the companion row itself (owner
    ruling: "it comes with the X and Y, so it should follow them, to have the same
    delay"). `qty`/`delivery_date`/`previous_qty`/`previous_delivery_date` are all null
    when the host has no live row of its own on the same order inquiry header."""

    item_code: str
    qty: Optional[str] = None
    delivery_date: Optional[date] = None
    previous_qty: Optional[str] = None
    previous_delivery_date: Optional[date] = None


class OrderInquiryLinkOut(BaseModel):
    """One placement on an order inquiry row (`projects.order_inquiry_links`, AC-I5).

    A row keeps its full quantity and carries a list of these, so "where is this linked" is
    answered once, by the ONE reader
    (`ProjectOrderInquiryService.links_for_rows`), for the worklist, the per-project list
    and the SCM sales-order detail alike.

    Everything here is what a person reads. `document` is the link's own copy of the
    number, which is why it survives the line it named being re-imported under a new id;
    `line_label` is `L3` only when the book numbered the line, and absent rather than
    invented when it did not - the LOCATION is what identifies the line then, and it is a
    fact. `po_id` addresses the PO popover and is null on an SPO link, because there is no
    purchase order to open.
    """

    id: str
    #: `po` or `spo`. EITHER on any linkable row since R5 (27 Aug,
    #: `PLAN-scm-oi-draft-links.md`): SPO first, then PO. It was the order back alone
    #: under the 25 Aug rule, which no longer holds.
    kind: str
    document: Optional[str] = None
    line_label: Optional[str] = None
    qty: str
    location: Optional[str] = None
    issue_date: Optional[date] = None
    expected_date: Optional[date] = None
    #: Q5's location fit, 1 to 5. Never a filter, only a rank - a link outside tier 1 is
    #: the split instruction the buyer keys into AutoCount, not a mistake.
    tier: Optional[int] = None
    #: The document arrives AFTER the row's own required date (AC-P3-7). Stated, never
    #: acted on: purchasing decides whether a late document is still the answer, and
    #: unlinking it here would take away the only cover the row has. Derived from the two
    #: dates rather than stored, so it can never go stale against either of them.
    late: bool = False
    #: HOW late, in whole days between the row's own delivery date and the document's
    #: expected date. `None` when the document is not late, so the column has nothing to
    #: print rather than a zero it would have to read as "on time" (AC-D17). Derived
    #: beside `late` from the same two dates, never stored.
    late_days: Optional[int] = None
    #: The document this link names is FULLY received (`PLAN-oi-replan-received-links.md`
    #: S1, AC-RL-17): a PO line whose `qty_received >= qty_ordered` or `line_status =
    #: 'closed'`, or an SPO allocation that fails `spo_supply.open_incoming_clauses()`.
    #: Goods that have landed, not a promise still in transit.
    received: bool = False
    #: How much of THIS link's own line has been received, stated even when `received`
    #: itself is false - a partly received document says the figure too.
    received_qty: Optional[str] = None
    auto: bool = False
    linked_at: Optional[datetime] = None
    #: WHO linked it, by name. Null on a cascade link, which nobody did by hand.
    linked_by_name: Optional[str] = None
    po_id: Optional[str] = None
    #: Issue #1215 point 2 - `links_for_rows` has always computed this (the candidate
    #: walk's own target id), but `response_model` silently drops a field it has not
    #: been told about (same lesson as `ack_state` elsewhere) and this one never was.
    #: Needed so the Lines tab / PO lightbox can highlight the exact line this link
    #: sits on. Null on an SPO link.
    po_line_id: Optional[str] = None
    #: R15 (owner rulings, 25 Sep 2026, hand test on stack C): the same gap as
    #: `po_line_id` above, mirrored for the other book - `links_for_rows` has always
    #: computed this (the link's own target), but it never reached the wire. Needed so
    #: the SPO lightbox can highlight the exact allocation line this link sits on, the
    #: same way `po_line_id` already does for the PO lightbox. Null on a PO-kind link.
    spo_allocation_id: Optional[str] = None
    #: R17 (owner rulings, 25 Sep 2026): the purchase order an SPO link's allocation
    #: draws down (`SPOAllocation.po_line_id` traced to its own PO header), so the Lines
    #: tab's "<number> via SPO" cell can open that PO directly rather than guessing by
    #: number. A different question from `po_id` above, which addresses THIS link's own
    #: document. Null on a PO-kind link and on an SPO allocation with no resolved supply
    #: PO line (`links_for_rows`' own `purchase_order_id`, review round 1's L4 item,
    #: which never reached the wire either).
    purchase_order_id: Optional[str] = None
    #: The purchase order an SPO link's allocation was raised FROM, per AutoCount's own
    #: statement (`SPOAllocation.from_po_number`, migration 493 / contract 2.2) - a
    #: different question from `po_id` above, which addresses this link's OWN document.
    #: Plain text, never a link. Null on a `po`-kind link and on an SPO the book named no
    #: source for. Never `from_po_line_ref` - that is a resolver key, not a thing a buyer
    #: reads, and it is deliberately never sent.
    source_po_number: Optional[str] = None
    #: S5 (R-E, `PLAN-scm-oi-worklist-excel-parity.md`): a SYNTHETIC `spo`-kind entry -
    #: never written, never addressable - for a PO link whose PO has an open SPO
    #: allocation for the same product. The SPO column shows it marked "via PO".
    derived: bool = False
    #: The mirror: this `po`-kind entry's `source_po_number` is itself read off an SPO
    #: link (never a link this system made independently), so the PO column marks it
    #: "via SPO".
    derived_po: bool = False
    #: S1b (`PLAN-oi-replan-received-links.md`, AC-RL-20 to AC-RL-24, 17 Sep rulings): a
    #: concrete instruction, never a reason - `{"kind": "reallocate", "candidates":
    #: [{"inquiry_no", "item_code", "so_number", "delivery_date", "open_qty"}, ...]}`
    #: naming EVERY other linkable row of the same product with open need, delivery
    #: date ascending then open need descending (the first is the suggested target),
    #: or `{"kind": "unlink"}` when there is none. Null on a received link or one still
    #: inside the product's lead-time window. Nothing is written from it - purchasing
    #: acts in AutoCount, S5 follows.
    suggestion: Optional[Dict[str, Any]] = None


class OrderInquirySuggestedLinkOut(BaseModel):
    """One guess the cascade walk made, never a placement
    (`PLAN-oi-links-autocount-truth-24sep.md` 3.5, AC-LT-33) - kept off `links` above,
    which carries real links only. Same vocabulary as `OrderInquiryLinkOut` where the
    two overlap, minus everything only a real link has (no `id` to unlink by, no
    `linked_by_name`, no `received`): a suggestion is not purchasing's word and there
    is nothing on it to act on directly."""

    #: `po` or `spo`.
    kind: str
    document: Optional[str] = None
    po_id: Optional[str] = None
    po_line_id: Optional[str] = None
    spo_allocation_id: Optional[str] = None
    location: Optional[str] = None
    qty: str
    expected_date: Optional[date] = None
    late_days: Optional[int] = None
    #: Why the walk offered this - `raise`, `worklist`, `link_now`, `acknowledge`,
    #: `po_confirm`, `decision_confirm` - the same trigger vocabulary a real link's
    #: `auto` note already carries.
    trigger: Optional[str] = None


class OrderInquiryRowOut(BaseModel):
    id: str
    order_inquiry_id: str
    so_line_id: Optional[str] = None
    project_sales_order_id: Optional[str] = None
    # AC-B6-7 (`PLAN-board-oi-mechanical-22sep.md`, S6): the deep-link ids the "SO line"
    # column resolves to `/scm/sales-orders/<sales_order_id>?tab=lines&line=<core_line_id>`
    # - `sales_order_id` is the CORE `sales_orders.id`, `core_line_id` the mirror's own
    # `core_sales_order_line_id`. Both null when the mirror has no core line yet.
    # `response_model` drops a field it has not been told about (same lesson as
    # `ack_state` above), so both are declared here even though `serialize_rows` already
    # reads them.
    sales_order_id: Optional[str] = None
    core_line_id: Optional[str] = None
    sales_order_ref: Optional[str] = None
    # AC-D06: the Project SO reference, its line number and the decision revision the Buy
    # came from. Absent on an amendment exception row, which no revision decided.
    project_so_ref: Optional[str] = None
    line_no: Optional[int] = None
    decision_revision: Optional[int] = None
    so_date: Optional[datetime] = None
    project_customer: Optional[str] = None
    is_amendment: bool = False

    item_code: Optional[str] = None
    qty: str
    delivery_date: Optional[date] = None
    # Empty when no allocation has been confirmed yet (AC-H5). Never defaulted.
    stock_location: Optional[str] = None
    verb: str
    remark: Optional[str] = None
    spo_ref: Optional[str] = None
    covered_by: Optional[str] = None
    note: Optional[str] = None
    # The FIRST link's document and line, kept as the one-word display the older readers
    # print. The TRUTH is `links` below: a row may sit on two lines of one purchase order
    # and on an SPO allocation at the same time, and a single column cannot say that.
    po_ref: Optional[str] = None
    po_line_id: Optional[str] = None
    #: The document CS NAMED for an order back. Not a link - it is what the walk tries
    #: FIRST, and a document this system does not hold is recorded rather than refused.
    cited_document: Optional[str] = None
    #: Every document this row's quantity sits on, oldest link first (AC-I5).
    links: List[OrderInquiryLinkOut] = []
    #: AC-LT-33: the cascade's own guesses, never a placement - kept off `links`
    #: above, which carries nothing suggested.
    suggested_links: List[OrderInquirySuggestedLinkOut] = []
    #: The sum of `links[].qty` for the REAL links only - `links` also carries synthetic
    #: "via PO" entries (`derived: true`) for a linked PO's own open SPO allocations,
    #: which never wrote an `order_inquiry_links` row and are excluded from this sum.
    #: `qty - linked_qty` is what still flows to reorder planning, and is exactly what
    #: `scm.committed_v` now nets (migration 422).
    linked_qty: str = "0"
    #: PLAN-scm-supplied-with-companions.md S5. `bundled_qty` never exceeds
    #: `qty - linked_qty`; `bundled_with` is null on an un-bundled row. Both declared
    #: here because `response_model` drops a field it has not been told about
    #: (same lesson as `ack_state` below) - `serialize_rows` already computes them for
    #: this schema's own route (`/projects/{project_id}/order-inquiry-rows`) and they
    #: were silently vanishing on the wire before this.
    bundled_qty: str = "0"
    bundled_with: Optional[OrderInquiryBundledWithOut] = None
    # Whether this row has anywhere to link to at all (the captain, 20 Aug: a "Link PO"
    # offer with nothing behind it reads as a bug, not an empty state). Verb AND product,
    # not product alone: EVERY linkable verb may link to an `spo_allocations` row as well
    # as to a purchase order line (R5, 27 Aug - SPO first, then PO), so a flag that only
    # looked at purchase orders hid the Link action on rows that had open incoming stock
    # waiting for them. Computed with the SAME predicate `po-candidates` answers, so the
    # flag and the dialog can never disagree.
    has_link_candidate: bool = False

    state: str
    actioned_at: Optional[datetime] = None
    actioned_by_name: Optional[str] = None
    created_at: Optional[datetime] = None

    #: The HANDSHAKE (`PLAN-scm-oi-handshake.md`), beside `state` and never merged with
    #: it: `awaiting`, `acknowledged`, `changed` or `rejected`. Every one of the columns
    #: below is declared here because `response_model` silently drops a field it has not
    #: been told about, and the whole screen reads off them.
    ack_state: str = "awaiting"
    acknowledged_by_name: Optional[str] = None
    acknowledged_at: Optional[datetime] = None
    rejected_by_name: Optional[str] = None
    rejected_at: Optional[datetime] = None
    rejected_reason: Optional[str] = None
    #: When CS last amended a row purchasing had already acknowledged.
    changed_at: Optional[datetime] = None
    #: What the row said BEFORE that amendment - the Was half of the Was / Now table. Two
    #: figures rather than a sentence the screen has to parse back.
    previous_qty: Optional[str] = None
    previous_delivery_date: Optional[date] = None
    #: OI-PRODUCT-FOLLOW: the item code before a product change moved the row ("was X").
    previous_item_code: Optional[str] = None


class UploadJobScope(BaseModel):
    """What one upload from the Order Inquiries page wrote (AC-H13).

    Read off the finished import job, never recomputed: `product_ids` is what "Link now"
    is narrowed to, and `documents` is what the purchase-order list is filtered to when
    the buyer goes to look at them. `document_count` is the whole truth where the list is
    capped, so a caller can tell "these are all of them" from "these are the first 50".
    """

    job_id: str
    status: str
    #: The worker is done with it, whichever way it ended. The page offers its two next
    #: steps only then - linking against a book still being read links half of it.
    finished: bool
    job_type: Optional[str] = None
    filename: Optional[str] = None
    product_ids: List[str] = []
    documents: List[str] = []
    document_count: int = 0


class OrderInquiryDetail(BaseModel):
    id: str
    #: `OI-000001` - what a person calls this inquiry. Optional only so a record written
    #: before the column existed still reads; everything created since carries one.
    inquiry_no: Optional[str] = None
    project_sales_order_id: str
    amendment_id: Optional[str] = None
    state: str
    raised_at: Optional[datetime] = None
    # The purchasing task the rows are attached to (AC-I4).
    task_id: Optional[str] = None
    task_name: Optional[str] = None
    rows: List[OrderInquiryRowOut] = []


class OrderInquirySummary(BaseModel):
    total: int = 0
    raised: int = 0
    actioned: int = 0
    cancelled: int = 0


class OrderInquiryRaiseHistoryEntry(BaseModel):
    """One PRIOR raise of the same SO line under the same inquiry
    (PLAN-oi-worklist-split-customer-project.md) - the Raised at cell's own tooltip. A re-confirm
    cancels the old row and raises a fresh one, so this is where the first raise's own
    time and raiser still live once `raised_at` has moved on to the latest one."""

    raised_at: Optional[datetime] = None
    raised_by_name: Optional[str] = None


class OrderInquiryWorklistRow(BaseModel):
    """One instruction on purchasing's own list, in the spreadsheet's columns.

    Same vocabulary as the per-project row above - the sales order NUMBER, the item CODE,
    a quantity as a string - plus the three facts the cross-project view needs and the
    per-project one does not: which document to open (a core sales order for an adopted
    record, the project document for an authored one), who it is for when there is no
    project to name, and whether anybody has placed it yet.
    """

    id: str
    #: `OI-000123` - the number of the inquiry this row belongs to, off its own header.
    #: Optional only so a record written before the column existed still reads; every
    #: inquiry raised since carries one. The S/O no beside it does not answer the same
    #: question: an amendment raises a SECOND inquiry on the same sales order.
    inquiry_no: Optional[str] = None
    so_date: Optional[date] = None
    so_number: Optional[str] = None
    item_code: Optional[str] = None
    #: Addressing only, never rendered - two products on the live book share one item
    #: code, so a caller that keys a stock lookup off `item_code` risks the wrong one
    #: (`PLAN-oi-request-cs-reserve.md` section 6 item 1). Already selected by `_COLUMNS`
    #: (`Product.id.label("product_id")`); declared here because `response_model` drops
    #: what it is not told about.
    product_id: Optional[str] = None
    product_name: Optional[str] = None
    qty: str
    delivery_date: Optional[date] = None
    project_customer: Optional[str] = None
    # PLAN-oi-worklist-split-customer-project.md: `project_customer` above stays for the
    # export and search; the worklist screen itself prints these two split out into a
    # Customer column and a Project column, in that position. `project_title` carries the
    # PRE-ORDER note `project_customer` does, so a pre-order row still reads as one.
    customer_name: Optional[str] = None
    project_title: Optional[str] = None
    # Blank until the row traces to a placed purchase order. Never a guess at who would
    # supply it: purchasing reads a filled cell as a statement that an order exists.
    supplier: Optional[str] = None
    supplier_id: Optional[str] = None
    po_number: Optional[str] = None
    # The location the PO is placed for: the donor to order back for an order-back row,
    # the confirmed allocation's warehouse for a plan/confirmed row, otherwise the line's
    # own fulfilment location. Blank when neither is known.
    location: Optional[str] = None
    # What flows to reorder planning, for this row's own SO line (the captain, 20 Aug: "show
    # the quantity, quantity taken from PO, and the remaining quantity, cause this is what
    # flows to reorder planning"). `taken_from_po` sums every SIBLING placed ORDER row on the
    # same line - the PO no cell links to one PO, but a line may have been covered across
    # several; `remaining_open` sums every raised ORDER row on the line, which is exactly
    # `committed_v`'s confirmed leg (`verb='ORDER' AND state='raised'`) - what still counts as
    # demand to the reorder engine. On a raised row that includes itself. `0` for a row with
    # no `so_line_id` (an amendment exception row traces to none).
    #
    # Both figures are scoped to `verb='ORDER'` SIBLINGS, always - including for a row whose
    # OWN verb is not `ORDER` (the captain, 21 Aug: an ADVANCE row read "Taken from PO 432 /
    # Remaining 0", technically correct about its ORDER siblings, but read as "handled" next
    # to an unactioned date change of its own). The frontend mutes these two cells with an
    # honest per-verb label instead of a figure whenever `verb != 'ORDER'`
    # (`orderInquiryWorklist.ts`'s `flowExclusionLabel`) - this schema still always ships the
    # real ORDER-sibling numbers, so nothing here needs to change to keep that true.
    taken_from_po: str = "0"
    remaining_open: str = "0"
    # Same as `OrderInquiryRowOut.has_link_candidate` - whether this row has anywhere to
    # link to, computed the same way so the two listings that render "Link PO" can never
    # disagree with the dialog.
    has_link_candidate: bool = False
    # Who sold it (`sales_orders.sales_agent_id` -> `sales_agents`), read off the same core
    # sales order the SO DATE / S/O NO columns already join to. Null on an authored row
    # that reaches no core order and on one whose core order carries no agent.
    agent_code: Optional[str] = None
    agent_label: Optional[str] = None
    state: str
    raised_at: Optional[datetime] = None
    # WHO told purchasing to buy THIS ROW, by name: the confirmer of the supply revision
    # that raised it (`supply_decision_id` -> `so_supply_decisions.confirmed_by`), falling
    # back to the inquiry header's `raised_by` for an amendment-born row that has no
    # revision. Never the header for a row that HAS one: the header is re-stamped on every
    # reconfirm, so it would print the latest reconfirmer beside an older row's own clock.
    # Never the id either - the column is printed as it comes. Null when nobody was
    # recorded, or the user has since been removed.
    raised_by_name: Optional[str] = None
    # The Raised at cell's own tooltip (PLAN-oi-worklist-split-customer-project.md,
    # Slice 2): the CANCELLED predecessors on the same SO line, newest first - an open
    # sibling row is a second live instruction, not history (review round 1, blocker
    # B1). `[]` on a row with no SO line, or nothing prior. Never on the Excel export.
    raise_history: List[OrderInquiryRaiseHistoryEntry] = []
    # AC-DT-3 (`PLAN-oi-decision-trail-ui.md`): the actual `order_inquiry_raises` EVENT
    # this row traces to - the confirm or reconfirm that raised it, matched by timing
    # (the same inquiry's earliest event at or after this row's own `created_at`, minus
    # a one-second grace). Distinct from `raised_by_name`/`raised_at` above, which name
    # WHO currently owns the row rather than what raised it. `raise_event_kind` is
    # `"raised"` or `"reconfirmed"` (`OI_RAISE_RAISED`/`OI_RAISE_RECONFIRMED`), or null
    # when nothing matches - a row migrated before raises were recorded.
    raise_event_kind: Optional[str] = None
    raise_event_by_name: Optional[str] = None
    raise_event_at: Optional[datetime] = None
    verb: str
    note: Optional[str] = None

    # Addressing only, never rendered.
    project_id: Optional[str] = None
    project_sales_order_id: Optional[str] = None
    core_sales_order_id: Optional[str] = None
    # AC-B6-7 (`PLAN-board-oi-mechanical-22sep.md`, S6): the core LINE's own id, which the
    # "SO line" cell puts on
    # `/scm/sales-orders/<core_sales_order_id>?tab=lines&line=<core_line_id>` beside
    # `core_sales_order_id` above - the cell reads THAT one, so this row carries no second
    # name for the same sales order (review round, 22 Sep). `response_model` drops a field
    # it has not been told about, so this is declared here even though `_serialize`
    # already reads it. Null when the mirror has no core line.
    core_line_id: Optional[str] = None
    # Fix round (22 Sep): AutoCount's own line number, beside the id above - the S/O line
    # cell's own `SO402757 · L5` label (`orderInquirySoLineLabel`) reads this. Null when
    # the mirror has no core line (same as `core_line_id`).
    line_no: Optional[int] = None
    # `PLAN-oi-no-double-count-25sep.md` S1 (AC-ND-21): the sales order line's own Qty and
    # No., the numbers the sales order's Lines grid shows for that line - the OI detail's
    # SO Qty column. The core line's `qty_ordered` / `line_no`, else the mirror's own when
    # it has no core line; both null when the row names no sales order line at all.
    so_line_qty: Optional[str] = None
    so_line_no: Optional[int] = None
    # PR #1266 review S1: the row's mirror sales order line, the OI detail's fold key and
    # the header Lines count's own (G10). Unlike `core_line_id` it is there before
    # AutoCount reconciles the line. Null when the row names no sales order line.
    so_line_id: Optional[str] = None
    is_adopted: bool = False
    # The placed purchase order this row traces to (same coalesce the PO NO column reads),
    # so the "PO no" cell's popup can address `GET .../order-inquiries/po/{po_id}` without
    # a second lookup. Null on a row nobody has placed yet.
    po_id: Optional[str] = None
    #: Every document this row's quantity sits on (AC-I5), the SAME reader the per-project
    #: list and the SCM sales-order detail use. Empty on a row nobody has linked.
    links: List[OrderInquiryLinkOut] = []
    #: AC-LT-33: the cascade's own guesses, never a placement - kept off `links`
    #: above, which carries nothing suggested.
    suggested_links: List[OrderInquirySuggestedLinkOut] = []
    linked_qty: str = "0"
    #: The document CS cited on an order back, so the screen can say the walk honoured it.
    cited_document: Optional[str] = None
    #: PLAN-scm-supplied-with-companions.md S5. `bundled_qty` never exceeds
    #: `qty - linked_qty`; `bundled_with` is null on an un-bundled row. Both declared
    #: here because `response_model` drops a field it has not been told about
    #: (`test_order_inquiry_bundles.py::test_d7`).
    bundled_qty: str = "0"
    bundled_with: Optional[OrderInquiryBundledWithOut] = None
    #: PLAN-oi-bundled-row-host-change.md. One entry per host item code, in rule order,
    #: read from each host's own live row - null on a non-bundled row. Declared here for
    #: the same reason `bundled_with` is (`response_model` drops what it is not told
    #: about, `test_order_inquiry_worklist.py`).
    bundled_host_changes: Optional[List[OrderInquiryBundledHostChangeOut]] = None

    #: The HANDSHAKE (`PLAN-scm-oi-handshake.md`), beside `state` and never merged with
    #: it: `awaiting`, `acknowledged`, `changed` or `rejected`. Every one of the columns
    #: below is declared here because `response_model` silently drops a field it has not
    #: been told about, and the whole screen reads off them.
    ack_state: str = "awaiting"
    acknowledged_by_name: Optional[str] = None
    acknowledged_at: Optional[datetime] = None
    rejected_by_name: Optional[str] = None
    rejected_at: Optional[datetime] = None
    rejected_reason: Optional[str] = None
    #: When CS last amended a row purchasing had already acknowledged.
    changed_at: Optional[datetime] = None
    #: What the row said BEFORE that amendment - the Was half of the Was / Now table. Two
    #: figures rather than a sentence the screen has to parse back.
    previous_qty: Optional[str] = None
    previous_delivery_date: Optional[date] = None
    #: OI-PRODUCT-FOLLOW: the item code before a product change moved the row ("was X").
    previous_item_code: Optional[str] = None
    #: A replan met this row's only coverage already fully received and could not carry
    #: it forward (`PLAN-oi-replan-received-links.md` S2, AC-RL-16): its `qty`/
    #: `delivery_date`/`links` stand as history, and the fresh need is a separate row.
    #: Excluded from the Buy / Purchased / Incoming cards and from `taken_from_po` /
    #: `remaining_open` - declared here because `response_model` silently drops a field
    #: it has not been told about.
    redirected_to_pool: bool = False
    #: PLAN-oi-cancelled-line-used-confirm.md (AC-CL-1): true when the sales order line
    #: this row sits on has `line_status = cancelled`. Excluded from Buy only (AC-CL-4);
    #: Purchased/Incoming still count it when it holds a link. Declared here because
    #: `response_model` silently drops a field it has not been told about.
    line_cancelled: bool = False
    #: PLAN-oi-request-cs-reserve.md 3.5 (AC-RS-20): `requested` while an open reserve
    #: request row exists, `reserved` once something has actually been reserved (and no
    #: open request), `declined` when the latest answer was 0 (6e.4, AC-RS-78c), else
    #: null. Declared here because `response_model` silently drops a
    #: field it has not been told about.
    reserve_state: Optional[str] = None
    #: 3.4 (AC-RS-12): the sum of the row's reserve links - a THIRD figure beside
    #: `taken_from_po`/`remaining_open`, both of which already include it (they sum by
    #: `row_id` with no target filter).
    reserved_qty: str = "0"
    #: Round 4 (`PLAN-oi-request-cs-reserve.md` 6e.2): the OPEN reserve request row's
    #: own `qty_requested` for this row - "0" when `reserve_state` is not `requested`.
    #: The Lines grid's `Request to reserve N` pill and the tick's default stage both
    #: read N off this, not off a second lookup. Declared here because `response_model`
    #: silently drops a field it has not been told about.
    requested_qty: str = "0"


class OrderInquiryMonthTotal(BaseModel):
    month: str
    #: `JAN 26`, spelled the way their sheet tab is.
    label: str
    rows: int = 0
    qty: str = "0"


class OrderInquiryFacet(BaseModel):
    id: str
    label: str
    rows: int = 0


class OrderInquiryMatrixCell(BaseModel):
    """One cell of the Schedule matrix (S3, R-I second half): this axis value, by this
    date bucket, over every row the list itself would show for the same filters.

    `axis_key` is never rendered (no UUIDs in the UI); `axis_label` is. `period` is the
    ISO date the bucket STARTS on (week buckets start Monday, month/year on the first).
    `rows` is the row COUNT summed into the cell, not the rows themselves - a click drills
    down by asking the list for this cell's own axis + period, never by reading rows back
    out of this response.
    """

    axis_key: str
    axis_label: str
    period: date
    qty: str = "0"
    buy: str = "0"
    po: str = "0"
    spo: str = "0"
    rows: int = 0


class OrderInquiryMatrixResponse(BaseModel):
    """`{data: [...]}`, never paginated - the matrix's own contract has no page to ask
    for, and the old `ListResponse` shape's mandatory `pagination` would say so of a
    response that has none."""

    data: List[OrderInquiryMatrixCell] = []


class OrderInquiryStateCounts(BaseModel):
    raised: int = 0
    #: Some of the quantity is on documents and the rest is still demand (section 3.I).
    partly_linked: int = 0
    actioned: int = 0
    cancelled: int = 0
    #: Wholly covered by links. Stored as `placed`, read as "Linked" (AC-I1). Declared
    #: here because `response_model` silently drops a key it has not been told about, and
    #: the service builds this dict off the rows themselves rather than off a fixed list.
    placed: int = 0
    total: int = 0


class OrderInquiryAckCounts(BaseModel):
    """The Acknowledgement filter's own four counts (`PLAN-scm-oi-handshake.md` section
    4), computed with the `ack` filter itself DROPPED - the same rule the month, supplier,
    project, raised-by and kind controls follow, so choosing one value leaves the other
    three readable.

    Declared field by field rather than left to the dict the service builds, because
    `response_model` drops a key it has not been told about.
    """

    awaiting: int = 0
    acknowledged: int = 0
    changed: int = 0
    rejected: int = 0
    #: The page's DEFAULT view (R3): awaiting plus changed, the rows purchasing has still
    #: to answer. A fifth count rather than a fifth state - nothing is stored as
    #: `to_confirm` - so the chip on screen and the filter behind it read one number.
    to_confirm: int = 0


class OrderInquiryKindTotals(BaseModel):
    """The three cards above the schedule and the list (section 3.I2, AC-I11).

    Quantity, not row count: what a buyer acts on is how much is still to buy, and two
    rows of one and one row of two are the same day's work. Decimal STRINGS, like every
    quantity on this screen - a float round trip loses the tail of a quantity somebody
    signed for.

    Declared here rather than left to the dict the service returns, because
    `response_model` silently drops a key it has not been told about.
    """

    #: On SPO allocations already on their way.
    spo: str = "0"
    #: On purchase order lines.
    po: str = "0"
    #: Raised and on nothing - what still flows to reorder planning.
    buy: str = "0"


class OrderInquiryWorklistSummary(BaseModel):
    """The strip above the list, and the controls beside it.

    The totals honour every filter, the month included, because they describe what is on
    screen. Each axis drops its OWN filter, because a control that empties itself the
    moment it is used cannot be used a second time.
    """

    total_rows: int = 0
    total_qty: str = "0"
    by_state: OrderInquiryStateCounts = OrderInquiryStateCounts()
    by_month: List[OrderInquiryMonthTotal] = []
    suppliers: List[OrderInquiryFacet] = []
    projects: List[OrderInquiryFacet] = []
    #: The people who raised at least one of the rows in view, by the same rule the rows
    #: themselves use (their revision's confirmer, the header only when there is no
    #: revision) - the "Raised by" filter's own list. Never every user in the company: a
    #: picker whose entries mostly return nothing is a picker nobody uses twice.
    raised_by: List[OrderInquiryFacet] = []
    #: S1, R-K: the Location and Agent filters' own lists, same shape as `suppliers`,
    #: each computed with its own filter dropped.
    locations: List[OrderInquiryFacet] = []
    agents: List[OrderInquiryFacet] = []
    #: What the rows in view still need, per kind (AC-I11) - the cards' own figures.
    #: Computed with the `kind` filter dropped, like every other control here, so
    #: pressing one card leaves the other two readable.
    kinds: OrderInquiryKindTotals = OrderInquiryKindTotals()
    #: How many rows sit at each acknowledgement state (AC-H4), computed with the `ack`
    #: filter dropped for the same reason `kinds` drops its own.
    ack: OrderInquiryAckCounts = OrderInquiryAckCounts()
    #: What the page's "Link up to" date starts at (AC-LH5): the latest completed reorder
    #: run's own "Plan until". Read from here rather than invented on the page, so the plan
    #: and the buyer cannot be working to two different horizons. `None` when no run has
    #: named one, which means no horizon is in force.
    link_up_to_default: Optional[date] = None


class AcknowledgeFilter(BaseModel):
    """The SAME shape `GET /order-inquiries` filters on, minus paging and sort (AC-CF-8,
    S2 `PLAN-oi-confirm-per-so.md`, `oi-confirm-per-so-contract.md`): "Select all N
    matching" resolves against exactly the scope the worklist itself is filtered to,
    never a client-rebuilt copy of it. Every field is optional; an absent one means "not
    filtered on that axis", exactly as the list reads it. `query`/`location`/
    `po_number`/`spo_number` carry the SAME length caps the list route's own `Query(...,
    max_length=...)` declarations do, and `state` the same closed set - a bad `ack`/
    `linked`/`kind` (open strings here, same as the list route's own free-standing
    validation) is still refused by `OrderInquiryWorklistService._base`, which is where
    the list route's own values are validated too; the length/state checks below just
    move that refusal to the schema, before any SQL, for the fields the list route
    itself pins at the route layer rather than in `_base`.

    `project_id`/`supplier_id`/`agent` are pattern-pinned the same way the list route's
    own `axis_key` query param is (`pattern=UUID_PATTERN`, AC-CF-8d): a JSON body field
    is a request the caller composed, not a path segment, so a malformed one reads as
    422 (bad input) here rather than the 404 ("missing resource") `validate_uuid_path`
    answers for a path param - the route ALSO runs the shared UUID guard those routes
    use (`_validate_worklist_filter_uuids`), so a value that somehow slipped past this
    pattern is still refused before it reaches SQL.

    `project` (S5, `PLAN-oi-project-label-from-so.md` section 5) is text, exact match on
    the Project column, never a uuid - separate from `project_id`, which stays as it is.
    A bulk action that ignored it would act on rows outside the worklist's own scope. No
    length bound: `projects.title` is TEXT with none.
    """

    model_config = ConfigDict(extra="forbid")

    query: Optional[str] = Field(None, max_length=WORKLIST_QUERY_MAX_LENGTH)
    delivery_month: Optional[str] = None
    raised_date: Optional[str] = None
    state: Optional[WorklistState] = None
    project_id: Optional[str] = Field(None, pattern=UUID_PATTERN)
    project: Optional[str] = None
    supplier_id: Optional[str] = Field(None, pattern=UUID_PATTERN)
    raised_by: Optional[str] = None
    linked: Optional[str] = None
    kind: Optional[str] = None
    ack: Optional[str] = None
    location: Optional[str] = Field(None, max_length=WORKLIST_FILTER_MAX_LENGTH)
    agent: Optional[str] = Field(None, pattern=UUID_PATTERN)
    so_month: Optional[str] = None
    po_number: Optional[str] = Field(None, max_length=WORKLIST_FILTER_MAX_LENGTH)
    spo_number: Optional[str] = Field(None, max_length=WORKLIST_FILTER_MAX_LENGTH)
    delivery_from: Optional[str] = None
    delivery_to: Optional[str] = None
    axis: Optional[str] = None
    axis_key: Optional[str] = Field(None, pattern=UUID_PATTERN)
    #: The OI detail page's own whole-header Confirm (S3, `PLAN-oi-header-list-detail.
    #: md`): "Select all N matching" narrowed to one header, so pressing Confirm with
    #: nothing ticked means exactly that OI and nothing else.
    inquiry_id: Optional[str] = Field(None, pattern=UUID_PATTERN)


class AcknowledgeRowsRequest(BaseModel):
    """Purchasing takes on one row, a batch of them, or every row a `filter` matches
    (AC-H2, AC-CF-8 `PLAN-oi-confirm-per-so.md` S2).

    Exactly one of `row_ids` / `filter` is named - both, or neither, is refused
    (AC-CF-8c). What an acknowledgement means is fixed either way: the rows become
    purchasing's work and the cascade runs for exactly them.

    `extra="forbid"` (fix round, consistency with `AutoPlaceRequest`): an unknown key
    used to be silently dropped rather than refused.
    """

    model_config = ConfigDict(extra="forbid")

    #: Capped at 500 (security review round 1) - the same guard rail a hand-typed batch
    #: id list gets everywhere else on this route module, so a caller cannot force one
    #: request to walk an unbounded id list.
    row_ids: Optional[List[str]] = Field(None, min_length=1, max_length=500)
    #: "Select all N matching" (S2): resolved server-side against the SAME filters the
    #: worklist's own list/summary read, so the scope is never a stale or hand-rebuilt
    #: copy of what the buyer is looking at.
    filter: Optional[AcknowledgeFilter] = None
    #: The LINK HORIZON (`PLAN-scm-oi-handshake.md` section 11): rows due AFTER this date
    #: are still TAKEN ON, but they are left Not linked, so a 2030 order stops eating a
    #: purchase order a nearer one needed. Omitted means the reorder plan's own horizon,
    #: unless `link_horizon` says otherwise.
    link_up_to: Optional[date] = None
    #: WHICH horizon this call means (S1, code review 27 Aug 2026). `"none"` is an explicit
    #: NO horizon - the buyer emptied the date box, which is a different instruction from
    #: naming none and used to travel as the same silence; `"plan"` is the reorder plan's
    #: own; `"date"` requires `link_up_to`. OMITTED is inferred - the date when one is
    #: given, the plan when it is not - so every existing caller means what it always did.
    link_horizon: Optional[Literal["date", "plan", "none"]] = None

    @model_validator(mode="after")
    def _exactly_one_scope(self) -> "AcknowledgeRowsRequest":
        if bool(self.row_ids) == bool(self.filter):
            raise ValueError(
                "Name row_ids or filter - never both, and never neither (AC-CF-8c)."
            )
        return self


class AcknowledgeResult(BaseModel):
    """What one press did, in the numbers the banner reports: how many rows were taken on,
    how much of them found a document at that moment, and how many were left for a later
    horizon."""

    acknowledged: int = 0
    #: Rows the cascade linked, and how many placements it made across them. `0` is an
    #: ordinary answer: there may be nothing open to link to yet.
    linked_rows: int = 0
    links: int = 0
    #: Rows still owed but due after `link_up_to`, left Not linked on purpose (AC-LH1). The
    #: banner's second half: "1 linked, 1 after 31 Dec 2026".
    after_horizon: int = 0
    #: The horizon the press actually ran under - the caller's own date, or the plan's when
    #: they named none. Stated back so a zero is never a figure measured against a date
    #: nobody can see.
    link_up_to: Optional[date] = None
    #: WHETHER a horizon was in force, so a null `link_up_to` is never read two ways: `"none"`
    #: is "nothing was held back for a date", `"date"` names the one above (S1).
    link_horizon: Literal["date", "none"] = "none"
    #: Rows the `filter` matched but left untouched - rejected, already acknowledged, or
    #: cancelled (AC-CF-8, S2 `PLAN-oi-confirm-per-so.md`). Always 0 on a `row_ids` press,
    #: which still refuses such a row outright rather than quietly skipping it.
    skipped: int = 0


class UnacknowledgeRowsRequest(BaseModel):
    """Purchasing takes a row back off its own plate
    (PLAN-oi-worklist-split-customer-project.md, Slice 3, owner 18 Sep 2026) - the
    reverse of Confirm, for a row taken on by mistake or a reconfirm that has not
    actually happened yet.

    `row_ids` only - no `filter` branch: Unconfirm always names exactly what the buyer
    ticked, never "everything matching a scope" the way "Select all N matching" does for
    Confirm.
    """

    #: At least one, capped at 500 (security review round 1) - the Actions menu names
    #: exactly what is ticked, so an unbounded list here could only be a hand-built
    #: request, never the UI's own.
    row_ids: List[str] = Field(..., min_length=1, max_length=500)


class UnacknowledgeResult(BaseModel):
    """What one Unconfirm press did. No cascade runs, so there is nothing here like
    `AcknowledgeResult`'s linking figures - just how many rows actually moved and how
    many the press left alone."""

    #: Rows that were `acknowledged`/`changed` and are now back to `awaiting`.
    updated: int = 0
    #: Rows named that were already `awaiting`, `rejected`, cancelled, or outside this
    #: company's scope - never an error, always just left untouched (S1).
    skipped: int = 0


class RejectRowRequest(BaseModel):
    """Purchasing refuses a row, with a reason CS will read on the board cell.

    The reason is REQUIRED (AC-H5). A refusal with no reason sends CS back to a person to
    ask, which is the whole thing the board's "Rejected by X: Y" exists to stop.
    """

    reason: str = Field(..., min_length=1, max_length=1000)

    @model_validator(mode="after")
    def _reason_is_not_blank(self) -> "RejectRowRequest":
        if not (self.reason or "").strip():
            raise ValueError("Say why this row is being rejected.")
        return self


class RejectRowsRequest(BaseModel):
    """Purchasing refuses a BATCH with ONE reason (`PLAN-scm-oi-draft-links.md` 5.6).

    Reject is a bulk action now that the row Actions column is gone (R8), and asking for
    the reason once per row would make refusing twenty rows twenty dialogs. One reason for
    the press, because one press is one decision.
    """

    row_ids: List[str] = Field(..., min_length=1)
    reason: str = Field(..., min_length=1, max_length=1000)

    @model_validator(mode="after")
    def _reason_is_not_blank(self) -> "RejectRowsRequest":
        if not (self.reason or "").strip():
            raise ValueError("Say why these rows are being rejected.")
        return self


class RejectRowResult(BaseModel):
    """What the batch did to ONE row. `ok` is always true today - the whole batch is
    refused before anything is written when any row cannot be rejected - and the shape
    still reports per row, because that is what the screen names a failure by."""

    row_id: str
    ok: bool = True
    error: Optional[str] = None


class RejectRowsResult(BaseModel):
    rejected: int = 0
    results: List[RejectRowResult] = []


class LinkNowRequest(BaseModel):
    """Run the cascade over acknowledged rows now (AC-H13), optionally narrowed to the
    products an upload just touched. Omitted `product_ids` means every acknowledged or
    changed row that still has something unlinked."""

    product_ids: Optional[List[str]] = None
    #: The LINK HORIZON (section 11). Omitted means the reorder plan's own horizon.
    link_up_to: Optional[date] = None
    #: WHICH horizon this call means (S1, code review 27 Aug 2026). `"none"` is an explicit
    #: NO horizon - the buyer emptied the date box, which is a different instruction from
    #: naming none and used to travel as the same silence; `"plan"` is the reorder plan's
    #: own; `"date"` requires `link_up_to`. OMITTED is inferred - the date when one is
    #: given, the plan when it is not - so every existing caller means what it always did.
    link_horizon: Optional[Literal["date", "plan", "none"]] = None


class MarkInquiryRowsRequest(BaseModel):
    row_ids: List[str] = Field(..., min_length=1)
    state: str = Field(..., description="raised, actioned or cancelled")


class OrderInquiryPoCandidateClaim(BaseModel):
    """One EXISTING tag already on this candidate's PO line - the row's expand (section G,
    "the captain, 20 Aug"). Read straight off the placed rows themselves: the tag IS the
    evidence, so there is nothing else to derive it from.
    """

    so_number: Optional[str] = None
    item_code: Optional[str] = None
    qty: str
    placed_date: Optional[datetime] = None


class OrderInquiryPoCandidate(BaseModel):
    """One open document line this row could be linked to (section 3.I).

    In the walk's own order, outermost key first: the document CS cited; then an SPO
    allocation before a purchase order line on an ORDER BACK row; then the location tier
    (Q5); then the purchase order's OWN issue date, then the line's expected date, then the
    document number (Q7). Location NEVER filters a candidate out - it only ranks it.

    `recommended` marks the first candidate whose `remaining` balance covers what the row
    still needs. `already_tagged` is what OTHER links already claim off this same line, so
    `remaining` is never a promise this line cannot keep, and `claims` names those other
    rows one by one.
    """

    #: `po` or `spo`. An `spo` candidate is offered to an ORDER BACK row and nothing else.
    kind: str = "po"
    #: The purchase order line, or the SPO allocation. Exactly one is set.
    po_line_id: Optional[str] = None
    spo_allocation_id: Optional[str] = None
    po_number: str
    line_label: Optional[str] = None
    #: Where that line lands the goods, and how well it fits the row's own location.
    location: Optional[str] = None
    tier: int = 5
    #: CS named this document on the order back, so the walk tried it before any other.
    cited: bool = False
    supplier_name: Optional[str] = None
    #: BOTH dates, because the cascade orders on the document's date first and the line's
    #: second (Q7) and a candidate list that showed one could not be checked against it.
    issue_date: Optional[date] = None
    expected_date: Optional[date] = None
    qty_ordered: str
    qty_received: str
    already_tagged: str
    remaining: str
    covers: bool
    recommended: bool = False
    # The line's own held price, when the PO carries one. Never a guess.
    unit_cost: Optional[str] = None
    currency: Optional[str] = None
    claims: List[OrderInquiryPoCandidateClaim] = []
    # What the cascade (G2, 20 Aug: "take from the earliest PO, then subsequently from
    # subsequent PO") would take off THIS line for THIS row - `0` when the cascade never
    # reaches this line (already covered by an earlier one, or nothing is left to cover).
    # Server-computed by the SAME walk `auto_place_for_products` runs, so the dialog's
    # preview and the auto pass can never disagree; the dialog offers it as an editable
    # starting point, not the only answer.
    default_take: str = "0"
    # G7 dedication (`PLAN-scm-reorder-oi-feedback-1sep.md` S6): the SO NUMBER another
    # `scm.order_link_claim` names on this line, when that SO is not the row's own - the
    # dialog greys the row "Dedicated to SO xxxx". `None` when nobody but this row's own
    # SO (or nobody at all) claims it.
    dedicated_to: Optional[str] = None
    # G12's project-bin lock: this line sits at a `segment = 'project'` warehouse and NO
    # SO has claimed it, not even this row's own - the dialog greys it "Unattributed -
    # link manually". Always `False` for a pool-destination line (AC-6.10).
    unattributed: bool = False
    # S8 (AC-CF-24): what THIS row already takes off this line, "0" when it holds none.
    # `remaining` above is already credited back to include it, so re-placing the same
    # take never reads as "over the line's remaining".
    current_take: str = "0"
    # S8 review round (17 Sep): `False` only on a FORCED entry - a line this row already
    # holds a link on that is closed, or on a PO no longer active/partial. Every ordinary
    # candidate the walk offers is `True`.
    line_open: bool = True


class OrderInquiryPoCandidatesResponse(BaseModel):
    """The Link dialog's own GET (S8): the candidate list, plus the header line the
    dialog reads - "N still to link of Q" - computed the same way `covers` is, so the
    two can never disagree.
    """

    candidates: List[OrderInquiryPoCandidate] = []
    still_to_link: str
    # S8 review round (17 Sep): `row.qty - row.bundled_qty`, the SAME ceiling
    # `_place_on_po_set` enforces server-side - never the row's bare `qty`, which a
    # bundled row's dialog used to check the submitted total against instead.
    linkable_qty: str


class PlaceOnPoAllocation(BaseModel):
    """One line of a link: this row takes `qty` off ONE document line.

    Exactly one of the two ids is set, the same rule the link row's own CHECK constraint
    holds and the same rule `order_link_claim` follows for its purchase side.
    """

    po_line_id: Optional[str] = None
    spo_allocation_id: Optional[str] = None
    qty: str

    @model_validator(mode="after")
    def _names_exactly_one_target(self) -> "PlaceOnPoAllocation":
        if bool(self.po_line_id) == bool(self.spo_allocation_id):
            raise ValueError(
                "Name a purchase order line or an SPO allocation, not both and not neither."
            )
        return self


class PlaceOnPoRequest(BaseModel):
    po_line_id: Optional[str] = Field(
        None, description="Single-target link: one purchase order line, whole remainder."
    )
    # Several document lines can cover one row. Mutually exclusive with `po_line_id` - a
    # caller either names one line directly or hands over the whole allocation.
    allocations: Optional[List[PlaceOnPoAllocation]] = Field(
        None,
        description=(
            "Link across one or more lines: {po_line_id | spo_allocation_id, qty}."
        ),
    )
    # S8 review round (17 Sep): the candidate ids the Link dialog actually rendered
    # before this press. With `allocations` present this scopes SET semantics' retire
    # step - a line the row holds that is missing from BOTH `allocations` and this list
    # was never shown to the caller and survives; omitted entirely, every caller before
    # this round keeps retiring whatever `allocations` left out, unchanged.
    offered_line_ids: Optional[List[str]] = None

    @model_validator(mode="after")
    def _names_something_to_place(self) -> "PlaceOnPoRequest":
        if not self.po_line_id and not self.allocations:
            raise ValueError("Name a purchase order line, or a list of allocations.")
        return self


class AutoPlaceInquiryFilter(BaseModel):
    """The ONE scope the detail page's gear "Auto link" needs (S3,
    `PLAN-oi-header-list-detail.md`): everything ticked, else the whole OI - never a
    client-rebuilt copy of the worklist's own filter shape, because a header's Auto link
    means exactly "this OI", nothing wider.
    """

    model_config = ConfigDict(extra="forbid")

    inquiry_id: Optional[str] = Field(None, pattern=UUID_PATTERN)


class AutoPlaceRequest(BaseModel):
    """Run the cascade now - the worklist's own "Auto-link". Omitted `product_ids` means
    every product that currently has a raised or partly linked ORDER / RESERVE & ORDER /
    ORDER BACK row. `row_ids` names the rows and nothing else (the worklist's and the OI
    detail's "Link selected", R18 - there is no separate route for it any more), and
    wins over `product_ids`. `filter.inquiry_id` (S3) scopes the whole cascade to one
    header's own rows, on top of whichever of the other two is also given.

    `extra="forbid"` (S3, security review round 1 precedent on `AcknowledgeFilter`): an
    unknown `filter` key used to be silently dropped by Pydantic's own default, which let
    a caller believe `filter.inquiry_id` scoped the cascade when it did nothing at all -
    the cascade then ran UNSCOPED, across every company's rows, so this is a
    scoping-correctness fix and not just a stricter validator.
    """

    model_config = ConfigDict(extra="forbid")

    product_ids: Optional[List[str]] = None
    row_ids: Optional[List[str]] = None
    filter: Optional[AutoPlaceInquiryFilter] = None
    #: The LINK HORIZON (section 11) the page's "Link selected" carries. Omitted means the
    #: reorder plan's own horizon.
    link_up_to: Optional[date] = None
    #: WHICH horizon this call means (S1, code review 27 Aug 2026). `"none"` is an explicit
    #: NO horizon - the buyer emptied the date box, which is a different instruction from
    #: naming none and used to travel as the same silence; `"plan"` is the reorder plan's
    #: own; `"date"` requires `link_up_to`. OMITTED is inferred - the date when one is
    #: given, the plan when it is not - so every existing caller means what it always did.
    link_horizon: Optional[Literal["date", "plan", "none"]] = None


class UnlinkRequest(BaseModel):
    """Unlink. With a `link_id` that ONE link goes and the row keeps its others; without
    one every link the row holds goes, which is what the whole-row action means."""

    link_id: Optional[str] = None


class AutoPlaceResult(BaseModel):
    placed_rows: int = 0
    allocations: int = 0
    products_touched: int = 0
    #: AC-LT-37 (G4): the book step's own count, real links, distinct from the
    #: cascade's own guesses below.
    book_linked_rows: int = 0
    #: AC-LT-37: rows the cascade walk offered a suggested link to this pass - never
    #: a placement, and `placed_rows` above already counts them for backward
    #: compatibility (the walk's own terminal write is a suggestion since S3).
    suggested_rows: int = 0
    #: Rows still owed but due after `link_up_to`, left Not linked on purpose (AC-LH2).
    after_horizon: int = 0
    #: The horizon the pass ran under - the caller's own date, or the plan's own when they
    #: named none.
    link_up_to: Optional[date] = None
    #: WHETHER a horizon was in force at all (S1). See `AcknowledgeResult.link_horizon`.
    link_horizon: Literal["date", "none"] = "none"
    #: R18 (`PLAN-oi-links-autocount-truth-24sep.md` 3.6): how many rows this pass
    #: actually moved - book-linked this pass, or given a different suggestion than
    #: the one they held coming in. "Link selected" reports this so recalculating
    #: against AutoCount can say whether it caught anything, distinct from
    #: `book_linked_rows` / `suggested_rows`, which count the OUTCOME rather than
    #: whether that outcome is new.
    changed_rows: int = 0


class UnplaceAllRequest(BaseModel):
    """"Unplace all" for the CURRENT worklist scope (the captain, 20-21 Aug: it operates
    on whatever the list is filtered to - one product when the filters happen to narrow
    to one, every placed row when they do not). The SAME filter shape `GET
    /order-inquiries` takes, minus `state` - this is always about placed rows, whatever
    else is filtered - and no `product_ids`: the worklist paginates server-side, so a
    client-derived product list would silently miss rows behind page 1. Every field
    omitted means every placed row in the company.
    """

    query: Optional[str] = None
    delivery_month: Optional[str] = None
    raised_date: Optional[str] = None
    project_id: Optional[str] = None
    #: S5 (`PLAN-oi-project-label-from-so.md` section 5): text, exact match on the
    #: Project column - separate from `project_id`, unchanged. No length bound:
    #: `projects.title` is TEXT with none.
    project: Optional[str] = None
    supplier_id: Optional[str] = None
    #: The user whose inquiries the list is narrowed to, so the action can never reach
    #: further than what the person pressing it can see.
    raised_by: Optional[str] = None


class OrderInquiryWorklistExportRequest(BaseModel):
    """Lane B (`PLAN-order-sheet-oi-reports-22sep.md`, AC-B6): the list page's own
    async export - the SAME filter shape `GET /order-inquiries` (and its retiring
    sync `GET /order-inquiries/export`) already take, as a JSON body rather than a
    query string. Every field omitted means the whole book, exactly like the GET.

    Security review fix round 2, item 1: `query`/`location`/`po_number`/`spo_number`
    carry the SAME length caps the GET route's own `Query(..., max_length=...)`
    declarations do; `state`/`linked`/`kind`/`ack` the SAME closed `Literal` sets the
    GET route pins at the route layer. `project_id`/`supplier_id`/`agent` are
    pattern-pinned the same way `AcknowledgeFilter` above pins its own (`pattern=
    UUID_PATTERN`) - a malformed JSON body field reads as 422 (bad input), never the
    404 `validate_uuid_path` answers for a path param. The route ALSO runs
    `_validate_worklist_filter_uuids` before creating the download row, so a value
    that somehow slipped past this pattern is still refused before it reaches SQL.
    """

    query: Optional[str] = Field(None, max_length=WORKLIST_QUERY_MAX_LENGTH)
    delivery_month: Optional[str] = None
    raised_date: Optional[str] = None
    state: Optional[WorklistState] = None
    project_id: Optional[str] = Field(None, pattern=UUID_PATTERN)
    project: Optional[str] = None
    supplier_id: Optional[str] = Field(None, pattern=UUID_PATTERN)
    raised_by: Optional[str] = None
    linked: Optional[Literal["po", "spo", "none"]] = None
    kind: Optional[Literal["spo", "po", "buy"]] = None
    ack: Optional[
        Literal["awaiting", "acknowledged", "changed", "rejected", "to_confirm"]
    ] = None
    location: Optional[str] = Field(None, max_length=WORKLIST_FILTER_MAX_LENGTH)
    agent: Optional[str] = Field(None, pattern=UUID_PATTERN)
    so_month: Optional[str] = None
    po_number: Optional[str] = Field(None, max_length=WORKLIST_FILTER_MAX_LENGTH)
    spo_number: Optional[str] = Field(None, max_length=WORKLIST_FILTER_MAX_LENGTH)
    delivery_from: Optional[str] = None
    delivery_to: Optional[str] = None


class UnplaceAllResult(BaseModel):
    unplaced: int = 0


class UnplaceAllPreview(BaseModel):
    """The confirm dialog's own numbers, resolved server-side against the SAME filters
    `unplace-all` itself reads - never derived from whatever page of the worklist happens
    to be loaded in the browser. `product_code`/`product_name` are set only when EVERY
    matching row resolves to the same product; otherwise both stay null and the dialog
    speaks only of the count."""

    count: int = 0
    product_code: Optional[str] = None
    product_name: Optional[str] = None


class OrderInquiryPoDetailLine(BaseModel):
    """One line of the purchase order behind a placed worklist row - the "PO no" cell's
    popup (the captain, 20 Aug). Read straight off `purchase_order_lines`, never netted
    against other rows' claims - that reading belongs to the "Place on PO" candidates,
    not to a plain look at what was ordered."""

    #: Issue #1215 point 2 - the line's own identity, so the FE can highlight the line
    #: an opening row's link actually sits on. The SKU alone is ambiguous the moment a
    #: PO carries two lines of the same item.
    id: Optional[str] = None
    sku: Optional[str] = None
    product_name: Optional[str] = None
    qty_ordered: str
    qty_received: str
    remaining: str
    #: Every order inquiry row's own placement on THIS line, summed (never netted
    #: against anything else - that reading belongs to the "Place on PO" candidates).
    #: Optional only for a caller that predates this field; `get_po_detail` always
    #: sends it.
    allocated: Optional[str] = None
    location: Optional[str] = None
    #: The book's own linkage for this line - the SAME fact and the SAME shape the SCM
    #: purchase-order detail's Lines tab prints (`PurchaseOrderLine.book_so_number` /
    #: `book_so_unresolved`), read here off the line's own `from_so_line_ref` and resolved
    #: through the same reader, `order_link_service.book_so_numbers_by_ref`, so a line's
    #: linkage does not depend on which screen it is read from. `response_model` silently
    #: drops an undeclared field, which is exactly why both of these are declared.
    book_so_number: Optional[str] = None
    #: True when the book named a sales order this CRM does not hold. Three states, not
    #: two - see `PurchaseOrderLine` in `app/schemas/scm_orders.py` for the full note.
    #:
    #: Stays `bool` here, unlike `PurchaseOrderLine.book_so_unresolved` (review of PR #764,
    #: F2): `get_po_detail` is a single-document detail read with no list-mode variant, so
    #: it always calls `book_so_numbers_by_ref` and never passes
    #: `order_link_service.BOOK_SO_NOT_RESOLVED` - the fourth, "not computed" state that
    #: `Optional[bool]` exists to carry on the PO route never arises on this one.
    book_so_unresolved: bool = False


class OrderInquiryDocumentAllocation(BaseModel):
    """WHO is holding this document's quantity - the lightbox's Allocated to panel
    (`PLAN-scm-oi-draft-links.md` section 4.3).

    `ack_state` is the whole point of it: a link on a row nobody has confirmed is a DRAFT
    and the panel reads it Proposed, one on an acknowledged row reads Confirmed. There is
    no state on the link itself (R1), so the ROW's own stamp travels here instead.

    Nothing is an id: the inquiry number and the sales-order number are what a person
    quotes, and a UUID on this panel is a UUID on the screen.
    """

    inquiry_no: Optional[str] = None
    so_number: Optional[str] = None
    item_code: Optional[str] = None
    qty: str
    ack_state: Optional[str] = None
    linked_at: Optional[datetime] = None
    #: Issue #1215 point 2 - which PO line this allocation sits on, so the FE can
    #: highlight it on the lines grid. Null on an SPO allocation.
    po_line_id: Optional[str] = None


class OrderInquiryPoDetail(BaseModel):
    """The PO popup's whole answer: the header purchasing already reads off the sheet,
    plus every line, not only the one this row happened to be tagged to."""

    id: str
    po_number: str
    supplier_code: Optional[str] = None
    supplier_name: Optional[str] = None
    expected_date: Optional[date] = None
    status: str
    lines: List[OrderInquiryPoDetailLine] = []
    #: Who this purchase order's quantity is spoken for by, drafts included (AC-D18).
    #: Real links only - a suggested link never appears here (AC-LT-34).
    allocations: List[OrderInquiryDocumentAllocation] = []


class OrderInquirySpoDetailLine(BaseModel):
    """One allocation line of a shipping order: what is on it, what has landed, where.

    `location` is the warehouse code when the book named a warehouse we hold, else the raw
    code it printed, else nothing - the screen says "no location" rather than inventing
    one. EVERY line is listed, including one outside the pool set that the cascade will
    never take (R11): showing it is how a buyer learns why nothing was drafted onto it.
    """

    #: R15 (owner rulings, 25 Sep 2026, hand test on stack C): the line's own identity
    #: (`spo_allocations.id`), the same reason `OrderInquiryPoDetailLine.id` exists
    #: (issue #1215 point 2) - without it the SPO lightbox has no field to highlight a
    #: line by, unlike the PO lightbox next door.
    id: Optional[str] = None
    sku: Optional[str] = None
    product_name: Optional[str] = None
    #: R31b (stock debt lane): this line's own `spo_allocations.spo_line_number`, so a
    #: caller opening the dialog off a linked demand line can mark and jump to the row.
    spo_line_number: Optional[int] = None
    allocated: str
    received: str
    remaining: str
    location: Optional[str] = None
    #: The purchase order this SPO allocation was raised FROM, per AutoCount's own
    #: statement (`SPOAllocation.from_po_number`, migration 493 / contract 2.2). Plain
    #: text, never a link - null when the book named no source for this line. Never
    #: `from_po_line_ref` - that is a resolver key, not a thing a buyer reads, and it is
    #: deliberately never sent.
    source_po_number: Optional[str] = None


class OrderInquirySpoDetail(BaseModel):
    """The same lightbox for the other book (section 4.3).

    Addressed by NUMBER: a shipping order is a set of `spo_allocations` rows and has no
    purchase order id behind it, and the number is what the link on screen carries.
    """

    spo_number: str
    supplier_name: Optional[str] = None
    #: When the goods are expected - the earliest date the document's own lines state.
    eta: Optional[date] = None
    #: The inbound shipment behind it, when one has been raised. Absent on a book line the
    #: outstanding upload wrote, which names no shipment at all.
    shipment_ref: Optional[str] = None
    container_no: Optional[str] = None
    lines: List[OrderInquirySpoDetailLine] = []
    #: Real links only - a suggested link never appears here (AC-LT-34).
    allocations: List[OrderInquiryDocumentAllocation] = []


# ---------------------------------------------------------------------------------
# The order inquiry HEADER (S2/S3, `PLAN-oi-header-list-detail.md`). One row per OI -
# never one per instruction, which is what every schema above this line answers.


class OrderInquiryHeaderOut(BaseModel):
    """One row of the Documents view (AC-LS-01, plan "Contract"). Every field declared
    here and asserted by a test - `response_model` silently drops an undeclared one."""

    id: str
    inquiry_no: Optional[str] = None
    #: The pre-renumber value (S1) - kept so a number quoted in an old email still
    #: finds this OI. `None` for every header born after the renumber.
    legacy_inquiry_no: Optional[str] = None
    raised_at: Optional[datetime] = None
    raised_by_name: Optional[str] = None
    #: The CORE `sales_orders.id`, null when this order never reached AutoCount.
    sales_order_id: Optional[str] = None
    project_sales_order_id: str
    so_number: Optional[str] = None
    so_date: Optional[date] = None
    customer_name: Optional[str] = None
    customer_code: Optional[str] = None
    project_id: Optional[str] = None
    project_title: Optional[str] = None
    agent_name: Optional[str] = None
    #: Non-cancelled rows only (AC-LS-05).
    lines_total: int = 0
    lines_to_confirm: int = 0
    qty_total: str = "0"
    #: DERIVED, never stored (AC-LS-02): `lines_to_confirm > 0`.
    status: Literal["outstanding", "completed"] = "outstanding"


class OrderInquiryRaiseHistoryEntryOut(BaseModel):
    """One entry of the General tab's Raise history card (AC-RD-03). Newest first."""

    kind: Literal["raised", "reconfirmed"]
    by_name: Optional[str] = None
    at: Optional[datetime] = None


class OrderInquiryHeaderDetailOut(OrderInquiryHeaderOut):
    """`GET /order-inquiry-headers/{id}` (AC-DT-01): the header plus the Order/Customer
    blocks and the full raise history - a superset of the list row's own fields, so a
    detail page opened straight from a deep link never has to re-fetch the list row."""

    order_type: Optional[str] = None
    raise_history: List[OrderInquiryRaiseHistoryEntryOut] = []


class OrderInquiryRelatedPOOut(BaseModel):
    """One purchase order this header's rows are linked to (AC-DT-03)."""

    po_id: str
    po_number: Optional[str] = None
    supplier_name: Optional[str] = None
    po_date: Optional[date] = None
    lines_linked: int = 0
    qty_linked: str = "0"


class OrderInquiryRelatedSPOOut(BaseModel):
    """One SPO this header's rows are linked to (AC-DT-03)."""

    spo_number: Optional[str] = None
    supplier_name: Optional[str] = None
    lines_linked: int = 0
    qty_linked: str = "0"


class OrderInquiryRelatedDocumentsOut(BaseModel):
    """`GET /order-inquiry-headers/{id}/related-documents` (AC-DT-03). Empty lists,
    never null, when this header's rows link to nothing yet."""

    purchase_orders: List[OrderInquiryRelatedPOOut] = []
    spos: List[OrderInquiryRelatedSPOOut] = []


# ------------------------------------------------------- request CS to reserve (3.2/3.3)


def _finite_qty(value: str) -> str:
    """SF-9 (security review): `"nan"`/`"inf"`/`"-inf"` construct a valid `Decimal`
    (no exception at parse time) and only blow up - `decimal.InvalidOperation` -> an
    uncaught 500 - on the FIRST comparison the service makes against one, on reserve,
    unreserve and create alike. Pydantic answers 422 here, before any of that code
    runs; `order_inquiry_reserve_service._dec` rejects the same shape as its own
    belt-and-braces, for a caller that reaches the service directly."""
    try:
        parsed = Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("must be a valid number") from None
    if not parsed.is_finite():
        raise ValueError("must be a finite number")
    # Security re-review: the columns are `Numeric(15,4)` - more than 4 decimals would be
    # rounded (a misleading 409 once 0.00001 reads 0) and more than 11 integer digits
    # overflows (a `NumericValueOutOfRange` 500).
    if parsed.adjusted() > 10 or parsed != parsed.quantize(Decimal("0.0001")):
        raise ValueError("must have at most 11 digits before and 4 after the decimal point")
    return value


def _no_nul(value: Optional[str]) -> Optional[str]:
    """Postgres text cannot hold NUL; psycopg raises a ValueError (a 500) on one."""
    if value is not None and "\x00" in value:
        raise ValueError("must not contain a NUL character")
    return value


class ReserveRequestRowIn(BaseModel):
    """One order-inquiry row named on a request (3.2). `warehouse_id` omitted means the
    pool of the row's own `stock_location` (R3).

    N-2 (review round): `row_id`/`warehouse_id` are UUID-PATTERNED - a malformed value
    reached a raw `.id.in_([...])` downstream and 500'd instead of 422."""

    row_id: str = Field(..., pattern=UUID_PATTERN)
    qty_requested: str
    warehouse_id: Optional[str] = Field(None, pattern=UUID_PATTERN)

    _qty_requested_finite = field_validator("qty_requested")(_finite_qty)


class CreateReserveRequestIn(BaseModel):
    rows: List[ReserveRequestRowIn]
    #: N-4 (review round): an arbitrarily long note lands verbatim in an outgoing email
    #: body / the worklist chip.
    note: Optional[str] = Field(None, max_length=5000)

    @model_validator(mode="after")
    def _no_duplicate_rows(self) -> "CreateReserveRequestIn":
        """N-2: the SAME `row_id` named twice in one CREATE payload used to write two
        `OrderInquiryReserveRequestRow`s for one row, together requesting more than the
        row's own remaining - refused here, before any write, same wording family as
        the service's own "already has an open reserve request" (`reserve_request_
        already_open`)."""
        seen: set = set()
        for row in self.rows:
            if row.row_id in seen:
                raise ValueError(
                    f"Row {row.row_id} already has an open reserve request on this ask."
                )
            seen.add(row.row_id)
        return self


# ------------------------------------------------- request CS to reserve, round 4 (6e.1)


class CommitReserveRowIn(BaseModel):
    """One OPEN request row answered inside a commit call (6e.1's own `reserves` list).
    `row_id` is `OrderInquiryRow.id`, the same id `ReserveRequestRowIn` already keys by;
    `warehouse_id` omitted falls back to the request row's own default (R3)."""

    row_id: str = Field(..., pattern=UUID_PATTERN)
    warehouse_id: Optional[str] = Field(None, pattern=UUID_PATTERN)
    qty_reserved: str
    reason: Optional[str] = Field(None, max_length=2000)

    _qty_reserved_finite = field_validator("qty_reserved")(_finite_qty)
    _reason_no_nul = field_validator("reason")(_no_nul)


class CommitAmendRowIn(BaseModel):
    """One ALREADY-ANSWERED request row amended inside a commit call (6e.1's own
    `amendments` list, R4-3). `warehouse_id` is never accepted here - the location is
    locked to whatever the row was already answered with."""

    row_id: str = Field(..., pattern=UUID_PATTERN)
    qty_reserved: str
    reason: Optional[str] = Field(None, max_length=2000)

    _qty_reserved_finite = field_validator("qty_reserved")(_finite_qty)
    _reason_no_nul = field_validator("reason")(_no_nul)


class CommitReserveRequestIn(BaseModel):
    """`POST .../order-inquiries/{inquiry_id}/reserve-commit` (6e.1, re-keyed by 6e.4):
    one transaction, `reserves` for rows with an open request row and `amendments` for
    rows already answered - at least one entry across the two lists. Duplicates are the
    service's own 422 (`reserve_commit_duplicate_row`)."""

    reserves: List[CommitReserveRowIn] = []
    amendments: List[CommitAmendRowIn] = []

    @model_validator(mode="after")
    def _at_least_one_row(self) -> "CommitReserveRequestIn":
        if not self.reserves and not self.amendments:
            raise ValueError("Select at least one line to reserve or amend.")
        return self


class ReserveHistoryEntryOut(BaseModel):
    """One line of the dialog's History tab (F3): `kind` is `requested` / `reserved` /
    `unreserved` / `cancelled`, newest first. `actor_name` is always a human name or
    email, never a UUID (Cursor rules)."""

    kind: str
    qty: Optional[str] = None
    location: Optional[str] = None
    reason: Optional[str] = None
    actor_name: Optional[str] = None
    created_at: Optional[datetime] = None


class OrderInquiryReserveRequestRowOut(BaseModel):
    id: str
    row_id: str
    item_code: Optional[str] = None
    qty_requested: str
    warehouse_id: Optional[str] = None
    location: Optional[str] = None
    qty_reserved: Optional[str] = None
    reason: Optional[str] = None


class OrderInquiryReserveRequestOut(BaseModel):
    """`POST .../reserve-requests`, `.../reserve-requests/{id}/cancel`, `.../reserve`
    (AC-RS-1, AC-RS-19, AC-RS-6). `notified_name` is the first resolved recipient of the
    request mail, read back for the dialog's own toast (plan 3.7) - null when the
    request automation is disabled or holds no recipient yet (nothing has broken; there
    is simply nobody configured to name)."""

    id: str
    order_inquiry_id: str
    ordinal: int
    state: str
    requested_by: Optional[str] = None
    requested_by_name: Optional[str] = None
    requested_at: Optional[datetime] = None
    note: Optional[str] = None
    reserved_by_name: Optional[str] = None
    reserved_at: Optional[datetime] = None
    cancelled_at: Optional[datetime] = None
    rows: List[OrderInquiryReserveRequestRowOut] = []
    notified_name: Optional[str] = None
