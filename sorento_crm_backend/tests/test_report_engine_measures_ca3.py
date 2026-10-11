"""CA-3 RED tests: the engine's two new measure kinds (slice 1 of the CA-3 plan).

`registry.Column` gains `agg` ("sum" default | "count_distinct") and `ratio_of=(num_key, den_key)`.
Proven on the SYNTHETIC scratch dataset of `tests/_report_fixture.py` (never the chat dataset: the
claim is that the kernel is generic). The fixture rows booked in 2026, North and South:

    Z-001 Alice North Jan 1000.00 fee 10.50      Z-004 Bob   South Mar  null   fee 5.00
    Z-002 Alice North Jan  250.25 fee null       Z-005 Bob   North Nov  99.99  fee 1.01
    Z-003 Alice South Mar  400.00 fee 20.00

`contacts` counts DISTINCT agents; `yield_` is amount / fee, recomputed, never a sum of ratios.

Left open by the plan and pinned loosely: a ratio is compared as the FRACTION num/den within
0.0001 (the route turns it into a percent), and the ratio column's own `type` / `expr` are not
meaningful (the engine computes it), so the test gives it type "integer" and a placeholder expr.
"""
from __future__ import annotations

import dataclasses
from decimal import Decimal

import pytest
import sqlalchemy as sa

from tests import _report_fixture as fixture
from tests._pg_fixture import pg_session


@pytest.fixture
def db():
    with pg_session() as session:
        fixture.create_table(session)
        yield session


def _definition():
    from app.services.reports import registry as reg

    base = fixture.dataset()
    t = fixture.table_clause()
    extra = (
        reg.Column("contacts", "Contacts", "integer", "measure", lambda c: t.c.agent, agg="count_distinct"),
        reg.Column("yield_", "Yield", "integer", "measure", lambda c: t.c.amount, ratio_of=("amount", "fee")),
    )
    ds = dataclasses.replace(base, columns=base.columns + extra)
    return fixture.definition(for_dataset=ds)


def _pivot(db, rows, cols, measures):
    from app.schemas.report import ReportViewConfig
    from app.services.reports import engine

    definition = _definition()
    merged = dict(definition.default_view["params"])
    view = ReportViewConfig(
        params={}, detail={"columns": [], "order": []},
        pivot={"rows": rows, "cols": cols, "measures": list(measures)},
    )
    return engine.run(db, definition, merged, view).layouts.summary


def _d(value) -> Decimal:
    return Decimal(str(value))


# ------------------------------------------------------------------ declarations


def test_a_column_defaults_to_sum_with_no_ratio():
    from app.services.reports import registry as reg

    t = fixture.table_clause()
    col = reg.Column("amount", "Amount", "money", "measure", lambda c: t.c.amount)
    assert col.agg == "sum" and col.ratio_of is None


def test_an_unknown_agg_is_refused():
    from app.services.reports import registry as reg

    t = fixture.table_clause()
    with pytest.raises(ValueError):
        reg.Column("amount", "Amount", "money", "measure", lambda c: t.c.amount, agg="median")


# ------------------------------------------------------------------ count distinct


def test_count_distinct_cells_count_each_value_once(db):
    pivot = _pivot(db, "region", "month", ["contacts"])
    assert _d(pivot.cells["North"]["2026-01"]["contacts"]) == 1, "Alice twice in January: one contact"
    assert _d(pivot.cells["North"]["2026-11"]["contacts"]) == 1
    assert _d(pivot.cells["South"]["2026-03"]["contacts"]) == 2


def test_count_distinct_row_and_column_totals_are_distinct_counts(db):
    pivot = _pivot(db, "region", "month", ["contacts"])
    assert _d(pivot.row_totals["North"]["contacts"]) == 2
    assert _d(pivot.row_totals["South"]["contacts"]) == 2
    assert _d(pivot.col_totals["2026-03"]["contacts"]) == 2
    assert _d(pivot.col_totals["2026-01"]["contacts"]) == 1


def test_the_grand_total_is_distinct_over_the_whole_set_not_a_sum(db):
    """Alice and Bob each sit in two regions: 2 distinct, where the rows sum to 4."""
    pivot = _pivot(db, "region", "month", ["contacts"])
    assert _d(pivot.grand_total["contacts"]) == 2


def test_count_distinct_and_a_sum_measure_sit_in_one_pivot(db):
    pivot = _pivot(db, "region", "month", ["amount", "contacts"])
    assert _d(pivot.grand_total["amount"]) == _d("1750.24")
    assert _d(pivot.grand_total["contacts"]) == 2
    assert _d(pivot.row_totals["North"]["amount"]) == _d("1350.24")


# ------------------------------------------------------------------ ratio


def test_a_ratio_cell_is_num_over_den_of_that_cell(db):
    pivot = _pivot(db, "agent", "month", ["amount", "fee", "yield_"])
    # Alice, January: amount 1250.25, fee 10.50
    assert _d(pivot.cells["Alice"]["2026-01"]["yield_"]) == pytest.approx(_d("1250.25") / _d("10.50"), abs=_d("0.0001"))


def test_ratio_totals_are_recomputed_from_the_summed_parts_not_summed(db):
    pivot = _pivot(db, "agent", "month", ["amount", "fee", "yield_"])
    assert _d(pivot.row_totals["Alice"]["yield_"]) == pytest.approx(_d("1650.25") / _d("30.50"), abs=_d("0.0001"))
    assert _d(pivot.row_totals["Bob"]["yield_"]) == pytest.approx(_d("99.99") / _d("6.01"), abs=_d("0.0001"))
    assert _d(pivot.col_totals["2026-03"]["yield_"]) == pytest.approx(_d("400.00") / _d("25.00"), abs=_d("0.0001"))
    assert _d(pivot.grand_total["yield_"]) == pytest.approx(_d("1750.24") / _d("36.51"), abs=_d("0.0001"))


def test_a_ratio_with_no_denominator_is_blank_not_a_crash(db):
    """Bob in March has a fee (5.00) but no amount: 0 or blank. A cell whose denominator sums to
    nothing (Alice's fee-less rows alone) must never divide by zero."""
    pivot = _pivot(db, "agent", "month", ["amount", "fee", "yield_"])
    bob_mar = pivot.cells["Bob"]["2026-03"]["yield_"]
    assert bob_mar is None or _d(bob_mar) == 0, bob_mar


# ------------------------------------------------------------------ existing datasets unchanged


def test_sum_only_measures_are_unchanged(db):
    """A regression guard on the plain scratch dataset (green before and after the lane)."""
    from app.schemas.report import ReportViewConfig
    from app.services.reports import engine

    definition = fixture.definition()
    view = ReportViewConfig(
        params={}, detail={"columns": [], "order": []},
        pivot={"rows": "agent", "cols": "month", "measures": ["amount", "fee"]},
    )
    pivot = engine.run(db, definition, dict(definition.default_view["params"]), view).layouts.summary
    assert pivot.row_totals["Alice"]["amount"] == "1650.25"
    assert pivot.grand_total["amount"] == "1750.24" and pivot.grand_total["fee"] == "36.51"
