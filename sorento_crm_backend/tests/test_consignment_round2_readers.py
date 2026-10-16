"""AC-CONSIGN-CRM round 2, UAC-07: the report ask reads consignment lines on the outstanding
basis only (a consignment is demand, not a sale: ordered and delivered stay without it).

Postgres only, every row seeded here. Harness = tests/test_report_ask_route.py's.
"""
from __future__ import annotations

from decimal import Decimal

from app.models.order import DOC_TYPE_CONSIGNMENT

from tests._pg_fixture import unique_code
from tests._report_ask_so_seed import seed_so_line
from tests._sales_report_do_seed import client, db, seed_agent, seed_customer  # noqa: F401
from tests.test_report_ask_route import _full, _ok, _prod, _rows

D = Decimal


def _seed_pair(db, *, delivered):
    """One plain SO line (agent PLAIN) and one consignment line (agent CONS), side by side."""
    cust = seed_customer(db, name=unique_code("Cust"))
    prod = _prod(db, unique_code("ZZTCG"))
    plain, cons = seed_agent(db, code="ZZT-CG-PLAIN"), seed_agent(db, code="ZZT-CG-CONS")
    seed_so_line(db, cust=cust, prod=prod, agent=plain, qty=10, total="1000.00", delivered=delivered)
    so = seed_so_line(db, cust=cust, prod=prod, agent=cons, qty=4, total="400.00", delivered=delivered)
    so.doc_type = DOC_TYPE_CONSIGNMENT
    db.commit()
    return _full(db)


def _agents(body):
    return {name for name, _qty, _amt in _rows(body)}


def test_outstanding_basis_includes_an_open_consignment_line(client, db):
    contact = _seed_pair(db, delivered=0)
    body = _ok(client, contact, group_by="sales_agent", top_n=10, basis="outstanding")
    assert _agents(body) == {"ZZT-CG-PLAIN", "ZZT-CG-CONS"}, body["rows"]
    assert body["total"]["qty"] == 14, body["total"]


def test_ordered_basis_excludes_a_consignment_line(client, db):
    contact = _seed_pair(db, delivered=0)
    body = _ok(client, contact, group_by="sales_agent", top_n=10, basis="ordered")
    assert _agents(body) == {"ZZT-CG-PLAIN"}, body["rows"]


def test_delivered_basis_excludes_a_consignment_line(client, db):
    contact = _seed_pair(db, delivered=10)
    body = _ok(client, contact, group_by="sales_agent", top_n=10, basis="delivered")
    assert _agents(body) == {"ZZT-CG-PLAIN"}, body["rows"]
