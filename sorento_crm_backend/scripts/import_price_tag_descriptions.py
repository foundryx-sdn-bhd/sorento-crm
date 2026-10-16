#!/usr/bin/env python3
"""Set `products.price_tag_description` from marketing's flyer product-code sheet.

WHY
---
`Sorento_Flyer_Product_Codes.xlsx` (tab Products: No. / Product Code / Description / Flyer
Page(s)) maps each flyer code to the flyer text, which is the price tag description. Owner
decision 15 Oct: set it once with this script. Family rows (SRTWC286-SH) reach their variants
with the matching S-trap / P-trap text; a combo "BF + MR" puts the text on the BF only.

WHAT IT DOES
------------
Reads the first worksheet (header row 1, columns found by name: `Product Code`, `Description`)
and, for the chosen company (--company, default SRT) only:

* EXACT: the code (after code normalisation) names a product -> that product.
* COMBO: `A + B` -> the whole text on A only.
* VARIANT: a product `<sheet code>-<anything>` that is not itself printed in the sheet, for
  the longest sheet code it extends, when the sheet code is a product and finish / seat
  material match. Same rule as the flyer spec reader (product_spec_flyer_ingest._siblings).
* Same code on several rows: format-equal rows are one (first row's text); otherwise the
  longest text wins when it contains every shorter one; otherwise the code is skipped.
* A text printing both an S-Trap and a P-Trap block is cut per product by its stored trap
  type; one that cannot be cut is skipped.

It writes `price_tag_description` and nothing else, in ONE transaction, through the ORM so the
existing audit listener writes one UPDATE row per changed product. Embedding and spec
listeners are not registered here, so nothing is queued.

Every run writes two CSVs next to the xlsx (changes and skipped) and prints their paths.
DRY-RUN unless --apply.

USAGE / PROD STEPS
------------------
    cd sorento_crm_backend
    venv/bin/python scripts/import_price_tag_descriptions.py --file "<path>/Sorento_Flyer_Product_Codes.xlsx"
    #   read the two CSVs next to the xlsx (changes: old -> new; skipped: send to marketing)
    venv/bin/python scripts/import_price_tag_descriptions.py --file "<same path>" --apply
    venv/bin/python scripts/import_price_tag_descriptions.py --file "<same path>"   # must say 0 products to update

Plan: documentation/plans/products/PLAN-pricetag-desc-import-15oct.md.
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Optional, Sequence, TextIO

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import func  # noqa: E402

from app.audit_context import AuditActor, clear_actor, stamp_actor  # noqa: E402
from app.models.base import company_scope  # noqa: E402
from app.models.company import Company  # noqa: E402
from app.models.product import Product  # noqa: E402
from app.models.product_spec import ProductSpecifications  # noqa: E402
from app.services.audit_service import register_audit_listeners  # noqa: E402
from app.services.product_alternative_service import normalize_code  # noqa: E402

JOB_ID = "import_price_tag_descriptions"

# The prod host is shared with the API: refuse a bigger sheet than marketing's (~1,100 rows).
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_ROWS = 20_000

R_NO_CODE = "no code"
R_NO_TEXT = "no description"
R_FRAGMENT = "text starts with part of a code"
R_FORMULA = "text starts with = or @"
R_TOKEN = "text contains {{"
R_COMBO = "combo is not two codes"
R_NOT_FOUND = "code not found in the CRM"
R_CONFLICT = "same code on several rows with different text"
R_BASE_NOT_PRODUCT = "variant of a sheet code that is not a product"
R_FINISH = "variant has a different finish"
R_SEAT = "variant has a different seat material"
R_BOTH = "variant has a different finish and seat material"
R_BOTH_TRAPS = "both traps printed, product has no trap type"
R_TRAP_NOT_PRINTED = "product's trap type is not printed"

CHANGES_COLUMNS = [
    "product_code", "sheet_code", "sheet_rows", "rule", "status",
    "active", "old_description", "new_description",
]
SKIPPED_COLUMNS = ["sheet_code", "sheet_rows", "product_code", "reason", "sheet_texts"]


# --------------------------------------------------------------------------- parse


def clean_text(value) -> str:
    """Cell text as printed: CRLF -> LF, trailing spaces per line and outer blank space removed."""
    if value is None:
        return ""
    text = str(value).replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in text.split("\n")).strip()


def read_sheet(path: str) -> Optional[list[tuple[int, str, str]]]:
    """(excel row, code cell, cleaned text) per data row of the first worksheet, or None when
    the header row lacks `Product Code` or `Description`. Raises on an unreadable file."""
    import openpyxl

    if os.path.getsize(path) > MAX_FILE_BYTES:
        raise ValueError(f"file is over {MAX_FILE_BYTES} bytes")
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb.worksheets[0]
        try:
            ws.reset_dimensions()
        except Exception:
            pass
        rows = ws.iter_rows(values_only=True)
        header = next(rows, None) or ()
        names = {str(h).strip().lower(): i for i, h in enumerate(header) if h is not None}
        if "product code" not in names or "description" not in names:
            return None
        ci, di = names["product code"], names["description"]
        out = []
        for n, row in enumerate(rows, start=2):
            if n - 1 > MAX_ROWS:
                raise ValueError(f"more than {MAX_ROWS} data rows")
            code = row[ci] if ci < len(row) else None
            desc = row[di] if di < len(row) else None
            out.append((n, "" if code is None else str(code).strip(), clean_text(desc)))
        return out
    finally:
        wb.close()


# --------------------------------------------------------------------------- dedupe


def format_key(text: str) -> str:
    """Text compared for duplicates: spacing collapsed, `:` spaced as `: `, `D: ` dropped, no case."""
    t = re.sub(r"\s+", " ", text)
    t = re.sub(r"\s*:\s*", ": ", t).lower()
    return re.sub(r"(?:(?<=\s)|^)d: ", "", t).strip(" :")


def dedupe(entries: list[tuple[int, str, str]]):
    """entries: (row, text, rule) in sheet order -> the chosen (text, rule), or None on a conflict."""
    keyed = [(format_key(text), text, rule) for _, text, rule in entries]
    distinct = list(dict.fromkeys(k for k, _, _ in keyed))
    if len(distinct) == 1:
        return keyed[0][1], keyed[0][2]
    longest = max(distinct, key=len)
    if all(k in longest for k in distinct):
        _, text, rule = next(e for e in keyed if e[0] == longest)
        return text, rule
    return None


# --------------------------------------------------------------------------- trap cut

_TRAP_LINE = re.compile(r"^\s*([sp])[ -]?trap", re.IGNORECASE)


def _trap_of(line: str) -> Optional[str]:
    m = _TRAP_LINE.match(line)
    return m.group(1).lower() if m else None


def cut_trap(text: str, trap_type: Optional[str]):
    """Per-product text: (text, cut, reason). A block runs from its trap line to the next trap
    line, a line starting with `*`, or the end."""
    lines = text.split("\n")
    printed = {t for t in map(_trap_of, lines) if t}
    mine = {"s_trap": "s", "p_trap": "p"}.get(trap_type or "")
    if len(printed) < 2:
        if printed and mine and mine not in printed:
            return None, False, R_TRAP_NOT_PRINTED
        return text, False, None
    if mine is None:
        return None, False, R_BOTH_TRAPS
    kept, dropping = [], False
    for line in lines:
        t = _trap_of(line)
        if t is not None:
            dropping = t != mine
        elif line.lstrip().startswith("*"):
            dropping = False
        if not dropping:
            kept.append(line)
    return "\n".join(kept), True, None


# --------------------------------------------------------------------------- plan


def build_groups(rows):
    """Split sheet rows into row-level skips and per-sheet-code groups.

    Returns (skips, groups, printed, skipped_first): skips are dicts for the skipped CSV,
    groups maps the normalised first code to {code, rows, entries}, printed is every normalised
    code printed in the code column (either side of a combo), skipped_first maps a first code
    seen only on skipped rows to {rows, texts}."""
    skips, groups, printed, skipped_first = [], {}, set(), {}

    def skip(n, code, text, reason):
        skips.append({"sheet_code": code, "rows": [n], "product_code": "", "reason": reason,
                      "texts": [(n, text)] if text else []})

    for n, code, text in rows:
        parts = [p.strip() for p in code.split("+") if p.strip()]
        printed.update(normalize_code(p) for p in parts)
        reason = (
            R_NO_CODE if not parts
            else R_NO_TEXT if not text
            else R_FRAGMENT if text[0] in "+-"
            else R_FORMULA if text[0] in "=@"
            else R_TOKEN if "{{" in text
            else R_COMBO if len(parts) > 2
            else None
        )
        if reason:
            skip(n, code, text, reason)
            if parts:
                s = skipped_first.setdefault(normalize_code(parts[0]), {"rows": [], "texts": []})
                s["rows"].append(n)
                s["texts"].append((n, text)) if text else None
        else:
            key = normalize_code(parts[0])
            g = groups.setdefault(key, {"code": key, "rows": [], "entries": []})
            g["rows"].append(n)
            g["entries"].append((n, text, "combo" if len(parts) == 2 else "exact"))
    return skips, groups, printed, skipped_first


def _spec_value(specs, product_id, key):
    values = specs.get(product_id) or {}
    return ((values.get(key) or {}).get("value")) or None


def make_plan(db, rows, company_id):
    """Everything the sheet says, laid over the company's products. Writes nothing."""
    skips, groups, printed, skipped_first = build_groups(rows)
    changes = []

    with company_scope(db, frozenset({company_id})):
        prods = {}
        for pid, code, active, desc in db.query(
            Product.id, Product.product_code, Product.is_active, Product.price_tag_description
        ).filter(Product.company_id == company_id):
            norm = normalize_code(code)
            if norm and norm not in prods:
                prods[norm] = {"id": pid, "code": code, "active": bool(active), "desc": desc or ""}
        # One query for every spec of the company, never one per product.
        specs = {
            pid: values
            for pid, values in db.query(ProductSpecifications.product_id, ProductSpecifications.values)
            .join(Product, Product.id == ProductSpecifications.product_id)
            .filter(Product.company_id == company_id)
        }

    state = {}  # group key -> "ok" | "conflict" | "missing"
    chosen = {}
    for key, g in groups.items():
        g["texts"] = [(n, t) for n, t, _ in g["entries"]]
        if key not in prods:
            state[key] = "missing"
            skips.append({"sheet_code": key, "rows": g["rows"], "product_code": "",
                          "reason": R_NOT_FOUND, "texts": g["texts"]})
            continue
        pick = dedupe(g["entries"])
        if pick is None:
            state[key] = "conflict"
            skips.append({"sheet_code": key, "rows": g["rows"], "product_code": "",
                          "reason": R_CONFLICT, "texts": g["texts"]})
            continue
        state[key] = "ok"
        chosen[key] = pick

    def place(prod, key, rule, text):
        g = groups[key]
        new_text, cut, reason = cut_trap(text, _spec_value(specs, prod["id"], "trap_type"))
        if reason:
            skips.append({"sheet_code": key, "rows": g["rows"], "product_code": prod["code"],
                          "reason": reason, "texts": g["texts"]})
            return
        changes.append({
            "product": prod, "sheet_code": key, "rows": g["rows"],
            "rule": rule + ("+trap" if cut else ""), "new": new_text,
            "status": "unchanged" if new_text == prod["desc"] else "update",
        })

    for key, (text, rule) in chosen.items():
        place(prods[key], key, rule, text)

    # Variants: the longest printed code a product extends is its base.
    for norm, prod in prods.items():
        if norm in printed:
            continue
        bases = [c for c in printed if norm.startswith(c + "-")]
        base = max(bases, key=len) if bases else None
        if base in skipped_first and base not in groups:
            # The base is printed only on skipped rows; a base printed only as a combo
            # partner is in neither dict and its variants are left alone.
            sf = skipped_first[base]
            skips.append({"sheet_code": base, "rows": sf["rows"], "product_code": prod["code"],
                          "reason": f"variant of {base}, which was skipped", "texts": sf["texts"]})
            continue
        if base not in groups:
            continue
        g = groups[base]

        def vskip(reason):
            skips.append({"sheet_code": base, "rows": g["rows"], "product_code": prod["code"],
                          "reason": reason, "texts": g["texts"]})

        if state[base] == "missing":
            vskip(R_BASE_NOT_PRODUCT)
        elif state[base] == "conflict":
            vskip(f"variant of {base}, which was skipped")
        else:
            base_id = prods[base]["id"]
            fin = _spec_value(specs, prod["id"], "finish") != _spec_value(specs, base_id, "finish")
            seat = (_spec_value(specs, prod["id"], "seat_material")
                    != _spec_value(specs, base_id, "seat_material"))
            if fin or seat:
                vskip(R_BOTH if fin and seat else R_FINISH if fin else R_SEAT)
            else:
                place(prod, base, "variant", chosen[base][0])

    skips.sort(key=lambda s: s["rows"][0])
    return {"changes": changes, "skips": skips}


# --------------------------------------------------------------------------- apply / report


def apply_updates(db, the_plan, company_id) -> int:
    """Set price_tag_description through the ORM (the audit listener writes the rows)."""
    register_audit_listeners()
    stamp_actor(AuditActor(actor_type="system", job_id=JOB_ID), db=db)
    new_by_id = {c["product"]["id"]: c["new"] for c in the_plan["changes"] if c["status"] == "update"}
    ids = list(new_by_id)
    with company_scope(db, frozenset({company_id})):
        for i in range(0, len(ids), 500):
            for product in db.query(Product).filter(Product.id.in_(ids[i:i + 500])):
                product.price_tag_description = new_by_id[product.id]
        db.flush()
    return len(ids)


def _one_line(text: str) -> str:
    return text.replace("\n", " / ") if text else "(empty)"


def _rows_cell(rows) -> str:
    return ", ".join(str(n) for n in rows)


def _safe(cell):
    """A cell a spreadsheet would run as a formula gets a leading quote (CSV only)."""
    if isinstance(cell, str) and cell[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + cell
    return cell


def write_reports(xlsx: Path, the_plan, mode: str):
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    changes_path = xlsx.with_name(f"{xlsx.stem}-price-tag-changes-{mode}-{stamp}.csv")
    skipped_path = xlsx.with_name(f"{xlsx.stem}-price-tag-skipped-{mode}-{stamp}.csv")
    with open(changes_path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(CHANGES_COLUMNS)
        for c in the_plan["changes"]:
            p = c["product"]
            w.writerow(_safe(v) for v in [p["code"], c["sheet_code"], _rows_cell(c["rows"]),
                       c["rule"], c["status"], p["active"], p["desc"], c["new"]])
    with open(skipped_path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(SKIPPED_COLUMNS)
        for s in the_plan["skips"]:
            texts = "\n\n".join(f"row {n}: {t}" for n, t in s["texts"])
            w.writerow(_safe(v) for v in [s["sheet_code"], _rows_cell(s["rows"]),
                       s["product_code"], s["reason"], texts])
    return changes_path, skipped_path


def _summary(the_plan, out: TextIO) -> int:
    updates = [c for c in the_plan["changes"] if c["status"] == "update"]
    for c in updates:
        out.write(f"{c['rule']}  {c['product']['code']}  {_one_line(c['product']['desc'])}"
                  f"  ->  {_one_line(c['new'])}\n")
    out.write(f"\nupdates: {len(updates)}\n")
    for rule, n in sorted(Counter(c["rule"] for c in updates).items()):
        out.write(f"  by rule {rule}: {n}\n")
    out.write(f"unchanged: {sum(1 for c in the_plan['changes'] if c['status'] == 'unchanged')}\n")
    out.write(f"skipped: {len(the_plan['skips'])}\n")
    for reason, n in sorted(Counter(s["reason"] for s in the_plan["skips"]).items()):
        out.write(f"  {reason}: {n}\n")
    return len(updates)


def main(argv: Optional[Sequence[str]] = None, db=None, out: Optional[TextIO] = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--file", required=True, help="the flyer product-code xlsx")
    parser.add_argument("--company", default="SRT", help="company code (default SRT)")
    parser.add_argument("--apply", action="store_true", help="write the descriptions (default: dry run)")
    args = parser.parse_args(list(argv) if argv is not None else None)
    out = out or sys.stdout

    try:
        rows = read_sheet(args.file)
    except Exception as exc:
        out.write(f"cannot read {args.file}: {exc}\n")
        return 1
    if rows is None:
        out.write("the first worksheet needs the headers 'Product Code' and 'Description' in row 1\n")
        return 1

    owns_session = db is None
    if owns_session:
        from app.database import SessionLocal

        db = SessionLocal()
    try:
        with company_scope(db, None):
            company = db.query(Company).filter(func.upper(Company.code) == args.company.upper()).first()
            company_id = company.id if company else None
        if company_id is None:
            out.write(f"unknown company code: {args.company}\n")
            return 1

        the_plan = make_plan(db, rows, company_id)
        n = _summary(the_plan, out)
        try:
            if args.apply:
                apply_updates(db, the_plan, company_id)
            # Reports first: a report that cannot be written must not leave a commit behind.
            changes_path, skipped_path = write_reports(
                Path(args.file), the_plan, "apply" if args.apply else "dryrun"
            )
            if args.apply:
                db.commit()
            else:
                db.rollback()
        except Exception as exc:
            db.rollback()
            out.write(f"FAILED, nothing written: {exc}\n")
            return 1
        finally:
            clear_actor(db)
        out.write(f"changes report: {changes_path}\n")
        out.write(f"skipped report: {skipped_path}\n")
        if args.apply:
            out.write(f"{n} products updated.\n")
        else:
            out.write(f"DRY RUN: {n} products to update. Run with --apply to write them.\n")
        return 0
    finally:
        if owns_session:
            db.close()


if __name__ == "__main__":
    sys.exit(main())
