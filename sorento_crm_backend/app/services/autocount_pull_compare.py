"""Pure comparison functions for the AutoCount pull "Compare with my Excel" tab
(PLAN-autocount-pull-review.md, P11). No database access, no side effects - the
route (`app.api.v1.integrations.autocount_pull`) reads the pull's rows, the
browser posts its own parsed file, and this module is what decides where they
agree and where they do not.

`excel_rows` carry the manual-template column names (`Item Code`, `Description`,
`Desc 2`, `Item Group`, `Item Brand`, `Price`, `Is Active`) - what the browser
parses out of the checker's own workbook, the same shape `ProductService.
bulk_import_products` reads. `pull_rows` carry the RAW FoundryX snapshot-row
shape (`code`/`name`/`description`/`category_code`/`brand_code`/`list_price`/
`is_active`) - AC-CM-2 compares the Excel join against the pull's own
`description` field directly, never a mapped view row.

The Excel-side normalisation calls the manual import's OWN rules (Desc 2 join,
price parse/clamp, Is Active truthy rule, all lifted onto module-level
functions in `product_service.py` for exactly this reason) so this tab and the
manual upload can never quietly disagree on what counts as a match.

Every comparison is STRICT - byte for byte on `description`, exact on the rest
(captain ruling, SR3 fix round): a single-space Excel join against a pull
`description` that carries a real double space IS a reportable difference, not
noise to smooth over. `map_product_row`'s own Desc 2 rule (AC-RV-3) exists so a
downloaded, re-imported file round-trips exactly - a checker's own genuine
export carries its original spacing verbatim (a mismatch here means their file
actually differs), so there is nothing left for this tab to forgive.
"""
from __future__ import annotations

import math
import re
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Optional

from app.services.autocount_compare_mapping import DEFAULT_MAPPINGS
from app.services.product_service import (
    is_active_from_manual_value,
    join_description_and_desc2,
    parse_manual_list_price,
)


def _key(code: Any) -> str:
    return str(code or "").strip().upper()


def _excel_code(row: dict) -> str:
    return str(row.get("Item Code") or "").strip()


def _pull_code(row: dict) -> str:
    return str(row.get("code") or "").strip()


def _excel_description(row: dict) -> str:
    return join_description_and_desc2(row.get("Description") or "", row.get("Desc 2") or "")


def _excel_price(row: dict) -> Decimal:
    try:
        return parse_manual_list_price(row.get("Price"))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0")


def _pull_price(row: dict) -> Decimal:
    try:
        return Decimal(str(row.get("list_price")))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0")


def compare_products(excel_rows: list[dict], pull_rows: list[dict]) -> dict:
    """AC-CM-2: keyed by Item Code, trimmed and case-insensitive.

    Returns a `summary` (`total`/`matched`/`different`, over the INTERSECTION
    of the two sides), `differences` (one entry per differing field, each
    carrying both values), and `only_in_excel` / `only_in_pull` (item codes).
    """
    excel_by_key: dict[str, dict] = {}
    for row in excel_rows:
        key = _key(_excel_code(row))
        if key:
            excel_by_key[key] = row

    pull_by_key: dict[str, dict] = {}
    for row in pull_rows:
        key = _key(_pull_code(row))
        if key:
            pull_by_key[key] = row

    only_in_excel = sorted(_excel_code(excel_by_key[k]) for k in excel_by_key if k not in pull_by_key)
    only_in_pull = sorted(_pull_code(pull_by_key[k]) for k in pull_by_key if k not in excel_by_key)

    common_keys = [k for k in excel_by_key if k in pull_by_key]
    differences: list[dict] = []
    matched = 0

    for key in common_keys:
        excel_row = excel_by_key[key]
        pull_row = pull_by_key[key]
        item_code = _pull_code(pull_row) or _excel_code(excel_row)
        row_diffs: list[tuple[str, Any, Any]] = []

        excel_desc = _excel_description(excel_row)
        pull_desc = pull_row.get("description") or ""
        if excel_desc != pull_desc:
            row_diffs.append(("description", excel_desc, pull_desc))

        excel_group = (excel_row.get("Item Group") or "").strip()
        pull_group = (pull_row.get("category_code") or "").strip()
        if excel_group != pull_group:
            row_diffs.append(("item_group", excel_group, pull_group))

        excel_brand = (excel_row.get("Item Brand") or "").strip()
        pull_brand = (pull_row.get("brand_code") or "").strip()
        if excel_brand != pull_brand:
            row_diffs.append(("item_brand", excel_brand, pull_brand))

        excel_price = _excel_price(excel_row)
        pull_price = _pull_price(pull_row)
        if excel_price != pull_price:
            row_diffs.append(("price", str(excel_price), str(pull_price)))

        excel_active = is_active_from_manual_value(excel_row.get("Is Active"))
        pull_active = bool(pull_row.get("is_active"))
        if excel_active != pull_active:
            row_diffs.append(("is_active", excel_active, pull_active))

        if row_diffs:
            for field, excel_value, pull_value in row_diffs:
                differences.append(
                    {"item_code": item_code, "field": field, "excel": excel_value, "pull": pull_value}
                )
        else:
            matched += 1

    total = len(common_keys)
    return {
        "summary": {"total": total, "matched": matched, "different": total - matched},
        "differences": differences,
        "only_in_excel": only_in_excel,
        "only_in_pull": only_in_pull,
    }


# ============================================================================== stock


def _stock_key(row: dict) -> tuple[str, str]:
    return (
        str(row.get("Item Code") or "").strip().upper(),
        str(row.get("Location") or "").strip().upper(),
    )


def _stock_pair_label(row: dict) -> str:
    code = str(row.get("Item Code") or "").strip()
    location = str(row.get("Location") or "").strip()
    return f"{code}|{location}"


def _stock_qty(row: dict) -> int:
    try:
        return int(float(str(row.get("On Hand Qty"))))
    except (TypeError, ValueError):
        return 0


def compare_stock(excel_rows: list[dict], fed_rows: list[dict]) -> dict:
    """AC-CM-3: both sides in the manual-template shape (`Item Code`/`Item
    Description`/`Location`/`On Hand Qty`), keyed by (Item Code, Location) trimmed
    and case-insensitive, against the FED rows only. On Hand Qty compared as
    integers. `summary` additionally carries `qty_total_excel`/`qty_total_pull` -
    the sum of On Hand Qty over ALL rows of each side, not just the common pairs.
    """
    excel_by_key: dict[tuple[str, str], dict] = {}
    for row in excel_rows:
        key = _stock_key(row)
        if key[0]:
            excel_by_key[key] = row

    pull_by_key: dict[tuple[str, str], dict] = {}
    for row in fed_rows:
        key = _stock_key(row)
        if key[0]:
            pull_by_key[key] = row

    only_in_excel = sorted(
        _stock_pair_label(excel_by_key[k]) for k in excel_by_key if k not in pull_by_key
    )
    only_in_pull = sorted(
        _stock_pair_label(pull_by_key[k]) for k in pull_by_key if k not in excel_by_key
    )

    common_keys = [k for k in excel_by_key if k in pull_by_key]
    differences: list[dict] = []
    matched = 0

    for key in common_keys:
        excel_row = excel_by_key[key]
        pull_row = pull_by_key[key]
        excel_qty = _stock_qty(excel_row)
        pull_qty = _stock_qty(pull_row)
        if excel_qty != pull_qty:
            differences.append({
                "item_code": str(excel_row.get("Item Code") or "").strip(),
                "location": str(excel_row.get("Location") or "").strip(),
                "field": "on_hand_qty", "excel": excel_qty, "pull": pull_qty,
            })
        else:
            matched += 1

    total = len(common_keys)
    return {
        "summary": {
            "total": total, "matched": matched, "different": total - matched,
            "qty_total_excel": sum(_stock_qty(r) for r in excel_rows),
            "qty_total_pull": sum(_stock_qty(r) for r in fed_rows),
        },
        "differences": differences,
        "only_in_excel": only_in_excel,
        "only_in_pull": only_in_pull,
    }


# ===================================================================== delivery orders

#: Money on both sides is compared at two decimals (the sheet prints two).
_MONEY = Decimal("0.01")
_EXCEL_EPOCH = date(1899, 12, 30)


def _excel_day(value: Any) -> Optional[date]:
    """A sheet cell as a calendar day, or None when it carries none. `sheet_to_json` hands a
    date cell over as an Excel serial number (days since 1899-12-30) unless the sheet stored
    text; a typed cell may read `27/09/2026`, `2026-09-27`, `2026-09-27T00:00:00` or
    `20260927`. A vendor `DocDate` comes through the same function."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if not math.isfinite(value) or not 20_000 <= value <= 80_000:
            return None
        return _EXCEL_EPOCH + timedelta(days=int(value))
    text = str(value).strip()
    if not text:
        return None
    if re.fullmatch(r"\d{8}", text):
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    match = re.match(r"^(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})", text)
    if match:
        day, month, year = (int(g) for g in match.groups())
        try:
            return date(year, month, day)
        except ValueError:
            return None
    try:
        return datetime.fromisoformat(text[:19]).date() if "T" in text or " " in text \
            else date.fromisoformat(text[:10])
    except ValueError:
        return None


def window_excel_rows(
    excel_rows: list[dict], from_day: Optional[str], to_day: Optional[str],
    mapping: Optional[dict] = None,
) -> tuple[list[dict], int]:
    """(rows inside the pulled DocDate window, count left out). The macro files hold extra
    days (owner decision 30 Sep, item 4): a row dated outside the window is ignored, never
    reported; a row with no readable date stays in. No window = every row stays. The date is
    the column the `mapping` maps to `doc_date` (default: the Order Listing mapping)."""
    start = _excel_day(from_day) if from_day else None
    end = _excel_day(to_day) if to_day else None
    mapping = mapping or DEFAULT_MAPPINGS["order_listing"]
    kept: list[dict] = []
    ignored = 0
    mapped = [(row, _mapped_row(row, mapping)) for row in excel_rows]
    # When the sheet carries the mapped document number column, a row without one is not a
    # line or a document: the listing's own totals row (GRN-PULL-CRM e2e gap 3: 72 "lines"
    # against AutoCount's 71). The browser's parser leaves a blank cell out of the row, so
    # "without one" is the key absent or blank. It is neither in the window nor ignored
    # outside it; the compares skip it anyway. A sheet with no such column is left alone.
    has_doc_column = any("doc_no" in fields for _, fields in mapped)
    for row, fields in mapped:
        if has_doc_column and not str(fields.get("doc_no") or "").strip():
            continue
        if start is None and end is None:
            kept.append(row)
            continue
        day = fields.get("doc_date")
        if day is None or (start is None or day >= start) and (end is None or day <= end):
            kept.append(row)
        else:
            ignored += 1
    return kept, ignored


def _money_raw(value: Any) -> Optional[Decimal]:
    """A finite, bounded decimal, None when the cell carries no number."""
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        parsed = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    if not parsed.is_finite() or abs(parsed.adjusted()) > _MAX_QTY_EXPONENT:
        return None
    return parsed


def _money(value: Any) -> Optional[Decimal]:
    """Two-decimal money, None when the cell carries no number (a blank stays a blank, and a
    blank against 0.00 is not a difference)."""
    parsed = _money_raw(value)
    if parsed is None:
        return None
    return parsed.quantize(_MONEY, rounding=ROUND_HALF_UP)


def _percent_text(value: Any) -> str:
    """A discount as AutoCount and the sheet both write it: text such as `5%` or `40%+5%`, or
    a bare number read as a percent. Canonical text: a plain percent is its number (`37`), a
    zero reads as blank, anything compound is upper-cased text."""
    if value is None or isinstance(value, bool):
        return ""
    text = str(value).strip()
    if not text:
        return ""
    number = _money(text[:-1] if text.endswith("%") else text)
    if number is not None:
        return "" if number == 0 else f"{number.normalize():f}"
    return text.upper()


def _percent_fraction(value: Any) -> str:
    """A discount the sheet stores as a fraction (0.37 shown as 37%): a number is multiplied
    by 100, text carrying a `%` is read as `percent_text`."""
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, str) and "%" in value:
        return _percent_text(value)
    number = _money_raw(value)
    if number is None:
        return _percent_text(value)
    return _percent_text(number * 100)


def _cancel_flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().upper() in {"T", "Y", "1", "TRUE", "YES", "CANCELLED", "CANCEL"}


def _plain_number(value: Optional[Decimal]):
    if value is None:
        return None
    return int(value) if value == value.to_integral_value() else float(value)


_TRANSFORMS = {
    "text": lambda v: str(v or "").strip(),
    "number": lambda v: _do_qty(v),
    "money": lambda v: _money(v),
    "date": lambda v: _excel_day(v),
    "percent_text": _percent_text,
    "percent_fraction": _percent_fraction,
    "cancel_flag": lambda v: _cancel_flag(v),
}


def _mapped_row(row: dict, mapping: dict) -> dict[str, Any]:
    """One sheet row through the mapping into canonical fields. Headers match trimmed and
    case-insensitive; a mapped column the row does not carry yields no field at all, a blank
    cell yields the transform's own blank. No alias guessing."""
    lowered = {str(k).strip().lower(): v for k, v in row.items()}
    out: dict[str, Any] = {}
    for col in mapping["columns"]:
        header = str(col["excel_header"]).strip().lower()
        if header in lowered:
            out[col["field"]] = _TRANSFORMS[col["transform"]](lowered[header])
    return out


def _do_label(doc_no: Any, item_code: Any, location: Any) -> str:
    return f"{str(doc_no or '').strip()}|{str(item_code or '').strip()}|{str(location or '').strip()}"


#: The largest exponent a DO line quantity may carry before it reads as "not a quantity"
#: (security review B1): `Decimal("1e3000000")` parses in microseconds but `int()` of it
#: runs for minutes under the GIL, and a NaN/Infinity is not a quantity either. Anything a
#: real delivery order could carry sits far below 10^15.
_MAX_QTY_EXPONENT = 15


def _do_qty(value: Any) -> Decimal:
    """A bounded, finite quantity, else 0 - the same "unparseable reads as 0" rule
    `_stock_qty` uses, tightened so a hostile or malformed cell can never cost more
    than a normal one."""
    try:
        parsed = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, TypeError, AttributeError):
        return Decimal("0")
    if not parsed.is_finite() or parsed == 0:
        return Decimal("0")
    if abs(parsed.adjusted()) > _MAX_QTY_EXPONENT:
        return Decimal("0")
    return parsed


def _json_number(value: Decimal):
    """A JSON number for a quantity `_do_qty` already bounded - `int` only for a whole
    value, never for anything with more than `_MAX_QTY_EXPONENT` digits."""
    if value == value.to_integral_value():
        return int(value)
    return float(value)


def _sum_money(current: Optional[Decimal], value: Optional[Decimal]) -> Optional[Decimal]:
    if value is None:
        return current
    return (current or Decimal("0.00")) + value


def compare_delivery_orders(
    excel_rows: list[dict], pull_rows: list[dict], mapping: Optional[dict] = None
) -> dict:
    """AC-DP-32 (the LINES half): the Order Listing macro's `Master` sheet against the pull's raw DO
    records, keyed by (Doc No, Item Code, Location) trimmed and case-insensitive. Each sheet row goes through the `mapping` (default: the
    Order Listing mapping) into canonical fields first; a blank or unmapped cell is not
    compared. Fields (owner Q3): `qty` and `total_ex` summed per key, `unit_price` and
    `discount` from the first line of the key on each side. `pull_rows` are the raw vendor
    records (one per document, `Details[]`); a Details row with no `ItemCode` is not a line,
    the DO ingest's own rule, and a cancelled document (`Cancelled` = T) is skipped because
    the Order Listing leaves it out. Only-in labels are `DOCNO|ITEM|LOCATION`."""
    mapping = mapping or DEFAULT_MAPPINGS["order_listing"]
    mapped_fields = {c["field"] for c in mapping["columns"]}
    # One entry per (Doc No, Item Code, Location) on each side, quantities SUMMED (review
    # S2): the same item can sit twice on one document (two batches), and the sheet and
    # the pull may split it differently. The first row seen keeps the labels.
    excel_by_key: dict[tuple[str, str, str], dict] = {}
    excel_qty: dict[tuple[str, str, str], Decimal] = {}
    excel_total: dict[tuple[str, str, str], Optional[Decimal]] = {}
    for row in excel_rows:
        fields = _mapped_row(row, mapping)
        key = (_key(fields.get("doc_no")), _key(fields.get("item_code")), _key(fields.get("location")))
        if key[0] and key[1]:
            excel_by_key.setdefault(key, fields)
            excel_qty[key] = excel_qty.get(key, Decimal("0")) + (fields.get("qty") or Decimal("0"))
            excel_total[key] = _sum_money(excel_total.get(key), fields.get("total_ex"))

    pull_by_key: dict[tuple[str, str, str], tuple[dict, dict]] = {}
    pull_qty: dict[tuple[str, str, str], Decimal] = {}
    pull_total: dict[tuple[str, str, str], Optional[Decimal]] = {}
    for rec in pull_rows:
        if not isinstance(rec, dict) or _cancel_flag(rec.get("Cancelled")):
            continue
        for line in rec.get("Details") or []:
            if not isinstance(line, dict) or not str(line.get("ItemCode") or "").strip():
                continue
            key = (_key(rec.get("DocNo")), _key(line.get("ItemCode")), _key(line.get("Location")))
            pull_by_key.setdefault(key, (rec, line))
            pull_qty[key] = pull_qty.get(key, Decimal("0")) + _do_qty(line.get("Qty"))
            # The sheet's Total (Ex) is the line before tax: AutoCount's SubTotalExTax when
            # the record carries it, else SubTotal.
            ex_tax = line.get("SubTotalExTax") if line.get("SubTotalExTax") is not None else line.get("SubTotal")
            pull_total[key] = _sum_money(pull_total.get(key), _money(ex_tax))

    only_in_excel = sorted(
        _do_label(f.get("doc_no"), f.get("item_code"), f.get("location"))
        for k, f in excel_by_key.items() if k not in pull_by_key
    )
    only_in_pull = sorted(
        _do_label(rec.get("DocNo"), line.get("ItemCode"), line.get("Location"))
        for k, (rec, line) in pull_by_key.items() if k not in excel_by_key
    )

    common_keys = [k for k in excel_by_key if k in pull_by_key]
    differences: list[dict] = []
    matched = 0
    for key in common_keys:
        rec, line = pull_by_key[key]
        excel_row = excel_by_key[key]
        row_diffs: list[tuple[str, Any, Any]] = []
        if "qty" in mapped_fields and excel_qty[key] != pull_qty[key]:
            row_diffs.append(("qty", _json_number(excel_qty[key]), _json_number(pull_qty[key])))
        excel_price = excel_row.get("unit_price")
        pull_price = _money(line.get("UnitPrice"))
        if excel_price is not None and pull_price is not None and excel_price != pull_price:
            row_diffs.append(("unit_price", _plain_number(excel_price), _plain_number(pull_price)))
        if "discount" in mapped_fields:
            excel_discount = excel_row.get("discount") or ""
            pull_discount = _percent_text(line.get("Discount"))
            if excel_discount != pull_discount:
                row_diffs.append(("discount", excel_discount or None, pull_discount or None))
        if (
            excel_total[key] is not None and pull_total[key] is not None
            and excel_total[key] != pull_total[key]
        ):
            row_diffs.append(("total_ex", _plain_number(excel_total[key]), _plain_number(pull_total[key])))
        if row_diffs:
            for field, excel_value, pull_value in row_diffs:
                differences.append({
                    "item_code": str(line.get("ItemCode") or "").strip(),
                    "doc_no": str(rec.get("DocNo") or "").strip(),
                    "location": str(line.get("Location") or "").strip(),
                    "field": field, "excel": excel_value, "pull": pull_value,
                })
        else:
            matched += 1

    total = len(common_keys)
    return {
        "summary": {"total": total, "matched": matched, "different": total - matched},
        "differences": differences,
        "only_in_excel": only_in_excel,
        "only_in_pull": only_in_pull,
    }


def compare_delivery_order_headers(
    excel_rows: list[dict], pull_rows: list[dict], mapping: Optional[dict] = None
) -> dict:
    """The HEADERS half (owner decision 30 Sep): the Order Tracking macro's `Master` sheet,
    one row per DO, through the `mapping` (default: the Order Tracking mapping), against the
    pull's documents, keyed by document number trimmed and case-insensitive. Fields (owner
    Q3): `doc_date` as a calendar day, `debtor_code`, `cancel` as a flag; a field the mapping
    does not name is not compared. Not compared, by ruling: Created Time, Debtor Name, Agent,
    Remarks CS, Type, and the whole Overall Tracking sheet. Only-in labels are the bare
    document number; a DO the pull did not bring back is a difference to check, never
    something Confirm deletes."""
    mapping = mapping or DEFAULT_MAPPINGS["order_tracking"]
    mapped_fields = {c["field"] for c in mapping["columns"]}
    excel_by_key: dict[str, dict] = {}
    for row in excel_rows:
        fields = _mapped_row(row, mapping)
        key = _key(fields.get("doc_no"))
        if key:
            excel_by_key.setdefault(key, fields)
    pull_by_key: dict[str, dict] = {}
    for rec in pull_rows:
        if isinstance(rec, dict) and _key(rec.get("DocNo")):
            pull_by_key.setdefault(_key(rec.get("DocNo")), rec)

    only_in_excel = sorted(
        str(excel_by_key[k].get("doc_no") or "").strip() for k in excel_by_key if k not in pull_by_key
    )
    only_in_pull = sorted(
        str(pull_by_key[k].get("DocNo") or "").strip() for k in pull_by_key if k not in excel_by_key
    )

    common_keys = [k for k in excel_by_key if k in pull_by_key]
    differences: list[dict] = []
    matched = 0
    for key in common_keys:
        excel_row = excel_by_key[key]
        rec = pull_by_key[key]
        doc_no = str(rec.get("DocNo") or "").strip()
        row_diffs: list[tuple[str, Any, Any]] = []
        excel_day = excel_row.get("doc_date")
        pull_day = _excel_day(rec.get("DocDate"))
        if excel_day is not None and pull_day is not None and excel_day != pull_day:
            row_diffs.append(("doc_date", excel_day.isoformat(), pull_day.isoformat()))
        excel_debtor = _key(excel_row.get("debtor_code"))
        pull_debtor = _key(rec.get("DebtorCode"))
        if excel_debtor and pull_debtor and excel_debtor != pull_debtor:
            row_diffs.append(("debtor_code", excel_row["debtor_code"], str(rec.get("DebtorCode") or "").strip()))
        if "cancel" in mapped_fields:
            excel_cancel = bool(excel_row.get("cancel"))
            pull_cancel = _cancel_flag(rec.get("Cancelled"))
            if excel_cancel != pull_cancel:
                row_diffs.append(("cancel", excel_cancel, pull_cancel))
        if row_diffs:
            for field, excel_value, pull_value in row_diffs:
                differences.append({
                    "item_code": "", "doc_no": doc_no, "location": "",
                    "field": field, "excel": excel_value, "pull": pull_value,
                })
        else:
            matched += 1

    total = len(common_keys)
    return {
        "summary": {"total": total, "matched": matched, "different": total - matched},
        "differences": differences,
        "only_in_excel": only_in_excel,
        "only_in_pull": only_in_pull,
    }


# ================================================================ goods receive notes
def _source_keys(value: Any) -> frozenset[str]:
    """The documents a cell names (one, or a comma list on the listing's `Transfer From`),
    each matched the upload's way (`_spo_match_key`: letters and digits, upper case)."""
    from app.services.procurement_service import _spo_match_key

    return frozenset(
        key for key in (_spo_match_key(part) for part in str(value or "").split(",")) if key
    )


def _qty_text(quantities: list[Decimal]) -> str:
    return ", ".join(str(_json_number(q)) for q in sorted(quantities))


def compare_goods_receive_notes(
    excel_rows: list[dict], pull_rows: list[dict], mapping: Optional[dict] = None
) -> dict:
    """GRN-PULL-CRM plan 1.5, the LINES half: the GRN "DETAIL LISTING" sheet against the
    pull's raw GRN records, keyed by (Doc No, Item Code, Location) trimmed and case-
    insensitive. The file's rows are not in AutoCount's order and one document's rows are
    scattered, so nothing is compared by position: per key, `qty` is a MULTISET (the sorted
    quantities: 106/24 against 24/106 agrees, 2 + 98 against 100 does not), and `source_doc`
    (the sheet's `Our PO No.` = AutoCount's line `FromDocNo`) compares as the set of
    documents named, matched the upload's way. A blank sheet cell is not compared; the totals
    row (no Doc No) is skipped. Only-in labels are `DOCNO|ITEM|LOCATION`."""
    mapping = mapping or DEFAULT_MAPPINGS["grn_detail_listing"]
    mapped_fields = {c["field"] for c in mapping["columns"]}
    excel_by_key: dict[tuple[str, str, str], dict] = {}
    excel_qty: dict[tuple[str, str, str], list[Decimal]] = {}
    excel_source: dict[tuple[str, str, str], set[str]] = {}
    excel_source_text: dict[tuple[str, str, str], set[str]] = {}
    for row in excel_rows:
        fields = _mapped_row(row, mapping)
        key = (_key(fields.get("doc_no")), _key(fields.get("item_code")), _key(fields.get("location")))
        if not (key[0] and key[1]):
            continue
        excel_by_key.setdefault(key, fields)
        excel_qty.setdefault(key, []).append(fields.get("qty") or Decimal("0"))
        excel_source.setdefault(key, set()).update(_source_keys(fields.get("source_doc")))
        if str(fields.get("source_doc") or "").strip():
            excel_source_text.setdefault(key, set()).add(str(fields["source_doc"]).strip())

    pull_by_key: dict[tuple[str, str, str], tuple[dict, dict]] = {}
    pull_qty: dict[tuple[str, str, str], list[Decimal]] = {}
    pull_source: dict[tuple[str, str, str], set[str]] = {}
    pull_source_text: dict[tuple[str, str, str], set[str]] = {}
    for rec in pull_rows:
        if not isinstance(rec, dict):
            continue
        for line in rec.get("Details") or []:
            if not isinstance(line, dict) or not str(line.get("ItemCode") or "").strip():
                continue
            key = (_key(rec.get("DocNo")), _key(line.get("ItemCode")), _key(line.get("Location")))
            pull_by_key.setdefault(key, (rec, line))
            pull_qty.setdefault(key, []).append(_do_qty(line.get("Qty")))
            named = line.get("FromDocNo") or line.get("OurPONo")
            pull_source.setdefault(key, set()).update(_source_keys(named))
            if str(named or "").strip():
                pull_source_text.setdefault(key, set()).add(str(named).strip())

    only_in_excel = sorted(
        _do_label(f.get("doc_no"), f.get("item_code"), f.get("location"))
        for k, f in excel_by_key.items() if k not in pull_by_key
    )
    only_in_pull = sorted(
        _do_label(rec.get("DocNo"), line.get("ItemCode"), line.get("Location"))
        for k, (rec, line) in pull_by_key.items() if k not in excel_by_key
    )

    common_keys = [k for k in excel_by_key if k in pull_by_key]
    differences: list[dict] = []
    matched = 0
    for key in common_keys:
        rec, line = pull_by_key[key]
        row_diffs: list[tuple[str, Any, Any]] = []
        if "qty" in mapped_fields and sorted(excel_qty[key]) != sorted(pull_qty[key]):
            row_diffs.append(("qty", _qty_text(excel_qty[key]), _qty_text(pull_qty[key])))
        if "source_doc" in mapped_fields and excel_source[key] and excel_source[key] != pull_source.get(key, set()):
            excel_text = ", ".join(sorted(excel_source_text.get(key, set())))
            row_diffs.append(("source_doc", excel_text or None,
                              ", ".join(sorted(pull_source_text.get(key, set()))) or None))
        if row_diffs:
            for field, excel_value, pull_value in row_diffs:
                differences.append({
                    "item_code": str(line.get("ItemCode") or "").strip(),
                    "doc_no": str(rec.get("DocNo") or "").strip(),
                    "location": str(line.get("Location") or "").strip(),
                    "field": field, "excel": excel_value, "pull": pull_value,
                })
        else:
            matched += 1

    total = len(common_keys)
    return {
        "summary": {"total": total, "matched": matched, "different": total - matched},
        "differences": differences,
        "only_in_excel": only_in_excel,
        "only_in_pull": only_in_pull,
    }


def compare_goods_receive_note_headers(
    excel_rows: list[dict], pull_rows: list[dict], mapping: Optional[dict] = None
) -> dict:
    """The HEADERS half: the "GRN Listing" macro's `Master` sheet, one row per GRN, keyed by
    document number. Fields: `doc_date` (a calendar day), `creditor_code`, `source_doc`
    (`Transfer From` against the set of documents the GRN's lines name, matched the
    upload's way), `cancel`. A GRN only in AutoCount (live 1 Oct: GR-2026/10-0006 missing
    from the listing) is an only-in row to check, never something Confirm deletes."""
    mapping = mapping or DEFAULT_MAPPINGS["grn_listing"]
    mapped_fields = {c["field"] for c in mapping["columns"]}
    excel_by_key: dict[str, dict] = {}
    for row in excel_rows:
        fields = _mapped_row(row, mapping)
        key = _key(fields.get("doc_no"))
        if key:
            excel_by_key.setdefault(key, fields)
    pull_by_key: dict[str, dict] = {}
    for rec in pull_rows:
        if isinstance(rec, dict) and _key(rec.get("DocNo")):
            pull_by_key.setdefault(_key(rec.get("DocNo")), rec)

    only_in_excel = sorted(
        str(excel_by_key[k].get("doc_no") or "").strip() for k in excel_by_key if k not in pull_by_key
    )
    only_in_pull = sorted(
        str(pull_by_key[k].get("DocNo") or "").strip() for k in pull_by_key if k not in excel_by_key
    )

    common_keys = [k for k in excel_by_key if k in pull_by_key]
    differences: list[dict] = []
    matched = 0
    for key in common_keys:
        excel_row = excel_by_key[key]
        rec = pull_by_key[key]
        doc_no = str(rec.get("DocNo") or "").strip()
        row_diffs: list[tuple[str, Any, Any]] = []
        excel_day = excel_row.get("doc_date")
        pull_day = _excel_day(rec.get("DocDate"))
        if excel_day is not None and pull_day is not None and excel_day != pull_day:
            row_diffs.append(("doc_date", excel_day.isoformat(), pull_day.isoformat()))
        excel_creditor = _key(excel_row.get("creditor_code"))
        pull_creditor = _key(rec.get("CreditorCode"))
        if excel_creditor and pull_creditor and excel_creditor != pull_creditor:
            row_diffs.append(("creditor_code", excel_row["creditor_code"],
                              str(rec.get("CreditorCode") or "").strip()))
        excel_sources = _source_keys(excel_row.get("source_doc"))
        if "source_doc" in mapped_fields and excel_sources:
            named = [l.get("FromDocNo") or l.get("OurPONo") for l in rec.get("Details") or []
                     if isinstance(l, dict)]
            pull_sources = frozenset().union(*(_source_keys(n) for n in named)) if named else frozenset()
            if excel_sources != pull_sources:
                row_diffs.append(("source_doc", str(excel_row.get("source_doc") or "").strip(),
                                  ", ".join(sorted({str(n).strip() for n in named if n})) or None))
        if "cancel" in mapped_fields:
            excel_cancel = bool(excel_row.get("cancel"))
            pull_cancel = _cancel_flag(rec.get("Cancelled"))
            if excel_cancel != pull_cancel:
                row_diffs.append(("cancel", excel_cancel, pull_cancel))
        if row_diffs:
            for field, excel_value, pull_value in row_diffs:
                differences.append({
                    "item_code": "", "doc_no": doc_no, "location": "",
                    "field": field, "excel": excel_value, "pull": pull_value,
                })
        else:
            matched += 1

    total = len(common_keys)
    return {
        "summary": {"total": total, "matched": matched, "different": total - matched},
        "differences": differences,
        "only_in_excel": only_in_excel,
        "only_in_pull": only_in_pull,
    }
