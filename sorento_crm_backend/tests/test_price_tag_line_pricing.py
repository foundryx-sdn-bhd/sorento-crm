"""`line_pricing` - one call answering what a LINE costs, for the form and the

CRM lines table (D2, D3, D4, S7).

UAC: AC-S7-1 to AC-S7-5. Written test-FIRST (PRINCIPLES.md Phase 2):
`app.services.dealer_kit.pricing.line_pricing` does not exist yet, so the
import below fails the WHOLE FILE at collection with one `ImportError` - the
same accepted pattern `test_price_tag_tag_render_data.py` uses for a slice
that has not landed.

Contract this file pins down (Plan D4's shape, made concrete because the
plan's prose left the exact call signature to the tester):

    line_pricing(db, lines: list[dict], viewer: ViewerContext) -> list[dict]

Each input line dict: ``{key, product_id, part_product_ids, candidate_product_ids,
promotion_id, manual_sell_price}`` - the last two optional. ``manual_sell_price``
is NOT part of either route's request body (the plan's two lookup routes never
send it - a line has no manual figure until it is saved), but the service
function accepts it as a plain dict key so `resolve_tags_live` (S9) can call the
SAME engine with a persisted line's `manual_sell_price` rather than
re-implementing the "manual wins" rule a second time.

Each output row: ``{key, list_price, promotion_options, auto_promotion_id,
sell_price, sell_price_basis, parts_at_list, candidates}`` per the plan's API
contract section.
"""
from __future__ import annotations

import os
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.services.dealer_kit.pricing import line_pricing  # THE red import
from app.services.dealer_kit.viewer import ViewerContext

from tests._pg_fixture import blank_session, unique_code

pytestmark = pytest.mark.skipif(
    os.environ.get("SKIP_LIVE_DB_TESTS") == "1",
    reason="SKIP_LIVE_DB_TESTS=1",
)

SORENTO = "00000000-0000-0000-0000-000000000001"


def _uid() -> str:
    return str(uuid.uuid4())


@pytest.fixture
def db():
    with blank_session() as session:
        yield session


def _staff_viewer() -> ViewerContext:
    return ViewerContext(is_staff=True, is_internal_copy=True)


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------


def _product(db, *, list_price="1599.00"):
    from app.models.product import Brand, Product, ProductCategory, UnitOfMeasure

    stem = unique_code("LP")
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
        list_price=Decimal(list_price),
        currency="MYR",
        is_active=True,
    )
    db.add(product)
    db.flush()
    return product


def _promotion(
    db,
    lines: list[tuple],
    *,
    is_active: bool = True,
    start=None,
    end=None,
    access_levels: list[str] | None = None,
    description: str | None = None,
):
    """``lines`` is a list of (product, promo_selling_price)."""
    from app.models.marketing import Promotion, PromotionGroup, PromotionProduct

    promotion = Promotion(
        id=_uid(),
        description=description or unique_code("ZZT promo"),
        start_date=start if start is not None else date.today() - timedelta(days=1),
        end_date=end if end is not None else date.today() + timedelta(days=30),
        is_active=is_active,
        access_levels=access_levels or ["dealer", "end_user"],
        company_id=SORENTO,
    )
    db.add(promotion)
    db.flush()
    group = PromotionGroup(promotion_id=promotion.id, group_name="ZZT group", sort_order=0)
    db.add(group)
    db.flush()
    for product, price in lines:
        db.add(
            PromotionProduct(
                id=_uid(),
                promotion_id=promotion.id,
                promotion_group_id=str(group.id),
                product_id=product.id,
                promo_selling_price=Decimal(price),
                company_id=SORENTO,
            )
        )
    db.flush()
    return promotion


def _contact(db):
    from app.models.access import RespondContact

    contact = RespondContact(
        id=_uid(), phone_number=f"+60{uuid.uuid4().hex[:9]}", name=unique_code("contact")
    )
    db.add(contact)
    db.flush()
    from tests._portal_grant import link_contact_company

    link_contact_company(db, contact.id)
    return contact


# --------------------------------------------------------------------------- AC-S7-1


def test_line_pricing_shape(db):
    parent = _product(db, list_price="1599.00")
    part = _product(db, list_price="199.00")
    # promo_a covers only the parent; its total (1299 + 199 list) is WORSE than
    # promo_b, which also covers the part.
    promo_a = _promotion(db, [(parent, "1299.00")], description="ZZT Promo A")
    promo_b = _promotion(
        db, [(parent, "1199.00"), (part, "149.00")], description="ZZT Promo B"
    )

    result = line_pricing(
        db,
        lines=[
            {
                "key": "L1",
                "product_id": parent.id,
                "part_product_ids": [part.id],
                "candidate_product_ids": [],
                "promotion_id": None,
            }
        ],
        viewer=_staff_viewer(),
    )

    assert len(result) == 1
    row = result[0]
    assert row["key"] == "L1"
    assert row["list_price"] == Decimal("1798.00")
    option_ids = [option["id"] for option in row["promotion_options"]]
    assert option_ids == [promo_b.id, promo_a.id], "lowest total first"
    assert {"id", "description", "sell_price"} <= set(row["promotion_options"][0].keys())
    assert row["auto_promotion_id"] == promo_b.id
    assert row["sell_price"] == Decimal("1348.00")
    assert row["sell_price_basis"] == "promotion"
    assert row["parts_at_list"] == []
    assert row["candidates"] == []


# --------------------------------------------------------------------------- AC-S7-2


def test_options_only_active_covering_audience(db):
    parent = _product(db, list_price="500.00")
    candidate = _product(db, list_price="80.00")
    unrelated = _product(db, list_price="10.00")

    wrong_audience = _promotion(db, [(parent, "400.00")], access_levels=["dealer"])
    expired = _promotion(
        db, [(parent, "1.00")], end=date.today() - timedelta(days=1)
    )
    inactive = _promotion(db, [(parent, "1.00")], is_active=False)
    not_covering = _promotion(db, [(unrelated, "1.00")])
    covers_candidate_only = _promotion(db, [(candidate, "50.00")])

    row = line_pricing(
        db,
        lines=[
            {
                "key": "L1",
                "product_id": parent.id,
                "part_product_ids": [],
                "candidate_product_ids": [candidate.id],
                "promotion_id": None,
            }
        ],
        viewer=ViewerContext(access_codes=frozenset({"end_user"})),
    )[0]

    ids = {option["id"] for option in row["promotion_options"]}
    assert wrong_audience.id not in ids
    assert expired.id not in ids
    assert inactive.id not in ids
    assert not_covering.id not in ids
    assert covers_candidate_only.id in ids, (
        "a promotion covering only a CANDIDATE still counts as covering the line"
    )


# --------------------------------------------------------------------------- AC-S7-3


def test_sell_price_sums_offer_else_list(db):
    parent = _product(db, list_price="1599.00")
    part_no_offer = _product(db, list_price="199.00")
    part_with_offer = _product(db, list_price="299.00")
    unresolved_candidate = _product(db, list_price="349.00")
    promo = _promotion(db, [(parent, "899.00"), (part_with_offer, "249.00")])

    row = line_pricing(
        db,
        lines=[
            {
                "key": "L1",
                "product_id": parent.id,
                "part_product_ids": [part_no_offer.id, part_with_offer.id],
                "candidate_product_ids": [unresolved_candidate.id],
                "promotion_id": promo.id,
            }
        ],
        viewer=_staff_viewer(),
    )[0]

    assert row["sell_price"] == Decimal("1347.00"), "899 offer + 199 list + 249 offer"
    assert row["parts_at_list"] == [part_no_offer.id]


def test_manual_wins(db):
    parent = _product(db, list_price="1599.00")
    promo = _promotion(db, [(parent, "999.00")])

    row = line_pricing(
        db,
        lines=[
            {
                "key": "L1",
                "product_id": parent.id,
                "part_product_ids": [],
                "candidate_product_ids": [],
                "promotion_id": promo.id,
                "manual_sell_price": "1200.00",
            }
        ],
        viewer=_staff_viewer(),
    )[0]

    assert row["sell_price"] == Decimal("1200.00")
    assert row["sell_price_basis"] == "manual"


# --------------------------------------------------------------------------- AC-S7-4


def test_basis_list_when_nothing_covers(db):
    parent = _product(db, list_price="899.00")

    row = line_pricing(
        db,
        lines=[
            {
                "key": "L1",
                "product_id": parent.id,
                "part_product_ids": [],
                "candidate_product_ids": [],
                "promotion_id": None,
            }
        ],
        viewer=_staff_viewer(),
    )[0]

    assert row["promotion_options"] == []
    assert row["auto_promotion_id"] is None
    assert row["sell_price"] == row["list_price"] == Decimal("899.00")
    assert row["sell_price_basis"] == "list"


def test_auto_pick_is_lowest_total(db):
    parent = _product(db, list_price="1599.00")
    cheaper = _promotion(db, [(parent, "999.00")], description="ZZT Cheaper")
    pricier = _promotion(db, [(parent, "1099.00")], description="ZZT Pricier")

    row = line_pricing(
        db,
        lines=[
            {
                "key": "L1",
                "product_id": parent.id,
                "part_product_ids": [],
                "candidate_product_ids": [],
                "promotion_id": None,
            }
        ],
        viewer=_staff_viewer(),
    )[0]

    assert row["auto_promotion_id"] == cheaper.id
    assert row["sell_price"] == Decimal("999.00")
    assert pricier.id in {o["id"] for o in row["promotion_options"]}


# --------------------------------------------------------------------------- AC-S7-5


def test_show_promo_price_derived_on_save(db):
    """`show_promo_price` is written per-line at save time, from `sell_price_basis`.

    Exercised through `PriceTagRequestService.create_request` (the actual save
    path, D1) rather than `line_pricing` directly, since the derivation lives
    beside `_add_lines`.
    """
    from app.services.price_tag_request_service import PriceTagRequestService

    covered = _product(db, list_price="500.00")
    not_covered = _product(db, list_price="500.00")
    promo = _promotion(db, [(covered, "400.00")])
    contact = _contact(db)

    request = PriceTagRequestService.create_request(
        db,
        contact_id=contact.id,
        company_id=SORENTO,
        data={
            "debtor_name": "ZZT Dealer",
            "price_mode": "selling",
            "lines": [
                {
                    "line_type": "product",
                    "product_id": covered.id,
                    "promotion_id": promo.id,
                },
                {"line_type": "product", "product_id": not_covered.id},
            ],
        },
    )
    db.flush()

    by_product = {line.product_id: line for line in request.lines}
    assert by_product[covered.id].show_promo_price is True
    assert by_product[not_covered.id].show_promo_price is False, (
        "a line with no covering promotion and no manual price is LP even in "
        "Selling mode"
    )

    list_mode_request = PriceTagRequestService.create_request(
        db,
        contact_id=_contact(db).id,
        company_id=SORENTO,
        data={
            "debtor_name": "ZZT Dealer",
            "price_mode": "list",
            "lines": [
                {
                    "line_type": "product",
                    "product_id": covered.id,
                    "promotion_id": promo.id,
                }
            ],
        },
    )
    db.flush()
    assert list_mode_request.lines[0].show_promo_price is False


# --------------------------------------------------------------------------- routes


@pytest.fixture
def portal_client():
    from app.api.v1.public.portal import get_portal_token
    from app.database import get_db
    from app.main import app  # noqa: E402  first app import in this fixture
    from app.models.access import RespondContact
    from app.models.portal import PortalToken
    from fastapi.testclient import TestClient
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


def test_portal_route_uses_contact_audience(portal_client):
    """`POST /public/portal/lookups/line-pricing` gates on the CONTACT's codes.

    A dealer-only promotion must not price a line for a contact who holds no
    dealer access code, even though the promotion genuinely covers the product.
    """
    client, db, contact_id = portal_client
    from app.models.access import ContactAccessType, respond_contact_access_types

    parent = _product(db, list_price="500.00")
    _promotion(db, [(parent, "400.00")], access_levels=["dealer"])

    res = client.post(
        "/api/v1/public/portal/lookups/line-pricing",
        json={
            "price_mode": "selling",
            "lines": [
                {
                    "key": "L1",
                    "product_id": parent.id,
                    "part_product_ids": [],
                    "candidate_product_ids": [],
                }
            ],
        },
    )
    assert res.status_code == 200, res.text
    row = res.json()[0]
    assert row["promotion_options"] == []
    assert row["sell_price_basis"] == "list"

    # Granting the contact the "dealer" code makes it visible.
    code = "dealer"
    if db.query(ContactAccessType).filter(ContactAccessType.code == code).first() is None:
        db.add(ContactAccessType(code=code, name=code))
        db.flush()
    db.execute(
        respond_contact_access_types.insert().values(
            contact_id=contact_id, access_type_code=code
        )
    )
    db.flush()

    res2 = client.post(
        "/api/v1/public/portal/lookups/line-pricing",
        json={
            "price_mode": "selling",
            "lines": [
                {
                    "key": "L1",
                    "product_id": parent.id,
                    "part_product_ids": [],
                    "candidate_product_ids": [],
                }
            ],
        },
    )
    assert res2.status_code == 200, res2.text
    assert res2.json()[0]["sell_price_basis"] == "promotion"


@pytest.fixture
def crm_client():
    from app.dependencies import get_current_user, get_current_user_or_api_key, get_db
    from app.main import app  # noqa: E402
    from app.models.base import set_company_scope
    from app.models.user import (
        User,
        UserPermission,
        UserRole,
        UserRoleAssignment,
        UserRolePermission,
    )
    from app.services.company_scope_resolver import apply_company_scope
    from fastapi.testclient import TestClient

    with blank_session() as db:
        role_id = _uid()
        user_id = _uid()
        db.add(
            UserRole(
                id=role_id,
                slug=unique_code("role").lower(),
                name="ZZT Line Pricing Role",
                is_protected=False,
                is_default=False,
            )
        )
        db.add(
            User(
                id=user_id,
                email=f"{unique_code('u').lower()}@test.com",
                name="ZZT Line Pricing User",
                status="ACTIVE",
            )
        )
        db.flush()
        db.add(UserRoleAssignment(user_id=user_id, role_id=role_id))
        view_perm = str(uuid.uuid4())
        db.add(
            UserPermission(
                id=view_perm, slug="dealer_kit.price_tag_requests.view", name="view", description=""
            )
        )
        db.flush()
        db.add(
            UserRolePermission(id=str(uuid.uuid4()), role_id=role_id, permission_id=view_perm)
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
        principal = {"id": user_id, "email": "zzt-line-pricing@test.com"}
        app.dependency_overrides[get_current_user] = lambda: principal
        app.dependency_overrides[get_current_user_or_api_key] = lambda: principal

        def _grant_process():
            process_perm = str(uuid.uuid4())
            db.add(
                UserPermission(
                    id=process_perm,
                    slug="dealer_kit.price_tag_requests.process",
                    name="process",
                    description="",
                )
            )
            db.flush()
            db.add(
                UserRolePermission(
                    id=str(uuid.uuid4()), role_id=role_id, permission_id=process_perm
                )
            )
            db.commit()
            # RBAC contract: permissions are cached per user; a grant made mid-test
            # is invisible to the next request until the cache is dropped.
            from app.services.user_service import invalidate_rbac_cache

            invalidate_rbac_cache(user_id)

        with TestClient(app) as client:
            yield client, db, _grant_process
        app.dependency_overrides.clear()


def test_crm_route_requires_process_permission(crm_client):
    client, db, grant_process = crm_client
    parent = _product(db, list_price="500.00")

    without = client.post(
        "/api/v1/dealer-kit/price-tag-requests/line-pricing",
        json={
            "price_mode": "selling",
            "lines": [
                {
                    "key": "L1",
                    "product_id": parent.id,
                    "part_product_ids": [],
                    "candidate_product_ids": [],
                }
            ],
        },
    )
    assert without.status_code == 403, without.text

    grant_process()

    granted = client.post(
        "/api/v1/dealer-kit/price-tag-requests/line-pricing",
        json={
            "price_mode": "selling",
            "lines": [
                {
                    "key": "L1",
                    "product_id": parent.id,
                    "part_product_ids": [],
                    "candidate_product_ids": [],
                }
            ],
        },
    )
    assert granted.status_code == 200, granted.text


# ---------------------------------------------------------------------------
# R4/R7/R8/R10/R11/R12 (Phase 3 review) - hardening on `line_pricing` and the
# route it backs.
# ---------------------------------------------------------------------------


def test_no_access_codes_gives_no_promotion_options(db):
    """R4a: a viewer with NO access codes falls back to the PUBLIC code
    (`end_user`), so a promotion tagged `dealer` only must never reach it."""
    parent = _product(db, list_price="500.00")
    _promotion(db, [(parent, "400.00")], access_levels=["dealer"])

    row = line_pricing(
        db,
        lines=[
            {
                "key": "L1",
                "product_id": parent.id,
                "part_product_ids": [],
                "candidate_product_ids": [],
                "promotion_id": None,
            }
        ],
        viewer=ViewerContext(access_codes=frozenset()),
    )[0]

    assert row["promotion_options"] == []


def test_promotion_covering_only_a_candidate_prices_the_line_at_list(db):
    """R11a: `promotion_options` legitimately lists a promotion that covers
    only an unresolved CANDIDATE (AC-S7-2) - but if that is the ONLY covering
    promotion, `_line_total_under` sums to exactly the RESOLVED products'
    list price (nothing on the line itself is discounted), so the line's own
    `sell_price_basis` must read `list`, not `promotion` - a line cannot be
    "on sale" for a discount that lives on a candidate nobody has picked yet.
    """
    parent = _product(db, list_price="500.00")
    candidate = _product(db, list_price="80.00")
    covers_candidate_only = _promotion(db, [(candidate, "50.00")])

    row = line_pricing(
        db,
        lines=[
            {
                "key": "L1",
                "product_id": parent.id,
                "part_product_ids": [],
                "candidate_product_ids": [candidate.id],
                "promotion_id": None,
            }
        ],
        viewer=_staff_viewer(),
    )[0]

    assert row["auto_promotion_id"] == covers_candidate_only.id, (
        "seed assumption: it is still the only, and so the cheapest, option"
    )
    assert row["sell_price"] == row["list_price"] == Decimal("500.00")
    assert row["sell_price_basis"] == "list", (
        "nothing on the line itself is discounted by the chosen promotion"
    )


def test_auto_pick_on_a_tie_is_independent_of_query_order(db, monkeypatch):
    """R12: two promotions tied on total must resolve to the SAME winner no
    matter what order Postgres's un-ordered `_covering_promotions` query
    happens to return them in (its `.distinct()` gives no ordering
    guarantee at all, so this cannot be pinned by seeding order alone -
    `_covering_promotions` itself is monkeypatched to hand back the exact
    same two `Promotion` rows in each order, isolating the actual gap:
    `line_pricing`'s own `promotion_options.sort(key=... sell_price only)`
    has no tiebreaker, so a tie's winner is whatever `_covering_promotions`
    happened to return first).
    """
    import app.services.dealer_kit.pricing as pricing_module

    parent = _product(db, list_price="500.00")
    promo_a = _promotion(db, [(parent, "400.00")], description="ZZT A Promo")
    promo_z = _promotion(db, [(parent, "400.00")], description="ZZT Z Promo")

    def _covering_z_first(db_, product_ids, viewer):
        return [promo_z, promo_a]

    def _covering_a_first(db_, product_ids, viewer):
        return [promo_a, promo_z]

    monkeypatch.setattr(pricing_module, "_covering_promotions", _covering_z_first)
    winner_z_first = line_pricing(
        db,
        lines=[{"key": "L1", "product_id": parent.id, "promotion_id": None}],
        viewer=_staff_viewer(),
    )[0]["auto_promotion_id"]

    monkeypatch.setattr(pricing_module, "_covering_promotions", _covering_a_first)
    winner_a_first = line_pricing(
        db,
        lines=[{"key": "L1", "product_id": parent.id, "promotion_id": None}],
        viewer=_staff_viewer(),
    )[0]["auto_promotion_id"]

    assert winner_z_first == winner_a_first, (
        f"tie winner flipped with query order: {winner_z_first=} {winner_a_first=}"
    )


def test_numbers_serialise_as_json_numbers_not_strings(portal_client):
    """R10: a `LinePricingRow`'s `Decimal` fields must reach the browser as
    JSON numbers - `manual_sell_price` on the request/line schemas is a
    documented exception (serialises as a string, per
    `test_patch_line_manual_price`), but the LOOKUP row has no such carve-out
    in the plan (AC-S7-1's contract), so a caller must be able to do
    arithmetic on it without a `parseFloat`.

    No `price_mode` in the payload (T4): `LinePricingRequest` dropped the
    field - neither route ever read it and `line_pricing` takes no such
    parameter - so a test payload naming it drifts from the real contract.
    """
    client, db, _contact_id = portal_client
    parent = _product(db, list_price="500.00")

    res = client.post(
        "/api/v1/public/portal/lookups/line-pricing",
        json={
            "lines": [
                {
                    "key": "L1",
                    "product_id": parent.id,
                    "part_product_ids": [],
                    "candidate_product_ids": [],
                }
            ],
        },
    )
    assert res.status_code == 200, res.text
    row = res.json()[0]
    assert isinstance(row["list_price"], (int, float)), type(row["list_price"])
    assert isinstance(row["sell_price"], (int, float)), type(row["sell_price"])


def test_line_pricing_route_caps_at_200_lines(portal_client):
    """R7: 201 lines on one lookup call is refused with 422, not processed -
    the plan gives every route a body ceiling; this one never got one."""
    client, db, _contact_id = portal_client

    res = client.post(
        "/api/v1/public/portal/lookups/line-pricing",
        json={
            "price_mode": "list",
            "lines": [
                {"key": f"L{i}", "product_id": None, "part_product_ids": [], "candidate_product_ids": []}
                for i in range(201)
            ],
        },
    )
    assert res.status_code == 422, res.text


def test_line_pricing_route_caps_candidates_per_line_at_50(portal_client):
    """R7: 51 candidate ids on ONE line is refused with 422."""
    client, db, _contact_id = portal_client

    res = client.post(
        "/api/v1/public/portal/lookups/line-pricing",
        json={
            "price_mode": "list",
            "lines": [
                {
                    "key": "L1",
                    "product_id": None,
                    "part_product_ids": [],
                    "candidate_product_ids": [str(uuid.uuid4()) for _ in range(51)],
                }
            ],
        },
    )
    assert res.status_code == 422, res.text


def test_malformed_product_id_in_line_pricing_is_422_not_500(portal_client):
    """R8: a junk `product_id` must not reach `Product.id.in_(...)` and blow
    up as a Postgres `DataError` (500) - `_add_lines` already fixed this on
    the save path (security review finding); the bare lookup route never
    validates shape at all."""
    client, db, _contact_id = portal_client

    res = client.post(
        "/api/v1/public/portal/lookups/line-pricing",
        json={
            "price_mode": "list",
            "lines": [
                {
                    "key": "L1",
                    "product_id": "not-a-uuid",
                    "part_product_ids": [],
                    "candidate_product_ids": [],
                }
            ],
        },
    )
    assert res.status_code == 422, res.text


def test_malformed_promotion_id_with_a_candidate_is_422_not_500(portal_client):
    """R8b: a junk `promotion_id`, given (not auto-picked), becomes `chosen_id`
    unconditionally - and with at least one candidate present, reaches
    `_offer_prices`' `PromotionProduct.promotion_id == promotion_id` filter,
    a UUID column, as an unvalidated string (`_covering_promotions`/
    `totals_by_promotion` never touch it as a filter, only as a dict key, so
    this is the ONE place a junk promotion_id actually reaches Postgres)."""
    client, db, _contact_id = portal_client
    parent = _product(db, list_price="500.00")
    candidate = _product(db, list_price="80.00")

    res = client.post(
        "/api/v1/public/portal/lookups/line-pricing",
        json={
            "price_mode": "selling",
            "lines": [
                {
                    "key": "L1",
                    "product_id": parent.id,
                    "part_product_ids": [],
                    "candidate_product_ids": [candidate.id],
                    "promotion_id": "not-a-uuid",
                }
            ],
        },
    )
    assert res.status_code == 422, res.text


def test_lookup_route_still_returns_the_auto_pick_for_an_unpromoted_line(portal_client):
    """D3/AC-S7-4 (owner ruling): the auto pick belongs to the LOOKUP - the
    portal/CRM line-pricing routes exist so a salesperson can SEE what would
    apply before choosing - and stays there. Same product/promotion shape as
    `test_a_saved_lines_basis_never_auto_picks_a_promotion_it_was_not_given`
    (`test_price_tag_tag_render_data.py`), through the ROUTE this time: no
    `promotion_id` given, one covering promotion exists, `auto_promotion_id`
    must still name it.
    """
    client, db, contact_id = portal_client
    from app.models.access import ContactAccessType, respond_contact_access_types

    code = "end_user"
    if db.query(ContactAccessType).filter(ContactAccessType.code == code).first() is None:
        db.add(ContactAccessType(code=code, name=code))
        db.flush()
    db.execute(
        respond_contact_access_types.insert().values(
            contact_id=contact_id, access_type_code=code
        )
    )
    db.flush()

    parent = _product(db, list_price="899.00")
    covering = _promotion(db, [(parent, "799.00")])

    res = client.post(
        "/api/v1/public/portal/lookups/line-pricing",
        json={
            "lines": [
                {
                    "key": "L1",
                    "product_id": parent.id,
                    "part_product_ids": [],
                    "candidate_product_ids": [],
                }
            ],
        },
    )
    assert res.status_code == 200, res.text
    row = res.json()[0]
    assert row["auto_promotion_id"] == covering.id
    assert row["sell_price_basis"] == "promotion"
