"""SERIES-FILTERS (UAC A2-A4, B1-B9, C1, D1-D5): brand is many-or-none, the scope rule is
brand-ok AND category-ok with empty meaning all, and the Products table is paged on the server.

Red-first against the Phase 1 contract. Brands are seeded through the API (brand_ids) so a
red run fails on assertions, not on imports. A4 (migration backfill) is covered by migration
review, not here.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from app.models.product import Product
from app.services import project_seed_service

from ._pg_fixture import blank_session
from .test_project_series_product_routes import BASE, _client, _quotation, _restore, _sorento, _user
from .test_series_brand_scope import (
    MARKER as BMARK,
    SELECT,
    SLUGS,
    _brand,
    _category,
    _product,
    _uom,
)

MARK = "zzt-series-filters"


def _uid() -> str:
    return str(uuid.uuid4())


@pytest.fixture()
def world():
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
            title=f"{MARK} Tower {_uid()[:12]}",
        )
        uom = _uom(db)
        w = type("W", (), {})()
        w.db, w.company_id, w.project, w.uom = db, company_id, project, uom
        w.a, w.b, w.c = _brand(db, "A"), _brand(db, "B"), _brand(db, "C")
        w.x = _category(db, "X")
        w.x_child = _category(db, "XChild", w.x.id)
        w.y = _category(db, "Y")
        w.a_x = _product(db, "ZZF-A-X", w.x.id, uom, w.a.id)
        w.a_xc = _product(db, "ZZF-A-XC", w.x_child.id, uom, w.a.id)
        w.a_y = _product(db, "ZZF-A-Y", w.y.id, uom, w.a.id)
        w.b_x = _product(db, "ZZF-B-X", w.x.id, uom, w.b.id)
        w.b_y = _product(db, "ZZF-B-Y", w.y.id, uom, w.b.id)
        w.c_x = _product(db, "ZZF-C-X", w.x.id, uom, w.c.id)
        w.c_y = _product(db, "ZZF-C-Y", w.y.id, uom, w.c.id)
        w.n_x = _product(db, "ZZF-N-X", w.x.id, uom, None)
        w.dead = _product(db, "ZZF-DEAD", w.x.id, uom, w.a.id, active=False)
        db.commit()
        client, originals = _client(db, user_id, slugs=SLUGS)
        w.client = client
        try:
            with company_scope(db, frozenset({company_id})):
                yield w
        finally:
            _restore(originals)


def _series(w, brands=(), cats=(), name="S"):
    r = w.client.post(
        f"{BASE}/config/series",
        json={
            "name": f"{MARK} {name} {_uid()[:6]}",
            "brand_ids": [b.id for b in brands],
            "category_ids": [c.id for c in cats],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _page(w, sid, **params):
    r = w.client.get(f"{BASE}/config/series/{sid}/products/rows", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def _codes(w, sid):
    out, page = [], 1
    while True:
        body = _page(w, sid, page=page, limit=100)
        out += [row["product_code"] for row in body["data"]]
        if len(out) >= body["pagination"]["total"] or not body["data"]:
            return set(out)
        page += 1


def _in(w, sid, product):
    from app.services import project_pricing_service as pricing

    return pricing.is_in_series(w.db, series_id=sid, product=product)


def _mine(codes):
    return {c for c in codes if c.startswith("ZZF-")}


# ------------------------------------------------------------------------- A: API shape


def test_a2_a3_create_returns_brand_ids_and_names_sorted_by_name(world):
    s = _series(world, [world.b, world.a])
    assert s["brand_ids"] == [world.a.id, world.b.id]
    assert s["brand_names"] == [f"{BMARK} A", f"{BMARK} B"]
    listed = world.client.get(f"{BASE}/config/series").json()["data"]
    row = next(r for r in listed if r["id"] == s["id"])
    assert row["brand_ids"] == [world.a.id, world.b.id]
    assert row["brand_names"] == [f"{BMARK} A", f"{BMARK} B"]


def test_a2_put_sends_brands_whole_and_empty_clears(world):
    s = _series(world, [world.a])
    r = world.client.put(f"{BASE}/config/series/{s['id']}", json={"brand_ids": [world.b.id, world.c.id]})
    assert r.status_code == 200, r.text
    assert r.json()["brand_ids"] == [world.b.id, world.c.id]
    r = world.client.put(f"{BASE}/config/series/{s['id']}", json={"name": f"{MARK} renamed"})
    assert r.json()["brand_ids"] == [world.b.id, world.c.id]  # omitted = untouched
    r = world.client.put(f"{BASE}/config/series/{s['id']}", json={"brand_ids": []})
    assert r.status_code == 200, r.text
    assert r.json()["brand_ids"] == [] and r.json()["brand_names"] == []
    got = next(
        x for x in world.client.get(f"{BASE}/config/series").json()["data"] if x["id"] == s["id"]
    )
    assert got["brand_ids"] == []


# ----------------------------------------------------------------------- B: scope rule


def test_b1_one_brand_under_category_includes_descendants(world):
    s = _series(world, [world.a], [world.x])
    assert _mine(_codes(world, s["id"])) == {"ZZF-A-X", "ZZF-A-XC"}
    assert _in(world, s["id"], world.a_xc) is True
    assert _in(world, s["id"], world.a_y) is False


def test_b2_two_brands_under_category(world):
    s = _series(world, [world.a, world.b], [world.x])
    assert _mine(_codes(world, s["id"])) == {"ZZF-A-X", "ZZF-A-XC", "ZZF-B-X"}
    assert _in(world, s["id"], world.b_x) is True
    assert _in(world, s["id"], world.c_x) is False
    assert _in(world, s["id"], world.b_y) is False


def test_b3_no_brand_category_only_includes_brandless(world):
    s = _series(world, [], [world.x])
    assert _mine(_codes(world, s["id"])) == {
        "ZZF-A-X", "ZZF-A-XC", "ZZF-B-X", "ZZF-C-X", "ZZF-N-X",
    }
    assert _in(world, s["id"], world.n_x) is True
    assert _in(world, s["id"], world.c_y) is False


def test_b4_brand_only_is_every_product_of_the_brand(world):
    s = _series(world, [world.a], [])
    assert _mine(_codes(world, s["id"])) == {"ZZF-A-X", "ZZF-A-XC", "ZZF-A-Y"}
    assert _in(world, s["id"], world.a_y) is True
    assert _in(world, s["id"], world.b_y) is False
    assert _in(world, s["id"], world.dead) is False


def test_b5_nothing_picked_is_every_active_product(world):
    s = _series(world, [], [])
    codes = _mine(_codes(world, s["id"]))
    assert codes == {
        "ZZF-A-X", "ZZF-A-XC", "ZZF-A-Y", "ZZF-B-X", "ZZF-B-Y",
        "ZZF-C-X", "ZZF-C-Y", "ZZF-N-X",
    }
    assert _in(world, s["id"], world.n_x) is True and _in(world, s["id"], world.c_y) is True
    assert _in(world, s["id"], world.dead) is False


def test_b6_named_product_is_always_in(world):
    s = _series(world, [world.a], [world.x])
    r = world.client.post(
        f"{BASE}/config/series/{s['id']}/products",
        json={"codes": [world.c_y.product_code], "mode": "append"},
    )
    assert r.status_code == 200, r.text
    assert _in(world, s["id"], world.c_y) is True
    assert "ZZF-C-Y" in _codes(world, s["id"])


def test_b8_derived_row_in_scope_flag_and_price_edit_follow_the_rule(world):
    s = _series(world, [world.a, world.b], [])
    rows = {r["product_code"]: r for r in _page(world, s["id"], limit=100)["data"]}
    assert rows["ZZF-B-Y"]["in_scope"] is True
    assert "ZZF-C-Y" not in rows
    ok = world.client.patch(
        f"{BASE}/config/series/{s['id']}/products/{world.b_y.id}",
        json={"selling_price": "500.00", "max_discount_pct": "10"},
    )
    assert ok.status_code == 200, ok.text
    no = world.client.patch(
        f"{BASE}/config/series/{s['id']}/products/{world.c_y.id}",
        json={"selling_price": "500.00", "max_discount_pct": "10"},
    )
    assert no.status_code == 404, no.text


def test_b8_empty_filters_make_unstored_product_patchable(world):
    s = _series(world, [], [])
    r = world.client.patch(
        f"{BASE}/config/series/{s['id']}/products/{world.n_x.id}",
        json={"selling_price": "500.00", "max_discount_pct": "10"},
    )
    assert r.status_code == 200, r.text


def _verdicts(w, series_id, product):
    q = _quotation(w.client, w.project.id)
    assert w.client.put(f"{BASE}/quotations/{q['id']}", json={"series_id": series_id}).status_code == 200
    line = w.client.post(
        f"{BASE}/quotation-versions/{q['current_version_id']}/lines",
        json={"product_id": product.id, "unit_price": "900.00", "quantity": "1"},
    )
    assert line.status_code in (200, 201), line.text
    live = w.client.get(
        f"{BASE}/quotations/{q['id']}/line-verdict",
        params={"product_id": product.id, "unit_price": "900.00"},
    )
    assert live.status_code == 200, live.text
    return line.json()["is_non_standard"], live.json()["is_non_standard"]


def test_b8_other_brand_is_non_standard_on_a_brand_only_series(world):
    s = _series(world, [world.a, world.b], [])
    assert _verdicts(world, s["id"], world.c_x) == (True, True)
    assert _verdicts(world, s["id"], world.b_y) == (False, False)


def test_b8_same_product_is_standard_when_no_brand_and_no_category(world):
    s = _series(world, [], [])
    assert _verdicts(world, s["id"], world.c_x) == (False, False)


def test_b8_recompute_follows_the_rule(world):
    s = _series(world, [world.a, world.b], [])
    q = _quotation(world.client, world.project.id)
    world.client.put(f"{BASE}/quotations/{q['id']}", json={"series_id": s["id"]})
    r = world.client.post(
        f"{BASE}/quotation-versions/{q['current_version_id']}/lines",
        json={"product_id": world.c_x.id, "unit_price": "900.00", "quantity": "1"},
    )
    assert r.json()["is_non_standard"] is True
    assert world.client.put(
        f"{BASE}/config/series/{s['id']}", json={"brand_ids": []}
    ).status_code == 200
    rc = world.client.post(f"{BASE}/quotation-versions/{q['current_version_id']}/recompute")
    assert rc.status_code == 200, rc.text
    lines = world.client.get(f"{BASE}/quotation-versions/{q['current_version_id']}/lines")
    if lines.status_code == 200:
        data = lines.json()
        data = data["data"] if isinstance(data, dict) else data
        assert all(not ln["is_non_standard"] for ln in data)


def test_b9_quotation_without_series_treats_catalogue_lines_as_standard(world):
    q = _quotation(world.client, world.project.id)
    line = world.client.post(
        f"{BASE}/quotation-versions/{q['current_version_id']}/lines",
        json={"product_id": world.c_x.id, "unit_price": "900.00", "quantity": "1"},
    )
    assert line.status_code in (200, 201), line.text
    assert line.json()["is_non_standard"] is False


# --------------------------------------------------------------- C1: category picker


def test_c1_two_brands_union_of_categories_plus_ancestors(world):
    extra_top = _category(world.db, "ExtraTop")
    extra = _category(world.db, "ExtraLeaf", extra_top.id)
    _product(world.db, "ZZF-B-EXTRA", extra.id, world.uom, world.b.id)
    world.db.commit()
    r = world.client.get(SELECT, params=[("brand_id", world.a.id), ("brand_id", world.b.id)])
    assert r.status_code == 200, r.text
    got = {c["id"] for c in r.json()}
    assert {world.x.id, world.x_child.id, world.y.id, extra.id, extra_top.id} <= got
    only_b = {
        c["id"] for c in world.client.get(SELECT, params=[("brand_id", world.b.id)]).json()
    }
    assert extra.id in only_b and world.x_child.id not in only_b
    assert extra.id not in {
        c["id"] for c in world.client.get(SELECT, params=[("brand_id", world.a.id)]).json()
    }


# ------------------------------------------------------------------------ D: paging


@pytest.fixture()
def big(world):
    """Series with no filters; bulk products ZZF-P-000..119 under brand A (120 rows plus
    the 8 seeded active ZZF- rows -> 128 total)."""
    for i in range(120):
        _product(world.db, f"ZZF-P-{i:03d}", world.y.id, world.uom, world.a.id)
    world.db.commit()
    world.sid = _series(world, [world.a], [world.y])["id"]
    # brand A + category Y: ZZF-A-Y plus 120 bulk = 121
    world.expected = 121
    return world


def test_d1_d2_page_sizes_total_defaults_and_order(big):
    first = _page(big, big.sid)
    assert first["pagination"] == {"total": 121, "page": 1, "limit": 50}
    assert len(first["data"]) == 50
    second = _page(big, big.sid, page=2)
    assert len(second["data"]) == 50
    third = _page(big, big.sid, page=3)
    assert len(third["data"]) == 21 and third["pagination"]["total"] == 121
    codes = [r["product_code"] for p in (first, second, third) for r in p["data"]]
    assert codes == sorted(codes) and len(set(codes)) == 121
    assert codes[0] == "ZZF-A-Y"
    assert "empty" in first


def test_d3_server_search_over_whole_series_case_insensitive_code_or_name(big):
    body = _page(big, big.sid, query="zzf-p-115")
    assert [r["product_code"] for r in body["data"]] == ["ZZF-P-115"]
    assert body["pagination"]["total"] == 1
    by_name = _page(big, big.sid, query="ZZT-SERIES-BRAND ZZF-P-007")  # name = "<marker> <code>"
    assert [r["product_code"] for r in by_name["data"]] == ["ZZF-P-007"]
    many = _page(big, big.sid, query="zzf-p-1", limit=100)
    assert many["pagination"]["total"] == 20  # 100..119


def test_d4_limit_bounds(big):
    r = big.client.get(f"{BASE}/config/series/{big.sid}/products/rows", params={"limit": 101})
    assert r.status_code == 422, r.text
    ok = big.client.get(f"{BASE}/config/series/{big.sid}/products/rows", params={"limit": 100})
    assert ok.status_code == 200 and len(ok.json()["data"]) == 100


def test_d5_patch_a_derived_row_on_page_two_and_total_counts_named_plus_scope(big):
    page2 = _page(big, big.sid, page=2)["data"]
    target = page2[5]
    assert target["selling_price"] is None
    pid = big.db.query(Product).filter(Product.product_code == target["product_code"]).one().id
    r = big.client.patch(
        f"{BASE}/config/series/{big.sid}/products/{pid}",
        json={"selling_price": "123.00", "max_discount_pct": "5"},
    )
    assert r.status_code == 200, r.text
    again = _page(big, big.sid, page=2)
    row = next(x for x in again["data"] if x["product_code"] == target["product_code"])
    assert Decimal(str(row["selling_price"])) == Decimal("123.00")
    assert again["pagination"]["total"] == 121
    # a named out-of-scope product adds one to the total
    big.client.post(
        f"{BASE}/config/series/{big.sid}/products",
        json={"codes": [big.c_x.product_code], "mode": "append"},
    )
    assert _page(big, big.sid)["pagination"]["total"] == 122


def test_b7_empty_filters_never_list_another_companys_products(world):
    from sqlalchemy import text

    from app.models.base import company_scope

    other_id = _uid()
    world.db.execute(
        text("insert into companies (id, code, name) values (:id, :code, :name)"),
        {"id": other_id, "code": f"ZZ{_uid()[:4]}", "name": f"{MARK} Other"},
    )
    with company_scope(world.db, frozenset({other_id})):
        theirs = _product(world.db, "ZZF-THEIRS-1", world.x.id, world.uom, None)
        assert theirs.company_id == other_id
    world.db.commit()
    s = _series(world, [], [])
    body = _page(world, s["id"], limit=100)
    assert body["pagination"]["total"] == len(_codes(world, s["id"]))
    assert "ZZF-THEIRS-1" not in _codes(world, s["id"])
    assert _page(world, s["id"], query="ZZF-THEIRS")["pagination"]["total"] == 0
    assert _in(world, s["id"], world.n_x) is True
    assert _in(world, s["id"], theirs) is False
