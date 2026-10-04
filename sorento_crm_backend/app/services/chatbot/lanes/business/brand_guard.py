"""The fetch output guard for a brand-scoped contact (CONTACT-BRAND-SCOPE, AC-17).

Last of three layers (session criterion, raw-SQL filters, this). After a tool returns, every
list item naming a product outside the contact's brands is dropped before the answer is
built. An item names a product by `product_code`, `item_code` or `product_id`, on itself or
in a dict nested inside it; a nested LIST is walked on its own, so a shipment keeps its
header while its out-of-scope lines go.

Only tools marked `filtered` in `contracts.BRAND_SCOPE_TREATMENT` are guarded; a tool missing
from the map is treated as `filtered` (fail closed). A guard that cannot look the products
up (no session, a query error) treats every named product as out of scope, never as in.
Unscoped (no scope given) returns the result untouched.
"""
from __future__ import annotations

import logging
from typing import Any

from app.services.chatbot import contracts

logger = logging.getLogger(__name__)

_CODE_KEYS = ("product_code", "item_code")
_ID_KEYS = ("product_id",)


def _names(item: dict[str, Any], codes: set[str], ids: set[str], extra: tuple[str, ...] = ()) -> bool:
    """Collect the product codes / ids `item` names (nested dicts too, nested lists not);
    True when it names at least one. `extra` are the tool's own product keys."""
    found = False
    for key, value in item.items():
        if isinstance(value, dict):
            found = _names(value, codes, ids, extra) or found
        elif isinstance(value, str) and value:
            if key in _CODE_KEYS or key in extra:
                codes.add(value)
                found = True
            elif key in _ID_KEYS:
                ids.add(value)
                found = True
    return found


def _walk(node: Any, codes: set[str], ids: set[str], extra: tuple[str, ...]) -> None:
    if isinstance(node, list):
        for item in node:
            if isinstance(item, dict):
                _names(item, codes, ids, extra)
            _walk(item, codes, ids, extra)
    elif isinstance(node, dict):
        for value in node.values():
            _walk(value, codes, ids, extra)


def _prune(node: Any, bad_codes: set[str], bad_ids: set[str], extra: tuple[str, ...]) -> Any:
    if isinstance(node, list):
        kept = []
        for item in node:
            if isinstance(item, dict):
                codes: set[str] = set()
                ids: set[str] = set()
                if _names(item, codes, ids, extra) and (codes & bad_codes or ids & bad_ids):
                    continue
            kept.append(_prune(item, bad_codes, bad_ids, extra))
        return kept
    if isinstance(node, dict):
        return {k: _prune(v, bad_codes, bad_ids, extra) for k, v in node.items()}
    return node


def guard_result(tool_name: str, result: Any, scope_brand_ids: Any, db: Any) -> Any:
    """`result` with out-of-scope product items removed, or `result` itself when unscoped,
    the tool is `no_products`, or the result is not a container."""
    if not scope_brand_ids or not isinstance(result, (dict, list)):
        return result
    if contracts.BRAND_SCOPE_TREATMENT.get(tool_name, "filtered") != "filtered":
        return result
    scope = frozenset(str(b) for b in scope_brand_ids)
    extra = contracts.BRAND_SCOPE_PRODUCT_KEYS.get(tool_name, ())
    codes: set[str] = set()
    ids: set[str] = set()
    _walk(result, codes, ids, extra)
    if not codes and not ids:
        return result
    try:
        if db is None:
            raise RuntimeError("no session to look the products up with")
        from app.services.contact_brand_scope import out_of_scope_product_codes, out_of_scope_product_ids

        bad_codes = out_of_scope_product_codes(db, scope, codes)
        bad_ids = out_of_scope_product_ids(db, scope, ids)
    except Exception:  # noqa: BLE001 - fail closed: what cannot be checked is not shown
        logger.warning("chatbot: brand scope guard could not look products up, dropping them", exc_info=True)
        bad_codes, bad_ids = set(codes), set(ids)
    return _prune(result, bad_codes, bad_ids, extra)
