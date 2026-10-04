"""Order promising: the supply composition sheet, and the one atomic confirmation.

Contract: `PLAN-scm-front-planning.md` sections 3.1 to 3.5 and 4,
`documentation/plans/scm/STAGE1C-scm-front-planning-promising.md` section 5, UAC groups B
and C.

The arithmetic is NOT here. It is the pure engine next door
(`app.services.scm.front_planning_engine`), so the rules can be tested without a database
and so the sheet and the commit cannot drift into two different opinions about the same
line. This file is everything around it: which facts are read, how the one location pile is
shared, what is rechecked at commit, and what is written.

Four ideas run through the whole file.

**Confirmation is one decision per SO, covering the lines the planner chose** (3.1, as
amended by PLAN-fulfilment-planning-from-autocount-so.md 13.4). It was atomic over every
line of the order; it is now atomic over every line of THIS confirmation:

> "we shouldn't block the confirm when the decision for the order are incomplete yet, we
>  might want to flow a few product to reorder planning first"   (captain)

One stale, unbalanced or unmapped line among the chosen ones still rolls back the whole
transaction, and the refusal still names every failing line by line number and item code.
No UUID ever appears in a message. What changed is that a line the planner did not choose
is EXPLICITLY UNDECIDED (`decided: false`, no snapshot, no allocation, no inquiry row) -
never implicitly zero and never implicitly covered - so its full open quantity keeps
counting as demand for reorder planning. That is the whole reason the captain wanted this,
and the per-line half of `scm.committed_v` (`app/services/scm/demand.py`) is what makes it
true rather than merely intended. There is still no per-line workflow STATE: a line is
covered by the order's one active revision or it is not.

**Every fact is re-read at commit** (AC-C03). The sheet may have been open for an hour, and
the stock behind it moves. The payload is a proposal, not an instruction: open quantity, the
line's share of timely SPO, Reserve eligibility, the BRW cap, donor availability and product
lifecycle are all recomputed from authoritative rows before anything is written.

**Confirmed cover is not free** (AC-B13). Free stock is on hand, minus reserved, minus what
active decisions (and legacy confirmed allocations, which belong to no decision) already
hold. The lines being REPLACED are excluded from that subtraction, or their own previous
revision would compete with the one replacing it - by line and not by order, because a
covered line the confirmation carries forward keeps its hold (13.4, the union is the
server's) and must be read as any other order's covered line.

**One pile, one order of consumption** (3.5). Several lines can want the same product at the
same location, and which of them gets the stock and which gets the incoming is decided by
`attribute_sources`, once, for every outstanding line at that product and location - not by
whichever line the database happened to return first.
"""
from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass, field, replace as dataclass_replace
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from functools import cmp_to_key
from types import SimpleNamespace
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    List,
    Mapping,
    MutableMapping,
    Optional,
    Sequence,
    Set,
    Tuple,
)

from sqlalchemy import case, func, null, nullslast, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased

from app.models.inventory import Stock, Warehouse
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
from app.models.sales_agent import SalesAgent
from app.models.project_so import (
    ACK_AWAITING,
    ALLOC_SOURCE_BRW,
    ALLOC_SOURCE_GROUP_TAKE,
    ALLOC_SOURCE_ORDER,
    ALLOC_SOURCE_OTHER_LOCATION,
    ALLOC_SOURCE_OTHER_PROJECT,
    ALLOC_SOURCE_OWN,
    CLAIM_ACCEPTED,
    DECISION_ACTIVE,
    DECISION_CHALLENGED,
    DECISION_SUPERSEDED,
    INQUIRY_CANCELLED,
    INQUIRY_PLACED,
    INQUIRY_RAISED,
    IV_ORDER_BACK,
    LIVE_SO_STATUSES,
    AllocationClaim,
    OrderInquiryLink,
    OrderInquiryRow,
    ProjectSalesOrder,
    ProjectSalesOrderLine,
    SOLineAllocation,
    SOSupplyDecision,
)
from app.models.projects import Project
from app.models.scm import ItemClassification, ReorderLevel, SupplierPerformance
from app.models.user import User
from app.services.error_handler import AppException
from app.services.scm import priority, spo_supply
from app.services.scm.history_sources import SPO_HISTORY_SOURCE
from app.services.scm.supply_origin import buy_origin_by_product
from app.services.scm.container_request_service import OPEN_PO_STATUSES
#: `purchase_orders.source_system` for a CRM-minted SPO document. Imported under a
#: name that says whose stamp it is, so `_po_rows`' exclusion reads as "not a shipping
#: leg" rather than as an unexplained string comparison.
from app.services.scm.spo_conversion_service import (
    SOURCE_SYSTEM as CRM_SPO_SOURCE_SYSTEM,
)
from app.services.scm import sales_agent_service
from app.services.scm.demand import demand_qty, is_open_demand, is_transferable_order
from app.services.scm.group_netting import GroupNetting
from app.services.scm.planning_predicate import (
    OUTSIDE_FULFILMENT_PLANNING,
    fulfilment_planning_predicate,
    outside_fulfilment_planning,
)
from app.services.scm.front_planning_engine import (
    BORROW,
    BUY,
    DEFAULT_LEAD_TIME_DAYS,
    RESERVE,
    RUNG_GROUP_BORROW,
    RUNG_GROUP_TAKE,
    RUNG_ORDER_BORROW,
    RUNG_POOL,
    RUNG_SUPPLY_BORROW,
    TIMELY_SPO,
    Component,
    Option,
    attribute_sources,
    available_for_project,
    group_take_reason,
    group_water_reason,
    landed_on,
    later_order_can_wait,
    pool_reserve_capacity,
    pool_share_reason,
    qty_text,
    reserve_window_end,
    spo_reason,
    supply_borrow_reason,
    walk_line,
)
# Ladder v7.1 reads ONE assignment with the Stock Debt view (R21). Aliased on import so the
# two vocabularies stay distinguishable in this file: `STATUS_COVERED` beside a decision's
# own states would read as the same word twice.
from app.services.scm.supply_assignment import (
    KIND_ON_HAND as SA_KIND_ON_HAND,
    KIND_PO as SA_KIND_PO,
    KIND_SPO as SA_KIND_SPO,
    POOL_GROUP as SA_POOL_GROUP,
    STATUS_COVERED as SA_STATUS_COVERED,
    STATUS_PINNED as SA_STATUS_PINNED,
    effective_date as sa_effective_date,
    free_piles_at,
    group_book_positions,
    parse_supply_key,
)

logger = logging.getLogger(__name__)

_ZERO = Decimal("0")

#: The ladder that composed a proposal, stamped on every component frozen at confirm
#: (`_proposed_component`). Read by the board so a suggestion made under an older rule can
#: be labelled as one rather than read as today's answer. Bumped when the rungs change what
#: they MEAN, never when a sentence is reworded - and v8 changed three of them: the site
#: pool is asked FIRST and for a SHARE rather than all-or-nothing (R-A/R-B), the dealer
#: hot-selling gate that removed the step entirely is retired, and a planning unit's
#: contributing lines walk one at a time rather than as one quantity (R-E).
#: `supplyVocabulary.ts` mirrors this string; the two move together or every live
#: suggestion reads as history.
LADDER_VERSION = "v8"

#: The statuses a Project SO may be confirmed in. A draft has not left the building and a
#: blocked one has findings in the way.
#:
#: `adopted` is IN, which is `PLAN-fulfilment-planning-from-autocount-so.md` section 4's own
#: verdict on this site ("Confirming it is the point"): an order adopted from the AutoCount
#: book is a real customer commitment that nobody in this module authored, and planning it
#: is the entire journey. It is `LIVE_SO_STATUSES` exactly - the same three the worklist and
#: the reconciliation service already treat as live - so the tuple is imported rather than
#: restated, or the two would drift the next time a status is added.
CONFIRMABLE_STATUSES = LIVE_SO_STATUSES

#: The columns the Plans page (D1) may sort by, and therefore the only ones the route's own
#: `Literal` offers - imported into the route rather than restated, the same discipline
#: `FulfilmentPlanningRow.sort` follows, with a test asserting the two agree.
PLAN_SORT_FIELDS: Tuple[str, ...] = (
    "so_number",
    "customer_name",
    "agent_code",
    "revision_no",
    "state",
    "decided_at",
)

#: The product a hold is keyed by: the CORE line's when the mirror line is reconciled to
#: one, else the mirror's own. Free stock is read against the core product (`_facts_for`
#: prefers it on a remap), so the hold must net out of that pile and not the mirror's.
_hold_product = func.coalesce(SalesOrderLine.product_id, ProjectSalesOrderLine.product_id)


def owed_qty_expr():
    """`_open_of` as SQL, over the `SalesOrderLine` `_hold_query` already outer-joins.

    The same three rules the Python primitive applies, in the same order: a CANCELLED line
    owes nothing whatever its columns say (the book rarely reverses a delivered quantity
    when it cancels one), what is owed is `ordered - delivered` floored at zero, and an
    allocation with NO core line behind it is not a question this can answer - it returns
    NULL there, and the caller decides what that means.

    NULL rather than zero for the missing line is the whole reason for the leading branch.
    Postgres `greatest()` IGNORES nulls, so `greatest(NULL - 0, 0)` is 0, not NULL: without
    it, every allocation on an unreconciled mirror line would silently cap to zero and stop
    holding stock it really is holding.
    """
    return case(
        (SalesOrderLine.id.is_(None), null()),
        (func.coalesce(SalesOrderLine.line_status, "open") == "cancelled", 0),
        else_=func.greatest(
            func.coalesce(SalesOrderLine.qty_ordered, 0)
            - func.coalesce(SalesOrderLine.qty_delivered, 0),
            0,
        ),
    )


def held_qty_expr():
    """What a confirmed allocation is ACTUALLY holding: `least(alloc.qty, still owed)`.

    `so_line_allocations.qty` is frozen at confirm and delivery never shrinks it, while the
    book has already taken the delivered units off `quantity_on_hand` - so a decided line
    that has since shipped was subtracted twice, once by the warehouse and once by its own
    hold, and the difference was stock the business had that no screen could see. Capping
    here rather than at each reader is what makes the free-stock arithmetic, the Stock Debt
    screen and the confirm-time refusal quote one figure (AC-S1-4).

    An allocation with no core line keeps its raw quantity: there is nothing to cap against,
    and guessing zero would release stock somebody is holding.
    """
    return func.least(
        SOLineAllocation.qty, func.coalesce(owed_qty_expr(), SOLineAllocation.qty)
    )


def _pile_order(line: Dict[str, Any]) -> Tuple[Any, ...]:
    """The queue order at one pile: score, then required date (missing last), then the
    OPEN QUANTITY ascending, then sales-order number, line number, line id. See
    `ProjectSupplyService._rank_pile`.

    LADDER V8 (R-E): the quantity joins the tie-break, ascending, so a pile that cannot
    cover everything wanted on one date covers the SMALL lines it can rather than being
    spent on the first big one and leaving four short. SO419208's 135 is served before its
    own 1,305 for that reason, and the Stock tab's running Available reads the two in the
    same order the walk fills them in.
    """
    return (
        -line["rank_score"],
        line.get("required_date") is None,
        line.get("required_date") or date.min,
        _dec(line.get("open_qty")),
        line.get("so_number") or "",
        line["line_no"] if line.get("line_no") is not None else 0,
        line["line_id"],
    )


def _pile_key(code: str, water: bool, per_half: bool) -> str:
    """How one bin's pile is addressed in a walk ledger.

    The OWN half keeps the floor and the water apart (ladder v8, R-E: two lines of a unit
    share one bin, and what the first took off the FLOOR is not still there for the second),
    every other caller budgets the bin as one pile, which is what it was before.
    """
    return f"{code}\x00water" if (per_half and water) else code


def _group_budget_key(group: str) -> str:
    """How a LENDING GROUP's whole budget is addressed in the offer ledger (R-M).

    R-M's cap is a statement about the GROUP, not about a bin, so the walk has to spend it
    once across every bin the group owns and every unit of the board. The NUL keeps it out
    of the warehouse-code namespace the same ledger's per-bin keys live in, exactly as
    `_pile_key`'s water suffix does.
    """
    return f"group\x00{group}"


def _own_arrival_spare_key(source_ref: str) -> str:
    """How ONE sibling line's own-arrival SPARE is addressed in the walk's credit ledger
    (MB2, review round 21 Sep 2026).

    R7 tier 2 lends a sales order's landed-but-unclaimed stock to its other open lines, and
    that spare belongs to the SIBLING, not to the bin: on a location holding plenty, two
    open lines each read the whole of one closed sibling's 40 and both were credited off
    it. Keyed by the sibling's own `source_ref` so the second line sees what the first
    already took. The NUL keeps it out of the warehouse-code namespace the same ledger's
    per-bin keys live in, exactly as `_pile_key` and `_group_budget_key` do.
    """
    return f"spare\x00{source_ref}"


def _add_landed(
    by_ref: Dict[str, Dict[str, Decimal]], ref: str, spo_number: Optional[str], qty: Any
) -> None:
    """One SPO row's receipt, added to `ref -> {spo_number -> landed}`."""
    shipments = by_ref.setdefault(ref, {})
    shipments[spo_number or ""] = shipments.get(spo_number or "", _ZERO) + _dec(qty)


def _landed_document(shipments: Mapping[str, Decimal]) -> Tuple[Decimal, Optional[str]]:
    """`(what landed, the document naming it)` for one line (#1362 item 2).

    One shipment is named bare ("SPO-2026/06-0092"). Several that each landed something
    are named every one, with its own quantity, in SPO order ("60 on SPO-2026/06-0092, 40
    on SPO-2026/07-0019") - `front_planning_engine.landed_on` reads that shape.
    """
    total = sum(shipments.values(), _ZERO)
    named = sorted((spo, qty) for spo, qty in shipments.items() if spo and qty > _ZERO)
    if len(named) > 1:
        return total, ", ".join(f"{qty_text(qty)} on {spo}" for spo, qty in named)
    if named:
        return total, named[0][0]
    return total, next((spo for spo in sorted(shipments) if spo), None)


def _landed_for_refs(
    by_ref: Mapping[str, Mapping[str, Decimal]],
    linked_by_ref: Mapping[str, Mapping[str, Decimal]],
) -> Dict[str, Tuple[Decimal, Optional[str]]]:
    """`ref -> (landed, document)`, the order inquiry's placement first (#1362 item 2).

    Where the line's order inquiry row links a specific SPO and that link has landed
    something, the link names the shipment and its landed figure is tier 1 - also when the
    PO line's own receipts add to LESS (the inquiry placed the line on a shipment its own
    PO line was never received across). The one exception is a PO line that landed MORE
    than the placement says: those goods physically arrived against this line's own
    purchase, and a placement cannot take them away (AC-S3-12's own shape, 70 landed on the
    line's PO beside a 40 placement), so that larger figure stands with its own shipments
    named. Otherwise the PO line's receipts stand, every contributing shipment named.
    """
    out: Dict[str, Tuple[Decimal, Optional[str]]] = {}
    for ref in set(by_ref) | set(linked_by_ref):
        linked = linked_by_ref.get(ref) or {}
        linked_total = sum(linked.values(), _ZERO)
        own_total = sum((by_ref.get(ref) or {}).values(), _ZERO)
        if linked_total > _ZERO and linked_total >= own_total:
            out[ref] = _landed_document(linked)
        elif ref in by_ref:
            out[ref] = _landed_document(by_ref[ref])
    return out


def _dec(value: Any, default: Decimal = _ZERO) -> Decimal:
    if value is None:
        return default
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return default


def _parse_date(value: Any) -> Optional[date]:
    """A frozen snapshot's date fields are ISO strings (JSON has no date type); a carried
    line reads them back off `SOSupplyDecision.line_snapshots` and needs the `date` this
    class's other readers already expect."""
    if value is None or isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


#: How many of the queue in front of a line are NAMED beside it. The captain's question is "why
#: do the orders stand ahead of me", and 142 rows is not an answer to it: three named lines plus
#: a count of the rest BY WHAT PUT THEM THERE is. The whole queue is a click away (`pile_queue`).
_AHEAD_NAMED = 3


def _leading_factor(mine: Dict[str, Any], theirs: Dict[str, Any]) -> str:
    """Which factor put `theirs` in front of `mine`, in one word.

    The largest WEIGHTED difference, `weight * (their value - my value)` over the factors both
    lines carry a value for, because that is literally the term that made their score the bigger
    one. Absent on either side is skipped rather than read as zero, the same rule `rank_score`
    itself applies.

    A factor THEY carry and I do not is a difference too: their score has a term mine has
    nothing to answer, so it counts as `weight * their value` and is named for that factor
    (they have payment terms recorded, I have none). Without it every shared difference was
    zero and the answer fell through to "a lower sales order number", which is not why they
    are ahead.

    Equal scores mean the POLICY separated nothing and the queue was decided by the tie-break
    instead, in `_pile_book`'s own order: the required date first (`earlier_date` - it IS a
    date difference, but a tie-break rather than a factor, so it is named apart from
    `need_by_date`), then an earlier line of the same order, then a lower sales-order number.
    """
    same_order = (theirs.get("so_number") or "") == (mine.get("so_number") or "")
    if round(float(theirs.get("rank_score") or 0.0), 9) == round(
        float(mine.get("rank_score") or 0.0), 9
    ):
        their_date = theirs.get("required_date")
        my_date = mine.get("required_date")
        if their_date is not None and (my_date is None or their_date < my_date):
            return "earlier_date"
        return "line_order" if same_order else "tie_break"
    tie = "line_order" if same_order else "tie_break"
    ours = {
        factor.key: factor
        for factor in (mine.get("factors") or [])
        if factor.present and factor.value is not None
    }
    best_key: Optional[str] = None
    best_diff = 0.0
    for factor in theirs.get("factors") or []:
        if not factor.present or factor.value is None or factor.weight <= 0:
            continue
        counterpart = ours.get(factor.key)
        my_value = 0.0 if counterpart is None else float(counterpart.value)
        diff = float(factor.weight) * (float(factor.value) - my_value)
        if diff > best_diff:
            best_key, best_diff = factor.key, diff
    return best_key or tie


def _queue_entry(line: Dict[str, Any], mine: Dict[str, Any]) -> Dict[str, Any]:
    """One line of the queue as a screen reads it, against the line that is asking."""
    return {
        "so_number": line.get("so_number") or "",
        "line_no": line.get("line_no"),
        "qty": qty_text(line["open_qty"]),
        "required_date": line.get("required_date"),
        "rank_score": round(float(line.get("rank_score") or 0.0), 6),
        "leading_factor": _leading_factor(mine, line),
        "same_order": (line.get("so_number") or "") == (mine.get("so_number") or ""),
    }


def _ahead_detail(mine: Dict[str, Any], queued: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Who is in front of this line at its pile, and what put each of them there.

    Three named, in queue order, plus how many more there are and a count of the WHOLE queue by
    leading factor - so "142 lines are ahead" becomes "139 of them by required date, 2 by
    document age, and one is an earlier line of your own order".
    """
    by_factor: Dict[str, int] = {}
    named: List[Dict[str, Any]] = []
    for other in queued:
        entry = _queue_entry(other, mine)
        key = entry["leading_factor"]
        by_factor[key] = by_factor.get(key, 0) + 1
        if len(named) < _AHEAD_NAMED:
            named.append(entry)
    return {
        "lines": named,
        "more": max(len(queued) - len(named), 0),
        "by_factor": by_factor,
    }


def _open_of(core: Optional[SalesOrderLine]) -> Decimal:
    """AC-B01: the core line's CURRENT open fulfilment quantity, floored at zero.

    Not the original customer quantity, and not a figure a downstream reader has already
    netted: what is still owed, in the line's own UOM. A CANCELLED line owes nothing (R2c,
    13 Sep browser walk): `qty_ordered - qty_delivered` alone does not read zero for one
    (the book rarely reverses a delivered quantity when it cancels a line), and this is the
    single primitive the drift check (`_carry_snapshot_has_drifted`) and the board's own
    queue both read - reading it here is the one place that fixes both.
    """
    if core is None or (core.line_status or "open") == "cancelled":
        return _ZERO
    return max(_dec(core.qty_ordered) - _dec(core.qty_delivered), _ZERO)


def plan_qty_of(core: Optional[SalesOrderLine]) -> Decimal:
    """What the BOARD plans one line for: `coalesce(qty_required, qty_ordered)`.

    The Python twin of `demand.plan_qty()`, and the 14 September 2026 ruling in one line:
    NOT the still-owed figure `_open_of` states. A delivered unit nobody ever sourced is a
    unit to put back, so the ladder is asked for the whole quantity and a Buy on it raises
    the order-back row. A CANCELLED line plans nothing, exactly as it owes nothing.

    `_open_of` keeps its own readers, all of which genuinely mean "what is still owed": the
    hold cap (S1), `project_line_draft_service`'s saved snapshot and `sales_order_service
    .is_stale`.
    """
    if core is None or (core.line_status or "open") == "cancelled":
        return _ZERO
    required = core.qty_required
    return max(_dec(core.qty_ordered if required is None else required), _ZERO)


class SupplyLinesRefused(AppException):
    """A refusal that names its lines (AC-C02).

    The envelope stays the shared one - `message` is what `extractApiError` reads - and
    `failing_lines` travels beside it, because a sentence cannot tell the sheet WHICH row
    to mark and a list of rows cannot be read out as a sentence.
    """

    def __init__(
        self,
        status_code: int,
        message: str,
        failing_lines: Sequence[Dict[str, Any]],
        code: str = "supply_lines_failed",
    ):
        super().__init__(status_code=status_code, message=message, code=code)
        self.detail["failing_lines"] = list(failing_lines)

    @property
    def failing_lines(self) -> List[Dict[str, Any]]:
        """The refused lines under the name this class's own docstring gives them.

        The wire shape is `detail["failing_lines"]` and stays that way - it is what the
        frontend reads. This is the same list for a Python caller, so a test or a service
        catching the refusal asks for it by name instead of reaching into the envelope.
        """
        return list(self.detail.get("failing_lines") or [])


class ReserveOverHand(SupplyLinesRefused):
    """A Reserve for more than is physically there once other lines are counted (R14).

    A `SupplyLinesRefused` so the board pins the row exactly as it pins every other refusal,
    with the position stated beside the sentence: the message says "BRW-AM: 10 on hand, 1
    already reserved by SO383850, you asked 15", and `reserve_conflict` carries the same
    facts in fields for anything that wants to render them rather than read them.
    """

    def __init__(
        self,
        *,
        message: str,
        failing_lines: Sequence[Dict[str, Any]],
        conflict: Dict[str, Any],
    ):
        super().__init__(
            status_code=409,
            message=message,
            failing_lines=failing_lines,
            code="supply_reserve_over_hand",
        )
        self.detail["reserve_conflict"] = conflict


def _error_detail(exc: Exception) -> Tuple[str, Optional[List[Dict[str, Any]]]]:
    """What went wrong, and which lines it named, off any exception `confirm` can raise.

    `confirm_many` cannot let one order's raised `AppException` bubble - it has to KEEP the
    message (and `failing_lines`, when the refusal named any) beside the `pso_id` it belongs
    to and move on to the next order. `AppException.detail` is a plain dict, not an attribute,
    which is why this is a function and not a property on the exception itself.
    """
    if isinstance(exc, AppException):
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        message = detail.get("message") or str(exc)
        failing_lines = detail.get("failing_lines")
        return message, failing_lines
    return str(exc), None


@dataclass
class _SpoRow:
    spo_number: str
    spo_line_no: Optional[int]
    allocation_id: str
    arrival_date: Optional[date]
    qty: Decimal
    #: How many days late the promised arrival is, 0 when it is today, ahead or unstated.
    #: Carried on the row rather than recomputed per surface: the trail, the popover and
    #: the sheet all say "overdue N days" and they must say the same N.
    overdue_days: int = 0
    #: Who it is coming from. Display only, and defaulted so every existing construction of
    #: this row keeps working; the sheet does not read it, the stock drill-down does.
    supplier_name: Optional[str] = None
    #: R26 (Stock Debt only): the SPO line's RAW `allocated_quantity`/`quantity_received`,
    #: beside `qty` above which stays the NETTED outstanding balance every other caller
    #: (the ladder, the board) already reads and keeps reading unchanged. Defaulted so the
    #: one other construction of this row keeps working; only the Stock Debt drill's own
    #: Qty/Received/Outstanding columns read either.
    ordered_qty: Optional[Decimal] = None
    received_qty: Optional[Decimal] = None


@dataclass(frozen=True)
class _PoRow:
    """One open purchase-order line, net of the SPO cut from it, at the date it would land.

    The dated half of "what is on order", and the counterpart of `_SpoRow`. The two dates it
    carries mean opposite things and the names say which is which (R29, R30):

    * `arrival_date` = `purchase_orders.issue_date + the supplier's lead time`. What the
      timeline counts.
    * `bought_for` = the line's own `expected_date`, which on this book is the SO DELIVERY
      DATE the buyer typed the line against - verified on SRTWB242, where PO 202605-S0072's
      line dates are exactly the open SO due dates for the product. Display only; nothing
      here or downstream reads it as an arrival.
    """

    po_number: str
    #: Derived, because `purchase_order_lines` carries no line number: the position within
    #: its document in the order the document's own relationship uses (created_at, id).
    #: Two lines of one PO for one product otherwise read as the same row on screen.
    po_line_no: int
    line_id: str
    arrival_date: Optional[date]
    bought_for: Optional[date]
    qty: Decimal
    supplier_name: Optional[str] = None
    #: R42 (Stock Debt only, 28 Sep 2026): the document's own id and the RAW ordered/
    #: received quantities beside the netted `qty` above. Defaulted so every other
    #: construction keeps working; the board and the ladder read none of them.
    purchase_order_id: Optional[str] = None
    ordered_qty: Optional[Decimal] = None
    received_qty: Optional[Decimal] = None


@dataclass(frozen=True)
class _WaterAtLocation:
    """What one group location has ON THE WATER for one line, split by the line's own date.

    The group's net is date-blind by design (section 1d: `on hand + SPO - SO`), but what a
    line may DRAW off that water is not - an SPO landing after the required date covers
    nothing (captain, 27 August 2026). So both halves are carried: the timely quantity, which
    question 1 may offer, and the late quantity, which the proof names with its date and
    never draws.
    """

    location: str
    #: Arriving on or before the line's required date, so drawable.
    timely_qty: Decimal
    #: The day the WHOLE of `timely_qty` has landed by - the date a planner promises against.
    arrival_date: Optional[date]
    #: Arriving after it. Named in question 1's sentence, never offered.
    late_qty: Decimal
    #: The earliest of the late arrivals: the soonest this water could be counted at all.
    late_from: Optional[date]


@dataclass
class _LineFacts:
    """Everything one line is judged against, read live (AC-C03).

    `line` and `core` are optional because the same bundle now serves TWO callers: the sheet,
    which judges a mirror line, and the multi-order board, which judges a bare core demand row
    and has no mirror to point at. Everything the source ladder reads is below them.
    """

    line: Optional[ProjectSalesOrderLine] = None
    core: Optional[SalesOrderLine] = None
    #: This line's number on its order. Stated on the FACT rather than read off `line`,
    #: because the board has no mirror to read it off and `compose_lines` needs it from both
    #: callers: the members of one planning unit are filled in line order (ladder v6), and a
    #: unit whose members were filled in the board's rank order would split one composition
    #: differently on the board and on the sheet.
    line_no: Optional[int] = None
    item_code: Optional[str] = None
    product_id: Optional[str] = None
    open_qty: Decimal = _ZERO
    #: What is still owed the CUSTOMER on this line, floored at zero (`_open_of`).
    #:
    #: `open_qty` above is what the line ASKS FOR - the plan quantity since the 14 September
    #: 2026 ruling - and the two differ on any line with a delivery. This one exists for the
    #: arithmetic that has to agree with the NETTING engine, which counts `demand_qty()`:
    #: see `_group_offer`, where un-netting the plan quantity would add back more than was
    #: ever subtracted.
    owed_qty: Decimal = _ZERO
    required_date: Optional[date] = None
    warehouse: Optional[Warehouse] = None
    pool: Optional[Warehouse] = None
    #: This line's share of the fulfilment location's free stock, from the shared
    #: projection - NOT the whole pile, or two lines would each be offered all of it.
    own_free: Decimal = _ZERO
    #: What the demand ranked AHEAD of this line at its own pile still wants, and how many
    #: lines that is. The arithmetic behind `own_free`, carried so a screen can print it:
    #: "1015 on hand, 1015 owed to 12 earlier lines, 0 left for this line".
    so_qty_ahead: Decimal = _ZERO
    lines_ahead: int = 0
    #: What was left AT THIS LINE'S OWN LOCATION when it was reached - `on hand - reserved -
    #: held - so_qty_ahead`. Distinct from `own_free`, which is what the line actually TOOK of
    #: it (capped at what it needs), and from a Reserve total, which may also draw on the pool.
    #:
    #: REPORTING ONLY since ladder v4 (section 1d). It was the own location's Reserve cap
    #: under v3; availability is now the ownership GROUP's (`group_net`), and the rank queue
    #: decides the ORDER lines are served in, not how much exists.
    available_to_this_line: Decimal = _ZERO
    #: WHO is in that queue: the first `_AHEAD_NAMED` of it in rank order, how many more there
    #: are, and a count of the whole queue by the factor that put each line there. The captain,
    #: on a rung reading "18730 across 142 lines": "why do the orders stand ahead of me? why?"
    ahead_lines_named: List[Dict[str, Any]] = field(default_factory=list)
    ahead_more: int = 0
    ahead_by_factor: Dict[str, int] = field(default_factory=dict)
    pool_free: Decimal = _ZERO
    #: What the POOL'S OWN book, ranked ahead of this line, still claims there, and how many
    #: lines that is - the subtraction behind `pool_free`, carried so a screen can print it:
    #: "BRW holds 1 on hand, but its own orders ahead of this line claim 1, so 0 is left".
    pool_claimed_qty: Decimal = _ZERO
    pool_claimed_lines: int = 0
    pool_reorder_level: Decimal = _ZERO
    #: ABC A on RETAIL (dealer)-classed demand at an active, available warehouse (PLAN 3.3,
    #: amended 19 August 2026: the demand class the ABC figure was computed against decides
    #: hot-selling now, not the warehouse's own segment). Gates the shared pool to nothing.
    is_dealer_hot_selling: bool = False
    #: The locations holding it as ABC A on retail demand, by code - the evidence behind
    #: `is_dealer_hot_selling`, so the verdict can be checked rather than trusted.
    dealer_hot_selling_where: List[str] = field(default_factory=list)
    #: ABC A on PROJECT-classed demand at an active, available warehouse. Gates the shared
    #: pool to its own signed availability (`pool_available`) rather than removing it.
    is_project_hot_selling: bool = False
    #: The locations holding it as ABC A on project demand, by code.
    project_hot_selling_where: List[str] = field(default_factory=list)
    #: Classified (a non-null letter exists on that column, at an active location) but not
    #: hot - "Cold at retail" / "Cold at project", the plain word for a real, non-A letter.
    #: `False` while the class is hot too; the hot flag above already covers that case.
    dealer_classified: bool = False
    project_classified: bool = False
    #: The shared pool's own signed availability - `on hand - SO qty + SPO qty` - the figure
    #: a project hot-selling line's pool draw is capped against. Unsigned `pool_free` above
    #: is what THIS line's queue left of it; this is the pool's whole position, and can be
    #: negative (an oversold pool offers nothing, never a floor of zero read as "some").
    pool_available: Decimal = _ZERO
    classification_unavailable: bool = False
    is_discontinued: bool = False
    timely_qty: Decimal = _ZERO
    timely_refs: List[_SpoRow] = field(default_factory=list)
    advisory_refs: List[_SpoRow] = field(default_factory=list)
    #: Why nothing can be proposed for this line, when nothing can (a mirror line with no
    #: reconciled AutoCount line). Set, the line is read out with no components and no
    #: donors; it is never carried and cannot be named to a confirmation.
    unplannable_reason: Optional[str] = None
    #: Ladder v2 (`PLAN-demo-followups-19aug-ladder-v2.md` section E, section 8). The
    #: OWNERSHIP GROUP this line's own location belongs to - the suffix after the hyphen
    #: (`BRW-BB` -> `BB`) - or `None` for a location that carries no group (a bare pool
    #: code, or an inactive/unrecognised warehouse).
    group_code: Optional[str] = None
    #: Ladder v4 (section 1d). What the WHOLE ownership group holds of this product -
    #: `sum(on hand) - sum(SO) + sum(SPO)` over every active `*-<group>` location, signed -
    #: and what the five site pools hold between them. These, not any one warehouse's own
    #: reading, are what rungs 1 to 4 may draw on: `MWH-IB` holding 7000 offers nothing
    #: while the IB group nets -15514, because those 7000 are already owed at `BRW-IB`.
    group_net: Decimal = _ZERO
    pools_net: Decimal = _ZERO
    #: What the group's net leaves for THIS line: `max(group_net + its own STILL-OWED
    #: quantity, 0)`. See `ProjectSupplyService._group_offer` for the rule, for why the
    #: un-net is `owed_qty` and not `open_qty`, and for the consequence it carries: while a
    #: group cannot cover its own book, no line of it takes its stock.
    group_offer: Decimal = _ZERO
    #: Ladder v7.1: the CORE sales-order line ids of the planning UNIT this fact stands
    #: for. A unit of one is its own line; `_unit_fact` stamps every member's for a unit of
    #: several, because step 1's date-aware pile is what the assignment gave THOSE lines.
    unit_core_line_ids: List[str] = field(default_factory=list)
    #: R7 (review round, query-count): the CORE line's own `source_ref` / `sales_order_id`
    #: / `company_id`, carried on the fact for the BOARD path (`demand_facts`'s row is
    #: already the full `SalesOrderLine` the board's own demand read fetched - see
    #: `project_fulfilment_board_service._demand_rows` - so stamping them here costs no
    #: second query). `own_arrival_credit_for`'s own read used to look its representative
    #: core line up by id, fresh, once per FACT the walk asked about; a board of 76 lines
    #: split into ten cells still paid ten identical-shaped round trips for it. `None` for
    #: any caller that has not threaded them through yet - `_prefetch_own_arrival` falls
    #: back to the old per-id read for those, so nothing regresses, only costs more.
    source_ref: Optional[str] = None
    sales_order_id: Optional[str] = None
    line_company_id: Optional[str] = None
    #: The group's position location by location (`group_netting.LocationNet`), the evidence
    #: behind `group_net`.
    group_net_by_location: List[Any] = field(default_factory=list)
    #: What rung 1 WOULD have counted before the group's own backlog was served (section 1d:
    #: an SPO to `BRW-IB` is owed to the IB backlog first). `timely_qty` above is already
    #: netted; this is kept so the trail can say "110 arrives, and none of it is free for
    #: this line" rather than showing nothing at all.
    timely_qty_before_group_net: Decimal = _ZERO

    @property
    def own_code(self) -> Optional[str]:
        return self.warehouse.warehouse_code if self.warehouse else None

    @property
    def pool_code(self) -> Optional[str]:
        return self.pool.warehouse_code if self.pool else None


class _BorrowLedger:
    """What is still borrowable as one confirmation's lines are checked in turn.

    Two lines of the same Project SO can both name the same donor location, and each of
    them is checked against live figures - so without a running ledger both would pass and
    the same units would be promised twice inside one transaction. Seeded lazily from the
    live readers so nothing is queried for a location nobody borrows from.
    """

    def __init__(self) -> None:
        self._free: Dict[Tuple[str, str], Decimal] = {}
        self._held: Dict[Tuple[str, str, str], Decimal] = {}

    def free(self, product_id: Optional[str], warehouse_id: str, read) -> Decimal:
        key = (product_id or "", warehouse_id)
        if key not in self._free:
            self._free[key] = read(product_id, warehouse_id)
        return self._free[key]

    def take_free(self, product_id: Optional[str], warehouse_id: str, qty: Decimal) -> None:
        key = (product_id or "", warehouse_id)
        self._free[key] = max(self._free.get(key, _ZERO) - qty, _ZERO)

    def held(
        self, product_id: Optional[str], warehouse_id: str, donor_id: str, read
    ) -> Decimal:
        key = (product_id or "", warehouse_id, donor_id)
        if key not in self._held:
            self._held[key] = read(product_id, warehouse_id, donor_id)
        return self._held[key]

    def take_held(
        self, product_id: Optional[str], warehouse_id: str, donor_id: str, qty: Decimal
    ) -> None:
        key = (product_id or "", warehouse_id, donor_id)
        self._held[key] = max(self._held.get(key, _ZERO) - qty, _ZERO)


class _CapacityLedger:
    """What a Reserve rung's location still has left, across every line of ONE
    confirmation (S7).

    `_check_line` used to build a fresh capacity dict per line from live figures, and only
    carried the line's OWN site pool forward. Every OTHER pool in the
    chain and every group-take sibling was re-read live on each line with no memory of
    what earlier lines of the SAME confirmation had already drawn - so two lines of one
    order could each be offered the whole of a secondary pool, or the whole of a sibling's
    free stock, and both accepted.

    Seeded lazily, one location at a time, from whatever LIVE capacity `_check_line` first
    computes for it; every later line of the confirmation reads and draws down the SAME
    running balance instead of the live figure again.
    """

    def __init__(self) -> None:
        self._left: Dict[Tuple[str, str], Decimal] = {}
        #: What this location's pile has been STATED to hold, in total, by whichever
        #: readings have spoken for it so far. `_left` is that number less what the
        #: confirmation has already taken, so raising the statement raises the balance by
        #: the difference and never by more.
        self._basis: Dict[Tuple[str, str], Decimal] = {}
        #: The date-aware slices claimed against it, summed (`offer`).
        self._claimed: Dict[Tuple[str, str], Decimal] = {}

    def capacity(
        self,
        product_id: Optional[str],
        warehouse_id: str,
        live_qty: Decimal,
        *,
        own: bool = False,
    ) -> Decimal:
        key = (product_id or "", warehouse_id)
        if key not in self._left:
            self._left[key] = live_qty
            self._basis[key] = live_qty
        elif own:
            # THE OWNING LINE'S OWN NETTING NEVER LOSES TO AN EARLIER SEED (review fix
            # round, S2's confirm-time counterpart): an earlier line's chain reached this
            # pool as one of its OTHERS and seeded it here unclaimed, and the line that
            # actually owns the pool - whose `live_qty` already nets the claims ranked
            # ahead of IT specifically - intersects the running balance with its own
            # reading rather than trusting whatever an unrelated line seeded first.
            self._left[key] = min(self._left[key], live_qty)
        return self._left[key]

    def offer(
        self, product_id: Optional[str], warehouse_id: str, share: Decimal
    ) -> Decimal:
        """State one planning unit's DATE-AWARE slice of a location (ladder v7.1, R24).

        Seed-if-absent is the right reading of a WHOLE-PILE number - ladder v4's group offer
        says what the location holds, so the first line to read it states the balance every
        later line draws down. It is the wrong reading of a SLICE: the assignment hands each
        unit a DISJOINT part of one bin (a unit due in September and a unit due in October
        are given different parts of the same 199), so seeding from the first unit's slice
        sells the whole confirmation the smallest of them.

        So the slices are SUMMED, and the pile is whichever statement about it is larger -
        the undated whole-pile reading or the slices claimed so far. Never their sum: two
        readings of one bin are two answers to one question, and adding them would promise a
        location's stock twice over.
        """
        key = (product_id or "", warehouse_id)
        if key not in self._left:
            self._left[key] = _ZERO
            self._basis[key] = _ZERO
        claimed = self._claimed[key] = self._claimed.get(key, _ZERO) + share
        if claimed > self._basis[key]:
            self._left[key] += claimed - self._basis[key]
            self._basis[key] = claimed
        return self._left[key]

    def state_at_least(
        self, product_id: Optional[str], warehouse_id: str, qty: Decimal
    ) -> Decimal:
        """State that this location's pile holds AT LEAST `qty` in total (S1, security
        review round two) - a single re-statement about the bin ("N of this floor landed
        for this line"), never a date-aware SLICE of demand the way `offer`'s `share` is.

        `offer` folds its `share` into `_claimed`, the running sum every unit's own dated
        slice at this bin is added to - right for another unit's genuine slice, wrong for
        the own-arrival credit: `offer` grants the credit nothing when the pile's stated
        basis already covers it (the common case), yet still adds it to `_claimed`, so a
        LATER unit's own dated slice at the SAME bin can then sum past `_basis` by the
        credit's own amount even though nothing physical backs that excess. `state_at_least`
        raises `_basis` to `max(_basis, qty)` and `_left` by the same delta, and never
        touches `_claimed` at all.
        """
        key = (product_id or "", warehouse_id)
        if key not in self._left:
            self._left[key] = _ZERO
            self._basis[key] = _ZERO
        if qty > self._basis[key]:
            self._left[key] += qty - self._basis[key]
            self._basis[key] = qty
        return self._left[key]

    def take(self, product_id: Optional[str], warehouse_id: str, qty: Decimal) -> None:
        key = (product_id or "", warehouse_id)
        self._left[key] = max(self._left.get(key, _ZERO) - qty, _ZERO)


@dataclass
class _UnitCheck:
    """What the confirmation judges a line's PLANNING UNIT against (ladder v6).

    The proposal is composed for the unit - one order's lines for the same item, location
    and delivery date are one quantity - so the recheck has to be seeded from the unit too.
    `_check_line` used to re-derive the group's offer per LINE, and on a group whose net is
    negative the members' own offers sum to LESS than the unit's: line 31 was proposed 5
    from its own location and then refused it ("has nothing free for this line now"), so no
    split of a unit could be confirmed at all.

    ONE instance per unit, shared by its members and drawn down as each is checked: `fact`
    is the unit fact (`_unit_fact`) the ladder was asked about, and `timely_left` is what is
    left of the water question 1 offered it, so two members cannot each post the whole of it.
    """

    fact: _LineFacts
    timely_left: Decimal
    #: Whether this unit's step-1 own-group slice has been added to the confirmation's
    #: capacity ledger yet (ladder v7.1). The slice belongs to the UNIT, so it is added
    #: once, on whichever of its member lines is checked first, and its members then share
    #: it - adding it per line would sell one slice to each of them.
    own_seeded: bool = False


@dataclass
class _FrozenComponent:
    """One component read back off a frozen snapshot, in the shape the payload's own
    components have (`.warehouse_id`, `.qty`, and for a borrow `.source`), so the shortfall
    arithmetic can walk a carried line and a named line with one loop.

    Ladder v2 group borrow (section E.4) also needs the donor fields carried through: a
    carried `group_borrow` component still holds another sales order's own committed
    quantity, and `_borrow_shortfalls` names that donor and raises its order-back off
    `donor_core_line_id` / `donor_so_number` / `donor_line_no` regardless of whether the
    line was just named or is riding along carried forward. Without these, a reconfirm
    that carries a group-borrow line silently drops that line's order-back.
    """

    warehouse_id: Optional[str]
    qty: Decimal
    source: Optional[str] = None
    donor_project_id: Optional[str] = None
    donor_core_line_id: Optional[str] = None
    donor_so_number: Optional[str] = None
    donor_line_no: Optional[int] = None
    donor_agent_code: Optional[str] = None
    same_agent: bool = False
    order_back_qty: Optional[str] = None
    donor_required_date: Optional[date] = None
    #: Ladder v7.1 step 3 (S4): WHICH document this component comes off. Carried for the
    #: same reason the donor fields are - `_borrow_shortfalls` reads it to know that this is
    #: a DOCUMENT and not stock at a bin, and without it a carried FREE take fell through to
    #: the location-pile rule and raised an order-back against the group whose bin the
    #: container is merely bound for.
    supply_key: Optional[str] = None
    supply_document: Optional[str] = None
    arrival_date: Optional[date] = None


@dataclass
class _CarriedLine:
    """A line the ACTIVE revision covers that this confirmation did not name.

    Carried into the new revision verbatim: `snapshot` is the previous revision's dict,
    unchanged, and its holds are copied row for row. It is judged against nothing, because
    it was judged when it was decided and nobody has asked to change it.
    """

    line: ProjectSalesOrderLine
    snapshot: Dict[str, Any]
    fact: _LineFacts

    def _components(self, kind: str) -> List[_FrozenComponent]:
        return [
            _FrozenComponent(
                warehouse_id=component.get("source_warehouse_id"),
                qty=_dec(component.get("qty")),
                source=component.get("source"),
                donor_project_id=component.get("donor_project_id"),
                donor_core_line_id=component.get("donor_core_line_id"),
                donor_so_number=component.get("donor_so_number"),
                donor_line_no=component.get("donor_line_no"),
                donor_agent_code=component.get("donor_agent_code"),
                same_agent=bool(component.get("same_agent")),
                order_back_qty=component.get("order_back_qty"),
                donor_required_date=_parse_date(component.get("donor_required_date")),
                supply_key=component.get("supply_key"),
                supply_document=component.get("supply_document"),
                arrival_date=_parse_date(component.get("arrival_date")),
            )
            for component in (self.snapshot.get("components") or [])
            if component.get("kind") == kind
        ]

    @property
    def reserve(self) -> List[_FrozenComponent]:
        return self._components(RESERVE)

    @property
    def borrow(self) -> List[_FrozenComponent]:
        return self._components("borrow")

    @property
    def buy_qty(self) -> Decimal:
        return _dec(self.snapshot.get("buy_qty"))


#: The phrase a same-agent borrow's reason has to carry (AC-L6, section 1c). The frontend
#: composes "Authorised by agent JEREMY: ..." in front of the planner's own words; this is
#: the half of it a machine can check without pinning the agent's name or the punctuation.
#: Matched case-insensitively, because the reason is free text a person typed.
_AUTHORISATION_PHRASE = "authorised by"


def _states_an_authorisation(reason: Optional[str]) -> bool:
    return _AUTHORISATION_PHRASE in (reason or "").strip().lower()


class ProjectSupplyService:
    """The supply sheet (`proposal_for`) and the atomic commit (`confirm`)."""

    def __init__(self, db: Session):
        self.db = db
        # Facts about THIS request, filled by `_facts_for` and read by the helpers below.
        # Per-request rather than per-call because one proposal asks the same "what is
        # free at that location" question once per line and once per borrow candidate.
        self._free_cache: Dict[Tuple[str, str], Decimal] = {}
        self._holds_cache: Dict[Tuple[str, str, str], Decimal] = {}
        # AutoCount's Stock Status triple per pile, for the locations this request touches.
        # Lazily filled, and only when somebody asks for borrow donors: a board that offers
        # no Borrow must not pay for three more reads. `None` means "not asked yet".
        self._pile_cache: Optional[Dict[Tuple[str, str], Dict[str, Decimal]]] = None
        # The open PURCHASE-order balance per pile, for the donor rows' own `po_open_qty`
        # (`po_open_facts`). Same lifetime as the pile it is read over. `None` means
        # "not asked yet".
        self._po_open_cache: Optional[Dict[Tuple[str, str], Decimal]] = None
        # The products the current read is about, stated by whichever fact builder ran.
        # The pile span is theirs, and not "whatever has a stock row" - see `_pile_facts`.
        self._request_product_ids: Set[str] = set()
        # Ladder v4's own reader (`app.services.scm.group_netting`), built off the pile
        # cache above and thrown away with it. `None` means "not built for this span yet".
        self._netting_cache: Optional[GroupNetting] = None
        # Open SPO rows WITH THEIR DATES for this request's products at every active
        # warehouse, read once. The pile's own `spo_qty` is a total with no arrival in it,
        # and question 1 may only draw the share that lands by the line's required date, so
        # the documents themselves have to be in hand - at the group's siblings too, not
        # only at the line's own location. `None` means "not read for this span yet".
        self._spo_by_location_cache: Optional[
            Dict[Tuple[str, str], List[_SpoRow]]
        ] = None
        # Every active warehouse by id, read once: the span the nets are summed over.
        self._planning_warehouses_cache: Optional[Dict[str, Warehouse]] = None
        # Warehouse and project rows this request has already read, by id. A donor list is
        # asked for once per line, and a board of hundreds of lines paid two round trips
        # per line to re-read the same handful of rows.
        self._warehouse_memo: Dict[str, Optional[Warehouse]] = {}
        self._project_memo: Dict[str, Optional[Project]] = {}
        # The free / holds caches indexed by product, rebuilt when either cache is
        # replaced (`_by_product`), so a per-line lookup walks that product's rows only.
        self._indexed: Optional[Tuple[Any, Any, Dict[str, Any], Dict[str, Any]]] = None
        # Ladder v2 (section E) per-request caches. `None` means "not read yet"; a
        # populated-but-empty dict is a real, cacheable answer, so each uses its own
        # sentinel rather than a falsy check.
        self._fulfilment_settings_cache: Optional[Dict[str, Any]] = None
        self._site_pools_cache: Optional[Dict[str, Warehouse]] = None
        self._group_siblings_cache: Dict[str, Optional[Dict[str, Warehouse]]] = {}
        self._group_donor_cache: Dict[str, List[Dict[str, Any]]] = {}
        self._warehouse_code_memo: Dict[str, Optional[Warehouse]] = {}
        # Ladder v2 group borrow (section E rule 4): how much of ONE donor sales-order
        # line is still available to lend, within this confirmation only - so two lines
        # of the same confirm cannot both take the whole of it (seeded net of what OTHER
        # confirmed decisions already hold from it, S1 - see `_group_borrow_held_qty`).
        self._donor_line_ledger: Dict[str, Decimal] = {}
        # Ladder v7.1 step 3 (S4): how much of ONE incoming document is still available to
        # this confirmation, seeded from the live book net of every placement link on it -
        # so two lines of one confirm cannot both be given the whole of it, exactly as
        # `_donor_line_ledger` stops two lines taking the whole of one donor line.
        self._supply_doc_ledger: Dict[str, Decimal] = {}
        #: Which holders' placements this confirmation has already credited back into that
        #: ledger - `(document, holder)`. A holder is credited ONCE however many components
        #: name it, or a document held by one donor would read as twice the size the moment
        #: two lines of the same confirm asked for it.
        self._supply_doc_credited: Set[Tuple[str, str]] = set()
        # The ATP reserve window's lead time per product, read once each per request: a board
        # of 300 lines asks about the same handful of products.
        self._lead_time_memo: Dict[str, Optional[int]] = {}
        # S3: the group pile is the SAME ranked list for every line sharing a
        # (product, group) - see `_group_pile_members`. `priority.active_policy` and
        # `priority.payment_terms_by_customer` are read at most once each per request too
        # (`_active_policy`, `_payment_terms_for`), and `_decided_elsewhere`'s own
        # whole-table read is memoized behind `_decided_elsewhere_cached` for the same
        # reason: a board of N lines of one product/group must not pay N round trips for
        # facts that do not vary by line.
        self._group_pile_members_cache: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
        self._payment_terms_cache: Dict[str, Optional[int]] = {}
        self._active_policy_loaded = False
        self._active_policy_value: Optional[Any] = None
        self._decided_elsewhere_cache: Optional[Set[str]] = None
        self._decided_anywhere_cache: Optional[Set[str]] = None
        self._active_decision_rows: Optional[List[Tuple[Any, Any]]] = None
        # PROJECT line ids of the lines this request is reading/replacing right now
        # (`_facts_for`'s own `replaced`, `demand_facts`'s own `exclude_line_ids`) - fed to
        # `_decided_elsewhere` so a line being decided in THIS request is never read as
        # "covered elsewhere" (S2, parity with `_pile_book`), and to `_group_borrow_held_qty`
        # so a donor line's OWN prior hold on one of these lines is un-netted the same way
        # `_free_stock` already un-nets it (S1).
        self._replaced_line_ids: Set[str] = set()
        # CORE sales-order line ids of every line the current confirmation/board selection
        # is itself deciding - never offered as a group-borrow donor (S2): a line whose own
        # state is moving in this same request is not a stable, pre-existing donor.
        self._current_selection_core_ids: Set[str] = set()
        # Ladder v7.1: `supply_assignment.assign()` per product, per `as_of`, read once for
        # the whole request (`planning_assignments`), plus its line index by CORE line id.
        # One board walks hundreds of lines over a handful of products, and the assignment
        # is the same answer for every one of them.
        self._assignment_cache: Dict[date, Dict[str, Any]] = {}
        self._assignment_index: Dict[Tuple[str, date], Dict[str, Any]] = {}
        #: (product, as_of) -> event key -> how late that COUNTED document is (R-O). Only
        #: the late ones are in it, so a bucket with no entry costs nothing to ask about.
        self._late_days_memo: Dict[Tuple[str, date], Dict[str, int]] = {}
        # The day the CURRENT walk is pinned to (`compose_lines`). A donor list asked for
        # outside the walk - the manual dialog's, say - has to read the same assignment the
        # walk did, or a board pinned to a simulated date pays for a second one against the
        # clock and answers from it.
        self._walk_as_of: Optional[date] = None
        # R7's own-arrival reads, memoized for the whole request (review round, SF2/SF3):
        # a board walks every line of a sales order and each one asks the SAME two
        # questions of that order - which siblings of this product it holds, and what has
        # landed against every purchase-order line bought for any of them. Keyed by
        # (sales order, product, company), so one sales order costs ONE pair of queries
        # per walk however many of its lines are composed rather than one pair per
        # sibling per line. `_own_arrival_line_memo` is the same idea for the core line a
        # fact names.
        self._own_arrival_line_memo: Dict[str, Optional[Any]] = {}
        self._own_arrival_order_memo: Dict[
            Tuple[str, str, str],
            Tuple[List[Any], Dict[str, Tuple[Decimal, Optional[str]]]],
        ] = {}

    # ------------------------------------------------------------------ lookups

    def get_order(self, pso_id: str) -> ProjectSalesOrder:
        order = (
            self.db.query(ProjectSalesOrder)
            .filter(ProjectSalesOrder.id == pso_id)
            .first()
        )
        if order is None:
            raise AppException(
                status_code=404, message="Sales order not found.", code="so_not_found"
            )
        return order

    def lines_of(self, pso_id: str) -> List[ProjectSalesOrderLine]:
        return (
            self.db.query(ProjectSalesOrderLine)
            .filter(ProjectSalesOrderLine.project_sales_order_id == pso_id)
            .order_by(ProjectSalesOrderLine.line_no.asc())
            .all()
        )

    def active_decision(self, pso_id: str) -> Optional[SOSupplyDecision]:
        return (
            self.db.query(SOSupplyDecision)
            .filter(
                SOSupplyDecision.project_sales_order_id == pso_id,
                SOSupplyDecision.state == DECISION_ACTIVE,
            )
            .first()
        )

    def latest_decision(self, pso_id: str) -> Optional[SOSupplyDecision]:
        return (
            self.db.query(SOSupplyDecision)
            .filter(SOSupplyDecision.project_sales_order_id == pso_id)
            .order_by(SOSupplyDecision.revision_no.desc())
            .first()
        )

    # -------------------------------------------------------------- the sheet

    def proposal_for(self, order: ProjectSalesOrder) -> Dict[str, Any]:
        """The Supply composition section for one Project SO (J04).

        Reads live facts and proposes a composition per line with the reason beside every
        quantity. A drift between the active revision's frozen snapshot and today's facts is
        no longer a signal of its own here (Slice E, one signal): the sheet reads what is
        confirmed, unchanged, and a manual edit or re-run raises its own change batch instead.

        TWO readings of the stock, when the order has a covered line, because there are two
        different questions on the page and they net that line's hold differently:

        * the WALK, which proposes for the undecided lines. A covered line's hold is stock
          on the floor with somebody's name on it, so it is netted out, exactly as the board
          and `confirm` net it;
        * the covered line's OWN proposal, which is what "Compose again" starts from. There
          its hold is un-netted, because an amendment replaces it: a line holding 10 has to
          read as the Reserve it is holding, not as the Buy its own hold makes of it.

        One extra fact read, and only for an order that has a covered line at all.
        """
        lines = self.lines_of(str(order.id))
        decision = self.active_decision(str(order.id))
        frozen = self._frozen_by_line(decision)
        covered_ids = {
            str(line.id) for line in lines if frozen.get(str(line.id)) is not None
        }
        planned_ids = {str(line.id) for line in lines} - covered_ids

        # A COVERED line's own live proposal, composed FIRST and against facts that un-net
        # ITS hold (`replacing`), because that composition is what "Compose again" starts
        # the planner from: a line holding 10 at a sibling has to read as a Reserve of 10,
        # not as the Buy its own hold would make of it. First, because `_facts_for` replaces
        # the request's stock caches and everything below - the walk, the donor lists, the
        # availability printed beside each line - has to read the ones for the walk.
        # N6 (fix round 5): matches `compose_lines`'s own return annotation
        # (`Dict[Any, Tuple[Any, ...]]`) - the value is the walk's NINE-tuple
        # `(components, pool_open, borrow_open, net_open, options, other_group_open,
        # supply_open, share_open, own_group_open)`, not the three-tuple this said before.
        covered_alone: Dict[str, Tuple[Any, ...]] = {}
        if covered_ids:
            amend_facts = self._facts_for(order, lines, replacing=covered_ids)
            for line_id in covered_ids:
                fact = amend_facts[line_id]
                covered_alone.update(
                    self.compose_lines([(line_id, fact, self._unit_key(fact))])
                )

        # THE WALK's own facts: the lines being proposed for are the UNCOVERED ones, so
        # those are the holds that are un-netted, and a covered line's hold stays where it
        # is. `replacing=None` reads "every line of the order", which was true while the
        # sheet proposed for all of them and became a defect the moment it stopped: 25 at a
        # sibling holding 10 for a covered line was offered to the open one as 25, which is
        # a Reserve neither the board (it nets the hold) nor `confirm` (it un-nets only the
        # lines being replaced) would agree to.
        facts = self._facts_for(order, lines, replacing=planned_ids)

        # ONE walk over the order's UNDECIDED lines, in line order, so the shared piles are
        # drawn down once and the lines of one delivery date are planned as the single
        # quantity they are (ladder v6). `lines_of` is already in line order.
        #
        # A COVERED line is not in that walk at all - the captain's standing rule that a
        # decided line is not re-planned, which the board has always kept by leaving such a
        # row out of `proposable`. It was in this one, so 25 in the pool covered the open
        # line of 20 on the board and bought it here. Its claim is already a hold in the
        # facts every other line reads; counting it again in a unit, or against the pool
        # ledger, is the same quantity twice.
        entries = [
            (str(line.id), facts[str(line.id)], self._unit_key(facts[str(line.id)]))
            for line in lines
            if str(line.id) in planned_ids
        ]
        composed = self.compose_lines(entries)
        composed.update(covered_alone)
        units = self.unit_totals(entries)
        # S3 (`PLAN-local-supplier-oi-routing.md`, AC-2.14): ONE call for the whole sheet,
        # covering every line's product - never a per-line lookup.
        origin_by_product = buy_origin_by_product(
            self.db,
            {str(facts[str(line.id)].product_id) for line in lines if facts[str(line.id)].product_id},
        )
        payload_lines: List[Dict[str, Any]] = []
        for line in lines:
            fact = facts[str(line.id)]
            buy_origin = origin_by_product.get(str(fact.product_id), "overseas")
            if str(line.id) in covered_ids:
                # Composed above, on its own: it competes with nobody, having already won.
                units[str(line.id)] = (max(_dec(fact.open_qty), _ZERO), 1)
            unit_qty, unit_line_count = units[str(line.id)]
            if fact.unplannable_reason:
                # Nothing to walk the ladder for: no core line, no open quantity, no
                # location. The line is read out with the reason and nothing else.
                payload_lines.append(
                    self._serialize_line(
                        fact, (), None,
                        unit_qty=unit_qty, unit_line_count=unit_line_count,
                        buy_origin=buy_origin,
                    )
                )
                continue
            components = composed[str(line.id)][0]
            payload_lines.append(
                self._serialize_line(
                    fact, components, frozen.get(str(line.id)),
                    unit_qty=unit_qty, unit_line_count=unit_line_count,
                    options=composed[str(line.id)][4],
                    buy_origin=buy_origin,
                )
            )

        header = self._header_fields(order)
        return {
            "lines_total": len(payload_lines),
            # How much of the order carries a verdict (13.4). The pill stays a whole-order
            # value (AC-A03) and this is the sentence beside it, so "Confirmed" on an order
            # with four of twelve lines decided is not read as a finished order.
            "lines_decided": sum(1 for line in payload_lines if line["decided"]),
            "project_sales_order_id": str(order.id),
            "provisional_ref": order.provisional_ref,
            "autocount_doc_no": order.autocount_doc_no,
            # None, never "None": an adopted order has no project (section 4), and a
            # stringified null is a value the screen renders and links to.
            "project_id": str(order.project_id) if order.project_id else None,
            "project_code": header.get("project_code"),
            "project_name": header.get("project_name"),
            "status": order.status,
            "review_state": self._review_state(order),
            "decision": self._serialize_decision(decision or self.latest_decision(str(order.id))),
            "lines": payload_lines,
        }

    def outside_reserve_window(
        self, fact: _LineFacts, *, as_of: Optional[date] = None
    ) -> bool:
        """Is this line due beyond the window inside which it may still take stock?

        `as_of + the product's lead time + RESERVE_BUFFER_DAYS`, and a line due ON that day is
        INSIDE it - the same boundary rule rung 0's coverage date follows. An UNDATED line is
        never outside anything: there is no delivery date to be beyond, and deciding on a date
        nobody stated is exactly the guess the rest of this engine refuses to make.

        PUBLIC because the verdict has three readers and must never have three answers: the
        ladder that skips two rungs on it, the sheet that offers no donor beside it, and the
        board that has to say so on the row and in the trail. The board read it as "nothing
        free at L, and borrowing is possible from ..." on SO414341 precisely because it had no
        way to ask.
        """
        if fact.required_date is None:
            return False
        window_end = reserve_window_end(
            as_of or date.today(), self._lead_time_days(fact.product_id)
        )
        return fact.required_date > window_end

    def lead_times(self, product_ids: Iterable[str]) -> Dict[str, Optional[int]]:
        """`_lead_time_days` for many products in TWO queries, filling the same memo.

        The single-product path memoizes, which is right for a board of 300 lines over a
        handful of products and wrong for the Stock Debt view, where the page is a thousand
        products and one round trip each is two thousand. Same two sources in the same order
        (measured first, stated second) - this is the batched door onto that rule, not a
        second copy of it, which is why `_lead_time_days` reads the memo this fills.
        """
        wanted = {str(pid) for pid in product_ids if pid}
        missing = [pid for pid in wanted if pid not in self._lead_time_memo]
        if missing:
            measured = {
                str(pid): days
                for pid, days in self.db.query(
                    SupplierPerformance.product_id,
                    func.min(SupplierPerformance.avg_lead_time_days),
                )
                .filter(
                    SupplierPerformance.product_id.in_(missing),
                    SupplierPerformance.avg_lead_time_days.isnot(None),
                )
                .group_by(SupplierPerformance.product_id)
                .all()
            }
            stated = {
                str(pid): days
                for pid, days in self.db.query(
                    ProductSupplier.product_id,
                    func.min(ProductSupplier.standard_lead_time_days),
                )
                .filter(ProductSupplier.product_id.in_(missing))
                .group_by(ProductSupplier.product_id)
                .all()
            }
            for pid in missing:
                days = measured.get(pid)
                if days is None:
                    days = stated.get(pid)
                self._lead_time_memo[pid] = None if days is None else max(int(days), 0)
        return {pid: self._lead_time_memo[pid] for pid in wanted}

    def _lead_time_days(self, product_id: Optional[str]) -> Optional[int]:
        """How long buying this product actually takes, in days, or `None` for "nobody says".

        Two sources, in this order, and both are the FASTEST supplier rather than an average
        across them: the question is whether purchasing could still get it here in time, and
        that is answered by the supplier they would use.

        1. `scm.supplier_performance.avg_lead_time_days` - what the last orders MEASURED. It
           is the honest answer and it is also empty on this book today (0 rows), which is why
           it cannot be the only one.
        2. `product_suppliers.standard_lead_time_days` - what the supplier agreement STATES.
           17,667 rows on the live book, so this is the source that actually answers.

        `None` falls through to `DEFAULT_LEAD_TIME_DAYS`, and the caller never invents one.
        Memoized per request: a board of 300 lines asks about the same handful of products.
        """
        if not product_id:
            return None
        if product_id in self._lead_time_memo:
            return self._lead_time_memo[product_id]
        measured = (
            self.db.query(func.min(SupplierPerformance.avg_lead_time_days))
            .filter(
                SupplierPerformance.product_id == product_id,
                SupplierPerformance.avg_lead_time_days.isnot(None),
            )
            .scalar()
        )
        stated = (
            measured
            if measured is not None
            else (
                self.db.query(func.min(ProductSupplier.standard_lead_time_days))
                .filter(ProductSupplier.product_id == product_id)
                .scalar()
            )
        )
        days = None if stated is None else max(int(stated), 0)
        self._lead_time_memo[product_id] = days
        return days

    def walk(
        self,
        fact: _LineFacts,
        *,
        pool_free_left: Optional[Mapping[str, Decimal]] = None,
        borrow_left: Optional[Mapping[str, Decimal]] = None,
        as_of: Optional[date] = None,
        pools_net_left: Optional[Decimal] = None,
        other_group_left: Optional[MutableMapping[str, Decimal]] = None,
        supply_left: Optional[MutableMapping[str, Decimal]] = None,
        own_group_left: Optional[MutableMapping[str, Decimal]] = None,
        pool_share_left: Optional[MutableMapping[str, Decimal]] = None,
        own_arrival_left: Optional[MutableMapping[str, Decimal]] = None,
    ):
        """`compose_line` plus the five OPTIONS behind it (R36, AC-S3-14).

        Two names, one walk: most callers want what to promise, and the board also wants
        every step the ladder asked about with the date it would have delivered on.
        """
        # The ATP reserve window (`front_planning_engine`): a line due beyond
        # `as_of + lead time + buffer` takes NO STOCK, so none of the candidate lists -
        # which cost queries - is built for it at all. The trail still states every step
        # with the window as its reason, so "not walked" is visible rather than silent.
        outside_window = self.outside_reserve_window(fact, as_of=as_of)
        group_take: List[Dict[str, Any]] = []
        own_arrival_candidates: List[Dict[str, Any]] = []
        other_group: List[Dict[str, Any]] = []
        order_borrow: List[Dict[str, Any]] = []
        supply_borrow: List[Dict[str, Any]] = []
        pool_borrow: List[Dict[str, Any]] = []
        pools: List[Dict[str, Any]] = []
        own_offer = _ZERO
        other_group_short: Dict[str, Decimal] = {}
        # S5: sized inside `if not outside_window` below; kept defined out here too, since
        # the deferred own-arrival charge after `walk_line` returns reads them
        # unconditionally.
        credit_qty = _ZERO
        credit_tier1_qty = _ZERO
        credit_tier2: List[Tuple[str, Decimal, Optional[str]]] = []
        if not outside_window:
            pools = self._pool_chain(fact, pool_free_left=pool_free_left)
            group_take, other_group, own_offer, other_group_short = (
                self.use_candidates_for(
                    fact, as_of=as_of, other_left=other_group_left,
                    own_left=own_group_left,
                )
            )
            # R7: a candidate OF ITS OWN, drawn separately from the assignment's own
            # group-take pile (`use_candidates_for`'s own return stays untouched for every
            # other caller - the recheck at confirm time in particular must not see stock
            # counted twice). `walk_line` draws it ahead of `group_take_candidates` and, as
            # a sub-unit like `pool_share`, lets it cover PART of the line - and then
            # SUBTRACTS what it drew from the ordinary candidates at that same bin
            # (AC-S3-11), because the credit is a claim ON that pile, not a second pile
            # beside it: a line needing 80 with 40 received and 40 on hand composes Reserve
            # 40 plus Buy 40, never Reserve 80.
            #
            # S5: `_own_arrival_credit_components` only SIZES this candidate here - it does
            # NOT charge `own_arrival_left` yet. `walk_line`'s own pool-share sub-step (0)
            # runs BEFORE the own-arrival sub-step and may already cover part of the line,
            # so what `walk_line` actually draws off this candidate (below, after it
            # returns) can be smaller than the theoretical figure computed here - charging
            # the theoretical amount would overcharge the ledger a sibling line at the same
            # bin reads afterwards.
            credit_qty, credit_po, credit_tier1_qty, credit_tier2 = (
                self._own_arrival_credit_components(
                    fact, own_arrival_left=own_arrival_left
                )
            )
            if credit_qty > _ZERO:
                # #1362: the sentence states two facts apart - what landed for this line
                # (tier 1 plus the tier-2 spare, capped by what the line needs) and how
                # much of it is still free at the bin (`credit_qty`, that figure capped by
                # what is left of this product's on hand there in this walk). Read as one
                # number, "40 landed" was taken to mean the SPO carried 40.
                landed = min(
                    credit_tier1_qty
                    + sum((spare for _ref, spare, _po in credit_tier2), _ZERO),
                    max(_dec(fact.open_qty), _ZERO),
                )
                own_arrival_candidates = [{
                    "location": fact.own_code,
                    "qty": credit_qty,
                    "source": "own_arrival",
                    "supply_document": credit_po,
                    "landed_qty": landed,
                    "free_qty": credit_qty,
                    # #1362 item 4: a sibling's spare is named as that sibling's purchase.
                    "landed_text": self.own_arrival_landed_text(
                        fact, landed, credit_tier1_qty, credit_tier2
                    ),
                }]
            order_borrow = self.order_borrow_candidates_for(
                fact, as_of=as_of, borrow_left=borrow_left
            )
            supply_borrow = self.supply_borrow_candidates_for(
                fact, as_of=as_of, supply_left=supply_left
            )
            pool_borrow = self.order_borrow_candidates_for(
                fact, as_of=as_of, borrow_left=borrow_left, pool=True
            )
        pools_net = fact.pools_net if pools_net_left is None else pools_net_left
        settings = self._fulfilment_settings()
        walked = walk_line(
            open_qty=fact.open_qty,
            line_no=fact.line_no,
            required_date=fact.required_date,
            as_of=as_of,
            lead_time_days=self._lead_time_days(fact.product_id),
            fulfilment_location=fact.own_code,
            transfer_days=int(settings.get("transfer_days") or 0),
            group_code=fact.group_code,
            is_dealer_hot_selling=fact.is_dealer_hot_selling,
            is_project_hot_selling=fact.is_project_hot_selling,
            pools=pools,
            is_discontinued=fact.is_discontinued,
            reorder_coverage_until=self._reorder_coverage_until(),
            group_take_candidates=group_take,
            own_arrival_candidates=own_arrival_candidates,
            other_group_candidates=other_group,
            # R-M (3 Sep 2026): the other groups whose own book is short, so step 1's row
            # can say why it gave nothing rather than printing a bare 0.
            other_group_short=other_group_short,
            order_borrow_candidates=order_borrow,
            supply_borrow_candidates=supply_borrow,
            pool_borrow_candidates=pool_borrow,
            outside_reserve_window=outside_window,
            # Step 1's own number, date-aware since v7.1 (R24): what the ASSIGNMENT gave
            # this unit by its own date, which each sentence names as the figure it is a
            # share of.
            group_offer=own_offer if fact.group_code else None,
            pools_net=pools_net,
            # LADDER V8 (R-B): the two policy numbers the share rule reads, off the SAME
            # active row `transfer_days` rides. Read here rather than defaulted in the
            # engine so the admin screen and the walk cannot come to different views of
            # how much of the pool a project may take.
            pool_share_pct=settings.get("pool_share_pct"),
            immediate_window_days=settings.get("immediate_window_days"),
            # LADDER V8 (R-B): what is LEFT of each POOL's project share in this walk, by
            # pool location. The share is a share of the PILE - "the pool keeps half of
            # itself for dealers" - so two lines of one board cannot each be offered half of
            # it, which is how a pool of 20 came to lend all 20 to two lines of 10. Keyed by
            # POOL (review round 1, S1): BRW's share being spent says nothing about MWH's,
            # and one number across the five pools spent them all at once.
            pool_share_left=pool_share_left,
        )
        # S5: charge `own_arrival_left` with what `walk_line` ACTUALLY drew off the
        # own-arrival candidate above, never the theoretical figure it was sized with -
        # the pool-share sub-step may have already covered part of the line, leaving less
        # than `credit_qty` for own-arrival to draw. Tier 1 (this line's own PO) first,
        # only the rest off the siblings' tier-2 spare, same order as before (MB2).
        if own_arrival_left is not None and credit_qty > _ZERO:
            # S-c: SUMMED over every own-arrival component, not just the first - a line
            # `walk_line` splits across more than one component (e.g. a partial pool-share
            # sub-step beside an own-arrival one, or the candidate itself split) leaves a
            # `next(...)` read blind to every component after the first.
            drawn = sum(
                (
                    _dec(component.qty)
                    for component in walked.components
                    if getattr(component, "source", None) == "own_arrival"
                ),
                _ZERO,
            )
            if drawn > _ZERO:
                self._charge_own_arrival_credit(
                    fact, drawn, credit_tier1_qty, credit_tier2, own_arrival_left
                )
        return walked

    # ----------------------------------------------------- ladder v6: order units

    def compose_lines(
        self,
        entries: Sequence[Tuple[Any, _LineFacts, Any]],
        *,
        as_of: Optional[date] = None,
    ) -> Dict[Any, Tuple[Any, ...]]:
        """Compose a WHOLE WALK: every entry in the order it is walked, keyed by its caller's
        own key, with the shared piles drawn down once as the walk passes them.

        `entries` is `(key, fact, unit_key)` in walk order, and the answer for each key is
        `(components, pool_open, borrow_open, net_open, options, other_group_open,
        supply_open, share_open, own_group_open)` - the composition, the five options behind
        it (R36), and what EVERY shared pile held when this LINE was reached: every site
        pool's free FLOOR by pool location (AC-N.12 - the asking bin's own pool used to be
        the only one carried), the cross-group donors by warehouse id, the five site
        pools' NET (the number that bounds rung 2, `pool_reserve_capacity`), what is left of
        each site pool's PROJECT SHARE (v8, R-B), and what is left of the unit's own
        ownership-group pile (v8, R-E). The board's proof states them from HERE rather than
        re-reading the live figures, which is how question 3 came to say "free stock at
        DC1-NT, within the limit" beside a Buy the ledger had just forced - and, until the
        last two were handed over, how question 1 came to offer the 135 at BRW-BB to the
        1,305 line that the 135 line had already taken (C3, code review round 4).

        THE UNIT (ladder v6, `PLAN-scm-order-unit-ladder-v6.md`). Entries sharing a
        `unit_key` are ONE quantity to plan: the captain, reading SO381895's lines 31 and 32
        - same item, same location, same delivery date - "this is 1 order as a whole, so we
        should look at the order as a whole instead of line by line". The engine's whole-line
        rule then applies to the unit, so 10 and 20 are covered from stock together or bought
        together, never "31 borrows and 32 buys". A unit's position in the walk is where its
        FIRST member appears (the board serves by rank, the sheet by line order, and neither
        ordering is disturbed); its members are filled in LINE order, so the sheet, the board
        and the freeze split one composition the same way.

        A unit of one line is `compose_line` exactly as before, on the fact itself.

        THE SHARED PILES are drawn once per unit, and this is the only place any of the
        ledgers lives: every site pool's free floor (`pool_free_left`), the cross-group
        donors (`borrow_left`), and the site pools' net (`pools_net_left`, per product -
        the net is ONE pile across all five pools, section 1d). All three callers used to
        keep the pool one and none of them kept the donor one - which is how four delivery
        dates of SO381895 were each proposed a Borrow of 10 from a location holding 10 in
        all, and why `confirm` refused every line but the first. The net one came the same
        day, from the same order one rung up: three delivery dates of TPE-9204 were each
        offered "30 of the 31 the site pools net between them", because the free-stock
        ledger stood at 3365 while the 31 was read fresh off the fact every time.

        A fact with an `unplannable_reason` is not walked at all (empty tuple), as before: a
        mirror line with no reconciled AutoCount line has no open quantity to promise
        against.
        """
        out: Dict[Any, Tuple[Any, ...]] = {}
        units: Dict[Any, List[Tuple[Any, _LineFacts]]] = {}
        walk: List[Any] = []
        for key, fact, unit_key in entries:
            if fact.unplannable_reason:
                out[key] = ((), None, {}, None, (), {}, {}, {}, {})
                continue
            if unit_key not in units:
                units[unit_key] = []
                walk.append(unit_key)
            units[unit_key].append((key, fact))

        # ONE assignment read for the WHOLE walk (R21). Warmed here rather than lazily per
        # line, because `planning_assignments` batches by product and a board of 300 lines
        # over six products must pay six products' worth of reads once, not once per line.
        self._walk_as_of = as_of or date.today()
        self.planning_assignments(
            [
                fact.product_id
                for members in units.values()
                for _key, fact in members
                if fact.product_id
            ],
            as_of=self._walk_as_of,
        )
        # R7's own-arrival reads, batched for the WHOLE walk (review round, query-count):
        # `_own_arrival_credit_components` used to look up its own representative core
        # line by id lazily, one round trip per FACT it was asked about - a board of 76
        # lines split into ten weekly units still paid ten identical-shaped queries for
        # ten different rows. Seeded here off every entry's own core line id, before the
        # per-unit walk below ever calls it.
        self._prefetch_own_arrival(entries)

        # product id -> POOL LOCATION -> what is LEFT of that pool's FREE FLOOR in this
        # walk (AC-N.12, the R-N leftover). One ledger for EVERY pool, the asking bin's own
        # included: the old one carried the own pool alone (keyed by product and pool id)
        # and every other pool's floor was re-read live per line. Under R-N step 0 walks
        # the whole chain, so two lines of one walk were each offered the whole of another
        # site pool's floor and the walk promised 10 units off a pool holding 5.
        pool_free_left: Dict[str, Dict[str, Decimal]] = {}
        # product id -> what the five site pools still net between them in this walk.
        # Keyed by PRODUCT alone: the net is one pile across every pool (section 1d), so a
        # draw at any of them is a draw on the same number.
        net_left: Dict[str, Decimal] = {}
        # product id -> POOL LOCATION -> what is LEFT of that pool's PROJECT SHARE in this
        # walk (ladder v8, R-B). The share is a share of the PILE, not of each line: without
        # this ledger a pool of 20 was offered as 10 to one line and 10 to the next, and lent
        # all of it. Keyed by POOL as well as product (review round 1, S1) because each pool
        # has its own allowance under R-L - BRW's share being spent leaves MWH's untouched -
        # while `net_left` above stays ONE number, because the five pools are one pile.
        share_left: Dict[str, Dict[str, Decimal]] = {}
        # product id -> DONOR LINE id -> what is still borrowable from it in this walk
        # (v7.1, AC-S3-9: a borrow takes one order's committed quantity, so the ledger is
        # keyed by that order's line and not by the bin it happens to sit in).
        borrow_left: Dict[str, Dict[str, Decimal]] = {}
        # product id -> WAREHOUSE CODE -> what another project group's free pile still has
        # on the table in this walk (R40, step 1's offer half). See `use_candidates_for`.
        # It also carries one entry per LENDING GROUP (`_group_budget_key`): R-M's cap is a
        # statement about the group, so the group's spare book is spent once across every
        # bin it owns and every unit of the board, not once per bin.
        other_group_left: Dict[str, Dict[str, Decimal]] = {}
        # product id -> EVENT KEY -> how much of one incoming document this walk has already
        # DRAWN (step 3, `supply_borrow_candidates_for`). Keyed by the DOCUMENT and not by a
        # bin or a donor: R33 gives the whole unit to one document, so the document is the
        # thing two units of a board compete for. What is DRAWN rather than what is LEFT,
        # because what is left of a document is personal to the asker - one held by the
        # asker's own order is worth nothing to it and everything to the next order along.
        supply_left: Dict[str, Dict[str, Decimal]] = {}
        # product id -> LOCATION CODE -> what is left of that location's physical on hand
        # for R7's own-arrival credit, this walk (AC-S3-4), plus one entry per SIBLING
        # SPARE (`_own_arrival_spare_key`, MB2). Seeded lazily, off `LocationNet.on_hand`,
        # the first line that asks - the SAME physical pile the walk's ordinary group-take
        # rung draws from, so a later line of the SAME order at the SAME bin cannot be
        # offered units an earlier line already took as credit, and (AC-S3-11) so an
        # ORDINARY draw at that bin by ANY line of this walk leaves the credit less to give.
        own_arrival_left: Dict[str, Dict[str, Decimal]] = {}
        for unit_key in walk:
            arrived = units[unit_key]
            # The unit fact is the FIRST-ARRIVING member's, not the lowest-numbered one:
            # `pool_free` and the rest are a reading taken at the moment the walk reached
            # this unit, and the walk reached it when its first member came up. It carries
            # the unit's CONTEXT - every member's core line id - which is what makes step 1
            # read the pile the assignment gave the unit rather than one line's share of it.
            unit_fact = self._unit_fact(arrived)
            # LADDER V8 (R-E): the unit's contributing lines walk ONE AT A TIME, smallest
            # first, each fed the piles the previous one left. v6 walked the unit as one
            # quantity and split the answer back in line order; on SO419208 that read "Buy
            # 1440" while 135 sat on the floor at BRW-BB, because 145 could not cover 1440
            # whole. The unit is still one board cell - what changed is the walk inside it.
            members = self._members_in_walk_order(arrived)
            # Whether or not this location names a pool of its own: rung 2 draws the OTHER
            # site pools too (`_pool_chain`'s `rest`), and a bare site code with no
            # `pool_warehouse_id` (BRW-IB on the live book) still draws BRW through them.
            product_id = unit_fact.product_id
            if product_id and product_id not in net_left:
                net_left[product_id] = _dec(unit_fact.pools_net)
            if product_id:
                # Seeded off the CHAIN, which is what the walk itself reads (`_pool_chain`:
                # this line's own site pool first, then the others by on hand), one entry per
                # POOL. A bare site code names no pool of its own - `BRW-IB` on the live book
                # - and reading `fact.pool_available` would seed 0 for a line the walk offers
                # BRW's share to. Seeded once per pool and then drawn down: a pool nobody
                # asks about costs nothing.
                shares = share_left.setdefault(product_id, {})
                floors = pool_free_left.setdefault(product_id, {})
                pct = self._fulfilment_settings().get("pool_share_pct")
                for entry in self.pool_chain_for(unit_fact):
                    code = str(entry.get("location") or "")
                    if not code:
                        continue
                    if code not in shares:
                        shares[code] = available_for_project(
                            entry.get("available"), unit_fact.pools_net, pct
                        )
                    # The FLOOR beside the share, off the same chain and for the same
                    # reason (AC-N.12): the share is what the policy lets a pool lend and
                    # the floor is what it physically holds, and a pool whose floor is the
                    # lower of the two is spent by the first line that reaches it.
                    #
                    # THE OWNING UNIT'S OWN NETTING NEVER LOSES TO AN EARLIER SEED (review
                    # fix round, S2): `unit_fact.pool_free` already nets OUT the claims
                    # ranked ahead of this unit at its OWN pool, but an earlier unit at a
                    # DIFFERENT site reads that same pool as one of its OTHERS and seeds it
                    # RAW - unclaimed. Seeding once (`if code not in floors`) for every pool
                    # then kept that raw figure for good, so the unit that actually owns the
                    # pool lost its own claim netting to whichever unit's walk happened to
                    # reach the ledger first. The owning unit always intersects the ledger
                    # with its OWN reading instead of only seeding once.
                    if code == unit_fact.pool_code:
                        floors[code] = min(
                            floors.get(code, unit_fact.pool_free), unit_fact.pool_free
                        )
                    elif code not in floors:
                        floors[code] = max(_dec(entry.get("free")), _ZERO)
            # A fact with no product cannot borrow at all (`_cross_group_borrow_candidates`
            # refuses it by rule), so it keeps no ledger either - one bucket keyed by the
            # empty string would pool every such line's donors together.
            donors = (
                borrow_left.setdefault(unit_fact.product_id, {})
                if unit_fact.product_id
                else None
            )
            # Step 1's OFFER half needs a ledger of its own since R40 (`use_candidates_for`):
            # the walk no longer draws another group's pile down, so without one every unit
            # of a board would be offered the same free stock and `confirm` would refuse all
            # but the first. Keyed by product then warehouse code, because that is what the
            # offer names.
            other_group = (
                other_group_left.setdefault(unit_fact.product_id, {})
                if unit_fact.product_id
                else None
            )
            supply = (
                supply_left.setdefault(unit_fact.product_id, {})
                if unit_fact.product_id
                else None
            )
            # R7's own-arrival credit ledger (AC-S3-4): persists across every unit of the
            # walk that shares this product, the same as `other_group`/`supply` above - a
            # later line at the same bin must find what an earlier one already drew off it.
            own_arrival = (
                own_arrival_left.setdefault(unit_fact.product_id, {})
                if unit_fact.product_id
                else None
            )
            # THE UNIT'S OWN ownership-group pile, and nobody else's (R-E). `use_candidates_for`
            # hands every member of the unit the SAME draw - what the assignment gave the
            # unit's lines by their own date - so without a ledger the second member would be
            # offered stock the first has just taken. Fresh per unit: two units read two
            # different assignment rows, and one ledger across them would deduct a draw twice.
            own_group: Dict[str, Decimal] = {}
            for key, member in members:
                fact = self._member_fact(unit_fact, member)
                # Captured BEFORE this line's own draw: the proof states what each pile held
                # when the LINE was reached, and reading it back afterwards would state what
                # was left instead.
                floors_open = pool_free_left.get(product_id) if product_id else None
                net_open = net_left.get(product_id) if product_id else None
                share_open = share_left.get(product_id) if product_id else None
                borrow_open = dict(donors) if donors is not None else {}
                other_group_open = dict(other_group) if other_group is not None else {}
                supply_open = dict(supply) if supply is not None else {}
                # The two ledgers v8 added, in the same shape and for the same reason (C3):
                # what is left of each site pool's project SHARE, and what is left of the
                # UNIT's own ownership-group pile. Copies, because the walk below draws both
                # down in place and the proof has to state what this line was offered.
                share_snapshot = dict(share_open) if share_open is not None else {}
                # The floors this LINE was offered, in the same shape and for the same
                # reason: the board's proof states what each pool held when the line was
                # reached, and the walk below draws the ledger down in place.
                pool_open = dict(floors_open) if floors_open is not None else {}
                own_group_open = dict(own_group)
                walked = self.walk(
                    fact,
                    pool_free_left=floors_open,
                    borrow_left=donors,
                    as_of=as_of,
                    pools_net_left=net_open,
                    other_group_left=other_group,
                    supply_left=supply,
                    own_group_left=own_group,
                    pool_share_left=share_open,
                    own_arrival_left=own_arrival,
                )
                components = walked.components
                if floors_open is not None:
                    # Every POOL rung draw, at whichever pool gave it (AC-N.12). The old
                    # drawdown matched `fact.pool_code` and so knew about the asking bin's
                    # own pool alone; step 0 answers from the whole chain since R-N.
                    for component in components:
                        if component.kind != RESERVE or component.rung != RUNG_POOL:
                            continue
                        code = component.source_location
                        if not code:
                            continue
                        floors_open[code] = max(
                            floors_open.get(code, _ZERO) - component.qty, _ZERO
                        )
                if product_id and net_open is not None:
                    # EVERY pool draw, not only the own pool's: the step may spill into the
                    # other site pools, and all of it comes off the one net.
                    drawn_net = sum(
                        (
                            component.qty
                            for component in components
                            if component.kind == RESERVE and component.rung == RUNG_POOL
                        ),
                        _ZERO,
                    )
                    net_left[product_id] = max(net_open - drawn_net, _ZERO)
                if share_open is not None:
                    # Per POOL, off what each one actually gave (R-L): a draw at MWH does
                    # not spend BRW's share, and the free half is what the share bounds -
                    # a pool BORROW (R34) is a later order's stock, not the pile's share.
                    for component in components:
                        if component.kind != RESERVE or component.rung != RUNG_POOL:
                            continue
                        code = component.source_location
                        if not code:
                            continue
                        share_open[code] = max(
                            share_open.get(code, _ZERO) - component.qty, _ZERO
                        )
                if donors is not None:
                    self._draw_donors(donors, fact, components)
                for component in components:
                    code = component.source_location
                    if component.rung != RUNG_GROUP_TAKE or not code:
                        continue
                    if (
                        own_arrival is not None
                        and component.kind == RESERVE
                        and getattr(component, "source", None) != "own_arrival"
                    ):
                        # AC-S3-11 (security review, 21 Sep 2026): the PHYSICAL pile at the
                        # bin, charged by every ORDINARY group-take Reserve of this walk as
                        # well as by the credit. The credit is a claim on the same units the
                        # ordinary rung hands out, so a ledger only the credit spends lets
                        # one order's assignment and another order's credit each cover the
                        # whole of one floor. The credit's OWN component is skipped here:
                        # `self.walk` above already charged the ledger (S5's
                        # `_charge_own_arrival_credit`, called with what `walk_line`
                        # ACTUALLY drew, not the theoretical figure `_own_arrival_credit_
                        # components` sized the candidate with) before this drawdown loop
                        # ever runs, so charging it again here would spend those units
                        # twice. Seeded off `LocationNet.on_hand`, the same figure
                        # `_own_arrival_credit_components` seeds it with; a bin this fact's
                        # netting says nothing about is left alone rather than invented as
                        # zero.
                        on_hand = self._on_hand_at(fact, code)
                        if on_hand is not None:
                            left = _dec(own_arrival.setdefault(code, on_hand))
                            own_arrival[code] = max(left - component.qty, _ZERO)
                    lender = sales_agent_service.group_of_warehouse_code(code)
                    mine = lender == fact.group_code
                    ledger = own_group if mine else other_group
                    if ledger is None:
                        continue
                    # The OWN ledger keeps a bin's floor and its water apart (`_pile_key`),
                    # because the next line of this unit may only have what this one left of
                    # THAT half: a Reserve is stock on a shelf, a Timely SPO is a promise on
                    # the water, and spending one does not spend the other.
                    pile = _pile_key(code, component.kind == TIMELY_SPO, mine)
                    if (
                        getattr(component, "source", None) == "own_arrival"
                        and pile not in ledger
                    ):
                        # R7 + AC-S3-11: the credit DOES spend the unit's own pile where
                        # that pile was offered one (the units are the same units), but it
                        # is drawn outside `use_candidates_for`, so it must not invent a
                        # budget of zero for a bin the assignment never offered this unit
                        # at all - the same care the lending-group budget below takes.
                        continue
                    ledger[pile] = max(ledger.get(pile, _ZERO) - component.qty, _ZERO)
                    if mine or not lender:
                        continue
                    # R-M: and off the LENDING GROUP's own budget, which the bin key cannot
                    # stand in for - the next unit of this walk may reach the same group at
                    # a different bin on a different date. Only where the budget was seeded
                    # (`_other_group_free_at_own_date` read this group), so a component from
                    # a rung that never asked about it cannot invent a budget of zero.
                    budget = _group_budget_key(lender)
                    if budget in ledger:
                        ledger[budget] = max(ledger[budget] - component.qty, _ZERO)
                if supply is not None:
                    # Counted up by what was COMPOSED, never by what was merely offered: the
                    # step offers a whole document and takes only what the line needed.
                    for component in components:
                        key_of = getattr(component, "supply_key", None)
                        if not key_of:
                            continue
                        supply[key_of] = supply.get(key_of, _ZERO) + component.qty
                out[key] = (
                    components,
                    pool_open,
                    borrow_open,
                    net_open,
                    walked.options,
                    other_group_open,
                    supply_open,
                    share_snapshot,
                    own_group_open,
                )
        return out

    def _members_in_walk_order(
        self, members: Sequence[Tuple[Any, _LineFacts]]
    ) -> List[Tuple[Any, _LineFacts]]:
        """A unit's members in WALK order (R-E): smallest quantity first, then line number.

        The captain's own case is why the quantity leads: SO419208's unit is 1,305 and 135
        against 135 free, and the small line is the one the pile can actually cover. Walking
        by line number would hand the 135 to the 1,305 line, cover nothing whole, and buy
        both - which is the answer v6 gave and the one this rule replaces.

        The line number breaks a tie (two lines of 100 on one date), and the caller's own
        key closes it, so two runs of one board cannot order a unit two ways.
        """
        return sorted(
            members,
            key=lambda member: (
                max(_dec(member[1].open_qty), _ZERO),
                member[1].line_no is None,
                member[1].line_no or 0,
                str(member[0]),
            ),
        )

    def _member_fact(self, unit_fact: _LineFacts, member: _LineFacts) -> _LineFacts:
        """One contributing line of a unit, carrying the UNIT's own context (R-E).

        The member's own quantity, date, line number and mirror row - and the unit's core
        line ids, because step 1's date-aware pile is what the ASSIGNMENT gave those lines
        together (`_drawn_at_own_date`). Without them a member would read only its own
        share of the assignment, and the two members would each be offered a piece of a
        pile the walk's own ledger is there to divide.

        A unit of one is its own fact untouched: `_unit_fact` has already stamped it.
        """
        if unit_fact is member:
            return member
        # #1362 item 6: the member's OWN core line leads the list, so `_core_id_of` still
        # names this line and not the unit's first (the own-arrival credit reads it).
        own = self._core_id_of(member)
        ids = list(unit_fact.unit_core_line_ids)
        if own in ids:
            ids.remove(own)
            ids.insert(0, own)
        return dataclass_replace(member, unit_core_line_ids=ids)

    @staticmethod
    def unit_totals(
        entries: Sequence[Tuple[Any, _LineFacts, Any]]
    ) -> Dict[Any, Tuple[Decimal, int]]:
        """What each entry's planning UNIT is, as the payload states it: the unit's whole
        open quantity and how many lines it is.

        Beside `compose_lines` and over the same `entries`, so the sheet and the board cannot
        group one order two ways. An unplannable line is a unit of itself - it was not walked
        with anybody (`compose_lines`), and saying it was would describe a plan nobody made.
        """
        totals: Dict[Any, Decimal] = {}
        counts: Dict[Any, int] = {}
        for _key, fact, unit_key in entries:
            if fact.unplannable_reason:
                continue
            totals[unit_key] = totals.get(unit_key, _ZERO) + max(
                _dec(fact.open_qty), _ZERO
            )
            counts[unit_key] = counts.get(unit_key, 0) + 1
        out: Dict[Any, Tuple[Decimal, int]] = {}
        for key, fact, unit_key in entries:
            if fact.unplannable_reason:
                out[key] = (max(_dec(fact.open_qty), _ZERO), 1)
                continue
            out[key] = (totals[unit_key], counts[unit_key])
        return out

    @staticmethod
    def _unit_key(fact: _LineFacts) -> Tuple[Any, ...]:
        """This line's planning unit ON ONE ORDER: item, fulfilment location, delivery date.

        The sales order is not in the key here because both callers of this helper walk a
        single order's own lines. The BOARD walks several and states its own key with the
        order in it: two customers asking for the same item at the same location on the same
        day are two promises, and planning them as one quantity would buy or reserve for
        somebody else's order (R1).
        """
        return (
            fact.product_id,
            str(fact.warehouse.id) if fact.warehouse else None,
            fact.required_date,
        )

    def _unit_checks(
        self,
        lines: Sequence[ProjectSalesOrderLine],
        facts: Dict[str, _LineFacts],
        *,
        covered: Optional[Set[str]] = None,
    ) -> Dict[str, "_UnitCheck"]:
        """The unit each line of ONE ORDER was proposed in, keyed by mirror line id.

        The confirmation's own view of `compose_lines`' grouping, built from the same
        `_unit_key` and the same `_unit_fact`, so what the recheck seeds its ledgers from
        and what the ladder was asked about cannot come apart - which is exactly what made a
        proposal unconfirmable. ONE `_UnitCheck` per unit, shared by its members.

        `covered` names the lines a decision still holds and nobody is replacing: out of
        every unit, because they are out of the walk that proposed for the others.
        """
        skip = covered or set()
        units: Dict[Any, List[Tuple[Any, _LineFacts]]] = {}
        for line in lines:
            key = str(line.id)
            fact = facts.get(key)
            if fact is None or key in skip or fact.unplannable_reason:
                continue
            units.setdefault(self._unit_key(fact), []).append((key, fact))
        out: Dict[str, "_UnitCheck"] = {}
        for members in units.values():
            unit_fact = self._unit_fact(members)
            check = _UnitCheck(fact=unit_fact, timely_left=unit_fact.timely_qty)
            for key, _fact in members:
                out[str(key)] = check
        return out

    def _unit_fact(self, members: Sequence[Tuple[Any, _LineFacts]]) -> _LineFacts:
        """The one line the engine is asked about for a unit of several.

        The first member, carrying the unit's WHOLE open quantity and the group's offer
        against it: `max(group net + the unit's demand, 0)` is `_group_offer`'s own rule
        applied to the quantity actually being planned, so a group that can cover 30 offers
        30 to the unit rather than 10 to one line and 20 to another out of the same pile.

        `timely_qty` is recomputed with it, exactly as `_apply_group_nets` does for a line:
        the water share is what question 1 offers, and question 1 now offers the unit's
        number. The composition itself does not need it (`_group_take_candidates` derives
        the water from `group_offer` on whatever fact it is handed), but the CONFIRMATION
        does - it caps a posted Timely SPO against this figure, and a unit fact carrying the
        first member's share would refuse the split it had just proposed.
        """
        first = members[0][1]
        # The unit's CORE line ids, from whichever half of the fact carries one: the sheet
        # reads a mirror (`core`), the board a bare demand row (`unit_core_line_ids`, set by
        # `demand_facts`). Never overwritten with an empty list - that is how a board fact
        # lost its own line and read step 1 as empty on every row.
        member_ids = [
            line_id
            for line_id in (self._core_id_of(fact) for _key, fact in members)
            if line_id
        ]
        if len(members) == 1:
            if member_ids:
                first.unit_core_line_ids = member_ids
            return first
        total = sum((max(_dec(fact.open_qty), _ZERO) for _key, fact in members), _ZERO)
        # The unit's own STILL-OWED total, summed separately: `_group_offer` un-nets this
        # one and not the ask, for the reason its docstring gives. On a unit whose members
        # have no delivery the two are the same number, which is nearly every unit.
        owed_total = sum((max(_dec(fact.owed_qty), _ZERO) for _key, fact in members), _ZERO)
        unit = dataclass_replace(
            first,
            open_qty=total,
            owed_qty=owed_total,
            unit_core_line_ids=member_ids,
            group_offer=(
                max(first.group_net + owed_total, _ZERO)
                if first.group_code
                else first.group_offer
            ),
        )
        if unit.group_code:
            # The unit's share of what the group has arriving in time, exactly as
            # `_apply_group_nets` stamps it on a line (`timely_qty_before_group_net`, the
            # unnetted figure, is already copied by `replace` above).
            unit.timely_qty = sum(
                (
                    _dec(candidate.get("qty"))
                    for candidate in self._group_take_candidates(unit)
                    if candidate.get("water")
                ),
                _ZERO,
            )
        return unit

    @staticmethod
    def _core_id_of(fact: _LineFacts) -> Optional[str]:
        """This fact's own CORE sales-order line id, whichever caller built it."""
        if fact.core is not None:
            return str(fact.core.id)
        return fact.unit_core_line_ids[0] if fact.unit_core_line_ids else None

    def _draw_donors(
        self,
        donors: Dict[str, Decimal],
        fact: _LineFacts,
        components: Sequence[Component],
    ) -> None:
        """Take this unit's Borrow off the walk's donor ledger (AC-S3-9).

        KEYED BY DONOR LINE since v7.1, not by warehouse: a borrow takes ONE order's
        committed quantity, and two orders at one bin are two donors. Seeded from what the
        assignment says that donor line holds on hand, the first time the walk borrows from
        it - so a donor nobody borrows from costs nothing, and a donor two units both name
        is offered the second time only what the first one left.
        """
        for component in components:
            if component.kind != BORROW or not component.donor_core_line_id:
                continue
            key = str(component.donor_core_line_id)
            if key not in donors:
                donors[key] = self._donor_on_hand_total(fact, key)
            donors[key] = max(donors[key] - component.qty, _ZERO)

    def _donor_on_hand_total(self, fact: _LineFacts, donor_line_id: str) -> Decimal:
        """What ONE donor line holds ON HAND, off the assignment the offer was read from."""
        row = self._assignment_line(fact, donor_line_id)
        if row is None:
            return _ZERO
        return sum(
            (
                max(_dec(item.qty), _ZERO)
                for item in row.assigned
                if item.event.kind == SA_KIND_ON_HAND
            ),
            _ZERO,
        )

    # ---------------------------------------- ladder v3 rung candidates, for the trail

    def pool_chain_for(
        self, fact: _LineFacts, *, pool_free_left: Optional[Mapping[str, Decimal]] = None
    ) -> List[Dict[str, Any]]:
        """`compose_line`'s pool candidate list (section 1b rung 3), public so the board's
        trail can show every pool the ladder actually consulted, not only this line's own.

        `pool_free_left` is the walk's free-floor ledger by pool LOCATION (AC-N.12), which
        `compose_lines` hands the board back per line so the proof states the floors this
        line was actually offered rather than the ones the first line of the walk saw."""
        return self._pool_chain(fact, pool_free_left=pool_free_left)

    def group_take_candidates_for(self, fact: _LineFacts) -> List[Dict[str, Any]]:
        """`compose_line`'s group candidate list (section 1b rung 2), public for the
        board's trail."""
        return self._group_take_candidates(fact)

    def warehouse_id_for_code(self, code: Optional[str]) -> Optional[str]:
        """A warehouse CODE resolved to its id (addressing only), for a caller that only
        has the ladder's own component `source_location` in hand - the board's row needs
        this for a group take / group borrow / cross-group borrow source, which its own
        `warehouse_ids` map (built from `fact.own_code` / `fact.pool_code` alone) does not
        cover."""
        if not code:
            return None
        warehouse = self._warehouse_by_code(code)
        return str(warehouse.id) if warehouse else None

    # ------------------------------------------------ ladder v3 group context (section 1b)

    def _fulfilment_settings(self) -> Dict[str, Any]:
        """`{reorder_coverage_until, tba_date_from}` for the active policy, read once per
        request. `.get()` with a None-safe fallback
        throughout: this reads the SAME row `app.services.scm.priority.fulfilment_settings`
        serves to the admin screen, which may still be mid-migration on another branch."""
        if self._fulfilment_settings_cache is None:
            try:
                policy = self._active_policy()
                self._fulfilment_settings_cache = dict(
                    priority.fulfilment_settings(policy) or {}
                )
            except Exception:  # pragma: no cover - defensive, see docstring
                self._fulfilment_settings_cache = {}
        return self._fulfilment_settings_cache

    def fulfilment_settings(self) -> Dict[str, Any]:
        """The active policy's fulfilment settings, PUBLIC for the board (LADDER V8, R-K).

        The board prints `available_for_project` beside a pool row and hands the client the
        `pool_share_pct` its subtotal applies; both must be the numbers the WALK obeyed, so
        they are read off this one cached accessor rather than off a second query.
        """
        return self._fulfilment_settings()

    def _reorder_coverage_until(self) -> Optional[date]:
        return self._fulfilment_settings().get("reorder_coverage_until")

    def _site_pool_warehouses(self) -> Dict[str, Warehouse]:
        """Every active warehouse that is SOME location's `pool_warehouse_id` - the pools
        ladder v3's rung 3 can draw, read once per request.

        The authoritative test is the FK, not the code's shape: on the live book every
        pool also happens to be a plain site code with no hyphen (`BRW`, `MWH`, ...), but
        that is a naming convention the data does not enforce, and a warehouse's OWN code
        says nothing about whether it is anybody's pool.
        """
        if self._site_pools_cache is None:
            pool_ids = {
                str(row[0])
                for row in self.db.query(Warehouse.pool_warehouse_id)
                .filter(Warehouse.pool_warehouse_id.isnot(None))
                .distinct()
                .all()
            }
            self._site_pools_cache = {
                warehouse_id: warehouse
                for warehouse_id, warehouse in (
                    self._warehouses(pool_ids) if pool_ids else {}
                ).items()
                if warehouse.is_active
            }
        return self._site_pools_cache

    def site_pool_warehouses(self) -> Dict[str, Warehouse]:
        """`{warehouse_id: Warehouse}` for every pool rung 2 may draw, public for the board.

        The board lists the pool a proposal cites among the cell's locations - "Pool BRW has
        1716 available" has to be a row of the table, not a figure only the sentence knows -
        and it has to read the SAME set the ladder walks, cached on the same request.
        """
        return self._site_pool_warehouses()

    def _pool_chain(
        self, fact: _LineFacts, *, pool_free_left: Optional[Mapping[str, Decimal]]
    ) -> List[Dict[str, Any]]:
        """The DRAW ORDER for rung 3: this line's own site pool first, then every other
        active site pool BY ON HAND (section 1d).

        By on hand rather than by code, because the rung now draws one pile down across
        several warehouses (`pools_net` bounds the total, each pool's own free stock bounds
        its share) and the fullest pool is the one that costs the fewest movements. The code
        breaks a tie so two runs cannot disagree.

        `available` is still carried per pool: nothing caps a draw with it any more, but the
        board's trail and the cell table print it, and it is the term the pile's net is
        summed from.

        `pool_free_left` is the WALK's running free floor, by pool LOCATION and for EVERY
        pool of the chain (AC-N.12, R-N leftover). It used to be one Decimal for the asking
        bin's own pool alone, and every other pool's floor was re-read live on each line -
        which R-N turned from a corner into the common path: step 0 walks the whole chain
        now, so two lines of one walk were each told WH3 held all 5 units of its floor and
        the walk promised 10 off a pool holding 5. A pool the ledger has nothing to say
        about is read live, which is what a single line on its own may draw.
        """
        chain: List[Dict[str, Any]] = []
        if fact.pool_code and fact.pool:
            stated = (
                None if pool_free_left is None else pool_free_left.get(fact.pool_code)
            )
            chain.append(
                {
                    "location": fact.pool_code,
                    "free": fact.pool_free if stated is None else stated,
                    "available": fact.pool_available,
                    "on_hand": self._pile_facts()
                    .get(
                        (fact.product_id, str(fact.pool.id)),
                        {"on_hand": _ZERO},
                    )
                    .get("on_hand", _ZERO),
                }
            )
        if not fact.product_id:
            return chain
        seen = {str(fact.pool.id)} if fact.pool else set()
        rest: List[Dict[str, Any]] = []
        for pool_id, pool in self._site_pool_warehouses().items():
            if pool_id in seen:
                continue
            stated = (
                None
                if pool_free_left is None
                else pool_free_left.get(pool.warehouse_code)
            )
            free = (
                self._free_at(fact.product_id, pool_id) if stated is None else stated
            )
            triple = self._pile_facts().get(
                (fact.product_id, pool_id), {"on_hand": _ZERO, "so_qty": _ZERO, "spo_qty": _ZERO}
            )
            available = triple["on_hand"] - triple["so_qty"] + triple["spo_qty"]
            rest.append(
                {
                    "location": pool.warehouse_code,
                    "free": free,
                    "available": available,
                    "on_hand": triple["on_hand"],
                }
            )
        rest.sort(key=lambda entry: (-_dec(entry["on_hand"]), entry["location"]))
        chain.extend(rest)
        return chain

    def _group_sibling_warehouses(self, fact: _LineFacts) -> Dict[str, Warehouse]:
        """Every active warehouse sharing this line's ownership group, THIS LINE'S OWN
        LOCATION INCLUDED (ladder v3, section 1b rung 2: "consider the group location
        first ... only available quantity").

        v2 excluded the own location on the reading that stock sitting there was already
        committed to whichever order was queued for it. The captain's 25 August ruling
        replaces that: the group is drawn first and the own location is part of the group,
        with the SAME `max(min(free, available), 0)` cap every other group location gets -
        so what is already owed there is netted out by the arithmetic rather than by
        refusing to look. Cached per group code, since a board asks this for many lines at
        once.
        """
        if not fact.group_code or not fact.own_code:
            return {}
        cache = self._group_siblings_cache.setdefault(fact.group_code, None)
        if cache is None:
            rows = list(self._planning_warehouses().values())
            cache = {
                row.warehouse_code: row
                for row in rows
                if row.warehouse_code
                and sales_agent_service.group_of_warehouse_code(row.warehouse_code)
                == fact.group_code
            }
            self._group_siblings_cache[fact.group_code] = cache
        return dict(cache)

    def _group_take_candidates(self, fact: _LineFacts) -> List[Dict[str, Any]]:
        """Rung 2, LADDER V4 (section 1d): what the OWNERSHIP GROUP holds, drawn own
        location first then the siblings by site.

        The whole rung offers `max(group_net, 0)` and not a unit more - the group is one
        pile. `B2155-NL-BLUE` is the case that forced it: `MWH-IB` holds 7000 with nothing
        owed against it, and under v3 that read as 7000 free to promise, while `BRW-IB` -
        where the orders are booked - owed 27,804 against 5,290 on hand. The group nets
        -15,514, so there is nothing to take, and the line buys.

        HOW MUCH is `fact.group_offer` - what the group's own net leaves for this line
        (`_group_offer`). WHERE it comes from is each location's free stock, in draw order:
        this line's own first, then the siblings by site code. A
        location's OWN signed availability takes no part any more, and that is the whole
        change - it is the per-warehouse reading that let `MWH-IB` offer 7000 while the
        group was 15,514 short, and it would now refuse a draw the group's pile has already
        allowed.

        The order is decided here and nowhere else: `propose_line` walks the list it is
        handed and never re-sorts, so a planner reading "40 from BRW-BB, 30 from DC1-BB" is
        reading one decision about draw order, not two.

        LADDER V5 (section 1e): the group's SPO is inside `group_net` and there is no
        incoming rung above this one any more, so nothing is deducted here. The whole offer
        belongs to this question - which is what "SPO stays inside the group net" means when
        it is the only reading of the pile.

        WHAT IS ON THE WATER IS OFFERED TOO, because the group's net counts it (`on hand +
        SPO - SO`, AutoCount's own Available) while `_free_at` counts only what is on the
        floor. Without that, a group whose cover is an SPO would have an offer and no
        location to point at, and the cover would be lost to a Buy - which is the double
        purchase rung 1 existed to prevent, and exactly what AC-V2 refuses.

        THE WATER IS DATED AND IT IS DISTRIBUTED (captain, 27 August 2026). Two rules, and
        both were missing:

        * only water arriving ON OR BEFORE this line's required date may be drawn. The NET
          stays date-blind, because it is the group's position and not this line's promise;
          what THIS line may take out of it is not. Late water is named in question 1's own
          sentence with its date and never offered (`_group_water`);
        * it is drawn AT THE LOCATION IT IS COMING TO, in the same order the floor is drawn,
          not booked wholesale at `fact.own_code`. "40: 20 from BRW-IB, 20 on the water to
          MWH-IB arriving 3 Sep" is checkable against the cell's own table; "40 from BRW-IB"
          against a BRW-IB holding nothing is not.

        A water candidate is marked `water` and carries the day the whole of it has landed
        by; `propose_line` composes it as `timely_spo`, never as a Reserve, so no hold is
        written against goods that are not on a floor.
        """
        if not fact.product_id or not fact.group_code:
            return []
        left = max(_dec(fact.group_offer), _ZERO)
        if left <= _ZERO:
            return []
        siblings = self._group_sibling_warehouses(fact)
        ordered = sorted(
            siblings.items(), key=lambda item: (item[0] != fact.own_code, item[0])
        )
        out: List[Dict[str, Any]] = []
        for code, warehouse in ordered:
            if left <= _ZERO:
                break
            # WHERE it can physically come from is the location's question, and HOW MUCH
            # is the group's. A location's own signed availability takes no part: that is
            # the per-warehouse reading ladder v4 exists to stop asking, and applying it
            # here would refuse a draw the group's pile has already allowed.
            capacity = min(max(self._free_at(fact.product_id, str(warehouse.id)), _ZERO), left)
            if capacity > _ZERO:
                out.append({"location": code, "qty": capacity})
                left -= capacity
        # THEN the water, in the same order, so the floor is always spent first: stock on a
        # shelf can be picked today and a promise cannot.
        for water in self._group_water(fact):
            if left <= _ZERO:
                break
            take = min(water.timely_qty, left)
            if take <= _ZERO:
                continue
            out.append(
                {
                    "location": water.location,
                    "qty": take,
                    "water": True,
                    "arrival_date": water.arrival_date,
                }
            )
            left -= take
        return out

    # ------------------------------------------------- ladder v7.1: the one assignment

    def planning_assignments(
        self, product_ids: Sequence[str], *, as_of: Optional[date] = None
    ) -> Dict[str, Any]:
        """`supply_assignment.assign()` for these products, read once per request (R21).

        THE BOARD AND THE STOCK DEBT VIEW READ ONE ASSIGNMENT. That is the whole of R21 and
        it is why this is a read of `StockDebtService`'s own input builders rather than a
        second spelling of them: two readers of one book that disagree about what is free
        is exactly the defect the assignment was introduced to end.

        The SPAN is wider than the view's by one thing - the SITE POOLS. The view answers
        "what does the project book owe", so it reads the flagged bins; the ladder also has
        a POOL step (R34), and the pool is a group of its own inside the walk
        (`supply_assignment.POOL_GROUP`), sealed off from the project groups in both
        directions. Adding it therefore changes nothing about steps 1 and 2 and is the only
        way step 4 can read the pool's own book at all.
        """
        as_of = as_of or self._walk_as_of or date.today()
        cache = self._assignment_cache.setdefault(as_of, {})
        wanted = [str(pid) for pid in product_ids if pid and str(pid) not in cache]
        if wanted:
            from app.services.scm.stock_debt_service import StockDebtService

            reader = StockDebtService(self.db)
            # The SAME service, so the policy, the lead times, the site pools and the SPO /
            # PO reads are the caches this request has already paid for.
            reader.supply = self
            warehouses = dict(self._planning_warehouses())
            warehouses.update(self._site_pool_warehouses())
            cache.update(
                reader.assignments_for(
                    sorted(set(wanted)), warehouses, as_of=as_of
                )
            )
        return {pid: cache[pid] for pid in (str(p) for p in product_ids) if pid in cache}

    def _assignment_line(
        self, fact: _LineFacts, line_id: str, *, as_of: Optional[date] = None
    ) -> Optional[Any]:
        """One CORE line's result inside its product's assignment, or `None`."""
        if not fact.product_id:
            return None
        as_of = as_of or self._walk_as_of or date.today()
        result = self.planning_assignments([fact.product_id], as_of=as_of).get(
            str(fact.product_id)
        )
        if result is None:
            return None
        index = self._assignment_index.get((str(fact.product_id), as_of))
        if index is None:
            index = {row.line.key: row for row in result.lines}
            self._assignment_index[(str(fact.product_id), as_of)] = index
        return index.get(str(line_id))

    def _late_days(
        self, fact: _LineFacts, *, as_of: Optional[date] = None
    ) -> Dict[str, int]:
        """How late each COUNTED document of this product's assignment is (R-O, #586).

        `assign()` admits a document whose arrival has passed at an ASSUMED date and keeps
        the lateness on the event; the walk's candidate lists carry only the event's KEY,
        so this is the lookup that lets a water bucket naming exactly one document say
        "SPO 2026/07-0031 is 41 days late, assumed by 17 Sep 2026" beside the promise.

        Read off the ONE assignment this request already paid for (R21) and memoised, so
        a board of 300 lines asks the question once per product.
        """
        if not fact.product_id:
            return {}
        as_of = as_of or self._walk_as_of or date.today()
        key = (str(fact.product_id), as_of)
        memo = self._late_days_memo.get(key)
        if memo is None:
            result = self.planning_assignments([fact.product_id], as_of=as_of).get(
                str(fact.product_id)
            )
            memo = {
                str(event.key): int(getattr(event, "days_late", 0) or 0)
                for event in (result.supply if result is not None else ())
                if int(getattr(event, "days_late", 0) or 0) > 0
            }
            self._late_days_memo[key] = memo
        return memo

    def _unit_line_ids(self, fact: _LineFacts) -> List[str]:
        """The CORE line ids of the planning unit being walked.

        A unit of one is the fact's own line; a unit of several carries every member's,
        stamped by `_unit_fact`, because step 1's date-aware pile is what the ASSIGNMENT
        gave those lines and a unit asking for 30 has to read all three of them.
        """
        if fact.unit_core_line_ids:
            return list(fact.unit_core_line_ids)
        own = self._core_id_of(fact)
        return [own] if own else []

    def _drawn_at_own_date(
        self, fact: _LineFacts, *, as_of: Optional[date] = None
    ) -> List[Tuple[str, Decimal, Optional[date], str, Optional[str], Optional[str]]]:
        """What the assignment gave this unit BY ITS OWN DATE, per bin (R24, AC-S3-1b).

        `(warehouse_code, qty, arrival_date_or_None, kind, event_key, event_ref)`, in the
        order the walk drew them. The cut is `open_qty - short_at_date`: `short_at_date` is
        what the line was still missing when its own date came round, so the difference is
        precisely what was there BY THEN - supply that arrived later and cleared the
        shortfall made the line `late` and is not a promise this step may make.

        This is what makes rung 1 date-aware: SRTWB242's BB pile holds 199 with 144 due
        before JEREMY, so his 27 due 15 September reads 55 free and reserves; JAY's 32 due
        26 October reads 16, which is not the whole of his unit, so step 1 gives him
        nothing (AC-S3-1b).

        `event_key`/`event_ref` are the event's own address (S4 task 3) - carried so a WATER
        entry that turns out to be exactly one document can name it, the way a step-3
        component already does; a caller reading a floor entry simply never uses them.
        """
        out: List[
            Tuple[str, Decimal, Optional[date], str, Optional[str], Optional[str]]
        ] = []
        own_line = self._core_id_of(fact)
        for line_id in self._unit_line_ids(fact):
            row = self._assignment_line(fact, line_id, as_of=as_of)
            if row is None:
                continue
            left = max(_dec(row.line.open_qty) - _dec(row.short_at_date), _ZERO)
            for item in row.assigned:
                if left <= _ZERO:
                    break
                take = min(left, max(_dec(item.qty), _ZERO))
                if take <= _ZERO:
                    continue
                if getattr(item, "landed", False) and str(line_id) != str(own_line):
                    # #1362 round 5 (owner ruling): goods that landed for ANOTHER member
                    # of this unit are that member's, never offered to this one. They
                    # still absorb that member's own `left`.
                    left -= take
                    continue
                event = item.event
                if not event.warehouse or event.is_pool:
                    # A pool hold is step 4's business, and a hold with no bin cannot be
                    # named as a source at all.
                    left -= take
                    continue
                if event.kind == SA_KIND_PO:
                    # S4 (1 Sep ruling): a PO event may never supply a walk offer, own group
                    # included - it is still ON ORDER, not incoming. It is still what the
                    # ASSIGNMENT gave this unit (`left` still absorbs it, exactly like a pool
                    # hold above), so a later SPO or floor share the same date is not
                    # double-counted; it is simply never named as a source.
                    left -= take
                    continue
                out.append(
                    (
                        str(event.warehouse),
                        take,
                        event.at if event.kind != SA_KIND_ON_HAND else None,
                        str(event.kind),
                        str(event.key),
                        event.ref,
                    )
                )
                left -= take
        return out

    def _group_book_positions(
        self, product_id: Optional[str], *, as_of: Optional[date] = None
    ) -> Dict[str, Decimal]:
        """Every ownership group's whole open book for this product (R-M), signed.

        `supply_assignment.group_book_positions` off the ONE assignment this request has
        already paid for (R21). Two readers, one book: the walk bounds a lending group's
        offer with it (`_other_group_free_at_own_date`) and the confirmation bounds what
        that group may actually give away with the same number, so a Confirm cannot accept
        an overdraw the walk would have refused.
        """
        if not product_id:
            return {}
        result = self.planning_assignments([product_id], as_of=as_of).get(str(product_id))
        if result is None:
            return {}
        return {
            group: _dec(value)
            for group, value in group_book_positions(result).items()
        }

    def _other_group_free_at_own_date(
        self,
        fact: _LineFacts,
        *,
        as_of: Optional[date] = None,
        other_left: Optional[MutableMapping[str, Decimal]] = None,
    ) -> Tuple[
        List[Tuple[str, Decimal, Optional[date], str, str, Optional[str], Optional[str]]],
        Dict[str, Decimal],
    ]:
        """The OTHER project groups' FREE piles at this unit's own date (R40's offer half).

        `(warehouse_code, qty, arrival_date_or_None, kind, group, event_key, event_ref)`,
        the same shape `_drawn_at_own_date` returns plus the GROUP the bin belongs to -
        carried so the option's label can name whose stock it is (S4 task 2) without a
        second read of `sales_agent_service.group_of_warehouse_code`, which would risk
        disagreeing with the grouping `free_piles_at` already did - and the event's own
        address (S4 task 3), for the same reason `_drawn_at_own_date` carries it.

        R40 stopped the WALK from giving an undecided line another group's stock - the
        captain: "who is us to decide those BB group takes our IB pile" - and left the OFFER
        standing, because proposing is not assuming. This is that offer: what that group had
        by the asker's date, net of every pin and of what its own earlier lines drew
        (`supply_assignment.free_piles_at`). It becomes real only on Confirm, which writes a
        pinned hold, and only then does the pile deplete for everybody else.

        **S4 (1 Sep ruling): a PO event may never supply this offer either.** The captain's
        own repro named it exactly here - "Use incoming 15 from BRW-BB, arriving 6 Sep 2026"
        off a bin whose SPO qty was 0 and whose PO qty was 978 - so a PO event reaching this
        function is skipped outright, not merely relabelled.

        **R-M (3 Sep 2026): A LENDING GROUP'S PILE IS CAPPED BY ITS WHOLE OPEN BOOK.** The
        date-bounded pile above is the right question of the group that OWNS it and the
        wrong one of a group being asked to lend: demand due AFTER the asker's date is not
        subtracted from it, so an OVERSOLD group reads as a donor of free stock. The
        captain's cell: SO419417's BB line of 4 due 5 October was proposed "4 from BRW-IB
        ... free stock is owed to nobody" while BRW-IB held 2,237 against 2,684 of open IB
        demand - 447 short on its own book. So each other group's offer is
        `min(date-bounded free, max(group book position, 0))`
        (`supply_assignment.group_book_positions`), spread over its bins in the draw order
        they already come in. A group whose book is short gives NOTHING, and it says so:
        the second return value is `group -> how short`, which the `use` option row prints
        instead of a silent 0. The own group is untouched - its draw already stops at its
        own short.

        **THE BUDGET IS THE WALK'S, NOT THIS CALL'S.** `other_left` is `compose_lines`'
        offer ledger, and the group's spare book is seeded into it under
        `_group_budget_key` the first time any unit reads the group, then drawn down by
        what the walk actually composed. Without it the cap was applied per UNIT: two
        askers whose dates bring DIFFERENT bins of one lending group into view - one seeing
        the floor, the next seeing an arrival after the floor is spoken for - were each
        given the whole budget, and both Confirms passed on a group short on its own book.
        A group whose budget this walk has already spent offers nothing more, and says
        nothing extra about it: an exhausted budget is the same silence an exhausted BIN
        already keeps, and only a SHORT book is a refusal a planner can act on.
        """
        if not fact.product_id or not fact.group_code:
            return [], {}
        as_of = as_of or self._walk_as_of or date.today()
        result = self.planning_assignments([fact.product_id], as_of=as_of).get(
            str(fact.product_id)
        )
        if result is None:
            return [], {}
        at = sa_effective_date(fact.required_date, as_of)
        positions = self._group_book_positions(fact.product_id, as_of=as_of)
        out: List[
            Tuple[str, Decimal, Optional[date], str, str, Optional[str], Optional[str]]
        ] = []
        short: Dict[str, Decimal] = {}
        for group, pile in free_piles_at(result, at=at, as_of=as_of).items():
            # An ungrouped bin (`group` empty) is outside this step, and the site pools are
            # step 4 - taking one raises an order-back, which is the opposite of free.
            if not group or group == fact.group_code or group == SA_POOL_GROUP:
                continue
            book = positions.get(group, _ZERO)
            # ONE budget for the whole group, spent bin by bin in the order the pile
            # already has (oldest arrival first): the cap is a statement about the GROUP,
            # so applying it per bin would let a group short by 400 lend 100 from each of
            # four sites.
            budget = max(book, _ZERO)
            if other_left is not None:
                # And one budget for the whole WALK: seeded on the first unit that reads
                # this group, drawn down by `compose_lines` with what was composed off it.
                budget = max(
                    _dec(other_left.setdefault(_group_budget_key(group), budget)), _ZERO
                )
            offerable = False
            for event, qty in pile:
                if not event.warehouse or event.is_pool:
                    continue
                if event.kind == SA_KIND_PO:
                    continue
                offerable = True
                take = min(_dec(qty), budget)
                if take <= _ZERO:
                    continue
                budget -= take
                out.append(
                    (
                        str(event.warehouse),
                        take,
                        event.at if event.kind != SA_KIND_ON_HAND else None,
                        str(event.kind),
                        group,
                        str(event.key),
                        event.ref,
                    )
                )
            if offerable and book < _ZERO:
                # Named only where the group HAD something the date-bounded reading would
                # have offered: a group with nothing on its floor at all is not refusing
                # anything, and a sentence about it would be noise on every walk.
                short[group] = -book
        return out, short

    def _po_received_by_so_line_ref(
        self,
        source_refs: Sequence[str],
        *,
        product_id: Optional[str],
        company_id: Optional[str],
    ) -> Dict[str, Tuple[Decimal, Optional[str]]]:
        """`source_ref -> (what has LANDED, the SPO it came off)` for every
        `PurchaseOrderLine.from_so_line_ref` in `source_refs` - the raw figure R7's tier 1
        and tier 2 are both built from, before either is capped by anything.

        R7 FOLLOW-UP (`PLAN-r7-landed-reads-spo-received.md`, R1): every PO in this
        business is received through an SPO, so `PurchaseOrderLine.qty_received` is the
        AutoCount TRANSFER of the line onto a shipping order, never a physical receipt.
        What has actually landed is `SPOAllocation.quantity_received`, resolved off the
        PO line's own `source_ref` via `SPOAllocation.from_po_line_ref`. A PO line with
        no SPO row naming it contributes nothing - the join below is an inner join.

        ONE query for the whole sales order (review round, SF2): the caller asks about a
        line's own ref and every sibling's in one go, so a board walking ten lines of one
        order pays for one read rather than a hundred.

        FILTERED BY PRODUCT (review round, MB3, and the R7 follow-up's own AC-6): `from_
        so_line_ref` is a TEXT column with no unique constraint, so a collision - or a
        data-entry mistake - on it would otherwise credit a line with a delivery of a
        completely different item. Both the receiving purchase-order line AND the SPO
        row that landed against it have to be for the SAME product as the line being
        credited.

        FILTERED BY COMPANY (security review, and the R7 follow-up's own AC-6):
        `company_id` is stated explicitly rather than left to the ORM-level scope alone,
        the same way `project_order_inquiry_service._resolve_ref_line` states it when
        resolving the very same `from_so_line_ref` text - an unscoped text match must not
        resolve another company's purchase-order line, or another company's SPO row, as
        confidently as one of ours. Absent (a line with no company stamped, the
        pre-isolation shape) means "do not narrow", exactly as `_resolve_ref_line` reads
        it.

        #1362 item 2 (owner, 29 Sep 2026): the document is no longer "the first SPO
        found". A PO line received across two shipping orders names BOTH, each with what it
        landed ("60 on SPO-2026/06-0092, 40 on SPO-2026/07-0019"), and a line whose order
        inquiry row carries a placement link to a specific SPO names THAT shipment and
        takes ITS received quantity as the landed figure, unless the PO line itself landed
        more (`_landed_for_refs`).
        """
        refs = [ref for ref in {str(r).strip() for r in source_refs if r} if ref]
        if not refs or not product_id:
            return {}
        query = (
            self.db.query(
                PurchaseOrderLine.from_so_line_ref,
                SPOAllocation.quantity_received,
                SPOAllocation.spo_number,
            )
            .join(
                SPOAllocation,
                SPOAllocation.from_po_line_ref == PurchaseOrderLine.source_ref,
            )
            .filter(
                PurchaseOrderLine.from_so_line_ref.in_(refs),
                PurchaseOrderLine.product_id == product_id,
                SPOAllocation.product_id == product_id,
            )
        )
        if company_id is not None:
            query = query.filter(
                PurchaseOrderLine.company_id == company_id,
                SPOAllocation.company_id == company_id,
            )
        by_ref: Dict[str, Dict[str, Decimal]] = {}
        for ref, qty, spo_number in query.all():
            _add_landed(by_ref, str(ref), spo_number, qty)
        return _landed_for_refs(
            by_ref,
            self._oi_linked_landed_by_ref(refs, product_id=product_id, company_id=company_id),
        )

    def _oi_linked_landed_by_ref(
        self,
        refs: Sequence[str],
        *,
        product_id: Optional[str],
        company_id: Optional[str],
    ) -> Dict[str, Dict[str, Decimal]]:
        """`source_ref -> {spo_number -> landed}` off the order inquiry's own PLACEMENT
        links (#1362 item 2): the SO line's live order-inquiry row(s), each link naming an
        SPO row (`OrderInquiryLink.spo_allocation_id`), of the SAME product as the line.

        What a link says landed for the line is `min(link.qty, allocation.quantity_
        received)`: the placement is the part of that shipment put against this line, and
        the allocation's receipt is how much of the shipment has actually arrived, so the
        line cannot be owed more of it than either. A cancelled row places nothing.
        """
        if not refs or not product_id:
            return {}
        out: Dict[str, Dict[str, Decimal]] = {}
        for ref, _product, _company, spo_number, placed, received in self._oi_placements(
            refs, [product_id], company_id=company_id
        ):
            _add_landed(out, str(ref).strip(), spo_number, min(_dec(placed), _dec(received)))
        return out

    def _oi_placements(
        self,
        refs: Iterable[str],
        product_ids: Iterable[str],
        *,
        company_id: Optional[str] = None,
    ) -> List[Any]:
        """The raw rows behind `_oi_linked_landed_by_ref`: `(line source_ref, product,
        company, spo_number, placed, received)` for every live order-inquiry SPO placement
        on a line holding one of `refs`. The SPO row must be for the line's own product and
        company (the same AC-6 pairing the SPO receipt join states in its JOIN)."""
        query = (
            self.db.query(
                SalesOrderLine.source_ref,
                SalesOrderLine.product_id,
                SalesOrderLine.company_id,
                SPOAllocation.spo_number,
                OrderInquiryLink.qty,
                SPOAllocation.quantity_received,
            )
            .join(
                ProjectSalesOrderLine,
                ProjectSalesOrderLine.core_sales_order_line_id == SalesOrderLine.id,
            )
            .join(OrderInquiryRow, OrderInquiryRow.so_line_id == ProjectSalesOrderLine.id)
            .join(OrderInquiryLink, OrderInquiryLink.row_id == OrderInquiryRow.id)
            .join(
                SPOAllocation,
                (SPOAllocation.id == OrderInquiryLink.spo_allocation_id)
                & (SPOAllocation.product_id == SalesOrderLine.product_id)
                & (SPOAllocation.company_id == SalesOrderLine.company_id),
            )
            .filter(
                SalesOrderLine.source_ref.in_(list(refs)),
                SalesOrderLine.product_id.in_(list(product_ids)),
                OrderInquiryRow.state != INQUIRY_CANCELLED,
            )
        )
        if company_id is not None:
            query = query.filter(SalesOrderLine.company_id == company_id)
        return query.all()

    def _prefetch_own_arrival(
        self, entries: Sequence[Tuple[Any, _LineFacts, Any]]
    ) -> None:
        """`compose_lines`'s own batch: seeds `_own_arrival_line_memo` and
        `_own_arrival_order_memo` for the WHOLE walk in a handful of round trips, so
        `_own_arrival_credit_components` (called once per unit from `walk()`) finds every
        answer already memoized rather than paying for its own representative core line
        one row at a time.

        Up to two reads, however many entries the walk holds - and often none for the
        first:

        1. every entry's own core line, by id (`_own_arrival_line_memo`). A fact already
           carrying its own core line at ZERO extra cost - the sheet's `core`, or the
           board's `source_ref`/`sales_order_id`/`line_company_id` (`demand_facts`,
           threaded off the same row `project_fulfilment_board_service._demand_rows`
           already fetched) - is resolved from the fact itself, never queried; only a
           fact from a caller that has not threaded them through falls back to one `IN`
           fetch for whatever is left;
        2. every one of those lines' siblings - same sales order, same product (MB3) - in
           one query keyed by every sales order and product the walk actually names,
           filtered client-side back down to the exact (order, product, company) triples
           `_own_arrival_order_facts` would have asked for one at a time, plus what has
           landed against every one of those siblings' own purchase-order lines
           (`_po_received_by_so_line_ref`'s own shape), split back out by (product,
           company) so a company-scoped key never sees another company's receipt.

        A line with no `source_ref` never reaches `_own_arrival_order_facts` at all
        (`_own_arrival_credit_components`'s own early return), so it costs this prefetch
        nothing beyond resolving its own core line.
        """
        resolved: Dict[str, Any] = {}
        unresolved: Set[str] = set()
        for _key, fact, _unit_key in entries:
            cid = self._core_id_of(fact)
            if not cid or cid in self._own_arrival_line_memo or cid in resolved:
                continue
            if fact.core is not None:
                resolved[cid] = fact.core
            elif fact.sales_order_id and fact.product_id:
                resolved[cid] = SimpleNamespace(
                    id=cid,
                    source_ref=fact.source_ref,
                    sales_order_id=fact.sales_order_id,
                    product_id=fact.product_id,
                    company_id=fact.line_company_id,
                )
            else:
                unresolved.add(cid)
        for cid, line in resolved.items():
            self._own_arrival_line_memo[cid] = line
        fetched: List[Any] = []
        if unresolved:
            fetched = (
                self.db.query(SalesOrderLine)
                .filter(SalesOrderLine.id.in_(unresolved))
                .all()
            )
            by_id = {str(line.id): line for line in fetched}
            for cid in unresolved:
                self._own_arrival_line_memo[cid] = by_id.get(cid)
        core_lines = list(resolved.values()) + fetched
        if not core_lines:
            return

        keys: Dict[Tuple[str, str, str], List[Any]] = {}
        for line in core_lines:
            if not line.source_ref or not line.sales_order_id or not line.product_id:
                continue
            key = (
                str(line.sales_order_id), str(line.product_id),
                str(getattr(line, "company_id", None) or ""),
            )
            keys.setdefault(key, [])
        if not keys:
            return

        order_ids = {key[0] for key in keys}
        product_ids = {key[1] for key in keys}
        sibling_rows = (
            self.db.query(SalesOrderLine)
            .filter(
                SalesOrderLine.sales_order_id.in_(order_ids),
                SalesOrderLine.product_id.in_(product_ids),
            )
            .all()
        )
        siblings_by_key: Dict[Tuple[str, str, str], List[Any]] = {}
        for row in sibling_rows:
            row_key = (
                str(row.sales_order_id), str(row.product_id),
                str(getattr(row, "company_id", None) or ""),
            )
            siblings_by_key.setdefault(row_key, []).append(row)

        refs = {str(row.source_ref).strip() for row in sibling_rows if row.source_ref}
        received_all: Dict[str, Dict[str, Dict[str, Decimal]]] = {}
        received_scoped: Dict[Tuple[str, str], Dict[str, Dict[str, Decimal]]] = {}
        if refs and product_ids:
            # R7 follow-up (`PLAN-r7-landed-reads-spo-received.md`): the same SPO join
            # `_po_received_by_so_line_ref` reads, batched. `SPOAllocation.product_id ==
            # PurchaseOrderLine.product_id` AND `SPOAllocation.company_id ==
            # PurchaseOrderLine.company_id` in the JOIN itself (not only `.in_(product_ids)`
            # on each side) - the single-read seam gets this for free by comparing both
            # tables to the SAME literal `product_id`/`company_id` parameters; the batched
            # read has no single parameter, only the whole walk's product SET across every
            # company it touches, so an SPO row for a DIFFERENT product or a DIFFERENT
            # company that both happen to appear somewhere in that set would otherwise pair
            # across the join without ever being compared to each other (AC-6, fix-round
            # S2).
            rows = (
                self.db.query(
                    PurchaseOrderLine.from_so_line_ref,
                    SPOAllocation.quantity_received,
                    PurchaseOrderLine.product_id,
                    PurchaseOrderLine.company_id,
                    SPOAllocation.spo_number,
                )
                .join(
                    SPOAllocation,
                    (SPOAllocation.from_po_line_ref == PurchaseOrderLine.source_ref)
                    & (SPOAllocation.product_id == PurchaseOrderLine.product_id)
                    & (SPOAllocation.company_id == PurchaseOrderLine.company_id),
                )
                .filter(
                    PurchaseOrderLine.from_so_line_ref.in_(refs),
                    PurchaseOrderLine.product_id.in_(product_ids),
                    SPOAllocation.product_id.in_(product_ids),
                )
                .all()
            )
            for ref, qty, product_id, company_id, spo_number in rows:
                ref = str(ref)
                _add_landed(
                    received_all.setdefault(str(product_id), {}), ref, spo_number, qty
                )
                if company_id is not None:
                    _add_landed(
                        received_scoped.setdefault((str(product_id), str(company_id)), {}),
                        ref, spo_number, qty,
                    )

        # #1362 item 2: the order inquiry's own SPO placements, the same question
        # `_oi_linked_landed_by_ref` asks for one key, batched and split back out the same
        # way the SPO receipts above are.
        linked_all: Dict[str, Dict[str, Dict[str, Decimal]]] = {}
        linked_scoped: Dict[Tuple[str, str], Dict[str, Dict[str, Decimal]]] = {}
        if refs and product_ids:
            for ref, product_id, company_id, spo_number, placed, received in (
                self._oi_placements(refs, product_ids)
            ):
                landed = min(_dec(placed), _dec(received))
                ref = str(ref).strip()
                _add_landed(linked_all.setdefault(str(product_id), {}), ref, spo_number, landed)
                if company_id is not None:
                    _add_landed(
                        linked_scoped.setdefault((str(product_id), str(company_id)), {}),
                        ref, spo_number, landed,
                    )

        for key in keys:
            sales_order_id, product_id, company_id = key
            siblings = siblings_by_key.get(key, [])
            by_ref = (
                received_scoped.get((product_id, company_id), {})
                if company_id
                else received_all.get(product_id, {})
            )
            linked = (
                linked_scoped.get((product_id, company_id), {})
                if company_id
                else linked_all.get(product_id, {})
            )
            self._own_arrival_order_memo[key] = (siblings, _landed_for_refs(by_ref, linked))

    def _own_arrival_order_facts(
        self, core_line: Any
    ) -> Tuple[List[Any], Dict[str, Tuple[Decimal, Optional[str]]]]:
        """`(this product's sibling lines, ref -> received)` for `core_line`'s own sales
        order, read ONCE per (order, product, company) per request (SF2).

        Siblings are narrowed to the credited line's OWN product (MB3): a sibling holding
        a different item says nothing about what this line may draw, and its receipt is a
        different pile of stock entirely.
        """
        product_id = str(core_line.product_id) if core_line.product_id else ""
        company_id = getattr(core_line, "company_id", None)
        key = (str(core_line.sales_order_id), product_id, str(company_id or ""))
        held = self._own_arrival_order_memo.get(key)
        if held is not None:
            return held
        siblings = (
            self.db.query(SalesOrderLine)
            .filter(
                SalesOrderLine.sales_order_id == core_line.sales_order_id,
                SalesOrderLine.product_id == core_line.product_id,
            )
            .all()
        )
        received = self._po_received_by_so_line_ref(
            [line.source_ref for line in siblings] + [core_line.source_ref],
            product_id=product_id or None,
            company_id=company_id,
        )
        self._own_arrival_order_memo[key] = (siblings, received)
        return siblings, received

    def own_arrival_credit_for(
        self,
        fact: _LineFacts,
        *,
        own_arrival_left: Optional[MutableMapping[str, Decimal]] = None,
    ) -> Tuple[Decimal, Optional[str]]:
        """R7: what landed FOR this line - tier 1, its own purchase order(s)
        (`from_so_line_ref == this line's source_ref`), then tier 2, the rest of the same
        sales order's landed stock OF THIS PRODUCT, each sibling's own claim on its own PO
        subtracted first (a closed line's by its `qty_ordered` - what it delivered; an open
        one's by the same field - what it still needs). Capped by physical on hand at the
        line's own location (`LocationNet.on_hand`, already stamped on
        `fact.group_net_by_location` by `_apply_group_nets` - no second query) and, via
        `own_arrival_left`, by what the SAME walk has already spent of that location and of
        each sibling's spare.

        PUBLIC (review round): `project_order_inquiry_service`'s path picker asks the same
        question of the same seam, and a cross-module caller must not have to reach for a
        private name to get the ONE answer the board and the worklist have to agree on.

        `own_arrival_left` is the walk's own ledger, `location code -> what is left of it`
        plus `spare:<sibling source_ref> -> what is left of that sibling's spare`
        (`_own_arrival_spare_key`). TWO things are bounded, and one does not stand in for
        the other:

        * the PHYSICAL pile at the bin (AC-S3-4, AC-S3-11) - charged here by the credit and
          by `compose_lines` with every ORDINARY group-take Reserve drawn off the same bin,
          because a credit and an assignment draw are two claims on one pile of units;
        * each SIBLING's SPARE (MB2) - once L2 takes 10 of L1's 40, L3 sees 30, not 40.
          The physical ledger cannot stand in for it: on a bin holding plenty, two open
          lines would each read the whole of one closed sibling's spare and both be
          credited off it.

        Returns `(credit_qty, document)` - `document` is tier 1's own SPO where there is
        one (R7 follow-up, R1/R3: what landed is `spo_allocations.quantity_received`,
        never `purchase_order_lines.qty_received`, so this names the SPO, not the PO),
        else the first tier-2 SPO the credit actually drew a spare from.

        S5: this method charges `own_arrival_left` immediately with the credit it
        returns, because for THIS caller the credit IS the final draw (`_check_line`'s
        confirm-time recheck, `project_order_inquiry_service`'s path picker). `walk()`'s
        own candidate-build-time call is different - `walk_line` may draw LESS than the
        theoretical credit once its pool-share sub-step is netted out - so `walk()` reads
        `_own_arrival_credit_components` directly (no charge) and charges
        `_charge_own_arrival_credit` itself, afterwards, with what was actually drawn.
        """
        credit, po_number, _said = self.own_arrival_credit_said(
            fact, own_arrival_left=own_arrival_left
        )
        return credit, po_number

    def own_arrival_credit_said(
        self,
        fact: _LineFacts,
        *,
        own_arrival_left: Optional[MutableMapping[str, Decimal]] = None,
    ) -> Tuple[Decimal, Optional[str], Optional[str]]:
        """`own_arrival_credit_for`, plus how the credit is said when part of it is a
        sibling's spare (#1362 item 4, `own_arrival_landed_text`), read BEFORE the charge
        spends that spare. The confirm recheck's refusal names the purchase with it."""
        credit, po_number, tier1_qty, tier2 = self._own_arrival_credit_components(
            fact, own_arrival_left=own_arrival_left
        )
        said = (
            self.own_arrival_landed_text(fact, credit, tier1_qty, tier2)
            if credit > _ZERO
            else None
        )
        if own_arrival_left is not None and credit > _ZERO:
            self._charge_own_arrival_credit(
                fact, credit, tier1_qty, tier2, own_arrival_left
            )
        return credit, po_number, said

    def _own_arrival_credit_components(
        self,
        fact: _LineFacts,
        *,
        own_arrival_left: Optional[MutableMapping[str, Decimal]] = None,
    ) -> Tuple[Decimal, Optional[str], Decimal, List[Tuple[str, Decimal, Optional[str]]]]:
        """S5: the READ-ONLY half of `own_arrival_credit_for` - tier 1's own SPO receipt
        (R7 follow-up, R1: what LANDED is `spo_allocations.quantity_received`, never
        `purchase_order_lines.qty_received`, a TRANSFER, not a receipt), tier 2's
        sibling spare (netted against `own_arrival_left`'s own running balance, MB2, via
        `setdefault` - a READ that seeds the ledger's starting point, not a charge), and
        the THEORETICAL credit those two and the bin's on hand cap the line's open qty
        to. Nothing is SPENT off `own_arrival_left` here - that is
        `_charge_own_arrival_credit`'s job, called with whatever was actually drawn,
        which for `walk()`'s own caller can be less than what is returned here.

        Returns `(credit_qty, document, tier1_qty, tier2)` - `tier2` is the
        `(source_ref, spare, document)` list a deferred charge walks in the SAME order
        this computed it, so tier 1 is always spent before any sibling's tier-2 spare.
        `document` names the SPO the goods landed on, never the PO (R3).
        """
        # #1362 item 6: THIS member's own core line (`_core_id_of`), never the unit's
        # first. Two same-date lines of one product form one planning unit, and every
        # member used to read `unit_core_line_ids[0]`: the line with no purchase of its own
        # was credited its sibling's whole tier-1 receipt as if bought for it, so 100
        # landed for one line was credited 160 across the two.
        if not fact.own_code:
            return _ZERO, None, _ZERO, []
        core_line = self._own_core_line(fact)
        if core_line is None or not core_line.source_ref:
            return _ZERO, None, _ZERO, []
        if not core_line.sales_order_id or not core_line.product_id:
            return _ZERO, None, _ZERO, []

        siblings, received = self._own_arrival_order_facts(core_line)
        tier1_qty, tier1_po = received.get(
            str(core_line.source_ref).strip(), (_ZERO, None)
        )
        # The siblings' spare, one entry each, kept as a LIST rather than a running sum so
        # a charge (immediate or deferred) can spend exactly the ones the credit actually
        # drew on (MB2).
        tier2: List[Tuple[str, Decimal, Optional[str]]] = []
        for sibling in siblings:
            if str(sibling.id) == str(core_line.id) or not sibling.source_ref:
                continue
            ref = str(sibling.source_ref).strip()
            sibling_received, sibling_po = received.get(ref, (_ZERO, None))
            if sibling_received <= _ZERO:
                continue
            # AC-S3-5: net of what the sibling's own PO already owes IT - its own
            # `qty_ordered`, delivered where it is closed, still-owed where it is not.
            spare = sibling_received - min(sibling_received, _dec(sibling.qty_ordered))
            if spare <= _ZERO:
                continue
            if own_arrival_left is not None:
                # MB2: what is LEFT of this sibling's spare in this walk, not what it
                # started with - an earlier open line of the same order may already have
                # been credited part of it.
                spare = max(
                    _dec(
                        own_arrival_left.setdefault(
                            _own_arrival_spare_key(ref), spare
                        )
                    ),
                    _ZERO,
                )
            if spare > _ZERO:
                tier2.append((ref, spare, sibling_po))
        tier2_qty = sum((spare for _ref, spare, _po in tier2), _ZERO)
        tier2_po = next((po for _ref, _spare, po in tier2 if po), None)

        # Capped by what the LINE itself still needs (AC-S3-4): a line whose own PO
        # received more than it needs must not be sized a candidate bigger than what it
        # could ever draw.
        theoretical = min(tier1_qty + tier2_qty, max(_dec(fact.open_qty), _ZERO))
        if theoretical <= _ZERO:
            return _ZERO, None, tier1_qty, tier2

        on_hand = self._on_hand_at(fact, fact.own_code)
        if on_hand is None:
            return _ZERO, None, tier1_qty, tier2

        if own_arrival_left is not None:
            remaining = own_arrival_left.get(fact.own_code)
            if remaining is None:
                remaining = on_hand
        else:
            remaining = on_hand

        credit = min(theoretical, max(remaining, _ZERO))
        if credit <= _ZERO:
            return _ZERO, None, tier1_qty, tier2
        return credit, tier1_po or tier2_po, tier1_qty, tier2

    def _own_core_line(self, fact: _LineFacts) -> Optional[Any]:
        """The CORE sales-order line this fact's own-arrival credit is read for, memoised
        per request (`_own_arrival_line_memo`)."""
        core_line_id = self._core_id_of(fact)
        if not core_line_id:
            return None
        if core_line_id not in self._own_arrival_line_memo:
            self._own_arrival_line_memo[core_line_id] = (
                self.db.query(SalesOrderLine)
                .filter(SalesOrderLine.id == core_line_id)
                .one_or_none()
            )
        return self._own_arrival_line_memo[core_line_id]

    def own_arrival_landed_text(
        self,
        fact: _LineFacts,
        landed: Decimal,
        tier1_qty: Decimal,
        tier2: List[Tuple[str, Decimal, Optional[str]]],
    ) -> Optional[str]:
        """#1362 item 4 (owner, 29 Sep 2026): what landed, said by WHOSE purchase it was.

        `landed` is split the way `_charge_own_arrival_credit` spends it, tier 1 first:
        this line's own PO reads "100 landed for this line on SPO-2026/09-0036"; a
        sibling's over-buy reads "100 spare from line 1648's purchase (200 bought for 100)
        landed on SPO-2026/09-0036", naming the sibling by its AutoCount line number. A
        spare is never said to have landed "for this line": SO382618's January B2154-NL
        line was credited the surplus of the 200 bought for line 1648, and "landed for
        this line" sent the owner looking for a purchase that was never made for it.

        `None` when no spare is part of `landed` - the caller's one-shipment sentence
        (`own_arrival_reason`) already says it right.
        """
        core_line = self._own_core_line(fact)
        if core_line is None or landed <= _ZERO or not tier2:
            return None
        siblings, received = self._own_arrival_order_facts(core_line)
        _tier1, tier1_po = received.get(
            str(core_line.source_ref or "").strip(), (_ZERO, None)
        )
        own = min(max(_dec(tier1_qty), _ZERO), landed)
        spare_left = landed - own
        if spare_left <= _ZERO:
            return None
        by_ref = {
            str(line.source_ref).strip(): line for line in siblings if line.source_ref
        }
        parts: List[str] = []
        if own > _ZERO:
            parts.append(f"{qty_text(own)} landed for this line{landed_on(tier1_po)}")
        for ref, spare, document in tier2:
            if spare_left <= _ZERO:
                break
            take = min(spare, spare_left)
            spare_left -= take
            sibling = by_ref.get(ref)
            sibling_received, _doc = received.get(ref, (_ZERO, None))
            line_no = getattr(sibling, "line_no", None)
            whose = f"line {line_no}'s" if line_no is not None else "another line's"
            ordered = _dec(getattr(sibling, "qty_ordered", None))
            parts.append(
                f"{qty_text(take)} spare from {whose} purchase "
                f"({qty_text(sibling_received)} bought for {qty_text(ordered)}) "
                f"landed{landed_on(document)}"
            )
        return "; ".join(parts)

    @staticmethod
    def _charge_own_arrival_credit(
        fact: _LineFacts,
        drawn: Decimal,
        tier1_qty: Decimal,
        tier2: List[Tuple[str, Decimal, Optional[str]]],
        own_arrival_left: MutableMapping[str, Decimal],
    ) -> None:
        """S5: charge `own_arrival_left` with what was ACTUALLY drawn (`drawn`), never
        the theoretical credit `_own_arrival_credit_components` sized a candidate with -
        `walk()`'s own caller learns `drawn` only after `walk_line` returns, once its
        pool-share sub-step has told it how much of the line's need the own-arrival
        sub-step was even asked to cover. Tier 1 (this line's own PO) is charged first,
        exactly as `_own_arrival_credit_components` measured it; only what `drawn` takes
        BEYOND tier 1 comes off the siblings' tier-2 spare, in the order they were read
        (MB2) - unchanged from the charge `own_arrival_credit_for` used to do inline.
        """
        if not fact.own_code:
            return
        remaining = own_arrival_left.get(fact.own_code)
        if remaining is None:
            remaining = ProjectSupplyService._on_hand_at(fact, fact.own_code) or _ZERO
        own_arrival_left[fact.own_code] = max(_dec(remaining) - drawn, _ZERO)
        tier2_used = drawn - tier1_qty
        for ref, spare, _po in tier2:
            if tier2_used <= _ZERO:
                break
            spend = min(spare, tier2_used)
            key = _own_arrival_spare_key(ref)
            own_arrival_left[key] = max(
                _dec(own_arrival_left.get(key, spare)) - spend, _ZERO
            )
            tier2_used -= spend

    @staticmethod
    def _on_hand_at(fact: _LineFacts, code: Optional[str]) -> Optional[Decimal]:
        """Physical on hand at one of this fact's own group locations (`LocationNet
        .on_hand`, already stamped on `fact.group_net_by_location` by `_apply_group_nets`
        - no second query). `None` when the netting says nothing about that bin, which is
        not the same answer as zero: the caller declines rather than assuming a floor.
        """
        if not code:
            return None
        for entry in fact.group_net_by_location or []:
            if getattr(entry, "location", None) == code:
                return _dec(getattr(entry, "on_hand", _ZERO))
        return None

    def use_candidates_for(
        self,
        fact: _LineFacts,
        *,
        as_of: Optional[date] = None,
        other_left: Optional[MutableMapping[str, Decimal]] = None,
        own_left: Optional[MutableMapping[str, Decimal]] = None,
    ) -> Tuple[
        List[Dict[str, Any]], List[Dict[str, Any]], Decimal, Dict[str, Decimal]
    ]:
        """Step 1 (`use`), both halves, and the number its sentences are a share OF.

        `(own_group, other_groups, own_group_offer, other_group_short)`. The own half is
        the ASSIGNMENT's own
        draw (R24), in the order the ladder has always had - this line's own bin first, then
        its siblings by code, the floor before the water. The other half is the other
        PROJECT groups' free piles at this unit's date, which since R40 is an OFFER rather
        than something the walk already did (`_other_group_free_at_own_date`). It is a
        Reserve either way: free means owed to nobody, so nobody is owed it back (R5,
        AC-S3-1).

        THE QUANTITY IS THE ASSIGNMENT'S, NOT `group_offer`'s. Ladder v4 offered
        `max(group net + this unit's own quantity, 0)`, which is the group's position over
        its WHOLE book, 2030 lines included; v7.1 asks what was free BY THIS UNIT'S DATE.
        On SRTWB242 the two disagree by design: plain Available is -1,156 and 55 is free by
        15 September, so JEREMY reserves his 27 (AC-S3-1b). Where nothing earlier competes
        the two numbers are equal, which is why ladder v4's own suite keeps its figures.

        A location carrying NO ownership group is outside this step in both halves, the same
        rule ladder v4 kept: the group is what "our locations" means, and offering an
        ungrouped bin under that name would silently widen it.

        `own_left` is the same idea for the OWN half, and it exists for ladder v8's per-line
        walk (R-E): every contributing line of a planning unit is handed the SAME draw - what
        the assignment gave the unit's lines by their own date - so without a ledger the
        second line of SO419208's unit would be offered the 135 the first one has just taken.
        It is the UNIT's ledger and nobody else's; `compose_lines` opens a fresh one per unit.

        `other_left` is the walk's ledger for the OFFER half, warehouse code -> what is
        still on the table there (`compose_lines`). It exists because R40 took away the
        thing that used to bound it: while the assignment DREW cross-group, a second unit of
        the same board read a pile the first had already emptied, and now it reads the same
        free pile as the first did. That is the defect four delivery dates of SO381895 hit
        on the donor rung on 28 August 2026 - each offered the whole of one pile, and
        `confirm` refusing all but the first. It carries R-M's LENDING-GROUP budget in the
        same dict (`_group_budget_key`), because a bin ledger alone cannot bound a group
        whose bins come into view on different dates.

        `other_group_short` is R-M's refusal (3 Sep 2026): the OTHER groups whose whole open
        book is short, and by how much, so the walk can say "IB group is 447 short on its
        own book, nothing to spare" rather than print a 0 nobody can act on.
        """
        if not fact.group_code:
            return [], [], _ZERO, {}
        own_group = fact.group_code
        mine: Dict[Tuple[str, bool], Dict[str, Any]] = {}
        others: Dict[Tuple[str, bool], Dict[str, Any]] = {}

        def accumulate(
            into: Dict[Tuple[str, bool], Dict[str, Any]],
            code: str,
            qty: Decimal,
            arrival: Optional[date],
            kind: str,
            group: Optional[str] = None,
            *,
            event_key: Optional[str] = None,
            event_ref: Optional[str] = None,
        ) -> None:
            # On a floor, or on the water: the second is composed as `timely_spo`, never as
            # a Reserve, because a hold cannot be written against goods nobody can pick.
            water = kind != SA_KIND_ON_HAND
            entry = into.setdefault(
                (code, water),
                {
                    "location": code,
                    "qty": _ZERO,
                    "water": water,
                    "arrival_date": None,
                    # The lending group, for the OTHER half only (task 2): the step-1 option
                    # row names it rather than defaulting to "our locations" beside a
                    # composition that never touched this line's own group.
                    "group": group,
                    # Every WATER document that fed this bucket (task 3), keyed by the
                    # event's own address. Named in the sentence only when there turns out
                    # to be exactly one - "list nothing rather than lie" when two documents
                    # share one bucket, which the aggregate sentence still describes fine.
                    "documents": {},
                },
            )
            entry["qty"] += qty
            if arrival is not None and (
                entry["arrival_date"] is None or arrival > entry["arrival_date"]
            ):
                # The day the WHOLE of this draw has landed by, which is the date a planner
                # promises against - not the earliest of several documents.
                entry["arrival_date"] = arrival
            if water and event_key:
                entry["documents"][event_key] = event_ref

        for code, qty, arrival, kind, event_key, event_ref in self._drawn_at_own_date(
            fact, as_of=as_of
        ):
            group = sales_agent_service.group_of_warehouse_code(code)
            if not group:
                continue
            if group == own_group:
                accumulate(
                    mine, code, qty, arrival, kind,
                    event_key=event_key, event_ref=event_ref,
                )
            else:
                # A CONFIRMED hold at another group's bin. Already this line's, so it is
                # stated with the offer half rather than re-offered by it - `free_piles_at`
                # nets a pin out of the pile it stands on.
                accumulate(
                    others, code, qty, arrival, kind, group,
                    event_key=event_key, event_ref=event_ref,
                )
        offered, other_short = self._other_group_free_at_own_date(
            fact, as_of=as_of, other_left=other_left
        )
        for (
            code, qty, arrival, kind, group, event_key, event_ref,
        ) in offered:
            accumulate(
                others, code, qty, arrival, kind, group,
                event_key=event_key, event_ref=event_ref,
            )

        late_days = self._late_days(fact, as_of=as_of)

        def rows(
            entries: Dict[Tuple[str, bool], Dict[str, Any]],
            *,
            own_first: bool,
            ledger: Optional[MutableMapping[str, Decimal]] = None,
            per_half: bool = False,
        ) -> List[Dict[str, Any]]:
            out: List[Dict[str, Any]] = []
            # Per BIN, across its floor and its water: one pile, one budget. Seeded into the
            # walk's ledger the first time it is read (the whole pile, which is what nobody
            # has been offered yet) and drawn down by `compose_lines` with what was actually
            # composed - never with what was merely offered.
            #
            # `per_half` keeps the FLOOR and the WATER of one bin apart, and the OWN half
            # (ladder v8, R-E) needs that: the two contributing lines of a unit share one
            # bin's pile, the first takes the floor, and a budget that only knew the bin's
            # total would offer the second line a floor the first had already emptied - the
            # captain's own "reserve 10 then timely 10" against 10 on the floor.
            budget: Dict[str, Decimal] = {}
            if ledger is not None:
                for (code, water), entry in entries.items():
                    key = _pile_key(code, water, per_half)
                    budget[key] = budget.get(key, _ZERO) + _dec(entry["qty"])
                for key, whole in budget.items():
                    budget[key] = max(_dec(ledger.setdefault(key, whole)), _ZERO)
            for (code, water), entry in sorted(
                entries.items(),
                key=lambda item: (
                    item[0][1],
                    own_first and item[0][0] != fact.own_code,
                    item[0][0],
                ),
            ):
                qty = _dec(entry["qty"])
                if ledger is not None:
                    key = _pile_key(code, water, per_half)
                    qty = min(qty, budget.get(key, _ZERO))
                    budget[key] = budget.get(key, _ZERO) - qty
                if qty <= _ZERO:
                    continue
                # ONE document, named; more than one, named to nobody (task 3) - the
                # aggregate sentence still says the quantity and the arrival either way.
                documents = entry.get("documents") or {}
                single = next(iter(documents.items())) if len(documents) == 1 else None
                # R-O: the BUCKET's lateness, not just a single document's - two late
                # documents sharing one bucket (BRW-BB on SO419417, 24 Jul + 6 Aug) are
                # each late on their own, so the sentence must still carry the clause even
                # though `single` is None here. `late_document_reason(None, days, arrival)`
                # already reads a None document as "the document".
                bucket_late_days = (
                    max((late_days.get(str(key), 0) for key in documents), default=0)
                    if documents
                    else 0
                )
                out.append(
                    {
                        "location": code,
                        "qty": qty,
                        **(
                            {"water": True, "arrival_date": entry["arrival_date"]}
                            if water
                            else {}
                        ),
                        **({"group": entry["group"]} if entry.get("group") else {}),
                        **(
                            {"supply_key": single[0], "supply_document": single[1]}
                            if single
                            else {}
                        ),
                        **(
                            {"late_days": bucket_late_days}
                            if bucket_late_days > 0
                            else {}
                        ),
                    }
                )
            return out

        # This line's own bin first, then the siblings by code; the floor of a bin before
        # its water, so stock on a shelf is always spent before a promise.
        own = rows(mine, own_first=True, ledger=own_left, per_half=True)
        other = rows(others, own_first=False, ledger=other_left)
        # The DAY the offer half was measured on (R-M): the sentence states the pile and the
        # date it stood on, and the engine has no other way to know which day that was.
        free_at = sa_effective_date(
            fact.required_date, as_of or self._walk_as_of or date.today()
        )
        for candidate in other:
            candidate["free_at"] = free_at
        return own, other, sum((_dec(c["qty"]) for c in own), _ZERO), other_short

    def _eligible_donor(
        self,
        row: Any,
        *,
        my_so: Optional[str],
        window: date,
        tba_from: date,
        pool: bool = False,
    ) -> bool:
        """"A LATER ORDER THAT CAN WAIT", in ONE place for steps 2 and 3 (R3, R12).

        Both steps ask the same question of the same `DemandLine` - step 2 of an order
        holding stock, step 3 of an order holding a promise - so "a later order" must not
        come to mean one thing when the cover is on a floor and another when it is on a
        ship. It was written out twice; this is the one copy.

        Covered or pinned (there is nothing to lend otherwise), dated, short of the TBA
        line, at or beyond `as_of + lead + 14` (inside the window purchasing cannot buy in
        time for the DONOR either, and past-due fails the same test), never the asker's own
        sales order, and never a line this very board is deciding.
        """
        line = row.line
        if bool(line.is_pool) != bool(pool):
            return False
        if row.status not in (SA_STATUS_COVERED, SA_STATUS_PINNED):
            return False
        # The date half is SHARED with the Stock Debt view's lendable pin
        # (STOCK-DEBT-LENDABLE): one predicate, so a pin the view lends is a donor the
        # board offers, and the Rebalance the view runs is the borrow the board would make.
        if not later_order_can_wait(
            line.required_date, window=window, tba_from=tba_from
        ):
            return False
        if my_so and (line.so_number or "") == my_so:
            return False
        return str(line.key) not in self._current_selection_core_ids

    def order_borrow_candidates_for(
        self,
        fact: _LineFacts,
        *,
        as_of: Optional[date] = None,
        borrow_left: Optional[Mapping[str, Decimal]] = None,
        pool: bool = False,
    ) -> List[Dict[str, Any]]:
        """Step 2 (R3, R4, R5, R9, R12, R19, R25): the LATER orders holding on hand.

        A donor is a line the assignment reads as `covered` or `pinned` whose cover is ON
        HAND - a promise already met off a floor - in ANY flagged project group (R5), whose
        own required date is at or after `as_of + lead + 14` (`reserve_window_end`, R12),
        never past-due, never TBA, never undated (R3), and never the asker's own sales
        order. A DECIDED line donates like any other (R9): its decision is superseded inside
        the same Confirm (R25), which is what makes "the stock is spoken for" a thing the
        planner can undo rather than a wall.

        Ordered `(same_agent desc, required_date desc, same_group desc, same_warehouse
        desc)` (R4, R19): her own agent first (she can authorise moving stock between her
        own orders), then the order that can wait longest, then the same ownership group,
        then the asker's own bin - fewest transfers. The list is the order the engine walks
        AND the order `BorrowAddDialog` prints, set here and re-sorted nowhere.

        `borrow_left` is the walk's donor ledger, keyed by DONOR LINE (AC-S3-9): two units
        of one board naming the same donor draw it down between them rather than each being
        offered the whole of it.

        `pool=True` asks the same question of the pool's own book (R34, step 4b).
        """
        if not fact.product_id:
            return []
        result = self.planning_assignments([fact.product_id], as_of=as_of).get(
            str(fact.product_id)
        )
        if result is None:
            return []
        as_of = as_of or self._walk_as_of or date.today()
        today = as_of
        window = reserve_window_end(today, self._lead_time_days(fact.product_id))
        tba_from = self._tba_date_from()
        my_so = self._so_number_of(fact)
        # The asker's own agent, off the SAME book the donors come from - never a second
        # read, so "same agent" cannot mean one thing here and another in the donor row.
        mine = next(
            (
                self._assignment_line(fact, line_id, as_of=as_of)
                for line_id in self._unit_line_ids(fact)
            ),
            None,
        )
        my_agent = mine.line.agent_code if mine is not None else None
        pool_codes = {
            (w.warehouse_code or "") for w in self._site_pool_warehouses().values()
        }
        out: List[Dict[str, Any]] = []
        for row in result.lines:
            line = row.line
            if not self._eligible_donor(
                row, my_so=my_so, window=window, tba_from=tba_from, pool=pool
            ):
                continue
            left = None if borrow_left is None else borrow_left.get(str(line.key))
            by_location: Dict[str, Decimal] = {}
            for item in row.assigned:
                event = item.event
                if event.kind != SA_KIND_ON_HAND or not event.warehouse:
                    continue
                if pool != bool(event.is_pool or event.warehouse in pool_codes):
                    continue
                by_location[str(event.warehouse)] = by_location.get(
                    str(event.warehouse), _ZERO
                ) + max(_dec(item.qty), _ZERO)
            if not by_location:
                continue
            donor_group = sales_agent_service.group_of_warehouse_code(line.warehouse)
            for code, qty in sorted(by_location.items()):
                if left is not None:
                    qty = min(qty, max(_dec(left), _ZERO))
                    left = max(_dec(left) - qty, _ZERO)
                if qty <= _ZERO:
                    continue
                out.append(
                    {
                        "location": code,
                        "qty": qty,
                        "donor_so_number": line.so_number or None,
                        "donor_line_no": line.line_no,
                        "donor_agent_code": line.agent_code,
                        "donor_core_line_id": str(line.key),
                        "donor_required_date": line.required_date,
                        "donor_warehouse_code": line.warehouse,
                        "same_agent": bool(my_agent and line.agent_code == my_agent),
                        "same_group": bool(donor_group) and donor_group == fact.group_code,
                        "same_warehouse": code == fact.own_code,
                    }
                )
        out.sort(key=self._donor_order)
        return out

    def supply_borrow_candidates_for(
        self,
        fact: _LineFacts,
        *,
        as_of: Optional[date] = None,
        supply_left: Optional[MutableMapping[str, Decimal]] = None,
        need: Optional[Decimal] = None,
    ) -> List[Dict[str, Any]]:
        """Step 3 (R32, R33): the ONE document that covers the whole unit.

        A supply event - an SPO allocation - is a candidate when it is ELIGIBLE and when the
        whole of this unit can be met off it alone. Everything else about the step follows
        from those two words.

        **Incoming means SPO** (31 Aug ruling, R-A, retiring R27/R29/R30/R35's PO half). A
        PO is still ON ORDER with a computed date, not goods on the water, and "Borrow
        incoming"/"Use incoming" must never name one - the captain's own production row
        named `202607-S0067`, a real PO issued with no expected date, as the thing a
        planner was told to borrow. A unit only a PO could cover falls through to the pool
        step and then to Buy; that is the point, not a regression. The ASSIGNMENT still
        reads purchase orders - the SPO cut out of a PO is still netted (`_po_rows`), the
        stock table's "PO qty" column is unchanged, and Stock Debt reads the same rows -
        only this step stops OFFERING one.

        **Eligible** (R32): it arrives by the asker's own date, OR before a fresh purchase
        raised today would land (`as_of + lead`). The second half is the captain's, on
        AC-S4-2b's row: "if buy, it is going to arrive even later". Such a line is late, and
        it is the earliest thing the ladder still has.

        **One document, whole** (R33): the quantity available off ONE event has to cover the
        unit. An SPO of 5 beside an SPO of 7 does not cover a unit of 12, and the walk moves
        on rather than promising one delivery on two dates. That is why this returns the
        rows of a single document and not a merged list - the engine walks what it is given,
        and a flat list of every eligible document would let it combine them.

        **Nearest arrival wins.** Every candidate here is an SPO - ARRIVING, cut from a
        purchase order and put on a shipment - so the ordering is nearest arrival first,
        then R19's donor order so two documents landing the same day are broken the way
        every other donor list is.

        **What is available off one document** is three things added together: what nobody
        has been promised (`free` in the assignment), what the walk has ALREADY GIVEN THIS
        ASKER on it, and what an ELIGIBLE later order holds of it - the same window and the
        same donor rules step 2 applies (`_eligible_donor`), because a donor that cannot
        itself wait is no donor whether it is holding stock or a promise.

        The middle term is the one this step was missing (SO414244, 30 Aug 2026): the
        assignment had already put 56 of a 75-line on a purchase order landing inside the
        window, so 19 read free, the whole unit did not fit and a line ONE document covers
        was bought instead. It is not a borrow and it names no donor - taking what the walk
        has already given you owes nobody anything - so it counts beside `free`, and the two
        make one row.

        `supply_left` is the walk's ledger, keyed by event (`compose_lines`). It counts what
        has been DRAWN, not what is left, because "what is left" is personal: a document
        held by a line of the asker's OWN order is worth nothing to that asker and the whole
        of it to the next order along, so seeding the ledger with the first asker's answer
        capped everybody after it (the step-2 donor ledger keeps the same shape for the same
        reason). Without a ledger at all, every unit of a board reads the same document whole
        and `confirm` refuses all but the first - the defect four delivery dates of SO381895
        hit on the donor rung on 28 August 2026.

        `need` overrides the fact's own open quantity, for the board's trail: the walk asked
        about the whole planning UNIT (R10) and the trail is built per ROW, so re-reading it
        with the row's smaller quantity would offer a document the walk itself refused.
        """
        if not fact.product_id:
            return []
        as_of = as_of or self._walk_as_of or date.today()
        result = self.planning_assignments([fact.product_id], as_of=as_of).get(
            str(fact.product_id)
        )
        if result is None:
            return []
        need = max(_dec(fact.open_qty if need is None else need), _ZERO)
        if need <= _ZERO:
            return []
        lead = self._lead_time_days(fact.product_id)
        # The two dates eligibility is measured against (R32). `buy_lands` is the earliest a
        # purchase raised today could arrive, which is what makes a late document better
        # than nothing rather than merely late.
        buy_lands = as_of + timedelta(
            days=DEFAULT_LEAD_TIME_DAYS if lead is None else max(int(lead), 0)
        )
        window = reserve_window_end(as_of, lead)
        tba_from = self._tba_date_from()
        my_so = self._so_number_of(fact)
        mine = next(
            (
                self._assignment_line(fact, line_id, as_of=as_of)
                for line_id in self._unit_line_ids(fact)
            ),
            None,
        )
        my_agent = mine.line.agent_code if mine is not None else None
        my_line_keys = {str(key) for key in self._unit_line_ids(fact)}

        # What each event still has on the table: what nobody has spoken for (free plus this
        # asker's own share) first, then each eligible donor's hold of it in R19's order.
        # Keyed by event, because ONE document is the unit here.
        mine_by_event: Dict[str, Decimal] = {}
        rows_by_event: Dict[str, List[Dict[str, Any]]] = {}
        for row in result.lines:
            if str(row.line.key) in my_line_keys:
                # THE ASKER'S OWN SHARE. `_eligible_donor` refuses it - an order does not
                # borrow from itself - and that is right about borrowing and wrong about
                # availability, which is what this step measures.
                for item in row.assigned:
                    event = item.event
                    if event.kind != SA_KIND_SPO or event.is_pool:
                        continue
                    mine_by_event[str(event.key)] = mine_by_event.get(
                        str(event.key), _ZERO
                    ) + max(_dec(item.qty), _ZERO)
                continue
            if not self._eligible_donor(
                row, my_so=my_so, window=window, tba_from=tba_from
            ):
                continue
            donor_group = sales_agent_service.group_of_warehouse_code(row.line.warehouse)
            for item in row.assigned:
                event = item.event
                if event.kind != SA_KIND_SPO or event.is_pool:
                    continue
                qty = max(_dec(item.qty), _ZERO)
                if qty <= _ZERO:
                    continue
                rows_by_event.setdefault(str(event.key), []).append(
                    {
                        "qty": qty,
                        "donor_so_number": row.line.so_number or None,
                        "donor_line_no": row.line.line_no,
                        "donor_agent_code": row.line.agent_code,
                        "donor_core_line_id": str(row.line.key),
                        "donor_required_date": row.line.required_date,
                        "same_agent": bool(
                            my_agent and row.line.agent_code == my_agent
                        ),
                        "same_group": bool(donor_group)
                        and donor_group == fact.group_code,
                        "same_warehouse": event.warehouse == fact.own_code,
                    }
                )

        documents: List[Tuple[tuple, str, List[Dict[str, Any]]]] = []
        for event in result.supply:
            if event.kind != SA_KIND_SPO or event.is_pool:
                continue
            if not event.warehouse:
                # NOT A DOCUMENT THIS STEP MAY NAME. `assign()` honours a pinned hold whose
                # supply is outside the read span by standing an event up FROM THE HOLD
                # (AC-S2-1b) - no bin, and dated `as_of` because the span it was given holds
                # no arrival to place it on. Read as a real document that would make it one
                # landing TODAY, which is a promise about a date nobody knows.
                continue
            arrival = event.at
            if arrival is None:
                continue
            timely = fact.required_date is not None and arrival <= fact.required_date
            if not timely and arrival >= buy_lands:
                # No better than buying, so there is nothing to prefer it for (R32).
                continue
            free = max(_dec(result.free.get(str(event.key), 0)), _ZERO)
            unclaimed = free + max(_dec(mine_by_event.get(str(event.key), 0)), _ZERO)
            held = rows_by_event.get(str(event.key), [])
            budget = unclaimed + sum((entry["qty"] for entry in held), _ZERO)
            if supply_left is not None:
                budget = max(
                    budget - max(_dec(supply_left.get(str(event.key), 0)), _ZERO), _ZERO
                )
            if budget < need:
                # A document that cannot cover the unit on its own is not an option at all
                # (R33): the step gives nothing rather than half of something.
                continue
            rows: List[Dict[str, Any]] = []
            shared = {
                "location": event.warehouse,
                "supply_key": str(event.key),
                "supply_kind": event.kind,
                "supply_document": event.ref,
                "arrival_date": arrival,
                # R-O: how late the paperwork is, when `arrival` is the ASSUMED date the
                # grace period gave it rather than the one the document states. 0 says
                # there is nothing extra for the sentence to mention.
                "late_days": int(getattr(event, "days_late", 0) or 0),
            }
            if unclaimed > _ZERO:
                # Unclaimed first: it owes nobody - it is free, or it was already this
                # asker's - so it is the part of the document that costs the least to take.
                rows.append({**shared, "qty": unclaimed})
            for entry in sorted(held, key=self._donor_order):
                rows.append({**shared, **entry})
            documents.append(
                (
                    (
                        # Nearest arrival, then R19's donor order, then the document itself
                        # so two runs cannot disagree. R27/R35's SPO-before-PO ordering is
                        # retired with the PO half of this step (31 Aug ruling, R-A): every
                        # candidate reaching here is already an SPO.
                        arrival,
                        min(
                            (self._donor_order(entry) for entry in held),
                            default=(),
                        ),
                        str(event.ref or ""),
                        str(event.key),
                    ),
                    str(event.key),
                    rows,
                )
            )
        if not documents:
            return []
        documents.sort(key=lambda item: item[0])
        return documents[0][2]

    @staticmethod
    def _donor_order(candidate: Mapping[str, Any]) -> tuple:
        """R4 + R19, in one place: same agent, then latest date, then same group, then the
        asker's own bin. The document and the line break the last tie so two runs of the
        same book cannot disagree about which of two identical donors was offered first."""
        when = candidate.get("donor_required_date")
        return (
            not candidate.get("same_agent"),
            -(when.toordinal() if when is not None else 0),
            not candidate.get("same_group"),
            not candidate.get("same_warehouse"),
            str(candidate.get("donor_so_number") or ""),
            str(candidate.get("donor_core_line_id") or ""),
            str(candidate.get("location") or ""),
        )

    def _tba_date_from(self) -> date:
        from app.services.scm.priority import DEFAULT_TBA_DATE_FROM

        return self._fulfilment_settings().get("tba_date_from") or DEFAULT_TBA_DATE_FROM

    def group_water_for(self, fact: _LineFacts) -> List[_WaterAtLocation]:
        """What each of the group's locations has on the water for this line, timely and
        late, public so the board's question 1 can name the late half it did not draw."""
        return self._group_water(fact)

    def _group_water(self, fact: _LineFacts) -> List[_WaterAtLocation]:
        """The group's open SPO rows, per location, split on this line's required date.

        In the same draw order the floor uses (own location first, then the siblings by
        code), so the two halves of question 1 read as one walk. A location with no open SPO
        at all is left out entirely - there is nothing to offer and nothing to name.

        An UNDATED line (no required date) has nothing to be late for, so every open row
        counts as timely; that is the same boundary `timely_refs` has always used.
        """
        if not fact.product_id or not fact.group_code:
            return []
        siblings = self._group_sibling_warehouses(fact)
        ordered = sorted(
            siblings.items(), key=lambda item: (item[0] != fact.own_code, item[0])
        )
        rows_by_location = self._spo_by_location()
        out: List[_WaterAtLocation] = []
        for code, warehouse in ordered:
            timely = _ZERO
            late = _ZERO
            arrival: Optional[date] = None
            late_from: Optional[date] = None
            for ref in rows_by_location.get((fact.product_id, str(warehouse.id))) or []:
                qty = max(_dec(ref.qty), _ZERO)
                if qty <= _ZERO:
                    continue
                on_time = fact.required_date is None or (
                    ref.arrival_date is not None
                    and ref.arrival_date <= fact.required_date
                )
                if on_time:
                    timely += qty
                    if ref.arrival_date is not None and (
                        arrival is None or ref.arrival_date > arrival
                    ):
                        arrival = ref.arrival_date
                else:
                    late += qty
                    if ref.arrival_date is None:
                        continue
                    if late_from is None or ref.arrival_date < late_from:
                        late_from = ref.arrival_date
            if timely > _ZERO or late > _ZERO:
                out.append(
                    _WaterAtLocation(
                        location=code,
                        timely_qty=timely,
                        arrival_date=arrival,
                        late_qty=late,
                        late_from=late_from,
                    )
                )
        return out

    def _group_pile_members(self, fact: _LineFacts) -> List[Dict[str, Any]]:
        """Every OPEN demand line for one PRODUCT across a whole ownership GROUP (every
        `*-<group>` location), ranked TOGETHER by the active priority policy.

        Cached per `(product_id, group_code)`, not per line (S3): the query and the ranked
        order it produces are identical for every line that shares a product and a group -
        `locations` is the group's WHOLE membership regardless of which sibling is asking
        (own code included), so a board of N lines of one product/group used to pay N
        round trips for the exact same rows. `priority.active_policy` and
        `priority.payment_terms_by_customer` are read through the request-scoped helpers
        below for the same reason: unlike this list, THOSE could legitimately differ
        between calls, so they memoize by their own key rather than piggy-backing on this
        cache.
        """
        if not fact.group_code or not fact.own_code:
            return []
        cache_key = (fact.product_id or "", fact.group_code)
        if cache_key in self._group_pile_members_cache:
            return self._group_pile_members_cache[cache_key]
        locations = dict(self._group_sibling_warehouses(fact))
        if fact.own_code and fact.warehouse:
            locations[fact.own_code] = fact.warehouse
        if not fact.product_id or not locations:
            self._group_pile_members_cache[cache_key] = []
            return []
        warehouse_ids = [str(w.id) for w in locations.values()]
        rows = (
            self.db.query(
                SalesOrderLine, SalesOrder, SalesAgent.sales_agent,
                # `line_no` is the PROJECT mirror's own, the same way `_pile_book` reads
                # it: a core line nobody has adopted has none, and the ladder's own
                # display already treats a missing line number as absent (never "0").
                ProjectSalesOrderLine.line_no,
            )
            .join(SalesOrder, SalesOrder.id == SalesOrderLine.sales_order_id)
            .outerjoin(SalesAgent, SalesAgent.id == SalesOrder.sales_agent_id)
            .outerjoin(
                ProjectSalesOrderLine,
                ProjectSalesOrderLine.core_sales_order_line_id == SalesOrderLine.id,
            )
            .filter(
                SalesOrderLine.product_id == fact.product_id,
                SalesOrderLine.warehouse_id.in_(warehouse_ids),
                SalesOrder.status == "open",
                is_open_demand(),
                is_transferable_order(),
            )
            .all()
        )
        if not rows:
            self._group_pile_members_cache[cache_key] = []
            return []
        customer_ids = {str(order.customer_id) for _l, order, _a, _n in rows if order.customer_id}
        terms = self._payment_terms_for(customer_ids)
        warehouses_by_id = {str(w.id): code for code, w in locations.items()}
        members: List[Dict[str, Any]] = []
        for line, order, agent_code, line_no in rows:
            open_qty = max(_dec(line.qty_ordered) - _dec(line.qty_delivered), _ZERO)
            if open_qty <= _ZERO:
                continue
            members.append(
                {
                    "line_id": str(line.id),
                    "sales_order_id": str(line.sales_order_id),
                    "so_number": order.so_number or "",
                    "line_no": line_no,
                    "open_qty": open_qty,
                    "required_date": line.required_date or order.requested_delivery_date,
                    "order_date": order.order_date,
                    "demand_class": order.demand_class,
                    "payment_terms_days": terms.get(str(order.customer_id or "")),
                    "warehouse_code": warehouses_by_id.get(str(line.warehouse_id)),
                    "donor_agent_code": agent_code,
                    "donor_agent_id": str(order.sales_agent_id) if order.sales_agent_id else None,
                }
            )
        if not members:
            self._group_pile_members_cache[cache_key] = []
            return []
        weights, class_weights = priority.policy_weights(self._active_policy())
        self._rank_pile(members, weights=weights, class_weights=class_weights)
        self._group_pile_members_cache[cache_key] = members
        return members

    def _group_pile(
        self, fact: _LineFacts
    ) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
        """This line's group pile (`_group_pile_members`) and this line's own entry within
        it, or `None` when this line is not itself open demand at its pile (an
        amended/carried line has none to compare against, so no donor can be judged
        against it either)."""
        if not fact.product_id or not fact.core:
            return [], None
        members = self._group_pile_members(fact)
        if not members:
            return [], None
        mine = next((m for m in members if m["line_id"] == str(fact.core.id)), None)
        return members, mine

    def _group_borrow_donors(
        self, fact: _LineFacts
    ) -> List[Dict[str, Any]]:
        """Every OTHER open line's committed quantity at this line's ownership group,
        L first then the siblings (section 1c), each stating whether it is ranked
        below this line (auto-eligible) and whether it shares this line's agent (offered
        at any rank). Cached per line, since the sheet asks for both the auto list and the
        offer list off the same read.

        **Never a sibling line of this line's OWN sales order** (S2): borrowing from
        another line of the same order and raising an order-back against it is a
        borrow "against itself", not against another customer's commitment.
        **Never a line already covered by another active decision** (S2, parity with
        `_pile_book`'s own `_decided_elsewhere` exclusion): its committed quantity is
        already promised to whichever composition covers it, so it is not a stable donor
        for a different one - the same rule that keeps it out of the competing-demand
        pile keeps it out of the donor list. **Never a line that is ITSELF part of the
        current confirmation/board selection** (S2): its own state is moving in this same
        request, so it is not a pre-existing, stable donor either.
        """
        cache_key = str(fact.core.id) if fact.core else None
        if cache_key is not None and cache_key in self._group_donor_cache:
            return self._group_donor_cache[cache_key]
        members, mine = self._group_pile(fact)
        if mine is None:
            if cache_key is not None:
                self._group_donor_cache[cache_key] = []
            return []
        my_agent_id = mine.get("donor_agent_id")
        my_sales_order_id = mine.get("sales_order_id")
        my_index = members.index(mine)
        decided = self._decided_elsewhere_cached()
        donors: List[Dict[str, Any]] = []
        for index, member in enumerate(members):
            if member is mine:
                continue
            if member.get("sales_order_id") == my_sales_order_id:
                continue
            if member["line_id"] in decided:
                continue
            if member["line_id"] in self._current_selection_core_ids:
                continue
            location = member.get("warehouse_code")
            if not location:
                continue
            donor_agent_id = member.get("donor_agent_id")
            same_agent = bool(my_agent_id) and donor_agent_id == my_agent_id
            donors.append(
                {
                    "location": location,
                    # L first, then the siblings: the location's own draw order.
                    "location_rank": 0 if location == fact.own_code else 1,
                    "qty": member["open_qty"],
                    "donor_so_number": member.get("so_number") or None,
                    "donor_line_no": member.get("line_no"),
                    "donor_agent_code": member.get("donor_agent_code"),
                    "donor_core_line_id": member.get("line_id"),
                    "required_date": member.get("required_date"),
                    "same_agent": same_agent,
                    # Below this line in the SAME ranked pile - "ranked below this line".
                    "lower_ranked": index > my_index,
                    # Position in the ranked pile - the safest donor at one location is
                    # the LOWEST-ranked one (furthest down the queue), not the biggest.
                    "rank_index": index,
                }
            )
        donors.sort(key=lambda d: (d["location_rank"], d["location"], -d["rank_index"]))
        if cache_key is not None:
            self._group_donor_cache[cache_key] = donors
        return donors

    def borrow_candidates_for(
        self, fact: _LineFacts, *, need: Optional[Decimal] = None
    ) -> List[Dict[str, Any]]:
        """Where else this line could be met from, with what it costs the holder (AC-B09).

        Public for the board, which otherwise prints a bare Buy for a line whose stock exists
        one location away. Requires `demand_facts` or `_facts_for` to have run, because it
        reads the same free / holds caches the proposal was computed from.

        `need` is the line's RESIDUAL at the borrow rung - what the ladder was going to buy -
        and it is what the ranking is computed against (13.11). The caller states it because
        the caller is the one that just walked the ladder; omitted, the donors are ranked on
        availability alone.
        """
        return self._borrow_candidates(fact, need=need or _ZERO)

    def attribution_by_core_line(
        self, product_ids: Iterable[str], warehouse_ids: Iterable[str]
    ) -> Dict[Tuple[str, str], Dict[str, Dict[str, Decimal]]]:
        """Each CORE line's share of its product-location pile, keyed by core line id.

        The section 3.5 projection, made public because the confirmation judges a Reserve
        against it and therefore anything that PROPOSES a Reserve has to be judged against the
        same figure. The board used to share the pile among the orders in its own selection,
        which on a crowded location offered a line stock the book had already promised
        elsewhere: `confirm` then refused it, and the planner was told their own location was
        not their own location.

        Requires `demand_facts` (or `_facts_for`) to have run: it reads the free-stock cache
        that the proposal was computed from, so the share and the availability behind it cannot
        come from two different reads.
        """
        warehouses = self._warehouses(warehouse_ids)
        return self._attribution(
            product_ids, warehouse_ids, warehouses, self._spo_rows(product_ids, warehouse_ids)
        )

    def demand_facts(
        self,
        rows: Sequence[Dict[str, Any]],
        *,
        exclude_line_ids: Optional[Sequence[str]] = None,
    ) -> Dict[str, _LineFacts]:
        """`_LineFacts` for arbitrary CORE demand rows, keyed by the caller's own `key`.

        The board's way in to the same facts the sheet judges a line against: the fulfilment
        warehouse and its pool, the pool's free stock and reorder level, the dealer hot-selling
        verdict, product lifecycle, and the location's undelivered incoming. Each row states
        `key`, `product_id`, `warehouse_id`, `open_qty`, `required_date` and `item_code`.

        `own_free` and `timely_qty` are filled here too, from the SAME book-wide projection the
        confirmation judges against (`_attribution`): a line may reserve what is left of its
        pile after the demand the active policy ranks ahead of it, and nothing more. They used
        to be left for the caller, from when the board ran a contest of its own among the
        selected orders - which is exactly how it came to propose Reserves the confirmation
        refused.

        `exclude_line_ids` (PROJECT line ids, `confirm`'s own `replaced` shape) un-nets a
        line's own hold and keeps its demand in the queue - the same carve-out `confirm` gives
        the lines it is about to replace - so a covered line can be asked "what would the
        ladder propose today", as the planning-change batch does for a `replan` row.
        """
        product_ids = {str(r["product_id"]) for r in rows if r.get("product_id")}
        warehouse_ids = {str(r["warehouse_id"]) for r in rows if r.get("warehouse_id")}
        warehouses = self._warehouses(warehouse_ids)
        pool_ids = {
            str(w.pool_warehouse_id) for w in warehouses.values() if w.pool_warehouse_id
        }
        warehouses.update(self._warehouses(pool_ids - set(warehouses)))

        # S1/S2 (see `_facts_for`'s own copy of this): the lines this read is un-netting,
        # and the CORE lines of the whole selection this call is asking about at once - the
        # board's own "current selection" for `_group_borrow_donors`.
        self._replaced_line_ids = {str(line_id) for line_id in (exclude_line_ids or [])}
        self._current_selection_core_ids = {
            str(row["line_id"]) for row in rows if row.get("line_id")
        }
        self._decided_elsewhere_cache = None
        self._decided_anywhere_cache = None
        self._active_decision_rows = None
        self._request_product_ids = {str(pid) for pid in product_ids if pid}
        self._free_cache = self._drawable_free_stock(
            product_ids, exclude_line_ids=exclude_line_ids
        )
        self._holds_cache = self._holds_by_project(
            product_ids, exclude_line_ids=exclude_line_ids
        )
        self._pile_cache = None
        self._po_open_cache = None
        self._netting_cache = None
        self._spo_by_location_cache = None
        # Eager, not lazy: a project hot-selling line's pool draw is capped against the
        # pool's signed availability on every read, not only when a donor list is asked for.
        pool_piles = self._pile_facts()
        (
            dealer_hot, project_hot, unavailable, dealer_where, project_where,
            dealer_classified_set, project_classified_set,
        ) = self._classification(product_ids)
        levels = self._reorder_levels(product_ids, pool_ids)
        discontinued = self._discontinued(product_ids)
        # ONE dated SPO read for the whole request, at every active warehouse: question 1
        # draws water at the group's SIBLINGS as well as at the line's own location, and the
        # attribution below wants the same rows. Two identical narrow reads used to stand
        # here, so the wider span costs a query rather than adding one.
        spo = self._spo_by_location()

        attribution = self._attribution(
            product_ids,
            warehouse_ids,
            warehouses,
            spo,
            exclude_line_ids=exclude_line_ids,
            # Only these lines are asking, so only these lines' queues are described. Every
            # other line of the pile still counts toward them - it is named, not counted, that
            # is being narrowed here.
            detail_for={
                str(row.get("line_id") or row["key"]) for row in rows if row.get("key")
            },
        )
        # What the shared pool's own book claims ahead of each line that would draw on it.
        pool_ahead = self.pool_claims(
            product_ids,
            {
                str(w.pool_warehouse_id)
                for w in warehouses.values()
                if w.pool_warehouse_id
            },
            [
                {
                    "key": str(row["key"]),
                    "product_id": str(row["product_id"]),
                    "pool_id": str(
                        warehouses[str(row["warehouse_id"])].pool_warehouse_id
                    ),
                    "required_date": row.get("required_date"),
                    "order_date": row.get("order_date"),
                    "payment_terms_days": row.get("payment_terms_days"),
                    "demand_class": row.get("demand_class"),
                    "so_number": row.get("so_number"),
                    "line_no": row.get("line_no"),
                    # The core line, when the caller named one, so a member fulfilled from
                    # the pool itself is not counted ahead of itself.
                    "line_id": row.get("line_id"),
                }
                for row in rows
                if row.get("product_id")
                and row.get("warehouse_id")
                and str(row["warehouse_id"]) in warehouses
                and warehouses[str(row["warehouse_id"])].pool_warehouse_id
            ],
            exclude_line_ids=exclude_line_ids,
        )

        facts: Dict[str, _LineFacts] = {}
        for row in rows:
            product_id = str(row["product_id"]) if row.get("product_id") else None
            warehouse = (
                warehouses.get(str(row["warehouse_id"])) if row.get("warehouse_id") else None
            )
            pool = (
                warehouses.get(str(warehouse.pool_warehouse_id))
                if warehouse and warehouse.pool_warehouse_id
                else None
            )
            required_date = row.get("required_date")
            key = (product_id or "", str(warehouse.id) if warehouse else "")
            timely_refs = [
                ref
                for ref in spo.get(key, [])
                if required_date is None
                or (ref.arrival_date is not None and ref.arrival_date <= required_date)
            ]
            # The projection is keyed by CORE LINE id; the caller's own `key` may be anything
            # (the board's is a cell address). It states `line_id` when the two differ.
            shares = attribution.get(key, {}).get(
                str(row.get("line_id") or row["key"]), {}
            )
            claim = pool_ahead.get(str(row["key"]), {})
            claimed = _dec(claim.get("qty"))
            pool_triple = pool_piles.get((product_id, str(pool.id))) if pool and product_id else None
            facts[str(row["key"])] = _LineFacts(
                line_no=row.get("line_no"),
                # The CORE line this row IS. The board has no project mirror to read a
                # `core` off, and ladder v7.1 addresses the assignment by core line id, so
                # a board fact with none would read step 1 as empty on every line.
                unit_core_line_ids=(
                    [str(row["line_id"])] if row.get("line_id") else []
                ),
                # R7 (query-count): the same row already carries these off the board's own
                # demand read - see the field's own docstring.
                source_ref=row.get("source_ref"),
                sales_order_id=(
                    str(row["sales_order_id"]) if row.get("sales_order_id") else None
                ),
                line_company_id=(
                    str(row["company_id"]) if row.get("company_id") else None
                ),
                item_code=row.get("item_code"),
                product_id=product_id,
                open_qty=_dec(row.get("open_qty")),
                # The still-owed figure `_group_offer` un-nets. Absent from the payload it
                # defaulted to zero, which silently turned the offer into the raw group net
                # for every board row (review round 2, N1).
                owed_qty=_dec(row.get("owed_qty")),
                required_date=required_date,
                warehouse=warehouse,
                pool=pool,
                own_free=_dec(shares.get(RESERVE)),
                timely_qty=_dec(shares.get(TIMELY_SPO)),
                so_qty_ahead=_dec(shares.get("so_qty_ahead")),
                lines_ahead=int(shares.get("lines_ahead") or 0),
                available_to_this_line=_dec(shares.get("available_to_this_line")),
                ahead_lines_named=list((shares.get("ahead_detail") or {}).get("lines") or []),
                ahead_more=int((shares.get("ahead_detail") or {}).get("more") or 0),
                ahead_by_factor=dict(
                    (shares.get("ahead_detail") or {}).get("by_factor") or {}
                ),
                # The pool nets its own book the same way the own location does: what is left
                # after the lines the policy ranks ahead of this one there.
                pool_free=(
                    max(self._free_at(product_id, str(pool.id)) - claimed, _ZERO)
                    if pool
                    else _ZERO
                ),
                pool_claimed_qty=claimed if pool else _ZERO,
                pool_claimed_lines=int(claim.get("lines") or 0) if pool else 0,
                pool_reorder_level=levels.get(
                    (product_id or "", str(pool.id) if pool else ""), _ZERO
                ),
                is_dealer_hot_selling=(product_id or "") in dealer_hot,
                dealer_hot_selling_where=list(dealer_where.get(product_id or "", [])),
                is_project_hot_selling=(product_id or "") in project_hot,
                project_hot_selling_where=list(project_where.get(product_id or "", [])),
                dealer_classified=(product_id or "") in dealer_classified_set,
                project_classified=(product_id or "") in project_classified_set,
                pool_available=(
                    pool_triple["on_hand"] - pool_triple["so_qty"] + pool_triple["spo_qty"]
                    if pool_triple
                    else _ZERO
                ),
                classification_unavailable=(product_id or "") in unavailable,
                is_discontinued=(product_id or "") in discontinued,
                timely_refs=timely_refs,
                advisory_refs=[ref for ref in spo.get(key, []) if ref not in timely_refs],
                group_code=sales_agent_service.group_of_warehouse_code(
                    warehouse.warehouse_code if warehouse else None
                ),
            )
            self._apply_group_nets(facts[str(row["key"])])
        return facts

    def _apply_group_nets(self, fact: _LineFacts) -> None:
        """Ladder v4 (section 1d): stamp the two piles on the line, and net rung 1 against
        the group's own.

        ONE copy of the rule, called by both fact builders, because the sheet and the board
        proposing different quantities for one line is the exact failure this module keeps
        being repaired for.

        `timely_qty` is netted here rather than in the ladder because it is read by
        everything downstream - the trail, the confirm-time recheck, the frozen snapshot -
        and a figure that means "arriving" in one place and "arriving and free for this
        line" in another is two numbers under one name. `MWH-IB`'s SPO of 110 is real and is
        still shown (`timely_qty_before_group_net`); what it is not is available to a line
        whose group already owes 2,335 against it.

        LADDER V5, second pass (captain, 27 August 2026): `timely_qty` is now exactly the
        WATER SHARE question 1 offers - what `_group_take_candidates` marks `water`, and not
        a unit more. The two halves of that question come off ONE ledger, so a confirmation
        cannot post 80 of timely SPO beside a 40 Reserve against a group that only ever
        offered 40 of each. Capping at `group_offer` alone let exactly that through, because
        the floor share was never subtracted from it.
        """
        netting = self.netting()
        group = netting.group_net(fact.product_id, fact.group_code)
        pools = netting.pools_net(fact.product_id)
        fact.group_net = group.net
        fact.group_net_by_location = list(group.by_location)
        fact.pools_net = pools.net
        fact.group_offer = (
            self._group_offer(fact, group) if fact.group_code else _ZERO
        )
        fact.timely_qty_before_group_net = fact.timely_qty
        if fact.group_code:
            fact.timely_qty = sum(
                (
                    _dec(candidate.get("qty"))
                    for candidate in self._group_take_candidates(fact)
                    if candidate.get("water")
                ),
                _ZERO,
            )

    def _group_offer(self, fact: _LineFacts, group: Any) -> Decimal:
        """What the group's net leaves for this line: `max(group_net + its own OWED
        quantity, 0)`.

        THE CAPTAIN'S RULE, 26 August 2026, stated in the plan and in AC-L7: a group that
        cannot cover its own book offers nothing to any line of it, however much sits at any
        one of its locations. `B2155-NL-BLUE` nets -15514 across `BRW-IB` and `MWH-IB`, so
        the 60 is bought and `MWH-IB`'s 7000 is never on the table - it is already owed at
        `BRW-IB`, and promising it here is what creates the double allocation this ruling
        exists to stop.

        The rank queue takes no part. It was v3's own-location cap
        (`available_to_this_line`), it decided how much this line could have out of one
        WAREHOUSE, and it is exactly what the ruling replaced.

        Its OWN quantity is un-netted, and that is arithmetic rather than a softening:
        `sum(SO)` counts every open line at the group INCLUDING this one, so a line standing
        alone on exactly the stock it needs would read a net of zero and buy stock that is
        sitting there waiting for it. Every OTHER line's demand stays netted.

        WHAT IS UN-NETTED IS `owed_qty`, NOT `open_qty`. The group net is the netting
        engine's own figure and it counts `demand_qty()` - what is STILL OWED - so adding
        back the plan quantity would return more than was ever subtracted: a line 3 ordered
        and 3 delivered contributed nothing to the net and would have handed its group a
        free 3, offering stock the group does not have. The two were the same number until
        the 14 September 2026 ruling split them.

        THE CONSEQUENCE, named rather than buried: on a group whose book runs ahead of its
        stock, EVERY line of that group buys - the line at the front of the queue included.
        1,015 on hand against 9,080 owed proposes a Buy for the 80 at the front as well as
        for the 9,000 behind it. That is the rule as ruled: while the group is short, its
        stock is not promised to anybody in particular, and whoever ships first uses it.
        """
        return max(group.net + max(_dec(fact.owed_qty), _ZERO), _ZERO)

    def _pool_allowances(self, fact: _LineFacts) -> Dict[str, str]:
        """`{warehouse_id: available_for_project}` for every site pool this line's own
        walk consulted (B2, fix round 5).

        THE SAME figures the walk obeyed, read off `pool_chain_for` and `fact.pools_net` -
        never a second arithmetic. The per-order SHEET (`SupplyCompositionSection`) has no
        cell to read `poolShareLimitsOf` off, so it composed a line seeded from the engine's
        own "BRW 62 + Buy 73" (LADDER V8, R-C) and then refused to confirm its own suggestion,
        because `lineBlockers` ran with no `limits` at all and the whole-line rule saw a mix.
        This is the sheet's OWN source for those limits, one line at a time.
        """
        pct = self._fulfilment_settings().get("pool_share_pct")
        allowances: Dict[str, str] = {}
        for entry in self.pool_chain_for(fact):
            warehouse_id = self.warehouse_id_for_code(entry.get("location"))
            if not warehouse_id:
                continue
            allowances[warehouse_id] = qty_text(
                available_for_project(entry.get("available"), fact.pools_net, pct)
            )
        return allowances

    def _serialize_line(
        self,
        fact: _LineFacts,
        components: Sequence[Component],
        frozen: Optional[Dict[str, Any]],
        *,
        unit_qty: Optional[Decimal] = None,
        unit_line_count: int = 1,
        options: Sequence[Option] = (),
        buy_origin: Optional[str] = None,
    ) -> Dict[str, Any]:
        line = fact.line
        return {
            "project_line_id": str(line.id),
            # The FIVE steps of ladder v7.1, answered (R36, AC-S3-14). The decision panel
            # reads them here and the board's own contribution reads them there; one
            # builder, so the two screens cannot show a different table for one unit.
            "options": [
                {
                    "step": option.step,
                    "label": option.label,
                    "whole": bool(option.whole),
                    # LADDER V8 (C5, code review round 3 batch 2): `gives_qty` and `reason`
                    # never reached this sheet, only the board's own `_option_row` - so the
                    # sheet's Gives column (`SupplyLineCard` -> the shared
                    # `BoardLadderOptionsTable`) rendered blank. Same two fields, same
                    # computation as `_option_row` in `project_fulfilment_board_service.py`.
                    "gives_qty": (
                        qty_text(_dec(getattr(option, "gives_qty", None)))
                        if getattr(option, "gives_qty", None) is not None
                        else None
                    ),
                    "reason": getattr(option, "reason", None),
                    "fulfil_date": (
                        option.fulfil_date.isoformat() if option.fulfil_date else None
                    ),
                    "days_late": option.days_late,
                    "debt_so_number": option.debt_so_number,
                    "debt_month": option.debt_month,
                    "chosen": bool(option.chosen),
                }
                for option in options
            ],
            "line_no": line.line_no,
            #: The PLANNING UNIT this line was composed in (ladder v6): this order's lines
            #: for the same item, location and delivery date are one quantity, covered whole
            #: from stock or bought whole. `1` and the line's own quantity when it was
            #: planned alone, which is most lines.
            "unit_qty": qty_text(
                fact.open_qty if unit_qty is None else unit_qty
            ),
            "unit_line_count": unit_line_count,
            "item_code": fact.item_code,
            "product_id": fact.product_id,
            # S3 (`PLAN-local-supplier-oi-routing.md`, AC-2.14): every line carries it,
            # filled from the SAME `buy_origin_by_product` call `proposal_for` makes once
            # for the whole sheet - never a second per-line lookup.
            "buy_origin": buy_origin,
            "description": line.description,
            "uom": line.uom,
            "open_qty": qty_text(fact.open_qty),
            "required_date": fact.required_date,
            "fulfilment_location": fact.own_code,
            "is_dealer_hot_selling": fact.is_dealer_hot_selling,
            "is_project_hot_selling": fact.is_project_hot_selling,
            "dealer_classified": fact.dealer_classified,
            "project_classified": fact.project_classified,
            "classification_unavailable": fact.classification_unavailable,
            "is_discontinued": fact.is_discontinued,
            "pool_location": fact.pool_code,
            # The old reorder-level cap never applied above own-location Reserve and it no
            # longer applies to the pool either (19 August 2026): dealer hot-selling offers
            # the pool nothing, project hot-selling caps it by availability instead. Null,
            # never a number a limit nobody set would read as.
            "pool_cap": None,
            "pool_reorder_level": (
                qty_text(fact.pool_reorder_level) if fact.pool_code else None
            ),
            # B2 (fix round 5): the pool-share carve-out (R-C), stated on the sheet's own
            # line the same way the board's cell states it on its locations - see
            # `_pool_allowances`. Without them the sheet's `lineBlockers` had no `limits` and
            # refused the engine's own "BRW 62 + Buy 73" as a mix.
            "pool_allowances": self._pool_allowances(fact),
            "pools_net": qty_text(fact.pools_net),
            "components": [
                self._serialize_component(component, fact) for component in components
            ],
            "timely_spo": [self._serialize_spo(ref) for ref in fact.timely_refs],
            "advisory_spo": [self._serialize_spo(ref) for ref in fact.advisory_refs],
            # Ranked against what this line still has to cover - the Buy the ladder just
            # proposed - because that is the quantity a donor would be asked for (13.11).
            # An unplannable line is offered nobody's stock: there is no need to rank against.
            # Neither is a line beyond its reserve window (the ATP rule): rungs 4 and 5 are
            # not walked for it, so a donor list beside it would OFFER the one thing the rule
            # exists to refuse.
            "borrow_candidates": (
                []
                if fact.unplannable_reason or self.outside_reserve_window(fact)
                else self._borrow_candidates(
                    fact,
                    need=sum(
                        (
                            component.qty
                            for component in components
                            if component.kind == BUY
                        ),
                        _ZERO,
                    ),
                )
            ),
            "unplannable_reason": fact.unplannable_reason,
            "frozen": frozen,
            # Covered by the order's active revision, or explicitly not (13.4). Never
            # inferred from `frozen` being absent by whoever reads this next: an undecided
            # line and a line decided to nothing would look identical if it were.
            "decided": frozen is not None,
        }

    def _serialize_component(
        self, component: Component, fact: _LineFacts
    ) -> Dict[str, Any]:
        warehouse_id = self._resolve_source_warehouse_id(component.source_location, fact)
        return {
            "kind": component.kind,
            "qty": qty_text(component.qty),
            "reason": component.reason,
            "source_location": component.source_location,
            "source_warehouse_id": warehouse_id,
            "rung": component.rung,
            "donor_so_number": component.donor_so_number,
            "donor_line_no": component.donor_line_no,
            "donor_agent_code": component.donor_agent_code,
            "same_agent": component.same_agent,
            "order_back_qty": (
                qty_text(component.order_back_qty)
                if component.order_back_qty is not None
                else None
            ),
            "donor_core_line_id": component.donor_core_line_id,
            "donor_required_date": component.donor_required_date,
            # Step 3 (S4): WHICH document, how it is named, and when it lands. Carried on
            # every component rather than only on a `supply_borrow` one, because a reader
            # that has to ask which kind it is holding before it knows which keys exist is
            # a reader that will one day drop the keys.
            "supply_key": component.supply_key,
            "supply_document": component.supply_document,
            "arrival_date": component.arrival_date,
            # R7: `"own_arrival"` on a Reserve born from the own-arrival credit, `None`
            # everywhere else - what `set_row_decision` reads to refuse a Buy amend over it.
            "source": component.source,
        }

    def _resolve_source_warehouse_id(
        self, source_location: Optional[str], fact: _LineFacts
    ) -> Optional[str]:
        """A component's warehouse CODE, resolved to an id (addressing only).

        Own location and pool first, at no query cost - the common case. Ladder v2's
        group take / group borrow / cross-group borrow rungs name a SIBLING or an outside
        location the fact does not carry a reference for, so those fall back to a
        memoized by-code lookup (`_warehouse_by_code`).
        """
        if not source_location:
            return None
        if fact.own_code == source_location and fact.warehouse:
            return str(fact.warehouse.id)
        if fact.pool_code == source_location and fact.pool:
            return str(fact.pool.id)
        warehouse = self._warehouse_by_code(source_location)
        return str(warehouse.id) if warehouse else None

    def _warehouse_by_code(self, code: str) -> Optional[Warehouse]:
        if code not in self._warehouse_code_memo:
            self._warehouse_code_memo[code] = (
                self.db.query(Warehouse)
                .filter(Warehouse.warehouse_code == code, Warehouse.is_active.is_(True))
                .first()
            )
        return self._warehouse_code_memo[code]

    def _serialize_spo(self, ref: _SpoRow) -> Dict[str, Any]:
        return {
            "spo_number": ref.spo_number,
            "arrival_date": ref.arrival_date,
            "qty": qty_text(ref.qty),
            "overdue_days": ref.overdue_days,
        }

    def frozen_lines_of(
        self, decision: Optional[SOSupplyDecision]
    ) -> Dict[str, Dict[str, Any]]:
        """`_frozen_by_line` for a caller outside this service (the planning board).

        Public because the board reads back the same snapshots the sheet does, and a second
        parse of `line_snapshots` on that side would be a second opinion about what a decision
        covered.
        """
        return self._frozen_by_line(decision)

    def _frozen_by_line(
        self, decision: Optional[SOSupplyDecision]
    ) -> Dict[str, Dict[str, Any]]:
        """What the active revision froze, so a confirmed line states what it was balanced
        against rather than the quantity that is live now."""
        if decision is None:
            return {}
        out: Dict[str, Dict[str, Any]] = {}
        for snapshot in decision.line_snapshots or []:
            line_id = str(snapshot.get("project_line_id") or "")
            if not line_id:
                continue
            out[line_id] = {
                "open_qty": str(snapshot.get("open_qty") or "0"),
                "components": list(snapshot.get("components") or []),
                # What the engine had proposed, beside what was decided (AC-D1). NONE, not
                # an empty list, when the key is absent: a revision written before this
                # field existed recorded no proposal, which the board says out loud rather
                # than printing as "the engine proposed nothing".
                "proposed_components": (
                    list(snapshot["proposed_components"])
                    if snapshot.get("proposed_components") is not None
                    else None
                ),
                # Read back beside the components it explains. A snapshot written before the
                # field existed simply has none, which is the same answer as "nobody amended
                # this line" and reads identically on screen.
                "amend_reason": snapshot.get("amend_reason"),
                # The doubt the planner recorded beside that reason (R10). Same rule: a
                # snapshot written before the checkbox existed carries none, which reads as
                # "nobody flagged it".
                "suspected_system_issue": bool(snapshot.get("suspected_system_issue")),
                "buy_reason": snapshot.get("buy_reason"),
                "order_back": bool(snapshot.get("order_back")),
                "cited_document": snapshot.get("cited_document"),
            }
        return out

    def _serialize_decision(
        self, decision: Optional[SOSupplyDecision]
    ) -> Optional[Dict[str, Any]]:
        if decision is None:
            return None
        name = None
        if decision.confirmed_by:
            user = (
                self.db.query(User.name).filter(User.id == decision.confirmed_by).first()
            )
            name = user[0] if user else None
        return {
            "revision_no": decision.revision_no,
            "state": decision.state,
            "confirmed_by_name": name,
            "confirmed_at": decision.confirmed_at,
            "challenged_reason": (
                decision.superseded_reason
                if decision.state == DECISION_CHALLENGED
                else None
            ),
        }

    def _review_state(self, order: ProjectSalesOrder) -> Optional[str]:
        from app.services.project_so_reconciliation_service import (
            ProjectSOReconciliationService,
        )

        states = ProjectSOReconciliationService(self.db).review_states_for([str(order.id)])
        return (states.get(str(order.id)) or {}).get("review_state")

    def _header_fields(self, order: ProjectSalesOrder) -> Dict[str, Any]:
        row = (
            self.db.query(Project.project_code, Project.title)
            .filter(Project.id == order.project_id)
            .first()
        )
        return {
            "project_code": row[0] if row else None,
            "project_name": row[1] if row else None,
        }

    # ------------------------------------------------------- supersede / challenge

    def supersede_for_material_change(
        self, order: ProjectSalesOrder, reason: str
    ) -> bool:
        """AC-C06: a material change retires the active revision, with no replacement.

        The whole SO goes back to Needs CS review. Nothing about the components is
        deleted: the superseded revision and its allocations stay for audit, and any Buy
        already placed stays in purchasing's ledger.
        """
        decision = self.active_decision(str(order.id))
        if decision is None:
            return False
        decision.state = DECISION_SUPERSEDED
        decision.superseded_at = datetime.utcnow()
        decision.superseded_reason = reason
        # ...except a step-3 PLACEMENT, which is a hold on somebody else's document rather
        # than a record of what was decided (S4). It goes with the revision, for the reason
        # every other hold does.
        self._release_supply_borrow_holds(order, decision, reason=reason)
        self.db.flush()
        return True

    def _release_supply_borrow_holds(
        self, order: ProjectSalesOrder, decision: SOSupplyDecision, *, reason: str
    ) -> None:
        """This revision's step-3 placements, given back with the revision itself (S4).

        A revision that is superseded or challenged promises nothing any more, and a
        placement is a promise about a document: left standing it goes on pinning it to a
        decision the board is already re-proposing. The rest of the revision behaves the
        same way - its holds stop counting the moment it stops being active - and this is
        the one part of it that lives outside `so_line_allocations`.
        """
        from app.services.project_order_inquiry_service import (
            ProjectOrderInquiryService,
        )

        ProjectOrderInquiryService(self.db).retire_supply_borrow_rows(
            str(order.id),
            reason=reason,
            line_ids=[
                str(snapshot.get("project_line_id") or "")
                for snapshot in (decision.line_snapshots or [])
                if snapshot.get("project_line_id")
            ],
        )

    # ---------------------------------------------------------------- the commit

    def confirm(
        self,
        order: ProjectSalesOrder,
        payload: Any,
        *,
        actor_user_id: str,
        uncover_line_ids: Sequence[str] = (),
        uncover_reason_by_line: Optional[Mapping[str, str]] = None,
        settle_in_place_line_ids: Sequence[str] = (),
        defer_auto_place: bool = False,
    ) -> Dict[str, Any]:
        """One transaction, every CHOSEN line, or nothing (PLAN 3.1, AC-C01 as amended
        by PLAN-fulfilment-planning-from-autocount-so.md 13.4).

        The payload names the lines being confirmed. They commit together or not at all;
        a line it does not name and that NO active revision covers stays undecided and
        keeps flowing to reorder planning. A payload naming NO line is refused, because
        superseding the revision that is holding stock and putting nothing in its place
        is not a decision anybody made.

        **The union is the server's.** A line the ACTIVE revision covers that the payload
        does not name is CARRIED FORWARD into the new revision verbatim: the same frozen
        snapshot dict, the same holds copied row for row, no re-validation against live
        facts. The board posts only what the planner just decided (at day granularity it
        cannot even see every covered line), and re-posting a covered line rebuilt from
        its snapshot re-judged it against facts that had moved since - a discontinued
        product's covered line 422'd the confirmation of an unrelated one. A named line
        REPLACES its frozen one (that is an amendment).

        **The un-decide seam** (PLAN-so-book-diff-replanning.md section 10, defect found
        live 19 August 2026): a covered line this call is meant to DROP - not replace,
        not carry - names itself in `uncover_line_ids` instead of the payload. Without
        this, a planning-change batch's `release`/`replan`/`retire` row deliberately left
        OUT of the payload (so it returns to the board undecided) was carried forward
        verbatim by the rule above the instant any OTHER line on the SAME order WAS named -
        seen live: SO403765 rev 5 kept line 12's old Buy and old date after an ADVANCE was
        raised for it, because line 8's Release was the only line actually posted and line
        12 rode along uninvited. Named here, a line's snapshot is excluded from the carry
        outright: its hold is gone (`_carry_allocations` never runs for it), `_borrow_shortfalls`
        reads it as gone (`checked` no longer contains it), and `refresh_for_decision`
        treats it exactly as a line "absent from `buy_lines`" already does - cancels
        whatever it had raised. The only OTHER way a covered line leaves the decision
        remains a material change superseding the whole revision
        (`supersede_for_material_change`, which carries nothing at all) or a drift
        challenging it.

        `uncover_reason_by_line` (S2/S3, `PLAN-board-reject-on-confirmed-line.md`, rework
        fix round): the bare per-line reason for EACH id in `uncover_line_ids`, threaded
        to `refresh_for_decision` so a withdrawn line's raised row reads "Taken out of
        the confirmation: <its own reason>" rather than the ordinary carry-forward's
        "Superseded by revision N". `None`/absent (an ordinary planning-change release,
        which names no reason of its own) keeps that ordinary default.

        **The settle-in-place seam** (`PLAN-scm-cs-planning-uat.md` part 3, AC-P3-5): a
        line named in `settle_in_place_line_ids` has its existing order inquiry row
        UPDATED - same id, new quantity, new date, links kept - instead of superseded and
        re-raised. Only a planning change sets it: it is the one caller that knows the
        book moved the same instruction rather than issuing a new one.

        **No confirmation links anything** (`PLAN-scm-oi-handshake.md`, captain 27 Aug
        2026). The rows this raises come out `awaiting`, and a document is tied to one only
        when purchasing ACKNOWLEDGES it. `auto_place_products` still comes back - the
        planning-change apply runs its own pass after it has shifted a closed line's
        documents to the survivor - but that pass links an acknowledged row and nothing
        else, so an ordinary board confirm ends with every new row unlinked.

        The caller owns the commit. Everything here runs inside it, including the Order
        Inquiry refresh, so purchasing can never be told to buy something that was not
        also promised.
        """
        locked = (
            self.db.query(ProjectSalesOrder)
            .filter(ProjectSalesOrder.id == order.id)
            .with_for_update()
            .first()
        )
        order = locked or order
        if order.status not in CONFIRMABLE_STATUSES:
            raise AppException(
                status_code=409,
                message=(
                    "This sales order is not published yet, so there is nothing to promise "
                    "supply against."
                ),
                code="supply_order_not_published",
            )

        # No self-heal here (issue #969, B2 owner ruling 17 Sep 2026): the heal moved to the
        # board read (`FulfilmentBoardService.build`), the one place the FE derives
        # `no_mirror` from and the read confirm-all's own multi-order build comes from.
        # Confirm stays a pure write - AC-PR8 pins that confirming with no prior board read
        # mirrors nothing.
        lines = self.lines_of(str(order.id))
        by_id = {str(line.id): line for line in lines}
        payload_lines = list(getattr(payload, "lines", []) or [])
        if not payload_lines and not uncover_line_ids:
            raise AppException(
                status_code=422,
                message=(
                    "Nothing was chosen to confirm. Pick at least one line, or leave the "
                    "sales order as it is."
                ),
                code="supply_nothing_to_confirm",
            )
        # What the order's OWN active revision already holds per line. A resubmitted
        # component this order already holds is not a new ask: `_check_line` credits it
        # back so re-affirming a hold this order has held all along does not compete in
        # the queue against itself, or lose to a rival that only appeared after the hold
        # was taken. Slice E, one signal: a drift against the frozen snapshot is no longer
        # read here at all - the change batch is the only thing that supersedes an active
        # revision now, and `_write_decision` below finds this one still active and
        # supersedes it in the same transaction the new composition is written in.
        carried_holds = self._frozen_by_line(self.active_decision(str(order.id)))
        self._lock_stock(payload_lines, lines)

        named = {str(entry.project_line_id) for entry in payload_lines}
        # The NAMED lines are being replaced and the UNCOVERED lines are being released, and
        # both stop holding stock when this transaction commits, so neither may be netted
        # from what the payload is judged against: a released line whose hold stayed netted
        # was demand in the queue AND stock still on the floor at once, and the line the
        # board had just given its stock to was refused for it. A covered line the payload
        # leaves alone is carried forward with its holds, so it is judged as any other
        # order's covered line - hold netted, out of the queue - and the named lines cannot
        # be offered what it is still holding. An unreconciled line is refused only when it
        # is named.
        facts = self._facts_for(
            order,
            lines,
            replacing=named | {str(line_id) for line_id in uncover_line_ids},
            refuse_unmapped=named,
        )
        item_codes = {
            str(line.id): facts[str(line.id)].item_code for line in lines
        }

        stale: List[Dict[str, Any]] = []
        invalid: List[Dict[str, Any]] = []
        seen: set = set()
        checked: List[Tuple[ProjectSalesOrderLine, Any, _LineFacts]] = []
        # #1362 (owner, 29 Sep 2026): a Buy over goods that landed for the line is the
        # planner's recorded intent, confirmed with a notice - `_check_line` fills this,
        # `_write_decision` puts the landed fact on the Buy's order inquiry row for
        # purchasing, and the result returns it. Project line id -> notice line.
        self._landed_buy_notices: Dict[str, Dict[str, Any]] = {}
        # What is still available as the payload is walked, so two lines of the SAME
        # confirmation cannot each be sold the whole pile. The per-line facts say what was
        # free when the sheet was read; these say what is left after the lines before it
        # (S7: every pool AND every group-take sibling, not only this line's own pool).
        capacity_left = _CapacityLedger()
        borrow_left: _BorrowLedger = _BorrowLedger()
        # AC-S3-10: the same product -> location -> remaining ledger `compose_lines` keeps
        # for `own_arrival_credit_for` (R7), scoped to this ONE confirm call - a confirm
        # may name several lines of one order, and credit `_check_line` already granted an
        # earlier line in this same call must not be re-offered to a later one at the same
        # bin.
        own_arrival_left: Dict[str, Dict[str, Decimal]] = {}
        # THE COVERED SET, computed once and read twice: by the recheck below (which unit
        # each line was proposed in) and by the frozen proposal in `_write_decision`. It is
        # what an active revision still holds, less the lines this payload REPLACES and less
        # the lines it RELEASES - an uncovered line is being let go by this same transaction,
        # so it is undecided demand again and belongs in the walk and in the unit. Built from
        # two different subtractions, the freeze planned a unit the recheck then refused.
        covered_ids = {
            line_id
            for line_id in self._frozen_by_line(self.active_decision(str(order.id)))
            if line_id not in named and line_id not in set(uncover_line_ids)
        }
        # The PLANNING UNIT each line was proposed in (ladder v6), so the recheck seeds its
        # ledgers from the same quantity the ladder was asked about. Grouped exactly as
        # `_proposals_for` groups.
        units = self._unit_checks(lines, facts, covered=covered_ids)
        # #1362 item 3 (owner, 29 Sep 2026): a named line with NOTHING open left - delivered
        # after a decision was saved on it - is skipped, not checked and not guarded. Its
        # saved composition answers a quantity the line no longer has, so confirming it
        # refused the WHOLE order ("100 landed for this line ...; nothing to buy for it",
        # or "the components add up to 100 and the line is open for 0").
        # A planning change SETTLING a line to zero names it on purpose (its order inquiry
        # row is updated to the new quantity), so a settle-in-place line is never skipped.
        fulfilled: List[ProjectSalesOrderLine] = []
        settle_in_place = {str(line_id) for line_id in settle_in_place_line_ids}

        for entry in payload_lines:
            line = by_id.get(str(entry.project_line_id))
            if line is None:
                invalid.append(
                    {
                        "line_no": None,
                        "item_code": None,
                        "reason": "That line is not on this sales order any more.",
                    }
                )
                continue
            if str(line.id) in seen:
                invalid.append(
                    {
                        "line_no": line.line_no,
                        "item_code": item_codes.get(str(line.id)),
                        "reason": "This line appears twice in the composition.",
                    }
                )
                continue
            fact = facts[str(line.id)]
            if fact.open_qty <= _ZERO and str(line.id) not in settle_in_place:
                fulfilled.append(line)
                continue
            seen.add(str(line.id))
            checked.append((line, entry, fact))
            self._check_line(
                entry,
                fact,
                # A unit of its own for a line `_unit_checks` skipped, which is a line with
                # no reconciled AutoCount line: `_check_line` refuses it on its next
                # statement, and reaching that refusal matters more than the seed does.
                units.get(str(line.id))
                or _UnitCheck(fact=fact, timely_left=fact.timely_qty),
                capacity_left,
                borrow_left,
                stale,
                invalid,
                carried_holds,
                own_arrival_left,
            )

        # A line the payload does not name is NOT a failure any more (13.4). It is
        # undecided, deliberately, and its demand goes on flowing to reorder planning
        # untouched. What is still refused is a confirmation of nothing at all, below.

        # STRUCTURAL refusals first - a negative quantity, a line that is not on this order,
        # a composition that does not add up. They are about the PAYLOAD, and the on-hand
        # guard's sentence about a location would be answering a question that cannot be
        # asked yet of a body this malformed.
        if invalid:
            failing = invalid + stale
            raise SupplyLinesRefused(
                status_code=422,
                message=(
                    f"{len(failing)} line{'' if len(failing) == 1 else 's'} cannot be "
                    "confirmed. Nothing was written."
                ),
                failing_lines=failing,
            )

        # THE ON-HAND GUARD (R14), ahead of the staleness refusals below so its sentence is
        # the one the planner reads: it names the location AND the earlier order, where the
        # capacity message can only say how much is left for this line. Order is all that is
        # at stake - both refuse by raising, before `_write_decision`, so nothing is written
        # either way; which sentence comes back is the whole difference.
        # Released lines are excluded with the named ones: their holds are gone at commit
        # (same reading as `replacing` above), so they are not "other lines" holding here.
        self._check_reserve_against_on_hand(
            checked, named=seen | {str(line_id) for line_id in uncover_line_ids}
        )

        if stale:
            raise SupplyLinesRefused(
                status_code=409,
                message=(
                    f"{len(stale)} line{'' if len(stale) == 1 else 's'} cannot be "
                    "confirmed. Nothing was written."
                ),
                failing_lines=stale,
            )

        if fulfilled:
            # The stale decision is CLEARED, not marked superseded: a draft is a staged
            # verdict with no history of its own (`project_line_draft_service`), and on a
            # line with nothing open there is nothing left for any verdict to decide.
            from app.services import project_line_draft_service

            project_line_draft_service.delete_drafts_for_lines(
                self.db,
                [line.core_sales_order_line_id for line in fulfilled],
                company_id=str(order.company_id) if order.company_id else None,
            )
        if not checked and not uncover_line_ids:
            # Every named line was already fulfilled: nothing to write, and a new revision
            # carrying the old one forward verbatim is not a decision anybody made.
            decided = len(self._frozen_by_line(self.active_decision(str(order.id))))
            return {
                "revision_no": None,
                "inquiry_rows_created": 0,
                "exceptions": [],
                "lines_decided": decided,
                "lines_undecided": max(len(lines) - decided, 0),
                "lines_fulfilled_skipped": len(fulfilled),
                "lines_confirmed": [],
                "lines_carried": decided,
            }

        carried = self._carried_lines(
            self.active_decision(str(order.id)), named=seen, by_id=by_id, facts=facts,
            uncover=set(uncover_line_ids),
        )
        body = self._write_decision(
            order,
            checked,
            # The order's lines and every line's facts, so the frozen proposal is composed
            # over the ORDER (the walk the sheet ran) rather than over the lines this
            # payload happens to name - and read here rather than queried again below.
            lines=lines,
            facts=facts,
            covered=covered_ids,
            carried=carried,
            actor_user_id=actor_user_id,
            # Part 3 (AC-P3-5): the lines a planning change is applying, whose order
            # inquiry row is UPDATED rather than superseded and re-raised.
            settle_in_place_line_ids=settle_in_place_line_ids,
            defer_auto_place=defer_auto_place,
            uncover_reason_by_line=uncover_reason_by_line,
            # The day the planner was deciding on (the board's own dial), so the proposal
            # frozen beside the decision is the one they were shown. Absent means today.
            as_of=getattr(payload, "as_of", None),
        )
        body["lines_fulfilled_skipped"] = len(fulfilled)
        body["landed_buy_notices"] = list(self._landed_buy_notices.values()) or None
        return body

    def _carried_lines(
        self,
        previous: Optional[SOSupplyDecision],
        *,
        named: set,
        by_id: Dict[str, ProjectSalesOrderLine],
        facts: Dict[str, _LineFacts],
        uncover: Optional[set] = None,
    ) -> List[_CarriedLine]:
        """The active revision's lines this confirmation did not name, verbatim - except
        the ones the caller named in `uncover` (the un-decide seam, `confirm`'s docstring):
        those are dropped from the new revision outright rather than carried.

        A snapshot for a line no longer on the order is not carried either: there is no
        row to hold stock for. Nor is one whose frozen link, open quantity or required
        date has moved since the revision that covered it, or whose core line the book
        CANCELLED (`_carry_snapshot_has_drifted`) - Slice E (one signal) judges that per
        line, here, rather than flipping the whole decision to `challenged`: the lines
        this confirmation DOES name still commit, and the drifted one is simply undecided
        again, exactly as a line the revision never covered would be. A line whose PRODUCT
        was renamed is carried, not excluded, its snapshot's identity patched to the live
        product (R2, 13 Sep browser walk) - its demand did not change, only what it is
        for. Everything else comes across untouched.
        """
        if previous is None:
            return []
        uncover = uncover or set()
        out: List[_CarriedLine] = []
        for snapshot in previous.line_snapshots or []:
            line_id = str(snapshot.get("project_line_id") or "")
            if not line_id or line_id in named or line_id not in by_id or line_id in uncover:
                continue
            fact = facts.get(line_id)
            if self._carry_snapshot_has_drifted(by_id[line_id], snapshot, fact):
                continue
            # R2 (renamed product, 13 Sep browser walk): the line's own quantity is
            # untouched, so it still carries - but never under a product that has left
            # the order. Patched onto a COPY; `snapshot` is the previous revision's own
            # stored dict and must read exactly as it was decided.
            live_product = str(fact.product_id) if fact and fact.product_id else None
            if live_product and str(snapshot.get("product_id") or "") != live_product:
                snapshot = dict(snapshot)
                snapshot["product_id"] = live_product
                if fact.item_code:
                    snapshot["item_code"] = fact.item_code
            out.append(
                _CarriedLine(line=by_id[line_id], snapshot=snapshot, fact=facts[line_id])
            )
        return out

    @staticmethod
    def _carry_snapshot_has_drifted(
        line: ProjectSalesOrderLine,
        snapshot: Dict[str, Any],
        fact: Optional[_LineFacts],
    ) -> bool:
        """Whether `line`'s live facts still match what `snapshot` froze - the per-line
        check `_carried_lines` uses in place of the retired whole-decision `challenge_if_
        drifted` flip.

        R2/R2c (13 Sep browser walk, SO400884): a core line the book CANCELLED must never
        be carried on the strength of the checks below alone. Caught explicitly rather
        than relied on to fall out of the open-qty comparison - `_open_of` now floors a
        cancelled line to zero, so most cancellations already disagree with a nonzero
        frozen `open_qty`, but a line frozen at zero already (nothing left to fulfil when
        it was confirmed) would not, and this line has genuinely left the book either way.

        A RENAMED product is not this function's concern: the line's own demand has not
        changed, only what it is for, and `_carried_lines` patches the carried snapshot's
        identity to the live product rather than excluding the line here - excluding it
        would read as the demand itself having gone, which is not what happened.
        """
        if fact is None:
            return True
        if fact.core is not None and (fact.core.line_status or "open") == "cancelled":
            return True
        frozen_core = snapshot.get("core_line_id")
        live_core = (
            str(line.core_sales_order_line_id) if line.core_sales_order_line_id else None
        )
        if frozen_core is not None and str(frozen_core) != (live_core or ""):
            return True
        frozen_open = snapshot.get("open_qty")
        if frozen_open is not None and _dec(frozen_open) != fact.open_qty:
            return True
        frozen_date = snapshot.get("required_date")
        live_date = fact.required_date.isoformat() if fact.required_date else ""
        if frozen_date is not None and str(frozen_date) != live_date:
            return True
        return False

    def _lock_stock(
        self,
        payload_lines: Sequence[Any],
        lines: Sequence[ProjectSalesOrderLine],
    ) -> None:
        """Lock every stock row the payload touches, in a deterministic order.

        Deterministic because two confirmations touching the same two locations in
        opposite orders deadlock, and a deadlock reads to CS as the button doing nothing.
        """
        # Both spellings of the product: `_facts_for` prefers the CORE line's product when
        # the two disagree (a remap), and locking only the Project line's would leave the
        # rechecked free-stock read unprotected on exactly the remapped line.
        product_ids = {str(line.product_id) for line in lines if line.product_id}
        core_ids = [
            str(line.core_sales_order_line_id)
            for line in lines
            if line.core_sales_order_line_id
        ]
        if core_ids:
            for (core_product_id,) in (
                self.db.query(SalesOrderLine.product_id)
                .filter(SalesOrderLine.id.in_(core_ids))
                .all()
            ):
                if core_product_id:
                    product_ids.add(str(core_product_id))
        warehouse_ids: set = set()
        for entry in payload_lines:
            for source in list(entry.reserve or []) + list(entry.borrow or []):
                if getattr(source, "warehouse_id", None):
                    warehouse_ids.add(str(source.warehouse_id))
        if not product_ids or not warehouse_ids:
            return
        (
            self.db.query(Stock)
            .filter(
                Stock.product_id.in_(list(product_ids)),
                Stock.warehouse_id.in_(list(warehouse_ids)),
            )
            .order_by(Stock.product_id.asc(), Stock.warehouse_id.asc())
            .with_for_update()
            .all()
        )

    def _carried_component_qty(
        self,
        carried_holds: Dict[str, Dict[str, Any]],
        line_id: str,
        kind: str,
        warehouse_id: str,
        *,
        donor_project_id: Optional[str] = None,
    ) -> Decimal:
        """What this order's own active (or just-challenged) revision already held here.

        Read off the frozen snapshot taken before this `confirm()` call, by exact
        (line, kind, location[, donor]) - not a total, so a component moved to a
        different location or a different donor carries nothing and competes fresh
        (PLAN-so-book-diff-replanning.md section 10, defect A). `kind`/`source_warehouse_id`
        /`donor_project_id` are the same keys `_snapshot` freezes a component under.
        """
        components = (carried_holds.get(line_id) or {}).get("components") or []
        total = _ZERO
        for component in components:
            if str(component.get("kind") or "") != kind:
                continue
            if str(component.get("source_warehouse_id") or "") != warehouse_id:
                continue
            if kind == BORROW and str(
                component.get("donor_project_id") or ""
            ) != (donor_project_id or ""):
                continue
            total += _dec(component.get("qty"))
        return total

    def _check_line(
        self,
        entry: Any,
        fact: _LineFacts,
        unit: "_UnitCheck",
        capacity_left: "_CapacityLedger",
        borrow_left: "_BorrowLedger",
        stale: List[Dict[str, Any]],
        invalid: List[Dict[str, Any]],
        carried_holds: Dict[str, Dict[str, Any]],
        own_arrival_left: Optional[Dict[str, Dict[str, Decimal]]] = None,
    ) -> None:
        """Recheck one line against authoritative facts (PLAN 3.1 steps 3 to 5).

        `unit` is the PLANNING UNIT this line was proposed in (ladder v6) - itself, for the
        ordinary line planned alone. Everything about the LINE is judged from `fact`: its
        open quantity, its balance, the whole-line rule. Everything about what the LADDER
        OFFERED is judged from the unit, because that is the quantity the ladder was asked
        about: the group's offer is `max(group net + the unit's demand, 0)`, and on a group
        in deficit the members' own offers sum to less than the unit's, so a per-line
        rederivation refuses the very split it proposed.
        """
        line = fact.line
        # #1362 item 5: `so_line_no` is the AutoCount line number off the core row, the one
        # the refusal sentence names; `line_no` stays the sheet's own address.
        subject = {
            "line_no": line.line_no,
            "so_line_no": fact.core.line_no if fact.core is not None else None,
            "item_code": fact.item_code,
            # #1362 hold-back: addressing only, so confirm-all can hold THIS line back and
            # confirm the rest of the order (`confirm_many`). Never rendered.
            "project_line_id": str(line.id),
        }

        def refuse(bucket: List[Dict[str, Any]], reason: str) -> None:
            bucket.append({**subject, "reason": reason})

        if fact.core is None:
            refuse(
                invalid,
                "This line has no reconciled AutoCount line, so there is no open "
                "quantity to promise against.",
            )
            return

        # Anything else the fact judged unplannable - today only a bin flagged out of
        # fulfilment planning (R17). The ladder proposed nothing for it, so a payload
        # naming it is a composition the engine never offered.
        if fact.unplannable_reason:
            refuse(invalid, fact.unplannable_reason)
            return

        timely = _dec(entry.timely_spo_qty)
        reserve_items = [_dec(item.qty) for item in entry.reserve or []]
        borrow_items = [_dec(item.qty) for item in entry.borrow or []]
        reserve_total = sum(reserve_items, _ZERO)
        borrow_total = sum(borrow_items, _ZERO)
        buy = _dec(entry.buy_qty)
        # Every ITEM, not just the per-kind totals: a negative row beside a positive one
        # sums clean, then inflates the capacity ledger when it is subtracted, and the
        # snapshot-vs-allocation split drops it - the over-Reserve would commit.
        if min([timely, buy, *reserve_items, *borrow_items], default=_ZERO) < _ZERO:
            refuse(invalid, "A component quantity is negative.")
            return

        # Against the UNIT's water, drawn down as its members are checked: question 1 offers
        # the unit one water share and the split can hand the whole of it to one line, whose
        # own line-level share is smaller. Identical to the old per-line test on a line
        # planned alone, which is most of them.
        if timely > unit.timely_left:
            refuse(
                stale,
                f"Timely SPO cover is now {qty_text(unit.timely_left)}, not "
                f"{qty_text(timely)}.",
            )
        else:
            unit.timely_left -= timely

        # Ladder v3 (section 1b rungs 2 and 3): a Reserve may name any location of this
        # line's ownership GROUP - its own included - or any active site pool. The same
        # candidates `compose_line` walked to propose this composition, and drawn from the
        # same builders, so the recheck cannot refuse what the proposal itself offered
        # (the own location's cap in particular is `use_candidates_for`' own).
        #
        # S7: every location here is drawn through `capacity_left`, the running ledger
        # shared across every line of this confirmation - not read live per line - so a
        # second line asking the same pool or the same group-take sibling sees what the
        # first line of this confirmation already took, not the live figure again.
        #
        # LADDER V6: seeded from the UNIT's fact, which is the quantity the ladder walked
        # these rungs with. The ledger is shared across the whole confirmation, so the unit's
        # members draw the same seeded balance down between them in payload order.
        pools = self._pool_chain(unit.fact, pool_free_left=None)
        capacity: Dict[str, Decimal] = {}
        location_ids: Dict[str, str] = {}
        for location, live_qty in pool_reserve_capacity(
            pools=pools,
            pools_net=unit.fact.pools_net,
        ):
            source = self._warehouse_by_code(location)
            if source is None:
                continue
            location_ids[location] = str(source.id)
            capacity[location] = capacity_left.capacity(
                fact.product_id, str(source.id), live_qty,
                own=(location == unit.fact.pool_code),
            )
        # STEP 1's OWN half, read TWICE and reconciled into one pile.
        #
        # Ladder v4's `_group_take_candidates` is the UNDATED whole-pile reading: what the
        # group's own net leaves, per bin. It is what a line the dated walk cannot place -
        # a TBA order, an undated one, a line outside the reserve window - is still judged
        # against, so it stays.
        #
        # Ladder v7.1's `use_candidates_for` is the DATE-AWARE one (R24, AC-S3-1b): what the
        # assignment gave this unit BY ITS OWN DATE, which is the number the proposal was
        # composed from. Seeding the recheck from the undated number alone refused the
        # board's own answer on exactly the book v7.1 exists for - where later-dated demand
        # dominates, plain Available is negative while the pile IS free by an early line's
        # date. SO381895's `Confirm (76)` came back "34 lines cannot be confirmed. Nothing
        # was written", every refusal "<bin> has nothing free for this line now" in front of
        # a row the board itself was proposing as `Use own location` off that bin
        # (30 August 2026).
        #
        # The pile is whichever of the two is LARGER, never their sum (`_CapacityLedger`),
        # and the dated slices of two units are summed against it because the assignment
        # gives each unit a different part of one bin.
        #
        # The FLOOR half only, in both readings. A `water` candidate is incoming supply,
        # judged against `fact.timely_qty` above; seeding Reserve capacity with it would let
        # a hold be written against goods that are not on a floor for anybody to pick.
        own_use, other_use, _own_offer, _short = self.use_candidates_for(unit.fact)
        undated: Dict[str, Decimal] = {}
        for candidate in self._group_take_candidates(unit.fact):
            if candidate.get("water"):
                continue
            undated[candidate["location"]] = undated.get(
                candidate["location"], _ZERO
            ) + _dec(candidate["qty"])
        dated: Dict[str, Decimal] = {}
        for candidate in own_use:
            if candidate.get("water"):
                continue
            dated[candidate["location"]] = dated.get(candidate["location"], _ZERO) + _dec(
                candidate["qty"]
            )
        for location in list(undated) + [code for code in dated if code not in undated]:
            source = self._warehouse_by_code(location)
            if source is None:
                continue
            location_ids[location] = str(source.id)
            left = capacity_left.capacity(
                fact.product_id, str(source.id), undated.get(location, _ZERO)
            )
            share = dated.get(location, _ZERO)
            if share > _ZERO and not unit.own_seeded:
                # Once per UNIT, on whichever of its member lines is checked first: the
                # slice belongs to the unit and its members split it between them, so
                # claiming it per line would sell one slice to each of them.
                left = capacity_left.offer(fact.product_id, str(source.id), share)
            capacity[location] = capacity.get(location, _ZERO) + left
        unit.own_seeded = True
        # LADDER V7.1 step 1's SECOND half (R5, AC-S3-1): the other project groups' free
        # piles. Seeded from the same builder that offered them, for the same reason the own
        # half is - a recheck that does not know about a step the proposal walked refuses
        # the engine's own answer, which is what "ZZTDC1-IR has nothing free for this line"
        # was in front of a composition the ladder had just written.
        #
        # R-M's cap is a statement about the LENDING GROUP, so it is drawn through
        # `capacity_left` under the group's own key as well as the bin's: a unit's offer is
        # already capped by the group's spare book, but two units of one confirmation whose
        # dates bring DIFFERENT bins of that group into view each cleared the whole of it,
        # and the confirmation wrote both. The group's budget is now seeded once, on
        # whichever line reads it first, and every Reserve taken at one of its bins draws it
        # down - so the second line is refused in the same wording an exhausted bin gets.
        books = self._group_book_positions(fact.product_id)
        for candidate in other_use:
            if candidate.get("water"):
                continue
            location = candidate["location"]
            source = self._warehouse_by_code(location)
            if source is None:
                continue
            offer = _dec(candidate["qty"])
            lender = candidate.get("group")
            if lender:
                offer = min(
                    offer,
                    capacity_left.capacity(
                        fact.product_id,
                        _group_budget_key(str(lender)),
                        max(books.get(str(lender), _ZERO), _ZERO),
                    ),
                )
            location_ids[location] = str(source.id)
            capacity[location] = capacity.get(location, _ZERO) + capacity_left.capacity(
                fact.product_id, str(source.id), offer
            )
        # AC-S3-10: the own-arrival credit (R7) is a rung the composer walks OUTSIDE
        # `use_candidates_for` (`walk`'s own `own_arrival_candidates`, drawn ahead of the
        # ordinary group-take pile), so a recheck seeded only from `own_use`/`other_use`
        # never hears about it and refuses the very Reserve the board proposed - "ZZT-OWN
        # has nothing free for this line now" in front of a composition the ladder credited
        # for exactly this reason. Read with the LINE's own fact, never the unit's: the
        # credit is this line's own purchase order, not something its unit siblings share.
        # `own_arrival_left` is product -> location -> what is left, the same shape
        # `compose_lines` keeps, scoped to this one confirm call so a second line of the
        # same payload cannot be offered units an earlier line already drew off the same
        # bin.
        #
        # AC-S3-11 (security review, 21 Sep 2026): the credit is stated THROUGH
        # `capacity_left`, the ledger every other rung's capacity draws through, and never
        # added straight onto the local `capacity` dict beside it. It is a second READING
        # of one bin - "N of this floor landed for this line" - not a second pile, so the
        # pile becomes whichever statement about it is larger, never their sum, and a bin
        # another line of the same confirmation has already emptied grants the credit
        # nothing at all.
        #
        # S1 (second review round): `offer` is the WRONG verb for this - it folds the
        # credit into `_claimed`, the running sum a later unit's own DATED slice at this
        # same bin is added to, so a credit that granted this line 0 (the pile's stated
        # basis already covered it) still inflates `_claimed` by its own amount, letting
        # that later slice cross `_basis` by units nothing physical backs. `state_at_least`
        # states the pile is at least `credit_qty` without touching `_claimed`.
        #
        # B1 (round 3): the credit is a SECOND READING of the same bin - "this line's own
        # PO put at least `credit_qty` of it there" - never a second pile added to whatever
        # the ordinary reading already stated. `state_at_least(..., credit_qty)` intersects
        # the two readings (`max(before, credit_qty)`, `state_at_least`'s own semantics);
        # calling it with `before + credit_qty` summed them instead, so a competing earlier
        # demand that had already netted the ordinary reading down still let the credited
        # line's Reserve clear the bin's full, un-netted amount.
        # AC-S3-15: hoisted above the `if` below (rather than declared inside it) so
        # both survive to the Buy-over-credit check after the Reserve loop, which needs
        # the SAME live credit reading this block computes for the capacity statement,
        # not a second, possibly-inconsistent read.
        credit_qty = _ZERO
        credit_po: Optional[str] = None
        credit_said: Optional[str] = None
        # AC-S3-16: same verdict as the composer - `walk()` builds no own-arrival
        # candidate at all for a line outside the reserve window, so the recheck must
        # not credit one either. A line due beyond the window is never credited,
        # composed or confirmed.
        # `outside_reserve_window` below is called with no `as_of`, so it defaults to
        # `date.today()` - per `ConfirmSupplyBody.as_of` ("live stock, the queue and
        # every refusal are judged against now"), this window verdict is judged against
        # today, never the body's `as_of`.
        if (
            own_arrival_left is not None
            and fact.own_code
            and not self.outside_reserve_window(fact)
        ):
            ledger = (
                own_arrival_left.setdefault(fact.product_id, {})
                if fact.product_id
                else None
            )
            # S-b: `own_arrival_credit_for` (not the size-then-charge split `walk()` uses)
            # charges the ledger with the full THEORETICAL credit immediately, which can
            # be more than what this confirm's own Reserve ends up posting. Conservative,
            # not a bug: it only ever makes a LATER line's own credit smaller (refuses
            # rather than over-grants), and a confirm-time recheck has no "drawn" figure
            # to defer the charge to the way `walk_line`'s candidate draw does.
            credit_qty, credit_po, credit_said = self.own_arrival_credit_said(
                fact, own_arrival_left=ledger
            )
            if credit_qty > _ZERO:
                source = self._warehouse_by_code(fact.own_code)
                if source is not None:
                    location_ids[fact.own_code] = str(source.id)
                    before = capacity_left.capacity(
                        fact.product_id, str(source.id), _ZERO
                    )
                    after = capacity_left.state_at_least(
                        fact.product_id, str(source.id), credit_qty
                    )
                    capacity[fact.own_code] = capacity.get(
                        fact.own_code, _ZERO
                    ) + max(after - before, _ZERO)
        reserve_locations = self._reserve_ladder_locations(fact)
        by_id = {str(w.id): code for code, w in reserve_locations.items()}
        allowed = ", ".join(sorted(reserve_locations))
        for item in entry.reserve or []:
            warehouse = by_id.get(str(item.warehouse_id))
            qty = _dec(item.qty)
            # What this order's own active revision already holds here - read BEFORE the
            # location gate, because a location can leave the allowed set after a decision
            # was taken at it (an admin flags the bin out of fulfilment planning, R17) and a
            # re-send of the unchanged component is not a new ask about it. Same reasoning
            # the capacity exemption below already carries, applied one step earlier: the
            # snapshot only exists because a previous confirm accepted that location.
            carried = self._carried_component_qty(
                carried_holds, str(line.id), RESERVE, str(item.warehouse_id)
            )
            if warehouse is None:
                if qty <= carried:
                    continue
                # A location that is neither this line's own nor its pool. Name what was
                # asked for and what is allowed, by CODE - the old message named neither, so
                # a planner reading it could not tell whether the location, the quantity or
                # the whole line was the problem.
                posted = self._warehouse_row(str(item.warehouse_id))
                posted_code = posted.warehouse_code if posted else "another location"
                if not reserve_locations:
                    # This line belongs to no ownership group and has no pool configured,
                    # so there is no location it may reserve from at all. Say that plainly
                    # instead of naming "no location" as the allowed set, which read like
                    # the line was broken rather than simply unpooled and ungrouped.
                    refuse(
                        invalid,
                        f"Reserve was asked for from {posted_code}, and this line has no "
                        "ownership group and no pool configured, so it can reserve from "
                        "nowhere. Buy the quantity, or borrow it from that location on "
                        "the order's own sheet.",
                    )
                else:
                    refuse(
                        invalid,
                        f"Reserve was asked for from {posted_code}, and this line "
                        f"reserves only from {allowed} - its own ownership group and its "
                        "pool(s). Buy the quantity instead, or borrow it from that "
                        "location on the order's own sheet, which records who it came "
                        "from and why.",
                    )
                continue
            # What this order's own active revision already holds here, resubmitted:
            # not a new ask, so it is exempt from the recheck entirely - `capacity` is
            # computed from a ZERO baseline for this line (`_facts_for`'s un-netting), so
            # an increase still has to clear the SAME capacity a first-time ask of that
            # total would (PLAN-so-book-diff-replanning.md section 10, defect A). Only the
            # exemption for an unchanged-or-smaller ask is new; the arithmetic for a real
            # increase is untouched, so a genuinely competing sibling hold (another line's
            # own Reserve, never excluded) still refuses it exactly as it always has
            # (`test_a_hold_the_same_order_carries_forward_is_netted_by_the_confirm_as_
            # the_board_nets_it`). `carried` is read above the location gate.
            ask = qty - carried if qty > carried else _ZERO
            if ask <= _ZERO:
                continue
            # TEMPORARY LIFT (the captain, 8 Sep 2026): an ownership group's net can be
            # oversold on paper (SO419417 line 9, BT012-CR at BRW-BB - on hand 1035, owed
            # 6213, group net -5166) while the FLOOR at the line's own bin still has stock
            # a planner can hand a customer today. The group-net-seeded `capacity` above
            # reads every own-group bin as if it were already spoken for by the whole
            # group's backlog, so a hand-composed Reserve at the line's own location could
            # never be written even when the units are sitting there. With a stated
            # `amend_reason` - the planner's own account of why this differs from the
            # proposal - a Reserve at THIS LINE'S OWN GROUP's bins (its fulfilment
            # location, or another bin carrying the same ownership group) is no longer
            # capped by the group net; `capacity_left`'s "own" business is left alone for
            # a pool. Still bounded: R14 (`_check_reserve_against_on_hand`, called on
            # every checked line independent of this block) refuses anything past on hand
            # less other lines' holds, the location gate above still limits this line to
            # its own group's bins and the pools, and the ledger below still draws the
            # take down so a later line of the same payload sees it spent. Pool
            # locations, other groups' bins, borrow and Buy are unchanged.
            #
            # REVISIT when the BB group's book is cleaned up or a queue-jumping complaint
            # arrives, whichever first (PLAN-scm-cs-planning-uat.md).
            own_group_bin = warehouse == fact.own_code or (
                sales_agent_service.group_of_warehouse_code(warehouse) == fact.group_code
                and fact.group_code is not None
            )
            if own_group_bin and (getattr(entry, "amend_reason", None) or "").strip():
                capacity[warehouse] = max(capacity.get(warehouse, _ZERO), qty)
            if warehouse not in capacity:
                # The location IS this line's own or its pool; it simply has nothing left for
                # this line. `pool_reserve_capacity` omits a location contributing zero, so
                # this used to fall through to the message above and report a location error
                # for a quantity problem - which is what the planner saw as "Reserve may only
                # come from this line's own location" printed about their own location.
                refuse(
                    stale,
                    f"{warehouse} has nothing free for this line now, so none of the "
                    f"{qty_text(ask)} asked for can be reserved from it. Buy that quantity "
                    "instead, or borrow it on the order's own sheet.",
                )
                continue
            if qty > capacity[warehouse]:
                # Named for the INCREASE, not the whole resubmitted quantity: `capacity`
                # never had the carried part subtracted from it (it was excluded, not
                # credited), so what remains for the increase alone is `capacity` less
                # what this line already carries.
                room = capacity[warehouse] - carried
                refuse(
                    stale,
                    f"{warehouse} now has {qty_text(room if room > _ZERO else _ZERO)} free "
                    f"for this line, and {qty_text(ask)} was asked for.",
                )
                continue
            capacity[warehouse] -= qty
            warehouse_id = location_ids.get(warehouse)
            if warehouse_id:
                capacity_left.take(fact.product_id, warehouse_id, qty)
            lender = sales_agent_service.group_of_warehouse_code(warehouse)
            if lender and lender != fact.group_code:
                # R-M: and off the LENDING GROUP's budget, so the next line of this
                # confirmation cannot spend it again at another of that group's bins.
                # Seeded here as well as above, because a Reserve may be posted at a bin
                # no candidate list offered (a location that left the ladder since the
                # decision was taken) and its budget is a fact either way.
                budget_key = _group_budget_key(lender)
                capacity_left.capacity(
                    fact.product_id, budget_key, max(books.get(lender, _ZERO), _ZERO)
                )
                capacity_left.take(fact.product_id, budget_key, qty)

        # AC-S3-15 (round-4 fix round, browser-pass finding): R7's Buy-over-credit
        # refusal was wired only into `set_row_decision`'s amend path
        # (`_refuse_buy_over_own_arrival`, `planning_change_service.py`, reading the
        # FROZEN proposal's own `sources`) - the ordinary board Confirm never read this
        # line's own-arrival credit before letting a Buy stand for it. `credit_qty` /
        # `credit_po` are the SAME live reading already computed above for the Reserve
        # capacity statement; only the part the posted Reserve at the CREDITED bin
        # (`fact.warehouse`, `fact.own_code`'s own location) leaves uncovered is
        # refused - a Buy beside a credit the Reserve already fully covers (the
        # control) is untouched, the same "only the part that would drop Reserve below
        # what is credited" rule `_refuse_buy_over_own_arrival` states for its own seam.
        #
        # Raised directly, the way `ReserveOverHand` above is - not folded into the
        # `invalid`/`stale` buckets, whose shared "N line(s) cannot be confirmed"
        # sentence would drop the credited quantity and the document this message names -
        # and still `SupplyLinesRefused`, so `failing_lines` pins the same line the
        # sheet marks for every other refusal. "Nothing was written" holds because this
        # raises before `_write_decision` is ever reached (a draft save never calls
        # `_check_line` at all, so it stays lenient as designed).
        # `credit_qty > _ZERO` already implies `fact.warehouse is not None`
        # (`fact.own_code`, gating `credit_qty` above, is
        # `fact.warehouse.warehouse_code if fact.warehouse else None`).
        if credit_qty > _ZERO and buy > _ZERO:
            reserved_at_credit_bin = sum(
                (
                    _dec(item.qty)
                    for item in entry.reserve or []
                    if str(item.warehouse_id) == str(fact.warehouse.id)
                ),
                _ZERO,
            )
            uncovered = credit_qty - reserved_at_credit_bin
            if uncovered > _ZERO:
                # #1362 (owner, 29 Sep 2026): "this good is on hand, and is covering the
                # line, but, from fulfilment planning, is kind of requesting it to be
                # delayed while the link is intact, then only purchasing will do the
                # adjustment in the linkage". Planning RECORDS the intent: the Buy is
                # confirmed as decided, never refused and never swapped for the landed
                # goods, and the PO/SPO link stays as it is. What landed is said on the
                # Buy's own order inquiry row (`_write_decision`), which purchasing must
                # acknowledge anyway, and in the confirm result. It used to refuse the
                # whole order ("... nothing to buy for it", AC-S3-15).
                # #1362 item 4: a sibling's spare says whose purchase it was.
                landed = credit_said or (
                    f"{qty_text(credit_qty)} landed for this line{landed_on(credit_po)}"
                )
                notices = getattr(self, "_landed_buy_notices", None)
                if notices is not None:
                    notices[str(line.id)] = {
                        **{k: v for k, v in subject.items() if k != "project_line_id"},
                        "landed": landed,
                        "reason": (
                            f"Buy {qty_text(buy)} confirmed as decided; {landed} "
                            "stay linked to it, for purchasing to adjust"
                        ),
                    }

        for item in entry.borrow or []:
            self._check_borrow(item, fact, borrow_left, refuse, stale, invalid, carried_holds)

        total = timely + reserve_total + borrow_total + buy
        if total != fact.open_qty:
            refuse(
                invalid,
                f"The components add up to {qty_text(total)} and the line is open for "
                f"{qty_text(fact.open_qty)}.",
            )
            return

        # The whole-line rule (AC-L5, the captain 25 August 2026): "a line is either wholly
        # covered from stock (own group, pools, borrow, incoming in any mix) or wholly Buy".
        # The engine has refused to PROPOSE a mix since ladder v2's rule 6, and that still
        # governs a first confirmation and the SO supply sheet.
        #
        # LIFTED for a manual amendment (the captain, 8 Sep 2026): the engine never proposes
        # a mix, so any mix reaching confirm is a person's own composition, not a stale echo
        # of the proposal. `entry.amend_reason` is that person's statement of why this line
        # differs from the proposal (the schema comment: "Absent when they took the proposal
        # as it stood"), so a reason present is what tells purchasing the split is deliberate,
        # and is the gate here. Stated after the balance check, so a line that does not add
        # up is told THAT rather than this.
        from_stock = timely + reserve_total + borrow_total
        if (
            from_stock > _ZERO
            and buy > _ZERO
            and not (getattr(entry, "amend_reason", None) or "").strip()
            and not self._is_pool_share_split(fact, entry, from_stock)
        ):
            refuse(
                invalid,
                "A line is either met wholly from stock or wholly bought unless a reason "
                f"is given. This one mixes {qty_text(from_stock)} from stock with a Buy of "
                f"{qty_text(buy)}: take the whole {qty_text(fact.open_qty)} from stock, buy "
                f"the whole {qty_text(fact.open_qty)}, or say why this differs from the "
                "proposal.",
            )

    def _is_pool_share_split(
        self, fact: _LineFacts, entry: Any, from_stock: Decimal
    ) -> bool:
        """LADDER V8 (R-C): the ONE mix the whole-line rule allows.

        The site pool keeps a share back for dealers and lends the rest, so a line bigger
        than that share is legitimately "BRW 450 + Buy 200" - one draw off the pool, and the
        remainder bought. Everything else the rule refused it still refuses: half a line off
        the ownership group beside a Buy is the composition purchasing cannot act on, and it
        is what this check exists for.

        Four conditions, all of them:

        * every from-stock unit is a RESERVE at a SITE POOL of this line's own chain (a
          borrow or a timely SPO beside a Buy is still a mix);
        * the line is INSIDE `immediate_window_days` - beyond it the pool is whole or
          nothing (R-B), so a part share there is not a composition the engine would ever
          make (review round 1, S8);
        * each pool's quantity is inside THAT POOL's own allowance, AND the pools' total is
          inside the ONE five-pool net (R-D, review round 2 blocker 2) - per-pool alone let
          two pools of 1,000 netting 100 between them be reserved 100 each, which is the
          over-draw the engine itself was making;
        * and the allowance is read off `pool_chain_for`, the SAME source `compose_lines`
          seeds its ledger from - never `fact.pool_available`, which is 0 for a bare site
          bin like `BRW-IB` and refused the composition the board had just proposed (review
          round 1, B2).

        DELIBERATELY WIDER THAN THE WALK (captain, 2 Sep): a Reserve at ANY site pool of the
        chain is admitted, not only the asking bin's own, because S3 lets a planner ADD a
        pool location to Reserve by hand (R-G) and the product must be able to confirm what
        it invites. The ENGINE's own R-L step stays whole-or-nothing, so it never composes
        another site's part share beside a Buy; this rule is about what a person may
        compose, and the allowance and the net are what bound them.
        """
        chain = {
            str(entry_pool.get("location") or ""): entry_pool
            for entry_pool in self.pool_chain_for(fact)
            if entry_pool.get("location")
        }
        if not chain or entry.borrow:
            return False
        settings = self._fulfilment_settings()
        window = max(int(settings.get("immediate_window_days") or 0), 0)
        as_of = self._walk_as_of or date.today()
        if (
            fact.required_date is not None
            and fact.required_date > as_of + timedelta(days=window)
        ):
            return False
        per_pool: Dict[str, Decimal] = {}
        for item in entry.reserve or []:
            row = self._warehouse_row(str(item.warehouse_id))
            code = row.warehouse_code if row is not None else None
            if not code or code not in chain:
                return False
            per_pool[code] = per_pool.get(code, _ZERO) + _dec(item.qty)
        pooled = sum(per_pool.values(), _ZERO)
        if pooled <= _ZERO or pooled != from_stock:
            return False
        pct = settings.get("pool_share_pct")
        return pooled <= max(_dec(fact.pools_net), _ZERO) and all(
            qty <= available_for_project(chain[code].get("available"), fact.pools_net, pct)
            for code, qty in per_pool.items()
        )

    def _check_reserve_against_on_hand(
        self,
        checked: Sequence[Tuple[ProjectSalesOrderLine, Any, _LineFacts]],
        *,
        named: set,
    ) -> None:
        """No Reserve may exceed what is ON HAND at that location, less what OTHER lines
        have already confirmed there (R14, captain 27 August 2026).

        The ladder already caps what it PROPOSES, but the board lets a planner compose an
        amendment by hand, and a hand-typed 15 against 10 on hand came back as "BRW-AM now
        has 9 free for this line, and 15 was asked for" - true, and it does not say who has
        the other one. This refusal names them: "BRW-AM: 10 on hand, 1 already reserved by
        SO383850, you asked 15", with the holders on the body so the row can be pinned.

        OTHER lines, exactly: the lines this confirmation names are excluded (a line
        re-confirming its own hold is not competing with itself), and the lines EARLIER in
        this same payload count as holders the moment they take, so two lines of one press
        cannot each be sold the same pile.

        Reserve only. A Borrow names a donor line and is checked against that line's own
        commitment (`_check_borrow`); incoming supply is not on a floor for anybody to pick.

        Silent when NOBODY else holds at that location: the free-stock recheck already
        refuses more than a location has, and it says what to do instead. This rule is here
        to NAME the line holding the rest, so it speaks only when there is one to name.
        """
        pairs = {
            (fact.product_id, str(item.warehouse_id))
            for _line, entry, fact in checked
            for item in (entry.reserve or [])
            if fact.product_id and getattr(item, "warehouse_id", None)
        }
        if not pairs:
            return
        product_ids = sorted({product_id for product_id, _ in pairs})
        levels = self.stock_levels_by_location(product_ids)
        holds = self._holds_by_holder(product_ids, exclude_line_ids=sorted(named))
        mine: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
        for line, entry, fact in checked:
            for item in entry.reserve or []:
                qty = _dec(item.qty)
                warehouse_id = str(getattr(item, "warehouse_id", "") or "")
                if qty <= _ZERO or not warehouse_id or not fact.product_id:
                    continue
                key = (fact.product_id, warehouse_id)
                on_hand = levels.get(key, (_ZERO, _ZERO))[0]
                holders = list(holds.get(key, ())) + list(mine.get(key, ()))
                held = sum((_dec(holder["qty"]) for holder in holders), _ZERO)
                # NOBODY ELSE HOLDING HERE is not this rule's case: the free-stock recheck
                # below already refuses more than a location has, and it says what to do
                # about it ("Buy that quantity instead, or borrow it on the order's own
                # sheet"). This rule exists to name the line that has the rest, so it only
                # speaks when there IS one.
                if holders and qty > on_hand - held:
                    warehouse = self._warehouse_row(warehouse_id)
                    code = warehouse.warehouse_code if warehouse else "That location"
                    asking = self._so_number_of(fact)
                    names = ", ".join(
                        dict.fromkeys(
                            self._holder_name(holder, asking) for holder in holders
                        )
                    )
                    message = (
                        f"{code}: {qty_text(on_hand)} on hand, {qty_text(held)} already "
                        f"reserved by {names}, you asked {qty_text(qty)}"
                    )
                    raise ReserveOverHand(
                        message=message,
                        failing_lines=[
                            {
                                "line_no": line.line_no,
                                "so_line_no": (
                                    fact.core.line_no if fact.core is not None else None
                                ),
                                "item_code": fact.item_code,
                                "reason": message,
                                "project_line_id": str(line.id),
                            }
                        ],
                        conflict={
                            "line": line.line_no,
                            "warehouse_code": warehouse.warehouse_code
                            if warehouse
                            else None,
                            "on_hand": qty_text(on_hand),
                            "held_by": holders,
                            "asked": qty_text(qty),
                        },
                    )
                mine[key].append(
                    {
                        "so_number": self._so_number_of(fact),
                        "line_no": line.line_no,
                        "qty": qty_text(qty),
                    }
                )

    @staticmethod
    def _holder_name(holder: Dict[str, Any], asking: Optional[str]) -> str:
        """How the refusal names the line holding the stock this one asked for.

        Another order is named by its document number. A line of the SAME order is named as
        a line, because handing a planner their own SO number back reads as "some other copy
        of this order is holding it" and sends them looking for a document they are already
        on - when the competitor is a row on the screen in front of them.
        """
        number = holder.get("so_number")
        if asking and number == asking:
            line_no = holder.get("line_no")
            return f"line {line_no} of this order" if line_no is not None else "this order"
        return number or "another order"

    @staticmethod
    def _so_number_of(fact: _LineFacts) -> Optional[str]:
        """The document number a person knows this line by, off its CORE line's order.

        `None` on a line with no reconciled core line, which the message renders as "another
        order" rather than inventing a number - the confirm refuses such a line anyway
        (`_check_line`), so this is the belt and not the braces.
        """
        core = getattr(fact, "core", None)
        order = getattr(core, "sales_order", None) if core is not None else None
        return getattr(order, "so_number", None)

    def _holds_by_holder(
        self, product_ids: Sequence[str], *, exclude_line_ids: Sequence[str]
    ) -> Dict[Tuple[str, str], List[Dict[str, Any]]]:
        """Confirmed holds per `(product, warehouse)`, WITH the line holding each one.

        The same predicate `_hold_rows` nets stock by, asked for different columns, so the
        refusal cannot name a hold the arithmetic does not count (or miss one it does).
        Summed per (order, line), because one line can hold at a location on several
        allocation rows and the message says "1 already reserved by SO383850", not "1 and 0".
        """
        ids = [pid for pid in product_ids if pid]
        if not ids:
            return {}
        rows = self._hold_query(
            ids,
            exclude_line_ids=list(exclude_line_ids) or None,
            entities=(
                _hold_product,
                SOLineAllocation.warehouse_id,
                SalesOrder.so_number,
                ProjectSalesOrderLine.line_no,
                held_qty_expr(),
            ),
        ).all()
        totals: Dict[Tuple[str, str], Dict[Tuple[Any, Any], Decimal]] = defaultdict(dict)
        for product_id, warehouse_id, so_number, line_no, qty in rows:
            key = (str(product_id), str(warehouse_id))
            holder = (so_number, int(line_no) if line_no is not None else None)
            totals[key][holder] = totals[key].get(holder, _ZERO) + _dec(qty)
        return {
            key: [
                {"so_number": so_number, "line_no": line_no, "qty": qty_text(qty)}
                for (so_number, line_no), qty in holders.items()
                if qty > _ZERO
            ]
            for key, holders in totals.items()
        }

    def _warehouse_of(self, fact: _LineFacts, warehouse_id: str) -> Optional[str]:
        if fact.warehouse and str(fact.warehouse.id) == warehouse_id:
            return fact.own_code
        if fact.pool and str(fact.pool.id) == warehouse_id:
            return fact.pool_code
        return None

    def _reserve_location_ids(self, fact: _LineFacts) -> set:
        """The warehouses Reserve MAY draw this line from, by id.

        Structural, not "wherever there happens to be stock": the fulfilment location
        (a group rung source again under ladder v3, section 1b rung 2) and EVERY active
        site pool - rung 3 reaches every pool, not only this line's own site. None of
        these is ever offered as a plain free-stock Borrow donor: the pool rung already
        reaches them automatically, and offering them again as `other_location` would
        double the same stock up as two different decisions.
        """
        ids = set()
        if fact.warehouse:
            ids.add(str(fact.warehouse.id))
        if fact.pool:
            ids.add(str(fact.pool.id))
        ids.update(self._site_pool_warehouses().keys())
        return ids

    def _reserve_ladder_locations(self, fact: _LineFacts) -> Dict[str, Warehouse]:
        """Every location Reserve may draw THIS line from, by CODE.

        Ladder v3's set was this line's own ownership group (section 1b rung 2, its own
        location included) plus every active site pool (rung 3). LADDER V7.1 adds the OTHER
        PROJECT GROUPS' flagged bins, because step 1's second half composes a Reserve there
        (R5, AC-S3-1: "given the own group cannot but another project group's FREE pile can,
        the composition is `reserve` from that group"). Without them the engine's own
        proposal could not be confirmed - `_check_line` refused it with "this line reserves
        only from its own ownership group and its pool(s)", which is a sentence about ladder
        v3 in front of a ladder v7.1 answer.

        A bin with NO ownership group is still out (the pools are in on their own rung), the
        same rule step 1 itself keeps: the group is what "our locations" means.
        """
        out: Dict[str, Warehouse] = {
            pool.warehouse_code: pool for pool in self._site_pool_warehouses().values()
        }
        for row in self._planning_warehouses().values():
            code = row.warehouse_code
            if code and sales_agent_service.group_of_warehouse_code(code):
                out[code] = row
        out.update(self._group_sibling_warehouses(fact))
        return out

    def _check_borrow(
        self,
        item: Any,
        fact: _LineFacts,
        borrow_left: "_BorrowLedger",
        refuse,
        stale: List[Dict[str, Any]],
        invalid: List[Dict[str, Any]],
        carried_holds: Dict[str, Dict[str, Any]],
    ) -> None:
        qty = _dec(item.qty)
        if not (item.reason or "").strip():
            refuse(
                invalid,
                "Borrowing takes a reason. Say why this line is taking somebody else's "
                "stock.",
            )
            return
        warehouse = self._warehouse_row(str(item.warehouse_id))
        if warehouse is None:
            refuse(invalid, "That location no longer exists.")
            return

        supply_key = str(getattr(item, "supply_key", "") or "")
        if supply_key:
            # LADDER V7.1 STEP 3: this is a DOCUMENT, not stock on a floor, so neither the
            # free-stock read below nor the donor line's committed quantity is the thing to
            # judge it against. What it is judged against is the document's own open
            # balance, net of the placements already on it.
            self._check_supply_borrow(supply_key, item, fact, refuse, stale, invalid)
            return

        donor_core_line_id = getattr(item, "donor_core_line_id", None)
        if item.source == ALLOC_SOURCE_OTHER_LOCATION and donor_core_line_id:
            self._check_group_borrow(
                item, fact, warehouse, donor_core_line_id, refuse, stale, invalid,
                carried_holds,
            )
            return

        if item.source == ALLOC_SOURCE_OTHER_LOCATION:
            if fact.warehouse and str(item.warehouse_id) == str(fact.warehouse.id):
                # Ladder v3: this line's own location is never a BORROW source - free
                # stock there has no donor to ask, its demand IS this line, and rung 2
                # reaches it as a Reserve already. The only thing left to borrow here is
                # ANOTHER SO's committed quantity, which names that line by id.
                refuse(
                    invalid,
                    f"{warehouse.warehouse_code} is this line's own location. Free stock "
                    "there cannot be borrowed - name the sales-order line it should come "
                    "from, on the order's own sheet.",
                )
                return
            if str(item.warehouse_id) in self._reserve_location_ids(fact):
                refuse(
                    invalid,
                    f"{warehouse.warehouse_code} is inside this line's Reserve pool. "
                    "Reserve it rather than borrowing it.",
                )
                return
            # What this order's own active revision already holds here, resubmitted:
            # not a new ask - same "zero baseline" reasoning as the Reserve check above
            # (PLAN-so-book-diff-replanning.md section 10, defect A).
            carried = self._carried_component_qty(
                carried_holds, str(fact.line.id), BORROW, str(warehouse.id)
            )
            ask = qty - carried if qty > carried else _ZERO
            if ask <= _ZERO:
                return
            available = borrow_left.free(
                fact.product_id, str(warehouse.id), self._free_at
            )
            if qty > available:
                room = available - carried
                refuse(
                    stale,
                    f"{warehouse.warehouse_code} has {qty_text(room if room > _ZERO else _ZERO)} "
                    f"free, and {qty_text(ask)} was asked for.",
                )
                return
            borrow_left.take_free(fact.product_id, str(warehouse.id), qty)
            return

        if not item.donor_project_id:
            refuse(invalid, "Name the project this stock is being borrowed from.")
            return
        if self._project_id_of(fact.line) is None:
            # An order adopted from the AutoCount book has no project (section 4), and a
            # cross-project Borrow is a claim from one project to another: there is no
            # side to record. Refused by name here rather than exploding on
            # `AllocationClaim.from_project_id`, which is NOT NULL. The sheet does not
            # offer this Borrow on an adopted order either (`_borrow_candidates`); free
            # stock at another location is still available as an ordinary Borrow.
            refuse(
                invalid,
                "This sales order is planned straight from the AutoCount book and belongs "
                "to no project, so it cannot borrow another project's stock. Borrow the "
                "free stock at that location instead.",
            )
            return
        donor = (
            self.db.query(Project).filter(Project.id == item.donor_project_id).first()
        )
        if donor is None:
            refuse(invalid, "The project holding that stock no longer exists.")
            return
        # The donor's own hold first, then whatever is free at that location: both are
        # stock that exists, and neither may be handed to two lines of one confirmation.
        held = borrow_left.held(
            fact.product_id, str(warehouse.id), str(donor.id), self._held_at
        )
        free = borrow_left.free(fact.product_id, str(warehouse.id), self._free_at)
        # What this order's own active revision already holds here FROM THIS SAME donor,
        # resubmitted: not a new ask (PLAN-so-book-diff-replanning.md section 10, defect A).
        carried = self._carried_component_qty(
            carried_holds,
            str(fact.line.id),
            BORROW,
            str(warehouse.id),
            donor_project_id=str(donor.id),
        )
        ask = qty - carried if qty > carried else _ZERO
        if ask <= _ZERO:
            return
        if qty > held + free:
            room = held + free - carried
            refuse(
                stale,
                f"{donor.project_code} has {qty_text(room if room > _ZERO else _ZERO)} at "
                f"{warehouse.warehouse_code}, and {qty_text(ask)} was asked for.",
            )
            return
        from_hold = min(qty, held)
        borrow_left.take_held(fact.product_id, str(warehouse.id), str(donor.id), from_hold)
        borrow_left.take_free(fact.product_id, str(warehouse.id), qty - from_hold)

    def _check_supply_borrow(
        self,
        supply_key: str,
        item: Any,
        fact: _LineFacts,
        refuse,
        stale: List[Dict[str, Any]],
        invalid: List[Dict[str, Any]],
    ) -> None:
        """Step 3, re-read live (AC-C03): is the DOCUMENT still there, and still free of it?

        A document is not stock, so neither test the other borrows make applies: there is no
        floor to read free stock off, and no donor line whose committed quantity bounds it.
        What bounds it is the document's own OPEN BALANCE - what is still to come on it -
        less every placement already written against it, which is exactly the number the
        Confirm is about to write a placement into.

        Two placements are CREDITED BACK, because this Confirm is about to take them down:
        the named DONOR's (PLAN 3.3's middle clause - judging against it would refuse the
        very move being made) and this line's OWN, resubmitted, for the reason every other
        component here has its carried quantity added back. Each holder is credited once
        per document however many components name it.

        Per-confirmation ledger, so two lines of one confirmation asking for the same
        document draw it down between them rather than each being told it is whole.
        """
        qty = _dec(item.qty)
        if qty <= _ZERO:
            return
        if not (item.reason or "").strip():
            refuse(
                invalid,
                "Borrowing takes a reason. Say why this line is taking somebody else's "
                "stock.",
            )
            return
        named = getattr(item, "supply_document", None) or "That document"
        if supply_key not in self._supply_doc_ledger:
            balance = self._supply_document_balance(supply_key)
            if balance is None:
                refuse(
                    invalid,
                    f"{named} is no longer an open document, so nothing can be taken off "
                    "it. Buy the quantity instead.",
                )
                return
            self._supply_doc_ledger[supply_key] = balance
        holders = [
            ("line", str(fact.line.id) if fact.line is not None else ""),
            ("donor", str(getattr(item, "donor_core_line_id", None) or "")),
        ]
        for kind, holder_id in holders:
            if not holder_id or (supply_key, holder_id) in self._supply_doc_credited:
                continue
            self._supply_doc_credited.add((supply_key, holder_id))
            self._supply_doc_ledger[supply_key] += self._supply_document_held_by(
                supply_key, holder_id, by_core_line=kind == "donor"
            )
        available = self._supply_doc_ledger[supply_key]
        if qty > available:
            refuse(
                stale,
                f"{named} now has {qty_text(max(available, _ZERO))} left, and "
                f"{qty_text(qty)} was asked for.",
            )
            return
        self._supply_doc_ledger[supply_key] = available - qty

    def _supply_document_balance(self, supply_key: str) -> Optional[Decimal]:
        """What is still to come on this document, less every placement already on it.

        `None` when the document is closed, received or gone - which is a refusal and not a
        zero, because "it has nothing left" and "it is not there" send a planner to two
        different places.
        """
        kind, target = parse_supply_key(supply_key)
        if not target:
            return None
        if kind == SA_KIND_SPO:
            row = (
                self.db.query(SPOAllocation)
                .outerjoin(
                    InboundShipment,
                    InboundShipment.id == SPOAllocation.inbound_shipment_id,
                )
                .filter(
                    SPOAllocation.id == target,
                    # The one copy of "this row is still incoming" (`spo_supply`), the same
                    # clauses `_spo_rows` read it with - so the document the ladder offered
                    # and the document the Confirm re-reads cannot come to differ.
                    *spo_supply.open_incoming_clauses(),
                )
                .first()
            )
            if row is None:
                return None
            open_qty = _dec(row.allocated_quantity) - _dec(row.quantity_received)
        elif kind == SA_KIND_PO:
            row = (
                self.db.query(PurchaseOrderLine)
                .join(
                    PurchaseOrder,
                    PurchaseOrder.id == PurchaseOrderLine.purchase_order_id,
                )
                .filter(
                    PurchaseOrderLine.id == target,
                    PurchaseOrderLine.line_status == "open",
                    PurchaseOrder.status.in_(OPEN_PO_STATUSES),
                )
                .first()
            )
            if row is None:
                return None
            open_qty = _dec(row.qty_ordered) - _dec(row.qty_received)
        else:
            return None
        if open_qty <= _ZERO:
            return None
        placed = sum(
            (qty for _line_id, _core_id, qty in self._supply_document_links(supply_key)),
            _ZERO,
        )
        return max(open_qty - placed, _ZERO)

    def _supply_document_links(
        self, supply_key: str
    ) -> List[Tuple[str, str, Decimal]]:
        """Every LIVE placement on this document: `(project line id, core line id, qty)`.

        A CANCELLED inquiry row holds nothing - the same filter every other consumer of
        these links applies - so a withdrawn placement does not go on reserving a document
        nobody is waiting on.
        """
        kind, target = parse_supply_key(supply_key)
        if not target:
            return []
        column = (
            OrderInquiryLink.spo_allocation_id
            if kind == SA_KIND_SPO
            else OrderInquiryLink.po_line_id
        )
        rows = (
            self.db.query(
                ProjectSalesOrderLine.id,
                ProjectSalesOrderLine.core_sales_order_line_id,
                OrderInquiryLink.qty,
            )
            .select_from(OrderInquiryLink)
            .join(OrderInquiryRow, OrderInquiryRow.id == OrderInquiryLink.row_id)
            .outerjoin(
                ProjectSalesOrderLine,
                ProjectSalesOrderLine.id == OrderInquiryRow.so_line_id,
            )
            .filter(column == target, OrderInquiryRow.state != INQUIRY_CANCELLED)
            .all()
        )
        return [
            (str(line_id or ""), str(core_id or ""), _dec(qty))
            for line_id, core_id, qty in rows
        ]

    def _supply_document_held_by(
        self, supply_key: str, holder_id: str, *, by_core_line: bool
    ) -> Decimal:
        """How much of this document one holder's placements are holding, by PROJECT line
        (this order's own, resubmitted) or by CORE line (the donor's, which this Confirm is
        about to take down)."""
        return sum(
            (
                qty
                for line_id, core_id, qty in self._supply_document_links(supply_key)
                if (core_id if by_core_line else line_id) == holder_id
            ),
            _ZERO,
        )

    def _check_group_borrow(
        self,
        item: Any,
        fact: _LineFacts,
        warehouse: Warehouse,
        donor_core_line_id: str,
        refuse,
        stale: List[Dict[str, Any]],
        invalid: List[Dict[str, Any]],
        carried_holds: Dict[str, Dict[str, Any]],
    ) -> None:
        """The group borrow a person picked in Amend (section 1c), re-read live (AC-C03).

        A group borrow takes another sales order's own COMMITTED quantity, not free
        stock - so it is checked against that donor line's live open quantity, never
        against `_free_at`/`_BorrowLedger`. A per-donor-line ledger (`_donor_line_ledger`)
        stops two lines of the SAME confirmation both borrowing the whole of one donor
        line; seeded net of what OTHER confirmed decisions already hold from it (S1,
        `_group_borrow_held_qty`), so a SECOND confirmation cannot do the same.
        """
        qty = _dec(item.qty)
        donor_line = (
            self.db.query(SalesOrderLine, SalesOrder)
            .join(SalesOrder, SalesOrder.id == SalesOrderLine.sales_order_id)
            .filter(SalesOrderLine.id == donor_core_line_id)
            .first()
        )
        if donor_line is None:
            refuse(invalid, "The donor sales-order line no longer exists.")
            return
        line, order = donor_line
        if fact.core is not None and str(line.id) == str(fact.core.id):
            refuse(invalid, "A line cannot borrow from itself.")
            return
        # The CORE `SalesOrderLine` carries no line number of its own (that is the
        # PROJECT mirror's `line_no`); named off the confirm payload's own
        # `donor_line_no`, round-tripped from the proposal that offered this donor,
        # the same way `_borrow_shortfalls` names it in the order-back note.
        donor_line_no = getattr(item, "donor_line_no", None)
        line_text = f" line {donor_line_no}" if donor_line_no is not None else ""
        # S8: the donor line named has to still BE what the borrow claims it is - the
        # same product, still open demand, and inside this line's own ownership group -
        # not merely a row that exists.
        if fact.product_id and str(line.product_id or "") != str(fact.product_id):
            refuse(
                invalid,
                f"{order.so_number or 'That sales order'}{line_text} no longer holds "
                "this product.",
            )
            return
        if str(line.warehouse_id or "") != str(warehouse.id):
            refuse(
                invalid,
                f"{order.so_number or 'That sales order'}{line_text} is no longer at "
                f"{warehouse.warehouse_code}.",
            )
            return
        # LADDER V7.1 (R5): ANY ownership group may donate, so the donor no longer has to
        # sit in the asker's own group - the cap that made another group's stock a favour is
        # gone with the columns behind it. What is still refused is a donor line at a bin
        # that is in NO ownership group at all and is not a site pool: nothing addresses it,
        # nothing nets it, and the order-back would be owed to a place with no book. A site
        # POOL donor is allowed, because step 4b borrows a later POOL order's on hand (R34).
        donor_group = sales_agent_service.group_of_warehouse_code(warehouse.warehouse_code)
        if not donor_group and str(warehouse.id) not in self._site_pool_warehouses():
            refuse(
                invalid,
                f"{warehouse.warehouse_code} is in no ownership group, so it cannot be "
                "borrowed from another sales order's committed quantity.",
            )
            return
        # AC-L6 (section 1c): a donor sharing this line's SALES AGENT is offered at any
        # rank, and one ranked AHEAD of this line is offered only because that agent can
        # authorise moving stock between her own orders. So taking it has to say she did.
        #
        # Judged on the SERVER's own donor list, never on the payload's `same_agent` flag:
        # that flag is a client claim and the rule it gates is a permission. A donor ranked
        # BELOW this line needs nothing extra - that is ordinary group borrow, which the v2
        # ladder took automatically - so demanding a reason for it would make a mandatory
        # field a rubber stamp.
        donor = next(
            (
                d
                for d in self._group_borrow_donors(fact)
                if str(d.get("donor_core_line_id") or "") == str(donor_core_line_id)
            ),
            None,
        )
        if (
            donor is not None
            and donor.get("same_agent")
            and not donor.get("lower_ranked")
            and not _states_an_authorisation(item.reason)
        ):
            refuse(
                invalid,
                f"{order.so_number or 'That sales order'}{line_text} shares this line's "
                "sales agent and is ranked ahead of it, so it can only be borrowed with "
                "that agent's authorisation. Say who authorised it in the reason.",
            )
            return
        still_open = (
            self.db.query(SalesOrderLine.id)
            .join(SalesOrder, SalesOrder.id == SalesOrderLine.sales_order_id)
            .filter(
                SalesOrderLine.id == donor_core_line_id,
                SalesOrder.status == "open",
                is_open_demand(),
                is_transferable_order(),
            )
            .first()
        )
        if still_open is None:
            refuse(
                stale,
                f"{order.so_number or 'That sales order'}{line_text} is no longer open "
                "demand, so it cannot be borrowed from.",
            )
            return
        carried = self._carried_component_qty(
            carried_holds, str(fact.line.id), BORROW, str(warehouse.id)
        )
        ask = qty - carried if qty > carried else _ZERO
        if ask <= _ZERO:
            return
        if donor_core_line_id not in self._donor_line_ledger:
            # S1: net off what any OTHER active decision already holds from this SAME
            # donor line - this confirmation's OWN lines being replaced excluded, since
            # their prior hold is what THIS confirmation is about to replace.
            held_elsewhere = self._group_borrow_held_qty(
                donor_core_line_id, exclude_line_ids=self._replaced_line_ids
            )
            self._donor_line_ledger[donor_core_line_id] = max(
                _dec(line.qty_ordered) - _dec(line.qty_delivered) - held_elsewhere, _ZERO
            )
        available = self._donor_line_ledger[donor_core_line_id]
        if qty > available:
            room = available - carried
            refuse(
                stale,
                f"{order.so_number or 'that sales order'}{line_text} now holds "
                f"{qty_text(room if room > _ZERO else _ZERO)} at {warehouse.warehouse_code}, "
                # The number actually tested, above (`qty`) - not `ask`, which is only
                # the increment over what this order already carried.
                f"and {qty_text(qty)} was asked for.",
            )
            return
        self._donor_line_ledger[donor_core_line_id] = available - qty

    # ------------------------------------------------------------------- writing

    def _proposals_for(
        self,
        lines: Sequence[ProjectSalesOrderLine],
        checked: Sequence[Tuple[ProjectSalesOrderLine, Any, _LineFacts]],
        *,
        facts: Dict[str, _LineFacts],
        covered: Optional[Set[str]] = None,
        as_of: Optional[date] = None,
    ) -> Dict[str, Tuple[Component, ...]]:
        """The engine's composition for each line being confirmed, keyed by mirror line id.

        THE SAME WALK `proposal_for` RUNS, over the whole ORDER and not over the lines the
        payload happens to name. The two shared ledgers and the planning UNIT are both
        properties of the walk, so a walk of one line composes that line as though it stood
        alone: confirming line 32 of a unit of 30 froze "Reserve 20 from the pool" beside it
        while the sheet had shown a Buy, and the snapshot is supposed to record what the
        planner was shown. A line the ladder cannot walk at all (no core line, no open
        quantity, no location) proposes an empty tuple, which is a different answer from an
        absent key.

        `lines` is the order's own lines, in line order, handed over by the caller that
        already read them; `covered` names the lines an active revision still holds and this
        confirmation is neither replacing nor releasing - the carried ones. They are out of
        the walk for the same reason they are out of the board's: a decided line is not
        re-planned, and its claim is already a hold in the facts rather than a place in a
        queue. ONE set, shared with the recheck (`confirm`), or the two disagree about which
        lines were planned together.

        IN LINE ORDER, not payload order, and that is load-bearing: the ledgers are drawn
        down in the order the walk goes, so the same lines posted in two orders would
        otherwise freeze two different proposals for the same board. `lines_of` is line
        order, which is what `proposal_for` walks too.

        `as_of` is the day the planner was deciding on, so the frozen suggestion is the one
        they saw rather than one recomputed against a later calendar. Defaults to today, in
        `compose_line`, which is what every caller sends.
        """
        skip = covered or set()
        composed = self.compose_lines(
            [
                (str(line.id), facts[str(line.id)], self._unit_key(facts[str(line.id)]))
                for line in lines
                if str(line.id) in facts and str(line.id) not in skip
            ],
            as_of=as_of,
        )
        return {
            str(line.id): composed.get(str(line.id), ((),))[0]
            for line, _entry, _fact in checked
        }

    def _write_decision(
        self,
        order: ProjectSalesOrder,
        checked: Sequence[Tuple[ProjectSalesOrderLine, Any, _LineFacts]],
        *,
        lines: Sequence[ProjectSalesOrderLine],
        facts: Dict[str, _LineFacts],
        covered: Set[str],
        carried: Sequence[_CarriedLine] = (),
        actor_user_id: str,
        as_of: Optional[date] = None,
        settle_in_place_line_ids: Sequence[str] = (),
        defer_auto_place: bool = False,
        uncover_reason_by_line: Optional[Mapping[str, str]] = None,
    ) -> Dict[str, Any]:
        previous = self.active_decision(str(order.id))
        if previous is not None:
            previous.state = DECISION_SUPERSEDED
            previous.superseded_at = datetime.utcnow()
            previous.superseded_reason = "Reconfirmed by CS."
            # Flushed on its own: the partial unique index allows one active revision, so
            # the new row cannot be inserted while the old one still says it is active.
            self.db.flush()

        latest = self.latest_decision(str(order.id))
        revision_no = (latest.revision_no if latest else 0) + 1
        now = datetime.utcnow()
        # What the ENGINE would have said about each named line, read once, here (AC-D1).
        # Beside the decision rather than instead of it: a line the planner amended is the
        # whole reason the board can ask "suggested what, decided what".
        #
        # Walked over the whole ORDER, minus the lines being carried: those keep the
        # composition they were decided with and are not re-planned, and everything else is
        # in the same walk the sheet ran, so the frozen suggestion is the one on screen.
        proposals = self._proposals_for(
            lines,
            checked,
            facts=facts,
            covered=covered,
            as_of=as_of,
        )
        # The named lines as decided now, then the carried ones exactly as they were
        # frozen (`confirm`): the same dicts, not a re-serialisation of them.
        snapshots = [
            self._snapshot(
                line, entry, fact, proposed=proposals.get(str(line.id), ())
            )
            for line, entry, fact in checked
        ] + [entry.snapshot for entry in carried]

        # Flagged lines, this revision's own (R10). Counted off the SNAPSHOTS rather than off
        # the payload, so a carried line's flag travels with it exactly as its reason does.
        suspected_issues = sum(
            1 for snapshot in snapshots if snapshot.get("suspected_system_issue")
        )
        decision = SOSupplyDecision(
            company_id=order.company_id,
            project_sales_order_id=order.id,
            revision_no=revision_no,
            state=DECISION_ACTIVE,
            suspected_system_issue=suspected_issues > 0,
            source_revision=(
                f"{order.status} @ "
                f"{order.updated_at.isoformat() if order.updated_at else ''}"
            )[:120],
            line_snapshots=snapshots,
            confirmed_by=actor_user_id,
            confirmed_at=now,
            supersedes_id=previous.id if previous else None,
        )
        self.db.add(decision)
        try:
            self.db.flush()
        except IntegrityError as exc:
            # The DB-level singleton did its job: somebody else confirmed this order
            # between our read and our write, so this attempt loses whole (AC-C05).
            self.db.rollback()
            raise AppException(
                status_code=409,
                message=(
                    "Somebody else confirmed this sales order while this composition was "
                    "open. Reload it and check the composition before confirming again."
                ),
                code="supply_decision_conflict",
            ) from exc
        # AFTER this order's own row is safely in: the donor's revision is RE-ISSUED now
        # (R25 as fixed 30 Aug), which flushes, and a flush inside the `try` above would
        # have reported the donor's write as this order's conflict.
        self._supersede_borrowed_donors(order, checked)

        # S3 (`PLAN-local-supplier-oi-routing.md`): ONE call for the whole confirm, covering
        # every product this order's Buy lines - checked or carried - could name, so a local
        # Buy raises no Order Inquiry row (AC-2.15/AC-2.18) below.
        origin_by_product = buy_origin_by_product(
            self.db,
            {str(fact.product_id) for _line, _entry, fact in checked if fact.product_id}
            | {str(entry.fact.product_id) for entry in carried if entry.fact.product_id},
        )

        buy_lines: List[Dict[str, Any]] = []
        for line, entry, fact in checked:
            self._write_allocations(decision, line, entry, fact, actor_user_id=actor_user_id)
            self._restamp_stock_location(line, entry, fact)
            buy_lines.append(
                {
                    "line": line,
                    "line_no": line.line_no,
                    "item_code": fact.item_code,
                    "buy_qty": _dec(entry.buy_qty),
                    "required_date": fact.required_date or line.delivery_date,
                    "stock_location": line.stock_location,
                    # Part 2 section 4b: this Buy is owed against something already
                    # ordered, and CS may have named which document.
                    "order_back": bool(getattr(entry, "order_back", False)),
                    "cited_document": (
                        (getattr(entry, "cited_document", None) or "").strip() or None
                    ),
                    "origin": origin_by_product.get(str(fact.product_id), "overseas"),
                    # #1362: a Buy over goods that landed for the line, kept as decided.
                    # Purchasing reads this on the Buy's own row and adjusts the linkage.
                    "landed_note": (
                        getattr(self, "_landed_buy_notices", None) or {}
                    ).get(str(line.id), {}).get("landed"),
                }
            )
        for entry in carried:
            # Its holds move to this revision row for row, and its Buy stays on
            # purchasing's list: `refresh_for_decision` treats a line absent from
            # `buy_lines` as dropped and cancels its raised rows.
            self._carry_allocations(decision, previous, entry.line)
            buy_lines.append(
                {
                    "line": entry.line,
                    "line_no": entry.line.line_no,
                    "item_code": entry.fact.item_code,
                    "buy_qty": entry.buy_qty,
                    "required_date": entry.fact.required_date or entry.line.delivery_date,
                    "stock_location": entry.line.stock_location,
                    # A carried line was decided once and nobody has asked to change it,
                    # so its ORDER BACK marking and the document CS cited travel with it.
                    # Dropping them would have turned the row back into a plain ORDER on
                    # the next confirmation of a DIFFERENT line - and an ORDER may not
                    # name an SPO allocation, so the row would have lost the only cover it
                    # had.
                    "order_back": bool(entry.snapshot.get("order_back")),
                    "cited_document": entry.snapshot.get("cited_document"),
                    # Re-raised under this revision, but not NEW to purchasing: the
                    # confirm result counts only what this confirmation decided.
                    "carried": True,
                    "origin": origin_by_product.get(str(entry.fact.product_id), "overseas"),
                }
            )
        # THE SAVED DECISIONS THIS CONFIRMATION PROMOTES GO WITH IT (S4, AC-4.4), in the
        # same transaction that wrote the revision above: a draft left behind would re-seed
        # the board's panel on the next read and offer to confirm a line already confirmed,
        # and a delete outside this transaction would lose the planner's work whenever the
        # confirmation itself was refused (the 409 above, a stale line, `confirm_many`'s
        # per-order rollback).
        #
        # Addressed by the CORE sales order line each confirmed mirror line reconciles to
        # (C2, code review round 4). It used to be the MIRROR's own `(line_no, item_code)`,
        # and the board numbers a line positionally whenever the order's lines are not all
        # mirrored (`FulfilmentBoardService._line_numbers`) - so on such an order the two
        # numberings named different rows, nothing matched, and the draft survived its own
        # promotion to re-attach beside the frozen decision.
        from app.services import project_line_draft_service

        project_line_draft_service.delete_drafts_for_lines(
            self.db,
            [line.core_sales_order_line_id for line, _entry, _fact in checked],
            company_id=str(order.company_id) if order.company_id else None,
        )
        transfers_written, transfers_failed, transfers_kept = self._write_transfers(
            order, decision, snapshots
        )
        self.db.flush()

        from app.services.project_order_inquiry_service import (
            ProjectOrderInquiryService,
        )

        # BEFORE THE HANDOFF, and that ordering is the whole of it (fixed 30 Aug, S4
        # review). An earlier revision's step-3 row on a line this confirmation is deciding
        # names a document this revision may no longer be taking, so it goes - but the
        # handoff READS those rows: a Buy marked "Order back" owns the ORDER_BACK verb on
        # its own line, and it settled the step-3 row in place, kept its link and therefore
        # raised nothing new. Cancelled afterwards, the quantity ended up with no live
        # purchasing instruction at all. Retired first, the handoff sees the true state and
        # raises the whole need.
        self._retire_supply_borrows(order, decision, checked)
        handoff = ProjectOrderInquiryService(self.db).refresh_for_decision(
            order,
            decision,
            buy_lines,
            actor_user_id=actor_user_id,
            borrow_shortfalls=self._borrow_shortfalls(
                order,
                list(checked) + [(entry.line, entry, entry.fact) for entry in carried],
            ),
            settle_in_place_line_ids=settle_in_place_line_ids,
            uncover_reason_by_line=uncover_reason_by_line,
        )
        # LADDER V7.1 STEP 3'S OTHER HALF (PLAN 3.3, R8): the placement MOVES. Run after
        # the handoff, because it needs the inquiry header the handoff mints and because the
        # donor's own hole is raised there - the asker's row is a different row with a
        # different date, and the two only make sense together.
        self._place_supply_borrows(
            order, decision, checked, handoff["inquiry"], actor_user_id=actor_user_id
        )
        auto_place_products = sorted(
            {
                str(entry["line"].product_id)
                for entry in buy_lines
                if entry["line"].product_id
            }
        )
        # THE CASCADE RUNS AGAIN HERE (`PLAN-scm-oi-draft-links.md` R6, captain 27 Aug
        # 2026), reversing one half of the handshake's ruling on purpose. The handshake was
        # right that a document tied to a row is purchasing's word; it was wrong that the
        # page should therefore be blank until somebody presses Confirm. What this writes
        # is a DRAFT - the rows are `awaiting`, and a link on an unconfirmed row IS a draft
        # (R1) - so purchasing opens the page with the answer already found and still says
        # the word. Scoped to THIS decision's own rows, never to their products: the
        # products these rows name are named by half the company's open orders too.
        #
        # `defer_auto_place` still holds it back for the planning-change apply, which shifts
        # a closed line's documents to the survivor first and then runs its own pass.
        if not defer_auto_place:
            self._draft_links_for_decision(decision, actor_user_id=actor_user_id)
        decided = len(checked) + len(carried)
        return {
            "revision_no": decision.revision_no,
            "confirmed_at": decision.confirmed_at,
            "review_state": "confirmed",
            "inquiry_rows_created": handoff["created"],
            "exceptions": handoff["exceptions"],
            # Information, not a gate (13.4): the planner is told what is still open on
            # this order rather than being stopped from committing what is not.
            "lines_decided": decided,
            "lines_undecided": max(len(self.lines_of(str(order.id))) - decided, 0),
            # The lines this press froze, in payload order, and how many covered lines rode
            # forward untouched: the caller can see exactly what was written.
            "lines_confirmed": [
                {
                    "project_line_id": str(line.id),
                    "line_no": line.line_no,
                    "item_code": fact.item_code,
                }
                for (line, _entry, fact) in checked
            ],
            "lines_carried": len(carried),
            # What this confirmation asked a warehouse to physically carry, and how much of
            # it could not be written down (PLAN section E). `transfers_failed > 0` is the
            # only sign a planner gets that a movement is missing, so it reaches the screen.
            "transfers_written": transfers_written,
            "transfers_failed": transfers_failed,
            # And what was already on somebody's list and stayed there (R16). Counted apart
            # from `transfers_written` so "nothing new had to move" reads as the outcome it
            # is, rather than as a confirmation that raised nothing.
            "transfers_kept": transfers_kept,
            # What somebody asked us to LOOK AT, beside what they decided (R10).
            "suspected_issues": suspected_issues,
            # The lines whose order inquiry row this confirmation UPDATED rather than
            # superseded (AC-P3-5). In-process only - `ConfirmResult` does not declare it,
            # so the HTTP boundary drops it, and the only reader is the planning change
            # that asked for the settle in the first place.
            "settled_in_place": handoff.get("settled_in_place", []),
            # The products whose cascade this confirmation OWES, when the caller asked to
            # run it itself (`defer_auto_place`). Empty on every ordinary confirm, which
            # has already run it by the time this returns. In-process only, like the above.
            "auto_place_products": auto_place_products,
        }

    def _retire_supply_borrows(
        self,
        order: ProjectSalesOrder,
        decision: SOSupplyDecision,
        checked: Sequence[Tuple[ProjectSalesOrderLine, Any, _LineFacts]],
    ) -> None:
        """Take down the step-3 placements of the lines THIS confirmation is re-deciding.

        A placement is a hold on a document, made for one revision's instruction. The
        moment a later revision decides that line again, the old hold is a claim on behalf
        of an instruction that no longer exists - so it comes down, links, claims and all,
        whether the new revision takes the same document, a different one, or none.

        Run BEFORE `refresh_for_decision`, never after: see the call site.

        A CARRIED line is not in `checked` and is not touched, which is right - nobody
        asked to change it, and its placement is still holding the document its own
        revision named.
        """
        from app.services.project_order_inquiry_service import (
            ProjectOrderInquiryService,
        )

        line_ids = [str(line.id) for line, _entry, _fact in checked]
        if not line_ids:
            return
        ProjectOrderInquiryService(self.db).retire_supply_borrow_rows(
            str(order.id),
            reason=f"Superseded by revision {decision.revision_no}",
            line_ids=line_ids,
            except_decision_id=str(decision.id),
        )

    def _place_supply_borrows(
        self,
        order: ProjectSalesOrder,
        decision: SOSupplyDecision,
        checked: Sequence[Tuple[ProjectSalesOrderLine, Any, _LineFacts]],
        inquiry: Any,
        *,
        actor_user_id: str,
    ) -> None:
        """Move the placement onto the asker, for every step-3 borrow this Confirm decided
        (PLAN 3.3, R8, AC-S4-3).

        Three writes, in this order, and the order is the whole of it:

        1. the DONOR's own placement on that document comes down for what it is giving up.
           First, because until it does the document reads as fully claimed and the asker's
           own link would be refused by the very rule that stops two rows pointing at one
           quantity;
        2. the ASKER gets an `ORDER_BACK`-verb row NAMING that document in `covered_by` -
           which is what that column has always meant, "why no ORDER row was emitted: the
           inbound that already covers this quantity" - and the link is written on it. The
           verb is ORDER_BACK because it is the only verb whose links may name an
           `spo_allocations` row (`OrderInquiryLink`), and the reason that rule exists is
           exactly this case: an order back is a shortfall against something already
           ordered, and the asker's is;
        3. the DONOR's own hole is already raised, at the donor's own date, by
           `_borrow_shortfalls` - the same writer step 2 uses, because it is the same fact.

        And then nothing else has to happen. `assign()` reads a link as a pinned hold
        (`stock_debt_service._holds`), so the next read of the book has the asker covered off
        that document and the donor short in its own month, with no second ledger to keep in
        step.

        A row raised by an EARLIER revision of this order for the same line is cancelled by
        `_retire_supply_borrows`, BEFORE the handoff rather than here - see that method for
        why the order is the whole of it.
        """
        from app.services.project_order_inquiry_service import (
            ProjectOrderInquiryService,
        )

        wanted = [
            (line, item)
            for line, entry, _fact in checked
            for item in (entry.borrow or [])
            if getattr(item, "supply_key", None) and _dec(item.qty) > _ZERO
        ]
        if not wanted:
            return
        service = ProjectOrderInquiryService(self.db)
        if inquiry is None:
            # `refresh_for_decision`'s gate correctly raised no header when the Buy
            # residual and the donor holes were both empty, but this line's step-3
            # borrow still needs somewhere to write its own ORDER_BACK row: the asker
            # is covered by a document, not by nothing, and that is not "no header
            # needed", it is a header this method is the first to discover it needs.
            inquiry = service.ensure_inquiry(order, actor_user_id=actor_user_id)

        for line, item in wanted:
            qty = _dec(item.qty)
            supply_key = str(item.supply_key)
            donor_core_line_id = getattr(item, "donor_core_line_id", None)
            if donor_core_line_id:
                service.release_supply_borrow(
                    supply_key=supply_key,
                    core_line_id=str(donor_core_line_id),
                    qty=qty,
                )
            fact = next(
                (f for candidate, _e, f in checked if str(candidate.id) == str(line.id)),
                None,
            )
            document = getattr(item, "supply_document", None)
            row = OrderInquiryRow(
                company_id=order.company_id,
                order_inquiry_id=inquiry.id,
                so_line_id=line.id,
                item_code=fact.item_code if fact is not None else None,
                qty=qty,
                # THE ASKER's own date, not the donor's: this row says when this line needs
                # the goods the document is bringing. The donor's hole carries the donor's
                # date, and it is a different row for that reason.
                delivery_date=(
                    (fact.required_date if fact is not None else None)
                    or line.delivery_date
                ),
                stock_location=(fact.own_code if fact is not None else None),
                verb=IV_ORDER_BACK,
                covered_by=document,
                note=(item.reason or "").strip() or None,
                supply_decision_id=decision.id,
                state=INQUIRY_RAISED,
                # Born awaiting (`PLAN-oi-confirm-per-so.md` S1): this row carries a
                # `supply_decision_id`, so it is board-origin like every other decision-
                # linked row and R1's flip applies to it too - the cascade may already have
                # auto-linked it to the document, but purchasing still has to say yes
                # before it feeds reorder planning.
                ack_state=ACK_AWAITING,
                acknowledged_by=None,
                acknowledged_at=None,
            )
            self.db.add(row)
            self.db.flush()
            service.place_supply_borrow(
                row, supply_key=supply_key, qty=qty, actor_user_id=actor_user_id
            )

    def _supersede_borrowed_donors(
        self,
        order: ProjectSalesOrder,
        checked: Sequence[Tuple[ProjectSalesOrderLine, Any, _LineFacts]],
    ) -> None:
        """A DECIDED donor's decision falls with the borrow, in the same transaction (R25).

        Ladder v7.1 offers a decided line as a donor like any other (R9), which only makes
        sense if taking its stock also takes back the promise made against it: a decision
        that still says `Reserve 30 at MWH-IB` beside stock that has just been handed to
        somebody else is a lie the next board build would print. So the donor's active
        revision is superseded with the borrower NAMED - `Borrowed by SO<asker> line <n>` -
        and its line is re-proposed the next time anybody looks at it (`_apply_frozen` has
        nothing to apply), with its ORDER_BACK already raised at its own required date by
        `_borrow_shortfalls`.

        Keyed on the donor's CORE line, which is what the component carries and what the
        project mirror joins on. A donor with no active decision needs nothing done.

        **ONLY THE BORROWED LINE LOSES ITS COVER**, which is `uncover_lines`' own rule
        applied here (fixed 30 Aug, review of S3). Flipping the donor's whole active
        revision to SUPERSEDED released every OTHER line's confirmed hold on it too: a
        five-line donor lent line 3 and lines 1, 2, 4 and 5 stopped holding stock they had
        been promised, silently, with an ORDER_BACK raised for none of them. The revision is
        RE-ISSUED minus the borrowed line - same snapshots, same allocations copied row for
        row - and the old one is superseded with the borrower named. A revision that covered
        only the borrowed line has nothing to re-issue, so it is simply superseded, which is
        the case the original code was right about and the only one it was ever tested on.
        """
        reference = order.autocount_doc_no or order.provisional_ref or str(order.id)
        by_donor: Dict[str, Any] = {}
        for line, entry, _fact in checked:
            for item in entry.borrow or []:
                donor_core_line_id = getattr(item, "donor_core_line_id", None)
                if not donor_core_line_id or _dec(item.qty) <= _ZERO:
                    continue
                by_donor.setdefault(str(donor_core_line_id), line)
        if not by_donor:
            return
        rows = (
            self.db.query(
                ProjectSalesOrderLine.core_sales_order_line_id,
                ProjectSalesOrderLine.id,
                SOSupplyDecision,
            )
            .join(
                SOSupplyDecision,
                SOSupplyDecision.project_sales_order_id
                == ProjectSalesOrderLine.project_sales_order_id,
            )
            .filter(
                ProjectSalesOrderLine.core_sales_order_line_id.in_(list(by_donor)),
                SOSupplyDecision.state == DECISION_ACTIVE,
                SOSupplyDecision.project_sales_order_id != order.id,
            )
            .all()
        )
        for core_line_id, donor_line_id, decision in rows:
            borrower = by_donor.get(str(core_line_id))
            if borrower is None or decision.state != DECISION_ACTIVE:
                continue
            self._reissue_without_line(
                decision,
                str(donor_line_id),
                reason=f"Borrowed by SO{reference} line {borrower.line_no}",
            )

    def _reissue_without_line(
        self, decision: SOSupplyDecision, project_line_id: str, *, reason: str
    ) -> None:
        """Supersede `decision` and re-issue it covering every line but this one.

        The un-decide shape `uncover_lines` already has, written directly on the rows rather
        than through `confirm`: nothing is being re-decided here, so nothing may be
        re-validated. The surviving snapshots travel as the SAME dicts and their holds are
        copied allocation for allocation (`_carry_allocations`' rule - a superseded
        revision's rows stop holding the moment it is superseded, so the new revision needs
        its own), stamped with who confirmed them and when, because that is still the answer
        to "who promised this".
        """
        snapshots = [
            snapshot
            for snapshot in (decision.line_snapshots or [])
            if str(snapshot.get("project_line_id") or "") != str(project_line_id)
        ]
        # The dropped line's own step-3 placement goes with it (S4): it is being un-decided,
        # so its hold on a document is a claim nobody stands behind any more.
        from app.services.project_order_inquiry_service import (
            ProjectOrderInquiryService,
        )

        ProjectOrderInquiryService(self.db).retire_supply_borrow_rows(
            str(decision.project_sales_order_id),
            reason=reason,
            line_ids=[str(project_line_id)],
        )
        now = datetime.utcnow()
        decision.state = DECISION_SUPERSEDED
        decision.superseded_at = now
        decision.superseded_reason = reason
        # Flushed on its own: the partial unique index allows one active revision, so the
        # replacement cannot be inserted while this row still says it is active.
        self.db.flush()
        if not snapshots:
            return
        latest = self.latest_decision(str(decision.project_sales_order_id))
        fresh = SOSupplyDecision(
            company_id=decision.company_id,
            project_sales_order_id=decision.project_sales_order_id,
            revision_no=(latest.revision_no if latest else 0) + 1,
            state=DECISION_ACTIVE,
            suspected_system_issue=any(
                snapshot.get("suspected_system_issue") for snapshot in snapshots
            ),
            source_revision=decision.source_revision,
            line_snapshots=snapshots,
            confirmed_by=decision.confirmed_by,
            confirmed_at=decision.confirmed_at,
            supersedes_id=decision.id,
        )
        self.db.add(fresh)
        self.db.flush()
        kept = {str(snapshot.get("project_line_id") or "") for snapshot in snapshots}
        rows = (
            self.db.query(SOLineAllocation)
            .filter(SOLineAllocation.decision_id == decision.id)
            .all()
        )
        for row in rows:
            if str(row.so_line_id) not in kept:
                continue
            self.db.add(
                SOLineAllocation(
                    company_id=row.company_id,
                    so_line_id=row.so_line_id,
                    source_type=row.source_type,
                    warehouse_id=row.warehouse_id,
                    source_project_id=row.source_project_id,
                    qty=row.qty,
                    claim_id=row.claim_id,
                    decision_id=fresh.id,
                    reason=row.reason,
                    donor_impact_snapshot=row.donor_impact_snapshot,
                    confirmed_by=row.confirmed_by,
                    confirmed_at=row.confirmed_at,
                )
            )
        self.db.flush()

    def uncover_lines(
        self,
        order: ProjectSalesOrder,
        line_ids: Sequence[str],
        *,
        actor_user_id: str,
        reason: str,
        reason_by_line: Optional[Mapping[str, str]] = None,
    ) -> bool:
        """Take these lines OUT of the active revision and leave the rest exactly as it is.

        The un-decide seam, called for its own sake (`PLAN-scm-oi-handshake.md`: purchasing
        rejects an order inquiry row, so the LINE is undecided again and CS decides it
        afresh). `confirm`'s carry-forward rule does the work: a revision is written naming
        no line, `uncover_line_ids` drops these from the carry, and every other covered line
        arrives verbatim with its holds.

        Two cases it answers itself rather than writing a revision that says nothing:

        * no active revision - there is nothing to uncover, and CS was never holding stock
          for this line;
        * the active revision covers ONLY the lines being uncovered - a revision covering
          no line at all is worse than none, because the board would read it as a decision.
          `supersede_for_material_change` retires it instead, which is exactly "this order
          is undecided again".

        The new revision is attributed to whoever took the ORIGINAL decision, never to the
        buyer who rejected a row: this is CS's own decision minus one line, and stamping
        purchasing on it would make every order inquiry row of the order read as raised by
        the person who refused one of them.

        `reason_by_line` (S2/S3, `PLAN-board-reject-on-confirmed-line.md`, rework fix
        round): the BARE reason for EACH line in `line_ids`, keyed by id - the purchasing-
        refusal caller (`reject_row`/`reject_rows`) still passes none, so its own single
        row keeps stamping `reason` bare exactly as it always has (its `reason` IS already
        bare - there is no "Line N rejected:" join on that path). The board's own reject
        caller (`fulfilment_planning.py`) passes both: `reason` is the joined sentence for
        `superseded_reason`, `reason_by_line` is what each row's own note reads instead.
        """
        from app.schemas.project_supply import ConfirmSupplyBody

        active = self.active_decision(str(order.id))
        if active is None:
            return False
        covered = {
            str(snapshot.get("project_line_id") or "")
            for snapshot in (active.line_snapshots or [])
        }
        covered.discard("")
        wanted = {str(line_id) for line_id in line_ids} & covered
        if not wanted:
            return False
        # The step-3 placements of the lines being taken out, first (S4). `confirm` retires
        # only the rows of the lines it NAMES, and this call names none - so without this
        # the document went on being pinned to a line nobody is deciding any more. The
        # whole-revision branch below does the same through `supersede_for_material_change`.
        from app.services.project_order_inquiry_service import (
            ProjectOrderInquiryService,
        )

        ProjectOrderInquiryService(self.db).retire_supply_borrow_rows(
            str(order.id), reason=reason, line_ids=sorted(wanted)
        )
        if covered - wanted:
            self.confirm(
                order,
                ConfirmSupplyBody(lines=[]),
                actor_user_id=str(active.confirmed_by or actor_user_id),
                uncover_line_ids=sorted(wanted),
                uncover_reason_by_line=reason_by_line,
            )
            # WHY the revision this call just retired was retired. `confirm` stamps its
            # own "Reconfirmed by CS.", which is not what happened here - nobody
            # reconfirmed anything, purchasing refused a row - and that sentence is the
            # only record of the cause on the superseded revision.
            active.superseded_reason = reason
            self.db.flush()
        else:
            # Owner case, 22 Sep 2026 (fix round, `PLAN-board-reject-on-confirmed-line.md`):
            # "one confirmed Buy line, reject it, it must not flow to purchasing at all."
            # This branch writes NO successor revision for the confirm-based path above to
            # route the retirement through - `refresh_for_decision`'s own
            # `_retire_uncovered_rows` call never runs - so the wanted lines' still-raised
            # ORDER/ORDER_BACK rows are retired directly, the same way, before the revision
            # they belong to is retired with no replacement. `supersede_for_material_change`
            # already does this for a step-3 PLACEMENT (a document already covers the row);
            # this is its own raised-instruction twin.
            ProjectOrderInquiryService(self.db).retire_rows_for_dropped_lines(
                str(order.id), active, sorted(wanted), reason=reason,
                actor_user_id=actor_user_id, reason_by_line=reason_by_line,
            )
            self.supersede_for_material_change(order, reason)
        return True

    def _draft_links_for_decision(
        self, decision, *, actor_user_id: str
    ) -> None:
        """Find the documents for the rows this confirmation just raised (R6).

        Best-effort in a SAVEPOINT, exactly like `auto_place_for_confirmed_products` and
        for the same reason: the decision and the inquiry rows are already written by the
        time this runs, and a defect in the walk must not turn that success into a 500 the
        planner cannot get past. The page's own Auto link all makes the same pass again.

        Under the PLAN's link horizon, with nothing asked of the planner: the board has no
        place to put a date and CS is not the person who owns one (R6).
        """
        try:
            with self.db.begin_nested():
                from app.services.project_order_inquiry_service import (
                    ProjectOrderInquiryService,
                )

                service = ProjectOrderInquiryService(self.db)
                row_ids = service.row_ids_of_decision(str(decision.id))
                if not row_ids:
                    return
                service.auto_place_for_products(
                    None,
                    actor_user_id=actor_user_id,
                    trigger="raise",
                    row_ids=row_ids,
                    include_awaiting=True,
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "supply confirmed, but the draft-link pass failed (%s)", exc
            )

    def auto_place_for_confirmed_products(
        self, product_ids: Sequence[str], *, actor_user_id: str
    ) -> None:
        """The cascade for these products, run by a caller that owes it one.

        NOT a decision confirm any more (`PLAN-scm-oi-handshake.md` section 3): a board
        confirm links nothing, because links are purchasing's. What still calls this is
        the planning-change apply, which deferred its own pass (`defer_auto_place`) and
        owes it afterwards - and the pass is a no-op for every row nobody has acknowledged,
        which is what makes it safe to keep.

        Best-effort on purpose, in a SAVEPOINT, mirroring
        `ProjectOrderInquiryService._hand_to_purchasing`: the confirm itself already
        succeeded (the decision and the inquiry rows are written) by the time this runs,
        so a failure here must not turn that success into a 500 the retry cannot repair -
        it would only repeat the placement, since re-running finds nothing left to place
        for a row already tagged.
        """
        wanted = [str(product_id) for product_id in product_ids if product_id]
        if not wanted:
            return
        try:
            with self.db.begin_nested():
                from app.services.project_order_inquiry_service import (
                    ProjectOrderInquiryService,
                )

                ProjectOrderInquiryService(self.db).auto_place_for_products(
                    wanted,
                    actor_user_id=actor_user_id,
                    trigger="decision_confirm",
                    # Awaiting rows too (R6): the planning change raised them a moment ago
                    # and its own pass is the raise-time cascade for them.
                    include_awaiting=True,
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "supply confirmed, but the order-inquiry auto-place pass failed (%s)", exc
            )

    def _borrow_shortfalls(
        self,
        order: ProjectSalesOrder,
        checked: Sequence[Tuple[ProjectSalesOrderLine, Any, _LineFacts]],
    ) -> List[Dict[str, Any]]:
        """The holes this confirmation's borrows opened at the donors (PLAN 13.11).

        The captain: "when borrowed, does it / should it trigger an order back via order
        inquiries? Because I sort of need to return, right - or we should order back only if
        the available quantity of the borrowed location is negative?" **Only if negative.**
        A donor whose availability survives the borrow is not short of anything, and raising
        a buy for it would order stock nobody needs; a donor pushed below zero has a hole
        somebody must cover, and purchasing is told about the HOLE, not about the whole
        quantity that was taken.

        Aggregated per donor pile, because two lines of one order taking from the same
        location open one hole between them and not two.

        A Reserve drawn from the line's SHARED POOL is a donor by the same rule (13.11, the
        second rule). The captain, on a rung reading `Pool BRW | 4 | took 4`: "when we take
        from BRW, first we need to see its available quantity also, if available quantity is
        negative and if we want to take, we must have an order back just like mentioned."
        The pool's own book ranked BEHIND this line still wants that stock, and it shows in
        the pool's availability rather than in the queue ahead - so a pool take that leaves
        the pool's `on hand - SO + SPO` below zero opens a hole there, and purchasing is told
        about the hole at the POOL's location. A Reserve at the line's OWN location is never
        one: that location's demand IS this line, already inside its `SO qty`, and taking the
        reserve out again would count the same quantity twice. **A Borrow from the line's
        own location is never one either, for the same reason** - a dealer hot-selling
        product may only Reserve from the pool, so its own location is a legitimate Borrow
        source, and its demand is still inside that location's SO qty.

        The donor's availability is the AutoCount triple LESS what other confirmed
        decisions already hold there from elsewhere. A borrow, or a pool take, writes a
        hold and no sales-order line at the donor, so `on hand - SO + SPO` cannot see it,
        and a second borrower judged on the triple alone would be offered stock the first
        one has already taken. A hold whose line's own location IS the donor is left out:
        that demand is already inside the donor's SO qty.

        `checked` carries the named lines AND the carried ones (`_CarriedLine`), because
        a carried borrow still holds its donor's stock and the hole is the order's, not the
        revision's.
        """
        piles: Dict[Tuple[str, str], Dict[str, Any]] = {}

        def pile_for(
            line: ProjectSalesOrderLine, fact: _LineFacts, warehouse_id: str
        ) -> Dict[str, Any]:
            return piles.setdefault(
                (fact.product_id or "", warehouse_id),
                {
                    "borrowed": _ZERO,
                    "group": None,
                    "item_code": fact.item_code,
                    "lines": [],
                    "line": line,
                    "required_date": fact.required_date or line.delivery_date,
                },
            )

        # Ladder v2 group borrow (section E rule 4): "every borrow carries an order-back",
        # unconditionally - unlike the location-pile shortfall below, which only fires
        # when the donor's WHOLE pile goes negative. A group borrow takes another sales
        # order's own committed quantity, so the donor line is short by exactly what was
        # taken from it, full stop; built here, before the pile loop, so it never depends
        # on the pile's own availability triple.
        order_backs: List[Dict[str, Any]] = []
        reference = order.autocount_doc_no or order.provisional_ref or ""
        # AC-B2: "an ORDER_BACK row FOR THE DONOR" - a group borrow's hole is raised on the
        # DONOR's own project line, not the asker's (which is what every OTHER row this
        # method writes hangs off - the location-pile shortfall below has no single line to
        # name it against). One batch query for every `donor_core_line_id` this
        # confirmation's borrows name, rather than one lookup per component.
        donor_core_line_ids = {
            str(getattr(item, "donor_core_line_id", None))
            for _line, entry, _fact in checked
            for item in (entry.borrow or [])
            if getattr(item, "donor_core_line_id", None) and _dec(item.qty) > _ZERO
        }
        donor_project_line_by_core: Dict[str, ProjectSalesOrderLine] = {}
        if donor_core_line_ids:
            for donor_line in (
                self.db.query(ProjectSalesOrderLine)
                .filter(
                    ProjectSalesOrderLine.core_sales_order_line_id.in_(donor_core_line_ids)
                )
                .all()
            ):
                donor_project_line_by_core[str(donor_line.core_sales_order_line_id)] = donor_line
        for line, entry, fact in checked:
            for item in entry.borrow or []:
                qty = _dec(item.qty)
                donor_core_line_id = getattr(item, "donor_core_line_id", None)
                if qty <= _ZERO or not donor_core_line_id:
                    continue
                supply_key = getattr(item, "supply_key", None)
                if supply_key:
                    # STEP 3, AND THE DONOR'S OWN ROW SAYS IT FIRST (S4, fixed 30 Aug).
                    # A donor holding a document through a PLACEMENT of its own gets that
                    # placement taken down by this same Confirm (`release_supply_borrow`),
                    # which puts its own row straight back to `raised` for the quantity -
                    # at the donor's date, on the donor's line, which is a sharper record
                    # than anything raised over here. Raising this one as well told
                    # purchasing to buy 100 for a donor that is short 50.
                    #
                    # The remainder is still ours: a donor holding the document through a
                    # confirmed DECISION and no link re-raises nothing by itself, and that
                    # is the case this row exists for.
                    from app.services.project_order_inquiry_service import (
                        ProjectOrderInquiryService,
                    )

                    qty -= min(
                        qty,
                        ProjectOrderInquiryService(self.db).supply_borrow_held_qty(
                            str(supply_key), str(donor_core_line_id)
                        ),
                    )
                    if qty <= _ZERO:
                        continue
                donor_so_number = getattr(item, "donor_so_number", None) or "an unnamed sales order"
                donor_line_no = getattr(item, "donor_line_no", None)
                donor_agent_code = getattr(item, "donor_agent_code", None)
                warehouse = self._warehouse_row(str(item.warehouse_id))
                code = warehouse.warehouse_code if warehouse else ""
                line_text = f" line {donor_line_no}" if donor_line_no is not None else ""
                agent_text = f" (agent {donor_agent_code})" if donor_agent_code else ""
                # The DONOR's own project line when it has one - AC-B2's "for the donor".
                # Falls back to the asking line only for a donor core line with no project
                # mirror (never adopted onto `projects.sales_orders`), which has nowhere
                # else to hang the row and is the pre-existing behaviour for that edge case.
                donor_line = donor_project_line_by_core.get(str(donor_core_line_id), line)
                order_backs.append(
                    {
                        "line": donor_line,
                        "item_code": fact.item_code,
                        "qty": qty,
                        "required_date": (
                            getattr(item, "donor_required_date", None)
                            or fact.required_date
                            or line.delivery_date
                        ),
                        "stock_location": code,
                        "note": (
                            f"Order-back: {donor_so_number}{line_text} lent "
                            f"{qty_text(qty)} to {reference} line {line.line_no}"
                            f"{agent_text}"
                        ),
                    }
                )

        # LADDER V4 (section 1d, ruled 26 August 2026): a borrow from ANOTHER OWNERSHIP
        # GROUP raises an order back against that group, for the WHOLE quantity taken and
        # not merely for a hole. A POOL DRAW RAISES NOTHING (AC-L13). The v3 rule - raise
        # only where the donor pile's own availability went negative - was the right rule
        # when a warehouse's own reading decided what it could lend; under v4 rung 3 draws
        # the pool only while the five pools NET positive between them and rung 4 borrows
        # only within the donor group's own net, so neither can push a pile below zero and
        # the negativity test would raise nothing at all, ever. What is left to record is
        # the fact itself: the `-IR` group lent 40 to a `-BB` line and is owed 40 back.
        for line, entry, fact in checked:
            if not fact.product_id:
                continue
            own_id = str(fact.warehouse.id) if fact.warehouse else None
            for item in entry.borrow or []:
                qty = _dec(item.qty)
                if qty <= _ZERO or not item.warehouse_id:
                    continue
                if str(item.warehouse_id) == own_id:
                    continue
                if getattr(item, "donor_core_line_id", None):
                    # A borrow from another ORDER: already raised above, against the donor
                    # LINE, which is a sharper record than the location it sits at.
                    continue
                if getattr(item, "supply_key", None):
                    # STEP 3 ON A FREE DOCUMENT (S4). Nobody was waiting on it, so nobody is
                    # owed it back - the same rule step 1's free pile follows. The location
                    # test below would have read the bin the container is BOUND for as a
                    # donor group and raised an order-back against a group that has lost
                    # nothing.
                    continue
                donor_group = self.netting().group_of(str(item.warehouse_id))
                if not donor_group or donor_group == fact.group_code:
                    # The line's own group (its stock to move) or a pool (shared, and
                    # nobody is owed it back).
                    continue
                pile = pile_for(line, fact, str(item.warehouse_id))
                pile["borrowed"] += qty
                pile["lines"].append(line.line_no)
                pile["group"] = donor_group
        if not piles:
            return order_backs

        out: List[Dict[str, Any]] = list(order_backs)
        for key, pile in piles.items():
            taken = pile["borrowed"]
            if taken <= _ZERO:
                continue
            warehouse = self._warehouse_row(key[1])
            code = warehouse.warehouse_code if warehouse else ""
            lines = ", ".join(f"line {no}" for no in sorted(set(pile["lines"])))
            out.append(
                {
                    "line": pile["line"],
                    "item_code": pile["item_code"],
                    "qty": taken,
                    "required_date": pile["required_date"],
                    # The DONOR's location: the order back is owed where the stock left
                    # from, not where the borrowing line sits.
                    "stock_location": code,
                    "note": (
                        f"Order-back: {code} lent {qty_text(taken)} to {reference} "
                        f"{lines}"
                    ),
                }
            )
        return out

    def _snapshot(
        self,
        line: ProjectSalesOrderLine,
        entry: Any,
        fact: _LineFacts,
        *,
        proposed: Sequence[Component] = (),
    ) -> Dict[str, Any]:
        """Freeze the line as it was decided, in the words it was decided in (AC-G01),
        AND what the engine had proposed for it at that moment (AC-D1).

        `proposed` is the ladder's own composition for this line, read once by
        `_write_decision` just before the revision is written. It is frozen rather than
        recomputed on read for the same reason the decision is: the ladder answers against
        live stock, and a proposal recomputed a week later is a different proposal. Without
        it an amended line loses what the engine had said, so the board can only ever show
        what was decided and "Suggested" is unanswerable the next day.
        """
        components: List[Dict[str, Any]] = []
        reserve_by_id = {
            str(w.id): code for code, w in self._reserve_ladder_locations(fact).items()
        }
        siblings = self._group_sibling_warehouses(fact)
        for item in entry.reserve or []:
            qty = _dec(item.qty)
            if qty <= _ZERO:
                continue
            location = self._warehouse_of(fact, str(item.warehouse_id)) or reserve_by_id.get(
                str(item.warehouse_id)
            )
            components.append(
                {
                    "kind": RESERVE,
                    "qty": qty_text(qty),
                    "source_location": location,
                    "source_warehouse_id": str(item.warehouse_id),
                    "reason": self._reserve_reason(fact, location),
                    "rung": (
                        RUNG_GROUP_TAKE
                        if location in siblings
                        else RUNG_POOL
                        if location and location != fact.own_code
                        else None
                    ),
                }
            )
        # THE WATER, SPREAD OVER THE LOCATIONS QUESTION 1 OFFERED IT AT, in the same draw
        # order the proposal used and bounded by the same candidate list the recheck above
        # judged the quantity against. `ConfirmLine` carries one scalar, so the split has to
        # be derived - from the ladder's own ledger, never from a second reading of it - and
        # without it a frozen water row named the line's own location whatever sibling the
        # goods were actually coming to, and carried no rung at all, so the board filed a
        # confirmed v5 line under "Incoming supply" while its own suggestion filed it under
        # "Use own location".
        timely = _dec(entry.timely_spo_qty)
        if timely > _ZERO:
            left = timely
            for candidate in self._group_take_candidates(fact):
                if left <= _ZERO:
                    break
                if not candidate.get("water"):
                    continue
                take = min(left, max(_dec(candidate.get("qty")), _ZERO))
                if take <= _ZERO:
                    continue
                location = str(candidate["location"])
                source = self._warehouse_by_code(location)
                components.append(
                    {
                        "kind": TIMELY_SPO,
                        "qty": qty_text(take),
                        "source_location": location,
                        "source_warehouse_id": str(source.id) if source else None,
                        "reason": group_water_reason(
                            location,
                            take,
                            fact.group_code,
                            fact.group_offer if fact.group_code else None,
                            candidate.get("arrival_date"),
                        ),
                        "rung": RUNG_GROUP_TAKE,
                    }
                )
                left -= take
            if left > _ZERO:
                # No water on offer for it: an ungrouped line, or a quantity a person
                # recorded by hand. The retired rung 1's shape, and it reads as one.
                components.append(
                    {
                        "kind": TIMELY_SPO,
                        "qty": qty_text(left),
                        "source_location": fact.own_code,
                        "source_warehouse_id": (
                            str(fact.warehouse.id) if fact.warehouse else None
                        ),
                        "reason": self._timely_reason(fact),
                    }
                )
        for item in entry.borrow or []:
            qty = _dec(item.qty)
            if qty <= _ZERO:
                continue
            warehouse = self._warehouse_row(str(item.warehouse_id))
            donor = (
                self.db.query(Project).filter(Project.id == item.donor_project_id).first()
                if item.donor_project_id
                else None
            )
            supply_key = getattr(item, "supply_key", None)
            is_group_borrow = bool(getattr(item, "donor_core_line_id", None))
            arrival = getattr(item, "arrival_date", None)
            components.append(
                {
                    "kind": "borrow",
                    "qty": qty_text(qty),
                    "source": item.source,
                    "source_location": warehouse.warehouse_code if warehouse else None,
                    "source_warehouse_id": str(item.warehouse_id),
                    "donor_project_ref": donor.project_code if donor else None,
                    "donor_project_id": str(donor.id) if donor else None,
                    "reason": (
                        # Step 3's sentence is the ENGINE's own, rebuilt from what was
                        # posted rather than replaced by "borrowed from free stock at X":
                        # the document, its arrival and the debt month are the whole of
                        # what this component says, and a location is none of them.
                        supply_borrow_reason(
                            qty,
                            kind=parse_supply_key(supply_key)[0] or SA_KIND_SPO,
                            document=getattr(item, "supply_document", None),
                            arrival_date=arrival,
                            donor_so_number=getattr(item, "donor_so_number", None),
                            donor_line_no=getattr(item, "donor_line_no", None),
                            donor_agent_code=getattr(item, "donor_agent_code", None),
                            donor_required_date=getattr(
                                item, "donor_required_date", None
                            ),
                        )
                        if supply_key
                        else self._borrow_reason(item, warehouse, donor)
                    ),
                    "cs_reason": (item.reason or "").strip(),
                    "supply_key": supply_key,
                    "supply_document": getattr(item, "supply_document", None),
                    "arrival_date": arrival.isoformat() if arrival else None,
                    "rung": (
                        RUNG_SUPPLY_BORROW
                        if supply_key
                        else RUNG_GROUP_BORROW
                        if is_group_borrow
                        else None
                    ),
                    "donor_so_number": getattr(item, "donor_so_number", None),
                    "donor_line_no": getattr(item, "donor_line_no", None),
                    "donor_agent_code": getattr(item, "donor_agent_code", None),
                    "same_agent": bool(getattr(item, "same_agent", False)),
                    "order_back_qty": qty_text(qty) if is_group_borrow else None,
                    # (a free document names no donor, so `is_group_borrow` is False and
                    # nothing is owed back - which is the whole content of "it was free")
                    "donor_core_line_id": getattr(item, "donor_core_line_id", None),
                    "donor_required_date": (
                        getattr(item, "donor_required_date", None).isoformat()
                        if getattr(item, "donor_required_date", None)
                        else None
                    ),
                }
            )
        if _dec(entry.buy_qty) > _ZERO:
            components.append(
                {
                    "kind": BUY,
                    "qty": qty_text(_dec(entry.buy_qty)),
                    "reason": "remaining uncovered need",
                    "cs_reason": (entry.buy_reason or "").strip() or None,
                }
            )

        return {
            "line_no": line.line_no,
            "project_line_id": str(line.id),
            "core_line_id": str(line.core_sales_order_line_id)
            if line.core_sales_order_line_id
            else None,
            "product_id": fact.product_id,
            "item_code": fact.item_code,
            "location": fact.own_code,
            "required_date": (
                fact.required_date.isoformat() if fact.required_date else None
            ),
            "open_qty": qty_text(fact.open_qty),
            "timely_spo_qty": qty_text(_dec(entry.timely_spo_qty)),
            "timely_spo_refs": [
                {
                    "spo_number": ref.spo_number,
                    "arrival_date": (
                        ref.arrival_date.isoformat() if ref.arrival_date else None
                    ),
                    "qty": qty_text(ref.qty),
                    "overdue_days": ref.overdue_days,
                }
                for ref in fact.timely_refs
            ],
            "reserve_qty": qty_text(
                sum((_dec(item.qty) for item in entry.reserve or []), _ZERO)
            ),
            "borrow_qty": qty_text(
                sum((_dec(item.qty) for item in entry.borrow or []), _ZERO)
            ),
            "buy_qty": qty_text(_dec(entry.buy_qty)),
            "components": components,
            # What the ENGINE proposed for this line at this moment, beside what was
            # decided (AC-D1). Always present, empty on a line the ladder could not plan -
            # an ABSENT key is the one thing that means "this revision predates the field",
            # and the board renders that as "not recorded" rather than as "nothing".
            "proposed_components": [
                self._proposed_component(component, fact) for component in proposed
            ],
            "suggestion_basis": {
                "is_dealer_hot_selling": fact.is_dealer_hot_selling,
                "is_project_hot_selling": fact.is_project_hot_selling,
                "classification_unavailable": fact.classification_unavailable,
                "pool_location": fact.pool_code,
                "pool_cap": None,
                "pool_reorder_level": (
                    qty_text(fact.pool_reorder_level) if fact.pool_code else None
                ),
            },
            "lifecycle_warning": (
                "This product is discontinued." if fact.is_discontinued else None
            ),
            "buy_reason": (entry.buy_reason or "").strip() or None,
            # An ORDER BACK Buy and the document CS cited for it (part 2 section 4b).
            # Frozen with the line for the same reason the reasons above are: re-opening
            # the Amend editor on a covered line has to show what was decided, and the
            # order inquiry row is raised from these two.
            "order_back": bool(getattr(entry, "order_back", False)) and _dec(entry.buy_qty) > _ZERO,
            "cited_document": (
                (getattr(entry, "cited_document", None) or "").strip() or None
                if getattr(entry, "order_back", False)
                else None
            ),
            # Why the composition above is not the one the engine proposed. Frozen with the
            # line rather than discarded at the call: every other component carries the
            # sentence of the RULE that produced it, and on an amended line those sentences
            # explain a decision nobody took.
            "amend_reason": (getattr(entry, "amend_reason", None) or "").strip() or None,
            # "This might be a system problem" (R10). Frozen for the same reason the reason
            # is: the board reads the decision back to draw its pill, and a flag that lived
            # only in the session's draft would say the doubt had been answered the moment
            # the page was refreshed.
            "suspected_system_issue": bool(
                getattr(entry, "suspected_system_issue", False)
            ),
        }

    def _proposed_component(
        self, component: Component, fact: _LineFacts
    ) -> Dict[str, Any]:
        """One component of the engine's proposal, in the SAME keys a decided one is
        frozen under, so both halves of a snapshot are read by one reader.

        The `rung` comes off the component itself here rather than being inferred from the
        location the way a decided Reserve's has to be: the engine knows which rung it drew
        from, and re-deriving it would be a second opinion about its own answer.
        """
        return {
            "kind": component.kind,
            "qty": qty_text(component.qty),
            "source_location": component.source_location,
            "source_warehouse_id": self.warehouse_id_for_code(component.source_location),
            "reason": component.reason,
            "rung": component.rung,
            # WHICH LADDER wrote this proposal. A frozen suggestion outlives the rule that
            # made it - `MWH-IB has 30 available in the IB group` is a v3 sentence about a
            # warehouse's own availability, and under v4 that reading does not exist - so a
            # screen showing it beside a live one has to be able to say which is which.
            # Its ABSENCE is the signal for every snapshot written before this key: a JSON
            # column needs no migration to grow one, and "no stamp" is exactly "pre v4".
            "ladder": LADDER_VERSION,
            "donor_so_number": component.donor_so_number,
            "donor_line_no": component.donor_line_no,
            "donor_agent_code": component.donor_agent_code,
            "same_agent": bool(component.same_agent),
            "donor_core_line_id": component.donor_core_line_id,
            "donor_required_date": (
                component.donor_required_date.isoformat()
                if component.donor_required_date
                else None
            ),
            # Step 3's document, frozen with the suggestion for the reason everything else
            # here is: a proposal recomputed a week later is a different proposal, and this
            # one named a document that may have landed since.
            "supply_key": component.supply_key,
            "supply_document": component.supply_document,
            "arrival_date": (
                component.arrival_date.isoformat() if component.arrival_date else None
            ),
        }

    def _reserve_reason(self, fact: _LineFacts, location: Optional[str]) -> str:
        """The rule's own sentence for a confirmed Reserve component, at whichever
        location it named - ladder v3's GROUP first (this line's own location, then its
        siblings), then the pool chain (own site pool, then every other)."""
        pool_chain = self._pool_chain(fact, pool_free_left=None)
        for candidate, qty in pool_reserve_capacity(
            pools=pool_chain, pools_net=fact.pools_net
        ):
            if candidate == location:
                # LADDER V8 (nit, code review round 3 batch 2): the v7.1 sentence ("Pool
                # BRW lends N of the M the site pools net between them") is retired - the
                # pool answers with its OWN allowance now (R-A/R-B), and re-displaying a
                # CONFIRMED component in the old wording said something the walk that
                # produced it never said.
                pool = next(
                    (p for p in pool_chain if str(p.get("location")) == candidate), None
                )
                pct = self._fulfilment_settings().get("pool_share_pct")
                allowance = available_for_project(
                    pool.get("available") if pool else None, fact.pools_net, pct
                )
                return pool_share_reason(str(location), qty, allowance)
        for candidate in self._group_take_candidates(fact):
            # A confirmed RESERVE is floor stock by definition; the water half of question 1
            # is confirmed as `timely_spo` and reads through `_timely_reason`.
            if candidate.get("water") or candidate["location"] != location:
                continue
            return group_take_reason(
                str(location),
                _dec(candidate["qty"]),
                fact.group_code,
                fact.group_offer if fact.group_code else None,
            )
        return f"free stock at {location} covers the need by the required date"

    def _timely_reason(self, fact: _LineFacts) -> str:
        if not fact.timely_refs:
            return "incoming supply arrives by the required date"
        first = fact.timely_refs[0]
        # The engine's own builder, not a second copy of the sentence: this reason and the
        # one in the trail describe the same row, and they said the same thing by
        # coincidence until an overdue promise had to be named in both.
        return spo_reason(first.spo_number, first.arrival_date, first.overdue_days)

    def _borrow_reason(
        self, item: Any, warehouse: Optional[Warehouse], donor: Optional[Project]
    ) -> str:
        where = warehouse.warehouse_code if warehouse else "another location"
        if item.source == ALLOC_SOURCE_OTHER_PROJECT and donor is not None:
            return f"borrowed from {donor.project_code} at {where}"
        return f"borrowed from free stock at {where}"

    def _write_transfers(
        self,
        order: ProjectSalesOrder,
        decision: SOSupplyDecision,
        snapshots: Sequence[Dict[str, Any]],
    ) -> Tuple[int, int, int]:
        """The physical movements this revision implies (PLAN section E, Q2 ruled).

        A reserve or a borrow drawn from a warehouse that is not the line's own location
        has to be carried there before anything can be delivered, and until this existed
        nothing in the product said so - the captain's third finding of 25 August. One
        `proposed` row per such component; the Transfers page is where a person approves
        it deliberately.

        Read off `snapshots` rather than off `checked`, so a NAMED line and a CARRIED one
        are written by the same loop: a carried line's components are the previous
        revision's frozen dicts in the same shape, its transfers were just cancelled with
        that revision, and it therefore needs fresh ones or the movement it still implies
        would vanish because an unrelated line was reconfirmed.

        Best-effort in a SAVEPOINT, mirroring `auto_place_for_confirmed_products` and for its
        reason: the decision and the holds are already written by the time this runs, so a
        failure here must not turn a confirmation the planner was told succeeded into a
        500 - and without the savepoint the failed statement would poison the transaction
        the promise itself is in, so catching the exception alone would not save it.

        **The failure is reported, not swallowed.** Returns `(written, failed, kept)`, which the
        confirm result carries to the planner (`transfers_written` / `transfers_failed`):
        a movement nobody was told about is a movement nobody makes, and a silent log line
        on a server is not telling anybody. `failed` is the number of components that
        SHOULD have raised a row, so the planner knows how many are missing and can
        reconfirm.
        """
        from app.services import stock_transfer_service as transfers

        try:
            with self.db.begin_nested():
                # RECONCILED against what is already open, never swept (R16): a movement the
                # new revision still asks for keeps its row, its number and the approval
                # somebody already gave it. Keyed on the ORDER, never on the revision this
                # one supersedes: a confirmation whose transfer write failed leaves its
                # predecessor's rows open, and matching only `previous.id` would strand them.
                written, kept = transfers.reconcile_for_decision(
                    self.db,
                    order,
                    decision,
                    snapshots,
                    warehouse_id_for_code=self.warehouse_id_for_code,
                )
            return len(written), 0, len(kept)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "supply confirmed, but the stock transfers for revision %s were not "
                "written (%s)",
                decision.revision_no,
                exc,
            )
            return 0, transfers.movements_implied(
                snapshots, warehouse_id_for_code=self.warehouse_id_for_code
            ), 0

    def _write_allocations(
        self,
        decision: SOSupplyDecision,
        line: ProjectSalesOrderLine,
        entry: Any,
        fact: _LineFacts,
        *,
        actor_user_id: str,
    ) -> None:
        """The components of THIS revision, grouped by `decision_id`.

        The previous revision's rows are left exactly where they are: they are the record
        of what was promised then, and the audit trail is the only thing that can answer
        "what did we tell the customer in March".
        """
        now = datetime.utcnow()
        reserve_locations = self._reserve_ladder_locations(fact)
        by_id = {str(w.id): code for code, w in reserve_locations.items()}
        siblings = self._group_sibling_warehouses(fact)
        unowned = self._unowned_holds(line)
        for item in entry.reserve or []:
            qty = _dec(item.qty)
            if qty <= _ZERO:
                continue
            # A hold this line already has that belongs to NO revision is this reserve,
            # not a second one: a reallocation handed it over (Slice D, AC-D4) precisely so
            # it would survive every future revision, and writing a fresh row beside it
            # would hold the same physical units twice - the floor would read short by
            # that quantity for everybody else, for good.
            standing = unowned.get(str(item.warehouse_id), _ZERO)
            if standing > _ZERO:
                adopted = min(standing, qty)
                unowned[str(item.warehouse_id)] = standing - adopted
                qty -= adopted
                if qty <= _ZERO:
                    continue
            location = self._warehouse_of(fact, str(item.warehouse_id)) or by_id.get(
                str(item.warehouse_id)
            )
            if location and location == fact.own_code:
                # The own location is a GROUP rung source again under v3, but it keeps its
                # own `source_type`: every reader that asks "is this stock already where it
                # has to be" reads this column, and a transfer is exactly what an own-
                # location reserve does NOT need.
                source = ALLOC_SOURCE_OWN
            elif location and location in siblings:
                source = ALLOC_SOURCE_GROUP_TAKE
            elif fact.pool_code and location == fact.pool_code:
                source = ALLOC_SOURCE_BRW
            elif location and location != fact.own_code:
                # Any other active site pool, the second half of ladder v3's rung 3:
                # the same Reserve kind as this line's own pool, just a different one.
                source = ALLOC_SOURCE_BRW
            else:
                source = ALLOC_SOURCE_OWN
            self.db.add(
                SOLineAllocation(
                    company_id=line.company_id,
                    so_line_id=line.id,
                    source_type=source,
                    warehouse_id=str(item.warehouse_id),
                    qty=qty,
                    decision_id=decision.id,
                    confirmed_by=actor_user_id,
                    confirmed_at=now,
                )
            )

        for item in entry.borrow or []:
            qty = _dec(item.qty)
            if qty <= _ZERO:
                continue
            if getattr(item, "supply_key", None):
                # LADDER V7.1 STEP 3 WRITES NO ALLOCATION. An `so_line_allocations` row is a
                # hold on STOCK at a bin, and every reader of free stock nets it out of the
                # floor - so writing one for a container still at sea would take units off a
                # shelf that never had them. The hold this component writes is the PLACEMENT
                # LINK (`_place_supply_borrows`), which `assign()` reads as pinned supply on
                # the document itself (PLAN 3.3).
                continue
            claim_id = None
            donor_project_id = None
            if item.source == ALLOC_SOURCE_OTHER_PROJECT and item.donor_project_id:
                donor_project_id = str(item.donor_project_id)
                claim = AllocationClaim(
                    company_id=line.company_id,
                    from_project_id=self._project_id_of(line),
                    to_project_id=donor_project_id,
                    so_line_id=line.id,
                    product_id=fact.product_id,
                    warehouse_id=str(item.warehouse_id),
                    qty=qty,
                    # Straight to the terminal state (AC-B10): the confirming CS actor IS
                    # the approval, so there is no requested step for a donor to answer.
                    state=CLAIM_ACCEPTED,
                    reason=(item.reason or "").strip(),
                    requested_by=actor_user_id,
                    decided_by=actor_user_id,
                    decided_at=now,
                )
                self.db.add(claim)
                self.db.flush()
                claim_id = claim.id
            self.db.add(
                SOLineAllocation(
                    company_id=line.company_id,
                    so_line_id=line.id,
                    source_type=item.source,
                    warehouse_id=str(item.warehouse_id),
                    source_project_id=donor_project_id,
                    qty=qty,
                    claim_id=claim_id,
                    decision_id=decision.id,
                    reason=(item.reason or "").strip(),
                    donor_impact_snapshot=self._donor_impact(item, fact, qty),
                    confirmed_by=actor_user_id,
                    confirmed_at=now,
                )
            )

        buy = _dec(entry.buy_qty)
        if buy > _ZERO:
            self.db.add(
                SOLineAllocation(
                    company_id=line.company_id,
                    so_line_id=line.id,
                    source_type=ALLOC_SOURCE_ORDER,
                    warehouse_id=None,
                    qty=buy,
                    decision_id=decision.id,
                    reason=(entry.buy_reason or "").strip() or None,
                    confirmed_by=actor_user_id,
                    confirmed_at=now,
                )
            )

    def _unowned_holds(self, line: ProjectSalesOrderLine) -> Dict[str, Decimal]:
        """What this line already holds under NO revision, by warehouse.

        A reallocation writes one (`planning_change_service._move_reserve`): a reserve
        another order gave this line, deliberately tied to no decision so that superseding
        one cannot make it read gone. Every later revision that names the same reserve is
        naming THAT hold. Scoped to the reallocation's own `reason` prefix rather than to
        `decision_id IS NULL` alone - `_hold_query`'s docstring is explicit that NULL also
        covers every row written before Stage 1C, a much wider set this method has no
        business summing, and a future backfill of THOSE rows must not silently change what
        every confirm writes here.
        """
        rows = (
            self.db.query(SOLineAllocation.warehouse_id, SOLineAllocation.qty)
            .filter(
                SOLineAllocation.so_line_id == line.id,
                SOLineAllocation.decision_id.is_(None),
                SOLineAllocation.confirmed_at.isnot(None),
                SOLineAllocation.warehouse_id.isnot(None),
                SOLineAllocation.source_type != ALLOC_SOURCE_ORDER,
                SOLineAllocation.reason.like("Reallocated from %"),
            )
            .all()
        )
        held: Dict[str, Decimal] = {}
        for warehouse_id, qty in rows:
            key = str(warehouse_id)
            held[key] = held.get(key, _ZERO) + _dec(qty)
        return held

    def _carry_allocations(
        self,
        decision: SOSupplyDecision,
        previous: Optional[SOSupplyDecision],
        line: ProjectSalesOrderLine,
    ) -> None:
        """A carried line's components, copied from the superseded revision row for row.

        Copied rather than re-derived: a hold is an allocation row under an ACTIVE decision
        (`_hold_rows`), and the superseded revision's rows stop holding the moment it is
        superseded, so the new revision needs its own. The copy keeps the same warehouse,
        quantity, source, claim and donor snapshot - the claim was accepted once and is not
        made again, and the donor's position is the one the decision was taken against, not
        today's. `confirmed_by` / `confirmed_at` stay the original's, because that is when
        and by whom the line was decided; this revision only carries it.
        """
        if previous is None:
            return
        rows = (
            self.db.query(SOLineAllocation)
            .filter(
                SOLineAllocation.decision_id == previous.id,
                SOLineAllocation.so_line_id == line.id,
            )
            .all()
        )
        for row in rows:
            self.db.add(
                SOLineAllocation(
                    company_id=row.company_id,
                    so_line_id=row.so_line_id,
                    source_type=row.source_type,
                    warehouse_id=row.warehouse_id,
                    source_project_id=row.source_project_id,
                    qty=row.qty,
                    claim_id=row.claim_id,
                    decision_id=decision.id,
                    reason=row.reason,
                    donor_impact_snapshot=row.donor_impact_snapshot,
                    confirmed_by=row.confirmed_by,
                    confirmed_at=row.confirmed_at,
                )
            )

    def _project_id_of(self, line: ProjectSalesOrderLine) -> Optional[str]:
        """The project the line's order belongs to, or None for an adopted order.

        None rather than `str(None)`: this feeds `AllocationClaim.from_project_id`, which
        is a NOT NULL uuid column, so a stringified null would have reached Postgres as the
        text "None" and failed the write of an otherwise valid confirmation. The confirm
        recheck refuses a cross-project Borrow before it gets here (`_check_borrow`).
        """
        order = (
            self.db.query(ProjectSalesOrder.project_id)
            .filter(ProjectSalesOrder.id == line.project_sales_order_id)
            .first()
        )
        return str(order[0]) if order and order[0] else None

    def _donor_impact(self, item: Any, fact: _LineFacts, qty: Decimal) -> Dict[str, Any]:
        free = self._free_at(fact.product_id, str(item.warehouse_id))
        held = (
            self._held_at(fact.product_id, str(item.warehouse_id), str(item.donor_project_id))
            if item.donor_project_id
            else _ZERO
        )
        return {
            "free_before": qty_text(free + held),
            "free_after_full_borrow": qty_text(max(free + held - qty, _ZERO)),
            "committed_qty": qty_text(held),
            # S1: the only place a group borrow's donor CORE line is recorded on the
            # written allocation row (there is no dedicated column for it). Read back by
            # `_group_borrow_held_qty` so a SECOND confirmation - of this order or another
            # one - sees what an earlier one already took from the same donor line.
            "donor_core_line_id": getattr(item, "donor_core_line_id", None),
        }

    def _group_borrow_held_qty(
        self, donor_core_line_id: str, *, exclude_line_ids: Optional[Set[str]] = None
    ) -> Decimal:
        """What is currently held FROM one donor sales-order line by an ACTIVE group
        borrow, written by any confirmation (S1).

        Seeds `_donor_line_ledger`: without this, `_check_group_borrow` only guarded
        against two lines of the SAME confirmation both taking the whole of a donor's open
        quantity - a SECOND, separate confirmation (this order's next revision, or a
        different project SO entirely) read the donor's live open quantity fresh and could
        borrow the same units again. Read off `donor_impact_snapshot`, the one place a
        group-borrow allocation records its donor (`_donor_impact`); a row written before
        this key existed is simply not found, the same as any other additive JSON field -
        it goes on not being netted rather than raising.

        `exclude_line_ids` (PROJECT line ids) leaves out this confirmation's OWN lines
        being replaced: their previous hold on this donor is about to be superseded by
        whatever this same confirmation asks for now, and un-netting it here mirrors the
        same carve-out `_free_stock`/`_hold_query` already give a line being replaced.
        """
        query = (
            self.db.query(func.coalesce(func.sum(SOLineAllocation.qty), 0))
            .join(
                ProjectSalesOrderLine,
                ProjectSalesOrderLine.id == SOLineAllocation.so_line_id,
            )
            .outerjoin(
                SOSupplyDecision, SOSupplyDecision.id == SOLineAllocation.decision_id
            )
            .filter(
                SOLineAllocation.source_type == ALLOC_SOURCE_OTHER_LOCATION,
                SOLineAllocation.donor_impact_snapshot["donor_core_line_id"].astext
                == donor_core_line_id,
                SOLineAllocation.confirmed_at.isnot(None),
                or_(
                    SOLineAllocation.decision_id.is_(None),
                    SOSupplyDecision.state == DECISION_ACTIVE,
                ),
            )
        )
        if exclude_line_ids:
            query = query.filter(
                SOLineAllocation.so_line_id.notin_(list(exclude_line_ids))
            )
        return _dec(query.scalar())

    def _restamp_stock_location(
        self, line: ProjectSalesOrderLine, entry: Any, fact: _LineFacts
    ) -> None:
        """AC-H5: the inquiry row names ONE location, always - the line's OWN fulfilment
        warehouse.

        The ORDER row is the buy purchasing places; its destination is the fulfilment
        location, not a description of every reserve/borrow component that ALSO covered
        this line's demand. A joined string of every component's warehouse (previously
        `" + "`-joined here) is not a real location, so it could never match a
        borrow-shortfall row netted by `(item_code, stock_location)`, and it read as one
        row naming two places. The composition itself is still on the confirmed
        decision's snapshots (`_snapshot`) for anyone who needs it; this field is
        purely where the Buy goes.
        """
        line.stock_location = fact.own_code or None

    # ------------------------------------------------------------------- facts

    def _facts_for(
        self,
        order: ProjectSalesOrder,
        lines: Sequence[ProjectSalesOrderLine],
        *,
        replacing: Optional[Set[str]] = None,
        refuse_unmapped: Optional[Set[str]] = None,
    ) -> Dict[str, _LineFacts]:
        """Read every fact the sheet and the commit judge a line against.

        A line with no reconciled core line has no current open quantity, and promising
        supply against the ORIGINAL customer quantity is exactly the double-count this
        contract exists to stop (PLAN 3.1 step 2). Such a line is refused ONLY when
        `refuse_unmapped` names it - the confirmation passes the lines its payload names,
        so one unreconciled sibling cannot stop the lines that are ready. Every other
        unmapped line is read as unplannable (`unplannable_reason`, open quantity 0, no
        location), which is how the sheet still reads while it waits for reconciliation.

        `replacing` names the project lines this composition is about to REPLACE - the ones
        the confirmation payload names. Their holds are un-netted and their demand stays in
        the queue; every other line of the order is read as covered-elsewhere (hold netted,
        out of the queue), which is what the union-is-the-server's carry-forward makes true
        of it. `None` means every line (the sheet, which proposes for all of them).
        """
        unmapped = [
            line
            for line in lines
            if not line.core_sales_order_line_id
            and refuse_unmapped is not None
            and str(line.id) in refuse_unmapped
        ]
        core_ids = [
            str(line.core_sales_order_line_id)
            for line in lines
            if line.core_sales_order_line_id
        ]
        cores = {
            str(row.id): row
            for row in (
                self.db.query(SalesOrderLine)
                .filter(SalesOrderLine.id.in_(core_ids))
                .all()
                if core_ids
                else []
            )
        }
        product_ids = {
            str(core.product_id)
            for core in cores.values()
            if core.product_id
        } | {str(line.product_id) for line in lines if line.product_id}
        codes = self._product_codes(product_ids)

        if unmapped:
            raise SupplyLinesRefused(
                status_code=422,
                message=(
                    f"{len(unmapped)} line{'' if len(unmapped) == 1 else 's'} "
                    "still has no AutoCount line. Reconcile the sales order first."
                ),
                failing_lines=[
                    {
                        "line_no": line.line_no,
                        "item_code": codes.get(str(line.product_id or "")),
                        "reason": "No reconciled AutoCount line.",
                    }
                    for line in unmapped
                ],
                code="supply_lines_unreconciled",
            )

        warehouse_ids = {
            str(core.warehouse_id) for core in cores.values() if core.warehouse_id
        }
        warehouses = self._warehouses(warehouse_ids)
        pool_ids = {
            str(w.pool_warehouse_id) for w in warehouses.values() if w.pool_warehouse_id
        }
        warehouses.update(self._warehouses(pool_ids - set(warehouses)))

        # The lines being REPLACED by this composition: their holds are un-netted (a previous
        # revision must not compete with the one replacing it) and their demand stays in the
        # queue. Every other line of the order - a covered line the confirmation carries
        # forward verbatim - is treated exactly as another order's covered line: its hold is
        # netted and it is out of the queue. The board reads it that way (`demand_facts`), and
        # a confirm that un-netted the WHOLE order's holds handed a named line stock its own
        # carried sibling was still holding (`test_fulfilment_board`).
        replaced = [
            str(line.id)
            for line in lines
            if replacing is None or str(line.id) in replacing
        ]
        # S1/S2: the lines this call is reading/replacing right now, for
        # `_decided_elsewhere_cached` and `_group_borrow_held_qty`; and the CORE lines
        # among them, so `_group_borrow_donors` never offers a line that is itself part
        # of the current confirmation as a donor to another line of it.
        self._replaced_line_ids = set(replaced)
        self._current_selection_core_ids = {
            str(line.core_sales_order_line_id)
            for line in lines
            if line.core_sales_order_line_id and str(line.id) in self._replaced_line_ids
        }
        self._decided_elsewhere_cache = None
        self._decided_anywhere_cache = None
        self._active_decision_rows = None
        self._request_product_ids = {str(pid) for pid in product_ids if pid}
        self._free_cache = self._drawable_free_stock(
            product_ids, exclude_line_ids=replaced
        )
        self._holds_cache = self._holds_by_project(product_ids, exclude_line_ids=replaced)
        self._pile_cache = None
        self._po_open_cache = None
        self._netting_cache = None
        self._spo_by_location_cache = None
        # Eager, not lazy: a project hot-selling line's pool draw is capped against the
        # pool's signed availability on every read, not only when a donor list is asked for.
        pool_piles = self._pile_facts()
        # The core sales orders behind the lines, for the SAME factor values the board hands
        # `pool_claims` for a member: document date, demand class, payment terms, and the
        # sales-order number the tie-break sorts on. Passing None for them scored the asking
        # line on need-by date alone against a pool book scored on every factor, so the confirm
        # ranked it lower than the board had, claimed more of the pool ahead of it, and
        # refused the very Reserve the board proposed (SO403765 line 8, live: "BRW has nothing
        # free for this line now" against a board reading "3 claimed ahead, 4 left").
        core_orders = {
            str(row.id): row
            for row in (
                self.db.query(SalesOrder)
                .filter(
                    SalesOrder.id.in_(
                        list({str(core.sales_order_id) for core in cores.values()})
                    )
                )
                .all()
                if cores
                else []
            )
        }
        terms = priority.payment_terms_by_customer(
            self.db,
            [str(row.customer_id) for row in core_orders.values() if row.customer_id],
        )

        members: List[Dict[str, Any]] = []
        for line in lines:
            core = cores.get(str(line.core_sales_order_line_id or ""))
            if core is None or not core.warehouse_id:
                continue
            warehouse = warehouses.get(str(core.warehouse_id))
            if warehouse is None or not warehouse.pool_warehouse_id:
                continue
            core_order = core_orders.get(str(core.sales_order_id))
            members.append(
                {
                    "key": str(line.id),
                    "product_id": str(core.product_id or line.product_id),
                    "pool_id": str(warehouse.pool_warehouse_id),
                    "required_date": core.required_date or line.delivery_date,
                    "order_date": core_order.order_date if core_order else None,
                    "payment_terms_days": (
                        terms.get(str(core_order.customer_id or "")) if core_order else None
                    ),
                    "demand_class": core_order.demand_class if core_order else None,
                    "so_number": (
                        (core_order.so_number if core_order else None)
                        or order.provisional_ref
                    ),
                    "line_no": line.line_no,
                    "line_id": str(core.id),
                }
            )
        pool_ahead = self.pool_claims(
            product_ids, pool_ids, exclude_line_ids=replaced, members=members
        )
        (
            dealer_hot, project_hot, unavailable, dealer_where, project_where,
            dealer_classified_set, project_classified_set,
        ) = self._classification(product_ids)
        levels = self._reorder_levels(product_ids, pool_ids)
        discontinued = self._discontinued(product_ids)
        spo = self._spo_by_location()
        attribution = self._attribution(
            product_ids,
            warehouse_ids,
            warehouses,
            spo,
            exclude_line_ids=replaced,
            # The sheet prints the share and never the names behind it, so it asks for
            # nobody's queue: describing every line of every pile is quadratic work on a
            # crowded location, paid on every sheet read and every confirm, for nothing.
            detail_for=set(),
        )

        # Core lines an ACTIVE decision covers, the ones being replaced INCLUDED - read
        # once for the whole call. See `unplannable_reason` below for why this reading and
        # not `_decided_elsewhere_cached`'s.
        decided_anywhere = self._decided_anywhere_cached()

        facts: Dict[str, _LineFacts] = {}
        for line in lines:
            core = cores.get(str(line.core_sales_order_line_id or ""))
            product_id = str(core.product_id) if core and core.product_id else (
                str(line.product_id) if line.product_id else None
            )
            warehouse = (
                warehouses.get(str(core.warehouse_id))
                if core and core.warehouse_id
                else None
            )
            pool = (
                warehouses.get(str(warehouse.pool_warehouse_id))
                if warehouse and warehouse.pool_warehouse_id
                else None
            )
            key = (product_id or "", str(warehouse.id) if warehouse else "")
            shares = attribution.get(key, {}).get(str(core.id) if core else "", {})
            ahead = _dec(shares.get("so_qty_ahead"))
            required_date = (core.required_date if core else None) or line.delivery_date
            timely_refs = [
                row
                for row in spo.get(key, [])
                if required_date is None
                or (row.arrival_date is not None and row.arrival_date <= required_date)
            ]
            claim = pool_ahead.get(str(line.id), {})
            claimed = _dec(claim.get("qty"))
            pool_triple = pool_piles.get((product_id, str(pool.id))) if pool and product_id else None
            facts[str(line.id)] = _LineFacts(
                line=line,
                core=core,
                line_no=line.line_no,
                item_code=codes.get(product_id or ""),
                product_id=product_id,
                open_qty=plan_qty_of(core),
                owed_qty=_open_of(core),
                required_date=required_date,
                warehouse=warehouse,
                pool=pool,
                own_free=shares.get(RESERVE, _ZERO),
                so_qty_ahead=ahead,
                lines_ahead=int(shares.get("lines_ahead") or 0),
                available_to_this_line=_dec(shares.get("available_to_this_line")),
                pool_free=(
                    max(self._free_at(product_id, str(pool.id)) - claimed, _ZERO)
                    if pool
                    else _ZERO
                ),
                pool_claimed_qty=claimed if pool else _ZERO,
                pool_claimed_lines=int(claim.get("lines") or 0) if pool else 0,
                pool_reorder_level=levels.get(
                    (product_id or "", str(pool.id) if pool else ""), _ZERO
                ),
                is_dealer_hot_selling=(product_id or "") in dealer_hot,
                dealer_hot_selling_where=list(dealer_where.get(product_id or "", [])),
                is_project_hot_selling=(product_id or "") in project_hot,
                project_hot_selling_where=list(project_where.get(product_id or "", [])),
                dealer_classified=(product_id or "") in dealer_classified_set,
                project_classified=(product_id or "") in project_classified_set,
                pool_available=(
                    pool_triple["on_hand"] - pool_triple["so_qty"] + pool_triple["spo_qty"]
                    if pool_triple
                    else _ZERO
                ),
                classification_unavailable=(product_id or "") in unavailable,
                is_discontinued=(product_id or "") in discontinued,
                timely_qty=shares.get(TIMELY_SPO, _ZERO),
                timely_refs=timely_refs,
                advisory_refs=[
                    row for row in spo.get(key, []) if row not in timely_refs
                ],
                unplannable_reason=(
                    "No reconciled AutoCount line. Reconcile the sales order first."
                    if core is None
                    # R17 / AC-S1-6: the line's own bin is ACTIVE and flagged out of
                    # fulfilment planning, so there is no pile to walk, no group to net it
                    # against and no donor to ask. Stated as the verdict rather than
                    # proposed as a Buy, which is what a location the engine cannot see
                    # would otherwise become. An INACTIVE bin is NOT this case
                    # (`outside_fulfilment_planning`): it was already outside every read
                    # before the flag existed and keeps the verdict it carried then.
                    #
                    # And a line an ACTIVE decision covers is NOT this case either. The
                    # stock was found, promised and confirmed; turning the switch off
                    # afterwards says what may be PROPOSED next, it does not retract what
                    # was decided. Read through `_decided_anywhere_cached`, so a verbatim
                    # re-send - where the line being re-confirmed is the one whose decision
                    # must count - is accepted rather than refused with the verdict the
                    # board itself no longer shows.
                    else OUTSIDE_FULFILMENT_PLANNING
                    if (
                        outside_fulfilment_planning(warehouse)
                        and str(core.id) not in decided_anywhere
                    )
                    else None
                ),
                group_code=sales_agent_service.group_of_warehouse_code(
                    warehouse.warehouse_code if warehouse else None
                ),
            )
            self._apply_group_nets(facts[str(line.id)])
        return facts

    # -------------------------------------------------------------- fact readers

    def _product_codes(self, product_ids: Iterable[str]) -> Dict[str, str]:
        ids = [pid for pid in product_ids if pid]
        if not ids:
            return {}
        return {
            str(row[0]): row[1] or ""
            for row in self.db.query(Product.id, Product.product_code)
            .filter(Product.id.in_(ids))
            .all()
        }

    def _discontinued(self, product_ids: Iterable[str]) -> set:
        ids = [pid for pid in product_ids if pid]
        if not ids:
            return set()
        return {
            str(row[0])
            for row in self.db.query(Product.id)
            .filter(Product.id.in_(ids), Product.is_discontinued.is_(True))
            .all()
        }

    def _warehouses(self, warehouse_ids: Iterable[str]) -> Dict[str, Warehouse]:
        """Warehouse rows by id, read once per request and remembered (an id that matches
        no row is remembered too, so it is not asked for again)."""
        ids = {str(wid) for wid in warehouse_ids if wid}
        missing = [wid for wid in ids if wid not in self._warehouse_memo]
        if missing:
            for wid in missing:
                self._warehouse_memo[wid] = None
            for row in self.db.query(Warehouse).filter(Warehouse.id.in_(missing)).all():
                self._warehouse_memo[str(row.id)] = row
        return {
            wid: self._warehouse_memo[wid]
            for wid in ids
            if self._warehouse_memo.get(wid) is not None
        }

    def _warehouse_row(self, warehouse_id: str) -> Optional[Warehouse]:
        return self._warehouses([warehouse_id]).get(str(warehouse_id)) if warehouse_id else None

    def _projects(self, project_ids: Iterable[str]) -> Dict[str, Project]:
        """Project rows by id, the same way `_warehouses` remembers warehouses."""
        ids = {str(pid) for pid in project_ids if pid}
        missing = [pid for pid in ids if pid not in self._project_memo]
        if missing:
            for pid in missing:
                self._project_memo[pid] = None
            for row in self.db.query(Project).filter(Project.id.in_(missing)).all():
                self._project_memo[str(row.id)] = row
        return {
            pid: self._project_memo[pid]
            for pid in ids
            if self._project_memo.get(pid) is not None
        }

    def _by_product(
        self,
    ) -> Tuple[
        Dict[str, Dict[str, Decimal]], Dict[str, Dict[Tuple[str, str], Decimal]]
    ]:
        """The free and holds caches, indexed by product.

        `free[product_id][warehouse_id]` and `holds[product_id][(warehouse_id,
        project_id)]`, over the SAME cache dicts `_free_at` / `_held_at` read - rebuilt
        only when `_facts_for` or `demand_facts` replaces them, which is the only way
        they change. A donor list walks one product's rows, not the whole request's.
        """
        view = self._indexed
        if (
            view is None
            or view[0] is not self._free_cache
            or view[1] is not self._holds_cache
        ):
            free: Dict[str, Dict[str, Decimal]] = {}
            for (product_id, warehouse_id), qty in self._free_cache.items():
                free.setdefault(product_id, {})[warehouse_id] = qty
            holds: Dict[str, Dict[Tuple[str, str], Decimal]] = {}
            for (product_id, warehouse_id, project_id), qty in self._holds_cache.items():
                holds.setdefault(product_id, {})[(warehouse_id, project_id)] = qty
            view = self._indexed = (self._free_cache, self._holds_cache, free, holds)
        return view[2], view[3]

    def held_stock_by_location(
        self, product_ids: Iterable[str]
    ) -> Dict[Tuple[str, str], Decimal]:
        """What CONFIRMED decisions are holding, per `(product_id, warehouse_id)`.

        The third term of the free-stock arithmetic, exposed so a screen can print it: on hand,
        less reserved, less THIS, is what `free_stock_by_location` answers. Without it a strip
        showing 478 on hand and 478 free looks like a rounding error rather than the truth that
        nothing is committed yet.
        """
        ids = [pid for pid in product_ids if pid]
        if not ids:
            return {}
        out: Dict[Tuple[str, str], Decimal] = {}
        for key, _project_id, qty in self._hold_rows(ids, exclude_line_ids=None):
            out[key] = out.get(key, _ZERO) + qty
        return out

    def holds_at_locations(
        self, product_ids: Iterable[str], warehouse_ids: Iterable[str]
    ) -> List[Dict[str, Any]]:
        """Confirmed holds at these bins, WITH the order holding them and its date.

        `held_stock_by_location` states the QUANTITY; this states WHO. The group stock drill
        needs the second, because a hold whose own line is booked in another group takes
        stock out of this group's pile and appears nowhere in its book: no sales-order row
        of this group names it, so a running balance without it walks a pile bigger than the
        one a planner can actually draw on. 25 of the 35 confirmed holds on the 30 August dev
        copy are cross-group, so this is the ordinary case rather than an edge.

        The SAME predicate `held_stock_by_location` uses (`_hold_query`), so the rows a
        screen lists and the quantity the arithmetic nets can never disagree.
        """
        ids = [str(pid) for pid in product_ids if pid]
        bins = [str(wid) for wid in warehouse_ids if wid]
        if not ids or not bins:
            return []
        rows = (
            self._hold_query(
                ids,
                exclude_line_ids=None,
                entities=(
                    SOLineAllocation.warehouse_id,
                    held_qty_expr(),
                    SalesOrder.so_number,
                    SalesOrderLine.required_date,
                    SalesOrderLine.warehouse_id,
                ),
            )
            .filter(SOLineAllocation.warehouse_id.in_(bins))
            .all()
        )
        return [
            {
                "warehouse_id": str(row[0]),
                "qty": _dec(row[1]),
                "so_number": row[2],
                "required_date": row[3],
                "line_warehouse_id": str(row[4]) if row[4] else None,
            }
            for row in rows
        ]

    def stock_levels_by_location(
        self, product_ids: Iterable[str]
    ) -> Dict[Tuple[str, str], Tuple[Decimal, Decimal]]:
        """`(on hand, reserved)` per `(product_id, warehouse_id)`, straight off the rows.

        The raw pair behind `free_stock_by_location`, read from the same `stock` rows so a
        screen can show what the planner asked to see - "what is the quantity on hand" - beside
        what the engine may actually use, and the two can never come from different reads. The
        subtraction stays in `_free_stock`; this states its inputs, it does not repeat them.
        """
        ids = [pid for pid in product_ids if pid]
        if not ids:
            return {}
        rows = (
            self.db.query(Stock)
            .join(Warehouse, Warehouse.id == Stock.warehouse_id)
            .filter(Stock.product_id.in_(ids), Warehouse.is_active.is_(True))
            .all()
        )
        return {
            (str(stock.product_id), str(stock.warehouse_id)): (
                _dec(stock.quantity_on_hand),
                _dec(stock.quantity_reserved),
            )
            for stock in rows
        }

    def free_stock_by_location(
        self, product_ids: Iterable[str]
    ) -> Dict[Tuple[str, str], Decimal]:
        """Free stock per `(product_id, warehouse_id)`, as THIS service computes it.

        The public seam for readers outside one order's sheet - today the multi-order planning
        board. It exists so the board asks this service what is free rather than growing a
        second opinion about availability, which would be the same defect two rankings would
        have been (`app/services/scm/priority.py`).

        No line is excluded, because a cross-order reader is not replacing any one line.

        EVERY ACTIVE location, flagged into fulfilment planning or not (R17): this states
        what a location HOLDS, beside the on hand and held figures its two siblings state,
        and a bin nobody plans against still holds its stock. What a PROPOSAL may draw is
        the narrower `_drawable_free_stock`.

        It carries `_free_stock`'s known limit, and the board is built to SHOW that limit
        rather than hide it (PLAN 13.5.1): only CONFIRMED holds are netted off, so two orders
        composed separately can both still be proposed the same stock. The board answers that
        by serving one pile down the ranking and marking the rows it could not cover as
        contested. The locking fix belongs to the confirmation path.
        """
        return self._free_stock(product_ids, exclude_line_ids=None)

    def _free_stock(
        self, product_ids: Iterable[str], *, exclude_line_ids: Optional[Sequence[str]]
    ) -> Dict[Tuple[str, str], Decimal]:
        """On hand, minus reserved, minus what confirmed decisions already hold.

        A hold counts when its allocation belongs to no decision (every row written before
        Stage 1C) or to an ACTIVE one. A superseded revision's rows are history and hold
        nothing, and the lines being replaced (`exclude_line_ids`, project line ids) are
        excluded so their own previous revision does not compete with the one replacing it.

        EVERY ACTIVE location, fulfilment-planning flag or not. This is the arithmetic
        `stock_levels_by_location` and `held_stock_by_location` state the two other terms
        of, and a screen printing 1,928 on hand beside 0 free reads as a defect rather than
        as a policy. What the LADDER may draw is a narrower question, and its own
        (`_drawable_free_stock`, R17).
        """
        ids = [pid for pid in product_ids if pid]
        if not ids:
            return {}
        rows = (
            self.db.query(Stock, Warehouse)
            .join(Warehouse, Warehouse.id == Stock.warehouse_id)
            .filter(Stock.product_id.in_(ids), Warehouse.is_active.is_(True))
            .all()
        )
        free = {
            (str(stock.product_id), str(stock.warehouse_id)): max(
                _dec(stock.quantity_on_hand) - _dec(stock.quantity_reserved), _ZERO
            )
            for stock, _warehouse in rows
        }
        for (product_id, warehouse_id), _project_id, qty in self._hold_rows(
            ids, exclude_line_ids=exclude_line_ids
        ):
            key = (product_id, warehouse_id)
            if key in free:
                free[key] = max(free[key] - qty, _ZERO)
        return free

    def _drawable_free_stock(
        self, product_ids: Iterable[str], *, exclude_line_ids: Optional[Sequence[str]]
    ) -> Dict[Tuple[str, str], Decimal]:
        """`_free_stock`, narrowed to the locations a PROPOSAL may draw from (R17).

        The fulfilment-planning bins plus the site pools: a bin flagged out of planning has
        no pile, no group net and no donor, so no rung, no manual donor list and no
        confirm-time re-read may take a unit from it. The pools are in because the pool rung
        draws them and they are flagged off by design.

        Applied HERE, on the ladder's own fact caches (`demand_facts` and `_facts_for` are
        the only two writers of `_free_cache`), rather than inside `_free_stock` itself:
        the public `free_stock_by_location` seam is also what the board's stock detail and
        the location-stock screen print, and those state what a location HOLDS, which does
        not change because nobody plans against it.
        """
        free = self._free_stock(product_ids, exclude_line_ids=exclude_line_ids)
        drawable = set(self._planning_warehouses()) | set(self._site_pool_warehouses())
        return {
            key: qty for key, qty in free.items() if key[1] in drawable
        }

    def _hold_rows(
        self, product_ids: Sequence[str], *, exclude_line_ids: Optional[Sequence[str]]
    ) -> List[Tuple[Tuple[str, str], str, Decimal]]:
        """Confirmed holds, per (product, warehouse) and holding project.

        Ported from `project_allocation_service._holds` and narrowed by decision state,
        because a superseded revision must stop holding stock the moment it is superseded.
        """
        query = self._hold_query(product_ids, exclude_line_ids=exclude_line_ids)
        # An ADOPTED order holds stock with no project behind it (section 4), and the
        # holding project is a dict KEY here - so it is the empty string rather than the
        # string "None", which is a value that looks like an id and matches nothing. The
        # hold still nets out of free stock either way; what it cannot be is a cross-project
        # Borrow donor, because there is no project to borrow FROM.
        return [
            ((str(product_id), str(warehouse_id)), str(project_id) if project_id else "",
             _dec(qty))
            for product_id, warehouse_id, project_id, qty in query.all()
        ]

    def _hold_query(
        self,
        product_ids: Sequence[str],
        *,
        exclude_line_ids: Optional[Sequence[str]],
        entities: Optional[Sequence[Any]] = None,
    ):
        """The one predicate for "this allocation row is holding stock right now".

        A hold counts when its allocation belongs to no decision (every row written before
        Stage 1C) or to an ACTIVE one; the LINES being replaced (`exclude_line_ids`, project
        line ids) are excluded so their own previous revision does not compete with the one
        replacing it. By line and not by order, because a covered line the confirmation
        carries forward keeps its hold - the union is the server's - and un-netting it too
        offered a named sibling stock the order was still holding. Shared by the free-stock
        arithmetic and the donor-shortfall netting so the two cannot come to disagree about
        what is held.

        The hold is keyed by the CORE line's product when the mirror line has one, and by
        the mirror's own product only when it does not (`_hold_product`): free stock is
        read against the core product (`_facts_for` prefers it on a remap), and a hold
        keyed by the mirror's product would net out of the wrong pile.

        `entities` swaps WHAT is selected and nothing else, so the confirm-time guard can
        ask the same question and get the holder's name back with it (R14) without a second
        opinion about which rows are holding.
        """
        query = (
            self.db.query(
                *(
                    entities
                    or (
                        _hold_product,
                        SOLineAllocation.warehouse_id,
                        ProjectSalesOrder.project_id,
                        held_qty_expr(),
                    )
                )
            )
            .select_from(SOLineAllocation)
            .join(
                ProjectSalesOrderLine,
                ProjectSalesOrderLine.id == SOLineAllocation.so_line_id,
            )
            .join(
                ProjectSalesOrder,
                ProjectSalesOrder.id == ProjectSalesOrderLine.project_sales_order_id,
            )
            .outerjoin(
                SalesOrderLine,
                SalesOrderLine.id == ProjectSalesOrderLine.core_sales_order_line_id,
            )
            .outerjoin(SalesOrder, SalesOrder.id == SalesOrderLine.sales_order_id)
            .outerjoin(
                SOSupplyDecision, SOSupplyDecision.id == SOLineAllocation.decision_id
            )
            .filter(
                _hold_product.in_(list(product_ids)),
                SOLineAllocation.confirmed_at.isnot(None),
                SOLineAllocation.warehouse_id.isnot(None),
                SOLineAllocation.source_type != ALLOC_SOURCE_ORDER,
                or_(
                    SOLineAllocation.decision_id.is_(None),
                    SOSupplyDecision.state == DECISION_ACTIVE,
                ),
            )
        )
        if exclude_line_ids:
            query = query.filter(
                SOLineAllocation.so_line_id.notin_(list(exclude_line_ids))
            )
        return query

    def _holds_by_project(
        self, product_ids: Iterable[str], *, exclude_line_ids: Optional[Sequence[str]]
    ) -> Dict[Tuple[str, str, str], Decimal]:
        out: Dict[Tuple[str, str, str], Decimal] = {}
        ids = [pid for pid in product_ids if pid]
        if not ids:
            return out
        for (product_id, warehouse_id), project_id, qty in self._hold_rows(
            ids, exclude_line_ids=exclude_line_ids
        ):
            key = (product_id, warehouse_id, project_id)
            out[key] = out.get(key, _ZERO) + qty
        return out

    def _free_at(self, product_id: Optional[str], warehouse_id: str) -> Decimal:
        return self._free_cache.get(
            (product_id or "", warehouse_id), _ZERO
        )

    def _held_at(
        self, product_id: Optional[str], warehouse_id: str, project_id: str
    ) -> Decimal:
        return self._holds_cache.get(
            (product_id or "", warehouse_id, project_id), _ZERO
        )

    def _classification(
        self, product_ids: Iterable[str]
    ) -> Tuple[set, set, set, Dict[str, List[str]], Dict[str, List[str]], set, set]:
        """PLAN 3.3's hot-selling predicate, per demand class, and the "no evidence" case.

        Amended 19 August 2026 (the captain): "you should do the demand over project classed
        demand to judge project hot selling, same for dealer" - hot-selling is read off the
        DEMAND CLASS the ABC figure was computed against (`abc_class_retail` for dealer,
        `abc_class_project` for project), not off the warehouse's own segment. The demand
        class already states who bought it, so a warehouse's segment plays no further part
        and `Warehouse.segment == DEALER_SEGMENT` is dropped. The ranking itself is BY
        QUANTITY delivered in that class, not by value ("hot selling is by quantity, not
        related to money") - unlike the reorder engine's own value-based `abc_class`, which
        this predicate does not read.

        `computed_at` is display evidence, never a freshness gate: the existing ABC facts
        are the test, and adding an age threshold would be a new knob this contract forbids.

        Returns the dealer-hot set, the project-hot set, the unclassified set, WHERE each hot
        verdict earned it - the locations holding it as ABC A by quantity on that demand
        class, by code and sorted - and finally the dealer-classified / project-classified
        sets: every product carrying a NON-NULL letter on that column ANYWHERE, hot or not.
        The captain, reading a trail that never mentioned it: "where is the consideration of
        dealer hot selling?" A bare boolean would be something to take on trust; "ABC A at
        BRW" is something to check. The classified sets are what a "Cold at retail" / "Cold
        at project" chip is told apart from "no evidence of that class at all" with - a
        product can carry a real, non-A letter and still not be hot.

        "Unclassified" means no NON-NULL letter in EITHER column, not merely "a row exists".
        A NULL letter means no DELIVERED demand of that class in the trailing-12mo window -
        "unknown", never a computed verdict of "not hot". Reading that as "seen, therefore
        cold" would print a false "Not dealer hot-selling" for an item nobody has actually
        judged, which is the whole book today: every row is NULL in both columns because
        nothing has been delivered against them yet.
        """
        ids = [pid for pid in product_ids if pid]
        if not ids:
            return set(), set(), set(), {}, {}, set(), set()
        rows = (
            self.db.query(
                ItemClassification.product_id,
                ItemClassification.abc_class_project,
                ItemClassification.abc_class_retail,
                Warehouse.warehouse_code,
            )
            .join(Warehouse, Warehouse.id == ItemClassification.warehouse_id)
            .filter(
                ItemClassification.product_id.in_(ids),
                Warehouse.is_active.is_(True),
                Warehouse.counts_as_available.is_(True),
            )
            .all()
        )
        dealer_classified: set = set()
        project_classified: set = set()
        dealer_where: Dict[str, List[str]] = {}
        project_where: Dict[str, List[str]] = {}
        for product_id, abc_project, abc_retail, code in rows:
            pid = str(product_id)
            if abc_retail is not None:
                dealer_classified.add(pid)
            if abc_project is not None:
                project_classified.add(pid)
            if (abc_retail or "").upper() == "A":
                dealer_where.setdefault(pid, []).append(code or "")
            if (abc_project or "").upper() == "A":
                project_where.setdefault(pid, []).append(code or "")
        for codes in dealer_where.values():
            codes.sort()
        for codes in project_where.values():
            codes.sort()
        seen = dealer_classified | project_classified
        return (
            set(dealer_where),
            set(project_where),
            {pid for pid in ids if pid not in seen},
            dealer_where,
            project_where,
            dealer_classified,
            project_classified,
        )

    def _reorder_levels(
        self, product_ids: Iterable[str], warehouse_ids: Iterable[str]
    ) -> Dict[Tuple[str, str], Decimal]:
        """Per-location levels. An absent row and a NULL level both contribute 0 (Q7)."""
        pids = [pid for pid in product_ids if pid]
        wids = [wid for wid in warehouse_ids if wid]
        if not pids or not wids:
            return {}
        return {
            (str(row.product_id), str(row.warehouse_id)): _dec(row.level)
            for row in self.db.query(ReorderLevel)
            .filter(
                ReorderLevel.product_id.in_(pids),
                ReorderLevel.warehouse_id.in_(wids),
            )
            .all()
        }

    def incoming_by_location(
        self, product_ids: Iterable[str], warehouse_ids: Iterable[str]
    ) -> Dict[Tuple[str, str], List[_SpoRow]]:
        """Undelivered SPO allocations per `(product_id, warehouse_id)`.

        The public seam beside `free_stock_by_location`, and there for the same reason: the
        board shares one location's opening stock AND its dated incoming through the same
        engine the sheet uses, so the two surfaces cannot come to differ about what is on the
        water.
        """
        return self._spo_rows(product_ids, warehouse_ids)

    def _spo_by_location(self) -> Dict[Tuple[str, str], List[_SpoRow]]:
        """Every open SPO row for this request's products, at EVERY active warehouse, once.

        The span is wide for the same reason `_pile_facts`' is (ladder v4): question 1 reads
        the whole ownership GROUP, and the water it may draw sits at the group's siblings as
        often as at the line's own location. A read narrowed to the demand rows' warehouses
        would leave the sibling's SPO with a quantity in the pile's net and no date beside
        it, and a draw with no date is exactly the promise this rule refuses to make.

        One query, cached for the span the fact builders reset together with the pile.
        """
        if self._spo_by_location_cache is None:
            self._spo_by_location_cache = self._spo_rows(
                self._request_product_ids,
                # Same span as `_pile_facts`, and for the same reason: the group's water
                # sits at the flagged siblings, the pool rung reads the pools' own.
                set(self._planning_warehouses()) | set(self._site_pool_warehouses()),
            )
        return self._spo_by_location_cache

    def _spo_rows(
        self, product_ids: Iterable[str], warehouse_ids: Iterable[str]
    ) -> Dict[Tuple[str, str], List[_SpoRow]]:
        """Undelivered SPO allocations at these locations, with their current ETA.

        `eta_delay_date` wins over `estimated_arrival_date` because the revised date is
        the accurate one, and a line promised against a date that has already slipped is
        the promise this whole contract is trying not to make. Where there is no shipment
        at all - a shipping order nobody has booked a container for, which is every SPO
        document since migration 420 - the SPO line's own `expected_date` stands in, and
        that is what rung 1 compares against the sales line's required date.
        """
        pids = [pid for pid in product_ids if pid]
        wids = [wid for wid in warehouse_ids if wid]
        if not pids or not wids:
            return {}
        # Two ways a row can name a factory: through its shipment, or on the SPO line
        # itself for a document nobody has booked a container for yet.
        DirectSupplier = aliased(Supplier)
        today = date.today()
        rows = (
            self.db.query(
                SPOAllocation.id,
                SPOAllocation.spo_number,
                SPOAllocation.spo_line_number,
                SPOAllocation.product_id,
                SPOAllocation.warehouse_id,
                SPOAllocation.allocated_quantity,
                SPOAllocation.quantity_received,
                SPOAllocation.expected_date,
                InboundShipment.eta_delay_date,
                InboundShipment.estimated_arrival_date,
                Supplier.supplier_name.label("shipment_supplier_name"),
                DirectSupplier.supplier_name.label("spo_supplier_name"),
            )
            # OUTER, since migration 420: an SPO document has no shipment until somebody
            # books a container for it, and an inner join dropped every one of them - which
            # is the whole of the incoming supply this ladder's rung 1 exists to offer.
            .outerjoin(
                InboundShipment, InboundShipment.id == SPOAllocation.inbound_shipment_id
            )
            .outerjoin(Supplier, Supplier.id == InboundShipment.supplier_id)
            .outerjoin(DirectSupplier, DirectSupplier.id == SPOAllocation.supplier_id)
            .filter(
                SPOAllocation.product_id.in_(pids),
                SPOAllocation.warehouse_id.in_(wids),
                # Closed lines, landed shipments and received rows, in one rule shared
                # with `on_order_v`, the order inquiry's inbound pool and the coverage
                # screen. The board's cell popover reads this same method, so what the
                # planner is shown and what the engine decides on cannot differ. A promise
                # whose date has passed is still supply and is stated as OVERDUE below.
                *spo_supply.open_incoming_clauses(),
            )
            .all()
        )
        out: Dict[Tuple[str, str], List[_SpoRow]] = {}
        for row in rows:
            # The quantity test, stated beside the arithmetic it belongs to: nothing left to
            # come is not supply, whatever the statuses say.
            balance = _dec(row.allocated_quantity) - _dec(row.quantity_received)
            if balance <= _ZERO:
                continue
            arrival = (
                row.eta_delay_date or row.estimated_arrival_date or row.expected_date
            )
            out.setdefault((str(row.product_id), str(row.warehouse_id)), []).append(
                _SpoRow(
                    spo_number=str(row.spo_number or ""),
                    spo_line_no=row.spo_line_number,
                    allocation_id=str(row.id),
                    arrival_date=arrival,
                    qty=balance,
                    overdue_days=spo_supply.overdue_days(arrival, today),
                    supplier_name=row.shipment_supplier_name or row.spo_supplier_name,
                    ordered_qty=_dec(row.allocated_quantity),
                    received_qty=_dec(row.quantity_received),
                )
            )
        return out

    def po_by_location(
        self, product_ids: Iterable[str], warehouse_ids: Iterable[str]
    ) -> Dict[Tuple[str, str], List[_PoRow]]:
        """Open PO supply per `(product_id, warehouse_id)` - the public seam over `_po_rows`.

        Beside `incoming_by_location` and there for the same reason: the Stock Debt view, the
        board's cell and (S4) rung 3 all have to read ON ORDER the same way, or the screen
        and the engine come to different views of what is still to be bought.
        """
        return self._po_rows(product_ids, warehouse_ids)

    def _po_rows(
        self, product_ids: Iterable[str], warehouse_ids: Iterable[str]
    ) -> Dict[Tuple[str, str], List[_PoRow]]:
        """Open purchase-order lines at these locations - what is STILL ON ORDER, dated.

        Four decisions, each of which reverses what the column names suggest:

        * **Still on order is `qty_ordered - qty_received`, and nothing else** (R11,
          AC-S2-4). The netting the AC asks for is real, but it is already IN that
          subtraction: both writers of `spo_allocations.po_line_id` advance the source
          line's `qty_received` by exactly what they placed, in the same action -
          `allocation_suggestion_service.approve` ("the quantity moves from Ordered to
          Incoming in ONE action", its AC-G6 comment) and `spo_conversion_service.create`
          ("the pull ADVANCES the source PO line's own accounting"). Subtracting the open
          allocation as well deducted the same placement twice, so a line of 100 with 40
          allocated reported 20 on order instead of 60. Measured on the 30 Aug dev copy:
          every one of the 9 `spo_allocations` rows carrying a `po_line_id` names a
          `crm_spo` line, and none of them has `qty_received >= allocated`.
        * **A CRM SPO document is not an open purchase order.** `spo_conversion_service`
          mints a `purchase_orders` header stamped `crm_spo` to carry a shipment's leg of an
          existing PO; `incoming_by_location` contributes it at its own arrival, off the
          `spo_allocations` row hanging on it. Reading it here as well promises the units
          twice, and once a GRN receives part of that allocation the double stops even
          cancelling: 50 ordered, 0 received, 20 still allocated left a phantom 30 on order
          for a document that had half landed.
        * **The arrival is computed, not read** (R29). `expected_date` on this book is the SO
          delivery date the buyer typed the line against, so the date the goods would land is
          `issue_date + the supplier's lead time`. The typed date travels as `bought_for`.
        * **The lead time is the SUPPLIER'S** when the agreement states one for this product
          and supplier, then the product's own fastest, then `DEFAULT_LEAD_TIME_DAYS`. The
          fallback is read through the BATCHED `lead_times()` for the whole page before the
          first row is walked, never through the scalar `_lead_time_days` per line: the
          Stock Debt list asks about a thousand products at once, and one round trip each
          cost ~1,900 extra queries per request on the dev copy.

        A PO with no issue date carries `arrival_date = None` and is returned anyway. The
        assignment refuses to count an undated document, and the drill still lists it - a
        document nobody mentions reads as a document that is not there.
        """
        pids = [pid for pid in product_ids if pid]
        wids = [wid for wid in warehouse_ids if wid]
        if not pids or not wids:
            return {}

        # The line's position inside its own document, in the order `PurchaseOrder.lines`
        # uses - so the number on screen is the number the document detail shows.
        numbered = (
            self.db.query(
                PurchaseOrderLine.id.label("line_id"),
                func.row_number()
                .over(
                    partition_by=PurchaseOrderLine.purchase_order_id,
                    order_by=(PurchaseOrderLine.created_at, PurchaseOrderLine.id),
                )
                .label("line_no"),
            )
            .subquery()
        )
        rows = (
            self.db.query(
                PurchaseOrderLine.id,
                PurchaseOrderLine.product_id,
                PurchaseOrderLine.warehouse_id,
                PurchaseOrderLine.qty_ordered,
                PurchaseOrderLine.qty_received,
                PurchaseOrderLine.expected_date,
                PurchaseOrder.po_number,
                PurchaseOrder.issue_date,
                PurchaseOrder.id.label("purchase_order_id"),
                Supplier.supplier_name,
                ProductSupplier.standard_lead_time_days.label("lead_days"),
                numbered.c.line_no,
            )
            .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderLine.purchase_order_id)
            .join(numbered, numbered.c.line_id == PurchaseOrderLine.id)
            .outerjoin(Supplier, Supplier.id == PurchaseOrder.supplier_id)
            .outerjoin(
                ProductSupplier,
                (ProductSupplier.product_id == PurchaseOrderLine.product_id)
                & (ProductSupplier.supplier_id == PurchaseOrder.supplier_id),
            )
            .filter(
                PurchaseOrderLine.product_id.in_(pids),
                PurchaseOrderLine.warehouse_id.in_(wids),
                # The same open-PO rule `scm.po_ordered_v` and the container request read
                # (`container_request_service.OPEN_PO_SQL`), so "on order" means one thing.
                PurchaseOrder.status.in_(OPEN_PO_STATUSES),
                PurchaseOrderLine.line_status == "open",
                PurchaseOrderLine.qty_ordered > PurchaseOrderLine.qty_received,
                # The shipping leg, not an order (see the docstring). Stamped on both the
                # header and its lines by `spo_conversion_service`; read off the HEADER,
                # because it is the DOCUMENT that is a shipping order.
                func.coalesce(PurchaseOrder.source_system, "") != CRM_SPO_SOURCE_SYSTEM,
            )
            .all()
        )

        # ONE batched read for every product on the page, before the first row is walked -
        # so the per-line fallback below is a dict lookup rather than a round trip.
        leads = self.lead_times({str(row.product_id) for row in rows})

        out: Dict[Tuple[str, str], List[_PoRow]] = {}
        for row in rows:
            balance = _dec(row.qty_ordered) - _dec(row.qty_received)
            if balance <= _ZERO:
                continue
            lead = row.lead_days
            if lead is None:
                lead = leads.get(str(row.product_id))
            if lead is None:
                lead = DEFAULT_LEAD_TIME_DAYS
            arrival = (
                row.issue_date + timedelta(days=max(int(lead), 0))
                if row.issue_date
                else None
            )
            out.setdefault((str(row.product_id), str(row.warehouse_id)), []).append(
                _PoRow(
                    po_number=str(row.po_number or ""),
                    po_line_no=int(row.line_no or 1),
                    line_id=str(row.id),
                    arrival_date=arrival,
                    bought_for=row.expected_date,
                    qty=balance,
                    supplier_name=row.supplier_name,
                    purchase_order_id=str(row.purchase_order_id),
                    ordered_qty=_dec(row.qty_ordered),
                    received_qty=_dec(row.qty_received),
                )
            )
        return out

    def _decided_elsewhere(self, exclude_line_ids: Optional[Sequence[str]]) -> set:
        """Core lines an ACTIVE decision covers, other than the ones being replaced.

        The carve-out matters as much as the rule. A line covered by a decision holds stock,
        and that hold is already out of `_free_stock`, so counting its demand again as ranked
        ahead would subtract the same units twice. But `_free_stock` EXCLUDES the lines being
        replaced - their own previous revision must not compete with the one replacing them -
        so for those lines the hold is NOT netted out, and they therefore have to stay in the
        queue. Miss this and re-confirming an order that reserved everything is refused as
        "nothing free for this line", which is what the sheet's own suite caught.

        By PROJECT LINE (`exclude_line_ids`), not by order: a covered line the confirmation
        carries forward keeps its hold, so it stays out of the queue like any other order's
        covered line, and only the named lines come back in.
        """
        rows = self._active_decision_snapshots()
        replaced = {str(line_id) for line_id in (exclude_line_ids or [])}
        out: set = set()
        for _pso_id, snapshots in rows:
            for snapshot in snapshots or []:
                if str((snapshot or {}).get("project_line_id") or "") in replaced:
                    continue
                core_line_id = (snapshot or {}).get("core_line_id")
                if core_line_id:
                    out.add(str(core_line_id))
        return out

    def _active_decision_snapshots(self) -> List[Tuple[Any, Any]]:
        """Every ACTIVE decision's `line_snapshots`, read at most once per request.

        The rows behind BOTH readings of "decided": `_decided_elsewhere`, which carves out
        the lines being replaced, and `_decided_anywhere_cached`, which does not. One query
        rather than two, because the answer cannot change mid-request.
        """
        if self._active_decision_rows is None:
            self._active_decision_rows = (
                self.db.query(
                    SOSupplyDecision.project_sales_order_id,
                    SOSupplyDecision.line_snapshots,
                )
                .filter(SOSupplyDecision.state == DECISION_ACTIVE)
                .all()
            )
        return self._active_decision_rows

    def _decided_anywhere_cached(self) -> Set[str]:
        """Core lines ANY active decision covers - the lines being replaced INCLUDED.

        Deliberately not `_decided_elsewhere_cached`. That one asks "whose hold competes
        with the line I am pricing", which is why it carves the replaced lines out. This one
        asks "has this line been decided at all", and on a verbatim re-send the line being
        re-confirmed is exactly the line whose decision has to count - carving it out would
        make the answer "no" for the one case it exists to serve (R17 / AC-S1-6: the
        outside-planning verdict belongs to undecided lines).
        """
        if self._decided_anywhere_cache is None:
            self._decided_anywhere_cache = self._decided_elsewhere(None)
        return self._decided_anywhere_cache

    def _decided_elsewhere_cached(self) -> Set[str]:
        """`_decided_elsewhere(self._replaced_line_ids)`, read once per request (S3).

        `_decided_elsewhere`'s own query has no product/warehouse filter - it reads every
        ACTIVE decision in the system - and `self._replaced_line_ids` is fixed for the
        whole of one `proposal_for`/`confirm`/`demand_facts` call, so calling it once per
        group-borrow line the way `_group_borrow_donors` used to is the exact N-queries-
        for-one-answer shape S3 is about.
        """
        if self._decided_elsewhere_cache is None:
            self._decided_elsewhere_cache = self._decided_elsewhere(self._replaced_line_ids)
        return self._decided_elsewhere_cache

    def _active_policy(self) -> Optional[Any]:
        """The active `scm.priority_policy` row, read at most once per request (S3).

        `priority.active_policy` has no caching of its own, and several ladder v2 readers
        each asked it independently - `_group_pile`, `_pile_book`, `pool_claims` - so a
        board of many lines paid one extra round trip per line for a row that cannot
        change mid-request.
        """
        if not self._active_policy_loaded:
            self._active_policy_value = priority.active_policy(self.db)
            self._active_policy_loaded = True
        return self._active_policy_value

    def _payment_terms_for(self, customer_ids: Iterable[str]) -> Dict[str, Optional[int]]:
        """`priority.payment_terms_by_customer`, memoized per customer for the request
        (S3): only customers not already answered are queried, so asking the same handful
        of customers across many lines of one board pays for each customer once."""
        ids = {str(cid) for cid in customer_ids if cid}
        missing = ids - set(self._payment_terms_cache.keys())
        if missing:
            self._payment_terms_cache.update(
                priority.payment_terms_by_customer(self.db, list(missing))
            )
        return {cid: self._payment_terms_cache.get(cid) for cid in ids}

    def _pile_book(
        self,
        product_ids: Iterable[str],
        warehouse_ids: Iterable[str],
        *,
        exclude_line_ids: Optional[Sequence[str]] = None,
    ) -> Dict[Tuple[str, str], List[Dict[str, Any]]]:
        """Every line still competing for each (product, location) pile, IN RANK ORDER.

        Ranked by the active `scm.priority_policy` - the one policy that also ranks the
        planning board and the loading plan - with sales-order number then line number then
        line id as the total tie-break, so the queue is reproducible.

        **Lines a confirmed decision already covers are excluded, and that is the
        double-count rule.** Such a line's claim on this pile is already expressed once, as a
        hold that `_free_stock` has taken out of the opening stock. Counting its outstanding
        quantity again as demand ranked ahead would subtract the same units twice and
        understate what is left for everybody behind it. The exception is the lines being
        replaced, whose own holds `_free_stock` deliberately does not net - see
        `_decided_elsewhere`.
        """
        pids = [pid for pid in product_ids if pid]
        wids = [wid for wid in warehouse_ids if wid]
        if not pids or not wids:
            return {}
        rows = (
            self.db.query(
                SalesOrderLine.id,
                SalesOrderLine.product_id,
                SalesOrderLine.warehouse_id,
                SalesOrderLine.qty_ordered,
                SalesOrderLine.qty_delivered,
                SalesOrderLine.required_date,
                SalesOrder.id.label("sales_order_id"),
                SalesOrder.so_number,
                SalesOrder.order_date,
                SalesOrder.demand_class,
                SalesOrder.customer_id,
                SalesOrder.requested_delivery_date,
                ProjectSalesOrderLine.line_no,
            )
            .join(SalesOrder, SalesOrder.id == SalesOrderLine.sales_order_id)
            .outerjoin(
                ProjectSalesOrderLine,
                ProjectSalesOrderLine.core_sales_order_line_id == SalesOrderLine.id,
            )
            .filter(
                SalesOrderLine.product_id.in_(pids),
                SalesOrderLine.warehouse_id.in_(wids),
                SalesOrder.status == "open",
                is_open_demand(),
                is_transferable_order(),
            )
            .all()
        )
        if not rows:
            return {}
        decided = self._decided_elsewhere(exclude_line_ids)
        rows = [row for row in rows if str(row.id) not in decided]
        if not rows:
            return {}

        terms = priority.payment_terms_by_customer(
            self.db, [str(row.customer_id) for row in rows if row.customer_id]
        )
        weights, class_weights = priority.policy_weights(self._active_policy())

        grouped: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
        for row in rows:
            key = (str(row.product_id), str(row.warehouse_id))
            open_qty = max(_dec(row.qty_ordered) - _dec(row.qty_delivered), _ZERO)
            if open_qty <= _ZERO:
                continue
            grouped.setdefault(key, []).append(
                {
                    # The CORE line id is the key, so two lines of one order that nobody has
                    # mirrored cannot collapse onto `(so_number, None)` and lose their share.
                    "key": str(row.id),
                    "so_number": row.so_number or "",
                    # Addressing only, and only because the queue is now READ: a screen showing
                    # who is ahead of you has to be able to link to their sales order and name
                    # their customer.
                    "sales_order_id": str(row.sales_order_id),
                    "customer_id": str(row.customer_id) if row.customer_id else None,
                    "line_no": row.line_no,
                    "line_id": str(row.id),
                    "open_qty": open_qty,
                    "required_date": row.required_date or row.requested_delivery_date,
                    "order_date": row.order_date,
                    "demand_class": row.demand_class,
                    "payment_terms_days": terms.get(str(row.customer_id or "")),
                }
            )

        for lines in grouped.values():
            self._rank_pile(lines, weights=weights, class_weights=class_weights)
        return grouped

    def _rank_pile(
        self,
        lines: List[Dict[str, Any]],
        *,
        weights: Dict[str, Any],
        class_weights: Dict[str, Any],
    ) -> None:
        """Score and sort ONE pile's lines in place, in the active policy's order.

        The one ranking rule for a pile, so the queue the sheet and the board serve stock
        down (`_pile_book`) and the queue a pool draw is measured against (`pool_claims`)
        cannot disagree about who is ahead. Each line states `line_id` (its ranking key),
        `required_date`, `order_date`, `payment_terms_days`, `demand_class`, `so_number`
        and `line_no`; scores are normalised across the lines passed in.

        Score first, then PLAN 3.5's own order as the tie-break: required date (missing
        last), sales-order number, line number, line id. The tie-break is load-bearing
        rather than decorative - a database with no policy configured scores every demand
        row 0.0 (the default weights name only `po_document_sequence`, which no
        sales-order line has), and without the date here the queue for a scarce pile
        would be alphabetical by sales-order number.
        """
        factors = priority.factors_for_demand_rows(
            self.db,
            [
                {
                    "row_key": line["line_id"],
                    "required_date": line.get("required_date"),
                    "order_date": line.get("order_date"),
                    "payment_terms_days": line.get("payment_terms_days"),
                    "demand_class": line.get("demand_class"),
                }
                for line in lines
            ],
            weights=weights,
            class_weights=class_weights,
        )
        scores = priority.scores_for(factors)
        for line in lines:
            line["rank_score"] = scores[line["line_id"]]
            # The terms behind the score, KEPT rather than thrown away with the local. A
            # queued line has to be able to say which factor put it in front of the line
            # behind it ("why do the orders stand ahead of me?"), and re-ranking the pile a
            # second time to answer that is how two rankings of one pile appear.
            line["factors"] = factors[line["line_id"]]
        lines.sort(key=_pile_order)

    def pile_book(
        self,
        product_id: str,
        warehouse_id: str,
        *,
        exclude_line_ids: Optional[Sequence[str]] = None,
    ) -> List[Dict[str, Any]]:
        """The queue at ONE pile, in the active policy's order. The read behind the screen.

        Public because the captain asked to see it - "I need to know what is ahead of me to have
        the visibility, and why they are ahead of me, meaning I need to know their rank also" -
        and it is `_pile_book` itself rather than a second query shaped like it: a queue drawn a
        second way would eventually disagree with the one the proposal was computed from, and
        then the screen would be arguing with the plan.
        """
        book = self._pile_book(
            [product_id], [warehouse_id], exclude_line_ids=exclude_line_ids
        )
        return book.get((str(product_id), str(warehouse_id)), [])

    def pool_claims(
        self,
        product_ids: Iterable[str],
        pool_ids: Iterable[str],
        members: Sequence[Dict[str, Any]],
        *,
        exclude_line_ids: Optional[Sequence[str]] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """What a shared pool's OWN book claims ahead of each line that would draw on it.

        The pool is a pile like any other: the lines fulfilled directly from it want it too,
        and a member location's shortfall queues behind whichever of them the policy ranks
        first. Without this a pool draw promised stock the pool's own outstanding orders had
        already been sold - the same defect as the own-location one, one location along.

        `members` are the lines asking, each stating `key`, `product_id`, `pool_id`,
        `required_date`, `order_date`, `payment_terms_days`, `demand_class`, `so_number` and
        `line_no`, and - when the member IS a core line - its `line_id`. Returns, per member
        key, the quantity ranked ahead (`qty`) and how many of the pool's lines that is
        (`lines`) - the count travels with the sum because the trail prints both ("BRW's own
        orders ranked ahead of this line claim 1").

        A member fulfilled from the pool itself (a bare site code is its own pool, migration
        311) is IN the pool's book, and must not be counted ahead of itself: it is one line
        with two keys here, and reading it as a rival took its own quantity out of what it
        could then draw. `line_id` is what excludes it.
        """
        book = self._pile_book(product_ids, pool_ids, exclude_line_ids=exclude_line_ids)
        if not members:
            return {}
        weights, class_weights = priority.policy_weights(self._active_policy())
        out: Dict[str, Dict[str, Any]] = {}
        by_pile: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
        for member in members:
            key = (str(member["product_id"]), str(member["pool_id"]))
            by_pile.setdefault(key, []).append(member)

        for key, asking in by_pile.items():
            # The askers are put INTO the pool's queue and ranked by the one rule the queue
            # itself is ranked by (`_rank_pile`), so "ahead" means exactly what the queue
            # screen shows: score, then required date, then sales-order number and line.
            everyone = [dict(line) for line in book.get(key, [])] + [
                {
                    "line_id": str(member["key"]),
                    "so_number": member.get("so_number") or "",
                    "line_no": member.get("line_no"),
                    "open_qty": _ZERO,
                    "required_date": member.get("required_date"),
                    "order_date": member.get("order_date"),
                    "payment_terms_days": member.get("payment_terms_days"),
                    "demand_class": member.get("demand_class"),
                    "asker": True,
                }
                for member in asking
            ]
            self._rank_pile(everyone, weights=weights, class_weights=class_weights)
            for member in asking:
                own_line_id = str(member["line_id"]) if member.get("line_id") else None
                ahead: List[Dict[str, Any]] = []
                for line in everyone:
                    if line.get("asker"):
                        if line["line_id"] == str(member["key"]):
                            break
                        continue
                    if line["line_id"] == own_line_id:
                        continue
                    ahead.append(line)
                out[str(member["key"])] = {
                    "qty": sum((line["open_qty"] for line in ahead), _ZERO),
                    "lines": len(ahead),
                }
        return out

    def _attribution(
        self,
        product_ids: Iterable[str],
        warehouse_ids: Iterable[str],
        warehouses: Dict[str, Warehouse],
        spo: Dict[Tuple[str, str], List[_SpoRow]],
        exclude_line_ids: Optional[Sequence[str]] = None,
        detail_for: Optional[Set[str]] = None,
    ) -> Dict[Tuple[str, str], Dict[str, Dict[str, Any]]]:
        """Share each product-location pile across every line still competing for it.

        The section 3.5 projection, now served in the ACTIVE POLICY's order rather than by
        required date alone (`_pile_book`), which is what makes a Reserve mean "what is left
        for this line after the demand ranked ahead of it" - the rule the captain asked for on
        reading a strip that said Available -8013 beside a proposed Reserve of 80.

        Per line it answers the components AND the arithmetic behind them: what is ranked
        ahead, how many lines that is, and what was therefore left.
        """
        grouped = self._pile_book(
            product_ids, warehouse_ids, exclude_line_ids=exclude_line_ids
        )

        out: Dict[Tuple[str, str], Dict[str, Dict[str, Decimal]]] = {}
        for key, demand_lines in grouped.items():
            product_id, warehouse_id = key
            warehouse = warehouses.get(warehouse_id)
            attributed = attribute_sources(
                warehouse_code=(
                    warehouse.warehouse_code if warehouse else warehouse_id
                ),
                opening_stock=self._free_at(product_id, warehouse_id),
                supply_events=[
                    {
                        "spo_number": row.spo_number,
                        "spo_line_no": row.spo_line_no,
                        "allocation_id": row.allocation_id,
                        "arrival_date": row.arrival_date,
                        "qty": row.qty,
                        # Named the same way here as in the trail: this allocator writes the
                        # sentence too, and one surface calling a promise overdue while
                        # another calls it fine is the disagreement the shared rule exists
                        # to prevent.
                        "overdue_days": row.overdue_days,
                    }
                    for row in spo.get(key, [])
                ],
                demand_lines=demand_lines,
                # `_pile_book` has already ordered them by the active policy; re-sorting by
                # required date here would serve the pile in one order and report the queue
                # in another.
                preserve_demand_order=True,
            )
            opening = self._free_at(product_id, warehouse_id)
            per_line: Dict[str, Dict[str, Any]] = {}
            ahead = _ZERO
            lines_ahead = 0
            queued: List[Dict[str, Any]] = []
            for line in demand_lines:
                line_id = line["line_id"]
                totals: Dict[str, Decimal] = {}
                for component in attributed.get(line_id, ()):  # type: ignore[arg-type]
                    totals[component.kind] = (
                        totals.get(component.kind, _ZERO) + component.qty
                    )
                per_line[line_id] = {
                    **totals,
                    # The arithmetic behind the share, in the strip's own words: what the
                    # queue in front of this line still wants, how long that queue is, and
                    # what was therefore left of the pile when this line was reached.
                    "so_qty_ahead": ahead,
                    "lines_ahead": lines_ahead,
                    "available_to_this_line": max(opening - ahead, _ZERO),
                }
                # WHO is in that queue, for the lines somebody is about to ask about. Computed
                # here, where the queue is already walked in rank order and every entry still
                # carries the factors it was ranked on - and only for the asked-for lines,
                # because naming the queue in front of all 2000 lines of a crowded pile would
                # be quadratic work nobody reads.
                if detail_for is None or line_id in detail_for:
                    per_line[line_id]["ahead_detail"] = _ahead_detail(line, queued)
                queued.append(line)
                ahead += line["open_qty"]
                lines_ahead += 1
            out[key] = per_line
        return out

    # ------------------------------------------------------------ borrow candidates

    def _borrow_candidates(
        self, fact: _LineFacts, *, need: Decimal = _ZERO
    ) -> List[Dict[str, Any]]:
        """Where else this line could be met from, with what it costs the holder.

        Two shapes, and they are answered differently: free stock at a location outside
        the Reserve pool has no donor to ask, so it carries no claim; stock another
        project is holding names that project and its impact, so CS is deciding with the
        donor's position in front of them (AC-B09).

        **Every donor states AutoCount's own triple beside the engine's figures** (PLAN
        13.11). Free stock alone nets reserved and confirmed holds only, and on this book
        almost nothing is confirmed, so "6,990 free" reads as a donor with plenty when
        47,000 is owed there. `qty_on_hand`, `so_qty`, `spo_qty` and the signed
        `available_qty` are the sentence the planner needs before taking somebody's stock.

        **The list is RANKED by what meeting THIS line would leave the donor with**, which is
        the answer to the captain's "I assume this list is ranked by recommendation, is it?" -
        it was not. The key is `available_after_need = available_qty - need`, where `need` is
        the line's residual at this rung (what the ladder was otherwise going to buy),
        descending, then availability descending, then free descending. The first row, and
        only the first, is `recommended`.

        A donor is never judged on giving away the WHOLE of its free stock. That was the first
        rule here and it was wrong in the way that matters: a location holding 11,000 with 140
        owed against it ranked below one holding 7,000 with nothing owed, so the screen
        recommended the smaller pile to cover 21 units.

        The typed quantity never re-orders the list either: a list that reshuffles under the
        cursor is not a recommendation.
        """
        if not fact.product_id:
            return []
        # Everything Reserve cannot reach. For a hot-selling product that INCLUDES the
        # line's own dealer location: Reserve may not touch it, and CS borrowing it with a
        # reason is the sanctioned way to use it (PLAN 3.3).
        inside = self._reserve_location_ids(fact)
        group_siblings = self._group_sibling_warehouses(fact)
        inside_group = inside | {str(w.id) for w in group_siblings.values()}
        out: List[Dict[str, Any]] = []

        free_index, holds_index = self._by_product()
        free_here = free_index.get(fact.product_id, {})
        holds_here = holds_index.get(fact.product_id, {})
        committed_at: Dict[str, Decimal] = {}
        for (held_warehouse, _project), qty in holds_here.items():
            committed_at[held_warehouse] = committed_at.get(held_warehouse, _ZERO) + qty
        warehouse_ids = [
            warehouse_id
            for warehouse_id, qty in free_here.items()
            if qty > _ZERO and warehouse_id not in inside
        ]
        warehouses = self._warehouses(warehouse_ids)
        for warehouse_id in sorted(warehouse_ids):
            warehouse = warehouses.get(warehouse_id)
            if warehouse is None:
                continue
            free = free_here[warehouse_id]
            committed = committed_at.get(warehouse_id, _ZERO)
            # LADDER V7.1 (AC-S3-10, tester's defect 30 Aug): free stock OUTSIDE this
            # line's ownership group is step 1's second half now (`group_take`), not a
            # borrow of any kind - free means owed to nobody. `RUNG_CROSS_GROUP_BORROW`
            # survives for READING a frozen v4/v5 snapshot and is written on no live row,
            # which is exactly what "retired" has to mean or the rung is not retired.
            # A bin inside the group needs no rung at all: it is the plain
            # `other_location` donor the ladder has always offered.
            is_other_group = bool(fact.group_code) and warehouse_id not in inside_group
            out.append(
                {
                    "source": ALLOC_SOURCE_OTHER_LOCATION,
                    "warehouse_code": warehouse.warehouse_code,
                    "warehouse_id": warehouse_id,
                    "free_qty": qty_text(free),
                    **self._donor_pile(fact.product_id, warehouse_id, committed, need),
                    "donor_impact": {
                        "free_before": qty_text(free),
                        "free_after_full_borrow": qty_text(_ZERO),
                        "committed_qty": qty_text(committed),
                    },
                    "rung": RUNG_GROUP_TAKE if is_other_group else None,
                }
            )

        holds = [
            (warehouse_id, project_id, qty)
            for (warehouse_id, project_id), qty in holds_here.items()
            # `project_id` empty means an adopted order holds it and there is no project to
            # name as the donor. Offering it would be offering a Borrow that cannot be
            # confirmed, so it is not offered - the stock stays netted out of free either way.
            if qty > _ZERO and project_id
        ]
        if holds:
            projects = self._projects([project_id for _w, project_id, _q in holds])
            donor_warehouses = self._warehouses([w for w, _p, _q in holds])
            for warehouse_id, project_id, qty in sorted(holds):
                warehouse = donor_warehouses.get(warehouse_id)
                donor = projects.get(project_id)
                if warehouse is None or donor is None:
                    continue
                free = free_here.get(warehouse_id, _ZERO)
                out.append(
                    {
                        "source": ALLOC_SOURCE_OTHER_PROJECT,
                        "warehouse_code": warehouse.warehouse_code,
                        "warehouse_id": warehouse_id,
                        "donor_project_ref": donor.project_code,
                        "donor_project_id": project_id,
                        "free_qty": qty_text(qty),
                        **self._donor_pile(fact.product_id, warehouse_id, qty, need),
                        "donor_impact": {
                            "free_before": qty_text(free + qty),
                            "free_after_full_borrow": qty_text(free),
                            "committed_qty": qty_text(qty),
                        },
                    }
                )

        out.extend(self._group_borrow_offer_rows(fact, need))
        ranked = self._ranked(out)
        # LADDER V7.1 (AC-S3-11): `BorrowAddDialog` lists the step-2 donors FIRST and in the
        # server's own order - same agent, latest date, same group, same warehouse (R4, R19)
        # - because that is the order the engine itself walks, and a dialog that re-ranked
        # them would put a different donor at the top from the one the proposal took. The
        # remaining rows (free stock, another project's hold, the manual same-group offers)
        # keep `_ranked`'s availability ordering behind them.
        step_two = self._order_borrow_offer_rows(fact, need)
        # EXACTLY ONE `recommended` head, and it is the ENGINE's actual choice (schema
        # `BorrowCandidate`: "exactly one row carries it"). `_ranked` stamps the head of the
        # manual list on its own availability ranking, which is the right answer only when
        # the ladder itself proposed nothing - two heads let the dialog pre-select a donor
        # the proposal never took.
        if step_two:
            for row in ranked:
                row["recommended"] = False
        candidates = step_two + ranked
        # S4 (`PLAN-local-supplier-oi-routing.md`, AC-2.20): the per-order sheet's own
        # `BorrowCandidate.location`, the board's `_donors_for` wrapper is not on this call
        # path at all - `SupplyLineCard`'s Borrow modal reads THIS list directly. Built by
        # `donor_location`, the SAME method the board's own `_donors_for` calls, so a
        # donor's figures here and on the Grid Location table can never come apart.
        for candidate in candidates:
            warehouse_id = candidate.get("warehouse_id")
            if not warehouse_id:
                continue
            candidate["location"] = self.donor_location(
                fact.product_id,
                warehouse_id,
                location_code=candidate.get("warehouse_code"),
                rung=candidate.get("rung"),
            )
        return candidates

    def open_po_balance(
        self, product_ids: Iterable[str], warehouse_ids: Iterable[str]
    ) -> Dict[Tuple[str, str], Decimal]:
        """Open PURCHASE-order balance per (product, location), netted for what is linked
        (S4, `PLAN-local-supplier-oi-routing.md`: moved here from the board's own
        `_open_po_balance` so both surfaces read ONE implementation).

        INFORMATION ONLY - `available_qty` stays `on hand - SO + SPO` - because a purchase
        order reaches a project line only through a link (PLAN section I).

        A line counts as ON ORDER on the same four tests every other on-order reader in this
        codebase applies (`allocation_suggestion_service`, `loading_plan_service`,
        `scm.on_order_v`, and `project_order_inquiry_service._candidates_for_row`, which is
        the reader that decides what may be LINKED):

          * `line_status = 'open'` and a balance still to come;
          * `purchase_orders.status IN ('active', 'partial')` - a draft recommendation
            nobody has confirmed is not on order;
          * an SPO document is not a PO - it is already counted as `spo_qty`, so counting
            it here would state one arrival twice.

        What an order-inquiry row already claims (`INQUIRY_PLACED`) is netted OFF, per line
        and floored at zero, so this figure and the quantity that dialog offers cannot
        disagree.

        Company-scoped through the ORM's own `do_orm_execute` listener - both
        `PurchaseOrderLine` and `OrderInquiryRow` carry `CompanyScopedMixin`, so this needs
        no explicit predicate of its own, unlike a raw `text()` query would.
        """
        products = list(product_ids)
        warehouses = list(warehouse_ids)
        if not products or not warehouses:
            return {}
        placed: Dict[str, Decimal] = {
            str(po_line_id): _dec(qty)
            for po_line_id, qty in (
                self.db.query(OrderInquiryRow.po_line_id, func.sum(OrderInquiryRow.qty))
                .filter(
                    OrderInquiryRow.state == INQUIRY_PLACED,
                    OrderInquiryRow.po_line_id.isnot(None),
                )
                .group_by(OrderInquiryRow.po_line_id)
                .all()
            )
        }
        rows = (
            self.db.query(
                PurchaseOrderLine.id,
                PurchaseOrderLine.product_id,
                PurchaseOrderLine.warehouse_id,
                PurchaseOrderLine.qty_ordered,
                PurchaseOrderLine.qty_received,
            )
            .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderLine.purchase_order_id)
            .filter(
                PurchaseOrderLine.product_id.in_(products),
                PurchaseOrderLine.warehouse_id.in_(warehouses),
                PurchaseOrderLine.line_status == "open",
                PurchaseOrderLine.qty_ordered > PurchaseOrderLine.qty_received,
                PurchaseOrder.status.in_(("active", "partial")),
                func.coalesce(PurchaseOrder.source_system, "") != SPO_HISTORY_SOURCE,
                PurchaseOrder.po_number.notlike("SPO-%"),
            )
            .all()
        )
        out: Dict[Tuple[str, str], Decimal] = defaultdict(lambda: _ZERO)
        for line_id, product_id, warehouse_id, ordered, received in rows:
            left = _dec(ordered) - _dec(received) - placed.get(str(line_id), _ZERO)
            if left > _ZERO:
                out[(str(product_id), str(warehouse_id))] += left
        return dict(out)

    def po_open_facts(
        self,
        *,
        also_products: Iterable[str] = (),
        also_warehouses: Iterable[str] = (),
    ) -> Dict[Tuple[str, str], Decimal]:
        """`open_po_balance` for the whole request, read ONCE (CI fix round).

        `donor_location` below is called per CANDIDATE, and the board calls it for every
        donor of every cell, so reading per row would be two statements per row - one of
        them an aggregate over every placed order-inquiry row in the book. The board's own
        Location table needs the same figures over a slightly different span (its rows can
        name an INACTIVE warehouse a frozen decision points at, which no pile covers), so
        it names that span with `also_*` on the FIRST call and both readers then share the
        one map. The per-key figure does not depend on how wide the `IN` was - the netting
        is per PO line - so a wider span changes no number, it only saves a round trip.

        Ordering matters and is not incidental: the board calls this AFTER `demand_facts`,
        which is what fixes the pile span (and resets this cache with it). Called before
        that, it would cache a map built over an empty request.
        """
        if self._po_open_cache is None:
            pile = self._pile_facts()
            self._po_open_cache = self.open_po_balance(
                {product_id for product_id, _warehouse_id in pile} | {
                    str(product_id) for product_id in also_products if product_id
                },
                {warehouse_id for _product_id, warehouse_id in pile} | {
                    str(warehouse_id) for warehouse_id in also_warehouses if warehouse_id
                },
            )
        return self._po_open_cache

    def donor_location(
        self,
        product_id: str,
        warehouse_id: str,
        *,
        location_code: Optional[str] = None,
        rung: Optional[str] = None,
    ) -> Dict[str, Any]:
        """The Grid Location table's own row shape, for ONE donor (S4,
        `PLAN-local-supplier-oi-routing.md`).

        Shared by the board's own `_donors_for` (candidate decoration on the board) and by
        `_borrow_candidates` above (the per-order sheet's own candidates), so a donor's
        figures can never come apart between the two surfaces for the same warehouse in the
        same request. `qty_on_hand`/`so_qty`/`spo_qty` are read off `_pile_facts` - the same
        `is_open_demand()`-filtered query the board's own `_demand_pressure` and
        `stock_levels_by_location`/`incoming_by_location` (both already a public seam the
        board calls through `self.supply.`) apply - so those three needed no extraction of
        their own. `po_open_qty` (`open_po_balance` above), the site-pool dealer-share
        carve-out (`available_for_project`) and the `where` tag are the three this method
        adds, and they are the three the two surfaces used to disagree about: the sheet
        called every donor `other_group`, never applied a pool's dealer share, and read its
        own un-netted open-PO figure off raw SQL.

        `rung` is the candidate's own (`front_planning_engine`), and it is what classifies
        `where` - the SAME rule the board applied at the call site before this was one
        method, stated here once so neither caller can restate it differently. The values
        are `project_fulfilment_board_service.WHERE_*`, named by literal here because that
        module imports THIS one.
        """
        pile = self._pile_facts().get(
            (product_id, warehouse_id),
            {"on_hand": _ZERO, "so_qty": _ZERO, "spo_qty": _ZERO},
        )
        available = pile["on_hand"] - pile["so_qty"] + pile["spo_qty"]
        po_open = self.po_open_facts().get((product_id, warehouse_id), _ZERO)
        is_pool = warehouse_id in self.site_pool_warehouses()
        if is_pool:
            where = "site_pool"
            pct = self.fulfilment_settings().get("pool_share_pct")
            avail_for_project = available_for_project(
                available, self.netting().pools_net(product_id).net, pct
            )
        else:
            where = "other_group" if rung == RUNG_GROUP_TAKE else "group"
            # No dealer share applies outside a site pool (D2, captain 3 Sep): the whole
            # of `available_qty` is a project's to take.
            avail_for_project = available
        return {
            "location": location_code,
            "where": where,
            "product_id": product_id,
            "warehouse_id": warehouse_id,
            # A donor has no demand OF ITS OWN in this shape - `qty`/`qty_demand` are
            # required on `BoardCellLocation` and read 0 the same way the board's own
            # `_location` reads them for a cited donor it was given no rows for.
            "qty": "0",
            "qty_demand": "0",
            "qty_on_hand": qty_text(pile["on_hand"]),
            "so_qty": qty_text(pile["so_qty"]),
            "spo_qty": qty_text(pile["spo_qty"]),
            "available_qty": qty_text(available),
            "po_open_qty": qty_text(po_open),
            "available_for_project": qty_text(avail_for_project),
        }

    def _order_borrow_offer_rows(
        self, fact: _LineFacts, need: Decimal = _ZERO
    ) -> List[Dict[str, Any]]:
        """The step-2 donors, in the dialog's own shape and in the engine's own order.

        `donor_impact` and the AutoCount pile triple are stated like every other donor row's
        (`BorrowCandidate` requires the first and the dialog prints the second): a row that
        omitted them did not merely look thin, it failed the response model and 500'd the
        whole supply sheet for any line that had a step-2 donor at all.
        """
        rows: List[Dict[str, Any]] = []
        for donor in self.order_borrow_candidates_for(fact):
            warehouse = self._warehouse_by_code(str(donor["location"]))
            if warehouse is None:
                continue
            qty = max(_dec(donor["qty"]), _ZERO)
            # What is free at that bin BESIDE what this donor's order is holding there: the
            # borrow takes the donor's committed quantity, so the free stock is what the bin
            # is left with either way and the committed quantity is what changes hands.
            free = self._free_at(fact.product_id, str(warehouse.id))
            rows.append(
                {
                    "source": ALLOC_SOURCE_OTHER_LOCATION,
                    "rung": RUNG_ORDER_BORROW,
                    "warehouse_code": donor["location"],
                    "warehouse_id": str(warehouse.id),
                    "free_qty": qty_text(qty),
                    **self._donor_pile(fact.product_id, str(warehouse.id), qty, need),
                    "donor_impact": {
                        "free_before": qty_text(free + qty),
                        "free_after_full_borrow": qty_text(free),
                        "committed_qty": qty_text(qty),
                    },
                    "donor_so_number": donor.get("donor_so_number"),
                    "donor_line_no": donor.get("donor_line_no"),
                    "donor_agent_code": donor.get("donor_agent_code"),
                    "donor_core_line_id": donor.get("donor_core_line_id"),
                    "donor_required_date": donor.get("donor_required_date"),
                    "same_agent": bool(donor.get("same_agent")),
                    # The engine's own order decided this list, so the FIRST row is the one
                    # the proposal would take.
                    "recommended": not rows,
                }
            )
        return rows

    def _group_borrow_offer_rows(
        self, fact: _LineFacts, need: Decimal
    ) -> List[Dict[str, Any]]:
        """The MANUAL group-borrow offer list (section 1c, ruled 25 August 2026): the
        donors a person may pick in Amend. Never auto-composed - the engine has no group
        borrow rung any more - because taking another customer's committed quantity and
        raising an order-back against their order is a decision a person makes with the
        donor's position in front of them.

        WHO IS OFFERED (AC-L6): a donor line sharing THIS line's sales agent at ANY rank
        ("she can authorise CS to move stock between her own orders"), and another agent's
        order only when it is ranked BELOW this line. A higher-ranked order belonging to
        somebody else is not this planner's to take, so it is not on the list at all.

        `recommended` is decided by `_ranked` alongside every other donor; this only states
        the facts that are true regardless of rank.
        """
        out: List[Dict[str, Any]] = []
        for donor in self._group_borrow_donors(fact):
            if not (donor["same_agent"] or donor["lower_ranked"]):
                continue
            warehouse = self._warehouse_by_code(donor["location"])
            if warehouse is None:
                continue
            free = self._free_at(fact.product_id, str(warehouse.id))
            out.append(
                {
                    "source": ALLOC_SOURCE_OTHER_LOCATION,
                    "rung": RUNG_GROUP_BORROW,
                    "warehouse_code": donor["location"],
                    "warehouse_id": str(warehouse.id),
                    "free_qty": qty_text(donor["qty"]),
                    **self._donor_pile(fact.product_id, str(warehouse.id), donor["qty"], need),
                    "donor_impact": {
                        "free_before": qty_text(free + donor["qty"]),
                        "free_after_full_borrow": qty_text(free),
                        "committed_qty": qty_text(donor["qty"]),
                    },
                    "donor_so_number": donor["donor_so_number"],
                    "donor_line_no": donor["donor_line_no"],
                    "donor_agent_code": donor["donor_agent_code"],
                    "donor_core_line_id": donor["donor_core_line_id"],
                    "lower_ranked": donor["lower_ranked"],
                    "same_agent": donor["same_agent"],
                    "donor_required_date": donor["required_date"],
                    # Position in the ranked pile, the tie-break `_ranked` needs to prefer
                    # the safest donor line at a pile rather than the largest.
                    "rank_index": donor["rank_index"],
                }
            )
        return out

    def _donor_pile(
        self,
        product_id: str,
        warehouse_id: str,
        committed: Decimal,
        need: Decimal = _ZERO,
    ) -> Dict[str, Any]:
        """The (product, location) pile behind a donor, in AutoCount's vocabulary.

        `available_qty` is on hand, less what the whole book still owes there, plus what is
        on the water. SIGNED and never clamped: "this donor is oversold by 46,531" is the
        fact that decides whether taking from them is safe, and a floor of zero would report
        it as "nothing left", which is a different and much weaker statement.

        `available_after_need` is that figure once THIS line is met, and `need_qty` is the
        quantity it was computed against, so the screen can state the default "After borrow"
        before anybody types anything and the planner can check the subtraction.
        """
        pile = self._pile_facts().get(
            (product_id, warehouse_id),
            {"on_hand": _ZERO, "so_qty": _ZERO, "spo_qty": _ZERO},
        )
        available = pile["on_hand"] - pile["so_qty"] + pile["spo_qty"]
        return {
            "qty_on_hand": qty_text(pile["on_hand"]),
            "so_qty": qty_text(pile["so_qty"]),
            "spo_qty": qty_text(pile["spo_qty"]),
            "available_qty": qty_text(available),
            "qty_free": qty_text(self._free_cache.get((product_id, warehouse_id), _ZERO)),
            "qty_committed": qty_text(committed),
            "need_qty": qty_text(need),
            # Signed like availability itself: a donor that cannot meet the line without going
            # short says so here, and that is the row the impact line turns red on.
            "available_after_need": qty_text(available - need),
        }

    @staticmethod
    def _ranked(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Least-damaging donor first, and only that one carries `recommended`.

        Damage is measured against THIS line's residual (`available_after_need`), not against
        the donor's whole free stock. Availability then free break the ties, so of two donors
        that could both meet the line comfortably the one with more room to spare wins.

        Between two donor LINES at one pile those first two keys are identical, because they
        are facts about the pile rather than about the line, so the ranked queue's own
        position breaks it: the donor furthest DOWN the queue is the safest to take from.
        Without it the biggest donor won on `free_qty`, and the biggest donor is usually the
        one whose own delivery is nearest.

        THAT tie-break applies only between two rows that BOTH carry a position. Free stock
        at a location, and a hold another project carries, name no sales-order line and so
        have no queue position at all - and a sentinel standing in for the absence sorted
        them behind every group-borrow donor they tied with, which recommended somebody
        else's committed quantity over stock nobody had claimed. A comparator rather than a
        sort key, because "compare this only when both sides have it" is a statement about a
        PAIR and a key function cannot make one.

        Every row is eligible since v7.1 (R5): the cross-group cap that used to mark a row
        `over_cap` - shown but never recommended - is gone, so the first row of the ranked
        list is always the recommendation.
        """

        def compare(left: Dict[str, Any], right: Dict[str, Any]) -> int:
            for name in ("available_after_need", "available_qty"):
                a, b = _dec(left[name]), _dec(right[name])
                if a != b:
                    return -1 if a > b else 1
            left_rank, right_rank = left.get("rank_index"), right.get("rank_index")
            if left_rank is not None and right_rank is not None and left_rank != right_rank:
                return -1 if int(left_rank) > int(right_rank) else 1
            a, b = _dec(left["free_qty"]), _dec(right["free_qty"])
            if a != b:
                return -1 if a > b else 1
            if left["warehouse_code"] != right["warehouse_code"]:
                return -1 if left["warehouse_code"] < right["warehouse_code"] else 1
            return 0

        candidates.sort(key=cmp_to_key(compare))
        for index, candidate in enumerate(candidates):
            candidate["recommended"] = index == 0
        return candidates

    def _pile_facts(self) -> Dict[Tuple[str, str], Dict[str, Decimal]]:
        """`on hand / SO qty / SPO qty` for every pile this request could borrow from.

        Read ONCE per request and only when a donor list is actually asked for: three
        queries, spanning every (product, location) the free and hold caches already know
        about, rather than three per donor row. A board of 800 products offering donors on
        every line would otherwise pay for thousands of round trips.

        `so_qty` is the shared `is_open_demand()` rule over every demand class, exactly as
        the cell's own stock strip counts it (13.7) - a dealer order occupies the donor's
        stock as completely as a project one does.
        """
        if self._pile_cache is not None:
            return self._pile_cache

        # EVERY ACTIVE WAREHOUSE, not only the ones carrying a `stock` row for the product
        # (ladder v4). The span used to come off the free cache, which is built from `stock`
        # rows - so a location holding nothing and owing 27,804 was absent, read as three
        # zeroes, and made the ownership group's net look better than the book says it is.
        # Same three queries either way; only the `IN` list is wider.
        self._pile_cache = self._pile_read(
            # The products this REQUEST is about, not only the ones that happen to carry a
            # `stock` row. A product with no stock row anywhere - which is every product
            # whose only supply is an SPO still on the water - left the span empty, and the
            # whole pile then read as three zeroes.
            self._request_product_ids
            | {product_id for product_id, _w in self._free_cache}
            | {product_id for product_id, _w, _p in self._holds_cache},
            # The planning bins AND the site pools. The pools are flagged OFF (R17) yet the
            # pool rung still reads their triple off this cache, so the two sets are unioned
            # here rather than the predicate being loosened - "outside fulfilment planning"
            # is about the ownership groups, and a pool is not one.
            set(self._planning_warehouses()) | set(self._site_pool_warehouses()),
        )
        return self._pile_cache

    def _planning_warehouses(self) -> Dict[str, Warehouse]:
        """Every warehouse fulfilment planning READS, by id, read once per request.

        Active AND flagged into fulfilment planning (`planning_predicate`, R17): a bin that
        is off contributes no on hand, no incoming and no sales-order line to anything the
        ladder or the board computes. This is the span ladder v4 nets over
        (`group_netting`), and the same rows `_group_sibling_warehouses` filters for one
        group's members - so narrowing it here narrows every one of them at once.

        The SITE POOLS are not in it, deliberately: a pool is off (it is reached through
        `pool_warehouse_id`, never as an ownership group) and its own set is
        `_site_pool_warehouses`. Anything that needs both unions the two, which is what
        `_pile_facts` does.
        """
        if self._planning_warehouses_cache is None:
            self._planning_warehouses_cache = {
                str(row.id): row
                for row in self.db.query(Warehouse)
                .filter(fulfilment_planning_predicate())
                .all()
            }
        return self._planning_warehouses_cache

    def netting(self) -> GroupNetting:
        """The ONE reader of availability for this request (section 1d).

        Built off `_pile_facts()`, which the request has already read - so the ladder, the
        board's cell table and the confirm-time recheck all net the same three figures, and
        none of them can come to a different view of what the IB group holds. Re-created
        whenever the pile cache is (`demand_facts` / `_facts_for` reset both), because the
        un-netting carve-outs differ per call.

        **The order-inquiry link walk shares the ARITHMETIC, not the SPAN** (AC-S1-5b,
        corrected 30 Aug). `project_order_inquiry_service._netting` calls the batched
        `netting_for_products` door WITHOUT `planning_only`, so its ownership-group index
        covers every ACTIVE bin, while this one is narrowed to the flagged ones (R17). That
        is the boundary the AC draws: the flag narrows what a PROPOSAL may draw, never what
        a non-planning consumer may see. Reading the two as one span was the claim this
        docstring used to make, and it was wrong.
        """
        if self._netting_cache is None:
            planning = self._planning_warehouses()
            pools = self._site_pool_warehouses()
            self._netting_cache = GroupNetting(
                triples=self._pile_facts(),
                warehouse_codes={
                    warehouse_id: warehouse.warehouse_code
                    for warehouse_id, warehouse in {**pools, **planning}.items()
                    if warehouse.warehouse_code
                },
                # Only the flagged bins form ownership groups (R17). The pools are in the
                # codes above so `pools_net` still has locations to net, and out of the
                # group index so a flagged-off bin can never be a group member.
                planning_warehouse_ids=set(planning),
                pool_warehouse_ids=set(pools),
            )
        return self._netting_cache

    def _pile_read(
        self, product_ids: Iterable[str], warehouse_ids: Iterable[str]
    ) -> Dict[Tuple[str, str], Dict[str, Decimal]]:
        """The three reads behind `_pile_facts`, over a stated span and cached by nobody."""
        product_ids = {pid for pid in product_ids if pid}
        warehouse_ids = {wid for wid in warehouse_ids if wid}
        pile: Dict[Tuple[str, str], Dict[str, Decimal]] = {}
        if not product_ids or not warehouse_ids:
            return pile

        for key, (on_hand, _reserved) in self.stock_levels_by_location(
            product_ids
        ).items():
            if key[1] in warehouse_ids:
                pile[key] = {"on_hand": on_hand, "so_qty": _ZERO, "spo_qty": _ZERO}

        owed = demand_qty()
        rows = (
            self.db.query(
                SalesOrderLine.product_id,
                SalesOrderLine.warehouse_id,
                func.coalesce(func.sum(owed), 0).label("owed"),
            )
            .join(SalesOrder, SalesOrder.id == SalesOrderLine.sales_order_id)
            .filter(
                SalesOrderLine.product_id.in_(list(product_ids)),
                SalesOrderLine.warehouse_id.in_(list(warehouse_ids)),
                SalesOrder.status == "open",
                is_open_demand(),
                is_transferable_order(),
            )
            .group_by(SalesOrderLine.product_id, SalesOrderLine.warehouse_id)
            .all()
        )
        for row in rows:
            key = (str(row.product_id), str(row.warehouse_id))
            pile.setdefault(
                key, {"on_hand": _ZERO, "so_qty": _ZERO, "spo_qty": _ZERO}
            )["so_qty"] = _dec(row.owed)

        for key, refs in self._spo_rows(product_ids, warehouse_ids).items():
            pile.setdefault(
                key, {"on_hand": _ZERO, "so_qty": _ZERO, "spo_qty": _ZERO}
            )["spo_qty"] = sum((ref.qty for ref in refs), _ZERO)

        return pile

    def pile_triples(
        self, product_ids: Iterable[str], warehouse_ids: Iterable[str]
    ) -> Dict[Tuple[str, str], Dict[str, Decimal]]:
        """AutoCount's `on hand / SO qty / SPO qty` per (product, location), over a stated span.

        Public for the board's pool rung: `Pool BRW | Had 0` beside an Inventory screen saying
        `Available 1` is two true numbers with nothing between them, and the trail has to print
        the pile the pool's queue was netted from. The same three reads the donor list is built
        on (`_pile_read`), so the pool rung and a Borrow row can never describe one pile two
        ways. One batched read for every pool pile a board touches, never one per rung.
        """
        return self._pile_read(product_ids, warehouse_ids)

    # --------------------------------------------------------------- the Plans page (D1)

    def list_decisions(
        self,
        *,
        query: Optional[str] = None,
        state: Optional[str] = None,
        agent_code: Optional[str] = None,
        sort: Optional[str] = None,
        dir: str = "desc",
        page: int = 1,
        limit: int = 50,
    ) -> Dict[str, Any]:
        """"Is the plan stored, how do I review it" (PLAN-demo-followups-19aug-ladder-v2 D1):
        every supply decision, one row per revision, cross-order.

        `state` defaults to `active` - what is COMMITTED NOW - because that is what the
        question is asking; `superseded` and `challenged` stay a click away for whoever wants
        the history. `query` matches the sales-order number, the customer name and the agent
        code, the three the row itself prints; a decision is a composition across an order's
        lines, not a per-line record, so there is no product to search here the way the
        worklist searches one.

        A pure read: nothing here writes, and unlike `proposal_for` it never challenges a
        drifted revision - the row is reporting what was decided, not re-judging it live.
        """
        columns = (
            SOSupplyDecision,
            ProjectSalesOrder.project_id,
            SalesOrder.id.label("sales_order_id"),
            SalesOrder.so_number,
            Customer.customer_name,
            SalesAgent.sales_agent.label("agent_code"),
            SalesAgent.person_label.label("agent_label"),
            User.name.label("decided_by_name"),
        )
        base = (
            self.db.query(*columns)
            .join(
                ProjectSalesOrder,
                ProjectSalesOrder.id == SOSupplyDecision.project_sales_order_id,
            )
            .outerjoin(SalesOrder, SalesOrder.id == ProjectSalesOrder.so_id)
            .outerjoin(Customer, Customer.id == SalesOrder.customer_id)
            .outerjoin(SalesAgent, SalesAgent.id == SalesOrder.sales_agent_id)
            .outerjoin(User, User.id == SOSupplyDecision.confirmed_by)
            .filter(SOSupplyDecision.state == (state or DECISION_ACTIVE))
        )
        if agent_code:
            base = base.filter(SalesAgent.sales_agent.ilike(agent_code.strip()))
        if query and query.strip():
            needle = f"%{query.strip()}%"
            base = base.filter(
                SalesOrder.so_number.ilike(needle)
                | Customer.customer_name.ilike(needle)
                | SalesAgent.sales_agent.ilike(needle)
            )

        total = base.count()
        sort_column = self._plan_sort_column(sort)
        ordering = sort_column.desc() if (dir or "desc") == "desc" else sort_column.asc()
        # A stable tie-break, always: two decisions confirmed the same second (a batch
        # confirm-all) would otherwise reorder between pages as ties are broken arbitrarily.
        base = base.order_by(nullslast(ordering), SOSupplyDecision.revision_no.desc())

        offset = max(page - 1, 0) * max(limit, 1)
        rows = base.offset(offset).limit(limit).all()
        data = [
            self._serialize_plan_row(
                decision,
                project_id=project_id,
                sales_order_id=sales_order_id,
                so_number=so_number,
                customer_name=customer_name,
                agent_code=row_agent_code,
                agent_label=agent_label,
                decided_by_name=decided_by_name,
            )
            for (
                decision,
                project_id,
                sales_order_id,
                so_number,
                customer_name,
                row_agent_code,
                agent_label,
                decided_by_name,
            ) in rows
        ]
        return {"data": data, "pagination": {"total": total, "page": page, "limit": limit}}

    def _plan_sort_column(self, sort: Optional[str]) -> Any:
        columns = {
            "so_number": SalesOrder.so_number,
            "customer_name": Customer.customer_name,
            "agent_code": SalesAgent.sales_agent,
            "revision_no": SOSupplyDecision.revision_no,
            "state": SOSupplyDecision.state,
            "decided_at": SOSupplyDecision.confirmed_at,
        }
        return columns.get(sort or "decided_at", SOSupplyDecision.confirmed_at)

    def _serialize_plan_row(
        self,
        decision: SOSupplyDecision,
        *,
        project_id: Optional[str],
        sales_order_id: Optional[str],
        so_number: Optional[str],
        customer_name: Optional[str],
        agent_code: Optional[str],
        agent_label: Optional[str],
        decided_by_name: Optional[str],
    ) -> Dict[str, Any]:
        return {
            "project_sales_order_id": str(decision.project_sales_order_id),
            "sales_order_id": str(sales_order_id) if sales_order_id else None,
            "project_id": str(project_id) if project_id else None,
            "so_number": so_number,
            "customer_name": customer_name,
            "agent_code": agent_code,
            "agent_label": agent_label,
            "revision_no": decision.revision_no,
            "state": decision.state,
            "decided_by_name": decided_by_name,
            "decided_at": decision.confirmed_at,
            "line_count": len(decision.line_snapshots or []),
            "components_summary": self._plan_components_summary(decision),
            "challenged_reason": (
                decision.superseded_reason if decision.state == DECISION_CHALLENGED else None
            ),
        }

    def _plan_components_summary(self, decision: SOSupplyDecision) -> Optional[str]:
        """"Reserve 213 · Buy 145": what a decision's revision actually holds, summed across
        every line it covers. The same four kinds `_snapshot` freezes, in the order the ladder
        proposes them, so the summary reads in the order a planner reasons in."""
        totals: Dict[str, Decimal] = {}
        for snapshot in decision.line_snapshots or []:
            for component in snapshot.get("components") or []:
                kind = component.get("kind")
                if not kind:
                    continue
                totals[kind] = totals.get(kind, _ZERO) + _dec(component.get("qty"))
        order = (RESERVE, TIMELY_SPO, BORROW, BUY)
        labels = {RESERVE: "Reserve", TIMELY_SPO: "Incoming", BORROW: "Borrow", BUY: "Buy"}
        parts = [
            f"{labels[kind]} {qty_text(totals[kind])}"
            for kind in order
            if totals.get(kind, _ZERO) > _ZERO
        ]
        return " · ".join(parts) if parts else None

    # --------------------------------------------------- confirm all approved (D3)

    def _write_holding_back(
        self,
        order: ProjectSalesOrder,
        entry: Any,
        write: Callable[[ProjectSalesOrder, Any], Dict[str, Any]],
        hold_back: bool,
    ) -> Tuple[Dict[str, Any], Any, List[Dict[str, Any]]]:
        """#1362 (owner, 29 Sep 2026: "i think we are too restrictive already"): one line's
        stale or conflicting decision must not refuse the whole order.

        When the recheck refuses named lines (`SupplyLinesRefused`, every failing line
        addressed by `project_line_id`), those lines are held back - rolled back, left out
        of the body, their saved decisions untouched - and the rest of the order is
        confirmed without them. A line the payload does not name is undecided, which is a
        normal outcome (13.4), so nothing here invents a state. Repeated while it still
        makes progress, because holding one member back can make a unit sibling fail its
        own check next. Refused as before when nothing would be left to confirm, or when a
        refusal names no line (it is about the order, not a line).

        Not for a planning-change apply (`hold_back=False`): a batch names its lines on
        purpose, and applying part of it would leave the batch half applied.
        """
        held: List[Dict[str, Any]] = []
        while True:
            try:
                return write(order, entry), entry, held
            except SupplyLinesRefused as refused:
                failing = refused.failing_lines
                ids = {str(f.get("project_line_id") or "") for f in failing}
                lines = list(getattr(entry, "lines", None) or [])
                keep = [line for line in lines if str(line.project_line_id) not in ids]
                if (
                    not hold_back
                    or not failing
                    or "" in ids
                    or not keep
                    or len(keep) == len(lines)
                ):
                    raise
                self.db.rollback()
                held.extend(
                    {k: v for k, v in f.items() if k != "project_line_id"} for f in failing
                )
                entry = entry.model_copy(update={"lines": keep})

    def confirm_many(
        self,
        entries: Sequence[Any],
        *,
        actor_user_id: str,
        assert_can_act: Callable[[Session, ProjectSalesOrder], None],
        write: Optional[Callable[[ProjectSalesOrder, Any], Dict[str, Any]]] = None,
        can_hold_back: Optional[Callable[[Any], bool]] = None,
        preview: bool = False,
    ) -> List[Dict[str, Any]]:
        """"Confirm all approved" (D3): every order's Confirm, each in its OWN transaction.

        The board's Approve all composes a verdict per LINE across up to fifty orders; this is
        the same per-order write `confirm` already does, called once per order rather than once
        per press from the panel. One order failing its own re-check - a stale line, a line
        somebody else confirmed a moment earlier - must never take the orders around it down
        too, or "Confirm all" would silently discard decisions the planner had gotten right.
        So each entry is tried, committed or rolled back, on its own; the caller gets a result
        named by `pso_id` either way, and a later entry runs whether or not an earlier one
        failed (no silent partial success - every order is accounted for in the reply).

        `entries` duck-types `ConfirmSupplyBody` per order (`.pso_id`, `.lines`), which is why
        `confirm` itself needed no change: it never learns this call has siblings.

        `write` swaps WHAT one order's press does and nothing else - the per-order
        transaction, the refusal handling and the shape of the reply are this method's
        either way. The board's single Confirm on a `?batch=` board passes the planning
        change's own apply (`_confirm_a_planning_change`), so a press that answers a batch
        applies it rather than writing an ordinary revision beside it (AC-P3-4).

        `preview` runs the very same per-order write and ROLLS BACK instead of committing,
        answering each ok order with the order inquiry rows and stock transfers the press
        raised, read inside the transaction (`_preview_of`). The session is flagged
        (`info["confirm_preview"]`) for the whole order so the post-commit drains keep their
        queues, which the root rollback then discards: a preview notifies nobody.

        An entry's `only_line_ids` scopes the order to exactly those lines BEFORE the write:
        a line outside the list is neither confirmed, held back nor echoed.
        """
        write = write or (
            lambda order, entry: self.confirm(order, entry, actor_user_id=actor_user_id)
        )
        can_hold_back = can_hold_back or (lambda _entry: True)
        results: List[Dict[str, Any]] = []
        for entry in entries:
            pso_id = str(entry.pso_id)
            # A malformed id would otherwise reach `get_order`'s query, and Postgres would
            # raise its own message for it - which then surfaces verbatim as this entry's
            # `error`, a database internal leaking to the planner instead of the plain "this
            # id is not a sales order" it actually is. Caught before the query, same as the
            # single-order route's own `validate_uuid_path` guard.
            try:
                uuid.UUID(pso_id)
            except (ValueError, AttributeError, TypeError):
                results.append(
                    {
                        "pso_id": pso_id,
                        "ok": False,
                        "error": "Not a valid sales order id.",
                        "failing_lines": None,
                    }
                )
                continue
            held_back: List[Dict[str, Any]] = []
            if preview:
                self.db.info["confirm_preview"] = True
            try:
                order = self.get_order(pso_id)
                assert_can_act(self.db, order)
                only = getattr(entry, "only_line_ids", None)
                if only is not None:
                    keep = {str(x) for x in only}
                    entry = entry.model_copy(
                        update={
                            "lines": [
                                line for line in entry.lines if str(line.project_line_id) in keep
                            ],
                            "rejected_line_ids": [
                                x for x in (entry.rejected_line_ids or []) if str(x) in keep
                            ],
                        }
                    )
                before_decision_id = None
                before_rows: Set[str] = set()
                before_moves: Set[str] = set()
                if preview:
                    active = self.active_decision(str(order.id))
                    before_decision_id = str(active.id) if active is not None else None
                    before_rows, before_moves = self._preview_baseline(order)
                body, entry, held_back = self._write_holding_back(
                    order, entry, write, can_hold_back(entry)
                )
                previewed = None
                if preview:
                    now_active = self.active_decision(str(order.id))
                    changed = now_active is not None and str(now_active.id) != before_decision_id
                    previewed = (
                        self._preview_of(order, before_rows, before_moves)
                        if changed
                        else {"inquiry_rows": [], "transfers": []}
                    )
                    self.db.rollback()
                else:
                    self.db.commit()
                results.append(
                    {
                        "pso_id": pso_id,
                        "ok": True,
                        **({"preview": True, **previewed} if preview else {}),
                        "decision_revision": body.get("revision_no"),
                        "inquiry_rows_created": body.get("inquiry_rows_created"),
                        "lines_decided": body.get("lines_decided"),
                        "lines_undecided": body.get("lines_undecided"),
                        "lines_confirmed": body.get("lines_confirmed"),
                        "lines_carried": body.get("lines_carried"),
                        "lines_withdrawn": body.get("lines_withdrawn") or [],
                        # The board confirms every order in one press and reports one toast,
                        # so the movements and the flags have to come back PER ORDER or the
                        # toast has nothing to add up. `.get`, because the planning-change
                        # apply hands back the revision it wrote through its own outcome
                        # dict and a key it never carried must not fail the whole press.
                        "transfers_written": body.get("transfers_written"),
                        "transfers_failed": body.get("transfers_failed"),
                        "transfers_kept": body.get("transfers_kept"),
                        "suspected_issues": body.get("suspected_issues"),
                        # How many covered lines THIS order's own press withdrew (owner
                        # ruling 23 Sep 2026, `PLAN-board-reject-on-confirmed-line.md`) -
                        # `.get`, the same reason every field above reads one: a body this
                        # order's own write never populated must not fail the whole result.
                        "rejected_count": body.get("rejected_count"),
                        # #1362 item 3: named lines skipped because nothing was open.
                        "lines_fulfilled_skipped": body.get("lines_fulfilled_skipped"),
                        # #1362 hold-back: lines the recheck refused, left out so the rest
                        # of the order confirmed; each keeps its saved decision.
                        "lines_held_back": held_back,
                        # #1362: Buys confirmed over goods that landed for their line.
                        "landed_buy_notices": body.get("landed_buy_notices"),
                    }
                )
            except Exception as exc:  # noqa: BLE001 - every order must get an answer
                self.db.rollback()
                message, failing_lines = _error_detail(exc)
                results.append(
                    {
                        "pso_id": pso_id,
                        "ok": False,
                        "error": message,
                        "failing_lines": failing_lines,
                        **({"preview": True} if preview else {}),
                    }
                )
            finally:
                if preview:
                    self.db.info.pop("confirm_preview", None)
        return results

    def _preview_baseline(self, order: ProjectSalesOrder) -> Tuple[Set[str], Set[str]]:
        """The ids of the order's inquiry rows and transfers BEFORE a press writes, so the
        preview can tell a row this press raised from one it only settles."""
        from app.models.stock_transfer import StockTransfer

        line_ids = [str(line.id) for line in self.lines_of(str(order.id))]
        rows = (
            {
                str(row_id)
                for (row_id,) in self.db.query(OrderInquiryRow.id).filter(
                    OrderInquiryRow.so_line_id.in_(line_ids)
                )
            }
            if line_ids
            else set()
        )
        moves = {
            str(move_id)
            for (move_id,) in self.db.query(StockTransfer.id).filter(
                StockTransfer.project_sales_order_id == str(order.id)
            )
        }
        return rows, moves

    def _preview_of(
        self,
        order: ProjectSalesOrder,
        before_rows: Set[str],
        before_moves: Set[str],
    ) -> Dict[str, Any]:
        """The order inquiry rows and stock transfers the press just wrote, read INSIDE its
        transaction (before the caller rolls it back). The new revision is the active one
        until that rollback, so its id names exactly what this press raised; `is_new` is
        false for a row or transfer that already existed before the write."""
        from app.models.stock_transfer import StockTransfer

        decision = self.active_decision(str(order.id))
        if decision is None:
            return {"inquiry_rows": [], "transfers": []}
        lines = self.lines_of(str(order.id))
        line_no_of = {str(line.id): line.line_no for line in lines}
        line_no_by_core = {
            str(line.core_sales_order_line_id): line.line_no
            for line in lines
            if line.core_sales_order_line_id
        }
        inquiry_rows = [
            {
                "line_no": line_no_of.get(str(row.so_line_id or "")),
                "item_code": row.item_code,
                "verb": row.verb,
                "is_new": str(row.id) not in before_rows,
                "qty": row.qty,
                "delivery_date": row.delivery_date,
                "stock_location": row.stock_location,
                "note": row.note,
            }
            for row in self.db.query(OrderInquiryRow)
            .filter(OrderInquiryRow.supply_decision_id == str(decision.id))
            .all()
        ]
        inquiry_rows.sort(key=lambda entry: (entry["line_no"] is None, entry["line_no"] or 0))
        moves = (
            self.db.query(StockTransfer)
            .filter(StockTransfer.supply_decision_id == str(decision.id))
            .all()
        )
        warehouse_ids = {
            str(w)
            for move in moves
            for w in (move.from_warehouse_id, move.to_warehouse_id)
            if w
        }
        codes = (
            {
                str(w.id): w.warehouse_code
                for w in self.db.query(Warehouse).filter(Warehouse.id.in_(warehouse_ids))
            }
            if warehouse_ids
            else {}
        )
        transfers = [
            {
                "line_no": line_no_by_core.get(str(move.so_line_id or "")),
                "is_new": str(move.id) not in before_moves,
                "kind": move.kind,
                "qty": move.qty,
                "from_location": codes.get(str(move.from_warehouse_id or "")),
                "to_location": codes.get(str(move.to_warehouse_id or "")),
            }
            for move in moves
        ]
        transfers.sort(key=lambda entry: (entry["line_no"] is None, entry["line_no"] or 0))
        return {"inquiry_rows": inquiry_rows, "transfers": transfers}
