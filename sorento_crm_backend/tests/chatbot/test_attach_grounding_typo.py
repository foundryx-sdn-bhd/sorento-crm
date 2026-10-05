"""ATTACH-GROUNDING-TYPO (prod 5 Oct 2026 12:02, prompt v42).

"m495 techincal drawings": the parser returned `product_attachment` with the entity
{raw "techincal drawings", hint attachment_type, canonical_code "technical drawing"}.
Grounding read only `raw`, found "techincal" on no list (exact match only, #833), demoted
the entity to an EMPTY specification and switched the domain to master_products: the reply
was a product list, no files.

The parser's own canonical code now counts when it is on the attachment list and a word the
customer typed is, exactly, a word of it. The #833 misfilings ("zebra" read as "photo")
share no word with their code and stay demoted (pinned in `test_spec_grounding.py`). Fake
data only: the product is a seeded ZZT code.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from tests.chatbot._turn_helpers import entity, verdict
from tests.chatbot.test_engine import _envelope, stub_access, stub_parser  # noqa: F401
from tests.chatbot.test_rearch_s3_attribute_first import (
    SORENTO,
    _db,
    _link_contact_company,
    _seed_contact,
    _seed_products,
)
from tests.chatbot.test_spec_grounding import _e, vdb  # noqa: F401

TYPO = "techincal drawings"


def _ground(db, *entities: dict[str, Any], domain: str | None = "product_attachment"):
    from app.services.chatbot.head.grounding import ground

    return ground(db, {"domain_hint": domain, "intent_hint": "check_product_attachment", "entities": list(entities)})


# --------------------------------------------------------------------------- #
# Grounding                                                                    #
# --------------------------------------------------------------------------- #


def test_the_prod_turn_keeps_the_document_type_and_the_domain(vdb):
    product = _e("m495", "product")
    document = _e(TYPO, "attachment_type", canonical_code="technical drawing")
    out, _notes = _ground(vdb, product, document)
    assert out["domain_hint"] == "product_attachment", out
    assert out["entities"] == [product, document], out
    assert not [e for e in out["entities"] if e["hint"] == "specification"], out


@pytest.mark.parametrize(
    "raw,canonical",
    [("techincal drawings", "technical drawing"), ("techincal drawing", "technical drawing"), ("techincal drawing", "Technical Drawing")],
)
def test_a_word_of_the_parsers_document_type_backs_it(vdb, raw, canonical):
    out, _ = _ground(vdb, _e("m495", "product"), _e(raw, "attachment_type", canonical_code=canonical))
    hints = [e["hint"] for e in out["entities"]]
    assert hints == ["product", "attachment_type"] and out["domain_hint"] == "product_attachment", out


@pytest.mark.parametrize(
    "raw,canonical",
    [
        ("techincal drawngs", "technical drawing"),  # every word a typo: no word backs it
        ("techincal drawings", None),  # no parser code at all
        ("techincal drawings", "zebra drawing"),  # a code that is on no list
    ],
)
def test_without_a_backed_code_on_the_list_the_old_demotion_stands(vdb, raw, canonical):
    out, _ = _ground(vdb, _e("m495", "product"), _e(raw, "attachment_type", canonical_code=canonical))
    assert all(e["hint"] != "attachment_type" for e in out["entities"]), out
    [spec] = [e for e in out["entities"] if e["hint"] == "specification"]
    assert spec["spec_key"] == "" and spec["spec_value"] is None, spec
    assert out["domain_hint"] == "master_products", out


def test_a_code_sharing_no_word_with_what_was_typed_gets_no_say(vdb):
    """#833: "zebra" read as "photo" is a misfiled descriptor, never a document type."""
    out, _ = _ground(vdb, _e("water closet", "category"), _e("zebra", "attachment_type", canonical_code="photo"))
    assert all(e["hint"] != "attachment_type" for e in out["entities"]), out


# --------------------------------------------------------------------------- #
# The whole turn, on fake data                                                 #
# --------------------------------------------------------------------------- #


def _seed_drawing_type(session_factory) -> None:
    import uuid

    from app.models.resources import AttachmentType

    db = _db(session_factory)
    if db.query(AttachmentType).filter(AttachmentType.type_name == "Technical Drawing").first() is None:
        db.add(AttachmentType(id=str(uuid.uuid4()), type_name="Technical Drawing", allowed_extensions="pdf"))
        db.commit()


def test_the_prod_turn_replayed_looks_up_the_files(session_factory, stub_parser, stub_access, monkeypatch):
    from app.services.ai_assistant_service import MCPRuntimeClient
    from app.services.chatbot import engine as engine_mod

    _seed_contact(session_factory, phone="+60000000031")
    _link_contact_company(session_factory, company_id=SORENTO)
    [code] = _seed_products(session_factory, class_label="m495", synonyms=[], count=1)
    _seed_drawing_type(session_factory)

    calls: list[str] = []

    def fake_call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        calls.append(name)
        return json.dumps({"items": [], "attachments": [], "action_links": []})

    monkeypatch.setattr(MCPRuntimeClient, "call_tool", fake_call_tool)
    document = entity(TYPO, hint="attachment_type", canonical_code="technical drawing")
    stub_parser(
        verdict(
            domain_hint="product_attachment",
            intent_hint="check_product_attachment",
            entities=[entity(code, hint="product"), document],
        )
    )
    stub_access(attributes=["product_attachment.technical_drawing"])
    envelope = _envelope()
    envelope.message["message"]["message"]["text"] = f"{code} {TYPO}"

    result = engine_mod.run_turn(envelope, session_factory=session_factory)

    assert result.ctx["parse"]["output"]["domain_hint"] == "product_attachment", result.ctx["parse"]["output"]
    assert "crm_master_product_attachments_list" in calls, (calls, (result.reply or {}).get("text"))
    assert "crm_master_products_list" not in calls, calls
    # The parser's own answer, before grounding, is what `_parser_raw` carries.
    raw_entities = result.ctx["parse"]["_parser_raw"]["entities"]
    assert document in raw_entities, raw_entities


def test_parser_raw_is_the_verdict_before_grounding(session_factory, stub_parser, stub_access, monkeypatch):
    """`ctx.parse._parser_raw` is what the parser said: a misfiled "zebra" document type
    that grounding rewrites stays an attachment_type there, while `output` is grounded."""
    from app.services.ai_assistant_service import MCPRuntimeClient
    from app.services.chatbot import engine as engine_mod

    _seed_contact(session_factory, phone="+60000000032")
    _seed_drawing_type(session_factory)
    monkeypatch.setattr(MCPRuntimeClient, "call_tool", lambda self, name, arguments: json.dumps({"items": []}))
    misfiled = entity("zebra", hint="attachment_type", canonical_code="photo")
    stub_parser(verdict(domain_hint="product_attachment", entities=[misfiled]))
    stub_access()

    result = engine_mod.run_turn(_envelope(), session_factory=session_factory)

    assert misfiled in result.ctx["parse"]["_parser_raw"]["entities"], result.ctx["parse"]["_parser_raw"]
    assert result.ctx["parse"]["_parser_raw"]["domain_hint"] == "product_attachment"
    assert all(e.get("hint") != "attachment_type" for e in result.ctx["parse"]["output"]["entities"])
