"""Mapping a published Project SO onto the AutoCount sales order it became (Stage 1B).

Contract: `documentation/plans/scm/STAGE1B-scm-front-planning-reconciliation.md` sections 2
to 4, `PLAN-scm-front-planning.md` sections 3.1 and 4, UAC AC-A01 to AC-A04.

The header half already exists: `ProjectSOIngestService.reconcile_core_order` adopts the
AutoCount number and points `projects.sales_orders.so_id` at the core row. This file adds
the LINE half - which core `sales_order_lines` row each Project line became - and the one
whole-SO review state that follows from it.

## The mapping rule, and why it is two passes

Candidates are the core lines of `so_id` that are not closed AND are not already held by
another Project SO's line. A Project line whose existing `core_sales_order_line_id` still
names one of them KEEPS it, on candidacy alone: reconciliation runs again every time somebody
presses Re-run, and a mapping that reshuffles itself under a person who has already read it is
worse than one that occasionally reports an exception.

The rest are matched inside each `product_id` group, in the same two passes
`app/services/scm/outstanding_diff.py` uses to identify a line across weekly uploads:

1. **exact required-date match**, and ONLY when exactly one Project line and one core line
   share that date. Two of either is a genuine "which of these is which", so both sides are
   reported ambiguous and NOTHING is written - a guess here links a customer's quantity to
   the wrong delivery date.
2. **whatever is left pairs in date order**, both sides sorted by date and zipped. That is
   the rule a person can check by eye: "you had lines due 1 Jul and 3 Aug, the document has
   15 Jul and 3 Aug, so the 1 Jul line moved."

Leftover Project lines are `missing`, or `duplicate` when the core line for that item is held
by another Project SO (two sales orders adopted the same AutoCount document, and one of them is
wrong - `uq_projects_so_line_core_line` allows exactly one holder, so reporting this is the
only alternative to dying on the index). Leftover core lines are `surplus` (the document has a
line this sales order does not, which is answered in AutoCount Differences).

## What this file must never do (AC-A04)

Reconciling writes core-line links and nothing else. It creates no purchase requirement and
no order inquiry row: purchasing demand is created inside the atomic SO confirmation, from
the confirmed Buy residual alone (`ProjectOrderInquiryService.refresh_for_decision`, the
only writer of a standard demand row). What reconciling DOES do to a confirmed order is
retire its decision - a re-mapped line supersedes the active revision and drifted facts
challenge it (AC-C06) - so the SO returns to Needs CS review rather than staying confirmed
against a mapping that has moved.

## No UUID in a message

Every exception names a Project line number and an item code, or an item code alone for a
surplus core line, and the `message` carries the REASON only - the screen prints the subject
itself ("Line 2, SRT501-CP"), so a message repeating it renders the same fact twice.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy import case, func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.order import Customer, SalesOrder, SalesOrderLine
from app.models.product import Product
from app.models.sales_agent import SalesAgent
from app.models.project_so import (
    AUTHORED_LIVE_STATUSES,
    LIVE_SO_STATUSES,
    SO_STATUS_ADOPTED,
    ProjectSalesOrder,
    ProjectSalesOrderLine,
)
from app.models.projects import Project, ProjectParty, ProjectPurchaseOrder
from app.services import planning_change_service
from app.services.error_handler import AppException
from app.services.scm.demand import PROJECT_CLASS, demand_qty, is_open_demand

# Per-line outcomes (slice note section 2). `linked` is the only one that writes.
LINK_LINKED = "linked"
LINK_MISSING = "missing"
LINK_AMBIGUOUS = "ambiguous"
#: The core line this Project line would take is already held by ANOTHER Project SO.
#: A distinct outcome from `missing` because the answer is somewhere else entirely: one
#: of the two sales orders adopted the wrong AutoCount document.
LINK_DUPLICATE = "duplicate"

# Header outcomes.
HEADER_NO_DOCUMENT = "no_document"
HEADER_NO_CORE_SO = "no_core_so"
HEADER_LINKED = "linked"
#: The planning record WAS the core sales order (plan section 5.2). There is no separately
#: authored document to disagree with, so reconciliation is a one-way SYNC rather than a
#: diff and this outcome is what the card says instead of "linked".
HEADER_ADOPTED = "adopted"

# Exception kinds. `surplus` is the only one with no Project line behind it.
KIND_HEADER = "header"
KIND_MISSING = "missing"
KIND_AMBIGUOUS = "ambiguous"
KIND_DUPLICATE = "duplicate"
KIND_SURPLUS = "surplus"

#: A per-line outcome is its own exception kind; `linked` raises none.
_KIND_OF_LINK = {
    LINK_MISSING: KIND_MISSING,
    LINK_AMBIGUOUS: KIND_AMBIGUOUS,
    LINK_DUPLICATE: KIND_DUPLICATE,
}

# The whole SO's one state (AC-A03). No line ever carries a state of its own, and there is
# no fourth value: a superseded or challenged decision reads `needs_cs_review` again.
#: `not_started` belongs to a subject with NO planning record at all (AC-FP01), so no
#: `_OrderOutcome` ever carries it: the worklist writes it for an outstanding core sales
#: order nobody has adopted yet.
REVIEW_NOT_STARTED = "not_started"
REVIEW_AWAITING = "awaiting_reconciliation"
REVIEW_NEEDS_CS = "needs_cs_review"
#: Stage 1C. The Stage 1B conditions hold AND an active supply decision exists.
REVIEW_CONFIRMED = "confirmed"

#: A core line in this state is not a candidate, and a link naming one is stale. Same word
#: `project_so_ingest_service` retires a superseded row with.
CORE_LINE_CLOSED = "closed"

#: The header half of "outstanding" (plan section 3), beside `is_open_demand()`'s line half.
#: It is what keeps a retired provisional row out of a planning screen.
_CORE_SO_OPEN = "open"

#: Which arm of the union a row came from. ADDRESSING ONLY, never rendered: the screen tells
#: the two apart by what they carry, not by a code.
ROW_KIND_SALES_ORDER = "sales_order"
ROW_KIND_PLANNING_RECORD = "planning_record"

#: Where the planning record came from. `adopted` is the AutoCount book, `authored` is a
#: document somebody wrote here. Absent on a row with no planning record at all.
ORIGIN_ADOPTED = "adopted"
ORIGIN_AUTHORED = "authored"

#: Every column the worklist renders is sortable (captain, 18 August 2026), and this is the
#: CLOSED SET. An unknown value is a 422 at the route rather than a silent fall back to the
#: default: a grid drawing a sort arrow on a column the server ignored is a screen lying
#: about what it is showing, which is worse than a refusal a person can see.
#:
#: The route declares the same names as a `Literal` (FastAPI cannot build one from a runtime
#: set), and `test_the_route_and_the_service_agree_on_the_sortable_set` is what stops the
#: two drifting.
SORTABLE_FIELDS = frozenset(
    {
        "so_number",
        "customer_name",
        "project_label",
        "earliest_required_date",
        "outstanding_qty",
        "line_count",
        "review_state",
        "provisional_ref",
        "po_number",
        "area_group",
        "updated_at",
    }
)

#: Earliest still-owed required date, because that is the order the work is due in
#: (AC-FP04). Sorting is an override of this, never a replacement for having one.
DEFAULT_SORT_FIELD = "earliest_required_date"

#: Sorting on one of these needs the DERIVED review state, so the outcomes have to be
#: computed for every planning record in the result rather than for the page alone.
_SORT_NEEDS_OUTCOMES = frozenset({"review_state", "line_count"})

#: Sorting on one of these needs the joined project / customer / PO strip, so
#: `_header_rows` runs over every planning record in the result rather than the page.
_SORT_NEEDS_HEADERS = frozenset({"customer_name", "project_label", "po_number"})

#: The order the work MOVES in, which is the only order these four pills mean anything in.
#: Alphabetically they read awaiting, confirmed, needs, not started - which is no order at
#: all to the person looking at the column.
_REVIEW_STATE_RANK = {
    REVIEW_NOT_STARTED: 0,
    REVIEW_AWAITING: 1,
    REVIEW_NEEDS_CS: 2,
    REVIEW_CONFIRMED: 3,
}

#: What the Order Inquiry sheet prefixes the project string on the core order with
#: (`project_order_inquiry_import_service`). Stripped for display: the machine's word for
#: where a fact came from is not part of the fact.
_PROJECT_NOTE_PREFIX = "Order Inquiry project:"

# `LIVE_SO_STATUSES` is imported from `app.models.project_so` above rather than restated
# here. It is now `(published, amended, adopted)`, and it lives on the model so the fifteen
# sites that branch on the status pair read ONE tuple instead of fifteen copies of it (plan
# section 4). A site that means "a document this system authored" reads
# `AUTHORED_LIVE_STATUSES` instead, and the name is the answer to "why is adopted not here".

#: Who holds a core line: the Project SO's id, its provisional reference, and the line
#: number on it. The reference and the line number are what an exception names.
_Claim = Tuple[str, Optional[str], Optional[int]]

_SURPLUS_MESSAGE = (
    "On the AutoCount document and not on this sales order. Answer it in AutoCount "
    "Differences."
)


def _duplicate_reason(other_ref: Optional[str], other_line_no: Optional[int]) -> str:
    """Name the sales order holding the core line, so CS knows where to look.

    A reference, never an id: the two orders are told apart on screen by their
    provisional reference, which is what the person reading this has in front of them.
    """
    where = other_ref or "another sales order"
    if other_line_no is None:
        return f"This core line is already linked to Project SO {where}."
    return f"This core line is already linked to Project SO {where}, line {other_line_no}."


def _project_string(internal_note: Optional[str]) -> Optional[str]:
    """The project the Order Inquiry sheet named, without the prefix it wrote it under."""
    text_value = (internal_note or "").strip()
    if not text_value:
        return None
    if text_value.startswith(_PROJECT_NOTE_PREFIX):
        text_value = text_value[len(_PROJECT_NOTE_PREFIX) :].strip()
    return text_value or None


def _origin_of(record: Optional[ProjectSalesOrder]) -> Optional[str]:
    if record is None:
        return None
    return ORIGIN_ADOPTED if record.status == SO_STATUS_ADOPTED else ORIGIN_AUTHORED


def _folded(value: Optional[str]) -> Optional[str]:
    """A text sort key a person would recognise: case-folded, and blank counts as absent.

    An empty string is not a value somebody typed, so it sorts with the nulls rather than
    ahead of every real name.
    """
    text_value = (value or "").strip()
    return text_value.casefold() if text_value else None


def _worklist_tiebreak(row: Dict[str, Any]) -> tuple:
    """How two rows that tie on the sorted field are separated. TOTAL, and ALWAYS ASCENDING.

    The human key first - the sales-order number, else the AutoCount document number, else
    our own reference, which is the same fallback chain the screen names the row by - then
    the row's own identity, so two subjects that somehow shared a reference still have an
    answer. A non-total order is what puts one row on two pages of a paged list and another
    on none, and it is invisible until somebody counts.

    Ascending whichever way the primary sort runs, deliberately: "which of these equal rows
    comes first" should have ONE answer on this screen rather than an answer that flips
    when somebody reverses an unrelated column.
    """
    record: Optional[ProjectSalesOrder] = row.get("record")
    reference = (
        row.get("so_number")
        or (record.autocount_doc_no if record is not None else None)
        or (record.provisional_ref if record is not None else None)
        or ""
    )
    return (str(reference), str(row.get("id") or row.get("sales_order_id") or ""))


def _date_phrase(value: Optional[date]) -> str:
    return f"on {value.strftime('%d %b %Y')}" if value else "with no required date"


def _project_sort_key(line: ProjectSalesOrderLine) -> tuple:
    """Date order, then line number. `None` dates sort last without comparing None."""
    return (
        line.delivery_date is None,
        line.delivery_date or date.min,
        line.line_no or 0,
        str(line.id),
    )


def _core_sort_key(line: SalesOrderLine) -> tuple:
    return (
        line.required_date is None,
        line.required_date or date.min,
        str(line.id),
    )


@dataclass
class _LineOutcome:
    """One Project line's verdict, and the link `reconcile` would write for it."""

    line: ProjectSalesOrderLine
    link: str
    core_line_id: Optional[str]
    candidate_count: int
    reason: str
    #: The product code, resolved once for the whole page. Carried here because every
    #: exception names its line by number AND item code, never by an id.
    item_code: Optional[str] = None


@dataclass
class _OrderOutcome:
    """Everything derived for one Project SO, before display fields are added.

    `review_states_for` reads this directly; `evaluate` and `reconcile` add the header
    strip (project, customer, PO) around it.
    """

    order: ProjectSalesOrder
    header_outcome: str
    header_reason: str
    core_so_number: Optional[str]
    lines: List[_LineOutcome] = field(default_factory=list)
    surplus: List[Tuple[Optional[str], str]] = field(default_factory=list)
    #: Whether an ACTIVE `projects.so_supply_decisions` row covers this order (Stage 1C).
    #: Read once per batch, so a page of fifty rows costs one more query, not fifty.
    has_active_decision: bool = False

    @property
    def lines_total(self) -> int:
        return len(self.lines)

    @property
    def lines_linked(self) -> int:
        return sum(1 for row in self.lines if row.link == LINK_LINKED)

    @property
    def review_state(self) -> Optional[str]:
        """AC-A02/AC-A03: entered only when the header AND every line map cleanly.

        None on an order that is not published or amended. A draft, a blocked order or
        one still being costed is reconciled against nothing - there is no AutoCount
        document for it to disagree with - so it carries NO state rather than an
        "awaiting reconciliation" it has not earned, and the screen renders no pill.

        `confirmed` sits on top of the same conditions rather than replacing them: an
        order whose mapping has since broken is back to awaiting reconciliation whatever
        its decision says, and a superseded or challenged decision is not an active one,
        so the order reads Needs CS review again (AC-C06).

        An ADOPTED order never reads awaiting_reconciliation (AC-FP14): it IS the core
        sales order, so there is nothing to wait for. It is reviewable the moment it is
        adopted, and drops back only when a mirror line's core link goes away.
        """
        if self.order.status not in LIVE_SO_STATUSES:
            return None
        if self.header_outcome == HEADER_ADOPTED:
            if self.lines_total == 0 or self.lines_linked != self.lines_total:
                return REVIEW_AWAITING
            return REVIEW_CONFIRMED if self.has_active_decision else REVIEW_NEEDS_CS
        if self.header_outcome != HEADER_LINKED:
            return REVIEW_AWAITING
        if self.lines_total == 0 or self.lines_linked != self.lines_total:
            return REVIEW_AWAITING
        if self.surplus:
            return REVIEW_AWAITING
        return REVIEW_CONFIRMED if self.has_active_decision else REVIEW_NEEDS_CS

    @property
    def exceptions(self) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        # `adopted` is a clean header, not a problem to answer: the planning record IS the
        # core sales order, so raising a header exception for it would put a permanent
        # "unresolved" on a screen with nothing to resolve.
        if self.header_outcome not in (HEADER_LINKED, HEADER_ADOPTED):
            rows.append(
                {
                    "line_no": None,
                    "item_code": None,
                    "kind": KIND_HEADER,
                    "message": self._header_next_step(),
                }
            )
        for row in self.lines:
            if row.link == LINK_LINKED:
                continue
            rows.append(
                {
                    "line_no": row.line.line_no,
                    "item_code": row.item_code,
                    "kind": _KIND_OF_LINK[row.link],
                    "message": row.reason,
                }
            )
        for _core_id, item_code in self.surplus:
            rows.append(
                {
                    "line_no": None,
                    "item_code": item_code or None,
                    "kind": KIND_SURPLUS,
                    "message": _SURPLUS_MESSAGE,
                }
            )
        return rows

    def _header_next_step(self) -> str:
        """The reason, plus where the person goes to answer it. Never an id."""
        if self.header_outcome == HEADER_NO_DOCUMENT:
            return f"{self.header_reason} Upload it on the AutoCount screen for this order."
        if self.order.so_id:
            # The link exists and its row is simply out of this company's reach. There
            # is nothing to re-run, so the reason stands on its own.
            return self.header_reason
        return (
            f"{self.header_reason} Re-run this once the weekly outstanding sales order "
            "book carries it."
        )


class ProjectSOReconciliationService:
    """Reads the mapping (`evaluate`), writes it (`reconcile`), summarises it in bulk."""

    def __init__(self, db: Session):
        self.db = db

    # ------------------------------------------------------------------ reads

    def evaluate(self, order: ProjectSalesOrder) -> Dict[str, Any]:
        """What the mapping makes of this order right now. Writes nothing."""
        outcome = self._outcomes_for([order])[str(order.id)]
        return self._summary(outcome)

    def review_states_for(self, order_ids: Sequence[str]) -> Dict[str, Dict[str, Any]]:
        """The list row's three numbers, for a whole page of orders at once.

        The same pairing the sheet shows, so a row and the sheet it opens can never
        disagree - and batched, so a 50-row page is a handful of queries rather than 50
        evaluations.
        """
        ids = [str(order_id) for order_id in order_ids if order_id]
        if not ids:
            return {}
        # Only a published or amended order has a state at all: a draft is reconciled
        # against nothing, so it carries no review state rather than an "awaiting
        # reconciliation" it has not earned (AC-A03). Absent from this map means absent
        # from the row.
        orders = (
            self.db.query(ProjectSalesOrder)
            .filter(
                ProjectSalesOrder.id.in_(ids),
                ProjectSalesOrder.status.in_(LIVE_SO_STATUSES),
            )
            .all()
        )
        outcomes = self._outcomes_for(orders)
        return {
            order_id: {
                "review_state": outcome.review_state,
                "lines_total": outcome.lines_total,
                "lines_linked": outcome.lines_linked,
                "exception_count": len(outcome.exceptions),
            }
            for order_id, outcome in outcomes.items()
        }

    # ----------------------------------------------------------------- writes

    def reconcile(self, order: ProjectSalesOrder) -> Dict[str, Any]:
        """Link what can be proven, clear what went stale, and say what is left.

        Idempotent: a second run keeps the links the first one wrote (they are candidates
        again, and an existing link is kept before anything is re-matched) and answers with
        the same summary.

        The header link is attempted first when the document number is known but nothing
        has carried it yet - the outstanding SO book may have created the row since the
        upload, and asking CS to re-upload the same document to pick it up would be a step
        the system can take itself.

        An order that is not published or amended is READ and nothing else: it carries no
        review state (AC-A03), and linking its lines to a core sales order it has not been
        published against would be the system deciding something on its own.

        An ADOPTED order runs the one sync it actually has HERE - `mirror_missing_lines`
        (AC-FP12), the same additive re-sync `ProjectSOAdoptionService.adopt`'s own
        already-adopted branch runs - rather than the two-pass authored mapping below:
        running that over mirror lines that already carry their core link would be
        answering a question nobody asked. Measured live (attempt 7 browser walk,
        SO419851): an AutoCount re-ingest closes the core lines an adopted order's mirror
        already points at and inserts brand-new open ones nobody mirrors, and pressing
        Re-sync used to leave `lines_linked` and the exception list untouched - the board's
        own notice names Re-sync as the remedy, so this is what makes that name true.
        """
        if order.status == SO_STATUS_ADOPTED:
            from app.services.project_so_adoption_service import ProjectSOAdoptionService

            ProjectSOAdoptionService(self.db).mirror_missing_lines(order)
            self.db.flush()
            return self.evaluate(order)
        if order.status not in LIVE_SO_STATUSES:
            return self.evaluate(order)
        if order.so_id is None and order.autocount_doc_no:
            # Imported here rather than at module scope: the ingest service calls back into
            # this one at the end of an upload, and a top-level import both ways is a cycle.
            from app.services.project_so_ingest_service import ProjectSOIngestService

            ProjectSOIngestService(self.db).reconcile_core_order(
                order, str(order.autocount_doc_no)
            )
            self.db.flush()

        outcome = self._outcomes_for([order])[str(order.id)]
        self._persist(outcome)
        return self._summary(outcome)

    def _persist(self, outcome: _OrderOutcome) -> None:
        """Clear first, then link.

        Two flushes on purpose: `uq_projects_so_line_core_line` is a unique index, so two
        lines swapping core lines inside one flush would collide on the statement that
        writes the first of them.

        A re-mapping is a material change (AC-C06): an active revision was decided against
        the links that stood then, so if any of them moves it is superseded and the whole
        SO goes back to Needs CS review. When the links stand but the facts behind them
        have drifted - a quantity or a required date on the core line - that is no longer
        read as a signal of its own here (Slice E, one signal): the change batch a re-run
        or manual edit raises is the only thing that supersedes an active revision now.
        """
        relinked = [
            row
            for row in outcome.lines
            if (
                str(row.line.core_sales_order_line_id)
                if row.line.core_sales_order_line_id
                else None
            )
            != row.core_line_id
        ]
        cleared = False
        for row in outcome.lines:
            current = (
                str(row.line.core_sales_order_line_id)
                if row.line.core_sales_order_line_id
                else None
            )
            if current is not None and current != row.core_line_id:
                row.line.core_sales_order_line_id = None
                cleared = True
        if cleared:
            self.db.flush()

        for row in outcome.lines:
            if row.core_line_id and not row.line.core_sales_order_line_id:
                row.line.core_sales_order_line_id = row.core_line_id
        try:
            self.db.flush()
        except IntegrityError as exc:
            # Belt and braces behind the `duplicate` outcome: a core line held by another
            # Project SO is kept out of the pool, so this can only fire on a race between
            # two reconciliations. Answered as a conflict naming the document, never an id.
            raise AppException(
                409,
                "Another sales order linked a line of AutoCount document "
                f"{outcome.order.autocount_doc_no or '(none)'} while this one was being "
                "reconciled. Re-run the reconciliation to see which line it took.",
            ) from exc

        if relinked:
            from app.services.project_supply_service import ProjectSupplyService

            ProjectSupplyService(self.db).supersede_for_material_change(
                outcome.order,
                "The AutoCount line mapping changed after this revision was confirmed.",
            )

    # ---------------------------------------------------------------- the map

    def _outcomes_for(
        self, orders: Sequence[ProjectSalesOrder]
    ) -> Dict[str, _OrderOutcome]:
        """Every order's verdict, in a fixed number of queries whatever the page size."""
        orders = [order for order in orders if order is not None]
        if not orders:
            return {}

        order_ids = [str(order.id) for order in orders]
        so_ids = [str(order.so_id) for order in orders if order.so_id]

        project_lines: Dict[str, List[ProjectSalesOrderLine]] = {
            order_id: [] for order_id in order_ids
        }
        for line in (
            self.db.query(ProjectSalesOrderLine)
            .filter(ProjectSalesOrderLine.project_sales_order_id.in_(order_ids))
            .order_by(ProjectSalesOrderLine.line_no.asc())
            .all()
        ):
            project_lines.setdefault(str(line.project_sales_order_id), []).append(line)

        core_lines: Dict[str, List[SalesOrderLine]] = {so_id: [] for so_id in so_ids}
        if so_ids:
            for line in (
                self.db.query(SalesOrderLine)
                .filter(
                    SalesOrderLine.sales_order_id.in_(so_ids),
                    SalesOrderLine.line_status.is_distinct_from(CORE_LINE_CLOSED),
                )
                .all()
            ):
                core_lines.setdefault(str(line.sales_order_id), []).append(line)

        core_numbers = self._core_so_numbers(so_ids)
        claims = self._claims_by_other_orders(core_lines)
        stale = self._stale_link_targets(project_lines, core_lines, orders)
        codes = self._product_codes(
            [line.product_id for lines in project_lines.values() for line in lines]
            + [line.product_id for lines in core_lines.values() for line in lines]
        )
        decided = self._orders_with_an_active_decision(order_ids)

        outcomes = {
            str(order.id): self._outcome(
                order,
                project_lines.get(str(order.id), []),
                core_lines.get(str(order.so_id), []) if order.so_id else [],
                core_numbers=core_numbers,
                claims=claims,
                stale=stale,
                codes=codes,
            )
            for order in orders
        }
        for order_id, outcome in outcomes.items():
            outcome.has_active_decision = order_id in decided
        return outcomes

    def _orders_with_an_active_decision(self, order_ids: Sequence[str]) -> set:
        """Which of these orders CS has already confirmed (Stage 1C)."""
        from app.models.project_so import DECISION_ACTIVE, SOSupplyDecision

        if not order_ids:
            return set()
        return {
            str(row[0])
            for row in self.db.query(SOSupplyDecision.project_sales_order_id)
            .filter(
                SOSupplyDecision.project_sales_order_id.in_(list(order_ids)),
                SOSupplyDecision.state == DECISION_ACTIVE,
            )
            .all()
        }

    def _outcome(
        self,
        order: ProjectSalesOrder,
        project_lines: Sequence[ProjectSalesOrderLine],
        core_lines: Sequence[SalesOrderLine],
        *,
        core_numbers: Dict[str, Optional[str]],
        claims: Dict[str, _Claim],
        stale: Dict[str, Optional[SalesOrderLine]],
        codes: Dict[str, str],
    ) -> _OrderOutcome:
        if order.status == SO_STATUS_ADOPTED:
            return self._adopted_outcome(
                order, project_lines, core_lines, core_numbers=core_numbers, codes=codes
            )

        header_outcome, header_reason, core_so_number = self._header(order, core_numbers)

        # A core line another Project SO already holds is not ours to take: the unique
        # index would refuse the write, and reporting it as `missing` would send CS
        # looking for a line that is sitting on the next sales order along. Out of the
        # pool, and named by the order holding it below.
        available: List[SalesOrderLine] = []
        # One claimed core line answers for one unmatched Project line, in required-date
        # order, so two unmatched lines against one taken core line report one duplicate
        # and one genuinely missing.
        duplicates_by_product: Dict[str, List[_Claim]] = {}
        for line in sorted(core_lines, key=_core_sort_key):
            claim = claims.get(str(line.id))
            if claim and claim[0] != str(order.id):
                duplicates_by_product.setdefault(
                    str(line.product_id or ""), []
                ).append(claim)
            else:
                available.append(line)

        assignments, ambiguity, leftover_core = _map_lines(project_lines, available)

        candidates_by_product: Dict[str, int] = {}
        for line in available:
            key = str(line.product_id or "")
            candidates_by_product[key] = candidates_by_product.get(key, 0) + 1
        # How many lines WE carry for the item, which is what makes "the document has
        # fewer lines for this item" a statement that can be checked rather than a guess.
        lines_by_product: Dict[str, int] = {}
        for line in project_lines:
            key = str(line.product_id or "")
            lines_by_product[key] = lines_by_product.get(key, 0) + 1

        rows: List[_LineOutcome] = []
        for line in project_lines:
            item_code = codes.get(str(line.product_id or "")) or None
            core_line = assignments.get(str(line.id))
            if core_line is not None:
                row = _LineOutcome(
                    line=line,
                    link=LINK_LINKED,
                    core_line_id=str(core_line[0].id),
                    candidate_count=1,
                    reason=core_line[1],
                    item_code=item_code,
                )
            elif str(line.id) in ambiguity:
                row = _LineOutcome(
                    line=line,
                    link=LINK_AMBIGUOUS,
                    core_line_id=None,
                    candidate_count=ambiguity[str(line.id)][0],
                    reason=ambiguity[str(line.id)][1],
                    item_code=item_code,
                )
            else:
                product_key = str(line.product_id or "")
                # A line that HELD a core line has its own answer, and it is a truer one
                # than the duplicate fallback: "your core line is now closed" sends CS to
                # the document, while "line 3 of another sales order holds it" would send
                # them to a sales order that never touched this line. So the stale reason
                # is read FIRST, and the duplicate is only for a line that held nothing.
                stale_reason = self._stale_reason(order, line, stale)
                if stale_reason is None and duplicates_by_product.get(product_key):
                    _holder, other_ref, other_line_no = duplicates_by_product[
                        product_key
                    ].pop(0)
                    row = _LineOutcome(
                        line=line,
                        link=LINK_DUPLICATE,
                        core_line_id=None,
                        # Nothing it may take, which is the whole point of the outcome.
                        candidate_count=0,
                        reason=_duplicate_reason(other_ref, other_line_no),
                        item_code=item_code,
                    )
                else:
                    row = _LineOutcome(
                        line=line,
                        link=LINK_MISSING,
                        core_line_id=None,
                        candidate_count=0,
                        reason=stale_reason
                        or self._missing_reason(
                            order,
                            product_candidates=candidates_by_product.get(product_key, 0),
                            product_lines=lines_by_product.get(product_key, 0),
                        ),
                        item_code=item_code,
                    )
            rows.append(row)

        return _OrderOutcome(
            order=order,
            header_outcome=header_outcome,
            header_reason=header_reason,
            core_so_number=core_so_number,
            lines=rows,
            surplus=[
                (str(line.id), codes.get(str(line.product_id or ""), ""))
                for line in leftover_core
            ],
        )

    def _adopted_outcome(
        self,
        order: ProjectSalesOrder,
        project_lines: Sequence[ProjectSalesOrderLine],
        core_lines: Sequence[SalesOrderLine],
        *,
        core_numbers: Dict[str, Optional[str]],
        codes: Dict[str, str],
    ) -> _OrderOutcome:
        """An adopted order's verdict: a SYNC report, not a diff (plan section 5.2).

        There is no second document to disagree with, so there is no mapping rule to run
        and nothing to be ambiguous about: every mirror line was written carrying the core
        line it addresses. What CAN go wrong is that the core line went away - closed,
        deleted, or covered - and then the mirror line addresses nothing. That is the only
        exception this outcome raises, and it names the line by number and item code like
        every other one.

        `surplus` stays empty on purpose. A core line the mirror does not carry is not a
        disagreement between two documents; it is a line to mirror, which is Re-sync's job
        and not an exception a person has to answer.
        """
        candidate_ids = {str(line.id) for line in core_lines}
        rows: List[_LineOutcome] = []
        for line in project_lines:
            item_code = codes.get(str(line.product_id or "")) or None
            link_id = (
                str(line.core_sales_order_line_id)
                if line.core_sales_order_line_id
                else None
            )
            if link_id and link_id in candidate_ids:
                rows.append(
                    _LineOutcome(
                        line=line,
                        link=LINK_LINKED,
                        core_line_id=link_id,
                        candidate_count=1,
                        reason="Mirrored from this line on the sales order.",
                        item_code=item_code,
                    )
                )
            else:
                rows.append(
                    _LineOutcome(
                        line=line,
                        link=LINK_MISSING,
                        core_line_id=None,
                        candidate_count=0,
                        reason=(
                            "The sales-order line this was mirrored from is gone from "
                            "the book. Remove this line to carry on planning."
                        ),
                        item_code=item_code,
                    )
                )

        number = core_numbers.get(str(order.so_id)) if order.so_id else None
        if order.so_id and str(order.so_id) not in core_numbers:
            # The core order is out of this company's reach, so nothing about it can be
            # asserted. Same answer the authored header gives, for the same reason.
            return _OrderOutcome(
                order=order,
                header_outcome=HEADER_NO_CORE_SO,
                header_reason="The linked core sales order is not visible to this company.",
                core_so_number=None,
                lines=rows,
            )
        return _OrderOutcome(
            order=order,
            header_outcome=HEADER_ADOPTED,
            header_reason=(
                f"This order came from the AutoCount sales-order book as {number}. "
                "There is no separately authored Project SO to compare it against."
                if number
                else (
                    "This order came from the AutoCount sales-order book. There is no "
                    "separately authored Project SO to compare it against."
                )
            ),
            core_so_number=number,
            lines=rows,
        )

    def _header(
        self, order: ProjectSalesOrder, core_numbers: Dict[str, Optional[str]]
    ) -> Tuple[str, str, Optional[str]]:
        """`linked` is decided by `so_id`, not by the document number.

        The slice note lists `no_document` first, but an order that HAS a core sales order
        is linked whatever its document number says: the review state turns on `so_id`, so
        a header reading "nothing uploaded yet" beside a Needs CS review pill would be the
        screen contradicting itself.
        """
        if order.so_id:
            if str(order.so_id) not in core_numbers:
                # The link exists, but the row it names cannot be read under this
                # company's scope. Claiming `linked` to a sales order we cannot even
                # name would be the screen asserting something it has not seen.
                return (
                    HEADER_NO_CORE_SO,
                    "The linked core sales order is not visible to this company.",
                    None,
                )
            number = core_numbers.get(str(order.so_id))
            if number:
                return HEADER_LINKED, f"Linked to sales order {number}.", number
            return (
                HEADER_LINKED,
                "Linked to the core sales order for this document.",
                None,
            )
        if not order.autocount_doc_no:
            return (
                HEADER_NO_DOCUMENT,
                "No AutoCount document has been uploaded for this sales order yet.",
                None,
            )
        return (
            HEADER_NO_CORE_SO,
            "The outstanding sales order book has not carried document "
            f"{order.autocount_doc_no} yet.",
            None,
        )

    def _stale_reason(
        self,
        order: ProjectSalesOrder,
        line: ProjectSalesOrderLine,
        stale: Dict[str, Optional[SalesOrderLine]],
    ) -> Optional[str]:
        """What became of the core line this Project line used to hold, if it held one.

        Stated as the core line's CURRENT state, never as something this call did: the
        read path (`evaluate`) writes nothing, so "the link was cleared" would be a
        sentence about an action the reader has not asked for yet.
        """
        if not order.so_id or not line.core_sales_order_line_id:
            return None
        key = str(line.core_sales_order_line_id)
        if key not in stale:
            # Still a candidate of this order, so the link is not stale at all.
            return None
        target = stale[key]
        if target is None:
            return "Its previous core line no longer exists."
        if target.line_status == CORE_LINE_CLOSED:
            return "Its previous core line is now closed."
        return "Its previous core line now belongs to another sales order."

    def _missing_reason(
        self,
        order: ProjectSalesOrder,
        *,
        product_candidates: int,
        product_lines: int,
    ) -> str:
        """Why nothing on the document could be this line, in a checkable sentence.

        The counts are the whole point. Saying the document carries FEWER lines for the
        item is something CS will go and count, so it is said only when it is true; when
        the document carries as many as we do and they were spoken for by an ambiguity
        elsewhere on the order, that is what the sentence says instead.
        """
        if not order.so_id:
            return "There is no core sales order to map this line against."
        if product_candidates == 0:
            return "The AutoCount document has no line for this item."
        if product_candidates < product_lines:
            return (
                "The AutoCount document has fewer lines for this item than this sales "
                "order does."
            )
        return (
            "Every AutoCount line for this item is already accounted for by another line "
            "on this sales order."
        )

    # ----------------------------------------------------------------- lookups

    def _core_so_numbers(self, so_ids: Sequence[str]) -> Dict[str, Optional[str]]:
        if not so_ids:
            return {}
        return {
            str(row[0]): row[1]
            for row in self.db.query(SalesOrder.id, SalesOrder.so_number)
            .filter(SalesOrder.id.in_(list(set(so_ids))))
            .all()
        }

    def _claims_by_other_orders(
        self, core_lines: Dict[str, List[SalesOrderLine]]
    ) -> Dict[str, _Claim]:
        """Which Project SO already holds each candidate core line, if any.

        `uq_projects_so_line_core_line` allows exactly one holder, so this is a lookup
        rather than a list. Read for EVERY order in the batch at once and filtered per
        order afterwards: two Project SOs that adopted the same document can both be on
        the page, and each has to see the other's claim.
        """
        wanted = {str(line.id) for lines in core_lines.values() for line in lines}
        if not wanted:
            return {}
        rows = (
            self.db.query(
                ProjectSalesOrderLine.core_sales_order_line_id,
                ProjectSalesOrderLine.project_sales_order_id,
                ProjectSalesOrderLine.line_no,
                ProjectSalesOrder.provisional_ref,
            )
            .join(
                ProjectSalesOrder,
                ProjectSalesOrder.id == ProjectSalesOrderLine.project_sales_order_id,
            )
            .filter(ProjectSalesOrderLine.core_sales_order_line_id.in_(list(wanted)))
            .all()
        )
        return {str(row[0]): (str(row[1]), row[3], row[2]) for row in rows}

    def _stale_link_targets(
        self,
        project_lines: Dict[str, List[ProjectSalesOrderLine]],
        core_lines: Dict[str, List[SalesOrderLine]],
        orders: Sequence[ProjectSalesOrder],
    ) -> Dict[str, Optional[SalesOrderLine]]:
        """The core lines a stale link still names, so the reason can say WHY it went.

        "Its previous core line is now closed" and "it moved to another sales order" are
        different problems answered in different places, and the difference is only
        knowable by reading the row the dead link points at.
        """
        # Candidacy is per ORDER, not per batch: another order's core line is not this
        # order's candidate, so a batch-wide set would silently declare a genuinely stale
        # link healthy whenever the two orders happen to land on the same page.
        wanted: set[str] = set()
        for order in orders:
            candidate_ids = {
                str(candidate.id)
                for candidate in core_lines.get(str(order.so_id), [])
            }
            for line in project_lines.get(str(order.id), []):
                link_id = (
                    str(line.core_sales_order_line_id)
                    if line.core_sales_order_line_id
                    else None
                )
                if link_id and link_id not in candidate_ids:
                    wanted.add(link_id)
        if not wanted:
            return {}
        found = {
            str(row.id): row
            for row in self.db.query(SalesOrderLine)
            .filter(SalesOrderLine.id.in_(list(wanted)))
            .all()
        }
        return {link_id: found.get(link_id) for link_id in wanted}

    def _product_codes(self, product_ids: Iterable[Any]) -> Dict[str, str]:
        wanted = {str(product_id) for product_id in product_ids if product_id}
        if not wanted:
            return {}
        return {
            str(row[0]): row[1] or ""
            for row in self.db.query(Product.id, Product.product_code)
            .filter(Product.id.in_(list(wanted)))
            .all()
        }

    # ------------------------------------------------------------- the summary

    def _summary(self, outcome: _OrderOutcome) -> Dict[str, Any]:
        order = outcome.order
        header = self._display_fields(order)
        return {
            "project_sales_order_id": order.id,
            "provisional_ref": order.provisional_ref,
            "autocount_doc_no": order.autocount_doc_no,
            "project_id": order.project_id,
            "project_code": header["project_code"],
            "project_name": header["project_name"],
            "customer_name": header["customer_name"],
            "po_number": header["po_number"],
            "area_group": order.area_group,
            "status": order.status,
            "review_state": outcome.review_state,
            "header": {
                "outcome": outcome.header_outcome,
                "core_so_number": outcome.core_so_number,
                "reason": outcome.header_reason,
            },
            "lines": [
                {
                    "id": row.line.id,
                    "line_no": row.line.line_no,
                    "product_code": row.item_code,
                    "description": row.line.description,
                    "qty": row.line.qty,
                    "uom": row.line.uom,
                    "delivery_date": row.line.delivery_date,
                    "stock_location": row.line.stock_location,
                    "link": row.link,
                    "candidate_count": row.candidate_count,
                    "reason": row.reason,
                }
                for row in outcome.lines
            ],
            "exceptions": outcome.exceptions,
            "lines_total": outcome.lines_total,
            "lines_linked": outcome.lines_linked,
        }

    def _display_fields(self, order: ProjectSalesOrder) -> Dict[str, Optional[str]]:
        rows = self._header_rows([str(order.id)])
        return rows.get(
            str(order.id),
            {
                "project_code": None,
                "project_name": None,
                "customer_name": None,
                "po_number": None,
            },
        )

    def _header_rows(self, order_ids: Sequence[str]) -> Dict[str, Dict[str, Any]]:
        """Project, customer and PO number for a whole page, in one joined query.

        The customer falls back to the issuing party's own name for the same reason the
        sales-order row does: a pre-order parked under another debtor still has a name a
        person recognises, and a blank column reads as missing data.
        """
        ids = [str(order_id) for order_id in order_ids if order_id]
        if not ids:
            return {}
        rows = (
            self.db.query(
                ProjectSalesOrder.id,
                Project.project_code,
                Project.title,
                ProjectPurchaseOrder.po_number,
                ProjectParty.name,
                Customer.customer_name,
            )
            .outerjoin(Project, Project.id == ProjectSalesOrder.project_id)
            .outerjoin(
                ProjectPurchaseOrder,
                ProjectPurchaseOrder.id == ProjectSalesOrder.purchase_order_id,
            )
            .outerjoin(
                ProjectParty, ProjectParty.id == ProjectPurchaseOrder.issuing_party_id
            )
            .outerjoin(Customer, Customer.id == ProjectParty.customer_id)
            .filter(ProjectSalesOrder.id.in_(ids))
            .all()
        )
        return {
            str(row[0]): {
                "project_code": row[1],
                "project_name": row[2],
                "po_number": row[3],
                "customer_name": row[5] or row[4],
            }
            for row in rows
        }

    # -------------------------------------------------------------- the worklist

    def list_fulfilment_planning(
        self,
        *,
        query: Optional[str] = None,
        review_state: Optional[str] = None,
        project_id: Optional[str] = None,
        sales_order_id: Optional[str] = None,
        sort: Optional[str] = None,
        dir: Optional[str] = None,
        page: int = 1,
        limit: int = 50,
    ) -> Dict[str, Any]:
        """Everything that needs planning, one row per subject (plan section 5.2, 6).

        A UNION of two arms, disjoint by construction:

        * arm 1, `row_kind = 'sales_order'` - an OUTSTANDING project-class core sales
          order, whether or not anybody has planned it. This is the arm that makes the
          AutoCount book visible without a row being written for it (AC-FP01): an
          unplanned order reads `not_started` and carries no planning record's fields,
          because no planning record exists to carry them.
        * arm 2, `row_kind = 'planning_record'` - a planning record this system authored
          that arm 1 does NOT already carry. Stage 1B's Awaiting reconciliation.

        The plan words arm 2 as "planning records with `so_id IS NULL`". That is the same
        set for every order the plan describes, and it loses one the plan does not: a
        published order LINKED to a core sales order that is not project-class, or whose
        lines are all covered, is on neither arm and would silently vanish from the screen
        Stage 1B built it for. So the rule implemented here is "not already represented by
        arm 1", which is a superset of both and keeps each subject on exactly one row
        (AC-FP03). Deviation recorded in the plan's section 6.

        Ordering DEFAULTS to earliest OUTSTANDING required date ascending, undated last
        (AC-FP04): the order the work is actually due in, rather than the order somebody
        last saved a record in. `sort` + `dir` (the names `buildDataGridParams` sends)
        override it on any of `SORTABLE_FIELDS`, and three rules hold for every one of them:

        * **The sort is applied to the UNION, not per arm.** A core sales order and an
          authored record interleave on every field; sorting each arm and concatenating
          would look right at the top of page 1 and be wrong everywhere below it.
        * **It stays TOTAL and STABLE.** Every sort ends in `_worklist_tiebreak`, applied as
          a stable pre-pass, so a tie breaks the same way on page 2 as on page 1 and in
          either direction.
        * **Nulls sort LAST in BOTH directions.** Postgres would put them last ascending
          and first descending, so reversing "earliest required date" would answer with the
          orders that have no date at all. A missing value is not an extreme value.

        A field one arm cannot supply (an authored record has no sales-order number, a
        not-started row has no reference, updated_at or area group) does not hide that arm:
        those rows sort to the end by the null rule and are never dropped.

        Paging is done in Python, and this is a deliberate change from Stage 1B's SQL
        paging. Two arms over two tables have no single SQL cursor to offset, and the
        population is bounded by what is outstanding: 605 core orders and 16 planning
        records on the live book (plan section 12). The alternative is a hand-built UNION
        over `__table__`, which would leave the company predicate to be spliced in by hand
        on both arms - fail-open exactly where AC-FP06 must be fail-closed.

        The review state is DERIVED and has no column, so it is derived for as few subjects
        as the filter allows: not at all for `not_started` (which is simply "no planning
        record"), for every planning record when another state is filtered on OR when the
        sort needs it (`_SORT_NEEDS_OUTCOMES`), and for the page alone otherwise. The same
        rule governs the joined project / customer / PO strip (`_SORT_NEEDS_HEADERS`).

        **What that costs, measured rather than assumed** (live `sorento_scm_e2e_stack`,
        606 subjects, 18 August 2026, page size 25):

        ==============================  ==================  ====================
        sort                            1 planning record   ALL 605 adopted
        ==============================  ==================  ====================
        earliest_required_date / other  25 to 45 ms         80 to 140 ms
        customer_name / project_label   ~35 ms              ~140 ms
        review_state                    ~25 ms              **~450 ms**
        line_count                      ~30 ms              **~484 ms**
        ==============================  ==================  ====================

        So the header pass is free and the OUTCOME pass is not: it is roughly 0.7 ms per
        planning record, because deriving a state with no column means reading that record's
        mirror lines and its core lines. Today (one record) nothing is measurable; if the
        whole book is ever planned, those two columns cost about half a second.

        The cheap way out was to sort `line_count` on the core arm's own open-line count,
        which needs no derivation - and is REFUSED here, because for an authored order
        linked to a core order that number differs from the one the cell prints, and a
        column that sorts by one number and shows another is the same defect as a sort the
        server ignored. If it ever bites, the answer is a stored review state or a
        materialised line count, which is a schema decision for the plan owner.
        """
        page = max(page, 1)
        offset = (page - 1) * limit
        needle = (query or "").strip()
        sort_field = sort or DEFAULT_SORT_FIELD
        if sort_field not in SORTABLE_FIELDS:
            # Belt and braces behind the route's `Literal`: a service caller (a test, a
            # script, the MCP) must get the same refusal the HTTP caller gets rather than a
            # quiet fall back to the default order.
            raise AppException(
                422,
                f"'{sort_field}' is not a column this list can be sorted by.",
                code="unsortable_field",
            )
        if dir not in (None, "asc", "desc"):
            raise AppException(
                422,
                f"'{dir}' is not a sort direction. Use asc or desc.",
                code="invalid_sort_direction",
            )
        descending = dir == "desc"

        core_rows = self._core_arm(needle=needle, sales_order_id=sales_order_id)
        records = self._records_for([row["sales_order_id"] for row in core_rows])
        authored_rows = self._authored_arm(
            needle=needle, taken={str(row["sales_order_id"]) for row in core_rows}
        )

        rows = [self._core_row(row, records.get(str(row["sales_order_id"]))) for row in core_rows]
        rows += authored_rows
        if project_id:
            # A not-started row belongs to no project registration, so filtering by one
            # excludes it rather than guessing which project the sheet's string means.
            rows = [row for row in rows if row["project_id"] == project_id]
        if sales_order_id:
            rows = [
                row
                for row in rows
                if str(row.get("sales_order_id") or "") == str(sales_order_id)
            ]
        # A not-started row IS a row with no planning record, so that filter needs nothing
        # derived; it also empties the two extra passes below, because there is then no
        # record left to derive anything for.
        if review_state == REVIEW_NOT_STARTED:
            rows = [row for row in rows if row["id"] is None]

        record_ids = [row["id"] for row in rows if row["id"]]
        wants_all_outcomes = bool(
            (review_state and review_state != REVIEW_NOT_STARTED)
            or sort_field in _SORT_NEEDS_OUTCOMES
        )
        outcomes: Dict[str, _OrderOutcome] = (
            self._outcomes_for(self._planning_records(record_ids))
            if wants_all_outcomes
            else {}
        )
        headers: Dict[str, Dict[str, Any]] = (
            self._header_rows(record_ids)
            if sort_field in _SORT_NEEDS_HEADERS
            else {}
        )

        if review_state and review_state != REVIEW_NOT_STARTED:
            rows = [
                row
                for row in rows
                if row["id"] and outcomes[str(row["id"])].review_state == review_state
            ]

        self._sort_rows(rows, sort_field, descending, outcomes, headers)
        total = len(rows)
        window = rows[offset : offset + limit]

        # Top up for the page when the sort did not already need the whole set. Both
        # helpers are already covering a superset when it did.
        page_ids = [row["id"] for row in window if row["id"]]
        if not wants_all_outcomes:
            outcomes = self._outcomes_for(self._planning_records(page_ids))
        if sort_field not in _SORT_NEEDS_HEADERS:
            headers = self._header_rows(page_ids)

        data = []
        for row in window:
            data.append(self._worklist_row(row, outcomes, headers))
        # AC-B7: the same `Changed` pill the SCM Sales Orders list and the fulfilment
        # board show, off the SAME rule
        # (`planning_change_service.pending_batch_id_by_sales_order`,
        # `PLAN-scm-board-picks-up-pending-change.md`) - one query for the page, not one
        # per row.
        page_so_ids = list({r["sales_order_id"] for r in data if r.get("sales_order_id")})
        pending_by_so = planning_change_service.pending_batch_id_by_sales_order(
            self.db, page_so_ids,
        )
        for r in data:
            r["planning_change_batch_id"] = pending_by_so.get(r.get("sales_order_id"))
        return {
            "data": data,
            "pagination": {"total": total, "page": page, "limit": limit},
            # The whole result set, not this page: page 3 of 2 pages is empty of rows
            # without the worklist being empty, and the siblings all read it this way.
            "empty": total == 0,
        }

    # ------------------------------------------------------------------- sorting

    def _sort_rows(
        self,
        rows: List[Dict[str, Any]],
        field: str,
        descending: bool,
        outcomes: Dict[str, _OrderOutcome],
        headers: Dict[str, Dict[str, Any]],
    ) -> None:
        """Order the whole union in place, nulls last, total and stable.

        TWO PASSES, and that is the whole trick. Python's sort is stable, so sorting by the
        tiebreaker FIRST and by the field second leaves the tiebreaker's order intact inside
        every group of equal values - including when the second pass runs in reverse. Doing
        it as one composite key would need the tiebreaker to invert with the direction,
        which is how "which of these equal rows comes first" ends up with two answers.

        Rows with no value are partitioned out BEFORE either pass rather than given a
        sentinel, so they land last in both directions and never compare against a real
        value. This is the one thing an `ORDER BY` would have got wrong for free.
        """
        decorated = [
            (self._sort_value(row, field, outcomes, headers), row) for row in rows
        ]
        present = [pair for pair in decorated if pair[0] is not None]
        absent = [pair[1] for pair in decorated if pair[0] is None]

        present.sort(key=lambda pair: _worklist_tiebreak(pair[1]))
        present.sort(key=lambda pair: pair[0], reverse=descending)
        absent.sort(key=_worklist_tiebreak)

        rows[:] = [pair[1] for pair in present] + absent

    def _sort_value(
        self,
        row: Dict[str, Any],
        field: str,
        outcomes: Dict[str, _OrderOutcome],
        headers: Dict[str, Dict[str, Any]],
    ) -> Any:
        """What one row sorts BY, or `None` when it has no value for this field.

        `None` is the honest answer for a field the row's arm cannot supply - an authored
        record has no sales-order number, a not-started row has no reference, area group or
        `updated_at` - and it is what puts that row at the end rather than dropping it.

        Every value here is the one the CELL SHOWS, resolved through the same fallbacks
        `_worklist_row` uses, so a column can never sort by one number and print another.
        Text is compared case-folded, or `Zebra` sorts above `apple` and the grid looks
        broken to everyone except a computer.
        """
        record: Optional[ProjectSalesOrder] = row.get("record")
        display = headers.get(str(row["id"]), {}) if row.get("id") else {}
        outcome = outcomes.get(str(row["id"])) if row.get("id") else None

        if field == "so_number":
            return _folded(row.get("so_number"))
        if field == "earliest_required_date":
            return row.get("earliest_required_date")
        if field == "outstanding_qty":
            value = row.get("outstanding_qty")
            return Decimal(str(value)) if value is not None else None
        if field == "line_count":
            # The mirror's count when there is a planning record, the core order's open-line
            # count when there is not: exactly what the cell prints.
            return outcome.lines_total if outcome is not None else row.get("open_line_count")
        if field == "review_state":
            state = (
                (outcome.review_state if outcome is not None else None)
                if record is not None
                else REVIEW_NOT_STARTED
            )
            return _REVIEW_STATE_RANK.get(state) if state else None
        if field == "customer_name":
            return _folded(display.get("customer_name") or row.get("core_customer_name"))
        if field == "project_label":
            return _folded(display.get("project_name") or row.get("project_string"))
        if field == "po_number":
            return _folded(display.get("po_number"))
        if field == "provisional_ref":
            return _folded(record.provisional_ref if record is not None else None)
        if field == "area_group":
            return _folded(record.area_group if record is not None else None)
        if field == "updated_at":
            return record.updated_at if record is not None else None
        # Unreachable: `SORTABLE_FIELDS` is checked before this is ever called, and the
        # route's `Literal` checks it again a layer earlier.
        return None

    # ------------------------------------------------------------- arm 1: the book

    def _core_arm(
        self, *, needle: str, sales_order_id: Optional[str]
    ) -> List[Dict[str, Any]]:
        """Outstanding project-class core sales orders, with the numbers the row prints.

        The predicate is `is_open_demand()` VERBATIM plus `status = 'open'` plus
        `demand_class = 'project'` (plan section 3), imported and never restated: it is
        what `scm.committed_v` counts and what the SCM sales-order screen's
        `outstanding = true` filter already means, so the two screens cannot disagree about
        which orders are still owed. The inner join to the lines is what makes "has at
        least one line still owed" a fact of the same query rather than a second one.

        `Customer` is reached with `.has(...)` rather than an outer join on purpose: the
        company-scope listener splices its predicate into the statement, and an outer join
        carrying it can silently narrow to an inner one, which would drop every order that
        has no customer at all.
        """
        aggregate = (
            self.db.query(
                SalesOrder.id,
                SalesOrder.so_number,
                SalesOrder.internal_note,
                SalesOrder.customer_id,
                func.min(SalesOrderLine.required_date),
                func.sum(demand_qty()),
                func.count(SalesOrderLine.id),
                # Who sold it, in the same read: the worklist's own row, not a per-row lookup.
                SalesAgent.sales_agent,
                SalesAgent.person_label,
                SalesOrder.doc_type,
            )
            .join(SalesOrderLine, SalesOrderLine.sales_order_id == SalesOrder.id)
            .outerjoin(SalesAgent, SalesAgent.id == SalesOrder.sales_agent_id)
            .filter(
                SalesOrder.demand_class == PROJECT_CLASS,
                SalesOrder.status == _CORE_SO_OPEN,
                is_open_demand(),
            )
            .group_by(
                SalesOrder.id,
                SalesOrder.so_number,
                SalesOrder.internal_note,
                SalesOrder.customer_id,
                SalesAgent.sales_agent,
                SalesAgent.person_label,
                SalesOrder.doc_type,
            )
        )
        if sales_order_id:
            aggregate = aggregate.filter(SalesOrder.id == str(sales_order_id))
        if needle:
            like = f"%{needle}%"
            matched = (
                SalesOrder.so_number.ilike(like)
                | SalesOrder.internal_note.ilike(like)
                | SalesOrder.customer.has(Customer.customer_name.ilike(like))
            )
            # The product arm, as a MEMBERSHIP TEST against a set computed by its own
            # query. Not a join (an order owing six of the wanted item would fan out into
            # six rows, or - worse, since this query aggregates - would silently narrow
            # `min`, `sum` and `count` to the matching lines and print the wrong totals),
            # and not a correlated EXISTS either: the outer query already has
            # `sales_order_lines` in its FROM, so a sub-select over the same table would
            # auto-correlate to the row being aggregated and quietly become that same
            # per-line filter. A separate query cannot be adapted wrongly.
            owners = self._core_orders_matching_product(like)
            if owners:
                matched = matched | SalesOrder.id.in_(list(owners))
            aggregate = aggregate.filter(matched)
        rows = aggregate.all()
        names = self._customer_names([row[3] for row in rows])
        return [
            {
                "sales_order_id": str(row[0]),
                "so_number": row[1],
                "project_string": _project_string(row[2]),
                "customer_name": names.get(str(row[3] or "")),
                "earliest_required_date": row[4],
                "outstanding_qty": row[5],
                "open_line_count": row[6] or 0,
                "agent_code": row[7],
                "agent_label": row[8],
                "so_doc_type": row[9],
            }
            for row in rows
        ]

    def _core_orders_matching_product(self, like: str) -> set:
        """Which outstanding project-class core orders owe a product answering to `like`.

        BOUNDED BY THE WORKLIST'S OWN POPULATION, deliberately. The same header predicate as
        `_core_arm` is applied here, so the answer can never be larger than the arm it
        filters - 605 orders on the live book - and the `IN` list it becomes has a ceiling
        somebody has measured rather than one that grows with the sales-order table.

        `is_open_demand()` is applied VERBATIM, the same as everywhere else on this screen.
        Without it the box would surface an order whose only matching line this very screen
        calls finished, and a filter that disagrees with the list it filters is how a
        planner learns to stop trusting the box.

        `DISTINCT` at the database, so an order owing six of the item arrives once.
        """
        rows = (
            self.db.query(SalesOrderLine.sales_order_id)
            .join(SalesOrder, SalesOrder.id == SalesOrderLine.sales_order_id)
            .join(Product, Product.id == SalesOrderLine.product_id)
            .filter(
                SalesOrder.demand_class == PROJECT_CLASS,
                SalesOrder.status == _CORE_SO_OPEN,
                is_open_demand(),
                Product.product_code.ilike(like) | Product.product_name.ilike(like),
            )
            .distinct()
            .all()
        )
        return {str(row[0]) for row in rows}

    def _records_matching_product(self, like: str) -> set:
        """The same question for the authored arm, over the record's OWN lines.

        An arm-2 record has no core sales order - that absence is what puts it on this arm -
        so there is no `is_open_demand()` to apply and no core line to read. Its own lines
        are what it owes, which is the same set the row's `line_count` and `outstanding_qty`
        are computed from, so the box and the columns agree.
        """
        rows = (
            self.db.query(ProjectSalesOrderLine.project_sales_order_id)
            .join(Product, Product.id == ProjectSalesOrderLine.product_id)
            .filter(Product.product_code.ilike(like) | Product.product_name.ilike(like))
            .distinct()
            .all()
        )
        return {str(row[0]) for row in rows}

    def _customer_names(self, customer_ids: Iterable[Any]) -> Dict[str, Optional[str]]:
        wanted = {str(value) for value in customer_ids if value}
        if not wanted:
            return {}
        return {
            str(row[0]): row[1]
            for row in self.db.query(Customer.id, Customer.customer_name)
            .filter(Customer.id.in_(list(wanted)))
            .all()
        }

    def _records_for(
        self, sales_order_ids: Sequence[str]
    ) -> Dict[str, ProjectSalesOrder]:
        """The planning record holding each of those core orders, where one exists.

        `uq_projects_so_core_order` allows exactly one, so this is a lookup and not a list.
        The search needle is deliberately NOT applied here: an arm-1 row is found by what
        the CORE order prints, and dropping its record because the record's own reference
        does not match the search would turn a planned order into a Not started one on
        screen, which is a lie about state rather than a narrower list.
        """
        ids = [str(value) for value in sales_order_ids if value]
        if not ids:
            return {}
        return {
            str(row.so_id): row
            for row in self.db.query(ProjectSalesOrder)
            .filter(ProjectSalesOrder.so_id.in_(ids))
            .all()
        }

    def _core_row(
        self, core: Dict[str, Any], record: Optional[ProjectSalesOrder]
    ) -> Dict[str, Any]:
        return {
            "row_kind": ROW_KIND_SALES_ORDER,
            "id": str(record.id) if record is not None else None,
            "sales_order_id": core["sales_order_id"],
            "so_number": core["so_number"],
            "project_string": core["project_string"],
            "core_customer_name": core["customer_name"],
            "earliest_required_date": core["earliest_required_date"],
            "outstanding_qty": core["outstanding_qty"],
            "open_line_count": core["open_line_count"],
            #: Who sold it. Absent on the authored arm - `ProjectSalesOrder` carries no
            #: `sales_agent_id` of its own - so `_worklist_row`'s `.get()` reads it as null
            #: there rather than this method inventing a key for a row that has none.
            "agent_code": core["agent_code"],
            "agent_label": core["agent_label"],
            # AC-CONSIGN-CRM: the authored arm fills its own (a record's core order, if any).
            "so_doc_type": core["so_doc_type"],
            "record": record,
            "project_id": str(record.project_id)
            if record is not None and record.project_id
            else None,
        }

    # --------------------------------------------------- arm 2: authored records

    def _authored_arm(self, *, needle: str, taken: set) -> List[Dict[str, Any]]:
        """Authored planning records arm 1 does not already carry.

        Their quantities and dates come off their OWN lines, because there is no core sales
        order to read them from - that absence is exactly what puts them on this arm.
        """
        rows = self.db.query(ProjectSalesOrder).filter(
            ProjectSalesOrder.status.in_(AUTHORED_LIVE_STATUSES)
        )
        if needle:
            # Everything the row PRINTS is searchable, through the same outer joins
            # `_header_rows` reads those columns with: the screen offers "sales order,
            # project or customer", and a search that quietly covered only the sales
            # order would answer "no results" for a project code sitting in the list.
            like = f"%{needle}%"
            matched = (
                ProjectSalesOrder.provisional_ref.ilike(like)
                | ProjectSalesOrder.autocount_doc_no.ilike(like)
                | ProjectSalesOrder.area_group.ilike(like)
                | Project.project_code.ilike(like)
                | Project.title.ilike(like)
                | Customer.customer_name.ilike(like)
                # The party's own name is the customer column's fallback, so it has
                # to be searchable or a row would not answer to the name it shows.
                | ProjectParty.name.ilike(like)
            )
            # The product arm, on THIS arm's own lines. Same membership-test shape as arm 1
            # and for the same reason: a record with six matching lines is one row.
            owners = self._records_matching_product(like)
            if owners:
                matched = matched | ProjectSalesOrder.id.in_(list(owners))
            rows = (
                rows.outerjoin(Project, Project.id == ProjectSalesOrder.project_id)
                .outerjoin(
                    ProjectPurchaseOrder,
                    ProjectPurchaseOrder.id == ProjectSalesOrder.purchase_order_id,
                )
                .outerjoin(
                    ProjectParty,
                    ProjectParty.id == ProjectPurchaseOrder.issuing_party_id,
                )
                .outerjoin(Customer, Customer.id == ProjectParty.customer_id)
                .filter(matched)
            )
        orders = [
            order for order in rows.all() if str(order.so_id or "") not in taken
        ]
        totals = self._authored_line_totals([str(order.id) for order in orders])
        # AC-CONSIGN-CRM: a record can point at a core order that is not on the project-class
        # arm; its doc_type still marks it, in one read for the page.
        so_ids = [str(order.so_id) for order in orders if order.so_id]
        doc_types = (
            {
                str(sid): doc_type
                for sid, doc_type in self.db.query(SalesOrder.id, SalesOrder.doc_type)
                .filter(SalesOrder.id.in_(so_ids))
                .all()
            }
            if so_ids
            else {}
        )
        return [
            {
                "row_kind": ROW_KIND_PLANNING_RECORD,
                "id": str(order.id),
                "sales_order_id": None,
                "so_number": None,
                "so_doc_type": doc_types.get(str(order.so_id or "")),
                "project_string": None,
                "core_customer_name": None,
                "earliest_required_date": totals.get(str(order.id), (None, None, 0))[0],
                "outstanding_qty": totals.get(str(order.id), (None, None, 0))[1],
                "open_line_count": totals.get(str(order.id), (None, None, 0))[2],
                "record": order,
                "project_id": str(order.project_id) if order.project_id else None,
            }
            for order in orders
        ]

    def _authored_line_totals(self, order_ids: Sequence[str]) -> Dict[str, Tuple]:
        """This arm's `earliest_required_date`, `outstanding_qty` and `open_line_count`.

        THE SAME QUESTION ARM 1 ASKS, off the mirror instead of the core book, because these
        records have no outstanding core order to read. Arm 1 aggregates
        `min(required_date)`, `sum(demand_qty())` and `count(*)` over lines passing
        `is_open_demand()`; a row here that counted a different population would print a
        larger outstanding figure beside arm 1's on the same screen.

        A mirror line RECONCILED to a core line is measured on that core line, exactly as arm
        1 measures it, and a still-owed line is the only kind that counts. An UNRECONCILED
        line keeps its own `qty` and delivery date: there is no book behind it, which is the
        whole reason an authored record exists.

        Unfiltered until the 14 September 2026 ruling, and correct while it was: adoption
        only ever mirrored `is_open_demand()` lines, so every mirror line was a still-owed
        one by construction. `adopt` now mirrors delivered undecided lines too - the board
        has to be able to confirm them - so the filter has to be said out loud.
        """
        ids = [str(value) for value in order_ids if value]
        if not ids:
            return {}
        # BRANCH ON THE JOIN, never `coalesce` on the expression. Postgres `greatest()`
        # IGNORES nulls, so `demand_qty()` on an outer-joined row with no core line is 0 and
        # not NULL - a coalesce behind it never fires, and every unreconciled authored line
        # contributed nothing (review round 2, N2: three real records went 2,666 / 27,888 /
        # 11,364 to 0). The same trap `owed_qty_expr()` names in the supply service.
        unreconciled = ProjectSalesOrderLine.core_sales_order_line_id.is_(None)
        owed_date = case(
            (unreconciled, ProjectSalesOrderLine.delivery_date),
            else_=SalesOrderLine.required_date,
        )
        owed_qty = case(
            (unreconciled, ProjectSalesOrderLine.qty),
            else_=demand_qty(),
        )
        rows = (
            self.db.query(
                ProjectSalesOrderLine.project_sales_order_id,
                func.min(owed_date),
                func.sum(owed_qty),
                func.count(ProjectSalesOrderLine.id),
            )
            .outerjoin(
                SalesOrderLine,
                SalesOrderLine.id == ProjectSalesOrderLine.core_sales_order_line_id,
            )
            .filter(
                ProjectSalesOrderLine.project_sales_order_id.in_(ids),
                # Reconciled: it counts only while the core line is still owed. Unreconciled
                # (`core_sales_order_line_id` null): it is this record's own line and always
                # counts, because the mirror IS the book for it.
                or_(
                    ProjectSalesOrderLine.core_sales_order_line_id.is_(None),
                    is_open_demand(),
                ),
            )
            .group_by(ProjectSalesOrderLine.project_sales_order_id)
            .all()
        )
        return {str(row[0]): (row[1], row[2], row[3] or 0) for row in rows}

    # ------------------------------------------------------------- row assembly

    def _planning_records(
        self, order_ids: Iterable[Any]
    ) -> List[ProjectSalesOrder]:
        ids = [str(value) for value in order_ids if value]
        if not ids:
            return []
        return (
            self.db.query(ProjectSalesOrder)
            .filter(ProjectSalesOrder.id.in_(ids))
            .all()
        )

    def _worklist_row(
        self,
        row: Dict[str, Any],
        outcomes: Dict[str, _OrderOutcome],
        headers: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        """One wire row, from whichever of the two subjects it actually has.

        Everything a not-started row cannot have is `None` rather than a stand-in: there is
        no planning record, so there is no reference, no status and no counts off a mirror
        nobody has written, and stating that absence is what lets the screen offer Start
        planning instead of Open.
        """
        record: Optional[ProjectSalesOrder] = row["record"]
        outcome = outcomes.get(str(row["id"])) if row["id"] else None
        display = headers.get(str(row["id"]), {}) if row["id"] else {}

        if record is None:
            state = REVIEW_NOT_STARTED
        elif outcome is not None:
            state = outcome.review_state
        else:
            state = None

        project_name = display.get("project_name")
        return {
            "row_kind": row["row_kind"],
            "id": str(record.id) if record is not None else None,
            "sales_order_id": row["sales_order_id"],
            "so_number": row["so_number"],
            "so_doc_type": row.get("so_doc_type"),
            "origin": _origin_of(record),
            "provisional_ref": record.provisional_ref if record is not None else None,
            "autocount_doc_no": record.autocount_doc_no if record is not None else None,
            "project_id": row["project_id"],
            "project_code": display.get("project_code"),
            "project_name": project_name,
            # The registered project's name when there is one, else the string the Order
            # Inquiry sheet wrote on the core order. Never an id, and never blank when
            # either source has an answer.
            "project_label": project_name or row["project_string"],
            "customer_name": display.get("customer_name") or row["core_customer_name"],
            #: Who sold it. Null on the authored arm, which has no agent of its own (see
            #: `_core_row`).
            "agent_code": row.get("agent_code"),
            "agent_label": row.get("agent_label"),
            "po_number": display.get("po_number"),
            "area_group": record.area_group if record is not None else None,
            "status": record.status if record is not None else None,
            "line_count": outcome.lines_total if outcome is not None else row["open_line_count"],
            "lines_linked": outcome.lines_linked if outcome is not None else 0,
            "exception_count": len(outcome.exceptions) if outcome is not None else 0,
            "outstanding_qty": row["outstanding_qty"],
            "earliest_required_date": row["earliest_required_date"],
            "review_state": state,
            "updated_at": record.updated_at if record is not None else None,
        }


def _map_lines(
    project_lines: Sequence[ProjectSalesOrderLine],
    core_lines: Sequence[SalesOrderLine],
) -> Tuple[
    Dict[str, Tuple[SalesOrderLine, str]],
    Dict[str, Tuple[int, str]],
    List[SalesOrderLine],
]:
    """The whole mapping rule, as a pure function over two line lists.

    Returns the pairs (Project line id -> core line and the sentence that explains it),
    the ambiguous Project lines (id -> candidate count and its sentence), and the core
    lines nothing took. Pure so the same rule serves the sheet, the list row and the
    write, and so it can be reasoned about without a database.
    """
    candidates = {str(line.id): line for line in core_lines}
    assignments: Dict[str, Tuple[SalesOrderLine, str]] = {}
    ambiguity: Dict[str, Tuple[int, str]] = {}
    taken: set[str] = set()

    # Stability first: a link that still names a candidate is kept, and that core line is
    # out of the pool before anything is re-matched.
    for line in project_lines:
        existing = (
            str(line.core_sales_order_line_id) if line.core_sales_order_line_id else None
        )
        if existing and existing in candidates and existing not in taken:
            taken.add(existing)
            assignments[str(line.id)] = (
                candidates[existing],
                "Already linked to this line on the AutoCount document.",
            )

    remaining_project = [
        line for line in project_lines if str(line.id) not in assignments
    ]
    remaining_core = [line for line in core_lines if str(line.id) not in taken]

    by_product_project: Dict[str, List[ProjectSalesOrderLine]] = {}
    for line in remaining_project:
        by_product_project.setdefault(str(line.product_id or ""), []).append(line)
    by_product_core: Dict[str, List[SalesOrderLine]] = {}
    for line in remaining_core:
        by_product_core.setdefault(str(line.product_id or ""), []).append(line)

    leftover_core: List[SalesOrderLine] = []

    for product_key in sorted(set(by_product_project) | set(by_product_core)):
        ours = sorted(by_product_project.get(product_key, []), key=_project_sort_key)
        theirs = sorted(by_product_core.get(product_key, []), key=_core_sort_key)

        # Keyed ``Any`` rather than ``Optional[date]``: a model attribute reads as
        # ``Column[date]`` to the type checker on this codebase's SQLAlchemy version.
        theirs_by_date: Dict[Any, List[SalesOrderLine]] = {}
        for line in theirs:
            theirs_by_date.setdefault(line.required_date, []).append(line)
        ours_by_date: Dict[Any, List[ProjectSalesOrderLine]] = {}
        for line in ours:
            ours_by_date.setdefault(line.delivery_date, []).append(line)

        # --- pass 1: an exact date pairs, but only when it names ONE line on each side.
        spent: set[str] = set()
        still_ours: List[ProjectSalesOrderLine] = []
        for when, group in ours_by_date.items():
            their_group = theirs_by_date.get(when, [])
            if not their_group:
                still_ours.extend(group)
                continue
            if len(group) == 1 and len(their_group) == 1:
                assignments[str(group[0].id)] = (
                    their_group[0],
                    f"Matched on product and required date {when.strftime('%d %b %Y')}."
                    if when
                    else "Matched on product code; neither line carries a required date.",
                )
                spent.add(str(their_group[0].id))
                continue
            # Two of either side at one date: which is which is a question, not a guess.
            # Both sides drop out of pass 2 - the core lines are accounted for by the
            # ambiguity, so they are not surplus either.
            reason = (
                f"{len(their_group)} lines on the AutoCount document carry this item "
                f"{_date_phrase(when)}."
                if len(their_group) > 1
                else (
                    f"{len(group)} lines on this sales order carry this item "
                    f"{_date_phrase(when)}, so no single AutoCount line can be picked."
                )
            )
            for line in group:
                ambiguity[str(line.id)] = (len(their_group), reason)
            for line in their_group:
                spent.add(str(line.id))

        # --- pass 2: what is left pairs in date order, one to one.
        still_theirs = [line for line in theirs if str(line.id) not in spent]
        still_ours.sort(key=_project_sort_key)
        still_theirs.sort(key=_core_sort_key)
        for line, core_line in zip(still_ours, still_theirs):
            assignments[str(line.id)] = (
                core_line,
                "Matched in required-date order after the exact-date pass.",
            )
        leftover_core.extend(still_theirs[len(still_ours) :])

    return assignments, ambiguity, leftover_core
