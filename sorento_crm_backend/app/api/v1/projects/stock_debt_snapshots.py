"""Stock Debt snapshots: the board frozen at a moment, with a decision per product
(STOCK-DEBT-SNAPSHOT, AC-1 .. AC-18).

    POST /project-sales/stock-debt/snapshots
    GET  /project-sales/stock-debt/snapshots
    GET  /project-sales/stock-debt/snapshots/{id}
    GET  /project-sales/stock-debt/snapshots/{id}/rows
    GET  /project-sales/stock-debt/snapshots/{id}/rows/{row_id}/lines
    PUT  /project-sales/stock-debt/snapshots/{id}/rows/{row_id}/decision
    POST /project-sales/stock-debt/snapshots/{id}/export

Delete is the deferred action `stock_debt_snapshot.delete` (`app/services/record_actions.py`),
not a route. Every route needs `projects.stock_debt.view`; the three writes use
`require_permission` (session only), the same split `stock_debt.py` makes for its export.
Mounted before the stock debt router so the literal `snapshots` segment is never read as a
`{product_id}`.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_permission, require_permission_with_api_key
from app.schemas.common import MAX_PAGE_LIMIT
from app.schemas.download import DownloadResponse
from app.schemas.stock_debt_snapshot import (
    DecisionFilter,
    SnapshotCreate,
    SnapshotDecisionIn,
    SnapshotDetail,
    SnapshotExportIn,
    SnapshotLineList,
    SnapshotList,
    SnapshotRow,
    SnapshotRowList,
)
from app.services.error_handler import AppException, handle_internal_error
from app.services.scm.stock_debt_snapshot_service import StockDebtSnapshotService
from app.services.uuid_path_param import validate_uuid_path

router = APIRouter()

VIEW = "projects.stock_debt.view"
BASE = "/stock-debt/snapshots"


@router.post(BASE, response_model=SnapshotDetail, status_code=201)
def create_snapshot(
    payload: SnapshotCreate,
    current_user: dict = Depends(require_permission(VIEW)),
    db: Session = Depends(get_db),
):
    """Freeze the board for the filters given. Plain `def`: one synchronous read of the book."""
    try:
        service = StockDebtSnapshotService(db)
        snapshot = service.create(
            name=payload.name,
            filters=payload.filters.model_dump(),
            user_id=str(current_user["id"]),
        )
        return service.get(str(snapshot.id), str(current_user["id"]))
    except Exception as exc:
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.get(BASE, response_model=SnapshotList)
def list_snapshots(
    query: Optional[str] = Query(None, description="Matches the snapshot name."),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=MAX_PAGE_LIMIT),
    current_user: dict = Depends(require_permission_with_api_key(VIEW)),
    db: Session = Depends(get_db),
):
    try:
        return StockDebtSnapshotService(db).list(
            user_id=str(current_user["id"]), query=query, page=page, limit=limit
        )
    except Exception as exc:
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.get(BASE + "/{snapshot_id}", response_model=SnapshotDetail)
def get_snapshot(
    snapshot_id: str,
    current_user: dict = Depends(require_permission_with_api_key(VIEW)),
    db: Session = Depends(get_db),
):
    try:
        validate_uuid_path(snapshot_id, resource="Snapshot")
        return StockDebtSnapshotService(db).get(snapshot_id, str(current_user["id"]))
    except Exception as exc:
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.get(BASE + "/{snapshot_id}/rows", response_model=SnapshotRowList)
def list_snapshot_rows(
    snapshot_id: str,
    query: Optional[str] = Query(None, description="Product code or name."),
    decision: Optional[DecisionFilter] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=MAX_PAGE_LIMIT),
    _user: dict = Depends(require_permission_with_api_key(VIEW)),
    db: Session = Depends(get_db),
):
    try:
        validate_uuid_path(snapshot_id, resource="Snapshot")
        return StockDebtSnapshotService(db).rows(
            snapshot_id, query=query, decision=decision, page=page, limit=limit
        )
    except Exception as exc:
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.get(BASE + "/{snapshot_id}/rows/{row_id}/lines", response_model=SnapshotLineList)
def snapshot_row_lines(
    snapshot_id: str,
    row_id: str,
    month: Optional[str] = Query(
        None, description="One bucket: `YYYY-MM`, `tba`, `undated` or `unlocated`."
    ),
    _user: dict = Depends(require_permission_with_api_key(VIEW)),
    db: Session = Depends(get_db),
):
    try:
        validate_uuid_path(snapshot_id, resource="Snapshot")
        validate_uuid_path(row_id, resource="Snapshot row")
        return StockDebtSnapshotService(db).lines(snapshot_id, row_id, month)
    except Exception as exc:
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.put(BASE + "/{snapshot_id}/rows/{row_id}/decision", response_model=SnapshotRow)
def set_snapshot_decision(
    snapshot_id: str,
    row_id: str,
    payload: SnapshotDecisionIn,
    current_user: dict = Depends(require_permission(VIEW)),
    db: Session = Depends(get_db),
):
    try:
        validate_uuid_path(snapshot_id, resource="Snapshot")
        validate_uuid_path(row_id, resource="Snapshot row")
        return StockDebtSnapshotService(db).set_decision(
            snapshot_id,
            row_id,
            user_id=str(current_user["id"]),
            decision=payload.decision,
            warehouse_id=payload.warehouse_id,
            note=payload.note,
        )
    except Exception as exc:
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.post(BASE + "/{snapshot_id}/export", response_model=DownloadResponse, status_code=201)
def export_snapshot(
    snapshot_id: str,
    payload: SnapshotExportIn,
    # Session only, for the reason `export_stock_debt` gives: a download row and a worker job
    # are a write.
    current_user: dict = Depends(require_permission(VIEW)),
    db: Session = Depends(get_db),
):
    """Queue the workbook of the STORED snapshot through My Downloads, the pipeline
    `export_stock_debt` uses. The guards run before any `user_downloads` row exists."""
    from app.api.v1.projects._common import acting_company_id
    from app.services.download_service import DownloadService
    from app.services.queue_service import enqueue_job
    from app.tasks.export_tasks import generate_stock_debt_snapshot_xlsx

    try:
        validate_uuid_path(snapshot_id, resource="Snapshot")
        snapshot = StockDebtSnapshotService(db)._get_snapshot(snapshot_id)
        user_id = str(current_user["id"])
        if DownloadService(db).has_in_flight(user_id=user_id, kind="stock_debt_snapshot_xlsx"):
            raise AppException(
                status_code=409,
                message="A snapshot export is already being prepared - check My Downloads.",
            )
        company_id = acting_company_id(db)
        download = DownloadService(db).create(
            user_id=user_id,
            kind="stock_debt_snapshot_xlsx",
            filename=f"stock-debt-snapshot-{snapshot.created_at.strftime('%d%m%Y')}.xlsx",
        )
        try:
            enqueue_job(
                generate_stock_debt_snapshot_xlsx,
                str(download.id),
                user_id,
                snapshot_id,
                split=payload.split,
                show_aux=payload.show_aux,
                company_id=company_id,
                queue_name="imports",
                job_timeout=600,
            )
        except Exception as e:  # noqa: BLE001
            DownloadService(db).mark_failed(
                str(download.id), f"Could not queue snapshot export: {e}"
            )
            raise AppException(
                status_code=503,
                message="Could not queue snapshot export. Please try again.",
            )
        return DownloadResponse.model_validate(DownloadService(db).get(str(download.id)))
    except Exception as exc:
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))
