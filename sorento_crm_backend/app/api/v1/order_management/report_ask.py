"""`GET /api/v1/order-management/report-ask`: the chatbot's one flexible sales report (lane
REPORT-ENGINE, slice 1a; `documentation/plans/chatbot/PLAN-report-engine.md` section 0 and 10).

A ranking (one `group_by`) or a number over delivered, ordered or outstanding sales (all from sales order lines), for a required period,
with the common filters. Every gate runs here, from `contact_id`, never in the lane or the
prompt: the API key, the `sales_orders.sales_report` reveal grant, the audience (a
customer-scoped contact is a DEALER and may only slice by product, brand, category, month),
the contact's stock visibility policy over locations, and the engine's own company arm.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import false, or_
from sqlalchemy.orm import Session

from app.api.v1.order_management._contact_scope import (
    enforce_customer_scope,
    require_contact_identity_pair,
)
from app.api.v1.order_management.orders import _normalize_entities
from app.database import get_db
from app.dependencies import require_permission_with_api_key
from app.models.base import company_scope
from app.models.company import RespondContactCompany
from app.models.order import Customer
from app.models.product import Brand, Product, ProductCategory
from app.models.sales_agent import SalesAgent
from app.schemas.report_ask import ReportAskResponse
from app.services.error_handler import AppException, handle_not_found
from app.services.reports import ask
from app.services.sales_report_service import TOP_SELLING_N_CEILING, _resolve_products
from app.services.uuid_list_param import parse_uuid_list

router = APIRouter()

GRANT = "sales_orders.sales_report"
_RATE_LIMIT = 10
_RATE_WINDOW_SECONDS = 600
_DEALER_MESSAGE = "That breakdown is not available for your account."
_CHANNELS = {"dealer": "Dealer", "project": "Project team"}
#: The basis a request without one gets. The owner decides the default; this is the one place.
DEFAULT_BASIS = "delivered"
_MAX_VALUES = 50
_MIN_YEAR, _MAX_YEAR = 1900, 2200

#: Every query key the route declares; any other is 422 `unknown_param`.
_PARAMS = frozenset(
    {
        "date_from", "date_to", "basis", "measure", "group_by", "top_n", "sort", "product_code",
        "product_ids", "brand_ids", "category_ids", "sales_agent_ids", "customer_ids", "warehouse_codes",
        "channel", "contact_id", "space_id",
    }
)
#: The filter params: present but resolving to nothing is 422 `empty_filter`, never "no filter".
_FILTER_PARAMS = (
    "product_code", "product_ids", "brand_ids", "category_ids", "sales_agent_ids", "customer_ids",
    "warehouse_codes", "channel",
)


def _unprocessable(message: str, code: str, detail: Optional[str] = None) -> AppException:
    return AppException(422, message, detail=detail, code=code)


def _allowed(values: Any) -> str:
    return "allowed: " + ", ".join(values)


def _lookup_pairs(
    db: Session, model: Any, name_column: Any, ids: list[str], grants: set[str], label: str,
    *, shared: bool = False,
) -> list[tuple[str, str]]:
    """`(id, name)` of `ids`, inside the contact's companies. An id naming no row is 404, so a
    named filter never quietly widens to "no filter"."""
    # No company of its own: every id is outside the contact's companies (fail closed).
    cond = model.company_id.in_(sorted(grants)) if grants else false()
    if grants and shared:
        cond = or_(model.company_id.is_(None), cond)
    with company_scope(db, None):
        rows = db.query(model.id, name_column).filter(model.id.in_(ids), cond).all()
    found = {str(i): str(n) for i, n in rows}
    for wanted in ids:
        if wanted not in found:
            raise handle_not_found(label, wanted)
    return list(found.items())


def _lookup(
    db: Session, model: Any, name_column: Any, ids: list[str], grants: set[str], label: str,
    *, shared: bool = False,
) -> list[str]:
    """The names of `ids` (`_lookup_pairs`), sorted."""
    return sorted(n for _i, n in _lookup_pairs(db, model, name_column, ids, grants, label, shared=shared))


def _echo(
    db: Session,
    grants: set[str],
    *,
    product_code: str,
    product_names: list[str],
    customers: Optional[list[str]],
    brands: Optional[list[str]],
    categories: Optional[list[str]],
    agents: Optional[list[str]],
    warehouse_codes: list[str],
    channel: Optional[str],
) -> list[dict[str, Any]]:
    """The filters the caller named, by name, in the catalogue's word order. Looks every id
    up, so an id naming no row is 404 here."""
    out: list[tuple[str, list[str]]] = []
    if customers:
        out.append(("customer", _lookup(db, Customer, Customer.customer_name, customers, grants, "Customer")))
    if product_names:
        out.append(("product", product_names))
    elif product_code:
        out.append(("product", [product_code.upper()]))
    if brands:
        out.append(("brand", _lookup(db, Brand, Brand.brand_name, brands, grants, "Brand")))
    if categories:
        out.append(
            ("category", _lookup(db, ProductCategory, ProductCategory.category_name, categories, grants, "Category"))
        )
    if agents:
        out.append(
            ("sales_agent", _lookup(db, SalesAgent, SalesAgent.sales_agent, agents, grants, "Sales agent", shared=True))
        )
    if warehouse_codes:
        out.append(("location", list(warehouse_codes)))
    if channel:
        out.append(("channel", [_CHANNELS[channel]]))
    return [{"key": key, "label": ask.GROUP_LABELS[key], "values": values} for key, values in out if values]


def _response(data: dict) -> JSONResponse:
    return JSONResponse(content=ReportAskResponse(**data).model_dump(mode="json"))


@router.get("/report-ask")
def report_ask(
    request: Request,
    date_from: Optional[date] = Query(None, description="First day of the period (ISO). Required."),
    date_to: Optional[date] = Query(None, description="Last day of the period (ISO). Required."),
    basis: str = Query(
        DEFAULT_BASIS,
        description="delivered | ordered | outstanding. All three rank sales order lines (the SO's own agent), "
        "dated by the line's required date, else the SO date.",
    ),
    measure: str = Query("amount", description="amount | qty - what the ranking sorts by."),
    group_by: Optional[str] = Query(
        None,
        description="customer | product | brand | category | sales_agent | location | channel | month. Absent = one total.",
    ),
    top_n: Optional[int] = Query(None, description=f"1 to {TOP_SELLING_N_CEILING}. Required with group_by."),
    sort: str = Query("desc", description="desc (top) | asc (bottom)."),
    product_code: Optional[str] = Query(None, description="Product code prefix, at least 3 characters."),
    product_ids: Optional[list[str]] = Query(None, description="Product ids; ANDed with product_code."),
    brand_ids: Optional[list[str]] = Query(None),
    category_ids: Optional[list[str]] = Query(None),
    sales_agent_ids: Optional[list[str]] = Query(None),
    customer_ids: Optional[list[str]] = Query(None),
    warehouse_codes: Optional[list[str]] = Query(None),
    channel: Optional[str] = Query(None, description="dealer | project"),
    contact_id: Optional[str] = Query(None, description="Respond.io contact id. Required with space_id."),
    space_id: Optional[str] = Query(None, description="Respond.io workspace id. Required with contact_id."),
    current_user: dict = Depends(require_permission_with_api_key("order_management.orders.view")),
    db: Session = Depends(get_db),
):
    contact_id = (contact_id or "").strip() or None
    space_id = (space_id or "").strip() or None
    require_contact_identity_pair(contact_id, space_id)
    if not (contact_id and space_id):
        raise AppException(
            422,
            "contact_id and space_id are required",
            detail="contact_id, space_id",
            code="contact_identity_required",
        )
    if not request.headers.get("X-API-Key"):
        raise AppException(403, "This route is for the chatbot only", code="api_key_required")
    for key in request.query_params.keys():
        if key not in _PARAMS:
            raise _unprocessable(f"Unknown parameter '{key}'", "unknown_param", key)

    if date_from is None or date_to is None:
        raise _unprocessable("date_from and date_to are required", "period_required")
    if not all(_MIN_YEAR <= d.year <= _MAX_YEAR for d in (date_from, date_to)):
        raise _unprocessable(
            f"Dates must fall in {_MIN_YEAR} to {_MAX_YEAR}", "date_out_of_range", "date_from, date_to"
        )
    if date_from > date_to:
        raise _unprocessable("date_from is after date_to", "date_range_inverted")

    basis_norm = (basis or "").strip().lower()
    if basis_norm not in ask.CATALOGUE:
        raise _unprocessable(f"Unknown basis '{basis}'", "unknown_basis", _allowed(ask.CATALOGUE))
    measure_norm = (measure or "").strip().lower()
    if measure_norm not in ask.MEASURES:
        raise _unprocessable(f"Unknown measure '{measure}'", "unknown_measure", _allowed(ask.MEASURES))
    sort_norm = (sort or "").strip().lower()
    if sort_norm not in ("desc", "asc"):
        raise _unprocessable(f"Unknown sort '{sort}'", "unknown_sort", _allowed(("desc", "asc")))
    group_norm = (group_by or "").strip().lower() or None
    if group_norm is not None and group_norm not in ask.CATALOGUE[basis_norm]["dimensions"]:
        raise _unprocessable(
            f"Unknown group_by value '{group_by}'", "unknown_group_by",
            _allowed(ask.CATALOGUE[basis_norm]["dimensions"]),
        )
    if group_norm is not None and top_n is None:
        raise _unprocessable("A ranking needs top_n", "top_n_required")
    if top_n is not None and not 1 <= top_n <= TOP_SELLING_N_CEILING:
        raise _unprocessable(f"top_n must be between 1 and {TOP_SELLING_N_CEILING}", "top_n_out_of_range")
    channel_norm = (channel or "").strip().lower() or None
    if channel_norm is not None and channel_norm not in _CHANNELS:
        raise _unprocessable(f"Unknown channel value '{channel}'", "invalid_channel", _allowed(_CHANNELS))
    code = (product_code or "").strip()
    if code and len(code) < 3:
        raise _unprocessable("product_code must be at least 3 characters", "product_code_too_short")

    named_customers = parse_uuid_list(customer_ids, param_name="customer_ids")
    named_products = parse_uuid_list(product_ids, param_name="product_ids")
    brands = parse_uuid_list(brand_ids, param_name="brand_ids")
    categories = parse_uuid_list(category_ids, param_name="category_ids")
    agents = parse_uuid_list(sales_agent_ids, param_name="sales_agent_ids")
    codes = _normalize_entities(warehouse_codes) or []
    resolved_filters = {
        "product_code": code,
        "product_ids": named_products,
        "brand_ids": brands,
        "category_ids": categories,
        "sales_agent_ids": agents,
        "customer_ids": named_customers,
        "warehouse_codes": codes,
        "channel": channel_norm,
    }
    for name in _FILTER_PARAMS:
        if name in request.query_params.keys() and not resolved_filters[name]:
            raise _unprocessable(f"'{name}' was given but names nothing", "empty_filter", name)
    for name in ("product_ids", "brand_ids", "category_ids", "sales_agent_ids", "customer_ids", "warehouse_codes"):
        if len(resolved_filters[name] or []) > _MAX_VALUES:
            raise _unprocessable(f"Too many values for '{name}' (max {_MAX_VALUES})", "too_many_values", name)

    # The reveal grant, off the resolved contact (the sales report route's own gate).
    from app.services.contact_field_reveal_service import granted_keys
    from app.services.field_access import resolve_contact_with_null_workspace_fallback

    resolved = resolve_contact_with_null_workspace_fallback(db, contact_id=contact_id, space_id=space_id)
    keys = granted_keys(db, resolved) if resolved else []
    if GRANT not in keys:
        raise AppException(403, "Sales report is not enabled for your account.", code="sales_report_not_enabled")

    common = {
        "basis": basis_norm,
        "basis_label": ask.CATALOGUE[basis_norm]["label"],
        "measure": measure_norm,
        "sort": sort_norm,
        "group_by": group_norm,
        "group_label": ask.GROUP_LABELS[group_norm] if group_norm else None,
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
    }

    from app.services import rate_limit

    if not rate_limit.hit(
        "report_ask", str(resolved), limit=_RATE_LIMIT, window_seconds=_RATE_WINDOW_SECONDS
    ).allowed:
        return _response(
            {**ask._empty("busy", "Too many reports in the last 10 minutes."), **common, "filters": []}
        )

    # Audience: a customer-scoped contact is a DEALER, forced to its own customers (a named
    # customer outside them is the scope helper's own 403).
    scoped = enforce_customer_scope(
        db,
        contact_id=contact_id,
        space_id=space_id,
        customer_ids=named_customers,
        customer_query=None,
    )
    if scoped is not None:
        outside = (
            (group_norm is not None and group_norm not in ask.DEALER_KEYS)
            or bool(agents)
            or bool(codes)
            or channel_norm is not None
        )
        if outside:
            raise AppException(403, _DEALER_MESSAGE, code="report_dimension_not_allowed")

    # The contact's OWN companies (the sales analysis route's read), never the session scope.
    grants = {
        str(c)
        for (c,) in db.query(RespondContactCompany.company_id)
        .filter(RespondContactCompany.respond_contact_id == str(resolved))
        .all()
        if c
    }

    # Matched inside the contact's companies, like every other lookup and the run itself.
    with company_scope(db, frozenset(grants)):
        products = _resolve_products(db, code) if code else []
    if code and not products:
        raise handle_not_found("Product", product_code)
    product_filter: Optional[list[str]] = [str(p.id) for p in products] if code else None
    product_names: list[str] = []
    if named_products:
        # Ids naming no product inside the contact's companies are 404; ANDed with the
        # prefix when both are given (an empty AND is zero rows in `run_ask`, never "no filter").
        by_id = dict(
            _lookup_pairs(db, Product, Product.product_code, named_products, grants, "Product")
        )
        if product_filter is None:
            product_filter = list(named_products)
        else:
            by_prefix = set(product_filter)
            product_filter = [i for i in named_products if i in by_prefix]
        product_names = sorted({by_id[i] for i in product_filter} or set(by_id.values()))
    echo = _echo(
        db,
        grants,
        product_code=code,
        product_names=product_names,
        customers=named_customers,
        brands=brands,
        categories=categories,
        agents=agents,
        warehouse_codes=codes,
        channel=channel_norm,
    )

    filters: dict[str, list[str]] = {}
    customers = scoped if scoped is not None else named_customers
    if customers:
        filters["customer"] = customers
    if product_filter is not None:
        filters["product"] = product_filter
    if brands:
        filters["brand"] = brands
    if categories:
        filters["category"] = categories
    if agents:
        filters["sales_agent"] = agents
    if channel_norm:
        filters["channel"] = [channel_norm]

    from app.services.stock_visibility import resolve_policy

    policy = resolve_policy(db, resolved, space_id) if resolved else None
    data = ask.run_ask(
        db,
        basis=basis_norm,
        measure=measure_norm,
        group_by=group_norm,
        date_from=date_from,
        date_to=date_to,
        filters=filters,
        warehouse_codes=codes,
        policy=policy,
        company_grants=grants,
        sort=sort_norm,
        top_n=top_n,
    )
    return _response({**data, **common, "filters": echo})
