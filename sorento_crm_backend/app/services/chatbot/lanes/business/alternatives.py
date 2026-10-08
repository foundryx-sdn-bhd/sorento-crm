"""PRODUCT-ALTERNATIVES round B: the one shared resolver behind every product domain's
`Alternative:` line (card Addendum 1, owner 8 Oct 2026).

`run_fetch` asks `plan` which asked products are DISCONTINUED and have a stored alternative
(`product_alternative_service.alternatives_for`, the lookup the stock tool uses). For each
alternative it calls the SAME tool once more with the alternative in place of the asked
products (`alternative_trigger`), so the alternative is answered by that domain's own logic,
permissions and brand guard. `fold` then puts each answer into the reply:

    incoming, dealer   `Alternative: X-NEW: ✅ ETA 20/10/2026` under the old line
    incoming, staff    `*Alternative:* X-NEW` under the old rows, then X-NEW's rows
    product info       `*Alternative:* X-NEW` under the old block, then X-NEW's block
    attachment         `Alternative: X-NEW: <File Name>` under the old file, X-NEW's file sent
    promotion          after the list: `Alternative: X-NEW: <Promotion> (<start> to <end>)`,
                       or `Alternative: X-NEW: No promotion.`

Delivery (orders) is deliberately absent (owner: a delivery history of the old code is what
was delivered), as are the stock tool (it has its own, `inventory_service`), the outstanding
report and counted sets. A product the contact also asked for in its own right is never
offered again as an alternative. An alternative is named only when its own call returned
rows for it (a promotion's "No promotion." aside), so a code the output guard dropped is
never said.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

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


def plan(
    db: Any,
    tool_name: str,
    entities: Any,
    *,
    predicate: Any = None,
    scope_brand_ids: Any = None,
) -> list[tuple[str, Any]]:
    """`[(old_code, alternative Product)]` for the asked discontinued products, or []."""
    kind = TOOL_KINDS.get(tool_name)
    if db is None or kind is None or predicate is not None:
        # A counted set (`predicate`) lists what it counted; an alternative would pad it.
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
    scope = [str(b) for b in jsc.array(scope_brand_ids)]
    if pairs and scope:
        # The contact's brand scope, checked here too and not only through the session's
        # own brand filter: an alternative outside it is never named, even on a session
        # the engine did not stamp (`_brand_scope_ids` reads the contact row then).
        from app.services.contact_brand_scope import out_of_scope_product_ids

        out = out_of_scope_product_ids(db, frozenset(scope), [a.id for _o, a in pairs])
        pairs = [(o, a) for o, a in pairs if str(a.id) not in out]
    return pairs


def alternative_trigger(trigger: dict[str, Any], alternative: Any) -> dict[str, Any]:
    """The same trigger with the asked products swapped for one alternative; every other
    filter (attachment type, date window, tier, customer, contact) is kept, so the second
    call asks the same question about the alternative."""
    kept = [
        e
        for e in jsc.array(trigger.get("entities"))
        if not (isinstance(e, dict) and e.get("entity_type") == "product")
    ]
    swapped = {"entity_type": "product", "uuid": alternative.id, "code": alternative.product_code}
    return {**trigger, "entities": kept + [swapped]}


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
    answers: list[tuple[str, Any, Any]],
    *,
    fmt_value: Callable[[Any], Any],
    render: Callable[[Any, list], str],
    status_only: bool = False,
) -> tuple[list[str], bool]:
    """Put each alternative's answer into `envelope` (mutated) and return `(tail lines,
    found)`. `answers` is `[(old_code, alternative, its envelope or None)]`. The tail is
    printed after the rendered reply (promotion, or an old code with no row of its own to
    hang a line under); `found` says some alternative actually had rows. `fmt_value` is
    `fetch._fmt_value`, so dates read as the reply's own; `render(alt_envelope, items)` is
    the reply's own item renderer, for an alternative's rows with no old row to follow."""
    kind = TOOL_KINDS[tool_name]
    items = envelope.get("items") if isinstance(envelope.get("items"), list) else None
    if items is None:
        items = []
        envelope["items"] = items
    tail: list[str] = []
    found = False
    inserted_after: dict[int, int] = {}

    for old_code, alternative, alt_envelope in answers:
        alt_code = (alternative.product_code or "").strip()
        alt_items = [it for it in jsc.array(jsc.get(alt_envelope, "items")) if isinstance(it, dict)]
        anchors = [
            i for i, it in enumerate(items) if isinstance(it, dict) and _code_of(it) == old_code.strip().upper()
        ]
        anchor_at = anchors[-1] if anchors else None
        anchor = items[anchor_at] if anchor_at is not None else None

        if kind == "product" and status_only:
            # DISCONTINUED-ASK (#1535): the status line names the item's top-level
            # `alternatives`; no second call was made and no block is added.
            if anchor is not None:
                anchor["alternatives"] = jsc.array(anchor.get("alternatives")) + [alt_code]
            continue

        if kind == "promotion":
            if not alt_items:
                tail.append(f"Alternative: {alt_code}: No promotion.")
            for it in alt_items:
                found = True
                name = jsc.nullish_str(_field(it, "Promotion") or jsc.get(it, "title"))
                start = fmt_value(_field(it, "Start Date"))
                end = fmt_value(_field(it, "End Date"))
                line = f"Alternative: {alt_code}: {name} ({start} to {end})"
                if jsc.truthy(jsc.get(jsc.get(it, "flags"), "expired")):
                    line += "\n⚠️  *(EXPIRED)*"
                tail.append(line)
            continue

        own = [it for it in alt_items if _code_of(it) == alt_code.upper()]
        if not own:
            # Nothing came back for it (or the guard dropped it): never named.
            continue
        found = True

        dealer = jsc.truthy(jsc.get(own[0].get("flags"), "dealer_view"))
        if kind == "incoming" and dealer:
            # Dealer: one line, the alternative's own ETA line.
            line = jsc.nullish_str(jsc.get(own[0], "title"))
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
            envelope["attachments"] = jsc.array(envelope.get("attachments")) + list(
                jsc.array(jsc.get(alt_envelope, "attachments"))
            )
            continue

        # Staff incoming and product info: the code under the old row(s), then the
        # alternative's own rows right after them (and after any alternative already
        # placed there, so several read in order). With no old row to follow, the code
        # and the alternative's rows go after the reply.
        if anchor is None:
            rows = render(alt_envelope, own).strip()
            tail.append(f"*Alternative:* {alt_code}" + (f"\n{rows}" if rows else ""))
            continue
        if kind == "product":
            # DISCONTINUED-ASK (#1535) reads the top-level key when the status is asked
            # beside another property.
            anchor["alternatives"] = jsc.array(anchor.get("alternatives")) + [alt_code]
        _flags(anchor).setdefault("alternatives", []).append(alt_code)
        at = anchor_at + 1 + inserted_after.get(anchor_at, 0)
        items[at:at] = own
        inserted_after[anchor_at] = inserted_after.get(anchor_at, 0) + len(own)
    return tail, found


def miss_sentence(tool_name: str, old_codes: list[str]) -> str:
    return f"No {_MISS_LABEL[TOOL_KINDS[tool_name]]} found for {', '.join(old_codes)}."
