"""Phase 2 RED tests - the delivery_order_lines dataset resolves the sales agent from the DO's OWN
`orders.agent` code first, the linked sales order second (lane REPORT-ENGINE, PR #1447).

Today `delivery_order_lines._base` reaches the agent only through `Order.sales_order_id ->
SalesOrder.sales_agent_id`, but that link is set only by the AutoCount doc push; tracking-upload
DOs never carry it, so every agent ranking says "(no agent)". Every DO carries its own agent code
in `orders.agent`.

Contract pinned here, per DO:
1. `orders.agent` non-blank: the `sales_agents` row whose `sales_agent` equals upper(trim(agent))
   and whose company_id is the DO's company OR NULL (shared); company row beats shared row. The
   DO's own agent wins over the linked SO's agent.
2. else the linked SO's `sales_agent_id`.
Dimension shows the master code; an unmatched non-blank `orders.agent` shows the trimmed raw text;
nothing at all shows "(no agent)". The filter matches the RESOLVED agent id. No line fan-out.

Fake data only. Postgres only (`tests/_pg_fixture.py`), every row seeded here.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from app.models.sales_agent import SalesAgent
from app.services.company_scope import DEFAULT_COMPANY_ID

from tests._pg_fixture import unique_code
from tests._sales_report_do_seed import (  # noqa: F401  (client / db are fixtures)
    _as_contact,
    _contact,
    client,
    db,
    line,
    money,
    product,
    seed_agent,
    seed_customer,
    seed_do,
    seed_so,
    warehouse,
)

D = Decimal
ROUTE = "/api/v1/order-management/report-ask"
KEY = {"X-API-Key": "k"}
SEP = date(2026, 9, 10)
PERIOD = {"date_from": "2026-09-01", "date_to": "2026-09-30"}


def _world(db):
    cust = seed_customer(db, name="CUSTOMER A")
    prod = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("SKU"))
    wh = warehouse(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("WH"))
    return cust, prod, wh


def _do(db, world, *, agent, qty, total, so=None):
    """One AutoCount DO of one line. `agent` is written to `orders.agent` verbatim (None, '' or text)."""
    cust, prod, wh = world
    row = seed_do(db, customer_id=cust.id, order_date=SEP, source_book="db1",
                  sales_order_id=so.id if so is not None else None,
                  lines=[line(prod.id, wh.id, qty, price=D("1"), total=D(str(total)))])
    row.agent = agent
    db.flush()
    return row


def _shared_agent(db, code):
    row = SalesAgent(id=str(uuid.uuid4()), sales_agent=code, company_id=None)
    db.add(row)
    db.flush()
    return row


def _ask(client, db, **params):
    contact = _contact(db)  # grant holder with no customer link: the full audience
    db.commit()
    q ={**PERIOD, **_as_contact(contact), **params}
    resp = client.get(ROUTE, params=q, headers=KEY)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _rows(body):
    return [(r["name"], r["qty"], money(r["amount"])) for r in body["rows"]]


def _by_agent(client, db, **extra):
    return _ask(client, db, group_by="sales_agent", top_n=10, **extra)


# ------------------------------------------------------------------------ 1: unlinked DO


def test_1_unlinked_do_resolves_its_own_agent_code(client, db):
    world = _world(db)
    seed_agent(db, code="AGENT A")
    _do(db, world, agent="AGENT A", qty=7, total="70.00")
    db.commit()
    body = _by_agent(client, db)
    assert _rows(body) == [("AGENT A", 7, D("70.00"))], body["rows"]
    assert "(no agent)" not in [r["name"] for r in body["rows"]], body["rows"]


# ------------------------------------------------------------------------ 2: trim and case


def test_2_a_padded_lower_case_code_resolves_to_the_upper_case_master_row(client, db):
    world = _world(db)
    master = seed_agent(db, code="AGENT A")
    _do(db, world, agent=" agent a ", qty=3, total="30.00")
    db.commit()
    body = _by_agent(client, db)
    assert _rows(body) == [("AGENT A", 3, D("30.00"))], body["rows"]
    # resolved to the master row, so the id filter finds it too
    filtered = _by_agent(client, db, sales_agent_ids=[master.id])
    assert _rows(filtered) == [("AGENT A", 3, D("30.00"))], filtered["rows"]


# ------------------------------------------------------------------------ 3: SO fallback


def test_3_blank_or_null_own_agent_falls_back_to_the_linked_sales_order(client, db):
    """Regression guard: the current (SO only) behaviour must survive for DOs with no own agent."""
    world = _world(db)
    cust = world[0]
    agent_b = seed_agent(db, code="AGENT B")
    so = seed_so(db, customer_id=cust.id, agent_id=agent_b.id)
    _do(db, world, agent=None, qty=2, total="20.00", so=so)
    _do(db, world, agent="", qty=5, total="50.00", so=so)
    db.commit()
    body = _by_agent(client, db)
    assert _rows(body) == [("AGENT B", 7, D("70.00"))], body["rows"]


# ------------------------------------------------------------------------ 4: DO wins over SO


def test_4_the_dos_own_agent_wins_over_the_linked_sales_orders_agent(client, db):
    world = _world(db)
    cust = world[0]
    seed_agent(db, code="AGENT A")
    agent_b = seed_agent(db, code="AGENT B")
    so = seed_so(db, customer_id=cust.id, agent_id=agent_b.id)
    _do(db, world, agent="AGENT A", qty=4, total="40.00", so=so)
    db.commit()
    body = _by_agent(client, db)
    assert _rows(body) == [("AGENT A", 4, D("40.00"))], body["rows"]


# ------------------------------------------------------------------------ 5: unmatched code


def test_5_an_unmatched_own_agent_shows_the_trimmed_raw_text(client, db):
    world = _world(db)
    _do(db, world, agent="  AGENT Z ", qty=6, total="60.00")
    db.commit()
    body = _by_agent(client, db)
    assert _rows(body) == [("AGENT Z", 6, D("60.00"))], body["rows"]


# ------------------------------------------------------------------------ 6: nothing at all


def test_6_no_agent_anywhere_is_no_agent(client, db):
    """Regression guard: passes on current code."""
    world = _world(db)
    _do(db, world, agent=None, qty=1, total="10.00")
    _do(db, world, agent="   ", qty=2, total="20.00")
    db.commit()
    body = _by_agent(client, db)
    assert _rows(body) == [("(no agent)", 3, D("30.00"))], body["rows"]


# ------------------------------------------------------------------------ 7: the filter


def test_7_the_agent_filter_matches_the_resolved_agent_of_an_unlinked_do(client, db):
    world = _world(db)
    a = seed_agent(db, code="AGENT A")
    seed_agent(db, code="AGENT B")
    _do(db, world, agent="AGENT A", qty=7, total="70.00")
    _do(db, world, agent="AGENT B", qty=9, total="90.00")
    db.commit()
    body = _by_agent(client, db, sales_agent_ids=[a.id])
    assert _rows(body) == [("AGENT A", 7, D("70.00"))], body["rows"]
    assert body["total"]["qty"] == 7 and money(body["total"]["amount"]) == D("70.00"), body["total"]


# ------------------------------------------------------------------------ 8: shared + company rows


def test_8_a_shared_and_a_company_row_with_one_code_count_a_line_once_and_the_company_row_wins(client, db):
    world = _world(db)
    shared = _shared_agent(db, "AGENT A")
    company = seed_agent(db, code="AGENT A")  # company_id = DEFAULT_COMPANY_ID
    _do(db, world, agent="agent a", qty=7, total="70.00")
    db.commit()

    body = _by_agent(client, db)
    assert _rows(body) == [("AGENT A", 7, D("70.00"))], body["rows"]  # once, not 14 / 140.00
    assert body["total"]["qty"] == 7 and money(body["total"]["amount"]) == D("70.00"), body["total"]

    via_company = _by_agent(client, db, sales_agent_ids=[company.id])
    assert _rows(via_company) == [("AGENT A", 7, D("70.00"))], via_company["rows"]
    assert shared.id != company.id


def test_8b_a_shared_only_row_resolves_for_a_company_do(client, db):
    world = _world(db)
    shared = _shared_agent(db, "AGENT A")
    _do(db, world, agent="AGENT A", qty=2, total="20.00")
    db.commit()
    body = _by_agent(client, db, sales_agent_ids=[shared.id])
    assert _rows(body) == [("AGENT A", 2, D("20.00"))], body["rows"]


# ------------------------------------------------------------------------ 9: the route


def test_9_route_ranking_over_unlinked_dos_returns_the_agent_codes(client, db):
    world = _world(db)
    seed_agent(db, code="AGENT A")
    seed_agent(db, code="AGENT B")
    _do(db, world, agent="AGENT A", qty=10, total="100.00")
    _do(db, world, agent="agent b", qty=3, total="300.00")
    _do(db, world, agent="AGENT A", qty=1, total="5.00")
    db.commit()
    body = _by_agent(client, db)
    assert body["status"] == "ok" and body["group_by"] == "sales_agent", body
    assert _rows(body) == [("AGENT B", 3, D("300.00")), ("AGENT A", 11, D("105.00"))], body["rows"]
    assert body["total_count"] == 2, body
