"""OI-SPO-EXACT: an order inquiry link follows the EXACT purchase order line to the SPO
allocation that names it, and never the purchase order document.

Plan: `documentation/plans/scm/PLAN-oi-spo-exact-13oct.md`. Owner, 13 Oct 2026: "AutoCount
linkage is the truth, explicit links only, no matching guesses".

The rule: SO line -> the PO line whose `from_so_line_ref` is the SO line's `source_ref` (or
an AutoCount claim that pairs them) -> ONLY the SPO allocations whose `from_po_number` AND
`from_po_line_ref` name that PO line. None -> the row links to the PO line itself.

The owner's case (12 Oct prod backup): OI-2609-0717, SO385467, SRTWB1515 32 BRW-BB. Its PO
line on 202512-S0014 (32, received 0) has shipped nothing; a SIBLING line of the same PO and
product (36, bought for another sales order line) is on SPO-2026/04-0053 line 25, received.
`_through_po` used to fall back to "any allocation of this PO number + product" and linked
the row to the sibling's container. Every test below is that shape or one of its edges.

The live path (`ProjectOrderInquiryService.follow_book_for_rows`, the entry the owner's row
came through) is driven here; the importer reaches the same `pair_needs`. Seeds are the
follow-book chain file's own, imported rather than copied.

Postgres only (`tests/_pg_fixture.py::blank_session` through `ctx`).
"""
from __future__ import annotations

from decimal import Decimal

from app.models.procurement import PurchaseOrderLine
from app.models.scm import OrderLinkClaim
from app.services import project_order_inquiry_import_service as importer
from app.services.project_order_inquiry_service import ProjectOrderInquiryService

from .test_oi_follow_book_chain import (  # the seeded world, not copied
    _links_of,
    _ref,
    _seed_po_line,
    _seed_product,
    _seed_row_and_mirror,
    _seed_so_line,
    _seed_spo_line,
    ctx,  # noqa: F401 - pytest fixture
)


def _sibling_line(db, *, po, product_id, qty_ordered, from_so_line_ref=None, qty_received="0"):
    """A SECOND line on an existing purchase order, with its own `source_ref`: one PO
    carrying two lines of one item for two different sales order lines is the whole shape."""
    line = PurchaseOrderLine(
        company_id=po.company_id,
        purchase_order_id=po.id,
        product_id=product_id,
        qty_ordered=Decimal(qty_ordered),
        qty_received=Decimal(qty_received),
        line_status="open",
        from_so_line_ref=from_so_line_ref,
        source_ref=_ref("POL"),
    )
    db.add(line)
    db.flush()
    return line


def _follow(db, ctx, row):
    ProjectOrderInquiryService(db).follow_book_for_rows(
        [str(row.id)], trigger="autocount_ingest", company_id=ctx.company_a, actor_user_id=None
    )
    return _links_of(db, row.id)


def _case_one(db, ctx, *, name_by_ref: bool = True):
    """The owner's shape: (row, own PO line, the sibling's allocation, core line, sales
    order, purchase order, product).

    The sibling line (36, another SO line's) is on an SPO, fully received; this row's own
    line (32) has shipped nothing and no allocation names it.
    """
    product = _seed_product(db, company_id=ctx.company_a)
    ref = _ref("SOL")
    so, core_line = _seed_so_line(
        db, company_id=ctx.company_a, product_id=product.id, source_ref=ref, qty="32"
    )
    po, sibling = _seed_po_line(
        db,
        company_id=ctx.company_a,
        product_id=product.id,
        from_so_line_ref=_ref("OTHER-SOL"),
        qty_ordered="36",
        qty_received="36",
    )
    own = _sibling_line(
        db, po=po, product_id=product.id, qty_ordered="32",
        from_so_line_ref=ref if name_by_ref else None,
    )
    sibling_allocation = _seed_spo_line(
        db,
        company_id=ctx.company_a,
        product_id=product.id,
        from_po_number=po.po_number,
        from_po_line_ref=sibling.source_ref,
        allocated_quantity=36,
        quantity_received=36,
    )
    _pso, _mirror, _inquiry, row = _seed_row_and_mirror(
        db, company_id=ctx.company_a, core_line=core_line, product_id=product.id, qty="32"
    )
    return row, own, sibling_allocation, core_line, so, po, product


def test_case_one_ref_links_the_own_po_line_not_the_siblings_spo(ctx):
    """The owner's case through the line reference: the row's own PO line answers for all
    32, the sibling's container answers for none."""
    db = ctx.db
    row, own, sibling_allocation, *_ = _case_one(db, ctx)
    db.commit()

    links = _follow(db, ctx, row)

    assert [str(link.spo_allocation_id) for link in links if link.spo_allocation_id] == [], (
        "the row landed on an SPO allocation that names another line of its PO"
    )
    assert len(links) == 1, [(link.document, str(link.qty)) for link in links]
    assert str(links[0].po_line_id) == str(own.id)
    assert Decimal(str(links[0].qty)) == Decimal("32")
    assert all(str(link.spo_allocation_id) != str(sibling_allocation.id) for link in links)


def test_case_one_claim_links_the_own_po_line_not_the_siblings_spo(ctx):
    """The same shape when only an AutoCount claim pairs the SO line with the PO line (the
    case-1 row had both): the claim walk follows the same exact-line rule.

    Through `pair_needs` itself, the one rule the importer and `follow_book_for_rows` both
    call: the live path only picks up rows whose ref the book names (`_refs_named_by_book`),
    so a claim-only line is reached by the importer, not by it.
    """
    db = ctx.db
    row, own, sibling_allocation, core_line, so, po, product = _case_one(
        db, ctx, name_by_ref=False
    )
    db.add(OrderLinkClaim(
        company_id=ctx.company_a,
        so_number=so.so_number,
        po_number=po.po_number,
        item_code=product.product_code,
        source="autocount",
        so_line_id=str(core_line.id),
        po_line_id=str(own.id),
    ))
    db.flush()

    links, _not_linkable = importer.pair_needs(
        db, [importer._Need(key="row", need_qty=Decimal("32"), core_line=core_line)],
        ([], []),
    )

    takes = links["row"].takes
    assert [take["spo_allocation_id"] for take in takes] == [None], takes
    assert [(take["po_line_id"], take["qty"]) for take in takes] == [
        (str(own.id), Decimal("32"))
    ], takes


def test_own_allocation_is_still_taken_first(ctx):
    """The rule removes the guess, not the chain: when an allocation DOES name the row's own
    PO line, it takes the quantity first and the PO line takes only the remainder (D10)."""
    db = ctx.db
    row, own, sibling_allocation, _core, _so, po, _product = _case_one(db, ctx)
    own_allocation = _seed_spo_line(
        db,
        company_id=ctx.company_a,
        product_id=own.product_id,
        from_po_number=po.po_number,
        from_po_line_ref=own.source_ref,
        allocated_quantity=20,
    )
    db.commit()

    links = _follow(db, ctx, row)

    by_target = {
        str(link.spo_allocation_id or link.po_line_id): Decimal(str(link.qty)) for link in links
    }
    assert by_target == {str(own_allocation.id): Decimal("20"), str(own.id): Decimal("12")}, (
        by_target
    )
    assert str(sibling_allocation.id) not in by_target


def test_allocation_naming_only_the_po_number_is_not_taken(ctx):
    """An allocation that states the PO number but no PO line is not evidence of THIS line
    (owner: explicit links only). The row links to its PO line; no fallback. (The 14 Sep
    prod copy held 0 such allocations, `_less_own_shipments`.)"""
    db = ctx.db
    product = _seed_product(db, company_id=ctx.company_a)
    ref = _ref("SOL")
    _so, core_line = _seed_so_line(
        db, company_id=ctx.company_a, product_id=product.id, source_ref=ref, qty="10"
    )
    po, po_line = _seed_po_line(
        db, company_id=ctx.company_a, product_id=product.id, from_so_line_ref=ref,
        qty_ordered="10",
    )
    unnamed = _seed_spo_line(
        db, company_id=ctx.company_a, product_id=product.id, from_po_number=po.po_number,
        allocated_quantity=10,
    )
    _pso, _mirror, _inquiry, row = _seed_row_and_mirror(
        db, company_id=ctx.company_a, core_line=core_line, product_id=product.id, qty="10"
    )
    db.commit()

    links = _follow(db, ctx, row)

    assert len(links) == 1, [(link.document, str(link.qty)) for link in links]
    assert str(links[0].po_line_id) == str(po_line.id)
    assert str(links[0].spo_allocation_id) != str(unnamed.id)


def test_quantity_split_across_two_po_lines_each_follows_its_own_allocation(ctx):
    """One SO line bought on TWO PO lines (20 + 30) of one PO; each line's own container
    (20 on the first, 10 on the second) is taken, the second line answers for its unshipped
    20, and nothing crosses from one line's container to the other."""
    db = ctx.db
    product = _seed_product(db, company_id=ctx.company_a)
    ref = _ref("SOL")
    _so, core_line = _seed_so_line(
        db, company_id=ctx.company_a, product_id=product.id, source_ref=ref, qty="50"
    )
    po, first = _seed_po_line(
        db, company_id=ctx.company_a, product_id=product.id, from_so_line_ref=ref,
        qty_ordered="20",
    )
    second = _sibling_line(
        db, po=po, product_id=product.id, qty_ordered="30", from_so_line_ref=ref
    )
    first_allocation = _seed_spo_line(
        db, company_id=ctx.company_a, product_id=product.id, from_po_number=po.po_number,
        from_po_line_ref=first.source_ref, allocated_quantity=20,
    )
    second_allocation = _seed_spo_line(
        db, company_id=ctx.company_a, product_id=product.id, from_po_number=po.po_number,
        from_po_line_ref=second.source_ref, allocated_quantity=10,
    )
    _pso, _mirror, _inquiry, row = _seed_row_and_mirror(
        db, company_id=ctx.company_a, core_line=core_line, product_id=product.id, qty="50"
    )
    db.commit()

    links = _follow(db, ctx, row)

    by_target = {
        str(link.spo_allocation_id or link.po_line_id): Decimal(str(link.qty)) for link in links
    }
    assert by_target == {
        str(first_allocation.id): Decimal("20"),
        str(second_allocation.id): Decimal("10"),
        str(second.id): Decimal("20"),
    }, by_target
