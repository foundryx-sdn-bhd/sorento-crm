"""Tool registration: the 34 reused catalog tools and the five chat tools (card section 4)."""
from __future__ import annotations

import functools
import json
import logging
import time
from typing import Any, Optional

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from sorento_crm_mcp.catalog import CATALOG
from sorento_crm_mcp.module_loader import merged_catalog
from sorento_crm_mcp.server import _compile_tool

from sorento_analytics_mcp.cap import cap_text, count_rows

logger = logging.getLogger(__name__)

BE = "/api/v1/analytics-connector"

# Card 4.2. A static list on purpose: a catalog edit can never widen what the connector exposes.
# Removed in the security round (card 4.2 table): crm_low_stock_report, crm_report_ask,
# crm_sales_analysis, crm_complaints_list, crm_resource_attachments_current_stock_list,
# crm_sla_conversation_tracking_list.
ANALYTICS_REUSED_TOOLS: frozenset[str] = frozenset({
    # Master data
    "crm_master_products_list", "crm_master_brands_list", "crm_master_product_categories_list",
    "crm_master_units_of_measure_list", "crm_master_product_attachments_list", "crm_certificates_list",
    "crm_master_customers_list", "crm_lookup_resolve",
    # Stock / incoming
    "crm_inventory_stock_balance_list", "crm_inventory_warehouses_list",
    "crm_incoming_stock_by_product", "crm_incoming_stock_shipments", "crm_incoming_stock_list",
    # Sales / orders
    "crm_order_management_orders_list", "crm_order_management_orders_by_product_list",
    "crm_outstanding_report", "crm_sales_report", "crm_top_selling_report",
    # Procurement
    "crm_procurement_po_placed_list", "crm_procurement_spo_allocations_last_receipt_list",
    "crm_procurement_po_last_cost_list",
    # Marketing / resources
    "crm_marketing_promotions_list", "crm_marketing_promotion_products_list",
    "crm_marketing_promotion_attachments_list", "crm_resource_attachments_list",
    "crm_resource_attachments_catalogue",
    # Projects
    "crm_projects_list", "crm_project_detail", "crm_project_quotations_list", "crm_project_forecast",
    # Complaints / forms / SLA
    "crm_complaint_analytics", "crm_forms_management_forms_list",
    "crm_sla_conversation_tracking_dashboard", "crm_sla_conversation_event_logs_list",
})

# Presigned URLs are credentials (owner ruling R2): this argument is never forwarded.
_DROPPED_ARGS = ("resolve_signed_urls",)

# The backend's dataset keys (app/services/analytics_connector/datasets.py). A key outside this set
# never reaches the backend, so a tool argument cannot steer the request path.
DATASET_KEYS: frozenset[str] = frozenset({"messages", "contacts", "bot_turns", "episodes"})

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False)


async def _no_escalation(_tool_name: str, response: str, **_kwargs: Any) -> str:
    """Replaces the template's escalation side call, which would use the shared API key."""
    return response


def _is_error(text: str) -> bool:
    try:
        obj = json.loads(text)
    except ValueError:
        return False
    return isinstance(obj, dict) and (int(obj.get("status_code") or 0) >= 400 or obj.get("error") == "request_failed")


def _wrap_reused(impl, name: str):
    """Cap the answer, then audit the call (card 6). Fail closed: no audit row, no data."""

    @functools.wraps(impl)
    async def wrapper(*args: Any, **kwargs: Any) -> str:
        ctx = kwargs.get("ctx")
        for arg in _DROPPED_ARGS:
            if arg in kwargs:
                kwargs[arg] = None
        started = time.perf_counter()
        status = "ok"
        try:
            text = await impl(*args, **kwargs)
        except Exception:
            status = "error"
            await _audit(ctx, name, kwargs, status, started, None, 0)
            raise
        text = cap_text(text)
        if _is_error(text):
            status = "error"
        if not await _audit(ctx, name, kwargs, status, started, count_rows(text), len(text)):
            raise ToolError("audit_unavailable: the call was not recorded, so no data is returned. Try again later.")
        return text

    return wrapper


async def _audit(ctx: Optional[Context], tool: str, kwargs: dict, status: str, started: float,
                 rows: Optional[int], chars: int) -> bool:
    """POST the audit row; True only when the backend accepted it."""
    try:
        client = ctx.request_context.lifespan_context["client"]  # type: ignore[union-attr]
        resp = await client.post(f"{BE}/audit", body={
            "tool": tool,
            "args": {k: v for k, v in kwargs.items() if k != "ctx" and v is not None},
            "status": status,
            "ms": int((time.perf_counter() - started) * 1000),
            "rows": rows,
            "chars": chars,
        })
    except Exception as exc:  # noqa: BLE001 - reported to the caller as a failed audit
        logger.warning("analytics audit post failed: %s", type(exc).__name__)
        return False
    # The route answers 204 (empty body). Every CRMClient failure (HTTP >= 400, transport error,
    # oversize, bad encoding) is a JSON object with an "error" key.
    if _has_error_key(resp):
        logger.warning("analytics audit post refused")
        return False
    return True


def _has_error_key(text: Any) -> bool:
    if not isinstance(text, str) or not text.strip():
        return False
    try:
        obj = json.loads(text)
    except ValueError:
        return False
    return isinstance(obj, dict) and bool(obj.get("error"))


def register_reused_tools(mcp: FastMCP) -> int:
    by_name = {t.name: t for t in merged_catalog(CATALOG)}
    missing = ANALYTICS_REUSED_TOOLS - by_name.keys()
    if missing:
        raise RuntimeError(f"reused tools missing from the catalog: {sorted(missing)}")
    for name in sorted(ANALYTICS_REUSED_TOOLS):
        spec = by_name[name]
        if not (spec.method.upper() == "GET" or spec.read_only) or spec.external or spec.internal:
            raise RuntimeError(f"{name} is not a read-only catalog tool")
        impl = _compile_tool(spec)
        impl.__globals__["_attach_suggested_escalation"] = _no_escalation
        mcp.add_tool(_wrap_reused(impl, name), name=name, description=spec.description, annotations=READ_ONLY)
    return len(ANALYTICS_REUSED_TOOLS)


def _client(ctx: Context):
    return ctx.request_context.lifespan_context["client"]


def _given(**values: Any) -> dict[str, Any]:
    return {k: v for k, v in values.items() if v is not None}


def register_chat_tools(mcp: FastMCP) -> None:
    """Thin wrappers over the backend routes; the backend audits these itself."""

    async def chat_list_datasets(ctx: Context) -> str:
        return cap_text(await _client(ctx).get(f"{BE}/datasets", tool_name="chat_list_datasets"))

    async def chat_describe_dataset(ctx: Context, dataset: str) -> str:
        if dataset not in DATASET_KEYS:
            raise ToolError(f"unknown dataset; use one of: {', '.join(sorted(DATASET_KEYS))}")
        return cap_text(await _client(ctx).get(
            f"{BE}/datasets/{{dataset}}", path_params={"dataset": dataset}, tool_name="chat_describe_dataset"))

    async def chat_query(
        ctx: Context,
        dataset: str,
        columns: Optional[list[str]] = None,
        filters: Optional[list[dict[str, Any]]] = None,
        group_by: Optional[list[str]] = None,
        measures: Optional[list[dict[str, Any]]] = None,
        period: Optional[dict[str, str]] = None,
        order_by: Optional[list[dict[str, Any]]] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> str:
        body = _given(dataset=dataset, columns=columns, filters=filters, group_by=group_by, measures=measures,
                      period=period, order_by=order_by, limit=limit, offset=offset)
        return cap_text(await _client(ctx).post(f"{BE}/query", body=body, tool_name="chat_query"))

    async def chat_search_messages(
        ctx: Context,
        text: Optional[str] = None,
        contact: Optional[str] = None,
        customer: Optional[str] = None,
        direction: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> str:
        query = _given(text=text, contact=contact, customer=customer, direction=direction,
                       date_from=date_from, date_to=date_to, limit=limit, offset=offset)
        return cap_text(await _client(ctx).get(f"{BE}/messages/search", query=query, tool_name="chat_search_messages"))

    async def chat_get_conversation(
        ctx: Context,
        contact: str,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> str:
        query = _given(contact=contact, date_from=date_from, date_to=date_to, limit=limit, offset=offset)
        return cap_text(await _client(ctx).get(f"{BE}/conversation", query=query, tool_name="chat_get_conversation"))

    steer = (
        " Start with group_by and measures for counts and trends; pull raw message text only for samples,"
        " because the whole history is far larger than one answer."
    )
    for fn, desc in (
        (chat_list_datasets, "List the chat datasets (messages, contacts, bot_turns, episodes) with row counts and date ranges."),
        (chat_describe_dataset, "Describe one dataset: its columns (type, filterable, groupable, measurable) and three sample rows."),
        (chat_query,
         "Query a chat dataset with a whitelist builder: columns, filters {column, op, value} (ops eq ne in not_in gt gte"
         " lt lte contains is_null not_null), group_by (a column or a date bucket like sent_at:month|day|week|hour|dow|"
         "hour_of_day), measures {fn count|count_distinct|sum|avg|min|max, column}, period {from, to}, order_by, limit"
         " (max 1000), offset. Times are Malaysia time." + steer),
        (chat_search_messages,
         "Search message text (case-insensitive contains) with optional contact, customer, direction, date range."
         " Newest first, max 200 per page." + steer),
        (chat_get_conversation,
         "One contact's thread, oldest first, with bot turn status. contact may be an id, a Respond.io id or a name;"
         " several matches return a candidate list."),
    ):
        mcp.add_tool(fn, name=fn.__name__, description=desc, annotations=READ_ONLY)
