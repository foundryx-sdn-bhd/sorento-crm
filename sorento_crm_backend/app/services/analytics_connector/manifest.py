"""The tool manifest SS registers the connector's tools from (contract section 3).

34 reused read tools (``reused_tools.REUSED_TOOLS``, a static copy of the n8n catalog entries) plus
the five chat tools over ``/api/v1/connector/*``. ``version`` is a hash of the tools, so any change
tells SS to re-read. The route guard's list (``access.CONNECTOR_ROUTES``) is built from the same
reused table plus ``access.CHAT_ROUTES``; ``tests/analytics_connector/test_manifest.py`` holds the
two equal.
"""
from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from typing import Any

from app.services.analytics_connector import datasets
from app.services.analytics_connector.access import BASE
from app.services.analytics_connector.reused_tools import REUSED_TOOLS

ANNOTATIONS = {"readOnlyHint": True, "destructiveHint": False}

_STEER = (
    " Start with group_by and measures for counts and trends; pull raw message text only for samples,"
    " because the whole history is far larger than one answer."
)
_STR = {"type": "string"}
_INT = {"type": "integer"}
_DATE = {"type": "string", "format": "date"}

CHAT_TOOLS: tuple[dict, ...] = (
    {
        "name": "chat_list_datasets",
        "title": "List chat datasets",
        "description": "List the chat datasets (messages, contacts, bot_turns, episodes) with row counts and date ranges.",
        "method": "GET",
        "path": f"{BASE}/datasets",
        "properties": {},
        "required": [],
        "query_params": [],
    },
    {
        "name": "chat_describe_dataset",
        "title": "Describe a chat dataset",
        "description": "Describe one dataset: its columns (type, filterable, groupable, measurable) and three sample rows.",
        "method": "GET",
        "path": f"{BASE}/datasets/{{key}}",
        "properties": {"key": {"type": "string", "enum": sorted(datasets.CATALOG)}},
        "required": ["key"],
        "query_params": [],
    },
    {
        "name": "chat_query",
        "title": "Query a chat dataset",
        "description": (
            "Query a chat dataset with a whitelist builder: columns, filters {column, op, value} (ops eq ne in"
            " not_in gt gte lt lte contains is_null not_null), group_by (a column or a date bucket like"
            " sent_at:month|day|week|hour|dow|hour_of_day), measures {fn count|count_distinct|sum|avg|min|max,"
            f" column}}, period {{from, to}}, order_by, limit (max {datasets.MAX_ROWS}), offset. Times are"
            " Malaysia time." + _STEER
        ),
        "method": "POST",
        "path": f"{BASE}/query",
        "properties": {
            "dataset": {"type": "string", "enum": sorted(datasets.CATALOG)},
            "columns": {"type": "array", "items": _STR},
            "filters": {"type": "array", "items": {"type": "object"}},
            "group_by": {"type": "array", "items": _STR},
            "measures": {"type": "array", "items": {"type": "object"}},
            "period": {"type": "object", "properties": {"from": _DATE, "to": _DATE}},
            "order_by": {"type": "array", "items": {"type": "object"}},
            "limit": {"type": "integer", "minimum": 1, "maximum": datasets.MAX_ROWS},
            "offset": {"type": "integer", "minimum": 0},
        },
        "required": ["dataset"],
        "query_params": [],
    },
    {
        "name": "chat_search_messages",
        "title": "Search chat messages",
        "description": (
            "Search message text (case-insensitive contains) with optional contact, customer, direction, date"
            " range. Newest first, max 200 per page." + _STEER
        ),
        "method": "GET",
        "path": f"{BASE}/messages/search",
        "properties": {
            "text": _STR, "contact": _STR, "customer": _STR,
            "direction": {"type": "string", "enum": ["incoming", "outgoing"]},
            "date_from": _DATE, "date_to": _DATE,
            "limit": {"type": "integer", "minimum": 1, "maximum": 200}, "offset": {"type": "integer", "minimum": 0},
        },
        "required": [],
        "query_params": ["text", "contact", "customer", "direction", "date_from", "date_to", "limit", "offset"],
    },
    {
        "name": "chat_get_conversation",
        "title": "Get a conversation",
        "description": (
            "One contact's thread, oldest first, with bot turn status. contact may be an id, a Respond.io id or"
            " a name; several matches return a candidate list."
        ),
        "method": "GET",
        "path": f"{BASE}/conversation",
        "properties": {
            "contact": _STR, "date_from": _DATE, "date_to": _DATE,
            "limit": {"type": "integer", "minimum": 1, "maximum": 500}, "offset": {"type": "integer", "minimum": 0},
        },
        "required": ["contact"],
        "query_params": ["contact", "date_from", "date_to", "limit", "offset"],
    },
)


def _title(name: str) -> str:
    return name.removeprefix("crm_").replace("_", " ").capitalize()


def _reused(t: dict) -> dict:
    params = [*t["path_params"], *t["query_params"], *t["body_params"]]
    required = [*t["path_params"], *t["body_params"]]
    return _tool(
        name=t["name"], title=_title(t["name"]), description=t["description"], method=t["method"], path=t["path"],
        properties={p: dict(_STR) for p in params}, required=required,
        query_params=list(t["query_params"]), path_params=list(t["path_params"]),
    )


def _chat(t: dict) -> dict:
    path_params = ["key"] if "{key}" in t["path"] else []
    return _tool(
        name=t["name"], title=t["title"], description=t["description"], method=t["method"], path=t["path"],
        properties=t["properties"], required=t["required"], query_params=t["query_params"], path_params=path_params,
    )


def _tool(*, name, title, description, method, path, properties, required, query_params, path_params) -> dict:
    return {
        "name": name,
        "title": title,
        "description": description,
        "input_schema": {"type": "object", "properties": properties, "required": required, "additionalProperties": False},
        "annotations": dict(ANNOTATIONS),
        "http": {"method": method, "path_template": path, "query_params": query_params, "path_params": path_params},
    }


@lru_cache(maxsize=1)
def _cached() -> str:
    tools = [_reused(t) for t in REUSED_TOOLS] + [_chat(t) for t in CHAT_TOOLS]
    version = hashlib.sha256(json.dumps(tools, sort_keys=True).encode()).hexdigest()[:16]
    return json.dumps({"version": version, "tools": tools})


def build_manifest() -> dict[str, Any]:
    return json.loads(_cached())
