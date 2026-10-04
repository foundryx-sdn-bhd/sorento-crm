"""A sales order's stock location is derived, never picked (owner hand test, PR #1264 note 3).

"each sales agent is assigned to a location group like BB then the stock location is
automatically BRW-BB". The chain the data already carries: the order's PO names an issuing
party, the party is a customer, the customer has a sales agent (`customers.sales_agent_id`),
and the agent has a location group (`sales_agents.location_group`). The site is the master
one, read off `settings.project_allocation_brw_warehouse_code` (`BRW-BB` -> `BRW`), because
every site runs a `-BB` bin and only Bukit Raja's is the master
(`project_allocation_service.py` module docstring).

Where the chain breaks, the order carries no location and says which link is missing, so the
screen can flag it instead of offering a picker.

Postgres only, via ``blank_session``; fixture builders shared with ``test_project_so_draft``.
"""
from __future__ import annotations

import uuid

import pytest

from app.models.inventory import Warehouse
from app.models.sales_agent import SalesAgent
from app.schemas.project_sales_order import ProjectSalesOrderDetail
from app.services.project_so_draft_service import ProjectSODraftService

from ._pg_fixture import blank_session
from .test_project_so_draft import (
    _customer,
    _minimal,
    _sorento,
    _user,
    project_seed_service,
)

MARKER = "zzt-so-location"


@pytest.fixture()
def seeded():
    with blank_session() as db:
        company_id = _sorento(db)
        project_seed_service.run(db, company_id=company_id)
        owner = _user(db, f"{MARKER} CONTACT AH")
        yield db, company_id, owner


def _agent(db, code: str, *, group: str | None) -> SalesAgent:
    row = SalesAgent(id=str(uuid.uuid4()), sales_agent=code, location_group=group)
    db.add(row)
    db.flush()
    return row


def _warehouse(db, code: str, *, active: bool = True) -> Warehouse:
    row = Warehouse(
        id=str(uuid.uuid4()), warehouse_code=code, warehouse_name=code, is_active=active,
    )
    db.add(row)
    db.flush()
    return row


def _detail(db, company_id, owner, *, customer=None) -> dict:
    _project, _product, po, schedule = _minimal(db, company_id, owner, customer=customer)
    service = ProjectSODraftService(db)
    built = service.build(po.id, schedule.id)
    order = service.get_order(built["data"][0]["id"])
    return service.serialize_detail(order)


def _customer_of(db, name: str, agent: SalesAgent | None):
    customer = _customer(db, name)
    customer.sales_agent_id = agent.id if agent else None
    db.flush()
    return customer


def test_the_agents_location_group_names_the_master_sites_bin(seeded):
    db, company_id, owner = seeded
    _warehouse(db, "BRW-BB")
    _warehouse(db, "MWH-BB")
    customer = _customer_of(db, "Buimaco", _agent(db, "ZZT CYNDI", group="BB"))

    body = _detail(db, company_id, owner, customer=customer)

    assert body["stock_location"] == "BRW-BB"
    assert body["stock_location_gap"] is None


def test_a_group_typed_in_lower_case_still_resolves(seeded):
    db, company_id, owner = seeded
    _warehouse(db, "BRW-IB")
    customer = _customer_of(db, "Buimaco", _agent(db, "ZZT SEAN", group=" ib "))

    body = _detail(db, company_id, owner, customer=customer)

    assert body["stock_location"] == "BRW-IB"


def test_an_agent_with_no_location_group_is_named_as_the_gap(seeded):
    db, company_id, owner = seeded
    _warehouse(db, "BRW-BB")
    customer = _customer_of(db, "Buimaco", _agent(db, "ZZT LCL", group=None))

    body = _detail(db, company_id, owner, customer=customer)

    assert body["stock_location"] is None
    assert body["stock_location_gap"] == "Sales agent ZZT LCL has no location group."


def test_a_customer_with_no_sales_agent_is_named_as_the_gap(seeded):
    db, company_id, owner = seeded
    customer = _customer_of(db, "Buimaco", None)

    body = _detail(db, company_id, owner, customer=customer)

    assert body["stock_location"] is None
    assert body["stock_location_gap"] == f"{customer.customer_name} has no sales agent."


def test_a_po_with_no_customer_is_named_as_the_gap(seeded):
    db, company_id, owner = seeded

    body = _detail(db, company_id, owner, customer=None)

    assert body["stock_location"] is None
    assert body["stock_location_gap"] == "The purchase order names no customer."


def test_a_group_with_no_active_bin_at_the_master_site_is_named_as_the_gap(seeded):
    db, company_id, owner = seeded
    _warehouse(db, "BRW-ZQ", active=False)
    customer = _customer_of(db, "Buimaco", _agent(db, "ZZT NEW", group="ZQ"))

    body = _detail(db, company_id, owner, customer=customer)

    assert body["stock_location"] is None
    assert body["stock_location_gap"] == "No active stock location BRW-ZQ."


def test_the_response_model_keeps_both_fields(seeded):
    """`response_model` silently drops an undeclared field (LESSONS-LEARNT)."""
    db, company_id, owner = seeded
    _warehouse(db, "BRW-BB")
    customer = _customer_of(db, "Buimaco", _agent(db, "ZZT CYNDI", group="BB"))

    body = _detail(db, company_id, owner, customer=customer)
    served = ProjectSalesOrderDetail.model_validate(body).model_dump()

    assert served["stock_location"] == "BRW-BB"
    assert served["stock_location_gap"] is None
