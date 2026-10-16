"""Dry run of the dealer targets Excel import against a local database (lane SALES-TARGET-DEALER).

For crew, on the owner's Mac: the real Sorento and Mocha files never leave it. This runs the
same import the screen's Test runs, in a transaction that is always rolled back, and prints the
per-row report. `--masked` prints only the rows that are not representable, as a reason
category plus the SHAPE of any quoted cell: digits become 9, and every word that is not a
layout keyword (RM, Additional, A/C, POINT, Q1, a month...) becomes x's, so no code, dealer or
account name survives and the output can be pasted on the PR.

    venv/bin/python -m scripts.dealer_targets_dry_run "Sorento targets.xlsx" --company SRT --year 2026
    venv/bin/python -m scripts.dealer_targets_dry_run "Mocha targets.xlsx" --company MCH --masked
"""
from __future__ import annotations

import argparse
import re
import sys

from sqlalchemy import text


#: Words a masked cell keeps, because they are the layout, not the data.
_KEEP = {
    "rm", "k", "m", "additional", "add", "addl", "a", "c", "ac", "i", "ii", "iii", "iv", "v",
    "point", "points", "pts", "q", "yearly", "year", "annual", "to", "nil", "na", "n", "and",
    "jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
    "quarterly", "monthly", "rate", "target", "incentive", "threshold", "tier", "period",
}

_CATEGORIES = (
    ("not read: no DEALER NAME", "SHEET_NOT_READ"),
    ("no dealer name or customer code", "NO_NAME_OR_CODE"),
    ("no customer has this name", "NO_NAME_MATCH"),
    ("no ledger of this name has the account", "NAME_WITHOUT_ACCOUNT"),
    ("which has no row in the file", "PAY_TO_ACCOUNT_NOT_FOUND"),
    ("looks wrong: Excel", "RATE_ABOVE_LIMIT"),
    ("more than 200 characters", "CELL_TOO_LONG"),
    ("more than one customer has this name", "NAME_MATCHES_SEVERAL"),
    ("picked customer", "PICK_NOT_A_CUSTOMER"),
    ("skipped in the preview", "SKIPPED"),
    ("is no rate", "NIL_RATE"),
    ("is not a rate", "NOT_A_RATE"),
    ("not an account of this dealer", "RATE_NAMES_UNKNOWN_ACCOUNT"),
    ("has no incentive", "THRESHOLD_WITHOUT_INCENTIVE"),
    ("cannot read the threshold", "THRESHOLD_UNREADABLE"),
    ("name two different accounts to pay", "TWO_PAYEES"),
    ("both every account and named accounts", "MIXED_RATES"),
    ("an incentive column with no", "INCENTIVE_WITHOUT_TIER"),
    ("shares a target with row", "SHARES_A_BROKEN_TARGET"),
    ("PAY TO: no customer", "UNKNOWN_PAY_TO_CODE"),
    ("no customer with code", "UNKNOWN_CODE"),
    ("no customer code on this row", "NO_CODE"),
    ("cannot read the rate", "RATE_UNREADABLE"),
    ("cannot read", "CELL_UNREADABLE"),
    ("which is not an account of this target", "RATE_NAMES_UNKNOWN_ACCOUNT"),
    ("has no rate", "THRESHOLD_WITHOUT_RATE"),
    ("a rate with no threshold", "RATE_WITHOUT_THRESHOLD"),
    ("repeats in one period", "THRESHOLD_REPEATED"),
    ("give different", "ACCOUNTS_DIFFER_IN_ONE_TARGET"),
    ("characters", "CELL_TOO_LONG"),
    ("same threshold in one period", "THRESHOLD_REPEATED"),
    ("names a period this target does not have", "UNKNOWN_PERIOD"),
    ("out of range", "DATES_OUT_OF_RANGE"),
    ("end before they start", "DATES_BACKWARDS"),
)


def _shape(text: str, digits: bool = True) -> str:
    if digits:
        text = re.sub(r"\d", "9", text)
    return re.sub(r"[A-Za-z]+", lambda m: m.group(0) if m.group(0).lower() in _KEEP else "x" * len(m.group(0)), text)


def _differ(names) -> str:
    """What tells several same-name candidates apart, masked: the words each one has that the
    others lack, as shapes (no name survives), or that they differ only in punctuation, or are
    the same name twice (gate 3 diagnosis of NAME_MATCHES_SEVERAL)."""
    norm = [" ".join(n.upper().split()) for n in names]
    if len(set(norm)) == 1:
        return "identical names"
    words = [re.findall(r"[A-Z0-9]+", n) for n in norm]
    if all(w == words[0] for w in words):
        return "differ by: punctuation or spacing only"
    common = set.intersection(*(set(w) for w in words))
    parts = [_shape(" ".join(w for w in ws if w not in common)) or "-" for ws in words]
    return "differ by: " + " | ".join(parts)


def _mask(reason: str) -> str:
    """`CATEGORY` plus the shape of a quoted cell, and nothing else from the file."""
    reason = reason or ""
    category = next((name for needle, name in _CATEGORIES if needle in reason), "OTHER")
    quoted = re.findall(r'"([^"]*)"', reason)
    # The column (a header such as "Q1" or "JAN-DEC'26") before the first colon is layout too.
    column = reason.split(":", 1)[0] if ":" in reason and category not in {"UNKNOWN_CODE", "NO_CODE", "SHARES_A_BROKEN_TARGET", "UNKNOWN_PAY_TO_CODE"} else ""
    parts = [category]
    if column:
        # A header ("Q1 POINT (4%)") is layout: its digits stay, other words are x'd.
        parts.append(f"column {_shape(column, digits=False)}")
    if quoted and category not in {"UNKNOWN_CODE", "UNKNOWN_PAY_TO_CODE", "SHARES_A_BROKEN_TARGET"}:
        parts.append(f'cell "{_shape(quoted[0])}"')
    if category == "OTHER" and '"' not in reason:
        # The target service's own words (no file content in them), digits masked.
        parts.append(re.sub(r"\d", "9", reason.split(":", 1)[-1].strip()))
    return ", ".join(parts)


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
        f"rows {summary['rows']}, mapped {summary['mapped']} (automatically by name "
        f"{summary['auto_mapped']}), needs a pick {summary['needs_pick']}, not representable "
        f"{summary['not_representable']}, targets new {summary['targets_new']}, "
        f"updated {summary['targets_updated']}"
    )
    for note in result["notes"]:
        # A sheet's title could name a dealer: masked like any other file text.
        print(f"note: {_shape(note) if args.masked else note}")
    for row in result["rows"]:
        if args.masked:
            if row["status"] == "mapped":
                continue
            line = f"{_shape(row['sheet'], digits=False)} row {row['row']}: {_mask(row['reason'])}"
            if row["status"] == "needs_pick":
                line += f", {len(row['suggestions'])} suggestion(s)"
                if len(row["suggestions"]) > 1 and "more than one customer" in (row["reason"] or ""):
                    line += f", {_differ([s['name'] for s in row['suggestions']])}"
            print(line)
            continue
        line = f"{row['sheet']} row {row['row']} {row['code'] or '-'}: {row['status']}"
        if row["customer"]:
            line += f" = {row['customer']} ({row['match']})"
        if row["suggestions"]:
            line += " suggestions: " + "; ".join(f"{x['code']} {x['name']}" for x in row["suggestions"])
        if row["reason"]:
            line += f" ({row['reason']})"
        if row["warnings"]:
            line += f" [{'; '.join(row['warnings'])}]"
        if row["target_keys"]:
            line += f" -> {', '.join(row['target_keys'])}"
        print(line)
    # Non-zero on ANY row not mapped, any row waiting on a pick, or any sheet not read (item 8).
    return 0 if dealer_target_import.gate_passes(result) else 1


if __name__ == "__main__":
    raise SystemExit(main())
