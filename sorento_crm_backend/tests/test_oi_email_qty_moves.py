"""EMAIL-SO-CHANGES (crew audit, 12 Oct; crew answer (b)): a CANCEL BALANCE or a quantity
INCREASE prints the SO line's QTY -> NEW QTY like every other change, taken from the
structured change values its writer already holds. No note parsing, no new columns, and the
Order Inquiries Was/Now dialog is untouched.

* SO amendment: the delta row's own `from_value` / `to_value` (field `qty`).
* Book change (planning change `qty_down`): the batch row's `from_json` / `to_json` qty.
* Confirm exception ("placed more than the new need"): `placed` / `need`.

One email line per SO line: an increase the netting splits over two rows (part covered by a
pool, the rest bought) prints once, with every split row's verb still in the headline.
"""
from __future__ import annotations

from decimal import Decimal

from app.models.project_so import (
    AMENDMENT_PUBLISHED,
    INQUIRY_CANCELLED,
    INQUIRY_PLACED,
    IV_CANCEL_BALANCE,
    OrderInquiryRow,
    SOAmendment,
)
from app.services.project_order_inquiry_engine import POOL_PRE_ORDER, CoveringPool
from app.services.project_order_inquiry_service import ProjectOrderInquiryService

from .test_order_inquiry_handover_automation import (
    _captured_dispatches,
    _confirm,
    _handover_calls,
    _line_payload,
    _raise_one_row,
    _register,
    api,
    world,
)

__all__ = ["api", "world"]  # re-exported fixtures


def _amend(world, fixture, *, verb, from_qty, to_qty):
    amendment = SOAmendment(
        company_id=world.company_id,
        project_sales_order_id=fixture["order"].id,
        from_version_kind="schedule",
        status=AMENDMENT_PUBLISHED,
        delta_json={
            "rows": [
                {
                    "row_key": "0",
                    "verb": verb,
                    "so_line_id": str(fixture["line"].id),
                    "product_id": str(world.product.id),
                    "product_code": world.product.product_code,
                    "field": "qty",
                    "qty": str(abs(Decimal(to_qty) - Decimal(from_qty))),
                    "from_value": from_qty,
                    "to_value": to_qty,
                }
            ]
        },
    )
    world.db.add(amendment)
    world.db.flush()
    return amendment


def _lines(calls, item_code):
    matches = _handover_calls(calls)
    assert matches, "the write must dispatch the handover"
    handover = matches[-1]["context"]["handover"]
    return handover, [l for l in handover["lines"] if l["item_code"] == item_code]


def test_amendment_cut_prints_line_before_and_after(api, monkeypatch):
    """SO amendment 10 -> 6: one CANCEL BALANCE row of 4, printed QTY 10 / NEW QTY 6."""
    _client, world = api
    _register(world)
    calls = _captured_dispatches(monkeypatch)
    fixture = _raise_one_row(api, qty="10")
    calls.clear()

    amendment = _amend(world, fixture, verb="CANCEL BALANCE", from_qty="10", to_qty="6")
    ProjectOrderInquiryService(world.db).derive_for_amendment(
        amendment, actor_user_id=world.cs_user
    )
    world.db.commit()

    handover, lines = _lines(calls, world.product.product_code)
    assert len(lines) == 1, lines
    assert lines[0]["qty"] == "6" and lines[0]["was"] == {"qty": "10"}, lines[0]
    assert "CANCEL BALANCE" in handover["headline"], handover["headline"]


def test_amendment_increase_prints_line_before_and_after(api, monkeypatch):
    """SO amendment 10 -> 14: one ORDER row of 4, printed QTY 10 / NEW QTY 14."""
    _client, world = api
    _register(world)
    calls = _captured_dispatches(monkeypatch)
    fixture = _raise_one_row(api, qty="10")
    calls.clear()

    amendment = _amend(world, fixture, verb="ORDER", from_qty="10", to_qty="14")
    ProjectOrderInquiryService(world.db).derive_for_amendment(
        amendment, actor_user_id=world.cs_user
    )
    world.db.commit()

    _handover, lines = _lines(calls, world.product.product_code)
    assert len(lines) == 1, lines
    assert lines[0]["qty"] == "14" and lines[0]["was"] == {"qty": "10"}, lines[0]


def test_split_increase_prints_one_line_per_so_line(api, monkeypatch):
    """10 -> 14 where a pre-order pool covers 2 of the 4: two rows (PRE-ORDERED 2, ORDER 2),
    ONE email line QTY 10 / NEW QTY 14, both verbs in the headline."""
    _client, world = api
    _register(world)
    calls = _captured_dispatches(monkeypatch)
    fixture = _raise_one_row(api, qty="10")
    calls.clear()
    pool = CoveringPool(
        kind=POOL_PRE_ORDER, reference="ZZT-PRE-1", product_id=str(world.product.id),
        qty=Decimal("2"),
    )
    monkeypatch.setattr(ProjectOrderInquiryService, "_pools", lambda self, order, demand: [pool])

    amendment = _amend(world, fixture, verb="ORDER", from_qty="10", to_qty="14")
    inquiry = ProjectOrderInquiryService(world.db).derive_for_amendment(
        amendment, actor_user_id=world.cs_user
    )
    world.db.commit()

    rows = world.db.query(OrderInquiryRow).filter(
        OrderInquiryRow.order_inquiry_id == inquiry.id
    ).all()
    assert sorted(Decimal(str(r.qty)) for r in rows) == [Decimal("2"), Decimal("2")], (
        "sanity: the netting split the increase over two rows"
    )

    handover, lines = _lines(calls, world.product.product_code)
    assert len(lines) == 1, f"one line per SO line, not per row: {lines}"
    assert lines[0]["qty"] == "14" and lines[0]["was"] == {"qty": "10"}, lines[0]
    assert "ORDER" in handover["headline"] and "PRE-ORDERED" in handover["headline"], (
        handover["headline"]
    )


def test_confirm_cut_below_placed_prints_placed_and_new_need(api, monkeypatch):
    """Purchasing placed 10 (no link), Confirm now asks for 6: the exception CANCEL
    BALANCE row (qty 4) prints QTY 10 / NEW QTY 6, off `placed` / `need`."""
    client, world = api
    _register(world)
    calls = _captured_dispatches(monkeypatch)
    fixture = _raise_one_row(api, qty="10")
    row = fixture["row"]
    row.state = INQUIRY_PLACED
    # The book cut the line to 6.
    fixture["core_line"].qty_ordered = Decimal("6")
    fixture["line"].qty = Decimal("6")
    world.db.commit()
    calls.clear()

    response = _confirm(client, fixture["order"].id, [_line_payload(fixture["line"].id, buy_qty="6")])
    assert response.status_code == 200, response.text
    world.db.commit()

    cancel = [
        r for r in world.db.query(OrderInquiryRow).filter(
            OrderInquiryRow.so_line_id == fixture["line"].id,
            OrderInquiryRow.verb == IV_CANCEL_BALANCE,
            OrderInquiryRow.state != INQUIRY_CANCELLED,
        )
    ]
    assert len(cancel) == 1 and Decimal(str(cancel[0].qty)) == Decimal("4"), cancel

    _handover, lines = _lines(calls, world.product.product_code)
    told = [l for l in lines if l["was"] and l["was"].get("qty")]
    assert len(told) == 1, lines
    assert told[0]["qty"] == "6" and told[0]["was"] == {"qty": "10"}, told[0]


def test_book_change_qty_down_prints_from_and_to(api, monkeypatch):
    """A planning change `qty_down` row (the "What changed" source) reaches
    `derive_for_book_change` with its from/to qty, and prints them."""
    _client, world = api
    _register(world)
    calls = _captured_dispatches(monkeypatch)
    fixture = _raise_one_row(api, qty="10")
    calls.clear()

    from app.services.project_order_inquiry_service import CHANGE_QTY_DECREASE

    ProjectOrderInquiryService(world.db).derive_for_book_change(
        fixture["order"],
        [
            {
                "line_id": str(fixture["line"].id),
                "product_id": str(world.product.id),
                "item_code": world.product.product_code,
                "qty": Decimal("4"),
                "delivery_date": fixture["row"].delivery_date,
                "stock_location": None,
                "change": CHANGE_QTY_DECREASE,
                "note": "Was 10, now 6",
                "from_qty": Decimal("10"),
                "to_qty": Decimal("6"),
            }
        ],
        batch_id="zzt-batch",
        actor_user_id=world.cs_user,
    )
    world.db.commit()

    _handover, lines = _lines(calls, world.product.product_code)
    assert len(lines) == 1, lines
    assert lines[0]["qty"] == "6" and lines[0]["was"] == {"qty": "10"}, lines[0]


def _amend_rows(world, fixture, rows):
    amendment = SOAmendment(
        company_id=world.company_id,
        project_sales_order_id=fixture["order"].id,
        from_version_kind="schedule",
        status=AMENDMENT_PUBLISHED,
        delta_json={
            "rows": [
                {
                    "row_key": str(index),
                    "so_line_id": str(fixture["line"].id),
                    "product_id": str(world.product.id),
                    "product_code": world.product.product_code,
                    **row,
                }
                for index, row in enumerate(rows)
            ]
        },
    )
    world.db.add(amendment)
    world.db.flush()
    return amendment


_DATE_AND_CUT = [
    {"verb": "DELAY", "field": "delivery_date", "qty": "6",
     "from_value": "2026-08-25", "to_value": "2026-09-10"},
    {"verb": "CANCEL BALANCE", "field": "qty", "qty": "4", "from_value": "10", "to_value": "6"},
]


def _date_line_and_qty_line(lines):
    dated = [l for l in lines if (l["was"] or {}).get("delivery_date")]
    qty = [l for l in lines if (l["was"] or {}).get("qty")]
    assert len(dated) == 1 and len(qty) == 1 and len(lines) == 2, lines
    assert dated[0]["was"] == {"delivery_date": "25/08/2026"}, dated[0]
    assert qty[0]["qty"] == "6" and qty[0]["was"] == {"qty": "10"}, qty[0]


def test_a_date_move_and_a_qty_move_on_one_line_print_as_their_own_lines(api, monkeypatch):
    """Reviewer B1: one amendment moves a line's date AND cuts it. The DELAY row keeps
    its own line and its date Was; the qty move prints QTY 10 / NEW QTY 6 on the other."""
    _client, world = api
    _register(world)
    calls = _captured_dispatches(monkeypatch)
    fixture = _raise_one_row(api, qty="10")
    calls.clear()

    amendment = _amend_rows(world, fixture, _DATE_AND_CUT)
    ProjectOrderInquiryService(world.db).derive_for_amendment(
        amendment, actor_user_id=world.cs_user
    )
    world.db.commit()

    _handover, lines = _lines(calls, world.product.product_code)
    _date_line_and_qty_line(lines)
