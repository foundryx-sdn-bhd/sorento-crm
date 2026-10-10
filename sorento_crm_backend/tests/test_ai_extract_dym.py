"""AI-EXTRACT-DYM: a misread product code carries "did you mean" suggestions
the user confirms; nothing is ever auto-applied.

See documentation/plans/dealer-kit/PLAN-ai-extract-dym.md and
ai-extract-dym-acceptance-criteria.md (AC-B1..B8).

Drives `AIExtractService._extract_products(parsed)` on a real Postgres session,
the same seam `test_ai_extract_resolver_match.py` uses, so the resolver's own
trigram neighbours, company scope and the active filter are all real. The four
codes are the owner's real misreads from the 9 Oct sales order screenshot.
"""
from __future__ import annotations

import uuid

import pytest

from app.models.base import company_scope
from app.models.company import Company
from app.models.product import Product, ProductCategory, UnitOfMeasure
from app.services.ai_extract import extract_service as extract_service_mod
from app.services.ai_extract.extract_service import AIExtractService
from tests._pg_fixture import blank_session, unique_code

SORENTO = "00000000-0000-0000-0000-000000000001"
MOCHA = "00000000-0000-0000-0000-000000000002"


@pytest.fixture
def db():
    with blank_session() as session:
        yield session


def _uid() -> str:
    return str(uuid.uuid4())


def _mocha(db) -> str:
    if db.query(Company).filter(Company.id == MOCHA).first() is None:
        db.add(
            Company(id=MOCHA, name="ZZT Mocha", code=unique_code("MCH")[:20], is_active=True)
        )
        db.flush()
    return MOCHA


def _product(db, code: str, *, company_id: str = SORENTO, is_active: bool = True) -> Product:
    cat = ProductCategory(
        id=_uid(), category_code=unique_code("cat")[:50], category_name="ZZT cat"
    )
    uom = UnitOfMeasure(id=_uid(), uom_code=unique_code("uom")[:20], uom_name="Each")
    db.add_all([cat, uom])
    db.flush()
    row = Product(
        id=_uid(),
        company_id=company_id,
        product_code=code,
        product_name=f"ZZT {code}",
        category_id=cat.id,
        base_uom_id=uom.id,
        list_price=1,
        is_active=is_active,
    )
    db.add(row)
    db.flush()
    return row


def _parsed(*codes: str) -> dict:
    return {
        "products": [
            {"product_code": c, "product_name": "raw name", "quantity": 1} for c in codes
        ]
    }


def _extract(db, *codes: str):
    with company_scope(db, frozenset({SORENTO})):
        return AIExtractService(db)._extract_products(_parsed(*codes))


# The owner's real misreads: model read -> real catalogue code.
REAL_MISREADS = [
    pytest.param("SRTK1634SS-BL", "SRTKT1634SS-BL", id="dropped-T-blue"),
    pytest.param("SRTK1636SS-GM", "SRTKT1636SS-GM", id="dropped-T-gunmetal"),
    pytest.param("SRTK1632SS", "SRTKT1632SS", id="dropped-T-plain"),
    pytest.param("SRTKT1868-BL", "SRTKT1868SS-BL", id="dropped-SS"),
]

# The catalogue neighbours around them, so the right code has to WIN a ranking
# rather than be the only thing there.
NEIGHBOURS = [
    "SRTKT1634SS-BL",
    "SRTKT1634SS-GM",
    "SRTKT1634SS",
    "SRTKT1636SS-BL",
    "SRTKT1636SS-GM",
    "SRTKT1632SS",
    "SRTKT1632SS-BL",
    "SRTKT1868SS-BL",
    "SRTKT1868SS",
    "SRTKT1868SS-GM",
    "SRTKT1866SS-BL",
]


# AC-B1 + AC-B2: the real code is suggested first, and nothing is applied.
@pytest.mark.parametrize("read, real", REAL_MISREADS)
def test_misread_code_suggests_the_real_code_first_and_applies_nothing(db, read, real):
    by_code = {c: _product(db, c) for c in NEIGHBOURS}

    out = _extract(db, read)

    assert len(out) == 1
    line = out[0]
    assert line.match is None
    assert line.product_id is None
    assert line.product_set_id is None
    assert line.product_code == read
    assert line.suggestions, f"no suggestion for {read}"
    first = line.suggestions[0]
    assert first.product_code == real
    assert first.product_id == by_code[real].id
    assert first.product_name == by_code[real].product_name


# AC-B3: an exact match carries no suggestions.
def test_exact_match_carries_no_suggestions(db):
    for c in NEIGHBOURS:
        _product(db, c)

    out = _extract(db, "SRTKT1634SS-BL")

    assert out[0].match == "product"
    assert out[0].suggestions == []


# AC-B4: at most 3 suggestions, even when the resolver has more neighbours.
def test_at_most_three_suggestions(db):
    for c in NEIGHBOURS:
        _product(db, c)

    out = _extract(db, "SRTKT1868-BL")

    assert 1 <= len(out[0].suggestions) <= 3


# AC-B5: another company's product is never suggested.
def test_another_companys_product_is_not_suggested(db):
    _product(db, "SRTKT1632SS", company_id=_mocha(db))

    out = _extract(db, "SRTK1632SS")

    assert out[0].match is None
    assert out[0].suggestions == []


# AC-B6: an inactive product is never suggested; an active neighbour still is.
def test_inactive_product_is_not_suggested(db):
    _product(db, "SRTKT1632SS", is_active=False)
    active = _product(db, "SRTKT1632SS-BL")

    out = _extract(db, "SRTK1632SS")

    codes = [s.product_code for s in out[0].suggestions]
    assert "SRTKT1632SS" not in codes
    assert codes == [active.product_code]


# AC-B6b (crew audit): the active filter runs BEFORE the resolver's cap of 5, so
# inactive near-matches cannot push an active one out. Five inactive prefix
# extensions of the misread code rank first in the trigram pass (`is_variant`);
# capped first, they would fill all 5 slots and leave nothing to suggest.
def test_inactive_near_matches_do_not_crowd_out_an_active_one(db):
    for suffix in "ABCDE":
        _product(db, f"SRTK1632SS-{suffix}", is_active=False)
    real = _product(db, "SRTKT1632SS")

    out = _extract(db, "SRTK1632SS")

    assert [s.product_code for s in out[0].suggestions][:1] == [real.product_code]


# AC-B7: nothing near enough -> no suggestions, plain miss.
def test_no_neighbour_means_no_suggestions(db):
    _product(db, "SRTKT1632SS")

    out = _extract(db, "ZZQWXY-0000")

    assert out[0].match is None
    assert out[0].suggestions == []


# AC-B8: a resolver failure still answers every line as a plain miss.
def test_resolver_failure_answers_plain_miss_with_no_suggestions(db, monkeypatch):
    def _boom(*_a, **_k):
        raise RuntimeError("resolver down")

    monkeypatch.setattr(extract_service_mod, "resolve_references", _boom)

    out = _extract(db, "SRTK1632SS")

    assert len(out) == 1
    assert out[0].match is None
    assert out[0].suggestions == []


# The public route's `response_model=ExtractResult` keeps the field (a field the
# model does not declare is dropped from the response in silence).
def test_suggestions_survive_the_route_response_model():
    from app.services.ai_extract.extract_service import (
        ExtractedProductLine,
        ExtractedProductSuggestion,
        ExtractResult,
    )

    line = ExtractedProductLine(
        product_code="SRTK1632SS",
        suggestions=[
            ExtractedProductSuggestion(
                product_id="p1", product_code="SRTKT1632SS", product_name="ZZT"
            )
        ],
    )
    dumped = ExtractResult(values={}, products=[line]).model_dump(mode="json")
    assert dumped["products"][0]["suggestions"] == [
        {"product_id": "p1", "product_code": "SRTKT1632SS", "product_name": "ZZT"}
    ]
