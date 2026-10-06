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
    from app.models.resources import AttachmentType
    from app.services.ai_assistant_service import MCPRuntimeClient
    from app.services.chatbot import engine as engine_mod

    _seed_contact(session_factory, phone="+60000000051")
    _link_contact_company(session_factory, company_id=SORENTO)
    [code] = _seed_products(session_factory, class_label=code_word, synonyms=[], count=1)
    _seed_types(session_factory)
    calls: list[str] = []
    sent: list[dict[str, Any]] = []
    type_name = "Product Photos" if canonical == "photo" else "Technical Drawing"
    filename = f"{code} {'photo.jpg' if canonical == 'photo' else 'drawing.pdf'}"

    def fake_call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        calls.append(name)
        if name == "crm_master_product_attachments_list":
            sent.append(dict(arguments))
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
    type_id = _db(session_factory).query(AttachmentType.id).filter(AttachmentType.type_name == type_name).scalar()
    return result, calls, document, filename, sent, type_id


@pytest.mark.parametrize(
    "said,canonical",
    [("TD DRAW", "technical drawing"), ("techincal drawings", "technical drawing"), ("phtos", "photo")],
)
def test_the_parsers_document_type_fetches_the_files(session_factory, stub_parser, stub_access, monkeypatch, said, canonical):
    result, calls, document, filename, sent, type_id = _turn(
        session_factory, stub_parser, stub_access, monkeypatch, said=said, code_word="srtwc286", canonical=canonical
    )
    reply = result.reply or {}
    assert result.ctx["parse"]["output"]["domain_hint"] == "product_attachment", result.ctx["parse"]["output"]
    assert "crm_master_product_attachments_list" in calls, (calls, reply.get("text"))
    assert "crm_master_products_list" not in calls, calls
    assert reply.get("text", "").startswith("I have attached the file(s) below."), reply
    assert [a["filename"] for a in reply.get("attachments_src") or []] == [filename], reply
    assert document in result.ctx["parse"]["_parser_raw"]["entities"]
    assert sent and sent[0].get("attachment_type_ids") == [type_id], sent


def test_an_unlisted_code_never_fetches_files(session_factory, stub_parser, stub_access, monkeypatch):
    """Control: the parser's code is not a listed type, so no files are fetched."""
    result, calls, _document, _filename, _sent, _type_id = _turn(
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


# --------------------------------------------------------------------------- #
# The described-set path: a category, not a product code (reviewer B1)          #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "raw,code,expected",
    [
        ("techincal drawings", "technical drawing", {"attachment_type": "technical drawing"}),
        ("phtos", "photo", {"attachment_type": "photo"}),
        ("TD DRAW", "technical drawing", {"attachment_type": "technical drawing"}),
        ("PPS cert", "certificate", {"certificate": {"scheme": "PPS"}}),
        ("photo", None, {"attachment_type": "photo"}),
    ],
)
def test_the_document_leg_is_the_parsers_code(raw, code, expected):
    """The set's document leg is the parser's code, never the customer's spelling; a
    certificate scheme still rides on the words ("PPS cert")."""
    from app.services.chatbot.lanes.business.predicate import derive_require

    parser_output = {
        "intent_hint": "check_product_attachment",
        "entities": [{"hint": "attachment_type", "raw": raw, "canonical_code": code}],
    }
    assert derive_require(parser_output) == expected


def test_the_document_leg_and_its_words_come_from_one_entity():
    """The code is read off the same entity whose words are read: an entity with a code
    and no words in front of it never lends its code to the next one (reviewer nit)."""
    from app.services.chatbot.lanes.business.predicate import derive_require

    parser_output = {
        "intent_hint": "check_product_attachment",
        "entities": [
            {"hint": "attachment_type", "raw": "", "canonical_code": "photo"},
            {"hint": "attachment_type", "raw": "TD DRAW", "canonical_code": "technical drawing"},
        ],
    }
    assert derive_require(parser_output) == {"attachment_type": "technical drawing"}


@pytest.mark.parametrize(
    "said,canonical,type_name",
    [
        ("techincal drawings", "technical drawing", "Technical Drawing"),
        ("technical drawings", "technical drawing", "Technical Drawing"),
        # Round 8's misfiling, under the ruling: the parser filed "pink colour" as a photo,
        # so the set's photos are what is fetched (reviewer B2: say what customers get).
        ("pink colour", "photo", "Product Photos"),
    ],
)
def test_a_set_ask_fetches_the_parsers_document_type(
    session_factory, stub_parser, stub_access, monkeypatch, said, canonical, type_name
):
    """"which tap has techincal drawings": the taps that hold a Technical Drawing, fetched
    with that type's id, exactly as the correct spelling is."""
    from app.models.product import ProductAttachment
    from app.models.resources import Attachment, AttachmentType
    from tests.chatbot.test_engine import _parser_output
    from tests.chatbot.test_lane_require import (
        _s4_contact_id,
        _s4_envelope,
        _s4_real_resolve_entity,
        _s4_seed_contact,
        _s4_wire_engine,
        _seed_category_and_uom,
        _seed_registry,
        _tap_product,
    )

    contact_id = _s4_contact_id("tdset")
    db = session_factory()
    category_id, uom_id = _seed_category_and_uom(db)
    _seed_registry(db)
    drawing = AttachmentType(id=str(uuid.uuid4()), code="ZZT_DOC", type_name=type_name, allowed_extensions="pdf")
    db.add(drawing)
    db.flush()
    codes = []
    for i in range(2):
        product = _tap_product(db, category_id=category_id, uom_id=uom_id)
        attachment = Attachment(
            id=str(uuid.uuid4()),
            original_filename=f"zzt-td-{i}.pdf",
            stored_filename=f"zzt-td-{i}.pdf",
            file_path=f"https://example.invalid/zzt-td-{i}.pdf",
            attachment_type_id=drawing.id,
        )
        db.add(attachment)
        db.flush()
        db.add(ProductAttachment(id=str(uuid.uuid4()), product_id=product.id, attachment_id=attachment.id))
        codes.append(product.product_code)
    db.commit()
    _s4_seed_contact(session_factory, contact_id=contact_id, session_vars={"variables": {}})

    calls: list[dict[str, Any]] = []

    def fake_call_tool(name: str, args: dict[str, Any]) -> str:
        calls.append({"name": name, "args": dict(args)})
        return _files_envelope(codes[0], type_name, "zzt-td-0.pdf")

    engine_mod = _s4_wire_engine(
        session_factory, monkeypatch, resolve_entity=_s4_real_resolve_entity(db), fetch_mcp_call=fake_call_tool
    )
    stub_parser(
        _parser_output(
            intent_hint="check_product_attachment",
            domain_hint="product_attachment",
            match_mode="or",
            entities=[
                {"raw": "tap", "hint": "product_type", "canonical_code": None, "current_message": True, "confident": True},
                {"raw": said, "hint": "attachment_type", "canonical_code": canonical, "current_message": True, "confident": True},
            ],
        )
    )
    stub_access()

    turn = engine_mod.run_turn(
        _s4_envelope(contact_id=contact_id, message_id=f"ZZT-tdset-{said[:4]}", text=f"which tap has {said}"),
        session_factory=session_factory,
    )
    text = (turn.reply or {}).get("text", "")
    assert turn.status == "done", turn.error
    assert "as a document type" not in text, text
    fetches = [c for c in calls if c["name"] == "crm_master_product_attachments_list"]
    assert fetches, (calls, text)
    assert fetches[0]["args"].get("attachment_type_ids") == [drawing.id], fetches[0]["args"]


def test_an_unlisted_code_under_a_category_keeps_the_parsers_domain(vdb):
    """AC-4: dropping a document type never flips the domain. With only a category left the
    ask stays the parser's `product_attachment` / `check_product_attachment`."""
    category = _e("water closet", "category")
    out, notes = _ground(vdb, category, _e("pink", "attachment_type", canonical_code="pink"))
    assert out["entities"] == [category], out
    assert (out["domain_hint"], out["intent_hint"]) == ("product_attachment", "check_product_attachment"), out
    assert all(note.get("from") != "domain" for note in notes), notes


def test_an_unlisted_code_under_a_category_asks_which_kind_of_file(
    session_factory, stub_parser, stub_access, monkeypatch
):
    """"which tap has zebra", filed as a document type the list does not hold: the type is
    dropped, the domain stays the parser's, and the reply is the file-kind menu. No product
    answer and no file fetch (hand test Expected 7)."""
    from tests.chatbot.test_engine import _parser_output
    from tests.chatbot.test_lane_require import (
        _s4_contact_id,
        _s4_envelope,
        _s4_real_resolve_entity,
        _s4_seed_contact,
        _s4_wire_engine,
        _seed_category_and_uom,
        _seed_registry,
        _tap_product,
    )

    contact_id = _s4_contact_id("dropset")
    db = session_factory()
    category_id, uom_id = _seed_category_and_uom(db)
    _seed_registry(db)
    product = _tap_product(db, category_id=category_id, uom_id=uom_id)
    code = product.product_code
    db.commit()
    _s4_seed_contact(session_factory, contact_id=contact_id, session_vars={"variables": {}})

    calls: list[dict[str, Any]] = []

    def fake_call_tool(name: str, args: dict[str, Any]) -> str:
        calls.append({"name": name, "args": dict(args)})
        return json.dumps({"items": []})

    engine_mod = _s4_wire_engine(
        session_factory, monkeypatch, resolve_entity=_s4_real_resolve_entity(db), fetch_mcp_call=fake_call_tool
    )
    stub_parser(
        _parser_output(
            intent_hint="check_product_attachment",
            domain_hint="product_attachment",
            match_mode="or",
            entities=[
                {"raw": "tap", "hint": "product_type", "canonical_code": None, "current_message": True, "confident": True},
                {"raw": "zebra", "hint": "attachment_type", "canonical_code": "zebra", "current_message": True, "confident": True},
            ],
        )
    )
    stub_access()

    turn = engine_mod.run_turn(
        _s4_envelope(contact_id=contact_id, message_id="ZZT-dropset-1", text="which tap has zebra"),
        session_factory=session_factory,
    )
    text = (turn.reply or {}).get("text", "")
    assert turn.status == "done", turn.error
    assert turn.ctx["parse"]["output"]["domain_hint"] == "product_attachment", turn.ctx["parse"]["output"]
    assert "Which kind of file do you need?" in text, text
    assert code not in text, text
    assert not [c for c in calls if c["name"] == "crm_master_product_attachments_list"], calls


def test_an_attachment_type_code_is_a_listed_type(vdb):
    """The type table's `code` column counts, exactly (reviewer N2)."""
    from app.models.resources import AttachmentType

    vdb.add(AttachmentType(id=str(uuid.uuid4()), code="ZZT_INSTALL", type_name="ZZT Install Sheet", allowed_extensions="pdf"))
    vdb.flush()
    document = _e("install sheet", "attachment_type", canonical_code="zzt_install")
    out, _ = _ground(vdb, document)
    assert out["entities"] == [document], out
    word, _ = _ground(vdb, _e("install sheet", "attachment_type", canonical_code="zzt"))
    assert word["entities"] == [], word
