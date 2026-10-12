"""The generic find ask (CHAT-FIND): the parser's `find.filters` become one `crm_find` call.

`documentation/plans/chatbot/PLAN-chat-generic-find-12oct.md` section 2.5. The parser decides
what the message means (`find.filters`, keys from `product_find.FILTERS`); the resolver decides
which products it names; the route (`GET /api/v1/master-data/products/find`, MCP `crm_find`)
answers under the contact's company, brand and stock policy. This module only:

* `filter_items`: the registered filters the parse carries, by key, in order, once each (an
  unknown key is dropped: the registry is the vocabulary, never the model);
* `named_this_turn`: the resolved product entities this message named, when it named any (a
  product carried from an earlier turn is not the subject of a new find), else the carried ones
  (a bare "which seat cover fits it?");
* `route_args`: the transformer's args as the route's `filters` (products and typed values), or
  None and the filter's own question when it has nothing to run on (card rule 6).
"""
from __future__ import annotations

from typing import Any, Optional

from app.services import product_find
from app.services.chatbot import jsc

TOOL = "crm_find"
ASK_PRODUCT_LINE = product_find.ASK_PRODUCT_LINE
#: The transformer's args the route takes besides `filters`.
_PASSED = ("contact_id", "space_id", "view")


def filter_items(parse_output: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The registered filters the parse carries, by key, in order, first one per key (an
    unknown key is dropped: the registry is the vocabulary, never the model)."""
    find = parse_output.get("find") if isinstance(parse_output, dict) else None
    items: dict[str, dict[str, Any]] = {}
    for item in jsc.array(find.get("filters") if isinstance(find, dict) else None):
        key = item.get("key") if isinstance(item, dict) else None
        if isinstance(key, str) and key in product_find.FILTERS and key not in items:
            items[key] = item
    return items


def filter_keys(parse_output: dict[str, Any]) -> list[str]:
    return list(filter_items(parse_output))


def _key(value: Any) -> str:
    """A code compared by its letters and digits only, casefolded: the parser's token and the
    resolved code may differ by dashes, spaces and case ("SRTWC8383 RL MBL" names
    SRTWC8383-RL-MBL). Data against data; the user's words are never pattern-matched."""
    return "".join(ch for ch in jsc.js_string(value or "").casefold() if ch.isalnum())


def _is_product(entity: Any) -> bool:
    return isinstance(entity, dict) and entity.get("entity_type") == "product"


def named_this_turn(parse_output: dict[str, Any], entities: list[Any]) -> list[Any]:
    """`entities` with the products this find is about.

    * The parse names a product this message: the resolved products whose code matches one of
      its tokens; when none matches, every resolved product (the resolver resolved what was
      typed, so a mismatch in spelling never turns into asking again).
    * The parse names no product this message but carries one (a product entity the parser
      marked as not from this message, e.g. "which seat cover fits it?"): the resolved products.
    * No product entity at all: no product, so the lane asks for the code (card rule 6).
    """
    parsed = [
        e for e in jsc.array(parse_output.get("entities"))
        if isinstance(e, dict) and jsc.js_string(e.get("hint") or "") == "product"
    ]
    if not parsed:
        return [e for e in entities if not _is_product(e)]
    tokens = {
        _key(tok)
        for e in parsed
        if e.get("current_message") is True
        for tok in (e.get("raw"), e.get("canonical_code"))
        if jsc.truthy(tok)
    }
    tokens.discard("")
    if not tokens:
        return list(entities)
    named = [
        e for e in entities
        if _is_product(e)
        and any(tok in code for code in (_key(e.get("code")), _key(e.get("canonical_code"))) if code for tok in tokens)
    ]
    if not named:
        return list(entities)
    return [e for e in entities if not _is_product(e)] + named


def _ids(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [v for v in value.split(",")]
    out: list[str] = []
    for v in jsc.array(value):
        s = jsc.js_string(v).strip()
        if s and s not in out:
            out.append(s)
    return out


def route_args(args: dict[str, Any], items: dict[str, dict[str, Any]]) -> tuple[Optional[dict[str, Any]], str]:
    """The route's args, or None and the line to ask when a filter has nothing to run on
    (its row's own `ask`: no product named, or no value given)."""
    product_ids = _ids(args.get("product_ids"))
    filters: list[dict[str, Any]] = []
    for key, item in items.items():
        row = product_find.FILTERS[key]
        entry: dict[str, Any] = {"key": key}
        if row.products:
            if not product_ids:
                return None, row.ask
            entry["product_ids"] = product_ids
        if row.value is not None:
            value = item.get("value")
            if value is None or (isinstance(value, str) and not value.strip()):
                return None, row.ask
            entry["value"], entry["unit"] = value, item.get("unit")
        filters.append(entry)
    out = {k: args[k] for k in _PASSED if k in args}
    out["filters"] = filters
    return out, ""
