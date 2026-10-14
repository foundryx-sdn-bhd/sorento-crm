"""Saved decisions on the planning board (S4 of `PLAN-scm-fulfilment-feedback-2sep.md`).

One row per CORE SALES ORDER LINE, in `projects.so_supply_decision_drafts` - addressed from
outside by the board's contribution key, and identified inside by the line that key resolves
to (C2, code review round 4: none of the key's own four parts survives a re-upload that
renumbers the order, a partly-mirrored order, or a change of granularity). Three readers and
they are deliberately the only three:

  * the two routes, `PUT` / `DELETE .../fulfilment-planning/lines/{key}/draft`;
  * `FulfilmentBoardService.build`, which stamps what is saved back onto the board;
  * `ProjectSupplyService._write_decision`, which deletes the drafts a confirmation
    promotes, inside the confirmation's own transaction.

The draft's decision is OPAQUE here. The confirmation is posted from the board's own body
(`confirmLinesFor` composes every line it sends, whether from the saved decision or from the
engine's suggestion), so nothing on the server reads inside this JSON - it is stored, handed
back, and deleted when the line it belongs to is confirmed.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, Iterable, Optional, Tuple

from sqlalchemy.orm import Session

from app.models.order import SalesOrder, SalesOrderLine
from app.models.planning_change import PlanningChangeBatch, PlanningChangeRow
from app.models.product import Product
from app.models.project_so import (
    DECISION_ACTIVE,
    INQUIRY_CANCELLED,
    OrderInquiryRow,
    ProjectSalesOrder,
    ProjectSalesOrderLine,
    SOSupplyDecision,
    SOSupplyDecisionDraft,
)
from app.models.user import User
from app.services.error_handler import AppException
from app.services.project_line_numbering import LineFacts, number_lines
from app.services.project_supply_service import plan_qty_of
from app.services.scm.demand import is_undecided_demand
from app.services.scm.front_planning_engine import qty_text

#: `${sales_order_id}|${line_no}|${item_code}|${bucket_key}` - `_Row.key` in
#: `project_fulfilment_board_service.py`, rebuilt by `standingsFor` on the frontend.
KEY_PARTS = 4

#: Column lengths the model actually carries (`item_code` `String(100)`, `bucket_key`
#: `String(32)`): a key naming something longer than the column can hold is refused before
#: it ever reaches an INSERT, rather than surfacing as a raw truncation/DB error (S3).
ITEM_CODE_MAX = 100
BUCKET_KEY_MAX = 32

#: The 409 a covered line refuses every verdict but `amended`/`rejected` with (owner
#: rework, 23 Sep 2026, `PLAN-board-reject-on-confirmed-line.md`: reject is now STAGED
#: like every other board decision - the draft alone, written below, never a write against
#: the confirmation itself). "reject it with a reason" (fix round 3, nit, still true after
#: the rework): R3(b) gave a covered line a second way to leave a confirmation, and a
#: sentence naming only Amend/undo was stale the moment Reject stopped refusing outright.
CONFIRMED_LINE_MESSAGE = (
    "This line is already confirmed. Amend it to change the decision, "
    "reject it with a reason, or undo the confirmation."
)

#: The CORE sales order line a draft belongs to (C2, code review round 4). None of the
#: contribution key's own four parts is durable - `line_no` is positional whenever the
#: order's lines are not all mirrored, and `bucket_key` moves with the board's granularity
#: - so the row the board, the mirror and the confirmation all agree on is the identity.
LineKey = str


def parse_contribution_key(key: str) -> Tuple[str, int, str, str]:
    """The four parts of a board contribution key, or a 422.

    A key the server cannot read is refused rather than stored: a row saved under a key no
    board will ever rebuild is invisible for ever, which reads to the planner as a save
    that silently did nothing.
    """
    parts = (key or "").split("|")
    if len(parts) != KEY_PARTS or not all(part.strip() for part in parts):
        raise _bad_key(key)
    sales_order_id, line_no, item_code, bucket_key = (part.strip() for part in parts)
    try:
        uuid.UUID(sales_order_id)
    except (ValueError, AttributeError, TypeError):
        raise _bad_key(key) from None
    try:
        line_no_int = int(line_no)
    except ValueError:
        raise _bad_key(key) from None
    if len(item_code) > ITEM_CODE_MAX or len(bucket_key) > BUCKET_KEY_MAX:
        raise _bad_key(key)
    return sales_order_id, line_no_int, item_code, bucket_key


def _bad_key(key: str) -> AppException:
    return AppException(
        status_code=422,
        message=(
            "That is not a planning board line. A line is addressed by its own "
            "contribution key, and this one could not be read: "
            f"'{key}'."
        ),
        code="board_contribution_key_invalid",
    )


def _bad_line(key: str) -> AppException:
    """S3, captain ruling: a key naming a line that does not exist is a 422, not a row.

    Covers every way a syntactically-valid key can still name nothing: an order outside the
    caller's company scope, an order with no such line, or a UUID that names something other
    than a sales order at all. One message either way - the planner did not ask which of
    those it was, only that the save did not happen.
    """
    return AppException(
        status_code=422,
        message=(
            "That sales order line does not exist, or this company cannot see it. "
            f"Nothing was saved for '{key}'."
        ),
        code="board_contribution_line_not_found",
    )


def _resolve_core_line(db: Session, sales_order_id: str, line_no: int, item_code: str) -> SalesOrderLine:
    """The core line a contribution key names, or a 422 (S3).

    `SalesOrder` and `SalesOrderLine` are `CompanyScopedMixin`, so the queries below are
    already narrowed to the caller's own company by the session's `do_orm_execute` scope
    filter - an order that belongs to another company reads back as "no such order", the
    same as one that never existed.

    Mirrors `FulfilmentBoardService._line_numbers`: `project_line_numbering.number_lines`
    (B1 review round) - AutoCount's own `line_no` wins once every contributing line of the
    order carries one, distinctly (gaps and all); otherwise derived per order by (required
    date nulls last, item code, line id). One rule, shared, so a key the board handed out
    and a key resolved here name the same line. Where a mirror line exists for EVERY line
    of the order and numbers them distinctly, its numbers win over both.

    The SET of lines numbered is the board's own `_demand_rows` set - `SalesOrder.status in
    (open, closed)`, `SalesOrder.demand_class == "project"`, `is_undecided_demand()` on the
    line - never every line the order has ever carried. A save against SO391698 line 10 read
    back "line not found" without this: the order carries lines this board never counted
    (non-project, cancelled, or already marked no purchase needed), so numbering ALL of them
    landed line 10 on a different row than the one the board's own ordinal gave the same
    product its date.

    IT HAS TO MOVE WITH THE BOARD, and on 14 September 2026 the board moved (AC-S2-16). Left
    on `is_open_demand()` it refused every draft on a delivered or closed line the board had
    just started showing - PUT and DELETE both 422'd, so Confirm never left 0 on exactly the
    order this lane exists for.
    """
    order = (
        db.query(SalesOrder.id)
        .filter(
            SalesOrder.id == sales_order_id,
            SalesOrder.status.in_(["open", "closed"]),
            SalesOrder.demand_class == "project",
        )
        .one_or_none()
    )
    if order is None:
        raise _bad_line(f"{sales_order_id}|{line_no}|{item_code}")

    # CANCEL-STOP-AT-SO: a line AutoCount cancelled is keyed by its mirror's own `line_no`
    # outside the demand set numbered below (an order AutoCount cancelled in full has no
    # demand line left at all). Looked up first so that case resolves; a demand line that
    # ALSO answers to the key makes it ambiguous, refused rather than guessed.
    awaiting = _awaiting_cancel_line(db, sales_order_id, line_no, item_code)
    lines = (
        db.query(SalesOrderLine, Product.product_code)
        .join(Product, Product.id == SalesOrderLine.product_id)
        .filter(SalesOrderLine.sales_order_id == sales_order_id, is_undecided_demand())
        .all()
    )
    if not lines:
        if awaiting is not None:
            return awaiting
        raise _bad_line(f"{sales_order_id}|{line_no}|{item_code}")

    line_ids = [str(line.id) for line, _code in lines]
    # S3 (fix round 5): scoped to the record that HOLDS the core order
    # (`ProjectSalesOrder.so_id.isnot(None)`), the SAME scope `FulfilmentBoardService.
    # _mirror_addressing` numbers the board's own contribution keys against. Unscoped, a
    # core line an AUTHORED PSO also happens to reference (a different subject entirely -
    # see `_mirror_addressing`'s own docstring) could win this dict and number the line
    # differently than the key the board just handed out.
    mirrored: Dict[str, int] = dict(
        db.query(
            ProjectSalesOrderLine.core_sales_order_line_id,
            ProjectSalesOrderLine.line_no,
        )
        .join(
            ProjectSalesOrder,
            ProjectSalesOrder.id == ProjectSalesOrderLine.project_sales_order_id,
        )
        .filter(
            ProjectSalesOrderLine.core_sales_order_line_id.in_(line_ids),
            ProjectSalesOrder.so_id.isnot(None),
        )
        .all()
    )
    entries = [
        LineFacts(str(line.id), line.line_no, line.required_date, product_code or "")
        for line, product_code in lines
    ]
    derived = number_lines(entries)
    numbers = [mirrored.get(line.id) for line, _code in lines]
    if all(number is not None for number in numbers) and len(set(numbers)) == len(numbers):
        derived = {str(line.id): int(mirrored[line.id]) for line, _code in lines}

    for line, product_code in lines:
        if derived.get(str(line.id)) == line_no and product_code == item_code:
            if awaiting is not None:
                raise AppException(
                    status_code=409,
                    message="Two lines of this order answer to that line. Reload the board.",
                    code="board_contribution_line_ambiguous",
                )
            return line
    if awaiting is not None:
        return awaiting
    raise _bad_line(f"{sales_order_id}|{line_no}|{item_code}")


# CANCEL-STOP-AT-SO (owner, 14 Oct 23:20): "when SO is cancelled, we trigger the transfer to
# cancel SO action in fulfilment planning and let the CS confirm themselves". The cancel ingest
# saves the board's own Transfer to Cancel SO as this line's draft, marked with this source so
# a cancel that lowers again withdraws only what the sync itself proposed.
AUTOCOUNT_CANCEL_SOURCE = "autocount_cancel"


def awaits_cancel_so(core_line: SalesOrderLine) -> bool:
    """An AutoCount Cancel SO cancelled the whole line and CS has not transferred it yet: the
    one cancelled line the board still decides (Transfer to Cancel SO only)."""
    return (
        core_line.line_status == "cancelled"
        and bool(core_line.cancelled_by_cancel_doc)
        and core_line.cancelled_at is None
    )


def _awaiting_cancel_line(
    db: Session, sales_order_id: str, line_no: int, item_code: str
) -> Optional[SalesOrderLine]:
    """The key of a line the board shows from `_crm_cancelled_rows` (outside the demand set
    numbered above): its mirror's own `line_no` and its item code, as that read keys it."""
    found = (
        db.query(SalesOrderLine)
        .join(Product, Product.id == SalesOrderLine.product_id)
        .join(
            ProjectSalesOrderLine,
            ProjectSalesOrderLine.core_sales_order_line_id == SalesOrderLine.id,
        )
        .join(
            ProjectSalesOrder,
            ProjectSalesOrder.id == ProjectSalesOrderLine.project_sales_order_id,
        )
        .filter(
            SalesOrderLine.sales_order_id == sales_order_id,
            SalesOrderLine.line_status == "cancelled",
            SalesOrderLine.cancelled_by_cancel_doc.is_(True),
            SalesOrderLine.cancelled_at.is_(None),
            ProjectSalesOrder.so_id.isnot(None),
            ProjectSalesOrderLine.line_no == line_no,
            Product.product_code == item_code,
        )
        .limit(2)
        .all()
    )
    return found[0] if len(found) == 1 else None


def propose_autocount_cancel(
    db: Session, core_line_id: str, doc_no: str, *, actor_user_id: Optional[str]
) -> bool:
    """Save Transfer to Cancel SO as the line's pending decision for CS when an AutoCount
    Cancel SO has just cancelled it and a live order inquiry row still rests on it. Nothing
    else moves: CS confirms it on the board (`ProjectSupplyService.cancel_lines`) or dismisses
    it. Replaces whatever was saved on the line, which no longer has anything to supply.
    True when a proposal was written."""
    core = db.query(SalesOrderLine).filter(SalesOrderLine.id == str(core_line_id)).one_or_none()
    if core is None or not awaits_cancel_so(core):
        return False
    mirror = (
        db.query(ProjectSalesOrderLine)
        .join(
            ProjectSalesOrder,
            ProjectSalesOrder.id == ProjectSalesOrderLine.project_sales_order_id,
        )
        .filter(
            ProjectSalesOrderLine.core_sales_order_line_id == str(core.id),
            ProjectSalesOrder.so_id.isnot(None),
        )
        .first()
    )
    if mirror is None:
        return False
    live = (
        db.query(OrderInquiryRow.id)
        .filter(
            OrderInquiryRow.so_line_id == mirror.id,
            OrderInquiryRow.state != INQUIRY_CANCELLED,
        )
        .first()
    )
    if live is None:
        return False
    item_code = (
        db.query(Product.product_code).filter(Product.id == core.product_id).scalar() or ""
    )
    row = _row_for(db, str(core.id), company_id=core.company_id)
    if row is None:
        row = SOSupplyDecisionDraft(
            id=str(uuid.uuid4()),
            core_line_id=str(core.id),
            sales_order_id=str(core.sales_order_id),
            company_id=core.company_id,
        )
        db.add(row)
    row.line_no = mirror.line_no
    row.item_code = item_code
    row.bucket_key = "no_date"
    row.decision = {
        "verdict": "cancelled",
        "reason": f"AutoCount Cancel SO {doc_no}".strip(),
        "source": AUTOCOUNT_CANCEL_SOURCE,
    }
    row.line_snapshot = _line_snapshot(core)
    row.saved_by = actor_user_id
    row.saved_at = datetime.utcnow()
    db.flush()
    return True


def withdraw_autocount_cancel(db: Session, core_line_ids: Iterable[str]) -> int:
    """The Cancel SO that proposed Transfer to Cancel SO on these lines no longer cancels them
    (lowered, voided, re-decided): drop the proposals the sync saved. A decision CS saved
    themselves stays."""
    ids = [str(i) for i in core_line_ids if i]
    if not ids:
        return 0
    gone = 0
    for row in (
        db.query(SOSupplyDecisionDraft)
        .filter(SOSupplyDecisionDraft.core_line_id.in_(ids))
        .all()
    ):
        if (row.decision or {}).get("source") == AUTOCOUNT_CANCEL_SOURCE:
            db.delete(row)
            gone += 1
    if gone:
        db.flush()
    return gone


def _line_snapshot(line: SalesOrderLine) -> Dict[str, Any]:
    """The LINE's own facts at save time (S1, code review round 3, captain ruling).

    `open_qty` and `required_date` - never the proposal: the proposal depends on which
    orders share the board, its granularity and its window, so a snapshot of it flipped
    stale falsely the moment a planner opened a different view of the same line.

    `open_qty` IS THE PLAN QUANTITY (AC-S2-17), the figure the board shows and the figure a
    composition is balanced against. Frozen as `_open_of` it read 0 on a 3-ordered,
    3-delivered line while the board compared it against 3, so a draft was stale the instant
    it was saved: the pill read "Suggestion changed" and `lineFor` dropped the line, leaving
    Confirm at 0 on the order this lane exists for. What makes a draft stale is the line's
    ASK moving, not a delivery against it.
    """
    return {
        "open_qty": qty_text(plan_qty_of(line)),
        "required_date": line.required_date.isoformat() if line.required_date else None,
    }


def save_draft(
    db: Session,
    key: str,
    *,
    decision: Dict[str, Any],
    proposed: Optional[list] = None,
    actor_user_id: str,
) -> Dict[str, Any]:
    """Upsert the decision saved on one line, and re-stamp who saved it (AC-4.5).

    One row per line, replaced rather than added to: drafts are SHARED (R-F), so a second
    planner saving over the first must leave the board with one answer, named after the
    newer saver.

    S3 (captain ruling): refused with a 422 unless the key names a real line under the
    caller's own company - `_resolve_core_line` is what makes that true, and its own return
    doubles as the LINE this save's `line_snapshot` is taken off (S1).

    `proposed` (D12, #573): the caller's own board contribution `sources` at this moment,
    stored opaque and NEVER read here - see `SOSupplyDecisionDraft.proposed`'s own
    docstring for why it exists and why it is not `line_snapshot`. OMITTING it leaves the
    stored one alone; only a caller that has one replaces it.

    R3(b) REWORK (owner ruling 23 Sep 2026, `PLAN-board-reject-on-confirmed-line.md`,
    hand-test feedback: "we should confirm the rejection"): a `rejected` verdict on a
    covered line is a STAGED decision now, exactly like every other board decision -
    the draft is written here and NOTHING about the active confirmation moves. Confirm is
    what carries the withdrawal (`ConfirmSupplyBody.rejected_line_ids`,
    `app/api/v1/projects/fulfilment_planning.py::confirm_supply`), the same press that
    commits every other decision on the board. `save_draft` NEVER calls `uncover_lines`
    any more - that used to happen here, at click time, which is exactly what the owner's
    ruling took back out. A reason is still required (422
    `board_line_reject_reason_required`), because the reason is what the confirmation
    stamps on the superseded revision, and there is nothing to stamp with a blank one.
    """
    sales_order_id, line_no, item_code, bucket_key = parse_contribution_key(key)
    core_line = _resolve_core_line(db, sales_order_id, line_no, item_code)
    # Only the sync marks its own proposal (`propose_autocount_cancel`); a saved decision is
    # the saver's own, so a reopen never withdraws it.
    decision = {k: v for k, v in decision.items() if k != "source"}
    verdict = decision.get("verdict")
    awaiting = awaits_cancel_so(core_line)
    if awaiting and verdict != "cancelled":
        # CANCEL-STOP-AT-SO: AutoCount cancelled the line; all that is left is to transfer it.
        raise AppException(
            status_code=409,
            message="This line was cancelled in AutoCount. Only Transfer to Cancel SO applies.",
            code="board_line_cancelled_in_autocount",
        )
    # An AutoCount-cancelled line holds nothing (its owed qty is 0), so an active decision
    # still naming it does not stand in the way of transferring it.
    covered = (
        None if verdict == "amended" or awaiting else _active_coverage(db, core_line)
    )
    if covered is not None:
        if verdict == "rejected":
            reason = str(decision.get("reason") or "").strip()
            if not reason:
                raise AppException(
                    status_code=422,
                    message="Say why this line is being refused first.",
                    code="board_line_reject_reason_required",
                )
            # Reason given: falls through to the ordinary draft upsert below, exactly as
            # an uncovered line's rejection already saves. The line stays covered - its
            # OI row stays raised - until Confirm is pressed.
        else:
            raise AppException(
                status_code=409,
                message=CONFIRMED_LINE_MESSAGE,
                code="board_line_already_confirmed",
            )
    row = _row_for(db, str(core_line.id), company_id=core_line.company_id)
    if row is None:
        row = SOSupplyDecisionDraft(
            id=str(uuid.uuid4()),
            core_line_id=str(core_line.id),
            sales_order_id=sales_order_id,
        )
        db.add(row)
    # Re-stamped on every save, because all three move: the board renumbers a line when an
    # earlier one's date changes, an item code follows the line's product, and the bucket
    # follows the granularity the save was made under. They are what this draft was CALLED,
    # never what it is.
    row.line_no = line_no
    row.item_code = item_code
    row.bucket_key = bucket_key
    row.decision = decision
    if proposed is not None:
        # ABSENT is "I am not telling you", never "there is none": a caller with no board
        # contribution to hand (an older client, or a surface that saves the verdict alone)
        # would otherwise erase the suggestion the first save stored, and the Sales Order
        # page's Suggested column would fall back to Decided "-" on a line that had one.
        row.proposed = proposed
    row.line_snapshot = _line_snapshot(core_line)
    row.saved_by = actor_user_id
    row.saved_at = datetime.utcnow()
    db.flush()
    return {
        "decision": row.decision,
        "proposed": row.proposed,
        "saved_by": _saver_name(db, row.saved_by),
        "saved_at": row.saved_at,
        # Just saved against the line's own facts as they stand right now, so nothing about
        # it can have moved yet. The board recomputes it on every read.
        "stale": False,
    }


def remove_draft(db: Session, key: str) -> None:
    """Undo (AC-4.3). A line nobody saved is a 404: there is nothing to undo.

    Resolved through the CORE line, the same way the save is (C2), so an Undo made after
    the board renumbered the line still removes the draft that line actually carries. A key
    naming no line at all is that same 404 rather than the save's 422: this route's whole
    contract is "there is nothing saved here", which is true either way, and the client
    already treats it as "already gone".
    """
    sales_order_id, line_no, item_code, _bucket = parse_contribution_key(key)
    try:
        core_line = _resolve_core_line(db, sales_order_id, line_no, item_code)
    except AppException:
        core_line = None
    row = None if core_line is None else _row_for(db, str(core_line.id))
    if row is None:
        raise AppException(
            status_code=404,
            message="There is no saved decision on that line.",
            code="board_line_draft_not_found",
        )
    db.delete(row)
    db.flush()


def drafts_for_orders(
    db: Session, sales_order_ids: Iterable[str]
) -> Dict[LineKey, Dict[str, Any]]:
    """Every saved decision on these CORE sales orders, keyed by CORE LINE id.

    One query for the whole board, like every other per-board read here, with the saver's
    NAME resolved in it - the pill's popover renders a person, never an id.

    Read by sales order (one indexed predicate for the whole board) and keyed by the line
    (C2): the board stamps a draft onto the row whose `line_id` matches, never onto the row
    that happens to carry the number the save was made under.
    """
    ids = [str(order_id) for order_id in sales_order_ids if order_id]
    if not ids:
        return {}
    rows = (
        db.query(SOSupplyDecisionDraft, User.name)
        .outerjoin(User, User.id == SOSupplyDecisionDraft.saved_by)
        .filter(SOSupplyDecisionDraft.sales_order_id.in_(ids))
        .all()
    )
    return {
        str(row.core_line_id): {
            "decision": row.decision,
            "proposed": row.proposed,
            "saved_by": name or "",
            "saved_at": row.saved_at,
            "line_snapshot": row.line_snapshot,
        }
        for row, name in rows
    }


def delete_drafts_for_lines(
    db: Session,
    core_line_ids: Iterable[Optional[str]],
    *,
    company_id: Optional[str] = None,
) -> int:
    """Drop the drafts a confirmation has just promoted, in ITS transaction.

    Addressed by the CORE sales order line of each confirmed line (C2), which the mirror
    carries as `core_sales_order_line_id`. It used to be the mirror's own `(line_no,
    item_code)`, and on an order the board numbers POSITIONALLY - any order whose lines are
    not all mirrored - the two numberings disagree, so nothing matched and the draft
    survived its own confirmation.

    Per line rather than per order, because a confirmation may cover a SUBSET of an order's
    lines (13.4) and the ones the planner deliberately left undecided keep what they saved.
    """
    ids = {str(line_id) for line_id in core_line_ids if line_id}
    if not ids:
        return 0
    deleted = 0
    for core_line_id in ids:
        row = _row_for(db, core_line_id, company_id=company_id)
        if row is not None:
            db.delete(row)
            deleted += 1
    if deleted:
        db.flush()
    return deleted


def is_stale(
    snapshot: Optional[Dict[str, Any]],
    open_qty: Decimal,
    required_date: Optional[date],
) -> bool:
    """Has this LINE's own facts moved since it was saved (AC-4.4, S1 captain ruling)?

    Judged on the line's own outstanding quantity and required date - NEVER on the
    proposal. The proposal depends on which orders share the board, its granularity and its
    window (`_allocate` draws the shared piles in board order), so comparing PROPOSED
    snapshots flipped `stale` falsely the moment a planner opened a different view of the
    exact same line, and silently dropped it from Confirm. A contribution with no proposal
    at all is never stale on that account, because this predicate never looks at one.

    A draft with NO snapshot is never stale: nothing was written down to compare, and a
    warning with no evidence behind it is worse than no warning.
    """
    if snapshot is None or not isinstance(snapshot, dict):
        return False
    current_qty = qty_text(open_qty)
    current_date = required_date.isoformat() if required_date else None
    return snapshot.get("open_qty") != current_qty or snapshot.get("required_date") != current_date


def _active_coverage(
    db: Session, core_line: SalesOrderLine
) -> Optional[Tuple[ProjectSalesOrder, Dict[str, Any]]]:
    """The mirror ORDER and the LINE SNAPSHOT an active decision covers this core line
    with, or `None` when it is not covered.

    R1 (SO314595, 17 Sep 2026): an outage lost the Confirm response, the planner re-saved
    every line, and the drafts printed Saved over an already-Confirmed line. Covered = an
    ACTIVE `SOSupplyDecision` on the mirror order whose `line_snapshots` names this core
    line, UNLESS the line sits in a planning-change batch nobody has applied yet
    (AC-B8/B9/B10/B11, review round 2).

    Returns the ORDER AND SNAPSHOT rather than a bare bool (S1,
    `PLAN-board-reject-on-confirmed-line.md`, 22 Sep 2026): kept as a pair even after the
    23 Sep rework took the reject seam back out of `save_draft` (it no longer reads either
    one off the snapshot) - `save_draft` only asks `is not None` of it now, and a caller
    that DOES need the order or the snapshot (Confirm's own withdrawal handling,
    `app/api/v1/projects/fulfilment_planning.py`) reads them off the decision it loads for
    itself rather than this function, which stays board-draft-scoped.
    """
    decisions = (
        db.query(SOSupplyDecision, ProjectSalesOrder)
        .join(ProjectSalesOrder, ProjectSalesOrder.id == SOSupplyDecision.project_sales_order_id)
        .filter(
            ProjectSalesOrder.so_id == core_line.sales_order_id,
            SOSupplyDecision.state == DECISION_ACTIVE,
        )
        .all()
    )
    core_line_id = str(core_line.id)
    for decision, order in decisions:
        for snapshot in decision.line_snapshots or []:
            if (snapshot or {}).get("core_line_id") == core_line_id:
                if _in_open_planning_change(db, core_line_id, decision.project_sales_order_id):
                    return None
                return order, snapshot
    return None


def _in_open_planning_change(
    db: Session, core_line_id: str, project_sales_order_id: str
) -> bool:
    """The BATCH is the unit the client uncovers a line on - `FulfilmentBoardPanel` picks
    its surviving batch on `!batch.applied_at` - so the exemption keys on the same fact.
    A row's own state (superseded, failed) is terminal on its own account and must never
    carry the exemption once the batch it sits in is done.
    """
    return (
        db.query(PlanningChangeRow.id)
        .join(PlanningChangeBatch, PlanningChangeBatch.id == PlanningChangeRow.batch_id)
        .filter(
            PlanningChangeRow.core_line_id == core_line_id,
            PlanningChangeRow.project_sales_order_id == project_sales_order_id,
            PlanningChangeBatch.applied_at.is_(None),
        )
        .first()
        is not None
    )


def _row_for(
    db: Session, core_line_id: LineKey, *, company_id: Optional[str] = None
) -> Optional[SOSupplyDecisionDraft]:
    """The one draft row for a CORE sales order line, or `None`.

    `company_id`, when the caller already has it, is an EXPLICIT predicate on top of the
    session's own scope filter (N2, code review round 3): the all-companies principal (no
    `EXTERNAL_API_KEY_ACT_AS_USER_ID`) reads with no company predicate at all, and while the
    line here is already pinned to one company through its own FK, a caller that has already
    resolved it is better placed to say which company than a query left to find out the hard
    way.
    """
    query = db.query(SOSupplyDecisionDraft).filter(
        SOSupplyDecisionDraft.core_line_id == str(core_line_id),
    )
    if company_id:
        query = query.filter(SOSupplyDecisionDraft.company_id == company_id)
    return query.one_or_none()


def _saver_name(db: Session, user_id: Optional[str]) -> str:
    if not user_id:
        return ""
    return db.query(User.name).filter(User.id == user_id).scalar() or ""
