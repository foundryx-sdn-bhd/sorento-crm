"""Line-level promotion/manual-price on the create, update and response schemas (S6, D1).

UAC: AC-S6-3, AC-S6-4, AC-S6-5. Written test-FIRST (PRINCIPLES.md Phase 2)
against the CURRENT schemas - `PriceTagRequestLineCreate` has no
`promotion_id`/`manual_sell_price` yet, `PriceTagRequestCreate.promotion_id`
is still a declared (accepted) field, and neither request model sets
`extra="forbid"` - so every assertion below is a genuine behaviour mismatch,
not a collection-time import error.

Two routes exercise the create/update halves:

* Portal `POST /api/v1/public/portal/submissions/price_tag_request` (create).
* CRM `PATCH /api/v1/dealer-kit/price-tag-requests/{id}` - the office update
  route (`PriceTagRequestOfficeUpdate`) is the only request-level PATCH the
  CRM has; once the header's `promotion_id` field is gone everywhere, a stray
  one sent here must 422 too, or the schema boundary is porous on one side.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

# MUST be first app import - resolves a circular import in app.modules.runtime.guards
from app.main import app  # noqa: E402

from tests._pg_fixture import blank_session, unique_code

SORENTO = "00000000-0000-0000-0000-000000000001"
_PORTAL_BASE = "/api/v1/public/portal/submissions/price_tag_request"
_CRM_BASE = "/api/v1/dealer-kit/price-tag-requests"


def _uid() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------


def _product(db, *, list_price=100.00):
    from app.models.product import Brand, Product, ProductCategory, UnitOfMeasure

    stem = unique_code("PTS")
    category = ProductCategory(
        id=_uid(), category_code=stem, category_name=f"ZZT cat {stem}"
    )
    brand = Brand(id=_uid(), brand_code=stem[:50], brand_name=f"ZZT {stem}")
    uom = UnitOfMeasure(id=_uid(), uom_code=stem[:20], uom_name="Each")
    db.add_all([category, brand, uom])
    db.flush()
    product = Product(
        id=_uid(),
        company_id=SORENTO,
        product_code=stem,
        product_name=f"ZZT {stem}",
        category_id=category.id,
        brand_id=brand.id,
        base_uom_id=uom.id,
        list_price=list_price,
        is_active=True,
    )
    db.add(product)
    db.flush()
    return product


def _promotion(db, product, *, offer=400.00, access_levels=None, end=None):
    from app.models.marketing import Promotion, PromotionGroup, PromotionProduct

    promotion = Promotion(
        id=_uid(),
        description=unique_code("ZZT promo"),
        start_date=date.today() - timedelta(days=1),
        end_date=end if end is not None else date.today() + timedelta(days=30),
        is_active=True,
        access_levels=access_levels or ["dealer", "end_user"],
        company_id=SORENTO,
    )
    db.add(promotion)
    db.flush()
    group = PromotionGroup(promotion_id=promotion.id, group_name="ZZT group", sort_order=0)
    db.add(group)
    db.flush()
    db.add(
        PromotionProduct(
            id=_uid(),
            promotion_id=promotion.id,
            promotion_group_id=str(group.id),
            product_id=product.id,
            promo_selling_price=offer,
            company_id=SORENTO,
        )
    )
    db.flush()
    return promotion


@pytest.fixture
def portal_client():
    from app.api.v1.public.portal import get_portal_token
    from app.database import get_db
    from app.models.access import RespondContact
    from app.models.portal import PortalToken
    from tests._portal_grant import link_contact_company, link_contact_segment, seed_segment

    with blank_session() as db:
        contact = RespondContact(
            id=_uid(), phone_number=f"+60{uuid.uuid4().hex[:9]}", name=unique_code("contact")
        )
        db.add(contact)
        db.flush()
        segment = seed_segment(db, kinds=["price_tag_request"])
        link_contact_segment(db, contact.id, segment.code)
        link_contact_company(db, contact.id)

        def _override_get_db():
            yield db

        def _override_portal_token():
            return PortalToken(id=_uid(), contact_id=contact.id, space_id="zzt-space")

        app.dependency_overrides[get_db] = _override_get_db
        app.dependency_overrides[get_portal_token] = _override_portal_token
        try:
            with TestClient(app, headers={"X-Portal-Token": "zzt-token"}) as client:
                yield client, db, contact.id
        finally:
            app.dependency_overrides.clear()


@pytest.fixture
def crm_client():
    from app.dependencies import get_current_user, get_current_user_or_api_key, get_db
    from app.models.base import set_company_scope
    from app.models.user import (
        User,
        UserPermission,
        UserRole,
        UserRoleAssignment,
        UserRolePermission,
    )
    from app.services.company_scope_resolver import apply_company_scope

    with blank_session() as db:
        role_id, user_id = _uid(), _uid()
        db.add(
            UserRole(
                id=role_id,
                slug=unique_code("role").lower(),
                name="ZZT Schema Role",
                is_protected=False,
                is_default=False,
            )
        )
        db.add(
            User(
                id=user_id,
                email=f"{unique_code('u').lower()}@test.com",
                name="ZZT Schema User",
                status="ACTIVE",
            )
        )
        db.flush()
        db.add(UserRoleAssignment(user_id=user_id, role_id=role_id))
        for slug in (
            "dealer_kit.price_tag_requests.view",
            "dealer_kit.price_tag_requests.process",
        ):
            perm_id = str(uuid.uuid4())
            db.add(UserPermission(id=perm_id, slug=slug, name=slug, description=""))
            db.flush()
            db.add(
                UserRolePermission(id=str(uuid.uuid4()), role_id=role_id, permission_id=perm_id)
            )
        db.commit()

        def _override_get_db():
            yield db

        app.dependency_overrides[get_db] = _override_get_db

        async def _override_scope():
            scope = frozenset({SORENTO})
            set_company_scope(db, scope)
            return scope

        app.dependency_overrides[apply_company_scope] = _override_scope
        principal = {"id": user_id, "email": "zzt-schema@test.com"}
        app.dependency_overrides[get_current_user] = lambda: principal
        app.dependency_overrides[get_current_user_or_api_key] = lambda: principal

        with TestClient(app) as client:
            yield client, db
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------- AC-S6-3


def _grant_audience_code(db, contact_id: str, code: str = "dealer") -> None:
    from app.models.access import ContactAccessType, respond_contact_access_types

    if db.query(ContactAccessType).filter(ContactAccessType.code == code).first() is None:
        db.add(ContactAccessType(code=code, name=code))
        db.flush()
    db.execute(
        respond_contact_access_types.insert().values(
            contact_id=contact_id, access_type_code=code
        )
    )
    db.flush()


def test_portal_create_rejects_a_header_level_promotion_id(portal_client):
    """A REAL, contact-visible promotion, or `validate_promotion_access` 422s
    for the wrong reason (promotion not entitled) and this test is red for
    nothing this slice is about."""
    client, db, contact_id = portal_client
    product = _product(db)
    _grant_audience_code(db, contact_id, "dealer")
    promotion = _promotion(db, product, access_levels=["dealer", "end_user"])

    res = client.post(
        _PORTAL_BASE,
        json={
            "debtor_name": "ZZT Dealer",
            "promotion_id": promotion.id,
            "lines": [{"line_type": "product", "product_id": product.id}],
        },
    )
    assert res.status_code == 422, res.text


def test_crm_office_patch_rejects_a_promotion_id(crm_client):
    from app.services.price_tag_request_service import PriceTagRequestService

    client, db = crm_client
    from app.models.access import RespondContact

    contact = RespondContact(
        id=_uid(), phone_number=f"+60{uuid.uuid4().hex[:9]}", name=unique_code("contact")
    )
    db.add(contact)
    db.flush()
    product = _product(db)
    request = PriceTagRequestService.create_request(
        db,
        contact_id=contact.id,
        company_id=SORENTO,
        data={
            "debtor_name": "ZZT Dealer",
            "lines": [{"line_type": "product", "product_id": product.id}],
        },
    )
    request.portal_draft_at = None
    db.commit()

    res = client.patch(
        f"{_CRM_BASE}/{request.id}", json={"promotion_id": str(uuid.uuid4())}
    )
    assert res.status_code == 422, res.text


def test_line_carries_promotion_and_prices_in_response(portal_client):
    """`response_model` drops an undeclared field silently - assert each one."""
    client, db, contact_id = portal_client
    product = _product(db)
    _grant_audience_code(db, contact_id, "dealer")
    promotion = _promotion(db, product)

    res = client.post(
        _PORTAL_BASE,
        json={
            "debtor_name": "ZZT Dealer",
            "price_mode": "selling",
            "lines": [
                {
                    "line_type": "product",
                    "product_id": product.id,
                    "promotion_id": promotion.id,
                }
            ],
        },
    )
    assert res.status_code == 201, res.text
    line = res.json()["lines"][0]
    for field in (
        "promotion_id",
        "promotion_name",
        "manual_sell_price",
        "list_price",
        "sell_price",
        "sell_price_basis",
    ):
        assert field in line, f"{field} missing from the line response"
    assert line["promotion_id"] == promotion.id


# --------------------------------------------------------------------------- AC-S6-4


def test_manual_with_a_promotion_is_422(portal_client):
    client, db, _contact_id = portal_client
    product = _product(db)
    promotion = _promotion(db, product)

    res = client.post(
        _PORTAL_BASE,
        json={
            "debtor_name": "ZZT Dealer",
            "price_mode": "selling",
            "lines": [
                {
                    "line_type": "product",
                    "product_id": product.id,
                    "promotion_id": promotion.id,
                    "manual_sell_price": 123.45,
                }
            ],
        },
    )
    assert res.status_code == 422, res.text


def test_manual_in_list_mode_is_422(portal_client):
    client, db, _contact_id = portal_client
    product = _product(db)

    res = client.post(
        _PORTAL_BASE,
        json={
            "debtor_name": "ZZT Dealer",
            "price_mode": "list",
            "lines": [
                {
                    "line_type": "product",
                    "product_id": product.id,
                    "manual_sell_price": 123.45,
                }
            ],
        },
    )
    assert res.status_code == 422, res.text


# --------------------------------------------------------------------------- AC-S6-5


def test_line_promotion_must_cover_the_line(portal_client):
    client, db, _contact_id = portal_client
    covered_product = _product(db)
    other_product = _product(db)
    # A promotion that covers a DIFFERENT product, not this line's.
    non_covering = _promotion(db, other_product)

    res = client.post(
        _PORTAL_BASE,
        json={
            "debtor_name": "ZZT Dealer",
            "price_mode": "selling",
            "lines": [
                {
                    "line_type": "product",
                    "product_id": covered_product.id,
                    "promotion_id": non_covering.id,
                }
            ],
        },
    )
    assert res.status_code == 422, res.text
    assert "line" in (res.json().get("detail") or res.text).lower()


def test_line_promotion_covering_only_a_candidate_is_accepted(portal_client):
    from app.models.product_combo import ProductCombo, ProductComboPart

    client, db, contact_id = portal_client
    parent = _product(db)
    candidate_a = _product(db)
    candidate_b = _product(db)
    _grant_audience_code(db, contact_id, "dealer")
    covers_candidate = _promotion(db, candidate_a)
    combo = ProductCombo(
        id=_uid(), host_product_id=parent.id, name="ZZT combo", sort_order=0
    )
    db.add(combo)
    db.flush()
    for index, candidate in enumerate((candidate_a, candidate_b)):
        db.add(
            ProductComboPart(
                id=_uid(),
                combo_id=combo.id,
                part_product_id=candidate.id,
                choice_group="Basin",
                sort_order=index,
            )
        )
    db.flush()

    res = client.post(
        _PORTAL_BASE,
        json={
            "debtor_name": "ZZT Dealer",
            "price_mode": "selling",
            "lines": [
                {
                    "line_type": "product",
                    "product_id": parent.id,
                    "combo_id": combo.id,
                    "promotion_id": covers_candidate.id,
                    "parts": [
                        {
                            "role": "Basin",
                            "candidates": [candidate_a.id, candidate_b.id],
                        }
                    ],
                }
            ],
        },
    )
    assert res.status_code == 201, res.text
