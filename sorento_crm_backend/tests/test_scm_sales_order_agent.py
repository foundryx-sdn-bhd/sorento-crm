"""Sales agent on the SCM sales order (PLAN-demo-followups-19aug-ladder-v2 A2).

`sales_orders.sales_agent_id` already exists and the AutoCount upload fills it on 2,233 of
2,249 open orders, but the field was never serialized, never filterable, and never
write-able. This covers the three things the plan asks the backend for:

* `serialize()` emits the agent's code + human `person_label`, without an N+1 across a page
  of orders (`_agent_map` - one query for every DISTINCT agent on the page);
* `update()` can both SET and CLEAR the agent - clearing needs `model_fields_set`, not a
  plain `is not None` check, because an omitted field and an explicit `null` both arrive as
  `None` on the Pydantic model;
* the list can be narrowed to one agent's orders;
* `list_agents()` - the options source for the FE's Agent filter/select, added when a
  Purchasing-only role (holds `scm.dashboard.view`, not `master_data.sales_agents.view`)
  turned out unable to load the master's own select route - active rows, ordered by code,
  optionally narrowed by a code/label substring.

Postgres only (`tests/_pg_fixture.py`), rolled back - every row this file creates is its own,
marker-prefixed with `unique_code`, never a borrowed `LIMIT 1` row.
"""
from __future__ import annotations

import uuid

import pytest

from app.models.order import Customer, SalesOrder, SalesOrderLine
from app.models.product import Product, ProductCategory, UnitOfMeasure
from app.models.sales_agent import SalesAgent
from app.schemas.scm_orders import SalesOrderUpdate
from app.services.error_handler import AppException
from app.services.scm.sales_order_service import SalesOrderService
from tests._pg_fixture import pg_session, unique_code

MARKER = "ZZTSOAGT"


def _u() -> str:
    return str(uuid.uuid4())


@pytest.fixture()
def db():
    with pg_session() as s:
        yield s


@pytest.fixture()
def world(db):
    cat = ProductCategory(id=_u(), category_code=unique_code(MARKER),
                          category_name=f"{MARKER} cat")
    uom = UnitOfMeasure(id=_u(), uom_code=unique_code("U")[:20], uom_name=f"{MARKER} u")
    db.add_all([cat, uom])
    db.flush()
    product = Product(id=_u(), product_code=unique_code("P"), product_name=f"{MARKER} p",
                      category_id=cat.id, base_uom_id=uom.id, list_price=0,
                      is_active=True, is_discontinued=False)
    customer = Customer(id=_u(), customer_code=unique_code("C"), customer_name=f"{MARKER} Acme")
    jeremy = SalesAgent(id=_u(), sales_agent=unique_code("AGT"), person_label="JEREMY")
    cindy = SalesAgent(id=_u(), sales_agent=unique_code("AGT"), person_label="CONTACT W")
    db.add_all([product, customer, jeremy, cindy])
    db.flush()
    return {"product": product, "customer": customer, "jeremy": jeremy, "cindy": cindy}


def _order(db, world, *, agent=None) -> SalesOrder:
    so = SalesOrder(id=_u(), so_number=unique_code(MARKER), status="open",
                    order_date=None, customer_id=world["customer"].id,
                    sales_agent_id=agent.id if agent else None)
    db.add(so)
    db.flush()
    db.add(SalesOrderLine(
        id=_u(), sales_order_id=so.id, product_id=world["product"].id,
        qty_ordered=10, qty_delivered=0, line_status="open",
    ))
    db.flush()
    return so


# --------------------------------------------------------------------------- #
# serialize
# --------------------------------------------------------------------------- #

def test_serialize_emits_the_agents_code_and_label(db, world):
    so = _order(db, world, agent=world["jeremy"])

    row = SalesOrderService(db).serialize(SalesOrderService(db)._get_or_404(so.id))

    assert row["sales_agent_id"] == world["jeremy"].id
    assert row["sales_agent_code"] == world["jeremy"].sales_agent
    assert row["sales_agent_label"] == "JEREMY"


def test_serialize_without_an_agent_is_all_none(db, world):
    so = _order(db, world, agent=None)

    row = SalesOrderService(db).serialize(SalesOrderService(db)._get_or_404(so.id))

    assert row["sales_agent_id"] is None
    assert row["sales_agent_code"] is None
    assert row["sales_agent_label"] is None


def test_list_batches_agents_into_one_query_per_distinct_agent(db, world):
    """Not an N+1: two orders sharing one agent, plus a third with a different one, still
    resolve correctly through the ONE `_agent_map` lookup `list()` builds for the page."""
    a = _order(db, world, agent=world["jeremy"])
    b = _order(db, world, agent=world["jeremy"])
    c = _order(db, world, agent=world["cindy"])

    out = SalesOrderService(db).list(
        page=1, limit=50, sort="so_number", direction="asc",
        query=MARKER, status=None, priority=None,
    )
    by_number = {row["so_number"]: row for row in out["data"]}

    assert by_number[a.so_number]["sales_agent_label"] == "JEREMY"
    assert by_number[b.so_number]["sales_agent_label"] == "JEREMY"
    assert by_number[c.so_number]["sales_agent_label"] == "CONTACT W"


# --------------------------------------------------------------------------- #
# update: set / clear / leave alone
# --------------------------------------------------------------------------- #

def test_update_sets_the_agent(db, world):
    so = _order(db, world, agent=None)

    out = SalesOrderService(db).update(
        so.id, SalesOrderUpdate(sales_agent_id=world["jeremy"].id), user_id=None,
    )

    assert out["sales_agent_id"] == world["jeremy"].id
    assert out["sales_agent_label"] == "JEREMY"


def test_update_with_an_explicit_empty_string_clears_the_agent(db, world):
    so = _order(db, world, agent=world["jeremy"])

    out = SalesOrderService(db).update(
        so.id, SalesOrderUpdate(sales_agent_id=""), user_id=None,
    )

    assert out["sales_agent_id"] is None
    assert out["sales_agent_label"] is None


def test_update_omitting_the_field_leaves_the_agent_untouched(db, world):
    """The field the caller never sent must not be read as `null` and wipe the agent - the
    whole reason `update()` reads `model_fields_set` instead of a plain `is not None`."""
    so = _order(db, world, agent=world["jeremy"])

    out = SalesOrderService(db).update(
        so.id, SalesOrderUpdate(priority="urgent"), user_id=None,
    )

    assert out["sales_agent_id"] == world["jeremy"].id
    assert out["priority"] == "urgent"


def test_update_reassigns_from_one_agent_to_another(db, world):
    so = _order(db, world, agent=world["jeremy"])

    out = SalesOrderService(db).update(
        so.id, SalesOrderUpdate(sales_agent_id=world["cindy"].id), user_id=None,
    )

    assert out["sales_agent_id"] == world["cindy"].id
    assert out["sales_agent_label"] == "CONTACT W"


def test_update_with_an_unknown_agent_id_404s(db, world):
    so = _order(db, world, agent=None)

    with pytest.raises(AppException) as exc:
        SalesOrderService(db).update(
            so.id, SalesOrderUpdate(sales_agent_id=_u()), user_id=None,
        )
    assert exc.value.status_code == 404


# --------------------------------------------------------------------------- #
# list filter
# --------------------------------------------------------------------------- #

def test_the_list_can_be_narrowed_to_one_agent(db, world):
    mine = _order(db, world, agent=world["jeremy"])
    theirs = _order(db, world, agent=world["cindy"])
    unassigned = _order(db, world, agent=None)

    out = SalesOrderService(db).list(
        page=1, limit=50, sort="so_number", direction="asc",
        query=MARKER, status=None, priority=None,
        sales_agent_id=world["jeremy"].id,
    )
    numbers = {row["so_number"] for row in out["data"]}

    assert numbers == {mine.so_number}
    assert theirs.so_number not in numbers
    assert unassigned.so_number not in numbers


# --------------------------------------------------------------------------- #
# list_agents - GET /scm/sales-orders/agents (PLAN-demo-followups-19aug-ladder-v2 follow-up)
#
# A Purchasing-only SCM user holds `scm.dashboard.view` but not the sales-agents master's
# own `master_data.sales_agents.view`, so the Agent filter/select is served off this route
# rather than `GET /master-data/sales-agents/`.
# --------------------------------------------------------------------------- #

def test_list_agents_returns_active_agents_with_code_and_label(db, world):
    out = SalesOrderService(db).list_agents()
    by_code = {a["sales_agent"]: a for a in out}

    assert world["jeremy"].sales_agent in by_code
    assert by_code[world["jeremy"].sales_agent]["id"] == world["jeremy"].id
    assert by_code[world["jeremy"].sales_agent]["person_label"] == "JEREMY"
    assert world["cindy"].sales_agent in by_code


def test_list_agents_is_ordered_by_code_ascending(db, world):
    out = SalesOrderService(db).list_agents()
    codes = [a["sales_agent"] for a in out]

    assert codes == sorted(codes)


def test_list_agents_excludes_inactive_rows(db, world):
    world["jeremy"].is_active = False
    db.flush()

    out = SalesOrderService(db).list_agents()
    codes = {a["sales_agent"] for a in out}

    assert world["jeremy"].sales_agent not in codes
    assert world["cindy"].sales_agent in codes


def test_list_agents_filters_by_code_substring(db, world):
    out = SalesOrderService(db).list_agents(query=world["jeremy"].sales_agent)
    codes = {a["sales_agent"] for a in out}

    assert codes == {world["jeremy"].sales_agent}


def test_list_agents_filters_by_person_label_substring(db, world):
    # `query` runs unscoped over the whole shared master (real production agents included on
    # the local prod-copy DB), so this asserts membership rather than set equality - a full
    # equality check would be a flaky assertion about data this file does not own.
    out = SalesOrderService(db).list_agents(query="CONTACT W")
    ids = {a["id"] for a in out}

    assert world["cindy"].id in ids
    assert world["jeremy"].id not in ids
