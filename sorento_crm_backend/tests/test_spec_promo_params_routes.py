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
# Slice 2: virtual `size` key (card section 9, slice 1), inactive keys
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
# (Owner Q4 option (a), the promotion-products path, was reversed by the owner on 13 Oct.)
# --------------------------------------------------------------------------------------- #


def test_inactive_class_registry_row_is_rejected_and_an_absent_one_stays_accepted(api, db):
    """A `class` registry row switched off is a 422 naming it. RED if
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


def test_a_dealers_spec_entry_matches_the_dealers_direct_entry_for_that_code(db):
    """Crew live check #2 (b12d0334): a dealer's "600mm basin got stock?" now asks "How many units
    for each?" (the availability flow). Evidence that each entry is the same answer a dealer gets
    asking for that one code directly: the same entry, field for field."""
    from app.services.inventory_service import StockService
    from tests.test_stock_availability_block import _contact, _policy_row

    wh = warehouse(db, company_id=DEFAULT_COMPANY_ID)
    hit = _p(db, "DEV1", {"class": _enum("Basin"), "dim_length": _num(600)})
    stock(db, company_id=DEFAULT_COMPANY_ID, product_id=hit.id, warehouse_id=wh.id, on_hand=5)
    contact = _contact(db)
    _policy_row(db, mode="availability", warehouse_ids=[wh.id], contact=contact)
    db.flush()
    svc = StockService(db)
    by_spec = svc.list_stock(
        contact_id=contact.id, page=1, limit=5,
        spec_constraints=[{"key": "class", "op": "eq", "value": "Basin"}, {"key": "size", "op": "eq", "value": 600}],
    )
    direct = svc.list_stock(contact_id=contact.id, product_ids=[hit.id], page=1, limit=5)
    spec_entry = next(e for e in by_spec["stock_availability"] if e.get("product_id") == hit.id)
    direct_entry = next(e for e in direct["stock_availability"] if e.get("product_id") == hit.id)
    assert spec_entry == direct_entry, (spec_entry, direct_entry)
    assert spec_entry.get("needs_quantity") is True, spec_entry


def test_a_size_no_axis_holds_states_the_size_asked(api, db):
    """Owner ruling 13 Oct (size-miss wording, option b): "No 250 mm. Sizes on file: 130 mm,
    150 mm." The route stamps the size asked so the reply can echo it."""
    rows = [_g(db, "AL", dim_length=150), _g(db, "AW", dim_width=130)]
    db.commit()
    resp = _list(api, db, rows, **_constraints(GRATING, {"key": "size", "op": "eq", "value": 250}))
    assert resp.status_code == 200, resp.text
    assert resp.json().get("spec_size_asked") == {"value": 250, "unit": "mm"}, resp.json()


def test_a_ranged_size_miss_states_no_single_size(api, db):
    """Reviewer S1 (b6da364a): "No 250 mm." is the wording for ONE size asked. A range or a bound
    has no single size to echo, so the field is absent and the older wording stands."""
    rows = [_g(db, "RL", dim_length=150)]
    db.commit()
    resp = _list(api, db, rows, **_constraints(GRATING, {"key": "size", "op": "between", "value": [200, 300]}))
    assert resp.status_code == 200, resp.text
    assert "spec_size_asked" not in resp.json(), resp.json()


@pytest.mark.parametrize("path", ["/api/v1/inventory/stock/balance"])
def test_every_spec_route_states_the_size_asked(api, db, path, monkeypatch):
    """Reviewer S2: the stock and promotion routes carry `spec_size_asked` too."""
    monkeypatch.setattr(
        UserPermissionService, "get_user_permission_slugs",
        lambda self, uid: {"marketing.promotion_products.view", "inventory.stock.view"},
    )
    prod = _g(db, "SR", dim_length=150)
    wh = warehouse(db, company_id=DEFAULT_COMPANY_ID)
    stock(db, company_id=DEFAULT_COMPANY_ID, product_id=prod.id, warehouse_id=wh.id, on_hand=3)
    _promo(db, prod, start=TODAY - timedelta(days=1), end=TODAY + timedelta(days=1))
    db.commit()
    resp = api.get(path, params={"limit": 10, **_constraints(GRATING, {"key": "size", "op": "eq", "value": 250})})
    assert resp.status_code == 200, resp.text
    assert resp.json().get("spec_size_asked") == {"value": 250, "unit": "mm"}, resp.json()


def test_the_promotions_list_keeps_promotions_holding_the_size_on_any_axis(api, db, monkeypatch):
    """Owner hand test 13 Oct: a size in a promotion ask only resolves the products; the answer is
    the promotions list narrowed to promotions with a linked product holding it (any axis)."""
    monkeypatch.setattr(
        UserPermissionService, "get_user_permission_slugs", lambda self, uid: {"marketing.promotions.view"}
    )
    on_length = _g(db, "PLL", dim_length=600)
    on_width = _g(db, "PLW", dim_width=600)
    other = _g(db, "PLO", dim_length=500)
    keep = {_promo(db, p, start=TODAY - timedelta(days=1), end=TODAY + timedelta(days=1)).id for p in (on_length, on_width)}
    _promo(db, other, start=TODAY - timedelta(days=1), end=TODAY + timedelta(days=1))
    db.commit()
    resp = api.get(
        "/api/v1/marketing/promotions/",
        params={"serving_policy": "true", "limit": 50, **_constraints(GRATING, {"key": "size", "op": "eq", "value": 600})},
    )
    assert resp.status_code == 200, resp.text
    assert {row["id"] for row in resp.json()["data"]} == keep, resp.json()


def test_the_promotions_list_rejects_an_unknown_spec_key(api, db, monkeypatch):
    monkeypatch.setattr(
        UserPermissionService, "get_user_permission_slugs", lambda self, uid: {"marketing.promotions.view"}
    )
    resp = api.get("/api/v1/marketing/promotions/", params=_constraints({"key": "zzt_nope", "op": "eq", "value": 1}))
    assert resp.status_code == 422, resp.text
