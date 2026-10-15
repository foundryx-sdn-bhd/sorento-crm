"""SET-QUOTE-LINE review round 2, written red before the fixes.

R2-1 complete_sets gated on projects.projects.view + inventory module, R2-2 ids limits,
R2-3 brand_name on select items, R2-4 SO draft unresolved_product for a set code,
R2-5 SQL NULL snapshot on a product line. Helpers come from `test_set_quote_line.py`.
"""
from __future__ import annotations

from sqlalchemy import select

from app.models.product import Brand
from app.models.projects import ProjectQuotationLine

from ._pg_fixture import blank_session
from .test_set_quote_line import (  # noqa: F401  (api is a fixture)
    MARKER,
    _catalogue,
    _client,
    _env,
    _product,
    _restore,
    _save,
    _set,
    _set_line,
    _uid,
    api,
)
from .test_set_quote_line_review import SELECT, _clean_422, _driver_of, _select_world
from .test_set_quote_line_wire import _drafted_lines, _so_chain

SETS_ONLY = ["master_data.product_sets.view"]
SETS_AND_PROJECTS = ["master_data.product_sets.view", "projects.projects.view"]


# ------------------------------------------------------------------ R2-1 stock exposure


def test_r2_1_complete_sets_is_a_number_for_a_caller_holding_projects_view(api):
    _c, db, company_id, user_id, _project = api
    live, _retired, _theirs, token = _select_world(db, company_id)
    db.commit()
    client, originals = _client(db, user_id, slugs=SETS_AND_PROJECTS)
    try:
        response = client.get(SELECT, params={"query": f"Alpha {token}"})

        assert response.status_code == 200, response.text
        row = response.json()["data"][0]
        assert row["id"] == live.id
        assert isinstance(row["complete_sets"], (int, float)), row["complete_sets"]
    finally:
        _restore(originals)


def test_r2_1_complete_sets_is_null_without_projects_view_and_the_rest_is_unchanged(api):
    _c, db, company_id, user_id, _project = api
    live, _retired, _theirs, token = _select_world(db, company_id)
    db.commit()
    client, originals = _client(db, user_id, slugs=SETS_ONLY)
    try:
        response = client.get(SELECT, params={"query": f"Alpha {token}"})

        assert response.status_code == 200, response.text
        row = response.json()["data"][0]
        assert row["id"] == live.id
        assert row["complete_sets"] is None, row["complete_sets"]
        assert row["set_code"] == live.set_code
        assert row["member_count"] == 3
        assert len(row["parts"]) == 3
    finally:
        _restore(originals)


def test_r2_1_complete_sets_is_null_when_the_inventory_module_is_not_allowed(api, monkeypatch):
    from app.api.v1.master_data import product_sets

    _c, db, company_id, user_id, _project = api
    _select_world(db, company_id)
    db.commit()
    # the route must ask the module check; patch it at the route module (the name the
    # fix is told to use), and fail loudly if the route never imports it
    assert hasattr(product_sets, "module_allowed_with_api_key"), (
        "select route does not call module_allowed_with_api_key(db, user, 'inventory')"
    )
    monkeypatch.setattr(product_sets, "module_allowed_with_api_key", lambda *a, **k: False)
    client, originals = _client(db, user_id, slugs=SETS_AND_PROJECTS)
    try:
        response = client.get(SELECT, params={"query": "Alpha"})

        assert response.status_code == 200, response.text
        assert all(r["complete_sets"] is None for r in response.json()["data"])
    finally:
        _restore(originals)


# ------------------------------------------------------------------ R2-2 ids limits


def test_r2_2_more_than_100_ids_is_a_422(api):
    _c, db, _company_id, user_id, _project = api
    db.commit()
    client, originals = _client(db, user_id, slugs=SETS_AND_PROJECTS)
    try:
        ids = ",".join(_uid() for _ in range(101))

        _clean_422(client.get(SELECT, params={"ids": ids}))
    finally:
        _restore(originals)


def test_r2_2_a_malformed_id_inside_ids_is_a_422_with_no_sql_text(api):
    _c, db, company_id, user_id, _project = api
    live, _retired, _theirs, _token = _select_world(db, company_id)
    db.commit()
    client, originals = _client(db, user_id, slugs=SETS_AND_PROJECTS)
    try:
        _clean_422(client.get(SELECT, params={"ids": f"{live.id},not-a-uuid"}))
    finally:
        _restore(originals)


# ------------------------------------------------------------------ R2-3 brand at pick


def test_r2_3_select_items_carry_the_driver_parts_brand_name_or_null(api):
    _c, db, company_id, user_id, _project = api
    category, uom = _catalogue(db)
    token = _uid()[:6].upper()
    branded = _set(db, company_id, category, uom, name=f"{MARKER} Branded {token}")
    plain = _set(db, company_id, category, uom, name=f"{MARKER} Plain {token}")
    brand = Brand(
        id=_uid(),
        company_id=company_id,
        brand_code=f"ZZT{_uid()[:5]}",
        brand_name=f"{MARKER} Cabana",
    )
    db.add(brand)
    db.flush()
    _driver_of(branded).brand_id = brand.id
    db.commit()
    client, originals = _client(db, user_id, slugs=SETS_AND_PROJECTS)
    try:
        response = client.get(SELECT, params={"ids": f"{branded.id},{plain.id}"})

        assert response.status_code == 200, response.text
        by_id = {r["id"]: r for r in response.json()["data"]}
        assert "brand_name" in by_id[branded.id], by_id[branded.id]
        assert by_id[branded.id]["brand_name"] == f"{MARKER} Cabana"
        assert by_id[plain.id].get("brand_name", "MISSING") is None
    finally:
        _restore(originals)


# ------------------------------------------------------------------ R2-4 SO draft finding


def _unresolved(db, po, result):
    from app.models.project_so import SODraftFinding

    order_ids = [o["id"] for o in result["data"]]
    rows = db.query(SODraftFinding).filter(SODraftFinding.code == "unresolved_product").all()
    return [
        f
        for f in rows
        if f.purchase_order_id == po.id or f.project_sales_order_id in order_ids
    ]


def test_r2_4_a_po_line_keyed_by_a_quoted_set_code_is_not_an_unresolved_product():
    from app.services.project_so_draft_service import ProjectSODraftService

    with blank_session() as db:
        env = _env(db)
        product_set = _set(db, env["company_id"], env["category"], env["uom"])
        _set_line(db, env, unit_price="900.00", quantity="100")
        po, schedule_version = _so_chain(db, env, product_set, po_qty="100")

        result = ProjectSODraftService(db).build(po.id, schedule_version.id)

        assert len(_drafted_lines(db, result)) == 3
        found = _unresolved(db, po, result)
        assert found == [], [f.detail for f in found]


def test_r2_4_a_po_line_with_an_unknown_code_and_no_product_still_raises_it():
    from app.services.project_so_draft_service import ProjectSODraftService

    with blank_session() as db:
        env = _env(db)
        product_set = _set(db, env["company_id"], env["category"], env["uom"])
        _set_line(db, env, unit_price="900.00", quantity="100")
        po, schedule_version = _so_chain(
            db, env, product_set, po_qty="100", po_code=f"ZZT-NOPE-{_uid()[:6]}"
        )

        result = ProjectSODraftService(db).build(po.id, schedule_version.id)

        assert len(_unresolved(db, po, result)) == 1


def test_r2_4_a_po_line_keyed_by_a_quoted_product_code_still_raises_it():
    from app.services.project_so_draft_service import ProjectSODraftService

    with blank_session() as db:
        env = _env(db)
        product_set = _set(db, env["company_id"], env["category"], env["uom"])
        product = _product(db, env["category"], env["uom"], f"ZZT-QP-{_uid()[:6]}")
        quoted = _save(db, env, {"product_id": product.id, "unit_price": "10.00", "quantity": "5"})
        code = quoted.product_code_snapshot or product.product_code
        po, schedule_version = _so_chain(db, env, product_set, po_qty="5", po_code=code)

        result = ProjectSODraftService(db).build(po.id, schedule_version.id)

        assert len(_unresolved(db, po, result)) == 1


def test_r2_4_a_quoted_set_line_with_an_empty_parts_snapshot_still_raises_it():
    from app.services.project_so_draft_service import ProjectSODraftService

    with blank_session() as db:
        env = _env(db)
        product_set = _set(db, env["company_id"], env["category"], env["uom"])
        quoted = _set_line(db, env, unit_price="900.00", quantity="100")
        quoted.set_parts_snapshot = []
        db.flush()
        po, schedule_version = _so_chain(db, env, product_set, po_qty="100")

        result = ProjectSODraftService(db).build(po.id, schedule_version.id)

        assert len(_unresolved(db, po, result)) == 1


# ------------------------------------------------------------------ R2-5 SQL NULL storage


def test_r2_5_a_product_lines_set_parts_snapshot_is_sql_null_not_json_null():
    with blank_session() as db:
        env = _env(db)
        product = _product(db, env["category"], env["uom"], f"ZZT-R25-{_uid()[:5]}")
        line = _save(db, env, {"product_id": product.id, "unit_price": "10.00", "quantity": "1"})
        db.commit()

        is_sql_null = db.execute(
            select(ProjectQuotationLine.set_parts_snapshot.is_(None)).where(
                ProjectQuotationLine.id == line.id
            )
        ).scalar()

        assert is_sql_null is True
