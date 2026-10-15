"""Dry run of the dealer targets Excel import against a local database (lane SALES-TARGET-DEALER).

For crew, on the owner's Mac: the real Sorento and Mocha files never leave it. This runs the
same import the screen's Test runs, in a transaction that is always rolled back, and prints the
per-row report. `--masked` prints only the rows that are not representable, with every digit
replaced by 9 and the code and names left out, so the output can be pasted on the PR.

    venv/bin/python -m scripts.dealer_targets_dry_run "Sorento targets.xlsx" --company SRT --year 2026
    venv/bin/python -m scripts.dealer_targets_dry_run "Mocha targets.xlsx" --company MCH --masked
"""
from __future__ import annotations

import argparse
import re
import sys

from sqlalchemy import text


def _mask(value) -> str:
    return re.sub(r"\d", "9", str(value or ""))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file")
    ap.add_argument("--company", required=True, help="company code, e.g. SRT")
    ap.add_argument("--year", type=int, default=None)
    ap.add_argument("--masked", action="store_true", help="only failing rows, digits masked, no codes or names")
    args = ap.parse_args(argv)

    from app.database import SessionLocal
    from app.models.base import company_scope
    from app.services.sales import dealer_target_import, team_service

    with open(args.file, "rb") as fh:
        data = fh.read()
    db = SessionLocal()
    try:
        company_id = db.execute(text("select id from companies where code = :c"), {"c": args.company}).scalar()
        if not company_id:
            print(f"no company with code {args.company}", file=sys.stderr)
            return 2
        year = args.year or team_service._today().year
        with company_scope(db, frozenset({company_id})):
            result = dealer_target_import.run(db, data, year=year, dry_run=True)
    finally:
        db.rollback()
        db.close()

    summary = result["summary"]
    print(
        f"rows {summary['rows']}, mapped {summary['mapped']}, not representable "
        f"{summary['not_representable']}, targets new {summary['targets_new']}, "
        f"updated {summary['targets_updated']}"
    )
    for note in result["notes"]:
        print(f"note: {note}")
    for row in result["rows"]:
        if args.masked:
            if row["status"] == "mapped":
                continue
            print(f"{row['sheet']} row {row['row']}: {_mask(row['reason'])}")
            continue
        line = f"{row['sheet']} row {row['row']} {row['code'] or '-'}: {row['status']}"
        if row["reason"]:
            line += f" ({row['reason']})"
        if row["warnings"]:
            line += f" [{'; '.join(row['warnings'])}]"
        if row["target_keys"]:
            line += f" -> {', '.join(row['target_keys'])}"
        print(line)
    return 0 if summary["not_representable"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
