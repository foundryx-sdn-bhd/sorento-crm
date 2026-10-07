#!/usr/bin/env python3
"""Fill the delivery date of cancelled sheet-origin sales order lines from the order sheet.

SO-LINE-CHANGE-HISTORY Q5 (owner ruling, 7 Oct 2026). Sales order lines created by the
order inquiry import (`source_system = 'scm_order_inquiry'`) can carry a NULL
`required_date` once they are cancelled: the import service's `_resolve_delivery_date_repairs`
never writes `sales_order_lines.required_date` and it excludes cancelled lines
(project_order_inquiry_import_service.py), so nothing ever fills them in. The 6 Oct 2026
snapshot held 46 such lines: 20 with a single date in the sheet, 4 with several, 22 with none.

The owner wants those dates filled from the monthly order sheet. A line is matched to the
sheet by (SO number, item code), both upper-cased and stripped. It is written only when the
sheet gives exactly ONE distinct date for that pair; several dates (AMBIGUOUS), no date
(UNDATED) and absent from the sheet (UNMATCHED) are listed and never written.

Dry run by default. `--apply` is the only thing that writes:

    python scripts/backfill_sheet_line_required_date.py --sheet PATH            # dry run
    python scripts/backfill_sheet_line_required_date.py --sheet PATH --apply    # write
    python scripts/backfill_sheet_line_required_date.py --sheet PATH --so SO123 # one order

Idempotent: a written line has a date, so the NULL filter no longer selects it.
"""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models.order import SalesOrder, SalesOrderLine  # noqa: E402
from app.models.product import Product  # noqa: E402
from app.services.project_order_inquiry_reader import (  # noqa: E402
    OrderInquiryRow,
    read_order_inquiry,
)

SOURCE_SYSTEM = "scm_order_inquiry"


@dataclass
class BackfillPlan:
    single: list = field(default_factory=list)
    ambiguous: list = field(default_factory=list)
    undated: list = field(default_factory=list)
    unmatched: list = field(default_factory=list)


def _key(so_number, item_code) -> tuple[str, str]:
    return ((so_number or "").strip().upper(), (item_code or "").strip().upper())


def plan_backfill(lines, sheet_rows) -> BackfillPlan:
    """Sort lines (each with `so_number` and `product_code`) by what the sheet says."""
    dates: dict[tuple[str, str], set[date]] = {}
    for row in sheet_rows:
        bucket = dates.setdefault(_key(row.so_number, row.item_code), set())
        if row.delivery_date is not None:
            bucket.add(row.delivery_date)

    plan = BackfillPlan()
    for line in lines:
        found = dates.get(_key(line.so_number, line.product_code))
        if found is None:
            plan.unmatched.append(line)
        elif not found:
            plan.undated.append(line)
        elif len(found) == 1:
            plan.single.append((line, next(iter(found))))
        else:
            plan.ambiguous.append((line, sorted(found)))
    return plan


def _load_lines(db, so_number: str | None) -> list:
    stmt = (
        select(SalesOrderLine, SalesOrder.so_number, Product.product_code)
        .join(SalesOrder, SalesOrder.id == SalesOrderLine.sales_order_id)
        .join(Product, Product.id == SalesOrderLine.product_id)
        .where(
            SalesOrderLine.source_system == SOURCE_SYSTEM,
            SalesOrderLine.required_date.is_(None),
        )
        .order_by(SalesOrder.so_number, Product.product_code, SalesOrderLine.id)
    )
    if so_number:
        stmt = stmt.where(SalesOrder.so_number == so_number.strip())
    lines = []
    for line, so, code in db.execute(stmt).all():
        # Plain attributes read by plan_backfill; not mapped, never persisted.
        line.so_number = so
        line.product_code = code
        lines.append(line)
    return lines


def run(db, sheet_rows, *, apply: bool, so_number: str | None = None) -> BackfillPlan:
    plan = plan_backfill(_load_lines(db, so_number), sheet_rows)
    if apply:
        for line, when in plan.single:
            line.required_date = when
        db.flush()
    return plan


def _print_plan(plan: BackfillPlan) -> None:
    print(f"SINGLE: {len(plan.single)}")
    for line, when in plan.single:
        print(f"  {line.so_number}  {line.product_code}  qty {line.qty_ordered}  {when}")
    print(f"AMBIGUOUS: {len(plan.ambiguous)}")
    for line, dates in plan.ambiguous:
        print(f"  {line.so_number}  {line.product_code}  {', '.join(str(d) for d in dates)}")
    print(f"UNDATED: {len(plan.undated)}")
    for line in plan.undated:
        print(f"  {line.so_number}  {line.product_code}")
    print(f"UNMATCHED: {len(plan.unmatched)}")
    for line in plan.unmatched:
        print(f"  {line.so_number}  {line.product_code}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--sheet", required=True, help="Path to the order sheet (.xlsx).")
    parser.add_argument(
        "--apply", action="store_true", help="Write the dates. Default is dry-run."
    )
    parser.add_argument("--so", dest="so_number", help="Limit to one SO number.")
    args = parser.parse_args()

    with open(args.sheet, "rb") as fh:
        result = read_order_inquiry(fh.read())
    for problem in result.problems:
        print(f"sheet problem: {problem}")

    db = SessionLocal()
    try:
        plan = run(db, result.rows, apply=args.apply, so_number=args.so_number)
        _print_plan(plan)
        if not args.apply:
            print("\nDry run only - nothing written. Re-run with --apply to write.")
            db.rollback()
            return 0
        db.commit()
        print(f"\nWritten: {len(plan.single)} lines.")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
