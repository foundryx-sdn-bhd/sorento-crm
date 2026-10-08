"""The multi-order planning board: dates across, products down, one pile per location.

Contract: `documentation/plans/scm/PLAN-fulfilment-planning-from-autocount-so.md` section 13,
and the Phase 1 shapes in
`sorento_crm_frontend/app/(protected)/project-sales/_shared/types/fulfilmentPlanning.types.ts`.

The board is a LENS. It writes nothing, holds no decision object of its own, and creates no
status: `projects.so_supply_decisions` stays keyed per sales order, and a cell cuts across
orders, so a cell could never be the unit of persistence (13.4). Everything here is a read.

Four ideas carry the file.

**Nothing about supply is recomputed here.** Free stock is `ProjectSupplyService`'s own
figure through `free_stock_by_location`, and the division of one location's pile is
`attribute_sources`, the same pure engine the per-order sheet runs. What the board adds is
the ORDER the pile is served in, which is the one thing that genuinely differs (13.5).

**The ranking is the reorder engine's policy, not a second one.** `scm.priority_policy`
through `app.services.scm.priority.factors_for_demand_rows` - same row, same
`SUM(w*v) / SUM(w present)`, same rule that an absent factor is dropped from both sums and
never scored zero. The board states which policy produced what is on screen, and says so when
that policy separates nothing at all rather than showing a plausible-looking order it did not
earn.

**Every date keeps its own column.** There is no aggregate for the past: a line owed in July
2022 is shown in July 2022, marked as past, not merged into an "Overdue" pile with everything
else. The earlier build had that pile and on real data it swallowed 160 of 160 lines into one
column, which is precisely the schedule the planner opened the board to read.

**Allocation is per (product, LOCATION), never across.** Free stock at one warehouse cannot
cover a line that must be fulfilled from another; moving it is a transfer, which is M9's job
and a non-goal here (13.7). One cell therefore legitimately spans several locations, and the
source strip says which.

**The contest is shown, not hidden.** `_free_stock` nets CONFIRMED holds only, so two orders
composed separately can both be proposed the same stock and the second only finds out when
its confirmation is refused (13.5.1). Here they are in one cell: the higher-ranked row takes
the stock, and every row that lost is marked `contested` with a reason naming who took it.
The locking fix belongs to the confirmation path and is not attempted here.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import (
    AbstractSet,
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

from sqlalchemy import case, func
from sqlalchemy.orm import Session, aliased

from app.models.inventory import Warehouse
from app.models.order import Customer, SalesOrder, SalesOrderLine
from app.models.product import Product
from app.models.project_so import (
    ACK_REJECTED,
    DECISION_ACTIVE,
    INQUIRY_CANCELLED,
    OrderInquiry,
    OrderInquiryRow,
    ProjectSalesOrder,
    ProjectSalesOrderLine,
    SOSupplyDecision,
)
from app.models.sales_agent import SalesAgent
from app.models.user import User
from app.services import project_line_draft_service
from app.services.error_handler import AppException
from app.services.project_line_numbering import LineFacts, number_lines
from app.services.project_supply_service import (
    LADDER_VERSION,
    ProjectSupplyService,
    _dec,
    _leading_factor,
    _open_of,
    plan_qty_of,
)
from app.services.scm import priority
from app.services.scm import sales_agent_service
from app.services.scm import supply_assignment
from app.services.scm.supply_origin import buy_origin_by_product
from app.services.scm.planning_predicate import (
    OUTSIDE_FULFILMENT_PLANNING,
    outside_fulfilment_planning,
)
from app.services.scm.demand import (
    demand_qty,
    is_open_demand,
    is_plan_demand_line,
    is_undecided_demand,
)
from app.services.scm.front_planning_engine import (
    BORROW,
    available_for_project,
    BUY,
    RESERVE,
    RUNG_BUY,
    RUNG_GROUP_TAKE,
    RUNG_INCOMING,
    RUNG_ORDER_BORROW,
    RUNG_POOL,
    RUNG_SUPPLY_BORROW,
    TIMELY_SPO,
    date_text,
    landed_on,
    own_arrival_reason,
    pool_reserve_capacity,
    pool_share_capacity,
    qty_text,
    reserve_window_end,
)

_ZERO = Decimal("0")

#: How many orders may be planned together (13.2). Not arbitrary: the whole book is 862
#: products across 349 dates, roughly 300,000 cells, which is not a screen. The bound is part
#: of the design rather than a guard bolted on, so it is stated when it bites.
BOARD_ORDER_CAP = 50

#: The one column that is not a bucket of time. An absent required date has no period to be
#: put in, and guessing one is the same class of silent wrong answer as guessing a warehouse,
#: so it gets its own column, pinned last.
#:
#: There is deliberately NO aggregate column for the past. An earlier build lumped every past
#: date into one "Overdue", and on real data that swallowed 160 of 160 lines into a single
#: column - which destroys exactly the schedule the planner opened the board to read. A date
#: in the past is still a date; what it needs is to be MARKED, not merged (see `is_past`).
NO_DATE_BUCKET = "no_date"

#: Day granularity renders a window, not a column per distinct date (13.3). A DISPLAY bound
#: only: nothing is filtered out of the plan by it, and demand outside it is reached by moving
#: the window.
DAY_WINDOW_COLUMNS = 30

GRANULARITIES = ("day", "date", "week", "month")

_MONTHS = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)

#: What the Order Inquiry feed writes into `sales_orders.internal_note` when it cannot resolve
#: the project to a customer. Stripped for display, never shown with its own prefix.
_PROJECT_NOTE_PREFIX = "Order Inquiry project:"


def _own_pool_floor(fact: Any, pool_open: Optional[Mapping[str, Decimal]]) -> Decimal:
    """The ASKING bin's own site pool floor, off the walk's free-floor ledger (AC-N.12).

    `compose_lines` hands the proof one entry per pool since the R-N leftover was fixed -
    every pool's floor is a running balance now, not just the asking bin's - and the two
    sentences below still speak about the asking bin's own pool, so they read their own
    entry out of it rather than a second live figure.
    """
    if not pool_open:
        return _ZERO
    return max(_dec((pool_open or {}).get(getattr(fact, "pool_code", None) or "")), _ZERO)


def week_start(when: date) -> date:
    """The Monday of the ISO week containing `when`."""
    return when - timedelta(days=when.weekday())


def bucket_key_for(
    required_date: Optional[date], as_of: date, granularity: str
) -> str:
    """Which column a line belongs in: its own period, whether that period is past or future.

    Nothing is aggregated by age. The captain, on seeing the earlier build: "don't put overdue
    together, still split by the date, don't put under overdue". A line owed in July 2022 is
    owed in July 2022, and collapsing three years of them into one column destroys the schedule
    the board exists to show.

    `as_of` therefore takes NO part in the key, and is kept in the signature on purpose: the
    key is what the frontend rebuilds (`bucketKeyFor` in `_shared/lib/fulfilmentBoard.ts`), the
    two must stay the same function, and dropping the argument on one side only would be a
    silent divergence. Whether a period has already passed is reported separately, as a flag
    (`is_past`), because a bucket that is past today is not past-SHAPED - it is just old.

    Bucketing is a DISPLAY choice and nothing is ever stored bucketed: every contribution
    carries the line's own real `required_date`, and the allocation sorts on the ranking,
    never on the bucket.
    """
    if required_date is None:
        return NO_DATE_BUCKET
    if granularity == "month":
        return required_date.replace(day=1).isoformat()
    if granularity in ("day", "date"):
        return required_date.isoformat()
    return week_start(required_date).isoformat()


def bucket_end(key: str, granularity: str) -> Optional[date]:
    """The last date the bucket covers, or None for the dateless column."""
    if key == NO_DATE_BUCKET:
        return None
    start = date.fromisoformat(key)
    if granularity in ("day", "date"):
        return start
    if granularity == "week":
        return start + timedelta(days=6)
    following = (
        date(start.year + 1, 1, 1) if start.month == 12
        else date(start.year, start.month + 1, 1)
    )
    return following - timedelta(days=1)


def _bucket_label(key: str, granularity: str) -> str:
    if key == NO_DATE_BUCKET:
        return "No date"
    when = date.fromisoformat(key)
    month = _MONTHS[when.month - 1]
    if granularity == "month":
        return f"{month} {when.year}"
    if granularity == "day":
        return f"{when.day} {month} {when.year}"
    if granularity == "date":
        return when.strftime("%d/%m/%Y")
    return f"w/c {when.day} {month} {when.year}"


#: Where a location on a cell's stock table stands. The table is the evidence behind the
#: proposal, so it lists every location the ladder consulted - and these say which is which,
#: because a site pool holding 1716 and a group warehouse holding nothing are not the same
#: kind of row.
WHERE_OWN = "own"
WHERE_GROUP = "group"
WHERE_SITE_POOL = "site_pool"
WHERE_OTHER_GROUP = "other_group"

#: What `net_of` calls the five site pools when they are netted as ONE pile, and the value
#: the stock drill accepts under `group=` to read that same pile. An ownership group is its
#: own suffix (`IB`), and the pools have no suffix to be.
POOLS_SET = "pools"

#: Said by every question the ladder reached after the line was already covered. One sentence,
#: in one place, so five questions cannot phrase the same fact five ways. "Question" and not
#: "rung": the rung names are internal keys under ladder v5 and never reach a reader, and a
#: sentence that used one leaked the vocabulary the rewrite exists to hide.
_COVERED_BEFORE = "Fully covered before this question."

#: Said by a question that HAD something to give and gave nothing: the whole-line rule
#: (section 1e's last rule) refused the partial cover, so the line is bought entire and every
#: component the questions above produced was dropped. "Nothing was there" would be false and
#: "you were covered already" would be the opposite of what happened.
_WHOLE_LINE_RULE_DROPPED = (
    "It had stock to give, but the questions together could not cover the whole line, so "
    "none of it is taken and the line is bought entire."
)

#: Said by EVERY question on a line beyond its ATP reserve window
#: (`front_planning_engine`): ladder v5 buys such a line whole and walks none of them, so the
#: proof states the rule rather than reporting an empty search that never happened. Incoming
#: used to be the exception here and is not a question any more (section 1e).
_RESERVE_WINDOW_RUNG_WHY = (
    "The delivery date is beyond the lead time window, so this line takes no stock at all: "
    "purchasing can still buy for it in time."
)

#: What each ranking factor MEANS when it is the reason another line stands in front of you.
#: The planner's words, matching the frontend's `factorLabel` map subject for subject: the
#: policy's own keys (`need_by_date`) are exactly what the rank chips were told to stop showing.
#:
#: Kept even though `_trail` no longer calls it (`_reserve_own_why` names a COUNT, not a
#: factor, per the captain's own words on the own-location rung): `tests/
#: test_supply_ahead_detail.py::test_the_ahead_phrase_has_words_for_the_date_tie` still
#: unit-tests it directly, and that file is outside this fix's ownership.
_AHEAD_PHRASES = {
    "need_by_date": "an earlier delivery date",
    "document_age": "an older order date",
    "customer_credit": "shorter payment terms",
    "demand_class": "a higher-ranked demand type",
    "po_document_sequence": "an earlier purchase order sequence",
    #: Not policy factors at all: these three are the tie-break, in `_pile_book`'s own order
    #: (delivery date, then line number within an order, then sales-order number), and naming
    #: a factor here would claim a score difference the two lines do not have.
    "earlier_date": "the same rank and an earlier delivery date",
    "line_order": "an earlier line number in the same order",
    "tie_break": "the same rank and a lower sales order number",
}


def _ahead_phrase(by_factor: Optional[Dict[str, int]]) -> str:
    """Why the queue is ahead, in at most two reasons.

    The two commonest, biggest first. Naming all five would be a paragraph, and a paragraph is
    what the captain rejected ("the justification needs to be STRUCTURED").
    """
    ranked = sorted((by_factor or {}).items(), key=lambda item: (-item[1], item[0]))
    phrases = [_AHEAD_PHRASES.get(key, key.replace("_", " ")) for key, _n in ranked[:2]]
    if not phrases:
        return "a higher rank"
    return " or ".join(phrases)


def _parse_iso_date(value: Optional[str]) -> Optional[date]:
    """A frozen component's `donor_required_date` (ISO text, `_snapshot`'s own format),
    read back as a real date. `None` for anything that is not one, rather than raising -
    a snapshot written before the field existed simply has none."""
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _date_words(when: Optional[date]) -> str:
    """A date as it is said out loud: `3 Sep 2026`. Never ISO inside a sentence."""
    if when is None:
        return "an unstated date"
    return f"{when.day} {_MONTHS[when.month - 1]} {when.year}"


def _option_row(option: Any) -> Dict[str, Any]:
    """One ladder option, on the wire (R36, AC-S3-14).

    The contract is stated once, in
    `app/(protected)/project-sales/_shared/services/fulfilmentPlanningService.ts`, and this
    is the server half of it: five entries, in step order, one chosen at most,
    `fulfil_date` and `days_late` null TOGETHER, `debt_*` on the borrow steps only.

    LADDER V8 adds two fields to every row: `gives_qty`, because `pool_share` may cover PART
    of a line (R-B) and no other column would say how much, and `reason`, which is the
    `pool_share` row's own sentence when the number needs one ("600 is more than the 450 BRW
    can spare", AC-2.4).
    """
    gives = getattr(option, "gives_qty", None)
    return {
        "step": option.step,
        "label": option.label,
        "whole": bool(option.whole),
        "gives_qty": qty_text(_dec(gives)) if gives is not None else None,
        "reason": getattr(option, "reason", None),
        "fulfil_date": option.fulfil_date.isoformat() if option.fulfil_date else None,
        "days_late": option.days_late,
        "debt_so_number": option.debt_so_number,
        "debt_month": option.debt_month,
        "chosen": bool(option.chosen),
    }


def _project_key(label: Optional[str]) -> Optional[str]:
    """A stable key a pivot may group a project by.

    The board can be read with SALES ORDER, CUSTOMER or PROJECT down the side instead of
    product, and a pivot must never merge two subjects that merely read alike. For a customer
    that is `customer_id`. For a project there is no id to use: an order adopted from the
    AutoCount book has no project registration by design (plan section 4), and the project
    string the sheet named is the only identity it has. So the key is that string, normalised -
    case-folded with its whitespace collapsed - which merges "PP CHIN HIN / KIN TONG" with
    "PP Chin Hin /  Kin Tong" and nothing else. Null when the order names no project.
    """
    if not label:
        return None
    return " ".join(label.split()).casefold() or None


def _project_label_from_note(note: Optional[str]) -> Optional[str]:
    text_value = (note or "").strip()
    if not text_value:
        return None
    if text_value.startswith(_PROJECT_NOTE_PREFIX):
        return text_value[len(_PROJECT_NOTE_PREFIX):].strip() or None
    return text_value


def _project_label(order: SalesOrder) -> Optional[str]:
    # PLAN-so-project-label.md: the column, once a rule has ever written it, before the
    # note-reading fallback below - so the board keeps reading exactly what it always has
    # for the vast majority of orders the migration's backfill has not reached yet.
    return order.project_label or _project_label_from_note(order.internal_note)


class _Row:
    """One still-owed core sales-order line, which is all the board is ever built from."""

    __slots__ = (
        "line_id", "sales_order_id", "so_number", "customer_id", "customer_name",
        "agent_code", "agent_label", "agent_location_group",
        "project_label", "order_date", "line_no", "so_line_no", "item_code", "product_id",
        "qty",
        "qty_outstanding",
        "required_date", "warehouse_id", "location", "priority", "demand_class",
        "payment_terms_days", "bucket_key", "is_past", "rank_score", "rank_factors",
        "sources", "trail", "options", "contested", "qty_ordered", "qty_delivered",
        "proposed",
        "proposed_components",
        "free_before",
        "raw_facts", "taken_before", "last_taker", "borrow_candidates",
        "outside_reserve_window",
        "project_sales_order_id", "project_line_id", "warehouse_ids", "project_key",
        "so_qty_ahead", "lines_ahead", "available_to_this_line",
        "decision", "decided_by_name", "draft", "item_flags", "order_inquiry",
        "inquiry_decided", "lent_to",
        "unit_qty", "unit_line_count",
        "outside_planning",
        #: R3 (13 Sep browser walk): the book CANCELLED this line and it still has a
        #: PENDING change row nobody has decided on yet - `is_open_demand()` (`_demand_
        #: rows`) already dropped it out of ordinary demand, so this pair is what carries
        #: it back onto the board instead, read-only and at zero (`_cancelled_pending_
        #: change_rows`). `False`/`None` for every ordinary row.
        "cancelled", "pending_change_batch_id",
        #: SO-CANCEL-LINE: CS transferred the line to Cancel SO. `cancelled_by_name` is a
        #: display name, never the user id.
        "cancelled_at", "cancelled_by_name", "cancel_reason",
        #: R7 (query-count): the core line's own `source_ref` / `company_id`, off the SAME
        #: `SalesOrderLine` this row's own demand read already fetched - threaded onto
        #: `_LineFacts` (`ProjectSupplyService.demand_facts`) so the own-arrival credit's
        #: prefetch never pays a second query for a row this one already carries.
        "source_ref", "company_id",
    )

    def __init__(self, **kw: Any) -> None:
        for slot in self.__slots__:
            setattr(self, slot, kw.get(slot))
        self.rank_score = 0.0
        self.rank_factors = []
        self.sources = []
        # The ladder as it was walked for this line, rung by rung. Empty for an unplannable
        # line: no ladder was walked for it.
        self.trail = []
        # The five OPTIONS behind that walk (R36, AC-S3-14): every step with the date it
        # would have fulfilled the unit and how late that is. Empty on an unplannable or a
        # frozen line, for the same reason `trail` is: no walk, no options.
        self.options = []
        self.contested = False
        self.is_past = False
        # Filled by `_allocate`: what the engine proposes for this line, by kind, and what was
        # still unclaimed at its location when this line was reached.
        self.proposed = {}
        # What the ENGINE suggested for this line, in `sources` shape (AC-D2): the live
        # ladder on an undecided line, the composition frozen at confirm on a covered one.
        # `None` - never `[]` - on a covered line whose revision predates the frozen
        # proposal: "not recorded" and "suggested nothing" are different answers.
        self.proposed_components = None
        self.free_before = None
        self.raw_facts = {}
        # Who had already drawn this line's pile down when it was reached, and how much - the
        # evidence behind `contested` and behind the sentence a Buy carries.
        self.taken_before = None
        self.last_taker = None
        self.borrow_candidates = []
        # The ATP reserve window verdict for this line (`ProjectSupplyService
        # .outside_reserve_window`), read once and then answered the same way by the sentence
        # on the row, by its donor list and by its trail.
        self.outside_reserve_window = False
        # Warehouse CODE -> id, for the locations this line's Reserve may name. A confirm
        # component addresses a warehouse by id and the screen reads a code, so the pair has
        # to travel together (the same reason `SupplyComponent.source_warehouse_id` exists).
        self.warehouse_ids = {}
        # The queue in front of this line at its own pile, and what it left behind. None on a
        # covered line, which is not in that queue at all - see `covered`.
        self.so_qty_ahead: Optional[Decimal] = _ZERO
        self.lines_ahead: Optional[int] = 0
        self.available_to_this_line: Optional[Decimal] = _ZERO
        # What the order's ACTIVE revision froze for this line, when it covers it (13.4).
        self.decision: Optional[Dict[str, Any]] = None
        # AC-DT-2 (`PLAN-oi-decision-trail-ui.md`): who confirmed the decision that
        # covers this line, by name - never an id. `None` on an uncovered line, the
        # same as `decision` itself.
        self.decided_by_name: Optional[str] = None
        # What somebody SAVED on this line without confirming it yet (S4, R-F), read off
        # `projects.so_supply_decision_drafts` by `_attach_drafts` once the ladder has run -
        # `stale` is judged against the proposal this build has just computed. None when
        # nobody has saved one, which is most of the board.
        self.draft: Optional[Dict[str, Any]] = None
        # The item facts the ladder judged this line on (dealer hot-selling and where,
        # discontinued, whether anybody classified it). None on a line the ladder never
        # walked - unplannable or covered - because `false` there would claim a judgement that
        # was never made.
        self.item_flags: Optional[Dict[str, Any]] = None
        # What ANOTHER sales order borrowed off this line, and which order took it (AC-L6).
        # A list, and an empty one when nothing was lent: the cell has one shape to read.
        self.lent_to: List[Dict[str, Any]] = []
        # What purchasing has already been TOLD about this line, and how far they got:
        # `{inquiry_no, state}` off the inquiry row covering it, None when there is none.
        self.order_inquiry: Optional[Dict[str, Any]] = None
        # A LIVE order inquiry row already names this line, so the migrated book decided it
        # on the buying side (#875, 14 Sep 2026 ruling). Read off the same rule the Sales
        # Orders list counts by (`demand.live_inquiry_core_line_ids`), never re-derived from
        # `order_inquiry` above: that field is the CURRENT instruction, last writer winning,
        # and a line whose newest row is cancelled can still carry a live one.
        self.inquiry_decided: bool = False
        # The PLANNING UNIT this line was composed in (ladder v6): its order's lines for the
        # same item, location and delivery date, planned as one quantity. Filled by
        # `_allocate`; a line nobody proposed for (covered, unplannable) keeps the default,
        # which is itself - it was not planned with anybody.
        self.unit_qty: Optional[Decimal] = None
        self.unit_line_count: int = 1

    @property
    def covered(self) -> bool:
        """Somebody has already decided this line, so nothing is proposed for it.

        TWO WAYS, equal in weight since the 14 September 2026 ruling. An ACTIVE decision
        covers it - the board kept re-planning such a line, "Buy 43" beside a confirmed
        borrow of 10 and buy of 33, because it stayed in the board's demand while the pile's
        own queue (`_pile_book`) rightly left it out. Or a LIVE order inquiry row names it:
        since #875 the migrated sheet raised rows on real sales-order lines, so purchasing
        was told about 2,311 open lines the board would otherwise propose for all over again.

        The second kind has NO composition to print - the instruction predates any board - so
        it renders through `_apply_frozen` with an empty decision: no sources, no trail, no
        share of a queue it is not in. `decision` stays null, which is how the screen tells
        the two apart.
        """
        return self.decision is not None or self.inquiry_decided is True

    @property
    def unplannable(self) -> bool:
        """Nothing can be sourced for this line, so no ladder is walked for it.

        Two ways in. No location on the sales-order line (AC-FP16), and - since borrow
        ladder v7.1 - a location FLAGGED OUT of fulfilment planning (R17 / AC-S1-6): a bin
        that is off has no pile, no group net and no donor, so a proposal for it would be
        computed from stock the rest of the engine cannot see.

        **The flag verdict applies to UNDECIDED lines only.** A line an active decision
        covers is covered - the stock was found, promised and confirmed - and turning the
        bin's switch off afterwards is a statement about what may be PROPOSED next, never a
        retraction of what was already decided. Counting such a line as unplannable made a
        settled order read `Needs a location` / `blocked`, dropped it out of the frontend's
        `confirmLinesFor`, and had confirm refuse a verbatim re-send of the very composition
        it had itself written. A line with no location at all is still unplannable either
        way: there is nothing to have decided about.
        """
        if not self.warehouse_id:
            return True
        return bool(self.outside_planning) and self.decision is None

    @property
    def key(self) -> str:
        """The stable draft key, and part of the contract: the frontend REBUILDS it.

        `standingsFor` in `_shared/lib/fulfilmentBoard.ts` recomputes
        `${sales_order_id}|${line_no}|${item_code}|${bucket_key}` to count what the planner
        has decided, so a different shape here silently zeroes that counter.
        """
        return f"{self.sales_order_id}|{self.line_no}|{self.item_code}|{self.bucket_key}"


class FulfilmentBoardService:
    """`GET /project-sales/fulfilment-planning/board`. A pure read."""

    def __init__(self, db: Session):
        self.db = db
        self.supply = ProjectSupplyService(db)
        # Per-request stock facts, filled by `_allocate` and read by `_cell`. Cached rather
        # than re-read because the cell states the same figures the allocation was computed
        # from - a second read could disagree with the proposal sitting next to it.
        self._free: Dict[Tuple[str, str], Decimal] = {}
        self._levels: Dict[Tuple[str, str], Tuple[Decimal, Decimal]] = {}
        self._incoming: Dict[Tuple[str, str], List[Any]] = {}
        # Core line id -> how the confirm endpoint names it (planning record, mirror line).
        self._addressing: Dict[str, Dict[str, Any]] = {}
        # (product, warehouse) -> (owed by the whole book, of which confirmed) and what
        # confirmed decisions hold there. Read once, printed beside the free figure.
        self._pressure: Dict[Tuple[str, str], Tuple[Decimal, Decimal]] = {}
        self._held: Dict[Tuple[str, str], Decimal] = {}
        # (product, POOL warehouse) -> AutoCount's `on hand / so_qty / spo_qty`, for the pool
        # rung of the trail. One batched read over every pool a served line may draw on.
        self._pool_piles: Dict[Tuple[str, str], Dict[str, Decimal]] = {}
        # Ownership group -> the warehouses that carry its suffix, e.g. `BB` -> BRW-BB /
        # MWH-BB / DC1-BB. One read for the whole board; empty when no order's agent holds a
        # group, which is what makes the cell say so rather than silently show one location.
        self._group_warehouses: Dict[str, List[Tuple[str, str]]] = {}
        # EVERY active warehouse by the group its code carries, built once per board off
        # the supply service's own cached read. `_cited_locations` asks per cell.
        self._warehouses_by_group_cache: Optional[
            Dict[str, List[Tuple[str, str]]]
        ] = None
        # Warehouse id -> code for every site pool rung 2 may draw on, read once per board.
        # The cell's location table lists the pool a proposal cites, tagged as a pool rather
        # than left to look like one of the agent's own group warehouses.
        self._pool_warehouses: Dict[str, str] = {}
        # Warehouse id -> the pool warehouse id it draws on, for every active warehouse. What
        # makes "this line's OWN site pool" a fact off `warehouses.pool_warehouse_id` rather
        # than a comparison of code prefixes - the naming coincidence is not the rule.
        self._pool_of: Dict[str, str] = {}
        # The warehouses `_pressure` / `_incoming` / `_po_open` were actually asked about. What
        # tells "counted, and the answer is zero" from "never looked" for a cited location.
        self._counted_warehouses: set = set()
        # (product, warehouse) -> open PURCHASE-order balance there, netted for the
        # order-inquiry rows already placed on those lines. Information beside the decision:
        # `available_qty` stays on hand - SO + SPO, because a PO reaches a project line only
        # through a link (PLAN section I).
        self._po_open: Dict[Tuple[str, str], Decimal] = {}
        # PROJECT line ids `build()` was asked to preview as uncovered (`exclude_covered_line_ids`).
        self._exclude_covered_line_ids: set = set()
        # Row key -> that CONTRIBUTION's own location table (R1/B1). Built cell by cell and
        # read again when the top-level contribution list is assembled, so the List view and
        # the cell's drawer quote one line the same figures.
        self._locations_by_row: Dict[str, List[Dict[str, Any]]] = {}

    # ----------------------------------------------------------------- public

    def build(
        self,
        so_numbers: Sequence[str],
        *,
        granularity: str = "week",
        as_of: Optional[date] = None,
        day_window_start: Optional[date] = None,
        preview_policy: Optional[str] = None,
        exclude_covered_line_ids: Optional[Sequence[str]] = None,
        actor_user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """The board for one selection of sales orders.

        `as_of` is a parameter rather than the clock so which periods read as past is
        reproducible, and it is echoed back in the response for the same reason: a board that
        quietly disagreed with itself between two reads would be disagreeing about which of the
        planner's commitments are already late.

        `actor_user_id` is the requester, threaded through only for `board_undo_map`'s
        own admin gate (AC-R2-30, S5): a journal-less order's `undo` reads
        `mode="reconstructed"` for a superadmin/admin requester and `None` for every
        other role, same as an order with no active decision at all.

        `exclude_covered_line_ids` (PROJECT line ids) previews the ladder for a line an ACTIVE
        decision currently covers, as if that one line's hold did not exist: its own reserve is
        un-netted and its demand stays in the queue - `ProjectSupplyService`'s own carve-out for
        the lines a `confirm` is about to replace (`_decided_elsewhere`). Used by the planning-
        change batch to show what the ladder would propose TODAY for a `replan` row, rather than
        the composition that is about to be released.
        """
        self._exclude_covered_line_ids = {
            str(i) for i in (exclude_covered_line_ids or [])
        }
        self._locations_by_row = {}
        self._buy_origin: Dict[str, Optional[str]] = {}
        if granularity not in GRANULARITIES:
            raise AppException(
                status_code=422,
                message=(
                    "Granularity must be day, date, week or month, "
                    f"not '{granularity}'."
                ),
                code="board_granularity_unknown",
            )
        numbers = [str(n).strip() for n in so_numbers if str(n).strip()]
        if len(numbers) > BOARD_ORDER_CAP:
            raise AppException(
                status_code=422,
                message=(
                    f"A board plans at most {BOARD_ORDER_CAP} sales orders at once, and "
                    f"{len(numbers)} were selected. Narrow the selection and try again."
                ),
                code="board_selection_too_large",
            )
        as_of = as_of or date.today()
        policy_name, weights, class_weights, is_preview = self._policy(preview_policy)

        # ONE query for the adopted record, the pending-batch id per order and the core
        # lines those pending rows name (query-count guard, `test_a_board_of_76_lines_does
        # _not_scale_its_query_count_with_the_line_count`): `_standings` used to run these
        # as two separate reads, and a bare `_cancelled_pending_change_rows` call below ran
        # its own join every build whether or not anything was ever pending. Read once here
        # instead - and BEFORE the demand rows, because the third value decides which of
        # them an inquiry still counts as deciding.
        adopted_by_so, pending_by_so, pending_core_lines = self._order_plan_status(numbers)
        # Self-heal (issue #969, B2 owner ruling 17 Sep 2026): the board read is the one
        # place the FE derives `no_mirror` from (`fulfilmentBoard.ts`) and the read
        # confirm-all's own multi-order build comes from, so this is the seam that heals a
        # core line that arrived after adoption - confirm stays a pure write (AC-PR8). Run
        # BEFORE `_demand_rows` below, which is what resolves each line's `project_line_id`
        # off the mirror this creates (`_mirror_addressing`) - a heal after it would leave
        # this same response's late line addressed to nothing. Gated on an adopted,
        # unauthored mirror (`status = 'adopted'`, `project_id IS NULL`), the same gate
        # every other seam uses. `adopted_by_so` above already answers whether ANY selected
        # order has a `ProjectSalesOrder` row at all - empty means none of them can need
        # healing, so an ordinary (never-adopted) board build costs no extra statement
        # (`test_a_board_of_76_lines_does_not_scale_its_query_count_with_the_line_count`).
        if adopted_by_so:
            from app.models.project_so import SO_STATUS_ADOPTED
            from app.services.project_so_adoption_service import ProjectSOAdoptionService

            heal_targets = (
                self.db.query(ProjectSalesOrder)
                .join(SalesOrder, SalesOrder.id == ProjectSalesOrder.so_id)
                .filter(
                    SalesOrder.so_number.in_(numbers),
                    ProjectSalesOrder.status == SO_STATUS_ADOPTED,
                    ProjectSalesOrder.project_id.is_(None),
                )
                .all()
            )
            if heal_targets:
                adoption = ProjectSOAdoptionService(self.db)
                for order in heal_targets:
                    adoption.mirror_missing_lines(order)
        # S1 (#978): one `undo` per selected order, keyed the same way `adopted_by_so`
        # is. A separate read rather than folded into `_order_plan_status`'s own query -
        # that one is a query-count guard over the LINE-scaling `PlanningChangeRow` join;
        # this is bounded by the (<=50) order count regardless of how many lines a board
        # holds, so it does not carry the same guard. Runs AFTER the self-heal above so a
        # newly-healed order's own decision (none yet, but a future re-confirm's) is read
        # off the mirror the heal just completed, not a stale one.
        from app.services.project_supply_undo_service import board_undo_map

        undo_by_so = board_undo_map(self.db, adopted_by_so, actor_user_id=actor_user_id)
        # AC-DT-1 (`PLAN-oi-decision-trail-ui.md`): the board header's own "Revision N,
        # confirmed by <name>" line, one per order. A SEPARATE read from `undo_by_so`
        # above on purpose: that one is gated to journalled (or admin-reconstructable)
        # revisions for the undo gear specifically, and a header built off it would read
        # "No decision yet" on most confirmed orders - a pre-lane revision, or one
        # minted outside the board's own confirm routes, still gets a header.
        decision_headers_by_so = self._decision_headers(adopted_by_so)
        rows = self._demand_rows(numbers, reopened_by_change=pending_core_lines)
        # R3 (13 Sep browser walk): a cancelled line with a still-PENDING change row, read
        # separately from ordinary demand and added to `contributions` alone, below - never
        # to `rows` itself, so it takes no part in bucketing, ranking or the ladder walk
        # (`_allocate`) that follows, and no part of `_standings`' totals either. Skipped
        # entirely when NO selected order has a pending batch at all: its own predicate
        # already requires one (`PlanningChangeBatch.applied_at IS NULL AND PlanningChangeRow
        # .applied_state == pending`, exactly what `pending_by_so` above just answered), so
        # an empty `pending_by_so` guarantees this would find nothing either.
        cancelled_rows = (
            # ALREADY-ADMITTED LINES ARE EXCLUDED, and since the board's predicate widened
            # (14 Sep 2026) that is not a formality. This read's own predicate is pure book
            # status - `line_status != 'open' OR demand_qty() <= 0` - which a closed-by-
            # delivery undecided line now satisfies WHILE `_demand_rows` also admits it, so
            # without the carve-out such a line would appear in `contributions` twice under
            # one key: once planned, once read-only at zero.
            self._cancelled_pending_change_rows(
                numbers, admitted={row.line_id: row for row in rows}
            )
            if pending_by_so
            else []
        )
        # SO-CANCEL-LINE: lines CS transferred to Cancel SO stay on the board read-only at
        # zero (no ladder, no bucketing), whether or not a change row is pending.
        shown = {row.line_id for row in rows} | {row.line_id for row in cancelled_rows}
        # A CRM-cancelled line always has a project mirror line, so only an adopted order can
        # have one; a never-adopted board build costs no extra statement
        # (`test_a_board_of_76_lines_does_not_scale_its_query_count_with_the_line_count`).
        if adopted_by_so:
            cancelled_rows = cancelled_rows + self._crm_cancelled_rows(numbers, shown=shown)
        # S3 (`PLAN-local-supplier-oi-routing.md`): ONE call for the whole board, never per
        # line - `test_buy_origin_computed_once_per_board_build` pins this.
        self._buy_origin = buy_origin_by_product(
            self.db, {row.product_id for row in rows if row.product_id}
        )
        if self._exclude_covered_line_ids:
            for row in rows:
                if row.project_line_id and row.project_line_id in self._exclude_covered_line_ids:
                    # Previewed as uncovered (see `build`'s docstring): the ladder is walked for
                    # it below like any other row, against facts that un-net its own hold.
                    #
                    # BOTH WAYS OF BEING COVERED, not just the decision. The caller has said
                    # "treat this line as undecided"; leaving `inquiry_decided` standing would
                    # honour half of that and hand back an empty composition - which is what
                    # `build_batch` saw, because it builds this preview BEFORE its own change
                    # row exists for `_order_plan_status` to find.
                    row.decision = None
                    row.inquiry_decided = False
        for row in rows:
            row.bucket_key = bucket_key_for(row.required_date, as_of, granularity)
            # Per LINE, against its own date, which is the number the "N of M lines are past
            # their delivery date" summary counts. A line dated yesterday is past even though
            # the week it sits in has not ended, so the bucket flag alone would undercount it.
            row.is_past = row.required_date is not None and row.required_date < as_of

        cells_by_key: Dict[Tuple[str, str], List[_Row]] = defaultdict(list)
        for row in rows:
            cells_by_key[(row.item_code, row.bucket_key)].append(row)

        factors_by_key: Dict[str, Any] = {}
        for members in cells_by_key.values():
            factors = priority.factors_for_demand_rows(
                self.db,
                [
                    {
                        "row_key": member.key,
                        "required_date": member.required_date,
                        "order_date": member.order_date,
                        "payment_terms_days": member.payment_terms_days,
                        "demand_class": member.demand_class,
                    }
                    for member in members
                ],
                weights=weights,
                class_weights=class_weights,
            )
            scores = priority.scores_for(factors)
            for member in members:
                member.rank_factors = factors[member.key]
                member.raw_facts = priority.raw_facts_for_demand_row(
                    {
                        "required_date": member.required_date,
                        "order_date": member.order_date,
                        "payment_terms_days": member.payment_terms_days,
                        "demand_class": member.demand_class,
                    }
                )
                member.rank_score = scores[member.key]
                factors_by_key[member.key] = factors[member.key]
            # Highest rank first, ties broken on sales-order number then line number so the
            # order is TOTAL: a non-total rule gives a different answer on each refresh and
            # "why did this order lose today" becomes unanswerable.
            members.sort(key=lambda m: (-m.rank_score, m.so_number, m.line_no))

        buckets = self._buckets(
            {row.bucket_key for row in rows}, granularity, day_window_start, as_of
        )
        products = sorted({row.item_code for row in rows})

        # Serve order: bucket by bucket in date order, product by product, and inside a cell by
        # rank. That is the order the one pile is served in, so a line owed in 2025 is served
        # before a line owed in 2030 rather than losing to it by an accident of iteration. With
        # the buckets now plainly chronological this falls out of the axis itself, where before
        # it needed the aggregate past column pinned to the front.
        #
        # Built from EVERY bucket the selection has, never from the ones on screen. The day
        # window is a display bound, and a display bound that changed what a line was proposed
        # would make the same line read Reserve on one view and Buy on another.
        served: List[_Row] = []
        for bucket_key in self._serve_order({row.bucket_key for row in rows}):
            for item in products:
                served.extend(cells_by_key.get((item, bucket_key), []))

        self._allocate(served, as_of=as_of)
        # AFTER the ladder, never before: `stale` compares what was saved against what this
        # build is proposing, and before `_allocate` there is no proposal to compare with.
        self._attach_drafts(rows)

        cells: List[Dict[str, Any]] = []
        for bucket in buckets:
            for item in products:
                members = cells_by_key.get((item, bucket["key"]))
                if not members:
                    # No cell at all, and the grid renders that as blank. A blank cell is NOT
                    # a zero: it means no selected order owes this product by this date.
                    continue
                cells.append(self._cell(item, bucket["key"], members))

        return {
            "granularity": granularity,
            "as_of": as_of,
            "policy": {
                "name": policy_name,
                "factors": {k: float(v or 0.0) for k, v in (weights or {}).items()},
                "demand_class_weights": {
                    k: float(v or 0.0) for k, v in (class_weights or {}).items()
                },
                "is_preview": is_preview,
                # Stated rather than left for the reader to work out: under the live seeded
                # rule every board row scores 0.0, and a planner who cannot see that is
                # looking at a flat ranking believing it is a considered one (13.5).
                "discriminates_nothing": priority.discriminates_nothing(factors_by_key),
            },
            "dateBuckets": buckets,
            "productRows": [{"item_code": item, "description": None} for item in products],
            "cells": cells,
            # EVERY contributing line, never windowed: a cell only exists for a bucket that
            # made it into `buckets`, and at day granularity that is the 30-day window
            # (`DAY_WINDOW_COLUMNS`), not the whole selection. `_allocate` already ran over
            # every bucket (`served`, above), so a line outside the window carries a real
            # proposal here even though no cell on screen shows it - Approve all, the strip and
            # the List view read this list, never `cells`, for exactly that reason.
            "contributions": (
                [self._contribution(row) for row in rows]
                + [self._contribution(row) for row in cancelled_rows]
            ),
            "orders": self._standings(
                rows, adopted_by_so, pending_by_so, undo_by_so, decision_headers_by_so
            ),
            # SELECTION-scoped totals, counted over every contributing line before any window
            # is applied - never over the cells on screen.
            #
            # The screen cannot compute these for itself. Summed off the visible cells, "143 of
            # 153 lines are already past their delivery date" is right at week and month and
            # DISAPPEARS at day, because the 30-day window opens on work still to come and so
            # holds no past cell at all. The planner switching to the closest view would
            # silently lose the most important number on the board.
            "line_count": len(rows),
            "past_line_count": sum(1 for row in rows if row.is_past),
            "unplannable_line_count": sum(1 for row in rows if row.unplannable),
            "contested_line_count": sum(1 for row in rows if row.contested),
            # LADDER V8 (R-K): how much of a site pool is kept back for dealers, so the Stock
            # tab's SUBTOTAL row can print "Available for Project" over the pool's own net -
            # a figure no row carries, because it is the SET's position rather than a bin's.
            # Every pool ROW already arrives with its own `available_for_project` computed
            # here; this is the one number the client still has to apply the rule with.
            "pool_share_pct": self._pool_share_pct(),
            # "Borrow from another order" (PLAN-borrow-location-only-8oct.md, BR1): the Decide
            # menu offers it exactly when this is on, read once with the walk's own switch.
            "borrow_from_order_enabled": self.supply.borrow_from_order,
        }

    # ----------------------------------------------------- the stock drill-down

    def stock_detail(
        self,
        product_id: str,
        warehouse_id: Optional[str] = None,
        line_ids: Optional[Sequence[str]] = None,
        group: Optional[str] = None,
    ) -> Dict[str, Any]:
        """One product at one location - or at a whole OWNERSHIP GROUP - and its documents.

        AutoCount's Stock Status with Detail, which is the screen the captain checks stock on:
        On Hand, SO Qty, PO Qty, Available - and under it, every document contributing to those
        totals. "so when a SO is created, it already flows to the outstanding quantity same
        goes for PO created, so it cannot be all quantity are free because we got so many
        outstanding SO, right?"

        The list ADDS UP to the total by construction: both are summed from the same rows, so a
        drill-down can never justify a number the strip did not print. The predicate is the
        shared one - `SalesOrder.status = 'open'` plus `is_open_demand()` - so this screen,
        `/scm/sales-orders` and the netting engine cannot disagree about what is outstanding.
        Deliberately every demand class, because a dealer order occupies the stock as
        completely as a project one does.

        Bounded by nature: one product at one location. The widest on the live book is 289
        sales-order lines (B2155-NL-BLUE at BRW-BB), so this is a page, not a report.

        ORDERED BY DELIVERY DATE, and carrying neither a rank nor a queue state (R5, 27
        August 2026). The rank belongs to the queue screen, which exists to explain a
        ranking; here it competed with the question this list answers, which is what else is
        claiming this stock and when it is wanted. `line_ids` are the lines the drawer was
        opened for - the cell's own contributions - and their rows come back marked
        `is_this_line` so a planner can find themselves in the list.

        **`group` reads the whole SET instead of one bin** (captain, 30 August 2026). Step 1
        of the ladder draws the OWNERSHIP GROUP's pile, not a bin's - a `BRW-IB` line is fed
        by `MWH-IB` stock - so a running balance per bin would answer a question the engine
        never asks. Given `group` (the suffix `IB`, or `pools` for the five site pools) the
        totals and the documents cover every bin of that set, each row carrying the bin it
        sits at, and `bins` states each member's own on hand so the reader can open the walk
        on the pile it actually starts from. The set is resolved through `group_netting`,
        which is the same reader the cell's subtotal prints its net from, so the drill and
        the subtotal cannot come to disagree about who is in the group.
        """
        product = (
            self.db.query(Product).filter(Product.id == product_id).first()
            if product_id
            else None
        )
        warehouse = (
            self.db.query(Warehouse).filter(Warehouse.id == warehouse_id).first()
            if warehouse_id and not group
            else None
        )
        if product is None or (warehouse is None and not group):
            raise AppException(
                status_code=404,
                message="That product or location does not exist.",
                code="stock_detail_not_found",
            )
        # The bins this read covers: one, or the whole set. Codes by id, so every document
        # row can say where it sits without a second lookup.
        set_position = self._set_position(str(product.id), group) if group else None
        if set_position is not None:
            codes = {
                str(entry.warehouse_id): entry.location
                for entry in set_position.by_location
            }
        else:
            codes = {str(warehouse.id): warehouse.warehouse_code or ""}
        target_ids = list(codes)

        owed = demand_qty()
        rows = (
            self.db.query(
                SalesOrderLine.id.label("line_id"),
                SalesOrder.id.label("sales_order_id"),
                SalesOrder.so_number,
                SalesOrder.order_date,
                SalesOrder.internal_note,
                SalesOrder.project_label,
                SalesOrder.demand_class,
                Customer.customer_name,
                Customer.id.label("customer_id"),
                SalesOrderLine.required_date,
                SalesOrderLine.warehouse_id.label("warehouse_id"),
                owed.label("owed"),
                # Who sold it. A purchase row (the SPO list below) has no agent - it is not a
                # sales document - so the column is S/O-only by construction, never a guess.
                SalesAgent.sales_agent.label("agent_code"),
            )
            .join(SalesOrder, SalesOrder.id == SalesOrderLine.sales_order_id)
            .outerjoin(Customer, Customer.id == SalesOrder.customer_id)
            .outerjoin(SalesAgent, SalesAgent.id == SalesOrder.sales_agent_id)
            .filter(
                SalesOrderLine.product_id == product_id,
                SalesOrderLine.warehouse_id.in_(target_ids),
                SalesOrder.status == "open",
                is_open_demand(),
            )
            .order_by(
                SalesOrderLine.required_date.asc().nullslast(),
                SalesOrder.so_number,
                # `id` closes the ordering: two lines of one order wanted on one date would
                # otherwise page in whatever order Postgres felt like.
                SalesOrderLine.id,
            )
            .all()
        )

        # The lines the drawer was opened for, so their rows can say so. Ids, never numbers:
        # one order stands behind a location once per line.
        asking = {str(value) for value in (line_ids or []) if value}

        def _so_row(row) -> Dict[str, Any]:
            return {
                "line_id": str(row.line_id),
                "sales_order_id": str(row.sales_order_id),
                "so_number": row.so_number,
                "customer_name": row.customer_name,
                "customer_id": str(row.customer_id) if row.customer_id else None,
                #: WHERE this claim sits. Always stated, so a group read can column it and a
                #: bin read never has to ask a second time.
                "location": codes.get(str(row.warehouse_id)),
                "agent_code": row.agent_code,
                "project_label": row.project_label or _project_label_from_note(row.internal_note),
                "demand_class": row.demand_class,
                #: AutoCount prints the document's own date and the date it is wanted.
                "doc_date": row.order_date,
                "delivery_date": row.required_date,
                "so_qty": qty_text(_dec(row.owed)),
                #: One of the lines this drawer is planning. The list is otherwise a wall of
                #: other people's documents, and a planner has to be able to find their own.
                "is_this_line": str(row.line_id) in asking,
            }

        # DELIVERY DATE, ascending, which is the order the query already read them in
        # (R5): the earliest claim on the pile leads, and a document with no date lists
        # last rather than first, because "not stated" is not "wanted immediately".
        sales_orders = [_so_row(row) for row in rows]
        # #1362 item 3: a line the drawer was opened for that has nothing open any more
        # (delivered) is no claim on the pile, so the ledger above never lists it and "My
        # line" found nothing. It is listed after the claims, at zero, saying what was
        # delivered - never counted into SO Qty, which sums `rows` alone.
        listed = {row["line_id"] for row in sales_orders}
        missing = [line_id for line_id in asking if line_id not in listed]
        if missing:
            done = (
                self.db.query(
                    SalesOrderLine.id.label("line_id"),
                    SalesOrder.id.label("sales_order_id"),
                    SalesOrder.so_number,
                    SalesOrder.order_date,
                    SalesOrder.internal_note,
                    SalesOrder.project_label,
                    SalesOrder.demand_class,
                    Customer.customer_name,
                    Customer.id.label("customer_id"),
                    SalesOrderLine.required_date,
                    SalesOrderLine.warehouse_id.label("warehouse_id"),
                    owed.label("owed"),
                    SalesOrderLine.qty_delivered,
                    Warehouse.warehouse_code.label("line_location"),
                    SalesAgent.sales_agent.label("agent_code"),
                )
                .join(SalesOrder, SalesOrder.id == SalesOrderLine.sales_order_id)
                .outerjoin(Warehouse, Warehouse.id == SalesOrderLine.warehouse_id)
                .outerjoin(Customer, Customer.id == SalesOrder.customer_id)
                .outerjoin(SalesAgent, SalesAgent.id == SalesOrder.sales_agent_id)
                .filter(
                    SalesOrderLine.id.in_(missing),
                    SalesOrderLine.product_id == product_id,
                    ~is_open_demand(),
                )
                .order_by(SalesOrderLine.required_date.asc().nullslast(), SalesOrderLine.id)
                .all()
            )
            for row in done:
                entry = _so_row(row)
                entry["location"] = entry["location"] or row.line_location
                entry["fulfilled_qty"] = qty_text(_dec(row.qty_delivered))
                sales_orders.append(entry)
        by_location = self.supply.incoming_by_location([product_id], target_ids)
        incoming_rows = [
            (bin_id, ref)
            for bin_id in target_ids
            for ref in by_location.get((str(product_id), bin_id), [])
        ]
        # R-O (3 Sep 2026): what the WALK assumes about a late document, off the same
        # policy row the walk itself reads, so the ledger and the ladder can never disagree
        # about which documents are still being counted on.
        settings = self.supply.fulfilment_settings()
        # Same defaults `supply_assignment.compute_overdue_event` falls back to when the
        # walk itself finds no policy row: `_fulfilment_settings()` returns `{}` on its own
        # defensive except, and `or 0` here used to read that as grace=0/dead=0, which
        # labelled every late-but-alive document "Not counted" while the walk (14/90) still
        # counted it as supply - the ledger and the ladder disagreeing about the same book.
        grace = max(
            int(
                settings.get("overdue_grace_days")
                if settings.get("overdue_grace_days") is not None
                else supply_assignment.DEFAULT_OVERDUE_GRACE_DAYS
            ),
            0,
        )
        dead = max(
            int(
                settings.get("overdue_dead_days")
                if settings.get("overdue_dead_days") is not None
                else supply_assignment.DEFAULT_OVERDUE_DEAD_DAYS
            ),
            0,
        )
        today = date.today()
        assumed_arrival = today + timedelta(days=grace)

        def _assumed(late: Optional[int]) -> Dict[str, Any]:
            days = int(late or 0)
            if days <= 0:
                return {"assumed_date": None, "counted": True}
            if days > dead:
                # Nothing is assumed about a document the walk counts as nothing (R31).
                return {"assumed_date": None, "counted": False}
            return {"assumed_date": assumed_arrival, "counted": True}

        incoming = [
            {
                "spo_number": ref.spo_number,
                "supplier_name": ref.supplier_name,
                "location": codes.get(bin_id),
                "expected_date": ref.arrival_date,
                "spo_qty": qty_text(ref.qty),
                # A promise whose date has passed is still supply and is still counted
                # (captain, 26 Aug: trust the book). It is STATED as overdue so the buyer
                # can see which one to chase, rather than silently dropped or silently
                # read as fresh. Same number the engine's trail names.
                "overdue_days": ref.overdue_days,
                **_assumed(ref.overdue_days),
            }
            for bin_id, ref in sorted(
                incoming_rows,
                key=lambda pair: (
                    pair[1].arrival_date is None,
                    pair[1].arrival_date,
                    pair[1].spo_number,
                ),
            )
        ]

        # A confirmed hold whose OWN line is booked outside this set (R40, 30 Aug): it takes
        # stock out of this pile and appears in no row of this book, so a walk without it
        # counts a pile nobody can draw on. A hold whose line IS in the set is already one of
        # the sales-order rows above, and listing it again would subtract it twice. Only the
        # GROUP reading asks: one bin's drill is read beside its own Available, which does
        # not net holds either.
        holds = [
            {
                "so_number": hold["so_number"],
                "location": codes.get(hold["warehouse_id"]),
                "required_date": hold["required_date"],
                "qty": qty_text(hold["qty"]),
            }
            for hold in (
                self.supply.holds_at_locations([product_id], target_ids) if group else []
            )
            if hold["line_warehouse_id"] not in codes
        ]
        holds.sort(
            key=lambda hold: (
                hold["required_date"] is None,
                hold["required_date"] or date.min,
                hold["so_number"] or "",
            )
        )

        levels = self.supply.stock_levels_by_location([product_id])
        frees = self.supply.free_stock_by_location([product_id])
        helds = self.supply.held_stock_by_location([product_id])
        # Per bin, and then summed - a group read is the set's own position, and the members
        # are stated so the drill can open its walk on the pile each one starts from.
        bins = [
            {
                "warehouse_id": bin_id,
                "location": codes.get(bin_id) or "",
                "qty_on_hand": qty_text(
                    levels.get((str(product_id), bin_id), (_ZERO, _ZERO))[0]
                ),
            }
            for bin_id in sorted(target_ids, key=lambda bid: codes.get(bid) or "")
        ]
        on_hand = sum(
            (levels.get((str(product_id), bid), (_ZERO, _ZERO))[0] for bid in target_ids),
            _ZERO,
        )
        reserved = sum(
            (levels.get((str(product_id), bid), (_ZERO, _ZERO))[1] for bid in target_ids),
            _ZERO,
        )
        so_qty = sum((_dec(row.owed) for row in rows), _ZERO)
        spo_qty = sum((ref.qty for _bid, ref in incoming_rows), _ZERO)
        free = sum((frees.get((str(product_id), bid), _ZERO) for bid in target_ids), _ZERO)
        held = sum((helds.get((str(product_id), bid), _ZERO) for bid in target_ids), _ZERO)
        # PLAN-oi-request-cs-reserve.md 3.9 (section 6, verify item 2): the OI stock grid
        # reuses `CellStockTable`, the SAME per-location matrix the board's cell dialog
        # renders, so this read states its rows in that shape rather than the FE inventing
        # a second one from `bins` alone. Aggregated from the documents already listed
        # above - never a second query. `where` is `group` for every member of a GROUP
        # read (a bare product+group carries no asking line, so this endpoint cannot say
        # which one bin is "its own") and `own` for a plain one-bin read; `net`/`net_of`
        # are stated on a GROUP read only, off the SAME aggregate this method already sums
        # so the table's own subtotal can never disagree with it.
        so_by_wid: Dict[str, Decimal] = {}
        for row in rows:
            wid = str(row.warehouse_id)
            so_by_wid[wid] = so_by_wid.get(wid, _ZERO) + _dec(row.owed)
        spo_by_wid: Dict[str, Decimal] = {}
        for bin_id, ref in incoming_rows:
            spo_by_wid[bin_id] = spo_by_wid.get(bin_id, _ZERO) + _dec(ref.qty)
        locations = [
            {
                "location": codes.get(bin_id) or "",
                "where": "group" if group else "own",
                "product_id": str(product.id),
                "warehouse_id": bin_id,
                # No demand context at this endpoint (no cell, no asking line) - never
                # rendered, `CellStockTable` carries no Demand column.
                "qty": "0",
                "qty_demand": "0",
                "qty_on_hand": qty_text(
                    levels.get((str(product_id), bin_id), (_ZERO, _ZERO))[0]
                ),
                "so_qty": qty_text(so_by_wid.get(bin_id, _ZERO)),
                "spo_qty": qty_text(spo_by_wid.get(bin_id, _ZERO)),
                "available_qty": qty_text(
                    levels.get((str(product_id), bin_id), (_ZERO, _ZERO))[0]
                    - so_by_wid.get(bin_id, _ZERO)
                    + spo_by_wid.get(bin_id, _ZERO)
                ),
                **(
                    {"net": qty_text(on_hand - so_qty + spo_qty), "net_of": group}
                    if group
                    else {}
                ),
            }
            for bin_id in sorted(target_ids, key=lambda bid: codes.get(bid) or "")
        ]
        return {
            "product_id": str(product.id),
            "item_code": product.product_code,
            "description": product.product_name,
            #: A group read names no single bin: it is the SET's position, and `bins` says
            #: which locations that set holds.
            "warehouse_id": str(warehouse.id) if warehouse is not None else None,
            "location": warehouse.warehouse_code if warehouse is not None else None,
            "group": group or None,
            "bins": bins,
            "locations": locations,
            "qty_on_hand": qty_text(on_hand),
            "so_qty": qty_text(so_qty),
            "spo_qty": qty_text(spo_qty),
            # Signed, never clamped: this is where "oversold by 632" is said out loud.
            "available_qty": qty_text(on_hand - so_qty + spo_qty),
            # The engine's own figures, so the other reconciliation still closes.
            "qty_reserved": qty_text(reserved),
            "qty_held_by_decisions": qty_text(held),
            "qty_free": qty_text(free),
            "sales_orders": sales_orders,
            "incoming": incoming,
            "holds": holds,
            # LADDER V8 (R-K): the five site pools' own net and the share kept back for
            # dealers, so the expanded ledger under a SITE POOL section can read its running
            # column as "Available for Project" - the pool's share of each running balance,
            # capped by the same net the walk was bound by (R-D). Stated only on the pools
            # reading: a bin or an ownership group has no dealer share to keep back, and a
            # number there would invite the client to apply the rule where it does not hold.
            **(
                {
                    # The SET's own net, off the position this read's membership came from
                    # (D1) - never `supply.netting()`, whose pile span is the products a
                    # REQUEST has asked about and is therefore empty on a drill-down.
                    "five_pool_net": qty_text(set_position.net),
                    "pool_share_pct": self._pool_share_pct(),
                }
                if (group or "").strip().lower() == POOLS_SET and set_position is not None
                else {}
            ),
        }

    def _set_position(self, product_id: str, group: str) -> Any:
        """One set's whole netted POSITION: the ownership group's, or the five site pools'.

        Read through `group_netting` - the SAME reader the cell's subtotal prints its net
        from - so the documents the drill lists, the membership it lists them over and the
        NET it caps its running column by are all one reading. `planning_only` because the
        flag decides who is in a group (R17): a bin flagged out holds stock no proposal may
        draw, and listing its documents under the group's balance would show a pile the
        ladder cannot spend.

        THE POSITION, not just its bins (D1, captain 3 Sep). `stock_detail` used to take the
        membership from here and then read the NET back off `supply.netting()` - a reader
        whose pile span is the products the REQUEST has already asked about
        (`ProjectSupplyService._pile_facts`). A drill-down asks about no line, so that span
        was empty, every pile read as three zeroes, and `five_pool_net` came back 0 while the
        subtotal beside it printed 142 off the same stock. The ledger then capped "Available
        for Project" at 0 on every row under a subtotal reading 71.

        An unknown group answers with no bins and a zero net, which is the honest reading:
        nothing was found to look at.
        """
        from app.services.scm.group_netting import netting_for_products

        netting = netting_for_products(self.db, [product_id], planning_only=True)
        return (
            netting.pools_net(product_id)
            if (group or "").strip().lower() == POOLS_SET
            else netting.group_net(product_id, group)
        )

    def pile_queue(
        self,
        product_id: str,
        warehouse_id: str,
        line_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """The WHOLE queue at one pile, in the order the stock is actually served in.

        The captain, having been given the top three beside the rung: "I need to know what is
        ahead of me to have the visibility, and why they are ahead of me, meaning I need to know
        their rank also."

        So this is `_pile_book` read out - the SAME list `_attribution` counts `so_qty_ahead`
        from, never a second ranking of the same pile - with each line's rank, the factors and
        the facts behind it, the running total of what is claimed by the time the queue reaches
        it, and (against the line that asked) which factor put it in front.

        A line a confirmed decision covers is absent for the reason `_pile_book` states: its
        claim is already expressed as a hold that came out of the opening stock, and listing it
        again would count the same units twice. `is_covered_excluded` says so per row and is
        false on every row that IS here, so the field means something the day the exclusion is
        made visible rather than being a placeholder.
        """
        product = (
            self.db.query(Product).filter(Product.id == product_id).first()
            if product_id
            else None
        )
        warehouse = (
            self.db.query(Warehouse).filter(Warehouse.id == warehouse_id).first()
            if warehouse_id
            else None
        )
        if product is None or warehouse is None:
            raise AppException(
                status_code=404,
                message="That product or location does not exist.",
                code="pile_queue_not_found",
            )

        book = self.supply.pile_book(str(product.id), str(warehouse.id))
        asked = next(
            (row for row in book if line_id and row["line_id"] == str(line_id)), None
        )
        names = self._customer_names(
            {row["customer_id"] for row in book if row.get("customer_id")}
        )

        asked_position = (
            next(
                (
                    position
                    for position, row in enumerate(book, start=1)
                    if row["line_id"] == asked["line_id"]
                ),
                None,
            )
            if asked is not None
            else None
        )

        lines: List[Dict[str, Any]] = []
        running = _ZERO
        for position, row in enumerate(book, start=1):
            running += _dec(row["open_qty"])
            raw = priority.raw_facts_for_demand_row(row)
            lines.append(
                {
                    "position": position,
                    "line_id": row["line_id"],
                    "sales_order_id": row.get("sales_order_id"),
                    "so_number": row["so_number"],
                    "line_no": row.get("line_no"),
                    "customer_name": names.get(row.get("customer_id") or ""),
                    "qty": qty_text(_dec(row["open_qty"])),
                    "required_date": row["required_date"],
                    "order_date": row.get("order_date"),
                    "payment_terms_days": row.get("payment_terms_days"),
                    "demand_class": row.get("demand_class"),
                    "rank_score": round(float(row.get("rank_score") or 0.0), 6),
                    "rank_factors": [
                        {**factor.as_dict(), "raw": raw.get(factor.key)}
                        for factor in (row.get("factors") or [])
                    ],
                    # Why this line is in front of the one that asked. Null for the asked line
                    # itself, which outranks nobody; null for every row BEHIND it, which is
                    # not in front of anything and must not claim a reason for being; and
                    # null when nobody asked.
                    "leading_factor": (
                        None
                        if asked_position is None or position >= asked_position
                        else _leading_factor(asked, row)
                    ),
                    #: What the queue has claimed by the time it has served this row, this row
                    #: included. Read down the column and the point the pile runs out is
                    #: visible without arithmetic.
                    "cumulative_ahead_qty": qty_text(running),
                    "is_this_line": asked is not None and row["line_id"] == asked["line_id"],
                    "is_covered_excluded": False,
                }
            )

        return {
            "product_id": str(product.id),
            "item_code": product.product_code,
            "description": product.product_name,
            "warehouse_id": str(warehouse.id),
            "location": warehouse.warehouse_code,
            #: What the pile held before the queue drew on it: the same opening figure the
            #: trail's own rung prints.
            "qty_free_opening": qty_text(
                self.supply.free_stock_by_location([str(product.id)]).get(
                    (str(product.id), str(warehouse.id)), _ZERO
                )
            ),
            "this_line_position": (
                next(
                    (line["position"] for line in lines if line["is_this_line"]),
                    None,
                )
            ),
            "policy_name": self._policy(None)[0],
            "lines": lines,
        }

    def _customer_names(self, customer_ids: Iterable[str]) -> Dict[str, str]:
        """Ids to names in one query. The queue prints a customer, never an id."""
        ids = [cid for cid in customer_ids if cid]
        if not ids:
            return {}
        rows = (
            self.db.query(Customer.id, Customer.customer_name)
            .filter(Customer.id.in_(ids))
            .all()
        )
        return {str(row.id): row.customer_name for row in rows}

    # ---------------------------------------------------------------- policy

    def _policy(
        self, preview_policy: Optional[str]
    ) -> Tuple[str, dict, dict, bool]:
        """Which policy ranks this board, and whether it is the live one.

        A preview is a what-if, never a second active policy: the partial unique index allows
        exactly one active row, and two policies for two moments is the defect
        `app/services/scm/priority.py` exists to prevent. So a preview READS a named row (or
        the module's own board preview when no row carries that name) and persists nothing.

        `1` / `true` means the module's own board preview, and that translation lives HERE
        rather than in the route: the parameter's meaning has one owner, or the tested service
        and the shipped endpoint come to disagree about what "preview" was asked for.
        """
        if (preview_policy or "").strip().lower() in {"1", "true"}:
            preview_policy = priority.BOARD_PREVIEW_NAME
        if not preview_policy:
            active = priority.active_policy(self.db)
            weights, class_weights = priority.policy_weights(active)
            name = active.name if active else "No policy configured (document sequence)"
            return name, weights, class_weights, False

        named = priority.policy_by_name(self.db, preview_policy)
        if named is not None:
            weights, class_weights = priority.policy_weights(named)
            # Named ROW, so whether it is a preview is a fact about the row rather than about
            # the request: asking for the policy that happens to be live is not a what-if, and
            # labelling it "Preview, not live" would be a lie on screen.
            return named.name, weights, class_weights, not bool(named.is_active)
        if preview_policy == priority.BOARD_PREVIEW_NAME:
            return (
                priority.BOARD_PREVIEW_NAME,
                dict(priority.BOARD_PREVIEW_WEIGHTS),
                dict(priority.BOARD_PREVIEW_CLASS_WEIGHTS),
                True,
            )
        raise AppException(
            status_code=404,
            message=f"No priority policy named '{preview_policy}'.",
            code="priority_policy_not_found",
        )

    # ------------------------------------------------------------ the demand

    def _demand_rows(
        self,
        so_numbers: Sequence[str],
        *,
        reopened_by_change: Optional[AbstractSet[str]] = None,
    ) -> List[_Row]:
        """Every UNDECIDED line of the selected project-class sales orders.

        NOT "still owed" (14 September 2026 ruling, from SO421404). The board asks whether
        anybody decided where a line's stock comes from, and delivery is not a decision: an
        order that read Completed, three of three delivered and had never been planned was
        invisible here, so no ORDER row ever reached purchasing and nothing was bought back.
        The header admits `closed` orders beside `open` ones and the line predicate is
        `is_undecided_demand()`; both are the board's alone. `is_open_demand()` stays exactly
        as it was for the netting engine, the reorder plan and the worklist (AC-S2-11), and
        the two diverge on purpose.

        A `cancelled` ORDER is still refused outright. Its lines are owed to nobody, and the
        line predicate cannot see an order's own status.

        Company scoping is the session's, injected into every SELECT touching a scoped model -
        so an order of another company is simply not there.
        """
        if not so_numbers:
            return []
        records = (
            self.db.query(SalesOrderLine, SalesOrder, Product, Warehouse, SalesAgent)
            .join(SalesOrder, SalesOrder.id == SalesOrderLine.sales_order_id)
            .join(Product, Product.id == SalesOrderLine.product_id)
            .outerjoin(Warehouse, Warehouse.id == SalesOrderLine.warehouse_id)
            # Who sold it. One join, on the same read that already pulls the whole board's
            # demand - the captain asked for the agent "everywhere", and a per-row lookup
            # would be one query per line rather than one for the board.
            .outerjoin(SalesAgent, SalesAgent.id == SalesOrder.sales_agent_id)
            .filter(
                SalesOrder.so_number.in_(list(so_numbers)),
                SalesOrder.status.in_(["open", "closed"]),
                SalesOrder.demand_class == "project",
                is_undecided_demand(),
            )
            .all()
        )
        if not records:
            return []

        # The ids are already inside the caller's company scope (they came off scoped rows),
        # which is what makes the unscoped raw read of the terms column safe.
        customers = self._customers(
            {str(order.customer_id) for _l, order, _p, _w, _a in records if order.customer_id}
        )
        terms = priority.payment_terms_by_customer(self.db, list(customers.keys()))
        # How the confirm endpoint names these lines, when their order has been adopted.
        # Resolved before the line numbers, which prefer the mirror's own numbering.
        self._addressing = self._mirror_addressing([str(line.id) for line, *_r in records])
        line_numbers = self._line_numbers(records)
        # What an active revision already froze, per CORE line: the decision, and beside it
        # the proposal the engine had made at that moment (AC-D1), and (AC-DT-2) who
        # confirmed it. One read for all three.
        frozen, frozen_proposals, frozen_decided_by = self._frozen_decisions()
        # What purchasing was already TOLD, per CORE line. One read for the whole board too.
        # The active decisions go in with it: a refusal is only news until CS answers it
        # (see `_order_inquiries`).
        inquiries = self._order_inquiries(
            [str(line.id) for line, *_r in records],
            decided_at={
                core_id: (decision or {}).get("confirmed_at")
                for core_id, decision in frozen.items()
            },
        )
        # What another order borrowed OFF each of these lines. One read for the whole board.
        lent = self._lent_from([str(line.id) for line, *_r in records])

        rows: List[_Row] = []
        for line, order, product, warehouse, agent in records:
            customer_id = str(order.customer_id) if order.customer_id else None
            row = _Row(
                line_id=str(line.id),
                sales_order_id=str(order.id),
                so_number=order.so_number,
                customer_id=customer_id,
                customer_name=customers.get(customer_id or ""),
                agent_code=agent.sales_agent if agent else None,
                agent_label=agent.person_label if agent else None,
                # Which warehouse-suffix ownership group this agent's stock lives in. The
                # cell's stock table lists the WHOLE group, because "can I fulfil this" is a
                # question about the three BB warehouses, not about the one the line names.
                agent_location_group=agent.location_group if agent else None,
                project_label=_project_label(order),
                order_date=order.order_date,
                line_no=line_numbers[str(line.id)],
                # #1362 item 5: AutoCount's own line number off the core row, the "No." the
                # sales order's Lines tab shows. `line_no` above stays the ADDRESS (the
                # draft key and the confirm endpoint read it); this is what a person reads.
                so_line_no=line.line_no,
                item_code=product.product_code,
                product_id=str(line.product_id),
                # The PLAN quantity, not the still-owed one: a delivered unit nobody
                # sourced is a unit to put back, and the ladder is asked for the whole
                # figure. Imported from the supply service rather than restated, so the
                # board's ask and `_LineFacts.open_qty` cannot come to disagree.
                qty=plan_qty_of(line),
                # What is still owed the CUSTOMER, which the board now says separately
                # because it and the planned quantity have stopped being the same number.
                qty_outstanding=_open_of(line),
                qty_ordered=_dec(line.qty_ordered),
                qty_delivered=_dec(line.qty_delivered),
                required_date=line.required_date,
                warehouse_id=str(line.warehouse_id) if line.warehouse_id else None,
                location=warehouse.warehouse_code if warehouse else None,
                # R17: read here, off the warehouse row the demand query already joins, so
                # the board's verdict and the supply service's `unplannable_reason` come
                # from the one predicate rather than from two lookups that could disagree.
                # ACTIVE and flagged off - an inactive bin keeps whatever verdict it carried
                # before the flag existed (`outside_fulfilment_planning`).
                outside_planning=outside_fulfilment_planning(warehouse),
                priority=line.priority,
                demand_class=order.demand_class,
                # R7 (query-count): off the SAME `line` row this read already fetched -
                # see `_Row.__slots__`'s own docstring.
                source_ref=line.source_ref,
                company_id=str(line.company_id) if line.company_id else None,
            )
            addressing = self._addressing.get(str(line.id), {})
            # Null when nobody has adopted this sales order: there is no record to confirm
            # against, and that IS the state of a not-started row. An invented id or an
            # absent key would both make the screen guess.
            row.project_sales_order_id = addressing.get("project_sales_order_id")
            row.project_line_id = addressing.get("project_line_id")
            row.project_key = _project_key(row.project_label)
            row.payment_terms_days = terms.get(customer_id or "")
            # Covered or not, and by what. A line an active decision covers is not planned
            # again: it states the composition that was frozen for it (13.4).
            row.decision = frozen.get(str(line.id))
            # AC-DT-2: who confirmed the decision that covers this line, by name. None
            # on an uncovered line, same as `row.decision` itself.
            row.decided_by_name = frozen_decided_by.get(str(line.id))
            # What the engine had suggested for it when that decision was taken - the
            # STARTING value only. `_allocate` replaces it with the live ladder on every
            # line: the uncovered ones from the board's own walk, the covered ones from a
            # walk of their order today (`_suggest_live_for_covered`). The snapshot stays
            # in `line_snapshots` for the record; it stopped being the on-screen
            # suggestion when a stale one sent "BRW 30" to the confirm three times over.
            row.proposed_components = frozen_proposals.get(str(line.id))
            # What was already asked for, and how far purchasing got with it. The decision
            # beside it is what was PROMISED; these are the two halves of one answer, and a
            # board that carried only the first sent the planner to another screen for the
            # second.
            row.order_inquiry = inquiries.get(str(line.id))
            # Purchasing was TOLD to buy for this line and nobody has withdrawn or refused
            # that instruction, so it is decided on the buying side: read-only, naming the
            # inquiry, never proposed for again (AC-S2-7). Popped rather than sent: it is
            # how that read answers this question, not a field the screen has a use for.
            told_by_the_book = bool((row.order_inquiry or {}).pop("_decides_line", False))
            # THE BOOK MOVING A LINE BEATS PURCHASING HAVING BEEN TOLD (owner's ruling,
            # 14 Sep 2026). A pending planning-change row says the book has moved this line
            # since the instruction was written, and apply CANCELS and UNLINKS that placed
            # row - so it is stale by definition and cannot be what decides the line. Left
            # decided, the board stopped proposing for the very line the change re-opened.
            row.inquiry_decided = told_by_the_book and str(line.id) not in (
                reopened_by_change or frozenset()
            )
            row.lent_to = lent.get(str(line.id), [])
            rows.append(row)
        return rows

    def _cancelled_pending_change_rows(
        self,
        so_numbers: Sequence[str],
        *,
        admitted: Optional[Mapping[str, _Row]] = None,
    ) -> List[_Row]:
        """R3 (13 Sep browser walk, widened on the second re-walk): a line the book has
        CLOSED OUT - cancelled, or otherwise no longer open demand - still has a home on
        the board while a change row about it is still PENDING.

        PURE BOOK STATUS (S2, review round): `line_status != 'open' OR demand_qty() <= 0`,
        deliberately NOT `~is_open_demand()`. `is_open_demand()` also requires
        `purchasing_status != 'covered'`, and a covered-but-still-open, still-owed line is
        a PERSON's ruling ("no purchase needed") - never "the book closed this line". That
        line belongs in ordinary demand (`_demand_rows` excludes it correctly, via
        `is_open_demand()`), and must NOT also read as a non-open row here: the two
        predicates are deliberately NOT exact complements, this one and `is_open_demand()`
        agree on every OTHER case (closed line status, or nothing left owed), and diverge
        only on `purchasing_status == 'covered'`. NOT scoped to `kind == "cancelled"`
        alone: a `product_changed` row on a line since fully delivered (closed,
        `qty_delivered == qty_ordered`, an honest drift - see `_carry_snapshot_has_drifted`)
        is just as invisible to `_demand_rows` and just as undecided, so the same book-
        status predicate admits it here too, whatever kind its own pending row carries.
        Read separately rather than folded into `_demand_rows`'s shared predicate
        (`is_open_demand()` is read by the netting engine and the worklist too, and
        neither of those wants a closed-out line back): the ladder never runs for one
        (`qty` is forced to zero, `qty_delivered` is read straight off the core line,
        UNCHANGED by a product change - the delivered figure is a fact about the OLD
        product, not something this read invents), and `_contribution` prints it read-only
        - the batch it is pending in, nothing to compose. `cancelled` is true only when the
        row's own `kind` actually is "cancelled" (R3): a product change is a different
        verdict and must not wear the same pill. Absent again the moment that row applies
        (`applied_state` stops being `pending`).
        """
        if not so_numbers:
            return []
        from app.models.planning_change import PlanningChangeBatch, PlanningChangeRow
        from app.services.planning_change_service import PLANNING_CHANGE_STATE_PENDING

        records = (
            self.db.query(
                SalesOrderLine, SalesOrder, ProjectSalesOrderLine, PlanningChangeRow,
                Warehouse, SalesAgent,
            )
            .join(SalesOrder, SalesOrder.id == SalesOrderLine.sales_order_id)
            .join(
                ProjectSalesOrderLine,
                ProjectSalesOrderLine.core_sales_order_line_id == SalesOrderLine.id,
            )
            .join(
                PlanningChangeRow,
                PlanningChangeRow.project_line_id == ProjectSalesOrderLine.id,
            )
            .join(PlanningChangeBatch, PlanningChangeBatch.id == PlanningChangeRow.batch_id)
            .outerjoin(Warehouse, Warehouse.id == SalesOrderLine.warehouse_id)
            .outerjoin(SalesAgent, SalesAgent.id == SalesOrder.sales_agent_id)
            .filter(
                SalesOrder.so_number.in_(list(so_numbers)),
                # THE SAME HEADER `_demand_rows` ADMITS. A closed order's pending change was
                # invisible here while the board shows the order itself, so the one line
                # this read exists for - the one ordinary demand cannot see - vanished on
                # exactly the completed orders this lane put on screen.
                SalesOrder.status.in_(["open", "closed"]),
                SalesOrder.demand_class == "project",
                (SalesOrderLine.line_status != "open") | (demand_qty() <= 0),
                PlanningChangeRow.applied_state == PLANNING_CHANGE_STATE_PENDING,
                PlanningChangeBatch.applied_at.is_(None),
            )
            .all()
        )
        if not records:
            return []
        # Same helper `_demand_rows` reads its customer names off - one query for the whole
        # set rather than one per row.
        customers = self._customers(
            {str(order.customer_id) for _cl, order, *_r in records if order.customer_id}
        )
        rows: List[_Row] = []
        already_admitted = admitted or {}
        for core_line, order, project_line, change_row, warehouse, agent in records:
            # ONE CONTRIBUTION PER LINE. Since the board's predicate widened (14 Sep 2026)
            # this read and `_demand_rows` genuinely overlap - a closed-by-delivery line is
            # "not open" by this read's book-status test AND undecided by the board's - so a
            # second row here would put the same key on screen twice, once planned and once
            # read-only at zero. The pending batch is stamped on the row that IS there
            # instead, which is the fact this read exists to carry.
            standing = already_admitted.get(str(core_line.id))
            if standing is not None:
                # The BATCH only. `cancelled` is left alone: on an admitted row it is the
                # read-only-at-zero state this read gives a line `_demand_rows` cannot see,
                # and stamping it on a line the ladder has just proposed for would print
                # "Cancelled" over a live Buy.
                standing.pending_change_batch_id = str(change_row.batch_id)
                continue
            customer_id = str(order.customer_id) if order.customer_id else None
            rows.append(
                _Row(
                    line_id=str(core_line.id),
                    sales_order_id=str(order.id),
                    so_number=order.so_number,
                    customer_id=customer_id,
                    customer_name=customers.get(customer_id or ""),
                    agent_code=agent.sales_agent if agent else None,
                    agent_label=agent.person_label if agent else None,
                    # Same field `_demand_rows` sets, same reason (nit, review round): the
                    # cell's stock table reads the whole warehouse-suffix group off this,
                    # and a non-open row is shown beside ordinary demand on the same board.
                    agent_location_group=agent.location_group if agent else None,
                    line_no=change_row.line_no,
                    so_line_no=core_line.line_no,
                    item_code=change_row.item_code,
                    product_id=str(core_line.product_id) if core_line.product_id else None,
                    project_sales_order_id=str(project_line.project_sales_order_id),
                    project_line_id=str(project_line.id),
                    qty=_ZERO,
                    qty_ordered=core_line.qty_ordered,
                    qty_delivered=core_line.qty_delivered,
                    required_date=core_line.required_date,
                    warehouse_id=str(core_line.warehouse_id) if core_line.warehouse_id else None,
                    location=warehouse.warehouse_code if warehouse else None,
                    bucket_key=NO_DATE_BUCKET,
                    cancelled=change_row.kind == "cancelled",
                    pending_change_batch_id=str(change_row.batch_id),
                    source_ref=core_line.source_ref,
                    company_id=(
                        str(core_line.company_id) if core_line.company_id else None
                    ),
                )
            )
        return rows

    def _crm_cancelled_rows(
        self, so_numbers: Sequence[str], *, shown: AbstractSet[str]
    ) -> List[_Row]:
        """SO-CANCEL-LINE: the lines CS transferred to Cancel SO (`cancelled_at` set). The
        board's own predicate drops a `cancelled` line, so without this the line CS just
        cancelled would vanish from the board it was cancelled on. Read-only at zero."""
        if not so_numbers:
            return []
        records = (
            self.db.query(SalesOrderLine, SalesOrder, ProjectSalesOrderLine, Product, User)
            .join(SalesOrder, SalesOrder.id == SalesOrderLine.sales_order_id)
            .join(
                ProjectSalesOrderLine,
                ProjectSalesOrderLine.core_sales_order_line_id == SalesOrderLine.id,
            )
            .join(Product, Product.id == SalesOrderLine.product_id)
            .outerjoin(User, User.id == SalesOrderLine.cancelled_by_id)
            .filter(
                SalesOrder.so_number.in_(list(so_numbers)),
                SalesOrder.status.in_(["open", "closed"]),
                SalesOrder.demand_class == "project",
                SalesOrderLine.line_status == "cancelled",
                SalesOrderLine.cancelled_at.isnot(None),
            )
            .all()
        )
        records = [r for r in records if str(r[0].id) not in shown]
        if not records:
            return []
        customers = self._customers(
            {str(order.customer_id) for _cl, order, *_r in records if order.customer_id}
        )
        rows: List[_Row] = []
        for core_line, order, project_line, product, user in records:
            customer_id = str(order.customer_id) if order.customer_id else None
            rows.append(
                _Row(
                    line_id=str(core_line.id),
                    sales_order_id=str(order.id),
                    so_number=order.so_number,
                    customer_id=customer_id,
                    customer_name=customers.get(customer_id or ""),
                    line_no=project_line.line_no,
                    so_line_no=core_line.line_no,
                    item_code=product.product_code,
                    product_id=str(core_line.product_id) if core_line.product_id else None,
                    project_sales_order_id=str(project_line.project_sales_order_id),
                    project_line_id=str(project_line.id),
                    qty=_ZERO,
                    qty_ordered=core_line.qty_ordered,
                    qty_delivered=core_line.qty_delivered,
                    required_date=core_line.required_date,
                    warehouse_id=str(core_line.warehouse_id) if core_line.warehouse_id else None,
                    bucket_key=NO_DATE_BUCKET,
                    cancelled=True,
                    cancelled_at=core_line.cancelled_at,
                    cancelled_by_name=(
                        (getattr(user, "name", None) or "Unknown user")
                        if user is not None
                        else None
                    ),
                    cancel_reason=core_line.cancel_reason,
                    source_ref=core_line.source_ref,
                    company_id=str(core_line.company_id) if core_line.company_id else None,
                )
            )
        return rows

    def _lent_from(self, core_line_ids: Sequence[str]) -> Dict[str, List[Dict[str, Any]]]:
        """What another sales order borrowed OFF each of these lines (AC-L6).

        The captain, 25 August 2026: the donor's cell reads "71 lent to SO415472". A borrow
        was visible only on the taking side, so the agent whose stock moved found out when
        the delivery did not.

        Read off the ACTIVE decisions' frozen compositions, because that is where a group
        borrow names its donor line (`donor_core_line_id`); `so_line_allocations` records
        the warehouse the stock came from but not the line it was promised to. This is the
        REVERSE direction of `_frozen_decisions` - the borrowing order is usually not in
        this board's own selection - so it reads every active revision rather than the
        selection's own, in one query, and filters in Python.

        One row per project sales order, and a group borrow is rare, so the scan is small.
        The trigger for a JSONB index on `donor_core_line_id`: the day this read shows up in
        a board's own timings.
        """
        wanted = {str(line_id) for line_id in core_line_ids}
        if not wanted:
            return {}
        decisions = (
            self.db.query(SOSupplyDecision, ProjectSalesOrder)
            .join(
                ProjectSalesOrder,
                ProjectSalesOrder.id == SOSupplyDecision.project_sales_order_id,
            )
            .filter(SOSupplyDecision.state == DECISION_ACTIVE)
            .all()
        )
        out: Dict[str, List[Dict[str, Any]]] = {}
        for decision, order in decisions:
            so_number = order.autocount_doc_no or order.provisional_ref
            for snapshot in decision.line_snapshots or []:
                for component in (snapshot or {}).get("components") or []:
                    donor = component.get("donor_core_line_id")
                    if not donor or str(donor) not in wanted:
                        continue
                    qty = _dec(component.get("qty"))
                    if qty <= _ZERO:
                        continue
                    out.setdefault(str(donor), []).append(
                        {
                            "qty": qty_text(qty),
                            "so_number": so_number,
                            "line_no": (snapshot or {}).get("line_no"),
                        }
                    )
        for rows in out.values():
            rows.sort(key=lambda r: (str(r["so_number"] or ""), r["line_no"] or 0))
        return out

    def _order_inquiries(
        self,
        core_line_ids: Sequence[str],
        *,
        decided_at: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """The instruction covering each core line, keyed by that line.

        Each entry also carries `_decides_line`, popped by the caller the way
        `with_order_inquiries` pops its own `_is_amendment`: whether a LIVE instruction no
        board decision raised still stands on this line, which is what holds the row
        read-only (AC-S2-7). Answered here because these are the same rows - the SQL twin
        is `demand.live_inquiry_core_line_ids()`, which the Sales Orders list counts
        `planned_lines` by, and a second statement per build would breach the query-count
        bound (`test_a_board_of_76_lines_does_not_scale_its_query_count_with_the_line_
        count`). Two spellings of one rule: change one and change the other.

        Through the mirror, the same link `_mirror_addressing` traverses:
        `projects.order_inquiry_rows.so_line_id` -> `projects.sales_order_lines` ->
        `core_sales_order_line_id`. A partial unique index makes that at most one project
        line per core line, so the join cannot multiply a board row.

        `decided_at` is when the ACTIVE revision covering each line was confirmed, where
        there is one. It decides nothing about the instruction; it is read only to drop a
        refusal CS has already answered (see the loop at the end).

        ONE query for the whole board. Ordered oldest first with the LAST writer winning,
        because a line routinely carries several rows - the G2 cascade splits a placed
        allocation from its raised remainder, and an amendment raises a second inquiry
        entirely - and what the column answers is "what is the current instruction". The
        ordering ends on the row id so a same-timestamp tie resolves the same way on every
        read: inside one transaction `now()` is a constant, so `created_at` is no tiebreaker.

        That tiebreaker is a random uuid, though, so "last writer" cannot be trusted to
        pick the LIVE row when a refused row and the row CS raised in its place share a
        `created_at`. An answered refusal is therefore taken out of the running explicitly
        rather than left to lose the coin flip - see `_refusal_answered`.

        A cancelled row is never a "last writer" either - it is skipped before it can seed
        or overwrite an entry, so a withdrawn line falls back to whatever older row still
        stands, or to no entry at all when none does.
        """
        if not core_line_ids:
            return {}
        rejecter = aliased(User)
        rows = (
            self.db.query(
                ProjectSalesOrderLine.core_sales_order_line_id,
                OrderInquiry.inquiry_no,
                # S6 (`PLAN-board-oi-mechanical-22sep.md`, AC-B6-15): the HEADER's own id,
                # beside the ROW's id read further down - the List view's OI column needs
                # the pair to address the exact row, and prints neither.
                OrderInquiry.id,
                OrderInquiryRow.state,
                # The handshake, so a REJECTED line says who refused it and why
                # (`PLAN-scm-oi-handshake.md`, AC-H6). The line is undecided again by then
                # - purchasing's rejection uncovers it - and a cell that only went back to
                # blank would tell CS nothing about why.
                OrderInquiryRow.ack_state,
                OrderInquiryRow.rejected_at,
                OrderInquiryRow.rejected_reason,
                rejecter.name,
                # Whose instruction it is. A row a confirmation raised carries its
                # decision; the migrated sheet's rows (#875) carry none, and only those
                # decide a line the board would otherwise propose for again.
                OrderInquiryRow.supply_decision_id,
                # AC-RL-07 (`PLAN-oi-replan-received-links.md`): what backs the
                # instruction, and whether it was itself redirected off a document that
                # had already landed (AC-RL-10) - the row id addresses `links_for_rows`
                # below, `redirected_to_pool` reaches the wire as-is.
                OrderInquiryRow.id,
                OrderInquiryRow.redirected_to_pool,
            )
            .select_from(OrderInquiryRow)
            .join(
                ProjectSalesOrderLine,
                ProjectSalesOrderLine.id == OrderInquiryRow.so_line_id,
            )
            .join(OrderInquiry, OrderInquiry.id == OrderInquiryRow.order_inquiry_id)
            .outerjoin(rejecter, rejecter.id == OrderInquiryRow.rejected_by)
            .filter(ProjectSalesOrderLine.core_sales_order_line_id.in_(list(core_line_ids)))
            .order_by(OrderInquiryRow.created_at.asc(), OrderInquiryRow.id.asc())
            .all()
        )
        answered = decided_at or {}

        def _refusal_answered(core_key: str, rejected_at: Optional[datetime]) -> bool:
            """Has CS decided this line again SINCE purchasing refused it?

            `>=` and not `>`, deliberately. The two instants land on the same tick often
            enough to be ordinary rather than exotic - `now()` is frozen for the length of
            a transaction, and both stamps are written microseconds apart - and a refusal
            that outlives its own answer by one tick reads as an open objection on a line
            somebody has already dealt with. Equal means answered.

            The revision the REJECT itself writes cannot be mistaken for that answer: it
            uncovers the line, so it is not among the covering decisions the caller reads
            `decided_at` off (`_frozen_decisions`), and a line it left uncovered has no
            entry here at all.
            """
            decided = answered.get(core_key)
            if decided is None or rejected_at is None:
                return False
            return decided >= rejected_at

        # The CURRENT instruction per line, last writer winning - except that a refusal CS
        # has already answered is not a current instruction, so it never outranks a live
        # row on the same line however the two happened to sort. It still seeds the entry
        # when it is the only row the line has: the cell keeps the inquiry number it was
        # last told about rather than going blank.
        out: Dict[str, Dict[str, Any]] = {}
        # When an entry was seeded by an ANSWERED refusal, the instant that refusal was
        # written. `None` for an entry seeded by a live row, which no refusal outranks.
        # Two answered refusals on one line used to leave the FIRST one's inquiry number
        # standing, because the second was skipped wholesale; the cell should show the
        # inquiry it was last told about.
        answered_refusal_at: Dict[str, Optional[datetime]] = {}
        # The NEWEST LIVE row per line, and its own payload. A line whose newest row was
        # cancelled can still carry one that stands, and when it does that row is both what
        # decides the line AND what the cell must name - a number off the cancelled row
        # beside a read-only "decided" would send CS to an instruction nobody holds.
        live_entry: Dict[str, Dict[str, Any]] = {}
        for (
            core_id, inquiry_no, inquiry_id, state, ack_state, rejected_at, _reason, _name,
            supply_decision_id, row_id, redirected_to_pool,
        ) in rows:
            core_key = str(core_id)
            if (
                supply_decision_id is None
                and state != INQUIRY_CANCELLED
                and ack_state != ACK_REJECTED
            ):
                # Rows arrive oldest first, so the last one seen is the newest.
                live_entry[core_key] = {
                    "inquiry_no": inquiry_no,
                    "inquiry_id": str(inquiry_id) if inquiry_id else None,
                    "state": state,
                    "ack_state": ack_state,
                    "rejected_reason": None,
                    "rejected_by_name": None,
                    "_row_id": str(row_id),
                    "redirected": bool(redirected_to_pool),
                }
            if state == INQUIRY_CANCELLED:
                # A cancelled row never becomes the column's entry, not even to seed one
                # over a blank cell - so a line whose only surviving row is cancelled reads
                # "-" rather than a number nobody holds. `live_entry` above already excludes
                # it; this excludes it from last-writer-wins too, so an older row that is
                # still live (or still-open refusal) wins the cell instead.
                continue
            answered_refusal = ack_state == ACK_REJECTED and _refusal_answered(
                core_key, rejected_at
            )
            if answered_refusal and core_key in out:
                previous = answered_refusal_at.get(core_key)
                # `rejected_at` is never None inside this branch: `_refusal_answered`
                # answers False without one.
                if previous is None or rejected_at <= previous:
                    continue
            out[core_key] = {
                "inquiry_no": inquiry_no,
                "inquiry_id": str(inquiry_id) if inquiry_id else None,
                "state": state,
                # NULL once CS has answered the refusal. The entry is still seeded from the
                # row - the cell keeps the inquiry number it was last told about - but the
                # objection's own word is not repeated: with it the grid printed "Rejected
                # by purchasing: no reason given" on a line somebody had already re-decided,
                # which is the one reading the captain's 27 August rule exists to stop.
                "ack_state": None if answered_refusal else ack_state,
                "rejected_reason": None,
                "rejected_by_name": None,
                # AC-RL-07: this ROW's own id (to address `links_for_rows`) and whether
                # IT was redirected - popped/resolved below, never sent as `_row_id`.
                "_row_id": str(row_id),
                "redirected": bool(redirected_to_pool),
            }
            answered_refusal_at[core_key] = rejected_at if answered_refusal else None
        # The REFUSAL is read off the line rather than off its current row, and that is
        # the difference between the cell saying why it came back and saying nothing. A
        # line routinely carries several rows - an order back beside the order, an
        # amendment's own - so the newest one is frequently not the one purchasing
        # refused, and last-wins would then drop the only explanation CS has. The rows
        # arrive oldest first, so the LATEST refusal is the one left standing here.
        #
        # And it stands only until CS answers it (the captain, 27 Aug): a refusal is a
        # line coming BACK to them, so once they have decided it again - an active
        # revision covering the line, confirmed after the refusal - the cell is about that
        # decision and not about the objection that prompted it. A flag that outlived the
        # answer would read as an open refusal on a line somebody had already dealt with.
        for (
            core_id, _inquiry_no, _inquiry_id, _state, ack_state, rejected_at, reason, name,
            _decision_id, _row_id, _redirected_to_pool,
        ) in rows:
            if ack_state != ACK_REJECTED:
                continue
            entry = out.get(str(core_id))
            if entry is None:
                continue
            if _refusal_answered(str(core_id), rejected_at):
                continue
            entry["ack_state"] = ACK_REJECTED
            entry["rejected_reason"] = reason
            entry["rejected_by_name"] = name
        # A LINE WITH A LIVE INSTRUCTION IS ABOUT THAT INSTRUCTION. It replaces whatever
        # last-wins left standing, so the number the cell prints is the row that decides the
        # line and not a cancelled or refused one that happened to be written after it. A
        # refusal recorded above is dropped with it, for the same reason an answered one is:
        # the line has a live instruction, so the objection is not what it is about.
        for core_key, live in live_entry.items():
            out[core_key] = {**live, "_decides_line": True}
        for core_key, entry in out.items():
            entry.setdefault("_decides_line", False)
        # AC-RL-07 (`PLAN-oi-replan-received-links.md`): what is BEHIND the instruction,
        # not only its number - the winning row's own documents and whether it was
        # itself redirected off one that had already landed. Read through `links_for_
        # rows`, the ONE reader every other surface (the worklist, the SCM sales-order
        # detail) already uses - no second query shape for "where is this linked".
        from app.services.project_order_inquiry_service import ProjectOrderInquiryService

        row_ids = [entry["_row_id"] for entry in out.values() if entry.get("_row_id")]
        links_by_row = (
            ProjectOrderInquiryService(self.db).links_for_rows(row_ids) if row_ids else {}
        )
        for entry in out.values():
            row_id = entry.pop("_row_id", None)
            # S6 (AC-B6-15): the same id, now on the WIRE as well - the List view's OI
            # column addresses `?row=<row_id>` with it. Read off `_row_id` at the moment
            # it is popped so the two can never name different rows.
            entry["row_id"] = row_id
            entry["documents"] = [
                {
                    "document": link["document"],
                    "kind": link["kind"],
                    "received": bool(link.get("received")),
                }
                for link in links_by_row.get(row_id, [])
            ]
        return out

    def _frozen_decisions(
        self,
    ) -> Tuple[
        Dict[str, Dict[str, Any]],
        Dict[str, Optional[List[Dict[str, Any]]]],
        Dict[str, Optional[str]],
    ]:
        """What each ACTIVE revision froze, keyed by the CORE line it covers.

        Three maps off one read: the DECISION, the PROPOSAL the engine had made when that
        decision was taken (AC-D1), and (AC-DT-2, `PLAN-oi-decision-trail-ui.md`) who
        confirmed it, by name. The second is `None` for a revision written before the
        proposal was frozen - which is "not recorded", not "the engine suggested nothing".

        The core line id is the key because that is what a snapshot names a covered line by,
        and it is the same key `_decided_elsewhere` reads to keep such a line out of the pile's
        queue - so the two halves of "this line is decided" cannot come apart.

        One query for the whole board, and the parse is `ProjectSupplyService`'s own
        (`frozen_lines_of`): a second reading of `line_snapshots` here would be a second
        opinion about what a decision covered.
        """
        pso_ids = {
            entry["project_sales_order_id"]
            for entry in self._addressing.values()
            if entry.get("project_sales_order_id")
        }
        if not pso_ids:
            return {}, {}, {}
        decisions = (
            self.db.query(SOSupplyDecision)
            .filter(
                SOSupplyDecision.project_sales_order_id.in_(list(pso_ids)),
                SOSupplyDecision.state == DECISION_ACTIVE,
            )
            .all()
        )
        from app.services.project_service import resolve_user_names

        confirmer_names = resolve_user_names(
            self.db, {d.confirmed_by for d in decisions if d.confirmed_by}
        )
        out: Dict[str, Dict[str, Any]] = {}
        proposals: Dict[str, Optional[List[Dict[str, Any]]]] = {}
        decided_by_name: Dict[str, Optional[str]] = {}
        for decision in decisions:
            frozen = self.supply.frozen_lines_of(decision)
            for snapshot in decision.line_snapshots or []:
                core_line_id = (snapshot or {}).get("core_line_id")
                line_id = str((snapshot or {}).get("project_line_id") or "")
                if not core_line_id or line_id not in frozen:
                    continue
                out[str(core_line_id)] = self._line_decision(decision, frozen[line_id])
                decided_by_name[str(core_line_id)] = (
                    confirmer_names.get(decision.confirmed_by)
                    if decision.confirmed_by
                    else None
                )
                proposed = frozen[line_id].get("proposed_components")
                proposals[str(core_line_id)] = (
                    None
                    if proposed is None
                    else [self._frozen_source(component) for component in proposed]
                )
        return out, proposals, decided_by_name

    @staticmethod
    def _frozen_source(component: Dict[str, Any]) -> Dict[str, Any]:
        """One frozen PROPOSAL component in the same shape a live one arrives in.

        The snapshot spells the warehouse `source_location` / `source_warehouse_id` and the
        wire spells it `location` / `warehouse_id`; one shape reaches the screen, so both
        halves of "suggested vs decided" are read by one reader. The engine's reason is a
        fragment meant to follow "Reserve 10:", stated here as a sentence the way `_source`
        states a live one.
        """
        reason = str(component.get("reason") or "")
        return {
            "kind": component.get("kind"),
            "qty": qty_text(_dec(component.get("qty"))),
            "location": component.get("source_location"),
            "warehouse_id": component.get("source_warehouse_id"),
            "reason": (reason[:1].upper() + reason[1:] + ".") if reason else "",
            "spo_number": None,
            # Step 3 (S4): the document, named and dated. A `timely_spo` source states its
            # arrival too, and every other kind has none - so this is read off the component
            # rather than blanked, or a confirmed step-3 line came back documentless and
            # re-posted `supply_key: null`.
            "arrival_date": _parse_iso_date(component.get("arrival_date")),
            "rung": component.get("rung"),
            #: The ladder the SNAPSHOT was written under, carried through rather than
            #: restamped: this is history, and re-stamping it with today's version would
            #: make every old proposal claim to be a current one.
            "ladder": component.get("ladder"),
            "donor_so_number": component.get("donor_so_number"),
            "donor_line_no": component.get("donor_line_no"),
            "donor_agent_code": component.get("donor_agent_code"),
            "same_agent": bool(component.get("same_agent", False)),
            "donor_core_line_id": component.get("donor_core_line_id"),
            "donor_required_date": _parse_iso_date(component.get("donor_required_date")),
            "supply_key": component.get("supply_key"),
            "supply_document": component.get("supply_document"),
        }

    def _line_decision(
        self, decision: SOSupplyDecision, frozen: Dict[str, Any]
    ) -> Dict[str, Any]:
        """One covered line's frozen composition, in the words the confirmation takes back.

        The WHOLE composition rather than a summary of it: the Amend editor is seeded from
        it (there is no proposal to seed from on a covered line), and an amendment posts the
        composition back in these words. The board does NOT re-post an untouched covered
        line - the server carries every covered line the body does not name into the next
        revision itself (`ProjectSupplyService.confirm`, 13.4).
        """
        components = list(frozen.get("components") or [])

        def total(kind: str) -> Decimal:
            return sum(
                (_dec(c.get("qty")) for c in components if c.get("kind") == kind), _ZERO
            )

        return {
            "revision_no": decision.revision_no,
            "confirmed_at": decision.confirmed_at,
            "timely_spo_qty": qty_text(total(TIMELY_SPO)),
            # The water, per component, exactly as the Reserve rows are. `timely_spo_qty`
            # above is the TOTAL and stays, because that is the single field `ConfirmLine`
            # takes the composition back in; what it cannot carry is WHERE the water was
            # coming to and WHICH question drew it. Under ladder v5 that question is 1
            # (`rung: group_take`), so a screen reading only the scalar filed a confirmed
            # water line under "Incoming supply" while its own suggestion filed it under
            # "Use own location" - one line, two kinds, and an amber dot on both cards.
            "incoming": [
                {
                    "warehouse_id": c.get("source_warehouse_id"),
                    "location": c.get("source_location"),
                    "qty": qty_text(_dec(c.get("qty"))),
                    "rung": c.get("rung"),
                }
                for c in components
                if c.get("kind") == TIMELY_SPO
            ],
            "reserve": [
                {
                    "warehouse_id": c.get("source_warehouse_id"),
                    "location": c.get("source_location"),
                    "qty": qty_text(_dec(c.get("qty"))),
                    # The rung the confirmation froze, carried rather than dropped. It was
                    # dropped, and every reserve row of a covered line reached the screen as
                    # `rung: null` - so the vocabulary had to be guessed back from the
                    # warehouse code, which is the one reading PLAN section 2 forbids.
                    "rung": c.get("rung"),
                }
                for c in components
                if c.get("kind") == RESERVE
            ],
            "borrow": [
                {
                    "source": c.get("source") or "other_location",
                    "warehouse_id": c.get("source_warehouse_id"),
                    "location": c.get("source_location"),
                    "donor_project_id": c.get("donor_project_id"),
                    "qty": qty_text(_dec(c.get("qty"))),
                    # The PERSON's reason, with the rule's sentence as the fallback: the
                    # confirmation refuses a Borrow carrying none, so re-posting this
                    # composition needs whichever of the two was actually given.
                    "reason": (c.get("cs_reason") or c.get("reason") or ""),
                    # Ladder v2 group borrow (section E.4): the donor fields, so amending
                    # a covered group-borrow line still names its donor line and re-posts
                    # the SAME donor - dropping them here made `boardAmend.frozenDraft`
                    # read a null `donor_core_line_id` and re-post the borrow as a plain
                    # free-stock donor, which the own-location check (rule 7) then refused.
                    "rung": c.get("rung"),
                    "donor_so_number": c.get("donor_so_number"),
                    "donor_line_no": c.get("donor_line_no"),
                    "donor_agent_code": c.get("donor_agent_code"),
                    "same_agent": bool(c.get("same_agent", False)),
                    "donor_core_line_id": c.get("donor_core_line_id"),
                    "donor_required_date": _parse_iso_date(c.get("donor_required_date")),
                    "order_back_qty": c.get("order_back_qty"),
                    # LADDER v7.1 STEP 3 (S4): which document, how it is named and when it
                    # lands. Dropped here, amending a confirmed step-3 line re-posted it as
                    # a free-stock borrow - the placement link came down and the Confirm
                    # re-checked the quantity against on-hand capacity at a bin holding a
                    # container that has not landed.
                    "supply_key": c.get("supply_key"),
                    "supply_document": c.get("supply_document"),
                    "arrival_date": _parse_iso_date(c.get("arrival_date")),
                }
                for c in components
                if c.get("kind") == BORROW
            ],
            "buy_qty": qty_text(total(BUY)),
            "buy_reason": frozen.get("buy_reason"),
            "amend_reason": frozen.get("amend_reason"),
            # The planner's doubt about the numbers behind this decision (R10), echoed so
            # the pill still warns after a reload. Absent on a revision frozen before the
            # checkbox existed, which reads as false: nobody flagged it.
            "suspected_system_issue": bool(frozen.get("suspected_system_issue")),
        }

    def _demand_pressure(
        self, product_ids: Iterable[str], warehouse_ids: Iterable[str]
    ) -> Dict[Tuple[str, str], Tuple[Decimal, Decimal]]:
        """`(owed by the whole book, of which already covered by a confirmed decision)`.

        The captain, reading a cell that said 478 free beside 482 owed: "why is everything
        free, are you sure there is no SO occupying this?" They were right to doubt it - 47,009
        was owed at that location across 289 open lines, and `_free_stock` nets only
        `stock.quantity_reserved` and holds belonging to CONFIRMED decisions, of which the book
        has two. So "free" is very nearly raw on-hand, and printing it alone invites exactly
        that wrong conclusion.

        This changes NO allocation. It states the pressure beside the pile.

        The predicate is the shared one, not a private restatement: `is_open_demand()` plus
        `SalesOrder.status = 'open'`, which is what the worklist, the netting engine and
        `scm.committed_v` all mean by outstanding - so the two screens cannot disagree about
        what is owed. Deliberately NOT narrowed to `demand_class = 'project'`: that filter says
        which orders this screen may PLAN, and the question here is what occupies the stock,
        which a dealer order does just as well as a project one.

        The quantity is `demand_qty()` - `coalesce(qty_required, qty_ordered) - delivered`,
        floored - the same figure the netting engine sums, so a planner comparing this against
        the reorder plan sees one number. It can differ from a board row's own
        `qty_outstanding`, which is what is owed the CUSTOMER (AC-B01) rather than what
        purchasing was asked to cover; they differ only where CS has stated a `qty_required`.

        "Covered" is per LINE, through `is_plan_demand_line()`, so a partially confirmed order
        contributes only its confirmed lines - the same rule `committed_v` applies since
        migration 384, rather than a second opinion about what a decision covers.
        """
        pids = [pid for pid in product_ids if pid]
        wids = [wid for wid in warehouse_ids if wid]
        if not pids or not wids:
            return {}
        owed = demand_qty()
        rows = (
            self.db.query(
                SalesOrderLine.product_id,
                SalesOrderLine.warehouse_id,
                func.coalesce(func.sum(owed), 0).label("owed"),
                func.coalesce(
                    func.sum(case((~is_plan_demand_line(), owed), else_=0)), 0
                ).label("confirmed"),
            )
            .join(SalesOrder, SalesOrder.id == SalesOrderLine.sales_order_id)
            .filter(
                SalesOrderLine.product_id.in_(pids),
                SalesOrderLine.warehouse_id.in_(wids),
                SalesOrder.status == "open",
                is_open_demand(),
            )
            .group_by(SalesOrderLine.product_id, SalesOrderLine.warehouse_id)
            .all()
        )
        return {
            (str(row.product_id), str(row.warehouse_id)): (
                _dec(row.owed),
                _dec(row.confirmed),
            )
            for row in rows
        }

    def _customers(self, customer_ids: Iterable[str]) -> Dict[str, str]:
        ids = [cid for cid in customer_ids if cid]
        if not ids:
            return {}
        return {
            str(row.id): row.customer_name
            for row in self.db.query(Customer).filter(Customer.id.in_(ids)).all()
        }

    def _line_numbers(self, records: Sequence[tuple]) -> Dict[str, int]:
        """A line number per core line, because the core table has none.

        `project_line_numbering.number_lines` (B1 review round): AutoCount's own `line_no`
        wins once every contributing line of the order carries one, distinctly - gaps and
        all. Otherwise every line falls back to a DERIVED 1..n, by (required date nulls
        last, item code, line id) - the same rule `_resolve_core_line` and adoption's own
        `_mirror` share, so a key the board hands out is readable by both without a mirror
        having to exist first.

        A mirror line, when it ALREADY exists and numbers every contributing line of that
        order distinctly, still wins over both: the mirror is what the sheet shows, and
        Re-sync can renumber a later line. `number_lines` has no opinion on the mirror -
        that pass is applied here, same as before.
        """
        by_order: Dict[str, List[LineFacts]] = defaultdict(list)
        for line, order, product, _warehouse, _agent in records:
            by_order[str(order.id)].append(
                LineFacts(
                    str(line.id), line.line_no, line.required_date, product.product_code or ""
                )
            )
        derived: Dict[str, int] = {}
        for entries in by_order.values():
            derived.update(number_lines(entries))

        mirrored = {
            core_id: entry["line_no"]
            for core_id, entry in self._addressing.items()
            # `is not None`, not truthiness (S-1, fix round): a mirror line_no of 0 is a
            # real AutoCount number, ingest accepts it (`ge=0`), and a falsy check would
            # drop it the way a resolver that used truthiness would too.
            if entry.get("line_no") is not None
        }
        for entries in by_order.values():
            ids = [entry.id for entry in entries]
            numbers = [mirrored.get(line_id) for line_id in ids]
            if all(n is not None for n in numbers) and len(set(numbers)) == len(numbers):
                for line_id, number in zip(ids, numbers):
                    derived[line_id] = int(number)
        return derived

    def _mirror_addressing(
        self, core_line_ids: Sequence[str]
    ) -> Dict[str, Dict[str, Any]]:
        """How the confirm endpoint names each core line, when anybody has adopted its order.

        `POST /sales-orders/{pso_id}/confirm` addresses a planning RECORD and its own mirror
        LINES (`ProjectSupplyService.confirm` builds `by_id` from `lines_of(order.id)`), so a
        board that returned only core ids could not reach it and the frontend would have to
        map between two id spaces to get there.

        Scoped to the record that HOLDS the core order (`projects.sales_orders.so_id`), which
        a partial unique index makes at most one. An authored Project SO that happens to
        mirror the same core line is a different subject and must not be confirmed by
        accident, so it is not offered here.
        """
        if not core_line_ids:
            return {}
        rows = (
            self.db.query(
                ProjectSalesOrderLine.core_sales_order_line_id,
                ProjectSalesOrderLine.id,
                ProjectSalesOrderLine.line_no,
                ProjectSalesOrder.id,
                ProjectSalesOrder.so_id,
            )
            .join(
                ProjectSalesOrder,
                ProjectSalesOrder.id == ProjectSalesOrderLine.project_sales_order_id,
            )
            .filter(
                ProjectSalesOrderLine.core_sales_order_line_id.in_(list(core_line_ids)),
                ProjectSalesOrder.so_id.isnot(None),
            )
            .all()
        )
        out: Dict[str, Dict[str, Any]] = {}
        for core_id, line_id, line_no, pso_id, so_id in rows:
            if core_id is None:
                continue
            out[str(core_id)] = {
                "project_sales_order_id": str(pso_id),
                "project_line_id": str(line_id),
                "line_no": int(line_no) if line_no is not None else None,
                "sales_order_id": str(so_id),
            }
        return out

    # -------------------------------------------------------------- sourcing

    def _allocate(self, served: Sequence[_Row], *, as_of: Optional[date] = None) -> None:
        """Compose every contributing line through the SHEET'S OWN ladder, in board order.

        Two questions, and they belong to different owners.

        **Which sources may cover this line, in what order** is `ProjectSupplyService.
        compose_line`: own location, then the shared pool under PLAN 3.3's hot-selling rules,
        then timely incoming, then Buy. That is the sheet's code, called here rather than
        approximated - an earlier build ran a reduced three-source version, and it proposed a
        Buy for a line the sheet would have covered from the pool, so purchasing acting on the
        board and purchasing acting on the sheet disagreed about the same line.

        **Who gets a scarce pile** is the board's: contributions are served in the
        fulfilment-priority ranking (13.5), which is what the per-order sheet structurally
        cannot show. So each pile - the own-location free stock, its dated incoming, and the
        shared pool - is drawn down in board order, and the ladder is then run per line against
        what is left.

        Deliberately NOT pro-rata. Splitting 100 free units across five lines needing 100 each
        produces five short deliveries instead of one complete one and four honest Buys.

        Borrow is not proposed, here or on the sheet: it needs a donor and a reason from a
        person (AC-B09). What the board does is SAY it is available, so a Buy is never printed
        as if the stock existed nowhere.

        A line an ACTIVE decision covers is left out of all of it. It is not competing for the
        pile - the pile's own queue leaves it out, because its claim is already a hold - so
        running the ladder for it produced a fresh proposal for a line that had been decided,
        beside share figures that defaulted to zero because it is not in the queue they are
        counted from. It states what was frozen instead.
        """
        # Covered rows still count towards the STOCK reads: a cell whose only line is decided
        # still has a location, and dropping it here would blank that location's position.
        # Since the ruling above, a COVERED row is never unplannable while it has a location
        # - the flag verdict belongs to undecided lines - so `plannable` already carries it
        # and there is no second list to union in.
        plannable = [row for row in served if not row.unplannable]
        proposable = [row for row in plannable if not row.covered]
        counted = plannable
        for row in served:
            if row.covered:
                self._apply_frozen(row)
            elif row.unplannable:
                row.sources = [
                    {
                        "kind": "unplannable",
                        "qty": qty_text(row.qty),
                        "location": None,
                        "warehouse_id": None,
                        "reason": (
                            OUTSIDE_FULFILMENT_PLANNING
                            if row.outside_planning
                            else "No fulfilment location on the sales order line, so "
                            "nothing can be sourced for it."
                        ),
                        "spo_number": None,
                        "arrival_date": None,
                    }
                ]

        product_ids = {row.product_id for row in counted if row.product_id}
        warehouse_ids = {row.warehouse_id for row in counted if row.warehouse_id}
        # The agents' ownership groups, resolved to warehouses ONCE for the whole board. Their
        # ids join the read set below so the group's rows carry the same demand pressure and
        # incoming figures the line's own location does - a group warehouse listed with two of
        # its seven numbers blank would be worse than not listing it.
        self._group_warehouses = self._warehouses_for_groups(
            {row.agent_location_group for row in served}
        )
        warehouse_ids |= {
            warehouse_id
            for pairs in self._group_warehouses.values()
            for warehouse_id, _code in pairs
        }
        # The site pools rung 2 may draw on, resolved ONCE for the whole board (one query,
        # cached on the supply service beside the ladder's own use of it). Their ids join the
        # read set for the same reason the group's do: the cell lists the pool a proposal
        # cites - "Pool BRW has 1716 available" was a sentence with no row behind it - and a
        # pool row missing its SO qty would compute an Available of 1728 that agrees with
        # nothing on screen.
        self._pool_warehouses = {
            warehouse_id: pool.warehouse_code
            for warehouse_id, pool in self.supply.site_pool_warehouses().items()
        }
        warehouse_ids |= set(self._pool_warehouses)
        self._pool_of = self._pool_by_warehouse()
        # EVERY OTHER PLANNING WAREHOUSE, for AC-V3 (ladder v5, section 1e). A cited donor's
        # whole ownership group is listed in the cell table now, with its own signed available
        # per row and the donor group's net as the subtotal - and WHICH group that is is not
        # known until the engine has composed, which is after this read set would otherwise be
        # fixed. The board used to leave such a row's figures null on purpose ("nothing
        # looked" is a different answer from zero), and a block of dashes is not a subtotal a
        # planner can check. Any group could be the donor, so the honest read set is all of
        # them: it is the same three grouped queries with a wider `IN`, and the product filter
        # is what makes them cheap.
        warehouse_ids |= set(self.supply._planning_warehouses())
        # WHICH warehouses the three batched reads below were actually asked about. A location
        # OUTSIDE this set has no fact and no absence of one: nothing looked, and the table
        # prints a dash rather than a zero. With the line above there is normally no such
        # location; an INACTIVE warehouse a frozen decision names is the case that remains.
        self._counted_warehouses = set(warehouse_ids)
        # Every stock fact the board states comes from these reads and no other, so the
        # availability printed beside a proposal is the availability the proposal was computed
        # from.
        free = self._free = self.supply.free_stock_by_location(product_ids)
        self._levels = self.supply.stock_levels_by_location(product_ids)
        self._held = self.supply.held_stock_by_location(product_ids)
        self._pressure = self._demand_pressure(product_ids, warehouse_ids)
        self._incoming = self.supply.incoming_by_location(product_ids, warehouse_ids)
        # Facts for every plannable row, covered ones included: a covered line is not run
        # through the ladder (its share fields come back empty, and `_apply_frozen` reads
        # none of them), but its DONORS are still read below, from the same fact - Amend on a
        # decided line offers a Borrow exactly as it does on an undecided one.
        facts = self.supply.demand_facts(
            [
                {
                    "key": row.key,
                    # The core line id, which is what the book-wide projection is keyed by.
                    "line_id": row.line_id,
                    "product_id": row.product_id,
                    "warehouse_id": row.warehouse_id,
                    "open_qty": row.qty,
                    # What the line is still OWED, beside what it asks for. `_group_offer`
                    # un-nets this one - the group net subtracted `demand_qty()`, so adding
                    # the ask back would return more than was ever taken - and a payload
                    # without it left every board row un-netting zero.
                    "owed_qty": row.qty_outstanding,
                    "required_date": row.required_date,
                    "order_date": row.order_date,
                    "payment_terms_days": row.payment_terms_days,
                    "demand_class": row.demand_class,
                    "so_number": row.so_number,
                    "line_no": row.line_no,
                    "item_code": row.item_code,
                    # R7 (query-count): off the same row's own core line - see `_Row
                    # .__slots__`'s own docstring - so the own-arrival credit prefetch
                    # never queries for a fact this payload already names.
                    "sales_order_id": row.sales_order_id,
                    "source_ref": row.source_ref,
                    "company_id": row.company_id,
                }
                for row in plannable
            ],
            exclude_line_ids=list(self._exclude_covered_line_ids) or None,
        )

        # S4 (`PLAN-local-supplier-oi-routing.md`): moved onto `ProjectSupplyService` as
        # `open_po_balance`, the ONE implementation both this board and a donor's own
        # `donor_location` read - and read ONCE for both through `po_open_facts`, which
        # this call primes with the board's own span (`also_*`). A board row can name a
        # warehouse no donor pile covers - an inactive one a frozen decision points at -
        # so the span is the union, and the donors then read the same map rather than
        # paying a second pair of statements for it. AFTER `demand_facts`, which is what
        # fixes the pile span the cache is built over.
        self._po_open = self.supply.po_open_facts(
            also_products=product_ids, also_warehouses=warehouse_ids
        )

        # The pool piles behind rung 2, in AutoCount's own triple, read once for every pool a
        # served line may draw on. `Pool BRW | Had 0` beside `Available 1` in Inventory is two
        # true numbers, and the trail has to show the pile the queue was netted from.
        pool_pairs = {
            (fact.product_id, str(fact.pool.id))
            for fact in facts.values()
            if fact.product_id and fact.pool is not None
        }
        self._pool_piles = (
            self.supply.pile_triples(
                {product_id for product_id, _pool in pool_pairs},
                {pool_id for _product, pool_id in pool_pairs},
            )
            if pool_pairs
            else {}
        )

        piles: Dict[Tuple[str, str], List[_Row]] = defaultdict(list)
        for row in proposable:
            piles[(row.product_id, row.warehouse_id)].append(row)

        # Pass one: bookkeeping only. How much of its own pile each line may have was already
        # decided by `demand_facts`, from the SAME book-wide projection the confirmation judges
        # against - a line reserves what is left after the demand the active policy ranks ahead
        # of it, and nothing more. What is recorded here is who drew the pile down before this
        # row was reached, which is what the contest sentence and `qty_free_remaining` say.
        for (product_id, warehouse_id), members in piles.items():
            free_left = free.get((product_id, warehouse_id), _ZERO)
            taken = _ZERO
            taker: Optional[_Row] = None
            for row in members:
                fact = facts[row.key]
                own_share = fact.own_free
                timely_share = fact.timely_qty
                # Stock is not per date, so the same free figure is true of every column of
                # this product; what differs is how much an earlier date has already taken.
                row.free_before = free_left
                row.taken_before = taken
                row.last_taker = taker
                free_left = max(free_left - own_share, _ZERO)
                if own_share + timely_share > _ZERO:
                    taken += own_share + timely_share
                    taker = row

        # Pass two: run the ladder in board order, against the running piles - and per
        # PLANNING UNIT rather than per line (ladder v6). One sales order's lines for the
        # same item, location and delivery date are one quantity to plan: the captain,
        # reading SO381895's lines 31 and 32, "this is 1 order as a whole". Another order
        # asking for the same thing on the same day is a different unit, which is why the
        # sales order is in the key.
        entries = [
            (
                row.key,
                facts[row.key],
                (
                    row.sales_order_id,
                    row.product_id,
                    row.warehouse_id,
                    row.required_date,
                ),
            )
            for row in proposable
        ]
        composed = self.supply.compose_lines(entries, as_of=as_of)
        units = self.supply.unit_totals(entries)
        borrow_cache: Dict[Tuple[Any, ...], List[Dict[str, Any]]] = {}
        for row in proposable:
            fact = facts[row.key]
            row.unit_qty, row.unit_line_count = units[row.key]
            # A Reserve component names its location by CODE and the confirmation addresses a
            # warehouse by ID, so the pair is captured here where both are in hand.
            row.so_qty_ahead = fact.so_qty_ahead
            row.lines_ahead = fact.lines_ahead
            row.available_to_this_line = fact.available_to_this_line
            row.warehouse_ids = {
                code: warehouse_id
                for code, warehouse_id in (
                    (fact.own_code, str(fact.warehouse.id) if fact.warehouse else None),
                    (fact.pool_code, str(fact.pool.id) if fact.pool else None),
                )
                if code and warehouse_id
            }
            # `as_of` is the board's own, never the clock: which side of the ATP reserve
            # window a line falls on has to be the same answer a pinned simulation gives.
            row.outside_reserve_window = self.supply.outside_reserve_window(
                fact, as_of=as_of
            )
            # What the shared pool still held when this line's UNIT was reached, captured
            # before the draw: the trail states what each source held, and reading it back
            # afterwards would state what was left instead.
            (
                components, pool_open, borrow_open, net_open, options, other_group_open,
                supply_open, share_open, own_group_open,
            ) = composed[row.key]
            row.options = [_option_row(option) for option in options]
            # Ladder v2's group take / group borrow / cross-group borrow rungs name a
            # location `warehouse_ids` (own + pool only, above) does not cover.
            for component in components:
                code = component.source_location
                if code and code not in row.warehouse_ids:
                    warehouse_id = self.supply.warehouse_id_for_code(code)
                    if warehouse_id:
                        row.warehouse_ids[code] = warehouse_id

            reserved = sum((c.qty for c in components if c.kind == RESERVE), _ZERO)
            incoming = sum((c.qty for c in components if c.kind == TIMELY_SPO), _ZERO)
            # Ladder v2 (section E rules 4/5): group borrow and cross-group borrow are now
            # PROPOSED, not only offered, so a line's Buy figure has to net them out too -
            # `borrowed` was always in the balance invariant, it just used to be zero here.
            borrowed = sum((c.qty for c in components if c.kind == BORROW), _ZERO)
            bought = sum((c.qty for c in components if c.kind == BUY), _ZERO)
            row.proposed = {
                RESERVE: reserved, TIMELY_SPO: incoming, BORROW: borrowed, BUY: bought,
            }
            # Contested means this line is buying while its location DOES hold free stock -
            # somebody got there first, whether that somebody is on this board or is one of the
            # earlier-dated orders in the book that the share above already accounts for. A
            # location that never held any stock is NOT contested; it is a plain Buy.
            row.contested = bought > _ZERO and self._free.get(
                (row.product_id, row.warehouse_id), _ZERO
            ) > _ZERO

            # Donors on EVERY plannable row, not only a row the engine is buying for. The
            # captain's flow is "borrow instead of taking the reserved stock", and a line met
            # from its own Reserve had no donors on it, so Amend said nobody held any and
            # offered no Borrow. A need of 0 ranks the donors by availability alone.
            # EXCEPT beyond the reserve window, where the two borrow rungs are not walked at
            # all: offering the donors anyway is offering the one move the rule forbids.
            row.borrow_candidates = (
                []
                if row.outside_reserve_window
                else self._donors_for(borrow_cache, row, fact, bought)
            )

            # THE TRAIL FIRST, and the sources from what it offered. The Buy's own
            # sentence names where a person could still borrow from, and the only honest
            # answer to that is what questions 3 and 4 actually put on the table - read
            # off the trail rather than filtered a second time out of the raw donor list
            # (AC-V4: the note must never offer what the proof has refused).
            row.trail, offerable = self._trail(
                row, fact, components, pool_open, borrow_open, net_open, as_of=as_of,
                other_group_open=other_group_open, supply_open=supply_open,
                share_open=share_open, own_group_open=own_group_open,
            )
            row.sources = [
                self._source(component, row, offerable) for component in components
            ]
            # An undecided line's suggestion IS its live proposal, said under the key the
            # decision strip reads for every line (AC-D2), so the strip never has to ask
            # which of two fields a given row keeps its suggestion in.
            row.proposed_components = row.sources
            # Said, not implied: the ladder consulted these and never printed them, and the
            # captain read the trail as if it had consulted nothing.
            row.item_flags = {
                "dealer_hot_selling": bool(fact.is_dealer_hot_selling),
                "dealer_hot_selling_where": list(fact.dealer_hot_selling_where or []),
                "project_hot_selling": bool(fact.is_project_hot_selling),
                "project_hot_selling_where": list(fact.project_hot_selling_where or []),
                "dealer_classified": bool(fact.dealer_classified),
                "project_classified": bool(fact.project_classified),
                "discontinued": bool(fact.is_discontinued),
                "retail_classification_available": not fact.classification_unavailable,
            }

        # A covered line is amendable too, so its donors are read - ranked against what its
        # frozen composition is still buying - and nothing else about it is touched: no
        # ladder, no contest, no share of a queue it is not in.
        for row in plannable:
            if not row.covered:
                continue
            fact = facts[row.key]
            row.outside_reserve_window = self.supply.outside_reserve_window(
                fact, as_of=as_of
            )
            row.borrow_candidates = (
                []
                if row.outside_reserve_window
                else self._donors_for(
                    borrow_cache, row, fact, row.proposed.get(BUY, _ZERO)
                )
            )
            # Amend on a covered line reads the same flags a proposal states: a discontinued
            # product needs a Buy reason whether or not the line was decided before.
            row.item_flags = {
                "dealer_hot_selling": bool(fact.is_dealer_hot_selling),
                "dealer_hot_selling_where": list(fact.dealer_hot_selling_where or []),
                "project_hot_selling": bool(fact.is_project_hot_selling),
                "project_hot_selling_where": list(fact.project_hot_selling_where or []),
                "dealer_classified": bool(fact.dealer_classified),
                "project_classified": bool(fact.project_classified),
                "discontinued": bool(fact.is_discontinued),
                "retail_classification_available": not fact.classification_unavailable,
            }

        # A covered line's SUGGESTION is what the ladder says TODAY (captain, 28 Aug 2026,
        # ruling 1), read after the walk so the fresh service below cannot disturb the
        # caches the walk above read.
        self._suggest_live_for_covered(served, as_of=as_of)

    def _donors_for(
        self,
        borrow_cache: Dict[Tuple[Any, ...], List[Dict[str, Any]]],
        row: _Row,
        fact: Any,
        need: Decimal,
    ) -> List[Dict[str, Any]]:
        """Where else this line could be met from, read once per distinct question.

        Cached per (product, location, the quantity being covered): the donors depend on the
        fact's reserve reach rather than on the line, and recomputing them per row walks the
        whole free-stock cache once per row - but the RANKING is against this line's residual
        (13.11), so two rows covering different quantities are two different questions.
        Hot-selling no longer changes the donor set (own location is always Reserve-eligible,
        never a Borrow source), so it is not part of the key.
        """
        cache_key = (fact.product_id, row.warehouse_id, need)
        if cache_key not in borrow_cache:
            candidates = self.supply.borrow_candidates_for(fact, need=need)
            for candidate in candidates:
                warehouse_id = candidate.get("warehouse_id")
                if not warehouse_id:
                    continue
                # S4 (`PLAN-local-supplier-oi-routing.md`): the SAME location facts the Grid
                # Location table states for this warehouse, built by `ProjectSupplyService.
                # donor_location` - the ONE shared builder both this board and the per-order
                # sheet's own `_borrow_candidates` call, so a donor's figures can never
                # disagree between the manual Borrow modal and the Grid table. The `where`
                # tag is classified in there too, off the candidate's own `rung`, rather
                # than here: stated at one of the two call sites it was the one field the
                # two surfaces still came apart on (the sheet called every donor
                # `other_group`).
                candidate["location"] = self.supply.donor_location(
                    fact.product_id,
                    warehouse_id,
                    location_code=candidate.get("warehouse_code"),
                    rung=candidate.get("rung"),
                )
            borrow_cache[cache_key] = candidates
        return borrow_cache[cache_key]

    def _suggest_live_for_covered(
        self, served: Sequence[_Row], *, as_of: Optional[date] = None
    ) -> None:
        """A covered line's `proposed` is what the ladder would propose for it TODAY.

        The captain, 28 August 2026, on SO381895: revision 1 had frozen Reserve 30 / Buy 30
        / Buy 15 for TPE-9204's three dates, but the board printed the proposal SNAPSHOT
        taken with that revision - "BRW 30" on every date, by an engine that had no pool
        ledger yet - and Approve resubmitted it: "0 of 1 orders confirmed ... BRW now has 1
        free for this line, and 30 was asked for". Ruled: the suggestion is live (option 1);
        the at-decision-time proposal stays in `line_snapshots` for the record.

        ONE WALK PER ORDER, the order's own lines in line order - the walk `proposal_for`
        and the freeze run - against facts that un-net THIS order's holds (`replacing`) and
        keep every other order's. That is the reading `confirm` judges an amendment of the
        order against, so what is suggested here is what a Save can commit; and it is a
        walk, not a per-line composition, so the pool and donor ledgers hold across the
        order's delivery dates exactly as they do for undecided lines.

        A FRESH service, because `_facts_for` replaces the request's stock caches and the
        board's own walk has already read the ones it needs. The decided side is untouched:
        `sources`, `decision` and the proposed totals go on stating what was frozen.
        """
        by_order: Dict[str, List[_Row]] = defaultdict(list)
        for row in served:
            if (
                # A DECISION, specifically - not merely `covered`. A line the migrated book
                # decided (a live inquiry row, no revision) has nothing frozen for a live
                # suggestion to sit beside, and "here is what the engine would propose" over
                # an instruction purchasing already holds is an invitation to raise a second
                # one. It states the inquiry and nothing else (AC-S2-7).
                row.decision is not None
                and not row.unplannable
                and row.project_sales_order_id
                and row.project_line_id
            ):
                by_order[str(row.project_sales_order_id)].append(row)
        if not by_order:
            return
        for pso_id, rows in by_order.items():
            order = (
                self.db.query(ProjectSalesOrder)
                .filter(ProjectSalesOrder.id == pso_id)
                .first()
            )
            if order is None:
                continue
            supply = ProjectSupplyService(self.db)
            lines = supply.lines_of(pso_id)
            wanted = {str(row.project_line_id) for row in rows}
            replacing = {str(line.id) for line in lines if str(line.id) in wanted}
            if not replacing:
                continue
            facts = supply._facts_for(order, lines, replacing=replacing)
            checked = [
                (line, None, facts[str(line.id)])
                for line in lines
                if str(line.id) in replacing and str(line.id) in facts
            ]
            proposals = supply._proposals_for(lines, checked, facts=facts, as_of=as_of)
            for row in rows:
                components = proposals.get(str(row.project_line_id))
                if components is None:
                    continue
                for component in components:
                    code = component.source_location
                    if code and code not in row.warehouse_ids:
                        warehouse_id = self.supply.warehouse_id_for_code(code)
                        if warehouse_id:
                            row.warehouse_ids[code] = warehouse_id
                row.proposed_components = [
                    self._source(component, row, ()) for component in components
                ]

    def _apply_frozen(self, row: _Row) -> None:
        """A covered line states what was decided for it, and nothing else (13.4).

        The sources are the FROZEN composition, including a Borrow - the engine's own ladder
        (`order_borrow`/`supply_borrow`) can propose one too, but a decided line prints what was
        actually confirmed for it, not what the ladder would propose if walked again today, and
        printing anything else would describe a decision nobody took. There is no trail because
        no ladder was walked, no contest because a decided line is not competing, and no share
        of the queue because it is not in the queue: `null` there, never `0`, which would be a
        claim about a contest it left. Its donors are the one thing still read for it, by
        `_allocate`, because it can still be amended.
        """
        decision = row.decision or {}
        reserve = sum((_dec(c["qty"]) for c in decision.get("reserve") or []), _ZERO)
        incoming = _dec(decision.get("timely_spo_qty"))
        borrow = sum((_dec(c["qty"]) for c in decision.get("borrow") or []), _ZERO)
        bought = _dec(decision.get("buy_qty"))
        row.proposed = {
            RESERVE: reserve, TIMELY_SPO: incoming, BORROW: borrow, BUY: bought,
        }
        # EVERY kind carries its rung, not only the borrows. Rebuilt without it, a covered
        # line's whole composition reached the screen as `rung: null` and the vocabulary had
        # to be inferred back from the warehouse code - which is exactly the reading PLAN
        # section 2 replaced ("BRW-BB" and the pool "BRW" share a prefix and are not the
        # same kind of supply). Incoming and Buy carry their own rung by definition.
        row.sources = [
            *(
                {
                    "kind": RESERVE,
                    "qty": component["qty"],
                    "location": component.get("location"),
                    "warehouse_id": component.get("warehouse_id"),
                    "reason": self._frozen_reason(row, f"Reserved at {component.get('location')}"),
                    "spo_number": None,
                    "arrival_date": None,
                    "rung": component.get("rung"),
                }
                for component in decision.get("reserve") or []
            ),
            *(
                # PER COMPONENT where the decision recorded them (every v5 confirmation),
                # so the water keeps the question that drew it and the location it was
                # coming to. The single-row fallback is for a revision frozen before
                # `incoming` was recorded, where the only facts are the total and the rung
                # that existed then.
                {
                    "kind": TIMELY_SPO,
                    "qty": component["qty"],
                    "location": component.get("location"),
                    "warehouse_id": component.get("warehouse_id"),
                    "reason": self._frozen_reason(row, "Incoming supply, as confirmed"),
                    "spo_number": None,
                    "arrival_date": None,
                    "rung": component.get("rung") or RUNG_INCOMING,
                }
                for component in (
                    decision.get("incoming")
                    or (
                        [
                            {
                                "qty": qty_text(incoming),
                                "location": row.location,
                                "warehouse_id": row.warehouse_id,
                                "rung": RUNG_INCOMING,
                            }
                        ]
                        if incoming > _ZERO
                        else []
                    )
                )
            ),
            *(
                {
                    "kind": BORROW,
                    "qty": component["qty"],
                    "location": component.get("location"),
                    "warehouse_id": component.get("warehouse_id"),
                    "reason": self._frozen_reason(
                        row,
                        f"Borrowed from {component.get('location') or 'another location'}",
                        component.get("reason"),
                    ),
                    "spo_number": None,
                    # Step 3 (S4): the DOCUMENT this borrow comes off. `arrival_date` is
                    # the component's own - a step-3 source has one and every other borrow
                    # does not, so the None above is the fallback and not the rule.
                    "arrival_date": component.get("arrival_date"),
                    "rung": component.get("rung"),
                    "donor_so_number": component.get("donor_so_number"),
                    "donor_line_no": component.get("donor_line_no"),
                    "donor_agent_code": component.get("donor_agent_code"),
                    "same_agent": bool(component.get("same_agent", False)),
                    "donor_core_line_id": component.get("donor_core_line_id"),
                    "donor_required_date": component.get("donor_required_date"),
                    "supply_key": component.get("supply_key"),
                    "supply_document": component.get("supply_document"),
                }
                for component in decision.get("borrow") or []
            ),
            *(
                [
                    {
                        "kind": BUY,
                        "qty": qty_text(bought),
                        "location": None,
                        "warehouse_id": None,
                        "reason": self._frozen_reason(row, "Bought, as confirmed"),
                        "spo_number": None,
                        "arrival_date": None,
                        "rung": RUNG_BUY,
                    }
                ]
                if bought > _ZERO
                else []
            ),
        ]
        row.trail = []
        row.item_flags = None
        row.contested = False
        # Donors are NOT cleared: `_allocate` reads them for a covered line too, because a
        # decided line is still amendable and Amend has to be able to offer a Borrow on it.
        row.so_qty_ahead = None
        row.lines_ahead = None
        row.available_to_this_line = None

    @staticmethod
    def _frozen_reason(row: _Row, what: str, said: Optional[str] = None) -> str:
        """The sentence a frozen component carries: what was done, in which revision.

        Never the engine's own reason for a proposal it did not make - this quantity was
        decided by a person, and the reason they gave (a Borrow's, always) is what follows it.
        """
        revision = (row.decision or {}).get("revision_no")
        sentence = f"{what} in revision {revision}." if revision else f"{what}."
        stated = (said or "").strip()
        return f"{sentence} {stated}" if stated else sentence

    def _trail(
        self,
        row: _Row,
        fact: Any,
        components: Sequence[Any],
        pool_open: Optional[Mapping[str, Decimal]],
        borrow_open: Optional[Mapping[str, Decimal]] = None,
        net_open: Optional[Decimal] = None,
        as_of: Optional[date] = None,
        other_group_open: Optional[MutableMapping[str, Decimal]] = None,
        supply_open: Optional[MutableMapping[str, Decimal]] = None,
        share_open: Optional[Mapping[str, Decimal]] = None,
        own_group_open: Optional[MutableMapping[str, Decimal]] = None,
    ) -> Tuple[List[Dict[str, Any]], List[str]]:
        """The four questions ladder v8 asks about this line, and Buy.

        `net_open` is the site pools' net as the walk found it for this line's unit
        (`compose_lines`' third ledger); `None` reads the fact's own. Question 2 is
        answered from it, so the second delivery date says "the site pools net 1" and
        not the 31 the first date has already drawn from.

        `share_open` and `own_group_open` are the other two ledgers of that same walk, as
        this LINE found them (C3, code review round 4): what is left of each site pool's
        project share, and what is left of the unit's own ownership-group pile. Passed for
        exactly the reason `borrow_open` and `other_group_open` are - the proof has to be
        the answer the engine actually got. Without them question 2 re-offered a pool share
        an earlier line had spent, and question 1 offered the 135 at BRW-BB to the 1,305
        line of the same unit that the 135 line had just taken.

        FIVE ROWS, always, in the WALK's own order (v8, R-A): the site pool's share, our
        own locations, borrowing on hand from a later order, borrowing what one is waiting
        on, Buy. The captain, walking SO381895: "our thought process is simpler now" - so
        the proof is the questions a planner would ask out loud, in the order the ladder
        asks them, each answered Yes or No with the figure that decided it inside the
        words. `OPTION_STEPS` is that order and this list mirrors it; two orders for one
        walk is a reader reconciling two screens.

        Every question is answered even when the line was covered two rows above, because
        "the pool was checked and had none" is the answer to that question and an omitted
        row reads as a question nobody asked. `kind` stays the engine's own internal rung
        key (`own` / `pool` / `cross_group_borrow` / `group_borrow` / `buy`) and is never
        rendered: the reader sees the question.

        A READ, never a second allocator: every quantity here is either one the engine
        already produced (`components`) or one the facts already state, so the trail cannot
        disagree with the proposal it explains.

        Returns the five rows AND the locations questions 3 and 4 actually offered, in the
        order they were offered. The Buy's own sentence is written from that list and from
        nothing else (AC-V4): a second filter over the raw donor set was how the note came
        to say "borrowing is possible from DC1-NTC" one line under a question 3 that had
        just said the NTC group has nothing left.
        """
        steps: List[Dict[str, Any]] = []
        # The line's still-uncovered quantity as the questions are walked. INTERNAL, and no
        # longer on the wire: what it decides is which of several true sentences a question
        # writes ("nothing was left there" and "you were already covered before I asked" are
        # different answers to a No), and a reader does not need the running balance to
        # follow five rows.
        remaining = _dec(row.qty)
        own_code = fact.own_code
        pool_code = fact.pool_code

        def took_at(rung: str) -> Decimal:
            return sum(
                (c.qty for c in components if getattr(c, "rung", None) == rung), _ZERO
            )

        def drawn_at(rung: str) -> List[str]:
            """The locations a rung actually drew from, in the order it drew them."""
            out: List[str] = []
            for component in components:
                if getattr(component, "rung", None) != rung:
                    continue
                location = component.source_location
                if location and location not in out:
                    out.append(location)
            return out

        def add(
            kind: str,
            question: str,
            *,
            taken: Decimal = _ZERO,
            sources: Optional[Sequence[str]] = None,
            why: Callable[[str], str],
            location: Optional[str] = None,
            warehouse_id: Optional[str] = None,
            ahead_qty: Optional[Decimal] = None,
            ahead_lines: Optional[int] = None,
            ahead: Optional[Sequence[Dict[str, Any]]] = None,
            ahead_more: int = 0,
            ahead_by_factor: Optional[Dict[str, int]] = None,
            note: Optional[str] = None,
            pool: Optional[Dict[str, Any]] = None,
            offered: Decimal = _ZERO,
            eligible: bool = True,
        ) -> None:
            nonlocal remaining
            wanted = remaining
            remaining = max(remaining - taken, _ZERO)
            steps.append(
                {
                    "step": len(steps) + 1,
                    #: The rung's INTERNAL key. Addressing and test-ids only; the reader is
                    #: shown `question`, because "cross_group_borrow" is the engine's word
                    #: for a thing a planner calls borrowing from another location.
                    "kind": kind,
                    "question": question,
                    #: `yes` when this question supplied something, `no` otherwise. The
                    #: whole point of the rewrite: one word a reader can scan five of.
                    "answer": "yes" if taken > _ZERO else "no",
                    "took": qty_text(taken),
                    #: Where it came from, when it came from anywhere. Null rather than an
                    #: empty string on a No, so the column is blank rather than a dash the
                    #: reader has to interpret.
                    "from": ", ".join(sources or []) or None,
                    #: The sentence, with the figure that decided it inside it: the group's
                    #: net, the pile's net, the donor group's net, the cap. Chosen by the
                    #: finer internal outcome, because a No has several true reasons.
                    "why": why(
                        "not_eligible"
                        if not eligible
                        else "took"
                        if taken > _ZERO
                        else "none_needed"
                        if wanted <= _ZERO
                        else "offered"
                        if offered > _ZERO
                        else "nothing_left"
                    ),
                    "note": note,
                    "location": location,
                    "warehouse_id": warehouse_id,
                    #: The queue at THIS line's own pile, carried on question 1 - the one
                    #: question that has a queue, and the one `QueueLink`'s dialog can open
                    #: (it opens exactly `fulfilment_warehouse_id`). Under ladder v4 the
                    #: queue decides no availability; it is who is in front of this line.
                    "ahead_qty": None if ahead_qty is None else qty_text(ahead_qty),
                    "ahead_lines": ahead_lines,
                    "ahead": list(ahead or []),
                    "ahead_more": ahead_more,
                    "ahead_by_factor": dict(ahead_by_factor or {}),
                    #: The pool pile in AutoCount's triple, on question 2 alone.
                    "pool": pool,
                }
            )

        # The ATP reserve window verdict, read once and answered the same way by all four
        # questions. Under v5 NOTHING runs beyond it, incoming included, so every question
        # answers No with the rule as its reason and Buy takes the whole line.
        outside_window = bool(row.outside_reserve_window)
        # The day a donor has to be due on or after to be able to wait (R12). Named in
        # step 2's refusal, because "no donor" sends a planner nowhere and "no order due on
        # or after 12 Dec 2026" tells them which orders would have qualified (AC-S3-4).
        window = reserve_window_end(
            as_of or date.today(), self.supply._lead_time_days(fact.product_id)
        )
        # The manual donors `BorrowAddDialog` also lists, read once for the Buy's own
        # sentence: the same-ownership-group offers a person may pick at any rank, and -
        # for a line whose bin carries no ownership group at all - the plain free-stock
        # donors, which no step of the ladder reaches.
        group_borrow_donors = (
            []
            if outside_window
            else [c for c in row.borrow_candidates if c.get("rung") == "group_borrow"]
        )
        ungrouped_donors = (
            [] if outside_window or fact.group_code else list(row.borrow_candidates)
        )

        # ------------------------------------------- 0. Can the site pool spare us a share?
        #
        # FIRST since ladder v8 (R-A), and in the walk's own order (review round 1, S5): a
        # proof that asked its questions in a different order from the ladder made the
        # reader do the reconciling. ALL FIVE site pools are ONE pile (section 1d) and each
        # spares its own share (R-B, R-L); the dealer hot-selling gate that used to refuse
        # the whole pile is retired. The step has a second half since v7.1 - a LATER POOL
        # ORDER may lend its on hand, and that half DOES raise an order-back at the pool
        # order's own date (R34).
        pool_chain = (
            [] if outside_window else self.supply.pool_chain_for(fact, pool_free_left=pool_open)
        )
        pools_net_open = fact.pools_net if net_open is None else net_open
        pool_taken = took_at("pool")
        pool_pile = (
            self._pool_pile(row, fact, _own_pool_floor(fact, pool_open))
            if pool_code and pool_chain
            else None
        )
        # Step 4b's donors: the LATER POOL ORDERS holding on hand. Read off the same builder
        # the engine walked (`pool=True`), because without them the step printed
        # `answer=yes, took=30` beside `offered=0` and "No shared pool holds this product."
        # over a borrow it had just composed.
        # LADDER V8 (R-A, review round 2 S4): the dealer hot-selling gate is retired here
        # too. `ProjectSupplyService.walk` builds `pool_borrow` unconditionally, so a
        # hot-selling item whose pool floor is empty and whose later pool order can lend
        # read `offered=0` beside the borrow the engine had just composed.
        pool_borrow_candidates = (
            []
            if outside_window
            else self.supply.order_borrow_candidates_for(
                fact, as_of=as_of, borrow_left=borrow_open, pool=True
            )
        )
        # LADDER V8 (R-B, R-L, R-N): what the CHAIN can offer this line is each pool's own
        # share, walked the way step 0 itself walks it (`_draw_pool_share` draws exactly
        # this list since R-N) and bounded by the one five-pool net.
        # Capping the whole chain by the FIRST pool's allowance printed `offered=0` beside
        # `taken=300` the moment another site's pool answered the remainder (review round 2,
        # S5); before that cap existed at all the proof advertised 31 beside a step that
        # could only ever have given 15.
        pool_share_chain = (
            []
            if outside_window
            else pool_share_capacity(
                pools=list(pool_chain),
                pools_net=pools_net_open,
                pool_share_pct=self.supply.fulfilment_settings().get("pool_share_pct"),
                share_left=share_open,
            )
        )
        # The two halves are ALTERNATIVES, never a sum: one step, one story (R33), so the
        # offer is the larger of them and not both added together.
        pool_offered = max(
            sum((amount for _location, amount, _allowance in pool_share_chain), _ZERO),
            sum((_dec(c.get("qty")) for c in pool_borrow_candidates), _ZERO),
        )
        add(
            "pool",
            "Can we take from the pool?",
            taken=pool_taken,
            offered=pool_offered,
            # LADDER V8 (R-A): the dealer hot-selling gate is retired - the SHARE is what
            # keeps stock for dealers now - so a hot item's pool step is walked like any
            # other item's.
            eligible=not outside_window,
            sources=drawn_at("pool"),
            location=pool_code,
            warehouse_id=row.warehouse_ids.get(pool_code) if pool_code else None,
            note=(
                None
                if outside_window
                else self._pool_note(pool_chain)
                or self._order_borrow_note(pool_borrow_candidates)
            ),
            why=lambda outcome: (
                _RESERVE_WINDOW_RUNG_WHY
                if outside_window
                else self._pool_answer_why(
                    fact, pool_chain, pool_taken, pool_pile, pool_open, outcome,
                    pools_net=pools_net_open,
                    borrow_donors=pool_borrow_candidates,
                    components=components,
                    share_left=share_open,
                )
            ),
            pool=pool_pile,
        )


        # ------------------------------------------- 1. Can we use our locations?
        #
        # The OWNERSHIP GROUP, this line's own location included, read as one pile
        # (`group_net`, section 1d). The old read-only own-location strip is folded in
        # here: it existed to name the queue, and the queue is one of this question's
        # facts, not a question of its own.
        # LADDER V7.1: step 1 has TWO halves - this line's own ownership group, date-aware
        # (R24), and then the OTHER project groups' free piles (R5), which is the free stock
        # the retired `cross_group_borrow` rung used to call a Borrow. Both are one question,
        # because free stock is owed to nobody wherever it sits.
        # R-M's shorts map is DELIBERATELY discarded here: the trail states what this line
        # was offered, and the refusal it explains is already printed on the `use` OPTION
        # row, which the walk composes from the same map (`walk_line(other_group_short=)`).
        # Printing it twice would say the same thing in two voices.
        group_take_candidates, other_group_candidates, own_offer, _other_short = (
            ([], [], _ZERO, {})
            if outside_window
            # `other_group_open` is step 1b's own ledger as this unit found it, passed for
            # exactly the reason `borrow_open` is: the proof has to be the answer the engine
            # actually got. Without it question 1 re-read a pile an earlier unit of the same
            # walk had spent, and the Buy's sentence sent a planner to a bin with nothing in
            # it (`test_the_proof_never_offers_a_donor_the_walk_has_already_spent`).
            else self.supply.use_candidates_for(
                fact, as_of=as_of, other_left=other_group_open,
                own_left=own_group_open,
            )
        )
        # What the group has ON THE WATER, both halves. The timely half is already inside
        # `group_take_candidates`; the late half is named in the sentence and drawn by
        # nobody (captain, 27 August 2026), because a promise landing after the required
        # date covers nothing and silence about it reads as "there is none".
        group_water = [] if outside_window else self.supply.group_water_for(fact)
        group_taken = took_at("group_take")
        # WHAT WAS ACTUALLY DRAWN, per component: the quantity, the location, and whether it
        # came off a floor or off the water. Read from `components` and not from the
        # candidate list, because the last candidate is routinely drawn short (offer 10,
        # need 9), and a sentence quoting the offer beside a component quoting the draw is
        # two numbers for one fact.
        group_drawn = [
            (
                component.source_location,
                _dec(component.qty),
                component.kind == TIMELY_SPO,
            )
            for component in components
            if getattr(component, "rung", None) == RUNG_GROUP_TAKE
            and component.source_location
        ]
        # R7: what own-arrival credit this rung actually drew, so the trail can say WHICH
        # PO landed it before the group-net sentence that follows - read off `components`
        # (what was DRAWN), the same as `group_drawn` above.
        own_arrival_drawn = [
            (
                _dec(component.qty),
                getattr(component, "supply_document", None),
                component.source_location,
                getattr(component, "landed_qty", None),
                getattr(component, "free_qty", None),
                getattr(component, "landed_text", None),
            )
            for component in components
            if getattr(component, "rung", None) == RUNG_GROUP_TAKE
            and getattr(component, "source", None) == "own_arrival"
        ]
        group_offered = sum(
            (
                _dec(c.get("qty"))
                for c in [*group_take_candidates, *other_group_candidates]
            ),
            _ZERO,
        )
        add(
            "own",
            "Can we use our locations?",
            taken=group_taken,
            offered=group_offered,
            eligible=not outside_window,
            sources=drawn_at("group_take"),
            location=own_code,
            warehouse_id=row.warehouse_ids.get(own_code),
            ahead_qty=fact.so_qty_ahead,
            ahead_lines=fact.lines_ahead,
            ahead=fact.ahead_lines_named,
            ahead_more=fact.ahead_more,
            ahead_by_factor=fact.ahead_by_factor,
            note=(
                None
                if outside_window
                else self._group_take_note(
                    [*group_take_candidates, *other_group_candidates], fact
                )
            ),
            why=lambda outcome: (
                _RESERVE_WINDOW_RUNG_WHY
                if outside_window
                else self._group_take_why(
                    outcome,
                    group_take_candidates,
                    fact,
                    group_water,
                    group_drawn,
                    other=other_group_candidates,
                    offer=group_offered,
                    own_arrival=own_arrival_drawn,
                )
            ),
        )

        # --------------------- 2. Can we borrow on hand from a later order?
        #
        # LADDER V7.1 (R1, reversing the 25 August 2026 ruling): this is a RUNG now, not a
        # person's pick. A later order holding stock on a floor can wait; the asker cannot;
        # and the debt is recorded at the donor's own date, where purchasing can see it.
        order_borrow_candidates = (
            []
            if outside_window
            # `borrow_open` is the walk's DONOR LEDGER as this line's unit found it
            # (`compose_lines`), passed here exactly as `pool_free_left=pool_open` is passed
            # to the pool question: the proof has to be the answer the engine actually got,
            # not a fresh read of a donor an earlier unit has already spent.
            else self.supply.order_borrow_candidates_for(
                fact, as_of=as_of, borrow_left=borrow_open
            )
        )
        order_borrow_taken = took_at(RUNG_ORDER_BORROW)
        add(
            RUNG_ORDER_BORROW,
            "Can we borrow on hand from a later order?",
            taken=order_borrow_taken,
            offered=sum((_dec(c.get("qty")) for c in order_borrow_candidates), _ZERO),
            eligible=not outside_window,
            sources=drawn_at(RUNG_ORDER_BORROW),
            note=self._order_borrow_note(order_borrow_candidates),
            why=lambda outcome: (
                _RESERVE_WINDOW_RUNG_WHY
                if outside_window
                else self._order_borrow_why(
                    outcome, order_borrow_candidates, components, window=window
                )
            ),
        )

        # --------------------- 3. Can we borrow incoming from a later order?
        #
        # ONE document, whole (R33), so the candidates are the rows of the single document
        # the step chose - and `supply_open` is the walk's own ledger as this unit found it,
        # passed for the reason `borrow_open` is: the proof has to be the answer the engine
        # actually got, not a fresh read of a document an earlier unit has already spent.
        #
        # THE UNIT'S need, not the row's: "whole unit or nothing" is measured against what
        # the walk asked about (R10), and this trail is built per row. Asked with the row's
        # own smaller quantity, the proof offered a document the walk had refused.
        supply_borrow_candidates = (
            []
            if outside_window
            else self.supply.supply_borrow_candidates_for(
                fact, as_of=as_of, supply_left=supply_open, need=row.unit_qty
            )
        )
        add(
            RUNG_SUPPLY_BORROW,
            "Can we borrow incoming from a later order?",
            taken=took_at(RUNG_SUPPLY_BORROW),
            offered=sum((_dec(c.get("qty")) for c in supply_borrow_candidates), _ZERO),
            sources=drawn_at(RUNG_SUPPLY_BORROW),
            eligible=not outside_window,
            note=self._supply_borrow_note(supply_borrow_candidates),
            why=lambda outcome: (
                _RESERVE_WINDOW_RUNG_WHY
                if outside_window
                else self._supply_borrow_why(
                    outcome, supply_borrow_candidates, components, window=window
                )
            ),
        )

        # ------------------------------------------- 4. Buy
        #
        # The whole-line rule: a unit no single step could cover in full is bought WHOLE,
        # and the partial components the steps above produced are what the ladder tried,
        # not what it kept.
        bought = row.proposed.get(BUY, _ZERO)
        add(
            "buy",
            "Buy",
            taken=bought,
            offered=bought,
            why=lambda outcome: self._buy_why(outcome, outside_window),
        )
        # Where a person could still borrow from, and it is exactly what the borrow steps
        # above put on the table - step 2's windowed donors, then the manual same-group
        # donors `BorrowAddDialog` also lists. In offer order, de-duplicated, because one
        # location can answer both.
        offerable: List[str] = []
        for code in [
            # Step 1's OTHER-GROUP half: free stock the question named and the whole-unit
            # rule then refused. A person may still pick it in Amend, and it is exactly what
            # question 1 put on the table, so the Buy's sentence may name it (AC-V4).
            *(str(c["location"]) for c in other_group_candidates if c.get("location")),
            *(str(c["location"]) for c in order_borrow_candidates if c.get("location")),
            *(
                str(c["warehouse_code"])
                for c in [*group_borrow_donors, *ungrouped_donors]
                if c.get("warehouse_code")
            ),
        ]:
            if code not in offerable:
                offerable.append(code)
        return steps, offerable

    @staticmethod
    def _order_borrow_note(donors: Sequence[Dict[str, Any]]) -> Optional[str]:
        """Who was on the table, in the order they were offered (R4, R19)."""
        if not donors:
            return None
        shown = " · ".join(
            # "Borrow from another order" OFF: the location is the whole story (D2).
            f"{qty_text(_dec(c.get('qty')))} on hand at {c.get('location')}"
            if c.get("location_only")
            else f"{c.get('donor_so_number') or 'an unnamed order'}"
            + (f" line {c['donor_line_no']}" if c.get("donor_line_no") is not None else "")
            + f" {qty_text(_dec(c.get('qty')))} at {c.get('location')}"
            for c in donors[:3]
        )
        more = len(donors) - 3
        return f"{shown} (+{more} more)" if more > 0 else shown

    @staticmethod
    def _supply_borrow_note(candidates: Sequence[Dict[str, Any]]) -> Optional[str]:
        """The DOCUMENT on the table, named once with its arrival.

        Every candidate row here belongs to one document (R33), so this is one phrase and
        not a list: naming the same SPO three times because three orders hold parts of it
        reads as three documents.
        """
        if not candidates:
            return None
        first = candidates[0]
        when = first.get("arrival_date")
        total = sum((_dec(c.get("qty")) for c in candidates), _ZERO)
        arriving = f", arriving {_date_words(when)}" if when else ""
        return f"{first.get('supply_document') or 'One document'} {qty_text(total)}{arriving}"

    def _supply_borrow_why(
        self,
        outcome: str,
        candidates: Sequence[Dict[str, Any]],
        components: Sequence[Any],
        *,
        window: Optional[date] = None,
    ) -> str:
        """Step 3's sentence (AC-S4-1, AC-S4-5).

        On a YES it is the component's own sentence - the document, the arrival, the donor
        and the debt month - because a second wording of one fact is a second fact. On a NO
        it says which of the two refusals fired: no document covers the whole unit (R33),
        or there is no eligible document at all.
        """
        drawn = [
            component
            for component in components
            if getattr(component, "rung", None) == RUNG_SUPPLY_BORROW
        ]
        if drawn:
            return " ".join(component.reason for component in drawn)
        if outcome == "none_needed":
            return "This unit was already covered before a document was needed."
        when = f" dated on or after {_date_words(window)}" if window else ""
        return (
            "No single incoming document covers the whole of this unit, so none is "
            f"proposed: a document has to cover it whole (never half of one and half of "
            f"another), and it lends only where it is free or where a later order{when} "
            "holds it."
        )

    def _order_borrow_why(
        self,
        outcome: str,
        donors: Sequence[Dict[str, Any]],
        components: Sequence[Any],
        *,
        window: Optional[date] = None,
    ) -> str:
        """Step 2's sentence (AC-S3-4, AC-S3-11).

        On a YES it is the component's own sentence - the one that names the donor, the
        agent, the date and the debt month - because a second wording of one fact is a
        second fact. On a NO it names THE WINDOW DATE: "there was no donor" is not
        actionable, and "no order dated on or after 12 December 2026 holds any of this on
        hand" tells a planner exactly which orders would have qualified.
        """
        drawn = [
            component
            for component in components
            if getattr(component, "rung", None) == RUNG_ORDER_BORROW
        ]
        if drawn:
            return " ".join(component.reason for component in drawn)
        if outcome == "none_needed":
            return "This unit was already covered before a borrow was needed."
        if donors:
            named = ", ".join(
                f"{qty_text(_dec(c.get('qty')))} on hand at {c.get('location')}"
                if c.get("location_only")
                else f"{c.get('donor_so_number') or 'an unnamed order'} {qty_text(_dec(c.get('qty')))}"
                for c in donors[:3]
            )
            return (
                f"{named} could lend, but no single set of them covers the whole unit, so "
                "none of it is proposed."
            )
        when = f" dated on or after {_date_words(window)}" if window else ""
        return (
            f"No later order{when} holds any of this item on hand, so there is nothing to "
            "borrow. An order due sooner than that cannot wait for a replacement either."
        )

    def _pool_pile(
        self,
        row: _Row,
        fact: Any,
        balance: Decimal,
    ) -> Dict[str, Any]:
        """The pool's pile as rung 2 saw it, in AutoCount's vocabulary and this line's.

        The captain, on `Pool BRW | Had 0` beside `Available 1` in Inventory: "why it shows
        0?" Because `Had` is what the pool's OWN book ranked ahead of this line left, and
        Available is the pile's whole position - two true numbers, so both are printed with
        the subtraction between them. `available` is SIGNED and never clamped, as on a
        donor row.

        `cap` is always null (19 August 2026): the old reorder-level cap on a hot-selling
        line's pool draw is gone - dealer hot-selling offers the pool nothing at all, and
        project hot-selling caps it against `available` instead. Kept for wire
        compatibility, never a number that would read as a limit nobody set.
        """
        pool_id = str(fact.pool.id)
        key = (fact.product_id, pool_id)
        triple = self._pool_piles.get(
            key, {"on_hand": _ZERO, "so_qty": _ZERO, "spo_qty": _ZERO}
        )
        on_hand, reserved = self._levels.get(key, (triple["on_hand"], _ZERO))
        return {
            "location": fact.pool_code,
            "warehouse_id": pool_id,
            "on_hand": qty_text(on_hand),
            "so_qty": qty_text(triple["so_qty"]),
            "spo_qty": qty_text(triple["spo_qty"]),
            "available": qty_text(on_hand - triple["so_qty"] + triple["spo_qty"]),
            "reserved": qty_text(reserved),
            "free": qty_text(self._free.get(key, _ZERO)),
            "claimed_ahead_qty": qty_text(fact.pool_claimed_qty),
            "claimed_ahead_lines": int(fact.pool_claimed_lines or 0),
            "left": qty_text(balance),
            "reorder_level": qty_text(max(_dec(fact.pool_reorder_level), _ZERO)),
            "cap": None,
        }

    @staticmethod
    def _hot_prefix(fact: Any, *, dealer: bool) -> str:
        """"Dealer hot-selling at BRW" or the project equivalent - the evidence sentence the
        pool rung leads with (PLAN 3.3a).

        The captain: "don't give me jargon like abc classification, just tell me hot selling
        or cold selling, at project or retail" (19 August 2026) - so this names the class and
        the location, never the letter or the word behind it. The number itself (which rank,
        out of how many, what share) is a press away on the trail's Proof button
        (`GET .../fulfilment-planning/classification`).
        """
        if dealer:
            where = ", ".join(fact.dealer_hot_selling_where or [])
            return f"Dealer hot-selling at {where}" if where else "Dealer hot-selling"
        where = ", ".join(fact.project_hot_selling_where or [])
        return f"Project hot-selling at {where}" if where else "Project hot-selling"

    @staticmethod
    def _cold_prefix(fact: Any) -> str:
        """"Cold at retail and project" or the one class that carries evidence - the plain
        word for "classified, but never ranked A", read off `dealer_classified` /
        `project_classified` rather than off `classification_unavailable`, which only
        answers "any evidence at all", not which class it was."""
        classes = []
        if fact.dealer_classified:
            classes.append("retail")
        if fact.project_classified:
            classes.append("project")
        return f"Cold at {' and '.join(classes)}"

    # ------------------------------------------------------------------ why, per rung

    @staticmethod
    def _pool_why_no_own(
        outcome: str, capacity_by_location: Dict[str, Decimal]
    ) -> str:
        """Why rung 2 ended where it did for a line whose own location carries no pool of
        its own (section E rule 2, section 8): the OTHER active site pools are still real
        sources, and the sentence names which of them offered anything.
        """
        if outcome == "none_needed":
            return _COVERED_BEFORE
        offering = [
            location for location, amount in capacity_by_location.items() if amount > _ZERO
        ]
        if not offering:
            return (
                "This location has no pool of its own, and no other active site pool "
                "has anything to offer."
            )
        named = ", ".join(offering)
        if outcome == "took":
            return (
                f"This location has no pool of its own; {named} offered stock and this "
                "line took some of it."
            )
        return (
            f"This location has no pool of its own; {named} offered stock, but none of "
            "it reached this line."
        )

    @staticmethod
    def _pool_note(pool_chain: Sequence[Dict[str, Any]]) -> Optional[str]:
        """Which pools were opened, under question 2's own row.

        The captain, on SO415472: "why is BRW the only pool considered? What about MWH, DC1,
        WH3?" They were all considered - the five are one pile - but only the pool a proposal
        happened to cite was named, so a pool that was opened and gave nothing looked exactly
        like one that was never opened.

        The dealer hot-selling line it used to carry is gone with the gate (v8, R-A): every
        pool in the chain is opened for every item now, and the share is what is kept back -
        and `fact` went with it (review round 2, nit 7), because a parameter nothing reads
        is a rule a reader still believes in.
        """
        if not pool_chain:
            return "no shared pool"
        opened = ", ".join(
            str(entry.get("location")) for entry in pool_chain if entry.get("location")
        )
        # AC-N.10: this note sits under the pool question's own `why` sentence, which ends
        # in a period - a lowercase, unpunctuated "checked ..." read as that sentence
        # running on rather than a note of its own, so it is capitalized and terminated
        # here to stand as its own sentence.
        return f"Checked {opened}." if opened else None

    def _pool_answer_why(
        self,
        fact: Any,
        pool_chain: Sequence[Dict[str, Any]],
        taken: Decimal,
        pile: Optional[Dict[str, Any]],
        pool_open: Optional[Mapping[str, Decimal]],
        outcome: str,
        pools_net: Optional[Decimal] = None,
        borrow_donors: Sequence[Dict[str, Any]] = (),
        components: Sequence[Any] = (),
        share_left: Optional[Mapping[str, Decimal]] = None,
    ) -> str:
        """Question 2's one sentence, with the PILE's net inside it (section 1e).

        `pools_net` is the walk's running net for this unit when the caller has one;
        `None` reads the fact's own. `share_left` is the same for each pool's own project
        share (C3): without it the sentence named a pool whose share an earlier line of the
        walk had already spent.

        One entry point where the old trail had three - the line's own pool, a line with no
        pool of its own, and no active pool anywhere - because the answer to "can we take
        from the pool" is about the five pools together and never about which of them this
        location happens to call its own. `_pool_why` still writes the sentence for a line
        that has a pool of its own, since it is the one that can quote the pile's triple.

        STEP 4b comes first when it fired (R34): its component's own sentence names the pool
        order that lent, and that is the fact a planner acts on. The free-pile sentences
        below all describe the pile, which is not where the quantity came from - "No shared
        pool holds this product." was printed over a borrow of 30 from a pool order, because
        the pile was empty and the borrow was invisible here.
        """
        drawn = [
            component
            for component in components
            if getattr(component, "rung", None) == RUNG_POOL
            # The borrow half, not the free share. Read off `kind` rather than the donor's
            # name: "Borrow from another order" OFF borrows the same stock nameless (D2).
            and getattr(component, "kind", None) == BORROW
        ]
        if drawn:
            return " ".join(component.reason for component in drawn)
        # R-N (3 Sep 2026): step 0 walks the WHOLE chain, so the ordinary answer is several
        # pools at once - and the sentences below describe the ASKING pool's pile, which
        # would report the whole step's take ("this line takes 8") against one of the two
        # piles it actually came from. Where more than one pool answered, the pools' own
        # sentences ARE the answer, which is what the walk's own option row states too, so
        # the trail and the decision panel cannot spell one composition two ways.
        share = [
            component
            for component in components
            if getattr(component, "rung", None) == RUNG_POOL
            and getattr(component, "source_location", None)
        ]
        if len({component.source_location for component in share}) > 1:
            return " ".join(component.reason for component in share)
        if not pool_chain:
            if borrow_donors:
                named = ", ".join(
                    f"{qty_text(_dec(c.get('qty')))} on hand at {c.get('location')}"
                    if c.get("location_only")
                    else f"{c.get('donor_so_number') or 'an unnamed pool order'} "
                    f"{qty_text(_dec(c.get('qty')))}"
                    for c in borrow_donors[:3]
                )
                return (
                    f"The shared pile holds none of this, and {named} could lend from the "
                    "pool, but no single set of them covers the whole unit."
                )
            return "No shared pool holds this product."
        # `pool_reserve_capacity` is the SAME cap the ladder applied, read rather than
        # forked: an oversold pile whose raw free stock still reads positive must not show
        # an offer the engine itself refused.
        net = fact.pools_net if pools_net is None else pools_net
        capacity = pool_reserve_capacity(
            pools=list(pool_chain),
            pools_net=net,
        )
        # LADDER V8 (R-B, R-L, R-N): what the pile may give THIS line is EACH pool's own
        # share, walked the way the engine walks it, so the sentence never offers a figure
        # the walk could not have taken and never prints 0 beside a draw another pool made.
        # One allowance spread over every location was the round-1 shape, and it read
        # `offered=0` under `taken=300` (review round 2, S5).
        capacity_by_location: Dict[str, Decimal] = {
            location: amount
            for location, amount, _allowance in pool_share_capacity(
                pools=list(pool_chain),
                pools_net=net,
                pool_share_pct=self.supply.fulfilment_settings().get("pool_share_pct"),
                share_left=share_left,
            )
        }
        pools_net_refused = not capacity and any(
            _dec(entry.get("free")) > _ZERO for entry in pool_chain
        )
        if pile is not None:
            return self._pool_why(
                fact,
                outcome,
                pile,
                _own_pool_floor(fact, pool_open),
                capacity_by_location.get(fact.pool_code, _ZERO),
                taken,
                pools_net_refused,
                pools_net=net,
            )
        return self._pool_why_no_own(outcome, capacity_by_location)

    def _pool_class_prefix(self, fact: Any) -> str:
        """How this item's demand class reads in front of a pool sentence.

        One copy, because three branches of `_pool_why` need the same phrase and the
        captain's instruction about it is exact: "don't give me jargon like abc
        classification, just tell me hot selling or cold selling, at project or retail".
        """
        if fact.is_dealer_hot_selling:
            return self._hot_prefix(fact, dealer=True)
        if fact.is_project_hot_selling:
            return self._hot_prefix(fact, dealer=False)
        if fact.classification_unavailable:
            return (
                "Not classified (no retail or project deliveries of this item in the last "
                "12 months)"
            )
        return self._cold_prefix(fact)

    def _pool_why(
        self,
        fact: Any,
        outcome: str,
        pile: Dict[str, Any],
        balance: Decimal,
        offered: Decimal,
        taken: Decimal,
        pools_net_refused: bool = False,
        pools_net: Optional[Decimal] = None,
    ) -> str:
        """Why the pool rung ended where it did, with the pool's own numbers in the sentence.

        The captain, on `Pool BRW | Had 0` beside `Available 1` in Inventory: "why it shows
        0?" - and on `Pool BRW | 4 | took 4`: "is it taken because for location BRW there is
        no outstanding quantity?" The answer is the pool's on hand, its availability, and what
        its own orders ranked ahead of this line claim, said together.

        Amended 19 August 2026 (PLAN 3.3a): hot-selling is what gates the pool now, by demand
        class - dealer wins when both flags are set, and each has its own sentence. A
        classified-but-not-hot item ("Cold at retail" / "Cold at project" / both) is offered
        the pool as it would be for any ordinary item, said in those words; an unclassified
        item (no delivered demand of either class, ever) is offered the same way and says so
        too - "Not classified" is a different answer from "cold" and must not print as it.

        Amended again the same day: the captain, reading the trail, asked for the plain word
        instead of the classification jargon - "don't give me jargon like abc classification,
        just tell me hot selling or cold selling, at project or retail". Every sentence below
        now names hot/cold/not-classified and never the letter or the word "ABC"; the ranked
        number behind each verdict is the trail's Proof button
        (`GET .../fulfilment-planning/classification`), not this sentence.
        """
        code = fact.pool_code
        if outcome == "none_needed":
            return _COVERED_BEFORE
        # LADDER V8 (R-A): the dealer hot-selling gate is retired, so there is no sentence
        # here refusing the pile for a hot item any more. What keeps stock for dealers is the
        # SHARE - a percentage of every pool, for every item - and the ordinary sentences
        # below say what the pile is and what it gave, with `_pool_class_prefix` still naming
        # hot or cold in front of them because the captain reads that either way.
        # LADDER V4 (section 1d): the five site pools are ONE pile, and that pile's net is
        # now the ONLY thing that bounds the rung - `BRW -103` beside `DC1 +1` nets -102,
        # and the 1 at DC1 is stock the shared book already owes at BRW. Said wherever the
        # net is what refused the draw, WITH the classification in front of it, because
        # "hot or cold" is what the captain asked to be told and the net is a different
        # fact from it.
        if pools_net_refused:
            return (
                f"{self._pool_class_prefix(fact)}: the site pools net "
                f"{qty_text(_dec(getattr(fact, 'pools_net', _ZERO) if pools_net is None else pools_net))} between them, so no "
                "pool is offered."
            )
        available = _dec(pile.get("available"))
        if fact.is_project_hot_selling:
            # Ladder v4: the classification is still NAMED, because the captain reads it,
            # but it no longer changes the arithmetic - the five pools' own net bounds this
            # draw exactly as it bounds a cold item's.
            base = (
                f"{self._pool_class_prefix(fact)}: the site pools net "
                f"{qty_text(_dec(getattr(fact, 'pools_net', _ZERO) if pools_net is None else pools_net))} between them, and "
                f"{qty_text(offered)} is offered here."
            )
            return f"{base} This line takes {qty_text(taken)}." if outcome == "took" else base
        if fact.classification_unavailable:
            base = (
                "Not classified (no retail or project deliveries of this item in the last "
                f"12 months), so {code} is offered as for a cold item."
            )
            return f"{base} This line takes {qty_text(taken)}." if outcome == "took" else base
        # LADDER V8 (R-A): the dealer-hot early return above is gone, so these sentences
        # are now the ones a HOT item reads too - and they have to name the class the
        # captain asked for rather than defaulting every reader to "cold".
        prefix = self._pool_class_prefix(fact)
        on_hand = _dec(pile.get("on_hand"))
        # The "Available" the captain was holding the rung against is the Inventory screen's
        # (on hand less reserved), so that is the figure the sentence quotes; AutoCount's
        # signed position (on hand - SO + SPO) is beside it in the sub-table.
        on_hand_available = on_hand - _dec(pile.get("reserved"))
        claimed = _dec(pile.get("claimed_ahead_qty"))
        if available <= _ZERO and balance > _ZERO:
            # The queue-netted `balance` (this line's own book, "Had") reads positive -
            # nothing of THIS line's own queue claims the pile - while the pile's own
            # SIGNED position is not: other orders across the WHOLE book already outsell
            # what the pool holds. `pool_reserve_capacity` caps the draw at that position,
            # so the offered figure must say why: the pool is oversold, not merely empty
            # (the `balance == 0` case below is a different, already-explained story - the
            # own queue claimed it all).
            return (
                f"{prefix}: {code} is oversold ({qty_text(available)} available), so "
                "nothing is offered."
            )
        left = (
            f"{prefix}, so {code} is offered: {qty_text(balance)} left after its own queue "
            "ahead of this line"
        )
        if outcome == "took":
            return f"{left}; this line takes {qty_text(taken)}."
        if claimed > _ZERO:
            return (
                f"{prefix}, so {code} is offered. {code} holds {qty_text(on_hand)} on hand "
                f"(Available {qty_text(on_hand_available)} in stock), but {code}'s own orders "
                f"ranked ahead of this line claim {qty_text(claimed)}, so {qty_text(balance)} "
                "is left."
            )
        if on_hand > _ZERO:
            return (
                f"{prefix}, so {code} is offered. {code} holds {qty_text(on_hand)} on hand "
                f"(Available {qty_text(on_hand_available)} in stock), but none is left for "
                "this line."
            )
        return f"{prefix}, so {code} is offered, but there is no stock at {code}."

    @staticmethod
    def _group_take_note(candidates: List[Dict[str, Any]], fact: Any) -> Optional[str]:
        """What each of the group's locations OFFERED, floor and water told apart.

        A location routinely appears twice - what is on its floor and what is on its way -
        and "BRW-SMC 1 · BRW-SMC 10" reads as a repeat rather than as two different piles.
        The water half says so.
        """
        if not candidates:
            return None
        shown = " · ".join(
            f"{c['location']} {qty_text(_dec(c.get('qty')))}"
            + (" (on the water)" if c.get("water") else "")
            for c in candidates[:3]
        )
        more = len(candidates) - 3
        return f"{shown} (+{more} more)" if more > 0 else shown

    def _group_take_why(
        self,
        outcome: str,
        candidates: List[Dict[str, Any]],
        fact: Any,
        water: Sequence[Any] = (),
        drawn: Sequence[Tuple[str, Decimal, bool]] = (),
        other: Sequence[Dict[str, Any]] = (),
        offer: Optional[Decimal] = None,
        own_arrival: Sequence[
            Tuple[
                Decimal, Optional[str], Optional[str], Optional[Decimal], Optional[Decimal],
                Optional[str],
            ]
        ] = (),
    ) -> str:
        """Why rung 2 (the ownership group) ended where it did (section 1d).

        THIS LINE'S OWN LOCATION IS IN THIS RUNG, so the sentences say "group location",
        not "sibling": telling a planner "no sibling has stock" while their own location is
        the one that came up empty names the wrong place to go and look.

        Every sentence carries the group's NET and, where they differ, what that net leaves
        for THIS line - the two are different numbers (`max(net + this line's own quantity,
        0)`, AC-L14) and a reader shown only the first cannot check the second.

        LADDER V5 (section 1e): there is no rung above this one for the group's SPO to be
        spent on. The water is inside the net, and inside this question's own offer, so a
        group whose only cover is an SPO answers Yes here - but only the share of it landing
        by the required date (captain, 27 August 2026). The rest is NAMED, with its date,
        and never drawn: "30 on the water, arrives 1 Mar, not counted" is the one sentence
        that tells a planner why a group with stock coming still bought.

        R7: `own_arrival` is what THIS rung drew as own-arrival credit, said FIRST and by
        the document it landed on - "20 landed for this line on ..., taken first" - ahead
        of whatever the ordinary group-net sentence below it says, because the two are
        different facts answering the same question.

        R7 FOLLOW-UP (`PLAN-r7-landed-reads-spo-received.md`, R1/R3): that document is now
        the SPO the goods physically landed on, never the PO - a PO line's own
        `qty_received` is the AutoCount TRANSFER onto a shipping order, not a receipt - so
        the noun in front of it is dropped rather than saying "PO" of an SPO number.

        #1362 (owner ruling 29 Sep 2026): what landed for the line and how much of it is
        still free at the bin, taken first, are two facts and are said apart - "100 landed
        for this line on SPO-2026/06-0152; 40 free at BRW-BB, taken first." - the engine's
        own `own_arrival_reason`, so the trail and the component's reason cannot drift.
        """
        prefix = "".join(
            (
                own_arrival_reason(
                    location, qty, document, landed=landed, free=free,
                    landed_text=landed_text,
                )
                + ". "
                if location and landed is not None and free is not None
                else f"{qty_text(qty)} landed for this line{landed_on(document)}, taken first. "
            )
            for qty, document, location, landed, free, landed_text in own_arrival
            if qty > _ZERO
        )
        if outcome == "none_needed":
            return prefix + _COVERED_BEFORE
        if not fact.group_code:
            return prefix + (
                "This location carries no ownership group, so there is no group to take "
                "from."
            )
        net = _dec(getattr(fact, "group_net", _ZERO))
        # What step 1 could actually put on the table: the own group's date-aware share plus
        # the other project groups' free piles (v7.1, R5/R24), stated by the caller because
        # only the caller ran the walk that produced it.
        offer = (
            _dec(getattr(fact, "group_offer", _ZERO)) if offer is None else _dec(offer)
        )
        late = self._late_water_clause(water)
        candidates = [*candidates, *other]
        if not candidates:
            # LADDER V4 (section 1d): the group is ONE pile, so the sentence is about the
            # group's net and never about a single warehouse. `MWH-IB` holding 7000 is not
            # an answer to "why nothing" while `BRW-IB` owes 27,804 against 5,290.
            if offer <= _ZERO:
                return prefix + (
                    f"The {fact.group_code} group nets {qty_text(net)}, so there is "
                    "nothing left for this line - whatever sits at any one of its "
                    f"locations is already owed at another.{late}"
                )
            return prefix + (
                f"The {fact.group_code} group nets {qty_text(net)}, leaving "
                f"{qty_text(offer)} for this line, and none of it sits free at a location "
                f"this line can draw from.{late}"
            )
        if outcome == "took":
            # WHERE it came from, and whether it was floor or water: the two halves of this
            # question are one walk, and "20 from BRW-IB, 20 on the water to MWH-IB" is the
            # captain's own wording and a sentence a planner can check against the cell's
            # own table.
            taken_at = ", ".join(
                (
                    f"{qty_text(qty)} on the water to {location}"
                    if is_water
                    else f"{qty_text(qty)} from {location}"
                )
                for location, qty, is_water in drawn
            ) or ", ".join(str(c["location"]) for c in candidates)
            return prefix + (
                f"The {fact.group_code} group nets {qty_text(net)}, leaving "
                f"{qty_text(offer)} for this line; it was drawn as {taken_at}.{late}"
            )
        offered_at = ", ".join(
            (
                f"{c['location']} (on the water)"
                if c.get("water")
                else str(c["location"])
            )
            for c in candidates
        )
        return prefix + (
            f"The {fact.group_code} group nets {qty_text(net)}, leaving {qty_text(offer)} "
            f"for this line at {offered_at}. {_WHOLE_LINE_RULE_DROPPED}{late}"
        )

    @staticmethod
    def _late_water_clause(water: Sequence[Any]) -> str:
        """"30 on the water to MWH-IB, arriving 1 Mar 2027, after the required date, so it
        is not counted." - said whenever the group holds a promise this line cannot use.

        The NET counts it (it is the group's position, not this line's promise) and the DRAW
        does not, and those two facts read as a contradiction unless the row says both. The
        captain asked for the date by name: a buyer who can see 1 March can go and chase it.
        """
        late = [entry for entry in water if _dec(getattr(entry, "late_qty", 0)) > _ZERO]
        if not late:
            return ""
        named = ", ".join(
            f"{qty_text(_dec(entry.late_qty))} to {entry.location}"
            + (
                f" arriving {date_text(entry.late_from)}"
                if getattr(entry, "late_from", None)
                else " on an unstated date"
            )
            for entry in late[:3]
        )
        more = len(late) - 3
        tail = f" (+{more} more)" if more > 0 else ""
        return (
            f" The group also has {named}{tail} on the water after the required date, so "
            "none of that is counted here."
        )

    def _donor_group_net(self, fact: Any, group: Optional[str]) -> Decimal:
        """What a DONOR group nets as a whole (section 1d), through the ladder's own reader.

        The same `netting()` the engine drew on, so the figure in the sentence and the
        figure the engine obeyed cannot come apart.
        """
        if not group or not fact.product_id:
            return _ZERO
        return _dec(self.supply.netting().donor_group_net(fact.product_id, group).net)

    @staticmethod
    def _buy_why(outcome: str, outside_window: bool = False) -> str:
        """Why the remainder is bought.

        Beyond the reserve window "nothing left to take" is not what happened: none of the
        four questions was asked at all, and there may well be stock at a donor this line is
        simply not allowed to take. The rule says so instead.
        """
        if outcome != "took":
            return _COVERED_BEFORE
        return (
            "The delivery date is beyond the lead time window, so the stock is kept for "
            "nearer orders and the quantity is bought."
            if outside_window
            else "Nothing left to take, so the remainder is bought."
        )

    def _buy_reason(
        self,
        row: _Row,
        component: Any = None,
        offerable: Sequence[str] = (),
    ) -> str:
        """Why this quantity is being bought, said in a way a person can check.

        Three cases the earlier build got wrong, all visible in one card the captain read:

        * the row that took the stock belonged to the SAME sales order, and the sentence read
          "went to SO396563, which outranks this line" on a contribution FROM SO396563 - an
          order cannot outrank itself, and what actually happened is that its own earlier line
          took it;
        * the two scores were EQUAL and the sentence still said "outranks ... 0.00 to 0.00".
          A tie is not a ranking; what decided it was the tiebreaker, so the tiebreaker is what
          the sentence names;
        * nothing was said about Borrow, so a Buy read as "this stock exists nowhere" when it
          existed one location away.

        And a fourth, from SO414341: a line beyond its ATP reserve window was bought BY A
        RULE, and the contest sentence was written over the rule's own. Precedence, not a
        fourth branch: when the engine names the rule that decided the line, that sentence
        wins whole and unedited - `boardSuggestion.ts` matches it verbatim to tell a
        "beyond the window" Buy from a "nothing free anywhere" one. The contest sentence
        below only ever explains a Buy the ARITHMETIC produced.
        """
        if row.outside_reserve_window and component is not None:
            return component.reason
        taker = row.last_taker if row.contested else None
        where = f" at {row.location}" if row.location else ""
        if taker is None:
            reason = (
                f"Nothing free{where} by the delivery date, so the quantity is bought."
            )
        elif taker.sales_order_id == row.sales_order_id:
            reason = (
                f"An earlier line of this sales order (line {taker.line_no}) took the free "
                f"stock{where}, so the residual is bought."
            )
        elif taker.rank_score > row.rank_score:
            reason = (
                f"Free stock{where} went to {taker.so_number}, which outranks this line "
                f"{taker.rank_score:.2f} to {row.rank_score:.2f}."
            )
        else:
            reason = (
                f"Free stock{where} went to {taker.so_number}, which ties this line on rank "
                f"({row.rank_score:.2f}) and was served first by the tiebreaker: sales order "
                "number, then line number."
            )
        # AC-V4: the note must never say borrowing is possible where the trail says nothing
        # is left. ONE source of truth for that, and it is the trail itself - `offerable` is
        # what questions 3 and 4 actually offered (`_trail`). Re-filtering the raw donor
        # list here was the defect: it dropped only the donors the CAP refused, so a donor
        # refused because its own GROUP nets nothing was still named one line under a
        # question 3 that had just said no group has anything left.
        if offerable:
            where_from = ", ".join(offerable[:3])
            reason += (
                f" Borrowing is possible from {where_from}, which is a decision for a person "
                "and carries a reason."
            )
        return reason

    def _source(
        self, component, row: _Row, offerable: Sequence[str] = ()
    ) -> Dict[str, Any]:
        """One proposed source, with the sentence its rule wrote.

        The engine's reasons are fragments meant to follow "Reserve 10:", so they are stated
        as sentences here. A Buy says why it is a Buy, because "why did this order lose" is the
        question the planner opens the cell with (13.5).
        """
        reason = component.reason
        if component.kind == BUY:
            reason = self._buy_reason(row, component, offerable)
        else:
            reason = reason[:1].upper() + reason[1:] + "."
        return {
            "kind": component.kind,
            "qty": qty_text(component.qty),
            "location": component.source_location,
            #: Addressing only, never rendered: a Reserve component of the confirmation names
            #: its warehouse by id while the screen names it by code. Null for a Buy, which is
            #: not held anywhere, and for the unplannable row, which has no location at all.
            "warehouse_id": row.warehouse_ids.get(component.source_location),
            "reason": reason,
            # The SPO number and its date are IN the engine's sentence ("SPO 202703-S0011
            # arrives on 2027-03-01"), which is what the cell shows. Parsing them back out of
            # it to fill two more fields would be a second source of the same fact.
            "spo_number": None,
            # STEP 3's arrival IS a field, and it is not the same case: the drill row prints
            # "SPO 202607-S0105, arriving 15 Sep 2026" beside the quantity (AC-S4-5), and
            # the alternative to a field is the client parsing the sentence, which is the
            # one thing the "the server writes the words" rule forbids.
            "arrival_date": getattr(component, "arrival_date", None),
            "supply_key": getattr(component, "supply_key", None),
            "supply_document": getattr(component, "supply_document", None),
            "rung": getattr(component, "rung", None),
            #: WHICH LADDER wrote this. A LIVE suggestion is today's by definition, so it
            #: carries today's version; a FROZEN one carries whatever was stamped when it
            #: was frozen, and `None` for a snapshot older than the stamp. That is the only
            #: thing that lets the screen label a stale suggestion without guessing, and
            #: without it the label appeared on every line including the live ones (AC-V8).
            "ladder": LADDER_VERSION,
            "donor_so_number": getattr(component, "donor_so_number", None),
            "donor_line_no": getattr(component, "donor_line_no", None),
            "donor_agent_code": getattr(component, "donor_agent_code", None),
            "same_agent": bool(getattr(component, "same_agent", False)),
            "donor_core_line_id": getattr(component, "donor_core_line_id", None),
            "donor_required_date": getattr(component, "donor_required_date", None),
            #: R7: `"own_arrival"` on a Reserve born from goods that landed for this line
            #: (or the rest of its own order), `None` on an ordinary group-take Reserve -
            #: what the cell's "Received N (own arrival)" chip keys off.
            "source": getattr(component, "source", None),
            #: #1362 item 4: how an own-arrival credit that is partly a sibling's spare is
            #: said, so the amend refusal names the purchase the way the board does.
            "landed_text": getattr(component, "landed_text", None),
        }

    # ------------------------------------------------------------ the answer

    def _warehouses_by_group(self) -> Dict[str, List[Tuple[str, str]]]:
        """Every warehouse fulfilment planning reads, indexed by the ownership group its
        code carries. A bin flagged OUT of planning is in no group at all (R17).

        ONE read for the whole board (the supply service's own request-scoped
        `_planning_warehouses`, which ladder v4's netting has already paid for), then the
        ladder's OWN suffix rule (`sales_agent_service.group_of_warehouse_code`) decides
        which group each code belongs to. Filtering in SQL with a `LIKE '%-BB'` would be a
        second definition of "the BB group", and the two would drift the first time a code
        grew a second hyphen.

        Indexed rather than filtered per call because `_cited_locations` asks per CELL, and
        which group a donor belongs to is not known until the engine has composed: a board
        of 400 cells was scanning the whole warehouse table 400 times.
        """
        if self._warehouses_by_group_cache is None:
            index: Dict[str, List[Tuple[str, str]]] = defaultdict(list)
            for warehouse_id, warehouse in self.supply._planning_warehouses().items():
                group = sales_agent_service.group_of_warehouse_code(
                    warehouse.warehouse_code
                )
                if group:
                    index[group].append((str(warehouse_id), warehouse.warehouse_code))
            for pairs in index.values():
                pairs.sort(key=lambda pair: pair[1])
            self._warehouses_by_group_cache = dict(index)
        return self._warehouses_by_group_cache

    def _warehouses_for_groups(
        self, groups: Sequence[Optional[str]]
    ) -> Dict[str, List[Tuple[str, str]]]:
        """`{group: [(warehouse_id, warehouse_code), ...]}` for the groups asked about."""
        index = self._warehouses_by_group()
        return {
            key: index[key]
            for key in {
                key
                for key in (
                    sales_agent_service.normalize_location_group(g) for g in groups
                )
                if key
            }
            if key in index
        }

    def _pool_by_warehouse(self) -> Dict[str, str]:
        """`{warehouse_id: pool_warehouse_id}` for every active warehouse, one query.

        The FK is the authoritative test, not the code's shape: on the live book `BRW-BB`
        draws on `BRW` and both are plain codes, but a client whose codes look nothing like
        Sorento's repoints rows rather than needing code (`Warehouse.pool_warehouse_id`).
        """
        rows = (
            self.db.query(Warehouse.id, Warehouse.pool_warehouse_id)
            .filter(
                Warehouse.is_active.is_(True),
                Warehouse.pool_warehouse_id.isnot(None),
            )
            .all()
        )
        return {str(warehouse_id): str(pool_id) for warehouse_id, pool_id in rows}

    # `_open_po_balance` moved to `ProjectSupplyService.open_po_balance` (S4,
    # `PLAN-local-supplier-oi-routing.md`) - called above as `self.supply.open_po_balance`,
    # the ONE implementation both this board and a donor's own `donor_location` read.

    def _group_note(self, members: Sequence[_Row], group_codes: Sequence[str]) -> Optional[str]:
        """Why this cell is showing one location instead of a group, when it is.

        Only ever set when NO group was resolved. Silence would read as "this product lives in
        exactly one place", which is the belief the group listing exists to correct.
        """
        if group_codes:
            return None
        agents = sorted({row.agent_code for row in members if row.agent_code})
        if not agents:
            return "No sales agent on the order, so no location group."
        if len(agents) == 1:
            return f"Agent {agents[0]} has no location group."
        return f"Agents {', '.join(agents)} have no location group."

    def _cell(self, item_code: str, bucket_key: str, members: Sequence[_Row]) -> Dict[str, Any]:
        by_location: Dict[Optional[str], List[_Row]] = defaultdict(list)
        for row in members:
            by_location[row.location].append(row)
        # The rest of the agents' ownership group, appended after the locations this cell's own
        # lines named. "Can I fulfil this" is a question about the whole group - BRW-BB alone
        # answers a narrower one - and the line's own location leads because it is the one the
        # order actually states, group or no group.
        group_codes = sorted({
            group
            for group in (
                sales_agent_service.normalize_location_group(row.agent_location_group)
                for row in members
            )
            if group and group in self._group_warehouses
        })
        # ONE TABLE PER CONTRIBUTING LINE (R1, B1). The netting used to come out of the whole
        # CELL, and on a cell holding two 24s at a location with 10 on hand the table printed
        # Available 9 while the ladder offered each line nothing: `_group_offer` is
        # `max(group net + THIS line's own quantity, 0)`, never plus the cell's. So the asking
        # line is the unit - the drawer, the decision panel's "N available" and the suggestion
        # then answer one question with one number.
        for row in members:
            self._locations_by_row[row.key] = self._locations_for(
                members, by_location, group_codes, own_demand=self._own_demand(row)
            )
        # The cell's own table is the FIRST contributing line's, which is the highest ranked
        # (members are sorted) and, on the overwhelming majority of cells, the only one. The
        # drawer switches it to whichever line the planner expands.
        locations = self._locations_by_row.get(members[0].key, []) if members else []
        return {
            "item_code": item_code,
            "bucket_key": bucket_key,
            #: The ownership group whose warehouses are listed beside the line's own, or None
            #: when none could be resolved - in which case `location_group_note` says why.
            "location_group": " / ".join(group_codes) or None,
            "location_group_note": self._group_note(members, group_codes),
            # Summed across every contributing line INCLUDING the unplannable ones: the demand
            # is not hidden because the source record is incomplete (13.7).
            "total_qty": qty_text(sum((row.qty for row in members), _ZERO)),
            "locations": locations,
            "contributions": [self._contribution(row) for row in members],
            "unplannable_count": sum(1 for row in members if row.unplannable),
            "contested_count": sum(1 for row in members if row.contested),
            #: How many DISTINCT sales orders contribute here, and whether the ranking actually
            #: told any two of these rows apart. "The active policy separates none of these
            #: rows" is true of a cell holding one line, and of a cell holding several lines of
            #: one order - and in both cases it reads as a policy failure when nothing failed.
            #: These two say which case it is, so the screen can word it honestly.
            "distinct_order_count": len({row.sales_order_id for row in members}),
            "rank_separates": len({round(float(row.rank_score), 6) for row in members}) > 1,
            # Lines whose OWN delivery date is already past. Counted here so the screen can say
            # "160 of 160 lines are past their delivery date" without walking every
            # contribution, which is the information the aggregate Overdue column used to carry
            # and the only part of it worth keeping.
            "past_count": sum(1 for row in members if row.is_past),
        }

    def _locations_for(
        self,
        members: Sequence[_Row],
        by_location: Mapping[Optional[str], List[_Row]],
        group_codes: Sequence[str],
        *,
        own_demand: Mapping[Tuple[str, str], Decimal],
    ) -> List[Dict[str, Any]]:
        """The whole location table, as ONE asking line sees it.

        `own_demand` is that line's own open quantity, and the only thing that changes
        between two lines of the same cell: the demand each row states, and the stock behind
        it, are the cell's own facts either way.
        """
        locations = sorted(
            (
                self._location(location, rows, where=WHERE_OWN, own_demand=own_demand)
                for location, rows in by_location.items()
            ),
            key=lambda entry: (-Decimal(entry["qty_demand"]), entry["location"] or ""),
        )
        locations.extend(
            self._group_locations(members, locations, group_codes, own_demand=own_demand)
        )
        locations.extend(self._pool_locations(members, locations, own_demand=own_demand))
        locations.extend(self._cited_locations(members, locations, own_demand=own_demand))
        return locations

    def _group_locations(
        self,
        members: Sequence[_Row],
        already: Sequence[Dict[str, Any]],
        group_codes: Sequence[str],
        *,
        own_demand: Mapping[Tuple[str, str], Decimal],
    ) -> List[Dict[str, Any]]:
        """The group's OTHER warehouses: no demand of this cell sits there, stock does.

        Emitted only for a cell that holds ONE product. Two products behind one item code is a
        real case on this book (`B2155-NL-BLUE`), and a pivoted cell can hold several products
        outright - so a group row would have to say which product it counts, and this table has
        no column for that. A cell that cannot answer honestly says nothing extra.

        Ordered by code, after the locations the lines themselves named.
        """
        if not group_codes:
            return []
        product_id = self._single_product(members)
        if product_id is None:
            return []
        seen = {entry["location"] for entry in already}
        out: List[Dict[str, Any]] = []
        for group in group_codes:
            for warehouse_id, code in self._group_warehouses.get(group, []):
                if code in seen:
                    continue
                seen.add(code)
                out.append(
                    self._location(
                        code, (), product_id=product_id, warehouse_id=warehouse_id,
                        where=WHERE_GROUP, own_demand=own_demand,
                    )
                )
        return out

    def _pool_locations(
        self,
        members: Sequence[_Row],
        already: Sequence[Dict[str, Any]],
        *,
        own_demand: Mapping[Tuple[str, str], Decimal],
    ) -> List[Dict[str, Any]]:
        """EVERY active site pool, this line's own site first (AC-B1).

        The captain, on SO415472: "why is BRW the only pool considered? What about MWH, DC1,
        WH3?" They were considered - `_pool_chain` walks all of them for the ladder - but the
        table listed only the pool a proposal happened to cite, so a pool that was opened and
        gave nothing looked exactly like one that was never opened. A row reading 0 answers
        the question; a missing row leaves it open.

        Read off the SAME warehouses the ladder walks (`supply.site_pool_warehouses`) through
        the SAME per-location reader every other row uses, so the pool's figures here and the
        figures the proposal was computed from cannot come apart.

        Emitted only for a cell holding ONE product, for `_group_locations`' reason: a pivoted
        cell spans several, and this table has no column to say which one a row counts.
        """
        if not self._pool_warehouses:
            return []
        product_id = self._single_product(members)
        if product_id is None:
            return []
        # This line's own pool leads, off the FK rather than off a shared code prefix: a cell
        # can hold lines at several locations, so there can be more than one.
        own_pools: List[str] = []
        for row in members:
            pool_id = self._pool_of.get(row.warehouse_id) if row.warehouse_id else None
            if pool_id and pool_id in self._pool_warehouses and pool_id not in own_pools:
                own_pools.append(pool_id)
        rest = sorted(
            (pool_id for pool_id in self._pool_warehouses if pool_id not in own_pools),
            key=lambda pool_id: self._pool_warehouses[pool_id],
        )
        seen = {entry["location"] for entry in already}
        out: List[Dict[str, Any]] = []
        for pool_id in [*own_pools, *rest]:
            code = self._pool_warehouses[pool_id]
            if code in seen:
                continue
            seen.add(code)
            out.append(
                self._location(
                    code, (), product_id=product_id, warehouse_id=pool_id,
                    where=WHERE_SITE_POOL, own_demand=own_demand,
                )
            )
        return out

    @staticmethod
    def _single_product(members: Sequence[_Row]) -> Optional[str]:
        """The one product behind this cell, or None when there is more than one.

        Two products share the item code `B2155-NL-BLUE` on the live book, and a pivoted cell
        can hold several outright - a row added beyond the lines' own locations would then have
        to say WHICH product it counts, and this table has no column for that.
        """
        product_ids = {row.product_id for row in members if row.product_id}
        return next(iter(product_ids)) if len(product_ids) == 1 else None

    def _cited_locations(
        self,
        members: Sequence[_Row],
        already: Sequence[Dict[str, Any]],
        *,
        own_demand: Mapping[Tuple[str, str], Decimal],
    ) -> List[Dict[str, Any]]:
        """Every location a PROPOSAL on this cell actually names, and is not listed yet.

        The captain, on SO415472: "Use own location 71 from BRW - Pool BRW has 1716 available"
        beside a table of five -BB warehouses, all "Not stated". The pool is a warehouse in its
        own right (`warehouses.pool_warehouse_id` points at it), it is not in the agent's
        ownership group, and nothing listed it - so the one figure the decision rested on was
        the only one the reader could not check. The same is true of a cross-group donor the
        ladder proposes a Borrow from.

        Read off the components the engine already produced, never re-derived: the table then
        lists exactly what the ladder consulted, and cannot come to list something else.

        LADDER V5 (section 1e, AC-V3): a cited location in ANOTHER ownership group brings its
        whole group with it, exactly as the line's own group is listed whole. The captain's
        reason is the same one that produced ladder v4: the donor's offer is its GROUP's net
        (`donor_group_net`), so listing only the one site the ladder happened to draw from
        shows a number the subtotal cannot be checked against - `DC1-NTC 100` beside a group
        that nets 166 across five sites is a fact the reader has no way to reach. Every
        `*-<group>` sibling is listed, each with its own signed available, and the subtotal
        carries the net.
        """
        product_id = self._single_product(members)
        if product_id is None:
            return []
        seen = {entry["location"] for entry in already}
        out: List[Dict[str, Any]] = []
        donor_groups: List[str] = []
        for row in members:
            for source in row.sources or []:
                code = source.get("location")
                warehouse_id = source.get("warehouse_id") or row.warehouse_ids.get(code)
                if not code or not warehouse_id:
                    continue
                if code in seen:
                    # Already listed - as the line's OWN location, as one of the agent's
                    # group, or as a pool. Registering its group as a DONOR group here was
                    # how a cell whose order has no agent group came to list every `*-BB`
                    # warehouse under "another ownership group": the location the ladder
                    # cited was the line's own. A donor is a location this table has not
                    # already accounted for.
                    continue
                seen.add(code)
                is_pool = warehouse_id in self._pool_warehouses
                group = None if is_pool else sales_agent_service.group_of_warehouse_code(code)
                if group and group not in donor_groups and group not in self._group_warehouses:
                    donor_groups.append(group)
                out.append(
                    self._location(
                        code, (), product_id=product_id, warehouse_id=warehouse_id,
                        where=WHERE_SITE_POOL if is_pool else WHERE_OTHER_GROUP,
                        own_demand=own_demand,
                    )
                )
        # The cited donor's SIBLINGS. Resolved after the cited rows so the site the ladder
        # actually drew from keeps its place in the list, and its group fills in around it.
        for group, pairs in self._warehouses_for_groups(donor_groups).items():
            for warehouse_id, code in pairs:
                if code in seen:
                    continue
                seen.add(code)
                out.append(
                    self._location(
                        code, (), product_id=product_id, warehouse_id=warehouse_id,
                        where=WHERE_OTHER_GROUP, own_demand=own_demand,
                    )
                )
        return out

    @staticmethod
    def _own_demand(row: _Row) -> Dict[Tuple[str, str], Decimal]:
        """THE ASKING LINE's own open quantity, at its own (product, location) (R1).

        Netted back out of the SO qty its table prints, so the table answers "what is here
        for me" rather than "what is here for everybody, me included".

        ONE LINE, never the cell: the engine's offer is `max(group net + this line's open
        quantity, 0)`, so a cell holding two 24s would otherwise print an Available its own
        suggestion contradicts (10 on hand, 49 owed there: the table said 9, each line was
        offered 0).

        Keyed by (product, warehouse), never by warehouse alone: two products share the item
        code `B2155-NL-BLUE` on the live book and land in ONE cell, and their demand is not
        each other's - netting by location would take the second product's lines out of the
        first product's SO qty and print stock nobody has.
        """
        if not row.warehouse_id or not row.product_id:
            return {}
        return {(str(row.product_id), str(row.warehouse_id)): _dec(row.qty)}

    def _location(
        self,
        location: Optional[str],
        rows: Sequence[_Row],
        *,
        product_id: Optional[str] = None,
        warehouse_id: Optional[str] = None,
        where: str = WHERE_OWN,
        own_demand: Mapping[Tuple[str, str], Decimal],
    ) -> Dict[str, Any]:
        """One (product, location) line of the cell: its demand here, and what is there.

        `rows` is empty for a warehouse of the agent's ownership group that this cell's lines
        do not name: it holds no demand of this cell (so every proposed and demand figure is a
        plain 0, which is TRUE rather than absent) and its stock facts are read exactly as any
        other row's, off the same maps. `product_id` / `warehouse_id` address it, since there
        is no row to read them from.

        The strip used to read "BRW-BB 22" and the 22 was the DEMAND, which is the one reading
        nobody guessed - the captain asked "how do i see the available quantity for each stock,
        is it BRW-BB - 22?". So every number is named, and the stock facts sit beside the
        demand rather than being implied by a sentence.

        A row whose sales order states no location answers `null` for every stock fact, never
        zero: there is no location whose stock could be counted, and a zero would read as
        "that location is empty".

        `own_demand` is what THE ASKING LINE owes at its own (product, warehouse), and it
        comes back out of the SO qty (R1, 27 August 2026). The drawer used to count that
        line's own 24 as demand competing with itself and printed Available -15 beside a
        suggestion of "use own location, 9" - one set of facts under two definitions. SO qty
        is now the OTHER open lines at the location, full stop, so the table and the ladder
        answer the same question. `qty_owed_all_orders` still states the whole book's
        pressure, unchanged.
        """
        first = rows[0] if rows else None
        if first is not None:
            product_id = first.product_id
            warehouse_id = first.warehouse_id
        key = (product_id, warehouse_id)
        # What was still unclaimed when this cell was reached, off the first row that was
        # actually served: a covered row never draws the pile down, so reading it off the first
        # row full stop would answer "not stated" for a cell whose first line is decided.
        free_remaining = next(
            (row.free_before for row in rows if row.free_before is not None), None
        )
        on_hand, reserved = self._levels.get(key, (None, None))
        incoming = self._incoming.get(key, [])
        stated = location is not None and warehouse_id is not None
        # COUNTED: this warehouse was in the set the batched reads were asked about, so an
        # absent row is an answer of zero rather than the absence of a question (AC-B2) - the
        # last upload counted none there, which is a fact. A location that was NOT asked about
        # keeps its nulls: `_cited_locations` discovers a cross-group Borrow donor off the
        # engine's own components, after the read set was fixed, and zeroing it would print
        # "SO qty 0, Available = on hand" for a warehouse the whole book owes against.
        counted = stated and warehouse_id in self._counted_warehouses
        # `_levels` / `_free` / `_held` are read by PRODUCT across every warehouse, so an absent
        # key there is a real zero for any stated location, cited ones included. The three reads
        # below are warehouse-filtered, and that is the whole distinction `counted` draws.
        if stated and on_hand is None:
            on_hand, reserved = _ZERO, _ZERO
        # Same rule, and the same default `qty_owed_all_orders` below has always used: a
        # location nothing is owed at owes 0. The two said different things about one number.
        pressure, _covered = self._pressure.get(
            key, (_ZERO, _ZERO) if counted else (None, None)
        )
        # THE OTHER lines' demand here: the book's pressure less this cell's own share of it
        # (R1). Floored at zero rather than allowed negative - the cell can owe more here than
        # the pressure read counted (a line whose order is not `open` is in the drawer and not
        # in the pressure), and a negative SO qty would print as stock nobody has.
        mine = (
            _dec(own_demand.get((str(product_id), str(warehouse_id)), _ZERO))
            if stated
            else _ZERO
        )
        owed = None if pressure is None else max(pressure - mine, _ZERO)
        incoming_qty = sum((ref.qty for ref in incoming), _ZERO) if counted else None
        # AutoCount's own arithmetic: on hand, less what the book has sold, plus what is on the
        # water. It may be NEGATIVE and is never clamped - "oversold here by 632" is the signal
        # a planner needs, and a floor of zero would report it as "nothing left", which is a
        # different and less useful fact.
        available = (
            (on_hand or _ZERO) - (owed or _ZERO) + (incoming_qty or _ZERO)
            if counted and on_hand is not None
            else None
        )
        return {
            "location": location,
            #: WHERE this location stands relative to the cell: the lines' own, the agent's
            #: ownership group, a site pool the ladder drew from, or a location outside the
            #: group a Borrow was proposed from. Without it the table is a flat list in which
            #: the pool holding 1716 looks exactly like a group warehouse holding nothing.
            "where": where,
            #: Addressing only: the stock drill-down is opened by id, never by resolving a
            #: warehouse code or an item code back into one.
            "product_id": product_id if stated else None,
            "warehouse_id": warehouse_id if stated else None,
            #: The demand, kept under its old name because the frontend's source strip reads
            #: it. `qty_demand` is the same number said unambiguously.
            "qty": qty_text(sum((row.qty for row in rows), _ZERO)),
            "qty_demand": qty_text(sum((row.qty for row in rows), _ZERO)),
            "qty_on_hand": qty_text(on_hand) if stated and on_hand is not None else None,
            "qty_reserved": qty_text(reserved) if stated and reserved is not None else None,
            #: What this engine may actually use: on hand, less reserved, less what confirmed
            #: decisions already hold. The figure the proposal was computed from.
            "qty_free": (
                qty_text(self._free.get(key, _ZERO)) if stated else None
            ),
            #: Of that, what was still unclaimed when THIS cell's lines were served. Earlier
            #: dates draw first, so a December cell can face a smaller pile than the product's
            #: free figure suggests.
            "qty_free_remaining": (
                qty_text(free_remaining) if stated and free_remaining is not None
                else None
            ),
            #: What confirmed decisions are holding here. On hand, less reserved, less this, IS
            #: `qty_free`, so the arithmetic on screen closes rather than looking like a
            #: rounding error.
            "qty_held_by_decisions": (
                qty_text(self._held.get(key, _ZERO)) if stated else None
            ),
            #: What the WHOLE BOOK still owes at this location - every open line of every open
            #: sales order, not merely the ones on this board. The number that stops "478 free"
            #: being read as "478 available to me" when 47,009 is owed.
            "qty_owed_all_orders": (
                qty_text(self._pressure.get(key, (_ZERO, _ZERO))[0]) if counted else None
            ),
            #: Of that, the part already covered by a confirmed decision, so committed pressure
            #: can be told from uncommitted pressure.
            "qty_owed_confirmed": (
                qty_text(self._pressure.get(key, (_ZERO, _ZERO))[1]) if counted else None
            ),
            #: Still to arrive at this location: allocated on a supply PO, not yet received.
            "qty_incoming": qty_text(incoming_qty) if counted else None,
            # ---- AutoCount's Stock Status vocabulary, which is what the planner reads ----
            #: "SO Qty": what the OTHER open lines still owe here, under the word the captain
            #: uses. `qty_owed_all_orders` above is the whole book including this cell's own
            #: lines; this one nets them out, because a line does not compete with itself for
            #: stock (R1). No tooltip and no second field: SO qty is plainly the other lines
            #: (R15).
            "so_qty": qty_text(owed) if stated and owed is not None else None,
            #: "PO Qty" in AutoCount is the supplier order; in Sorento that is the SPO.
            "spo_qty": qty_text(incoming_qty) if counted else None,
            #: "Available Qty": on hand - SO + SPO. Signed.
            "available_qty": qty_text(available) if available is not None else None,
            #: Open PURCHASE-order balance here, less what an order-inquiry row already
            #: claims. Information only, and deliberately NOT in `available_qty`: a purchase
            #: order reaches a project line through a link, never by sitting at the location
            #: (PLAN section I).
            "po_open_qty": (
                qty_text(self._po_open.get(key, _ZERO)) if counted else None
            ),
            #: LADDER V4 (section 1d): what the SET this row belongs to nets between all of
            #: its locations, which is what the engine drew on. Stated per row so the table's
            #: subtotal can print the net rather than a sum of the rows it happens to show -
            #: the net is over the whole group, silent members included.
            **self._net_fields(
                product_id, warehouse_id, where, stated, own_demand=own_demand
            ),
            #: LADDER V8 (R-K): what a SITE POOL row may give a project line once the dealers'
            #: share is kept back - the SAME `available_for_project` the walk's step 0 asked it
            #: for, so the lightbox and the engine can never disagree about the number. `0`
            #: rather than blank on an addressable pool row ("the pool can spare you nothing"
            #: is an answer); absent everywhere else, because outside a pool there is no share
            #: to keep back and a zero would read as a pool with nothing in it.
            **self._project_share_fields(product_id, warehouse_id, where, stated, available),
            "incoming": [
                {
                    "spo_number": ref.spo_number,
                    "arrival_date": ref.arrival_date,
                    "qty": qty_text(ref.qty),
                }
                for ref in sorted(
                    incoming, key=lambda ref: (ref.arrival_date is None, ref.arrival_date)
                )
            ] if counted else [],
            "qty_proposed_reserve": self._proposed_text(rows, RESERVE),
            "qty_proposed_incoming": self._proposed_text(rows, TIMELY_SPO),
            "qty_proposed_buy": self._proposed_text(rows, BUY),
        }

    def _net_fields(
        self,
        product_id: Optional[str],
        warehouse_id: Optional[str],
        where: str,
        stated: bool,
        *,
        own_demand: Mapping[Tuple[str, str], Decimal],
    ) -> Dict[str, Optional[str]]:
        """`net` / `net_of` for one row of the cell table (ladder v4, section 1d).

        The set a row belongs to, and never the row itself: an `own` or `group` row is
        netted with its whole ownership group (so both carry the same figure, which is what
        lets the Group subtotal print it once), a `site_pool` row with all five pools, and
        an `other_group` row with the donor group it sits in. A row with no location, or one
        outside any set, states nothing rather than a zero - "no set" and "a set that nets
        zero" are different answers.

        Read through the supply service's own `netting()`, which is the SAME reader the
        ladder drew on, so the subtotal a planner checks and the number the engine obeyed
        cannot come apart.

        THE ASKING LINE's own quantity comes back out of the set, exactly as it does per
        row (R1): the ladder's offer is `max(group net + that line's quantity, 0)`, so the
        subtotal the table prints IS that offer - unclamped, because "-2" is the fact that
        explains why nothing was offered and "0" is not. It stays a figure over the WHOLE
        set, silent members included, which is why it is read from `netting()` rather than
        summed off the rows the table happens to list.
        """
        if not stated or not product_id or not warehouse_id:
            return {"net": None, "net_of": None, "net_raw": None}
        netting = self.supply.netting()
        if where == WHERE_SITE_POOL or netting.is_pool(warehouse_id):
            position = netting.pools_net(product_id)
            return {
                "net": qty_text(
                    position.net + self._mine_in(position, own_demand, product_id)
                ),
                "net_of": POOLS_SET,
                # N1 (fix round 5): the figure `_is_pool_share_split` and `stock-detail`
                # actually bound a pool-share composition by - never the displayed net
                # above, which has this line's own demand added back in.
                "net_raw": qty_text(position.net),
            }
        group = netting.group_of(warehouse_id)
        if not group:
            return {"net": None, "net_of": None, "net_raw": None}
        position = netting.group_net(product_id, group)
        return {
            "net": qty_text(
                position.net + self._mine_in(position, own_demand, product_id)
            ),
            "net_of": group,
            "net_raw": qty_text(position.net),
        }

    def _pool_share_pct(self) -> int:
        """How much of a site pool is kept back for dealers, for the CLIENT's own subtotal.

        The documented default when the policy row states none (review round 1, S7) - `or 0`
        read a missing figure as "keep nothing back", which is the one value that makes the
        client print a share twice the size of the one the walk obeyed. The engine defaults
        the same way (`front_planning_engine.DEFAULT_POOL_SHARE_PCT`), off the same
        `priority.FULFILMENT_SETTINGS_DEFAULTS`.
        """
        stated = self.supply.fulfilment_settings().get("pool_share_pct")
        if stated is None:
            return int(priority.FULFILMENT_SETTINGS_DEFAULTS["pool_share_pct"])
        return int(stated)

    def _project_share_fields(
        self,
        product_id: Optional[str],
        warehouse_id: Optional[str],
        where: str,
        stated: bool,
        available: Optional[Decimal],
    ) -> Dict[str, Optional[str]]:
        """`available_for_project` for one row of the cell table (LADDER V8, R-K).

        A SITE POOL row only. `min(floor(available x (100 - pool_share_pct) / 100),
        max(five-pool net, 0))` - the engine's own `available_for_project`, called rather than
        restated, because the whole point of R-K is that the planner reads the number the walk
        obeyed. The net is the row's own (`_net_fields` prints the same figure), so the two
        columns of one row are read off one position.

        Absent on every other row: `own`, `group` and `other_group` keep no dealer share, and
        a `0` there would read as a location that has nothing rather than as a rule that does
        not apply.
        """
        if not stated or not product_id or not warehouse_id or available is None:
            return {}
        netting = self.supply.netting()
        if not (where == WHERE_SITE_POOL or netting.is_pool(warehouse_id)):
            return {}
        return {
            "available_for_project": qty_text(
                available_for_project(
                    available,
                    netting.pools_net(product_id).net,
                    self.supply.fulfilment_settings().get("pool_share_pct"),
                )
            )
        }

    @staticmethod
    def _mine_in(
        position: Any,
        own_demand: Mapping[Tuple[str, str], Decimal],
        product_id: str,
    ) -> Decimal:
        """What the asking line owes at the locations of one netted set.

        Of THIS product: a set is netted per product, and a sibling product behind the same
        item code owes against its own pile, not against this one.
        """
        return sum(
            (
                _dec(own_demand.get((str(product_id), str(entry.warehouse_id)), _ZERO))
                for entry in position.by_location
            ),
            _ZERO,
        )

    @staticmethod
    def _proposed_text(rows: Sequence[_Row], kind: str) -> str:
        return qty_text(sum((row.proposed.get(kind, _ZERO) for row in rows), _ZERO))

    def _attach_drafts(self, rows: Sequence[_Row]) -> None:
        """Stamp what has been SAVED on each line back onto it (S4, R-F, AC-4.2).

        One query for the whole board, read by sales order and matched by the CORE LINE
        (C2, code review round 4). NONE of the contribution key's own parts is durable
        enough to match on: `line_no` is POSITIONAL whenever the order's lines are not all
        mirrored (`_line_numbers`), so a re-upload that moves an earlier line's required
        date renumbered the rest and their drafts stopped attaching, and `bucket_key` moves
        with the board's GRANULARITY (`bucket_key_for`), so matching on it would hide every
        saved line the moment the planner switched from week to day. `row.line_id` is the
        one identity all three of the board, the mirror and the confirmation agree on.

        `stale` is computed here rather than stored, against the LINE's own current facts
        (S1, code review round 3, captain ruling) - `row.qty` and `row.required_date`, the
        same figures the contribution itself states - never against the proposal: the
        proposal depends on which orders share this board, its granularity and its window,
        so comparing it flipped `stale` falsely the moment a planner opened a different view
        of the exact same line.
        """
        saved = project_line_draft_service.drafts_for_orders(
            self.db, {row.sales_order_id for row in rows}
        )
        if not saved:
            return
        for row in rows:
            if not row.line_id:
                continue
            entry = saved.get(str(row.line_id))
            if entry is None:
                continue
            row.draft = {
                "decision": entry["decision"],
                "saved_by": entry["saved_by"],
                "saved_at": entry["saved_at"],
                "stale": project_line_draft_service.is_stale(
                    entry["line_snapshot"], row.qty, row.required_date
                ),
                # D12 (#573): what the caller saved the draft AGAINST, echoed back opaque.
                # Never read by `is_stale` above - see `SOSupplyDecisionDraft.proposed`.
                "proposed": entry.get("proposed"),
            }

    def _contribution(self, row: _Row) -> Dict[str, Any]:
        return {
            #: THIS LINE's own location table (R1/B1): the same rows the cell's drawer shows,
            #: netted of this line's own quantity and no other's, so the "N available" beside
            #: a Reserve input is the figure the ladder offered this line. Empty for a line
            #: whose bucket is outside the day window - no cell was built for it, and a table
            #: computed off a pile nobody drew from would be inventing an answer.
            "locations": self._locations_by_row.get(row.key, []),
            "key": row.key,
            "sales_order_id": row.sales_order_id,
            #: The CORE sales-order line, which is what the pile queue is addressed by
            #: (`GET .../fulfilment-planning/queue?line_id=`). Addressing only, never rendered.
            #: Distinct from `project_line_id`, which is the MIRROR and is null until somebody
            #: adopts the order - a line with no mirror still stands in a queue.
            "line_id": row.line_id,
            #: The product, for the same drill-down and by the same rule: two products on the
            #: live book share the item code `B2155-NL-BLUE`, so a pile looked up by code would
            #: be the wrong pile. Addressing only, never rendered.
            "product_id": row.product_id,
            #: The MIRROR line id, which is what `confirm` names a line by
            #: (`lines[].project_line_id`). Null until somebody adopts this sales order, and
            #: null is the honest answer: there is no planning record to confirm against.
            "project_line_id": row.project_line_id,
            "so_number": row.so_number,
            "customer_name": row.customer_name,
            #: Addressing only, and the key a pivot BY CUSTOMER groups on: two different
            #: customers can carry the same name, and grouping by the label would merge them.
            "customer_id": row.customer_id,
            #: Who sold it (`sales_orders.sales_agent_id` -> `sales_agents`). The code is what
            #: the column shows; the label names who the code belongs to, in the code's title.
            "agent_code": row.agent_code,
            "agent_label": row.agent_label,
            "project_label": row.project_label,
            #: The key a pivot BY PROJECT groups on (see `_project_key`).
            "project_key": row.project_key,
            "line_no": row.line_no,
            #: #1362 item 5: the AutoCount sales-order line number (`sales_order_lines
            #: .line_no`), `None` when AutoCount gave the line none. What the Line column,
            #: the drawer title and every confirm message print; `line_no` is addressing.
            "so_line_no": row.so_line_no,
            "item_code": row.item_code,
            #: The quantity this board PLANS for, kept under its old name because the
            #: frontend reads it. Since the 14 September 2026 ruling that is
            #: `coalesce(qty_required, qty_ordered)` - a delivered unit nobody sourced is a
            #: unit to put back - so it is no longer an alias of `qty_outstanding` below.
            "qty": qty_text(row.qty),
            #: What the customer ordered on this line, what has gone out, and what is still
            #: owed the CUSTOMER. Three separate facts, and the third is NOT what the board
            #: plans against any more: printing the plan quantity under the name
            #: "outstanding" would make the screen state a delivery that never happened.
            "qty_ordered": qty_text(row.qty_ordered or _ZERO),
            "qty_delivered": qty_text(row.qty_delivered or _ZERO),
            "qty_outstanding": qty_text(row.qty_outstanding or _ZERO),
            #: The PLANNING UNIT this line was composed in (ladder v6): its own order's
            #: lines for the same item, location and delivery date, planned as one quantity.
            #: The line's own quantity and `1` when it was planned alone, which is most
            #: lines - and every covered or unplannable one, which was not planned here.
            "unit_qty": qty_text(row.qty if row.unit_qty is None else row.unit_qty),
            "unit_line_count": row.unit_line_count,
            #: What the engine proposes to meet it with. The three add up to the plan
            #: quantity (`qty`), which is the balance invariant the per-order sheet also keeps.
            "qty_proposed_reserve": qty_text(row.proposed.get(RESERVE, _ZERO)),
            "qty_proposed_incoming": qty_text(row.proposed.get(TIMELY_SPO, _ZERO)),
            "qty_proposed_buy": qty_text(row.proposed.get(BUY, _ZERO)),
            #: What could be borrowed instead of bought, and from where. Never PROPOSED - a
            #: Borrow needs a donor and a reason from a person (AC-B09) - but never hidden
            #: either, or a Buy reads as "this stock exists nowhere".
            #: The arithmetic behind the Reserve, in the strip's own vocabulary, so the planner
            #: can check it against the drill-down: "1015 on hand, 1015 owed to 12 lines ranked
            #: ahead, 0 left for this line, so it is bought".
            #: Null on a covered line, which is not in the queue these count (see `covered`).
            "so_qty_ahead": (
                None if row.so_qty_ahead is None else qty_text(row.so_qty_ahead)
            ),
            "lines_ahead": row.lines_ahead,
            "available_to_this_line": (
                None
                if row.available_to_this_line is None
                else qty_text(row.available_to_this_line)
            ),
            "qty_borrow_available": qty_text(
                sum(
                    (_dec(candidate["free_qty"]) for candidate in row.borrow_candidates),
                    _ZERO,
                )
            ),
            # The sheet's own candidate, passed through whole. It was being narrowed to the
            # four fields a READER needs, and the board now composes a Borrow: the confirm
            # body names the donor by `warehouse_id` and `donor_project_id`, neither of which
            # a warehouse code can be resolved into on the client without guessing at an id.
            "borrow_candidates": [
                {
                    "source": candidate["source"],
                    "warehouse_code": candidate["warehouse_code"],
                    "warehouse_id": candidate.get("warehouse_id"),
                    "donor_project_ref": candidate.get("donor_project_ref"),
                    "donor_project_id": candidate.get("donor_project_id"),
                    "free_qty": candidate["free_qty"],
                    # AutoCount's triple for the DONOR's pile, and the ranking that put this
                    # row where it is (PLAN 13.11). "Before I decide to borrow, I need to
                    # know I am not hurting them" is not answerable from a free figure.
                    "qty_on_hand": candidate.get("qty_on_hand"),
                    "so_qty": candidate.get("so_qty"),
                    "spo_qty": candidate.get("spo_qty"),
                    "available_qty": candidate.get("available_qty"),
                    "qty_free": candidate.get("qty_free"),
                    "qty_committed": candidate.get("qty_committed"),
                    "need_qty": candidate.get("need_qty"),
                    "available_after_need": candidate.get("available_after_need"),
                    "recommended": bool(candidate.get("recommended")),
                    "donor_impact": candidate.get("donor_impact"),
                    # S4 (`PLAN-local-supplier-oi-routing.md`): the Grid Location table's own
                    # facts for this donor, so the manual Borrow modal renders `CellStockTable`
                    # instead of a second, narrower table of its own.
                    "location": candidate.get("location"),
                    # The rung and the donor line (BORROW-LOCATION-ONLY defect A): a Decide or
                    # Amend borrow posts `donor_core_line_id` back, which is what keeps the
                    # re-borrow guard (K6) and gives each line at one bin its own running
                    # claim. The donor's name fields are whatever the service left on them -
                    # blank with "Borrow from another order" off.
                    "rung": candidate.get("rung"),
                    "donor_core_line_id": candidate.get("donor_core_line_id"),
                    "donor_so_number": candidate.get("donor_so_number"),
                    "donor_line_no": candidate.get("donor_line_no"),
                    "donor_agent_code": candidate.get("donor_agent_code"),
                    "donor_required_date": candidate.get("donor_required_date"),
                    "same_agent": bool(candidate.get("same_agent")),
                    "lower_ranked": bool(candidate.get("lower_ranked")),
                }
                for candidate in row.borrow_candidates
            ],
            "required_date": row.required_date,
            #: This line's own date is behind the as-of date. Per line, not per column.
            "is_past": row.is_past,
            "fulfilment_location": row.location,
            #: The same warehouse by id, addressing only. An amendment on a line the engine
            #: proposed nothing for has no Reserve source to read one off, and inventing one
            #: from the location code would be guessing at an id.
            "fulfilment_warehouse_id": row.warehouse_id,
            "unplannable": row.unplannable,
            "priority": row.priority,
            "rank_score": round(float(row.rank_score), 6),
            # The normalised score with the ABSOLUTE fact beside it. On its own a 0.00 next to
            # a 1.00 explains nothing, and under a policy that weights nothing a demand row
            # carries every score is 0.00 - so the screen shows the fact (this line's required
            # date, this order's document date, this customer's terms) and keeps the normalised
            # value secondary.
            "rank_factors": [
                {**factor.as_dict(), "raw": row.raw_facts.get(factor.key)}
                for factor in row.rank_factors
            ],
            #: An active decision already covers this line, and what it froze. A covered line
            #: is not proposed for again: everything above states what was DECIDED.
            "covered": row.covered,
            "decision": row.decision,
            #: AC-DT-2 (`PLAN-oi-decision-trail-ui.md`): the same three facts flattened
            #: onto the line itself - the Confirmed chip's tooltip reads these rather
            #: than reaching into `decision`, which is shaped for re-posting an
            #: amendment and carries no confirmer at all. `None` on an uncovered line.
            "decided_by_name": row.decided_by_name,
            "decided_at": (row.decision or {}).get("confirmed_at"),
            "decision_revision": (row.decision or {}).get("revision_no"),
            #: A decision SAVED on this line and not yet confirmed (S4, R-F). Beside
            #: `decision` rather than instead of it: that is what an ACTIVE revision froze,
            #: this is what somebody has settled but not committed, and a line can carry
            #: either, both or neither. Null when nobody has saved one.
            "draft": row.draft,
            #: The same saver facts flattened, for the tooltip (AC-DT-2) - `row.draft`
            #: already carries `saved_by`/`saved_at`, in the shape the draft PUT/GET
            #: round-trips; these are the same values under the trail's own names.
            "draft_saved_by_name": (row.draft or {}).get("saved_by") or None,
            "draft_saved_at": (row.draft or {}).get("saved_at"),
            #: What ANOTHER sales order borrowed off this line (AC-L6): "71 lent to
            #: SO415472". Empty when nothing was lent, never absent.
            "lent_to": row.lent_to or [],
            #: What purchasing was already TOLD about this line, and how far they got with
            #: it. The other half of the decision beside it: the decision is the promise,
            #: this is the instruction it produced. Null when nobody has raised one - never
            #: an empty object, because that would claim an instruction saying nothing.
            "order_inquiry": row.order_inquiry,
            "sources": row.sources,
            #: What the ENGINE suggested, beside what was decided (AC-D2). The live ladder
            #: on an undecided line - the same list as `sources` there - and the composition
            #: frozen at confirm on a covered one, where `sources` is the DECISION and would
            #: otherwise be the only thing on screen. Null on a revision written before the
            #: proposal was frozen: "not recorded" is not "suggested nothing".
            "proposed": (
                None
                if row.proposed_components is None
                else {"components": row.proposed_components}
            ),
            # The ladder, rung by rung, in the order it was walked - including the rungs that
            # gave nothing, because "the pool was checked and had none" is the answer to "how
            # did you arrive at the Buy" and an omitted step reads as a step never taken.
            "trail": row.trail,
            # The five steps of ladder v7.1, always all five and always in step order
            # (R36, AC-S3-14). The client renders them as given and never re-sorts.
            "options": row.options,
            # The item facts the ladder judged this line on. Null, never `false`, on a line
            # it did not walk: an unplannable or covered line was judged against nothing.
            "item_flags": row.item_flags,
            "contested": row.contested,
            # S3 (`PLAN-local-supplier-oi-routing.md`): whether this line's product is
            # bought locally, computed once for the whole board (never per line).
            "buy_origin": self._buy_origin.get(str(row.product_id)) if row.product_id else None,
            # R3 (13 Sep browser walk): the book CANCELLED this line and a change row for
            # it is still PENDING - `false`/`null` for every ordinary row, which is every
            # row `_demand_rows` itself ever builds (`_cancelled_pending_change_rows` is
            # the only writer of a truthy pair, and it never runs the ladder for one).
            "cancelled": bool(row.cancelled),
            "pending_change_batch_id": row.pending_change_batch_id,
            "cancelled_at": row.cancelled_at,
            "cancelled_by": row.cancelled_by_name,
            "cancel_reason": row.cancel_reason,
        }

    def _buckets(
        self,
        keys: Iterable[str],
        granularity: str,
        day_window_start: Optional[date],
        as_of: date,
    ) -> List[Dict[str, Any]]:
        """Chronological, earliest first, with No date pinned last (13.3).

        No aggregate column: a past period is a column like any other, carrying `is_past` so
        the screen can tint it and count what is late. A bucket is past when its WHOLE period
        ended before the as-of date - the period we are inside is not past, because some of its
        dates are still to come and tinting it would tell the planner this week is already
        lost.

        Only DATED buckets somebody owes are emitted, so a selection with a three-year gap in
        it produces no columns for those years. That keeps the axis proportional to the work
        rather than to the calendar; the day granularity's 30-column window is the one place a
        run of empty columns IS rendered, because there a gap between two days is the
        information and a calendar that hides its empty days is not a calendar.

        Demand outside the day window is not lost from the plan - it is reached by moving the
        window - but it is not SHOWN, and so is not served from the pile either. A day view can
        therefore offer stock the week view has already promised to a line outside the window,
        which is why the window is a display control rather than a planning horizon.
        """
        present = set(keys)
        dated = sorted(k for k in present if k != NO_DATE_BUCKET)
        if granularity == "day":
            start = day_window_start or self._default_day_window(dated, as_of)
            dated = (
                [(start + timedelta(days=offset)).isoformat()
                 for offset in range(DAY_WINDOW_COLUMNS)]
                if start
                else []
            )
        buckets: List[Dict[str, Any]] = []
        for key in dated:
            end = bucket_end(key, granularity)
            buckets.append(
                {
                    "key": key,
                    "kind": "dated",
                    "label": _bucket_label(key, granularity),
                    "start": date.fromisoformat(key),
                    "is_past": end is not None and end < as_of,
                }
            )
        if NO_DATE_BUCKET in present:
            buckets.append(
                {
                    "key": NO_DATE_BUCKET,
                    "kind": "no_date",
                    "label": "No date",
                    "start": None,
                    # An absent date has not passed. It is simply absent, which is a different
                    # thing and must not be tinted as lateness.
                    "is_past": False,
                }
            )
        return buckets

    @staticmethod
    def _serve_order(keys: Iterable[str]) -> List[str]:
        """Every bucket the selection has, in the order the piles are served.

        Chronological, with the dateless column last: a line nobody has dated cannot claim
        stock ahead of one with a date on it. Deliberately independent of `_buckets`, which
        answers a different question - what is on SCREEN - and at day granularity answers it
        with a 30-column window. Allocating over the window instead would leave every line
        outside it with no proposal at all and would make the board's contest counts describe
        the columns rather than the selection.
        """
        present = set(keys)
        ordered = sorted(key for key in present if key != NO_DATE_BUCKET)
        if NO_DATE_BUCKET in present:
            ordered.append(NO_DATE_BUCKET)
        return ordered

    @staticmethod
    def _default_day_window(dated: Sequence[str], as_of: date) -> Optional[date]:
        """Where the 30-day window opens when the planner has not moved it.

        On the earliest day still to come, and only on the earliest day owed at all when
        everything is already past. Without the second half a selection of old orders would
        open on an empty month; without the first, one whose oldest line is from 2024 would
        open the calendar three years ago - which is what happened the moment the aggregate
        past column was removed and "the earliest dated bucket" stopped meaning "the earliest
        future one".
        """
        if not dated:
            return None
        future = [key for key in dated if date.fromisoformat(key) >= as_of]
        return date.fromisoformat(future[0] if future else dated[0])

    def _standings(
        self,
        rows: Sequence[_Row],
        adopted_by_so: Optional[Dict[str, str]] = None,
        pending_by_so: Optional[Dict[str, str]] = None,
        undo_by_so: Optional[Dict[str, Optional[Dict[str, Any]]]] = None,
        decision_headers_by_so: Optional[Dict[str, Optional[Dict[str, Any]]]] = None,
    ) -> List[Dict[str, Any]]:
        """Per order: how much of it is on this board, and how much of it can never be decided.

        `decided_count` is 0 from the server, always: the verdicts live in the board's client
        draft (13.4), and the screen recomputes this from the draft it holds. It is carried
        so the shape is the one the frontend already reads, not because the server knows.

        `adopted_by_so` / `pending_by_so` are `_order_plan_status`'s own read, taken by the
        caller BEFORE this runs (query-count guard: this used to run two of its own queries
        here, and a bare board build's own line count must not swing on how many orders it
        names). `None` reads as "nobody has adopted the sales order at all" and "no batch
        is pending" respectively, never merely because no single line carried a mirror
        (attempt 6, 13 Sep 2026: `project_sales_order_id` used to come from `setdefault`'s
        FIRST `_Row`, whose own addressing - `_mirror_addressing`, per LINE, or
        `_cancelled_pending_change_rows` - could easily carry none at all).
        """
        adopted_by_so = adopted_by_so or {}
        pending_by_so = pending_by_so or {}
        undo_by_so = undo_by_so or {}
        decision_headers_by_so = decision_headers_by_so or {}
        by_order: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            standing = by_order.setdefault(
                row.sales_order_id,
                {
                    "sales_order_id": row.sales_order_id,
                    #: The planning record this order's confirmation posts to
                    #: (`POST /sales-orders/{pso_id}/confirm`). Set below, off the ORDER's
                    #: own adoption record - NULL only when nobody has adopted the sales
                    #: order at all, never merely because no single line carried a mirror.
                    "project_sales_order_id": adopted_by_so.get(row.sales_order_id),
                    "so_number": row.so_number,
                    "customer_name": row.customer_name,
                    "line_count": 0,
                    "decided_count": 0,
                    "unplannable_count": 0,
                    "pending_change_batch_id": pending_by_so.get(row.sales_order_id),
                    #: S1 (#978): whether the order's newest confirm can be undone from
                    #: the gear, and why not when it cannot. `board_undo_map`'s own read.
                    "undo": undo_by_so.get(row.sales_order_id),
                    #: AC-DT-1 (`PLAN-oi-decision-trail-ui.md`): the ACTIVE decision's
                    #: own header facts - revision, confirmer, when, how many lines -
                    #: for the board panel's "Revision N, confirmed by <name>" line.
                    #: Null when the order has no active decision. `_decision_headers`'s
                    #: own read.
                    "decision": decision_headers_by_so.get(row.sales_order_id),
                },
            )
            standing["line_count"] += 1
            if row.unplannable:
                standing["unplannable_count"] += 1
        return sorted(by_order.values(), key=lambda s: s["so_number"] or "")

    def _decision_headers(
        self, adopted_by_so: Dict[str, str]
    ) -> Dict[str, Optional[Dict[str, Any]]]:
        """AC-DT-1 (`PLAN-oi-decision-trail-ui.md`): the ACTIVE decision's own header
        facts per selected order - revision, confirmer, when, how many lines - so the
        board panel can print "Revision 1, confirmed by Nurain, 25 Sep 2026 09:20, 11
        lines" beside "N to confirm - N rejected", or "No decision yet".

        Read straight off `so_supply_decisions`, never through `board_undo_map`: that
        read is gated to a JOURNALLED revision (or, admin-only, a reconstructable one)
        for the undo gear specifically, and a header built off it would read "No
        decision yet" on most confirmed orders - a pre-lane revision, or one minted
        outside the board's own confirm routes, still gets a header here.

        One query for the whole board's selection, keyed the same way `adopted_by_so`
        is - by the CORE `sales_orders.id`. `None` for an order with no active decision.
        """
        out: Dict[str, Optional[Dict[str, Any]]] = {so_id: None for so_id in adopted_by_so}
        pso_ids = {pso_id for pso_id in adopted_by_so.values() if pso_id}
        if not pso_ids:
            return out
        decisions = (
            self.db.query(SOSupplyDecision)
            .filter(
                SOSupplyDecision.project_sales_order_id.in_(list(pso_ids)),
                SOSupplyDecision.state == DECISION_ACTIVE,
            )
            .all()
        )
        if not decisions:
            return out
        from app.services.project_service import resolve_user_names

        names = resolve_user_names(
            self.db, {d.confirmed_by for d in decisions if d.confirmed_by}
        )
        by_pso = {str(d.project_sales_order_id): d for d in decisions}
        for so_id, pso_id in adopted_by_so.items():
            decision = by_pso.get(pso_id) if pso_id else None
            if decision is None:
                continue
            out[so_id] = {
                "revision_no": decision.revision_no,
                "confirmed_by_name": (
                    names.get(decision.confirmed_by) if decision.confirmed_by else None
                ),
                "confirmed_at": decision.confirmed_at,
                "line_count": len(decision.line_snapshots or []),
            }
        return out

    def _order_plan_status(
        self, so_numbers: Sequence[str]
    ) -> Tuple[Dict[str, str], Dict[str, str], Set[str]]:
        """Per selected order (by core `sales_orders.id`): its adoption record, its newest
        PENDING planning-change batch, and the CORE LINES those pending rows name - ONE
        query for all three (query-count guard,
        `test_a_board_of_76_lines_does_not_scale_its_query_count_with_the_line_count`),
        where `_standings` used to run two, and `_cancelled_pending_change_rows` a third
        of its own regardless of whether anything was ever pending.

        Both OUTER joins, from `ProjectSalesOrder` (the adoption record): an order with NO
        pending-change row at all still needs to answer `project_sales_order_id`, and one
        with rows but none of them PENDING still needs to answer `None` for the batch, not
        be excluded from the read entirely. Absent from `adopted_by_so` means nobody has
        adopted that order; absent from `pending_by_so` means nothing is pending for it -
        neither is ever a `None` value, so a caller uses `.get(so_id)`.

        The third value is the PER LINE half of the same read, and it costs nothing extra:
        one more selected column on a query that already joins the pending rows. It is what
        re-opens a line the migrated book had decided (see `_demand_rows`).
        """
        if not so_numbers:
            return {}, {}, set()
        from app.models.planning_change import PlanningChangeBatch, PlanningChangeRow
        from app.services.planning_change_service import PLANNING_CHANGE_STATE_PENDING

        rows = (
            self.db.query(
                SalesOrder.id, ProjectSalesOrder.id,
                PlanningChangeBatch.id, PlanningChangeBatch.created_at,
                PlanningChangeRow.core_line_id,
            )
            .join(ProjectSalesOrder, ProjectSalesOrder.so_id == SalesOrder.id)
            .outerjoin(
                PlanningChangeRow,
                PlanningChangeRow.project_sales_order_id == ProjectSalesOrder.id,
            )
            .outerjoin(
                PlanningChangeBatch,
                (PlanningChangeBatch.id == PlanningChangeRow.batch_id)
                & (PlanningChangeBatch.applied_at.is_(None))
                & (PlanningChangeRow.applied_state == PLANNING_CHANGE_STATE_PENDING),
            )
            .filter(SalesOrder.so_number.in_(list(so_numbers)))
            # The NEWEST pending batch wins, same tie-break as `pending_batch_id_by_
            # sales_order`: rows arrive newest-batch-first, so the first one seen per
            # order in the loop below is kept and a later, older one never overwrites it.
            .order_by(PlanningChangeBatch.created_at.desc(), PlanningChangeBatch.id.desc())
            .all()
        )
        adopted_by_so: Dict[str, str] = {}
        pending_by_so: Dict[str, str] = {}
        pending_core_lines: Set[str] = set()
        for so_id, pso_id, batch_id, _created_at, core_line_id in rows:
            adopted_by_so[str(so_id)] = str(pso_id)
            if not batch_id:
                continue
            pending_by_so.setdefault(str(so_id), str(batch_id))
            # THE BATCH THAT WON, not every pending batch the order has. `pending_by_so` is
            # the newest one and the Changed badge opens the board on it, so a line whose
            # only pending row belongs to an OLDER batch is not a line this board is about.
            if core_line_id and pending_by_so[str(so_id)] == str(batch_id):
                pending_core_lines.add(str(core_line_id))
        return adopted_by_so, pending_by_so, pending_core_lines
