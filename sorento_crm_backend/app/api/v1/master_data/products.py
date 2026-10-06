"""Products API routes."""
import logging
import time
from datetime import date
from fastapi import APIRouter, Depends, Query, HTTPException, status, Body
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Optional, List
from app.database import get_db
from app.dependencies import get_current_user, get_current_user_or_api_key
from app.services.product_service import ProductService
from app.services import product_purchase_history_service
from app.services.attachment_field_link_service import AttachmentFieldLinkService
from app.services.uuid_list_param import parse_uuid_list
from app.schemas.product import (
    ProductCreate,
    ProductUpdate,
    ProductResponse,
    BulkImportProductsRequest,
    BulkDeleteProductsRequest,
    BulkUpdateProductsRequest,
)
from app.schemas.common import ListResponse, ErrorResponse, ValidateImportResponse
from app.services.error_handler import handle_internal_error
from app.services.stock_ask_limits import guard_chatbot_limits_edit, NULL_LIMITS

router = APIRouter()
logger = logging.getLogger(__name__)


def _normalize_entities(raw: Optional[list[str]]) -> Optional[list[str]]:
    """Flatten an `entities` query param into clean list[str] (JSON / comma / repeated)."""
    if raw is None:
        return None
    import json as _json
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if item is None:
            continue
        s = str(item).strip()
        if not s:
            continue
        if s.startswith("[") and s.endswith("]"):
            try:
                parsed = _json.loads(s)
                if isinstance(parsed, list):
                    for p in parsed:
                        ps = str(p).strip()
                        key = ps.lower()
                        if ps and key not in seen:
                            seen.add(key)
                            out.append(ps)
                    continue
            except Exception:
                pass
        for piece in s.split(","):
            piece = piece.strip()
            if not piece:
                continue
            key = piece.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(piece)
    return out or None


def _with_specs_and_or_specifications(
    service: ProductService,
    result: dict,
    *,
    include_specifications: bool,
    include_specs: bool,
) -> JSONResponse:
    """The listing page, each row carrying `specifications` and/or `specs`.

    Serialized through `ListResponse[ProductResponse]` BY HAND and returned as a
    raw response, because the declared `response_model` drops any key it does not
    declare - the standing gotcha. Declaring the fields on the schema instead
    would emit them as `null` on every row for every caller that never asked,
    which is exactly the byte-for-byte change these two opt-ins exist to avoid.

    Two separate fields on purpose - they answer different questions:
    `specifications: {values, rendered_text, sources}` is a sentence for a human
    editing the catalogue (`include_specifications`, existing); `specs:
    [{key, label, value, unit, rank_weight}]` is the RANKED LIST the chatbot
    presenter walks to build one field per populated key (`include_specs`, A1 -
    chatbot-growth-r1, AC-901). A caller can ask for either, both, or neither;
    asking for one never changes what the other returns.
    """
    body = ListResponse[ProductResponse].model_validate(result).model_dump(mode="json")
    rows = result.get("data") or []
    ids = [str(row.id) for row in rows]
    specifications_by_product = service.specifications_for_products(ids) if include_specifications else {}
    specs_by_product = service.spec_list_for_products(ids) if include_specs else {}
    for serialized, row in zip(body.get("data") or [], rows):
        if include_specifications:
            # Present-but-null when nothing has been derived: absence of data is a
            # fact the caller should be able to read, not a key it has to miss.
            serialized["specifications"] = specifications_by_product.get(str(row.id))
        if include_specs:
            serialized["specs"] = specs_by_product.get(str(row.id)) or []
    return JSONResponse(content=body)


@router.get("/", response_model=ListResponse[ProductResponse])
def get_products(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=5000),
    query: Optional[str] = Query(None),
    entities: Optional[list[str]] = Query(
        None,
        description=(
            "DEPRECATED - free-text entity bag. Prefer `product_ids`. Free-text "
            "resolution should go through `/api/v1/system/references/resolve` first."
        ),
    ),
    product_ids: Optional[list[str]] = Query(
        None,
        description=(
            "Filter by canonical product UUIDs. Accepts repeated values, comma-"
            "separated string, or JSON array. Non-UUID input → HTTP 400."
        ),
    ),
    category_id: Optional[str] = Query(None),
    brand_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    discontinued_batch_id: Optional[str] = Query(
        None,
        description="Filter to products reported in one 'products discontinued' notification batch.",
    ),
    discontinued_from: Optional[date] = Query(
        None,
        description="Discontinued-at range start (YYYY-MM-DD), inclusive by Malaysia calendar day.",
    ),
    discontinued_to: Optional[date] = Query(
        None,
        description="Discontinued-at range end (YYYY-MM-DD), inclusive by Malaysia calendar day.",
    ),
    price_min: Optional[float] = Query(None),
    price_max: Optional[float] = Query(None),
    item_type: Optional[str] = Query(None),
    length_min: Optional[float] = Query(None, description="Minimum dimensions_length in mm."),
    length_max: Optional[float] = Query(None, description="Maximum dimensions_length in mm."),
    width_min: Optional[float] = Query(None, description="Minimum dimensions_width in mm."),
    width_max: Optional[float] = Query(None, description="Maximum dimensions_width in mm."),
    height_min: Optional[float] = Query(None, description="Minimum dimensions_height in mm."),
    height_max: Optional[float] = Query(None, description="Maximum dimensions_height in mm."),
    any_dimension_min: Optional[float] = Query(
        None,
        description="Minimum value (mm) for ANY of length/width/height. Use for axis-agnostic queries like 'dimensions > 300mm'.",
    ),
    any_dimension_max: Optional[float] = Query(
        None,
        description="Maximum value (mm) for ANY of length/width/height.",
    ),
    variant_filter: Optional[str] = Query(
        "all",
        pattern="^(base|variant|all)$",
        description="Variant-graph filter: base (no parent) | variant (has parent) | all.",
    ),
    include_specifications: bool = Query(
        False,
        description=(
            "Attach each row's derived product specifications: "
            "`specifications: {values, rendered_text, sources}`, or null when the "
            "product has no derived row. Off by default - the response is "
            "unchanged for every caller that does not ask."
        ),
    ),
    include_specs: bool = Query(
        False,
        description=(
            "Attach each row's derived product specs as a RANKED LIST: "
            "`specs: [{key, label, value, unit, rank_weight}]`, only populated keys, "
            "ordered by rank_weight desc then label. `[]` when the product has none. "
            "Off by default - the response is unchanged for every caller that does "
            "not ask. Distinct from `include_specifications` (see that field)."
        ),
    ),
    chat_visible: bool = Query(
        False,
        description=(
            "Chatbot read: only products the chatbot may answer with (the product's and "
            "its category's `is_searchable` not false); an unscoped list (no product_ids) "
            "is also limited to active rows. The admin list never sends it and is unchanged."
        ),
    ),
    sort: Optional[str] = Query("created_at"),
    dir: Optional[str] = Query("asc"),
    current_user: dict = Depends(get_current_user_or_api_key),
    db: Session = Depends(get_db)
):
    """Get products with pagination, filtering, and sorting."""
    started = time.perf_counter()
    logger.info(
        "products.get start page=%s limit=%s query=%s category_id=%s brand_id=%s status=%s item_type=%s",
        page,
        limit,
        (query or "")[:80],
        category_id,
        brand_id,
        status,
        item_type,
    )
    try:
        service = ProductService(db)
        result = service.list_products(
            page=page,
            limit=limit,
            query=query,
            entities=_normalize_entities(entities),
            product_ids=parse_uuid_list(product_ids, param_name="product_ids"),
            category_id=category_id,
            brand_id=brand_id,
            status=status,
            discontinued_batch_id=discontinued_batch_id,
            discontinued_from=discontinued_from,
            discontinued_to=discontinued_to,
            price_min=price_min,
            price_max=price_max,
            item_type=item_type,
            length_min=length_min,
            length_max=length_max,
            width_min=width_min,
            width_max=width_max,
            height_min=height_min,
            height_max=height_max,
            any_dimension_min=any_dimension_min,
            any_dimension_max=any_dimension_max,
            variant_filter=variant_filter,
            chat_visible=chat_visible,
            sort_field=sort or "created_at",
            sort_dir=dir or "asc"
        )
        elapsed_ms = (time.perf_counter() - started) * 1000
        data_len = len(result.get("data") or []) if isinstance(result, dict) else -1
        total = (result.get("pagination") or {}).get("total") if isinstance(result, dict) else None
        logger.info(
            "products.get done elapsed_ms=%.1f rows=%s total=%s",
            elapsed_ms,
            data_len,
            total,
        )
        # Data-miss path (§3.3): when the service attached `alternatives` /
        # `relaxed_axis` (empty result only), bypass the strict `ListResponse`
        # response_model - which would silently drop those keys - and emit the raw
        # dict. `data` is always [] here, so the with-data path stays byte-identical
        # (AC-R1).
        if isinstance(result, dict) and result.get("alternatives"):
            from fastapi.encoders import jsonable_encoder
            return JSONResponse(content=jsonable_encoder(result))
        if (include_specifications or include_specs) and isinstance(result, dict):
            return _with_specs_and_or_specifications(
                service,
                result,
                include_specifications=include_specifications,
                include_specs=include_specs,
            )
        return result
    except Exception as e:
        elapsed_ms = (time.perf_counter() - started) * 1000
        logger.exception("products.get failed elapsed_ms=%.1f error=%s", elapsed_ms, str(e))
        raise handle_internal_error(str(e))


@router.get("/{product_id}", response_model=ProductResponse)
def get_product(
    product_id: str,
    current_user: dict = Depends(get_current_user_or_api_key),
    db: Session = Depends(get_db)
):
    """Get a single product by ID."""
    try:
        service = ProductService(db)
        product = service.get_product(product_id)
        return product
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.get("/{product_id}/purchase-history")
def get_product_purchase_history(
    product_id: str,
    limit: int = Query(
        50, ge=1, le=200,
        description="Max number of purchase-history rows to return (not a DataGrid page size).",
    ),
    current_user: dict = Depends(get_current_user_or_api_key),
    db: Session = Depends(get_db),
):
    """Every purchase order that bought this product, newest first, plus the cost summary.

    The product is fetched first, through the service, so a product this company cannot see
    returns 404 here too rather than leaking whether another company buys it.
    """
    try:
        ProductService(db).get_product(product_id)   # visibility gate (404s on miss)
        return product_purchase_history_service.purchase_history(db, product_id, limit)
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post("/", response_model=ProductResponse, status_code=status.HTTP_201_CREATED)
async def create_product(
    product_data: ProductCreate,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Create a new product."""
    try:
        guard_chatbot_limits_edit(
            db, current_user["id"], NULL_LIMITS, product_data.model_dump(exclude_unset=True)
        )
        service = ProductService(db)
        product = service.create_product(product_data, current_user["id"])
        return product
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


# Declared BEFORE `PUT /{product_id}`: FastAPI matches routes in order, and the
# path parameter would otherwise swallow the literal "bulk" (the DELETE pair
# below is ordered the same way for the same reason).
@router.put("/bulk", status_code=status.HTTP_200_OK)
def bulk_update_products(
    body: BulkUpdateProductsRequest = Body(...),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Bulk update products by ID. Body: { ids: string[], updates: { is_searchable } }."""
    try:
        service = ProductService(db)
        return service.bulk_update_products(body.ids, body.updates, current_user["id"])
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.put("/{product_id}", response_model=ProductResponse)
async def update_product(
    product_id: str,
    product_data: ProductUpdate,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Update a product."""
    try:
        service = ProductService(db)
        existing = service.get_product(product_id)
        guard_chatbot_limits_edit(
            db, current_user["id"], existing, product_data.model_dump(exclude_unset=True)
        )
        product = service.update_product(product_id, product_data, current_user["id"])
        return product
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post(
    "/bulk-import",
    status_code=status.HTTP_202_ACCEPTED,
    responses={200: {"description": "Validation only (validate_only=true)", "model": ValidateImportResponse}},
)
async def bulk_import_products(
    import_data: BulkImportProductsRequest,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Bulk import products from Excel data (queued). If validate_only=true, run validation only and return errors/warnings (no job)."""
    try:
        if getattr(import_data, "validate_only", False):
            service = ProductService(db)
            result = service.validate_products_import(import_data.products)
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
        from app.tasks.import_tasks import process_product_import

        job_service = JobService(db)
        job = job_service.create_job(
            job_type='product_import',
            user_id=current_user["id"],
            metadata={'total_rows': len(import_data.products)},
        )
        setattr(job, "total_rows", len(import_data.products))
        db.commit()

        rq_job = enqueue_job(
            process_product_import,
            str(job.id),
            import_data.products,
            current_user["id"],
            queue_name='imports',
            job_timeout=3600,
            job_id=str(job.job_id),  # pre-assign RQ id = DB job_id; see update_job_with_rq_id
        )
        job_service.update_job_with_rq_id(job, rq_job.id)

        return {
            'job_id': rq_job.id,
            'status': 'queued',
            'message': 'Import job queued successfully',
        }
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        raise handle_internal_error(str(e))


@router.delete("/bulk", status_code=status.HTTP_200_OK)
async def bulk_delete_products(
    body: BulkDeleteProductsRequest = Body(...),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Bulk delete products by ID. Body: { ids: string[] }."""
    try:
        service = ProductService(db)
        result = service.bulk_delete_products(body.ids)
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.delete("/{product_id}", status_code=status.HTTP_200_OK)
async def delete_product(
    product_id: str,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Delete a product."""
    try:
        service = ProductService(db)
        result = service.delete_product(product_id)
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


class _VariantParentBody(BaseModel):
    parent_id: Optional[str] = None


@router.put("/{product_id}/variant-parent", response_model=ProductResponse)
async def set_variant_parent(
    product_id: str,
    body: _VariantParentBody,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Manually set/change a product's variant parent (sticky override).

    Also the "attach a child" path: call with the child's id and the parent's
    code/UUID in the body. `product_id` and `parent_id` both accept a UUID or a
    product_code.
    """
    try:
        service = ProductService(db)
        return service.set_variant_parent(product_id, body.parent_id, current_user["id"])
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.delete("/{product_id}/variant-parent", response_model=ProductResponse)
async def unlink_variant(
    product_id: str,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Manually unlink a product from its variant parent (sticky override).

    Also the "remove a child" path: call with the child's id. `product_id`
    accepts a UUID or a product_code.
    """
    try:
        service = ProductService(db)
        return service.unlink_variant(product_id, current_user["id"])
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post("/{product_id}/variant-reset", response_model=ProductResponse)
async def reset_variant_auto(
    product_id: str,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Reset a manually-curated variant link back to auto-derivation.

    Clears `variant_link_manual` and re-derives the link from the product code.
    `product_id` accepts a UUID or a product_code.
    """
    try:
        service = ProductService(db)
        return service.reset_variant_auto(product_id)
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


class _FieldLinksBody(BaseModel):
    field_keys: List[str] = []


@router.get("/{product_id}/attachments/{attachment_id}/field-links", status_code=status.HTTP_200_OK)
async def get_product_attachment_field_links(
    product_id: str,
    attachment_id: str,
    current_user: dict = Depends(get_current_user_or_api_key),
    db: Session = Depends(get_db),
):
    """Read the per-row field keys currently linked to one product +
    attachment. Used by the FE Manage-field-links dialog to populate the
    initial selection."""
    try:
        product = ProductService(db).get_product(product_id)
        pid = str(product.get("id") if isinstance(product, dict) else getattr(product, "id"))
        rows = AttachmentFieldLinkService(db).list_for_attachment_and_row(
            attachment_id, "product", pid
        )
        return {
            "product_id": pid,
            "attachment_id": attachment_id,
            "field_keys": sorted({r.field_key for r in rows}),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post("/{product_id}/attachments/{attachment_id}/field-links", status_code=status.HTTP_200_OK)
async def set_product_attachment_field_links(
    product_id: str,
    attachment_id: str,
    body: _FieldLinksBody = Body(...),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Idempotently replace the per-row field-link rows for one product +
    attachment with ``body.field_keys``."""
    try:
        # Resolve product UUID - accept SKU like other endpoints.
        product = ProductService(db).get_product(product_id)
        pid = str(product.get("id") if isinstance(product, dict) else getattr(product, "id"))
        service = AttachmentFieldLinkService(db)
        keys = service.set_links(
            "product",
            pid,
            attachment_id,
            body.field_keys,
            created_by=current_user.get("id") if isinstance(current_user, dict) else None,
        )
        db.commit()
        return {"product_id": pid, "attachment_id": attachment_id, "field_keys": keys}
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))
