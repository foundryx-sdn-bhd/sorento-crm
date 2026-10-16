"""Dealer targets Excel import (lane SALES-TARGET-DEALER, UAC D3).

The owner's gate (16 Oct 00:19): every row of the Sorento and Mocha files must be representable
on the one target engine, and the dry run says, per row, whether it is and why not. The layout
this reads is `documentation/plans/sales/dealer-targets-import-layout.md`; the real files never
enter the repo, so crew runs the dry run on them locally and sends back the patterns it rejects.

Two halves:

- `parse_workbook` (pure): sheets -> header by name -> merged cells resolved -> rows grouped into
  targets (a merged period cell over several rows is one target over those accounts) -> each
  period cell read by `parse_tier_cell` -> one `Scheme` per target and scheme kind (quarterly,
  monthly, yearly), with a `RowResult` per data row.
- `run` (database): codes resolved to the acting company's customers, then every scheme is
  created or updated through `target_service` (so the import obeys exactly the rules the screen
  does), each in its own savepoint; a scheme the service refuses marks its rows. A dry run is
  the same work in a savepoint the route rolls back, so its report is what an apply would do.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.services.error_handler import AppException
from app.services.sales.period_service import add_months

# --------------------------------------------------------------------------------------
# the cell grammar
# --------------------------------------------------------------------------------------

_BLANK = {"", "-", "--", "n/a", "na", "nil", "none", "x"}
_MONTHS = {
    m: i
    for i, names in enumerate(
        [
            ("JAN", "JANUARY"), ("FEB", "FEBRUARY"), ("MAR", "MARCH"), ("APR", "APRIL"),
            ("MAY",), ("JUN", "JUNE"), ("JUL", "JULY"), ("AUG", "AUGUST"),
            ("SEP", "SEPT", "SEPTEMBER"), ("OCT", "OCTOBER"), ("NOV", "NOVEMBER"),
            ("DEC", "DECEMBER"),
        ],
        start=1,
    )
    for m in names
}
_MONTH_RE = "|".join(sorted(_MONTHS, key=len, reverse=True))
#: `DEC'25-NOV'26`, `Jan-Dec 2026`, `JAN-DEC'26`, `Dec 25 - Nov 26`.
_WINDOW = re.compile(
    rf"\b({_MONTH_RE})\s*'?\s*(\d{{2}}|\d{{4}})?\s*(?:-|TO|\u2013)\s*({_MONTH_RE})\s*'?\s*(\d{{2}}|\d{{4}})\b",
    re.IGNORECASE,
)
_PCT = re.compile(r"(\d+(?:\.\d+)?)\s*%")
#: A money figure: `RM 300,000`, `300,000.50`, `300k`, `1.2m`, `300000`. Never followed by `%`.
_AMOUNT = re.compile(
    r"(?<![\w.])(?:RM\s*)?(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?\s*([kKmM])?(?![\w.,]*\s*%)(?![\d])"
)
_ADDITIONAL = re.compile(r"\+?\s*\badd(?:itional|'?l)?\b\.?", re.IGNORECASE)


class CellError(ValueError):
    """A cell this reader cannot turn into tiers; the message quotes it."""


@dataclass
class CellTier:
    threshold: Optional[Decimal]
    rate: Optional[Decimal]
    is_additional: bool = False
    #: Account label as written ("A/C I", "Deluxe") -> rate.
    account_rates: Optional[Dict[str, Decimal]] = None


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace(" ", " ")).strip()


def _label_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _amount(match: re.Match) -> Decimal:
    whole = match.group(1).replace(",", "")
    value = Decimal(whole + (match.group(2) or ""))
    suffix = (match.group(3) or "").lower()
    if suffix == "k":
        value *= 1000
    elif suffix == "m":
        value *= 1000000
    return value


def _number(value) -> Optional[Decimal]:
    """A number cell, or a text cell that is only a number (`"100,000"`, `"RM 50,000"`, `"1%"`)."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value))
    text = _norm(str(value))
    m = re.fullmatch(r"(?:RM\s*)?(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?\s*([kKmM])?\s*%?", text, re.IGNORECASE)
    return _amount(m) if m else None


def _rates_in(text: str) -> Tuple[List[Tuple[str, Decimal]], Optional[Decimal]]:
    """`[(label, rate)]` for every `label n%` in `text`, and the rate written with no label."""
    named: List[Tuple[str, Decimal]] = []
    plain: Optional[Decimal] = None
    last = 0
    for m in _PCT.finditer(text):
        before = text[last:m.start()]
        label = re.sub(r"^[\s/,;|&+@=:()\-]+|[\s/,;|&+@=:()\-]+$", "", before)
        label = re.sub(r"^(?:and|at)\b\s*", "", label, flags=re.IGNORECASE).strip()
        rate = Decimal(m.group(1))
        if label and re.search(r"[A-Za-z\[]", label) and not re.fullmatch(r"(?i)rm|rate|@", label):
            named.append((_norm(label), rate))
        elif plain is None:
            plain = rate
        last = m.end()
    return named, plain


def _read_part(text: str) -> Tuple[Optional[Decimal], List[Tuple[str, Decimal]], Optional[Decimal]]:
    """(threshold, named rates, plain rate) of one part of a line."""
    amount = _AMOUNT.search(text)
    threshold = _amount(amount) if amount else None
    rest = text[: amount.start()] + " " + text[amount.end():] if amount else text
    named, plain = _rates_in(rest)
    return threshold, named, plain


def parse_tier_cell(value, *, default_rate: Optional[Decimal] = None) -> List[CellTier]:
    """The tiers one period cell holds (layout doc, "Cells"); `[]` for no scheme."""
    if value is None:
        return []
    number = None if isinstance(value, str) else _number(value)
    if number is not None:
        return [CellTier(number, default_rate)]
    text = _norm(str(value))
    if text.lower() in _BLANK:
        return []
    if len(text) > MAX_CELL_TEXT:
        raise CellError(f"a cell of more than {MAX_CELL_TEXT} characters")
    tiers: List[CellTier] = []
    for raw_line in re.split(r"[\n;|]+", str(value)):
        line = _WINDOW.sub(" ", _norm(raw_line)).strip(" :")
        if not line or line.lower() in _BLANK:
            continue
        split = _ADDITIONAL.search(line)
        main, extra = (line[: split.start()], line[split.end():]) if split else (line, None)
        threshold, named, plain = _read_part(main)
        main_tier: Optional[CellTier] = None
        if named or plain is not None:
            main_tier = CellTier(threshold, None if named else plain, False, dict(named) if named else None)
        elif threshold is not None and extra is None:
            main_tier = CellTier(threshold, default_rate)
        if main_tier is not None:
            tiers.append(main_tier)
        if extra is not None:
            x_threshold, x_named, x_plain = _read_part(extra)
            if x_plain is None and not x_named:
                raise CellError(f'cannot read "{_norm(raw_line)}"')
            tiers.append(
                CellTier(
                    x_threshold if x_threshold is not None else threshold,
                    None if x_named else x_plain,
                    True,
                    dict(x_named) if x_named else None,
                )
            )
        if main_tier is None and extra is None:
            raise CellError(f'cannot read "{_norm(raw_line)}"')
    return tiers


def _window(text: str, year: int) -> Optional[Tuple[date, date]]:
    """The start and end a header or cell states (`DEC'25-NOV'26`, `JAN-DEC'26`)."""
    m = _WINDOW.search(text or "")
    if not m:
        return None

    def yr(token: Optional[str], fallback: int) -> int:
        if not token:
            return fallback
        value = int(token)
        return value + 2000 if value < 100 else value

    end_year = yr(m.group(4), year)
    start_month, end_month = _MONTHS[m.group(1).upper()], _MONTHS[m.group(3).upper()]
    start_year = yr(m.group(2), end_year if start_month <= end_month else end_year - 1)
    if not (2000 <= start_year <= 2100 and 2000 <= end_year <= 2100):
        raise CellError(f'the dates "{m.group(0)}" are out of range')
    start = date(start_year, start_month, 1)
    end = add_months(date(end_year, end_month, 1), 1).replace(day=1)
    if end <= start:
        raise CellError(f'the dates "{m.group(0)}" end before they start')
    return start, date.fromordinal(end.toordinal() - 1)


# --------------------------------------------------------------------------------------
# the sheet reader
# --------------------------------------------------------------------------------------


@dataclass
class SchemeTier:
    period_start: date
    threshold: Decimal
    rate: Optional[Decimal]
    is_additional: bool
    #: customer code -> rate
    account_rates: Optional[Dict[str, Decimal]]


@dataclass
class Scheme:
    key: str
    kind: str  # quarterly | monthly | yearly
    sheet: str
    rows: List[int]
    codes: List[str]
    names: List[str]
    payout_code: Optional[str]
    start: date
    end: date
    split_every: Optional[int]
    split_unit: Optional[str]
    threshold_unit: str
    rounding: str
    tiers: List[SchemeTier]
    #: "<dealer> <2026 | Dec 2025 to Nov 2026> <quarterly | monthly | yearly>", the kind last.
    name: str = ""


@dataclass
class RowResult:
    sheet: str
    row: int
    code: Optional[str]
    name_in_file: Optional[str]
    account_in_file: Optional[str]
    status: str = "mapped"
    reason: Optional[str] = None
    warnings: List[str] = field(default_factory=list)
    target_keys: List[str] = field(default_factory=list)
    #: The CRM customer's name once the code matched (set by `run`).
    customer: Optional[str] = None


@dataclass
class ParsedWorkbook:
    rows: List[RowResult]
    schemes: List[Scheme]
    notes: List[str]


@dataclass
class _Column:
    index: int
    role: str  # code | name | account | payto | round | period
    kind: Optional[str] = None  # Q | M | Y
    number: Optional[int] = None  # quarter or month
    window: Optional[Tuple[date, date]] = None
    points: bool = False
    default_rate: Optional[Decimal] = None
    is_rate: bool = False
    header: str = ""


_KIND_NAME = {"Q": "quarterly", "M": "monthly", "Y": "yearly"}


def _text(value) -> str:
    return "" if value is None else _norm(str(value))


def _classify(header: str, year: int) -> Optional[_Column]:
    up = header.upper()
    if not up:
        return None
    if "PAY" in up and "TO" in up:
        return _Column(0, "payto", header=header)
    if "CODE" in up:
        return _Column(0, "code", header=header)
    if "ROUND" in up:
        return _Column(0, "round", header=header)
    points = "POINT" in up or "PTS" in up
    rate = _PCT.search(up)
    default_rate = Decimal(rate.group(1)) if rate else None
    q = re.search(r"\bQ\s*([1-4])\b", up)
    if q:
        return _Column(0, "period", "Q", int(q.group(1)), points=points, default_rate=default_rate, header=header)
    window = _window(up, year)
    if window or re.search(r"\b(YEAR|YEARLY|ANNUAL|ANNUALLY|FY)\b", up):
        return _Column(0, "period", "Y", None, window=window, points=points,
                       default_rate=default_rate, header=header)
    month = re.fullmatch(rf"\s*({_MONTH_RE})\b[\s'0-9]*(?:\(.*\))?(?:\s*POINTS?)?\s*", up)
    if month:
        return _Column(0, "period", "M", _MONTHS[month.group(1)], points=points,
                       default_rate=default_rate, header=header)
    if re.search(r"\b(NAME|DEALER|CUSTOMER|DEBTOR)\b", up):
        return _Column(0, "name", header=header)
    if re.search(r"\bACCOUNT\b|^A/?C$|\bA/C\b", up):
        return _Column(0, "account", header=header)
    return None


#: Bounds on what one upload may make the reader do (security review B1): a sheet's declared
#: size is not trusted, only the cells that hold a value are visited, and a sheet past these is
#: skipped with a note. The real files are a few hundred rows wide of a few dozen columns.
MAX_SHEETS = 30
MAX_SHEET_CELLS = 100_000
MAX_SHEET_ROWS = 5_000
MAX_SHEET_COLS = 300
MAX_MERGE_AREA = 2_000
MAX_CELL_TEXT = 500
#: The workbook's XML once unzipped; a 10 MB upload that inflates past this is refused.
MAX_UNZIPPED_BYTES = 150 * 1024 * 1024


class SheetTooLarge(ValueError):
    pass


class _Grid:
    """The cells that hold a value, with merged ranges resolved: every cell of a merge reads the
    top-left value and knows the range's rows. Sparse: nothing is visited by the sheet's declared
    size, which one stray cell far away can make a billion cells (security review B1)."""

    def __init__(self, ws):
        self.ws = ws
        self.values: Dict[Tuple[int, int], object] = {}
        for (r, c), cell in getattr(ws, "_cells", {}).items():
            value = cell.value
            if value is None or (isinstance(value, str) and not value.strip()):
                continue
            self.values[(r, c)] = value
            if len(self.values) > MAX_SHEET_CELLS:
                raise SheetTooLarge(f"more than {MAX_SHEET_CELLS:,} filled cells")
        self.merge: Dict[Tuple[int, int], Tuple[int, int, int, int]] = {}
        for rng in ws.merged_cells.ranges:
            area = (rng.max_row - rng.min_row + 1) * (rng.max_col - rng.min_col + 1)
            if area > MAX_MERGE_AREA:
                # A title banner merged across the sheet, never a threshold over a few rows.
                continue
            box = (rng.min_row, rng.min_col, rng.max_row, rng.max_col)
            if len(self.merge) + area > MAX_SHEET_CELLS:
                raise SheetTooLarge(f"more than {MAX_SHEET_CELLS:,} merged cells")
            for r in range(rng.min_row, rng.max_row + 1):
                for c in range(rng.min_col, rng.max_col + 1):
                    self.merge[(r, c)] = box
        cells = set(self.values) | {
            key for key, box in self.merge.items() if (box[0], box[1]) in self.values
        }
        self.rows = sorted({r for r, _ in cells})
        self.cols = sorted({c for _, c in cells})
        if len(self.rows) > MAX_SHEET_ROWS:
            raise SheetTooLarge(f"more than {MAX_SHEET_ROWS:,} rows")
        if len(self.cols) > MAX_SHEET_COLS:
            raise SheetTooLarge(f"more than {MAX_SHEET_COLS} columns")
        self.row_cols: Dict[int, List[int]] = {}
        for r, c in sorted(cells):
            self.row_cols.setdefault(r, []).append(c)

    def value(self, r: int, c: int):
        box = self.merge.get((r, c))
        if box:
            r, c = box[0], box[1]
        return self.values.get((r, c))

    def own(self, r: int, c: int):
        """The cell's own value, not its merge's."""
        return self.values.get((r, c))

    def box(self, r: int, c: int) -> Optional[Tuple[int, int, int, int]]:
        return self.merge.get((r, c))


def _header_row(grid: _Grid) -> Optional[int]:
    for r in grid.rows:
        if r > 20:
            break
        for c in grid.row_cols.get(r, []):
            if "CODE" in _text(grid.value(r, c)).upper():
                return r
    return None


def _columns(grid: _Grid, header: int, year: int) -> Tuple[List[_Column], int]:
    """The classified columns and the first data row (after an optional sub-header row)."""
    cols: List[_Column] = []
    for c in grid.row_cols.get(header, []):
        col = _classify(_text(grid.value(header, c)), year)
        if col is not None:
            col.index = c
            cols.append(col)
    first = header + 1
    code_col = next((col.index for col in cols if col.role == "code"), None)
    periods = [col for col in cols if col.role == "period"]
    if code_col and periods and first in grid.row_cols:
        subs = {col.index: _text(grid.value(first, col.index)).upper() for col in periods}
        is_sub = not _text(grid.value(first, code_col)) and any(
            re.search(r"TARGET|POINT|PTS|%|RM|RATE|INCENTIVE|THRESHOLD", s) for s in subs.values()
        )
        if is_sub:
            for col in periods:
                sub = subs[col.index]
                if "POINT" in sub or "PTS" in sub:
                    col.points = True
                if "%" in sub or "RATE" in sub or "INCENTIVE" in sub:
                    col.is_rate = True
            first += 1
    return cols, first


def _union(groups: Dict[int, int], a: int, b: int) -> None:
    def find(x):
        while groups[x] != x:
            groups[x] = groups[groups[x]]
            x = groups[x]
        return x

    ra, rb = find(a), find(b)
    if ra != rb:
        groups[max(ra, rb)] = min(ra, rb)


def _root(groups: Dict[int, int], x: int) -> int:
    while groups[x] != x:
        x = groups[x]
    return x


def _rounding(text: str) -> Optional[str]:
    t = text.strip().lower()
    if not t:
        return None
    if t in {"sen", "0.01", "cent", "cents", "2", "2dp"}:
        return "sen"
    if t in {"ringgit", "rm", "1", "whole", "0", "0dp"}:
        return "ringgit"
    return "?"


def parse_workbook(data: bytes, *, year: int) -> ParsedWorkbook:
    import zipfile

    from openpyxl import load_workbook

    not_a_workbook = AppException(
        status_code=422, message="That file is not an Excel workbook (.xlsx).", code="NOT_A_WORKBOOK"
    )
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            unzipped = sum(info.file_size for info in archive.infolist())
    except zipfile.BadZipFile as exc:
        raise not_a_workbook from exc
    if unzipped > MAX_UNZIPPED_BYTES:
        raise AppException(status_code=422, message="That workbook is too large.", code="FILE_TOO_LARGE")
    try:
        wb = load_workbook(io.BytesIO(data), data_only=True)
    except Exception as exc:  # InvalidFileException, KeyError, ...
        raise not_a_workbook from exc
    if len(wb.worksheets) > MAX_SHEETS:
        raise AppException(
            status_code=422, message=f"That workbook has more than {MAX_SHEETS} sheets.", code="FILE_TOO_LARGE"
        )
    rows: List[RowResult] = []
    schemes: List[Scheme] = []
    notes: List[str] = []
    for ws in wb.worksheets:
        try:
            grid = _Grid(ws)
        except SheetTooLarge as exc:
            notes.append(f'Sheet "{ws.title}": {exc}; skipped.')
            continue
        header = _header_row(grid)
        if header is None:
            if grid.values:
                notes.append(f'Sheet "{ws.title}": no header row with CODE in the first 20 rows; skipped.')
            continue
        sheet_rows, sheet_schemes = _parse_sheet(grid, ws.title, header, year, notes)
        rows.extend(sheet_rows)
        schemes.extend(sheet_schemes)
    return ParsedWorkbook(rows, schemes, notes)


def _parse_sheet(grid: _Grid, sheet: str, header: int, year: int, notes: List[str]):
    cols, first = _columns(grid, header, year)
    by_role: Dict[str, Optional[_Column]] = {
        role: next((c for c in cols if c.role == role), None)
        for role in ("code", "name", "account", "payto", "round")
    }
    periods = [c for c in cols if c.role == "period"]
    if not periods:
        notes.append(f'Sheet "{sheet}": no Q1-Q4, month or yearly column; skipped.')
        return [], []
    code_col = by_role["code"]

    results: Dict[int, RowResult] = {}
    for r in grid.rows:
        if r < first:
            continue
        # A row only a merge from above reaches (no value of its own) is still a data row: it is
        # one of the accounts that merged threshold covers.
        if not any(_text(grid.value(r, c)) for c in grid.row_cols.get(r, [])):
            continue
        code = _text(grid.value(r, code_col.index)) if code_col else ""
        res = RowResult(
            sheet=sheet,
            row=r,
            code=code or None,
            name_in_file=_text(grid.value(r, by_role["name"].index)) or None if by_role["name"] else None,
            account_in_file=(
                _text(grid.value(r, by_role["account"].index)) or None if by_role["account"] else None
            ),
        )
        if not code:
            res.status, res.reason = "not_representable", "no customer code on this row"
        results[r] = res

    data_rows = [r for r, res in results.items() if res.code]
    groups = {r: r for r in data_rows}
    for col in periods:
        for r in data_rows:
            box = grid.box(r, col.index)
            if box:
                for other in data_rows:
                    if other != r and box[0] <= other <= box[2]:
                        _union(groups, r, other)
    members: Dict[int, List[int]] = {}
    for r in data_rows:
        members.setdefault(_root(groups, r), []).append(r)

    schemes: List[Scheme] = []
    for root, group_rows in sorted(members.items()):
        group_rows.sort()
        try:
            schemes.extend(_group_schemes(grid, sheet, group_rows, results, periods, by_role, year))
        except CellError as exc:
            for r in group_rows:
                results[r].status, results[r].reason = "not_representable", str(exc)
    for scheme in schemes:
        for r in scheme.rows:
            results[r].target_keys.append(scheme.key)
    for res in results.values():
        if res.status == "mapped" and not res.target_keys:
            res.warnings.append("no target figures on this row")
    return [results[r] for r in sorted(results)], schemes


def _group_schemes(grid, sheet, group_rows, results, periods, by_role, year) -> List[Scheme]:
    codes = [results[r].code for r in group_rows]
    labels = {}
    for r in group_rows:
        res = results[r]
        for text in (res.account_in_file, res.name_in_file, res.code):
            if text:
                labels.setdefault(_label_key(text), res.code)
    first_name = next((results[r].name_in_file for r in group_rows if results[r].name_in_file), codes[0])

    # A window stated in any period cell of the group (P6) moves its whole scheme.
    stated: Optional[Tuple[date, date]] = None
    for col in periods:
        for r in group_rows:
            win = _window(_text(grid.value(r, col.index))[:MAX_CELL_TEXT], year)
            if win:
                stated = win
    rounding = "sen"
    if by_role["round"]:
        for r in group_rows:
            got = _rounding(_text(grid.value(r, by_role["round"].index)))
            if got == "?":
                raise CellError(f'ROUND: cannot read "{_text(grid.value(r, by_role["round"].index))}"')
            if got:
                rounding = got
    payto = None
    if by_role["payto"]:
        for r in group_rows:
            payto = payto or _text(grid.value(r, by_role["payto"].index)) or None

    out: List[Scheme] = []
    for kind in ("Q", "M", "Y"):
        kind_cols = [c for c in periods if c.kind == kind]
        if not kind_cols:
            continue
        header_window = next((c.window for c in kind_cols if c.window), None)
        window = stated or header_window or (date(year, 1, 1), date(year, 12, 31))
        if kind == "Y":
            start, end, split = window[0], window[1], (None, None)
        else:
            start, end = window
            split = (3, "month") if kind == "Q" else (1, "month")
        tiers: List[SchemeTier] = []
        numbers = sorted({c.number or 0 for c in kind_cols})
        for number in numbers:
            cols = [c for c in kind_cols if (c.number or 0) == number]
            period_start = _period_start(kind, number, start)
            tiers.extend(_period_tiers(grid, cols, group_rows, results, labels, period_start, kind))
        if not tiers:
            continue
        seen = set()
        for t in tiers:
            key = (t.period_start, t.is_additional, t.threshold)
            if key in seen:
                raise CellError(f"{_KIND_NAME[kind]}: the threshold {t.threshold:,.0f} repeats in one period")
            seen.add(key)
        payout = payto
        if payout is None:
            for t in tiers:
                if t.account_rates:
                    payout = next(iter(t.account_rates))
                    break
        payout = payout or codes[0]
        span = (
            str(start.year)
            if (start.month, start.day, end.month, end.day) == (1, 1, 12, 31) and start.year == end.year
            else f"{start:%b %Y} to {end:%b %Y}"
        )
        out.append(
            Scheme(
                key=f"{_KIND_NAME[kind]} {start:%Y-%m} {'+'.join(codes)}",
                kind=_KIND_NAME[kind],
                sheet=sheet,
                rows=list(group_rows),
                codes=list(codes),
                names=[results[r].name_in_file or "" for r in group_rows],
                payout_code=payout,
                start=start,
                end=end,
                split_every=split[0],
                split_unit=split[1],
                threshold_unit="points" if any(c.points for c in kind_cols) else "amount",
                rounding=rounding,
                tiers=tiers,
                name=f"{first_name[:80]} {span} {_KIND_NAME[kind]}",
            )
        )
    return out


def _period_start(kind: str, number: int, start: date) -> date:
    if kind == "Q":
        return add_months(start, 3 * (number - 1))
    if kind == "M":
        # The month's first occurrence on or after the window's start.
        offset = (number - start.month) % 12
        return add_months(start.replace(day=1), offset)
    return start


def _period_tiers(grid, cols, group_rows, results, labels, period_start, kind) -> List[SchemeTier]:
    """One period's tiers for one target: from its tier-text columns and its threshold / rate
    column pairs."""
    name = cols[0].header or _KIND_NAME[kind]
    out: List[SchemeTier] = []

    def resolve(cell: CellTier) -> SchemeTier:
        rates = None
        if cell.account_rates:
            rates = {}
            for label, rate in cell.account_rates.items():
                code = labels.get(_label_key(label))
                if code is None:
                    raise CellError(f'{name}: the rate names "{label}", which is not an account of this target')
                rates[code] = rate
        if cell.threshold is None:
            raise CellError(f"{name}: a rate with no threshold")
        if rates is None and cell.rate is None:
            raise CellError(f"{name}: the threshold {cell.threshold:,.0f} has no rate")
        return SchemeTier(period_start, cell.threshold, None if rates else cell.rate, cell.is_additional, rates)

    def group_value(col) -> Tuple[object, Dict[str, object]]:
        """The value the target reads: the merged cell, or the one row holding a value; and the
        per-row values when rows differ (a rate per account)."""
        per_row = {}
        for r in group_rows:
            v = grid.value(r, col.index)
            if _text(v) and _text(v).lower() not in _BLANK:
                per_row[results[r].code] = v
        distinct = {_text(v) for v in per_row.values()}
        if not per_row:
            return None, {}
        if len(distinct) == 1:
            return next(iter(per_row.values())), {}
        return None, per_row

    i = 0
    while i < len(cols):
        col = cols[i]
        rate_col = cols[i + 1] if i + 1 < len(cols) and cols[i + 1].is_rate and not col.is_rate else None
        value, per_row = group_value(col)
        if per_row and rate_col is None:
            raise CellError(f"{name}: the accounts of one target give different tiers")
        if rate_col is not None:
            # A threshold / rate pair: the threshold cell, and a rate per row or for all.
            if per_row:
                raise CellError(f"{name}: the accounts of one target give different thresholds")
            rate_value, rate_rows = group_value(rate_col)
            threshold = _number(value) if value is not None else None
            if value is not None and threshold is None:
                raise CellError(f'{name}: cannot read "{_text(value)}"')
            if threshold is not None:
                if rate_rows:
                    rates = {}
                    for code, v in rate_rows.items():
                        n = _number(v)
                        if n is None:
                            raise CellError(f'{name}: cannot read the rate "{_text(v)}"')
                        rates[code] = n
                    out.append(SchemeTier(period_start, threshold, None, False, rates))
                else:
                    rate = _number(rate_value) if rate_value is not None else col.default_rate
                    if rate is None:
                        raise CellError(f"{name}: the threshold {threshold:,.0f} has no rate")
                    out.append(SchemeTier(period_start, threshold, rate, False, None))
            i += 2
            continue
        if value is not None:
            out.extend(resolve(t) for t in parse_tier_cell(value, default_rate=col.default_rate))
        i += 1
    return out


# --------------------------------------------------------------------------------------
# the database half
# --------------------------------------------------------------------------------------


def _same_name(a: Optional[str], b: Optional[str]) -> bool:
    def key(text: Optional[str]) -> str:
        t = (text or "").lower()
        t = re.sub(r"\bsdn\.?\s*bhd\.?|\bs/b\b|\bsb\b", "", t)
        return re.sub(r"[^a-z0-9]", "", t)

    return key(a) == key(b)


def run(db: Session, data: bytes, *, year: int, dry_run: bool, user_id: Optional[str] = None) -> dict:
    from app.models.order import Customer
    from app.models.sales import SalesTarget, SalesTargetAccount
    from app.schemas.sales import SalesTargetCreate, SalesTargetUpdate
    from app.services.sales import target_service

    parsed = parse_workbook(data, year=year)
    rows_by_key = {(r.sheet, r.row): r for r in parsed.rows}
    codes = {r.code for r in parsed.rows if r.code}
    customers = {c.customer_code: c for c in db.query(Customer).filter(Customer.customer_code.in_(codes))} if codes else {}
    # Codes are matched exactly, then without regard to case or surrounding spaces.
    loose = {code.strip().upper(): c for code, c in customers.items()}

    def customer_of(code: Optional[str]):
        if not code:
            return None
        return customers.get(code) or loose.get(code.strip().upper())

    for res in parsed.rows:
        if res.status != "mapped":
            continue
        customer = customer_of(res.code)
        if customer is None:
            res.status, res.reason = "not_representable", f"no customer with code {res.code}"
            continue
        res.customer = customer.customer_name
        if res.name_in_file and not _same_name(res.name_in_file, customer.customer_name):
            res.warnings.append(f'name differs: the file says "{res.name_in_file}", the CRM "{customer.customer_name}"')

    # Existing dealer targets by (accounts, dates, split): a re-import updates, never adds.
    existing: Dict[Tuple, SalesTarget] = {}
    dealer_targets = db.query(SalesTarget).filter(SalesTarget.subject_kind == "dealer").all()
    if dealer_targets:
        links: Dict[str, set] = {}
        for link in db.query(SalesTargetAccount).filter(
            SalesTargetAccount.target_id.in_([t.id for t in dealer_targets])
        ):
            links.setdefault(link.target_id, set()).add(link.customer_id)
        for t in dealer_targets:
            existing[(frozenset(links.get(t.id, ())), t.start_date, t.end_date, t.split_every, t.split_unit)] = t

    new = updated = 0
    target_nos: Dict[str, str] = {}
    for scheme in parsed.schemes:
        scheme_rows = [rows_by_key[(scheme.sheet, r)] for r in scheme.rows]
        broken = [r for r in scheme_rows if r.status != "mapped"]
        if broken:
            for r in scheme_rows:
                if r.status == "mapped":
                    r.status = "not_representable"
                    r.reason = f"shares a target with row {broken[0].row}: {broken[0].reason}"
            continue
        ids = [customer_of(code).id for code in scheme.codes]
        payout = customer_of(scheme.payout_code)
        if payout is None:
            for r in scheme_rows:
                r.status, r.reason = "not_representable", f"PAY TO: no customer with code {scheme.payout_code}"
            continue
        tiers = [
            {
                "period_start": t.period_start if scheme.split_every else None,
                "threshold": float(t.threshold),
                "rate": float(t.rate or 0),
                "is_additional": t.is_additional,
                "account_rates": (
                    [{"customer_id": customer_of(code).id, "rate": float(rate)} for code, rate in t.account_rates.items()]
                    if t.account_rates
                    else None
                ),
            }
            for t in scheme.tiers
        ]
        fields = {
            "commission_method": "retroactive",
            "threshold_unit": scheme.threshold_unit,
            "rounding": scheme.rounding,
            "tiers_per_period": bool(scheme.split_every),
            "tiers": tiers,
        }
        match = existing.get((frozenset(ids), scheme.start, scheme.end, scheme.split_every, scheme.split_unit))
        try:
            with db.begin_nested():
                if match is not None:
                    target_service.update_target(
                        db,
                        match,
                        SalesTargetUpdate(customer_ids=ids, payout_customer_id=payout.id, **fields),
                        user_id=user_id,
                    )
                    target = match
                else:
                    target = target_service.create_target(
                        db,
                        SalesTargetCreate(
                            subject_kind="dealer",
                            customer_ids=ids,
                            payout_customer_id=payout.id,
                            name=scheme.name[:120],
                            metric="amount",
                            basis="ordered",
                            product_scope="all",
                            start_date=scheme.start,
                            end_date=scheme.end,
                            split_every=scheme.split_every,
                            split_unit=scheme.split_unit,
                            **fields,
                        ),
                        user_id=user_id,
                    )
        except AppException as exc:
            for r in scheme_rows:
                r.status, r.reason = "not_representable", f"{scheme.kind}: {exc.message}"
            continue
        except ValueError as exc:  # a payload the schema refuses (pydantic's ValidationError)
            errors = getattr(exc, "errors", None)
            message = errors()[0].get("msg", "not valid") if callable(errors) and errors() else str(exc)
            for r in scheme_rows:
                r.status, r.reason = "not_representable", f"{scheme.kind}: {message}"
            continue
        if match is not None:
            updated += 1
        else:
            new += 1
            existing[(frozenset(ids), scheme.start, scheme.end, scheme.split_every, scheme.split_unit)] = target
        target_nos[scheme.key] = target.target_no

    out_rows = []
    for res in parsed.rows:
        out_rows.append(
            {
                "sheet": res.sheet,
                "row": res.row,
                "code": res.code,
                "name_in_file": res.name_in_file,
                "account_in_file": res.account_in_file,
                "customer": res.customer,
                "status": res.status,
                "reason": res.reason,
                "warnings": res.warnings,
                "target_keys": res.target_keys if res.status == "mapped" else [],
                "target_nos": (
                    []
                    if dry_run or res.status != "mapped"
                    else [target_nos[k] for k in res.target_keys if k in target_nos]
                ),
            }
        )
    mapped = sum(1 for r in out_rows if r["status"] == "mapped")
    return {
        "dry_run": dry_run,
        "summary": {
            "rows": len(out_rows),
            "mapped": mapped,
            "not_representable": len(out_rows) - mapped,
            "targets_new": new,
            "targets_updated": updated,
        },
        "rows": out_rows,
        "notes": parsed.notes,
    }
