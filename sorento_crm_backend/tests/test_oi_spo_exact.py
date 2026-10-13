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


# --------------------------------------------------------------------------- repair


def _fallback_link(db, ctx, row, allocation, *, qty, auto=True):
    """A link exactly as the old fallback wrote it (or, `auto=False`, a person's click)."""
    from app.models.project_so import OrderInquiryLink

    link = OrderInquiryLink(
        company_id=ctx.company_a,
        row_id=row.id,
        spo_allocation_id=allocation.id,
        document=allocation.spo_number,
        qty=Decimal(qty),
        auto=auto,
    )
    db.add(link)
    db.flush()
    return link


def test_repair_moves_the_fallback_link_and_leaves_manual_links(ctx, capsys):
    """The repair script: a dry run writes nothing and names the change; `--apply` moves the
    case-1 row from the sibling's SPO to its own PO line; a MANUAL link with the same shape
    is untouched; a second run finds nothing."""
    from scripts import repair_oi_spo_exact as repair

    db = ctx.db
    row, own, sibling_allocation, _core, _so, _po, product = _case_one(db, ctx)
    _fallback_link(db, ctx, row, sibling_allocation, qty="32")

    manual_row, manual_own, manual_sibling, *_rest = _case_one(db, ctx)
    _fallback_link(db, ctx, manual_row, manual_sibling, qty="32", auto=False)
    db.commit()

    def targets(of_row):
        return sorted(
            (str(link.spo_allocation_id or link.po_line_id), Decimal(str(link.qty)))
            for link in _links_of(db, of_row.id)
        )

    assert repair.main([], db=db) == 0
    db.expire_all()
    assert targets(row) == [(str(sibling_allocation.id), Decimal("32"))], "dry run wrote"
    out = capsys.readouterr().out
    assert product.product_code in out and "1 row(s) would be re-linked" in out, out

    assert repair.main(["--apply"], db=db) == 0
    db.expire_all()
    assert targets(row) == [(str(own.id), Decimal("32"))]
    assert targets(manual_row) == [(str(manual_sibling.id), Decimal("32"))], (
        "a person's link was moved"
    )

    capsys.readouterr()
    assert repair.main(["--apply"], db=db) == 0
    assert "0 row(s) re-linked" in capsys.readouterr().out


def test_repair_leaves_every_auto_link_the_book_states(ctx, capsys):
    """The repair never touches an auto link the book itself states: on an allocation
    naming the row's own PO line, on one naming the SO line directly (`from_so_line_ref`),
    or on one naming a PO line of the row that was cancelled after the link was written."""
    from scripts import repair_oi_spo_exact as repair

    db = ctx.db
    row, own, sibling_allocation, core_line, _so, po, product = _case_one(db, ctx)
    own_allocation = _seed_spo_line(
        db, company_id=ctx.company_a, product_id=product.id, from_po_number=po.po_number,
        from_po_line_ref=own.source_ref, allocated_quantity=10,
    )
    direct_allocation = _seed_spo_line(
        db, company_id=ctx.company_a, product_id=product.id, from_po_number=po.po_number,
        from_so_line_ref=core_line.source_ref, allocated_quantity=10,
    )
    cancelled = _sibling_line(
        db, po=po, product_id=product.id, qty_ordered="12",
        from_so_line_ref=core_line.source_ref,
    )
    cancelled.line_status = "cancelled"
    cancelled_allocation = _seed_spo_line(
        db, company_id=ctx.company_a, product_id=product.id, from_po_number=po.po_number,
        from_po_line_ref=cancelled.source_ref, allocated_quantity=12,
    )
    for allocation, qty in (
        (own_allocation, "10"), (direct_allocation, "10"), (cancelled_allocation, "12"),
    ):
        _fallback_link(db, ctx, row, allocation, qty=qty)
    db.commit()
    before = sorted(str(link.id) for link in _links_of(db, row.id))

    assert repair.main(["--apply"], db=db) == 0

    db.expire_all()
    assert sorted(str(link.id) for link in _links_of(db, row.id)) == before
    assert "0 row(s) re-linked" in capsys.readouterr().out
