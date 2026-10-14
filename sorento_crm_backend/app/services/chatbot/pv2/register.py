"""The answer register writer (PV2 redesign R1): what the reply PRINTED, as typed entries.

Pure. Reads only keyed, printed fields of the composer envelopes (`turn_runtime.envelope_of`): never an item
title, never text, and a field the reveal filter removed is simply not there to read. Rows of `figures` are
read only when the envelope's `printed_rows` lists them (the structurer's single record of what it rendered);
without that key nothing is recorded from `figures`. Keys (codes, uuids, numbers) are stored for later
pointing and never rendered; no label is a uuid.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Sequence

from app.services.chatbot.pv2.state import RegisterEntry

REGISTER_CAP = 20
REGISTER_REPLIES = 2

_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

# R3: move to chatbot_entity_kinds. A printed field (its `key`, else its label) -> the entity kind it names.
_FIELD_KIND: dict[str, str] = {
    "order_number": "delivery_order",
    "order number": "delivery_order",
    "po_number": "purchase_order",
    "po number": "purchase_order",
    "product_code": "product",
    "product code": "product",
}
_PRODUCT_FIELDS = ("product_code", "product code")
_PRODUCTS_LIST_LABEL = "products"  # the order card's joined-products field; present only when revealed


def _clean(value: Any) -> str | None:
    text = "" if value is None else str(value).strip()
    return text if text and not _UUID.match(text) else None


def _named(fields: Sequence[Any]) -> dict[str, str]:
    """{name: printed value} for the keyed fields of one figure; the name is the field's `key` or label."""
    out: dict[str, str] = {}
    for f in fields:
        if not isinstance(f, dict):
            continue
        name = str(f.get("key") or f.get("label") or "").strip().lower()
        value = _clean(f.get("value"))
        if name and value is not None:
            out.setdefault(name, value)
    return out


def _figure_entries(figure: Any) -> list[tuple[str, str, str, int | None]]:
    """[(kind, key, label, parent index)] for one printed figure, parent before its children."""
    if not isinstance(figure, dict):
        return []
    fields = figure.get("fields") or []
    named = _named(fields)
    doc_kind = next((_FIELD_KIND[n] for n in named if _FIELD_KIND.get(n) in ("delivery_order", "purchase_order")), None)
    product = next((named[n] for n in _PRODUCT_FIELDS if n in named), None)
    if doc_kind is None:
        return [("product", product, product, None)] if product else []
    number = next(v for n, v in named.items() if _FIELD_KIND.get(n) == doc_kind)
    rows: list[tuple[str, str, str, int | None]] = [(doc_kind, number, number, None)]
    if doc_kind == "purchase_order" and product:
        rows.append(("product", product, product, 0))
    elif doc_kind == "delivery_order" and _PRODUCTS_LIST_LABEL in named:
        for line in figure.get("lines") or []:
            code = _clean(line.get("product_code")) if isinstance(line, dict) else None
            if code:
                rows.append(("product", code, code, 0))
    return rows


def _rows(env: dict[str, Any]) -> Iterable[tuple[str, str, str, int | None]]:
    """The printed rows of one envelope, in printed order."""
    figures = env.get("figures") or []
    for idx in env.get("printed_rows") or []:
        if isinstance(idx, int) and not isinstance(idx, bool) and 0 <= idx < len(figures):
            yield from _figure_entries(figures[idx])
    for row in env.get("rows") or []:
        if isinstance(row, dict) and row.get("kind"):
            label = _clean(row.get("label"))
            if label:
                yield (str(row["kind"]), str(row.get("key") or label), label, None)
    for item in env.get("listed_products") or []:
        if isinstance(item, dict):
            label = _clean(item.get("canonical_code")) or _clean(item.get("raw"))
            if label:
                yield (str(item.get("hint") or "product"), str(item.get("canonical_code") or label), label, None)
    for item in env.get("files") or []:
        code = _clean(item.get("product_code")) if isinstance(item, dict) else None
        if code:
            yield ("product", code, code, None)
    for row in ((env.get("lane_ask") or {}).get("last_result_set") or []):
        # An offer's options (a drill-down) carry no entity_type: they are not printed rows.
        if isinstance(row, dict) and row.get("entity_type"):
            label = _clean(row.get("code")) or _clean(row.get("label"))
            if label:
                yield (str(row["entity_type"]), str(row.get("code") or label), label, None)
    shown = (env.get("sales_ranking_frame") or {}).get("shown") or {}
    kind = shown.get("group_by")
    for row in shown.get("rows") or []:
        label = _clean(row.get("name")) if isinstance(row, dict) else None
        if kind and label:
            keys = row.get("keys") or []
            yield (str(kind), str(keys[0] if keys else label), label, None)


def entries_from_envelopes(envelopes: Sequence[dict[str, Any]], turn: int) -> list[RegisterEntry]:
    """The reply's register entries: ids R1..Rn depth-first (an entry, then its children)."""
    out: list[RegisterEntry] = []
    for env in envelopes or []:
        if not isinstance(env, dict):
            continue
        base = len(out)
        rows = list(_rows(env))
        ids: dict[int, str] = {}
        for i, (kind, key, label, parent) in enumerate(rows):
            entry_id = f"R{base + i + 1}"
            ids[i] = entry_id
            # `parent` is an index into this figure's own rows (always 0 = its head); resolve it to the
            # head entry that precedes the child in `rows`.
            parent_id = None
            if parent is not None:
                head = i - 1
                while head >= 0 and rows[head][3] is not None:
                    head -= 1
                parent_id = ids.get(head)
            out.append(RegisterEntry(id=entry_id, kind=kind, key=key, label=label, parent=parent_id,
                                     subject="S1", turn=turn))
    return out


def roll(previous: Sequence[RegisterEntry], fresh: Sequence[RegisterEntry]) -> list[RegisterEntry]:
    """The saved register after a reply: this reply's entries first, then the last earlier reply's;
    at most the last two replies and 20 entries. A reply that printed nothing keeps the previous entries."""
    if not fresh:
        return list(previous)
    combined = [*fresh, *previous]
    turns: list[int] = []
    for e in combined:
        if e.turn not in turns:
            turns.append(e.turn)
    keep = set(turns[:REGISTER_REPLIES])
    kept = [e for e in combined if e.turn in keep][:REGISTER_CAP]
    # Ids stay unique across the two replies: renumber R1..Rn in order and re-point each parent.
    remap = {(e.turn, e.id): f"R{n}" for n, e in enumerate(kept, start=1)}
    return [
        e.model_copy(update={
            "id": remap[(e.turn, e.id)],
            "parent": remap.get((e.turn, e.parent)) if e.parent else None,
        })
        for e in kept
    ]
