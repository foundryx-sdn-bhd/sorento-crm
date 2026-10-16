"""Sample request service (SR-CORE): create, edit a draft, submit, move, list, print.

The staff router and the portal router both call this module, so every rule lives once.
Ownership and permission are decided here from an `Actor`, never in the routers:

- owner = `salesperson_id == actor.user_id` (staff) or `contact_id == actor.contact_id` (portal);
- `can_process` (`projects.sample_requests.process`) moves a request along and fills the
  internal fields; the owner may only submit and cancel a Draft / Submitted request.

The legal moves are the edges of the `sample_request` status graph (card rule 10), so an
admin who edits the graph changes them; the permission rules are by status KEY.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional

from dateutil.relativedelta import relativedelta
from sqlalchemy import case, func, or_
from sqlalchemy.orm import Session

from app.models.product import Brand, Product
from app.models.projects import (
    Project,
    ProjectQuotation,
    ProjectQuotationVersion,
    ProjectSample,
)
from app.models.sample_request import SampleRequest, SampleRequestLine
from app.models.status import Status, StatusTransition
from app.models.user import SystemSetting, User
from app.services.error_handler import AppException, handle_not_found
from app.services.pdf_render import today_in_malaysia

ENTITY = "sample_request"

DRAFT = "draft"
SUBMITTED = "submitted"
PROCESSING = "processing"
CUSTOMER_CONFIRMED = "customer_confirmed"
DELIVERED = "delivered"
CANCELLED = "cancelled"

#: What the coordinator queue shows until a filter says otherwise (AC-SR-51).
DEFAULT_OPEN = (SUBMITTED, PROCESSING, CUSTOMER_CONFIRMED)

HEADER_TEXT = (
    "customer_name",
    "project_title",
    "scope_note",
    "ship_to_name",
    "ship_to_address",
    "site_contact_name",
    "site_contact_phone",
    "site_contact_note",
    "purpose",
    "board_label",
)
#: NOT NULL columns: an explicit null in a payload means "leave as is".
HEADER_NOT_NULL = ("loan_value", "loan_unit", "goods_from")


@dataclass
class Actor:
    user_id: Optional[str] = None
    contact_id: Optional[str] = None
    can_process: bool = False
    can_view_all: bool = False


# ------------------------------------------------------------------ small helpers


def _clean(value: Any) -> Any:
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def _is_uuid(value: Any) -> bool:
    try:
        uuid.UUID(str(value))
        return True
    except (ValueError, AttributeError, TypeError):
        return False


def _forbidden(message: str = "You do not have access to this sample request.") -> AppException:
    return AppException(status_code=403, message=message, code="FORBIDDEN")


def _incomplete(field: str, message: str) -> AppException:
    return AppException(
        status_code=422,
        message=message,
        code="SAMPLE_REQUEST_INCOMPLETE",
        details={"field": field},
    )


def is_owner(req: SampleRequest, actor: Actor) -> bool:
    if actor.contact_id is not None:
        return req.contact_id is not None and str(req.contact_id) == str(actor.contact_id)
    return actor.user_id is not None and req.salesperson_id == actor.user_id


def assert_can_view(req: SampleRequest, actor: Actor) -> None:
    if not (actor.can_view_all or is_owner(req, actor)):
        raise _forbidden()


def settings_flags(db: Session) -> tuple[bool, bool]:
    row = db.query(SystemSetting).first()
    return (
        bool(getattr(row, "sample_request_require_project", False)),
        bool(getattr(row, "sample_request_require_quotation", False)),
    )


def _statuses(db: Session) -> Dict[str, Status]:
    rows = (
        db.query(Status)
        .filter(Status.entity_type == ENTITY, Status.scope_id.is_(None))
        .all()
    )
    if not rows:
        raise AppException(
            status_code=500,
            message="The sample request status graph has not been set up.",
            code="SAMPLE_REQUEST_GRAPH_MISSING",
        )
    return {s.key: s for s in rows}


def status_key(db: Session, req: SampleRequest) -> Optional[str]:
    if req.status_id is None:
        return None
    row = db.query(Status.key).filter(Status.id == req.status_id).first()
    return row[0] if row else None


def _set_status(db: Session, req: SampleRequest, key: str) -> None:
    req.status_id = _statuses(db)[key].id
    db.flush()


def _edge_exists(db: Session, frm: Optional[str], to: str) -> bool:
    statuses = _statuses(db)
    if frm not in statuses or to not in statuses:
        return False
    return (
        db.query(StatusTransition.id)
        .filter(
            StatusTransition.entity_type == ENTITY,
            StatusTransition.from_status_id == statuses[frm].id,
            StatusTransition.to_status_id == statuses[to].id,
        )
        .first()
        is not None
    )


def illegal_move(frm: Optional[str], to: str) -> AppException:
    return AppException(
        status_code=409,
        message=f"A sample request cannot move from {frm or 'no status'} to {to}.",
        code="SAMPLE_REQUEST_ILLEGAL_MOVE",
    )


# ------------------------------------------------------------------ validation


def _check_products(db: Session, lines: Iterable[Dict[str, Any]]) -> None:
    """Every product and alternative must exist and be active (AC-SR-10)."""
    wanted: set[str] = set()
    for line in lines:
        for key in ("product_id", "alt_product_id"):
            value = line.get(key)
            if value is None:
                continue
            if not _is_uuid(value):
                raise _product_invalid()
            wanted.add(str(value))
    if not wanted:
        return
    found = {
        str(pid)
        for (pid,) in db.query(Product.id)
        .filter(Product.id.in_(wanted), Product.is_active.is_(True))
        .all()
    }
    if wanted - found:
        raise _product_invalid()


def _product_invalid() -> AppException:
    return AppException(
        status_code=422,
        message="Every line needs a product from the product list, and it has to be active.",
        code="SAMPLE_REQUEST_PRODUCT_INVALID",
        details={"field": "lines"},
    )


def _check_quotation(db: Session, project_id: Optional[str], version_id: Optional[str]) -> None:
    if not version_id:
        return
    bad = AppException(
        status_code=422,
        message="That quotation does not belong to the project.",
        code="SAMPLE_REQUEST_QUOTATION_PROJECT",
        details={"field": "quotation_version_id"},
    )
    if not _is_uuid(version_id) or not project_id:
        raise bad
    owner = (
        db.query(ProjectQuotation.project_id)
        .join(ProjectQuotationVersion, ProjectQuotationVersion.quotation_id == ProjectQuotation.id)
        .filter(ProjectQuotationVersion.id == version_id)
        .scalar()
    )
    if owner is None or str(owner) != str(project_id):
        raise bad


def _check_refs(db: Session, data: Dict[str, Any]) -> None:
    from app.models.order import Customer

    cid = data.get("customer_id")
    if cid and (
        not _is_uuid(cid) or db.query(Customer.id).filter(Customer.id == cid).first() is None
    ):
        raise AppException(
            status_code=422,
            message="That customer was not found.",
            code="SAMPLE_REQUEST_CUSTOMER_INVALID",
            details={"field": "customer_id"},
        )
    pid = data.get("project_id")
    if pid and (
        not _is_uuid(pid) or db.query(Project.id).filter(Project.id == pid).first() is None
    ):
        raise AppException(
            status_code=422,
            message="That project was not found.",
            code="SAMPLE_REQUEST_PROJECT_INVALID",
            details={"field": "project_id"},
        )


def assert_portal_project_allowed(db: Session, *, contact, project_id: Optional[str]) -> None:
    """A project picked in the portal must be one `projects_for_contact` returns (AC-SR-42)."""
    if not project_id:
        return
    from app.models.base import company_scope
    from app.services.sponsorship_link_service import contact_company_ids

    company_ids = contact_company_ids(db, contact=contact)
    allowed = False
    if company_ids and _is_uuid(project_id):
        with company_scope(db, frozenset(company_ids)):
            allowed = (
                db.query(Project.id)
                .filter(Project.id == project_id, Project.company_id.in_(company_ids))
                .first()
                is not None
            )
    if not allowed:
        raise AppException(
            status_code=422,
            message="Pick one of your own projects.",
            code="SAMPLE_REQUEST_PROJECT_NOT_ALLOWED",
            details={"field": "project_id"},
        )


def validate_submit(db: Session, req: SampleRequest) -> None:
    """The refusals of AC-SR-02/03/06/21/22, in the contract's order."""
    require_project, require_quotation = settings_flags(db)

    if not (req.customer_id or _clean(req.customer_name)):
        raise _incomplete("customer_id", "Pick the customer, or type the customer name.")
    if require_project:
        if not req.project_id:
            raise _incomplete("project_id", "Pick the project.")
    elif not (req.project_id or _clean(req.project_title)):
        raise _incomplete("project_id", "Pick the project, or type the project title.")
    if require_quotation and not req.quotation_version_id:
        raise _incomplete("quotation_version_id", "Pick the quotation.")
    if not (req.delivery_date or req.deliver_immediately):
        raise _incomplete(
            "delivery_date", "Pick a delivery date, or choose Immediate arrangement."
        )
    if not req.delivery_mode:
        raise _incomplete("delivery_mode", "Choose a delivery mode.")
    if not _clean(req.ship_to_name):
        raise _incomplete("ship_to_name", "Enter the ship-to name.")
    if not req.lines and not req.board_mounting:
        raise _incomplete("lines", "Add at least one item.")
    if req.board_mounting and not _clean(req.board_label):
        raise _incomplete("board_label", "Enter the board label.")


# ------------------------------------------------------------------ writes


def _apply(db: Session, req: SampleRequest, data: Dict[str, Any]) -> None:
    """Copy a (partial) payload onto the header and replace the lines when given."""
    data = dict(data)
    lines = data.pop("lines", None)

    if data.get("delivery_date") and data.get("deliver_immediately"):
        raise AppException(
            status_code=422,
            message="Choose a delivery date or Immediate arrangement, not both.",
            code="SAMPLE_REQUEST_DELIVERY_BOTH",
            details={"field": "delivery_date"},
        )

    _check_refs(db, data)
    line_dicts: List[Dict[str, Any]] = [dict(line) for line in (lines or [])]
    _check_products(db, line_dicts)

    for field in HEADER_TEXT:
        if field in data:
            setattr(req, field, _clean(data[field]))
    for field in ("customer_id", "project_id", "quotation_version_id", "delivery_mode"):
        if field in data:
            setattr(req, field, data[field] or None)
    for field in HEADER_NOT_NULL:
        if data.get(field) is not None:
            setattr(req, field, data[field])
    for field in ("deliver_immediately", "board_mounting"):
        if field in data:
            setattr(req, field, bool(data[field]))
    if "delivery_date" in data:
        req.delivery_date = data["delivery_date"]
        if data["delivery_date"] and "deliver_immediately" not in data:
            req.deliver_immediately = False
    if data.get("deliver_immediately"):
        req.delivery_date = None

    _check_quotation(db, req.project_id, req.quotation_version_id)

    if lines is not None:
        req.lines = [
            SampleRequestLine(
                line_no=index,
                product_id=line["product_id"],
                alt_product_id=line.get("alt_product_id") or None,
                qty=line.get("qty") or 1,
                remark=_clean(line.get("remark")),
            )
            for index, line in enumerate(line_dicts, start=1)
        ]
    db.flush()


def create_draft(
    db: Session,
    *,
    salesperson_id: Optional[str],
    data: Dict[str, Any],
    source: str,
    contact_id: Optional[str] = None,
    space_id: Optional[str] = None,
) -> SampleRequest:
    req = SampleRequest(
        source=source,
        salesperson_id=salesperson_id,
        contact_id=contact_id,
        space_id=space_id,
        request_date=today_in_malaysia(),
        status_id=_statuses(db)[DRAFT].id,
        loan_value=2,
        loan_unit="months",
        goods_from="either",
        deliver_immediately=False,
        board_mounting=False,
    )
    _apply(db, req, data)
    db.add(req)
    db.flush()
    return req


def update_draft(db: Session, req: SampleRequest, *, actor: Actor, data: Dict[str, Any]) -> None:
    if not is_owner(req, actor):
        raise _forbidden("Only the person who raised this request can edit it.")
    if status_key(db, req) != DRAFT:
        raise AppException(
            status_code=409,
            message="A submitted request cannot be edited. Cancel it and raise a new one.",
            code="SAMPLE_REQUEST_NOT_EDITABLE",
        )
    _apply(db, req, data)


def update_internal(
    db: Session, req: SampleRequest, *, actor: Actor, consignment_no: Optional[str], po_no: Optional[str]
) -> None:
    if not actor.can_process:
        raise _forbidden("Only the coordinator can fill the internal fields.")
    if status_key(db, req) == CANCELLED:
        raise AppException(
            status_code=409,
            message="A cancelled request cannot be changed.",
            code="SAMPLE_REQUEST_CANCELLED",
        )
    req.consignment_no = _clean(consignment_no)
    req.po_no = _clean(po_no)
    db.flush()


def _record(db: Session, req: SampleRequest, *, actor: Actor, label: str) -> None:
    """One project activity row per move, when the request has a project (AC-SR-34)."""
    if not req.project_id:
        return
    project = db.query(Project).filter(Project.id == req.project_id).first()
    if project is None:
        return
    from app.services import project_activity_service as activity

    activity.record_project_event(
        db,
        project=project,
        template="sample_request_moved",
        payload={"request_id": str(req.id), "request_no": req.request_no, "status": label},
        actor_id=actor.user_id,
        body_text=f"Sample request {req.request_no}: {label}",
    )


def _label(db: Session, key: str) -> str:
    return _statuses(db)[key].label


def submit(db: Session, req: SampleRequest, *, actor: Actor) -> None:
    if not is_owner(req, actor):
        raise _forbidden("Only the person who raised this request can submit it.")
    frm = status_key(db, req)
    if frm != DRAFT or not _edge_exists(db, frm, SUBMITTED):
        raise illegal_move(frm, SUBMITTED)
    validate_submit(db, req)
    _set_status(db, req, SUBMITTED)
    req.submitted_at = datetime.utcnow()
    db.flush()
    _record(db, req, actor=actor, label=_label(db, SUBMITTED))


def _add_loan(start: date, value: int, unit: str) -> date:
    return start + (relativedelta(months=value) if unit == "months" else relativedelta(days=value))


def move(
    db: Session,
    req: SampleRequest,
    *,
    actor: Actor,
    to: str,
    on: Optional[date] = None,
    reason: Optional[str] = None,
) -> None:
    frm = status_key(db, req)
    if to == CANCELLED and frm in (DRAFT, SUBMITTED):
        allowed = actor.can_process or is_owner(req, actor)
    else:
        allowed = actor.can_process
    if not allowed:
        raise _forbidden("You cannot make this move.")

    # Draft -> Submitted is `submit`, which carries the validation.
    if to == SUBMITTED or not _edge_exists(db, frm, to):
        raise illegal_move(frm, to)

    reason = _clean(reason)
    if to in (CUSTOMER_CONFIRMED, DELIVERED) and on is None:
        raise AppException(
            status_code=422,
            message="Enter the date.",
            code="SAMPLE_REQUEST_DATE_REQUIRED",
            details={"field": "date"},
        )
    if to == CANCELLED and frm == PROCESSING and not reason:
        raise AppException(
            status_code=422,
            message="Enter the reason for cancelling.",
            code="SAMPLE_REQUEST_REASON_REQUIRED",
            details={"field": "reason"},
        )

    _set_status(db, req, to)
    if to == CUSTOMER_CONFIRMED:
        req.confirmed_at = on
    elif to == DELIVERED:
        req.delivered_at = on
        req.return_due = _add_loan(on, req.loan_value, req.loan_unit)
        _create_feedback_row(db, req, on)
    elif to == CANCELLED:
        req.cancelled_at = datetime.utcnow()
        req.cancel_reason = reason
    db.flush()
    _record(db, req, actor=actor, label=_label(db, to))


def _create_feedback_row(db: Session, req: SampleRequest, on: date) -> None:
    """Card rule 13: Delivered with a quotation version feeds the Samples tab."""
    if not (req.quotation_version_id and req.project_id):
        return
    db.add(
        ProjectSample(
            project_id=req.project_id,
            quotation_version_id=req.quotation_version_id,
            submitted_on=on,
            submitted_by=req.salesperson_id,
        )
    )
    db.flush()


# ------------------------------------------------------------------ reads


def get_for(db: Session, request_id: str, *, contact_id: Optional[str] = None) -> SampleRequest:
    q = db.query(SampleRequest).filter(SampleRequest.id == request_id)
    if contact_id is not None:
        q = q.filter(SampleRequest.contact_id == contact_id)
    req = q.first() if _is_uuid(request_id) else None
    if req is None:
        raise handle_not_found("Sample request", request_id)
    return req


def _folded(column):
    return func.upper(func.regexp_replace(column, "[^A-Za-z0-9]", "", "g"))


def search_products(db: Session, q: str, limit: int = 20) -> List[Dict[str, Any]]:
    """Active products whose code (dashes, spaces, case ignored) or name matches. The
    search only LISTS products; a person always picks one (card rule 5)."""
    text = (q or "").strip()
    if not text:
        return []
    clauses = [Product.product_name.ilike(f"%{text}%")]
    folded = "".join(ch for ch in text if ch.isalnum()).upper()
    if folded:
        clauses.append(_folded(Product.product_code).like(f"%{folded}%"))
    rows = (
        db.query(Product, Brand.brand_name)
        .outerjoin(Brand, Brand.id == Product.brand_id)
        .filter(Product.is_active.is_(True), or_(*clauses))
        .order_by(Product.product_code)
        .limit(limit)
        .all()
    )
    return [
        {
            "id": p.id,
            "product_code": p.product_code,
            "product_name": p.product_name,
            "brand_name": brand_name,
        }
        for p, brand_name in rows
    ]


LIST_SORTS = {
    "request_no": SampleRequest.request_no,
    "request_date": SampleRequest.request_date,
    "delivery_date": SampleRequest.delivery_date,
    "customer_name": SampleRequest.customer_name,
    "ship_to_name": SampleRequest.ship_to_name,
}


def list_for(
    db: Session,
    *,
    actor: Actor,
    statuses: Optional[List[str]] = None,
    project_id: Optional[str] = None,
    contact_id: Optional[str] = None,
    q: Optional[str] = None,
    page: int = 1,
    limit: int = 25,
    sort: Optional[str] = None,
    direction: str = "asc",
) -> Dict[str, Any]:
    """One page of list rows. `contact_id` is the portal's own-rows filter; staff without
    `.view` see only the rows they own."""
    from app.models.order import Customer

    query = db.query(SampleRequest)
    if contact_id is not None:
        query = query.filter(SampleRequest.contact_id == contact_id)
    elif not actor.can_view_all:
        query = query.filter(SampleRequest.salesperson_id == actor.user_id)
    if project_id:
        query = query.filter(SampleRequest.project_id == project_id)
    if statuses:
        ids = [s.id for k, s in _statuses(db).items() if k in statuses]
        query = query.filter(SampleRequest.status_id.in_(ids))
    needle = (q or "").strip()
    if needle:
        like = f"%{needle}%"
        query = (
            query.outerjoin(Customer, Customer.id == SampleRequest.customer_id)
            .outerjoin(Project, Project.id == SampleRequest.project_id)
            .filter(
                or_(
                    SampleRequest.request_no.ilike(like),
                    SampleRequest.customer_name.ilike(like),
                    SampleRequest.project_title.ilike(like),
                    SampleRequest.ship_to_name.ilike(like),
                    Customer.customer_name.ilike(like),
                    Customer.customer_code.ilike(like),
                    Project.title.ilike(like),
                    Project.project_code.ilike(like),
                )
            )
        )

    total = query.with_entities(func.count(SampleRequest.id)).scalar() or 0

    column = LIST_SORTS.get(sort or "")
    if column is not None:
        ordered = column.desc() if direction == "desc" else column.asc()
        query = query.order_by(ordered.nulls_last(), SampleRequest.request_no.asc())
    else:
        # Immediate arrangement first, then the needed date, soonest first.
        query = query.order_by(
            case((SampleRequest.deliver_immediately.is_(True), 0), else_=1),
            SampleRequest.delivery_date.asc().nulls_last(),
            SampleRequest.request_no.asc(),
        )
    rows = query.offset((page - 1) * limit).limit(limit).all()
    return {
        "data": serialize_rows(db, rows),
        "pagination": {"total": total, "page": page, "limit": limit},
        "empty": total == 0,
    }


def _names(db: Session, reqs: List[SampleRequest]) -> Dict[str, Dict[str, Any]]:
    from app.models.order import Customer

    cust_ids = {r.customer_id for r in reqs if r.customer_id}
    proj_ids = {r.project_id for r in reqs if r.project_id}
    user_ids = {r.salesperson_id for r in reqs if r.salesperson_id}
    customers = (
        {c.id: c for c in db.query(Customer).filter(Customer.id.in_(cust_ids)).all()}
        if cust_ids
        else {}
    )
    projects = (
        {p.id: p for p in db.query(Project).filter(Project.id.in_(proj_ids)).all()}
        if proj_ids
        else {}
    )
    users = (
        {u.id: u for u in db.query(User).filter(User.id.in_(user_ids)).all()} if user_ids else {}
    )
    labels = {s.id: (s.key, s.label) for s in _statuses(db).values()}
    return {"customers": customers, "projects": projects, "users": users, "labels": labels}


def _row_common(req: SampleRequest, ctx: Dict[str, Any]) -> Dict[str, Any]:
    customer = ctx["customers"].get(req.customer_id) if req.customer_id else None
    project = ctx["projects"].get(req.project_id) if req.project_id else None
    user = ctx["users"].get(req.salesperson_id) if req.salesperson_id else None
    key, label = ctx["labels"].get(req.status_id, (None, None))
    return {
        "id": req.id,
        "request_no": req.request_no,
        "status": key,
        "status_label": label,
        "request_date": req.request_date,
        "submitted_at": req.submitted_at,
        "delivery_date": req.delivery_date,
        "deliver_immediately": bool(req.deliver_immediately),
        "delivery_mode": req.delivery_mode,
        "customer_name": customer.customer_name if customer else req.customer_name,
        "customer_code": customer.customer_code if customer else None,
        "project_id": req.project_id,
        "project_name": project.title if project else req.project_title,
        "ship_to_name": req.ship_to_name,
        "salesperson_name": (user.name if user else None),
        "board_mounting": bool(req.board_mounting),
    }


def serialize_rows(db: Session, reqs: List[SampleRequest]) -> List[Dict[str, Any]]:
    """List rows. NEVER the site contact phone or the ship-to address (AC-SR-54)."""
    if not reqs:
        return []
    ctx = _names(db, reqs)
    counts = dict(
        db.query(SampleRequestLine.request_id, func.count(SampleRequestLine.id))
        .filter(SampleRequestLine.request_id.in_([r.id for r in reqs]))
        .group_by(SampleRequestLine.request_id)
        .all()
    )
    out = []
    for req in reqs:
        row = _row_common(req, ctx)
        row["line_count"] = int(counts.get(req.id, 0))
        out.append(row)
    return out


def serialize_detail(db: Session, req: SampleRequest) -> Dict[str, Any]:
    ctx = _names(db, [req])
    project = ctx["projects"].get(req.project_id) if req.project_id else None
    row = _row_common(req, ctx)

    quotation_label = None
    if req.quotation_version_id:
        found = (
            db.query(ProjectQuotation.scope_label, ProjectQuotationVersion.version_no)
            .join(
                ProjectQuotationVersion,
                ProjectQuotationVersion.quotation_id == ProjectQuotation.id,
            )
            .filter(ProjectQuotationVersion.id == req.quotation_version_id)
            .first()
        )
        if found:
            quotation_label = f"{found[0]} v{found[1]}"

    alt = Product.__table__.alias("alt_product")
    line_rows = (
        db.query(SampleRequestLine, Product, Brand.brand_name, alt.c.product_code)
        .join(Product, Product.id == SampleRequestLine.product_id)
        .outerjoin(Brand, Brand.id == Product.brand_id)
        .outerjoin(alt, alt.c.id == SampleRequestLine.alt_product_id)
        .filter(SampleRequestLine.request_id == req.id)
        .order_by(SampleRequestLine.line_no)
        .all()
    )
    lines = [
        {
            "id": line.id,
            "line_no": line.line_no,
            "product_id": line.product_id,
            "product_code": product.product_code,
            "product_name": product.product_name,
            "brand_name": brand_name,
            "product_active": bool(product.is_active),
            "alt_product_id": line.alt_product_id,
            "alt_product_code": alt_code,
            "qty": line.qty,
            "remark": line.remark,
        }
        for line, product, brand_name, alt_code in line_rows
    ]
    row.update(
        {
            "source": req.source,
            "salesperson_id": req.salesperson_id,
            "customer_id": req.customer_id,
            "project_title": req.project_title,
            "project_code": project.project_code if project else None,
            "quotation_version_id": req.quotation_version_id,
            "quotation_label": quotation_label,
            "scope_note": req.scope_note,
            "ship_to_address": req.ship_to_address,
            "site_contact_name": req.site_contact_name,
            "site_contact_phone": req.site_contact_phone,
            "site_contact_note": req.site_contact_note,
            "loan_value": req.loan_value,
            "loan_unit": req.loan_unit,
            "goods_from": req.goods_from,
            "purpose": req.purpose,
            "board_label": req.board_label,
            "confirmed_at": req.confirmed_at,
            "delivered_at": req.delivered_at,
            "return_due": req.return_due,
            "cancelled_at": req.cancelled_at,
            "cancel_reason": req.cancel_reason,
            "consignment_no": req.consignment_no,
            "po_no": req.po_no,
            "created_at": req.created_at,
            "updated_at": req.updated_at,
            "lines": lines,
        }
    )
    return row


def render_pdf(db: Session, req: SampleRequest) -> bytes:
    from app.services.pdf_render import PDFRenderingUnavailable, render_html
    from app.services.sample_request_pdf_service import build_html

    try:
        return render_html(build_html(serialize_detail(db, req)))
    except PDFRenderingUnavailable as exc:
        raise AppException(status_code=503, message=str(exc), code="PDF_UNAVAILABLE")
