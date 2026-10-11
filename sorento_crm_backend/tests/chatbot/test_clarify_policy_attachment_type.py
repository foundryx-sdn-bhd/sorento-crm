"""CLARIFY-POLICY AC-103 / AC-125 (owner Q3): a product attachment ask with NO attachment type still asks (no guessed type). Regression guards; the old v1 text was:
 "Please provide the attachment type ... e.g. product image,
technical drawing, or certificate." UAC: documentation/plans/chatbot/clarify-policy-acceptance-criteria.md.

Harness: the real engine + real routes + real MCP presenter (`Console`, parser verdict stubbed);
the product, its attachment types and its files are seeded here on the blank schema.
"""
from __future__ import annotations

from typing import Any

import pytest

from app.main import app
from app.models.base import set_company_scope
from app.models.product import Product
from tests.chatbot._turn_helpers import entity, verdict
from tests.chatbot.test_dealer_eta_stock_routing import CODE, Console, _seed
from tests.chatbot.test_engine import stub_access  # noqa: F401 - fixture
from tests.chatbot.test_product_attachment_picker_stamp import _seed_attachment_type, _seed_file_for
from tests.chatbot.test_rearch_s3_attribute_first import SORENTO

PHOTOS = "Product Photos"
CERT = "Certification"
DRAWING = "Technical Drawing"
OLD_EXAMPLE = "e.g. product image, technical drawing, or certificate"


@pytest.fixture
def world(session_factory, monkeypatch, stub_access):
    _seed(session_factory, dealer=False, salesperson=False, shipments=False)
    console = Console(session_factory, monkeypatch, stub_access)
    db = session_factory()
    set_company_scope(db, frozenset({SORENTO}))
    console.product_id = db.query(Product).filter(Product.product_code == CODE).one().id
    console.types = {
        name: _seed_attachment_type(session_factory, name) for name in (PHOTOS, CERT, DRAWING)
    }

    def add_file(type_name: str, filename: str, *, product_id: str | None = None) -> None:
        _seed_file_for(
            session_factory,
            product_id=product_id or console.product_id,
            attachment_type_id=console.types[type_name],
            company_id=SORENTO,
            filename=filename,
        )

    console.add_file = add_file
    try:
        yield console
    finally:
        app.dependency_overrides.clear()


def _ask() -> dict[str, Any]:
    """A product attachment ask naming a product and NO attachment type."""
    return verdict(
        domain_hint="product_attachment",
        intent_hint="check_product_attachment",
        entities=[entity(CODE, hint="product")],
    )


def _is_the_old_ask(reply: str) -> bool:
    return "provide the attachment type" in reply or "Which kind of file" in reply or OLD_EXAMPLE in reply


def test_ac103_no_type_in_the_parse_still_asks_and_never_sends_files(world):
    world.add_file(PHOTOS, "zzt-front.jpg")
    world.add_file(CERT, "zzt-cert.pdf")
    reply = world.say(f"attachment for {CODE}", _ask())
    assert "Which kind of file do you need?" in reply, reply
    assert "zzt-front.jpg" not in reply and "zzt-cert.pdf" not in reply, reply  # no guessed type


def test_ac103_a_parse_naming_a_type_sends_that_type(world):
    world.add_file(PHOTOS, "zzt-front.jpg")
    world.add_file(CERT, "zzt-cert.pdf")
    named = verdict(
        domain_hint="product_attachment",
        intent_hint="check_product_attachment",
        entities=[entity(CODE, hint="product"), entity("photo", hint="attachment_type", canonical_code="photo")],
    )
    reply = world.say(f"photo for {CODE}", named)
    assert "zzt-front.jpg" in reply and "zzt-cert.pdf" not in reply, reply


def test_ac125_replay_of_dev_cand_0258_never_says_provide_the_attachment_type(world):
    """Prod history: the attachment-type ask twice, then "I want the DESCRIPTION SIZE!!!"."""
    replies = [
        world.say("size?", _ask()),
        world.say("photo", _ask()),
        world.say("I want the DESCRIPTION SIZE!!!", _ask()),
    ]
    for reply in replies:
        assert "provide the attachment type" not in reply, reply


def _other_product(world) -> str:
    import uuid

    db = world.sf()
    set_company_scope(db, frozenset({SORENTO}))
    base = db.query(Product).filter(Product.id == world.product_id).one()
    other = Product(
        id=str(uuid.uuid4()),
        product_code="ZZTOTHER1",
        product_name="ZZT Other",
        category_id=base.category_id,
        base_uom_id=base.base_uom_id,
        list_price=1,
        company_id=SORENTO,
    )
    db.add(other)
    db.commit()
    return other.id


def test_ac7_replay_of_dev_cand_0258_never_says_provide_the_attachment_type(world):
    """Prod history: the attachment-type ask twice, then "I want the DESCRIPTION SIZE!!!". The
    product has no files here (cand-0258's product had no dimensions on record either)."""
    replies = [
        world.say("size?", _ask()),
        world.say("photo", _ask()),
        world.say("I want the DESCRIPTION SIZE!!!", _ask()),
    ]
    for reply in replies:
        assert "provide the attachment type" not in reply, reply
    assert "Which kind of file" not in replies[2], replies[2]
