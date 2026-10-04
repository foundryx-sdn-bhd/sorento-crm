"""Stock API routes."""
import json as _json
import logging
import time
from fastapi import APIRouter, Depends, Query, HTTPException, status, Body, Path
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from typing import Optional
from pydantic import BaseModel


def _normalize_entities(raw: Optional[list[str]]) -> Optional[list[str]]:
    """Flatten an `entities` query param into a clean list[str].

    Accepts None, a list of strings (repeated query param), or a single-element list
    containing a JSON array or comma-separated string - all forms n8n / curl callers
    produce depending on how they encode the param.
    """
    if raw is None:
        return None
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

from app.database import get_db
from app.dependencies import (
    get_current_user,
    get_current_user_or_api_key,
    require_permission,
    require_permission_with_api_key,
)
from app.services.inventory_service import StockService
from app.services.uuid_list_param import parse_requested_quantities, parse_uuid_list
from app.schemas.inventory import StockResponse, StockBalanceListResponse, StockDashboardResponse, BulkImportStockRequest, BulkImportStockResponse, StockLedgerResponse
from app.schemas.common import ListResponse, ValidateImportResponse
from app.services.error_handler import handle_internal_error

router = APIRouter()
logger = logging.getLogger(__name__)


class BulkDeleteStockRequest(BaseModel):
    ids: list[str]


def _with_sellable(service: StockService, result: dict) -> JSONResponse:
    """Attach `open_so_qty` + `sellable` per row/entry (A2, AC-903/AC-904).

    Bypasses `response_model` on purpose, same reason as products'
    `_with_specifications`: `StockBalanceListResponse` does not declare these
    fields, so returning `result` straight through response_model would drop
    them silently rather than serialize them. `sellable` is `on_hand - open_qty`,
    left NEGATIVE here - the chatbot presenter is what renders "0 (oversold by
    N)"; a raw/staff caller reading a negative number can already do that math.

    `stock_availability` rows are skipped entirely: that mode's whole point is
    never disclosing a quantity, and open_so_qty/sellable are quantities.

    The company gate (PLAN company-so-feed-flag): a company whose AutoCount SO
    feed is not connected (`companies.so_feed_live = false`) has no sales-order
    data in the CRM at all, so `open_so_qty = 0` would read as "nothing on
    order" when the truth is "we do not know". A row/entry/location whose
    company is in that set gets NEITHER `open_so_qty` NOR `sellable` attached -
    the same shape as a caller that never asked for `include_sellable`.
    """
    body = StockBalanceListResponse.model_validate(result).model_dump(mode="json")
    rows = result.get("data") or []
    product_ids: set[str] = {
        str(getattr(r, "product_id", None)) for r in rows if getattr(r, "product_id", None)
    }
    for entry in result.get("stock_summary") or []:
        pid = entry.get("product_id") if isinstance(entry, dict) else None
        if pid:
            product_ids.add(str(pid))
    # TWO aggregations, and the split is the point (review, should-fix 5). A per-warehouse
    # ROW gets that warehouse's own open SO; the product SUMMARY row gets the product
    # total (staff path: the per-warehouse quantities plus the lines that carry no
    # `warehouse_id`; under a contact's policy: see below). Subtracting the product total on every warehouse row - the first cut
    # - reported the same demand two, three, four times over and printed "oversold" against
    # a warehouse that was not.
    open_so_by_warehouse, _unlocated = service.open_so_qty_by_product_warehouse(
        list(product_ids)
    )
    # STOCK-TOTAL-OS-SCOPE (owner decision 2 Oct 2026): under a contact's visibility
    # policy the product TOTAL's open SO is the sum over the warehouses that contact may
    # see - the same rule its location lines are filtered by - so a hidden warehouse's
    # demand is never shown or hinted. Lines with no warehouse are not shown anywhere in
    # this reply (owner option b). The staff path (no contact, no policy) keeps the
    # product total: every line, unlocated included.
    policy = service.resolved_policy
    visible: dict[str, str] = {}
    if policy is not None:
        visible = service.visible_warehouse_ids(policy)
        open_so_total: dict[str, int] = {}
        for (pid, wid), qty in open_so_by_warehouse.items():
            if wid in visible:
                open_so_total[pid] = open_so_total.get(pid, 0) + qty
    else:
        open_so_total = service.open_so_qty_by_product(list(product_ids))

    # Company feed gate: look up the (small, handful-of-rows) no-feed set FIRST.
    # Empty on the common all-feed-on path, which skips `company_id_by_product`
    # entirely - one query fewer per stock page when every company is connected.
    # A detailed row carries its own `company_id` directly; a compact/synthesised
    # entry does not, so it resolves through the product only when needed.
    no_feed_companies = service.no_feed_company_ids()
    company_by_product = (
        service.company_id_by_product(list(product_ids)) if no_feed_companies else {}
    )

    def _feed_on(pid: str, row_company_id: Optional[str] = None) -> bool:
        if not no_feed_companies:
            return True
        cid = row_company_id or company_by_product.get(pid)
        return not (cid and str(cid) in no_feed_companies)

    def _attach(target: dict, open_qty: int, on_hand) -> None:
        try:
            oh = int(on_hand) if on_hand is not None else 0
        except (TypeError, ValueError):
            oh = 0
        target["open_so_qty"] = open_qty
        target["sellable"] = oh - open_qty

    def _attach_total(target: dict, pid: str, on_hand) -> None:
        _attach(target, open_so_total.get(pid, 0), on_hand)

    for serialized, row in zip(body.get("data") or [], rows):
        pid = str(getattr(row, "product_id", "") or "")
        if not pid:
            continue
        if not _feed_on(pid, getattr(row, "company_id", None)):
            continue
        wid = str(getattr(row, "warehouse_id", "") or "")
        # A detailed row IS a (product, warehouse) pair. With no warehouse on the row at
        # all there is nothing to narrow by, so it takes the product total (visible-only
        # under a policy) - the one row shape that has no better answer.
        open_qty = open_so_by_warehouse.get((pid, wid), 0) if wid else open_so_total.get(pid, 0)
        _attach(serialized, open_qty, getattr(row, "quantity_on_hand", None))
    # COMPACT entries: the product total on the entry (under a policy, visible warehouses
    # only and no unassigned remainder), and each warehouse line's own open SO on the location (D1, owner
    # console pass 8 Sep: "*BRW:* 0 (O/S: 12)"). Locations carry a code, not an id, so the
    # codes are resolved once through the service.
    summary_entries = [e for e in (body.get("stock_summary") or []) if isinstance(e, dict)]
    loc_codes = {
        str(loc.get("warehouse_code"))
        for e in summary_entries
        for loc in (e.get("locations") or [])
        if isinstance(loc, dict) and loc.get("warehouse_code")
    }
    wh_id_by_code = service.warehouse_ids_by_code(list(loc_codes)) if loc_codes else {}
    for entry in summary_entries:
        pid = str(entry.get("product_id") or "")
        if not pid or not _feed_on(pid):
            continue
        _attach_total(entry, pid, entry.get("total_on_hand"))
        for loc in entry.get("locations") or []:
            if not isinstance(loc, dict):
                continue
            wid = wh_id_by_code.get(str(loc.get("warehouse_code") or ""))
            loc["open_so_qty"] = open_so_by_warehouse.get((pid, wid), 0) if wid else 0
        if policy is not None:
            # Owner ruling (a), hand test of #1431 (SO414050 on BRW-IB): the Total's O/S
            # must equal the sum of the printed lines. A visible warehouse with open SO
            # but no line - no stock row for the product, or a 0-on-hand line that
            # `hide_zero_locations` dropped - is printed as `<code>: 0 (O/S: n)`.
            locations = entry.setdefault("locations", [])
            printed = {
                str(loc.get("warehouse_code")) for loc in locations if isinstance(loc, dict)
            }
            for vwid, code in visible.items():
                qty = open_so_by_warehouse.get((pid, vwid), 0)
                if qty > 0 and code and code not in printed:
                    locations.append(
                        {"warehouse_code": code, "quantity_on_hand": 0, "open_so_qty": qty}
                    )
            locations.sort(key=lambda loc: str(loc.get("warehouse_code") or ""))
    # DETAILED mode carries a per-product summary too (review round 2, S2): the chatbot's
    # "Open SO n, Available n" line reads the product TOTAL from here, never a sum over
    # the page of rows, which is short of the truth for a product held in more warehouses
    # than the page limit. `total_on_hand` is summed over every warehouse row of the
    # product (`on_hand_total_by_product`), not over `data`. Only under `include_sellable`
    # (this function), so a caller that never asked is byte-identical.
    if not body.get("stock_summary") and rows:
        on_hand_total = service.on_hand_total_by_product(list(product_ids), policy=policy)
        summary: list[dict] = []
        seen: set[str] = set()
        for serialized, row in zip(body.get("data") or [], rows):
            pid = str(getattr(row, "product_id", "") or "")
            if not pid or pid in seen:
                continue
            seen.add(pid)
            product = serialized.get("product") if isinstance(serialized.get("product"), dict) else {}
            entry = {
                "product_id": pid,
                "product_code": product.get("product_code") or serialized.get("product_code"),
                "product_name": product.get("product_name") or serialized.get("product_name"),
                "total_on_hand": on_hand_total.get(pid, 0),
            }
            # The entry itself is still emitted (a no-feed product's total_on_hand
            # is real) - only the open-SO/sellable attachment is withheld.
            if _feed_on(pid, getattr(row, "company_id", None)):
                _attach_total(entry, pid, entry["total_on_hand"])
            summary.append(entry)
        body["stock_summary"] = summary
    return JSONResponse(content=body)


@router.get("/balance", response_model=StockBalanceListResponse)
def get_stock_balance(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=5000),
    query: Optional[str] = Query(None),
    entities: Optional[list[str]] = Query(
        None,
        description=(
            "DEPRECATED - free-text entity bag. Prefer `product_ids` (canonical UUID csv/list). "
            "Free-text resolution should go through `/api/v1/system/references/resolve` first."
        ),
    ),
    product_ids: Optional[list[str]] = Query(
        None,
        description=(
            "Filter by canonical product UUIDs. Accepts repeated values, comma-separated "
            "string, or JSON array. Non-UUID input → HTTP 400."
        ),
    ),
    sort: Optional[str] = Query(None),
    dir: Optional[str] = Query(None),
    warehouse_id: Optional[str] = Query(None),
    warehouse_ids: Optional[list[str]] = Query(
        None,
        description="Filter by canonical warehouse UUIDs (repeated / csv / JSON array).",
    ),
    product_id: Optional[str] = Query(
        None,
        description="Single product UUID or product_code (kept for direct callers).",
    ),
    quantity_operator: Optional[str] = Query(None),
    quantity_value: Optional[str] = Query(None),
    status: Optional[str] = Query(None, description="Filter by status: critical, low, normal, overstock"),
    exclude_zero_system_adjustment: bool = Query(
        False,
        description=(
            "When true, hide rows whose on-hand is 0 ONLY because the latest stock-ledger "
            "movement was a SYSTEM_ADJUSTMENT (e.g. 'missing from full stock take'). A genuine "
            "0 (last movement an import/sale, or no ledger) is still returned. Used by the MCP."
        ),
    ),
    contact_id: Optional[str] = Query(
        None,
        description=(
            "The contact this question is being asked ON BEHALF OF - either the "
            "respond_contacts.id or the Respond.io id. Its presence switches the "
            "stock-visibility policy on: which locations may be named, and whether "
            "the answer comes back as rows, a per-product summary or a bare yes/no. "
            "Absent (the staff web grid) means no policy is applied at all."
        ),
    ),
    space_id: Optional[str] = Query(
        None,
        description=(
            "Respond.io workspace id. Only used to disambiguate `contact_id` when "
            "it is a Respond.io id: the same id can exist in two workspaces."
        ),
    ),
    requested_qty: Optional[int] = Query(
        None,
        description=(
            "How many units the contact asked for. Read only under an "
            "`availability` policy, where it turns 'how many do you need?' into a "
            "yes/no. A value below 1 is read as not provided (the number is parsed "
            "out of a sentence, so a 0 is a parse artefact) and the reply asks for "
            "the quantity again."
        ),
    ),
    requested_quantities: Optional[str] = Query(
        None,
        description=(
            "Chatbot stock ask v2 (ported from PR #1118, not merged). JSON object, "
            "product UUID -> the quantity asked for THAT product. Read only under an "
            "`availability` policy; per product the map wins over `requested_qty`, "
            "which fills any product it does not name. A non-object body, a "
            "non-UUID key or a non-int value is HTTP 400."
        ),
    ),
    include_sellable: bool = Query(
        False,
        description=(
            "Attach `open_so_qty` (open sales-order quantity not yet DO'd) and "
            "`sellable` (on_hand minus open_so_qty) to each detailed row / compact "
            "summary entry. Off by default - the response is unchanged for every "
            "caller that does not ask. Never attached in `availability` mode."
        ),
    ),
    current_user: dict = Depends(get_current_user_or_api_key),
    db: Session = Depends(get_db)
):
    """Get stock balance with pagination and filtering.

    The stock-visibility policy (PLAN-stock-visibility-policy) is applied ONLY when
    `contact_id` is present. Staff callers pass none, so the web grid keeps getting
    full rows for every warehouse their RBAC allows even if the DEFAULT policy row
    is later flipped to `compact` for the chatbot.

    A `contact_id` MUST arrive with its `space_id`. Two independent resolvers read
    this pair - the request-entry company scope (`_resolve_api_key_scope`) and the
    visibility policy - and company scope needs BOTH params or it stays off
    entirely. With only `contact_id` the contact would therefore be answered a
    policy-shaped slice of every company's stock. They agree or nobody is answered.
    """
    if contact_id and not space_id:
        return {
            "data": [],
            "pagination": {"total": 0, "page": page, "limit": limit},
            "empty": True,
        }
    # AC-1752 (ported from PR #1118): parsed OUTSIDE the try/except below on purpose.
    # That block's `except Exception as e: raise handle_internal_error(str(e))` treats
    # every exception alike, HTTPException included, so a 400 raised from inside it
    # comes back as a 500. The two pre-existing `parse_uuid_list` calls are hoisted out
    # with it rather than left in the trap they were already caught by - a malformed
    # product/warehouse UUID is the caller's mistake (400), never ours (500), and the
    # two params now answer a bad value exactly the way this one does.
    parsed_requested_quantities = parse_requested_quantities(requested_quantities)
    parsed_warehouse_ids = parse_uuid_list(warehouse_ids, param_name="warehouse_ids")
    parsed_product_ids = parse_uuid_list(product_ids, param_name="product_ids")
    try:
        service = StockService(db)
        result = service.list_stock(
            page=page,
            limit=limit,
            query=query,
            sort=sort,
            dir=dir,
            warehouse_id=warehouse_id,
            warehouse_ids=parsed_warehouse_ids,
            product_id=product_id,
            product_ids=parsed_product_ids,
            quantity_operator=quantity_operator,
            quantity_value=quantity_value,
            status=status,
            entities=_normalize_entities(entities),
            exclude_zero_system_adjustment=exclude_zero_system_adjustment,
            contact_id=contact_id,
            space_id=space_id,
            requested_qty=requested_qty,
            requested_quantities=parsed_requested_quantities,
        )
        # Data-miss path (§3.3): when the service attached `alternatives` /
        # `relaxed_axis` (only on an empty result), bypass the strict
        # `ListResponse` response_model - which would silently drop those keys - 
        # and emit the raw dict. `data` is always [] here, so encoding is trivial
        # and the with-data path stays byte-identical (AC-R1).
        if isinstance(result, dict) and result.get("alternatives"):
            from fastapi.encoders import jsonable_encoder
            return JSONResponse(content=jsonable_encoder(result))
        if include_sellable and isinstance(result, dict):
            return _with_sellable(service, result)
        return result
    except Exception as e:
        raise handle_internal_error(str(e))


@router.get("/dashboard", response_model=StockDashboardResponse)
def get_stock_dashboard(
    limit: int = Query(10, ge=1, le=50, description="Cap on list sizes (top warehouses, top low-stock rows)."),
    current_user: dict = Depends(get_current_user_or_api_key),
    db: Session = Depends(get_db)
):
    """Get stock dashboard statistics."""
    try:
        service = StockService(db)
        result = service.get_stock_dashboard(limit=limit)
        return result
    except Exception as e:
        raise handle_internal_error(str(e))


@router.get("/alerts")
def get_stock_alerts(
    current_user: dict = Depends(get_current_user_or_api_key),
    db: Session = Depends(get_db)
):
    """Get low stock alerts."""
    started = time.perf_counter()
    logger.info("inventory.stock_alerts start")
    try:
        service = StockService(db)
        alerts = service.get_stock_alerts()
        elapsed_ms = (time.perf_counter() - started) * 1000
        count = len(alerts) if isinstance(alerts, list) else -1
        logger.info("inventory.stock_alerts done elapsed_ms=%.1f count=%s", elapsed_ms, count)
        return alerts
    except Exception as e:
        elapsed_ms = (time.perf_counter() - started) * 1000
        logger.exception("inventory.stock_alerts failed elapsed_ms=%.1f error=%s", elapsed_ms, str(e))
        raise handle_internal_error(str(e))


@router.get("/balance/export")
def export_stock_balance(
    warehouse_id: Optional[str] = Query(None),
    product_id: Optional[str] = Query(
        None,
        description="Single product UUID or product_code (kept for direct callers).",
    ),
    product_ids: Optional[list[str]] = Query(
        None,
        description="Filter by canonical product UUIDs (csv/JSON/repeated). Preferred over `entities`.",
    ),
    entities: Optional[list[str]] = Query(
        None,
        description=(
            "DEPRECATED - free-text entity bag. Prefer `product_ids`. Free-text resolution "
            "should go through `/api/v1/system/references/resolve` first."
        ),
    ),
    quantity_operator: Optional[str] = Query(None),
    quantity_value: Optional[str] = Query(None),
    current_user: dict = Depends(require_permission_with_api_key("inventory.stock.export")),
    db: Session = Depends(get_db)
):
    """Export all stock balance data (no pagination, returns all records)."""
    started = time.perf_counter()
    logger.info(
        "inventory.stock_balance_export start warehouse_id=%s product_id=%s quantity_operator=%s quantity_value=%s",
        warehouse_id,
        product_id,
        quantity_operator,
        quantity_value,
    )
    try:
        service = StockService(db)
        stock_items = service.get_all_stock_for_export(
            warehouse_id=warehouse_id,
            product_id=product_id,
            product_ids=parse_uuid_list(product_ids, param_name="product_ids"),
            quantity_operator=quantity_operator,
            quantity_value=quantity_value,
            entities=_normalize_entities(entities),
        )
        elapsed_ms = (time.perf_counter() - started) * 1000
        logger.info("inventory.stock_balance_export done elapsed_ms=%.1f rows=%s", elapsed_ms, len(stock_items))
        return {
            "data": stock_items,
            "total": len(stock_items)
        }
    except Exception as e:
        elapsed_ms = (time.perf_counter() - started) * 1000
        logger.exception("inventory.stock_balance_export failed elapsed_ms=%.1f error=%s", elapsed_ms, str(e))
        raise handle_internal_error(str(e))


@router.delete("/bulk")
async def bulk_delete_stock(
    body: BulkDeleteStockRequest = Body(...),
    current_user: dict = Depends(require_permission("inventory.stock.delete")),
    db: Session = Depends(get_db)
):
    """Bulk delete stock records by id. Requires inventory.stock.delete permission."""
    try:
        service = StockService(db)
        result = service.bulk_delete_stock(body.ids)
        return result
    except Exception as e:
        raise handle_internal_error(str(e))


@router.get("/{product_id}/{warehouse_id}/ledger", response_model=ListResponse[StockLedgerResponse])
def get_stock_ledger_by_stock(
    product_id: str = Path(
        ...,
        description="Product UUID or product_code (e.g. SKU).",
    ),
    warehouse_id: str = Path(..., description="Warehouse id (UUID)."),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=5000),
    current_user: dict = Depends(get_current_user_or_api_key),
    db: Session = Depends(get_db)
):
    """Get stock ledger entries for a specific product and warehouse."""
    try:
        service = StockService(db)
        result = service.get_stock_ledger_by_stock(
            product_id=product_id,
            warehouse_id=warehouse_id,
            page=page,
            limit=limit
        )
        return result
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post(
    "/bulk-import",
    status_code=status.HTTP_202_ACCEPTED,
    responses={200: {"description": "Validation only (validate_only=true)", "model": ValidateImportResponse}},
)
async def bulk_import_stock(
    import_data: BulkImportStockRequest,
    current_user: dict = Depends(require_permission("inventory.stock.import")),
    db: Session = Depends(get_db)
):
    """Bulk import stock from Excel data (queued). If validate_only=true, validate only and return errors/warnings (no job)."""
    try:
        if getattr(import_data, "validate_only", False):
            service = StockService(db)
            result = service.bulk_import_stock(
                import_data.stock, current_user["id"], validate_only=True
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
        from app.tasks.import_tasks import process_stock_import

        # Create job record
        job_service = JobService(db)
        job = job_service.create_job(
            job_type='stock_import',
            user_id=current_user["id"],
            metadata={'total_rows': len(import_data.stock)}
        )
        setattr(job, "total_rows", len(import_data.stock))
        db.commit()
        
        # Enqueue job
        rq_job = enqueue_job(
            process_stock_import,
            str(job.id),  # Pass DB job ID (UUID)
            import_data.stock,
            current_user["id"],
            queue_name='imports',
            job_timeout=3600,
            job_id=str(job.job_id),  # pre-assign RQ id = DB job_id; see update_job_with_rq_id
        )
        
        # Update job with RQ job ID
        job_service.update_job_with_rq_id(job, rq_job.id)
        
        return {
            'job_id': rq_job.id,
            'status': 'queued',
            'message': 'Import job queued successfully'
        }
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))
