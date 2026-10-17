"""CHAT-CLOSED-LISTS S4c: card R3 / crew Q3 (a), the "which file?" ask lists real options.

When the parser picks no file type off the lists, the bot asks which file, listing the file
types the subject product really has, as a numbered roster a bare "2" answers. Card c19's
"Product: X / Which kind of file do you need?" printed no option at all. Gate off: unchanged.
"""
from __future__ import annotations

import json
import uuid

import pytest

import app.main  # noqa: F401  isort:skip
from app.models.base import set_company_scope
from app.models.product import Product, ProductAttachment, ProductCategory, UnitOfMeasure
from app.models.resources import Attachment, AttachmentType
from app.services.ai_assistant_service import MCPRuntimeClient
from app.services.chatbot import engine as engine_mod
from app.services.chatbot import turn_runtime
from app.services.chatbot.contracts import Focus
from app.services.chatbot.head import parser as parser_mod
from app.services.chatbot.turn import compose as turn_compose
from app.services.chatbot.turn.pending import ask as pending_ask
from app.services.company_scope import DEFAULT_COMPANY_ID
from tests._pg_fixture import unique_code
from tests.chatbot.conftest import session_factory  # noqa: F401
from tests.chatbot.test_engine import _envelope, _parser_output, seeded, stub_access  # noqa: F401

CODE = "ZZTWC7615"


def _seed(session_factory) -> dict[str, str]:
    db = session_factory()
    cat = ProductCategory(
        id=str(uuid.uuid4()), category_code=unique_code("CAT")[:50], category_name="ZZT cat", company_id=DEFAULT_COMPANY_ID,
    )
    uom = UnitOfMeasure(id=str(uuid.uuid4()), uom_code=unique_code("UOM")[:50], uom_name="Each", company_id=DEFAULT_COMPANY_ID)
    db.add_all([cat, uom])
    db.flush()
    product = Product(
        id=str(uuid.uuid4()), product_code=CODE, product_name="ZZT one piece WC", category_id=cat.id,
        base_uom_id=uom.id, list_price=1, company_id=DEFAULT_COMPANY_ID,
    )
    db.add(product)
    db.flush()
    ids = {"product": product.id}
    for name, code, cert in (
        ("Technical Specifications", "technical_specifications", False),
        ("Certification", "certification", True),
        ("Product Photos", "product_photos", False),
    ):
        t = AttachmentType(id=str(uuid.uuid4()), type_name=name, code=code, allowed_extensions="pdf", is_certificate=cert)
        db.add(t)
        db.flush()
        ids[code] = t.id
        if code == "product_photos":
            continue  # a type this product has no file of
        a = Attachment(
            id=str(uuid.uuid4()), original_filename="z.pdf", stored_filename=uuid.uuid4().hex,
            file_path="https://cdn.test/z.pdf", attachment_type_id=t.id, is_deleted=False, company_id=DEFAULT_COMPANY_ID,
        )
        db.add(a)
        db.flush()
        db.add(ProductAttachment(id=str(uuid.uuid4()), product_id=product.id, attachment_id=a.id, company_id=DEFAULT_COMPANY_ID))
    db.commit()
    return ids


def test_the_ask_lists_the_file_types_the_product_has(session_factory):
    ids = _seed(session_factory)
    focus = Focus(products=[{"uuid": ids["product"], "canonical_code": CODE, "raw": CODE, "hint": "product"}])
    ask = pending_ask("attachment_type_ask", [], payload={"domain": "product_attachment"})

    db = session_factory()
    set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))  # the engine's sessions are always scoped
    offered = turn_runtime.file_type_ask(db, ask, focus)

    assert offered.kind == "attachment_type_ask"
    assert offered.payload == {"domain": "product_attachment"}
    assert [(o["position"], o["entity_type"], o["code"], o["uuid"], o["label"]) for o in offered.options] == [
        (1, "attachment_type", "certification", ids["certification"], "Certification"),
        (2, "attachment_type", "technical_specifications", ids["technical_specifications"], "Technical Specifications"),
    ]


def test_no_product_on_the_focus_leaves_the_ask_as_it_is(session_factory):
    _seed(session_factory)
    ask = pending_ask("attachment_type_ask", [])

    assert turn_runtime.file_type_ask(session_factory(), ask, Focus()) is ask


def test_another_kind_of_ask_is_left_alone(session_factory):
    ids = _seed(session_factory)
    focus = Focus(products=[{"uuid": ids["product"], "canonical_code": CODE}])
    ask = pending_ask("customer_pick", [{"position": 1, "code": "C1"}])

    assert turn_runtime.file_type_ask(session_factory(), ask, focus) is ask


def _asked(session_factory, stub_access, monkeypatch, *, gate: bool):
    def fake_resolve_config(db, *, current_date, override_version_id=None):
        return parser_mod.ParserConfig(
            system_prompt="stub", prompt_version=1, provider="openai", model="gpt-test", api_key="k", closed_lists=gate,
        )

    verdict = _parser_output(
        intent_hint="check_product_attachment",
        domain_hint="product_attachment",
        user_goal="wants a file of the product",
        entities=[
            {"raw": CODE, "hint": "product", "canonical_code": CODE, "current_message": True, "confident": True},
            {"raw": "span", "hint": "attachment_type", "canonical_code": None, "current_message": True, "confident": True},
        ],
    )
    monkeypatch.setattr(parser_mod, "resolve_config", fake_resolve_config)
    monkeypatch.setattr(parser_mod, "parse", lambda config, user_block: verdict)
    monkeypatch.setattr(MCPRuntimeClient, "call_tool", lambda self, name, arguments: json.dumps({"ok": True, "items": []}))
    monkeypatch.setattr(engine_mod, "_contact_company_scope", lambda factory, contact: frozenset({DEFAULT_COMPANY_ID}))
    asked: list = []
    real = turn_compose.compose_question

    def spy(ask, state, localizer=None):
        asked.append(ask)
        return real(ask, state, localizer) if localizer is not None else real(ask, state)

    monkeypatch.setattr(turn_compose, "compose_question", spy)
    stub_access()
    result = engine_mod.run_turn(_envelope(), session_factory=session_factory)
    return asked, result


def test_gate_on_the_engine_offers_the_products_file_types(session_factory, seeded, stub_access, monkeypatch):
    _seed(session_factory)

    asked, result = _asked(session_factory, stub_access, monkeypatch, gate=True)

    assert [a.kind for a in asked] == ["attachment_type_ask"], result.reply
    assert [o["label"] for o in asked[0].options] == ["Certification", "Technical Specifications"]
    text = (result.reply or {}).get("text") or ""
    assert "1. Certification" in text and "2. Technical Specifications" in text, text


def test_gate_off_the_ask_is_unchanged(session_factory, seeded, stub_access, monkeypatch):
    _seed(session_factory)

    asked, _result = _asked(session_factory, stub_access, monkeypatch, gate=False)

    assert all(not a.options for a in asked if a.kind == "attachment_type_ask")


def test_a_product_named_by_code_before_the_resolver_ran_is_found_by_its_code(session_factory):
    ids = _seed(session_factory)
    db = session_factory()
    set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
    focus = Focus(products=[{"raw": CODE.lower(), "canonical_code": CODE.lower(), "hint": "product"}])

    offered = turn_runtime.file_type_ask(db, pending_ask("attachment_type_ask", []), focus)

    assert [o["uuid"] for o in offered.options] == [ids["certification"], ids["technical_specifications"]]


def test_a_bare_2_fetches_the_second_offered_type(session_factory, seeded, stub_access, monkeypatch):
    from tests.chatbot.test_engine import CONTACT_ID

    ids = _seed(session_factory)
    _asked(session_factory, stub_access, monkeypatch, gate=True)

    calls: list[tuple[str, dict]] = []

    def call_tool(self, name, arguments):
        calls.append((name, arguments))
        return json.dumps({"ok": True, "items": []})

    pick = _parser_output(
        intent_hint=None, domain_hint=None, message_type="business_query", user_goal="picks option 2",
        reference_positions=[2], entities=[],
    )
    monkeypatch.setattr(parser_mod, "parse", lambda config, user_block: pick)
    monkeypatch.setattr(MCPRuntimeClient, "call_tool", call_tool)
    message = {
        "event_type": "message.received",
        "contact": {"id": CONTACT_ID},
        "message": {
            "messageId": "ZZT-file-ask-2", "contactId": CONTACT_ID, "channelId": "whatsapp", "traffic": "incoming",
            "message": {"type": "text", "text": "2"},
        },
    }
    engine_mod.run_turn(_envelope(message=message), session_factory=session_factory)

    sent = [args for _name, args in calls if "attachment_type_ids" in (args or {})]
    assert sent and sent[0]["attachment_type_ids"] == [ids["technical_specifications"]], calls


def test_a_product_outside_the_contacts_scope_offers_nothing(session_factory):
    ids = _seed(session_factory)
    db = session_factory()
    set_company_scope(db, frozenset({"00000000-0000-0000-0000-000000000009"}))
    focus = Focus(products=[{"uuid": ids["product"], "canonical_code": CODE}])
    ask = pending_ask("attachment_type_ask", [])

    assert turn_runtime.file_type_ask(db, ask, focus) is ask
