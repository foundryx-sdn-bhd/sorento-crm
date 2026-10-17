"""SCHEME-INDEX (CHAT-CLOSED-LISTS, crew T2, 17 Oct 2026): a listed certificate scheme is an
index into certificates, never a filter that empties a product's answer (owner c04 / c19).

"span certificate" was read as the listed scheme SPAN alone. The leg became `{"certificate":
{"scheme": "SPAN"}}`, the product held no SPAN certificate, and nothing was sent. Under the
gate, a scheme pick about products (named in the message, else held by the conversation) is
checked against the explicit link: product, attachment, certificate revision, certificate
scheme. Holding one, the scheme stays and narrows the files, with the certificate kind beside
it and the held products carried into the fetch. Holding none, the pick becomes the
certificate kind, so every certificate file of the products is sent. With no product at all
("which item has PPS cert") the verdict is left as it was. Data only: no word is read.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _asked_product_ids(db: "Session", said: list[dict[str, Any]], held: list[dict[str, Any]]) -> tuple[list[str], bool]:
    """The products the ask is about and whether the message named them itself."""
    from app.models.product import Product, chat_searchable_products

    codes = [
        str(e.get("canonical_code")).strip()
        for e in said
        if str(e.get("hint") or "").strip().lower() == "product" and str(e.get("canonical_code") or "").strip()
    ]
    if codes:
        rows = db.query(Product.id).filter(Product.product_code.in_(codes), chat_searchable_products()).all()
        return [str(pid) for (pid,) in rows], True
    return [str(p["uuid"]) for p in held if isinstance(p, dict) and p.get("uuid")], False


def _held_certificates(db: "Session", product_ids: list[str], scheme: str) -> list[str]:
    """The certificates of the scheme filed against the products' own attachments."""
    from sqlalchemy import func

    from app.models.certificate import Certificate, CertificateRevision
    from app.models.product import ProductAttachment

    rows = (
        db.query(Certificate.id)
        .join(CertificateRevision, CertificateRevision.certificate_id == Certificate.id)
        .join(ProductAttachment, ProductAttachment.attachment_id == CertificateRevision.attachment_id)
        .filter(ProductAttachment.product_id.in_(product_ids), func.lower(Certificate.scheme) == scheme.casefold())
        .distinct()
        .all()
    )
    return sorted(str(cid) for (cid,) in rows)


def _kind_item(db: "Session", scheme: str, kinds: dict[str, str]) -> str | None:
    """The certificate kind the scheme's own certificates are filed under, else the only
    (or first) kind listed."""
    from sqlalchemy import func

    from app.models.certificate import Certificate
    from app.models.resources import AttachmentType

    for code, name in (
        db.query(AttachmentType.code, AttachmentType.type_name)
        .join(Certificate, Certificate.attachment_type_id == AttachmentType.id)
        .filter(func.lower(Certificate.scheme) == scheme.casefold())
        .order_by(AttachmentType.type_name)
        .all()
    ):
        for key in (code, name):
            item = kinds.get(str(key or "").strip().casefold())
            if item:
                return item
    listed = sorted(set(kinds.values()))
    return listed[0] if listed else None


def index_scheme(
    db: "Session", verdict: dict[str, Any], held_products: list[dict[str, Any]]
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """The verdict with its scheme pick read as an index, and a trace note; the verdict
    unchanged (and no note) when the rule does not apply."""
    from app.services.chatbot_prompt_vars import closed_list_items

    entities = [e for e in verdict.get("entities") or [] if isinstance(e, dict)]
    said = [e for e in entities if e.get("current_message") is not False]
    schemes = [e for e in said if e.get("listed") == "cert_scheme" and str(e.get("canonical_code") or "").strip()]
    if not schemes:
        return verdict, None
    product_ids, named = _asked_product_ids(db, said, held_products)
    if not product_ids:
        return verdict, None
    scheme = str(schemes[0]["canonical_code"]).strip()
    kind = _kind_item(db, scheme, closed_list_items(db).get("cert_kind", {}))
    if kind is None:
        return verdict, None
    has_kind = any(e.get("listed") == "cert_kind" for e in said)
    kind_entity = {**schemes[0], "canonical_code": kind, "listed_item": kind, "listed": "cert_kind"}
    # Added beside a kept scheme, the kind is its own entity: reconciliation keys on `raw`,
    # so it must not share the scheme's word (or it is retyped with it).
    added_kind = {**kind_entity, "raw": kind}
    held = _held_certificates(db, product_ids, scheme)
    if held:
        # The certificates the scheme names for these products, read here through the link,
        # are the scheme's resolution (`resolve_gate`): its word is never a record lookup.
        # Typed `certificate`, the kind its rows are; the certificate kind beside it stays
        # the file leg.
        entities = [{**e, "hint": "certificate", "certificate_uuids": held} if e is schemes[0] else e for e in entities]
        carried = [] if named else [
            {"raw": p.get("canonical_code") or p.get("raw"), "hint": "product", "canonical_code": p.get("canonical_code"),
             "current_message": True, "confident": True}
            for p in held_products
            if isinstance(p, dict) and p.get("uuid") and p.get("canonical_code")
        ]
        out = [*carried, *entities, *([] if has_kind else [added_kind])]
        note = {"scheme": scheme, "held_by_products": True, "carried": len(carried)}
    else:
        out = [kind_entity if e is schemes[0] else e for e in entities if e not in schemes[1:]]
        if has_kind:
            out = [e for e in out if e is not kind_entity]
        note = {"scheme": scheme, "held_by_products": False, "as": kind}
    return {**verdict, "entities": out}, note
