"""Phase 2 RED tests - REPORT-DRILLDOWN, the route half (`GET /api/v1/order-management/report-ask`).

`documentation/plans/chatbot/PLAN-report-drilldown-08oct.md` (design 1 to 3) and the approved
card `report-drilldown-behaviour-card.md` revision 3 (owner answers 8 Oct 2026).

* Every ranked row carries `keys`: the ids its name stands for (warehouse codes for a location,
  `"none"` for the "(no agent)" row), so a follow-up can drill into exactly the rows shown.
  Channel and month rows carry none (not a drill source).
* `customer_agent_ids` is the customer CARD link (owner Q1): customers whose
  `customers.sales_agent_id` is one of the agents, ranked over ALL their sales order lines,
  whoever the SO's agent is, with every other filter kept. Echoed "Customers assigned to".
* `"none"` in `sales_agent_ids` / `customer_agent_ids`: no agent on the SO / on the card.

Postgres only, every row seeded here (CI's database has none).
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from tests._report_ask_so_seed import seed_so_line
from tests._sales_report_do_seed import (  # noqa: F401  (client / db are fixtures)
    _contact,
    _link,
    client,
    db,
    money,
    seed_agent,
    seed_customer,
)
from tests._pg_fixture import unique_code
from tests.test_report_ask_route import (
    AUG,
    DEALER_MESSAGE,
    _ask,
    _code,
    _full,
    _ok,
    _prod,
    _rows,
)
from tests._sales_report_do_seed import warehouse
from app.services.company_scope import DEFAULT_COMPANY_ID

D = Decimal


def _world(db):
    """Two agents on the customer CARD, sales sold by other agents too.

        customer  card agent   sales (Sep, product ZZTDD-1 unless said)
        c1        G1           G1 sells 100/1, G2 sells 300/3
        c2        G1           G2 sells 50/1 ; ZZTEE-1 by G1 900/9 (other product)
        c3        G2           G2 sells 70/1
        c4        (none)       G1 sells 20/1 ; (no agent) sells 5/1
        c5        G1           G1 sells 400/4 in AUGUST (outside the period)
    """
    w = type("W", (), {})()
    w.wh = warehouse(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("WH"))
    w.p = _prod(db, "ZZTDD-1")
    w.q = _prod(db, "ZZTEE-1")
    w.g1, w.g2 = seed_agent(db, code="ZZT-DG-1"), seed_agent(db, code="ZZT-DG-2")
    w.c1, w.c2, w.c3, w.c4, w.c5 = (seed_customer(db, name=f"ZZT DRILL C{n}") for n in range(1, 6))
    w.c1.sales_agent_id = w.g1.id
    w.c2.sales_agent_id = w.g1.id
    w.c3.sales_agent_id = w.g2.id
    w.c5.sales_agent_id = w.g1.id
    db.flush()

    def sale(cust, agent, qty, total, prod=None, when=None):
        kw = {"order_date": when} if when else {}
        seed_so_line(db, cust=cust, prod=prod or w.p, qty=qty, total=total, agent=agent, wh=w.wh, **kw)

    sale(w.c1, w.g1, 1, "100.00")
    sale(w.c1, w.g2, 3, "300.00")
    sale(w.c2, w.g2, 1, "50.00")
    sale(w.c2, w.g1, 9, "900.00", prod=w.q)
    sale(w.c3, w.g2, 1, "70.00")
    sale(w.c4, w.g1, 1, "20.00")
    sale(w.c4, None, 1, "5.00")
    sale(w.c5, w.g1, 4, "400.00", when=AUG)
    db.commit()
    return w


def _keys(body):
    return {r["name"]: sorted(r["keys"]) for r in body["rows"]}


# ======================================================================== row keys


def test_sales_agent_rows_carry_their_ids_and_no_agent_carries_none(client, db):
    w = _world(db)
    body = _ok(client, _full(db), group_by="sales_agent", top_n=10, product_code="ZZTDD")
    assert _keys(body) == {"ZZT-DG-2": [w.g2.id], "ZZT-DG-1": [w.g1.id], "(no agent)": ["none"]}, body["rows"]


def test_customer_product_brand_category_rows_carry_their_ids(client, db):
    w = _world(db)
    body = _ok(client, _full(db), group_by="customer", top_n=10, product_code="ZZTDD")
    assert _keys(body)["ZZT DRILL C1"] == [w.c1.id], body["rows"]
    body = _ok(client, _full(db), group_by="product", top_n=10, sales_agent_ids=[w.g1.id])
    assert _keys(body) == {"ZZTEE-1": [w.q.id], "ZZTDD-1": [w.p.id]}, body["rows"]


def test_location_rows_carry_the_warehouse_code(client, db):
    w = _world(db)
    body = _ok(client, _full(db), group_by="location", top_n=10, product_code="ZZTDD")
    assert _keys(body) == {w.wh.warehouse_code: [w.wh.warehouse_code]}, body["rows"]


@pytest.mark.parametrize("group_by", ["channel", "month"])
def test_channel_and_month_rows_carry_no_keys(client, db, group_by):
    _world(db)
    body = _ok(client, _full(db), group_by=group_by, top_n=10, product_code="ZZTDD")
    assert body["rows"] and all(r["keys"] == [] for r in body["rows"]), body["rows"]


def test_a_total_has_no_rows_and_keys_are_only_for_rows_shown(client, db):
    w = _world(db)
    body = _ok(client, _full(db), group_by="sales_agent", top_n=1, product_code="ZZTDD")
    assert [r["name"] for r in body["rows"]] == ["ZZT-DG-2"] and _keys(body) == {"ZZT-DG-2": [w.g2.id]}, body


# ======================================================================== the card link


def test_customer_agent_ids_ranks_the_card_customers_over_every_so_agent(client, db):
    """G1's card customers c1, c2 (c5 sold in August only). Product ZZTDD, September:
    c1 = 100 (G1) + 300 (G2) = 400/4 ; c2 = 50/1 (sold by G2). c4 is G1's SALE but not G1's card."""
    w = _world(db)
    body = _ok(client, _full(db), group_by="customer", top_n=10, product_code="ZZTDD",
               customer_agent_ids=[w.g1.id])
    assert _rows(body) == [("ZZT DRILL C1", 4, D("400.00")), ("ZZT DRILL C2", 1, D("50.00"))], body["rows"]
    assert money(body["total"]["amount"]) == D("450.00"), body["total"]


def test_customer_agent_ids_keeps_the_period(client, db):
    w = _world(db)
    body = _ok(client, _full(db), group_by="customer", top_n=10, customer_agent_ids=[w.g1.id],
               date_from="2026-08-01", date_to="2026-08-31")
    assert _rows(body) == [("ZZT DRILL C5", 4, D("400.00"))], body["rows"]


def test_customer_agent_ids_none_is_customers_with_no_card_agent(client, db):
    w = _world(db)
    body = _ok(client, _full(db), group_by="customer", top_n=10, product_code="ZZTDD",
               customer_agent_ids=["none", w.g2.id])
    assert _rows(body) == [("ZZT DRILL C3", 1, D("70.00")), ("ZZT DRILL C4", 2, D("25.00"))], body["rows"]


def test_sales_agent_ids_none_is_orders_with_no_agent(client, db):
    _world(db)
    body = _ok(client, _full(db), group_by="customer", top_n=10, product_code="ZZTDD",
               sales_agent_ids=["none"])
    assert _rows(body) == [("ZZT DRILL C4", 1, D("5.00"))], body["rows"]


def test_the_card_link_is_echoed_as_customers_assigned_to(client, db):
    w = _world(db)
    body = _ok(client, _full(db), group_by="customer", top_n=10, product_code="ZZTDD",
               customer_agent_ids=[w.g1.id, "none"])
    echo = {f["key"]: (f["label"], f["values"]) for f in body["filters"]}
    assert echo["customer_agent"] == ("Customers assigned to", ["(no agent)", "ZZT-DG-1"]), body["filters"]


def test_sales_agent_none_is_echoed_as_no_agent(client, db):
    w = _world(db)
    body = _ok(client, _full(db), group_by="customer", top_n=10, sales_agent_ids=["none", w.g2.id])
    echo = {f["key"]: f["values"] for f in body["filters"]}
    assert echo["sales_agent"] == ["(no agent)", "ZZT-DG-2"], body["filters"]


def test_an_unknown_card_agent_id_is_404(client, db):
    _world(db)
    resp = _ask(client, _full(db), group_by="customer", top_n=10,
                customer_agent_ids=["00000000-0000-0000-0000-000000000001"])
    assert resp.status_code == 404, resp.text


def test_a_malformed_card_agent_id_is_the_uuid_error(client, db):
    """The route's own convention for every id list (`parse_uuid_list`)."""
    _world(db)
    resp = _ask(client, _full(db), group_by="customer", top_n=10, customer_agent_ids=["nope"])
    assert resp.status_code == 400 and "INVALID_UUID" in resp.text and "customer_agent_ids" in resp.text, resp.text


def test_a_dealer_may_not_use_the_card_link(client, db):
    w = _world(db)
    contact = _contact(db)
    _link(db, contact, w.c1)
    db.commit()
    resp = _ask(client, contact, group_by="product", top_n=3, customer_agent_ids=[w.g1.id])
    assert resp.status_code == 403 and _code(resp) == "report_dimension_not_allowed", resp.text
    assert DEALER_MESSAGE in resp.text


# ======================================================================== review round 1


def test_the_none_key_reads_in_any_case(client, db):
    _world(db)
    body = _ok(client, _full(db), group_by="customer", top_n=10, product_code="ZZTDD", sales_agent_ids=["NONE"])
    assert _rows(body) == [("ZZT DRILL C4", 1, D("5.00"))], body["rows"]


def test_customer_agent_ids_has_the_route_cap(client, db):
    _world(db)
    ids = [f"{i:08d}-0000-4000-8000-000000000000" for i in range(51)]
    resp = _ask(client, _full(db), group_by="customer", top_n=10, customer_agent_ids=ids)
    assert resp.status_code == 422 and _code(resp) == "too_many_values", resp.text


def test_card_agent_none_never_matches_a_line_with_no_customer(client, db):
    """`customers.sales_agent_id IS NULL` is a customer with no card agent, not a line whose
    customer is missing (the customer join is an outer join)."""
    from app.models.order import SalesOrder

    w = _world(db)
    so = seed_so_line(db, cust=w.c3, prod=w.p, qty=1, total="999.00", agent=w.g1, wh=w.wh)
    db.query(SalesOrder).filter(SalesOrder.id == so.id).update({"customer_id": None})
    db.commit()
    body = _ok(client, _full(db), group_by="product", top_n=10, product_code="ZZTDD", customer_agent_ids=["none"])
    assert money(body["total"]["amount"]) == D("25.00"), body


def test_row_keys_stay_inside_the_contact_companies(client, db):
    """A same-code agent of a company the contact has no link to is never a key."""
    from app.models.sales_agent import SalesAgent
    from tests._mc_lookup_seed import MOCHA_ID, seed_mocha
    import uuid as _uuid

    w = _world(db)
    seed_mocha(db)
    db.add(SalesAgent(id=str(_uuid.uuid4()), sales_agent="ZZT-DG-1", company_id=MOCHA_ID))
    db.commit()
    body = _ok(client, _full(db), group_by="sales_agent", top_n=10, product_code="ZZTDD")
    assert _keys(body)["ZZT-DG-1"] == [w.g1.id], body["rows"]
