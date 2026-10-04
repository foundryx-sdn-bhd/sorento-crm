"""Order inquiry derivation, the SCM handoff and the Excel (P10, AC-I1 to AC-I7).

The netting and the verb rule are the pure engine next door
(``project_order_inquiry_engine.py``). This file is everything around them: where the
covering pools come from, where the stock location comes from, how the rows are written
once and only once, how purchasing is handed them, and how they leave the system as the
spreadsheet the client already reads.

Five things worth knowing before changing anything here.

**The standard demand row is the confirmed Buy residual, and only that**
(`PLAN-scm-front-planning.md` section 4). `refresh_for_decision` is its ONLY writer and it
runs inside the atomic CS confirmation. Publish writes no inquiry and reconciliation writes
none (AC-D01), because a published order may be covered entirely by Reserve, Borrow or
timely SPO cover and ordering all of it would buy it twice. The netting engine below still
serves AMENDMENTS, whose exception verbs are a different thing from new demand.

**The inquiry is never a second source of demand** (AC-I6). Committed quantity lives on
`sales_order_lines` and the SCM reorder engine reads that, exactly as it does today.
These rows say what to DO about that quantity. The only thing they are read back for is
the coverage LEDGER below, which is a record of what a pool has already been promised
to, not a record of what anybody has ordered.

**The covering pool is consumed across publishes, not just within one.** Publishing a
second sales order against a project whose pre-order is already spoken for must not net
against the same 5,950 twice, so the pool is reduced by every row that already claims
it. ``covered_by`` is the key for that: the engine writes a stable label, not free text.
It stays NULL on a confirmed-Buy row: nothing covers it, because CS already removed the
covered part of the line.

**The stock location is never invented** (AC-H5). It is the warehouse on a CONFIRMED
allocation from slice P9. No confirmation yet means the column is empty, and the screen
and the spreadsheet both say so rather than defaulting to the master location.

**Purchasing is handed a task, not an email** (AC-I4). It is a `project_tasks` row on
the delivery phase, linked to the inquiry, plus an in-app notification. The rows stay in
`project_order_inquiry_rows` and the task points at them, so marking one actioned
updates the one record rather than a copy pasted into a description.
"""
from __future__ import annotations

import io
import logging
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

from sqlalchemy import event, func, or_, tuple_
from sqlalchemy.orm import Session

from app.models.base import company_scope, get_company_scope
from app.models.inventory import Warehouse
from app.models.order import Customer, SalesOrder, SalesOrderLine
from app.models.procurement import (
    InboundShipment,
    ProductSupplier,
    PurchaseOrder,
    PurchaseOrderLine,
    SPOAllocation,
    Supplier,
)
from app.models.product import Product
from app.models.product_companion import ProductCompanionRule, ProductCompanionRuleHost
from app.services.product_companion_service import (
    bundled_with_item_codes_map as _bundled_with_item_codes_map,
    resolve_bundled_item_codes as _resolve_bundled_item_codes,
)
from app.models.project_so import (
    ACK_ACKNOWLEDGED,
    ACK_AWAITING,
    ACK_CHANGED,
    ACK_LINKABLE,
    ACK_REJECTED,
    INQUIRY_ACTIONED,
    INQUIRY_CANCELLED,
    INQUIRY_LINK_STATES,
    INQUIRY_PARTLY_LINKED,
    INQUIRY_PLACED,
    INQUIRY_RAISED,
    IV_ADVANCE,
    IV_ALREADY_INBOUND,
    IV_CANCEL_BALANCE,
    IV_CHANGE_SO,
    IV_DELAY,
    IV_ORDER,
    IV_ORDER_BACK,
    IV_PRE_ORDERED,
    IV_RESERVE_AND_ORDER,
    OI_RAISE_RAISED,
    OI_RAISE_RECONFIRMED,
    SO_STATUS_AMENDED,
    SO_STATUS_PUBLISHED,
    OrderInquiry,
    OrderInquiryLink,
    OrderInquiryRaise,
    OrderInquiryRow,
    OrderInquirySuggestedLink,
    ProjectSalesOrder,
    ProjectSalesOrderLine,
    SOAmendment,
    SOLineAllocation,
    next_inquiry_no as _next_inquiry_no,
)
from app.models.projects import (
    TASK_LINK_ORDER_INQUIRY,
    TASK_PHASE_DELIVERY,
    Project,
    ProjectParty,
    ProjectPurchaseOrder,
    ProjectTask,
)
from app.services.company_scope import build_company_predicate
from app.services.error_handler import AppException
from app.services.scm import order_link_service, priority, spo_supply
from app.services.scm.front_planning_engine import DEFAULT_LEAD_TIME_DAYS
from app.services.scm.pool_predicate import is_site_pool
from app.services.scm.supply_assignment import (
    KIND_PO as SA_KIND_PO,
    KIND_SPO as SA_KIND_SPO,
    parse_supply_key,
)
from app.services.scm.group_netting import (
    GroupNetting,
    group_of_warehouse_code,
    netting_for_products,
)
from app.services.project_order_inquiry_engine import (
    CHANGE_DATE_EARLIER,
    CHANGE_DATE_LATER,
    CHANGE_QTY_DECREASE,
    CHANGE_QTY_INCREASE,
    CHANGE_REPOINT,
    POOL_INBOUND_SPO,
    POOL_PRE_ORDER,
    CoveringPool,
    DemandRow,
    net_demand,
)

logger = logging.getLogger(__name__)

_ZERO = Decimal("0")
#: Every date the inquiry number is minted by is Asia/Kuala_Lumpur (S1, R3) - matches
#: `app.models.project_so`'s own `_MY_TZ`.
_MY_TZ = ZoneInfo("Asia/Kuala_Lumpur")

#: `Session.info` key for `order_inquiry_changed_with_links` dispatches queued mid-
#: transaction and fired once the session actually commits - see
#: `_dispatch_changed_with_links` / `register_order_inquiry_post_commit_dispatch`.
_CHANGED_WITH_LINKS_PENDING_KEY = "oi_changed_with_links_pending"

#: `Session.info` key for the "order inquiry raised" notifications a purchasing task
#: queues mid-transaction, fired once the session actually commits - see
#: `_notify_purchasing` / `register_order_inquiry_post_commit_dispatch`.
_PURCHASING_NOTIFY_PENDING_KEY = "oi_purchasing_notify_pending"

#: `Session.info` key for the `order_inquiry_handover` parallel-run email
#: (`PLAN-scm-oi-handover-email.md`) queued mid-transaction and fired once the session
#: actually commits - see `_record_handover` / `register_order_inquiry_post_commit_dispatch`.
_HANDOVER_PENDING_KEY = "oi_handover_pending"

#: `Session.info` key holding the transactions THIS session has actually committed
#: (never rolled back), so `_fire_pending_handover` - which listens on
#: `after_transaction_end` because that is the only event carrying the concluded
#: transaction object, and fires for a ROLLBACK exactly as it does for a commit - can
#: tell the two apart (AC-H10). Populated by `after_commit`, which fires ONLY on the
#: commit path, and drained of its own entries as each is consumed.
_HANDOVER_COMMITTED_TX_KEY = "oi_handover_committed_tx"

#: `Session.info` key for the `order_inquiry_undone` email
#: (`PLAN-board-undo-last-confirm.md` "The email") queued mid-transaction by
#: `undo_last_confirm` and fired once the session actually commits - see
#: `_record_undo` / `register_order_inquiry_post_commit_dispatch`. Same shape as
#: `_HANDOVER_PENDING_KEY` / `_HANDOVER_COMMITTED_TX_KEY` above, copied not adapted.
_UNDO_PENDING_KEY = "oi_undo_pending"
_UNDO_COMMITTED_TX_KEY = "oi_undo_committed_tx"

#: #1312 (Q4, grill recommended answer): the handover email's own attachment budget -
#: Google Workspace allows 25 MB per message including ~33% base64 overhead, so 15 MB
#: of raw file bytes leaves headroom. `_build_handover_context` attaches lines-then-
#: files in queue order while the running total stays at or under this; anything past
#: it is linked in the row instead (AC-E4).
HANDOVER_ATTACHMENT_CAP_BYTES = 15 * 1024 * 1024


def _transaction_chain(session) -> List[Any]:
    """The transaction a queued item was written under, and every one above it.

    A batch apply gives EACH ORDER its own savepoint, and one order's rollback must not
    take a sibling's already-earned notification with it (review round, C2: popping the
    whole queue on `after_soft_rollback` discarded order 1's because order 2 failed). The
    chain is what makes "was this item written inside the thing that just rolled back?"
    answerable: an item is discarded only when the rolled-back transaction IS one of its
    own ancestors. Identity, not `id()` - a dead object's id can be reused.
    """
    current = session.get_nested_transaction() or session.get_transaction()
    chain: List[Any] = []
    while current is not None:
        chain.append(current)
        current = getattr(current, "parent", None)
    return chain

#: How many purchase orders `relink_to_matching_lines` walks per pass. A purchase-history
#: upload names thousands of documents in one call, and one `IN` list that long is a bad
#: plan and, on some drivers, a refused statement.
_RELINK_BATCH = 200

#: The states pre-seeded at zero on the header strip. `placed` (section G) is
#: deliberately NOT one of them - `summary()` adds it to the dict dynamically the moment a
#: placed row actually exists, so a project with none yet keeps reporting the exact four
#: keys this screen has always reported.
INQUIRY_STATES = (INQUIRY_RAISED, INQUIRY_ACTIONED, INQUIRY_CANCELLED)

# The verbs whose rows claim part of a covering pool, and so have to be counted before
# the next publish nets against the same pool again.
_COVERING_VERBS = (IV_PRE_ORDERED, IV_ALREADY_INBOUND)

# Only a row that still costs money and is not yet covered can be LINKED to a document.
# `RESERVE_AND_ORDER` is buying work exactly like `ORDER` (`BUYING_VERBS` on the frontend
# groups them the same way); the rest are either informational or already closed off.
#
# `ORDER_BACK` joined the set in section 3.I. It was kept off it while a link meant a
# purchase order and nothing else - an order back is a shortfall against something already
# ORDERED, so pointing it at a fresh purchase order said the wrong thing. Now that a link
# may name an `spo_allocations` row (part 2 section 4b) that objection is answered: an
# order back is exactly the row that should be able to name the shipping order it is owed
# against, and it is the ONLY verb allowed to.
_LINKABLE_VERBS = (IV_ORDER, IV_RESERVE_AND_ORDER, IV_ORDER_BACK)
#: The verbs whose links may name an SPO allocation. EVERY linkable verb since R5
#: (`PLAN-scm-oi-draft-links.md`, captain 27 Aug 2026): "SPO link is always one, always SPO
#: first then PO". It was the order back alone while an SPO was read as the document a
#: shortfall is owed against; the captain's reading is simpler and is the one that matches
#: the book - an open shipping-order allocation is stock already bought and on its way, so
#: an ORDER should be answered by it before a new purchase order is dealt out. The sort key
#: already put an SPO ahead of a PO, so widening the set is the whole change.
_SPO_LINKABLE_VERBS = _LINKABLE_VERBS
#: The old name, for the readers that have not been renamed yet. Same tuple.
_PLACEABLE_VERBS = _LINKABLE_VERBS


def derived_spo_open_clauses() -> tuple:
    """S5 (R-D/R-E, `PLAN-scm-oi-worklist-excel-parity.md`, coordinator's 16 Sep
    addendum + round 2 ruling): the clauses an SPO allocation must pass to be a
    DERIVED cover for a PO link's own product - open per
    `spo_supply.open_incoming_clauses()` (line open, not received, shipment not
    landed) AND `retired_at IS NULL` AND `allocated_quantity > coalesce(
    quantity_received, 0)`.

    Declared ONCE, at one seam, so `links_for_rows`' display entries and
    `OrderInquiryWorklistService`'s `kind=spo` / `linked=spo` / `spo_number` filters -
    the same rule, read from two different files - can never drift apart. The caller
    has already joined `SPOAllocation` (matched on `from_po_number` + `product_id`)
    and outer-joined `InboundShipment` on it; this states only the openness test.
    """
    return (
        SPOAllocation.retired_at.is_(None),
        SPOAllocation.allocated_quantity
        > func.coalesce(SPOAllocation.quantity_received, 0),
        *spo_supply.open_incoming_clauses(),
    )

# How the client spells each verb in the order inquiry they send today. `ALREADY_INBOUND`
# is deliberately absent: their file writes the SPO reference itself in that column
# (`202511-S0022`), which is the thing purchasing looks up.
REMARK_SPELLING = {
    IV_ORDER: "ORDER",
    IV_RESERVE_AND_ORDER: "RESERVE & ORDER",
    IV_ADVANCE: "ADVANCE",
    IV_DELAY: "DELAY",
    IV_CHANGE_SO: "CHANGE SO NO",
    IV_CANCEL_BALANCE: "CANCEL BALANCE",
    IV_PRE_ORDERED: "PRE-ORDERED, DO NOT ORDER",
    IV_ALREADY_INBOUND: "ALREADY INBOUND",
    # Not a spelling of theirs: this row is new to them, and it says what it is.
    IV_ORDER_BACK: "ORDER BACK",
}

# AC-H17's fixed vocabulary order for the handover email's `verbs` / `headline`: ORDER,
# RESERVE & ORDER, ORDER BACK, PRE-ORDERED, ALREADY INBOUND, ADVANCE, DELAY, CHANGE SO NO,
# CANCEL BALANCE, RELEASE. Its own tuple rather than `REMARK_SPELLING`'s keys in
# declaration order: a raised row's own spelling ("PRE-ORDERED, DO NOT ORDER") carries the
# instruction to purchasing, and the headline only needs the bare verb name.
#
# `IV_RELEASE` deliberately absent (review round 1 nit): nothing today ever produces it.
# `verb_for` in `project_order_inquiry_engine.py` returns it for `change == CHANGE_RELEASE`,
# but that constant is imported into `planning_change_service.py`'s `_oi_demand_rows` and
# never assigned to a row's `"change"` key there (only `CHANGE_DATE_LATER`/`_EARLIER` and
# `CHANGE_QTY_DECREASE` are), and `derive_for_amendment`'s own `_DELTA_VERB_CHANGE` table
# has no entry that maps to it either - so no live seam can ever raise a `RELEASE` row for
# `_record_handover` to see. Carrying dead vocabulary here would be a label nothing can
# earn; the day a producer exists, add it back beside the producer.
_HANDOVER_VERB_ORDER = (
    IV_ORDER,
    IV_RESERVE_AND_ORDER,
    IV_ORDER_BACK,
    IV_PRE_ORDERED,
    IV_ALREADY_INBOUND,
    IV_ADVANCE,
    IV_DELAY,
    IV_CHANGE_SO,
    IV_CANCEL_BALANCE,
    "CHANGE_ITEM_CODE",
)
_HANDOVER_VERB_LABEL = {
    IV_ORDER: "ORDER",
    IV_RESERVE_AND_ORDER: "RESERVE & ORDER",
    IV_ORDER_BACK: "ORDER BACK",
    IV_PRE_ORDERED: "PRE-ORDERED",
    IV_ALREADY_INBOUND: "ALREADY INBOUND",
    IV_ADVANCE: "ADVANCE",
    IV_DELAY: "DELAY",
    IV_CHANGE_SO: "CHANGE SO NO",
    IV_CANCEL_BALANCE: "CANCEL BALANCE",
    # OI-PRODUCT-FOLLOW R4 (review S1): a product-only settle earns no qty/date verb, and
    # the email's headline would otherwise print blank.
    "CHANGE_ITEM_CODE": "CHANGE ITEM CODE",
}


def _handover_settle_diff(
    row: Any, was: Optional[Dict[str, Any]], qty: Optional[Decimal] = None
) -> Tuple[Optional[str], Optional[str], Optional[Decimal]]:
    """The date verb and the qty verb a settle earns, read off the row's CURRENT values
    against the `was` a settle-in-place captured before overwriting them.

    Shared by `handover_remark` (which turns this into the sentence purchasing reads) and
    the handover drain (which turns it into the `verbs` bucket) so the two can never read
    a settle differently.

    `qty` (EMAIL-HANDOVER-QTY, PR #1392): the LINE'S new total when the line is told as
    one settled line off a row whose own `qty` never moved - `_write`'s declined-settle
    path, where the existing placed row keeps its quantity and the netting raises the
    remainder as its own row. `None` reads `row.qty`, as every settle-in-place does.
    """
    date_key: Optional[str] = None
    old_date = (was or {}).get("delivery_date")
    if old_date is not None and row.delivery_date:
        if row.delivery_date < old_date:
            date_key = IV_ADVANCE
        elif row.delivery_date > old_date:
            date_key = IV_DELAY
    qty_key: Optional[str] = None
    qty_diff: Optional[Decimal] = None
    old_qty = (was or {}).get("qty")
    if old_qty is not None:
        diff = _dec(row.qty if qty is None else qty) - _dec(old_qty)
        if diff < _ZERO:
            qty_key, qty_diff = IV_CANCEL_BALANCE, -diff
        elif diff > _ZERO:
            qty_key, qty_diff = IV_ORDER, diff
    return date_key, qty_key, qty_diff


def _handover_verb_keys(
    kind: str, row: Any, was: Optional[Dict[str, Any]], qty: Optional[Decimal] = None
) -> List[str]:
    """Which entries of `_HANDOVER_VERB_ORDER` this one handover line earns. `qty` as
    `_handover_settle_diff` reads it."""
    if kind == "raised":
        keys = [row.verb] if row.verb else []
        if (was or {}).get("item_code"):
            keys.append("CHANGE_ITEM_CODE")
        return keys
    if kind == "cancelled":
        return [IV_CANCEL_BALANCE]
    if kind == "settled":
        date_key, qty_key, _qty_diff = _handover_settle_diff(row, was, qty)
        item_key = "CHANGE_ITEM_CODE" if (was or {}).get("item_code") else None
        return [key for key in (date_key, qty_key, item_key) if key]
    return []


def handover_remark(
    kind: str, row: Any, was: Optional[Dict[str, Any]], qty: Optional[Decimal] = None
) -> str:
    """The REMARK cell of the handover email, table-tested (AC-H2/H3/H4/H5, PLAN 3.3).

    `row` reads `verb`, `qty`, `delivery_date`, `cited_document` and `note` - either a real
    `OrderInquiryRow` or a lightweight stand-in carrying the same attributes, which is
    exactly why this is a pure function rather than a method: it never queries anything.

    * `kind="raised"`: the verb's own spelling (`REMARK_SPELLING`), an `ORDER_BACK`
      appending its cited document, and any CS `note` appended after " - ".
    * `kind="settled"`: the date verb (ADVANCE/DELAY) and the qty phrase (`CANCEL BALANCE
      N NOS` / `ORDER N`), whichever of the two actually moved, joined by ", " when both did.
    * `kind="cancelled"`: `CANCEL BALANCE <old qty> NOS` - the honest end of a line the book
      reduced to nothing.

    `qty` as `_handover_settle_diff` reads it: the line's new total for a settled line
    told off a row whose own quantity did not move.
    """
    if kind == "raised":
        label = REMARK_SPELLING.get(row.verb, row.verb or "")
        if row.verb == IV_ORDER_BACK and getattr(row, "cited_document", None):
            label = f"{label} {row.cited_document}"
        note = getattr(row, "note", None)
        # AC-R2-06: a `was` carrying `qty` or `delivery_date` means the r2 layout
        # already prints the previous value in its own CHANGE TO columns, so the
        # note's own ISO-dated sentence ("Was 2026-08-25") would only repeat it in a
        # different format - the REMARK stays the bare verb. A CHANGE SO row's `was`
        # carries `so_number`, not a date/qty, so its note (naming the source order)
        # still prints.
        skip_note = bool(was) and ("qty" in was or "delivery_date" in was)
        if note and not skip_note:
            label = f"{label} - {note}"
        old_code = (was or {}).get("item_code")
        if old_code:
            # OI-PRODUCT-FOLLOW R4: a fresh row raised on the line's new product.
            label = (
                f"{label}, CHANGE ITEM CODE TO "
                f"{getattr(row, 'item_code', None) or '-'} (WAS {old_code})"
            )
        return label
    if kind == "settled":
        date_key, qty_key, qty_diff = _handover_settle_diff(row, was, qty)
        parts: List[str] = []
        if date_key:
            parts.append(REMARK_SPELLING.get(date_key, date_key))
        if qty_key == IV_CANCEL_BALANCE:
            parts.append(f"CANCEL BALANCE {_qty_str(qty_diff)} NOS")
        elif qty_key == IV_ORDER:
            parts.append(f"ORDER {_qty_str(qty_diff)}")
        # OI-PRODUCT-FOLLOW R4: AutoCount swapped the line's product and Confirm moved
        # the row onto it - purchasing reads the change in the same REMARK cell.
        old_code = (was or {}).get("item_code")
        if old_code:
            parts.append(
                f"CHANGE ITEM CODE TO {getattr(row, 'item_code', None) or '-'} (WAS {old_code})"
            )
        return ", ".join(parts)
    if kind == "cancelled":
        old_qty = (was or {}).get("qty")
        return f"CANCEL BALANCE {_qty_str(_dec(old_qty))} NOS"
    return ""


def _handover_fmt_date(value: Any) -> Optional[str]:
    """`21/07/2026`, the way the manual mail spells a date (AC-H17)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.strftime("%d/%m/%Y")
    return str(value)


def _format_handover_was(was: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """`was` as recorded, with its two FORMATTABLE keys turned into the same strings the
    line itself prints (`_qty_str` / dd-mm-yyyy) - every other key (a CHANGE SO NO row's
    `so_number` / `customer` / `project`) passes through unchanged (AC-H6)."""
    if not was:
        return None
    formatted: Dict[str, Any] = {}
    for key, value in was.items():
        if key == "qty":
            formatted[key] = _qty_str(_dec(value))
        elif key == "delivery_date":
            formatted[key] = _handover_fmt_date(value)
        else:
            formatted[key] = value
    return formatted


# The headings on `(04).03.2026 MARYAM TUJU RESIDENCE.xlsx`, committed to the golden set
# as `e2e/fixtures/project-cs/expected-order-inquiry-2026-03-04.xlsx`. Read off the file
# rather than retyped: this is the spreadsheet purchasing already works from, and a
# renamed column is a column their own filters stop finding.
EXPORT_TITLE = "ORDER INQUIRY"
EXPORT_SHEET = "NEW"
EXPORT_HEADINGS = (
    "SO DATE",
    "S/O NO",
    "ITEM CODE",
    "QTY",
    "DELIVERY DATE",
    "PROJECT/CUSTOMER",
    "STOCK LOCATION",
    "REMARK",
)

# How an amendment's own verb reads as a change to this line. The delta service spells
# its verbs the way the client writes them; the inquiry stores the AC-I2 constants.
_DELTA_VERB_CHANGE = {
    "DELAY": CHANGE_DATE_LATER,
    "ADVANCE": CHANGE_DATE_EARLIER,
    "CANCEL BALANCE": CHANGE_QTY_DECREASE,
    "CHANGE SO NO": CHANGE_REPOINT,
    "ORDER": CHANGE_QTY_INCREASE,
    "RESERVE & ORDER": CHANGE_QTY_INCREASE,
}


def next_inquiry_no(db: Session, company_id: str, ref_date: Optional[date] = None) -> str:
    """`OI-2609-0001` for this company and month, from the ONE minting function
    (`app.models.project_so`).

    Re-exported here rather than reimplemented: the `before_insert` stamp on the model
    already guarantees every inquiry gets a number, and a second series generator in this
    file would be a second answer to the same question the day the two drifted.

    `ref_date` defaults to today, Asia/Kuala_Lumpur (S1, R3) - the same timezone the
    model's own `before_insert` listener converts `raised_at` through.

    Deliberately NOT routed through `NumberingService` the way `PSO-000001` optionally is:
    nothing seeds a rule for this document type, so that branch would be a configuration
    surface with no configuration behind it. If a client ever wants to word their own
    inquiry numbers, that is the moment to add it.
    """
    if ref_date is None:
        ref_date = datetime.now(timezone.utc).astimezone(_MY_TZ).date()
    return _next_inquiry_no(db, company_id, ref_date)


def flag_rows_for_cancelled_lines(
    db: Session, core_line_ids: Sequence[Any]
) -> int:
    """PLAN-oi-cancelled-line-used-confirm.md (AC-CL-6/7, C1): a sales order line just
    transitioned to `cancelled` - every LIVE order inquiry row of that line (state not
    `cancelled`, ACTIONED included) that purchasing had already acknowledged goes back
    to To confirm, `changed_at` set, the SAME handshake `_settle_row_in_place` uses when
    CS amends a row purchasing had already taken on.

    ONE function, called from the two write sites (`SalesOrderService._upsert_lines`,
    `document_ingest_service`) with only the core line ids THAT CALL'S OWN write just
    cancelled - never every cancelled line in the company. That is what makes AC-CL-7
    true: a second push of an already-cancelled document, or an edit that leaves a
    cancelled line cancelled, names no transitioned id at all, so this is never called
    for it and a row purchasing has re-confirmed does not come back.

    A row already `awaiting`, `changed` or `rejected` is left as it is - there is
    nothing new to tell purchasing about a row it has not yet confirmed, or has already
    been told about, or CS has already re-decided. A row whose OWN state is `cancelled`
    is left alone too - a dead row is nobody's work either way.
    """
    ids = [cid for cid in core_line_ids if cid]
    if not ids:
        return 0
    now = datetime.utcnow()
    rows = (
        db.query(OrderInquiryRow)
        .join(
            ProjectSalesOrderLine,
            ProjectSalesOrderLine.id == OrderInquiryRow.so_line_id,
        )
        .filter(
            ProjectSalesOrderLine.core_sales_order_line_id.in_(ids),
            OrderInquiryRow.state != INQUIRY_CANCELLED,
            OrderInquiryRow.ack_state == ACK_ACKNOWLEDGED,
        )
        .all()
    )
    for row in rows:
        row.ack_state = ACK_CHANGED
        row.changed_at = now
    if rows:
        db.flush()
    logger.info(
        "flag_rows_for_cancelled_lines: %d core line id(s), %d row(s) flagged",
        len(ids),
        len(rows),
    )
    return len(rows)


def _dec(value: Any, default: Decimal = _ZERO) -> Decimal:
    if value is None:
        return default
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except Exception:  # noqa: BLE001 - a malformed stored number is data, not a crash
        return default


def _qty_str(value: Decimal) -> str:
    """`600`, not `600.0000`. ``normalize()`` alone turns 100 into `1E+2`."""
    return format(_dec(value).normalize(), "f")


def project_customer_label(
    customer_name: Optional[str],
    project_title: Optional[str],
    is_pre_order: Optional[bool] = False,
) -> Optional[str]:
    """`BUIMACO / TUJU RESIDENCE`, the way purchasing reads the column.

    The billed party first because that is who the document is against, then the project,
    then the parking note when the order is a pre-order rather than a real commercial
    commitment (D18).

    A module-level function rather than a method because TWO screens print this column -
    the per-project inquiry and purchasing's cross-project worklist - and two screens
    spelling the same customer differently is a support call. Each supplies the three
    facts its own query already has; the rule for turning them into words lives here.
    """
    parts = [part for part in (customer_name, project_title) if part]
    if is_pre_order:
        parts.append("PRE-ORDER")
    return " / ".join(parts) if parts else None


def project_title_with_note(
    project_title: Optional[str],
    is_pre_order: Optional[bool] = False,
) -> Optional[str]:
    """The split worklist's own Project cell
    (`PLAN-oi-worklist-split-customer-project.md`): the same "PRE-ORDER" note
    `project_customer_label` appends to the combined column, carried on the Project half
    now that Customer and Project print as two columns instead of one, so a pre-order row
    still reads as one.
    """
    if not is_pre_order:
        return project_title
    return f"{project_title} / PRE-ORDER" if project_title else "PRE-ORDER"


def _as_date(value: Any) -> Optional[date]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def arrives_outside_window(
    expected_date: Optional[date], delivery_date: Optional[date], lead_days: int
) -> bool:
    """True when a document's promised arrival is a full lead time (or more) BEFORE the
    row's delivery date - the stock would sit for a whole buying cycle before this row
    needs it, so a nearer row should have it. Either date missing -> False.

    The ONE predicate both the worklist's reallocate/unlink pill
    (`_attach_link_suggestions`) and the cascade (`auto_place_for_products`) read, so the
    two can never drift apart by another route (`PLAN-oi-cascade-skip-early-arrival.md`).
    """
    if expected_date is None or delivery_date is None:
        return False
    return expected_date <= delivery_date - timedelta(days=lead_days)


#: The location tiers a link candidate is ranked by (Q5, ruled 25 August 2026). NEVER a
#: filter: "candidate lines are ranked by location fit, never filtered out", because a
#: purchase order that lands at the pool still covers a line standing at BRW-IB - it just
#: has to be moved, and the tier is what tells the buyer which split to key into AutoCount.
TIER_SAME_LOCATION = 1
TIER_SAME_GROUP = 2
TIER_POOL = 3
TIER_SIBLING = 4
TIER_ELSEWHERE = 5


def _also_cited(note: Optional[str]) -> List[str]:
    """The EXTRA documents a form remark named, read back off the row's note.

    `order_inquiry_rows` has one `cited_document` column and a remark routinely names two,
    so the rest are written behind a fixed prefix
    (`project_order_inquiry_import_service.ALSO_CITED_PREFIX`) and read back here.

    ONLY that segment is parsed, never the whole note. The note also carries the cascade's
    own "Linked to 202607-S0031 (...)" stamp and the relocation a book re-upload wrote, and
    reading a document out of those would make an already-linked row cite the document it is
    already sitting on - pinning the walk to its own past, which is the exact trap
    `cited_document` was given its own column to avoid.

    Uses the READER's document-number pattern rather than a second copy of it, so the two
    cannot come to disagree about what a document number looks like.
    """
    from app.services.project_order_inquiry_import_service import ALSO_CITED_PREFIX
    from app.services.project_order_inquiry_reader import _PO_NUMBER

    text_value = str(note or "")
    at = text_value.find(ALSO_CITED_PREFIX)
    if at < 0:
        return []
    segment = text_value[at + len(ALSO_CITED_PREFIX):].split(";")[0]
    return [str(found).strip().upper() for found in _PO_NUMBER.findall(segment)]


def link_location_tier(
    row_location: Optional[str],
    candidate_location: Optional[str],
    pool_codes: set,
) -> Tuple[int, int]:
    """How well a document line's location fits the row's own, as `(tier, sub-rank)`.

    For a row at `BRW-IB`: `BRW-IB` is tier 1; `DC1-IB` / `MWH-IB` (the same ownership
    group at another site) tier 2; a POOL - `BRW` first, then the others - tier 3; a
    sibling at the same site such as `BRW-BB` tier 4; anything else tier 5.

    The sub-rank exists for tier 3 alone and orders the row's OWN site pool ahead of the
    others, which is the "(3) the site pool (BRW, then the other pools)" of the ruling. It
    is a second element rather than a fourth tier so the tier a person reads stays the four
    the plan names.

    Whether a code is a POOL is decided by the FK - the set of warehouses that are some
    location's `pool_warehouse_id` - not by the code's shape. Every pool on the live book
    happens to be a plain site code with no hyphen, but that is a naming convention the
    data does not enforce, and `_pool_codes` reads the same authority the ladder does.

    A row that names NO location can be ranked by nothing, so every candidate is tier 5 and
    the dates decide - which is honest, rather than pretending a fit nobody stated.
    """
    if not row_location or not candidate_location:
        return TIER_ELSEWHERE, 0
    own = str(row_location).strip().upper()
    other = str(candidate_location).strip().upper()
    if own == other:
        return TIER_SAME_LOCATION, 0
    own_site, _, own_group = own.partition("-")
    other_site, _, other_group = other.partition("-")
    if own_group and other_group and own_group == other_group:
        return TIER_SAME_GROUP, 0
    if other in pool_codes:
        return TIER_POOL, 0 if other == own_site else 1
    if other_site and other_site == own_site:
        return TIER_SIBLING, 0
    return TIER_ELSEWHERE, 0


#: How a caller says WHICH horizon it means (S1, `PLAN-scm-oi-handshake.md` section 11).
#: A date lives in `link_up_to`; this field never carries one, so "no horizon" is never a
#: magic string inside a date field.
LINK_HORIZON_DATE = "date"
LINK_HORIZON_PLAN = "plan"
LINK_HORIZON_NONE = "none"


class ProjectOrderInquiryService:
    """Derives, serves, exports and closes off what purchasing is told to do."""

    #: AC-RL-50 (security review, 17 Sep): `follow_book_repairing` processes at most
    #: this many `moves` in one call - each one fans out into several queries, and
    #: nothing else bounds how many an ESB push can name in a single request.
    FOLLOW_BOOK_REPAIRING_MAX_MOVES = 200

    #: AC-FB-24 (`PLAN-oi-follow-book-chain.md`, S2): `follow_book_for_rows`' own
    #: sibling cap - same figure, same reason: a single ESB push (the PO/SPO
    #: ingest hooks) can name arbitrarily many rows, and each one fans out into
    #: several queries.
    FOLLOW_BOOK_FOR_ROWS_MAX_ROWS = 200

    def __init__(self, db: Session):
        self.db = db
        # Which warehouses are somebody's pool, read once per service instance: the link
        # tier asks it for every candidate of every row, and it does not change inside one
        # request.
        self._pool_codes_cache: Optional[set] = None
        # What every link already claims, per target. `auto_place_for_products` asks for
        # it once per ROW through `_candidates_for_row`, which on a full pass is two
        # aggregate queries per row over the whole link table. Cached here and dropped by
        # `_invalidate_link_cache` on every write, so the cascade cannot read a total it
        # has already changed.
        self._linked_by_target_cache: Optional[Tuple[Dict[str, Decimal], Dict[str, Decimal]]] = None
        # G7 dedication evidence per candidate LINE (`PLAN-scm-reorder-oi-feedback-1sep.md`
        # S6), filled by `_prime_claims` for the targets actually under consideration and
        # dropped by the same invalidation `_linked_by_target` uses - a claim written
        # mid-pass must not be read stale by the next row.
        self._claims_cache: Dict[str, Sequence[Dict[str, Any]]] = {}
        # Ladder v4's availability reader (`app.services.scm.group_netting`), over the
        # products this instance has been asked about. A candidate walk needs to know what
        # the group it would link into already owes, and a listing asks the same question
        # of fifty products at once.
        self._netting_value: Optional[GroupNetting] = None
        self._netted_products: set = set()
        # Which ownership groups hold an ACKNOWLEDGED, still-unlinked row of a product,
        # and WHICH rows those are - the deficit exemption's evidence (B1, code review
        # 27 Aug 2026). Five uncached queries per product, and the cascade asks it once
        # per row, so it is answered per product and remembered for the instance.
        self._awaiting_link_cache: Dict[str, Dict[str, set]] = {}
        # This service instance's own answer to "what SO does this row's claim identity
        # name" (`claim_identity`'s first element), memoised per row: the cascade calls
        # `_candidates_for_row` once per row and the identity costs two queries to derive.
        self._so_number_cache: Dict[str, Optional[str]] = {}
        # AC-H25: `_record_handover` runs once per line a write touches, and a 341-line
        # confirm asked for the SAME sales order's facts (and often the same actor) on
        # every one of them - `_handover_order_facts` / `_handover_actor` memoise per
        # instance instead of re-querying every time, the same reasoning as every cache
        # above. Not invalidated mid-instance: nothing here writes to `ProjectSalesOrder`,
        # `Project`, `Customer` or `users` while a confirm is raising rows against them.
        self._handover_order_facts_cache: Dict[str, Dict[str, Any]] = {}
        self._handover_actor_cache: Dict[str, Optional[Dict[str, str]]] = {}
        # AC-2 (issue #1166, reviewer nit): `_record_handover`'s own `so_line_id ->
        # line_no` read, memoised the same way. `_append_still_raised_amendment_rows`
        # preloads this in ONE query for every row it is about to queue - a re-confirm
        # carrying thirty still-raised amendment rows would otherwise cost thirty PK
        # round trips, one per `_record_handover` call, inside the same transaction.
        # #1312: widened to also carry `core_sales_order_line_id` - the id the line's
        # attachments key on - off the SAME `ProjectSalesOrderLine` read, so this stays
        # one round trip rather than two.
        self._handover_line_no_cache: Dict[str, tuple[Optional[int], Optional[str]]] = {}
        # R7's own-arrival credit, asked once per ROW by the path picker
        # (`_own_arrival_credit_for_row`). A replan settles every row of an order in one
        # call, and each row used to build a fresh `ProjectSupplyService` (throwing away
        # its own memos) and a fresh `netting_for_products` read of the same product
        # (review round, SF3). Both live for the instance now - the same lifetime every
        # other cache above has, and nothing here writes stock or purchase-order receipts.
        self._own_arrival_supply: Optional[Any] = None
        self._own_arrival_netting: Dict[str, List[Any]] = {}
        # S2 (second review round): `own_arrival_credit_for`'s own running ledger,
        # `location code -> what is left of it` (plus sibling-spare keys), product-scoped
        # the same way `_own_arrival_netting` above is. A replan settles several rows of
        # ONE order in one call, and without a ledger threaded through, each row re-read
        # the SAME physical floor fresh and could each be credited off it in full. Rows
        # are processed in the order the caller iterates them (date order), so the first
        # row a bin's credit covers spends it and a later row at the same bin sees what
        # is left, not the whole pile again.
        self._own_arrival_left: Dict[str, Dict[str, Decimal]] = {}
        # B2 (round 3): `_own_arrival_credit_for_row`'s own THEORETICAL credit
        # (`_own_arrival_credit_components`'s read-only answer - tier1_qty/tier2 kept
        # alongside so a later charge spends the SAME components a first call sized),
        # memoised per ROW id so a second call never re-reads `_own_arrival_left` and
        # sizes a smaller theoretical off its own prior charge to itself.
        self._own_arrival_row_theoretical: Dict[str, Tuple[Any, ...]] = {}
        # S-2 (round 5): what has actually been CHARGED for this row so far, so a second
        # call with a LARGER `need` than the first charges only the delta beyond it
        # (never double-charging, never exceeding the theoretical above) instead of
        # freezing the first call's answer forever.
        self._own_arrival_row_charged: Dict[str, Decimal] = {}

    # ------------------------------------------------------------- derivation

    def refresh_for_decision(
        self,
        order: ProjectSalesOrder,
        decision: Any,
        buy_lines: Sequence[Dict[str, Any]],
        *,
        actor_user_id: Optional[str] = None,
        borrow_shortfalls: Sequence[Dict[str, Any]] = (),
        settle_in_place_line_ids: Sequence[str] = (),
        uncover_reason_by_line: Optional[Mapping[str, str]] = None,
    ) -> Dict[str, Any]:
        """The Buy-only handoff, written INSIDE the atomic confirmation (PLAN section 4).

        The only creator of standard demand rows. Publish creates none and reconciliation
        creates none (AC-D01): a published-but-unconfirmed order may be covered entirely by
        Reserve, Borrow or timely SPO cover, and ordering all of it would buy it twice.

        What reaches purchasing is the confirmed Buy residual and nothing else - no netting
        pass, no coverage verbs, `covered_by` NULL. The evidence for why the rest of the
        line needs nothing bought belongs to the decision, not to a purchasing instruction.

        Three lifecycle rules, all of them AC-C07/AC-D05:

        * a still-unplaced row from a superseded revision is CANCELLED with the revision
          that replaced it named, never edited in place;
        * a row purchasing already actioned STAYS. Placed supply is in the ledger and this
          service does not get to rewrite history;
        * when the new need is lower than what was already placed, the difference becomes a
          `CANCEL_BALANCE` exception row stating both figures, so somebody answers it.

        Since partial confirmation (PLAN-fulfilment-planning-from-autocount-so.md 13.4) a
        revision covers the lines the planner chose. A line the previous revision covered
        and this confirmation did not name is CARRIED into the new revision by
        `ProjectSupplyService.confirm` and arrives here in `buy_lines` like any other, so
        its Buy stays on purchasing's list. `_retire_uncovered_rows` still cancels the
        still-raised rows of a line genuinely absent from the revision (one no longer on
        the order): that line is undecided again and its whole open quantity goes back to
        counting as demand, so a raised Buy row left behind would be the same requirement
        told to purchasing twice. Cancelled by the same rule and with the same words as
        any other superseded row - never deleted, because they are what purchasing was
        told.

        `borrow_shortfalls` is the fourth thing purchasing is handed, and the only one that
        is not about the borrowing line's own quantity: a borrow that pushed a DONOR
        location below zero availability opened a hole at THAT location, and it is raised
        there under its own verb (PLAN 13.11). A donor the borrow left covered raises
        nothing.

        `created` counts the rows this confirmation ADDED to purchasing's list. A carried
        line (`carried: True`) has its still-raised row moved under this revision - a
        cancel and a re-raise, so `confirmed_unplaced_buy_rows` keeps seeing it under the
        ACTIVE decision - but purchasing already had that row, so it is not counted.

        `settle_in_place_line_ids` names the lines a PLANNING CHANGE is applying (part 3,
        AC-P3-5), and on those the supersede-and-re-raise above is the wrong shape: the
        book moved the SAME instruction, so the row is UPDATED - same id, new quantity,
        new date, every link kept, the previous value on its note - and no second raised
        row is created for a line that already has one. `_settle_row_in_place` holds the
        rule, including the over-cover unlink (AC-P3-8) that replaces the CANCEL_BALANCE
        exception for a drop the row can simply absorb. Only where the line has exactly
        ONE still-owed row: where it has two, this build has no way to say which of them
        the book moved, and inventing an answer is worse than the supersede it already
        does.

        `uncover_reason_by_line` (S2/S3, `PLAN-board-reject-on-confirmed-line.md`, rework
        fix round): the bare per-line reason for a line `ProjectSupplyService.confirm`'s
        own `uncover_line_ids` dropped - passed straight through to `_retire_uncovered_
        rows`, so a withdrawn line's row reads "Taken out of the confirmation: <its own
        reason>" rather than the blanket "Superseded by revision N" every OTHER dropped
        line (drift, a line no longer on the order) still gets.
        """
        inquiry = self._existing(order.id, None)
        if inquiry is None:
            # A header is raised only when this confirmation actually has something to
            # buy: a plan covered entirely by Reserve, Borrow or timely SPO cover has no
            # Buy residual and opened no donor hole, so `buy_lines` and
            # `borrow_shortfalls` are both empty and the loop below would write zero
            # rows. Minting the header anyway burns an OI number for nothing and leaves
            # a dangling "Order inquiries" link on the SO list that the (rows-based) OI
            # worklist never shows anything for (prod OI-000020, local OI-000007).
            # S3: a local Buy raises nothing, so it never justifies minting a header on
            # its own - only an OVERSEAS residual (or a donor hole) does.
            will_raise = any(
                _dec(entry.get("buy_qty")) > _ZERO
                for entry in buy_lines
                if entry.get("origin") != "local"
            ) or bool(borrow_shortfalls)
            if not will_raise:
                # R9 (review round 4, item 2): a local line still has to join
                # settled_in_place even when the order mints no header at all - an
                # all-local decision on an order with no prior header would otherwise
                # return an empty list here, and the reaction pass (which never reaches
                # the loop below) would raise a DELAY/ADVANCE row nobody local buys.
                return {
                    "inquiry": None,
                    "created": 0,
                    "exceptions": [],
                    "settled_in_place": [
                        str(entry["line"].id)
                        for entry in buy_lines
                        if entry.get("origin") == "local"
                    ],
                }
            inquiry = self.ensure_inquiry(order, actor_user_id=actor_user_id)
        elif actor_user_id:
            # A reconfirm no longer RE-STAMPS the header (S1, R5 - superseding PLAN
            # section H, AC-H4): `raised_at`/`raised_by` are the header's fixed FIRST
            # raise for life, and every later reconfirm is its own
            # `order_inquiry_raises` row instead, so the General tab can still say who
            # reconfirmed and when without losing who raised it in the first place. The
            # inquiry is still deliberately reused so purchasing keeps quoting one
            # number; the rows keep their own `actioned_by` - that is purchasing's
            # answer, not CS's instruction.
            self._record_raise(inquiry, actor_user_id=actor_user_id, kind=OI_RAISE_RECONFIRMED)

        created = 0
        raised = 0
        exceptions: List[Dict[str, Any]] = []
        settle_in_place = {str(line_id) for line_id in (settle_in_place_line_ids or [])}
        # Which lines were ACTUALLY settled in place, not which were offered: the caller
        # decides whether to raise a separate DELAY / ADVANCE row on that answer, and
        # `_settle_row_in_place` declines a line whose rows it cannot read as one.
        settled_in_place: List[str] = []
        #: OI-PRODUCT-FOLLOW (review S2): product moves made on the DECLINED-settle path,
        #: told to purchasing once the line's own handover lines are queued.
        product_moves: List[Tuple[List[OrderInquiryRow], str]] = []
        for entry in buy_lines:
            # R9 (owner ruling, S9, `PLAN-oi-worklist-one-header.md`, superseding the old
            # S3 `PLAN-local-supplier-oi-routing.md` rule below): a line decided as a
            # LOCAL buy is not purchasing's job at all, so any of its still-RAISED order
            # rows are superseded exactly like any other decided line's - no fresh ORDER
            # row is raised for it (its own `buy_qty` is irrelevant, a local buy has
            # none), and it joins `settled_in_place` so the reaction pass raises no
            # DELAY/ADVANCE row for it either (AC-OH-90/91) - the same "this line was
            # decided, not dropped" list a settled or redirected line already joins.
            # PLACED/ACTIONED rows stay untouched: purchasing already bought or actioned
            # them, which is history, not an instruction still open. Scoped to ORDER/
            # ORDER_BACK only - a local line was never routed to CANCEL_BALANCE. The
            # entry STAYS in `buy_lines` (old S3 behaviour, unchanged), so
            # `_retire_uncovered_rows`'s own `covered` set still names this line -
            # harmless here, since a migrated row with no `supply_decision_id` reads to
            # that method as the amendment path and it never touches these rows anyway.
            if entry.get("origin") == "local":
                if entry.get("carried"):
                    # A carried local line is not a change at all - this confirmation
                    # named OTHER lines, and R9 only retires a line CS actively
                    # re-decided this revision (the same reason the ordinary supersede
                    # path a few lines down gates ITS OWN handover on `not carried`).
                    # Left exactly alone: the old S3 behaviour, for a line nobody asked
                    # about this time.
                    continue
                local_line = entry["line"]
                local_owned_verbs = (
                    (IV_ORDER, IV_ORDER_BACK)
                    if bool(entry.get("order_back"))
                    else (IV_ORDER,)
                )
                local_rows = (
                    self.db.query(OrderInquiryRow)
                    .filter(
                        OrderInquiryRow.order_inquiry_id == inquiry.id,
                        OrderInquiryRow.so_line_id == local_line.id,
                        OrderInquiryRow.verb.in_(local_owned_verbs),
                        OrderInquiryRow.state == INQUIRY_RAISED,
                    )
                    .all()
                )
                for row in local_rows:
                    was_qty = row.qty
                    row.state = INQUIRY_CANCELLED
                    row.note = f"Superseded by revision {decision.revision_no}"
                    self._record_handover(
                        row,
                        kind="cancelled",
                        was={"qty": was_qty},
                        actor_user_id=actor_user_id,
                    )
                # Review round 2 Blocking 4 (AC-LT-18): this supersede sets `state`
                # directly rather than through `refresh_link_state`, so a suggestion the
                # walk left on one of these rows would otherwise go on holding capacity
                # against every other row for good.
                self._drop_suggested_links(local_rows)
                settled_in_place.append(str(local_line.id))
                continue
            line = entry["line"]
            need = _dec(entry.get("buy_qty"))
            carried = bool(entry.get("carried"))
            # Is THIS line's Buy an order back (part 2 section 4b)? Only then does this
            # loop own the line's `ORDER_BACK` rows; on any other line such a row is a
            # DONOR hole raised by `_raise_borrow_shortfalls`, which supersedes and nets it
            # by its own rule. The two writers never meet on one line - the whole-line rule
            # (AC-L5) makes a line either wholly stock or wholly Buy - and this is what
            # keeps them apart.
            order_back = bool(entry.get("order_back")) and need > _ZERO
            owned_verbs = (
                (IV_ORDER, IV_ORDER_BACK, IV_CANCEL_BALANCE)
                if order_back
                else (IV_ORDER, IV_CANCEL_BALANCE)
            )
            # This sales order's OWN inquiry only. An OCN amendment (`derive_for_amendment`)
            # still raises its exception verbs under its OWN separate inquiry, and
            # cancelling those here would delete an instruction purchasing is still
            # working from. A BOOK-CHANGE reaction is different since S3: it now sits on
            # this SAME `inquiry` (the order's one `amendment_id IS NULL` header), so a
            # `CANCEL_BALANCE` row a book-change wrote for a qty decrease is correctly
            # picked up and retired here too, exactly like one this confirm raised itself
            # - one header, one supersede rule, no second inquiry to leave stale.
            rows = (
                self.db.query(OrderInquiryRow)
                .filter(
                    OrderInquiryRow.order_inquiry_id == inquiry.id,
                    OrderInquiryRow.so_line_id == line.id,
                    OrderInquiryRow.verb.in_(owned_verbs),
                )
                .all()
            )
            # A still-raised CANCEL_BALANCE exception is superseded like a raised ORDER
            # row, or every reconfirm at the same lower need would stack another copy.
            # `placed` sums ORDER rows only: the exception row is a message, not supply.
            #
            # `INQUIRY_PLACED` counts here too, and it is not a cosmetic addition: the
            # live "Place on PO" path (section G) writes rows straight to `placed`, never
            # to `actioned` - `mark_rows` is the only writer of `actioned`, and nothing on
            # the real workflow calls it for an ORDER row anymore. A predicate that only
            # recognised `actioned` was blind to every placed row in the company (145
            # placed / 0 actioned, live, 20 Aug), so a qty-up reconfirm re-raised the FULL
            # new need on top of supply that was already there (SO349754 WESERP10B: placed
            # 5 untouched, a fresh 10 raised, 15 against a 10 line).
            #
            # A PARTLY LINKED row is the case the links table added, and it is netted
            # HALF: the quantity that sits on a document is real supply and counts, and
            # the remainder is demand this revision is about to restate, so the row is
            # shrunk to what is linked rather than cancelled. Cancelling it would have
            # taken the links down with it; leaving it whole would have counted the
            # unlinked half twice, once here and once on the row raised below.
            #
            # A DRAFT settles in place too, whoever asked for the confirmation (B2 as
            # refined in the CI round, 28 Aug). Since R6 the raise links its own rows, so a
            # row nobody has confirmed reads `placed` or `partly linked` within a second of
            # being raised, and the netting below then read it as quantity purchasing had
            # already bought: a reconfirm carrying a new date changed nothing at all, and a
            # lower quantity raised a CANCEL_BALANCE exception about a purchase nobody had
            # agreed to. Settling answers both - the row takes the new date and the new
            # quantity, keeping the drafts it can still hold and giving the excess back
            # latest-dated first (AC-P3-8's own rule) - and it answers them without
            # cancelling anything, so a draft the planning change has just SHIFTED onto
            # this row stays where the shift put it.
            #
            # `_settle_row_in_place` still declines a line it cannot read as one
            # instruction (two still-owed rows, or a lone placed row carrying no link at
            # all), and those fall through to the netting below exactly as they always
            # did: the drafted rows stand and only the outstanding remainder is raised.
            # `ack_state` is not the signal here, even though a row is born AWAITING
            # again (`PLAN-oi-confirm-per-so.md` S1): linking never waits for confirm
            # (raise, Link now and the worklist's own auto-place all cascade an awaiting
            # row too), so an unread row can still hold a MANUAL link, and a confirmed
            # one can still hold nothing but the cascade's own guess. `_cascade_only`
            # reads the fact underneath that either way: a row nobody has manually linked
            # is still the cascade's own guess, and settling it in place costs nobody a
            # decision they made. Batched (S6): one grouped load for this line's own rows
            # rather than one query per row.
            drafted_links = self._links_by_row([str(row.id) for row in rows])
            # #1226, owner ruling 25 Sep 2026 ("nothing changed, nothing moves"): a
            # CARRIED line's draft is never handed to `_settle_row_in_place` - this
            # confirmation named OTHER lines, so its still-placed/partly-linked row is
            # not this revision's to settle, let alone to redirect (`_redirect_row_if_
            # received`, `_settle_row_in_place`'s own first step). Without `not carried`
            # here, a carried line's row whose link happened to have since become fully
            # received read as a draft this call could still settle, and settling
            # declined into the redirect anyway (OI-2609-0755, PLAN-oi-carried-line-no-
            # settle.md).
            drafted = [
                row
                for row in rows
                if not carried
                and row.verb in (IV_ORDER, IV_ORDER_BACK)
                and row.state in (INQUIRY_PLACED, INQUIRY_PARTLY_LINKED)
                and self._cascade_only(drafted_links.get(str(row.id), []))
                and not row.redirected_to_pool
            ]
            if carried:
                # B1 (review round 1, 9fb06ff66): `_settle_row_in_place`'s ordinary
                # settle stamps `row.supply_decision_id = decision.id` on the row it
                # touches - gate above means a carried line's live draft never reaches
                # that call, so left alone it stays tied to the OLD revision.
                # `_retire_uncovered_rows`'s `only_line_ids` mode
                # (`retire_rows_for_dropped_lines`, `ProjectSupplyService.uncover_lines`'
                # whole-revision reject branch) filters on `supply_decision_id ==
                # decision.id`, so a stale id makes the row invisible to a reject that
                # names its line: the reject would retire nothing and the row would stay
                # `placed`/`partly_linked` with its links still holding real PO/SPO
                # quantity. Re-stamped alone - qty, links, note, state and the handshake
                # all stand exactly as they are ("nothing changed, nothing moves"); only
                # which revision the row is filed under moves, the same as a carried
                # RAISED row's own replacement moves onto this revision below.
                #
                # Deliberately unscoped to `_cascade_only` - a carried row with a MANUAL
                # link, or no link at all, is restamped too. Harmless: `_retire_
                # uncovered_rows`'s own retirement loop skips a row whose links are not
                # ALL cascade-made (`if not self._cascade_only(...): continue`), so a
                # restamped manually-linked row still never gets cancelled by a reject it
                # was never eligible for - the restamp only ever widens which revision a
                # reject can FIND the row under, never what the reject does with it.
                for row in rows:
                    if (
                        row.verb in (IV_ORDER, IV_ORDER_BACK)
                        and row.state in (INQUIRY_PLACED, INQUIRY_PARTLY_LINKED)
                        and not row.redirected_to_pool
                    ):
                        row.supply_decision_id = decision.id
            # S2 (`PLAN-scm-oi-handover-r2-undo.md`, AC-R2-10/11): a NAMED line (not
            # carried, not a planning-change settle) whose only live row is a plain
            # `raised` ORDER/ORDER_BACK row, no links at all, not redirected, and whose
            # verb equals the verb THIS confirm would raise - `target_verb` below - reads
            # as the same instruction restated, not a fresh one. Widened alongside
            # `drafted` above rather than folded into it: a placed/partly-linked row
            # earned its slot by carrying only the cascade's OWN links (still a "draft"
            # nobody has manually touched), where a raised row earns it by carrying NO
            # links whatsoever - two different reasons to trust the same settle-in-place
            # call. `_settle_row_in_place` itself still declines two live rows or a verb
            # mismatch (its own `live` filter, `len(live) != 1`), so AC-R2-12's two shapes
            # (two live rows, a verb switch) fall through unchanged to the supersede path
            # below. `carried` is gated explicitly: a line riding along only because a
            # DIFFERENT line of the same order was named is not a restatement of anything,
            # so its still-raised row keeps the ordinary cancel-and-re-raise
            # (`tests/scm/test_confirm_local_buy_no_oi.py`).
            #
            # A REFUSED row is excluded too (AC-H6, captain ruling on CI round 1):
            # purchasing said no to that instruction, so re-deciding the line is a NEW
            # instruction and has to be born acknowledged under its own id - settling the
            # refused row in place would quietly re-open the very row purchasing declined,
            # keeping its `rejected_by`/`rejected_at` stamp on a live instruction and
            # leaving CS nothing to read the refusal off. The supersede path below is the
            # right answer there: it cancels the refused row (the refusal stays readable
            # on it) and raises a fresh one.
            target_verb = IV_ORDER_BACK if order_back else IV_ORDER
            named_raised = (
                []
                if entry.get("carried")
                else [
                    row
                    for row in rows
                    if row.verb == target_verb
                    and row.state == INQUIRY_RAISED
                    and not row.redirected_to_pool
                    and not drafted_links.get(str(row.id))
                    and row.ack_state != ACK_REJECTED
                    and row.rejected_by is None
                    and row.rejected_at is None
                ]
            )
            if not drafted and len(named_raised) == 1:
                drafted = named_raised
            # S4/AC-OH-40..42: every row this LINE already carried `redirected_to_pool` on,
            # before anything below touches it - a row an EARLIER decision released
            # (AC-OH-41) must never be re-read as "newly" released by this one. Whatever is
            # `redirected_to_pool` afterwards and was not in this set is what THIS call
            # redirected - whether `_settle_row_in_place` declined into it below, or the
            # netting loop's own PARTLY_LINKED branch redirects it further down.
            previously_redirected_ids = {row.id for row in rows if row.redirected_to_pool}
            asked_to_settle = str(line.id) in settle_in_place
            # EMAIL-HANDOVER-QTY: set below when the declined-settle path tells the
            # email this line's total change as ONE settled line (recorded after the
            # netting loop, off `deferred_settle`) - the netting loop then still WRITES
            # its rows (the worklist is unchanged) but prints none of them as separate
            # lines for this line. `deferred_cancels` holds the superseded rows' own
            # lines back until the loop has run: a row the netting REDIRECTS changes
            # what "before" means, and then today's per-row lines print instead.
            line_total_told = False
            deferred_settle: Dict[str, Any] = {}
            deferred_cancels: List[Tuple[OrderInquiryRow, Any]] = []
            held_qty = _ZERO
            if (asked_to_settle or drafted) and self._settle_row_in_place(
                inquiry, entry, rows, need, decision, actor_user_id=actor_user_id
            ):
                # Only what the CALLER asked for is reported back: the planning-change
                # apply reads this list to decide whether to raise a separate DELAY /
                # ADVANCE row, and a line it never named is none of its business.
                if asked_to_settle:
                    settled_in_place.append(str(line.id))
                continue
            if asked_to_settle:
                # S2 (`PLAN-board-oi-mechanical-22sep.md`, AC-B2-4..7): `_settle_row_in_
                # place` just declined - two still-owed rows, a lone placed row with no
                # link, or every row already actioned (excluded from its own `live`
                # filter outright). None of that changes what the DATE half of the change
                # should do: every buy row of the line still gets the date stamped in
                # place, and no second ADVANCE/DELAY row is raised beside it.
                #
                # The stamp is UNGATED by the quantity (review round, 22 Sep). It was
                # gated on the composed `need` matching what the line's own live buy rows
                # already total, and a book that moved the date AND the quantity then
                # moved neither: the gate failed, nothing was stamped, and the line came
                # out of the confirm with its existing row on the OLD date, a fresh
                # remainder row on the new one, and no notice either (a buy row existed,
                # so `_oi_demand_rows` suppressed it). The stamp touches no quantity and
                # no link, so it is safe either way; the quantity half stays with the
                # netting below, which still runs whenever the two disagree.
                #
                # A line with NO existing buy row at all still falls through on its own:
                # `_stamp_date_move` finds no target and returns False, and the netting
                # raises its fresh row exactly as it always has (AC-B2-4).
                live_rows = [
                    r
                    for r in rows
                    if r.verb in (IV_ORDER, IV_ORDER_BACK)
                    and r.state in (
                        INQUIRY_RAISED, INQUIRY_PARTLY_LINKED,
                        INQUIRY_PLACED, INQUIRY_ACTIONED,
                    )
                    and not r.redirected_to_pool
                ]
                live_buy_qty = sum((_dec(r.qty) for r in live_rows), _ZERO)
                # OI-PRODUCT-FOLLOW (review S2): `_settle_row_in_place` declined, but the
                # product AutoCount swapped still follows on every live buy row of the
                # line, the same three writes it would have made.
                product_move = self._restate_product(inquiry, live_rows, entry)
                if product_move:
                    product_moves.append(product_move)
                # EMAIL-HANDOVER-QTY (owner, 30 Sep; `PLAN-oi-handover-qty-change-
                # 30sep.md` S1): the line's TOTAL is about to change under the netting
                # below, and purchasing already holds something for it. No row of this
                # path carries that change on its own `qty` (the placed row keeps what
                # it holds, the remainder is raised fresh), so the email is told once,
                # in `_settle_row_in_place`'s own shape: QTY = what they held, QTY
                # CHANGE TO = the new Buy, remark = the difference. What they HELD
                # leaves out a raised row purchasing refused (review, should-fix 4):
                # a refused instruction is not one they hold. A line with NO held row
                # has no "before" to print and stays a plain raise. An ORDER BACK line
                # keeps today's per-row lines (review, should-fix 2): its remainder
                # names the cited document, which a totals line has no cell for.
                held_rows = [r for r in live_rows if r.ack_state != ACK_REJECTED]
                held_qty = sum((_dec(r.qty) for r in held_rows), _ZERO)
                # `held_qty != need` as well (review round 1, nit): a refused raised
                # row alone can make the netting run while what purchasing holds
                # already equals the need - a "5 -> 5" line with nothing to say.
                total_moved = (
                    bool(held_rows)
                    and live_buy_qty != need
                    and held_qty != need
                    and not order_back
                )
                stamped = self._stamp_date_move(
                    inquiry, rows, entry, decision, actor_user_id=actor_user_id,
                    will_net=(live_buy_qty != need),
                    defer=deferred_settle if total_moved else None,
                )
                if stamped and live_buy_qty == need:
                    # Nothing but the date moved, so the netting has nothing left to say
                    # about this line and the caller is told it is settled.
                    settled_in_place.append(str(line.id))
                    continue
                if total_moved:
                    if not stamped:
                        # No date to move (or every row already carries it), so
                        # `_stamp_date_move` picked nothing: the total change is told
                        # off the same representative it would have picked - a row
                        # that survives the netting first (raised rows are seconds
                        # from being cancelled by it).
                        deferred_settle = {
                            "row": next(
                                (r for r in held_rows if r.state != INQUIRY_RAISED),
                                held_rows[0],
                            ),
                            "previous_date": None,
                            "stamped": False,
                        }
                    line_total_told = True
            # Read BEFORE the loop below cancels anything: what purchasing had already
            # taken on for this line, off the rows that are still LIVE. Taken afterwards it
            # would read the rows this loop has just cancelled, which is every superseded
            # row this line ever carried, so a line whose acknowledgement had long since
            # been superseded would keep promoting its replacements to `changed` forever.
            prior_ack = self._live_handshake(rows)
            linked = self._linked_qty_by_row([row.id for row in rows])
            placed = _ZERO
            # AC-H22: `prior_ack` is None whenever the live handshake finds nothing
            # ACKNOWLEDGED/CHANGED to point at - a row purchasing rejected, or one still
            # AWAITING - which is not the same as "nothing to compare this carry
            # against". The row this very loop is about to cancel below IS that
            # instruction, so it is kept as the fallback the handover carry-gate reads.
            cancelled_owned_row: Optional[OrderInquiryRow] = None
            # Review round 2 Blocking 4 (AC-LT-18): this loop sets `state` directly
            # rather than through `refresh_link_state`, so it has to drop each
            # cancelled row's own suggestions by hand once it is done - the board
            # re-confirm supersede path the reviewer's probe named.
            superseded_cancelled_rows: List[OrderInquiryRow] = []
            for row in rows:
                if row.state == INQUIRY_RAISED:
                    was_qty = row.qty
                    row.state = INQUIRY_CANCELLED
                    row.note = f"Superseded by revision {decision.revision_no}"
                    superseded_cancelled_rows.append(row)
                    if row.verb in (IV_ORDER, IV_ORDER_BACK) and cancelled_owned_row is None:
                        cancelled_owned_row = row
                    if not carried and not line_total_told:
                        # AC-H23, narrowed by AC-R2-12 (S2): a single-raised-row same-
                        # verb line is caught by `named_raised` above now, so what
                        # reaches here is only a genuine supersede - two still-owed
                        # rows, or a verb switch the settle above will not absorb -
                        # and purchasing has to be told the old row is gone, same as
                        # H19.
                        self._record_handover(
                            row,
                            kind="cancelled",
                            was={"qty": was_qty},
                            actor_user_id=actor_user_id,
                        )
                    elif not carried:
                        # EMAIL-HANDOVER-QTY: the line's total is being told as ONE
                        # settled line whose QTY is the sum of exactly these rows, so
                        # "CANCEL BALANCE <this row>" would say it a second time, per
                        # row - held back until the loop has shown whether it
                        # redirected anything (then today's lines print after all).
                        deferred_cancels.append((row, was_qty))
                    continue
                if row.verb not in owned_verbs or row.verb == IV_CANCEL_BALANCE:
                    continue
                # A planning change already REDIRECTED this row to replenish the pool it
                # drew on (`planning_change_service._apply_placed_redirect`, the captain's
                # ruling 21 Aug 2026) - it is still real placed quantity, just not this
                # line's anymore, so it must not net off this line's need a second time or
                # the new Buy would be silently short.
                if row.redirected_to_pool:
                    continue
                # A DRAFT that reached HERE is one the settle above declined - a line
                # carrying two still-owed rows, or a lone placed row with no link behind it
                # (B2 as refined in the CI round, 28 Aug). There is no way to say which of
                # two rows the book moved, and no draft to give back on a row that holds
                # none, so the old path stands: the drafted rows are netted and left
                # exactly where they are, and only the outstanding remainder is raised -
                # which the raise-time cascade drafts in its turn.
                if row.state == INQUIRY_ACTIONED:
                    # `mark_rows` is the only writer of this state and it carries no
                    # links, so the row's own quantity is what purchasing dealt with.
                    placed += _dec(row.qty)
                elif row.state == INQUIRY_PLACED:
                    placed += _dec(row.qty)
                elif row.state == INQUIRY_PARTLY_LINKED:
                    # AC-OH-42: this is `_settle_row_in_place`'s OWN received-document
                    # decline (AC-RL-10..14), reached here rather than there because a
                    # line with TWO still-owed rows never gets that far - `live` refuses
                    # to guess which one the book moved, so both fall through to this
                    # netting loop instead. The same rule applies at the same seam: a
                    # fully received link is not carried through a replan, whichever path
                    # found the row.
                    #
                    # BLOCKER B1 (Opus review round 1): gated on `asked_to_settle` - a
                    # PLANNING CHANGE naming this line - the same gate `_settle_row_in_place`
                    # itself sits behind above. Ungated, this ran on every ordinary confirm
                    # of a line with two still-owed rows, redirecting a row purchasing had
                    # placed by hand (a MANUAL link, `_cascade_only` false) the moment its
                    # document happened to arrive - not a replan, nobody asked this line to
                    # be restated, and the buyer's own placement should not silently become
                    # history under them.
                    if carried:
                        # #1226, owner ruling 25 Sep 2026 ("nothing changed, nothing
                        # moves"): this confirmation did not name the line, so its
                        # partly-linked row is not restated at all - the full qty nets
                        # into `placed` exactly as a PLACED row's does above, and the
                        # row itself, its links and its note stand untouched. No shrink
                        # to the linked qty, no "Remainder superseded" note, no
                        # `refresh_link_state`, no redirect.
                        #
                        # S2 (review round 1, 9fb06ff66): no `and not asked_to_settle`
                        # here - a PLANNING-CHANGE settle line is always NAMED
                        # (`planning_change_service` appends every `settle_line_ids`
                        # entry to `confirm_lines`), so `carried` and `asked_to_settle`
                        # can never both be true and the extra clause was unreachable.
                        placed += _dec(row.qty)
                        continue
                    if asked_to_settle and self._redirect_row_if_received(
                        row, drafted_links.get(str(row.id), []), decision
                    ):
                        continue
                    covered = linked.get(row.id, _ZERO)
                    placed += covered
                    row.qty = covered
                    # Through the one writer, not by hand: the row's derived display
                    # (`po_ref` / `po_line_id` / `spo_ref`) is restated with the state, and
                    # setting `placed` here alone would have left them saying whatever the
                    # last link change happened to leave.
                    self.refresh_link_state([row])
                    row.note = (
                        f"{row.note}; Remainder superseded by revision "
                        f"{decision.revision_no}"
                        if row.note
                        else f"Remainder superseded by revision {decision.revision_no}"
                    )

            self._drop_suggested_links(superseded_cancelled_rows)

            # S4/AC-OH-40..42, R4 revised/AC-OH-44: every row THIS call redirected,
            # whichever seam found it - `_settle_row_in_place`'s own single-row decline
            # above, or the netting loop's PARTLY_LINKED branch just above - earliest
            # DUE first, so "the first one's delivery_date" (AC-OH-42) is well-defined.
            # NOT `created_at`: both rows are written in the SAME apply's transaction, so
            # Postgres' `now()` ties them (LESSONS-LEARNT, "now() ties in transaction")
            # and the id tie-break would answer differently from one run to the next.
            redirected_this_call = sorted(
                (
                    row
                    for row in rows
                    if row.redirected_to_pool and row.id not in previously_redirected_ids
                ),
                key=lambda row: (row.delivery_date or date.max, str(row.id)),
            )
            if line_total_told:
                if redirected_this_call:
                    # Review (blocking 1): a row the netting just REDIRECTED left this
                    # line's "before" - `placed` no longer counts it, so the fresh row
                    # below is `need - placed`, not `need - held`, and a totals line
                    # would tell purchasing to buy less than the row asks for. The
                    # redirect has its own wording (AC-OH-40..42, the fresh row's
                    # "Replaces N used"), so this line prints today's per-row lines:
                    # the date-only settle, each superseded row, and the fresh row.
                    line_total_told = False
                    if deferred_settle.get("stamped"):
                        previous_date = deferred_settle.get("previous_date")
                        self._record_handover(
                            deferred_settle["row"],
                            kind="settled",
                            was={"delivery_date": previous_date} if previous_date else {},
                            actor_user_id=actor_user_id,
                        )
                    for cancelled_row, cancelled_qty in deferred_cancels:
                        self._record_handover(
                            cancelled_row,
                            kind="cancelled",
                            was={"qty": cancelled_qty},
                            actor_user_id=actor_user_id,
                        )
                else:
                    # The ONE line for this line: QTY = what purchasing held, QTY
                    # CHANGE TO = the new Buy, remark = the date verb (when the date
                    # moved) and the difference.
                    handover_was: Dict[str, Any] = {}
                    if deferred_settle.get("previous_date"):
                        handover_was["delivery_date"] = deferred_settle["previous_date"]
                    handover_was["qty"] = held_qty
                    self._record_handover(
                        deferred_settle["row"],
                        kind="settled",
                        was=handover_was,
                        qty=need,
                        actor_user_id=actor_user_id,
                    )
            # Did purchasing already take this line's instruction on, and is this
            # confirmation actually changing it?
            #
            # A CARRIED line is not a change at all (13.4): this confirmation named other
            # lines, and this one's still-raised row is moved under the new revision by a
            # cancel and a re-raise purely so `confirmed_unplaced_buy_rows` keeps finding
            # it. Promoting it to `changed` there told the buyer, on every confirm of any
            # OTHER line of the same order, that a row they had acknowledged had moved -
            # with no Was and no Now to show for it, because nothing had. So the carried
            # row inherits the handshake verbatim, stamps included.
            #
            # A NAMED line IS a change (AC-H9): a supersede is the same line moving under
            # them, and raising the replacement plain `awaiting` would hide from the buyer
            # that this is one they had already read. The acknowledgement stamps travel
            # with it, so the cell can still say who had taken it on.
            ack_state, acknowledged_by, acknowledged_at, changed_at = self._handshake_for_raise(
                prior_ack, carried=carried, actor_user_id=actor_user_id
            )
            outstanding = need - placed
            if outstanding > _ZERO:
                # An ORDER BACK is the same Buy said differently: the quantity is owed
                # against something already ordered or already shipped (part 2 section
                # 4b). CS marks it in Amend, optionally naming the document, and the two
                # facts travel to the row: the verb decides that an SPO allocation is a
                # legal link target, and `cited_document` is what the walk tries first.
                order_back = bool(entry.get("order_back"))
                raised_row = OrderInquiryRow(
                    company_id=order.company_id,
                    order_inquiry_id=inquiry.id,
                    so_line_id=line.id,
                    item_code=entry.get("item_code") or None,
                    qty=outstanding,
                    delivery_date=entry.get("required_date"),
                    stock_location=entry.get("stock_location"),
                    verb=IV_ORDER_BACK if order_back else IV_ORDER,
                    cited_document=(
                        entry.get("cited_document") if order_back else None
                    ),
                    # No netting on this path, so nothing covers this row: the coverage
                    # decision was CS's and is recorded on the supply decision.
                    covered_by=None,
                    supply_decision_id=decision.id,
                    state=INQUIRY_RAISED,
                    ack_state=ack_state,
                    acknowledged_by=acknowledged_by,
                    acknowledged_at=acknowledged_at,
                    changed_at=changed_at,
                )
                if entry.get("landed_note"):
                    # #1362 (owner, 29 Sep 2026): planning kept this Buy over goods that
                    # landed for the line; the link is intact and adjusting it is
                    # purchasing's call, made on this row they acknowledge anyway.
                    raised_row.note = (
                        f"Planning keeps this Buy: {entry['landed_note']} stay linked "
                        "to this line; adjust the linkage if the Buy replaces them"
                    )
                if redirected_this_call:
                    # AC-OH-40..42: the fresh row states, in one place, what old supply it
                    # replaces - the released rows' own qty (summed, AC-OH-42) and the
                    # earliest one's date, plus a note naming each document. Only the rows
                    # THIS call released (`redirected_this_call`, the diff computed above) -
                    # AC-OH-41's later reconfirm carries none, so this block never runs for
                    # it and the row's own ordinary settle value stands instead.
                    raised_row.previous_qty = sum(
                        (_dec(r.qty) for r in redirected_this_call), _ZERO
                    )
                    raised_row.previous_delivery_date = redirected_this_call[
                        0
                    ].delivery_date
                    fragments = [
                        fragment
                        for fragment in (
                            self._release_fragment(r) for r in redirected_this_call
                        )
                        if fragment
                    ]
                    # OI-PRODUCT-FOLLOW R1 (tester FAIL on 3db1012a): the rows set aside
                    # keep the product they received for (history, R2); when that is not
                    # the line's product any more, this fresh row is where the switch
                    # shows, so it carries the old code as its "was".
                    replaced_code = next(
                        (
                            r.item_code
                            for r in redirected_this_call
                            if r.item_code
                            and r.item_code != raised_row.item_code
                            and self._is_catalogue_code(r.item_code)
                        ),
                        None,
                    )
                    if replaced_code:
                        raised_row.previous_item_code = replaced_code
                        fragments.append(f"Was item {replaced_code}")
                    raised_row.note = "; ".join(
                        [f"Replaces {_qty_str(raised_row.previous_qty)} used"]
                        + fragments
                        # #1362: the landed-goods notice above is kept beside it.
                        + ([raised_row.note] if raised_row.note else [])
                    )
                    if asked_to_settle:
                        # S2 (Opus review round 1): joined HERE, where the fresh row that
                        # actually carries the Was/Now is written - not earlier, on the
                        # bare fact that something redirected. A line that redirects but
                        # then raises NOTHING (`outstanding <= 0`, its need fully met some
                        # other way) never reaches this block, and R4 revised only ever
                        # meant to suppress the DELAY/ADVANCE row for a line the confirm
                        # actually restated with a fresh ORDER row - one still owed, its
                        # own DELAY row stands.
                        settled_in_place.append(str(line.id))
                self.db.add(raised_row)
                if carried:
                    # AC-H20/AC-H22: the carry site cancels-and-re-raises even when
                    # NOTHING for purchasing changed (13.4's own reason -
                    # `confirmed_unplaced_buy_rows` needs the row moved under the new
                    # revision) - so comparing the fresh row against the one it carries
                    # is what tells a silent carry from one that actually moved. Gated
                    # on `carried` ALONE, never on `prior_ack`: a rejected or still-
                    # AWAITING row has no LIVE handshake (`_live_handshake` only reads
                    # ACKNOWLEDGED/CHANGED), but it is still the row this carry moves
                    # from, so `cancelled_owned_row` - the very row the loop above just
                    # cancelled - is read as the fallback reference. Same qty and date:
                    # nothing for purchasing to read, print nothing. Either differs: it
                    # reads exactly like an in-place settle, never a second bare ORDER.
                    carry_reference = prior_ack or cancelled_owned_row
                    if (
                        carry_reference is not None
                        and not raised_row.previous_item_code
                        and carry_reference.item_code
                        and carry_reference.item_code != raised_row.item_code
                        and self._is_catalogue_code(carry_reference.item_code)
                    ):
                        # OI-PRODUCT-FOLLOW R1: the carry re-raised the line on its live
                        # product, so the switch shows here.
                        raised_row.previous_item_code = carry_reference.item_code
                        product_note = f"Was item {carry_reference.item_code}"
                        raised_row.note = (
                            f"{raised_row.note}; {product_note}"
                            if raised_row.note
                            else product_note
                        )
                    if carry_reference is not None:
                        carry_was: Dict[str, Any] = {}
                        if raised_row.previous_item_code:
                            carry_was["item_code"] = raised_row.previous_item_code
                        if raised_row.qty != carry_reference.qty:
                            carry_was["qty"] = carry_reference.qty
                        if raised_row.delivery_date != carry_reference.delivery_date:
                            carry_was["delivery_date"] = carry_reference.delivery_date
                        if carry_was:
                            self._record_handover(
                                raised_row,
                                kind="settled",
                                was=carry_was,
                                actor_user_id=actor_user_id,
                            )
                    else:
                        # No reference at all to compare against - cannot honestly
                        # print a diff, so fall back to a plain raise rather than
                        # silently dropping a carry that HAD no prior row.
                        self._record_handover(
                            raised_row, kind="raised", actor_user_id=actor_user_id
                        )
                elif not line_total_told:
                    # EMAIL-HANDOVER-QTY: a line already told as one settled line
                    # (`ORDER n` in its remark IS this row's quantity) prints no bare
                    # ORDER line beside it. OI-PRODUCT-FOLLOW: a fresh row that replaces
                    # supply of the OLD product says so (`previous_item_code` above).
                    self._record_handover(
                        raised_row,
                        kind="raised",
                        was=(
                            {"item_code": raised_row.previous_item_code}
                            if raised_row.previous_item_code
                            else None
                        ),
                        actor_user_id=actor_user_id,
                    )
                raised += 1
                if not carried:
                    created += 1
            elif placed > need:
                message = (
                    f"Placed {_qty_str(placed)}, new need {_qty_str(need)}"
                )
                cancel_balance_row = OrderInquiryRow(
                    company_id=order.company_id,
                    order_inquiry_id=inquiry.id,
                    so_line_id=line.id,
                    item_code=entry.get("item_code") or None,
                    qty=placed - need,
                    delivery_date=entry.get("required_date"),
                    stock_location=entry.get("stock_location"),
                    verb=IV_CANCEL_BALANCE,
                    note=message,
                    supply_decision_id=decision.id,
                    state=INQUIRY_RAISED,
                    # Born AWAITING (S1, `PLAN-oi-confirm-per-so.md`): an exception row
                    # is still purchasing's to confirm like any other raised instruction -
                    # the G4 exemption that let it skip the Confirm press is retired with
                    # everything else G4 did.
                    ack_state=ACK_AWAITING,
                    acknowledged_by=None,
                    acknowledged_at=None,
                )
                self.db.add(cancel_balance_row)
                if not line_total_told:
                    # EMAIL-HANDOVER-QTY: the settled line's `CANCEL BALANCE n NOS`
                    # already says what this exception row says.
                    self._record_handover(
                        cancel_balance_row, kind="raised", actor_user_id=actor_user_id
                    )
                exceptions.append(
                    {
                        "line_no": entry.get("line_no"),
                        "item_code": entry.get("item_code"),
                        "message": message,
                    }
                )

        self._retire_uncovered_rows(
            inquiry,
            decision,
            buy_lines,
            actor_user_id=actor_user_id,
            reason_by_line=uncover_reason_by_line,
        )
        shortfalls = self._raise_borrow_shortfalls(
            order,
            inquiry,
            decision,
            borrow_shortfalls,
            # The lines whose ORDER BACK rows the loop above already owns: a Buy CS
            # marked "Order back". NOT every confirmed line - a line with no Buy at all
            # appears in `buy_lines` too, and excluding it would have stopped the donor's
            # own hole being netted against what purchasing had already placed.
            buy_line_ids={
                str(entry["line"].id)
                for entry in buy_lines
                if entry.get("line")
                and entry.get("order_back")
                and _dec(entry.get("buy_qty")) > _ZERO
            },
            actor_user_id=actor_user_id,
        )
        created += shortfalls
        raised += shortfalls
        self.db.flush()
        # PLAN-scm-supplied-with-companions.md S5 (call site 1): the revision's rows are
        # all written now, so any companion this inquiry carries can be derived against
        # its host(s)' fresh state.
        self.derive_bundles(inquiry.id)
        # S1: the one-task-per-header guard is `_hand_to_purchasing`'s own now.
        if raised:
            self._hand_to_purchasing(order, inquiry, raised)
        # AC-R2-16/17 (owner ruling Q4, 18 Sep), narrowed by S4 (captain ruling, review
        # round 1): still-raised amendment rows ride along on THIS confirm's own email,
        # appended after the confirm's own lines - but ONLY when this order actually
        # queued at least one line of its own this commit. AC-R2-10's widened settle
        # gate (S2) means a re-confirm can settle every named line SILENTLY (same id,
        # no handover line at all), and appending the amendment rows onto a commit that
        # said nothing of its own would dispatch an email whose only content purchasing
        # already read on the amendment's own publish email.
        self._tell_product_moves(product_moves, actor_user_id=actor_user_id)
        queued_own_line = any(
            item.get("pso_id") == str(order.id)
            for item in self.db.info.get(_HANDOVER_PENDING_KEY, [])
        )
        if queued_own_line:
            self._append_still_raised_amendment_rows(order, actor_user_id=actor_user_id)
        return {
            "inquiry": inquiry,
            "created": created,
            "exceptions": exceptions,
            "settled_in_place": settled_in_place,
        }

    @staticmethod
    def _live_handshake(
        rows: Sequence[OrderInquiryRow],
    ) -> Optional[OrderInquiryRow]:
        """The row of this line purchasing has actually taken on, if there is one.

        LIVE rows only - raised, partly linked, placed. A cancelled row is a superseded
        instruction and an actioned one was answered elsewhere; neither is what purchasing
        is holding now, so neither may decide what the next row says about them. Rejected
        is deliberately not a match either: a refusal sends the line back to CS, and what
        CS raises next is a fresh instruction nobody has read (AC-H6).
        """
        for row in rows:
            if row.state not in (
                INQUIRY_RAISED,
                INQUIRY_PARTLY_LINKED,
                INQUIRY_PLACED,
            ):
                continue
            if row.ack_state in (ACK_ACKNOWLEDGED, ACK_CHANGED):
                return row
        return None

    @staticmethod
    def _handshake_for_raise(
        prior: Optional[OrderInquiryRow], *, carried: bool, actor_user_id: Optional[str] = None
    ) -> Tuple[str, Optional[str], Optional[datetime], Optional[datetime]]:
        """What the row about to be raised says about the handshake.

        G4 reversed (S1, `PLAN-oi-confirm-per-so.md`, owner ruling 17 Sep 2026): purchasing's
        own Confirm press is back, so the manual step G4 retired a row never sits on any more.
        Three answers: nobody had read this line before (`prior` is None - either no row
        existed, or the live one purchasing was holding was never taken on), so it is born
        AWAITING, with no acknowledgement stamp for a Confirm press to still take on; this
        confirmation is only CARRYING the line, so its row says exactly what the row it
        replaces said, whichever state that is; this confirmation is CHANGING a line
        purchasing HAD acknowledged (`prior` can only be non-None and reach this branch when
        it is, since `_live_handshake` only ever returns an ACKNOWLEDGED or CHANGED row), so
        the replacement stamps `changed` from today - purchasing's prior stamp travels
        UNTOUCHED, because R2 (the owner, 17 Sep) is a change coming back to them to confirm
        again, never a second confirm nobody pressed.

        `actor_user_id` is kept on the signature for callers, but no branch below attributes
        anything to it any more: a born row has nobody's name on it yet, and a changed row
        keeps the buyer's own prior stamp, never the CS actor who triggered the change.
        """
        now = datetime.utcnow()
        if prior is None:
            return ACK_AWAITING, None, None, None
        if carried:
            return (
                prior.ack_state,
                prior.acknowledged_by,
                prior.acknowledged_at,
                prior.changed_at,
            )
        return (
            ACK_CHANGED,
            prior.acknowledged_by,
            prior.acknowledged_at,
            now,
        )

    def _settle_row_in_place(
        self,
        inquiry: OrderInquiry,
        entry: Dict[str, Any],
        rows: Sequence[OrderInquiryRow],
        need: Decimal,
        decision: Any,
        *,
        actor_user_id: Optional[str] = None,
    ) -> bool:
        """A planning change moved THIS line: update its one row rather than replace it.

        Part 3's own rule (AC-P3-5): the sales order book moved a line the plan already
        told purchasing about, so what changed is the instruction's quantity and date -
        not which instruction it is. Cancelling and re-raising would have handed the row's
        links back with nothing said (a raised row carries none, but a linked one carries
        everything the buyer has already arranged) and left the line reading as two
        instructions on a screen whose whole point is one per line.

        Three things this writes and the supersede path does not:

        * the PREVIOUS value, in `previous_qty` / `previous_delivery_date` AND as prose on
          the row's own note ("Was 10 on 2026-08-25"). A DELAY that does not say what it
          was is not actionable, and the same is true of a quantity. The columns are what
          the Was / Now table reads; the note is for a person, and is never parsed back;
        * the OVER-COVER unlink (AC-P3-8): more linked than the new quantity gives the
          excess back, LATEST-dated document first, because the earliest arrival is the one
          the line still needs. No `CANCEL_BALANCE` exception is written for a drop the row
          absorbs in place - the exception exists for quantity already bought that this
          line no longer wants, and the unlink is exactly how it stops wanting it;
        * a need of nothing cancels the row outright, which is the honest end of a line the
          book reduced to zero.

        Returns False, changing nothing, when the line has no still-owed row or has more
        than one: with two the caller's supersede is the only answer that does not guess.
        It also declines a lone PLACED (or actioned) row that carries NO link, and that is
        the SO349754 WESERP10B shape: purchasing put 5 on a purchase order through a path
        that writes no link row, so there is nothing here to keep whole - restating the
        row at the new need would silently demote real placed supply back to raised and
        lose the netting that says 5 of it is already bought. The caller's own path nets
        `placed` off the need and raises only the difference, which is the right answer.
        """
        live = [
            row
            for row in rows
            if row.state
            in (INQUIRY_RAISED, INQUIRY_PARTLY_LINKED, INQUIRY_PLACED)
            and row.verb in (IV_ORDER, IV_ORDER_BACK)
            and not row.redirected_to_pool
        ]
        if len(live) != 1:
            return False
        row = live[0]
        # REV nit (17 Sep): one query, read once - `links` below used to be a
        # second, identical `_links_of(row.id)` call.
        links = self._links_of(row.id)
        if row.state in (INQUIRY_PLACED, INQUIRY_ACTIONED) and not links:
            return False

        # AC-RL-10 to AC-RL-14 (`PLAN-oi-replan-received-links.md`, S2): a FULLY
        # RECEIVED document is not carried through a replan - the goods it names have
        # already shipped to somebody else's order, and settling this row onto them
        # would silently understate what purchasing still has to buy. Checked before
        # the over-cover step below, on the row's own links as they stand right now.
        if links:
            redirected = self._redirect_row_if_received(row, links, decision)
            if redirected:
                return False

        previous_qty = _dec(row.qty)
        previous_date = row.delivery_date
        moved = (
            f"Was {_qty_str(previous_qty)} on {previous_date.isoformat()}"
            if previous_date
            else f"Was {_qty_str(previous_qty)}, no previous delivery date"
        )

        if need <= _ZERO:
            # Nothing is bought for this line any more - the book reduced it to nothing, or
            # the fresh plan meets it from stock. Its placements go back, so the document is
            # free for whoever needs it next rather than held against a withdrawn
            # instruction; `_remove_links` writes its own "Unlinked from ..." stamp.
            links = self._links_of(row.id)
            had_links = bool(links)
            if links:
                self._remove_links(row, links)
            row.state = INQUIRY_CANCELLED
            row.note = (
                f"{row.note}; {moved}; the book left nothing to buy"
                if row.note
                else f"{moved}; the book left nothing to buy"
            )
            # Review round 2 Blocking 4 (AC-LT-18): a settled-in-place row this branch
            # cancels holds no capacity for anyone else's row a moment longer.
            self._drop_suggested_links([row])
            self._retire_settled_cancel_balance(rows, decision, actor_user_id=actor_user_id)
            self.db.flush()
            if had_links:
                # S4: a row that carried supply and is now zeroed out is exactly what
                # purchasing has to hear about - the document it held is going back. The
                # row holds no link any more by the time this runs, so the fact has to
                # travel as an explicit flag rather than a fresh query re-deriving it.
                self._dispatch_changed_with_links(inquiry, row, had_link=True)
            # AC-H5: the honest end of a line the book reduced to nothing, whether or not
            # it carried a link - the handover email is not conditioned on that the way
            # `order_inquiry_changed_with_links` above is.
            self._record_handover(
                row, kind="cancelled", was={"qty": previous_qty}, actor_user_id=actor_user_id
            )
            return True

        links = self._links_of(row.id)
        linked = sum((_dec(link.qty) for link in links), _ZERO)
        if linked > need:
            # Latest arrival first: the row keeps the cover that lands soonest.
            by_arrival = sorted(
                links,
                key=lambda link: (
                    self._link_expected_date(link) or date.max,
                    link.linked_at or datetime.min,
                ),
                reverse=True,
            )
            giving_back: List[OrderInquiryLink] = []
            for link in by_arrival:
                excess = linked - need
                if excess <= _ZERO:
                    break
                qty = _dec(link.qty)
                if excess >= qty:
                    giving_back.append(link)
                    linked -= qty
                    continue
                # Only the EXCESS goes back, not the whole placement: the buyer arranged
                # that quantity on that document and the line still wants most of it.
                link.qty = qty - excess
                linked = need
            if giving_back:
                self._remove_links(row, giving_back)

        # Did the CONFIRMATION actually restate this line, or is it only riding along
        # because a different line in the same order was named (review of PR #471, B2)?
        # `drafted` above reaches every still-cascaded row of every confirm, including a
        # line nobody touched, whatever its handshake reads (S1, `PLAN-oi-confirm-per-so.md`)
        # - so this still has to tell a genuine restatement from a carry, or it would stamp
        # `changed_at`/`previous_qty` and fire the automation on a line that never moved,
        # rendering a false "Was 10 -> Now 10". `required_date` is compared only when the
        # confirmation states one - an entry that names none is not proposing a date
        # change, whatever the row's own date already reads.
        required_date = entry.get("required_date")
        qty_or_date_moved = need != previous_qty or (
            required_date is not None and required_date != previous_date
        )
        # OI-PRODUCT-FOLLOW R1: the entry names the line's LIVE product (`_carried_lines`
        # patches the identity), so a product AutoCount swapped reaches the row here, at
        # Confirm, like the qty and date do. Links are left as they are (R2).
        new_item_code = entry.get("item_code") or None
        previous_item_code = row.item_code
        product_moved = (
            bool(new_item_code)
            and new_item_code != previous_item_code
            and self._is_catalogue_code(previous_item_code)
        )
        changed = qty_or_date_moved or product_moved

        row.qty = need
        # Only when the confirmation states one. A line whose new composition carries no
        # required date must not have the date purchasing is working to erased.
        if required_date:
            row.delivery_date = required_date
        if entry.get("stock_location"):
            row.stock_location = entry.get("stock_location")
        row.supply_decision_id = decision.id
        row.order_inquiry_id = inquiry.id
        if product_moved:
            row.item_code = new_item_code
        if changed:
            # One rule for all three "was" columns (review S5): every REAL settle says what
            # THIS change moved and nothing older, so the Was / Now table on a re-flagged
            # row never shows an earlier move as if it were this one.
            row.previous_item_code = previous_item_code if product_moved else None
            was_note = moved if qty_or_date_moved else None
            if product_moved:
                product_note = f"Was item {previous_item_code or '-'}"
                was_note = f"{was_note}; {product_note}" if was_note else product_note
            row.note = f"{row.note}; {was_note}" if row.note else was_note
        if qty_or_date_moved:
            # The same two facts as figures, for the Was / Now table (the note above is
            # the sentence a person reads, and stays one). Written on every REAL settle,
            # not only on a row purchasing has read: the question they answer is "what
            # did this row say before", which has the same answer either way. Not on a
            # product-only change: that would print a false "Was 10 -> Now 10".
            row.previous_qty = previous_qty
            row.previous_delivery_date = previous_date
        elif changed:
            # Product-only: no false "Was 10 -> Now 10", and no older qty move left behind.
            row.previous_qty = None
            row.previous_delivery_date = None
        if changed:
            # The handshake, if there is one to speak of (`PLAN-scm-oi-handshake.md`
            # section 3, REVERSED again by `PLAN-oi-confirm-per-so.md` S1/R2, owner ruling
            # 17 Sep 2026: "change is inevitable ... need to change that back to be To
            # confirm"). A row purchasing had already taken on has just been amended under
            # them, so it stamps CHANGED - `changed_at` plus the previous_qty/date above ARE
            # the audit the Was/Now table reads - and goes BACK to To confirm rather than
            # auto-acknowledging: purchasing's own PRIOR stamp (who, when) travels untouched,
            # because this is the change coming back to them to confirm again, not a second
            # confirm nobody pressed. A row still AWAITING is left alone and says nothing: CS
            # is free to change what nobody has read, and marking it would ask purchasing to
            # re-read something they never read.
            if row.ack_state in (ACK_ACKNOWLEDGED, ACK_CHANGED):
                row.changed_at = datetime.utcnow()
                row.ack_state = ACK_CHANGED
        self._retire_settled_cancel_balance(rows, decision, actor_user_id=actor_user_id)
        self.refresh_link_state([row])
        self.db.flush()
        if changed:
            # `linked` is the CURRENT total after any over-cover trim above, not the
            # pre-trim count - the row may have given a link back entirely.
            self._dispatch_changed_with_links(inquiry, row, had_link=linked > _ZERO)
            # AC-H3/AC-H4: only the field(s) that actually moved - a settle restating the
            # same date the confirmation never proposed changing must not read as one.
            handover_was: Dict[str, Any] = {}
            if need != previous_qty:
                handover_was["qty"] = previous_qty
            if required_date is not None and required_date != previous_date:
                handover_was["delivery_date"] = previous_date
            if product_moved:
                handover_was["item_code"] = previous_item_code
            self._record_handover(
                row, kind="settled", was=handover_was, actor_user_id=actor_user_id
            )
        return True

    def _restate_product(
        self,
        inquiry: OrderInquiry,
        rows: Sequence[OrderInquiryRow],
        entry: Dict[str, Any],
    ) -> Optional[Tuple[List[OrderInquiryRow], str]]:
        """OI-PRODUCT-FOLLOW (review S2): the declined-settle half of R1. Every row given
        whose code is a catalogue code other than the entry's takes the entry's code, keeps
        the old one as `previous_item_code`, notes it and is flagged the way
        `_settle_row_in_place` flags a change (R2: links untouched). Returns the rows moved
        and the old code, or None when nothing moved."""
        new_item_code = entry.get("item_code") or None
        if not new_item_code:
            return None
        moved: List[OrderInquiryRow] = []
        old_code: Optional[str] = None
        for row in rows:
            if row.item_code == new_item_code or not self._is_catalogue_code(row.item_code):
                continue
            old_code = row.item_code
            row.previous_item_code = old_code
            row.item_code = new_item_code
            product_note = f"Was item {old_code}"
            row.note = f"{row.note}; {product_note}" if row.note else product_note
            if row.ack_state in (ACK_ACKNOWLEDGED, ACK_CHANGED):
                row.changed_at = datetime.utcnow()
                row.ack_state = ACK_CHANGED
            moved.append(row)
        if not moved:
            return None
        self.db.flush()
        for row in moved:
            self._dispatch_changed_with_links(
                inquiry, row, had_link=bool(self._links_of(row.id))
            )
        return moved, old_code

    def _tell_product_moves(
        self,
        moves: Sequence[Tuple[List[OrderInquiryRow], str]],
        *,
        actor_user_id: Optional[str] = None,
    ) -> None:
        """Puts each declined-path product move into the handover email ONCE: onto the
        line this confirm already queued for one of the moved rows (a date stamp, a totals
        line), else as its own settled line off a moved row still live."""
        pending = self.db.info.get(_HANDOVER_PENDING_KEY, [])
        for rows, old_code in moves:
            live = [row for row in rows if row.state != INQUIRY_CANCELLED]
            live_ids = {str(row.id) for row in live}
            queued = [item for item in pending if item.get("row_id") in live_ids]
            if queued:
                item = queued[0]
                line = item["line"]
                was = dict(line.get("was") or {})
                was["item_code"] = old_code
                line["was"] = was
                line["item_code"] = live[0].item_code
                item["item_code"] = live[0].item_code
                phrase = f"CHANGE ITEM CODE TO {live[0].item_code} (WAS {old_code})"
                line["remark"] = f"{line['remark']}, {phrase}" if line.get("remark") else phrase
                if "CHANGE_ITEM_CODE" not in item["verb_keys"]:
                    item["verb_keys"] = [*item["verb_keys"], "CHANGE_ITEM_CODE"]
            elif live:
                self._record_handover(
                    live[0], kind="settled", was={"item_code": old_code},
                    actor_user_id=actor_user_id,
                )

    def _is_catalogue_code(self, item_code: Optional[str]) -> bool:
        """OI-PRODUCT-FOLLOW: whether `item_code` is a real product code. Only then is a
        differing entry code read as a product change: a row whose code is a sheet's own
        spelling is never rewritten by a Confirm. Deliberately NOT compared with the
        mirror line's product - a line AutoCount swapped before the mirror learnt to
        follow (S1) still carries the old product there, and its Confirm must still move
        the row (SO423414)."""
        if not item_code:
            return False
        return (
            self.db.query(Product.id)
            .filter(Product.product_code == item_code)
            .limit(1)
            .scalar()
        ) is not None

    def _stamp_date_move(
        self,
        inquiry: OrderInquiry,
        rows: Sequence[OrderInquiryRow],
        entry: Dict[str, Any],
        decision: Any,
        *,
        actor_user_id: Optional[str] = None,
        will_net: bool = False,
        defer: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """The DATE half of a change `_settle_row_in_place` declined to read as one
        instruction - two still-owed rows, a lone placed row with no link, or every row
        already actioned (excluded from its own `live` filter outright) - restated on
        EVERY buy row of the line instead (S2, `PLAN-board-oi-mechanical-22sep.md`,
        AC-B2-4..7). Purchasing sees the row(s) it already had, each carrying the new
        date, the old one on its own note and as `previous_delivery_date`, rather than
        the same rows left bare beside a duplicate ADVANCE/DELAY notice telling them the
        same thing a second time.

        Links and QUANTITIES are untouched, and that is what makes this safe to run
        regardless of what the quantity did (review round, 22 Sep): the two halves of a
        `DATE_AND_QTY_CHANGED` are independent, so gating the date stamp on the composed
        `need` matching the line's live buy total - as this used to - meant a book that
        moved the date AND the quantity moved neither on the existing row, left it
        sitting on the OLD date beside a freshly-raised remainder row on the new one,
        and suppressed the notice as well because a buy row existed. The quantity half
        stays the netting's own business: the caller falls through to it whenever `need`
        and the live buy total disagree.

        One handover line for the WHOLE line, not one per row (AC-B2-9): the line's
        `ADVANCE`/`DELAY` change is told once, off a single representative row, the same
        "one row per sales-order line" rule `_oi_demand_rows` already holds for the
        notice this replaces. `order_inquiry_changed_with_links` is per ROW, though, and
        fires for each stamped row that actually carries a link (review round): a date
        purchasing already bought against moving is exactly what that automation exists
        to tell them, and `_settle_row_in_place` fires it for the same reason.

        `will_net` is the caller's own fact (round 3, nit): whether the netting loop
        below this call in `_write` is about to run (`live_buy_qty != need`), which
        cancels every still-RAISED row of the line seconds after this method returns. A
        RAISED row is a live target here too - it still needs its date stamped, whether
        or not it survives what comes next - but it must never be the row the ONE
        handover line is recorded off: purchasing would be pointed at a row already
        gone by the time the email lands. When netting is coming, the representative
        is picked from whatever targets are NOT `raised`; falling back to the full list
        only when every target is (nothing else to point at).

        `refresh_link_state` is deliberately NOT called, unlike the settle path: nothing
        here changes a row's quantity or its links, so there is no coverage to re-derive
        - and it would DEMOTE the very shape AC-B2-6 is about, a lone `placed` row with
        no link row behind it, back to `raised` (the reason `_settle_row_in_place`
        declines that shape outright).

        Returns False, writing nothing, when there is no date to move to or every buy
        row already carries it - the caller reads that as "nothing to stamp" and falls
        through to its own fallback (AC-B2-4's fresh line has no row here to stamp at
        all).
        """
        new_date = entry.get("required_date")
        if new_date is None:
            return False
        targets = [
            row
            for row in rows
            if row.verb in (IV_ORDER, IV_ORDER_BACK)
            and row.state in (
                INQUIRY_RAISED, INQUIRY_PARTLY_LINKED, INQUIRY_PLACED, INQUIRY_ACTIONED,
            )
            and not row.redirected_to_pool
            and row.delivery_date != new_date
        ]
        if not targets:
            return False
        # The ONE handover line (AC-B2-9) is recorded off a row that will still be here
        # to have moved (round 3, nit): when the netting below is about to run, a
        # still-RAISED target is seconds from being cancelled by it
        # (`project_order_inquiry_service.py`'s own supersede loop in `_write`), so the
        # representative is picked from whatever survives - falling back to the full
        # list only when every target is RAISED and there is nothing else to point at.
        handover_pool = targets
        if will_net:
            handover_pool = [
                row for row in targets if row.state != INQUIRY_RAISED
            ] or targets
        previous_date = handover_pool[0].delivery_date
        for row in targets:
            previous_qty = _dec(row.qty)
            row_previous_date = row.delivery_date
            moved = (
                f"Was {_qty_str(previous_qty)} on {row_previous_date.isoformat()}"
                if row_previous_date
                else f"Was {_qty_str(previous_qty)}, no previous delivery date"
            )
            row.previous_qty = previous_qty
            row.previous_delivery_date = row_previous_date
            row.delivery_date = new_date
            row.note = f"{row.note}; {moved}" if row.note else moved
            # Whose instruction the row is now, the same two facts `_settle_row_in_place`
            # restates: this decision's, at the location this composition states (when it
            # states one - a composition naming none must not blank a location purchasing
            # is working to).
            if entry.get("stock_location"):
                row.stock_location = entry.get("stock_location")
            row.supply_decision_id = decision.id
            # Same handshake rule as `_settle_row_in_place`, `changed_at` included (owner
            # ruling, 22 Sep): a row purchasing has already taken on goes back to To
            # confirm and stamps WHEN it was amended under them; one still AWAITING is
            # left alone, because CS is free to change what nobody has read yet.
            # `changed_at` is the column's own question - "when CS last amended a row
            # purchasing had already acknowledged" (`OrderInquiryRow.changed_at`) - so a
            # stamp on an awaiting row would answer it about a row nobody had read. The
            # Was/Now table is unaffected: it reads `previous_qty` /
            # `previous_delivery_date`, which every stamped row gets either way.
            if row.ack_state in (ACK_ACKNOWLEDGED, ACK_CHANGED):
                row.changed_at = datetime.utcnow()
                row.ack_state = ACK_CHANGED
        self.db.flush()
        # Batched (round 3, nit): one grouped load for every target's links rather than
        # one query per row - the same `_links_by_row` the raise/settle paths above
        # already reach for once a caller is walking a SET rather than one row.
        links_by_target = self._links_by_row([str(row.id) for row in targets])
        for row in targets:
            self._dispatch_changed_with_links(
                inquiry, row, had_link=bool(links_by_target.get(str(row.id)))
            )
        # AC-H3/AC-H4, as `_settle_row_in_place` reads them: only the field that actually
        # moved. A row that carried NO previous date states none rather than a blank one -
        # "Was <nothing>" is a handover line nobody can act on.
        if defer is not None:
            # EMAIL-HANDOVER-QTY: the caller is about to change the LINE'S total under
            # the netting and wants to tell the email once, off this same
            # representative, AFTER the netting has said what it did (a row the netting
            # redirects changes what "before" honestly means) - so nothing is recorded
            # here; the caller reads these two facts back and records the line itself.
            defer["row"] = handover_pool[0]
            defer["previous_date"] = previous_date
            defer["stamped"] = True
            return True
        self._record_handover(
            handover_pool[0],
            kind="settled",
            was={"delivery_date": previous_date} if previous_date else {},
            actor_user_id=actor_user_id,
        )
        return True

    def _own_arrival_credit_for_row(self, row: OrderInquiryRow, need: Decimal) -> Decimal:
        """R2/R7: what landed FOR this row's line, the SAME credit the board's own ladder
        reads (`ProjectSupplyService.own_arrival_credit_for`) - reused rather than
        restated. Built off a MINIMAL `_LineFacts` (this call is a single-row question,
        not a walk) that carries no `required_date`, so this credit applies no
        reserve-window gate the way the board's own ladder does - a filed follow-up
        (plan), not a claim that the picker and the board agree on every case.

        AC-S3-14 (round-4 fix round): `_redirect_row_if_received` compares this credit
        against `linked_qty` (the row's LINKED total), not the row's own `qty` - the two
        diverge whenever a row's links do not sum to its own quantity. `need` is that
        caller-supplied number, required (round 5, S-2) - every caller already states
        what it is asking the credit to cover.

        `open_qty` is `qty_ordered - qty_delivered` (review round, SF1), the SAME reading
        of "open" every other `_LineFacts` builder in this codebase uses
        (`project_supply_service.py` `_facts_for` / `demand_facts`). A line ordered 40 and
        delivered 30 owes 10, so 10 is the most a credit may cover for it - reading the
        original 40 would settle a row in place off stock the line no longer needs.

        S2 (second review round): `self._own_arrival_left` is threaded through as the
        SAME per-instance ledger `own_arrival_credit_for` charges - without it, a replan
        settling several rows of one order in one call would re-read the same physical
        floor fresh for each row and could credit each of them off it in full. Rows are
        processed in the order the caller iterates them (date order).

        B2 (round 3): this is a per-ROW question, not a per-LINE one - two rows of the
        SAME line each carry their own, smaller need. Calling `own_arrival_credit_for`
        charged the ledger with the whole LINE's theoretical credit on the first row
        asked, so a second row of the same line read an already-drained ledger and was
        wrongly refused. Sized the same way `walk()` fixed this (S5): read the
        THEORETICAL credit with `_own_arrival_credit_components` (no charge, memoised per
        row id in `self._own_arrival_row_theoretical` - a SECOND call for the same row
        must judge against the SAME theoretical the first call sized, never a smaller one
        re-read off a ledger this row's own earlier charge already reduced), then charge
        only the DELTA beyond what this row was already charged.

        S-2 (round 5): the memo used to be on the ANSWER, keyed by row id, so a row asked
        first with a small `need` (an artificial probe, or an earlier caller with a
        smaller ask) froze that answer for every later call regardless of what `need` it
        was asked with - `_redirect_row_if_received`'s own `>= linked_qty` check could
        then read a stale, too-small credit and wrongly redirect a row landed stock
        covers in full. The memo is on the THEORETICAL credit and how much of it THIS row
        has been CHARGED so far (`self._own_arrival_row_charged`); each call answers
        `min(theoretical, need)` and charges only the wedge beyond the previous charge -
        never double-charging the ledger, never exceeding the theoretical, and a call
        with a larger `need` than before is answered in full rather than replaying the
        smaller one's cached number.
        """
        row_key = str(row.id)
        cached = self._own_arrival_row_theoretical.get(row_key)
        if cached is None:
            from app.services.project_supply_service import (
                ProjectSupplyService,
                _LineFacts,
            )

            if not row.so_line_id:
                return _ZERO
            project_line = self.db.get(ProjectSalesOrderLine, row.so_line_id)
            if project_line is None or not project_line.core_sales_order_line_id:
                return _ZERO
            core_line = self.db.get(
                SalesOrderLine, project_line.core_sales_order_line_id
            )
            if core_line is None or not core_line.warehouse_id:
                return _ZERO
            warehouse = self.db.get(Warehouse, core_line.warehouse_id)
            if warehouse is None:
                return _ZERO
            if self._own_arrival_supply is None:
                self._own_arrival_supply = ProjectSupplyService(self.db)
            supply = self._own_arrival_supply
            product_id = str(core_line.product_id) if core_line.product_id else None
            group_code = group_of_warehouse_code(warehouse.warehouse_code)
            by_location: List[Any] = []
            if product_id and group_code:
                key = f"{product_id}\x00{group_code}"
                if key not in self._own_arrival_netting:
                    netting = netting_for_products(self.db, [product_id])
                    self._own_arrival_netting[key] = list(
                        netting.group_net(product_id, group_code).by_location
                    )
                by_location = self._own_arrival_netting[key]
            fact = _LineFacts(
                unit_core_line_ids=[str(core_line.id)],
                product_id=product_id,
                warehouse=warehouse,
                group_code=group_code,
                group_net_by_location=by_location,
                open_qty=max(
                    _dec(core_line.qty_ordered) - _dec(core_line.qty_delivered), _ZERO
                ),
            )
            ledger = (
                self._own_arrival_left.setdefault(product_id, {})
                if product_id
                else None
            )
            theoretical, _po, tier1_qty, tier2 = supply._own_arrival_credit_components(
                fact, own_arrival_left=ledger
            )
            cached = (theoretical, tier1_qty, tier2, fact, ledger, supply)
            self._own_arrival_row_theoretical[row_key] = cached

        theoretical, tier1_qty, tier2, fact, ledger, supply = cached
        target = min(theoretical, max(_dec(need), _ZERO))
        charged_before = self._own_arrival_row_charged.get(row_key, _ZERO)
        delta = target - charged_before
        if ledger is not None and delta > _ZERO:
            # Charge only what THIS call adds beyond the previous charge, against
            # whatever tier 1 this row has not already spent - `_charge_own_arrival_
            # credit`'s own tier1-then-tier2 split (`tier2_used = drawn - tier1_qty`)
            # is written for a single, whole draw, so a delta call states its own
            # REMAINING tier 1 room rather than the row's full tier1_qty, which would
            # double-count tier 1 on every call after the first.
            tier1_remaining = max(tier1_qty - min(charged_before, tier1_qty), _ZERO)
            supply._charge_own_arrival_credit(
                fact, delta, tier1_remaining, tier2, ledger
            )
        # A later, smaller `need` must never LOWER the recorded charge - a following
        # larger need for the SAME row would then re-charge the bin for ground already
        # covered.
        self._own_arrival_row_charged[row_key] = max(charged_before, target)
        return target

    def _redirect_row_if_received(
        self,
        row: OrderInquiryRow,
        links: Sequence[OrderInquiryLink],
        decision: Any,
    ) -> Optional[List[OrderInquiryLink]]:
        """AC-RL-10 to AC-RL-12 (`PLAN-oi-replan-received-links.md`, S2): the rule that
        makes `_settle_row_in_place` decline a row whose coverage has already shipped.

        A fully received document is not carried through a replan. When any of `links`
        names one: the row is marked `redirected_to_pool` and left OTHERWISE untouched -
        its `qty`, `delivery_date`, `state` and the received link itself stand exactly as
        they were, so the documents that covered it stay visible as history. A link to a
        STILL OPEN document is different: it is freed back to its target through
        `_remove_links`, so the raise-time cascade below may draft it onto the fresh row
        this decline sends the caller to raise instead.

        Returns `None`, changing nothing, when no link on the row is received - the
        ordinary case, which leaves `_settle_row_in_place` to run its usual settle.
        Otherwise returns the received links themselves (every one, not just the first
        named in this row's own note) - Opus review round 1: `_release_fragment` reuses
        this list (cached on the row) rather than re-deriving it with a second,
        identical query.

        AC-S3-12 (owner ruling R2, second review round): a mixed row (a received link
        plus a still-open PO link) whose own-arrival credit covers the row's WHOLE
        linked total is retained WITH BOTH LINKS - the received link stays as history,
        and the still-open PO link stays too, because shifting it off the row is
        purchasing's own decision at Order Inquiries, not something a replan's
        credit-covered settle should make for them as a side effect. (Review round one,
        SF10, had this branch release the still-open link the same way the ordinary
        not-fully-covered branch below does; that was reversed by this ruling.)
        """
        received = self._received_documents_for(links)
        received_links = [link for link in links if str(link.id) in received]
        if not received_links:
            return None
        # R2/R7: own landed stock covers the link - settle in place, links kept, no
        # redirect (Path B). Measured against the WHOLE of `links`' quantity, not only the
        # received part: the row's demand is what the credit has to cover to make a fresh
        # row unnecessary. AC-S3-12: this branch must not touch any OTHER link on the
        # row - a still-open PO link is left exactly as it was; only the ordinary,
        # not-fully-covered branch below redirects an open link.
        linked_qty = sum((_dec(link.qty) for link in links), _ZERO)
        if (
            linked_qty > _ZERO
            and self._own_arrival_credit_for_row(row, need=linked_qty) >= linked_qty
        ):
            return None
        open_links = [link for link in links if str(link.id) not in received]
        if open_links:
            self._remove_links(row, open_links)
        fragments = []
        for link in received_links:
            document = link.document or "the document"
            arrived = received[str(link.id)]
            when = arrived.strftime("%d %b %Y") if arrived else "in full"
            fragments.append(f"{document} received {when}")
        location = f", goods are {row.stock_location} stock" if row.stock_location else ""
        fragment = (
            f"{'; '.join(fragments)}{location}, released at revision {decision.revision_no}"
        )
        row.note = f"{row.note}; {fragment}" if row.note else fragment
        row.redirected_to_pool = True
        # AC-LT-18: released to stock is USED, not owed - the same reading
        # `auto_place_for_products`'s own `redirected_to_pool` gate already applies to
        # a fresh cascade; a guess still sitting on this row is stale the same way.
        self._drop_suggested_links([row])
        # PLAN-oi-cancelled-line-used-confirm.md (AC-CL-12, C3): a used row is a fresh
        # fact for purchasing the same way a cancelled line is - flip it back to To
        # confirm the SAME shape `_settle_row_in_place` above uses for a row CS amends
        # under purchasing. A row still `awaiting` is left alone: nothing new for
        # purchasing to be told about a row nobody has read yet.
        if row.ack_state in (ACK_ACKNOWLEDGED, ACK_CHANGED):
            row.changed_at = datetime.utcnow()
            row.ack_state = ACK_CHANGED
        # Cached for `_release_fragment` (an ad-hoc attribute, not a mapped column) -
        # the fresh row's own Was/Now note reuses exactly what this call already found
        # (the links AND their receipt dates) rather than re-querying either.
        row.received_links_for_release = [
            (link, received[str(link.id)]) for link in received_links
        ]
        self.db.flush()
        return received_links

    def _release_fragment(self, row: OrderInquiryRow) -> Optional[str]:
        """S4/AC-OH-40..42: what the FRESH row's own note says about a row `this` just
        released - `<document> received <date|in full> into <location>` per document,
        naming EVERY received link on the row, not only the first. Reuses the
        `(link, receipt date)` pairs `_redirect_row_if_received` already found (cached
        on `row`) when they are there; falls back to a fresh query only when they are
        not (defensive - every row this is actually called for went through that method
        in the same call).
        """
        cached = getattr(row, "received_links_for_release", None)
        if cached is None:
            links = self._links_of(row.id)
            received = self._received_documents_for(links)
            cached = [
                (link, received[str(link.id)])
                for link in links
                if str(link.id) in received
            ]
        if not cached:
            return None
        location = f" into {row.stock_location}" if row.stock_location else ""
        fragments = [
            f"{link.document or 'the document'} received "
            f"{arrived.strftime('%d %b %Y') if arrived else 'in full'}"
            for link, arrived in cached
        ]
        return f"{'; '.join(fragments)}{location}"

    def _received_documents_for(
        self, links: Sequence[OrderInquiryLink]
    ) -> Dict[str, Optional[date]]:
        """Which of `links` name a FULLY RECEIVED document (AC-RL-10): a PO line whose
        `qty_received >= qty_ordered` or `line_status == 'closed'`, or an SPO allocation
        that fails `spo_supply.open_incoming_clauses()`. Same rule `links_for_rows`
        states on the wire (S1) - kept as a separate small query here rather than reused
        wholesale, because this caller has at most a couple of links and no page of rows
        to batch over.

        Keyed by link id, mapping to the date the book states for the receipt (an SPO's
        landed shipment date) or `None` when it states none - `_redirect_row_if_
        received`'s own note reads it, and 'None' there means "in full", not "unknown".
        """
        out: Dict[str, Optional[date]] = {}
        po_line_ids = {str(link.po_line_id) for link in links if link.po_line_id}
        spo_ids = {str(link.spo_allocation_id) for link in links if link.spo_allocation_id}
        po_lines: Dict[str, bool] = {}
        if po_line_ids:
            for line_id, qty_ordered, qty_received, line_status in self.db.query(
                PurchaseOrderLine.id,
                PurchaseOrderLine.qty_ordered,
                PurchaseOrderLine.qty_received,
                PurchaseOrderLine.line_status,
            ).filter(PurchaseOrderLine.id.in_(po_line_ids)):
                # REV nit (17 Sep): `qty_ordered` null or zero must never read as
                # received just because `_dec(None)` and `_dec(0)` are both zero -
                # `0 >= 0` would otherwise flag a line nobody has ordered anything
                # against yet.
                po_lines[str(line_id)] = bool(
                    line_status == "closed"
                    or (_dec(qty_ordered) > _ZERO and _dec(qty_received) >= _dec(qty_ordered))
                )
        spo_allocations: Dict[str, Optional[date]] = {}
        if spo_ids:
            rows = (
                self.db.query(
                    SPOAllocation.id,
                    SPOAllocation.line_status,
                    SPOAllocation.receipt_status,
                    InboundShipment.actual_arrival_date,
                )
                .outerjoin(
                    InboundShipment,
                    InboundShipment.id == SPOAllocation.inbound_shipment_id,
                )
                .filter(SPOAllocation.id.in_(spo_ids))
                .all()
            )
            for alloc_id, line_status, receipt_status, arrival_date in rows:
                is_open = (
                    (line_status is None or line_status == "open")
                    and (
                        receipt_status is None
                        or receipt_status not in spo_supply.RECEIVED_RECEIPT_STATUSES
                    )
                    and arrival_date is None
                )
                if not is_open:
                    spo_allocations[str(alloc_id)] = arrival_date
        for link in links:
            if link.po_line_id and po_lines.get(str(link.po_line_id)):
                out[str(link.id)] = None
            elif link.spo_allocation_id and str(link.spo_allocation_id) in spo_allocations:
                out[str(link.id)] = spo_allocations[str(link.spo_allocation_id)]
        return out

    # -------------------------------------------------------- S5: link follows the book

    def follow_book_repairing(
        self,
        moves: Sequence[Dict[str, Any]],
        *,
        trigger: str = "autocount_ingest",
        company_id: str,
        actor_user_id: Optional[str] = None,
    ) -> int:
        """AC-RL-40 to AC-RL-45 (`PLAN-oi-replan-received-links.md` S5): `from_so_
        line_ref` is the source of truth, and whenever the ESB book moves it, our own
        link on the affected row follows.

        `moves` is `DocumentIngestService.ref_moves` / `ShippingOrderIngestService.
        ref_moves` - `{"target_kind": "po"|"spo", "target_id", "old_ref", "new_ref"}`,
        one entry per PO line or SPO allocation whose ref genuinely changed on this
        push (the capture sites already drop a same-ref repush, AC-RL-45 second half;
        AC-RL-49 review fix: each service's own capture site now stages a move per
        record and only publishes it into `ref_moves` once that record's own
        savepoint has actually committed, so a move captured for a record that later
        fails is never read here at all). Called by `ingest.py`'s post-write hooks,
        after the ingest's own transaction has written the moved ref.

        `company_id` is the pushing principal's own anchor (AC-RL-48 security review
        fix): threaded down to `_resolve_ref_line` so a ref that happens to resolve
        to ANOTHER company's sales-order line is never read as confidently as one of
        ours. `actor_user_id` (SEC-N2) is threaded to `place_on_po_allocations`
        rather than the `None` it used to hard-code.

        AC-RL-50 security review fix: a single push can name arbitrarily many moves
        (a malformed or malicious ESB batch), and each one fans out into several
        queries (`_resolve_ref_line`, `_linkable_row_for_core_line`,
        `place_on_po_allocations`) - `FOLLOW_BOOK_REPAIRING_MAX_MOVES` caps how many
        of THIS call's `moves` are processed; the rest are skipped and logged.

        Returns how many moves the cap dropped (0 on every ordinary push) - the S4
        review fix (17 Sep): a dropped move used to be a log line only, invisible to
        the operator who pushed the batch. `ingest.py`'s hook reads this and folds it
        into the ingest response's own `summary`.
        """
        cap = self.FOLLOW_BOOK_REPAIRING_MAX_MOVES
        applied_moves = moves
        dropped = 0
        if cap is not None and len(moves) > cap:
            applied_moves = moves[:cap]
            dropped = len(moves) - cap
            logger.warning(
                "follow_book_repairing: capped at %s moves, skipped %s of %s",
                cap, dropped, len(moves),
            )
        for move in applied_moves:
            self._follow_one_move(
                move, trigger=trigger, company_id=company_id, actor_user_id=actor_user_id,
            )
        return dropped

    def _follow_one_move(
        self,
        move: Dict[str, Any],
        *,
        trigger: str,
        company_id: str,
        actor_user_id: Optional[str] = None,
    ) -> None:
        target_kind = move.get("target_kind")
        target_id = move.get("target_id")
        if not target_kind or not target_id:
            return
        # AC-RL-43 is RETIRED (D4, `PLAN-oi-follow-book-chain.md`, owner ruling 18
        # Sep: lift fully). A move follows AutoCount whether or not the goods have
        # landed - a fully received document used to be read as S2's own history,
        # exempt from a book pairing repair, but the owner's "doesn't matter it is
        # closed or not" applies here exactly as it does to S1's own pairing.
        link_column = (
            OrderInquiryLink.po_line_id
            if target_kind == "po"
            else OrderInquiryLink.spo_allocation_id
        )
        links = (
            self.db.query(OrderInquiryLink)
            .filter(link_column == target_id)
            .all()
        )
        if not links:
            return

        old_ref = move.get("old_ref")
        new_ref = move.get("new_ref")
        old_line_id, _old_so_number = self._resolve_ref_line(old_ref, company_id=company_id)
        new_line_id, new_so_number = self._resolve_ref_line(new_ref, company_id=company_id)
        # AC-RL-47/48/51/52 (security review, 17 Sep): `_resolve_ref_line` answers
        # `(None, None)` for FOUR different facts - no ref at all, a ref naming
        # nothing this company has ever pushed, a ref naming another company's line,
        # and an ambiguous ref matching more than one line - and only the FIRST is
        # "nothing to match against"; the other three are "something was said that
        # this push cannot actually stand behind" and must leave the move alone
        # entirely, never read the same way a genuine absence is (AC-RL-45's own
        # `null` clear, or the supersede path's genuine "never carried a ref").
        if new_ref is not None and new_line_id is None:
            logger.warning(
                "follow_book_repairing: new_ref=%r did not resolve for target_kind=%s "
                "target_id=%s; no-op",
                new_ref, target_kind, target_id,
            )
            return
        if old_ref is not None and old_line_id is None:
            logger.warning(
                "follow_book_repairing: old_ref=%r did not resolve for target_kind=%s "
                "target_id=%s; no-op",
                old_ref, target_kind, target_id,
            )
            return

        row_ids = {str(link.row_id) for link in links}
        rows_by_id = {
            str(row.id): row
            for row in self.db.query(OrderInquiryRow).filter(OrderInquiryRow.id.in_(row_ids))
        }
        so_line_ids = {
            row.so_line_id for row in rows_by_id.values() if row.so_line_id
        }
        core_line_by_so_line: Dict[str, Optional[str]] = {}
        if so_line_ids:
            for psl_id, core_id in self.db.query(
                ProjectSalesOrderLine.id, ProjectSalesOrderLine.core_sales_order_line_id
            ).filter(ProjectSalesOrderLine.id.in_(so_line_ids)):
                core_line_by_so_line[str(psl_id)] = str(core_id) if core_id else None

        moving: List[Tuple[OrderInquiryLink, OrderInquiryRow]] = []
        for link in links:
            row = rows_by_id.get(str(link.row_id))
            if row is None:
                continue
            row_core_line_id = (
                core_line_by_so_line.get(str(row.so_line_id)) if row.so_line_id else None
            )
            if old_line_id is not None:
                # The precise reading (S5's own words): only a link whose row
                # actually sat on the OLD line moves.
                if row_core_line_id != old_line_id:
                    continue
            else:
                # No old ref to match against (the supersede path: an xlsx-era
                # allocation never carried one - `_supersede_xlsx_rows` records
                # the superseded row's ref as null against the new one). Every
                # link `repoint_allocation_dependants` already carried onto
                # `target_id` is a candidate, except one already on the new
                # line - nothing to repair there.
                if new_line_id is not None and row_core_line_id == new_line_id:
                    continue
            moving.append((link, row))
        if not moving:
            return

        document = moving[0][0].document
        when = date.today().isoformat()
        product_id = self._resolve_product_id(moving[0][1])
        candidate_row = (
            self._linkable_row_for_core_line(
                new_line_id, product_id, exclude_row_ids={str(r.id) for _l, r in moving}
            )
            if new_line_id and product_id
            else None
        )
        for link, row in moving:
            qty = _dec(link.qty)
            self._remove_links(row, [link])
            if new_line_id is not None:
                fragment = (
                    f"AutoCount moved {document or 'the document'} to "
                    f"{new_so_number or 'another sales order'} on {when}"
                )
            else:
                fragment = (
                    f"AutoCount removed {document or 'the document'} from "
                    f"{self._row_core_so_number(row) or 'its sales order'} on {when}"
                )
            row.note = f"{row.note}; {fragment}" if row.note else fragment
            self.refresh_link_state([row])
            if candidate_row is not None:
                need = self._unlinked_need(candidate_row)
                take = min(qty, need)
                if take > _ZERO:
                    allocation = (
                        {"po_line_id": target_id, "qty": _qty_str(take)}
                        if target_kind == "po"
                        else {"spo_allocation_id": target_id, "qty": _qty_str(take)}
                    )
                    try:
                        self.place_on_po_allocations(
                            str(candidate_row.id),
                            [allocation],
                            actor_user_id=actor_user_id,
                            auto_trigger=trigger,
                        )
                    except AppException:
                        # The book named a real linkable row, but the cascade's
                        # own rules (a group deficit, a dedication) refuse the
                        # placement - the row above is still correctly
                        # unlinked, which is the honest half of this repair;
                        # the raise-time cascade gets the next attempt.
                        logger.warning(
                            "follow_book_repairing: place failed for row=%s target=%s",
                            candidate_row.id, target_id,
                        )
        self.db.flush()

    def _resolve_ref_line(
        self, ref: Optional[str], *, company_id: str
    ) -> Tuple[Optional[str], Optional[str]]:
        """`(core_sales_order_line_id, so_number)` for an ESB `from_so_line_ref`, or
        `(None, None)` when it is absent, names nothing this system holds, names a
        line OUTSIDE `company_id` (AC-RL-48: scoped to the pushing company, never an
        unscoped lookup that resolves a foreign line as confidently as one of ours),
        or names MORE THAN ONE line (AC-RL-51: `source_ref` carries no unique
        constraint, and an ambiguous ref is refused, never guessed at via `.first()`).
        The caller (`_follow_one_move`) is the one that decides what a `(None, None)`
        answer MEANS for a given ref - this only ever resolves or refuses.

        Same exact `source_ref` match `order_link_service.write_line_ref_claims`
        uses to resolve the same field."""
        if not ref:
            return None, None
        query = (
            self.db.query(SalesOrderLine.id, SalesOrder.so_number)
            .join(SalesOrder, SalesOrder.id == SalesOrderLine.sales_order_id)
            .filter(SalesOrderLine.source_ref == ref)
        )
        if company_id is not None:
            query = query.filter(SalesOrderLine.company_id == company_id)
        found = query.limit(2).all()
        if not found:
            return None, None
        if len(found) > 1:
            logger.warning(
                "follow_book_repairing: ambiguous source_ref=%r resolves to more than "
                "one sales_order_line; refused",
                ref,
            )
            return None, None
        return str(found[0][0]), found[0][1]

    def _row_core_so_number(self, row: OrderInquiryRow) -> Optional[str]:
        """The CORE `sales_orders.so_number` a row's own line already traces to -
        only needed for the AC-RL-45 note, which names the row's CURRENT order
        rather than a new one nothing was found for.

        Deliberately its own query rather than the existing `_row_so_number`
        (the dedication identity `claim_identity` writes under, `PSO.
        autocount_doc_no or provisional_ref`): a `provisional_ref` is an
        internal placeholder a person never typed, and this note names the
        document AutoCount's own book already gave a real number.
        """
        if not row.so_line_id:
            return None
        found = (
            self.db.query(SalesOrder.so_number)
            .join(SalesOrderLine, SalesOrderLine.sales_order_id == SalesOrder.id)
            .join(
                ProjectSalesOrderLine,
                ProjectSalesOrderLine.core_sales_order_line_id == SalesOrderLine.id,
            )
            .filter(ProjectSalesOrderLine.id == row.so_line_id)
            .first()
        )
        return found[0] if found else None

    @staticmethod
    def _linkable_row_clauses(
        *,
        states: Sequence[str] = (INQUIRY_RAISED, INQUIRY_PARTLY_LINKED),
        include_awaiting: bool = False,
    ) -> List[Any]:
        """ONE linkable-row predicate (review round item 3): state, verb, ack
        (widened to AWAITING when the caller says so - review round item 1,
        `include_awaiting`) and `redirected_to_pool` false. Shared by
        `auto_place_for_products`'s own query, `_linkable_row_for_core_line`
        (`follow_book_repairing`'s landing-row search) and the book step's own
        `_linkable_rows_with_core_line` - a second copy of this is the defect,
        not a variant reading of it.
        """
        ack_states = tuple(ACK_LINKABLE) + (ACK_AWAITING,) if include_awaiting else tuple(ACK_LINKABLE)
        return [
            OrderInquiryRow.state.in_(states),
            OrderInquiryRow.verb.in_(_LINKABLE_VERBS),
            OrderInquiryRow.ack_state.in_(ack_states),
            OrderInquiryRow.redirected_to_pool.is_(False),
        ]

    def _linkable_row_for_core_line(
        self,
        core_line_id: str,
        product_id: str,
        *,
        exclude_row_ids: Optional[set] = None,
    ) -> Optional[OrderInquiryRow]:
        """The cascade's own linkable-row predicate (`auto_place_for_products`),
        narrowed to rows of ONE reconciled core sales-order line - what a book
        move's `new_ref` resolves to, and the row S5 tries to place the freed
        document on. Awaiting rows included (D3/D4: the book always follows) -
        a rejected row or one released to stock never is."""
        query = (
            self.db.query(OrderInquiryRow)
            .join(
                ProjectSalesOrderLine,
                ProjectSalesOrderLine.id == OrderInquiryRow.so_line_id,
            )
            .filter(
                ProjectSalesOrderLine.core_sales_order_line_id == core_line_id,
                *self._linkable_row_clauses(include_awaiting=True),
            )
        )
        for row in query.all():
            if exclude_row_ids and str(row.id) in exclude_row_ids:
                continue
            product = self._resolve_product_id(row)
            if product and product != product_id:
                continue
            if self._unlinked_need(row) > _ZERO:
                return row
        return None

    # ---------------------------------------------------------- S1: follow the book

    def follow_book_for_rows(
        self,
        row_ids: Sequence[str],
        *,
        trigger: str,
        company_id: str,
        actor_user_id: Optional[str] = None,
        max_rows: Optional[int] = None,
        _may_reoffer: bool = True,
    ) -> int:
        """S1/S3 (`PLAN-oi-follow-book-chain.md`, AC-FB-1 to AC-FB-12, AC-FB-20,
        AC-FB-30 to AC-FB-33): a row follows the document AutoCount's own book
        already states for its sales-order line - closed or not, and even when
        another line's link is already sitting on it (owner ruling 18 Sep:
        "doesn't matter it is closed or not, if autocount has that linking, we
        must use and follow that"; D3, 19 Sep: "the book wins, always").

        Runs the OI sheet importer's own pairing rule
        (`project_order_inquiry_import_service.pair_needs`, an extraction of `_pair`
        with no behaviour change, AC-FB-12) against LIVE rows instead of a migrated
        one. `row_ids` is narrowed first to the cascade's own linkable predicate
        (`_linkable_row_for_core_line`'s: state, verb, ack) with need left
        (`_unlinked_need`), and resolved to each row's core sales-order line - a
        row whose mirror never adopted one is left alone, same as a row this pass
        does not otherwise touch.

        `company_id` pins the company scope for the WHOLE call (AC-FB-9), same
        shape and same reason as `ShippingOrderIngestService._apply`: the pairing's
        own queries are ordinary company-scoped ORM reads, filtered by whatever the
        session's ambient scope happens to be - and a caller whose own scope is
        wider than one company (an `X-API-Key` principal with no tenant resolved
        yet) must never resolve a ref belonging to another company as confidently
        as one of ours.

        D3 (AC-FB-30 to 33): when `pair_needs` cannot fully satisfy a need because
        a book-named target's whole capacity already sits under another core
        line's link, that link is DISPLACED - removed (or reduced, for the
        quantity the book row does not need) with a note, never a row of the SAME
        core line (AC-FB-6/33) - and `pair_needs` is re-run once so the freed
        capacity actually reaches the book row. The displaced rows are then
        offered to the ordinary cascade, once, after the book rows are linked.

        Each take is written through `_write_link` (the one link writer, exactly
        as the importer's own `apply` calls it) with `auto_trigger=trigger`, and
        `refresh_link_state` runs once for every row this call actually touched.

        `max_rows` is a CALLER decision, not a standing rule (fix round, 19 Sep):
        AC-FB-24's cap guards the external ingest surface, where one ESB batch
        can name arbitrarily many rows in a single request - the two ingest
        hooks pass `FOLLOW_BOOK_FOR_ROWS_MAX_ROWS` explicitly. The cascade
        caller (`auto_place_for_products`) and the backfill script pass
        nothing, so an ordinary company-wide Confirm/Link now/Auto link all
        press honours the book for every eligible row, not an arbitrary 200 of
        it (measured on the prod copy: capped at 200 of 4,588 rows named).
        `None` (the default) means uncapped. Returns how many rows this call
        dropped past `max_rows` - 0 whenever `max_rows` is `None` or nothing
        was dropped, whatever this call itself actually linked or displaced.

        Review round item 6 (both reviewers): the cap applies AFTER narrowing
        to linkable, book-named rows - never on the raw `row_ids` list, which
        could drop the one row the book actually names while keeping rows that
        were never going anywhere - and over a DETERMINISTIC order
        (`created_at`, `id`), so which row a tight cap admits does not depend
        on Postgres's own scan order.

        Review round item 7 (security S3 / reviewer blocker 8, AC-FB-55):
        `_may_reoffer` (private - no other caller sets it) caps the chain at
        one further book pass. A displaced holder is re-offered to the
        cascade, which runs ITS OWN book step for it (so a holder the book
        also names something for still gets to follow it - D3 does not stop
        because the row it is about happens to be a holder this call just
        displaced) - but if THAT pass displaces yet another holder, the row
        it displaces is re-offered WITHOUT a further book step: no third pass
        ever runs. Two real book passes, at most, per top-level call.
        """
        wanted = [str(row_id) for row_id in row_ids if row_id]
        if not wanted:
            return 0

        from app.services.project_order_inquiry_import_service import (
            _Need,
            _bought_rows,
            pair_needs,
        )

        with company_scope(self.db, frozenset({company_id})):
            # Security S2 (review round item 8): a tally memoised under a
            # DIFFERENT company's scope must never answer for this one -
            # cleared on entry, and again on exit, of this company's scope.
            self._invalidate_link_cache()
            try:
                dropped = 0
                rows_by_id, core_line_by_row = self._linkable_rows_with_core_line(wanted)
                if not core_line_by_row:
                    return dropped

                # Fix round, 19 Sep: a coarse, cheap pre-filter BEFORE the per-row
                # `_unlinked_need` query below - on the prod copy this narrows
                # 4,588 linkable rows down to the 191 whose ref the book actually
                # states anywhere, so an uncapped company-wide pass never pays a
                # per-row query for the other 4,397. Safe: a row whose ref the
                # book never states would take nothing from `pair_needs` either
                # way (AC-FB-1 to 33 are all about a book-stated ref).
                candidate_refs = {
                    (core_line.source_ref or "").strip()
                    for core_line in core_line_by_row.values()
                    if (core_line.source_ref or "").strip()
                }
                named_refs = self._refs_named_by_book(candidate_refs)
                core_line_by_row = {
                    row_id: core_line
                    for row_id, core_line in core_line_by_row.items()
                    if (core_line.source_ref or "").strip() in named_refs
                }
                if not core_line_by_row:
                    return dropped

                if max_rows is not None and len(core_line_by_row) > max_rows:
                    ordered_row_ids = sorted(
                        core_line_by_row,
                        key=lambda rid: (rows_by_id[rid].created_at, rid),
                    )
                    dropped = len(ordered_row_ids) - max_rows
                    keep = set(ordered_row_ids[:max_rows])
                    core_line_by_row = {
                        row_id: core_line
                        for row_id, core_line in core_line_by_row.items()
                        if row_id in keep
                    }
                    logger.warning(
                        "follow_book_for_rows: capped at %s rows, skipped %s of %s "
                        "linkable book-named rows",
                        max_rows, dropped, len(ordered_row_ids),
                    )
                if not core_line_by_row:
                    return dropped

                core_lines_by_id: Dict[str, SalesOrderLine] = {}
                needs: List[_Need] = []
                for row_id, core_line in core_line_by_row.items():
                    row = rows_by_id[row_id]
                    need_qty = self._unlinked_need(row)
                    if need_qty <= _ZERO:
                        continue
                    core_lines_by_id[str(core_line.id)] = core_line
                    needs.append(_Need(key=row_id, need_qty=need_qty, core_line=core_line))
                if not needs:
                    return dropped

                bought_rows = _bought_rows(self.db, list(core_lines_by_id.values()))
                # Review round item 4 (security B1 / reviewer blocker 3): the SAME
                # call reports, per need, the book-named targets it walked and
                # their facts - already netted (`_target_facts` +
                # `_less_own_shipments`) - so displacement below reads exactly
                # what `pair_needs` read, never a second, re-derived capacity
                # query (`_book_targets_map` is retired).
                book_targets_by_line: Dict[str, List[Tuple[str, dict]]] = {}
                links_by_key, _not_linkable = pair_needs(
                    self.db, needs, bought_rows, book_targets_out=book_targets_by_line,
                )

                # D3: a need `pair_needs` could not fully satisfy might be blocked
                # only by another core line's link sitting on the book's own target -
                # never re-derived from `links_by_key` alone, since a need that took
                # NOTHING is absent from it entirely.
                displaced_rows: List[OrderInquiryRow] = []
                shortfalls = [
                    need for need in needs
                    if self._still_needed(need, links_by_key) > _ZERO
                ]
                for need in shortfalls:
                    still = self._still_needed(need, links_by_key)
                    if still <= _ZERO:
                        continue
                    so_number = self._so_number_for_core_line(need.core_line)
                    for target_id, fact in book_targets_by_line.get(str(need.core_line.id), []):
                        if still <= _ZERO:
                            break
                        freed, rows = self._displace_other_line_holders(
                            target_id,
                            protect_core_line_id=str(need.core_line.id),
                            amount_needed=still,
                            note_so_number=so_number,
                            capacity=fact["capacity"],
                            trigger=trigger,
                            actor_user_id=actor_user_id,
                        )
                        still -= freed
                        displaced_rows.extend(rows)
                if displaced_rows:
                    # Capacity has moved - the SAME needs, re-paired, is the only way
                    # the freed quantity actually reaches the book row (never a
                    # hand-written write here, which would be a second pairing rule).
                    links_by_key, _not_linkable = pair_needs(self.db, needs, bought_rows)

                touched: List[OrderInquiryRow] = []
                for row_id, held in links_by_key.items():
                    row = rows_by_id[row_id]
                    for take in held.takes:
                        self._write_link(
                            row, take, take["qty"],
                            actor_user_id=actor_user_id,
                            auto_trigger=trigger,
                        )
                    touched.append(row)

                if touched:
                    self.db.flush()
                    self.refresh_link_state(touched)
                    self.db.flush()

                if displaced_rows and _may_reoffer:
                    # AC-FB-30/55: "the holder is offered to the cascade again" -
                    # once, after the book's own rows are linked, so a displaced
                    # row is measured against what is left rather than what it
                    # just gave up. Its OWN book step still runs (D3: a holder
                    # is not exempt from "the book wins" just for having been a
                    # holder), but `_book_step_may_reoffer=False` caps THAT
                    # pass's own chain right there (review round item 7): if it
                    # displaces yet another holder, that row is re-offered with
                    # no further book step at all.
                    self.auto_place_for_products(
                        None, actor_user_id=actor_user_id, trigger=trigger,
                        row_ids=sorted({str(row.id) for row in displaced_rows}),
                        _book_step_may_reoffer=False,
                    )

                return dropped
            finally:
                self._invalidate_link_cache()

    @staticmethod
    def _still_needed(need, links_by_key: Dict[Any, Any]) -> Decimal:
        held = links_by_key.get(need.key)
        taken = sum((_dec(t["qty"]) for t in held.takes), _ZERO) if held else _ZERO
        return max(need.need_qty - taken, _ZERO)

    def _refs_named_by_book(self, refs: set) -> set:
        """Fix round, 19 Sep: of these candidate `source_ref`s, which ones a
        LIVE purchase-order line or SPO allocation states in its own
        `from_so_line_ref` - a cheap, coarse existence check (no product or
        visibility matching; `_bought_rows`/`pair_needs` does that precisely
        once the row set is already narrowed to this), so an uncapped
        company-wide `follow_book_for_rows` call never runs `_unlinked_need`
        for a row the book has nothing to say about."""
        wanted = sorted({str(r).strip() for r in refs if str(r or "").strip()})
        if not wanted:
            return set()
        po_refs = {
            ref
            for (ref,) in self.db.query(PurchaseOrderLine.from_so_line_ref)
            .filter(PurchaseOrderLine.from_so_line_ref.in_(wanted))
            .distinct()
        }
        spo_refs = {
            ref
            for (ref,) in self.db.query(SPOAllocation.from_so_line_ref)
            .filter(SPOAllocation.from_so_line_ref.in_(wanted))
            .distinct()
        }
        return po_refs | spo_refs

    def _book_names_target_for_line(
        self,
        core_line: SalesOrderLine,
        *,
        po_line_id: Optional[str],
        spo_allocation_id: Optional[str],
    ) -> bool:
        """AC-FB-33's exemption: does the book ALSO state this ONE target for
        this ONE other core line? Runs only when `_displace_other_line_
        holders` actually finds a holder to check - rare - so a couple of
        targeted queries here cost nothing like a batch walk would.

        Review round item 5 (reviewer's own `_resolve_ref_line` precedent,
        AC-RL-51): the `(po_number, source_ref)` pair a chained SPO's
        exemption resolves through carries no uniqueness guarantee either -
        `purchase_order_lines.source_ref` is not unique (the August extract
        wrote bare ordinals onto hundreds of lines). Two lines sharing that
        pair is an ambiguous match, refused (treated as NOT named) rather
        than guessed at via `.first()`: a holder that is not actually exempt
        must still be displaced, never wrongly spared because Postgres
        happened to return the matching row first.
        """
        ref = (core_line.source_ref or "").strip()
        if not ref:
            return False
        if po_line_id:
            row = (
                self.db.query(PurchaseOrderLine.from_so_line_ref)
                .filter(PurchaseOrderLine.id == po_line_id)
                .first()
            )
            return bool(row and row[0] == ref)
        if spo_allocation_id:
            row = (
                self.db.query(
                    SPOAllocation.from_so_line_ref,
                    SPOAllocation.from_po_line_ref,
                    SPOAllocation.from_po_number,
                )
                .filter(SPOAllocation.id == spo_allocation_id)
                .first()
            )
            if row is None:
                return False
            direct_ref, po_line_ref, po_number = row
            if direct_ref:
                return direct_ref == ref
            if po_line_ref and po_number:
                matches = (
                    self.db.query(PurchaseOrderLine.from_so_line_ref)
                    .join(
                        PurchaseOrder,
                        PurchaseOrder.id == PurchaseOrderLine.purchase_order_id,
                    )
                    .filter(
                        PurchaseOrder.po_number == po_number,
                        PurchaseOrderLine.source_ref == po_line_ref,
                    )
                    .limit(2)
                    .all()
                )
                if len(matches) != 1:
                    if len(matches) > 1:
                        logger.warning(
                            "_book_names_target_for_line: ambiguous "
                            "(po_number=%r, source_ref=%r) resolves to more "
                            "than one purchase_order_line; refused",
                            po_number, po_line_ref,
                        )
                    return False
                return matches[0][0] == ref
        return False

    def _displace_other_line_holders(
        self,
        target_id: str,
        *,
        protect_core_line_id: str,
        amount_needed: Decimal,
        note_so_number: Optional[str],
        capacity: Optional[Decimal] = None,
        trigger: str = "follow_book",
        actor_user_id: Optional[str] = None,
    ) -> Tuple[Decimal, List[OrderInquiryRow]]:
        """D3 (AC-FB-30 to 33): free up to `amount_needed` of `target_id` by
        taking it off whichever OTHER core line's link is sitting on it.

        Never a row of `protect_core_line_id` (AC-FB-6): the same line already
        holding its own document is not a conflict. Never a holder the book
        ALSO names this exact target for (AC-FB-33): two shipping-order lines,
        one per sales-order line, is each row on its own document, not a
        collision. A manual link is taken exactly like an automatic one
        (AC-FB-31, owner ruling 19 Sep). Partial: only what the book row needs
        comes off (AC-FB-30b) - the holder keeps the rest. Newest link first
        (review round item 5, `linked_at` desc, id tiebreak) - which holder
        loses the quantity is a stated rule, not Postgres's own scan order.

        `capacity` is the CALLER's own figure - the SAME `fact["capacity"]`
        `pair_needs` computed for this exact target (`_target_facts` +
        `_less_own_shipments`, review round item 4 / security B1): a purchase
        order line that has already shipped answers only for what has not
        sailed, and reading the raw `qty_ordered` here instead either strips a
        holder for a quantity the line can never actually give the book row,
        or, netted the other way, leaves a genuinely over-held target alone.

        AC-FB-55: sized in two passes, never one. FIRST, every existing link
        is classified - protected (this need's own core line, never touched),
        exempt (AC-FB-33) or displaceable - and `protected_qty` (what stays no
        matter what) sets the true ceiling: `capacity - protected_qty` is the
        most this target could EVER give a line other than the protected one,
        whatever gets stripped. The book row's actual gain is capped there
        (`max_gain`); if that is zero or less, NOTHING is displaced - a holder
        is never stripped for no gain. SECOND, exactly enough is taken off the
        displaceable holders (newest first) to make that gain reachable
        (`to_free`, which still accounts for a target already over-held: the
        over-holding excess comes off too, so the target never ends up over
        its own capacity again).
        """
        if amount_needed <= _ZERO:
            return _ZERO, []
        links = (
            self.db.query(OrderInquiryLink)
            .filter(
                or_(
                    OrderInquiryLink.po_line_id == target_id,
                    OrderInquiryLink.spo_allocation_id == target_id,
                )
            )
            .order_by(OrderInquiryLink.linked_at.desc(), OrderInquiryLink.id.desc())
            .all()
        )
        if not links:
            return _ZERO, []

        if capacity is None:
            # No netted figure supplied - a caller reaching for this method
            # directly rather than through `follow_book_for_rows` (a unit
            # test, or any future one) gets the RAW capacity rather than a
            # crash; `follow_book_for_rows` itself always passes the netted
            # `pair_needs` figure (review round item 4).
            is_po_line = links[0].po_line_id == target_id
            capacity = _dec(
                self.db.query(PurchaseOrderLine.qty_ordered)
                .filter(PurchaseOrderLine.id == target_id)
                .scalar()
                if is_po_line
                else self.db.query(SPOAllocation.allocated_quantity)
                .filter(SPOAllocation.id == target_id)
                .scalar()
            )

        used_now = _ZERO
        protected_qty = _ZERO
        displaceable: List[Tuple[OrderInquiryLink, OrderInquiryRow]] = []
        for link in links:
            qty = _dec(link.qty)
            used_now += qty
            row = (
                self.db.query(OrderInquiryRow)
                .filter(OrderInquiryRow.id == link.row_id)
                .first()
            )
            if row is None:
                continue
            holder_core_line_id = self._core_line_id_for_row(row)
            if holder_core_line_id is None:
                continue
            if holder_core_line_id == protect_core_line_id:
                protected_qty += qty
                continue
            holder_core_line = self._core_line_by_id(holder_core_line_id)
            if holder_core_line is not None and self._book_names_target_for_line(
                holder_core_line,
                po_line_id=link.po_line_id,
                spo_allocation_id=link.spo_allocation_id,
            ):
                # AC-FB-33: the book names this SAME target for the holder's own
                # line too - it is not wrongly held, so nothing is displaced.
                continue
            displaceable.append((link, row))

        max_gain = min(amount_needed, capacity - protected_qty)
        if max_gain <= _ZERO:
            return _ZERO, []
        to_free = max_gain - (capacity - used_now)
        if to_free <= _ZERO:
            return _ZERO, []

        when = date.today().strftime("%d/%m/%Y")
        who = f"{trigger}, actor {actor_user_id}" if actor_user_id else trigger
        freed = _ZERO
        displaced: List[OrderInquiryRow] = []
        for link, row in displaceable:
            if freed >= to_free:
                break
            take = min(to_free - freed, _dec(link.qty))
            if take <= _ZERO:
                continue
            document = link.document
            fragment = (
                f"AutoCount states {document or 'the document'} is for "
                f"{note_so_number or 'another sales order'} ({who}), {when}"
            )
            row.note = f"{row.note}; {fragment}" if row.note else fragment
            if take >= _dec(link.qty):
                self._remove_links(row, [link])
            else:
                link.qty = _dec(link.qty) - take
                self.db.flush()
                # Review round item 8 (security S2): the full-removal branch
                # invalidates through `_remove_links`; a partial reduce must
                # too, or this instance's own tally (`_linked_by_target`)
                # keeps answering with the pre-displacement total.
                self._invalidate_link_cache()
            self.refresh_link_state([row])
            freed += take
            displaced.append(row)
        return freed, displaced

    def _core_line_id_for_row(self, row: OrderInquiryRow) -> Optional[str]:
        if not row.so_line_id:
            return None
        found = (
            self.db.query(ProjectSalesOrderLine.core_sales_order_line_id)
            .filter(ProjectSalesOrderLine.id == row.so_line_id)
            .first()
        )
        return str(found[0]) if found and found[0] else None

    def _core_line_by_id(self, core_line_id: str) -> Optional[SalesOrderLine]:
        return (
            self.db.query(SalesOrderLine)
            .filter(SalesOrderLine.id == core_line_id)
            .first()
        )

    def _so_number_for_core_line(self, core_line: SalesOrderLine) -> Optional[str]:
        found = (
            self.db.query(SalesOrder.so_number)
            .filter(SalesOrder.id == core_line.sales_order_id)
            .first()
        )
        return found[0] if found else None

    def _linkable_rows_with_core_line(
        self, row_ids: Sequence[str]
    ) -> Tuple[Dict[str, OrderInquiryRow], Dict[str, SalesOrderLine]]:
        """Of these row ids, the ones the shared linkable-row predicate
        (`_linkable_row_clauses`) accepts, resolved to their core sales-order
        line. Awaiting rows are included, always (review round item 1: a link
        on an unconfirmed row is a draft, same as any other cascade draft) -
        a rejected row or one released to stock (`redirected_to_pool`,
        review round item 3) never is.

        A row whose mirror resolves to no core line - never adopted, or the join
        itself finds nothing - is absent from the second map and left for the
        caller to skip; this never raises over it, the same way the cascade's own
        walk quietly moves past a row it cannot resolve a product for.
        """
        rows = (
            self.db.query(OrderInquiryRow, ProjectSalesOrderLine.core_sales_order_line_id)
            .join(
                ProjectSalesOrderLine,
                ProjectSalesOrderLine.id == OrderInquiryRow.so_line_id,
            )
            .filter(
                OrderInquiryRow.id.in_(row_ids),
                *self._linkable_row_clauses(include_awaiting=True),
            )
            .all()
        )
        core_line_ids = {str(core_id) for _row, core_id in rows if core_id}
        core_lines = (
            {
                str(line.id): line
                for line in self.db.query(SalesOrderLine).filter(
                    SalesOrderLine.id.in_(core_line_ids)
                )
            }
            if core_line_ids
            else {}
        )
        rows_by_id: Dict[str, OrderInquiryRow] = {}
        core_line_by_row: Dict[str, SalesOrderLine] = {}
        for row, core_id in rows:
            core_line = core_lines.get(str(core_id)) if core_id else None
            if core_line is None:
                continue
            rows_by_id[str(row.id)] = row
            core_line_by_row[str(row.id)] = core_line
        return rows_by_id, core_line_by_row

    def _dispatch_changed_with_links(
        self, inquiry: OrderInquiry, row: OrderInquiryRow, *, had_link: bool
    ) -> None:
        """Queue `order_inquiry_changed_with_links` (G6, `PLAN-scm-reorder-oi-feedback-
        1sep.md` S1) to fire once this session actually COMMITS (S5, review of PR #471).

        `had_link` is the caller's own fact, not a fresh query: by the time a zeroed-out
        settle calls this, the row's links are already GONE - the very thing purchasing
        needs telling about - so re-deriving "does it have a link" here would always read
        False and the notification meant for exactly that case would never fire. A
        linkless amendment still queues nothing: CS changing an instruction nobody has
        bought anything against yet has nothing for purchasing to be warned moved.

        This runs deep inside the atomic confirm transaction, unlike the post-commit
        dispatches `complaints_service` and `form_skip_service` run AFTER their own
        `db.commit()` - there is no equivalent seam here to call it from, several
        callers deep in `refresh_for_decision`. A SAVEPOINT around the dispatch was tried
        and does not work: `AutomationService` commits internally (queueing the
        notification/email outbox), and `Session.commit()` releases every savepoint
        above it, not just the innermost one - so the wrapping savepoint's own
        bookkeeping broke the instant a real dispatch ran. Queuing the CONTEXT now (while
        the row is live) and firing it from `_fire_pending_changed_with_links`, a
        module-level `after_commit` listener, is the same pattern
        `product_spec_write.py`'s bypass backstop already uses for the same reason: real
        work, off the transaction this write cannot afford to poison.
        """
        if not had_link:
            return
        try:
            from app.services.automation_triggers import build_order_inquiry_link

            order = (
                self.db.query(ProjectSalesOrder)
                .filter(ProjectSalesOrder.id == inquiry.project_sales_order_id)
                .one_or_none()
            )
            so_number = (
                (order.autocount_doc_no or order.provisional_ref) if order else None
            )
            ctx = {
                "order_inquiry_row": {
                    "id": str(row.id),
                    "item_code": row.item_code,
                    "so_number": so_number,
                    # `_qty_str`, not a bare `str()`: `refresh_link_state` above may
                    # have just re-read this row from the database (`derive_bundles`'s
                    # own `populate_existing()`), which widens a Decimal to the
                    # column's stored NUMERIC(15,4) scale ("25" prints back "25.0000")
                    # - a database implementation detail this outbound context must
                    # not leak (review round 2, CI).
                    "qty": _qty_str(_dec(row.qty)),
                    "previous_qty": (
                        str(row.previous_qty) if row.previous_qty is not None else None
                    ),
                    "delivery_date": (
                        row.delivery_date.isoformat() if row.delivery_date else None
                    ),
                    "previous_delivery_date": (
                        row.previous_delivery_date.isoformat()
                        if row.previous_delivery_date
                        else None
                    ),
                    "link": build_order_inquiry_link(inquiry.id),
                },
                "today": date.today().isoformat(),
            }
            self.db.info.setdefault(_CHANGED_WITH_LINKS_PENDING_KEY, []).append(
                {"context": ctx, "source_id": str(row.id)}
            )
        except Exception:  # noqa: BLE001 - a notification must not fail the settle
            logger.exception(
                "Automation dispatch(order_inquiry_changed_with_links) failed to queue"
                " for row %s",
                row.id,
            )

    def _record_handover(
        self,
        row: OrderInquiryRow,
        *,
        kind: str,
        was: Optional[Dict[str, Any]] = None,
        actor_user_id: Optional[str] = None,
        qty: Optional[Decimal] = None,
    ) -> None:
        """Queue one line of the `order_inquiry_handover` parallel-run email
        (`PLAN-scm-oi-handover-email.md` S0-S3), fired post-commit by
        `_fire_pending_handover` - the same `Session.info` queue this file already uses
        for `_dispatch_changed_with_links` / `_notify_purchasing` above, and for the same
        reason: several call sites here run deep inside the atomic confirmation
        transaction, and `AutomationService` commits internally.

        `kind` is `"raised"` / `"settled"` / `"cancelled"` (PLAN 3.1's table); `was` is
        whatever of the row's PREVIOUS values actually differ from what it holds now -
        `None` for a plain raise, `{"qty": ...}` and/or `{"delivery_date": ...}` for a
        settle, `{"so_number": ..., "customer": ..., "project": ...}` for the one caller
        that already knows a CHANGE SO NO row's source order.

        Resolved and formatted EAGERLY, off THIS session, rather than re-read by the
        drain's fresh session: `_hand_to_purchasing` wraps its own work in
        `db.begin_nested()`, and a SAVEPOINT commit fires `Session`'s `after_commit` event
        exactly as a real outer commit does (confirmed against SQLAlchemy's own
        `SessionTransaction.commit()` - it dispatches whenever `self.nested` is true, not
        only at the root) - so the drain can run while the OUTER write is still open, and
        a fresh `SessionLocal()` genuinely cannot see it yet. `_dispatch_changed_with_links`
        above has the same constraint and answers it the same way: build the payload now,
        forward it later.

        `actor_user_id` falls back to the inquiry header's `raised_by` (AC-H18) when the
        write seam received none.

        `qty` (EMAIL-HANDOVER-QTY): what the line PRINTS as its quantity instead of
        `row.qty` - the line's new total, for a settled line told off a row whose own
        quantity never moved (see `_handover_settle_diff`). `None` reads the row.
        """
        # One round trip for both inquiry scalars (AC-H25), not two: `raised_by` is only
        # READ when the seam gave no actor, but `project_sales_order_id` is needed either
        # way, so there is nothing to save by asking twice.
        inquiry_scalars = (
            self.db.query(OrderInquiry.raised_by, OrderInquiry.project_sales_order_id)
            .filter(OrderInquiry.id == row.order_inquiry_id)
            .first()
        )
        resolved_actor_id = actor_user_id or (inquiry_scalars[0] if inquiry_scalars else None)
        pso_id = inquiry_scalars[1] if inquiry_scalars else None
        facts = self._handover_order_facts(pso_id) if pso_id else {}
        so_number = facts.get("so_number")
        # A row `_settle_row_in_place`'s zero-need branch cancels never has its OWN `qty`
        # column zeroed (that column is the record of what it once asked for) - the
        # handover line still has to say "0" (AC-H5), which no read of `row.qty` gives.
        qty_str = (
            "0" if kind == "cancelled" else _qty_str(_dec(row.qty if qty is None else qty))
        )
        # AC-2 (24 Sep, owner ruling): the AutoCount SO line sequence this row's own
        # line carries, read off `sales_order_lines.line_no` rather than
        # `FulfilmentBoardService._line_numbers`'s positional renumbering (that
        # sequence is a board display convenience for lines not all mirrored, not the
        # book's own order) - `None` for a row with no `so_line_id` (an amendment
        # exception row that names no line), which `_build_handover_context` sorts
        # after every row of the same S/O that has one. Memoised
        # (`_handover_line_no_cache`, reviewer nit): `_append_still_raised_amendment_
        # rows` preloads it in one query before this method's own loop.
        if not row.so_line_id:
            line_no = None
            core_line_id = None
        elif row.so_line_id in self._handover_line_no_cache:
            line_no, core_line_id = self._handover_line_no_cache[row.so_line_id]
        else:
            so_line = self.db.get(ProjectSalesOrderLine, row.so_line_id)
            line_no = so_line.line_no if so_line is not None else None
            core_line_id = so_line.core_sales_order_line_id if so_line is not None else None
            self._handover_line_no_cache[row.so_line_id] = (line_no, core_line_id)
        line = {
            "so_date": _handover_fmt_date(facts.get("so_date")),
            "so_number": so_number,
            "customer": facts.get("customer"),
            "project": facts.get("project"),
            "item_code": row.item_code,
            "qty": qty_str,
            "delivery_date": _handover_fmt_date(row.delivery_date),
            # AC-1: "very, very important" to the owner - blank, never the word "None",
            # when the row carries no stock location (`_build_handover_context`'s own
            # subject-scope reduction already treats a blank the same way).
            "location": row.stock_location or "",
            "remark": handover_remark(kind, row, was, qty),
            "was": _format_handover_was(was),
        }
        self.db.info.setdefault(_HANDOVER_PENDING_KEY, []).append(
            {
                #: AC-R2-16: what `_append_still_raised_amendment_rows` dedups on, so a
                #: row already queued (by whichever caller queued it first) never prints
                #: twice inside the same commit's email.
                "row_id": str(row.id),
                "order_inquiry_id": str(row.order_inquiry_id),
                "pso_id": pso_id,
                "so_number": so_number,
                "customer": facts.get("customer"),
                "project": facts.get("project"),
                "stock_location": row.stock_location,
                #: AC-2/AC-3: what `_build_handover_context` sorts the whole queue by -
                #: `so_number` above, then these two.
                "line_no": line_no,
                #: #1312: the core sales-order line this row's own line carries -
                #: `_fire_pending_handover` keys its `handover_attachments` read on
                #: this, and `_build_handover_context` reads it back to print/attach
                #: what that returns. `None` for a row with no `so_line_id`, same as
                #: `line_no` above.
                "core_line_id": core_line_id,
                "item_code": row.item_code,
                "verb_keys": _handover_verb_keys(kind, row, was, qty),
                "line": line,
                "actor": self._handover_actor(resolved_actor_id),
                #: Which savepoint this was earned under (C2, `_notify_purchasing`'s own
                #: rule), so a sibling order's rollback cannot discard it.
                "tx_chain": (tx_chain := _transaction_chain(self.db)),
                #: The OUTERMOST entry of that same chain - the ROOT transaction, not
                #: the innermost savepoint (AC-H27/AC-H28, review round 2 ruling) - is
                #: what `_fire_pending_handover` waits to see CONCLUDE BY COMMIT before
                #: firing, however many savepoints this line's own write nests inside.
                "tx": tx_chain[-1] if tx_chain else None,
            }
        )

    def _amendment_row_was(self, row: OrderInquiryRow) -> Optional[Dict[str, Any]]:
        """AC-R2-16: what a still-raised amendment row's own `was` is, read off the row
        itself rather than re-derived - `previous_qty` / `previous_delivery_date` when a
        later write set them (a settle this row never gets today, but the columns exist
        and a future writer may), else parsed back out of the row's own note, which
        `_change_note` writes as `Was dd/mm/yyyy` (AC-R2-07) for exactly this reason."""
        was: Dict[str, Any] = {}
        if row.previous_qty is not None:
            was["qty"] = row.previous_qty
        if row.previous_delivery_date is not None:
            was["delivery_date"] = row.previous_delivery_date
        if not was and row.note:
            match = re.search(r"Was (\d{2})/(\d{2})/(\d{4})", row.note)
            if match:
                day, month, year = match.groups()
                was["delivery_date"] = date(int(year), int(month), int(day))
            else:
                # S1 (review round 1): a PRE-LANE row's note is still in the ISO shape
                # `_change_note` wrote before AC-R2-07's dd/mm/yyyy fix ("Was
                # 2026-09-01") - every amendment row confirmed before that migration
                # landed reads this way, SO314593/SO314594 included, and must still
                # parse rather than fall through to `was = None`.
                iso_match = re.search(r"Was (\d{4})-(\d{2})-(\d{2})", row.note)
                if iso_match:
                    year, month, day = iso_match.groups()
                    was["delivery_date"] = date(int(year), int(month), int(day))
        return was or None

    def _append_still_raised_amendment_rows(
        self, order: ProjectSalesOrder, *, actor_user_id: Optional[str] = None
    ) -> None:
        """AC-R2-16/17 (owner ruling Q4, 18 Sep): a Confirm's own email also carries
        every row this order's amendments raised (DELAY / ADVANCE / CANCEL BALANCE /
        CHANGE SO) that is STILL `raised` - an amendment writes its own inquiry
        (`amendment_id` set) and no `supply_decision_id`, so undo, journalled or
        reconstructed, never touches these rows and a Confirm never re-derives them;
        without this they would only ever have appeared on their own publish email,
        which by the time purchasing reads a re-confirm may be long buried. Appended
        AFTER the confirm's own lines (the caller queues those first), deduped by row
        id against whatever THIS commit's queue already carries so a row already
        recorded by another path in the same commit is never printed twice. A Confirm
        on an order with no such rows appends nothing (AC-R2-17)."""
        rows = (
            self.db.query(OrderInquiryRow)
            .join(OrderInquiry, OrderInquiryRow.order_inquiry_id == OrderInquiry.id)
            .filter(
                OrderInquiry.project_sales_order_id == order.id,
                OrderInquiry.amendment_id.isnot(None),
                OrderInquiryRow.state == INQUIRY_RAISED,
            )
            .all()
        )
        if not rows:
            return
        already_queued = {
            item.get("row_id") for item in self.db.info.get(_HANDOVER_PENDING_KEY, [])
        }
        # Reviewer nit: preload every one of THESE rows' `line_no` in one query, rather
        # than leaving `_record_handover` to hit `ProjectSalesOrderLine` per row below -
        # the confirm's own lines are typically already in the identity map (loaded
        # earlier in the same transaction), but a still-raised amendment row's line
        # usually is not.
        uncached_line_ids = {
            row.so_line_id
            for row in rows
            if row.so_line_id and row.so_line_id not in self._handover_line_no_cache
        }
        if uncached_line_ids:
            for line_id, line_no, core_line_id in self.db.query(
                ProjectSalesOrderLine.id,
                ProjectSalesOrderLine.line_no,
                ProjectSalesOrderLine.core_sales_order_line_id,
            ).filter(ProjectSalesOrderLine.id.in_(uncached_line_ids)):
                self._handover_line_no_cache[line_id] = (line_no, core_line_id)
        for row in rows:
            if str(row.id) in already_queued:
                continue
            self._record_handover(
                row,
                kind="raised",
                was=self._amendment_row_was(row),
                actor_user_id=actor_user_id,
            )

    def _handover_order_facts(self, pso_id: Optional[str]) -> Dict[str, Any]:
        """SO number / customer / project / SO date for ONE project sales order, kept
        SEPARATE (PLAN-scm-oi-handover-email.md section 2: "the email keeps them apart") -
        unlike `_project_customer_labels`, which is the one thing that joins them for the
        worklist screen.

        Memoised per instance (AC-H25): a write raising N lines against the same order
        asked this the same question N times.
        """
        if not pso_id:
            return {}
        if pso_id in self._handover_order_facts_cache:
            return self._handover_order_facts_cache[pso_id]
        row = (
            self.db.query(
                ProjectSalesOrder.autocount_doc_no,
                ProjectSalesOrder.provisional_ref,
                ProjectSalesOrder.published_at,
                ProjectSalesOrder.created_at,
                Project.title,
                Customer.customer_name,
                SalesOrder.project_label,
                SalesOrder.order_date,
            )
            .outerjoin(Project, Project.id == ProjectSalesOrder.project_id)
            .outerjoin(
                ProjectPurchaseOrder,
                ProjectPurchaseOrder.id == ProjectSalesOrder.purchase_order_id,
            )
            .outerjoin(ProjectParty, ProjectParty.id == ProjectPurchaseOrder.issuing_party_id)
            .outerjoin(SalesOrder, SalesOrder.id == ProjectSalesOrder.so_id)
            .outerjoin(
                Customer,
                Customer.id
                == func.coalesce(ProjectParty.customer_id, SalesOrder.customer_id),
            )
            .filter(ProjectSalesOrder.id == pso_id)
            .first()
        )
        if row is None:
            facts: Dict[str, Any] = {}
        else:
            (
                autocount_doc_no,
                provisional_ref,
                published_at,
                created_at,
                title,
                customer_name,
                project_label,
                order_date,
            ) = row
            facts = {
                "so_number": autocount_doc_no or provisional_ref,
                "customer": customer_name,
                # A registered project wins; an adopted AutoCount order (`project_id`
                # NULL by design) falls back to the SO's own free-text label
                # (PLAN-oi-project-label-from-so.md).
                "project": title or project_label,
                # The AutoCount SO DOCUMENT date (`sales_orders.order_date`) wins - an
                # adopted order has `published_at` NULL by design, and the CRM's own pull
                # date (`created_at`) is not the day the customer's order was raised
                # (PLAN-oi-handover-so-date-autocount-25sep.md). `published_at` then
                # `created_at` stay the fallback for an authored order whose core SO
                # never carried a date, and for a draft with no core SO at all.
                "so_date": order_date or published_at or created_at,
            }
        self._handover_order_facts_cache[pso_id] = facts
        return facts

    def _handover_actor(self, user_id: Optional[str]) -> Optional[Dict[str, str]]:
        """`{name, email}` for the handover's `actor` context key, or `None` (AC-H18).

        Memoised per instance (AC-H25): the same actor raises every line of one write.
        """
        if not user_id:
            return None
        if user_id in self._handover_actor_cache:
            return self._handover_actor_cache[user_id]
        from app.models.user import User

        user = self.db.query(User).filter(User.id == user_id).first()
        actor = (
            {"name": user.name or user.email, "email": user.email}
            if user is not None and user.email
            else None
        )
        self._handover_actor_cache[user_id] = actor
        return actor

    def _record_undo(
        self,
        *,
        pso_id: Optional[str],
        decision_id: str,
        revision_no: int,
        lines: List[Dict[str, Any]],
        actor_user_id: Optional[str],
        headline: str = "UNDONE",
    ) -> None:
        """Queue one `order_inquiry_undone` email (`PLAN-board-undo-last-confirm.md`
        "The email"), fired post-commit by `_fire_pending_undo` - copied from
        `_record_handover` above, not adapted: same `Session.info` queue shape, same
        `tx` / `tx_chain` bookkeeping via `_transaction_chain`, for the same reason -
        `undo_last_confirm` runs inside the board confirm routes' own transaction and
        a fresh drain-time session cannot see a write that has not committed yet.

        `lines` is built by the caller (`undo_last_confirm`) from the journal's OWN
        order inquiry row entries, read BEFORE replay deletes or overwrites them - by
        the time this method runs, those rows may already be gone.

        `headline` (AC-R2-31h, S5): `project_supply_undo_reconstruct_service.
        reconstruct_undo` is this method's OTHER caller and passes `"RECONSTRUCTED"`,
        so the email purchasing gets says plainly that this is a best-effort restore
        of a journal-less revision, not a journalled replay.
        """
        facts = self._handover_order_facts(pso_id) if pso_id else {}
        from app.services.automation_triggers import build_order_inquiry_link

        so_number = facts.get("so_number")
        # The order's own standard-demand header (S3, AC-LK-01) - `None` when the order
        # never raised one (an all-covered decision), which `build_order_inquiry_link`
        # reads the same way it always has: the unfiltered list.
        existing_inquiry = self._existing(pso_id, None) if pso_id else None
        inquiry_id = existing_inquiry.id if existing_inquiry else None
        self.db.info.setdefault(_UNDO_PENDING_KEY, []).append(
            {
                "decision_id": str(decision_id),
                "so_number": so_number,
                "customer": facts.get("customer"),
                "project": facts.get("project"),
                "revision_no": revision_no,
                "lines": lines,
                "headline": headline,
                "link": build_order_inquiry_link(inquiry_id),
                "actor": self._handover_actor(actor_user_id),
                #: Which savepoint this was earned under (C2, `_notify_purchasing`'s
                #: own rule), so a sibling order's rollback cannot discard it.
                "tx_chain": (tx_chain := _transaction_chain(self.db)),
                #: The OUTERMOST entry of that same chain - the ROOT transaction
                #: (AC-H27/AC-H28's own ruling, carried over unchanged) - is what
                #: `_fire_pending_undo` waits to see CONCLUDE BY COMMIT before firing.
                "tx": tx_chain[-1] if tx_chain else None,
            }
        )

    def _retire_settled_cancel_balance(
        self,
        rows: Sequence[OrderInquiryRow],
        decision: Any,
        *,
        actor_user_id: Optional[str] = None,
    ) -> None:
        """A settle answers the exception an earlier revision raised for the same line.

        A still-raised `CANCEL_BALANCE` says "placed X, new need Y" against a quantity
        this settle has just restated in place - so left standing it asks purchasing to
        answer a question about a figure that no longer exists, beside the very row that
        now carries the true one. The supersede path cancels it on every reconfirm
        (`refresh_for_decision`, the loop below); the settle path skipped it because it
        only ever looks at the row it is updating.
        """
        for row in rows:
            if row.verb == IV_CANCEL_BALANCE and row.state == INQUIRY_RAISED:
                # AC-H19 (plan 3.1, revised): retired rows print ALWAYS, no pairing to
                # whatever else the same commit raises.
                was_qty = row.qty
                row.state = INQUIRY_CANCELLED
                row.note = f"Superseded by revision {decision.revision_no}"
                self._record_handover(
                    row, kind="cancelled", was={"qty": was_qty}, actor_user_id=actor_user_id
                )

    def _link_expected_date(self, link: OrderInquiryLink):
        """When the document behind this link arrives, whichever family it names."""
        if link.spo_allocation_id:
            return (
                self.db.query(SPOAllocation.expected_date)
                .filter(SPOAllocation.id == link.spo_allocation_id)
                .scalar()
            )
        if link.po_line_id:
            return (
                self.db.query(PurchaseOrderLine.expected_date)
                .filter(PurchaseOrderLine.id == link.po_line_id)
                .scalar()
            )
        return None

    def _raise_borrow_shortfalls(
        self,
        order: ProjectSalesOrder,
        inquiry: OrderInquiry,
        decision: Any,
        shortfalls: Sequence[Dict[str, Any]],
        buy_line_ids: Optional[set] = None,
        actor_user_id: Optional[str] = None,
    ) -> int:
        """One row per donor location this confirmation left oversold (PLAN 13.11).

        Its own verb rather than `ORDER`, and that is not cosmetic: the quantity belongs to
        the DONOR's location while the row hangs off the borrowing line, so counted as
        `ORDER` it would reach `confirmed_unplaced_buy_rows` attributed to the borrowing
        line's warehouse and be cancelled by the Buy-residual rules on the next re-confirm.

        The lifecycle is the same as every other row here: a still-raised one from an
        earlier revision is CANCELLED and kept, never edited in place, and one purchasing
        has already actioned stays - AND is netted, exactly as an actioned ORDER row is
        netted off the line's next Buy. A hole of 10 that purchasing placed is not
        raised again by the next revision; a hole that has widened to 15 raises the 5
        still outstanding. Netted per (item, donor location), which is the pile the hole
        is in: a donor short of two products has two holes.

        **`placed` is a POOL, consumed once, not restated per entry** (B3). Two order-backs
        can share one (item, donor location) key - a group borrow at one location, and a
        location-pile shortfall at the same one - and the actioned quantity purchasing
        already placed there covers the FIRST entry that draws on it, then whatever is
        left over covers the next. Netting the whole `placed[key]` off every entry sharing
        the key (rather than decrementing it as each entry consumes it) under-raised every
        entry after the first by the SAME amount, as if purchasing had placed it twice.
        """
        # `ORDER_BACK` is written by TWO paths since part 2 section 4b: this one, for the
        # hole a borrow left at the donor's location, and the Buy line CS marked "Order
        # back" in Amend. The two never meet on one sales-order LINE - the whole-line rule
        # (AC-L5) makes a line either wholly stock or wholly Buy - so the line is what
        # tells them apart, and this method leaves the Buy-line rows to
        # `refresh_for_decision`, which supersedes and nets them exactly as it does an
        # ORDER. Without the exclusion every board order-back would be cancelled on the
        # next confirm and never re-raised.
        rows = [
            row
            for row in self.db.query(OrderInquiryRow)
            .filter(
                OrderInquiryRow.order_inquiry_id == inquiry.id,
                OrderInquiryRow.verb == IV_ORDER_BACK,
            )
            .all()
            if not (buy_line_ids and str(row.so_line_id) in buy_line_ids)
            # A ROW THAT NAMES ITS COVER IS NOT A HOLE (S4). Ladder v7.1 step 3 raises an
            # ORDER_BACK on the ASKER's own line carrying `covered_by` - the document that
            # already covers the quantity, which is exactly what this column has always
            # meant - and the placement link hangs off it. Netted here, that row would have
            # cancelled a real donor hole at the same (item, location) as if purchasing had
            # already bought it. The two live on the same line and only this tells them
            # apart: the hole names no cover, because nothing covers it yet.
            and not (row.covered_by or "").strip()
        ]
        # A PARTLY LINKED row is netted HALF, exactly as an ORDER row is one method up:
        # the quantity sitting on a document is real supply and counts against the hole,
        # and the remainder is a hole this revision is about to restate, so the row is
        # SHRUNK to what is linked rather than cancelled. Cancelling it would have taken
        # its links down with it; leaving it whole would have counted the unlinked half
        # twice, once here and once on the row raised below; and re-raising the FULL hole
        # on top of it - which is what a plain `raised` test does to it - would have told
        # purchasing to buy the covered part a second time.
        linked = self._linked_qty_by_row([row.id for row in rows])
        placed: Dict[Tuple[Optional[str], Optional[str]], Decimal] = {}
        cancelled_this_pass: List[OrderInquiryRow] = []
        for row in rows:
            key = (row.item_code or None, row.stock_location or None)
            if row.state == INQUIRY_RAISED:
                row.state = INQUIRY_CANCELLED
                row.note = f"Superseded by revision {decision.revision_no}"
                cancelled_this_pass.append(row)
            elif row.state == INQUIRY_PARTLY_LINKED:
                covered = linked.get(row.id, _ZERO)
                placed[key] = placed.get(key, _ZERO) + covered
                row.qty = covered
                row.state = INQUIRY_PLACED
                row.note = (
                    f"{row.note}; Remainder superseded by revision {decision.revision_no}"
                    if row.note
                    else f"Remainder superseded by revision {decision.revision_no}"
                )
            # `placed` (section G) is a real, distinct state from `actioned` and a
            # shortfall row purchasing already dealt with must net off just as an actioned
            # one does.
            elif row.state in (INQUIRY_ACTIONED, INQUIRY_PLACED):
                placed[key] = placed.get(key, _ZERO) + _dec(row.qty)

        # Review round 2 Blocking 4 (AC-LT-18): an ORDER_BACK row is linkable and can
        # carry a suggestion, so a hole this pass cancels must not go on holding it.
        self._drop_suggested_links(cancelled_this_pass)

        created = 0
        for entry in shortfalls:
            key = (entry.get("item_code") or None, entry.get("stock_location") or None)
            raw_qty = _dec(entry.get("qty"))
            already_placed = placed.get(key, _ZERO)
            netted = min(raw_qty, already_placed)
            if netted > _ZERO:
                placed[key] = already_placed - netted
            qty = raw_qty - netted
            if qty <= _ZERO:
                continue
            line = entry.get("line")
            shortfall_row = OrderInquiryRow(
                company_id=order.company_id,
                order_inquiry_id=inquiry.id,
                so_line_id=line.id if line is not None else None,
                item_code=entry.get("item_code") or None,
                qty=qty,
                delivery_date=entry.get("required_date"),
                #: The DONOR's location, which is where the hole is.
                stock_location=entry.get("stock_location"),
                verb=IV_ORDER_BACK,
                note=entry.get("note"),
                covered_by=None,
                supply_decision_id=decision.id,
                state=INQUIRY_RAISED,
                # Born AWAITING (S1, `PLAN-oi-confirm-per-so.md`): the hole a borrow left
                # is purchasing's work the moment it exists, but it is still THEIRS to
                # confirm - the G4 exemption that skipped the press is retired.
                ack_state=ACK_AWAITING,
                acknowledged_by=None,
                acknowledged_at=None,
            )
            self.db.add(shortfall_row)
            self._record_handover(shortfall_row, kind="raised", actor_user_id=actor_user_id)
            created += 1
        return created

    def _retire_uncovered_rows(
        self,
        inquiry: OrderInquiry,
        decision: Any,
        buy_lines: Sequence[Dict[str, Any]],
        *,
        actor_user_id: Optional[str] = None,
        only_line_ids: Optional[Sequence[str]] = None,
        reason: Optional[str] = None,
        reason_by_line: Optional[Mapping[str, str]] = None,
    ) -> None:
        """Cancel still-raised rows of an EARLIER revision on lines this one dropped.

        Scoped to rows that carry a `supply_decision_id` other than this decision's: a row
        with none belongs to the amendment path, which is a different instruction to
        purchasing and is not this method's to touch. An `actioned` row stays, exactly as
        it does on a covered line - placed supply is in the ledger.

        A DRAFTED row counts as still-raised here (B3, review round 28 Aug). Since R6 the
        raise links its own rows, so a row nobody has confirmed reads `placed` or `partly
        linked` within a second of being raised - and a filter that only knew `raised` left
        every one of them alive on a line CS had taken back out of the decision, holding
        purchase-order quantity for an instruction that no longer exists. The links come
        down with the row, because they were drafts and the document is owed to whoever
        needs it next. A row a PERSON has manually linked is left exactly where it is:
        purchasing bought it (`_only_cascade_links`, `PLAN-scm-oi-draft-links.md` S1 -
        `ack_state` is never this proxy, born awaiting again or not
        (`PLAN-oi-confirm-per-so.md` S1): linking never waits for confirm, so an unread
        row can hold a manual link just as a confirmed one can hold only the cascade's
        own guess).

        `only_line_ids`/`reason` (owner case, 22 Sep 2026,
        `PLAN-board-reject-on-confirmed-line.md`, fix round): `ProjectSupplyService
        .uncover_lines`' whole-revision branch retires the very decision this call would
        otherwise diff against - there is no SUCCESSOR revision to name a
        `supply_decision_id != decision.id` row as belonging to an earlier one, so the
        ordinary query above would exclude every row this call means to retire (it found
        none, ever, for that branch - "one confirmed Buy line, reject it" left its raised
        row in front of purchasing for ever). Given explicitly, the query scopes to just
        these lines instead of diffing against a successor, reads `IV_ORDER_BACK` alongside
        `IV_ORDER`/`IV_CANCEL_BALANCE` (a Buy CS marked "Order back" with no `covered_by`
        document is not a step-3 placement - `retire_supply_borrow_rows` does not see it -
        but it is exactly as much this method's "line dropped, raised row must go" case as
        a plain ORDER row), and the note carries `reason` prefixed (B1/S4, review round 3):
        purchasing reads the same `note` column for a superseded-revision cancellation and
        for this one, and a bare reason fragment with no lead-in read as a glitch next to
        "Superseded by revision N" above it.

        `OrderInquiryRow.supply_decision_id == decision.id` (B1, review round 3): dropped
        from the first cut of this mode, which scoped by `so_line_id` alone. A row with NO
        `supply_decision_id` on the SAME line belongs to the amendment/book-change path
        (`derive_for_book_change` writes such a row onto the order's OWN header, verbs
        including `IV_ORDER`/`IV_CANCEL_BALANCE`, with no decision attached) - a different
        instruction to purchasing this method has never been the one to cancel, in the
        ordinary `else` branch below either. Without the predicate, rejecting a covered
        line whose header also carried a planning-change reaction cancelled that reaction
        alongside the decision's own row.

        `reason_by_line` (S2/S3, `PLAN-board-reject-on-confirmed-line.md`, rework fix
        round): the BARE reason CS gave for EACH withdrawn line, keyed by `so_line_id` -
        read in preference to `reason` (which on the reject path is the JOINED "Line N
        rejected: ...; Line M rejected: ..." sentence Confirm also stamps on the
        superseded revision's own `superseded_reason`). Without this a two-line
        withdrawal stamped every row with the WHOLE joined sentence, prefixed a second
        time by "Taken out of the confirmation: " - a row's own note is one line's
        reason, never every withdrawn line's. A row whose line is absent from the map
        falls back to `reason`/the ordinary default, unchanged.
        """
        covered = {str(entry["line"].id) for entry in buy_lines}
        query = self.db.query(OrderInquiryRow).filter(
            OrderInquiryRow.order_inquiry_id == inquiry.id,
            OrderInquiryRow.state.in_(
                (INQUIRY_RAISED, INQUIRY_PARTLY_LINKED, INQUIRY_PLACED)
            ),
        )
        if only_line_ids is not None:
            query = query.filter(
                OrderInquiryRow.verb.in_((IV_ORDER, IV_ORDER_BACK, IV_CANCEL_BALANCE)),
                OrderInquiryRow.so_line_id.in_([str(x) for x in only_line_ids]),
                OrderInquiryRow.supply_decision_id == decision.id,
                # A row purchasing has ALREADY rejected is left exactly as it is (owner
                # case, fix round, found by `test_the_summary_ack_facet_carries_all_four_
                # keys_by_name`): `reject_row`/`reject_rows` calls `uncover_lines` on the
                # very line it just refused, through this same whole-revision branch, and
                # `row.state` still reads RAISED at that point (`_stamp_rejected` moves
                # only `ack_state`) - so without this the retirement below would cancel
                # the row purchasing just rejected, and the ack summary's "rejected" facet
                # (`_acks`, `order_inquiry_worklist_service.py`) excludes CANCELLED rows by
                # design, so the row purchasing was just told about vanished from it.
                OrderInquiryRow.ack_state != ACK_REJECTED,
            )
        else:
            query = query.filter(
                OrderInquiryRow.verb.in_((IV_ORDER, IV_CANCEL_BALANCE)),
                OrderInquiryRow.supply_decision_id.isnot(None),
                OrderInquiryRow.supply_decision_id != decision.id,
            )
        stale = query.all()

        def _stamp_for(row: OrderInquiryRow) -> str:
            """This ROW's own note (S2/S3): a per-line bare reason wins when the caller
            gave one, else the method's own defaults - unchanged from before this
            parameter existed."""
            per_line = (reason_by_line or {}).get(str(row.so_line_id))
            if per_line is not None:
                return f"Taken out of the confirmation: {per_line}"
            if only_line_ids is not None and reason is not None:
                return f"Taken out of the confirmation: {reason}"
            return reason if reason is not None else f"Superseded by revision {decision.revision_no}"

        # Batched (S6): one grouped load for every stale row's links, rather than one
        # query per row inside the loop below.
        stale_links = self._links_by_row([str(row.id) for row in stale])
        retired_this_pass: List[OrderInquiryRow] = []
        for row in stale:
            if str(row.so_line_id) in covered:
                continue
            # AC-H19 (plan 3.1, revised 16 Sep): a retired row prints ALWAYS, no pairing
            # to whatever else the same commit raises - the qty it once asked for is
            # `was`, captured before either branch below touches the row.
            was_qty = row.qty
            stamp = _stamp_for(row)
            if row.state == INQUIRY_RAISED:
                row.state = INQUIRY_CANCELLED
                row.note = stamp
                retired_this_pass.append(row)
                self._record_handover(
                    row, kind="cancelled", was={"qty": was_qty}, actor_user_id=actor_user_id
                )
                continue
            if not self._cascade_only(stale_links.get(str(row.id), [])):
                continue
            # The draft's own history is kept rather than overwritten: which document it
            # was holding, and that the retirement is what took it back.
            self._unplace_drafts([row], trigger="retired")
            row.state = INQUIRY_CANCELLED
            row.note = f"{row.note}; {stamp}" if row.note else stamp
            retired_this_pass.append(row)
            # S4: this line dropped out of the revision taking real cascade-linked
            # supply with it - a superseded row that held a link is exactly what
            # purchasing has to hear about, the same as a zeroed settle-in-place.
            self._dispatch_changed_with_links(inquiry, row, had_link=True)
            self._record_handover(
                row, kind="cancelled", was={"qty": was_qty}, actor_user_id=actor_user_id
            )
        # Review round 2 Blocking 4 (AC-LT-18): a retired row's own suggestion must not
        # go on holding capacity against every other row for a line CS took back out of
        # the confirmation.
        self._drop_suggested_links(retired_this_pass)

    def retire_rows_for_dropped_lines(
        self,
        project_sales_order_id: str,
        decision: Any,
        line_ids: Sequence[str],
        *,
        reason: str,
        actor_user_id: Optional[str] = None,
        reason_by_line: Optional[Mapping[str, str]] = None,
    ) -> None:
        """Public entry to `_retire_uncovered_rows`'s `only_line_ids` mode, for
        `ProjectSupplyService.uncover_lines`' whole-revision branch (owner case, 22 Sep
        2026, `PLAN-board-reject-on-confirmed-line.md`, fix round): the confirm-based
        branch reaches the SAME retirement through `refresh_for_decision`'s own call
        inside `confirm()`; this branch writes no fresh decision to route a confirm
        through, so it calls the retirement directly instead. No-op when the order has
        never raised an inquiry at all.

        `reason_by_line` (S2/S3, rework fix round): the per-line bare reason each row's
        own note is stamped with; `reason` stays the JOINED sentence a row falls back to
        when its own line is absent from the map.
        """
        inquiry = self._existing(project_sales_order_id, None)
        if inquiry is None:
            return
        self._retire_uncovered_rows(
            inquiry,
            decision,
            buy_lines=[],
            actor_user_id=actor_user_id,
            only_line_ids=line_ids,
            reason=reason,
            reason_by_line=reason_by_line,
        )

    def derive_for_amendment(
        self, amendment: SOAmendment, *, actor_user_id: Optional[str] = None
    ) -> OrderInquiry:
        """An amendment says what CHANGED, in the same verbs purchasing already reads.

        The delta is read AFTER it has been applied, so the line carries the new date and
        the new quantity. What the row adds is the previous value, which is the half of
        a DELAY that makes it actionable.
        """
        existing = self._existing(amendment.project_sales_order_id, amendment.id)
        if existing is not None:
            return existing

        order = self._order_or_404(amendment.project_sales_order_id)
        delta = amendment.delta_json or {}
        # Section 9.3: a declined row was never applied to the order, so it must not
        # become a purchasing instruction either. `row_decisions` defaults every row
        # absent from it to accepted, which is why an amendment nobody touched still
        # derives exactly as it always has.
        row_decisions = amendment.row_decisions or {}
        demand: List[DemandRow] = []
        for index, row in enumerate(delta.get("rows") or []):
            row_key = str(row.get("row_key") or index)
            if (row_decisions.get(row_key) or {}).get("decision") == "declined":
                continue
            change = _DELTA_VERB_CHANGE.get(str(row.get("verb") or ""))
            if change is None:
                continue
            line = self._line_or_none(row.get("so_line_id"))
            qty = _dec(row.get("qty"))
            if qty <= _ZERO:
                continue
            delivery_date = (
                _as_date(row.get("to_value"))
                if change in (CHANGE_DATE_LATER, CHANGE_DATE_EARLIER)
                else (line.delivery_date if line else None)
            )
            # AC-R2-06: the previous date, structured, for a DELAY/ADVANCE row - the
            # value is already in the delta's own row dict, no lookup needed. Every
            # other change carries no structured `was` yet (CHANGE SO's `was` is built
            # separately, by the one caller that already knows the source order).
            was = (
                {"delivery_date": _as_date(row.get("from_value"))}
                if change in (CHANGE_DATE_LATER, CHANGE_DATE_EARLIER)
                and row.get("from_value")
                else None
            )
            demand.append(
                DemandRow(
                    line_id=line.id if line else str(row.get("so_line_id") or ""),
                    product_id=str(row.get("product_id") or ""),
                    item_code=row.get("product_code")
                    or self._product_code(row.get("product_id")),
                    qty=qty,
                    delivery_date=delivery_date,
                    stock_location=self._stock_location(line.id) if line else None,
                    change=change,
                    note=self._change_note(change, row),
                    was=was,
                )
            )
        return self._write(order, amendment, demand, actor_user_id=actor_user_id)

    def derive_for_book_change(
        self,
        order: ProjectSalesOrder,
        rows: Sequence[Dict[str, Any]],
        *,
        batch_id: str,
        actor_user_id: Optional[str] = None,
    ) -> Optional[OrderInquiry]:
        """A planning-change batch's accepted reactions, in purchasing's own verbs
        (`PLAN-so-book-diff-replanning.md` AC-R08).

        `rows` is one already-resolved demand row per accepted line: `{line_id, product_id,
        item_code, qty, delivery_date, stock_location, change, note}` - the caller
        (`planning_change_service.apply`) is the one that knows which reaction happened and
        what the previous value was, so this stays as thin a wrapper over `net_demand` as
        `derive_for_amendment` is.

        S3 (`PLAN-oi-worklist-one-header.md`, R1): written onto the order's OWN
        `amendment_id IS NULL` header, the same one `refresh_for_decision` owns, rather than
        under a synthetic `SOAmendment` of its own - one sales order, one order inquiry
        number, whichever writer (a confirmed Buy residual, or a book-change reaction) put
        rows on it. The DB-level singleton the old docstring feared colliding with was never
        a real collision: `ensure_inquiry` returns the exact row `confirm()` wrote earlier in
        the same apply, and `_write`'s own `inquiry=` argument tells it to append there
        rather than mint a second header. `batch_id` is kept on the signature for the
        caller's own bookkeeping (`planning_change_service.apply` still names its batch),
        though nothing here writes it anywhere any more - the synthetic amendment it used to
        travel on is gone. `derive_for_amendment` (OCN) is unchanged: a project-authored SO's
        amendment still keeps its own header (backlog: fold those too, see the plan).
        """
        demand: List[DemandRow] = []
        for row in rows:
            qty = _dec(row.get("qty"))
            if qty <= _ZERO:
                continue
            change = str(row.get("change") or "")
            demand.append(
                DemandRow(
                    line_id=str(row.get("line_id") or ""),
                    product_id=str(row.get("product_id") or ""),
                    item_code=row.get("item_code") or "",
                    qty=qty,
                    delivery_date=row.get("delivery_date"),
                    stock_location=row.get("stock_location"),
                    change=change,
                    note=row.get("note"),
                )
            )
        if not demand:
            return None
        inquiry = self.ensure_inquiry(order, actor_user_id=actor_user_id)
        return self._write(order, None, demand, actor_user_id=actor_user_id, inquiry=inquiry)

    def _change_note(self, change: str, row: Dict[str, Any]) -> Optional[str]:
        """The half of the instruction the verb does not carry."""
        before = row.get("from_value")
        after = row.get("to_value")
        if change in (CHANGE_DATE_LATER, CHANGE_DATE_EARLIER):
            moved = _as_date(before)
            # AC-R2-07: dd/mm/yyyy, matching every other date this email and the OI
            # worklist note print - `.isoformat()` was the one place still on ISO.
            return f"Was {_handover_fmt_date(moved)}" if moved else "No previous delivery date"
        if change == CHANGE_REPOINT:
            return f"Moved to {after}" if after else None
        if change in (CHANGE_QTY_DECREASE, CHANGE_QTY_INCREASE):
            if before is None or after is None:
                return None
            return f"Was {before}, now {after}"
        return None

    def _write(
        self,
        order: ProjectSalesOrder,
        amendment: Optional[SOAmendment],
        demand: Sequence[DemandRow],
        *,
        actor_user_id: Optional[str],
        inquiry: Optional[OrderInquiry] = None,
    ) -> OrderInquiry:
        plans = net_demand(demand, self._pools(order, demand))

        if inquiry is None:
            inquiry = OrderInquiry(
                company_id=order.company_id,
                project_sales_order_id=order.id,
                amendment_id=amendment.id if amendment else None,
                state=INQUIRY_RAISED,
                raised_by=actor_user_id,
                # An amendment raises its OWN inquiry, so the stamp gives it its own
                # number: it is a separate instruction to purchasing and gets referred
                # to as one.
            )
            self.db.add(inquiry)
            self.db.flush()
            self._record_raise(inquiry, actor_user_id=actor_user_id, kind=OI_RAISE_RAISED)
        # S3 (AC-OH-30..32): a caller passing an EXISTING header (`derive_for_book_change`,
        # onto the order's own amendment_id IS NULL inquiry) is appending to a header
        # something else already raised or re-stamped this apply - re-stamping raised_by
        # here would attribute rows a batch reaction wrote to whoever the SAME apply's
        # confirm happened to run as, which is already right without touching it again.

        written: List[OrderInquiryRow] = []
        for plan in plans:
            plan_row = OrderInquiryRow(
                company_id=order.company_id,
                order_inquiry_id=inquiry.id,
                so_line_id=plan.line_id or None,
                item_code=plan.item_code or None,
                qty=plan.qty,
                delivery_date=plan.delivery_date,
                stock_location=plan.stock_location,
                verb=plan.verb,
                spo_ref=plan.spo_ref,
                covered_by=plan.covered_by,
                note=plan.note,
                state=INQUIRY_RAISED,
                # Born AWAITING (S1, `PLAN-oi-confirm-per-so.md`): an amendment's own
                # instruction reads the same as any other raised row now - the G4
                # exemption that read it in already acknowledged is retired.
                ack_state=ACK_AWAITING,
                acknowledged_by=None,
                acknowledged_at=None,
            )
            self.db.add(plan_row)
            written.append(plan_row)
        self.db.flush()
        # AC-H2/H6: an amendment/book-change row raises exactly like any other.
        # AC-R2-06 (S1): `plan.was` carries the structured previous value for a
        # DELAY/ADVANCE row (threaded from `DemandRow.was`, built where the delta is
        # read - `derive_for_amendment`); every other row's `was` stays `None` here,
        # same as before - CHANGE SO NO's own source-order `was` is still built at the
        # one caller that already knows it, per the PLAN's own section 6.
        for plan, plan_row in zip(plans, written):
            self._record_handover(
                plan_row, kind="raised", was=plan.was, actor_user_id=actor_user_id
            )
        self._hand_to_purchasing(order, inquiry, len(plans))
        return inquiry

    def _existing(self, pso_id: str, amendment_id: Optional[str]) -> Optional[OrderInquiry]:
        query = self.db.query(OrderInquiry).filter(
            OrderInquiry.project_sales_order_id == pso_id
        )
        query = (
            query.filter(OrderInquiry.amendment_id == amendment_id)
            if amendment_id
            else query.filter(OrderInquiry.amendment_id.is_(None))
        )
        return query.first()

    def _record_raise(
        self, inquiry: OrderInquiry, *, actor_user_id: Optional[str], kind: str
    ) -> None:
        """One `order_inquiry_raises` row for this raise/reconfirm (R5, S1
        `PLAN-oi-header-list-detail.md`).

        Guarded so ONE confirmation adds ONE row (AC-RD-01), never two: two calls that
        land inside the same uncommitted unit of work (`refresh_for_decision`'s own gate
        and `project_supply_service`'s step-3 borrow fallback can both reach the SAME
        header in one HTTP confirm) must not double-count, while two calls separated by
        a real commit (a reconfirm days later) each earn their own row. `Session.
        get_transaction()` is what tells the two apart: it is the SAME object across
        every call inside one uncommitted pass and a DIFFERENT one the moment a commit
        (or a rollback) ends it and the session autobegins the next - which holds
        whether that commit is a real one or, under `tests/_pg_fixture.py`'s
        `join_transaction_mode="create_savepoint"`, a savepoint release. A `session.
        info` set cleared by an `after_commit` listener was the other way to say this,
        but it depends on that listener having been registered in whatever process is
        running (`register_order_inquiry_post_commit_dispatch` is only ever called from
        `app.main`'s startup event, which several of this service's own test modules
        never trigger) - reading the transaction's own identity needs nothing to have
        registered anything first.

        `get_transaction()` returns the session's ROOT transaction (SQLAlchemy's own
        contract), never an inner savepoint, so a nested savepoint opened and released
        inside the same request cannot smuggle a second row past the marker. There is
        deliberately no retry path here: a marker hit means THIS call already recorded
        the row, so the right response is to do nothing, not to try again.
        """
        txn = self.db.get_transaction()
        marker = self.db.info.setdefault("_oi_raise_marker", {})
        if marker.get(inquiry.id) is txn:
            return
        marker[inquiry.id] = txn
        self.db.add(
            OrderInquiryRaise(
                company_id=inquiry.company_id,
                order_inquiry_id=inquiry.id,
                kind=kind,
                raised_by=actor_user_id,
                raised_at=datetime.utcnow(),
            )
        )
        self.db.flush()

    def ensure_inquiry(
        self, order: ProjectSalesOrder, *, actor_user_id: Optional[str] = None
    ) -> OrderInquiry:
        """Get this order's standard-demand header (`amendment_id IS NULL`), minting it.

        Three callers: `refresh_for_decision`'s own gate, once it has decided a header is
        actually needed; `project_supply_service._place_supply_borrows`'s fallback - a
        step-3 supply borrow's asker-side ORDER_BACK row needs a header to hang off even
        on a confirmation whose Buy residual and donor holes were both empty (the line
        was fully covered by borrowing somebody else's already-placed document), which is
        a case `refresh_for_decision`'s gate cannot see because it never receives the
        borrow composition, only the confirmed Buy and the donor holes; and (S3,
        `PLAN-oi-worklist-one-header.md`) `derive_for_book_change`, which reuses this SAME
        header for a planning-change reaction rather than minting its own. Callers must
        not call this unless they are about to write at least one row: an empty header is
        exactly the defect this method's sibling gate exists to avoid.

        `raised_at`/`raised_by` are written ONCE, here, at insert (R5, S1) - a reuse
        records a `reconfirmed` raise-history row instead of re-stamping either column.
        """
        existing = self._existing(order.id, None)
        if existing is not None:
            self._record_raise(existing, actor_user_id=actor_user_id, kind=OI_RAISE_RECONFIRMED)
            return existing
        inquiry = OrderInquiry(
            company_id=order.company_id,
            project_sales_order_id=order.id,
            amendment_id=None,
            state=INQUIRY_RAISED,
            raised_by=actor_user_id,
            # The number is stamped by the model's own `before_insert` (there is one
            # minting path, so no writer can forget). Numbered ONCE, on the header this
            # order keeps: a re-confirm reuses the same inquiry (`_existing` above), so
            # purchasing keeps quoting one number through every revision.
        )
        self.db.add(inquiry)
        self.db.flush()
        self._record_raise(inquiry, actor_user_id=actor_user_id, kind=OI_RAISE_RAISED)
        return inquiry

    # ----------------------------------------------------------- covering pools

    def _pools(
        self, order: ProjectSalesOrder, demand: Sequence[DemandRow]
    ) -> List[CoveringPool]:
        """What already exists, or is on the water, for the products being asked for.

        Only the products in front of us, so a project with one product does not drag
        every open shipment in the company into the calculation.
        """
        product_ids = {row.product_id for row in demand if row.product_id}
        if not product_ids:
            return []
        pools = self._pre_order_pools(order, product_ids) + self._inbound_pools(product_ids)
        claimed = self._claimed(pools)
        out: List[CoveringPool] = []
        for pool in pools:
            balance = pool.qty - claimed.get((pool.label, pool.product_id), _ZERO)
            if balance > _ZERO:
                out.append(
                    CoveringPool(
                        kind=pool.kind,
                        reference=pool.reference,
                        product_id=pool.product_id,
                        qty=balance,
                        available_from=pool.available_from,
                    )
                )
        return out

    def _pre_order_pools(
        self, order: ProjectSalesOrder, product_ids: set
    ) -> List[CoveringPool]:
        """Published pre-order sales orders on the SAME PROJECT.

        The project is the anchor, not the customer (D18): a pre-order parked under
        another debtor still belongs to this project, so the join goes through
        `project_id` rather than through whoever the document is billed to. The order
        being published is excluded, or a pre-order would net against itself.
        """
        rows = (
            self.db.query(
                ProjectSalesOrder.provisional_ref,
                ProjectSalesOrder.autocount_doc_no,
                ProjectSalesOrderLine.product_id,
                func.sum(ProjectSalesOrderLine.qty),
            )
            .join(
                ProjectSalesOrderLine,
                ProjectSalesOrderLine.project_sales_order_id == ProjectSalesOrder.id,
            )
            .filter(
                ProjectSalesOrder.project_id == order.project_id,
                ProjectSalesOrder.id != order.id,
                ProjectSalesOrder.is_pre_order.is_(True),
                ProjectSalesOrder.status.in_([SO_STATUS_PUBLISHED, SO_STATUS_AMENDED]),
                ProjectSalesOrderLine.product_id.in_(list(product_ids)),
            )
            .group_by(
                ProjectSalesOrder.provisional_ref,
                ProjectSalesOrder.autocount_doc_no,
                ProjectSalesOrderLine.product_id,
            )
            .all()
        )
        return [
            CoveringPool(
                kind=POOL_PRE_ORDER,
                reference=doc_no or ref,
                product_id=str(product_id),
                qty=_dec(qty),
            )
            for ref, doc_no, product_id, qty in rows
            if _dec(qty) > _ZERO
        ]

    def _inbound_pools(self, product_ids: set) -> List[CoveringPool]:
        """Open SPO lines that have not landed: stock already on the water.

        Outer-joined to the shipment since migration 420: an SPO document exists before
        anybody books a container for it, and an inner join here counted only the ones that
        had one. A row with no shipment offers its own `expected_date` as the arrival, and a
        CLOSED line offers nothing - history is written closed for exactly that reason.

        `spo_supply.open_incoming_clauses` is the shared rule. A promise whose date has
        passed is still a pool (captain, 26 Aug: trust the book) - the goods are owed until
        the book says they arrived - so what a past date changes is the wording elsewhere,
        not whether this pool exists.
        """
        rows = (
            self.db.query(
                SPOAllocation.spo_number,
                SPOAllocation.product_id,
                SPOAllocation.allocated_quantity,
                SPOAllocation.quantity_received,
                func.coalesce(
                    InboundShipment.estimated_arrival_date, SPOAllocation.expected_date
                ).label("eta"),
            )
            .outerjoin(
                InboundShipment, InboundShipment.id == SPOAllocation.inbound_shipment_id
            )
            .filter(
                SPOAllocation.product_id.in_(list(product_ids)),
                SPOAllocation.spo_number.isnot(None),
                # Supply we cannot place is cover for nobody: the pool this fills is read
                # per location, so a row naming a location we do not hold counts nowhere,
                # exactly as `on_order_v` treats it.
                SPOAllocation.warehouse_id.isnot(None),
                *spo_supply.open_incoming_clauses(),
            )
            .all()
        )
        pools: List[CoveringPool] = []
        for spo_number, product_id, allocated, received, eta in rows:
            balance = _dec(allocated) - _dec(received)
            if balance <= _ZERO:
                continue
            pools.append(
                CoveringPool(
                    kind=POOL_INBOUND_SPO,
                    reference=str(spo_number),
                    product_id=str(product_id),
                    qty=balance,
                    available_from=eta,
                )
            )
        return pools

    def _claimed(self, pools: Sequence[CoveringPool]) -> Dict[Tuple[str, str], Decimal]:
        """What earlier inquiries already promised out of these same pools.

        Keyed on ``covered_by`` because the engine writes it as a stable label rather
        than as prose. A cancelled row releases its claim: purchasing said the
        instruction is dead, so the quantity behind it is available again.
        """
        labels = {pool.label for pool in pools}
        if not labels:
            return {}
        rows = (
            self.db.query(
                OrderInquiryRow.covered_by,
                OrderInquiryRow.so_line_id,
                OrderInquiryRow.qty,
            )
            .filter(
                OrderInquiryRow.covered_by.in_(list(labels)),
                OrderInquiryRow.verb.in_(list(_COVERING_VERBS)),
                OrderInquiryRow.state != INQUIRY_CANCELLED,
            )
            .all()
        )
        if not rows:
            return {}
        line_ids = [row[1] for row in rows if row[1]]
        products = dict(
            self.db.query(ProjectSalesOrderLine.id, ProjectSalesOrderLine.product_id)
            .filter(ProjectSalesOrderLine.id.in_(line_ids))
            .all()
        ) if line_ids else {}

        claimed: Dict[Tuple[str, str], Decimal] = {}
        for covered_by, line_id, qty in rows:
            product_id = products.get(line_id)
            if not product_id:
                continue
            key = (covered_by, str(product_id))
            claimed[key] = claimed.get(key, _ZERO) + _dec(qty)
        return claimed

    # ------------------------------------------------------------ stock location

    def _stock_location(self, so_line_id: str) -> Optional[str]:
        """The line's OWN fulfilment warehouse (AC-H5), or nothing at all.

        One row names one location: the amendment row is a Buy/schedule instruction the
        same way `refresh_for_decision`'s ORDER row is, and its destination is where the
        CORE reconciled line is fulfilled from, not a list of every reserve/borrow
        location a past confirmation drew on to cover it (those live on the confirmed
        decision's snapshots, not here). Joining several warehouses with `" / "` used to
        read as a real place purchasing could act on; it never was one - mirrors
        `ProjectSupplyService._restamp_stock_location`.

        Never a default the other way either: a line with no reconciled core line, or a
        core line with no warehouse set, leaves the column empty - nobody has said yet
        where this is coming from.
        """
        row = (
            self.db.query(Warehouse.warehouse_code)
            .join(SalesOrderLine, SalesOrderLine.warehouse_id == Warehouse.id)
            .join(
                ProjectSalesOrderLine,
                ProjectSalesOrderLine.core_sales_order_line_id == SalesOrderLine.id,
            )
            .filter(ProjectSalesOrderLine.id == so_line_id)
            .first()
        )
        return row[0] if row and row[0] else None

    # -------------------------------------------------------- the SCM handoff

    def _hand_to_purchasing(
        self, order: ProjectSalesOrder, inquiry: OrderInquiry, row_count: int
    ) -> None:
        """A task on the project's delivery phase, with the rows attached (AC-I4), when
        the order has a registered project - and the purchasing notification either way
        (owner ruling, `PLAN-oi-project-label-from-so.md` section 4: "whether got
        project or not should also hand to purchasing").

        Best-effort on purpose. The rows this task points at are already written when
        this runs, so a notification backend that is down must not turn that success
        into a 500 the retry cannot repair. The write sits in a SAVEPOINT because this
        now also runs INSIDE the atomic confirmation transaction: a swallowed DB error
        without one would leave that transaction aborted, and the caller's commit would
        then fail for an operation that had already succeeded (the post-commit
        side-effect lesson in CLAUDE.md, applied pre-commit).

        S1 (Opus review round 1, AC-OH-35): the ONE-TASK-PER-HEADER guard lives HERE,
        not at each caller. S3's one-header-per-SO means `refresh_for_decision`'s own
        raise and `derive_for_book_change`'s reaction can now write onto the SAME
        `inquiry` inside the SAME apply, and a caller-side `if self.task_for(inquiry.id)
        is None` was only ever checked before the FIRST caller of the pair created one -
        `_write` (the ONLY caller `derive_for_book_change`/`derive_for_amendment` reach
        this through) called this unconditionally, so a batch that both confirmed and
        reacted minted a second `ProjectTask` and a second notification for one header.
        A caller that already holds a task for this inquiry gets nothing more.

        An order ADOPTED from the AutoCount book has `project_id` NULL by design, and
        `tasks.project_id` is NOT NULL - a task only surfaces under a project's Tasks
        tab, so a project-less order has nowhere for one to appear. That order still
        skips the `ProjectTask` here, but purchasing is still told: the notification
        alone carries the SO's own project label (or its reference, with neither).
        """
        if self.task_for(inquiry.id) is not None:
            return
        try:
            with self.db.begin_nested():
                project = (
                    self.db.query(Project).filter(Project.id == order.project_id).first()
                )
                if project is None:
                    # No task without a project (`tasks.project_id` is NOT NULL) - but
                    # purchasing still needs to know. The only duplicate guard is the
                    # notification's own dedup key (`{inquiry_id}:order_inquiry_raised`,
                    # `uq_notification_user_dedup_event`): a same-transaction pending-
                    # queue check here would never fire, since this savepoint's own
                    # commit drains the queue before a second call could see it.
                    project_label = (
                        self.db.query(SalesOrder.project_label)
                        .filter(SalesOrder.id == order.so_id)
                        .scalar()
                        if order.so_id
                        else None
                    )
                    to_buy = self._buying_count(inquiry.id)
                    self._notify_purchasing(
                        None, order, inquiry, row_count, to_buy, project_label
                    )
                    return
                reference = order.autocount_doc_no or order.provisional_ref
                to_buy = self._buying_count(inquiry.id)
                task = ProjectTask(
                    company_id=order.company_id,
                    project_id=project.id,
                    name=f"Order inquiry {reference}",
                    description=(
                        f"{row_count} instruction{'' if row_count == 1 else 's'} from "
                        f"{reference}, {to_buy} of which still need buying."
                    ),
                    task_phase=TASK_PHASE_DELIVERY,
                    category="Purchasing",
                    linked_entity_type=TASK_LINK_ORDER_INQUIRY,
                    linked_entity_id=inquiry.id,
                )
                self.db.add(task)
                self.db.flush()
                self._notify_purchasing(project, order, inquiry, row_count, to_buy, None)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "order inquiry %s raised, but the purchasing task was not created (%s)",
                inquiry.id,
                exc,
            )

    def _notify_purchasing(
        self,
        project: Optional[Project],
        order: ProjectSalesOrder,
        inquiry: OrderInquiry,
        row_count: int,
        to_buy: int,
        project_label: Optional[str],
    ) -> None:
        """QUEUED, never sent from here.

        `NotificationService.create_with_channel_preferences` COMMITS (its own docstring:
        "never call it from inside a `db.begin_nested()` block"), and this runs deep inside
        one - `refresh_for_decision` is called from the confirm, and the planning-change
        apply wraps every order in a savepoint. The commit released that savepoint, so a
        perfectly written revision came back as "This transaction is closed" the moment a
        purchasing-role user existed to be notified (measured on `confirm-all` with a batch
        whose confirmation raised a NEW inquiry - Slice C, where CS confirming a delayed
        line's re-run does exactly that).

        So the payload is queued on `Session.info` and fired by
        `register_order_inquiry_post_commit_dispatch`'s `after_commit` listener on a FRESH
        session - the same pattern `_dispatch_changed_with_links` above already uses, for
        the same reason, and the same one `planning_change_service.apply` follows when it
        notifies purchasing after each order's savepoint rather than inside it.
        """
        reference = order.autocount_doc_no or order.provisional_ref
        user_ids = self._purchasing_user_ids()
        if not user_ids:
            return
        # A registered project's title wins; an adopted order (no project) falls back
        # to the core sales order's own free-text label, then to its reference when
        # neither exists (PLAN-oi-project-label-from-so.md section 4).
        heading = (project.title if project else None) or project_label or reference
        self.db.info.setdefault(_PURCHASING_NOTIFY_PENDING_KEY, []).append(
            {
                #: Which savepoint this was earned under (C2), so a sibling order's
                #: rollback cannot discard it.
                "tx_chain": _transaction_chain(self.db),
                "user_ids": [str(user_id) for user_id in user_ids],
                "title": f"Order inquiry {reference}",
                "body": (
                    f"{heading}: {row_count} instruction"
                    f"{'' if row_count == 1 else 's'}, {to_buy} still to buy."
                ),
                "data": {
                    "project_id": str(project.id) if project else None,
                    "project_code": project.project_code if project else None,
                    "order_inquiry_id": str(inquiry.id),
                    "sales_order_ref": reference,
                    "row_count": row_count,
                    "to_buy": to_buy,
                },
                "inquiry_id": str(inquiry.id),
            }
        )

    def _purchasing_user_ids(self) -> List[str]:
        """Everyone holding a purchasing role - `purchasing`, `purchasing_manager`,
        `purchasing_executive`, or any other slug SCM's own family grows to (AC-X1) - which
        is what SCM is granted through. Matched by prefix, not substring: a role that merely
        contains the word (an admin role covering purchasing among other things) is not one."""
        from app.models.user import User, UserRole, UserRoleAssignment, UserStatus

        rows = (
            self.db.query(UserRoleAssignment.user_id)
            .join(UserRole, UserRole.id == UserRoleAssignment.role_id)
            .join(User, User.id == UserRoleAssignment.user_id)
            .filter(
                UserRole.slug.like("purchasing%"),
                User.status == UserStatus.ACTIVE.value,
                User.is_trashed.is_(False),
            )
            .distinct()
            .all()
        )
        return [str(row[0]) for row in rows]

    def _buying_count(self, inquiry_id: str) -> int:
        return (
            self.db.query(func.count(OrderInquiryRow.id))
            .filter(
                OrderInquiryRow.order_inquiry_id == inquiry_id,
                # A borrow shortfall is buying work too: the donor is oversold and
                # somebody has to buy the hole (PLAN 13.11).
                OrderInquiryRow.verb.in_(
                    [IV_ORDER, IV_RESERVE_AND_ORDER, IV_ORDER_BACK]
                ),
            )
            .scalar()
            or 0
        )

    def task_for(self, inquiry_id: str) -> Optional[ProjectTask]:
        return (
            self.db.query(ProjectTask)
            .filter(
                ProjectTask.linked_entity_type == TASK_LINK_ORDER_INQUIRY,
                ProjectTask.linked_entity_id == inquiry_id,
            )
            .first()
        )

    # -------------------------------------------------------------- reading

    def list_rows(
        self,
        project_id: str,
        *,
        query: Optional[str] = None,
        verb: Optional[Sequence[str]] = None,
        state: Optional[Sequence[str]] = None,
        pso_id: Optional[str] = None,
        page: int = 1,
        limit: int = 50,
        sort: str = "delivery_date",
        direction: str = "asc",
    ) -> Tuple[List[Dict[str, Any]], int]:
        """Every instruction raised on one project, newest inquiry first by default."""
        base = self._rows_query(project_id, query=query, verb=verb, state=state, pso_id=pso_id)
        total = base.with_entities(func.count(OrderInquiryRow.id)).scalar() or 0

        sortable = {
            "delivery_date": OrderInquiryRow.delivery_date,
            "item_code": OrderInquiryRow.item_code,
            "qty": OrderInquiryRow.qty,
            "verb": OrderInquiryRow.verb,
            "state": OrderInquiryRow.state,
            "created_at": OrderInquiryRow.created_at,
        }
        column = sortable.get(sort, OrderInquiryRow.delivery_date)
        ordering = column.desc() if str(direction).lower() == "desc" else column.asc()
        rows = (
            base.order_by(ordering, OrderInquiryRow.item_code.asc())
            .offset(max(page - 1, 0) * limit)
            .limit(limit)
            .all()
        )
        return self.serialize_rows(rows), int(total)

    def all_rows(
        self,
        project_id: str,
        *,
        query: Optional[str] = None,
        verb: Optional[Sequence[str]] = None,
        state: Optional[Sequence[str]] = None,
        pso_id: Optional[str] = None,
    ) -> List[OrderInquiryRow]:
        """The same set the list serves, unpaged, for the export."""
        return (
            self._rows_query(project_id, query=query, verb=verb, state=state, pso_id=pso_id)
            .order_by(OrderInquiryRow.created_at.asc(), OrderInquiryRow.item_code.asc())
            .all()
        )

    def _rows_query(
        self,
        project_id: str,
        *,
        query: Optional[str],
        verb: Optional[Sequence[str]],
        state: Optional[Sequence[str]],
        pso_id: Optional[str],
    ):
        base = (
            self.db.query(OrderInquiryRow)
            .join(OrderInquiry, OrderInquiry.id == OrderInquiryRow.order_inquiry_id)
            .join(
                ProjectSalesOrder,
                ProjectSalesOrder.id == OrderInquiry.project_sales_order_id,
            )
            .filter(ProjectSalesOrder.project_id == project_id)
        )
        if pso_id:
            base = base.filter(OrderInquiry.project_sales_order_id == pso_id)
        if query:
            like = f"%{query.strip()}%"
            base = base.filter(
                or_(
                    OrderInquiryRow.item_code.ilike(like),
                    OrderInquiryRow.spo_ref.ilike(like),
                    OrderInquiryRow.stock_location.ilike(like),
                    ProjectSalesOrder.autocount_doc_no.ilike(like),
                    ProjectSalesOrder.provisional_ref.ilike(like),
                )
            )
        if verb:
            base = base.filter(OrderInquiryRow.verb.in_(list(verb)))
        if state:
            base = base.filter(OrderInquiryRow.state.in_(list(state)))
        return base

    def summary(self, project_id: str) -> Dict[str, Any]:
        """How much of this project's inquiry is still open, for the screen's header."""
        rows = (
            self._rows_query(project_id, query=None, verb=None, state=None, pso_id=None)
            .with_entities(OrderInquiryRow.state, func.count(OrderInquiryRow.id))
            .group_by(OrderInquiryRow.state)
            .all()
        )
        counts = {state: 0 for state in INQUIRY_STATES}
        total = 0
        for state, count in rows:
            counts[state] = int(count)
            # Summed off the actual rows, not off the pre-seeded keys: a `placed` row
            # (section G) grows this dict dynamically rather than being one of the
            # states pre-seeded above, and a total that only added the three seeded
            # keys would silently drop it.
            total += int(count)
        counts["total"] = total
        return counts

    def serialize_rows(self, rows: Sequence[OrderInquiryRow]) -> List[Dict[str, Any]]:
        if not rows:
            return []
        context, names = self._context_for(rows)
        traces = self._decision_traces(rows)
        product_by_row = self._resolve_product_ids_bulk(rows)
        candidates = self.link_candidate_products(set(product_by_row.values()))
        links_by_row = self.links_for_rows([row.id for row in rows])
        suggested_links_by_row = self.suggested_links_for_rows([row.id for row in rows])
        linked_by_row = self._linked_qty_by_row([row.id for row in rows])
        # PLAN-scm-supplied-with-companions.md S5: the anchor's own item code, for the
        # rows that carry a bundle - one query for the whole page rather than one per row.
        anchor_ids = {row.bundled_with_row_id for row in rows if row.bundled_with_row_id}
        anchor_item_code_by_id: Dict[str, str] = {}
        if anchor_ids:
            anchor_item_code_by_id = dict(
                self.db.query(OrderInquiryRow.id, OrderInquiryRow.item_code)
                .filter(OrderInquiryRow.id.in_(anchor_ids))
                .all()
            )
        # Review round 1 item 10: the rule set behind every bundled row, resolved ONCE
        # for the whole page rather than once per row.
        bundle_map: Dict[str, List[str]] = {}
        codes_by_company: Dict[str, set] = {}
        for row in rows:
            if row.bundled_with_row_id:
                codes_by_company.setdefault(row.company_id, set()).add(row.item_code)
        for company_id, codes in codes_by_company.items():
            bundle_map.update(
                _bundled_with_item_codes_map(
                    self.db, company_id=company_id, companion_item_codes=codes
                )
            )
        out: List[Dict[str, Any]] = []
        for row in rows:
            meta = context.get(row.order_inquiry_id, {})
            trace = traces.get(row.id, {})
            out.append(
                {
                    "id": row.id,
                    "order_inquiry_id": row.order_inquiry_id,
                    "so_line_id": row.so_line_id,
                    "sales_order_ref": meta.get("sales_order_ref"),
                    "project_sales_order_id": meta.get("project_sales_order_id"),
                    # AC-B6-7 (`PLAN-board-oi-mechanical-22sep.md`, S6): the deep-link
                    # ids the OI Lines tab / worklist's own "SO line" column resolves to
                    # `/scm/sales-orders/<sales_order_id>?tab=lines&line=<core_line_id>`
                    # - never printed, addressing only.
                    "sales_order_id": meta.get("sales_order_id"),
                    "core_line_id": trace.get("core_line_id"),
                    # AC-D06: the buyer traces a Buy back to the Project SO, the line
                    # number and the revision that decided it, in identifiers a person
                    # reads - never an id.
                    "project_so_ref": meta.get("project_so_ref"),
                    "line_no": trace.get("line_no"),
                    "decision_revision": trace.get("decision_revision"),
                    "so_date": meta.get("so_date"),
                    "project_customer": meta.get("project_customer"),
                    "is_amendment": meta.get("is_amendment", False),
                    "item_code": row.item_code,
                    "qty": _qty_str(_dec(row.qty)),
                    "delivery_date": row.delivery_date,
                    "stock_location": row.stock_location,
                    "verb": row.verb,
                    "remark": self._remark(row),
                    "spo_ref": row.spo_ref,
                    "po_ref": row.po_ref,
                    "po_line_id": row.po_line_id,
                    "cited_document": row.cited_document,
                    # PLAN-scm-supplied-with-companions.md S5: how much of this row
                    # rides inside another item's own line, and which row anchors it.
                    # `response_model` drops what it is not told about - both asserted
                    # in `test_order_inquiry_bundles.py::test_d7`.
                    "bundled_qty": _qty_str(_dec(row.bundled_qty)),
                    "bundled_with": (
                        {
                            "row_id": row.bundled_with_row_id,
                            "item_code": anchor_item_code_by_id.get(row.bundled_with_row_id),
                            "item_codes": _resolve_bundled_item_codes(
                                bundle_map,
                                companion_item_code=row.item_code,
                                anchor_item_code=anchor_item_code_by_id.get(
                                    row.bundled_with_row_id
                                ),
                            ),
                        }
                        if row.bundled_with_row_id
                        else None
                    ),
                    # WHERE the quantity actually sits (AC-I5/AC-I9). `po_ref` above is the
                    # first of these, kept for the older readers that print one number.
                    "links": links_by_row.get(row.id, []),
                    # AC-LT-33 (`PLAN-oi-links-autocount-truth-24sep.md` 3.5): the
                    # cascade's own guesses, kept separate from `links` above, which
                    # carries nothing suggested.
                    "suggested_links": suggested_links_by_row.get(row.id, []),
                    "linked_qty": _qty_str(linked_by_row.get(row.id, _ZERO)),
                    "has_link_candidate": self.has_link_candidate(
                        row.verb, product_by_row.get(row.id), candidates
                    ),
                    "covered_by": row.covered_by,
                    "note": row.note,
                    "state": row.state,
                    "actioned_at": row.actioned_at,
                    "actioned_by_name": names.get(row.actioned_by),
                    "created_at": row.created_at,
                    # The handshake, beside the supply state (AC-H14). Every column on
                    # the wire: `response_model` drops what it has not been declared.
                    "ack_state": row.ack_state,
                    "acknowledged_by_name": names.get(row.acknowledged_by),
                    "acknowledged_at": row.acknowledged_at,
                    "rejected_by_name": names.get(row.rejected_by),
                    "rejected_at": row.rejected_at,
                    "rejected_reason": row.rejected_reason,
                    "changed_at": row.changed_at,
                    # What the row said before the last settle restated it. The Was / Now
                    # table reads these, never the note's own sentence.
                    "previous_qty": (
                        _qty_str(_dec(row.previous_qty))
                        if row.previous_qty is not None
                        else None
                    ),
                    "previous_delivery_date": row.previous_delivery_date,
                    "previous_item_code": row.previous_item_code,
                }
            )
        return out

    def links_for_rows(self, row_ids: Sequence[str]) -> Dict[str, List[Dict[str, Any]]]:
        """Every link on these rows, serialized, keyed by row - one query for a page.

        The ONE reader of `projects.order_inquiry_links` for a screen, used by the
        per-project list, the cross-project worklist and the SCM sales-order detail, so the
        three surfaces answer "where is this linked" with one voice (section 3.I: "Same
        data as the worklist and the PO occupancy panel, one reader").

        Everything a person reads comes off the link itself or off the document it names -
        never an id: `document` is denormalised on the link precisely so a purchase order
        line that has since been re-imported cannot make the answer disappear. `po_id`
        addresses the PO popover and is null on an SPO link, because there is no purchase
        order to open THAT way; `purchase_order_id` (L4, review round) is the header id
        either kind's document is written against - a plain PO link's own `po_id`, or an
        SPO link's allocation traced back to its SPO's header (`SpoPOLine`/`SpoPO`, a
        SECOND alias of the same tables above: the first pair's join is keyed off
        `OrderInquiryLink.po_line_id`, which an SPO link never sets, so it cannot also
        answer for `SPOAllocation.po_line_id`).

        `source_po_number` (owner's 9 Sep feedback): the purchase order an SPO link's
        allocation was raised FROM, per the AutoCount feed's own statement
        (`SPOAllocation.from_po_number`, migration 493 / contract 2.2) - a different
        question from `purchase_order_id` above, which only ever answers for a
        Sorento-raised SPO carrying its own resolved `po_line_id`. Plain text, null on a
        PO-kind link and on an SPO the book named no source for.
        """
        wanted = [row_id for row_id in row_ids if row_id]
        if not wanted:
            return {}
        rows = (
            self.db.query(
                OrderInquiryLink,
                OrderInquiryRow.stock_location,
                OrderInquiryRow.delivery_date,
                PurchaseOrder.id,
                PurchaseOrder.po_number,
                PurchaseOrder.issue_date,
                PurchaseOrderLine.expected_date,
                PurchaseOrderLine.source_ref,
                # S5, R-D: the PO line's own product - what a derived SPO allocation has
                # to match, beside the PO number, before it can stand in for this link.
                PurchaseOrderLine.product_id,
                Warehouse.warehouse_code,
                SPOAllocation.spo_number,
                SPOAllocation.spo_line_number,
                SPOAllocation.issue_date,
                SPOAllocation.expected_date,
                SPOAllocation.location_code,
                SPOAllocation.from_po_number,
                # R17 (owner rulings, 25 Sep 2026): the supply PO line this allocation
                # draws down. Resolved to a header id through a FOLLOW-UP query
                # (`_purchase_order_ids_for_supply_lines` below), never a second JOINED
                # alias of `PurchaseOrderLine` in THIS query - that shape silently
                # returned NULL here (the session's company-scope `with_loader_criteria`
                # does not disambiguate two occurrences of the same mapped class inside
                # one query the way `include_aliases=True` promises to), which is why
                # `purchase_order_id` never reached the wire despite the L4 review item
                # believing it already worked.
                SPOAllocation.po_line_id,
                # S1 (`PLAN-oi-replan-received-links.md`, AC-RL-17): the receipt figure
                # every link states, and the fields the "fully received" test reads for
                # whichever book this link names.
                PurchaseOrderLine.qty_ordered,
                PurchaseOrderLine.qty_received,
                PurchaseOrderLine.line_status,
                SPOAllocation.quantity_received,
                SPOAllocation.receipt_status,
                SPOAllocation.line_status,
                InboundShipment.actual_arrival_date,
            )
            .join(OrderInquiryRow, OrderInquiryRow.id == OrderInquiryLink.row_id)
            .outerjoin(PurchaseOrderLine, PurchaseOrderLine.id == OrderInquiryLink.po_line_id)
            .outerjoin(
                PurchaseOrder, PurchaseOrder.id == PurchaseOrderLine.purchase_order_id
            )
            .outerjoin(
                SPOAllocation, SPOAllocation.id == OrderInquiryLink.spo_allocation_id
            )
            .outerjoin(
                Warehouse,
                Warehouse.id
                == func.coalesce(
                    PurchaseOrderLine.warehouse_id, SPOAllocation.warehouse_id
                ),
            )
            .outerjoin(
                InboundShipment,
                InboundShipment.id == SPOAllocation.inbound_shipment_id,
            )
            .filter(
                OrderInquiryLink.row_id.in_(wanted),
                # A cancelled row's links are history, not an answer to "where does this
                # quantity sit": the quantity is not owed any more. A superseded revision
                # would otherwise keep printing its documents on the SO detail beside the
                # revision that replaced it.
                OrderInquiryRow.state != INQUIRY_CANCELLED,
                # PLAN-oi-request-cs-reserve.md (AC-RS-12): a reserve link is not a PO or
                # an SPO document - this reader's whole vocabulary is "which BOOK is this
                # on" - so it never leaks in here as a `kind="po"` entry with every book
                # column blank. `reserved_qty` (the worklist serializer) is where it
                # actually surfaces.
                OrderInquiryLink.reserve_request_row_id.is_(None),
                # R7/AC-E9: SPOAllocation is OUTER-joined, so this passes a plain PO
                # link (its columns come back NULL) untouched and only excludes a
                # link whose SPO side names a retired line.
                *spo_supply.visible_line_clauses(),
            )
            .order_by(OrderInquiryLink.linked_at.asc(), OrderInquiryLink.id.asc())
            .all()
        )
        pools = self._pool_codes()
        from app.services.project_service import resolve_user_names

        names = resolve_user_names(
            self.db, [link.linked_by for link, *_rest in rows if link.linked_by]
        )
        out: Dict[str, List[Dict[str, Any]]] = {}
        # S5, R-D: every PO-kind link's own (po_number, product_id) - what a derived SPO
        # allocation has to match. Collected while the loop is on the real links anyway,
        # so the second query below runs once for the whole page rather than once per row.
        #
        # A SET PER ROW (AC-D13), never one pair: a row linked to two purchase orders
        # has two pairs, and a dict of one pair per row silently kept the LAST link's
        # and showed only that purchase order's derived SPO.
        po_pairs_by_row: Dict[str, set] = {}
        # R17: entries needing their `purchase_order_id` backfilled once the follow-up
        # query below resolves `spo_supply_po_line_id -> purchase_order_id`, keyed by
        # the supply PO line id so one query answers every link on the page.
        entries_awaiting_purchase_order_id: Dict[str, List[Dict[str, Any]]] = {}
        for (
            link,
            stock_location,
            row_needed_by,
            po_id,
            po_number,
            po_issue_date,
            po_expected_date,
            po_source_ref,
            po_product_id,
            warehouse_code,
            spo_number,
            spo_line_number,
            spo_issue_date,
            spo_expected_date,
            spo_location_code,
            spo_from_po_number,
            spo_supply_po_line_id,
            po_qty_ordered,
            po_qty_received,
            po_line_status,
            spo_quantity_received,
            spo_receipt_status,
            spo_line_status,
            spo_actual_arrival_date,
        ) in rows:
            is_spo = link.spo_allocation_id is not None
            if not is_spo and po_number and po_product_id:
                po_pairs_by_row.setdefault(link.row_id, set()).add(
                    (po_number, str(po_product_id))
                )
            location = warehouse_code or (spo_location_code if is_spo else None)
            tier, _sub = link_location_tier(stock_location, location, pools)
            arrives = spo_expected_date if is_spo else po_expected_date
            # AC-P3-7: the document lands after the row needs it. A fact about two dates,
            # derived here rather than stored, so it cannot go stale against either - and
            # never a reason to unlink: the quantity is still on that document, and taking
            # it off would leave the row with nothing rather than with something late.
            late = bool(arrives and row_needed_by and arrives > row_needed_by)
            # HOW late, in whole days (AC-D17). `None` rather than 0 when it is not late,
            # so the column has nothing to print instead of a zero that reads as on time.
            late_days = (arrives - row_needed_by).days if late else None
            # S1 (AC-RL-17, `PLAN-oi-replan-received-links.md`): the document is FULLY
            # received - a PO line whose `qty_received >= qty_ordered` or `line_status =
            # 'closed'`, or an SPO allocation that fails `spo_supply.
            # open_incoming_clauses()`. `bool(po_number)`/`bool(spo_number)` guard the
            # rare row whose target line was itself deleted (SET NULL) - nothing joined,
            # so there is no document to call received. `received_qty` states the
            # figure regardless of `received` - a partly received document says so too.
            if is_spo:
                spo_open = (
                    (spo_line_status is None or spo_line_status == "open")
                    and (
                        spo_receipt_status is None
                        or spo_receipt_status not in spo_supply.RECEIVED_RECEIPT_STATUSES
                    )
                    and spo_actual_arrival_date is None
                )
                received = bool(spo_number) and not spo_open
                received_qty = _qty_str(_dec(spo_quantity_received))
            else:
                # REV nit (17 Sep): same guard as `_received_documents_for` / `_is_
                # target_received` - a null or zero `qty_ordered` line is not
                # "received".
                received = bool(po_number) and (
                    po_line_status == "closed"
                    or (
                        _dec(po_qty_ordered) > _ZERO
                        and _dec(po_qty_received) >= _dec(po_qty_ordered)
                    )
                )
                received_qty = _qty_str(_dec(po_qty_received))
            entry = {
                    "id": link.id,
                    "kind": "spo" if is_spo else "po",
                    # S3 (`PLAN-oi-cascade-skip-early-arrival.md`): the target itself,
                    # so a reader deciding whether THIS row's own SO claims the line
                    # (`scm.order_link_claim`) can ask without a second query per link.
                    "po_line_id": link.po_line_id,
                    "spo_allocation_id": link.spo_allocation_id,
                    # The link's own copy first: the document it was made against, even
                    # when the line it named has since been deleted out from under it.
                    "document": link.document or spo_number or po_number,
                    "line_label": self._line_label(
                        spo_line_number if is_spo else po_source_ref
                    ),
                    "qty": _qty_str(_dec(link.qty)),
                    "location": location,
                    "issue_date": spo_issue_date if is_spo else po_issue_date,
                    "expected_date": spo_expected_date if is_spo else po_expected_date,
                    "tier": tier,
                    "late": late,
                    "late_days": late_days,
                    # S1, AC-RL-17: the receipt figure, stated on every link, and
                    # whether it makes the document FULLY received - the replan rule
                    # (S2) and the PO/SPO chip's `received` mark both read this.
                    "received": received,
                    "received_qty": received_qty,
                    "auto": bool(link.auto),
                    "linked_at": link.linked_at,
                    # WHO linked it, by name. Null on a cascade link, which nobody did.
                    "linked_by_name": names.get(link.linked_by),
                    "po_id": None if is_spo else po_id,
                    # L4 (review round): the header id EITHER kind's document lives on -
                    # a plain PO link's own `po_id`, or an SPO link's allocation traced
                    # back to its own supply PO line's header. The SPO half is filled in
                    # below, AFTER the follow-up query resolves it (see
                    # `entries_awaiting_purchase_order_id` above) - never here, where a
                    # second joined alias of `PurchaseOrderLine` silently returned NULL.
                    "purchase_order_id": None if is_spo else (str(po_id) if po_id else None),
                    # Owner's 9 Sep feedback against the running lane: "if we link by
                    # SPO, where do we see the PO number of this SPO?" - nowhere, before
                    # this. `from_po_number` is the raw AutoCount pass-through
                    # (`SPOAllocation.from_po_number`, migration 493 / contract 2.2), not
                    # `purchase_order_id` above - that traces a Sorento-raised SPO's own
                    # `po_line_id` FK, which is null for the ordinary case of a book-fed
                    # allocation the ESB simply STATED a source document for. Plain text,
                    # never a link yet (a later slice decides where it goes); None on a
                    # PO-kind link and on an SPO the book named no source for - never a
                    # guess. `from_po_line_ref` (the resolver key) is never sent - it is
                    # not a thing a buyer reads.
                    "source_po_number": spo_from_po_number if is_spo else None,
                    # S5, R-E: the mirror of the derived SPO entry below - this real link
                    # names the PO through an SPO allocation the book itself sourced it
                    # from, so the PO column marks the number "via SPO" rather than
                    # treating it as a link this system made independently.
                    "derived_po": bool(is_spo and spo_from_po_number),
                }
            out.setdefault(link.row_id, []).append(entry)
            if is_spo and spo_supply_po_line_id:
                entries_awaiting_purchase_order_id.setdefault(
                    str(spo_supply_po_line_id), []
                ).append(entry)
        if entries_awaiting_purchase_order_id:
            supply_lines = (
                self.db.query(PurchaseOrderLine.id, PurchaseOrderLine.purchase_order_id)
                .filter(
                    PurchaseOrderLine.id.in_(entries_awaiting_purchase_order_id.keys())
                )
                .all()
            )
            for supply_line_id, purchase_order_id in supply_lines:
                if not purchase_order_id:
                    continue
                for entry in entries_awaiting_purchase_order_id[str(supply_line_id)]:
                    entry["purchase_order_id"] = str(purchase_order_id)
        # Should fix 3 (review round 2): the ORDINARY via-SPO case, resolved on the
        # server rather than by the frontend scanning the worklist by number
        # (`useOrderInquiryPoIdByNumber`, now retired). The block above only ever
        # answers for a Sorento-raised SPO carrying its own `po_line_id` FK; most
        # book-fed allocations carry only the AutoCount pass-through PO NUMBER
        # (`from_po_number`, `source_po_number` on the wire) with no such FK, and a
        # PO reached ONLY through an SPO never appears as a `po`-kind link on any
        # row for the worklist scan to find. One batched `PurchaseOrder.po_number
        # IN (...)` for the whole page, exactly beside the block above.
        entries_awaiting_po_by_number: Dict[str, List[Dict[str, Any]]] = {}
        for entries in out.values():
            for entry in entries:
                source_po_number = entry.get("source_po_number")
                if (
                    entry.get("kind") == "spo"
                    and entry.get("purchase_order_id") is None
                    and source_po_number
                ):
                    entries_awaiting_po_by_number.setdefault(source_po_number, []).append(
                        entry
                    )
        if entries_awaiting_po_by_number:
            numbered_pos = (
                self.db.query(PurchaseOrder.po_number, PurchaseOrder.id)
                .filter(PurchaseOrder.po_number.in_(entries_awaiting_po_by_number.keys()))
                .all()
            )
            for po_number, purchase_order_id in numbered_pos:
                for entry in entries_awaiting_po_by_number.get(po_number, []):
                    entry["purchase_order_id"] = str(purchase_order_id)
        self._append_derived_spo_entries(out, po_pairs_by_row)
        return out

    def _append_derived_spo_entries(
        self, out: Dict[str, List[Dict[str, Any]]], po_pairs_by_row: Dict[str, set]
    ) -> None:
        """S5 (R-D, R-E; coordinator's 16 Sep addendum): a SYNTHETIC `spo`-kind entry per
        row, for every OPEN SPO allocation the row's own PO links' POs have for the same
        product - `from_po_number = po_number AND product_id = po_line.product_id`. OPEN
        is `spo_supply.open_incoming_clauses()` (line open, not received, shipment not
        landed) AND `retired_at IS NULL` AND `allocated_quantity > coalesce(quantity_
        received, 0)` - a landed shipment has already measured out, not incoming any
        more. Two open allocations on the same PO and product both appear; there is no
        tie-break to pick between them (the owner's ruling, 16 Sep). A row linked to two
        purchase orders carries BOTH POs' allocations (AC-D13), which is why the map
        above holds a SET of pairs per row.

        COMPANY-SCOPED BY HAND (AC-D14), not only by the session listener. The join is
        on `from_po_number` (plain text off the AutoCount feed) and `product_id`, and
        neither is company-scoped in itself, so another company's allocation naming the
        same PO number string and the same product would otherwise read as this row's
        incoming stock. The listener does fire here - `SPOAllocation` is an entity of
        this query - but the worklist's own reader states the predicate explicitly, and
        the two seams must not be able to differ if the listener ever changes.

        Written nowhere: this never creates an `order_inquiry_links` row, so
        `committed_v` and every demand read stay on real links only.
        """
        if not po_pairs_by_row:
            return
        pairs = sorted({pair for pairs_ in po_pairs_by_row.values() for pair in pairs_})
        company_predicate = build_company_predicate(
            SPOAllocation, get_company_scope(self.db)
        )
        allocations = (
            self.db.query(
                SPOAllocation.spo_number,
                SPOAllocation.from_po_number,
                SPOAllocation.product_id,
                SPOAllocation.allocated_quantity,
                SPOAllocation.quantity_received,
                SPOAllocation.location_code,
                SPOAllocation.expected_date,
                SPOAllocation.id,
                Warehouse.warehouse_code,
            )
            .outerjoin(
                InboundShipment, InboundShipment.id == SPOAllocation.inbound_shipment_id
            )
            .outerjoin(Warehouse, Warehouse.id == SPOAllocation.warehouse_id)
            .filter(
                tuple_(SPOAllocation.from_po_number, SPOAllocation.product_id).in_(pairs),
                *derived_spo_open_clauses(),
                *((company_predicate,) if company_predicate is not None else ()),
            )
            .all()
        )
        if not allocations:
            return
        by_pair: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
        for (
            spo_number,
            from_po_number,
            product_id,
            allocated,
            received,
            location_code,
            expected_date,
            allocation_id,
            warehouse_code,
        ) in allocations:
            open_qty = _dec(allocated) - _dec(received)
            by_pair.setdefault((from_po_number, str(product_id)), []).append(
                {
                    "id": f"derived-{allocation_id}",
                    "kind": "spo",
                    "derived": True,
                    "document": spo_number,
                    # This ONE entry is one specific `spo_allocations` row (the loop is
                    # over allocations, not products), so its own id is unambiguous even
                    # though WHICH allocations qualify as a candidate for this row is a
                    # product/PO-number match (R-D/R-E, unchanged) - never used to pick
                    # the entry, only to name the one it already is.
                    "spo_allocation_id": str(allocation_id),
                    # 17 Sep prod 500: `sales_order_service._line_links` hard-indexes
                    # `line_label`/`late`/`late_days` on EVERY entry `links_for_rows`
                    # returns, and reads `purchase_order_id` via `.get`. A synthetic
                    # entry carries the same shape as a real one - one reader, one
                    # contract - rather than making every consumer know an entry might
                    # be missing keys because it is derived.
                    "line_label": None,
                    "purchase_order_id": None,
                    "qty": _qty_str(open_qty),
                    "location": warehouse_code or location_code,
                    "expected_date": expected_date,
                    "late": False,
                    "late_days": None,
                }
            )
        for row_id, row_pairs in po_pairs_by_row.items():
            for pair in sorted(row_pairs):
                entries = by_pair.get(pair)
                if entries:
                    out.setdefault(row_id, []).extend(entries)

    def suggested_links_for_rows(
        self, row_ids: Sequence[str]
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Every SUGGESTED link on these rows (`PLAN-oi-links-autocount-truth-24sep.md`
        3.5, AC-LT-33) - a SEPARATE reader from `links_for_rows` above, because a
        suggestion is never a placement: the cascade's own guess, not purchasing's
        word. Same wire vocabulary as a real link where the two questions overlap
        (`kind`, `document`, `po_line_id`, `spo_allocation_id`, `location`, `qty`,
        `expected_date`, `late_days`), plus `trigger` (why the walk offered this) -
        and none of what only a real link carries: no `id` that addresses an unlink,
        no `linked_by`, no `received`, no claim.

        Filtered to `_open_for_buying_clauses` (review round 3 Should fix 1): a row
        `_retire_inquiry_rows` cancels never gets a `_drop_suggested_links` call of
        its own (`_shift_links_off_retired_lines` right after it only ever moves a
        REAL link, so a row holding just a suggestion is skipped there too), and any
        future writer could make the same omission. The same clause already keeps a
        cancelled row's suggestion out of `_suggested_totals_by_target`'s capacity
        count; applying it here too means the Suggested cell can never show a
        document for a row purchasing has been told is done, whether or not the
        writer that moved it remembered the drop.
        """
        wanted = [row_id for row_id in row_ids if row_id]
        if not wanted:
            return {}
        rows = (
            self.db.query(
                OrderInquirySuggestedLink,
                OrderInquiryRow.delivery_date,
                PurchaseOrder.id,
                PurchaseOrderLine.expected_date,
                Warehouse.warehouse_code,
                SPOAllocation.expected_date,
                SPOAllocation.location_code,
            )
            .join(OrderInquiryRow, OrderInquiryRow.id == OrderInquirySuggestedLink.row_id)
            .outerjoin(
                PurchaseOrderLine,
                PurchaseOrderLine.id == OrderInquirySuggestedLink.po_line_id,
            )
            .outerjoin(
                PurchaseOrder, PurchaseOrder.id == PurchaseOrderLine.purchase_order_id
            )
            .outerjoin(
                SPOAllocation,
                SPOAllocation.id == OrderInquirySuggestedLink.spo_allocation_id,
            )
            .outerjoin(Warehouse, Warehouse.id == PurchaseOrderLine.warehouse_id)
            .filter(
                OrderInquirySuggestedLink.row_id.in_(wanted),
                *self._open_for_buying_clauses(),
            )
            .order_by(
                OrderInquirySuggestedLink.suggested_at.asc(),
                OrderInquirySuggestedLink.id.asc(),
            )
            .all()
        )
        out: Dict[str, List[Dict[str, Any]]] = {}
        for (
            suggestion,
            row_needed_by,
            po_id,
            po_expected_date,
            warehouse_code,
            spo_expected_date,
            spo_location_code,
        ) in rows:
            is_spo = suggestion.spo_allocation_id is not None
            location = spo_location_code if is_spo else warehouse_code
            arrives = spo_expected_date if is_spo else po_expected_date
            late_days = (
                (arrives - row_needed_by).days
                if arrives and row_needed_by and arrives > row_needed_by
                else None
            )
            out.setdefault(suggestion.row_id, []).append(
                {
                    "kind": "spo" if is_spo else "po",
                    "document": suggestion.document,
                    "po_id": None if is_spo else po_id,
                    "po_line_id": suggestion.po_line_id,
                    "spo_allocation_id": suggestion.spo_allocation_id,
                    "location": location,
                    "qty": _qty_str(_dec(suggestion.qty)),
                    "expected_date": arrives,
                    "late_days": late_days,
                    "trigger": suggestion.trigger,
                }
            )
        return out

    def _context_for(
        self, rows: Sequence[OrderInquiryRow]
    ) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, str]]:
        """One query per fact the rows need, rather than one per row."""
        inquiry_ids = {row.order_inquiry_id for row in rows}
        joined = (
            self.db.query(OrderInquiry, ProjectSalesOrder, SalesOrder.order_date)
            .join(
                ProjectSalesOrder,
                ProjectSalesOrder.id == OrderInquiry.project_sales_order_id,
            )
            .outerjoin(SalesOrder, SalesOrder.id == ProjectSalesOrder.so_id)
            .filter(OrderInquiry.id.in_(list(inquiry_ids)))
            .all()
        )
        labels = self._project_customer_labels({so.id for _inq, so, _order_date in joined})
        context: Dict[str, Dict[str, Any]] = {}
        for inquiry, order, order_date in joined:
            context[inquiry.id] = {
                "project_sales_order_id": order.id,
                # AC-B6-7 (`PLAN-board-oi-mechanical-22sep.md`, S6): the CORE
                # `sales_orders.id` the SCM sales order detail page is keyed by
                # (`/scm/sales-orders/<sales_order_id>`) - null on a project order never
                # published to the core book.
                "sales_order_id": order.so_id,
                "sales_order_ref": order.autocount_doc_no or order.provisional_ref,
                # The Project SO's OWN reference, beside the AutoCount number the
                # sales_order_ref prefers: they are two different documents and the buyer
                # tracing a Buy back to a project needs the one this system minted.
                "project_so_ref": order.provisional_ref,
                # The AutoCount SO DOCUMENT date wins over the CRM's own pull date, same
                # rule as `_handover_order_facts`
                # (PLAN-oi-handover-so-date-autocount-25sep.md).
                "so_date": (order_date or order.published_at or order.created_at),
                "project_customer": labels.get(order.id),
                "is_amendment": bool(inquiry.amendment_id),
            }

        from app.services.project_service import resolve_user_names

        # Every person a row can name, resolved in ONE call: who acted on it, who
        # acknowledged it and who rejected it are three different people and the screen
        # prints all three by name (`PLAN-scm-oi-handshake.md` section 4).
        names = resolve_user_names(
            self.db,
            [
                user_id
                for row in rows
                for user_id in (row.actioned_by, row.acknowledged_by, row.rejected_by)
                if user_id
            ],
        )
        return context, names

    def _decision_traces(
        self, rows: Sequence[OrderInquiryRow]
    ) -> Dict[str, Dict[str, Any]]:
        """The line number and decision revision behind each row (AC-D06).

        Both are absent on an amendment exception row and on anything raised before
        Stage 1C, which is honest: those rows were not decided by a supply revision.
        """
        from app.models.project_so import SOSupplyDecision

        line_ids = {row.so_line_id for row in rows if row.so_line_id}
        decision_ids = {row.supply_decision_id for row in rows if row.supply_decision_id}
        line_nos: Dict[str, int] = {}
        # AC-B6-7: the mirror's own `core_sales_order_line_id` - the AutoCount line the
        # SCM Lines tab actually addresses - null when the mirror has no core line at all.
        core_line_ids: Dict[str, Optional[str]] = {}
        if line_ids:
            for line_id, line_no, core_line_id in (
                self.db.query(
                    ProjectSalesOrderLine.id,
                    ProjectSalesOrderLine.line_no,
                    ProjectSalesOrderLine.core_sales_order_line_id,
                )
                .filter(ProjectSalesOrderLine.id.in_(list(line_ids)))
                .all()
            ):
                line_nos[line_id] = line_no
                core_line_ids[line_id] = core_line_id
        revisions = (
            dict(
                self.db.query(SOSupplyDecision.id, SOSupplyDecision.revision_no)
                .filter(SOSupplyDecision.id.in_(list(decision_ids)))
                .all()
            )
            if decision_ids
            else {}
        )
        return {
            row.id: {
                "line_no": line_nos.get(row.so_line_id),
                "decision_revision": revisions.get(row.supply_decision_id),
                "core_line_id": core_line_ids.get(row.so_line_id),
            }
            for row in rows
        }

    def _project_customer_labels(self, pso_ids: set) -> Dict[str, Optional[str]]:
        """`BUIMACO / TUJU RESIDENCE` per sales order, via `project_customer_label`.

        The join to `Project` is OUTER, and that is a fix rather than a style choice: an
        order ADOPTED from the AutoCount book has no project registration by design, so an
        inner join answered nothing for it and the column came back blank on a row that
        plainly has a customer. When there is no project party to bill, the CORE sales
        order's own customer is that customer - it is the same document, read through the
        table it was imported into.
        """
        if not pso_ids:
            return {}
        rows = (
            self.db.query(
                ProjectSalesOrder.id,
                ProjectSalesOrder.is_pre_order,
                Project.title,
                Customer.customer_name,
                SalesOrder.project_label,
            )
            .outerjoin(Project, Project.id == ProjectSalesOrder.project_id)
            .outerjoin(
                ProjectPurchaseOrder,
                ProjectPurchaseOrder.id == ProjectSalesOrder.purchase_order_id,
            )
            .outerjoin(ProjectParty, ProjectParty.id == ProjectPurchaseOrder.issuing_party_id)
            .outerjoin(SalesOrder, SalesOrder.id == ProjectSalesOrder.so_id)
            # ONE join through a coalesce rather than two aliases of `customers`: the
            # company-scope listener emits an UNALIASED `customers.company_id` into an
            # aliased ON clause, which Postgres refuses outright.
            .outerjoin(
                Customer,
                Customer.id
                == func.coalesce(ProjectParty.customer_id, SalesOrder.customer_id),
            )
            .filter(ProjectSalesOrder.id.in_(list(pso_ids)))
            .all()
        )
        return {
            pso_id: project_customer_label(
                customer_name, title or project_label, is_pre_order
            )
            for pso_id, is_pre_order, title, customer_name, project_label in rows
        }

    def _remark(self, row: OrderInquiryRow) -> str:
        """The REMARK column, spelled the way the client's own file spells it.

        An inbound row prints its SPO reference rather than a verb, because the
        reference is the thing purchasing looks up when they want to know when it lands.
        """
        if row.verb == IV_ALREADY_INBOUND and row.spo_ref:
            return row.spo_ref
        return REMARK_SPELLING.get(row.verb, row.verb)

    def get_for_sales_order(self, pso_id: str) -> Optional[Dict[str, Any]]:
        """The latest inquiry raised on one sales order, with its rows."""
        inquiry = (
            self.db.query(OrderInquiry)
            .filter(OrderInquiry.project_sales_order_id == pso_id)
            .order_by(OrderInquiry.raised_at.desc())
            .first()
        )
        if inquiry is None:
            return None
        rows = (
            self.db.query(OrderInquiryRow)
            .filter(OrderInquiryRow.order_inquiry_id == inquiry.id)
            .order_by(OrderInquiryRow.created_at.asc())
            .all()
        )
        task = self.task_for(inquiry.id)
        return {
            "id": inquiry.id,
            # What the screen prints. The id is addressing; this is the name.
            "inquiry_no": inquiry.inquiry_no,
            "project_sales_order_id": inquiry.project_sales_order_id,
            "amendment_id": inquiry.amendment_id,
            "state": inquiry.state,
            "raised_at": inquiry.raised_at,
            "task_id": task.id if task else None,
            "task_name": task.name if task else None,
            "rows": self.serialize_rows(rows),
        }

    # --------------------------------------------------------------- acting

    def mark_rows(
        self, row_ids: Sequence[str], *, state: str, actor_user_id: str
    ) -> List[Dict[str, Any]]:
        """Purchasing says what happened to a row (AC-I7)."""
        if state not in (INQUIRY_ACTIONED, INQUIRY_CANCELLED, INQUIRY_RAISED):
            raise AppException(
                status_code=422,
                message="An inquiry row is raised, actioned or cancelled.",
                code="order_inquiry_state_invalid",
            )
        if not row_ids:
            raise AppException(
                status_code=422,
                message="Name at least one row.",
                code="order_inquiry_no_rows",
            )
        rows = (
            self.db.query(OrderInquiryRow)
            .filter(OrderInquiryRow.id.in_(list(row_ids)))
            .all()
        )
        found = {row.id for row in rows}
        missing = [row_id for row_id in row_ids if row_id not in found]
        if missing:
            raise AppException(
                status_code=404,
                message=f"{len(missing)} of those rows no longer exist.",
                code="order_inquiry_row_not_found",
            )
        now = datetime.utcnow()
        for row in rows:
            # A row this bulk action moves OFF `placed` (section G) drops its PO tag too:
            # "blank means not placed yet" is what the worklist's PO no / Supplier columns
            # promise, and a cancelled or reopened row that still carried one would read
            # as placed when it no longer is. The claim itself is left alone here - it is
            # evidence of what the row WAS tagged to, and only Untag owns removing it.
            if row.state == INQUIRY_PLACED and state != INQUIRY_PLACED:
                row.po_ref = None
                row.po_line_id = None
            row.state = state
            # Back to raised is an undo, and an undo has to clear the claim it made or
            # the row would still read as something somebody dealt with.
            row.actioned_by = actor_user_id if state != INQUIRY_RAISED else None
            row.actioned_at = now if state != INQUIRY_RAISED else None
        self.db.flush()
        # Review round 2 Blocking 4 (AC-LT-18): this is a CANCEL/ACTIONED writer that
        # never runs `refresh_link_state` (it sets `state` directly, on purchasing's own
        # word), so it is the one place `_drop_suggested_links` has to be called by
        # hand - a row this press just moved off "open for buying" must not go on
        # holding a suggestion nobody is ever going to act on.
        self._drop_suggested_links(
            [row for row in rows if row.state in (INQUIRY_ACTIONED, INQUIRY_CANCELLED)]
        )
        self._refresh_inquiry_states({row.order_inquiry_id for row in rows})
        return self.serialize_rows(rows)

    # ----------------------------------------------------------- the handshake

    def acknowledge_rows(
        self,
        row_ids: Sequence[str],
        *,
        actor_user_id: str,
        link_up_to: Optional[date] = None,
        link_horizon: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Purchasing's own Confirm press: takes these instructions on, and the cascade
        runs for exactly them.

        `PLAN-scm-oi-handshake.md` section 3 (captain, 27 Aug 2026), reinstated as the
        page's own primary press by `PLAN-oi-confirm-per-so.md` S1/S2 (owner ruling 17 Sep
        2026), which retires G4's "born acknowledged, no manual confirm anywhere" reading.
        Two things happen in one press and they are one decision: the row becomes
        purchasing's work, and the documents that can cover it are tied to it if they were
        not already - most of them are, since raising and every other cascade door already
        run `include_awaiting=True` and never waited for this press (AC-CF-4). What Confirm
        adds is the handshake itself: the row leaves To confirm, is stamped with who and
        when, and only a confirmed SO's demand counts for reorder planning (S5).

        The guard stays TOLERANT of a row already acknowledged rather than refusing it: it
        is left exactly as it is - not re-stamped with a fresh time and name, because that
        would move the record of who actually confirmed it - and still joins the cascade
        below, so a repeated press (a double click, two tabs, "Select all N matching"
        catching a row somebody else just confirmed) is a no-op on the handshake and a real
        re-run of Link. Only a REJECTED row is refused: taking it back is CS re-deciding the
        line, not purchasing changing its mind about a row that no longer counts.

        The row's SUPPLY state is refused on too, but only when it is CANCELLED
        (PLAN-oi-cancelled-line-used-confirm.md, section 3.7, owner ruling 20 Sep 2026):
        a cancelled row was called off, so taking it on is taking on work nobody is
        doing. An ACTIONED row is different - it was answered somewhere else, but "seen"
        is still true of it (C2), and `_linkable_row_clauses` already keeps every
        non-linkable state, ACTIONED included, out of the cascade below, so confirming
        one takes the handshake stamp and links nothing.

        `link_up_to` is the LINK HORIZON the cascade half of the press runs under (section
        11): every named row is TAKEN ON whatever its date, and only the linking stops at
        the horizon, because the acknowledgement is the buyer reading the instruction and
        the link is the buyer answering it. `after_horizon` is what the banner reports as
        "N after <date>".
        """
        rows = self._rows_or_404(row_ids)
        gone = [row for row in rows if row.state == INQUIRY_CANCELLED]
        if gone:
            raise AppException(
                status_code=422,
                message=(
                    f"{len(gone)} of those rows are no longer open: a cancelled row "
                    "is nobody's work to take on."
                ),
                code="order_inquiry_row_not_open",
            )
        refused = [row for row in rows if row.ack_state == ACK_REJECTED]
        if refused:
            raise AppException(
                status_code=422,
                message=(
                    f"{len(refused)} of those rows cannot be acknowledged: a rejected "
                    "row goes back to CS."
                ),
                code="order_inquiry_not_acknowledgeable",
            )
        now = datetime.utcnow()
        transitioned = 0
        for row in rows:
            # Already acknowledged: left untouched rather than re-stamped, so a repeated
            # press cannot move who took the row on or when. Both AWAITING (never
            # confirmed) and CHANGED (confirmed once, since amended) transition here -
            # Confirm is the one press that takes either kind of To-confirm row on.
            if row.ack_state == ACK_ACKNOWLEDGED:
                continue
            row.ack_state = ACK_ACKNOWLEDGED
            row.acknowledged_by = actor_user_id
            row.acknowledged_at = now
            transitioned += 1
        transitioned += self._acknowledge_used_rows_of(
            rows, actor_user_id=actor_user_id, now=now
        )
        # FLUSHED before the cascade: the session runs `autoflush=False` the way the
        # application's does, so the pass below would read these rows at their OLD
        # acknowledgement state and link none of them.
        self.db.flush()
        placed = self.auto_place_for_products(
            None,
            actor_user_id=actor_user_id,
            trigger="acknowledge",
            row_ids=[str(row.id) for row in rows],
            link_up_to=link_up_to,
            link_horizon=link_horizon,
        )
        return {
            # Rows actually TRANSITIONED (nit, review of PR #471), not every row named:
            # the guard is tolerant of an already-acknowledged row, and counting it as
            # "acknowledged" here would tell a caller it did something to a row this
            # press left untouched.
            "acknowledged": transitioned,
            "linked_rows": placed["placed_rows"],
            "links": placed["allocations"],
            "after_horizon": placed["after_horizon"],
            "link_up_to": placed["link_up_to"],
            "link_horizon": placed["link_horizon"],
        }

    def _acknowledge_used_rows_of(
        self, rows: Sequence[OrderInquiryRow], *, actor_user_id: str, now: datetime
    ) -> int:
        """G6 (`PLAN-oi-no-double-count-25sep.md`, owner ruling 26 Sep 2026): the OI
        detail shows ONE row per sales order line and puts a used row in History only, so
        confirming a line also takes on that line's WAITING used rows - on the SAME header
        and the SAME sales order line as a confirmed row, never another line or another
        header (AC-ND-24, AC-ND-25). Without it the header sits Outstanding on a row
        nobody can see.

        A used row waits while it is not cancelled and its ack is awaiting or changed:
        the same rule `lines_to_confirm` counts and the Lines tab reads as To confirm
        (PR #1266 review S2), so a used row redirected before anyone read it (still
        `awaiting`) is taken on too. A cancelled used row is history and stays as it is.
        Swept rows are not linked: a used row's goods were released to the pool.

        The (header, line) PAIR is matched in the query itself (review N1): one Confirm
        can name header A line 1 and header B line 2, and a used row on header A line 2
        is on neither pair."""
        lines = {
            (str(row.order_inquiry_id), str(row.so_line_id))
            for row in rows
            if row.so_line_id is not None
        }
        if not lines:
            return 0
        named = {str(row.id) for row in rows}
        used = (
            self.db.query(OrderInquiryRow)
            .filter(
                tuple_(OrderInquiryRow.order_inquiry_id, OrderInquiryRow.so_line_id).in_(
                    list(lines)
                ),
                OrderInquiryRow.redirected_to_pool.is_(True),
                OrderInquiryRow.ack_state.in_((ACK_AWAITING, ACK_CHANGED)),
                OrderInquiryRow.state != INQUIRY_CANCELLED,
            )
            .all()
        )
        swept = 0
        for row in used:
            if str(row.id) in named:
                continue
            row.ack_state = ACK_ACKNOWLEDGED
            row.acknowledged_by = actor_user_id
            row.acknowledged_at = now
            swept += 1
        return swept

    def unacknowledge_rows(
        self, row_ids: Sequence[str], *, actor_user_id: str
    ) -> Dict[str, Any]:
        """Unconfirm (N) (PLAN-oi-worklist-split-customer-project.md, Slice 3, owner 18
        Sep 2026) - the reverse of `acknowledge_rows`, for a row purchasing took on by
        mistake or a re-confirm CS has not actually made yet. It is reversible (Confirm
        again undoes it), so unlike every write above this refuses NOTHING: a row not
        currently `acknowledged`/`changed`, or CANCELLED regardless of what its
        `ack_state` still reads (a superseded row's handshake is history, not something
        to reopen) - already `awaiting`, `rejected`, or gone from this company's own
        scope entirely - is counted on `skipped` rather than raised as a 404 or a 422 for
        the whole batch. `row_ids` only, deliberately no `filter` branch: the Actions
        menu names exactly what is ticked, never "everything matching a scope" the way
        "Select all N matching" does for Confirm.

        Company-scoped the same way `acknowledge_rows`'s own `_rows_or_404` is: a bare
        `db.query(OrderInquiryRow)` naming the model at the TOP level of the statement,
        so the session's own `company_scope` listener (`with_loader_criteria`) silently
        excludes another company's row from `rows` below - it never reaches `found_by_id`
        and so counts as `skipped`, the same as a row_id nobody can find at all.

        Clears `ack_state`/`acknowledged_by`/`acknowledged_at` only. `changed_at` STAYS
        (owner ruling, review round 1): it is the Was/Now audit trail
        `_settle_row_in_place` reads and `_handshake_for_raise` carries forward across a
        carry, not a stamp of Unconfirm's own to clear.

        No cascade: taking a row off purchasing's plate does not touch whatever it was
        already linked to - only Unlink, by its own press, does that.

        `OrderInquiryRow` carries no `__audit_track__` (review round 1, security item d:
        verified by `test_oi_unconfirm.py::test_unacknowledge_writes_an_audit_log_entry`,
        which fails without this), so the generic session-dirty listener never sees this
        write - one `log_audit` call per BATCH, naming the actor and every row id this
        call actually moved, the same manual-event pattern `procurement_service.py` and
        `project_supply_undo_service.py` use for a write outside that listener's reach.
        Nothing is written when nothing was eligible - an audit entry for zero rows moved
        would be a log of NOT doing something.
        """
        wanted = [str(row_id) for row_id in row_ids if row_id]
        if not wanted:
            raise AppException(
                status_code=422,
                message="Name at least one row.",
                code="order_inquiry_no_rows",
            )
        rows = self.db.query(OrderInquiryRow).filter(OrderInquiryRow.id.in_(wanted)).all()
        found_by_id = {str(row.id): row for row in rows}
        updated = 0
        skipped = 0
        touched_ids: List[str] = []
        prior_states: List[Dict[str, Any]] = []
        for row_id in wanted:
            row = found_by_id.get(row_id)
            if (
                row is None
                or row.state == INQUIRY_CANCELLED
                or row.ack_state not in (ACK_ACKNOWLEDGED, ACK_CHANGED)
            ):
                skipped += 1
                continue
            prior_states.append({"id": row_id, "ack_state": row.ack_state})
            row.ack_state = ACK_AWAITING
            row.acknowledged_by = None
            row.acknowledged_at = None
            touched_ids.append(row_id)
            updated += 1
        if touched_ids:
            from app.audit_context import get_audit_context
            from app.services.audit_service import log_audit

            # ip_address only - `user_id` stays `actor_user_id`, the route's own
            # resolved caller, not whatever `get_audit_context` names (review round 2).
            _, ip_address = get_audit_context()
            log_audit(
                self.db,
                "project_order_inquiry_rows",
                touched_ids[0],
                "UPDATE",
                # The REAL prior ack_state per row (review round 2) - "acknowledged" and
                # "changed" are different facts to undo, and a flat placeholder erased
                # that distinction.
                old_values={"rows": prior_states},
                new_values={"ack_state": "awaiting", "row_ids": touched_ids},
                user_id=actor_user_id,
                # A NULL `company_id` audit row shows in every company's listing
                # (review round 2) - every touched row is this same company's (the
                # session's own company-scope listener already excludes any other),
                # so the first one's is as good as any.
                company_id=found_by_id[touched_ids[0]].company_id,
                ip_address=ip_address,
                description=f"Unconfirm: {len(touched_ids)} row(s) back to To confirm",
            )
        return {"updated": updated, "skipped": skipped}

    def acknowledge_eligible_rows(
        self,
        row_ids: Sequence[str],
        *,
        actor_user_id: str,
        link_up_to: Optional[date] = None,
        link_horizon: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Like `acknowledge_rows`, but tolerant of an EMPTY scope (AC-CF-8b, S2
        `PLAN-oi-confirm-per-so.md`): `OrderInquiryWorklistService.acknowledge_scope`
        already excludes every rejected, cancelled or already-acknowledged row a `filter`
        matched before this is ever called, so what reaches here is never refused by
        `acknowledge_rows`'s own guards - it can only be EMPTY, when a filter matched
        nothing eligible at all. `acknowledge_rows` itself still refuses an empty list
        (`_rows_or_404`'s "Name at least one row"), which is right for a caller naming
        `row_ids` by hand but wrong for "Select all N matching" finding zero: the caller
        still deserves the horizon this press would have run under, not a 422 for a list
        it built itself out of rows already excluded.
        """
        if not row_ids:
            placed = self.auto_place_for_products(
                None,
                actor_user_id=actor_user_id,
                trigger="acknowledge",
                row_ids=[],
                link_up_to=link_up_to,
                link_horizon=link_horizon,
            )
            return {
                "acknowledged": 0,
                "linked_rows": placed["placed_rows"],
                "links": placed["allocations"],
                "after_horizon": placed["after_horizon"],
                "link_up_to": placed["link_up_to"],
                "link_horizon": placed["link_horizon"],
            }
        return self.acknowledge_rows(
            row_ids,
            actor_user_id=actor_user_id,
            link_up_to=link_up_to,
            link_horizon=link_horizon,
        )

    def reject_row(
        self, row_id: str, *, reason: str, actor_user_id: str
    ) -> Dict[str, Any]:
        """Purchasing refuses one instruction, with a reason CS reads on the board cell.

        The row leaves netting and the LINE goes back to the board undecided, carrying the
        refusal - `ProjectSupplyService.uncover_lines`, which is `confirm`'s own un-decide
        seam. A rejection that only marked the row would leave the line reading as decided
        and promised while nobody was buying anything for it, which is the exact state the
        board exists to make impossible.

        The reason is required at the schema. It is required again here because a service
        caller (a test, a script) reaches this without one.

        A row WHOLLY on documents may be refused too (`PLAN-scm-oi-draft-links.md` 5.6).
        It could not before, and the rule made sense while a link meant purchasing had
        already bought: "purchasing rejected it" beside a purchase order that exists is a
        sentence CS cannot act on. With DRAFTS the same state means the opposite - a row
        raised a minute ago is fully linked and nobody has agreed to anything - so refusing
        it would have refused most of the page. The links come down FIRST instead, which is
        the truth of a refusal: the quantity goes back to the document for the next row.

        A cancelled or actioned row is still past refusing, for the same reason
        acknowledging one is: nobody is doing it. Refused before anything is written, so
        such a row keeps its state AND gains no revision on its line.
        """
        text_reason = self._reject_reason(reason)
        row = self._row_or_404(row_id)
        self._assert_rejectable(row)
        self._reject_one(row, reason=text_reason, actor_user_id=actor_user_id)
        return self.serialize_rows([row])[0]

    def reject_rows(
        self, row_ids: Sequence[str], *, reason: str, actor_user_id: str
    ) -> Dict[str, Any]:
        """Refuse a BATCH with ONE reason (`PLAN-scm-oi-draft-links.md` 5.6, item 15).

        ALL OR NOTHING: every row is checked before the first is written, so a batch
        holding one row nobody may refuse writes nothing at all. A press that half happened
        leaves the buyer to work out which half from a screen that has already moved on,
        and the dialog asked one question about all of them.

        Every row is STAMPED first and the sales orders are un-decided afterwards, ONE call
        per order carrying every line the batch refused (B4, review round 28 Aug). Row by
        row it could not work: un-decking one line writes a fresh revision of the whole
        order, and that revision cancels and re-raises the other lines' rows - so the
        second refusal stamped a row that had just been superseded while its live
        replacement went on sitting in front of purchasing as if nobody had refused it. One
        revision per order also matches what the buyer did: they pressed once.
        """
        text_reason = self._reject_reason(reason)
        rows = self._rows_or_404(row_ids)
        for row in rows:
            self._assert_rejectable(row)
        for row in rows:
            self._stamp_rejected(row, reason=text_reason, actor_user_id=actor_user_id)
        self._uncover_rejected_lines(rows, actor_user_id=actor_user_id)
        return {
            "rejected": len(rows),
            "results": [{"row_id": str(row.id), "ok": True} for row in rows],
        }

    @staticmethod
    def _reject_reason(reason: str) -> str:
        """The reason, required at the schema and required again here: a service caller (a
        test, a script) reaches this without one."""
        text_reason = (reason or "").strip()
        if not text_reason:
            raise AppException(
                status_code=422,
                message="Say why this row is being rejected.",
                code="order_inquiry_reject_reason_required",
            )
        return text_reason

    def _assert_rejectable(self, row: OrderInquiryRow) -> None:
        """Whether this row is one a refusal can be about, said before anything is written."""
        if row.state not in (INQUIRY_RAISED, INQUIRY_PARTLY_LINKED, INQUIRY_PLACED):
            raise AppException(
                status_code=422,
                message=(
                    "This row cannot be rejected: it is called off or answered elsewhere."
                ),
                code="order_inquiry_row_not_rejectable",
            )
        if row.ack_state == ACK_REJECTED:
            raise AppException(
                status_code=422,
                message="This row has already been rejected.",
                code="order_inquiry_already_rejected",
            )

    def _reject_one(
        self, row: OrderInquiryRow, *, reason: str, actor_user_id: str
    ) -> None:
        """Take the row's documents back, stamp the refusal, and uncover its line.

        ONE row, which is the per-row endpoint's whole job. The batch stamps every row
        first and then un-decides each ORDER once (`reject_rows`), because a revision
        written between two refusals moves the rows the second one is about.
        """
        self._stamp_rejected(row, reason=reason, actor_user_id=actor_user_id)
        self._uncover_rejected_line(row, actor_user_id=actor_user_id)

    def _stamp_rejected(
        self, row: OrderInquiryRow, *, reason: str, actor_user_id: str
    ) -> None:
        """The refusal itself: the documents back, then who refused it, when and why.

        The unlink comes FIRST and is not optional: every link on a refused row was a claim
        on somebody's purchase order, and leaving it there would hold quantity for an
        instruction nobody is answering.
        """
        links = self._links_of(row.id)
        if links:
            self._remove_links(row, links)
            self.refresh_link_state([row])
            self.db.flush()
        row.ack_state = ACK_REJECTED
        row.rejected_by = actor_user_id
        row.rejected_at = datetime.utcnow()
        row.rejected_reason = reason
        # AC-LT-18: a rejected row is nobody's to buy any more, and a suggestion left
        # standing on it would still show on the Suggested column purchasing just
        # refused.
        self._drop_suggested_links([row])
        self.db.flush()
        self._refresh_inquiry_states({row.order_inquiry_id})

    def _uncover_rejected_line(self, row: OrderInquiryRow, *, actor_user_id: str) -> None:
        """Send the rejected row's sales-order line back to the board undecided.

        NOT best-effort: every failure below this point takes the rejection down with it,
        because a row marked rejected on a line the board still reads as decided and
        promised is the exact state the board exists to make impossible. What it DOES
        answer quietly is a row that traces to no line at all - an amendment exception, a
        form row the book carries no line for. That row has no decision to uncover, which
        is an ordinary outcome and not a failure. Everything else goes through the ONE
        seam: a fresh revision carrying every line but this one.
        """
        self._uncover_rejected_lines([row], actor_user_id=actor_user_id)

    def _uncover_rejected_lines(
        self, rows: Sequence[OrderInquiryRow], *, actor_user_id: str
    ) -> None:
        """The same un-decide for a BATCH: one call per sales order, every refused line of
        that order named in it.

        Grouped rather than looped (B4): each call writes a revision of the WHOLE order, so
        a second call for a sibling line would be undoing and redoing the first one's work
        - and, worse, would cancel and re-raise the rows the rest of the batch is about.
        """
        by_order: Dict[str, Tuple[Any, List[str]]] = {}
        for row in rows:
            if not row.so_line_id:
                continue
            line = self._line_or_none(str(row.so_line_id))
            if line is None:
                continue
            order = (
                self.db.query(ProjectSalesOrder)
                .filter(ProjectSalesOrder.id == line.project_sales_order_id)
                .first()
            )
            if order is None:
                continue
            _order, line_ids = by_order.setdefault(str(order.id), (order, []))
            if str(line.id) not in line_ids:
                line_ids.append(str(line.id))
        if not by_order:
            return
        from app.services.project_supply_service import ProjectSupplyService

        supply = ProjectSupplyService(self.db)
        for order, line_ids in by_order.values():
            supply.uncover_lines(
                order,
                line_ids,
                actor_user_id=actor_user_id,
                reason="Purchasing rejected the order inquiry row for this line.",
            )

    def link_now(
        self,
        product_ids: Optional[Sequence[str]],
        *,
        actor_user_id: str,
        link_up_to: Optional[date] = None,
        link_horizon: Optional[str] = None,
    ) -> Dict[str, Any]:
        """The cascade over ACKNOWLEDGED rows, now (AC-H13).

        What the buyer presses after uploading a purchase-order or SPO book from their own
        page: the documents that arrived a moment ago meet the instructions already taken
        on. Narrowed to the products the upload touched when the caller knows them, because
        one book must not re-deal every open instruction in the company.

        It is also the page's Auto link all, and that is why it RE-DEALS
        (`PLAN-scm-oi-draft-links.md` R2): a book that has just landed may carry a nearer
        document than the one a draft is sitting on, and the press is the buyer asking for
        the best answer available now. Drafts only - a confirmed row's link is never moved
        - and awaiting rows are in scope, because a draft is exactly what this deals.
        """
        return self.auto_place_for_products(
            list(product_ids) if product_ids else None,
            actor_user_id=actor_user_id,
            trigger="link_now",
            link_up_to=link_up_to,
            link_horizon=link_horizon,
            redeal_drafts=True,
            include_awaiting=True,
        )

    def row_ids_of_decision(self, decision_id: str) -> List[str]:
        """The linkable rows THIS supply decision raised or carried (R6).

        The scope of the raise-time draft pass. By the decision rather than by the products
        those rows name, for the reason the Order Inquiry form's own pass already states: a
        product scope walks every open row in the company that happens to name the same
        item, and one board confirm must not re-deal somebody else's instructions.
        """
        rows = (
            self.db.query(OrderInquiryRow.id)
            .filter(
                OrderInquiryRow.supply_decision_id == decision_id,
                OrderInquiryRow.state.in_(INQUIRY_LINK_STATES),
                OrderInquiryRow.verb.in_(_LINKABLE_VERBS),
            )
            .all()
        )
        return [str(row_id) for (row_id,) in rows]

    def _rows_or_404(self, row_ids: Sequence[str]) -> List[OrderInquiryRow]:
        """The named rows, or a 404 naming how many of them are gone."""
        wanted = [str(row_id) for row_id in row_ids if row_id]
        if not wanted:
            raise AppException(
                status_code=422,
                message="Name at least one row.",
                code="order_inquiry_no_rows",
            )
        rows = (
            self.db.query(OrderInquiryRow)
            .filter(OrderInquiryRow.id.in_(wanted))
            .all()
        )
        found = {str(row.id) for row in rows}
        missing = [row_id for row_id in wanted if row_id not in found]
        if missing:
            raise AppException(
                status_code=404,
                message=f"{len(missing)} of those rows no longer exist.",
                code="order_inquiry_row_not_found",
            )
        return rows

    def _refresh_inquiry_states(self, inquiry_ids: set) -> None:
        """An inquiry is closed when nothing on it is still waiting."""
        for inquiry_id in inquiry_ids:
            inquiry = (
                self.db.query(OrderInquiry).filter(OrderInquiry.id == inquiry_id).first()
            )
            if inquiry is None:
                continue
            states = {
                state
                for (state,) in self.db.query(OrderInquiryRow.state)
                .filter(OrderInquiryRow.order_inquiry_id == inquiry_id)
                .distinct()
                .all()
            }
            # A PARTLY LINKED row is still waiting on somebody, so its inquiry is open -
            # exactly as a raised one is. Reading it as "actioned" would have closed a
            # header whose own rows still carry unlinked demand.
            if not states or states & {INQUIRY_RAISED, INQUIRY_PARTLY_LINKED}:
                inquiry.state = INQUIRY_RAISED
            elif states == {INQUIRY_CANCELLED}:
                inquiry.state = INQUIRY_CANCELLED
            else:
                inquiry.state = INQUIRY_ACTIONED
        self.db.flush()

    # ------------------------------------------------- Link PO / Link SPO (section 3.I)
    #
    # "identify which outstanding PO has quantity to fulfil this order inquiry, tag it,
    # and the quantity to be ordered is deducted" (the captain, 20 Aug), reworked twice
    # since. What it is today, and why each half is the way it is:
    #
    # **A row keeps its full quantity and carries LINKS.** The captain, 25 August, walking
    # SO414285: "1 line here should correspond to 1 line in sales order, so 1 line can be
    # placed by multiple PO and SPO". `order_inquiry_rows` held exactly one `po_line_id`,
    # so a cascade needing two lines SPLIT the row - and nine sales-order lines read as
    # eleven instructions. `projects.order_inquiry_links` (migration 421) is one row per
    # placement; the row's `state` and `po_ref` are DERIVED from them, never written by
    # hand (`refresh_link_state`).
    #
    # **The lever on the reorder engine is netting, not a state test.** `committed_v`'s
    # confirmed leg counts `qty - sum(links.qty)` (migration 422), so a fully linked row
    # leaves confirmed demand exactly as `placed` did and a half-linked one leaves half.
    # Linking never makes the document SUPPLY - `on_order_v` still reads `spo_allocations`
    # alone - it retires the DEMAND that document is already covering.
    #
    # **Both PO lines and SPO allocations are candidates for every linkable row** (R5,
    # 27 August, widening the 25 August rule; `PLAN-scm-oi-worklist-excel-parity.md` S5).
    # An ORDER, a RESERVE & ORDER and an ORDER BACK row may all name either book -
    # `_SPO_LINKABLE_VERBS` equals `_LINKABLE_VERBS` below, not a narrower set.
    #
    # **Location ranks a candidate, it never filters one out** (Q5, ruled 25 August). Same
    # location, then the same ownership group at another site, then the site pools, then a
    # sibling location at the site. A link outside tier 1 is not a mistake - it is the
    # split instruction the buyer keys into AutoCount, and the PO occupancy panel marks it.
    #
    # **Then the PO's own date, then the line's** (Q7, ruled): `purchase_orders.issue_date`
    # ascending, then `expected_date`, then the document number. Before Q7 the key was the
    # line's expected date alone, which dealt a January line of an August purchase order
    # ahead of an August line of an April one.
    #
    # **A cited document comes before all of it.** CS naming "202604-S0083" on the form is
    # the most specific thing anybody knows about the row, and a walk that ignored it would
    # be answering a question nobody asked.

    def refresh_link_state(self, rows: Sequence[OrderInquiryRow]) -> None:
        """Set each row's state and its derived display from its own links.

        The ONE writer of `state` / `po_ref` / `po_line_id` / `spo_ref` on a linkable row,
        so the four cannot come to disagree. `actioned` and `cancelled` are a person's word
        about the row and are left exactly as they are: a link change is not an opinion
        about whether purchasing dealt with it.

        The stored value for "wholly covered" stays `placed` and reads "Linked" on screen.
        Renaming the column value would have rewritten `scm.committed_v`, the worklist's
        own filter and every saved column preference to say the same thing in a different
        word, which buys nothing and breaks a bookmark.

        PLAN-scm-supplied-with-companions.md S5 (call site 2, section 3.2): re-derives
        every touched inquiry's bundles FIRST, so a host row's own link change (gaining
        one, being cancelled, its qty dropping) is reflected in its companions' state
        below in the SAME pass - `derive_bundles` is the one writer of `bundled_qty`,
        never this loop.
        """
        for inquiry_id in {row.order_inquiry_id for row in rows if row.order_inquiry_id}:
            self.derive_bundles(inquiry_id)
        # AC-LT-17/18: a suggested link means nothing once the row is no longer open
        # for buying - a person's word about it (actioned, cancelled, rejected,
        # redirected to pool) or full REAL coverage (`placed`, below). Collected and
        # dropped in ONE call after the loop, so a pass over many rows costs one
        # DELETE rather than one per row.
        to_drop: List[OrderInquiryRow] = []
        for row in rows:
            if row.state in (INQUIRY_ACTIONED, INQUIRY_CANCELLED):
                # The STATE is a person's word and is left alone, but the derived display
                # is not a word - it is a reading of the links, and a row whose links have
                # gone must stop naming a document it no longer sits on.
                if not self._links_of(row.id):
                    row.po_ref = None
                    row.po_line_id = None
                    row.spo_ref = None
                to_drop.append(row)
                continue
            links = self._links_of(row.id)
            linked = sum((_dec(link.qty) for link in links), _ZERO)
            row.state = self._coverage_state(_dec(row.qty), linked, _dec(row.bundled_qty))
            # The FIRST REAL document's own link, by when it was made - a reserve link
            # (`document="Reserved @ BRW"`, `reserve_request_row_id` set) is not a PO or
            # an SPO (PLAN-oi-request-cs-reserve.md, review round SF-1): skipped here so
            # a row reserved before it is ever placed on a book does not read a fake
            # `po_ref`. `po_number` (the worklist's own reader) already gets this right
            # (`_PO_LINKED_QTY`/`links_for_rows` both filter on the real target column);
            # this is the SAME rule applied to the row's own stored display.
            first = next(
                (link for link in links if link.reserve_request_row_id is None), None
            )
            # `po_ref` has carried a PO number since section G and several readers still
            # print it; it is a display of the links now, so it is restated here rather
            # than left holding whatever the last single-line placement happened to set.
            row.po_ref = first.document if first is not None else None
            row.po_line_id = first.po_line_id if first is not None else None
            row.spo_ref = (
                first.document
                if first is not None and first.spo_allocation_id is not None
                else None
            )
            if row.state == INQUIRY_PLACED or row.ack_state == ACK_REJECTED or row.redirected_to_pool:
                to_drop.append(row)
        if to_drop:
            self._drop_suggested_links(to_drop)

    @staticmethod
    def _coverage_state(qty: Decimal, linked: Decimal, bundled: Decimal) -> str:
        """`linked + bundled_qty >= qty` reads `placed`; between, `partly_linked`; none
        of it, `raised` (plan 3.4 "State"). The one formula `refresh_link_state` and
        `derive_bundles` both read, so the two writers of a row's coverage can never
        come to disagree about what it means."""
        covered = linked + bundled
        if covered <= _ZERO:
            return INQUIRY_RAISED
        if covered < qty:
            return INQUIRY_PARTLY_LINKED
        return INQUIRY_PLACED

    # --------------------------------------------------- supplied-with companions (S5)

    def derive_bundles(self, inquiry_id: str) -> None:
        """PLAN-scm-supplied-with-companions.md sections 3.2-3.3.

        Recomputes `bundled_qty` / `bundled_with_row_id` (and, since coverage now reads
        both, `state`) for every COMPANION row of this inquiry - never a host row's own
        fields, and never a new row. Idempotent (B14): re-running it is a no-op when
        nothing on the inquiry has changed. RESETS, never freezes: a row with no
        applicable rule this pass (the rule was deleted or deactivated since the last
        derivation, or a pair rule's host went missing) is written back to bundled_qty
        0 / bundled_with_row_id NULL / state-from-links-alone, not left holding an
        earlier pass's answer. The one exception is a row this function does not touch
        at all: ACTIONED or CANCELLED, "a person's word about the row" exactly as
        `refresh_link_state` reads them - an actioned companion keeps whatever it was
        actioned with, bundle included.

        Per companion row R (ORDER / ORDER_BACK, not actioned or cancelled):
          rules  = active rules for R's product, company-scoped
          for each rule (first match wins - the UNIQUE constraint means at most one
                         should ever apply to a given order's supplier anyway):
              host rows H_k = this inquiry's rows of each host product, verb in
                              (ORDER, ORDER_BACK), state not cancelled, ack not rejected
              every H_k must have at least one row, or the rule does not apply
              the rule's OWN supplier scope must match (3.3), or it does not apply
              host_cap = min over k of (sum(H_k.qty) * ratio)
              bundled  = min(R.qty - linked(R), host_cap), never below 0
          R.bundled_qty = bundled (0 if no rule applied)
          R.bundled_with_row_id = the first host row of the first host key (3.2's
                                   "display anchor"), or None
        """
        # FLUSH first: `populate_existing()` below reads columns straight off the
        # database, and without this, a caller's own PENDING change on one of these
        # rows made earlier in the SAME session (a settle, a cancel, a qty rewrite -
        # exactly what `refresh_link_state`'s own callers do before handing it rows to
        # refresh) would still be unflushed, so `populate_existing()` would read the
        # OLD row back over it and silently discard the caller's write. This was a real
        # regression (CI round 2): a board merge that raised a survivor's qty to 25 and
        # cancelled its siblings read back qty 10 and "not cancelled" once this ran.
        #
        # `populate_existing()` itself still has to stay: B10 cancels a HOST row via
        # raw SQL on this same session (`world.cancel`, the fixture's own stand-in for
        # "the owner relinks by hand" in production) and derive_bundles has to see that
        # NOW, not the stale `state` this session's identity map already cached the
        # host object as - the one real, tested case its own docstring was written for.
        # It DOES widen a re-read row's printed decimal precision to the column's
        # stored NUMERIC(15,4) scale ("25" back as "25.0000") - real, but the caller's
        # own job to format, not this function's: `_dispatch_changed_with_links` fixes
        # its own read of `row.qty` with `_qty_str()` for exactly that reason.
        self.db.flush()
        rows = (
            self.db.query(OrderInquiryRow)
            .filter(OrderInquiryRow.order_inquiry_id == inquiry_id)
            .populate_existing()
            .all()
        )
        if not rows:
            return
        inquiry = self.db.query(OrderInquiry).filter(OrderInquiry.id == inquiry_id).first()
        if inquiry is None:
            return
        company_id = inquiry.company_id

        codes = {row.item_code for row in rows if row.item_code}
        if not codes:
            return
        product_by_code: Dict[str, str] = {
            code: pid
            for pid, code in self.db.query(Product.id, Product.product_code)
            .filter(Product.company_id == company_id, Product.product_code.in_(codes))
            .all()
        }
        if not product_by_code:
            return

        def _is_host_row(row: OrderInquiryRow) -> bool:
            # SO314592 (prod, 21 Sep 2026): a USED row (`redirected_to_pool`) is grey,
            # already spent, and must never anchor a companion - the same exclusion
            # `order_inquiry_worklist_service.py`'s own `_live_host_row` already applies.
            return (
                row.verb in (IV_ORDER, IV_ORDER_BACK)
                and row.state != INQUIRY_CANCELLED
                and row.ack_state != ACK_REJECTED
                and not row.redirected_to_pool
            )

        host_rows_by_product: Dict[str, List[OrderInquiryRow]] = {}
        for row in rows:
            if not _is_host_row(row):
                continue
            product_id = product_by_code.get(row.item_code)
            if product_id:
                host_rows_by_product.setdefault(product_id, []).append(row)

        product_ids = set(product_by_code.values())
        active_rules = (
            self.db.query(ProductCompanionRule)
            .filter(
                ProductCompanionRule.company_id == company_id,
                ProductCompanionRule.companion_product_id.in_(product_ids),
                ProductCompanionRule.is_active.is_(True),
            )
            .all()
        )
        rules_by_companion: Dict[str, List[ProductCompanionRule]] = {}
        for rule in active_rules:
            rules_by_companion.setdefault(rule.companion_product_id, []).append(rule)
        # NOT an early return when this comes up empty (a deleted or deactivated rule
        # leaves it exactly this way): every row the loop below still walks gets reset,
        # which is the whole point - a companion whose rule is gone must not keep a
        # frozen bundle (B3).

        hosts_by_rule: Dict[str, List[str]] = {}
        rule_ids = [rule.id for rule in active_rules]
        if rule_ids:
            for host_link in (
                self.db.query(ProductCompanionRuleHost)
                .filter(ProductCompanionRuleHost.rule_id.in_(rule_ids))
                .order_by(ProductCompanionRuleHost.seq.asc())
                .all()
            ):
                hosts_by_rule.setdefault(host_link.rule_id, []).append(
                    host_link.host_product_id
                )

        linked_by_row = self._linked_qty_by_row([row.id for row in rows])

        for row in rows:
            # `_refresh_link_state` never touches an ACTIONED or CANCELLED row's state -
            # "a person's word about the row" - and this has to agree: an actioned
            # companion keeps whatever it was actioned with, bundle included (B4).
            if row.state in (INQUIRY_ACTIONED, INQUIRY_CANCELLED):
                continue
            if row.verb not in (IV_ORDER, IV_ORDER_BACK):
                continue

            product_id = product_by_code.get(row.item_code)
            candidate_rules = rules_by_companion.get(product_id) if product_id else None

            # Never a companion candidate, and nothing on it to reset: leave the row
            # untouched ENTIRELY, `state` included (review round 2, CI: this used to
            # recompute `state` from links alone for every row of the whole inquiry,
            # not only the ones this function is actually about - a "placed with no
            # link row" special case, or a row `refresh_link_state`'s own caller had
            # just settled/cancelled a moment ago and not yet re-read, would get
            # demoted back to `raised` here even though nothing about its bundle
            # ever changed). A row that HAS a candidate rule, or still carries a
            # bundle from an earlier pass (the reset case, B3/B16/B17), still runs
            # the full derivation below.
            if (
                not candidate_rules
                and _dec(row.bundled_qty) <= _ZERO
                and not row.bundled_with_row_id
            ):
                continue

            linked = linked_by_row.get(row.id, _ZERO)

            bundled = _ZERO
            anchor_row_id: Optional[str] = None
            # First rule that actually APPLIES wins (plan 3.2's anchor is "the first
            # host row" of the rule that matched, never the larger of several
            # candidates) - the UNIQUE constraint means more than one active rule for
            # the same companion only happens across different supplier scopes, and at
            # most one supplier scope can match a given order.
            for rule in candidate_rules or []:
                host_ids = hosts_by_rule.get(rule.id) or []
                if not host_ids:
                    continue
                host_rows_per_host = [host_rows_by_product.get(hid) or [] for hid in host_ids]
                if any(not host_rows for host_rows in host_rows_per_host):
                    # Not every host on this rule has a row on THIS order (B7) - the
                    # rule does not apply.
                    continue
                if not self._companion_rule_supplier_matches(rule, host_rows_per_host):
                    continue
                host_cap: Optional[Decimal] = None
                for host_rows in host_rows_per_host:
                    host_total = sum((_dec(h.qty) for h in host_rows), _ZERO)
                    cap = host_total * _dec(rule.ratio)
                    host_cap = cap if host_cap is None else min(host_cap, cap)
                remaining = max(_dec(row.qty) - linked, _ZERO)
                bundled = min(remaining, host_cap if host_cap is not None else _ZERO)
                anchor_row_id = host_rows_per_host[0][0].id
                break

            # Written every time, win or lose: a row with no applicable rule (B3 - the
            # rule was deleted or deactivated since the last derivation, or B7 - a pair
            # rule with one host missing) resets to ala carte rather than keeping
            # whatever an earlier pass left on it.
            row.bundled_qty = bundled
            row.bundled_with_row_id = anchor_row_id if bundled > _ZERO else None
            row.state = self._coverage_state(_dec(row.qty), linked, bundled)

        self.db.flush()

    def _companion_rule_supplier_matches(
        self, rule: ProductCompanionRule, host_rows_per_host: Sequence[Sequence[OrderInquiryRow]]
    ) -> bool:
        """PLAN section 3.3. NULL `supplier_id` is the "just in case" scope and always
        matches. Otherwise EVERY host on the rule must resolve to that supplier - a
        linked host row reads the supplier of its own link; an unlinked one reads its
        product's primary supplier."""
        if not rule.supplier_id:
            return True
        for host_rows in host_rows_per_host:
            if not any(
                self._host_row_supplier_id(row) == rule.supplier_id for row in host_rows
            ):
                return False
        return True

    def _host_row_supplier_id(self, row: OrderInquiryRow) -> Optional[str]:
        """3.3: a linked host row's supplier is the one its (first PO) link names; an
        unlinked one's is its product's own primary supplier."""
        links = self._links_of(row.id)
        for link in links:
            if link.po_line_id:
                po_line = (
                    self.db.query(PurchaseOrderLine)
                    .filter(PurchaseOrderLine.id == link.po_line_id)
                    .first()
                )
                if po_line is not None:
                    po = (
                        self.db.query(PurchaseOrder)
                        .filter(PurchaseOrder.id == po_line.purchase_order_id)
                        .first()
                    )
                    if po is not None and po.supplier_id:
                        return po.supplier_id
        if links:
            # Every link resolved to no supplier (an SPO with no PO behind it) - there
            # is nothing to match against.
            return None
        product_id = (
            self.db.query(Product.id)
            .filter(Product.company_id == row.company_id, Product.product_code == row.item_code)
            .scalar()
        )
        if not product_id:
            return None
        return (
            self.db.query(ProductSupplier.supplier_id)
            .filter(
                ProductSupplier.product_id == product_id,
                ProductSupplier.is_primary_supplier.is_(True),
            )
            .scalar()
        )

    def _links_of(self, row_id: str) -> List[OrderInquiryLink]:
        """This row's links, oldest first - the order "the first link" means."""
        return (
            self.db.query(OrderInquiryLink)
            .filter(OrderInquiryLink.row_id == row_id)
            .order_by(OrderInquiryLink.linked_at.asc(), OrderInquiryLink.id.asc())
            .all()
        )

    def _links_by_row(
        self, row_ids: Sequence[str]
    ) -> Dict[str, List[OrderInquiryLink]]:
        """Every named row's links, grouped - ONE query for a whole set of rows.

        `_only_cascade_links` used to be a row-at-a-time convenience every caller walking
        more than a handful of rows reached for in a loop, which is an N+1 the moment that
        set grows past a few (review of PR #471, S6): a plan-wide re-deal or a per-line
        settle check both walk this per row. Callers holding a SET of candidate rows
        should load through here once and read `_cascade_only` off the preloaded list;
        `_only_cascade_links` stays for a genuine single-row caller.
        """
        wanted = [row_id for row_id in row_ids if row_id]
        if not wanted:
            return {}
        grouped: Dict[str, List[OrderInquiryLink]] = {}
        for link in (
            self.db.query(OrderInquiryLink)
            .filter(OrderInquiryLink.row_id.in_(wanted))
            .order_by(OrderInquiryLink.linked_at.asc(), OrderInquiryLink.id.asc())
            .all()
        ):
            grouped.setdefault(str(link.row_id), []).append(link)
        return grouped

    @staticmethod
    def _cascade_only(links: Sequence[OrderInquiryLink]) -> bool:
        """Whether a row's links are ALL the cascade's own guess (`auto=True`) - AND
        there is at least one.

        `ack_state` used to be this proxy (B2/B3, `PLAN-scm-oi-draft-links.md`): a row
        nobody had acknowledged held only links the system found on its own, so settling
        it in place or retiring it with a dropped line cost nobody a decision they had
        made. That reading broke the moment linking stopped waiting for confirm at all
        (raise, Link now and the worklist's own auto-place cascade an awaiting row too,
        `include_awaiting=True`) - a row still AWAITING today (`PLAN-oi-confirm-per-so.md`
        S1 born it that way again) can hold a MANUAL link just as easily as a confirmed
        one can hold only the cascade's own guess, so `ack_state` was never safe to read
        either way. `OrderInquiryLink.auto` is the TRUE fact underneath it: written by
        the cascade rather than by a person clicking (its own column comment).

        A LINKLESS row is NOT cascade-only (review of PR #471, S1): `all()` over an empty
        list is vacuously True, which read a row purchasing placed through a path that
        writes no link row (the SO349754/WESERP10B shape - `mark_rows`' own `actioned`,
        or any other placement this table never learned of) as a "draft" free for the
        walk to settle, retire or redeal. A row with nothing to show here is exactly the
        row `_settle_row_in_place`'s own PLACED/ACTIONED-with-no-link guard already
        declines for the same reason; this reading has to agree with it.
        """
        return bool(links) and all(link.auto for link in links)

    def _only_cascade_links(self, row_id: str) -> bool:
        """Single-row convenience over `_cascade_only` - a caller walking more than one
        row should batch through `_links_by_row` instead (S6)."""
        return self._cascade_only(self._links_of(row_id))

    def _linked_qty_by_row(self, row_ids: Sequence[str]) -> Dict[str, Decimal]:
        """How much of each row already sits on a document. One query for a whole page."""
        wanted = [row_id for row_id in row_ids if row_id]
        if not wanted:
            return {}
        rows = (
            self.db.query(OrderInquiryLink.row_id, func.sum(OrderInquiryLink.qty))
            .filter(OrderInquiryLink.row_id.in_(wanted))
            .group_by(OrderInquiryLink.row_id)
            .all()
        )
        return {row_id: _dec(qty) for row_id, qty in rows}

    def _linked_by_target(self) -> Tuple[Dict[str, Decimal], Dict[str, Decimal]]:
        """What every link already claims, per PO line and per SPO allocation.

        The netting that stops two rows claiming the same purchase-order quantity. Read off
        the LINKS rather than off the rows' own `po_line_id`, which is derived display now:
        a row linked to two lines claims quantity on both, and the scalar names one.
        """
        if self._linked_by_target_cache is not None:
            return self._linked_by_target_cache
        by_po = {
            str(po_line_id): _dec(qty)
            for po_line_id, qty in self.db.query(
                OrderInquiryLink.po_line_id, func.sum(OrderInquiryLink.qty)
            )
            .filter(OrderInquiryLink.po_line_id.isnot(None))
            .group_by(OrderInquiryLink.po_line_id)
            .all()
        }
        by_spo = {
            str(allocation_id): _dec(qty)
            for allocation_id, qty in self.db.query(
                OrderInquiryLink.spo_allocation_id, func.sum(OrderInquiryLink.qty)
            )
            .filter(OrderInquiryLink.spo_allocation_id.isnot(None))
            .group_by(OrderInquiryLink.spo_allocation_id)
            .all()
        }
        self._linked_by_target_cache = (by_po, by_spo)
        return by_po, by_spo

    def _invalidate_link_cache(self) -> None:
        """Every writer of a link calls this. A stale total is a double-claim.

        BOTH memos go (item 4, re-review 27 Aug 2026). `_awaiting_link_cache` answers
        "which groups hold an acknowledged, still-unlinked row of this product", which a
        link written since is exactly what changes - and the route that writes one
        serialises its answer through this same instance, so the listing read the rows as
        they were before the write and offered a Link for a row it had just fully covered.
        The memo still earns its place inside one cascade pass: a placement is decided
        before its own links are written, so the walk asks between writes rather than
        across them.
        """
        self._linked_by_target_cache = None
        self._claims_cache = {}
        self._awaiting_link_cache = {}
        # A write that resolves or creates a claim (`_write_link`, every placement) must
        # not leave the NEXT row in the same pass reading the dedication state as it was
        # before this one wrote - which is why `_claims_cache` is dropped above.
        # `_so_number_cache` stays: a row's own identity does not change inside a pass.

    def _row_so_number(self, row: OrderInquiryRow) -> Optional[str]:
        """This row's own SO number, the identity G7 dedication ranks a candidate's own
        claim against - the same identity `claim_identity` writes a claim under, so a
        row can never be told its OWN document is "dedicated to" a different one.

        Memoised per row (`_so_number_cache`): the cascade calls `_candidates_for_row`
        once per row and `claim_identity` costs two queries to derive.
        """
        key = str(row.id)
        if key not in self._so_number_cache:
            so_number, _item_code, _core_line_id = self.claim_identity(row)
            self._so_number_cache[key] = so_number
        return self._so_number_cache[key]

    def _prime_claims(self, target_ids: Sequence[str]) -> None:
        """Read the dedication evidence for these candidate lines, once.

        Scoped to the targets actually under consideration (S5, review of PR #490). This
        used to read every RESOLVED claim in the database on the first candidate walk of
        every service instance - 33,231 rows on the live book - and again after every
        `_write_link`, because a placement invalidates the cache. A cascade over a product
        with fifty candidates paid for the whole table each time somebody was linked.

        Cached per TARGET rather than as one whole-table answer, so a second row asking
        about the same purchase order pays nothing and a row asking about a new one pays
        only for its own lines.
        """
        missing = [str(t) for t in target_ids if str(t) not in self._claims_cache]
        if not missing:
            return
        found = order_link_service.reservations_by_target(self.db, target_ids=missing)
        for target_id in missing:
            self._claims_cache[target_id] = found.get(target_id, [])

    def _claims_of(self, target_id: str) -> Sequence[Dict[str, Any]]:
        """This line's claims, reading through the primed cache and filling it if the
        caller never primed (`po_candidates_for_row`'s single-line reads, tests)."""
        if target_id not in self._claims_cache:
            self._prime_claims([target_id])
        return self._claims_cache.get(target_id, ())

    def _dedication_for_target(
        self,
        target_id: str,
        own_so_number: Optional[str],
        *,
        ignore_claim_ids: frozenset = frozenset(),
    ) -> Tuple[Decimal, Optional[str], bool]:
        """`(reserved, dedicated_to, claimed_by_own_so)` for one candidate line (G7 + G12).

        `reserved` is the SUM of every OTHER SO's claimed outstanding on this line -
        mathematically the same figure a strict SO-date-ordered cascade against the
        line's own capacity would land on, since both are clamped to zero the same way
        once subtracted (PO 100, SO A 30 + SO B 50 claimed both read 80 reserved,
        whichever order they are summed in - the ORDER only decides which SO's number is
        shown when more than one holds a claim, which `dedicated_to` answers by picking
        the earliest SO date).

        `dedicated_to` is None when nobody but this row's own SO claims the line - the
        ordinary case - so a candidate list where nothing is dedicated adds no queries a
        caller has to filter back out. A claim whose SO line has SETTLED reserves zero
        (G7) and is excluded here entirely, not just from the sum: a fulfilled or
        cancelled order is not who the line is "dedicated to" any more, whatever the
        claim itself still names - the claim row stays, the dedication does not.

        `ignore_claim_ids` is `_candidates_for_row`'s `credit_own_links` reaching in here
        too (bug found chasing PR #490's CI failures, `test_order_inquiry_draft_links.py`
        R2/S4 redeal): crediting the row's own DRAFT link's quantity back to `remaining`
        but leaving the claim `_write_link` wrote for that SAME placement standing made
        `own_so_claim` true for the document the row ALREADY holds and nothing else -
        which G7's own sort ranks first (AC-6.4, deliberately, for a REAL dedication) -
        so a redeal could never move a draft to a genuinely nearer document once G7
        started writing claims for the cascade's own ordinary, revisable guesses and not
        only for a person's deliberate ones. The row's own draft claims named here are
        invisible for this one evaluation - neither own nor another's - so the walk
        compares the held document and the alternatives on the same terms `credit_own_
        links` already promises for their quantity.
        """
        claims = self._claims_of(target_id)
        own_claim = False
        others: List[Dict[str, Any]] = []
        for claim in claims:
            if claim["claim_id"] in ignore_claim_ids:
                continue
            if own_so_number is not None and claim["so_number"] == own_so_number:
                # A SETTLED claim does not unlock the line either (nit, review of PR #490).
                # G12 opens a project bin to the sales order that claims it, and an order
                # that has been delivered or cancelled is not claiming anything any more -
                # the same test the `others` branch below applies to a stranger's claim.
                own_claim = own_claim or claim["outstanding"] > _ZERO
                continue
            if claim["outstanding"] > _ZERO:
                others.append(claim)
        if not others:
            return _ZERO, None, own_claim
        others.sort(
            key=lambda c: (
                c["so_date"] is None,
                c["so_date"] or date.max,
                c["claim_id"],
            )
        )
        # The WALKED reservation (B2), not the claiming line's raw outstanding: a need
        # already reserved on an earlier document is not reserved again here.
        reserved = sum((c["reserved"] for c in others), _ZERO)
        return reserved, others[0]["so_number"], own_claim

    def _reserved_for_netting(
        self, target_id: str, own_so_number: Optional[str]
    ) -> Decimal:
        """What comes off a candidate's `remaining` on top of the LINKS already netted out.

        `order_link_service.reservations_by_target` has already done the arithmetic (B2):
        each claim's `reserved` is its share of the claiming SALES ORDER LINE's still
        unplaced need, capped at the document's own capacity and handed out once across
        every document that line claims. So what is left to subtract here is simply the
        sum of the OTHER sales orders' shares - the part of the line somebody else is
        owed and has not taken yet.

        The row's own claims are skipped: a line this row's order claims reserves nothing
        against that same order (AC-6.4), and its own placements are already in
        `by_po` / `by_spo`.
        """
        total = _ZERO
        for claim in self._claims_of(target_id):
            if own_so_number is not None and claim["so_number"] == own_so_number:
                continue
            if claim["reserved"] > _ZERO:
                total += claim["reserved"]
        return total

    def _pool_codes(self) -> set:
        """Every warehouse that is SOME location's pool, by code.

        The authoritative test is the FK, not the code's shape - the same reading
        `project_supply_service._site_pool_warehouses` uses, and for the same reason: on the
        live book every pool happens to be a plain site code with no hyphen, but that is a
        naming convention the data does not enforce.
        """
        if self._pool_codes_cache is None:
            pool_ids = {
                str(row[0])
                for row in self.db.query(Warehouse.pool_warehouse_id)
                .filter(Warehouse.pool_warehouse_id.isnot(None))
                .distinct()
                .all()
            }
            self._pool_codes_cache = {
                str(code).upper()
                for (code,) in self.db.query(Warehouse.warehouse_code).filter(
                    Warehouse.id.in_(list(pool_ids)), Warehouse.is_active.is_(True)
                )
                if code
            } if pool_ids else set()
        return self._pool_codes_cache

    def _cited_documents(self, row: OrderInquiryRow) -> Dict[str, int]:
        """Every document this row NAMES, upper-cased, RANKED in the order CS wrote them.

        `cited_document` is the column CS writes into (the Amend "Order back" field, and
        the Order Inquiry Form's remark) and it holds the FIRST document. A form remark
        routinely names more than one - `SPO-2026/08-0061 & 202606-S0082` - and the second
        is not decoration: it is the answer when the first cannot cover the quantity. One
        column cannot hold two, so the rest are written onto the NOTE behind a fixed prefix
        (`project_order_inquiry_import_service.ALSO_CITED_PREFIX`) and read back here.
        Parsed with the reader's own document-number pattern rather than a second one, so
        the two cannot come to disagree about what a document number looks like.

        A RANK rather than a set, because "cited" is not one bucket: the walk must try the
        first document CS named before the second, or a row whose first citation is short
        lands on the wrong one and reads as a rule that ignored the form.

        `spo_ref` is read after them for the rows raised before `cited_document` existed.
        `po_ref` is NOT read: since section 3.I it is the derived display of the first link,
        so reading it would make every already-linked row cite the document it is already on
        and pin the walk to its own past.
        """
        ordered: List[str] = []

        def _add(value: Any) -> None:
            key = str(value or "").strip().upper()
            if key and key not in ordered:
                ordered.append(key)

        _add(row.cited_document)
        for document in _also_cited(row.note):
            _add(document)
        _add(row.spo_ref)
        return {document: rank for rank, document in enumerate(ordered)}

    def _candidates_for_row(
        self, row: OrderInquiryRow, *, manual: bool = False, credit_own_links: bool = False
    ) -> List[Dict[str, Any]]:
        """Every open document line this row could be linked to, in the walk's own order.

        `credit_own_links` asks the question a RE-DEAL has to ask: what could this row
        reach if it gave back what it is already holding? Its own links are added back to
        every line's `remaining`, so the walk compares the document it sits on against the
        alternatives on equal terms - without which a row fully covering itself would find
        its own line "fully claimed" and move to a worse one. Nothing is unlinked to ask
        it (B1, review round 28 Aug): the answer is computed FIRST and the links come down
        only once there is a better one to write, so a row whose candidate has gone, or
        that the horizon holds back, keeps what it has.

        `manual` widens it to a purchase order that is not yet ACTIVE. The automatic walk
        refuses one deliberately - a `draft_recommendation` order is not an outstanding
        order (it is outside `scm.on_order_v` for exactly that reason), and its lines are
        the ones a re-decision may delete out from under a link. A PERSON naming a line by
        hand has always been allowed to, because the dialog is override and audit rather
        than the workflow, and taking that away would have been a narrowing nobody asked
        for.

        ONE query pair feeding both the dialog's candidate list and the auto-link cascade,
        so the preview and the pass can never disagree. `remaining` is already net of every
        OTHER link on the same line, which is why two rows can never be pointed at the same
        quantity.

        SPO allocations are read for every linkable row (R5, 27 August - `spo_allowed`
        below reads `_SPO_LINKABLE_VERBS`, which equals `_LINKABLE_VERBS`, not an
        ORDER-BACK-only set). Their open test is the one
        copy in `app.services.scm.spo_supply` (`open_incoming_clauses`): open line status, a
        receipt status that is not received, no landed shipment - and, per the captain's
        26 August ruling, a promised date in the PAST does not remove a row. The book is
        the record of what is still owed; a supplier being late is not evidence the goods
        stopped existing.

        LADDER V4 (section 1d): a purchase-order line sitting at a `*-<group>` location is
        free only to the extent the GROUP can cover its own backlog with it -
        `group_net + the group's own open PO balance > 0`. In deficit those lines are
        already spoken for, and linking a raised row to one of them says a quantity is
        covered when the group is 15,514 short of covering what it already owes. A
        pool-location line has no group and is always offered; the row that finds nothing
        stays raised and buys, which is the honest outcome.

        DEDICATION (G7, `PLAN-scm-reorder-oi-feedback-1sep.md` S6): `scm.order_link_claim`
        - explicit claims only, never a location or product match - subtracts every OTHER
        SO's live outstanding from a line's `remaining`, in the same order regardless of
        which claim is walked first (a straight sum, clamped at zero the same way a
        SO-date-ordered cascade against the line's own capacity would be). This row's OWN
        claim reserves nothing against itself, and a line it claims ranks first among the
        candidates (`_candidate`'s sort key). Never removed from the list on account of
        dedication alone - a fully dedicated line still shows, greyed, so the buyer sees
        why and can still name it by hand (AC-6.5); the OLD `remaining <= 0` exclusion
        below fires only for a line already spoken for by an actual link, unchanged.

        PROJECT-BIN LOCK (G12, same slice): a line at a `segment = 'project'` warehouse is
        `cascadable` ONLY when this row's own claim names it - claimed by another SO or
        claimed by nobody are refused alike. Never a `remaining` cut: an unclaimed bin's
        whole quantity is still there for a manual link to take, which is what converts it
        to claimed (AC-6.9). A pool-destination line carries no such lock (AC-6.10).

        The claim the lock waits for comes from ONE of three places, and never from this
        walk (captain, 2 Sep 2026, on real data - see `_candidate`'s `cascadable`):

          * the book's own `FromSODocList` column, on either purchase channel
            (`po_history` / `po_upload` claims);
          * the SUPPLY WRITER that created the line, at the moment it created it
            (`app/services/scm/supply_claim.py`, `crm_supply` claims) - a purchase order
            this codebase raised off the plan is a buy for the order-inquiry rows that
            sized it, and it says so in the same transaction that opens the line;
          * a person naming the line by hand in the Link dialog (`manual`, AC-6.9).

        A line none of the three attributed is UNATTRIBUTED and stays manual-link only.
        A blank `FromSODocList` beside a Loading Date remark such as "REPLACE BACK" means
        the line belongs to somebody else's order (captain, 2 Sep), which is one more
        reason the automatic pass may not help itself to one.
        """
        product_id = self._resolve_product_id(row)
        if not product_id:
            return []
        own_so_number = self._row_so_number(row)
        by_po, by_spo = self._linked_by_target()
        # `credit_own_links` reaching `_dedication_for_target` too (bug found chasing
        # PR #490's CI failures, `test_order_inquiry_draft_links.py` R2/S4 redeal): the
        # row's OWN draft claims are ignored the SAME way its own linked quantity
        # already is, below, so a redeal compares the held document and a genuinely
        # nearer one on equal terms rather than always keeping the one G7's own claim
        # sort ranks first.
        ignore_claim_ids: frozenset = frozenset()
        if credit_own_links:
            by_po, by_spo = dict(by_po), dict(by_spo)
            own_claim_ids = set()
            for link in self._links_of(str(row.id)):
                key = str(link.po_line_id) if link.po_line_id else None
                if key and key in by_po:
                    by_po[key] = by_po[key] - _dec(link.qty)
                key = str(link.spo_allocation_id) if link.spo_allocation_id else None
                if key and key in by_spo:
                    by_spo[key] = by_spo[key] - _dec(link.qty)
                if link.claim_id:
                    own_claim_ids.add(str(link.claim_id))
            ignore_claim_ids = frozenset(own_claim_ids)
        pools = self._pool_codes()
        cited = self._cited_documents(row)
        own_location = (row.stock_location or "").strip().upper() or None
        spo_allowed = row.verb in _SPO_LINKABLE_VERBS

        candidates: List[Dict[str, Any]] = []

        po_rows = (
            self.db.query(PurchaseOrderLine, PurchaseOrder, Supplier, Warehouse)
            .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderLine.purchase_order_id)
            .outerjoin(Supplier, Supplier.id == PurchaseOrder.supplier_id)
            .outerjoin(Warehouse, Warehouse.id == PurchaseOrderLine.warehouse_id)
            .filter(
                PurchaseOrderLine.product_id == product_id,
                PurchaseOrderLine.line_status == "open",
                PurchaseOrder.po_number.notlike("SPO-%"),
                *(
                    ()
                    if manual
                    else (PurchaseOrder.status.in_(("active", "partial")),)
                ),
            )
            .all()
        )
        # LADDER V4's group-deficit rule applies to what is OFFERED - the dialog's list and
        # the cascade's walk. A PERSON naming a line by hand is let through, exactly as
        # `manual` already lets them through to a purchase order that is not yet active:
        # this dialog is override and audit rather than the workflow, and refusing a link
        # somebody has deliberately typed would be a narrowing nobody asked for.
        #
        # THIS row's own exemption comes off it, and only this row's (B1, code review
        # 27 Aug 2026): a group holding an acknowledged, unlinked row may reach its own
        # purchase order, and the row that earns that is the one being placed. Lifted per
        # product instead, a row at any other group walked first took the line.
        # S5: one scoped read of the dedication evidence for every line this walk could
        # offer, instead of the whole claim table per candidate.
        self._prime_claims([str(line.id) for line, _po, _sup, _wh in po_rows])
        deficit = (
            set()
            if manual
            else (
                self._groups_in_deficit(product_id, po_rows, by_po)
                - self._exempt_groups_for_row(row, product_id)
            )
        )
        for line, po, supplier, warehouse in po_rows:
            remaining = (
                _dec(line.qty_ordered)
                - _dec(line.qty_received)
                - by_po.get(str(line.id), _ZERO)
            )
            if remaining <= _ZERO:
                continue
            location = warehouse.warehouse_code if warehouse else None
            if group_of_warehouse_code(location) in deficit:
                continue
            target_id = str(line.id)
            raw_remaining = remaining
            _, dedicated_to, own_claim = self._dedication_for_target(
                target_id, own_so_number, ignore_claim_ids=ignore_claim_ids
            )
            remaining = max(
                remaining - self._reserved_for_netting(target_id, own_so_number), _ZERO
            )
            project_bin = not is_site_pool(warehouse.segment if warehouse else None)
            candidates.append(
                self._candidate(
                    kind="po",
                    target_id=target_id,
                    document=po.po_number,
                    line_label=self._line_label(line.source_ref),
                    location=location,
                    issue_date=po.issue_date,
                    expected_date=line.expected_date,
                    remaining=remaining,
                    qty_ordered=_dec(line.qty_ordered),
                    qty_received=_dec(line.qty_received),
                    supplier_name=supplier.supplier_name if supplier else None,
                    unit_cost=line.unit_cost,
                    currency=line.currency,
                    own_location=own_location,
                    pools=pools,
                    cited=cited,
                    own_so_claim=own_claim,
                    dedicated_to=dedicated_to,
                    project_locked=project_bin and not own_claim,
                    unattributed=project_bin and dedicated_to is None and not own_claim,
                    raw_remaining=raw_remaining,
                )
            )

        if spo_allowed:
            spo_rows = (
                self.db.query(SPOAllocation, Supplier, Warehouse)
                .outerjoin(InboundShipment, InboundShipment.id == SPOAllocation.inbound_shipment_id)
                .outerjoin(Supplier, Supplier.id == SPOAllocation.supplier_id)
                .outerjoin(Warehouse, Warehouse.id == SPOAllocation.warehouse_id)
                .filter(
                    SPOAllocation.product_id == product_id,
                    SPOAllocation.spo_number.isnot(None),
                    *spo_supply.open_incoming_clauses(),
                )
                .all()
            )
            self._prime_claims([str(a.id) for a, _sup, _wh in spo_rows])
            for allocation, supplier, warehouse in spo_rows:
                remaining = (
                    _dec(allocation.allocated_quantity)
                    - _dec(allocation.quantity_received)
                    - by_spo.get(str(allocation.id), _ZERO)
                )
                if remaining <= _ZERO:
                    continue
                target_id = str(allocation.id)
                raw_remaining = remaining
                _, dedicated_to, own_claim = self._dedication_for_target(
                    target_id, own_so_number, ignore_claim_ids=ignore_claim_ids
                )
                remaining = max(
                    remaining - self._reserved_for_netting(target_id, own_so_number),
                    _ZERO,
                )
                location = (
                    warehouse.warehouse_code if warehouse else allocation.location_code
                )
                # R11 (`PLAN-scm-oi-draft-links.md`): an SPO is always allocated at a POOL
                # location and moved out from there, so only a pool line is OFFERED. A line
                # at any other code is SHOWN - the lightbox lists every line of the
                # document - and never dealt, because the goods on it are already spoken
                # for by the site that holds them. Read off the warehouse rather than the
                # book's raw code: an unknown code resolved to no warehouse, and a code
                # nobody holds is not a pool.
                #
                # A PERSON naming a line by hand is let through, exactly as `manual`
                # already lets them through to a purchase order that is not yet active and
                # past a group in deficit. The container planner is the caller that needs
                # it: its ticks say "this row is served by THIS container line", the line
                # is at the site the split just sent it to, and that is a deliberate
                # instruction rather than the automatic walk helping itself.
                #
                # The WAREHOUSE and never the book's raw code (review round 28 Aug): the
                # listing's own read of the same rule (`link_candidate_products`) joins the
                # warehouse and can read nothing else, so a book line carrying a pool-shaped
                # code the master does not hold would have been dealt here and reported as
                # no candidate there - the flag offering a Link the dialog then shows empty.
                if not manual and (
                    str(warehouse.warehouse_code if warehouse else "").strip().upper()
                    not in pools
                ):
                    continue
                project_bin = not is_site_pool(warehouse.segment if warehouse else None)
                candidates.append(
                    self._candidate(
                        kind="spo",
                        target_id=target_id,
                        document=allocation.spo_number,
                        line_label=self._line_label(allocation.spo_line_number),
                        location=location,
                        issue_date=allocation.issue_date,
                        expected_date=allocation.expected_date,
                        remaining=remaining,
                        qty_ordered=_dec(allocation.allocated_quantity),
                        qty_received=_dec(allocation.quantity_received),
                        supplier_name=supplier.supplier_name if supplier else None,
                        unit_cost=allocation.unit_cost,
                        currency=allocation.currency,
                        own_location=own_location,
                        pools=pools,
                        cited=cited,
                        own_so_claim=own_claim,
                        dedicated_to=dedicated_to,
                        project_locked=project_bin and not own_claim,
                        unattributed=project_bin and dedicated_to is None and not own_claim,
                        raw_remaining=raw_remaining,
                    )
                )

        candidates.sort(key=lambda candidate: candidate["sort"])
        return candidates

    def _candidates_including_own_closed_links(
        self, row: OrderInquiryRow, *, manual: bool
    ) -> List[Dict[str, Any]]:
        """`_candidates_for_row(credit_own_links=True)` widened to FORCE-INCLUDE every
        line this row already holds a live link on, even one the ordinary walk's OPEN
        line-status / ACTIVE-PO filters would drop (S8 review round, 17 Sep). A PO line
        closed, or a purchase order taken off active/partial, after the link was written
        still has to answer BOTH the dialog's GET (so the buyer sees what the row holds,
        AC-CF-24/26) and `_place_on_po_set`'s own validation (so re-submitting or
        lowering that same take does not read as "not a candidate" and 409, and an
        untouched take never falls out of the submission and gets retired for having
        nowhere to be resubmitted to - the merge-blocker this method exists to close).

        A forced entry's `remaining` / `raw_remaining` is the row's own credited take and
        nothing more: a closed line has nothing ELSE left to offer, but what this row
        already holds there is always still there to keep or hand back. `dedicated_to` /
        `unattributed` / `cascadable` stay at their ordinary defaults (unclaimed,
        unlocked, cascadable) - a closed line is never something the automatic pass
        reaches anyway, and the dialog's grey states exist to explain what a MANUAL link
        may still take, not to grade one the row already holds.

        Every entry the ordinary walk offers carries `line_open: True`; a forced one
        carries `line_open: False` - the ONLY reason it needed forcing in the first
        place.
        """
        candidates = self._candidates_for_row(row, manual=manual, credit_own_links=True)
        for candidate in candidates:
            candidate["line_open"] = True
        present = {candidate["target_id"] for candidate in candidates}
        own_take_by_target: Dict[str, Decimal] = {}
        for link in self._links_of(row.id):
            target = str(link.po_line_id or link.spo_allocation_id)
            own_take_by_target[target] = own_take_by_target.get(target, _ZERO) + _dec(
                link.qty
            )
        missing = {target for target in own_take_by_target if target not in present}
        if not missing:
            return candidates

        pools = self._pool_codes()
        cited = self._cited_documents(row)
        own_location = (row.stock_location or "").strip().upper() or None
        forced: List[Dict[str, Any]] = []
        seen: set = set()
        for link in self._links_of(row.id):
            target_id = str(link.po_line_id or link.spo_allocation_id)
            if target_id not in missing or target_id in seen:
                # TWO links on the same line (reachable via the ADD path, which does
                # not merge onto an existing link the way SET semantics does) must
                # force ONE candidate, not one per link - `own_take_by_target` is
                # already the SUM across every link on this target, so the second
                # link here would otherwise render the same line twice, each row
                # carrying the full summed take (the dialog's own total then double-
                # counts it).
                continue
            seen.add(target_id)
            take = own_take_by_target[target_id]
            if link.po_line_id:
                found = (
                    self.db.query(PurchaseOrderLine, PurchaseOrder, Supplier, Warehouse)
                    .join(
                        PurchaseOrder,
                        PurchaseOrder.id == PurchaseOrderLine.purchase_order_id,
                    )
                    .outerjoin(Supplier, Supplier.id == PurchaseOrder.supplier_id)
                    .outerjoin(
                        Warehouse, Warehouse.id == PurchaseOrderLine.warehouse_id
                    )
                    .filter(PurchaseOrderLine.id == link.po_line_id)
                    .first()
                )
                if found is None:
                    continue
                line, po, supplier, warehouse = found
                candidate = self._candidate(
                    kind="po",
                    target_id=target_id,
                    document=po.po_number,
                    line_label=self._line_label(line.source_ref),
                    location=warehouse.warehouse_code if warehouse else None,
                    issue_date=po.issue_date,
                    expected_date=line.expected_date,
                    remaining=take,
                    qty_ordered=_dec(line.qty_ordered),
                    qty_received=_dec(line.qty_received),
                    supplier_name=supplier.supplier_name if supplier else None,
                    unit_cost=line.unit_cost,
                    currency=line.currency,
                    own_location=own_location,
                    pools=pools,
                    cited=cited,
                    raw_remaining=take,
                )
            elif link.spo_allocation_id:
                found = (
                    self.db.query(SPOAllocation, Supplier, Warehouse)
                    .outerjoin(Supplier, Supplier.id == SPOAllocation.supplier_id)
                    .outerjoin(Warehouse, Warehouse.id == SPOAllocation.warehouse_id)
                    .filter(SPOAllocation.id == link.spo_allocation_id)
                    .first()
                )
                if found is None:
                    continue
                allocation, supplier, warehouse = found
                candidate = self._candidate(
                    kind="spo",
                    target_id=target_id,
                    document=allocation.spo_number,
                    line_label=self._line_label(allocation.spo_line_number),
                    location=(
                        warehouse.warehouse_code if warehouse else allocation.location_code
                    ),
                    issue_date=allocation.issue_date,
                    expected_date=allocation.expected_date,
                    remaining=take,
                    qty_ordered=_dec(allocation.allocated_quantity),
                    qty_received=_dec(allocation.quantity_received),
                    supplier_name=supplier.supplier_name if supplier else None,
                    unit_cost=allocation.unit_cost,
                    currency=allocation.currency,
                    own_location=own_location,
                    pools=pools,
                    cited=cited,
                    raw_remaining=take,
                )
            else:
                continue
            candidate["line_open"] = False
            forced.append(candidate)

        combined = candidates + forced
        combined.sort(key=lambda candidate: candidate["sort"])
        return combined

    def _netting(self, product_ids: Sequence[Optional[str]]) -> GroupNetting:
        """Ladder v4's availability reader, over every product asked about so far.

        Rebuilt when a product it has not seen turns up, so a caller that primes it with
        the whole batch (the cascade does) pays for ONE three-query read and a single-row
        caller pays for one too. It is the same `group_netting` reader the fulfilment ladder
        uses, which is the point: the board refusing to promise a group's stock and
        purchasing linking a row to that group's purchase order would be two answers to one
        question.

        **The same READER, over a WIDER span** (AC-S1-5b, corrected 30 Aug). This call is
        not `planning_only`, so its ownership-group index covers every ACTIVE bin, while the
        board's proposal reads only the bins flagged into fulfilment planning (R17). That is
        deliberate and it is the boundary the AC draws: the flag narrows what a PROPOSAL may
        draw, never what a non-planning consumer may see, so the deficit gate here still
        sees a group's real negative net. The two therefore agree about the ARITHMETIC and
        can legitimately differ about the SPAN.
        """
        wanted = {str(pid) for pid in product_ids if pid}
        if self._netting_value is None or wanted - self._netted_products:
            self._netted_products |= wanted
            self._netting_value = netting_for_products(
                self.db, sorted(self._netted_products)
            )
        return self._netting_value

    def _groups_in_deficit(
        self,
        product_id: str,
        po_rows: Sequence[Any],
        by_po: Dict[str, Decimal],
    ) -> set:
        """The ownership groups whose OPEN PURCHASE ORDERS cannot cover their own backlog
        (ladder v4, section 1d).

        SLICE H, 8 Sep 2026: a group line is `cascadable` now only when it sits at the site
        pool (it does not - a group line is a group line precisely because it is not one)
        or when THIS row's own SO already claims it (`own_so_claim`, G12's `own_claim`).
        This set therefore bears on the automatic pass only through that second, narrower
        door; its main effect is still on what `_candidates_for_row` OFFERS, which is what
        the Link dialog lists and what a manual placement (`manual=True`) may take. Left in
        place rather than removed for that reason.

        `group_net + everything still to come on the group's own purchase orders`. At or
        above zero the group has purchases nobody has claimed and a row may link to one;
        BELOW zero, every unit already on order is owed to demand the group carries and a
        link would promise the same stock twice.

        ZERO IS OFFERED, and that boundary is the ordinary case rather than an edge one
        (captain, 27 Aug). A purchase order raised off the plan buys exactly what the plan
        said the group was short, so the group lands on precisely `net + remaining == 0`.
        Read as "at or below zero is deficit", that group was refused its own purchase
        order: the rows that sized the buy stayed raised, the PO-confirm cascade offered
        them nothing and the Link dialog listed nothing. Nothing is promised twice at zero -
        the demand the buy covers IS the demand the group carries.

        The second half of the same ruling - a group holding an ACKNOWLEDGED, UNLINKED row
        for this product may reach its own purchase order however short the arithmetic says
        it is - is NOT applied here. It is a per-ROW exemption and it is applied by
        `_candidates_for_row` to the row being placed (B1, code review 27 Aug 2026):
        lifting the group out of this set instead exempted EVERY row of the product, so a
        row at another group entirely - walked first, because candidates are ranked by
        location and never filtered by it - helped itself to the line the exempt group's
        own backlog had been bought.

        The deficit itself is computed off the CANDIDATE ROWS, so it costs no query beyond
        the netting read: those rows already are the product's whole open purchase-order
        book, and `by_po` is already netted for the links written against them. That claim
        was FALSE while the exemption lived here - it ran up to three uncached queries per
        row through `_groups_awaiting_a_link` (S3, code review 27 Aug 2026) - and is true
        again now the exemption is the caller's, and memoised per product besides.
        """
        remaining_by_group: Dict[str, Decimal] = {}
        for line, _po, _supplier, warehouse in po_rows:
            group = group_of_warehouse_code(
                warehouse.warehouse_code if warehouse else None
            )
            if not group:
                continue
            remaining = (
                _dec(line.qty_ordered)
                - _dec(line.qty_received)
                - by_po.get(str(line.id), _ZERO)
            )
            if remaining > _ZERO:
                remaining_by_group[group] = (
                    remaining_by_group.get(group, _ZERO) + remaining
                )
        if not remaining_by_group:
            return set()
        netting = self._netting([product_id])
        return {
            group
            for group, remaining in remaining_by_group.items()
            if netting.group_net(product_id, group).net + remaining < _ZERO
        }

    def _exempt_groups_for_row(self, row: OrderInquiryRow, product_id: str) -> set:
        """The deficit exemption THIS row has earned, and nobody else's (B1, code review
        27 Aug 2026).

        SLICE H, 8 Sep 2026: the same note as `_groups_in_deficit` carries here - a group
        line this exemption lifts is `cascadable` only if it also clears the pool-or-own-
        claim test in `_candidate`, so the exemption mostly bears on what
        `_candidates_for_row` OFFERS (the Link dialog) and on a manual placement, and on
        the automatic pass only for a line THIS row's own SO already claims. Left in place
        for that reason.

        A group holding an acknowledged, still-unlinked row for the product may reach its
        own purchase order however short it is, because that row is the demand somebody
        bought the order for (captain, 27 Aug). The exemption is the ROW's, not the
        product's: subtracting it from the deficit set once per product let any row of the
        product take the line, and the walk ranks candidates by location without ever
        filtering by it, so a row at another group walked first simply took it.

        `set()` for a row that is rejected, fully linked, or resolves to no location - none
        of those is the instruction a buy answers. An AWAITING row does count (R6): its
        draft is the answer purchasing will read, and refusing a group its own purchase
        order until somebody pressed Confirm left the page reading "Not found" for exactly
        the rows the buy was sized from.
        """
        return {
            group
            for group, row_ids in self._rows_awaiting_a_link(product_id).items()
            if str(row.id) in row_ids
        }

    def _groups_awaiting_a_link(self, product_id: str) -> set:
        """The ownership groups holding a still-unlinked row of this product - the demand
        a purchase order at that group was bought for.

        The LISTING's own read (`link_candidate_products`), which answers per product and
        has no row in hand. The per-ROW exemption the candidate walk applies is
        `_exempt_groups_for_row`.
        """
        return set(self._rows_awaiting_a_link(product_id))

    def _rows_awaiting_a_link(self, product_id: str) -> Dict[str, set]:
        """`{group: {row ids}}` - every row of this product a document is still owed to,
        filed under the group it sits at.

        Read off the rows the cascade itself would walk (open supply state, linkable verb,
        anything but rejected), so "there is an instruction waiting" here and "there is a
        row to place" there cannot disagree. AWAITING rows count since
        `PLAN-scm-oi-draft-links.md` R6: the cascade drafts for them now, and the demand a
        group's purchase order was bought for is exactly the row CS has just raised - the
        earlier `ACK_LINKABLE` reading refused a group its own buy until somebody had
        pressed Confirm, which is the press this whole plan exists to answer.

        The row's group is its own stated `stock_location` where it has one, and otherwise
        the reconciled core line's warehouse - the same two arms `rows_needed_at` reads a
        row's location through. A row that resolves to no location belongs to no group and
        is not evidence about one.

        MEMOISED per product on the instance (S3, code review 27 Aug 2026): it costs up to
        three queries and the cascade asks it once per row, which on a full pass over one
        product is the same three queries answered over and over. A row this pass has since
        linked cannot change the answer for the row being placed now - it is placed before
        its own links are written, and it is walked once. The memo is dropped by
        `_invalidate_link_cache` on every link written or removed all the same (item 4),
        because a caller that writes and then READS through one instance - which is every
        link route - would otherwise be answered about a state it has already left.
        """
        cached = self._awaiting_link_cache.get(str(product_id))
        if cached is not None:
            return cached
        answer = self._read_rows_awaiting_a_link(product_id)
        self._awaiting_link_cache[str(product_id)] = answer
        return answer

    def _read_rows_awaiting_a_link(self, product_id: str) -> Dict[str, set]:
        """`_rows_awaiting_a_link` without the memo - the queries themselves."""
        query = self.db.query(OrderInquiryRow).filter(
            OrderInquiryRow.state.in_((INQUIRY_RAISED, INQUIRY_PARTLY_LINKED)),
            OrderInquiryRow.verb.in_(_LINKABLE_VERBS),
            OrderInquiryRow.ack_state.in_(
                tuple(ACK_LINKABLE) + (ACK_AWAITING,)
            ),
        )
        query = self._narrow_to_products(query, [product_id])
        rows = query.all() if query is not None else []
        if not rows:
            return {}
        linked = self._linked_qty_by_row([str(row.id) for row in rows])
        owed = [
            row
            for row in rows
            if _dec(row.qty) - linked.get(str(row.id), _ZERO) > _ZERO
        ]
        if not owed:
            return {}

        # The core line's warehouse CODE, for the rows that state no location of their own.
        so_line_ids = [row.so_line_id for row in owed if row.so_line_id]
        core_code: Dict[str, Optional[str]] = {}
        if so_line_ids:
            for psl_id, code in (
                self.db.query(ProjectSalesOrderLine.id, Warehouse.warehouse_code)
                .join(
                    SalesOrderLine,
                    SalesOrderLine.id == ProjectSalesOrderLine.core_sales_order_line_id,
                )
                .outerjoin(Warehouse, Warehouse.id == SalesOrderLine.warehouse_id)
                .filter(ProjectSalesOrderLine.id.in_(so_line_ids))
                .all()
            ):
                core_code[str(psl_id)] = code

        groups: Dict[str, set] = {}
        for row in owed:
            code = (row.stock_location or "").strip() or core_code.get(
                str(row.so_line_id or "")
            )
            group = group_of_warehouse_code(code)
            if group:
                groups.setdefault(group, set()).add(str(row.id))
        return groups

    @staticmethod
    def _line_label(raw: Any) -> Optional[str]:
        """`L3`, when the book numbered the line. `None` when it did not.

        The purchase book carries its line number in `purchase_order_lines.source_ref` and
        the shipping book in `spo_allocations.spo_line_number`; plenty of documents on the
        dev copy carry neither, and a made-up ordinal beside a document with six open lines
        would name the wrong one. The reader falls back to the LOCATION, which is a fact.
        """
        if raw is None:
            return None
        text_value = str(raw).strip()
        if not text_value:
            return None
        return f"L{text_value}"

    def _candidate(
        self,
        *,
        kind: str,
        target_id: str,
        document: Optional[str],
        line_label: Optional[str],
        location: Optional[str],
        issue_date: Optional[date],
        expected_date: Optional[date],
        remaining: Decimal,
        qty_ordered: Decimal,
        qty_received: Decimal,
        supplier_name: Optional[str],
        unit_cost: Any,
        currency: Optional[str],
        own_location: Optional[str],
        pools: set,
        cited: Dict[str, int],
        own_so_claim: bool = False,
        dedicated_to: Optional[str] = None,
        project_locked: bool = False,
        unattributed: bool = False,
        raw_remaining: Optional[Decimal] = None,
    ) -> Dict[str, Any]:
        """One candidate, with the sort key that IS the walk (G7, then Q5, then Q7).

        The key, outermost first: whether THIS row's own SO claims the line (G7, AC-6.4) -
        an explicit dedication outranks even what CS cited, because a claim is a fact
        while a citation is a hint typed on a form; then WHICH document CS cited, in the
        order they wrote them - `SPO-2026/08-0061 & 202606-S0082` tries the allocation
        before the purchase order, and a rank rather than a flag is what makes that true (a
        flag puts both in one bucket and lets the date decide between two documents the
        form already ordered); then an SPO before a purchase order (an order back is owed
        against what is already shipped before it is owed against a new purchase); then the
        location tier; then the pool sub-rank inside tier 3 (the row's own site pool before
        the others); then the PO's own issue date, then the line's expected date, then the
        document number, then the id so a tie breaks the same way twice.

        `dedicated_to` / `unattributed` are the Link dialog's grey states (G7 / G12): the
        SO number another claim names, or - project-bin only - that nobody has claimed it
        at all. `project_locked` folds into `cascadable` below rather than living beside
        it: a line the automatic pass may not touch is exactly what `cascadable` already
        means, whichever rule refused it.
        """
        tier, sub = link_location_tier(own_location, location, pools)
        # Uncited sorts after every citation, however many there are.
        citation_rank = cited.get(str(document or "").strip().upper(), len(cited) + 1)
        is_cited = bool(document) and citation_rank <= len(cited)
        return {
            "kind": kind,
            "po_line_id": target_id if kind == "po" else None,
            "spo_allocation_id": target_id if kind == "spo" else None,
            "target_id": target_id,
            "document": document,
            "line_label": line_label,
            "location": location,
            "tier": tier,
            # What the automatic pass may take (the owner, 8 Sep 2026 - slice H, correcting
            # the reading of the 27 August ruling, and the captain's own S1 ruling on the
            # review round that followed): a line claimed by THIS row's own sales order, OR
            # one that sits at the SITE POOL. Nothing else - not the row's own project
            # location (tier 1), not its ownership group at another site (tier 2), not a
            # sibling location at the site (tier 4). The 27 August wording ("the site pool
            # and better") read as a ceiling that also admitted tiers 1 and 2, and on real
            # data that let the automatic pass take BRW-IB's own open line for SO391853 out
            # from under it - stock standing at a project location is already spoken for by
            # that project UNLESS this row's own SO is the one holding it.
            #
            # `own_so_claim` is that exception, and it is G12's own `own_claim` - not a new
            # mechanism. `is_site_pool(segment)` is the SAME predicate `project_locked`
            # already reads it through (`project_locked = project_bin and not own_claim`,
            # `project_bin = not is_site_pool(...)`), never `_pool_codes()`'s FK graph:
            # S1, the captain's own ruling, corrects the first cut of this line, which
            # tested FK pool membership - a SECOND, different definition of "pool" living
            # beside `project_locked`'s. `pool_predicate.is_site_pool` is written to be the
            # one spelling of a site pool, `segment`-based; `_pool_codes()` stays, but only
            # for `link_location_tier`'s ORDERING below, a different question entirely. The
            # algebra: with `own_claim` true, `project_locked` is always false regardless of
            # `project_bin`, so `own_so_claim or not project_locked` already reduces to
            # `own_so_claim or is_site_pool(segment)` - no second field to thread in.
            #
            # The tier and its sub-rank are UNCHANGED and still decide the ORDER a pool is
            # tried in (the row's own site pool before the others). The Link dialog is
            # untouched: it still lists every tier, including a project-location line, and
            # a buyer may take one by hand - that override path is `manual`, read below.
            #
            # G12's OTHER half stands: a project-bin line claimed by ANOTHER SO, or by
            # nobody, is refused to the automatic pass however good its location tier,
            # because the SO that claims it is the only one allowed to auto-take it.
            #
            # There is no trial, preview or self-claiming variant of `own_so_claim`, and
            # there must never be one (captain, 2 Sep 2026, on real data): the cascade
            # writing its OWN claim for a line it did not create is how PO 202607-S0067's
            # 114 units at BRW-IB - bought for SO391853 per the AutoCount book - were taken
            # by SO381895. `own_so_claim` is computed ONCE, upstream, off attribution the
            # SUPPLY WRITER that created the line wrote (`app/services/scm/supply_claim.py`),
            # the book's own FromSODocList column, or a person in the Link dialog - never by
            # the pass that wants to consume it; this rule only READS that value.
            "cascadable": own_so_claim or not project_locked,
            "issue_date": issue_date,
            "expected_date": expected_date,
            "remaining": remaining,
            # `remaining` before G7's dedication subtracted anything - what an ACTUAL
            # link already claims, and nothing a claim alone reserves. `po_candidates_for_row`
            # reads `already_tagged` off this rather than off `remaining`, or a dedicated
            # line with no link on it at all would misreport a link that was never written.
            "raw_remaining": raw_remaining if raw_remaining is not None else remaining,
            "qty_ordered": qty_ordered,
            "qty_received": qty_received,
            "supplier_name": supplier_name,
            "unit_cost": unit_cost,
            "currency": currency,
            "cited": is_cited,
            # The G7 fact itself, not just its fold into `cascadable` above - S2
            # (`PLAN-oi-cascade-skip-early-arrival.md`) reads this to let an early
            # candidate through the lead-time window when THIS row's own SO claims it.
            "own_so_claim": own_so_claim,
            # G7's dedication label: the SO another claim names, when this row's own SO
            # is not the one holding it. None on the ordinary, unclaimed line.
            "dedicated_to": dedicated_to,
            # G12's project-bin lock, unclaimed by ANY SO - the "Unattributed" state,
            # distinct from `dedicated_to` (claimed, just not by this row).
            "unattributed": unattributed,
            "sort": (
                0 if own_so_claim else 1,
                citation_rank,
                0 if kind == "spo" else 1,
                tier,
                sub,
                (issue_date is None, issue_date or date.min),
                (expected_date is None, expected_date or date.min),
                document or "",
                target_id,
            ),
        }

    @staticmethod
    def _cascade_take(
        candidates: Sequence[Dict[str, Any]],
        need: Decimal,
        *,
        held_by_others: Optional[Dict[str, Decimal]] = None,
    ) -> List[Tuple[Dict[str, Any], Decimal]]:
        """`min(what is left on this line, what is still needed)` off each CASCADABLE
        candidate in the order it was given, until the need is covered - or NOTHING at all,
        ruled by the owner 8 Sep 2026 (slice D): when the cascadable candidates cannot
        cover `need` IN FULL, this returns an empty list rather than the partial cover it
        used to.

        Half covering a 493-piece row and buying the other 378 strands whatever the half
        DID cover - it cannot be re-offered to another row that needed exactly that much -
        while leaving the row PARTLY LINKED with the balance still counting as demand. The
        row's whole quantity going to Buy is the honest outcome when nothing on hand can
        answer it in full; a row already `partly_linked` from before this ruling is left
        exactly as it is (not retro-applied), and `partly_linked` stays reachable through a
        re-deal, a book re-upload, or a manual partial taken by hand in the Link dialog -
        none of which calls this method (`place_on_po_allocations` walks `by_target`
        directly and is never routed through the cascade).

        `held_by_others` (AC-LT-14, G2) is the walk's own suggestion-path addition: what
        OTHER rows already suggested on each target, this pass or an earlier one. The
        ALL-OR-NOTHING gate above stays read off the document's raw capacity - a line
        that can genuinely cover the need is never refused outright over a scarcity
        another row's own GUESS created - but the per-candidate take below is netted
        against it, so two rows are never offered the very same units. `None` (every
        caller before this parameter existed, and the Link dialog's own preview) is a
        no-op and leaves this exactly as it always was.

        Review round 4 found that netting `held_by_others` UNCONDITIONALLY double-
        subtracted a claim-dedicated candidate's share: `remaining` is already reduced by
        `_reserved_for_netting` for every OTHER sales order's claim on the line, so
        subtracting that same other claimant's suggestion again on top read two rows
        each claim-dedicated 30 and 84 of one 114 line as 84 and 0 instead of 30 and 84.
        Round 4's fix over-corrected: exempting every `own_so_claim`/`cited` candidate
        from `held_by_others` outright let two rows CITING the same document take its
        full capacity twice (`test_ac_lt_14_two_cited_rows_still_net_against_each_
        other`), and let two rows of the SAME sales order - `_reserved_for_netting`
        skips a row's OWN SO, so neither row's `remaining` was reduced for the other -
        each take the line's full amount
        (`test_ac_lt_14_two_rows_of_one_so_never_suggested_more_than_the_line`).

        Review round 5: only the SLICE of `held_by_others` that `_reserved_for_netting`
        already reserved for another SO's claim is exempt, never the whole thing, and
        never on the say-so of `cited` alone (a citation is not a claim). That slice is
        `raw_remaining - remaining` - exactly what `_reserved_for_netting` subtracted
        when this candidate's `remaining` was built - so a held total that MATCHES it
        (the two-claimants case: the other claimant's own suggestion is exactly its
        reserved share) nets to zero here, same as before, while any held total ABOVE it
        (a cited or same-SO row's actual take, which is not backed by a reservation this
        candidate's own `remaining` already accounts for) still nets in full.
        """
        cascadable_total = sum(
            (
                candidate["remaining"]
                for candidate in candidates
                if candidate.get("cascadable", True)
            ),
            _ZERO,
        )
        if cascadable_total < need:
            return []
        still = need
        takes: List[Tuple[Dict[str, Any], Decimal]] = []
        for candidate in candidates:
            if still <= _ZERO:
                break
            # Only a candidate the SITE POOL owns, or one THIS row's own SO already claims,
            # is ever taken automatically (slice H, correcting the 27 August reading - see
            # `_candidate`'s own `cascadable`). A project-location line claimed by nobody or
            # by another SO, a sibling group's line, or one at another site is still LISTED
            # in the Link dialog - a buyer may take it by hand - but the automatic pass
            # never does.
            if not candidate.get("cascadable", True):
                continue
            remaining = candidate["remaining"]
            if held_by_others:
                already_reserved = max(
                    candidate["raw_remaining"] - candidate["remaining"], _ZERO
                )
                excess_held = max(
                    held_by_others.get(candidate["target_id"], _ZERO) - already_reserved,
                    _ZERO,
                )
                remaining = max(remaining - excess_held, _ZERO)
            take = remaining if remaining < still else still
            if take > _ZERO:
                takes.append((candidate, take))
                still -= take
        return takes

    @staticmethod
    def _within_window(
        row: OrderInquiryRow, candidates: Sequence[Dict[str, Any]], lead_days: int
    ) -> List[Dict[str, Any]]:
        """The candidates this row may be linked to AUTOMATICALLY
        (`PLAN-oi-cascade-skip-early-arrival.md` S2/S4) - the ONE filter both
        `auto_place_for_products` and the Link dialog's own preview
        (`po_candidates_for_row`) run, so the walk and the dialog never disagree about
        which candidate the pass would take.

        A candidate THIS row's own SO claims (`own_so_claim`), or one the row cites
        (`cited`), is exempt regardless of when it arrives - a person or the book
        outranks the window exactly as it already outranks every ordering rule in
        `_candidate`'s own sort key. Everything else promised a full lead time (or
        more) before `row.delivery_date` (`arrives_outside_window`) is REMOVED from
        the list, not marked `cascadable=False` (F3, review round 2): `cascadable` is a
        property of the LINE - who may take it at ALL, whichever row is asking - while
        the window is a property of THIS ROW's own delivery date, so folding it into
        `cascadable` would state a row-specific fact on a dict several rows' walks can
        share. An early candidate simply stays OUT of the list this call returns; it is
        still listed in the Link dialog (`po_candidates_for_row` runs this same filter
        only for `recommended`/`default_take`, never to drop a row from what it shows).
        """
        return [
            candidate
            for candidate in candidates
            if candidate.get("own_so_claim")
            or candidate.get("cited")
            or not arrives_outside_window(
                candidate.get("expected_date"), row.delivery_date, lead_days
            )
        ]

    def po_candidates_for_row(self, row_id: str) -> List[Dict[str, Any]]:
        """The candidate list the Link dialog shows, in the walk's own order.

        `remaining` already nets what every OTHER link claims on the same line, so `covers`
        and `default_take` are answered against what is ACTUALLY left. `default_take` and
        `recommended` are the cascade's own preview, computed by the SAME walk
        `auto_place_for_products` runs and through the SAME lead-time window it refuses an
        early candidate with (`_within_window`, S4, `PLAN-oi-cascade-skip-early-
        arrival.md`) - which is what stops the dialog and the automatic pass being two
        opinions. A candidate the window refuses stays LISTED, so a buyer may still take
        it by hand, but reads `recommended = False` and `default_take = "0"`: the dialog
        must never pre-recommend the very line the automatic pass just refused.

        The need is the row's UNLINKED remainder, not its whole quantity: a row already
        linked 5 of 8 opens the dialog looking for 3.

        S8's "choose document in one press" widened this to a row that ALREADY holds
        links: `credit_own_links=True` adds this row's own take back into every line's
        `remaining` before the walk measures it, so a line this row has fully claimed -
        `remaining` would otherwise read 0 and the line would vanish from the list -
        still shows, with `current_take` naming what this row already holds there and
        `remaining` read as if that take were free to re-place. A line nothing of this
        row's sits on carries `current_take: "0"`, unaffected either way.

        S8 review round (17 Sep, merge-blocker): a line closed - or its PO taken off
        active/partial - AFTER this row's link was written used to vanish from this list
        entirely, and a "Choose document" press that never touched it then read as
        retiring it (`_candidates_including_own_closed_links` force-includes it, with
        `line_open: False` so the dialog can grey it apart from an ordinary candidate).
        """
        row = self._row_or_404(row_id)
        self._assert_linkable(row)
        product_id = self._resolve_product_id(row)
        if not product_id:
            raise AppException(
                status_code=409,
                message="This row names no product to match a purchase order line against.",
                code="order_inquiry_no_product",
            )
        need = self._unlinked_need(row)
        # S8 review round: force-includes every line this row already holds a live
        # link on, even one the ordinary open/active walk would drop (a closed line,
        # or a PO taken off active/partial, since the link was written).
        candidates = self._candidates_including_own_closed_links(row, manual=False)
        # S4: the SAME lead-time source and default S2 reads, for this one product -
        # over the SAME candidate list (forced entries included), so a closed line
        # this row already holds never becomes the cascade's "recommended" pick.
        from app.services.project_supply_service import ProjectSupplyService

        lead_days = ProjectSupplyService(self.db).lead_times({product_id}).get(product_id)
        if lead_days is None:
            lead_days = DEFAULT_LEAD_TIME_DAYS
        within_window = self._within_window(row, candidates, lead_days)
        within_window_ids = {candidate["target_id"] for candidate in within_window}
        cascade = {
            candidate["target_id"]: take
            for candidate, take in self._cascade_take(within_window, need)
        }
        claims_by_line = self._linked_claims_by_target(candidates)
        own_take_by_target: Dict[str, Decimal] = {}
        for link in self._links_of(row.id):
            target = str(link.po_line_id or link.spo_allocation_id)
            own_take_by_target[target] = own_take_by_target.get(target, _ZERO) + _dec(
                link.qty
            )
        out: List[Dict[str, Any]] = []
        for candidate in candidates:
            # Off `raw_remaining` - what an ACTUAL link already claims - never off the
            # dedication-reduced `remaining`, or a line no link has ever touched but
            # another SO's claim reserves would misreport that claim as an "already
            # tagged" placement nobody made (G7). `credit_own_links` has already taken
            # this row's OWN take back out of `raw_remaining`, so `already` names what
            # OTHER rows claim, never this one's.
            already = (
                candidate["qty_ordered"]
                - candidate["qty_received"]
                - candidate["raw_remaining"]
            )
            out.append(
                {
                    "kind": candidate["kind"],
                    "po_line_id": candidate["po_line_id"],
                    "spo_allocation_id": candidate["spo_allocation_id"],
                    "po_number": candidate["document"],
                    "line_label": candidate["line_label"],
                    "location": candidate["location"],
                    "tier": candidate["tier"],
                    "cited": candidate["cited"],
                    "supplier_name": candidate["supplier_name"],
                    "issue_date": candidate["issue_date"],
                    "expected_date": candidate["expected_date"],
                    "qty_ordered": _qty_str(candidate["qty_ordered"]),
                    "qty_received": _qty_str(candidate["qty_received"]),
                    "already_tagged": _qty_str(already),
                    "remaining": _qty_str(candidate["remaining"]),
                    "covers": candidate["remaining"] >= need,
                    "recommended": False,
                    "default_take": _qty_str(cascade.get(candidate["target_id"], _ZERO)),
                    "unit_cost": (
                        _qty_str(_dec(candidate["unit_cost"]))
                        if candidate["unit_cost"] is not None
                        else None
                    ),
                    "currency": candidate["currency"],
                    "claims": claims_by_line.get(candidate["target_id"], []),
                    # G7 / G12 (S6): the dedication state the dialog greys with.
                    "dedicated_to": candidate["dedicated_to"],
                    "unattributed": candidate["unattributed"],
                    # S8: this row's own current take on this line, "0" when it holds
                    # none - the dialog's "Current" mark and prefilled Take.
                    "current_take": _qty_str(
                        own_take_by_target.get(candidate["target_id"], _ZERO)
                    ),
                    # S8 review round: False on a forced entry - a line closed, or on a
                    # PO no longer active/partial, since this row's link was written.
                    "line_open": candidate.get("line_open", True),
                }
            )
        # S4: the first entry that BOTH covers the need AND survives the window - by the
        # entry's OWN target id (`po_line_id` on a PO candidate, `spo_allocation_id` on
        # an SPO one; `_candidate`'s `target_id` is whichever of the two is set), never
        # positional pairing against `candidates`.
        recommended = next(
            (
                entry
                for entry in out
                if (entry["po_line_id"] or entry["spo_allocation_id"]) in within_window_ids
                and entry["covers"]
            ),
            None,
        )
        if recommended is not None:
            recommended["recommended"] = True
        return out

    def linkable_qty_for_row(self, row_id: str) -> str:
        """What the row's OWN placement capacity is (AC-CF-25's SET-total ceiling,
        `_place_on_po_set`'s own `capacity`): `qty - bundled_qty`, never the bare `qty`
        the dialog's props otherwise default to. A bundled row's ala-carte remainder is
        smaller than its whole quantity - what a bundle already covers is never something
        THIS row's own links additionally claim (S8 review round, 17 Sep: the dialog's
        footer and its enable check used to read the bare `qty` and would let a bundled
        row compose an allocation the server was always going to refuse)."""
        row = self._row_or_404(row_id)
        return _qty_str(_dec(row.qty) - _dec(row.bundled_qty))

    def still_to_link_for_row(self, row_id: str) -> str:
        """The Link dialog's own header line (S8, AC-CF-24): "N still to link of Q" -
        the SAME `_unlinked_need` the candidate walk itself measures `covers` against,
        read separately so the row-id-keyed HTTP response can carry it without changing
        what `po_candidates_for_row` itself returns (existing callers read a plain list).
        """
        row = self._row_or_404(row_id)
        return _qty_str(self._unlinked_need(row))

    def _unlinked_need(self, row: OrderInquiryRow) -> Decimal:
        """What is still to be linked on this row: its quantity, less its links, less
        whatever rides inside another item's own line (plan 3.4 "Cascade") - a bundled
        unit is never something the cascade goes looking for a document for."""
        linked = sum((_dec(link.qty) for link in self._links_of(row.id)), _ZERO)
        return max(_dec(row.qty) - linked - _dec(row.bundled_qty), _ZERO)

    def _write_link(
        self,
        row: OrderInquiryRow,
        candidate: Dict[str, Any],
        qty: Decimal,
        *,
        actor_user_id: str,
        auto_trigger: Optional[str] = None,
    ) -> OrderInquiryLink:
        """One link, plus the audit claim behind it.

        `auto_trigger` names WHY this happened without a person clicking it - appended to
        the ROW's own note, which is already this feature's evidence field, rather than a
        new column. The claim is written for a PO link only: `order_link_service` keys a
        claim on (SO number, PO number, item), and an SPO number resolves through the same
        function since migration 420, so both families are claimable - but the claim's
        `source = 'order_inquiry'` delete-on-unlink rule is what makes it safe, and it is
        written for whichever document the link names.
        """
        document = candidate["document"]
        supplier = candidate["supplier_name"] or "unknown supplier"
        expected = (
            candidate["expected_date"].isoformat() if candidate["expected_date"] else "no date"
        )
        stamp = (
            f"Linked to {document or 'an unnamed document'} ({supplier}), "
            f"expected {expected}"
        )
        if auto_trigger:
            stamp = f"{stamp}; auto: {auto_trigger}"
        row.note = f"{row.note}; {stamp}" if row.note else stamp
        row.actioned_by = actor_user_id
        row.actioned_at = datetime.utcnow()

        claim_id = None
        if document:
            so_number, item_code, core_line_id = self.claim_identity(row)
            claim = order_link_service.claim_placed_on_po(
                self.db,
                company_id=row.company_id,
                so_number=so_number,
                po_number=document,
                item_code=item_code,
                so_line_id=core_line_id,
                po_line_id=candidate["po_line_id"],
                spo_allocation_id=candidate["spo_allocation_id"],
            )
            claim_id = claim.id if claim is not None else None

        link = OrderInquiryLink(
            company_id=row.company_id,
            row_id=row.id,
            po_line_id=candidate["po_line_id"],
            spo_allocation_id=candidate["spo_allocation_id"],
            document=document,
            qty=qty,
            linked_by=actor_user_id,
            linked_at=datetime.utcnow(),
            auto=bool(auto_trigger),
            claim_id=claim_id,
        )
        self.db.add(link)
        self.db.flush()
        self._invalidate_link_cache()
        # AC-LT-15 (G2, "a real link always wins"): the ONE link writer, so this is
        # the one place a real link's own arrival can trim what is left of the
        # suggestions on the SAME target - whichever caller reached here, `place_on_
        # po_allocations`'s own loop or `follow_book_for_rows`'s (fix round, 24 Sep:
        # the book never calls `place_on_po_allocations`, so a trim that only ran
        # there missed every book-named real link entirely).
        room = self._room_for_suggestions_after_real_link(candidate)
        if room is not None:
            self._trim_suggested_links_to_room(candidate, room)
        return link

    def _room_for_suggestions_after_real_link(
        self, candidate: Dict[str, Any]
    ) -> Optional[Decimal]:
        """What is left for a SUGGESTION on this target, read fresh right after a real
        link just landed on it - the document's own capacity minus every real link now
        sitting on it (this one included). A live query rather than a value the caller
        already had (`raw_remaining`, computed BEFORE this write): `_write_link` is the
        one choke point every real-link writer reaches, and a caller offering several
        allocations on the SAME target in one call would otherwise need to thread a
        running total through - a query is one extra cost per real link, and a target
        rarely holds more than a couple of suggestions to begin with.

        `None` when the target itself is gone (row already `SET NULL`led) - nothing to
        trim against, and the caller leaves the suggestions alone rather than reading
        a missing line as zero capacity.
        """
        po_line_id = candidate.get("po_line_id")
        spo_allocation_id = candidate.get("spo_allocation_id")
        if po_line_id:
            line = (
                self.db.query(PurchaseOrderLine)
                .filter(PurchaseOrderLine.id == po_line_id)
                .one_or_none()
            )
            if line is None:
                return None
            capacity = _dec(line.qty_ordered) - _dec(line.qty_received)
            real_total = _dec(
                self.db.query(func.sum(OrderInquiryLink.qty))
                .filter(OrderInquiryLink.po_line_id == po_line_id)
                .scalar()
            )
        elif spo_allocation_id:
            allocation = (
                self.db.query(SPOAllocation)
                .filter(SPOAllocation.id == spo_allocation_id)
                .one_or_none()
            )
            if allocation is None:
                return None
            capacity = _dec(allocation.allocated_quantity) - _dec(
                allocation.quantity_received
            )
            real_total = _dec(
                self.db.query(func.sum(OrderInquiryLink.qty))
                .filter(OrderInquiryLink.spo_allocation_id == spo_allocation_id)
                .scalar()
            )
        else:
            return None
        return max(capacity - real_total, _ZERO)

    def _suggested_of_row(self, row_id: str) -> List[OrderInquirySuggestedLink]:
        """This row's own suggested links, oldest first - the shape `_same_placement`
        (reused below) does not care about, but a stable order beats none for a test
        reading `_suggested_of` back."""
        return (
            self.db.query(OrderInquirySuggestedLink)
            .filter(OrderInquirySuggestedLink.row_id == row_id)
            .order_by(OrderInquirySuggestedLink.suggested_at.asc())
            .all()
        )

    @staticmethod
    def _open_for_buying_clauses() -> Tuple[Any, ...]:
        """The row conditions a suggested link may still hold capacity under (review
        round 2 Blocking 4, AC-LT-14/18): the exact INVERSE of `_drop_suggested_links`'s
        own "no longer open for buying" (cancelled, actioned, rejected, redirected to
        pool), so the two can never drift apart. A row's suggestions stop counting
        against other rows' room the moment it leaves this set, whether or not the
        writer that moved it remembered to call `_drop_suggested_links` itself.
        """
        return (
            OrderInquiryRow.state.notin_((INQUIRY_CANCELLED, INQUIRY_ACTIONED)),
            OrderInquiryRow.ack_state != ACK_REJECTED,
            OrderInquiryRow.redirected_to_pool.is_(False),
        )

    def _suggested_totals_by_target(
        self, *, exclude_row_id: Optional[str] = None
    ) -> Dict[str, Decimal]:
        """Every OTHER OPEN row's suggested total, PO and SPO merged into one dict keyed
        by target id (AC-LT-14, G2): what the walk's own take-sizing nets a candidate's
        `remaining` against, on top of the real links `_linked_by_target` already nets.

        Built ONCE per pass, not read fresh per row (Nit 1, review round 3 - the
        docstring used to say the opposite; Should fix 4 of review round 2 is what
        changed it): `auto_place_for_products` calls this once before its loop and
        keeps the dict updated in memory as each row's own answer lands
        (`_release_own_contribution`), rather than re-querying it as each row goes,
        which would have cost one GROUP BY per row over a pass that names thousands.
        `exclude_row_id` is this row's own OLD suggestions - about to be replaced, not
        a claim against itself, exactly as `credit_own_links` already excludes a row's
        own real links from the same netting for the manual dialog.

        Joined to `OrderInquiryRow` and filtered to `_open_for_buying_clauses` (review
        round 2 Blocking 4): a row that has gone cancelled, actioned, rejected or
        redirected to pool never again nets capacity from other rows through a
        suggestion nobody is ever going to act on.
        """
        totals: Dict[str, Decimal] = {}
        po_query = (
            self.db.query(
                OrderInquirySuggestedLink.po_line_id, func.sum(OrderInquirySuggestedLink.qty)
            )
            .join(OrderInquiryRow, OrderInquiryRow.id == OrderInquirySuggestedLink.row_id)
            .filter(
                OrderInquirySuggestedLink.po_line_id.isnot(None),
                *self._open_for_buying_clauses(),
            )
        )
        spo_query = (
            self.db.query(
                OrderInquirySuggestedLink.spo_allocation_id,
                func.sum(OrderInquirySuggestedLink.qty),
            )
            .join(OrderInquiryRow, OrderInquiryRow.id == OrderInquirySuggestedLink.row_id)
            .filter(
                OrderInquirySuggestedLink.spo_allocation_id.isnot(None),
                *self._open_for_buying_clauses(),
            )
        )
        if exclude_row_id:
            po_query = po_query.filter(OrderInquirySuggestedLink.row_id != exclude_row_id)
            spo_query = spo_query.filter(OrderInquirySuggestedLink.row_id != exclude_row_id)
        for target_id, qty in po_query.group_by(OrderInquirySuggestedLink.po_line_id).all():
            totals[str(target_id)] = totals.get(str(target_id), _ZERO) + _dec(qty)
        for target_id, qty in spo_query.group_by(
            OrderInquirySuggestedLink.spo_allocation_id
        ).all():
            totals[str(target_id)] = totals.get(str(target_id), _ZERO) + _dec(qty)
        return totals

    def _write_suggested_links(
        self,
        row: OrderInquiryRow,
        takes: Sequence[Tuple[Dict[str, Any], Decimal]],
        trigger: str,
        *,
        existing: Optional[Sequence[OrderInquirySuggestedLink]] = None,
    ) -> bool:
        """The cascade walk's OWN terminal write (plan 3.4) - never a real link, never
        `scm.order_link_claim`, nobody's name on it. REPLACES this row's suggested
        links with today's answer, unless the answer is unchanged: `_same_placement`
        (S4 of the draft-links plan) already compares a multiset of (target, qty), and
        an `OrderInquirySuggestedLink` carries the same `po_line_id` / `spo_
        allocation_id` / `qty` attributes a real link does, so it is reused as-is
        rather than writing a second comparison (AC-LT-16).

        Writes NOTHING onto the row itself - no note, no `actioned_by`, no state
        change: `po_ref` / `spo_ref` / `state` stay exactly what they were before this
        pass (AC-LT-10), because a guess is not a placement.

        Returns whether the row's suggested links actually changed (R18,
        `PLAN-oi-links-autocount-truth-24sep.md` 3.6): "Link selected" recalculates
        against AutoCount to catch a stale suggestion, and needs to say how many rows
        it actually moved, distinct from the rows it looked at and left alone.

        `existing` (Should fix 4, review round 2): the row's own suggestions, when the
        caller has already loaded them to net its own shared `_suggested_totals_by_
        target` in memory (`auto_place_for_products`'s own pass) - never fetched a
        second time in that case. Omitted (every other caller), this fetches them
        itself exactly as before.
        """
        existing = self._suggested_of_row(row.id) if existing is None else existing
        if existing and self._same_placement(existing, takes):
            return False
        if existing:
            for suggestion in existing:
                self.db.delete(suggestion)
            self.db.flush()
        now = datetime.utcnow()
        for candidate, qty in takes:
            self.db.add(
                OrderInquirySuggestedLink(
                    company_id=row.company_id,
                    row_id=row.id,
                    po_line_id=candidate["po_line_id"],
                    spo_allocation_id=candidate["spo_allocation_id"],
                    document=candidate["document"],
                    qty=qty,
                    trigger=trigger,
                    suggested_at=now,
                )
            )
        self.db.flush()
        return True

    def _drop_suggested_links(self, rows: Sequence[OrderInquiryRow]) -> None:
        """Delete every suggested link on these rows (AC-LT-17/18): full real coverage,
        or a state no longer open for buying - cancelled, actioned, rejected,
        redirected to pool. Plural and explicit, not folded silently into a bigger
        method, because Link selected (S4) calls it on a ticked batch and the state
        writers each call it on their own single-row list.
        """
        wanted = [row.id for row in rows if row is not None]
        if not wanted:
            return
        self.db.query(OrderInquirySuggestedLink).filter(
            OrderInquirySuggestedLink.row_id.in_(wanted)
        ).delete(synchronize_session=False)
        self.db.flush()

    def _trim_suggested_links_to_room(
        self, candidate: Dict[str, Any], room: Decimal
    ) -> None:
        """AC-LT-15 (G2, "a real link always wins"): once a real link lands on this
        target, shrink what is left of the suggested links sitting on it - lowest
        priority first, latest `delivery_date` on the suggestion's OWN row, then
        newest `suggested_at` - until they fit `room`. Never touches a real link and
        never refuses one: `room` is what the real link left behind, AFTER it, so
        this only ever removes or shrinks a guess.
        """
        po_line_id = candidate.get("po_line_id")
        spo_allocation_id = candidate.get("spo_allocation_id")
        if not po_line_id and not spo_allocation_id:
            return
        column = (
            OrderInquirySuggestedLink.po_line_id
            if po_line_id
            else OrderInquirySuggestedLink.spo_allocation_id
        )
        target_id = po_line_id or spo_allocation_id
        # Review round 2 Blocking 4: the same `_open_for_buying_clauses` filter as
        # `_suggested_totals_by_target` - a suggestion on a row no longer open for
        # buying is not real demand for this room and must not inflate `over` (which
        # would otherwise shrink or delete an OPEN row's own suggestion to make space
        # for a real link that never needed it).
        suggestions = (
            self.db.query(OrderInquirySuggestedLink)
            .join(OrderInquiryRow, OrderInquiryRow.id == OrderInquirySuggestedLink.row_id)
            .filter(column == target_id, *self._open_for_buying_clauses())
            .order_by(
                OrderInquiryRow.delivery_date.desc().nullslast(),
                OrderInquirySuggestedLink.suggested_at.desc(),
            )
            .all()
        )
        if not suggestions:
            return
        over = sum((_dec(s.qty) for s in suggestions), _ZERO) - room
        if over <= _ZERO:
            return
        for suggestion in suggestions:
            if over <= _ZERO:
                break
            qty = _dec(suggestion.qty)
            if qty <= over:
                self.db.delete(suggestion)
                over -= qty
            else:
                suggestion.qty = qty - over
                over = _ZERO
        self.db.flush()

    def place_supply_borrow(
        self,
        row: OrderInquiryRow,
        *,
        supply_key: str,
        qty: Decimal,
        actor_user_id: str,
    ) -> None:
        """Link an ORDER_BACK row to the document ladder v7.1 step 3 gave it (PLAN 3.3).

        The board's Confirm, not a buyer's click, so it takes the row rather than a row id
        and the document by the ASSIGNMENT's own key (`spo:<allocation id>` /
        `po:<line id>`) - the same address the component carries, so there is no second
        lookup and no chance of naming a different line of the same document.

        Everything else is `place_on_po_allocations`' own walk, and deliberately: the
        candidate read, the remaining-quantity test, the ORDER-BACK-only rule for an SPO
        allocation, the claim, the note stamp and the row's link state are all one
        implementation. What is NOT reused is the AUTOMATIC reading of that walk
        (`manual=False`), which applies ladder v4's group-deficit rule: it refuses a
        purchase-order line at a group that cannot cover its own backlog, and a group in
        deficit is the ordinary case for the very unit this step is borrowing for. The
        engine has already decided; this is the write.
        """
        kind, target = parse_supply_key(supply_key)
        if not target:
            return
        self.place_on_po_allocations(
            str(row.id),
            [
                {
                    "spo_allocation_id": target if kind == SA_KIND_SPO else None,
                    "po_line_id": target if kind == SA_KIND_PO else None,
                    "qty": qty,
                }
            ],
            actor_user_id=actor_user_id,
        )

    def release_supply_borrow(
        self, *, supply_key: str, core_line_id: str, qty: Decimal
    ) -> Decimal:
        """Take DOWN a line's placements on one document, up to `qty`, and say how much
        came down (PLAN 3.3's middle clause).

        The donor is giving up what it was holding, so the placement that held it has to go
        - a link left standing would keep the document reserved for an order the board has
        just decided is waiting for a replacement instead, and `_candidates_for_row` would
        then refuse the asker's own link on the grounds that the document is fully claimed.

        LATEST FIRST, and reduced rather than always deleted: a donor holding a document
        through two placements gives up the newest one first, and a placement bigger than
        what is being borrowed keeps its remainder. A link of zero is not a smaller
        placement, it is a row that should not exist, so it is deleted - through
        `_remove_links` like every other unlink in this service, because the audit CLAIM
        the link wrote goes with it. Deleted by hand it stayed behind, naming a document
        the donor no longer holds.
        """
        left = _dec(qty)
        if left <= _ZERO:
            return _ZERO
        rows = self._supply_borrow_links(supply_key, core_line_id=core_line_id)
        released = _ZERO
        touched: List[OrderInquiryRow] = []
        going: Dict[str, Tuple[OrderInquiryRow, List[OrderInquiryLink]]] = {}
        for link, row in rows:
            if left <= _ZERO:
                break
            take = min(left, _dec(link.qty))
            if take <= _ZERO:
                continue
            if take >= _dec(link.qty):
                going.setdefault(str(row.id), (row, []))[1].append(link)
            else:
                link.qty = _dec(link.qty) - take
            left -= take
            released += take
            touched.append(row)
        for row, links in going.values():
            self._remove_links(row, links)
        if released > _ZERO:
            self.db.flush()
            self._invalidate_link_cache()
            self.refresh_link_state(touched)
            self.db.flush()
        return released

    def supply_borrow_held_qty(self, supply_key: str, core_line_id: str) -> Decimal:
        """How much of one document a line holds through LIVE placements of its own.

        The quantity `release_supply_borrow` is about to hand back, read before it does -
        which is what tells the confirmation whether the donor's own row is about to
        re-raise the shortfall by itself (`_borrow_shortfalls`). Same query, so the answer
        and the action cannot disagree.
        """
        return sum(
            (
                _dec(link.qty)
                for link, _row in self._supply_borrow_links(
                    supply_key, core_line_id=core_line_id
                )
            ),
            _ZERO,
        )

    def _supply_borrow_links(
        self, supply_key: str, *, core_line_id: str
    ) -> List[Tuple[OrderInquiryLink, OrderInquiryRow]]:
        """One line's LIVE placements on one document, newest first, with their rows.

        `supply_key` is parsed HERE and nowhere else on this side (`spo:<allocation id>` /
        `po:<purchase order line id>`, the assignment's own address).
        """
        kind, target = parse_supply_key(supply_key)
        if not target:
            return []
        column = (
            OrderInquiryLink.spo_allocation_id
            if kind == SA_KIND_SPO
            else OrderInquiryLink.po_line_id
        )
        return (
            self.db.query(OrderInquiryLink, OrderInquiryRow)
            .join(OrderInquiryRow, OrderInquiryRow.id == OrderInquiryLink.row_id)
            .join(
                ProjectSalesOrderLine,
                ProjectSalesOrderLine.id == OrderInquiryRow.so_line_id,
            )
            .filter(
                column == target,
                ProjectSalesOrderLine.core_sales_order_line_id == core_line_id,
                OrderInquiryRow.state != INQUIRY_CANCELLED,
            )
            .order_by(OrderInquiryLink.linked_at.desc())
            .all()
        )

    def retire_supply_borrow_rows(
        self,
        project_sales_order_id: str,
        *,
        reason: str,
        line_ids: Optional[Sequence[str]] = None,
        except_decision_id: Optional[str] = None,
    ) -> int:
        """Cancel this order's step-3 placement rows, links and all (PLAN 3.3).

        A step-3 row belongs to the DECISION that raised it: it is not an instruction to buy
        anything, it is the record that a line's quantity is coming off a named document, and
        the placement link hanging off it is what holds that document. So when the line
        leaves the revision - undecided through `uncover_lines`, superseded by a material
        change, challenged by drift, or re-decided by a later revision of the same order -
        the row goes and the document is free for whoever needs it next. Left standing, it
        pinned the document forever to an instruction that no longer exists.

        Told apart from every other `ORDER_BACK` row by `covered_by`, which is what that
        column has always meant here: a step-3 row NAMES what covers it, and a donor hole
        names nothing because nothing covers it yet (`_raise_borrow_shortfalls`).

        `line_ids` scopes it to the lines that moved; `except_decision_id` spares the rows
        the confirmation now running has just written. The links come down through
        `_remove_links`, so their claims go with them.
        """
        inquiry = self._existing(project_sales_order_id, None)
        if inquiry is None:
            return 0
        query = self.db.query(OrderInquiryRow).filter(
            OrderInquiryRow.order_inquiry_id == inquiry.id,
            OrderInquiryRow.verb == IV_ORDER_BACK,
            OrderInquiryRow.covered_by.isnot(None),
            OrderInquiryRow.state != INQUIRY_CANCELLED,
        )
        if line_ids is not None:
            wanted = sorted({str(line_id) for line_id in line_ids})
            if not wanted:
                return 0
            query = query.filter(OrderInquiryRow.so_line_id.in_(wanted))
        if except_decision_id is not None:
            query = query.filter(
                or_(
                    OrderInquiryRow.supply_decision_id.is_(None),
                    OrderInquiryRow.supply_decision_id != except_decision_id,
                )
            )
        rows = query.all()
        for row in rows:
            links = self._links_of(row.id)
            if links:
                self._remove_links(row, links)
            row.state = INQUIRY_CANCELLED
            row.note = f"{row.note}; {reason}" if row.note else reason
        if rows:
            self.db.flush()
        # Review round 2 Blocking 4 (AC-LT-18): same rule as every other cancel writer
        # in this file - a row this call cancels must not go on holding capacity.
        self._drop_suggested_links(rows)
        return len(rows)

    def place_on_po(
        self, row_id: str, po_line_id: str, *, actor_user_id: str
    ) -> Dict[str, Any]:
        """Link this row to ONE open PO line, for its whole unlinked remainder.

        The single-target shape the feature shipped with, kept: a person who names one line
        that covers the row is not asked to compose an allocation.
        """
        row = self._row_or_404(row_id)
        # THE ROW, not a list. `place_on_po_allocations` answers with a list because it
        # once split the row into several; this one has always answered with the single
        # row a caller named, and changing that quietly would have handed every existing
        # caller a list where it indexes a dict.
        written = self.place_on_po_allocations(
            row_id,
            [{"po_line_id": po_line_id, "qty": self._unlinked_need(row)}],
            actor_user_id=actor_user_id,
        )
        return written[0]

    def place_on_po_allocations(
        self,
        row_id: str,
        allocations: Sequence[Dict[str, Any]],
        *,
        actor_user_id: str,
        auto_trigger: Optional[str] = None,
        full_set: bool = False,
        offered_line_ids: Optional[Sequence[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Link this row across one or more document lines, in one call.

        **The row is never split** (AC-I6). It keeps its full quantity and gains one link
        per allocation; what the allocations do not cover stays demand and the row reads
        partly linked. `sum(links) <= row.qty` is held here, and `qty > 0` per link is held
        by the table's own CHECK.

        Refuses, in the words the buyer needs: a line that is no longer open, a line for a
        different product, a document named by a row whose verb is not linkable at all
        (R5, 27 August: every linkable verb may name either book, SPO first then PO), an
        allocation bigger than what the line has left, and a total bigger than what the
        row still needs.

        `full_set=True` is S8's SET semantics, the "Choose document" dialog's own write:
        the submitted allocations become the row's ENTIRE link set, so a line the row held
        before that is missing from this call is retired, not merely uncounted. See
        `_place_on_po_set`. `offered_line_ids` (S8 review round, 17 Sep) is the candidate
        ids the CALLER actually rendered before this press - a line the row holds that
        is missing from the submission AND absent from this list was never shown to the
        caller at all, and survives rather than being read as a deliberate drop. `None`
        (every API caller before this round) keeps retiring every omitted line, unchanged.
        """
        row = self._row_or_404(row_id)
        self._assert_linkable(row)
        if full_set:
            return self._place_on_po_set(
                row,
                allocations,
                actor_user_id=actor_user_id,
                offered_line_ids=offered_line_ids,
            )
        if not allocations:
            raise AppException(
                status_code=422,
                message="Name at least one document line.",
                code="order_inquiry_no_allocations",
            )
        product_id = self._resolve_product_id(row)
        if not product_id:
            raise AppException(
                status_code=409,
                message="This row names no product to match a purchase order line against.",
                code="order_inquiry_no_product",
            )

        need = self._unlinked_need(row)
        manual = auto_trigger is None
        by_target = {
            candidate["target_id"]: candidate
            for candidate in self._candidates_for_row(row, manual=manual)
        }
        # AC-6.5's "manual override stays" (B3, review of PR #490). A PERSON naming a
        # dedicated line is measured against what the line ACTUALLY has left -
        # `raw_remaining`, net of real LINKS - never against the dedication-reduced
        # `remaining` the automatic walk uses. The dialog greys such a line and still
        # offers it (AC-6.5), and G12's whole answer for an unattributed project bin is
        # "link it manually" (AC-6.9) - but the check refused exactly those links with a
        # 409, so the override the design promised could not be taken. What another
        # order's CLAIM reserves is guidance for the automatic pass; a buyer overriding it
        # deliberately is the escape hatch, and it is audited like any other placement.
        capacity_field = "raw_remaining" if manual else "remaining"

        resolved: List[Tuple[Dict[str, Any], Decimal]] = []
        taken_within_call: Dict[str, Decimal] = {}
        total = _ZERO
        for entry in allocations:
            spo_allocation_id = str(entry.get("spo_allocation_id") or "") or None
            po_line_id = str(entry.get("po_line_id") or "") or None
            qty = _dec(entry.get("qty"))
            if qty <= _ZERO:
                continue
            if spo_allocation_id and row.verb not in _SPO_LINKABLE_VERBS:
                # R5 (27 August) widened `_SPO_LINKABLE_VERBS` to every linkable verb, so
                # `_assert_linkable` already refuses an unlinkable one before this branch
                # is ever reached - kept as a second guard, not a narrower rule.
                raise AppException(
                    status_code=409,
                    message=(
                        "This row's instruction cannot be linked to a document - it is "
                        "not one of the linkable verbs."
                    ),
                    code="order_inquiry_spo_not_linkable",
                )
            target_id = spo_allocation_id or po_line_id
            if not target_id:
                raise AppException(
                    status_code=422,
                    message="Each line must name a purchase order line or an SPO allocation.",
                    code="order_inquiry_no_target",
                )
            candidate = by_target.get(target_id)
            if candidate is None:
                # Not a candidate, and WHY matters: "that line is for a different product"
                # and "that line is closed" send a person to different places, and one
                # message covering both sends them to neither.
                self._refuse_absent_target(
                    po_line_id=po_line_id, spo_allocation_id=spo_allocation_id,
                    product_id=product_id,
                )
            left = candidate[capacity_field] - taken_within_call.get(target_id, _ZERO)
            if left < qty:
                raise AppException(
                    status_code=409,
                    message=(
                        f"{candidate['document'] or 'That line'} has {_qty_str(left)} "
                        f"left, {_qty_str(qty - left)} short of the {_qty_str(qty)} "
                        "allocated to it."
                    ),
                    code="order_inquiry_po_line_short",
                )
            taken_within_call[target_id] = taken_within_call.get(target_id, _ZERO) + qty
            resolved.append((candidate, qty))
            total += qty

        if not resolved:
            raise AppException(
                status_code=422,
                message="Name at least one document line.",
                code="order_inquiry_no_allocations",
            )
        if total > need:
            raise AppException(
                status_code=409,
                message=(
                    f"{_qty_str(total)} allocated is more than the {_qty_str(need)} "
                    "this row still needs."
                ),
                code="order_inquiry_over_allocated",
            )

        # AC-LT-15 (G2): a real link always wins over a suggestion on the SAME target -
        # `_write_link` itself trims it (fix round, 24 Sep), the one choke point every
        # real-link writer reaches, book included.
        for candidate, qty in resolved:
            self._write_link(
                row, candidate, qty, actor_user_id=actor_user_id, auto_trigger=auto_trigger
            )

        self.refresh_link_state([row])
        self.db.flush()
        self._refresh_inquiry_states({row.order_inquiry_id})
        return self.serialize_rows([row])

    def _place_on_po_set(
        self,
        row: OrderInquiryRow,
        allocations: Sequence[Dict[str, Any]],
        *,
        actor_user_id: str,
        offered_line_ids: Optional[Sequence[str]] = None,
    ) -> List[Dict[str, Any]]:
        """S8's one-press re-link (AC-CF-25): the submitted allocations ARE the row's
        link set afterwards.

        A line left out of the submission is RETIRED, through the same `_remove_links`
        `unplace` uses - audit note and claim release included - but ONLY when
        `offered_line_ids` names it too (S8 review round, 17 Sep): a line this row
        holds that the CALLER never even saw (missing from both the submission and what
        it says it rendered) is left exactly as it stands rather than read as a silent
        drop. `offered_line_ids=None` (every caller before this round, and any that
        still omits it) keeps retiring every line missing from the submission, same as
        always. A line submitted with a different qty than it already holds is ADJUSTED
        in place (the link row's own `qty` is changed directly, with its own audit
        stamp): no "Unlinked from X; Linked to X" churn on a line the buyer never
        actually let go of, which is what "nothing else on the row changes" (AC-CF-25)
        means for a line whose take only moved by a number. A line the row held NOTHING
        on before is a fresh link, written the usual way. The caller
        (`place_on_po_allocations(full_set=True)`) has already checked `_assert_linkable`.
        """
        if not allocations:
            raise AppException(
                status_code=422,
                message="Name at least one document line.",
                code="order_inquiry_no_allocations",
            )
        product_id = self._resolve_product_id(row)
        if not product_id:
            raise AppException(
                status_code=409,
                message="This row names no product to match a purchase order line against.",
                code="order_inquiry_no_product",
            )

        existing = {
            str(link.po_line_id or link.spo_allocation_id): link
            for link in self._links_of(row.id)
        }

        # Accumulate by target: `{po_line_id | spo_allocation_id, qty}` entries the FE
        # never repeats today, but two entries naming the same line is a total, not a
        # second placement.
        resolved: Dict[str, Dict[str, Any]] = {}
        for entry in allocations:
            spo_allocation_id = str(entry.get("spo_allocation_id") or "") or None
            po_line_id = str(entry.get("po_line_id") or "") or None
            qty = _dec(entry.get("qty"))
            if qty <= _ZERO:
                continue
            if spo_allocation_id and row.verb not in _SPO_LINKABLE_VERBS:
                raise AppException(
                    status_code=409,
                    message=(
                        "This row's instruction cannot be linked to a document - it is "
                        "not one of the linkable verbs."
                    ),
                    code="order_inquiry_spo_not_linkable",
                )
            target_id = spo_allocation_id or po_line_id
            if not target_id:
                raise AppException(
                    status_code=422,
                    message="Each line must name a purchase order line or an SPO allocation.",
                    code="order_inquiry_no_target",
                )
            slot = resolved.setdefault(
                target_id,
                {
                    "po_line_id": po_line_id,
                    "spo_allocation_id": spo_allocation_id,
                    "qty": _ZERO,
                },
            )
            slot["qty"] = slot["qty"] + qty

        if not resolved:
            raise AppException(
                status_code=422,
                message="Name at least one document line.",
                code="order_inquiry_no_allocations",
            )

        capacity = _dec(row.qty) - _dec(row.bundled_qty)
        total = sum((slot["qty"] for slot in resolved.values()), _ZERO)
        if total > capacity:
            raise AppException(
                status_code=409,
                message=(
                    f"{_qty_str(total)} allocated is more than the {_qty_str(capacity)} "
                    "this row can hold."
                ),
                code="order_inquiry_over_allocated",
            )

        # Manual override, credited: a line THIS row already sits on is measured as if
        # that take were free (S8's whole point - re-place it without first unlinking),
        # and a person naming a line by hand may still reach one a redeal walk would not
        # (`manual=True`, the same override `place_on_po_allocations`'s ADD path grants).
        # `_candidates_including_own_closed_links` (not the bare `_candidates_for_row`):
        # a line closed, or its PO taken off active/partial, since this row's link was
        # written is still THIS row's own line to resubmit or lower - without it,
        # re-submitting the unchanged take on such a line 409'd as "not a candidate"
        # before this call ever reached the retire/adjust logic below (S8 review round).
        by_target = {
            candidate["target_id"]: candidate
            for candidate in self._candidates_including_own_closed_links(row, manual=True)
        }
        for target_id, slot in resolved.items():
            candidate = by_target.get(target_id)
            if candidate is None:
                self._refuse_absent_target(
                    po_line_id=slot["po_line_id"],
                    spo_allocation_id=slot["spo_allocation_id"],
                    product_id=product_id,
                )
                continue
            if slot["qty"] > candidate["raw_remaining"]:
                raise AppException(
                    status_code=409,
                    message=(
                        f"{candidate['document'] or 'That line'} has "
                        f"{_qty_str(candidate['raw_remaining'])} left, "
                        f"{_qty_str(slot['qty'] - candidate['raw_remaining'])} short of "
                        f"the {_qty_str(slot['qty'])} allocated to it."
                    ),
                    code="order_inquiry_po_line_short",
                )

        offered_ids = set(offered_line_ids) if offered_line_ids is not None else None
        to_retire = [
            link
            for target_id, link in existing.items()
            if target_id not in resolved
            and (offered_ids is None or target_id in offered_ids)
        ]
        if to_retire:
            self._remove_links(row, to_retire)

        for target_id, slot in resolved.items():
            link = existing.get(target_id)
            if link is not None:
                if _dec(link.qty) != slot["qty"]:
                    # ADJUSTED in place - its own audit stamp, the same as a fresh link
                    # or a retirement leaves, so a re-deal that only moved a NUMBER on a
                    # line the buyer never let go of is not the one branch of the three
                    # that left no trail (S8 review round, 17 Sep).
                    candidate = by_target.get(target_id)
                    document = candidate["document"] if candidate else link.document
                    stamp = (
                        f"Adjusted {document or 'an unnamed document'} from "
                        f"{_qty_str(link.qty)} to {_qty_str(slot['qty'])}"
                    )
                    row.note = f"{row.note}; {stamp}" if row.note else stamp
                    link.qty = slot["qty"]
                continue
            candidate = by_target[target_id]
            self._write_link(row, candidate, slot["qty"], actor_user_id=actor_user_id)

        self.refresh_link_state([row])
        self.db.flush()
        self._refresh_inquiry_states({row.order_inquiry_id})
        return self.serialize_rows([row])

    def _refuse_absent_target(
        self,
        *,
        po_line_id: Optional[str],
        spo_allocation_id: Optional[str],
        product_id: str,
    ) -> None:
        """Say WHICH of the four reasons a named line is not a candidate.

        Only reached when the candidate walk did not offer it, so the row is always one of:
        gone, someone else's product, closed, or fully claimed. Each sends a person
        somewhere different, and one message covering all four sends them nowhere.
        """
        if spo_allocation_id:
            allocation = (
                self.db.query(SPOAllocation)
                .filter(SPOAllocation.id == spo_allocation_id)
                .first()
            )
            if allocation is None:
                raise AppException(
                    status_code=404,
                    message="That SPO allocation no longer exists.",
                    code="po_line_not_found",
                )
            if str(allocation.product_id) != str(product_id):
                raise AppException(
                    status_code=409,
                    message="That SPO allocation is not for this row's product.",
                    code="order_inquiry_product_mismatch",
                )
            raise AppException(
                status_code=409,
                message=(
                    "That SPO allocation is closed, received, or already fully claimed."
                ),
                code="order_inquiry_po_line_closed",
            )

        line = (
            self.db.query(PurchaseOrderLine)
            .filter(PurchaseOrderLine.id == po_line_id)
            .first()
        )
        if line is None:
            raise AppException(
                status_code=404,
                message="That purchase order line no longer exists.",
                code="po_line_not_found",
            )
        if str(line.product_id) != str(product_id):
            raise AppException(
                status_code=409,
                message="That purchase order line is not for this row's product.",
                code="order_inquiry_product_mismatch",
            )
        if line.line_status != "open":
            raise AppException(
                status_code=409,
                message="That purchase order line is no longer open.",
                code="order_inquiry_po_line_closed",
            )
        raise AppException(
            status_code=409,
            message=(
                "That purchase order line has nothing left on it - every unit of it is "
                "already linked to another row."
            ),
            code="order_inquiry_po_line_short",
        )

    def _linked_claims_by_target(
        self, candidates: Sequence[Dict[str, Any]]
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Every OTHER row already linked onto these lines - a candidate's expand.

        Read straight off the links: the link IS the evidence, and reading the rows' own
        `po_line_id` would miss every second link a row holds.
        """
        target_ids = [candidate["target_id"] for candidate in candidates]
        if not target_ids:
            return {}
        rows = (
            self.db.query(OrderInquiryLink, OrderInquiryRow, ProjectSalesOrder)
            .join(OrderInquiryRow, OrderInquiryRow.id == OrderInquiryLink.row_id)
            .join(OrderInquiry, OrderInquiry.id == OrderInquiryRow.order_inquiry_id)
            .join(
                ProjectSalesOrder,
                ProjectSalesOrder.id == OrderInquiry.project_sales_order_id,
            )
            .filter(
                or_(
                    OrderInquiryLink.po_line_id.in_(target_ids),
                    OrderInquiryLink.spo_allocation_id.in_(target_ids),
                )
            )
            .order_by(OrderInquiryLink.linked_at.asc())
            .all()
        )
        out: Dict[str, List[Dict[str, Any]]] = {}
        for link, row, order in rows:
            key = str(link.po_line_id or link.spo_allocation_id)
            out.setdefault(key, []).append(
                {
                    "so_number": order.autocount_doc_no or order.provisional_ref,
                    "item_code": row.item_code,
                    "qty": _qty_str(_dec(link.qty)),
                    "placed_date": link.linked_at,
                }
            )
        return out

    def _narrow_to_products(self, query, product_ids: Sequence[str]):
        """`query`, narrowed to rows that could be FOR one of these products.

        The same two arms `_resolve_product_id` reads a row's product through, in the same
        order: the reconciled project line's `product_id`, and the row's own `item_code`.
        `None` when neither arm names anything, which is the caller's cue that there is
        nothing to do at all - distinct from "no narrowing", which would scan the book.
        """
        wanted = [pid for pid in product_ids if pid]
        if not wanted:
            return None
        so_line_ids = [
            line_id
            for (line_id,) in self.db.query(ProjectSalesOrderLine.id).filter(
                ProjectSalesOrderLine.product_id.in_(wanted)
            )
        ]
        codes = [
            code
            for (code,) in self.db.query(Product.product_code).filter(
                Product.id.in_(wanted)
            )
            if code
        ]
        conditions = []
        if so_line_ids:
            conditions.append(OrderInquiryRow.so_line_id.in_(so_line_ids))
        if codes:
            conditions.append(OrderInquiryRow.item_code.in_(codes))
        if not conditions:
            return None
        return query.filter(or_(*conditions))

    def rows_needed_at(
        self, cells: Sequence[Tuple[str, Optional[str]]]
    ) -> List[str]:
        """Every row `rows_needed_at_by_cell` found, flattened - what the confirm's own
        first cascade pass hands to `auto_place_for_products`, which is product-wide and
        has no use for which cell a row came from."""
        found: List[str] = []
        for row_ids in self.rows_needed_at_by_cell(cells).values():
            found.extend(row_ids)
        return found

    def rows_needed_at_by_cell(
        self, cells: Sequence[Tuple[str, Optional[str]]]
    ) -> Dict[Tuple[str, Optional[str]], List[str]]:
        """The ids of raised rows whose demand sits at these `(product_id, warehouse_id)`
        cells - the rows that SIZED a plan line, in other words - KEYED BY THE CELL.

        Per cell rather than flat because the two callers want different grains. The
        cascade's first pass wants "everybody this order bought for" and flattens it
        (`rows_needed_at`); G12's write-time claim wants "who is this LINE's buy for", and
        one purchase order can carry lines at two bins for two different customers
        (`supply_claim.claim_purchase_order_for_sizing_rows`) - a flat list there would
        claim each line for both.

        `PLAN-scm-purchasing-uat-journey.md` P7. A purchase order confirmed off the plan is
        a buy for particular plan rows, and a plan row is a `(product, location)` cell whose
        Project figure is exactly the un-linked remainder of the inquiry rows this returns.
        Handing those ids to `auto_place_for_products` first is what makes the confirm link
        back to the rows that asked for it, rather than to whichever open row happens to
        have the earliest date somewhere else in the country.

        The "needed at" location is read the way `scm.committed_v` reads it, because that is
        the figure the plan row shows:

          * a row with a supply decision lands at the reconciled core line's warehouse -
            except an ORDER BACK, which lands at the DONOR location its own
            `stock_location` names (the row hangs off the borrowing line, so the core line's
            warehouse would put the hole in a warehouse that never had one);
          * a row with no decision (the CS form's own instructions) lands at the location
            the ROW states, which is the only location it has.

        A cell whose warehouse is None matches a row that resolves to no location at all -
        the same NULL the view emits and no reader joins to.
        """
        wanted = {(str(pid), str(wid) if wid else None) for pid, wid in cells if pid}
        if not wanted:
            return {}
        # Narrowed to the confirmed lines' PRODUCTS before anything is fetched. Without it
        # this walks every raised row in the book to answer a question about one purchase
        # order, which on the live book is thousands of rows and their product lookups.
        query = self.db.query(OrderInquiryRow).filter(
            OrderInquiryRow.state.in_((INQUIRY_RAISED, INQUIRY_PARTLY_LINKED)),
            OrderInquiryRow.verb.in_(_LINKABLE_VERBS),
        )
        query = self._narrow_to_products(query, [pid for pid, _wid in wanted])
        rows = query.all() if query is not None else []
        if not rows:
            return {}
        product_by_row = self._resolve_product_ids_bulk(rows)

        # The core line's fulfilment warehouse, one query for the whole set.
        so_line_ids = [row.so_line_id for row in rows if row.so_line_id]
        core_warehouse: Dict[str, Optional[str]] = {}
        if so_line_ids:
            for psl_id, warehouse_id in (
                self.db.query(ProjectSalesOrderLine.id, SalesOrderLine.warehouse_id)
                .join(
                    SalesOrderLine,
                    SalesOrderLine.id == ProjectSalesOrderLine.core_sales_order_line_id,
                )
                .filter(ProjectSalesOrderLine.id.in_(so_line_ids))
                .all()
            ):
                core_warehouse[str(psl_id)] = str(warehouse_id) if warehouse_id else None

        # The warehouse each stated stock location names, keyed by (COMPANY, code).
        # `warehouses.warehouse_code` is unique per company and not globally, so a bare
        # code-to-id map keeps whichever company the query happened to return last - and
        # this pass would then claim a row for a warehouse of the wrong company, which is
        # the same mis-attribution the company scope exists to prevent.
        codes = {
            (row.stock_location or "").strip()
            for row in rows
            if (row.stock_location or "").strip()
        }
        warehouse_by_code: Dict[Tuple[Optional[str], str], str] = {}
        if codes:
            warehouse_by_code = {
                (str(company_id) if company_id else None, str(code)): str(wid)
                for code, wid, company_id in self.db.query(
                    Warehouse.warehouse_code, Warehouse.id, Warehouse.company_id
                ).filter(Warehouse.warehouse_code.in_(list(codes)))
            }

        out: Dict[Tuple[str, Optional[str]], List[str]] = {}
        for row in rows:
            product_id = product_by_row.get(row.id)
            if not product_id:
                continue
            stated = warehouse_by_code.get((
                str(row.company_id) if row.company_id else None,
                (row.stock_location or "").strip(),
            ))
            if row.supply_decision_id is None:
                warehouse_id = stated
            elif row.verb == IV_ORDER_BACK:
                warehouse_id = stated or core_warehouse.get(str(row.so_line_id or ""))
            else:
                warehouse_id = core_warehouse.get(str(row.so_line_id or ""))
            cell = (str(product_id), warehouse_id)
            if cell in wanted:
                out.setdefault(cell, []).append(str(row.id))
        return out

    def resolve_link_horizon(
        self, link_up_to: Optional[date], link_horizon: Optional[str] = None
    ) -> Optional[date]:
        """The date this pass may link up to: the caller's own, the plan's, or none at all.

        `PLAN-scm-oi-handshake.md` section 11. Three answers, and until S1 of the 27 August
        review only two could be SAID: a caller that named no date got the plan's, and
        there was no way to ask for no horizon. The buyer's own empty date box therefore
        travelled as silence and came back as the plan's date, so once a plan run named a
        horizon the page could not link a far-future row at all.

          `link_horizon="none"` -> no horizon, `link_up_to` ignored.
          `link_horizon="plan"` -> the reorder plan's own (`scm.priority.plan_link_horizon`).
          `link_horizon="date"` -> `link_up_to`, which must be there.
          OMITTED               -> inferred: the date when one is given, the plan when not.

        The inferred arm is what keeps every existing caller - the CS form's own pass, a
        purchase-order confirm, the MCP - meaning exactly what it always did without
        stating anything new.
        """
        mode = (link_horizon or "").strip().lower() or None
        if mode == LINK_HORIZON_NONE:
            return None
        if mode == LINK_HORIZON_DATE and link_up_to is None:
            raise AppException(
                status_code=422,
                message="Name the date to link up to, or ask for no horizon.",
                code="order_inquiry_horizon_without_a_date",
            )
        if mode != LINK_HORIZON_PLAN and link_up_to is not None:
            return link_up_to
        from app.services.scm import priority

        return priority.plan_link_horizon(self.db)

    @staticmethod
    def _horizon_mode(link_up_to: Optional[date]) -> str:
        """What the RESULT says about the horizon it ran under (S1). `"none"` is "nothing
        was held back for a date", which a null `link_up_to` alone could not tell from "the
        plan has never named one"."""
        return LINK_HORIZON_NONE if link_up_to is None else LINK_HORIZON_DATE

    @staticmethod
    def _after_horizon(row: OrderInquiryRow, link_up_to: Optional[date]) -> bool:
        """Is this row due beyond the horizon, and therefore not this pass's to link?

        A row with NO delivery date is INSIDE it (AC-LH4): the quantity is still owed,
        nobody has said when, and refusing it a document would leave it unbought for a date
        that was never stated.
        """
        return (
            link_up_to is not None
            and row.delivery_date is not None
            and row.delivery_date > link_up_to
        )

    def auto_place_for_products(
        self,
        product_ids: Optional[Sequence[str]],
        *,
        actor_user_id: str,
        trigger: str,
        row_ids: Optional[Sequence[str]] = None,
        inquiry_id: Optional[str] = None,
        link_up_to: Optional[date] = None,
        link_horizon: Optional[str] = None,
        redeal_drafts: bool = False,
        include_awaiting: bool = False,
        _skip_book_step: bool = False,
        _book_step_may_reoffer: bool = True,
    ) -> Dict[str, Any]:
        """The bulk, idempotent cascade pass (G2 rule 1: "we need to link already at
        first already instead of suggesting and needing the users to click 1 by 1").

        Every RAISED, placeable row of the named products - or of every product with one,
        when `product_ids` is omitted - is cascaded against its own open PO lines,
        HIGHEST FULFILMENT PRIORITY FIRST. AC-H5 says the ranking that decides ANY
        draw-down is the SAME policy, everywhere it applies - so which row claims a
        scarce PO line first is scored through `scm.priority.factors_for_demand_rows`,
        the identical assembly the fulfilment board and the Loading Plan already use,
        rather than a private sort this method grew on its own. That answers the
        captain's 20 Aug question - "do we account for both SO date and delivery date?"
      - with both: `need_by_date` from the row's own `delivery_date`, and
        `document_age` from the sales order's own document date (`published_at`, or
        `created_at` before publish - see `_rank_raised_rows`). With no active
        `PriorityPolicy` the call falls back to `DEFAULT_WEIGHTS` on its own; ties
        (including "this policy weights nothing here") fall back to the original
        ordering (`delivery_date` then `created_at`), so this is a strict refinement of
        the old behaviour, never a different one. A second run places nothing further: a
        row this pass placed (or split) is no longer `raised`, so it drops out of the
        very query that feeds the next run - the idempotence the three triggers all rely
        on. The doors are ACKNOWLEDGE, Link now, a purchase-order confirm and - since
        `PLAN-scm-oi-draft-links.md` R6 - the board's own confirm again. What the board's
        pass writes is a DRAFT, because its rows are `awaiting`, which is why it is safe:
        purchasing still says the word, and now they say it looking at an answer.

        `trigger` is stamped onto every placement it makes (`_apply_placement`'s
        `auto_trigger`), so "why is this placed" is always answerable from the row's own
        note - never a silent placement with nothing to show for it.

        `link_up_to` is the LINK HORIZON (section 11, captain 27 Aug): a row due AFTER it is
        left Not linked and counted on `after_horizon` rather than skipped silently, so a
        2030 order stops eating a purchase order a nearer one needed. Omitted, it is the
        reorder plan's own horizon (`resolve_link_horizon`) - a press that says nothing is
        a press under the horizon the plan planned to. A caller that genuinely wants NO
        horizon says so, with `link_horizon="none"` (S1).

        `include_awaiting` and `redeal_drafts` are the DRAFT half
        (`PLAN-scm-oi-draft-links.md` R1/R2/R6). A link on a row purchasing has not
        confirmed is a draft, so:

        * `include_awaiting` widens the gate below to awaiting rows, which is what lets the
          board's own confirm - and a purchase-order confirm, and Auto link all - find the
          documents up front rather than leaving the page blank until somebody presses
          Confirm. The links it writes read as drafts because their rows are awaiting;
        * `redeal_drafts` lets a draft MOVE, so a nearer document that has arrived since can
          take over. Only drafts: a confirmed row's link is a promise, and no automatic pass
          ever moves it (R2). The take is computed first and the old links come down only
          when there is a better answer to write in their place, so a row the horizon holds
          back, a row whose document has closed, and a row the walk lands on the same
          document again all come out of the pass exactly as they went in (B1/S4).

        Both default to false, so Confirm's own cascade and every existing caller keep the
        acknowledged-only gate they were written under.

        `inquiry_id` (S3, `PLAN-oi-header-list-detail.md`) scopes the WHOLE pass to one
        header's own rows, on top of `row_ids` / `product_ids` when either is also
        given - the OI detail page's gear > Auto link, which must never touch a row of
        another header even when it shares a product with this one.
        """
        link_up_to = self.resolve_link_horizon(link_up_to, link_horizon)
        # A row its drafts cover WHOLLY is `placed`, so a re-deal has to be able to see it:
        # moving a link that is already there is the entire point of the press. Only on a
        # re-deal, so an ordinary pass keeps walking exactly the rows it always did.
        states = (
            (INQUIRY_RAISED, INQUIRY_PARTLY_LINKED, INQUIRY_PLACED)
            if redeal_drafts
            else (INQUIRY_RAISED, INQUIRY_PARTLY_LINKED)
        )
        # PARTLY LINKED rows are in scope too, which is new with the links table: a row
        # the last pass could only half cover is exactly the row a fresh purchase order
        # should finish, and before this it left the query the moment it was touched.
        # ACKNOWLEDGED (or changed since), and AWAITING too when the caller is one of
        # the DRAFT doors (R6) - held in the ONE shared predicate (review round item 3,
        # `_linkable_row_clauses`) rather than restated here, because it is one rule and
        # several doors: Confirm, Link now, a purchase-order confirm and the board's own
        # raise. What a link on an awaiting row MEANS is the whole difference: it is a
        # draft, and Confirm is still the buyer's word. `redirected_to_pool` false
        # (S1/AC-OH-10..12, SO314593): a row released to stock is USED, not owed - its
        # own unlinked remainder is the quantity the fresh row raised after it already
        # replaces, never a fresh cascade's to fill.
        query = self.db.query(OrderInquiryRow).filter(
            *self._linkable_row_clauses(states=states, include_awaiting=include_awaiting)
        )
        if inquiry_id:
            query = query.filter(OrderInquiryRow.order_inquiry_id == inquiry_id)
        if row_ids is not None:
            # The NAMED rows and nothing else. A product scope is right for "this purchase
            # order was just confirmed, who was waiting for this item" and wrong for "this
            # upload raised these instructions": the Order Inquiry Form's rows name items
            # half the company's open orders also name, and one CS spreadsheet must not
            # re-cascade somebody else's instructions. Wins over `product_ids`, which a
            # caller passing both would be asking two different questions with.
            wanted_rows = [row_id for row_id in row_ids if row_id]
            if not wanted_rows:
                return self._nothing_placed(link_up_to)
            query = query.filter(OrderInquiryRow.id.in_(wanted_rows))
        elif product_ids:
            narrowed = self._narrow_to_products(query, product_ids)
            if narrowed is None:
                return self._nothing_placed(link_up_to)
            query = narrowed

        # S1 (`PLAN-oi-follow-book-chain.md`, AC-FB-11/AC-FB-20): the book is asked
        # FIRST, for exactly the rows this pass is about to deal - a closed PO line
        # or a chain-only SPO line is invisible to the candidate walk below (it
        # reads open balance and existing claims, never `from_so_line_ref`), so
        # without this a document AutoCount already named for a row sits unused
        # while the cascade buys it a second one. Every cascade trigger (Confirm,
        # Link now, a purchase-order confirm, the board) goes through this one
        # method, so honouring the book here is honouring it everywhere at once.
        #
        # Grouped by the ROW's own `company_id` (S1 review fix), never the
        # ambient session scope: a scope of `None` (the `X-API-Key` principal,
        # or a batch this pass runs across products of several companies) would
        # otherwise skip the book pass entirely for a multi-company press of the
        # very same button, which is not a case `follow_book_for_rows` itself
        # needs to guess about - it already re-scopes per call.
        #
        # `_skip_book_step` / `_book_step_may_reoffer` (review round item 7,
        # AC-FB-55): private, no other caller sets them. A displaced holder is
        # re-offered to THIS method with `_book_step_may_reoffer=False` -
        # `follow_book_for_rows` still runs for it below (a holder is not
        # exempt from "the book wins" for having been one), but that pass is
        # told not to re-offer a SECOND time, so the chain is at most two real
        # book passes deep, never unbounded. `_skip_book_step` skips the book
        # step outright, for a caller that needs the ordinary cascade only.
        book_linked_rows = 0
        book_linked_row_ids: set = set()
        if not _skip_book_step:
            book_rows_by_company: Dict[str, List[str]] = {}
            for row_id, row_company_id in query.with_entities(
                OrderInquiryRow.id, OrderInquiryRow.company_id
            ).all():
                if row_company_id:
                    book_rows_by_company.setdefault(str(row_company_id), []).append(
                        str(row_id)
                    )
            # AC-LT-37 (G4): how many of these rows the book step ITSELF links for
            # real THIS pass - snapshot which already held a real link before the
            # call, then again after, so the count names only what this press did,
            # never a row that was already book-linked coming in.
            all_book_row_ids = [
                row_id for ids in book_rows_by_company.values() for row_id in ids
            ]
            linked_before = {
                row_id
                for row_id, links in self._links_by_row(all_book_row_ids).items()
                if links
            }
            for book_company_id, book_row_ids in book_rows_by_company.items():
                self.follow_book_for_rows(
                    book_row_ids,
                    trigger=trigger,
                    company_id=book_company_id,
                    actor_user_id=actor_user_id,
                    _may_reoffer=_book_step_may_reoffer,
                )
            linked_after = {
                row_id
                for row_id, links in self._links_by_row(all_book_row_ids).items()
                if links
            }
            book_linked_row_ids = linked_after - linked_before
            book_linked_rows = len(book_linked_row_ids)

        rows = query.all()
        # Self-heal (issue #1215 point 1): a row can read `placed`/`partly_linked` with
        # NONE of the links that state describes - the diagnosis found 17 such rows
        # company wide, their links deleted by a path that bypassed `_remove_links` and
        # never re-derived the state afterwards. This pass already loaded every row the
        # query's own states allow (RAISED/PARTLY_LINKED, or also PLACED when
        # `redeal_drafts` widens it), so re-deriving each one's state from its OWN links
        # here means a row like that reads `raised` again even when the walk below finds
        # no better candidate to link it to - the one case `_write_link`/`_remove_links`
        # below never reaches, because nothing in this pass runs for it. Idempotent: a
        # row whose stored state already agrees with its links is untouched.
        #
        # Should-fix 3 (review of PR #1220): bounded to the SAME predicate the standalone
        # repair script uses (`scripts/repair_oi_stale_link_state.find_stale_rows`) -
        # `placed`/`partly_linked`, zero bundle, zero links - one grouped query over the
        # ids this pass already loaded, rather than `refresh_link_state` (one `_links_of`
        # query plus a `derive_bundles` reload) on every row it walks. On Auto link all /
        # Link now the loaded set is every linkable row company wide, which brought back
        # the per-row N+1 the S6 batching comment below says was removed on purpose. A
        # healthy pass (the ordinary case) finds no stale row and issues no extra query
        # at all.
        if rows:
            stale_row_ids = {
                str(stale_id)
                for (stale_id,) in self.db.query(OrderInquiryRow.id)
                .filter(
                    OrderInquiryRow.id.in_([row.id for row in rows]),
                    OrderInquiryRow.state.in_((INQUIRY_PLACED, INQUIRY_PARTLY_LINKED)),
                    OrderInquiryRow.bundled_qty == 0,
                    ~self.db.query(OrderInquiryLink.id)
                    .filter(OrderInquiryLink.row_id == OrderInquiryRow.id)
                    .exists(),
                )
                .all()
            }
            if stale_row_ids:
                self.refresh_link_state(
                    [row for row in rows if str(row.id) in stale_row_ids]
                )
                self.db.flush()
        rows = self._rank_raised_rows(rows)
        # `_resolve_product_id` costs one or two queries per row (review round 1 nit) -
        # resolved ONCE here and read everywhere else this pass needs it (the netting
        # prime below, S2's lead-time set, and the walk itself), rather than three times
        # over a pass that names thousands.
        product_id_by_row = {row.id: self._resolve_product_id(row) for row in rows}
        # LADDER V4 (section 1d): prime the availability reader ONCE, from every product
        # this pass will ask about. `_netting` rebuilds whenever it meets a product it has
        # not seen, so a loop that met them one at a time would rebuild per row - three
        # queries each, over a growing product list, on a pass that names thousands.
        self._netting(list(product_id_by_row.values()))
        # S2 (`PLAN-oi-cascade-skip-early-arrival.md`): the SAME two lead-time sources and
        # the SAME default the worklist's reallocate/unlink pill reads
        # (`order_inquiry_worklist_service._attach_link_suggestions`), fetched once for
        # the whole pass rather than per row. `ProjectSupplyService` stays a LOCAL import,
        # by the same convention as `_uncover_rejected_lines` above - not because of a
        # cycle with this pass; `DEFAULT_LEAD_TIME_DAYS` has no such cycle and imports at
        # module level.
        from app.services.project_supply_service import ProjectSupplyService

        pass_product_ids = {pid for pid in product_id_by_row.values() if pid}
        lead_times = ProjectSupplyService(self.db).lead_times(pass_product_ids)

        placed_rows = 0
        allocation_count = 0
        after_horizon = 0
        products_touched: set = set()
        changed_suggestion_row_ids: set = set()
        # Should fix 4 (review round 2): ONE full-table aggregate for the WHOLE pass,
        # not one per row - `_suggested_totals_by_target` used to run inside the loop
        # below, so an Auto link all over ~2,000 rows ran ~2,000 GROUP BY queries,
        # quadratic as the table grows. Updated in memory as the loop writes each
        # row's own answer (see `_release_own_contribution` below), never re-queried.
        suggested_totals_by_target = self._suggested_totals_by_target()
        for row in rows:
            product_id = product_id_by_row.get(row.id)
            if not product_id:
                continue
            # G5 guard (`PLAN-oi-links-autocount-truth-24sep.md` 3.4, dated 25 Sep 2026,
            # review round 2 Blocking 3): this pass USED to re-deal a row's own
            # `_cascade_only` real links - dropping them and writing a fresh real link
            # in their place when it found a better candidate. Since S3 the cascade
            # never writes a real link at all, so any `_cascade_only` real link on a
            # row today is a LEGACY link from before S3, and re-dealing it here would
            # delete that real link and replace it with only a suggestion - exactly
            # the blind conversion G5 ruled out ("the owner sees the delta first"),
            # ahead of the S5 script the owner reviews before it touches one of these.
            # So `drafts` is always empty: every real link, book-named or not, stays
            # exactly where it is, and the walk only ever offers a suggestion for the
            # row's own UNLINKED remainder. `redeal_drafts` still widens the row scope
            # above to `placed` rows (a re-deal may still find a placed row worth
            # walking once S5 has run) and `_unplace_drafts`/`_cascade_only` still
            # serve `_retire_uncovered_rows`; plan section 8 removes all three once a
            # query shows zero legacy `_cascade_only` real links left on prod.
            drafts: List[OrderInquiryLink] = []
            need = self._unlinked_need(row)
            if need <= _ZERO:
                continue
            # The horizon, checked on a row that still has something to link and before any
            # candidate is read: a row already covered is not one the buyer left behind, and
            # counting it would put a number on the banner nobody could act on. Review round
            # 3 Blocking 1 asked this branch be decided the same way as the no-candidate ones
            # below; the decision here is NOT to drop - B1's own rule
            # (`test_auto_link_all_keeps_the_draft_of_a_row_that_is_now_past_the_cut_off`)
            # already governs this exact branch: the cut off says "do not deal this row", not
            # "take back what it holds". A row past the horizon is not re-walked at all - no
            # candidate is even read for it - so there is no fresher answer to prefer over
            # what it already has, unlike the no-candidate branches below, which DO walk the
            # row and find nothing left to stand behind the suggestion it is holding.
            #
            # Review round 4 Nit 1: `existing_suggestions` is fetched AFTER this check
            # (moved back from before it, review round 3) - a row past the horizon
            # `continue`s without ever reading it, so fetching it earlier cost one
            # `_suggested_of_row` query per after-horizon row for nothing; this branch
            # never drops a suggestion, so it never needed the fetch in the first place.
            if self._after_horizon(row, link_up_to):
                after_horizon += 1
                continue
            # AC-LT-14/G2, Should fix 4 (review round 2): this row's OWN current
            # suggestions, fetched once and used three ways below - to net them OUT
            # of the shared `suggested_totals_by_target` (so the row never competes
            # against itself), to update that SAME shared total in memory afterward
            # (never a second full-table query), and handed to `_write_suggested_
            # links` so it does not fetch them a second time.
            existing_suggestions = self._suggested_of_row(row.id)
            own_by_target: Dict[str, Decimal] = {}
            for suggestion in existing_suggestions:
                target = str(suggestion.po_line_id or suggestion.spo_allocation_id)
                own_by_target[target] = own_by_target.get(target, _ZERO) + _dec(
                    suggestion.qty
                )

            def _release_own_contribution() -> None:
                """This row is about to hold no suggestion (or a different one) - its
                OLD contribution comes out of the shared total now, so a LATER row
                this same pass sees the room it actually left behind."""
                for target, qty in own_by_target.items():
                    suggested_totals_by_target[target] = (
                        suggested_totals_by_target.get(target, _ZERO) - qty
                    )

            candidates = self._candidates_for_row(row, credit_own_links=bool(drafts))
            if not candidates:
                # Review round 2 Blocking 5 (AC-LT-19): no candidate at all is the
                # honest end of a suggestion, not a reason to leave a stale one
                # standing - the row's own line may have closed since the last pass
                # that offered it (issue #1215 point 3's own defect, back on a guess
                # rather than a real link). A no-op when the row holds none.
                #
                # Review round 4 Should fix 1: this drop is counted in
                # `changed_suggestion_row_ids` too - round 3's B1 fix counted the
                # SAME drop on the empty-takes branch below but missed this older,
                # more common one (a closed line with no alternative), which left
                # Link selected reporting `changed_rows: 0` on the exact case its
                # own toast exists to catch.
                if existing_suggestions:
                    self._drop_suggested_links([row])
                    _release_own_contribution()
                    changed_suggestion_row_ids.add(str(row.id))
                continue
            # S2/S4 (`PLAN-oi-cascade-skip-early-arrival.md`): `_within_window` is the
            # SAME filter the Link dialog's own preview runs (`po_candidates_for_row`),
            # so the walk and the dialog stay one opinion about which candidate this
            # row may take automatically.
            lead_days = lead_times.get(product_id)
            if lead_days is None:
                lead_days = DEFAULT_LEAD_TIME_DAYS
            candidates = self._within_window(row, candidates, lead_days)
            if not candidates:
                # Same reasoning as the empty-candidates branch above: nothing left
                # inside the lead-time window is nothing to keep suggesting.
                # Review round 4 Should fix 1: counted too, same reason as above.
                if existing_suggestions:
                    self._drop_suggested_links([row])
                    _release_own_contribution()
                    changed_suggestion_row_ids.add(str(row.id))
                continue
            # What OTHER rows already suggest on each target, netted from the ONE
            # shared total built before this loop started - this row's own current
            # suggestions (about to be replaced) come out first, exactly what
            # `exclude_row_id` used to give a fresh GROUP BY query for.
            held_by_others = {
                target: qty - own_by_target.get(target, _ZERO)
                for target, qty in suggested_totals_by_target.items()
            }
            takes = self._cascade_take(candidates, need, held_by_others=held_by_others)
            if not takes:
                # Review round 3 Blocking 1 (AC-LT-19's third branch): the ALL-OR-
                # NOTHING gate above can return `[]` on a row that still HAS
                # candidates - the remaining lines just cannot cover `need` in full,
                # or every unit left is already held by other rows' own suggestions.
                # Left alone this kept a stale suggestion pointing at a document
                # that closed since the last pass - the same issue #1215 point 3
                # defect the two no-candidate branches above are already fixed for.
                # The honest outcome is the same: nothing to suggest is nothing
                # suggested, not whatever was offered last time.
                if existing_suggestions:
                    self._drop_suggested_links([row])
                    _release_own_contribution()
                    changed_suggestion_row_ids.add(str(row.id))
                continue
            if drafts and self._same_placement(drafts, takes):
                # The best answer today is the one the row already holds. Deleting and
                # rewriting identical links would move the audit trail and append
                # "Unlinked from X; Re-dealt by <trigger>" to the note on every press
                # (S4), so a buyer pressing Auto link all twice read a row that looked
                # like it had changed document twice and had not moved at all.
                continue
            if drafts:
                self._unplace_drafts([row], trigger=trigger)
            # PLAN-oi-links-autocount-truth-24sep.md 3.4: the walk's own terminal write
            # is a SUGGESTION, never a real link - the book step above (real links,
            # untouched) already had first go at every row in this pass.
            if self._write_suggested_links(
                row, takes, trigger, existing=existing_suggestions
            ):
                changed_suggestion_row_ids.add(str(row.id))
                # In memory (Should fix 4): this row's OLD contribution is gone, its
                # NEW one now holds room against every row still to come this pass.
                _release_own_contribution()
                for candidate, qty in takes:
                    target = candidate["target_id"]
                    suggested_totals_by_target[target] = (
                        suggested_totals_by_target.get(target, _ZERO) + qty
                    )
            # One ROW touched, however many documents it took: the row is never split any
            # more, so counting the rows the call returned would always have said 1.
            placed_rows += 1
            allocation_count += len(takes)
            products_touched.add(str(product_id))

        return {
            "placed_rows": placed_rows,
            "allocations": allocation_count,
            "products_touched": len(products_touched),
            # AC-LT-37 (G4): the book step's own count, real links, distinct from
            # `suggested_rows` below - `placed_rows` above stays as it was for a
            # caller that read it before this slice, and is exactly what the
            # cascade walk suggested this pass (its own terminal write is a
            # suggestion, never a placement, since S3).
            "book_linked_rows": book_linked_rows,
            "suggested_rows": placed_rows,
            # R18 (`PLAN-oi-links-autocount-truth-24sep.md` 3.6): how many rows this
            # pass actually moved - book-linked this pass, or given a DIFFERENT
            # suggestion than the one they held coming in. A row the pass looked at
            # and left exactly as it was (same suggestion, or no candidate at all) is
            # not "changed" - "Link selected" reads this to say whether recalculating
            # against AutoCount caught anything, rather than a blanket re-link.
            "changed_rows": len(book_linked_row_ids | changed_suggestion_row_ids),
            "after_horizon": after_horizon,
            "link_up_to": link_up_to,
            "link_horizon": self._horizon_mode(link_up_to),
        }

    @staticmethod
    def _same_placement(
        links: Sequence[OrderInquiryLink],
        takes: Sequence[Tuple[Dict[str, Any], Decimal]],
    ) -> bool:
        """Would the re-deal write exactly what the row already holds? (S4)

        Compared as a MULTISET of (target, quantity): the walk may return the same
        documents in a different order and that is not a change, while two links of 5 on
        one line are not the same answer as one of 10.
        """
        held = sorted(
            (str(link.spo_allocation_id or link.po_line_id or ""), _dec(link.qty))
            for link in links
        )
        offered = sorted(
            (str(candidate["target_id"]), _dec(qty)) for candidate, qty in takes
        )
        return held == offered

    def _unplace_drafts(self, rows: Sequence[OrderInquiryRow], *, trigger: str) -> None:
        """Take the DRAFT links off these rows so the walk can deal them again (R2).

        A draft is a link nobody has manually made - there is no state on the link itself
        (R1) - so the test is `_only_cascade_links`, not the row's own `ack_state`: linking
        never waits for confirm (`include_awaiting=True` on every cascade door), so
        whichever way a row is born (`PLAN-oi-confirm-per-so.md` S1) that stamp still
        cannot tell a draft from a promise. A row carrying so much as one MANUAL link is
        skipped whole: that link is a promise purchasing made, and an automatic pass that
        moved it would move a commitment nobody was asked about.

        WHY, on the row's note: `_remove_links` already writes "Unlinked from X", which
        says what happened and not why. The trigger says why, so a buyer reading a row that
        changed document overnight finds the press that did it.
        """
        # Batched (S6): one grouped load, read twice (the cascade-only test, then the
        # removal itself) instead of two separate queries per row.
        links_by_row = self._links_by_row([str(row.id) for row in rows])
        touched: List[OrderInquiryRow] = []
        for row in rows:
            links = links_by_row.get(str(row.id), [])
            if not self._cascade_only(links):
                continue
            self._remove_links(row, links)
            stamp = f"Re-dealt by {trigger}"
            row.note = f"{row.note}; {stamp}" if row.note else stamp
            touched.append(row)
        if touched:
            self.refresh_link_state(touched)
            self.db.flush()
            self._refresh_inquiry_states({row.order_inquiry_id for row in touched})

    @staticmethod
    def _nothing_placed(link_up_to: Optional[date]) -> Dict[str, Any]:
        """The pass had nothing to walk. Still states the horizon it was run under, so a
        caller never has to guess which date a zero was measured against."""
        return {
            "placed_rows": 0,
            "allocations": 0,
            "products_touched": 0,
            "book_linked_rows": 0,
            "suggested_rows": 0,
            "changed_rows": 0,
            "after_horizon": 0,
            "link_up_to": link_up_to,
            "link_horizon": ProjectOrderInquiryService._horizon_mode(link_up_to),
        }

    def _rank_raised_rows(
        self, rows: Sequence[OrderInquiryRow]
    ) -> List[OrderInquiryRow]:
        """Highest fulfilment priority first, for `auto_place_for_products` (AC-H5).

        Scores every row through `scm.priority.factors_for_demand_rows` - the same call
        the fulfilment board and the Loading Plan already use - so which RAISED row
        claims a scarce PO line first is decided by the one active `PriorityPolicy`,
        never a second convention this method invents for itself. The mapping per row:

          * `row_key`            <- the row's own id.
          * `required_date`      <- the row's `delivery_date` (`need_by_date`).
          * `order_date`         <- `published_at` or `created_at` before publish. This
            ranking input was never touched by
            `PLAN-oi-handover-so-date-autocount-25sep.md`: `_context_for` and the
            handover email now prefer the core `sales_orders.order_date` for the
            `so_date` a person reads, while `scm/priority.py`'s own docstring
            (`factors_for_demand_rows`) still documents `document_age <-
            sales_orders.order_date` for the score a row is RANKED by - the two have
            drifted apart, and pointing this cascade's `order_date` at the same core
            column is a named follow-up, out of scope for that lane.
          * `demand_class`       <- always `"project"`: every row this cascade sees came
            off a project order inquiry.
          * `payment_terms_days` <- the resolved customer's terms
            (`_customer_ids_for_pso` + `priority.payment_terms_by_customer`) when a
            customer is reachable, `None` otherwise - an unknown is ABSENT, not a
            default.

        `factors_for_demand_rows` resolves the active policy itself (falling back to
        `DEFAULT_WEIGHTS` when none is active), so nothing here hand-rolls a fallback.
        Rows tie on their score - including "this policy weights nothing here" - break
        to the ORIGINAL ordering, `delivery_date` then `created_at`: this is a strict
        refinement of what the cascade did before, not a different rule.

        **Scored per PRODUCT, one contender group at a time** (S5, code review, 20 Aug
        2026): `factors_for_demand_rows`'s own docstring is explicit that it normalizes
        VALUES ACROSS THE ROWS PASSED IN - "the caller passes one cell's contributors,
        not the world" - because a rank only means anything against the rows actually
        competing for the SAME scarce PO lines. `auto_place_for_products` calls this
        with every raised row across EVERY product in one batch; passing the whole batch
        through in one `factors_for_demand_rows` call let an unrelated product's extreme
        `order_date` compress the `document_age` axis for every OTHER product's rows too,
        which could flip which of two genuinely competing rows on the SAME product wins
        the only PO line there is. Grouped by product here instead, each group scored in
        isolation, then the ranked groups concatenated back in the order their product
        first appeared in `rows` - so which PRODUCT is processed first is unchanged, only
        the SCORE within a product no longer depends on demand for a different one.
        """
        if not rows:
            return []

        inquiry_ids = {row.order_inquiry_id for row in rows}
        joined = (
            self.db.query(OrderInquiry.id, ProjectSalesOrder)
            .join(
                ProjectSalesOrder,
                ProjectSalesOrder.id == OrderInquiry.project_sales_order_id,
            )
            .filter(OrderInquiry.id.in_(list(inquiry_ids)))
            .all()
        )
        order_dates: Dict[str, Any] = {}
        pso_by_inquiry: Dict[str, str] = {}
        pso_ids: set = set()
        for inquiry_id, order in joined:
            order_dates[inquiry_id] = order.published_at or order.created_at
            pso_by_inquiry[inquiry_id] = order.id
            pso_ids.add(order.id)

        customer_ids = self._customer_ids_for_pso(pso_ids)
        terms_by_customer = priority.payment_terms_by_customer(
            self.db, [cid for cid in customer_ids.values() if cid]
        )

        # One contender group per product (`None` for a row that resolves to no product
        # at all - it competes with nothing and is filtered out downstream anyway), each
        # group keeping the rows' relative order from `rows` and the groups themselves
        # ordered by each product's first appearance - `auto_place_for_products` still
        # processes products in the same order it always has.
        product_by_row = self._resolve_product_ids_bulk(rows)
        groups: Dict[Optional[str], List[OrderInquiryRow]] = {}
        group_order: List[Optional[str]] = []
        for row in rows:
            key = product_by_row.get(row.id)
            bucket = groups.get(key)
            if bucket is None:
                bucket = []
                groups[key] = bucket
                group_order.append(key)
            bucket.append(row)

        def _sort_key(row: OrderInquiryRow, scores: Dict[str, float]) -> Tuple[float, date, datetime]:
            return (
                -scores.get(row.id, 0.0),
                row.delivery_date or date.max,
                row.created_at or datetime.max,
            )

        # S5 (code review, 20 Aug 2026): hoisted OUT of the per-product loop below.
        # `factors_for_demand_rows` resolves `active_policy(db)` itself whenever `weights`/
        # `class_weights` is left None - an uncached query - so leaving it unset here issued
        # one identical query PER PRODUCT GROUP (300 products, 300 queries) for a policy row
        # that cannot change mid-call. Resolved once and passed explicitly into every group;
        # the grouping/scoring semantics are unchanged, only the resolution moved.
        weights, class_weights = priority.policy_weights(priority.active_policy(self.db))

        ranked: List[OrderInquiryRow] = []
        for key in group_order:
            group_rows = groups[key]
            demand_rows = []
            for row in group_rows:
                pso_id = pso_by_inquiry.get(row.order_inquiry_id)
                customer_id = customer_ids.get(pso_id) if pso_id else None
                demand_rows.append(
                    {
                        "row_key": row.id,
                        "required_date": row.delivery_date,
                        "order_date": order_dates.get(row.order_inquiry_id),
                        "payment_terms_days": (
                            terms_by_customer.get(customer_id) if customer_id else None
                        ),
                        "demand_class": "project",
                    }
                )
            factors_by_row = priority.factors_for_demand_rows(
                self.db, demand_rows, weights=weights, class_weights=class_weights
            )
            scores = priority.scores_for(factors_by_row)
            ranked.extend(sorted(group_rows, key=lambda r: _sort_key(r, scores)))

        return ranked

    def _customer_ids_for_pso(self, pso_ids: set) -> Dict[str, Optional[str]]:
        """Customer id per project sales order, the same resolution
        `_project_customer_labels` uses for the display name: the project's billing
        party first (`ProjectParty.customer_id` through the issuing purchase order),
        the CORE sales order's own customer when there is no project party - an ADOPTED
        order has none by design. Cheap on purpose: this only feeds an optional ranking
        factor, so a customer that costs more than one join to reach stays ABSENT.
        """
        if not pso_ids:
            return {}
        rows = (
            self.db.query(
                ProjectSalesOrder.id,
                func.coalesce(ProjectParty.customer_id, SalesOrder.customer_id),
            )
            .outerjoin(
                ProjectPurchaseOrder,
                ProjectPurchaseOrder.id == ProjectSalesOrder.purchase_order_id,
            )
            .outerjoin(ProjectParty, ProjectParty.id == ProjectPurchaseOrder.issuing_party_id)
            .outerjoin(SalesOrder, SalesOrder.id == ProjectSalesOrder.so_id)
            .filter(ProjectSalesOrder.id.in_(list(pso_ids)))
            .all()
        )
        return {pso_id: customer_id for pso_id, customer_id in rows}

    def relink_to_matching_lines(
        self,
        po_ids: Sequence[str],
        *,
        actor_user_id: Optional[str],
        trigger: str,
    ) -> int:
        """Move each placement onto the line of ITS OWN purchase order whose warehouse fits.

        Section 3.G, AC-G3. The occupancy panel exists to show the buyer that a PO line says
        DC1 while the demand is at BRW-BB; acting on that finding means re-keying the split
        in AutoCount and uploading the book again. The book then states BRW-BB 487 + BRW 13,
        and the placements are still sitting on the line that used to be DC1 - so the finding
        the buyer just acted on is still on the screen, and the split reads as if it never
        happened. This is the step that finishes the loop: "keeps every placement attached to
        the line whose warehouse matches; none is orphaned or unplaced".

        Deliberately narrow, in four ways, because a book upload runs over thousands of
        documents and a relocation nobody asked for is worse than no relocation at all:

        * **within ONE purchase order.** A link already names this document; moving it
          between two of the document's own lines re-reads what the buyer restated. Moving it
          to a DIFFERENT document would be buying decision, and that is Link PO's job.
        * **exact location only.** The target line's warehouse must be the row's own
          `stock_location`, not a tier-2 group sibling or a pool. Anything looser would move a
          placement to a line that reads "location differs" just the same, for no gain.
        * **never off a line that already fits.** A row sitting at tier 1 is left exactly
          where it is, or every upload would churn the link audit for nothing.
        * **whole links, never split ones.** A target with less room than the placement needs
          is passed over: half a placement on a line the book has closed is the very state
          this is fixing.

        Capacity is `qty_ordered` less what OTHER links already claim, not `outstanding`. The
        history channel writes its lines closed and fully received, and this is a relocation
        rather than a promise of fresh supply - the quantity was always against this document.
        Open lines are preferred over closed ones, then the earliest expected date.

        Answers how many links moved. Idempotent: a second run finds every placement already
        at tier 1 and moves nothing.
        """
        wanted = [str(po_id) for po_id in (po_ids or []) if po_id]
        if not wanted:
            return 0
        if len(wanted) > _RELINK_BATCH:
            # A purchase-history upload names thousands of documents, and an `IN` list that
            # long is a query plan nobody wants and a parameter list some drivers refuse.
            # Chunked rather than capped: every document the upload touched is still walked.
            moved = 0
            for start in range(0, len(wanted), _RELINK_BATCH):
                moved += self.relink_to_matching_lines(
                    wanted[start:start + _RELINK_BATCH],
                    actor_user_id=actor_user_id,
                    trigger=trigger,
                )
            return moved

        lines = (
            self.db.query(PurchaseOrderLine, Warehouse.warehouse_code)
            .outerjoin(Warehouse, Warehouse.id == PurchaseOrderLine.warehouse_id)
            .filter(PurchaseOrderLine.purchase_order_id.in_(wanted))
            .all()
        )
        if not lines:
            return 0
        location_of = {
            str(line.id): (code or "").strip().upper() for line, code in lines
        }
        by_order: Dict[str, List[Any]] = {}
        for line, code in lines:
            by_order.setdefault(str(line.purchase_order_id), []).append((line, code))

        links = (
            self.db.query(OrderInquiryLink, OrderInquiryRow, PurchaseOrderLine)
            .join(OrderInquiryRow, OrderInquiryRow.id == OrderInquiryLink.row_id)
            .join(PurchaseOrderLine, PurchaseOrderLine.id == OrderInquiryLink.po_line_id)
            .filter(
                OrderInquiryLink.po_line_id.in_(list(location_of)),
                OrderInquiryRow.state != INQUIRY_CANCELLED,
            )
            .order_by(OrderInquiryLink.linked_at.asc(), OrderInquiryLink.id.asc())
            .all()
        )
        if not links:
            return 0

        # What every link claims per line, as this pass sees it - kept in step as links move,
        # so two placements cannot both be given the same 13 units. Tallied off the rows
        # already fetched above rather than fetching them again: the two queries would be
        # the same query, and a second one that drifted from the first is how a line comes
        # to be promised twice.
        claimed: Dict[str, Decimal] = {}
        for link, _row, _line in links:
            key = str(link.po_line_id)
            claimed[key] = claimed.get(key, _ZERO) + _dec(link.qty)

        moved = 0
        touched: List[OrderInquiryRow] = []
        for link, row, current in links:
            wants = (row.stock_location or "").strip().upper()
            if not wants or location_of.get(str(current.id)) == wants:
                continue
            qty = _dec(link.qty)
            candidates = [
                line
                for line, code in by_order.get(str(current.purchase_order_id), [])
                if (code or "").strip().upper() == wants
                and str(line.id) != str(current.id)
                and str(line.product_id) == str(current.product_id)
                and _dec(line.qty_ordered) - claimed.get(str(line.id), _ZERO) >= qty
            ]
            if not candidates:
                continue
            candidates.sort(
                key=lambda line: (
                    0 if line.line_status == "open" else 1,
                    line.expected_date is None,
                    line.expected_date or date.min,
                    str(line.id),
                )
            )
            target = candidates[0]
            claimed[str(current.id)] = claimed.get(str(current.id), _ZERO) - qty
            claimed[str(target.id)] = claimed.get(str(target.id), _ZERO) + qty
            link.po_line_id = str(target.id)
            # The document has not changed - only which of its lines this sits on - so the
            # link's denormalised `document` stays as it is and the claim it put up is still
            # true. What DOES need saying is why the row moved, on the row's own note, which
            # is already this feature's evidence field.
            stamp = (
                f"Moved to the {wants} line of {link.document or 'the same document'} "
                f"after the book was re-uploaded; auto: {trigger}"
            )
            row.note = f"{row.note}; {stamp}" if row.note else stamp
            # `actioned_by` / `actioned_at` are NOT touched. They say who in purchasing
            # dealt with this instruction, and a book upload is not a person dealing with
            # it - stamping the uploader there would erase the buyer who linked it and put
            # a name against work they did not do. The note is where "why did this move"
            # belongs, and it already carries the cascade's own stamp.
            touched.append(row)
            moved += 1

        if moved:
            self.db.flush()
            self._invalidate_link_cache()
            # The derived display (`po_ref` / `po_line_id`) is read off the links, so it has
            # to be restated or the row keeps naming the line it has just left.
            self.refresh_link_state(touched)
            self.db.flush()
        return moved

    def unplace(
        self, row_id: str, *, actor_user_id: str, link_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Unlink. With a `link_id` that ONE link goes; without one every PO/SPO link on
        the row goes, which is what the whole-row action means.

        A partly linked row can therefore give back one of its documents and keep the
        other, which is the point of the child table: before it, "unplace" was the only
        move and it took the whole placement with it.

        **A reserve link is never touched here** (`PLAN-oi-request-cs-reserve.md`
        section 6c, F5 - "unlink is unlink, unreserve is unreserve ... the one doing
        the job different so dangerous if they are the same"). Naming a reserve
        link's own id is refused outright; the whole-row form silently leaves any
        reserve link standing and acts on the PO/SPO links only.
        """
        row = self._row_or_404(row_id)
        links = self._links_of(row.id)
        if link_id:
            links = [link for link in links if str(link.id) == str(link_id)]
            if not links:
                raise AppException(
                    status_code=404,
                    message="That link no longer exists.",
                    code="order_inquiry_link_not_found",
                )
            if links[0].reserve_request_row_id is not None:
                raise AppException(
                    status_code=409,
                    message=(
                        "This is a reserve, not a link - use Unreserve to give it back."
                    ),
                    code="order_inquiry_unlink_reserve_refused",
                )
        else:
            links = [link for link in links if link.reserve_request_row_id is None]
        if not links:
            raise AppException(
                status_code=409,
                message="This row is not linked to anything.",
                code="order_inquiry_not_placed",
            )
        self._remove_links(row, links)
        self.refresh_link_state([row])
        self.db.flush()
        self._refresh_inquiry_states({row.order_inquiry_id})
        return self.serialize_rows([row])[0]

    def unplace_rows(self, row_ids: Sequence[str]) -> int:
        """Bulk unlink by explicit row id - the WRITE half of "Unlink all".

        Which ids are in scope is entirely the caller's job
        (`OrderInquiryWorklistService.unplace_all` resolves them off the worklist's own
        filters, the same `_base()` the list and the summary already read, so the count a
        person confirmed and the rows this actually touches can never disagree); this
        method only ever writes, through the same `_remove_links` a single Unlink uses.

        **No merging problem left to solve.** Before the links table this had to state a
        policy about split rows - "leave each split row raised at its own quantity, do not
        merge siblings back" - because a cascade had turned one instruction into several
        and there was no reliable key that said they had ever been one. A row is never
        split now, so unlinking it simply returns it to the quantity it always had.

        Idempotent: an empty `row_ids`, or a set none of which holds a link (a second click
        after the first already ran), returns 0.

        **Skips reserve links** (`PLAN-oi-request-cs-reserve.md` section 6c, F5): the
        bulk action is PO/SPO unlink, never Unreserve, so a row holding only a reserve
        link is left standing and not counted.
        """
        wanted = [row_id for row_id in (row_ids or []) if row_id]
        if not wanted:
            return 0
        rows = (
            self.db.query(OrderInquiryRow)
            .join(OrderInquiryLink, OrderInquiryLink.row_id == OrderInquiryRow.id)
            .filter(
                OrderInquiryRow.id.in_(wanted),
                OrderInquiryLink.reserve_request_row_id.is_(None),
            )
            .distinct()
            .all()
        )
        for row in rows:
            self._remove_links(
                row,
                [
                    link
                    for link in self._links_of(row.id)
                    if link.reserve_request_row_id is None
                ],
            )
        if rows:
            self.refresh_link_state(rows)
            self.db.flush()
            self._refresh_inquiry_states({row.order_inquiry_id for row in rows})
        return len(rows)

    def _remove_links(
        self, row: OrderInquiryRow, links: Sequence[OrderInquiryLink]
    ) -> None:
        """Delete these links and the audit claim each one put up.

        The claim goes only when THIS link is what wrote it (`source = 'order_inquiry'`,
        held by `order_link_service.delete_own_claim`): a claim at the same identity that
        the PO history import is the source of was never this row's to make, and unlinking
        must not take somebody else's evidence down with it.
        """
        going = {str(link.id) for link in links}
        for link in links:
            document = link.document
            stamp = f"Unlinked from {document}" if document else "Unlinked"
            row.note = f"{row.note}; {stamp}" if row.note else stamp
            # By ID, not by identity. `delete_own_claim` matches on (SO number, PO number,
            # item) and would have taken down the claim behind a SIBLING link on the same
            # document - a row linked to two lines of one purchase order lost both claims
            # when one line was given back. The link records which claim it wrote, so this
            # removes exactly that one and nothing else (S3, review round: the shared
            # guard lives in `order_link_service.free_claim_if_orphaned`; the
            # planning-change re-deal that also called it was retired 29 Sep 2026).
            order_link_service.free_claim_if_orphaned(
                self.db, link.claim_id, excluding=going
            )
            self.db.delete(link)
        self.db.flush()
        self._invalidate_link_cache()
        # WHO acted on this row, and when: still true while ANY link stands. Blanking it
        # on a partial unlink would have said nobody had ever touched a row that is still
        # half covered.
        if not self._links_of(row.id):
            row.actioned_by = None
            row.actioned_at = None
        self.db.flush()

    def _row_or_404(self, row_id: str) -> OrderInquiryRow:
        row = self.db.query(OrderInquiryRow).filter(OrderInquiryRow.id == row_id).first()
        if row is None:
            raise AppException(
                status_code=404,
                message="That order inquiry row no longer exists.",
                code="order_inquiry_row_not_found",
            )
        return row

    def _assert_linkable(self, row: OrderInquiryRow) -> None:
        """Refuse a row that cannot hold a link, in the words the buyer needs.

        A PARTLY LINKED, PLACED or ACTIONED row is linkable too (S8): "Choose document"
        is a one-press re-link on a row that already carries links, not just a first
        placement, so only CANCELLED - a row nobody can point at a document any more -
        is refused.
        """
        if row.verb not in _LINKABLE_VERBS:
            raise AppException(
                status_code=409,
                message=(
                    "Only an ORDER, RESERVE & ORDER or ORDER BACK row can be linked to a "
                    "document."
                ),
                code="order_inquiry_not_placeable_verb",
            )
        if row.state == INQUIRY_CANCELLED:
            raise AppException(
                status_code=409,
                message="A cancelled row cannot be linked to a document.",
                code="order_inquiry_not_raised",
            )

    #: The name this check carried before section 3.I. Same call.
    _assert_placeable = _assert_linkable

    def _resolve_product_id(self, row: OrderInquiryRow) -> Optional[str]:
        """The product this row is FOR: the reconciled line's product first, the item
        code second. Never invented when neither resolves."""
        if row.so_line_id:
            line = (
                self.db.query(ProjectSalesOrderLine.product_id)
                .filter(ProjectSalesOrderLine.id == row.so_line_id)
                .first()
            )
            if line and line[0]:
                return line[0]
        if row.item_code:
            product = (
                self.db.query(Product.id)
                .filter(Product.product_code == row.item_code)
                .first()
            )
            if product:
                return product[0]
        return None

    def _resolve_product_ids_bulk(
        self, rows: Sequence[OrderInquiryRow]
    ) -> Dict[str, Optional[str]]:
        """Row id -> product id, the SAME precedence as `_resolve_product_id` (the
        reconciled line's product first, the item code second), but one query per source
        rather than one per row - the batch version a listing needs."""
        so_line_ids = {row.so_line_id for row in rows if row.so_line_id}
        item_codes = {row.item_code for row in rows if row.item_code}
        line_products = (
            dict(
                self.db.query(
                    ProjectSalesOrderLine.id, ProjectSalesOrderLine.product_id
                )
                .filter(ProjectSalesOrderLine.id.in_(list(so_line_ids)))
                .all()
            )
            if so_line_ids
            else {}
        )
        code_products = (
            dict(
                self.db.query(Product.product_code, Product.id)
                .filter(Product.product_code.in_(list(item_codes)))
                .all()
            )
            if item_codes
            else {}
        )
        out: Dict[str, Optional[str]] = {}
        for row in rows:
            product_id = line_products.get(row.so_line_id) if row.so_line_id else None
            if not product_id and row.item_code:
                product_id = code_products.get(row.item_code)
            out[row.id] = product_id
        return out

    def link_candidate_products(
        self, product_ids: Sequence[Optional[str]]
    ) -> Dict[str, set]:
        """Which of these products still has something to link to, by KIND.

        The exact predicate `po_candidates_for_row` answers per row, computed ONCE for a
        whole listing, so the row action's offer and the dialog can never disagree.

        Two sets because the two books are asked different questions, NOT because the
        verb narrows which book answers: since R5 (27 August,
        `PLAN-scm-oi-draft-links.md`) EVERY linkable verb - ORDER, RESERVE & ORDER and
        ORDER BACK alike - may name either a purchase order line or an
        `spo_allocations` row, SPO first and then PO (`_SPO_LINKABLE_VERBS` equals
        `_LINKABLE_VERBS`). The 25 August rule this docstring used to state - that a
        shipping order answers an ORDER BACK alone - is retired, and the sets are kept
        apart only so a caller can ask "is there an open PO line" and "is there open
        incoming" separately.

        SPO- prefixed PURCHASE orders are excluded from the `po` set as they are in the
        walk: since migration 420 a shipping order is an `spo_allocations` row, and one
        still sitting in `purchase_orders` is a document nobody migrated rather than a
        candidate. The `spo` set applies `spo_supply.open_incoming_clauses`, the one copy
        of "what counts as incoming", so this flag and rung 1 cannot come to disagree.
        """
        wanted = {pid for pid in product_ids if pid}
        if not wanted:
            return {"po": set(), "spo": set()}
        by_po, by_spo = self._linked_by_target()

        # LADDER V4 (section 1d): the flag applies the SAME group-deficit rule the walk
        # does, so a row is never offered a Link that the dialog would then show as empty.
        # Per (product, group), because a product can be flush in one group and 15,514
        # short in another, and a pool-location line belongs to no group and always counts.
        # ZERO IS OFFERED and a group holding an acknowledged unlinked row is offered
        # whatever its arithmetic (captain, 27 Aug 2026) - both halves of the ruling
        # `_groups_in_deficit` and `_candidates_for_row` carry, or the row action would
        # hide the Link the dialog is about to fill. The row exemption is answered per
        # PRODUCT here, which is all a listing-wide flag has to go on: it cannot tell the
        # exempt group's own row from a neighbour's, so it errs towards offering and the
        # dialog stays the exact answer.
        po_open: Dict[str, Dict[Optional[str], Decimal]] = {}
        for line_id, product_id, qty_ordered, qty_received, warehouse_code in (
            self.db.query(
                PurchaseOrderLine.id,
                PurchaseOrderLine.product_id,
                PurchaseOrderLine.qty_ordered,
                PurchaseOrderLine.qty_received,
                Warehouse.warehouse_code,
            )
            .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderLine.purchase_order_id)
            .outerjoin(Warehouse, Warehouse.id == PurchaseOrderLine.warehouse_id)
            .filter(
                PurchaseOrderLine.product_id.in_(list(wanted)),
                PurchaseOrderLine.line_status == "open",
                PurchaseOrder.status.in_(("active", "partial")),
                PurchaseOrder.po_number.notlike("SPO-%"),
            )
            .all()
        ):
            remaining = (
                _dec(qty_ordered) - _dec(qty_received) - by_po.get(str(line_id), _ZERO)
            )
            if remaining <= _ZERO:
                continue
            group = group_of_warehouse_code(warehouse_code)
            per_group = po_open.setdefault(str(product_id), {})
            per_group[group] = per_group.get(group, _ZERO) + remaining

        po_products: set = set()
        if po_open:
            netting = self._netting(list(po_open))
            for product_id, per_group in po_open.items():
                awaiting = self._groups_awaiting_a_link(product_id)
                for group, remaining in per_group.items():
                    if (
                        group is None
                        or group in awaiting
                        or netting.group_net(product_id, group).net + remaining >= _ZERO
                    ):
                        po_products.add(product_id)
                        break

        spo_products: set = set()
        pools = self._pool_codes()
        for allocation_id, product_id, allocated, received, warehouse_code in (
            self.db.query(
                SPOAllocation.id,
                SPOAllocation.product_id,
                SPOAllocation.allocated_quantity,
                SPOAllocation.quantity_received,
                Warehouse.warehouse_code,
            )
            .outerjoin(
                InboundShipment, InboundShipment.id == SPOAllocation.inbound_shipment_id
            )
            .outerjoin(Warehouse, Warehouse.id == SPOAllocation.warehouse_id)
            .filter(
                SPOAllocation.product_id.in_(list(wanted)),
                SPOAllocation.spo_number.isnot(None),
                *spo_supply.open_incoming_clauses(),
            )
            .all()
        ):
            # The POOL rule the walk applies (R11), applied here too, or the flag would
            # offer a Link the dialog then shows as empty.
            if str(warehouse_code or "").strip().upper() not in pools:
                continue
            remaining = (
                _dec(allocated) - _dec(received) - by_spo.get(str(allocation_id), _ZERO)
            )
            if remaining > _ZERO:
                spo_products.add(str(product_id))

        return {"po": po_products, "spo": spo_products}

    @staticmethod
    def has_link_candidate(
        verb: Optional[str], product_id: Optional[str], candidates: Dict[str, set]
    ) -> bool:
        """Does THIS row have anywhere to link to? Verb and product together.

        Stated once, so the per-project list, the cross-project worklist and anything else
        that prints the flag cannot each decide it differently.
        """
        if not product_id:
            return False
        if product_id in candidates.get("po", ()):
            return True
        return verb in _SPO_LINKABLE_VERBS and product_id in candidates.get("spo", ())

    def claim_identity(
        self, row: OrderInquiryRow
    ) -> Tuple[str, Optional[str], Optional[str]]:
        """The (so_number, item_code, core so_line_id) the audit claim is written and
        matched on - the same identity `order_link_service.resolve()` already reads."""
        inquiry = (
            self.db.query(OrderInquiry).filter(OrderInquiry.id == row.order_inquiry_id).first()
        )
        order = (
            self.db.query(ProjectSalesOrder)
            .filter(ProjectSalesOrder.id == inquiry.project_sales_order_id)
            .first()
            if inquiry is not None
            else None
        )
        so_number = (
            (order.autocount_doc_no or order.provisional_ref)
            if order is not None
            else str(row.order_inquiry_id)
        )
        core_line_id = None
        if row.so_line_id:
            line = (
                self.db.query(ProjectSalesOrderLine.core_sales_order_line_id)
                .filter(ProjectSalesOrderLine.id == row.so_line_id)
                .first()
            )
            core_line_id = line[0] if line else None
        return so_number, row.item_code, core_line_id

    # ---------------------------------------------------------------- export

    def export_xlsx(
        self,
        project_id: str,
        *,
        query: Optional[str] = None,
        verb: Optional[Sequence[str]] = None,
        state: Optional[Sequence[str]] = None,
        pso_id: Optional[str] = None,
    ) -> Tuple[str, bytes]:
        """The same rows, as the spreadsheet purchasing already reads (AC-I5).

        Generated on demand rather than stored, for the same reason the AutoCount import
        file is: a stored file goes stale the moment an amendment publishes, and a stale
        instruction is exactly what this slice exists to stop being emailed around.
        """
        import openpyxl

        rows = self.all_rows(project_id, query=query, verb=verb, state=state, pso_id=pso_id)
        serialized = self.serialize_rows(rows)

        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.title = EXPORT_SHEET
        sheet.append([EXPORT_TITLE])
        sheet.append(list(EXPORT_HEADINGS))
        for row in serialized:
            sheet.append(
                [
                    self._as_naive(row.get("so_date")),
                    row.get("sales_order_ref") or "",
                    row.get("item_code") or "",
                    float(_dec(row.get("qty"))),
                    row.get("delivery_date"),
                    row.get("project_customer") or "",
                    # Empty rather than a guess when no allocation is confirmed.
                    row.get("stock_location") or "",
                    row.get("remark") or "",
                ]
            )
        buffer = io.BytesIO()
        workbook.save(buffer)
        project = self.db.query(Project).filter(Project.id == project_id).first()
        stem = (project.project_code if project else "project") or "project"
        filename = f"order-inquiry-{stem}-{date.today().isoformat()}.xlsx"
        return filename, buffer.getvalue()

    def _as_naive(self, value: Any) -> Any:
        if isinstance(value, datetime):
            return value.replace(tzinfo=None)
        return value

    # --------------------------------------------------------------- helpers

    def _order_or_404(self, pso_id: str) -> ProjectSalesOrder:
        order = (
            self.db.query(ProjectSalesOrder).filter(ProjectSalesOrder.id == pso_id).first()
        )
        if order is None:
            raise AppException(
                status_code=404, message="Sales order not found.", code="so_not_found"
            )
        return order

    def _lines_of(self, pso_id: str) -> List[ProjectSalesOrderLine]:
        return (
            self.db.query(ProjectSalesOrderLine)
            .filter(ProjectSalesOrderLine.project_sales_order_id == pso_id)
            .order_by(ProjectSalesOrderLine.line_no.asc())
            .all()
        )

    def _line_or_none(self, line_id: Optional[str]) -> Optional[ProjectSalesOrderLine]:
        if not line_id:
            return None
        return (
            self.db.query(ProjectSalesOrderLine)
            .filter(ProjectSalesOrderLine.id == line_id)
            .first()
        )

    def _product_code(self, product_id: Optional[str]) -> str:
        if not product_id:
            return ""
        row = self.db.query(Product.product_code).filter(Product.id == product_id).first()
        return row[0] if row else ""


def confirmed_unplaced_buy_rows(
    db: Session,
    *,
    product_id: Optional[str] = None,
    warehouse_id: Optional[str] = None,
) -> List[OrderInquiryRow]:
    """Confirmed, still-unplaced Project Buy - the one thing SCM reads (AC-D04).

    Counts the current `raised` ORDER rows of ACTIVE decisions DIRECTLY. No re-netting
    against pre-order or inbound pools, and no subtracting customer deliveries a second
    time: CS already decided what still has to be bought, and repeating that arithmetic
    downstream is how the same requirement gets bought twice or vanishes entirely.

    The join to core stock facts runs through
    `projects.sales_order_lines.core_sales_order_line_id` (front planning section 4),
    never through a reference, a document number or an item code.
    """
    from app.models.project_so import DECISION_ACTIVE, SOSupplyDecision

    query = (
        db.query(OrderInquiryRow)
        .join(
            SOSupplyDecision, SOSupplyDecision.id == OrderInquiryRow.supply_decision_id
        )
        .join(
            ProjectSalesOrderLine,
            ProjectSalesOrderLine.id == OrderInquiryRow.so_line_id,
        )
        .join(
            SalesOrderLine,
            SalesOrderLine.id == ProjectSalesOrderLine.core_sales_order_line_id,
        )
        .filter(
            SOSupplyDecision.state == DECISION_ACTIVE,
            OrderInquiryRow.verb == IV_ORDER,
            OrderInquiryRow.state == INQUIRY_RAISED,
        )
    )
    if product_id:
        query = query.filter(SalesOrderLine.product_id == product_id)
    if warehouse_id:
        query = query.filter(SalesOrderLine.warehouse_id == warehouse_id)
    return query.all()


def _handover_sort_key(item: Dict[str, Any]) -> Tuple[str, bool, int, str]:
    """AC-2/AC-3 (24 Sep, owner ruling): S/O no, then the AutoCount SO line sequence
    ascending with a row that names no line (`line_no is None`) sorted LAST within its
    own S/O, then item code - the order CS ticked lines in, or whether a line is the
    confirm's own or a carried-forward amendment row, has no effect."""
    line_no = item.get("line_no")
    return (
        item.get("so_number") or "",
        line_no is None,
        line_no if line_no is not None else 0,
        item.get("item_code") or "",
    )


def _build_handover_context(
    pending: Sequence[Dict[str, Any]],
    attachments_by_line: Optional[Dict[str, List[Dict[str, Any]]]] = None,
) -> Optional[Tuple[Dict[str, Any], str]]:
    """The `order_inquiry_handover` dispatch context (AC-H17) plus the `source_id` to
    dispatch it under - PURE aggregation over what `_record_handover` already resolved
    and formatted eagerly (see its own docstring for why: a fresh drain-time session
    cannot see a write that is still open under a savepoint). `None` on an empty queue.

    AC-2/AC-3: the LINE TABLE is built off a copy sorted by `_handover_sort_key` -
    a still-raised amendment row `_append_still_raised_amendment_rows` appends to this
    same queue is not a special case, it sorts exactly like every other item. The SO
    summary table (`orders`) is separately re-sorted by S/O no after it is built, so
    the two tables agree on which order comes first (reviewer nit) - everything ELSE
    below (`so_numbers`/location aggregation for the subject, `first_inquiry_id`)
    stays over `pending` in QUEUE order, unchanged by this lane: AC-4 pins the subject
    rule as-is.

    #1312: `attachments_by_line` (`so_line_attachments.handover_attachments`'s own
    shape, keyed by `core_line_id`) is optional and defaults to `None` so the several
    existing one-arg callers keep working unchanged. Each queued line dict is COPIED
    before anything is added to it - the caller's own queued item must never be
    mutated, since a later drain of the SAME session's queue (a second commit within
    one request) could otherwise see files printed twice. Files are walked in the
    SAME sorted (email) order the line table itself uses, then in upload order within
    a line, with a running total against `HANDOVER_ATTACHMENT_CAP_BYTES` (AC-E4): a
    file that would push the total over the cap is not attached, only named in its
    row as a link to the order inquiry line.
    """
    if not pending:
        return None

    from app.services.automation_triggers import build_order_inquiry_link

    attachments_by_line = attachments_by_line or {}
    email_attachments: List[Dict[str, Any]] = []
    running_total = 0
    # fix round 1 (should-fix 6): the SAME core line queued twice in one write (two
    # OI rows raised against one SO line) must attach its files ONCE, not once per
    # row - keyed by `core_line_id`, decided on the FIRST row that names it and
    # reused verbatim by every later one, so two rows can never disagree about
    # which of a line's files made it into the email (both still PRINT the names).
    printed_by_core_line: Dict[str, List[Dict[str, Any]]] = {}

    lines: List[Dict[str, Any]] = []
    for item in sorted(pending, key=_handover_sort_key):
        line = dict(item["line"])
        core_line_id = item.get("core_line_id")
        files = attachments_by_line.get(core_line_id) if core_line_id else None
        if files:
            if core_line_id in printed_by_core_line:
                printed = printed_by_core_line[core_line_id]
            else:
                so_number = item.get("so_number") or ""
                line_no = item.get("line_no")
                printed = []
                for entry in files:
                    name = f"{so_number}-L{line_no}-{entry['filename']}"
                    size = entry.get("size_bytes") or 0
                    # fix round 1 (should-fix 5): only counted toward the running
                    # total when it IS attached - a file too big to fit must not
                    # also sink every SMALLER file queued after it (10 MB, 10 MB,
                    # 1 MB attaches the first and third, not just the first).
                    if running_total + size <= HANDOVER_ATTACHMENT_CAP_BYTES:
                        running_total += size
                        printed.append({"name": name, "attached": True, "url": None})
                        email_attachments.append(
                            {
                                "filename": name,
                                "storage_provider": entry["storage_provider"],
                                "storage_key": entry["storage_key"],
                                "optional": True,
                            }
                        )
                    else:
                        url = (
                            f"{build_order_inquiry_link(item.get('order_inquiry_id'))}"
                            f"?row={item.get('row_id')}"
                        )
                        printed.append({"name": name, "attached": False, "url": url})
                printed_by_core_line[core_line_id] = printed
            line["attachments"] = printed
        lines.append(line)

    orders: List[Dict[str, Any]] = []
    seen_pso: set = set()
    so_numbers: List[str] = []
    seen_so: set = set()
    locations: set = set()
    verb_keys: set = set()
    # S3, AC-LK-01: the FIRST header this batch's own lines named - `_record_handover`
    # already queues `order_inquiry_id` on every item, so nothing new has to be looked
    # up here. A handover naming several orders still points the email at one header,
    # the same way `so_numbers[0]` already picked one SO to lead the subject with.
    first_inquiry_id: Optional[str] = None

    for item in pending:
        pso_id = item.get("pso_id")
        so_number = item.get("so_number")
        if first_inquiry_id is None and item.get("order_inquiry_id"):
            first_inquiry_id = item["order_inquiry_id"]

        if pso_id not in seen_pso:
            seen_pso.add(pso_id)
            orders.append(
                {
                    "so_number": so_number,
                    "customer": item.get("customer"),
                    "project": item.get("project"),
                }
            )
        if so_number and so_number not in seen_so:
            seen_so.add(so_number)
            so_numbers.append(so_number)
        # AC-R2-14/15 (S3, `PLAN-scm-oi-handover-r2-undo.md`): a BLANK location is
        # ignored outright rather than counted as its own distinct value - a blank
        # used to sit in this set as `None` and made "one named location, some blank"
        # read as two locations (mixed) instead of one, so a mostly-local order's
        # subject went bare when it should have named the one warehouse that mattered.
        location = (item.get("stock_location") or "").strip()
        if location:
            locations.add(location)
        verb_keys.update(item.get("verb_keys") or ())

    # Reviewer nit: the SO summary table reads by S/O no, the same primary key the
    # line table below is sorted by - built in queue order above (`seen_pso` still
    # dedups on first sight), then re-sorted here so the two tables never disagree
    # about which S/O comes first. The SUBJECT's own `so_numbers` stays in queue
    # order (AC-4 pins its rule as-is; it only ever joins them with " , ", so their
    # order is not user-visible the way two tables printed one under the other is).
    orders = sorted(orders, key=lambda order: order.get("so_number") or "")

    # AC-H7 / AC-R2-14/15: one NAMED location (blanks ignored) -> "<location> @ <so
    # list>"; two or more named, or none at all, -> bare "<so list>".
    so_list = " , ".join(so_numbers)
    if len(locations) == 1:
        subject_scope = f"{next(iter(locations))} @ {so_list}"
    else:
        subject_scope = so_list

    verbs = [
        _HANDOVER_VERB_LABEL[key] for key in _HANDOVER_VERB_ORDER if key in verb_keys
    ]

    from app.services.certificate_service import today_malaysia

    context = {
        "handover": {
            "subject_scope": subject_scope,
            "verbs": verbs,
            "headline": ", ".join(verbs),
            "orders": orders,
            "lines": lines,
            "line_count": len(lines),
            "link": build_order_inquiry_link(first_inquiry_id),
        },
        "actor": pending[0].get("actor"),
        # Asia/Kuala_Lumpur, not the server's own local time (nit, review round 1) -
        # the seeded automation's own timezone, and the one every other date-stamped
        # outbound email in this codebase already reads off (`certificate_service.
        # today_malaysia`). dd/mm/yyyy (AC-H14, review round 2), like every other date
        # the template prints - the "Raised by ... on <date>" line is not the one place
        # this email reverts to ISO.
        "today": today_malaysia().strftime("%d/%m/%Y"),
    }
    if email_attachments:
        # AC-E5: a write whose lines hold no files sends exactly today's email - no
        # key at all, not an empty list, so a template/automation hop that merely
        # checks presence never treats this write as attachment-bearing.
        context["email_attachments"] = email_attachments
    return context, pending[0]["order_inquiry_id"]


def _build_undo_context(
    pending: Sequence[Dict[str, Any]]
) -> Optional[Tuple[Dict[str, Any], str]]:
    """The `order_inquiry_undone` dispatch context plus the `source_id` to dispatch it
    under - PURE aggregation over what `_record_undo` already resolved and formatted
    eagerly, the same shape `_build_handover_context` above is. One undo commits one
    decision at a time, so there is exactly one entry to read (`pending[0]`), unlike
    the handover queue's own many-lines-per-commit shape.
    """
    if not pending:
        return None
    item = pending[0]
    context = {
        "undo": {
            "so_number": item.get("so_number"),
            "customer": item.get("customer"),
            "project": item.get("project"),
            "revision_no": item.get("revision_no"),
            "lines": item.get("lines"),
            "headline": item.get("headline"),
            "link": item.get("link"),
        },
        "actor": item.get("actor"),
        "today": date.today().strftime("%d/%m/%Y"),
    }
    return context, item["decision_id"]


_POST_COMMIT_DISPATCH_REGISTERED = False


def register_order_inquiry_post_commit_dispatch() -> None:
    """Fire every `order_inquiry_changed_with_links` a settle queued, once this session's
    write actually commits (S5, review of PR #471).

    Mirrors `product_spec_write.register_spec_write_backstop`: `_dispatch_changed_with_links`
    cannot dispatch synchronously (see its own docstring - `AutomationService` commits
    internally, which would release every SAVEPOINT above it, not just its own), so it
    queues the fully-built context on `Session.info` instead and this module-level
    listener drains the queue after a REAL commit. `after_soft_rollback` discards the
    queue on any rollback (including a savepoint's, which is what every test in this
    suite exercises) - a write that never landed has nothing to report.

    Idempotent and called once at startup, the same as every other global session
    listener this app registers (`register_audit_listeners`, the embedding listeners,
    the spec backstop above).
    """
    global _POST_COMMIT_DISPATCH_REGISTERED
    if _POST_COMMIT_DISPATCH_REGISTERED:
        return

    @event.listens_for(Session, "after_commit")
    def _fire_pending_changed_with_links(session):  # noqa: ANN001
        # A Preview runs the real write and rolls it back: its queues must survive every
        # savepoint commit inside it and be discarded by the root rollback, never sent.
        if session.info.get("confirm_preview"):
            return
        pending = session.info.pop(_CHANGED_WITH_LINKS_PENDING_KEY, None)
        if not pending:
            return
        # A FRESH session, not this one (S5): `session` just committed and, under
        # `join_transaction_mode="create_savepoint"` (every test in this suite),
        # `after_commit` fires before the session has re-begun a usable transaction of
        # its own - a query here raises "Can't operate on closed transaction". A
        # dispatch is real, independent work (`AutomationService` commits its own
        # notification/email-outbox rows), so it earns its own connection the same way
        # the embedding pipeline hands its own work to a separate consumer rather than
        # keep running on the producer's session.
        from app.database import SessionLocal
        from app.services.automation_service import AutomationService

        fresh = SessionLocal()
        try:
            for item in pending:
                try:
                    AutomationService(fresh).dispatch_event(
                        "order_inquiry_changed_with_links",
                        context=item["context"],
                        source_kind="order_inquiry_row",
                        source_id=item["source_id"],
                    )
                except Exception:  # noqa: BLE001 - a post-commit side effect never raises
                    fresh.rollback()
                    logger.exception(
                        "Automation dispatch(order_inquiry_changed_with_links) failed"
                        " for row %s",
                        item.get("source_id"),
                    )
        finally:
            fresh.close()

    @event.listens_for(Session, "after_commit")
    def _fire_pending_purchasing_notifications(session):  # noqa: ANN001
        """Tell purchasing an inquiry was raised, once the write it is about has landed.

        Queued by `_notify_purchasing` rather than sent there, because the notification
        service commits and this runs inside somebody's savepoint. A FRESH session for the
        same reason the dispatch above takes one: `after_commit` fires before this session
        has re-begun a usable transaction.
        """
        if session.info.get("confirm_preview"):
            return
        pending = session.info.pop(_PURCHASING_NOTIFY_PENDING_KEY, None)
        if not pending:
            return
        from app.database import SessionLocal
        from app.services.notification_service import NotificationService

        fresh = SessionLocal()
        try:
            for item in pending:
                for user_id in item["user_ids"]:
                    try:
                        NotificationService(fresh).create_with_channel_preferences(
                            user_id=user_id,
                            type="project_order_inquiry_raised",
                            title=item["title"],
                            body=item["body"],
                            data=item["data"],
                            source_entity_type="order_inquiry",
                            source_entity_id=item["inquiry_id"],
                            dedup_key=f"{item['inquiry_id']}:order_inquiry_raised",
                            event_type="project_order_inquiry_raised",
                            send_in_app=True,
                            # Deliberately not email. AC-I4 is that this stops being an
                            # email: the task is the record, and a mailbox is what it
                            # replaces.
                            send_email=False,
                        )
                    except Exception:  # noqa: BLE001 - post-commit work never raises
                        fresh.rollback()
                        logger.exception(
                            "order inquiry %s raised, but purchasing was not notified",
                            item.get("inquiry_id"),
                        )
        finally:
            fresh.close()

    @event.listens_for(Session, "after_commit")
    def _mark_handover_transaction_committed(session):  # noqa: ANN001
        """Record that the ROOT transaction just committed, for `_fire_pending_handover`
        below to tell a genuine root commit apart from a rollback (AC-H27/AC-H28,
        review round 2 ruling: the drain fires ONLY at the root's own conclusion, never
        at any savepoint release - see that listener's own docstring for why).

        `after_commit` fires on EVERY `SessionTransaction.commit()`, nested or not
        (`self._parent is None or self.nested`, straight from SQLAlchemy's own source),
        so this fires just as much for `_hand_to_purchasing`'s own `db.begin_nested()`
        and for `planning_change_service.apply`'s one-savepoint-per-order as it does for
        the write's real outer commit. `get_nested_transaction()` is how the two are
        told apart: `SessionTransaction.close()` (which would clear it) runs AFTER
        `after_commit` dispatches, so at the moment THIS fires, `get_nested_transaction()`
        still answers with whichever savepoint is currently committing, if any - a
        NESTED commit (savepoint or `_hand_to_purchasing`'s own, at any depth) always
        sees a non-None answer here, and only the session's own root-level `commit()`
        (called only once every savepoint below it has already closed) sees None.

        Skips the append entirely when nothing is pending on this session (AC-H24): every
        OTHER commit anywhere in the app also fires this listener (`after_commit` is a
        `Session`-wide event, not scoped to order-inquiry work), and a long-running import
        session making thousands of unrelated commits must not grow this list once per
        commit forever. The transaction object itself is kept (not its `id()`) so nothing
        here can be confused by CPython reusing a freed object's address for an unrelated
        later transaction.
        """
        if not session.info.get(_HANDOVER_PENDING_KEY):
            return
        if session.get_nested_transaction() is not None:
            # A savepoint's OWN commit fired this, not the root's - do nothing except
            # leave the queue exactly as it is (AC-H27/AC-H28).
            return
        session.info.setdefault(_HANDOVER_COMMITTED_TX_KEY, []).append(
            session.get_transaction()
        )

    @event.listens_for(Session, "after_transaction_end")
    def _fire_pending_handover(session, transaction):  # noqa: ANN001
        """Fire the `order_inquiry_handover` parallel-run email once the ROOT
        transaction has genuinely CONCLUDED BY COMMIT (AC-H1, AC-H15, AC-H27, AC-H28).

        Not `after_commit`: see `_mark_handover_transaction_committed` above for why a
        plain commit event cannot by itself tell "a savepoint just released, more of
        this write may follow" from "the write itself just landed". `after_transaction_end`
        gives the CONCLUDED transaction directly (fired from `SessionTransaction.close()`,
        by which point `session._transaction` has already moved to its parent).

        Review round 2 ruling (AC-H27/AC-H28): `planning_change_service.apply` gives
        EACH ORDER its own savepoint, so matching on "the transaction active when the
        line was recorded" (round 1's fix) fired once PER SAVEPOINT - one email per
        order instead of R2's one email per WRITE, and could dispatch for rows a later
        PARENT rollback still had a chance to remove. So this drains ONLY when
        `transaction` is the ROOT (`transaction.parent is None`) - every line recorded
        under any savepoint of this write (`_record_handover` tags each with the
        OUTERMOST entry of its own `_transaction_chain`, not the innermost) waits for
        THAT SAME root object to conclude, however many savepoints came and went above
        it. A savepoint release itself does nothing here at all - the queue is simply
        left as it is, still pending, until the root concludes. An inner savepoint's own
        ROLLBACK is untouched by this listener (its `transaction.parent is not None`
        guard below returns immediately) - `after_soft_rollback`'s C2 tx_chain rule
        further down discards exactly that savepoint's own lines, independently.

        `after_transaction_end` ALSO fires for a ROLLED-BACK transaction (`close()` runs
        on both paths) and, per `SessionTransaction.rollback()`'s own source, runs BEFORE
        `after_soft_rollback` dispatches - so without the commit check below, a root
        rollback would drain and dispatch its own items instead of the discard further
        down ever getting a chance to (AC-H10, AC-H28). `_HANDOVER_COMMITTED_TX_KEY` is
        what that check reads.

        `transaction`'s OWN marker (if it has one) is pruned FIRST, unconditionally, on
        every exit path below (AC-H24) - a transaction ends exactly once, so its marker
        is dead weight the moment this fires, whether or not anything was pending for it.
        """
        if transaction.parent is not None:
            # A savepoint concluding (commit OR rollback) - never this listener's to
            # act on (AC-H27/AC-H28). Nothing was ever marked "committed" for it either
            # (see `_mark_handover_transaction_committed`'s own guard), so there is
            # nothing to prune here.
            return
        committed = session.info.get(_HANDOVER_COMMITTED_TX_KEY)
        was_committed = False
        if committed:
            still_committed = [tx for tx in committed if tx is not transaction]
            was_committed = len(still_committed) != len(committed)
            if still_committed:
                session.info[_HANDOVER_COMMITTED_TX_KEY] = still_committed
            else:
                session.info.pop(_HANDOVER_COMMITTED_TX_KEY, None)

        pending = session.info.get(_HANDOVER_PENDING_KEY)
        if not pending:
            return
        concluded = [item for item in pending if item["tx"] is transaction]
        if not concluded:
            return
        if not was_committed:
            # This transaction ENDED (closed) via rollback, not commit - AC-H10's job,
            # not this listener's; leave the items for `after_soft_rollback` to discard.
            return
        remaining = [item for item in pending if item["tx"] is not transaction]
        if remaining:
            session.info[_HANDOVER_PENDING_KEY] = remaining
        else:
            session.info.pop(_HANDOVER_PENDING_KEY, None)

        from app.database import SessionLocal
        from app.services.automation_service import AutomationService

        fresh = SessionLocal()
        try:
            # #1312: whatever files this write's lines hold, read on the FRESH session
            # (the root has committed, so the uploads a concurrent upload wrote are
            # visible here) - a failure here must never stop the email itself, so it
            # is caught and logged INSIDE this try, separately from the dispatch below:
            # on any error the handover still sends, just without attachments.
            attachments_by_line: Dict[str, List[Dict[str, Any]]] = {}
            core_line_ids = [
                item["core_line_id"] for item in concluded if item.get("core_line_id")
            ]
            if core_line_ids:
                try:
                    from app.services import so_line_attachments

                    attachments_by_line = so_line_attachments.handover_attachments(
                        fresh, core_line_ids
                    )
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "order_inquiry_handover: could not load line attachments; "
                        "sending without files"
                    )

            built = _build_handover_context(concluded, attachments_by_line)
            if built is None:
                return
            context, source_id = built
            AutomationService(fresh).dispatch_event(
                "order_inquiry_handover",
                context=context,
                source_kind="order_inquiry_handover",
                source_id=source_id,
            )
        except Exception:  # noqa: BLE001 - a post-commit side effect never raises
            fresh.rollback()
            logger.exception("Automation dispatch(order_inquiry_handover) failed to queue")
        finally:
            fresh.close()

    @event.listens_for(Session, "after_commit")
    def _mark_undo_transaction_committed(session):  # noqa: ANN001
        """Copied from `_mark_handover_transaction_committed` above, not adapted -
        same reasoning, same rule: tell the ROOT transaction's own commit apart from a
        nested savepoint's, for `_fire_pending_undo` below.
        """
        if not session.info.get(_UNDO_PENDING_KEY):
            return
        if session.get_nested_transaction() is not None:
            return
        session.info.setdefault(_UNDO_COMMITTED_TX_KEY, []).append(
            session.get_transaction()
        )

    @event.listens_for(Session, "after_transaction_end")
    def _fire_pending_undo(session, transaction):  # noqa: ANN001
        """Fire the `order_inquiry_undone` email once the ROOT transaction has
        genuinely CONCLUDED BY COMMIT - copied from `_fire_pending_handover` above,
        not adapted; see that listener's own docstring for the full reasoning
        (`after_transaction_end` vs `after_commit`, why a savepoint's own conclusion
        is not this listener's to act on).
        """
        if transaction.parent is not None:
            return
        committed = session.info.get(_UNDO_COMMITTED_TX_KEY)
        was_committed = False
        if committed:
            still_committed = [tx for tx in committed if tx is not transaction]
            was_committed = len(still_committed) != len(committed)
            if still_committed:
                session.info[_UNDO_COMMITTED_TX_KEY] = still_committed
            else:
                session.info.pop(_UNDO_COMMITTED_TX_KEY, None)

        pending = session.info.get(_UNDO_PENDING_KEY)
        if not pending:
            return
        concluded = [item for item in pending if item["tx"] is transaction]
        if not concluded:
            return
        if not was_committed:
            # This transaction ENDED (closed) via rollback, not commit - leave the
            # items for `after_soft_rollback` to discard.
            return
        remaining = [item for item in pending if item["tx"] is not transaction]
        if remaining:
            session.info[_UNDO_PENDING_KEY] = remaining
        else:
            session.info.pop(_UNDO_PENDING_KEY, None)

        from app.database import SessionLocal
        from app.services.automation_service import AutomationService

        fresh = SessionLocal()
        try:
            built = _build_undo_context(concluded)
            if built is None:
                return
            context, source_id = built
            AutomationService(fresh).dispatch_event(
                "order_inquiry_undone",
                context=context,
                source_kind="order_inquiry_undone",
                source_id=source_id,
            )
        except Exception:  # noqa: BLE001 - a post-commit side effect never raises
            fresh.rollback()
            logger.exception("Automation dispatch(order_inquiry_undone) failed to queue")
        finally:
            fresh.close()

    @event.listens_for(Session, "after_soft_rollback")
    def _discard_pending_changed_with_links(session, previous_transaction):  # noqa: ANN001
        session.info.pop(_CHANGED_WITH_LINKS_PENDING_KEY, None)
        # A write that never landed has nothing to tell purchasing about either - but ONLY
        # that write. This fires on a nested rollback too, and a batch apply gives each
        # order its own savepoint, so popping the whole queue let a failing order discard a
        # sibling's notification (C2, review round). An item goes only when the transaction
        # that just rolled back is one of its own ancestors.
        pending = session.info.get(_PURCHASING_NOTIFY_PENDING_KEY)
        if pending:
            kept = [
                item
                for item in pending
                if not any(tx is previous_transaction for tx in item.get("tx_chain") or ())
            ]
            if kept:
                session.info[_PURCHASING_NOTIFY_PENDING_KEY] = kept
            else:
                session.info.pop(_PURCHASING_NOTIFY_PENDING_KEY, None)

        # Same C2 rule for the handover queue (AC-H10): only the entries earned under the
        # transaction that just rolled back are discarded, never a sibling order's.
        handover_pending = session.info.get(_HANDOVER_PENDING_KEY)
        if handover_pending:
            handover_kept = [
                item
                for item in handover_pending
                if not any(
                    tx is previous_transaction for tx in item.get("tx_chain") or ()
                )
            ]
            if handover_kept:
                session.info[_HANDOVER_PENDING_KEY] = handover_kept
            else:
                session.info.pop(_HANDOVER_PENDING_KEY, None)

        # Same C2 rule for the undo queue: only the entries earned under the
        # transaction that just rolled back are discarded, never a sibling order's.
        undo_pending = session.info.get(_UNDO_PENDING_KEY)
        if undo_pending:
            undo_kept = [
                item
                for item in undo_pending
                if not any(
                    tx is previous_transaction for tx in item.get("tx_chain") or ()
                )
            ]
            if undo_kept:
                session.info[_UNDO_PENDING_KEY] = undo_kept
            else:
                session.info.pop(_UNDO_PENDING_KEY, None)

    _POST_COMMIT_DISPATCH_REGISTERED = True
