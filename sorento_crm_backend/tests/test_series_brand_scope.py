"""SERIES-BRAND-SCOPE (UAC A1-A4, B1-B5, C1-C5, C7, C8): a series with a brand only treats
products of THAT brand as standard by category, and its Products table is preloaded with the
brand's active products in the series categories.

Red-first: written against the Phase 1 contract, before the backend exists. Every row is
seeded on a blank Postgres schema.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.models.product import Brand, Product, ProductCategory, UnitOfMeasure
from app.services import project_seed_service

from ._pg_fixture import blank_session
from .test_project_series_product_routes import (
    BASE,
    _client,
    _quotation,
    _restore,
    _sorento,
    _user,
)

MARKER = "zzt-series-brand"
SELECT = "/api/v1/master-data/product-categories/select"
SLUGS = [
    "projects.projects.view",
    "projects.projects.create",
    "projects.projects.edit",
    "projects.projects.delete",
    "projects.projects.manage",
    "projects.types.view",
    "projects.types.edit",
    "master_data.product_categories.view",
]


def _uid() -> str:
    return str(uuid.uuid4())


def _uom(db) -> str:
    row = UnitOfMeasure(id=_uid(), uom_code=f"ZZT{_uid()[:4]}", uom_name="Piece")
    db.add(row)
    db.flush()
    return row.id


def _brand(db, name: str) -> Brand:
    row = Brand(id=_uid(), brand_code=f"ZZT{_uid()[:8]}", brand_name=f"{MARKER} {name}")
    db.add(row)
    db.flush()
    return row


def _category(db, name: str, parent_id=None, active: bool = True) -> ProductCategory:
    row = ProductCategory(
        id=_uid(),
        category_code=f"ZZT-{_uid()[:8]}",
        category_name=f"{MARKER} {name}",
        parent_category_id=parent_id,
        is_active=active,
    )
    db.add(row)
    db.flush()
    return row


def _product(db, code, category_id, uom_id, brand_id=None, active=True) -> Product:
    row = Product(
        id=_uid(),
        product_code=code,
        product_name=f"{MARKER} {code}",
        category_id=category_id,
        base_uom_id=uom_id,
        brand_id=brand_id,
        is_active=active,
        list_price=Decimal("1000.00"),
    )
    db.add(row)
    db.flush()
    return row


@pytest.fixture()
def world():
    """Two brands; a parent/child category pair; products of both brands, an inactive one
    and one with no brand, all in the child category; a second category holding only an
    inactive product of brand A."""
    from app.models.base import company_scope
    from app.services.project_service import register_project

    with blank_session() as db:
        company_id = _sorento(db)
        project_seed_service.run(db, company_id=company_id)
        user_id = _user(db)
        project = register_project(
            db,
            company_id=company_id,
            actor_user_id=user_id,
            developer_party_id=None,
            title=f"{MARKER} Tower {_uid()[:12]}",
        )
        uom = _uom(db)
        a, b = _brand(db, "A"), _brand(db, "B")
        parent = _category(db, "Parent")
        child = _category(db, "Child", parent.id)
        dead_only = _category(db, "InactiveOnly")
        unrelated = _category(db, "Unrelated")
        w = type("W", (), {})()
        w.db, w.company_id, w.project, w.uom = db, company_id, project, uom
        w.a, w.b, w.parent, w.child = a, b, parent, child
        w.dead_only, w.unrelated = dead_only, unrelated
        w.a1 = _product(db, "ZZT-A-1", child.id, uom, a.id)
        w.a2 = _product(db, "ZZT-A-2", parent.id, uom, a.id)
        w.a_dead = _product(db, "ZZT-A-DEAD", child.id, uom, a.id, active=False)
        w.b1 = _product(db, "ZZT-B-1", child.id, uom, b.id)
        w.nobrand = _product(db, "ZZT-N-1", child.id, uom, None)
        w.a_in_dead_only = _product(db, "ZZT-A-X", dead_only.id, uom, a.id, active=False)
        db.commit()
        client, originals = _client(db, user_id, slugs=SLUGS)
        w.client = client
        try:
            with company_scope(db, frozenset({company_id})):
                yield w
        finally:
            _restore(originals)


def _series(w, brand_id=None, category_ids=(), name="S"):
    r = w.client.post(
        f"{BASE}/config/series",
        json={
            "name": f"{MARKER} {name} {_uid()[:6]}",
            "brand_id": brand_id,
            "category_ids": list(category_ids),
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _rows(w, series_id):
    r = w.client.get(f"{BASE}/config/series/{series_id}/products/rows")
    assert r.status_code == 200, r.text
    return {row["product_code"]: row for row in r.json()["data"]}


def _ids(response):
    assert response.status_code == 200, response.text
    return {c["id"] for c in response.json()}


# ---------------------------------------------------------------- A: category picker


def test_a1_a2_brand_filter_lists_qualifying_categories_and_their_parents(world):
    got = _ids(world.client.get(SELECT, params={"brand_id": world.a.id}))
    # child holds active brand-A product; parent holds one too and is the child's ancestor.
    assert world.child.id in got and world.parent.id in got
    assert world.dead_only.id not in got  # A2: only inactive products of the brand
    assert world.unrelated.id not in got  # A1: nothing else


def test_a1_ancestor_of_a_qualifying_category_is_listed_even_with_no_products_of_its_own(world):
    top = _category(world.db, "Top")
    mid = _category(world.db, "Mid", top.id)
    leaf = _category(world.db, "Leaf", mid.id)
    _product(world.db, "ZZT-LEAF-A", leaf.id, world.uom, world.a.id)
    world.db.commit()
    got = _ids(world.client.get(SELECT, params={"brand_id": world.a.id}))
    assert {top.id, mid.id, leaf.id} <= got


def test_a4_without_brand_every_active_category_is_listed(world):
    inactive = _category(world.db, "Off", active=False)
    world.db.commit()
    got = _ids(world.client.get(SELECT))
    assert {world.parent.id, world.child.id, world.dead_only.id, world.unrelated.id} <= got
    assert inactive.id not in got


def test_a3_the_picker_is_not_capped_at_100(world):
    made = [_category(world.db, f"Bulk{i}") for i in range(105)]
    for i, cat in enumerate(made):
        _product(world.db, f"ZZT-BULK-{i}", cat.id, world.uom, world.a.id)
    world.db.commit()
    plain = _ids(world.client.get(SELECT))
    branded = _ids(world.client.get(SELECT, params={"brand_id": world.a.id}))
    wanted = {c.id for c in made}
    assert wanted <= plain
    assert wanted <= branded


# ------------------------------------------------------------ B: brand restricts standard


def _in(w, series_id, product):
    from app.models.projects import ProjectSeries
    from app.services import project_pricing_service as pricing

    return pricing.is_in_series(w.db, series_id=series_id, product=product)


def test_b1_branded_series_category_match_requires_the_series_brand(world):
    s = _series(world, world.a.id, [world.parent.id])  # parent covers child
    assert _in(world, s["id"], world.a1) is True
    assert _in(world, s["id"], world.a2) is True


def test_b2_other_brand_or_no_brand_in_a_series_category_is_non_standard(world):
    s = _series(world, world.a.id, [world.parent.id])
    assert _in(world, s["id"], world.b1) is False
    assert _in(world, s["id"], world.nobrand) is False


def test_b3_series_without_brand_ignores_brand_for_category_match(world):
    s = _series(world, None, [world.parent.id])
    assert _in(world, s["id"], world.b1) is True
    assert _in(world, s["id"], world.nobrand) is True


def test_b4_a_named_other_brand_product_stays_standard(world):
    s = _series(world, world.a.id, [world.parent.id])
    r = world.client.post(
        f"{BASE}/config/series/{s['id']}/products",
        json={"codes": [world.b1.product_code], "mode": "append"},
    )
    assert r.status_code == 200, r.text
    assert _in(world, s["id"], world.b1) is True
    assert _in(world, s["id"], world.nobrand) is False


def test_b2_series_membership_carries_the_brand_id(world):
    from app.services import project_pricing_service as pricing

    s = _series(world, world.a.id, [world.parent.id])
    assert pricing.series_membership(world.db, s["id"]).brand_id == world.a.id


def test_b2_quotation_save_and_live_check_flag_the_other_brand(world):
    w = world
    s = _series(w, w.a.id, [w.parent.id])
    named = _series(w, w.a.id, [w.parent.id], name="Named")
    w.client.post(
        f"{BASE}/config/series/{named['id']}/products",
        json={"codes": [w.b1.product_code], "mode": "append"},
    )
    q = _quotation(w.client, w.project.id)
    assert w.client.put(f"{BASE}/quotations/{q['id']}", json={"series_id": s["id"]}).status_code == 200

    def line(product):
        r = w.client.post(
            f"{BASE}/quotation-versions/{q['current_version_id']}/lines",
            json={"product_id": product.id, "unit_price": "900.00", "quantity": "1"},
        )
        assert r.status_code in (200, 201), r.text
        return r.json()

    def live(product):
        return w.client.get(
            f"{BASE}/quotations/{q['id']}/line-verdict",
            params={"product_id": product.id, "unit_price": "900.00"},
        ).json()["is_non_standard"]

    assert line(w.a1)["is_non_standard"] is False
    assert line(w.b1)["is_non_standard"] is True
    assert line(w.nobrand)["is_non_standard"] is True
    assert live(w.a1) is False and live(w.b1) is True and live(w.nobrand) is True
    # named (stored row) other-brand product on another quotation stays standard (B4)
    q2 = _quotation(w.client, w.project.id)
    w.client.put(f"{BASE}/quotations/{q2['id']}", json={"series_id": named["id"]})
    assert (
        w.client.get(
            f"{BASE}/quotations/{q2['id']}/line-verdict",
            params={"product_id": w.b1.id, "unit_price": "900.00"},
        ).json()["is_non_standard"]
        is False
    )


def test_b5_a_derived_blank_row_gives_no_series_floor(world):
    from app.services import project_pricing_service as pricing

    s = _series(world, world.a.id, [world.parent.id])
    rows = _rows(world, s["id"])
    assert rows["ZZT-A-1"]["derived_floor"] is None
    floor = pricing.resolve_floor(
        world.db,
        company_id=world.company_id,
        product=world.a1,
        list_price=Decimal("1000.00"),
        series_id=s["id"],
    )
    assert floor is None or floor.level != "series"


# --------------------------------------------------------------- C: products preloaded


def test_c1_c2_rows_are_preloaded_with_active_brand_products_once_each_by_code(world):
    s = _series(world, world.a.id, [world.parent.id])
    r = world.client.get(f"{BASE}/config/series/{s['id']}/products/rows")
    codes = [row["product_code"] for row in r.json()["data"]]
    assert codes == ["ZZT-A-1", "ZZT-A-2"]  # sorted, deduped, no inactive, no other brand
    rows = _rows(world, s["id"])
    for row in rows.values():
        assert row["selling_price"] is None and row["max_discount_pct"] is None
        assert row["derived_floor"] is None
        assert row["in_scope"] is True


def test_c1_c2_stored_rows_keep_numbers_inactive_stored_still_listed_and_out_of_scope(world):
    s = _series(world, world.a.id, [world.parent.id])
    for p in (world.a1, world.b1, world.a_dead):
        world.client.post(
            f"{BASE}/config/series/{s['id']}/products",
            json={"codes": [p.product_code], "mode": "append"},
        )
    world.client.patch(
        f"{BASE}/config/series/{s['id']}/products/{world.a1.id}",
        json={"selling_price": "800.00", "max_discount_pct": "10"},
    )
    rows = _rows(world, s["id"])
    assert set(rows) == {"ZZT-A-1", "ZZT-A-2", "ZZT-B-1", "ZZT-A-DEAD"}
    assert Decimal(str(rows["ZZT-A-1"]["selling_price"])) == Decimal("800.00")
    assert rows["ZZT-A-1"]["derived_floor"] is not None
    assert rows["ZZT-A-1"]["in_scope"] is True
    assert rows["ZZT-B-1"]["in_scope"] is False  # other brand, stored
    assert rows["ZZT-A-2"]["in_scope"] is True


def test_c7_no_brand_or_no_categories_shows_stored_rows_only(world):
    no_brand = _series(world, None, [world.parent.id], name="NB")
    no_cats = _series(world, world.a.id, [], name="NC")
    assert _rows(world, no_brand["id"]) == {}
    assert _rows(world, no_cats["id"]) == {}
    world.client.post(
        f"{BASE}/config/series/{no_cats['id']}/products",
        json={"codes": [world.a1.product_code], "mode": "append"},
    )
    rows = _rows(world, no_cats["id"])
    assert set(rows) == {"ZZT-A-1"} and rows["ZZT-A-1"]["in_scope"] is False


def test_c3_typing_on_a_derived_row_stores_it_and_floor_appears(world):
    from app.models.projects import ProjectSeriesProduct

    s = _series(world, world.a.id, [world.parent.id])
    r = world.client.patch(
        f"{BASE}/config/series/{s['id']}/products/{world.a2.id}",
        json={"selling_price": "500.00", "max_discount_pct": "20"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["derived_floor"] is not None
    assert (
        world.db.query(ProjectSeriesProduct)
        .filter(ProjectSeriesProduct.series_id == s["id"], ProjectSeriesProduct.product_id == world.a2.id)
        .count()
        == 1
    )


def test_c3_out_of_scope_unstored_product_is_still_a_404(world):
    s = _series(world, world.a.id, [world.parent.id])
    for p in (world.b1, world.a_dead, world.unrelated and world.a_in_dead_only):
        r = world.client.patch(
            f"{BASE}/config/series/{s['id']}/products/{p.id}",
            json={"selling_price": "500.00", "max_discount_pct": "20"},
        )
        assert r.status_code == 404, r.text
        assert "series_product_not_found" in r.text


def test_c4_c5_removing_a_category_drops_derived_rows_but_keeps_priced_stored_ones(world):
    extra = _category(world.db, "Extra")
    only_extra = _product(world.db, "ZZT-A-EXTRA", extra.id, world.uom, world.a.id)
    kept = _product(world.db, "ZZT-A-KEPT", extra.id, world.uom, world.a.id)
    world.db.commit()
    s = _series(world, world.a.id, [world.parent.id, extra.id])
    assert {"ZZT-A-EXTRA", "ZZT-A-KEPT"} <= set(_rows(world, s["id"]))
    world.client.patch(
        f"{BASE}/config/series/{s['id']}/products/{kept.id}",
        json={"selling_price": "700.00", "max_discount_pct": None},
    )
    r = world.client.put(
        f"{BASE}/config/series/{s['id']}",
        json={"category_ids": [world.parent.id]},
    )
    assert r.status_code == 200, r.text
    rows = _rows(world, s["id"])
    assert "ZZT-A-EXTRA" not in rows
    assert "ZZT-A-KEPT" in rows and rows["ZZT-A-KEPT"]["in_scope"] is False
    # C5: a product added to the catalogue later appears on the next load
    _product(world.db, "ZZT-A-LATER", world.child.id, world.uom, world.a.id)
    world.db.commit()
    assert "ZZT-A-LATER" in _rows(world, s["id"])


def test_c8_replace_import_then_unlisted_in_scope_products_reappear_blank(world):
    s = _series(world, world.a.id, [world.parent.id])
    r = world.client.post(
        f"{BASE}/config/series/{s['id']}/products",
        json={"codes": [world.a1.product_code], "mode": "replace"},
    )
    assert r.status_code == 200, r.text
    rows = _rows(world, s["id"])
    assert "ZZT-A-2" in rows  # in scope, not in the sheet: preloaded again
    assert rows["ZZT-A-2"]["selling_price"] is None and rows["ZZT-A-2"]["in_scope"] is True
