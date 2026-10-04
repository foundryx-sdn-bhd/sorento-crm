"""Supply composition and atomic confirmation (front planning section 6, Stage 1C).

The wire shapes here are the ones the Phase 1 frontend was built against
(`sorento_crm_frontend/app/(protected)/project-sales/_shared/types/fulfilmentPlanning.types.ts`),
transcribed field for field. Two conventions carry over from the reconciliation schemas
next door and are the reason a screen full of these renders no identifiers a person has to
resolve:

* **quantities are decimal STRINGS.** A float round trip loses the tail of a quantity a
  customer signed for, and these numbers are compared for exact equality at confirmation.
* **codes, not ids, everywhere the screen reads.** Warehouse CODE, item CODE, project
  REFERENCE. The id fields that do exist (`source_warehouse_id`, `donor_project_id`,
  `warehouse_id` on a candidate, `project_id` on the proposal, `product_id` on a line) are
  addressing only: the confirm payload names a warehouse and a donor project by id while the
  sheet names them by code, and the read had no other way to carry the pair -
  `SupplyLine.product_id` is what the Proof button asks the classification-evidence endpoint
  with. They are never rendered, the same way `ReconciliationLine.id` is never rendered.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field

# S4 (`PLAN-local-supplier-oi-routing.md`): the Grid Location table's own row shape,
# reused rather than duplicated for `BorrowCandidate.location`.
from app.schemas.project_board import BoardCellLocation

SupplyComponentKind = Literal["timely_spo", "reserve", "borrow", "buy"]
BorrowSource = Literal["other_location", "other_project"]
SupplyDecisionState = Literal["active", "superseded", "challenged"]


class SupplyComponent(BaseModel):
    kind: SupplyComponentKind
    qty: str
    #: The sentence the rule that produced the quantity wrote (AC-B14). Deterministic,
    #: never LLM-written, frozen with the snapshot at confirmation.
    reason: str
    source_location: Optional[str] = None
    source_warehouse_id: Optional[str] = None
    donor_project_ref: Optional[str] = None
    donor_project_id: Optional[str] = None
    #: What CS typed: the Borrow reason, or the reason a discontinued product is bought.
    cs_reason: Optional[str] = None
    #: Ladder v2 (`PLAN-demo-followups-19aug-ladder-v2.md` section E): which rung of the
    #: source ladder produced this component - finer than `kind`, which only carries the
    #: balance-invariant bucket. `None` on a component frozen before ladder v2 landed.
    rung: Optional[str] = None
    #: The donor sales order for a `group_borrow` component (section E.4): its number,
    #: line and agent, so the sheet can name who it came from. `None` on every other kind.
    donor_so_number: Optional[str] = None
    donor_line_no: Optional[int] = None
    donor_agent_code: Optional[str] = None
    #: The donor shares this line's own sales agent - "she can authorise CS to move stock
    #: between her own orders" (section 8).
    same_agent: bool = False
    #: The order-back this component raised: an Order Inquiry Buy for the donor's own
    #: line, equal to what was taken. `None` outside `group_borrow`.
    order_back_qty: Optional[str] = None
    #: Addressing only, never rendered: the donor's own core sales-order line id, so
    #: re-confirming this proposal AS PROPOSED can re-identify the same donor line
    #: without a second lookup (`ConfirmBorrowComponent.donor_core_line_id`).
    donor_core_line_id: Optional[str] = None
    #: The DONOR's own required date (section E.4: "urgency = the donor's required
    #: date"), round-tripped into `ConfirmBorrowComponent.donor_required_date` so the
    #: order-back Order Inquiry row's urgency is the donor's, not this line's own.
    #: `None` outside `group_borrow`.
    donor_required_date: Optional[date] = None
    #: STEP 3 (`supply_borrow`, S4): WHICH incoming document this quantity comes off, by
    #: ADDRESS - `spo:<allocation id>` or `po:<purchase order line id>`, the assignment's own
    #: event key. Never rendered; it is what the Confirm moves the placement link onto
    #: (PLAN 3.3). `None` on every other rung.
    supply_key: Optional[str] = None
    #: The same document as a person names it - `SPO 202607-S0105`, `PO 202607-P0031 line 3`.
    #: The SERVER's spelling, so the sentence and the Stock Debt drill cannot name one
    #: document two ways.
    supply_document: Optional[str] = None
    #: The day it lands: an SPO's arrival, a PO line's `issue_date + lead time` (R29).
    arrival_date: Optional[date] = None


class SupplySpoRef(BaseModel):
    spo_number: str
    arrival_date: Optional[date] = None
    qty: str


class BorrowDonorImpact(BaseModel):
    free_before: str
    free_after_full_borrow: str
    committed_qty: str


class BorrowCandidate(BaseModel):
    """A donor, with the whole of its own position beside what it could give.

    The captain, on a list that stated free stock alone: "before I decide to borrow, I need
    to know I am not hurting them". Free nets reserved and confirmed holds only, so on this
    book it reads as raw on-hand; the AutoCount triple is what says whether the donor can
    actually spare it (PLAN 13.11).
    """

    source: BorrowSource
    warehouse_code: str
    warehouse_id: str
    donor_project_ref: Optional[str] = None
    donor_project_id: Optional[str] = None
    #: What this donor can give: the location's free stock, or the donor project's own hold.
    free_qty: str
    #: AutoCount's Stock Status columns for the DONOR's (product, location) pile.
    qty_on_hand: Optional[str] = None
    so_qty: Optional[str] = None
    spo_qty: Optional[str] = None
    #: on hand - SO + SPO. SIGNED: a donor the book has oversold says so.
    available_qty: Optional[str] = None
    #: The engine's own pair, so the two arithmetics can be reconciled on screen.
    qty_free: Optional[str] = None
    qty_committed: Optional[str] = None
    #: What this line still has to cover at the borrow rung, and what the donor is left with
    #: once it is met. `available_after_need` is what the list is RANKED on, and is signed.
    need_qty: Optional[str] = None
    available_after_need: Optional[str] = None
    #: First in the ranking - the donor this borrow hurts least. Exactly one row carries it.
    recommended: bool = False
    donor_impact: BorrowDonorImpact
    #: Ladder v2 (section E): `group_borrow` or `cross_group_borrow` when this row is one
    #: of the new group-aware donors, `None` for a plain `other_location`/`other_project`
    #: donor the old ladder already offered.
    rung: Optional[str] = None
    #: The donor sales-order line this row names, for `group_borrow` (section E.4): its
    #: number, line, and the agent who owns it.
    donor_so_number: Optional[str] = None
    donor_line_no: Optional[int] = None
    donor_agent_code: Optional[str] = None
    #: Addressing only, never rendered: `donor_core_line_id` re-identifies the donor's own
    #: sales-order line so the confirmation can re-read its live open quantity at commit
    #: (AC-C03), the same way `warehouse_id` re-identifies a location.
    donor_core_line_id: Optional[str] = None
    #: The donor is ranked BELOW this line by the active priority policy, so this rung
    #: proposes it automatically (section E.4). `False` on a same-agent donor ranked above
    #: this line: offered, never auto-composed.
    lower_ranked: bool = False
    #: The donor shares this line's own sales agent (section 8) - offered at any rank.
    same_agent: bool = False
    #: The donor's own required date (section E.4: "urgency = the donor's required
    #: date"), for a `group_borrow` row - the confirm payload round-trips this into
    #: `ConfirmBorrowComponent.donor_required_date`. `None` on every other rung.
    donor_required_date: Optional[date] = None
    #: S4: the SAME location facts the Grid Location table states for this warehouse.
    #: `None` on a server build that has not wired this yet.
    location: Optional[BoardCellLocation] = None


class SupplyFrozenLine(BaseModel):
    """What the active revision was balanced against, as it was frozen."""

    open_qty: str
    components: List[SupplyComponent] = []
    #: The reason given for overriding the engine's proposal, when one was. Absent on a line
    #: confirmed as proposed, and on any line frozen before the field existed.
    amend_reason: Optional[str] = None
    #: The reason a discontinued product was still bought (AC-B11), when one was given.
    buy_reason: Optional[str] = None
    #: The frozen Buy was an ORDER BACK - a shortfall against something already ordered or
    #: already shipped, not a fresh purchase - and the document CS cited for it, if any
    #: (`PLAN-scm-purchasing-uat-journey.md` section 4b). Read back by the Amend editor so
    #: re-opening a covered line shows what was actually decided.
    order_back: bool = False
    cited_document: Optional[str] = None


class SupplyLadderOption(BaseModel):
    """One step of ladder v7.1, answered whether or not it was taken (R36, AC-S3-14).

    The wire contract is stated once, in
    `app/(protected)/project-sales/_shared/services/fulfilmentPlanningService.ts`
    ("LADDER v7.1: THE OPTIONS CONTRACT"), and this is the sheet's half of it. The board's
    is `schemas/project_board.py::BoardLadderOption`; the two are the same six answers about
    the same walk, kept as siblings because the two payloads already are.
    """

    step: str
    label: str
    whole: bool = False
    #: LADDER V8 (C5, code review round 3 batch 2): how much this step can give. On
    #: `pool_share` it is the share itself - the one figure a reader cannot derive from
    #: `whole` - and on every other step it is what that step would contribute to what is
    #: LEFT after the share. Same field the board's own `BoardLadderOption` carries.
    gives_qty: Optional[str] = None
    #: The step's own sentence, where the quantity alone does not say it ("600 is more than
    #: the 450 BRW can spare"). Null on a step whose label and quantity are the whole answer.
    reason: Optional[str] = None
    #: Null exactly when the step offered nothing, and `days_late` is null with it.
    fulfil_date: Optional[date] = None
    #: Never negative: landing early is on time, not minus six days late.
    days_late: Optional[int] = None
    #: Set on the borrow steps only - `use` draws the free pile and `buy` orders new stock.
    debt_so_number: Optional[str] = None
    debt_month: Optional[str] = None
    chosen: bool = False


class SupplyLine(BaseModel):
    project_line_id: str
    line_no: int
    #: The PLANNING UNIT this line was composed in (ladder v6): this order's lines for the
    #: same item, the same fulfilment location and the same delivery date are ONE quantity,
    #: covered whole from stock or bought whole. The unit's total, and how many lines it is.
    #: `1` and the line's own quantity when it was planned on its own, which is most lines -
    #: the screen says nothing extra for those.
    unit_qty: str = "0"
    unit_line_count: int = 1
    item_code: Optional[str] = None
    #: Addressing only, never rendered (see module docstring) - what the Proof button asks
    #: `GET .../fulfilment-planning/classification` with.
    product_id: Optional[str] = None
    description: Optional[str] = None
    uom: Optional[str] = None
    open_qty: str
    #: S3 (`PLAN-local-supplier-oi-routing.md`): whether the product's supplier sits in the
    #: home country. A `local` Buy raises no Order Inquiry row on confirm. `None` on a
    #: server build that skipped origin resolution.
    buy_origin: Optional[Literal["local", "overseas"]] = None
    required_date: Optional[date] = None
    fulfilment_location: Optional[str] = None
    is_dealer_hot_selling: bool = False
    is_project_hot_selling: bool = False
    #: Classified (a non-null letter exists on that class, at an active location) but not
    #: hot - "Cold at retail" / "Cold at project" in the UI's own words. `False` while the
    #: class is hot too - the hot flag already covers it.
    dealer_classified: bool = False
    project_classified: bool = False
    classification_unavailable: bool = False
    is_discontinued: bool = False
    pool_location: Optional[str] = None
    #: The old reorder-level cap for a hot-selling line. Always null now (19 August 2026):
    #: dealer hot-selling offers the pool nothing and project hot-selling caps it by the
    #: pool's own availability instead. Kept for wire compatibility, never a number again.
    pool_cap: Optional[str] = None
    pool_reorder_level: Optional[str] = None
    #: The pool-share carve-out (LADDER V8, R-C), stated on THIS line the way the board's
    #: cell states it on its locations (B2, fix round 5): `{warehouse_id:
    #: available_for_project}` for every site pool the walk consulted, and the five pools'
    #: own net. Without them the per-order sheet's `lineBlockers` had no allowance to check
    #: a reserve-plus-Buy split against, and refused the engine's own suggestion.
    pool_allowances: Dict[str, str] = {}
    pools_net: Optional[str] = None
    components: List[SupplyComponent] = []
    #: The FIVE options of ladder v7.1 (R36, AC-S3-14), always all five and always in step
    #: order, one chosen at most - the same table the board's contribution carries, because
    #: the decision panel renders it on either surface. Declared here or `response_model`
    #: would drop the field on its way out.
    options: List["SupplyLadderOption"] = []
    timely_spo: List[SupplySpoRef] = []
    advisory_spo: List[SupplySpoRef] = []
    borrow_candidates: List[BorrowCandidate] = []
    frozen: Optional[SupplyFrozenLine] = None
    #: Covered by the order's active revision (13.4). A confirmation covers the lines the
    #: planner chose, so `false` here means EXPLICITLY undecided - the line's whole open
    #: quantity is still demand and still reaches reorder planning. Stated as its own
    #: field rather than left to be inferred from `frozen`, because "nobody decided this"
    #: and "decided, and it needed nothing" are different answers.
    decided: bool = False
    #: Why nothing can be proposed for this line, when nothing can: a mirror line with no
    #: reconciled AutoCount line has no open quantity to promise against. Such a line still
    #: reads on the sheet, with no components and no donors, rather than refusing the whole
    #: read; a plannable line carries `null` here.
    unplannable_reason: Optional[str] = None


class SupplyDecisionOut(BaseModel):
    revision_no: int
    state: SupplyDecisionState
    confirmed_by_name: Optional[str] = None
    confirmed_at: Optional[datetime] = None
    #: Why the active revision no longer matches the order. Present when challenged.
    challenged_reason: Optional[str] = None


class SupplyFailingLine(BaseModel):
    """A line the server will not confirm, named the only way it may be (AC-C02)."""

    line_no: Optional[int] = None
    #: #1362 item 5: AutoCount's own line number, the one the message names.
    so_line_no: Optional[int] = None
    item_code: Optional[str] = None
    reason: str


class SupplyProposal(BaseModel):
    project_sales_order_id: str
    provisional_ref: str
    autocount_doc_no: Optional[str] = None
    #: NULLABLE since adoption (`PLAN-fulfilment-planning-from-autocount-so.md` section 4):
    #: an order adopted from the AutoCount book has no project registration and must not
    #: invent one, so all three project fields are absent on that path. Optional here so
    #: the value is a null rather than the string "None" - which is what a required field
    #: turned it into, and which the screen would have rendered and linked to.
    project_id: Optional[str] = None
    project_code: Optional[str] = None
    project_name: Optional[str] = None
    status: str
    review_state: Optional[str] = None
    decision: Optional[SupplyDecisionOut] = None
    #: "4 of 12 lines decided", per order. Information, never a gate (13.4).
    lines_total: int = 0
    lines_decided: int = 0
    lines: List[SupplyLine] = []
    failing_lines: Optional[List[SupplyFailingLine]] = None


# ------------------------------------------------------------------- the confirmation


class ConfirmReserveComponent(BaseModel):
    warehouse_id: str
    qty: Decimal = Decimal("0")
    #: The warehouse CODE the id names (R4, review round, second re-walk). Optional and
    #: ignored on the way IN - addressing is the id's job, and the endpoint reads nothing
    #: else here - but this SAME schema is reused as the OUTPUT shape for a planning-change
    #: row's stored `composition` (`app/schemas/planning_change.py` imports `ConfirmLine`
    #: from this module), and without this field declared, `response_model` drops the code
    #: `_validate_composition_shape` already wrote into the stored JSON on its way back out
    #: over the wire - a reader of the ROUTE's response never sees it, only a reader of the
    #: row directly off the database would.
    location: Optional[str] = None


class ConfirmBorrowComponent(BaseModel):
    source: BorrowSource
    warehouse_id: str
    #: The warehouse CODE the id names (R4), same reasoning as `ConfirmReserveComponent.
    #: location` - ignored on input, needed on output since this schema is reused for a
    #: planning-change row's stored `composition`.
    location: Optional[str] = None
    donor_project_id: Optional[str] = None
    qty: Decimal = Decimal("0")
    #: Required, but NOT by the schema: an empty reason is a failing LINE, named by line
    #: number and item code like every other refusal, rather than a field-level 422 the
    #: sheet cannot attach to a row (AC-B09, AC-C02).
    reason: str = ""
    #: Ladder v2 group borrow (section E.4): the donor's own sales-order line, addressing
    #: only. Present, this component is checked against that line's LIVE committed
    #: quantity (re-read at commit, AC-C03) rather than against free stock - it is
    #: borrowing what is SOLD there, not what is spare. Absent, this is an ordinary
    #: `other_location` free-stock borrow, unchanged.
    donor_core_line_id: Optional[str] = None
    #: Round-tripped from the proposal/donor row so the order-back note can be built
    #: without a second lookup (`_borrow_shortfalls`). Display only; never validated.
    donor_so_number: Optional[str] = None
    donor_line_no: Optional[int] = None
    donor_agent_code: Optional[str] = None
    same_agent: bool = False
    #: The donor's own required date, for the order-back Order Inquiry row's urgency
    #: (section E.4: "urgency = the donor's required date").
    donor_required_date: Optional[date] = None
    #: STEP 3 (`supply_borrow`, S4): WHICH incoming document this quantity comes off, by
    #: ADDRESS - `spo:<allocation id>` or `po:<purchase order line id>`, the assignment's own
    #: event key. Never rendered; it is what the Confirm moves the placement link onto
    #: (PLAN 3.3). `None` on every other rung.
    supply_key: Optional[str] = None
    #: The same document as a person names it - `SPO 202607-S0105`, `PO 202607-P0031 line 3`.
    #: The SERVER's spelling, so the sentence and the Stock Debt drill cannot name one
    #: document two ways.
    supply_document: Optional[str] = None
    #: The day it lands: an SPO's arrival, a PO line's `issue_date + lead time` (R29).
    arrival_date: Optional[date] = None

    #: LADDER V7.1 STEP 3 IS A BORROW COMPONENT WHETHER OR NOT ANYBODY IS WAITING ON THE
    #: DOCUMENT, and that is a deviation from PLAN 3.2's "kind `reserve` when the document
    #: is free", recorded here because this schema is why. A Reserve component is
    #: `(warehouse_id, qty)` and nothing else - it is a hold on STOCK at a bin - so a free
    #: document posted as one would be re-checked against on-hand capacity at a bin that
    #: does not hold it and refused. The free-ness is said where it belongs: no donor is
    #: named, no order-back is raised, and the sentence reads "Take" rather than "Borrow".


class ConfirmLine(BaseModel):
    project_line_id: str
    timely_spo_qty: Decimal = Decimal("0")
    reserve: List[ConfirmReserveComponent] = Field(default_factory=list)
    borrow: List[ConfirmBorrowComponent] = Field(default_factory=list)
    buy_qty: Decimal = Decimal("0")
    buy_reason: Optional[str] = None
    #: This Buy is an ORDER BACK (part 2 section 4b, captain 25 Aug): the quantity is owed
    #: against something already ordered or already shipped rather than being a new
    #: purchase, so the row purchasing gets carries verb `ORDER_BACK` and is the one row
    #: whose links may name an `spo_allocations` row.
    #:
    #: Meaningless without a Buy, and ignored when `buy_qty` is 0: an order back with
    #: nothing bought is not an instruction.
    order_back: bool = False
    #: The document CS named for it - "202604-S0083", "SPO-2026/08-0061" - as they spelled
    #: it. Not a link by itself: it is what the auto-link walk tries FIRST, before any
    #: location tier or date, and a document this system does not hold is recorded rather
    #: than refused (AC-J2).
    cited_document: Optional[str] = None
    #: Why this composition is not the one the engine proposed, in the planner's own words.
    #: Absent when they took the proposal as it stood: demanding a reason for agreeing is how
    #: a mandatory field becomes a rubber stamp. It is FROZEN with the line, beside the
    #: engine's own sentences, because those explain a decision nobody took once it is amended.
    amend_reason: Optional[str] = None
    #: "This might be a system problem, flag it for investigation" (R10,
    #: `PLAN-scm-planning-inline-decisions.md` section 3.D5).
    #:
    #: A SECOND answer, beside the verdict rather than instead of it: a planner who amends a
    #: line because the availability printed next to it reads wrong is telling us two
    #: different things, and a decision that recorded only the amendment lost the one worth
    #: chasing. It travels with whichever verdict was given - approved, amended or rejected -
    #: and is frozen beside `amend_reason` so the warning is still on the pill after a reload.
    suspected_system_issue: bool = False


class ConfirmSupplyBody(BaseModel):
    lines: List[ConfirmLine] = Field(default_factory=list)
    #: The date the planner is deciding AS OF, which the board already has as a dial. It is
    #: read for ONE purpose: the engine proposal frozen beside the decision (AC-D1) is walked
    #: against the same day the planner saw, so a board opened on a Friday and confirmed on
    #: the Monday does not record a suggestion nobody was ever shown. It does NOT move the
    #: decision itself - live stock, the queue and every refusal are judged against now, and a
    #: back-dated confirmation of stock that has since gone would be a promise nobody can keep.
    #: Absent means today, which is what every caller sends today.
    as_of: Optional[date] = None
    #: The planning-change batch this Confirm is ANSWERING (part 3, AC-P3-4).
    #:
    #: Set when the board was opened at `?orders=...&batch=<id>`: the lines above become
    #: those batch rows' own compositions, the batch is applied, and exactly one revision
    #: is written - one press, one call. Absent on every ordinary board Confirm. A second
    #: Confirm naming a batch already applied is refused with a message rather than
    #: writing a second revision.
    batch_id: Optional[str] = None
    #: The mirror `project_line_id`s of COVERED lines a `rejected` draft was staged on
    #: (owner ruling 23 Sep 2026, `PLAN-board-reject-on-confirmed-line.md`: "we should
    #: confirm the rejection" - reject on a confirmed line is a STAGED decision like every
    #: other board decision, and Confirm is what commits it, never the draft save itself).
    #: Never overlaps `lines` above - a line is either being REPLACED (named in `lines`) or
    #: DROPPED (named here), and the route refuses a line named in both. Absent on every
    #: ordinary Confirm; a batch Confirm (`batch_id` set) may not carry this - the two are
    #: refused together (422), because a pending planning change has no shape for a
    #: withdrawal alongside it.
    #:
    #: DEDUPED, not refused (nit, fix round, review): the route drops a repeated id
    #: rather than 422ing over it - a line named twice is one withdrawal either way, and
    #: nothing about a duplicate is a stale-client signal worth refusing over the way a
    #: line named in BOTH `lines` and here is.
    rejected_line_ids: List[str] = Field(default_factory=list)


class ConfirmException(BaseModel):
    """An outcome purchasing has to answer, not a refusal of the confirmation."""

    line_no: Optional[int] = None
    item_code: Optional[str] = None
    message: str


class ConfirmedLine(BaseModel):
    project_line_id: str
    line_no: Optional[int] = None
    item_code: Optional[str] = None


class ConfirmResult(BaseModel):
    #: None only when every named line was already fulfilled and nothing was written
    #: (#1362 item 3).
    revision_no: Optional[int] = None
    confirmed_at: Optional[datetime] = None
    review_state: str = "confirmed"
    inquiry_rows_created: int = 0
    exceptions: List[ConfirmException] = []
    #: What this revision covers, and what is still open on the order after it (13.4).
    #: `lines_undecided > 0` is a normal, deliberate outcome, not a warning.
    lines_decided: int = 0
    lines_undecided: int = 0
    #: The lines this press froze (payload order) and how many covered lines were carried
    #: forward untouched. `lines_decided == len(lines_confirmed) + lines_carried`.
    lines_confirmed: List[ConfirmedLine] = []
    lines_carried: int = 0
    #: The covered lines this press took OUT of the confirmation (staged rejections).
    lines_withdrawn: List[ConfirmedLine] = []
    #: The physical movements this confirmation raised, and how many it could NOT write
    #: (`PLAN-scm-cs-planning-uat.md` section E). The transfer write is best-effort so a
    #: failure cannot fail a promise already made, but a movement nobody was told about is
    #: a movement nobody makes - so the count reaches the screen rather than a server log.
    transfers_written: int = 0
    transfers_failed: int = 0
    #: And how many open movements this revision KEPT rather than re-raising (R16): the same
    #: instruction at the same quantity survives a reconfirm with its state and its approval
    #: intact. Counted apart from `transfers_written`, which is only rows actually created,
    #: so "nothing new had to move" is not read as "nothing moved".
    transfers_kept: int = 0
    #: How many of this revision's lines the planner flagged as a suspected system problem
    #: (R10). Reported rather than logged: the flag is a request to look at something, and a
    #: request nobody is told about is a request nobody answers.
    suspected_issues: int = 0
    #: How many covered lines this SAME press withdrew (`ConfirmSupplyBody.rejected_line_ids`,
    #: owner ruling 23 Sep 2026). The toast needs it beside `lines_decided` - "N confirmed"
    #: says nothing about the lines this press also took OUT, and `response_model` drops an
    #: undeclared field silently, so it has to be named here rather than left in the
    #: in-process dict the way `settled_in_place`/`auto_place_products` are.
    rejected_count: int = 0
    #: Named lines skipped because nothing was open on them any more, their saved
    #: decision cleared (#1362 item 3). The toast says "1 line already fulfilled,
    #: decision cleared".
    lines_fulfilled_skipped: int = 0
    #: #1362 (owner, 29 Sep 2026): Buys confirmed as decided over goods that landed for
    #: their line. The link stays; purchasing adjusts it off the Buy's inquiry row.
    landed_buy_notices: Optional[List[SupplyFailingLine]] = None


# ------------------------------------------------------------------- the Plans page (D1)


class PlanRow(BaseModel):
    """One row of `GET /project-sales/plans`: one supply decision revision, cross-order.

    Addressing is by `project_sales_order_id` (the SO sheet) and `sales_order_id` (the SCM
    sales order), never rendered - the same "codes, not ids" rule the rest of this file
    follows, with `so_number` and the agent CODE as what actually prints.
    """

    project_sales_order_id: str
    sales_order_id: Optional[str] = None
    project_id: Optional[str] = None
    so_number: Optional[str] = None
    customer_name: Optional[str] = None
    agent_code: Optional[str] = None
    agent_label: Optional[str] = None
    revision_no: int
    state: SupplyDecisionState
    decided_by_name: Optional[str] = None
    decided_at: Optional[datetime] = None
    #: How many lines this revision covers. Never the sales order's whole line count - a
    #: revision may cover a chosen subset (13.4) and this is that subset's size.
    line_count: int = 0
    #: "Reserve 213 · Buy 145", summed across every line the revision covers. `None` on a
    #: revision that covers nothing (should not happen, but a summary is never invented).
    components_summary: Optional[str] = None
    #: Why the active revision no longer matches the order. Present only when `state` is
    #: `challenged`.
    challenged_reason: Optional[str] = None


# --------------------------------------------------------- confirm all approved (D3)


class ConfirmManyOrderBody(BaseModel):
    """One order's half of `POST .../fulfilment-planning/confirm-all`. Same shape as
    `ConfirmSupplyBody.lines`, named so a batch of them addresses each order by id."""

    pso_id: str
    lines: List[ConfirmLine] = Field(default_factory=list)
    #: This order's OWN planning-change batch (`PLAN-scm-board-picks-up-pending-change.md`,
    #: AC-B5/AC-B6): a board can now show two orders on two different batches, so the batch
    #: an order answers has to travel WITH that order, not once for the whole press. Falls
    #: back to `ConfirmManyBody.batch_id` when absent, so the pre-slice shape (one
    #: body-level id applied to every order) keeps working during the deploy window.
    batch_id: Optional[str] = None
    #: This order's own half of `ConfirmSupplyBody.rejected_line_ids` - same rule, same
    #: refusal alongside a batch (owner ruling 23 Sep 2026,
    #: `PLAN-board-reject-on-confirmed-line.md`).
    rejected_line_ids: List[str] = Field(default_factory=list)
    #: Scope this order's press to exactly these mirror line ids: a line outside the list is
    #: neither confirmed, held back nor echoed, and a staged rejection outside it is left
    #: alone. What a Preview showed is what Confirm sends.
    only_line_ids: Optional[List[str]] = None


class ConfirmManyBody(BaseModel):
    orders: List[ConfirmManyOrderBody] = Field(default_factory=list)
    #: The planning-change batch this press is ANSWERING (part 3, AC-P3-4).
    #:
    #: DEPRECATED as the primary shape (`PLAN-scm-board-picks-up-pending-change.md`, change
    #: 4): a board with two orders on two different batches cannot name one body-level id
    #: for both. `ConfirmManyOrderBody.batch_id` is read first; this is the FALLBACK for a
    #: caller that has not moved to the per-order id yet - one batch per board, which was
    #: the only shape the board had before this slice: opened at `?orders=...&batch=<id>`,
    #: every order on it belonging to that one batch. Absent on every ordinary board Confirm.
    batch_id: Optional[str] = None
    #: Run every order exactly as a real press and roll it back instead of committing; each
    #: ok order then answers with the order inquiry rows and transfers it would raise.
    preview: bool = False


class PreviewInquiryRow(BaseModel):
    line_no: Optional[int] = None
    item_code: Optional[str] = None
    verb: str
    #: True when this press raised the row; False when it only settles a row an earlier
    #: revision already raised.
    is_new: bool = True
    qty: Decimal
    delivery_date: Optional[date] = None
    stock_location: Optional[str] = None
    note: Optional[str] = None


class PreviewTransfer(BaseModel):
    line_no: Optional[int] = None
    is_new: bool = True
    kind: str
    qty: Decimal
    from_location: Optional[str] = None
    to_location: Optional[str] = None


class ConfirmManyOrderResult(BaseModel):
    """One order's outcome. `ok` decides which of the two halves is populated - never both,
    and never neither: a result the caller cannot read as success-or-failure is worse than
    a slower reply."""

    pso_id: str
    ok: bool
    #: True on a preview: nothing below was committed.
    preview: Optional[bool] = None
    inquiry_rows: Optional[List[PreviewInquiryRow]] = None
    transfers: Optional[List[PreviewTransfer]] = None
    decision_revision: Optional[int] = None
    inquiry_rows_created: Optional[int] = None
    lines_decided: Optional[int] = None
    lines_undecided: Optional[int] = None
    lines_confirmed: Optional[List[ConfirmedLine]] = None
    lines_carried: Optional[int] = None
    lines_withdrawn: Optional[List[ConfirmedLine]] = None
    #: The movements this order's confirmation raised, and how many could NOT be written -
    #: the same pair the single-order `ConfirmResult` carries. The board's toast reads "N
    #: lines confirmed, T transfers proposed", and T comes from here.
    transfers_written: Optional[int] = None
    transfers_failed: Optional[int] = None
    #: The open movements this order's confirmation kept as they were (R16).
    transfers_kept: Optional[int] = None
    #: The lines flagged as a suspected system problem, summed the same way (R10).
    suspected_issues: Optional[int] = None
    #: How many covered lines this order's own press withdrew, the per-order twin of
    #: `ConfirmResult.rejected_count` (owner ruling 23 Sep 2026).
    rejected_count: Optional[int] = None
    #: The per-order twin of `ConfirmResult.lines_fulfilled_skipped` (#1362 item 3).
    lines_fulfilled_skipped: Optional[int] = None
    #: #1362 hold-back: lines the recheck refused, left out so the rest of the order
    #: confirmed. Each keeps its saved decision; named the way a refusal names a line.
    lines_held_back: Optional[List[SupplyFailingLine]] = None
    #: The per-order twin of `ConfirmResult.landed_buy_notices` (#1362).
    landed_buy_notices: Optional[List[SupplyFailingLine]] = None
    error: Optional[str] = None
    #: The lines the server refused, named the way `SupplyFailingLine` always is (AC-C02),
    #: when the refusal named any.
    failing_lines: Optional[List[SupplyFailingLine]] = None


class ConfirmManyResult(BaseModel):
    """`POST .../fulfilment-planning/confirm-all`. One result per order named in the body,
    in the SAME order - a caller matching by index never has to search."""

    results: List[ConfirmManyOrderResult] = []


class ClassificationEvidenceLocation(BaseModel):
    """One location's contribution to a demand class's ranking - the "proof" line."""

    warehouse_code: str
    qty_delivered: str
    rank: int
    of: int
    #: This row's OWN share of the class's total quantity - qty / sum(qty) over the whole
    #: window. "Its share" in the popover; kept separate from `cumulative_share_pct` so a
    #: thin ranking's high cumulative figure is never read as this row's own weight (captain,
    #: 19 Aug 2026: "Share: top 93.6%" read as good on a row that held almost nothing).
    share_pct: Optional[float] = None
    #: The running share INCLUDING this row - "Ranked above it" in the popover is this minus
    #: `share_pct`.
    cumulative_share_pct: Optional[float] = None
    #: A/B/C, or absent when the class ranking never reached this row (should not happen -
    #: every ranked row carries a letter).
    letter: Optional[str] = None
    hot: bool = False


class ClassificationEvidenceClass(BaseModel):
    demand_class: Literal["retail", "project"]
    #: The word the captain asked for instead of the demand-class code: "Dealer" (retail
    #: customers) or "Project".
    label: str
    verdict: Literal["hot", "cold", "unclassified"]
    locations: List[ClassificationEvidenceLocation] = []


class ClassificationEvidence(BaseModel):
    """`GET .../fulfilment-planning/classification` - the proof behind a hot/cold chip."""

    product_id: str
    item_code: Optional[str] = None
    computed_at: Optional[datetime] = None
    window_days: int = 365
    hot_cut_pct: float
    classes: List[ClassificationEvidenceClass] = []
