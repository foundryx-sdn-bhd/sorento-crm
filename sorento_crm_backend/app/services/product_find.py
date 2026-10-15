"""Generic product find: ONE read (`crm_find`) whose filters live in a registry (CHAT-FIND).

Architecture option D, "reads generic, actions specific"
(`documentation/plans/chatbot/ARCH-scalable-chat-11oct.html`, PR #1566). A new question type
is one `FindFilter` row here plus eval cases: the parser learns it through the live
`{{find_filters}}` prompt variable (`parser_vocabulary`), the reply through `labels` (merged
into `chatbot/label_catalog.LABELS` for ms / zh), and the route / MCP tool / lane never change.

What a filter takes (declared on its row, checked by the route, never by the lane):

* products (`products=True`, type `product_ref`): the resolved products the message names (the
  chatbot's resolver fills `product_ids`; the model never invents an id);
* a value (`value=ValueSpec(...)`): `code` (a code-shaped string), `number` (finite, with a unit
  from the row's `units`) or `enum` (one of the row's `values`, a tuple or read from the
  database); the parser emits it as `value` + `unit`.

A row may take both. Its runner gets one `FilterArg(product_ids, value, unit)` and answers groups
(one per named product, or one group with no anchor), so a spec or promotion row (#1582) moves
in with no branch in the route, the lane or the MCP tool.

First filter, `fits` (owner request 13 Oct; card `CARD-chat-generic-find-12oct.md`): the WC
<-> seat cover links of PR #1590 (`product_compatibility`). The direction comes from the data
(card Q1 a): a product on the WC end of a link lists its seat covers, one on the seat cover
end the WCs it fits. Only links on record are answered; nothing is inferred (owner ruling 12
Oct). Company and brand scope apply through the ORM listener, as on every product read.

Stock (card Q2 a) is read through `StockService.list_stock` with the asking contact, so the
contact's stock-visibility policy decides the shape: a number for detailed / compact, the
availability verdict for 1 unit (never a number of ours) for a dealer.

Plan: `documentation/plans/chatbot/PLAN-chat-generic-find-12oct.md`.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any, Callable, Collection, Optional, Union

from sqlalchemy.orm import Session

from app.services.error_handler import AppException

TYPE_PRODUCT_REF = "product_ref"
TYPE_VALUE = "value"
#: The value kinds a row may declare.
VALUE_KINDS = ("code", "number", "enum")
MAX_CODE_CHARS = 64

#: Rows printed per group; the rest are counted in `more` ("Showing 20 of N.").
ROWS_PER_GROUP = 20
MAX_FILTERS = 8
MAX_PRODUCT_IDS = 10
MAX_FILTERS_CHARS = 4096

#: The lane's line when a product_ref filter arrives with no product named (card rule 6).
ASK_PRODUCT_LINE = "Which product code? For example SRTWC8383-RL-MBL."
#: The lane's line when the route answered no group: no named product is visible to this
#: contact, or the contact resolved to nobody (fail closed). Asking again changes neither.
NOTHING_LINE = "I have nothing on record for that product."

#: The labels the MCP presenter prints for every filter, with their translations.
COMMON_LABELS: dict[str, dict[str, str]] = {
    "Code": {"ms": "Kod", "zh": "代码"},
    "Stock": {"ms": "Stok", "zh": "库存"},
    "Showing {shown} of {total}.": {"ms": "Menunjukkan {shown} daripada {total}.", "zh": "显示 {total} 个中的 {shown} 个。"},
    ASK_PRODUCT_LINE: {
        "ms": "Kod produk yang mana? Contohnya SRTWC8383-RL-MBL.",
        "zh": "请问是哪个产品代码？例如 SRTWC8383-RL-MBL。",
    },
    NOTHING_LINE: {
        "ms": "Saya tiada rekod untuk produk itu.",
        "zh": "我没有该产品的记录。",
    },
}


@dataclass(frozen=True)
class Group:
    #: The named product the group answers for; None for a group no product anchors.
    anchor_id: Optional[str]
    anchor_code: Optional[str]
    relation: Optional[str]
    head: str
    rows: list[Any] = field(default_factory=list)  # Product rows, ordered


@dataclass(frozen=True)
class ValueSpec:
    kind: str  # one of VALUE_KINDS
    #: `number`: the units accepted, the first being the one an unsaid unit means.
    units: tuple[str, ...] = ()
    #: `enum`: the accepted values, or a reader of them from the database.
    values: Union[tuple[str, ...], Callable[[Any], Collection[str]]] = ()


@dataclass(frozen=True)
class FilterArg:
    product_ids: list[str]
    value: Any = None
    unit: Optional[str] = None


@dataclass(frozen=True)
class FindFilter:
    key: str
    #: One line for the parser policy block: what the key means and when to emit it.
    vocabulary: str
    #: English presenter lines (heads with `{code}`) -> {"ms", "zh"}.
    labels: dict[str, dict[str, str]]
    run: Callable[[Session, FilterArg], list[Group]]
    #: Takes the products the message names.
    products: bool = False
    #: Takes a typed value; None = no value.
    value: Optional[ValueSpec] = None
    #: What the lane asks when the message gives the filter nothing to run on.
    ask: str = ASK_PRODUCT_LINE

    @property
    def type(self) -> str:
        return TYPE_PRODUCT_REF if self.products else TYPE_VALUE


# --------------------------------------------------------------------------- fits

FITS_COVERS_HEAD = "Seat cover for {code}:"
FITS_WCS_HEAD = "{code} fits these WCs:"
FITS_NONE_LINE = "No seat cover is on record for {code}."
#: The none line for a product that is itself a seat cover (crew audit, round 3).
FITS_NO_WC_LINE = "No WC is on record for {code}."
#: The derived spec value that says a product is a seat cover (`product_spec_registry`).
SEAT_COVER_PRODUCT_TYPE = "toilet_seat"


def _seat_covers(db: Session, products: list[Any]) -> set[str]:
    """The ids among `products` the data calls a seat cover: an `-SC` code (owner ruling 12
    Oct, `product_compatibility_service.is_spare_seat_cover`) or a derived `product_type` of
    toilet_seat. Read from the data, never from the customer's words."""
    from app.models.product_spec import ProductSpecifications
    from app.services.product_compatibility_service import is_spare_seat_cover

    out = {str(p.id) for p in products if is_spare_seat_cover(p.product_code)}
    rest = [str(p.id) for p in products if str(p.id) not in out]
    if rest:
        rows = db.query(ProductSpecifications.product_id, ProductSpecifications.values).filter(
            ProductSpecifications.product_id.in_(rest)
        )
        for pid, values in rows:
            product_type = (values or {}).get("product_type") if isinstance(values, dict) else None
            if isinstance(product_type, dict) and product_type.get("value") == SEAT_COVER_PRODUCT_TYPE:
                out.add(str(pid))
    return out


def _run_fits(db: Session, arg: FilterArg) -> list[Group]:
    from app.models.product import Product
    from app.services.product_compatibility_service import covers_for, wcs_for

    product_ids = arg.product_ids

    anchors = {
        str(p.id): p for p in db.query(Product).filter(Product.id.in_(product_ids)).all()
    }
    covers = covers_for(db, list(anchors))
    wcs = wcs_for(db, list(anchors))
    unlinked = [p for pid, p in anchors.items() if not covers.get(pid) and not wcs.get(pid)]
    seat_covers = _seat_covers(db, unlinked) if unlinked else set()
    groups: list[Group] = []
    for pid in product_ids:
        anchor = anchors.get(pid)
        if anchor is None:
            continue  # not visible to this caller (company / brand scope): never named
        code = anchor.product_code or ""
        if covers.get(pid):
            groups.append(Group(pid, code, "covers", FITS_COVERS_HEAD.format(code=code), covers[pid]))
        elif wcs.get(pid):
            groups.append(Group(pid, code, "wcs", FITS_WCS_HEAD.format(code=code), wcs[pid]))
        else:
            none_line = FITS_NO_WC_LINE if pid in seat_covers else FITS_NONE_LINE
            groups.append(Group(pid, code, None, none_line.format(code=code), []))
    return groups


FILTERS: dict[str, FindFilter] = {
    f.key: f
    for f in (
        FindFilter(
            key="fits",
            products=True,
            ask=ASK_PRODUCT_LINE,
            vocabulary=(
                "fits: the products that fit or go with the named product: the seat cover(s) "
                "for a WC (water closet / toilet), or the WC(s) a seat cover fits. The other "
                "side named beside a code, in any language or order, is this filter (\"seat "
                "cover SRTWC8122\", \"SRTWC8383-RL-MBL 马桶盖\", \"WC for SRTSCBD330-UF\", "
                "\"stock seat cover for SRTWC8122\"): emit fits and keep that word out of the "
                "entities. The other side is only a seat cover or a WC word, never a file word "
                "(photo, drawing, files), a fact (stock, price) or the code itself: \"send me "
                "SRTWC8383-RL-MBL drawing\" names no other side, so it is no find. Its value is the "
                "other-side word copied character for character from THIS message's text (\"seat "
                "cover\", \"马桶盖\", \"WC\"), null when THIS message's text has none, whatever "
                "earlier turns said; its file_word is the word of THIS message asking for a file, as "
                "typed (\"photo\", \"drawing\", \"files\", \"cert\"), null when it asks for none. "
                "The other-side word never IMPLIES an attachment type (no photo, drawing, "
                "installation guide, certificate or spec file): with no file word said, emit "
                "no attachment_type (\"seat cover for SRTWC8383-RL-MBL\" -> fits, entities "
                "[SRTWC8383-RL-MBL] only). It stays this filter when the message ALSO asks something about that "
                "other side (stock, ETA, photo, price): \"SRTWC8122 马桶盖 有货吗\" is fits with "
                "domain inventory, \"SRTWC8088 seat cover photo\" is fits with domain "
                "product_attachment. A fact of the named code itself, with no other side named in "
                "THIS message (\"SRTWC8383-RL-MBL stock\", \"SRTWC8383-RL-MBL technical "
                "drawing\"), is no find, even right after a fit ask; only a bare code with no ask "
                "word of its own carries the fit ask on."
            ),
            labels={
                FITS_COVERS_HEAD: {"ms": "Penutup tempat duduk untuk {code}:", "zh": "{code} 适配的马桶盖："},
                FITS_WCS_HEAD: {"ms": "{code} sesuai untuk WC ini:", "zh": "{code} 适配以下马桶："},
                FITS_NONE_LINE: {
                    "ms": "Tiada penutup tempat duduk direkodkan untuk {code}.",
                    "zh": "没有 {code} 的马桶盖记录。",
                },
                FITS_NO_WC_LINE: {
                    "ms": "Tiada WC direkodkan untuk {code}.",
                    "zh": "没有 {code} 适配的马桶记录。",
                },
            },
            run=_run_fits,
        ),
    )
}


# --------------------------------------------------------------------------- vocabulary

FIND_RULE = (
    "FIND (key `find`): when the message is about the products that stand in a relation listed "
    "below to a product CODE it names (for example the seat cover of a WC), emit "
    '"find": {"filters": [{"key": <a key from the list>, "value": <the value its line asks for, '
    'else null>, "unit": <the unit of a number value, else null>, "file_word": <the file word '
    'its line asks for, else null>}]}. The relation only says WHICH '
    "products the message is about: the named code stays an ordinary product entity (hint "
    "product), and every other key, domain_hint and intent_hint included, is exactly what it "
    "would be if the message had named the related product's own code instead: \"stock seat "
    "cover for SRTWC8122\" is a stock ask, \"eta seat cover for SRTWC8122\" an incoming ask, "
    "\"SRTWC8122 seat cover photo\" a file ask. With nothing else asked (\"seat cover for "
    "SRTWC8122\", \"seat cover SRTWC8122\", \"SRTWC8383-RL-MBL 马桶盖\") the message is a bare product code: BARE ENTITY "
    "CONTINUATION decides domain_hint and intent_hint (the previous turn's, else null); the "
    "relation itself never sets master_products, and never product_attachment: a file domain "
    "needs a file word in THIS message, or BARE ENTITY CONTINUATION of a file answer. "
    "Across turns `find` follows BARE ENTITY CONTINUATION, read from the conversation as usual, "
    "and is part of \"the same question\" it repeats: when the previous user message was a fit "
    "ask (it named a seat cover / WC beside a code), a reply that is only another code repeats "
    "that fit ask, find included: "
    "a bare code right after a fit ask is the same fit ask for the new code (\"SRTWC8122 seat "
    "cover\" answered, then \"SRTWC8383-RL-MBL\" alone -> fits with value null, domain as "
    "that previous turn); a "
    "message pointing at what the last answer listed (\"only the 333A one\", \"the second "
    "one\", \"its ETA\") is about those listed products, so name the listed code and emit "
    "\"find\": null, never the code the earlier fit ask named; a message with its own ask "
    "about a code and no other side named (\"SRTWC8383-RL-MBL technical drawing\") is about "
    "that code itself: \"find\": null, whatever earlier turns asked. "
    "A product entity here needs a product CODE: "
    "the type word on its own (\"seat cover\", "
    "\"my WC\", \"马桶盖\") is no entity at all, never a category, attachment_type or "
    "specification (\"which seat cover fits my WC?\" -> the filter, entities []); never invent a "
    "code. A type word asked about itself in a message that names NO product code (\"seat cover "
    "got promotion?\") is no find. Use only the keys listed; otherwise emit \"find\": null."
)


def parser_vocabulary(db: Any = None) -> str:
    """The `{{find_filters}}` prompt variable: the rule, then one line per registered filter."""
    lines = [FIND_RULE, "Find filters:"]
    lines += [f"- {f.vocabulary}" for f in FILTERS.values()]
    return "\n".join(lines)


def catalog_labels() -> dict[str, dict[str, str]]:
    """Every presenter line of every filter, for `label_catalog.LABELS`."""
    out = dict(COMMON_LABELS)
    for f in FILTERS.values():
        out.update(f.labels)
    return out


# --------------------------------------------------------------------------- validation


def _unprocessable(message: str, code: str) -> AppException:
    return AppException(422, message, code=code)


def _value(spec: ValueSpec, key: str, value: Any, unit: Any, db: Any) -> tuple[Any, Optional[str]]:
    """`value` (and `unit`) as `spec` declares them, or a 422."""
    if value is None or (isinstance(value, str) and not value.strip()):
        raise _unprocessable(f"filter {key} needs a value", "value_required")
    if spec.kind == "code":
        if not isinstance(value, str) or len(value.strip()) > MAX_CODE_CHARS:
            raise _unprocessable(f"filter {key} takes a code of at most {MAX_CODE_CHARS} characters", "bad_value")
        return value.strip(), None
    if spec.kind == "number":
        try:
            finite = not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)
        except OverflowError:  # an int too large for a float (security review round 3, N1)
            finite = False
        if not finite:
            raise _unprocessable(f"filter {key} takes a number", "bad_value")
        if unit is None:
            unit = spec.units[0] if spec.units else None
        if spec.units and unit not in spec.units:
            raise _unprocessable(f"filter {key} takes a unit of {', '.join(spec.units)}", "bad_value")
        return value, unit
    if spec.kind == "enum":
        allowed = spec.values(db) if callable(spec.values) else spec.values
        if not isinstance(value, str) or value not in allowed:
            raise _unprocessable(f"filter {key} takes one of its listed values", "bad_value")
        return value, None
    raise _unprocessable(f"filter {key} has no reader", "unknown_filter")  # pragma: no cover


def parse_filters(raw: Optional[str], db: Any = None) -> list[dict[str, Any]]:
    """The route's `filters` param: a JSON list of `{key, product_ids?, value?, unit?}`, each
    checked against its registry row. `db` reads an `enum` row's values when they live there."""
    from app.services.uuid_list_param import parse_uuid_list

    if raw is None or not str(raw).strip():
        raise _unprocessable("filters is required", "filters_required")
    if len(raw) > MAX_FILTERS_CHARS:
        raise _unprocessable(f"filters is longer than {MAX_FILTERS_CHARS} characters", "too_many")
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        raise _unprocessable("filters must be a JSON list", "filters_required")
    if not isinstance(parsed, list) or not parsed:
        raise _unprocessable("filters must be a non-empty JSON list", "filters_required")
    if len(parsed) > MAX_FILTERS:
        raise _unprocessable(f"at most {MAX_FILTERS} filters", "too_many")
    # One entry per key: a repeated key's products join the first entry's (one read per key);
    # a repeated value must be the same value.
    by_key: dict[str, dict[str, Any]] = {}
    for item in parsed:
        if not isinstance(item, dict) or not isinstance(item.get("key"), str):
            raise _unprocessable("each filter is an object with a key", "filters_required")
        spec = FILTERS.get(item["key"])
        if spec is None:
            raise _unprocessable(
                f"unknown filter {item['key'][:40]!r}; allowed: {', '.join(FILTERS)}", "unknown_filter"
            )
        entry = by_key.setdefault(spec.key, {"key": spec.key})
        if spec.products:
            raw_ids = item.get("product_ids")
            if raw_ids is not None and not isinstance(raw_ids, list):
                raise _unprocessable("product_ids must be a list", "product_ids_required")
            ids = parse_uuid_list([json.dumps(raw_ids)] if raw_ids else None, param_name="product_ids")
            if not ids:
                raise _unprocessable(f"filter {spec.key} needs product_ids", "product_ids_required")
            entry.setdefault("product_ids", [])
            entry["product_ids"] += [i for i in ids if i not in entry["product_ids"]]
            if len(entry["product_ids"]) > MAX_PRODUCT_IDS:
                raise _unprocessable(f"at most {MAX_PRODUCT_IDS} product_ids per filter", "too_many")
        if spec.value is not None:
            value, unit = _value(spec.value, spec.key, item.get("value"), item.get("unit"), db)
            if "value" in entry and (entry["value"], entry["unit"]) != (value, unit):
                raise _unprocessable(f"filter {spec.key} given two values", "bad_value")
            entry["value"], entry["unit"] = value, unit
    return list(by_key.values())


# --------------------------------------------------------------------------- stock


def _stock_by_product(
    db: Session, product_ids: list[str], contact_id: Optional[str], space_id: Optional[str]
) -> tuple[dict[str, dict], Optional[dict]]:
    """`{product_id: stock fact}` and the visibility block, from ONE stock read."""
    from app.services.inventory_service import StockService

    if not product_ids:
        return {}, None
    result = StockService(db).list_stock(
        page=1,
        limit=5000,
        product_ids=product_ids,
        contact_id=contact_id,
        space_id=space_id,
        # Only read under an `availability` policy: the dealer's verdict for one unit.
        requested_qty=1 if contact_id else None,
    )
    result = result if isinstance(result, dict) else {}
    visibility = result.get("stock_visibility") if isinstance(result.get("stock_visibility"), dict) else None
    # No contact (staff, n8n): no policy applies, every row is read, as detailed.
    mode = (visibility or {}).get("mode") or "detailed"
    out: dict[str, dict] = {}
    if mode == "availability":
        by_code: dict[str, dict] = {}
        for entry in result.get("stock_availability") or []:
            if not isinstance(entry, dict):
                continue
            verdict = {k: entry.get(k) for k in ("branch", "eta", "available_qty")}
            if entry.get("product_id"):
                out[str(entry["product_id"])] = {"mode": mode, "on_hand": None, "verdict": verdict}
            if entry.get("product_code"):
                by_code[str(entry["product_code"]).strip().lower()] = verdict
        # One availability entry answers per CODE (a code in two companies is one entry,
        # keyed on its first id), so a row whose own id is not the key reads it by code.
        if by_code:
            from app.models.product import Product

            for pid, code in db.query(Product.id, Product.product_code).filter(Product.id.in_(product_ids)):
                verdict = by_code.get((code or "").strip().lower())
                if str(pid) not in out and verdict is not None:
                    out[str(pid)] = {"mode": mode, "on_hand": None, "verdict": verdict}
        return out, visibility
    if mode == "compact":
        for entry in result.get("stock_summary") or []:
            if isinstance(entry, dict) and entry.get("product_id"):
                out[str(entry["product_id"])] = {
                    "mode": mode, "on_hand": int(entry.get("total_on_hand") or 0), "verdict": None,
                }
        return out, visibility
    totals: dict[str, int] = {}
    for row in result.get("data") or []:
        pid = row.get("product_id") if isinstance(row, dict) else getattr(row, "product_id", None)
        qty = row.get("quantity_on_hand") if isinstance(row, dict) else getattr(row, "quantity_on_hand", None)
        if pid:
            totals[str(pid)] = totals.get(str(pid), 0) + int(qty or 0)
    for pid in product_ids:
        out[pid] = {"mode": mode, "on_hand": totals.get(pid, 0), "verdict": None}
    return out, visibility


# --------------------------------------------------------------------------- run


def run_find(
    db: Session,
    filters: list[dict[str, Any]],
    *,
    contact_id: Optional[str] = None,
    space_id: Optional[str] = None,
    with_stock: bool = True,
) -> dict[str, Any]:
    """The route's body for already-validated `filters` (`parse_filters`). `with_stock` False
    (the inventory module is off for this caller) leaves every row's stock as mode `none`."""
    # Stripped as the company scope resolver strips them (`_resolve_api_key_scope`), so the
    # two never disagree about whether a contact was named.
    contact_id = (contact_id or "").strip() or None
    space_id = (space_id or "").strip() or None
    if contact_id and not space_id:
        # Company scope needs both or stays off, the stock policy reads the contact alone:
        # they agree or nobody is answered (the stock route's rule).
        return {"groups": [], "stock_visibility": None}
    groups: list[tuple[str, Group]] = []
    for item in filters:
        spec = FILTERS.get(item.get("key")) if isinstance(item, dict) else None
        if spec is None:
            # `parse_filters` refuses these at the route; a direct caller is refused the same.
            raise _unprocessable(f"unknown filter; allowed: {', '.join(FILTERS)}", "unknown_filter")
        seen: list[str] = []
        for pid in item.get("product_ids") or []:
            if pid not in seen:
                seen.append(pid)
        arg = FilterArg(seen, item.get("value"), item.get("unit"))
        groups.extend((spec.key, g) for g in spec.run(db, arg))
    shown_ids: list[str] = []
    for _, g in groups:
        for p in g.rows[:ROWS_PER_GROUP]:
            if str(p.id) not in shown_ids:
                shown_ids.append(str(p.id))
    stock, visibility = (
        _stock_by_product(db, shown_ids, contact_id, space_id) if with_stock else ({}, None)
    )
    if with_stock and contact_id and visibility is None and shown_ids:
        # The contact resolved to no policy: fail closed, as the stock route does.
        return {"groups": [], "stock_visibility": None}
    body_groups = []
    for key, g in groups:
        body_groups.append(
            {
                "filter": key,
                "anchor": (
                    {"product_id": g.anchor_id, "product_code": g.anchor_code}
                    if g.anchor_id is not None
                    else None
                ),
                "relation": g.relation,
                "head": g.head,
                "rows": [
                    {
                        "product_id": str(p.id),
                        "product_code": p.product_code,
                        "product_name": p.product_name,
                        "is_discontinued": bool(p.is_discontinued),
                        "stock": stock.get(str(p.id)) or {"mode": "none", "on_hand": None, "verdict": None},
                    }
                    for p in g.rows[:ROWS_PER_GROUP]
                ],
                "more": max(0, len(g.rows) - ROWS_PER_GROUP),
            }
        )
    return {
        "groups": body_groups,
        "stock_visibility": {"mode": visibility.get("mode")} if visibility else None,
    }
