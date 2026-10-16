"""Count 2026 AutoCount Cancel SO groups by how exactly they can be attributed (AC-CANCEL-CRM).

Owner rule (13 Oct): attribution must be 100% exact, no guess. A group is one (SO, item, location):
its SO lines i with AutoCount TransferedQty T_i, the DO qty D for the group (DO lines naming the SO
in FromDocNo, same item and location) and the key-less cancel qty C (XS lines naming the SO, same
item and location; a keyed XS line, pre Dec 2024, is exact on its own). This script only counts;
it decides nothing and writes nothing.

Buckets, per group:
  keyed_only      every XS line carries its SO line key: exact
  single_line     one SO line in the group: exact, it gets C
  d_zero_exact    D = 0 and C = sum(T): every line's transfer is cancelled, exact per line
  d_zero_mismatch D = 0 but C != sum(T): T holds something else (for example a direct invoice
                  transfer, or a DO the CRM does not hold), so not exact
  undetermined    D > 0 and C > 0 over 2+ lines: which line was cancelled cannot be known
  no_so_line      no SO line of that item and location (SO not here, or location differs)
  do_not_covered  the SO predates the CRM's DO data, so D is unknown (only with --do-from)

Inputs (CSV with header, extra columns ignored):
  groups.csv  so_no, item, loc, so_date, xs_lines, keyed_lines, c_unkeyed, n_lines, sum_t
              (the AutoCount query in the crew-note)
  do.csv      so_no, item, loc, d   (the CRM prod-clone query in the crew-note)

  python -m scripts.autocount_cancel_bucket_report groups.csv do.csv [--do-from 2026-09-01]

Prints counts only, never a row.
"""
from __future__ import annotations

import argparse
import csv
from collections import Counter
from decimal import Decimal


def _d(value) -> Decimal:
    text = str(value or "").strip()
    return Decimal(text) if text else Decimal("0")


def _key(row) -> tuple[str, str, str]:
    return (str(row["so_no"]).strip(), str(row["item"]).strip(),
            str(row.get("loc") or "").strip().upper())


def bucket(group: dict, d: Decimal, do_from: str | None) -> str:
    if int(_d(group["xs_lines"])) == int(_d(group["keyed_lines"])):
        return "keyed_only"
    n_lines = int(_d(group["n_lines"]))
    c = _d(group["c_unkeyed"])
    if n_lines == 0:
        return "no_so_line"
    if n_lines == 1:
        return "single_line"
    if d == 0 and do_from and str(group.get("so_date") or "")[:10] < do_from:
        return "do_not_covered"
    if d == 0:
        return "d_zero_exact" if c == _d(group["sum_t"]) else "d_zero_mismatch"
    return "undetermined"


def report(groups_path: str, do_path: str, do_from: str | None) -> Counter:
    with open(do_path, newline="", encoding="utf-8-sig") as handle:
        do_qty = {_key(r): _d(r["d"]) for r in csv.DictReader(handle)}
    counts: Counter = Counter()
    with open(groups_path, newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            name = bucket(row, do_qty.get(_key(row), Decimal("0")), do_from)
            counts[f"groups:{name}"] += 1
            counts[f"lines:{name}"] += int(_d(row["xs_lines"]))
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("groups")
    parser.add_argument("do")
    parser.add_argument("--do-from", default=None,
                        help="first DocDate the CRM holds DOs for (YYYY-MM-DD)")
    args = parser.parse_args()
    counts = report(args.groups, args.do, args.do_from)
    for kind in ("groups", "lines"):
        total = sum(v for k, v in counts.items() if k.startswith(kind + ":"))
        print(f"{kind}: {total}")
        for key in sorted(k for k in counts if k.startswith(kind + ":")):
            print(f"  {key.split(':', 1)[1]}: {counts[key]}")


if __name__ == "__main__":
    main()
