"""ATTACH-SET-BRAND-FOLLOWUP (owner hand test, 6 Oct 2026 22:40, #1490 test copy, prompt v58).

Turn 1 "which tap has technical drawing" answered the counted set with a brand breakdown
and fetched under `attachment_type_ids = [Technical Specifications]`. Turn 2 "sorento"
narrowed the set to the brand - and the attachments tool was called with `product_ids`
alone, so the rows under the "Sorento taps with technical specifications" header were
Product Photos files. Owner: "it forgot the attachment type".

Same substrate as `test_attribute_asks_round3.py`: `engine.run_turn` with the real resolver,
the real brands table and the real class vocabulary, with only the MCP tool call stubbed.
The attachments stub honours `attachment_type_ids` exactly as the real tool does: with the
filter it returns that type's files, without it EVERY file of the products, photos first -
which is what the owner saw.
"""
from __future__ import annotations

import json
import re
import uuid
from typing import Any

import pytest

from tests.chatbot.set_reply import one_line_header, snake_tokens
from tests.chatbot.test_attribute_asks_round2 import _brand, _product
from tests.chatbot.test_counted_set_no_paging import _link_to_default_company
from tests.chatbot.test_engine import stub_access, stub_parser  # noqa: F401 - fixtures used by name
from tests.chatbot.test_lane_require import (
    _s4_contact_id,
    _s4_envelope,
    _s4_real_resolve_entity,
    _s4_seed_contact,
    _s4_wire_engine,
    _seed_category_and_uom,
    _seed_registry,
)
from tests.chatbot.test_reverse_asks_owner_phrasings import _class_category

DRAWING = "Technical Drawing"
PHOTO = "Product Photos"


# --------------------------------------------------------------------------- #
# World                                                                         #
# --------------------------------------------------------------------------- #


def _attach(db, product, type_row, filename: str) -> None:
    from app.models.product import ProductAttachment
    from app.models.resources import Attachment

    attachment = Attachment(
        id=str(uuid.uuid4()),
        original_filename=filename,
        stored_filename=filename,
        file_path=f"https://example.invalid/{filename}",
        attachment_type_id=type_row.id,
    )
    db.add(attachment)
    db.flush()
    db.add(ProductAttachment(id=str(uuid.uuid4()), product_id=product.id, attachment_id=attachment.id))


@pytest.fixture()
def world(session_factory):
    """Four Sorento taps and one Mocha tap, every one with a Technical Drawing AND a
    Product Photo on file. With `SET_LIST_MAX` at 3 the five-tap set is too long to list,
    so turn 1 is the count with its brand breakdown, exactly as the owner's 298 was."""
    from app.models.product import Brand
    from app.models.resources import AttachmentType

    db = session_factory()
    db.query(Brand).update({Brand.chatbot_weight: 0})
    _category_id, uom_id = _seed_category_and_uom(db)
    tap_category = _class_category(db, "FT")
    sorento = _brand(db, f"Sorento{uuid.uuid4().hex[:4]}", default=True)
    mocha = _brand(db, f"Mocha{uuid.uuid4().hex[:4]}")
    _seed_registry(db)
    types = {}
    for name in (DRAWING, PHOTO):
        row = db.query(AttachmentType).filter(AttachmentType.type_name == name).first()
        if row is None:
            row = AttachmentType(id=str(uuid.uuid4()), type_name=name, allowed_extensions="pdf,jpg")
            db.add(row)
            db.flush()
        types[name] = row

    def make(brand, prefix, name):
        return _product(db, brand_id=brand.id, category_id=tap_category, uom_id=uom_id, noun="BASIN TAP", prefix=prefix, name=name)

    srt_taps = [make(sorento, "ZZSBF", f"Sorento Basin Tap {i}") for i in range(4)]
    # Reviewer should-fix (round 2): the Mocha code sorts FIRST, so the Sorento page (the
    # first three Sorento taps) is never the same three ids as the first page of EVERY tap;
    # a document word that recounted the class set would fetch the Mocha tap and show it.
    mch_taps = [make(mocha, "ZZSBA", "Mocha Basin Tap")]
    for p in srt_taps + mch_taps:
        _attach(db, p, types[PHOTO], f"{p.product_code}-01.jpg")
        _attach(db, p, types[DRAWING], f"{p.product_code}-td.pdf")
    db.commit()
    # The dev copy's own `attachment_type_alias` row "photo" -> Product Photos (the one
    # "technical drawing" -> Technical Specifications sits beside): a document word the
    # resolver can place. Without one the word reaches the fetch with no uuid and is
    # skipped there (`lanes/business/fetch.py` `missing_or_bad_uuid`), on main and here.
    from tests.chatbot.test_escalation_agent_carry import _seed_photo_alias

    _seed_photo_alias(session_factory)
    return {
        "db": db,
        "sorento": sorento,
        "mocha": mocha,
        "srt_taps": srt_taps,
        "mch_taps": mch_taps,
        "drawing": types[DRAWING],
        "photo": types[PHOTO],
    }


def _attachments_tool(db):
    """`crm_master_product_attachments_list` as the presenter renders it: one row per file
    of each product in `product_ids`, narrowed to `attachment_type_ids` WHEN GIVEN - and
    every file, photos first, when not (the real tool's behaviour, and the owner's rows)."""
    from app.models.product import Product, ProductAttachment
    from app.models.resources import Attachment, AttachmentType

    def call(args: dict[str, Any]) -> str:
        product_ids = list(args.get("product_ids") or [])
        type_ids = list(args.get("attachment_type_ids") or [])
        query = (
            db.query(Product, Attachment, AttachmentType)
            .join(ProductAttachment, ProductAttachment.product_id == Product.id)
            .join(Attachment, Attachment.id == ProductAttachment.attachment_id)
            .join(AttachmentType, AttachmentType.id == Attachment.attachment_type_id)
            .filter(Product.id.in_(product_ids))
        )
        if type_ids:
            query = query.filter(Attachment.attachment_type_id.in_(type_ids))
        rows = query.order_by(Product.product_code, AttachmentType.type_name.desc()).all()
        items = []
        files = []
        for product, attachment, type_row in rows:
            items.append(
                {
                    "title": product.product_code,
                    "fields": [
                        {"label": "Product Code", "value": product.product_code},
                        {"label": "Product Name", "value": product.product_name},
                        {"label": "Attachment Type", "value": type_row.type_name},
                        {"label": "File Name", "value": attachment.original_filename},
                    ],
                    "flags": {"discontinued": False, "expired": False, "expiring_soon": False},
                }
            )
            files.append(
                {
                    "url": attachment.file_path,
                    "filename": attachment.original_filename,
                    "mimeType": "application/pdf" if attachment.original_filename.endswith(".pdf") else "image/jpeg",
                    "attachmentType": type_row.type_name,
                }
            )
        return json.dumps(
            {
                "result_type": "product_attachments",
                "intro": "I have attached the file(s) below.",
                "items": items,
                "attachments": files,
                "action_links": [],
                "last_updated_at": None,
                "has_result": bool(items),
            }
        )

    return call


class _Chat:
    def __init__(self, session_factory, monkeypatch, stub_parser, stub_access, world):
        self.session_factory = session_factory
        self.stub_parser = stub_parser
        self.stub_access = stub_access
        self.calls: list[dict[str, Any]] = []
        self.contact_id = _s4_contact_id(f"bf{uuid.uuid4().hex[:6]}")
        _s4_seed_contact(session_factory, contact_id=self.contact_id, session_vars={"variables": {}})
        _link_to_default_company(session_factory, self.contact_id)
        attachments = _attachments_tool(world["db"])

        def fake_call_tool(name: str, args: dict[str, Any]) -> str:
            self.calls.append({"name": name, "args": dict(args)})
            if name == "crm_master_product_attachments_list":
                return attachments(args)
            return json.dumps({"items": [], "attachments": [], "action_links": [], "has_result": False})

        self.engine = _s4_wire_engine(
            session_factory,
            monkeypatch,
            resolve_entity=_s4_real_resolve_entity(world["db"]),
            fetch_mcp_call=fake_call_tool,
        )
        self.n = 0

    def say(self, text: str, verdict: dict[str, Any]) -> str:
        self.n += 1
        self.stub_parser(verdict)
        self.stub_access()
        turn = self.engine.run_turn(
            _s4_envelope(contact_id=self.contact_id, message_id=f"ZZT-{self.contact_id}-{self.n}", text=text),
            session_factory=self.session_factory,
        )
        assert turn.status == "done", turn.error
        reply = (turn.reply or {}).get("text") or ""
        assert not snake_tokens(reply), (text, snake_tokens(reply), reply)
        return reply

    def attachment_calls(self) -> list[dict[str, Any]]:
        return [c["args"] for c in self.calls if c["name"] == "crm_master_product_attachments_list"]


@pytest.fixture()
def chat(session_factory, monkeypatch, stub_parser, stub_access, world):
    return _Chat(session_factory, monkeypatch, stub_parser, stub_access, world)


@pytest.fixture()
def small_list(monkeypatch):
    from app.services.chatbot.lanes.business import answer as answer_mod

    monkeypatch.setattr(answer_mod, "SET_LIST_MAX", 3)


def _entity(raw: str, hint: str, canonical_code: str | None = None) -> dict[str, Any]:
    return {"raw": raw, "hint": hint, "canonical_code": canonical_code, "current_message": True, "confident": True}


def _turn_1() -> dict[str, Any]:
    """"which tap has technical drawing": the category and the document type, as the parser
    files them (#1490: the parser's `canonical_code` is the document leg)."""
    from tests.chatbot.test_engine import _parser_output

    return _parser_output(
        intent_hint="check_product_attachment",
        domain_hint="product_attachment",
        match_mode="or",
        user_goal="which tap has technical drawing",
        entities=[_entity("tap", "product_type"), _entity("technical drawing", "attachment_type", "technical drawing")],
    )


def _turn_2_brand(brand: str) -> dict[str, Any]:
    """"sorento" after the breakdown, as the real trace reads it: a brand entity continuing
    the open ask (`entity_op: reuse`), nothing else named."""
    from tests.chatbot.test_engine import _parser_output

    return _parser_output(
        intent_hint="check_product_attachment",
        domain_hint="product_attachment",
        match_mode="or",
        user_goal="sorento taps with technical drawing",
        entities=[_entity(brand, "brand")],
        entity_op="reuse",
    )


def _turn_2_count() -> dict[str, Any]:
    from tests.chatbot.test_engine import _parser_output

    return _parser_output(intent_hint=None, domain_hint=None, entities=[], user_goal=None, top_n=3)


def _row_types(text: str) -> list[str]:
    return re.findall(r"\*Attachment Type:\* (.+)", text)


def _display(name: str) -> str:
    return name.title() if name.isupper() else name


# --------------------------------------------------------------------------- #
# AC-1 / AC-2: the brand follow-up                                              #
# --------------------------------------------------------------------------- #


def test_the_brand_follow_up_fetches_the_same_document_type(chat, world, small_list):
    first = chat.say("which tap has technical drawing", _turn_1())
    sorento, mocha = _display(world["sorento"].brand_name), _display(world["mocha"].brand_name)
    # Turn 1 as the owner saw it: the count and the breakdown by brand, fetched under the
    # resolved document type.
    # SET-STOCK-RENDER (SSR-6): the first 3 of 5 are listed, no breakdown lines.
    assert one_line_header(first) == "Here's what you want: taps with technical drawing (5, showing 1 to 3)", first
    assert not [ln for ln in first.split("\n") if ln.startswith("• ")], first
    assert chat.attachment_calls()[-1].get("attachment_type_ids") == [world["drawing"].id], chat.calls

    second = chat.say(world["sorento"].brand_name.lower(), _turn_2_brand(world["sorento"].brand_name.lower()))

    # The owner's "(215, showing 1 to 50)": four Sorento taps, three listed under the small list.
    assert one_line_header(second) == f"Here's what you want: {sorento} taps with technical drawing (4, showing 1 to 3)", second
    calls = chat.attachment_calls()
    assert len(calls) == 2, chat.calls
    # AC-1: the SAME attachment_type_ids as turn 1, beside the Sorento product ids.
    assert calls[1].get("attachment_type_ids") == calls[0].get("attachment_type_ids") == [world["drawing"].id], calls[1]
    page_ids = list(calls[1].get("product_ids") or [])
    assert len(page_ids) == 3 and set(page_ids) <= {p.id for p in world["srt_taps"]}, calls[1]
    # AC-2: the header and the rows agree - three drawings, no photo.
    assert _row_types(second) == [DRAWING] * 3, second
    assert PHOTO not in second, second


def test_the_brand_follow_up_on_a_set_listed_in_full_fetches_the_same_document_type(chat, world):
    """Reviewer should-fix: the OFFER-ONLY carry (`engine.py`, a set listed in full whose
    brands are carried so a brand named next narrows it) needs the ids too - it is the
    same page branch once `set_brand_chosen` replans it."""
    first = chat.say("which tap has technical drawing", _turn_1())
    assert one_line_header(first) == "Here's what you want: taps with technical drawing (5)", first
    assert _row_types(first) == [DRAWING] * 5, first

    sorento = _display(world["sorento"].brand_name)
    second = chat.say(world["sorento"].brand_name.lower(), _turn_2_brand(world["sorento"].brand_name.lower()))

    assert one_line_header(second) == f"Here's what you want: {sorento} taps with technical drawing (4)", second
    calls = chat.attachment_calls()
    assert len(calls) == 2, chat.calls
    assert calls[1].get("attachment_type_ids") == calls[0].get("attachment_type_ids") == [world["drawing"].id], calls[1]
    assert set(calls[1].get("product_ids") or []) == {p.id for p in world["srt_taps"]}, calls[1]
    assert _row_types(second) == [DRAWING] * 4, second


def test_another_n_after_the_brand_page_fetches_the_same_document_type(chat, world, small_list):
    """Reviewer nit: `page_the_set`'s `next_key` spreads the carried key, so "another N"
    after the brand page still fetches under the document type."""
    chat.say("which tap has technical drawing", _turn_1())
    chat.say(world["sorento"].brand_name.lower(), _turn_2_brand(world["sorento"].brand_name.lower()))
    third = chat.say("another 1", _turn_2_count())

    calls = chat.attachment_calls()
    assert len(calls) == 3, chat.calls
    assert calls[2].get("attachment_type_ids") == [world["drawing"].id], calls[2]
    listed = set(calls[1].get("product_ids") or []) | set(calls[2].get("product_ids") or [])
    assert len(calls[2].get("product_ids") or []) == 1 and listed == {p.id for p in world["srt_taps"]}, calls[2]
    assert _row_types(third) == [DRAWING], third


def _turn_3_document_word(raw: str, canonical: str) -> dict[str, Any]:
    """"pic" after the brand page, as the parser prompt files a domain word alone
    (`chatbot_parser_prompt.py` "A domain word alone after an answer ... keeps the
    subject"): only the document entity, `entity_op: reuse`."""
    from tests.chatbot.test_engine import _parser_output

    return _parser_output(
        intent_hint="check_product_attachment",
        domain_hint="product_attachment",
        match_mode="or",
        user_goal=raw,
        entities=[_entity(raw, "attachment_type", canonical)],
        entity_op="reuse",
    )


def test_a_document_word_after_the_brand_page_runs_over_the_listed_taps(chat, world, small_list):
    """Owner hand test on the local copy (crew, 7 Oct 2026): after "sorento" listed the
    page, "pic" ran over the two taps the resolver's code tiers had matched on turn 1
    (the COLD TAP family) instead of the taps the page LISTED. The listed page is the
    conversation's subject once it is shown; a document word after it reads about
    those products, every one of them, under the new document type."""
    chat.say("which tap has technical drawing", _turn_1())
    chat.say(world["sorento"].brand_name.lower(), _turn_2_brand(world["sorento"].brand_name.lower()))
    listed = list(chat.attachment_calls()[1].get("product_ids") or [])
    assert len(listed) == 3, chat.calls

    third = chat.say("photo?", _turn_3_document_word("photo", "photo"))

    calls = chat.attachment_calls()
    assert len(calls) == 3, chat.calls
    # The SAME products the page listed, no more and no fewer, under the photo type: never
    # the Mocha tap, which heads the class set's own first page.
    assert set(calls[2].get("product_ids") or []) == set(listed), (calls[2], listed)
    assert world["mch_taps"][0].id not in (calls[2].get("product_ids") or []), calls[2]
    assert calls[2].get("attachment_type_ids") == [world["photo"].id], calls[2]
    assert _row_types(third) == [PHOTO] * 3, third
    assert DRAWING not in third, third
    # A product-scoped answer, not a recount of the set: no count line, no brand breakdown.
    assert "Here's what you want" not in third and "•" not in third, third


def _turn_3_brand_and_document_word(brand: str) -> dict[str, Any]:
    """"they got pic/" as the live parser read it on the sandbox pass (gpt-5.4-mini, this
    checkout's production prompt): the page's own brand named again beside the document
    word, `entity_op: replace_combine`, `match_mode: and`."""
    from tests.chatbot.test_engine import _parser_output

    return _parser_output(
        intent_hint="check_product_attachment",
        domain_hint="product_attachment",
        match_mode="and",
        user_goal="the picture attachment for the current Sorento tap results",
        entities=[_entity(brand, "brand", brand), _entity("photo", "attachment_type", "photo")],
        entity_op="replace_combine",
    )


def test_the_pages_own_brand_beside_a_document_word_still_reads_about_the_listed_taps(chat, world, small_list):
    """Live console pass on the sandbox (7 Oct 2026, real parser): "they got pic/" after
    the Sorento page came back as brand Sorento + photo, `replace_combine`. The brand
    word names the brand the page is already narrowed to: it restates the subject, it
    opens no new one. Read as a new ask it dropped the listed rows from the focus, ran a
    described set the unplaced brand word qualified nothing for ("Couldn't find: they
    pic"), and left "pic" with no subject at all (a blank header)."""
    chat.say("which tap has technical drawing", _turn_1())
    chat.say(world["sorento"].brand_name.lower(), _turn_2_brand(world["sorento"].brand_name.lower()))
    listed = list(chat.attachment_calls()[1].get("product_ids") or [])
    assert len(listed) == 3, chat.calls

    third = chat.say("they got pic/", _turn_3_brand_and_document_word(world["sorento"].brand_name))

    calls = chat.attachment_calls()
    assert len(calls) == 3, chat.calls
    assert set(calls[2].get("product_ids") or []) == set(listed), (calls[2], listed)
    assert calls[2].get("attachment_type_ids") == [world["photo"].id], calls[2]
    assert _row_types(third) == [PHOTO] * 3, third
    assert "Couldn't find" not in third and "•" not in third, third


def _turn_3_echo_and_document_word(codes: list[str]) -> dict[str, Any]:
    """"pic" as the live parser also read it: a few of the listed codes echoed back as the
    carried subject (`current_message: false`, the prompt's CONTINUE rule) beside the
    document word."""
    from tests.chatbot.test_engine import _parser_output

    return _parser_output(
        intent_hint="check_product_attachment",
        domain_hint="product_attachment",
        match_mode="and",
        user_goal="pic",
        entities=[*({**_entity(c, "product", c), "current_message": False} for c in codes), _entity("pic", "attachment_type", "photo")],
        entity_op="reuse",
    )


def _turn_3_brand_only(brand: str) -> dict[str, Any]:
    """"they got pic/" as the live parser read it on another run: the page's brand and
    nothing else, `replace_combine`."""
    from tests.chatbot.test_engine import _parser_output

    return _parser_output(
        intent_hint="check_product_attachment",
        domain_hint="product_attachment",
        match_mode="and",
        user_goal="they got pic",
        entities=[_entity(brand, "brand", brand)],
        entity_op="replace_combine",
    )


def test_echoed_listed_codes_beside_a_document_word_do_not_narrow_the_listed_taps(chat, world, small_list):
    """Live console pass (7 Oct 2026): the parser echoed some of the listed codes as its
    own entities; re-resolving them re-settled the focus to that handful, so the photos
    came back for two taps of the fifty. An echo of a settled row is the carry restated."""
    chat.say("which tap has technical drawing", _turn_1())
    chat.say(world["sorento"].brand_name.lower(), _turn_2_brand(world["sorento"].brand_name.lower()))
    listed = list(chat.attachment_calls()[1].get("product_ids") or [])
    by_id = {p.id: p.product_code for p in world["srt_taps"]}
    echoed = [by_id[i] for i in listed[:2]]

    third = chat.say("pic", _turn_3_echo_and_document_word(echoed))

    calls = chat.attachment_calls()
    assert len(calls) == 3, chat.calls
    assert set(calls[2].get("product_ids") or []) == set(listed), (calls[2], listed)
    assert calls[2].get("attachment_type_ids") == [world["photo"].id], calls[2]
    assert _row_types(third) == [PHOTO] * 3, third


def test_the_pages_brand_alone_keeps_the_listed_taps_for_the_document_word_that_follows(chat, world, small_list):
    """Live console pass (7 Oct 2026): "they got pic/" read as the brand alone dropped the
    listed rows (a new ask starts fresh), so "pic" after it had no subject and rendered a
    blank header. The page's own brand alone restates the subject."""
    chat.say("which tap has technical drawing", _turn_1())
    chat.say(world["sorento"].brand_name.lower(), _turn_2_brand(world["sorento"].brand_name.lower()))
    listed = set(chat.attachment_calls()[1].get("product_ids") or [])

    chat.say("they got pic/", _turn_3_brand_only(world["sorento"].brand_name))
    for call in chat.attachment_calls()[2:]:
        assert set(call.get("product_ids") or []) <= listed, call
    fourth = chat.say("pic", _turn_3_document_word("pic", "photo"))

    last = chat.attachment_calls()[-1]
    assert set(last.get("product_ids") or []) == listed, (last, listed)
    assert last.get("attachment_type_ids") == [world["photo"].id], last
    assert _row_types(fourth) == [PHOTO] * 3, fourth


def test_an_echo_is_kept_when_the_turn_types_another_axis():
    """Reviewer should-fix: "outstanding DO for hanlim" after a stock answer echoes the
    settled product beside the typed customer; the echo is what keeps the product on a
    new ask that starts fresh, so it is left alone. Only a turn typing nothing but a
    document word drops its echoes."""
    from app.services.chatbot import turn_runtime
    from app.services.chatbot.turn.state import Focus

    settled = {"raw": "ZZT-1", "hint": "product", "canonical_code": "ZZT-1", "uuid": str(uuid.uuid4()), "current_message": False}
    focus = Focus(products=[settled])
    echo = {"raw": "ZZT-1", "hint": "product", "canonical_code": "ZZT-1", "current_message": False}
    customer = {"raw": "hanlim", "hint": "customer", "canonical_code": None, "current_message": True}
    photo = {"raw": "pic", "hint": "attachment_type", "canonical_code": "photo", "current_message": True}

    kept = turn_runtime.without_restated_rows({"entities": [echo, customer]}, focus)
    assert kept["entities"] == [echo, customer], kept
    dropped = turn_runtime.without_restated_rows({"entities": [echo, photo]}, focus)
    assert dropped["entities"] == [photo], dropped
    alone = turn_runtime.without_restated_rows({"entities": [echo]}, focus)
    assert alone["entities"] == [], alone


def test_the_subject_line_names_a_few_of_many_settled_products():
    """Live console pass (7 Oct 2026): fifty listed codes on the subject line made the
    parser echo fifty entities and overflow its token cap."""
    from app.services.chatbot.head.parser import SUBJECT_NAMES_CAP, current_subject_line
    from app.services.chatbot.turn.state import Focus

    rows = [{"raw": f"ZZT-{i:02d}", "hint": "product", "canonical_code": f"ZZT-{i:02d}", "uuid": str(uuid.uuid4())} for i in range(50)]
    line = current_subject_line(Focus(products=rows, domains=["product_attachment"]))
    assert line is not None and f"ZZT-{SUBJECT_NAMES_CAP - 1:02d}" in line and f"(and {50 - SUBJECT_NAMES_CAP} more)" in line, line
    assert f"ZZT-{SUBJECT_NAMES_CAP:02d}" not in line, line
    short = current_subject_line(Focus(products=rows[:3]))
    assert short == "Current subject: product ZZT-00, ZZT-01, ZZT-02.", short


# --------------------------------------------------------------------------- #
# AC-3: the count answer                                                        #
# --------------------------------------------------------------------------- #


def test_a_count_after_the_breakdown_fetches_the_same_document_type(chat, world, small_list):
    chat.say("which tap has technical drawing", _turn_1())
    second = chat.say("3", _turn_2_count())

    calls = chat.attachment_calls()
    assert len(calls) == 2, chat.calls
    assert calls[1].get("attachment_type_ids") == [world["drawing"].id], calls[1]
    # SET-STOCK-RENDER (SSR-5/6): the ask listed 1 to 3, so "3" lists the 2 left, count bare.
    assert len(calls[1].get("product_ids") or []) == 2, calls[1]
    # SET-STOCK-RENDER (SSR-14): a continuation page says its range, the last page too.
    assert one_line_header(second) == "Here's what you want: taps with technical drawing (5, showing 4 to 5)", second
    assert _row_types(second) == [DRAWING] * 2, second


# --------------------------------------------------------------------------- #
# AC-4: the carry                                                               #
# --------------------------------------------------------------------------- #


def _carry_predicate() -> dict[str, Any]:
    return {
        "require": {"attachment_type": "technical drawing"},
        "qualifying_total": 5,
        "class_labels": ["Tap"],
        "set_key": {"specs": [{"key": "class", "value": "Tap"}], "brand": None, "product_ids": []},
        "set_brands": [{"brand": "Sorento", "count": 4}, {"brand": "Mocha", "count": 1}],
        "breakdown": {"brand": [["Sorento", 4], ["Mocha", 1]]},
    }


def _spec():
    from app.services.chatbot.turn.plan import FetchSpec

    return FetchSpec(domain="product_attachment", entities=[], filters={}, date_window=None)


def test_set_page_carry_records_the_document_type_ids():
    from app.services.chatbot import turn_runtime

    carry = turn_runtime.set_page_carry(
        _carry_predicate(), _spec(), ["tap"], access_levels=[], shown=0, attachment_type_ids=["11111111-1111-4111-8111-111111111111"]
    )
    assert carry is not None
    assert carry["set_key"]["attachment_type_ids"] == ["11111111-1111-4111-8111-111111111111"], carry


def test_set_page_carry_writes_no_key_for_a_fetch_without_a_document_type():
    from app.services.chatbot import turn_runtime

    carry = turn_runtime.set_page_carry(_carry_predicate(), _spec(), ["tap"], access_levels=[], shown=0, attachment_type_ids=[])
    assert carry is not None and "attachment_type_ids" not in carry["set_key"], carry
    carry = turn_runtime.set_page_carry(_carry_predicate(), _spec(), ["tap"], access_levels=[], shown=0)
    assert carry is not None and "attachment_type_ids" not in carry["set_key"], carry


def test_the_envelope_reports_the_fetches_attachment_type_ids():
    from app.services.chatbot import turn_runtime

    type_id = "22222222-2222-4222-8222-222222222222"
    envelope = turn_runtime.envelope_of(
        {"fetch": {"has_result": True, "answers": [{"fields": []}]}},
        _spec(),
        [
            {"uuid": "33333333-3333-4333-8333-333333333333", "entity_type": "product", "canonical_code": "ZZT1"},
            {"uuid": type_id, "entity_type": "attachment_type", "canonical_code": "technical drawing"},
            {"uuid": None, "entity_type": "attachment_type", "canonical_code": "photo"},
        ],
    )
    assert envelope["attachment_type_ids"] == [type_id], envelope
