"""STOCK-DEBT-PROJECTION (owner, 12 Oct 2026): the running projected balance, pure half.

The owner's rule: "only consider pool, those project locations are matched already". The
projection starts from each month's EXISTING figure (today's matching, R37/R45 untouched)
and carries forward only POOL supply that is still spare, plus pool supply the page does
not count at all (a site pool outside the span). Debt never carries; project spare never
carries. No double count: pool spare the page already credits to its own month is carried
FROM that month, never added a second time.

    carried_in(M) = carry(M-1)                         (0 before the first month)
    projected(M)  = month(M) + added(M) + carried_in(M)
    carry(M)      = clamp(projected(M), 0, carried_in(M) + pool_free(M) + added(M))

The SRTWT2215 shape is the owner's prod row (12 Oct 2026): BRW on hand, PO 202610-S0042
5,000 at HQ, months Oct +138, Nov -17, Dec -1,436, Jan -1,613, Feb -480, Mar -797.
"""
from __future__ import annotations

from app.services.scm.stock_debt_projection import project_months

KEYS = ["2026-10", "2026-11", "2026-12", "2027-01", "2027-02", "2027-03"]
MONTHS = {
    "2026-10": 138.0, "2026-11": -17.0, "2026-12": -1436.0,
    "2027-01": -1613.0, "2027-02": -480.0, "2027-03": -797.0,
}


def _balances(rows):
    return [row.projected for row in rows]


def test_srtwt2215_po_outside_the_span_carries_dec_to_mar():
    """HQ outside the page's span: the 5,000 is in no month figure, so it is ADDED in
    October and carried. This is the owner's expectation."""
    rows = project_months(
        KEYS, MONTHS, pool_free={"2026-10": 138.0}, pool_added={"2026-10": 5000.0}
    )
    assert _balances(rows) == [5138.0, 5121.0, 3685.0, 2072.0, 1592.0, 795.0]
    assert rows[0].carried_in == 0.0 and rows[0].pool_added == 5000.0
    assert rows[2].carried_in == 5121.0 and rows[2].month_balance == -1436.0


def test_srtwt2215_po_already_in_october_is_not_counted_twice():
    """HQ inside the span: October's +138 ALREADY includes the 5,000 (spent on October's
    and past-due shortfalls). Nothing is added; only the 138 that is left carries."""
    rows = project_months(
        KEYS, MONTHS, pool_free={"2026-10": 5138.0}, pool_added={}
    )
    assert _balances(rows) == [138.0, 121.0, -1315.0, -1613.0, -480.0, -797.0]
    assert rows[0].projected == MONTHS["2026-10"]


def test_project_spare_does_not_carry():
    """Spare at a project bin is matched already: it stays in its own month."""
    rows = project_months(
        ["2026-10", "2026-11"], {"2026-10": 50.0, "2026-11": -30.0}, pool_free={},
        pool_added={},
    )
    assert _balances(rows) == [50.0, -30.0]


def test_debt_does_not_carry():
    """A month states its own shortfall (R37); the next month does not inherit it."""
    rows = project_months(
        ["2026-10", "2026-11"], {"2026-10": -100.0, "2026-11": 0.0}, pool_free={},
        pool_added={},
    )
    assert _balances(rows) == [-100.0, 0.0]


def test_only_the_pool_part_of_a_spare_month_carries():
    """October: 40 project spare and 60 pool spare beside 0 debt. Only the 60 carries."""
    rows = project_months(
        ["2026-10", "2026-11"], {"2026-10": 100.0, "2026-11": -80.0},
        pool_free={"2026-10": 60.0}, pool_added={},
    )
    assert _balances(rows) == [100.0, -20.0]


def test_project_spare_is_spent_before_carried_pool_stock():
    """A month whose own spare is project stock nets its own debt with it first, so the
    carried pool stock is used only for what is left."""
    rows = project_months(
        ["2026-10", "2026-11", "2026-12"], {"2026-10": 100.0, "2026-11": -10.0, "2026-12": -100.0},
        pool_free={"2026-10": 100.0}, pool_added={"2026-11": 0.0},
    )
    # Nov: -10 + 100 carried = 90, all of it pool (carried_in 100, nothing else free).
    assert _balances(rows) == [100.0, 90.0, -10.0]


def test_a_later_pool_document_lands_in_its_own_month():
    rows = project_months(
        ["2026-10", "2026-11", "2026-12"], {"2026-10": -5.0, "2026-11": -10.0, "2026-12": -20.0},
        pool_free={}, pool_added={"2026-11": 25.0},
    )
    assert _balances(rows) == [-5.0, 15.0, -5.0]
