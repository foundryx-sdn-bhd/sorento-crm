"""Case/whitespace-insensitive master matching, shared by every path that
creates or adopts a warehouse, supplier, product category, unit of measure,
product or brand (D17), plus the supplier name-cleaning + ambiguity rule
(D2) `outstanding_import_service` and `po_history_service` each carried a
copy of.

`normalize_code` is a NEW function with the same `upper(btrim())` semantics
`sales_agent_service.normalize_code` already uses for agent codes - that one
is reused as-is (not moved: agents have their own module for a reason), this
one is for the other five masters. `resolve_master_by_code` is the single
`func.upper(func.btrim(...))` match every one of those five now goes through
on every channel (manual create, xlsx import, ESB masters push, the SO/PO
ladder's code rung).
"""
from __future__ import annotations

import re
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.inventory import Warehouse
from app.models.procurement import Supplier
from app.models.product import Brand, ItemType, Product, ProductCategory, UnitOfMeasure

#: The exact-match code column per master this module resolves through.
#: Customers are absent on purpose - identity there is the (code, name) pair,
#: not the code alone (D13, `customer_rules.customer_identity`). Sales agents
#: are absent too - shared (no company scope), matched via
#: `sales_agent_service`, which owns its own normalisation.
_CODE_COLUMNS: dict[type, str] = {
    Warehouse: "warehouse_code",
    Supplier: "supplier_code",
    ProductCategory: "category_code",
    UnitOfMeasure: "uom_code",
    Product: "product_code",
    Brand: "brand_code",
    ItemType: "item_type_code",
}

#: The matching name column, for `ensure_reference`'s "code = name = raw
#: value" auto-create (D3) - only ever used for the references a product
#: push may auto-create.
_NAME_COLUMNS: dict[type, str] = {
    ProductCategory: "category_name",
    UnitOfMeasure: "uom_name",
    Brand: "brand_name",
    ItemType: "item_type_name",
}


def normalize_code(code: Optional[str]) -> str:
    """The stored and compared form of a master code: trimmed and upper-cased.

    Same semantics as `sales_agent_service.normalize_code` - kept as a
    separate function because agents are matched through their own module for
    reasons that have nothing to do with the other five masters.
    """
    return (code or "").strip().upper()


def resolve_master_by_name(
    db: Session, model: type, name: Optional[str], company_id: Optional[str] = None
) -> Optional[str]:
    """A row matched by its NAME column, case/whitespace-insensitive, within
    the company - the second rung `ensure_reference`'s code-or-name match
    (D3, review S2) uses, for the references a product push may
    auto-create. `None` when `model` has no name column here (only
    `_NAME_COLUMNS`'s do) or nothing matches.
    """
    normalized = normalize_code(name)
    if not normalized:
        return None
    name_column = _NAME_COLUMNS.get(model)
    if name_column is None:
        return None
    column = getattr(model, name_column)
    query = db.query(model.id).filter(func.upper(func.btrim(column)) == normalized)
    if company_id is not None and hasattr(model, "company_id"):
        query = query.filter(model.company_id == company_id)
    row = query.first()
    return str(row[0]) if row else None


def resolve_master_by_code(
    db: Session, model: type, code: Optional[str], company_id: Optional[str] = None
) -> Optional[str]:
    """A row matched by business code, case/whitespace-insensitive, within
    the company (D17). `None` for a blank code or no match - never raises,
    since "not found" is a normal answer every caller here already handles
    (adopt vs create, or report retryable/unresolved).

    `company_id=None` (the manual-service callers, which run inside the
    request's ambient `company_scope` and have no anchor of their own to
    pass) applies no EXPLICIT filter and relies entirely on the ambient
    scope's own `do_orm_execute` filtering. The ESB masters ingest passes its
    anchor explicitly instead of relying on ambient scope, which a batch
    running two companies through one session never resets between calls.

    Fix round (Group 3, `PLAN-autocount-pull-preview-perf.md`): `ORDER BY
    created_at, id` before `.first()` - every model here (`Warehouse`,
    `Supplier`, `ProductCategory`, `UnitOfMeasure`, `Product`, `Brand`, `ItemType`) has
    `created_at`, so this is safe for every caller; it picks the OLDEST row
    deterministically for the rare case two rows normalize to the same code
    (the unique constraint is on the exact stored string, not the
    normalized one) - the same answer `MasterIngestService`'s own bulk code
    preload now picks for the identical shape.
    """
    normalized = normalize_code(code)
    if not normalized:
        return None
    column = getattr(model, _CODE_COLUMNS[model])
    query = db.query(model.id).filter(func.upper(func.btrim(column)) == normalized)
    if company_id is not None and hasattr(model, "company_id"):
        query = query.filter(model.company_id == company_id)
    row = query.order_by(model.created_at, model.id).first()
    return str(row[0]) if row else None


#: AutoCount appends the account's currency to a creditor name
#: (`ACME (RMB)` / `ACME (USD)` for the same company held under two currency
#: accounts). Moved from `outstanding_import_service._clean_supplier_name`
#: (unchanged body) - the upload imports this instead of keeping its own copy.
_CURRENCY_SUFFIX_RE = re.compile(r"[\(（]\s*[A-Za-z]{2,4}\s*[\)）]\s*$")


def clean_supplier_name(raw: Optional[str]) -> str:
    """The legal supplier name, with AutoCount's trailing currency note
    stripped - `"ACME (RMB)"` -> `"ACME"`. See `_CURRENCY_SUFFIX_RE`."""
    if not raw:
        return ""
    return _CURRENCY_SUFFIX_RE.sub("", str(raw).strip()).strip()


class AmbiguousMaster(Exception):
    """A cleaned name matches more than one existing row. Named for a future
    caller that wants the codes (`po_history_service`'s own refusal logs
    them); `resolve_supplier_by_name` itself does not raise it - it returns
    `None`, the same "refuse, do not guess" outcome its one caller already
    treats as "not found"."""

    def __init__(self, message: str, codes: list[str]):
        self.codes = codes
        super().__init__(message)


def resolve_supplier_by_name(db: Session, name: str, company_id: str) -> Optional[str]:
    """A supplier matched by its cleaned name, within the company (D2).

    Two suppliers can clean to the same name (the same company held twice,
    once per currency account) - `po_history_service._match_creditors`'
    refusal rule, generalised: an ambiguous name resolves to nothing rather
    than guessing which row a re-sync should update.
    """
    cleaned = clean_supplier_name(name).upper()
    if not cleaned:
        return None
    suppliers = (
        db.query(Supplier)
        .filter(Supplier.company_id == company_id)
        .order_by(Supplier.id.desc())
        .all()
    )
    matches = [s for s in suppliers if clean_supplier_name(s.supplier_name).upper() == cleaned]
    if len(matches) != 1:
        return None
    return str(matches[0].id)


def count_supplier_name_matches(db: Session, name: str, company_id: str) -> int:
    """How many suppliers share `name`'s cleaned form, within the company.

    `resolve_supplier_by_name` collapses "no match" and "more than one
    match" to the same `None` - correct for a caller that only wants an id,
    wrong for one that has to warn `supplier_ambiguous` on the second case
    and stay silent on the first (`MasterRefResolver._resolve_by_fallback`,
    S3 repoint).
    """
    cleaned = clean_supplier_name(name).upper()
    if not cleaned:
        return 0
    suppliers = db.query(Supplier).filter(Supplier.company_id == company_id).all()
    return sum(1 for s in suppliers if clean_supplier_name(s.supplier_name).upper() == cleaned)


def code_name_columns(model: type) -> tuple[str, str]:
    """The (code_column, name_column) pair for a model `ensure_reference`
    auto-creates. Raises `KeyError` for a model that has no name column here -
    deliberately, since only the product-reference masters ever get
    auto-created (D3); a caller reaching this for anything else is a bug."""
    return _CODE_COLUMNS[model], _NAME_COLUMNS[model]
