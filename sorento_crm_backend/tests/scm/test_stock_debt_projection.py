"""STOCK-DEBT-PROJECTION (owner, 12 Oct 2026, option D): the running projected balance,
pure half.

Pool supply reaches a project line only through a link, which the page's walk already
pins, so `project(M)` holds it. The one term this module adds is a PROJECT RESERVE's
unlinked incoming supply (crew answer (b); HQ-PROJECT-RESERVE, #1597), applied to the
earliest shortfall. Only a SURPLUS carries; debt never accumulates across months (R37).

    remainder(M)    = carried_in(M) + reserve_supply(M)
    projected(M)    = project(M) + remainder(M)
    carried_in(M+1) = max(0, min(remainder(M), projected(M)))

The SRTWT2215 shape is the owner's prod row (12 Oct 2026): Book=Project months Oct -38,
Nov -17, Dec -1,436, Jan -1,613, Feb -480, Mar -797, Apr -368; PO 202610-S0042 5,000 at
HQ delivered 8 Oct, HQ a project reserve.
"""
from __future__ import annotations

from app.services.scm.stock_debt_projection import project_months

KEYS = ["2026-10", "2026-11", "2026-12", "2027-01", "2027-02", "2027-03", "2027-04"]
PROJECT = {
    "2026-10": -38.0, "2026-11": -17.0, "2026-12": -1436.0, "2027-01": -1613.0,
    "2027-02": -480.0, "2027-03": -797.0, "2027-04": -368.0,
}


def _balances(rows):
    return [row.projected for row in rows]


def test_srtwt2215_prod_shape_carries_the_reserve_po():
    """Oct -38 + 5,000 = 4,962, and the surplus carries month after month."""
    rows = project_months(KEYS, PROJECT, reserve_supply={"2026-10": 5000.0})
    assert _balances(rows) == [4962.0, 4945.0, 3509.0, 1896.0, 1416.0, 619.0, 251.0]
    assert rows[0].carried_in == 0.0 and rows[0].reserve_supply == 5000.0
    assert rows[0].carried_out == 4962.0
    assert rows[2].carried_in == 4945.0 and rows[2].month_balance == -1436.0


def test_without_reserve_supply_the_projection_is_the_project_months():
    """Rule D with no reserve: nothing carries, each month is its own figure."""
    rows = project_months(KEYS, PROJECT, reserve_supply={})
    assert _balances(rows) == list(PROJECT.values())
    assert all(row.carried_out == 0.0 for row in rows)


def test_project_spare_does_not_carry():
    rows = project_months(
        ["2026-10", "2026-11"], {"2026-10": 50.0, "2026-11": -30.0}, reserve_supply={},
    )
    assert _balances(rows) == [50.0, -30.0]


def test_debt_does_not_carry():
    rows = project_months(
        ["2026-10", "2026-11"], {"2026-10": -100.0, "2026-11": 0.0}, reserve_supply={},
    )
    assert _balances(rows) == [-100.0, 0.0]


def test_project_spare_is_spent_before_carried_reserve_stock():
    """October has 40 project spare and 60 reserve beside 0 debt: only the 60 carries. In
    November the 10 short is met from it, leaving 50."""
    rows = project_months(
        ["2026-10", "2026-11", "2026-12"], {"2026-10": 40.0, "2026-11": -10.0, "2026-12": -100.0},
        reserve_supply={"2026-10": 60.0},
    )
    assert _balances(rows) == [100.0, 50.0, -50.0]


def test_a_later_reserve_document_lands_in_its_own_month():
    rows = project_months(
        ["2026-10", "2026-11", "2026-12"], {"2026-10": -5.0, "2026-11": -10.0, "2026-12": -20.0},
        reserve_supply={"2026-11": 25.0},
    )
    assert _balances(rows) == [-5.0, 15.0, -5.0]


def test_a_month_that_ends_short_carries_nothing():
    rows = project_months(
        ["2026-10", "2026-11", "2026-12"], {"2026-10": -30.0, "2026-11": -5.0, "2026-12": 0.0},
        reserve_supply={"2026-10": 10.0, "2026-12": 4.0},
    )
    assert _balances(rows) == [-20.0, -5.0, 4.0]
    assert rows[1].carried_in == 0.0
