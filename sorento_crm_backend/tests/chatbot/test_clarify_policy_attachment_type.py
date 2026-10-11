"""CLARIFY-POLICY S3 (AC-13..AC-16): a product attachment ask with NO attachment type answers
from what the product HAS instead of "Please provide the attachment type ... e.g. product image,
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


def test_ac13_one_type_on_record_sends_its_files_and_names_the_type(world):
    world.add_file(PHOTOS, "zzt-front.jpg")
    reply = world.say(f"attachment for {CODE}", _ask())
    assert not _is_the_old_ask(reply), reply
    assert PHOTOS in reply, reply  # the one-line disclose naming the type
    assert "zzt-front.jpg" in reply, reply  # the file itself is sent


def test_ac14_several_types_lists_the_types_the_product_has(world):
    world.add_file(PHOTOS, "zzt-front.jpg")
    world.add_file(CERT, "zzt-cert.pdf")
    reply = world.say(f"attachment for {CODE}", _ask())
    assert f"{CODE} has:" in reply, reply
    assert PHOTOS in reply and CERT in reply, reply
    assert reply.endswith("Reply with the one you need."), reply
    assert not _is_the_old_ask(reply), reply


def test_ac14_never_lists_a_type_the_product_lacks(world):
    world.add_file(PHOTOS, "zzt-front.jpg")
    world.add_file(CERT, "zzt-cert.pdf")
    reply = world.say(f"attachment for {CODE}", _ask())
    assert f"{CODE} has:" in reply, reply
    assert DRAWING not in reply, reply  # seeded as a type, but no file on this product


def test_ac14_a_file_of_the_same_type_on_another_product_does_not_count(world):
    world.add_file(PHOTOS, "zzt-front.jpg")
    other = _other_product(world)
    world.add_file(CERT, "zzt-other-cert.pdf", product_id=other)
    reply = world.say(f"attachment for {CODE}", _ask())
    assert CERT not in reply, reply  # one type on THIS product, so the photos are sent
    assert PHOTOS in reply, reply


def test_ac15_a_product_with_no_files_says_so_and_refers_with_no_type_question(world):
    reply = world.say(f"attachment for {CODE}", _ask())
    assert not _is_the_old_ask(reply), reply
    lowered = reply.lower()
    assert "no files" in lowered and "on record" in lowered, reply
    assert "refer" in lowered or "escalate" in lowered, reply


def test_ac16_types_are_counted_only_over_files_the_contact_may_see(world):
    """ASSUMPTION: this contact is not an office audience, so a file whose access_levels is
    ["office"] only is invisible to it (the existing access level filter on attachments)."""
    world.add_file(PHOTOS, "zzt-front.jpg")
    world.add_file(CERT, "zzt-office-only.pdf")
    from app.models.resources import Attachment

    db = world.sf()
    set_company_scope(db, frozenset({SORENTO}))
    db.query(Attachment).filter(Attachment.original_filename == "zzt-office-only.pdf").update(
        {"access_levels": ["office"]}
    )
    db.commit()
    reply = world.say(f"attachment for {CODE}", _ask())
    assert CERT not in reply and "zzt-office-only.pdf" not in reply, reply
    assert PHOTOS in reply, reply


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
