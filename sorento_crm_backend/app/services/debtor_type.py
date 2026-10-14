"""Customer debtor type (FORM-ROUTING-CONDITIONS, DT).

The default rule and the one place the PR Sales type auto-fill is decided. The migration
`frc_0001_debtor_type_no_cs` re-states `default_debtor_type` in SQL; a test pins both to the
same answers.
"""
from __future__ import annotations

import re
from typing import Optional

DEBTOR_TYPES = ("project", "project_cash", "cash_sales")
DEBTOR_TYPE_LABELS = {"project": "Project", "project_cash": "Project cash", "cash_sales": "Cash sales"}
# Owner Q5 (14 Oct 2026). The ONE place the PR sales-type auto-fill is decided.
SALES_TYPE_FOR_DEBTOR_TYPE = {"project": "project", "project_cash": "cash_sales", "cash_sales": "cash_sales"}

_CASH_WORD = re.compile(r"^\s*CASH\b", re.IGNORECASE)


def default_debtor_type(customer_code: Optional[str], customer_name: Optional[str]) -> Optional[str]:
    """302- -> project_cash; 301- whose name starts with the word CASH -> None; else project."""
    code = (customer_code or "").strip()
    if code.startswith("302-"):
        return "project_cash"
    if code.startswith("301-") and _CASH_WORD.match(customer_name or ""):
        return None
    return "project"


def lookup_customers(db, q: str = "", limit: int = 20) -> list[dict]:
    """Active customers whose name or code contains `q`, for the PR / SF customer pickers.

    `sales_type` is the Q5 mapping of the customer's debtor type (None when unset): the
    forms never carry their own copy of that mapping.
    """
    from sqlalchemy import or_

    from app.models.order import Customer

    query = db.query(Customer).filter(Customer.is_active.is_(True))
    qs = (q or "").strip()
    if qs:
        like = f"%{qs}%"
        query = query.filter(or_(Customer.customer_name.ilike(like), Customer.customer_code.ilike(like)))
    rows = query.order_by(Customer.customer_name, Customer.customer_code).limit(limit).all()
    return [
        {
            "customer_id": str(r.id),
            "debtor_name": r.customer_name,
            "customer_code": r.customer_code,
            "debtor_type": r.debtor_type,
            "sales_type": SALES_TYPE_FOR_DEBTOR_TYPE.get(r.debtor_type) if r.debtor_type else None,
        }
        for r in rows
    ]


def link_customer(db, raw, current_id=None):
    """Resolve a form's customer link: (customer_id, customer_name), or (None, None) to unlink.

    "" / None unlink. The customer must be visible in the caller's company scope, so an
    unknown id and another company's id get the SAME 422. An inactive customer is refused
    only when the link is new or changed: re-saving a form already linked to a since
    deactivated customer stays fine. The returned name is the customer's own, which the
    caller stores as customer_name so the printed name and the routed customer cannot differ.
    """
    from uuid import UUID

    from app.models.order import Customer
    from app.services.error_handler import AppException

    if raw is None or str(raw).strip() == "":
        return None, None
    refuse = AppException(status_code=422, message="Pick a customer from the list.")
    try:
        cid = str(UUID(str(raw).strip()))
    except (TypeError, ValueError):
        raise refuse
    row = db.query(Customer).filter(Customer.id == cid).first()
    if row is None:
        raise refuse
    if not row.is_active and cid != (str(current_id) if current_id else None):
        raise refuse
    return cid, row.customer_name
