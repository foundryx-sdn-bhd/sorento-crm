"""PRICE-TAG-COMPANY: the price tag portal reads the CONTACT'S company, never "the first
active company" (prod report: a Sorento salesman's line search and AI Extract ran
against Mocha's catalogue).

``_resolve_company`` used ``db.query(Company).filter(Company.is_active).first()`` - a
``LIMIT 1`` with no ``ORDER BY``, so Postgres's physical row order picked the company.
With two active companies that is whichever heap tuple comes first. These tests pin that
order instead of producing it: an unordered, LIMITed ORM read of ``companies`` on the test
session is given ``ORDER BY id <> mocha``, so it hands back Mocha exactly as the prod heap
did. Producing the order physically (an UPDATE of the Sorento row so its new tuple lands
behind Mocha's) flaked under xdist: the dead tuples and free space earlier tests leave in
the table decide where the new tuples go (release run 38071049191, shard 5).

Resolution rule (crew answer, mirrors ``company_routing_service.py``'s "never an
arbitrary pick"): the contact's active ``respond_contact_companies``; one company is
that company; several including the incumbent default is the default; several without
it is a 409; none is a 422.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select

# MUST be first app import - resolves the circular import in app.modules.runtime.guards
from app.main import app  # noqa: E402
from tests._pg_fixture import blank_session, unique_code
from tests._portal_grant import grant_portal_forms

_SORENTO_COMPANY_ID = "00000000-0000-0000-0000-000000000001"
_LOOKUP = "/api/v1/public/portal/lookups/price-tag-items"
_EXTRACT = "/api/v1/public/portal/ai-extract"
_TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0"
    b"\x00\x00\x03\x01\x01\x00\xc9\xfe\x92\xef\x00\x00\x00\x00IEND\xaeB`\x82"
)


@pytest.fixture
def api():
    from app.database import get_db

    with blank_session() as db:

        def _override_get_db():
            yield db

        app.dependency_overrides[get_db] = _override_get_db
        try:
            yield db
        finally:
            app.dependency_overrides.clear()


def _company(db, *, name: str, is_active: bool = True) -> str:
    from app.models.company import Company

    company = Company(
        id=str(uuid.uuid4()),
        name=name,
        code=f"ZZT{uuid.uuid4().hex[:6].upper()}",
        is_active=is_active,
    )
    db.add(company)
    db.flush()
    return company.id


def _mocha_ahead_of_sorento(db) -> str:
    """A second active company that an unordered "first active company" read returns."""
    from app.models.company import Company

    mocha_id = _company(db, name="ZZT Mocha")

    @event.listens_for(db, "do_orm_execute")
    def _mocha_scans_first(state):
        stmt = state.statement
        if (
            state.is_select
            and stmt.column_descriptions
            and stmt.column_descriptions[0].get("entity") is Company
            and stmt._limit_clause is not None
            and not stmt._order_by_clauses
        ):
            state.statement = stmt.order_by(Company.id != mocha_id)

    first = db.query(Company).filter(Company.is_active).first()
    assert first is not None and str(first.id) == mocha_id, (
        "precondition: the non-Sorento row must scan first"
    )
    first = db.execute(select(Company.id).where(Company.is_active).limit(1)).scalar()
    assert str(first) == mocha_id, "precondition: the non-Sorento row must scan first"
    return mocha_id


def _contact(db, *company_ids: str) -> str:
    from app.models.access import RespondContact
    from app.models.company import RespondContactCompany

    contact = RespondContact(
        id=str(uuid.uuid4()),
        phone_number=f"+6011{uuid.uuid4().hex[:8]}",
        name=unique_code("ptag-company"),
    )
    db.add(contact)
    db.flush()
    for company_id in company_ids:
        db.add(RespondContactCompany(respond_contact_id=contact.id, company_id=company_id))
    db.flush()
    grant_portal_forms(db, contact.id, ["price_tag_request"])
    return contact.id


def _token(db, contact_id: str) -> str:
    from app.models.portal import PortalToken

    token = PortalToken(
        id=str(uuid.uuid4()),
        token=f"ZZT-tok-{uuid.uuid4().hex}",
        contact_id=contact_id,
        space_id="ZZT-ptag-company-space",
        expires_at=datetime.utcnow() + timedelta(days=30),
        verified_at=datetime.utcnow() - timedelta(minutes=5),
    )
    db.add(token)
    db.commit()
    return token.token


def _product(db, *, company_id: str, code: str) -> None:
    from app.models.base import company_scope
    from app.models.product import Brand, Product, ProductCategory, UnitOfMeasure

    with company_scope(db, frozenset({company_id})):
        category = ProductCategory(
            id=str(uuid.uuid4()),
            category_code=unique_code("cat"),
            category_name=unique_code("Category"),
            company_id=company_id,
        )
        brand = Brand(
            id=str(uuid.uuid4()),
            brand_code=unique_code("br"),
            brand_name=unique_code("Brand"),
            company_id=company_id,
        )
        uom = UnitOfMeasure(
            id=str(uuid.uuid4()), uom_code=unique_code("uom"), uom_name="Each"
        )
        db.add_all([category, brand, uom])
        db.flush()
        db.add(
            Product(
                id=str(uuid.uuid4()),
                product_code=code,
                product_name=f"{code} name",
                category_id=category.id,
                brand_id=brand.id,
                base_uom_id=uom.id,
                list_price=100.00,
                company_id=company_id,
            )
        )
        db.flush()


def _search(token: str, q: str):
    with TestClient(app) as c:
        return c.get(_LOOKUP, params={"q": q}, headers={"X-Portal-Token": token})


# ---------------------------------------------------------------------------
# The reported bug, through the routes the salesman used
# ---------------------------------------------------------------------------


def test_line_search_finds_the_sorento_code_when_mocha_scans_first(api):
    db = api
    _mocha_ahead_of_sorento(db)
    stem = unique_code("SRTWT106")
    _product(db, company_id=_SORENTO_COMPANY_ID, code=stem)
    token = _token(db, _contact(db, _SORENTO_COMPANY_ID))

    response = _search(token, stem)

    assert response.status_code == 200, response.text
    assert [row["code"] for row in response.json()] == [stem]


def test_line_search_never_shows_the_other_companys_catalogue(api):
    db = api
    mocha_id = _mocha_ahead_of_sorento(db)
    stem = unique_code("ZZTDIY")
    _product(db, company_id=_SORENTO_COMPANY_ID, code=f"{stem}-SRT")
    _product(db, company_id=mocha_id, code=f"{stem}-MCH")
    token = _token(db, _contact(db, _SORENTO_COMPANY_ID))

    response = _search(token, stem)

    assert response.status_code == 200, response.text
    assert [row["code"] for row in response.json()] == [f"{stem}-SRT"]


def test_ai_extract_runs_under_the_contacts_company(api, monkeypatch):
    from app.api.v1.public import ai_extract as route_module
    from app.models.base import get_company_scope
    from app.services.ai_extract.extract_service import ExtractResult

    db = api
    _mocha_ahead_of_sorento(db)
    token = _token(db, _contact(db, _SORENTO_COMPANY_ID))
    seen: list = []

    def _fake_extract(self, *a, **k):
        seen.append(get_company_scope(self.db))
        return ExtractResult(values={})

    monkeypatch.setattr(route_module.AIExtractService, "extract", _fake_extract)

    with TestClient(app) as c:
        response = c.post(
            _EXTRACT,
            data={"form_key": "portal.price_tag_request"},
            files=[("files", ("zzt.png", _TINY_PNG, "image/png"))],
            headers={"X-Portal-Token": token},
        )

    assert response.status_code == 200, response.text
    assert seen == [frozenset({_SORENTO_COMPANY_ID})]


# ---------------------------------------------------------------------------
# One test per resolution branch
# ---------------------------------------------------------------------------


def test_a_single_company_contact_resolves_to_that_company(api):
    db = api
    mocha_id = _mocha_ahead_of_sorento(db)
    stem = unique_code("ZZTMCH")
    _product(db, company_id=mocha_id, code=stem)
    token = _token(db, _contact(db, mocha_id))

    response = _search(token, stem)

    assert response.status_code == 200, response.text
    assert [row["code"] for row in response.json()] == [stem]


def test_a_contact_in_both_companies_resolves_to_the_default(api):
    db = api
    mocha_id = _mocha_ahead_of_sorento(db)
    stem = unique_code("ZZTBOTH")
    _product(db, company_id=_SORENTO_COMPANY_ID, code=f"{stem}-SRT")
    _product(db, company_id=mocha_id, code=f"{stem}-MCH")
    token = _token(db, _contact(db, mocha_id, _SORENTO_COMPANY_ID))

    response = _search(token, stem)

    assert response.status_code == 200, response.text
    assert [row["code"] for row in response.json()] == [f"{stem}-SRT"]


def test_several_companies_without_the_default_is_a_clear_409(api):
    db = api
    mocha_id = _mocha_ahead_of_sorento(db)
    third_id = _company(db, name="ZZT Third")
    token = _token(db, _contact(db, mocha_id, third_id))

    response = _search(token, "anything")

    assert response.status_code == 409, response.text
    body = response.json()
    assert body.get("code") == "CONTACT_COMPANY_AMBIGUOUS", body
    assert "more than one company" in body.get("message", ""), body


def test_a_contact_with_no_company_is_a_clear_422_not_the_first_company(api):
    db = api
    _mocha_ahead_of_sorento(db)
    token = _token(db, _contact(db))

    response = _search(token, "anything")

    assert response.status_code == 422, response.text
    body = response.json()
    assert body.get("code") == "CONTACT_HAS_NO_COMPANY", body
    assert "not linked to a company" in body.get("message", ""), body


def test_an_inactive_company_membership_does_not_count(api):
    db = api
    _mocha_ahead_of_sorento(db)
    retired_id = _company(db, name="ZZT Retired", is_active=False)
    token = _token(db, _contact(db, retired_id))

    response = _search(token, "anything")

    assert response.status_code == 422, response.text
    assert response.json().get("code") == "CONTACT_HAS_NO_COMPANY"
