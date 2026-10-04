"""Stock Debt auxiliary columns (STOCKDEBT-AUX-COLS, AC-1..AC-7, AC-10..AC-12).

WRITTEN BEFORE THE IMPLEMENTATION EXISTS (Phase 2 is test-first). `pool_on_hand`,
`pool_po_qty`, `pool_incoming_qty`, `dealer_outstanding` on every row and on the envelope
`totals`, plus `show_aux` on the export, are named by
`documentation/plans/scm/stockdebt-aux-cols-acceptance-criteria.md` and none exist yet.
Nothing here imports a not-yet-existing symbol at module level, so the file collects and each
test fails on its own missing field / column / param.

Postgres only (`requires_pg`); every test seeds its own chain with a `ZZTSD` marker and fake
product codes.
"""
from __future__ import annotations

import math
from datetime import date
from decimal import Decimal
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, text

from tests.scm.conftest import SORENTO_COMPANY_ID, requires_pg, seed_user
from tests.scm.test_stock_debt_export import _sheets, _task
from tests.scm.test_stock_debt_routes import (
    BASE,
    _agent,
    _client,
    _demand,
    _product,
    _row_of,
    _spo,
    _stock,
    _supplier,
    _u,
    _warehouse,
)

pytestmark = requires_pg

AUX_KEYS = ("pool_on_hand", "pool_po_qty", "pool_incoming_qty", "dealer_outstanding")
AUX_HEADERS = [
    "BRW On Hand Qty", "BRW PO Qty", "BRW Incoming Qty", "Dealer O/standing Qty",
]
D_FROM = date(2027, 3, 1)
D_TO = date(2027, 3, 31)


# --------------------------------------------------------------------------- seeding


def _marker() -> str:
    return f"ZZTSD{_u()[:6]}".upper()


def _pool(db, suffix="BB"):
    return _warehouse(db, f"ZZTBRW{_u()[:4]}-{suffix}")


def _project_bin(db):
    wh = _warehouse(db, f"ZZTPRJ{_u()[:4]}-BB")
    wh.segment = "project"
    db.flush()
    return wh


def _unavailable_pool(db):
    wh = _warehouse(db, f"ZZTQUA{_u()[:4]}-BB")
    wh.counts_as_available = False
    db.flush()
    return wh


def _present(db, product, warehouse, marker, tag="P", required=None):
    """A project-class line so the product is on the board (the board lists a product that
    has stock, demand or incoming at a planning bin); project lines never count as dealer."""
    return _demand(
        db, product, warehouse, qty=1, required_date=required or date(2027, 3, 10),
        so_number=f"{marker}-{tag}",
    )


def _retail(db, product, warehouse, *, qty, required_date, so, delivered=0,
            demand_class="retail", purchasing_status=None, required_qty=None):
    order, line = _demand(
        db, product, warehouse, qty=qty, required_date=required_date, so_number=so,
        qty_delivered=delivered, qty_required=required_qty,
    )
    order.demand_class = demand_class
    if purchasing_status is not None:
        line.purchasing_status = purchasing_status
    db.flush()
    return order, line


def _po(db, product, warehouse, *, qty, received=0, supplier=None):
    from app.models.procurement import PurchaseOrder, PurchaseOrderLine

    po = PurchaseOrder(
        id=_u(), po_number=f"ZZTPO{_u()[:6]}".upper(),
        supplier_id=supplier.id if supplier else None,
        issue_date=date(2026, 9, 1), status="active", company_id=SORENTO_COMPANY_ID,
    )
    db.add(po)
    db.flush()
    db.add(
        PurchaseOrderLine(
            id=_u(), purchase_order_id=po.id, product_id=product.id,
            warehouse_id=warehouse.id if warehouse is not None else None,
            qty_ordered=Decimal(str(qty)), qty_received=Decimal(str(received)),
            line_status="open", company_id=SORENTO_COMPANY_ID,
        )
    )
    db.flush()
    return po


def _aux(row: dict) -> dict:
    missing = [k for k in AUX_KEYS if k not in row]
    assert not missing, f"row lacks {missing}: {sorted(row)}"
    return {k: row[k] for k in AUX_KEYS}


def _listing(db, marker, **kw):
    from app.services.scm.stock_debt_service import StockDebtService

    kw.setdefault("only_debt", False)
    kw.setdefault("limit", 50)
    return StockDebtService(db).list(query=marker, **kw)


# --------------------------------------------------------------------------- AC-2


def test_pool_on_hand_sums_available_pool_stock_only(scm_app):
    """AC-2: SUM quantity_on_hand over active non-project warehouses that count as
    available; project-bin stock and an unavailable bin are not counted; no stock reads 0."""
    from app.services.scm.summary_order_service import _pool_on_hand_map

    app, db = _client(scm_app)
    marker = _marker()
    pool_a, pool_b = _pool(db), _pool(db)
    prod = _product(db, f"{marker}-A")
    bare = _product(db, f"{marker}-B")
    _stock(db, prod, pool_a, 40)
    _stock(db, prod, pool_b, 5)
    _stock(db, prod, _project_bin(db), 100)
    _stock(db, prod, _unavailable_pool(db), 9)
    _present(db, prod, pool_a, marker, "A")
    _present(db, bare, pool_a, marker, "B")

    body = _listing(db, marker)
    assert _aux(_row_of(body, prod.product_code))["pool_on_hand"] == 45
    assert _aux(_row_of(body, bare.product_code))["pool_on_hand"] == 0
    assert _pool_on_hand_map(db, [str(prod.id)])[str(prod.id)] == 45


# --------------------------------------------------------------------------- AC-3


def test_pool_po_qty_counts_open_pool_lines_only(scm_app):
    """AC-3: open PO lines at pool warehouses; a project bin, a line with no warehouse, and
    an over-received line add nothing."""
    from app.services.scm.site_pool_supply import open_po_by_product

    app, db = _client(scm_app)
    marker = _marker()
    pool = _pool(db)
    prod = _product(db, f"{marker}-A")
    _present(db, prod, pool, marker)
    _po(db, prod, pool, qty=30, received=10)       # 20 owed
    _po(db, prod, pool, qty=5, received=8)         # over-received -> 0
    _po(db, prod, _project_bin(db), qty=50)        # project bin -> not counted
    _po(db, prod, None, qty=70)                    # no warehouse -> not counted

    row = _row_of(_listing(db, marker), prod.product_code)
    assert _aux(row)["pool_po_qty"] == 20
    assert open_po_by_product(db, [str(prod.id)])[str(prod.id)]["qty"] == 20


# --------------------------------------------------------------------------- AC-4


def test_pool_incoming_qty_counts_open_spo_at_pool_only(scm_app):
    """AC-4: open, not-received SPO allocations at pool warehouses; a fully received one
    and one at a project bin add nothing."""
    from app.services.scm.site_pool_supply import open_spo_by_product

    app, db = _client(scm_app)
    marker = _marker()
    pool = _pool(db)
    prod = _product(db, f"{marker}-A")
    _present(db, prod, pool, marker)
    _spo(db, prod, pool, qty=60, arrives=date(2027, 3, 20))
    _spo(db, prod, pool, qty=25, arrives=date(2027, 3, 21), received=25,
         receipt_status="received", line_status="closed")
    _spo(db, prod, _project_bin(db), qty=40, arrives=date(2027, 3, 22))

    row = _row_of(_listing(db, marker), prod.product_code)
    assert _aux(row)["pool_incoming_qty"] == 60
    assert open_spo_by_product(db, [str(prod.id)])[str(prod.id)]["qty"] == 60


# --------------------------------------------------------------------------- AC-5 / 5b


def _dealer_case(db, marker):
    pool = _pool(db)
    prod = _product(db, f"{marker}-A")
    _present(db, prod, pool, marker)
    _retail(db, prod, pool, qty=5, required_date=date(2027, 2, 15), so=f"{marker}-R1")   # before
    _retail(db, prod, pool, qty=9, delivered=2, required_date=date(2027, 3, 10),
            so=f"{marker}-R2")                                                          # 7 inside
    _retail(db, prod, pool, qty=11, required_date=date(2027, 4, 20), so=f"{marker}-R3")  # after
    _retail(db, prod, pool, qty=13, required_date=None, so=f"{marker}-R4")               # no date
    _retail(db, prod, pool, qty=17, required_date=date(2027, 3, 12), so=f"{marker}-R5",
            demand_class="project")                                                     # project
    _retail(db, prod, pool, qty=19, required_date=date(2027, 3, 12), so=f"{marker}-R6",
            purchasing_status="covered")                                                # covered
    _retail(db, prod, pool, qty=23, required_date=date(2027, 3, 12), so=f"{marker}-R7",
            demand_class=None)                                                          # unclassified
    return prod


def test_dealer_outstanding_is_negative_and_windowed_by_start_and_end(scm_app):
    """AC-5/5b: retail lines inside [date_from, date_to] plus the undated one (7 + 13 = 20),
    printed NEGATIVE (-20); before-start, after-end, project, covered, unclassified ignored."""
    from app.services.scm.summary_order_service import _demand_aggregates

    app, db = _client(scm_app)
    marker = _marker()
    prod = _dealer_case(db, marker)

    row = _row_of(_listing(db, marker, date_from=D_FROM, date_to=D_TO), prod.product_code)
    assert _aux(row)["dealer_outstanding"] == -20
    agg = _demand_aggregates(db, [str(prod.id)], horizon_start=D_FROM, horizon=D_TO)
    assert row["dealer_outstanding"] == -agg[str(prod.id)]["dealer_qty"]


def test_dealer_outstanding_unset_bounds_do_not_narrow(scm_app):
    """AC-5: an unset bound does not narrow - 5 + 7 + 11 + 13 = 36 -> -36."""
    app, db = _client(scm_app)
    marker = _marker()
    prod = _dealer_case(db, marker)

    row = _row_of(_listing(db, marker), prod.product_code)
    assert _aux(row)["dealer_outstanding"] == -36


def test_dealer_outstanding_zero_is_plain_zero_never_negative_zero(scm_app):
    """AC-5b: no outstanding retail demand reads 0, never -0."""
    app, db = _client(scm_app)
    marker = _marker()
    pool = _pool(db)
    prod = _product(db, f"{marker}-A")
    _present(db, prod, pool, marker)

    value = _aux(_row_of(_listing(db, marker), prod.product_code))["dealer_outstanding"]
    assert value == 0
    assert math.copysign(1.0, value) > 0, "dealer_outstanding is -0.0"


# --------------------------------------------------------------------------- AC-6


def test_values_do_not_change_with_book_or_supplier_filter(scm_app):
    """AC-6: Book and Supplier decide which rows show, never what the four numbers read."""
    from app.models.procurement import ProductSupplier

    app, db = _client(scm_app)
    marker = _marker()
    pool = _pool(db)
    sup_a, sup_b = _supplier(db, f"ZZTSA{_u()[:4]}".upper()), _supplier(db, f"ZZTSB{_u()[:4]}".upper())
    prod = _product(db, f"{marker}-A")
    other = _product(db, f"{marker}-B")
    for p, s in ((prod, sup_a), (other, sup_b)):
        db.add(ProductSupplier(
            id=_u(), product_id=p.id, supplier_id=s.id, standard_lead_time_days=7,
            is_primary_supplier=True, company_id=SORENTO_COMPANY_ID,
        ))
        _present(db, p, pool, marker, p.product_code[-1])
    db.flush()
    _stock(db, prod, pool, 40)
    _po(db, prod, pool, qty=30, received=10, supplier=sup_a)
    _spo(db, prod, pool, qty=60, arrives=date(2027, 3, 20))
    _retail(db, prod, pool, qty=7, required_date=date(2027, 3, 10), so=f"{marker}-R1")

    base = _aux(_row_of(_listing(db, marker), prod.product_code))
    assert base == {
        "pool_on_hand": 40, "pool_po_qty": 20, "pool_incoming_qty": 60,
        "dealer_outstanding": -7,
    }
    seen = 0
    for book in ("all", "project", "retail"):
        body = _listing(db, marker, book=book)
        if any(r["product_code"] == prod.product_code for r in body["data"]):
            seen += 1
            assert _aux(_row_of(body, prod.product_code)) == base, book
    assert seen >= 1
    narrowed = _listing(db, marker, supplier_ids=[str(sup_a.id)])
    assert [r["product_code"] for r in narrowed["data"]] == [prod.product_code]
    assert _aux(_row_of(narrowed, prod.product_code)) == base


# --------------------------------------------------------------------------- AC-7


def test_aux_reads_are_batched_not_per_product(scm_app):
    """AC-7: four batch reads per request - the statement count must not grow with the
    number of products on the board."""
    app, db = _client(scm_app)
    marker = _marker()
    pool = _pool(db)
    first = _product(db, f"{marker}-A")
    _present(db, first, pool, marker, "A")

    def count(m):
        n = {"v": 0}

        def _tick(*_a, **_k):
            n["v"] += 1

        bind = db.get_bind()
        event.listen(bind, "before_cursor_execute", _tick)
        try:
            body = _listing(db, m)
        finally:
            event.remove(bind, "before_cursor_execute", _tick)
        return n["v"], body

    one, body_one = count(marker)
    _aux(body_one["data"][0])
    for tag in "BCDE":
        extra = _product(db, f"{marker}-{tag}")
        _present(db, extra, pool, marker, tag)
    many, body_many = count(marker)
    assert len(body_many["data"]) == 5
    assert all(set(AUX_KEYS) <= set(r) for r in body_many["data"])
    assert many == one, f"statements grew with products: {one} -> {many}"


# --------------------------------------------------------------------------- AC-10


def test_totals_sum_the_whole_filtered_set_not_the_page(scm_app):
    """AC-10: envelope totals carry the four sums over the WHOLE filtered set; limit=1
    must not shrink them; dealer total is negative."""
    app, db = _client(scm_app)
    marker = _marker()
    pool = _pool(db)
    for tag, stock, po, spo, dealer in (("A", 40, 30, 60, 7), ("B", 2, 4, 8, 5)):
        prod = _product(db, f"{marker}-{tag}")
        _present(db, prod, pool, marker, tag)
        _stock(db, prod, pool, stock)
        _po(db, prod, pool, qty=po)
        _spo(db, prod, pool, qty=spo, arrives=date(2027, 3, 20))
        _retail(db, prod, pool, qty=dealer, required_date=date(2027, 3, 10),
                so=f"{marker}-R{tag}")

    body = _listing(db, marker, limit=1)
    assert len(body["data"]) == 1
    totals = body["totals"]
    missing = [k for k in AUX_KEYS if k not in totals]
    assert not missing, f"totals lacks {missing}: {sorted(totals)}"
    assert totals["pool_on_hand"] == 42
    assert totals["pool_po_qty"] == 34
    assert totals["pool_incoming_qty"] == 68
    assert totals["dealer_outstanding"] == -12


# --------------------------------------------------------------------------- AC-1 route


def test_route_returns_the_four_fields_on_rows_and_totals(scm_app):
    """AC-1: `response_model` silently drops undeclared fields - assert them through HTTP."""
    app, db = _client(scm_app)
    marker = _marker()
    pool = _pool(db)
    prod = _product(db, f"{marker}-A")
    _present(db, prod, pool, marker)
    _stock(db, prod, pool, 40)
    _retail(db, prod, pool, qty=7, required_date=date(2027, 3, 10), so=f"{marker}-R1")

    with TestClient(app) as c:
        got = c.get(BASE, params={"query": marker, "only_debt": False})
    assert got.status_code == 200, got.text
    body = got.json()
    row = _row_of(body, prod.product_code)
    assert _aux(row)["pool_on_hand"] == 40
    assert row["dealer_outstanding"] == -7
    for key in AUX_KEYS:
        assert key in body["totals"], f"totals dropped {key}: {sorted(body['totals'])}"
    assert body["totals"]["pool_on_hand"] == 40
    assert body["totals"]["dealer_outstanding"] == -7


# --------------------------------------------------------------------------- AC-11 / 12


def _export_world(db, marker):
    from app.models.procurement import ProductSupplier

    pool = _pool(db)
    sup = _supplier(db, f"ZZTSX{_u()[:4]}".upper())
    sup.supplier_name = "ZZT Aux Supplier"
    db.flush()
    prods = []
    for tag, stock, po, spo, dealer in (("A", 40, 30, 60, 7), ("B", 2, 4, 8, 5)):
        prod = _product(db, f"{marker}-{tag}")
        db.add(ProductSupplier(
            id=_u(), product_id=prod.id, supplier_id=sup.id, standard_lead_time_days=7,
            is_primary_supplier=True, company_id=SORENTO_COMPANY_ID,
        ))
        _present(db, prod, pool, marker, tag)
        _stock(db, prod, pool, stock)
        _po(db, prod, pool, qty=po, supplier=sup)
        _spo(db, prod, pool, qty=spo, arrives=date(2027, 3, 20))
        _retail(db, prod, pool, qty=dealer, required_date=date(2027, 3, 10),
                so=f"{marker}-R{tag}")
        prods.append(prod)
    return prods


def test_export_with_show_aux_adds_four_numeric_columns_and_totals(scm_app):
    """AC-11: Product, Name, Category, Supplier, <4 aux>, months, TBA, Total; numeric cells;
    Total row sums them (dealer negative)."""
    from app.services.scm.stock_debt_service import StockDebtService

    app, db = _client(scm_app)
    marker = _marker()
    prods = _export_world(db, marker)
    svc = StockDebtService(db)
    listing = svc.list(query=marker, only_debt=False, limit=50)

    blob, _ct, _fn, _counts = svc.export(
        query=marker, only_debt=False, split="none", show_aux=True,
    )
    ws = _sheets(blob)["Stock debt"]
    header = [c.value for c in ws[1]]
    assert header[:4] == ["Product", "Name", "Category", "Supplier"], header
    assert header[4:8] == AUX_HEADERS, header
    assert header[-2:] == ["TBA", "Total"], header
    assert len(header) == 10 + len(listing["months"]), header

    by_code = {ws.cell(row=r, column=1).value: r for r in range(2, ws.max_row)}
    a = by_code[prods[0].product_code]
    assert [ws.cell(row=a, column=c).value for c in range(5, 9)] == [40, 20 + 10, 60, -7] \
        or [ws.cell(row=a, column=c).value for c in range(5, 9)] == [40, 30, 60, -7]
    for c in range(5, 9):
        assert isinstance(ws.cell(row=a, column=c).value, (int, float)), c
    total = [c.value for c in ws[ws.max_row]]
    assert total[0] == "Total", total
    assert total[4:8] == [42, 34, 68, -12], total


@pytest.mark.parametrize("kwargs", [{"show_aux": False}, {}])
def test_export_without_show_aux_is_exactly_todays_workbook(scm_app, kwargs):
    """AC-11/AC-12: off (or omitted) = today's columns, no aux headers."""
    from app.services.scm.stock_debt_service import StockDebtService

    app, db = _client(scm_app)
    marker = _marker()
    _export_world(db, marker)
    svc = StockDebtService(db)
    listing = svc.list(query=marker, only_debt=False, limit=50)
    blob, _ct, _fn, _c = svc.export(query=marker, only_debt=False, split="none", **kwargs)
    header = [c.value for c in _sheets(blob)["Stock debt"][1]]
    assert len(header) == 6 + len(listing["months"]), header
    assert not set(AUX_HEADERS) & set(header), header


def test_export_split_by_supplier_sheets_carry_the_aux_columns(scm_app):
    """AC-11: split sheets carry the four columns and a Total row summing them."""
    from app.services.scm.stock_debt_service import StockDebtService

    app, db = _client(scm_app)
    marker = _marker()
    _export_world(db, marker)
    blob, _ct, _fn, _c = StockDebtService(db).export(
        query=marker, only_debt=False, split="supplier", show_aux=True,
    )
    wb = _sheets(blob)
    ws = wb["ZZT Aux Supplier"]
    assert [c.value for c in ws[1]][4:8] == AUX_HEADERS
    assert [c.value for c in ws[ws.max_row]][4:8] == [42, 34, 68, -12]


def test_export_route_threads_show_aux_into_the_job_filters(scm_app, monkeypatch):
    """AC-12: body `show_aux` reaches the enqueued task's filters; omitted = False."""
    from app.services import queue_service

    calls: list[dict] = []
    monkeypatch.setattr(
        queue_service, "enqueue_job",
        lambda func, *a, **k: calls.append({"args": a}) or type("J", (), {"id": "j"})(),
    )
    app, db = _client(scm_app)
    with TestClient(app) as c:
        resp = c.post(f"{BASE}/export", json={"split": "none", "show_aux": True})
    assert resp.status_code == 201, resp.text
    assert calls[0]["args"][2].get("show_aux") is True, calls[0]["args"][2]

    # one client: clear the first row's in-flight state so the 409 guard lets the second in
    db.execute(text("UPDATE user_downloads SET status = 'ready'"))
    with TestClient(app) as c:
        resp = c.post(f"{BASE}/export", json={"split": "none"})
    assert resp.status_code == 201, resp.text
    assert calls[1]["args"][2].get("show_aux") is False, calls[1]["args"][2]


def test_export_task_passes_show_aux_to_the_service(scm_app, monkeypatch):
    """AC-12: `generate_stock_debt_xlsx` threads `show_aux` into `StockDebtService.export`."""
    from app.services.download_service import DownloadService
    from tests.scm.test_order_sheet_export_downloads import _NoCloseSession

    export_tasks, task_fn = _task()
    app, db = _client(scm_app)
    user_id = db.execute(text("SELECT id FROM users LIMIT 1")).scalar()
    download = DownloadService(db).create(
        user_id=str(user_id), kind="stock_debt_xlsx", filename="zzt-aux.xlsx",
    )
    db.flush()
    monkeypatch.setattr(export_tasks, "SessionLocal", lambda: _NoCloseSession(db))
    seen: dict = {}

    def _fake_export(self, **kwargs):
        seen.update(kwargs)
        raise RuntimeError("stop after capture")

    monkeypatch.setattr(
        "app.services.scm.stock_debt_service.StockDebtService.export", _fake_export
    )
    task_fn(str(download.id), str(user_id), {"split": "none", "show_aux": True})
    assert seen.get("show_aux") is True, seen
