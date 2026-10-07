"""PROMO-SUBJECT-FILTER backend: GET /api/v1/marketing/promotions `scope_terms` (AC-1, AC-2, AC-3).

UAC `documentation/plans/chatbot/promo-subject-filter-acceptance-criteria.md`.
`scope_terms` is a list of class phrases, read the same way the resolver reads them
(`product_spec_search.filter_specs`): a promotion matches when at least one linked
`promotion_products` product carries that class in `product_specifications.values.class`.

Postgres blank schema, every row seeded here (CI's database is empty).
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.access import ContactAccessType
from app.models.marketing import Promotion, PromotionGroup, PromotionProduct
from app.models.product_spec import ProductSpecifications
from tests._mc_lookup_seed import product
from tests._pg_fixture import blank_session
from app.services.company_scope import DEFAULT_COMPANY_ID

_USER_ID = "130c548f-048f-53b2-97a6-3a54676bea77"
TODAY = date.today()


@pytest.fixture
def ctx(monkeypatch):
    from app.services.user_service import UserPermissionService

    monkeypatch.setattr(UserPermissionService, "get_user_role_slugs", lambda self, uid: {"superadmin"})
    from app.dependencies import get_current_user, get_current_user_or_api_key, get_db
    from app.services.company_scope_resolver import apply_company_scope

    with blank_session() as db:

        def _db():
            yield db

        def _user():
            return {
                "id": _USER_ID,
                "email": "u@test.com",
                "role": "superadmin",
                "is_superuser": True,
                "auth_method": "api_key",
            }

        app.dependency_overrides[get_db] = _db
        app.dependency_overrides[get_current_user] = _user
        app.dependency_overrides[get_current_user_or_api_key] = _user
        app.dependency_overrides[apply_company_scope] = lambda: None
        try:
            with TestClient(app) as client:
                yield client, db
        finally:
            app.dependency_overrides.clear()


def _class_product(db, class_label):
    row = product(db, company_id=DEFAULT_COMPANY_ID)
    db.add(
        ProductSpecifications(
            id=str(uuid.uuid4()),
            product_id=row.id,
            values={"class": {"value": class_label}},
        )
    )
    db.flush()
    return row


def _promo(db, description, *, products=(), levels=("office",), start=None, end=None, active=True):
    promo = Promotion(
        id=str(uuid.uuid4()),
        description=description,
        start_date=start or TODAY - timedelta(days=5),
        end_date=end or TODAY + timedelta(days=30),
        is_active=active,
        access_levels=list(levels),
    )
    db.add(promo)
    db.flush()
    if products:
        group = PromotionGroup(promotion_id=promo.id, group_name="Default", sort_order=0)
        db.add(group)
        db.flush()
        for p in products:
            db.add(
                PromotionProduct(
                    id=str(uuid.uuid4()),
                    promotion_id=promo.id,
                    promotion_group_id=group.id,
                    product_id=p.id,
                )
            )
    db.flush()
    return promo


def _get(client, db, **params):
    db.commit()
    res = client.get("/api/v1/marketing/promotions/", params=params)
    assert res.status_code == 200, res.text
    return res.json()


def _names(body):
    return {row["description"] for row in body["data"]}


@pytest.fixture
def seeded(ctx):
    client, db = ctx
    cabinet = _class_product(db, "Bathroom Furniture")
    sink = _class_product(db, "Kitchen Sink")
    a = _promo(db, "Promo A bathroom", products=[cabinet], levels=("office", "dealer"))
    b = _promo(db, "Promo B sink", products=[sink], levels=("office",))
    unlinked = _promo(db, "Promo C unlinked", levels=("office",))
    db.add_all(
        [
            ContactAccessType(code="office", name="Office"),
            ContactAccessType(code="dealer", name="Dealer"),
        ]
    )
    db.flush()
    return client, db, {"a": a, "b": b, "unlinked": unlinked, "cabinet": cabinet, "sink": sink}


def test_subject_keeps_only_promotions_linked_to_that_class(seeded):
    """AC-1: only promotions with a linked product in the class; unlinked never matches."""
    client, db, s = seeded
    body = _get(client, db, scope_terms=["Bathroom Furniture"])
    assert _names(body) == {"Promo A bathroom"}


def test_subject_does_not_match_on_description_text(seeded):
    """AC-1: no match on promotion description (a promo NAMED for the subject but not linked)."""
    client, db, s = seeded
    _promo(db, "Bathroom Furniture mega sale", products=[s["sink"]], levels=("office",))
    body = _get(client, db, scope_terms=["Bathroom Furniture"])
    assert "Bathroom Furniture mega sale" not in _names(body)
    assert _names(body) == {"Promo A bathroom"}


def test_unlinked_promotion_never_matches_a_subject_but_stays_for_no_subject_ask(seeded):
    """AC-1 + AC-12: an unlinked promo is absent under a subject, present with none."""
    client, db, s = seeded
    with_subject = _get(client, db, scope_terms=["Kitchen Sink"])
    assert "Promo C unlinked" not in _names(with_subject)
    no_subject = _get(client, db, query="unlinked")
    assert "Promo C unlinked" in _names(no_subject)


def test_subject_matching_nothing_returns_empty_not_inactive_fallback(seeded):
    """AC-2: zero-match subject does not fall back to inactive/expired promotions."""
    client, db, s = seeded
    _promo(
        db,
        "Old sink promo",
        products=[s["sink"]],
        start=TODAY - timedelta(days=90),
        end=TODAY - timedelta(days=30),
    )
    # A class that products carry but no promotion links to: nothing matches, and the
    # route must not widen to inactive rows.
    _class_product(db, "Wall Hung Basin")
    body = _get(client, db, scope_terms=["Wall Hung Basin"])
    assert body["data"] == []


def test_subject_ands_with_access_levels(seeded):
    """AC-3: subject AND access level."""
    client, db, s = seeded
    office = _get(client, db, scope_terms=["Bathroom Furniture"], access_levels=["Office"])
    assert _names(office) == {"Promo A bathroom"}
    nobody = _get(client, db, scope_terms=["Kitchen Sink"], access_levels=["Dealer"])
    assert nobody["data"] == []


def test_subject_ands_with_active_window(seeded):
    """AC-3: an expired promo linked to the subject is not returned beside a live one."""
    client, db, s = seeded
    _promo(
        db,
        "Expired bathroom promo",
        products=[s["cabinet"]],
        start=TODAY - timedelta(days=90),
        end=TODAY - timedelta(days=30),
    )
    body = _get(client, db, scope_terms=["Bathroom Furniture"])
    assert _names(body) == {"Promo A bathroom"}


def test_subject_ands_with_product_ids(seeded):
    """AC-3: subject narrows product_ids, never widens it."""
    client, db, s = seeded
    body = _get(client, db, scope_terms=["Bathroom Furniture"], product_ids=[s["sink"].id])
    assert body["data"] == []


def test_ac18_kitchen_sink_returns_only_sink_promotions_at_the_level(seeded):
    """AC-18: "kitchen sink promo" at Office: no bathroom furniture, WC or tap promotion."""
    client, db, s = seeded
    wc = _class_product(db, "Water Closet")
    tap = _class_product(db, "Tap")
    _promo(db, "Promo WC", products=[wc], levels=("office",))
    _promo(db, "Promo Tap", products=[tap], levels=("office",))
    _promo(db, "Promo sink dealer only", products=[s["sink"]], levels=("dealer",))
    body = _get(client, db, scope_terms=["Kitchen Sink"], access_levels=["Office"])
    assert _names(body) == {"Promo B sink"}


def test_ac17_product_ids_path_still_returns_that_products_promotion(seeded):
    """AC-17 regression guard: a product-code ask (product_ids) is unchanged by the new param."""
    client, db, s = seeded
    body = _get(client, db, product_ids=[s["sink"].id], access_levels=["Office"])
    assert _names(body) == {"Promo B sink"}
