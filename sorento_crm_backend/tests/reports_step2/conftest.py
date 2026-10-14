"""Shared fixtures for the REPORTS-STEP2 tests."""
from __future__ import annotations

import pytest

from app.services.reports import ask


@pytest.fixture
def fake_measure():
    """Registers one fake measure (product-only dimensions) and removes it afterwards.

    `run` echoes what it was called with so a test can see the route passed it through."""
    from app.services.reports import measures

    calls: list[dict] = []

    def _run(db, **kw):
        calls.append(kw)
        body = ask._empty()
        body["rows"] = [{"rank": 1, "name": "ZZT-FAKE", "qty": 7, "amount": 12.5, "keys": []}]
        body["total_count"] = 1
        body["total"] = {"qty": 7, "amount": 12.5}
        return body

    m = measures.Measure(
        key="zzt_fake_measure", label="ZZT fake", synonyms=("zzt",), basis="delivered",
        figure="amount", dimensions=("product", "month"), grant="sales_orders.sales_report",
        dealer_dimensions=("product",), run=_run,
    )
    measures.register(m)
    try:
        yield SimpleCalls(m, calls)
    finally:
        measures.MEASURES.pop(m.key, None)


class SimpleCalls:
    def __init__(self, measure, calls):
        self.measure, self.calls = measure, calls


@pytest.fixture
def register_fake():
    """Factory: registers a fake measure with the given overrides, removes all afterwards."""
    from app.services.reports import measures

    made: list[str] = []

    def _make(key, **over):
        base = dict(
            key=key, label="ZZT fake", synonyms=("zzt",), basis="delivered", figure="amount",
            dimensions=("product", "month"), grant="sales_orders.sales_report",
            dealer_dimensions=("product",),
            run=lambda db, **kw: {**ask._empty(), "rows": [], "total": {"qty": 1, "amount": 1.0}},
        )
        base.update(over)
        m = measures.Measure(**base)
        measures.register(m)
        made.append(key)
        return m

    try:
        yield _make
    finally:
        for k in made:
            measures.MEASURES.pop(k, None)
