"""Order inquiry: request CS to reserve stock (`PLAN-oi-request-cs-reserve.md`, slices
2/3; `oi-request-cs-reserve-acceptance-criteria.md` AC-RS-1 to AC-RS-21, AC-RS-30/31/32).

R1-R11 (owner, 22 Sep 2026). Purchasing asks CS to cover part of a raised row from own or
pool stock before buying the balance: `create_request` (3.2) writes one
`OrderInquiryReserveRequest` plus one row per named order-inquiry row and dispatches ONE
`order_inquiry_reserve_requested` email (R9); `commit_request` (6e.1, re-keyed by 6e.4) is
Eling's own Confirm, one call per `Reserve` click across the whole inquiry - it writes one
`OrderInquiryLink` per row reserved above zero, naming the reserve request row as its target (the THIRD leg the widened `ck_order_inquiry_links_one_target` CHECK now
allows), refreshes the row's own coverage state through the SAME writer every other link
uses (`ProjectOrderInquiryService.refresh_link_state`), amends already-answered rows
(R4-3), and dispatches one `order_inquiry_reserved` email per request touched.
`cancel_request` (3.2, 6e.4) withdraws a request's still-open rows - no email; rows
already answered keep their links and stay amendable.

**Post-commit dispatch** mirrors `_dispatch_changed_with_links` /
`register_order_inquiry_post_commit_dispatch` in `project_order_inquiry_service.py`
(9355), the SIMPLER of that file's two shapes - not `_fire_pending_handover`'s
transaction-chain bookkeeping, which exists there because ONE write can give several
sibling orders their own savepoint and a failing one must not discard an already-earned
sibling's queued item. Neither `create_request` nor `commit_request` opens a savepoint of
its own, so there is no sibling to protect and the plain queue-on-`Session.info` /
drain-`after_commit` / discard-`after_soft_rollback` shape is the whole of what "simplest
thing that works" asks for here. Context is built EAGERLY, before the commit that queues
it (the same reason `_record_handover` does: several callers run deep inside somebody
else's transaction, and `AutomationService` commits internally, which a fresh session
reads only once the write it is about has actually landed).
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import and_, event, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.inventory import Warehouse
from app.models.project_so import (
    INQUIRY_PARTLY_LINKED,
    INQUIRY_RAISED,
    IV_ORDER,
    IV_ORDER_BACK,
    OrderInquiry,
    OrderInquiryLink,
    OrderInquiryReserveEvent,
    OrderInquiryReserveRequest,
    OrderInquiryReserveRequestRow,
    OrderInquiryRow,
    RESERVE_CANCELLED,
    RESERVE_EVENT_RESERVED,
    RESERVE_EVENT_UNRESERVED,
    RESERVE_REQUESTED,
    RESERVE_RESERVED,
)
from app.services.certificate_service import today_malaysia
from app.services.error_handler import AppException

logger = logging.getLogger(__name__)

_ZERO = Decimal("0")
_REQUESTABLE_VERBS = (IV_ORDER, IV_ORDER_BACK)
_REQUESTABLE_STATES = (INQUIRY_RAISED, INQUIRY_PARTLY_LINKED)


def _dec(value: Any) -> Decimal:
    # SF-9 (security review): `Decimal("nan")`/`Decimal("inf")` construct cleanly -
    # neither raises here - and only blow up (`decimal.InvalidOperation`, an uncaught
    # 500) on the FIRST comparison a caller makes against the result. The schema-level
    # `_finite_qty` validator (`app/schemas/project_order_inquiry.py`) is the real gate
    # for the three request-body fields; this is the belt-and-braces for every other
    # caller of `_dec` (a live `qty`/`qty_requested` read off the row itself, for one),
    # so a non-finite value is treated exactly like the "not a number at all" case
    # already below it, not left to crash three lines downstream.
    if value is None:
        return _ZERO
    if isinstance(value, Decimal):
        return value if value.is_finite() else _ZERO
    try:
        parsed = Decimal(str(value))
    except Exception:  # noqa: BLE001 - a malformed number is data, not a crash
        return _ZERO
    return parsed if parsed.is_finite() else _ZERO


def as_utc(value: Optional[datetime]) -> Optional[datetime]:
    """The reserve tables' timestamps are `TIMESTAMP` without time zone and hold naive
    UTC (`datetime.utcnow()`; no migration wanted to change the columns). Tagging them
    UTC on the way out makes the wire carry an offset, so the browser converts to local
    time instead of printing UTC as if it were local - rows stored before this fix
    included."""
    if value is None or not isinstance(value, datetime):
        return value
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _qty_str(value: Any) -> str:
    """`50`, never `50.0000` - `_qty_str` everywhere else in this domain."""
    return format(_dec(value).normalize(), "f")


def _fmt_date(value) -> Optional[str]:
    """dd/mm/yyyy as the Malaysia calendar has it: a stored timestamp is naive UTC, so
    it moves to Malaysia time first (`pdf_render.in_malaysia`, the shared reader); a
    plain `date` (a delivery date) carries no instant and prints as written."""
    from app.services.pdf_render import in_malaysia

    if value is None:
        return None
    if isinstance(value, datetime):
        value = in_malaysia(value).date()
    return value.strftime("%d/%m/%Y")


def _row_label(row: OrderInquiryRow) -> str:
    return row.item_code or str(row.id)


def _remaining(db: Session, row: OrderInquiryRow) -> Decimal:
    """`row.qty - sum(links.qty) - bundled_qty` (plan 3.2) - the row's own arithmetic,
    not the worklist's line-outstanding-capped `_UNLINKED_QTY`: a reserve request is
    about what THIS row itself still has open, the same way the row's own qty is what
    the request caps `qty_requested` against (AC-RS-3)."""
    linked = db.query(func.coalesce(func.sum(OrderInquiryLink.qty), 0)).filter(
        OrderInquiryLink.row_id == row.id
    ).scalar()
    return _dec(row.qty) - _dec(linked) - _dec(row.bundled_qty)


def _default_warehouse_id(db: Session, stock_location: Optional[str]) -> Optional[str]:
    """The pool of `stock_location` (R3): `BRW-BB` -> `BRW`'s own id; a bare pool code
    resolves to itself, since it names no `pool_warehouse_id` of its own."""
    if not stock_location:
        return None
    warehouse = (
        db.query(Warehouse).filter(Warehouse.warehouse_code == stock_location).first()
    )
    if warehouse is None:
        return None
    return warehouse.pool_warehouse_id or warehouse.id


def _validated_warehouse_id(
    db: Session, warehouse_id: Optional[str], row_label: str
) -> Optional[str]:
    """SF-3 (review round): `warehouse_id` - whichever of the two writers sets it,
    explicit on the payload or R3's own default - must resolve through the SAME
    company-scoped ORM query every other reader of `Warehouse` uses, to an ACTIVE row,
    or 422 naming the row. A foreign-company id is invisible to this query already
    (`do_orm_execute`'s own company-scope filter), so it reads exactly like a bad id
    rather than needing a second, explicit tenant check."""
    if not warehouse_id:
        return None
    warehouse = db.query(Warehouse).filter(Warehouse.id == warehouse_id).first()
    if warehouse is None or not warehouse.is_active:
        raise AppException(
            422,
            f"{row_label}: choose an active warehouse.",
            code="reserve_bad_warehouse",
        )
    return warehouse.id


def _warehouse_code(db: Session, warehouse_id: Optional[str]) -> Optional[str]:
    if not warehouse_id:
        return None
    warehouse = db.query(Warehouse).filter(Warehouse.id == warehouse_id).first()
    return warehouse.warehouse_code if warehouse is not None else None


def _person(db: Session, user_id: Optional[str]) -> Optional[Dict[str, str]]:
    """`{name, email}`, or `None` - the same shape `_handover_actor` builds
    (`project_order_inquiry_service.py:3150`)."""
    if not user_id:
        return None
    from app.models.user import User

    user = db.query(User).filter(User.id == user_id).first()
    if user is None or not user.email:
        return None
    return {"name": user.name or user.email, "email": user.email}


def _actor_name(db: Session, user_id: Optional[str]) -> Optional[str]:
    """A history entry's own `actor_name` (F3): a human name or email, never a UUID
    (CLAUDE.md Cursor rules) - the same reading `_serialize_reserve_request`'s local
    `_name` helper uses for `requested_by_name`/`reserved_by_name`."""
    if not user_id:
        return None
    from app.models.user import User

    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        return None
    return user.name or user.email


def _strict_qty(value: Any, row: OrderInquiryRow) -> Decimal:
    """A commit's own quantity, strictly: `_dec` reads a non-finite or malformed value
    as 0, which would pass as a legal "Reserve 0" here - so this refuses it instead
    (the schema's `_finite_qty` is the route's gate; this is the service's own)."""
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value))
    except Exception:  # noqa: BLE001 - a malformed number is a 422, not a crash
        parsed = None
    # Security re-review: `Numeric(15,4)` holds at most 11 integer digits and 4
    # decimals; outside that the write would round (0.00001 reads 0) or overflow (a 500).
    # `adjusted()` is checked first so `quantize` never sees a value it cannot hold.
    if (
        parsed is None
        or not parsed.is_finite()
        or parsed.adjusted() > 10
        or parsed != parsed.quantize(Decimal("0.0001"))
    ):
        raise AppException(
            422,
            f"{_row_label(row)}: enter a quantity with at most 4 decimal places.",
            code="reserve_qty_invalid",
        )
    return parsed


def _checked_answer(
    row: OrderInquiryRow,
    entry: Dict[str, Any],
    *,
    cap: Decimal,
    reason_below: Decimal,
    out_of_range_code: str,
    noun: str,
) -> tuple:
    """The ONE validator both `commit_request` lists share (6e.4, reviewer S9): the
    quantity sits in `0..cap`, and a reason is required whenever it is short of
    `reason_below`. Returns `(qty, reason_clean)`."""
    qty = _strict_qty(entry.get("qty_reserved"), row)
    if qty < _ZERO or qty > cap:
        raise AppException(
            422,
            f"{_row_label(row)}: {noun} quantity must be between 0 and {_qty_str(cap)}.",
            code=out_of_range_code,
        )
    reason_clean = (entry.get("reason") or "").strip() or None
    if qty < reason_below and not reason_clean:
        raise AppException(
            422,
            f"{_row_label(row)}: a reason is required when the {noun} quantity is short "
            "of requested.",
            code="reserve_reason_required",
        )
    return qty, reason_clean


def reserve_status_predicates() -> Dict[str, Any]:
    """OI-STAGED-NOTIFY (AC-SN-30): the four statuses of one request row, as SQL predicates
    over `OrderInquiryReserveRequestRow` joined to its `OrderInquiryReserveRequest`.
    `needs_reserve` / `parked` = unanswered on an open request, sent / parked;
    `notice_parked` / `reserved` = answered, notice to purchasing parked / not."""
    rr, req = OrderInquiryReserveRequestRow, OrderInquiryReserveRequest
    unanswered = and_(rr.qty_reserved.is_(None), req.state == RESERVE_REQUESTED)
    return {
        "parked": and_(unanswered, req.parked_at.isnot(None)),
        "needs_reserve": and_(unanswered, req.parked_at.is_(None)),
        "notice_parked": and_(rr.qty_reserved.isnot(None), rr.notice_parked_at.isnot(None)),
        "reserved": and_(rr.qty_reserved.isnot(None), rr.notice_parked_at.is_(None)),
    }


def _open_request_row_ids(db: Session, row_ids: Sequence[str]) -> set:
    if not row_ids:
        return set()
    rows = (
        db.query(OrderInquiryReserveRequestRow.row_id)
        .join(
            OrderInquiryReserveRequest,
            OrderInquiryReserveRequest.id == OrderInquiryReserveRequestRow.request_id,
        )
        .filter(
            OrderInquiryReserveRequestRow.row_id.in_(list(row_ids)),
            # 6e.4 (reviewer S4): an ANSWERED row of a request still open for its
            # siblings is not open itself - its balance may be requested again.
            OrderInquiryReserveRequestRow.qty_reserved.is_(None),
            OrderInquiryReserveRequest.state == RESERVE_REQUESTED,
        )
        .all()
    )
    return {row_id for (row_id,) in rows}


def _row_context(db: Session, row: OrderInquiryRow, rr: OrderInquiryReserveRequestRow) -> Dict[str, Any]:
    """One `reserve.rows[]` entry, read fresh off the row/request-row at dispatch time -
    the same field set both the request and the reserved template read (3.6): `remaining`
    doubles as the request mail's REMAINING and the reserved mail's BALANCE, since the two
    templates print it at different MOMENTS (before the request changes nothing, after the
    reserve link has already landed) rather than under two different keys."""
    return {
        "item_code": row.item_code,
        "delivery_date": _fmt_date(row.delivery_date),
        "qty": _qty_str(row.qty),
        "remaining": _qty_str(_remaining(db, row)),
        "qty_requested": _qty_str(rr.qty_requested),
        "location": _warehouse_code(db, rr.warehouse_id),
        "qty_reserved": _qty_str(rr.qty_reserved) if rr.qty_reserved is not None else None,
        "reason": rr.reason,
    }


def _build_context(
    db: Session,
    request: OrderInquiryReserveRequest,
    pairs: Sequence[tuple],
    *,
    actor_user_id: Optional[str],
) -> Dict[str, Any]:
    """The shared context both triggers dispatch with (plan 3.6): `actor` is whoever DID
    this action (the requester for the request event, the reserver for the reserved
    event); `requester` is always the request's own `requested_by`, so the reserved
    email's `include_requester` keeps naming the ORIGINAL asker even though `actor` has
    moved on to Eling."""
    from app.services.automation_triggers import build_order_inquiry_link
    from app.services.project_order_inquiry_service import ProjectOrderInquiryService

    inquiry = (
        db.query(OrderInquiry).filter(OrderInquiry.id == request.order_inquiry_id).first()
    )
    facts: Dict[str, Any] = {}
    if inquiry is not None:
        facts = ProjectOrderInquiryService(db)._handover_order_facts(
            inquiry.project_sales_order_id
        )
    rows_ctx = [_row_context(db, row, rr) for rr, row in pairs]
    link = f"{build_order_inquiry_link(request.order_inquiry_id)}?reserve={request.id}"
    reserve = {
        "inquiry_no": inquiry.inquiry_no if inquiry is not None else None,
        "ordinal": request.ordinal,
        "so_number": facts.get("so_number"),
        "customer": facts.get("customer"),
        "project": facts.get("project"),
        "requested_by": _person(db, request.requested_by),
        "requested_at": _fmt_date(request.requested_at),
        "note": request.note,
        "rows": rows_ctx,
        "row_count": len(rows_ctx),
        "state": request.state,
        "link": link,
    }
    return {
        "reserve": reserve,
        "actor": _person(db, actor_user_id),
        "raiser": _person(db, inquiry.raised_by) if inquiry is not None else None,
        "requester": _person(db, request.requested_by),
        "today": today_malaysia().strftime("%d/%m/%Y"),
    }


def _build_commit_context(
    db: Session,
    request: OrderInquiryReserveRequest,
    touched: Sequence[Dict[str, Any]],
    *,
    open_row_count: int,
    row_count: int,
    actor_user_id: Optional[str],
) -> Dict[str, Any]:
    """`commit_request`'s own dispatch context (6e.1) - `reserve.rows` is ONLY the rows
    THIS call touched (reserved or amended), never the whole request (`_build_context`'s
    own shape, which the request/completion-era dispatches still use). Each row entry
    carries `balance` (the plan's own word - what is left to buy AFTER this call, read
    fresh off the row/links once every write above has landed) rather than `_row_context`'s
    `remaining` (read at a different moment for the other two dispatches); `remaining` is
    ALSO carried, same value, so the one seeded reserved-mail template's existing
    `row.remaining` (its BALANCE column) keeps rendering unchanged."""
    from app.services.automation_triggers import build_order_inquiry_link
    from app.services.project_order_inquiry_service import ProjectOrderInquiryService

    inquiry = (
        db.query(OrderInquiry).filter(OrderInquiry.id == request.order_inquiry_id).first()
    )
    facts: Dict[str, Any] = {}
    if inquiry is not None:
        facts = ProjectOrderInquiryService(db)._handover_order_facts(
            inquiry.project_sales_order_id
        )

    rows_ctx = []
    for item in touched:
        rr = item["rr"]
        row = item["row"]
        balance = _qty_str(_remaining(db, row))
        rows_ctx.append(
            {
                "item_code": row.item_code,
                "delivery_date": _fmt_date(row.delivery_date),
                "qty": _qty_str(row.qty),
                "qty_requested": _qty_str(rr.qty_requested),
                "location": _warehouse_code(db, rr.warehouse_id),
                "qty_reserved": _qty_str(rr.qty_reserved) if rr.qty_reserved is not None else None,
                "balance": balance,
                "remaining": balance,
                "reason": rr.reason,
            }
        )

    link = f"{build_order_inquiry_link(request.order_inquiry_id)}?reserve={request.id}"
    reserve = {
        "inquiry_no": inquiry.inquiry_no if inquiry is not None else None,
        "ordinal": request.ordinal,
        "so_number": facts.get("so_number"),
        "customer": facts.get("customer"),
        "project": facts.get("project"),
        "requested_by": _person(db, request.requested_by),
        "requested_at": _fmt_date(request.requested_at),
        "note": request.note,
        "rows": rows_ctx,
        "row_count": row_count,
        "open_row_count": open_row_count,
        "state": request.state,
        "link": link,
    }
    return {
        "reserve": reserve,
        "actor": _person(db, actor_user_id),
        "raiser": _person(db, inquiry.raised_by) if inquiry is not None else None,
        "requester": _person(db, request.requested_by),
        "today": today_malaysia().strftime("%d/%m/%Y"),
    }


class OrderInquiryReserveService:
    def __init__(self, db: Session):
        self.db = db

    # ------------------------------------------------------------ 3.2 request

    def create_request(
        self,
        *,
        inquiry_id: str,
        rows: Sequence[Dict[str, Any]],
        note: Optional[str],
        actor_user_id: Optional[str],
        park: bool = False,
    ) -> OrderInquiryReserveRequest:
        inquiry = self.db.query(OrderInquiry).filter(OrderInquiry.id == inquiry_id).first()
        if inquiry is None:
            raise AppException(
                404, "This order inquiry no longer exists.", code="order_inquiry_not_found"
            )
        if not rows:
            raise AppException(
                422, "Select at least one row to request.", code="reserve_request_no_rows"
            )

        row_ids = [str(entry["row_id"]) for entry in rows]
        by_id = {
            row.id: row
            for row in self.db.query(OrderInquiryRow)
            .filter(OrderInquiryRow.id.in_(row_ids))
            .all()
        }
        already_open = _open_request_row_ids(self.db, row_ids)

        prepared: List[tuple] = []
        for entry in rows:
            row_id = str(entry["row_id"])
            row = by_id.get(row_id)
            if row is None or str(row.order_inquiry_id) != str(inquiry_id):
                raise AppException(
                    409,
                    f"Row {row_id} does not belong to this order inquiry.",
                    code="reserve_request_wrong_inquiry",
                )
            if row.verb not in _REQUESTABLE_VERBS:
                raise AppException(
                    409,
                    f"{_row_label(row)} is not an ORDER / ORDER BACK row and cannot be "
                    "requested for reserve.",
                    code="reserve_request_wrong_verb",
                )
            if row.state not in _REQUESTABLE_STATES:
                raise AppException(
                    409,
                    f"{_row_label(row)} is not open for a reserve request.",
                    code="reserve_request_wrong_state",
                )
            if row_id in already_open:
                raise AppException(
                    409,
                    f"{_row_label(row)} already has an open reserve request.",
                    code="reserve_request_already_open",
                )
            remaining = _remaining(self.db, row)
            if remaining <= _ZERO:
                raise AppException(
                    409,
                    f"{_row_label(row)} has nothing left to request.",
                    code="reserve_request_nothing_remaining",
                )
            qty_requested = _dec(entry.get("qty_requested"))
            if qty_requested <= _ZERO or qty_requested > remaining:
                raise AppException(
                    422,
                    f"{_row_label(row)}: requested quantity must be more than 0 and at "
                    f"most {_qty_str(remaining)}.",
                    code="reserve_request_qty_out_of_range",
                )
            warehouse_id = entry.get("warehouse_id") or _default_warehouse_id(
                self.db, row.stock_location
            )
            warehouse_id = _validated_warehouse_id(self.db, warehouse_id, _row_label(row))
            prepared.append((row, qty_requested, warehouse_id))

        ordinal = (
            self.db.query(func.coalesce(func.max(OrderInquiryReserveRequest.ordinal), 0))
            .filter(OrderInquiryReserveRequest.order_inquiry_id == inquiry_id)
            .scalar()
            or 0
        ) + 1

        request = OrderInquiryReserveRequest(
            id=str(uuid.uuid4()),
            company_id=inquiry.company_id,
            order_inquiry_id=inquiry_id,
            ordinal=ordinal,
            state=RESERVE_REQUESTED,
            requested_by=actor_user_id,
            # Explicit, not the column's own `server_default=func.now()`: several
            # writes in this feature's own lifecycle (a request, then a reserve, then
            # an unreserve) commonly land in the SAME outer Postgres transaction
            # (`tests/_pg_fixture.py`'s savepoint mode; a real request can share one
            # too), and `now()` is frozen for the whole transaction block
            # (LESSONS-LEARNT "now() ties in transaction") - the History tab's own
            # "newest first" would tie every entry to one instant and fall back to
            # insertion order, which is requested-first, never what the reader wants.
            requested_at=datetime.utcnow(),
            note=note,
            # OI-STAGED-NOTIFY: parked = saved, not mailed to CS until "send all parked".
            parked_at=datetime.utcnow() if park else None,
        )
        self.db.add(request)
        try:
            self.db.flush()
        except IntegrityError:
            # SF-8 (review round): a genuine race - another session minted this SAME
            # ordinal between the read above and this INSERT. The partial/unique index
            # (`uq_order_inquiry_reserve_requests_ordinal`) is what caught it; rolling
            # back to the savepoint (same shape `form_action_service.py`'s own pending-
            # action park uses) keeps the session usable for the caller's 409, never an
            # unhandled 500. Simpler than a re-mint-and-retry: the loser's request is
            # small enough that "click again" costs nothing, and a blind retry here
            # cannot tell a genuine collision apart from a real duplicate ordinal bug.
            self.db.rollback()
            raise AppException(
                409,
                "Another reserve request was just raised for this inquiry. Try again.",
                code="reserve_request_ordinal_collision",
            )

        pairs = []
        for row, qty_requested, warehouse_id in prepared:
            rr = OrderInquiryReserveRequestRow(
                id=str(uuid.uuid4()),
                company_id=inquiry.company_id,
                request_id=request.id,
                row_id=row.id,
                qty_requested=qty_requested,
                warehouse_id=warehouse_id,
            )
            self.db.add(rr)
            pairs.append((rr, row))
        self.db.flush()

        if park:
            return request
        context = _build_context(self.db, request, pairs, actor_user_id=actor_user_id)
        self.db.info.setdefault(_REQUESTED_PENDING_KEY, []).append(
            {"context": context, "source_id": str(request.id)}
        )
        return request

    # ------------------------------------------------------------- 3.2 cancel

    def cancel_request(
        self,
        *,
        request_id: str,
        actor_user_id: Optional[str],
        actor_can_reserve: bool = False,
    ) -> OrderInquiryReserveRequest:
        """SF-1 (review round): the route/deferred-action gate only proves the actor
        holds ONE of the two purchasing-side grants (`ACKNOWLEDGE`/`RESERVE`) - it says
        nothing about whether THIS request is theirs. Ownership is checked HERE: the
        requester who raised it, or anyone holding the reserve permission (Eling may
        always withdraw an ask nobody has answered yet), never a colleague who merely
        also holds `ACKNOWLEDGE`. `actor_can_reserve` is the caller's own fact -
        computed at the route for an immediate cancel, and recomputed at commit time for
        the deferred one (`record_actions.py`) - never re-derived here, so this method
        stays a pure permission-free ownership check."""
        # 6e.4 (security S2): locked, so a cancel and a commit on the same request
        # serialize - the commit re-reads the state under its own lock.
        request = (
            self.db.query(OrderInquiryReserveRequest)
            .filter(OrderInquiryReserveRequest.id == request_id)
            .with_for_update()
            .first()
        )
        if request is None:
            raise AppException(
                404,
                "This reserve request no longer exists.",
                code="reserve_request_not_found",
            )
        # 6e.4 (security S3): allowed while partly answered - only the still-open rows
        # are withdrawn; answered rows keep their links and may still be amended.
        if request.state != RESERVE_REQUESTED:
            raise AppException(
                409,
                "Only an open reserve request can be cancelled.",
                code="reserve_request_not_cancellable",
            )
        is_requester = (
            actor_user_id is not None and str(actor_user_id) == str(request.requested_by)
        )
        if not (is_requester or actor_can_reserve):
            raise AppException(
                403,
                "Only the person who requested this, or CS, may cancel it.",
                code="reserve_request_cancel_forbidden",
            )
        request.state = RESERVE_CANCELLED
        request.cancelled_by = actor_user_id
        request.cancelled_at = datetime.utcnow()
        request.parked_at = None
        self.db.flush()
        return request

    # --------------------------------------------------- 6e.1 / 6e.4: commit, one call

    def commit_request(
        self,
        *,
        inquiry_id: str,
        reserves: Sequence[Dict[str, Any]],
        amendments: Sequence[Dict[str, Any]],
        actor_user_id: Optional[str],
        park_notice: bool = False,
    ) -> List[OrderInquiryReserveRequest]:
        """CS commits every staged line of ONE order inquiry in ONE call, ONE
        transaction (`PLAN-oi-request-cs-reserve.md` 6e.1, re-keyed by 6e.4). The caller
        names order-inquiry rows only; each is resolved HERE, inside `inquiry_id`:
        a `reserves` row to its OPEN request row, an `amendments` row to its LATEST
        answered request row (which may sit on a finished or cancelled request, 6e.4
        cancel semantics). Everything is validated before anything is written; one
        `order_inquiry_reserved` dispatch is queued per request touched, naming only
        that request's own touched rows. Returns the touched requests, ordinal order."""
        from app.services.project_order_inquiry_service import ProjectOrderInquiryService

        reserves = list(reserves or [])
        amendments = list(amendments or [])
        if not reserves and not amendments:
            raise AppException(
                422,
                "Select at least one line to reserve or amend.",
                code="reserve_commit_empty",
            )
        # 6e.4 (AC-RS-76c): a row named twice is refused before anything is read.
        row_ids = [str(entry["row_id"]) for entry in reserves] + [
            str(entry["row_id"]) for entry in amendments
        ]
        seen: set = set()
        for row_id in row_ids:
            if row_id in seen:
                # Named by item code when the row loads (6e.4 re-review); a ghost id
                # still reads as itself.
                named = (
                    self.db.query(OrderInquiryRow).filter(OrderInquiryRow.id == row_id).first()
                )
                label = _row_label(named) if named is not None else f"Row {row_id}"
                raise AppException(
                    422,
                    f"{label} is named more than once in this commit.",
                    code="reserve_commit_duplicate_row",
                )
            seen.add(row_id)

        inquiry = self.db.query(OrderInquiry).filter(OrderInquiry.id == inquiry_id).first()
        if inquiry is None:
            raise AppException(
                404, "This order inquiry no longer exists.", code="order_inquiry_not_found"
            )
        rows_by_id = {
            row.id: row
            for row in self.db.query(OrderInquiryRow)
            .filter(OrderInquiryRow.id.in_(row_ids))
            .all()
        }
        for row_id in row_ids:
            row = rows_by_id.get(row_id)
            if row is None or str(row.order_inquiry_id) != str(inquiry.id):
                raise AppException(
                    404,
                    "That line is not part of this order inquiry.",
                    code="reserve_commit_row_not_found",
                )

        # ---- resolve each row to its request row (unlocked read; re-checked below) ----
        reserve_rr_ids: Dict[str, str] = {}
        for entry in reserves:
            row = rows_by_id[str(entry["row_id"])]
            open_rr = (
                self.db.query(OrderInquiryReserveRequestRow)
                .join(
                    OrderInquiryReserveRequest,
                    OrderInquiryReserveRequest.id == OrderInquiryReserveRequestRow.request_id,
                )
                .filter(
                    OrderInquiryReserveRequestRow.row_id == row.id,
                    OrderInquiryReserveRequestRow.qty_reserved.is_(None),
                    OrderInquiryReserveRequest.state == RESERVE_REQUESTED,
                )
                .order_by(OrderInquiryReserveRequest.ordinal.desc())
                .first()
            )
            if open_rr is None:
                raise AppException(
                    409,
                    f"{_row_label(row)} has no open reserve request to answer.",
                    code="reserve_commit_no_open_request",
                )
            reserve_rr_ids[row.id] = open_rr.id
        # 6e.4 "Amend edits the LINE's net": every answered request row of the line,
        # newest request first - a decrease releases in this order, an increase lands
        # on the first (the latest answered).
        amend_rr_ids: Dict[str, List[str]] = {}
        for entry in amendments:
            row = rows_by_id[str(entry["row_id"])]
            answered = (
                self.db.query(OrderInquiryReserveRequestRow.id)
                .join(
                    OrderInquiryReserveRequest,
                    OrderInquiryReserveRequest.id == OrderInquiryReserveRequestRow.request_id,
                )
                .filter(
                    OrderInquiryReserveRequestRow.row_id == row.id,
                    OrderInquiryReserveRequestRow.qty_reserved.isnot(None),
                )
                .order_by(OrderInquiryReserveRequest.ordinal.desc())
                .all()
            )
            if not answered:
                raise AppException(
                    422,
                    f"{_row_label(row)} has not been reserved yet - answer it first.",
                    code="reserve_amend_not_reserved",
                )
            amend_rr_ids[row.id] = [rr_id for (rr_id,) in answered]

        # ---- lock: requests first, then request rows, each ordered by id (6e.4) ----
        all_rr_ids = sorted(
            set(reserve_rr_ids.values())
            | {rr_id for rr_ids in amend_rr_ids.values() for rr_id in rr_ids}
        )
        request_ids = sorted(
            {
                request_id
                for (request_id,) in self.db.query(OrderInquiryReserveRequestRow.request_id)
                .filter(OrderInquiryReserveRequestRow.id.in_(all_rr_ids))
                .all()
            }
        )
        requests_by_id = {
            request.id: request
            for request in self.db.query(OrderInquiryReserveRequest)
            .filter(OrderInquiryReserveRequest.id.in_(request_ids))
            .order_by(OrderInquiryReserveRequest.id.asc())
            .with_for_update()
            .populate_existing()
            .all()
        }
        rr_by_id = {
            rr.id: rr
            for rr in self.db.query(OrderInquiryReserveRequestRow)
            .filter(OrderInquiryReserveRequestRow.id.in_(all_rr_ids))
            .order_by(OrderInquiryReserveRequestRow.id.asc())
            .with_for_update()
            .populate_existing()
            .all()
        }

        # ---- validate everything; write nothing until every entry passes ----
        prepared_reserves: List[tuple] = []
        for entry in reserves:
            row = rows_by_id[str(entry["row_id"])]
            rr = rr_by_id[reserve_rr_ids[row.id]]
            request = requests_by_id[rr.request_id]
            # Re-checked under the lock: another commit or a cancel may have landed
            # between the resolving read above and the lock.
            if request.state != RESERVE_REQUESTED or rr.qty_reserved is not None:
                raise AppException(
                    409,
                    f"{_row_label(row)} has no open reserve request to answer.",
                    code="reserve_commit_no_open_request",
                )
            cap = min(_dec(rr.qty_requested), _remaining(self.db, row))
            qty, reason_clean = _checked_answer(
                row,
                entry,
                cap=cap,
                reason_below=cap,
                out_of_range_code="reserve_qty_out_of_range",
                noun="reserved",
            )
            warehouse_id = entry.get("warehouse_id") or rr.warehouse_id
            warehouse_id = _validated_warehouse_id(self.db, warehouse_id, _row_label(row))
            prepared_reserves.append((rr, row, qty, reason_clean, warehouse_id))

        prepared_amendments: List[tuple] = []
        for entry in amendments:
            row = rows_by_id[str(entry["row_id"])]
            answered = [
                rr_by_id[rr_id]
                for rr_id in amend_rr_ids[row.id]
                if rr_by_id[rr_id].qty_reserved is not None
            ]
            if not answered:
                raise AppException(
                    422,
                    f"{_row_label(row)} has not been reserved yet - answer it first.",
                    code="reserve_amend_not_reserved",
                )
            links_by_rr = {
                link.reserve_request_row_id: link
                for link in self.db.query(OrderInquiryLink)
                .filter(OrderInquiryLink.reserve_request_row_id.in_([rr.id for rr in answered]))
                .all()
            }
            current_net = sum((_dec(link.qty) for link in links_by_rr.values()), _ZERO)
            new_net = _strict_qty(entry.get("qty_reserved"), row)
            # 6e.4 (security N2): a no-op is skipped outright - no event, no mail, and
            # the reason on file is left as it was.
            if new_net == current_net:
                continue
            # The line may grow by what it still has open, never past its own qty.
            cap = current_net + _remaining(self.db, row)
            # What the line has asked for, counting a later balance request once: the
            # net kept on every earlier answered row plus the latest row's own ask,
            # capped at the line's qty (36 asked / 10 got, then 26 asked = 36, not 62).
            total_requested = min(
                sum((_dec(rr.qty_reserved) for rr in answered[1:]), _ZERO)
                + _dec(answered[0].qty_requested),
                _dec(row.qty),
            )
            new_net, reason_clean = _checked_answer(
                row,
                entry,
                cap=cap,
                reason_below=total_requested,
                out_of_range_code="reserve_amend_qty_out_of_range",
                noun="amended",
            )
            prepared_amendments.append((row, answered, links_by_rr, current_net, new_net, reason_clean))

        # ---- everything validated - now write ----
        # Nit N-a: row id order, so two concurrent commits on one inquiry take the row
        # locks `refresh_link_state` needs in the same order (no deadlock).
        prepared_reserves.sort(key=lambda item: str(item[1].id))
        prepared_amendments.sort(key=lambda item: str(item[0].id))
        refresher = ProjectOrderInquiryService(self.db)
        touched_by_request: Dict[str, List[Dict[str, Any]]] = {}

        for rr, row, qty, reason_clean, warehouse_id in prepared_reserves:
            rr.qty_reserved = qty
            rr.reason = reason_clean
            rr.warehouse_id = warehouse_id
            if qty > _ZERO:
                code = _warehouse_code(self.db, warehouse_id) or ""
                self.db.add(
                    OrderInquiryLink(
                        id=str(uuid.uuid4()),
                        company_id=row.company_id,
                        row_id=row.id,
                        reserve_request_row_id=rr.id,
                        document=f"Reserved @ {code}",
                        qty=qty,
                        linked_by=actor_user_id,
                        auto=False,
                    )
                )
            # 6e.4 (security N3): Reserve 0 writes its event too, so History shows it.
            self._add_event(row, rr, RESERVE_EVENT_RESERVED, qty, warehouse_id, reason_clean, actor_user_id)
            self._flush_link()
            refresher.refresh_link_state([row])
            touched_by_request.setdefault(rr.request_id, []).append({"rr": rr, "row": row})

        for row, answered, links_by_rr, current_net, new_net, reason_clean in prepared_amendments:
            touched_rrs: List[OrderInquiryReserveRequestRow] = []
            if new_net < current_net:
                # Release newest request first, one `unreserved` event per link touched.
                release = current_net - new_net
                for rr in answered:
                    if release <= _ZERO:
                        break
                    link = links_by_rr.get(rr.id)
                    if link is None or _dec(link.qty) <= _ZERO:
                        continue
                    take = min(_dec(link.qty), release)
                    left = _dec(link.qty) - take
                    if left > _ZERO:
                        link.qty = left
                    else:
                        self.db.delete(link)
                    rr.qty_reserved = _dec(rr.qty_reserved) - take
                    self._add_event(
                        row, rr, RESERVE_EVENT_UNRESERVED, take, rr.warehouse_id, reason_clean, actor_user_id
                    )
                    release -= take
                    touched_rrs.append(rr)
                # The reason lands on the row whose link was touched last.
                touched_rrs[-1].reason = reason_clean
            else:
                # Raise the latest answered request row's link (re-created from 0).
                rr = answered[0]
                delta = new_net - current_net
                link = links_by_rr.get(rr.id)
                if link is not None:
                    link.qty = _dec(link.qty) + delta
                else:
                    code = _warehouse_code(self.db, rr.warehouse_id) or ""
                    self.db.add(
                        OrderInquiryLink(
                            id=str(uuid.uuid4()),
                            company_id=row.company_id,
                            row_id=row.id,
                            reserve_request_row_id=rr.id,
                            document=f"Reserved @ {code}",
                            qty=delta,
                            linked_by=actor_user_id,
                            auto=False,
                        )
                    )
                rr.qty_reserved = _dec(rr.qty_reserved) + delta
                rr.reason = reason_clean
                self._add_event(
                    row, rr, RESERVE_EVENT_RESERVED, delta, rr.warehouse_id, reason_clean, actor_user_id
                )
                touched_rrs.append(rr)
            self._flush_link()
            refresher.refresh_link_state([row])
            for rr in touched_rrs:
                touched_by_request.setdefault(rr.request_id, []).append({"rr": rr, "row": row})

        # ---- per request touched: complete it when its last row is answered, queue
        # its own dispatch ----
        touched_requests = sorted(
            (requests_by_id[request_id] for request_id in touched_by_request),
            key=lambda request: request.ordinal,
        )
        for request in touched_requests:
            still_open = (
                self.db.query(OrderInquiryReserveRequestRow)
                .filter(
                    OrderInquiryReserveRequestRow.request_id == request.id,
                    OrderInquiryReserveRequestRow.qty_reserved.is_(None),
                )
                .count()
            )
            if request.state == RESERVE_REQUESTED and still_open == 0:
                request.state = RESERVE_RESERVED
                request.reserved_by = actor_user_id
                request.reserved_at = datetime.utcnow()
                request.parked_at = None
                self.db.flush()
            self._stamp_notice(touched_by_request[request.id], park_notice, actor_user_id)
            if park_notice:
                # OI-STAGED-NOTIFY: parked notices are mailed by "send all parked".
                continue
            total_row_count = (
                self.db.query(OrderInquiryReserveRequestRow)
                .filter(OrderInquiryReserveRequestRow.request_id == request.id)
                .count()
            )
            context = _build_commit_context(
                self.db,
                request,
                touched_by_request[request.id],
                # A cancelled request's open rows were withdrawn, not left to reserve.
                open_row_count=still_open if request.state == RESERVE_REQUESTED else 0,
                row_count=total_row_count,
                actor_user_id=actor_user_id,
            )
            self.db.info.setdefault(_RESERVED_PENDING_KEY, []).append(
                {"context": context, "source_id": str(request.id)}
            )
        # Empty when every entry was a no-op: the page reads that as "Nothing to change".
        return touched_requests

    def _stamp_notice(
        self,
        touched: Sequence[Dict[str, Any]],
        park_notice: bool,
        actor_user_id: Optional[str],
    ) -> None:
        """OI-STAGED-NOTIFY: after a commit's writes, per touched request row. Parked: the
        row is flagged with who parked it, unless the answer now equals what purchasing was
        last told (an amend back to the sent figure drops out). Not parked: today's
        dispatch follows, so the row is now "told" this figure and any earlier park is void."""
        now = datetime.utcnow()
        for item in touched:
            rr = item["rr"]
            if park_notice:
                if rr.notified_qty is not None and _dec(rr.qty_reserved) == _dec(rr.notified_qty):
                    rr.notice_parked_at = None
                    rr.notice_parked_by = None
                else:
                    rr.notice_parked_at = now
                    rr.notice_parked_by = actor_user_id
            else:
                rr.notified_qty = rr.qty_reserved
                rr.notice_parked_at = None
                rr.notice_parked_by = None
        self.db.flush()

    # ------------------------------------------- OI-STAGED-NOTIFY: send all parked

    def _request_items(self, actor_user_id: str, *, lock: bool = False):
        """The caller's own parked requests as `park_send` items, plus the parked requests
        with nothing left open (cleared without an email). Items carry only unanswered rows."""
        query = self.db.query(OrderInquiryReserveRequest).filter(
            OrderInquiryReserveRequest.requested_by == actor_user_id,
            OrderInquiryReserveRequest.state == RESERVE_REQUESTED,
            OrderInquiryReserveRequest.parked_at.isnot(None),
        )
        if lock:
            query = query.populate_existing().with_for_update()
        requests = query.order_by(OrderInquiryReserveRequest.id.asc()).all()
        items, empty = [], []
        for request in requests:
            pairs = (
                self.db.query(OrderInquiryReserveRequestRow, OrderInquiryRow)
                .join(OrderInquiryRow, OrderInquiryRow.id == OrderInquiryReserveRequestRow.row_id)
                .filter(
                    OrderInquiryReserveRequestRow.request_id == request.id,
                    OrderInquiryReserveRequestRow.qty_reserved.is_(None),
                )
                .order_by(OrderInquiryReserveRequestRow.id.asc())
                .all()
            )
            if not pairs:
                empty.append(request)
                continue
            context = _build_context(self.db, request, pairs, actor_user_id=actor_user_id)
            items.append(self._item(request, len(pairs), context, [rr for rr, _ in pairs]))
        return items, empty

    def _notice_items(self, actor_user_id: str, *, lock: bool = False):
        """The caller's own parked notices: rows whose answer differs from what purchasing
        was last told, one item per request. `stale` = parked rows no longer actionable."""
        query = self.db.query(OrderInquiryReserveRequestRow).filter(
            OrderInquiryReserveRequestRow.notice_parked_by == actor_user_id,
            OrderInquiryReserveRequestRow.notice_parked_at.isnot(None),
        )
        if lock:
            query = query.populate_existing().with_for_update()
        parked = query.order_by(OrderInquiryReserveRequestRow.id.asc()).all()
        actionable = [
            rr
            for rr in parked
            if rr.qty_reserved is not None
            and (rr.notified_qty is None or _dec(rr.qty_reserved) != _dec(rr.notified_qty))
        ]
        stale = [rr for rr in parked if rr not in actionable]
        by_request: Dict[str, List[OrderInquiryReserveRequestRow]] = {}
        for rr in actionable:
            by_request.setdefault(rr.request_id, []).append(rr)
        items = []
        for request_id, rrs in by_request.items():
            request = (
                self.db.query(OrderInquiryReserveRequest)
                .filter(OrderInquiryReserveRequest.id == request_id)
                .first()
            )
            if request is None:
                continue
            rows = {
                row.id: row
                for row in self.db.query(OrderInquiryRow)
                .filter(OrderInquiryRow.id.in_([rr.row_id for rr in rrs]))
                .all()
            }
            still_open = (
                self.db.query(OrderInquiryReserveRequestRow)
                .filter(
                    OrderInquiryReserveRequestRow.request_id == request.id,
                    OrderInquiryReserveRequestRow.qty_reserved.is_(None),
                )
                .count()
            )
            total = (
                self.db.query(OrderInquiryReserveRequestRow)
                .filter(OrderInquiryReserveRequestRow.request_id == request.id)
                .count()
            )
            context = _build_commit_context(
                self.db,
                request,
                [{"rr": rr, "row": rows[rr.row_id]} for rr in rrs if rr.row_id in rows],
                open_row_count=still_open if request.state == RESERVE_REQUESTED else 0,
                row_count=total,
                actor_user_id=actor_user_id,
            )
            items.append(self._item(request, len(rrs), context, rrs))
        items.sort(key=lambda item: (item["inquiry_no"] or "", item["ordinal"]))
        return items, stale

    def _item(self, request, line_count: int, context: Dict[str, Any], rows) -> Dict[str, Any]:
        reserve = context["reserve"]
        from app.services.automation_triggers import build_order_inquiry_link

        return {
            "context": context,
            "request": request,
            "rows": list(rows),
            "line_count": line_count,
            "inquiry_id": str(request.order_inquiry_id),
            "inquiry_no": reserve.get("inquiry_no"),
            "ordinal": request.ordinal,
            "so_number": reserve.get("so_number"),
            "customer": reserve.get("customer"),
            "link": build_order_inquiry_link(request.order_inquiry_id),
        }

    @staticmethod
    def _group_context(group: Dict[str, Any], *, status: str) -> Dict[str, Any]:
        """One item -> today's context exactly. Several -> the bulk context the templates'
        `{% if reserve.requests %}` branch reads: counts, the first five order inquiries by OI
        no, and every raiser / requester so `include_raiser` / `include_requester` reach them."""
        from app.services import park_send
        from app.services.automation_triggers import build_order_inquiry_link

        items = group["items"]
        if len(items) == 1:
            return items[0]["context"]
        contexts = [item["context"] for item in items]
        by_inquiry: Dict[str, Dict[str, Any]] = {}
        for item in items:
            entry = by_inquiry.setdefault(
                item["inquiry_id"],
                {
                    "inquiry_no": item["inquiry_no"],
                    "so_number": item["so_number"],
                    "customer": item["customer"],
                    "line_count": 0,
                    "link": item["link"],
                },
            )
            entry["line_count"] += item["line_count"]
        ordered = sorted(by_inquiry.values(), key=lambda entry: entry["inquiry_no"] or "")
        base = contexts[0]
        reserve = {
            **base["reserve"],
            "requests": ordered[:5],
            "oi_count": len(ordered),
            "line_count": sum(entry["line_count"] for entry in ordered),
            "more_count": max(len(ordered) - 5, 0),
            "link": f"{build_order_inquiry_link(None)}?reserve={status}",
        }
        return {**base, **park_send.merge_people(contexts), "reserve": reserve}

    def _groups(self, trigger_type: str, items, *, status: str):
        from app.services import park_send

        groups, unresolved = park_send.group_by_recipient(self.db, trigger_type, items)
        for group in groups:
            group["context"] = self._group_context(group, status=status)
            group["inquiry_count"] = len({item["inquiry_id"] for item in group["items"]})
            group["line_count"] = sum(item["line_count"] for item in group["items"])
        return groups, unresolved

    @staticmethod
    def _unresolved_out(unresolved) -> List[Dict[str, int]]:
        if not unresolved:
            return []
        return [
            {
                "inquiry_count": len({item["inquiry_id"] for item in unresolved}),
                "line_count": sum(item["line_count"] for item in unresolved),
            }
        ]

    def _preview(self, trigger_type: str, items, *, status: str) -> Dict[str, Any]:
        from app.services import park_send

        groups, unresolved = self._groups(trigger_type, items, status=status)
        emails = []
        for group in groups:
            email = park_send.render_preview(
                self.db, trigger_type, group["context"], group["recipients"]
            )
            email["inquiry_count"] = group["inquiry_count"]
            email["line_count"] = group["line_count"]
            emails.append(email)
        return {"emails": emails, "unresolved": self._unresolved_out(unresolved)}

    def parked_requests_preview(self, *, actor_user_id: str) -> Dict[str, Any]:
        items, _empty = self._request_items(actor_user_id)
        return self._preview("order_inquiry_reserve_requested", items, status="needs_reserve")

    def parked_notices_preview(self, *, actor_user_id: str) -> Dict[str, Any]:
        items, _stale = self._notice_items(actor_user_id)
        return self._preview("order_inquiry_reserved", items, status="reserved")

    def send_parked_requests(self, *, actor_user_id: str) -> Dict[str, Any]:
        """Stamps and queues under a row lock: a second press blocks here, then finds
        nothing parked. The queued dispatches fire only when the caller's transaction
        commits (the existing after_commit drain) and are dropped if it rolls back."""
        items, empty = self._request_items(actor_user_id, lock=True)
        groups, unresolved = self._groups(
            "order_inquiry_reserve_requested", items, status="needs_reserve"
        )
        for request in empty:
            request.parked_at = None
        sent = 0
        for group in groups:
            for item in group["items"]:
                item["request"].parked_at = None
                sent += 1
            self.db.info.setdefault(_REQUESTED_PENDING_KEY, []).append(
                {
                    "context": group["context"],
                    "source_id": str(group["items"][0]["request"].id),
                }
            )
        self.db.flush()
        return {
            "emails_sent": len(groups),
            "items_sent": sent,
            "unresolved": self._unresolved_out(unresolved),
        }

    def send_parked_notices(self, *, actor_user_id: str) -> Dict[str, Any]:
        items, stale = self._notice_items(actor_user_id, lock=True)
        groups, unresolved = self._groups("order_inquiry_reserved", items, status="reserved")
        for rr in stale:
            rr.notice_parked_at = None
            rr.notice_parked_by = None
        sent = 0
        for group in groups:
            for item in group["items"]:
                for rr in item["rows"]:
                    rr.notified_qty = rr.qty_reserved
                    rr.notice_parked_at = None
                    rr.notice_parked_by = None
                sent += 1
            self.db.info.setdefault(_RESERVED_PENDING_KEY, []).append(
                {
                    "context": group["context"],
                    "source_id": str(group["items"][0]["request"].id),
                }
            )
        self.db.flush()
        return {
            "emails_sent": len(groups),
            "items_sent": sent,
            "unresolved": self._unresolved_out(unresolved),
        }

    def reserve_status_summary(self, *, actor_user_id: str) -> Dict[str, int]:
        """OI count per status (everyone's) and the OI count of the caller's own parked requests / notices."""
        pred = reserve_status_predicates()
        rr, req = OrderInquiryReserveRequestRow, OrderInquiryReserveRequest
        counts = (
            self.db.query(
                *[
                    func.count(func.distinct(req.order_inquiry_id)).filter(pred[key])
                    for key in ("parked", "needs_reserve", "notice_parked", "reserved")
                ],
                func.count(func.distinct(req.order_inquiry_id)).filter(
                    and_(pred["parked"], req.requested_by == actor_user_id)
                ),
                func.count(func.distinct(req.order_inquiry_id)).filter(
                    and_(
                        rr.notice_parked_by == actor_user_id,
                        rr.notice_parked_at.isnot(None),
                        rr.qty_reserved.isnot(None),
                        rr.qty_reserved.is_distinct_from(rr.notified_qty),
                    )
                ),
            )
            .select_from(rr)
            .join(req, req.id == rr.request_id)
            .one()
        )
        keys = (
            "parked",
            "needs_reserve",
            "notice_parked",
            "reserved",
            "my_parked_requests",
            "my_parked_notices",
        )
        return {key: int(value or 0) for key, value in zip(keys, counts)}

    def notice_recipients(self, *, inquiry_id: str, actor_user_id: str) -> Dict[str, Any]:
        """AC-SN-27: who the reserved automation would mail for this OI's open request(s):
        the requester To, the OI raiser Cc, the caller moved to Cc."""
        from app.services import park_send

        inquiry = self.db.query(OrderInquiry).filter(OrderInquiry.id == inquiry_id).first()
        if inquiry is None:
            raise AppException(
                404, "This order inquiry no longer exists.", code="order_inquiry_not_found"
            )
        automation = park_send.first_automation(self.db, "order_inquiry_reserved")
        requesters = [
            _person(self.db, user_id)
            for (user_id,) in self.db.query(OrderInquiryReserveRequest.requested_by)
            .filter(
                OrderInquiryReserveRequest.order_inquiry_id == inquiry.id,
                OrderInquiryReserveRequest.state == RESERVE_REQUESTED,
            )
            .order_by(OrderInquiryReserveRequest.ordinal.asc())
            .all()
        ]
        requesters = [person for person in requesters if person]
        recipients: List[Dict[str, Any]] = []
        if automation is not None and requesters:
            context = {
                "actor": _person(self.db, actor_user_id),
                "raiser": _person(self.db, inquiry.raised_by),
                "requester": requesters[0],
                **park_send.merge_people([{"requester": person} for person in requesters]),
            }
            recipients = park_send.resolve_ordered(self.db, automation, context)
        if not recipients:
            return {"to": None, "cc": []}
        return {
            "to": {"name": recipients[0].get("name") or recipients[0]["email"]},
            "cc": [{"name": r.get("name") or r["email"]} for r in recipients[1:]],
        }

    def _add_event(
        self,
        row: OrderInquiryRow,
        rr: OrderInquiryReserveRequestRow,
        kind: str,
        qty: Decimal,
        warehouse_id: Optional[str],
        note: Optional[str],
        actor_user_id: Optional[str],
    ) -> None:
        self.db.add(
            OrderInquiryReserveEvent(
                id=str(uuid.uuid4()),
                company_id=row.company_id,
                reserve_request_row_id=rr.id,
                kind=kind,
                qty=qty,
                warehouse_id=warehouse_id,
                note=note,
                actor_id=actor_user_id,
                # Explicit - see `create_request`'s own note on `requested_at`.
                created_at=datetime.utcnow(),
            )
        )

    def _flush_link(self) -> None:
        """SF-9: the row lock closes the race for two real concurrent commits;
        `uq_order_inquiry_links_reserve_request_row` is the backstop for a request row
        read stale past the guard - a 409, never an unhandled 500."""
        try:
            self.db.flush()
        except IntegrityError:
            self.db.rollback()
            raise AppException(
                409,
                "This row has already been answered.",
                code="reserve_request_row_already_answered",
            )

    # ----------------------------------------------------------- 6c F3: per-row history

    def history_for_row(
        self, *, request_id: str, row_id: str
    ) -> List[Dict[str, Any]]:
        """Newest first, across EVERY request that has ever touched this OI ROW, any
        state (H1, captain ruling on the browser walk) - not only the one `request_id`
        named in the URL. A cancelled, never-answered request stays visible even once
        a LATER request on the same row has since been reserved: purchasing raised a
        request, CS never got to it, purchasing cancelled and asked again - the first
        ask is still part of the story. `request_id` still anchors the call (a bogus
        or foreign one 404s exactly as before, AC-RS-61's own access rule is
        unchanged) but no longer scopes WHICH rows' entries are read; `requested` /
        `cancelled` derive straight off each request row's own parent request (no
        second copy); `reserved` / `unreserved` come off `order_inquiry_reserve_events`
        (`PLAN-oi-request-cs-reserve.md` section 6c, F3)."""
        request = (
            self.db.query(OrderInquiryReserveRequest)
            .filter(OrderInquiryReserveRequest.id == request_id)
            .first()
        )
        if request is None:
            raise AppException(
                404,
                "This reserve request no longer exists.",
                code="reserve_request_not_found",
            )
        anchor = (
            self.db.query(OrderInquiryReserveRequestRow)
            .filter(
                OrderInquiryReserveRequestRow.request_id == request.id,
                OrderInquiryReserveRequestRow.row_id == row_id,
            )
            .first()
        )
        if anchor is None:
            raise AppException(
                404,
                "That row is not part of this reserve request.",
                code="reserve_request_row_not_found",
            )

        # H1: every request row this OI row has ever appeared on, across every
        # request - the anchor above only proves `request_id`/`row_id` is a
        # legitimate, company-scoped pair to ask about at all.
        rows_and_requests = (
            self.db.query(OrderInquiryReserveRequestRow, OrderInquiryReserveRequest)
            .join(
                OrderInquiryReserveRequest,
                OrderInquiryReserveRequest.id == OrderInquiryReserveRequestRow.request_id,
            )
            .filter(OrderInquiryReserveRequestRow.row_id == row_id)
            .all()
        )

        entries: List[Dict[str, Any]] = []
        for rr, req in rows_and_requests:
            entries.append(
                {
                    "kind": "requested",
                    "qty": _qty_str(rr.qty_requested),
                    "location": _warehouse_code(self.db, rr.warehouse_id),
                    "reason": None,
                    "actor_name": _actor_name(self.db, req.requested_by),
                    "created_at": req.requested_at,
                }
            )
            if req.state == RESERVE_CANCELLED and req.cancelled_at is not None:
                entries.append(
                    {
                        "kind": "cancelled",
                        "qty": None,
                        "location": None,
                        "reason": None,
                        "actor_name": _actor_name(self.db, req.cancelled_by),
                        "created_at": req.cancelled_at,
                    }
                )
            events = (
                self.db.query(OrderInquiryReserveEvent)
                .filter(OrderInquiryReserveEvent.reserve_request_row_id == rr.id)
                .all()
            )
            for event_row in events:
                entries.append(
                    {
                        "kind": event_row.kind,
                        "qty": _qty_str(event_row.qty),
                        "location": _warehouse_code(self.db, event_row.warehouse_id),
                        "reason": event_row.note,
                        "actor_name": _actor_name(self.db, event_row.actor_id),
                        "created_at": event_row.created_at,
                    }
                )
        entries.sort(
            key=lambda entry: entry["created_at"] or datetime.min, reverse=True
        )
        for entry in entries:
            entry["created_at"] = as_utc(entry["created_at"])
        return entries

    # --------------------------------------------------------------- toast-only read

    def notified_name(
        self,
        *,
        trigger_type: str,
        actor_user_id: Optional[str],
        raiser_user_id: Optional[str],
    ) -> Optional[str]:
        """Best-effort read of who the request mail's To address names, for the
        dialog's own toast (plan 3.7: "Request #2 sent to Eling"). A PUBLIC method
        here rather than the route reaching across into `AutomationService`'s own
        private `_normalize_recipient_config` directly (review round, layering) -
        the route asks its OWN service for this, the same as every other read.
        Never load-bearing: a disabled or not-yet-configured automation simply reads
        null, and the actual send already happened synchronously inside the write's
        own commit (`register_order_inquiry_reserve_post_commit_dispatch`'s
        `after_commit` listener runs before this)."""
        try:
            from app.models.automation import Automation
            from app.services.automation_recipients import resolve_recipients
            from app.services.automation_service import AutomationService

            automation = (
                self.db.query(Automation)
                .filter(Automation.trigger_type == trigger_type, Automation.enabled.is_(True))
                .order_by(Automation.created_at.asc())
                .first()
            )
            if automation is None:
                return None
            config = AutomationService._normalize_recipient_config(automation.recipient_config)
            context = {
                "actor": _person(self.db, actor_user_id),
                "raiser": _person(self.db, raiser_user_id),
            }
            recipients = resolve_recipients(self.db, config, context)
            if not recipients:
                return None
            return recipients[0].get("name")
        except Exception:  # noqa: BLE001 - a toast name is never load-bearing
            return None


# --------------------------------------------------------------- post-commit dispatch

#: `Session.info` keys for the two dispatches this module queues mid-transaction and
#: fires once the session actually commits - see `register_order_inquiry_reserve_post_
#: commit_dispatch` below and its module docstring for why this is the SIMPLER of the
#: two shapes `project_order_inquiry_service.py` carries.
_REQUESTED_PENDING_KEY = "oi_reserve_requested_pending"
_RESERVED_PENDING_KEY = "oi_reserve_reserved_pending"

_POST_COMMIT_DISPATCH_REGISTERED = False


def register_order_inquiry_reserve_post_commit_dispatch() -> None:
    """Fire every queued reserve dispatch once this session's write actually commits.

    Copied from `_dispatch_changed_with_links` / `register_order_inquiry_post_commit_
    dispatch` (`project_order_inquiry_service.py:9355`) - queue on `Session.info`, drain
    on `after_commit` with a FRESH session (a dispatch is real, independent work;
    `AutomationService` commits its own outbox rows), discard on `after_soft_rollback`.
    Idempotent and called once at startup, same as every other global session listener
    this app registers.
    """
    global _POST_COMMIT_DISPATCH_REGISTERED
    if _POST_COMMIT_DISPATCH_REGISTERED:
        return

    def _drain(session, key: str, trigger_type: str, source_kind: str) -> None:
        pending = session.info.pop(key, None)
        if not pending:
            return
        if session.info.get("confirm_preview"):
            # A simulated confirm sends nothing: its queue is dropped, not dispatched.
            return
        from app.database import SessionLocal
        from app.services.automation_service import AutomationService

        fresh = SessionLocal()
        try:
            for item in pending:
                try:
                    AutomationService(fresh).dispatch_event(
                        trigger_type,
                        context=item["context"],
                        source_kind=source_kind,
                        source_id=item["source_id"],
                    )
                except Exception:  # noqa: BLE001 - a post-commit side effect never raises
                    fresh.rollback()
                    logger.exception(
                        "Automation dispatch(%s) failed for reserve request %s",
                        trigger_type,
                        item.get("source_id"),
                    )
        finally:
            fresh.close()

    @event.listens_for(Session, "after_commit")
    def _fire_pending_reserve_requested(session):  # noqa: ANN001
        _drain(
            session,
            _REQUESTED_PENDING_KEY,
            "order_inquiry_reserve_requested",
            "order_inquiry_reserve_request",
        )

    @event.listens_for(Session, "after_commit")
    def _fire_pending_reserve_reserved(session):  # noqa: ANN001
        _drain(
            session,
            _RESERVED_PENDING_KEY,
            "order_inquiry_reserved",
            "order_inquiry_reserve_request",
        )

    @event.listens_for(Session, "after_soft_rollback")
    def _discard_pending_reserve(session, previous_transaction):  # noqa: ANN001
        session.info.pop(_REQUESTED_PENDING_KEY, None)
        session.info.pop(_RESERVED_PENDING_KEY, None)

    _POST_COMMIT_DISPATCH_REGISTERED = True
