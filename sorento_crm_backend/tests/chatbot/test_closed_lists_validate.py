"""CHAT-CLOSED-LISTS S4b: picks are validated against the lists, legs come from the list tag.

Card R2/R5: under the CLOSED LISTS gate an `attachment_type` entity is kept only when its
canonical_code is an item the turn's lists render (file type, certificate kind, certificate
scheme; case aside, never a word of the message), and is tagged with the list it came from.
`derive_require` then builds the certificate or file leg from that tag, not from the cert
regexes: card c04/c19 ("span certificate", "span" is no scheme on file) is the certificate
ask, never a scheme filter that matches nothing. Synthetic rows; masked card shapes.
"""
from __future__ import annotations

import uuid

import pytest

import app.main  # noqa: F401  isort:skip
from app.models.certificate import Certificate
from app.models.product import Product, ProductAttachment, ProductCategory, UnitOfMeasure
from app.models.resources import Attachment, AttachmentType
from app.services import chatbot_prompt_vars as pv
from app.services.chatbot.head import grounding
from app.services.chatbot.lanes.business.predicate import derive_require
from app.services.company_scope import DEFAULT_COMPANY_ID
from tests._pg_fixture import blank_session, unique_code


@pytest.fixture(autouse=True)
def _fresh_cache():
    pv.clear_cache()
    yield
    pv.clear_cache()


@pytest.fixture
def db():
    with blank_session() as session:
        cat = ProductCategory(
            id=str(uuid.uuid4()), category_code=unique_code("CAT")[:50], category_name="ZZT cat",
            company_id=DEFAULT_COMPANY_ID,
        )
        uom = UnitOfMeasure(id=str(uuid.uuid4()), uom_code=unique_code("UOM")[:50], uom_name="Each", company_id=DEFAULT_COMPANY_ID)
        session.add_all([cat, uom])
        session.flush()
        product = Product(
            id=str(uuid.uuid4()), product_code=unique_code("SRTZ"), product_name="ZZT", category_id=cat.id,
            base_uom_id=uom.id, list_price=1, company_id=DEFAULT_COMPANY_ID,
        )
        drawings = AttachmentType(
            id=str(uuid.uuid4()), type_name="Technical Specifications", code="technical_specifications",
            description="Spec / Drawing by Marketing", allowed_extensions="pdf",
        )
        certs = AttachmentType(
            id=str(uuid.uuid4()), type_name="Certification", code="certification",
            description="cert, certificate, watermark cert, wcm", allowed_extensions="pdf", is_certificate=True,
        )
        session.add_all([product, drawings, certs])
        session.flush()
        att = Attachment(
            id=str(uuid.uuid4()), original_filename="td.pdf", stored_filename=f"{uuid.uuid4().hex}.pdf",
            file_path="https://cdn.test/td.pdf", attachment_type_id=drawings.id, is_deleted=False,
            company_id=DEFAULT_COMPANY_ID,
        )
        session.add(att)
        session.flush()
        session.add(ProductAttachment(id=str(uuid.uuid4()), product_id=product.id, attachment_id=att.id, company_id=DEFAULT_COMPANY_ID))
        from app.models.lookup import LookupOption, LookupOptionKeyword, LookupSet

        lookup = LookupSet(id=str(uuid.uuid4()), set_key="attachment_type_alias", name="Attachment Type Alias")
        session.add(lookup)
        session.flush()
        option = LookupOption(id=str(uuid.uuid4()), set_id=lookup.id, value="Technical Specifications",
                              label="Technical Specifications", sort_order=0)
        session.add(option)
        session.flush()
        for word in ("technical drawing", "TD"):
            session.add(LookupOptionKeyword(id=str(uuid.uuid4()), option_id=option.id, keyword=word))
        session.add(Certificate(
            id=str(uuid.uuid4()), attachment_type_id=certs.id, scheme="SIRIM",
            certificate_number=unique_code("CERT")[:120], status="active", company_id=None,
        ))
        session.flush()
        yield session


def _file_entity(raw: str, canonical: str | None, *, confident: bool = True) -> dict:
    return {"raw": raw, "hint": "attachment_type", "canonical_code": canonical, "current_message": True, "confident": confident}


def _verdict(*entities: dict) -> dict:
    return {"intent_hint": "check_product_attachment", "domain_hint": "product_attachment", "entities": list(entities)}


def _ground(db, *entities: dict, closed: bool = True) -> list[dict]:
    out, _notes = grounding.ground(db, _verdict(*entities), message="", closed_lists=closed)
    return [e for e in out["entities"] if isinstance(e, dict)]


# --- grounding ----------------------------------------------------------------------------------


def test_a_listed_file_type_is_kept_and_tagged(db):
    [entity] = _ground(db, _file_entity("TD", "technical_specifications"))
    assert entity["canonical_code"] == "technical_specifications"
    assert entity["listed"] == "file_type"


def test_a_cert_kind_and_a_scheme_on_file_are_tagged_with_the_listed_spelling(db):
    kind, scheme = _ground(db, _file_entity("cert", "Certification"), _file_entity("sirim", "sirim"))
    assert (kind["listed"], kind["canonical_code"]) == ("cert_kind", "certification")
    assert (scheme["listed"], scheme["canonical_code"]) == ("cert_scheme", "SIRIM")


def test_the_parsers_listed_item_names_the_item_whatever_its_code_says(db):
    """S4b-v2: under the gate the provider holds `listed_item` to the rendered items (a JSON
    enum), so the parser's understanding of "TD" or "span certificate" arrives as the item."""
    [drawing] = _ground(db, {**_file_entity("TD", "technical drawing"), "listed_item": "technical_specifications"})
    assert (drawing["listed"], drawing["canonical_code"]) == ("file_type", "technical_specifications")
    [cert] = _ground(db, {**_file_entity("span certificate", "certificate"), "listed_item": "certification"})
    assert (cert["listed"], cert["canonical_code"]) == ("cert_kind", "certification")
    [scheme] = _ground(db, {**_file_entity("sirim", None), "listed_item": "SIRIM"})
    assert (scheme["listed"], scheme["canonical_code"]) == ("cert_scheme", "SIRIM")


def test_an_explicit_alias_link_names_its_type(db):
    """The owner's `attachment_type_alias` lookup set is an explicit link, so a code that is
    one of its keywords names that type."""
    [drawing] = _ground(db, _file_entity("TD", "technical drawing"))
    assert (drawing["listed"], drawing["canonical_code"]) == ("file_type", "technical_specifications")


def test_a_description_word_is_never_matched_by_code(db):
    """Crew's dev data check (17 Oct): description text ("cert, certificate, ...", "Spec /
    Drawing by Marketing") is shown to the parser, never matched by code."""
    assert _ground(db, _file_entity("span certificate", "certificate")) == []
    assert _ground(db, _file_entity("spec", "Spec")) == []


def test_a_word_two_items_share_names_neither(db):
    photos = AttachmentType(
        id=str(uuid.uuid4()), type_name="Product Photos", code="product_photos", description="photo, image",
        allowed_extensions="jpg",
    )
    db.add(photos)
    db.flush()
    from app.models.product import ProductAttachment as PA

    product_id = db.query(PA.product_id).first()[0]
    att = Attachment(
        id=str(uuid.uuid4()), original_filename="p.jpg", stored_filename=f"{uuid.uuid4().hex}.jpg",
        file_path="https://cdn.test/p.jpg", attachment_type_id=photos.id, is_deleted=False, company_id=DEFAULT_COMPANY_ID,
    )
    db.add(att)
    db.flush()
    db.add(PA(id=str(uuid.uuid4()), product_id=product_id, attachment_id=att.id, company_id=DEFAULT_COMPANY_ID))
    db.flush()
    # The owner links the word "sheet" to both file types in the alias set.
    from app.models.lookup import LookupOption, LookupOptionKeyword

    drawing_option = db.query(LookupOption).one()
    photo_option = LookupOption(
        id=str(uuid.uuid4()), set_id=drawing_option.set_id, value="Product Photos", label="Product Photos", sort_order=1,
    )
    db.add(photo_option)
    db.flush()
    for option in (drawing_option, photo_option):
        db.add(LookupOptionKeyword(id=str(uuid.uuid4()), option_id=option.id, keyword="sheet"))
    db.flush()

    assert _ground(db, _file_entity("sheet", "sheet")) == []


def test_a_pick_off_the_lists_is_dropped_whatever_the_customer_typed(db):
    # "drawing" is no listed word ("Drawing by Marketing" is), and the raw is never read.
    assert _ground(db, _file_entity("drawing", "drawing")) == []
    assert _ground(db, _file_entity("span", "span")) == []
    assert _ground(db, _file_entity("TD", None)) == []


def test_an_unconfident_pick_is_dropped(db):
    assert _ground(db, _file_entity("TD", "technical_specifications", confident=False)) == []


def test_gate_off_no_entity_is_tagged(db):
    out = _ground(db, _file_entity("TD", "technical_specifications"), closed=False)
    assert all("listed" not in e for e in out)


# --- derive_require from the tag ----------------------------------------------------------------


def _tagged(raw: str, canonical: str, listed: str) -> dict:
    return {**_file_entity(raw, canonical), "listed": listed}


def test_c04_c19_shape_a_cert_kind_alone_is_the_certificate_ask_whatever_the_raw():
    verdict = _verdict(_tagged("span certificate", "certification", "cert_kind"))
    assert derive_require(verdict, message_text="span certificate") == {"certificate": True}


def test_a_listed_scheme_scopes_the_certificate_ask():
    verdict = _verdict(_tagged("cert", "certification", "cert_kind"), _tagged("sirim", "SIRIM", "cert_scheme"))
    assert derive_require(verdict) == {"certificate": {"scheme": "SIRIM"}}


def test_a_listed_scheme_alone_is_the_certificate_ask_of_that_scheme():
    assert derive_require(_verdict(_tagged("sirim", "SIRIM", "cert_scheme"))) == {"certificate": {"scheme": "SIRIM"}}


def test_a_listed_file_type_is_its_own_leg():
    verdict = _verdict(_tagged("techincal drawing", "technical_specifications", "file_type"))
    assert derive_require(verdict) == {"attachment_type": "technical_specifications"}


def test_a_certificate_attribute_takes_its_scheme_from_the_tag_not_the_raw():
    verdict = {
        **_verdict(_tagged("span cert", "certification", "cert_kind")),
        "intent_hint": "check_product",
        "requested_attributes": ["certificate"],
    }
    assert derive_require(verdict) == {"certificate": True}


def test_control_untagged_entities_keep_todays_reading():
    """Gate off (no tag): today's split, "span" read as the scheme."""
    verdict = _verdict(_file_entity("span certificate", "certificate"))
    assert derive_require(verdict, message_text="span certificate") == {"certificate": {"scheme": "span"}}


def test_under_the_gate_an_untagged_file_ask_reads_no_word_and_is_asked():
    """Card R3: no listed pick means the bot asks; the cert regexes never read the raw or
    the parser's own goal sentence on a closed-lists verdict."""
    verdict = {**_verdict(_file_entity("span certificate", "certificate")), "closed_lists": True, "user_goal": "span cert"}
    assert derive_require(verdict, message_text="span certificate") is None
    bare = {**_verdict(), "closed_lists": True, "user_goal": "wants the cert"}
    assert derive_require(bare, message_text="any cert?") is None



def test_an_items_own_name_outranks_another_items_word(db):
    """"PPS" is the scheme's own name and also a word of the Certification type: it is the
    scheme (the live check on a synthetic 'which basin got pps cert' read it as the kind)."""
    certs = db.query(AttachmentType).filter(AttachmentType.code == "certification").one()
    certs.description = "cert, certificate, pps"
    db.add(Certificate(
        id=str(uuid.uuid4()), attachment_type_id=certs.id, scheme="PPS",
        certificate_number=unique_code("CERT")[:120], status="active", company_id=None,
    ))
    db.flush()

    [scheme] = _ground(db, _file_entity("pps", "PPS"))
    assert (scheme["listed"], scheme["canonical_code"]) == ("cert_scheme", "PPS")


def test_the_enum_is_every_rendered_item_once_in_list_order(db):
    assert pv.closed_list_enum(db) == ["technical_specifications", "certification", "SIRIM"]


def test_an_item_too_long_for_the_enum_is_left_out(db):
    """Security review (17 Oct): the strict enum is bounded. A scheme is at most 60 by its
    column (`certificates.scheme` varchar(60)); a type name may be 100, and one past the cap
    never reaches the prompt list's enum."""
    long_type = AttachmentType(
        id=str(uuid.uuid4()), type_name="X" * 80, code=None, allowed_extensions="pdf", is_certificate=True,
    )
    db.add(long_type)
    db.flush()

    assert pv.closed_list_enum(db) == ["technical_specifications", "certification", "SIRIM"]


def test_the_enum_is_capped():
    import app.services.chatbot_prompt_vars as module

    assert module.MAX_LISTED_ITEMS == 100 and module.MAX_LISTED_ITEM_CHARS == 60


def test_none_of_these_is_no_pick_whatever_the_code_says(db):
    from app.services.chatbot.head.closed_lists_prompt import NOT_LISTED

    assert _ground(db, {**_file_entity("brochure", "technical_specifications"), "listed_item": NOT_LISTED}) == []
