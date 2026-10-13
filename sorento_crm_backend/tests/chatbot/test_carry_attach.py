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

    chat.say("Srt8313", _t2())

    third = chat.say("Drawing", _t3())
    last = chat.attachment_calls()[-1]
    # The drawing of the product the bot asked about, not a miss over every product.
    assert last.get("product_ids") == [world["products"]["SRT8313"].id], chat.calls
    assert last.get("attachment_type_ids") == [world["drawing"].id], last
    assert "SRT8313-td.pdf" in third, third
    assert "escalate" not in third.lower(), third
    assert "no technical specifications" not in third.lower(), third


# --------------------------------------------------------------------------- #
# T2 (owner ruling, 13 Oct 2026: "the parser should be able to understand")    #
# --------------------------------------------------------------------------- #


def _assert_sent_drawing_of(chat, world, reply: str, code: str) -> None:
    last = chat.attachment_calls()[-1]
    assert last.get("product_ids") == [world["products"][code].id], chat.calls
    assert last.get("attachment_type_ids") == [world["drawing"].id], last
    # The same format as T1: the files, no ask, no extra wording.
    assert reply.startswith("I have attached the file(s) below."), reply
    assert f"{code}-td.pdf" in reply and "attachment type for" not in reply, reply


def _assert_asks_the_type_for_srt8313(reply: str) -> None:
    """The type ask, in whichever of its shapes ("Please provide the attachment type for
    product Srt8313 ..." or "Product: Srt8313 / Which kind of file do you need?")."""
    assert "Srt8313" in reply and reply.rstrip().endswith(("?", ".")), reply
    assert "attached" not in reply and "escalate" not in reply.lower(), reply


def test_t2_a_bare_code_after_a_drawing_sends_that_codes_drawing(chat, world):
    """"Srt8313" after "Srt8308 drawing": the parser keeps the domain with no domain word of
    its own (the same question about a new product), so the carried type answers it."""
    chat.say("Srt8308 drawing", _t1())
    second = chat.say("Srt8313", _t2())
    _assert_sent_drawing_of(chat, world, second, "SRT8313")


def test_t2_a_new_attachment_ask_naming_no_type_still_asks(chat, world):
    """"attachments for Srt8313" names its own ask (`domain_in_message: true`): a new ask,
    the carried type goes with the old one, and the gate asks which type."""
    chat.say("Srt8308 drawing", _t1())
    second = chat.say(
        "attachments for Srt8313",
        _verdict("attachments for Srt8313", [_entity("Srt8313", "product")], domain_in_message=True),
    )
    _assert_asks_the_type_for_srt8313(second)


def test_answering_the_open_kind_question_sends_the_asked_products_drawing(chat, world):
    """A guard beside the prod case: "Srt8313 attachment" leaves an OPEN kind question, and
    "Drawing" (the echo plus `domain_in_message: true`) answers it through that question. The
    prod T3 had no open question; `test_answering_the_attachment_type_ask_keeps_the_asked_product`
    is that case."""
    first = chat.say(
        "Srt8313 attachment",
        _verdict("attachment for Srt8313", [_entity("Srt8313", "product")], domain_in_message=True),
    )
    _assert_asks_the_type_for_srt8313(first)
    second = chat.say("Drawing", _t3())
    _assert_sent_drawing_of(chat, world, second, "SRT8313")


# --------------------------------------------------------------------------- #
# The seams, pure                                                              #
# --------------------------------------------------------------------------- #


def _focus_with_settled_product():
    from app.services.chatbot.turn.state import Focus

    return Focus(
        domains=["product_attachment"],
        products=[{"raw": "SRT8313", "canonical_code": "SRT8313", "uuid": "p-8313"}],
        extra={"attachment_type": [{"raw": "Technical Specifications", "canonical_code": "Technical Specifications", "uuid": "t-1"}]},
    )


def test_a_dropped_echo_records_the_kind_it_restated():
    from app.services.chatbot import turn_runtime
    from app.services.chatbot.turn.decide import restated_kinds

    out = turn_runtime.without_restated_rows(_t3(), _focus_with_settled_product())
    assert [e["raw"] for e in out["entities"]] == ["Drawing"], out["entities"]
    assert restated_kinds(out) == {"product"}


def test_no_echo_records_no_restated_kind():
    from app.services.chatbot import turn_runtime
    from app.services.chatbot.turn.decide import restated_kinds

    verdict = _verdict("drawing", [_entity("Drawing", "attachment_type", "technical drawing")], domain_in_message=True)
    out = turn_runtime.without_restated_rows(verdict, _focus_with_settled_product())
    assert restated_kinds(out) == set()


def test_a_new_ask_keeps_the_restated_kind_and_drops_the_rest():
    """The NEW ASK eviction keeps the kind the parser restated, and still drops a carried
    kind the message neither named nor restated."""
    from app.services.chatbot.turn import apply
    from app.services.chatbot.turn.decide import RESTATED_KINDS_KEY, decide
    from app.services.chatbot.turn.plan import Trace

    focus = _focus_with_settled_product()
    focus.customers = [{"raw": "HANLIM", "canonical_code": "HANLIM", "uuid": "c-1"}]
    verdict = {
        **_verdict("drawing", [_entity("Drawing", "attachment_type", "technical drawing")], domain_in_message=True),
        RESTATED_KINDS_KEY: ["product"],
    }
    decision = decide(verdict, focus, None)
    assert decision.starts_fresh, decision
    trace = Trace()
    out = apply._focus_rules(
        focus, verdict, verdict["entities"], decision, domain_locked=False, domain_override=None, trace=trace
    )
    assert [r["uuid"] for r in out.products] == ["p-8313"], out.products
    assert out.customers == [], out.customers
    assert "new_ask_drops_product" not in trace.rules_fired and "new_ask_drops_customer" in trace.rules_fired, trace.rules_fired


def test_a_restated_kinds_key_from_the_parser_is_never_trusted():
    """Reviewer S1: only the engine stamps the key; a parser emission carrying one loses it,
    on the early exits too."""
    from app.services.chatbot import turn_runtime
    from app.services.chatbot.turn.decide import RESTATED_KINDS_KEY, restated_kinds

    forged = {**_verdict("drawing for Srt8308", [_entity("Srt8308", "product")]), RESTATED_KINDS_KEY: ["customer", "product"]}
    assert restated_kinds(turn_runtime.without_restated_rows(forged, _focus_with_settled_product())) == set()


def test_a_typed_product_beside_the_echo_stamps_nothing():
    """Golden row 11: "drawing for <code C>" names its own product, so the echoed product is
    not restated and goes with the new ask."""
    from app.services.chatbot import turn_runtime
    from app.services.chatbot.turn.decide import restated_kinds

    verdict = _verdict(
        "drawing for SRT8308",
        [
            _entity("Srt8313", "product", typed=False),
            _entity("SRT8308", "product"),
            _entity("drawing", "attachment_type", "technical drawing"),
        ],
        domain_in_message=True,
    )
    out = turn_runtime.without_restated_rows(verdict, _focus_with_settled_product())
    assert restated_kinds(out) == set() and len(out["entities"]) == 3, out


def test_a_settled_carried_type_is_handed_over_with_its_uuid():
    """Reviewer S2: the settled type reaches the resolver as the row it is, uuid included."""
    from app.services.chatbot import turn_runtime

    out = turn_runtime._with_carried_document_types(_t2(), _focus_with_settled_product())
    carried = [e for e in out["entities"] if e.get("hint") == "attachment_type"]
    assert carried == [
        {
            "raw": "Technical Specifications",
            "hint": "attachment_type",
            "canonical_code": "Technical Specifications",
            "current_message": False,
            "confident": True,
            "uuid": "t-1",
        }
    ], out["entities"]
