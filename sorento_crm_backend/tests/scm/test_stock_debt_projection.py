"""STOCK-DEBT-PROJECTION (owner, 12 Oct 2026): the running projected balance, pure half.

The owner's rule: "only consider pool, those project locations are matched already", and
"pool a": pool supply first covers the dealer / retail orders due in its month, and only
the remainder goes to project shortfalls, earliest first. Only a SURPLUS carries; debt
never accumulates across months (R37).

    remainder(M)    = carried_in(M) + pool_supply(M) - dealer(M)
    projected(M)    = project(M) + remainder(M)
    carried_in(M+1) = max(0, min(remainder(M), projected(M)))

The SRTWT2215 shape is the owner's prod row (12 Oct 2026): Book=Project months Oct -38,
Nov -17, Dec -1,436, Jan -1,613, Feb -480, Mar -797, Apr -368; BRW on hand 191; PO
202610-S0042 5,000 at HQ delivered 8 Oct; a 15 dealer order at BRW this month.
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


def test_srtwt2215_prod_shape_carries_the_pool_po_dec_to_apr():
    rows = project_months(
        KEYS, PROJECT, pool_supply={"2026-10": 5191.0}, dealer_demand={"2026-10": 15.0}
    )
    assert _balances(rows) == [5138.0, 5121.0, 3685.0, 2072.0, 1592.0, 795.0, 427.0]
    assert rows[0].carried_in == 0.0 and rows[0].pool_supply == 5191.0
    assert rows[0].dealer_demand == 15.0 and rows[0].carried_out == 5138.0
    assert rows[2].carried_in == 5121.0 and rows[2].month_balance == -1436.0


def test_c_fhss12_pool_supply_is_applied_to_the_earliest_shortfall():
    """Owner's hand test: 57 BRW on hand + 15 BRW PO, Oct -377. Oct reads -305 and the
    later months are unchanged: nothing is left to carry, and debt does not carry."""
    rows = project_months(
        ["2026-10", "2026-11", "2026-12"], {"2026-10": -377.0, "2026-11": -20.0, "2026-12": -5.0},
        pool_supply={"2026-10": 72.0}, dealer_demand={},
    )
    assert _balances(rows) == [-305.0, -20.0, -5.0]
    assert rows[0].carried_out == 0.0


def test_dealer_orders_in_the_month_come_before_project_shortfalls():
    """Pool a: 100 of pool supply, 30 dealer orders and 100 project short in October. The
    dealer orders take 30 first; 70 is left for the project line."""
    rows = project_months(
        ["2026-10"], {"2026-10": -100.0}, pool_supply={"2026-10": 100.0},
        dealer_demand={"2026-10": 30.0},
    )
    assert _balances(rows) == [-30.0]


def test_a_later_dealer_order_only_gets_what_is_carried():
    """Pool a is per month: October's pool covers October's project shortfall first; the
    December dealer order gets only the surplus carried to it."""
    rows = project_months(
        ["2026-10", "2026-11", "2026-12"], {"2026-10": -80.0}, pool_supply={"2026-10": 100.0},
        dealer_demand={"2026-12": 100.0},
    )
    assert _balances(rows) == [20.0, 20.0, -80.0]


def test_project_spare_does_not_carry():
    rows = project_months(
        ["2026-10", "2026-11"], {"2026-10": 50.0, "2026-11": -30.0}, pool_supply={},
        dealer_demand={},
    )
    assert _balances(rows) == [50.0, -30.0]


def test_debt_does_not_carry():
    rows = project_months(
        ["2026-10", "2026-11"], {"2026-10": -100.0, "2026-11": 0.0}, pool_supply={},
        dealer_demand={},
    )
    assert _balances(rows) == [-100.0, 0.0]


def test_project_spare_is_spent_before_carried_pool_stock():
    """October has 40 project spare and 60 pool beside 0 debt: only the 60 carries. In
    November the 10 short is met from it, leaving 50."""
    rows = project_months(
        ["2026-10", "2026-11", "2026-12"], {"2026-10": 40.0, "2026-11": -10.0, "2026-12": -100.0},
        pool_supply={"2026-10": 60.0}, dealer_demand={},
    )
    assert _balances(rows) == [100.0, 50.0, -50.0]


def test_a_later_pool_document_lands_in_its_own_month():
    rows = project_months(
        ["2026-10", "2026-11", "2026-12"], {"2026-10": -5.0, "2026-11": -10.0, "2026-12": -20.0},
        pool_supply={"2026-11": 25.0}, dealer_demand={},
    )
    assert _balances(rows) == [-5.0, 15.0, -5.0]


def test_a_short_dealer_month_reads_short_and_carries_nothing():
    rows = project_months(
        ["2026-10", "2026-11"], {}, pool_supply={"2026-10": 10.0},
        dealer_demand={"2026-10": 25.0},
    )
    assert _balances(rows) == [-15.0, 0.0]
