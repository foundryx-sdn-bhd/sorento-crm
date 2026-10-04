"""Warehouses API routes."""
from fastapi import APIRouter, Depends, Query, HTTPException, Request, status, Body
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Literal, Optional
from app.services.company_scope_resolver import (
    company_name_map,
    grants_requested,
    tag_company,
    widen_if_requested,
)
from app.database import get_db
from app.dependencies import get_current_user, get_current_user_or_api_key, require_permission
from app.services.inventory_service import WarehouseService
from app.schemas.inventory import (
    WarehouseCreate,
    WarehouseUpdate,
    WarehouseResponse,
    BulkImportWarehousesRequest,
    BulkImportWarehousesResponse,
)
from app.schemas.common import ListResponse, MAX_PAGE_LIMIT, ValidateImportResponse
from app.services.error_handler import handle_internal_error
from app.services.uuid_list_param import parse_uuid_list

router = APIRouter()


class BulkDeleteWarehousesRequest(BaseModel):
    ids: list[str]


@router.get("/", response_model=ListResponse[WarehouseResponse])
async def get_warehouses(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=MAX_PAGE_LIMIT),
    query: Optional[str] = Query(None),
    warehouse_ids: Optional[list[str]] = Query(
        None,
        description="Filter by canonical warehouse UUIDs (repeated / csv / JSON array).",
    ),
    is_active: Optional[bool] = Query(None),
    segment: Optional[str] = Query(
        None,
        description=(
            "Filter by who the location sells to: `dealer` (the bare site codes - "
            "BRW, MWH, DC1) or `project` (their suffixed bins). Drives the stock "
            "visibility card's \"Dealer pool\" preset."
        ),
    ),
    sort: Optional[str] = Query(None, description="Sort field: warehouse_code|warehouse_name|location|is_active|fulfilment_planning|created_at|updated_at|zones_count|stock_count"),
    dir: Optional[str] = Query("asc", description="asc|desc"),
    company_scope: Optional[Literal["grants"]] = Query(
        None,
        description="`grants` reads every company the caller is granted (staff sessions only) and tags each row with its company.",
    ),
    request: Request = None,
    current_user: dict = Depends(get_current_user_or_api_key),
    db: Session = Depends(get_db)
):
    """Get warehouses with pagination, search, and sort. Use is_active=true for active-only."""
    try:
        service = WarehouseService(db)
        with widen_if_requested(db, request, current_user, company_scope):
            result = service.list_warehouses(
                page=page,
                limit=limit,
                query=query,
                warehouse_ids=parse_uuid_list(warehouse_ids, param_name="warehouse_ids"),
                is_active=is_active,
                segment=segment,
                sort_field=sort,
                sort_dir=dir,
            )
            if grants_requested(request, current_user, company_scope):
                tag_company(result["data"], company_name_map(db))
        return result
    except Exception as e:
        raise handle_internal_error(str(e))


@router.get("/{warehouse_id}", response_model=WarehouseResponse)
async def get_warehouse(
    warehouse_id: str,
    current_user: dict = Depends(get_current_user_or_api_key),
    db: Session = Depends(get_db)
):
    """Get a single warehouse by ID."""
    try:
        service = WarehouseService(db)
        warehouse = service.get_warehouse(warehouse_id)
        return warehouse
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post("/", response_model=WarehouseResponse, status_code=status.HTTP_201_CREATED)
async def create_warehouse(
    warehouse_data: WarehouseCreate,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Create a new warehouse."""
    try:
        service = WarehouseService(db)
        warehouse = service.create_warehouse(warehouse_data)
        return warehouse
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.put("/{warehouse_id}", response_model=WarehouseResponse)
async def update_warehouse(
    warehouse_id: str,
    warehouse_data: WarehouseUpdate,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Update a warehouse."""
    try:
        service = WarehouseService(db)
        warehouse = service.update_warehouse(warehouse_id, warehouse_data)
        return warehouse
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.delete("/bulk", status_code=status.HTTP_200_OK)
async def bulk_delete_warehouses(
    body: BulkDeleteWarehousesRequest = Body(...),
    current_user: dict = Depends(require_permission("inventory.warehouses.delete")),
    db: Session = Depends(get_db),
):
    """Bulk hard-delete warehouses. Rows referenced by stock/zones/allocations are reported in `failed`."""
    try:
        service = WarehouseService(db)
        return service.bulk_delete_warehouses(body.ids)
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.delete("/{warehouse_id}", status_code=status.HTTP_200_OK)
async def delete_warehouse(
    warehouse_id: str,
    current_user: dict = Depends(require_permission("inventory.warehouses.delete")),
    db: Session = Depends(get_db),
):
    """Hard-delete a single warehouse by id (UUID or code/name)."""
    try:
        service = WarehouseService(db)
        return service.delete_warehouse(warehouse_id)
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post(
    "/bulk-import",
    status_code=status.HTTP_202_ACCEPTED,
    responses={200: {"description": "Validation only (validate_only=true)", "model": ValidateImportResponse}},
)
async def bulk_import_warehouses(
    import_data: BulkImportWarehousesRequest,
    current_user: dict = Depends(require_permission("inventory.warehouses.import")),
    db: Session = Depends(get_db),
):
    """Bulk import warehouses (upsert by warehouse_code). Queued; validate_only=true returns errors/warnings without writes."""
    try:
        if getattr(import_data, "validate_only", False):
            service = WarehouseService(db)
            result = service.bulk_import_warehouses(
                import_data.warehouses, current_user["id"], validate_only=True
            )
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content={
                    "valid": result["valid"],
                    "errors": result["errors"],
                    "warnings": result.get("warnings", []),
                    "summary": result.get("summary"),
                },
            )

        from app.services.job_service import JobService
        from app.services.queue_service import enqueue_job
        from app.tasks.import_tasks import process_warehouse_import

        job_service = JobService(db)
        job = job_service.create_job(
            job_type="warehouse_import",
            user_id=current_user["id"],
            metadata={"total_rows": len(import_data.warehouses)},
        )
        setattr(job, "total_rows", len(import_data.warehouses))
        db.commit()

        rq_job = enqueue_job(
            process_warehouse_import,
            str(job.id),
            import_data.warehouses,
            current_user["id"],
            queue_name="imports",
            job_timeout=3600,
            job_id=str(job.job_id),  # pre-assign RQ id = DB job_id; see update_job_with_rq_id
        )
        job_service.update_job_with_rq_id(job, rq_job.id)

        return {
            "job_id": rq_job.id,
            "status": "queued",
            "message": "Warehouse import job queued successfully",
        }
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))
