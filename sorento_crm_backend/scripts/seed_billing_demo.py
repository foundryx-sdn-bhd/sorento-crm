#!/usr/bin/env python3
"""Seed illustrative billing documents and outstanding for ONE customer, for a hand test (AC-BILLING).

Why: before AutoCount grants the billing tables (after 25 Oct) no billing document or AR figure
exists anywhere, so the Finance pages, the customer's outstanding and the SO's Invoices tab have
nothing to show on a test copy. This pushes made-up documents through the real ingest services
(`billing_documents`, contract 2.6; `ar_outstanding`, 2.10), exactly as the shared service will.

What, for the customer named by `--customer-code`, built on its latest sales order with refs:
  - IV `DEMO-IV-1` from that SO's lines (each line linked to its SO line), partly paid;
  - IV `DEMO-IV-2`, one line, paid in full (outstanding 0);
  - CN `DEMO-CN-1` against DEMO-IV-1, unapplied (negative outstanding);
  - DN `DEMO-DN-1`, one charge line with no product, fully open;
  - CS `DEMO-CS-1`, never covered by the AR push (outstanding stays blank);
  - the customer's total outstanding and as-of, as AutoCount would state it.
Every source ref starts `DEMO_ACB:`, so `--remove` finds exactly these rows. Amounts are made up.

Writes only to the database in the env it runs with. Never run it against production.

Run from sorento_crm_backend/ with the test copy's env:
    venv/bin/python scripts/seed_billing_demo.py --customer-code <CODE>           # seed
    venv/bin/python scripts/seed_billing_demo.py --customer-code <CODE> --remove  # clean up
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.main import app  # noqa: E402,F401  (resolves the guards import cycle, as the tests do)
from sqlalchemy import text  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models.base import company_scope  # noqa: E402
from app.models.company import Company  # noqa: E402
from app.models.finance import BillingDocument  # noqa: E402
from app.models.integration_reference import IntegrationReference  # noqa: E402
from app.models.order import Customer, SalesOrder, SalesOrderLine  # noqa: E402
from app.models.product import Product  # noqa: E402
from app.models.sales_agent import SalesAgent  # noqa: E402
from app.services.finance.ar_outstanding_ingest_service import (  # noqa: E402
    AR_OUTSTANDING_ENTITY,
    ArOutstandingIngestService,
)
from app.services.finance.billing_document_ingest_service import (  # noqa: E402
    BILLING_DOCUMENTS_ENTITY,
    BillingDocumentIngestService,
)

PREFIX = "DEMO_ACB"
TAX_RATE = Decimal("8")
CENT = Decimal("0.01")


def _m(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(CENT, rounding=ROUND_HALF_UP)


def _line(ref: str, n: int, *, code, description, qty, price, from_ref=None, so_no=None) -> dict:
    net = _m(Decimal(str(qty)) * _m(price))
    tax = _m(net * TAX_RATE / 100)
    line = {
        "source_ref": f"{ref}:{n}", "line_number": n, "product_code": code,
        "description": description, "uom": "UNIT", "quantity": float(qty),
        "unit_price": float(_m(price)), "discount_amount": 0, "net_amount": float(net),
        "tax_code": "SV-8" if code else None, "tax_rate": float(TAX_RATE), "tax_amount": float(tax),
        "line_total": float(net + tax),
    }
    if from_ref:
        line.update({"from_doc_type": "SO", "from_doc_no": so_no, "from_line_ref": from_ref})
    return line


def _doc(source_ref, kind, doc_no, doc_date, customer, agent_code, lines, **extra) -> dict:
    net = sum((_m(x["net_amount"]) for x in lines), Decimal("0"))
    tax = sum((_m(x["tax_amount"]) for x in lines), Decimal("0"))
    return {
        "source_ref": source_ref, "document_type": kind, "doc_no": doc_no,
        "doc_date": doc_date.isoformat(), "status": "posted",
        "source_modified_at": datetime.utcnow().replace(microsecond=0).isoformat(),
        "customer_code": customer.customer_code, "customer_name": customer.customer_name,
        "agent_code": agent_code, "currency_code": "MYR", "currency_rate": 1,
        "net_total": float(net), "tax_total": float(tax), "total": float(net + tax),
        "local_net_total": float(net), "lines": lines, **extra,
    }


def _customer_company(db, code: str) -> tuple[str, str]:
    """(customer id, company id) by plain SQL: the ORM is company-scoped and no scope is set yet."""
    rows = db.execute(
        text("SELECT id, company_id FROM customers WHERE customer_code = :code"), {"code": code}
    ).all()
    if len(rows) != 1:
        sys.exit(f"customer code {code!r} matches {len(rows)} customers; pick a unique one")
    return str(rows[0][0]), str(rows[0][1])


def seed(db, customer: Customer) -> None:
    order = (
        db.query(SalesOrder)
        .join(SalesOrderLine, SalesOrderLine.sales_order_id == SalesOrder.id)
        .filter(SalesOrder.customer_id == customer.id, SalesOrderLine.source_ref.isnot(None))
        .order_by(SalesOrder.order_date.desc().nullslast())
        .first()
    )
    if order is None:
        sys.exit("this customer has no sales order with AutoCount line refs; pick another")
    so_lines = (
        db.query(SalesOrderLine, Product)
        .join(Product, Product.id == SalesOrderLine.product_id)
        .filter(SalesOrderLine.sales_order_id == order.id, SalesOrderLine.source_ref.isnot(None))
        .order_by(SalesOrderLine.line_no.nullslast())
        .limit(5)
        .all()
    )
    agent = db.get(SalesAgent, order.sales_agent_id) if order.sales_agent_id else None
    agent_code = agent.sales_agent if agent else None
    today = date.today()

    iv1 = f"{PREFIX}:IV:1"
    iv1_lines = [
        _line(iv1, i, code=p.product_code, description=p.product_name,
              qty=max(Decimal("1"), Decimal(str(sl.qty_ordered or 1))),
              price=sl.unit_price or 100, from_ref=sl.source_ref, so_no=order.so_number)
        for i, (sl, p) in enumerate(so_lines, start=1)
    ]
    first_code, first_name = so_lines[0][1].product_code, so_lines[0][1].product_name
    iv2 = f"{PREFIX}:IV:2"
    cn1 = f"{PREFIX}:CN:1"
    dn1 = f"{PREFIX}:DN:1"
    cs1 = f"{PREFIX}:CS:1"
    docs = [
        _doc(iv1, "invoice", "DEMO-IV-1", today - timedelta(days=20), customer, agent_code, iv1_lines,
             ref=order.so_number, description="Illustrative invoice from a sales order"),
        _doc(iv2, "invoice", "DEMO-IV-2", today - timedelta(days=45), customer, agent_code,
             [_line(iv2, 1, code=first_code, description=first_name, qty=2, price=150)]),
        _doc(cn1, "credit_note", "DEMO-CN-1", today - timedelta(days=10), customer, agent_code,
             [_line(cn1, 1, code=first_code, description=first_name, qty=1, price=100)],
             against_doc_no="DEMO-IV-1", against_source_ref=iv1),
        _doc(dn1, "debit_note", "DEMO-DN-1", today - timedelta(days=5), customer, agent_code,
             [_line(dn1, 1, code=None, description="Late payment charge", qty=1, price=50)],
             against_doc_no="DEMO-IV-1"),
        _doc(cs1, "cash_sale", "DEMO-CS-1", today - timedelta(days=3), customer, agent_code,
             [_line(cs1, 1, code=first_code, description=first_name, qty=1, price=120)]),
    ]
    company_id = customer.company_id
    result = BillingDocumentIngestService(db, None, company_id=company_id).ingest(
        BILLING_DOCUMENTS_ENTITY, docs
    )
    for rec in result.records:
        print(f"billing  {rec.source_ref}: {rec.outcome.value} {rec.errors or ''}")
    db.commit()

    iv1_total = Decimal(str(docs[0]["total"]))
    iv1_open = _m(iv1_total / 2)
    cn_open = -Decimal(str(docs[2]["total"]))
    dn_open = Decimal(str(docs[3]["total"]))
    snapshot = {
        "source_ref": f"{PREFIX}:debtor:{customer.customer_code}",
        "customer_code": customer.customer_code,
        "as_of": datetime.utcnow().replace(microsecond=0).isoformat(),
        "total_outstanding": float(iv1_open + cn_open + dn_open),
        "documents": [
            {"source_ref": iv1, "outstanding": float(iv1_open),
             "due_date": (today + timedelta(days=10)).isoformat()},
            {"source_ref": iv2, "outstanding": 0,
             "due_date": (today - timedelta(days=15)).isoformat()},
            {"source_ref": cn1, "outstanding": float(cn_open), "due_date": None},
            {"source_ref": dn1, "outstanding": float(dn_open),
             "due_date": (today - timedelta(days=2)).isoformat()},
        ],
    }
    result = ArOutstandingIngestService(db, None, company_id=company_id).ingest(
        AR_OUTSTANDING_ENTITY, [snapshot]
    )
    for rec in result.records:
        print(f"outstanding {rec.source_ref}: {rec.outcome.value} {rec.errors or ''} {rec.warnings or ''}")
    db.commit()
    if any(rec.errors for rec in result.records):
        sys.exit("the outstanding push failed; see above")
    print(f"customer {customer.customer_code}: total outstanding {snapshot['total_outstanding']:.2f}; "
          f"invoice DEMO-IV-1 is linked to sales order {order.so_number}")


def remove(db, customer: Customer) -> None:
    docs = db.query(BillingDocument).filter(
        BillingDocument.company_id == customer.company_id,
        BillingDocument.source_ref.like(f"{PREFIX}:%"),
    ).all()
    for doc in docs:
        db.delete(doc)
    refs = db.query(IntegrationReference).filter(
        IntegrationReference.company_id == customer.company_id,
        IntegrationReference.source_ref.like(f"{PREFIX}:%"),
    ).all()
    for ref in refs:
        db.delete(ref)
    customer.ar_outstanding = None
    customer.ar_as_of = None
    db.commit()
    print(f"removed {len(docs)} demo documents and {len(refs)} refs; "
          f"{customer.customer_code} outstanding back to not synced")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--customer-code", required=True)
    parser.add_argument("--remove", action="store_true")
    args = parser.parse_args()
    db = SessionLocal()
    try:
        customer_id, company_id = _customer_company(db, args.customer_code)
        with company_scope(db, frozenset({company_id})):
            customer = db.query(Customer).filter(Customer.id == customer_id).one()
            company = db.query(Company).filter(Company.id == company_id).first()
            print(f"company {company.code if company else company_id}")
            (remove if args.remove else seed)(db, customer)
    finally:
        db.close()


if __name__ == "__main__":
    main()
