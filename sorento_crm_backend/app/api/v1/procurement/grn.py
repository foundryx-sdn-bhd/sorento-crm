"""GRN (Goods Receipt Note) API routes."""
from fastapi import APIRouter, Depends, Query, HTTPException, status, File, UploadFile, Body
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from datetime import date
from typing import Optional
from pydantic import BaseModel

from app.database import get_db
from app.dependencies import get_current_user, get_current_user_or_api_key, require_permission
from app.services.procurement_service import PickingHeaderService
from app.services.job_service import JobService
from app.services.queue_service import enqueue_job
from app.schemas.procurement import PickingHeaderCreate, PickingHeaderUpdate, PickingHeaderResponse
from app.schemas.common import ListResponse
from app.services.error_handler import AppException, handle_internal_error
from app.services.grn_summary_service import grn_summary_rows
from app.services.uuid_list_param import parse_uuid_list

router = APIRouter()


class BulkDeleteGRNsRequest(BaseModel):
    ids: list[str]


@router.get("/", response_model=ListResponse[PickingHeaderResponse])
async def get_grns(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=1000),
    query: Optional[str] = Query(None),
    entities: Optional[list[str]] = Query(
        None,
        description=(
            "Free-text entity bag. Server resolves via hybrid (substring → pg_trgm → RAG). "
            "Product matches narrow linked picking_lines; picking_number matches narrow header. "
            "ONE ENTITY PER ARRAY ELEMENT."
        ),
    ),
    product_query: Optional[str] = Query(
        None,
        description="Legacy: partial product filter on linked picking_lines.",
    ),
    picking_status: Optional[str] = Query(None),
    inspection_status: Optional[str] = Query(None),
    sort: Optional[str] = Query("created_at"),
    dir: Optional[str] = Query("asc"),
    current_user: dict = Depends(get_current_user_or_api_key),
    db: Session = Depends(get_db)
):
    """Get GRNs (Goods Receipt Notes) with pagination, filtering, and sorting."""
    from app.services.entity_filter_helpers import (
        normalize_entities_query_param,
        resolve_or_empty,
    )

    entity_echo = None
    norm = normalize_entities_query_param(entities)
    if norm:
        buckets = resolve_or_empty(db, norm)
        if buckets is not None:
            entity_echo = buckets.as_echo()
            if buckets.product_codes and not product_query:
                product_query = buckets.product_codes[0]
            if buckets.picking_numbers and not query:
                query = buckets.picking_numbers[0]
            # If nothing relevant resolved → empty
            if not (buckets.product_codes or buckets.picking_numbers or query or product_query):
                return {
                    "data": [],
                    "pagination": {"total": 0, "page": page, "limit": limit},
                    "empty": True,
                    "resolved_entities": entity_echo,
                }
    try:
        service = PickingHeaderService(db)
        result = service.list_grns(
            page=page,
            limit=limit,
            query=query,
            product_query=product_query,
            picking_status=picking_status,
            inspection_status=inspection_status,
            sort_field=sort or "created_at",
            sort_dir=dir or "asc"
        )
        if entity_echo is not None and isinstance(result, dict):
            result["resolved_entities"] = entity_echo
        return result
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post("/import-listing", status_code=status.HTTP_202_ACCEPTED)
async def import_grn_listing(
    file: UploadFile = File(..., description="Excel file: GRN listing (doc number, transfer from, date)"),
    validate_only: bool = Query(False, description="If true, validate file only and return errors/warnings (no import)."),
    current_user: dict = Depends(require_permission("procurement.grn.import")),
    db: Session = Depends(get_db),
):
    """Queue GRN listing import. Use validate_only=true to test without importing."""
    if not file.filename or not file.filename.lower().endswith((".xlsx", ".xls", ".xlsm")):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Excel file (.xlsx, .xls or .xlsm) required")
    file_data = await file.read()
    # Retain the ORIGINAL bytes (pre-macro-strip) for source-file tracing.
    source_bytes, source_name, source_ctype = file_data, file.filename, file.content_type
    from app.services.excel_macro_stripper import maybe_strip
    file_data, cleaned_name = maybe_strip(file_data, file.filename or "unknown.xlsx")

    if validate_only:
        from app.models.base import get_company_scope
        from app.tasks.import_tasks import validate_grn_listing_import

        # Preview at the SAME company scope the import will run at, so it cannot
        # report "would succeed" against rows the scoped import never sees.
        result = validate_grn_listing_import(
            file_data, company_scope=get_company_scope(db)
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

    from app.tasks.import_tasks import process_grn_listing_import
    job_service = JobService(db)
    job = job_service.create_job(
        job_type="grn_listing_import",
        user_id=current_user["id"],
        filename=cleaned_name,
    )
    from app.services.import_source_store import store_import_source_file
    store_import_source_file(job, source_bytes, source_name, source_ctype)
    db.commit()
    rq_job = enqueue_job(
        process_grn_listing_import,
        str(job.id),
        file_data,
        cleaned_name,
        current_user["id"],
        queue_name="imports",
        job_timeout=3600,
        job_id=str(job.job_id),  # pre-assign RQ id = DB job_id; see update_job_with_rq_id
    )
    job_service.update_job_with_rq_id(job, rq_job.id)
    return {"message": "GRN listing import queued.", "job_id": job.job_id, "id": str(job.id)}


@router.post("/import-lines", status_code=status.HTTP_202_ACCEPTED)
async def import_grn_lines(
    file: UploadFile = File(..., description="Excel file: GRN lines (doc no, item code, location, quantity)"),
    validate_only: bool = Query(False, description="If true, validate file only and return errors/warnings (no import)."),
    current_user: dict = Depends(require_permission("procurement.grn.import")),
    db: Session = Depends(get_db),
):
    """Queue GRN lines import. Use validate_only=true to test without importing."""
    if not file.filename or not file.filename.lower().endswith((".xlsx", ".xls", ".xlsm")):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Excel file (.xlsx, .xls or .xlsm) required")
    file_data = await file.read()
    # Retain the ORIGINAL bytes (pre-macro-strip) for source-file tracing.
    source_bytes, source_name, source_ctype = file_data, file.filename, file.content_type
    from app.services.excel_macro_stripper import maybe_strip
    file_data, cleaned_name = maybe_strip(file_data, file.filename or "unknown.xlsx")

    if validate_only:
        from app.models.base import get_company_scope
        from app.tasks.import_tasks import validate_grn_lines_import

        result = validate_grn_lines_import(
            file_data, company_scope=get_company_scope(db)
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

    from app.tasks.import_tasks import process_grn_lines_import
    job_service = JobService(db)
    job = job_service.create_job(
        job_type="grn_lines_import",
        user_id=current_user["id"],
        filename=cleaned_name,
    )
    from app.services.import_source_store import store_import_source_file
    store_import_source_file(job, source_bytes, source_name, source_ctype)
    db.commit()
    rq_job = enqueue_job(
        process_grn_lines_import,
        str(job.id),
        file_data,
        cleaned_name,
        current_user["id"],
        queue_name="imports",
        job_timeout=3600,
        job_id=str(job.job_id),  # pre-assign RQ id = DB job_id; see update_job_with_rq_id
    )
    job_service.update_job_with_rq_id(job, rq_job.id)
    return {"message": "GRN lines import queued.", "job_id": job.job_id, "id": str(job.id)}


#: MULTI-ASK-PLAN S3: registered ahead of `/{grn_id}`, which accepts any one segment.
@router.get("/summary")
def get_grn_summary(
    product_ids: Optional[list[str]] = Query(
        None, description="Canonical product UUIDs (csv / JSON / repeated). Required."
    ),
    date_from: Optional[date] = Query(None, description="First GRN date counted (ISO), inclusive."),
    date_to: Optional[date] = Query(None, description="Last GRN date counted (ISO), inclusive."),
    current_user: dict = Depends(get_current_user_or_api_key),
    db: Session = Depends(get_db),
):
    """Per asked product: `received_qty` (sum of picked quantity over approved, not
    cancelled `goods_received` headers dated in the window; no window = all time),
    `grn_count`, and the newest such receipt of ALL time (`last_receipt_date`,
    `last_receipt_qty`, summed over that date). One row per product that exists, in the
    asked order. See `grn_summary_service`."""
    ids = parse_uuid_list(product_ids, param_name="product_ids")
    if not ids:
        raise AppException(422, "product_ids is required", detail="product_ids", code="product_ids_required")
    if date_from and date_to and date_from > date_to:
        raise AppException(422, "date_from is after date_to", detail="date_from, date_to", code="date_range_inverted")
    try:
        rows = grn_summary_rows(db, product_ids=ids, date_from=date_from, date_to=date_to)
        return JSONResponse(content={"data": rows, "empty": not rows})
    except AppException:
        raise
    except Exception as e:  # noqa: BLE001
        raise handle_internal_error(str(e))


@router.get("/{grn_id}", response_model=PickingHeaderResponse)
async def get_grn(
    grn_id: str,
    current_user: dict = Depends(get_current_user_or_api_key),
    db: Session = Depends(get_db)
):
    """Get a single GRN by ID."""
    try:
        service = PickingHeaderService(db)
        grn = service.get_grn(grn_id)
        return grn
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post("/", response_model=PickingHeaderResponse, status_code=status.HTTP_201_CREATED)
async def create_grn(
    grn_data: PickingHeaderCreate,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Create a new GRN (Goods Receipt Note) with lines."""
    try:
        service = PickingHeaderService(db)
        grn = service.create_grn(grn_data, current_user["id"])
        return grn
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.put("/{grn_id}", response_model=PickingHeaderResponse)
async def update_grn(
    grn_id: str,
    grn_data: PickingHeaderUpdate,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Update a GRN."""
    try:
        service = PickingHeaderService(db)
        grn = service.update_grn(grn_id, grn_data)
        return grn
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.delete("/bulk", status_code=status.HTTP_200_OK)
async def bulk_delete_grns(
    body: BulkDeleteGRNsRequest = Body(...),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Bulk delete GRNs by ID."""
    try:
        service = PickingHeaderService(db)
        return service.bulk_delete_grns(body.ids)
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.delete("/{grn_id}", status_code=status.HTTP_200_OK)
async def delete_grn(
    grn_id: str,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Delete a GRN (cascade will delete lines)."""
    try:
        service = PickingHeaderService(db)
        result = service.delete_grn(grn_id)
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))
