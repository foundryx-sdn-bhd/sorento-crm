"""Order inquiry rows, their state and the export (P10, AC-I1 to AC-I7).

Reading is the project's own view grant, because the inquiry is part of reading a
project. ACTING on a row is `projects.order_inquiry.action`, which is purchasing's grant
rather than the project owner's: the row is purchasing's work and they do not own the
project it came from, so gating it on project edit would mean granting purchasing the
right to edit every pursuit in the company.

Rows are never created here. They are DERIVED when a sales order or an amendment
publishes, which is the only moment the instruction is true.
"""
from __future__ import annotations

import logging
from typing import List, Literal, Optional

from fastapi import APIRouter, Body, Depends, Query, Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import (
    require_any_permission,
    require_permission,
    require_permission_with_api_key,
)
from app.schemas.common import ListResponse, MAX_PAGE_LIMIT
from app.schemas.download import DownloadResponse
from app.schemas.project_order_inquiry import (
    AcknowledgeResult,
    AcknowledgeRowsRequest,
    AutoPlaceRequest,
    AutoPlaceResult,
    CommitReserveRequestIn,
    HandoverResendIn,
    HandoverResendPreviewOut,
    HandoverResendSendOut,
    ParkedPreviewOut,
    ParkedSendOut,
    ReserveNoticeRecipientsOut,
    ReserveStatusSummaryOut,
    CreateReserveRequestIn,
    LinkNowRequest,
    MarkInquiryRowsRequest,
    OrderInquiryDetail,
    OrderInquiryHeaderDetailOut,
    OrderInquiryHeaderOut,
    OrderInquiryMatrixResponse,
    OrderInquiryPoCandidate,
    OrderInquiryPoCandidatesResponse,
    OrderInquiryPoDetail,
    OrderInquiryRelatedDocumentsOut,
    OrderInquiryReserveRequestOut,
    OrderInquiryRowOut,
    OrderInquirySpoDetail,
    OrderInquirySummary,
    OrderInquiryWorklistExportRequest,
    OrderInquiryWorklistRow,
    OrderInquiryWorklistSummary,
    PlaceOnPoRequest,
    RejectRowRequest,
    RejectRowsRequest,
    RejectRowsResult,
    ReserveHistoryEntryOut,
    UnacknowledgeResult,
    UnacknowledgeRowsRequest,
    UnlinkRequest,
    UnplaceAllPreview,
    UnplaceAllRequest,
    UnplaceAllResult,
    UploadJobScope,
    WORKLIST_FILTER_MAX_LENGTH,
    WORKLIST_QUERY_MAX_LENGTH,
)
from app.models.inventory import Warehouse
from app.models.project_so import (
    OrderInquiry,
    OrderInquiryReserveRequest,
    OrderInquiryReserveRequestRow,
)
from app.schemas.decision_trail import DecisionTrailResponse
from app.services import project_service as projects
from app.services.decision_trail_service import DecisionTrailService
from app.services.download_service import DownloadService
from app.services.error_handler import AppException, handle_internal_error
from app.services.order_inquiry_header_service import OrderInquiryHeaderService
from app.services.order_inquiry_reserve_service import OrderInquiryReserveService, as_utc
from app.services.order_inquiry_worklist_service import OrderInquiryWorklistService
from app.services.project_order_inquiry_service import ProjectOrderInquiryService
from app.services.scm.summary_order_service import compact_ddmmyyyy
from app.services.user_service import UserPermissionService
from app.services.uuid_path_param import UUID_PATTERN, validate_uuid_path
from app.utils.http import content_disposition

logger = logging.getLogger(__name__)

router = APIRouter()

VIEW = "projects.projects.view"
ACTION = "projects.order_inquiry.action"
#: The handshake (`PLAN-scm-oi-handshake.md`, captain 27 Aug 2026): acknowledging is
#: purchasing taking CS's instruction on, and it is what links documents to a row. Its own
#: grant rather than `ACTION`, because CS holds that one for their own screens and must
#: not be able to acknowledge their own instructions.
ACKNOWLEDGE = "projects.order_inquiries.acknowledge"
#: R1 (`PLAN-oi-request-cs-reserve.md`): only the CS head confirms a reserve. Held by
#: whoever may commit stock against a request - separate from `ACKNOWLEDGE`, which is
#: purchasing's own grant to raise the request in the first place.
RESERVE = "projects.order_inquiries.reserve"
#: Lane B (`PLAN-order-sheet-oi-reports-22sep.md`, AC-B7 / `app/dependencies.py`'s own
#: rule): a WRITE endpoint - it creates a `user_downloads` row and enqueues a
#: background render - is never reachable by `X-API-Key` alone, unlike the sync GET it
#: replaces. Same permission slug as `VIEW` (exporting states nothing new, it only
#: prints what the worklist already answers) but the real-signed-in-user dependency,
#: mirroring `order_summary.py`'s own `_EXPORT`.
_EXPORT = require_permission(VIEW)

#: The sort set the list accepts, declared here as a `Literal` because FastAPI cannot
#: build one from a runtime set. It MUST equal `SORTABLE_FIELDS` in the service, and a
#: test asserts the two agree.
WorklistSort = Literal[
    "inquiry_no",
    "so_date",
    "so_number",
    "item_code",
    "product_name",
    "qty",
    "delivery_date",
    "project_customer",
    "customer_name",
    "project_title",
    "supplier",
    "po_number",
    "state",
    "raised_at",
    "raised_by_name",
    "location",
    "agent",
    # The three columns the worklist grid draws a sort arrow on under a DIFFERENT id
    # than an existing key, or under no key at all (18 Sep 2026 bug report): the FE
    # sends its own column id verbatim as `sort`, so the id is what has to be accepted
    # here, not a renaming of it.
    "spo_number",
    "agent_code",
    "verb",
]

WORKLIST_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

#: The vertical axis a Schedule matrix column can group by (S3, R-I second half).
MatrixAxis = Literal["product", "sales_order", "customer", "agent"]
#: The date cut a matrix row is bucketed by. Week is the default, matching the planning
#: board's own.
MatrixGranularity = Literal["day", "week", "month", "year"]


def _validate_worklist_filter_uuids(filters: dict) -> None:
    """The UUID guard every worklist filter caller must run before a value reaches SQL
    (AC-CF-8d): `project_id`, `supplier_id` and `agent` are UUID columns, and a
    malformed one otherwise reaches Postgres as `invalid input syntax for type uuid` -
    a 500 carrying the statement. Shared between `_worklist_filters` (the list,
    summary and matrix routes) and the acknowledge route's `filter` branch
    (`AcknowledgeFilter`), so a bad id is refused the same way through either door
    rather than only the one that happens to call `validate_uuid_path` directly.
    """
    project_id = filters.get("project_id")
    if project_id:
        validate_uuid_path(project_id, resource="Project")
    supplier_id = filters.get("supplier_id")
    if supplier_id:
        validate_uuid_path(supplier_id, resource="Supplier")
    # `agent` is `sales_agents.id`, validated the same way - a malformed value is a
    # caller error, not a filter that silently matches nothing.
    agent = filters.get("agent")
    if agent:
        validate_uuid_path(agent, resource="Sales agent")
    # S3 (`PLAN-oi-header-list-detail.md`): the OI detail page's own scope.
    inquiry_id = filters.get("inquiry_id")
    if inquiry_id:
        validate_uuid_path(inquiry_id, resource="Order inquiry")


def _worklist_filters(
    query: Optional[str],
    delivery_month: Optional[str],
    raised_date: Optional[str],
    state: Optional[str],
    project_id: Optional[str],
    supplier_id: Optional[str],
    raised_by: Optional[str] = None,
    linked: Optional[str] = None,
    kind: Optional[str] = None,
    ack: Optional[str] = None,
    # S1, R-K (`PLAN-scm-oi-worklist-excel-parity.md`).
    location: Optional[str] = None,
    agent: Optional[str] = None,
    so_month: Optional[str] = None,
    po_number: Optional[str] = None,
    spo_number: Optional[str] = None,
    delivery_from: Optional[str] = None,
    delivery_to: Optional[str] = None,
    # S3: a Schedule cell's own rows. The LIST's drilldown only - the matrix route names
    # its axis as its own argument.
    axis: Optional[str] = None,
    axis_key: Optional[str] = None,
    # S5 (`PLAN-oi-project-label-from-so.md` section 5): the Project filter's own text,
    # exact match on the SAME `_PROJECT_TITLE` the column and `_projects()` facet read -
    # separate from `project_id`, which stays UUID-validated for any existing deep link.
    project: Optional[str] = None,
    # S3 (`PLAN-oi-header-list-detail.md`): the OI detail page's own Lines tab.
    inquiry_id: Optional[str] = None,
) -> dict:
    filters = {
        "query": query,
        "delivery_month": delivery_month,
        "raised_date": raised_date,
        "state": state,
        "project_id": project_id,
        "project": project,
        "supplier_id": supplier_id,
        # `users.id` is a plain string, not a UUID column - it is never validated as one.
        "raised_by": raised_by,
        "linked": linked,
        "kind": kind,
        "ack": ack,
        "location": location,
        "agent": agent,
        "so_month": so_month,
        "po_number": po_number,
        "spo_number": spo_number,
        "delivery_from": delivery_from,
        "delivery_to": delivery_to,
        "inquiry_id": inquiry_id,
    }
    _validate_worklist_filter_uuids(filters)
    # `axis_key` is NOT validated here: it is compared against a UUID column on every
    # axis, so it needs the same guard, but a QUERY param has no "missing row" reading
    # and `validate_uuid_path` answers 404 ("Schedule cell not found") - the lie
    # `uuid_path_param`'s own note warns about. It carries `pattern=UUID_PATTERN` on the
    # list route below instead, which FastAPI refuses with a 422 before this runs.
    # Absent unless a cell asked for them: the matrix route takes `axis` as its own
    # argument, and a key of the same name in this dict would collide with it.
    if axis and axis_key:
        filters["axis"] = axis
        filters["axis_key"] = axis_key
    return filters


#: The longest search string the worklist routes accept. The service caps the number of
#: WORDS it applies; this caps the string itself, before any of them are read. Shared
#: with `AcknowledgeFilter` (`app/schemas/project_order_inquiry.py`) rather than
#: retyped, so the list route and the `filter` branch of Confirm enforce the same cap.
_MAX_QUERY_LENGTH = WORKLIST_QUERY_MAX_LENGTH
#: The same cap on every other free-text filter (location, PO number, SPO number, a
#: matrix cell's key). They reach an `ilike` or an equality over a joined query, and a
#: megabyte of "x" is not a search anybody typed.
_MAX_FILTER_LENGTH = WORKLIST_FILTER_MAX_LENGTH


@router.get("/order-inquiries", response_model=ListResponse[OrderInquiryWorklistRow])
def list_order_inquiry_worklist(
    query: Optional[str] = Query(
        None,
        # A search box is a public-facing string, and every word in it costs another OR
        # across eleven columns of a joined query: the service applies the first ten words,
        # and nothing longer than this reaches it at all.
        max_length=_MAX_QUERY_LENGTH,
        description=(
            "One box. Matches the sales-order number, the item code, the product name or "
            "code, the customer, the project, and the name of the person who raised it "
            "(or the front of their email address)."
        ),
    ),
    delivery_month: Optional[str] = Query(
        None, description="`YYYY-MM`. The sheet tab purchasing works a month at a time."
    ),
    raised_date: Optional[str] = Query(
        None, description="`YYYY-MM-DD`. What was raised on one day, their per-day tab."
    ),
    # A closed set for the same reason `sort` is: a filter nothing can equal reads on
    # screen as "no work to do" when the truth is "that is not a state".
    state: Optional[Literal["raised", "partly_linked", "actioned", "cancelled", "placed"]] = Query(None),
    project_id: Optional[str] = Query(None),
    project: Optional[str] = Query(
        None,
        description=(
            "The Project column's own text, exact match - a registered project's title "
            "or an adopted order's SO-level label, off the summary's own `projects` "
            "facet. Separate from `project_id`, which stays UUID-only. No length bound: "
            "`projects.title` is TEXT with none, so a bounded param here would let the "
            "facet offer an option the filter itself refused."
        ),
    ),
    supplier_id: Optional[str] = Query(None),
    raised_by: Optional[str] = Query(
        None,
        description=(
            "The person who raised the rows, by id, off the summary's own list. Matches a "
            "row's supply revision confirmer, or its inquiry header when it has none."
        ),
    ),
    linked: Optional[Literal["po", "spo", "none"]] = Query(
        None,
        description=(
            "WHERE the row is linked (AC-I5). `po` / `spo` mean it holds at least one "
            "link of that kind; `none` means no link at all, which is the buyer's own "
            "worklist. A closed set for the same reason `state` is."
        ),
    ),
    kind: Optional[Literal["spo", "po", "buy"]] = Query(
        None,
        description=(
            "WHAT the row still needs - the three cards above the schedule and the list "
            "(AC-I11). Every row CARRYING that kind, so a row linked 5 of 8 to a "
            "purchase order answers to `po` and to `buy` alike, and a cancelled row to "
            "neither. A different question from `linked`, which asks only where a row's "
            "links point."
        ),
    ),
    ack: Optional[Literal["awaiting", "acknowledged", "changed", "rejected", "to_confirm"]] = Query(
        None,
        description=(
            "WHERE THE HANDSHAKE STANDS (AC-H4). `awaiting` is what purchasing has not "
            "taken on yet, `changed` is a row CS amended after it was acknowledged, and "
            "`rejected` is one purchasing refused with a reason. `to_confirm` is the "
            "page's own default (R3) and means awaiting OR changed - one question asked "
            "of two stored states, never a fifth state a row can be in. A third question "
            "beside `state` and `linked`, and a closed set for the same reason both of "
            "those are."
        ),
    ),
    sort: Optional[WorklistSort] = Query(
        None, description="Defaults to delivery_date. Nulls always last."
    ),
    direction: Optional[Literal["asc", "desc"]] = Query(
        "asc",
        alias="dir",
        description="Nulls sort last in BOTH directions, never first on desc.",
    ),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=MAX_PAGE_LIMIT),
    location: Optional[str] = Query(
        None,
        max_length=_MAX_FILTER_LENGTH,
        description="The row's Location column, equality (S1, R-K).",
    ),
    agent: Optional[str] = Query(
        None, description="The row's Agent column, equality, by `sales_agents.id`."
    ),
    so_month: Optional[str] = Query(
        None, description="`YYYY-MM` on the SO date, not the delivery date."
    ),
    po_number: Optional[str] = Query(
        None,
        max_length=_MAX_FILTER_LENGTH,
        description=(
            "Prefix, case-insensitive. Matches a PO link's own document, or an SPO "
            "link's source purchase order (AC-F5b)."
        ),
    ),
    spo_number: Optional[str] = Query(
        None,
        max_length=_MAX_FILTER_LENGTH,
        description="Prefix, case-insensitive. Matches an SPO link's document.",
    ),
    delivery_from: Optional[str] = Query(
        None, description="`YYYY-MM-DD`, inclusive. A schedule-matrix cell's own period."
    ),
    delivery_to: Optional[str] = Query(None, description="`YYYY-MM-DD`, inclusive."),
    axis: Optional[MatrixAxis] = Query(
        None,
        description=(
            "A Schedule cell's own drilldown (S3): the axis its `axis_key` is a key of. "
            "Both are needed; either alone filters nothing."
        ),
    ),
    axis_key: Optional[str] = Query(
        None,
        max_length=_MAX_FILTER_LENGTH,
        pattern=UUID_PATTERN,
        description=(
            "Equality on the matrix's grouping column for `axis`. A UUID on every axis "
            "(`products.id`, the sales order's own id, `customers.id`, "
            "`sales_agents.id`) - a malformed one reached Postgres as `invalid input "
            "syntax for type uuid`, a 500 carrying the statement."
        ),
    ),
    inquiry_id: Optional[str] = Query(
        None,
        description=(
            "S3: the OI detail page's own Lines tab - every row of exactly this header, "
            "by `order_inquiries.id`."
        ),
    ),
    include_history: bool = Query(
        False,
        description=(
            "`PLAN-oi-no-double-count-25sep.md` S1 (AC-ND-20): cancelled rows too, "
            "so the OI detail's Lines tab reads a line's whole history in its one "
            "fetch. An explicit `state` still wins. Off, the response is unchanged."
        ),
    ),
    _user: dict = Depends(require_permission_with_api_key(VIEW)),
    db: Session = Depends(get_db),
):
    """Everything purchasing has been told to buy, whoever it belongs to.

    Cross-project because purchasing is: an order ADOPTED from the AutoCount book has no
    project registration at all, so its rows appear on no per-project list and were
    reachable only from the one sales order that raised them.

    Plain ``def``, so FastAPI runs the whole handler in a threadpool: it is synchronous
    SQLAlchemy over a page of rows, and on the event loop it holds up every other request
    the worker is serving.
    """
    try:
        return OrderInquiryWorklistService(db).list_rows(
            page=page,
            limit=limit,
            sort=sort,
            direction=direction,
            include_cancelled=include_history,
            **_worklist_filters(
                query,
                delivery_month,
                raised_date,
                state,
                project_id,
                supplier_id,
                raised_by,
                linked,
                kind,
                ack,
                location,
                agent,
                so_month,
                po_number,
                spo_number,
                delivery_from,
                delivery_to,
                axis=axis,
                axis_key=axis_key,
                inquiry_id=inquiry_id,
                project=project,
            ),
        )
    except Exception as exc:
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.get("/order-inquiries/summary", response_model=OrderInquiryWorklistSummary)
def order_inquiry_worklist_summary(
    query: Optional[str] = Query(None, max_length=_MAX_QUERY_LENGTH),
    delivery_month: Optional[str] = Query(None),
    raised_date: Optional[str] = Query(None),
    state: Optional[Literal["raised", "partly_linked", "actioned", "cancelled", "placed"]] = Query(None),
    project_id: Optional[str] = Query(None),
    project: Optional[str] = Query(None),
    supplier_id: Optional[str] = Query(None),
    raised_by: Optional[str] = Query(None),
    linked: Optional[Literal["po", "spo", "none"]] = Query(None),
    kind: Optional[Literal["spo", "po", "buy"]] = Query(
        None,
        description=(
            "The pressed card (AC-I11). The TOTALS honour it, because they describe what "
            "is on screen; the `kinds` facet itself drops it, because a card that emptied "
            "the two beside it could not be pressed a second time."
        ),
    ),
    ack: Optional[Literal["awaiting", "acknowledged", "changed", "rejected", "to_confirm"]] = Query(
        None,
        description=(
            "WHERE THE HANDSHAKE STANDS (AC-H4). `awaiting` is what purchasing has not "
            "taken on yet, `changed` is a row CS amended after it was acknowledged, and "
            "`rejected` is one purchasing refused with a reason. A third question beside "
            "`state` and `linked`, and a closed set for the same reason both of those are."
        ),
    ),
    location: Optional[str] = Query(None, max_length=_MAX_FILTER_LENGTH),
    agent: Optional[str] = Query(None),
    so_month: Optional[str] = Query(None),
    po_number: Optional[str] = Query(None, max_length=_MAX_FILTER_LENGTH),
    spo_number: Optional[str] = Query(None, max_length=_MAX_FILTER_LENGTH),
    delivery_from: Optional[str] = Query(None),
    delivery_to: Optional[str] = Query(None),
    _user: dict = Depends(require_permission_with_api_key(VIEW)),
    db: Session = Depends(get_db),
):
    """The strip above the list, and the month / supplier / project controls beside it."""
    try:
        return OrderInquiryWorklistService(db).summary(
            **_worklist_filters(
                query,
                delivery_month,
                raised_date,
                state,
                project_id,
                supplier_id,
                raised_by,
                linked,
                kind,
                ack,
                location,
                agent,
                so_month,
                po_number,
                spo_number,
                delivery_from,
                delivery_to,
                project=project,
            ),
        )
    except Exception as exc:
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.get("/order-inquiries/export")
def export_order_inquiry_worklist(
    query: Optional[str] = Query(None, max_length=_MAX_QUERY_LENGTH),
    delivery_month: Optional[str] = Query(None),
    raised_date: Optional[str] = Query(None),
    state: Optional[Literal["raised", "partly_linked", "actioned", "cancelled", "placed"]] = Query(None),
    project_id: Optional[str] = Query(None),
    project: Optional[str] = Query(None),
    supplier_id: Optional[str] = Query(None),
    raised_by: Optional[str] = Query(None),
    linked: Optional[Literal["po", "spo", "none"]] = Query(None),
    kind: Optional[Literal["spo", "po", "buy"]] = Query(None),
    ack: Optional[Literal["awaiting", "acknowledged", "changed", "rejected", "to_confirm"]] = Query(None),
    location: Optional[str] = Query(None, max_length=_MAX_FILTER_LENGTH),
    agent: Optional[str] = Query(None),
    so_month: Optional[str] = Query(None),
    po_number: Optional[str] = Query(None, max_length=_MAX_FILTER_LENGTH),
    spo_number: Optional[str] = Query(None, max_length=_MAX_FILTER_LENGTH),
    delivery_from: Optional[str] = Query(None),
    delivery_to: Optional[str] = Query(None),
    inquiry_id: Optional[str] = Query(
        None,
        description=(
            "S3 (`PLAN-oi-header-list-detail.md`): the OI detail page's own Export "
            "Excel - exactly this header's own rows."
        ),
    ),
    _user: dict = Depends(require_permission_with_api_key(VIEW)),
    db: Session = Depends(get_db),
):
    """The filtered set as the workbook purchasing already reads: a sheet per month.

    Generated per request rather than stored, exactly as the per-project export is: a
    stored file goes stale the moment supply is reconfirmed, and a stale instruction is
    the thing this replaces.
    """
    try:
        filename, body = OrderInquiryWorklistService(db).export_xlsx(
            **_worklist_filters(
                query,
                delivery_month,
                raised_date,
                state,
                project_id,
                supplier_id,
                raised_by,
                linked,
                kind,
                ack,
                location,
                agent,
                so_month,
                po_number,
                spo_number,
                delivery_from,
                delivery_to,
                project=project,
                inquiry_id=inquiry_id,
            )
        )
        return Response(
            content=body,
            media_type=WORKLIST_XLSX,
            headers={"Content-Disposition": content_disposition(filename)},
        )
    except Exception as exc:
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.post("/order-inquiries/export", response_model=DownloadResponse)
def export_order_inquiry_worklist_async(
    payload: OrderInquiryWorklistExportRequest = Body(default_factory=OrderInquiryWorklistExportRequest),
    current_user: dict = Depends(_EXPORT),
    db: Session = Depends(get_db),
):
    """Lane B, AC-B6/R4 (`PLAN-order-sheet-oi-reports-22sep.md`): the list page's own
    Export Excel, through My Downloads - the GET above stays for one release (MCP /
    other callers), this is what the screen calls now.

    Same shape as `export_order_summary` (`app/api/v1/scm/order_summary.py`): create
    the `user_downloads` row, enqueue the render, mark it failed and answer 503 on an
    enqueue failure rather than leaving the row stuck pending.
    """
    from app.api.v1.projects._common import acting_company_id
    from app.services.queue_service import enqueue_job
    from app.tasks.export_tasks import generate_order_inquiry_worklist_xlsx

    filters = payload.model_dump(exclude_none=True)
    # Security review fix round 2, item 1: the SAME UUID guard the GET list/summary/
    # matrix routes and the acknowledge route's own `filter` branch run - defense in
    # depth alongside the schema's own `pattern=UUID_PATTERN` fields, and BEFORE any
    # `user_downloads` row exists, so a malformed filter never reaches the worker.
    _validate_worklist_filter_uuids(filters)
    # The worker has no request-scoped company: snapshot the enqueuing request's own
    # single-company scope so the task can adopt it - the render then sees exactly the
    # rows this caller could see, not the worker's fail-closed UNSET default.
    # `acting_company_id` (review fix round 3, item 4 - reuse, not reimplement) raises
    # a clean 400 for an ambiguous/no scope BEFORE any `user_downloads` row exists,
    # same as the UUID guard above.
    company_id = acting_company_id(db)

    if DownloadService(db).has_in_flight(
        user_id=str(current_user["id"]), kind="order_inquiry_worklist_xlsx",
    ):
        raise AppException(
            status_code=409,
            message="An order inquiry export is already being prepared - check My "
                    "Downloads.",
        )

    filename = f"order-inquiries-{compact_ddmmyyyy(None)}.xlsx"
    download = DownloadService(db).create(
        user_id=str(current_user["id"]),
        kind="order_inquiry_worklist_xlsx",
        filename=filename,
    )
    try:
        enqueue_job(
            generate_order_inquiry_worklist_xlsx,
            str(download.id),
            filters,
            str(current_user["id"]),
            company_id=company_id,
            queue_name="imports",
            job_timeout=600,
        )
    except Exception as e:
        DownloadService(db).mark_failed(
            str(download.id), f"Could not queue order inquiry export: {e}"
        )
        raise AppException(
            status_code=503,
            message="Could not queue order inquiry export. Please try again.",
        )

    return DownloadResponse.model_validate(DownloadService(db).get(str(download.id)))


@router.post("/order-inquiries/{inquiry_id}/export", response_model=DownloadResponse)
def export_order_inquiry_header(
    inquiry_id: str,
    current_user: dict = Depends(_EXPORT),
    db: Session = Depends(get_db),
):
    """Lane B, AC-B1/AC-B3/AC-B4/AC-B7 (`PLAN-order-sheet-oi-reports-22sep.md`): the OI
    detail page's own Export Excel, through My Downloads, tied to this one header so
    its download history is reachable from the OI itself (AC-B5, `EntityDownloadsButton`
    for `("order_inquiry", inquiry_id)`).

    Same shape as `export_order_summary`: create the `user_downloads` row, enqueue the
    render, mark it failed and answer 503 on an enqueue failure. 404 for an unknown or
    another company's header, off the SAME loader the detail page itself reads.
    """
    from app.services.queue_service import enqueue_job
    from app.tasks.export_tasks import generate_order_inquiry_xlsx

    validate_uuid_path(inquiry_id, resource="Order inquiry")
    header = OrderInquiryHeaderService(db).get(inquiry_id)

    if DownloadService(db).has_in_flight(
        user_id=str(current_user["id"]), kind="order_inquiry_xlsx",
        source_entity_type="order_inquiry", source_entity_id=inquiry_id,
    ):
        raise AppException(
            status_code=409,
            message="An order inquiry export is already being prepared - check My "
                    "Downloads.",
        )

    download = DownloadService(db).create(
        user_id=str(current_user["id"]),
        kind="order_inquiry_xlsx",
        source_entity_type="order_inquiry",
        source_entity_id=inquiry_id,
        filename=f"{header.inquiry_no}.xlsx",
    )
    try:
        enqueue_job(
            generate_order_inquiry_xlsx,
            str(download.id),
            inquiry_id,
            str(current_user["id"]),
            queue_name="imports",
            job_timeout=600,
        )
    except Exception as e:
        DownloadService(db).mark_failed(
            str(download.id), f"Could not queue order inquiry export: {e}"
        )
        raise AppException(
            status_code=503,
            message="Could not queue order inquiry export. Please try again.",
        )

    return DownloadResponse.model_validate(DownloadService(db).get(str(download.id)))


@router.get("/order-inquiries/matrix", response_model=OrderInquiryMatrixResponse)
def order_inquiry_worklist_matrix(
    axis: MatrixAxis = Query(
        ..., description="The vertical grouping - product, sales order, customer or agent."
    ),
    by: MatrixGranularity = Query("week", description="The date bucket's own width."),
    query: Optional[str] = Query(None, max_length=_MAX_QUERY_LENGTH),
    delivery_month: Optional[str] = Query(None),
    raised_date: Optional[str] = Query(None),
    state: Optional[Literal["raised", "partly_linked", "actioned", "cancelled", "placed"]] = Query(None),
    project_id: Optional[str] = Query(None),
    project: Optional[str] = Query(None),
    supplier_id: Optional[str] = Query(None),
    raised_by: Optional[str] = Query(None),
    linked: Optional[Literal["po", "spo", "none"]] = Query(None),
    kind: Optional[Literal["spo", "po", "buy"]] = Query(None),
    ack: Optional[Literal["awaiting", "acknowledged", "changed", "rejected", "to_confirm"]] = Query(None),
    location: Optional[str] = Query(None, max_length=_MAX_FILTER_LENGTH),
    agent: Optional[str] = Query(None),
    so_month: Optional[str] = Query(None),
    po_number: Optional[str] = Query(None, max_length=_MAX_FILTER_LENGTH),
    spo_number: Optional[str] = Query(None, max_length=_MAX_FILTER_LENGTH),
    delivery_from: Optional[str] = Query(None),
    delivery_to: Optional[str] = Query(None),
    _user: dict = Depends(require_permission_with_api_key(VIEW)),
    db: Session = Depends(get_db),
):
    """The Schedule view's own read (S3): the SAME filters the list reads, one GROUP BY
    over `axis` by `by`, no page and no cap.

    Replaces the old client-side matrix, which asked the list once with `limit=1000` and
    grouped the rows in the browser - a delivery-filtered worklist has already exceeded
    that on prod (PLAN section 0). Same permission as the list: reading the schedule is
    reading the worklist a second way, not a second grant.
    """
    try:
        cells = OrderInquiryWorklistService(db).matrix(
            axis=axis,
            by=by,
            **_worklist_filters(
                query,
                delivery_month,
                raised_date,
                state,
                project_id,
                supplier_id,
                raised_by,
                linked,
                kind,
                ack,
                location,
                agent,
                so_month,
                po_number,
                spo_number,
                delivery_from,
                delivery_to,
                project=project,
            ),
        )
        return {"data": cells}
    except Exception as exc:
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.post("/order-inquiries/acknowledge", response_model=AcknowledgeResult)
async def acknowledge_order_inquiry_rows(
    payload: AcknowledgeRowsRequest,
    current_user: dict = Depends(require_permission(ACKNOWLEDGE)),
    db: Session = Depends(get_db),
):
    """Purchasing's own Confirm press (AC-H2, AC-CF-5 to AC-CF-8 `PLAN-oi-confirm-per-so.md`
    S1/S2) - one row, a batch by id, or every row a `filter` matches ("Select all N
    matching").

    One press does two things because they are one decision: the rows become purchasing's
    work, stamped with who and when, and the cascade runs for EXACTLY these rows, so the
    open documents that can cover them are linked at that moment - though most of them are
    linked already, since linking never waits for this press (AC-CF-4).

    `link_up_to` is how far out the linking half reaches (AC-LH1): every named row is taken
    on, and one due after that date is left Not linked and counted on `after_horizon`.
    Omitted, it is the reorder plan's own horizon; `link_horizon: "none"` is how a caller
    asks for no horizon at all (S1).

    `row_ids` and `filter` are mutually exclusive (AC-CF-8c, refused at the schema when
    both or neither is named). A `filter` press resolves through the SAME predicate the
    list route reads (`OrderInquiryWorklistService._base` via `acknowledge_scope`), then
    confirms only what it matched that is actually eligible (`awaiting`/`changed`, not
    cancelled) - everything else it matched is reported on `skipped`, never silently
    taken on and never silently dropped (AC-CF-8b)."""
    try:
        if payload.row_ids:
            for row_id in payload.row_ids:
                validate_uuid_path(row_id, resource="Order inquiry row")
            body = ProjectOrderInquiryService(db).acknowledge_rows(
                payload.row_ids,
                actor_user_id=current_user["id"],
                link_up_to=payload.link_up_to,
                link_horizon=payload.link_horizon,
            )
        else:
            filter_kwargs = (
                payload.filter.model_dump(exclude_none=True) if payload.filter else {}
            )
            _validate_worklist_filter_uuids(filter_kwargs)
            eligible_ids, skipped = OrderInquiryWorklistService(db).acknowledge_scope(
                **filter_kwargs
            )
            body = ProjectOrderInquiryService(db).acknowledge_eligible_rows(
                eligible_ids,
                actor_user_id=current_user["id"],
                link_up_to=payload.link_up_to,
                link_horizon=payload.link_horizon,
            )
            body["skipped"] = skipped
        db.commit()
        return body
    except Exception as exc:
        db.rollback()
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.post("/order-inquiries/unacknowledge", response_model=UnacknowledgeResult)
async def unacknowledge_order_inquiry_rows(
    payload: UnacknowledgeRowsRequest,
    current_user: dict = Depends(require_permission(ACKNOWLEDGE)),
    db: Session = Depends(get_db),
):
    """Unconfirm (N) (PLAN-oi-worklist-split-customer-project.md, Slice 3, owner 18 Sep
    2026) - the Actions menu's own reverse of Confirm, for a row taken on by mistake or a
    reconfirm CS has not actually made yet. Same `ACKNOWLEDGE` grant as Confirm itself:
    whoever can take a row on can also put it back.

    Reversible (a plain Confirm undoes it), so this refuses nothing the way Confirm's own
    guards do: a row already `awaiting`/`rejected`, cancelled, or outside this company's
    scope is counted on `skipped`, never a 404 or a 422 for the whole batch."""
    try:
        # The CANONICAL (lowercased) id, not the caller's own casing (security review
        # round 1): the service's own lookup is a plain string equality, so a
        # mixed-case id that still passes `validate_uuid_path`'s format check would
        # silently miss the row and count as `skipped` instead of being acted on.
        canonical_ids = [
            validate_uuid_path(row_id, resource="Order inquiry row")
            for row_id in payload.row_ids
        ]
        body = ProjectOrderInquiryService(db).unacknowledge_rows(
            canonical_ids, actor_user_id=current_user["id"]
        )
        db.commit()
        return body
    except Exception as exc:
        db.rollback()
        # Security review round 1: `str(exc)` on an exception the app never meant a
        # client to see (a DB error, a driver message) is the same reconnaissance leak
        # `app/main.py`'s own global handler exists to close - never pass it through.
        # An `AppException` the service raised on purpose (its own message is already
        # safe) still passes through unchanged.
        raise exc if hasattr(exc, "status_code") else handle_internal_error()


@router.post("/order-inquiries/reject", response_model=RejectRowsResult)
async def reject_order_inquiry_rows(
    payload: RejectRowsRequest,
    current_user: dict = Depends(require_permission(ACKNOWLEDGE)),
    db: Session = Depends(get_db),
):
    """Purchasing refuses a BATCH, with ONE reason (`PLAN-scm-oi-draft-links.md` 5.6).

    Reject became a bulk action when the row Actions column went (R8), and one reason per
    press is what a press means: the buyer refused these rows for this cause. Each row goes
    through the same `reject_row` a single refusal does - links unplaced, the row stamped,
    the sales-order line sent back to the board undecided carrying the reason - so the two
    doors can never come to mean different things.

    ALL OR NOTHING. Every row is checked before anything is written, and a batch holding
    one row that cannot be refused is refused whole: a press that half happened leaves the
    buyer to work out which half, from a screen that has already moved on.
    """
    try:
        for row_id in payload.row_ids:
            validate_uuid_path(row_id, resource="Order inquiry row")
        body = ProjectOrderInquiryService(db).reject_rows(
            payload.row_ids, reason=payload.reason, actor_user_id=current_user["id"]
        )
        db.commit()
        return body
    except Exception as exc:
        db.rollback()
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.post("/order-inquiries/{row_id}/reject", response_model=OrderInquiryRowOut)
async def reject_order_inquiry_row(
    row_id: str,
    payload: RejectRowRequest,
    current_user: dict = Depends(require_permission(ACKNOWLEDGE)),
    db: Session = Depends(get_db),
):
    """Purchasing refuses one row, with a reason (AC-H5/AC-H6).

    The row leaves netting and its sales-order LINE goes back to the board undecided
    carrying the refusal, so CS decides it again rather than waiting on a purchase nobody
    is making. Never one-click and never silent: the reason is what the board cell shows."""
    try:
        validate_uuid_path(row_id, resource="Order inquiry row")
        body = ProjectOrderInquiryService(db).reject_row(
            row_id, reason=payload.reason, actor_user_id=current_user["id"]
        )
        db.commit()
        return body
    except Exception as exc:
        db.rollback()
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.post("/order-inquiries/link-now", response_model=AutoPlaceResult)
async def link_acknowledged_order_inquiry_rows(
    payload: LinkNowRequest,
    current_user: dict = Depends(require_permission(ACKNOWLEDGE)),
    db: Session = Depends(get_db),
):
    """Run the cascade over ACKNOWLEDGED rows now (AC-H13) - what the buyer presses after
    uploading a purchase order or SPO book from this page. `product_ids` narrows it to
    what the upload touched; omitted, it is every acknowledged row with something still
    unlinked. Idempotent: a second call links nothing more."""
    try:
        for product_id in payload.product_ids or []:
            validate_uuid_path(product_id, resource="Product")
        body = ProjectOrderInquiryService(db).link_now(
            payload.product_ids,
            actor_user_id=current_user["id"],
            link_up_to=payload.link_up_to,
            link_horizon=payload.link_horizon,
        )
        db.commit()
        return body
    except Exception as exc:
        db.rollback()
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.get("/order-inquiries/upload-jobs/{job_id}", response_model=UploadJobScope)
def get_order_inquiry_upload_job(
    job_id: str,
    _user: dict = Depends(require_permission(ACKNOWLEDGE)),
    db: Session = Depends(get_db),
):
    """What the book this page uploaded has written, once the worker is done with it.

    The two next steps AC-H13 offers need the same fact and neither can have it at queue
    time, because the write happens on the worker: the products to narrow "Link now" to,
    and the documents to filter the purchase-order list by. Both are read off the job's
    own result, which is the importer's own answer rather than a second derivation of it.

    Gated on the acknowledge grant, like every other action on this page: asking what an
    upload wrote is how the buyer decides what to link, and CS does neither.
    """
    from app.services.scm.import_job_scope import scope_of_job

    scope = scope_of_job(db, job_id)
    if scope is None:
        raise AppException(
            status_code=404,
            message="That upload could not be found.",
            code="import_job_not_found",
        )
    return scope


@router.get("/order-inquiries/po/{po_id}", response_model=OrderInquiryPoDetail)
def get_order_inquiry_po_detail(
    po_id: str,
    _user: dict = Depends(require_permission_with_api_key(VIEW)),
    db: Session = Depends(get_db),
):
    """The "PO no" cell's popup: that purchase order's header and every one of its lines.

    Gated the same as the worklist's own read (`projects.projects.view`), never
    `scm.dashboard.view` - purchasing works this worklist off project permissions, the
    same gotcha "Place on PO" already worked around, so this reads
    `purchase_orders`/`purchase_order_lines` off the PROJECTS router rather than calling
    the SCM purchase-orders route.
    """
    try:
        validate_uuid_path(po_id, resource="Purchase order")
        return OrderInquiryWorklistService(db).get_po_detail(po_id)
    except Exception as exc:
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.get("/order-inquiries/spo/{spo_number:path}", response_model=OrderInquirySpoDetail)
def get_order_inquiry_spo_detail(
    spo_number: str,
    _user: dict = Depends(require_permission_with_api_key(VIEW)),
    db: Session = Depends(get_db),
):
    """The "SPO no" cell's lightbox: that shipping order's allocation lines.

    `{spo_number:path}` rather than a plain segment, because a shipping order is numbered
    `SPO-2026/08-0015` - the number itself contains a slash, and a percent-encoded one is
    decoded before routing, so a single-segment parameter would never match the document
    the page is asking about.

    Gated the same as the purchase-order lightbox next door (`projects.projects.view`),
    for the same reason: purchasing works this worklist off project permissions.
    """
    try:
        return OrderInquiryWorklistService(db).get_spo_detail(spo_number)
    except Exception as exc:
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.get(
    "/projects/{project_id}/order-inquiry-rows",
    response_model=ListResponse[OrderInquiryRowOut],
)
async def list_order_inquiry_rows(
    project_id: str,
    query: Optional[str] = Query(None, description="Item code, SPO ref, location or SO number"),
    verb: Optional[List[str]] = Query(None),
    state: Optional[List[str]] = Query(None),
    sales_order_id: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=MAX_PAGE_LIMIT),
    sort: str = Query("delivery_date"),
    dir: str = Query("asc"),
    _user: dict = Depends(require_permission_with_api_key(VIEW)),
    db: Session = Depends(get_db),
):
    """Every instruction raised on one project, in delivery-date order by default."""
    try:
        validate_uuid_path(project_id, resource="Project")
        projects.get_project_or_404(db, project_id)
        service = ProjectOrderInquiryService(db)
        rows, total = service.list_rows(
            project_id,
            query=query,
            verb=verb,
            state=state,
            pso_id=sales_order_id,
            page=page,
            limit=limit,
            sort=sort,
            direction=dir,
        )
        return {
            "data": rows,
            "pagination": {"total": total, "page": page, "limit": limit},
            "empty": total == 0,
        }
    except Exception as exc:
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.get(
    "/projects/{project_id}/order-inquiry-summary", response_model=OrderInquirySummary
)
async def order_inquiry_summary(
    project_id: str,
    _user: dict = Depends(require_permission_with_api_key(VIEW)),
    db: Session = Depends(get_db),
):
    try:
        validate_uuid_path(project_id, resource="Project")
        projects.get_project_or_404(db, project_id)
        return ProjectOrderInquiryService(db).summary(project_id)
    except Exception as exc:
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.get("/projects/{project_id}/order-inquiry-export")
async def export_order_inquiry(
    project_id: str,
    query: Optional[str] = Query(None),
    verb: Optional[List[str]] = Query(None),
    state: Optional[List[str]] = Query(None),
    sales_order_id: Optional[str] = Query(None),
    _user: dict = Depends(require_permission(VIEW)),
    db: Session = Depends(get_db),
):
    """The same rows, as the spreadsheet purchasing already reads (AC-I5).

    Generated per request, exactly as the AutoCount import file is: a stored copy goes
    stale the moment an amendment publishes, and a stale instruction is the thing being
    emailed around today.
    """
    try:
        validate_uuid_path(project_id, resource="Project")
        projects.get_project_or_404(db, project_id)
        filename, body = ProjectOrderInquiryService(db).export_xlsx(
            project_id, query=query, verb=verb, state=state, pso_id=sales_order_id
        )
        return Response(
            content=body,
            media_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
            headers={"Content-Disposition": content_disposition(filename)},
        )
    except Exception as exc:
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.get("/sales-orders/{pso_id}/order-inquiry", response_model=OrderInquiryDetail)
async def get_sales_order_inquiry(
    pso_id: str,
    _user: dict = Depends(require_permission_with_api_key(VIEW)),
    db: Session = Depends(get_db),
):
    """What purchasing was told when this sales order published."""
    try:
        validate_uuid_path(pso_id, resource="Sales order")
        body = ProjectOrderInquiryService(db).get_for_sales_order(pso_id)
        if body is None:
            raise AppException(
                status_code=404,
                message=(
                    "No order inquiry has been raised for this sales order. One is "
                    "derived the moment it publishes."
                ),
                code="order_inquiry_not_raised",
            )
        return body
    except Exception as exc:
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


def _serialize_reserve_request(
    db: Session, request: OrderInquiryReserveRequest, *, notified_name: Optional[str] = None
) -> dict:
    """`OrderInquiryReserveRequestOut`'s wire shape - the request plus every one of its
    rows, human-readable (no UUID in the UI): `item_code` off the order-inquiry row,
    `location` off the chosen warehouse's own code."""
    from decimal import Decimal

    from app.models.project_so import OrderInquiryRow
    from app.models.user import User

    def _qty(value) -> Optional[str]:
        if value is None:
            return None
        return format(Decimal(str(value)).normalize(), "f")

    def _name(user_id: Optional[str]) -> Optional[str]:
        if not user_id:
            return None
        user = db.query(User).filter(User.id == user_id).first()
        return getattr(user, "name", None) or getattr(user, "email", None)

    rows = (
        db.query(OrderInquiryReserveRequestRow)
        .filter(OrderInquiryReserveRequestRow.request_id == request.id)
        .order_by(OrderInquiryReserveRequestRow.id.asc())
        .all()
    )
    row_by_id = {}
    warehouse_by_id = {}
    if rows:
        row_by_id = {
            row.id: row
            for row in db.query(OrderInquiryRow)
            .filter(OrderInquiryRow.id.in_([rr.row_id for rr in rows]))
            .all()
        }
        warehouse_ids = {rr.warehouse_id for rr in rows if rr.warehouse_id}
        if warehouse_ids:
            warehouse_by_id = {
                warehouse.id: warehouse.warehouse_code
                for warehouse in db.query(Warehouse).filter(Warehouse.id.in_(warehouse_ids)).all()
            }
    return {
        "id": request.id,
        "order_inquiry_id": request.order_inquiry_id,
        "ordinal": request.ordinal,
        "state": request.state,
        "requested_by": request.requested_by,
        "requested_by_name": _name(request.requested_by),
        "requested_at": as_utc(request.requested_at),
        "note": request.note,
        "reserved_by_name": _name(request.reserved_by),
        "reserved_at": as_utc(request.reserved_at),
        "cancelled_at": as_utc(request.cancelled_at),
        "rows": [
            {
                "id": rr.id,
                "row_id": rr.row_id,
                "item_code": getattr(row_by_id.get(rr.row_id), "item_code", None),
                "qty_requested": _qty(rr.qty_requested),
                "warehouse_id": rr.warehouse_id,
                "location": warehouse_by_id.get(rr.warehouse_id),
                "qty_reserved": _qty(rr.qty_reserved),
                "reason": rr.reason,
            }
            for rr in rows
        ],
        "notified_name": notified_name,
    }


@router.post(
    "/order-inquiries/{inquiry_id}/reserve-requests",
    response_model=OrderInquiryReserveRequestOut,
    status_code=201,
)
async def create_order_inquiry_reserve_request(
    inquiry_id: str,
    payload: CreateReserveRequestIn,
    current_user: dict = Depends(require_permission(ACKNOWLEDGE)),
    db: Session = Depends(get_db),
):
    """Purchasing asks CS to reserve stock for one or more rows of this inquiry
    (`PLAN-oi-request-cs-reserve.md` 3.2, AC-RS-1 to AC-RS-5). Same gate as every other
    purchasing action on this page - `ACKNOWLEDGE`, not the CS-only `RESERVE` grant
    below, which is Eling's own to confirm what was actually reserved."""
    try:
        validate_uuid_path(inquiry_id, resource="Order inquiry")
        request = OrderInquiryReserveService(db).create_request(
            inquiry_id=inquiry_id,
            rows=[row.model_dump() for row in payload.rows],
            note=payload.note,
            actor_user_id=current_user["id"],
            park=payload.park,
        )
        db.commit()
        inquiry = (
            db.query(OrderInquiry).filter(OrderInquiry.id == request.order_inquiry_id).first()
        )
        notified_name = OrderInquiryReserveService(db).notified_name(
            trigger_type="order_inquiry_reserve_requested",
            actor_user_id=current_user["id"],
            raiser_user_id=inquiry.raised_by if inquiry is not None else None,
        )
        return _serialize_reserve_request(db, request, notified_name=notified_name)
    except Exception as exc:
        db.rollback()
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.post(
    "/order-inquiries/reserve-requests/{request_id}/cancel",
    response_model=OrderInquiryReserveRequestOut,
)
async def cancel_order_inquiry_reserve_request(
    request_id: str,
    current_user: dict = Depends(require_any_permission([ACKNOWLEDGE, RESERVE])),
    db: Session = Depends(get_db),
):
    """The requester's own undo while nothing has been reserved yet (3.2, AC-RS-19) -
    also reachable through the deferred action `order_inquiry_reserve_request.cancel`
    (`record_actions.py`) for the standard reversible countdown. No email either way.

    SF-1 (review round): the dependency above only proves the actor holds ONE of the
    two purchasing-side grants - it says nothing about whose request this is. The
    actor's OWN hold on `RESERVE` is resolved here and handed to the service, which is
    where the real "requester or CS" ownership check lives (`cancel_request`)."""
    try:
        validate_uuid_path(request_id, resource="Reserve request")
        actor_can_reserve = UserPermissionService(db).check_user_has_permission(
            current_user["id"], RESERVE
        )
        request = OrderInquiryReserveService(db).cancel_request(
            request_id=request_id,
            actor_user_id=current_user["id"],
            actor_can_reserve=actor_can_reserve,
        )
        db.commit()
        return _serialize_reserve_request(db, request)
    except Exception as exc:
        db.rollback()
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.post(
    "/order-inquiries/{inquiry_id}/reserve-commit",
    response_model=List[OrderInquiryReserveRequestOut],
)
async def commit_order_inquiry_reserve(
    inquiry_id: str,
    payload: CommitReserveRequestIn,
    current_user: dict = Depends(require_permission(RESERVE)),
    db: Session = Depends(get_db),
):
    """Eling's own Confirm, staged on the Lines grid and committed ONE CLICK at a time
    (`PLAN-oi-request-cs-reserve.md` 6e.1, re-keyed by 6e.4). The body names order-
    inquiry rows only; the service resolves each to its open (`reserves`) or latest
    answered (`amendments`) request row inside THIS inquiry, so a line of a finished
    request can be amended while another request is open. One transaction, one
    `order_inquiry_reserved` dispatch per request touched. Returns the touched
    requests. `projects.order_inquiries.reserve` alone (R1)."""
    try:
        validate_uuid_path(inquiry_id, resource="Order inquiry")
        requests = OrderInquiryReserveService(db).commit_request(
            inquiry_id=inquiry_id,
            reserves=[row.model_dump() for row in payload.reserves],
            amendments=[row.model_dump() for row in payload.amendments],
            actor_user_id=current_user["id"],
            park_notice=payload.park_notice,
        )
        db.commit()
        return [_serialize_reserve_request(db, request) for request in requests]
    except Exception as exc:
        db.rollback()
        if hasattr(exc, "status_code"):
            raise exc
        # Security re-review: the detail goes to the log, never into the response.
        logger.exception("Reserve commit failed for order inquiry %s", inquiry_id)
        raise handle_internal_error()


@router.get(
    "/order-inquiries/reserve-status/summary", response_model=ReserveStatusSummaryOut
)
def order_inquiry_reserve_status_summary(
    current_user: dict = Depends(require_any_permission([VIEW, ACKNOWLEDGE, RESERVE])),
    db: Session = Depends(get_db),
):
    """OI-STAGED-NOTIFY (AC-SN-31): OIs per reserve status (everyone's) and the caller's
    own parked requests / notices."""
    try:
        return OrderInquiryReserveService(db).reserve_status_summary(
            actor_user_id=current_user["id"]
        )
    except Exception as exc:
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.post(
    "/order-inquiries/handover-resend/preview", response_model=HandoverResendPreviewOut
)
def preview_handover_resend(
    payload: HandoverResendIn,
    current_user: dict = Depends(require_permission(ACTION)),
    db: Session = Depends(get_db),
):
    """OI-RESEND-PURCHASING: the handover email "Send to purchasing" would send, one per
    order inquiry, rendered by the handover automation's own template. Writes nothing."""
    from app.services.oi_handover_resend import OrderInquiryHandoverResend

    try:
        return OrderInquiryHandoverResend(db).preview(
            payload.inquiry_ids, actor_user_id=current_user["id"], row_ids=payload.row_ids
        )
    except Exception as exc:
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.post("/order-inquiries/handover-resend/send", response_model=HandoverResendSendOut)
def send_handover_resend(
    payload: HandoverResendIn,
    current_user: dict = Depends(require_permission(ACTION)),
    db: Session = Depends(get_db),
):
    """OI-RESEND-PURCHASING: send each order inquiry's open rows to purchasing again, even
    while the automatic handover is switched off. Each email is its own recorded run."""
    from app.services.oi_handover_resend import OrderInquiryHandoverResend

    try:
        return OrderInquiryHandoverResend(db).send(
            payload.inquiry_ids, actor_user_id=current_user["id"], row_ids=payload.row_ids
        )
    except Exception as exc:
        db.rollback()
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.post(
    "/order-inquiries/reserve-requests/parked/preview", response_model=ParkedPreviewOut
)
def preview_parked_reserve_requests(
    current_user: dict = Depends(require_permission(ACKNOWLEDGE)),
    db: Session = Depends(get_db),
):
    """The emails "send all parked requests" would send, one per recipient, rendered by
    the automation's own template (AC-SN-04)."""
    try:
        return OrderInquiryReserveService(db).parked_requests_preview(
            actor_user_id=current_user["id"]
        )
    except Exception as exc:
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.post("/order-inquiries/reserve-requests/parked/send", response_model=ParkedSendOut)
def send_parked_reserve_requests(
    current_user: dict = Depends(require_permission(ACKNOWLEDGE)),
    db: Session = Depends(get_db),
):
    """Send the caller's parked requests to CS (AC-SN-06). The dispatch queued by the
    service fires only when this commit lands."""
    try:
        result = OrderInquiryReserveService(db).send_parked_requests(
            actor_user_id=current_user["id"]
        )
        db.commit()
        return result
    except Exception as exc:
        db.rollback()
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.post(
    "/order-inquiries/reserve-notices/parked/preview", response_model=ParkedPreviewOut
)
def preview_parked_reserve_notices(
    current_user: dict = Depends(require_permission(RESERVE)),
    db: Session = Depends(get_db),
):
    """The emails "send all parked reserves" would send to purchasing (AC-SN-22)."""
    try:
        return OrderInquiryReserveService(db).parked_notices_preview(
            actor_user_id=current_user["id"]
        )
    except Exception as exc:
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.post("/order-inquiries/reserve-notices/parked/send", response_model=ParkedSendOut)
def send_parked_reserve_notices(
    current_user: dict = Depends(require_permission(RESERVE)),
    db: Session = Depends(get_db),
):
    """Send the caller's parked reserve notices to purchasing (AC-SN-22)."""
    try:
        result = OrderInquiryReserveService(db).send_parked_notices(
            actor_user_id=current_user["id"]
        )
        db.commit()
        return result
    except Exception as exc:
        db.rollback()
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.get(
    "/order-inquiries/{inquiry_id}/reserve-notices/recipients",
    response_model=ReserveNoticeRecipientsOut,
)
def order_inquiry_reserve_notice_recipients(
    inquiry_id: str,
    row_ids: Optional[str] = Query(
        None, max_length=4000, description="Comma-separated staged OI row ids."
    ),
    current_user: dict = Depends(require_permission(RESERVE)),
    db: Session = Depends(get_db),
):
    """Who the reserve confirmation's notice would go to (AC-SN-27)."""
    try:
        validate_uuid_path(inquiry_id, resource="Order inquiry")
        ids = [part.strip() for part in (row_ids or "").split(",") if part.strip()]
        for row_id in ids:
            validate_uuid_path(row_id, resource="Order inquiry")
        return OrderInquiryReserveService(db).notice_recipients(
            inquiry_id=inquiry_id, actor_user_id=current_user["id"], row_ids=ids or None
        )
    except Exception as exc:
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.get(
    "/order-inquiries/reserve-requests/{request_id}/rows/{row_id}/history",
    response_model=List[ReserveHistoryEntryOut],
)
def order_inquiry_reserve_request_row_history(
    request_id: str,
    row_id: str,
    _user: dict = Depends(require_any_permission([VIEW, ACKNOWLEDGE, RESERVE])),
    db: Session = Depends(get_db),
):
    """The dialog's own History tab, newest first (`PLAN-oi-request-cs-reserve.md`
    section 6c, F3). Same read gate as the request list beside it: `VIEW`/
    `ACKNOWLEDGE`/`RESERVE` are the three ways to already be allowed to see this
    inquiry's own Lines tab at all."""
    try:
        validate_uuid_path(request_id, resource="Reserve request")
        validate_uuid_path(row_id, resource="Order inquiry row")
        return OrderInquiryReserveService(db).history_for_row(
            request_id=request_id, row_id=row_id
        )
    except Exception as exc:
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.get(
    "/sales-order-lines/{core_line_id}/decision-trail",
    response_model=DecisionTrailResponse,
)
def sales_order_line_decision_trail(
    core_line_id: str,
    _user: dict = Depends(require_any_permission([VIEW, ACKNOWLEDGE, RESERVE])),
    db: Session = Depends(get_db),
):
    """The History icon's own read (`PLAN-oi-decision-trail-ui.md`, round 2, AC-DT-10):
    who confirmed, saved or raised something against this CORE sales-order line - the id
    an OI row and a fulfilment-board line both point at (`OrderInquiryRow.so_line_id` ->
    the project mirror -> `core_sales_order_line_id`; `BoardContribution.line_id` names it
    directly, no lookup needed). Same read gate as the reserve history route above:
    `VIEW`/`ACKNOWLEDGE`/`RESERVE` are the three ways to already be allowed to see this
    line's own inquiry or board."""
    try:
        validate_uuid_path(core_line_id, resource="Sales order line")
        entries = DecisionTrailService(db).for_core_line(core_line_id)
        return DecisionTrailResponse(entries=entries)
    except Exception as exc:
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.get(
    "/order-inquiries/{inquiry_id}/reserve-requests",
    response_model=List[OrderInquiryReserveRequestOut],
)
async def list_order_inquiry_reserve_requests(
    inquiry_id: str,
    current_user: dict = Depends(require_any_permission([VIEW, ACKNOWLEDGE, RESERVE])),
    db: Session = Depends(get_db),
):
    """Every reserve request this header has ever raised, newest first - the Lines tab's
    own `ReserveRequestsCard` (3.7/3.8): the open one in act mode for a viewer holding
    `RESERVE`, the rest collapsed as history.

    SF-4 (review round): gating on `ACKNOWLEDGE` alone locked a reserve-only CS head
    (Eling, who never raises a request) out of reading her own worklist's card - `VIEW`/
    `ACKNOWLEDGE`/`RESERVE` are the three ways to already be allowed to see this
    inquiry's own Lines tab at all."""
    try:
        validate_uuid_path(inquiry_id, resource="Order inquiry")
        requests = (
            db.query(OrderInquiryReserveRequest)
            .filter(OrderInquiryReserveRequest.order_inquiry_id == inquiry_id)
            .order_by(OrderInquiryReserveRequest.ordinal.desc())
            .all()
        )
        return [_serialize_reserve_request(db, request) for request in requests]
    except Exception as exc:
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.post("/order-inquiry-rows/mark", response_model=List[OrderInquiryRowOut])
async def mark_order_inquiry_rows(
    payload: MarkInquiryRowsRequest,
    current_user: dict = Depends(require_permission(ACTION)),
    db: Session = Depends(get_db),
):
    """Purchasing records what happened to one row or to a selection of them (AC-I7)."""
    try:
        for row_id in payload.row_ids:
            validate_uuid_path(row_id, resource="Order inquiry row")
        body = ProjectOrderInquiryService(db).mark_rows(
            payload.row_ids, state=payload.state, actor_user_id=current_user["id"]
        )
        db.commit()
        return body
    except Exception as exc:
        db.rollback()
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.get(
    "/order-inquiry-rows/{row_id}/po-candidates",
    response_model=OrderInquiryPoCandidatesResponse,
)
async def order_inquiry_po_candidates(
    row_id: str,
    _user: dict = Depends(require_permission_with_api_key(ACTION)),
    db: Session = Depends(get_db),
):
    """Open PO lines this row could be tagged to (section G), soonest first, plus the
    dialog's own header line (S8, AC-CF-24): how much of the row is still unlinked."""
    try:
        validate_uuid_path(row_id, resource="Order inquiry row")
        service = ProjectOrderInquiryService(db)
        candidates = service.po_candidates_for_row(row_id)
        still_to_link = service.still_to_link_for_row(row_id)
        linkable_qty = service.linkable_qty_for_row(row_id)
        return {
            "candidates": candidates,
            "still_to_link": still_to_link,
            "linkable_qty": linkable_qty,
        }
    except Exception as exc:
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.post(
    "/order-inquiry-rows/{row_id}/place-on-po", response_model=OrderInquiryRowOut
)
async def place_order_inquiry_row_on_po(
    row_id: str,
    payload: PlaceOnPoRequest,
    current_user: dict = Depends(require_permission(ACTION)),
    db: Session = Depends(get_db),
):
    """Link a row to one or more document lines (PLAN-scm-cs-planning-uat.md section 3.I).

    The PATH is deliberately unchanged - the plan renames the verb, not the URLs - so this
    is "Link PO" / "Link SPO" on every screen. `po_line_id` links one purchase order line
    for the row's whole unlinked remainder; `allocations` links across several, each naming
    a `po_line_id` OR an `spo_allocation_id` (either book answers any linkable verb since
    R5, 27 Aug). The row is NEVER split (AC-I6): it keeps its full quantity and gains one
    link per allocation, so the response is that same row with its links on it."""
    try:
        validate_uuid_path(row_id, resource="Order inquiry row")
        service = ProjectOrderInquiryService(db)
        if payload.allocations:
            for allocation in payload.allocations:
                if allocation.po_line_id:
                    validate_uuid_path(
                        allocation.po_line_id, resource="Purchase order line"
                    )
                if allocation.spo_allocation_id:
                    validate_uuid_path(
                        allocation.spo_allocation_id, resource="SPO allocation"
                    )
            written = service.place_on_po_allocations(
                row_id,
                [
                    {
                        "po_line_id": allocation.po_line_id,
                        "spo_allocation_id": allocation.spo_allocation_id,
                        "qty": allocation.qty,
                    }
                    for allocation in payload.allocations
                ],
                actor_user_id=current_user["id"],
                # S8 (AC-CF-25): the dialog's `allocations` submission is the row's whole
                # link set - SET semantics, not an add-on-top. The single `po_line_id`
                # form below keeps its old ADD meaning.
                full_set=True,
                # S8 review round (17 Sep): the candidate ids the caller actually
                # rendered - scopes the retire step to what it saw. `None` when the
                # caller omits it, unchanged.
                offered_line_ids=payload.offered_line_ids,
            )
            body = written[0]
        else:
            validate_uuid_path(payload.po_line_id, resource="Purchase order line")
            body = service.place_on_po(
                row_id, payload.po_line_id, actor_user_id=current_user["id"]
            )
        db.commit()
        return body
    except Exception as exc:
        db.rollback()
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.post("/order-inquiries/auto-place", response_model=AutoPlaceResult)
async def auto_place_order_inquiries(
    payload: AutoPlaceRequest,
    current_user: dict = Depends(require_permission(ACTION)),
    db: Session = Depends(get_db),
):
    """Run the cascade now - the toolbar's "Auto link all" (AC-D9,
    `PLAN-scm-oi-draft-links.md` R2/R6): every raised or partly linked ORDER / RESERVE &
    ORDER / ORDER BACK row of the named products - or of every product carrying one, when
    `product_ids` is omitted - linked to its own open document lines in the walk's order
    (cited document, then SPO before PO on an order back, then location tier, then the
    purchase order's issue date, then the line's expected date). Idempotent: a second call
    links nothing more.

    `redeal_drafts=True, include_awaiting=True`: this is one of the DRAFT re-deal doors
    (section 5.4) - it reaches rows nobody has confirmed yet and may move a draft off a
    document a nearer one has since beaten, never a confirmed row's link (R2).

    `filter.inquiry_id` (S3, `PLAN-oi-header-list-detail.md`) is the OI detail page's
    own gear > Auto link: scopes the whole cascade to that header's rows, on top of
    whichever of `product_ids` / `row_ids` is also given.

    R18 (`PLAN-oi-links-autocount-truth-24sep.md` 3.6): this is also the worklist's and
    the OI detail's own "Link selected" - `row_ids` naming exactly the ticked rows, and
    nothing else. There is no separate route for it: the book step above already writes
    only what AutoCount names, in AutoCount's own name, and the cascade below only ever
    suggests, so "Link selected" is this same call, scoped to the ticked rows - it can
    never turn a suggestion into a link on its own."""
    try:
        for product_id in payload.product_ids or []:
            validate_uuid_path(product_id, resource="Product")
        for row_id in payload.row_ids or []:
            validate_uuid_path(row_id, resource="Order inquiry row")
        inquiry_id = payload.filter.inquiry_id if payload.filter else None
        if inquiry_id:
            validate_uuid_path(inquiry_id, resource="Order inquiry")
        body = ProjectOrderInquiryService(db).auto_place_for_products(
            payload.product_ids,
            actor_user_id=current_user["id"],
            trigger="worklist",
            row_ids=payload.row_ids,
            inquiry_id=inquiry_id,
            link_up_to=payload.link_up_to,
            link_horizon=payload.link_horizon,
            redeal_drafts=True,
            include_awaiting=True,
        )
        db.commit()
        return body
    except Exception as exc:
        db.rollback()
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.post(
    "/order-inquiry-rows/{row_id}/unplace", response_model=OrderInquiryRowOut
)
async def unplace_order_inquiry_row(
    row_id: str,
    payload: UnlinkRequest = UnlinkRequest(),
    current_user: dict = Depends(require_permission(ACTION)),
    db: Session = Depends(get_db),
):
    """Unlink. With a `link_id` that ONE link goes and the row keeps its others; without
    one every link on the row goes. Either way the quantity that comes back counts as
    demand again, and the row's state is re-derived from what is left."""
    try:
        validate_uuid_path(row_id, resource="Order inquiry row")
        if payload.link_id:
            validate_uuid_path(payload.link_id, resource="Order inquiry link")
        body = ProjectOrderInquiryService(db).unplace(
            row_id, actor_user_id=current_user["id"], link_id=payload.link_id
        )
        db.commit()
        return body
    except Exception as exc:
        db.rollback()
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.get(
    "/order-inquiries/unplace-all-preview", response_model=UnplaceAllPreview
)
def order_inquiry_unplace_all_preview(
    query: Optional[str] = Query(None),
    delivery_month: Optional[str] = Query(None),
    raised_date: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    project: Optional[str] = Query(None),
    supplier_id: Optional[str] = Query(None),
    raised_by: Optional[str] = Query(None),
    _user: dict = Depends(require_permission_with_api_key(ACTION)),
    db: Session = Depends(get_db),
):
    """The confirm dialog's own numbers before "Unplace all" runs anything (the captain,
    21 Aug): the count of placed rows in the CURRENT worklist scope - the SAME filters
    `GET /order-inquiries` reads, `state` always forced to placed - and the product code
    when every one of them resolves to the same product. Gated on the write permission
    (`ACTION`), not the read one: this is a preview of a write a person is about to make,
    not a browse.

    "The same filters" means the ones a person NARROWED the worklist with. `state`,
    `linked` and `kind` are deliberately not among them: all three describe where a row's
    quantity already sits, and this action is always about every placed row in the scope -
    a pressed Buy card must not quietly shrink what "Unplace all" is about to unplace."""
    try:
        filters = _worklist_filters(
            query,
            delivery_month,
            raised_date,
            None,
            project_id,
            supplier_id,
            raised_by,
            project=project,
        )
        filters.pop("state", None)
        return OrderInquiryWorklistService(db).unplace_all_preview(**filters)
    except Exception as exc:
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.post("/order-inquiries/unplace-all", response_model=UnplaceAllResult)
async def unplace_order_inquiry_rows_in_scope(
    payload: UnplaceAllRequest,
    current_user: dict = Depends(require_permission(ACTION)),
    db: Session = Depends(get_db),
):
    """"Unplace all" for the CURRENT worklist scope (the captain, 20-21 Aug): every
    PLACED row matching the SAME filters `GET /order-inquiries` reads - one product when
    the filters happen to narrow to it, every placed row in the company when they name
    nothing - reverts to raised in one call, so Auto-place can re-deal them
    earliest-first. Same scope as the preview above, and the same three exclusions:
    `state`, `linked` and `kind` never narrow it."""
    try:
        if payload.project_id:
            validate_uuid_path(payload.project_id, resource="Project")
        if payload.supplier_id:
            validate_uuid_path(payload.supplier_id, resource="Supplier")
        unplaced = OrderInquiryWorklistService(db).unplace_all(
            actor_user_id=current_user["id"],
            query=payload.query,
            delivery_month=payload.delivery_month,
            raised_date=payload.raised_date,
            project_id=payload.project_id,
            project=payload.project,
            supplier_id=payload.supplier_id,
            raised_by=payload.raised_by,
        )
        db.commit()
        return {"unplaced": unplaced}
    except Exception as exc:
        db.rollback()
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


# =============================================================================
# The order inquiry HEADER list, detail and related documents (S2/S3,
# `PLAN-oi-header-list-detail.md`). One row per OI, its own routes under
# `/order-inquiry-headers` rather than under the crowded `/order-inquiries/*` family
# (`/summary`, `/export`, `/matrix`, `/po/{id}`, `/spo/{n}`, `/upload-jobs/{id}`) -
# a bare `/order-inquiries/{id}` would shadow one of those (LESSONS: SLA route
# shadowing).
# =============================================================================

HeaderListSort = Literal[
    "raised_at",
    "inquiry_no",
    "so_number",
    "raised_by",
    "lines_total",
    "qty_total",
    "customer",
    "project",
    "agent",
    "so_date",
    "status",
]
_MAX_HEADER_QUERY_LENGTH = WORKLIST_QUERY_MAX_LENGTH


@router.get(
    "/order-inquiry-headers", response_model=ListResponse[OrderInquiryHeaderOut]
)
def list_order_inquiry_headers(
    state: Literal["outstanding", "completed", "all"] = Query(
        "outstanding",
        description=(
            "Outstanding = a non-cancelled row still awaiting/changed; Completed = "
            "every non-cancelled row confirmed (or none at all); All = both."
        ),
    ),
    query: Optional[str] = Query(
        None,
        max_length=_MAX_HEADER_QUERY_LENGTH,
        description=(
            "OI no, legacy OI no, S/O no, customer, project, agent, or the product "
            "code / location of any of the header's own non-cancelled lines."
        ),
    ),
    raised_by: Optional[str] = Query(
        None, max_length=200, description="By `users.id`, exact."
    ),
    agent: Optional[str] = Query(
        None, max_length=200, description="By the agent's own name, exact."
    ),
    # B2 (reviewer): free TEXT on the Project column's own value (`_PROJECT_TITLE`),
    # never `project_id` - 0 of 738 headers on the prod copy carry a
    # `ProjectSalesOrder.project_id` (an adopted AutoCount order has no registered
    # `Project` row), so a uuid-pattern filter never matched anything. Same shape
    # `order_inquiry_worklist_service.py`'s own `project` filter already uses.
    project: Optional[str] = Query(None, max_length=200, description="Exact match on the Project column's text."),
    reserve: Optional[Literal["parked", "needs_reserve", "notice_parked", "reserved"]] = Query(
        None, description="OIs with a reserve request row in this status (AC-SN-30)."
    ),
    sort: Optional[HeaderListSort] = Query(None, description="Defaults to raised_at."),
    direction: Optional[Literal["asc", "desc"]] = Query("asc", alias="dir"),
    page: int = Query(1, ge=1),
    limit: int = Query(25, ge=1, le=MAX_PAGE_LIMIT),
    _user: dict = Depends(require_permission_with_api_key(VIEW)),
    db: Session = Depends(get_db),
):
    """One row per order inquiry - Documents view, default (AC-LS-01..07)."""
    try:
        result = OrderInquiryHeaderService(db).list(
            state=state,
            query=query,
            raised_by=raised_by,
            agent=agent,
            project=project,
            reserve=reserve,
            sort=sort,
            direction=direction,
            page=page,
            limit=limit,
        )
        return {
            "data": result.items,
            "pagination": {"total": result.total, "page": result.page, "limit": result.limit},
        }
    except Exception as exc:
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.get(
    "/order-inquiry-headers/{inquiry_id}", response_model=OrderInquiryHeaderDetailOut
)
def get_order_inquiry_header(
    inquiry_id: str,
    _user: dict = Depends(require_permission_with_api_key(VIEW)),
    db: Session = Depends(get_db),
):
    """The detail page's own header card + Order/Customer blocks + raise history
    (AC-DT-01). 404 for an unknown id or another company's header - company scoping is
    the session's own `do_orm_execute` listener, not a filter written here."""
    try:
        validate_uuid_path(inquiry_id, resource="Order inquiry")
        return OrderInquiryHeaderService(db).get(inquiry_id)
    except Exception as exc:
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.get(
    "/order-inquiry-headers/{inquiry_id}/related-documents",
    response_model=OrderInquiryRelatedDocumentsOut,
)
def get_order_inquiry_related_documents(
    inquiry_id: str,
    _user: dict = Depends(require_permission_with_api_key(VIEW)),
    db: Session = Depends(get_db),
):
    """Related PO / Related SPO tabs (AC-DT-03): every document this header's own
    non-cancelled rows are linked to, empty lists when nothing is linked yet."""
    try:
        validate_uuid_path(inquiry_id, resource="Order inquiry")
        return OrderInquiryHeaderService(db).related_documents(inquiry_id)
    except Exception as exc:
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))
