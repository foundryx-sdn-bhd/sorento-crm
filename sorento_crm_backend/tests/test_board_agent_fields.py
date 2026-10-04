"""Agent everywhere on the board (PLAN-demo-followups-19aug-ladder-v2.md, workstream A2).

`sales_orders.sales_agent_id` -> `sales_agents` (code column `sales_agent`, `person_label`).
The captain: capture and display the sales agent on every SO, so the planner can phone her.

Three surfaces, one join each - never a per-row lookup:

  * the board row (`_Row` / `_contribution` in `project_fulfilment_board_service.py`);
  * the cell drill-down's Documents list (`stock_detail`'s `sales_orders` rows);
  * the worklist row (`ProjectSOReconciliationService.list_fulfilment_planning`).

A line whose sales order carries no agent states so with `None`, never an invented code.

Postgres, blank scratch schema, every FK target seeded here (PRINCIPLES).
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from app.models.inventory import Stock, Warehouse
from app.models.order import Customer, SalesOrder, SalesOrderLine
from app.models.product import Product, ProductCategory, UnitOfMeasure
from app.models.sales_agent import SalesAgent

from ._pg_fixture import blank_session

MARKER = "zzt-board-agent"
TODAY = date(2026, 8, 19)


def _uid() -> str:
    return str(uuid.uuid4())


def _product(db, code: str) -> Product:
    uom = UnitOfMeasure(id=_uid(), uom_code=f"ZZT{_uid()[:6]}", uom_name="Unit")
    category = ProductCategory(
        id=_uid(), category_code=f"ZZT-{_uid()[:8]}", category_name=f"{MARKER} cat"
    )
    db.add_all([uom, category])
    db.flush()
    row = Product(
        id=_uid(),
        product_code=code,
        product_name=f"{MARKER} {code}",
        category_id=category.id,
        base_uom_id=uom.id,
        list_price=Decimal("100.00"),
    )
    db.add(row)
    db.flush()
    return row


def _warehouse(db, code: str) -> Warehouse:
    row = Warehouse(
        id=_uid(), warehouse_code=code, warehouse_name=code, is_active=True,
        segment="project", fulfilment_planning=True,
    )
    db.add(row)
    db.flush()
    return row


def _stock(db, product: Product, warehouse: Warehouse, *, on_hand: int):
    row = Stock(
        id=_uid(), product_id=product.id, warehouse_id=warehouse.id, quantity_on_hand=on_hand,
    )
    db.add(row)
    db.flush()
    return row


def _customer(db, name: str) -> Customer:
    row = Customer(id=_uid(), customer_code=f"ZZT-{_uid()[:8]}", customer_name=name)
    db.add(row)
    db.flush()
    return row


def _agent(db, code: str, *, person_label: str | None = None) -> SalesAgent:
    row = SalesAgent(id=_uid(), sales_agent=code, person_label=person_label)
    db.add(row)
    db.flush()
    return row


def _order(
    db,
    *,
    so_number: str,
    customer: Customer | None = None,
    agent: SalesAgent | None = None,
    order_date: date | None = None,
) -> SalesOrder:
    row = SalesOrder(
        id=_uid(),
        so_number=so_number,
        customer_id=customer.id if customer else None,
        sales_agent_id=agent.id if agent else None,
        order_date=order_date,
        demand_class="project",
        status="open",
    )
    db.add(row)
    db.flush()
    return row


def _line(
    db,
    order: SalesOrder,
    product: Product,
    *,
    qty: str,
    required_date: date | None,
    warehouse: Warehouse | None,
) -> SalesOrderLine:
    row = SalesOrderLine(
        id=_uid(),
        sales_order_id=order.id,
        product_id=product.id,
        warehouse_id=warehouse.id if warehouse else None,
        qty_ordered=Decimal(qty),
        qty_delivered=Decimal("0"),
        required_date=required_date,
        line_status="open",
        purchasing_status="not_reviewed",
    )
    db.add(row)
    db.flush()
    return row


def _board_service(db):
    from app.services.project_fulfilment_board_service import FulfilmentBoardService

    return FulfilmentBoardService(db)


def _cell(board, item_code: str, bucket_key: str) -> dict:
    return next(
        c for c in board["cells"]
        if c["item_code"] == item_code and c["bucket_key"] == bucket_key
    )


# --------------------------------------------------------------------------- #
# the board row
# --------------------------------------------------------------------------- #


def test_a_board_contribution_carries_the_seeded_agents_code_and_label():
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        warehouse = _warehouse(db, f"ZZT-{_uid()[:6]}"[:20])
        agent = _agent(db, f"ZZT{_uid()[:4]}", person_label=f"{MARKER} Jeremy")
        order = _order(
            db, so_number=f"ZZT-SO-{_uid()[:8]}", agent=agent, order_date=date(2026, 1, 1)
        )
        _line(db, order, product, qty="10", required_date=date(2026, 9, 3), warehouse=warehouse)

        board = _board_service(db).build([order.so_number], granularity="week", as_of=TODAY)

        contribution = _cell(board, product.product_code, "2026-08-31")["contributions"][0]
        assert contribution["agent_code"] == agent.sales_agent
        assert contribution["agent_label"] == agent.person_label


def test_a_board_contribution_states_no_agent_rather_than_inventing_one():
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        warehouse = _warehouse(db, f"ZZT-{_uid()[:6]}"[:20])
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, order, product, qty="10", required_date=date(2026, 9, 3), warehouse=warehouse)

        board = _board_service(db).build([order.so_number], granularity="week", as_of=TODAY)

        contribution = _cell(board, product.product_code, "2026-08-31")["contributions"][0]
        assert contribution["agent_code"] is None
        assert contribution["agent_label"] is None


def test_two_lines_of_two_different_agents_each_keep_their_own_code():
    """One join over the whole board's demand, so a second seeded agent proves it is not a
    single value smeared across every row."""
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        warehouse = _warehouse(db, f"ZZT-{_uid()[:6]}"[:20])
        jeremy = _agent(db, f"ZZTJ{_uid()[:4]}", person_label=f"{MARKER} Jeremy")
        cindy = _agent(db, f"ZZTC{_uid()[:4]}", person_label=f"{MARKER} CONTACT W")
        first = _order(
            db, so_number="ZZT-SO-AGT1", agent=jeremy, order_date=date(2026, 1, 1)
        )
        second = _order(
            db, so_number="ZZT-SO-AGT2", agent=cindy, order_date=date(2026, 1, 1)
        )
        _line(db, first, product, qty="10", required_date=date(2026, 9, 3), warehouse=warehouse)
        _line(db, second, product, qty="10", required_date=date(2026, 9, 3), warehouse=warehouse)

        board = _board_service(db).build(
            ["ZZT-SO-AGT1", "ZZT-SO-AGT2"], granularity="week", as_of=TODAY
        )

        by_order = {
            c["so_number"]: c["agent_code"]
            for c in _cell(board, product.product_code, "2026-08-31")["contributions"]
        }
        assert by_order == {"ZZT-SO-AGT1": jeremy.sales_agent, "ZZT-SO-AGT2": cindy.sales_agent}


# --------------------------------------------------------------------------- #
# the cell drill-down's Documents list (`stock_detail`)
# --------------------------------------------------------------------------- #


def test_the_stock_detail_documents_list_carries_the_sales_orders_agent():
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        warehouse = _warehouse(db, f"ZZT-{_uid()[:6]}"[:20])
        _stock(db, product, warehouse, on_hand=10)
        agent = _agent(db, f"ZZT{_uid()[:4]}", person_label=f"{MARKER} Tera")
        customer = _customer(db, f"{MARKER} customer")
        order = _order(
            db, so_number=f"ZZT-SO-{_uid()[:8]}", customer=customer, agent=agent,
            order_date=date(2026, 1, 1),
        )
        _line(db, order, product, qty="10", required_date=date(2026, 9, 3), warehouse=warehouse)

        detail = _board_service(db).stock_detail(str(product.id), str(warehouse.id))

        assert len(detail["sales_orders"]) == 1
        assert detail["sales_orders"][0]["agent_code"] == agent.sales_agent


def test_the_stock_detail_documents_list_states_no_agent_rather_than_inventing_one():
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        warehouse = _warehouse(db, f"ZZT-{_uid()[:6]}"[:20])
        _stock(db, product, warehouse, on_hand=10)
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, order, product, qty="10", required_date=date(2026, 9, 3), warehouse=warehouse)

        detail = _board_service(db).stock_detail(str(product.id), str(warehouse.id))

        assert detail["sales_orders"][0]["agent_code"] is None


# --------------------------------------------------------------------------- #
# the worklist row
# --------------------------------------------------------------------------- #


def _worklist(db, **kwargs):
    from app.services.project_so_reconciliation_service import ProjectSOReconciliationService

    return ProjectSOReconciliationService(db).list_fulfilment_planning(**kwargs)


def test_a_not_started_worklist_row_carries_its_sales_orders_agent():
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        warehouse = _warehouse(db, f"ZZT-{_uid()[:6]}"[:20])
        agent = _agent(db, f"ZZT{_uid()[:4]}", person_label=f"{MARKER} Brendon")
        order = _order(
            db, so_number=f"ZZT-SO-{_uid()[:8]}", agent=agent, order_date=date(2026, 1, 1)
        )
        _line(db, order, product, qty="10", required_date=date(2026, 9, 3), warehouse=warehouse)

        result = _worklist(db, sales_order_id=str(order.id))

        assert len(result["data"]) == 1
        row = result["data"][0]
        assert row["row_kind"] == "sales_order"
        assert row["agent_code"] == agent.sales_agent
        assert row["agent_label"] == agent.person_label


def test_a_worklist_row_states_no_agent_rather_than_inventing_one():
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        warehouse = _warehouse(db, f"ZZT-{_uid()[:6]}"[:20])
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, order, product, qty="10", required_date=date(2026, 9, 3), warehouse=warehouse)

        result = _worklist(db, sales_order_id=str(order.id))

        assert len(result["data"]) == 1
        assert result["data"][0]["agent_code"] is None
        assert result["data"][0]["agent_label"] is None
