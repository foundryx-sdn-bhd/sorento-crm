"""RED tests, written before the coder, for the owner-gated one-off backfill
`scripts/backfill_sheet_line_required_date.py` (SO-LINE-CHANGE-HISTORY Q5).

46 cancelled sheet-origin `sales_order_lines` (source_system 'scm_order_inquiry') have a NULL
required_date. The script sets it from the monthly order sheet only when the sheet gives
exactly one distinct date for that SO + item code. Ambiguous (several dates), undated (no
date) and unmatched (not in the sheet) lines are listed, never written. Dry run is the
default; `--apply` writes and is idempotent.

The script does not exist yet, so the module import fails the whole file at collection
(ModuleNotFoundError) - the EXPECTED red until the coder lands it.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.models.order import SalesOrder, SalesOrderLine
from app.models.product import Product, ProductCategory, UnitOfMeasure
from app.services.project_order_inquiry_reader import OrderInquiryRow

from ._pg_fixture import pg_session

import scripts.backfill_sheet_line_required_date as backfill  # noqa: E402

MARKER = "ZZT-SLRD"


def _uid() -> str:
    return str(uuid.uuid4())


@dataclass
class Line:
    id: str
    so_number: str
    product_code: str
    qty_ordered: float = 1


def _row(so, item, delivery=None, *, order_back=False, source_row=2) -> OrderInquiryRow:
    return OrderInquiryRow(
        so_number=so, item_code=item, qty=1, so_date=None, delivery_date=delivery,
        project="", location="", supplier="", po_numbers=(), not_ordered=False,
        order_back=order_back, sheet="Sheet1", source_row=source_row,
    )


D1, D2 = date(2026, 9, 20), date(2026, 10, 5)


def test_plan_single_date():
    line = Line("1", "SO100", "ITEM-A")
    plan = backfill.plan_backfill([line], [_row("SO100", "ITEM-A", D1)])
    assert plan.single == [(line, D1)]
    assert not plan.ambiguous and not plan.undated and not plan.unmatched


def test_plan_same_date_twice_collapses_to_single():
    line = Line("1", "SO100", "ITEM-A")
    plan = backfill.plan_backfill(
        [line], [_row("SO100", "ITEM-A", D1), _row("SO100", "ITEM-A", D1, source_row=3)]
    )
    assert plan.single == [(line, D1)]
    assert not plan.ambiguous


def test_plan_different_dates_are_ambiguous_sorted():
    line = Line("1", "SO100", "ITEM-A")
    plan = backfill.plan_backfill(
        [line], [_row("SO100", "ITEM-A", D2), _row("SO100", "ITEM-A", D1, source_row=3)]
    )
    assert plan.ambiguous == [(line, [D1, D2])]
    assert not plan.single


def test_plan_all_undated_rows_are_undated():
    line = Line("1", "SO100", "ITEM-A")
    plan = backfill.plan_backfill(
        [line],
        [_row("SO100", "ITEM-A", None), _row("SO100", "ITEM-A", None, order_back=True, source_row=3)],
    )
    assert plan.undated == [line]
    assert not plan.single and not plan.ambiguous and not plan.unmatched


def test_plan_unmatched_and_key_is_case_and_whitespace_insensitive():
    hit = Line("1", " so314593 ", "srtwcx8605-s-rl-pj")
    miss = Line("2", "SO999", "ITEM-A")
    plan = backfill.plan_backfill(
        [hit, miss], [_row("SO314593", "SRTWCX8605-S-RL-PJ", D1)]
    )
    assert plan.single == [(hit, D1)]
    assert plan.unmatched == [miss]


def test_plan_mixed_set_keeps_input_order_per_group():
    s1, a1, u1, m1, s2 = (
        Line("1", "SO1", "A"), Line("2", "SO2", "A"), Line("3", "SO3", "A"),
        Line("4", "SO4", "A"), Line("5", "SO5", "A"),
    )
    rows = [
        _row("SO1", "A", D1), _row("SO2", "A", D1), _row("SO2", "A", D2, source_row=3),
        _row("SO3", "A", None), _row("SO5", "A", D2),
    ]
    plan = backfill.plan_backfill([s1, a1, u1, m1, s2], rows)
    assert plan.single == [(s1, D1), (s2, D2)]
    assert plan.ambiguous == [(a1, [D1, D2])]
    assert plan.undated == [u1]
    assert plan.unmatched == [m1]


def _sorento(db) -> str:
    return db.execute(text("select id from companies where code = 'SRT'")).scalar()


def _seed_line(db, company_id, so_number, code, *, source_system="scm_order_inquiry",
               required_date=None) -> SalesOrderLine:
    category = ProductCategory(
        id=_uid(), company_id=company_id, category_code=f"ZZT-{_uid()[:8]}",
        category_name=f"{MARKER} cat",
    )
    uom = UnitOfMeasure(id=_uid(), uom_code=f"ZZT{_uid()[:4]}", uom_name="Piece")
    db.add_all([category, uom])
    db.flush()
    product = Product(
        id=_uid(), company_id=company_id, product_code=code, product_name=f"{MARKER} {code}",
        category_id=category.id, base_uom_id=uom.id, list_price=Decimal("1"),
    )
    order = SalesOrder(id=_uid(), company_id=company_id, so_number=so_number, status="open")
    db.add_all([product, order])
    db.flush()
    line = SalesOrderLine(
        id=_uid(), company_id=company_id, sales_order_id=order.id, product_id=product.id,
        qty_ordered=Decimal("2"), source_system=source_system, required_date=required_date,
        line_status="cancelled",
    )
    db.add(line)
    db.flush()
    return line


def test_run_dry_run_reports_and_writes_nothing():
    with pg_session() as db:
        cid = _sorento(db)
        so, code = f"{MARKER}-SO1-{_uid()[:6]}", f"{MARKER}-P1-{_uid()[:6]}"
        line = _seed_line(db, cid, so, code)
        db.commit()
        plan = backfill.run(db, [_row(so, code, D1)], apply=False)
        assert [l.id for l, _ in plan.single] == [line.id]
        assert plan.single[0][1] == D1
        assert not db.dirty
        db.expire_all()
        assert db.get(SalesOrderLine, line.id).required_date is None


def test_run_apply_sets_date_and_is_idempotent():
    with pg_session() as db:
        cid = _sorento(db)
        so, code = f"{MARKER}-SO2-{_uid()[:6]}", f"{MARKER}-P2-{_uid()[:6]}"
        line = _seed_line(db, cid, so, code)
        db.commit()
        rows = [_row(so, code, D1)]
        plan = backfill.run(db, rows, apply=True)
        assert [l.id for l, _ in plan.single] == [line.id]
        db.expire_all()
        assert db.get(SalesOrderLine, line.id).required_date == D1
        again = backfill.run(db, rows, apply=True)
        mine = [l for l in again.single if l.id == line.id]
        assert not mine
        assert not [l for l in again.unmatched if l.id == line.id]
        assert db.get(SalesOrderLine, line.id).required_date == D1


def test_run_ignores_dated_lines_and_other_source_systems():
    with pg_session() as db:
        cid = _sorento(db)
        so_a, code_a = f"{MARKER}-SO3-{_uid()[:6]}", f"{MARKER}-P3-{_uid()[:6]}"
        so_b, code_b = f"{MARKER}-SO4-{_uid()[:6]}", f"{MARKER}-P4-{_uid()[:6]}"
        dated = _seed_line(db, cid, so_a, code_a, required_date=D2)
        ac = _seed_line(db, cid, so_b, code_b, source_system="autocount")
        db.commit()
        plan = backfill.run(
            db, [_row(so_a, code_a, D1), _row(so_b, code_b, D1)], apply=True
        )
        ids = {dated.id, ac.id}
        groups = (
            [l for l, _ in plan.single] + [l for l, _ in plan.ambiguous]
            + list(plan.undated) + list(plan.unmatched)
        )
        assert not ids & {l.id for l in groups}
        db.expire_all()
        assert db.get(SalesOrderLine, dated.id).required_date == D2
        assert db.get(SalesOrderLine, ac.id).required_date is None
