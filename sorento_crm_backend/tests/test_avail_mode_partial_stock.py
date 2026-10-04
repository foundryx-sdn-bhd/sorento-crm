"""AVAIL-MODE-REPLIES rule 2 (owner, 2 Oct 2026): an availability-only contact asking Q of a
product that HAS stock, but less than Q, with Q within the max quantity for the product's
category (X), is told "got stock" AND how many are available.

Today (main 7eb9767a) that case fell to `incoming` / `no_incoming`, i.e. "no stock", which
is false. It stays branch `in_stock` (no new `stock_asks.branch` value, so no migration); the
entry carries `available_qty` in that case only. Above X nothing changes (owner Q5 (a)).

Plan: documentation/plans/chatbot/PLAN-avail-mode-replies-02oct.md.
"""
from __future__ import annotations

import pytest

from app.main import app  # noqa: F401  (first app import, see test_stock_availability_block)
from app.services.company_scope import DEFAULT_COMPANY_ID
from app.services.inventory_service import StockService

from tests._mc_lookup_seed import product, stock
from tests.test_stock_availability_block import (  # noqa: F401  (fixtures)
    _category_of,
    _contact,
    _entry,
    _policy_row,
    _so_line,
    _wh,
    client,
    db,
)


def _ask(db, *, on_hand, q, x, open_so=0):
    brw = _wh(db, "ZZTBRW")
    p = product(db, company_id=DEFAULT_COMPANY_ID)
    _category_of(db, p).chatbot_max_qty = x
    stock(db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=brw.id, on_hand=on_hand)
    if open_so:
        _so_line(db, product_id=p.id, warehouse_id=brw.id, ordered=open_so)
    contact = _contact(db)
    _policy_row(db, mode="availability", warehouse_ids=[brw.id], contact=contact)
    db.flush()
    result = StockService(db).list_stock(
        product_ids=[p.id], contact_id=contact.id, requested_quantities={p.id: q}
    )
    return _entry(result, p.id)


def test_some_stock_short_of_q_within_x_is_in_stock_with_the_count(db):
    entry = _ask(db, on_hand=30, q=50, x=100)
    assert entry["branch"] == "in_stock"
    assert entry["available_qty"] == 30


def test_open_so_is_subtracted_before_the_count(db):
    entry = _ask(db, on_hand=100, q=50, x=200, open_so=60)
    assert entry["branch"] == "in_stock"
    assert entry["available_qty"] == 40


def test_q_covered_carries_no_count(db):
    entry = _ask(db, on_hand=100, q=50, x=100)
    assert entry["branch"] == "in_stock"
    assert entry["available_qty"] is None


def test_q_exactly_available_carries_no_count(db):
    entry = _ask(db, on_hand=50, q=50, x=100)
    assert entry["branch"] == "in_stock"
    assert entry["available_qty"] is None


def test_above_x_is_too_big_and_carries_no_count(db):
    entry = _ask(db, on_hand=30, q=150, x=100)
    assert entry["branch"] == "too_big"
    assert entry["available_qty"] is None


def test_x_unset_is_too_big_even_with_stock(db):
    entry = _ask(db, on_hand=30, q=5, x=None)
    assert entry["branch"] == "too_big"
    assert entry["available_qty"] is None


@pytest.mark.parametrize("on_hand,open_so", [(0, 0), (10, 10), (10, 25)])
def test_nothing_available_after_open_so_is_still_no_stock(db, on_hand, open_so):
    entry = _ask(db, on_hand=on_hand, q=5, x=100, open_so=open_so)
    assert entry["branch"] == "no_incoming"
    assert entry["available_qty"] is None


def test_the_route_declares_available_qty(client, db):
    """`response_model` drops an undeclared key (LESSONS-LEARNT), so the wire must carry it."""
    import json

    brw = _wh(db, "ZZTBRW")
    p = product(db, company_id=DEFAULT_COMPANY_ID)
    _category_of(db, p).chatbot_max_qty = 100
    stock(db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=brw.id, on_hand=7)
    contact = _contact(db)
    _policy_row(db, mode="availability", warehouse_ids=[brw.id], contact=contact)
    db.flush()

    res = client.get(
        "/api/v1/inventory/stock/balance",
        params={
            "product_ids": p.id,
            "contact_id": contact.id,
            "space_id": "364817",
            "requested_quantities": json.dumps({p.id: 9}),
        },
    )
    assert res.status_code == 200, res.text
    (entry,) = [e for e in res.json()["stock_availability"] if e["product_id"] == p.id]
    assert entry["branch"] == "in_stock"
    assert entry["available_qty"] == 7


def test_salesman_outcome_names_the_count_on_a_short_in_stock():
    from app.services.stock_ask_service import outcome_phrase

    assert outcome_phrase("in_stock", available_qty=30, quantity=50) == "in stock, 30 of 50 available"
    assert outcome_phrase("in_stock") == "in stock"
    assert outcome_phrase("too_big", available_qty=30, quantity=50) == "too big"
