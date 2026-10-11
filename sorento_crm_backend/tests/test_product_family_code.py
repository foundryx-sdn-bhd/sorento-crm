"""RED tests for CODE-VARIANTS data + API (AC-1 .. AC-5).

UAC: documentation/plans/chatbot/code-variants-acceptance-criteria.md

Postgres only (tests/_pg_fixture.py), every test seeds its own chain. The routes live
under the master-data prefix, next to the existing variant-parent routes.

Note for the coder: `ProductUpdate` has no `product_code` field (extra is ignored), so a
code change cannot arrive through ProductService.update_product today; AC-3's "API update
(incl. a code change)" is covered here through the update path that exists (manual rows are
never rewritten) and bulk import (update by code). Say so in the PR if that is wrong.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.models.base import set_company_scope
from app.models.product import Product, ProductCategory, UnitOfMeasure
from app.schemas.product import ProductCategoryCreate, ProductCreate, ProductUpdate, UnitOfMeasureCreate
from app.services.company_scope import DEFAULT_COMPANY_ID
from app.services.master_ingest_service import MasterIngestService
from app.services.product_service import ProductCategoryService, ProductService, UnitOfMeasureService
from tests._pg_fixture import blank_session, unique_code

BASE = "/api/v1/master-data/products"

# AC-1 table, every row.
AC1_TABLE = [
    ("SRTWC8357-RL-P", "SRTWC8357"),
    ("SRTWC8066", "SRTWC8066"),
    ("SRTUB206-BACK INLET", "SRTUB206"),
    ("MWC7630 -S10", "MWC7630"),
    ("cbs208-bl", "CBS208"),
    ("TPE-9201", None),
    ("SRT-BB7001", None),
    ("MWC-SC604", None),
    ("CWC INLET", None),
    ("SORENTO", None),
    ("", None),
    (None, None),
]


@pytest.fixture()
def db():
    with blank_session() as session:
        yield session


@pytest.fixture()
def refs(db):
    cat, uom = str(uuid.uuid4()), str(uuid.uuid4())
    db.add(ProductCategory(id=cat, category_code=unique_code("FC")[:50], category_name="ZZT"))
    db.add(UnitOfMeasure(id=uom, uom_code=unique_code("FU")[:20], uom_name="Each"))
    db.flush()
    return cat, uom


def _mk(db, refs, code, *, family=None, manual=False, active=True) -> str:
    pid = str(uuid.uuid4())
    db.add(
        Product(
            id=pid, product_code=code, product_name=f"N {code}", category_id=refs[0],
            base_uom_id=refs[1], list_price=0, is_active=active,
            family_code=family, family_code_manual=manual,
        )
    )
    db.flush()
    return pid


def _family(db, code):
    db.expire_all()
    row = db.execute(
        text("SELECT family_code, family_code_manual FROM products WHERE product_code = :c"),
        {"c": code},
    ).first()
    assert row is not None, f"{code} was not written"
    return row[0], row[1]


# --------------------------------------------------------------------------- AC-1
@pytest.mark.parametrize("code, expected", AC1_TABLE)
def test_ac1_family_code_for(code, expected):
    from app.services.product_family import family_code_for

    assert family_code_for(code) == expected


# --------------------------------------------------------------------------- AC-2
@pytest.mark.parametrize("code, expected", AC1_TABLE)
def test_ac2_backfill_sql_agrees_with_python_rule(db, code, expected):
    from app.services.product_family import FAMILY_CODE_SQL, family_code_for

    got = db.execute(
        text(f"SELECT {FAMILY_CODE_SQL} FROM (SELECT CAST(:c AS text) AS product_code) t"),
        {"c": code},
    ).scalar()
    assert got == expected
    assert got == family_code_for(code)


def test_ac2_columns_exist_with_defaults(db, refs):
    pid = _mk(db, refs, unique_code("ZZTCOL")[:30])
    row = db.execute(
        text("SELECT family_code, family_code_manual FROM products WHERE id = :i"), {"i": pid}
    ).first()
    assert row[0] is None and row[1] is False


def test_ac2_migration_file_chain():
    import pathlib

    src = pathlib.Path("alembic/versions").glob("family_code_0001*.py")
    files = list(src)
    assert files, "migration family_code_0001 is missing"
    assert "family_code_0001" in files[0].read_text()


# --------------------------------------------------------------------------- AC-3
def _esb_seed(db):
    svc = MasterIngestService(db, integration_id=None, company_id=DEFAULT_COMPANY_ID)
    cat, uom = unique_code("FCAT")[:30], unique_code("FUOM")[:15]
    svc.ingest("product_categories", [{"source_ref": f"DK-{cat}", "code": cat, "name": "Cat"}])
    svc.ingest("units_of_measure", [{"source_ref": f"DK-{uom}", "code": uom, "name": "Each"}])
    return svc, cat, uom


def _esb_row(code, cat, uom, name="Tap"):
    return {"source_ref": f"DK-{code}", "code": code, "name": name, "category_code": cat, "uom_code": uom}


def test_ac3_autocount_ingest_create_sets_family(db):
    set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
    svc, cat, uom = _esb_seed(db)
    code = "ZZTFAM8357-RL-P"
    res = svc.ingest("products", [_esb_row(code, cat, uom)])
    assert res.created == 1, res.records[0].errors
    assert _family(db, code) == ("ZZTFAM8357", False)


def test_ac3_autocount_ingest_update_fills_family_but_never_a_manual_row(db):
    set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
    svc, cat, uom = _esb_seed(db)
    code = "ZZTFAM8358-RL"
    svc.ingest("products", [_esb_row(code, cat, uom)])
    db.execute(text("UPDATE products SET family_code = 'HAND1', family_code_manual = true WHERE product_code = :c"), {"c": code})
    res = svc.ingest("products", [_esb_row(code, cat, uom, name="Tap renamed")])
    assert res.updated == 1, res.records[0].errors
    assert _family(db, code) == ("HAND1", True)

    auto = "ZZTFAM8359-RL"
    svc.ingest("products", [_esb_row(auto, cat, uom)])
    db.execute(text("UPDATE products SET family_code = NULL WHERE product_code = :c"), {"c": auto})
    svc.ingest("products", [_esb_row(auto, cat, uom, name="Tap renamed")])
    assert _family(db, auto) == ("ZZTFAM8359", False)


def test_ac3_api_create_sets_family(db, refs):
    set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
    code = "ZZTFAM8360-SH-150"
    ProductService(db).create_product(
        ProductCreate(product_code=code, product_name="Tap", category_id=refs[0],
                      base_uom_id=refs[1], list_price=Decimal("1.00")),
        created_by=None,
    )
    assert _family(db, code) == ("ZZTFAM8360", False)


def test_ac3_api_update_never_rewrites_a_manual_row(db, refs):
    set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
    pid = _mk(db, refs, "ZZTFAM8361-RL", family="HAND2", manual=True)
    ProductService(db).update_product(pid, ProductUpdate(product_name="Renamed"), updated_by=None)
    assert _family(db, "ZZTFAM8361-RL") == ("HAND2", True)


def test_ac3_excel_bulk_import_create_and_update(db):
    set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
    svc = ProductService(db)
    manual = "ZZTFAM8363-RL"
    svc.bulk_import_products(
        [{"product_code": "ZZTFAM8362-RL", "product_name": "A", "item_group": "ZZT G", "list_price": 5},
         {"product_code": manual, "product_name": "B", "item_group": "ZZT G", "list_price": 5}],
        user_id=str(uuid.uuid4()),
    )
    assert _family(db, "ZZTFAM8362-RL") == ("ZZTFAM8362", False)
    db.execute(text("UPDATE products SET family_code = 'HAND3', family_code_manual = true WHERE product_code = :c"), {"c": manual})
    db.execute(text("UPDATE products SET family_code = NULL WHERE product_code = 'ZZTFAM8362-RL'"))
    svc.bulk_import_products(
        [{"product_code": "ZZTFAM8362-RL", "product_name": "A2", "item_group": "ZZT G", "list_price": 6},
         {"product_code": manual, "product_name": "B2", "item_group": "ZZT G", "list_price": 6}],
        user_id=str(uuid.uuid4()),
    )
    assert _family(db, "ZZTFAM8362-RL") == ("ZZTFAM8362", False)
    assert _family(db, manual) == ("HAND3", True)


# --------------------------------------------------------------------------- AC-4 / AC-5 routes
_USER = {"id": str(uuid.uuid4()), "email": "family@example.com"}


@pytest.fixture()
def client(db):
    from app.main import app
    from app.database import get_db
    from app.dependencies import get_current_user, get_current_user_or_api_key
    from app.services.company_scope_resolver import apply_company_scope

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: _USER
    app.dependency_overrides[get_current_user_or_api_key] = lambda: _USER
    app.dependency_overrides[apply_company_scope] = lambda: None
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture()
def anon_client(db):
    from app.main import app
    from app.database import get_db

    app.dependency_overrides[get_db] = lambda: db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_ac4_put_stores_trimmed_uppercase_and_sets_manual(client, db, refs):
    pid = _mk(db, refs, "ZZTFAM8370-RL", family="ZZTFAM8370")
    r = client.put(f"{BASE}/{pid}/family-code", json={"family_code": " hand 9 "})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["family_code"] == "HAND9"
    assert body["family_code_manual"] is True
    assert _family(db, "ZZTFAM8370-RL") == ("HAND9", True)


@pytest.mark.parametrize("value", [None, "", "   "])
def test_ac4_put_empty_or_null_stores_null_but_stays_manual(client, db, refs, value):
    pid = _mk(db, refs, "ZZTFAM8371-RL", family="ZZTFAM8371")
    r = client.put(f"{BASE}/{pid}/family-code", json={"family_code": value})
    assert r.status_code == 200, r.text
    assert r.json()["family_code"] is None
    assert _family(db, "ZZTFAM8371-RL") == (None, True)


def test_ac4_reset_clears_flag_and_rederives(client, db, refs):
    pid = _mk(db, refs, "ZZTFAM8372-RL", family="HAND4", manual=True)
    r = client.post(f"{BASE}/{pid}/family-code-reset")
    assert r.status_code == 200, r.text
    assert r.json()["family_code"] == "ZZTFAM8372"
    assert r.json()["family_code_manual"] is False
    assert _family(db, "ZZTFAM8372-RL") == ("ZZTFAM8372", False)


def test_ac4_unknown_product_404(client):
    # "Product not found" is the service's message; a missing route answers a bare "Not Found".
    for r in (
        client.put(f"{BASE}/{uuid.uuid4()}/family-code", json={"family_code": "X1"}),
        client.post(f"{BASE}/{uuid.uuid4()}/family-code-reset"),
    ):
        assert r.status_code == 404
        assert "product not found" in r.text.lower(), r.text


def test_ac4_put_wrong_body_type_422(client, db, refs):
    pid = _mk(db, refs, "ZZTFAM8373-RL")
    r = client.put(f"{BASE}/{pid}/family-code", json={"family_code": ["a"]})
    assert r.status_code == 422


def test_ac4_auth_denied_anonymous(anon_client, db, refs):
    pid = _mk(db, refs, "ZZTFAM8374-RL")
    assert anon_client.put(f"{BASE}/{pid}/family-code", json={"family_code": "X1"}).status_code in (401, 403)
    assert anon_client.post(f"{BASE}/{pid}/family-code-reset").status_code in (401, 403)


def test_ac4_put_then_detail_round_trip(client, db, refs):
    pid = _mk(db, refs, "ZZTFAM8375-RL", family="ZZTFAM8375")
    client.put(f"{BASE}/{pid}/family-code", json={"family_code": "zz1"})
    assert client.get(f"{BASE}/{pid}").json()["family_code"] == "ZZ1"


def test_ac5_detail_carries_family_fields_and_members(client, db, refs):
    me = _mk(db, refs, "ZZTFAM8380-RL", family="ZZTFAM8380")
    b = _mk(db, refs, "ZZTFAM8380-B", family="ZZTFAM8380")
    a = _mk(db, refs, "ZZTFAM8380-A", family="ZZTFAM8380")
    _mk(db, refs, "ZZTFAM8380-OFF", family="ZZTFAM8380", active=False)
    _mk(db, refs, "ZZTFAM8381-RL", family="ZZTFAM8381")
    r = client.get(f"{BASE}/{me}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["family_code"] == "ZZTFAM8380"
    assert body["family_code_manual"] is False
    assert body["family_members"] == [
        {"id": a, "product_code": "ZZTFAM8380-A", "product_name": "N ZZTFAM8380-A"},
        {"id": b, "product_code": "ZZTFAM8380-B", "product_name": "N ZZTFAM8380-B"},
    ]


def test_ac5_no_family_means_empty_members(client, db, refs):
    pid = _mk(db, refs, "ZZTSOLO", family=None)
    body = client.get(f"{BASE}/{pid}").json()
    assert body["family_code"] is None
    assert body["family_members"] == []


def test_ac5_list_rows_do_not_carry_members(client, db, refs):
    _mk(db, refs, "ZZTFAM8390-RL", family="ZZTFAM8390")
    _mk(db, refs, "ZZTFAM8390-B", family="ZZTFAM8390")
    r = client.get(f"{BASE}", params={"search": "ZZTFAM8390", "limit": 10})
    assert r.status_code == 200, r.text
    rows = r.json().get("items") or r.json().get("data") or []
    assert rows, r.text
    assert all(not row.get("family_members") for row in rows)


# --------------------------------------------------------------------------- fix round
@pytest.fixture()
def perm_client(db, monkeypatch):
    """A signed-in user whose permission set is the returned `allow` set."""
    from app.main import app
    from app.database import get_db
    from app.dependencies import get_current_user, get_current_user_or_api_key
    from app.services.company_scope_resolver import apply_company_scope
    from app.services.user_service import UserPermissionService

    allow: set = set()
    caller = {"id": str(uuid.uuid4()), "email": "fam-perm@example.com"}
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: caller
    app.dependency_overrides[get_current_user_or_api_key] = lambda: caller
    app.dependency_overrides[apply_company_scope] = lambda: None
    monkeypatch.setattr(
        UserPermissionService, "check_user_has_permission", lambda self, uid, slug: slug in allow
    )
    try:
        yield TestClient(app), allow
    finally:
        app.dependency_overrides.clear()


def test_r1_family_routes_need_master_data_products_edit(perm_client, db, refs):
    client, allow = perm_client
    pid = _mk(db, refs, "ZZTFAM8400-RL", family="ZZTFAM8400")
    allow.add("master_data.products.view")  # a neighbouring slug does not open the writes
    assert client.put(f"{BASE}/{pid}/family-code", json={"family_code": "X1"}).status_code == 403
    assert client.post(f"{BASE}/{pid}/family-code-reset").status_code == 403
    assert _family(db, "ZZTFAM8400-RL") == ("ZZTFAM8400", False)
    allow.add("master_data.products.edit")
    assert client.put(f"{BASE}/{pid}/family-code", json={"family_code": "X1"}).status_code == 200
    assert client.post(f"{BASE}/{pid}/family-code-reset").status_code == 200


def test_r2_family_code_over_100_chars_is_422(client, db, refs):
    pid = _mk(db, refs, "ZZTFAM8401-RL", family="ZZTFAM8401")
    r = client.put(f"{BASE}/{pid}/family-code", json={"family_code": "A" * 101})
    assert r.status_code == 422, r.text
    assert _family(db, "ZZTFAM8401-RL") == ("ZZTFAM8401", False)


def test_r4_detail_members_stay_in_the_products_company_without_a_scope(db, refs, client):
    from app.models.company import Company

    mocha = "00000000-0000-0000-0000-000000000002"
    if db.query(Company).filter(Company.id == mocha).first() is None:
        db.add(Company(id=mocha, name="ZZT Mocha", code=unique_code("MCH")[:20], is_active=True))
        db.flush()
    me = _mk(db, refs, "ZZTFAM8410-RL", family="ZZTFAM8410")
    mine = _mk(db, refs, "ZZTFAM8410-B", family="ZZTFAM8410")
    other = _mk(db, refs, "ZZTFAM8410-MOCHA", family="ZZTFAM8410")
    db.execute(text("UPDATE products SET company_id = :c WHERE id = :i"), {"c": mocha, "i": other})
    set_company_scope(db, None)  # an all-companies / API-key call
    body = client.get(f"{BASE}/{me}").json()
    assert [m["id"] for m in body["family_members"]] == [mine], body["family_members"]
