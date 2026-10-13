"""How AC-CANCEL-CRM's matching (R2) would decide a set of AutoCount Cancel SO lines.

Owner ask (13 Oct): report how many 2026 Sorento XS lines hit the fallback and how many stay
unmatched after the Q2 (a) tie-break. Runs the SAME rules as the ingest
(`app.services.autocount_cancel_matching.resolve_group`) over CSV extracts of AutoCount itself,
so it needs no CRM database. Prints counts only, never a row.

Extracts (header row, these column names; extra columns ignored):
  xs.csv  DocKey, DocNo, DocDate, Cancelled, DtlKey, ItemCode, Location, Qty, FromDocNo,
          FromDocDtlKey                      (XS joined to XSDTL)
  so.csv  DocNo, DtlKey, Seq, ItemCode, Location, Qty, TransferedQty   (SO joined to SODTL)
  do.csv  FromDocNo, FromDocDtlKey, Qty      (DODTL lines transferred from an SO; DO not cancelled)

  python -m scripts.autocount_cancel_match_report xs.csv so.csv do.csv --year 2026
"""
from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from decimal import Decimal

from app.services.autocount_cancel_matching import CancelIn, CandidateIn, resolve_group


def _d(value) -> Decimal:
    text = str(value or "").strip()
    return Decimal(text) if text else Decimal("0")


def _key(value):
    text = str(value or "").strip()
    try:
        number = int(float(text))
    except ValueError:
        return None
    return number if number > 0 else None


def _rows(path):
    with open(path, newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def report(xs_path: str, so_path: str, do_path: str, year: int | None) -> Counter:
    so_lines = _rows(so_path)
    by_key = {(r["DocNo"], _key(r["DtlKey"])): r for r in so_lines}
    so_docs = {r["DocNo"] for r in so_lines}
    moved: dict[tuple, Decimal] = defaultdict(Decimal)
    for r in _rows(do_path):
        moved[(r["FromDocNo"], _key(r["FromDocDtlKey"]))] += _d(r["Qty"])

    xs = [r for r in _rows(xs_path) if str(r.get("Cancelled", "F")).strip().upper() != "T"]
    keyed_on: dict[tuple, Decimal] = defaultdict(Decimal)
    counts: Counter = Counter()
    in_scope = (lambda r: str(r["DocDate"])[:4] == str(year)) if year else (lambda r: True)
    groups: dict[tuple, list] = defaultdict(list)
    for r in xs:
        key = _key(r["FromDocDtlKey"])
        doc = r["FromDocNo"]
        if key is not None:
            target = by_key.get((doc, key))
            if target is not None:
                keyed_on[(doc, key)] += _d(r["Qty"])
                outcome = "matched:line_key"
            else:
                outcome = ("unmatched:dangling_line_key" if doc in so_docs
                           else "waiting:order_not_found")
            if in_scope(r):
                counts[outcome] += 1
            continue
        if not doc or doc not in so_docs:
            if in_scope(r):
                counts["unmatched:no_source_doc" if not doc else "waiting:order_not_found"] += 1
        else:
            groups[(doc, r["ItemCode"])].append(r)

    for (doc, item), members in groups.items():
        candidates = [
            CandidateIn(
                f"{doc}:{c['DtlKey']}",
                (int(_d(c["Seq"])), _key(c["DtlKey"]) or 0),
                c.get("Location"),
                _d(c["TransferedQty"]) - moved[(doc, _key(c["DtlKey"]))]
                - keyed_on[(doc, _key(c["DtlKey"]))],
            )
            for c in so_lines if c["DocNo"] == doc and c["ItemCode"] == item
        ]
        cancels = [CancelIn(f"{r['DocKey']}:{r['DtlKey']}", _d(r["Qty"]), r.get("Location"),
                            (str(r["DocDate"]), r["DocNo"], _key(r["DtlKey"]) or 0))
                   for r in members]
        outcomes = resolve_group(cancels, candidates)
        for r in members:
            if in_scope(r):
                o = outcomes[f"{r['DocKey']}:{r['DtlKey']}"]
                counts[f"{'matched' if o.matched else 'unmatched'}:{o.reason}"] += 1
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("xs")
    parser.add_argument("so")
    parser.add_argument("do")
    parser.add_argument("--year", type=int, default=None)
    args = parser.parse_args()
    counts = report(args.xs, args.so, args.do, args.year)
    total = sum(counts.values())
    print(f"XS lines in scope: {total}")
    for key, value in sorted(counts.items()):
        print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
