"""A workbook in the LAYOUT the owner's real dealer target files are delivered in (lane
SALES-TARGET-DEALER, gate-fail round 16 Oct). No real data: dealer names are masked patterns,
figures are shapes only, and the real files never enter the repo.

What the real files do, and this copies (crew's gate run on the real files, 16 Oct):

- Stacked headers, three levels: `Q1` over `JAN-MAR` over `Tier n` / `Tier n Incentive`; a
  yearly block over its window; a `Monthly` header over `JAN` .. `DEC`; on Mocha,
  `QUARTERLY (POINT)` over `Q1` .. `Q4` and `POINT (YEARLY)` over tiers, beside RM yearly tiers.
- No customer code: a dealer name merged down over its account rows, an account label per row
  (`A/C I`, `A/C III`, `Deluxe`, `[CERAMIC]`). The owner's ruling (16 Oct 09:12): match by name.
- Merged thresholds and incentives across account rows; incentives as fractions (0.02 = 2%) or
  text (`A/C I- 3%`, `+ Additional 1%`); thresholds as numbers or text (`>=1500000`).
- `+ Additional x%` written as a SECOND tier at the same threshold.
- Typos the importer must report, never map: `A/C 1`, `A/C I- 100000`, `NIL`.

Rows, by the masked dealer each one is (`DEALERS` maps them to the CRM names `seed` creates):
"""
from __future__ import annotations

import io
from typing import Dict, List, Tuple

from openpyxl import Workbook

TIERS = 3

#: masked dealer key -> (name as the FILE writes it, [(account label in the file, CRM name)]).
#: The CRM names are what `seed` creates; they differ from the file's only in the ways the owner
#: ruled a name match must ignore (case, spaces, punctuation, SDN BHD and ENTERPRISE(S) forms).
DEALERS: Dict[str, Tuple[str, List[Tuple[str, str]]]] = {
    # P1 merged A/C I + A/C III, Q1 2% then "+ Additional 1%" as a second tier at the same
    # threshold, then a second additional; Q2 "A/C I- 3%" (only A/C I earns, ruling 2).
    "ALPHA": ("Alpha Hardware Sdn. Bhd.", [
        ("A/C I", "ALPHA HARDWARE SDN BHD - [A/C I]"),
        ("A/C III", "ALPHA HARDWARE SDN BHD - [A/C III]"),
    ]),
    # P2 one threshold, a rate per account (Deluxe / Mastile / W-Ply), fractions.
    "BRAVO": ("BRAVO TRADING", [
        ("Deluxe", "BRAVO TRADING [DELUXE]"),
        ("Mastile", "BRAVO TRADING [MASTILE]"),
        ("W-Ply", "BRAVO TRADING [W-PLY]"),
    ]),
    # P3 Dec'25-Nov'26 yearly with ">=1500000" text; quarters stay Jan-Mar.
    "CHARLIE": ("Charlie Enterprises", [("A/C I", "CHARLIE ENTERPRISE - [A/C I]")]),
    # P4 pay-to: A/C III's own target "A/C I- 2%" is counted on A/C III and paid to A/C I.
    "DELTA": ("DELTA BUILDERS SDN BHD", [
        ("A/C I", "DELTA BUILDERS SDN BHD [A/C I]"),
        ("A/C III", "DELTA BUILDERS SDN BHD [A/C III]"),
    ]),
    # P5 monthly.
    "ECHO": ("ECHO HOME", [("A/C I", "ECHO HOME SDN BHD - [A/C I]")]),
    # P6 typos, each reported on its row: "A/C 1" names no account, "A/C I- 100000" is not a
    # rate, "NIL" beside a threshold is no rate.
    "FOXTROT": ("FOXTROT TILES", [("A/C I", "FOXTROT TILES - [A/C I]")]),
    "GOLF": ("GOLF CERAMICS", [("A/C I", "GOLF CERAMICS - [A/C I]")]),
    "HOTEL": ("HOTEL STONE", [("A/C I", "HOTEL STONE - [A/C I]")]),
    # P7 no CRM customer of that name at all: listed with suggestions, never auto-linked.
    "INDIA": ("INDIA MARBLE WORKS", [("A/C I", None)]),
}

#: Mocha: masked dealer key -> (file name, [(account label, CRM name)]).
MOCHA: Dict[str, Tuple[str, List[Tuple[str, str]]]] = {
    # P8 quarterly points at the header's 4%, POINT (YEARLY) tiers; [CERAMIC]/[IBORN] accounts
    # have no account level in the CRM, only the bracketed marker.
    "KILO": ("Kilo Living", [
        ("A/C I", "KILO LIVING SDN BHD - [A/C I]"),
        ("[CERAMIC]", "KILO LIVING SDN BHD [CERAMIC]"),
        ("[IBORN]", "KILO LIVING SDN BHD [IBORN]"),
    ]),
    # P9 RM yearly tiers on the Mocha sheet stay RM (units per column).
    "LIMA": ("LIMA DECOR", [("A/C I", "LIMA DECOR - [A/C I]")]),
}

#: CRM names `seed` adds that are NEAR the file's names, so suggestions have something to offer
#: and an ambiguous name is never auto-linked.
NEAR_MISSES = ["INDIA MARBLE SDN BHD - [A/C I]", "INDIAN MARBLE WORKS - [A/C II]"]

#: CRM account level per label (`customers.account_level`), as the acct_ledger seed sets it.
LEVEL = {"A/C I": 1, "A/C II": 2, "A/C III": 3, "A/C IV": 4}


def _header(ws, row: int, col: int, text: str, *, across: int = 1, down: int = 1) -> None:
    ws.cell(row=row, column=col, value=text)
    if across > 1 or down > 1:
        ws.merge_cells(start_row=row, start_column=col, end_row=row + down - 1, end_column=col + across - 1)


def _tier_heads(ws, row: int, col: int) -> int:
    for n in range(1, TIERS + 1):
        ws.cell(row=row, column=col, value=f"Tier {n}")
        ws.cell(row=row, column=col + 1, value=f"Tier {n} Incentive")
        col += 2
    return col


def sorento(ws, *, with_code_column: bool = False, codes: Dict[Tuple[str, str], str] | None = None) -> Dict[str, int]:
    """The Sorento sheet. Returns {column name: column index} for the tests' own writes."""
    ws.title = "SORENTO 2026"
    ws["A1"] = "SORENTO DEALER INCENTIVE 2026"
    ws.merge_cells("A1:H1")
    _header(ws, 2, 1, "NO", down=3)
    _header(ws, 2, 2, "DEALER NAME", down=3)
    col = 3
    cols: Dict[str, int] = {}
    if with_code_column:
        _header(ws, 2, col, "CUSTOMER CODE", down=3)
        cols["code"] = col
        col += 1
    _header(ws, 2, col, "ACCOUNT", down=3)
    cols["account"] = col
    col += 1
    for q, months in zip(("Q1", "Q2", "Q3", "Q4"), ("JAN-MAR", "APR-JUN", "JUL-SEP", "OCT-DEC")):
        _header(ws, 2, col, q, across=2 * TIERS)
        _header(ws, 3, col, months, across=2 * TIERS)
        cols[q] = col
        col = _tier_heads(ws, 4, col)
    _header(ws, 2, col, "YEARLY", across=1 + 2 * TIERS)
    _header(ws, 3, col, "PERIOD", down=2)
    cols["Y_PERIOD"] = col
    _header(ws, 3, col + 1, "JAN-DEC'26", across=2 * TIERS)
    cols["Y"] = col + 1
    col = _tier_heads(ws, 4, col + 1)
    _header(ws, 2, col, "Monthly", across=12 * 2)
    cols["M"] = col
    for m in ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"):
        _header(ws, 3, col, m, across=2)
        ws.cell(row=4, column=col, value="Tier 1")
        ws.cell(row=4, column=col + 1, value="Tier 1 Incentive")
        col += 2

    row = 5
    rows: Dict[str, int] = {}

    def dealer(key: str) -> int:
        nonlocal row
        name, accounts = DEALERS[key]
        first = row
        _header(ws, row, 1, str(len(rows) + 1), down=len(accounts))
        _header(ws, row, 2, name, down=len(accounts))
        for i, (label, _crm) in enumerate(accounts):
            ws.cell(row=row + i, column=cols["account"], value=label)
            if with_code_column and codes and (key, label) in codes:
                ws.cell(row=row + i, column=cols["code"], value=codes[(key, label)])
            rows[f"{key}:{label}"] = row + i
        row += len(accounts)
        return first

    def put(r: int, c: int, value, down: int = 1) -> None:
        _header(ws, r, c, value, down=down)

    # ALPHA: two accounts, every threshold merged over both.
    a = dealer("ALPHA")
    q1 = cols["Q1"]
    put(a, q1, 500000, down=2)                    # Tier 1
    put(a, q1 + 1, 0.02, down=2)                  # Tier 1 Incentive (2%, a fraction)
    put(a, q1 + 2, 500000, down=2)                # Tier 2 at the SAME threshold
    put(a, q1 + 3, "+ Additional 1%", down=2)     # ... is its additional (item 6)
    put(a, q1 + 4, 600000, down=2)                # Tier 3, a second additional line
    put(a, q1 + 5, "+ Additional 0.5%", down=2)
    q2 = cols["Q2"]
    put(a, q2, 400000, down=2)
    put(a, q2 + 1, "A/C I- 3%", down=2)           # only A/C I earns (ruling 2)
    y = cols["Y"]
    put(a, cols["Y_PERIOD"], "JAN-DEC'26", down=2)
    put(a, y, 1500000, down=2)
    put(a, y + 1, 0.01, down=2)
    put(a, y + 2, 2000000, down=2)
    put(a, y + 3, 0.015, down=2)

    # BRAVO: one threshold over three accounts, a rate per account.
    b = dealer("BRAVO")
    put(b, q1, 300000, down=3)
    for i, rate in enumerate((0.02, 0.03, 0.015)):
        put(b + i, q1 + 1, rate)

    # CHARLIE: Dec'25-Nov'26 yearly, text threshold; its Q1 stays Jan-Mar (item 5).
    c = dealer("CHARLIE")
    put(c, q1, 100000)
    put(c, q1 + 1, 0.02)
    put(c, cols["Y_PERIOD"], "DEC'25-NOV'26")
    put(c, y, "≥1500000")
    put(c, y + 1, 0.01)

    # DELTA: A/C I its own target; A/C III's own Q1 paid to A/C I (the pay-to case).
    d = dealer("DELTA")
    put(d, q1, 200000)
    put(d, q1 + 1, 0.02)
    put(d + 1, q1, 80000)
    put(d + 1, q1 + 1, "A/C I- 2%")

    # ECHO: monthly, January and February.
    e = dealer("ECHO")
    put(e, cols["M"], 50000)
    put(e, cols["M"] + 1, 0.01)
    put(e, cols["M"] + 2, 52000)
    put(e, cols["M"] + 3, "1%")

    # Typos, each on its own dealer so the rest still maps.
    f = dealer("FOXTROT")
    put(f, q1, 100000)
    put(f, q1 + 1, "A/C 1- 3%")                   # names no account of this dealer
    g = dealer("GOLF")
    put(g, q1, 100000)
    put(g, q1 + 1, "A/C I- 100000")               # an amount where a rate belongs
    h = dealer("HOTEL")
    put(h, q1, 100000)
    put(h, q1 + 1, "NIL")                         # a threshold with no rate

    i = dealer("INDIA")
    put(i, q1, 100000)
    put(i, q1 + 1, 0.02)
    return rows


def mocha(ws) -> Dict[str, int]:
    ws.title = "MOCHA 2026"
    _header(ws, 1, 1, "NO", down=3)
    _header(ws, 1, 2, "CUSTOMER NAME", down=3)
    _header(ws, 1, 3, "A/C", down=3)
    _header(ws, 1, 4, "QUARTERLY (POINT)", across=4)
    for i, q in enumerate(("Q1", "Q2", "Q3", "Q4")):
        _header(ws, 2, 4 + i, q)
        _header(ws, 3, 4 + i, "4%")
    _header(ws, 1, 8, "POINT (YEARLY)", across=2 * TIERS)
    _header(ws, 2, 8, "JAN-DEC'26", across=2 * TIERS)
    _tier_heads(ws, 3, 8)
    _header(ws, 1, 14, "YEARLY (RM)", across=2 * TIERS)
    _header(ws, 2, 14, "JAN-DEC'26", across=2 * TIERS)
    _tier_heads(ws, 3, 14)

    rows: Dict[str, int] = {}
    row = 4
    for key, (name, accounts) in MOCHA.items():
        _header(ws, row, 1, str(len(rows) + 1), down=len(accounts))
        _header(ws, row, 2, name, down=len(accounts))
        for i, (label, _crm) in enumerate(accounts):
            ws.cell(row=row + i, column=3, value=label)
            rows[f"{key}:{label}"] = row + i
        down = len(accounts)
        if key == "KILO":
            for q in range(4):
                _header(ws, row, 4 + q, 100000, down=down)
            _header(ws, row, 8, 450000, down=down)
            _header(ws, row, 9, 0.01, down=down)
            _header(ws, row, 10, 500000, down=down)
            _header(ws, row, 11, 0.015, down=down)
        else:
            _header(ws, row, 14, 600000, down=down)
            _header(ws, row, 15, 0.01, down=down)
            _header(ws, row, 16, 750000, down=down)
            _header(ws, row, 17, 0.02, down=down)
        row += down
    return rows


def build(*, with_code_column: bool = False, codes=None, extra_sheet=None) -> Tuple[bytes, Dict[str, int], Dict[str, int]]:
    """The two sheets in one workbook (crew runs the two files separately; one workbook holds
    both here so one test sees both layouts). `extra_sheet(ws)` adds a third sheet."""
    wb = Workbook()
    s_rows = sorento(wb.active, with_code_column=with_code_column, codes=codes)
    m_rows = mocha(wb.create_sheet("MOCHA 2026"))
    if extra_sheet is not None:
        extra_sheet(wb.create_sheet("NOTES"))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue(), s_rows, m_rows


def seed(db, company_id, uid, code) -> Dict[Tuple[str, str], object]:
    """The CRM customers the file's names stand for, plus near misses. {(dealer, label): row}."""
    from app.models.order import Customer

    out = {}
    for table in (DEALERS, MOCHA):
        for key, (_name, accounts) in table.items():
            for label, crm in accounts:
                if crm is None:
                    continue
                row = Customer(
                    id=uid(), company_id=company_id, customer_code=code(), customer_name=crm,
                    account_level=LEVEL.get(label),
                )
                db.add(row)
                out[(key, label)] = row
    for crm in NEAR_MISSES:
        db.add(Customer(id=uid(), company_id=company_id, customer_code=code(), customer_name=crm))
    db.flush()
    return out
