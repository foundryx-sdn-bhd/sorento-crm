"""RED tests for Apply, verification off and the shared apply mechanics (#1288, Lane A).

Covers AC-S1-26, AC-S1-27, AC-S2-05, AC-S2-06, AC-S2-07, AC-S2-13
(`cost-price-lane-a-test-list.md`), against `cost-price-api-contract.md` section 1.8.
Verification ON's own workflow (submit/decide/return/AC-S2-08) is
`tests/test_cost_price_verification.py`.

TEST-FIRST: nothing under test exists yet - see the module docstring of
`tests/test_cost_price_upload_routes.py` for why every import sits inside its test.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from tests.fixtures.cost_price.taiyang_shapes import LETTERHEAD_TEXT, simple_price_list_workbook
from tests.support.cost_price_env import UPLOAD_PERM, VIEW_PERM, cost_price_env


def _upload_one_changed_line(e, *, code="ZZCPC-APPLY-001", current=100, new=110, **kwargs):
    supplier = e.supplier(name=LETTERHEAD_TEXT)
    product = e.product(code=code)
    e.link(product, supplier, unit_cost=current, currency="CNY")
    data = simple_price_list_workbook([(code, "cfg", new)], letterhead=LETTERHEAD_TEXT)
    upload = e.upload(data, supplier_id=str(supplier.id), currency="CNY", **kwargs)
    assert upload.status_code == 201, upload.text
    return supplier, product, upload.json()


# --------------------------------------------------------------------------------- AC-S1-26


def test_verification_off_uploader_applies_directly(cost_price_env):
    e = cost_price_env
    user = e.user(UPLOAD_PERM, VIEW_PERM)
    e.as_user(user)
    e.seed_settings(cost_price_verification_enabled=False)

    _, _, uploaded = _upload_one_changed_line(e)
    set_id = uploaded["id"]

    r = e.apply(set_id)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "applied"
    assert body["verified"] is False
    assert body["applied_by_name"] in (user["name"], user["email"], "U") or body.get("applied_at")


# --------------------------------------------------------------------------------- AC-S1-27


def test_apply_writes_one_cost_row_per_line_with_set_dates_and_source(cost_price_env):
    """AC-S1-27's row shape, with a FUTURE start date. The line's `current_unit_cost` (100)
    was never backed by a cost list row - a plain `product_suppliers.unit_cost` an earlier
    writer set directly. With only a future-dated row nothing covers today, so the link
    keeps that 100 CNY until the row comes into force (Blocking 2 of the review at
    232e5706: never null the price). `test_apply_without_dates_sets_unit_cost_to_new_price`
    below is the undated counterpart, where the new row IS in force at once."""
    from app.models.cost_price import ProductSupplierCost
    from app.models.procurement import ProductSupplier

    e = cost_price_env
    user = e.user(UPLOAD_PERM, VIEW_PERM)
    e.as_user(user)
    e.seed_settings(cost_price_verification_enabled=False)

    # Relative, never a literal: "2026-10-01" stopped being the future on that day
    # (TEST-DATEBOMB-1001).
    start_date = (date.today() + timedelta(days=30)).isoformat()
    _, product, uploaded = _upload_one_changed_line(
        e, current=100, new=110, start_date=start_date
    )
    set_id = uploaded["id"]
    line = e.lines(set_id).json()["data"][0]

    r = e.apply(set_id)
    assert r.status_code == 200, r.text

    rows = e.db.query(ProductSupplierCost).filter_by(source_change_line_id=line["id"]).all()
    assert len(rows) == 1, rows
    row = rows[0]
    assert row.unit_cost == Decimal("110.00")
    assert row.currency == "CNY"
    assert str(row.start_date) == start_date
    assert row.end_date is None

    link = e.db.query(ProductSupplier).filter_by(product_id=product.id).one()
    # Nothing covers today (the only cost row starts in the future): the price stays.
    assert link.unit_cost == Decimal("100.00")
    assert link.currency == "CNY"


def test_apply_without_dates_sets_unit_cost_to_new_price(cost_price_env):
    """The undated (always) counterpart of the test above: with no start date the new
    cost row is in force immediately, so `unit_cost` becomes the new price at once
    (AC-CL-01, AC-CL-04)."""
    from app.models.procurement import ProductSupplier

    e = cost_price_env
    user = e.user(UPLOAD_PERM, VIEW_PERM)
    e.as_user(user)
    e.seed_settings(cost_price_verification_enabled=False)

    _, product, uploaded = _upload_one_changed_line(e, current=100, new=110)
    set_id = uploaded["id"]

    r = e.apply(set_id)
    assert r.status_code == 200, r.text

    link = e.db.query(ProductSupplier).filter_by(product_id=product.id).one()
    assert link.unit_cost == Decimal("110.00")
    assert link.currency == "CNY"


def test_apply_writes_nothing_for_unchanged_or_skipped_lines(cost_price_env):
    from app.models.cost_price import ProductSupplierCost

    e = cost_price_env
    user = e.user(UPLOAD_PERM, VIEW_PERM)
    e.as_user(user)
    e.seed_settings(cost_price_verification_enabled=False)

    supplier = e.supplier(name=LETTERHEAD_TEXT)
    unchanged_product = e.product(code="ZZCPC-UNCH-001")
    e.link(unchanged_product, supplier, unit_cost=100, currency="CNY")
    skip_product = e.product(code="ZZCPC-SKIP-001")
    e.link(skip_product, supplier, unit_cost=50, currency="CNY")

    data = simple_price_list_workbook(
        [("ZZCPC-UNCH-001", "cfg", 100), ("ZZCPC-SKIP-001", "cfg", 60)],
        letterhead=LETTERHEAD_TEXT,
    )
    upload = e.upload(data, supplier_id=str(supplier.id), currency="CNY")
    assert upload.status_code == 201, upload.text
    set_id = upload.json()["id"]

    lines = e.lines(set_id).json()["data"]
    skip_line = next(ln for ln in lines if ln["supplier_code"] == "ZZCPC-SKIP-001")
    e.patch_line(set_id, skip_line["id"], {"skipped": True, "skip_reason": "not changing this one"})

    r = e.apply(set_id)
    assert r.status_code == 200, r.text

    assert e.db.query(ProductSupplierCost).count() == 0


# --------------------------------------------------------------------------------- AC-S2-05


def test_new_link_lead_time_is_the_suppliers_most_common(cost_price_env):
    from app.models.procurement import ProductSupplier

    e = cost_price_env
    user = e.user(UPLOAD_PERM, VIEW_PERM)
    e.as_user(user)
    e.seed_settings(cost_price_verification_enabled=False)
    supplier = e.supplier(name=LETTERHEAD_TEXT)
    e.link(e.product(code="ZZCPC-LT-A"), supplier, lead_time_days=30)
    e.link(e.product(code="ZZCPC-LT-B"), supplier, lead_time_days=30)
    e.link(e.product(code="ZZCPC-LT-C"), supplier, lead_time_days=45)
    new_product = e.product(code="ZZCPC-LT-NEW")  # no existing link

    data = simple_price_list_workbook([("ZZCPC-LT-NEW", "cfg", 80)], letterhead=LETTERHEAD_TEXT)
    upload = e.upload(data, supplier_id=str(supplier.id), currency="CNY")
    assert upload.status_code == 201, upload.text
    set_id = upload.json()["id"]

    r = e.apply(set_id)
    assert r.status_code == 200, r.text

    new_link = e.db.query(ProductSupplier).filter_by(product_id=new_product.id).one()
    assert new_link.standard_lead_time_days == 30


def test_new_link_without_default_applies_with_no_lead_time(cost_price_env):
    """AC-S2-05 as amended (owner, 28 Sep 2026, round 6 R3): "don't need Lead time (days)".
    With no link to take a lead time from, Apply goes through and the new link has none."""
    from app.models.procurement import ProductSupplier

    e = cost_price_env
    user = e.user(UPLOAD_PERM, VIEW_PERM)
    e.as_user(user)
    e.seed_settings(cost_price_verification_enabled=False)
    supplier = e.supplier(name=LETTERHEAD_TEXT)  # no existing links at all
    new_product = e.product(code="ZZCPC-LT-NODEFAULT")

    data = simple_price_list_workbook(
        [("ZZCPC-LT-NODEFAULT", "cfg", 80)], letterhead=LETTERHEAD_TEXT
    )
    upload = e.upload(data, supplier_id=str(supplier.id), currency="CNY")
    assert upload.status_code == 201, upload.text
    set_id = upload.json()["id"]

    r = e.apply(set_id)
    assert r.status_code == 200, r.text
    new_link = e.db.query(ProductSupplier).filter_by(product_id=new_product.id).one()
    assert new_link.standard_lead_time_days is None


# --------------------------------------------------------------------------------- AC-S2-06


def test_stale_line_blocks_apply_and_writes_nothing(cost_price_env):
    from app.models.cost_price import ProductSupplierCost
    from app.models.procurement import ProductSupplier

    e = cost_price_env
    user = e.user(UPLOAD_PERM, VIEW_PERM)
    e.as_user(user)
    e.seed_settings(cost_price_verification_enabled=False)

    _, product, uploaded = _upload_one_changed_line(e, current=100, new=110)
    set_id = uploaded["id"]

    # Somebody else moves the live price after the upload was parsed.
    link = e.db.query(ProductSupplier).filter_by(product_id=product.id).one()
    link.unit_cost = Decimal("999.00")
    e.db.commit()

    r = e.apply(set_id)
    assert r.status_code == 409, r.text
    body = r.json()
    stale_lines = body["detail"]["lines"]
    assert stale_lines[0]["live_unit_cost"] == 999.0
    assert stale_lines[0]["recorded_unit_cost"] == 100.0

    assert e.db.query(ProductSupplierCost).count() == 0

    refreshed_lines = e.lines(set_id).json()["data"]
    assert refreshed_lines[0]["stale"] == {"live_unit_cost": 999.0, "live_currency": "CNY"}


# --------------------------------------------------------------------------------- AC-S2-07


def test_second_apply_is_409(cost_price_env):
    e = cost_price_env
    user = e.user(UPLOAD_PERM, VIEW_PERM)
    e.as_user(user)
    e.seed_settings(cost_price_verification_enabled=False)

    _, _, uploaded = _upload_one_changed_line(e)
    set_id = uploaded["id"]

    first = e.apply(set_id)
    assert first.status_code == 200, first.text

    second = e.apply(set_id)
    assert second.status_code == 409, second.text


# --------------------------------------------------------------------------------- AC-S2-13


def test_apply_leaves_product_prices_alone(cost_price_env):
    from app.models.product import Product

    e = cost_price_env
    user = e.user(UPLOAD_PERM, VIEW_PERM)
    e.as_user(user)
    e.seed_settings(cost_price_verification_enabled=False)

    _, product, uploaded = _upload_one_changed_line(e)
    set_id = uploaded["id"]
    before = (product.cost_price, product.list_price, product.invoice_price)

    r = e.apply(set_id)
    assert r.status_code == 200, r.text

    e.db.expire_all()
    after_product = e.db.query(Product).filter_by(id=product.id).one()
    assert (after_product.cost_price, after_product.list_price, after_product.invoice_price) == before
