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
"to <ref> line <m>". `--apply` moves the row to that line, gives it that line's delivery
date, and rewrites the note to the location form ("Order-back: <loc> lent <q> to <ref>
line <m>"). A row whose line cannot be resolved is reported and left alone. `placed`,
`actioned` and `cancelled` rows are never touched, and nothing is deleted.

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
from app.models.project_so import (  # noqa: E402
    INQUIRY_PARTLY_LINKED,
    INQUIRY_RAISED,
    IV_ORDER_BACK,
    OrderInquiry,
    OrderInquiryLink,
    OrderInquiryRow,
    ProjectSalesOrder,
    ProjectSalesOrderLine,
)
from app.services.project_order_inquiry_service import _dec, _qty_str  # noqa: E402

#: The borrowing line, as every borrow order-back note names it ("... to <ref> line <m>").
#: Read only to find WHICH line of the header's own order; the selection never uses it.
_TARGET_LINE_RE = re.compile(r" to .+ line (\d+)")


def _target_line_no(note: Optional[str]) -> Optional[int]:
    match = _TARGET_LINE_RE.search(note or "")
    return int(match.group(1)) if match else None


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
            skipped.append(
                {
                    "row_id": str(row.id),
                    "note": row.note,
                    "reason": "the borrowing line cannot be resolved from the note",
                }
            )
            continue
        reference = order.autocount_doc_no or order.provisional_ref or ""
        fixes.append(
            {
                "row_id": str(row.id),
                "item_code": row.item_code,
                "qty": _qty_str(_dec(row.qty)),
                "stock_location": row.stock_location,
                "from_so_line_id": str(row.so_line_id),
                "to_so_line_id": str(target.id),
                "old_delivery_date": row.delivery_date,
                "new_delivery_date": target.delivery_date,
                "old_note": row.note,
                "new_note": (
                    f"Order-back: {row.stock_location or ''} lent {_qty_str(_dec(row.qty))} "
                    f"to {reference} line {target.line_no}"
                ),
            }
        )
    return fixes, skipped


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
        applied += 1
    db.flush()
    return applied


def run(db: Session, apply: bool = False) -> Dict[str, Any]:
    """Report (and with `apply`, perform) every move. The caller commits."""
    fixes, skipped = _resolve(db)
    for fix in fixes:
        print(
            f"  {fix['row_id']} {fix['item_code']} qty {fix['qty']} at "
            f"{fix['stock_location']}: line {fix['from_so_line_id']} "
            f"({fix['old_delivery_date']}) -> line {fix['to_so_line_id']} "
            f"({fix['new_delivery_date']}); note {fix['new_note']!r}"
        )
    for skip in skipped:
        print(f"  SKIPPED ({skip['reason']}): {skip['row_id']} - note: {skip['note']!r}")
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
