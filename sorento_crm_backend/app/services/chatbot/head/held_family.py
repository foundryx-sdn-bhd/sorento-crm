"""SPEC-OVER-HELD (CHAT-CLOSED-LISTS, CHAT-STATE handoff 17 Oct 2026): a spec said about a
held product narrows that product's family, never the catalogue.

"How about p trap" after "SRTWC286-SH x 10: no stock" is about the SRTWC286 family's P-trap
variant, which exists; the bot answered every P-trap WC. When a gated message names
specification values and no product of its own, and the conversation holds products, the
held products' family (the variant graph: `products.variant_of_id`, root and descendants)
is filtered by the stored spec values, the parser's registry values compared exactly. The
members that carry every named value become this turn's products. No member carries them:
the verdict is left as it was. Data only: no word of the message is read.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

#: How far up and down the variant graph a family is read (codes nest a few levels deep).
MAX_DEPTH = 6

_SPEC_HINTS = frozenset({"specification", "spec"})


def _family_ids(db: "Session", held_ids: list[str]) -> set[str]:
    from app.models.product import Product, chat_searchable_products

    roots: set[str] = set()
    for pid in held_ids:
        current = pid
        for _ in range(MAX_DEPTH):
            row = db.query(Product.variant_of_id).filter(Product.id == current).first()
            if row is None or not row[0]:
                break
            current = str(row[0])
        roots.add(current)
    family = set(roots)
    frontier = set(roots)
    for _ in range(MAX_DEPTH):
        if not frontier:
            break
        children = {
            str(pid)
            for (pid,) in db.query(Product.id).filter(Product.variant_of_id.in_(list(frontier))).all()
        }
        frontier = children - family
        family |= children
    return {
        str(pid)
        for (pid,) in db.query(Product.id).filter(Product.id.in_(list(family)), chat_searchable_products()).all()
    }


def narrow_held_family(
    db: "Session", verdict: dict[str, Any], held_products: list[dict[str, Any]]
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """The verdict with the held family's spec-matching members as its products, and a
    trace note; the verdict unchanged (and no note) when the rule does not apply."""
    from sqlalchemy import func

    from app.models.product import Product
    from app.models.product_spec import ProductSpecifications

    entities = [e for e in verdict.get("entities") or [] if isinstance(e, dict)]
    said = [e for e in entities if e.get("current_message") is not False]
    specs = [
        (str(e.get("spec_key")), e.get("spec_value"))
        for e in said
        if str(e.get("hint") or "").strip().lower() in _SPEC_HINTS
        and e.get("spec_key")
        and isinstance(e.get("spec_value"), str)
        and e.get("spec_value")
    ]
    names_a_product = any(str(e.get("hint") or "").strip().lower() == "product" for e in said)
    held_ids = [str(p["uuid"]) for p in held_products if isinstance(p, dict) and p.get("uuid")]
    if not specs or names_a_product or not held_ids or verdict.get("family") is True:
        return verdict, None
    family = _family_ids(db, held_ids)
    if not family:
        return verdict, None
    query = db.query(Product.id, Product.product_code).join(
        ProductSpecifications, ProductSpecifications.product_id == Product.id
    ).filter(Product.id.in_(list(family)))
    for key, value in specs:
        query = query.filter(func.lower(ProductSpecifications.values[key]["value"].astext) == value.lower())
    matched = sorted(query.all(), key=lambda row: row[1])
    if not matched:
        return verdict, None
    products = [
        {"raw": code, "hint": "product", "canonical_code": code, "current_message": True, "confident": True}
        for _pid, code in matched
    ]
    note = {"held": len(held_ids), "family": len(family), "matched": [code for _pid, code in matched], "specs": specs}
    return {**verdict, "entities": [*products, *entities]}, note
