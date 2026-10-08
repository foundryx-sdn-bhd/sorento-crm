#!/usr/bin/env python3
"""Re-point open borrow order-backs from the donor's line to the line that borrowed.

WHY THIS EXISTS
---------------
`PLAN-borrow-location-only-8oct.md` B6 / D6 (owner, 8 Oct 2026). Before the switch, a
borrow from another order raised its ORDER_BACK row on the DONOR's line, at the donor's
date, under the BORROWER's Order Inquiry. The worklist then read the donor's SO qty and
date, and PO/SPO linking, which matches the row's `so_line_id`, never found the PO bought
for the borrowing line ("Supplier not linked"). Confirm now writes the row on the
borrowing line; this script moves the rows already raised the old way.

WHAT IT DOES
------------
Selects, structurally and never by a note regex, every ORDER_BACK row that is:

* `raised` or `partly_linked`, with NO `order_inquiry_links` and no `covered_by`
  (purchasing has not touched it), and
* hung on a line of a DIFFERENT project sales order than its own OI header's.

The borrowing line is the header's sales order's line whose `line_no` is the note's
"to <ref> line <m>". A row is moved only when that line holds a borrow allocation naming
the row's own line as its donor (`donor_impact_snapshot.donor_core_line_id`), which is
what a step-2 / group borrow confirm writes; a step-3 document borrow writes none, so its
donor-side remainder row is reported and left. The line must also carry the row's item,
and row, order and line must share one company.

`--apply` moves the row to that line, gives it the date confirm uses (the core line's
required date, else the line's delivery date), rewrites the note to the location form
("Order-back: <loc> lent <q> to <ref> line <m>"), and writes one audit row per move with
the old and new line, date and note, so it can be reversed. Every other row is reported
with why and left alone. `placed`, `actioned` and `cancelled` rows are never touched, and
nothing is deleted.

SAFETY / IDEMPOTENCY
---------------------
A moved row sits on its own header's sales order, so it no longer matches and a second run
reports nothing for it. `--dry-run` is the DEFAULT. Nothing is written without `--apply`.

Run from sorento_crm_backend/:
    venv/bin/python scripts/fix_borrow_order_back_rows.py
    venv/bin/python scripts/fix_borrow_order_back_rows.py --apply
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

# Allow `from app.*` imports when invoked from the backend directory.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.orm import Session  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models.base import set_company_scope  # noqa: E402
from app.models.order import SalesOrderLine  # noqa: E402
from app.models.product import Product  # noqa: E402
from app.models.project_so import (  # noqa: E402
    INQUIRY_PARTLY_LINKED,
    INQUIRY_RAISED,
    IV_ORDER_BACK,
    OrderInquiry,
    OrderInquiryLink,
    OrderInquiryRow,
    ProjectSalesOrder,
    ProjectSalesOrderLine,
    SOLineAllocation,
)
from app.services.audit_service import log_audit  # noqa: E402
from app.services.project_order_inquiry_service import _dec, _qty_str  # noqa: E402

#: The borrowing line, as every borrow order-back note names it ("... lent <q> to <ref>
#: line <m>"), read off the note's FIRST segment only: before an " (agent X)" tail and
#: before any "; ..." a later pass appended. Read only to find WHICH line of the header's
#: own order; the selection never uses it.
_TARGET_LINE_RE = re.compile(r" lent \S+ to .+ line (\d+)\s*$")


def _target_line_no(note: Optional[str]) -> Optional[int]:
    head = (note or "").split(";", 1)[0].split(" (agent", 1)[0]
    match = _TARGET_LINE_RE.search(head)
    return int(match.group(1)) if match else None


def _reference(order: Any) -> str:
    return (order.autocount_doc_no or order.provisional_ref or "") if order else ""


def _candidates(
    db: Session,
) -> List[Tuple[OrderInquiryRow, ProjectSalesOrder]]:
    """Every open, unlinked, uncovered ORDER_BACK row on another order's line, with the
    header's own sales order. Oldest first, so a report reads in the order rows were made."""
    has_link = (
        db.query(OrderInquiryLink.id)
        .filter(OrderInquiryLink.row_id == OrderInquiryRow.id)
        .exists()
    )
    rows = (
        db.query(OrderInquiryRow, ProjectSalesOrder)
        .join(OrderInquiry, OrderInquiry.id == OrderInquiryRow.order_inquiry_id)
        .join(ProjectSalesOrderLine, ProjectSalesOrderLine.id == OrderInquiryRow.so_line_id)
        .join(ProjectSalesOrder, ProjectSalesOrder.id == OrderInquiry.project_sales_order_id)
        .filter(
            OrderInquiryRow.verb == IV_ORDER_BACK,
            OrderInquiryRow.state.in_((INQUIRY_RAISED, INQUIRY_PARTLY_LINKED)),
            ~has_link,
            ProjectSalesOrderLine.project_sales_order_id
            != OrderInquiry.project_sales_order_id,
        )
        .order_by(OrderInquiryRow.created_at.asc(), OrderInquiryRow.id.asc())
        .all()
    )
    return [(row, order) for row, order in rows if not (row.covered_by or "").strip()]


def _resolve(
    db: Session,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """(fixes, skipped) for the rows `_candidates` finds."""
    fixes: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    for row, order in _candidates(db):
        donor_line = db.get(ProjectSalesOrderLine, row.so_line_id)
        donor_order = db.get(ProjectSalesOrder, donor_line.project_sales_order_id)
        report = {
            "row_id": str(row.id),
            "item_code": row.item_code,
            "qty": _qty_str(_dec(row.qty)),
            "stock_location": row.stock_location,
            "from_so": _reference(donor_order),
            "from_line_no": donor_line.line_no,
            "to_so": _reference(order),
            "note": row.note,
        }

        def skip(reason: str) -> None:
            skipped.append({**report, "reason": reason})

        line_no = _target_line_no(row.note)
        target = (
            db.query(ProjectSalesOrderLine)
            .filter(
                ProjectSalesOrderLine.project_sales_order_id == order.id,
                ProjectSalesOrderLine.line_no == line_no,
            )
            .first()
            if line_no is not None
            else None
        )
        if target is None:
            skip("the borrowing line cannot be resolved from the note")
            continue
        if not (str(row.company_id) == str(order.company_id) == str(target.company_id)):
            skip("the row, its order and the borrowing line are not one company")
            continue
        item = db.get(Product, target.product_id) if target.product_id else None
        if item is None or (item.product_code or "") != (row.item_code or ""):
            skip("the borrowing line carries a different item")
            continue
        borrowed = (
            db.query(SOLineAllocation.id)
            .filter(
                SOLineAllocation.so_line_id == target.id,
                SOLineAllocation.donor_impact_snapshot["donor_core_line_id"].astext
                == str(donor_line.core_sales_order_line_id or ""),
            )
            .first()
        )
        if borrowed is None:
            skip(
                "no borrow on the borrowing line names this donor line (a step-3 "
                "document borrow's remainder, or not a borrow at all)"
            )
            continue
        core = (
            db.get(SalesOrderLine, target.core_sales_order_line_id)
            if target.core_sales_order_line_id
            else None
        )
        fixes.append(
            {
                **report,
                "to_line_no": target.line_no,
                "from_so_line_id": str(row.so_line_id),
                "to_so_line_id": str(target.id),
                "old_delivery_date": row.delivery_date,
                # The date confirm gives a location order-back (`_location_order_backs`).
                "new_delivery_date": (
                    getattr(core, "required_date", None) or target.delivery_date
                ),
                "old_note": row.note,
                "new_note": (
                    f"Order-back: {row.stock_location or ''} lent {_qty_str(_dec(row.qty))} "
                    f"to {_reference(order)} line {target.line_no}"
                ),
            }
        )
    return fixes, skipped


def _iso(value: Any) -> Optional[str]:
    return value.isoformat() if value is not None else None


def plan(db: Session) -> List[Dict[str, Any]]:
    """Every row the script would move, with where it goes. Writes nothing."""
    return _resolve(db)[0]


def _apply(db: Session, fixes: Sequence[Dict[str, Any]]) -> int:
    rows = {
        str(row.id): row
        for row in db.query(OrderInquiryRow)
        .filter(OrderInquiryRow.id.in_([fix["row_id"] for fix in fixes]))
        .all()
    }
    applied = 0
    for fix in fixes:
        row = rows.get(fix["row_id"])
        if row is None:
            continue
        row.so_line_id = fix["to_so_line_id"]
        row.delivery_date = fix["new_delivery_date"]
        row.note = fix["new_note"]
        # One audit row per move, carrying everything needed to put it back.
        log_audit(
            db,
            "project_order_inquiry_rows",
            str(row.id),
            "UPDATE",
            old_values={
                "so_line_id": fix["from_so_line_id"],
                "delivery_date": _iso(fix["old_delivery_date"]),
                "note": fix["old_note"],
            },
            new_values={
                "so_line_id": fix["to_so_line_id"],
                "delivery_date": _iso(fix["new_delivery_date"]),
                "note": fix["new_note"],
            },
            company_id=row.company_id,
            description="Borrow order-back moved from the donor's line to the borrowing line",
            reason="PLAN-borrow-location-only-8oct.md B6",
        )
        applied += 1
    db.flush()
    return applied


def run(db: Session, apply: bool = False) -> Dict[str, Any]:
    """Report (and with `apply`, perform) every move. The caller commits."""
    fixes, skipped = _resolve(db)
    for fix in fixes:
        print(
            f"  {fix['item_code']} qty {fix['qty']} at {fix['stock_location']}: "
            f"{fix['from_so']} line {fix['from_line_no']} ({_iso(fix['old_delivery_date'])}) "
            f"-> {fix['to_so']} line {fix['to_line_no']} ({_iso(fix['new_delivery_date'])}); "
            f"note {fix['new_note']!r} [row {fix['row_id']}]"
        )
    for skip in skipped:
        print(
            f"  SKIPPED ({skip['reason']}): {skip['item_code']} qty {skip['qty']} at "
            f"{skip['stock_location']} on {skip['from_so']} line {skip['from_line_no']}, "
            f"OI of {skip['to_so']} - note: {skip['note']!r} [row {skip['row_id']}]"
        )
    applied = _apply(db, fixes) if apply and fixes else 0
    return {"fixes": fixes, "applied": applied, "skipped": skipped}


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Write the changes. Without this the script only reports.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Report only (the default; accepted so a run can say so out loud).",
    )
    args = parser.parse_args(argv)
    apply = args.apply and not args.dry_run

    db = SessionLocal()
    # A script has no request and no principal, so the session scope would be UNSET,
    # which is fail-closed and would return no rows at all. `None` is the sanctioned
    # system / all-companies scope.
    set_company_scope(db, None)
    try:
        print(
            "Borrow order-backs on a donor's line "
            f"({'APPLYING' if apply else 'DRY-RUN, nothing is written'}):"
        )
        summary = run(db, apply=apply)
        if apply:
            db.commit()
        print("\n=== summary ===")
        print(f"mode:     {'APPLIED' if apply else 'DRY-RUN (no writes)'}")
        print(f"eligible: {len(summary['fixes'])}")
        print(f"applied:  {summary['applied']}")
        print(f"skipped:  {len(summary['skipped'])}")
        if not apply and summary["fixes"]:
            print("\nRe-run with --apply to write these changes.")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
