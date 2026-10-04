"""STOCK-TOTAL-OS-SCOPE: the chatbot stock reply's Total O/S respects warehouse visibility.

`documentation/plans/chatbot/PLAN-stock-total-os-scope.md`, owner decision 2 Oct 2026 (a):

- Total O/S = open SO qty on the warehouses the contact may see (the same
  `warehouse_criterion` the location lines are filtered by).
- Open SO lines with NO warehouse are not shown anywhere in this reply (owner option b,
  2 Oct 2026): not in Total O/S and not on a line of their own.
- Open SO on a hidden warehouse is never shown or hinted.
- No contact (staff grid): unchanged, the product total.
- The open-SO aggregates are company scoped.

Postgres only, blank schema, every row seeded here (CI's database has none).
"""
from __future__ import annotations

import json
import uuid

import pytest

# MUST be first app import - resolves a circular import in app.modules.runtime.guards
from app.main import app  # noqa: E402,F401

from app.api.v1.inventory.stock import _with_sellable
from app.models.access import RespondContact, StockVisibilityPolicy
from app.models.base import set_company_scope
from app.models.order import SalesOrder, SalesOrderLine
from app.services.company_scope import DEFAULT_COMPANY_ID
from app.services.inventory_service import StockService

from tests._mc_lookup_seed import MOCHA_ID, product, seed_mocha, stock, warehouse
from tests._pg_fixture import blank_session, unique_code


@pytest.fixture
def db():
    with blank_session() as session:
        set_company_scope(session, frozenset({DEFAULT_COMPANY_ID}))
        yield session


def _contact(db) -> RespondContact:
    row = RespondContact(
        id=unique_code("CONTACT"),
        phone_number=f"+60{uuid.uuid4().int % 10**9:09d}",
        name="ZZT Contact",
    )
    db.add(row)
    db.flush()
    return row


def _policy(
    db, contact, *, mode, warehouse_ids=None, excluded_warehouse_ids=None, hide_zero_locations=False
):
    db.add(
        StockVisibilityPolicy(
            id=str(uuid.uuid4()),
            contact_id=contact.id,
            mode=mode,
            warehouse_ids=warehouse_ids,
            excluded_warehouse_ids=excluded_warehouse_ids,
            hide_zero_locations=hide_zero_locations,
        )
    )
    db.flush()


def _so_line(db, prod_id, wh_id, *, open_qty, company_id=DEFAULT_COMPANY_ID):
    so_id = str(uuid.uuid4())
    db.add(SalesOrder(id=so_id, so_number=unique_code("SO"), company_id=company_id))
    db.flush()
    db.add(
        SalesOrderLine(
            id=str(uuid.uuid4()),
            sales_order_id=so_id,
            product_id=prod_id,
            warehouse_id=wh_id,
            qty_ordered=open_qty,
            qty_delivered=0,
            line_status="open",
            company_id=company_id,
        )
    )
    db.flush()


def _seed(db, *, unassigned=3):
    """Visible W1 (on hand 54, open SO 10), hidden W2 (on hand 40, open SO 7), and
    `unassigned` open SO with no warehouse."""
    w1 = warehouse(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("VIS"))
    w2 = warehouse(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("HID"))
    p = product(db, company_id=DEFAULT_COMPANY_ID)
    stock(db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=w1.id, on_hand=54)
    stock(db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=w2.id, on_hand=40)
    _so_line(db, p.id, w1.id, open_qty=10)
    _so_line(db, p.id, w2.id, open_qty=7)
    if unassigned:
        _so_line(db, p.id, None, open_qty=unassigned)
    return w1, w2, p


def _sellable_body(db, **kwargs) -> dict:
    service = StockService(db)
    result = service.list_stock(**kwargs)
    return json.loads(_with_sellable(service, result).body)


# ------------------------------------------------------------------- compact (the reply)


@pytest.mark.parametrize("rule", ["include", "exclude"])
def test_compact_total_os_counts_only_visible_warehouses(db, rule):
    w1, w2, p = _seed(db)
    contact = _contact(db)
    if rule == "include":
        _policy(db, contact, mode="compact", warehouse_ids=[w1.id])
    else:
        _policy(db, contact, mode="compact", excluded_warehouse_ids=[w2.id])

    body = _sellable_body(db, product_ids=[p.id], contact_id=contact.id)

    entry = body["stock_summary"][0]
    assert entry["total_on_hand"] == 54
    assert entry["open_so_qty"] == 10, "hidden W2's 7 and the unassigned 3 are not Total O/S"
    assert "unassigned_open_so_qty" not in entry
    assert entry["sellable"] == 44
    assert [loc["open_so_qty"] for loc in entry["locations"]] == [10]


def test_compact_hidden_warehouse_open_so_is_never_in_the_body(db):
    """Neither the hidden warehouse's 7, the unassigned 3, nor any sum that includes them
    (13, 17, 20), appears anywhere in the answer."""
    w1, w2, p = _seed(db)
    contact = _contact(db)
    _policy(db, contact, mode="compact", warehouse_ids=[w1.id])

    body = _sellable_body(db, product_ids=[p.id], contact_id=contact.id)

    def numbers(node):
        if isinstance(node, dict):
            for v in node.values():
                yield from numbers(v)
        elif isinstance(node, list):
            for v in node:
                yield from numbers(v)
        elif isinstance(node, int) and not isinstance(node, bool):
            yield node

    seen = set(numbers(body["stock_summary"]))
    assert not seen & {3, 7, 13, 17, 20}, seen


def test_compact_without_unassigned_lines_reads_the_same(db):
    w1, _w2, p = _seed(db, unassigned=0)
    contact = _contact(db)
    _policy(db, contact, mode="compact", warehouse_ids=[w1.id])

    entry = _sellable_body(db, product_ids=[p.id], contact_id=contact.id)["stock_summary"][0]

    assert entry["open_so_qty"] == 10
    assert "unassigned_open_so_qty" not in entry


def test_compact_every_warehouse_policy_counts_both(db):
    """A policy naming neither list = every active warehouse: Total O/S is W1 + W2, and
    the unassigned remainder is still not shown."""
    _w1, _w2, p = _seed(db)
    contact = _contact(db)
    _policy(db, contact, mode="compact")

    entry = _sellable_body(db, product_ids=[p.id], contact_id=contact.id)["stock_summary"][0]

    assert entry["open_so_qty"] == 17
    assert "unassigned_open_so_qty" not in entry


def test_compact_inactive_warehouse_open_so_is_not_counted(db):
    """The lines never answer from an inactive warehouse, so Total O/S does not either."""
    w1, w2, p = _seed(db, unassigned=0)
    w2.is_active = False
    db.flush()
    contact = _contact(db)
    _policy(db, contact, mode="compact")

    entry = _sellable_body(db, product_ids=[p.id], contact_id=contact.id)["stock_summary"][0]

    assert entry["open_so_qty"] == 10


# ------------------------------------------------------------------- detailed under policy


def test_detailed_summary_under_policy_counts_only_visible_warehouses(db):
    """The detailed payload's per-product summary (on hand and open SO) is the same
    visible-only answer: a hidden warehouse's stock is not hinted through it either."""
    w1, _w2, p = _seed(db)
    contact = _contact(db)
    _policy(db, contact, mode="detailed", warehouse_ids=[w1.id])

    body = _sellable_body(db, product_ids=[p.id], contact_id=contact.id)

    assert [row["open_so_qty"] for row in body["data"]] == [10]
    entry = body["stock_summary"][0]
    assert entry["total_on_hand"] == 54
    assert entry["open_so_qty"] == 10
    assert "unassigned_open_so_qty" not in entry
    assert entry["sellable"] == 44


# ------------------------------------------------------------------- staff path unchanged


def test_staff_call_without_contact_keeps_the_product_total(db):
    _w1, _w2, p = _seed(db)

    body = _sellable_body(db, product_ids=[p.id])

    entry = body["stock_summary"][0]
    assert entry["total_on_hand"] == 94
    assert entry["open_so_qty"] == 20
    assert "unassigned_open_so_qty" not in entry


# ------------------------------------------------------------------- company scope


def test_open_so_aggregates_are_company_scoped(db):
    seed_mocha(db)
    p = product(db, company_id=DEFAULT_COMPANY_ID)
    w_sorento = warehouse(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("S"))
    w_mocha = warehouse(db, company_id=MOCHA_ID, code=unique_code("M"))
    _so_line(db, p.id, w_sorento.id, open_qty=10)
    _so_line(db, p.id, w_mocha.id, open_qty=500, company_id=MOCHA_ID)
    _so_line(db, p.id, None, open_qty=40, company_id=MOCHA_ID)
    db.flush()
    service = StockService(db)

    set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
    assert service.open_so_qty_by_product([p.id]) == {p.id: 10}
    by_pair, unlocated = service.open_so_qty_by_product_warehouse([p.id])
    assert by_pair == {(p.id, w_sorento.id): 10}
    assert unlocated == {}

    set_company_scope(db, frozenset({DEFAULT_COMPANY_ID, MOCHA_ID}))
    assert service.open_so_qty_by_product([p.id]) == {p.id: 550}


@pytest.mark.parametrize("narrow", ["warehouse_ids", "warehouse_id"])
def test_compact_total_os_follows_a_warehouse_named_in_the_question(db, narrow):
    """"MWC7624 at W1" under an every-warehouse policy: the lines read W1 only, so the
    Total's O/S is W1's 10, not W1 + W2 (17)."""
    w1, _w2, p = _seed(db)
    contact = _contact(db)
    _policy(db, contact, mode="compact")
    kwargs = {"warehouse_ids": [w1.id]} if narrow == "warehouse_ids" else {"warehouse_id": w1.id}

    entry = _sellable_body(db, product_ids=[p.id], contact_id=contact.id, **kwargs)["stock_summary"][0]

    assert entry["total_on_hand"] == 54
    assert entry["open_so_qty"] == 10
    assert "unassigned_open_so_qty" not in entry


def test_detailed_summary_follows_a_warehouse_named_in_the_question(db):
    w1, _w2, p = _seed(db)
    contact = _contact(db)
    _policy(db, contact, mode="detailed")

    entry = _sellable_body(
        db, product_ids=[p.id], contact_id=contact.id, warehouse_ids=[w1.id]
    )["stock_summary"][0]

    assert entry["total_on_hand"] == 54
    assert entry["open_so_qty"] == 10


# ------------------------------------------- the Total agrees with the lines it sits over


@pytest.mark.parametrize("hide_zero", [False, True])
def test_compact_total_os_equals_the_sum_of_the_printed_lines(db, hide_zero):
    """Hand test of #1431 (dev data, SO414050): MWC7624-RL-S10 read `Total: 372 (O/S: 531)`
    over lines adding to 530. The extra 1 was an open SO line on BRW-IB, a VISIBLE active
    warehouse holding no stock row for the product, so it fed the Total and printed no line.
    The same gap opens when `hide_zero_locations` drops a 0-on-hand line that has open SO.
    Owner rule: the Total and the lines must agree, whichever way the gap is closed."""
    w1, _w2, p = _seed(db, unassigned=0)
    no_stock_row = warehouse(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("NOSTK"))
    _so_line(db, p.id, no_stock_row.id, open_qty=1)
    zero_line = warehouse(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("ZERO"))
    stock(db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=zero_line.id, on_hand=0)
    _so_line(db, p.id, zero_line.id, open_qty=2)
    contact = _contact(db)
    _policy(
        db,
        contact,
        mode="compact",
        warehouse_ids=[w1.id, no_stock_row.id, zero_line.id],
        hide_zero_locations=hide_zero,
    )

    entry = _sellable_body(db, product_ids=[p.id], contact_id=contact.id)["stock_summary"][0]

    printed = sum(loc["open_so_qty"] for loc in entry["locations"])
    assert entry["open_so_qty"] == printed, (entry["open_so_qty"], entry["locations"])
    # Owner ruling (a): the warehouse is printed as `<code>: 0 (O/S: n)`, never dropped,
    # including a 0-on-hand line `hide_zero_locations` would otherwise drop.
    lines = {loc["warehouse_code"]: loc for loc in entry["locations"]}
    assert entry["open_so_qty"] == 13
    assert lines[no_stock_row.warehouse_code] == {
        "warehouse_code": no_stock_row.warehouse_code, "quantity_on_hand": 0, "open_so_qty": 1,
    }
    assert lines[zero_line.warehouse_code]["quantity_on_hand"] == 0
    assert lines[zero_line.warehouse_code]["open_so_qty"] == 2
    codes = [loc["warehouse_code"] for loc in entry["locations"]]
    assert codes == sorted(codes)
    assert entry["total_on_hand"] == 54


def test_hide_zero_still_drops_a_zero_line_with_no_open_so(db):
    """The ruling prints a hidden line only when it carries O/S: a 0-on-hand warehouse with
    no open SO stays dropped under `hide_zero_locations`."""
    w1, _w2, p = _seed(db, unassigned=0)
    empty = warehouse(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("EMPTY"))
    stock(db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=empty.id, on_hand=0)
    contact = _contact(db)
    _policy(db, contact, mode="compact", warehouse_ids=[w1.id, empty.id], hide_zero_locations=True)

    entry = _sellable_body(db, product_ids=[p.id], contact_id=contact.id)["stock_summary"][0]

    assert [loc["warehouse_code"] for loc in entry["locations"]] == [w1.warehouse_code]
    assert entry["open_so_qty"] == 10
