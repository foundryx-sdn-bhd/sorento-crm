"""Phase 2 RED tests - REPORTS-STEP2 S1 measure registry (`app/services/reports/measures.py`).

UAC `documentation/plans/chatbot/reports-step2-acceptance-criteria.md`: AC-1 (rows), AC-7, AC-8.
Pure unit tests, no database.

Pinned by the tester (the UAC names `run: Callable` only): a Measure's `run` is called as
`run(db, *, group_by, date_from, date_to, filters, warehouse_codes, policy, company_grants, sort,
top_n)` and returns the dict `ask.run_ask` returns. The S1 rows delegate to `ask.run_ask`.
"""
from __future__ import annotations

import dataclasses

import pytest

from app.services.reports import ask

S1 = (
    "ordered_amount", "ordered_qty", "delivered_amount", "delivered_qty",
    "outstanding_amount", "outstanding_qty",
)


def _fake(key="zzt_fake_measure", **over):
    from app.services.reports.measures import Measure

    base = dict(
        key=key, label="ZZT fake", synonyms=("zzt",), basis="delivered", figure="amount",
        dimensions=("product",), grant="sales_orders.sales_report", dealer_dimensions=("product",),
        run=lambda db, **kw: ask._empty(),
    )
    base.update(over)
    return Measure(**base)


def test_ac1_the_six_s1_measures_are_registered_in_order():
    from app.services.reports import measures

    assert tuple(k for k in measures.keys() if k in S1) == S1
    assert measures.keys()[: len(S1)] == S1


@pytest.mark.parametrize("key", S1)
def test_ac1_s1_rows_carry_the_ask_engine_dimensions_grant_and_basis(key):
    from app.services.reports import measures

    m = measures.get(key)
    basis, figure = key.split("_")
    assert (m.key, m.basis, m.figure) == (key, basis, figure)
    assert m.dimensions == ask.DIMENSIONS
    assert frozenset(m.dealer_dimensions) == ask.DEALER_KEYS
    assert m.grant == "sales_orders.sales_report"
    assert m.label and callable(m.run)


def test_ac1_a_measure_is_a_frozen_dataclass():
    from app.services.reports.measures import Measure

    assert dataclasses.is_dataclass(Measure)
    with pytest.raises(dataclasses.FrozenInstanceError):
        _fake().key = "y"


def test_ac7_register_adds_a_measure_to_get_and_keys_last():
    from app.services.reports import measures

    m = _fake("zzt_reg_unit")
    try:
        measures.register(m)
        assert measures.get("zzt_reg_unit") is m
        assert measures.keys()[-1] == "zzt_reg_unit"
        assert "zzt_reg_unit" in measures.MEASURES
    finally:
        measures.MEASURES.pop("zzt_reg_unit", None)


def test_ac8_a_duplicate_key_raises_value_error():
    from app.services.reports import measures

    with pytest.raises(ValueError):
        measures.register(measures.get("ordered_amount"))


def test_ac8_get_of_an_unknown_key_raises_key_error():
    from app.services import reports  # noqa: F401
    from app.services.reports import measures

    with pytest.raises(KeyError):
        measures.get("zzt_no_such_measure")
