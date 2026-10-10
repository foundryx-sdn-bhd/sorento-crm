"""Scripted fakes for the agent engine tests: no live LLM, no live MCP.

`FakeChat` replays a script of `ChatResult`s and records every call (messages + tools).
`McpRecorder` stands in for `services._mcp_call(db)`: `(name, args) -> result`.
"""
from __future__ import annotations

import copy
import json
from typing import Any

from app.services.llm_provider import ChatResult

# Reveal keys of the domains the fake catalog touches (turn.policy.default_policy seeds).
ALL_KEYS = frozenset(
    {
        "inventory.sellable",
        "sales_orders.outstanding",
        "sales_orders.sales_report",
        "purchase_orders.placed",
        "purchase_orders.cost",
    }
)
DEALER_KEYS = ALL_KEYS - {"purchase_orders.cost"}

MARKER = "ZZMARK-DEEP-ROW-9931"

CATALOG: dict[str, dict[str, Any]] = {
    "crm_inventory_stock_balance_list": {
        "description": "Stock balances per product and location.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "contact_id": {"type": "string"},
                "space_id": {"type": "string"},
                "product_ids": {"type": "array", "items": {"type": "string"}},
                "requested_quantities": {"type": "array", "items": {"type": "number"}},
            },
            "required": ["contact_id", "space_id"],
        },
    },
    "crm_order_management_orders_list": {
        "description": "Sales orders for customers.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "contact_id": {"type": "string"},
                "space_id": {"type": "string"},
                "customer_ids": {"type": "array", "items": {"type": "string"}},
                "order_ids": {"type": "array", "items": {"type": "string"}},
                "customer_query": {"type": "string"},
            },
            "required": ["contact_id", "space_id"],
        },
    },
    "crm_procurement_po_last_cost_list": {
        "description": "Last purchase cost per product.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "contact_id": {"type": "string"},
                "space_id": {"type": "string"},
                "product_ids": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["contact_id", "space_id"],
        },
    },
    "crm_master_products_list": {
        "description": "Products.",
        "inputSchema": {
            "type": "object",
            "properties": {"contact_id": {"type": "string"}, "space_id": {"type": "string"}, "query": {"type": "string"}},
        },
    },
    "crm_master_brands_list": {
        "description": "Brands.",
        "inputSchema": {"type": "object", "properties": {"contact_id": {"type": "string"}, "space_id": {"type": "string"}}},
    },
    "crm_portal_link_get": {
        "description": "Portal link.",
        "inputSchema": {
            "type": "object",
            "properties": {"contact_id": {"type": "string"}, "space_id": {"type": "string"}, "submission_type": {"type": "string"}},
        },
    },
    # Present in the catalog on purpose: not on the read-only allow-list, must never be callable.
    "crm_order_cancel": {
        "description": "Cancel an order (WRITE).",
        "inputSchema": {"type": "object", "properties": {"order_id": {"type": "string"}}},
    },
}


def rows_result(n: int = 30) -> dict[str, Any]:
    """A bulky MCP-style result; the marker sits on a deep row so a short summary cannot hold it."""
    items = [{"product_code": f"SRT-TEST-{i}", "available": i, "note": f"row {i}"} for i in range(1, n + 1)]
    items[19]["note"] = MARKER
    return {"items": items, "total": n}


def tool_call(name: str, arguments: dict[str, Any] | None = None, call_id: str = "call_1") -> dict[str, Any]:
    return {"id": call_id, "name": name, "arguments": arguments or {}}


def result(content: str = "", calls: list[dict[str, Any]] | None = None, *, prompt: int = 100, cached: int = 40, out: int = 10) -> ChatResult:
    return ChatResult(
        content=content,
        prompt_tokens=prompt,
        completion_tokens=out,
        total_tokens=prompt + out,
        tool_calls=calls or [],
        cached_tokens=cached,
    )


class FakeChat:
    """`chat(messages, tools) -> ChatResult`. `script` items are ChatResult, Exception, or a callable."""

    def __init__(self, script: list[Any] | None = None, *, default: Any = None) -> None:
        self.script = list(script or [])
        self.default = default
        self.calls: list[dict[str, Any]] = []

    def __call__(self, messages, tools=None, *args, **kwargs):
        self.calls.append({"messages": copy.deepcopy(messages), "tools": copy.deepcopy(tools)})
        if self.script:
            item = self.script.pop(0)
        elif self.default is not None:
            item = self.default
        else:
            raise AssertionError("FakeChat script exhausted")
        if callable(item) and not isinstance(item, ChatResult):
            item = item(messages, tools)
        if isinstance(item, BaseException):
            raise item
        return item

    def prompt_text(self, call_index: int = -1) -> str:
        return json.dumps(self.calls[call_index]["messages"], default=str)


class McpRecorder:
    def __init__(self, response: Any = None, *, raises: dict[str, Exception] | None = None) -> None:
        self.response = rows_result() if response is None else response
        self.raises = raises or {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, name: str, args: dict[str, Any]) -> Any:
        self.calls.append((name, copy.deepcopy(args)))
        if name in self.raises:
            raise self.raises[name]
        return self.response


class Recorder:
    """A generic `(*args, **kwargs)` recorder returning `response`."""

    def __init__(self, response: Any = None) -> None:
        self.response = response if response is not None else {"matches": []}
        self.calls: list[tuple[tuple, dict]] = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.response

    def seen(self, needle: Any) -> bool:
        return needle in json.dumps([self.calls], default=str)
