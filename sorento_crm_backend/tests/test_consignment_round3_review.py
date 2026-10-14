"""AC-CONSIGN-CRM fix round 1 (reviewer findings): R1 pool vs cancel, R2 return arrival order,
R3 released member, R4 sales figures leave a consignment out, R5 XS never names a consignment.
Every record is fake; Postgres only.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.autocount_cancel import AutocountCancelLine
from app.models.base import set_company_scope
from app.models.order import SalesOrder, SalesOrderLine
from app.models.procurement import PickingLine, SPOAllocation
from app.services.company_scope import DEFAULT_COMPANY_ID
from app.services.grn_spo_matching import build_allocation_pool
from app.services.procurement_service import PickingHeaderService

from tests._pg_fixture import blank_session, unique_code
from tests.test_grn_spo_forward_matching import SPO
from tests.test_incoming_stock_cancelled_sibling import (  # noqa: F401 - fixtures
    _alloc,
    _scope_listeners,
    world,
)
from tests.test_ingest_autocount_cancel_docs import _detail, _xs
from tests.test_ingest_autocount_consignments import (  # noqa: F401 - env is a fixture
    CG_INGEST,
    CR_INGEST,
    XS_INGEST,
    _cg,
    _cg_line,
    _cr,
    _cr_line,
    _line,
    env,
)


# ---------------------------------------------------------------------------- R1
def _cancel_with_grn(world, *, stated):
    product, wh = world.product(), world.warehouse()
    a = _alloc(world, product, wh, qty=10, line_no=1, cancelled=4, stated=stated)
    grn = world.grn(status="approved", spo_number=SPO, company_id=DEFAULT_COMPANY_ID)
    world.line(header_id=grn, product_id=product, warehouse_id=wh, quantity=3,
               allocation_id=a, company_id=DEFAULT_COMPANY_ID)
    world.db.commit()
    alloc = world.db.get(SPOAllocation, a)
    PickingHeaderService(world.db)._sync_received_for_allocations([alloc])
    world.db.flush()
    world.db.expire_all()
    return product, a


def _offered(world, product, a):
    pool = build_allocation_pool(world.db, product_id=product, spo_number=SPO)
    return {e.allocation_id: e.available for e in pool}.get(a)


def test_pool_offers_the_uncancelled_unreceived_part_while_stated_lags(world):
    product, a = _cancel_with_grn(world, stated=None)
    assert world.db.get(SPOAllocation, a).quantity_received == 7
    assert _offered(world, product, a) == 3


def test_pool_offers_the_same_part_once_stated_has_caught_up(world):
    product, a = _cancel_with_grn(world, stated=7)
    assert _offered(world, product, a) == 3


# ---------------------------------------------------------------------------- R2
def _same_item_group(env):
    env.post(CG_INGEST, [_cg(details=[_cg_line(981001, qty=1), _cg_line(981002, qty=1)])])


def _keyless(env):
    env.post(CR_INGEST, [_cr(990001, "ZZCR-0001", [_cr_line(991001, 1)])])


def _keyed(env):
    env.post(CR_INGEST, [_cr(990002, "ZZCR-0002", [_cr_line(992001, 1, from_dtl=981002)],
                             modified="2026-10-03T10:00:00.000")])


def _returned(env):
    return (_line(env, "db1:CSGN:980001:981001").qty_returned,
            _line(env, "db1:CSGN:980001:981002").qty_returned)


def test_keyed_then_keyless_return_ends_line_1_and_2_each_returned_one(env):
    _same_item_group(env)
    _keyed(env)
    _keyless(env)
    assert _returned(env) == (Decimal("1"), Decimal("1"))


def test_keyless_then_keyed_return_ends_the_same_as_the_other_order(env):
    _same_item_group(env)
    _keyless(env)
    _keyed(env)
    assert _returned(env) == (Decimal("1"), Decimal("1"))


# ---------------------------------------------------------------------------- R3
def test_a_released_partly_cancelled_member_keeps_only_its_cancel(world):
    product, wh = world.product(), world.warehouse()
    a = _alloc(world, product, wh, qty=10, line_no=1, cancelled=4, stated=0)
    grn = world.grn(status="approved", spo_number=SPO, company_id=DEFAULT_COMPANY_ID)
    pick = world.line(header_id=grn, product_id=product, warehouse_id=wh, quantity=6,
                      allocation_id=a, company_id=DEFAULT_COMPANY_ID)
    world.db.commit()
    svc = PickingHeaderService(world.db)
    svc._sync_received_for_allocations([world.db.get(SPOAllocation, a)])
    world.db.flush()
    world.db.delete(world.db.get(PickingLine, pick))
    world.db.flush()
    svc._sync_received_for_allocations([world.db.get(SPOAllocation, a)], released={a})
    world.db.flush()
    world.db.expire_all()
    row = world.db.get(SPOAllocation, a)
    assert row.quantity_received == 4
    assert row.allocated_quantity - row.quantity_received == 6


# ---------------------------------------------------------------------------- R4
@pytest.fixture
def pair():
    with blank_session() as db:
        set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
        from tests._mc_lookup_seed import customer, product
        cust = customer(db, company_id=DEFAULT_COMPANY_ID, name=unique_code("ZZT Cust"))
        prod = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("ZZTR4"))
        for doc_type, qty in (("sales_order", 7), ("consignment", 5)):
            so = SalesOrder(id=str(uuid.uuid4()), so_number=unique_code("SO")[:40],
                            customer_id=cust.id, company_id=DEFAULT_COMPANY_ID,
                            order_date=date(2026, 10, 5), status="open", doc_type=doc_type, demand_class="retail")
            db.add(so)
            db.flush()
            db.add(SalesOrderLine(sales_order_id=so.id, product_id=prod.id, qty_ordered=qty,
                                  qty_delivered=qty, line_total=Decimal(qty * 10),
                                  company_id=DEFAULT_COMPANY_ID))
        db.flush()
        yield db, cust, prod


def test_sales_report_counts_the_sales_order_line_only(pair):
    from app.services.sales_report_service import sales_report
    db, cust, prod = pair
    out = sales_report(db, customer_ids=[cust.id], product_code=prod.product_code,
                       date_from=date(2026, 10, 1), date_to=date(2026, 10, 31))
    assert sum(m["ordered_qty"] for m in out["months"]) == 7
    assert sum(Decimal(str(m["ordered_value"])) for m in out["months"]) == Decimal("70")


def test_reports_sales_order_lines_dataset_counts_the_sales_order_line_only(pair):
    from app.schemas.report import ReportViewConfig
    from app.services.reports import engine
    from app.services.reports import registry as reg
    db, cust, prod = pair
    definition = reg.get("sales_yearly")
    view = ReportViewConfig.model_validate({
        "params": {"date_basis": "order_date", "period": {"kind": "year", "year": 2026},
                   "company": [DEFAULT_COMPANY_ID], "channel": ["dealer", "project"],
                   "basis": ["ordered"]},
        "detail": {"columns": [], "order": []},
        "pivot": {"rows": "channel", "cols": "year", "measures": ["sales_value"]},
    })
    result = engine.run(db, definition, view.params, view,
                        company_grants=frozenset({DEFAULT_COMPANY_ID}))
    assert Decimal(result.layouts.summary.grand_total["sales_value"]) == Decimal("70.00")


# ---------------------------------------------------------------------------- R5
def test_a_keyless_cancel_so_naming_a_consignment_number_never_matches_it(env):
    env.post(CG_INGEST, [_cg()])
    detail = _detail(951001, from_no="ZZCG-0001", from_dtl=None)
    env.post(XS_INGEST, [_xs(details=[detail])])
    env.db.expire_all()
    stored = env.db.execute(select(AutocountCancelLine).where(
        AutocountCancelLine.dtl_key == 951001)).scalars().one()
    assert stored.match_status != "matched", stored.match_status
    assert stored.sales_order_line_id is None
    assert _line(env, "db1:CSGN:980001:981001").qty_cancelled == Decimal("0")


# ---------------------------------------------------------------------------- F1
def test_pool_subtracts_a_cancel_on_a_non_autocount_row(world):
    product, wh = world.product(), world.warehouse()
    a = world.allocation(product_id=product, warehouse_id=wh, quantity=100, received=30,
                         stated=None, source_system=None, company_id=DEFAULT_COMPANY_ID,
                         cancelled=20)
    world.db.commit()
    assert _offered(world, product, a) == 50


# ---------------------------------------------------------------------------- F2
def _return_in_book(env, book):
    rec = _cr(990001, "ZZCR-0001", [_cr_line(991001, 4, from_no=None, from_dtl=981001)])
    return env.post(CR_INGEST, [rec], book=book)


def _stored_return_line(env):
    from app.models.autocount_cancel import AutocountCancelDoc
    env.db.expire_all()
    return env.db.execute(select(AutocountCancelLine).join(
        AutocountCancelDoc, AutocountCancelLine.cancel_doc_id == AutocountCancelDoc.id).where(
        AutocountCancelDoc.kind == "consignment_return")).scalars().one()


def test_a_keyed_return_in_book_db_1_never_matches_a_line_in_book_dbA1(env):
    assert env.post(CG_INGEST, [_cg()], book="dbA1").status_code == 200
    assert _return_in_book(env, "db_1").status_code == 200
    stored = _stored_return_line(env)
    assert stored.match_status != "matched", stored.match_status
    assert stored.sales_order_line_id is None
    assert _line(env, "dbA1:CSGN:980001:981001").qty_returned == Decimal("0")


def test_a_keyed_return_in_the_same_book_dbA1_matches(env):  # guard
    assert env.post(CG_INGEST, [_cg()], book="dbA1").status_code == 200
    assert _return_in_book(env, "dbA1").status_code == 200
    assert _stored_return_line(env).match_status == "matched"
    assert _line(env, "dbA1:CSGN:980001:981001").qty_returned == Decimal("4")
