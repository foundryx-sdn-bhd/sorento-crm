"""New Product Application service (NEW-PRODUCT-APP): every rule, for staff and portal.

The staff router (`api/v1/procurement/new_product_applications.py`) and the portal router
(`api/v1/public/portal_new_product_application.py`) both call this module; neither decides
anything itself. Who may do what is decided here from an `Actor`:

- owner = the portal contact that raised it, or the staff user who is its applicant;
- `can_view_all`: `.view`, `.approve` or `.open_code` (the people who act on others' rows);
- `can_approve`: approve / reject a Submitted one, and revise one;
- `can_open_code`: link a line to a product, close it, undo either.

Approval, lock and Revise copy the quotation approval of #1698 (card rule 5): Submitted and
Approved lock every write (`assert_unlocked`), Revise asks a reason and returns to Draft,
and a Rejected application is edited and sent with the same Submit. Notifications go out
AFTER the route commits (`notify`), best effort, and never undo a move.

Card: documentation/plans/procurement/CARD-new-product-application-17oct.md.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import exists, func, or_
from sqlalchemy.orm import Session

from app.models.new_product_application import NewProductApplication, NewProductApplicationLine
from app.models.order import Customer
from app.models.procurement import StockInquiry, Supplier
from app.models.product import Brand, ItemType, Product, ProductCategory, UnitOfMeasure
from app.models.user import User
from app.services.error_handler import AppException, handle_not_found

logger = logging.getLogger(__name__)

ENTITY = "new_product_application"

VIEW = "procurement.new_product_applications.view"
CREATE = "procurement.new_product_applications.create"
APPROVE = "procurement.new_product_applications.approve"
OPEN_CODE = "procurement.new_product_applications.open_code"

DRAFT = "draft"
SUBMITTED = "submitted"
APPROVED = "approved"
REJECTED = "rejected"
COMPLETED = "completed"
CANCELLED = "cancelled"

STATUS_LABELS = {
    DRAFT: "Draft",
    SUBMITTED: "Submitted",
    APPROVED: "Approved",
    REJECTED: "Rejected",
    COMPLETED: "Completed",
    CANCELLED: "Cancelled",
}

#: Statuses the applicant may edit in. Submitted and Approved are LOCKED (422), the
#: terminal ones are closed (409).
EDITABLE = (DRAFT, REJECTED)
LOCKED = (SUBMITTED, APPROVED)

#: Owner switch (crew 18 Oct, PQ1 with the owner): the portal form carries no Supplier and
#: no Cost price, and the portal lists no suppliers. Flip to True if the owner picks (a).
PORTAL_SUPPLIER_AND_COST = False

HEADER_TEXT = ("customer_name", "project_name", "remark")
LINE_TEXT = ("proposed_code", "description", "further_description", "supplier_item_code", "remark")
LINE_REFS = {
    "category_id": ProductCategory,
    "base_uom_id": UnitOfMeasure,
    "brand_id": Brand,
    "item_type_id": ItemType,
    "supplier_id": Supplier,
}
LINE_NUMBERS = ("cost_price", "selling_price", "expected_qty")
PORTAL_HIDDEN_LINE_FIELDS = ("supplier_id", "cost_price")


@dataclass
class Actor:
    user_id: Optional[str] = None
    contact_id: Optional[str] = None
    can_view_all: bool = False
    can_create: bool = False
    can_approve: bool = False
    can_open_code: bool = False


def actor_from_slugs(user_id: str, slugs: Iterable[str]) -> Actor:
    s = set(slugs)
    return Actor(
        user_id=user_id,
        can_view_all=bool(s & {VIEW, APPROVE, OPEN_CODE}),
        can_create=CREATE in s,
        can_approve=APPROVE in s,
        can_open_code=OPEN_CODE in s,
    )


# ------------------------------------------------------------------ small helpers


def _clean(value: Any) -> Any:
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def _is_uuid(value: Any) -> bool:
    return bool(
        isinstance(value, str)
        and re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", value)
    )


def _forbidden(message: str) -> AppException:
    return AppException(status_code=403, message=message, code="FORBIDDEN")


def _conflict(message: str, code: str) -> AppException:
    return AppException(status_code=409, message=message, code=code)


def _invalid(field: str, message: str) -> AppException:
    return AppException(
        status_code=422, message=message, code="VALIDATION_ERROR", details={"field": field}
    )


def is_owner(app: NewProductApplication, actor: Actor) -> bool:
    if actor.contact_id and app.contact_id and str(app.contact_id) == str(actor.contact_id):
        return True
    return bool(actor.user_id and app.applicant_id and str(app.applicant_id) == str(actor.user_id))


def assert_can_view(app: NewProductApplication, actor: Actor) -> None:
    if actor.contact_id is None and actor.can_view_all:
        return
    if not is_owner(app, actor):
        raise _forbidden("You do not have access to this application.")


def assert_unlocked(app: NewProductApplication) -> None:
    """Card rule 5 (the #1698 lock): no header or line write while Submitted or Approved."""
    if app.status in LOCKED:
        raise AppException(
            status_code=422,
            message="This application is locked while it is submitted or approved. Revise it to change it.",
            code="new_product_application_locked",
        )
    if app.status not in EDITABLE:
        raise _conflict(
            f"A {STATUS_LABELS[app.status].lower()} application cannot be changed.",
            "NEW_PRODUCT_APPLICATION_CLOSED",
        )


def _parse_qty(raw: Optional[str]) -> Optional[str]:
    text = (raw or "").strip()
    if not re.fullmatch(r"\d+(\.\d+)?", text):
        return None
    value = Decimal(text)
    return format(value.normalize(), "f") if value > 0 else None


def _parse_date(raw: Optional[str]) -> Optional[str]:
    try:
        return date.fromisoformat((raw or "").strip()).isoformat()
    except ValueError:
        return None


def _resolved(line: NewProductApplicationLine) -> bool:
    return line.opened_product_id is not None or line.closed_reason is not None


# ------------------------------------------------------------------ references


def _ref_exists(db: Session, model, ref_id: Any) -> bool:
    # A query, not db.get: the company filter applies to queries, so another company's row
    # reads as missing.
    return _is_uuid(ref_id) and db.query(model.id).filter(model.id == ref_id).first() is not None


def _check_refs(db: Session, data: Dict[str, Any], *, portal: bool) -> None:
    inquiry_id = data.get("stock_inquiry_id")
    if inquiry_id is not None and not _ref_exists(db, StockInquiry, inquiry_id):
        raise _invalid("stock_inquiry_id", "That stock inquiry does not exist.")
    customer_id = data.get("customer_id")
    if customer_id is not None and not _ref_exists(db, Customer, customer_id):
        raise _invalid("customer_id", "That customer is not in the debtor list.")
    for index, line in enumerate(data.get("lines") or [], start=1):
        if portal and not PORTAL_SUPPLIER_AND_COST:
            for field in PORTAL_HIDDEN_LINE_FIELDS:
                if line.get(field) is not None:
                    raise _invalid(field, f"Line {index}: {field} is not set from the portal.")
        for field, model in LINE_REFS.items():
            value = line.get(field)
            if value is not None and not _ref_exists(db, model, value):
                raise _invalid(field, f"Line {index}: the chosen {field} does not exist.")


def _apply(db: Session, app: NewProductApplication, data: Dict[str, Any], *, portal: bool) -> None:
    _check_refs(db, data, portal=portal)
    for key in ("stock_inquiry_id", "customer_id", "needed_by"):
        if key in data:
            setattr(app, key, data[key] or None)
    for key in HEADER_TEXT:
        if key in data:
            setattr(app, key, _clean(data[key]))
    if "lines" in data:
        app.lines.clear()
        db.flush()
        for index, line in enumerate(data["lines"] or [], start=1):
            row = NewProductApplicationLine(line_no=index)
            for key in LINE_TEXT:
                setattr(row, key, _clean(line.get(key)))
            for key in LINE_REFS:
                setattr(row, key, line.get(key) or None)
            for key in LINE_NUMBERS:
                setattr(row, key, line.get(key))
            app.lines.append(row)


# ------------------------------------------------------------------ create / edit / delete


def create_draft(
    db: Session,
    *,
    applicant_id: Optional[str],
    data: Dict[str, Any],
    source: str = "staff",
    contact_id: Optional[str] = None,
    space_id: Optional[str] = None,
) -> NewProductApplication:
    app = NewProductApplication(
        status=DRAFT,
        source=source,
        applicant_id=applicant_id,
        contact_id=contact_id,
        space_id=space_id,
    )
    db.add(app)
    _apply(db, app, data, portal=source == "portal")
    db.flush()
    return app


def update(db: Session, app: NewProductApplication, *, actor: Actor, data: Dict[str, Any]) -> None:
    if not is_owner(app, actor):
        raise _forbidden("Only the applicant can edit this application.")
    assert_unlocked(app)
    _apply(db, app, data, portal=app.source == "portal")
    db.flush()


def get_for(db: Session, application_id: str, *, contact_id: Optional[str] = None) -> NewProductApplication:
    q = db.query(NewProductApplication).filter(NewProductApplication.id == application_id)
    if contact_id is not None:
        q = q.filter(NewProductApplication.contact_id == str(contact_id))
    app = q.first() if _is_uuid(application_id) else None
    if app is None:
        raise handle_not_found("New product application", application_id)
    return app


def assert_deletable(db: Session, application_id: str, user_id: Optional[str]) -> NewProductApplication:
    """Delete draft: the owner's own Draft, nothing else (403 not yours, 409 not a draft)."""
    app = get_for(db, application_id)
    if not is_owner(app, Actor(user_id=user_id)):
        raise _forbidden("Only the applicant can delete this application.")
    if app.status != DRAFT:
        raise _conflict(
            "Only a draft can be deleted. Cancel a submitted application instead.",
            "NEW_PRODUCT_APPLICATION_NOT_DRAFT",
        )
    return app


def delete_draft(db: Session, application_id: str, user_id: Optional[str]) -> None:
    """Hard delete (the lines go with it). Runs when the pending-action window lapses."""
    db.delete(assert_deletable(db, application_id, user_id))
    db.commit()


# ------------------------------------------------------------------ moves


def validate_submit(app: NewProductApplication) -> None:
    if not app.lines:
        raise _invalid("lines", "Add at least one line before you submit.")
    for line in app.lines:
        if not line.description:
            raise _invalid("description", f"Line {line.line_no}: enter the description.")
        if not line.category_id:
            raise _invalid("category_id", f"Line {line.line_no}: choose an item group.")
        if not line.base_uom_id:
            raise _invalid("base_uom_id", f"Line {line.line_no}: choose the base UOM.")


def submit(db: Session, app: NewProductApplication, *, actor: Actor) -> str:
    """Draft -> Submitted, or Rejected -> (Draft ->) Submitted in one press (#1698 C1)."""
    if not is_owner(app, actor):
        raise _forbidden("Only the applicant can submit this application.")
    if app.status not in EDITABLE:
        raise _conflict(
            f"A {STATUS_LABELS[app.status].lower()} application cannot be submitted.",
            "NEW_PRODUCT_APPLICATION_ILLEGAL_MOVE",
        )
    validate_submit(app)
    app.status = SUBMITTED
    app.submitted_at = datetime.utcnow()
    app.decided_at = None
    app.decided_by = None
    db.flush()
    return "submitted"


def _require(app: NewProductApplication, status: str, verb: str) -> None:
    if app.status != status:
        raise _conflict(
            f"Only a {STATUS_LABELS[status].lower()} application can be {verb}.",
            "NEW_PRODUCT_APPLICATION_ILLEGAL_MOVE",
        )


def approve(db: Session, app: NewProductApplication, *, actor: Actor) -> str:
    if not actor.can_approve:
        raise _forbidden("You cannot approve new product applications.")
    _require(app, SUBMITTED, "approved")
    app.status = APPROVED
    app.decided_at = datetime.utcnow()
    app.decided_by = actor.user_id
    app.reject_reason = None
    db.flush()
    return "approved"


def reject(db: Session, app: NewProductApplication, *, actor: Actor, reason: Optional[str]) -> str:
    if not actor.can_approve:
        raise _forbidden("You cannot reject new product applications.")
    _require(app, SUBMITTED, "rejected")
    reason = _clean(reason)
    if not reason:
        raise _invalid("reason", "Enter the reason for rejecting.")
    app.status = REJECTED
    app.decided_at = datetime.utcnow()
    app.decided_by = actor.user_id
    app.reject_reason = reason
    db.flush()
    return "rejected"


def _assert_nothing_resolved(app: NewProductApplication, verb: str) -> None:
    if any(_resolved(line) for line in app.lines):
        raise _conflict(
            f"A code has already been opened or closed on this application, so it cannot be {verb}.",
            "NEW_PRODUCT_APPLICATION_IN_PROGRESS",
        )


def revise(db: Session, app: NewProductApplication, *, actor: Actor, reason: Optional[str]) -> Optional[str]:
    """Submitted / Approved -> Draft with a reason. Returns "withdrawn" from Submitted so the
    approvers hear the request is gone (#1698 D3-D5); from Approved nobody is told."""
    if not (is_owner(app, actor) or actor.can_approve):
        raise _forbidden("Only the applicant or an approver can revise this application.")
    if app.status not in LOCKED:
        raise _conflict(
            "Only a submitted or approved application can be revised.",
            "NEW_PRODUCT_APPLICATION_NOT_REVISABLE",
        )
    _assert_nothing_resolved(app, "revised")
    reason = _clean(reason)
    if not reason:
        raise _invalid("reason", "Say what changed, and why.")
    was = app.status
    app.status = DRAFT
    app.revise_reason = reason
    app.decided_at = None
    app.decided_by = None
    app.reject_reason = None
    db.flush()
    return "withdrawn" if was == SUBMITTED else None


def cancel(db: Session, app: NewProductApplication, *, actor: Actor, reason: Optional[str]) -> None:
    if not is_owner(app, actor):
        raise _forbidden("Only the applicant can cancel this application.")
    if app.status not in (DRAFT, SUBMITTED, APPROVED, REJECTED):
        raise _conflict(
            f"A {STATUS_LABELS[app.status].lower()} application cannot be cancelled.",
            "NEW_PRODUCT_APPLICATION_ILLEGAL_MOVE",
        )
    _assert_nothing_resolved(app, "cancelled")
    app.status = CANCELLED
    app.cancelled_at = datetime.utcnow()
    app.cancel_reason = _clean(reason)
    db.flush()


# ------------------------------------------------------------------ purchasing opens the code


def _line(app: NewProductApplication, line_id: str) -> NewProductApplicationLine:
    for line in app.lines:
        if str(line.id) == str(line_id):
            return line
    raise handle_not_found("Line", line_id)


def _open_line(app: NewProductApplication, line_id: str, actor: Actor) -> NewProductApplicationLine:
    if not actor.can_open_code:
        raise _forbidden("Only purchasing can open codes for new product applications.")
    line = _line(app, line_id)
    _require(app, APPROVED, "worked by purchasing")
    if _resolved(line):
        raise _conflict("This line is already done. Undo it first.", "NEW_PRODUCT_APPLICATION_LINE_DONE")
    return line


def _maybe_complete(app: NewProductApplication) -> Optional[str]:
    if app.lines and all(_resolved(line) for line in app.lines):
        app.status = COMPLETED
        app.completed_at = datetime.utcnow()
        return "completed"
    return None


def link_line(
    db: Session, app: NewProductApplication, line_id: str, *, actor: Actor, product_id: Optional[str]
) -> Optional[str]:
    line = _open_line(app, line_id, actor)
    product = (
        db.query(Product)
        .filter(Product.id == product_id, Product.is_active.is_(True))
        .first()
        if _is_uuid(product_id)
        else None
    )
    if product is None:
        raise _invalid("product_id", "Pick an active product from the product list.")
    line.opened_product_id = product.id
    line.resolved_by = actor.user_id
    line.resolved_at = datetime.utcnow()
    event = _maybe_complete(app)
    db.flush()
    return event


def close_line(
    db: Session, app: NewProductApplication, line_id: str, *, actor: Actor, reason: Optional[str]
) -> Optional[str]:
    line = _open_line(app, line_id, actor)
    reason = _clean(reason)
    if not reason:
        raise _invalid("reason", "Enter why this code is not being opened.")
    line.closed_reason = reason
    line.resolved_by = actor.user_id
    line.resolved_at = datetime.utcnow()
    event = _maybe_complete(app)
    db.flush()
    return event


def undo_line(db: Session, app: NewProductApplication, line_id: str, *, actor: Actor) -> None:
    if not actor.can_open_code:
        raise _forbidden("Only purchasing can undo a line.")
    line = _line(app, line_id)
    if app.status not in (APPROVED, COMPLETED) or not _resolved(line):
        raise _conflict("There is nothing to undo on this line.", "NEW_PRODUCT_APPLICATION_ILLEGAL_MOVE")
    line.opened_product_id = None
    line.closed_reason = None
    line.resolved_by = None
    line.resolved_at = None
    if app.status == COMPLETED:
        app.status = APPROVED
        app.completed_at = None
    db.flush()


# ------------------------------------------------------------------ prefill


def prefill(db: Session, stock_inquiry_id: str, *, contact_id: Optional[str] = None,
            space_id: Optional[str] = None) -> Dict[str, Any]:
    """A New form body copied as TEXT from the inquiry (card rule 1). Nothing is matched:
    the customer stays typed text, the code field stays one line however many codes it
    holds. A portal contact may only start from its OWN responded inquiry."""
    q = db.query(StockInquiry).filter(StockInquiry.id == stock_inquiry_id)
    if contact_id is not None:
        q = q.filter(StockInquiry.contact_id == str(contact_id), StockInquiry.status == "responded")
        if space_id is not None:
            q = q.filter(StockInquiry.space_id == str(space_id))
    inquiry = q.first() if _is_uuid(stock_inquiry_id) else None
    if inquiry is None:
        raise handle_not_found("Stock inquiry", stock_inquiry_id)
    return {
        "stock_inquiry_id": str(inquiry.id),
        "stock_inquiry_number": inquiry.inquiry_number,
        "customer_id": None,
        "customer_name": _clean(inquiry.project_customer),
        "project_name": _clean(inquiry.project_name),
        "needed_by": _parse_date(inquiry.delivery_date),
        "remark": None,
        "lines": [
            {
                "proposed_code": _clean(inquiry.product_code),
                "description": _clean(inquiry.item_description),
                "expected_qty": _parse_qty(inquiry.quantity),
            }
        ],
    }


def assert_portal_inquiry_allowed(
    db: Session, *, contact_id: str, space_id: Optional[str], stock_inquiry_id: Optional[str]
) -> None:
    """A portal application may only link the contact's own inquiry."""
    if stock_inquiry_id is None:
        return
    q = db.query(StockInquiry.id).filter(
        StockInquiry.id == stock_inquiry_id, StockInquiry.contact_id == str(contact_id)
    )
    if space_id is not None:
        q = q.filter(StockInquiry.space_id == str(space_id))
    if not _is_uuid(stock_inquiry_id) or q.first() is None:
        raise _invalid("stock_inquiry_id", "Pick one of your own stock inquiries.")


# ------------------------------------------------------------------ lookups


def _folded(column):
    return func.upper(func.regexp_replace(column, "[^A-Za-z0-9]", "", "g"))


def search_products(db: Session, q: str) -> List[Dict[str, Any]]:
    from app.services import sample_request_service

    return sample_request_service.search_products(db, q)


def item_types(db: Session, q: str = "", limit: int = 50) -> List[Dict[str, Any]]:
    query = db.query(ItemType).filter(ItemType.is_active.is_(True))
    text = (q or "").strip()
    if text:
        query = query.filter(
            or_(ItemType.item_type_name.ilike(f"%{text}%"), ItemType.item_type_code.ilike(f"%{text}%"))
        )
    return [
        {"id": r.id, "item_type_code": r.item_type_code, "item_type_name": r.item_type_name}
        for r in query.order_by(ItemType.item_type_name).limit(limit).all()
    ]


def _simple_lookup(db: Session, model, code_col, name_col, q: str, limit: int = 50) -> List[Dict[str, Any]]:
    query = db.query(model)
    if hasattr(model, "is_active"):
        query = query.filter(model.is_active.is_(True))
    text = (q or "").strip()
    if text:
        query = query.filter(or_(code_col.ilike(f"%{text}%"), name_col.ilike(f"%{text}%")))
    return [
        {"id": r.id, "code": getattr(r, code_col.key), "name": getattr(r, name_col.key)}
        for r in query.order_by(name_col).limit(limit).all()
    ]


def portal_lookup(db: Session, kind: str, q: str = "") -> List[Dict[str, Any]]:
    """The read-only selects the portal form needs (PQ1 (b): no suppliers unless the switch)."""
    if kind == "item-groups":
        return _simple_lookup(db, ProductCategory, ProductCategory.category_code, ProductCategory.category_name, q)
    if kind == "uoms":
        return _simple_lookup(db, UnitOfMeasure, UnitOfMeasure.uom_code, UnitOfMeasure.uom_name, q)
    if kind == "brands":
        return _simple_lookup(db, Brand, Brand.brand_code, Brand.brand_name, q)
    if kind == "item-types":
        return _simple_lookup(db, ItemType, ItemType.item_type_code, ItemType.item_type_name, q)
    if kind == "suppliers" and PORTAL_SUPPLIER_AND_COST:
        return _simple_lookup(db, Supplier, Supplier.supplier_code, Supplier.supplier_name, q)
    raise handle_not_found("Lookup", kind)


# ------------------------------------------------------------------ reads


def _visible(query, actor: Actor):
    if actor.contact_id is not None:
        return query.filter(NewProductApplication.contact_id == str(actor.contact_id))
    if actor.can_view_all:
        return query
    return query.filter(NewProductApplication.applicant_id == str(actor.user_id))


_SORTS = {
    "application_no": NewProductApplication.application_no,
    "status": NewProductApplication.status,
    "created_at": NewProductApplication.created_at,
    "submitted_at": NewProductApplication.submitted_at,
    "needed_by": NewProductApplication.needed_by,
    "customer_name": NewProductApplication.customer_name,
}


def list_for(
    db: Session,
    *,
    actor: Actor,
    statuses: Optional[List[str]] = None,
    q: Optional[str] = None,
    stock_inquiry_id: Optional[str] = None,
    applicant_id: Optional[str] = None,
    page: int = 1,
    limit: int = 25,
    sort: Optional[str] = None,
    direction: str = "desc",
) -> Dict[str, Any]:
    query = _visible(db.query(NewProductApplication), actor)
    if statuses:
        query = query.filter(NewProductApplication.status.in_(statuses))
    if stock_inquiry_id:
        if not _is_uuid(stock_inquiry_id):
            return {"items": [], "total": 0, "page": page, "limit": limit}
        query = query.filter(NewProductApplication.stock_inquiry_id == stock_inquiry_id)
    if applicant_id:
        query = query.filter(NewProductApplication.applicant_id == applicant_id)
    text = (q or "").strip()
    if text:
        like = f"%{text}%"
        folded = "".join(ch for ch in text if ch.isalnum()).upper()
        line_match = exists().where(
            NewProductApplicationLine.application_id == NewProductApplication.id,
            or_(
                NewProductApplicationLine.description.ilike(like),
                _folded(NewProductApplicationLine.proposed_code).like(f"%{folded or text.upper()}%"),
            ),
        )
        query = query.filter(
            or_(
                NewProductApplication.application_no.ilike(like),
                NewProductApplication.customer_name.ilike(like),
                NewProductApplication.project_name.ilike(like),
                line_match,
            )
        )
    total = query.count()
    column = _SORTS.get(sort or "", NewProductApplication.created_at)
    ordered = column.asc() if direction == "asc" else column.desc()
    rows = (
        query.order_by(ordered.nulls_last(), NewProductApplication.application_no.desc())
        .offset((page - 1) * limit)
        .limit(limit)
        .all()
    )
    return {"items": [serialize_summary(db, r) for r in rows], "total": total, "page": page, "limit": limit}


def _names(db: Session, model, ids: Iterable[Any], *cols) -> Dict[str, Any]:
    wanted = {str(i) for i in ids if i}
    if not wanted:
        return {}
    rows = db.query(model.id, *cols).filter(model.id.in_(wanted)).all()
    return {str(r[0]): r[1:] for r in rows}


def _user_names(db: Session, ids: Iterable[Any]) -> Dict[str, str]:
    return {k: v[0] for k, v in _names(db, User, ids, User.name).items()}


def _num(value: Optional[Decimal]) -> Optional[str]:
    if value is None:
        return None
    return format(Decimal(value).normalize(), "f")


def _money(value: Optional[Decimal]) -> Optional[str]:
    return None if value is None else f"{Decimal(value):.2f}"


def _iso(value: Any) -> Optional[str]:
    return value.isoformat() if value is not None else None


def serialize_summary(db: Session, app: NewProductApplication) -> Dict[str, Any]:
    lines = list(app.lines)
    first = lines[0] if lines else None
    inquiry = (
        db.query(StockInquiry.inquiry_number).filter(StockInquiry.id == app.stock_inquiry_id).scalar()
        if app.stock_inquiry_id
        else None
    )
    applicant = _user_names(db, [app.applicant_id]).get(str(app.applicant_id)) if app.applicant_id else None
    return {
        "id": app.id,
        "application_no": app.application_no,
        "status": app.status,
        "status_label": STATUS_LABELS.get(app.status, app.status),
        "source": app.source,
        "applicant_id": app.applicant_id,
        "applicant_name": applicant,
        "stock_inquiry_id": app.stock_inquiry_id,
        "stock_inquiry_number": inquiry,
        "customer_name": app.customer_name,
        "project_name": app.project_name,
        "first_line": (first.description or first.proposed_code) if first else None,
        "lines_total": len(lines),
        "lines_done": sum(1 for line in lines if _resolved(line)),
        "opened_codes": [],
        "needed_by": _iso(app.needed_by),
        "submitted_at": _iso(app.submitted_at),
        "created_at": _iso(app.created_at),
    }


def serialize_detail(db: Session, app: NewProductApplication, *, portal: bool = False) -> Dict[str, Any]:
    lines = list(app.lines)
    cats = _names(db, ProductCategory, [l.category_id for l in lines], ProductCategory.category_code, ProductCategory.category_name)
    uoms = _names(db, UnitOfMeasure, [l.base_uom_id for l in lines], UnitOfMeasure.uom_code, UnitOfMeasure.uom_name)
    brands = _names(db, Brand, [l.brand_id for l in lines], Brand.brand_name)
    types = _names(db, ItemType, [l.item_type_id for l in lines], ItemType.item_type_name)
    suppliers = _names(db, Supplier, [l.supplier_id for l in lines], Supplier.supplier_code, Supplier.supplier_name)
    products = _names(
        db, Product, [l.opened_product_id for l in lines], Product.product_code, Product.product_name, Product.is_active
    )
    users = _user_names(db, [app.decided_by, app.applicant_id, *[l.resolved_by for l in lines]])
    customer = (
        db.query(Customer.customer_code, Customer.customer_name).filter(Customer.id == app.customer_id).first()
        if app.customer_id
        else None
    )
    inquiry = (
        db.query(StockInquiry.inquiry_number, StockInquiry.product_code)
        .filter(StockInquiry.id == app.stock_inquiry_id)
        .first()
        if app.stock_inquiry_id
        else None
    )

    def line_out(l: NewProductApplicationLine) -> Dict[str, Any]:
        cat = cats.get(str(l.category_id), (None, None))
        uom = uoms.get(str(l.base_uom_id), (None, None))
        sup = suppliers.get(str(l.supplier_id), (None, None))
        prod = products.get(str(l.opened_product_id), (None, None, None))
        out = {
            "id": l.id,
            "line_no": l.line_no,
            "proposed_code": l.proposed_code,
            "description": l.description,
            "further_description": l.further_description,
            "category_id": l.category_id,
            "category_code": cat[0],
            "category_name": cat[1],
            "base_uom_id": l.base_uom_id,
            "base_uom_code": uom[0],
            "base_uom_name": uom[1],
            "brand_id": l.brand_id,
            "brand_name": brands.get(str(l.brand_id), (None,))[0],
            "item_type_id": l.item_type_id,
            "item_type_name": types.get(str(l.item_type_id), (None,))[0],
            "supplier_id": l.supplier_id,
            "supplier_code": sup[0],
            "supplier_name": sup[1],
            "supplier_item_code": l.supplier_item_code,
            "cost_price": _money(l.cost_price),
            "selling_price": _money(l.selling_price),
            "expected_qty": _num(l.expected_qty),
            "remark": l.remark,
            "opened_product_id": l.opened_product_id,
            "opened_product_code": prod[0],
            "opened_product_name": prod[1],
            "opened_product_active": prod[2],
            "closed_reason": l.closed_reason,
            "resolved_by": l.resolved_by,
            "resolved_by_name": users.get(str(l.resolved_by)) if l.resolved_by else None,
            "resolved_at": _iso(l.resolved_at),
        }
        if portal and not PORTAL_SUPPLIER_AND_COST:
            for key in ("supplier_id", "supplier_code", "supplier_name", "cost_price"):
                out.pop(key, None)
        return out

    summary = serialize_summary(db, app)
    summary["opened_codes"] = [products[str(l.opened_product_id)][0] for l in lines if l.opened_product_id and str(l.opened_product_id) in products]
    return {
        **summary,
        "stock_inquiry_product_code": inquiry[1] if inquiry else None,
        "customer_id": app.customer_id,
        "customer_code": customer[0] if customer else None,
        "remark": app.remark,
        "decided_at": _iso(app.decided_at),
        "decided_by": app.decided_by,
        "decided_by_name": users.get(str(app.decided_by)) if app.decided_by else None,
        "reject_reason": app.reject_reason,
        "revise_reason": app.revise_reason,
        "cancelled_at": _iso(app.cancelled_at),
        "cancel_reason": app.cancel_reason,
        "completed_at": _iso(app.completed_at),
        "updated_at": _iso(app.updated_at),
        "lines": [line_out(l) for l in lines],
    }


# ------------------------------------------------------------------ notifications


def _holders(db: Session, slug: str, company_id: str, exclude: Iterable[Any] = ()) -> List[str]:
    from app.services.project_notify_service import approver_user_ids

    return approver_user_ids(db, company_id=company_id, exclude=set(exclude), slug=slug)


_EVENTS = {
    "submitted": ("waits for your approval", "approvers"),
    "withdrawn": ("was withdrawn by the applicant", "approvers"),
    "approved": ("is approved: codes to open", "purchasing"),
    "rejected": ("was rejected", "applicant"),
    "completed": ("is done: the codes are open", "applicant"),
}


def notify(db: Session, app: NewProductApplication, event: Optional[str]) -> int:
    """Tell the people who now have to act (card rule 8). Call AFTER the move commits; a
    failure is logged and swallowed per recipient, so it never undoes the move."""
    if event not in _EVENTS:
        return 0
    phrase, audience = _EVENTS[event]
    try:
        if audience == "approvers":
            recipients = _holders(db, APPROVE, app.company_id, exclude=[app.applicant_id])
        elif audience == "purchasing":
            recipients = _holders(db, OPEN_CODE, app.company_id)
        else:
            recipients = [app.applicant_id] if app.applicant_id else []
        from app.services.notification_service import NotificationService

        service = NotificationService(db)
    except Exception as exc:  # noqa: BLE001
        logger.warning("new product application notice not sent: %s %s (%s)", event, app.id, exc)
        return 0

    sent = 0
    if event == "approved":
        open_lines = sum(1 for line in app.lines if not _resolved(line))
        title = f"{app.application_no}: {open_lines} new code{'s' if open_lines != 1 else ''} to open"
    else:
        title = f"{app.application_no} {phrase}"
    for user_id in {str(u) for u in recipients if u}:
        try:
            service.create_with_channel_preferences(
                user_id=user_id,
                type=f"new_product_application_{event}",
                title=title,
                body=title,
                data={
                    "application_id": str(app.id),
                    "application_no": app.application_no,
                    "link": f"/procurement-management/new-product-applications/{app.id}",
                },
                source_entity_type=ENTITY,
                source_entity_id=str(app.id),
                dedup_key=f"{app.id}:{event}:{app.updated_at.isoformat() if app.updated_at else ''}",
                event_type=f"new_product_application_{event}",
                send_in_app=True,
                send_email=True,
                send_whatsapp=False,
                send_web_push=False,
            )
            sent += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("new product application notice not sent to %s: %s (%s)", user_id, event, exc)
            if not db.is_active:
                db.rollback()
    return sent
