"""QUOTATION-UX fix round 2 red tests.

1. GET /master-data/products/select returns description, brand_id, base_uom_id (never prices).
2. GET /project-sales/quotation-line-images?product_id= gives the photo / drawing cells of an
   editable line with no picks, so an unsaved line can show and pick them.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from .test_quotation_lines_grid import (
    BASE,
    MARKER,
    _drawing,
    _fixture_for_write,
    _photo,
    _uid,
    api,  # noqa: F401
)

URL = f"{BASE}/quotation-line-images"


def test_products_select_returns_description_brand_id_and_uom_id_but_no_prices(api):
    from app.models.product import Brand

    client, db, env, make_product, _version = _fixture_for_write(api)
    brand = Brand(id=_uid(), brand_code=f"ZZT{_uid()[:6]}", brand_name=f"{MARKER} Brand")
    db.add(brand)
    db.flush()
    product = make_product()
    product.brand_id = brand.id
    db.commit()

    response = client.get(
        "/api/v1/master-data/products/select", params={"query": product.product_code}
    )
    assert response.status_code == 200, response.text
    rows = [r for r in response.json()["data"] if r["id"] == product.id]
    assert rows, response.text
    row = rows[0]
    assert row["description"] == "Wall-hung basin"
    assert row["brand_id"] == brand.id
    assert row["base_uom_id"] == env["uom"].id
    assert "cost_price" not in row and "invoice_price" not in row


def test_line_images_returns_both_cells_with_candidates(api):
    client, db, env, make_product, _version = _fixture_for_write(api)
    product = make_product()
    p1 = _photo(db, env, product, "p1.jpg")
    p2 = _photo(db, env, product, "p2.jpg")
    d1 = _drawing(db, env, product, "d1.jpg")
    d2 = _drawing(db, env, product, "d2.jpg")
    db.commit()

    response = client.get(URL, params={"product_id": product.id})
    assert response.status_code == 200, response.text
    body = response.json()
    photo, drawing = body["product_image"], body["technical_drawing"]
    assert photo["state"] == "not_chosen"
    assert photo["candidate_count"] == 2
    assert {c["attachment_id"] for c in photo["candidates"]} == {p1.id, p2.id}
    assert drawing["candidate_count"] == 2
    assert {c["attachment_id"] for c in drawing["candidates"]} == {d1.id, d2.id}
    for c in photo["candidates"] + drawing["candidates"]:
        assert set(c) >= {"attachment_id", "filename", "url", "preview_url"}


def test_line_images_single_photo_is_the_photo(api):
    client, db, env, make_product, _version = _fixture_for_write(api)
    product = make_product()
    only = _photo(db, env, product, "only.jpg")
    db.commit()
    body = client.get(URL, params={"product_id": product.id}).json()
    assert body["product_image"]["state"] == "chosen"
    assert body["product_image"]["attachment_id"] == only.id


def test_line_images_drawing_defaults_to_first_uploaded(api):
    client, db, env, make_product, _version = _fixture_for_write(api)
    product = make_product()
    base = datetime(2025, 1, 1)
    _drawing(db, env, product, "newer.jpg", age=base + timedelta(days=3))
    oldest = _drawing(db, env, product, "older.jpg", age=base)
    db.commit()
    body = client.get(URL, params={"product_id": product.id}).json()
    assert body["technical_drawing"]["state"] == "default"
    assert body["technical_drawing"]["attachment_id"] == oldest.id


def test_line_images_other_company_product_is_404(api):
    from app.models.company import Company
    from app.models.product import Product

    client, db, env, make_product, _version = _fixture_for_write(api)
    other = Company(
        id=_uid(), name=f"{MARKER} Other Co", code=f"ZQ{_uid()[:8]}".upper(), is_active=True
    )
    db.add(other)
    db.flush()
    foreign = Product(
        id=_uid(),
        product_code=f"ZZT-{_uid()[:8]}",
        product_name=f"{MARKER} Foreign",
        category_id=env["category"].id,
        base_uom_id=env["uom"].id,
        list_price=Decimal("1.00"),
        company_id=other.id,
    )
    db.add(foreign)
    own = make_product()
    db.commit()
    # Guards against passing on a missing route: the caller's own product is served.
    assert client.get(URL, params={"product_id": own.id}).status_code == 200
    assert client.get(URL, params={"product_id": foreign.id}).status_code == 404
    assert client.get(URL, params={"product_id": _uid()}).status_code == 404


def test_line_images_malformed_id_is_422(api):
    client, *_ = api
    assert client.get(URL, params={"product_id": "not-a-uuid"}).status_code == 422
    assert client.get(URL).status_code == 422


def test_a_new_line_saved_through_the_bulk_write_keeps_complete_set(api):
    from .test_project_quotation_bulk_lines import _bulk

    client, db, env, make_product, version_id = _fixture_for_write(api)
    product = make_product()
    db.commit()
    saved = _bulk(
        client,
        version_id,
        [{"product_id": product.id, "unit_price": "10.00", "quantity": "1", "complete_set": "c/w seat"}],
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["data"][0]["complete_set"] == "c/w seat"
    again = client.get(f"{BASE}/quotation-versions/{version_id}/lines").json()["data"]
    assert again[0]["complete_set"] == "c/w seat"
