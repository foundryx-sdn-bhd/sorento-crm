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


def test_a_pick_off_the_lists_is_dropped_whatever_the_customer_typed(db):
    # "drawing" is a word of the type's description, never its code: no word is read.
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
