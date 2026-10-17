"""CHAT-CLOSED-LISTS S4a: card R4, the c06 shape (owner CP0: "Why we cant identify WC ya").

"Any model WC with 300mm available ?": the parser read "WC" as the water closet class,
confidently, and the bot still asked "1. WC (product) 2. WC (attachment type)". The
attachment-type hit is a NAME match: the resolver's attachment-type probe reads the type's
description, and the Certification type's words carry "WCM". The owner's CLASS-HINT-KIND-PICK
rule (6 Oct 2026, "a confident parser hint of category / product_type / product / brand keeps
its kind") already strips name-matched kinds off a locked token; the attachment TYPE was
missing from that set. Synthetic rows only.
"""
from __future__ import annotations

import uuid
from typing import Any

import app.main  # noqa: F401  isort:skip
from app.models.product import Product, ProductAttachment, ProductCategory, UnitOfMeasure
from app.models.resources import Attachment, AttachmentType
from app.services.company_scope import DEFAULT_COMPANY_ID
from tests._pg_fixture import unique_code
from tests.chatbot.conftest import session_factory  # noqa: F401
from tests.chatbot.test_class_hint_kind_pick import LOCK_FIELD, _kinds_under, _route_call


def _seed_wc(session_factory) -> None:
    db = session_factory()
    category = ProductCategory(
        id=str(uuid.uuid4()), category_code=unique_code("WC")[:50], category_name="ZZT Water Closet",
        class_label="Water Closet", search_synonyms=["water closet", "wc"], company_id=DEFAULT_COMPANY_ID,
    )
    uom = UnitOfMeasure(id=str(uuid.uuid4()), uom_code=unique_code("UOM")[:50], uom_name="Each", company_id=DEFAULT_COMPANY_ID)
    db.add_all([category, uom])
    db.flush()
    product = Product(
        id=str(uuid.uuid4()), product_code=unique_code("SRTZQ", alpha=True), product_name="ZZT one piece water closet",
        category_id=category.id, base_uom_id=uom.id, list_price=1, company_id=DEFAULT_COMPANY_ID,
    )
    cert = AttachmentType(
        id=str(uuid.uuid4()), type_name="Certification", code="certification", allowed_extensions="pdf",
        description="certification, cert, certificate, watermark cert, wcm, pps, ikram", is_certificate=True,
    )
    db.add_all([product, cert])
    db.flush()
    att = Attachment(
        id=str(uuid.uuid4()), original_filename="zzt.pdf", stored_filename=f"{uuid.uuid4().hex}.pdf",
        file_path="https://cdn.test/zzt.pdf", attachment_type_id=cert.id, is_deleted=False, company_id=DEFAULT_COMPANY_ID,
    )
    db.add(att)
    db.flush()
    db.add(ProductAttachment(id=str(uuid.uuid4()), product_id=product.id, attachment_id=att.id, company_id=DEFAULT_COMPANY_ID))
    db.commit()


def _wc_body(**extra: Any) -> dict[str, Any]:
    return {
        "query": "Any model WC with 300mm available ?",
        "tokens": ["wc"],
        "match_mode": "and",
        "allowed_entity_types": ["category"],
        "domain": "master_products",
        "fallback_to_all_types": True,
        "exact_match": True,
        **extra,
    }


def test_control_today_the_rerun_gives_wc_the_attachment_type_whose_words_hold_it(session_factory):
    """GREEN before the fix too: the seed reproduces the card, so the next test is red for
    the right reason."""
    _seed_wc(session_factory)
    result = _route_call(session_factory(), _wc_body())
    assert "attachment_type" in _kinds_under(result, "wc"), result


def test_a_locked_class_word_is_never_given_an_attachment_type_by_name(session_factory):
    _seed_wc(session_factory)
    result = _route_call(session_factory(), _wc_body(**{LOCK_FIELD: ["wc"]}))
    assert "attachment_type" not in _kinds_under(result, "wc"), result


def test_a_file_ask_keeps_the_attachment_type_for_its_own_domain(session_factory):
    """Under a file domain the type IS the answer ("wcm cert" asked as a file), so a
    locked token there keeps it, as promotion keeps its own name hits under promotion."""
    _seed_wc(session_factory)
    result = _route_call(
        session_factory(),
        _wc_body(query="wcm cert", tokens=["wcm"], domain="product_attachment", **{LOCK_FIELD: ["wcm"]}),
    )
    assert "attachment_type" in _kinds_under(result, "wcm"), result
