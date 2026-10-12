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
