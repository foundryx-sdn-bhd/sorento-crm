"""SPEC-PROMO-PARAMS: `spec_constraints` and `has_active_promotion` on the product list and
stock balance routes.

Card: `documentation/plans/chatbot/CARD-spec-promo-params-12oct.md` section 2 "MCP params".

RED for Phase 2: neither `GET /api/v1/master-data/products` nor `GET /api/v1/inventory/stock/balance`
declares `spec_constraints` or `has_active_promotion`, so FastAPI silently ignores both and every
filtered call returns the unfiltered set (extra rows, status 200 where a 422 is promised, no
`spec_alternatives` key). Each assertion below fails against today's code for one of those reasons,
never an import or fixture error.

Harness: `blank_session` + TestClient with get_db / get_current_user_or_api_key overridden
(`tests/test_product_discontinued_at_list.py`), seeding helpers from `tests/_mc_lookup_seed.py`.
Postgres only; every row is seeded here and carries the ZZTSPR stem; assertions look at those
codes only.
"""
from __future__ import annotations

import json
import uuid
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.dependencies import get_current_user, get_current_user_or_api_key, get_db
from app.main import app
from app.models.base import set_company_scope
from app.models.marketing import Promotion, PromotionGroup, PromotionProduct
from app.models.product_spec import ProductSpecifications
from app.services.company_scope import DEFAULT_COMPANY_ID, register_company_scope_listeners
from app.services.company_scope_resolver import apply_company_scope
from app.services.product_spec_registry import seed_spec_registry
from app.services.user_service import UserPermissionService

from tests._mc_lookup_seed import MOCHA_ID, product, seed_mocha, stock, warehouse
from tests._pg_fixture import blank_session

STEM = "ZZTSPR"
PRODUCTS = "/api/v1/master-data/products"
BALANCE = "/api/v1/inventory/stock/balance"
TODAY = date.today()


# --------------------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def _scope_listeners():
    register_company_scope_listeners()


@pytest.fixture
def db():
    with blank_session() as session:
        set_company_scope(session, frozenset({DEFAULT_COMPANY_ID}))
        seed_spec_registry(session)
        session.flush()
        yield session


@pytest.fixture
def api(db, monkeypatch):
    def _override_get_db():
        yield db

    principal = {"id": str(uuid.uuid4()), "email": "zzt-spr@test.com"}

    async def _override_scope():
        scope = frozenset({DEFAULT_COMPANY_ID})
        set_company_scope(db, scope)
        return scope

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = lambda: principal
    app.dependency_overrides[get_current_user_or_api_key] = lambda: principal
    app.dependency_overrides[apply_company_scope] = _override_scope
    monkeypatch.setattr(UserPermissionService, "check_user_has_permission", lambda self, uid, slug: True)
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------------------- #
# Seeding helpers
# --------------------------------------------------------------------------------------- #


def _p(db, tag: str, specs: dict | None = None, *, company_id: str = DEFAULT_COMPANY_ID):
    """A product with code ZZTSPR-<tag>-<n>; `specs` is the `values` JSONB, None = no spec row."""
    row = product(db, company_id=company_id, code=f"{STEM}-{tag}-{uuid.uuid4().hex[:6]}")
    if specs is not None:
        db.add(ProductSpecifications(id=str(uuid.uuid4()), product_id=row.id, values=specs))
        db.flush()
    return row


def _num(v, unit="mm"):
    return {"value": v, "unit": unit}


def _enum(v):
    return {"value": v}


def _promo(db, prod, *, is_active=True, start=None, end=None, company_id=DEFAULT_COMPANY_ID):
    promo = Promotion(
        id=str(uuid.uuid4()),
        description=f"{STEM} promo",
        is_active=is_active,
        start_date=start,
        end_date=end,
        company_id=company_id,
    )
    db.add(promo)
    db.flush()
    group = PromotionGroup(id=uuid.uuid4(), promotion_id=promo.id, group_name="G", company_id=company_id)
    db.add(group)
    db.flush()
    db.add(
        PromotionProduct(
            id=str(uuid.uuid4()),
            promotion_id=promo.id,
            promotion_group_id=group.id,
            product_id=prod.id,
            company_id=company_id,
        )
    )
    db.flush()
    return promo


def _constraints(*items) -> dict:
    return {"spec_constraints": json.dumps(list(items))}


def _list(api, db, rows, **params):
    """GET the product list scoped to the seeded rows; returns the response."""
    return api.get(PRODUCTS, params={"product_ids": [r.id for r in rows], "limit": 100, **params})


def _codes(resp) -> set[str]:
    assert resp.status_code == 200, resp.text
    return {row["product_code"] for row in resp.json()["data"]}


def _codes_of(rows) -> set[str]:
    return {r.product_code for r in rows}


def _grating_world(db):
    g250 = _p(db, "G250", {"product_type": _enum("floor_grating"), "dim_length": _num(250)})
    g300 = _p(db, "G300", {"product_type": _enum("floor_grating"), "dim_length": _num(300)})
    trap = _p(db, "TRAP", {"product_type": _enum("floor_trap"), "dim_length": _num(250)})
    db.commit()
    return g250, g300, trap


# --------------------------------------------------------------------------------------- #
# 1-3: single-key matching
# --------------------------------------------------------------------------------------- #


def test_enum_eq_returns_only_matching_products_case_insensitively(api, db):
    """RED today: `spec_constraints` is ignored, so the floor_trap row comes back too."""
    g250, g300, trap = _grating_world(db)
    rows = [g250, g300, trap]
    want = _codes_of([g250, g300])

    lower = _list(api, db, rows, **_constraints({"key": "product_type", "op": "eq", "value": "floor_grating"}))
    assert _codes(lower) == want

    upper = _list(api, db, rows, **_constraints({"key": "product_type", "op": "eq", "value": "FLOOR_GRATING"}))
    assert _codes(upper) == want


def test_numeric_eq_is_exact_and_op_defaults_to_eq(api, db):
    """RED today: ignored, so both gratings (and the trap) come back instead of the 250 one."""
    g250, g300, trap = _grating_world(db)
    rows = [g250, g300, trap]

    explicit = _list(
        api, db, rows,
        **_constraints(
            {"key": "product_type", "op": "eq", "value": "floor_grating"},
            {"key": "dim_length", "op": "eq", "value": 250},
        ),
    )
    assert _codes(explicit) == _codes_of([g250])

    defaulted = _list(
        api, db, rows,
        **_constraints(
            {"key": "product_type", "value": "floor_grating"},
            {"key": "dim_length", "value": 250},
        ),
    )
    assert _codes(defaulted) == _codes_of([g250])


def test_numeric_between_gte_lte(api, db):
    """RED today: ignored, so the 300 row appears in every case."""
    g250, g300, _trap = _grating_world(db)
    rows = [g250, g300]

    between = _list(api, db, rows, **_constraints({"key": "dim_length", "op": "between", "value": [240, 260]}))
    assert _codes(between) == _codes_of([g250])

    gte = _list(api, db, rows, **_constraints({"key": "dim_length", "op": "gte", "value": 280}))
    assert _codes(gte) == _codes_of([g300])

    lte = _list(api, db, rows, **_constraints({"key": "dim_length", "op": "lte", "value": 260}))
    assert _codes(lte) == _codes_of([g250])


# --------------------------------------------------------------------------------------- #
# 4-5: AND, and a missing spec row
# --------------------------------------------------------------------------------------- #


def test_constraints_and_together(api, db):
    """RED today: ignored, so white basins and a black tap come back."""
    black_basin = _p(db, "BB", {"product_type": _enum("basin"), "finish": _enum("black")})
    white_basin = _p(db, "WB", {"product_type": _enum("basin"), "finish": _enum("white")})
    black_tap = _p(db, "BT", {"product_type": _enum("tap"), "finish": _enum("black")})
    db.commit()

    resp = _list(
        api, db, [black_basin, white_basin, black_tap],
        **_constraints(
            {"key": "finish", "op": "eq", "value": "black"},
            {"key": "product_type", "op": "eq", "value": "basin"},
        ),
    )
    assert _codes(resp) == _codes_of([black_basin])


def test_product_without_spec_row_is_excluded_when_a_constraint_is_set(api, db):
    """RED today: ignored, so the product with no product_specifications row is returned."""
    has = _p(db, "HAS", {"product_type": _enum("floor_grating")})
    bare = _p(db, "BARE", None)
    db.commit()

    resp = _list(api, db, [has, bare], **_constraints({"key": "product_type", "op": "eq", "value": "floor_grating"}))
    assert _codes(resp) == _codes_of([has])


# --------------------------------------------------------------------------------------- #
# 6: validation
# --------------------------------------------------------------------------------------- #


def test_unknown_key_is_422_and_names_the_key(api, db):
    """RED today: ignored, so the route answers 200."""
    g250, _g300, _trap = _grating_world(db)
    resp = _list(api, db, [g250], **_constraints({"key": "zzt_not_a_key", "op": "eq", "value": 1}))
    assert resp.status_code == 422, resp.text
    assert "zzt_not_a_key" in resp.text


def test_malformed_json_is_rejected(api, db):
    """RED today: ignored, so the route answers 200."""
    g250, _g300, _trap = _grating_world(db)
    resp = _list(api, db, [g250], spec_constraints="[{not json")
    assert resp.status_code in (400, 422), resp.text


def test_unknown_op_is_422(api, db):
    """RED today: ignored, so the route answers 200."""
    g250, _g300, _trap = _grating_world(db)
    resp = _list(api, db, [g250], **_constraints({"key": "dim_length", "op": "approx", "value": 250}))
    assert resp.status_code == 422, resp.text


# --------------------------------------------------------------------------------------- #
# 7: spec_alternatives on zero rows
# --------------------------------------------------------------------------------------- #


def test_zero_rows_carries_spec_alternatives_for_the_values_that_exist(api, db):
    """RED today: ignored, so rows come back and there is no `spec_alternatives` key."""
    g250, g300, trap = _grating_world(db)
    rows = [g250, g300, trap]

    miss = _list(
        api, db, rows,
        **_constraints(
            {"key": "product_type", "op": "eq", "value": "floor_grating"},
            {"key": "dim_length", "op": "eq", "value": 999},
        ),
    )
    assert miss.status_code == 200, miss.text
    body = miss.json()
    assert body["data"] == []
    alts = {a["key"]: a["values"] for a in body.get("spec_alternatives", [])}
    assert "dim_length" in alts, body
    assert {250, 300} <= set(alts["dim_length"])

    hit = _list(api, db, rows, **_constraints({"key": "dim_length", "op": "eq", "value": 250}))
    assert hit.status_code == 200, hit.text
    assert hit.json()["data"], "the 250 rows exist"
    assert not hit.json().get("spec_alternatives")


def test_zero_rows_reports_spec_unrecorded_so_a_miss_is_not_read_as_none_exists(api, db):
    """RED today: ignored, so rows come back and neither `spec_alternatives` nor
    `spec_unrecorded` exists. Contract: per constrained key, the count of products matching
    the OTHER constraints that have no value for that key."""
    sized = [
        _p(db, "U100", {"product_type": _enum("floor_grating"), "dim_length": _num(100)}),
        _p(db, "U150", {"product_type": _enum("floor_grating"), "dim_length": _num(150)}),
    ]
    unsized = [_p(db, f"UNS{i}", {"product_type": _enum("floor_grating")}) for i in range(3)]
    db.commit()

    resp = _list(
        api, db, sized + unsized,
        **_constraints(
            {"key": "product_type", "op": "eq", "value": "floor_grating"},
            {"key": "dim_length", "op": "eq", "value": 250},
        ),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["data"] == []
    alts = {a["key"]: a["values"] for a in body.get("spec_alternatives", [])}
    assert sorted(alts.get("dim_length", [])) == [100, 150], body
    assert body.get("spec_unrecorded") == {"dim_length": 3}, body


# --------------------------------------------------------------------------------------- #
# 8-9: has_active_promotion
# --------------------------------------------------------------------------------------- #


def test_has_active_promotion_keeps_only_live_promotions(api, db):
    """RED today: ignored, so every seeded product comes back."""
    live = _p(db, "LIVE")
    open_dates = _p(db, "OPEN")
    ended = _p(db, "ENDED")
    inactive = _p(db, "OFF")
    future = _p(db, "FUT")
    none = _p(db, "NONE")
    _promo(db, live, start=TODAY - timedelta(days=5), end=TODAY + timedelta(days=5))
    _promo(db, open_dates, start=None, end=None)
    _promo(db, ended, start=TODAY - timedelta(days=9), end=TODAY - timedelta(days=1))
    _promo(db, inactive, is_active=False, start=TODAY - timedelta(days=5), end=TODAY + timedelta(days=5))
    _promo(db, future, start=TODAY + timedelta(days=3), end=TODAY + timedelta(days=9))
    db.commit()
    rows = [live, open_dates, ended, inactive, future, none]

    on = _list(api, db, rows, has_active_promotion="true")
    assert _codes(on) == _codes_of([live, open_dates])

    for params in ({}, {"has_active_promotion": "false"}):
        off = _list(api, db, rows, **params)
        assert _codes(off) == _codes_of(rows), "absent or false must not filter"


def test_has_active_promotion_ignores_a_promotion_of_another_company(api, db):
    """RED today: ignored. Mirrors `_leg_promotion`: the promotion must share the product's company."""
    seed_mocha(db)
    ours = _p(db, "OURS")
    foreign_only = _p(db, "FOREIGN")
    _promo(db, ours)
    _promo(db, foreign_only, company_id=MOCHA_ID)
    db.commit()

    resp = _list(api, db, [ours, foreign_only], has_active_promotion="true")
    assert _codes(resp) == _codes_of([ours])


# --------------------------------------------------------------------------------------- #
# 10: stock route
# --------------------------------------------------------------------------------------- #


def _stock_codes(resp, rows) -> set[str]:
    assert resp.status_code == 200, resp.text
    by_id = {r.id: r.product_code for r in rows}
    return {by_id[row["product_id"]] for row in resp.json()["data"] if row["product_id"] in by_id}


def test_stock_balance_spec_constraints_narrow_rows(api, db):
    """RED today: ignored, so stock rows of the 300 grating and the trap come back too."""
    g250, g300, trap = _grating_world(db)
    wh = warehouse(db, company_id=DEFAULT_COMPANY_ID)
    for p in (g250, g300, trap):
        stock(db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=wh.id, on_hand=5)
    db.commit()

    resp = api.get(
        BALANCE,
        params={
            "product_ids": [g250.id, g300.id, trap.id],
            "limit": 100,
            **_constraints(
                {"key": "product_type", "op": "eq", "value": "floor_grating"},
                {"key": "dim_length", "op": "eq", "value": 250},
            ),
        },
    )
    assert _stock_codes(resp, [g250, g300, trap]) == _codes_of([g250])


def test_stock_balance_has_active_promotion_narrows_rows(api, db):
    """RED today: ignored, so the stock row of the product with no live promotion comes back."""
    on_promo = _p(db, "SPROMO")
    ended = _p(db, "SENDED")
    wh = warehouse(db, company_id=DEFAULT_COMPANY_ID)
    for p in (on_promo, ended):
        stock(db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=wh.id, on_hand=5)
    _promo(db, on_promo, start=TODAY - timedelta(days=1), end=TODAY + timedelta(days=1))
    _promo(db, ended, start=TODAY - timedelta(days=9), end=TODAY - timedelta(days=1))
    db.commit()

    resp = api.get(
        BALANCE,
        params={"product_ids": [on_promo.id, ended.id], "limit": 100, "has_active_promotion": "true"},
    )
    assert _stock_codes(resp, [on_promo, ended]) == _codes_of([on_promo])


# --------------------------------------------------------------------------------------- #
# 11: no params = unchanged
# --------------------------------------------------------------------------------------- #


def test_without_either_param_both_routes_return_the_full_set(api, db):
    """Guard (passes today and must keep passing): a non-matching product still appears."""
    g250, g300, trap = _grating_world(db)
    wh = warehouse(db, company_id=DEFAULT_COMPANY_ID)
    for p in (g250, g300, trap):
        stock(db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=wh.id, on_hand=5)
    db.commit()
    rows = [g250, g300, trap]

    assert _codes(_list(api, db, rows)) == _codes_of(rows)
    resp = api.get(BALANCE, params={"product_ids": [r.id for r in rows], "limit": 100})
    assert _stock_codes(resp, rows) == _codes_of(rows)


# --------------------------------------------------------------------------------------- #
# Tier: has_active_promotion honours the caller's access_levels (SEC-S1 rule kept)
# --------------------------------------------------------------------------------------- #


def _tier_world(db):
    """Two products, each in a live promotion restricted to ONE tier."""
    from app.models.access import ContactAccessType

    db.add(ContactAccessType(code="zzt_end_user", name="ZZT End User Tier"))
    db.add(ContactAccessType(code="zzt_dealer", name="ZZT Dealer Tier"))
    db.flush()
    prod_a = _p(db, "TIERA")
    prod_b = _p(db, "TIERB")
    _promo(db, prod_a).access_levels = ["zzt_end_user"]
    _promo(db, prod_b).access_levels = ["zzt_dealer"]
    wh = warehouse(db, company_id=DEFAULT_COMPANY_ID)
    for p in (prod_a, prod_b):
        stock(db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=wh.id, on_hand=5)
    db.commit()
    return prod_a, prod_b


@pytest.mark.parametrize("route", ["products", "stock"])
def test_has_active_promotion_counts_only_promotions_open_to_the_callers_tier(api, db, route):
    """RED today: `access_levels` is not a param of either route, so it is ignored and both
    products (A: end_user promo, B: dealer promo) come back for a dealer."""
    prod_a, prod_b = _tier_world(db)
    rows = [prod_a, prod_b]

    def ask(**extra):
        params = {"product_ids": [r.id for r in rows], "limit": 100, "has_active_promotion": "true", **extra}
        resp = api.get(PRODUCTS if route == "products" else BALANCE, params=params)
        return _codes(resp) if route == "products" else _stock_codes(resp, rows)

    assert ask(access_levels="ZZT Dealer Tier") == _codes_of([prod_b])
    assert ask(access_levels="zzt_end_user") == _codes_of([prod_a])
    assert ask(access_levels=["zzt_dealer", "zzt_end_user"]) == _codes_of(rows), "repeated list accepted"
    assert ask(access_levels="zzt-no-such-tier-zz") == set(), "unknown tier matches nothing"
    assert ask() == _codes_of(rows), "absent access_levels = no tier restriction"


# --------------------------------------------------------------------------------------- #
# Slice 2: virtual `size` key (card section 9, slice 1), inactive keys, promotion-products
# --------------------------------------------------------------------------------------- #

GRATING = {"key": "product_type", "op": "eq", "value": "floor_grating"}


def _g(db, tag, **mm):
    """A floor grating carrying the given mm keys, e.g. _g(db, "A", dim_length=250)."""
    values = {"product_type": _enum("floor_grating")}
    values.update({k: _num(v) for k, v in mm.items()})
    return _p(db, tag, values)


def _alts(body) -> dict:
    return {a["key"]: a["values"] for a in body.get("spec_alternatives", [])}


def test_size_resolves_to_the_one_mm_key_that_holds_the_value(api, db):
    """RED today: `size` is not a registry key, so the route answers 422 (unknown key)."""
    a = _g(db, "SA", dim_length=250)
    b = _g(db, "SB", dim_length=100)
    db.commit()

    resp = _list(api, db, [a, b], **_constraints(GRATING, {"key": "size", "op": "eq", "value": 250}))
    assert _codes(resp) == _codes_of([a])
    assert resp.json().get("spec_resolved") == {"size": "dim_length"}


def test_size_held_by_two_keys_matches_either_axis(api, db):
    """Owner ruling (12 Oct 2026, after hand test step 1): a bare mm size matches products holding
    it on ANY dimension axis; no "Length or Width?" question. Was: no rows plus `spec_ambiguous`."""
    a = _g(db, "AA", dim_length=250)
    c = _g(db, "AC", dim_width=250)
    other = _g(db, "AO", dim_length=100)
    db.commit()

    resp = _list(api, db, [a, c, other], **_constraints(GRATING, {"key": "size", "op": "eq", "value": 250}))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert _codes(resp) == _codes_of([a, c])
    assert not body.get("spec_ambiguous"), body
    assert body.get("spec_resolved") == {"size": ["dim_length", "dim_width"]}, body


def test_an_axis_key_narrows_to_that_axis_only(api, db):
    """Owner ruling (12 Oct 2026): "600mm length" uses that axis only (guard)."""
    a = _g(db, "XA", dim_length=250)
    c = _g(db, "XC", dim_width=250)
    db.commit()

    resp = _list(api, db, [a, c], **_constraints(GRATING, {"key": "dim_length", "op": "eq", "value": 250}))
    assert _codes(resp) == _codes_of([a])


@pytest.mark.parametrize("path", [PRODUCTS, "/api/v1/inventory/stock/balance"])
def test_a_spec_filtered_call_states_its_page(api, db, path):
    """Owner ruling (12 Oct 2026): a few matches first, then "reply to see more". The chatbot
    pages a spec-filtered answer, so the route states the page it returned (`spec_page`): the
    total that matched and the page/limit asked. Absent without `spec_constraints`."""
    rows = [_g(db, f"PG{i}", dim_length=250) for i in range(3)]
    wh = warehouse(db, company_id=DEFAULT_COMPANY_ID)
    for p in rows:
        stock(db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=wh.id, on_hand=5)
    db.commit()

    resp = api.get(path, params={"limit": 2, "page": 1, **_constraints(GRATING, {"key": "size", "op": "eq", "value": 250})})
    assert resp.status_code == 200, resp.text
    assert resp.json().get("spec_page") == {"page": 1, "limit": 2, "total": 3}, resp.json().get("spec_page")
    plain = api.get(path, params={"limit": 2, "page": 1})
    assert "spec_page" not in plain.json()
    assert rows


def test_size_held_by_no_key_lists_every_mm_key_and_the_unrecorded_count(api, db):
    """RED today: `size` is an unknown key (422), so neither alternatives nor unrecorded exist."""
    rows = [
        _g(db, "NL", dim_length=100),
        _g(db, "NW", dim_width=120),
        _g(db, "N0A"),
        _g(db, "N0B"),
    ]
    db.commit()

    resp = _list(api, db, rows, **_constraints(GRATING, {"key": "size", "op": "eq", "value": 300}))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["data"] == []
    alts = _alts(body)
    assert alts.get("dim_length") == [100], body
    assert alts.get("dim_width") == [120], body
    assert body.get("spec_unrecorded") == {"size": 2}, body


def test_size_candidates_are_only_active_numeric_mm_keys(api, db):
    """RED today: `size` is an unknown key (422). A litre key and an inactive mm key both
    hold 250 on a grating, yet only the active mm key `dim_length` may be a candidate."""
    from app.models.product_spec import ProductSpecRegistry

    db.query(ProductSpecRegistry).filter(ProductSpecRegistry.spec_key == "dim_height").update(
        {"is_active": False}
    )
    db.flush()
    mm = _g(db, "MM", dim_length=250)
    litre = _p(db, "LITRE", {"product_type": _enum("floor_grating"), "capacity_litre": _num(250, "L")})
    off = _p(db, "OFF", {"product_type": _enum("floor_grating"), "dim_height": _num(250)})
    db.commit()

    resp = _list(api, db, [mm, litre, off], **_constraints(GRATING, {"key": "size", "op": "eq", "value": 250}))
    assert _codes(resp) == _codes_of([mm])
    body = resp.json()
    assert body.get("spec_resolved") == {"size": "dim_length"}
    assert not body.get("spec_ambiguous")


def _seat_cover_row(db):
    from app.models.product_spec import ProductSpecRegistry

    db.add(
        ProductSpecRegistry(
            id=str(uuid.uuid4()),
            spec_key="seat_cover",
            label="Seat cover",
            data_type="enum",
            allowed_values=["zzt_x"],
            is_active=False,
        )
    )
    db.flush()


def test_inactive_registry_key_is_rejected_by_name(api, db):
    """Guard for the inactive-key rule (may already pass after slice 1): a registry key that
    is switched off is a 422 naming it, never a silent filter."""
    _seat_cover_row(db)
    a = _g(db, "IK", dim_length=250)
    db.commit()

    resp = _list(api, db, [a], **_constraints({"key": "seat_cover", "op": "eq", "value": "zzt_x"}))
    assert resp.status_code == 422, resp.text
    assert "seat_cover" in resp.text


def test_spec_alternatives_items_carry_label_and_unit(api, db):
    """A7. RED today: items are `{key, values}` only, so the presenter cannot print
    "Length" or "mm" without a second lookup."""
    rows = [_g(db, "LU1", dim_length=100), _g(db, "LU2", dim_length=150)]
    db.commit()

    resp = _list(api, db, rows, **_constraints(GRATING, {"key": "dim_length", "op": "eq", "value": 999}))
    assert resp.status_code == 200, resp.text
    item = next(a for a in resp.json()["spec_alternatives"] if a["key"] == "dim_length")
    assert item["label"] == "Length"
    assert item["unit"] == "mm"
    assert item["values"] == [100, 150]


def test_promotion_products_route_filters_lines_by_spec_constraints(api, db, monkeypatch):
    """A5. RED today: `GET /marketing/promotion-products` has no `spec_constraints` param,
    so it is ignored and the tap line comes back with the basin line."""
    monkeypatch.setattr(
        UserPermissionService,
        "get_user_permission_slugs",
        lambda self, uid: {"marketing.promotion_products.view"},
    )
    basin = _p(db, "PPB", {"class": _enum("Basin")})
    tap = _p(db, "PPT", {"class": _enum("Tap")})
    promo = _promo(db, basin, start=TODAY - timedelta(days=1), end=TODAY + timedelta(days=1))
    group = promo.promotion_groups[0] if promo.promotion_groups else None
    assert group is not None
    db.add(
        PromotionProduct(
            id=str(uuid.uuid4()),
            promotion_id=promo.id,
            promotion_group_id=group.id,
            product_id=tap.id,
            company_id=DEFAULT_COMPANY_ID,
        )
    )
    db.commit()

    resp = api.get(
        "/api/v1/marketing/promotion-products/",
        params={
            "promotion_ids": [promo.id],
            "active": "true",
            "limit": 100,
            **_constraints({"key": "class", "op": "eq", "value": "Basin"}),
        },
    )
    assert resp.status_code == 200, resp.text
    codes = {row["product"]["product_code"] for row in resp.json()["data"]}
    assert codes == _codes_of([basin])


# --------------------------------------------------------------------------------------- #
# Slice 2b: company scope and spec visibility on the miss detail and on `size`
# --------------------------------------------------------------------------------------- #


def _bare_list(api, **params):
    """The chatbot call shape: no resolver product_ids, just the constraints."""
    return api.get(PRODUCTS, params={"limit": 100, **params})


def test_miss_detail_and_size_ignore_products_of_another_company(api, db):
    """S1. RED today: `spec_miss_detail` and the size resolver read `product_specifications`
    with no join to the scoped `products`, so a Mocha grating holding dim_length 999 leaks
    into `spec_alternatives`, `spec_unrecorded` and `spec_resolved`."""
    seed_mocha(db)
    ours_sized = _g(db, "S1OURS", dim_length=100)
    ours_bare = _g(db, "S1BARE")
    foreign_sized = _p(
        db, "S1FSIZED",
        {"product_type": _enum("floor_grating"), "dim_length": _num(999)},
        company_id=MOCHA_ID,
    )
    foreign_bare = _p(db, "S1FBARE", {"product_type": _enum("floor_grating")}, company_id=MOCHA_ID)
    db.commit()
    assert foreign_sized.company_id == MOCHA_ID and foreign_bare.company_id == MOCHA_ID

    miss = _bare_list(api, **_constraints(GRATING, {"key": "dim_length", "op": "eq", "value": 300}))
    assert miss.status_code == 200, miss.text
    body = miss.json()
    assert body["data"] == []
    assert _alts(body).get("dim_length") == [100], body
    assert "999" not in json.dumps(body.get("spec_alternatives")), body
    assert body.get("spec_unrecorded") == {"dim_length": 1}, "the foreign bare grating must not count"

    size = _bare_list(api, **_constraints(GRATING, {"key": "size", "op": "eq", "value": 999}))
    assert size.status_code == 200, size.text
    sbody = size.json()
    assert sbody["data"] == []
    assert not sbody.get("spec_resolved"), sbody
    assert not sbody.get("spec_ambiguous"), sbody
    assert "999" not in json.dumps(sbody.get("spec_alternatives")), sbody
    assert sbody.get("spec_unrecorded") == {"size": 1}, sbody


def _hide_dim_width_contact(db):
    """A respond contact whose per-contact spec policy hides dim_width."""
    from app.models.access import RespondContact, SpecVisibilityPolicy
    from tests._pg_fixture import unique_code

    contact = RespondContact(
        id=unique_code("CONTACT"),
        phone_number=f"+60{uuid.uuid4().int % 10**9:09d}",
        name="ZZT Spec Contact",
    )
    db.add(contact)
    db.flush()
    db.add(
        SpecVisibilityPolicy(
            id=str(uuid.uuid4()), contact_id=contact.id, spec_keys=None, excluded_spec_keys=["dim_width"]
        )
    )
    db.flush()
    return contact


def _width_world(db):
    a = _g(db, "VL250", dim_length=250)
    c = _g(db, "VW250", dim_width=250)
    d = _g(db, "VW120", dim_width=120)
    db.commit()
    return a, c, d


def test_hidden_key_is_never_a_size_candidate_or_an_alternative(api, db):
    """S2 (i). RED today: `contact_id` is not read by the products route, so dim_width stays
    visible: 250 is ambiguous (length or width) and 120 lists dim_width as an alternative."""
    contact = _hide_dim_width_contact(db)
    a, c, d = _width_world(db)
    who = {"contact_id": contact.id, "space_id": "364817"}

    shared = _bare_list(api, **who, **_constraints(GRATING, {"key": "size", "op": "eq", "value": 250}))
    assert _codes(shared) == _codes_of([a]), shared.text
    sbody = shared.json()
    assert sbody.get("spec_resolved") == {"size": "dim_length"}, sbody
    assert not sbody.get("spec_ambiguous"), sbody
    assert "dim_width" not in shared.text

    only_width = _bare_list(api, **who, **_constraints(GRATING, {"key": "size", "op": "eq", "value": 120}))
    assert only_width.status_code == 200, only_width.text
    wbody = only_width.json()
    assert wbody["data"] == []
    assert not wbody.get("spec_resolved"), wbody
    assert "dim_width" not in only_width.text, wbody
    assert "width" not in only_width.text.lower(), wbody
    assert _alts(wbody).get("dim_length") == [250], wbody


def test_a_constraint_on_a_hidden_key_is_not_applied_and_not_echoed(api, db):
    """S2 (ii). RED today: the dim_width constraint is applied as a filter (only the 250 width
    grating comes back). A hidden key neither ranks nor prints, so the constraint is dropped
    and all three gratings match; nothing in the body names the key."""
    contact = _hide_dim_width_contact(db)
    a, c, d = _width_world(db)

    resp = _bare_list(
        api, contact_id=contact.id, space_id="364817",
        **_constraints(GRATING, {"key": "dim_width", "op": "eq", "value": 250}),
    )
    assert _codes(resp) == _codes_of([a, c, d]), resp.text
    assert "dim_width" not in resp.text.replace("dim_width_", "")  # no echo outside the row data
    body = resp.json()
    for extra in ("spec_alternatives", "spec_unrecorded", "spec_resolved", "spec_ambiguous"):
        assert "dim_width" not in json.dumps(body.get(extra)), body


def test_staff_call_without_contact_still_sees_dim_width(api, db):
    """S2 (iii) guard (may pass today): no contact_id, nothing hidden."""
    a, c, d = _width_world(db)

    width = _bare_list(api, **_constraints(GRATING, {"key": "dim_width", "op": "eq", "value": 250}))
    assert _codes(width) == _codes_of([c])

    size = _bare_list(api, **_constraints(GRATING, {"key": "size", "op": "eq", "value": 250}))
    assert size.status_code == 200, size.text
    # Owner ruling (12 Oct 2026): either axis matches; no `spec_ambiguous`.
    assert _codes(size) == _codes_of([a, c]), size.text
    assert not size.json().get("spec_ambiguous")


# --------------------------------------------------------------------------------------- #
# Owner Q4 option (a), 12 Oct: promotion-products takes brand_ids and promotion_types
# --------------------------------------------------------------------------------------- #


def test_promotion_products_route_narrows_by_brand_and_kind_with_spec_constraints(api, db, monkeypatch):
    """R1. RED today: `brand_ids` and `promotion_types` are not params of
    `GET /marketing/promotion-products`, so they are ignored and every line comes back."""
    from app.models.marketing import PromotionType
    from app.models.product import Brand

    monkeypatch.setattr(
        UserPermissionService, "get_user_permission_slugs", lambda self, uid: {"marketing.promotion_products.view"}
    )
    standard = PromotionType(
        type_code="zzt_standard", type_name="ZZT Standard", show_expired=True, match_markers=[], match_priority=90,
        is_default=True,
    )
    flyer = PromotionType(
        type_code="zzt_flyer", type_name="ZZT Flyer", show_expired=True, match_markers=[], match_priority=40,
        is_default=False,
    )
    db.add_all([standard, flyer])
    b1 = Brand(id=str(uuid.uuid4()), brand_code=f"ZZT{uuid.uuid4().hex[:6]}", brand_name="ZZT One", company_id=DEFAULT_COMPANY_ID)
    b2 = Brand(id=str(uuid.uuid4()), brand_code=f"ZZT{uuid.uuid4().hex[:6]}", brand_name="ZZT Two", company_id=DEFAULT_COMPANY_ID)
    db.add_all([b1, b2])
    db.flush()
    p1 = _p(db, "RB1", {"class": _enum("Basin")})
    p2 = _p(db, "RB2", {"class": _enum("Basin")})
    p3 = _p(db, "RB3", {"class": _enum("Tap")})
    p1.brand_id, p2.brand_id, p3.brand_id = b1.id, b2.id, b1.id
    db.flush()
    live = dict(start=TODAY - timedelta(days=1), end=TODAY + timedelta(days=1))
    promo_std = _promo(db, p1, **live)
    promo_std.promotion_type_id = standard.id
    group = promo_std.promotion_groups[0]
    db.add(
        PromotionProduct(
            id=str(uuid.uuid4()), promotion_id=promo_std.id, promotion_group_id=group.id,
            product_id=p2.id, company_id=DEFAULT_COMPANY_ID,
        )
    )
    promo_fly = _promo(db, p3, **live)
    promo_fly.promotion_type_id = flyer.id
    db.commit()

    def lines(**params):
        resp = api.get("/api/v1/marketing/promotion-products/", params={"active": "true", "limit": 100, **params})
        assert resp.status_code == 200, resp.text
        return {row["product"]["product_code"] for row in resp.json()["data"]}

    assert lines() == _codes_of([p1, p2, p3]), "no narrowing: every live line"
    assert lines(brand_ids=[b1.id]) == _codes_of([p1, p3])
    assert lines(brand_ids=[b2.id]) == _codes_of([p2])
    assert lines(promotion_types=["zzt_flyer"]) == _codes_of([p3])
    assert lines(brand_ids=[b1.id], promotion_types=["zzt_standard"]) == _codes_of([p1])
    assert lines(brand_ids=[b1.id], **_constraints({"key": "class", "op": "eq", "value": "Tap"})) == _codes_of([p3])
    assert lines(
        brand_ids=[b1.id], promotion_types=["zzt_standard"],
        **_constraints({"key": "class", "op": "eq", "value": "Tap"}),
    ) == set()


def test_inactive_class_registry_row_is_rejected_and_an_absent_one_stays_accepted(api, db):
    """Owner Q4 option (a), 12 Oct: a `class` registry row switched off is a 422 naming it. RED if
    the route special-cases `class` and accepts it; an absent `class` row stays accepted as today
    (the seed has no `class` row in this schema)."""
    from app.models.product_spec import ProductSpecRegistry

    basin = _p(db, "CLS", {"class": _enum("Basin")})
    db.commit()
    present = db.query(ProductSpecRegistry).filter(ProductSpecRegistry.spec_key == "class").first()
    constraint = _constraints({"key": "class", "op": "eq", "value": "Basin"})

    if present is None:
        absent = _list(api, db, [basin], **constraint)
        assert _codes(absent) == _codes_of([basin]), absent.text
        db.add(
            ProductSpecRegistry(
                id=str(uuid.uuid4()), spec_key="class", label="Class", data_type="enum", allowed_values=[], is_active=False
            )
        )
    else:
        present.is_active = False
    db.commit()

    resp = _list(api, db, [basin], **constraint)
    assert resp.status_code == 422, resp.text
    assert "class" in resp.text


# --------------------------------------------------------------------------------------- #
# Security review round: caps, tier fail-closed, hostile JSON, type check at parse
# --------------------------------------------------------------------------------------- #


def _raw_list(api, raw: str, **params):
    return api.get(PRODUCTS, params={"limit": 100, "spec_constraints": raw, **params})


def _one(key="dim_length", op="eq", value=250):
    return {"key": key, "op": op, "value": value}


def test_s1_more_than_16_constraints_is_rejected(api, db):
    """RED today: no cap, so 17 constraints answer 200."""
    _grating_world(db)
    items = [_one(value=250 + i) for i in range(17)]
    resp = _raw_list(api, json.dumps(items))
    assert resp.status_code == 422, resp.text[:300]
    ok = _raw_list(api, json.dumps(items[:16]))
    assert ok.status_code == 200, ok.text[:300]


def test_s1_an_eq_value_list_over_50_is_rejected(api, db):
    """RED today: no cap on the value list."""
    _grating_world(db)
    resp = _raw_list(api, json.dumps([_one(value=list(range(51)))]))
    assert resp.status_code == 422, resp.text[:300]
    ok = _raw_list(api, json.dumps([_one(value=list(range(50)))]))
    assert ok.status_code == 200, ok.text[:300]


def test_s1_a_raw_string_over_4096_chars_is_rejected(api, db):
    """RED today: no length cap (a valid 5000 char list answers 200)."""
    _grating_world(db)
    padded = json.dumps([_one()]) + " " * 5000
    resp = _raw_list(api, padded)
    assert resp.status_code in (400, 422), resp.text[:300]


def test_s1_identical_duplicate_constraints_are_deduped(api, db):
    """Guard (may pass today): the same constraint twice answers like once."""
    g250, g300, trap = _grating_world(db)
    rows = [g250, g300, trap]
    once = _list(api, db, rows, **_constraints(GRATING, _one()))
    twice = _list(api, db, rows, **_constraints(GRATING, _one(), _one(), GRATING))
    assert twice.status_code == 200, twice.text[:300]
    assert _codes(twice) == _codes(once) == _codes_of([g250])


def _tier_contact_world(db):
    """A respond contact whose only access type is zzt_dealer, plus A (end_user promo),
    B (dealer promo) and C (no promo)."""
    from app.models.access import ContactAccessType, RespondContact
    from app.models.respond_workspace import RespondWorkspace
    from app.services.contact_access_type_service import ContactAccessTypeService

    db.add_all(
        [
            ContactAccessType(code="zzt_end_user", name="ZZT End User Tier"),
            ContactAccessType(code="zzt_dealer", name="ZZT Dealer Tier"),
        ]
    )
    suffix = uuid.uuid4().hex[:8]
    ws = RespondWorkspace(
        id=str(uuid.uuid4()), space_id=f"sp-{suffix}", name=f"zzt-ws-{suffix}", api_key_ciphertext="stub"
    )
    db.add(ws)
    db.flush()
    contact = RespondContact(
        id=str(uuid.uuid4()), phone_number=f"+60000{suffix[:6]}", name="ZZT Tier Contact",
        respond_io_id=f"rid-{suffix}", workspace_id=ws.id,
    )
    bare = RespondContact(
        id=str(uuid.uuid4()), phone_number=f"+60001{suffix[:6]}", name="ZZT No Tier Contact",
        respond_io_id=f"rid-bare-{suffix}", workspace_id=ws.id,
    )
    db.add_all([contact, bare])
    db.flush()
    ContactAccessTypeService(db).set_contact_access_codes(str(contact.id), ["zzt_dealer"])
    prod_a, prod_b, prod_c = _p(db, "T2A"), _p(db, "T2B"), _p(db, "T2C")
    _promo(db, prod_a).access_levels = ["zzt_end_user"]
    _promo(db, prod_b).access_levels = ["zzt_dealer"]
    wh = warehouse(db, company_id=DEFAULT_COMPANY_ID)
    for p in (prod_a, prod_b, prod_c):
        stock(db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=wh.id, on_hand=5)
    db.commit()
    return ws, contact, bare, [prod_a, prod_b, prod_c]


def _ids_in(node, known: dict) -> set[str]:
    """Product codes whose id appears anywhere in a JSON body (rows, summaries, availability)."""
    found: set[str] = set()
    if isinstance(node, dict):
        for v in node.values():
            found |= _ids_in(v, known)
    elif isinstance(node, list):
        for v in node:
            found |= _ids_in(v, known)
    elif isinstance(node, str) and node in known:
        found.add(known[node])
    return found


@pytest.mark.parametrize("route", ["products", "stock"])
def test_s2_has_active_promotion_without_access_levels_uses_the_contacts_own_tier(api, db, route):
    """RED today: with no `access_levels` the promotion clause carries no tier restriction, so the
    end_user-only promotion counts for a dealer contact (and an unresolvable or tier-less contact
    sees every live promotion instead of none)."""
    ws, contact, bare, prods = _tier_contact_world(db)
    prod_a, prod_b, prod_c = prods
    known = {p.id: p.product_code for p in prods}
    path = PRODUCTS if route == "products" else BALANCE

    def ask(**who):
        resp = api.get(
            path,
            params={"product_ids": [p.id for p in prods], "limit": 100, "has_active_promotion": "true", **who},
        )
        assert resp.status_code == 200, resp.text[:300]
        return _ids_in(resp.json(), known)

    mine = {"contact_id": contact.respond_io_id, "space_id": ws.space_id}
    assert ask(**mine) == _codes_of([prod_b]), "dealer contact: only the dealer promotion"
    assert ask(contact_id="rid-does-not-exist", space_id=ws.space_id) == set(), "unresolved contact: nothing"
    assert ask(contact_id=bare.respond_io_id, space_id=ws.space_id) == set(), "contact with no tier: nothing"
    assert ask() == _codes_of([prod_a, prod_b]), "staff call keeps no tier restriction"


def test_n1_deeply_nested_json_is_a_4xx_never_a_500(api, db):
    """Hostile input: 5000 nested brackets must not recurse into a 500."""
    _grating_world(db)
    resp = _raw_list(api, "[" * 5000 + "]" * 5000)
    assert 400 <= resp.status_code < 500, resp.text[:300]


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity"])
def test_n2_non_finite_numbers_are_422(api, db, token):
    """RED today: Python's json accepts these tokens and a non-finite value reaches the query."""
    _grating_world(db)
    raw = '[{"key":"dim_length","op":"eq","value":%s}]' % token
    resp = _raw_list(api, raw)
    assert resp.status_code == 422, resp.text[:300]
    raw_between = '[{"key":"dim_length","op":"between","value":[1,%s]}]' % token
    assert _raw_list(api, raw_between).status_code == 422


def test_n3_value_type_must_match_the_key_type(api, db):
    """RED today: a number on an enum key and text on a numeric key are accepted and silently
    match nothing. A numeric string stays rejected (strict)."""
    _grating_world(db)
    enum_number = _raw_list(api, json.dumps([{"key": "product_type", "op": "eq", "value": 5}]))
    assert enum_number.status_code == 422, enum_number.text[:300]
    numeric_text = _raw_list(api, json.dumps([_one(value="abc")]))
    assert numeric_text.status_code == 422, numeric_text.text[:300]
    numeric_string = _raw_list(api, json.dumps([_one(value="250")]))
    assert numeric_string.status_code == 422, numeric_string.text[:300]
    fine = _raw_list(api, json.dumps([_one(value=250)]))
    assert fine.status_code == 200, fine.text[:300]


def test_promotion_products_size_and_miss_detail_see_only_promoted_products(api, db, monkeypatch):
    """Reviewer round. RED today if size resolution and the miss detail read every product:
    unpromoted basin B (dim_width 600, dim_length 450) makes size 600 ambiguous, lists 450 as an
    alternative and inflates `spec_unrecorded` with unpromoted basin D."""
    monkeypatch.setattr(
        UserPermissionService, "get_user_permission_slugs", lambda self, uid: {"marketing.promotion_products.view"}
    )
    live = dict(start=TODAY - timedelta(days=1), end=TODAY + timedelta(days=1))
    promoted_a = _p(db, "PSA", {"class": _enum("Basin"), "dim_length": _num(600)})
    promoted_c = _p(db, "PSC", {"class": _enum("Basin")})  # promoted, no mm value recorded
    unpromoted_b = _p(db, "PSB", {"class": _enum("Basin"), "dim_width": _num(600), "dim_length": _num(450)})
    unpromoted_d = _p(db, "PSD", {"class": _enum("Basin")})  # not promoted, no mm value
    _promo(db, promoted_a, **live)
    _promo(db, promoted_c, **live)
    db.commit()
    assert unpromoted_b.id and unpromoted_d.id

    def ask(value):
        resp = api.get(
            "/api/v1/marketing/promotion-products/",
            params={
                "active": "true", "limit": 100,
                **_constraints({"key": "class", "op": "eq", "value": "Basin"}, {"key": "size", "op": "eq", "value": value}),
            },
        )
        assert resp.status_code == 200, resp.text[:300]
        return resp.json()

    hit = ask(600)
    assert {row["product"]["product_code"] for row in hit["data"]} == _codes_of([promoted_a]), hit
    assert hit.get("spec_resolved") == {"size": "dim_length"}, hit
    assert not hit.get("spec_ambiguous"), hit

    miss = ask(450)
    assert miss["data"] == []
    assert "450" not in json.dumps(miss.get("spec_alternatives")), miss
    assert _alts(miss).get("dim_length") == [600], miss
    assert miss.get("spec_unrecorded") == {"size": 1}, "only the promoted basin without a size counts"


def test_a_spec_filtered_promotion_call_lists_live_promotions_only(api, db, monkeypatch):
    """Crew live check on bec7d329: "which basins are on promotion?" listed a promotion that
    ended a month ago beside live ones (the serving policy keeps the newest expired one per
    kind). With a live promotion in the answer, the expired one goes (owner round 3: expired
    hidden beside live; with none live it still serves, `test_promo_round3_detection`)."""
    monkeypatch.setattr(
        UserPermissionService, "get_user_permission_slugs", lambda self, uid: {"marketing.promotion_products.view"}
    )
    live = _p(db, "PLV", {"class": _enum("Basin")})
    gone = _p(db, "PGN", {"class": _enum("Basin")})
    from app.models.marketing import PromotionType

    kept = PromotionType(id=str(uuid.uuid4()), type_code=f"ZZT{uuid.uuid4().hex[:8]}", type_name="ZZT kept", show_expired=True)
    db.add(kept)
    db.flush()
    _promo(db, live, start=TODAY - timedelta(days=1), end=TODAY + timedelta(days=1))
    expired = _promo(db, gone, start=TODAY - timedelta(days=120), end=TODAY - timedelta(days=30))
    expired.promotion_type_id = kept.id  # the only row of a kind that keeps its newest expired one
    db.commit()

    resp = api.get(
        "/api/v1/marketing/promotion-products/",
        params={"serving_policy": "true", "limit": 100, **_constraints({"key": "class", "op": "eq", "value": "Basin"})},
    )
    assert resp.status_code == 200, resp.text
    codes = {row["product"]["product_code"] for row in resp.json()["data"]}
    assert codes == _codes_of([live]), codes


def test_a_dealer_on_availability_gets_the_spec_filtered_products(db):
    """Crew live check on 045a0fea: a dealer (stock visibility `availability`) asking
    "600mm basin got stock?" got "no inventory matched these" while an office contact got the
    basins. The availability block's no-product guard (D35: a bare "what stock?" must not
    quantify the catalogue) also dropped a spec-filtered ask, whose set is already narrowed."""
    from app.services.inventory_service import StockService
    from tests.test_stock_availability_block import _contact, _policy_row

    wh = warehouse(db, company_id=DEFAULT_COMPANY_ID)
    hit = _p(db, "DAV1", {"class": _enum("Basin"), "dim_length": _num(600)})
    miss = _p(db, "DAV2", {"class": _enum("Basin"), "dim_length": _num(500)})
    for p in (hit, miss):
        stock(db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=wh.id, on_hand=5)
    contact = _contact(db)
    _policy_row(db, mode="availability", warehouse_ids=[wh.id], contact=contact)
    db.flush()

    result = StockService(db).list_stock(
        contact_id=contact.id,
        spec_constraints=[{"key": "class", "op": "eq", "value": "Basin"}, {"key": "size", "op": "eq", "value": 600}],
        page=1,
        limit=5,
    )
    ids = {e.get("product_id") for e in result.get("stock_availability") or []}
    assert ids == {hit.id}, result.get("stock_availability")
    assert not (result.get("stock_visibility") or {}).get("needs_product"), result.get("stock_visibility")


def test_spec_filtered_promotion_pages_neither_repeat_nor_drop_a_line(api, db, monkeypatch):
    """Reviewer should-fix (045a0fea, kill test K3): two promotions whose lines were added
    interleaved and share one `created_at`, paged at limit 2. Every line appears on exactly one
    page, and each promotion's lines come together (its heading is not split line by line)."""
    from datetime import datetime

    monkeypatch.setattr(
        UserPermissionService, "get_user_permission_slugs", lambda self, uid: {"marketing.promotion_products.view"}
    )
    same = datetime(2026, 10, 1, 9, 0, 0)
    # A class of its own, so promotions other tests in this worker left live never join the list.
    klass = f"ZZT Paging {uuid.uuid4().hex[:8]}"
    first = {tag: _p(db, f"PG{tag}0", {"class": _enum(klass)}) for tag in ("A", "B")}
    promos = {tag: _promo(db, prod, start=TODAY - timedelta(days=1), end=TODAY + timedelta(days=1)) for tag, prod in first.items()}
    owner = {first[t].product_code: promos[t].id for t in promos}
    for i in (1, 2):
        for tag, promo in promos.items():
            prod = _p(db, f"PG{tag}{i}", {"class": _enum(klass)})
            group = db.query(PromotionGroup).filter(PromotionGroup.promotion_id == promo.id).first()
            db.add(PromotionProduct(id=str(uuid.uuid4()), promotion_id=promo.id, promotion_group_id=group.id,
                                    product_id=prod.id, company_id=DEFAULT_COMPANY_ID))
            db.flush()
            owner[prod.product_code] = promo.id
    db.query(PromotionProduct).update({PromotionProduct.created_at: same})
    db.commit()

    seen = []
    for page in (1, 2, 3):
        resp = api.get(
            "/api/v1/marketing/promotion-products/",
            params={"serving_policy": "true", "limit": 2, "page": page,
                    **_constraints({"key": "class", "op": "eq", "value": klass})},
        )
        assert resp.status_code == 200, resp.text
        seen += [row["product"]["product_code"] for row in resp.json()["data"]]
    assert sorted(seen) == sorted(owner), seen
    order = [owner[c] for c in seen]
    assert order == sorted(order, key=order.index) and len({*order}) == 2, seen
    switches = sum(1 for a, b in zip(order, order[1:]) if a != b)
    assert switches == 1, f"a promotion's lines are split across the list: {seen}"


def test_a_promotion_line_names_its_group(api, db, monkeypatch):
    """Crew data on 045a0fea: SRTWB1514-BW printed twice under one promotion because it sits in
    two groups ("LESS 40% + 5%", ">=5 UNITS, EXTRA 10%") at one price. Each line names its group."""
    monkeypatch.setattr(
        UserPermissionService, "get_user_permission_slugs", lambda self, uid: {"marketing.promotion_products.view"}
    )
    prod = _p(db, "PGN", {"class": _enum("Basin")})
    _promo(db, prod, start=TODAY - timedelta(days=1), end=TODAY + timedelta(days=1))
    db.commit()
    resp = api.get(
        "/api/v1/marketing/promotion-products/",
        params={"serving_policy": "true", "limit": 10, **_constraints({"key": "class", "op": "eq", "value": "Basin"})},
    )
    assert resp.status_code == 200, resp.text
    rows = [r for r in resp.json()["data"] if r["product"]["product_code"] == prod.product_code]
    assert rows and rows[0].get("promotion_group_name") == "G", rows
