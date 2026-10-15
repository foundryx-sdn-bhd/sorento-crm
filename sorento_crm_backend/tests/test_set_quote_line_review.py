"""SET-QUOTE-LINE review findings, written red before the fixes (round 4).

Ids follow the reviewer's list: R1 issue photo, R2 part UOM, R3 set select route, R4 gaps,
R6 brand, R7 label, R9 deleted set, S1 malformed id, S2 foreign-set oracle, plus the route
level company-scope gaps. Helpers and the `api` fixture come from `test_set_quote_line.py`.
New symbols are used inside each test.
"""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from io import BytesIO

import pytest
from sqlalchemy import text

from app.models.product import Brand, Product, ProductAttachment, UnitOfMeasure
from app.models.product_set import ProductSet
from app.models.projects import ProjectQuotationLine, ProjectSeries, ProjectSeriesSet
from app.models.resources import Attachment

from ._pg_fixture import blank_session
from .test_set_quote_line import (  # noqa: F401  (api is a fixture)
    BASE,
    MARKER,
    SIGNATURE_DATA_URI,
    _api_quotation,
    _api_series,
    _catalogue,
    _client,
    _codes,
    _env,
    _foreign_set,
    _numbering_rule,
    _product,
    _restore,
    _save,
    _scope,
    _series,
    _series_row,
    _set,
    _set_line,
    _sorento,
    _system_rule,
    _uid,
    _user,
    api,
)

SELECT = "/api/v1/master-data/product-sets/select"


def _photo() -> bytes:
    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (120, 120), (200, 30, 30)).save(buffer, format="JPEG")
    return buffer.getvalue()


def _file(db, company_id: str, name: str) -> Attachment:
    row = Attachment(
        id=_uid(),
        company_id=company_id,
        original_filename=f"{MARKER}-{name}",
        stored_filename=f"{MARKER}-{name}",
        file_path=f"https://cdn.zzt.test/products/{MARKER}/{name}",
        thumbnail_path=f"https://cdn.zzt.test/products/{MARKER}/{name}.thumb.jpg",
        mime_type="image/jpeg",
    )
    db.add(row)
    db.flush()
    return row


def _driver_of(product_set) -> object:
    """The pedestal: the one part of the standard test set that is not member index 0."""
    return next(m for m in product_set.members if m.product.product_code.endswith("-RL")).product


def _foreign_set_with_driver(db, category, uom):
    """A set of ANOTHER company holding one member, with a photo linked to that member."""
    other = _uid()
    db.execute(
        text("INSERT INTO companies (id, code, name, is_active) VALUES (:i, :c, :n, true)"),
        {"i": other, "c": f"ZZT{_uid()[:5]}", "n": f"{MARKER} Mocha"},
    )
    product_id = _uid()
    db.execute(
        text(
            "INSERT INTO products (id, product_code, product_name, category_id, base_uom_id, "
            "list_price, company_id) VALUES (:id, :code, :name, :cat, :uom, 300.00, :co)"
        ),
        {
            "id": product_id,
            "code": f"ZZTFOREIGNPART{_uid()[:4]}",
            "name": f"{MARKER} their part",
            "cat": category.id,
            "uom": uom.id,
            "co": other,
        },
    )
    set_id = _uid()
    db.execute(
        text(
            "INSERT INTO product_sets (id, company_id, set_code, name, is_active) "
            "VALUES (:id, :co, :code, :name, true)"
        ),
        {"id": set_id, "co": other, "code": f"ZZTFSET{_uid()[:4]}", "name": f"{MARKER} theirs"},
    )
    db.execute(
        text(
            "INSERT INTO product_set_members (id, product_set_id, product_id, quantity, "
            "contributes_to_price, sort_order) VALUES (:id, :s, :p, 1, true, 0)"
        ),
        {"id": _uid(), "s": set_id, "p": product_id},
    )
    theirs = _file(db, other, "their-photo.jpg")
    db.execute(
        text(
            "INSERT INTO product_attachments (id, product_id, attachment_id, is_primary) "
            "VALUES (:id, :p, :a, true)"
        ),
        {"id": _uid(), "p": product_id, "a": str(theirs.id)},
    )
    db.flush()
    return other, set_id, str(theirs.id)


# ------------------------------------------------------------------ R1 issue photo


def test_r1_issuing_stamps_the_set_lines_photo_from_the_driver_part_and_the_pdf_embeds_it(
    monkeypatch,
):
    from app.services import product_image_service as images
    from app.services import project_quotation_document_service as qdocs
    from app.services import project_quotation_pdf_service as qpdf
    from app.services import project_quotation_service as quotes
    from app.services.project_service import register_project

    class _Backend:
        def download_file(self, key):
            return _photo()

    monkeypatch.setattr(images, "get_backend", lambda provider: _Backend())

    with blank_session() as db:
        company_id = _sorento(db)
        from app.services import project_seed_service

        project_seed_service.run(db, company_id=company_id)
        _numbering_rule(db, company_id)
        owner = _user(db)
        category, uom = _catalogue(db)
        product_set = _set(db, company_id, category, uom)
        driver = _driver_of(product_set)
        photo = _file(db, company_id, "pedestal.jpg")
        link = ProductAttachment(
            id=_uid(), product_id=driver.id, attachment_id=photo.id, is_primary=False, sort_order=0
        )
        db.add(link)
        db.flush()
        images.choose(db, link)

        project = register_project(
            db,
            company_id=company_id,
            actor_user_id=owner,
            developer_party_id=None,
            title=f"{MARKER} Tower {_uid()[:12]}",
        )
        document = qdocs.create_document(db, project=project, actor_user_id=owner)
        scope = qdocs.add_scope(
            db, document=document, scope_label=f"{MARKER} Townhouse", actor_user_id=owner
        )
        line = quotes.upsert_line(
            db,
            version=quotes.current_version(db, scope.id),
            actor_user_id=owner,
            payload={"product_set_id": product_set.id, "unit_price": "900.00", "quantity": "4"},
        )
        assert line.image_attachment_id is None

        qdocs.sign_as_sorento(
            db,
            document=document,
            actor_user_id=owner,
            payload={
                "mode": "draw",
                "signer_name": f"{MARKER} Baser",
                "image_data_uri": SIGNATURE_DATA_URI,
            },
        )
        issued = qdocs.issue(db, document=document, actor_user_id=owner)
        db.refresh(line)

        assert line.image_attachment_id == photo.id
        html = qpdf.build_issue_html(db, issued)
        assert "PRODUCT IMAGE" in html
        assert re.search(r'<td class="img"><img src="data:image/jpeg;base64,', html)


# ------------------------------------------------------------------ R2 part UOM


def test_r2_each_part_carries_its_own_uom_and_the_so_lines_use_it_not_the_pos():
    from app.services.project_so_draft_service import ProjectSODraftService

    from .test_set_quote_line_wire import _drafted_lines, _so_chain

    with blank_session() as db:
        env = _env(db)
        category = env["category"]
        litre = UnitOfMeasure(id=_uid(), uom_code=f"ZZTL{_uid()[:3]}", uom_name="Litre")
        each = UnitOfMeasure(id=_uid(), uom_code=f"ZZTE{_uid()[:3]}", uom_name="Each")
        db.add_all([litre, each])
        db.flush()
        pedestal = _product(db, category, each, "ZZT-UOM-RL")
        cistern = _product(db, category, litre, "ZZT-UOM-CY")
        product_set = _set(
            db,
            env["company_id"],
            category,
            env["uom"],
            parts=[(pedestal, "1", True), (cistern, "1", False)],
        )
        quoted = _set_line(db, env, unit_price="900.00", quantity="5")
        by_product = {p["product_id"]: p for p in quoted.set_parts_snapshot}
        assert by_product[pedestal.id]["uom"] == each.uom_code
        assert by_product[cistern.id]["uom"] == litre.uom_code

        po, schedule_version = _so_chain(db, env, product_set, po_qty="5")  # PO uom is "SETS"
        lines = _drafted_lines(db, ProjectSODraftService(db).build(po.id, schedule_version.id))

        uoms = {line.product_id: line.uom for line in lines}
        assert uoms == {pedestal.id: each.uom_code, cistern.id: litre.uom_code}


# ------------------------------------------------------------------ R3 select route


VIEW_SETS = [
    "projects.projects.view",
    "master_data.product_sets.view",
    "master_data.products.view",
]


def _viewer(db, user_id):
    """A client that may read product sets, so the content tests are about content only."""
    return _client(db, user_id, slugs=VIEW_SETS)


def _select_world(db, company_id):
    category, uom = _catalogue(db)
    token = _uid()[:6].upper()
    live = _set(db, company_id, category, uom, name=f"{MARKER} Alpha {token}")
    retired = _set(db, company_id, category, uom, name=f"{MARKER} Beta {token}", active=False)
    theirs = _foreign_set(db, code=f"ZZTSEL{_uid()[:4]}")
    return live, retired, theirs, token


def test_r3_the_select_route_lists_active_own_sets_found_by_code_or_name(api):
    _client_unused, db, company_id, user_id, _project = api
    live, retired, theirs, token = _select_world(db, company_id)
    db.commit()
    client, originals = _viewer(db, user_id)
    try:

        by_name = client.get(SELECT, params={"query": f"Alpha {token}"})
        by_code = client.get(SELECT, params={"query": live.set_code})
        inactive = client.get(SELECT, params={"query": f"Beta {token}"})

        assert by_name.status_code == 200, by_name.text
        rows = by_name.json()["data"]
        assert [r["id"] for r in rows] == [live.id]
        assert [r["id"] for r in by_code.json()["data"]] == [live.id]
        assert inactive.json()["data"] == []
        row = rows[0]
        assert row["set_code"] == live.set_code
        assert row["name"] == live.name
        assert row["is_active"] is True
        assert row["member_count"] == 3
        assert Decimal(str(row["list_price"])) == Decimal("200.00")
        assert "complete_sets" in row
        assert [p["product_code"] for p in row["parts"]] == _codes(live)
        assert [Decimal(str(p["quantity"])) for p in row["parts"]] == [1, 1, 2]
    finally:
        _restore(originals)


def test_r3_ids_return_exactly_those_own_sets_inactive_included_never_a_foreign_one(api):
    _client_unused, db, company_id, user_id, _project = api
    live, retired, theirs, _token = _select_world(db, company_id)
    db.commit()
    client, originals = _viewer(db, user_id)
    try:

        response = client.get(SELECT, params={"ids": f"{live.id},{retired.id},{theirs}"})

        assert response.status_code == 200, response.text
        assert sorted(r["id"] for r in response.json()["data"]) == sorted([live.id, retired.id])
        retired_row = next(r for r in response.json()["data"] if r["id"] == retired.id)
        assert retired_row["is_active"] is False
    finally:
        _restore(originals)


def test_r3_a_foreign_sets_code_never_appears_in_a_search(api):
    _client_unused, db, company_id, user_id, _project = api
    _select_world(db, company_id)
    code = f"ZZTONLYTHEIRS{_uid()[:4]}"
    _foreign_set(db, code=code)
    db.commit()
    client, originals = _viewer(db, user_id)
    try:

        response = client.get(SELECT, params={"query": code})

        assert response.status_code == 200, response.text
        assert response.json()["data"] == []
    finally:
        _restore(originals)


def test_r3_a_staff_user_without_the_product_sets_view_permission_still_gets_200(api):
    from app.dependencies import get_current_user_or_api_key
    from app.main import app

    client, db, company_id, user_id, _project = api
    live, _retired, _theirs, token = _select_world(db, company_id)
    db.commit()

    reader, originals = _client(db, user_id, slugs=["projects.projects.view"])
    app.dependency_overrides[get_current_user_or_api_key] = lambda: {
        "id": user_id,
        "email": f"{user_id}@zzt.test",
        "role": "sales_executive",
    }
    try:
        response = reader.get(SELECT, params={"query": f"Alpha {token}"})
        assert response.status_code == 200, response.text
        assert [r["id"] for r in response.json()["data"]] == [live.id]
    finally:
        _restore(originals)


def test_r3_no_session_is_refused(api):
    from app.dependencies import get_current_user_or_api_key
    from app.main import app

    client, db, company_id, _user_id, _project = api
    _select_world(db, company_id)
    db.commit()
    app.dependency_overrides.pop(get_current_user_or_api_key, None)

    response = client.get(SELECT, params={"query": "Alpha"})

    assert response.status_code in (401, 403), response.text


# ------------------------------------------------------------------ R4 gaps


def test_r4a_a_series_row_wins_over_a_company_default_rule():
    from app.services import project_pricing_service as pricing

    with blank_session() as db:
        env = _env(db)
        product_set = _set(db, env["company_id"], env["category"], env["uom"])
        series = _series(db, env["company_id"])
        _series_row(db, series, product_set, price="1000.00", pct="10.00")
        _system_rule(db, env["company_id"], "percent", "50.00")

        floor = pricing.resolve_set_floor(
            db,
            company_id=env["company_id"],
            product_set=product_set,
            list_price=Decimal("600.00"),
            series_id=series.id,
        )

        assert floor.level == "series"
        assert floor.value == Decimal("900.00")


def test_r4b_at_save_a_product_rule_on_the_driver_and_a_category_rule_give_a_set_no_floor():
    from app.services import project_pricing_service as pricing

    with blank_session() as db:
        env = _env(db)
        product_set = _set(db, env["company_id"], env["category"], env["uom"])
        driver = _driver_of(product_set)
        pricing.upsert_floor_rule(
            db,
            company_id=env["company_id"],
            payload={"mode": "percent", "value": "90", "product_id": driver.id},
        )
        pricing.upsert_floor_rule(
            db,
            company_id=env["company_id"],
            payload={"mode": "percent", "value": "90", "category_id": env["category"].id},
        )

        line = _set_line(db, env, unit_price="1.00")

        assert line.floor_value_applied is None
        assert line.floor_level_applied is None
        assert line.is_below_floor is False


def test_r4c_resaving_with_the_same_set_id_after_the_master_changed_keeps_the_old_parts():
    from app.models.product_set import ProductSetMember

    with blank_session() as db:
        env = _env(db)
        product_set = _set(db, env["company_id"], env["category"], env["uom"])
        line = _set_line(db, env)
        quoted = [p["product_code"] for p in line.set_parts_snapshot]

        extra = _product(db, env["category"], env["uom"], "ZZT-ADDED-AFTER")
        db.add(
            ProductSetMember(
                id=_uid(),
                product_set_id=product_set.id,
                product_id=extra.id,
                quantity=Decimal("1"),
                contributes_to_price=False,
                sort_order=9,
            )
        )
        db.flush()
        db.expire(product_set)

        # the grid always sends product_set_id, even when only the price moved
        edited = _save(
            db, env, {"product_set_id": product_set.id, "unit_price": "910.00"}, line=line
        )

        assert [p["product_code"] for p in edited.set_parts_snapshot] == quoted


# ------------------------------------------------------------------ R6 brand


def test_r6_a_new_set_pick_fills_a_blank_brand_with_the_driver_parts_brand_and_keeps_a_typed_one():
    with blank_session() as db:
        env = _env(db)
        brand = Brand(
            id=_uid(),
            company_id=env["company_id"],
            brand_code=f"ZZT{_uid()[:5]}",
            brand_name=f"{MARKER} Cabana",
        )
        db.add(brand)
        db.flush()
        product_set = _set(db, env["company_id"], env["category"], env["uom"])
        _driver_of(product_set).brand_id = brand.id
        db.flush()

        blank = _set_line(db, env, brand_snapshot="")
        absent = _set_line(db, env)
        typed = _set_line(db, env, brand_snapshot="HOUSE BRAND")

        assert blank.brand_snapshot == f"{MARKER} Cabana"
        assert absent.brand_snapshot == f"{MARKER} Cabana"
        assert typed.brand_snapshot == "HOUSE BRAND"


# ------------------------------------------------------------------ R7 label


def test_r7_recompute_names_a_line_with_no_code_and_no_description_non_standard_item():
    from app.services import project_quotation_service as quotes

    with blank_session() as db:
        env = _env(db)
        db.add(
            ProjectQuotationLine(
                id=_uid(),
                company_id=env["company_id"],
                version_id=env["version"].id,
                product_code_snapshot="",
                description_snapshot="",
                unit_price=Decimal("1.00"),
                quantity=Decimal("1"),
                line_total=Decimal("1.00"),
                is_non_standard=False,
            )
        )
        db.flush()

        report = quotes.recompute_version(db, version=env["version"])

        assert report["changed_lines"] == ["Non-standard item"]


# ------------------------------------------------------------------ R9 deleted set


def test_r9_the_so_split_still_works_from_the_frozen_parts_after_the_set_is_deleted():
    from app.services.project_so_draft_service import ProjectSODraftService

    from .test_set_quote_line_wire import _drafted_lines, _so_chain

    with blank_session() as db:
        env = _env(db)
        product_set = _set(db, env["company_id"], env["category"], env["uom"])
        quoted = _set_line(db, env, unit_price="900.00", quantity="10")
        part_products = {p["product_id"] for p in quoted.set_parts_snapshot}
        po, schedule_version = _so_chain(db, env, product_set, po_qty="10")
        set_id = product_set.id
        db.query(ProductSet).filter(ProductSet.id == set_id).delete(synchronize_session=False)
        db.flush()
        db.expire_all()
        stored = db.query(ProjectQuotationLine).filter(ProjectQuotationLine.id == quoted.id).one()
        assert stored.product_set_id is None
        assert stored.set_parts_snapshot

        lines = _drafted_lines(db, ProjectSODraftService(db).build(po.id, schedule_version.id))

        assert {line.explosion_source for line in lines} == {"set"}
        assert {line.product_id for line in lines} == part_products


# ------------------------------------------------------------------ S1 malformed id


BAD = "not-a-uuid"


def _clean_422(response):
    assert response.status_code == 422, response.text
    lowered = response.text.lower()
    assert "select " not in lowered and "psycopg" not in lowered and "sqlalchemy" not in lowered


def test_s1_a_malformed_product_set_id_is_a_422_on_the_verdict_and_every_line_write(api):
    client, db, _company_id, _user_id, project = api
    category, uom = _catalogue(db)
    product = _product(db, category, uom, "ZZT-S1")
    db.commit()
    quotation = _api_quotation(client, project.id)
    version_id = quotation["current_version_id"]
    existing = client.post(
        f"{BASE}/quotation-versions/{version_id}/lines",
        json={"product_id": product.id, "unit_price": "1.00", "quantity": "1"},
    ).json()

    _clean_422(
        client.get(
            f"{BASE}/quotations/{quotation['id']}/line-verdict",
            params={"product_set_id": BAD, "unit_price": "1.00"},
        )
    )
    _clean_422(
        client.post(
            f"{BASE}/quotation-versions/{version_id}/lines",
            json={"product_set_id": BAD, "unit_price": "1.00", "quantity": "1"},
        )
    )
    _clean_422(
        client.put(
            f"{BASE}/quotation-versions/{version_id}/lines/{existing['id']}",
            json={"product_set_id": BAD},
        )
    )
    _clean_422(
        client.put(
            f"{BASE}/quotation-versions/{version_id}/lines",
            json={"lines": [{"product_set_id": BAD, "unit_price": "1.00", "quantity": "1"}]},
        )
    )


# ------------------------------------------------------------------ S2 / foreign set on lines


def test_s2_a_foreign_set_with_members_answers_404_with_or_without_an_image_pick(api):
    client, db, _company_id, _user_id, project = api
    category, uom = _catalogue(db)
    _other, foreign_set, foreign_file = _foreign_set_with_driver(db, category, uom)
    db.commit()
    version_id = _api_quotation(client, project.id)["current_version_id"]
    url = f"{BASE}/quotation-versions/{version_id}/lines"
    body = {"product_set_id": foreign_set, "unit_price": "1.00", "quantity": "1"}

    plain = client.post(url, json=body)
    picked = client.post(url, json={**body, "image_attachment_id": foreign_file})

    assert plain.status_code == 404, plain.text
    assert picked.status_code == 404, picked.text
    assert picked.json()["code"] == plain.json()["code"] == "product_set_not_found"


def test_a_foreign_set_is_404_on_post_put_and_bulk_put(api):
    client, db, _company_id, _user_id, project = api
    category, uom = _catalogue(db)
    product = _product(db, category, uom, "ZZT-FS")
    foreign = _foreign_set(db, code=f"ZZTFS{_uid()[:4]}")
    db.commit()
    version_id = _api_quotation(client, project.id)["current_version_id"]
    existing = client.post(
        f"{BASE}/quotation-versions/{version_id}/lines",
        json={"product_id": product.id, "unit_price": "1.00", "quantity": "1"},
    ).json()
    line = {"product_set_id": foreign, "unit_price": "1.00", "quantity": "1"}

    posted = client.post(f"{BASE}/quotation-versions/{version_id}/lines", json=line)
    put = client.put(
        f"{BASE}/quotation-versions/{version_id}/lines/{existing['id']}",
        json={"product_set_id": foreign},
    )
    bulk = client.put(f"{BASE}/quotation-versions/{version_id}/lines", json={"lines": [line]})

    assert (posted.status_code, put.status_code, bulk.status_code) == (404, 404, 404)


# ------------------------------------------------------------------ series company scope


def test_another_companys_series_is_404_on_the_sets_rows_get_patch_and_delete(api):
    client, db, company_id, _user_id, _project = api
    category, uom = _catalogue(db)
    mine = _set(db, company_id, category, uom)
    other = _uid()
    db.execute(
        text("INSERT INTO companies (id, code, name, is_active) VALUES (:i, :c, :n, true)"),
        {"i": other, "c": f"ZZT{_uid()[:5]}", "n": f"{MARKER} Mocha"},
    )
    theirs = ProjectSeries(id=_uid(), company_id=other, name=f"{MARKER} their series")
    db.add(theirs)
    db.flush()
    assert theirs.company_id == other
    db.commit()
    body = {"selling_price": "1.00", "max_discount_pct": "1.00"}

    listed = client.get(f"{BASE}/config/series/{theirs.id}/sets/rows")
    patched = client.patch(f"{BASE}/config/series/{theirs.id}/sets/{mine.id}", json=body)
    deleted = client.delete(f"{BASE}/config/series/{theirs.id}/sets/{mine.id}")

    assert (listed.status_code, patched.status_code, deleted.status_code) == (404, 404, 404)


def test_a_series_row_pointing_at_a_foreign_set_is_not_listed(api):
    client, db, company_id, _user_id, _project = api
    series = _api_series(client, "Poisoned")
    foreign = _foreign_set(db, code=f"ZZTPOISON{_uid()[:4]}")
    db.add(
        ProjectSeriesSet(
            series_id=series["id"],
            product_set_id=foreign,
            selling_price=Decimal("1.00"),
            max_discount_pct=Decimal("1.00"),
        )
    )
    db.commit()

    response = client.get(f"{BASE}/config/series/{series['id']}/sets/rows")

    assert response.status_code == 200, response.text
    assert response.json()["data"] == []
