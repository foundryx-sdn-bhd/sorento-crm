"""The order inquiry sheet pairs on AutoCount's OWN line reference.

Contract: `documentation/plans/scm/scm-oi-sheet-pairing-repair-acceptance-criteria.md`,
AC-R-1 to AC-R-16, with `PLAN-scm-oi-sheet-pairing-repair.md` sections 2.1 to 2.4 for the
promised behaviour. One test per criterion, named for it.

TEST-FIRST, written before the importer was repaired. The red state is therefore the
SHIPPED behaviour of #875 - no link at all where AutoCount states one, the wrong sales
order line under a citation, two rows raised where the customer restated one - or a
`ModuleNotFoundError` for a rollback script that does not exist yet. Never an import typo
and never a fixture bug.

What changes, in the operator's words (owner rulings R1 to R3, 14 Sep 2026):

* **R1** the pairing is `purchase_order_lines.from_so_line_ref = sales_order_lines.source_ref`
  (and the `spo_allocations` twin, and `from_po_number` onwards to the shipping order).
  Claims become the FALLBACK, and `po_history` - the poisoned August Excel - pairs nothing.
* **R2** when several lines of the order fit, the line the CITED document names wins, and a
  cancelled ghost line loses to any real line that fits.
* **R3** a row that restates another on SO + item + qty + delivery date + location is a
  restatement whatever its remark says, and it lends its citation to the row it restates.

Fixture vocabulary, as the UAC spells it: a "ref" is `sales_order_lines.source_ref`
(`AED_SORENTO:41576559:41604391`); a PO line or SPO allocation "names" a line when its
`from_so_line_ref` equals that ref.

Postgres only (`tests/_pg_fixture.py`, `blank_session` through the parent file's `world()`).
Every chain is seeded here - company, uom, category, product, warehouse, supplier, sales
order + lines, purchase orders + lines, allocations, claims - because CI's database is
empty and nothing may be read off an existing row. The seed helpers are IMPORTED from
`tests/test_project_order_inquiry_import_migration.py` rather than copied, so the two files
cannot drift about what a seeded world is.

`test_ac_r_10_cancelled_ghost_loses_to_real_line`, `test_ac_r_22_ambiguous_ref_never_promotes_a_ghost`,
`test_ac_r_32_bought_line_outranks_unbought_without_citation`,
`test_ac_r_33_bought_beats_open_but_not_cancelled`, `test_ac_r_41_exact_date_beats_bought_line`
and `test_ac_r_42_bought_decides_when_no_line_matches_sheet_date` retired under R4,
PLAN-board-received-stock-own-arrival, 21 Sep 2026 - each pinned R2's citation-ranked line
pick (or D1's "cancelled still matches when lone"), both superseded by
`_pick_lines_by_date_order`'s date-order pairing and AC-S4-3; see the retirement notes left
in their place, in file order.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest
import sqlalchemy as sa

from app.models.base import company_scope
from app.models.order import SalesOrderLine
from app.models.procurement import PurchaseOrderLine, SPOAllocation
from app.models.project_so import (
    IV_ORDER_BACK,
    OrderInquiry,
    OrderInquiryLink,
    OrderInquiryRow,
)
from app.models.scm import OrderLinkClaim
from app.services import project_order_inquiry_import_service as importer
from app.services.import_outcome import ImportOutcome
from app.services.project_so_adoption_service import ProjectSOAdoptionService

from ._pg_fixture import blank_session, pg_session
from .test_project_order_inquiry_import_migration import (  # the seeded world, not copied
    D_NOV,
    D_OCT,
    MARKER,
    RESULT_KEYS,
    World,
    _n,
    _uid,
    book,
    sheet,
    world,
)

#: The shape AutoCount writes into `sales_order_lines.source_ref`:
#: `AED_SORENTO:<document key>:<line key>`. The exact string from the 14 Sep prod
#: measurement is `AED_SORENTO:41576559:41604391`; these are minted in the same family so
#: nothing in the importer can special-case a test-looking value.
def _ref() -> str:
    return f"AED_SORENTO:{41576559 + _n()}:{41604391 + _n()}"


def _with_ref(w: World, line: SalesOrderLine, ref: str) -> SalesOrderLine:
    """The ref AutoCount stamped on this sales order line.

    `World.line` does not take one (the parent slice never read the column), so it is set
    here rather than by widening a helper 44 other tests depend on.
    """
    line.source_ref = ref
    w.db.flush()
    return line


def _names(w: World, target, ref: str):
    """This purchase-order line / allocation states it is for that sales order line."""
    target.from_so_line_ref = ref
    w.db.flush()
    return target


def _born_at(w: World, line: SalesOrderLine, when: datetime) -> SalesOrderLine:
    """`created_at` written explicitly, because it is the LAST tiebreak in the line pick.

    `server_default=func.now()` gives every line seeded in one transaction the SAME
    timestamp (Postgres freezes `now()` per transaction), and `sorted` is stable, so a test
    that relies on "the older line" without saying which is older would be measuring the
    order the SELECT happened to return.
    """
    line.created_at = when
    w.db.flush()
    return line


def _apply(w: World, data: bytes, *, file_name: str | None = None, outcome=None) -> dict:
    """`importer.apply` with the uploader the world seeded, and the file's own name.

    `World.apply` cannot pass `file_name`, and the rollback (AC-R-13 to AC-R-16) is keyed
    on the stamp that name leaves on every raised row.
    """
    return importer.apply(
        w.db, data, actor=w.actor, outcome=outcome, file_name=file_name
    )


def _link_row(
    w: World,
    *,
    document: str,
    qty: str,
    po_line: PurchaseOrderLine | None = None,
    allocation: SPOAllocation | None = None,
) -> OrderInquiryLink:
    """An EXISTING link from somebody else's row, occupying that target's capacity.

    Seeded through a second sales order and the board's own writer, so the occupied
    capacity is the same fact `_claimed_capacity` reads for any other link. Either target
    may be named - a purchase order line or a shipping order allocation - because both can
    be full when this row arrives.
    """
    other = w.order()
    other_line = w.line(other, qty_ordered="50")
    ProjectSOAdoptionService(w.db).adopt(str(other.id), w.actor)
    held = w.board_row(w.mirror_of(other_line), qty=qty)
    link = OrderInquiryLink(
        id=_uid(),
        company_id=w.company_id,
        row_id=held.id,
        po_line_id=po_line.id if po_line is not None else None,
        spo_allocation_id=allocation.id if allocation is not None else None,
        document=document,
        qty=Decimal(qty),
        linked_by=w.actor,
        linked_at=datetime(2026, 6, 3, 9, 0, 0),
        auto=False,
    )
    w.db.add(link)
    w.db.flush()
    return link


def _documents(links) -> list:
    return [link.document for link in links]


# --------------------------------------------------------------------------- #
# R1: the pairing is the ref AutoCount already wrote                           #
# --------------------------------------------------------------------------- #


def test_ac_r_1_po_line_ref_pairs_without_any_claim():
    """AC-R-1. A purchase order line that NAMES the sales order line pairs the row, with no
    `order_link_claim` row anywhere.

    This is the whole repair. On the 3am 14 Sep prod copy, 32,674 PO lines name a held sales
    order line and only 4,277 of them carry a claim saying so - the claim table is one row
    per `(so_number, po_number, item_code)` and never repoints, so the August `po_history`
    Excel had already taken the key for 28,397 of them. The column is exact, persisted, and
    is what the owner's own query reads.
    """
    with world() as w:
        ref = _ref()
        order = w.order()
        _with_ref(w, w.line(order, qty_ordered="50"), ref)
        po, po_line = w.po_line(qty_ordered="50")
        _names(w, po_line, ref)
        data = sheet([
            (order.so_number, w.product.product_code, 30, D_OCT,
             w.warehouse.warehouse_code, ""),
        ])

        result = _apply(w, data)

        assert w.db.query(OrderLinkClaim).filter(
            OrderLinkClaim.source != "order_inquiry"
        ).count() == 0, "this criterion is about a pairing NO claim states"
        assert result["rows_raised"] == 1, result
        row = w.one_row()
        links = w.links(row)
        assert len(links) == 1, _documents(links)
        assert str(links[0].po_line_id) == str(po_line.id)
        assert links[0].document == po.po_number
        assert Decimal(str(links[0].qty)) == Decimal("30")
        assert links[0].auto is True, "a pairing the book states is not a person's click"
        assert f"auto: {importer._AUTOCOUNT_TRIGGER}" in (row.note or ""), row.note
        assert result["links_written"] == 1
        assert result["links_from_autocount"] == 1


def test_ac_r_2_ref_po_chains_to_spo_first():
    """AC-R-2. Reached through the ref, D10 still holds: the shipping order the purchase
    order became takes the quantity first, and the PO line takes only the remainder.

    87 is the owner's own case (SO347594 / CB2154-DIY): the allocation covers 30 of it and
    the purchase order line answers for the other 57.

    The allocation names the purchase order LINE (`from_po_line_ref`), as every AutoCount
    allocation that names a source purchase order does: OI-SPO-EXACT (13 Oct) removed the
    walk by document number alone (`test_oi_spo_exact.py`).
    """
    with world() as w:
        ref = _ref()
        po_line_ref = f"AED_SORENTO:{_n()}:PO"
        order = w.order()
        _with_ref(w, w.line(order, qty_ordered="100"), ref)
        po, po_line = w.po_line(qty_ordered="87")
        _names(w, po_line, ref)
        po_line.source_ref = po_line_ref
        w.db.flush()
        allocation = w.spo_allocation(
            quantity=30, from_po_number=po.po_number, from_po_line_ref=po_line_ref
        )
        data = sheet([
            (order.so_number, w.product.product_code, 87, D_OCT,
             w.warehouse.warehouse_code, ""),
        ])

        result = _apply(w, data)

        links = w.links(w.one_row())
        assert [str(link.spo_allocation_id or "") for link in links] == [
            str(allocation.id), "",
        ], _documents(links)
        assert [Decimal(str(link.qty)) for link in links] == [
            Decimal("30"), Decimal("57"),
        ]
        assert str(links[1].po_line_id) == str(po_line.id)
        assert result["links_from_autocount"] == 1


def test_ac_r_3_spo_direct_ref_before_po_ref():
    """AC-R-3. An allocation that names the line directly is taken before a purchase order
    line that also names it - "SPO first then PO" (R5 of `PLAN-scm-oi-draft-links.md`),
    unchanged, now reached through the ref rather than through a claim."""
    with world() as w:
        ref = _ref()
        order = w.order()
        _with_ref(w, w.line(order, qty_ordered="50"), ref)
        _po, po_line = w.po_line(qty_ordered="50")
        _names(w, po_line, ref)
        allocation = _names(w, w.spo_allocation(quantity=50), ref)
        data = sheet([
            (order.so_number, w.product.product_code, 30, D_OCT,
             w.warehouse.warehouse_code, ""),
        ])

        _apply(w, data)

        links = w.links(w.one_row())
        assert len(links) == 1, _documents(links)
        assert str(links[0].spo_allocation_id) == str(allocation.id)
        assert links[0].po_line_id is None


def test_ac_r_4_po_history_claim_alone_links_nothing():
    """AC-R-4. A `po_history` claim is not the book: it pairs NOTHING.

    Those 33,235 rows are one August Excel extract, and they hold the claim key for 28,397
    pairings AutoCount states exactly on the purchase side. The row is still raised - it is
    the instruction the sheet carries - and the sheet's own citation is still tried, so
    removing this source loses no pairing the operator asked for.
    """
    with world() as w:
        order = w.order()
        line = _with_ref(w, w.line(order, qty_ordered="50"), _ref())
        po, po_line = w.po_line(qty_ordered="50")
        w.claim(
            order=order, core_line=line, document=po.po_number,
            po_line=po_line, source="po_history",
        )
        data = sheet([
            (order.so_number, w.product.product_code, 30, D_OCT,
             w.warehouse.warehouse_code, ""),
        ])

        result = _apply(w, data)

        assert result["rows_raised"] == 1, result
        assert w.links(w.one_row()) == [], "the August extract paired the row anyway"
        assert result["links_written"] == 0
        assert result["links_from_autocount"] == 0

        # The second world this test carried before #915 ("source 3 must still run once
        # the August claim stops answering") is DELETED: source 3, the sheet's citation, no
        # longer exists at all (8.1 change 1), so there is nothing left for the August
        # claim's absence to fall through to.


def test_ac_r_5_autocount_claim_without_ref_still_links():
    """AC-R-5. Source 2 survives. Where AutoCount stated only the document NUMBER, the
    ingest's resolved `autocount` claim is all there is, and it still pairs the row.

    Green before the repair as well as after: it is the half of D9 the repair must not take
    away while it demotes the claim table.
    """
    with world() as w:
        order = w.order()
        line = _with_ref(w, w.line(order, qty_ordered="50"), _ref())
        po, po_line = w.po_line(qty_ordered="50")
        assert po_line.from_so_line_ref is None, "the purchase side states no line here"
        w.claim(order=order, core_line=line, document=po.po_number, po_line=po_line)
        data = sheet([
            (order.so_number, w.product.product_code, 30, D_OCT,
             w.warehouse.warehouse_code, ""),
        ])

        result = _apply(w, data)

        links = w.links(w.one_row())
        assert len(links) == 1, _documents(links)
        assert str(links[0].po_line_id) == str(po_line.id)
        assert Decimal(str(links[0].qty)) == Decimal("30")
        assert result["links_from_autocount"] == 1


def test_ac_r_6_ref_beats_claim_to_other_document():
    """AC-R-6. The ref outranks a claim that names a different document.

    Both have room for the whole need, so the only thing being measured is which source is
    asked first - and the claim key is exactly what the August extract poisoned.
    """
    with world() as w:
        ref = _ref()
        order = w.order()
        line = _with_ref(w, w.line(order, qty_ordered="50"), ref)
        named_po, named_line = w.po_line(qty_ordered="50")
        _names(w, named_line, ref)
        claimed_po, claimed_line = w.po_line(qty_ordered="50")
        w.claim(
            order=order, core_line=line, document=claimed_po.po_number,
            po_line=claimed_line,
        )
        data = sheet([
            (order.so_number, w.product.product_code, 30, D_OCT,
             w.warehouse.warehouse_code, ""),
        ])

        _apply(w, data)

        links = w.links(w.one_row())
        assert len(links) == 1, _documents(links)
        assert str(links[0].po_line_id) == str(named_line.id)
        assert links[0].document == named_po.po_number
        assert all(
            str(link.po_line_id) != str(claimed_line.id) for link in links
        ), "the claim's document took quantity the ref had already answered for"


def test_ac_r_7_named_line_with_no_capacity_is_skipped():
    """AC-R-7. The capacity rule is unchanged by the new source: a named line that existing
    links have already filled is SKIPPED, and the need falls through to the NEXT purchase
    order line the ref itself names (issue #915 retired the sheet's citation as a fallback;
    the ref can name more than one document for the same line, and the walk over them -
    still inside source 1 - is what this criterion is about).

    Green before the repair too - source 1 does not exist yet, so nothing tries the named
    line at all. It is here so the repair cannot make the ref a special case that overruns a
    line other rows already hold.
    """
    with world() as w:
        ref = _ref()
        order = w.order()
        _with_ref(w, w.line(order, qty_ordered="50"), ref)
        full_po, full_line = w.po_line(qty_ordered="20")
        _names(w, full_line, ref)
        _link_row(w, po_line=full_line, document=full_po.po_number, qty="20")
        spare_po, spare_line = w.po_line(qty_ordered="50")
        _names(w, spare_line, ref)
        data = sheet([
            (order.so_number, w.product.product_code, 30, D_OCT,
             w.warehouse.warehouse_code, ""),
        ])

        result = _apply(w, data)

        raised = [
            row for row in w.rows()
            if (row.note or "").startswith(importer._MIGRATION_STAMP)
        ]
        assert len(raised) == 1, [row.item_code for row in raised]
        links = w.links(raised[0])
        assert len(links) == 1, _documents(links)
        assert str(links[0].po_line_id) == str(spare_line.id)
        assert links[0].document == spare_po.po_number
        assert Decimal(str(links[0].qty)) == Decimal("30")
        assert all(
            str(link.po_line_id) != str(full_line.id) for link in links
        ), "a line with no capacity was linked anyway"
        assert result["links_partial"] == 0
        assert result["links_from_autocount"] == 1


# --------------------------------------------------------------------------- #
# R2: which of the order's lines the row means                                 #
# --------------------------------------------------------------------------- #


def test_ac_r_8_cited_po_ref_picks_the_named_line():
    """AC-R-8, restated by AC-R-39 (`PLAN-scm-oi-sheet-pairing-repair.md` section 8, issue
    #915). The remark no longer picks the line: two lines of the same item both fit, the
    cited purchase order names the SECOND, but the row's own delivery date is the FIRST
    line's - and the row now lands on the FIRST, not the second.

    Before #915 the citation was term 1 of `_rank_for` and picked the second line off a
    90-qty candidate the sheet's own date says nothing about (prod row 113, tab "JAN 26").
    The owner's ruling ("let's ignore the sheet remark at all") retires that term outright,
    so the row's own date decides and the citation is not even tried for a link.
    """
    with world() as w:
        order = w.order()
        first = w.line(order, qty_ordered="50", required_date=D_OCT)
        second = _with_ref(
            w, w.line(order, qty_ordered="50", required_date=D_NOV), _ref()
        )
        cited, cited_line = w.po_line(qty_ordered="50")
        _names(w, cited_line, second.source_ref)
        data = sheet([
            (order.so_number, w.product.product_code, 30, D_OCT,
             w.warehouse.warehouse_code, cited.po_number),
        ])

        result = _apply(w, data)

        assert result["rows_raised"] == 1, result
        row = w.one_row()
        mirror = w.mirror_of(first)
        assert mirror is not None, "the line whose date matches the sheet was never mirrored"
        assert str(row.so_line_id) == str(mirror.id), (
            "the remark still picked the line the cited purchase order names"
        )
        assert w.mirror_of(second) is None or str(row.so_line_id) != str(
            w.mirror_of(second).id
        )
        assert w.links(row) == [], "the remark linked the row to the document it cites"


def test_ac_r_9_cited_spo_chain_picks_the_named_line():
    """AC-R-9, restated (issue #915). The same retraction over the chain read BACKWARDS:
    the sheet cites a shipping order, whose allocation carries `from_po_number`, whose
    purchase order line names the SECOND sales order line - and the row still lands on the
    FIRST, matching its own date, with no link at all.
    """
    with world() as w:
        order = w.order()
        first = w.line(order, qty_ordered="50", required_date=D_OCT)
        second = _with_ref(
            w, w.line(order, qty_ordered="50", required_date=D_NOV), _ref()
        )
        source_po, source_line = w.po_line(qty_ordered="50")
        _names(w, source_line, second.source_ref)
        allocation = w.spo_allocation(quantity=50, from_po_number=source_po.po_number)
        data = sheet([
            (order.so_number, w.product.product_code, 30, D_OCT,
             w.warehouse.warehouse_code, allocation.spo_number),
        ])

        result = _apply(w, data)

        assert result["rows_raised"] == 1, result
        row = w.one_row()
        mirror = w.mirror_of(first)
        assert mirror is not None
        assert str(row.so_line_id) == str(mirror.id), (
            "the remark's shipping-order chain still picked the second line"
        )
        assert w.mirror_of(second) is None or str(row.so_line_id) != str(
            w.mirror_of(second).id
        )
        assert w.links(row) == [], "the cited shipping order linked the row anyway"


# test_ac_r_10_cancelled_ghost_loses_to_real_line (AC-R-10, D1) retired under R4,
# PLAN-board-received-stock-own-arrival, 21 Sep 2026: both its scenarios relied on D1's
# "cancelled ranks last but still matches over nothing" - explicitly overturned by AC-S4-3
# ("a closed or cancelled core line is never paired, even when it is the only qty fit").
# Its first scenario's own "real" line was itself CLOSED, so under R4 NEITHER candidate is
# open any more and the row is refused `no_line_for_item`; its second scenario (a lone
# cancelled line) is the identical shape AC-S4-3 already pins
# (`tests/scm/test_board_received_stock_s4_import_pairing.py::test_ac_s4_3_closed_or_cancelled_line_never_paired`).


# --------------------------------------------------------------------------- #
# R3: a restatement, whatever the remark says                                  #
# --------------------------------------------------------------------------- #


def test_ac_r_11_differing_remark_is_a_restatement():
    """AC-R-11. The restatement key is SO + item + qty + delivery date + location. The
    remark leaves it.

    The owner: "what we need from the order inquiries tab is just the sales order, location,
    quantity, delivery date, product ... the remark doesn't really matter, differing remark
    is same also as long as other keys are the same". A roll-up tab that carries the PO
    number the month tab left blank is the same instruction written twice, and raising it
    twice takes the line's quantity twice - which is how the third row here, for the
    quantity genuinely left, fails.

    Replaces the parent's AC-S1-38 reading of the remark.
    """
    with world() as w:
        order = w.order()
        w.line(order, qty_ordered="50")
        cited, _cited_line = w.po_line(qty_ordered="50")
        stated = (order.so_number, w.product.product_code, 30, D_OCT,
                  w.warehouse.warehouse_code, cited.po_number)
        restated = (order.so_number, w.product.product_code, 30, D_OCT,
                    w.warehouse.warehouse_code, "")
        remainder = (order.so_number, w.product.product_code, 20, D_OCT,
                     w.warehouse.warehouse_code, "")
        outcome = ImportOutcome(None, persist=False)

        result = _apply(
            w,
            book(JAN26=[stated, remainder], ROLLUP=[restated]),
            outcome=outcome,
        )

        assert result["rows"] == 3, (
            "the rows the import accounts for; no `+` cell here, so the file's own row "
            "count keeps all three"
        )
        assert outcome.count_of("restates_an_instalment") == 1, outcome.breakdown()
        assert result["rows_raised"] == 2, result
        assert len(w.rows()) == 2, [str(row.qty) for row in w.rows()]
        assert sorted(Decimal(str(row.qty)) for row in w.rows()) == [
            Decimal("20"), Decimal("30"),
        ]
        assert result["rows_line_not_found"] == 0, (
            "the restatement charged the line's quantity twice"
        )


# AC-R-12 ("the FIRST row's blank remark takes the SECOND row's citation, and the one
# raised row is linked to it") is DELETED here (issue #915, plan section 8.3): 8.1 change 1
# removes `_Match.cited` and the citation pre-pass entirely, so there is no longer a
# citation for a restatement to lend, and nothing but the removed source was under test.
# The restatement-dedup behaviour it shared with AC-R-11 (kept, unchanged) is still covered
# there.


# --------------------------------------------------------------------------- #
# S2: taking a wrong upload back out                                           #
# --------------------------------------------------------------------------- #
#
# `scripts/rollback_oi_sheet_upload.py` is a CLI
# (`--file-name "<name as stamped>" [--apply]`, dry-run by default), but the behaviour is
# tested at the function the CLI wraps:
#
#     run(db, file_name: str, apply: bool) -> dict
#
# with the four counts `{"rows", "links", "claims", "inquiries"}`. The CALLER owns the
# transaction, exactly as `scripts/delete_empty_order_inquiries.py::run` does - `run` must
# neither commit nor roll back, or a dry run would discard the caller's own session (and,
# here, the seeded world these tests are holding).


def _rollback():
    """The script under test, imported inside the test so its absence reds only S2."""
    import scripts.rollback_oi_sheet_upload as rollback

    return rollback


def _uploaded(w: World, *, file_name: str, qty: int = 30, product=None):
    """One sales order, one line, one purchase order line that NAMES it (the ref, source 1
    - issue #915 retired the remark as a link source) - raised under `file_name`."""
    order = w.order()
    product = product or w.product
    line = _with_ref(w, w.line(order, product=product, qty_ordered="50"), _ref())
    po, po_line = w.po_line(qty_ordered="50", product=product)
    _names(w, po_line, line.source_ref)
    result = _apply(
        w,
        sheet([
            (order.so_number, product.product_code, qty, D_OCT,
             w.warehouse.warehouse_code, ""),
        ]),
        file_name=file_name,
    )
    assert result["rows_raised"] == 1, result
    assert result["links_written"] == 1, result
    return order, po_line


def _rows_of(w: World, file_name: str) -> list:
    stamp = f"{importer._MIGRATION_STAMP} {file_name}"
    return [row for row in w.db.query(OrderInquiryRow).all()
            if (row.note or "").startswith(stamp)]


def test_ac_r_13_rollback_deletes_only_the_named_file():
    """AC-R-13. The rollback takes out the named upload's rows, their links and the
    `order_inquiry` claims those links wrote - and nothing of any other upload.

    This is what the owner runs on prod before re-uploading the 14 Sep file. A rollback that
    cannot be trusted to stop at one file name is a rollback nobody will run.
    """
    with world() as w:
        _order_a, _line_a = _uploaded(w, file_name="a.xlsx")
        _order_b, _line_b = _uploaded(w, file_name="b.xlsx", product=w.product_row())

        rows_a = _rows_of(w, "a.xlsx")
        rows_b = _rows_of(w, "b.xlsx")
        assert len(rows_a) == 1 and len(rows_b) == 1
        links_a = w.links(rows_a[0])
        links_b = w.links(rows_b[0])
        claim_ids_a = [str(link.claim_id) for link in links_a if link.claim_id]
        claim_ids_b = [str(link.claim_id) for link in links_b if link.claim_id]
        assert claim_ids_a, "the seeded upload wrote no claim to delete"
        assert claim_ids_b
        row_a_id, row_b_id = str(rows_a[0].id), str(rows_b[0].id)
        link_a_ids = [str(link.id) for link in links_a]

        counts = _rollback().run(w.db, file_name="a.xlsx", apply=True)

        assert set(counts) == {"rows", "links", "claims", "inquiries"}, counts
        assert counts["rows"] == 1, counts
        assert counts["links"] == len(link_a_ids), counts
        assert counts["claims"] == len(claim_ids_a), counts
        assert w.db.query(OrderInquiryRow).filter(
            OrderInquiryRow.id == row_a_id
        ).count() == 0
        assert w.db.query(OrderInquiryLink).filter(
            OrderInquiryLink.id.in_(link_a_ids)
        ).count() == 0
        assert w.db.query(OrderLinkClaim).filter(
            OrderLinkClaim.id.in_(claim_ids_a)
        ).count() == 0
        assert w.db.query(OrderInquiryRow).filter(
            OrderInquiryRow.id == row_b_id
        ).count() == 1, "the other file's row went with it"
        assert len(w.links(rows_b[0])) == len(links_b)
        assert w.db.query(OrderLinkClaim).filter(
            OrderLinkClaim.id.in_(claim_ids_b)
        ).count() == len(claim_ids_b)


def test_ac_r_14_rollback_dry_run_deletes_nothing():
    """AC-R-14. Without `--apply` nothing is deleted, and the counts printed are the counts
    the real run would produce - which is the only reason to look at a dry run at all."""
    with world() as w:
        _uploaded(w, file_name="a.xlsx")
        _uploaded(w, file_name="b.xlsx", product=w.product_row())
        rows_a = _rows_of(w, "a.xlsx")
        row_a_id = str(rows_a[0].id)
        link_a_ids = [str(link.id) for link in w.links(rows_a[0])]

        dry = _rollback().run(w.db, file_name="a.xlsx", apply=False)

        assert w.db.query(OrderInquiryRow).filter(
            OrderInquiryRow.id == row_a_id
        ).count() == 1, "a dry run deleted the row"
        assert w.db.query(OrderInquiryLink).filter(
            OrderInquiryLink.id.in_(link_a_ids)
        ).count() == len(link_a_ids)
        assert len(_rows_of(w, "b.xlsx")) == 1, (
            "the dry run rolled the caller's transaction back"
        )

        applied = _rollback().run(w.db, file_name="a.xlsx", apply=True)

        assert dry == applied, (dry, applied)


def test_ac_r_15_rollback_drops_empty_headers_only():
    """AC-R-15. An inquiry header the rollback empties is deleted; one that still carries
    another file's rows stays.

    A header with nothing on it burns an OI number and leaves a dangling "Order inquiries"
    link on the sales order list - the exact defect
    `scripts/delete_empty_order_inquiries.py` exists to clean up after.
    """
    with world() as w:
        alone, _line = _uploaded(w, file_name="a.xlsx")
        # One sales order, two lines, two files: its header survives the rollback because
        # b.xlsx's row is still on it.
        mixed = w.order()
        other_product = w.product_row()
        w.line(mixed, qty_ordered="50")
        w.line(mixed, product=other_product, qty_ordered="50")
        _apply(w, sheet([
            (mixed.so_number, w.product.product_code, 10, D_OCT,
             w.warehouse.warehouse_code, ""),
        ]), file_name="a.xlsx")
        _apply(w, sheet([
            (mixed.so_number, other_product.product_code, 10, D_OCT,
             w.warehouse.warehouse_code, ""),
        ]), file_name="b.xlsx")

        headers = {
            str(row.order_inquiry_id)
            for row in _rows_of(w, "a.xlsx") + _rows_of(w, "b.xlsx")
        }
        assert len(headers) == 2, headers
        emptied = {str(row.order_inquiry_id) for row in _rows_of(w, "a.xlsx")} - {
            str(row.order_inquiry_id) for row in _rows_of(w, "b.xlsx")
        }
        kept = {str(row.order_inquiry_id) for row in _rows_of(w, "b.xlsx")}
        assert len(emptied) == 1 and len(kept) == 1, (emptied, kept)

        counts = _rollback().run(w.db, file_name="a.xlsx", apply=True)

        assert counts["rows"] == 2, counts
        assert counts["inquiries"] == 1, counts
        assert w.db.query(OrderInquiry).filter(
            OrderInquiry.id.in_(list(emptied))
        ).count() == 0, "the header the rollback emptied was left behind"
        assert w.db.query(OrderInquiry).filter(
            OrderInquiry.id.in_(list(kept))
        ).count() == 1, "a header still holding another file's rows was deleted"
        assert alone is not None


def test_ac_r_16_rows_raise_again_after_rollback():
    """AC-R-16. After the rollback the same sheet raises the same rows again: D2's
    "this line already carries a row, leave it alone" no longer stands in the way.

    Rolling back and re-uploading is the whole point of the script (owner step 1 and 2 after
    deploy), so a rollback that leaves the second upload with nothing to do would be worse
    than none at all.
    """
    with world() as w:
        order = w.order()
        line = _with_ref(w, w.line(order, qty_ordered="50"), _ref())
        po, po_line = w.po_line(qty_ordered="50")
        _names(w, po_line, line.source_ref)
        data = sheet([
            (order.so_number, w.product.product_code, 30, D_OCT,
             w.warehouse.warehouse_code, ""),
        ])

        first = _apply(w, data, file_name="a.xlsx")
        assert first["rows_raised"] == 1, first

        _rollback().run(w.db, file_name="a.xlsx", apply=True)
        again = _apply(w, data, file_name="a.xlsx")

        assert again["rows_raised"] == first["rows_raised"], again
        assert again["rows_already_raised"] == 0, again
        assert len(_rows_of(w, "a.xlsx")) == 1
        assert again["links_written"] == 1, again


def test_ac_r_17_rollback_keeps_another_feeds_claim():
    """AC-R-17. The rollback deletes the claims this upload OPENED. A claim it merely
    reused is not its to take away (security review, 15 Sep 2026).

    `claim_placed_on_po` is fill-never-repoint: at an identity `(company, so_number,
    po_number, item_code)` that already carries a claim it resolves onto the EXISTING row
    and keeps whoever stated the pairing first. So `order_inquiry_links.claim_id` routinely
    names a claim the sheet never wrote - on the 3am 14 Sep prod copy that identity is taken
    by the August `po_history` extract for 28,397 pairings - and deleting every claim a
    deleted link happens to name would erase another feed's record of a pairing that is
    still true.

    Both halves of the same rule, because both are one line of SQL apart:

    * a claim another FEED owns (`autocount` here) keeps its row and its source;
    * a claim of this feature's own source that a SURVIVING link still names - two uploads
      linking the same SO, item and purchase order share one claim - stays for that link.
    """
    with world() as w:
        order = w.order()
        line = _with_ref(w, w.line(order, qty_ordered="50"), _ref())
        po, po_line = w.po_line(qty_ordered="50")
        _names(w, po_line, line.source_ref)
        foreign = w.claim(
            order=order, core_line=line, document=po.po_number,
            po_line=po_line, source="autocount",
        )
        foreign_id = str(foreign.id)

        result = _apply(w, sheet([
            (order.so_number, w.product.product_code, 30, D_OCT,
             w.warehouse.warehouse_code, ""),
        ]), file_name="a.xlsx")

        assert result["rows_raised"] == 1, result
        rows = _rows_of(w, "a.xlsx")
        links = w.links(rows[0])
        assert len(links) == 1, _documents(links)
        assert str(links[0].claim_id) == foreign_id, (
            "the premise of this test: the link reused the claim already at that identity"
        )
        row_id = str(rows[0].id)

        counts = _rollback().run(w.db, file_name="a.xlsx", apply=True)

        assert counts["rows"] == 1, counts
        assert counts["claims"] == 0, "the rollback counted another feed's claim as its own"
        assert w.db.query(OrderInquiryRow).filter(
            OrderInquiryRow.id == row_id
        ).count() == 0
        kept = w.db.query(OrderLinkClaim).filter(OrderLinkClaim.id == foreign_id).first()
        assert kept is not None, "the rollback deleted a claim the AutoCount ingest wrote"
        assert kept.source == "autocount", kept.source

    with world() as w:
        order = w.order()
        first_line = _with_ref(
            w, w.line(order, qty_ordered="50", required_date=D_OCT), _ref()
        )
        second_line = _with_ref(
            w, w.line(order, qty_ordered="50", required_date=D_NOV), _ref()
        )
        po, first_po_line = w.po_line(qty_ordered="30")
        _names(w, first_po_line, first_line.source_ref)
        second_po_line = _sibling_po_line(
            w, po, qty_ordered="30", source_ref=f"AED_SORENTO:{_n()}:2",
            from_so_line_ref=second_line.source_ref,
        )
        first = _apply(w, sheet([
            (order.so_number, w.product.product_code, 30, D_OCT,
             w.warehouse.warehouse_code, ""),
        ]), file_name="a.xlsx")
        second = _apply(w, sheet([
            (order.so_number, w.product.product_code, 30, D_NOV,
             w.warehouse.warehouse_code, ""),
        ]), file_name="b.xlsx")

        assert first["rows_raised"] == 1 and second["rows_raised"] == 1, (first, second)
        link_a = w.links(_rows_of(w, "a.xlsx")[0])[0]
        row_b = _rows_of(w, "b.xlsx")[0]
        link_b = w.links(row_b)[0]
        shared = str(link_a.claim_id)
        assert shared == str(link_b.claim_id), (
            "the premise: one SO, one item, one purchase order is ONE claim identity"
        )

        counts = _rollback().run(w.db, file_name="a.xlsx", apply=True)

        assert counts["rows"] == 1, counts
        assert counts["claims"] == 0, (
            "the rollback took the claim b.xlsx's link still stands on"
        )
        assert w.db.query(OrderInquiryRow).filter(
            OrderInquiryRow.id == str(row_b.id)
        ).count() == 1
        assert w.db.query(OrderLinkClaim).filter(
            OrderLinkClaim.id == shared
        ).count() == 1, "b.xlsx's link lost the evidence it points at"
        w.db.refresh(link_b)
        assert str(link_b.claim_id) == shared


def test_ac_r_18_rollback_refuses_blank_file_name():
    """AC-R-18. A blank file name is REFUSED, because the bare stamp is the prefix of every
    migrated row ever raised (security review, 15 Sep 2026).

    `_stamp("")` strips back to `"Migrated from order inquiry sheet"`, which every row this
    importer has ever written begins with - including the rows of an upload that stamped no
    file name at all. Run with `--apply` on prod that is not a rollback of one upload, it is
    the deletion of the whole migration. A whitespace-only name strips to the same string.
    """
    with world() as w:
        unnamed_order = w.order()
        w.line(unnamed_order, qty_ordered="50")
        result = _apply(w, sheet([
            (unnamed_order.so_number, w.product.product_code, 30, D_OCT,
             w.warehouse.warehouse_code, ""),
        ]))
        assert result["rows_raised"] == 1, result
        bare = [
            row for row in w.db.query(OrderInquiryRow).all()
            if (row.note or "") == importer._MIGRATION_STAMP
        ]
        assert len(bare) == 1, "the premise: an upload with no file name carries the stamp alone"
        _uploaded(w, file_name="a.xlsx", product=w.product_row())
        before = w.db.query(OrderInquiryRow).count()

        for blank in ("", "   "):
            with pytest.raises(ValueError):
                _rollback().run(w.db, file_name=blank, apply=True)

        assert w.db.query(OrderInquiryRow).count() == before, (
            "a blank file name deleted rows"
        )
        assert len(_rows_of(w, "a.xlsx")) == 1


def test_ac_r_19_rollback_matches_the_whole_file_name():
    """AC-R-19. The stamp is matched as a WHOLE file name, not as a prefix of one.

    `note LIKE 'Migrated from order inquiry sheet JAN%'` takes
    `JAN - DEC 2026 ORDER.xlsx` with it, and the owner's own file is called exactly that.
    What the note legitimately carries AFTER the file name is the operator's remark and each
    link's stamp, both introduced by `"; "` - so the row's own name still matches it.
    """
    with world() as w:
        long_name = "JAN - DEC 2026 ORDER.xlsx"
        short_name = "JAN.xlsx"

        remarked = w.order()
        w.line(remarked, qty_ordered="50")
        long_result = _apply(w, sheet([
            (remarked.so_number, w.product.product_code, 30, D_OCT,
             w.warehouse.warehouse_code, "CUSTOMER HOLD"),
        ]), file_name=long_name)
        _uploaded(w, file_name=short_name, product=w.product_row())

        assert long_result["rows_raised"] == 1, long_result
        long_row = _rows_of(w, long_name)[0]
        assert (long_row.note or "").startswith(
            f"{importer._MIGRATION_STAMP} {long_name}; CUSTOMER HOLD"
        ), long_row.note
        short_row = _rows_of(w, short_name)[0]

        partial = _rollback().run(w.db, file_name="JAN", apply=True)

        assert partial == {"rows": 0, "links": 0, "claims": 0, "inquiries": 0}, (
            "a prefix of the file name matched two whole uploads"
        )
        assert w.db.query(OrderInquiryRow).filter(
            OrderInquiryRow.id.in_([str(long_row.id), str(short_row.id)])
        ).count() == 2

        named = _rollback().run(w.db, file_name=short_name, apply=True)

        assert named["rows"] == 1, named
        assert w.db.query(OrderInquiryRow).filter(
            OrderInquiryRow.id == str(short_row.id)
        ).count() == 0
        assert w.db.query(OrderInquiryRow).filter(
            OrderInquiryRow.id == str(long_row.id)
        ).count() == 1, "the other file's row went with it"

        remaining = _rollback().run(w.db, file_name=long_name, apply=True)

        assert remaining["rows"] == 1, (
            "a row carrying the operator's own remark after the stamp was not matched by "
            "its own file name"
        )
        assert w.db.query(OrderInquiryRow).filter(
            OrderInquiryRow.id == str(long_row.id)
        ).count() == 0


def test_ac_r_20_rollback_refuses_a_run_spanning_companies():
    """AC-R-20. One file name in two companies is REFUSED until it is asked for out loud.

    The script has no request and no principal, so it runs under the system scope
    (`None`, every company) - which is the only way one stamp can be found at all, and
    equally what would let one company's operator take another company's rows out without
    ever seeing them. Two companies can hold the same sales order number (AC-S1-43) and
    nothing stops two of them uploading a file called `JAN.xlsx`, so this is a real shape,
    not a hypothetical one.
    """
    with blank_session() as db:
        srt = db.execute(sa.text("select id from companies where code = 'SRT'")).scalar()
        other = _uid()
        db.execute(
            sa.text(
                "insert into companies (id, name, code, is_active) "
                "values (:id, :name, :code, true)"
            ),
            {"id": other, "name": f"{MARKER} other company", "code": f"ZZTC{_n():04d}"},
        )

        with company_scope(db, frozenset({other})):
            far = World(db, other)
            far_order = far.order()
            far.line(far_order, qty_ordered="50")
            far_result = importer.apply(
                db,
                sheet([
                    (far_order.so_number, far.product.product_code, 30, D_OCT,
                     far.warehouse.warehouse_code, ""),
                ]),
                actor=far.actor,
                file_name="a.xlsx",
            )
            assert far_result["rows_raised"] == 1, far_result

        with company_scope(db, frozenset({srt})):
            near = World(db, srt)
            near_order = near.order()
            near.line(near_order, qty_ordered="50")
            near_result = importer.apply(
                db,
                sheet([
                    (near_order.so_number, near.product.product_code, 30, D_OCT,
                     near.warehouse.warehouse_code, ""),
                ]),
                actor=near.actor,
                file_name="a.xlsx",
            )
            assert near_result["rows_raised"] == 1, near_result

        stamp = f"{importer._MIGRATION_STAMP} a.xlsx"
        with company_scope(db, None):
            def stamped() -> int:
                return (
                    db.query(OrderInquiryRow)
                    .filter(OrderInquiryRow.note.startswith(stamp, autoescape=True))
                    .count()
                )

            assert stamped() == 2, "the premise: one stamp, two companies"

            with pytest.raises(ValueError):
                _rollback().run(db, file_name="a.xlsx", apply=True)

            assert stamped() == 2, "the refused run deleted rows anyway"

            counts = _rollback().run(
                db, file_name="a.xlsx", apply=True, all_companies=True
            )

            assert counts["rows"] == 2, counts
            assert counts["inquiries"] == 2, counts
            assert stamped() == 0

    with world() as w:
        # And a run that stays inside ONE company needs nothing said: the flag answers a
        # question this upload does not raise.
        _uploaded(w, file_name="a.xlsx")

        counts = _rollback().run(w.db, file_name="a.xlsx", apply=True)

        assert counts["rows"] == 1, counts
        assert _rows_of(w, "a.xlsx") == []


# --------------------------------------------------------------------------- #
# reviewer round, 15 Sep 2026: the exact line, and refs that name too much     #
# --------------------------------------------------------------------------- #


def _sibling_po_line(
    w: World,
    po,
    *,
    qty_ordered: str,
    source_ref: str | None = None,
    from_so_line_ref: str | None = None,
    product=None,
) -> PurchaseOrderLine:
    """A SECOND line on an EXISTING purchase order.

    `World.po_line` mints a new document each time, and `po_number` is one document: two
    lines of one purchase order is the whole shape these criteria are about.
    """
    line = PurchaseOrderLine(
        id=_uid(),
        company_id=w.company_id,
        purchase_order_id=po.id,
        product_id=(product or w.product).id,
        warehouse_id=w.warehouse.id,
        qty_ordered=Decimal(qty_ordered),
        qty_received=Decimal("0"),
        expected_date=date(2026, 9, 1),
        line_status="open",
        source_ref=source_ref,
        from_so_line_ref=from_so_line_ref,
    )
    w.db.add(line)
    w.db.flush()
    return line


def _allocation(
    w: World,
    *,
    spo_number: str,
    line_number: int,
    quantity: int,
    from_po_number: str,
    from_po_line_ref: str,
) -> SPOAllocation:
    """One container of a shipping order, stating the purchase order LINE it came from.

    Built here rather than through `World.spo_allocation`, which numbers every allocation
    line 1: two containers of ONE shipping order is the shape AC-R-21 is about, and
    `(company, spo_number, spo_line_number)` is unique, so the line number has to be right
    at insert.
    """
    row = SPOAllocation(
        id=_uid(),
        company_id=w.company_id,
        spo_number=spo_number,
        spo_line_number=line_number,
        product_id=w.product.id,
        warehouse_id=w.warehouse.id,
        location_code=w.warehouse.warehouse_code,
        allocated_quantity=quantity,
        quantity_received=0,
        receipt_status="pending",
        line_status="open",
        source_system="autocount",
        issue_date=date(2026, 6, 1),
        expected_date=date(2026, 9, 1),
        supplier_id=w.supplier().id,
        from_po_number=from_po_number,
        from_po_line_ref=from_po_line_ref,
    )
    w.db.add(row)
    w.db.flush()
    return row


def test_ac_r_21_chain_walks_the_exact_po_line():
    """AC-R-21. One purchase order, two lines of the same item, one shipping order carrying
    a container for each: the quantity is owed against the container that holds THIS line.

    On the 3am 14 Sep prod copy SPO-2026/01-0140 carries five CB2154-DIY allocations from
    202511-S0097 - 300, 87, 1, 10 and 2 - each raised for a different sales order line. A
    walk that knows only the document number lands the owner's 87 on the 300 that belongs to
    somebody else, and the worklist then shows a container this order has no claim on. Every
    allocation that names a source purchase order names its LINE too
    (`spo_allocations.from_po_line_ref`, quoting that line's `source_ref`), so the finer key
    is available wherever the coarser one is.
    """
    with world() as w:
        ref = _ref()
        first_po_ref = f"AED_SORENTO:{_n()}:1"
        second_po_ref = f"AED_SORENTO:{_n()}:2"
        order = w.order()
        _with_ref(w, w.line(order, qty_ordered="100"), ref)

        po, big_line = w.po_line(qty_ordered="300")
        big_line.source_ref = first_po_ref
        w.db.flush()
        exact_line = _sibling_po_line(
            w, po, qty_ordered="87", source_ref=second_po_ref, from_so_line_ref=ref,
        )

        spo_number = f"SPO-2026/01-{_n():04d}"
        big_allocation = _allocation(
            w, spo_number=spo_number, line_number=1, quantity=300,
            from_po_number=po.po_number, from_po_line_ref=first_po_ref,
        )
        exact_allocation = _allocation(
            w, spo_number=spo_number, line_number=2, quantity=87,
            from_po_number=po.po_number, from_po_line_ref=second_po_ref,
        )
        data = sheet([
            (order.so_number, w.product.product_code, 87, D_OCT,
             w.warehouse.warehouse_code, ""),
        ])

        result = _apply(w, data)

        assert result["rows_raised"] == 1, result
        links = w.links(w.one_row())
        assert len(links) == 1, _documents(links)
        assert str(links[0].spo_allocation_id) == str(exact_allocation.id), (
            "the walk landed on the container raised for another sales order line"
        )
        assert Decimal(str(links[0].qty)) == Decimal("87")
        assert all(
            str(link.spo_allocation_id) != str(big_allocation.id) for link in links
        )
        assert str(exact_line.id) not in {str(link.po_line_id) for link in links}, (
            "the allocation covered the whole need, so the purchase order line takes none"
        )


# test_ac_r_22_ambiguous_ref_never_promotes_a_ghost (AC-R-22) retired under R4,
# PLAN-board-received-stock-own-arrival, 21 Sep 2026: its whole premise was an ambiguous
# citation "promoting" a cancelled ghost line ahead of a real one in the LINE PICK, which no
# longer reads citations at all (`_pick_lines_by_date_order` reads only `required_date` /
# `delivery_date`). Its own "real" line was also CLOSED, so under AC-S4-3 neither candidate
# in this fixture is open and the row is refused `no_line_for_item` regardless of any
# citation.


# AC-R-23 ("the citation a restatement lends has to reach the LINE PICK, not just the
# pairing") is DELETED here (issue #915, plan section 8.3): its whole premise was the
# citation reaching the line pick, and 8.1 change 1 removes `_Match.cited` and the citation
# pre-pass entirely, so there is no lent citation left to reach anything. Nothing but the
# removed source was under test.


def test_ac_r_24_ref_shared_by_two_orders_pairs_nothing():
    """AC-R-24. An ordinal on two sales orders is not a statement about either of them.

    Silence is the only honest answer: a purchase order line carrying `'1'` in
    `from_so_line_ref` would otherwise pair itself to all 3,364 lines that carry `'1'`, and
    the operator would get a worklist full of confident links to documents nobody bought for
    them. The row is still raised - the instruction is real - it simply carries no pairing.
    """
    with world() as w:
        order = w.order()
        _with_ref(w, w.line(order, qty_ordered="50"), "1")
        twin = w.order()
        _with_ref(w, w.line(twin, qty_ordered="50"), "1")
        _po, po_line = w.po_line(qty_ordered="50")
        _names(w, po_line, "1")
        data = sheet([
            (order.so_number, w.product.product_code, 30, D_OCT,
             w.warehouse.warehouse_code, ""),
        ])

        result = _apply(w, data)

        assert result["rows_raised"] == 1, result
        row = w.one_row()
        assert w.links(row) == [], "an ambiguous ref paired the row anyway"
        assert result["links_written"] == 0
        assert result["links_from_autocount"] == 0


# --------------------------------------------------------------------------- #
# follow-up, 14 Sep evening: a line the book BOUGHT for outranks one it did not #
# --------------------------------------------------------------------------- #


# test_ac_r_32_bought_line_outranks_unbought_without_citation (AC-R-32) and
# test_ac_r_33_bought_beats_open_but_not_cancelled (AC-R-33) retired under R4,
# PLAN-board-received-stock-own-arrival, 21 Sep 2026: both pinned the "bought" term of the
# line-pick rank (`_rank_for`'s `plan.bought_refs` check), which no longer runs -
# `_pick_lines_by_date_order` never consults which document bought for a line, only
# `required_date` / `delivery_date` order. AC-R-33's second scenario (a cancelled line the
# book bought for must not outrank a real open line) is superseded even more directly: a
# cancelled line is never a candidate at all now (AC-S4-3), not merely outranked.


# --------------------------------------------------------------------------- #
# 7.2 and 7.4: no double count, and the line's own delivery date              #
# --------------------------------------------------------------------------- #


def test_ac_r_34_po_line_capacity_excludes_what_its_own_spo_carries():
    """AC-R-34. A purchase order line and the shipping order it BECAME are one supply, not
    two (`PLAN-scm-oi-sheet-pairing-repair.md` 7.2, owner 14 Sep evening).

    Measured on the 3am copy after the upload: 555 rows carry a link to a PO line AND a link
    to that same line's own allocation, 23,187 units counted twice. SO368872 / SRTWC286-SH is
    the owner's example - 62 on PO 202510-S0078 and 62 on SPO-2026/04-0043, which is that
    very line shipped. D10 links the shipping order first and the purchase order line "for
    the remainder", but the remainder was computed against the PO line's whole `qty_ordered`,
    so the same 62 units were owed twice.

    The owner: "we definitely cannot double count, but by this linking it helps us to know
    the PO and SPO corresponding to this order inquiry" - hence the FE half of 7.2, which
    keeps both numbers on screen without a second link.
    """
    with world() as w:
        ref = _ref()
        po_line_ref = f"AED_SORENTO:{_n()}:62"
        order = w.order()
        _with_ref(w, w.line(order, qty_ordered="400"), ref)
        po, po_line = w.po_line(qty_ordered="62")
        po_line.source_ref = po_line_ref
        w.db.flush()
        _names(w, po_line, ref)
        shipped = _allocation(
            w, spo_number=f"SPO-2026/04-{_n():04d}", line_number=1, quantity=62,
            from_po_number=po.po_number, from_po_line_ref=po_line_ref,
        )
        data = sheet([
            (order.so_number, w.product.product_code, 364, D_OCT,
             w.warehouse.warehouse_code, ""),
        ])

        _apply(w, data)

        links = w.links(w.one_row())
        assert len(links) == 1, [
            (link.document, str(link.qty)) for link in links
        ]
        assert str(links[0].spo_allocation_id) == str(shipped.id)
        assert Decimal(str(links[0].qty)) == Decimal("62")
        assert all(
            str(link.po_line_id or "") != str(po_line.id) for link in links
        ), "the purchase order line was linked for units already on its own ship"

    with world() as w:
        # The same, but the ship carries only 40 of the 62 the order bought: the purchase
        # order line answers for the 22 that have NOT sailed, and not one unit more.
        ref = _ref()
        po_line_ref = f"AED_SORENTO:{_n()}:62"
        order = w.order()
        _with_ref(w, w.line(order, qty_ordered="400"), ref)
        po, po_line = w.po_line(qty_ordered="62")
        po_line.source_ref = po_line_ref
        w.db.flush()
        _names(w, po_line, ref)
        shipped = _allocation(
            w, spo_number=f"SPO-2026/04-{_n():04d}", line_number=1, quantity=40,
            from_po_number=po.po_number, from_po_line_ref=po_line_ref,
        )
        data = sheet([
            (order.so_number, w.product.product_code, 364, D_OCT,
             w.warehouse.warehouse_code, ""),
        ])

        _apply(w, data)

        links = w.links(w.one_row())
        assert [Decimal(str(link.qty)) for link in links] == [
            Decimal("40"), Decimal("22"),
        ], [(link.document, str(link.qty)) for link in links]
        assert str(links[0].spo_allocation_id) == str(shipped.id)
        assert str(links[1].po_line_id) == str(po_line.id)


def test_ac_r_37_row_takes_the_sheets_own_delivery_date():
    """AC-R-37. The raised row carries the SHEET's own date, and the sales order line's
    only when the sheet states none (18 Sep 2026, reversing 7.4's rule - owner: "we should
    have followed the sheet's date").

    SO314593 on prod: the sheet said 182 @ 1.9.2026 and the line's own open AutoCount lines
    are 220 @ 01/03/2027; 7.4 wrote the line's date onto every migrated row, so the
    worklist, the month grouping and the export all read a delivery purchasing was not
    actually working to. The sheet's date still decides which line the row matches and
    whether two rows restate one instruction; that reading is unchanged.
    """
    sheet_date = date(2026, 1, 5)
    line_date = date(2030, 1, 1)
    with world() as w:
        order = w.order()
        line = w.line(order, qty_ordered="50", required_date=line_date)
        data = sheet([
            (order.so_number, w.product.product_code, 30, sheet_date,
             w.warehouse.warehouse_code, ""),
        ])

        result = _apply(w, data)

        assert result["rows_raised"] == 1, result
        row = w.one_row()
        assert str(row.so_line_id) == str(w.mirror_of(line).id)
        assert row.delivery_date == sheet_date, (
            "the row reports a delivery the sheet did not state"
        )

    with world() as w:
        # An ORDER BACK row takes the LINE's date (unaffected by this reversal): the words
        # in the date cell are still never a date, so the sheet states none, and `verb` is
        # what says the quantity is owed against something already ordered.
        order = w.order()
        w.line(order, qty_ordered="50", required_date=line_date)
        data = sheet([
            (order.so_number, w.product.product_code, 30, "ORDER BACK",
             w.warehouse.warehouse_code, ""),
        ])

        result = _apply(w, data)

        assert result["rows_raised"] == 1, result
        row = w.one_row()
        assert row.verb == IV_ORDER_BACK
        assert row.delivery_date == line_date

    with world() as w:
        # The sheet states a date and the line has none to fall back to anyway: the
        # sheet's date stands either way.
        order = w.order()
        w.line(order, qty_ordered="50", required_date=None)
        data = sheet([
            (order.so_number, w.product.product_code, 30, sheet_date,
             w.warehouse.warehouse_code, ""),
        ])

        result = _apply(w, data)

        assert result["rows_raised"] == 1, result
        assert w.one_row().delivery_date == sheet_date


def test_ac_r_34b_sibling_lines_shipment_does_not_zero_the_named_line():
    """AC-R-34, second case (reviewer round on #904). The deduction is per LINE, and a line
    that has shipped nothing must not be emptied by its sibling's containers.

    One purchase order, two lines of the same item: line A (100) shipped in full, line B (50)
    the one that names this row's sales order line. `_less_own_shipments` looks for the
    allocations quoting B's own `source_ref` and, finding none, falls back to the document's
    whole shipment set - which is A's 100. B's capacity goes to zero and the row is raised
    with nothing at all, when the purchase order the book raised FOR IT has 50 sitting on it.

    The fallback is right only where the feed named no line; where it named a different one,
    that is a statement about the sibling, not silence.
    """
    with world() as w:
        ref = _ref()
        shipped_ref = f"AED_SORENTO:{_n()}:A"
        named_ref = f"AED_SORENTO:{_n()}:B"
        order = w.order()
        _with_ref(w, w.line(order, qty_ordered="400"), ref)

        po, shipped_line = w.po_line(qty_ordered="100")
        shipped_line.source_ref = shipped_ref
        w.db.flush()
        named_line = _sibling_po_line(
            w, po, qty_ordered="50", source_ref=named_ref, from_so_line_ref=ref,
        )
        allocation = _allocation(
            w, spo_number=f"SPO-2026/05-{_n():04d}", line_number=1, quantity=100,
            from_po_number=po.po_number, from_po_line_ref=shipped_ref,
        )
        # Somebody else already holds every unit of that shipment, so it has nothing to
        # give this row - and the row's own purchase order line is all that is left.
        _link_row(w, allocation=allocation, document=allocation.spo_number, qty="100")

        data = sheet([
            (order.so_number, w.product.product_code, 50, D_OCT,
             w.warehouse.warehouse_code, ""),
        ])

        result = _apply(w, data)

        assert result["rows_raised"] == 1, result
        # `_link_row` seeded a board row of its own to occupy the shipment, so the migrated
        # row is picked out by its stamp rather than by being the only one there.
        mine = [
            r for r in w.rows()
            if (r.note or "").startswith(importer._MIGRATION_STAMP)
        ]
        assert len(mine) == 1, [r.item_code for r in mine]
        links = w.links(mine[0])
        assert len(links) == 1, [
            (link.document, str(link.qty)) for link in links
        ]
        assert str(links[0].po_line_id) == str(named_line.id), (
            "the line the book raised for this row was emptied by its SIBLING's shipment"
        )
        assert Decimal(str(links[0].qty)) == Decimal("50")
        assert result["links_from_autocount"] == 1


def _committed(db, product_id: str, *, planned: bool) -> Decimal:
    """The project leg for one product, as the VIEW says it and as the PLAN's own SELECT
    does (the shape `tests/test_order_inquiry_handshake.py::_project_committed` uses).

    Keyed on the product because this runs on the REAL database, where the tables are not
    empty: the seeded product is what makes the figure this test's own.
    """
    from app.services.scm import demand

    if planned:
        sql = (
            "SELECT COALESCE(SUM(project_committed), 0) FROM ("
            f"{demand.horizon_committed_select_sql()}) cv WHERE cv.product_id = :pid"
        )
        params = {"pid": str(product_id), "horizon": None, "horizon_start": None}
    else:
        sql = (
            "SELECT COALESCE(SUM(project_committed), 0) FROM scm.committed_v "
            "WHERE product_id = :pid"
        )
        params = {"pid": str(product_id)}
    return Decimal(str(db.execute(sa.text(sql), params).scalar() or 0))


def _remaining_open(db, row) -> Decimal:
    """The worklist's Remaining column for this row's sales order line."""
    from app.services.order_inquiry_worklist_service import OrderInquiryWorklistService

    flow = OrderInquiryWorklistService(db)._quantity_flow_by_so_line([row])
    return Decimal(str((flow.get(str(row.so_line_id)) or {}).get("remaining", 0)))


def _seed_partly_delivered(w, *, delivered: str):
    """One migrated row of 364 on a line that has already delivered most of itself, with
    62 of the rest on a purchase order line that NAMES it (the ref, source 1 - issue #915
    retired the remark as a link source)."""
    order = w.order()
    line = _with_ref(w, w.line(order, qty_ordered="364", qty_delivered=delivered), _ref())
    # A document number from a year no real book holds. This test runs on the REAL
    # database (the view), which on a developer's machine is a copy of production and
    # already carries every `2026MM-Snnnn` the parent file's `_po_number()` can mint -
    # `uq_purchase_orders_company_po_number` then aborts the seed, on that machine only.
    _po, po_line = w.po_line(qty_ordered="62", number=f"209901-S{_n():04d}")
    _names(w, po_line, line.source_ref)
    result = _apply(w, sheet([
        (order.so_number, w.product.product_code, 364, D_OCT,
         w.warehouse.warehouse_code, ""),
    ]))
    assert result["rows_raised"] == 1, result
    assert result["links_written"] == 1, result
    # Read by THIS test's own item code: on the real database the tables are not empty.
    rows = (
        w.db.query(OrderInquiryRow)
        .filter(OrderInquiryRow.item_code == w.product.product_code)
        .all()
    )
    assert len(rows) == 1, [r.item_code for r in rows]
    row = rows[0]
    assert Decimal(str(sum(Decimal(str(l.qty)) for l in w.links(row)))) == Decimal("62")
    return line, row


def test_ac_r_38a_the_remaining_column_is_capped():
    """REWRITTEN (R1, owner 23 Sep 2026, `PLAN-oi-order-rows-uncapped.md`, SO421985): the
    7.3 cap this test (AC-R-38) used to pin is RETIRED for every verb - the Remaining
    column is now the row's own qty less what is linked, `364 - 62 = 302`, whatever the
    sales order line's own delivered figure reads. Name kept (do not delete, same
    treatment as AC-OB-5/AC-R-36's own rewrites); the two blocks below now prove
    delivery has NO say at all: 352 delivered (12 outstanding) and 300 delivered (64
    outstanding) must read the SAME 302.

    On the REAL database (`pg_session`): `scm.committed_v`, which the sibling tests read, is
    installed by a migration and the blank scratch schema has no view, so all three share the
    one substrate and the one seed.
    """
    with world(pg_session) as w:
        line, row = _seed_partly_delivered(w, delivered="352")

        assert Decimal(str(line.qty_ordered)) - Decimal(str(line.qty_delivered)) == (
            Decimal("12")
        ), "twelve of the 364 are still owed"
        assert _remaining_open(w.db, row) == Decimal("302"), (
            "the cap is retired (R1) - Remaining is the row's own qty less what is linked"
        )

    with world(pg_session) as w:
        _line, row = _seed_partly_delivered(w, delivered="300")

        assert _remaining_open(w.db, row) == Decimal("302"), (
            "a different delivered figure must not move Remaining any more"
        )


def test_ac_r_38b_committed_v_is_capped():
    """REWRITTEN (R1, 23 Sep 2026), sibling of the rewrite above: `scm.committed_v` now
    counts the row's own qty less what is linked in full, `364 - 62 = 302`, regardless of
    delivery - both 352 delivered and 300 delivered must read 302."""
    with world(pg_session) as w:
        _line, _row = _seed_partly_delivered(w, delivered="352")

        assert _committed(w.db, w.product.id, planned=False) == Decimal("302"), (
            "the cap is retired (R1) - the view counts what is still linked short of the row"
        )

    with world(pg_session) as w:
        _line, _row = _seed_partly_delivered(w, delivered="300")

        assert _committed(w.db, w.product.id, planned=False) == Decimal("302"), (
            "a different delivered figure must not move the view's figure any more"
        )


def test_ac_r_38c_the_plans_own_select_is_capped():
    """REWRITTEN (R1, 23 Sep 2026), sibling of the rewrites above:
    `demand.horizon_committed_select_sql` - what a reorder run actually buys from - now
    proposes the row's own qty less what is linked in full, `364 - 62 = 302`, whatever
    the line's own delivered figure reads. Known consequence (R1's own words): the plan
    now proposes 302 of something twelve of which is owed, the trade the owner took."""
    with world(pg_session) as w:
        _line, _row = _seed_partly_delivered(w, delivered="352")

        assert _committed(w.db, w.product.id, planned=True) == Decimal("302"), (
            "the cap is retired (R1) - the reorder run buys the row's own qty less links"
        )

    with world(pg_session) as w:
        _line, _row = _seed_partly_delivered(w, delivered="300")

        assert _committed(w.db, w.product.id, planned=True) == Decimal("302"), (
            "a different delivered figure must not move the plan's own figure any more"
        )


# --------------------------------------------------------------------------- #
# follow-up, 15 Sep: the remark leaves the match, exact date wins, a cancelled  #
# purchase order line is never a target (plan section 8, issue #915)          #
# --------------------------------------------------------------------------- #


def test_ac_r_39_remark_does_not_pick_the_line():
    """AC-R-39. The remark no longer picks the line, not even to choose between two that
    fit.

    Two open lines of one item on one order: A dated the sheet's own date and named by no
    document, B dated a month later and named by purchase order P. Under #904 (before this
    lane) P's citation was term 1 of `_rank_for` and outranked everything, including the
    exact date match, so the row landed on B. The owner: "let's ignore the sheet remark at
    all."
    """
    with world() as w:
        order = w.order()
        a = w.line(order, qty_ordered="50", required_date=D_OCT)
        b = _with_ref(
            w, w.line(order, qty_ordered="50", required_date=D_NOV), _ref()
        )
        cited, cited_line = w.po_line(qty_ordered="50")
        _names(w, cited_line, b.source_ref)
        data = sheet([
            (order.so_number, w.product.product_code, 30, D_OCT,
             w.warehouse.warehouse_code, cited.po_number),
        ])

        result = _apply(w, data)

        assert result["rows_raised"] == 1, result
        row = w.one_row()
        mirror_a = w.mirror_of(a)
        assert mirror_a is not None
        assert str(row.so_line_id) == str(mirror_a.id), (
            "the remark still picked the line the cited purchase order names, over the "
            "line whose own date matches the sheet"
        )
        mirror_b = w.mirror_of(b)
        assert mirror_b is None or str(row.so_line_id) != str(mirror_b.id)
        assert w.links(row) == [], (
            "the row landed on the unnamed line yet still carried a link"
        )


def test_ac_r_40_remark_does_not_link():
    """AC-R-40. The remark does not link either, even where the cited document has room.

    One open line named by no document; purchase order P has a free line of the item with
    capacity; the sheet row cites P. The row is raised with NO link, `links_written` 0,
    `documents_not_linkable` empty (source 3 is gone, so nothing is ever reported against
    it - `_pair` loses source 3 and the `_purchase_side` read entirely, 8.1 change 1), and
    the note still ends with the operator's own remark text (AC-S1-28 kept, `_note_for` is
    untouched).
    """
    with world() as w:
        order = w.order()
        w.line(order, qty_ordered="50")
        cited, _cited_line = w.po_line(qty_ordered="50")
        data = sheet([
            (order.so_number, w.product.product_code, 30, D_OCT,
             w.warehouse.warehouse_code, cited.po_number),
        ])

        result = _apply(w, data)

        assert result["rows_raised"] == 1, result
        row = w.one_row()
        assert w.links(row) == [], "the remark linked the row anyway"
        assert result["links_written"] == 0, result
        assert result["documents_not_linkable"] == [], result
        assert row.note.endswith(cited.po_number), row.note


# test_ac_r_41_exact_date_beats_bought_line (AC-R-41) and
# test_ac_r_42_bought_decides_when_no_line_matches_sheet_date (AC-R-42) retired under R4,
# PLAN-board-received-stock-own-arrival, 21 Sep 2026: both pinned the ordering between the
# "exact date" and "bought" terms of `_rank_for`, neither of which the date-order pick
# consults any more. Both fixtures also seed only CLOSED lines (fully delivered), so under
# AC-S4-3 there is no open candidate in either order at all and the row is refused
# `no_line_for_item` regardless of date or citation - the same fully-delivered-order
# collision reported in the tester's handback for
# `test_project_order_inquiry_import_migration.py`.


def _cascade_world(w: World):
    """The SO388822 shape (plan section 8 opening story): three dated lines of ONE order,
    the first named by nothing, the second and third each named by a purchase order line
    that one shipping order shipped in full, and an open balance line dated 2030-01-01 that
    must never receive a row. Three sheet rows, dated L1/L2/L3, carry DIFFERENT quantities
    (10/20/30) rather than the UAC's "equal quantity" - the LINES are equal (50 each), and
    giving the rows distinct quantities is what lets the test tell which line each one
    landed on: `delivery_date` always reads back the LINE's own required date once a row is
    raised (7.4), whichever line a row ends up on, so it cannot testify to a cascade on its
    own.
    """
    d1, d2, d3 = date(2026, 3, 31), date(2026, 4, 14), date(2026, 4, 28)
    order = w.order()
    l1 = w.line(order, qty_ordered="50", required_date=d1)
    l2 = _with_ref(w, w.line(order, qty_ordered="50", required_date=d2), _ref())
    l3 = _with_ref(w, w.line(order, qty_ordered="50", required_date=d3), _ref())
    w.line(order, qty_ordered="1000", required_date=date(2030, 1, 1))  # the balance line

    po, po_line2 = w.po_line(qty_ordered="50")
    po_line2.source_ref = f"AED_SORENTO:{_n()}:2"
    w.db.flush()
    _names(w, po_line2, l2.source_ref)
    po_line3 = _sibling_po_line(
        w, po, qty_ordered="50", source_ref=f"AED_SORENTO:{_n()}:3",
        from_so_line_ref=l3.source_ref,
    )
    spo_number = f"SPO-2026/04-{_n():04d}"
    alloc2 = _allocation(
        w, spo_number=spo_number, line_number=1, quantity=50,
        from_po_number=po.po_number, from_po_line_ref=po_line2.source_ref,
    )
    alloc3 = _allocation(
        w, spo_number=spo_number, line_number=2, quantity=50,
        from_po_number=po.po_number, from_po_line_ref=po_line3.source_ref,
    )
    data = sheet([
        (order.so_number, w.product.product_code, 10, d1, w.warehouse.warehouse_code, ""),
        (order.so_number, w.product.product_code, 20, d2, w.warehouse.warehouse_code, ""),
        (order.so_number, w.product.product_code, 30, d3, w.warehouse.warehouse_code, ""),
    ])
    return order, (l1, l2, l3), (alloc2, alloc3), data


def test_ac_r_43_no_cascade_across_dated_lines():
    """AC-R-43. No cascade: promoting bought over exact-date pushed a whole sheet's rows off
    their own dated lines, one delivery at a time - this is the SO388822 story plan section 8
    opens with. Each row must land on its OWN dated line, not slide onto a bought neighbour.
    """
    with world() as w:
        _order, (l1, l2, l3), (alloc2, alloc3), data = _cascade_world(w)

        result = _apply(w, data)

        assert result["rows_raised"] == 3, result
        mirror1, mirror2, mirror3 = w.mirror_of(l1), w.mirror_of(l2), w.mirror_of(l3)
        assert mirror1 is not None and mirror2 is not None and mirror3 is not None
        by_line: dict = {}
        for row in w.rows():
            by_line.setdefault(str(row.so_line_id), []).append(row)

        row1 = by_line.get(str(mirror1.id)) or []
        row2 = by_line.get(str(mirror2.id)) or []
        row3 = by_line.get(str(mirror3.id)) or []
        assert [Decimal(str(r.qty)) for r in row1] == [Decimal("10")], (
            "L1's own row (named by nothing) did not land on L1"
        )
        assert [Decimal(str(r.qty)) for r in row2] == [Decimal("20")], (
            "L2's own row did not land on L2"
        )
        assert [Decimal(str(r.qty)) for r in row3] == [Decimal("30")], (
            "L3's own row did not land on L3"
        )
        assert w.links(row1[0]) == [], "L1, named by nothing, was linked anyway"
        links2 = w.links(row2[0])
        assert len(links2) == 1 and str(links2[0].spo_allocation_id) == str(alloc2.id), (
            _documents(links2)
        )
        links3 = w.links(row3[0])
        assert len(links3) == 1 and str(links3[0].spo_allocation_id) == str(alloc3.id), (
            _documents(links3)
        )
        assert all(row.delivery_date != date(2030, 1, 1) for row in w.rows()), (
            "a raised row carried the balance line's 2030 date"
        )


def test_ac_r_44_cancelled_po_line_never_a_target():
    """AC-R-44. A cancelled purchase order line is never a target, from any source - and the
    exclusion is per LINE, not per document.

    (a) A cancelled purchase order line X names a sales order line: X links nothing, and
    the line it names does NOT rank as "bought" because of X - a competing line nothing
    names, dated to match the sheet, wins instead.
    (c) A NON-cancelled sibling line of the same purchase order still links a different
    row in the SAME run, so the exclusion does not poison the whole document.
    """
    with world() as w:
        order = w.order()
        other_product = w.product_row()
        sheet_date = date(2026, 6, 15)  # matches neither candidate below
        never_named = w.line(order, qty_ordered="50", required_date=date(2026, 1, 10))
        named_by_cancelled = _with_ref(
            w, w.line(order, qty_ordered="50", required_date=date(2026, 8, 20)), _ref(),
        )
        linked_line = _with_ref(
            w,
            w.line(order, product=other_product, qty_ordered="50", required_date=D_OCT),
            _ref(),
        )

        po, cancelled_line = w.po_line(qty_ordered="50", line_status="cancelled")
        _names(w, cancelled_line, named_by_cancelled.source_ref)
        sibling_line = _sibling_po_line(
            w, po, qty_ordered="50", source_ref=f"AED_SORENTO:{_n()}:S",
            from_so_line_ref=linked_line.source_ref, product=other_product,
        )

        data = sheet([
            (order.so_number, w.product.product_code, 30, sheet_date,
             w.warehouse.warehouse_code, ""),
            (order.so_number, other_product.product_code, 20, D_OCT,
             w.warehouse.warehouse_code, ""),
        ])

        result = _apply(w, data)

        assert result["rows_raised"] == 2, result
        by_line: dict = {}
        for row in w.rows():
            by_line.setdefault(str(row.so_line_id), []).append(row)

        mirror_never = w.mirror_of(never_named)
        mirror_named = w.mirror_of(named_by_cancelled)
        mirror_linked = w.mirror_of(linked_line)
        assert mirror_never is not None and str(mirror_never.id) in by_line, (
            "a cancelled purchase order line still ranked the line it names as bought"
        )
        assert mirror_named is None or str(mirror_named.id) not in by_line
        assert w.links(by_line[str(mirror_never.id)][0]) == [], (
            "the cancelled purchase order line linked the row anyway"
        )
        assert mirror_linked is not None
        linked_rows = by_line.get(str(mirror_linked.id)) or []
        assert len(linked_rows) == 1, "the second row never even landed on its own line"
        links = w.links(linked_rows[0])
        assert len(links) == 1 and str(links[0].po_line_id) == str(sibling_line.id), (
            "a cancelled sibling poisoned the whole purchase order, not just its own line"
        )

    with world() as w:
        # (b) A claim naming the cancelled line links nothing either.
        order = w.order()
        line = w.line(order, qty_ordered="50")
        po, cancelled_line = w.po_line(qty_ordered="50", line_status="cancelled")
        w.claim(
            order=order, core_line=line, document=po.po_number,
            po_line=cancelled_line, source="autocount",
        )
        data = sheet([
            (order.so_number, w.product.product_code, 30, D_OCT,
             w.warehouse.warehouse_code, ""),
        ])

        result = _apply(w, data)

        assert result["rows_raised"] == 1, result
        row = w.one_row()
        assert w.links(row) == [], (
            "an autocount claim naming a cancelled purchase order line still linked it"
        )
        assert result["links_written"] == 0, result


def test_ac_r_45_result_contract_is_whole():
    """AC-R-45. The result keeps every key AC-S1-22 named, over the AC-R-43 world, and
    `links_from_autocount` equals `links_written` now that the citation is not a source of
    any link at all."""
    with world() as w:
        _order, _lines, _allocs, data = _cascade_world(w)

        result = w.preview(data)

        assert set(result) == RESULT_KEYS, sorted(set(result) ^ RESULT_KEYS)
        assert result["links_from_autocount"] == result["links_written"], result


def test_ac_r_46_undated_row_does_not_prefer_an_undated_line():
    """AC-R-46 (reviewer finding, `PLAN-scm-oi-sheet-pairing-repair.md` section 8.1 item 2b,
    issue #915). An ORDER BACK row's `delivery_date` is None - the words in the date cell
    are never a date - and `_rank_for`'s date term reads `line.required_date == wanted`
    with no guard against BOTH sides being None. An undated line then ties that term with
    the row for the wrong reason (the sheet stated no date at all, not "the same date as
    this undated line") and wins ahead of a line the book actually bought for and shipped
    in full.

    Two lines of one item on one order: L1 undated and named by nothing, L2 dated and
    named by a purchase order line that a shipping order shipped in full. One ORDER BACK
    row for the item. The row must land on L2, take the shipment, and report L2's own
    date - not None.
    """
    with world() as w:
        order = w.order()
        l1 = w.line(order, qty_ordered="50", required_date=None)
        l2 = _with_ref(
            w, w.line(order, qty_ordered="50", required_date=date(2026, 11, 1)), _ref()
        )
        po, po_line = w.po_line(qty_ordered="50")
        _names(w, po_line, l2.source_ref)
        # The allocation names the purchase order LINE, as AutoCount's always do
        # (OI-SPO-EXACT: no walk by document number alone).
        po_line.source_ref = f"AED_SORENTO:{_n()}:PO"
        w.db.flush()
        allocation = w.spo_allocation(
            quantity=50, from_po_number=po.po_number,
            from_po_line_ref=po_line.source_ref,
        )
        data = sheet([
            (order.so_number, w.product.product_code, 30, "ORDER BACK",
             w.warehouse.warehouse_code, ""),
        ])

        result = _apply(w, data)

        assert result["rows_raised"] == 1, result
        row = w.one_row()
        mirror_l2 = w.mirror_of(l2)
        assert mirror_l2 is not None
        assert str(row.so_line_id) == str(mirror_l2.id), (
            "an undated line tied the date term with the ORDER BACK row's own lack of a "
            "date, and won ahead of the line the book bought for and shipped in full"
        )
        mirror_l1 = w.mirror_of(l1)
        assert mirror_l1 is None or str(row.so_line_id) != str(mirror_l1.id)
        links = w.links(row)
        assert len(links) == 1, _documents(links)
        assert str(links[0].spo_allocation_id) == str(allocation.id)
        assert row.delivery_date == l2.required_date, (
            "the raised row's delivery date is not the line it actually landed on"
        )


# ---------------------------------------------------------------------------
# AC-RL-14 (writer 2 of 2, `PLAN-oi-replan-received-links.md` S2): the sheet
# importer's re-upload must not disturb a redirected row or its replacement.
# ---------------------------------------------------------------------------


def test_sheet_reupload_skips_redirected_line():
    """A line already carries TWO rows after a replan - the redirected history row and
    its fresh replacement - and a re-upload of the same sheet must still read the line
    as already raised, writing zero new links for either row. `_already_raised` only
    asks whether a non-cancelled row already exists on the mirror line, so this ought
    to hold with no change - pinned here as the regression guard AC-RL-14 names."""
    with world() as w:
        order = w.order()
        line = w.line(order, qty_ordered="220")
        ProjectSOAdoptionService(w.db).adopt(str(order.id), w.actor)
        mirror = w.mirror_of(line)

        redirected = w.board_row(mirror, qty="182")
        redirected.redirected_to_pool = True
        redirected.note = "SPO-2026/01-0143 received 19 Jan 2026, released at revision 4"
        replacement = w.board_row(mirror, qty="220")
        w.db.commit()

        data = sheet([
            (order.so_number, w.product.product_code, 220, D_OCT,
             w.warehouse.warehouse_code, ""),
        ])
        result = w.apply(data)

        assert result["rows_already_raised"] == 1, result
        assert result["rows_raised"] == 0, result
        assert w.links(redirected) == []
        assert w.links(replacement) == []
        w.db.refresh(redirected)
        w.db.refresh(replacement)
        assert redirected.redirected_to_pool is True
        # Set comparison, not order: `board_row`'s two inserts share one frozen `now()`
        # (Postgres freezes it per transaction), so `w.rows()`'s own `created_at, id`
        # ordering ties on the timestamp and falls back to the two rows' RANDOM ids -
        # asserting a fixed order here would fail on nothing but UUID luck.
        assert {str(r.id) for r in w.rows()} == {str(redirected.id), str(replacement.id)}
