"""Portal design preview real doc route (D11, PLAN-price-tag-r7-request-ux
AC-S4-3).

``GET /api/v1/public/portal/submissions/price_tag_request/{id}/design``
does not exist yet - every route test in this file 404s on a route FastAPI
has never heard of until the coder adds it. Contract: same payload shape as
the CRM design endpoint (``app/api/v1/dealer_kit/price_tag_requests.py``'s
``get_tag_sheet_design``, ~line 332) plus a ``lines`` key carrying
``tag_data_service.resolve_request_line_data`` rows - the same resolver
``PriceTagRequestService.response_with_resolved_lines`` already calls for
the ordinary detail body, so a line's code/name/prices can never disagree
between the two screens.

Owner-gated (``_require_own_request``, same as every other portal price tag
route) and status-gated to ``proof_ready | changes_requested | approved |
ready_for_collection | collected`` - r9 D8 retired ``ready`` and put the two
collection statuses in its place, so a design stays readable right through the
hand-over. 404 everywhere else (``new``, ``designing``, and a portal DRAFT,
whose status is also ``new`` but is never a status this route allows
regardless), so a design that is still being worked on never leaks to the
portal before marketing means it to.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

# MUST be first app import - resolves the circular import in app.modules.runtime.guards
from app.main import app  # noqa: E402
from tests._pg_fixture import blank_session, unique_code
from tests import _ptag_r9_seed

_SORENTO_COMPANY_ID = "00000000-0000-0000-0000-000000000001"
_DESIGN_URL = "/api/v1/public/portal/submissions/price_tag_request/{id}/design"


def _seed_contact_who_can_see_the_form(db: Session) -> str:
    from app.models.access import RespondContact
    from tests._portal_grant import link_contact_segment, seed_segment

    contact = RespondContact(
        id=str(uuid.uuid4()),
        phone_number=f"+60{uuid.uuid4().hex[:9]}",
        name=unique_code("contact"),
    )
    db.add(contact)
    db.flush()
    segment = seed_segment(db, kinds=["price_tag_request"])
    link_contact_segment(db, contact.id, segment.code)
    return contact.id


def _seed_product(db: Session) -> str:
    from app.models.product import Brand, Product, ProductCategory, UnitOfMeasure

    category = ProductCategory(
        id=str(uuid.uuid4()),
        category_code=unique_code("cat"),
        category_name=unique_code("Category"),
    )
    brand = Brand(id=str(uuid.uuid4()), brand_code=unique_code("br"), brand_name=unique_code("Brand"))
    uom = UnitOfMeasure(id=str(uuid.uuid4()), uom_code=unique_code("uom"), uom_name="Each")
    db.add_all([category, brand, uom])
    db.flush()
    product = Product(
        id=str(uuid.uuid4()),
        product_code=unique_code("prod"),
        product_name=unique_code("Product"),
        category_id=category.id,
        brand_id=brand.id,
        base_uom_id=uom.id,
        list_price=100.00,
    )
    db.add(product)
    db.flush()
    return product.id


def _request_with_a_design(
    db: Session,
    contact_id: str,
    *,
    status: str,
    portal_draft: bool = False,
) -> str:
    """A submitted request with a tag_sheet page + one saved version, at the
    given status. Returns the request id.
    """
    from app.models.dealer_kit import Page, PageVersion
    from app.services.price_tag_request_service import PriceTagRequestService

    product_id = _seed_product(db)
    req = PriceTagRequestService.create_request(
        db,
        contact_id=contact_id,
        company_id=_SORENTO_COMPANY_ID,
        data={
            "debtor_name": "ZZT Dealer",
            "needed_by_date": date.today() + timedelta(days=7),
            "lines": [{"line_type": "product", "product_id": product_id}],
        },
    )
    req.portal_draft_at = None if not portal_draft else req.portal_draft_at

    page = Page(
        name=f"ZZT Tags - {req.doc_number}",
        slug=unique_code("tag-sheet"),
        kind="tag_sheet",
        request_id=req.id,
        company_id=_SORENTO_COMPANY_ID,
    )
    db.add(page)
    db.flush()
    req.page_id = page.id

    db.add(
        PageVersion(
            page_id=page.id,
            version=1,
            doc={"kind": "tag_sheet", "sheets": [], "imposition": {}},
        )
    )
    req.status = status
    db.flush()
    db.commit()
    return req.id


@pytest.fixture
def client():
    from app.api.v1.public.portal import get_portal_token
    from app.database import get_db
    from app.models.portal import PortalToken

    with blank_session() as db:
        contact_id = _seed_contact_who_can_see_the_form(db)

        def _override_get_db():
            yield db

        def _override_portal_token():
            return PortalToken(id=str(uuid.uuid4()), contact_id=contact_id, space_id="zzt-space")

        app.dependency_overrides[get_db] = _override_get_db
        app.dependency_overrides[get_portal_token] = _override_portal_token
        try:
            with TestClient(app, headers={"X-Portal-Token": "zzt-token"}) as c:
                yield c, db, contact_id
        finally:
            app.dependency_overrides.clear()


@pytest.mark.parametrize(
    "status",
    ["proof_ready", "changes_requested", "approved", "ready_for_collection", "collected"],
)
class TestAllowedStatuses:
    def test_the_owning_contact_gets_200_with_the_expected_keys(self, client, status):
        c, db, contact_id = client
        request_id = _request_with_a_design(db, contact_id, status=status)

        res = c.get(_DESIGN_URL.format(id=request_id))

        assert res.status_code == 200, res.text
        body = res.json()
        for key in ("page_id", "version", "doc", "source", "lines"):
            assert key in body, f"missing {key!r} in {body!r}"
        assert body["version"] == 1
        assert isinstance(body["lines"], list)

    def test_the_lines_carry_resolved_display_data(self, client, status):
        """Same shape `tag_data_service.resolve_request_line_data` rows -
        code/name resolved, not a bare product id (ADR: no UUIDs to the FE)."""
        c, db, contact_id = client
        request_id = _request_with_a_design(db, contact_id, status=status)

        res = c.get(_DESIGN_URL.format(id=request_id))

        assert res.status_code == 200, res.text
        lines = res.json()["lines"]
        assert len(lines) == 1
        assert lines[0]["code"]
        assert lines[0]["name"]


class TestReadsUnderTheRequestsOwnCompany:
    def test_a_contact_retagged_to_another_company_still_sees_the_design(self, client):
        """PRICE-TAG-COMPANY: the design of an existing request scopes to the company
        the request was stamped with, not the contact's company today. Re-tag the
        contact into another company after a Sorento request exists: the design and
        its Sorento line still resolve."""
        c, db, contact_id = client
        from app.models.company import Company
        from tests._portal_grant import link_contact_company

        request_id = _request_with_a_design(db, contact_id, status="proof_ready")
        other = Company(
            id=str(uuid.uuid4()), name="ZZT Other Co", code=unique_code("co")[:20]
        )
        db.add(other)
        db.flush()
        link_contact_company(db, contact_id, other.id)
        db.commit()

        res = c.get(_DESIGN_URL.format(id=request_id))

        assert res.status_code == 200, res.text
        lines = res.json()["lines"]
        assert len(lines) == 1
        assert lines[0]["code"]


class TestPrefersTheSavedVersionOverTheDraft:
    def test_a_differing_draft_does_not_win_over_the_saved_version(self, client):
        """Review fix (D11 follow-up): the portal must show what was
        deliberately SAVED (and sent to the salesperson for review), never
        marketing's live in-progress autosave - the CRM designer stays
        draft-first (``get_tag_sheet_design``), this route does not."""
        c, db, contact_id = client
        from app.models.dealer_kit import Page
        from app.models.price_tag import PriceTagRequest

        request_id = _request_with_a_design(db, contact_id, status="proof_ready")
        req = db.query(PriceTagRequest).filter(PriceTagRequest.id == request_id).first()
        page = db.query(Page).filter(Page.id == req.page_id).first()
        page.draft_doc = {
            "kind": "tag_sheet",
            "sheets": [{"id": "zzt-unsaved-draft-sheet"}],
            "imposition": {},
        }
        db.commit()

        res = c.get(_DESIGN_URL.format(id=request_id))

        assert res.status_code == 200, res.text
        body = res.json()
        assert body["source"] == "version"
        assert body["doc"]["sheets"] == []


@pytest.mark.parametrize("status", ["new", "designing"])
class TestDisallowedStatuses:
    def test_404_before_a_design_is_ready_to_be_seen(self, client, status):
        c, db, contact_id = client
        request_id = _request_with_a_design(db, contact_id, status=status)

        res = c.get(_DESIGN_URL.format(id=request_id))

        assert res.status_code == 404, res.text


class TestPortalDraft:
    def test_a_portal_draft_404s_even_if_someone_forced_its_status(self, client):
        """A draft's status is `new` regardless - already covered by
        ``TestDisallowedStatuses`` - but this pins the OWN reason: a draft
        must never leak a design regardless of what status ends up on it."""
        c, db, contact_id = client
        request_id = _request_with_a_design(
            db, contact_id, status="proof_ready", portal_draft=True
        )
        from app.models.price_tag import PriceTagRequest

        row = db.query(PriceTagRequest).filter(PriceTagRequest.id == request_id).first()
        from datetime import datetime

        row.portal_draft_at = datetime.utcnow()
        db.commit()

        res = c.get(_DESIGN_URL.format(id=request_id))

        assert res.status_code == 404, res.text


class TestOwnership:
    def test_another_contacts_request_404s(self, client):
        c, db, _contact_id = client
        other_contact_id = _seed_contact_who_can_see_the_form(db)
        request_id = _request_with_a_design(db, other_contact_id, status="proof_ready")

        res = c.get(_DESIGN_URL.format(id=request_id))

        assert res.status_code == 404, res.text


class TestMalformedId:
    def test_a_non_uuid_id_404s_rather_than_500s(self, client):
        c, _db, _contact_id = client

        res = c.get(_DESIGN_URL.format(id="not-a-uuid"))

        assert res.status_code == 404, res.text


@pytest.fixture(autouse=True)
def no_respond(monkeypatch):
    """S8: no test run reaches api.respond.io. See `_ptag_r9_seed.block_respond`.

    Every transition here goes through the real notifier, which sends over the
    network unless something stops it - the run log used to carry a live
    ``Window check: Respond.io list_messages failed`` per transition.
    """
    return _ptag_r9_seed.block_respond(monkeypatch)
