"""PROMO-SUBJECT-FILTER R5, AC-20: `promotion_types` (type codes) and `brand_ids` on
GET /api/v1/marketing/promotions. Brand means the brand of a LINKED product
(promotion_products -> products.brand_id). Both AND with scope_terms, access_levels and the
active window. Seeded Postgres blank schema, fake promotion names.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.access import ContactAccessType
from app.models.marketing import Promotion, PromotionGroup, PromotionProduct, PromotionType
from app.models.product import Brand
from app.models.product_spec import ProductSpecifications
from app.services.company_scope import DEFAULT_COMPANY_ID
from tests._mc_lookup_seed import product
from tests._pg_fixture import blank_session

TODAY = date.today()


@pytest.fixture
def ctx(monkeypatch):
    from app.dependencies import get_current_user, get_current_user_or_api_key, get_db
    from app.services.company_scope_resolver import apply_company_scope
    from app.services.user_service import UserPermissionService

    monkeypatch.setattr(UserPermissionService, "get_user_role_slugs", lambda self, uid: {"superadmin"})
    with blank_session() as db:

        def _db():
            yield db

        def _user():
            return {"id": "130c548f-048f-53b2-97a6-3a54676bea77", "email": "u@test.com", "auth_method": "api_key"}

        app.dependency_overrides[get_db] = _db
        app.dependency_overrides[get_current_user] = _user
        app.dependency_overrides[get_current_user_or_api_key] = _user
        app.dependency_overrides[apply_company_scope] = lambda: None
        try:
            with TestClient(app) as client:
                yield client, db
        finally:
            app.dependency_overrides.clear()


def _brand(db, name):
    row = Brand(id=str(uuid.uuid4()), brand_code=f"ZZT{uuid.uuid4().hex[:6]}"[:50], brand_name=name, company_id=DEFAULT_COMPANY_ID)
    db.add(row)
    db.flush()
    return row


def _type(db, code, name):
    row = PromotionType(type_code=code, type_name=name, show_expired=True, match_markers=[], match_priority=50, is_default=False)
    db.add(row)
    db.flush()
    return row


def _prod(db, brand, klass):
    row = product(db, company_id=DEFAULT_COMPANY_ID)
    row.brand_id = brand.id
    db.add(ProductSpecifications(id=str(uuid.uuid4()), product_id=row.id, values={"class": {"value": klass}}))
    db.flush()
    return row


def _promo(db, name, *, ptype, products=(), levels=("dealer",), start=None, end=None, active=True):
    p = Promotion(
        id=str(uuid.uuid4()), description=name,
        start_date=start or TODAY - timedelta(days=5), end_date=end or TODAY + timedelta(days=30),
        is_active=active, access_levels=list(levels), promotion_type_id=ptype.id,
    )
    db.add(p)
    db.flush()
    group = PromotionGroup(promotion_id=p.id, group_name="Default", sort_order=0)
    db.add(group)
    db.flush()
    for prod in products:
        db.add(PromotionProduct(id=str(uuid.uuid4()), promotion_id=p.id, promotion_group_id=group.id, product_id=prod.id))
    db.flush()
    return p


@pytest.fixture
def world(ctx):
    client, db = ctx
    srt, mocha = _brand(db, "Sorento"), _brand(db, "Mocha")
    flyer, standard = _type(db, "a3_flyer", "A3 Flyer"), _type(db, "standard", "Standard Promo")
    db.add_all([ContactAccessType(code="dealer", name="Dealer"), ContactAccessType(code="office", name="Office")])
    srt_sink, mocha_sink = _prod(db, srt, "Kitchen Sink"), _prod(db, mocha, "Kitchen Sink")
    srt_wc = _prod(db, srt, "Water Closet")
    p = {
        "srt_flyer": _promo(db, "ZZT SORENTO A3 FLYER 2026", ptype=flyer, products=[srt_sink, srt_wc]),
        "srt_std": _promo(db, "ZZT SORENTO STANDARD", ptype=standard, products=[srt_sink, srt_wc]),
        "mocha_flyer": _promo(db, "ZZT MOCHA A3 FLYER A", ptype=flyer, products=[mocha_sink]),
        "mocha_flyer_office": _promo(db, "ZZT MOCHA A3 FLYER OFFICE", ptype=flyer, products=[mocha_sink], levels=("office",)),
        "srt_flyer_old": _promo(db, "ZZT SORENTO A3 FLYER OLD", ptype=flyer, products=[srt_sink],
                                start=TODAY - timedelta(days=90), end=TODAY - timedelta(days=30), active=False),
        "unlinked_flyer": _promo(db, "ZZT UNLINKED A3 FLYER", ptype=flyer),
    }
    return client, db, {"srt": srt, "mocha": mocha}, p


def _get(client, db, **params):
    db.commit()
    res = client.get("/api/v1/marketing/promotions/", params=params)
    assert res.status_code == 200, res.text
    return {r["description"] for r in res.json()["data"]}


def test_promotion_types_keeps_only_that_kind(world):
    """AC-20: type codes."""
    client, db, b, p = world
    names = _get(client, db, promotion_types=["a3_flyer"], access_levels=["Dealer"])
    assert "ZZT SORENTO STANDARD" not in names and "ZZT SORENTO A3 FLYER 2026" in names, names


def test_brand_ids_means_brand_of_a_linked_product(world):
    """AC-20: brand of a LINKED product; an unlinked promotion never matches a brand."""
    client, db, b, p = world
    names = _get(client, db, brand_ids=[b["mocha"].id], access_levels=["Dealer"])
    assert names == {"ZZT MOCHA A3 FLYER A"}, names


def test_kind_and_brand_and_level_and_window_all_and(world):
    """AC-20: Sorento A3 flyer at Dealer: the live one only (not the expired, not Mocha, not standard)."""
    client, db, b, p = world
    names = _get(client, db, promotion_types=["a3_flyer"], brand_ids=[b["srt"].id], access_levels=["Dealer"])
    assert names == {"ZZT SORENTO A3 FLYER 2026"}, names


def test_kind_with_no_live_brand_row_is_empty_not_the_inactive_fallback(world):
    """AC-20/AC-2: Sorento has only an expired, inactive, linked A3 flyer at Office (what the
    old zero-match fallback would return); kind + brand + Office must be empty."""
    client, db, b, p = world
    flyer = db.query(PromotionType).filter_by(type_code="a3_flyer").one()
    old = _prod(db, b["srt"], "Kitchen Sink")
    _promo(db, "ZZT SORENTO A3 FLYER OFFICE OLD", ptype=flyer, products=[old], levels=("office",),
           start=TODAY - timedelta(days=90), end=TODAY - timedelta(days=30), active=False)
    body = _get(client, db, promotion_types=["a3_flyer"], brand_ids=[b["srt"].id], access_levels=["Office"])
    assert body == set(), body


def test_api_key_kind_ask_keeps_the_ten_row_cap(world):
    """SF-c: promotion_types / brand_ids on an API-key call keep the cap of 10."""
    client, db, b, p = world
    flyer = db.query(PromotionType).filter_by(type_code="a3_flyer").one()
    prod = _prod(db, b["srt"], "Kitchen Sink")
    for i in range(12):
        _promo(db, f"ZZT SORENTO A3 FLYER CAP {i}", ptype=flyer, products=[prod])
    assert len(_get(client, db, promotion_types=["a3_flyer"], access_levels=["Dealer"])) == 10
    assert len(_get(client, db, brand_ids=[b["srt"].id], access_levels=["Dealer"])) == 10


def test_kind_ands_with_scope_terms(world):
    """AC-20: kind AND subject: Sorento flyer linked to a water closet and a sink."""
    client, db, b, p = world
    names = _get(client, db, promotion_types=["a3_flyer"], scope_terms=["Water Closet"], access_levels=["Dealer"])
    assert names == {"ZZT SORENTO A3 FLYER 2026"}, names
