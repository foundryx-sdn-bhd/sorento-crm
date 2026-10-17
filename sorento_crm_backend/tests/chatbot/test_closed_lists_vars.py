"""CHAT-CLOSED-LISTS S1: the file-type and certification-type lists the parser picks from.

Card R1/R2 (`documentation/plans/chatbot/chat-closed-lists-behaviour-card.md`): every turn the
parser is given the closed lists, rendered by code from the tables, so it answers with a listed
item or "none". Two registry variables, `{{attachment_types}}` and `{{certification_types}}`,
rendered by `chatbot_prompt_vars` like `{{specs}}`. A template without the tokens renders exactly
as before, so the production prompt is untouched until a version carrying them is published.
"""
from __future__ import annotations

import uuid

import pytest

import app.main  # noqa: F401  isort:skip
from app.models.certificate import Certificate
from app.models.lookup import LookupOption, LookupOptionKeyword, LookupSet
from app.models.product import Product, ProductAttachment, ProductCategory, UnitOfMeasure
from app.models.resources import Attachment, AttachmentType
from app.services import chatbot_prompt_vars as pv
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
        yield session


def _type(db, name: str, *, code: str | None = None, description: str | None = None, cert: bool = False) -> str:
    row = AttachmentType(
        id=str(uuid.uuid4()), type_name=name, code=code, description=description,
        allowed_extensions="jpg,pdf", is_certificate=cert,
    )
    db.add(row)
    db.flush()
    return row.id


def _product(db) -> str:
    cat = ProductCategory(
        id=str(uuid.uuid4()), category_code=unique_code("CAT")[:50], category_name="ZZT cat",
        company_id=DEFAULT_COMPANY_ID,
    )
    uom = UnitOfMeasure(
        id=str(uuid.uuid4()), uom_code=unique_code("UOM")[:50], uom_name="Each", company_id=DEFAULT_COMPANY_ID,
    )
    db.add_all([cat, uom])
    db.flush()
    code = unique_code("SRT")
    row = Product(
        id=str(uuid.uuid4()), product_code=code, product_name=code, category_id=cat.id,
        base_uom_id=uom.id, list_price=1, company_id=DEFAULT_COMPANY_ID,
    )
    db.add(row)
    db.flush()
    return row.id


def _file_on_product(db, type_id: str, product_id: str, *, deleted: bool = False) -> None:
    att = Attachment(
        id=str(uuid.uuid4()), original_filename="zzt.jpg", stored_filename=f"{uuid.uuid4().hex}.jpg",
        file_path="https://cdn.test/zzt.jpg", attachment_type_id=type_id, is_deleted=deleted,
        company_id=DEFAULT_COMPANY_ID,
    )
    db.add(att)
    db.flush()
    db.add(ProductAttachment(
        id=str(uuid.uuid4()), product_id=product_id, attachment_id=att.id, company_id=DEFAULT_COMPANY_ID,
    ))
    db.flush()


def _certificate(db, scheme: str, *, type_id: str | None = None, status: str = "active") -> None:
    db.add(Certificate(
        id=str(uuid.uuid4()), attachment_type_id=type_id, scheme=scheme,
        certificate_number=unique_code("CERT")[:120], status=status, company_id=None,
    ))
    db.flush()


def _scheme_lookup(db, values: dict[str, list[str]], *, inactive: tuple[str, ...] = ()) -> None:
    lookup = LookupSet(id=str(uuid.uuid4()), set_key="certificate_scheme", name="Certificate Scheme")
    db.add(lookup)
    db.flush()
    for i, (value, keywords) in enumerate(values.items()):
        option = LookupOption(
            id=str(uuid.uuid4()), set_id=lookup.id, value=value, label=value, sort_order=i,
            is_active=value not in inactive,
        )
        db.add(option)
        db.flush()
        for word in keywords:
            db.add(LookupOptionKeyword(id=str(uuid.uuid4()), option_id=option.id, keyword=word))
    db.flush()


# --- {{attachment_types}} ------------------------------------------------------------------------


def test_the_file_type_list_names_each_type_a_product_carries_with_its_code_and_words(db):
    product = _product(db)
    photos = _type(db, "Product Photos", code="product_photos", description="Product Photos, Photo, Image")
    drawings = _type(db, "Technical Specifications", code="technical_specifications",
                     description="Spec / Drawing by Marketing")
    _file_on_product(db, photos, product)
    _file_on_product(db, drawings, product)

    assert pv.render_value(db, "attachment_types") == (
        "File type product_photos: Product Photos. Words: Product Photos, Photo, Image.\n"
        "File type technical_specifications: Technical Specifications. Words: Spec / Drawing by Marketing."
    )


def test_a_type_no_product_carries_and_a_certificate_kind_are_not_file_types(db):
    product = _product(db)
    photos = _type(db, "Product Photos", code="product_photos")
    _type(db, "Shipment Line Photo", code="shipment_line_photo")  # an internal SCM type, no product file
    certs = _type(db, "Certification", code="certification", cert=True)
    _file_on_product(db, photos, product)
    _file_on_product(db, certs, product)
    gone = _type(db, "Old Brochure", code="old_brochure")
    _file_on_product(db, gone, product, deleted=True)

    assert pv.render_value(db, "attachment_types") == "File type product_photos: Product Photos."


def test_a_type_without_a_code_is_listed_by_its_name(db):
    product = _product(db)
    _file_on_product(db, _type(db, "Installation Guide"), product)

    assert pv.render_value(db, "attachment_types") == "File type Installation Guide: Installation Guide."


def test_no_file_types_renders_nothing(db):
    assert pv.render_value(db, "attachment_types") == ""


# --- {{certification_types}} ---------------------------------------------------------------------


def test_the_certification_list_names_the_cert_kinds_and_every_scheme_on_file(db):
    certs = _type(db, "Certification", code="certification", description="Cert, Certificate, Sijil", cert=True)
    _certificate(db, "SIRIM", type_id=certs)
    _certificate(db, "PPS", type_id=certs)
    _certificate(db, "PPS", type_id=certs)  # two certificates, one scheme
    _certificate(db, "BOMBA", type_id=certs, status="archived")

    assert pv.render_value(db, "certification_types") == (
        "Certificate kind certification: Certification. Words: Cert, Certificate, Sijil.\n"
        "Certificate scheme PPS.\n"
        "Certificate scheme SIRIM."
    )


def test_the_scheme_lookup_set_adds_its_words_and_its_own_schemes(db):
    _certificate(db, "PPS")
    _scheme_lookup(
        db,
        {"pps": ["pps cert", "suruhanjaya"], "IKRAM": ["ikram"], "HALAL": ["halal"]},
        inactive=("HALAL",),
    )

    # The register's spelling wins over the lookup value's case; an inactive option is left out.
    assert pv.render_value(db, "certification_types") == (
        "Certificate scheme IKRAM. Words: ikram.\n"
        "Certificate scheme PPS. Words: pps cert, suruhanjaya."
    )


def test_no_certificates_renders_nothing(db):
    assert pv.render_value(db, "certification_types") == ""


# --- registry wiring -----------------------------------------------------------------------------


def test_both_lists_are_registry_variables_cleared_when_their_tables_change():
    assert {"attachment_types", "certification_types"} <= set(pv.VARIABLE_NAMES)
    assert "attachment_types" in pv.VARIABLES["attachment_types"].tables
    assert {"attachment_types", "certificates", "lookup_options", "lookup_option_keywords"} <= set(
        pv.VARIABLES["certification_types"].tables
    )


def test_a_template_without_the_tokens_renders_unchanged(db):
    product = _product(db)
    _file_on_product(db, _type(db, "Product Photos", code="product_photos"), product)
    template = "Parser text with {{current_date}} and no list token."

    assert pv.render_values_safe(db, set()) == {}
    from app.services.ai_prompt_registry import _substitute

    assert _substitute(template, {"current_date": "today"}) == "Parser text with today and no list token."
