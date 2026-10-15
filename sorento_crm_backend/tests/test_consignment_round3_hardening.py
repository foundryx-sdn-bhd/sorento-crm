"""AC-CONSIGN-CRM fix round 1 (security review): hardening tests.

S1 the SO deletions door must not reach a consignment order; S2 the incoming cancel term is
capped per allocation; N4 DtlKey is normalised to an integer; N5 a keyed return never crosses
books; N7 an SO push cannot reuse a consignment's source_ref. Every record is fake.
"""
from __future__ import annotations

import uuid

from app.models.order import SalesOrder, SalesOrderLine
from app.models.procurement import InboundShipmentLine
from app.services.company_scope import DEFAULT_COMPANY_ID
from app.services.incoming_stock_service import IncomingStockService
from app.services.procurement_service import InboundShipmentService

from sqlalchemy import select

from tests.test_ingest_autocount_consignments import (  # noqa: F401 - env is a fixture
    CG_INGEST,
    CR_INGEST,
    SO_INGEST,
    _cg,
    _cg_line,
    _cr,
    _cr_line,
    _line,
    _order,
    _records,
    _return_lines,
    env,
)
from tests.test_incoming_stock_cancelled_sibling import (  # noqa: F401 - fixtures
    _alloc,
    _remaining_incoming,
    _scope_listeners,
    world,
)

SO_DELETE = "/api/v1/external/ingest/sales_orders/deletions"


def _delete_so(env, refs):
    return env.client.post(SO_DELETE, json={"companyCode": env.company_code, "source_refs": refs})


# ---------------------------------------------------------------------------- S1
def test_so_deletions_door_leaves_a_consignment_order_untouched(env):
    env.post(CG_INGEST, [_cg(details=[_cg_line(981001, transferred=0)])])
    before = _order(env)
    before_id, before_status = before.id, before.status
    line_before = _line(env, "db1:CSGN:980001:981001")
    line_id, line_status = line_before.id, line_before.line_status

    res = _delete_so(env, ["db1:CSGN:980001"])
    assert res.status_code == 200, res.text
    outcome = res.json()["records"][0]["outcome"]
    assert outcome == "not_found", res.json()

    after = _order(env)
    assert after is not None and (after.id, after.status) == (before_id, before_status)
    line_after = _line(env, "db1:CSGN:980001:981001")
    assert (line_after.id, line_after.line_status) == (line_id, line_status)


def test_so_deletions_door_still_removes_a_real_sales_order(env):  # guard
    so = {"source_ref": "db1:7001", "so_number": "ZZSO-DEL1", "doc_date": "2026-10-01",
          "lines": [{"source_ref": "db1:7001:1", "product_code": "ZZAC-P1", "qty_ordered": 3}]}
    assert _records(env.post(SO_INGEST, [so], book=None))["db1:7001"]["outcome"] == "created"
    res = _delete_so(env, ["db1:7001"])
    assert res.status_code == 200, res.text
    assert res.json()["records"][0]["outcome"] in ("deleted", "deactivated"), res.json()


# ---------------------------------------------------------------------------- S2
def test_cancel_term_is_capped_by_the_allocations_own_unpicked_capacity(world):
    from app.models.procurement import SPOAllocation  # noqa: F401
    from tests.test_grn_spo_forward_matching import SPO

    product, wh = world.product(), world.warehouse()
    db = world.db
    db.add(InboundShipmentLine(
        id=str(uuid.uuid4()), shipment_id=world.shipment, product_id=product,
        quantity_shipped=20, quantity_received=0, line_status="in_transit",
        company_id=DEFAULT_COMPANY_ID,
    ))
    db.flush()
    # A: fully picked AND (inconsistently) fully cancelled. B: still at sea.
    a = _alloc(world, product, wh, qty=10, line_no=1, cancelled=10)
    _alloc(world, product, wh, qty=10, line_no=2)
    grn = world.grn(status="approved", spo_number=SPO, company_id=DEFAULT_COMPANY_ID)
    world.line(header_id=grn, product_id=product, warehouse_id=wh, quantity=10,
               allocation_id=a, company_id=DEFAULT_COMPANY_ID)
    db.commit()
    assert _remaining_incoming(world, product) == 10


# ---------------------------------------------------------------------------- N4
def _two_pushes(env, first_key):
    first = _cg(details=[_cg_line(981012, transferred=0)])
    first["Details"][0]["DtlKey"] = first_key
    r1 = _records(env.post(CG_INGEST, [first]))["db1:CSGN:980001"]
    return r1


def _check_same_line_after_int_push(env):
    order = _order(env)
    env.db.expire_all()
    lines = env.db.execute(select(SalesOrderLine).where(
        SalesOrderLine.sales_order_id == order.id)).scalars().all()
    assert len(lines) == 1, [l.source_ref for l in lines]
    first_id = lines[0].id
    later = _cg(details=[_cg_line(12, transferred=0)], modified="2026-10-03T10:00:00.000")
    later["Details"][0]["DtlKey"] = 12
    env.post(CG_INGEST, [later])
    env.db.expire_all()
    after = env.db.execute(select(SalesOrderLine).where(
        SalesOrderLine.sales_order_id == order.id)).scalars().all()
    assert [l.id for l in after] == [first_id]
    assert after[0].source_ref.endswith(":12"), after[0].source_ref


def test_a_zero_padded_string_dtlkey_hits_the_same_line_as_the_integer(env):
    res = env.post(CG_INGEST, [_cg(details=[dict(_cg_line(12, transferred=0), DtlKey="012")])])
    assert res.status_code == 200, res.text
    assert _records(res)["db1:CSGN:980001"]["outcome"] == "created", res.text
    _check_same_line_after_int_push(env)


def test_a_float_dtlkey_hits_the_same_line_as_the_integer(env):
    res = env.post(CG_INGEST, [_cg(details=[dict(_cg_line(12, transferred=0), DtlKey=12.0)])])
    assert res.status_code == 200, res.text
    assert _records(res)["db1:CSGN:980001"]["outcome"] == "created", res.text
    _check_same_line_after_int_push(env)


# ---------------------------------------------------------------------------- N5
def _keyed_return(env, book):
    rec = _cr(details=[_cr_line(991001, 4, from_no=None, from_dtl=981001)])
    return env.post(CR_INGEST, [rec], book=book)


def test_a_keyed_return_in_another_book_never_matches_the_line(env):
    env.post(CG_INGEST, [_cg()])
    assert _keyed_return(env, "db2").status_code == 200
    env.db.expire_all()
    from app.models.autocount_cancel import AutocountCancelDoc, AutocountCancelLine
    stored = env.db.execute(select(AutocountCancelLine).join(
        AutocountCancelDoc, AutocountCancelLine.cancel_doc_id == AutocountCancelDoc.id).where(
        AutocountCancelDoc.kind == "consignment_return")).scalars().all()
    assert len(stored) == 1
    assert stored[0].match_status != "matched", stored[0].match_status
    assert stored[0].sales_order_line_id is None
    assert float(_line(env, "db1:CSGN:980001:981001").qty_returned) == 0.0


def test_a_keyed_return_in_the_same_book_still_matches(env):  # guard
    env.post(CG_INGEST, [_cg()])
    assert _keyed_return(env, "db1").status_code == 200
    assert _return_lines(env, 990001)[991001].match_status == "matched"
    assert float(_line(env, "db1:CSGN:980001:981001").qty_returned) == 4.0


# ---------------------------------------------------------------------------- N7
def test_a_sales_order_push_reusing_a_consignments_source_ref_fails_and_changes_nothing(env):
    env.post(CG_INGEST, [_cg()])
    so = {"source_ref": "db1:CSGN:980001", "so_number": "ZZSO-REUSE", "doc_date": "2026-10-01",
          "lines": [{"source_ref": "db1:CSGN:980001:981001", "product_code": "ZZAC-P1",
                     "qty_ordered": 3}]}
    rec = _records(env.post(SO_INGEST, [so], book=None))["db1:CSGN:980001"]
    assert rec["outcome"] == "failed", rec
    order = _order(env)
    assert order.doc_type == "consignment"
    assert order.so_number == "ZZCG-0001"
    assert _line(env, "db1:CSGN:980001:981001").qty_ordered == 10
