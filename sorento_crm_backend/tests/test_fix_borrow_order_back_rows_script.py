"""BORROW-LOCATION-ONLY D6 / B6: `scripts/fix_borrow_order_back_rows.py`
(`documentation/plans/scm/PLAN-borrow-location-only-8oct.md`).

TEST-FIRST: the script does not exist yet, so the module import in each test fails with
ModuleNotFoundError, the right red for a slice not yet built.

Interface this pins (shaped on `scripts/fold_oi_date_notices.py` and its test):

* `plan(db) -> list` of fixes, one per eligible row, each carrying `row_id`,
  `from_so_line_id`, `to_so_line_id`, `old_delivery_date`, `new_delivery_date`, `old_note`,
  `new_note` (a dict or an object with those names; read through `_field`).
* `run(db, apply: bool = False) -> dict` with `fixes` (the plan), `applied` (count written)
  and `skipped` (list of dicts, each with `row_id`, for an eligible-looking row whose target
  line cannot be resolved). The caller commits.
* `main(argv) -> int` with `--apply` (dry-run is the default), owning `SessionLocal()` at
  module level so a test can swap it, the way `tests/test_oi_stale_link_state_repair.py`
  does.

Selection (B6, structural, never a note regex): verb ORDER_BACK, state raised or
partly_linked with NO `order_inquiry_links`, `covered_by` empty, and the row's `so_line_id`
belongs to a DIFFERENT project sales order than its OrderInquiry header's
`project_sales_order_id`. Target = the header SO's line whose `line_no` matches the note's
"to <ref> line <m>". New date = that line's delivery date; new note starts
"Order-back: <loc> lent" and still says "line <m>".

Postgres via `blank_session`, every chain seeded here.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.models.base import company_scope
from app.models.project_so import (
    INQUIRY_CANCELLED,
    INQUIRY_PARTLY_LINKED,
    INQUIRY_PLACED,
    INQUIRY_RAISED,
    IV_ORDER_BACK,
    OrderInquiry,
    OrderInquiryLink,
    OrderInquiryRow,
    SOLineAllocation,
)

from ._pg_fixture import blank_session
from .scm.test_project_supply_service_ladder import _group_sites, _seed_line, _world
from .test_so_supply_confirmation import _core_line, _core_so, _project_line, _uid

TODAY = date.today()
BORROWER_LINE_1_DATE = TODAY + timedelta(days=10)
BORROWER_LINE_2_DATE = TODAY + timedelta(days=25)
DONOR_DATE = TODAY + timedelta(days=400)


class _NoCloseSession:
    """`main()` owns `SessionLocal()` and closes it; the test's session is the fixture's
    one rolled-back transaction, so `close` must be a no-op."""

    def __init__(self, session):
        self._session = session

    def __getattr__(self, name):
        return getattr(self._session, name)

    def close(self):
        pass


def _field(fix, name):
    return fix[name] if isinstance(fix, dict) else getattr(fix, name)


def _script():
    import scripts.fix_borrow_order_back_rows as module

    return module


def _po_line(db, product, warehouse):
    from app.models.procurement import PurchaseOrder, PurchaseOrderLine, Supplier

    supplier = Supplier(
        id=_uid(), supplier_code=f"ZZTFBO{_uid()[:8]}".upper(), supplier_name="zzt fbo supplier"
    )
    db.add(supplier)
    db.flush()
    po = PurchaseOrder(
        id=_uid(), po_number=f"ZZT-FBO-{_uid()[:6]}", supplier_id=supplier.id,
        status="active", issue_date=TODAY, expected_date=TODAY + timedelta(days=30),
    )
    db.add(po)
    db.flush()
    line = PurchaseOrderLine(
        id=_uid(), purchase_order_id=po.id, product_id=product.id,
        warehouse_id=warehouse.id, qty_ordered=100, qty_received=0, line_status="open",
        expected_date=TODAY + timedelta(days=30), source_ref="1",
    )
    db.add(line)
    db.flush()
    return po, line


class World:
    pass


def _world_for_script(db) -> World:
    """A borrowing SO (lines 1 and 2, different dates), a donor SO line due much later, and
    the borrowing SO's OI header. Rows are added per test through `row()`."""
    w = World()
    w.db = db
    w.company_id, w.eling, project, w.product = _world(db)
    _group, sites = _group_sites(db)
    w.own, _pool = sites["BRW"]
    w.donor_bin, _p2 = sites["MWH"]

    w.borrower, w.line1, _cso, _cl = _seed_line(
        db, w.company_id, project, w.product, w.own, qty_ordered="6",
        required_date=BORROWER_LINE_1_DATE, line_no=1, so_number=f"ZZT-BRW{_uid()[:5]}",
    )
    core_so = _core_so(db, w.company_id)
    core_line2 = _core_line(
        db, core_so, w.product, w.own, qty_ordered="9", required_date=BORROWER_LINE_2_DATE
    )
    w.line2 = _project_line(db, w.borrower, line_no=2, product=w.product, core_line=core_line2)
    w.donor, w.donor_line, _dc, w.donor_cline = _seed_line(
        db, w.company_id, project, w.product, w.donor_bin, qty_ordered="276",
        required_date=DONOR_DATE, line_no=446, so_number=f"ZZT-DON{_uid()[:5]}",
    )
    # S3 (a): what a step-2 / group borrow confirm writes on the BORROWING line - the
    # allocation naming the donor's core line inside `donor_impact_snapshot`.
    for borrowing in (w.line1, w.line2):
        _borrow_allocation(w, borrowing, w.donor_cline)
    w.header = OrderInquiry(
        id=_uid(), company_id=w.company_id, project_sales_order_id=w.borrower.id
    )
    db.add(w.header)
    db.flush()
    w.ref = w.borrower.autocount_doc_no or w.borrower.provisional_ref
    db.commit()
    return w


def _borrow_allocation(w, line, donor_cline):
    w.db.add(
        SOLineAllocation(
            id=_uid(), company_id=w.company_id, so_line_id=line.id,
            source_type="other_location", warehouse_id=w.donor_bin.id, qty=Decimal("6"),
            reason="Borrowed.",
            donor_impact_snapshot={"donor_core_line_id": str(donor_cline.id)},
        )
    )
    w.db.flush()


def _row(w, *, line, state=INQUIRY_RAISED, note=None, covered_by=None, qty="6", target_no=1,
         date_=None, link=False, item_code=None):
    note = note if note is not None else (
        f"Order-back: ZZT-DON lent {qty} to {w.ref} line {target_no}"
        if line is w.donor_line else f"Order-back: {w.donor_bin.warehouse_code} lent {qty} "
        f"to {w.ref} line {target_no}"
    )
    row = OrderInquiryRow(
        id=_uid(), company_id=w.company_id, order_inquiry_id=w.header.id,
        so_line_id=line.id, item_code=item_code or w.product.product_code, qty=Decimal(qty),
        delivery_date=date_ or (DONOR_DATE if line is w.donor_line else BORROWER_LINE_1_DATE),
        stock_location=w.donor_bin.warehouse_code, verb=IV_ORDER_BACK, state=state,
        note=note, covered_by=covered_by,
    )
    w.db.add(row)
    w.db.flush()
    if link:
        po, po_line = _po_line(w.db, w.product, w.own)
        w.db.add(
            OrderInquiryLink(
                id=_uid(), company_id=w.company_id, row_id=row.id, po_line_id=po_line.id,
                document=po.po_number, qty=Decimal("2"),
            )
        )
        w.db.flush()
    w.db.commit()
    return row


def _snapshot(row):
    return (row.so_line_id, row.delivery_date, row.note, row.state, row.stock_location, row.qty)


# --------------------------------------------------------------------------- eligible


def test_plan_lists_the_eligible_donor_line_row_with_its_new_line_date_and_note():
    script = _script()
    with blank_session() as db:
        w = _world_for_script(db)
        row = _row(w, line=w.donor_line, target_no=1)
        with company_scope(db, frozenset({w.company_id})):
            fixes = script.plan(db)

        assert len(fixes) == 1, fixes
        fix = fixes[0]
        assert str(_field(fix, "row_id")) == str(row.id)
        assert str(_field(fix, "from_so_line_id")) == str(w.donor_line.id)
        assert str(_field(fix, "to_so_line_id")) == str(w.line1.id)
        assert _field(fix, "old_delivery_date") == DONOR_DATE
        assert _field(fix, "new_delivery_date") == BORROWER_LINE_1_DATE
        assert _field(fix, "old_note") == row.note
        new_note = _field(fix, "new_note")
        assert new_note.startswith(f"Order-back: {w.donor_bin.warehouse_code} lent"), new_note
        assert "line 1" in new_note


def test_apply_re_points_the_row_to_the_borrowing_line_and_its_date_and_note():
    script = _script()
    with blank_session() as db:
        w = _world_for_script(db)
        row = _row(w, line=w.donor_line, target_no=2, qty="6")
        with company_scope(db, frozenset({w.company_id})):
            report = script.run(db, apply=True)
        db.commit()
        db.expire_all()
        db.refresh(row)

        assert report["applied"] == 1, report
        assert str(row.so_line_id) == str(w.line2.id), "the SECOND line, matched on line_no"
        assert row.delivery_date == BORROWER_LINE_2_DATE
        assert row.note.startswith(f"Order-back: {w.donor_bin.warehouse_code} lent"), row.note
        assert "line 2" in row.note
        assert row.state == INQUIRY_RAISED
        assert row.qty == Decimal("6")
        assert row.stock_location == w.donor_bin.warehouse_code


def test_a_partly_linked_row_with_no_links_is_eligible_too():
    script = _script()
    with blank_session() as db:
        w = _world_for_script(db)
        row = _row(w, line=w.donor_line, state=INQUIRY_PARTLY_LINKED, target_no=1)
        with company_scope(db, frozenset({w.company_id})):
            fixes = script.plan(db)
        assert [str(_field(f, "row_id")) for f in fixes] == [str(row.id)]


# --------------------------------------------------------------------------- untouched


def test_rows_outside_the_selection_are_untouched_by_apply():
    script = _script()
    with blank_session() as db:
        w = _world_for_script(db)
        eligible = _row(w, line=w.donor_line, target_no=1)
        untouched = {
            "placed": _row(w, line=w.donor_line, state=INQUIRY_PLACED, link=True),
            "linked_partly": _row(
                w, line=w.donor_line, state=INQUIRY_PARTLY_LINKED, link=True
            ),
            "cancelled": _row(w, line=w.donor_line, state=INQUIRY_CANCELLED),
            "covered": _row(w, line=w.donor_line, covered_by="SPO-ZZT-1"),
            # Already on the borrowing line, still carrying a donor-form note (the
            # no-mirror fallback): same SO as the header, so structurally not a donor-line row.
            "on_borrowing_line": _row(
                w, line=w.line1,
                note=f"Order-back: ZZT-DON line 446 lent 6 to {w.ref} line 1",
            ),
            # The location form, on the borrowing line.
            "location_form": _row(
                w, line=w.line1,
                note=f"Order-back: {w.donor_bin.warehouse_code} lent 6 to {w.ref} line 1",
            ),
        }
        before = {name: _snapshot(r) for name, r in untouched.items()}
        with company_scope(db, frozenset({w.company_id})):
            fixes = script.plan(db)
            report = script.run(db, apply=True)
        db.commit()
        db.expire_all()

        assert [str(_field(f, "row_id")) for f in fixes] == [str(eligible.id)]
        assert report["applied"] == 1
        for name, row in untouched.items():
            db.refresh(row)
            assert _snapshot(row) == before[name], name


def test_an_eligible_looking_row_whose_target_line_cannot_be_resolved_is_skipped_and_reported():
    script = _script()
    with blank_session() as db:
        w = _world_for_script(db)
        orphan = _row(w, line=w.donor_line, target_no=99)
        good = _row(w, line=w.donor_line, target_no=1)
        before = _snapshot(orphan)
        with company_scope(db, frozenset({w.company_id})):
            fixes = script.plan(db)
            report = script.run(db, apply=True)
        db.commit()
        db.expire_all()
        db.refresh(orphan)

        assert [str(_field(f, "row_id")) for f in fixes] == [str(good.id)]
        assert [str(s["row_id"]) for s in report["skipped"]] == [str(orphan.id)], report
        assert report["applied"] == 1
        assert _snapshot(orphan) == before, "left exactly as it was"


# --------------------------------------------------------------------------- dry run / idempotent


def test_dry_run_changes_nothing():
    script = _script()
    with blank_session() as db:
        w = _world_for_script(db)
        row = _row(w, line=w.donor_line, target_no=1)
        before = _snapshot(row)
        with company_scope(db, frozenset({w.company_id})):
            report = script.run(db, apply=False)
        db.commit()
        db.expire_all()
        db.refresh(row)

        assert len(report["fixes"]) == 1, report
        assert report["applied"] == 0
        assert _snapshot(row) == before


def test_a_second_apply_changes_nothing():
    script = _script()
    with blank_session() as db:
        w = _world_for_script(db)
        row = _row(w, line=w.donor_line, target_no=1)
        with company_scope(db, frozenset({w.company_id})):
            first = script.run(db, apply=True)
        db.commit()
        db.expire_all()
        db.refresh(row)
        after_first = _snapshot(row)

        with company_scope(db, frozenset({w.company_id})):
            second = script.run(db, apply=True)
        db.commit()
        db.expire_all()
        db.refresh(row)

        assert first["applied"] == 1
        assert second["applied"] == 0, second
        assert second["fixes"] == []
        assert _snapshot(row) == after_first


# --------------------------------------------------------------------------- CLI


def test_main_defaults_to_a_dry_run_and_apply_writes(monkeypatch):
    script = _script()
    with blank_session() as db:
        w = _world_for_script(db)
        row = _row(w, line=w.donor_line, target_no=1)
        before = _snapshot(row)
        monkeypatch.setattr(script, "SessionLocal", lambda: _NoCloseSession(db))

        assert script.main([]) == 0
        db.expire_all()
        db.refresh(row)
        assert _snapshot(row) == before, "no --apply, no write"

        assert script.main(["--apply"]) == 0
        db.expire_all()
        db.refresh(row)
        assert str(row.so_line_id) == str(w.line1.id)
        assert row.delivery_date == BORROWER_LINE_1_DATE


# --------------------------------------------------------------------------- fix round 1 (S3)


def test_a_donor_line_row_with_no_borrow_allocation_behind_it_is_skipped_with_a_reason():
    """S3 (a): a step-3 document borrow writes no allocation, so its donor-side remainder
    row has no borrow behind it on the borrowing line and is not this script's to move."""
    script = _script()
    with blank_session() as db:
        w = _world_for_script(db)
        project = w.donor.project_id
        from app.models.projects import Project

        other, other_line, _c, _cl = _seed_line(
            db, w.company_id, db.get(Project, project), w.product, w.donor_bin,
            qty_ordered="50", required_date=DONOR_DATE, line_no=7,
            so_number=f"ZZT-SPO{_uid()[:5]}",
        )
        remainder = _row(
            w, line=other_line,
            note=f"Order-back: ZZT-SPO lent 6 to {w.ref} line 1",
        )
        good = _row(w, line=w.donor_line, target_no=1)
        before = _snapshot(remainder)
        with company_scope(db, frozenset({w.company_id})):
            report = script.run(db, apply=True)
        db.commit()
        db.expire_all()
        db.refresh(remainder)

        assert [str(_field(f, "row_id")) for f in report["fixes"]] == [str(good.id)]
        skipped = {str(s["row_id"]): s for s in report["skipped"]}
        assert str(remainder.id) in skipped, report["skipped"]
        assert skipped[str(remainder.id)].get("reason"), skipped
        assert _snapshot(remainder) == before


def test_a_row_whose_target_line_carries_another_item_is_skipped():
    """S3 (b): the borrowing line found by number has to be the same item."""
    script = _script()
    with blank_session() as db:
        w = _world_for_script(db)
        odd = _row(w, line=w.donor_line, target_no=1, item_code="ZZT-NOT-THIS")
        before = _snapshot(odd)
        with company_scope(db, frozenset({w.company_id})):
            report = script.run(db, apply=True)
        db.commit()
        db.expire_all()
        db.refresh(odd)

        assert report["fixes"] == []
        assert [str(s["row_id"]) for s in report["skipped"]] == [str(odd.id)]
        assert _snapshot(odd) == before


def test_apply_writes_one_audit_row_per_moved_row_with_what_it_was_and_is():
    """S3 (e): the move is reversible from the audit log alone."""
    from app.models.audit import AuditLog

    script = _script()
    with blank_session() as db:
        w = _world_for_script(db)
        row = _row(w, line=w.donor_line, target_no=2)
        old_note = row.note
        with company_scope(db, frozenset({w.company_id})):
            script.run(db, apply=True)
        db.commit()
        audits = db.query(AuditLog).filter(AuditLog.entity_id == str(row.id)).all()
        audits = [a for a in audits if (a.old_values or {}).get("so_line_id")]

        assert len(audits) == 1, [(a.action, a.old_values, a.new_values) for a in audits]
        audit = audits[0]
        assert audit.old_values["so_line_id"] == str(w.donor_line.id)
        assert audit.new_values["so_line_id"] == str(w.line2.id)
        assert audit.old_values["delivery_date"] == DONOR_DATE.isoformat()
        assert audit.new_values["delivery_date"] == BORROWER_LINE_2_DATE.isoformat()
        assert audit.old_values["note"] == old_note
        assert audit.new_values["note"].startswith(f"Order-back: {w.donor_bin.warehouse_code}")
        assert str(audit.company_id) == str(w.company_id)
