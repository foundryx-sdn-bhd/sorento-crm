"""Phase 2 RED tests - per path, a MOCHA-only contact over REST via X-API-Key (AC-7..AC-15).

`PLAN-contact-brand-scope-4oct.md` test items 5 and 9. The real `apply_company_scope`
runs (it is where the brand scope is stamped, AC-7); only authentication is stubbed. Each
path has a scoped call (MOCHA only) and an unscoped call (all three, AC-6 regression).

Products: MOCHA (in scope), SORENTO (out), no brand (out, Q1). Mixed orders carry a MOCHA line
(2 x 100.00) and a SORENTO line (3 x 1000.00).
"""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from tests._brand_scope_seed import api, db, leaks, world  # noqa: F401  (fixtures by name)

BASE = "/api/v1"
Y = {"date_from": "2026-01-01", "date_to": "2026-12-31"}

#: (id, path, params) - every path whose answer can name a product. The mocha code must
#: appear for the scoped contact; the other two must not; the unscoped contact sees all.
NAMING_PATHS = [
    ("stock_balance", f"{BASE}/inventory/stock/balance", {"query": "ZZT"}),
    ("products_search", f"{BASE}/master-data/products", {"query": "ZZT"}),
    ("incoming_by_product", f"{BASE}/incoming-stock/by-product", {"query": "ZZT"}),
    ("incoming_list_shipment_products", f"{BASE}/incoming-stock/list", {"query": "ZZT"}),
    ("orders", f"{BASE}/order-management/orders", {"query": "ZZT"}),
    ("orders_by_product", f"{BASE}/order-management/orders/by-product", {"product_query": "ZZT"}),
    ("outstanding_report", f"{BASE}/order-management/outstanding-report", {"customer_query": "ZZT BRAND"}),
    ("top_selling", f"{BASE}/order-management/top-selling", {"rank_by": "quantity", **Y}),
    ("current_stock_list", f"{BASE}/resource-management/attachments/current-stock-list", {}),
]
IDS = [p[0] for p in NAMING_PATHS]


@pytest.mark.parametrize(("name", "path", "params"), NAMING_PATHS, ids=IDS)
def test_scoped_contact_never_sees_other_brand_or_unbranded_products(api, world, name, path, params) -> None:
    """AC-7 / AC-8 / AC-11 / AC-12 / AC-13 / AC-14: MOCHA only."""
    resp = api.get(path, world.scoped, **params)
    assert resp.status_code == 200, resp.text
    seen = leaks(resp, world)
    assert seen == {"mocha": True, "sorento": False, "null": False}, (name, seen)


@pytest.mark.parametrize(("name", "path", "params"), NAMING_PATHS, ids=IDS)
def test_unscoped_contact_sees_every_product_as_today(api, world, name, path, params) -> None:
    """AC-6: NULL brand_ids is byte-identical to today."""
    resp = api.get(path, world.unscoped, **params)
    assert resp.status_code == 200, resp.text
    assert leaks(resp, world) == {"mocha": True, "sorento": True, "null": True}, name


# --------------------------------------------------------------------- AC-8 stock replies


def _ghost() -> str:
    return "ZZT-GHOST-" + uuid.uuid4().hex[:8].upper()


@pytest.mark.parametrize("which", ["sorento", "null"])
def test_out_of_scope_code_gets_the_reply_of_a_nonexistent_code(api, world, which) -> None:
    """AC-8 / Q4: the reply for a SORENTO or unbranded code equals the reply for a code that
    does not exist (the typed code itself aside): no stock, no ETA, no did-you-mean."""
    code, ghost = world.codes[which], _ghost()
    out = api.get(f"{BASE}/inventory/stock/balance", world.scoped, query=code)
    miss = api.get(f"{BASE}/inventory/stock/balance", world.scoped, query=ghost)
    assert out.status_code == miss.status_code == 200, (out.text, miss.text)
    assert out.text.replace(code, "<X>") == miss.text.replace(ghost, "<X>")
    assert out.json()["data"] == []


def test_in_scope_code_is_answered_as_today(api, world) -> None:
    """AC-8: the MOCHA code still returns its stock."""
    resp = api.get(f"{BASE}/inventory/stock/balance", world.scoped, query=world.codes["mocha"])
    rows = resp.json()["data"]
    assert [r["product"]["product_code"] for r in rows] == [world.codes["mocha"]]
    assert rows[0]["quantity_on_hand"] == 50


@pytest.mark.parametrize("which", ["sorento", "null"])
def test_stale_product_id_of_an_out_of_scope_product_returns_nothing(api, world, which) -> None:
    """AC-10: a numbered pick / follow-up carrying an old product uuid returns no stock."""
    pid = {"sorento": world.p_sorento, "null": world.p_null}[which].id
    resp = api.get(f"{BASE}/inventory/stock/balance", world.scoped, product_ids=str(pid))
    assert resp.status_code in (200, 404), resp.text
    assert world.codes[which] not in resp.text
    if resp.status_code == 200:
        assert resp.json()["data"] == []


@pytest.mark.parametrize("which", ["sorento", "null"])
def test_out_of_scope_incoming_code_gets_the_nonexistent_code_reply(api, world, which) -> None:
    """AC-8 / AC-11: ETA for an out-of-scope code reads like an unknown code."""
    code, ghost = world.codes[which], _ghost()
    out = api.get(f"{BASE}/incoming-stock/by-product", world.scoped, query=code)
    miss = api.get(f"{BASE}/incoming-stock/by-product", world.scoped, query=ghost)
    assert out.status_code == miss.status_code, (out.text, miss.text)
    assert out.text.replace(code, "<X>") == miss.text.replace(ghost, "<X>")


# --------------------------------------------------------------------- AC-11 incoming


def test_shipment_headers_count_only_in_scope_lines(api, world) -> None:
    """AC-11: the shipment header's remaining quantity and product count come from MOCHA lines."""
    scoped = api.get(f"{BASE}/incoming-stock/shipments", world.scoped, query="ZZT").json()["data"]
    assert len(scoped) == 1
    assert scoped[0]["total_remaining_incoming_quantity"] == 11
    assert scoped[0]["distinct_products_incoming"] == 1
    plain = api.get(f"{BASE}/incoming-stock/shipments", world.unscoped, query="ZZT").json()["data"]
    assert plain[0]["total_remaining_incoming_quantity"] == 33
    assert plain[0]["distinct_products_incoming"] == 3


def test_shipment_with_only_out_of_scope_lines_is_absent(api, world, db) -> None:
    """AC-11: a shipment none of whose lines is in scope is not returned at all."""
    from tests._mc_lookup_seed import inbound_shipment, inbound_shipment_line
    from app.services.company_scope import DEFAULT_COMPANY_ID

    ship = inbound_shipment(db, company_id=DEFAULT_COMPANY_ID)
    inbound_shipment_line(db, company_id=DEFAULT_COMPANY_ID, shipment_id=ship.id, product_id=world.p_sorento.id)
    db.commit()
    resp = api.get(f"{BASE}/incoming-stock/list", world.scoped, query="ZZT")
    assert ship.shipment_number not in resp.text
    unscoped = api.get(f"{BASE}/incoming-stock/list", world.unscoped, query="ZZT")
    assert ship.shipment_number in unscoped.text


# --------------------------------------------------------------------- AC-12 orders


def _do_by_number(resp, order) -> dict | None:
    return next((r for r in resp.json()["data"] if r["order_number"] == order.order_number), None)


def test_mixed_delivery_order_shows_only_in_scope_lines_and_their_amount(api, world) -> None:
    """AC-12 / Q3: the mixed DO comes back with its MOCHA line only, and the amount is the
    sum of the returned lines (100.00), not the header's 1100.00."""
    resp = api.get(f"{BASE}/order-management/orders", world.scoped, query="ZZT")
    row = _do_by_number(resp, world.do_mixed)
    assert row is not None, resp.text
    assert [ln["product"]["product_code"] for ln in row["lines"]] == [world.codes["mocha"]]
    assert Decimal(str(row["total_amount"])) == Decimal("100.00"), row["total_amount"]


def test_orders_with_no_in_scope_line_are_not_returned(api, world) -> None:
    """AC-12: a SORENTO-only DO and a NULL-brand-only DO are absent, and the total says so."""
    resp = api.get(f"{BASE}/order-management/orders", world.scoped, query="ZZT")
    assert _do_by_number(resp, world.do_sorento_only) is None
    assert _do_by_number(resp, world.do_null_only) is None
    assert resp.json()["pagination"]["total"] == 1


def test_orders_by_product_matched_products_are_in_scope_only(api, world) -> None:
    resp = api.get(f"{BASE}/order-management/orders/by-product", world.scoped, product_query="ZZT")
    data = resp.json()["data"]
    assert [r["order_number"] for r in data] == [world.do_mixed.order_number], resp.text
    assert [m["product_code"] for m in data[0]["matched_products"]] == [world.codes["mocha"]]
    assert resp.json()["pagination"]["total"] == 1


def test_outstanding_report_totals_come_from_in_scope_lines(api, world) -> None:
    """AC-12 / Q3: SO outstanding qty is the MOCHA line's 2 (unscoped: 2+3+5+4 = 14)."""
    params = {"customer_query": "ZZT BRAND"}
    scoped = api.get(f"{BASE}/order-management/outstanding-report", world.scoped, **params).json()
    assert scoped["so"]["outstanding_qty"] == 2, scoped["so"]
    assert scoped["so"]["so_count"] == 1, scoped["so"]
    assert scoped["do"]["pending_qty"] == 2, scoped["do"]
    assert scoped["do"]["do_count"] == 1, scoped["do"]
    assert [r["product_code"] for r in scoped["so_by_product"]] == [world.codes["mocha"]]
    plain = api.get(f"{BASE}/order-management/outstanding-report", world.unscoped, **params).json()
    assert plain["so"]["outstanding_qty"] == 14 and plain["do"]["pending_qty"] == 14


def test_outstanding_report_for_an_out_of_scope_product_reads_as_unknown(api, world) -> None:
    """AC-8 / AC-12: asking the report about a SORENTO code gives what a ghost code gives."""
    code, ghost = world.codes["sorento"], _ghost()
    out = api.get(f"{BASE}/order-management/outstanding-report", world.scoped, product_code=code)
    miss = api.get(f"{BASE}/order-management/outstanding-report", world.scoped, product_code=ghost)
    assert out.status_code == miss.status_code, (out.text, miss.text)
    assert out.text.replace(code, "<X>") == miss.text.replace(ghost, "<X>")


# --------------------------------------------------------------------- AC-13 reports


def test_top_selling_rows_and_totals_are_in_scope_only(api, world) -> None:
    body = api.get(f"{BASE}/order-management/top-selling", world.scoped, rank_by="quantity", **Y).json()
    assert [r["code"] for r in body["rows"]] == [world.codes["mocha"]], body
    assert body["total_count"] == 1
    assert body["totals"]["quantity"] == 2
    assert Decimal(str(body["totals"]["amount"])) == Decimal("100.00")
    plain = api.get(f"{BASE}/order-management/top-selling", world.unscoped, rank_by="quantity", **Y).json()
    assert plain["total_count"] == 3 and plain["totals"]["quantity"] == 9


def test_sales_report_total_is_computed_from_in_scope_lines(api, world) -> None:
    """AC-13: the report reads delivery-order lines. Scoped: the MOCHA DO line only (2 x,
    100.00); unscoped: every DO line (14), with a larger amount."""
    params = {"customer_query": "ZZT BRAND", **Y}
    scoped = api.get(f"{BASE}/order-management/sales-report", world.scoped, **params).json()
    assert scoped["total"]["qty"] == 2, scoped
    assert Decimal(str(scoped["total"]["amount"])) == Decimal("100.00")
    plain = api.get(f"{BASE}/order-management/sales-report", world.unscoped, **params).json()
    assert plain["total"]["qty"] == 14, plain
    assert Decimal(str(plain["total"]["amount"])) > Decimal(str(scoped["total"]["amount"]))


def test_sales_report_for_an_out_of_scope_product_code_reads_as_unknown(api, world) -> None:
    code, ghost = world.codes["sorento"], _ghost()
    out = api.get(f"{BASE}/order-management/sales-report", world.scoped, product_code=code, **Y)
    miss = api.get(f"{BASE}/order-management/sales-report", world.scoped, product_code=ghost, **Y)
    assert out.status_code == miss.status_code, (out.text, miss.text)
    assert out.text.replace(code, "<X>") == miss.text.replace(ghost, "<X>")


def test_sales_analysis_total_is_computed_from_in_scope_lines(api, world) -> None:
    """AC-13: the analysis total sums MOCHA lines only (100.00 open + 100.00 delivered SO)."""
    scoped = api.get(f"{BASE}/sales/analysis", world.scoped, basis="ordered").json()
    assert Decimal(str(scoped["totals"]["total"])) == Decimal("200.00"), scoped
    plain = api.get(f"{BASE}/sales/analysis", world.unscoped, basis="ordered").json()
    assert Decimal(str(plain["totals"]["total"])) == Decimal("2225.00"), plain


def test_low_stock_view_lists_in_scope_rows_only(db, world) -> None:
    """AC-13, at its service function: the route only returns `pending` and a file, so the
    workbook model `build_low_stock_view` (what the file and the page both print) is the
    testable seam. A scoped session sees the MOCHA row only."""
    from datetime import date, datetime

    from app.models.base import set_brand_scope
    from app.models.scm import OrderSummaryRow, ReorderRun
    from app.services.scm.low_stock_report_service import build_low_stock_view

    run = ReorderRun(
        id=str(uuid.uuid4()), status="completed", buy_scope="warehouse", source_system="scm",
        source_ref=f"ZZT-RUN-{uuid.uuid4().hex[:6]}", decision_grain="product",
        front_planning_contract_version=1,
    )
    db.add(run)
    db.flush()
    for prod in world.products:
        db.add(OrderSummaryRow(
            id=str(uuid.uuid4()), run_id=run.id, product_id=prod.id, as_of=date(2026, 9, 10),
            computed_at=datetime(2026, 9, 10, 6, 0, 0), pool_on_hand=10, reorder_level=100,
            suggested_qty=0,
        ))
    db.commit()

    def codes() -> set[str]:
        view = build_low_stock_view(db, run_id=str(run.id))
        return {row[0] for row in view["rows"]}

    assert codes() == set(world.codes.values())
    set_brand_scope(db, frozenset({world.mocha.id}))
    assert codes() == {world.codes["mocha"]}


def test_current_stock_list_links_only_in_scope_products(api, world) -> None:
    """AC-14: the stock list attachment names the products it is linked to; MOCHA only."""
    body = api.get(f"{BASE}/resource-management/attachments/current-stock-list", world.scoped).json()
    names = {p["name"] for p in body["linked_products"]}
    assert names == {world.codes["mocha"]}, names
