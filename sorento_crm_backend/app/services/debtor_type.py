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
