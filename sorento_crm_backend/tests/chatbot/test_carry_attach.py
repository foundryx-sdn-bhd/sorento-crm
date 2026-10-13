"""CARRY-ATTACH (prod, 13 Oct 2026, parser v52): answering the bot's own attachment-type
question keeps the product it asked about.

T1 "Srt8308 drawing" sent the drawing. T2 "Srt8313" asked "Please provide the attachment
type for product Srt8313". T3 "Drawing", the answer to that question, reached the parser as
the carried product (`current_message: false`) plus the document word, `entity_op: reuse`;
the code dropped the product, the resolver saw only "technical drawing", and the reply said
no Technical Specifications matched and offered to escalate.

Same substrate as `test_attach_set_brand_followup.py`: `engine.run_turn` with the real
resolver, only the MCP tool call stubbed (the stub honours `attachment_type_ids` as the real
tool does). The parser verdicts are the prod parser_raw shapes, a fake contact.
"""
from __future__ import annotations

import uuid
from typing import Any

import pytest

from tests.chatbot.test_attach_set_brand_followup import _attach, _Chat
from tests.chatbot.test_engine import stub_access, stub_parser  # noqa: F401 - fixtures used by name
from tests.chatbot.test_lane_require import _seed_category_and_uom, _seed_registry

DRAWING = "Technical Specifications"
PHOTO = "Product Photos"


def _seed_alias(db, value: str, keyword: str) -> None:
    """The dev copy's `attachment_type_alias` row: `keyword` -> `value`."""
    from app.models.lookup import LookupOption, LookupOptionKeyword, LookupSet

    lookup_set = db.query(LookupSet).filter(LookupSet.set_key == "attachment_type_alias").first()
    if lookup_set is None:
        lookup_set = LookupSet(
            id=str(uuid.uuid4()), tenant_id=None, set_key="attachment_type_alias", name="Attachment Type Alias", is_active=True
        )
        db.add(lookup_set)
        db.flush()
    option = LookupOption(id=str(uuid.uuid4()), set_id=lookup_set.id, value=value, label=value, is_active=True)
    db.add(option)
    db.flush()
    db.add(LookupOptionKeyword(id=str(uuid.uuid4()), option_id=option.id, keyword=keyword, locale=None))
    db.flush()


def _product(db, *, code: str, category_id: str, uom_id: str):
    from app.models.product import Product
    from app.services.product_spec_derivation import derive_for_code

    row = Product(
        id=str(uuid.uuid4()),
        product_code=code,
        product_name=f"Basin Mixer {code}",
        description=f"{code} CHROME BASIN MIXER",
        category_id=category_id,
        base_uom_id=uom_id,
        list_price=10,
        is_active=True,
    )
    db.add(row)
    db.flush()
    derive_for_code(db, code)
    return row


@pytest.fixture()
def world(session_factory):
    """SRT8308 and SRT8313, each with a drawing and a photo on file (prod: SRT8313.jpg)."""
    from app.models.resources import AttachmentType

    db = session_factory()
    category_id, uom_id = _seed_category_and_uom(db)
    _seed_registry(db)
    types = {}
    for name in (DRAWING, PHOTO):
        row = db.query(AttachmentType).filter(AttachmentType.type_name == name).first()
        if row is None:
            row = AttachmentType(id=str(uuid.uuid4()), type_name=name, allowed_extensions="pdf,jpg")
            db.add(row)
            db.flush()
        types[name] = row
    _seed_alias(db, DRAWING, "technical drawing")
    _seed_alias(db, PHOTO, "photo")
    products = {code: _product(db, code=code, category_id=category_id, uom_id=uom_id) for code in ("SRT8308", "SRT8313")}
    for code, product in products.items():
        _attach(db, product, types[PHOTO], f"{code}.jpg")
        _attach(db, product, types[DRAWING], f"{code}-td.pdf")
    db.commit()
    return {"db": db, "products": products, "drawing": types[DRAWING], "photo": types[PHOTO]}


@pytest.fixture()
def chat(session_factory, monkeypatch, stub_parser, stub_access, world):
    return _Chat(session_factory, monkeypatch, stub_parser, stub_access, world)


def _entity(raw: str, hint: str, canonical_code: str | None = None, *, typed: bool = True) -> dict[str, Any]:
    return {"raw": raw, "hint": hint, "canonical_code": canonical_code, "current_message": typed, "confident": True}


def _verdict(user_goal: str, entities: list[dict[str, Any]], **overrides: Any) -> dict[str, Any]:
    from tests.chatbot.test_engine import _parser_output

    return _parser_output(
        intent_hint="check_product_attachment",
        domain_hint="product_attachment",
        user_goal=user_goal,
        entities=entities,
        **overrides,
    )


def _t1() -> dict[str, Any]:
    """"Srt8308 drawing" (prod parser_raw)."""
    return _verdict(
        "technical drawing for Srt8308",
        [_entity("Srt8308", "product"), _entity("drawing", "attachment_type", "technical drawing")],
        domain_in_message=True,
    )


def _t2() -> dict[str, Any]:
    """"Srt8313": the product alone, continuing the open ask."""
    return _verdict(
        "trying to get the attachment for Srt8313",
        [_entity("Srt8313", "product")],
        continuation=True,
        domain_in_message=False,
    )


def _t3() -> dict[str, Any]:
    """"Drawing", answering the bot's attachment-type question: the carried product echoed
    (`current_message: false`) beside the document word, `entity_op: reuse`."""
    return _verdict(
        "trying to get the drawing attachment for the previously mentioned product",
        [
            _entity("Srt8313", "product", typed=False),
            _entity("Drawing", "attachment_type", "technical drawing"),
        ],
        continuation=True,
        entity_op="reuse",
        domain_in_message=True,
    )


def test_answering_the_attachment_type_ask_keeps_the_asked_product(chat, world):
    first = chat.say("Srt8308 drawing", _t1())
    calls = chat.attachment_calls()
    assert calls and calls[-1].get("product_ids") == [world["products"]["SRT8308"].id], chat.calls
    assert calls[-1].get("attachment_type_ids") == [world["drawing"].id], calls[-1]
    assert "SRT8308-td.pdf" in first, first

    second = chat.say("Srt8313", _t2())
    assert "attachment type" in second.lower(), second

    third = chat.say("Drawing", _t3())
    last = chat.attachment_calls()[-1]
    # The drawing of the product the bot asked about, not a miss over every product.
    assert last.get("product_ids") == [world["products"]["SRT8313"].id], chat.calls
    assert last.get("attachment_type_ids") == [world["drawing"].id], last
    assert "SRT8313-td.pdf" in third, third
    assert "escalate" not in third.lower(), third
    assert "no technical specifications" not in third.lower(), third

