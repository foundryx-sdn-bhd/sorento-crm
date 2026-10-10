"""Red tests for PLAN-portal-price-tag-journey-r8 Round 3, R3-1 (AC-R1).

R3-1 REVERSES S8's D-P6: a submitted price tag request is read-only exactly
like a stock inquiry. ``PUT /api/v1/public/portal/submissions/price_tag_request/{id}``
now succeeds ONLY when ``portal_draft_at`` is set - ``_require_editable`` goes
back to ``_require_draft`` (409 ``NOT_DRAFT`` for every non-draft status,
``new`` and ``changes_requested`` included). Post-submit changes go through the
revision engine instead (``tests/test_portal_price_tag_revise.py``, AC-R2/R3),
which is why the audit-row-shape, zero-lines-422, set-guard-422 and
marketing-override-carry assertions that used to live here (S8) now live
there as part of the revise transaction, not the PUT route.

``is_editable`` (AC-B6, now AC-R1) mirrors the same reversal: True for a draft
only, False for every non-draft status including ``new`` / ``changes_requested``.

Fixtures modelled on ``tests/test_portal_price_tag_routes.py``: Postgres via
``tests/_pg_fixture.py::blank_session``, own seeded contact/product/promotion/
user chain, never borrowed rows.
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

_BASE = "/api/v1/public/portal/submissions/price_tag_request"
_SORENTO_COMPANY_ID = "00000000-0000-0000-0000-000000000001"

_ALL_NON_DRAFT_STATUSES = [
    "new",
    "changes_requested",
    "designing",
    "proof_ready",
    "approved",
    "ready",
    "void",
]


def _seed_contact_who_can_see_the_form(db: Session) -> str:
    from app.models.access import RespondContact
    from tests._portal_grant import link_contact_company, link_contact_segment, seed_segment

    contact = RespondContact(
        id=str(uuid.uuid4()),
        phone_number=f"+60{uuid.uuid4().hex[:9]}",
        name=unique_code("contact"),
    )
    db.add(contact)
    db.flush()
    segment = seed_segment(db, kinds=["price_tag_request"])
    link_contact_segment(db, contact.id, segment.code)
    link_contact_company(db, contact.id)
    return contact.id


def _seed_product(db: Session, *, class_label: str = "Kitchen Sink") -> str:
    from app.models.product import Brand, Product, ProductCategory, UnitOfMeasure

    category = ProductCategory(
        id=str(uuid.uuid4()),
        category_code=unique_code("cat"),
        category_name=unique_code("Category"),
        class_label=class_label,
    )
    brand = Brand(
        id=str(uuid.uuid4()),
        brand_code=unique_code("br"),
        brand_name=unique_code("Brand"),
    )
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


def _seed_user(db: Session) -> str:
    """A minimal designer/staff user, for ``assigned_to_id`` (FK to users.id)."""
    from app.models.user import User, UserStatus

    user = User(
        id=str(uuid.uuid4()),
        email=f"{unique_code('user')}@example.com",
        name=unique_code("Designer"),
        status=UserStatus.ACTIVE.value,
    )
    db.add(user)
    db.flush()
    return user.id


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
            return PortalToken(
                id=str(uuid.uuid4()),
                contact_id=contact_id,
                space_id="zzt-space",
            )

        app.dependency_overrides[get_db] = _override_get_db
        app.dependency_overrides[get_portal_token] = _override_portal_token
        try:
            with TestClient(app, headers={"X-Portal-Token": "zzt-token"}) as c:
                yield c, db, contact_id
        finally:
            app.dependency_overrides.clear()


def _create_draft(c, product_id: str, **extra) -> dict:
    payload = {
        "lines": [{"line_type": "product", "product_id": product_id}],
        # r9 D7: every draft here goes on to be submitted, and submit refuses
        # without a print choice. A test about the print choice itself passes
        # its own value through `extra`.
        "print_by": "office",
    }
    payload.update(extra)
    res = c.post(_BASE, json=payload)
    assert res.status_code == 201, res.text
    return res.json()


def _force_status(
    db: Session,
    request_id: str,
    *,
    status: str,
    portal_draft_at=None,
    assigned_to_id: str | None = None,
):
    """Move a request straight to ``status`` without going through the whole
    submit/claim/transition pipeline - only the state this suite cares about,
    set directly on the row, then flushed so the API's next call sees it."""
    from app.services.price_tag_request_service import PriceTagRequestService

    req = PriceTagRequestService.get_request(db, request_id)
    req.status = status
    req.portal_draft_at = portal_draft_at
    if assigned_to_id is not None:
        req.assigned_to_id = assigned_to_id
    db.flush()
    return req


# ---------------------------------------------------------------------------
# AC-R1: PUT is refused (409 NOT_DRAFT) for EVERY non-draft status, including
# new / changes_requested - the S8 post-submit-edit window is gone.
# ---------------------------------------------------------------------------


class TestPutRefusedOutsideDraft:
    @pytest.mark.parametrize("status_value", _ALL_NON_DRAFT_STATUSES)
    def test_put_non_draft_409_not_draft(self, client, status_value):
        c, db, _contact_id = client
        product_id = _seed_product(db)
        created = _create_draft(c, product_id, debtor_name="ZZT Original")
        _force_status(db, created["id"], status=status_value, portal_draft_at=None)

        res = c.put(
            f"{_BASE}/{created['id']}",
            json={"debtor_name": "ZZT Should Not Land"},
        )

        assert res.status_code == 409, res.text
        assert res.json()["code"] == "NOT_DRAFT"

        # Nothing changed: the field the PUT tried to set never landed.
        unchanged = c.get(f"{_BASE}/{created['id']}").json()
        assert unchanged["debtor_name"] == "ZZT Original"

    def test_put_new_keeps_assignee_and_status_untouched(self, client):
        """A refused PUT must not have side effects: status and assignee both
        stay exactly as ``_force_status`` left them."""
        c, db, _contact_id = client
        product_id = _seed_product(db)
        designer_id = _seed_user(db)
        created = _create_draft(c, product_id)
        _force_status(
            db, created["id"], status="new", portal_draft_at=None, assigned_to_id=designer_id,
        )

        res = c.put(f"{_BASE}/{created['id']}", json={"debtor_name": "ZZT Edited"})
        assert res.status_code == 409, res.text

        detail = c.get(f"{_BASE}/{created['id']}").json()
        assert detail["status"] == "new"
        assert detail["assigned_to_id"] == designer_id


# ---------------------------------------------------------------------------
# A draft PUT still works, unaffected by R3-1.
# ---------------------------------------------------------------------------


class TestDraftPutStillWorks:
    def test_put_draft_still_200(self, client):
        c, db, _contact_id = client
        product_id = _seed_product(db)
        created = _create_draft(c, product_id, debtor_name="ZZT Original")

        res = c.put(f"{_BASE}/{created['id']}", json={"debtor_name": "ZZT Draft Edit"})

        assert res.status_code == 200, res.text
        assert res.json()["debtor_name"] == "ZZT Draft Edit"


# ---------------------------------------------------------------------------
# AC-R1: detail is_editable - True for a draft only.
# ---------------------------------------------------------------------------


class TestIsEditableDraftOnly:
    def test_detail_is_editable_matrix(self, client):
        c, db, _contact_id = client
        product_id = _seed_product(db)

        draft = _create_draft(c, product_id)
        assert c.get(f"{_BASE}/{draft['id']}").json()["is_editable"] is True

        for locked_status in _ALL_NON_DRAFT_STATUSES:
            req_body = _create_draft(c, product_id)
            _force_status(db, req_body["id"], status=locked_status, portal_draft_at=None)
            body = c.get(f"{_BASE}/{req_body['id']}").json()
            assert body["is_editable"] is False, (locked_status, body)


# ---------------------------------------------------------------------------
# Ownership gate unchanged (unrelated to R3-1).
# ---------------------------------------------------------------------------


class TestOwnershipUnchanged:
    def test_put_other_contact_denied(self, client):
        c, db, _contact_id = client
        from app.services.price_tag_request_service import PriceTagRequestService

        other = _seed_contact_who_can_see_the_form(db)
        theirs = PriceTagRequestService.create_request(
            db,
            contact_id=other,
            company_id=_SORENTO_COMPANY_ID,
            data={"debtor_name": "ZZT Theirs"},
        )
        db.flush()

        res = c.put(
            f"{_BASE}/{theirs.id}",
            json={"debtor_name": "ZZT Should Not Land"},
        )

        assert res.status_code in (403, 404), res.text


# ---------------------------------------------------------------------------
# A second submit is still refused - unaffected by R3-1 (submit already used
# `_require_draft`; a PUT in between now never succeeds either way).
# ---------------------------------------------------------------------------


class TestSubmitTwiceStillRefused:
    def test_submit_twice_409_not_draft(self, client):
        c, db, _contact_id = client
        product_id = _seed_product(db)
        created = c.post(
            _BASE,
            json={
                "debtor_name": "ZZT Dealer",
                # r9 D7: no default, and submit refuses without it.
                "print_by": "office",
                "needed_by_date": str(date.today() + timedelta(days=7)),
                "lines": [{"line_type": "product", "product_id": product_id}],
            },
        ).json()
        first = c.post(f"{_BASE}/{created['id']}/submit")
        assert first.status_code == 200, first.text

        c.put(f"{_BASE}/{created['id']}", json={"debtor_name": "ZZT Edited"})

        second = c.post(f"{_BASE}/{created['id']}/submit")

        assert second.status_code == 409, second.text
        assert second.json()["code"] in ("NOT_DRAFT", "ALREADY_SUBMITTED")


# ---------------------------------------------------------------------------
# Selling with no promotion submits fine (r7's guard is retired) - unaffected
# by R3-1.
# ---------------------------------------------------------------------------


class TestSellingWithoutPromotionNowSubmits:
    def test_submit_selling_without_promotion_succeeds(self, client):
        """D-P2 owner ruling still holds: Selling with no promotion is a
        valid end state and submits fine. D3 (this lane) retires the OLD
        defect this test used to pin (every line printed SP even at plain
        list): with no covering promotion and no manual price, a line
        prints LP - `show_promo_price=False` - even in Selling mode
        (AC-S7-5)."""
        c, db, _contact_id = client
        first = _seed_product(db)
        second = _seed_product(db)
        created = c.post(
            _BASE,
            json={
                "debtor_name": "ZZT Dealer",
                # r9 D7: no default, and submit refuses without it.
                "print_by": "office",
                "needed_by_date": str(date.today() + timedelta(days=7)),
                "price_mode": "selling",
                "lines": [
                    {"line_type": "product", "product_id": first},
                    {"line_type": "product", "product_id": second},
                ],
            },
        ).json()
        assert created["lines"][0]["promotion_id"] is None

        res = c.post(f"{_BASE}/{created['id']}/submit")

        assert res.status_code == 200, res.text

        from app.models.price_tag import PriceTagRequestLine

        rows = (
            db.query(PriceTagRequestLine)
            .filter(PriceTagRequestLine.request_id == created["id"])
            .all()
        )
        assert len(rows) == 2
        assert all(row.show_promo_price is False for row in rows)


# ---------------------------------------------------------------------------
# Need-by is optional at submit - unaffected by R3-1.
# ---------------------------------------------------------------------------


class TestNeedByOptionalAtSubmit:
    def test_submit_without_needed_by_succeeds(self, client):
        c, db, _contact_id = client
        product_id = _seed_product(db)
        created = c.post(
            _BASE,
            json={
                "debtor_name": "ZZT Dealer",
                # r9 D7: no default, and submit refuses without it.
                "print_by": "office",
                "price_mode": "list",
                "lines": [{"line_type": "product", "product_id": product_id}],
            },
        ).json()
        assert created["needed_by_date"] is None

        res = c.post(f"{_BASE}/{created['id']}/submit")

        assert res.status_code == 200, res.text
        assert res.json()["status"] == "new"


# ---------------------------------------------------------------------------
# Promotion audience gate on a draft write - unaffected by R3-1 (a draft PUT
# still runs the same write path).
# ---------------------------------------------------------------------------


class TestPromotionAudienceGateOnWrite:
    def test_put_promotion_outside_audience_422(self, client):
        c, db, _contact_id = client
        product_id = _seed_product(db)

        from app.models.marketing import Promotion

        promo = Promotion(
            id=str(uuid.uuid4()),
            description="ZZT Dealer Only Promo",
            is_active=True,
            access_levels=["dealer"],
        )
        db.add(promo)
        db.flush()

        created = _create_draft(c, product_id)

        res = c.put(
            f"{_BASE}/{created['id']}",
            json={"price_mode": "selling", "promotion_id": promo.id},
        )

        assert res.status_code == 422, res.text


class TestDraftNeededByEmptyString:
    def test_submit_needed_by_empty_string_treated_as_null(self, client):
        """An empty-string ``needed_by_date`` from the form must be treated
        as "clear the field", not a malformed date. Today pydantic's
        ``Optional[date]`` rejects "" outright with a 422."""
        c, db, _contact_id = client
        product_id = _seed_product(db)
        created = _create_draft(c, product_id, debtor_name="ZZT Dealer")

        res = c.put(
            f"{_BASE}/{created['id']}",
            json={"needed_by_date": ""},
        )

        assert res.status_code == 200, res.text
        assert res.json()["needed_by_date"] is None

        submit_res = c.post(f"{_BASE}/{created['id']}/submit")
        assert submit_res.status_code == 200, submit_res.text


@pytest.fixture(autouse=True)
def no_respond(monkeypatch):
    """S8: no test run reaches api.respond.io. See `_ptag_r9_seed.block_respond`.

    Every transition here goes through the real notifier, which sends over the
    network unless something stops it - the run log used to carry a live
    ``Window check: Respond.io list_messages failed`` per transition.
    """
    return _ptag_r9_seed.block_respond(monkeypatch)
