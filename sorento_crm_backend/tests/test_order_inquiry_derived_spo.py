"""S5 - the derived SPO, the stage cards, and the verb-code rename (R-D, R-E, R-F, AC-D1
to AC-D9).

`PLAN-scm-oi-worklist-excel-parity.md` S5 + the coordinator's 16 Sep addendum (commit
35d4c1674): a derived SPO allocation must be OPEN per
`app.services.scm.spo_supply.open_incoming_clauses` (`line_status` null/open,
`receipt_status` not in `RECEIVED_RECEIPT_STATUSES`, shipment `actual_arrival_date` null)
AND `retired_at IS NULL` AND `allocated_quantity > coalesce(quantity_received, 0)`.

None of this exists yet: `links_for_rows` emits no synthetic `derived`/`derived_po` entry,
`_kinds` sums only REAL links, and the verb-refusal error code is still
`order_inquiry_spo_not_order_back`. Every assertion below is checked against that stale
state and is expected to fail for a business-logic reason (a missing dict key, a wrong
sum, a stale string) rather than a fixture bug.

Postgres only, via `tests/_pg_fixture.py::blank_session`. Fixture builders (`_uid`,
`_sorento`, `_user`, `_product`, `_project`, `_order_and_line`, `_inquiry`, `_row`,
`_link`, `_po_line`, `_client`, `_restore`) are imported from the sibling
`test_order_inquiry_kinds.py`, which already builds this exact shape (a PO-linked row, an
SPO-linked row, a cancelled row) for the same service.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.models.procurement import (
    InboundShipment,
    PurchaseOrder,
    PurchaseOrderLine,
    SPOAllocation,
    Supplier,
)
from app.models.base import company_scope
from app.models.company import Company
from app.services import project_seed_service
from app.services.order_inquiry_worklist_service import OrderInquiryWorklistService
from app.services.project_order_inquiry_service import ProjectOrderInquiryService

from ._pg_fixture import blank_session
from .test_order_inquiry_kinds import (
    MARKER as _KINDS_MARKER,  # noqa: F401 - imported for parity, not used directly
    READ_ONLY,
    _client,
    _inquiry,
    _link,
    _order_and_line,
    _po_line as _kinds_po_line,
    _product,
    _project,
    _restore,
    _row,
    _sorento,
    _uid,
    _user,
)

MARKER = "zzt-oi-derived-spo"
BASE = "/api/v1/project-sales"
LIST = f"{BASE}/order-inquiries"
SUMMARY = f"{LIST}/summary"

SOON = date.today() + timedelta(days=30)


def _supplier(db, company_id: str) -> Supplier:
    row = Supplier(id=_uid(), company_id=company_id, supplier_code=f"ZZT-{_uid()[:8]}",
                    supplier_name=f"{MARKER} supplier")
    db.add(row)
    db.flush()
    return row


def _line_ref(po_number: str, ordinal: int = 1) -> str:
    """The `source_ref` of a purchase order's `ordinal`-th line, as the allocation it
    became quotes it in `from_po_line_ref` (OI-SPO-EXACT: a derived SPO is reached through
    the PO LINE, never through the PO number alone)."""
    return f"{po_number}:{ordinal}"


def _po_line(db, company_id: str, product, supplier):
    """`test_order_inquiry_kinds._po_line`, with the line's own `source_ref` stated, the
    way every AutoCount purchase order line carries one."""
    po, line = _kinds_po_line(db, company_id, product, supplier)
    line.source_ref = _line_ref(po.po_number)
    db.flush()
    return po, line


def _spo(db, company_id: str, *, spo_number: str, product_id: str,
          from_po_number: str | None, allocated_quantity: int, quantity_received: int = 0,
          line_status: str = "open", receipt_status: str = "pending",
          retired_at=None, landed: bool = False,
          from_po_line_ref: str | None = None) -> SPOAllocation:
    """An SPOAllocation with every `open_incoming_clauses` clause under explicit
    control, plus the two the addendum adds (`retired_at`, the quantity test).

    `from_po_line_ref` defaults to the FIRST line of `from_po_number` (`_line_ref`), the
    line `_po_line` seeds: AutoCount names the source line on every allocation that names
    a source purchase order."""
    if from_po_number and from_po_line_ref is None:
        from_po_line_ref = _line_ref(from_po_number)
    shipment_id = None
    if landed:
        shipment = InboundShipment(
            id=_uid(), company_id=company_id, shipment_number=f"ZZT-{_uid()[:8]}",
            shipment_date=SOON, actual_arrival_date=SOON,
        )
        db.add(shipment)
        db.flush()
        shipment_id = shipment.id
    allocation = SPOAllocation(
        id=_uid(), company_id=company_id, spo_number=spo_number,
        allocated_quantity=allocated_quantity, quantity_received=quantity_received,
        product_id=product_id, from_po_number=from_po_number,
        from_po_line_ref=from_po_line_ref, line_status=line_status,
        receipt_status=receipt_status, retired_at=retired_at,
        inbound_shipment_id=shipment_id, expected_date=SOON, issue_date=date.today(),
    )
    db.add(allocation)
    db.flush()
    return allocation


@pytest.fixture()
def world():
    from app.models.base import company_scope

    with blank_session() as db:
        company_id = _sorento(db)
        project_seed_service.run(db, company_id=company_id)
        raiser = _user(db, f"{MARKER} raiser")
        project = _project(db, company_id, raiser, f"{MARKER} project {_uid()[:8]}")
        supplier = _supplier(db, company_id)
        with company_scope(db, frozenset({company_id})):
            yield db, company_id, raiser, project, supplier


def _po_linked_row(db, company_id, project, supplier, *, qty="8", linked_qty="8",
                     po_number_suffix="A"):
    product = _product(db, f"ZZT-DSPO-{_uid()[:6]}-{po_number_suffix}", f"{MARKER} product")
    order, line = _order_and_line(db, company_id, project.id, product, qty, date(2026, 5, 15))
    inquiry = _inquiry(db, company_id, order.id, None)
    row = _row(db, company_id, inquiry.id, line.id, product.product_code, qty,
               delivery_date=date(2026, 5, 15))
    po, po_line = _po_line(db, company_id, product, supplier)
    _link(db, company_id, row.id, linked_qty, po_line_id=po_line.id, document=po.po_number)
    return product, po, row


# ------------------------------------------------------------------------- AC-D1 / AC-D7


def test_a_po_linked_row_gains_a_derived_spo_entry_when_its_po_has_an_open_allocation(world):
    db, company_id, _raiser, project, supplier = world
    product, po, row = _po_linked_row(db, company_id, project, supplier)
    allocation = _spo(
        db, company_id, spo_number=f"ZZT-SPO-{_uid()[:6]}", product_id=product.id,
        from_po_number=po.po_number, allocated_quantity=5,
    )
    db.commit()

    links = ProjectOrderInquiryService(db).links_for_rows([row.id])[row.id]

    kinds = {(link["kind"], link.get("derived", False)) for link in links}
    assert ("po", False) in kinds
    assert ("spo", True) in kinds, links
    derived = next(link for link in links if link.get("derived"))
    assert derived["document"] == allocation.spo_number

    # AC-D7: the derived entry writes nothing. The row's REAL link is still 8, and
    # there is still exactly one row of `order_inquiry_links`.
    from app.models.project_so import OrderInquiryLink

    real_links = db.query(OrderInquiryLink).filter(OrderInquiryLink.row_id == row.id).all()
    assert len(real_links) == 1
    # `qty` is `Numeric(15, 4)`, so the column reads back `Decimal("8.0000")` - compare
    # numerically rather than against the string a person would type.
    from decimal import Decimal

    assert Decimal(real_links[0].qty) == Decimal("8")


def test_oi_spo_exact_an_allocation_of_a_sibling_po_line_is_not_derived(world):
    """OI-SPO-EXACT (owner 13 Oct, case 1 on the 12 Oct prod backup): an open allocation of
    the SAME purchase order and product that quotes ANOTHER line of it was bought for another
    sales order line. It is not this row's incoming stock - no derived entry, no `spo` card
    share, no `spo_number` filter hit - while the allocation naming the row's own line is."""
    db, company_id, raiser, project, supplier = world
    product, po, row = _po_linked_row(
        db, company_id, project, supplier, po_number_suffix="X"
    )
    sibling = PurchaseOrderLine(
        id=_uid(), company_id=company_id, purchase_order_id=po.id, product_id=product.id,
        qty_ordered=Decimal("36"), source_ref=_line_ref(po.po_number, 2),
    )
    db.add(sibling)
    db.flush()
    siblings_allocation = _spo(
        db, company_id, spo_number=f"ZZT-SPO-SIB-{_uid()[:6]}", product_id=product.id,
        from_po_number=po.po_number, from_po_line_ref=sibling.source_ref,
        allocated_quantity=36,
    )
    db.commit()

    links = ProjectOrderInquiryService(db).links_for_rows([row.id])[row.id]
    assert not any(link.get("derived") for link in links), links

    kinds = _kinds_for(db)
    assert kinds["spo"] == "0", kinds
    assert kinds["po"] == "8", kinds

    client, originals = _client(db, raiser, READ_ONLY)
    try:
        response = client.get(LIST, params={"spo_number": siblings_allocation.spo_number})
        assert row.id not in {r["id"] for r in response.json()["data"]}
    finally:
        _restore(originals)

    own_allocation = _spo(
        db, company_id, spo_number=f"ZZT-SPO-OWN-{_uid()[:6]}", product_id=product.id,
        from_po_number=po.po_number, allocated_quantity=5,
    )
    db.commit()

    links = ProjectOrderInquiryService(db).links_for_rows([row.id])[row.id]
    assert {link["document"] for link in links if link.get("derived")} == {
        own_allocation.spo_number
    }, links


# ------------------------------------------------------------------------------ AC-D2


def test_no_derived_entry_when_the_allocation_is_for_a_different_product(world):
    db, company_id, _raiser, project, supplier = world
    _product, po, row = _po_linked_row(db, company_id, project, supplier, po_number_suffix="B")
    other_product = _product_for_other(db)
    _spo(
        db, company_id, spo_number=f"ZZT-SPO-{_uid()[:6]}", product_id=other_product.id,
        from_po_number=po.po_number, allocated_quantity=5,
    )
    db.commit()

    links = ProjectOrderInquiryService(db).links_for_rows([row.id])[row.id]

    assert not any(link.get("derived") for link in links)


def _product_for_other(db):
    return _product(db, f"ZZT-DSPO-OTHER-{_uid()[:6]}", f"{MARKER} other product")


def test_no_derived_entry_when_the_allocation_is_received(world):
    db, company_id, _raiser, project, supplier = world
    product, po, row = _po_linked_row(db, company_id, project, supplier, po_number_suffix="C")
    _spo(
        db, company_id, spo_number=f"ZZT-SPO-{_uid()[:6]}", product_id=product.id,
        from_po_number=po.po_number, allocated_quantity=5, quantity_received=5,
        receipt_status="received",
    )
    db.commit()

    links = ProjectOrderInquiryService(db).links_for_rows([row.id])[row.id]

    assert not any(link.get("derived") for link in links)


def test_no_derived_entry_when_the_allocation_is_retired(world):
    db, company_id, _raiser, project, supplier = world
    product, po, row = _po_linked_row(db, company_id, project, supplier, po_number_suffix="D")
    from datetime import datetime

    _spo(
        db, company_id, spo_number=f"ZZT-SPO-{_uid()[:6]}", product_id=product.id,
        from_po_number=po.po_number, allocated_quantity=5,
        retired_at=datetime(2026, 1, 1),
    )
    db.commit()

    links = ProjectOrderInquiryService(db).links_for_rows([row.id])[row.id]

    assert not any(link.get("derived") for link in links)


def test_no_derived_entry_when_the_shipment_has_landed(world):
    db, company_id, _raiser, project, supplier = world
    product, po, row = _po_linked_row(db, company_id, project, supplier, po_number_suffix="E")
    _spo(
        db, company_id, spo_number=f"ZZT-SPO-{_uid()[:6]}", product_id=product.id,
        from_po_number=po.po_number, allocated_quantity=5, landed=True,
    )
    db.commit()

    links = ProjectOrderInquiryService(db).links_for_rows([row.id])[row.id]

    assert not any(link.get("derived") for link in links)


def test_no_derived_entry_when_the_allocation_is_fully_received_by_quantity(world):
    """The addendum's own quantity test, distinct from `receipt_status`: an allocation
    that never had its status flipped but is fully covered by `quantity_received` is not
    incoming either."""
    db, company_id, _raiser, project, supplier = world
    product, po, row = _po_linked_row(db, company_id, project, supplier, po_number_suffix="F")
    _spo(
        db, company_id, spo_number=f"ZZT-SPO-{_uid()[:6]}", product_id=product.id,
        from_po_number=po.po_number, allocated_quantity=5, quantity_received=5,
    )
    db.commit()

    links = ProjectOrderInquiryService(db).links_for_rows([row.id])[row.id]

    assert not any(link.get("derived") for link in links)


def test_two_open_allocations_on_the_same_po_and_product_both_appear_as_derived(world):
    """The addendum's own ruling: no tie-break. Both are shown."""
    db, company_id, _raiser, project, supplier = world
    product, po, row = _po_linked_row(db, company_id, project, supplier, po_number_suffix="G")
    first = _spo(
        db, company_id, spo_number=f"ZZT-SPO-{_uid()[:6]}-1", product_id=product.id,
        from_po_number=po.po_number, allocated_quantity=5,
    )
    second = _spo(
        db, company_id, spo_number=f"ZZT-SPO-{_uid()[:6]}-2", product_id=product.id,
        from_po_number=po.po_number, allocated_quantity=3,
    )
    db.commit()

    links = ProjectOrderInquiryService(db).links_for_rows([row.id])[row.id]

    derived_documents = {link["document"] for link in links if link.get("derived")}
    assert derived_documents == {first.spo_number, second.spo_number}


# ------------------------------------------------------------------------------ AC-D3


def test_an_spo_linked_row_marks_its_po_column_as_derived(world):
    db, company_id, _raiser, project, supplier = world
    product = _product(db, f"ZZT-DSPO-SPOLINK-{_uid()[:6]}", f"{MARKER} product")
    order, line = _order_and_line(db, company_id, project.id, product, "6", date(2026, 5, 20))
    inquiry = _inquiry(db, company_id, order.id, None)
    row = _row(db, company_id, inquiry.id, line.id, product.product_code, "6",
               delivery_date=date(2026, 5, 20))
    allocation = _spo(
        db, company_id, spo_number=f"ZZT-SPO-{_uid()[:6]}", product_id=product.id,
        from_po_number="ZZT-SOURCE-PO-0099", allocated_quantity=6,
    )
    _link(db, company_id, row.id, "6", spo_allocation_id=allocation.id,
          document=allocation.spo_number)
    db.commit()

    links = ProjectOrderInquiryService(db).links_for_rows([row.id])[row.id]

    spo_link = next(link for link in links if link["kind"] == "spo")
    assert spo_link["source_po_number"] == "ZZT-SOURCE-PO-0099"
    assert spo_link.get("derived_po") is True


# --------------------------------------------------------------- AC-D10 to AC-D14
#
# Review round 3 (commit 85327545a): the FIRST stage formula double-counted. These
# five pin the CORRECTED one (PLAN S5): `cover` = sum over DISTINCT open allocations
# derived from the row's PO links, EXCLUDING any allocation the row already links to
# for real, counted once even when two PO links share one PO + product;
# `derived_cover = least(po_linked, cover)`; `incoming = least(qty, spo_real +
# derived_cover)`; `purchased = least(qty - incoming, greatest(0, po_linked -
# derived_cover))`.


def _kinds_for(db) -> dict:
    """`_kinds()` over EVERYTHING in this session's scope - safe here because every
    test in this section builds its own scratch schema (`blank_session`) with nothing
    else in it."""
    return OrderInquiryWorklistService(db)._kinds({})


def test_ac_d10_a_real_spo_link_on_the_same_allocation_the_po_derives_is_not_double_counted(world):
    """Row of 8, PO-linked 8, plus a REAL SPO link of 5 on the VERY allocation the PO
    would otherwise derive. `cover` excludes an allocation the row already links to for
    real, so `derived_cover` is 0 here, not another 5 stacked on top of the real link:
    incoming 5 (the real link alone), purchased 3, buy 0.

    Today (uncapped, no exclusion): incoming = least(8, 5 real + 5 derived) = 8,
    purchased = greatest(least(0, 8-5), 0) = 0 -> spo 8, po 0.
    """
    db, company_id, _raiser, project, supplier = world
    product, po, row = _po_linked_row(db, company_id, project, supplier, qty="8", linked_qty="8")
    allocation = _spo(
        db, company_id, spo_number=f"ZZT-SPO-{_uid()[:6]}", product_id=product.id,
        from_po_number=po.po_number, allocated_quantity=5,
    )
    _link(db, company_id, row.id, "5", spo_allocation_id=allocation.id,
          document=allocation.spo_number)
    db.commit()

    kinds = _kinds_for(db)

    assert kinds["spo"] == "5", kinds
    assert kinds["po"] == "3", kinds
    assert kinds["buy"] == "0", kinds


def test_ac_d11_derived_cover_is_capped_at_the_po_linked_qty(world):
    """Row of 10, PO-linked 3, one open allocation of 500. The derived cover cannot
    exceed what the PO link itself carries - incoming 3, purchased 0, buy 7 (the 7 is
    real demand nobody has put anywhere, uncovered by an uncapped 500).

    Today (uncapped): incoming = least(10, 0 + 500) = 10 -> spo 10.
    """
    db, company_id, _raiser, project, supplier = world
    product, po, row = _po_linked_row(db, company_id, project, supplier, qty="10", linked_qty="3")
    _spo(
        db, company_id, spo_number=f"ZZT-SPO-{_uid()[:6]}", product_id=product.id,
        from_po_number=po.po_number, allocated_quantity=500,
    )
    db.commit()

    kinds = _kinds_for(db)

    assert kinds["spo"] == "3", kinds
    assert kinds["po"] == "0", kinds
    assert kinds["buy"] == "7", kinds


def test_ac_d12_one_allocation_is_counted_once_even_behind_two_po_lines_of_the_same_po(world):
    """Row of 20 over TWO PO lines of the SAME PO and product (12 + 8 = 20, fully
    linked), one open allocation of 5. The join from each PO link reaches the SAME
    allocation - `cover` has to count it ONCE, not once per link: incoming 5,
    purchased 15.

    Today (one EXISTS/SUM row per PO link reaching the same allocation): cover sums
    to 5 + 5 = 10 -> incoming = least(20, 10) = 10, purchased = least(20-10, 20-10) =
    10 -> spo 10, po 10 (coordinator's measured red state).
    """
    db, company_id, _raiser, project, supplier = world
    product = _product(db, f"ZZT-DSPO-D12-{_uid()[:6]}", f"{MARKER} product")
    order, line = _order_and_line(db, company_id, project.id, product, "20", date(2026, 5, 15))
    inquiry = _inquiry(db, company_id, order.id, None)
    row = _row(db, company_id, inquiry.id, line.id, product.product_code, "20",
               delivery_date=date(2026, 5, 15))
    po, first_line = _po_line(db, company_id, product, supplier)
    second_line = PurchaseOrderLine(
        id=_uid(), company_id=company_id, purchase_order_id=po.id, product_id=product.id,
        qty_ordered=Decimal("8"), source_ref=_line_ref(po.po_number, 2),
    )
    db.add(second_line)
    db.flush()
    _link(db, company_id, row.id, "12", po_line_id=first_line.id, document=po.po_number)
    _link(db, company_id, row.id, "8", po_line_id=second_line.id, document=po.po_number)
    _spo(
        db, company_id, spo_number=f"ZZT-SPO-{_uid()[:6]}", product_id=product.id,
        from_po_number=po.po_number, allocated_quantity=5,
    )
    db.commit()

    kinds = _kinds_for(db)

    assert kinds["spo"] == "5", kinds
    assert kinds["po"] == "15", kinds


def test_ac_d13_a_row_linked_to_two_purchase_orders_lists_both_derived_spos(world):
    """A row linked to PO-A and PO-B, each with its OWN open allocation for the same
    product, must carry BOTH derived documents in `links[]` - today `links_for_rows`
    keeps a dict of `{row_id: (po_number, product_id)}`, one pair per row, so the
    SECOND PO link's pair silently overwrites the first and only one derived entry
    ever appears."""
    db, company_id, _raiser, project, supplier = world
    product = _product(db, f"ZZT-DSPO-D13-{_uid()[:6]}", f"{MARKER} product")
    order, line = _order_and_line(db, company_id, project.id, product, "10", date(2026, 5, 15))
    inquiry = _inquiry(db, company_id, order.id, None)
    row = _row(db, company_id, inquiry.id, line.id, product.product_code, "10",
               delivery_date=date(2026, 5, 15))
    po_a, line_a = _po_line(db, company_id, product, supplier)
    po_b, line_b = _po_line(db, company_id, product, supplier)
    _link(db, company_id, row.id, "5", po_line_id=line_a.id, document=po_a.po_number)
    _link(db, company_id, row.id, "5", po_line_id=line_b.id, document=po_b.po_number)
    allocation_a = _spo(
        db, company_id, spo_number=f"ZZT-SPO-A-{_uid()[:6]}", product_id=product.id,
        from_po_number=po_a.po_number, allocated_quantity=5,
    )
    allocation_b = _spo(
        db, company_id, spo_number=f"ZZT-SPO-B-{_uid()[:6]}", product_id=product.id,
        from_po_number=po_b.po_number, allocated_quantity=5,
    )
    db.commit()

    links = ProjectOrderInquiryService(db).links_for_rows([row.id])[row.id]

    derived_documents = {link["document"] for link in links if link.get("derived")}
    assert derived_documents == {allocation_a.spo_number, allocation_b.spo_number}, links

    client, originals = _client(db, _raiser, READ_ONLY)
    try:
        response = client.get(LIST, params={"spo_number": allocation_b.spo_number})
        ids = {r["id"] for r in response.json()["data"]}
        assert row.id in ids
    finally:
        _restore(originals)


def test_ac_d14_a_derived_spo_never_crosses_a_company_boundary(world):
    """The derived join matches on `from_po_number` + `product_id` alone - neither is
    itself company-scoped - so an allocation seeded under ANOTHER company, naming the
    SAME product id and the SAME PO number string, must be invisible from company A's
    scope: no derived entry, no stage-sum contribution, no `spo_number` match."""
    db, company_id, raiser, project, supplier = world
    product, po, row = _po_linked_row(db, company_id, project, supplier, qty="8", linked_qty="8")

    other_company_id = _uid()
    with company_scope(db, None):
        db.add(Company(id=other_company_id, name=f"{MARKER} other co", code=f"ZZ{_uid()[:6]}"))
        db.flush()
        # Same product id, same PO number string, but a row owned by ANOTHER company -
        # the schema does not enforce that a company's SPOAllocation names only its
        # own products, so this is a legal (if wrong) row to test the predicate against.
        leaked = SPOAllocation(
            id=_uid(), company_id=other_company_id, spo_number="ZZT-LEAKED-SPO",
            allocated_quantity=5, quantity_received=0, product_id=product.id,
            from_po_number=po.po_number, from_po_line_ref=_line_ref(po.po_number),
            line_status="open", receipt_status="pending",
        )
        db.add(leaked)
        db.flush()
    db.commit()

    links = ProjectOrderInquiryService(db).links_for_rows([row.id])[row.id]
    assert not any(link.get("derived") for link in links), links

    kinds = _kinds_for(db)
    assert kinds["spo"] == "0", kinds
    assert kinds["po"] == "8", kinds

    client, originals = _client(db, raiser, READ_ONLY)
    try:
        response = client.get(LIST, params={"spo_number": "ZZT-LEAKED"})
        ids = {r["id"] for r in response.json()["data"]}
        assert row.id not in ids
    finally:
        _restore(originals)


# ------------------------------------------------------------------------------ AC-D5


@pytest.fixture()
def api():
    from app.models.base import company_scope

    with blank_session() as db:
        company_id = _sorento(db)
        project_seed_service.run(db, company_id=company_id)
        raiser = _user(db, f"{MARKER} raiser")
        project = _project(db, company_id, raiser, f"{MARKER} project {_uid()[:8]}")
        supplier = _supplier(db, company_id)

        # row_po_derived: qty 8, linked 8 to a PO whose PO has a derived SPO of 5.
        product_a = _product(db, f"ZZT-DSPO-KIND-A-{_uid()[:6]}", f"{MARKER} product a")
        order_a, line_a = _order_and_line(
            db, company_id, project.id, product_a, "8", date(2026, 6, 1)
        )
        inquiry_a = _inquiry(db, company_id, order_a.id, raiser)
        row_po_derived = _row(
            db, company_id, inquiry_a.id, line_a.id, product_a.product_code, "8",
            delivery_date=date(2026, 6, 1),
        )
        po_a, po_line_a = _po_line(db, company_id, product_a, supplier)
        _link(db, company_id, row_po_derived.id, "8", po_line_id=po_line_a.id,
              document=po_a.po_number)
        _spo(
            db, company_id, spo_number=f"ZZT-SPO-{_uid()[:6]}", product_id=product_a.id,
            from_po_number=po_a.po_number, allocated_quantity=5,
        )

        # row_unlinked: qty 4, nothing.
        product_b = _product(db, f"ZZT-DSPO-KIND-B-{_uid()[:6]}", f"{MARKER} product b")
        order_b, line_b = _order_and_line(
            db, company_id, project.id, product_b, "4", date(2026, 6, 1)
        )
        inquiry_b = _inquiry(db, company_id, order_b.id, raiser)
        row_unlinked = _row(
            db, company_id, inquiry_b.id, line_b.id, product_b.product_code, "4",
            delivery_date=date(2026, 6, 1),
        )

        # row_full_spo: qty 6, linked entirely (a REAL link) to an SPO allocation.
        product_c = _product(db, f"ZZT-DSPO-KIND-C-{_uid()[:6]}", f"{MARKER} product c")
        order_c, line_c = _order_and_line(
            db, company_id, project.id, product_c, "6", date(2026, 6, 1)
        )
        inquiry_c = _inquiry(db, company_id, order_c.id, raiser)
        row_full_spo = _row(
            db, company_id, inquiry_c.id, line_c.id, product_c.product_code, "6",
            delivery_date=date(2026, 6, 1),
        )
        allocation_c = _spo(
            db, company_id, spo_number=f"ZZT-SPO-{_uid()[:6]}", product_id=product_c.id,
            from_po_number=None, allocated_quantity=6,
        )
        _link(db, company_id, row_full_spo.id, "6", spo_allocation_id=allocation_c.id,
              document=allocation_c.spo_number)

        db.commit()
        client, originals = _client(db, raiser, READ_ONLY)
        try:
            with company_scope(db, frozenset({company_id})):
                yield client, db, company_id, {
                    "row_po_derived": row_po_derived,
                    "row_unlinked": row_unlinked,
                    "row_full_spo": row_full_spo,
                }
        finally:
            _restore(originals)


def test_the_stage_cards_split_a_po_linked_row_between_purchased_and_incoming(api):
    """AC-D5: a row of 8, linked to a PO with a derived SPO of 5, contributes 5 to
    Incoming (`spo`) and 3 to Purchased (`po`) and 0 to Buy - a unit counted once, the
    furthest it reached."""
    client, _db, _company_id, _seeded = api

    body = client.get(SUMMARY).json()
    kinds = body["kinds"]

    # row_full_spo (6) is entirely on a REAL spo link, so it also lands in `spo`.
    assert kinds["spo"] == "11", kinds
    assert kinds["po"] == "3", kinds
    assert kinds["buy"] == "4", kinds


def test_kind_spo_lists_the_derived_row(api):
    client, _db, _company_id, seeded = api

    body = client.get(LIST, params={"kind": "spo"}).json()

    ids = {row["id"] for row in body["data"]}
    assert seeded["row_po_derived"].id in ids
    assert seeded["row_full_spo"].id in ids
    assert seeded["row_unlinked"].id not in ids


def test_kind_po_lists_the_row_with_purchased_remainder(api):
    client, _db, _company_id, seeded = api

    body = client.get(LIST, params={"kind": "po"}).json()

    ids = {row["id"] for row in body["data"]}
    assert seeded["row_po_derived"].id in ids
    assert seeded["row_unlinked"].id not in ids
    assert seeded["row_full_spo"].id not in ids


def test_kind_buy_lists_only_the_unlinked_row(api):
    client, _db, _company_id, seeded = api

    body = client.get(LIST, params={"kind": "buy"}).json()

    ids = {row["id"] for row in body["data"]}
    assert ids == {seeded["row_unlinked"].id}


# ------------------------------------------------------------------------------ AC-D6b


def test_linked_spo_includes_the_derived_row_too(api):
    """AC-D6b: `linked=spo` is WHERE the row is linked (AC-I5), widened by S5 to include
    a row whose only real link is on a PO but whose PO carries a derived SPO cover -
    the row genuinely is on both books now, one of them derived."""
    client, _db, _company_id, seeded = api

    body = client.get(LIST, params={"linked": "spo"}).json()

    ids = {row["id"] for row in body["data"]}
    assert seeded["row_po_derived"].id in ids
    assert seeded["row_full_spo"].id in ids
    assert seeded["row_unlinked"].id not in ids


def test_linked_po_still_lists_the_derived_row_its_real_link_is_still_there(api):
    client, _db, _company_id, seeded = api

    body = client.get(LIST, params={"linked": "po"}).json()

    ids = {row["id"] for row in body["data"]}
    assert seeded["row_po_derived"].id in ids


def test_linked_none_excludes_the_derived_row_it_does_hold_a_real_link(api):
    client, _db, _company_id, seeded = api

    body = client.get(LIST, params={"linked": "none"}).json()

    ids = {row["id"] for row in body["data"]}
    assert seeded["row_po_derived"].id not in ids
    assert seeded["row_unlinked"].id in ids


# ------------------------------------------------------------------------------ AC-D9


def test_the_spo_verb_refusal_code_is_renamed_and_the_old_string_is_gone():
    """The rename is textual (R-D: "no behaviour change, one test renames the code") -
    `_SPO_LINKABLE_VERBS` already equals `_LINKABLE_VERBS`, so `_assert_linkable` refuses
    an unlinkable verb before the SPO-specific branch is ever reached and there is no
    live code path left to exercise at runtime. What is checked is the SOURCE: the new
    code is declared, and the old one - and every docstring claiming an SPO answers only
    an ORDER BACK row - are gone.

    Round 3 fix: the previous version of this assertion lower-cased the HAYSTACK but not
    the NEEDLE (`"only an ORDER BACK row" not in source.lower()` can never be true - the
    needle still carries its own uppercase letters), so it passed vacuously whether or
    not the phrase was there. Both sides are lower-cased now, and a second phrase this
    missed - `link_candidate_products`' own docstring, "an ORDER BACK row may link to
    either" (`project_order_inquiry_service.py` ~5921) - is checked too.
    """
    import inspect

    from app.services import project_order_inquiry_service as svc

    raw = inspect.getsource(svc).lower()
    # Whitespace-normalised too: the surviving phrase wraps across a docstring line
    # break ("...an ORDER BACK row may\n        link to either..."), so a single-line
    # substring search would miss it exactly the way the un-lower-cased needle did.
    source = " ".join(raw.split())
    assert "order_inquiry_spo_not_linkable" in raw
    assert "order_inquiry_spo_not_order_back" not in raw
    assert "only an order back row" not in source
    assert "an order back row may link to either" not in source
