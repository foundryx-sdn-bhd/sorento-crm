"""`GET /api/v1/order-management/report-ask`: the chatbot's one flexible sales report (lane
REPORT-ENGINE, slice 1a; `documentation/plans/chatbot/PLAN-report-engine.md` section 0 and 10).

A ranking (one `group_by`) or a number over delivered, ordered or outstanding sales (all from
sales order lines), for a required period, with the common filters. Every gate runs here, from
`contact_id`, never in the lane or the prompt: the API key, the `sales_orders.sales_report`
reveal grant, the audience (a customer-scoped contact is a DEALER and may only slice by product,
brand, category, month), the contact's stock visibility policy over locations, and the engine's
own company arm.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import false, or_, text
from sqlalchemy.orm import Session

from app.api.v1.order_management._contact_scope import (
    enforce_customer_scope,
    require_contact_identity_pair,
)
from app.api.v1.order_management.orders import _normalize_entities
from app.database import get_db
from app.dependencies import require_permission_with_api_key
from app.models.base import company_scope, set_brand_scope
from app.models.company import RespondContactCompany
from app.models.order import Customer
from app.models.product import Brand, Product, ProductCategory
from app.models.sales_agent import SalesAgent
from app.schemas.report_ask import ReportAskChatResponse, ReportAskResponse
from app.services.contact_brand_scope import contact_brand_scope
from app.services.error_handler import AppException, handle_not_found
from app.services.reports import ask
from app.services.sales_report_service import TOP_SELLING_N_CEILING, _resolve_products
from app.services.uuid_list_param import parse_uuid_list

router = APIRouter()

GRANT = "sales_orders.sales_report"
#: The cap's defaults, used only when the settings row is missing: the live numbers are
#: `system_settings.report_ask_rate_limit` / `report_ask_rate_window_minutes`.
_DEFAULT_RATE_LIMIT = 10
_DEFAULT_RATE_WINDOW_MINUTES = 10
_DEALER_MESSAGE = "That breakdown is not available for your account."
_CHANNELS = {"dealer": "Dealer", "project": "Project team"}
#: The route's default basis for a direct API caller that names none. The chatbot lane never
#: relies on it: it asks the basis and always sends one.
DEFAULT_BASIS = "delivered"
_MAX_VALUES = 50
_MIN_YEAR, _MAX_YEAR = 1900, 2200

#: Every query key the route declares; any other is 422 `unknown_param`.
_PARAMS = frozenset(
    {
        "date_from", "date_to", "basis", "measure", "group_by", "top_n", "sort", "product_code",
        "product_ids", "brand_ids", "category_ids", "sales_agent_ids", "customer_ids", "warehouse_codes",
        "channel", "contact_id", "space_id", "customer_agent_ids", "dataset",
    }
)
#: The chat dataset's own query keys (CA-3); any other is 422 `unknown_param`.
_CHAT_PARAMS = frozenset(
    {
        "dataset", "date_from", "date_to", "basis", "measure", "group_by", "top_n", "sort", "subjects",
        "product_ids", "customer_ids", "contact_id", "space_id",
    }
)
CHAT_GRANT = "chatbot.analytics"
_CHAT_DEFAULT_BASIS = "asks"
_CHAT_DEFAULT_MEASURE = "asks"
#: The filter params: present but resolving to nothing is 422 `empty_filter`, never "no filter".
_FILTER_PARAMS = (
    "product_code", "product_ids", "brand_ids", "category_ids", "sales_agent_ids", "customer_ids",
    "warehouse_codes", "channel", "customer_agent_ids",
)
#: The echo's name for the "(no agent)" key in an agent filter (REPORT-DRILLDOWN).
_NO_AGENT_NAME = "(no agent)"


def _agent_values(raw: Optional[list[str]], param_name: str) -> Optional[list[str]]:
    """An agent id list that may also name `ask.NO_AGENT_KEY` ("no agent", the drill down of a
    "(no agent)" row): the parsed ids, plus the key when named. None when nothing is given."""
    if raw is None:
        return None
    pieces = [p.strip() for item in raw for p in str(item or "").split(",") if p.strip()]
    none = any(p.lower() == ask.NO_AGENT_KEY for p in pieces)
    ids = parse_uuid_list([p for p in pieces if p.lower() != ask.NO_AGENT_KEY], param_name=param_name) or []
    out = ids + ([ask.NO_AGENT_KEY] if none else [])
    return out or None


def _rate_limit_setting(db: Session) -> tuple[int, int]:
    """`(limit, window_minutes)` from System Settings, read per request so a change on the
    Chatbot media page applies on the next report with no restart. A limit of 0 means no limit."""
    from app.models.user import SystemSetting

    row = db.query(SystemSetting).first()
    limit = getattr(row, "report_ask_rate_limit", None) if row else None
    window = getattr(row, "report_ask_rate_window_minutes", None) if row else None
    return (
        _DEFAULT_RATE_LIMIT if limit is None else int(limit),
        int(window) if window else _DEFAULT_RATE_WINDOW_MINUTES,
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
    card_agents: Optional[list[str]] = None,
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
    for key, values in (("sales_agent", agents), ("customer_agent", card_agents)):
        if values:
            ids = [v for v in values if v != ask.NO_AGENT_KEY]
            names = _lookup(db, SalesAgent, SalesAgent.sales_agent, ids, grants, "Sales agent", shared=True) if ids else []
            out.append((key, sorted(names + ([_NO_AGENT_NAME] if ask.NO_AGENT_KEY in values else []))))
    if warehouse_codes:
        out.append(("location", list(warehouse_codes)))
    if channel:
        out.append(("channel", [_CHANNELS[channel]]))
    return [{"key": key, "label": ask.FILTER_LABELS[key], "values": values} for key, values in out if values]


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
    customer_agent_ids: Optional[list[str]] = Query(
        None,
        description="Customers whose assigned agent (the customer card) is one of these; 'none' = no agent.",
    ),
    warehouse_codes: Optional[list[str]] = Query(None),
    channel: Optional[str] = Query(None, description="dealer | project"),
    dataset: str = Query("sales", description="sales (default) | chat."),
    subjects: Optional[list[str]] = Query(None, description="Chat only: subject (domain) codes."),
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
    dataset_norm = (dataset or "").strip().lower()
    if dataset_norm not in ask.DATASETS:
        raise _unprocessable(f"Unknown dataset '{dataset}'", "unknown_dataset", _allowed(ask.DATASETS))
    if dataset_norm == "chat":
        return _chat_report_ask(
            request, db, date_from=date_from, date_to=date_to, basis=basis, measure=measure,
            group_by=group_by, top_n=top_n, sort=sort, subjects=subjects, product_ids=product_ids,
            customer_ids=customer_ids, contact_id=contact_id, space_id=space_id,
        )
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
    if basis_norm not in ask.CATALOGUE["sales"]:
        raise _unprocessable(f"Unknown basis '{basis}'", "unknown_basis", _allowed(ask.CATALOGUE["sales"]))
    measure_norm = (measure or "").strip().lower()
    if measure_norm not in ask.MEASURES:
        raise _unprocessable(f"Unknown measure '{measure}'", "unknown_measure", _allowed(ask.MEASURES))
    sort_norm = (sort or "").strip().lower()
    if sort_norm not in ("desc", "asc"):
        raise _unprocessable(f"Unknown sort '{sort}'", "unknown_sort", _allowed(("desc", "asc")))
    group_norm = (group_by or "").strip().lower() or None
    if group_norm is not None and group_norm not in ask.CATALOGUE["sales"][basis_norm]["dimensions"]:
        raise _unprocessable(
            f"Unknown group_by value '{group_by}'", "unknown_group_by",
            _allowed(ask.CATALOGUE["sales"][basis_norm]["dimensions"]),
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
    agents = _agent_values(sales_agent_ids, "sales_agent_ids")
    card_agents = _agent_values(customer_agent_ids, "customer_agent_ids")
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
        "customer_agent_ids": card_agents,
    }
    for name in _FILTER_PARAMS:
        if name in request.query_params.keys() and not resolved_filters[name]:
            raise _unprocessable(f"'{name}' was given but names nothing", "empty_filter", name)
    for name in (
        "product_ids", "brand_ids", "category_ids", "sales_agent_ids", "customer_ids", "warehouse_codes",
        "customer_agent_ids",
    ):
        if len(resolved_filters[name] or []) > _MAX_VALUES:
            raise _unprocessable(f"Too many values for '{name}' (max {_MAX_VALUES})", "too_many_values", name)

    # The reveal grant, off the resolved contact (the sales report route's own gate).
    from app.services.contact_field_reveal_service import granted_keys
    from app.services.field_access import resolve_contact_with_null_workspace_fallback

    resolved = resolve_contact_with_null_workspace_fallback(db, contact_id=contact_id, space_id=space_id)
    keys = granted_keys(db, resolved) if resolved else []
    if GRANT not in keys:
        raise AppException(403, "Sales report is not enabled for your account.", code="sales_report_not_enabled")

    # Brand scope off the contact this route already resolved, whatever the key: the router
    # dependency stamps it only for the legacy shared key (security H1). Stamped before any
    # query below, the echo lookups included. Unscoped: nothing set.
    brand_scope = contact_brand_scope(db, contact_id, space_id)
    if brand_scope:
        set_brand_scope(db, brand_scope)

    common = {
        "basis": basis_norm,
        "basis_label": ask.CATALOGUE["sales"][basis_norm]["label"],
        "measure": measure_norm,
        "sort": sort_norm,
        "group_by": group_norm,
        "group_label": ask.GROUP_LABELS[group_norm] if group_norm else None,
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
    }

    from app.services import rate_limit

    limit, window_minutes = _rate_limit_setting(db)
    if not rate_limit.hit(
        "report_ask", str(resolved), limit=limit, window_seconds=window_minutes * 60
    ).allowed:
        unit = "1 minute" if window_minutes == 1 else f"{window_minutes} minutes"
        return _response(
            {**ask._empty("busy", f"Too many reports in the last {unit}."), **common, "filters": []}
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
            or bool(card_agents)
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

    if brands:
        # A brand named by id that belongs to a company outside the contact's own is dropped
        # silently (one brand name can exist per company); 404 only when none remain, so
        # nothing tells the caller another company's brand exists, and none is ever echoed.
        cond = or_(Brand.company_id.is_(None), Brand.company_id.in_(sorted(grants))) if grants else Brand.company_id.is_(None)
        with company_scope(db, None):
            own = {str(i) for (i,) in db.query(Brand.id).filter(Brand.id.in_(brands), cond).all()}
        brands = [b for b in brands if b in own]
        if not brands:
            raise handle_not_found("Brand", brand_ids[0] if brand_ids else "")

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
        card_agents=card_agents,
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
    if card_agents:
        filters["customer_agent"] = card_agents
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


# ----------------------------------------------------------------------- chat (CA-3)


def _chat_response(data: dict) -> JSONResponse:
    return JSONResponse(content=ReportAskChatResponse(**data).model_dump(mode="json"))


def _chat_report_ask(
    request: Request,
    db: Session,
    *,
    date_from: Optional[date],
    date_to: Optional[date],
    basis: str,
    measure: str,
    group_by: Optional[str],
    top_n: Optional[int],
    sort: str,
    subjects: Optional[list[str]],
    product_ids: Optional[list[str]],
    customer_ids: Optional[list[str]],
    contact_id: str,
    space_id: str,
):
    """`dataset=chat`: the bot's chat analytics (asks, turns, failures, contacts). Same gates
    as the sales report in the same order, with the `chatbot.analytics` reveal in place of the
    sales one, and a dealer or salesman held to their own customers' asks."""
    present = request.query_params.keys()
    for key in present:
        if key not in _CHAT_PARAMS:
            raise _unprocessable(f"Unknown parameter '{key}'", "unknown_param", key)
    if date_from is None or date_to is None:
        raise _unprocessable("date_from and date_to are required", "period_required")
    if not all(_MIN_YEAR <= d.year <= _MAX_YEAR for d in (date_from, date_to)):
        raise _unprocessable(
            f"Dates must fall in {_MIN_YEAR} to {_MAX_YEAR}", "date_out_of_range", "date_from, date_to"
        )
    if date_from > date_to:
        raise _unprocessable("date_from is after date_to", "date_range_inverted")

    basis_norm = (basis if "basis" in present else _CHAT_DEFAULT_BASIS).strip().lower()
    if basis_norm not in ask.CATALOGUE["chat"]:
        raise _unprocessable(f"Unknown basis '{basis}'", "unknown_basis", _allowed(ask.CATALOGUE["chat"]))
    measure_norm = (measure if "measure" in present else _CHAT_DEFAULT_MEASURE).strip().lower()
    if measure_norm not in ask.CHAT_MEASURES:
        raise _unprocessable(f"Unknown measure '{measure}'", "unknown_measure", _allowed(ask.CHAT_MEASURES))
    sort_norm = (sort or "").strip().lower()
    if sort_norm not in ("desc", "asc"):
        raise _unprocessable(f"Unknown sort '{sort}'", "unknown_sort", _allowed(("desc", "asc")))
    group_norm = (group_by or "").strip().lower() or None
    if group_norm is not None and group_norm not in ask.CHAT_DIMENSIONS:
        raise _unprocessable(
            f"Unknown group_by value '{group_by}'", "unknown_group_by", _allowed(ask.CHAT_DIMENSIONS)
        )
    if top_n is not None and not 1 <= top_n <= TOP_SELLING_N_CEILING:
        raise _unprocessable(f"top_n must be between 1 and {TOP_SELLING_N_CEILING}", "top_n_out_of_range")

    subject_codes = [
        p.strip().lower() for item in (subjects or []) for p in str(item or "").split(",") if p.strip()
    ]
    named_customers = parse_uuid_list(customer_ids, param_name="customer_ids")
    named_products = parse_uuid_list(product_ids, param_name="product_ids")
    for name, values in (("subjects", subject_codes), ("customer_ids", named_customers), ("product_ids", named_products)):
        if name in present and not values:
            raise _unprocessable(f"'{name}' was given but names nothing", "empty_filter", name)
        if len(values or []) > _MAX_VALUES:
            raise _unprocessable(f"Too many values for '{name}' (max {_MAX_VALUES})", "too_many_values", name)

    if subject_codes:
        known = {n for (n,) in db.execute(text("SELECT name FROM chatbot_domains")).all()}
        # No domain rows (a blank schema) leaves nothing to check against.
        unknown = [c for c in subject_codes if known and c not in known]
        if unknown:
            raise _unprocessable(f"Unknown subject '{unknown[0]}'", "unknown_subject", unknown[0])

    from app.services.contact_field_reveal_service import granted_keys
    from app.services.field_access import resolve_contact_with_null_workspace_fallback

    contact_id, space_id = contact_id.strip(), space_id.strip()
    resolved = resolve_contact_with_null_workspace_fallback(db, contact_id=contact_id, space_id=space_id)
    keys = granted_keys(db, resolved) if resolved else []
    if CHAT_GRANT not in keys:
        raise AppException(403, "Chat analytics is not enabled for your account.", code="chat_analytics_not_enabled")

    if measure_norm in ("failed", "failure_rate"):
        basis_norm = "turns"  # a failure is counted on messages, never on split asks
    unit = {"contacts": "contacts", "failure_rate": "%"}.get(measure_norm, basis_norm)
    common = {
        "dataset": "chat",
        "basis": basis_norm,
        "measure": measure_norm,
        "unit": unit,
        "sort": sort_norm,
        "group_by": group_norm,
        "group_label": ask.CHAT_GROUP_LABELS[group_norm] if group_norm else None,
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
    }

    from app.services import rate_limit

    limit, window_minutes = _rate_limit_setting(db)
    if not rate_limit.hit(
        "report_ask", str(resolved), limit=limit, window_seconds=window_minutes * 60
    ).allowed:
        minutes = "1 minute" if window_minutes == 1 else f"{window_minutes} minutes"
        return _chat_response(
            {
                "status": "busy", "message": f"Too many reports in the last {minutes}.", **common,
                "total": {"value": 0},
            }
        )

    # A dealer is forced to its own customers (a named one outside them is the helper's own
    # 403) and to the dealer's breakdowns; a salesman to the customers whose card agent is theirs.
    scoped = enforce_customer_scope(
        db, contact_id=contact_id, space_id=space_id, customer_ids=named_customers, customer_query=None
    )
    if scoped is not None and group_norm is not None and group_norm not in ask.CHAT_DEALER_KEYS:
        raise AppException(403, _DEALER_MESSAGE, code="report_dimension_not_allowed")
    customers = scoped if scoped is not None else named_customers
    if scoped is None:
        with company_scope(db, None):
            agent_ids = [str(i) for (i,) in db.query(SalesAgent.id).filter(SalesAgent.contact_id == str(resolved)).all()]
            if agent_ids:
                own = {
                    str(i) for (i,) in db.query(Customer.id).filter(Customer.sales_agent_id.in_(agent_ids)).all()
                }
                customers = [c for c in (named_customers or own) if c in own]

    grants = {
        str(c)
        for (c,) in db.query(RespondContactCompany.company_id)
        .filter(RespondContactCompany.respond_contact_id == str(resolved))
        .all()
        if c
    }
    echo: list[tuple[str, list[str]]] = []
    if subject_codes:
        from app.services.reports.datasets import chat_asks

        words = dict(chat_asks.subject_options(db))
        echo.append(("subject", sorted(words.get(c, c) for c in subject_codes)))
    if named_products:
        echo.append(("product", _lookup(db, Product, Product.product_code, named_products, grants, "Product")))
    if named_customers:
        # Only the customers left after scoping: a name outside the contact's own is never echoed.
        shown = [c for c in named_customers if c in set(customers)]
        if shown:
            echo.append(("customer", _lookup(db, Customer, Customer.customer_name, shown, grants, "Customer")))

    filters: dict[str, list[str]] = {}
    if subject_codes:
        filters["subject"] = subject_codes
    if named_products:
        filters["product"] = named_products
    if scoped is not None or named_customers or customers != named_customers:
        filters["customer"] = customers

    data = ask.run_ask(
        db,
        dataset="chat",
        basis=basis_norm,
        measure=measure_norm,
        group_by=group_norm,
        date_from=date_from,
        date_to=date_to,
        filters=filters,
        warehouse_codes=[],
        policy=None,
        sort=sort_norm,
        top_n=top_n,
    )
    return _chat_response(
        {
            **data,
            **common,
            "date_from": data["date_from"],
            "date_to": data["date_to"],
            "filters": [
                {"key": key, "label": ask.CHAT_FILTER_LABELS[key], "values": values} for key, values in echo if values
            ],
        }
    )
