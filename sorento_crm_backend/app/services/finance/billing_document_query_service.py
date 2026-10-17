"""Read side of the billing documents: the list, the record, a customer's AR total and the
list-query export (AC-BILLING S2, S3, S5; card CARD-ac-billing-17oct).

Read only. The one writer is the ingest (`billing_document_ingest_service`) and the
outstanding push (`ar_outstanding_ingest_service`). Every query is company scoped by the
session's scope, the way every other company-scoped read is; the record and the customer
lookups filter by a query (not `Session.get`) so that scope applies even to a row already in
the session's identity map.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any, Iterable, Optional

from sqlalchemy import case, exists, func, or_
from sqlalchemy.orm import Query, Session

from app.models.finance import CANCELLED, BillingDocument, BillingDocumentLine
from app.models.order import Customer, SalesOrder, SalesOrderLine
from app.models.product import Product
from app.models.sales_agent import SalesAgent
from app.services.error_handler import AppException
from app.services.uuid_path_param import validate_uuid_path

#: Gates every outstanding figure on this surface. The owner chose no separate slug (card Q2);
#: switching outstanding to a slug of its own is a change to this line only.
BILLING_VIEW_SLUG = "finance.billing_documents.view"
OUTSTANDING_VIEW_SLUG = BILLING_VIEW_SLUG

DEFAULT_SORT = "doc_date"

_SORT_COLUMNS = {
    "doc_date": BillingDocument.doc_date,
    "doc_no": BillingDocument.doc_no,
    "document_type": BillingDocument.document_type,
    "customer_name": BillingDocument.customer_name,
    "debtor_code": BillingDocument.debtor_code,
    "agent_code": BillingDocument.agent_code,
    "currency_code": BillingDocument.currency_code,
    "net_total": BillingDocument.net_total,
    "tax_total": BillingDocument.tax_total,
    "total": BillingDocument.total,
    "status": BillingDocument.status,
    # A cancelled document shows no outstanding (UAC S5-7), so it sorts with the blanks.
    "outstanding": case((BillingDocument.status == CANCELLED, None), else_=BillingDocument.outstanding),
    "due_date": case((BillingDocument.status == CANCELLED, None), else_=BillingDocument.due_date),
}


def _canonical_uuid(value: Any) -> Optional[str]:
    """The hyphenated form, or None. `uuid.UUID` also takes `urn:uuid:`, braces and no hyphens,
    which Postgres refuses, so the canonical string is what reaches SQL, never the raw input."""
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError):
        return None


def _num(value: Optional[Decimal]) -> Optional[float]:
    return None if value is None else float(value)


def _iso(value: Any) -> Optional[str]:
    return None if value is None else value.isoformat()


def _split(values: Optional[Iterable[str]]) -> list[str]:
    """Repeated `x=a&x=b` and comma separated `x=a,b` both, blanks dropped."""
    out: list[str] = []
    for value in values or []:
        out.extend(part.strip() for part in str(value).split(",") if part.strip())
    return out


# --------------------------------------------------------------------------------- list
def _filtered(
    db: Session,
    *,
    query: Optional[str] = None,
    document_types: Optional[Iterable[str]] = None,
    status: Optional[str] = None,
    customer_id: Optional[str] = None,
    agent_code: Optional[str] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    open_only: bool = False,
    sales_order_id: Optional[str] = None,
) -> Query:
    q = db.query(BillingDocument)
    if query and query.strip():
        like = f"%{query.strip()}%"
        q = q.filter(
            or_(
                BillingDocument.doc_no.ilike(like),
                BillingDocument.customer_name.ilike(like),
                BillingDocument.debtor_code.ilike(like),
            )
        )
    types = _split(document_types)
    if types:
        q = q.filter(BillingDocument.document_type.in_(types))
    if status:
        q = q.filter(BillingDocument.status == status)
    if customer_id:
        q = q.filter(BillingDocument.customer_id == customer_id)
    if agent_code:
        q = q.filter(BillingDocument.agent_code == agent_code)
    if date_from:
        q = q.filter(BillingDocument.doc_date >= date_from)
    if date_to:
        q = q.filter(BillingDocument.doc_date <= date_to)
    if open_only:
        # Still owed: a synced, non-zero balance. An unapplied credit note is negative and
        # counts; NULL (never synced) and 0 (paid) do not.
        q = q.filter(
            BillingDocument.status != "cancelled",
            BillingDocument.outstanding.isnot(None),
            BillingDocument.outstanding != 0,
        )
    if sales_order_id:
        # A correlated EXISTS, never a join: a document with two lines on the order is one row.
        q = q.filter(
            exists().where(
                BillingDocumentLine.document_id == BillingDocument.id,
                BillingDocumentLine.sales_order_line_id == SalesOrderLine.id,
                SalesOrderLine.sales_order_id == sales_order_id,
            )
        )
    return q


def list_documents(
    db: Session,
    *,
    page: int = 1,
    limit: int = 50,
    sort: str = DEFAULT_SORT,
    dir: str = "desc",
    **filters: Any,
) -> tuple[list[BillingDocument], int]:
    q = _filtered(db, **filters)
    total = q.count()
    column = _SORT_COLUMNS.get(sort, _SORT_COLUMNS[DEFAULT_SORT])
    order = column.desc().nulls_last() if dir == "desc" else column.asc().nulls_last()
    # The id tiebreak keeps a page boundary stable when many documents share a date.
    rows = q.order_by(order, BillingDocument.id).offset((page - 1) * limit).limit(limit).all()
    return rows, total


def _agent_names(db: Session, agent_ids: set[str]) -> dict[str, str]:
    if not agent_ids:
        return {}
    rows = db.query(SalesAgent.id, SalesAgent.description).filter(SalesAgent.id.in_(agent_ids))
    return {str(row[0]): (row[1] or "").strip() for row in rows}


def _outstanding_fields(doc: BillingDocument) -> dict[str, Any]:
    """A cancelled document is owed nothing, whatever balance was last synced onto it."""
    if doc.status == CANCELLED:
        return {"outstanding": None, "due_date": None, "outstanding_as_of": None}
    return {
        "outstanding": _num(doc.outstanding),
        "due_date": _iso(doc.due_date),
        "outstanding_as_of": _iso(doc.outstanding_as_of),
    }


def _customer_names(db: Session, docs: list[BillingDocument]) -> dict[str, str]:
    """The linked customer's name for documents whose own snapshot is blank, in one query."""
    ids = {d.customer_id for d in docs if d.customer_id and not d.customer_name}
    if not ids:
        return {}
    rows = db.query(Customer.id, Customer.customer_name).filter(Customer.id.in_(ids))
    return {str(cid): name for cid, name in rows if name}


def _row(
    doc: BillingDocument,
    agent_names: dict[str, str],
    customer_names: Optional[dict[str, str]] = None,
) -> dict[str, Any]:
    name = agent_names.get(str(doc.sales_agent_id)) if doc.sales_agent_id else None
    customer_name = doc.customer_name or (customer_names or {}).get(str(doc.customer_id))
    return {
        "id": str(doc.id),
        "document_type": doc.document_type,
        "doc_no": doc.doc_no,
        "doc_date": _iso(doc.doc_date),
        "customer_id": str(doc.customer_id) if doc.customer_id else None,
        "customer_name": customer_name,
        "debtor_code": doc.debtor_code,
        "agent_code": doc.agent_code,
        "agent_name": name or doc.agent_code,
        "currency_code": doc.currency_code,
        "net_total": _num(doc.net_total),
        "tax_total": _num(doc.tax_total),
        "total": _num(doc.total),
        "status": doc.status,
        **_outstanding_fields(doc),
    }


def serialize_list(db: Session, docs: list[BillingDocument]) -> list[dict[str, Any]]:
    names = _agent_names(db, {str(d.sales_agent_id) for d in docs if d.sales_agent_id})
    customer_names = _customer_names(db, docs)
    return [_row(doc, names, customer_names) for doc in docs]


# ------------------------------------------------------------------------------- record
def get_document_or_404(db: Session, document_id: str) -> BillingDocument:
    validate_uuid_path(document_id, resource="Billing document")
    doc = db.query(BillingDocument).filter(BillingDocument.id == document_id).first()
    if doc is None:
        raise AppException(status_code=404, message="Billing document not found.", code="NOT_FOUND")
    return doc


def document_detail(db: Session, doc: BillingDocument) -> dict[str, Any]:
    lines = list(doc.lines)  # ordered by line_no
    line_ids = [line.sales_order_line_id for line in lines if line.sales_order_line_id]
    orders_by_line: dict[str, dict[str, str]] = {}
    if line_ids:
        for line_id, order_id, so_number in (
            db.query(SalesOrderLine.id, SalesOrder.id, SalesOrder.so_number)
            .join(SalesOrder, SalesOrder.id == SalesOrderLine.sales_order_id)
            .filter(SalesOrderLine.id.in_(line_ids))
        ):
            orders_by_line[str(line_id)] = {"id": str(order_id), "doc_no": so_number}
    product_ids = {line.product_id for line in lines if line.product_id}
    product_names = {}
    if product_ids:
        product_names = {
            str(pid): name
            for pid, name in db.query(Product.id, Product.product_name).filter(
                Product.id.in_(product_ids)
            )
        }

    notes = (
        db.query(BillingDocument)
        .filter(BillingDocument.against_document_id == doc.id, BillingDocument.id != doc.id)
        .order_by(BillingDocument.doc_date, BillingDocument.doc_no)
        .all()
    )
    sales_orders = sorted(
        {o["id"]: o for o in orders_by_line.values()}.values(), key=lambda o: o["doc_no"]
    )
    delivery_order_nos = sorted(
        {
            line.from_doc_no
            for line in lines
            if line.from_doc_type == "DO" and line.from_doc_no
        }
    )
    against = None
    if doc.against_document_id:
        target = (
            db.query(BillingDocument.id, BillingDocument.doc_no)
            .filter(BillingDocument.id == doc.against_document_id)
            .first()
        )
        if target is not None:
            against = {"id": str(target[0]), "doc_no": target[1]}
    if against is None and doc.against_doc_no:
        against = {"id": None, "doc_no": doc.against_doc_no}

    body = _row(
        doc,
        _agent_names(db, {str(doc.sales_agent_id)} if doc.sales_agent_id else set()),
        _customer_names(db, [doc]),
    )
    body.update(
        {
            "currency_rate": _num(doc.currency_rate),
            "local_net_total": _num(doc.local_net_total),
            "ref": doc.ref,
            "description": doc.description,
            "last_synced_at": _iso(doc.last_synced_at),
            "source_modified_at": _iso(doc.source_modified_at),
            "against": against,
            "lines": [
                {
                    "line_no": line.line_no,
                    "item_code": line.item_code,
                    "product_name": product_names.get(str(line.product_id))
                    if line.product_id
                    else None,
                    "description": line.description,
                    "uom": line.uom,
                    "quantity": _num(line.quantity),
                    "unit_price": _num(line.unit_price),
                    "discount_amount": _num(line.discount_amount),
                    "net_amount": _num(line.net_amount),
                    "tax_code": line.tax_code,
                    "tax_rate": _num(line.tax_rate),
                    "tax_amount": _num(line.tax_amount),
                    "line_total": _num(line.line_total),
                    "from_doc_type": line.from_doc_type,
                    "from_doc_no": line.from_doc_no,
                    "sales_order": orders_by_line.get(str(line.sales_order_line_id))
                    if line.sales_order_line_id
                    else None,
                }
                for line in lines
            ],
            "related": {
                "notes": [
                    {
                        "id": str(note.id),
                        "document_type": note.document_type,
                        "doc_no": note.doc_no,
                        "doc_date": _iso(note.doc_date),
                        "currency_code": note.currency_code,
                        "total": _num(note.total),
                        "outstanding": _outstanding_fields(note)["outstanding"],
                    }
                    for note in notes
                ],
                "sales_orders": sales_orders,
                "delivery_order_nos": delivery_order_nos,
            },
        }
    )
    return body


# ------------------------------------------------------------------ customer, options
def customer_outstanding(db: Session, customer_id: str) -> dict[str, Any]:
    validate_uuid_path(customer_id, resource="Customer")
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if customer is None:
        raise AppException(status_code=404, message="Customer not found.", code="NOT_FOUND")
    return {
        "customer_id": str(customer.id),
        "ar_outstanding": _num(customer.ar_outstanding),
        "ar_as_of": _iso(customer.ar_as_of),
    }


def agent_options(db: Session) -> list[dict[str, str]]:
    """The agent codes the company's documents carry, labelled with the agent's name when the
    code resolves to one."""
    rows = (
        db.query(BillingDocument.agent_code, func.max(SalesAgent.description))
        .outerjoin(SalesAgent, SalesAgent.id == BillingDocument.sales_agent_id)
        .filter(BillingDocument.agent_code.isnot(None), BillingDocument.agent_code != "")
        .group_by(BillingDocument.agent_code)
        .order_by(BillingDocument.agent_code)
        .all()
    )
    return [
        {"value": code, "label": f"{code} - {name.strip()}" if name and name.strip() else code}
        for code, name in rows
    ]


# ----------------------------------------------------------------------------- export
def export_rows(
    db: Session,
    field_keys: list[str],
    *,
    quick_search: Optional[str] = None,
    record_ids: Optional[list[str]] = None,
    customer_id: Optional[str] = None,
    document_type: Optional[str] = None,
    status: Optional[str] = None,
    agent_code: Optional[str] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    open_only: bool = False,
) -> list[dict[str, Any]]:
    """The list's filtered set as flat rows, newest first, one per document. The fields are
    the list row's own keys (the grid's column ids), so there is no metadata to seed."""
    unknown = [key for key in field_keys if key not in _EXPORT_FIELDS]
    if unknown:
        raise ValueError(f"Unknown or non-exportable field: {unknown[0]}")
    if customer_id is not None:
        customer_id = _canonical_uuid(customer_id)
        if customer_id is None:
            raise ValueError("Invalid customer_id")
    if record_ids:
        record_ids = [_canonical_uuid(v) for v in record_ids]
        if None in record_ids:
            raise ValueError("Invalid record_ids")
    q = _filtered(
        db,
        query=quick_search,
        document_types=[document_type] if document_type else None,
        status=status,
        customer_id=customer_id,
        agent_code=agent_code,
        date_from=date_from,
        date_to=date_to,
        open_only=open_only,
    )
    if record_ids:
        q = q.filter(BillingDocument.id.in_(record_ids))
    docs = q.order_by(BillingDocument.doc_date.desc(), BillingDocument.doc_no, BillingDocument.id).all()
    return [{key: row[key] for key in field_keys} for row in serialize_list(db, docs)]


_EXPORT_FIELDS = (
    "document_type",
    "doc_no",
    "doc_date",
    "customer_name",
    "debtor_code",
    "agent_code",
    "agent_name",
    "currency_code",
    "net_total",
    "tax_total",
    "total",
    "status",
    "outstanding",
    "due_date",
    "outstanding_as_of",
)
