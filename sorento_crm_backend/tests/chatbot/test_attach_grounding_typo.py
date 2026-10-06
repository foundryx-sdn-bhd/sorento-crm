"""ATTACH-GROUNDING-TYPO, option (c): the parser's document type is trusted (owner decision,
6 Oct 2026 19:30).

Prod 5 Oct 2026 12:02, "m495 techincal drawings": the parser returned `product_attachment`
with {raw "techincal drawings", hint attachment_type, canonical_code "technical drawing"}.
Grounding read the customer's words, found "techincal" on no list, demoted the entity to an
empty specification and switched the domain to master_products: a product answer, no files.

Owner rule: only the parser understands words. A confident attachment_type whose
canonical_code is a listed document type (exact, whole code: a taxonomy value or an
`attachment_types` name or code) stays that type, whatever the customer typed. A code that is
not listed is dropped. No word matching, no aliases, no spec demotion, no domain flip. Fake
data only: the products are seeded ZZT codes.
"""
from __future__ import annotations

import json
import uuid
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


def _ground(db, *entities: dict[str, Any]):
    from app.services.chatbot.head.grounding import ground

    return ground(
        db, {"domain_hint": "product_attachment", "intent_hint": "check_product_attachment", "entities": list(entities)}
    )


# --------------------------------------------------------------------------- #
# Grounding                                                                    #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "raw,code",
    [
        ("techincal drawings", "technical drawing"),
        ("TD DRAW", "technical drawing"),
        ("phtos", "photo"),
        ("vid", "video"),
        ("3d", "3D model"),
        ("sijil", "certificate"),
        ("install guide", "installation guide"),
        ("pics", "Product Photos"),  # an `attachment_types.type_name`, exact
    ],
)
def test_a_listed_parser_code_is_kept_whatever_was_typed(vdb, raw, code):
    product = _e("m495", "product")
    document = _e(raw, "attachment_type", canonical_code=code)
    out, _notes = _ground(vdb, product, document)
    assert out["entities"] == [product, document], out
    assert out["domain_hint"] == "product_attachment", out


@pytest.mark.parametrize("raw", ["pink colour", "f trap", "thickness 1.2 mm", "zebra"])
def test_a_misfiled_descriptor_is_the_parsers_type_now(vdb, raw):
    """Owner ruling (c) replaces #833's misfiling demotion: the parser is trusted."""
    document = _e(raw, "attachment_type", canonical_code="photo")
    out, _ = _ground(vdb, _e("water closet", "category"), document)
    assert document in out["entities"], out
    assert not [e for e in out["entities"] if e["hint"] == "specification"], out
    assert out["domain_hint"] == "product_attachment", out


@pytest.mark.parametrize(
    "extra",
    [
        {"canonical_code": "zebra drawing"},  # not a listed type
        {"canonical_code": "drawings"},  # a word of one, not the whole code
        {"canonical_code": "technical drawing", "confident": False},
    ],
)
def test_an_unlisted_or_unconfident_code_is_dropped(vdb, extra):
    product = _e("m495", "product")
    out, notes = _ground(vdb, product, _e("techincal drawings", "attachment_type", **extra))
    assert out["entities"] == [product], out
    assert out["domain_hint"] == "product_attachment", out
    assert notes, notes


def test_no_canonical_code_reads_raw_exactly(vdb):
    kept, _ = _ground(vdb, _e("photo", "attachment_type"))
    assert [e["hint"] for e in kept["entities"]] == ["attachment_type"], kept
    dropped, _ = _ground(vdb, _e("photos", "attachment_type"))
    assert dropped["entities"] == [], dropped


# --------------------------------------------------------------------------- #
# The whole turn, on fake data                                                 #
# --------------------------------------------------------------------------- #


def _seed_types(session_factory) -> None:
    from app.models.resources import AttachmentType

    db = _db(session_factory)
    for name in ("Technical Drawing", "Product Photos"):
        if db.query(AttachmentType).filter(AttachmentType.type_name == name).first() is None:
            db.add(AttachmentType(id=str(uuid.uuid4()), type_name=name, allowed_extensions="pdf,jpg"))
    db.commit()


def _files_envelope(code: str, type_name: str, filename: str) -> str:
    """`sorento_crm_mcp.presenters.present_response` for one product_attachments row."""
    url = f"https://example.invalid/{filename.replace(' ', '_')}"
    return json.dumps(
        {
            "result_type": "product_attachments",
            "intro": "I have attached the file(s) below.",
            "items": [
                {
                    "title": code,
                    "fields": [
                        {"label": "Product Code", "value": code},
                        {"label": "Product Name", "value": f"ZZT {code}"},
                        {"label": "Attachment Type", "value": type_name},
                        {"label": "File Name", "value": filename},
                    ],
                    "flags": {"discontinued": False, "expired": False, "expiring_soon": False},
                }
            ],
            "attachments": [{"url": url, "filename": filename, "mimeType": "application/pdf", "attachmentType": type_name}],
            "action_links": [],
            "last_updated_at": None,
            "has_result": True,
        }
    )


def _turn(session_factory, stub_parser, stub_access, monkeypatch, *, said: str, code_word: str, canonical: str):
    from app.services.ai_assistant_service import MCPRuntimeClient
    from app.services.chatbot import engine as engine_mod

    _seed_contact(session_factory, phone="+60000000051")
    _link_contact_company(session_factory, company_id=SORENTO)
    [code] = _seed_products(session_factory, class_label=code_word, synonyms=[], count=1)
    _seed_types(session_factory)
    calls: list[str] = []
    type_name = "Product Photos" if canonical == "photo" else "Technical Drawing"
    filename = f"{code} {'photo.jpg' if canonical == 'photo' else 'drawing.pdf'}"

    def fake_call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        calls.append(name)
        if name == "crm_master_product_attachments_list":
            return _files_envelope(code, type_name, filename)
        return json.dumps({"items": [], "attachments": [], "action_links": [], "has_result": False})

    monkeypatch.setattr(MCPRuntimeClient, "call_tool", fake_call_tool)
    document = entity(said, hint="attachment_type", canonical_code=canonical)
    stub_parser(
        verdict(
            domain_hint="product_attachment",
            intent_hint="check_product_attachment",
            entities=[entity(code, hint="product"), document],
        )
    )
    stub_access()
    envelope = _envelope()
    envelope.message["message"]["message"]["text"] = f"{code} {said}"
    result = engine_mod.run_turn(envelope, session_factory=session_factory)
    return result, calls, document, filename


@pytest.mark.parametrize(
    "said,canonical",
    [("TD DRAW", "technical drawing"), ("techincal drawings", "technical drawing"), ("phtos", "photo")],
)
def test_the_parsers_document_type_fetches_the_files(session_factory, stub_parser, stub_access, monkeypatch, said, canonical):
    result, calls, document, filename = _turn(
        session_factory, stub_parser, stub_access, monkeypatch, said=said, code_word="srtwc286", canonical=canonical
    )
    reply = result.reply or {}
    assert result.ctx["parse"]["output"]["domain_hint"] == "product_attachment", result.ctx["parse"]["output"]
    assert "crm_master_product_attachments_list" in calls, (calls, reply.get("text"))
    assert "crm_master_products_list" not in calls, calls
    assert reply.get("text", "").startswith("I have attached the file(s) below."), reply
    assert [a["filename"] for a in reply.get("attachments_src") or []] == [filename], reply
    assert document in result.ctx["parse"]["_parser_raw"]["entities"]


def test_an_unlisted_code_never_fetches_files(session_factory, stub_parser, stub_access, monkeypatch):
    """Control: the parser's code is not a listed type, so no files are fetched."""
    result, calls, _document, _filename = _turn(
        session_factory, stub_parser, stub_access, monkeypatch, said="zebra", code_word="srtwc287", canonical="zebra"
    )
    assert "crm_master_product_attachments_list" not in calls, (calls, (result.reply or {}).get("text"))
    assert not (result.reply or {}).get("attachments_src"), result.reply


def test_parser_raw_is_the_verdict_before_grounding(session_factory, stub_parser, stub_access, monkeypatch):
    """`ctx.parse._parser_raw` is what the parser said: an unlisted document type that
    grounding drops stays an attachment_type there, while `output` no longer has it."""
    from app.services.ai_assistant_service import MCPRuntimeClient
    from app.services.chatbot import engine as engine_mod

    _seed_contact(session_factory, phone="+60000000052")
    _seed_types(session_factory)
    monkeypatch.setattr(MCPRuntimeClient, "call_tool", lambda self, name, arguments: json.dumps({"items": []}))
    unlisted = entity("zebra", hint="attachment_type", canonical_code="zebra")
    stub_parser(verdict(domain_hint="product_attachment", entities=[unlisted]))
    stub_access()

    result = engine_mod.run_turn(_envelope(), session_factory=session_factory)

    assert unlisted in result.ctx["parse"]["_parser_raw"]["entities"], result.ctx["parse"]["_parser_raw"]
    assert all(e.get("hint") != "attachment_type" for e in result.ctx["parse"]["output"]["entities"])
