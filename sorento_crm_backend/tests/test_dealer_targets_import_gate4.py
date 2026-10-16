"""Dealer targets import, fourth real-file gate (lane SALES-TARGET-DEALER, crew 16 Oct).

Round 3 on the real files: Sorento 45/109 mapped, Mocha still 0. Shapes, masked:
1. Mocha F3 is an INCENTIVE header with a trailing note that holds QUARTER ("INCENTIVE (xxx
   xxxx xxxxxxx)"), right after the "QUARTERLY (POINT)" block whose merge stops at E2. The reader
   placed it as a quarterly rate WITHOUT the block's points flag, so the points thresholds had no
   rate.
2. NAME_MATCHES_SEVERAL "differ by: - | A C I" and "I | III | IV": several ledgers answer to the
   account through `account_level`, and only one carries the file's account in its own name.
3. THRESHOLD_REPEATED unchanged by the per-account merge: the reason now quotes the repeated
   tiers' rates, so the masked dry run shows their shape.
"""
from __future__ import annotations

import io
from datetime import date
from decimal import Decimal

from .test_sales_targets_s1 import BASE, _code, _uid, api, world  # noqa: F401  (fixtures)

D = Decimal


def _parse(fill):
    from openpyxl import Workbook

    from app.services.sales.dealer_target_import import parse_workbook

    wb = Workbook()
    fill(wb.active)
    buf = io.BytesIO()
    wb.save(buf)
    return parse_workbook(buf.getvalue(), year=2026), buf.getvalue()


def _mocha(ws):
    ws["A2"] = "CUSTOMER NAME"
    ws.merge_cells("A2:A3")
    ws["B2"] = "QUARTERLY (POINT)"
    ws.merge_cells("B2:E2")
    for col, text in zip("BCDE", ("JAN-MAR", "APR-JUN", "JUL-SEPT", "OCT-DEC")):
        ws[f"{col}3"] = text
    ws["F3"] = "INCENTIVE (PAID EACH QUARTER)"
    ws["A4"] = "KILO LIVING A/C I"
    for i, col in enumerate("BCDE"):
        ws[f"{col}4"] = 100000 + 10000 * i
    ws["F4"] = 0.04


def test_an_incentive_with_a_trailing_note_takes_its_quarterly_block_s_points():
    parsed, _ = _parse(_mocha)
    assert parsed.rows[0].status == "mapped", parsed.rows[0].reason
    (q,) = [s for s in parsed.schemes if s.kind == "quarterly"]
    assert q.threshold_unit == "points"
    assert [(t.period_start, t.threshold, t.rate) for t in q.tiers] == [
        (date(2026, 1, 1), D("100000"), D("4")), (date(2026, 4, 1), D("110000"), D("4")),
        (date(2026, 7, 1), D("120000"), D("4")), (date(2026, 10, 1), D("130000"), D("4")),
    ]


def _customer(db, company_id, name, level):
    from app.models.order import Customer

    row = Customer(id=_uid(), company_id=company_id, customer_code=_code(), customer_name=name, account_level=level)
    db.add(row)
    db.flush()
    return row


def _dry_run(client, db, name):
    def fill(ws):
        ws["A1"], ws["B1"] = "DEALER NAME", "Q1"
        ws["A2"], ws["B2"] = name, "100,000 - 1%"

    _, data = _parse(fill)
    files = {"file": ("t.xlsx", data, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    db.autoflush = False
    try:
        return client.post(f"{BASE}/import", params={"dry_run": "true", "year": 2026}, files=files).json()["rows"][0]
    finally:
        db.autoflush = True


def test_the_ledger_whose_name_carries_the_account_breaks_a_tie(api):
    """`- | A C I`: a ledger with no marker and one marked [A/C I], both at level 1."""
    client, db, company_id = api
    _customer(db, company_id, "ECHO HOME SDN BHD", 1)
    marked = _customer(db, company_id, "ECHO HOME SDN BHD - [A/C I]", 1)
    row = _dry_run(client, db, "ECHO HOME SDN BHD A/C I")
    assert row["match"] == "auto" and row["customer"] == marked.customer_name


def test_a_marker_naming_another_account_outranks_a_wrong_level(api):
    """`I | III | IV`: three ledgers all at level 1, their names say I, III and IV."""
    client, db, company_id = api
    one = _customer(db, company_id, "KILO LIVING [A/C I]", 1)
    _customer(db, company_id, "KILO LIVING [A/C III]", 1)
    _customer(db, company_id, "KILO LIVING [A/C IV]", 1)
    row = _dry_run(client, db, "KILO LIVING A/C I")
    assert row["match"] == "auto" and row["customer"] == one.customer_name


def test_two_ledgers_both_marked_with_the_account_stay_a_pick(api):
    client, db, company_id = api
    _customer(db, company_id, "ECHO HOME SDN BHD - [A/C I]", 1)
    _customer(db, company_id, "ECHO HOME SDN. BHD. [A/C I]", 1)
    row = _dry_run(client, db, "ECHO HOME SDN BHD A/C I")
    assert row["status"] == "needs_pick"


def test_a_repeated_threshold_names_the_rates_of_the_repeated_tiers():
    def fill(ws):
        ws["A1"], ws["B1"] = "DEALER NAME", "Q1"
        ws.merge_cells("B1:E1")
        for col, text in zip("BCDE", ("Tier 1", "Tier 1 Incentive", "Tier 2", "Tier 2 Incentive")):
            ws[f"{col}2"] = text
        ws["A3"], ws["B3"], ws["C3"], ws["D3"], ws["E3"] = "X A/C I", 300000, 0.02, 300000, "A/C I- 1%"

    parsed, _ = _parse(fill)
    reason = parsed.rows[0].reason
    assert "repeats in one period" in reason
    assert 'rates "2% | A/C I 1%"' in reason
