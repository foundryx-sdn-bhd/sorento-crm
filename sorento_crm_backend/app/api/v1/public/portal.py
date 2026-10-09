"""Public user submission portal endpoints (no CRM login).

Auth: 7-day portal token. Token travels in either:
- ``X-Portal-Token`` header
- ``token`` query parameter

When a token expires the contact requests an OTP via ``POST /request-otp`` and
exchanges the code for a fresh token via ``POST /verify-otp``.
"""
from __future__ import annotations

import logging
import os
import uuid
from decimal import Decimal
from typing import Annotated, Optional

from fastapi import (
    APIRouter,
    Body,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Path,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.entity_attachment import EntityAttachmentLink
from app.models.portal import PortalToken
from app.models.resources import Attachment, AttachmentType
from app.services.entity_attachment_service import EntityAttachmentService
# Aliased to the old local name: every `_list_attachments_for(...)` call site
# below is unchanged. Moved to the service module so the price tag request's
# CRM detail route can answer with the same shape without a service reaching
# up into this route module (D49).
from app.services.entity_attachment_service import (
    list_attachments_for_entity as _list_attachments_for,
)
from app.services.portal_form_visibility_service import (
    require_form_visible,
    resolve_visible_form_types,
)
from app.services.error_handler import (
    AppException,
    handle_not_found,
    handle_validation_error,
)
from app.services.uuid_path_param import validate_uuid_path
from app.services.dealer_kit.pricing import flyer_price
from app.services.portal_service import (
    PORTAL_ATTACHMENT_TYPE_CODE,
    PortalAuthError,
    PortalService,
    SUPPORTED_TYPES,
)
from app.utils.http import content_disposition

logger = logging.getLogger(__name__)

router = APIRouter()


def _resolve_portal_token(
    db: Session,
    header_token: Optional[str],
    query_token: Optional[str],
) -> PortalToken:
    raw = (header_token or query_token or "").strip()
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Portal token is required.",
        )
    try:
        return PortalService(db).resolve_token(raw)
    except PortalAuthError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e)
        ) from e


def _portal_actor(db: Session, resolved: PortalToken, request: Optional[Request]):
    """The audit actor of a portal token (identity S0, plan 8.1, AC-11, AC-14).

    - a contact with no user: `contact`, no user;
    - a contact that has a user: `user`, that user;
    - an admin "view as contact" token: `user`, the contact's user (or none), with the
      admin as real_user_id and auth_method `impersonation`.
    """
    from app.audit_context import AuditActor
    from app.models.impersonation import ContactImpersonationSession
    from app.models.user import User

    contact_id = str(resolved.contact_id) if resolved.contact_id else None
    contact_user_id = None
    if contact_id:
        contact_user_id = (
            db.query(User.id)
            .filter(User.respond_contact_id == contact_id, User.is_trashed.is_(False))
            .scalar()
        )
    ip = request.client.host if request is not None and request.client else None
    user_agent = request.headers.get("user-agent") if request is not None else None

    if getattr(resolved, "is_impersonation", False):
        admin_id = (
            db.query(ContactImpersonationSession.admin_user_id)
            .filter(ContactImpersonationSession.portal_token_id == resolved.id)
            .scalar()
        )
        return AuditActor(
            actor_type="user",
            user_id=str(contact_user_id) if contact_user_id else None,
            real_user_id=str(admin_id) if admin_id else None,
            auth_method="impersonation",
            contact_id=contact_id,
            ip_address=ip,
            user_agent=user_agent,
        )
    if contact_user_id:
        return AuditActor(
            actor_type="user",
            user_id=str(contact_user_id),
            real_user_id=str(contact_user_id),
            auth_method="portal_token",
            contact_id=contact_id,
            ip_address=ip,
            user_agent=user_agent,
        )
    return AuditActor(
        actor_type="contact",
        auth_method="portal_token",
        contact_id=contact_id,
        ip_address=ip,
        user_agent=user_agent,
    )


def get_portal_token(
    request: Request,
    x_portal_token: Annotated[Optional[str], Header(alias="X-Portal-Token")] = None,
    token: Annotated[Optional[str], Query()] = None,
    db: Session = Depends(get_db),
) -> PortalToken:
    resolved = _resolve_portal_token(db, x_portal_token, token)
    # Attribute any audited write in this request to the acting contact / user.
    # Stamped on the SHARED db.info as well as the contextvar: FastAPI runs this sync
    # dependency in a different threadpool thread than the path op + flush, so the
    # contextvar alone would not be visible at flush time (see app.audit_context).
    from app.audit_context import set_source, stamp_actor

    stamp_actor(_portal_actor(db, resolved, request), db=db, request=request)
    # The channel, whoever the actor is (an admin viewing as the contact too): #1281 S0.
    set_source("portal")
    return resolved


# ---------- OTP ----------


class OtpRequestPayload(BaseModel):
    contact_id: str
    space_id: str


class OtpVerifyPayload(BaseModel):
    contact_id: str
    space_id: str
    code: str = Field(..., min_length=4, max_length=10)


class OtpResponse(BaseModel):
    sent_to: Optional[str]
    expires_at: str


class TokenResponse(BaseModel):
    token: str
    expires_at: str


@router.post("/request-otp", response_model=OtpResponse)
def portal_request_otp(payload: OtpRequestPayload, request: Request, db: Session = Depends(get_db)):
    # Per-IP global limit on this unauthenticated endpoint - the per-contact
    # cooldown/cap in PortalService can't stop an attacker fanning out across many
    # contact_ids to enumerate or to DOS the Respond.io send queue. Fail-open.
    from app.config import settings as app_settings
    from app.services import rate_limit

    ip = request.client.host if request.client else None
    gate = rate_limit.hit(
        "portal_otp", ip,
        limit=app_settings.rate_limit_portal_otp_max,
        window_seconds=app_settings.rate_limit_portal_otp_window_seconds,
    )
    if not gate.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please try again shortly.",
            headers={"Retry-After": str(gate.retry_after_seconds or app_settings.rate_limit_portal_otp_window_seconds)},
        )
    return PortalService(db).request_otp(payload.contact_id, payload.space_id)


@router.post("/verify-otp", response_model=TokenResponse)
def portal_verify_otp(payload: OtpVerifyPayload, db: Session = Depends(get_db)):
    token = PortalService(db).verify_otp(payload.contact_id, payload.space_id, payload.code)
    return TokenResponse(token=token.token, expires_at=token.expires_at.isoformat())


class PortalTokenInfoResponse(BaseModel):
    contact_id: str
    space_id: str
    expires_at: str
    expired: bool
    revoked: bool
    portal_slug: Optional[str] = None
    name: Optional[str] = None
    masked_phone: Optional[str] = None
    whatsapp_number: Optional[str] = None


@router.get("/token-info", response_model=PortalTokenInfoResponse)
def portal_token_info(token: str, db: Session = Depends(get_db)):
    """Return basic info for any token row in ``portal_tokens``, even when the
    token is expired or revoked. Lets the verify page recover the
    ``contact_id`` / ``space_id`` pair without granting access.
    """
    from app.models.access import RespondContact
    from app.services.portal_service import _utcnow

    row = (
        db.query(PortalToken)
        .filter(PortalToken.token == (token or "").strip())
        .first()
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Token not found.")
    service = PortalService(db)
    contact = (
        db.query(RespondContact)
        .filter(RespondContact.id == row.contact_id)
        .first()
    )
    hint = service.identity_hint(contact) if contact else {}
    return PortalTokenInfoResponse(
        contact_id=row.contact_id,
        space_id=row.space_id,
        expires_at=row.expires_at.isoformat(),
        expired=row.expires_at <= _utcnow(),
        revoked=row.revoked_at is not None,
        portal_slug=contact.portal_slug if contact else None,
        name=hint.get("name"),
        masked_phone=hint.get("masked_phone"),
        whatsapp_number=hint.get("whatsapp_number"),
    )


class PortalSlugInfoResponse(BaseModel):
    contact_id: str
    space_id: str
    name: Optional[str] = None
    masked_phone: Optional[str]
    whatsapp_number: Optional[str]


@router.get("/slug-info/{slug}", response_model=PortalSlugInfoResponse)
def portal_slug_info(slug: str = Path(..., min_length=4, max_length=32), db: Session = Depends(get_db)):
    """Identity hint behind the stable URL /portal/c/{slug}.

    Public by design: the slug is bookmarkable/shareable. Knowing it grants
    nothing beyond the ability to trigger an OTP that goes to the contact's
    own WhatsApp (cooldown + daily cap enforced in request_otp). 404 carries
    no detail - never confirm revoked-vs-missing.
    """
    return PortalSlugInfoResponse(**PortalService(db).slug_info(slug))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def portal_logout(
    x_portal_token: Annotated[Optional[str], Header(alias="X-Portal-Token")] = None,
    db: Session = Depends(get_db),
):
    """Server-side logout: revoke the presented token. Idempotent, and accepts
    expired/unverified tokens too - clearing client storage alone would leave
    a copied token valid until natural expiry.
    """
    raw = (x_portal_token or "").strip()
    if raw:
        PortalService(db).revoke_token(raw)
    return None


# ---------- Contact ----------


class PortalImpersonationInfo(BaseModel):
    session_id: str
    admin_user_id: str
    admin_name: Optional[str]
    admin_email: Optional[str]
    started_at: str


class PortalMeResponse(BaseModel):
    contact_id: str
    space_id: str
    name: Optional[str]
    phone_number: Optional[str]
    expires_at: str
    portal_slug: Optional[str] = None
    whatsapp_number: Optional[str] = None
    # AC-F4: per-contact sponsorship rollout. The form reads this to decide whether the
    # project picker is mandatory; the server enforces it regardless.
    requires_registered_project: bool = False
    impersonation: Optional[PortalImpersonationInfo] = None
    # Portal form types this contact may see (access-type union + per-contact
    # overrides). The landing reads it to decide which entry points to show;
    # every form route still enforces the same rule server-side.
    visible_form_types: list[str] = []


@router.get("/me", response_model=PortalMeResponse)
def portal_me(
    token: PortalToken = Depends(get_portal_token),
    db: Session = Depends(get_db),
):
    service = PortalService(db)
    contact = service.get_contact(token)
    # Lazily mint the stable slug so even legacy tokens surface a
    # bookmarkable URL on first /me. Impersonation sessions stay slug-less on
    # the FE (legacy tree), but the slug itself is harmless to mint.
    portal_slug = service.get_or_create_slug(contact)

    # Surface admin-impersonation context so the portal can show a banner.
    from app.models.impersonation import ContactImpersonationSession
    from app.models.user import User

    impersonation_info: Optional[PortalImpersonationInfo] = None
    session_row = (
        db.query(ContactImpersonationSession)
        .filter(
            ContactImpersonationSession.portal_token_id == token.id,
            ContactImpersonationSession.ended_at.is_(None),
        )
        .first()
    )
    if session_row is not None:
        admin = (
            db.query(User)
            .filter(User.id == session_row.admin_user_id)
            .first()
        )
        impersonation_info = PortalImpersonationInfo(
            session_id=session_row.id,
            admin_user_id=session_row.admin_user_id,
            admin_name=admin.name if admin else None,
            admin_email=admin.email if admin else None,
            started_at=session_row.started_at.isoformat(),
        )

    return PortalMeResponse(
        contact_id=token.contact_id,
        space_id=token.space_id,
        name=contact.name or " ".join(filter(None, [contact.first_name, contact.last_name])).strip() or None,
        phone_number=contact.phone_number,
        expires_at=token.expires_at.isoformat(),
        portal_slug=portal_slug,
        whatsapp_number=service.whatsapp_number_for_contact(contact),
        requires_registered_project=bool(
            getattr(contact, "requires_registered_project", False)
        ),
        impersonation=impersonation_info,
        visible_form_types=sorted(
            resolve_visible_form_types(db, token.contact_id)
        ),
    )


@router.post("/impersonation/stop")
def portal_impersonation_stop(
    token: PortalToken = Depends(get_portal_token),
    db: Session = Depends(get_db),
):
    """End the impersonation session and revoke the token from inside the portal.

    Authenticated via portal token, so the admin can exit impersonation
    directly from the portal banner without needing to switch back to the CRM
    tab first.
    """
    from app.models.impersonation import ContactImpersonationSession
    from app.services.audit_service import log_audit
    from datetime import datetime as _dt

    session_row = (
        db.query(ContactImpersonationSession)
        .filter(
            ContactImpersonationSession.portal_token_id == token.id,
            ContactImpersonationSession.ended_at.is_(None),
        )
        .first()
    )
    if session_row is None:
        # Token still gets revoked so a closed-tab token can't be reused.
        if token.revoked_at is None:
            token.revoked_at = _dt.utcnow()
            db.commit()
        return {"ended": False}

    now = _dt.utcnow()
    session_row.ended_at = now
    if token.revoked_at is None:
        token.revoked_at = now
    db.flush()
    log_audit(
        db,
        entity_type="contact_impersonation_session",
        entity_id=session_row.id,
        action="UPDATE",
        user_id=session_row.admin_user_id,
        description="contact_impersonation_end_via_portal",
        new_values={
            "admin_user_id": session_row.admin_user_id,
            "target_contact_id": session_row.target_contact_id,
            "ended_at": now.isoformat(),
        },
    )
    db.commit()
    return {"ended": True, "session_id": session_row.id}


# ---------- Lookups (gated by portal token) ----------


class ProductLookupItem(BaseModel):
    # Additive (plan section 16, S2-16): the portal opportunity form's product lines need
    # the real id (`sales_opportunity_lines.product_id`), not just the printed code the
    # complaint form's free-text line uses. Named `product_id`, not `id` - this schema's
    # "id" would read as the lookup ROW's own id, which nothing here has any use for.
    product_id: Optional[str] = None
    product_code: str
    product_name: Optional[str] = None
    category_id: Optional[str] = None
    category_code: Optional[str] = None
    category_name: Optional[str] = None
    #: The price the dealer flyer prints; null when the product has none (F7, PR #1296).
    list_price: Optional[Decimal] = None


@router.get("/lookups/products", response_model=list[ProductLookupItem])
def lookup_products(
    q: str = Query("", description="Substring match on code or name"),
    limit: int = Query(20, ge=1, le=50),
    token: PortalToken = Depends(get_portal_token),
    db: Session = Depends(get_db),
):
    from app.models.product import Product, ProductCategory
    query = (
        db.query(Product, ProductCategory)
        .outerjoin(ProductCategory, Product.category_id == ProductCategory.id)
        .filter(Product.is_active.is_(True))
    )
    qs = (q or "").strip()
    if qs:
        like = f"%{qs}%"
        query = query.filter((Product.product_code.ilike(like)) | (Product.product_name.ilike(like)))
    rows = query.order_by(Product.product_code).limit(limit).all()
    return [
        ProductLookupItem(
            product_id=str(p.id) if p.id else None,
            product_code=p.product_code,
            product_name=p.product_name,
            category_id=str(p.category_id) if p.category_id else None,
            category_code=c.category_code if c else None,
            category_name=c.category_name if c else None,
            list_price=flyer_price(p.list_price),
        )
        for (p, c) in rows
    ]


class DebtorLookupItem(BaseModel):
    debtor_name: str


@router.get("/lookups/debtors", response_model=list[DebtorLookupItem])
def lookup_debtors(
    q: str = Query(""),
    limit: int = Query(20, ge=1, le=50),
    token: PortalToken = Depends(get_portal_token),
    db: Session = Depends(get_db),
):
    from app.models.order import Order
    qs = (q or "").strip()
    query = db.query(Order.debtor_name).filter(Order.debtor_name.isnot(None))
    if qs:
        query = query.filter(Order.debtor_name.ilike(f"%{qs}%"))
    rows = query.distinct().order_by(Order.debtor_name).limit(limit).all()
    return [DebtorLookupItem(debtor_name=r[0]) for r in rows if r[0]]


class ProjectLookupItem(BaseModel):
    """A project the CONTACT may attach a sponsorship to (AC-F4a).

    ``company_name`` is on every row rather than only when it disambiguates: a contact
    mapped to two companies cannot otherwise tell two similarly-named phases apart, and a
    field that appears only sometimes reads as a rendering bug.
    """

    id: str
    project_code: str
    title: str
    company_name: Optional[str] = None


@router.get("/lookups/projects", response_model=list[ProjectLookupItem])
def lookup_projects(
    q: str = Query("", description="Substring match on the code or the title"),
    limit: int = Query(20, ge=1, le=50),
    token: PortalToken = Depends(get_portal_token),
    db: Session = Depends(get_db),
):
    """Only the projects of the companies this contact is linked to (AC-F4a).

    A contact linked to no company gets an empty list, which the form turns into "ask the
    Sorento team to link your company" rather than an empty dropdown that looks broken.
    """
    from app.services import sponsorship_link_service

    contact = PortalService(db).get_contact(token)
    rows = sponsorship_link_service.projects_for_contact(
        db, contact=contact, query=q, limit=limit
    )
    return [
        ProjectLookupItem(
            id=str(row["id"]),
            project_code=row["project_code"],
            title=row["title"],
            company_name=row["company_name"],
        )
        for row in rows
    ]


class DOProductLine(BaseModel):
    product_code: str
    product_name: Optional[str] = None
    quantity: Optional[float] = None


class DOLookupItem(BaseModel):
    order_number: str
    debtor_name: Optional[str] = None
    customer_name: Optional[str] = None
    products: list[str] = []
    product_lines: list[DOProductLine] = []
    order_date: Optional[str] = None


@router.get("/lookups/delivery-orders", response_model=list[DOLookupItem])
def lookup_delivery_orders(
    q: str = Query(""),
    limit: int = Query(20, ge=1, le=50),
    start_date: Optional[str] = Query(None, description="ISO date (YYYY-MM-DD) lower bound on order_date"),
    end_date: Optional[str] = Query(None, description="ISO date (YYYY-MM-DD) upper bound on order_date"),
    product_code: Optional[str] = Query(None, description="Filter to DOs containing this product code (substring)"),
    debtor_name: Optional[str] = Query(None, description="Filter to DOs whose debtor or customer name matches (substring)"),
    token: PortalToken = Depends(get_portal_token),
    db: Session = Depends(get_db),
):
    from datetime import date as _date, datetime
    from sqlalchemy import or_
    from app.models.order import Customer, Order, OrderLine
    from app.models.product import Product

    qs = (q or "").strip()
    base = db.query(Order).outerjoin(Customer, Order.customer_id == Customer.id)
    if qs:
        like = f"%{qs}%"
        product_subq = (
            db.query(OrderLine.order_id)
            .join(Product, OrderLine.product_id == Product.id)
            .filter(Product.product_code.ilike(like))
        )
        base = base.filter(
            or_(
                Order.order_number.ilike(like),
                Order.debtor_name.ilike(like),
                Customer.customer_name.ilike(like),
                Order.id.in_(product_subq),
            )
        )
    if start_date:
        try:
            base = base.filter(Order.order_date >= datetime.fromisoformat(start_date))
        except ValueError:
            raise handle_validation_error(f"Invalid start_date: {start_date!r}")
    if end_date:
        try:
            # inclusive upper bound: end of that day
            end_dt = datetime.fromisoformat(end_date).replace(hour=23, minute=59, second=59)
            base = base.filter(Order.order_date <= end_dt)
        except ValueError:
            raise handle_validation_error(f"Invalid end_date: {end_date!r}")
    if product_code and product_code.strip():
        like_p = f"%{product_code.strip()}%"
        product_subq = (
            db.query(OrderLine.order_id)
            .join(Product, OrderLine.product_id == Product.id)
            .filter(Product.product_code.ilike(like_p))
        )
        base = base.filter(Order.id.in_(product_subq))
    if debtor_name and debtor_name.strip():
        like_d = f"%{debtor_name.strip()}%"
        base = base.filter(
            or_(
                Order.debtor_name.ilike(like_d),
                Customer.customer_name.ilike(like_d),
            )
        )
    rows = base.order_by(Order.order_date.desc().nullslast(), Order.order_number.desc()).limit(limit).all()
    out: list[DOLookupItem] = []
    for o in rows:
        customer_name = o.customer.customer_name if getattr(o, "customer", None) else None
        products: list[str] = []
        product_lines: list[DOProductLine] = []
        try:
            from app.models.order import OrderLine as _OrderLine
            from app.models.product import Product as _Product

            lines = (
                db.query(_Product.product_code, _Product.product_name, _OrderLine.quantity)
                .join(_OrderLine, _OrderLine.product_id == _Product.id)
                .filter(_OrderLine.order_id == o.id)
                .order_by(_OrderLine.line_sequence.asc())
                .limit(200)
                .all()
            )
            for code, name, qty in lines:
                if not code:
                    continue
                products.append(code)
                product_lines.append(
                    DOProductLine(
                        product_code=code,
                        product_name=name,
                        quantity=float(qty) if qty is not None else None,
                    )
                )
        except Exception:
            pass
        od = o.order_date
        if od is None:
            order_date_iso = None
        elif isinstance(od, datetime):
            order_date_iso = od.date().isoformat()
        elif isinstance(od, _date):
            order_date_iso = od.isoformat()
        else:
            order_date_iso = str(od)
        out.append(
            DOLookupItem(
                order_number=o.order_number,
                debtor_name=o.debtor_name,
                customer_name=customer_name,
                products=products,
                product_lines=product_lines,
                order_date=order_date_iso,
            )
        )
    return out


_PORTAL_LOOKUP_SET_WHITELIST = {
    "complaints_within_warranty",
    "complaints_complaint_type",
    "complaints_customer_type",
    "complaints_defects_discovered",
    "procurement_sponsor_subject",
    "procurement_sales_type",
}


class LookupSetOption(BaseModel):
    value: str
    label: str


class LookupSetResponse(BaseModel):
    options: list[LookupSetOption]
    # Binding default option the portal pre-selects on a NEW form (from
    # lookup_bindings.default_value); mirrors the system LookupBoundField.
    default_value: Optional[str] = None


@router.get("/lookups/sets/{set_key}", response_model=LookupSetResponse)
def lookup_set_options(
    set_key: str = Path(...),
    token: PortalToken = Depends(get_portal_token),
    db: Session = Depends(get_db),
):
    if set_key not in _PORTAL_LOOKUP_SET_WHITELIST:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Lookup set not allowed for portal: {set_key}",
        )
    from app.models.lookup import LookupBinding, LookupOption, LookupSet

    s = db.query(LookupSet).filter(LookupSet.set_key == set_key).first()
    if not s:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lookup set not found")
    opts = (
        db.query(LookupOption)
        .filter(LookupOption.set_id == s.id, LookupOption.is_active.is_(True))
        .order_by(LookupOption.sort_order)
        .all()
    )
    binding = db.query(LookupBinding).filter(LookupBinding.set_id == s.id).first()
    return LookupSetResponse(
        options=[LookupSetOption(value=o.value, label=o.label) for o in opts],
        default_value=getattr(binding, "default_value", None) if binding else None,
    )


class RequestorOption(BaseModel):
    id: str
    name: str


class RequestorOptionsResponse(BaseModel):
    items: list[RequestorOption]
    has_more: bool


@router.get("/requestor-options", response_model=RequestorOptionsResponse)
def portal_requestor_options(
    q: Optional[str] = Query(None, description="Case-insensitive substring match on name"),
    token: PortalToken = Depends(get_portal_token),
    db: Session = Depends(get_db),
):
    """Names-only eligible set for the "Requested by" / "Salesperson" picker
    (PLAN-requested-by-contact-routing.md D3/D5/D6). Always includes the
    token's own contact even when unsegmented, so nobody is ever blocked from
    submitting on their own behalf."""
    return PortalService(db).list_requestor_options(token, q=q)


# ---------- Submissions ----------


class SubmissionPayload(BaseModel):
    fields: dict
    products: Optional[list[dict]] = None  # purchase_request / sponsorship_form


def _flatten_payload(payload: SubmissionPayload) -> dict:
    body = dict(payload.fields or {})
    if payload.products is not None:
        body["products"] = payload.products
    return body


def _check_kind(kind: str, db: Session, token: PortalToken) -> str:
    k = (kind or "").strip().lower()
    if k not in SUPPORTED_TYPES:
        raise handle_validation_error(f"Unsupported submission type: {kind!r}.")
    require_form_visible(db, token.contact_id, k)
    return k


# Kinds the ATTACHMENT routes accept beyond SUPPORTED_TYPES. price_tag_request
# has its own dedicated submissions router (portal_price_tag.py, not
# PortalService's generic CRUD) but shares this generic attachments plumbing
# (D3) - so only the kind check and the ownership check below widen for it;
# `_check_kind` above stays exactly as it was for the generic submissions routes.
_ATTACHMENT_ONLY_KINDS = ("price_tag_request",)


def _check_revisable_kind(kind: str, db: Session, token: PortalToken) -> str:
    """Kinds the REVISION routes accept - every registered RevisionAdapter.
    As of R3-1 that is SUPPORTED_TYPES' three original kinds plus
    price_tag_request (own dedicated router, sharing this generic revision
    plumbing the same way the attachment routes already do above)."""
    from app.services.portal_revision_service import ADAPTERS

    k = (kind or "").strip().lower()
    if k not in SUPPORTED_TYPES and k not in ADAPTERS:
        raise handle_validation_error(f"Unsupported submission type: {kind!r}.")
    require_form_visible(db, token.contact_id, k)
    return k


def _require_revisable_ownership(
    db: Session, token: PortalToken, kind: str, submission_id: str
) -> None:
    """Ownership check for a revision route, dispatched the same way the
    attachment routes already widen for price_tag_request: its own dedicated
    router's ownership check, not PortalService's generic CRUD."""
    if kind == "price_tag_request":
        from app.api.v1.public.portal_price_tag import _require_own_request

        _require_own_request(db, token, submission_id)
    else:
        PortalService(db).get_submission(token, kind, submission_id)


def _revision_submission_detail(
    db: Session, token: PortalToken, kind: str, submission_id: str
) -> dict:
    """The submission body a revision route hands back, dispatched the same
    way ownership is: price_tag_request's own detail body (already carries
    attachments, revision, revision_draft), the generic one otherwise."""
    if kind == "price_tag_request":
        from app.api.v1.public.portal_price_tag import _detail_body, _require_own_request

        row = _require_own_request(db, token, submission_id)
        return _detail_body(db, row)
    detail = PortalService(db).get_submission(token, kind, submission_id)
    detail["attachments"] = _list_attachments_for(db, _entity_type_for(kind), submission_id)
    return detail


def _check_attachment_kind(kind: str, db: Session, token: PortalToken) -> str:
    k = (kind or "").strip().lower()
    if k not in SUPPORTED_TYPES and k not in _ATTACHMENT_ONLY_KINDS:
        raise handle_validation_error(f"Unsupported submission type: {kind!r}.")
    require_form_visible(db, token.contact_id, k)
    return k


def _require_own_price_tag_request(
    db: Session,
    token: PortalToken,
    submission_id: str,
    *,
    require_editable: bool = False,
    check_visibility: bool = True,
) -> None:
    """The contact's own price tag request, or a 404 - mirrors
    ``_require_own_request`` in portal_price_tag.py. The attachment routes check
    ownership here instead of ``PortalService.get_submission``, which does not
    know about price_tag_request.

    Validates the id is a UUID before it reaches the query: ``PriceTagRequest
    .id`` is a UUID column, and a malformed value (not just a wrong-but-valid
    one) has to answer the same 404 a genuinely missing row would, not a 500
    from Postgres refusing to compare a UUID column to garbage.

    ``require_editable``: the two ATTACHMENT WRITES (upload, delete) pass this
    so a locked request (review round 2) refuses them with the same 409
    ``_require_editable`` gives the header PUT - reading attachments (list,
    download) does not, since viewing a locked request's files is still fine.

    ``check_visibility=False``: the row-derived attachment lookup
    (``_attachment_is_on_own_submission``, AC-G2) tries this kind speculatively
    inside a "not owned, try the next candidate" catch that would otherwise
    swallow the 403 this raises - it does its own visibility check once
    ownership is confirmed, outside that catch, instead.
    """
    from app.models.price_tag import PriceTagRequest

    submission_id = validate_uuid_path(submission_id, resource="Price tag request")
    if check_visibility:
        require_form_visible(db, token.contact_id, "price_tag_request")
    row = db.query(PriceTagRequest).filter(PriceTagRequest.id == submission_id).first()
    if row is None or str(row.contact_id) != str(token.contact_id):
        raise handle_not_found("Price tag request", submission_id)
    if require_editable:
        from app.api.v1.public.portal_price_tag import _require_editable

        _require_editable(row, db)


@router.get("/submissions")
def portal_list_submissions(
    type: str = Query(...),
    q: Optional[str] = Query(None, description="Free-text search across all fields"),
    token: PortalToken = Depends(get_portal_token),
    db: Session = Depends(get_db),
):
    return {
        "items": PortalService(db).list_submissions(token, _check_kind(type, db, token), q=q),
    }


class RevisionPolicyBlock(BaseModel):
    """Everything the portal needs to render (or not render) the Revise action.

    ``used`` is the submission's current ``revision_no`` - the value the FE sends back
    as ``expected_revision_no`` so a double tap cannot produce two revisions.
    """

    enabled: bool
    allowed: bool
    used: int
    max: int
    remaining: int
    blocked_reason: Optional[str] = None
    # Where a revision sends the form back to, in words (UAC E1/E1a). Derived from
    # the type's config so the confirm dialog never hardcodes "the purchasing team"
    # on a type that does not route to purchasing. NULL = nothing to name, and the
    # copy falls back to the generic sentence.
    restart_stage_label: Optional[str] = None


class RevisionEntry(BaseModel):
    id: str
    version_no: int
    revision_no: int
    kind: str
    label: str
    reason: Optional[str] = None
    submitted_at: Optional[str] = None
    submitted_by: Optional[str] = None
    is_reconstructed: bool = False
    snapshot: dict = {}
    attachments: list[dict] = []
    # Stage output this revision invalidated (e.g. the superseded purchasing
    # response), kept so history shows the answer beside the version it answered.
    invalidated: Optional[dict] = None
    # The PRIMARY (newest) voided stage. Every existing timeline renders these two.
    voided_stage_code: Optional[str] = None
    voided_assignee_name: Optional[str] = None
    # EVERY stage this revision voided, newest first: {stage_code, assignee_name}.
    # A form can sit with two stages open at once, and the revision stops both and
    # tells both handlers, so history must not name only one. Declared here or the
    # response_model would silently drop what the service built.
    voided_stages: list[dict] = []
    # {field, label, from, to} - `from` is a Python keyword, so this stays a plain
    # dict rather than a model that would have to alias around it.
    changes: list[dict] = []
    # {field, label, value, display} - the WHOLE form at this version, labeled and
    # ordered by the adapter, backing the read-only full-form view (UAC G9).
    # `display` is the server-rendered presentation of `value` (a lookup option's
    # label, a DD/MM/YYYY date) or null when the raw value already reads correctly,
    # so both surfaces show the same string. Declared here or the response_model
    # drops it, exactly as with `voided_stages` above.
    snapshot_fields: list[dict] = []


class RevisionListResponse(BaseModel):
    items: list[RevisionEntry]


class RevisePayload(BaseModel):
    """The submit payload plus the two things only a revision carries."""

    # Length is NOT constrained here: the service is the single validator, so a
    # blank reason reads as the same shared sentence ("Tell us what changed and
    # why.") whether it arrived as "" or as whitespace, rather than as a pydantic
    # envelope for one of the two.
    reason: str = Field(...)
    # The revision_no the contact was looking at. A mismatch is a 409 (UAC C5).
    expected_revision_no: int = Field(..., ge=0)
    fields: dict = {}
    products: Optional[list[dict]] = None


class ReviseResponse(BaseModel):
    submission: dict
    revision: RevisionPolicyBlock
    revision_no: int


def _revision_policy_block(db: Session, kind: str, submission_id: str) -> dict:
    """Policy block for a submission id. Fails closed (disabled) for a type with no
    adapter or no config row, so the portal simply renders no Revise action."""
    from app.services.portal_revision_service import PortalRevisionService

    return PortalRevisionService(db).policy_for(kind, submission_id).as_dict()


@router.get("/submissions/{kind}/{submission_id}")
def portal_get_submission(
    kind: str = Path(...),
    submission_id: str = Path(...),
    token: PortalToken = Depends(get_portal_token),
    db: Session = Depends(get_db),
):
    k = _check_kind(kind, db, token)
    detail = PortalService(db).get_submission(token, k, submission_id)
    detail["attachments"] = _list_attachments_for(db, _entity_type_for(kind), submission_id)
    # One call, no extra round trip (UAC B1).
    detail["revision"] = _revision_policy_block(db, k, submission_id)
    from app.services.portal_revision_service import PortalRevisionService

    detail["revision_draft"] = PortalRevisionService(db).get_draft(k, submission_id)
    return detail


@router.get(
    "/submissions/{kind}/{submission_id}/revisions",
    response_model=RevisionListResponse,
)
def portal_list_revisions(
    kind: str = Path(...),
    submission_id: str = Path(...),
    token: PortalToken = Depends(get_portal_token),
    db: Session = Depends(get_db),
):
    """The original plus every version since, each with what changed (UAC G).

    Read-only, ownership-checked through the same ``get_submission`` call the rest of
    the portal uses, so another contact's token gets the usual 404 / OWNER_MISMATCH.
    """
    from app.services.portal_revision_service import PortalRevisionService

    k = _check_revisable_kind(kind, db, token)
    _require_revisable_ownership(db, token, k, submission_id)
    return {"items": PortalRevisionService(db).list_revisions(k, submission_id)}


@router.post(
    "/submissions/{kind}/{submission_id}/revise",
    response_model=ReviseResponse,
)
def portal_revise_submission(
    payload: RevisePayload,
    kind: str = Path(...),
    submission_id: str = Path(...),
    token: PortalToken = Depends(get_portal_token),
    db: Session = Depends(get_db),
):
    """Send a revision: current work stops and the flow restarts (UAC F).

    409 when ``expected_revision_no`` is stale, 422 carrying one human sentence when
    the policy refuses it.
    """
    from app.services.portal_revision_service import PortalRevisionService

    k = _check_revisable_kind(kind, db, token)
    body = dict(payload.fields or {})
    if payload.products is not None:
        body["products"] = payload.products
    result = PortalRevisionService(db).revise(
        token,
        k,
        submission_id,
        body,
        payload.reason,
        payload.expected_revision_no,
    )
    submission = _revision_submission_detail(db, token, k, submission_id)
    submission["revision"] = result["policy"]
    return {
        "submission": submission,
        "revision": result["policy"],
        "revision_no": result["revision_no"],
    }


class ReviseDraftPayload(BaseModel):
    """A revision saved before Send revision - the reason is optional (a draft
    may be mid-thought) and there is no ``expected_revision_no`` gate here: Send
    revision re-checks staleness against the live submission, not the draft."""

    reason: Optional[str] = None
    base_revision_no: int = Field(..., ge=0)
    fields: dict = {}
    products: Optional[list[dict]] = None


@router.put("/submissions/{kind}/{submission_id}/revision-draft")
def portal_save_revision_draft(
    payload: ReviseDraftPayload,
    kind: str = Path(...),
    submission_id: str = Path(...),
    token: PortalToken = Depends(get_portal_token),
    db: Session = Depends(get_db),
):
    """Save (or update) an in-progress revision, without sending it."""
    from app.services.portal_revision_service import PortalRevisionService

    k = _check_revisable_kind(kind, db, token)
    body = dict(payload.fields or {})
    if payload.products is not None:
        body["products"] = payload.products
    return PortalRevisionService(db).save_draft(
        token,
        k,
        submission_id,
        body,
        payload.reason,
        payload.base_revision_no,
    )


@router.delete("/submissions/{kind}/{submission_id}/revision-draft")
def portal_discard_revision_draft(
    kind: str = Path(...),
    submission_id: str = Path(...),
    token: PortalToken = Depends(get_portal_token),
    db: Session = Depends(get_db),
):
    """Discard the in-progress revision, if any. Idempotent."""
    from app.services.portal_revision_service import PortalRevisionService

    k = _check_revisable_kind(kind, db, token)
    service = PortalRevisionService(db)
    service.fetch_owned(token, k, submission_id)  # ownership: 403/404 as elsewhere
    service.discard_draft(k, submission_id)
    return {"ok": True}


class SubmissionNeighboursResponse(BaseModel):
    prev_id: Optional[str] = None
    next_id: Optional[str] = None
    position: int
    total: int


@router.get(
    "/submissions/{kind}/{submission_id}/neighbours",
    response_model=SubmissionNeighboursResponse,
)
def portal_submission_neighbours(
    kind: str = Path(...),
    submission_id: str = Path(...),
    token: PortalToken = Depends(get_portal_token),
    db: Session = Depends(get_db),
):
    """Prev/next over the contact's OWN submissions of the same kind (UAC G1/G2).

    ``_check_revisable_kind`` (not ``_check_kind``): review round 3 - the
    price tag header wants the same counter the legacy kinds' view page
    shows, so this route widens the same way the revision routes already do
    for that kind, dispatching to its own dedicated router's neighbours
    query rather than ``PortalService``, which does not know about it.
    """
    k = _check_revisable_kind(kind, db, token)
    if k == "price_tag_request":
        from app.api.v1.public.portal_price_tag import price_tag_neighbours

        return price_tag_neighbours(db, token, submission_id)
    return PortalService(db).get_neighbours(token, k, submission_id)


@router.post("/submissions/{kind}")
def portal_create_draft(
    payload: SubmissionPayload,
    kind: str = Path(...),
    token: PortalToken = Depends(get_portal_token),
    db: Session = Depends(get_db),
):
    return PortalService(db).create_or_update_draft(
        token, _check_kind(kind, db, token), _flatten_payload(payload)
    )


@router.put("/submissions/{kind}/{submission_id}")
def portal_update_draft(
    payload: SubmissionPayload,
    kind: str = Path(...),
    submission_id: str = Path(...),
    token: PortalToken = Depends(get_portal_token),
    db: Session = Depends(get_db),
):
    return PortalService(db).create_or_update_draft(
        token, _check_kind(kind, db, token), _flatten_payload(payload), submission_id
    )


@router.delete("/submissions/{kind}/{submission_id}", status_code=204)
def portal_delete_draft(
    kind: str = Path(...),
    submission_id: str = Path(...),
    token: PortalToken = Depends(get_portal_token),
    db: Session = Depends(get_db),
):
    PortalService(db).delete_draft(token, _check_kind(kind, db, token), submission_id)
    return None


@router.post("/submissions/{kind}/{submission_id}/submit")
def portal_submit(
    kind: str = Path(...),
    submission_id: str = Path(...),
    payload: Optional[SubmissionPayload] = Body(default=None),
    token: PortalToken = Depends(get_portal_token),
    db: Session = Depends(get_db),
):
    body = _flatten_payload(payload) if payload else None
    return PortalService(db).submit_draft(
        token, _check_kind(kind, db, token), submission_id, body
    )


# ---------- Attachments ----------


def _entity_type_for(kind: str) -> str:
    """Sponsorship form shares the purchase_request entity_type for attachments."""
    return "purchase_request" if kind == "sponsorship_form" else kind


def _kinds_for_entity_type(entity_type: str) -> tuple[str, ...]:
    """Reverse of :func:`_entity_type_for` - the portal kinds an attachment link
    could belong to. `purchase_request` is ambiguous (PR and sponsorship form
    share the entity type), so both are tried."""
    et = (entity_type or "").strip().lower()
    if et == "purchase_request":
        return ("purchase_request", "sponsorship_form")
    if et in _ATTACHMENT_ONLY_KINDS:
        return (et,)
    return (et,) if et in SUPPORTED_TYPES else ()


def _ext(filename: Optional[str], content_type: Optional[str]) -> str:
    if filename and "." in filename:
        return filename.rsplit(".", 1)[-1].lower().strip()
    if content_type and "/" in content_type:
        return content_type.split("/", 1)[1].split(";", 1)[0].strip().lower()
    return ""


# attachments.mime_type is VARCHAR(100). `file.content_type` is whatever the
# UPLOADER's browser/OS sends - Content-Type can carry `; charset=...` /
# `; boundary=...` / `; name="..."` parameters, or simply be malformed, and
# nothing here controls its length. Postgres raises on overflow rather than
# truncating, so an oversized value 500s every kind's upload, not just
# price_tag_request's.
_MIME_TYPE_MAX_LEN = 100


def _normalize_mime_type(content_type: Optional[str]) -> Optional[str]:
    """Just `type/subtype`, capped to the column width. Never raises."""
    if not content_type:
        return None
    base = content_type.split(";", 1)[0].strip()
    if not base:
        return None
    return base[:_MIME_TYPE_MAX_LEN]


def _check_quota(
    db: Session,
    attachment_type: AttachmentType,
    entity_type: str,
    entity_id: str,
    incoming_size: int,
    incoming_ext: str,
) -> None:
    allowed_exts = {
        e.strip().lower().lstrip(".")
        for e in (attachment_type.allowed_extensions or "").split(",")
        if e.strip()
    }
    if allowed_exts and incoming_ext not in allowed_exts:
        raise handle_validation_error(
            f"Unsupported file type. Allowed: {', '.join(sorted(allowed_exts))}."
        )
    max_bytes = (attachment_type.max_file_size_mb or 0) * 1024 * 1024
    if max_bytes and incoming_size > max_bytes:
        raise handle_validation_error(
            f"File exceeds {attachment_type.max_file_size_mb} MB limit."
        )
    if attachment_type.max_count_per_entity is not None:
        existing = (
            db.query(EntityAttachmentLink)
            .filter(
                EntityAttachmentLink.entity_type == entity_type,
                EntityAttachmentLink.entity_id == entity_id,
            )
            .count()
        )
        if existing >= attachment_type.max_count_per_entity:
            raise handle_validation_error(
                f"Attachment limit reached ({attachment_type.max_count_per_entity})."
            )


def _safe_presigned_url(
    file_path: Optional[str],
    provider: Optional[str] = None,
) -> Optional[str]:
    """Sign portal attachment URLs against the row's provider (s3 or r2)."""
    if not file_path:
        return None
    try:
        from app.services.storage_router import resolve_signed_url

        return resolve_signed_url(file_path, provider=provider)
    except Exception as e:  # noqa: BLE001
        logger.warning("Portal presigned URL failed for %s: %s", file_path, e)
        return None


@router.get("/attachments")
def portal_list_attachments(
    kind: str = Query(...),
    submission_id: str = Query(...),
    token: PortalToken = Depends(get_portal_token),
    db: Session = Depends(get_db),
):
    k = _check_attachment_kind(kind, db, token)
    # Ensures the contact owns this submission.
    if k == "price_tag_request":
        _require_own_price_tag_request(db, token, submission_id)
    else:
        PortalService(db).get_submission(token, k, submission_id)
    return {"items": _list_attachments_for(db, _entity_type_for(k), submission_id)}


def _attachment_is_on_own_submission(
    db: Session, token: PortalToken, attachment_id: str
) -> bool:
    """True when this attachment is linked to a submission the contact owns."""
    portal = PortalService(db)
    links = (
        db.query(EntityAttachmentLink)
        .filter(EntityAttachmentLink.attachment_id == attachment_id)
        .all()
    )
    for link in links:
        entity_type, entity_id = link.entity_type, link.entity_id
        kinds: tuple[str, ...]
        if entity_type == "form_comment":
            # A comment file is linked to its comment only; the comment names the form.
            from app.models.entity_conversation import EntityConversationMessage

            msg = (
                db.query(EntityConversationMessage)
                .filter(
                    EntityConversationMessage.id == entity_id,
                    EntityConversationMessage.scope == "form_comment",
                )
                .first()
            )
            if msg is None:
                continue
            kinds, entity_id = (msg.entity_type,), msg.entity_id
        else:
            kinds = _kinds_for_entity_type(entity_type)
        for kind in kinds:
            try:
                if kind == "price_tag_request":
                    _require_own_price_tag_request(
                        db, token, entity_id, check_visibility=False
                    )
                else:
                    portal.get_submission(token, kind, entity_id)
            except HTTPException:
                # Not this contact's (404 / OWNER_MISMATCH) - try the next link.
                continue
            # Ownership confirmed for `kind` - AC-G2: the visibility gate raises
            # its OWN 403 here, outside the catch above, so a hidden kind reads
            # as "not visible" rather than being swallowed into "not owned".
            require_form_visible(db, token.contact_id, kind)
            return True
    return False


def _attachment_is_in_own_revision_history(
    db: Session, token: PortalToken, attachment_id: str
) -> bool:
    """True when the attachment appears in a revision snapshot of a submission the
    contact owns.

    This is the clause that keeps history previewable: a file dropped during a
    revision is unlinked (UAC G6), so it has no ``EntityAttachmentLink`` left and the
    live-link check above would 404 on exactly the historical files the history exists
    to show (UAC I2a).
    """
    from app.models.portal import PortalFormRevision
    from app.services.portal_revision_service import PortalRevisionService

    rows = (
        db.query(PortalFormRevision.source_entity_type, PortalFormRevision.source_entity_id)
        .filter(
            PortalFormRevision.attachments_json.contains(
                [{"attachment_id": str(attachment_id)}]
            )
        )
        .distinct()
        .all()
    )
    if not rows:
        return False
    portal = PortalService(db)
    service = PortalRevisionService(db)
    for entity_type, entity_id in rows:
        # A revision row stores the portal KIND (sponsorship_form stays itself), so
        # unlike an attachment link there is nothing ambiguous to resolve here.
        if entity_type not in SUPPORTED_TYPES:
            continue
        try:
            portal.get_submission(token, entity_type, str(entity_id))
        except HTTPException:
            continue  # not this contact's - try the next snapshot
        if str(attachment_id) in service.attachment_ids_in_history(
            entity_type, str(entity_id)
        ):
            # Ownership confirmed - same gate as the live-link path (AC-G2).
            require_form_visible(db, token.contact_id, entity_type)
            return True
    return False


def _portal_can_read_attachment(
    db: Session, token: PortalToken, attachment_id: str
) -> bool:
    """Authorisation for the portal bytes route: token -> contact owns a
    submission -> the attachment belongs to it, now or in one of its revisions.

    Single gate on purpose, so the preview, the download and the history all agree.
    """
    return _attachment_is_on_own_submission(
        db, token, attachment_id
    ) or _attachment_is_in_own_revision_history(db, token, attachment_id)


@router.get("/attachments/{attachment_id}/download")
def portal_download_attachment(
    attachment_id: str = Path(...),
    token: PortalToken = Depends(get_portal_token),
    db: Session = Depends(get_db),
):
    """Attachment bytes for the contact portal, authenticated by the portal token.

    Keyed on ``attachment_id``, NOT ``link_id``: an attachment removed during a
    revision is unlinked but stays visible in that revision's history, and a
    link-keyed route would 404 on exactly those historical files.

    The portal has no NextAuth JWT session, so the office
    ``/resource-management/attachments/{id}/download`` route 401s there. This
    route backs both the in-place preview (Excel bytes) and the Download button.
    """
    attachment_id = validate_uuid_path(attachment_id, resource="Attachment")
    # Ownership first: a 404 for "exists but not yours" and for "does not exist"
    # alike, so the route never confirms an id the contact has no claim on.
    if not _portal_can_read_attachment(db, token, attachment_id):
        raise handle_not_found("Attachment", attachment_id)

    attachment = db.query(Attachment).filter(Attachment.id == attachment_id).first()
    if attachment is None:
        raise handle_not_found("Attachment", attachment_id)

    from app.services.resources_service import AttachmentService

    try:
        content = AttachmentService(db).get_file_content(attachment_id)
    except HTTPException:
        # An AppException from the service (e.g. 404) is already the right
        # answer - don't relabel it as a storage failure.
        raise
    except Exception as e:  # noqa: BLE001
        logger.warning("Portal attachment download failed for %s: %s", attachment_id, e)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="File download failed. Please try again.",
        ) from e

    filename = attachment.original_filename or attachment.stored_filename or "attachment"
    return Response(
        content=content,
        media_type=str(getattr(attachment, "mime_type", None) or "application/octet-stream"),
        headers={
            "Content-Disposition": content_disposition(filename),
            "Content-Length": str(len(content)),
        },
    )


@router.post("/attachments")
async def portal_upload_attachment(
    kind: Annotated[str, Form()],
    submission_id: Annotated[str, Form()],
    file: Annotated[UploadFile, File(...)],
    token: PortalToken = Depends(get_portal_token),
    db: Session = Depends(get_db),
):
    portal = PortalService(db)
    k = _check_attachment_kind(kind, db, token)
    if k == "price_tag_request":
        _require_own_price_tag_request(db, token, submission_id, require_editable=True)
    else:
        portal.get_submission(token, k, submission_id)  # ownership check
    attachment_type = portal.get_portal_attachment_type()

    contents = await file.read()
    incoming_size = len(contents)
    extension = _ext(file.filename, file.content_type)
    _check_quota(
        db,
        attachment_type,
        _entity_type_for(k),
        submission_id,
        incoming_size,
        extension,
    )

    safe_ext = f".{extension}" if extension else ""
    s3_key = f"portal/{token.contact_id}/{uuid.uuid4()}{safe_ext}"
    from app.services.storage_router import default_provider, get_backend

    portal_provider = default_provider()
    portal_backend = get_backend(portal_provider)
    try:
        portal_backend.upload_file(
            contents, s3_key, content_type=file.content_type
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("Portal attachment upload failed (provider=%s): %s", portal_provider, e)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="File upload failed. Please try again.",
        ) from e

    # Grid thumbnail (images only) - portal uploads are device bytes in our own
    # bucket, so the same small-variant path applies. Best-effort; never blocks.
    from app.services.image_thumbnailer import store_thumbnail

    portal_thumbnail = store_thumbnail(
        portal_backend, portal_provider, s3_key, contents, file.content_type
    )

    service = EntityAttachmentService(db)
    link = service.create_attachment_and_link(
        entity_type=_entity_type_for(k),
        entity_id=submission_id,
        file_url=s3_key,
        file_name=file.filename or os.path.basename(s3_key),
        file_size_bytes=incoming_size,
        attachment_type_code=PORTAL_ATTACHMENT_TYPE_CODE,
        created_by=None,
        thumbnail_path=portal_thumbnail,
        storage_provider=portal_provider,
        # Pre-existing gap, not price-tag-specific: this call never passed the
        # browser's content type through, so every portal upload's attachment
        # row (every kind, not just price_tag_request) carried mime_type=None
        # and every reader of it - the PO cross-check PDF/image branch
        # included - always fell to the generic file row. AC-S1-5 needs a real
        # content_type, so it is fixed here rather than worked around per kind.
        # Normalized (type/subtype, capped) - the raw header is caller-
        # controlled and the column is VARCHAR(100).
        mime_type=_normalize_mime_type(file.content_type),
    )
    # Uploader attribution (UAC B1): create_attachment_and_link has no fields
    # for this, so stamp the freshly created row directly, in the same
    # transaction as the link, before commit.
    attachment = db.query(Attachment).filter(Attachment.id == link.attachment_id).first()
    if attachment is not None:
        attachment.uploader_kind = "contact"
        attachment.uploaded_by_contact_id = token.contact_id
    db.commit()
    db.refresh(link)
    if attachment is not None:
        db.refresh(attachment)
    file_path = attachment.file_path if attachment else s3_key
    return {
        "link_id": str(link.id),
        "attachment_id": str(link.attachment_id),
        "filename": attachment.original_filename if attachment else file.filename,
        "size": incoming_size,
        "url": _safe_presigned_url(file_path) or file_path,
        "content_type": (attachment.mime_type if attachment else file.content_type) or None,
    }


_COMMENT_RATE_WINDOW_SECONDS = 900
_COMMENT_TOKEN_LIMIT = 10
_COMMENT_GLOBAL_LIMIT = 300


@router.get("/submissions/{kind}/{submission_id}/comments")
def portal_list_comments(
    kind: str,
    submission_id: str,
    token: PortalToken = Depends(get_portal_token),
    db: Session = Depends(get_db),
):
    from app.services.form_comment_service import FormCommentService

    k = _check_kind(kind, db, token)
    sid = validate_uuid_path(submission_id, resource="Submission")
    svc = FormCommentService(db)
    # 404 unless the contact owns the row (list_comments below has no contact filter)
    row = svc._load_row(k, sid, contact_id=token.contact_id, space_id=token.space_id)
    return {"items": svc.list_comments(k, sid, {"contact_id": token.contact_id}, row=row)}


@router.post("/submissions/{kind}/{submission_id}/comments", status_code=status.HTTP_201_CREATED)
async def portal_add_comment(
    kind: str,
    submission_id: str,
    body: Annotated[str, Form()] = "",
    files: Annotated[Optional[list[UploadFile]], File()] = None,
    parent_id: Annotated[Optional[str], Form()] = None,
    token: PortalToken = Depends(get_portal_token),
    db: Session = Depends(get_db),
):
    import hashlib

    from app.api.v1.form_comments import read_uploads
    from app.services import rate_limit
    from app.services.form_comment_service import FormCommentService, resolve_impersonating_admin

    acting_user_id = (
        resolve_impersonating_admin(db, token) if getattr(token, "is_impersonation", False) else None
    )
    k = _check_kind(kind, db, token)
    sid = validate_uuid_path(submission_id, resource="Submission")
    # Per token plus one ceiling across tokens: checked first, charged only on a saved comment.
    buckets = (
        ("form_comment_token", hashlib.sha256(str(token.id).encode()).hexdigest(), _COMMENT_TOKEN_LIMIT),
        ("form_comment_global", "all", _COMMENT_GLOBAL_LIMIT),
    )
    for bucket, ident, limit in buckets:
        result = rate_limit.peek(bucket, ident, limit=limit, window_seconds=_COMMENT_RATE_WINDOW_SECONDS)
        if not result.allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many comments. Try again later.",
                headers={"Retry-After": str(result.retry_after_seconds or _COMMENT_RATE_WINDOW_SECONDS)},
            )
    uploads = await read_uploads(files)
    out = FormCommentService(db).add_contact_comment(
        k, sid, token.contact_id, token.space_id, body, uploads, acting_user_id, parent_id or None
    )
    for bucket, ident, _limit in buckets:
        rate_limit.record(bucket, ident, window_seconds=_COMMENT_RATE_WINDOW_SECONDS)
    return out


class _EditCommentBody(BaseModel):
    body: str = ""


@router.patch("/submissions/{kind}/{submission_id}/comments/{comment_id}")
def portal_edit_comment(
    kind: str,
    submission_id: str,
    comment_id: str,
    payload: _EditCommentBody,
    token: PortalToken = Depends(get_portal_token),
    db: Session = Depends(get_db),
):
    from app.services.form_comment_service import FormCommentService, resolve_impersonating_admin

    if getattr(token, "is_impersonation", False):
        resolve_impersonating_admin(db, token)
    k = _check_kind(kind, db, token)
    sid = validate_uuid_path(submission_id, resource="Submission")
    svc = FormCommentService(db)
    row = svc._load_row(k, sid, contact_id=token.contact_id, space_id=token.space_id)
    return {"comment": svc.edit_comment(k, sid, comment_id, {"contact_id": token.contact_id}, payload.body, row=row)}


@router.delete("/submissions/{kind}/{submission_id}/comments/{comment_id}")
def portal_delete_comment(
    kind: str,
    submission_id: str,
    comment_id: str,
    token: PortalToken = Depends(get_portal_token),
    db: Session = Depends(get_db),
):
    from app.services.form_comment_service import FormCommentService, resolve_impersonating_admin

    if getattr(token, "is_impersonation", False):
        resolve_impersonating_admin(db, token)
    k = _check_kind(kind, db, token)
    sid = validate_uuid_path(submission_id, resource="Submission")
    svc = FormCommentService(db)
    row = svc._load_row(k, sid, contact_id=token.contact_id, space_id=token.space_id)
    svc.delete_comment(k, sid, comment_id, {"contact_id": token.contact_id}, row=row)
    return {"deleted": True}


@router.delete("/attachments/{link_id}")
def portal_delete_attachment(
    link_id: str = Path(...),
    token: PortalToken = Depends(get_portal_token),
    db: Session = Depends(get_db),
):
    link = (
        db.query(EntityAttachmentLink)
        .filter(EntityAttachmentLink.id == link_id)
        .first()
    )
    if not link:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attachment not found.")

    portal = PortalService(db)
    raw_kind = link.entity_type
    if raw_kind == "form_comment":
        # Comment files belong to the thread; a contact cannot remove them.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attachment not found.")
    # sponsorship_form attachments live under entity_type=purchase_request; verify ownership against either type.
    if raw_kind == "purchase_request":
        owns = False
        owned_kind = None
        for k in ("purchase_request", "sponsorship_form"):
            try:
                portal.get_submission(token, k, link.entity_id)
                owns = True
                owned_kind = k
                break
            except HTTPException:
                continue
        if not owns:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attachment not found.")
        # AC-G2: ownership resolved which kind this is - the gate raises its
        # own 403 here rather than folding into the "not owned" 404 above.
        require_form_visible(db, token.contact_id, owned_kind)
    elif raw_kind == "price_tag_request":
        # SEC5: ownership first (same ordering the other two arms use), the
        # visibility gate after - so a link that is not this contact's own
        # 404s before it ever reveals whether the kind itself is hidden.
        _require_own_price_tag_request(
            db, token, link.entity_id, require_editable=True, check_visibility=False
        )
        require_form_visible(db, token.contact_id, "price_tag_request")
    else:
        portal.get_submission(token, raw_kind, link.entity_id)
        require_form_visible(db, token.contact_id, raw_kind)

    # UAC F2 (hard blocker): a staff-uploaded attachment cannot be unlinked from
    # the portal, even by a contact who owns the submission. FE gating alone is
    # not a control on a token surface - enforce it server-side here too.
    attachment = db.query(Attachment).filter(Attachment.id == link.attachment_id).first()
    if attachment is not None and attachment.uploader_kind == "user":
        raise AppException(
            status_code=status.HTTP_403_FORBIDDEN,
            message="This file was added by our team and cannot be removed here.",
            code="STAFF_UPLOAD_LOCKED",
        )

    EntityAttachmentService(db).delete_link(link_id)
    db.commit()
    return {"deleted": True}
