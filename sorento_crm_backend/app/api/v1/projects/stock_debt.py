"""Stock Debt: the month x product board, the cell drill and the workbook export
(S2, R22/R28; extended 24 Sep 2026, PLAN-stock-debt-filters-totals-export-24sep.md).

    GET  /project-sales/stock-debt
    GET  /project-sales/stock-debt/{product_id}/cell?month=
    POST /project-sales/stock-debt/export

All three behind `projects.stock_debt.view` (AC-S2-8; the permission and its grant
sweep from `projects.projects.view` ship in migration 443 with S1). Its own permission
rather than the module's general view right because this screen states the whole book's
exposure - every customer's shortfall on one page - which is not the same grant as reading
a project. The export route reuses it too (AC-12): exporting states nothing new, it only
prints what the board already answers.

Mounted before the projects router for the same reason its siblings are: `/project-sales/
stock-debt` is a literal segment that `/projects/{project_id}` must not capture.
"""
from __future__ import annotations

from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Body, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_permission, require_permission_with_api_key
from app.schemas.common import MAX_PAGE_LIMIT
from app.schemas.download import DownloadResponse
from app.schemas.stock_debt import Book, StockDebtCell, StockDebtExportIn, StockDebtList
from app.services.error_handler import AppException, handle_internal_error
# Reused, not reinvented (AC-18): the same cap `StockDebtService.export()` itself refuses
# above - the route checks it FIRST, before any `user_downloads` row exists, the same
# "every guard runs before the row" shape `order_summary.py`'s export route applies.
from app.services.scm.low_stock_report_service import MAX_LOW_STOCK_ROWS
from app.services.scm.stock_debt_service import StockDebtService
from app.services.uuid_path_param import validate_uuid_path

router = APIRouter()

VIEW = "projects.stock_debt.view"


@router.get("/stock-debt", response_model=StockDebtList)
def list_stock_debt(
    query: Optional[str] = Query(
        None, description="Product code or name."
    ),
    group: List[str] = Query(
        [],
        description=(
            "Ownership group suffix (`BB`, `IB`, ...), repeatable: any of the listed "
            "groups, spanning the flagged bins of every group named. Narrows the STOCK and the DEMAND "
            "read, so the balances are the group's own rather than the book's filtered. "
            "Ignored under `book=retail` - a site pool is nobody's ownership group."
        ),
    ),
    only_debt: bool = Query(
        True, description="Drop products that owe nothing in any month."
    ),
    date_from: Optional[date] = Query(
        None,
        description=(
            "Drop demand lines with `required_date` before this date; the axis (columns) "
            "starts at `max(current month, this date's month)`. Undated demand has no "
            "date to test and always survives (R14). Replaces `cutoff`, not an alias."
        ),
    ),
    date_to: Optional[date] = Query(
        None,
        description=(
            "Drop demand lines with `required_date` after this date; the axis (columns) "
            "ends at its month. Supply landing after a line's own due date, but on or "
            "before `date_to`, still covers it - only demand is dropped (R14). Replaces "
            "`cutoff`, not an alias."
        ),
    ),
    supplier_ids: List[str] = Query(
        [],
        description=(
            "Repeatable. Keep only products whose LAST supplier (newest purchase-order "
            "line, else the primary-flagged product supplier) is ANY of these values. "
            "`none` is one more value among the others, keeping products with neither. "
            "Replaces `supplier_id`, not an alias (R15)."
        ),
    ),
    book: Book = Query(
        "all",
        description=(
            "`all` (default) spans flagged project bins AND the site pools in one read; "
            "`project` is flagged bins only (the pre-24-Sep view); `retail` is pools only "
            "and ignores `group`."
        ),
    ),
    projected: bool = Query(
        False,
        description=(
            "STOCK-DEBT-PROJECTION: month cells show the running projected balance - the "
            "project lines' own month figure (pool supply only through a link) plus a "
            "project reserve's unlinked incoming supply, carried to the earliest shortfall. "
            "Display only; nothing is assigned."
        ),
    ),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=MAX_PAGE_LIMIT),
    _user: dict = Depends(require_permission_with_api_key(VIEW)),
    db: Session = Depends(get_db),
):
    """One row per product, one column per month, the cell is the cumulative balance.

    Plain ``def``, so FastAPI runs it in a threadpool: it is synchronous SQLAlchemy over the
    whole flagged catalogue, and on the event loop it would hold up every other request.
    """
    try:
        return StockDebtService(db).list(
            query=query,
            group=group,
            only_debt=only_debt,
            date_from=date_from,
            date_to=date_to,
            supplier_ids=supplier_ids,
            book=book,
            page=page,
            limit=limit,
            projected=projected,
        )
    except Exception as exc:
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.get("/stock-debt/{product_id}/cell", response_model=StockDebtCell)
def stock_debt_cell(
    product_id: str,
    month: str = Query(
        ...,
        description=(
            "`YYYY-MM`, or `tba` / `undated` / `unlocated` for the three right-hand columns."
        ),
    ),
    group: List[str] = Query(
        [],
        description=(
            "The ownership group(s) the BOARD was narrowed to (repeatable). Same meaning as on the list: it "
            "narrows the span the balance is recomputed from, so the drill foots with the "
            "cell that opened it."
        ),
    ),
    date_from: Optional[date] = Query(
        None,
        description=(
            "The BOARD's own `date_from`, echoed so the drill foots with the cell that "
            "opened it (R14). Replaces `cutoff`, not an alias."
        ),
    ),
    date_to: Optional[date] = Query(
        None,
        description=(
            "The BOARD's own `date_to`, echoed so the drill foots with the cell that "
            "opened it (R14). Replaces `cutoff`, not an alias."
        ),
    ),
    book: Book = Query(
        "all",
        description="The BOARD's own book, echoed so the drill foots with the cell that opened it.",
    ),
    projected: bool = Query(
        False,
        description=(
            "STOCK-DEBT-PROJECTION: month cells show the running projected balance - the "
            "project lines' own month figure (pool supply only through a link) plus a "
            "project reserve's unlinked incoming supply, carried to the earliest shortfall. "
            "Display only; nothing is assigned."
        ),
    ),
    _user: dict = Depends(require_permission_with_api_key(VIEW)),
    db: Session = Depends(get_db),
):
    """The two tables behind one cell: what is DUE in that month and what is HELD for it."""
    try:
        validate_uuid_path(product_id, resource="Product")
        return StockDebtService(db).cell(
            product_id, month, group, date_from=date_from, date_to=date_to, book=book,
            projected=projected,
        )
    except Exception as exc:
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.post("/stock-debt/export", response_model=DownloadResponse, status_code=201)
def export_stock_debt(
    payload: StockDebtExportIn = Body(default_factory=StockDebtExportIn),
    # `require_permission`, NOT `_with_api_key` (security review, fix-before-merge):
    # creating a download row and enqueuing a worker job is a WRITE, not the read that
    # dependency's own docstring says it is for - an API-key-only principal (no JWT)
    # must not reach it, however its act-as user's role is granted. The two GETs above
    # stay on the API-key variant; they are the read.
    current_user: dict = Depends(require_permission(VIEW)),
    db: Session = Depends(get_db),
):
    """Queue the workbook through My Downloads (R10/R12, AC-12), the same pipeline the low
    stock report uses (`generate_low_stock_report` / `export_order_summary`): a
    `user_downloads` row is created FIRST, `generate_stock_debt_xlsx` runs on the `imports`
    queue, and the workbook itself is fetched later from the drawer once the worker marks
    the row ready. On an enqueue failure the row is marked failed rather than left pending
    forever, and a 503 tells the caller to try again.

    Filters travel as the ROUTE's own body (`StockDebtExportIn`), not a `run_id`: this
    screen has no reorder run underneath it, only the board's own current filters.

    Two guards run BEFORE any `user_downloads` row exists (AC-12d/AC-18, `order_summary.
    py`'s own export route runs its guards the same way): one in-flight export per user
    per kind (`DownloadService.has_in_flight`, the same guard `export_order_inquiry_
    worklist_async` runs), and the row-count cap - a single `list(..., limit=1)` read,
    whose `pagination.total` is the WHOLE filtered set regardless of the limit used.
    """
    from app.api.v1.projects._common import acting_company_id
    from app.services.download_service import DownloadService
    from app.services.queue_service import enqueue_job
    from app.tasks.export_tasks import generate_stock_debt_xlsx

    try:
        filters = payload.model_dump()

        if DownloadService(db).has_in_flight(
            user_id=str(current_user["id"]), kind="stock_debt_xlsx",
        ):
            raise AppException(
                status_code=409,
                message="A stock debt export is already being prepared - check My "
                        "Downloads.",
            )

        guard = StockDebtService(db).list(
            query=payload.query,
            group=payload.group,
            only_debt=payload.only_debt,
            date_from=payload.date_from,
            date_to=payload.date_to,
            supplier_ids=payload.supplier_ids,
            book=payload.book,
            page=1,
            limit=1,
            projected=payload.projected,
        )
        if guard["pagination"]["total"] > MAX_LOW_STOCK_ROWS:
            raise AppException(status_code=422, message="Narrow the filters first")

        # The worker has no request-scoped company (AC-12b): snapshot the enqueuing
        # request's own single-company scope so the task can adopt it - same shape
        # `export_order_inquiry_worklist_async` uses for the same reason (reuse, not
        # reimplement).
        company_id = acting_company_id(db)

        filename = f"stock-debt-{date.today().strftime('%d%m%Y')}.xlsx"
        download = DownloadService(db).create(
            user_id=str(current_user["id"]),
            kind="stock_debt_xlsx",
            filename=filename,
        )
        try:
            enqueue_job(
                generate_stock_debt_xlsx,
                str(download.id),
                str(current_user["id"]),
                filters,
                company_id=company_id,
                queue_name="imports",
                job_timeout=600,
            )
        except Exception as e:  # noqa: BLE001
            DownloadService(db).mark_failed(
                str(download.id), f"Could not queue stock debt export: {e}"
            )
            raise AppException(
                status_code=503,
                message="Could not queue stock debt export. Please try again.",
            )
        return DownloadResponse.model_validate(DownloadService(db).get(str(download.id)))
    except Exception as exc:
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))
