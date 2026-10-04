"""Orders API routes."""
import calendar
import re
from datetime import datetime, time

from fastapi import APIRouter, Depends, Query, HTTPException, status, UploadFile, File, Body, Request
from pydantic import BaseModel
from fastapi.responses import JSONResponse
from sqlalchemy import func
from sqlalchemy.orm import Session
from typing import Optional
from app.database import get_db
from app.dependencies import (
    get_current_user,
    get_current_user_or_api_key,
    require_permission,
    require_permission_with_api_key,
)
from app.services.order_service import (
    OrderService,
    has_product_filter,
    keep_brand_lines,
    narrow_product_ids_by_brand,
    stamp_so_outstanding_rows,
)
from app.services.uuid_list_param import parse_uuid_list
from app.config import settings as app_settings


# External/AI callers capped at 20 rows: even with slimmed MCP rows (no UUIDs /
# pricing / customer block), 100 DOs with full lines is too much for the agent
# to reason over. Tried 100, reverted.
_EXTERNAL_ORDERS_LIST_LIMIT_CAP = 20
# Aggregation + by-product endpoints share the same tight cap.
_EXTERNAL_ORDERS_AGG_LIMIT_CAP = 20


def _request_has_valid_external_api_key(request: Optional[Request]) -> bool:
    """True when X-API-Key header matches configured external API key."""
    if request is None:
        return False
    key = request.headers.get("X-API-Key") or request.headers.get("x-api-key")
    valid = getattr(app_settings, "external_api_key", None)
    if not key or not valid:
        return False
    return key.strip() == str(valid).strip()


# Highest limit an external/agent call may receive (matches the route's le=1000).
# When a date filter scopes the result, we lift the tight 20-cap to this so the
# caller gets the full set for that window instead of a truncated top-20.
_EXTERNAL_ORDERS_DATE_SCOPED_LIMIT = 1000


def _has_orders_date_filter(*raw_values: Optional[str]) -> bool:
    """True when any order/delivery date-range filter string was supplied."""
    return any(v is not None and str(v).strip() != "" for v in raw_values)


def _external_orders_limit(
    request: Optional[Request], limit: int, *, cap: int, date_scoped: bool
) -> int:
    """Resolve the effective limit for external/agent order calls.

    date filter present  → lift to the date-scoped max (return all in the window).
    no date filter        → keep the tight top-N cap.
    Non-external callers  → unchanged.
    """
    if not _request_has_valid_external_api_key(request):
        return limit
    if date_scoped:
        return _EXTERNAL_ORDERS_DATE_SCOPED_LIMIT
    return min(limit, cap)


# --------------------------------------------------------------------------- #
# Flexible date parsing for query parameters
# --------------------------------------------------------------------------- #
# n8n / LLM tool callers commonly send dates in any of these forms:
# - ISO datetime   2026-02-01T00:00:00
# - ISO date       2026-02-01
# - DD/MM/YYYY     01/02/2026   (Malaysia/Singapore default; preferred)
# - DD-MM-YYYY     01-02-2026
# - YYYY/MM/DD     2026/02/01
# - Month-only     "2026-02", "02/2026", "February 2026", "Feb 2026"
# We accept all of them at the API layer so business logic stays in MCP/CRM.
_MONTH_NAMES = {name.lower(): idx for idx, name in enumerate(calendar.month_name) if name}
_MONTH_NAMES.update({name.lower(): idx for idx, name in enumerate(calendar.month_abbr) if name})

_DATE_FORMATS = (
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%d.%m.%Y",
    "%Y/%m/%d",
)


def _end_of_day(dt: datetime) -> datetime:
    """Return the same day at 23:59:59.999999 - used for inclusive `_to` filters."""
    return datetime.combine(dt.date(), time(23, 59, 59, 999999))


def _last_day_of_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def _safe_construct(year: int, month: int, day: int) -> datetime:
    """Build a datetime, clamping `day` to the month's last valid day if necessary.

    LLM agents commonly send "29/02/2026" for "end of February 2026" without checking
    leap years. Clamping (rather than erroring) keeps the user flow smooth.
    """
    last = _last_day_of_month(year, month)
    return datetime(year, month, min(max(day, 1), last))


def _is_day_out_of_range(exc: ValueError) -> bool:
    """Detect 'day out of range' across CPython versions.

    CPython <3.12 message:  "day is out of range for month"
    CPython 3.12+ message:  "day NN must be in range 1..NN for month M in year YYYY"
    """
    msg = str(exc).lower()
    return "out of range" in msg or "must be in range" in msg


def _try_strptime(value: str, fmt: str) -> Optional[datetime]:
    try:
        return datetime.strptime(value, fmt)
    except ValueError as exc:
        # Day-out-of-range is the common leap-year case - try clamping for D/M/Y formats.
        if not _is_day_out_of_range(exc):
            return None
        parts: list[str] = []
        ymd: Optional[tuple[int, int, int]] = None  # (year, month, day)
        if fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y"):
            sep = fmt[2]
            parts = value.split(sep)
            if len(parts) == 3:
                try:
                    ymd = (int(parts[2]), int(parts[1]), int(parts[0]))
                except ValueError:
                    return None
        elif fmt == "%Y/%m/%d":
            parts = value.split("/")
            if len(parts) == 3:
                try:
                    ymd = (int(parts[0]), int(parts[1]), int(parts[2]))
                except ValueError:
                    return None
        elif fmt == "%Y-%m-%d":
            parts = value.split("-")
            if len(parts) == 3:
                try:
                    ymd = (int(parts[0]), int(parts[1]), int(parts[2]))
                except ValueError:
                    return None
        if ymd is None:
            return None
        year, month, day = ymd
        if 1 <= month <= 12 and year >= 1900:
            return _safe_construct(year, month, day)
        return None


def _normalize_entities(raw: Optional[list[str]]) -> Optional[list[str]]:
    """Flatten an `entities` query param into a clean list[str].

    Accepts None, a list of strings (repeated query param), or a single-element list
    holding a JSON array or comma-separated string - all of which n8n / curl callers
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
        # Strip a JSON-array wrapper so '["A","B"]' splits as ["A","B"].
        if s.startswith("[") and s.endswith("]"):
            try:
                import json as _json

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


def _parse_flex_date(value: Optional[str], *, end_of_day: bool = False) -> Optional[datetime]:
    """Parse a date string in any of the supported formats. Returns None if value is empty.

    Raises HTTPException(422) with a clear message if the format is unrecognised, so the
    LLM/agent gets actionable feedback instead of an opaque pydantic error.
    """
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None

    for fmt in _DATE_FORMATS:
        dt = _try_strptime(s, fmt)
        if dt is not None:
            return _end_of_day(dt) if end_of_day else dt

    # Try ISO 8601 (handles trailing Z, timezone offsets).
    try:
        iso_clean = s.rstrip("Z")
        dt = datetime.fromisoformat(iso_clean)
        if dt.tzinfo:
            dt = dt.replace(tzinfo=None)
        return _end_of_day(dt) if end_of_day else dt
    except ValueError:
        pass

    # Month-only forms.
    # "YYYY-MM" or "YYYY/MM"
    m = re.fullmatch(r"(\d{4})[-/](\d{1,2})", s)
    if m:
        year, month = int(m.group(1)), int(m.group(2))
        if 1 <= month <= 12:
            day = _last_day_of_month(year, month) if end_of_day else 1
            dt = datetime(year, month, day)
            return _end_of_day(dt) if end_of_day else dt

    # "MM/YYYY" or "MM-YYYY"
    m = re.fullmatch(r"(\d{1,2})[-/](\d{4})", s)
    if m:
        month, year = int(m.group(1)), int(m.group(2))
        if 1 <= month <= 12:
            day = _last_day_of_month(year, month) if end_of_day else 1
            dt = datetime(year, month, day)
            return _end_of_day(dt) if end_of_day else dt

    # "Month YYYY" / "Mon YYYY" - e.g. "February 2026", "Feb 2026"
    m = re.fullmatch(r"([A-Za-z]+)\s+(\d{4})", s)
    if m:
        month = _MONTH_NAMES.get(m.group(1).lower())
        if month:
            year = int(m.group(2))
            day = _last_day_of_month(year, month) if end_of_day else 1
            dt = datetime(year, month, day)
            return _end_of_day(dt) if end_of_day else dt

    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail=(
            f"Unrecognised date '{value}'. Accepted formats: YYYY-MM-DD, DD/MM/YYYY, "
            "DD-MM-YYYY, YYYY/MM/DD, ISO datetime, 'YYYY-MM', 'MM/YYYY', or 'Month YYYY' "
            "(e.g. 'February 2026')."
        ),
    )
from app.schemas.order import (
    OrderCreate,
    OrderUpdate,
    OrderResponse,
    OrderSimpleRef,
    OrderLineCreate,
    OrderLineUpdate,
    OrderLineResponse,
    BulkImportRequest,
    BulkImportResponse,
    BulkDeleteOrdersRequest,
    BulkDeleteOrderLinesRequest,
)
from app.schemas.common import ListResponse, MAX_PAGE_LIMIT, ValidateImportResponse
from app.schemas.order_management import (
    OutstandingReportResponse,
    SalesReportResponse,
    TopSellingResponse,
)
from app.services.error_handler import handle_internal_error
from app.api.v1.order_management._contact_scope import enforce_customer_scope

router = APIRouter()


@router.get("/", response_model=ListResponse[OrderResponse])
async def get_orders(
    request: Request,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=1000),
    query: Optional[str] = Query(None),
    entities: Optional[list[str]] = Query(
        None,
        description=(
            "DEPRECATED - free-text entity bag. Prefer typed UUID params "
            "(`order_ids` / `customer_ids` / `product_ids` / `transporter_ids`). "
            "Resolve free-text refs via /api/v1/system/references/resolve first."
        ),
    ),
    order_ids: Optional[list[str]] = Query(
        None,
        description="Filter by order UUIDs (csv/JSON/repeated).",
    ),
    customer_ids: Optional[list[str]] = Query(
        None,
        description=(
            "Filter by customer UUIDs. Matches Order.customer_id IN (...) and, "
            "for legacy rows without FK, Order.debtor_name = customers.customer_name."
        ),
    ),
    product_ids: Optional[list[str]] = Query(
        None,
        description="Filter to orders containing any of these product UUIDs (via order lines).",
    ),
    brand_ids: Optional[list[str]] = Query(
        None,
        description=(
            "Canonical brand UUIDs (csv/JSON/repeated) - narrows to orders containing a "
            "product of this brand (Product.brand_id via order lines). Intersects with "
            "product_ids when both are given (#1262 slice 9, AC-S9-6)."
        ),
    ),
    transporter_ids: Optional[list[str]] = Query(
        None,
        description=(
            "Filter by transporter UUIDs. Matches Order.transporter_id IN (...) and, "
            "for legacy rows without FK, Order.transporter = transporters.name."
        ),
    ),
    customer_query: Optional[str] = Query(
        None,
        description="Partial customer/debtor filter (matches debtor_name/debtor_code/customer name/code, case-insensitive).",
    ),
    product_query: Optional[str] = Query(
        None,
        description="Partial product filter (matches product code/name/description across order lines, case-insensitive).",
    ),
    transporter_query: Optional[str] = Query(
        None,
        description="Partial transporter filter (matches Order.transporter, case-insensitive).",
    ),
    warehouse_codes: Optional[list[str]] = Query(
        None,
        description=(
            "Exact warehouse codes (csv/JSON/repeated), case-insensitive. An order "
            "qualifies when ANY of its lines sits at one of these warehouses. A code "
            "that matches no warehouse filters to nothing (AC-1121)."
        ),
    ),
    customer_id: Optional[str] = Query(None),
    order_status_id: Optional[str] = Query(None),
    order_status: Optional[str] = Query(
        None,
        description=(
            "Delivery bucket filter: 'outstanding' = orders NOT yet delivered, "
            "'delivered' = orders already delivered, 'so_outstanding' = open sales-order "
            "LINES not yet turned into a DO at all (qty_ordered - qty_delivered > 0 over "
            "sales_order_lines, line_status='open' - a DIFFERENT table from the other two "
            "buckets, so rows carry so_number/product/outstanding_qty/order_date/customer/ "
            "requested_delivery_date instead of the usual order fields), omit/null = no "
            "filter (all, over `orders`). Delivered means the order's status is "
            "delivered/completed AND its actual_delivery_date is set; everything else (New "
            "Order, Processing, In Transit, Cancelled, or a delivery date under a "
            "non-delivered status) is outstanding. Use for 'outstanding/pending/undelivered "
            "orders', 'belum hantar', 'not delivered yet'; use so_outstanding for 'SO "
            "outstanding', 'ordered but no DO', 'belum DO'. AND'd with the other filters."
        ),
    ),
    include_summary: bool = Query(
        False,
        description=(
            "true = also return `summary`: filter-wide measures (order/delivered/pending "
            "counts, customers, delivered date span, per-product delivered/pending "
            "quantity when product_ids is given). Send it when the user asks HOW MANY / "
            "how much was taken; omit for a plain DO list."
        ),
    ),
    include_pipeline: bool = Query(
        False,
        description=(
            "true = also fold so_outstanding_qty/so_outstanding_count (open SO lines for "
            "the same customer_ids/product_ids scope) into `summary`, so a render "
            "presenter can show the three-line SO outstanding / DO open / delivered "
            "pipeline (AC-905b). Opt-in and independent of `include_summary` on purpose "
            "(fix, 7 Sep 2026): the CRM's own chatbot lane sets it alongside "
            "`include_summary` on a quantity ask; a caller that only asks for "
            "`include_summary` (every pre-existing caller, n8n included) gets exactly "
            "the summary shape it got before this field existed."
        ),
    ),
    group_by: Optional[str] = Query(
        None,
        description=(
            "Group rows into headed sections. One of: customer, transporter, date, "
            "product. Applies to every bucket (outstanding/delivered/so_outstanding/all). "
            "An unrecognised value returns 422 naming the allowed axes."
        ),
    ),
    has_order_lines: Optional[str] = Query(
        None,
        description="Filter by lines: 'yes' = at least one line, 'no' = no lines, omit = all",
    ),
    has_actual_delivery_date: Optional[str] = Query(
        None,
        description="Filter by actual delivery date: 'yes' = has date, 'no' = missing date, omit = all",
    ),
    order_date_from: Optional[str] = Query(
        None,
        description=(
            "Filter by order date from (inclusive). Accepts YYYY-MM-DD, DD/MM/YYYY, "
            "DD-MM-YYYY, YYYY/MM/DD, ISO datetime, 'YYYY-MM', 'MM/YYYY', or 'Month YYYY'. "
            "Only use when the user EXPLICITLY mentions the order/placement date (verbs: "
            "'placed', 'created', 'raised', 'opened', 'booked', or literal 'order date'). "
            "For bare time windows ('today', 'this week', 'February 2026') and DO discovery, "
            "use actual_delivery_date_from instead."
        ),
    ),
    order_date_to: Optional[str] = Query(
        None,
        description="Filter by order date to (inclusive). Same flexible formats as order_date_from.",
    ),
    actual_delivery_date_from: Optional[str] = Query(
        None,
        description=(
            "Filter by actual delivery date from (inclusive). Same flexible formats as "
            "order_date_from. DEFAULT date param for DO discovery and bare 'orders in [today/"
            "this week/month/period]' questions. Use for delivery verbs ('delivered', 'received', "
            "'for delivery', 'pending delivery', 'arrived', 'delivery date') and ambiguous time "
            "windows. Only fall back to order_date_from when the user EXPLICITLY says the "
            "order/placement date."
        ),
    ),
    actual_delivery_date_to: Optional[str] = Query(
        None,
        description="Filter by actual delivery date to (inclusive). Same flexible formats.",
    ),
    sort: Optional[str] = Query("created_at"),
    dir: Optional[str] = Query("asc"),
    contact_id: Optional[str] = Query(
        None,
        description=(
            "Respond.io contact id. Both-or-neither with space_id (one alone is 422). A "
            "contact linked to customers and holding no office access type is scoped to "
            "them: another customer's id is 403 `customer_not_permitted`, and no customer "
            "argument means its own customers."
        ),
    ),
    space_id: Optional[str] = Query(
        None, description="Respond.io workspace id, required together with contact_id.",
    ),
    current_user: dict = Depends(get_current_user_or_api_key),
    db: Session = Depends(get_db)
):
    """Get orders with pagination, filtering, and sorting.

    External API-key callers (e.g. AI agent / MCP) are capped at limit=20 to keep
    tool responses small enough to reason over.
    """
    from app.services.error_handler import AppException
    from app.services.order_service import (
        ORDER_GROUP_BY_AXES,
        group_rows,
        so_outstanding_rows,
        so_outstanding_summary,
    )

    if group_by is not None and group_by not in ORDER_GROUP_BY_AXES:
        raise AppException(
            422,
            f"Unknown group_by value '{group_by}'",
            detail=f"allowed: {', '.join(sorted(ORDER_GROUP_BY_AXES))}",
            code="invalid_group_by",
        )

    try:
        _resolved_customer_ids = parse_uuid_list(customer_ids, param_name="customer_ids")
        # PLAN-chatbot-customer-scope-29sep.md D5: a scoped contact's customers. The legacy
        # singular `customer_id` counts as a requested customer too.
        _scoped_ids = enforce_customer_scope(
            db,
            contact_id=contact_id,
            space_id=space_id,
            customer_ids=[*(_resolved_customer_ids or []), *([customer_id] if customer_id else [])],
            customer_query=customer_query,
        )
        if _scoped_ids is not None:
            _resolved_customer_ids = _scoped_ids
        _resolved_brand_ids = parse_uuid_list(brand_ids, param_name="brand_ids")
        # Review round (26 Sep 2026): the SAME cap `get_outstanding_report` already
        # applies to its own `brand_ids` - an unbounded IN (...) from an external
        # caller is an easy way to make the brand-narrowing query slow.
        if _resolved_brand_ids is not None and len(_resolved_brand_ids) > 50:
            raise AppException(
                422,
                "Too many values for 'brand_ids' (max 50)",
                detail=f"got {len(_resolved_brand_ids)}",
                code="too_many_values",
            )
        _resolved_product_ids = narrow_product_ids_by_brand(
            db,
            parse_uuid_list(product_ids, param_name="product_ids"),
            _resolved_brand_ids,
        )

        # A3 (AC-905): a DIFFERENT table (sales_order_lines, not orders), so a
        # dedicated path rather than shoehorning it into `service.list_orders` -
        # the row shape (SO number/product/outstanding qty/order date/customer/
        # requested delivery date) has nothing in common with `OrderResponse`.
        # Hoisted above the so_outstanding arm: BOTH buckets need it for the cap.
        _date_scoped = _has_orders_date_filter(
            order_date_from, order_date_to, actual_delivery_date_from, actual_delivery_date_to
        )
        if order_status == "so_outstanding":
            from fastapi.encoders import jsonable_encoder

            # The SAME external cap the DO buckets take, applied BEFORE the read
            # (review, should-fix 7). This arm returned up to 500 rows to an
            # external/AI caller that every other bucket hard-caps at 20 - the cap is
            # what stops one WhatsApp turn pulling a five-hundred-row page through the
            # MCP and into a message, and a new bucket is exactly where it gets
            # forgotten. `_date_scoped` is computed once, above both arms now, because
            # the cap relaxes for a date-narrowed read and this bucket takes a date
            # window like the others.
            _so_limit = _external_orders_limit(
                request, limit, cap=_EXTERNAL_ORDERS_LIST_LIMIT_CAP, date_scoped=_date_scoped
            )
            rows = so_outstanding_rows(
                db,
                customer_ids=_resolved_customer_ids,
                product_ids=_resolved_product_ids,
                limit=min(_so_limit, 500),
            )
            payload: dict = {
                "data": rows,
                # The MCP presenter has no other way to tell a bucket whose rows
                # carry `so_number` instead of `order_number` apart from a plain
                # "no rows matched" answer - it never sees the query params, only
                # this JSON. Echoed back, not derived from the rows, so an empty
                # result still renders as an SO-outstanding miss, not a DO miss.
                "order_status": "so_outstanding",
                "pagination": {"total": len(rows), "page": 1, "limit": len(rows)},
                "empty": not rows,
            }
            if group_by:
                payload["groups"] = group_rows(rows, group_by=group_by)
            if include_summary:
                payload["summary"] = {
                    "scope": "filter",
                    "row_count": len(rows),
                    **so_outstanding_summary(
                        db, customer_ids=_resolved_customer_ids, product_ids=_resolved_product_ids
                    ),
                }
            return JSONResponse(content=jsonable_encoder(payload))

        limit = _external_orders_limit(
            request, limit, cap=_EXTERNAL_ORDERS_LIST_LIMIT_CAP, date_scoped=_date_scoped
        )
        service = OrderService(db)
        result = service.list_orders(
            page=page,
            limit=limit,
            query=query,
            entities=_normalize_entities(entities),
            order_ids=parse_uuid_list(order_ids, param_name="order_ids"),
            customer_ids=_resolved_customer_ids,
            product_ids=_resolved_product_ids,
            transporter_ids=parse_uuid_list(transporter_ids, param_name="transporter_ids"),
            warehouse_codes=_normalize_entities(warehouse_codes),
            customer_query=None if _scoped_ids is not None else customer_query,
            product_query=product_query,
            transporter_query=transporter_query,
            customer_id=customer_id,
            order_status_id=order_status_id,
            order_status=order_status,
            include_summary=include_summary,
            has_order_lines=has_order_lines,
            has_actual_delivery_date=has_actual_delivery_date,
            order_date_from=_parse_flex_date(order_date_from),
            order_date_to=_parse_flex_date(order_date_to, end_of_day=True),
            actual_delivery_date_from=_parse_flex_date(actual_delivery_date_from),
            actual_delivery_date_to=_parse_flex_date(actual_delivery_date_to, end_of_day=True),
            sort_field=sort or "created_at",
            sort_dir=dir or "asc"
        )
        if _scoped_ids is not None and isinstance(result, dict):
            # A scoped contact never sees how a free-text `entities` word resolved: the
            # echo would say whether another customer's name exists (a name oracle).
            result.pop("resolved_entities", None)
        # #1262 fix lane round 6: a brand ask lists only that brand's lines inside
        # each document it keeps. No brand, no change.
        if _resolved_brand_ids and isinstance(result, dict) and result.get("data"):
            result = {**result, "data": keep_brand_lines(db, result["data"], _resolved_product_ids)}
        # Date-axis relaxation (§3.4): when the service attached `alternatives` /
        # `relaxed_axis` (only on an empty result), bypass the strict
        # `ListResponse` response_model - which would silently drop those keys -
        # and emit the raw dict. `data` is always [] here so encoding is trivial,
        # and the with-data path stays byte-identical (AC-R1).
        if isinstance(result, dict) and result.get("alternatives"):
            from fastapi.encoders import jsonable_encoder
            return JSONResponse(content=jsonable_encoder(result))

        # A3 (AC-905b, AC-906): group_by and/or the SO-outstanding leg of the
        # three-line pipeline. Both bypass `response_model` (neither key exists
        # on `ListResponse[OrderResponse]`), same reason as the alternatives
        # path above - and ONLY when actually needed, so a plain call with
        # neither stays on the fast, byte-identical `return result` below.
        if isinstance(result, dict) and (group_by or include_summary):
            from fastapi.encoders import jsonable_encoder

            body = jsonable_encoder(result)
            if group_by:
                # DO rows have no single product (an order carries many lines), so
                # `product` groups everything under "Not specified" - the same
                # documented fallback `group_rows` uses for any axis a row lacks.
                axis_source = {
                    "customer": "debtor_name",
                    "transporter": "transporter",
                    "date": "actual_delivery_date",
                }.get(group_by)
                groups = group_rows(
                    body.get("data") or [],
                    group_by=group_by,
                    value_fn=(lambda o, _k=axis_source: o.get(_k) if _k else None),
                )
                body["groups"] = groups
            # `include_pipeline` (fix, 7 Sep 2026): gated SEPARATELY from
            # `include_summary` above - folding `so_outstanding_qty` in unconditionally
            # on every `include_summary=true` call put it in front of every existing
            # caller that never asked for it, n8n's quantity-ask workflow included, and
            # its presence alone is what `sorento_crm_mcp/presenters.py`'s
            # `_pipeline_summary_items` renders on. A caller that wants the three-line
            # pipeline now has to ask for it by name.
            if (
                include_summary
                and include_pipeline
                and isinstance(body.get("summary"), dict)
            ):
                body["summary"].update(
                    so_outstanding_summary(
                        db, customer_ids=_resolved_customer_ids, product_ids=_resolved_product_ids
                    )
                )
                # D8 (owner console pass, 8 Sep 2026): the per-row SO block the by-product
                # route stamps reaches THIS route's products[] / groups[] too - the same
                # five figures, the same three-state rule, the same scope. The top-level
                # leg above stays for callers that read it; the presenter no longer
                # renders a three-line block off it.
                stamp_so_outstanding_rows(
                    db,
                    body["summary"],
                    customer_ids=_resolved_customer_ids,
                    product_ids=_resolved_product_ids,
                )
            return JSONResponse(content=body)
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.get("/debtors")
async def list_distinct_debtors(
    request: Request,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=MAX_PAGE_LIMIT),
    query: Optional[str] = Query(
        None,
        description="Free-text partial match on debtor_name or debtor_code (case-insensitive).",
    ),
    customer_ids: Optional[list[str]] = Query(
        None,
        description="Filter source orders by canonical customer UUIDs (Order.customer_id) before aggregation. Repeated / csv / JSON array.",
    ),
    sort: str = Query("debtor_name", description="One of: debtor_name, debtor_code, order_count."),
    dir: str = Query("asc", description="asc | desc."),
    contact_id: Optional[str] = Query(
        None,
        description=(
            "Respond.io contact id. Both-or-neither with space_id (one alone is 422). A "
            "contact linked to customers and holding no office access type is scoped to "
            "them: another customer's id is 403 `customer_not_permitted`, and no customer "
            "argument means its own customers."
        ),
    ),
    space_id: Optional[str] = Query(
        None, description="Respond.io workspace id, required together with contact_id.",
    ),
    current_user: dict = Depends(get_current_user_or_api_key),
    db: Session = Depends(get_db),
):
    """Distinct customers/debtors aggregated from orders.

    The `customers` table is not used by the business - the real customer identity
    lives in `orders.debtor_name` / `debtor_code`. This endpoint deduplicates by
    debtor_name (case-insensitive trim) and returns each debtor with its code and
    total order count, so AI tools can search 'who are our customers' without
    relying on the underused customers master table.

    External AI/MCP callers are capped at limit=20 to keep responses small enough
    to reason over.
    """
    from app.models.order import Order as _Order

    if _request_has_valid_external_api_key(request) and limit > _EXTERNAL_ORDERS_AGG_LIMIT_CAP:
        limit = _EXTERNAL_ORDERS_AGG_LIMIT_CAP

    # Group by lower(trim(debtor_name)) so 'V Bath  ' and 'v bath' collapse to one row.
    name_key = func.lower(func.trim(_Order.debtor_name))
    base = (
        db.query(
            func.min(_Order.debtor_name).label("debtor_name"),
            func.min(_Order.debtor_code).label("debtor_code"),
            func.count(_Order.id).label("order_count"),
        )
        .filter(_Order.debtor_name.isnot(None))
        .filter(func.trim(_Order.debtor_name) != "")
        .group_by(name_key)
    )

    parsed_customer_ids = parse_uuid_list(customer_ids, param_name="customer_ids")
    scoped_ids = enforce_customer_scope(
        db, contact_id=contact_id, space_id=space_id, customer_ids=parsed_customer_ids, customer_query=None
    )
    if scoped_ids is not None:
        parsed_customer_ids = scoped_ids
    if parsed_customer_ids is not None:
        base = base.filter(_Order.customer_id.in_(parsed_customer_ids))

    q = (query or "").strip()
    if q:
        like = f"%{q.lower()}%"
        base = base.having(
            (func.lower(func.min(_Order.debtor_name)).like(like))
            | (func.lower(func.coalesce(func.min(_Order.debtor_code), "")).like(like))
        )

    sort_field = (sort or "debtor_name").strip().lower()
    desc = (dir or "asc").strip().lower() == "desc"
    sort_expr_map = {
        "debtor_name": func.min(_Order.debtor_name),
        "debtor_code": func.min(_Order.debtor_code),
        "order_count": func.count(_Order.id),
    }
    sort_expr = sort_expr_map.get(sort_field, func.min(_Order.debtor_name))
    base = base.order_by(sort_expr.desc().nulls_last() if desc else sort_expr.asc().nulls_last())

    # Count distinct debtor groups by wrapping the grouped query in a subquery.
    total = db.query(func.count()).select_from(base.order_by(None).subquery()).scalar() or 0
    rows = base.offset((page - 1) * limit).limit(limit).all()
    data = [
        {
            "debtor_name": r.debtor_name,
            "debtor_code": r.debtor_code,
            "order_count": int(r.order_count or 0),
        }
        for r in rows
    ]
    return {
        "data": data,
        "pagination": {"total": int(total), "page": page, "limit": limit},
        "empty": int(total) == 0,
    }


@router.get("/by-product", response_model=ListResponse[OrderSimpleRef])
async def get_orders_by_product(
    request: Request,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=1000),
    query: Optional[str] = Query(None, description="Matches product code, name, description, order number, or debtor name"),
    entities: Optional[list[str]] = Query(
        None,
        description=(
            "DEPRECATED - free-text entity bag. Prefer typed UUID params "
            "(`product_ids` / `customer_ids` / `transporter_ids`)."
        ),
    ),
    product_ids: Optional[list[str]] = Query(
        None,
        description="Canonical product UUIDs (csv/JSON/repeated). At least one required for this endpoint.",
    ),
    brand_ids: Optional[list[str]] = Query(
        None,
        description=(
            "Canonical brand UUIDs (csv/JSON/repeated) - narrows to that brand's own "
            "products (Product.brand_id). Counts as the product narrower this endpoint "
            "requires (#1262 slice 9, AC-S9-6); intersects with product_ids when both "
            "are given."
        ),
    ),
    customer_ids: Optional[list[str]] = Query(
        None,
        description="Optional customer UUIDs. OR-fallback to debtor_name for legacy rows without FK.",
    ),
    transporter_ids: Optional[list[str]] = Query(
        None,
        description="Optional transporter UUIDs. OR-fallback to Order.transporter text for legacy rows.",
    ),
    customer_query: Optional[str] = Query(
        None,
        description="Partial customer/debtor filter for complaint DO discovery (debtor_name/debtor_code/customer name/code).",
    ),
    product_query: Optional[str] = Query(
        None,
        description="Partial product filter (product code/name/description). Useful when product_id is not exact.",
    ),
    product_id: Optional[list[str]] = Query(
        None,
        description=(
            "Legacy - one or more product_codes/SKUs (still resolves fuzzy). Prefer `product_ids`."
        ),
    ),
    warehouse_codes: Optional[list[str]] = Query(
        None,
        description=(
            "Exact warehouse codes (csv/JSON/repeated), case-insensitive. Filters to the "
            "SAME line that matched the product narrower. A code that matches no "
            "warehouse filters to nothing (AC-1121)."
        ),
    ),
    has_actual_delivery_date: Optional[str] = Query(
        None,
        description="Filter by actual delivery date: 'yes' = has date, 'no' = missing date, omit = all",
    ),
    order_status: Optional[str] = Query(
        None,
        description=(
            "Delivery bucket filter, same semantics as the orders list: 'outstanding' = "
            "orders NOT yet delivered, 'delivered' = status delivered/completed AND "
            "actual_delivery_date set, omit/null = all."
        ),
    ),
    include_summary: bool = Query(
        False,
        description=(
            "true = also return `summary`: filter-wide measures (order/delivered/pending "
            "counts, customers, delivered date span, per-product delivered/pending quantity). "
            "Send it when the user asks HOW MANY / how much was taken; omit for a plain DO list."
        ),
    ),
    include_pipeline: bool = Query(
        False,
        description=(
            "true = also fold so_outstanding_qty (open SO lines for the same "
            "customer_ids/product_ids scope) into EVERY `summary.products` and "
            "`summary.groups` row, so a render presenter can show the SO outstanding "
            "leg next to each customer x product's DO figures. Opt-in and independent of "
            "`include_summary` on purpose: the CRM's own chatbot lane sets it alongside "
            "`include_summary` on a quantity ask; a caller that only asks for "
            "`include_summary` (every pre-existing caller, n8n included) gets exactly "
            "the summary shape it got before this field existed."
        ),
    ),
    order_date_from: Optional[str] = Query(
        None,
        description=(
            "Filter by order date from (inclusive). Accepts YYYY-MM-DD, DD/MM/YYYY, "
            "DD-MM-YYYY, YYYY/MM/DD, ISO datetime, 'YYYY-MM', 'MM/YYYY', or 'Month YYYY'. "
            "Only use when the user EXPLICITLY mentions the order/placement date "
            "(verbs: 'placed', 'created', 'raised', 'opened', 'booked', or literal 'order date'). "
            "For DO discovery and bare time windows, use actual_delivery_date_from instead."
        ),
    ),
    order_date_to: Optional[str] = Query(
        None,
        description="Filter by order date to (inclusive). Same flexible formats as order_date_from.",
    ),
    actual_delivery_date_from: Optional[str] = Query(
        None,
        description=(
            "Filter by actual delivery date from (inclusive). Same flexible formats. "
            "DEFAULT date param for DO discovery and bare 'orders in [today/this week/month/"
            "period]' questions. Use for delivery verbs ('delivered', 'received', 'for delivery', "
            "'pending delivery', 'arrived', 'delivery date') and ambiguous time windows. Only "
            "fall back to order_date_from when the user EXPLICITLY says order/placement date."
        ),
    ),
    actual_delivery_date_to: Optional[str] = Query(
        None,
        description="Filter by actual delivery date to (inclusive). Same flexible formats.",
    ),
    sort: Optional[str] = Query("order_date"),
    dir: Optional[str] = Query("desc"),
    contact_id: Optional[str] = Query(
        None,
        description=(
            "Respond.io contact id. Both-or-neither with space_id (one alone is 422). A "
            "contact linked to customers and holding no office access type is scoped to "
            "them: another customer's id is 403 `customer_not_permitted`, and no customer "
            "argument means its own customers."
        ),
    ),
    space_id: Optional[str] = Query(
        None, description="Respond.io workspace id, required together with contact_id.",
    ),
    current_user: dict = Depends(get_current_user_or_api_key),
    db: Session = Depends(get_db)
):
    """Get distinct orders matched by product search.

    External API-key callers (e.g. AI agent / MCP) are capped at limit=20 to keep
    tool responses small enough to reason over.
    """
    try:
        _date_scoped = _has_orders_date_filter(
            order_date_from, order_date_to, actual_delivery_date_from, actual_delivery_date_to
        )
        limit = _external_orders_limit(
            request, limit, cap=_EXTERNAL_ORDERS_AGG_LIMIT_CAP, date_scoped=_date_scoped
        )
        norm_entities = _normalize_entities(entities)
        requested_customer_ids = parse_uuid_list(customer_ids, param_name="customer_ids")
        scoped_customer_ids = enforce_customer_scope(
            db,
            contact_id=contact_id,
            space_id=space_id,
            customer_ids=requested_customer_ids,
            customer_query=customer_query,
        )
        is_scoped = scoped_customer_ids is not None
        if scoped_customer_ids is None:
            scoped_customer_ids = requested_customer_ids
        resolved_brand_ids = parse_uuid_list(brand_ids, param_name="brand_ids")
        # Review round (26 Sep 2026): the SAME cap `get_outstanding_report` already
        # applies to its own `brand_ids` - an unbounded IN (...) from an external
        # caller is an easy way to make the brand-narrowing query slow.
        if resolved_brand_ids is not None and len(resolved_brand_ids) > 50:
            from app.services.error_handler import AppException

            raise AppException(
                422,
                "Too many values for 'brand_ids' (max 50)",
                detail=f"got {len(resolved_brand_ids)}",
                code="too_many_values",
            )
        # #1262 slice 9 (F1a): a brand ALONE counts as the product narrower this
        # endpoint requires, same as `product_ids` - the boolean check below is
        # widened, not bypassed, so an unrelated free-text `query` is still enough
        # on its own exactly as before.
        parsed_product_ids = narrow_product_ids_by_brand(
            db, parse_uuid_list(product_ids, param_name="product_ids"), resolved_brand_ids
        )
        # Endpoint is product-centric: require a product narrower to prevent
        # full-catalog enumeration. Accepts canonical UUIDs, legacy
        # product_code/SKU list, partial product text, free-text query, or
        # the deprecated entity bag.
        has_product_narrower = bool(
            has_product_filter(parsed_product_ids)
            or product_id
            or (product_query and product_query.strip())
            or (query and query.strip())
            or norm_entities
        )
        if not has_product_narrower:
            return {
                "data": [],
                "pagination": {"total": 0, "page": page, "limit": limit},
                "empty": True,
            }
        service = OrderService(db)
        by_product = service.list_orders_by_product(
            page=page,
            limit=limit,
            query=query,
            entities=norm_entities,
            product_ids=parsed_product_ids,
            customer_ids=scoped_customer_ids,
            transporter_ids=parse_uuid_list(transporter_ids, param_name="transporter_ids"),
            warehouse_codes=_normalize_entities(warehouse_codes),
            customer_query=None if is_scoped else customer_query,
            product_query=product_query,
            product_id=product_id,
            has_actual_delivery_date=has_actual_delivery_date,
            order_status=order_status,
            include_summary=include_summary,
            include_pipeline=include_pipeline,
            order_date_from=_parse_flex_date(order_date_from),
            order_date_to=_parse_flex_date(order_date_to, end_of_day=True),
            actual_delivery_date_from=_parse_flex_date(actual_delivery_date_from),
            actual_delivery_date_to=_parse_flex_date(actual_delivery_date_to, end_of_day=True),
            sort_field=sort or "order_date",
            sort_dir=dir or "desc",
        )
        if is_scoped and isinstance(by_product, dict):
            # Same rule as the orders list: a scoped contact gets no `resolved_entities`
            # echo (a name oracle).
            by_product.pop("resolved_entities", None)
        return by_product
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.get("/analytics")
async def get_order_analytics(
    metric: str = Query(
        ...,
        description=(
            "Aggregate to compute. One of: count (number of orders), "
            "total_value (SUM of order total_amount = revenue), "
            "avg_delivery_days (AVG of actual_delivery_date - order_date, in days, "
            "over orders that have both dates)."
        ),
    ),
    group_by: str = Query(
        "none",
        description="Bucket results by: customer | product | month | none (single overall figure).",
    ),
    customer_ids: Optional[list[str]] = Query(
        None,
        description=(
            "Filter by customer UUIDs (csv/JSON/repeated). Matches Order.customer_id "
            "IN (...) with a legacy debtor_name fallback. Pass the resolved customer "
            "UUID - free-text names are coerced to UUIDs upstream."
        ),
    ),
    product_ids: Optional[list[str]] = Query(
        None,
        description="Filter to orders containing any of these product UUIDs (csv/JSON/repeated).",
    ),
    product_code: Optional[str] = Query(
        None,
        description="Partial product-code filter (case-insensitive) as an alternative to product_ids.",
    ),
    date_from: Optional[str] = Query(
        None,
        description=(
            "Order date from (inclusive). Accepts YYYY-MM-DD, DD/MM/YYYY, DD-MM-YYYY, "
            "YYYY/MM/DD, ISO datetime, 'YYYY-MM', 'MM/YYYY', or 'Month YYYY'."
        ),
    ),
    date_to: Optional[str] = Query(
        None,
        description="Order date to (inclusive). Same flexible formats as date_from.",
    ),
    limit: int = Query(50, ge=1, le=500, description="Max number of ranked group rows to return."),
    contact_id: Optional[str] = Query(
        None,
        description=(
            "Respond.io contact id. Both-or-neither with space_id (one alone is 422). A "
            "contact linked to customers and holding no office access type is scoped to "
            "them: another customer's id is 403 `customer_not_permitted`, and no customer "
            "argument means its own customers."
        ),
    ),
    space_id: Optional[str] = Query(
        None, description="Respond.io workspace id, required together with contact_id.",
    ),
    current_user: dict = Depends(get_current_user_or_api_key),
    db: Session = Depends(get_db),
):
    """Aggregate customer sales orders (count / total revenue / average delivery days).

    Returns ranked group rows plus an order-level overall total. Exposes ONLY the
    computed aggregates - never per-order cost/invoice pricing.
    """
    try:
        requested_customer_ids = parse_uuid_list(customer_ids, param_name="customer_ids")
        scoped_customer_ids = enforce_customer_scope(
            db, contact_id=contact_id, space_id=space_id, customer_ids=requested_customer_ids, customer_query=None
        )
        if scoped_customer_ids is None:
            scoped_customer_ids = requested_customer_ids
        service = OrderService(db)
        # Accept a bare 4-digit year ("2026") for a whole-year window.
        _df = re.fullmatch(r"\s*(\d{4})\s*", date_from or "")
        _dt = re.fullmatch(r"\s*(\d{4})\s*", date_to or "")
        date_from_s = f"{_df.group(1)}-01-01" if _df else date_from
        date_to_s = f"{_dt.group(1)}-12-31" if _dt else date_to
        return service.order_analytics(
            metric=metric,
            group_by=group_by,
            customer_ids=scoped_customer_ids,
            product_ids=parse_uuid_list(product_ids, param_name="product_ids"),
            product_code=product_code,
            date_from=_parse_flex_date(date_from_s),
            date_to=_parse_flex_date(date_to_s, end_of_day=True),
            limit=limit,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.get("/{order_id}/fulfilled-complaints")
async def get_order_fulfilled_complaints(
    order_id: str,
    current_user: dict = Depends(get_current_user_or_api_key),
    db: Session = Depends(get_db),
):
    """Complaints this Delivery Order fulfils (reverse of the complaint detail
    "Fulfilment Delivery Orders" section). See
    docs/plans/PLAN-complaint-do-auto-fulfilment.md."""
    try:
        from app.services.complaint_fulfilment_service import ComplaintFulfilmentService

        order = OrderService(db).get_order(order_id)  # 404 if missing
        return ComplaintFulfilmentService(db).list_fulfilled_complaints(str(order.id))
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.get("/{order_id}", response_model=OrderResponse)
async def get_order(
    order_id: str,
    current_user: dict = Depends(get_current_user_or_api_key),
    db: Session = Depends(get_db)
):
    """Get a single order by ID."""
    try:
        service = OrderService(db)
        order = service.get_order(order_id)
        return order
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post("/", response_model=OrderResponse, status_code=status.HTTP_201_CREATED)
async def create_order(
    order_data: OrderCreate,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Create a new order."""
    try:
        service = OrderService(db)
        order = service.create_order(order_data, current_user["id"])
        return order
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.put("/{order_id}", response_model=OrderResponse)
async def update_order(
    order_id: str,
    order_data: OrderUpdate,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Update an order."""
    try:
        service = OrderService(db)
        order = service.update_order(order_id, order_data, current_user["id"])
        return order
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


class OrderCancelRequest(BaseModel):
    reason: Optional[str] = None


@router.post("/{order_id}/cancel", response_model=OrderResponse)
async def cancel_order(
    order_id: str,
    body: OrderCancelRequest = Body(default_factory=OrderCancelRequest),
    current_user: dict = Depends(get_current_user_or_api_key),
    db: Session = Depends(get_db),
):
    """Cancel an order (sets is_cancelled=true, optional reason → remarks).

    Narrow, single-purpose alternative to the broad PUT so automation / the AI
    assistant (X-API-Key act-as principal) can cancel without a wide edit grant - 
    orders have no per-field edit permission, so this matches update_order's
    auth-only gate. Delegates to OrderService.update_order so the SAME
    complaint (un)link + re-fulfilment re-evaluation runs on cancel; only sets
    remarks when a reason is supplied (exclude_unset preserves existing remarks).
    """
    try:
        fields: dict = {"is_cancelled": True}
        if body and body.reason:
            fields["remarks"] = body.reason
        service = OrderService(db)
        order = service.update_order(order_id, OrderUpdate(**fields), current_user["id"])
        return order
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.delete("/bulk", status_code=status.HTTP_200_OK)
async def bulk_delete_orders(
    body: BulkDeleteOrdersRequest = Body(...),
    current_user: dict = Depends(require_permission("order_management.orders.delete")),
    db: Session = Depends(get_db)
):
    """Bulk delete orders by ID."""
    try:
        service = OrderService(db)
        return service.bulk_delete_orders(body.ids)
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.delete("/{order_id}", status_code=status.HTTP_200_OK)
async def delete_order(
    order_id: str,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Delete an order permanently (hard delete). Use archive for retention."""
    try:
        service = OrderService(db)
        result = service.delete_order(order_id)
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post("/{order_id}/lines", response_model=OrderLineResponse, status_code=status.HTTP_201_CREATED)
async def create_order_line(
    order_id: str,
    data: OrderLineCreate,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Add a line to an order (delivery order detail)."""
    try:
        service = OrderService(db)
        line = service.create_order_line(order_id, data)
        return line
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.put("/{order_id}/lines/{line_id}", response_model=OrderLineResponse)
async def update_order_line(
    order_id: str,
    line_id: str,
    data: OrderLineUpdate,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update an order line."""
    try:
        service = OrderService(db)
        line = service.update_order_line(order_id, line_id, data)
        return line
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.delete("/{order_id}/lines/{line_id}", status_code=status.HTTP_200_OK)
async def delete_order_line(
    order_id: str,
    line_id: str,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Remove an order line."""
    try:
        service = OrderService(db)
        return service.delete_order_line(order_id, line_id)
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.delete("/{order_id}/lines/bulk-delete", status_code=status.HTTP_200_OK)
async def bulk_delete_order_lines(
    order_id: str,
    body: BulkDeleteOrderLinesRequest = Body(...),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete multiple order lines from one order."""
    try:
        service = OrderService(db)
        return service.bulk_delete_order_lines(order_id, body.ids)
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post("/{order_id}/archive", status_code=status.HTTP_200_OK)
async def archive_order(
    order_id: str,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Archive an order (soft delete). Data remains for retention."""
    try:
        service = OrderService(db)
        result = service.archive_order(order_id)
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.put("/{order_id}/restore", status_code=status.HTTP_200_OK)
async def restore_order(
    order_id: str,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Restore an archived order."""
    try:
        service = OrderService(db)
        result = service.restore_order(order_id)
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post("/bulk-import", response_model=BulkImportResponse, status_code=status.HTTP_200_OK)
async def bulk_import_orders(
    import_data: BulkImportRequest,
    current_user: dict = Depends(require_permission("order_management.orders.import")),
    db: Session = Depends(get_db)
):
    """Bulk import orders from Excel data.
    
    Creates new orders or updates existing ones based on ID or order_number.
    """
    try:
        service = OrderService(db)
        result = service.bulk_import_orders(import_data.orders, current_user["id"])
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post("/import-tracking", status_code=status.HTTP_202_ACCEPTED)
async def import_order_tracking(
    file: UploadFile = File(...),
    validate_only: bool = Query(False, description="If true, validate file only and return errors/warnings (no import)."),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Import orders from Excel file with Master and Overall Tracking sheets (queued). Use validate_only=true to test without importing."""
    try:
        if not file.filename or not file.filename.lower().endswith((".xlsx", ".xls", ".xlsm")):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid file type. Please upload an Excel file (.xlsx, .xls, or .xlsm)."
            )
        file_data = await file.read()
        # Retain the ORIGINAL bytes (pre-macro-strip) for source-file tracing.
        source_bytes, source_name, source_ctype = file_data, file.filename, file.content_type
        from app.services.excel_macro_stripper import maybe_strip
        file_data, cleaned_name = maybe_strip(file_data, file.filename or "upload.xlsx")

        if validate_only:
            service = OrderService(db)
            result = service.import_excel_tracking(file_data, current_user["id"], validate_only=True)
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content={
                    "valid": result["valid"],
                    "errors": result["errors"],
                    "warnings": result["warnings"],
                    "summary": result.get("summary"),
                },
            )

        from app.services.job_service import JobService
        from app.services.queue_service import enqueue_job
        from app.tasks.import_tasks import process_order_tracking_import

        job_service = JobService(db)
        job = job_service.create_job(
            job_type='order_tracking_import',
            user_id=current_user["id"],
            filename=cleaned_name
        )
        from app.services.import_source_store import store_import_source_file
        store_import_source_file(job, source_bytes, source_name, source_ctype)
        db.commit()

        rq_job = enqueue_job(
            process_order_tracking_import,
            str(job.id),
            file_data,
            current_user["id"],
            queue_name='imports',
            job_timeout=3600,
            job_id=str(job.job_id),  # pre-assign RQ id = DB job_id; see update_job_with_rq_id
        )
        job_service.update_job_with_rq_id(job, rq_job.id)

        return {
            'job_id': rq_job.id,
            'status': 'queued',
            'message': 'Import job queued successfully'
        }
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post("/import-order-lines", status_code=status.HTTP_202_ACCEPTED)
async def import_delivery_order_detail(
    file: UploadFile = File(...),
    validate_only: bool = Query(False, description="If true, validate file only and return errors/warnings (no import)."),
    current_user: dict = Depends(require_permission("order_management.orders.import")),
    db: Session = Depends(get_db),
):
    """Import delivery order detail (order lines) from Excel. Uses doc no -> order, item code -> product, location -> warehouse. Upserts by (order, product, warehouse)."""
    try:
        if not file.filename or not file.filename.lower().endswith((".xlsx", ".xls", ".xlsm")):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid file type. Please upload an Excel file (.xlsx, .xls, or .xlsm)."
            )
        file_data = await file.read()
        # Retain the ORIGINAL bytes (pre-macro-strip) for source-file tracing.
        source_bytes, source_name, source_ctype = file_data, file.filename, file.content_type
        from app.services.excel_macro_stripper import (
            MacroWorkbookError,
            extract_macro_template_xlsx,
            is_xlsm_filename,
            maybe_strip,
        )

        # Macro workbooks carry the import rows on the 'Template' sheet
        # (docs/plans/PLAN-do-macro-upload-and-drawer-import-jobs.md): strip
        # VBA + keep Template only so the parser's workbook.active IS the data
        # sheet. Strict: 422 when Template is missing or the macro hasn't
        # populated it. Plain .xlsx/.xls flow unchanged (active sheet).
        if is_xlsm_filename(file.filename):
            try:
                file_data, cleaned_name, _ = extract_macro_template_xlsx(
                    file_data, file.filename, file.content_type, require_data=True
                )
            except MacroWorkbookError as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=str(exc),
                )
        else:
            file_data, cleaned_name = maybe_strip(file_data, file.filename or "upload.xlsx")
        if validate_only:
            service = OrderService(db)
            result = service.validate_delivery_order_detail_excel(file_data)
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content={
                    "valid": result["valid"],
                    "errors": result["errors"],
                    "warnings": result["warnings"],
                    "summary": result.get("summary"),
                },
            )

        from app.services.job_service import JobService
        from app.services.queue_service import enqueue_job
        from app.tasks.import_tasks import process_delivery_order_detail_import

        job_service = JobService(db)
        job = job_service.create_job(
            job_type="delivery_order_detail_import",
            user_id=current_user["id"],
            filename=cleaned_name,
        )
        from app.services.import_source_store import store_import_source_file
        store_import_source_file(job, source_bytes, source_name, source_ctype)
        db.commit()

        rq_job = enqueue_job(
            process_delivery_order_detail_import,
            str(job.id),
            file_data,
            cleaned_name,
            current_user["id"],
            queue_name="imports",
            job_timeout=3600,
            job_id=str(job.job_id),  # pre-assign RQ id = DB job_id; see update_job_with_rq_id
        )
        job_service.update_job_with_rq_id(job, rq_job.id)

        return {
            "job_id": rq_job.id,
            "status": "queued",
            "message": "Delivery order detail import job queued successfully",
        }
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        raise handle_internal_error(str(e))


# ---------------------------------------------------------------------------
# outstanding report - a SEPARATE router, no prefix (mounted directly by
# `app/api/v1/order_management/__init__.py`), because the plan/UAC pin the
# path at `/order-management/outstanding-report`, not
# `/order-management/orders/outstanding-report` - `router` above is mounted
# with `prefix="/orders"` and every other endpoint in this file lives under
# that prefix on purpose (they are order rows; this is a report).
# ---------------------------------------------------------------------------
outstanding_report_router = APIRouter()


@outstanding_report_router.get("/outstanding-report", response_model=OutstandingReportResponse)
async def get_outstanding_report(
    product_code: Optional[str] = Query(
        None,
        description=(
            "Exact product code, case-insensitive (AC-1119). No sibling-code expansion. "
            "OPTIONAL since R13: the report's subject may be a customer instead, or both - "
            "but at least one of product_code / customer_ids / customer_query is required."
        ),
    ),
    product_codes: Optional[list[str]] = Query(
        None,
        description=(
            "Exact product codes (csv/JSON/repeated), case-insensitive - the SEVERAL-code "
            "form of product_code, for a caller answering one question about a whole "
            "family (\"all\" over a ten-variant product roster). The header names every "
            "one of them, the way the Customer line names every ledger. Wins over "
            "product_code when both are given."
        ),
    ),
    scope: str = Query(
        "both",
        description="Which block(s) to compute: so | do | both (default both).",
    ),
    customer_query: Optional[str] = Query(
        None,
        description="Partial match on customers.customer_name (ILIKE) ONLY - never debtor/customer code (D7).",
    ),
    customer_ids: Optional[list[str]] = Query(
        None,
        description=(
            "Canonical customer UUIDs (csv/JSON/repeated) - the chatbot's resolved "
            "customer entity (AC-1113b). Intersects with customer_query when both are given."
        ),
    ),
    warehouse_codes: Optional[list[str]] = Query(
        None,
        description=(
            "Exact warehouse codes (csv/JSON/repeated), case-insensitive. Location TOKEN "
            "resolution (e.g. an 'IB' suffix matching several codes) happens in the caller, "
            "not here - this route only matches the exact codes it is given."
        ),
    ),
    brand_ids: Optional[list[str]] = Query(
        None,
        description=(
            "Canonical brand UUIDs (csv/JSON/repeated) - `Product.brand_id`, the chatbot's "
            "resolved brand entity (#1262 slice 9, AC-S9-4). A brand alone is a valid "
            "subject (counts toward the product_code/customer_ids/customer_query "
            "requirement below). The response echoes brand_name for the header."
        ),
    ),
    order_date_from: Optional[str] = Query(
        None,
        description=(
            "Filters SO rows on sales_orders.order_date and DO rows on orders.order_date. "
            "Same flexible formats as the orders list route. Never actual_delivery_date on "
            "this route - a pending DO by definition has none."
        ),
    ),
    order_date_to: Optional[str] = Query(None, description="Same flexible formats as order_date_from."),
    detail: Optional[str] = Query(
        None,
        description=(
            "so | do - MCP-layer directive only (S4 point 5, AC-1114b): when set, "
            "view=render swaps the two-block report for the numbered detail list of that "
            "scope. This route's own computation is unchanged by it - the value is only "
            "echoed onto the response body so the MCP presenter can read it."
        ),
    ),
    location_token: Optional[str] = Query(
        None,
        max_length=32,
        description=(
            "The raw location word the caller resolved into warehouse_codes (e.g. 'IB'). "
            "ECHO ONLY (AC-1105): this route never resolves or filters on it - it rides "
            "back on the body so the rendered header can read 'IB (BRW-IB, MWH-IB)' "
            "instead of repeating the codes with no word beside them."
        ),
    ),
    so_refused: bool = Query(
        False,
        description=(
            "ECHO ONLY (D13/AC-1141): the caller withheld the SO scope because the "
            "contact lacks the sales_orders.outstanding field reveal. Echoed onto the "
            "body so the rendered reply names the withheld half, between the header and "
            "the DO block. Filtering is unaffected - the caller sends scope=do."
        ),
    ),
    contact_id: Optional[str] = Query(
        None,
        description=(
            "Respond.io contact id. Both-or-neither with space_id (one alone is 422). A "
            "contact linked to customers and holding no office access type is scoped to "
            "them: another customer's id is 403 `customer_not_permitted`, and no customer "
            "argument means its own customers."
        ),
    ),
    space_id: Optional[str] = Query(
        None, description="Respond.io workspace id, required together with contact_id.",
    ),
    current_user: dict = Depends(require_permission_with_api_key("order_management.orders.view")),
    db: Session = Depends(get_db),
):
    """SO backlog + DO pending for one product (`documentation/plans/chatbot/
    PLAN-chatbot-outstanding-report.md`, AC-1110 to AC-1119).

    `so` / `do` is omitted from the body entirely when its scope was not asked
    (AC-1117) - `response_model` is declared for the OpenAPI schema and to
    validate every other declared field is present, but the actual response is
    built by hand (bypassing FastAPI's automatic serialization, the same
    pattern `get_orders` above uses for its `alternatives`/`groups` payloads)
    so that omission is possible: a null `so_count: 0` and a MISSING `so` key
    are different states the chatbot presenter must tell apart.
    """
    from app.services.error_handler import AppException
    from app.services.outstanding_report_service import outstanding_report

    # #1262 slice 9 (F1a): `brand_ids` is a UUID param like `customer_ids` (S3 below),
    # parsed before the subject check so a brand-only ask ("brand Sorento") counts as a
    # subject the same way a product or customer does - never `subject_required` for it
    # alone (AC-S9-4).
    resolved_brand_ids = parse_uuid_list(brand_ids, param_name="brand_ids")

    # PLAN-chatbot-customer-scope-29sep.md D5: a scoped contact's customers. The scope
    # itself is a subject (a bare ask from a scoped contact is its own customers).
    scoped_customer_ids = enforce_customer_scope(
        db,
        contact_id=contact_id,
        space_id=space_id,
        customer_ids=parse_uuid_list(customer_ids, param_name="customer_ids"),
        customer_query=customer_query,
    )

    # R13: the SUBJECT is a product, a customer, a brand, or any combination - but never
    # nothing. An unfiltered report would sum every open sales order line in the
    # company, which is not an answer to any question a customer can ask.
    resolved_product_codes = _normalize_entities(product_codes)
    if (
        not (product_code or "").strip()
        and not resolved_product_codes
        and not customer_ids
        and not scoped_customer_ids
        and not (customer_query or "").strip()
        and not resolved_brand_ids
    ):
        raise AppException(
            422,
            "This report needs a subject: give at least one of product_code, customer_ids, "
            "customer_query or brand_ids",
            detail="product_code, customer_ids, customer_query, brand_ids",
            code="subject_required",
        )

    scope = (scope or "both").strip().lower()
    if scope not in ("so", "do", "both"):
        raise AppException(
            422,
            f"Unknown scope value '{scope}'",
            detail="allowed: so, do, both",
            code="invalid_scope",
        )

    # S3 (security review, 13 Sep 2026): `customer_ids` is a UUID param like every
    # other `<entity>_ids` in this file (`_resolved_customer_ids = parse_uuid_list(...)`
    # a few routes up) - `_normalize_entities` is for opaque strings (warehouse codes),
    # and accepted a non-UUID value silently here where `outstanding_report_service`
    # would then filter on it and just find nothing, rather than 400 on the caller's
    # own malformed input. Both lists are capped at 50 - an unbounded IN (...) from an
    # external caller is an easy way to make this route's own two base queries slow.
    resolved_customer_ids = (
        scoped_customer_ids
        if scoped_customer_ids is not None
        else parse_uuid_list(customer_ids, param_name="customer_ids")
    )
    resolved_warehouse_codes = _normalize_entities(warehouse_codes)
    for values, name in (
        (resolved_customer_ids, "customer_ids"),
        (resolved_warehouse_codes, "warehouse_codes"),
        (resolved_product_codes, "product_codes"),
        (resolved_brand_ids, "brand_ids"),
    ):
        if values is not None and len(values) > 50:
            raise AppException(
                422,
                f"Too many values for '{name}' (max 50)",
                detail=f"got {len(values)}",
                code="too_many_values",
            )

    data = outstanding_report(
        db,
        product_code=product_code,
        product_codes=resolved_product_codes,
        scope=scope,
        # The scope helper already matched a scoped contact's query (name or code) into ids.
        customer_query=None if scoped_customer_ids is not None else customer_query,
        customer_ids=resolved_customer_ids,
        warehouse_codes=resolved_warehouse_codes,
        brand_ids=resolved_brand_ids,
        order_date_from=_parse_flex_date(order_date_from),
        order_date_to=_parse_flex_date(order_date_to, end_of_day=True),
    )
    validated = OutstandingReportResponse(**data)
    body = validated.model_dump(mode="json")
    for key in (
        "so", "do",
        # R13: a breakdown group the subject does not want is ABSENT, not empty.
        "so_by_customer", "so_by_product", "do_by_customer", "do_by_product",
    ):
        if data.get(key) is None:
            body.pop(key, None)
    if detail in ("so", "do"):
        body["detail"] = detail
    # Echo-only, same contract as `detail` above: read by the MCP presenter
    # (`sorento_crm_mcp/presenters.py::_outstanding_report`), never by this route.
    if location_token:
        body["location_token"] = location_token.strip()
    if so_refused:
        body["so_refused"] = True
    return JSONResponse(content=body)


# ---------------------------------------------------------------------------
# sales report - delivered sales by DO date (lane SALES-REPORT). A SEPARATE router,
# no prefix (mounted directly by `app/api/v1/order_management/__init__.py`),
# the same reason `outstanding_report_router` above is one: the plan/UAC pin
# the path at `/order-management/sales-report`, not
# `/order-management/orders/sales-report` - this is a report, not an order row.
# ---------------------------------------------------------------------------
sales_report_router = APIRouter()


@sales_report_router.get("/sales-report", response_model=SalesReportResponse)
async def get_sales_report(
    product_code: Optional[str] = Query(
        None,
        description=(
            "PREFIX, case-insensitive (S19, 19 Sep 2026): matches the typed code AND "
            "every product whose code STARTS WITH it, e.g. 'SRT5674' also matches "
            "'SRT5674-N'. Must be at least 3 characters after stripping. "
            "OPTIONAL: the report's subject may be a customer instead, or both - "
            "but at least one of product_code / customer_ids / customer_query is required."
        ),
    ),
    customer_query: Optional[str] = Query(
        None,
        description="Partial match on customers.customer_name (ILIKE) ONLY - never debtor/customer code.",
    ),
    customer_ids: Optional[list[str]] = Query(
        None,
        description="Canonical customer UUIDs (csv/JSON/repeated) - the chatbot's resolved customer entity.",
    ),
    channel: Optional[str] = Query(
        None,
        description=(
            "dealer | project - the account's market segment class (project / contract = "
            "project, any other segment = dealer); an account with no segment is in every "
            "channel. Ignored when the scoped accounts span only one class. Any other value is 422."
        ),
    ),
    warehouse_codes: Optional[list[str]] = Query(
        None,
        description=(
            "Exact warehouse codes (csv/JSON/repeated), case-insensitive. Location TOKEN "
            "resolution happens in the caller, not here - this route only matches the "
            "exact codes it is given."
        ),
    ),
    location_token: Optional[str] = Query(
        None,
        max_length=32,
        description=(
            "The raw location word the caller resolved into warehouse_codes (e.g. 'IB'). "
            "ECHO ONLY (S9): this route never resolves or filters on it - it rides back on "
            "the body so the rendered header can read 'IB (BRW-IB, MWH-IB)' the same way "
            "the outstanding report's header does."
        ),
    ),
    date_from: Optional[str] = Query(
        None,
        description=(
            "Filters on the DO's own date (orders.order_date). Same flexible formats as the "
            "orders list route. One bound alone is open-ended on the other side."
        ),
    ),
    date_to: Optional[str] = Query(None, description="Same flexible formats as date_from."),
    group_by: Optional[str] = Query(
        None,
        description=(
            "customer | product | delivery_order | sales_agent - adds rows[], ranked by amount "
            "(delivery orders latest first), top 10 with `more` the rest. Any other value is 422."
        ),
    ),
    contact_id: Optional[str] = Query(
        None,
        description=(
            "Respond.io contact id (SEC-S1/S2, ruling S14). Both-or-neither with "
            "space_id - one without the other is 422. When both are given the route "
            "re-checks the per-contact `sales_orders.sales_report` reveal key itself "
            "(not just the chatbot lane's own gate, which a direct MCP/n8n caller "
            "bypasses): no grant is 403 `sales_report_not_enabled`."
        ),
    ),
    space_id: Optional[str] = Query(
        None, description="Respond.io workspace id, required together with contact_id.",
    ),
    current_user: dict = Depends(require_permission_with_api_key("order_management.orders.view")),
    db: Session = Depends(get_db),
):
    """Delivered sales by DO date (`documentation/plans/chatbot/selfref-scope-acceptance-
    criteria.md` AC-SR-20 to AC-SR-26): `total`, `periods[]` at the window's grain, and with
    `group_by` a ranked `rows[]`, plus the drill-down `options[]` the reply offers. With a
    contact, the contact's stock visibility policy caps the locations: a named location
    outside it is answered `status: refused`.
    """
    from app.services.error_handler import AppException
    from app.services.sales_report_delivered import GROUP_BYS, delivered_sales_report

    # S7: the SUBJECT is a product, a customer, or both - but never nothing.
    # A contact identity defers this check until the customer scope is known below: a
    # scoped contact's own customers ARE the subject (the outstanding route's rule).
    def _needs_subject() -> AppException:
        return AppException(
            422,
            "This report needs a subject: give at least one of product_code, customer_ids "
            "or customer_query",
            detail="product_code, customer_ids, customer_query",
            code="subject_required",
        )

    _no_subject = (
        not (product_code or "").strip() and not customer_ids and not (customer_query or "").strip()
    )
    if _no_subject and not (contact_id and space_id):
        raise _needs_subject()

    # SEC-B2 (security review, Phase 3 fix round, ruling S17): a customer_query given
    # (non-blank - blank already 422s above as subject_required) needs at least 3
    # characters after stripping, or a single-letter ILIKE scans and returns every
    # customer whose name contains it, company-wide.
    _customer_query_stripped = (customer_query or "").strip()
    if _customer_query_stripped and len(_customer_query_stripped) < 3:
        raise AppException(
            422,
            "customer_query must be at least 3 characters",
            detail=_customer_query_stripped,
            code="customer_query_too_short",
        )

    # S19 (owner ruling, 19 Sep 2026): product_code is now a PREFIX, so the
    # same reasoning as customer_query_too_short above applies - a 1-2
    # character prefix would LIKE-scan the whole product master, company-wide.
    _product_code_stripped = (product_code or "").strip()
    if _product_code_stripped and len(_product_code_stripped) < 3:
        raise AppException(
            422,
            "product_code must be at least 3 characters",
            detail=_product_code_stripped,
            code="product_code_too_short",
        )

    # SEC-S2 (security review, Phase 3 fix round, ruling S14): contact_id and space_id
    # are both-or-neither on this route - one without the other is never silently
    # treated as "no contact at all" (which would skip the SEC-S1 gate below).
    if bool(contact_id) != bool(space_id):
        raise AppException(
            422,
            "contact_id and space_id must both be given, or neither",
            detail="contact_id, space_id",
            code="contact_identity_required",
        )

    # SEC-S1 (security review, Phase 3 fix round, ruling S14): a contact_id/space_id
    # caller - a direct MCP/n8n call, which bypasses the chatbot lane's own gate
    # entirely - is re-checked against the per-contact reveal key here, the same shape
    # `app/api/v1/scm/low_stock_report.py` uses for its own second gate.
    if contact_id and space_id:
        from app.services.contact_field_reveal_service import granted_keys
        from app.services.field_access import resolve_contact_with_null_workspace_fallback

        resolved_contact_id = resolve_contact_with_null_workspace_fallback(
            db, contact_id=contact_id, space_id=space_id
        )
        keys = granted_keys(db, resolved_contact_id) if resolved_contact_id else []
        if "sales_orders.sales_report" not in keys:
            raise AppException(
                403,
                "Sales report is not enabled for your account.",
                code="sales_report_not_enabled",
            )

    group_by_norm = (group_by or "").strip().lower() or None
    if group_by_norm is not None and group_by_norm not in GROUP_BYS:
        raise AppException(
            422,
            f"Unknown group_by value '{group_by}'",
            detail="allowed: " + ", ".join(GROUP_BYS),
            code="unknown_group_by",
        )

    channel_norm = (channel or "").strip().lower() or None
    if channel_norm is not None and channel_norm not in ("dealer", "project"):
        raise AppException(
            422,
            f"Unknown channel value '{channel}'",
            detail="allowed: dealer, project",
            code="invalid_channel",
        )

    resolved_customer_ids = parse_uuid_list(customer_ids, param_name="customer_ids")
    # PLAN-chatbot-customer-scope-29sep.md D5: a scoped contact's customers (the reveal
    # key above is the first gate, this is the second).
    scoped_customer_ids = enforce_customer_scope(
        db,
        contact_id=contact_id,
        space_id=space_id,
        customer_ids=resolved_customer_ids,
        customer_query=customer_query,
    )
    if scoped_customer_ids is not None:
        resolved_customer_ids = scoped_customer_ids
        if group_by_norm == "sales_agent":
            # Fix round 1, security N1: a customer-scoped contact asks about its own
            # accounts; a ranking of the company's sales agents is staff information.
            raise AppException(
                403,
                "That breakdown is not available for your account.",
                code="group_by_not_allowed",
            )
    if _no_subject and not scoped_customer_ids:
        raise _needs_subject()
    resolved_warehouse_codes = _normalize_entities(warehouse_codes)
    for values, name in (
        (resolved_customer_ids, "customer_ids"),
        (resolved_warehouse_codes, "warehouse_codes"),
    ):
        if values is not None and len(values) > 50:
            raise AppException(
                422,
                f"Too many values for '{name}' (max 50)",
                detail=f"got {len(values)}",
                code="too_many_values",
            )

    # AC-SR-25: a contact's own stock visibility policy caps the locations it may be told
    # about (`stock_visibility.resolve_policy`, the stock route's own resolver).
    policy = None
    if contact_id and space_id:
        from app.services.stock_visibility import resolve_policy

        policy = resolve_policy(db, resolved_contact_id, space_id) if resolved_contact_id else None

    parsed_from = _parse_flex_date(date_from)
    parsed_to = _parse_flex_date(date_to, end_of_day=True)
    data = delivered_sales_report(
        db,
        product_code=product_code,
        customer_query=None if scoped_customer_ids is not None else customer_query,
        customer_ids=resolved_customer_ids,
        channel=channel_norm,
        warehouse_codes=resolved_warehouse_codes,
        location_token=location_token,
        date_from=parsed_from.date() if parsed_from else None,
        date_to=parsed_to.date() if parsed_to else None,
        group_by=group_by_norm,
        policy=policy,
        capped_by_policy=bool(contact_id and space_id),
    )
    validated = SalesReportResponse(**data)
    return JSONResponse(content=validated.model_dump(mode="json", by_alias=True))


# ---------------------------------------------------------------------------
# top selling - a whole-window ranking of items (or categories) on the sales
# report's own source, predicate, reveal key and company scope
# (`documentation/plans/chatbot/PLAN-chatbot-top-x-hot-selling-24sep.md`, S2,
# as amended by the owner's 26 Sep 2026 rulings on PR #1175). Same no-prefix
# router as the sales report: a report over orders, not an order row.
# ---------------------------------------------------------------------------


def _top_selling_is_staff(db: Session, contact_id: str) -> bool:
    """Alias of `contact_customer_scope.is_office_staff` (the one shared rule)."""
    from app.services.contact_customer_scope import is_office_staff

    return is_office_staff(db, contact_id)


def _top_selling_dealer_scope(db: Session, contact_id: str) -> Optional[list[str]]:
    """The customers a dealer contact is forced to, or None for staff.

    Owner hand test on PR #1273 (27 Sep 2026, ruling assumed): any ACTIVE office
    access type makes the contact staff (`_top_selling_is_staff`), whatever
    dealer or end user types or customer links it also holds; staff sees every
    ledger. Without one, a contact linked to any customer
    (`respond_contact_customers`) is that customer's dealer and sees only its own
    ledgers. Anyone else (no type, end user, dealer with no link, an inactive
    office type, a type nobody classified) is refused (fail closed). The Sales
    report reveal is checked by the caller before this, for everyone."""
    from app.services import contact_customer_scope as scope_mod
    from app.services.error_handler import AppException

    scope = scope_mod.contact_customer_scope(db, contact_id)
    if scope.staff:
        return None
    if scope.linked:
        return scope.customer_ids
    raise AppException(
        403,
        "You can only see sales for your own account.",
        code="customer_not_permitted",
    )


@sales_report_router.get("/top-selling", response_model=TopSellingResponse)
async def get_top_selling(
    rank_by: Optional[str] = Query(
        None,
        description=(
            "REQUIRED: quantity | amount. No default metric (owner ruling): the chatbot "
            "asks when the message does not say. Missing is 422 `rank_by_required`."
        ),
    ),
    basis: Optional[str] = Query(
        None,
        description=(
            "delivered (default) | ordered. delivered = LEAST(qty_delivered, qty_ordered), "
            "transferred to DO, and its share of line_total; ordered = the whole line, "
            "qty_ordered and line_total, whatever the line's status."
        ),
    ),
    group: Optional[str] = Query(
        None, description="item (default) ranks products | category ranks product categories.",
    ),
    n: Optional[int] = Query(
        None,
        description=(
            "How many rows to return, 1 to 1000 (a safety ceiling, not a product limit). ABSENT = every ranked row (no cut-off, no "
            "paging); total_count always states the full count."
        ),
    ),
    customer_ids: Optional[list[str]] = Query(
        None, description="Customer UUIDs (csv/JSON/repeated), the chatbot's resolved ledgers.",
    ),
    customer_query: Optional[str] = Query(
        None, description="Partial match on customers.customer_name (ILIKE), at least 3 characters.",
    ),
    category_ids: Optional[list[str]] = Query(
        None, description="Product category UUIDs (csv/JSON/repeated): products.category_id IN.",
    ),
    sales_agent_ids: Optional[list[str]] = Query(
        None,
        description=(
            "Sales agent UUIDs (csv/JSON/repeated): sales_orders.sales_agent_id IN. When given, "
            "the body carries sales_agent_fill_rate."
        ),
    ),
    brand_ids: Optional[list[str]] = Query(
        None, description="Brand UUIDs (csv/JSON/repeated): products.brand_id IN. Echoed as filters.brand_name.",
    ),
    direction: Optional[str] = Query(
        None,
        description=(
            "top (default) ranks the most sold first | bottom ranks the least sold first. "
            "Either way only items with a sale in the window are ranked."
        ),
    ),
    channel: Optional[str] = Query(
        None, description="dealer | project - sales_orders.demand_class ('retail' / 'project'). Absent = all.",
    ),
    date_from: Optional[str] = Query(
        None,
        description=(
            "On the bucket date (required_date, else the SO's order_date). Both absent = the "
            "current calendar year; one absent = that side of the current year. The resolved "
            "window is echoed."
        ),
    ),
    date_to: Optional[str] = Query(None, description="Same formats as date_from."),
    detail_code: Optional[str] = Query(
        None,
        description=(
            "The detail offer: one product code (group=item) or category code (group=category), "
            "case-insensitive. Rows and totals narrow to that code and `detail` carries its "
            "by_customer and by_month, same filters and basis, sorted by the rank_by metric desc."
        ),
    ),
    count_only: bool = Query(
        False,
        description=(
            "The chatbot's how-many question (no N named): the full total_count and totals with "
            "NO rows, so the whole ranked book is never sent just to be counted. A single ranked "
            "row is still returned (nothing to choose between). n is ignored."
        ),
    ),
    contact_id: Optional[str] = Query(
        None,
        description=(
            "Respond.io contact id, both-or-neither with space_id. When given the route re-checks "
            "the `sales_orders.sales_report` reveal key (403 `sales_report_not_enabled`). A contact "
            "holding any active office access type is staff and sees every customer, whatever else "
            "it holds. Otherwise a contact linked to customers is forced to them (403 "
            "`customer_not_permitted` when it names another), and anyone else is 403 "
            "`customer_not_permitted`."
        ),
    ),
    space_id: Optional[str] = Query(None, description="Respond.io workspace id, required together with contact_id."),
    current_user: dict = Depends(require_permission_with_api_key("order_management.orders.view")),
    db: Session = Depends(get_db),
):
    """Top selling items or categories over a window (PLAN-chatbot-top-x-hot-selling-24sep, S2)."""
    from app.services.error_handler import AppException
    from app.services.sales_report_service import TOP_SELLING_N_CEILING, current_year_window, top_selling

    def _choice(value, name, allowed, default=None):
        norm = (value or "").strip().lower() or default
        if norm is None:
            raise AppException(
                422, f"{name} is required: one of {', '.join(allowed)}",
                detail=f"allowed: {', '.join(allowed)}", code=f"{name}_required",
            )
        if norm not in allowed:
            raise AppException(
                422, f"Unknown {name} value '{value}'",
                detail=f"allowed: {', '.join(allowed)}", code=f"invalid_{name}",
            )
        return norm

    rank_by_norm = _choice(rank_by, "rank_by", ("quantity", "amount"))
    basis_norm = _choice(basis, "basis", ("delivered", "ordered"), default="delivered")
    group_norm = _choice(group, "group", ("item", "category"), default="item")
    direction_norm = _choice(direction, "direction", ("top", "bottom"), default="top")
    channel_norm = (channel or "").strip().lower() or None
    if channel_norm is not None:
        channel_norm = _choice(channel_norm, "channel", ("dealer", "project"))

    # The shared safety ceiling (owner, 30 Sep 2026: no 100 cap), not a product limit.
    if n is not None and not 1 <= n <= TOP_SELLING_N_CEILING:
        raise AppException(
            422, f"n must be between 1 and {TOP_SELLING_N_CEILING}", detail=str(n), code="invalid_n",
        )

    customer_query_stripped = (customer_query or "").strip() or None
    if customer_query_stripped and len(customer_query_stripped) < 3:
        raise AppException(
            422, "customer_query must be at least 3 characters",
            detail=customer_query_stripped, code="customer_query_too_short",
        )

    detail_code_stripped = (detail_code or "").strip() or None
    if detail_code_stripped and len(detail_code_stripped) > 100:
        raise AppException(
            422, "detail_code must be at most 100 characters",
            detail=str(len(detail_code_stripped)), code="detail_code_too_long",
        )

    if bool(contact_id) != bool(space_id):
        raise AppException(
            422, "contact_id and space_id must both be given, or neither",
            detail="contact_id, space_id", code="contact_identity_required",
        )

    resolved_customer_ids = parse_uuid_list(customer_ids, param_name="customer_ids")
    resolved_category_ids = parse_uuid_list(category_ids, param_name="category_ids")
    resolved_agent_ids = parse_uuid_list(sales_agent_ids, param_name="sales_agent_ids")
    resolved_brand_ids = parse_uuid_list(brand_ids, param_name="brand_ids")
    for values, name in (
        (resolved_customer_ids, "customer_ids"),
        (resolved_category_ids, "category_ids"),
        (resolved_agent_ids, "sales_agent_ids"),
        (resolved_brand_ids, "brand_ids"),
    ):
        if values is not None and len(values) > 50:
            raise AppException(
                422, f"Too many values for '{name}' (max 50)", detail=f"got {len(values)}", code="too_many_values",
            )

    dealer_scoped = False
    if contact_id and space_id:
        from app.services.contact_field_reveal_service import granted_keys
        from app.services.field_access import resolve_contact_with_null_workspace_fallback

        resolved_contact_id = resolve_contact_with_null_workspace_fallback(
            db, contact_id=contact_id, space_id=space_id
        )
        keys = granted_keys(db, resolved_contact_id) if resolved_contact_id else []
        if "sales_orders.sales_report" not in keys:
            raise AppException(
                403, "Sales report is not enabled for your account.", code="sales_report_not_enabled",
            )
        own = _top_selling_dealer_scope(db, resolved_contact_id)
        if own is not None:
            not_permitted = AppException(
                403, "You can only see sales for your own account.", code="customer_not_permitted",
            )
            if resolved_customer_ids and any(c not in own for c in resolved_customer_ids):
                raise not_permitted
            if customer_query_stripped:
                from app.models.order import Customer
                from app.services.sales_report_service import _LIKE_ESCAPE, _escape_like

                matched = {
                    row[0]
                    for row in db.query(Customer.id).filter(
                        Customer.customer_name.ilike(
                            f"%{_escape_like(customer_query_stripped)}%", escape=_LIKE_ESCAPE
                        )
                    )
                }
                # Other customers only and nobody at all get the same 403, so
                # the answer never says whether a name exists in the book.
                if not matched.intersection(own):
                    raise not_permitted
            resolved_customer_ids = resolved_customer_ids or own
            dealer_scoped = True

    default_from, default_to = current_year_window()
    parsed_from = _parse_flex_date(date_from)
    parsed_to = _parse_flex_date(date_to)
    window_from = parsed_from.date() if parsed_from else default_from
    window_to = parsed_to.date() if parsed_to else default_to
    if window_from > window_to:
        raise AppException(
            422, "date_from is after date_to", detail=f"{window_from} > {window_to}", code="invalid_date_range",
        )

    data = top_selling(
        db,
        rank_by=rank_by_norm,
        basis=basis_norm,
        group=group_norm,
        # count_only: the window functions still count and total the whole set, so
        # one row is enough to read them off (and is the row a one-row set sends).
        n=1 if count_only else n,
        customer_query=customer_query_stripped,
        customer_ids=resolved_customer_ids,
        category_ids=resolved_category_ids,
        sales_agent_ids=resolved_agent_ids,
        brand_ids=resolved_brand_ids,
        channel=channel_norm,
        date_from=window_from,
        date_to=window_to,
        dealer_scoped=dealer_scoped,
        detail_code=detail_code_stripped,
        direction=direction_norm,
    )
    if count_only:
        data["n"] = None
        if data["total_count"] > 1:
            data["rows"] = []
    return TopSellingResponse(**data)
