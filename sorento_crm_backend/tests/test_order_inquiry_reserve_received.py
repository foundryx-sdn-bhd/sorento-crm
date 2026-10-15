"""OI-RESERVE-RECEIVED: a CS reserve request may reach into the received part of a row.

UAC: `documentation/plans/scm/oi-reserve-received-acceptance-criteria.md`
(AC-RR-1..5, 10..22, 30..35). Card: `oi-reserve-received-card.md`.
TEST-FIRST (Phase 2): written before any implementation. A red here is a missing
behaviour, a refusal the contract removes, or the missing `OrderInquiryReleasedLink`
model (imported INSIDE each test that needs it so one missing class fails that test, not
collection). Never a fixture bug.

Postgres only (`blank_session`). Every chain is seeded here (product, SO line, mirror row,
PO/SPO targets, warehouse, user); nothing is borrowed off an existing row. The harness
re-uses `ctx` and the seed helpers of `test_oi_follow_book_chain.py` by import.

Received PO line = closed or qty_received >= qty_ordered. Received SPO = receipt_status
`fully_received` (not open incoming).

Not expressed here: AC-RR-43/44 (screen; FE file + browser), AC-RR-50 (migration, the
coder's own revision plus `test_schema_uuid_id_principle.py`).
"""
from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from app.models.procurement import PurchaseOrderLine
from app.models.project_so import (
    INQUIRY_ACTIONED,
    INQUIRY_CANCELLED,
    INQUIRY_PARTLY_LINKED,
    INQUIRY_PLACED,
    INQUIRY_RAISED,
    IV_DELAY,
    OrderInquiryLink,
    OrderInquiryReserveRequestRow,
    OrderInquiryRow,
)
from app.services.error_handler import AppException
from app.services.order_inquiry_reserve_service import OrderInquiryReserveService
from app.services.project_order_inquiry_service import ProjectOrderInquiryService

from tests.test_oi_follow_book_chain import (
    ctx,  # noqa: F401 - pytest fixture, imported for reuse
    _existing_link,
    _links_of,
    _ref,
    _seed_mirror,
    _seed_po_line,
    _seed_product,
    _seed_row,
    _seed_row_and_mirror,
    _seed_so_line,
    _seed_spo_line,
)
from tests.test_order_inquiry_reserve import (
    _captured_dispatches,
    _register,
    _reserved_calls,
)
from tests.test_order_inquiry_worklist import (
    LIST,
    MARKER as WL_MARKER,
    _line_on_authored_order,
    _purchase_order,
    _row,
    _user,
    api as worklist_api,  # noqa: F401  (re-exported fixture)
)
from tests.test_planning_changes import _warehouse

__all__ = ["ctx", "worklist_api"]


# --------------------------------------------------------------------------- harness
def _scene(ctx):
    db = ctx.db
    product = _seed_product(db, company_id=ctx.company_a)
    wh = _warehouse(db, f"B{uuid.uuid4().hex[:5]}".upper())
    uid = _user(db, f"zzt-rr {uuid.uuid4().hex[:6]}")
    db.flush()
    return SimpleNamespace(
        ctx=ctx, db=db, company=ctx.company_a, product=product, wh=wh, uid=uid,
        svc=OrderInquiryReserveService(db),
    )


def _row_with(sc, *, qty, state=INQUIRY_PLACED, ref=None, **row_kwargs):
    """A row on its own SO line + mirror. `ref` is the SO line's source_ref."""
    ref = ref or _ref("SOL")
    _so, core = _seed_so_line(
        sc.db, company_id=sc.company, product_id=sc.product.id, source_ref=ref, qty=str(qty)
    )
    _pso, mirror, inquiry, row = _seed_row_and_mirror(
        sc.db, company_id=sc.company, core_line=core, product_id=sc.product.id, qty=qty,
        state=state, **row_kwargs,
    )
    return SimpleNamespace(row=row, inquiry=inquiry, ref=ref, core=core, mirror=mirror)


def _received_spo(sc, *, qty, ref=None, **kw):
    spo = _seed_spo_line(
        sc.db, company_id=sc.company, product_id=sc.product.id, from_so_line_ref=ref,
        allocated_quantity=qty, quantity_received=qty, **kw,
    )
    spo.receipt_status = "fully_received"
    sc.db.flush()
    return spo


def _link_spo(sc, row, spo, qty):
    return _existing_link(
        sc.db, company_id=sc.company, row_id=row.id, document=spo.spo_number, qty=qty,
        spo_allocation_id=spo.id,
    )


def _link_po(sc, row, po, line, qty):
    return _existing_link(
        sc.db, company_id=sc.company, row_id=row.id, document=po.po_number, qty=qty,
        po_line_id=line.id,
    )


def _request(sc, inq, row, qty):
    req = sc.svc.create_request(
        inquiry_id=inq.id,
        rows=[{"row_id": row.id, "qty_requested": Decimal(str(qty)), "warehouse_id": sc.wh.id}],
        note=None,
        actor_user_id=sc.uid,
    )
    sc.db.commit()
    return req


def _commit(sc, inq, row, qty, reason=None):
    entry = {"row_id": row.id, "warehouse_id": sc.wh.id, "qty_reserved": Decimal(str(qty))}
    if reason is not None:
        entry["reason"] = reason
    sc.svc.commit_request(
        inquiry_id=inq.id, reserves=[entry], amendments=[], actor_user_id=sc.uid
    )
    sc.db.commit()


def _amend(sc, inq, row, qty, reason="amended"):
    sc.svc.commit_request(
        inquiry_id=inq.id,
        reserves=[],
        amendments=[{"row_id": row.id, "qty_reserved": Decimal(str(qty)), "reason": reason}],
        actor_user_id=sc.uid,
    )
    sc.db.commit()


def _fresh(sc, row):
    sc.db.expire_all()
    return sc.db.query(OrderInquiryRow).filter(OrderInquiryRow.id == row.id).one()


def _reserve_links(sc, row):
    return [lk for lk in _links_of(sc.db, row.id) if lk.reserve_request_row_id is not None]


def _target_links(sc, row):
    return [lk for lk in _links_of(sc.db, row.id) if lk.reserve_request_row_id is None]


def _plain_remaining(sc, row):
    sc.db.expire_all()
    fresh = sc.db.query(OrderInquiryRow).filter(OrderInquiryRow.id == row.id).one()
    linked = sum((Decimal(str(lk.qty)) for lk in _links_of(sc.db, row.id)), Decimal("0"))
    return Decimal(str(fresh.qty)) - linked - Decimal(str(fresh.bundled_qty or 0))


def _released(sc, row):
    from app.models.project_so import OrderInquiryReleasedLink

    sc.db.expire_all()
    return (
        sc.db.query(OrderInquiryReleasedLink)
        .filter(OrderInquiryReleasedLink.row_id == row.id)
        .all()
    )


def _spo_1092_released(sc, *, cs=435, ref=None, reason="BRW only has 435"):
    """The card's partial example: 1092 on a received SPO, CS reserves `cs`."""
    sc_row = _row_with(sc, qty=1092, ref=ref)
    spo = _received_spo(sc, qty=1092, ref=sc_row.ref)
    _link_spo(sc, sc_row.row, spo, 1092)
    sc.db.commit()
    _request(sc, sc_row.inquiry, sc_row.row, 1092)
    _commit(sc, sc_row.inquiry, sc_row.row, cs, reason if cs < 1092 else None)
    return sc_row, spo


# --------------------------------------------------------------------------- request
@pytest.mark.parametrize("state", [INQUIRY_RAISED, INQUIRY_PARTLY_LINKED, INQUIRY_PLACED])
def test_rr1_received_row_can_be_requested(ctx, state):
    """AC-RR-1: placed (or raised / partly_linked), 1260 on a received SPO, requestable 1260."""
    sc = _scene(ctx)
    r = _row_with(sc, qty=1260, state=state)
    spo = _received_spo(sc, qty=1260, ref=r.ref)
    _link_spo(sc, r.row, spo, 1260)
    sc.db.commit()
    req = _request(sc, r.inquiry, r.row, 1260)
    rr = (
        sc.db.query(OrderInquiryReserveRequestRow)
        .filter(OrderInquiryReserveRequestRow.request_id == req.id)
        .one()
    )
    assert rr.qty_requested == Decimal("1260")


def test_rr2_only_open_links_refused_nothing_remaining(ctx):
    """AC-RR-2: only open links (incoming SPO, partly received PO line) -> 409."""
    sc = _scene(ctx)
    r = _row_with(sc, qty=100, state=INQUIRY_PLACED)
    spo = _seed_spo_line(
        sc.db, company_id=sc.company, product_id=sc.product.id, allocated_quantity=60
    )
    po, line = _seed_po_line(
        sc.db, company_id=sc.company, product_id=sc.product.id, qty_ordered="40",
        qty_received="10",
    )
    _link_spo(sc, r.row, spo, 60)
    _link_po(sc, r.row, po, line, 40)
    sc.db.commit()
    with pytest.raises(AppException) as exc:
        sc.svc.create_request(
            inquiry_id=r.inquiry.id,
            rows=[{"row_id": r.row.id, "qty_requested": Decimal("1"), "warehouse_id": sc.wh.id}],
            note=None, actor_user_id=sc.uid,
        )
    assert exc.value.status_code == 409
    assert exc.value.code == "reserve_request_nothing_remaining", exc.value.code


@pytest.mark.parametrize("bad", ["0", "159"])
def test_rr3_qty_out_of_range_names_requestable(ctx, bad):
    """AC-RR-3: 158 received + 24 open on 182 -> requestable 158; 0 and 159 are 422."""
    sc = _scene(ctx)
    r = _row_with(sc, qty=182, state=INQUIRY_PLACED)
    po_recv, recv_line = _seed_po_line(
        sc.db, company_id=sc.company, product_id=sc.product.id, qty_ordered="158",
        qty_received="158",
    )
    po_open, open_line = _seed_po_line(
        sc.db, company_id=sc.company, product_id=sc.product.id, qty_ordered="24",
        qty_received="0",
    )
    _link_po(sc, r.row, po_recv, recv_line, 158)
    _link_po(sc, r.row, po_open, open_line, 24)
    sc.db.commit()
    with pytest.raises(AppException) as exc:
        sc.svc.create_request(
            inquiry_id=r.inquiry.id,
            rows=[{"row_id": r.row.id, "qty_requested": Decimal(bad), "warehouse_id": sc.wh.id}],
            note=None, actor_user_id=sc.uid,
        )
    assert exc.value.status_code == 422
    assert exc.value.code == "reserve_request_qty_out_of_range", exc.value.code
    assert "158" in exc.value.message, exc.value.message


def test_rr4_unchanged_refusals(ctx):
    """AC-RR-4: actioned, cancelled, other verbs and an already open request stay 409."""
    sc = _scene(ctx)
    for kwargs in (
        {"state": INQUIRY_ACTIONED},
        {"state": INQUIRY_CANCELLED},
        {"state": INQUIRY_RAISED, "verb": IV_DELAY},
    ):
        r = _row_with(sc, qty=10, **kwargs)
        sc.db.commit()
        with pytest.raises(AppException) as exc:
            sc.svc.create_request(
                inquiry_id=r.inquiry.id,
                rows=[{"row_id": r.row.id, "qty_requested": Decimal("1"),
                       "warehouse_id": sc.wh.id}],
                note=None, actor_user_id=sc.uid,
            )
        assert exc.value.status_code == 409, kwargs
    r = _row_with(sc, qty=10, state=INQUIRY_RAISED)
    sc.db.commit()
    _request(sc, r.inquiry, r.row, 10)
    with pytest.raises(AppException) as exc:
        sc.svc.create_request(
            inquiry_id=r.inquiry.id,
            rows=[{"row_id": r.row.id, "qty_requested": Decimal("1"), "warehouse_id": sc.wh.id}],
            note=None, actor_user_id=sc.uid,
        )
    assert exc.value.status_code == 409


def test_rr5_detail_payload_carries_reserve_requestable_qty(worklist_api):
    """AC-RR-5: the OI Lines payload (worklist route, response_model) carries the number."""
    from datetime import date

    from app.models.project_so import OrderInquiry

    client, db, company_id, seeded = worklist_api
    inquiry = db.get(OrderInquiry, seeded["authored_row"].order_inquiry_id)
    line_a = _line_on_authored_order(db, company_id, seeded, qty="40", day=11)
    line_b = _line_on_authored_order(db, company_id, seeded, qty="40", day=12)
    received = _purchase_order(db, company_id)["line"]
    received.qty_received = Decimal("40")
    received.line_status = "closed"
    open_po = _purchase_order(db, company_id)["line"]  # 40 ordered, 15 received: open
    row_recv = _row(
        db, company_id, inquiry, so_line_id=line_a.id, item_code=f"{WL_MARKER}-RRCV", qty="40",
        state=INQUIRY_PLACED, delivery_date=date(2026, 4, 11), po_line_id=received.id,
    )
    row_open = _row(
        db, company_id, inquiry, so_line_id=line_b.id, item_code=f"{WL_MARKER}-ROPN", qty="40",
        state=INQUIRY_PLACED, delivery_date=date(2026, 4, 12), po_line_id=open_po.id,
    )
    db.commit()
    body = client.get(LIST, params={"delivery_month": "2026-04"}).json()
    by_id = {e["id"]: e for e in body["data"]}
    got_recv = by_id[row_recv.id].get("reserve_requestable_qty")
    got_open = by_id[row_open.id].get("reserve_requestable_qty")
    assert isinstance(got_recv, (int, float)) and not isinstance(got_recv, bool), got_recv
    assert got_recv == 40, got_recv
    assert isinstance(got_open, (int, float)) and got_open == 0, got_open


# --------------------------------------------------------------------------- commit
def test_rr10_cap_is_min_of_requested_and_requestable_at_commit(ctx):
    """AC-RR-10: requestable shrinks between request and commit; a higher qty is 422."""
    sc = _scene(ctx)
    r = _row_with(sc, qty=182, state=INQUIRY_PLACED)
    po_recv, recv_line = _seed_po_line(
        sc.db, company_id=sc.company, product_id=sc.product.id, qty_ordered="158",
        qty_received="158",
    )
    po_open, open_line = _seed_po_line(
        sc.db, company_id=sc.company, product_id=sc.product.id, qty_ordered="34",
        qty_received="0",
    )
    recv_link = _link_po(sc, r.row, po_recv, recv_line, 158)
    _link_po(sc, r.row, po_open, open_line, 24)
    sc.db.commit()
    _request(sc, r.inquiry, r.row, 158)
    # An open link of 10 lands, taking it from the received one: requestable is now 148.
    recv_link.qty = Decimal("148")
    _link_po(sc, r.row, po_open, open_line, 10)
    sc.db.commit()
    with pytest.raises(AppException) as exc:
        _commit(sc, r.inquiry, r.row, 150, "too many")
    assert exc.value.status_code == 422
    assert exc.value.code == "reserve_qty_out_of_range", exc.value.code
    sc.db.rollback()
    _commit(sc, r.inquiry, r.row, 148)
    assert sum(Decimal(str(lk.qty)) for lk in _reserve_links(sc, r.row)) == Decimal("148")


def test_rr11_to_14_release_every_received_link_keep_open(ctx):
    """AC-RR-11/12/13/14: release mode deletes EVERY received link, open stays; one
    released row, one note per released link; reserve link written as today."""
    sc = _scene(ctx)
    r = _row_with(sc, qty=300, state=INQUIRY_PLACED)
    po_recv, recv_line = _seed_po_line(
        sc.db, company_id=sc.company, product_id=sc.product.id, qty_ordered="100",
        qty_received="100",
    )
    spo_recv = _received_spo(sc, qty=100)
    po_open, open_line = _seed_po_line(
        sc.db, company_id=sc.company, product_id=sc.product.id, qty_ordered="100",
        qty_received="0",
    )
    _link_po(sc, r.row, po_recv, recv_line, 100)
    _link_spo(sc, r.row, spo_recv, 100)
    _link_po(sc, r.row, po_open, open_line, 100)
    sc.db.commit()
    req = _request(sc, r.inquiry, r.row, 200)
    _commit(sc, r.inquiry, r.row, 200)

    targets = _target_links(sc, r.row)
    assert [lk.po_line_id for lk in targets] == [open_line.id], targets
    reserve = _reserve_links(sc, r.row)
    assert len(reserve) == 1 and Decimal(str(reserve[0].qty)) == Decimal("200")
    assert reserve[0].document == f"Reserved @ {sc.wh.warehouse_code}"
    assert reserve[0].auto is False

    released = _released(sc, r.row)
    assert len(released) == 2, released
    rr = (
        sc.db.query(OrderInquiryReserveRequestRow)
        .filter(OrderInquiryReserveRequestRow.request_id == req.id)
        .one()
    )
    by_target = {(x.po_line_id, x.spo_allocation_id): x for x in released}
    po_rel = by_target[(recv_line.id, None)]
    spo_rel = by_target[(None, spo_recv.id)]
    assert po_rel.document == po_recv.po_number and spo_rel.document == spo_recv.spo_number
    for rel in released:
        assert str(rel.company_id) == str(sc.company)
        assert rel.reserve_request_row_id == rr.id
        assert Decimal(str(rel.qty)) == Decimal("100")
        assert rel.released_by == sc.uid
        assert rel.released_at is not None

    note = _fresh(sc, r.row).note or ""
    loc = sc.wh.warehouse_code
    for doc in (po_recv.po_number, spo_recv.spo_number):
        stamp = f"Released {doc} (received, stock used by other orders) for CS reserve 200 @ {loc}"
        assert stamp in note, note
    assert _fresh(sc, r.row).state == INQUIRY_PLACED


def test_rr15_full_accept(ctx):
    """AC-RR-15: 1260 received, CS 1260 @ loc -> no SPO link, reserve 1260, placed."""
    sc = _scene(ctx)
    r = _row_with(sc, qty=1260, state=INQUIRY_PLACED)
    spo = _received_spo(sc, qty=1260, ref=r.ref)
    _link_spo(sc, r.row, spo, 1260)
    sc.db.commit()
    _request(sc, r.inquiry, r.row, 1260)
    _commit(sc, r.inquiry, r.row, 1260)
    assert _target_links(sc, r.row) == []
    assert [Decimal(str(lk.qty)) for lk in _reserve_links(sc, r.row)] == [Decimal("1260")]
    assert _fresh(sc, r.row).state == INQUIRY_PLACED
    assert _plain_remaining(sc, r.row) == 0
    released = _released(sc, r.row)
    assert len(released) == 1 and Decimal(str(released[0].qty)) == Decimal("1260")
    assert released[0].spo_allocation_id == spo.id


def test_rr16_partial_accept(ctx):
    """AC-RR-16: 1092 received, CS 435 with a reason -> reserve 435, partly_linked, 657 left."""
    sc = _scene(ctx)
    r, spo = _spo_1092_released(sc)
    assert _target_links(sc, r.row) == []
    assert [Decimal(str(lk.qty)) for lk in _reserve_links(sc, r.row)] == [Decimal("435")]
    assert _fresh(sc, r.row).state == INQUIRY_PARTLY_LINKED
    assert _plain_remaining(sc, r.row) == Decimal("657")
    released = _released(sc, r.row)
    assert len(released) == 1 and Decimal(str(released[0].qty)) == Decimal("1092")


def test_rr17_decline_keeps_received_link(ctx):
    """AC-RR-17: CS 0 with a reason -> link stays, no released row, state unchanged,
    the reserve event is written as today."""
    sc = _scene(ctx)
    r = _row_with(sc, qty=1260, state=INQUIRY_PLACED)
    spo = _received_spo(sc, qty=1260, ref=r.ref)
    _link_spo(sc, r.row, spo, 1260)
    sc.db.commit()
    req = _request(sc, r.inquiry, r.row, 1260)
    _commit(sc, r.inquiry, r.row, 0, "BRW has none")
    assert [lk.spo_allocation_id for lk in _target_links(sc, r.row)] == [spo.id]
    assert _reserve_links(sc, r.row) == []
    assert _fresh(sc, r.row).state == INQUIRY_PLACED
    assert _released(sc, r.row) == []
    rr = (
        sc.db.query(OrderInquiryReserveRequestRow)
        .filter(OrderInquiryReserveRequestRow.request_id == req.id)
        .one()
    )
    events = sc.db.execute(
        sa.text(
            "SELECT count(*) FROM order_inquiry_reserve_events "
            "WHERE reserve_request_row_id = :rr AND kind = 'reserved'"
        ),
        {"rr": rr.id},
    ).scalar()
    assert events == 1


def test_rr18_not_reaching_received_part_keeps_link(ctx):
    """AC-RR-18: 200 row, received 150, unlinked 50, request 50, CS 50 -> link stays."""
    sc = _scene(ctx)
    r = _row_with(sc, qty=200, state=INQUIRY_PARTLY_LINKED)
    spo = _received_spo(sc, qty=150, ref=r.ref)
    _link_spo(sc, r.row, spo, 150)
    sc.db.commit()
    _request(sc, r.inquiry, r.row, 50)
    _commit(sc, r.inquiry, r.row, 50)
    assert [lk.spo_allocation_id for lk in _target_links(sc, r.row)] == [spo.id]
    assert [Decimal(str(lk.qty)) for lk in _reserve_links(sc, r.row)] == [Decimal("50")]
    assert _plain_remaining(sc, r.row) == 0
    assert _released(sc, r.row) == []


def test_rr19_mixed_received_and_open(ctx):
    """AC-RR-19: 158 received + 24 open, request 158, CS 158 -> 158 released, 24 stays."""
    sc = _scene(ctx)
    r = _row_with(sc, qty=182, state=INQUIRY_PLACED)
    po_recv, recv_line = _seed_po_line(
        sc.db, company_id=sc.company, product_id=sc.product.id, qty_ordered="158",
        qty_received="158",
    )
    po_open, open_line = _seed_po_line(
        sc.db, company_id=sc.company, product_id=sc.product.id, qty_ordered="24",
        qty_received="0",
    )
    _link_po(sc, r.row, po_recv, recv_line, 158)
    _link_po(sc, r.row, po_open, open_line, 24)
    sc.db.commit()
    _request(sc, r.inquiry, r.row, 158)
    _commit(sc, r.inquiry, r.row, 158)
    targets = _target_links(sc, r.row)
    assert [(lk.po_line_id, Decimal(str(lk.qty))) for lk in targets] == [
        (open_line.id, Decimal("24"))
    ]
    assert [Decimal(str(lk.qty)) for lk in _reserve_links(sc, r.row)] == [Decimal("158")]
    assert _plain_remaining(sc, r.row) == 0
    released = _released(sc, r.row)
    assert len(released) == 1 and released[0].po_line_id == recv_line.id


def test_rr20_bundle_requestable_and_release(ctx):
    """AC-RR-20: qty 420, bundled 266, 154 received; request 154, CS 26 releases 154."""
    sc = _scene(ctx)
    r = _row_with(sc, qty=420, state=INQUIRY_PLACED)
    r.row.bundled_qty = Decimal("266")
    po, line = _seed_po_line(
        sc.db, company_id=sc.company, product_id=sc.product.id, qty_ordered="154",
        qty_received="154",
    )
    _link_po(sc, r.row, po, line, 154)
    sc.db.commit()
    _request(sc, r.inquiry, r.row, 154)  # requestable is 154, not 0
    _commit(sc, r.inquiry, r.row, 26, "only 26 at BRW")
    assert _target_links(sc, r.row) == []
    assert [Decimal(str(lk.qty)) for lk in _reserve_links(sc, r.row)] == [Decimal("26")]
    released = _released(sc, r.row)
    assert len(released) == 1 and Decimal(str(released[0].qty)) == Decimal("154")


def test_rr21_amend_never_restores_released_link(ctx):
    """AC-RR-21: amending down, even to 0, never recreates the released link."""
    sc = _scene(ctx)
    r, spo = _spo_1092_released(sc)
    _amend(sc, r.inquiry, r.row, 100)
    assert _target_links(sc, r.row) == []
    _amend(sc, r.inquiry, r.row, 0)
    assert _target_links(sc, r.row) == []
    assert _reserve_links(sc, r.row) == []
    released = _released(sc, r.row)
    assert len(released) == 1 and Decimal(str(released[0].qty)) == Decimal("1092")


@pytest.mark.parametrize("cs_reason", [None, "BRW only has 435"])
def test_rr22_reserved_email_reason_names_released_document(ctx, monkeypatch, cs_reason):
    """AC-RR-22: context row reason starts `Released {doc} (received).`; CS reason follows."""
    sc = _scene(ctx)
    _register()
    calls = _captured_dispatches(monkeypatch)
    cs = 435 if cs_reason else 1092
    r = _row_with(sc, qty=1092)
    spo = _received_spo(sc, qty=1092, ref=r.ref)
    _link_spo(sc, r.row, spo, 1092)
    sc.db.commit()
    _request(sc, r.inquiry, r.row, 1092)
    calls.clear()
    _commit(sc, r.inquiry, r.row, cs, cs_reason)
    matches = _reserved_calls(calls)
    assert len(matches) == 1, matches
    reason = matches[0]["context"]["reserve"]["rows"][0]["reason"]
    expected = f"Released {spo.spo_number} (received)."
    if cs_reason:
        expected = f"{expected} {cs_reason}"
    assert reason == expected, reason


# --------------------------------------------------------------------------- never re-linked
def test_rr30_linked_by_target_adds_released_qty_row_sums_unchanged(ctx):
    """AC-RR-30: per PO line and per SPO allocation; the row's own sums ignore the table."""
    sc = _scene(ctx)
    r = _row_with(sc, qty=200, state=INQUIRY_PLACED)
    po, line = _seed_po_line(
        sc.db, company_id=sc.company, product_id=sc.product.id, qty_ordered="100",
        qty_received="100",
    )
    spo = _received_spo(sc, qty=100)
    _link_po(sc, r.row, po, line, 100)
    _link_spo(sc, r.row, spo, 100)
    sc.db.commit()
    _request(sc, r.inquiry, r.row, 200)
    _commit(sc, r.inquiry, r.row, 200)

    by_po, by_spo = ProjectOrderInquiryService(sc.db)._linked_by_target()
    assert by_po.get(str(line.id)) == Decimal("100"), by_po
    assert by_spo.get(str(spo.id)) == Decimal("100"), by_spo
    # The row's own sums never read the released table: reserve 200 covers it, remaining 0.
    assert _plain_remaining(sc, r.row) == 0
    ProjectOrderInquiryService(sc.db).refresh_link_state([_fresh(sc, r.row)])
    assert _fresh(sc, r.row).state == INQUIRY_PLACED


def test_rr31_book_step_does_not_relink_released_target(ctx):
    """AC-RR-31: SPO from_so_line_ref names the row's SO line; after a release the book
    step leaves the 657 shortfall unlinked."""
    sc = _scene(ctx)
    r, spo = _spo_1092_released(sc)
    assert _plain_remaining(sc, r.row) == Decimal("657")
    ProjectOrderInquiryService(sc.db).follow_book_for_rows(
        [str(r.row.id)], trigger="autocount_ingest", company_id=sc.company, actor_user_id=None
    )
    sc.db.commit()
    assert _target_links(sc, r.row) == [], _target_links(sc, r.row)
    assert _plain_remaining(sc, r.row) == Decimal("657")


def test_rr32_other_so_line_naming_same_target_is_not_given_released_qty(ctx):
    """AC-RR-32: a DIFFERENT SO line whose book names the same target (here through the
    PO the SPO chains from) is not given the released qty either."""
    sc = _scene(ctx)
    r, spo = _spo_1092_released(sc)
    ref_b = _ref("SOL")
    po, po_line = _seed_po_line(
        sc.db, company_id=sc.company, product_id=sc.product.id, from_so_line_ref=ref_b,
        qty_ordered="500", qty_received="500",
    )
    spo.from_po_line_ref = po_line.source_ref
    spo.from_po_number = po.po_number
    other = _row_with(sc, qty=500, state=INQUIRY_RAISED, ref=ref_b)
    sc.db.commit()
    ProjectOrderInquiryService(sc.db).follow_book_for_rows(
        [str(other.row.id)], trigger="autocount_ingest", company_id=sc.company,
        actor_user_id=None,
    )
    sc.db.commit()
    assert [lk for lk in _links_of(sc.db, other.row.id) if lk.spo_allocation_id == spo.id] == []


def test_rr33_displacement_never_strips_other_so_line_for_released_target(ctx):
    """AC-RR-33 (D3): another SO line's link on the released target is kept."""
    sc = _scene(ctx)
    r = _row_with(sc, qty=1092, state=INQUIRY_PLACED)
    spo = _received_spo(sc, qty=1500, ref=r.ref)
    spo.allocated_quantity = 1500
    holder = _row_with(sc, qty=408, state=INQUIRY_PLACED)
    _link_spo(sc, r.row, spo, 1092)
    holder_link = _link_spo(sc, holder.row, spo, 408)
    sc.db.commit()
    _request(sc, r.inquiry, r.row, 1092)
    _commit(sc, r.inquiry, r.row, 435, "BRW only has 435")
    ProjectOrderInquiryService(sc.db).follow_book_for_rows(
        [str(r.row.id)], trigger="autocount_ingest", company_id=sc.company, actor_user_id=None
    )
    sc.db.commit()
    kept = [lk.id for lk in _links_of(sc.db, holder.row.id)]
    assert kept == [holder_link.id], "the other SO line must keep its link"
    assert _target_links(sc, r.row) == []


def test_rr34_relink_to_matching_lines_counts_released_as_claimed(ctx):
    """AC-RR-34: a closed received PO line that was released is not a move target."""
    sc = _scene(ctx)
    loc_a = _warehouse(sc.db, f"A{uuid.uuid4().hex[:5]}".upper())
    po, line_a = _seed_po_line(
        sc.db, company_id=sc.company, product_id=sc.product.id, qty_ordered="10",
        qty_received="0",
    )
    line_a.warehouse_id = loc_a.id
    line_b = PurchaseOrderLine(
        company_id=sc.company, purchase_order_id=po.id, product_id=sc.product.id,
        qty_ordered=Decimal("10"), qty_received=Decimal("10"), line_status="closed",
        warehouse_id=sc.wh.id, source_ref=_ref("POL"),
    )
    sc.db.add(line_b)
    sc.db.flush()
    releasing = _row_with(sc, qty=10, state=INQUIRY_PLACED)
    _link_po(sc, releasing.row, po, line_b, 10)
    mover = _row_with(sc, qty=10, state=INQUIRY_PLACED)
    mover.row.stock_location = sc.wh.warehouse_code
    mover_link = _link_po(sc, mover.row, po, line_a, 10)
    sc.db.commit()
    _request(sc, releasing.inquiry, releasing.row, 10)
    _commit(sc, releasing.inquiry, releasing.row, 10)
    assert len(_released(sc, releasing.row)) == 1

    moved = ProjectOrderInquiryService(sc.db).relink_to_matching_lines(
        [str(po.id)], actor_user_id=None, trigger="test"
    )
    sc.db.commit()
    sc.db.refresh(mover_link)
    assert moved == 0
    assert mover_link.po_line_id == line_a.id


def test_rr35_fresh_row_book_names_received_line_still_links(ctx):
    """AC-RR-35: no release anywhere -> the book step links a received line as today."""
    sc = _scene(ctx)
    r = _row_with(sc, qty=5, state=INQUIRY_RAISED)
    spo = _received_spo(sc, qty=5, ref=r.ref)
    sc.db.commit()
    ProjectOrderInquiryService(sc.db).follow_book_for_rows(
        [str(r.row.id)], trigger="autocount_ingest", company_id=sc.company, actor_user_id=None
    )
    sc.db.commit()
    links = _target_links(sc, r.row)
    assert [lk.spo_allocation_id for lk in links] == [spo.id], links
