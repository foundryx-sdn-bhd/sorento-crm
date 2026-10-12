"""RED tests for hiding retired SPO allocation lines (AC-H1..AC-H6, AC-H8, AC-H10..AC-H14, AC-H17, AC-H19).

UAC: documentation/plans/autocount/hide-retired-spo-lines-acceptance-criteria.md
PLAN: documentation/plans/autocount/PLAN-hide-retired-spo-lines.md

Written test-first (Phase 2), before `spo_supply.visible_line_clauses()` exists.
Contract this suite pins:

- `app.services.scm.spo_supply.visible_line_clauses()` - one predicate: a line is
  VISIBLE unless `retired_at IS NOT NULL AND coalesce(quantity_received, 0) = 0`
  (R1/R2). A retired line that carries a receipt (R2) or a line closed for any
  other reason (fully received, R1) stays visible.
- Every DISPLAY reader in `app.services.procurement_service` applies it:
  `list_allocations`, `list_documents` (+ its `total_allocated`/`total_received`
  rollups), `get_document` (+ its own rollups) (R4, R6).
- A read that resolves an allocation BY ITS OWN ID - here, a GRN's picking line
  pointing at a retired allocation - is NOT filtered (R3).
- Line balance follows outstanding status, hidden or not: a line that is not
  outstanding reads `balance == 0` (R5), so the grid can never disagree with the
  document header again.

Substrate reused from `tests.scm.test_spo_allocation_documents` (the `scm_app`
Postgres-savepoint fixture, its `_client`/`_chain`/`_product` catalogue helpers
and `DOCUMENTS_URL`) rather than re-copied, per the tester brief. Allocation rows
here carry `retired_at`/`source_system`, which that module's own `_line` helper
does not accept, so this file adds its own `_alloc`.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from sqlalchemy import and_, text
from sqlalchemy.orm import Session

from app.models.inventory import Warehouse
from app.models.procurement import (
    InboundShipment,
    InboundShipmentLine,
    PickingHeader,
    PickingLine,
    SPOAllocation,
)
from app.models.product import Product, ProductCategory, UnitOfMeasure

from tests._pg_fixture import blank_session, unique_code
from tests.scm.test_spo_allocation_documents import (
    DOCUMENTS_URL,
    _chain,
    _client,
    _product,
    _supplier,
    _u,
)

# This suite lives in `tests/` rather than `tests/scm/`, so the SCM conftest is not
# auto-loaded for it - `scm_app` is imported by name (same pattern as
# `tests/test_project_order_inquiry_import_cs_handover.py` and
# `tests/test_incoming_stock_draft_exclusion.py`) rather than a second savepoint
# fixture maintained twice.
from tests.scm.conftest import requires_pg, scm_app  # noqa: F401

pytestmark = requires_pg

LIST_URL = "/api/v1/procurement/spo-allocations/"
GRN_URL = "/api/v1/procurement/grn"
PACKING_LISTS_URL = "/api/v1/procurement/packing-lists"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _alloc(
    db: Session,
    *,
    spo_number: str,
    line_no: int,
    product: Product,
    allocated: int = 10,
    received: int = 0,
    receipt_status: str = "pending",
    line_status: str = "open",
    retired_at: datetime | None = None,
    source_system: str | None = "autocount",
    stated_received: int | None = None,
    supplier_id: str | None = None,
) -> SPOAllocation:
    """One `spo_allocations` row, extended with `retired_at`/`source_system`/
    `stated_received`/`supplier_id` - columns `test_spo_allocation_documents._line`
    does not carry. `source_system="autocount"` by default so `list_allocations`'
    GRN-computed-receipt recompute (`_receipt_is_computed`, triggered for
    `source_system in {None, 'scm_spo_history'}`) never overwrites the seeded
    `quantity_received` out from under an assertion here.
    """
    allocation = SPOAllocation(
        id=_u(),
        spo_number=spo_number,
        spo_line_number=line_no,
        product_id=product.id,
        allocated_quantity=allocated,
        quantity_received=received,
        receipt_status=receipt_status,
        line_status=line_status,
        retired_at=retired_at,
        source_system=source_system,
        stated_received=stated_received,
        supplier_id=supplier_id,
    )
    db.add(allocation)
    db.flush()
    return allocation


# =================================================================================== #
# AC-H1 (R1): a retired line with no receipt is hidden
# =================================================================================== #


class TestAcH1HidesRetiredLineWithNoReceipt:
    def test_document_hides_the_retired_zero_receipt_line(self, scm_app):
        """AC-H1. One open line + one retired line (received 0): `get_document`
        returns only the open line; `total_allocated` counts the open line only;
        Balance is unchanged.

        RED today: `get_document` applies no retirement filter at all, so both
        lines come back, `len(lines) == 1` fails, and `total_allocated` reads 15
        (10 + 5) instead of 10.
        """
        client, db = _client(scm_app)
        chain = _chain(db)
        product = _product(db, chain)
        doc = unique_code("SPO-H1")

        open_line = _alloc(db, spo_number=doc, line_no=1, product=product, allocated=10, received=0)
        _alloc(
            db, spo_number=doc, line_no=2, product=product, allocated=5, received=0,
            line_status="closed", retired_at=_now(),
        )

        r = client.get(f"{DOCUMENTS_URL}/{doc}")
        assert r.status_code == 200, r.text
        body = r.json()

        line_ids = {line["id"] for line in body["lines"]}
        assert line_ids == {open_line.id}, line_ids
        assert body["total_allocated"] == 10, body["total_allocated"]
        assert body["balance"] == 10, body["balance"]


# =================================================================================== #
# AC-H2 (R1): merely closed (not retired) never disappears
# =================================================================================== #


class TestAcH2ClosedButNotRetiredStaysVisible:
    def test_fully_received_closed_line_with_no_retired_at_is_still_returned(self, scm_app):
        """AC-H2. A line closed with `receipt_status = 'fully_received'` and
        `retired_at` NULL is still returned, and still counts in
        `total_allocated`/`total_received` - seeded beside an open line so an
        over-broad implementation that hides every `line_status == 'closed'` row
        (ignoring `retired_at` entirely) fails this guard the moment it lands,
        rather than only R1's own line disappearing silently.
        """
        client, db = _client(scm_app)
        chain = _chain(db)
        product = _product(db, chain)
        doc = unique_code("SPO-H2")

        open_line = _alloc(db, spo_number=doc, line_no=1, product=product, allocated=10, received=0)
        closed_line = _alloc(
            db, spo_number=doc, line_no=2, product=product, allocated=10, received=10,
            receipt_status="fully_received", line_status="closed", retired_at=None,
        )

        r = client.get(f"{DOCUMENTS_URL}/{doc}")
        assert r.status_code == 200, r.text
        body = r.json()

        line_ids = {line["id"] for line in body["lines"]}
        assert line_ids == {open_line.id, closed_line.id}, line_ids
        assert body["total_allocated"] == 20, body["total_allocated"]
        assert body["total_received"] == 10, body["total_received"]


# =================================================================================== #
# AC-H3 (R2): a retired line carrying a receipt stays visible, balance 0
# =================================================================================== #


class TestAcH3RetiredLineWithReceiptStaysVisible:
    def test_retired_line_with_a_receipt_is_returned_counted_and_reads_balance_zero(
        self, scm_app
    ):
        """AC-H3. A line with `retired_at` set AND `quantity_received > 0` is
        still returned, counts in `total_received`, and reads `balance 0`.

        RED today at the balance assertion: `get_document` computes
        `balance = max(allocated - received, 0)` unconditionally (R5's bug), so
        this retired-but-received line reports balance 6 (10 - 4), not 0.
        """
        client, db = _client(scm_app)
        chain = _chain(db)
        product = _product(db, chain)
        doc = unique_code("SPO-H3")

        retired_with_receipt = _alloc(
            db, spo_number=doc, line_no=1, product=product, allocated=10, received=4,
            line_status="closed", retired_at=_now(),
        )

        r = client.get(f"{DOCUMENTS_URL}/{doc}")
        assert r.status_code == 200, r.text
        body = r.json()

        line_ids = {line["id"] for line in body["lines"]}
        assert line_ids == {retired_with_receipt.id}, line_ids
        assert body["total_received"] == 4, body["total_received"]
        line = body["lines"][0]
        assert line["balance"] == 0, line["balance"]


# =================================================================================== #
# AC-H4 (R5): a non-outstanding line always reads balance 0
# =================================================================================== #


class TestAcH4NonOutstandingLineBalanceIsZero:
    def test_every_non_outstanding_returned_line_reads_balance_zero_and_lines_foot_the_header(
        self, scm_app
    ):
        """AC-H4. Every returned line that is not outstanding reads `balance 0`;
        the sum of the returned lines' balances equals the document's Balance.

        Seeded: one open/outstanding line (balance 10), one retired line with a
        partial receipt (not outstanding - must read 0, not 6), one ordinary
        fully-received closed line (not outstanding, already 0 by arithmetic).

        RED today: the retired-partial line's `balance` computes to 6
        (`max(10 - 4, 0)`) instead of 0, so both the per-line assertion and the
        footing assertion (16 != 10) fail.
        """
        client, db = _client(scm_app)
        chain = _chain(db)
        product = _product(db, chain)
        doc = unique_code("SPO-H4")

        _alloc(db, spo_number=doc, line_no=1, product=product, allocated=10, received=0)
        _alloc(
            db, spo_number=doc, line_no=2, product=product, allocated=10, received=4,
            line_status="closed", retired_at=_now(),
        )
        _alloc(
            db, spo_number=doc, line_no=3, product=product, allocated=10, received=10,
            receipt_status="fully_received", line_status="closed",
        )

        r = client.get(f"{DOCUMENTS_URL}/{doc}")
        assert r.status_code == 200, r.text
        body = r.json()

        for line in body["lines"]:
            if not line["outstanding"]:
                assert line["balance"] == 0, line

        assert sum(line["balance"] for line in body["lines"]) == body["balance"], body


# =================================================================================== #
# AC-H5 (R3): an id-resolved read is NOT filtered
# =================================================================================== #


class TestAcH5GrnStillResolvesARetiredAllocationById:
    def test_grn_detail_still_renders_the_picking_line_s_retired_allocation(self, scm_app):
        """AC-H5. A GRN whose picking line points at a hidden (retired, zero
        receipt) allocation still renders that allocation in the GRN detail
        response - the allocation is reachable by id.

        This is a guard against overreach (R3), not a reproduction of a live
        bug: nothing filters `PickingHeaderService.get_grn`'s `spo_allocation`
        joinedload today, so it already passes. It exists so a later change
        that broadens `visible_line_clauses()` into a blanket
        `with_loader_criteria` cannot silently break it.
        """
        client, db = _client(scm_app)
        chain = _chain(db)
        product = _product(db, chain)
        doc = unique_code("SPO-H5")

        hidden = _alloc(
            db, spo_number=doc, line_no=1, product=product, allocated=7, received=0,
            line_status="closed", retired_at=_now(),
        )

        header = PickingHeader(
            id=_u(),
            picking_number=unique_code("GRN-H5"),
            picking_type="goods_received",
            picking_status="approved",
        )
        db.add(header)
        db.flush()
        picking_line = PickingLine(
            id=_u(),
            picking_header_id=header.id,
            spo_allocation_id=hidden.id,
            product_id=product.id,
            quantity_expected=7,
            quantity_picked=0,
        )
        db.add(picking_line)
        db.flush()

        r = client.get(f"{GRN_URL}/{header.id}")
        assert r.status_code == 200, r.text
        body = r.json()
        lines = body["picking_lines"] or []
        assert len(lines) == 1, lines
        allocation = lines[0]["spo_allocation"]
        assert allocation is not None, lines[0]
        assert allocation["id"] == hidden.id, allocation


# =================================================================================== #
# AC-H6 (R6): one predicate, one place - the grid, the list, the detail agree
# =================================================================================== #


class TestAcH6OnePredicateOnePlace:
    def _seed_document(self, db, chain):
        product = _product(db, chain)
        doc = unique_code("SPO-H6")
        open_line = _alloc(db, spo_number=doc, line_no=1, product=product, allocated=10, received=0)
        hidden_line = _alloc(
            db, spo_number=doc, line_no=2, product=product, allocated=7, received=0,
            line_status="closed", retired_at=_now(),
        )
        retired_with_receipt = _alloc(
            db, spo_number=doc, line_no=3, product=product, allocated=5, received=3,
            line_status="closed", retired_at=_now(),
        )
        fully_received = _alloc(
            db, spo_number=doc, line_no=4, product=product, allocated=10, received=10,
            receipt_status="fully_received", line_status="closed",
        )
        return doc, {
            "open": open_line,
            "hidden": hidden_line,
            "retired_with_receipt": retired_with_receipt,
            "fully_received": fully_received,
        }

    def test_visible_line_clauses_is_the_one_predicate_queried_directly(self, scm_app):
        """`spo_supply.visible_line_clauses()` is importable and usable as an
        AND-able clause tuple that, applied directly to `SPOAllocation`, keeps
        exactly the three non-hidden seeded rows.

        RED today: `app.services.scm.spo_supply` has no `visible_line_clauses`
        attribute yet.
        """
        from app.services.scm import spo_supply

        _, db = _client(scm_app)
        chain = _chain(db)
        doc, lines = self._seed_document(db, chain)

        visible_ids = {
            row.id
            for row in db.query(SPOAllocation)
            .filter(SPOAllocation.spo_number == doc)
            .filter(and_(*spo_supply.visible_line_clauses()))
            .all()
        }
        assert visible_ids == {
            lines["open"].id, lines["retired_with_receipt"].id, lines["fully_received"].id,
        }, visible_ids

    def test_grid_list_and_detail_all_agree_on_the_same_visible_set(self, scm_app):
        """AC-H6. `list_allocations`, `list_documents` and `get_document` all
        read the same visible set for one seeded document.

        RED today: none of the three readers filters on `retired_at` at all, so
        each includes the hidden line - the explicit expected-set assertions
        below fail (not merely the three-way equality, which a fully unfiltered
        implementation would also - wrongly - satisfy).
        """
        client, db = _client(scm_app)
        chain = _chain(db)
        doc, lines = self._seed_document(db, chain)
        expected_visible_ids = {
            lines["open"].id, lines["retired_with_receipt"].id, lines["fully_received"].id,
        }
        expected_total_allocated = 10 + 5 + 10  # excludes the hidden line's 7

        grid = client.get(LIST_URL, params={"query": doc, "limit": 100})
        assert grid.status_code == 200, grid.text
        grid_ids = {row["id"] for row in grid.json()["data"]}
        assert grid_ids == expected_visible_ids, grid_ids

        detail = client.get(f"{DOCUMENTS_URL}/{doc}")
        assert detail.status_code == 200, detail.text
        detail_body = detail.json()
        detail_ids = {line["id"] for line in detail_body["lines"]}
        assert detail_ids == expected_visible_ids, detail_ids
        assert detail_body["total_allocated"] == expected_total_allocated, detail_body["total_allocated"]

        doc_list = client.get(DOCUMENTS_URL, params={"state": "all", "query": doc, "limit": 100})
        assert doc_list.status_code == 200, doc_list.text
        doc_row = next(row for row in doc_list.json()["data"] if row["spo_number"] == doc)
        assert doc_row["total_allocated"] == expected_total_allocated, doc_row["total_allocated"]
        assert doc_row["total_allocated"] == detail_body["total_allocated"]


# =================================================================================== #
# AC-H8: document rollup shape on a seeded document (not production data)
# =================================================================================== #


class TestAcH8DocumentTotalExcludesTheRetiredLine:
    def test_total_allocated_excludes_the_retired_line_and_line_count_matches(self, scm_app):
        """AC-H8, adapted to a seeded document rather than production data
        (SPO-2026/09-0036 is not reachable from a Postgres-savepoint test):
        three open lines summing to a known total, plus one retired line with
        no receipt, prove `get_document`'s `total_allocated` and `line_count`
        both exclude the retired line.

        RED today: `total_allocated` sums all four lines (650), not the three
        open ones (600), and `line_count` reads 4, not 3.
        """
        client, db = _client(scm_app)
        chain = _chain(db)
        product = _product(db, chain)
        doc = unique_code("SPO-H8")

        open_ids = set()
        for line_no, qty in ((1, 100), (2, 200), (3, 300)):
            row = _alloc(db, spo_number=doc, line_no=line_no, product=product, allocated=qty, received=0)
            open_ids.add(row.id)
        retired_line = _alloc(
            db, spo_number=doc, line_no=4, product=product, allocated=50, received=0,
            line_status="closed", retired_at=_now(),
        )

        r = client.get(f"{DOCUMENTS_URL}/{doc}")
        assert r.status_code == 200, r.text
        body = r.json()

        line_ids = {line["id"] for line in body["lines"]}
        assert line_ids == open_ids, line_ids
        assert retired_line.id not in line_ids
        assert body["total_allocated"] == 600, body["total_allocated"]
        assert body["line_count"] == 3, body["line_count"]


# =================================================================================== #
# AC-H10 (B2, round 2): a receipt approved AFTER retirement still reaches the row
# =================================================================================== #


class TestAcH10ReceiptAfterRetirement:
    def test_a_grn_approved_after_retirement_still_writes_the_allocation_s_receipt(
        self, scm_app
    ):
        """AC-H10, first half. A retired allocation (received 0) plus an
        approved goods-received note whose picking line draws 5 against it:
        after `sync_grn_received_to_spo`, the allocation reads
        `quantity_received 5`, is still returned by the document detail (R2:
        `retired_at` set AND `quantity_received > 0` stays visible), and
        counts in `total_received`.

        RED today: `_sync_received_for_allocations` still `continue`s on a
        retired autocount row without writing anything, so
        `quantity_received` stays 0 - and at 0 the row is also hidden
        (R2 requires `> 0`), so both the DB-level and the document-detail
        assertions fail.
        """
        from app.services.procurement_service import PickingHeaderService

        client, db = _client(scm_app)
        chain = _chain(db)
        product = _product(db, chain)
        doc = unique_code("SPO-H10A")

        retired = _alloc(
            db, spo_number=doc, line_no=1, product=product, allocated=47, received=0,
            line_status="closed", retired_at=_now(),
        )

        header = PickingHeader(
            id=_u(),
            picking_number=unique_code("GRN-H10A"),
            picking_type="goods_received",
            picking_status="approved",
            spo_number=doc,
        )
        db.add(header)
        db.flush()
        db.add(
            PickingLine(
                id=_u(),
                picking_header_id=header.id,
                spo_allocation_id=retired.id,
                product_id=product.id,
                quantity_expected=5,
                quantity_picked=5,
            )
        )
        db.flush()
        db.commit()

        PickingHeaderService(db).sync_grn_received_to_spo(header.id)

        db.expire_all()
        stored = db.query(SPOAllocation).filter(SPOAllocation.id == retired.id).one()
        assert stored.quantity_received == 5, stored.quantity_received

        r = client.get(f"{DOCUMENTS_URL}/{doc}")
        assert r.status_code == 200, r.text
        body = r.json()
        line_ids = {line["id"] for line in body["lines"]}
        assert retired.id in line_ids, line_ids
        assert body["total_received"] == 5, body["total_received"]

    def test_a_retired_line_s_stated_receipt_survives_the_grn_that_proved_it_being_deleted(
        self, scm_app
    ):
        """AC-H10, second half - the AC-X40 shape
        (`tests/test_spo_xlsx_supersede.py::TestAcX40...`), but seeded with
        `retired_at` set directly rather than reached through the ingest's
        own leftover sweep: a retired allocation with `stated_received 29`
        and `quantity_received 29` (closed BY the GRN that proved it) whose
        GRN is then deleted still reads 29 and stays closed - the D28c
        floor (`max(stated_received, its own approved picking total)`)
        covers the deletion, so a retired line is never demand again just
        because the receipt that closed it went away.

        This is a guard, not a reproduction of a live bug: on the
        PRE-B2 tree the assertions below already pass BY INERTIA (a retired
        row is never touched by `_sync_received_for_allocations` at all, in
        either direction, so `quantity_received` simply never moves off 29).
        `test_a_grn_approved_after_retirement...` above is what actually
        pins the new write path being red today; this one exists so that
        once that write path lands, nobody drops the `max(stated_received,
        ...)` floor and silently zeroes a retired line's receipt the moment
        its GRN is deleted (AC-X40's own regression, one row over).
        """
        from app.services.procurement_service import PickingHeaderService

        client, db = _client(scm_app)
        chain = _chain(db)
        product = _product(db, chain)
        doc = unique_code("SPO-H10B")

        retired = _alloc(
            db, spo_number=doc, line_no=1, product=product, allocated=29, received=29,
            receipt_status="fully_received", line_status="closed", retired_at=_now(),
            stated_received=29,
        )

        header = PickingHeader(
            id=_u(),
            picking_number=unique_code("GRN-H10B"),
            picking_type="goods_received",
            picking_status="approved",
            spo_number=doc,
        )
        db.add(header)
        db.flush()
        db.add(
            PickingLine(
                id=_u(),
                picking_header_id=header.id,
                spo_allocation_id=retired.id,
                product_id=product.id,
                quantity_expected=29,
                quantity_picked=29,
            )
        )
        db.flush()
        db.commit()

        PickingHeaderService(db).delete_grn(header.id)

        db.expire_all()
        stored = db.query(SPOAllocation).filter(SPOAllocation.id == retired.id).one()
        assert stored.quantity_received == 29, stored.quantity_received
        assert stored.line_status == "closed", stored.line_status
        assert stored.receipt_status == "fully_received", stored.receipt_status


# =================================================================================== #
# AC-H14 (round 4): the retired branch skips a row nobody released or picked
# =================================================================================== #


class TestAcH14OwnershipGateOnTheRetiredBranch:
    def test_an_untouched_retired_row_is_left_exactly_as_stored(self, scm_app):
        """AC-H14. A retired allocation with `quantity_received 25`,
        `stated_received` NULL, and NO picking line anywhere: running
        `sync_received_for_spo_number` for its document (nothing released,
        nothing picked) leaves it at 25 and still visible. Pairs with
        `TestAcH10ReceiptAfterRetirement`'s first test - together the two
        pin both directions of the retired branch's gate: picked against ->
        recompute writes (AC-H10); neither released nor picked -> skip,
        exactly as the non-AutoCount branch's own
        `if alloc_id not in released and not
        self._allocation_has_picking_line(alloc_id): continue` already
        does.

        RED today: round 2's B2 write
        (`self._write_received(alloc, max(stated_received, computed),
        may_reopen=False)`) runs UNCONDITIONALLY for every retired autocount
        row, with no ownership gate at all - `compute_received_for_allocation`
        finds no approved picking line and returns 0, `stated_received` is
        NULL (reads as 0), so `max(0, 0) = 0` OVERWRITES the stored 25 with
        0 - which then also HIDES the row under R2 (`retired_at` set AND
        `quantity_received 0`), so both assertions below fail.
        """
        from app.services.procurement_service import PickingHeaderService

        client, db = _client(scm_app)
        chain = _chain(db)
        product = _product(db, chain)
        doc = unique_code("SPO-H14")

        retired = _alloc(
            db, spo_number=doc, line_no=1, product=product, allocated=30, received=25,
            line_status="closed", retired_at=_now(), stated_received=None,
        )
        db.commit()

        PickingHeaderService(db).sync_received_for_spo_number(doc)

        db.expire_all()
        stored = db.query(SPOAllocation).filter(SPOAllocation.id == retired.id).one()
        assert stored.quantity_received == 25, stored.quantity_received

        r = client.get(f"{DOCUMENTS_URL}/{doc}")
        assert r.status_code == 200, r.text
        body = r.json()
        line_ids = {line["id"] for line in body["lines"]}
        assert retired.id in line_ids, line_ids


# =================================================================================== #
# AC-H11 (round 3, B2 "ghost document"): no visible line -> no listing, no page
# =================================================================================== #


class TestAcH11GhostDocumentNeverLists:
    def test_a_document_whose_every_line_is_hidden_is_absent_from_every_state_and_404s(
        self, scm_app
    ):
        """AC-H11. A document whose only two lines are BOTH retired with zero
        receipt does not appear in `list_documents` under `state=all`,
        `state=outstanding` or `state=completed`; `get_document` still 404s
        for it, the same as a `spo_number` nothing was ever pushed under.

        RED today (state=all / state=completed): `list_documents` groups
        every `spo_number` regardless of visibility, gating only the
        AGGREGATES on `is_visible` - a document with two hidden, `closed`
        lines has `has_outstanding=False` (both are `line_status='closed'`,
        so `open_incoming_clauses()` already excludes them), so it already
        drops out of `state=outstanding` for free, but lists as a 0-line
        ghost row under `state=all`/`state=completed` that 404s the moment
        it is opened - the exact defect this AC exists to close.
        `get_document` already 404s today (round 1's own
        `visible_line_clauses()` filter on its main query empties `rows`),
        so that half of this test is a guard rather than new RED.
        """
        client, db = _client(scm_app)
        chain = _chain(db)
        product = _product(db, chain)
        doc = unique_code("SPO-H11")
        _alloc(
            db, spo_number=doc, line_no=1, product=product, allocated=10, received=0,
            line_status="closed", retired_at=_now(),
        )
        _alloc(
            db, spo_number=doc, line_no=2, product=product, allocated=5, received=0,
            line_status="closed", retired_at=_now(),
        )

        for state in ("all", "outstanding", "completed"):
            r = client.get(DOCUMENTS_URL, params={"state": state, "query": doc, "limit": 100})
            assert r.status_code == 200, r.text
            numbers = {row["spo_number"] for row in r.json()["data"]}
            assert doc not in numbers, (state, numbers)

        detail = client.get(f"{DOCUMENTS_URL}/{doc}")
        assert detail.status_code == 404, detail.text


# =================================================================================== #
# AC-H12 (round 3, S1/S2): the list header agrees with the page it opens
# =================================================================================== #


class TestAcH12ListHeaderAgreesWithDetail:
    """AC-H12, split into two tests (reviewer kill-test round) so a regression in
    EITHER ruling names itself instead of both failing under one test name: S1
    (`is_outstanding` visibility-gated) and S2 (`_document_supplier_rollup`
    visibility-gated) are independent code paths, and the original single test
    could not tell a reader which one broke. Same seed both times: a visible open
    line plus a HIDDEN line that is `line_status='open'` (not closed - nothing
    enforces "retired implies closed") with zero receipt and `retired_at` set, on
    a different supplier so the majority-vote half has something to get wrong.
    """

    def _seed(self, scm_app):
        client, db = _client(scm_app)
        chain = _chain(db)
        product = _product(db, chain)
        doc = unique_code("SPO-H12")

        supplier_visible = _supplier(db, name="Visible Supplier Co")
        supplier_hidden = _supplier(db, name="Hidden Supplier Co")

        _alloc(
            db, spo_number=doc, line_no=1, product=product, allocated=10, received=0,
            line_status="open", supplier_id=supplier_visible.id,
        )
        _alloc(
            db, spo_number=doc, line_no=2, product=product, allocated=999, received=0,
            line_status="open", retired_at=_now(), supplier_id=supplier_hidden.id,
        )
        return client, doc

    def test_balance_status_worst_overdue_and_earliest_eta_agree_with_detail(self, scm_app):
        """S1. The list row's Balance, status, worst overdue and earliest ETA
        equal the detail's, none of them driven by the hidden line.

        RED today: `is_outstanding` in `list_documents` only reads
        `open_incoming_clauses()` (line-status-gated, not visibility-gated), so
        this open-but-retired line still counts as outstanding there even
        though `get_document`'s own `rows` query has already filtered it out
        entirely - the list's Balance/status disagree with the page it opens.
        """
        client, doc = self._seed(scm_app)

        detail = client.get(f"{DOCUMENTS_URL}/{doc}")
        assert detail.status_code == 200, detail.text
        detail_body = detail.json()

        list_r = client.get(DOCUMENTS_URL, params={"state": "all", "query": doc, "limit": 100})
        assert list_r.status_code == 200, list_r.text
        row = next(r for r in list_r.json()["data"] if r["spo_number"] == doc)

        assert row["balance"] == detail_body["balance"], (row, detail_body)
        assert row["status"] == detail_body["status"], (row, detail_body)
        assert row["worst_overdue_days"] == max(
            (line["overdue_days"] for line in detail_body["lines"] if line["outstanding"]),
            default=0,
        ), (row, detail_body)
        detail_earliest = min(
            (
                line["arrival_date"] for line in detail_body["lines"]
                if line["outstanding"] and line["arrival_date"] is not None
            ),
            default=None,
        )
        assert row["earliest_eta"] == detail_earliest, (row, detail_body)

    def test_majority_supplier_name_and_extra_count_agree_with_detail(self, scm_app):
        """S2. The majority supplier name (and its "+N others" extra count)
        agree between the list and the detail page it opens.

        RED today: `_document_supplier_rollup` is unfiltered, so the hidden
        line's own supplier can win (or split) the majority vote the detail
        page's own `supplier_counts` never counts at all - the list can show a
        different supplier name (or a nonzero `supplier_extra_count`) than the
        page it opens.
        """
        client, doc = self._seed(scm_app)

        detail = client.get(f"{DOCUMENTS_URL}/{doc}")
        assert detail.status_code == 200, detail.text
        detail_body = detail.json()

        list_r = client.get(DOCUMENTS_URL, params={"state": "all", "query": doc, "limit": 100})
        assert list_r.status_code == 200, list_r.text
        row = next(r for r in list_r.json()["data"] if r["spo_number"] == doc)

        assert row["supplier_name"] == "Visible Supplier Co", row
        assert row["supplier_name"] == detail_body["supplier_name"], (row, detail_body)
        assert row["supplier_extra_count"] == detail_body["supplier_extra_count"], (
            row, detail_body,
        )


# =================================================================================== #
# Round 3 coverage gaps (reviewer): list_documents' line_count and its sort_map
# entries must read the SAME gated expression the SELECT itself uses.
# =================================================================================== #


class TestListDocumentsLineCountAndSortAreGated:
    def test_line_count_is_visible_only_and_sorting_by_it_or_total_allocated_uses_the_gate(
        self, scm_app
    ):
        """Two documents, scoped to one product so both are found by the same
        `query=` filter: document X carries one visible line (allocated 100)
        plus THREE hidden retired lines (allocated 1000 each, received 0);
        document Y carries two ordinary visible lines (allocated 200 each).

        Gated: X reads `line_count 1` / `total_allocated 100`; Y reads
        `line_count 2` / `total_allocated 400` - X sorts BEFORE Y ascending
        on either field. An UNGATED `line_count`/`total_allocated` would
        read X as 4 lines / 3100 allocated, sorting X AFTER Y instead -
        the opposite order, which is what pins this red rather than a
        same-order coincidence.

        RED today: `line_count` is already gated in the SELECT (round 1), so
        the VALUE assertions below already pass; the `sort_map` entries for
        `total_allocated`/`line_count` are NOT gated yet - both sort calls
        return X after Y (the ungated order) instead of before.
        """
        client, db = _client(scm_app)
        chain = _chain(db)
        product = _product(db, chain)
        doc_x = unique_code("SPO-H12X")
        doc_y = unique_code("SPO-H12Y")

        _alloc(db, spo_number=doc_x, line_no=1, product=product, allocated=100, received=0)
        for n in (2, 3, 4):
            _alloc(
                db, spo_number=doc_x, line_no=n, product=product, allocated=1000, received=0,
                line_status="closed", retired_at=_now(),
            )
        _alloc(db, spo_number=doc_y, line_no=1, product=product, allocated=200, received=0)
        _alloc(db, spo_number=doc_y, line_no=2, product=product, allocated=200, received=0)

        r = client.get(DOCUMENTS_URL, params={
            "state": "all", "query": product.product_code, "limit": 100,
        })
        assert r.status_code == 200, r.text
        by_number = {row["spo_number"]: row for row in r.json()["data"]}
        assert by_number[doc_x]["line_count"] == 1, by_number[doc_x]
        assert by_number[doc_x]["total_allocated"] == 100, by_number[doc_x]
        assert by_number[doc_y]["line_count"] == 2, by_number[doc_y]
        assert by_number[doc_y]["total_allocated"] == 400, by_number[doc_y]

        for sort_field in ("total_allocated", "line_count"):
            sorted_r = client.get(DOCUMENTS_URL, params={
                "state": "all", "query": product.product_code, "limit": 100,
                "sort": sort_field, "dir": "asc",
            })
            assert sorted_r.status_code == 200, sorted_r.text
            order = [
                row["spo_number"] for row in sorted_r.json()["data"]
                if row["spo_number"] in (doc_x, doc_y)
            ]
            assert order == [doc_x, doc_y], (sort_field, order)


# =================================================================================== #
# AC-H13 (round 3, N1): the packing-list detail's related-SPO strip hides retired
# =================================================================================== #


class TestAcH13PackingListRelatedSpoStripHidesRetired:
    def test_related_spo_strip_and_allocated_total_exclude_a_retired_allocation(
        self, scm_app
    ):
        """AC-H13. A packing list's per-product related-SPO strip
        (`GET /procurement/packing-lists/{shipment_id}`) hides a retired,
        zero-receipt allocation the same rule as every other listing, and
        excludes its quantity from `spo_allocated_quantity`.

        RED today: `get_packing_list` queries `SPOAllocation` by
        `inbound_shipment_id` alone, no visibility filter, so both the
        related-SPO strip and the allocated total include the retired row
        (999, dwarfing the visible line's 10 - chosen so an ungated sum is
        unmistakable rather than a coincidental match).
        """
        client, db = _client(scm_app)
        chain = _chain(db)
        product = _product(db, chain)
        doc = unique_code("SPO-H13")

        shipment = InboundShipment(
            id=_u(),
            shipment_number=unique_code("SHIP-H13"),
            shipment_date=date(2026, 7, 1),
            shipment_status="in_transit",
        )
        db.add(shipment)
        db.flush()
        db.add(
            InboundShipmentLine(
                id=_u(), shipment_id=shipment.id, product_id=product.id, quantity_shipped=10,
            )
        )
        db.flush()

        visible = _alloc(db, spo_number=doc, line_no=1, product=product, allocated=10, received=0)
        visible.inbound_shipment_id = shipment.id
        retired = _alloc(
            db, spo_number=doc, line_no=2, product=product, allocated=999, received=0,
            line_status="closed", retired_at=_now(),
        )
        retired.inbound_shipment_id = shipment.id
        db.flush()

        r = client.get(f"{PACKING_LISTS_URL}/{shipment.id}")
        assert r.status_code == 200, r.text
        body = r.json()
        product_lines = [
            line for line in body["shipment_lines"] if line["product_id"] == product.id
        ]
        assert len(product_lines) == 1, product_lines
        line_body = product_lines[0]
        assert line_body["spo_allocated_quantity"] == 10, line_body
        related_ids = {a["id"] for a in (line_body.get("related_spo_allocations") or [])}
        assert related_ids == {visible.id}, related_ids


# =================================================================================== #
# AC-H17 (round 4): the packing list's quantity and its derived status agree
# =================================================================================== #


class TestAcH17PackingListQuantityAndStatusAgree:
    def test_response_recomputes_visible_only_while_the_persisted_row_stays_unfiltered(
        self, scm_app
    ):
        """AC-H17 (revised, round 5). A shipment line whose product has one
        visible allocation (10) and one retired, zero-receipt allocation
        (999) with an `inbound_shipment_id` on the SAME shipment: the
        packing-list RESPONSE recomputes both `spo_allocated_quantity` and
        `line_status` from the VISIBLE set only. `quantity_shipped` is
        chosen (50) so the filtered total (10) and the unfiltered total
        (1009) fall in DIFFERENT status branches of
        `compute_inbound_shipment_line_status` - filtered:
        `quantity_shipped(50) > allocated(10)` -> `partially_allocated`;
        unfiltered: `quantity_shipped(50) <= allocated(1009)` and
        `received(0) == 0` -> `allocated` - so an ungated status cannot pass
        by coincidence.

        `refresh_shipment_line_statuses`' PERSISTED `spo_allocated_quantity` is
        deliberately NOT filtered (it feeds the reorder engine's ask, not the
        screen), so the request is also asserted NOT to have narrowed the
        stored figure: it reads the UNFILTERED 1009 after the very request
        whose own RESPONSE reports 10, catching a future over-filter that
        would silently starve the reorder engine's ask. The persisted
        `line_status` DOES follow the visible total since PL-STATUS-ROLLUP
        (`compute_shipment_line_figures`): the header rollup and the list page
        read it, and judging it on the unfiltered total left a container the
        page showed fully received reading In Transit (TCLU8176397). So it
        persists "partially_allocated", the same status the response reports.

        RED today at the response assertions: `get_packing_list` persists
        `line_status` via `refresh_shipment_line_statuses`, whose own
        `totals_alloc` query carries no visibility filter, and the route
        never recomputes `line_status` afterwards - so the response shows
        `spo_allocated_quantity 10` (round 3's own fix, already filtered)
        against a `line_status` still computed from 1009, exactly the
        disagreement the AC names. The persisted-row assertions already
        pass today (nothing filters the write path at all yet); they are
        the guard against a future fix reaching too far.
        """
        client, db = _client(scm_app)
        chain = _chain(db)
        product = _product(db, chain)
        doc = unique_code("SPO-H17")

        shipment = InboundShipment(
            id=_u(),
            shipment_number=unique_code("SHIP-H17"),
            shipment_date=date(2026, 7, 1),
            shipment_status="in_transit",
        )
        db.add(shipment)
        db.flush()
        db.add(
            InboundShipmentLine(
                id=_u(), shipment_id=shipment.id, product_id=product.id, quantity_shipped=50,
            )
        )
        db.flush()

        visible = _alloc(db, spo_number=doc, line_no=1, product=product, allocated=10, received=0)
        visible.inbound_shipment_id = shipment.id
        retired = _alloc(
            db, spo_number=doc, line_no=2, product=product, allocated=999, received=0,
            line_status="closed", retired_at=_now(),
        )
        retired.inbound_shipment_id = shipment.id
        db.flush()

        r = client.get(f"{PACKING_LISTS_URL}/{shipment.id}")
        assert r.status_code == 200, r.text
        body = r.json()
        product_lines = [
            line for line in body["shipment_lines"] if line["product_id"] == product.id
        ]
        assert len(product_lines) == 1, product_lines
        line_body = product_lines[0]

        # The RESPONSE: visible-only.
        assert line_body["spo_allocated_quantity"] == 10, line_body
        assert line_body["line_status"] == "partially_allocated", line_body

        # The PERSISTED row: the allocated figure stays unfiltered (round 5); the
        # status follows the visible total (PL-STATUS-ROLLUP).
        db.expire_all()
        persisted = db.execute(
            text(
                "SELECT spo_allocated_quantity, line_status FROM inbound_shipment_lines "
                "WHERE shipment_id = :sid AND product_id = :pid"
            ),
            {"sid": shipment.id, "pid": product.id},
        ).mappings().first()
        assert persisted["spo_allocated_quantity"] == 1009, persisted
        assert persisted["line_status"] == "partially_allocated", persisted


# =================================================================================== #
# AC-H19 (round 5): the incoming badge and its allocation gap exclude retired rows
# =================================================================================== #
#
# `IncomingStockService._warehouse_allocations_for` feeds `incoming_list` (the payload
# n8n actually reads - "n8n uses incoming_stock_list only"), `incoming_for_product` and
# `shipment_incoming_products`. This suite exercises `incoming_list`. Fixture shape
# matches `tests/test_incoming_allocation_gap.py`/`tests/test_incoming_list.py`
# (`blank_session`, no HTTP, no `scm_app`) rather than this file's own `_client`/`_alloc`
# helpers - those are scoped to the SPO-document HTTP surface and this AC is a plain
# service-level read, so a second, purpose-built `blank_session` fixture here is truer
# to the existing tests than bending `_alloc` (which has no `warehouse_id`/`spo_number`
# free-text shape) to fit.


@pytest.fixture()
def incoming_db():
    with blank_session() as session:
        yield session


def _h19_product(db, code: str) -> str:
    category = ProductCategory(
        id=_u(), category_code=f"CAT-{code}", category_name=f"Category {code}"
    )
    uom = UnitOfMeasure(id=_u(), uom_code=f"UOM-{code}", uom_name="Each")
    db.add_all([category, uom])
    db.flush()
    pid = _u()
    db.add(
        Product(
            id=pid, product_code=code, product_name=code, category_id=category.id,
            base_uom_id=uom.id, list_price=0, is_active=True,
        )
    )
    db.flush()
    return pid


def _h19_shipment(db, *, number: str, eta: date | None = date(2026, 2, 1)) -> str:
    sid = _u()
    db.add(
        InboundShipment(
            id=sid, shipment_number=number, shipment_date=date(2026, 1, 1),
            estimated_arrival_date=eta,
        )
    )
    db.flush()
    return sid


def _h19_line(db, shipment_id: str, product_id: str, *, shipped: int, received: int = 0) -> None:
    db.add(
        InboundShipmentLine(
            id=_u(), shipment_id=shipment_id, product_id=product_id,
            quantity_shipped=shipped, quantity_received=received, line_status="in_transit",
        )
    )
    db.flush()


def _h19_warehouse(db, code: str) -> str:
    wid = _u()
    db.add(Warehouse(id=wid, warehouse_code=code, warehouse_name=f"{code} Warehouse"))
    db.flush()
    return wid


def _h19_alloc(
    db, shipment_id: str, product_id: str, warehouse_id: str, qty: int, *,
    spo: str, retired: bool = False, line_status: str = "open",
) -> None:
    db.add(
        SPOAllocation(
            id=_u(), spo_number=spo, inbound_shipment_id=shipment_id, product_id=product_id,
            warehouse_id=warehouse_id, allocated_quantity=qty, quantity_received=0,
            line_status=line_status, retired_at=_now() if retired else None,
        )
    )
    db.flush()


class TestAcH19IncomingBadgeExcludesRetiredAllocations:
    def test_a_retired_allocation_does_not_count_as_allocated_and_the_gap_widens(
        self, incoming_db
    ):
        """AC-H19. `_warehouse_allocations_for` counts a retired allocation
        today - no line-status or retirement test at all - so the incoming
        badge (`incoming_list`, the signal n8n actually reads) and its
        `unallocated_quantity` gap both credit supply AutoCount deleted.

        Seeded: one shipment line shipped 100, one VISIBLE allocation of 40
        at warehouse BRW-H19, one RETIRED allocation of 999 at the SAME
        warehouse and product (closed, zero receipt, `retired_at` set) - 999
        chosen so an ungated sum (1039) exceeds `quantity_shipped` (100) and
        the existing over-allocation clamp
        (`test_list_over_allocated_clamps_to_none` in
        `tests/test_incoming_allocation_gap.py`) hides the gap entirely,
        worse than merely wrong: the badge would read
        `unallocated_quantity: None` ("fully covered") when 60 units are
        genuinely unclaimed.

        RED today: `_warehouse_allocations_for` filters only
        `allocated_quantity > 0`, no `visible_line_clauses()` - the retired
        row's 999 is summed in, `warehouse_allocations` sums to 1039 instead
        of 40, and `unallocated_quantity` clamps to None instead of reading
        60.
        """
        db = incoming_db
        product_id = _h19_product(db, "SKU-H19")
        shipment_id = _h19_shipment(db, number="SH-H19")
        warehouse_id = _h19_warehouse(db, "BRW-H19")
        _h19_line(db, shipment_id, product_id, shipped=100)
        _h19_alloc(db, shipment_id, product_id, warehouse_id, 40, spo="SPO-H19-VISIBLE")
        _h19_alloc(
            db, shipment_id, product_id, warehouse_id, 999, spo="SPO-H19-RETIRED",
            retired=True, line_status="closed",
        )
        db.commit()

        from app.services.incoming_stock_service import IncomingStockService

        res = IncomingStockService(db).incoming_list(product_ids=[product_id])
        assert res["empty"] is False, res
        line = res["data"][0]["lines"][0]

        allocated_sum = sum(a["allocated_quantity"] for a in line["warehouse_allocations"])
        assert allocated_sum == 40, line["warehouse_allocations"]
        assert line["unallocated_quantity"] == 60, line
