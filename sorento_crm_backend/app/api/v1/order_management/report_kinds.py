"""The non-ranking measures behind `GET /api/v1/order-management/report` (REPORTS-STEP2 S4).

Each row adapts the old tool's route or service to
`run(db, *, params, contact_id, space_id, keys, principal_id)` (`keys` = the contact's granted
reveal keys, `principal_id` = the act-as user) and returns that route's own body, so every guard of the old route holds and the reply is the
old tool's. `params` maps a route param name to its raw query values (always lists).
"""
from __future__ import annotations

import asyncio
import json
from datetime import date
from typing import Any, Optional
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.services.error_handler import AppException
from app.services.reports import measures
from app.services.reports.measures import Measure

_PAIR = ("contact_id", "space_id")


def _one(params: dict, name: str) -> Optional[str]:
    values = params.get(name) or []
    return values[0] if values else None


def _many(params: dict, name: str) -> Optional[list[str]]:
    return list(params[name]) if params.get(name) else None


def _body(response: Any) -> dict:
    return json.loads(response.body)


# ------------------------------------------------------------------------- outstanding


def _outstanding(db: Session, *, params: dict, contact_id: str, space_id: str, keys=(), **_):
    """Without `sales_orders.outstanding` the SO side is withheld, as the lane does today: the DO
    block only, `so_refused` echoed."""
    scope = _one(params, "scope") or "both"
    so_refused = False
    if scope in ("so", "both") and "sales_orders.outstanding" not in keys:
        scope, so_refused = "do", True
    from app.api.v1.order_management.orders import get_outstanding_report

    response = asyncio.run(
        get_outstanding_report(
            product_code=_one(params, "product_code"),
            product_codes=None,
            scope=scope,
            customer_query=_one(params, "customer_query"),
            customer_ids=_many(params, "customer_ids"),
            warehouse_codes=_many(params, "warehouse_codes"),
            brand_ids=_many(params, "brand_ids"),
            order_date_from=None,
            order_date_to=None,
            detail=None,
            location_token=None,
            so_refused=so_refused,
            contact_id=contact_id,
            space_id=space_id,
            current_user={},
            db=db,
        )
    )
    return _body(response)


def _so_count(db: Session, *, params: dict, contact_id: str, space_id: str, **kw):
    """How many sales orders are outstanding for the subject: the outstanding report, SO side only."""
    return _outstanding(db, params={**params, "scope": ["so"]}, contact_id=contact_id, space_id=space_id, **kw)


# ------------------------------------------------------------------------- delivered sales


def _delivered_sales(db: Session, *, params: dict, contact_id: str, space_id: str, **_):
    from app.api.v1.order_management.orders import get_sales_report

    response = asyncio.run(
        get_sales_report(
            product_code=_one(params, "product_code"),
            customer_query=_one(params, "customer_query"),
            customer_ids=_many(params, "customer_ids"),
            channel=_one(params, "channel"),
            warehouse_codes=_many(params, "warehouse_codes"),
            location_token=None,
            date_from=_one(params, "date_from"),
            date_to=_one(params, "date_to"),
            group_by=_one(params, "group_by"),
            contact_id=contact_id,
            space_id=space_id,
            current_user={},
            db=db,
        )
    )
    return _body(response)


# ------------------------------------------------------------------------- sales value


#: `group_by` -> the analysis `rows`, as the lane maps it (`fetch.SALES_ANALYSIS_GROUP_BY`): the years
#: are always across; "year" and no axis are one line per channel.
_SALES_VALUE_ROWS = {"": "channel", "year": "channel", "channel": "channel", "month": "month"}


def _sales_value(db: Session, *, params: dict, contact_id: str, space_id: str, **_):
    from app.api.v1.sales import analysis

    def _date(name: str) -> Optional[date]:
        raw = _one(params, name)
        try:
            return date.fromisoformat(raw) if raw else None
        except ValueError:
            raise AppException(422, f"{name} must be an ISO date", code="invalid_date")

    top_n = _one(params, "top_n")
    try:
        n = int(top_n) if top_n else None
    except ValueError:
        raise AppException(422, "top_n must be a number", code="invalid_top_n")
    read = analysis.read_answer(
        db,
        rows=_SALES_VALUE_ROWS.get((_one(params, "group_by") or "").strip().lower(), "channel"),
        cols="year",
        channel=(_one(params, "channel") or "").strip().lower() or None,
        basis=(_one(params, "basis") or "").strip().lower() or None,
        company=_one(params, "company"),
        date_from=_date("date_from"),
        date_to=_date("date_to"),
        n=n,
        contact_id=contact_id,
        space_id=space_id,
    )
    return read if isinstance(read, dict) else read.answer


# ------------------------------------------------------------------------- low stock


def _kl_date(stamp) -> str:
    """The Kuala Lumpur calendar date of a stored run time (naive values are UTC, as the column is)."""
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=ZoneInfo("UTC"))
    return stamp.astimezone(ZoneInfo("Asia/Kuala_Lumpur")).date().isoformat()


def _low_stock(db: Session, *, params: dict, contact_id: str, space_id: str, principal_id: str = "", **_):
    """The run the reorder page opens to (today's, else the latest completed), as a read view.
    Reads only: no run, download row or job."""
    from app.api.v1.scm.low_stock_report import SUPPLIER_GRANT, _adopt_company_scope, _csv_list, _name_list
    from app.services.contact_field_reveal_service import granted_keys
    from app.services.field_access import resolve_contact_with_null_workspace_fallback
    from app.services.scm import low_stock_report_service, reorder_run_service

    resolved = resolve_contact_with_null_workspace_fallback(db, contact_id=contact_id, space_id=space_id)
    include_supplier = SUPPLIER_GRANT in (granted_keys(db, resolved) if resolved else [])
    suppliers = _name_list(params.get("suppliers"))
    categories = _csv_list(params.get("categories"))
    if suppliers and not include_supplier:
        # The Supplier column is hidden from this contact; filtering by it would leak what it hides.
        return {"status": "error", "message": "Could not run the low stock report right now."}
    # One company, chosen as the old route does, and the pick runs under it.
    from sqlalchemy import text

    owner = str(db.execute(text(
        "SELECT id FROM users WHERE respond_contact_id = :c AND status = 'ACTIVE' "
        "ORDER BY created_at, id LIMIT 1"
    ), {"c": str(resolved)}).scalar() or principal_id)
    if _adopt_company_scope(db, resolved_contact_id=str(resolved) if resolved else None, owner_user_id=owner) is None:
        return {"status": "error", "message": "Could not run the low stock report right now."}
    picked = reorder_run_service.today_or_latest_run(db)
    if picked is None:
        return {"status": "no_snapshot", "message": "There is no low stock snapshot yet."}
    run = picked["row"]
    if run["status"] != "completed":
        # Only a first plan being built right now reaches here (a completed run is preferred).
        return {"status": "preparing", "message": "The low stock snapshot is still being prepared."}
    # Without the Supplier column the view's default supplier split is refused, so it is not split.
    split = {} if include_supplier else {"split": "none"}
    view = low_stock_report_service.build_low_stock_view(
        db, run_id=str(run["id"]), include_supplier=include_supplier, suppliers=suppliers,
        categories=categories, **split,
    )
    body = json.loads(json.dumps(view, default=str))
    if not include_supplier:
        # The Supplier column is hidden from this contact, and so is every supplier name.
        body.get("facets", {}).pop("suppliers", None)
    stamp = run.get("started_at") or run.get("finished_at")
    body["run_date"] = _kl_date(stamp) if stamp else (str(body.get("run", {}).get("as_of") or "")[:10] or None)
    return body


# ------------------------------------------------------------------------- the rows

_OUTSTANDING_PARAMS = frozenset({"product_code", "customer_query", "customer_ids", "warehouse_codes", "brand_ids", "scope"})

for _row in (
    Measure(
        key="order_outstanding", label="Outstanding orders by subject", synonyms=("outstanding", "backlog"),
        basis="outstanding", figure="qty", dimensions=(), grant="sales_orders.outstanding",
        dealer_dimensions=(), run=_outstanding, kind="outstanding", params=_OUTSTANDING_PARAMS,
        grant_optional=True,
    ),
    Measure(
        key="so_count", label="Outstanding sales order count", synonyms=("how many sales orders",),
        basis="outstanding", figure="count", dimensions=(), grant="sales_orders.outstanding",
        dealer_dimensions=(), run=_so_count, kind="outstanding", params=_OUTSTANDING_PARAMS - {"scope"},
    ),
    Measure(
        key="delivered_sales", label="Delivered sales", synonyms=("sales report", "delivered"),
        basis="delivered", figure="amount", dimensions=("customer", "product", "delivery_order", "sales_agent"),
        grant="sales_orders.sales_report", dealer_dimensions=("customer", "product", "delivery_order"),
        run=_delivered_sales, kind="sales_report",
        params=frozenset({"product_code", "customer_query", "customer_ids", "channel", "warehouse_codes",
                          "date_from", "date_to", "group_by"}),
    ),
    Measure(
        key="sales_value", label="Sales value", synonyms=("sales analysis", "sales by month"),
        basis="ordered", figure="amount", dimensions=("month", "year", "channel"),
        grant="sales_orders.sales_report", dealer_dimensions=(), run=_sales_value, kind="sales_analysis", module_key="sales", permission="sales.reports.view",
        params=frozenset({"group_by", "basis", "channel", "company", "date_from", "date_to", "top_n"}),
    ),
    Measure(
        key="low_stock", label="Low stock", synonyms=("low stock", "reorder"),
        basis="stock", figure="qty", dimensions=(), grant="scm.low_stock_report", dealer_dimensions=(),
        run=_low_stock, kind="low_stock", params=frozenset({"categories", "suppliers"}),
        module_key="scm", permission="scm.reorder.run",
    ),
):
    measures.register(_row)
