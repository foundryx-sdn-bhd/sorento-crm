"""AC-CONSIGN-CRM round 2, UAC-06: `so_doc_type` on the board contribution.

Harness = tests/test_fulfilment_board.py (blank schema). Built at SERVICE level (`build`), plus
the response schema must declare the field, because `response_model` drops undeclared fields
(the board route serialises through `BoardContribution`).
"""
from __future__ import annotations

from datetime import date

from app.models.order import DOC_TYPE_CONSIGNMENT
from app.schemas.project_board import BoardContribution

from ._pg_fixture import blank_session
from .test_fulfilment_board import (
    TODAY,
    _line,
    _order,
    _product,
    _service,
    _uid,
    _warehouse,
)


def _contribution(db, order):
    board = _service(db).build([order.so_number], granularity="week", as_of=TODAY)
    return board["cells"][0]["contributions"][0]


def test_board_contribution_carries_so_doc_type_consignment():
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        warehouse = _warehouse(db, f"ZZT-{_uid()[:6]}"[:20])
        order = _order(db, so_number=f"ZZT-CG-{_uid()[:8]}", order_date=date(2026, 1, 1))
        order.doc_type = DOC_TYPE_CONSIGNMENT
        _line(db, order, product, qty="10", required_date=date(2026, 9, 3), warehouse=warehouse)
        assert _contribution(db, order).get("so_doc_type") == "consignment"


def test_board_contribution_reads_sales_order_for_a_plain_so():
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        warehouse = _warehouse(db, f"ZZT-{_uid()[:6]}"[:20])
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, order, product, qty="10", required_date=date(2026, 9, 3), warehouse=warehouse)
        assert _contribution(db, order).get("so_doc_type") == "sales_order"


def test_board_contribution_schema_declares_so_doc_type():
    assert "so_doc_type" in BoardContribution.model_fields
