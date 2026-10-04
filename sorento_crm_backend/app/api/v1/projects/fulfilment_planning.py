"""Fulfilment Planning: the reconciliation worklist, the supply sheet, the confirmation.

Contract: `documentation/plans/scm/STAGE1B-scm-front-planning-reconciliation.md` section 3
and `STAGE1C-scm-front-planning-promising.md` section 6.

Five routes, mounted on the same `/project-sales` root as the sales-order ones because
they are addressed the same two ways: a cross-project worklist, and one order by id.

Rights follow the rest of the module: reads take `projects.projects.view`, and the re-run
takes `projects.projects.edit` on that project, checked in `project_service` like every
other project write. The re-run is a WRITE (it persists the links it can prove and clears
the ones that went stale), so it is `require_permission`, not the API-key variant.

Mounted BEFORE `sales_orders.router` for the same reason `divergences` is: these paths hang
off `/sales-orders/{pso_id}`, and keeping every declaration of that prefix ahead of the
plain `/sales-orders/{pso_id}` one removes any question of shadowing.
"""
from __future__ import annotations

import logging
from datetime import date
from dataclasses import dataclass
from typing import Any, Dict, Literal, Optional, Tuple

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.v1.projects._common import permission_slugs
from app.database import get_db
from app.dependencies import require_permission, require_permission_with_api_key
from app.schemas.common import ListResponse, MAX_PAGE_LIMIT
from app.schemas.project_board import (
    BoardLineDraft,
    BoardLineDraftBody,
    PileQueue,
    PlanningBoard,
    StockDetail,
)
from app.schemas.project_so_reconciliation import (
    AdoptSalesOrderBody,
    AdoptSalesOrderResult,
    FulfilmentPlanningRow,
    ReconciliationSummary,
)
from app.schemas.project_supply import (
    ClassificationEvidence,
    ConfirmManyBody,
    ConfirmManyResult,
    ConfirmResult,
    ConfirmSupplyBody,
    PlanRow,
    SupplyProposal,
)
from app.services import project_service as projects
from app.services.error_handler import AppException, handle_internal_error
from app.services.project_classification_evidence import classification_evidence
from app.services import project_line_draft_service
from app.services.project_fulfilment_board_service import FulfilmentBoardService
from app.services.project_so_adoption_service import ProjectSOAdoptionService
from app.services.project_so_draft_service import ProjectSODraftService
from app.services.project_so_reconciliation_service import (
    ProjectSOReconciliationService,
)
from app.services.project_supply_service import ProjectSupplyService
from app.services.project_supply_undo_service import UndoJournal
from app.services.uuid_list_param import parse_uuid_list
from app.services.uuid_path_param import validate_uuid_path

logger = logging.getLogger(__name__)

router = APIRouter()

VIEW = "projects.projects.view"
EDIT = "projects.projects.edit"


def _assert_can_act_on(db: Session, order, current_user: dict) -> None:
    """Write authorisation for one planning record, whether or not it has a project.

    `assert_can_edit_project` cannot run without a project, and an order adopted from the
    AutoCount book has none by design (`PLAN-fulfilment-planning-from-autocount-so.md`
    section 2, "Authorisation with no project"): the module permission on the route is then
    the whole gate, and the per-project check is kept for records that DO have a project.
    Passing `order.project_id` straight to `get_project_or_404` answered 404 "Project not
    found" for every adopted order, which took Confirm and Re-sync - the last steps of the
    journey - off the screen entirely. Company scope is enforced by the mixin either way.

    Callers: `confirm_supply` and `rerun_reconciliation`. `save_line_draft` never called
    this (S2, review round 3) once R3(b)'s rework (23 Sep 2026,
    `PLAN-board-reject-on-confirmed-line.md`) took the un-decide seam back out of the
    draft save - a rejected verdict on a covered line is STAGED like every other board
    decision, and Confirm is the only write that still reaches `uncover_lines`.
    """
    if not order.project_id:
        return
    project = projects.get_project_or_404(db, order.project_id)
    projects.assert_can_edit_project(
        db, project, current_user["id"], permission_slugs(db, current_user["id"])
    )


def _attach_undo_journal(db: Session, order, journal: UndoJournal, before_id) -> None:
    """Give the confirm's own journal to the decision it just minted (S1, #978).

    `before_id` is whatever was active before this call ran, read by the caller
    BEFORE the confirm - a call that refused mid-way, or one whose whole composition
    was cancellations with nothing new to confirm, leaves the SAME decision active,
    and attaching to it would overwrite an unrelated (possibly already-undone)
    journal with this call's own, irrelevant one.
    """
    from app.models.project_so import SOSupplyDecision

    decision = (
        db.query(SOSupplyDecision)
        .filter(
            SOSupplyDecision.project_sales_order_id == order.id,
            SOSupplyDecision.state == "active",
        )
        .first()
    )
    if decision is not None and str(decision.id) != str(before_id or ""):
        journal.attach(decision)


def _active_decision_id(db: Session, order) -> Optional[str]:
    from app.models.project_so import SOSupplyDecision

    return (
        db.query(SOSupplyDecision.id)
        .filter(
            SOSupplyDecision.project_sales_order_id == order.id,
            SOSupplyDecision.state == "active",
        )
        .scalar()
    )


@router.get("/fulfilment-planning", response_model=ListResponse[FulfilmentPlanningRow])
def list_fulfilment_planning(
    query: Optional[str] = Query(
        None,
        description=(
            "One box. Matches the sales-order number, the customer name, the project "
            "label, or a PRODUCT (item code or name) on any of the order's outstanding "
            "lines. Also the provisional ref, AutoCount doc no and area group of an "
            "authored record."
        ),
    ),
    # A closed set, so an unknown value is a 422 rather than a 200 with an empty list:
    # the state is derived, and a filter nothing can equal reads on screen as "no work
    # to do" when the truth is "that is not a state". `not_started` is the fourth value
    # (AC-FP05) and means "an outstanding core sales order nobody has planned".
    review_state: Optional[
        Literal[
            "not_started", "awaiting_reconciliation", "needs_cs_review", "confirmed"
        ]
    ] = Query(None),
    project_id: Optional[str] = Query(None),
    sales_order_id: Optional[str] = Query(
        None, description="Narrow to one core sales order. Addressing only."
    ),
    # `sort` + `dir` are the names `buildDataGridParams` sends, unchanged. The set is
    # CLOSED for the same reason `review_state` is: a grid drawing a sort arrow on a column
    # the server quietly ignored is a screen lying about what it is showing. It must equal
    # `SORTABLE_FIELDS` in the service - FastAPI cannot build a `Literal` from a runtime
    # set, so a test asserts the two agree.
    sort: Optional[
        Literal[
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
        ]
    ] = Query(None, description="Defaults to earliest_required_date. Nulls always last."),
    direction: Optional[Literal["asc", "desc"]] = Query(
        "asc",
        alias="dir",
        description="Nulls sort last in BOTH directions, never first on desc.",
    ),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=MAX_PAGE_LIMIT),
    _user: dict = Depends(require_permission_with_api_key(VIEW)),
    db: Session = Depends(get_db),
):
    """Everything that needs planning, one row per subject (AC-FP01 to AC-FP06).

    A union of the outstanding project-class sales-order book and the planning records
    authored here that it does not already carry, ordered by the earliest still-owed
    required date because that is the order the work is due in.

    Plain ``def``, so FastAPI runs the whole handler in a threadpool: the mapping is
    synchronous SQLAlchemy over a page of orders, and on the event loop it holds up every
    other request the worker is serving.
    """
    try:
        if project_id:
            validate_uuid_path(project_id, resource="Project")
        if sales_order_id:
            validate_uuid_path(sales_order_id, resource="Sales order")
        return ProjectSOReconciliationService(db).list_fulfilment_planning(
            query=query,
            review_state=review_state,
            project_id=project_id,
            sales_order_id=sales_order_id,
            sort=sort,
            dir=direction,
            page=page,
            limit=limit,
        )
    except Exception as exc:
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.get("/plans", response_model=ListResponse[PlanRow])
def list_plans(
    query: Optional[str] = Query(
        None,
        description="Matches the sales-order number, the customer name, or the agent code.",
    ),
    state: Optional[Literal["active", "superseded", "challenged"]] = Query(
        None, description="Defaults to active - what is stored NOW."
    ),
    agent_code: Optional[str] = Query(None, description="One agent's code, exact match."),
    sort: Optional[
        Literal["so_number", "customer_name", "agent_code", "revision_no", "state", "decided_at"]
    ] = Query(None, description="Defaults to decided_at, most recent first."),
    direction: Optional[Literal["asc", "desc"]] = Query("desc", alias="dir"),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=MAX_PAGE_LIMIT),
    _user: dict = Depends(require_permission_with_api_key(VIEW)),
    db: Session = Depends(get_db),
):
    """The Plans page (PLAN-demo-followups-19aug-ladder-v2 D1): "is the plan stored, how do
    I review it" - every supply decision, one row per revision, cross-order.

    A pure read over `so_supply_decisions`. `state` defaults to `active`: the question is
    about what is committed NOW, not the whole history a line's decision has ever carried,
    though `superseded` and `challenged` stay a click away.

    Plain ``def``, so FastAPI runs it in a threadpool: it is synchronous SQLAlchemy over a
    page of decisions.
    """
    try:
        result = ProjectSupplyService(db).list_decisions(
            query=query,
            state=state,
            agent_code=agent_code,
            sort=sort,
            dir=direction or "desc",
            page=page,
            limit=limit,
        )
        return {"data": result["data"], "pagination": result["pagination"]}
    except Exception as exc:
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.post("/fulfilment-planning/adopt", response_model=AdoptSalesOrderResult)
def adopt_sales_order(
    payload: AdoptSalesOrderBody,
    current_user: dict = Depends(require_permission(EDIT)),
    db: Session = Depends(get_db),
):
    """Start planning: journey step 2, and the whole of it (AC-FP07).

    One decision - which order - and everything else is derived from the order itself: the
    lines, the products, the quantities, the required dates, the fulfilment locations and
    the customer. No project to choose, no reference to invent, no confirmation dialog,
    because it destroys nothing and it repeats safely (AC-FP08): a second press, a retry or
    a second CS answers with the record that already exists.

    Gated on the module permission alone, with no `assert_can_edit_project`: an adopted
    record has no project for that check to run against (plan section 2, an accepted
    narrowing rather than an oversight). No new permission is introduced (AC-FP26).
    """
    try:
        body = ProjectSOAdoptionService(db).adopt(
            payload.sales_order_id, actor_user_id=current_user["id"]
        )
        db.commit()
        return body
    except Exception as exc:
        db.rollback()
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.get("/fulfilment-planning/board", response_model=PlanningBoard)
def get_planning_board(
    orders: str = Query(
        ...,
        description=(
            "Comma-separated sales-order NUMBERS, never ids, so a board can be linked to "
            "and reloaded. At most 50 (PLAN 13.2)."
        ),
    ),
    granularity: Literal["day", "date", "week", "month"] = Query("week"),
    day_window: Optional[date] = Query(
        None,
        description=(
            "First day of the day-granularity window. Defaults to the earliest still-future "
            "date owed. A DISPLAY bound only: nothing is filtered out of the plan by it."
        ),
    ),
    preview_policy: Optional[str] = Query(
        None,
        description=(
            "Rank by a NAMED alternative policy instead of the live one, without activating "
            "it. '1' or 'true' means the module's own board preview. Read-only: a previewed "
            "ranking is labelled and may never be committed against."
        ),
    ),
    as_of: Optional[date] = Query(
        None, description="Build the board against this date instead of today."
    ),
    _user: dict = Depends(require_permission_with_api_key(VIEW)),
    db: Session = Depends(get_db),
):
    """Several sales orders at once: dates across, products down, one pile per location.

    The board writes no decision object of its own - the decision stays per sales order,
    atomic across the lines that order is committing - so opening it claims no stock and
    there is no board write endpoint to pair with this.

    It writes at most one thing, and only ever the same thing (issue #969, B2 owner ruling
    17 Sep 2026): a core line that arrived after an order was adopted gets its planning-
    record mirror here, the one seam the confirm write no longer runs on its own
    (`ProjectSupplyService.confirm`, AC-PR8). Committed on the way out for that reason - a
    fresh per-request session otherwise rolls the flush back on close, and the confirm the
    FE fires right after reading this board is a SEPARATE request.

    Plain ``def`` so FastAPI runs it in a threadpool: it is synchronous SQLAlchemy over a
    selection of up to fifty orders, and on the event loop it would hold up every other
    request the worker is serving.
    """
    try:
        numbers = [part.strip() for part in (orders or "").split(",") if part.strip()]
        body = FulfilmentBoardService(db).build(
            numbers,
            granularity=granularity,
            as_of=as_of,
            day_window_start=day_window,
            preview_policy=preview_policy,
            actor_user_id=_user.get("id") if _user else None,
        )
        db.commit()
        return body
    except Exception as exc:
        db.rollback()
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.put(
    "/fulfilment-planning/lines/{contribution_key:path}/draft",
    response_model=BoardLineDraft,
)
def save_line_draft(
    contribution_key: str,
    payload: BoardLineDraftBody,
    current_user: dict = Depends(require_permission(EDIT)),
    db: Session = Depends(get_db),
):
    """Save decision on one board line (S4, R-F, AC-4.1).

    An upsert: one row per contribution key, re-stamped with whoever saved it last. Drafts
    are SHARED rather than per user (one planning team), so this is what a second planner
    opening the same board sees, named after the newer saver (AC-4.5).

    The EDIT permission is the whole gate, and deliberately: the per-PROJECT check Confirm
    runs (`_assert_can_act_on`) refuses a planner who is not the project's own salesperson,
    and drafts are SHARED across the planning team by ruling. A draft claims no stock and
    promises nothing - Confirm still applies the full check to the composition it posts -
    so gating the save on project ownership would only stop the second planner AC-4.5 is
    about from correcting the first one's line. This holds for EVERY verdict, `rejected` on
    a covered line included since the 23 Sep 2026 rework
    (`PLAN-board-reject-on-confirmed-line.md`, owner ruling): the save only writes a draft
    now, never `uncover_lines` - Confirm is the one write that still needs the per-project
    check, and it already runs its own.

    `{contribution_key}` is the board's own `contributions[].key` -
    `${sales_order_id}|${line_no}|${item_code}|${bucket_key}` - URL-encoded by the client
    because it embeds characters a path segment may not carry raw. Declared `:path` so an
    item code holding a slash still addresses its own line rather than 404ing on a route
    that never matched.
    """
    try:
        body = project_line_draft_service.save_draft(
            db,
            contribution_key,
            decision=payload.decision,
            # D12 (#573): dumped here, not left as pydantic models, so the JSONB column
            # stores exactly what a board GET already serialises a live `BoardSource` as.
            proposed=(
                [item.model_dump(mode="json") for item in payload.proposed]
                if payload.proposed is not None
                else None
            ),
            actor_user_id=current_user["id"],
        )
        db.commit()
        return body
    except Exception as exc:
        db.rollback()
        # S3, code review round 3: an `AppException` (the 422s above) states its own
        # message and propagates untouched; anything else is a genuine server defect and
        # gets the FIXED message - never `str(exc)`, which echoed raw DB/constraint text
        # (table and column names, the SQL fragment) straight into the response body.
        raise exc if hasattr(exc, "status_code") else handle_internal_error()


@router.delete(
    "/fulfilment-planning/lines/{contribution_key:path}/draft", status_code=204
)
def delete_line_draft(
    contribution_key: str,
    current_user: dict = Depends(require_permission(EDIT)),
    db: Session = Depends(get_db),
):
    """Undo on a saved line (S4, AC-4.3): the draft goes and the pill returns to Suggested.

    A line nobody has saved is a 404 rather than a quiet 204 - it says plainly that there
    was nothing there - and the client treats it as "already gone", which is what Undo
    asked for either way.
    """
    try:
        project_line_draft_service.remove_draft(db, contribution_key)
        db.commit()
        return None
    except Exception as exc:
        db.rollback()
        # S3, code review round 3: see `save_line_draft`'s own note - never `str(exc)`.
        raise exc if hasattr(exc, "status_code") else handle_internal_error()


@router.post("/fulfilment-planning/confirm-all", response_model=ConfirmManyResult)
def confirm_all(
    payload: ConfirmManyBody,
    current_user: dict = Depends(require_permission(EDIT)),
    db: Session = Depends(get_db),
):
    """"Confirm all approved" (D3): every order's Confirm, each its OWN transaction.

    The board's Approve all composes a verdict per line across up to fifty orders; this posts
    the SAME per-order write `POST .../sales-orders/{pso_id}/confirm` already does, once per
    order named in `orders`, rather than one call per order from the panel. One order's own
    refusal (a stale line, a line somebody else confirmed a moment earlier) never takes the
    orders around it down: each entry commits or rolls back on its own, and every order named
    in the body gets a result - there is no silent partial success. `orders: []` answers with
    an empty result rather than a refusal; a board with nothing approved yet is not an error.

    `batch_id` travels PER ORDER now (`PLAN-scm-board-picks-up-pending-change.md`, change 4,
    AC-B5/AC-B6). The body-level `batch_id` is the LEGACY shape and only applies when NO
    order in the payload names a non-null `batch_id` of its own: the instant at least one
    order carries a real id, the body-level id is ignored for every OTHER order in the same
    payload, including one that explicitly said `batch_id: null` - it must not silently
    inherit a batch a sibling order in the same press answered (reviewer finding B1,
    39a5d8b07: the frontend already sends both a body-level id AND a per-order `null` on a
    mixed board, and `entry.batch_id or payload.batch_id` could not tell "this order
    legitimately has none" from "this order said nothing", so it tried to apply order A's
    batch against order B, which held none of its rows). A payload where every order's
    `batch_id` is null or absent still falls back to the body-level id for all of them,
    exactly as before this fix - that is the legacy, single-batch shape. An order that
    resolves to a batch takes the same apply the per-order Confirm takes for a `?batch=`
    board (one press, one call, one revision, batch rows marked applied); an order with
    neither confirms as an ordinary revision beside it.
    """
    try:
        if not payload.orders:
            return {"results": []}
        actor_id = current_user["id"]
        supply = ProjectSupplyService(db)
        any_per_order = any(entry.batch_id for entry in payload.orders)

        def write_one(order, entry):
            resolved_batch_id = entry.batch_id or (None if any_per_order else payload.batch_id)
            # `_confirm_with_possible_rejects` opens its OWN `UndoJournal` per branch (S1,
            # #978: `confirm_many` commits or rolls back each entry on its own, so a journal
            # opened HERE - inside that per-order try - never survives past this order's own
            # write). `entry.rejected_line_ids` is this order's own half of the board's
            # withdrawal, `entry` itself the composition `confirm()` already reads.
            return _confirm_with_possible_rejects(
                db,
                supply,
                order,
                entry,
                actor_user_id=actor_id,
                batch_id=resolved_batch_id,
                confirm_batch=lambda: _confirm_a_planning_change(
                    db,
                    order,
                    _BatchedEntry(list(entry.lines), resolved_batch_id),
                    actor_id,
                    preview=payload.preview,
                ),
            )

        results = supply.confirm_many(
            payload.orders,
            actor_user_id=actor_id,
            assert_can_act=lambda session, order: _assert_can_act_on(
                session, order, current_user
            ),
            write=write_one,
            # #1362 hold-back: an order answering a planning-change batch is applied whole.
            can_hold_back=lambda entry: not (
                entry.batch_id or (None if any_per_order else payload.batch_id)
            ),
            preview=payload.preview,
        )
        return {"results": results}
    except Exception as exc:
        db.rollback()
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.get("/fulfilment-planning/stock-detail", response_model=StockDetail)
def get_stock_detail(
    product_id: str = Query(..., description="Addressing only; the board's cell carries it."),
    warehouse_id: Optional[str] = Query(
        None, description="One bin. Give this OR `group`, never neither."
    ),
    group: Optional[str] = Query(
        None,
        description=(
            "A whole set instead of one bin: the ownership-group suffix (`IB`) or `pools` "
            "for the five site pools. The ladder draws the GROUP's pile, so the group is "
            "what a running balance has to be read over."
        ),
    ),
    line_ids: Optional[str] = Query(
        None,
        description=(
            "The CORE sales-order lines the drawer was opened for, comma separated. Their "
            "rows come back marked `is_this_line`. Omitted lists the documents on nobody's "
            "behalf, which is what the pile looks like to somebody not in it."
        ),
    ),
    _user: dict = Depends(require_permission_with_api_key(VIEW)),
    db: Session = Depends(get_db),
):
    """One product at one location: On Hand, SO Qty, SPO Qty, Available, and the documents.

    AutoCount's Stock Status with Detail, which is the screen the planner checks stock on and
    the one they asked the board's cell strip to justify itself against. A pure read.

    Plain ``def``, so FastAPI runs it in a threadpool: it is synchronous SQLAlchemy over one
    product-location's outstanding book.
    """
    try:
        validate_uuid_path(product_id, resource="Product")
        if not warehouse_id and not group:
            raise AppException(
                status_code=422,
                message="Ask for one location or for one group.",
                code="stock_detail_target_required",
            )
        if warehouse_id and not group:
            validate_uuid_path(warehouse_id, resource="Warehouse")
        # The SAME reader every other `*_ids` filter uses: it takes CSV, a JSON array or
        # repeated params, deduplicates, and refuses a value that is not an id by naming the
        # parameter - rather than handing the typo to the query.
        wanted = parse_uuid_list([line_ids], param_name="line_ids") or []
        return FulfilmentBoardService(db).stock_detail(
            product_id, warehouse_id, line_ids=wanted, group=group
        )
    except Exception as exc:
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.get("/fulfilment-planning/queue", response_model=PileQueue)
def get_pile_queue(
    product_id: str = Query(..., description="Addressing only; the board's cell carries it."),
    warehouse_id: str = Query(...),
    line_id: Optional[str] = Query(
        None,
        description=(
            "The CORE sales-order line asking. Its row is marked, its position is stated, and "
            "every line in front of it says which factor put it there. Omitted reads the queue "
            "on nobody's behalf."
        ),
    ),
    _user: dict = Depends(require_permission_with_api_key(VIEW)),
    db: Session = Depends(get_db),
):
    """Who is standing in front of this line at its pile, in the order the stock is served.

    The same queue the trail counted (`_pile_book`), read out in full with each line's rank and
    the facts behind it. A pure read.

    Plain ``def``, so FastAPI runs it in a threadpool: it is synchronous SQLAlchemy over one
    product-location's outstanding book, the widest of which on the live data is 289 lines.
    """
    try:
        validate_uuid_path(product_id, resource="Product")
        validate_uuid_path(warehouse_id, resource="Warehouse")
        if line_id:
            validate_uuid_path(line_id, resource="Sales order line")
        return FulfilmentBoardService(db).pile_queue(product_id, warehouse_id, line_id)
    except Exception as exc:
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.get(
    "/fulfilment-planning/classification", response_model=ClassificationEvidence
)
def get_classification_evidence(
    product_id: str = Query(..., description="Addressing only; the board's chip carries it."),
    _user: dict = Depends(require_permission_with_api_key(VIEW)),
    db: Session = Depends(get_db),
):
    """The Proof button: the ranked evidence behind one product's hot/cold verdict.

    The captain, reading the trail: "don't give me jargon like abc classification, just tell
    me hot selling or cold selling, at project or retail, with some button for me to view
    detail as a proof". This is that detail - which location, delivered how much, ranked
    where out of how many, and what share of the class that is - read live over the same
    rows the board's own hot-selling predicate reads. A pure read.

    Plain ``def``, so FastAPI runs it in a threadpool: it is synchronous SQLAlchemy over one
    product's classification rows.
    """
    try:
        validate_uuid_path(product_id, resource="Product")
        return classification_evidence(db, product_id)
    except Exception as exc:
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.get(
    "/sales-orders/{pso_id}/reconciliation", response_model=ReconciliationSummary
)
def get_reconciliation(
    pso_id: str,
    _user: dict = Depends(require_permission_with_api_key(VIEW)),
    db: Session = Depends(get_db),
):
    """What the mapping makes of this order right now. A pure read: it writes nothing."""
    try:
        validate_uuid_path(pso_id, resource="Sales order")
        order = ProjectSODraftService(db).get_order(pso_id)
        return ProjectSOReconciliationService(db).evaluate(order)
    except Exception as exc:
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.get("/sales-orders/{pso_id}/supply", response_model=SupplyProposal)
def get_supply_proposal(
    pso_id: str,
    _user: dict = Depends(require_permission_with_api_key(VIEW)),
    db: Session = Depends(get_db),
):
    """The Supply composition section: what covers each line, and why (J04).

    It writes at most one thing, and only ever the same thing: an active revision whose
    frozen facts no longer match the live ones is flipped to `challenged` here, because a
    sheet that reads Confirmed against quantities that have moved is a promise nobody can
    keep. Committed on the way out for that reason.
    """
    try:
        validate_uuid_path(pso_id, resource="Sales order")
        service = ProjectSupplyService(db)
        order = service.get_order(pso_id)
        body = service.proposal_for(order)
        db.commit()
        return body
    except Exception as exc:
        db.rollback()
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


def _reasons_for_rejected_lines(
    db: Session, order, rejected_line_ids: list, *, named_lines: set
) -> Tuple[Dict[str, str], str]:
    """The reason CS gave for each `rejected_line_ids` entry, read off its own SAVED
    DRAFT - never off the request body, which carries no reason of its own (owner ruling
    23 Sep 2026, `PLAN-board-reject-on-confirmed-line.md`: reject on a confirmed line is
    STAGED, and the draft `save_draft` already wrote is the one place the reason lives).

    Refused 422 `board_line_reject_reason_required` when an id does not resolve to a
    line on THIS order, carries no draft, or that draft's own verdict is not `rejected`
    with a non-blank reason - a stale client (a tab open since before the reject, a race
    with another planner) is exactly what this defends against, the route never trusting
    an id on its own. Also refused when an id appears in `named_lines` too (`payload.lines`)
    - a line cannot be REPLACED and DROPPED by the same press.

    B1 (fix round, review): an id also has to be COVERED BY THE ACTIVE DECISION, checked
    HERE rather than left to `uncover_lines`' own bare `bool` (which the withdrawal-only
    caller used to ignore outright - a stale tab could Confirm a withdrawal for a line
    nobody still covers, and the mixed path never checked at all). A stale client is
    refused the SAME 422 either way - `board_line_withdrawal_not_covered` names the
    actual cause rather than reusing `board_line_reject_reason_required`, which would
    read as "you forgot the reason" on a line that never had anything to withdraw.
    Loaded ONCE for the whole call: every id in `rejected_line_ids` is checked against
    the SAME active decision, never a fresh read per id.

    N4 (nit, fix round): `covered_ids` above is deliberately a bare "does the active
    decision's own `line_snapshots` name this id" - unlike `_active_coverage`
    (`project_line_draft_service.py`), it carries NO `_in_open_planning_change` exemption
    for a line sitting in a batch nobody has applied yet. That is safe here, not an
    oversight: this function is only ever reached from `confirm_supply` /
    `confirm_all`'s shared seam, which already refuses `rejected_line_ids` alongside a
    `batch_id` outright (422 `board_reject_not_supported_in_batch`, AC-B12) BEFORE this
    function is called - a withdrawal never reaches a batched line in the first place, so
    there is nothing for the exemption to do here. `_active_coverage` still needs its own
    exemption because `save_draft` (a batched line's OTHER verdicts - amend, approve) is
    reached with no such upstream refusal.

    Returns the reasons keyed by `project_line_id`, and the ONE sentence the superseded
    revision is stamped with instead of `_write_decision`'s own "Reconfirmed by CS." -
    every rejected line named, in order: "Line 2 rejected: wrong site; Line 5 rejected:
    discontinued".
    """
    from app.models.project_so import DECISION_ACTIVE, ProjectSalesOrderLine, SOSupplyDecision

    reasons: Dict[str, str] = {}
    sentences: list = []
    if not rejected_line_ids:
        return reasons, ""
    active = (
        db.query(SOSupplyDecision)
        .filter(
            SOSupplyDecision.project_sales_order_id == order.id,
            SOSupplyDecision.state == DECISION_ACTIVE,
        )
        .one_or_none()
    )
    covered_ids = {
        str((snapshot or {}).get("project_line_id") or "")
        for snapshot in (active.line_snapshots or [])
    } if active is not None else set()
    covered_ids.discard("")
    drafts = project_line_draft_service.drafts_for_orders(db, [order.so_id] if order.so_id else [])
    for project_line_id in rejected_line_ids:
        if str(project_line_id) in named_lines:
            raise AppException(
                status_code=422,
                message=(
                    "A line cannot be confirmed with a new composition and withdrawn as "
                    "rejected in the same press."
                ),
                code="board_reject_line_named_twice",
            )
        if str(project_line_id) not in covered_ids:
            raise AppException(
                status_code=422,
                message="This line is not confirmed any more. Reload the board.",
                code="board_line_withdrawal_not_covered",
            )
        mirror = (
            db.query(ProjectSalesOrderLine)
            .filter(
                ProjectSalesOrderLine.id == str(project_line_id),
                ProjectSalesOrderLine.project_sales_order_id == order.id,
            )
            .one_or_none()
        )
        draft = (
            drafts.get(str(mirror.core_sales_order_line_id))
            if mirror is not None and mirror.core_sales_order_line_id
            else None
        )
        decision = (draft or {}).get("decision") or {}
        reason = str(decision.get("reason") or "").strip()
        if mirror is None or draft is None or decision.get("verdict") != "rejected" or not reason:
            raise AppException(
                status_code=422,
                message="Say why this line is being refused first.",
                code="board_line_reject_reason_required",
            )
        reasons[str(project_line_id)] = reason
        sentences.append(f"Line {mirror.line_no} rejected: {reason}")
    return reasons, "; ".join(sentences)


def _restamp_superseded_reason(db: Session, before_id, reason: str) -> None:
    """`_write_decision` always stamps a reconfirmed revision "Reconfirmed by CS." -
    right for an ordinary reconfirm, wrong for one that ALSO carried a withdrawal
    (owner ruling 23 Sep 2026): the audit is what the planner actually said, the same
    trade `uncover_lines` already makes for its own whole-revision branch. `before_id` is
    the decision that was active before `confirm()` ran, read by the caller before the
    call - the same row `confirm()` supersedes, so this corrects it in place rather than
    guessing which revision to look up.
    """
    if not before_id or not reason:
        return
    from app.models.project_so import SOSupplyDecision

    decision = (
        db.query(SOSupplyDecision).filter(SOSupplyDecision.id == before_id).one_or_none()
    )
    if decision is not None:
        decision.superseded_reason = reason
        db.flush()


def _withdrawal_only_result(
    db: Session, service: ProjectSupplyService, order, rejected_line_ids: list
) -> Dict[str, Any]:
    """`ConfirmResult`-shaped answer for a Confirm that named NO lines of its own -
    `uncover_lines` already ran (either of its two branches: some other covered line
    survives in a fresh revision, or none does and the order goes back to undecided) -
    built here rather than returned by `uncover_lines` itself, which keeps its own bare
    `bool` because the purchasing-refusal path (its other caller) never wanted more.
    """
    from app.models.project_so import DECISION_ACTIVE, SOSupplyDecision

    active = (
        db.query(SOSupplyDecision)
        .filter(
            SOSupplyDecision.project_sales_order_id == order.id,
            SOSupplyDecision.state == DECISION_ACTIVE,
        )
        .one_or_none()
    )
    decided = len(active.line_snapshots or []) if active is not None else 0
    total = len(service.lines_of(str(order.id)))
    suspected = (
        sum(
            1
            for snapshot in (active.line_snapshots or [])
            if (snapshot or {}).get("suspected_system_issue")
        )
        if active is not None
        else 0
    )
    return {
        "revision_no": active.revision_no if active is not None else 0,
        "confirmed_at": active.confirmed_at if active is not None else None,
        # Nit (fix round, review): a fixed literal, never read off `order`, the SAME
        # convention `_write_decision`'s own `ConfirmResult` follows for every OTHER
        # Confirm - this field states that the PRESS committed, not a workflow state the
        # order itself carries (the order has none at this granularity); a withdrawal
        # that retires the whole revision (AC-B3) still answers "confirmed" for that
        # reason, same as a press that confirms nothing new but keeps the order covered.
        "review_state": "confirmed",
        "inquiry_rows_created": 0,
        "exceptions": [],
        "lines_decided": decided,
        "lines_undecided": max(total - decided, 0),
        "lines_confirmed": [],
        "lines_carried": decided,
        "transfers_written": 0,
        "transfers_failed": 0,
        "transfers_kept": 0,
        "suspected_issues": suspected,
        "rejected_count": len(rejected_line_ids),
    }


def _withdrawn_entries(db: Session, service: ProjectSupplyService, order, line_ids: list) -> list:
    """The lines a press took out of the confirmation, named the way `lines_confirmed` is."""
    from app.models.product import Product

    wanted = {str(x) for x in line_ids}
    by_id = {str(line.id): line for line in service.lines_of(str(order.id)) if str(line.id) in wanted}
    product_ids = [line.product_id for line in by_id.values() if line.product_id]
    codes = (
        {
            str(row.id): row.product_code
            for row in db.query(Product.id, Product.product_code).filter(
                Product.id.in_(product_ids)
            )
        }
        if product_ids
        else {}
    )
    return [
        {
            "project_line_id": str(line_id),
            "line_no": by_id[str(line_id)].line_no if str(line_id) in by_id else None,
            "item_code": codes.get(str(by_id[str(line_id)].product_id or ""))
            if str(line_id) in by_id
            else None,
        }
        for line_id in line_ids
    ]


def _confirm_with_possible_rejects(
    db: Session,
    service: ProjectSupplyService,
    order,
    payload: Any,
    *,
    actor_user_id: str,
    batch_id: Optional[str],
    confirm_batch,
) -> Dict[str, Any]:
    """The one seam both Confirm routes share (owner ruling 23 Sep 2026,
    `PLAN-board-reject-on-confirmed-line.md`): a press may REPLACE lines (`payload.lines`),
    WITHDRAW covered ones (`payload.rejected_line_ids`), or both - Confirm is what commits
    a staged rejection, never the draft save.

    `batch_id` is the CALLER's own resolved id (`confirm_supply` reads `payload.batch_id`
    directly; `confirm_all`'s `write_one` folds in the body-level fallback first) - kept as
    an explicit argument rather than read off `payload` again so the two callers' different
    fallback rules never have to be reconciled here.

    A pending planning change may not ALSO carry a withdrawal (422): the batch apply
    (`_confirm_a_planning_change`) has no shape for `uncover_line_ids` - it turns the
    board's own lines into the batch rows' compositions - so a press naming both is refused
    outright rather than silently dropping one half (`PLAN-board-reject-on-confirmed-line.md`
    Design section, "Not in scope").

    UNDO JOURNAL (S1, #978, project_supply_undo_service.py's own module docstring): a
    revision `uncover_lines` mints on its OWN (nothing named in `payload.lines`) is NEVER
    wrapped in `UndoJournal` here - that is the existing, deliberate invariant the docstring
    states ("a revision minted anywhere else - `uncover_lines` after a rejection... is never
    wrapped... not undoable"), and this rework's `elif rejected_line_ids:` branch below is
    exactly that call. A withdrawal riding alongside `payload.lines` reaches `uncover_lines`
    THROUGH `confirm()`'s own `uncover_line_ids` parameter instead, which stays inside the
    journal exactly as an ordinary reconfirm does - undoable, as the plan states.
    """
    named = {str(entry.project_line_id) for entry in getattr(payload, "lines", []) or []}
    # DEDUPED (nit, fix round, review): a line named twice in `rejected_line_ids` is one
    # withdrawal, not two - `dict.fromkeys` keeps the FIRST occurrence's order, which
    # matters only for the joined `superseded_reason` sentence reading naturally.
    rejected_line_ids = list(
        dict.fromkeys(str(x) for x in (getattr(payload, "rejected_line_ids", None) or []))
    )
    if batch_id and rejected_line_ids:
        raise AppException(
            status_code=422,
            message=(
                "A pending planning change cannot also withdraw a rejected line in the "
                "same press. Apply the change first, then reject the line and confirm "
                "again."
            ),
            code="board_reject_not_supported_in_batch",
        )
    reasons, joined_reason = _reasons_for_rejected_lines(
        db, order, rejected_line_ids, named_lines=named
    )
    before_id = _active_decision_id(db, order)
    if batch_id:
        with UndoJournal(db) as journal:
            body = confirm_batch()
        _attach_undo_journal(db, order, journal, before_id)
    elif getattr(payload, "lines", None):
        with UndoJournal(db) as journal:
            body = service.confirm(
                order,
                payload,
                actor_user_id=actor_user_id,
                uncover_line_ids=rejected_line_ids,
                # S2/S3 (rework fix round): the BARE per-line reason, so each withdrawn
                # line's own OI row note reads "Taken out of the confirmation: <its own
                # reason>" rather than the ordinary carry-forward's "Superseded by
                # revision N" - `joined_reason` stays reserved for `superseded_reason`
                # below.
                uncover_reason_by_line=reasons,
            )
            # AC-B14 (fix round, review): INSIDE the journal, not after it closes.
            # `_write_decision` (`service.confirm`, just above) stamps the superseded
            # revision "Reconfirmed by CS." WHILE the journal is open, so the journal's
            # own `update` entry for `superseded_reason` records that value as its
            # `new`. Restamping AFTER the `with` block exits (the first cut of this
            # rework) left the LIVE column reading the joined sentence while the
            # journal still said "Reconfirmed by CS." - `_changed_refusal` compares
            # exactly those two and refused every undo of this path with 409
            # `changed`, on a row nothing but this confirm itself had touched.
            if rejected_line_ids:
                _restamp_superseded_reason(db, before_id, joined_reason)
                body["rejected_count"] = len(rejected_line_ids)
                body["lines_withdrawn"] = _withdrawn_entries(db, service, order, rejected_line_ids)
        _attach_undo_journal(db, order, journal, before_id)
    elif rejected_line_ids:
        # NOT wrapped in `UndoJournal` - see this function's own docstring.
        uncovered = service.uncover_lines(
            order,
            rejected_line_ids,
            actor_user_id=actor_user_id,
            reason=joined_reason,
            reason_by_line=reasons,
        )
        # B1 (fix round, review, belt and braces): `_reasons_for_rejected_lines` above
        # already refuses an id the active decision does not cover, so this should never
        # actually be `False` - kept as a second guard rather than trusting that no
        # future caller of `uncover_lines` ever drifts the two checks apart.
        if not uncovered:
            raise AppException(
                status_code=422,
                message="This line is not confirmed any more. Reload the board.",
                code="board_line_withdrawal_not_covered",
            )
        body = _withdrawal_only_result(db, service, order, rejected_line_ids)
        body["lines_withdrawn"] = _withdrawn_entries(db, service, order, rejected_line_ids)
    else:
        with UndoJournal(db) as journal:
            body = service.confirm(order, payload, actor_user_id=actor_user_id)
        _attach_undo_journal(db, order, journal, before_id)
    return body


@router.post("/sales-orders/{pso_id}/confirm", response_model=ConfirmResult)
def confirm_supply(
    pso_id: str,
    payload: ConfirmSupplyBody,
    current_user: dict = Depends(require_permission(EDIT)),
    db: Session = Depends(get_db),
):
    """Confirm the lines the planner has decided, in one action (AC-C01 as amended).

    Every line IN THIS CONFIRMATION commits together or none of them does: the service
    rechecks each against authoritative facts, and one stale, unbalanced or unmapped line
    refuses the lot with `failing_lines` naming each by line number and item code. A line
    the body does not name is left undecided on purpose (13.4) and keeps counting as
    demand; a body naming no line at all AND withdrawing none is refused. The Order Inquiry
    handoff runs inside this same transaction, so purchasing can never be told to buy
    something that was not also promised.

    `payload.rejected_line_ids` (owner ruling 23 Sep 2026,
    `PLAN-board-reject-on-confirmed-line.md`, hand-test feedback, "we should confirm the
    rejection"): the mirror ids of covered lines the board staged a `rejected` draft on.
    This press is what actually takes them OUT of the confirmation - `save_draft` only
    ever writes the draft. See `_confirm_with_possible_rejects`'s own docstring for the
    shared seam both Confirm routes run this through.
    """
    try:
        validate_uuid_path(pso_id, resource="Sales order")
        service = ProjectSupplyService(db)
        order = service.get_order(pso_id)
        _assert_can_act_on(db, order, current_user)
        body = _confirm_with_possible_rejects(
            db,
            service,
            order,
            payload,
            actor_user_id=current_user["id"],
            batch_id=payload.batch_id,
            confirm_batch=lambda: _confirm_a_planning_change(
                db, order, payload, current_user["id"]
            ),
        )
        db.commit()
        return body
    except Exception as exc:
        db.rollback()
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@dataclass
class _BatchedEntry:
    """One order's half of a batched `confirm-all`, in the shape the batch apply reads.

    `_confirm_a_planning_change` takes a `ConfirmSupplyBody` - `.lines` and `.batch_id` -
    while `ConfirmManyOrderBody` carries the lines under a body-level batch. This joins the
    two without giving every per-order entry a batch field it would have to be told to
    ignore.
    """

    lines: list
    batch_id: str


def _confirm_a_planning_change(
    db, order, payload, actor_user_id: str, *, preview: bool = False
) -> dict:
    """The board's Confirm, pressed on a board opened at `?batch=<id>` (part 3, AC-P3-4).

    ONE press, ONE call, ONE revision: the lines the planner composed become the batch
    rows' own compositions, and the batch is applied - which is what writes the revision,
    cancels the closed lines' rows, shifts their links and updates the surviving row in
    place. Confirming the batch through some second endpoint would have meant two writes
    and two chances for one of them to be the only one that landed.

    A line the press decided that the batch does NOT carry still goes in, as an ordinary
    confirmation beside the batch's rows: the planner pressed one button and is entitled
    to have it mean what the screen said.

    **THIS ORDER, AND NO OTHER.** A book upload moves many orders at once and the board
    presses Confirm per order, so the apply is narrowed to this one (`only_pso_ids`).
    Applying the whole batch off one press wrote revisions for orders nobody had
    confirmed, skipped the per-order permission check `_assert_can_act_on` gives this one,
    and stamped `applied_at`, which locked every remaining order's rows.

    A second press is REFUSED (409), not answered with the first press's revision number.
    """
    from app.models.planning_change import PlanningChangeRow
    from app.services import planning_change_service
    from app.services.planning_change_service import PLANNING_CHANGE_STATE_APPLIED
    from app.services.project_supply_service import SupplyLinesRefused

    batch_id = payload.batch_id
    validate_uuid_path(batch_id, resource="Planning change batch")
    rows = (
        db.query(PlanningChangeRow)
        .filter(
            PlanningChangeRow.batch_id == batch_id,
            PlanningChangeRow.project_sales_order_id == str(order.id),
        )
        .all()
    )
    if rows and all(r.applied_state == PLANNING_CHANGE_STATE_APPLIED for r in rows):
        # This ORDER is done even though the batch as a whole may not be: another order of
        # the same upload can still be waiting, so `applied_at` is not the thing to read.
        raise AppException(
            status_code=409,
            message="This planning change was already applied to this sales order.",
            code="planning_change_batch_applied",
        )
    by_line = {str(row.project_line_id): row for row in rows if row.project_line_id}

    extra: list[dict] = []
    for line in payload.lines:
        composition = line.model_dump()
        row = by_line.get(str(line.project_line_id))
        if row is None:
            extra.append(composition)
            continue
        if row.kind == "cancelled":
            # The book CANCELLED this line. Approving it on the board means "yes, do what
            # the book did" - it is not an amendment of the line's supply, and there is no
            # line left to compose one for. Posting it as an `amend` sent it down the
            # confirm branch instead, so the retire-and-shift never fired from the board at
            # all. Apply dispatches on the kind (Slice C contract D), so nothing has to be
            # recorded here; the row is still marked applied with the rest of them.
            continue
        planning_change_service.set_row_decision(
            db, batch_id, str(row.id), "amend", composition
        )

    result = planning_change_service.apply(
        db,
        batch_id,
        actor_user_id,
        extra_confirm_lines={str(order.id): extra},
        refuse_if_applied=True,
        only_pso_ids={str(order.id)},
        notify=not preview,
    )
    failed = [
        entry for entry in result["failed_orders"]
        if entry.get("so_number")
    ]
    outcome = (result.get("outcomes") or {}).get(str(order.id))
    confirmed = (outcome or {}).get("confirm_result")
    if confirmed is None:
        message = (
            failed[0]["reason"]
            if failed
            else "Nothing on this planning change could be confirmed."
        )
        # WHICH line, and why. An ordinary Confirm answers with `failing_lines` beside the
        # sentence and the sheet marks the row; the batch path dropped them, so the same
        # refusal read as "1 line cannot be confirmed" with nothing to act on.
        failing_lines = failed[0].get("failing_lines") if failed else None
        if failing_lines:
            raise SupplyLinesRefused(
                status_code=422,
                message=message,
                failing_lines=failing_lines,
                code="planning_change_not_confirmed",
            )
        raise AppException(
            status_code=422,
            message=message,
            code="planning_change_not_confirmed",
        )
    return confirmed


@router.post("/sales-orders/{pso_id}/reconcile", response_model=ReconciliationSummary)
def rerun_reconciliation(
    pso_id: str,
    current_user: dict = Depends(require_permission(EDIT)),
    db: Session = Depends(get_db),
):
    """Re-run after CS has answered whatever was in the way.

    Idempotent, so the button is safe to press on an order that is already clean: the same
    links are kept, the same summary comes back.
    """
    try:
        validate_uuid_path(pso_id, resource="Sales order")
        order = ProjectSODraftService(db).get_order(pso_id)
        _assert_can_act_on(db, order, current_user)
        body = ProjectSOReconciliationService(db).reconcile(order)
        db.commit()
        return body
    except Exception as exc:
        db.rollback()
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))
