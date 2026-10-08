"""PRODUCT-ALTERNATIVES round B: the one shared resolver behind every product domain's
`Alternative:` line (card Addendum 1, owner 8 Oct 2026).

`run_fetch` calls `plan` after its tool answered. When an asked product is DISCONTINUED
and has a stored alternative (`product_alternative_service.alternatives_for`, the lookup
the stock tool uses), the SAME tool is called a second time with the alternative in place
of the asked product (`alternative_trigger`), so the alternative is answered by that
domain's own logic, permissions and brand guard. `fold` then puts that answer into the
reply:

    incoming, dealer   `Alternative: X-NEW: ✅ ETA 20/10/2026` under the old line
    incoming, staff    `*Alternative:* X-NEW` under the old rows, then X-NEW's rows
    product info       `*Alternative:* X-NEW` under the old block, then X-NEW's block
    attachment         `Alternative: X-NEW: <File Name>` under the old file, X-NEW's file sent
    promotion          after the list: `Alternative: X-NEW: <Promotion> (<start> to <end>)`,
                       or `Alternative: X-NEW: No promotion.`

Delivery (orders) is deliberately absent (owner: a delivery history of the old code is what
was delivered), as are the stock tool (it has its own, `inventory_service`) and the
outstanding report. A product the contact also asked for in its own right is never offered
again as an alternative.
"""
from __future__ import annotations

import logging
from typing import Any

from app.services.chatbot import jsc

logger = logging.getLogger(__name__)

#: tool -> how its alternative is folded in.
TOOL_KINDS: dict[str, str] = {
    "crm_incoming_stock_list": "incoming",
    "crm_incoming_stock_by_product": "incoming",
    "crm_marketing_promotions_list": "promotion",
    "crm_master_product_attachments_list": "attachment",
    "crm_master_products_list": "product",
}

#: The miss sentence `turn/compose.py` would print for each kind ("No {label} found for
#: {codes}."), said here because a reply that carries an Alternative line is no longer the
#: bare fallback compose swaps.
_MISS_LABEL = {
    "incoming": "incoming stock",
    "promotion": "promotions",
    "attachment": "product attachments",
    "product": "product information",
}


def plan(db: Any, tool_name: str, entities: Any) -> list[tuple[str, Any]]:
    """`[(old_code, alternative Product)]` for the asked discontinued products, or []."""
    kind = TOOL_KINDS.get(tool_name)
    if db is None or kind is None:
        return []
    asked = {
        str(jsc.get(e, "uuid")): jsc.get(e, "code")
        for e in jsc.array(entities)
        if isinstance(e, dict) and e.get("entity_type") == "product" and e.get("uuid")
    }
    if not asked:
        return []
    from app.models.product import Product
    from app.services.product_alternative_service import alternatives_for

    discontinued = (
        db.query(Product)
        .filter(Product.id.in_(list(asked)), Product.is_discontinued.is_(True))
        .order_by(Product.product_code)
        .all()
    )
    if not discontinued:
        return []
    by_old = alternatives_for(db, [p.id for p in discontinued])
    pairs: list[tuple[str, Any]] = []
    seen: set[str] = set()
    for old in discontinued:
        for alternative in by_old.get(str(old.id)) or []:
            if alternative.id in asked or alternative.id in seen:
                continue
            seen.add(alternative.id)
            pairs.append((old.product_code, alternative))
    return pairs


def alternative_trigger(trigger: dict[str, Any], pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """The same trigger with the asked products swapped for their alternatives; every
    other filter (attachment type, date window, tier, customer) is kept, so the second
    call asks the same question about the alternative."""
    kept = [
        e
        for e in jsc.array(trigger.get("entities"))
        if not (isinstance(e, dict) and e.get("entity_type") == "product")
    ]
    swapped = [
        {"entity_type": "product", "uuid": alternative.id, "code": alternative.product_code}
        for _old, alternative in pairs
    ]
    return {**trigger, "entities": kept + swapped}


def _items(envelope: Any) -> list:
    return jsc.array(jsc.get(envelope, "items")) if isinstance(envelope, dict) else []


def _code_of(item: Any) -> str:
    """An item's product code: its `Product Code` field, else its title up to `": "`
    (the dealer ETA line and the availability line both open "<code>: ")."""
    for f in jsc.array(jsc.get(item, "fields")):
        if jsc.get(f, "label") == "Product Code" and jsc.truthy(jsc.get(f, "value")):
            return jsc.js_string(jsc.get(f, "value")).strip().upper()
    title = jsc.nullish_str(jsc.get(item, "title"))
    return title.split(": ", 1)[0].strip().upper()


def _field(item: Any, label: str) -> Any:
    for f in jsc.array(jsc.get(item, "fields")):
        if jsc.get(f, "label") == label:
            return jsc.get(f, "value")
    return None


def _flags(item: dict) -> dict:
    flags = item.get("flags")
    if not isinstance(flags, dict):
        flags = {}
        item["flags"] = flags
    return flags


def fold(
    tool_name: str,
    envelope: dict[str, Any],
    alternative_envelope: Any,
    pairs: list[tuple[str, Any]],
    fmt_value: Any,
    *,
    status_only: bool = False,
) -> list[str]:
    """Put the alternatives' answers into `envelope` (mutated) and return the tail lines
    to print after the rendered reply (promotion, or an old code with no row to hang a
    line under). `fmt_value` is `fetch._fmt_value`, so dates read as the reply's own."""
    kind = TOOL_KINDS[tool_name]
    items = envelope.get("items") if isinstance(envelope.get("items"), list) else None
    if items is None:
        items = []
        envelope["items"] = items
    alt_items = _items(alternative_envelope)
    tail: list[str] = []

    for old_code, alternative in pairs:
        alt_code = (alternative.product_code or "").strip()
        own = [it for it in alt_items if isinstance(it, dict) and _code_of(it) == alt_code.upper()]
        anchors = [
            i for i, it in enumerate(items) if isinstance(it, dict) and _code_of(it) == old_code.strip().upper()
        ]
        anchor = items[anchors[-1]] if anchors else None

        if kind == "promotion":
            own = [it for it in alt_items if isinstance(it, dict)]
            if not own:
                tail.append(f"Alternative: {alt_code}: No promotion.")
            for it in own:
                name = jsc.nullish_str(_field(it, "Promotion") or jsc.get(it, "title"))
                start = fmt_value(_field(it, "Start Date"))
                end = fmt_value(_field(it, "End Date"))
                tail.append(f"Alternative: {alt_code}: {name} ({start} to {end})")
            continue

        if kind == "incoming" and (anchor is None or jsc.truthy(jsc.get(anchor.get("flags"), "dealer_view"))):
            # Dealer line, or no old row at all: one line, the alternative's own ETA line.
            line = jsc.nullish_str(jsc.get(own[0], "title")) if own else f"{alt_code}: No ETA"
            if anchor is not None:
                _flags(anchor).setdefault("alternative_lines", []).append(line)
            else:
                tail.append(f"Alternative: {line}")
            continue

        if kind == "attachment":
            lines = [f"{alt_code}: {jsc.nullish_str(_field(it, 'File Name'))}" for it in own if _field(it, "File Name")]
            if anchor is not None:
                _flags(anchor).setdefault("alternative_lines", []).extend(lines)
            else:
                tail.extend(f"Alternative: {line}" for line in lines)
            if own:
                files = jsc.array(jsc.get(alternative_envelope, "attachments"))
                envelope["attachments"] = jsc.array(envelope.get("attachments")) + list(files)
            continue

        if kind == "product" and anchor is not None:
            # DISCONTINUED-ASK (#1535) names the item's top-level `alternatives` on its
            # status line; feed it. A status-only answer is one line per product, so the
            # alternative's own block (its own "Not discontinued." line) is not added.
            anchor["alternatives"] = jsc.array(anchor.get("alternatives")) + [alt_code]
            if status_only:
                continue

        # Staff incoming and product info: the code under the old row(s), then the
        # alternative's own rows right after them.
        if anchor is None:
            # No old row to hang it under (the old code has nothing here): name the
            # alternative after the reply rather than listing its rows as if they
            # answered the question.
            if own:
                tail.append(f"*Alternative:* {alt_code}")
            continue
        _flags(anchor).setdefault("alternatives", []).append(alt_code)
        at = anchors[-1] + 1
        items[at:at] = own
    return tail


def miss_sentence(tool_name: str, old_codes: list[str]) -> str:
    return f"No {_MISS_LABEL[TOOL_KINDS[tool_name]]} found for {', '.join(old_codes)}."
