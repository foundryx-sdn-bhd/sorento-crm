"""Who gets what, and what is left over: one product's supply assigned to its demand.

PURE maths, no I/O, golden-tested in isolation - the same discipline `coverage_timeline`
keeps. Months are a DISPLAY grouping of dated arithmetic (ADR-0011): no quantity is ever
netted inside a bucket, so supply arriving on the 25th cannot cover demand due on the 3rd.

**A month states its own month, and nothing else** (R37, 30 Aug 2026). What is debted in
August stays in August. The balance of month M is the supply DATED IN M that is still free
when the whole walk is over, minus, for each line due in M, what that line was short AT ITS
OWN DATE. A running balance was carried before, and it read as a fresh debt every month
after: `1/2" ULTRA CIRCULAR` printed -4 in August and -4 again in every later column, while
the drill for those columns was empty, because the cell was an accumulation and the drill
was a month. A line that later supply clears (`late`) still books its shortfall in its own
month - it went without on its own date, and that is the fact a planner acts on - and the
supply that cleared it is no longer free, so it is never counted a second time.

**The question.** "Is this product short, when, and whose order is the one that goes
without?" The balance alone answers the first two; the assignment answers the third, and
that is the one a planner acts on. The Stock Debt view (S2) prints both; the ladder (S3/S4)
reads the same answer so the board and the view can never disagree about what is free.

**The walk** (`PLAN-scm-borrow-ladder-v7-stock-debt.md` 3.1, rulings R14 / R21 / R24 / R31).

1. **Pinned holds bind first, at any date, and PINNED MEANS PINNED** (R21, AC-S2-1b). A
   confirmed decision or a placement link is a promise already made; re-deciding it every
   time somebody opens a screen is what pinning exists to prevent. The pinned quantity
   leaves the pile before anybody queues for it - and when the supply it names is OUTSIDE
   this call's span (a site pool, a bin flagged out of planning, another group when the
   caller narrowed with `group=`), the hold is honoured anyway, off the bin the hold itself
   names. The hold IS the supply: somebody confirmed it against real stock, and dropping it
   because the reader narrowed the question printed a board-covered line as `short`.
2. **Then ONE chronological walk, with a pile per ownership group, and a group draws its
   OWN pile and nothing else** (R40, 30 Aug 2026). The group is the warehouse code's suffix
   (`BRW-BB` -> `BB`), which is the pile ladder v4 nets (`group_netting`); the site pools
   are one further group of their own, because a pool is reached through
   `pool_warehouse_id` and is nobody's ownership group.
   Supply lands in ITS OWN group's free pile at its arrival date, and a demand line draws
   from its own group's pile, oldest first, at its own date. **UNDECIDED DEMAND NEVER
   CROSSES A GROUP.** The captain, on a BB line of 507 with no inquiry and no planning
   sitting on IB's pile: "who is us to decide those BB group takes our IB pile". Nobody -
   so the walk does not decide it. Cross-group movement exists only as a PINNED hold, born
   from a board decision somebody confirmed (step 1 above), and until that Confirm the other
   group's pile is untouched. The ladder still OFFERS it (`free_piles_at`, read by
   `use_candidates_for`), which is a PROPOSAL a planner accepts or does not.
   What a line cannot draw is an OPEN SHORTFALL, and the next supply to arrive in ITS OWN
   group clears the group's open shortfalls, earliest required date first, BEFORE it becomes
   free for a later line - which is what makes this first-come by REQUIRED DATE rather than
   by arrival, and it is the case AC-S2-1 pins.
3. **A past-due line is read at today.** Its date has gone; a column for a month that has
   gone is a column nobody can act in. So it queues at `as_of` (ahead of everything dated
   later) and its debt lands in the CURRENT month (AC-S2-5).
4. **Unlocated, TBA and undated lines draw nothing** (R14). A line with no warehouse cannot
   be sourced at all, a line dated on or after the policy's `tba_date_from` is a placeholder
   and a line with no date states nothing, so none of them may take stock a dated, located
   line will need. Their whole quantity is debt, in their own bucket. Unlocated is tested
   FIRST: no location is a fact about what can be done, and it holds whatever the date says.
5. **An overdue document counts as supply after a GRACE PERIOD** (R-O, 3 Sep 2026,
   superseding R31). A document whose arrival has passed is still owed on its outstanding
   balance - the captain, on SO419417: "Available for Project" at BRW read 355 off 725 SPO
   units dated 24 July and 6 August while the ladder lent 4 off the 11 on the floor, and
   the display was the honest one. So a late document counts as supply landing on
   `as_of + overdue_grace_days` (policy, `scm.priority_policy`, SHIPPED default 0): a line
   due before that day gets nothing from it, and a line due after it is planned against the
   ASSUMED date, which is the date the event carries from here on. The date the document
   itself states is kept beside it (`SupplyEvent.stated_at`) so a screen can print both.
   **R31 stays for the DEAD**: past `overdue_dead_days` of lateness (SHIPPED default 0) the
   document counts as nothing and is RETURNED (`uncounted`) rather than dropped, because a
   document nobody mentions reads as a document that is not there, and chasing it is the
   action the screen exists to prompt. Undated incoming is uncounted for the same reason
   from the other side - there is no date to place it on.

   **Both ship at 0 / 0** (captain's ruling, 3 Sep 2026): with dead at 0 any lateness at all
   is past it, so a document one day late counts as nothing exactly as R31 always had it -
   production keeps today's behaviour until someone raises the two numbers through the
   settings route. 14 / 90 is the RECOMMENDED pair once the captain is ready to turn the
   grace on.

6. **A LENDABLE landed pin walks as free on hand at its bin** (STOCK-DEBT-LENDABLE, owner
   30 Sep 2026, option B). Goods that landed for a line due far enough out that the line
   can still re-buy in time - `Hold.lendable`, which the CALLER decides off the board's own
   borrow-donor window, `as_of + lead + 14` - are not spent in step 1. The far line keeps a
   CLAIM on them and takes what is left of it at ITS OWN step in the walk, ahead of any
   other draw; whatever nearer lines took before then is LENT, and the far line reads
   `order_back` for it (a planned re-buy at its own date, which its month books as a
   shortfall) and stays `pinned` for the rest. The receiving take names whose stock it
   was. Free stock at the bin is drawn before the lent part, so a nearer line that only
   needed the spare 12 lends nothing. The owner's own case: SO381065 (Mar 2027) held 88 on
   hand at BRW-BB while SO396071 (Sep 2026) read short 32 - "would like this stock to be
   allocated nearer like oct, nov, dec". The Stock Debt VIEW alone sets `lendable`; the
   board and the ladder still pin (their Borrow step is where the lend becomes a decision).

**Status.** `short` when anything is left uncovered beyond what the line lent,
`order_back` when the line lent something and is still owed it, `pinned` when a decision
holds it, `late` when what covers it arrives after its date, `covered` otherwise. Short
outranks late because a line half covered late is still a line that goes without.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from dataclasses import replace as dataclass_replace
from datetime import date, timedelta
from typing import Dict, List, Optional, Sequence, Tuple

from app.services.scm.coverage_timeline import QTY_PRECISION

#: Quantities below this are noise from Decimal -> float, never a real shortfall.
EPSILON = 10 ** -QTY_PRECISION / 2

KIND_ON_HAND = "on_hand"
KIND_SPO = "spo"
KIND_PO = "po"

STATUS_COVERED = "covered"
STATUS_LATE = "late"
STATUS_SHORT = "short"
STATUS_PINNED = "pinned"
#: STOCK-DEBT-LENDABLE: the line lent its landed goods to nearer lines and is owed a
#: re-buy for them at its own date (rule 6 above).
STATUS_ORDER_BACK = "order_back"

TONE_RED = "red"
TONE_AMBER = "amber"
TONE_GREEN = "green"

#: The three buckets that are not months. They are keys the FE addresses a cell by, so they
#: are strings beside `YYYY-MM` rather than a second field nobody would render.
BUCKET_TBA = "tba"
BUCKET_UNDATED = "undated"
#: A sales-order line with no warehouse on it. 2,312 open lines over 427 products on the
#: 30 Aug dev copy - counted rather than silently dropped, because a screen that lists what
#: is owed and quietly omits a tenth of it is worse than one that says "somebody has to give
#: these a location".
BUCKET_UNLOCATED = "unlocated"

#: What the OVERDUE GRACE answers for a caller that names neither (R-O, 3 Sep 2026). Both
#: are POLICY (`scm.priority_policy.overdue_grace_days` / `overdue_dead_days`, migration
#: 464) and every real caller reads the active row; these are the same two numbers
#: `app.services.scm.priority.FULFILMENT_SETTINGS_DEFAULTS` states, restated so a direct
#: caller of this module walks the documented rule rather than no rule at all.
#:
#: SHIPPED at 0 / 0 (captain's ruling, 3 Sep 2026): dead at 0 makes any lateness at all
#: dead, which is exactly R31 - production keeps today's behaviour until someone raises
#: these. 14 / 90 is the RECOMMENDED pair, not the shipped one.
DEFAULT_OVERDUE_GRACE_DAYS = 0
DEFAULT_OVERDUE_DEAD_DAYS = 0

#: The ownership group the site pools share. A pool carries no group suffix of its own
#: (`BRW`, `MWH`), and reading one as a group would let a `-BB` line eat the pool silently.
POOL_GROUP = "__pool__"


def ownership_group(warehouse: Optional[str], is_pool: bool = False) -> str:
    """Public name for `_group_of`, for a caller outside this module that must agree with
    the walk about which pile a location is (R42's S/O pin stays inside its own group)."""
    return _group_of(warehouse, is_pool)


def _group_of(warehouse: Optional[str], is_pool: bool) -> str:
    """The ownership group a location belongs to, in ONE spelling.

    Delegates to `group_netting.group_of_warehouse_code` (which delegates to
    `sales_agent_service`) rather than restating the suffix rule, so the pile this module
    walks and the pile ladder v4 nets can never come to disagree about what `BRW-BB` is.
    Imported inside the function to keep this module importable with nothing behind it.
    """
    if is_pool:
        return POOL_GROUP
    from app.services.scm.group_netting import group_of_warehouse_code

    return group_of_warehouse_code(warehouse) or ""


@dataclass(frozen=True)
class SupplyEvent:
    """One dated quantity that could cover something.

    `key` is this module's identity for the event and the string a `Hold` names: the SPO
    allocation id, the PO line id, or `on_hand:<warehouse id>`. `ref` is what a person
    reads (`SPO 2026/09-0088`); `bought_for` is a PO line's `expected_date`, which is the
    SO delivery date the line was TYPED against and never an arrival (R29) - carried for
    display and read by nothing here (R30).
    """

    key: str
    kind: str
    warehouse: Optional[str]
    at: Optional[date]
    qty: float
    ref: Optional[str] = None
    bought_for: Optional[date] = None
    is_pool: bool = False
    #: The date the DOCUMENT states, when `at` is an ASSUMED one (R-O). Set only on a late
    #: document the grace period admitted: `at` is then `as_of + overdue_grace_days`, which
    #: is what every reader plans against, and this is what the paperwork says, so a ledger
    #: row can print "assumed 17 Sep 2026, stated 24 Jul". `None` means the two are the
    #: same and there is nothing extra to say.
    stated_at: Optional[date] = None
    #: HOW late, in days, on the day the walk was taken - `as_of - stated_at`, which is the
    #: number the component sentence reads out ("SPO 2026/07-0031 is 41 days late, assumed
    #: by 17 Sep 2026"). Carried rather than derived from `at - stated_at`, which would be
    #: the lateness PLUS the grace and so a different number from the one a person counts.
    days_late: int = 0
    #: R26 (Stock Debt only): an SPO's own RAW ordered/received quantities, beside `qty`
    #: above which stays the NETTED outstanding balance the WALK itself assigns against -
    #: changing what `qty` means here would change what every line's `free_qty`/
    #: `assigned_qty` comes out to, which is not this ruling's business. `None` for every
    #: kind but SPO, on hand included - the drill prints an on-hand row's Received/
    #: Outstanding columns blank, never a fabricated 0.
    ordered_qty: Optional[float] = None
    received_qty: Optional[float] = None
    #: R29 (Stock Debt only): the SPO's own `spo_number`/`spo_line_number` off
    #: `spo_allocations`, beside `ref` (the human label `_spo_ref` already builds) - the
    #: FE's own link target for the Document cell and for a demand line's `assigned_from`
    #: entry. `None` for on hand, which is a bin rather than a document.
    spo_number: Optional[str] = None
    spo_line_number: Optional[int] = None
    #: R42 (Stock Debt only, 28 Sep 2026): a PO line's own document number, its position in
    #: the document and the document's id - the FE opens the PO on that line from Covered
    #: by and from the Supply tab. `None` for every other kind. The PO LINE's id is the
    #: event key itself (`po:<id>`, `parse_supply_key`).
    po_number: Optional[str] = None
    po_line_number: Optional[int] = None
    purchase_order_id: Optional[str] = None
    #: R45 (owner, 30 Sep 2026, PO-NO-AUTO-ASSIGN; Stock Debt view only): a document only
    #: a LINK may spend. "The PO quantity is ordered for a reason, and the user is yet to
    #: do linking in AutoCount, so it will be premature to allocate to other SO by our
    #: calculation." A pin-only event enters no group's pile and clears no shortfall - the
    #: pinned holds (a placement, the book's S/O, landed goods) take from it in step 1 and
    #: nothing else does - and what they leave is FREE in its own month exactly as any
    #: other free supply ("it should still contribute to the stock debt quantity"). The
    #: board and the ladder never set it, so their walk is unchanged.
    pin_only: bool = False


@dataclass(frozen=True)
class DemandLine:
    """One open sales-order line, at the location it is booked in."""

    key: str
    so_number: str
    line_no: Optional[int]
    warehouse: Optional[str]
    agent_code: Optional[str]
    required_date: Optional[date]
    open_qty: float
    is_pool: bool = False
    #: The drill's own Ordered/Delivered columns (R22, Stock Debt only - `open_qty` above
    #: is what the WALK uses and stays as it is). `qty_ordered` is `plan_qty()`
    #: (`coalesce(qty_required, qty_ordered)`), `qty_delivered` the sales-order book's own
    #: column - both default 0.0 so every other `DemandLine(...)` call site in this module
    #: and its tests, none of which states either, keeps compiling unchanged.
    qty_ordered: float = 0.0
    qty_delivered: float = 0.0
    #: R29 (Stock Debt only): the sales order this line belongs to - the FE's own link
    #: target for the Sales order cell (`/scm/sales-orders/<id>`). Defaulted so every
    #: other `DemandLine(...)` call site, none of which states it, keeps compiling
    #: unchanged.
    sales_order_id: Optional[str] = None
    #: R29 (Stock Debt only): the CORE `sales_order_lines.line_no` (AutoCount's own `Seq`)
    #: - Supply's own "Assigned to" entries name THIS, never `line_no` above (the PROJECT
    #: mirror's own numbering, read by the ladder's borrow-donor naming and nothing here).
    #: `None` for a line AutoCount has never numbered.
    core_line_no: Optional[int] = None
    #: R43 (Stock Debt only, 28 Sep 2026): the line's own `sales_order_lines.source_ref`
    #: (`"{database}:{DocKey}:{DtlKey}"`), the value a PO line's S/O
    #: (`from_so_line_ref`) quotes, so a book pin lands on the LINE it names. `None` for a
    #: line the book never keyed.
    source_ref: Optional[str] = None


@dataclass(frozen=True)
class Hold:
    """A decision or a placement link, binding a quantity of one event to one line.

    `kind` / `warehouse` / `ref` describe the supply the hold NAMES, and they are read only
    when that supply is absent from this call's span (AC-S2-1b): the walk then stands an
    event of its own up from them, so the drill can still say `On hand BRW` or `SPO ...`
    rather than leaving a pinned line looking unsourced. When the event IS in the span they
    are ignored - the real one is better than a description of it.
    """

    line_key: str
    supply_key: str
    qty: float
    kind: str = KIND_ON_HAND
    warehouse: Optional[str] = None
    ref: Optional[str] = None
    #: R29: carried beside `ref` for the same reason `SupplyEvent` carries them - the
    #: "stood up" synthetic event branch below (AC-S2-1b) needs them on the HOLD, because
    #: the real document is outside this call's span.
    spo_number: Optional[str] = None
    spo_line_number: Optional[int] = None
    #: R29 addendum: the order inquiry this PLACEMENT came through (`order_inquiry_links`
    #: names one OI row, which points at the SO line) - `None` for an on-hand hold, which
    #: is a decision (`so_line_allocations`), never a placement.
    oi_number: Optional[str] = None
    oi_id: Optional[str] = None
    #: R42: a PO placement's own document, for the stood-up branch (AC-S2-1b) - the same
    #: reason `spo_number` rides here.
    po_number: Optional[str] = None
    purchase_order_id: Optional[str] = None
    #: R43 (owner, 28 Sep 2026, #1346): "when it is assigned, then it will fulfil the
    #: demand ady". The Stock Debt view sets it on every hold naming a PO line (the book's
    #: S/O and a placement alike): the quantity is fulfilled whatever the document's date,
    #: so a dead or undated PO books no shortfall for it (unlike AC-S2-7's precedent) and a
    #: PO landing after the line's date does not either. The board never sets it.
    fulfils: bool = False
    #: #1362 round 5 (owner ruling, 29 Sep 2026): goods that LANDED for this line on its
    #: own purchase (`StockDebtService._landed_holds`). They belong to this line alone, so
    #: the board offers them to no other line - not even another member of its own
    #: planning unit.
    landed: bool = False
    #: STOCK-DEBT-LENDABLE (rule 6): a `landed` pin whose line can wait for a re-buy. Not
    #: spent in step 1; the line takes what is left of it at its own step and reads
    #: `order_back` for what nearer lines drew first. The CALLER decides it (the Stock
    #: Debt view, off `reserve_window_end`); the board never sets it.
    lendable: bool = False


@dataclass(frozen=True)
class Assigned:
    event: SupplyEvent
    qty: float
    #: True when the hold was pinned rather than queued for.
    pinned: bool = False
    #: STOCK-DEBT-LENDABLE: this take is LENT - it came off another line's lendable landed
    #: claim (`Hold.lendable`), and that line is owed it back. The lending line's key and
    #: sales-order number, so the drill can print "On hand BRW-BB (from SO381065)". `None`
    #: on every other take, free stock at the same bin included.
    lent_from_line_key: Optional[str] = None
    lent_from_so: Optional[str] = None
    #: The day IN THE WALK this quantity stopped being free - the drawing line's own date,
    #: or the arrival that later cleared its shortfall. `None` on a pinned hold, which was
    #: spent before the walk started and is free at no date at all - except a lendable
    #: landed claim (rule 6), which is taken at the line's own step and carries that day.
    #: Read by `free_piles_at`, which is the only way to state a pile as it stood on a date
    #: somebody is asking about.
    at: Optional[date] = None
    #: R29 addendum: the order inquiry a PINNED placement came through - `Hold.oi_number`/
    #: `oi_id`, carried onto the take itself because the SHARED `event` object (drawn by
    #: many lines) cannot say which line's own placement this quantity answers for.
    #: `None` on anything that is not a pinned placement (a plain walk draw, or an on-hand
    #: pin, which is a decision rather than a placement).
    oi_number: Optional[str] = None
    oi_id: Optional[str] = None
    #: #1362 round 5: this take is a `Hold.landed` pin - goods that landed for THIS line.
    landed: bool = False


@dataclass(frozen=True)
class LineResult:
    line: DemandLine
    assigned: Tuple[Assigned, ...]
    uncovered: float
    status: str
    #: `YYYY-MM`, `tba` or `undated` - the cell this line is drilled from.
    bucket: str
    #: What the line was short ON ITS OWN DATE - open qty less what it could draw from the
    #: supply available by then. This is the figure its month books (R37), and it differs
    #: from `uncovered` for a `late` line: later supply cleared it, so it ends covered, but
    #: it still went without on the date it was promised. A TBA / undated / unlocated line
    #: draws nothing at all, so its whole quantity is short.
    short_at_date: float = 0.0
    #: STOCK-DEBT-LENDABLE (rule 6): who drew this line's lendable landed goods before
    #: its own date, each with the quantity, in walk order - the "Lent to SO396071 (32)"
    #: entries. `lent_qty` is their sum, what was lent; what the line is still OWED for it
    #: is `min(lent_qty, uncovered)`, which is what the `order_back` pill prints. Empty / 0
    #: on every line that lent nothing.
    lent: Tuple["Lent", ...] = ()
    lent_qty: float = 0.0


@dataclass(frozen=True)
class Lent:
    """One lend: the receiving line, and how much of the lender's claim it took."""

    line_key: str
    so_number: str
    qty: float


@dataclass(frozen=True)
class MonthBalance:
    """One month, on its own: free supply dated in it, less the shortfalls it books (R37)."""

    key: str
    balance: float
    tone: str


@dataclass(frozen=True)
class Assignment:
    lines: Tuple[LineResult, ...]
    months: Tuple[MonthBalance, ...]
    #: Event key -> quantity nobody took, once the whole walk is over. The other half of a
    #: month's balance (R37), and the number the drill prints beside a supply row, so a
    #: reader can add the lightbox up and get the cell they pressed.
    free: Dict[str, float]
    #: Signed totals: demand that draws nothing is debt, so all three are <= 0.
    tba: float
    undated: float
    #: Demand booked with no warehouse at all. It cannot be sourced and it is not in any
    #: group's pile, so it is stated on its own rather than netted into a month.
    unlocated: float
    #: Supply that contributed nothing - overdue, or with no arrival date at all (R31).
    uncounted: Tuple[SupplyEvent, ...]
    #: Every COUNTED event, in no particular order. Carried because `free` states only the
    #: quantities, and a caller that has to name a pile ("40 free at DC1-IR") needs the
    #: events themselves. `free_piles_at` reads this and nothing else.
    supply: Tuple[SupplyEvent, ...] = ()


@dataclass
class _Open:
    """A line's mutable state during the walk."""

    line: DemandLine
    remaining: float
    taken: List[Assigned] = field(default_factory=list)
    late: bool = False
    pinned: bool = False
    #: What was still missing when this line's own date came round (R37). Written once, by
    #: the walk, at the line's own step.
    short_at_date: float = 0.0
    #: Pinned to a document that is NOT supply - an overdue one (R31). The promise stands,
    #: so the line reads `pinned`, and the goods are still not there, so the month is still
    #: owed the quantity. Without this the two rulings would cancel each other out.
    uncounted_pinned: float = 0.0
    #: STOCK-DEBT-LENDABLE: event key -> what this line may still take of its lendable
    #: landed goods at its own step (rule 6). Registered in step 1 instead of spending the
    #: event; drawn by `_walk` at the line's date, ahead of the ordinary pile draw.
    claims: Dict[str, float] = field(default_factory=dict)
    #: Event key -> what nearer lines took of the claim above before this line's step.
    lent_by_event: Dict[str, float] = field(default_factory=dict)


def parse_supply_key(supply_key: str) -> Tuple[Optional[str], Optional[str]]:
    """`spo:<allocation id>` / `po:<purchase order line id>` split into `(kind, id)`.

    The event key is this module's own address for a document, so it is read back here and
    nowhere else - it was partitioned by hand in four places across two services, which is a
    format written once and understood four times. `(None, None)` for anything that is not
    one, which every caller treats as "no document".
    """
    kind, _sep, target = str(supply_key or "").partition(":")
    if not target or kind not in (KIND_SPO, KIND_PO):
        return None, None
    return kind, target


def _is_lendable_claim(hold: Hold, left: dict, events: dict) -> bool:
    """Rule 6: a lendable hold on a COUNTED, walkable floor. A pin-only event is never
    walked, so a claim on it could never be taken; such a hold pins as any other."""
    return bool(
        hold.lendable
        and hold.supply_key in left
        and not events[hold.supply_key].pin_only
    )


def _round(value: float) -> float:
    """Rounded, and NEVER negative zero: `-0.0` reaches a screen as `-0`, which reads as a
    debt of nothing rather than as nothing owed."""
    out = round(float(value), QTY_PRECISION)
    return 0.0 if out == 0 else out


def month_key(value: date) -> str:
    return value.strftime("%Y-%m")


def _month_start(key: str) -> date:
    year, month = key.split("-")
    return date(int(year), int(month), 1)


def _next_month(key: str) -> str:
    year, month = (int(part) for part in key.split("-"))
    return f"{year + 1}-01" if month == 12 else f"{year}-{month + 1:02d}"


def month_axis(first: str, last: str) -> List[str]:
    """Every month key from `first` to `last` inclusive, gaps included.

    A missing column is not the same as a zero one: a month with nothing due and nothing
    arriving reads 0 (nothing owed, nothing spare, R37), and leaving it out would make the
    calendar jump a month.
    """
    out = [first]
    while out[-1] < last:
        out.append(_next_month(out[-1]))
    return out


def tone_for(balance: float, key: str, *, as_of: date, lead_days: int) -> str:
    """Red when the debt cannot be bought in time, amber when it still can, green in surplus.

    The horizon is `as_of + lead` - the earliest a fresh purchase could land - so a month
    that STARTS before it is a month no buying decision can still rescue. That is the whole
    reading: red is not "worse", it is "too late to buy".
    """
    if balance >= -EPSILON:
        return TONE_GREEN
    return TONE_RED if _month_start(key) < _horizon(as_of, lead_days) else TONE_AMBER


def _horizon(as_of: date, lead_days: int):
    from datetime import timedelta

    return as_of + timedelta(days=max(int(lead_days or 0), 0))


def effective_date(at: Optional[date], as_of: date) -> date:
    """Where an event actually sits on the axis: never before today (AC-S2-5).

    PUBLIC because the caller has to place the SAME event in the same cell when it lists the
    drill - a document the balance counted in September and the drill filed under July is a
    lightbox that does not foot with the cell that opened it.
    """
    return as_of if at is None or at < as_of else at


def counted_event(
    event: SupplyEvent,
    *,
    as_of: date,
    overdue_grace_days: Optional[int] = None,
    overdue_dead_days: Optional[int] = None,
) -> Optional[SupplyEvent]:
    """One supply event as the walk counts it, or `None` when it counts as nothing (R-O).

    THE ONE PLACE the overdue rule lives, so `assign` and `group_book_positions` (which
    reads `assign`'s own answer) can never come to two views of the same document.

    * on hand is held NOW, whatever date the caller stamped it with;
    * an UNDATED document cannot be placed on the axis at all, so it counts as nothing;
    * a document dated on or after `as_of` counts as itself;
    * a document whose arrival has passed counts as supply on its outstanding balance,
      landing on `as_of + overdue_grace_days` - the ASSUMED date, which is what it carries from
      here on, with the date the paperwork states kept beside it (`stated_at`) and the
      lateness counted for the sentence (`days_late`);
    * unless it is later than `overdue_dead_days`, when it counts as nothing (R31 kept for
      the dead) and the caller returns it as `uncounted` so somebody chases it.
    """
    if float(event.qty) <= EPSILON:
        return None
    if event.kind == KIND_ON_HAND:
        return event
    if event.at is None:
        return None
    if event.at >= as_of:
        return event
    grace = max(
        int(
            DEFAULT_OVERDUE_GRACE_DAYS
            if overdue_grace_days is None
            else overdue_grace_days
        ),
        0,
    )
    dead = max(
        int(
            DEFAULT_OVERDUE_DEAD_DAYS if overdue_dead_days is None else overdue_dead_days
        ),
        0,
    )
    days_late = (as_of - event.at).days
    if days_late > dead:
        return None
    return dataclass_replace(
        event,
        at=as_of + timedelta(days=grace),
        stated_at=event.at,
        days_late=days_late,
    )


def assign(
    product: str,
    *,
    as_of: date,
    tba_from: date,
    lead_days: int,
    supply: Sequence[SupplyEvent],
    demand: Sequence[DemandLine],
    pinned: Sequence[Hold] = (),
    overdue_grace_days: Optional[int] = None,
    overdue_dead_days: Optional[int] = None,
) -> Assignment:
    """Assign `supply` to `demand` for one product, and state the balance it leaves.

    `product` is carried for the caller's own error messages and logging only - one call is
    one product, and mixing two would net a shortage away against another item's surplus.

    `overdue_grace_days` / `overdue_dead_days` are the policy numbers R-O added; `None`
    walks the documented default, so a direct caller still walks the rule.
    """
    counted: List[SupplyEvent] = []
    uncounted: List[SupplyEvent] = []
    for event in supply:
        if float(event.qty) <= EPSILON:
            continue
        admitted = counted_event(
            event,
            as_of=as_of,
            overdue_grace_days=overdue_grace_days,
            overdue_dead_days=overdue_dead_days,
        )
        if admitted is None:
            uncounted.append(event)
        else:
            counted.append(admitted)

    dated: List[DemandLine] = []
    totals = {BUCKET_UNLOCATED: 0.0, BUCKET_TBA: 0.0, BUCKET_UNDATED: 0.0}
    results: List[LineResult] = []

    for line in demand:
        # Unlocated FIRST: a line with no warehouse is in no group's pile, so it could not
        # draw whatever its date said, and filing it under TBA would hide the thing that
        # actually has to be fixed about it.
        if not line.warehouse and not line.is_pool:
            bucket = BUCKET_UNLOCATED
        elif line.required_date is None:
            bucket = BUCKET_UNDATED
        elif line.required_date >= tba_from:
            bucket = BUCKET_TBA
        else:
            dated.append(line)
            continue
        totals[bucket] += float(line.open_qty)
        results.append(
            LineResult(
                line=line,
                assigned=(),
                uncovered=_round(line.open_qty),
                status=STATUS_SHORT,
                bucket=bucket,
                short_at_date=_round(line.open_qty),
            )
        )

    left = {event.key: float(event.qty) for event in counted}
    events = {event.key: event for event in counted}
    uncounted_by_key = {event.key: event for event in uncounted}
    uncounted_left = {event.key: float(event.qty) for event in uncounted}
    states = {
        line.key: _Open(line=line, remaining=float(line.open_qty)) for line in dated
    }

    # 1. pinned holds, at any date -------------------------------------------------------
    #
    # A hold whose supply is not in this call's span is honoured off an event stood up from
    # the hold itself (AC-S2-1b). It never enters `left`, so nothing else can queue for it -
    # the pin consumed the whole of it - but it IS counted in the month balance, or the cell
    # would print red over a drill in which every line reads `pinned`.
    #: Rule 6: event key -> what the lendable claims registered so far already cover of
    #: it, so two far lines cannot both claim the whole floor.
    claimed: Dict[str, float] = {}
    #: Event key -> the lines claiming it, in WALK order (the order a lend is charged to
    #: them in `_attribute_lends`, and the order the walk lets them take their claims).
    claimants: Dict[str, List[_Open]] = {}
    # Lendable holds are registered AFTER every ordinary pin (so a decision's take is
    # netted from the floor first) and in the LINE's own walk order, never the caller's
    # hold order: `_landed_holds` reads an unordered query, and charging the claims in row
    # order let the same book read `short` on one refresh and `order_back` on the next.
    ordinary = [hold for hold in pinned if not _is_lendable_claim(hold, left, events)]
    lendable = sorted(
        (hold for hold in pinned if _is_lendable_claim(hold, left, events)),
        key=lambda hold: (
            _identity(states[hold.line_key]) if hold.line_key in states else (),
            hold.supply_key,
        ),
    )
    for hold in ordinary + lendable:
        state = states.get(hold.line_key)
        if state is None:
            continue
        if hold in lendable:
            # Rule 6: not spent here. The line takes what is left of the claim at its OWN
            # step (`_walk`), after every nearer line has queued; what they took is lent.
            # Capped exactly as a pin would be - by the line's need and by the floor -
            # less what earlier claims on the same floor already cover.
            claim = min(
                float(hold.qty),
                state.remaining - sum(state.claims.values()),
                left[hold.supply_key] - claimed.get(hold.supply_key, 0.0),
            )
            if claim <= EPSILON:
                continue
            state.claims[hold.supply_key] = state.claims.get(hold.supply_key, 0.0) + claim
            claimed[hold.supply_key] = claimed.get(hold.supply_key, 0.0) + claim
            holders = claimants.setdefault(hold.supply_key, [])
            if state not in holders:
                holders.append(state)
            continue
        if hold.supply_key in left:
            take = min(float(hold.qty), state.remaining, left[hold.supply_key])
            if take <= EPSILON:
                continue
            left[hold.supply_key] -= take
            event = events[hold.supply_key]
        elif hold.supply_key in uncounted_by_key:
            # A DEAD document (R-O) somebody has already been promised - `uncounted_by_key`
            # holds only what `counted_event` refused outright, past `overdue_dead_days`, a
            # late-but-alive document having already been counted above. The promise stands
            # (the line reads `pinned`) and the drill names the order the document is
            # placed against - but the document is still not supply, so it adds nothing to
            # the month. Chasing it is the action the red cell is asking for.
            #
            # R43: unless the hold `fulfils` (the view's PO pins): assigned means fulfilled,
            # so the line is owed nothing for it, and the document still adds nothing free.
            # A fulfilling hold is capped by what the document still has outstanding, the
            # way a counted one is capped by `left`: two placements, or a placement larger
            # than what is left after a receipt, cannot fulfil more than the PO will bring.
            take = min(float(hold.qty), state.remaining)
            if hold.fulfils:
                take = min(take, uncounted_left[hold.supply_key])
            if take <= EPSILON:
                continue
            event = uncounted_by_key[hold.supply_key]
            if hold.fulfils:
                uncounted_left[hold.supply_key] -= take
            else:
                state.uncounted_pinned += take
        else:
            take = min(float(hold.qty), state.remaining)
            if take <= EPSILON:
                continue
            event = SupplyEvent(
                key=hold.supply_key,
                kind=hold.kind,
                warehouse=hold.warehouse,
                # Placed at `as_of`: the promise is in force now, and the span this call was
                # given holds no arrival date to place it on.
                at=as_of,
                qty=_round(take),
                ref=hold.ref,
                spo_number=hold.spo_number,
                spo_line_number=hold.spo_line_number,
                po_number=hold.po_number,
                purchase_order_id=hold.purchase_order_id,
            )
            counted.append(event)
        state.remaining -= take
        state.pinned = True
        state.taken.append(
            Assigned(
                event=event,
                qty=_round(take),
                pinned=True,
                # R29 addendum: the placement's own order inquiry, carried on the TAKE
                # rather than the (possibly shared) event - `None` for an on-hand hold,
                # which is a decision, not a placement.
                oi_number=hold.oi_number,
                oi_id=hold.oi_id,
                landed=hold.landed,
            )
        )

    # 2. ONE chronological walk, with a pile per ownership group ---------------------------
    #
    # R45: a `pin_only` event stays out of the walk. The pins above have already taken
    # what a link gave them; nobody queues for the rest, which stays in `left` and so in
    # `free`, credited to its own month by `_months` below.
    #
    # Rule 6: what was FREE at a claimed floor when the walk started - the part a nearer
    # line draws before it touches anybody's landed goods (`_attribute_lends`).
    free_before = {key: left[key] - claimed[key] for key in claimed}
    draws: Dict[str, List[Tuple[_Open, int]]] = {key: [] for key in claimed}
    _walk(
        as_of=as_of,
        events=[
            event
            for event in counted
            if left.get(event.key, 0.0) > EPSILON and not event.pin_only
        ],
        left=left,
        states=[states[line.key] for line in dated],
        draws=draws,
        claimed=claimed,
    )
    lent_by_line = _attribute_lends(
        claimants=claimants, free_before=free_before, draws=draws,
    )
    del claimed

    for line in dated:
        state = states[line.key]
        uncovered = _round(max(state.remaining, 0.0))
        lent = tuple(lent_by_line.get(line.key, ()))
        lent_qty = _round(sum(item.qty for item in lent))
        if uncovered > lent_qty + EPSILON:
            status = STATUS_SHORT
        elif uncovered > EPSILON and lent_qty > EPSILON:
            status = STATUS_ORDER_BACK
        elif state.pinned:
            status = STATUS_PINNED
        elif state.late:
            status = STATUS_LATE
        else:
            status = STATUS_COVERED
        results.append(
            LineResult(
                line=line,
                assigned=tuple(state.taken),
                uncovered=uncovered,
                status=status,
                bucket=month_key(effective_date(line.required_date, as_of)),
                short_at_date=state.short_at_date,
                lent=lent,
                lent_qty=lent_qty,
            )
        )

    free = {
        event.key: _round(max(left.get(event.key, 0.0), 0.0)) for event in counted
    }
    months = _months(
        as_of=as_of,
        lead_days=lead_days,
        supply=counted,
        free=free,
        states=[states[line.key] for line in dated],
    )

    return Assignment(
        lines=tuple(results),
        months=tuple(months),
        free=free,
        tba=_round(-totals[BUCKET_TBA]),
        undated=_round(-totals[BUCKET_UNDATED]),
        unlocated=_round(-totals[BUCKET_UNLOCATED]),
        uncounted=tuple(uncounted),
        supply=tuple(counted),
    )


def _walk(
    *,
    as_of: date,
    events: Sequence[SupplyEvent],
    left: dict,
    states: Sequence[_Open],
    draws: Optional[Dict[str, List[Tuple["_Open", int]]]] = None,
    claimed: Optional[Dict[str, float]] = None,
) -> None:
    """The whole product's book, in date order, with a pile per ownership group.

    ONE walk over every group, and **a group draws its OWN pile and nothing else** (R40).
    Each group keeps its own free pile and its own queue of open shortfalls; a line draws
    its own group's pile, oldest first, at its own date, and an arrival clears its own
    group's open shortfalls before it becomes free for a later line of that group.

    The 30 August draft let an undecided line reach into the other project groups' piles,
    on the reading that free is free. The captain refused it on the live book - a BB line of
    507 with no inquiry and no planning had silently eaten the IB pile - and the rule is
    now: an undecided line is a line nobody has decided anything about, so the walk decides
    nothing about it either. Another group's stock reaches this line only through a PINNED
    hold (step 1 above), which is a Confirm somebody pressed. The OFFER still exists and is
    still made - `free_piles_at`, read by the ladder's `use_candidates_for` - because
    proposing is not assuming.

    A SITE POOL is sealed off for a second reason on top of that one: taking pool stock is
    the pool RUNG, which raises an ORDER_BACK (R34), so it could never have been a free draw
    even when project groups shared.

    Rule 6 (STOCK-DEBT-LENDABLE): a line with a CLAIM on a floor (`_Open.claims`) takes
    what is left of it at its own step, before the ordinary pile draw - a pinned, landed
    take, exactly what step 1 would have given it, less what nearer lines drew first.
    `draws`, when given, is filled with every walk draw off a claimed floor, in walk
    order, as `(state, index into state.taken)`, so the caller can say which of them were
    lent and by whom (`_attribute_lends`). `claimed` is event key -> what is still claimed
    of it by lines whose step has not come: the pile draw takes the group's FREE stock -
    every bin's unclaimed part - before it touches anybody's claim, so the lend is the
    same whatever the bins are called (a bin's ref decides the pile's order), and it is
    only ever what the group could not cover otherwise. Each claim is released at its
    line's own step.

    Mutates `left`, `claimed` and the line states.
    """
    draws = {} if draws is None else draws
    claimed = {} if claimed is None else claimed
    steps: List[Tuple[date, int, int, object]] = []
    for event in events:
        if left.get(event.key, 0.0) <= EPSILON:
            continue
        at = effective_date(event.at, as_of)
        # Supply BEFORE demand on a same-day tie, the ordering `coverage_timeline._sort_key`
        # already fixes for every consumer: a container cleared in the morning covers a
        # despatch due the same day.
        steps.append((at, 0, 0, event))
    for index, state in enumerate(states):
        at = effective_date(state.line.required_date, as_of)
        steps.append((at, 1, index, state))
    # The required date, then the document/line identity, so two rows that agree on
    # everything a person can see still sort the same way on every run.
    steps.sort(
        key=lambda step: (
            step[0],
            step[1],
            _identity(step[3]),
        )
    )

    #: event key -> the event, for a claim to name its floor (rule 6).
    by_key = {event.key: event for event in events}
    #: group -> arrived, not yet taken, oldest first
    piles: Dict[str, List[SupplyEvent]] = {}
    #: group -> open, earliest required date first
    shortfalls: Dict[str, List[_Open]] = {}

    for at, kind, _index, item in steps:
        if kind == 0:
            event: SupplyEvent = item  # type: ignore[assignment]
            group = _group_of(event.warehouse, event.is_pool)
            # This group's own queue, earliest required date first. A snapshot list,
            # because the loop removes from the queue as it goes.
            for state in list(shortfalls.get(group, ())):
                if left[event.key] <= EPSILON:
                    break
                take = min(state.remaining, left[event.key])
                if take <= EPSILON:
                    continue
                left[event.key] -= take
                state.remaining -= take
                state.late = True
                state.taken.append(Assigned(event=event, qty=_round(take), at=at))
                if event.key in draws:
                    draws[event.key].append((state, len(state.taken) - 1))
                if state.remaining <= EPSILON:
                    shortfalls[group].remove(state)
            if left[event.key] > EPSILON:
                piles.setdefault(group, []).append(event)
            continue

        state: _Open = item  # type: ignore[assignment]
        group = _group_of(state.line.warehouse, state.line.is_pool)
        # Rule 6: the line's own claim first, off the floor it names (the event is in
        # this group's pile - the claim was registered against a counted, non-pin-only
        # floor of the line's own bin). What nearer lines drew is what the line lent.
        for key, claim in state.claims.items():
            take = min(claim, left.get(key, 0.0), state.remaining)
            state.lent_by_event[key] = _round(max(claim - take, 0.0))
            # The claim is over once its line has stepped, taken or not.
            claimed[key] = claimed.get(key, 0.0) - claim
            if take <= EPSILON:
                continue
            event = by_key[key]
            left[key] -= take
            state.remaining -= take
            state.pinned = True
            state.taken.append(
                Assigned(event=event, qty=_round(take), pinned=True, landed=True, at=at)
            )
        if state.remaining > EPSILON:
            # Its OWN group's pile, in arrival order (it was appended that way), and no
            # other group's (R40). Two passes (rule 6): first every bin's UNCLAIMED part,
            # then, only if the line is still short, the parts other lines have claimed.
            pile = piles.setdefault(group, [])
            for free_only in (True, False):
                for event in list(pile):
                    if state.remaining <= EPSILON:
                        break
                    available = left[event.key]
                    if free_only:
                        available -= max(claimed.get(event.key, 0.0), 0.0)
                    take = min(state.remaining, available)
                    if take <= EPSILON:
                        if left[event.key] <= EPSILON:
                            pile.remove(event)
                        continue
                    left[event.key] -= take
                    state.remaining -= take
                    state.taken.append(Assigned(event=event, qty=_round(take), at=at))
                    if event.key in draws:
                        draws[event.key].append((state, len(state.taken) - 1))
                    if left[event.key] <= EPSILON:
                        pile.remove(event)
        # Its own date has now passed in the walk, so whatever is left is what this line
        # went without ON ITS DATE - the figure its month books (R37). Later supply may
        # still clear it (it becomes `late`), and that does not give the month back: the
        # order was still short in the month it was promised for.
        state.short_at_date = _round(
            max(state.remaining, 0.0) + state.uncounted_pinned
        )
        if state.remaining > EPSILON:
            shortfalls.setdefault(group, []).append(state)


def _attribute_lends(
    *,
    claimants: Dict[str, List[_Open]],
    free_before: Dict[str, float],
    draws: Dict[str, List[Tuple[_Open, int]]],
) -> Dict[str, List[Lent]]:
    """Which walk draws off a claimed floor were LENT, and by whom (rule 6).

    Free stock first: the part of the floor no claim covered is drawn before anybody's
    landed goods, so a nearer line that only needed the spare 12 of a 100 floor lent
    nothing and the far line reads plain `pinned`. What goes beyond that is charged to the
    claimants in the holds' own order, and each lending line's `lent_by_event` - what its
    claim came up short by at its own step - is exactly how much of its claim was lent:
    an earlier-due claimant took its claim first at its own step, so the later one bears
    the lend. The receiving take is SPLIT where a draw straddles the free part and a lend,
    so the drill can print both halves.

    Returns lending line key -> its `Lent` entries, in walk order. Mutates `state.taken`
    on the receiving lines (the split, and the `lent_from_*` stamp).
    """
    out: Dict[str, List[Lent]] = {}
    for key, taken in draws.items():
        free = max(free_before.get(key, 0.0), 0.0)
        lenders = [
            (state, state.lent_by_event.get(key, 0.0))
            for state in claimants.get(key, ())
            if state.lent_by_event.get(key, 0.0) > EPSILON
        ]
        if not lenders:
            continue
        lender_index = 0
        for state, index in taken:
            item = state.taken[index]
            qty = float(item.qty)
            pieces: List[Assigned] = []
            if free > EPSILON:
                spare = min(free, qty)
                free -= spare
                qty -= spare
                pieces.append(dataclass_replace(item, qty=_round(spare)))
            while qty > EPSILON and lender_index < len(lenders):
                lender, room = lenders[lender_index]
                if room <= EPSILON:
                    lender_index += 1
                    continue
                part = min(room, qty)
                lenders[lender_index] = (lender, room - part)
                qty -= part
                pieces.append(
                    dataclass_replace(
                        item,
                        qty=_round(part),
                        lent_from_line_key=lender.line.key,
                        lent_from_so=lender.line.so_number,
                    )
                )
                out.setdefault(lender.line.key, []).append(
                    Lent(
                        line_key=state.line.key,
                        so_number=state.line.so_number,
                        qty=_round(part),
                    )
                )
            if qty > EPSILON:
                # Beyond every claim: free stock that arrived after the claims were
                # capped cannot exist on an on-hand floor, but the arithmetic stays
                # honest rather than dropping a quantity.
                pieces.append(dataclass_replace(item, qty=_round(qty)))
            state.taken[index] = pieces[0]
            for extra in pieces[1:]:
                state.taken.append(extra)
    return out


def free_piles_at(
    assignment: Assignment, *, at: date, as_of: date
) -> Dict[str, List[Tuple[SupplyEvent, float]]]:
    """Every ownership group's free pile AS IT STOOD on `at`, oldest first.

    THE PROPOSAL HALF OF R40. The walk gives an undecided line its own group and nothing
    else, so another group's pile is never assumed - but the ladder still has to OFFER it
    (PLAN 3.2 step 1's second half), and this is the number that offer is made of: what had
    arrived in that group by `at`, less every quantity spent on or before `at` - the pins,
    which are spent before the walk starts, and the draws and late clears the walk itself
    made up to that day.

    Same-day competition counts as spent: a line of that group due on `at` has already
    queued. That is the conservative reading, and it is the one that keeps the offer from
    naming stock somebody due the same morning is going to take.

    Returns `group -> [(event, free quantity)]`, with a pool's own group under
    `POOL_GROUP`; the caller decides which groups it may name.
    """
    spent: Dict[str, float] = {}
    for row in assignment.lines:
        for item in row.assigned:
            if item.at is not None and item.at > at:
                continue
            spent[item.event.key] = spent.get(item.event.key, 0.0) + float(item.qty)
    out: Dict[str, List[Tuple[SupplyEvent, float]]] = {}
    for event in assignment.supply:
        if effective_date(event.at, as_of) > at:
            continue
        free = float(event.qty) - spent.get(event.key, 0.0)
        if free <= EPSILON:
            continue
        out.setdefault(_group_of(event.warehouse, event.is_pool), []).append(
            (event, _round(free))
        )
    for pile in out.values():
        pile.sort(key=lambda pair: (effective_date(pair[0].at, as_of), _identity(pair[0])))
    return out


def group_book_positions(assignment: Assignment) -> Dict[str, float]:
    """Each ownership group's WHOLE OPEN BOOK, in one signed number (R-M, 3 Sep 2026).

    `free_piles_at` above states a pile AS IT STOOD on a date, which is the right question
    of the group that OWNS it - its own earlier lines have queued, its later ones have not.
    It is the wrong question of a group being asked to lend, and the captain's production
    cell is what that costs: `BRW-IB` held 2,237 on hand against 2,684 of open IB demand,
    1,708 due on or before 5 October and 976 after, and the pile read 785 free on 5 October
    because the 976 was never subtracted. The ladder then proposed a BB line "4 from
    BRW-IB ... free stock is owed to nobody", off a group 447 short on its own book.

    So a lending group's offer is bounded by this: what it holds and what the assignment
    counts as coming - which is the walk's own reading of a late document and not a second
    one (R-O): a late-but-alive document is in `supply` at its assumed date and counts here
    too, and a DEAD one is in `uncounted` and counts as nothing (R31). Less ALL of its open
    demand, less any CONFIRMED hold another group has already taken out of it. A same-group hold is not subtracted - the line holding it
    is in this group's own demand already, and taking it twice would make every pinned
    group read short.

    `group -> position`, negative when the group is short. `POOL_GROUP` carries the site
    pools' own book; a location with no ownership group carries the empty key, and neither
    is a group the ladder may name. The CALLER decides what to do with a negative: this
    states the book and bounds nothing on its own.
    """
    counted = {event.key for event in assignment.supply}
    out: Dict[str, float] = {}
    for event in assignment.supply:
        group = _group_of(event.warehouse, event.is_pool)
        out[group] = out.get(group, 0.0) + float(event.qty)
    for row in assignment.lines:
        line = row.line
        if not line.warehouse and not line.is_pool:
            # Unlocated demand is in no group's pile at all (BUCKET_UNLOCATED), so netting
            # it against one would charge a group for an obligation it cannot be asked to
            # meet.
            continue
        group = _group_of(line.warehouse, line.is_pool)
        out[group] = out.get(group, 0.0) - float(line.open_qty)
        for item in row.assigned:
            if not item.pinned or item.event.key not in counted:
                continue
            lender = _group_of(item.event.warehouse, item.event.is_pool)
            if lender == group:
                continue
            out[lender] = out.get(lender, 0.0) - float(item.qty)
    return {group: _round(value) for group, value in out.items()}


def _identity(item) -> tuple:
    """A stable tie-break for a step: the document, then the line, then the key."""
    if isinstance(item, _Open):
        line = item.line
        return (
            line.required_date or date.min,
            line.so_number or "",
            line.line_no is None,
            line.line_no or 0,
            line.key,
        )
    return (item.at or date.min, item.ref or "", 0, 0, item.key)


def _months(
    *,
    as_of: date,
    lead_days: int,
    supply: Sequence[SupplyEvent],
    free: Dict[str, float],
    states: Sequence[_Open],
) -> List[MonthBalance]:
    """Each month on its own: what is spare in it, less what goes without in it (R37).

    Two sums, per month key:

    - the supply DATED IN the month that nobody took by the end of the walk. Supply a line
      consumed - including a late line whose shortfall it cleared - is spent, and spent
      stock is not spare in any month.
    - what each line due in the month was short AT ITS OWN DATE.

    Nothing carries. A month with neither reads 0, which is the true statement about it:
    nothing is owed and nothing is spare. The reader who wants the position over time reads
    the row, which is the calendar.
    """
    credit: Dict[str, float] = {}
    debit: Dict[str, float] = {}

    for event in supply:
        spare = free.get(event.key, 0.0)
        if spare <= EPSILON:
            continue
        key = month_key(effective_date(event.at, as_of))
        credit[key] = credit.get(key, 0.0) + spare
    for state in states:
        short = state.short_at_date
        if short <= EPSILON:
            continue
        key = month_key(effective_date(state.line.required_date, as_of))
        debit[key] = debit.get(key, 0.0) + short

    first = month_key(as_of)
    dates = [effective_date(event.at, as_of) for event in supply]
    dates += [effective_date(state.line.required_date, as_of) for state in states]
    last = max((month_key(value) for value in dates), default=first)

    out: List[MonthBalance] = []
    for key in month_axis(first, max(last, first)):
        balance = _round(credit.get(key, 0.0) - debit.get(key, 0.0))
        out.append(
            MonthBalance(
                key=key,
                balance=balance,
                tone=tone_for(balance, key, as_of=as_of, lead_days=lead_days),
            )
        )
    return out


__all__ = [
    "BUCKET_TBA",
    "BUCKET_UNDATED",
    "BUCKET_UNLOCATED",
    "KIND_ON_HAND",
    "KIND_PO",
    "KIND_SPO",
    "POOL_GROUP",
    "STATUS_COVERED",
    "STATUS_LATE",
    "STATUS_ORDER_BACK",
    "STATUS_PINNED",
    "STATUS_SHORT",
    "TONE_AMBER",
    "TONE_GREEN",
    "TONE_RED",
    "Assigned",
    "Assignment",
    "DEFAULT_OVERDUE_DEAD_DAYS",
    "DEFAULT_OVERDUE_GRACE_DAYS",
    "DemandLine",
    "Hold",
    "Lent",
    "LineResult",
    "MonthBalance",
    "SupplyEvent",
    "assign",
    "counted_event",
    "effective_date",
    "free_piles_at",
    "group_book_positions",
    "month_axis",
    "month_key",
    "ownership_group",
    "parse_supply_key",
    "tone_for",
]
