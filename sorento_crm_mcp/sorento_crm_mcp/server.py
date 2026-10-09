"""FastMCP server (streamable HTTP) with catalog-registered read-only tools."""
from __future__ import annotations

import logging
import json
import re
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo
from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from sorento_crm_mcp.catalog import CATALOG, ToolSpec
from sorento_crm_mcp.escalation_hint import attach_suggested_escalation
from sorento_crm_mcp.http_client import CRMClient
from sorento_crm_mcp.module_loader import merged_catalog
from sorento_crm_mcp.presenters import PRESENTER_TOOLS, present_response
from sorento_crm_mcp.ideation import register_ideation_tools
from sorento_crm_mcp.record_actions import register_record_action_tools
from sorento_crm_mcp.settings import Settings
from sorento_crm_mcp.user_guides import register_user_guide_tools

logger = logging.getLogger(__name__)

TOOL_QUERY_ALIASES: dict[str, dict[str, tuple[str, ...]]] = {}

TOOL_REQUIRED_QUERY_HINTS: dict[str, tuple[str, ...]] = {
    # The low stock report is per-CONTACT: the route reads that contact's reveal key
    # before it creates anything and answers 422 without the pair, so both ids have to
    # be no-default arguments or the LLM reads them as optional and skips them.
    "crm_low_stock_report": ("contact_id", "space_id"),
    # The sales analysis is per contact too: their company, their key, their chat.
    "crm_sales_analysis": ("contact_id", "space_id"),
    # The report ask needs the contact pair and a period (422 period_required otherwise).
    "crm_report_ask": ("contact_id", "space_id", "date_from", "date_to"),
}

# Parent-relation tools: meaningless without a parent entity UUID.
# These are "list X belonging to parent Y" tools, not general browse lists.
# Without a narrowing key, short-circuit to empty page (and let the agent know
# via tool description that the param is required).
TOOL_REQUIRED_NARROWING_FILTERS: dict[str, tuple[str, ...]] = {
    # certificate_ids is a narrowing key in its own right. Without it listed
    # here, "the files for this certificate" short-circuits to an empty page
    # even though the filter itself works - a silent wrong answer.
    "crm_master_product_attachments_list": ("product_ids", "attachment_ids", "certificate_ids"),
    "crm_marketing_promotion_products_list": ("promotion_ids", "product_ids"),
    "crm_marketing_promotion_attachments_list": ("promotion_ids", "attachment_ids"),
    "crm_order_management_orders_by_product_list": ("product_ids",),
    "crm_incoming_stock_by_product": ("product_ids",),
    # Shipments without any narrower returns the entire open inbound list - too
    # broad for an AI answer. Require shipment / supplier UUID or an ETA window
    # so questions about a specific shipment, supplier, or date scope get a
    # targeted result. "Incoming for product X" questions should be routed to
    # `crm_incoming_stock_by_product` instead.
    "crm_incoming_stock_shipments": ("shipment_ids", "supplier_ids", "eta_from", "eta_to"),
    # Unified incoming list: any one narrower (product / shipment / supplier / ETA
    # window) keeps it from full-scanning every open inbound line.
    "crm_incoming_stock_list": (
        "product_ids", "shipment_ids", "supplier_ids", "eta_from", "eta_to",
    ),
    # Domain-scoped attachment lookup: only resolves known catalogue UUIDs.
    # No UUIDs → empty page (mirrors n8n's domain-hint filtering contract).
    "crm_resource_attachments_catalogue": ("attachment_ids",),
    # Global document library: never browse the whole library - require at least
    # one narrower (attachment / directory / type / uploader UUID) or empty page.
    "crm_resource_attachments_list": (
        "attachment_ids", "directory_id", "attachment_type_id", "attachment_type_ids",
        # By NAME as well as by UUID. An agent answering "send me the container
        # status list" has the document class, not its UUID, and forcing a
        # lookup first turns one turn into two - or, more often, into an empty
        # page the agent narrates as "there is no such document".
        "attachment_type_code", "attachment_type_codes", "uploaded_by",
        # `contact_id` is deliberately NOT here. It scopes the answer to that
        # contact's entitlements, which bounds the result to a handful of files -
        # but a document request has to name a DOCUMENT. A contact-only call
        # returns everything that contact may have and leaves the caller to pick,
        # which is a directory listing, not an answer. Asking for nothing must
        # return nothing.
    ),
}


def _missing_narrowing_filters(tool_name: str, query: dict[str, Any] | None) -> tuple[str, ...] | None:
    needed = TOOL_REQUIRED_NARROWING_FILTERS.get(tool_name)
    if not needed:
        return None
    for key in needed:
        value = (query or {}).get(key)
        if value not in (None, "", []):
            return None
    return needed


def _empty_narrowing_response(tool_name: str, query: dict[str, Any] | None, needed: tuple[str, ...]) -> str:
    return json.dumps(
        {
            "data": [],
            "total": 0,
            "page": 1,
            "limit": (query or {}).get("limit"),
        }
    )


# Body params a tool accepts but does NOT require - generated as `str | None =
# None` so callers can omit them (the rest stay required). Lets a POST tool take
# discrete fields instead of one wrapped JSON blob.
TOOL_OPTIONAL_BODY_PARAMS: dict[str, tuple[str, ...]] = {
    "crm_portal_link_get": ("submission_type", "base_url"),
    # Multi-company isolation (AC-F7): contact_id + space_id optionally scope the
    # lookup resolution to the contact's company/companies. Forwarded via BODY
    # (not query) because a POST tool with required body params (set_key, raw)
    # can't carry optional query args before them in the generated signature - 
    # the same limitation the compiler notes for the `view` param. Optional so
    # existing n8n calls without them keep resolving across all companies
    # (required-first ordering keeps set_key/raw/locale ahead of these two).
    "crm_lookup_resolve": ("contact_id", "space_id"),
}


# Ported from PR #1118 (feat/chatbot-dealer-stock-verdict, not merged, owner ruling
# 24 Sep 2026) for chatbot-stock-ask-v2 S3 parity. Query params whose value is a JSON
# OBJECT, not a scalar. Generated as `dict[str, int] | str` instead of the
# `_scalar_union` below, because FastMCP's `pre_parse_json`
# (mcp/server/fastmcp/utilities/func_metadata.py) json.loads ANY string argument whose
# declared annotation is not exactly `str` - so the compact JSON string the backend
# route expects is turned into a native dict BEFORE Pydantic validation, and a
# scalar-only union then rejects it ("Input should be a valid string ...
# input_type=dict"). Both shapes therefore have to validate; `_normalize_query_value`
# re-serializes a dict to the same compact JSON string on the way out, so the backend
# keeps receiving its `Optional[str]` either way.
TOOL_OBJECT_QUERY_PARAMS: dict[str, tuple[str, ...]] = {
    # Chatbot stock ask v2 (ported from PR #1118): the per-product
    # {product_id: quantity} map, parsed backend-side by `parse_requested_quantities`.
    "crm_inventory_stock_balance_list": ("requested_quantities",),
}


TOOL_DEFAULT_QUERY_PARAMS: dict[str, dict[str, str]] = {
    # NOTE: promotions list intentionally has NO status/active default - the
    # backend owns the semantics (active-first, fallback to expired rows with
    # per-row `is_expired` so the agent can answer "found but expired").
    # Orders (DO discovery) surface the latest DELIVERED order first by default
    # (when no explicit sort is given). UI grid passes its own sort/dir, so this
    # only affects MCP/agent calls that omit them. Pairs with the route behaviour:
    # no date filter → top-20 by latest delivery; date filter → the full window.
    "crm_order_management_orders_list": {"sort": "actual_delivery_date", "dir": "desc"},
    "crm_order_management_orders_by_product_list": {"sort": "actual_delivery_date", "dir": "desc"},
    # Domain-scoped tool: hard-pins backend filter to AttachmentType=catalogue so
    # the n8n catalogue-hinted agent cannot accidentally return non-catalogue rows.
    "crm_resource_attachments_catalogue": {"attachment_type_code": "catalogue"},
    # Resource library list is hard-pinned to dealer-downloadable (direct-access)
    # types - it only ever surfaces files flagged is_direct_access.
    # Deliberately NOT resolve_signed_urls: the tool returns the bare storage key
    # ("import-sources/<uuid>/Container Status 2026.xlsx") and n8n signs it on the
    # way out. Signing here would mint a 1-hour URL at list time that expires on
    # its own schedule, for every row, whether or not the file is ever sent.
    # A caller that wants a ready-to-open link passes resolve_signed_urls=true.
    "crm_resource_attachments_list": {"direct_access_only": "true"},
    # Promo serving: the backend owns which promotions answer a customer's
    # question, per promotion TYPE (a PP or focus item is still usable after it
    # ends, a special is not). Hard-pinned rather than exposed as a tool param so
    # the agent cannot switch the policy off and re-introduce an expired special.
    # Each row comes back with promotion_type_code / promotion_type_name and
    # `expired_but_usable`.
    "crm_marketing_promotions_list": {"serving_policy": "true"},
    "crm_marketing_promotion_products_list": {"serving_policy": "true"},
    "crm_marketing_promotion_attachments_list": {"serving_policy": "true"},
    # A1/A2 (chatbot-growth-r1) used to default `include_specs` / `include_sellable`
    # ON here for every caller through this ONE shared MCP server - which is n8n's
    # production business logic AND the CRM's own chatbot lane, not the CRM alone.
    # REMOVED (fix, 7 Sep 2026): a server-level default cannot tell the two apart, so
    # n8n started getting `open_so_qty`/`sellable` on every stock row and a spec wall
    # on every product row the day this shipped, with only the CRM's OWN renderer
    # honouring `restricted_fields` - n8n's prints fields as given. The CRM lane now
    # sends these two explicitly, per turn, from `lanes/business/fetch.py`'s
    # `entity_ids_transformer` (sellable gated on the contact's own
    # `inventory.sellable` grant, specs on a product-check intent/domain); n8n never
    # sends them, so its calls are unchanged from before this plan.
}

# Tools whose responses are blocked / row-filtered to ACTIVE promotions only.
# `crm_marketing_promotions_list` is deliberately NOT here: the backend returns
# expired rows via its active-first fallback with `is_expired=true` per row,
# and the agent must present them as "found but expired" (director requirement)
# instead of silently getting an empty list.
PROMOTION_TOOL_NAMES: set[str] = {
    "crm_marketing_promotions_get",
    "crm_marketing_promotion_products_nested",
    "crm_marketing_promotion_attachments_get",
    "crm_marketing_promotion_attachments_by_promotion",
}
# NOTE: the flat *_list tools (promotion_products_list / promotion_attachments_list)
# are intentionally NOT filtered here. The backend now owns active-first + inactive
# fallback for them (active=True default for API-key callers, mirroring the
# promotions list): stripping inactive rows in this layer would discard the
# intentional fallback rows. Drill-down tools (get / *_nested / by_promotion) keep
# the active-only block below.

STOCK_TOOL_PREFIX = "crm_inventory_stock_"
INVENTORY_TOOL_PREFIX = "crm_inventory_"
ORDERS_LIST_TOOL = "crm_order_management_orders_list"
ORDERS_GET_TOOL = "crm_order_management_orders_get"
_ORDERS_SLIM_TOOLS = (ORDERS_LIST_TOOL, ORDERS_GET_TOOL)
_ORDERS_LIST_DROP_ROW_KEYS = {
    # No UUIDs in agent-facing rows - order_number is the human identifier;
    # UUID narrowing on the way IN comes from crm_resolve_references, never
    # from echoing row ids back.
    "id",
    "created_by",
    "updated_by",
    "customer_id",
    "billing_address_id",
    "shipping_address_id",
    "subtotal_amount",
    "kpi_warning",
    "discount_amount",
    "tax_amount",
    "total_amount",
    "deleted_at",
    "last_synced_to_excel",
    "synced_to_excel",
    "customer",
    "order_status_id",
    "customer_ref",
    # TCK-2026-000023: agent only needs actual_delivery_date; the estimated
    # field exists for the FE but adds noise to MCP responses.
    "estimated_delivery_date",
}
_ORDERS_LIST_DROP_LINE_KEYS = {
    # UUID columns - product/warehouse identity stays via the nested
    # product/warehouse blocks (code + name, id stripped separately).
    "id",
    "order_id",
    "product_id",
    "warehouse_id",
    "unit_price",
    "discount",
    "total",
    "tax",
    "total_excluding_tax",
    "total_including_tax",
    "line_sequence",
}
_MALAYSIA_TZ = ZoneInfo("Asia/Kuala_Lumpur")
_STOCK_HIDDEN_FIELDS = {
    "quantity_available",
    "quantity_reserved",
    "quantity_damaged",
    "reserved_quantity",
    "damaged_quantity",
    "available",
    "reserved",
    "damaged",
    "status",
    "reorder_point",
    "zone_id",
}

# Display-only rename for inventory MCP tools. DB columns + backend Pydantic
# fields are unchanged (n8n / Sage push compat); we only rewrite response keys
# so the AI assistant and MCP consumers see the new Sage-aligned vocabulary.
_WAREHOUSE_KEY_RELABEL = {
    "location": "warehouse",
    "warehouse_code": "system_location",
    "warehouse_name": "system_location_description",
    "warehouse_id": "system_location_id",
}

# The stock-visibility policy blocks. Held OUT of the stock sanitizers below and
# put back verbatim, because those sanitizers are written for a stock ROW and
# would take the answer apart:
#   * `available` is in `_STOCK_HIDDEN_FIELDS` (on a row it means
#     quantity_available and must never be shown). On the availability block it
#     is the ENTIRE answer, so one recursive pass deletes every dealer's reply.
#   * the Sage relabel renames `warehouse_code` -> `system_location` everywhere it
#     walks, and inside a summary location that key is the label the reader sees.
# The blocks are already the narrowest thing the policy allows - there is nothing
# left in them to strip.
_STOCK_POLICY_BLOCK_KEYS = ("stock_visibility", "stock_summary", "stock_availability")

# Stock tool nested `warehouse` object: trim to literal text identifiers only.
# Drops UUID + description so the AI assistant always answers with the human-
# readable system_location code + warehouse label.
_STOCK_NESTED_WAREHOUSE_KEEP_KEYS = {"system_location", "warehouse"}

UUID_REGEX = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
)

_INCOMING_SHIPMENT_DETAIL_TOOLS: set[str] = {
    "crm_incoming_stock_shipment_products",
    "crm_incoming_stock_shipment_attachment",
}

_PRODUCT_CODE_RESOLVABLE_TOOLS: set[str] = {
    "crm_master_product_attachments_by_product",
}


def _normalize_query_value(value: Any) -> Any:
    """Coerce LLM-supplied query values.

    Accepts str | int | float | bool | list[str] | dict | None. Lists pass
    through so httpx serializes them as repeated query params (?k=a&k=b), which
    FastAPI's `List[str] = Query(...)` consumes natively. Numeric scalars are
    stringified so httpx emits them verbatim. Booleans are stringified to
    lowercase so FastAPI's bool parser (`true` / `false`) accepts them; the same
    coercion applies to string "True" / "False" passed by older callers. A dict
    (only reachable for a param declared in TOOL_OBJECT_QUERY_PARAMS, either
    because the caller sent an object or because FastMCP's `pre_parse_json`
    turned the caller's JSON string into one) is re-serialized to the same
    compact, key-sorted JSON string the backend route parses. Ported from PR
    #1118 (not merged) for chatbot-stock-ask-v2 S3 parity.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, dict):
        return json.dumps(value, separators=(",", ":"), sort_keys=True)
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value in {"True", "False"}:
        return value.lower()
    return value


def _looks_like_uuid(value: str | None) -> bool:
    if not value:
        return False
    return UUID_REGEX.match(value) is not None


# Names that should be UUID-validated when present as query/path params.
# Skip respond.io identifiers (contact_id, space_id) - those are Respond's own
# numeric / opaque IDs, not server-side UUIDs.
_UUID_PARAM_SUFFIXES = ("_id", "_ids")
_UUID_PARAM_EXEMPT: frozenset[str] = frozenset({"contact_id", "space_id"})


def _is_uuid_param(name: str) -> bool:
    if name in _UUID_PARAM_EXEMPT:
        return False
    return any(name.endswith(suffix) for suffix in _UUID_PARAM_SUFFIXES)


def _validate_uuid_param(name: str, value: Any) -> Any:
    """Validate a query/path param expected to carry canonical UUIDs.

    Accepts: single UUID string, CSV string of UUIDs, list of UUID strings,
    JSON array string of UUIDs. Returns the original value (unchanged) so the
    HTTP serialization stays untouched. Raises ValueError with a structured
    payload on invalid input - caught by the envelope wrapper as status=error.
    """
    if value is None or value == "":
        return value
    items: list[str] = []
    if isinstance(value, list):
        items = [str(v).strip() for v in value if v not in (None, "")]
    elif isinstance(value, str):
        s = value.strip()
        if s.startswith("[") and s.endswith("]"):
            try:
                parsed = json.loads(s)
                if isinstance(parsed, list):
                    items = [str(v).strip() for v in parsed if v not in (None, "")]
            except json.JSONDecodeError:
                pass
        if not items:
            items = [p.strip() for p in s.split(",") if p.strip()]
    else:
        items = [str(value).strip()]
    for item in items:
        if not _looks_like_uuid(item):
            raise ValueError(
                json.dumps({
                    "code": "INVALID_UUID",
                    "message": f"`{name}` contains a non-UUID value: {item!r}",
                    "param": name,
                })
            )
    return value


def _list_spec_by_path(path: str) -> ToolSpec | None:
    for s in CATALOG:
        if s.path == path:
            return s
    return None


def _json_loads_safe(payload: str) -> Any:
    try:
        return json.loads(payload)
    except Exception:
        return None


def _parse_iso_date(value: str | None) -> date | None:
    if not value:
        return None
    s = value.strip()
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        pass
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _extract_incoming_rows(raw: str) -> list[dict[str, Any]]:
    data = _json_loads_safe(raw)
    if not isinstance(data, dict):
        return []
    rows = data.get("data")
    if not isinstance(rows, list):
        return []
    return [r for r in rows if isinstance(r, dict)]


def _shipment_row_ref(row: dict[str, Any]) -> str | None:
    shipment_number = row.get("shipment_number")
    if isinstance(shipment_number, str) and shipment_number.strip():
        return shipment_number.strip()
    return None


def _match_incoming_shipment_candidates(rows: list[dict[str, Any]], identifier: str) -> list[dict[str, Any]]:
    key = identifier.strip().lower()
    if not key:
        return []

    eta = _parse_iso_date(identifier)
    if eta is not None:
        eta_str = eta.isoformat()
        eta_matches = []
        for row in rows:
            eta_val = row.get("estimated_arrival_date")
            if isinstance(eta_val, str) and eta_val.startswith(eta_str):
                eta_matches.append(row)
        if eta_matches:
            return eta_matches

    exact_matches = []
    partial_matches = []
    for row in rows:
        fields = [
            row.get("shipment_number"),
            row.get("shipping_container_number"),
        ]
        normalized = [f.strip().lower() for f in fields if isinstance(f, str) and f.strip()]
        if key in normalized:
            exact_matches.append(row)
            continue
        if any(key in f for f in normalized):
            partial_matches.append(row)
    if exact_matches:
        return exact_matches
    return partial_matches


async def _resolve_product_reference(client: Any, identifier: str) -> tuple[str | None, list[dict[str, Any]]]:
    """Resolve a product code/name to its UUID via the products list endpoint.

    Mirrors the wildcard search behaviour of `crm_master_products_list` (ILIKE on
    product_code, product_name, description). Returns (resolved_uuid, candidates).
    Resolved UUID is non-None only when the input matches exactly one product code,
    or the search yields exactly one row. Otherwise candidates is returned so the
    caller can surface a disambiguation hint.
    """
    candidate = identifier.strip()
    if not candidate:
        return None, []

    list_raw = await client.get(
        "/api/v1/master-data/products",
        path_params={},
        query={"query": candidate, "page": "1", "limit": "20"},
        tool_name="crm_master_products_list",
    )
    data = _json_loads_safe(list_raw)
    rows = data.get("data") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return None, []
    candidates = [r for r in rows if isinstance(r, dict)]
    if not candidates:
        return None, []

    key = candidate.lower()
    exact = [
        r for r in candidates
        if isinstance(r.get("product_code"), str) and r["product_code"].strip().lower() == key
    ]
    if len(exact) == 1 and exact[0].get("id"):
        return str(exact[0]["id"]), candidates
    if len(candidates) == 1 and candidates[0].get("id"):
        return str(candidates[0]["id"]), candidates
    return None, candidates


async def _resolve_incoming_shipment_reference(client: Any, identifier: str) -> str | None:
    candidate = identifier.strip()
    if not candidate:
        return None

    list_raw = await client.get(
        "/api/v1/incoming-stock/shipments",
        path_params={},
        query={"query": candidate, "page": "1", "limit": "20"},
        tool_name="crm_incoming_stock_shipments",
    )
    rows = _extract_incoming_rows(list_raw)
    matches = _match_incoming_shipment_candidates(rows, candidate)
    if len(matches) == 1:
        return _shipment_row_ref(matches[0])
    if len(matches) > 1:
        return None

    parsed_eta = _parse_iso_date(candidate)
    if parsed_eta is None:
        return None

    eta_raw = await client.get(
        "/api/v1/incoming-stock/shipments",
        path_params={},
        query={
            "eta_from": parsed_eta.isoformat(),
            "eta_to": parsed_eta.isoformat(),
            "page": "1",
            "limit": "20",
        },
        tool_name="crm_incoming_stock_shipments",
    )
    eta_rows = _extract_incoming_rows(eta_raw)
    if len(eta_rows) == 1:
        return _shipment_row_ref(eta_rows[0])
    return None


def _looks_like_promotion_record(obj: Any) -> bool:
    """True only when obj resembles a real promotion row (has an id and an
    activity signal). Distinguishes promotion payloads from backend error
    envelopes (`detail`, `message`+`code`, `allowed`, `error`) so the filter
    can passthrough errors instead of masking them as PROMOTION_INACTIVE."""
    if not isinstance(obj, dict):
        return False
    if not obj.get("id"):
        return False
    return isinstance(obj.get("is_active"), bool) or isinstance(obj.get("status"), str)


def _is_active_promotion_obj(obj: Any) -> bool:
    """Whether the drill-down tools may return this promotion.

    Named for the old rule (active only), but the question it answers now is
    the serving one. The list tools hand the agent expired-but-usable
    promotions ("ended on 31/07 but still applies"), and blocking the very next
    drill-down on those made one surface answer two different ways. The backend
    stamps `expired_but_usable` from the single serving policy, so an expired
    promotion whose TYPE still honours it is drillable, while an expired
    special - which the policy withholds - stays blocked.
    """
    if not isinstance(obj, dict):
        return False
    if obj.get("expired_but_usable") is True:
        return True
    if isinstance(obj.get("is_active"), bool):
        return obj["is_active"]
    status = obj.get("status")
    if isinstance(status, str):
        return status.strip().lower() == "active"
    return False


async def _ensure_promotion_active(client: Any, promotion_id: str | None) -> str | None:
    if not promotion_id:
        return None
    try:
        raw = await client.get(f"/api/v1/marketing/promotions/{promotion_id}", path_params={}, query={})
    except Exception:
        return None
    data = _json_loads_safe(raw)
    if _looks_like_promotion_record(data) and not _is_active_promotion_obj(data):
        return json.dumps(
            {
                "message": "Promotion is inactive.",
                "code": "PROMOTION_INACTIVE",
                "promotion_id": str(promotion_id),
            }
        )
    return None


def _filter_active_promotion_records(tool_name: str, raw: str) -> str:
    if tool_name not in PROMOTION_TOOL_NAMES:
        return raw
    data = _json_loads_safe(raw)
    if data is None:
        return raw

    # Single-promotion details: only block when payload is a real promotion
    # record with is_active=false. Error envelopes (422 access_levels, 404
    # not-found, etc.) pass through unchanged so callers see the real cause.
    if tool_name == "crm_marketing_promotions_get":
        if _looks_like_promotion_record(data) and not _is_active_promotion_obj(data):
            return json.dumps({"message": "Promotion is inactive.", "code": "PROMOTION_INACTIVE"})
        return raw

    # Single linked records (e.g. promotion attachment get): block only when
    # the nested promotion is a real record with is_active=false.
    if isinstance(data, dict) and _looks_like_promotion_record(data.get("promotion")):
        if not _is_active_promotion_obj(data.get("promotion")):
            return json.dumps({"message": "Promotion is inactive.", "code": "PROMOTION_INACTIVE"})
        return raw

    # List-like responses: keep only rows with active promotion context.
    if isinstance(data, dict) and isinstance(data.get("data"), list):
        rows = data["data"]
        filtered = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            if _is_active_promotion_obj(row):
                filtered.append(row)
                continue
            promotion = row.get("promotion")
            if _is_active_promotion_obj(promotion):
                filtered.append(row)
        data["data"] = filtered
        return json.dumps(data)

    return raw


def _to_malaysia_iso(value: Any) -> Any:
    if not isinstance(value, str) or not value.strip():
        return value
    raw = value.strip()
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return value
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    # Emit the Malaysia WALL-CLOCK time as a NAIVE ISO string (no "+08:00" offset).
    # Downstream consumers (n8n/luxon) re-convert an offset-aware timestamp back to
    # UTC for display, which would undo the conversion and show UTC again; a naive
    # MYT string is rendered literally as the Malaysia time. See PLAN/timezone note.
    return dt.astimezone(_MALAYSIA_TZ).replace(tzinfo=None).isoformat()


#: Timestamp keys rewritten to Malaysia wall-clock on every response.
#:
#: `uploaded_at` is here because for an attachment it IS the freshness date - the
#: file is never edited in place, so there is no `updated_at` column on the table
#: at all. Left in UTC it was wrong twice over: the envelope's `last_updated_at`
#: reported a UTC instant, and the per-file "Uploaded" line is the DAY of that
#: instant, so anything uploaded after 16:00 MYT (08:00Z) renders as the previous
#: date to a Malaysian reader.
# `last_updated_at` is a PAYLOAD-level timestamp (the stock tool's "data last
# updated" moment). The summary visibility modes carry no rows, so it is the only
# freshness stamp their answer has - left alone it would report UTC while every
# other answer reports Malaysia time, i.e. eight hours early.
_MALAYSIA_TIME_KEYS = ("updated_at", "uploaded_at", "last_updated_at")


def _normalize_updated_at(value: Any) -> Any:
    """Recursively rewrite every timestamp in `_MALAYSIA_TIME_KEYS` to Malaysia time.

    Applies to every MCP tool response so n8n / the AI assistant render a
    consistent timezone regardless of which tool produced the row. Other fields
    are left untouched.
    """
    if isinstance(value, list):
        return [_normalize_updated_at(item) for item in value]
    if not isinstance(value, dict):
        return value
    out: dict[str, Any] = {}
    for key, raw in value.items():
        if key in _MALAYSIA_TIME_KEYS:
            out[key] = _to_malaysia_iso(raw)
        elif isinstance(raw, (dict, list)):
            out[key] = _normalize_updated_at(raw)
        else:
            out[key] = raw
    return out


def _strip_stock_hidden_fields(value: Any) -> Any:
    """Recursively drop quantity / status fields the stock MCP tools must hide.

    Stock tools only - see `_STOCK_HIDDEN_FIELDS`.
    """
    if isinstance(value, list):
        return [_strip_stock_hidden_fields(item) for item in value]
    if not isinstance(value, dict):
        return value
    out: dict[str, Any] = {}
    for key, raw in value.items():
        if key in _STOCK_HIDDEN_FIELDS:
            continue
        if isinstance(raw, (dict, list)):
            out[key] = _strip_stock_hidden_fields(raw)
        else:
            out[key] = raw
    return out


def _relabel_warehouse_keys(value: Any) -> Any:
    """Recursively rename warehouse-master keys to the Sage-aligned vocabulary.

    `warehouse_code` → `system_location`, `warehouse_name` →
    `system_location_description`, `location` → `warehouse`. Applied to MCP
    inventory tool responses only. Backend payloads are untouched.

    Collision guard: if a dict already contains the target key (e.g. a nested
    `warehouse` object on the same row that also has a scalar `location`),
    leave the original key untouched rather than overwrite the existing
    target.
    """
    if isinstance(value, list):
        return [_relabel_warehouse_keys(item) for item in value]
    if not isinstance(value, dict):
        return value
    out: dict[str, Any] = {}
    for key, raw in value.items():
        new_key = _WAREHOUSE_KEY_RELABEL.get(key, key)
        if new_key != key and new_key in value:
            new_key = key
        out[new_key] = _relabel_warehouse_keys(raw)
    return out


def _slim_stock_nested_warehouse(value: Any) -> Any:
    """For stock tool rows, rename the nested `warehouse` wrapper to
    `system_location` and trim its contents to literal-text identifiers
    (`system_location`, `warehouse`). Run AFTER `_relabel_warehouse_keys`.

    The backend embeds a `warehouse` object on each stock row carrying id +
    system_location_description (+ now system_location + warehouse after the
    additive WarehouseSimple change). For stock answers the assistant only
    needs the human-readable codes, not the UUID or the description, and the
    wrapper key itself is renamed so the row exposes a `system_location` object
    rather than a `warehouse` object.

    Collision guard: if a row already carries a `system_location` key, keep the
    original `warehouse` wrapper name to avoid overwriting it.
    """
    if isinstance(value, list):
        return [_slim_stock_nested_warehouse(item) for item in value]
    if not isinstance(value, dict):
        return value
    out: dict[str, Any] = {}
    for key, raw in value.items():
        if key == "warehouse" and isinstance(raw, dict):
            slimmed = {
                k: v for k, v in raw.items() if k in _STOCK_NESTED_WAREHOUSE_KEEP_KEYS
            }
            target_key = "system_location" if "system_location" not in value else key
            out[target_key] = slimmed
        else:
            out[key] = _slim_stock_nested_warehouse(raw)
    return out


def _line_matches_product_query(line: Any, term_lower: str) -> bool:
    """Substring match a single line against a lowercased product_query term.

    Checks the embedded `product` block (product_code / product_name /
    description) plus raw line-level `product_code` / `product_name` fields if
    present.
    """
    if not isinstance(line, dict):
        return False
    product = line.get("product")
    if isinstance(product, dict):
        for k in ("product_code", "product_name", "description"):
            val = product.get(k)
            if isinstance(val, str) and term_lower in val.lower():
                return True
    for k in ("product_code", "product_name"):
        val = line.get(k)
        if isinstance(val, str) and term_lower in val.lower():
            return True
    return False


def _slim_orders_list_row(row: Any, product_query: str | None = None) -> Any:
    """Trim a single order row for crm_order_management_orders_list.

    Drops fields the assistant never needs (address ids, amount aggregates,
    sync metadata, embedded customer block). Collapses `order_status` to
    `status_code` only. Trims each order line to non-pricing fields. When
    `product_query` is set, filters lines to those matching the term.
    """
    if not isinstance(row, dict):
        return row
    term_lower = (product_query or "").strip().lower() or None
    out: dict[str, Any] = {}
    for key, value in row.items():
        if key in _ORDERS_LIST_DROP_ROW_KEYS:
            continue
        # TCK-2026-000023: accept stale callers still sending delivery_time.
        if key == "delivery_time":
            if "pickup_time" not in row:
                out["pickup_time"] = value
            continue
        if key == "order_status":
            # TCK-2026-000023: flatten to a human-readable string. Prefer
            # status_name; fall back to status_code so we never emit null.
            if isinstance(value, dict):
                flat = value.get("status_name") or value.get("status_code")
                out[key] = flat
            else:
                out[key] = value
            continue
        if key == "lines" and isinstance(value, list):
            slim_lines: list[Any] = []
            for line in value:
                if term_lower and not _line_matches_product_query(line, term_lower):
                    continue
                if isinstance(line, dict):
                    slim_line: dict[str, Any] = {}
                    for k, v in line.items():
                        if k in _ORDERS_LIST_DROP_LINE_KEYS:
                            continue
                        # Nested product/warehouse refs: keep code + name, drop UUID.
                        if k in ("product", "warehouse") and isinstance(v, dict):
                            v = {nk: nv for nk, nv in v.items() if nk != "id"}
                        slim_line[k] = v
                    slim_lines.append(slim_line)
                else:
                    slim_lines.append(line)
            out[key] = slim_lines
            continue
        out[key] = value
    return out


def _slim_orders_list_response(data: Any, product_query: str | None = None) -> Any:
    """Apply order-row slimming to list / dict payload shapes."""
    if isinstance(data, list):
        return [_slim_orders_list_row(item, product_query) for item in data]
    if isinstance(data, dict):
        if isinstance(data.get("data"), list):
            data = {**data, "data": [_slim_orders_list_row(r, product_query) for r in data["data"]]}
            return data
        return _slim_orders_list_row(data, product_query)
    return data


_PROMO_PRODUCT_TOOL_PREFIXES = (
    "crm_marketing_promotion_products_",
)

# Promotion-list rows: drop UUID-bearing fields so the agent answers using
# human-readable identifiers (description + dates + access_levels) only.
# Nested attachments keep their own UUID handling via _strip_attachment_internals.
_PROMOTIONS_LIST_TOOL = "crm_marketing_promotions_list"
_PROMOTIONS_LIST_DROP_KEYS = frozenset({"id", "created_by"})
_PRODUCTS_LIST_TOOL = "crm_master_products_list"
# Confidential product economics - never surface to chat / WhatsApp. list_price
# (customer-facing) stays; cost + invoice price are internal.
_PRODUCTS_LIST_DROP_KEYS = ("cost_price", "invoice_price")


def _strip_products_list_confidential(data: Any) -> Any:
    if not isinstance(data, dict):
        return data
    rows = data.get("data")
    if not isinstance(rows, list):
        return data
    for row in rows:
        if isinstance(row, dict):
            for k in _PRODUCTS_LIST_DROP_KEYS:
                row.pop(k, None)
    return data
_PORTAL_LINK_TOOL = "crm_portal_link_get"

# Forms-list rows: the agent only needs the form NAME (to name it back to the
# user) plus `attachment_id` (to deliver the downloadable file). code, purpose,
# form_type, language, version, is_active, access_levels are internal noise the
# assistant should never surface. Whitelist projection - keep ONLY these keys.
_FORMS_LIST_TOOL = "crm_forms_management_forms_list"
# Browse (no form_ids → "what forms do you have"): name, plus `id` - not printed
# to the customer (the chatbot presenter carries it as the item's own identity,
# never as a rendered field), but the engine needs a stable id to arm a numbered
# pick over a forms list and resolve "1" back to `form_ids=[id]` (chatbot turn
# re-architecture item 1, 17 Sep 2026: a browse whitelisting `name` only left no
# uuid a roster option could resolve through, so "1" re-ran the whole catalogue).
# Narrowed (form_ids → a specific form): name + the attachment object so the
# caller can actually deliver the form file. Attachment internals are scrubbed
# separately by _strip_attachment_internals.
_FORMS_LIST_KEEP_BROWSE = ("name", "id")
_FORMS_LIST_KEEP_NARROW = ("name", "attachment")

# Tools whose row payload carries an inline attachment(s) blob. Browse-mode
# calls (no UUID narrowing - agent listing the whole catalog) strip those
# inline blobs to keep the response small and to stop the agent from echoing
# every linked file when the user just asked "what forms / promos do we have".
# UUID-narrowed calls keep attachments - the agent asked for a specific row
# and needs the linked files in the answer.
#
# (tool_name, narrowing_query_keys, row_keys_to_strip_when_browsing)
_BROWSE_ATTACHMENT_STRIP_RULES: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    # Note: crm_marketing_promotions_list no longer browse-strips its header
    # `attachments` - the promotion's packing-list file is exactly what n8n needs
    # to deliver, and the consolidated tool returns full granular data regardless
    # of browse vs narrowed mode.
    # Note: crm_forms_management_forms_list is handled by the stronger
    # whitelist projection _slim_forms_list_rows (name + attachment_id only),
    # which already drops the inline attachment blob in every mode.
)


def _is_browse_mode(query: dict[str, Any] | None, narrowing_keys: tuple[str, ...]) -> bool:
    q = query or {}
    for k in narrowing_keys:
        v = q.get(k)
        if v not in (None, "", []):
            return False
    return True


def _strip_inline_attachment_keys(data: Any, drop_keys: tuple[str, ...]) -> Any:
    if not isinstance(data, dict):
        return data
    rows = data.get("data")
    if not isinstance(rows, list):
        return data
    cleaned = []
    for row in rows:
        if not isinstance(row, dict):
            cleaned.append(row)
            continue
        cleaned.append({k: v for k, v in row.items() if k not in drop_keys})
    return {**data, "data": cleaned}


def _slim_forms_list_rows(data: Any, narrowed: bool = False) -> Any:
    """Project each forms-list row to NAME (browse) or NAME + attachment (narrowed)."""
    keep = _FORMS_LIST_KEEP_NARROW if narrowed else _FORMS_LIST_KEEP_BROWSE
    if not isinstance(data, dict):
        return data
    rows = data.get("data")
    if not isinstance(rows, list):
        return data
    cleaned = []
    for row in rows:
        if not isinstance(row, dict):
            cleaned.append(row)
            continue
        cleaned.append({k: row[k] for k in keep if k in row})
    return {**data, "data": cleaned}


def _strip_promotions_list_row_ids(data: Any) -> Any:
    if not isinstance(data, dict):
        return data
    rows = data.get("data")
    if not isinstance(rows, list):
        return data
    cleaned = []
    for row in rows:
        if not isinstance(row, dict):
            cleaned.append(row)
            continue
        cleaned.append({k: v for k, v in row.items() if k not in _PROMOTIONS_LIST_DROP_KEYS})
    return {**data, "data": cleaned}
# Keys removed recursively from every dict in the promotion-products payload
# (top-level promotion_product row, nested `product`, `promotion`, and inline
# `promotion_attachments`). Pure noise for the AI agent.
_PROMO_PRODUCT_DROP_KEYS = frozenset(
    {
        "discount_amount",
        "discount_percent",
        "dealer_discount_percent",
        "dealer_cost",
        "list_to_dealer_margin_amount",
        "synced_to_excel",
        "last_synced_to_excel",
        "original_filename",
        "attachment_type",
        "directory_id",
        "cost_price",
        "category_id",
        "category_code",
        "brand_id",
        "brand_code",
        "item_type",
        "warranty_months"
    }
)


def _slim_promotion_products_node(node: Any) -> Any:
    """Recursive slim for promotion-product responses.

    * Drops every key in `_PROMO_PRODUCT_DROP_KEYS` at any depth.
    * Renames `promotion_price` → `selling_price` (UI vocabulary).
    """
    if isinstance(node, list):
        return [_slim_promotion_products_node(item) for item in node]
    if not isinstance(node, dict):
        return node
    out: dict[str, Any] = {}
    for k, v in node.items():
        if k in _PROMO_PRODUCT_DROP_KEYS:
            continue
        if k == "promotion_price":
            if "selling_price" not in node:
                out["selling_price"] = _slim_promotion_products_node(v)
            continue
        out[k] = _slim_promotion_products_node(v)
    return out


async def _fetch_all_child_rows(
    client: Any, path: str, base_query: dict[str, Any], tool_name: str, max_pages: int = 50
) -> list[Any]:
    """Page a child list endpoint (limit cap 100) until exhausted; return all rows.

    Used by the merge-tool enrichers - a single page of parents can have more
    child rows (promotion products / product attachments) than the backend's
    per-page cap, so we must paginate or nesting would silently truncate.
    """
    rows: list[Any] = []
    page = 1
    while page <= max_pages:
        q = {**base_query, "limit": "100", "page": str(page)}
        raw = await client.get(path, path_params={}, query=q, tool_name=tool_name)
        payload = _json_loads_safe(raw)
        page_rows = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(page_rows, list) or not page_rows:
            break
        rows.extend(page_rows)
        if len(page_rows) < 100:
            break
        page += 1
    return rows


async def _enrich_products_with_attachments(client: Any, raw: str, query: dict[str, Any] | None) -> str:
    """Nest each product's attachments under an `attachments[]` key (merge tool).

    ONLY runs when the caller passed `attachment_type_ids` - then it fans out to
    /product-attachments?product_ids=<page ids>&attachment_type_ids=<types> and
    nests just those file types. Without `attachment_type_ids` the response is a
    plain product listing with no attachments (e.g. a price/spec list). Attachment
    internals are stripped later by `_sanitize_tool_response`.
    """
    att_type_ids = (query or {}).get("attachment_type_ids")
    if att_type_ids in (None, "", []):
        return raw
    data = _json_loads_safe(raw)
    if not isinstance(data, dict):
        return raw
    rows = data.get("data")
    if not isinstance(rows, list) or not rows:
        return raw
    product_ids = [str(r.get("id")) for r in rows if isinstance(r, dict) and r.get("id")]
    if not product_ids:
        return raw
    pa_rows = await _fetch_all_child_rows(
        client,
        "/api/v1/master-data/product-attachments",
        {"product_ids": ",".join(product_ids), "attachment_type_ids": att_type_ids},
        "crm_master_products_list",
    )
    by_product: dict[str, list[Any]] = defaultdict(list)
    for a in pa_rows or []:
        if isinstance(a, dict) and a.get("product_id") and a.get("attachment") is not None:
            by_product[str(a["product_id"])].append(a["attachment"])
    for r in rows:
        if isinstance(r, dict) and r.get("id"):
            r["attachments"] = by_product.get(str(r["id"]), [])
    return json.dumps(data)


async def _enrich_list_response(tool_name: str, client: Any, raw: str, query: dict[str, Any] | None) -> str:
    """Dispatch the merge-tool nesting enrichment for the consolidated list tools.

    Note: promotions_list returns the promo HEADER + its PDF only - products are
    intentionally NOT nested (the user reads SKU detail from the PDF).
    """
    if tool_name == "crm_master_products_list":
        return await _enrich_products_with_attachments(client, raw, query)
    return raw


def _slim_promotion_products_response(data: Any) -> Any:
    if isinstance(data, list):
        return [_slim_promotion_products_node(r) for r in data]
    if isinstance(data, dict):
        if isinstance(data.get("data"), list):
            return {**data, "data": [_slim_promotion_products_node(r) for r in data["data"]]}
        # Handle nested-by-promotion shapes: {promotion: ..., products: [...]}
        if isinstance(data.get("products"), list):
            return {**data, "products": [_slim_promotion_products_node(r) for r in data["products"]]}
        return _slim_promotion_products_node(data)
    return data


_ATTACHMENT_INTERNAL_KEYS = frozenset(
    {
        "full_directory_path",
        "directory_id",
        "storage_provider",
        "uploaded_by",
        "uploaded_by_user_id",
    }
)
_ATTACHMENT_TOOL_PREFIXES = (
    "crm_master_product_attachments_",
    "crm_marketing_promotion_attachments_",
    "crm_resource_attachments_",
)

# Extra fields stripped only from the global resource library list view - 
# noisy for chat answers; admins use the UI when they need them.
_RESOURCE_ATTACHMENT_LIST_EXTRA_KEYS = frozenset(
    {
        "file_size_bytes",
        "file_hash",
        "original_filename",
        "uploaded_by_user",
    }
)
_RESOURCE_ATTACHMENT_LIST_TOOL = "crm_resource_attachments_list"

# Catalogue-domain tool slims each row to a keep-list. Caller already supplied
# attachment_ids on the way in (REQUIRED narrowing); the n8n catalogue flow only
# needs file metadata + URL to forward the doc - never the row UUID, linkage
# arrays, audit columns, or soft-delete bookkeeping. Keep-list (not drop-list)
# so new backend columns don't leak into chat context by default.
_RESOURCE_ATTACHMENT_CATALOGUE_TOOL = "crm_resource_attachments_catalogue"
_RESOURCE_ATTACHMENT_CATALOGUE_KEEP_KEYS = frozenset(
    {
        "stored_filename",
        "file_path",
        "mime_type",
        "access_levels",
        "uploaded_at",
        "attachment_type",
    }
)


def _slim_resource_attachment_catalogue_rows(node: Any) -> Any:
    """Apply the keep-list to each row in the catalogue list response.

    Only row dicts (items of the top-level `data` list) are slimmed; envelope
    keys (`pagination`, `empty`, `fallback_used`, `resolved_entities`) pass
    through untouched.
    """
    if isinstance(node, dict) and isinstance(node.get("data"), list):
        out = dict(node)
        out["data"] = [
            {k: v for k, v in row.items() if k in _RESOURCE_ATTACHMENT_CATALOGUE_KEEP_KEYS}
            if isinstance(row, dict)
            else row
            for row in node["data"]
        ]
        return out
    return node


def _strip_attachment_internals(node: Any, _parent_key: str | None = None) -> Any:
    """Recurse + drop attachment internal fields (TCK-2026-000015).

    Triggered for attachment-bearing tool responses. Recurses into dicts +
    lists so nested `attachment` blocks (e.g. promotion_attachment rows that
    embed an `attachment` sub-object) are slimmed too.

    Also drops `description` from any nested `attachment_type` dict - the
    long-form description on AttachmentType is taxonomy admin noise (e.g.
    "Product Photos / Actual Photos / Photos by Marketing") that the LLM
    doesn't need to surface in chat. `type_name` already conveys what the
    file class is. Care: only stripped when the dict was reached via an
    `attachment_type` key - the sibling `Attachment.description` column
    (per-file caption) stays intact.
    """
    if isinstance(node, list):
        return [_strip_attachment_internals(item, _parent_key) for item in node]
    if isinstance(node, dict):
        is_attachment_type = _parent_key == "attachment_type"
        return {
            k: _strip_attachment_internals(v, k)
            for k, v in node.items()
            if k not in _ATTACHMENT_INTERNAL_KEYS
            and not (is_attachment_type and k == "description")
        }
    return node


def _strip_resource_attachment_list_extras(node: Any) -> Any:
    if isinstance(node, list):
        return [_strip_resource_attachment_list_extras(item) for item in node]
    if isinstance(node, dict):
        return {
            k: _strip_resource_attachment_list_extras(v)
            for k, v in node.items()
            if k not in _RESOURCE_ATTACHMENT_LIST_EXTRA_KEYS
        }
    return node


GRN_LIST_LIMIT_DEFAULT = 50
GRN_LIST_LIMIT_CAP = 200
_GRN_TOOL_PREFIX = "crm_procurement_grn_"
_GRN_LIST_TOOL = "crm_procurement_grn_list"
_GRN_KEEP_KEYS = frozenset(
    {
        "document_number",
        "spo_number",
        "receiving_date",
        "notes",
        "picking_lines",
        "lines_count",
        "created_at",
        "updated_at",
    }
)
_GRN_RENAMES = (
    ("picking_number", "document_number"),
    ("picking_date", "receiving_date"),
)


def _coerce_grn_limit(value: Any) -> int:
    """Clamp incoming limit to (default, cap). Invalid / empty → default."""
    try:
        n = int(value) if value not in (None, "") else 0
    except (TypeError, ValueError):
        return GRN_LIST_LIMIT_DEFAULT
    if n <= 0:
        return GRN_LIST_LIMIT_DEFAULT
    if n > GRN_LIST_LIMIT_CAP:
        return GRN_LIST_LIMIT_CAP
    return n


_GRN_SHAPE_MARKERS = ("picking_number", "document_number", "picking_lines", "picking_date")


def _slim_grn_row(row: Any) -> Any:
    if not isinstance(row, dict):
        return row
    if not any(m in row for m in _GRN_SHAPE_MARKERS):
        return row
    renamed: dict[str, str] = {}
    preserve_sources: set[str] = set()
    for src, dst in _GRN_RENAMES:
        if src not in row:
            continue
        if dst in row:
            preserve_sources.add(src)
        else:
            renamed[src] = dst
    out: dict[str, Any] = {}
    for k, v in row.items():
        if k in renamed:
            out[renamed[k]] = v
            continue
        if k in preserve_sources or k in _GRN_KEEP_KEYS:
            out[k] = v
    return out


def _slim_grn_response(data: Any) -> Any:
    if isinstance(data, list):
        return [_slim_grn_row(r) for r in data]
    if isinstance(data, dict):
        if isinstance(data.get("data"), list):
            return {**data, "data": [_slim_grn_row(r) for r in data["data"]]}
        return _slim_grn_row(data)
    return data


def _drop_company_id(node: Any) -> Any:
    if isinstance(node, dict):
        return {k: _drop_company_id(v) for k, v in node.items() if k != "company_id"}
    if isinstance(node, list):
        return [_drop_company_id(item) for item in node]
    return node


# Exactly the eleven tools the multi-company labelling touches. The strip is
# scoped to them rather than run globally because other tools put `company_id`
# on their rows ON PURPOSE - `crm_resource_attachments_list` is the one that
# proved it (`_stamp_company`, app/api/v1/resources/attachments.py), and a
# blanket strip silently undid it.
_COMPANY_ID_STRIP_TOOLS = frozenset(
    {
        "crm_inventory_stock_balance_list",
        "crm_incoming_stock_list",
        "crm_incoming_stock_by_product",
        "crm_incoming_stock_shipments",
        _PRODUCTS_LIST_TOOL,
        "crm_master_product_attachments_list",
        "crm_certificates_list",
        _PROMOTIONS_LIST_TOOL,
        "crm_marketing_promotion_products_list",
        ORDERS_LIST_TOOL,
        "crm_order_management_orders_by_product_list",
    }
)


def _strip_row_company_ids(tool_name: str, data: Any) -> Any:
    """Drop the raw `company_id` UUID from the rows of the labelled tools.

    The multi-company labelling put `company_id` on the affected row schemas,
    and the rows are ORM instances, so `from_attributes` now fills it on every
    row of those tools. It is a UUID, and no UUID belongs in an agent-facing
    row (the same rule `_ORDERS_LIST_DROP_ROW_KEYS` and
    `_PROMOTIONS_LIST_DROP_KEYS` enforce per tool) - `company_name` is the
    readable form the presenter shows. Recurses into nested rows (`product`,
    `promotion`, lines, attachments), because those are ORM rows of their own.
    Top-level `lookup_companies` is untouched: it is the envelope's company
    list and n8n matches on its ids.

    Only `_COMPANY_ID_STRIP_TOOLS` are affected: a tool outside that set keeps
    whatever it decided to publish.
    """
    if tool_name not in _COMPANY_ID_STRIP_TOOLS:
        return data
    if not isinstance(data, dict):
        return data
    rows = data.get("data")
    if not isinstance(rows, list):
        return data
    return {**data, "data": [_drop_company_id(row) for row in rows]}


# --- project sales -------------------------------------------------------------
# Internal UUIDs the agent must never quote (the chat and WhatsApp surfaces are the same
# renderer), plus browser-only fields it cannot act on. `id` deliberately SURVIVES: both
# crm_project_detail and crm_project_quotations_list take it, and the two-call pattern is how
# a project answer gets any depth. Every dropped key has a human-readable twin that stays
# (developer_name, type_name, owner_name, status_label, brands), so nothing becomes
# unanswerable -- it becomes answerable in words.
_PROJECT_SLIM_TOOLS = ("crm_projects_list", "crm_project_detail")
_PROJECT_DROP_KEYS = frozenset(
    {
        "developer_party_id",
        "type_id",
        "template_id",
        "lead_id",
        "lead_owner_user_id",
        "status_id",
        "owner_user_id",
        "architect_party_id",
        "main_contractor_party_id",
        "brand_ids",
        # A permission answer for rendering a button.
        "can_edit",
    }
)


_QUOTATION_SLIM_TOOL = "crm_project_quotations_list"
_QUOTATION_DROP_KEYS = frozenset(
    {
        # The caller passed this in to get the list.
        "project_id",
        # Nothing consumes it: the version the agent talks about is `current_version_no`.
        "current_version_id",
        # `issued_by_name` sits right beside it.
        "issued_by",
        # Line-level plumbing, when lines ride along.
        "product_id",
        "quotation_id",
    }
)


def _slim_rows(data: Any, drop_keys: frozenset) -> Any:
    """Drop `drop_keys` from every row, for a list payload, a bare list, or one detail row."""

    def _row(item: Any) -> Any:
        if not isinstance(item, dict):
            return item
        return {k: v for k, v in item.items() if k not in drop_keys}

    if isinstance(data, list):
        return [_row(item) for item in data]
    if isinstance(data, dict):
        if isinstance(data.get("data"), list):
            return {**data, "data": [_row(r) for r in data["data"]]}
        return _row(data)
    return data


def _slim_projects_response(data: Any) -> Any:
    """Same rule for a list payload, a bare list, and one detail row."""
    return _slim_rows(data, _PROJECT_DROP_KEYS)


def _sanitize_tool_response(
    tool_name: str,
    raw: str,
    query: dict[str, Any] | None = None,
) -> str:
    """Single sanitizer entry for every MCP tool response.

    * Always normalize `updated_at` → Malaysia time.
    * For `crm_inventory_stock_*` tools, additionally strip hidden quantity
      fields (preserves existing stock-tool sanitization behavior).
    * For every `crm_inventory_*` tool, relabel warehouse master keys to the
      Sage-aligned vocabulary (system_location, system_location_description,
      warehouse, system_location_id). Backend Pydantic response unchanged.
    * For `crm_inventory_stock_*` tools, finally slim the nested `warehouse`
      object on each row to its literal-text identifiers.
    * The stock-visibility blocks (`_STOCK_POLICY_BLOCK_KEYS`) sit out those three
      row-shaped passes and are restored verbatim - see the constant.
    * For `crm_procurement_grn_*` tools, slim picking response (rename to
      document_number/receiving_date, drop internal status/cost/inspection).
    * For the multi-company labelled tools (`_COMPANY_ID_STRIP_TOOLS`), drop
      `company_id` from the rows (see `_strip_row_company_ids`).
    """
    data = _json_loads_safe(raw)
    if data is None:
        return raw
    data = _normalize_updated_at(data)
    held_policy_blocks: dict[str, Any] = {}
    if tool_name.startswith(STOCK_TOOL_PREFIX) and isinstance(data, dict):
        for key in _STOCK_POLICY_BLOCK_KEYS:
            if key in data:
                held_policy_blocks[key] = data.pop(key)
    if tool_name.startswith(STOCK_TOOL_PREFIX):
        data = _strip_stock_hidden_fields(data)
    if tool_name.startswith(INVENTORY_TOOL_PREFIX):
        data = _relabel_warehouse_keys(data)
    if tool_name.startswith(STOCK_TOOL_PREFIX):
        data = _slim_stock_nested_warehouse(data)
    if held_policy_blocks and isinstance(data, dict):
        data.update(held_policy_blocks)
    if tool_name in _ORDERS_SLIM_TOOLS:
        product_query = (query or {}).get("product_query") if isinstance(query, dict) else None
        data = _slim_orders_list_response(data, product_query if isinstance(product_query, str) else None)
    if tool_name.startswith(_GRN_TOOL_PREFIX):
        data = _slim_grn_response(data)
    if tool_name in _PROJECT_SLIM_TOOLS:
        data = _slim_projects_response(data)
    if tool_name == _QUOTATION_SLIM_TOOL:
        data = _slim_rows(data, _QUOTATION_DROP_KEYS)
    if any(tool_name.startswith(p) for p in _ATTACHMENT_TOOL_PREFIXES):
        data = _strip_attachment_internals(data)
    if tool_name == _RESOURCE_ATTACHMENT_LIST_TOOL:
        data = _strip_resource_attachment_list_extras(data)
    if tool_name == _RESOURCE_ATTACHMENT_CATALOGUE_TOOL:
        data = _slim_resource_attachment_catalogue_rows(data)
    if any(tool_name.startswith(p) for p in _PROMO_PRODUCT_TOOL_PREFIXES):
        data = _slim_promotion_products_response(data)
    if tool_name == _PROMOTIONS_LIST_TOOL:
        # Header-only: drop row UUIDs, scrub attachment internals on the promo's
        # own files. Products are NOT nested (the PDF carries SKU detail).
        data = _strip_promotions_list_row_ids(data)
        data = _strip_attachment_internals(data)
    if tool_name == _PRODUCTS_LIST_TOOL:
        # Merge tool: drop confidential economics + scrub nested attachment internals.
        data = _strip_products_list_confidential(data)
        data = _strip_attachment_internals(data)
    if tool_name == _FORMS_LIST_TOOL:
        narrowed = bool((query or {}).get("form_ids"))
        data = _slim_forms_list_rows(data, narrowed)
        if narrowed:
            data = _strip_attachment_internals(data)
    if tool_name == _PORTAL_LINK_TOOL and isinstance(data, dict):
        # The portal link is the deliverable; the raw expiry timestamp is
        # internal bookkeeping the assistant should not surface to the user.
        data.pop("expires_at", None)
    # Browse-mode attachment strip - applied LAST so it runs after the
    # promotion / forms row-level sanitizers have already done their work.
    for rule_tool, narrowing_keys, drop_keys in _BROWSE_ATTACHMENT_STRIP_RULES:
        if tool_name == rule_tool and _is_browse_mode(query, narrowing_keys):
            data = _strip_inline_attachment_keys(data, drop_keys)
    # Last, for the labelled tools only: the per-company label ships
    # `company_name` on the rows; the id it was resolved from stays out of the
    # agent's context.
    data = _strip_row_company_ids(tool_name, data)
    return json.dumps(data)


# Back-compat alias: existing imports / tests can keep using the old name.
_sanitize_stock_tool_response = _sanitize_tool_response


async def _execute_tool_request(spec: ToolSpec, client: Any, path_params: dict[str, Any], query: dict[str, Any]) -> str:
    # Recovery path: if a *_get tool receives non-UUID id text (e.g. "bathtubs"),
    # route to sibling list endpoint with query search instead of throwing DB UUID errors.
    for p_name, p_val in path_params.items():
        if p_name.endswith("_id") and isinstance(p_val, str) and not _looks_like_uuid(p_val):
            if spec.name in _INCOMING_SHIPMENT_DETAIL_TOOLS and p_name == "shipment_id":
                resolved_ref = await _resolve_incoming_shipment_reference(client, p_val)
                if resolved_ref:
                    path_params[p_name] = resolved_ref
                # Let backend resolver still try direct business identifiers
                # (shipment number / container / BOL / invoice) even when MCP
                # cannot resolve this input to one candidate.
                continue
            if spec.name in _PRODUCT_CODE_RESOLVABLE_TOOLS and p_name == "product_id":
                resolved_pid, candidates = await _resolve_product_reference(client, p_val)
                if resolved_pid:
                    path_params[p_name] = resolved_pid
                    continue
                suggestions = [
                    {
                        "id": str(r.get("id")) if r.get("id") else None,
                        "product_code": r.get("product_code"),
                        "product_name": r.get("product_name"),
                    }
                    for r in candidates[:10]
                ]
                return json.dumps(
                    {
                        "message": (
                            f"Could not uniquely resolve product '{p_val}' for tool '{spec.name}'."
                        ),
                        "suggestion": (
                            "Re-call with the product UUID from `id` below, or a more specific "
                            "product_code. Use crm_master_products_list with query for broader search."
                        ),
                        "code": "AMBIGUOUS_PRODUCT_REFERENCE" if suggestions else "PRODUCT_NOT_FOUND",
                        "candidates": suggestions,
                    }
                )
            base_path = spec.path.replace(f"/{{{p_name}}}", "")
            list_spec = _list_spec_by_path(base_path)
            if list_spec:
                fallback_query: dict[str, Any] = {}
                # Preserve overlapping query filters (e.g. contact_id/space_id scope)
                # when auto-falling back from *_get(non-UUID id) to sibling list endpoint.
                for qk, qv in (query or {}).items():
                    if qk in list_spec.query_params and qv not in (None, ""):
                        fallback_query[qk] = qv
                if "query" in list_spec.query_params:
                    fallback_query["query"] = p_val
                elif "q" in list_spec.query_params:
                    fallback_query["q"] = p_val
                if "limit" in list_spec.query_params:
                    fallback_query.setdefault("limit", "10")
                if "page" in list_spec.query_params:
                    fallback_query.setdefault("page", "1")
                # If sibling list endpoint has no free-text query, still return a bounded first page
                # so callers get a useful response instead of a UUID cast failure.
                fallback_resp = await client.get(list_spec.path, path_params={}, query=fallback_query, tool_name=spec.name)
                fallback_resp = _filter_active_promotion_records(spec.name, fallback_resp)
                return _sanitize_tool_response(spec.name, fallback_resp, query=fallback_query)
            return json.dumps(
                {
                    "message": f"Parameter '{p_name}' expects a UUID for tool '{spec.name}'.",
                    "suggestion": f"Use a list/search tool with query='{p_val}' to find the correct id first.",
                    "code": "INVALID_IDENTIFIER_FORMAT",
                }
            )
    if spec.name == "crm_marketing_promotions_get":
        inactive_msg = await _ensure_promotion_active(client, path_params.get("promotion_id"))
        if inactive_msg is not None:
            return inactive_msg
    elif spec.name in {
        "crm_marketing_promotion_products_nested",
        "crm_marketing_promotion_attachments_by_promotion",
    }:
        inactive_msg = await _ensure_promotion_active(client, path_params.get("promotion_id"))
        if inactive_msg is not None:
            return inactive_msg
    elif spec.name in {"crm_marketing_promotion_products_list", "crm_marketing_promotion_attachments_list"}:
        # List endpoints may be used as broad search (e.g., by SKU text). Do not hard-block
        # on promotion activity precheck here; the backend applies active-first + inactive
        # fallback (active=True default for API-key callers), so the payload already carries
        # the right rows + fallback_used flag.
        pass

    if spec.name == _GRN_LIST_TOOL:
        query = dict(query or {})
        query["limit"] = _coerce_grn_limit(query.get("limit"))

    missing_narrowing = _missing_narrowing_filters(spec.name, query)
    if missing_narrowing is not None:
        return _empty_narrowing_response(spec.name, query, missing_narrowing)

    response = await client.request(
        spec.method,
        spec.path,
        path_params=path_params,
        query=query,
        tool_name=spec.name,
    )
    # Merge-tool nesting: fold child rows (promotion products / product
    # attachments) under their parent BEFORE sanitize, while parent UUIDs the
    # fan-out keys on are still present.
    response = await _enrich_list_response(spec.name, client, response, query)
    response = _filter_active_promotion_records(spec.name, response)
    return _sanitize_tool_response(spec.name, response, query=query)


async def _execute_tool_request_with_body(
    spec: ToolSpec,
    client: Any,
    path_params: dict[str, Any],
    query: dict[str, Any],
    body: Any,
) -> str:
    if spec.method.upper() == "GET":
        return await _execute_tool_request(spec, client, path_params, query)
    response = await client.request(
        spec.method,
        spec.path,
        path_params=path_params,
        query=query,
        body=body,
        tool_name=spec.name,
    )
    return _sanitize_tool_response(spec.name, response, query=query)


def _compile_tool(spec: ToolSpec):
    """Build async (ctx, ...) -> str for one catalog entry; Context carries CRMClient from lifespan."""
    pp_sig = ", ".join(f"{p}: str" for p in spec.path_params)
    query_params_with_aliases = list(spec.query_params)
    aliases = TOOL_QUERY_ALIASES.get(spec.name, {})
    for _canonical, alias_names in aliases.items():
        for alias_name in alias_names:
            if alias_name not in query_params_with_aliases:
                query_params_with_aliases.append(alias_name)
    # Opt-in `view=render` param: presenter tools expose it so callers can ask for
    # the uniform render envelope. Popped before the backend call (see impl).
    # Skip tools with body params - an optional query arg cannot precede a
    # required body arg in the generated signature (e.g. portal_link_get).
    if (
        spec.name in PRESENTER_TOOLS
        and not spec.body_params
        and "view" not in query_params_with_aliases
    ):
        query_params_with_aliases.append("view")
    qp_for_sig = list(query_params_with_aliases)
    # Body params: those listed in TOOL_OPTIONAL_BODY_PARAMS become `str | None =
    # None` (caller may omit); the rest stay required `str`. Required-first so the
    # generated signature stays valid (no required param after an optional one).
    optional_body_set = set(TOOL_OPTIONAL_BODY_PARAMS.get(spec.name, ()))
    _bp_all = list(spec.body_params)
    bp_for_sig = [b for b in _bp_all if b not in optional_body_set] + [
        b for b in _bp_all if b in optional_body_set
    ]
    # Promote any query param declared in TOOL_REQUIRED_QUERY_HINTS to a
    # no-default `str` argument so the generated JSON schema marks it
    # `required:true`. Without this the LLM treats it as optional and skips it.
    # Sort required-first so the signature stays valid Python (no required after
    # optional).
    required_query_set = {
        q for q in TOOL_REQUIRED_QUERY_HINTS.get(spec.name, ())
        if q in qp_for_sig
    }
    qp_required_sorted = [q for q in qp_for_sig if q in required_query_set]
    qp_optional_sorted = [q for q in qp_for_sig if q not in required_query_set]
    qp_for_sig = qp_required_sorted + qp_optional_sorted
    # Accept str / int / float / bool / list so LLMs that hand back native JSON
    # types (e.g. `limit: 10`, `is_active: true`) don't trip Pydantic's strict
    # type validation. `_normalize_query_value` + httpx serialize scalars to
    # str for the outbound HTTP query.
    _scalar_union = "str | int | float | bool | list[str]"
    # Ported from PR #1118 (not merged): params declared as JSON objects take an
    # object-first union instead, so the value survives FastMCP's `pre_parse_json`
    # (see TOOL_OBJECT_QUERY_PARAMS).
    _object_union = "dict[str, int] | str"
    object_query_set = {
        q for q in TOOL_OBJECT_QUERY_PARAMS.get(spec.name, ()) if q in qp_for_sig
    }

    def _union_for(q: str) -> str:
        return _object_union if q in object_query_set else _scalar_union

    qp_sig = ", ".join(
        (
            f"{q}: {_union_for(q)}"
            if q in required_query_set
            else f"{q}: {_union_for(q)} | None = None"
        )
        for q in qp_for_sig
    )
    bp_sig = ", ".join(
        (f"{b}: str | None = None" if b in optional_body_set else f"{b}: str")
        for b in bp_for_sig
    )
    parts = [p for p in (pp_sig, qp_sig, bp_sig) if p]
    sig = ", ".join(parts)

    pp_dict = "{" + ", ".join(f'"{p}": {p}' for p in spec.path_params) + "}"
    q_dict = "{" + ", ".join(f'"{q}": {q}' for q in qp_for_sig) + "}"
    b_dict = "{" + ", ".join(f'"{b}": {b}' for b in bp_for_sig) + "}"
    aliases_repr = repr(aliases)
    required_hints = repr(TOOL_REQUIRED_QUERY_HINTS.get(spec.name, ()))
    default_q = repr(TOOL_DEFAULT_QUERY_PARAMS.get(spec.name, {}))

    fname = f"_impl_{spec.name}"
    sig_with_ctx = f"ctx: Context, {sig}" if sig else "ctx: Context"
    code = (
        f"async def {fname}({sig_with_ctx}):\n"
        f"    client = ctx.request_context.lifespan_context['client']\n"
        f"    _settings = ctx.request_context.lifespan_context['settings']\n"
        f"    _pp = {pp_dict}\n"
        f"    _qq = {q_dict}\n"
        f"    _bb = {b_dict}\n"
        f"    _aliases = {aliases_repr}\n"
        f"    _required = {required_hints}\n"
        f"    _defaults = {default_q}\n"
        f"    for _canonical, _alias_names in _aliases.items():\n"
        f"        if _qq.get(_canonical) is None:\n"
        f"            for _alias in _alias_names:\n"
        f"                if _qq.get(_alias) is not None:\n"
        f"                    _qq[_canonical] = _qq[_alias]\n"
        f"                    break\n"
        f"    for _k, _v in list(_qq.items()):\n"
        f"        _qq[_k] = _normalize_query_value(_v)\n"
        f"    for _uk, _uv in list(_qq.items()):\n"
        f"        if _is_uuid_param(_uk):\n"
        f"            _validate_uuid_param(_uk, _uv)\n"
        f"    for _pk, _pv in list(_pp.items()):\n"
        f"        if _is_uuid_param(_pk):\n"
        f"            _validate_uuid_param(_pk, _pv)\n"
        f"    q = {{k: v for k, v in _qq.items() if v is not None and v != []}}\n"
        f"    for _dk, _dv in _defaults.items():\n"
        f"        if q.get(_dk) in (None, ''):\n"
        f"            q[_dk] = _dv\n"
        f"    body = None\n"
        f"    if _bb:\n"
        f"        _decoded = {{}}\n"
        f"        for _bk, _bv in _bb.items():\n"
        f"            if _bv is None:\n"
        f"                continue\n"
        f"            if not isinstance(_bv, str):\n"
        f"                _decoded[_bk] = _bv\n"
        f"                continue\n"
        f"            if _bv.lstrip()[:1] in ('{{', '['):\n"
        f"                try:\n"
        f"                    _decoded[_bk] = json.loads(_bv)\n"
        f"                except Exception:\n"
        f"                    _decoded[_bk] = _bv\n"
        f"            else:\n"
        f"                _decoded[_bk] = _bv\n"
        f"        if 'payload_json' in _decoded:\n"
        f"            _payload = _decoded.get('payload_json')\n"
        f"            if isinstance(_payload, dict):\n"
        f"                for _k, _v in _decoded.items():\n"
        f"                    if _k == 'payload_json':\n"
        f"                        continue\n"
        f"                    if _k not in _payload:\n"
        f"                        _payload[_k] = _v\n"
        f"            body = _payload\n"
        f"        else:\n"
        f"            body = _decoded\n"
        f"    _missing = [k for k in _required if q.get(k) in (None, '')]\n"
        f"    if _missing:\n"
        f"        raise ValueError('Missing required query parameter(s): ' + ', '.join(_missing))\n"
        f"    _view = q.pop('view', None)\n"
        f"    _resp = await _execute_tool_request_with_body(_spec, client, _pp, q, body)\n"
        f"    _resp = await _attach_suggested_escalation(_spec.name, _resp, api_url=_settings.crm_base_url, api_key=_settings.external_api_key)\n"
        f"    if _view == 'render':\n"
        f"        _resp = _present_response(_spec.name, _resp)\n"
        f"    return _resp\n"
    )
    ns: dict[str, Any] = {
        "Context": Context,
        "_normalize_query_value": _normalize_query_value,
        "_is_uuid_param": _is_uuid_param,
        "_validate_uuid_param": _validate_uuid_param,
        "_execute_tool_request": _execute_tool_request,
        "_execute_tool_request_with_body": _execute_tool_request_with_body,
        "_attach_suggested_escalation": attach_suggested_escalation,
        "_present_response": present_response,
        "json": json,
        "_spec": spec,
    }
    exec(code, ns)  # noqa: S102 - catalog-only templates
    return ns[fname]


def create_mcp_app(settings: Settings) -> FastMCP:
    @asynccontextmanager
    async def lifespan(_app: FastMCP) -> AsyncIterator[dict[str, Any]]:
        c = CRMClient(settings)
        try:
            yield {"client": c, "settings": settings}
        finally:
            await c.aclose()

    mcp = FastMCP(
        "Sorento CRM (read-only)",
        instructions=(
            "Read-only tools that mirror Sorento CRM GET APIs. "
            "Use pagination (page, limit). Pass filter ids and codes as strings. "
            "Combine multiple tools to answer cross-entity questions."
        ),
        host=settings.mcp_host,
        port=settings.mcp_port,
        log_level=settings.log_level.upper(),  # type: ignore[arg-type]
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        stateless_http=True,
        lifespan=lifespan,
    )

    hidden_internal = 0
    for spec in merged_catalog(CATALOG):
        if getattr(spec, "internal", False) and not settings.mcp_expose_internal:
            hidden_internal += 1
            continue
        if getattr(spec, "external", False):
            # External tools (e.g. Outline-backed user_guides_*) are registered
            # by their dedicated handler below - skip the HTTP-backed compile.
            continue
        impl = _compile_tool(spec)
        mcp.add_tool(impl, name=spec.name, description=spec.description)
        logger.debug("Registered MCP tool %s", spec.name)
    if hidden_internal:
        logger.info("Hid %d internal MCP tools (MCP_EXPOSE_INTERNAL=0)", hidden_internal)

    register_user_guide_tools(mcp, settings)
    logger.debug("Registered user-guide tools")

    register_record_action_tools(mcp, settings)
    logger.debug("Registered record-action tools")

    register_ideation_tools(mcp, settings)
    logger.debug("Registered ideation tools")

    return mcp
