"""Generic product find: ONE read (`crm_find`) whose filters live in a registry (CHAT-FIND).

Architecture option D, "reads generic, actions specific"
(`documentation/plans/chatbot/ARCH-scalable-chat-11oct.html`, PR #1566). A new question type
is one `FindFilter` row here plus eval cases: the parser learns it through the live
`{{find_filters}}` prompt variable (`parser_vocabulary`), the reply through `labels` (merged
into `chatbot/label_catalog.LABELS` for ms / zh), and the route / MCP tool / lane never change.

Filter types:

* `product_ref`: the value is the resolved products the message names (the chatbot's resolver
  fills `product_ids`; the model never invents an id). The filter answers one group per named
  product.

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
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from sqlalchemy.orm import Session

from app.services.error_handler import AppException

TYPE_PRODUCT_REF = "product_ref"

#: Rows printed per group; the rest are counted in `more` ("Showing 20 of N.").
ROWS_PER_GROUP = 20
MAX_FILTERS = 8
MAX_PRODUCT_IDS = 10
MAX_FILTERS_CHARS = 4096

#: The lane's line when a product_ref filter arrives with no product named (card rule 6).
ASK_PRODUCT_LINE = "Which product code? For example SRTWC8383-RL-MBL."

#: The labels the MCP presenter prints for every filter, with their translations.
COMMON_LABELS: dict[str, dict[str, str]] = {
    "Code": {"ms": "Kod", "zh": "代码"},
    "Stock": {"ms": "Stok", "zh": "库存"},
    "Status": {"ms": "Status", "zh": "状态"},
    "Discontinued": {"ms": "Dihentikan", "zh": "已停产"},
    "Showing {shown} of {total}.": {"ms": "Menunjukkan {shown} daripada {total}.", "zh": "显示 {total} 个中的 {shown} 个。"},
    ASK_PRODUCT_LINE: {
        "ms": "Kod produk yang mana? Contohnya SRTWC8383-RL-MBL.",
        "zh": "请问是哪个产品代码？例如 SRTWC8383-RL-MBL。",
    },
}


@dataclass(frozen=True)
class Group:
    anchor_id: str
    anchor_code: str
    relation: Optional[str]
    head: str
    rows: list[Any] = field(default_factory=list)  # Product rows, ordered


@dataclass(frozen=True)
class FindFilter:
    key: str
    type: str
    #: One line for the parser policy block: what the key means and when to emit it.
    vocabulary: str
    #: English presenter lines (heads with `{code}`) -> {"ms", "zh"}.
    labels: dict[str, dict[str, str]]
    run: Callable[[Session, list[str]], list[Group]]


# --------------------------------------------------------------------------- fits

FITS_COVERS_HEAD = "Seat cover for {code}:"
FITS_WCS_HEAD = "{code} fits these WCs:"
FITS_NONE_LINE = "No seat cover or WC is on record for {code}."


def _run_fits(db: Session, product_ids: list[str]) -> list[Group]:
    from app.models.product import Product
    from app.services.product_compatibility_service import covers_for, wcs_for

    anchors = {
        str(p.id): p for p in db.query(Product).filter(Product.id.in_(product_ids)).all()
    }
    covers = covers_for(db, list(anchors))
    wcs = wcs_for(db, list(anchors))
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
            groups.append(Group(pid, code, None, FITS_NONE_LINE.format(code=code), []))
    return groups


FILTERS: dict[str, FindFilter] = {
    f.key: f
    for f in (
        FindFilter(
            key="fits",
            type=TYPE_PRODUCT_REF,
            vocabulary=(
                "fits: the products that fit or go with the named product: the seat cover(s) "
                "for a WC (water closet / toilet), or the WC(s) a seat cover fits. The named "
                "product is a product entity as usual."
            ),
            labels={
                FITS_COVERS_HEAD: {"ms": "Penutup tempat duduk untuk {code}:", "zh": "{code} 适配的马桶盖："},
                FITS_WCS_HEAD: {"ms": "{code} sesuai untuk WC ini:", "zh": "{code} 适配以下马桶："},
                FITS_NONE_LINE: {
                    "ms": "Tiada penutup tempat duduk atau WC direkodkan untuk {code}.",
                    "zh": "没有 {code} 的马桶盖或马桶适配记录。",
                },
            },
            run=_run_fits,
        ),
    )
}


# --------------------------------------------------------------------------- vocabulary

FIND_RULE = (
    "FIND (key `find`): when the message asks WHICH products stand in a relation listed below "
    "to a product it names (for example which seat cover fits a WC), emit "
    '"find": {"filters": [{"key": <a key from the list>, "value": null}]}. Never emit it for '
    "facts about the named product itself (stock, price, ETA, photo, spec): those keep their "
    "usual keys. The named product stays an ordinary product entity (hint product) and "
    "domain_hint is master_products. A product entity here needs a product CODE: a type word "
    "on its own (\"my WC\", \"the toilet\", \"seat cover\", \"马桶\") is no entity at all "
    "(\"which seat cover fits my WC?\" -> the filter, entities []); never invent a code. Use "
    "only the keys listed; otherwise emit \"find\": null."
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


def parse_filters(raw: Optional[str]) -> list[dict[str, Any]]:
    """The route's `filters` param: a JSON list of `{key, product_ids?, value?}`."""
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
    out: list[dict[str, Any]] = []
    for item in parsed:
        if not isinstance(item, dict) or not isinstance(item.get("key"), str):
            raise _unprocessable("each filter is an object with a key", "filters_required")
        spec = FILTERS.get(item["key"])
        if spec is None:
            raise _unprocessable(
                f"unknown filter {item['key'][:40]!r}; allowed: {', '.join(FILTERS)}", "unknown_filter"
            )
        if spec.type == TYPE_PRODUCT_REF:
            raw_ids = item.get("product_ids")
            if raw_ids is not None and not isinstance(raw_ids, list):
                raise _unprocessable("product_ids must be a list", "product_ids_required")
            ids = parse_uuid_list([json.dumps(raw_ids)] if raw_ids else None, param_name="product_ids")
            if not ids:
                raise _unprocessable(f"filter {spec.key} needs product_ids", "product_ids_required")
            if len(ids) > MAX_PRODUCT_IDS:
                raise _unprocessable(f"at most {MAX_PRODUCT_IDS} product_ids per filter", "too_many")
            out.append({"key": spec.key, "product_ids": ids})
    return out


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
) -> dict[str, Any]:
    """The route's body for already-validated `filters` (`parse_filters`)."""
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
        groups.extend((spec.key, g) for g in spec.run(db, seen))
    shown_ids: list[str] = []
    for _, g in groups:
        for p in g.rows[:ROWS_PER_GROUP]:
            if str(p.id) not in shown_ids:
                shown_ids.append(str(p.id))
    stock, visibility = _stock_by_product(db, shown_ids, contact_id, space_id)
    if contact_id and visibility is None and shown_ids:
        # The contact resolved to no policy: fail closed, as the stock route does.
        return {"groups": [], "stock_visibility": None}
    body_groups = []
    for key, g in groups:
        body_groups.append(
            {
                "filter": key,
                "anchor": {"product_id": g.anchor_id, "product_code": g.anchor_code},
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
