"""The measure registry behind `GET /api/v1/order-management/report` (REPORTS-STEP2).

A measure is one row: its key, the words a caller may say for it, the dimensions it can be cut
by, the reveal grant it needs and the function that runs it. Adding a measure is one
`register(Measure(...))` plus its function. The route and the MCP tool read this registry.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from app.services.reports import ask


@dataclass(frozen=True)
class Measure:
    key: str
    label: str
    synonyms: tuple[str, ...]
    basis: str
    figure: str
    dimensions: tuple[str, ...]
    grant: str
    dealer_dimensions: tuple[str, ...]
    #: `run(db, *, group_by, date_from, date_to, filters, warehouse_codes, policy,
    #: company_grants, sort, top_n)` returns the dict `ask.run_ask` returns.
    run: Callable


MEASURES: dict[str, Measure] = {}


#: The reveal grants a measure may name. A new report adds its own here, with review.
KNOWN_GRANTS = frozenset({"sales_orders.sales_report"})


def register(measure: Measure) -> None:
    if not set(measure.dealer_dimensions) <= ask.DEALER_KEYS:
        raise ValueError(f"Measure '{measure.key}': dealer_dimensions must be within the dealer keys")
    if measure.grant not in KNOWN_GRANTS:
        raise ValueError(f"Measure '{measure.key}': unknown grant '{measure.grant}'")
    if measure.key in MEASURES:
        raise ValueError(f"Measure '{measure.key}' is already registered")
    MEASURES[measure.key] = measure


def get(key: str) -> Measure:
    return MEASURES[key]


def keys() -> tuple[str, ...]:
    return tuple(MEASURES)


def _ask_runner(basis: str, figure: str) -> Callable:
    def run(db, **kw):
        return ask.run_ask(db, basis=basis, measure=figure, **kw)

    return run


_SALES_GRANT = "sales_orders.sales_report"
_SALES_SYNONYMS = {
    "ordered": "ordered sales",
    "delivered": "delivered sales",
    "outstanding": "outstanding orders",
}

for _basis in ("ordered", "delivered", "outstanding"):
    for _figure in ("amount", "qty"):
        register(
            Measure(
                key=f"{_basis}_{_figure}",
                label=f"{_basis.capitalize()} {_figure}",
                synonyms=(_SALES_SYNONYMS[_basis],),
                basis=_basis,
                figure=_figure,
                dimensions=ask.DIMENSIONS,
                grant=_SALES_GRANT,
                dealer_dimensions=tuple(sorted(ask.DEALER_KEYS)),
                run=_ask_runner(_basis, _figure),
            )
        )
