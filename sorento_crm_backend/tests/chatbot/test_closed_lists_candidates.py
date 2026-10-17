"""CHAT-CLOSED-LISTS S2: the per-turn candidate shortlist (card R1, crew Q1 (a)).

Code only SHORTLISTS: for the words of this message it lists product codes (exact, contains),
product types and spec values (exact word or synonym), and near spellings of either. Each
candidate says what it is, so the parser picks the kind as well as the value, or picks none.
Code never binds on a candidate: near spellings only ADD a line. Shapes follow the owner's
CP0 cards with synthetic codes (c06 "WC", c07/c27 spec words, typos).
"""
from __future__ import annotations

import uuid

import pytest

import app.main  # noqa: F401  isort:skip
from app.models.base import set_company_scope
from app.models.product import Product, ProductCategory, UnitOfMeasure
from app.models.product_spec import ProductSpecRegistry
from app.services.chatbot.head import candidates
from app.services.company_scope import DEFAULT_COMPANY_ID, register_company_scope_listeners
from tests._pg_fixture import blank_session, unique_code

OTHER_COMPANY = "00000000-0000-0000-0000-000000000002"


@pytest.fixture(autouse=True)
def _scope_listeners():
    register_company_scope_listeners()


@pytest.fixture
def db():
    with blank_session() as session:
        from app.models.company import Company

        session.add(Company(id=OTHER_COMPANY, name="Other", code=unique_code("OTH")[:20]))
        session.flush()
        yield session


def _registry(db, spec_key: str, label: str, values: list[str], synonyms: dict[str, list[str]] | None = None,
              data_type: str = "enum") -> None:
    db.add(ProductSpecRegistry(
        id=str(uuid.uuid4()), spec_key=spec_key, label=label, data_type=data_type,
        allowed_values=values, synonyms=synonyms or {},
    ))
    db.flush()


def _products(db, *codes: str, company_id: str = DEFAULT_COMPANY_ID, searchable: bool = True) -> None:
    cat = ProductCategory(
        id=str(uuid.uuid4()), category_code=unique_code("CAT")[:50], category_name="ZZT cat", company_id=company_id,
    )
    uom = UnitOfMeasure(id=str(uuid.uuid4()), uom_code=unique_code("UOM")[:50], uom_name="Each", company_id=company_id)
    db.add_all([cat, uom])
    db.flush()
    for code in codes:
        db.add(Product(
            id=str(uuid.uuid4()), product_code=code, product_name=f"Name of {code}", category_id=cat.id,
            base_uom_id=uom.id, list_price=1, company_id=company_id, is_searchable=searchable,
        ))
    db.flush()


def _lines(db, message: str) -> list[str]:
    return candidates.candidate_lines(candidates.shortlist(db, message))


def _seed_wc(db) -> None:
    _registry(db, "class", "Class", ["water_closet", "basin"], {"water_closet": ["wc", "toilet bowl"]})
    _registry(db, "seat_cover", "Seat cover", ["soft_close", "normal"], {"soft_close": ["slow close"]})


def test_c06_shape_wc_is_listed_as_the_product_type_its_synonym_names(db):
    _seed_wc(db)

    assert _lines(db, "WC with soft close?") == [
        candidates.HEADER,
        '- "wc": product type Water closet (class = water_closet)',
        '- "soft close": spec seat_cover = soft_close (Soft close)',
    ]


def test_a_synonym_lists_the_value_it_belongs_to(db):
    _seed_wc(db)

    assert '- "slow close": spec seat_cover = soft_close (Soft close)' in _lines(db, "any slow close seat")


def test_a_near_spelling_adds_the_value_it_is_close_to_and_says_so(db):
    _seed_wc(db)

    assert '- "sofy close": near spec seat_cover = soft_close (Soft close)' in _lines(db, "sofy close cover")


def test_a_code_token_lists_the_codes_that_hold_it_exact_first(db):
    _products(db, "SRTWC8517", "SRTWC8517-WH", "SRTWC286")

    lines = _lines(db, "stock srtwc8517")
    assert lines == [
        candidates.HEADER,
        '- "srtwc8517": code SRTWC8517 (Name of SRTWC8517); code SRTWC8517-WH (Name of SRTWC8517-WH)',
    ]


def test_a_mistyped_code_lists_its_near_codes(db):
    _products(db, "SRTWC8517", "SRTKT71SS")

    assert _lines(db, "srtwc8571 eta") == [
        candidates.HEADER,
        '- "srtwc8571": near code SRTWC8517 (Name of SRTWC8517)',
    ]


def test_codes_outside_the_contact_scope_or_not_chat_searchable_are_never_listed(db):
    _products(db, "SRTZZ9001", company_id=OTHER_COMPANY)
    _products(db, "SRTZZ9002", searchable=False)
    set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))

    assert _lines(db, "srtzz900") == []


def test_each_word_lists_at_most_the_cap_and_the_block_at_most_its_line_cap(db):
    _products(db, *[f"SRTCAP{n:03d}" for n in range(12)])

    line = _lines(db, "srtcap0")[1]
    assert line.count("code SRTCAP") == candidates.CAP_PER_WORD

    _registry(db, "finish", "Finish", [f"tone{n}" for n in range(40)])
    message = " ".join(f"tone{n}" for n in range(40))
    assert len(_lines(db, message)) == 1 + candidates.CAP_LINES


def test_a_message_with_nothing_on_file_adds_no_block(db):
    _seed_wc(db)
    _products(db, "SRTWC8517")

    assert _lines(db, "hello, how are you today") == []
    assert _lines(db, "") == []


def test_a_word_alone_is_never_read_for_a_code_inside_a_longer_code(db):
    # "wc" is in every SRTWC code: without a digit the word is matched only as a whole code.
    _seed_wc(db)
    _products(db, "SRTWC8517", "WC")

    assert _lines(db, "wc price") == [
        candidates.HEADER,
        '- "wc": product type Water closet (class = water_closet); code WC (Name of WC)',
    ]


def test_numeric_and_boolean_keys_are_left_to_the_parser(db):
    _registry(db, "width_mm", "Width", [], {"_self": ["width"]}, data_type="numeric")
    _registry(db, "has_massage", "Massage jets", [], {"true": ["jacuzzi"]}, data_type="boolean")

    assert _lines(db, "width 600 jacuzzi") == []
