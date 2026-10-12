"""STOCK-DEBT-PROJECTION (owner, 12 Oct 2026): the running projected balance, pure half.

The owner's rule: "only consider pool, those project locations are matched already", and
"pool a": pool supply first covers the dealer / retail orders due in its month, and only
the remainder goes to project shortfalls, earliest first. Only a SURPLUS carries; debt
never accumulates across months (R37). Owner, 12 Oct 2026 after hand testing: pool ON HAND
is shared and decided at planning time, so it covers dealer orders only and never a project
shortfall; only INCOMING pool supply (POs, not-landed SPOs) carries to project lines.

    dealer_left(M)  = dealer(M) less what pool on hand (carried, earliest first) covers
    remainder(M)    = carried_in(M) + incoming(M) - dealer_left(M)
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


def test_srtwt2215_prod_shape_carries_the_hq_po_not_the_brw_on_hand():
    """BRW on hand 191 meets the 15 dealer order; the 176 left of it does not carry to the
    project months. The 5,000 HQ PO carries: Oct -38 + 5,000 = 4,962, and on."""
    rows = project_months(
        KEYS, PROJECT, pool_supply={"2026-10": 5000.0}, dealer_demand={"2026-10": 15.0},
        pool_on_hand={"2026-10": 191.0},
    )
    assert _balances(rows) == [4962.0, 4945.0, 3509.0, 1896.0, 1416.0, 619.0, 251.0]
    assert rows[0].carried_in == 0.0 and rows[0].pool_supply == 5000.0
    assert rows[0].dealer_demand == 0.0 and rows[0].carried_out == 4962.0
    assert rows[2].carried_in == 4945.0 and rows[2].month_balance == -1436.0


def test_c_fhss12_only_the_incoming_po_meets_the_project_shortfall():
    """Owner's hand test: 57 BRW on hand + 15 BRW PO, Oct -377. Only the PO counts: -362,
    and the later months are unchanged."""
    rows = project_months(
        ["2026-10", "2026-11", "2026-12"], {"2026-10": -377.0, "2026-11": -20.0, "2026-12": -5.0},
        pool_supply={"2026-10": 15.0}, dealer_demand={}, pool_on_hand={"2026-10": 57.0},
    )
    assert _balances(rows) == [-362.0, -20.0, -5.0]
    assert rows[0].carried_out == 0.0


def test_pool_on_hand_covers_dealer_orders_only():
    """100 on hand, a 30 dealer order and a 50 project shortfall: the dealer order is met
    from the floor and the 70 left of it does not touch the project line."""
    rows = project_months(
        ["2026-10"], {"2026-10": -50.0}, pool_supply={}, dealer_demand={"2026-10": 30.0},
        pool_on_hand={"2026-10": 100.0},
    )
    assert _balances(rows) == [-50.0]


def test_pool_on_hand_left_over_still_meets_a_later_dealer_order():
    """50 on hand in Oct meets a 40 dealer order in Dec, so the incoming PO's surplus is not
    charged for it."""
    rows = project_months(
        ["2026-10", "2026-11", "2026-12"], {"2026-10": -80.0}, pool_supply={"2026-10": 100.0},
        dealer_demand={"2026-12": 40.0}, pool_on_hand={"2026-10": 50.0},
    )
    assert _balances(rows) == [20.0, 20.0, 20.0]


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
