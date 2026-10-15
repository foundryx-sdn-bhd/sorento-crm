"""The answer register writer (PV2 redesign R1): what the reply PRINTED, as typed entries.

Pure. Reads only keyed, printed fields of the composer envelopes (`turn_runtime.envelope_of`): never an item
title, never text, and a field the reveal filter removed is simply not there to read. Rows of `figures` are
read only when the envelope's `printed_rows` lists them (the structurer's single record of what it rendered);
without that key nothing is recorded from `figures`. Keys (codes, uuids, numbers) are stored for later
pointing and never rendered; no label is a uuid.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Iterable, Sequence

from app.services.chatbot.pv2.state import ConversationState, Option, RegisterEntry

REGISTER_CAP = 20
REGISTER_REPLIES = 2

_UUID_HEX = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
_UUID = re.compile(rf"^{_UUID_HEX}$")
_UUID_TOKEN = re.compile(rf"(?<![0-9A-Za-z-]){_UUID_HEX}(?![0-9A-Za-z-])")
_EMPTY_BRACKETS = re.compile(r"[ \t]*\([ \t]*\)")

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
    from app.services import stock_ask_service

    for item in stock_ask_service.answered_entries(env.get("stock_availability") or []):
        # The stock-ask render's lines: only an answered entry (a branch and the dealer's quantity) was printed.
        code = _clean(item.get("product_code"))
        if code:
            yield ("product", code, code, None)
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


def entries_from_envelopes(
    envelopes: Sequence[dict[str, Any]], turn: int, subject: str | Callable[[dict[str, Any]], str | None] | None = None
) -> list[RegisterEntry]:
    """The reply's register entries: ids R1..Rn depth-first (an entry, then its children). `subject` is the
    printing subject's sid, or a function giving each envelope's own."""
    out: list[RegisterEntry] = []
    for env in envelopes or []:
        if not isinstance(env, dict):
            continue
        base = len(out)
        sid = subject(env) if callable(subject) else subject
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
                                     subject=sid, turn=turn))
    return out


def document_lines(envelopes: Sequence[dict[str, Any]], previous: dict[str, list[str]],
                   register: Sequence[RegisterEntry]) -> dict[str, list[str]]:
    """R5-2: the line product codes of each card the reply printed with `lines` (a sales order card prints none),
    by the card's key, over the previous ones; only documents still in the register are kept."""
    fresh = {
        str(row.get("key") or row.get("label")): [c for c in (_clean(x) for x in row["lines"]) if c]
        for env in envelopes or [] if isinstance(env, dict)
        for row in env.get("rows") or [] if isinstance(row, dict) and isinstance(row.get("lines"), list)
    }
    keys = {e.key for e in register}
    return {k: v for k, v in {**previous, **fresh}.items() if k in keys and v}


def scrub_ids(text: str, labels: dict[str, str] | None = None) -> str:
    """R5-7: a reply's text with every UUID-shaped token replaced by its known label (`labels`, id -> printed
    name), else removed, and the empty brackets and trailing spaces a removal leaves. A product code is never
    UUID-shaped, so it is untouched. The bot's own reply text only; never the user's message."""
    if not text or not _UUID_TOKEN.search(text):
        return text
    known = {k.lower(): v for k, v in (labels or {}).items() if v and not _UUID.match(v.strip())}
    out = _UUID_TOKEN.sub(lambda m: known.get(m.group(0).lower(), ""), text)
    out = _EMPTY_BRACKETS.sub("", out)
    return "\n".join(line.rstrip() for line in out.split("\n"))


def known_labels(state: Any) -> dict[str, str]:
    """R5-7: id -> printed label for what the state holds: the register's entries and the held / saved subjects."""
    out: dict[str, str] = {}
    for e in getattr(state, "register", None) or []:
        out.setdefault(str(e.key), str(e.label))
    for subject in [getattr(state, "held", None), *(getattr(state, "topics", None) or [])]:
        for refs in (getattr(subject, "refs", None) or {}).values():
            for r in refs:
                out.setdefault(str(r.key), str(r.label))
    return out


def entries_from_report_rows(rows: Sequence[Any], turn: int, subject: str | None = None) -> list[RegisterEntry]:
    """The ranked rows a report direct reply printed (`reports.ROW_PAYLOAD` on each option): one entry per row,
    its kind the row's dimension, its key the first key the row stands for."""
    from app.services.chatbot.pv2.reports import ROW_PAYLOAD

    out: list[RegisterEntry] = []
    for row in rows or []:
        picked = (getattr(row, "payload", None) or {}).get(ROW_PAYLOAD)
        if not isinstance(picked, dict) or not picked.get("dimension"):
            continue
        label = _clean(picked.get("label")) or _clean(getattr(row, "label", None))
        if not label:
            continue
        keys = picked.get("keys") or []
        out.append(RegisterEntry(id=f"R{len(out) + 1}", kind=str(picked["dimension"]), key=str(keys[0] if keys else label),
                                 label=label, parent=None, subject=subject, turn=turn))
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


def shown_rows(entries: Sequence[RegisterEntry], turn: int, total: int | None = None) -> list[Option]:
    """R4a: the entries the reply of `turn` printed, as the rows MEMORY numbers and a row_pick indexes: each printed
    label with its kind (a child with its parent's number), never the stored key (a picked row is looked up again
    by its label through the scoped resolver, which re-checks grants and links). `total` is how many rows that
    reply printed before the register's cap."""
    printed = [e for e in entries if e.turn == turn][:REGISTER_CAP]
    number = {e.id: n for n, e in enumerate(printed, start=1)}
    rows: list[Option] = []
    for n, e in enumerate(printed, start=1):
        payload: dict[str, Any] = {"kind": "row", "row_kind": e.kind, "of": total or len(printed)}
        if e.parent in number:
            payload["in"] = number[e.parent]
        rows.append(Option(key=str(n), label=e.label, payload=payload))
    return rows


def shown(state: ConversationState) -> list[Option]:
    """R4a: the rows of the last answer, which MEMORY numbers and a row_pick indexes: `last_rows` (an answer sets
    them to what it printed and clears them when it printed none; a question keeps them), else the register rows
    of the reply that ended this turn (state written without them)."""
    return list(state.last_rows) or shown_rows(state.register, state.turn_no)
