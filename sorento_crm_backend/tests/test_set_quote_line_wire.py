"""SET-QUOTE-LINE, second half: the SO draft split (E1-E3) and the HTTP seam (A1, A2, B6, C1, C5).

Helpers and the `api` fixture live in `test_set_quote_line.py` and are imported from there, so
the two files seed their chains identically. Written BEFORE the implementation: every test is
red until the slice lands. New symbols are imported inside each test.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.models.product_set import ProductSetMember

from ._pg_fixture import blank_session
from .test_set_quote_line import (  # noqa: F401  (api is a fixture)
    BASE,
    MARKER,
    _api_quotation,
    _api_series,
    _catalogue,
    _client,
    _codes,
    _env,
    _foreign_set,
    _product,
    _restore,
    _set,
    _set_line,
    _uid,
    api,
)


# ------------------------------------------------------------------ E SO draft


def _so_chain(db, env, product_set, *, po_qty: str, po_code: str | None = None):
    """A quotation holding the set line, a PO for it by set code, and an empty schedule."""
    from app.models.project_so import (
        DeliverySchedule,
        DeliveryScheduleVersion,
        ProjectPOLine,
        ProjectPOVersion,
    )
    from app.models.projects import ProjectParty, ProjectPurchaseOrder

    from app.models.projects import Project

    quotation = env["quotation"]
    project = db.query(Project).filter(Project.id == quotation.project_id).one()
    party = ProjectParty(
        id=_uid(),
        company_id=env["company_id"],
        party_type="trading_house",
        name=f"{MARKER} Buimaco {_uid()[:6]}",
    )
    db.add(party)
    db.flush()
    po = ProjectPurchaseOrder(
        id=_uid(),
        company_id=env["company_id"],
        project_id=project.id,
        quotation_version_id=env["version"].id,
        po_source="trading_house",
        issuing_party_id=party.id,
        po_number=f"HQ/26/01/{_uid()[:4]}",
        po_date=date(2026, 1, 19),
        term_days=60,
        status="approved",
    )
    db.add(po)
    db.flush()
    po_version = ProjectPOVersion(
        id=_uid(),
        company_id=env["company_id"],
        purchase_order_id=po.id,
        version_no=1,
        extraction_state="done",
        confirmed_at=date.today(),
    )
    db.add(po_version)
    db.flush()
    db.add(
        ProjectPOLine(
            id=_uid(),
            company_id=env["company_id"],
            po_version_id=po_version.id,
            line_no=1,
            stock_code_raw=po_code or product_set.set_code,
            description_raw=f"{MARKER} {product_set.set_code}",
            qty=Decimal(po_qty),
            uom_raw="SETS",
            # the PO scan says 999.00; the quotation (900.00) must win
            unit_price=Decimal("999.00"),
            amount=(Decimal(po_qty) * Decimal("999.00")).quantize(Decimal("0.01")),
            is_cancelled=False,
            resolved_product_id=None,
            arithmetic_ok=None,
        )
    )
    schedule = DeliverySchedule(
        id=_uid(),
        company_id=env["company_id"],
        project_id=project.id,
        purchase_order_id=po.id,
        label=f"{MARKER} programme",
    )
    db.add(schedule)
    db.flush()
    schedule_version = DeliveryScheduleVersion(
        id=_uid(),
        company_id=env["company_id"],
        delivery_schedule_id=schedule.id,
        version_no=1,
        po_version_id=po_version.id,
        extraction_state="done",
        schedule_date=date(2026, 3, 4),
    )
    db.add(schedule_version)
    db.flush()
    return po, schedule_version


def _drafted_lines(db, result):
    from app.models.project_so import ProjectSalesOrderLine

    lines = []
    for order in result["data"]:
        lines.extend(
            db.query(ProjectSalesOrderLine)
            .filter(ProjectSalesOrderLine.project_sales_order_id == order["id"])
            .order_by(ProjectSalesOrderLine.line_no.asc())
            .all()
        )
    return lines


def test_e1_a_po_line_for_a_set_code_drafts_one_so_line_per_part():
    from app.services.project_so_draft_service import ProjectSODraftService

    with blank_session() as db:
        env = _env(db)
        product_set = _set(db, env["company_id"], env["category"], env["uom"])
        quoted = _set_line(db, env, unit_price="900.00", quantity="100")
        po, schedule_version = _so_chain(db, env, product_set, po_qty="100")

        result = ProjectSODraftService(db).build(po.id, schedule_version.id)
        lines = _drafted_lines(db, result)

        assert len(lines) == 3
        by_product = {line.product_id: line for line in lines}
        parts = quoted.set_parts_snapshot
        driver = next(p for p in parts if p["is_driver"])
        for part in parts:
            line = by_product[part["product_id"]]
            assert line.qty == Decimal("100") * Decimal(part["quantity"])
            assert line.explosion_source == "set"
            assert line.quotation_line_id == quoted.id
            if part["is_driver"]:
                assert line.unit_price == Decimal("900.00")
            else:
                assert line.unit_price == Decimal("0.00")
        assert by_product[driver["product_id"]].amount == Decimal("90000.00")


def test_e2_the_parts_come_from_the_frozen_copy_not_todays_set_master():
    from app.services.project_so_draft_service import ProjectSODraftService

    with blank_session() as db:
        env = _env(db)
        product_set = _set(db, env["company_id"], env["category"], env["uom"])
        quoted = _set_line(db, env, unit_price="900.00", quantity="10")
        quoted_products = {p["product_id"] for p in quoted.set_parts_snapshot}

        # the master moves on after the quote: a member is dropped and another added
        dropped = product_set.members[2]
        db.delete(dropped)
        replacement = _product(db, env["category"], env["uom"], "ZZT-REPLACEMENT")
        db.add(
            ProductSetMember(
                id=_uid(),
                product_set_id=product_set.id,
                product_id=replacement.id,
                quantity=Decimal("1"),
                contributes_to_price=False,
                sort_order=5,
            )
        )
        db.flush()
        db.expire(product_set)

        po, schedule_version = _so_chain(db, env, product_set, po_qty="10")
        result = ProjectSODraftService(db).build(po.id, schedule_version.id)
        lines = _drafted_lines(db, result)

        assert {line.product_id for line in lines} == quoted_products
        assert replacement.id not in {line.product_id for line in lines}


def test_e3_an_item_package_for_the_code_still_wins_over_the_set_line():
    from app.services.project_so_draft_service import ProjectSODraftService

    with blank_session() as db:
        db.execute(
            text(
                "CREATE TABLE IF NOT EXISTS item_packages ("
                " id uuid PRIMARY KEY, package_code varchar(100) NOT NULL,"
                " description varchar(255))"
            )
        )
        db.execute(
            text(
                "CREATE TABLE IF NOT EXISTS item_package_lines ("
                " id uuid PRIMARY KEY, item_package_id uuid NOT NULL, product_id uuid NOT NULL,"
                " line_sequence integer NOT NULL, uom varchar(100), qty numeric(15,4),"
                " unit_price numeric(15,2))"
            )
        )
        env = _env(db)
        product_set = _set(db, env["company_id"], env["category"], env["uom"])
        _set_line(db, env, unit_price="900.00", quantity="10")
        packaged_parent = _product(db, env["category"], env["uom"], "ZZT-PKG-PARENT")
        packaged_other = _product(db, env["category"], env["uom"], "ZZT-PKG-OTHER")
        package_id = _uid()
        db.execute(
            text("INSERT INTO item_packages (id, package_code) VALUES (:id, :code)"),
            {"id": package_id, "code": product_set.set_code},
        )
        for sequence, (product, qty) in enumerate(
            [(packaged_parent, "1"), (packaged_other, "3")], start=1
        ):
            db.execute(
                text(
                    "INSERT INTO item_package_lines"
                    " (id, item_package_id, product_id, line_sequence, uom, qty, unit_price)"
                    " VALUES (:id, :pkg, :product, :seq, 'UNIT', :qty, 0)"
                ),
                {"id": _uid(), "pkg": package_id, "product": product.id, "seq": sequence, "qty": qty},
            )
        po, schedule_version = _so_chain(db, env, product_set, po_qty="10")

        result = ProjectSODraftService(db).build(po.id, schedule_version.id)
        lines = _drafted_lines(db, result)

        assert {line.explosion_source for line in lines} == {"package"}
        assert {line.product_id for line in lines} == {packaged_parent.id, packaged_other.id}


# ------------------------------------------------------------------ routes


def test_c1_route_patching_a_set_creates_its_row_and_the_listing_reads_it_back(api):
    client, db, company_id, _user_id, _project = api
    category, uom = _catalogue(db)
    product_set = _set(db, company_id, category, uom)
    db.commit()
    series = _api_series(client)

    patched = client.patch(
        f"{BASE}/config/series/{series['id']}/sets/{product_set.id}",
        json={"selling_price": "1000.00", "max_discount_pct": "10.00"},
    )
    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert body["product_set_id"] == product_set.id
    assert body["set_code"] == product_set.set_code
    assert body["set_name"] == product_set.name
    assert body["part_codes"] == _codes(product_set)
    assert Decimal(str(body["selling_price"])) == Decimal("1000.00")
    assert Decimal(str(body["derived_floor"])) == Decimal("900.00")

    listed = client.get(f"{BASE}/config/series/{series['id']}/sets/rows")
    assert listed.status_code == 200, listed.text
    rows = listed.json()["data"]
    assert [r["product_set_id"] for r in rows] == [product_set.id]
    assert rows[0]["part_codes"] == _codes(product_set)


def test_c1_route_deleting_a_set_row_is_204_and_a_second_delete_is_404(api):
    client, db, company_id, _user_id, _project = api
    category, uom = _catalogue(db)
    product_set = _set(db, company_id, category, uom)
    db.commit()
    series = _api_series(client, "Removal")
    client.patch(
        f"{BASE}/config/series/{series['id']}/sets/{product_set.id}",
        json={"selling_price": "1000.00", "max_discount_pct": "10.00"},
    )

    first = client.delete(f"{BASE}/config/series/{series['id']}/sets/{product_set.id}")
    second = client.delete(f"{BASE}/config/series/{series['id']}/sets/{product_set.id}")

    assert first.status_code == 204, first.text
    assert second.status_code == 404, second.text
    assert second.json()["code"] == "series_set_not_found"


def test_c1_route_a_malformed_price_is_a_422(api):
    client, db, company_id, _user_id, _project = api
    category, uom = _catalogue(db)
    product_set = _set(db, company_id, category, uom)
    db.commit()
    series = _api_series(client, "Validation")

    response = client.patch(
        f"{BASE}/config/series/{series['id']}/sets/{product_set.id}",
        json={"selling_price": "not-a-number", "max_discount_pct": "10.00"},
    )

    assert response.status_code == 422, response.text


def test_c1_route_a_reader_cannot_edit_or_remove_a_series_set(api):
    client, db, company_id, user_id, _project = api
    category, uom = _catalogue(db)
    product_set = _set(db, company_id, category, uom)
    db.commit()
    series = _api_series(client, "Denied")

    reader, originals = _client(db, user_id, slugs=["projects.projects.view"])
    try:
        patched = reader.patch(
            f"{BASE}/config/series/{series['id']}/sets/{product_set.id}",
            json={"selling_price": "1.00", "max_discount_pct": "1.00"},
        )
        deleted = reader.delete(f"{BASE}/config/series/{series['id']}/sets/{product_set.id}")
        assert patched.status_code == 403, patched.text
        assert deleted.status_code == 403, deleted.text
    finally:
        _restore(originals)


def test_c1_route_the_sets_listing_is_view_gated_and_an_unknown_series_is_404(api):
    client, db, _company_id, user_id, _project = api

    missing = client.get(f"{BASE}/config/series/{_uid()}/sets/rows")
    assert missing.status_code == 404, missing.text

    stranger, originals = _client(db, user_id, slugs=[])
    try:
        denied = stranger.get(f"{BASE}/config/series/{_uid()}/sets/rows")
        assert denied.status_code == 403, denied.text
    finally:
        _restore(originals)


def test_c5_route_another_companys_set_is_not_found_on_a_series(api):
    client, db, company_id, _user_id, _project = api
    category, uom = _catalogue(db)
    mine = _set(db, company_id, category, uom)
    theirs = _foreign_set(db, code=f"ZZTOTHER{_uid()[:4]}")
    db.commit()
    series = _api_series(client, "Foreign")
    body = {"selling_price": "1.00", "max_discount_pct": "1.00"}

    ours = client.patch(f"{BASE}/config/series/{series['id']}/sets/{mine.id}", json=body)
    response = client.patch(f"{BASE}/config/series/{series['id']}/sets/{theirs}", json=body)

    assert ours.status_code == 200, ours.text
    assert response.status_code == 404, response.text
    assert response.json()["code"] == "product_set_not_found"


def test_a2_route_a_set_line_comes_back_with_product_set_id_and_set_parts(api):
    client, db, company_id, _user_id, project = api
    category, uom = _catalogue(db)
    product_set = _set(db, company_id, category, uom)
    db.commit()
    quotation = _api_quotation(client, project.id)

    response = client.post(
        f"{BASE}/quotation-versions/{quotation['current_version_id']}/lines",
        json={"product_set_id": product_set.id, "unit_price": "900.00", "quantity": "10"},
    )

    assert response.status_code in (200, 201), response.text
    body = response.json()
    assert body["product_set_id"] == product_set.id
    assert body["product_code"] == product_set.set_code
    assert [p["product_code"] for p in body["set_parts"]] == _codes(product_set)
    assert body["uom"] == "SET"


def test_a1_route_naming_both_a_product_and_a_set_is_a_422(api):
    client, db, company_id, _user_id, project = api
    category, uom = _catalogue(db)
    product_set = _set(db, company_id, category, uom)
    product = _product(db, category, uom, "ZZT-ROUTE-BOTH")
    db.commit()
    quotation = _api_quotation(client, project.id)

    response = client.post(
        f"{BASE}/quotation-versions/{quotation['current_version_id']}/lines",
        json={
            "product_id": product.id,
            "product_set_id": product_set.id,
            "unit_price": "1.00",
            "quantity": "1",
        },
    )

    assert response.status_code == 422, response.text


def test_b6_the_live_verdict_for_a_set_matches_what_the_save_decides(api):
    client, db, company_id, _user_id, project = api
    category, uom = _catalogue(db)
    listed = _set(db, company_id, category, uom)
    unlisted = _set(db, company_id, category, uom)
    db.commit()
    series = _api_series(client, "Verdict")
    client.patch(
        f"{BASE}/config/series/{series['id']}/sets/{listed.id}",
        json={"selling_price": "1000.00", "max_discount_pct": "10.00"},
    )
    quotation = _api_quotation(client, project.id)
    client.put(f"{BASE}/quotations/{quotation['id']}", json={"series_id": series["id"]})

    below = client.get(
        f"{BASE}/quotations/{quotation['id']}/line-verdict",
        params={"product_set_id": listed.id, "unit_price": "850.00"},
    )
    clear = client.get(
        f"{BASE}/quotations/{quotation['id']}/line-verdict",
        params={"product_set_id": listed.id, "unit_price": "900.00"},
    )
    outside = client.get(
        f"{BASE}/quotations/{quotation['id']}/line-verdict",
        params={"product_set_id": unlisted.id, "unit_price": "900.00"},
    )

    assert below.status_code == 200, below.text
    assert below.json()["is_non_standard"] is False
    assert below.json()["is_below_floor"] is True
    assert below.json()["floor_value"] == "900.00"
    assert below.json()["floor_level"] == "series"
    assert clear.json()["is_below_floor"] is False
    assert outside.json()["is_non_standard"] is True
