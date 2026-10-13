"""Marketing service for business logic."""
# ORM models declare Column[T] on the class; at runtime instance attributes are Python values.
# Pyright reports false positives here until models use SQLAlchemy 2.0 Mapped[] typing.
# pyright: reportAttributeAccessIssue=false
# pyright: reportGeneralTypeIssues=false
# pyright: reportArgumentType=false
# pyright: reportCallIssue=false
# pyright: reportReturnType=false
# pyright: reportOptionalMemberAccess=false
import math
import re
import uuid
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo
from sqlalchemy.orm import Session
from sqlalchemy import and_, cast, func, or_, exists, select, text, String
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.exc import IntegrityError
from typing import Any, Optional
from decimal import Decimal

from app.services import promotion_serving, promotion_window
from app.models.marketing import (
    Promotion,
    PromotionGroup,
    PromotionProduct,
    PromotionAttachment,
    PromotionType,
    CampaignType,
    MarketingCampaign,
)
from app.models.product import Product, ProductCategory, Brand
from app.models.resources import Attachment, AttachmentType
from app.schemas.marketing import (
    PromotionCreate,
    PromotionUpdate,
    PromotionGroupCreate,
    PromotionGroupUpdate,
    PromotionProductCreate,
    PromotionProductUpdate,
    PromotionAttachmentCreate,
    PromotionAttachmentUpdate,
    CampaignTypeCreate,
    CampaignTypeUpdate,
    MarketingCampaignCreate,
    MarketingCampaignUpdate,
)
from app.services.error_handler import handle_not_found, handle_conflict, handle_internal_error, handle_validation_error
from app.services.contact_access_type_service import ContactAccessTypeService
from app.services.company_scope import stamp_lookup_companies
from app.services.embedding_events import publish_embedding_event
from app.services.identifier_resolver import resolve_identifier
from app.services.uuid_path_param import validate_uuid_path

_MY_TZ = ZoneInfo("Asia/Kuala_Lumpur")


def _promotion_stored_boundary_date(dt: datetime | date) -> date:
    """Calendar day for a promotion boundary (DATE column, or legacy datetime)."""
    if isinstance(dt, date) and not isinstance(dt, datetime):
        return dt
    if isinstance(dt, datetime):
        aware = dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
        return aware.astimezone(_MY_TZ).date()
    raise TypeError(f"Expected date or datetime, got {type(dt)}")


def _promotion_active_clause(today: date):
    """SQLAlchemy clause: parent Promotion is currently active.

    Active = ``is_active`` on, AND today inside whatever bounds are set, with a
    blank bound meaning unbounded on that side.

    Delegated so this module and the Dealer Kit's pricing cannot answer one
    commercial question two ways. They used to: this clause accepted only a
    promotion with NO dates or one whose start AND end bracket today, so an offer
    with a start date and a blank end was never active here while being live for
    pricing. See ``app/services/promotion_window``.
    """
    return promotion_window.live_clause(today)


def _promotion_is_expired(promotion, today: date) -> bool:
    """Python mirror of `_promotion_active_clause`: True when NOT currently live.

    Live = is_active flag on AND (no date window at all OR today inside it).
    Anything else (flag off, or today outside [start_date, end_date]) is expired,
    so callers (n8n) can answer "found but expired" for fallback / historical rows.
    Same definition the promotions list uses for its row-level `is_expired`.
    """
    if promotion is None:
        return True
    return not promotion_window.is_live(
        promotion.is_active, promotion.start_date, promotion.end_date, today
    )


def _promotion_type_labels(db: Session, type_ids) -> dict[str, tuple]:
    """`{type_id: (type_code, type_name)}` for the ids on one page, in one query.

    The relationship would answer this too, but lazily and once per row: a 50-row
    page of promotions is 50 extra round trips to fetch at most five distinct
    types. Same batching the attachments and product counts already do.
    """
    ids = [str(i) for i in dict.fromkeys(i for i in type_ids if i)]
    if not ids:
        return {}
    rows = (
        db.query(PromotionType.id, PromotionType.type_code, PromotionType.type_name)
        .filter(PromotionType.id.in_(ids))
        .all()
    )
    return {str(type_id): (code, name) for type_id, code, name in rows}


def _default_type_labels(db: Session) -> tuple:
    """`(code, name)` of the default type, or `(None, None)` when none is flagged.

    An untyped promotion is not type-less as far as serving goes: it is served
    under the DEFAULT type's rules (D3). Reporting a blank type for a row the
    policy just honoured as `standard` contradicts the same payload's own
    answer, so the display follows the policy rather than the raw column.
    """
    row = (
        db.query(PromotionType.type_code, PromotionType.type_name)
        .filter(PromotionType.is_default.is_(True))
        .first()
    )
    return (row[0], row[1]) if row else (None, None)


def _type_labels_for(raw_type_id, labels: dict, default_labels: tuple) -> tuple:
    """Label pair for one row: its own type when it has one, else the default's."""
    if not raw_type_id:
        return default_labels
    return labels.get(str(raw_type_id), (None, None))


def _stamp_promotion_type_fields(db: Session, promotions, verdict=None) -> None:
    """Copy the type's code/name onto each row, and the expired-but-usable flag.

    The API never returns a bare `promotion_type_id` for display -- the UI rule is
    no UUIDs on screen, and the bot needs the code to phrase the answer. An
    untyped row reports the default type, which is the one that actually decided
    whether it was served.
    """
    labels = _promotion_type_labels(db, [p.promotion_type_id for p in promotions])
    default_labels = (
        _default_type_labels(db)
        if any(not p.promotion_type_id for p in promotions)
        else (None, None)
    )
    if verdict is None and promotions:
        # A detail read must answer the same question the list answered: "would
        # the bot still honour this?". Evaluating the single row against the
        # same policy keeps `expired_but_usable` truthful everywhere, so a
        # drill-down cannot contradict the list it came from.
        verdict = promotion_serving.evaluate_candidates(
            db, [p.id for p in promotions], datetime.utcnow().date()
        )
    for promotion in promotions:
        code, name = _type_labels_for(
            promotion.promotion_type_id, labels, default_labels
        )
        promotion.promotion_type_code = code
        promotion.promotion_type_name = name
        if verdict is not None:
            promotion.expired_but_usable = verdict.is_expired_but_usable(promotion.id)


def _resolve_promotion_id_for_filter(db: Session, raw: Optional[str]) -> Optional[str]:
    """Map API promotion id/path segments to ``Promotion.id`` (UUID string).

    Only UUIDs are accepted now that promo_code has been removed.
    Returns ``None`` if *raw* is blank or not a UUID.
    """
    if raw is None:
        return None
    s = raw.strip()
    if not s:
        return None
    try:
        return str(uuid.UUID(s))
    except ValueError:
        return None


def _product_display_label(db: Session, product_id: str) -> str:
    """Human-readable product label (code and name) for error messages."""
    p = db.query(Product).filter(Product.id == product_id).first()
    if not p:
        return product_id
    code = (p.product_code or "").strip()
    name = (p.product_name or "").strip()
    if code and name:
        return f"{code} - {name}"
    return code or name or product_id


def raise_promotion_product_unique_violation(db: Session, exc: Exception) -> None:
    """
    Turn promotion_products unique constraint failures into 409 with product details.
    Call after session.rollback() when handling IntegrityError from commit/flush.
    """
    if not isinstance(exc, IntegrityError):
        raise exc
    orig = str(getattr(exc, "orig", exc) or exc)
    if (
        "uq_promotion" not in orig.lower()
        and "promotion_products" not in orig.lower()
        and "group_product" not in orig.lower()
    ):
        raise handle_internal_error(
            "Could not save promotion product due to a database constraint."
        )
    m = re.search(
        r"Key \(promotion_group_id, product_id\)=\([^,]+,\s*([^)]+)\)", orig
    ) or re.search(r"Key \(promotion_id, product_id\)=\([^,]+,\s*([^)]+)\)", orig)
    if not m:
        raise handle_conflict(
            "This product is already linked to this promotion group, or the request contains duplicate products."
        )
    product_id = m.group(1).strip()
    label = _product_display_label(db, product_id)
    raise handle_conflict(
        f"Product already in this promotion group: {label}. Remove duplicate rows or use one line per product per group."
    )


# promotion_products.discount_percent is NUMERIC(5,2) → |value| must be < 10**3 (max ±999.99).
_MAX_DISCOUNT_PERCENT = 999.99


def _load_attachments_by_promotion_ids(
    db: Session, promotion_ids: list[str]
) -> dict[str, list[PromotionAttachment]]:
    """Eager-load promotion attachments for a batch of promotions, grouped by promotion_id.

    Shared between PromotionService (list/get) and PromotionProductService
    (line listings) so any tool that surfaces a promotion can carry the
    promotion document inline without a follow-up call.
    """
    from sqlalchemy.orm import joinedload as _joinedload

    result: dict[str, list[PromotionAttachment]] = {pid: [] for pid in promotion_ids}
    if not promotion_ids:
        return result
    rows = (
        db.query(PromotionAttachment)
        .options(
            _joinedload(PromotionAttachment.attachment).joinedload(Attachment.attachment_type)
        )
        .filter(PromotionAttachment.promotion_id.in_(promotion_ids))
        .order_by(
            PromotionAttachment.sort_order.asc().nulls_last(),
            PromotionAttachment.created_at.asc(),
        )
        .all()
    )
    for row in rows:
        result.setdefault(row.promotion_id, []).append(row)
    return result


def _tiers_from_create_data(data: PromotionGroupCreate) -> Optional[list[dict]]:
    """Build normalized foc_tiers JSON from create payload."""
    if not data.foc_tiers:
        return None
    out: list[dict] = []
    for t in data.foc_tiers:
        pq = int(t.purchase_quantity)
        fq = int(t.foc_quantity)
        if pq < 1:
            raise handle_conflict("FOC purchase quantity must be at least 1.")
        if fq < 0:
            raise handle_conflict("FOC free quantity cannot be negative.")
        out.append({"purchase_quantity": pq, "foc_quantity": fq})
    return out


def dealer_cost_and_margin_from_list(
    list_price: Optional[float],
    dealer_discount_percent: Optional[float],
) -> tuple[Optional[float], Optional[float]]:
    """
    dealer_discount_percent e.g. 0.37 => dealer pays (1-0.37) of list (dealer cost).
    Returns (dealer_cost, list_to_dealer_margin_amount).
    """
    if list_price is None or dealer_discount_percent is None:
        return None, None
    lp = float(list_price)
    dd = float(dealer_discount_percent)
    dealer_cost = lp * (1.0 - dd)
    margin = lp - dealer_cost
    return round(dealer_cost, 2), round(margin, 2)


def clamp_discount_percent_for_db(value: Optional[float]) -> Optional[float]:
    """Clamp discount % to DB range so bulk inserts do not raise NumericValueOutOfRange."""
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v):
        return None
    v = max(-_MAX_DISCOUNT_PERCENT, min(_MAX_DISCOUNT_PERCENT, v))
    return round(v, 2)


def _promotion_kind_clause(kind_codes: list[str]):
    """Promotions whose type (`promotion_types.type_code`) is one of `kind_codes`: the kind
    narrowing the promotions list and the promotion products list share."""
    from app.models.marketing import PromotionType

    return Promotion.promotion_type_id.in_(select(PromotionType.id).where(PromotionType.type_code.in_(kind_codes)))

class PromotionService:
    """Service for promotion operations."""
    
    def __init__(self, db: Session):
        self.db = db

    @staticmethod
    def _canonical_access_levels(access_levels: Optional[list[str]]) -> tuple[str, ...]:
        """Canonicalize access levels for uniqueness checks (order-insensitive, deduplicated)."""
        if not access_levels:
            return tuple()
        return tuple(sorted({(a or "").strip() for a in access_levels if (a or "").strip()}))

    def _load_attachments_for_promotion_ids(
        self, promotion_ids: list[str]
    ) -> dict[str, list[PromotionAttachment]]:
        return _load_attachments_by_promotion_ids(self.db, promotion_ids)

    def _load_product_counts_for_promotion_ids(
        self, promotion_ids: list[str]
    ) -> dict[str, int]:
        """Distinct product count per promotion, in ONE query for the whole page.

        Counting per promotion inside the serializer loop was an N+1: a 50-row
        page issued 50 round trips to fetch 50 integers. Promotions with no
        products are simply absent from the result, so callers default to 0.
        """
        if not promotion_ids:
            return {}
        rows = (
            self.db.query(
                PromotionProduct.promotion_id,
                func.count(func.distinct(PromotionProduct.product_id)),
            )
            .filter(PromotionProduct.promotion_id.in_(promotion_ids))
            .group_by(PromotionProduct.promotion_id)
            .all()
        )
        return {pid: count for pid, count in rows}

    @staticmethod
    def _filter_attachments_by_codes(
        attachments: list[PromotionAttachment],
        codes: Optional[list[str]],
    ) -> list[PromotionAttachment]:
        """Drop inline attachments whose underlying file's access_levels does not overlap ``codes``.

        ``codes is None`` means no per-attachment filter (FE/admin path). ``codes=[]``
        hides every attachment (contact has no assigned access types).
        """
        if codes is None:
            return attachments
        if not codes:
            return []
        code_set = set(codes)
        out: list[PromotionAttachment] = []
        for pa in attachments:
            att = getattr(pa, "attachment", None)
            levels = getattr(att, "access_levels", None) if att is not None else None
            if isinstance(levels, list) and code_set.intersection(levels):
                out.append(pa)
        return out

    def _build_promotions_ordered_query(
        self,
        *,
        user_type: Optional[str] = None,
        contact_access_codes: Optional[list[str]] = None,
        query: Optional[str] = None,
        active: Optional[bool] = None,
        status: Optional[str] = None,
        period_from: Optional[date] = None,
        period_to: Optional[date] = None,
        date_mode: Optional[str] = None,
        sort_field: Optional[str] = None,
        sort_dir: Optional[str] = "desc",
        advanced_filter_clause: Optional[Any] = None,
        entity_promotion_ids: Optional[list[str]] = None,
        promotion_ids: Optional[list[str]] = None,
        product_ids: Optional[list[str]] = None,
        attachment_state: Optional[str] = None,
        expiry_notify_batch_id: Optional[str] = None,
        subject_clause: Optional[Any] = None,
    ):
        """Build the filtered + sorted promotions query shared by ``list_promotions``
        and ``neighbours`` so the two can never drift.

        Returns ``(ordered_query_factory, primary_active_mode, narrowing_filter_present)``
        where ``ordered_query_factory(active_mode)`` yields the SQLAlchemy query for a
        given active gate. ``primary_active_mode`` is the first gate the list applies
        (before the active-first fallback). The ORDER BY always appends
        ``Promotion.id`` as a deterministic tie-breaker so offset position and
        prev/next neighbours are unambiguous when the primary sort column ties.
        """
        # Back-compat: legacy `status` query param translates to `active`.
        show_all = status == "all"
        if active is None and status and status != "all":
            if status == "active":
                active = True
            elif status == "inactive":
                active = False

        query_norm = (query or "").strip() or None
        date_mode_norm = (date_mode or "overlap").strip().lower()
        if date_mode_norm not in ("overlap", "started", "ended"):
            date_mode_norm = "overlap"
        if date_mode_norm in ("started", "ended") and active is None and not status:
            show_all = True
        if attachment_state and active is None and not status:
            show_all = True
        narrowing_filter_present = bool(
            query_norm
            or user_type
            or contact_access_codes
            or period_from
            or period_to
            or promotion_ids
            or product_ids
            or entity_promotion_ids
            or attachment_state
            or expiry_notify_batch_id
        )

        today = datetime.utcnow().date()
        is_within_window = and_(
            Promotion.start_date <= today,
            Promotion.end_date >= today,
        )
        no_window = and_(
            Promotion.start_date.is_(None),
            Promotion.end_date.is_(None),
        )
        active_clause = and_(
            Promotion.is_active.is_(True),
            or_(no_window, is_within_window),
        )

        def _apply_common_filters(q):
            if query_norm:
                search_term = f"%{query_norm}%"
                has_product_match = exists().where(
                    PromotionProduct.promotion_id == Promotion.id
                ).where(
                    PromotionProduct.product_id == Product.id
                ).where(
                    Product.product_code.ilike(search_term)
                )
                has_attachment_match = exists().where(
                    PromotionAttachment.promotion_id == Promotion.id
                ).where(
                    PromotionAttachment.attachment_id == Attachment.id
                ).where(
                    or_(
                        Attachment.original_filename.ilike(search_term),
                        Attachment.stored_filename.ilike(search_term),
                    )
                )
                q = q.filter(
                    or_(
                        Promotion.description.ilike(search_term),
                        has_product_match,
                        has_attachment_match,
                    )
                )
            if user_type:
                q = q.filter(Promotion.access_levels.contains([user_type]))
            if contact_access_codes is not None:
                # Empty access_levels (=[]) is invisible to any contact (no overlap possible).
                # Empty contact_access_codes (contact has no assigned types) returns nothing.
                if not contact_access_codes:
                    q = q.filter(text("false"))
                else:
                    q = q.filter(
                        Promotion.access_levels.op("?|")(
                            cast(contact_access_codes, ARRAY(String))
                        )
                    )
            if date_mode_norm == "started":
                # Launch date within window: "promotions released in X".
                if period_from is not None:
                    q = q.filter(Promotion.start_date >= period_from)
                if period_to is not None:
                    q = q.filter(Promotion.start_date <= period_to)
            elif date_mode_norm == "ended":
                # Expiry within window: "promotions that ended in X".
                if period_from is not None:
                    q = q.filter(Promotion.end_date >= period_from)
                if period_to is not None:
                    q = q.filter(Promotion.end_date <= period_to)
            else:
                # overlap (default): promotion ran at any point during window.
                if period_from is not None:
                    q = q.filter(Promotion.end_date >= period_from)
                if period_to is not None:
                    q = q.filter(Promotion.start_date <= period_to)
            if expiry_notify_batch_id:
                q = q.filter(Promotion.expiry_notify_batch_id == expiry_notify_batch_id)
            if entity_promotion_ids:
                q = q.filter(Promotion.id.in_(entity_promotion_ids))
            # promotion_ids + product_ids always combine via OR (n8n promo
            # discovery: "these specific promotions OR any promotion containing
            # these products"). When only one of the two is supplied, the OR
            # collapses to that single clause - equivalent to a plain filter.
            product_ids_clause = None
            if product_ids:
                product_ids_clause = exists().where(
                    PromotionProduct.promotion_id == Promotion.id
                ).where(
                    PromotionProduct.product_id.in_(product_ids)
                )
            promotion_ids_clause = (
                Promotion.id.in_(promotion_ids) if promotion_ids else None
            )
            if promotion_ids_clause is not None and product_ids_clause is not None:
                from sqlalchemy import or_ as _sa_or
                q = q.filter(_sa_or(promotion_ids_clause, product_ids_clause))
            elif promotion_ids_clause is not None:
                q = q.filter(promotion_ids_clause)
            elif product_ids_clause is not None:
                q = q.filter(product_ids_clause)
            if advanced_filter_clause is not None:
                q = q.filter(advanced_filter_clause)
            if subject_clause is not None:
                q = q.filter(subject_clause)
            if attachment_state:
                from sqlalchemy import exists as _sa_exists, and_ as _sa_and, or_ as _sa_or2
                has_any_link = _sa_exists().where(
                    PromotionAttachment.promotion_id == Promotion.id
                )
                linked_to_trashed = _sa_exists().where(
                    _sa_and(
                        PromotionAttachment.promotion_id == Promotion.id,
                        PromotionAttachment.attachment_id == Attachment.id,
                        Attachment.is_deleted.is_(True),
                    )
                )
                if attachment_state == "unlinked":
                    q = q.filter(~has_any_link)
                elif attachment_state == "linked_to_trashed":
                    q = q.filter(linked_to_trashed)
                elif attachment_state == "unlinked_or_trashed":
                    q = q.filter(_sa_or2(~has_any_link, linked_to_trashed))
            return q

        sort_key = (sort_field or "created_at").strip() or "created_at"
        dir_norm = (sort_dir or "desc").lower()
        if dir_norm not in ("asc", "desc"):
            dir_norm = "desc"
        sort_map = {
            "start_date": Promotion.start_date,
            "end_date": Promotion.end_date,
            "is_active": Promotion.is_active,
            "created_at": Promotion.created_at,
            "access_levels": Promotion.access_levels,
        }

        def _ordered_query(active_mode: Optional[bool]):
            q = self.db.query(Promotion)
            q = _apply_common_filters(q)
            if active_mode is True:
                q = q.filter(active_clause)
            elif active_mode is False:
                q = q.filter(~active_clause)
            if sort_key == "products_count":
                pc_subq = (
                    select(
                        PromotionProduct.promotion_id.label("pid"),
                        func.count(func.distinct(PromotionProduct.product_id)).label("pcnt"),
                    )
                    .group_by(PromotionProduct.promotion_id)
                    .subquery()
                )
                q = q.outerjoin(pc_subq, Promotion.id == pc_subq.c.pid)
                order_col = func.coalesce(pc_subq.c.pcnt, 0)
            else:
                order_col = sort_map.get(sort_key, Promotion.created_at)
            # Deterministic tie-breaker (Promotion.id) so offset position and
            # prev/next neighbours are stable when ``order_col`` values tie.
            primary = order_col.asc() if dir_norm == "asc" else order_col.desc()
            return q.order_by(primary, Promotion.id.asc())

        if show_all:
            primary_active_mode: Optional[bool] = None
        else:
            primary_active_mode = True if active is None else active
        return _ordered_query, primary_active_mode, narrowing_filter_present, (not show_all and active is not False)

    def list_promotions(
        self,
        page: int = 1,
        limit: int = 50,
        user_type: Optional[str] = None,
        contact_access_codes: Optional[list[str]] = None,
        query: Optional[str] = None,
        status: Optional[str] = None,
        active: Optional[bool] = None,
        period_from: Optional[date] = None,
        period_to: Optional[date] = None,
        date_mode: Optional[str] = None,
        sort_field: Optional[str] = None,
        sort_dir: Optional[str] = "desc",
        advanced_filter_clause: Optional[Any] = None,
        entities: Optional[list[str]] = None,
        promotion_ids: Optional[list[str]] = None,
        product_ids: Optional[list[str]] = None,
        attachment_state: Optional[str] = None,
        expiry_notify_batch_id: Optional[str] = None,
        serving_policy: bool = False,
        scope_terms: Optional[list[str]] = None,
        promotion_types: Optional[list[str]] = None,
        brand_ids: Optional[list[str]] = None,
    ):
        """List promotions with active-first fallback semantics.

        `serving_policy=True` replaces the active gate and its inactive fallback
        with the per-type serving policy (`app/services/promotion_serving.py`):
        live rows win, and a type with no live row contributes its latest expired
        row when the type's configuration allows it. This is what the chatbot
        asks for; the FE DataGrid never passes it and is unaffected.

        active=None (default): return active rows; if a narrowing filter is
        present and zero active match, fall back to inactive rows.
        active=True: same as default - find active first, fall back to inactive
        when narrowing filter is present and active set is empty.
        active=False: return only inactive rows (no fallback).

        Active definition: Promotion.is_active is True AND today falls within
        [start_date, end_date]. Anything outside that window - even if the
        boolean flag is True - is treated as inactive.

        period_from/period_to: optional date bounds. `date_mode` selects which
        promotion date the window tests:
      - "overlap" (default): [start_date, end_date] overlaps the window - 
          "promotions valid/running during X".
      - "started": start_date falls within the window - "promotions
          released/launched in X".
      - "ended": end_date falls within the window - "promotions that
          ended/expired in X".
        started/ended skip the active gate by default (the window is the
        intent; both active and ended rows match) unless active/status is
        passed explicitly.
        """
        # Entity resolution: when caller passes `entities`, filter by the resolved
        # Promotion.id set (IN clause). Folding promotion UUIDs into `query` would
        # be wrong - the resolver returns the promotion UUIDs, not free-text terms.
        from app.services.entity_filter_helpers import resolve_or_empty as _resolve_or_empty
        entity_buckets = _resolve_or_empty(self.db, entities)
        if entity_buckets is not None and not entity_buckets.has_resolved_filter:
            from app.schemas.common import PaginationResponse
            payload = {
                "data": [],
                "pagination": PaginationResponse(total=0, page=page, limit=limit),
                "empty": True,
                "resolved_entities": entity_buckets.as_echo(),
            }
            stamp_lookup_companies(self.db, payload, [], product_ids=product_ids)
            return payload
        entity_promotion_ids: Optional[list[str]] = None
        if entity_buckets is not None and entity_buckets.promotion_ids:
            entity_promotion_ids = list(entity_buckets.promotion_ids)

        # Subject filter (PROMO-SUBJECT-FILTER): promotions with at least one explicitly
        # linked product in the class the terms name, read as the resolver reads them.
        # Terms that name nothing the catalogue knows match nothing; there is never
        # an inactive fallback under a subject.
        subject_clause = None
        scope_terms = [t for t in (scope_terms or []) if t and t.strip()]
        if scope_terms:
            from app.models.product_spec import ProductSpecifications
            from app.services.product_spec_search import filter_specs

            member = filter_specs(self.db, scope_terms=scope_terms)["clause"]
            if member is None:
                subject_clause = text("false")
            else:
                subject_clause = exists().where(
                    PromotionProduct.promotion_id == Promotion.id
                ).where(
                    PromotionProduct.product_id == ProductSpecifications.product_id
                ).where(member)

        # Kind (`promotion_types.type_code`) and brand of a LINKED product, both ANDed with
        # everything else and, like the subject, never widened by the inactive fallback.
        kind_codes = [c for c in (promotion_types or []) if c and c.strip()]
        if kind_codes:
            type_clause = _promotion_kind_clause(kind_codes)
            subject_clause = type_clause if subject_clause is None else and_(subject_clause, type_clause)
        brand_list = [b for b in (brand_ids or []) if b and b.strip()]
        if brand_list:
            brand_clause = exists().where(
                PromotionProduct.promotion_id == Promotion.id
            ).where(
                PromotionProduct.product_id == Product.id
            ).where(Product.brand_id.in_(brand_list))
            subject_clause = brand_clause if subject_clause is None else and_(subject_clause, brand_clause)

        (
            _ordered_query,
            primary_active_mode,
            narrowing_filter_present,
            fallback_allowed,
        ) = self._build_promotions_ordered_query(
            user_type=user_type,
            contact_access_codes=contact_access_codes,
            query=query,
            active=active,
            status=status,
            period_from=period_from,
            period_to=period_to,
            date_mode=date_mode,
            sort_field=sort_field,
            sort_dir=sort_dir,
            advanced_filter_clause=advanced_filter_clause,
            entity_promotion_ids=entity_promotion_ids,
            promotion_ids=promotion_ids,
            product_ids=product_ids,
            attachment_state=attachment_state,
            expiry_notify_batch_id=expiry_notify_batch_id,
            subject_clause=subject_clause,
        )
        if subject_clause is not None:
            fallback_allowed = False

        today = datetime.utcnow().date()
        fallback_used = False
        verdict = None

        if serving_policy:
            # The policy is a ranking over the whole candidate set, so the active
            # gate is skipped entirely and the survivors are paginated afterwards.
            candidate_ids = [
                str(row[0])
                for row in _ordered_query(None)
                .with_entities(Promotion.id)
                .limit(promotion_serving.CANDIDATE_CAP + 1)
                .all()
            ]
            verdict = promotion_serving.evaluate_candidates(self.db, candidate_ids, today)
            q = _ordered_query(None).filter(Promotion.id.in_(list(verdict.served_ids)))
            total = q.count()
        else:
            q = _ordered_query(primary_active_mode)
            total = q.count()

            if fallback_allowed and total == 0 and narrowing_filter_present:
                q = _ordered_query(False)
                total = q.count()
                fallback_used = total > 0

        offset = (page - 1) * limit
        promotions = q.offset(offset).limit(limit).all()

        attachments_by_promotion = self._load_attachments_for_promotion_ids(
            [p.id for p in promotions]
        )
        # DISTINCT product, not row: one product legitimately appears in several
        # promotion_groups (same item, different bundle price), so a row count
        # reports 17 under a column headed "Products" when the promotion covers 12.
        #
        # One grouped query for the whole page, not one per promotion - the same
        # batching `_load_attachments_for_promotion_ids` does above. Per-row it was
        # a clean N+1: a 50-row page issued 50 extra round trips to return 50
        # integers.
        counts_by_promotion = self._load_product_counts_for_promotion_ids(
            [p.id for p in promotions]
        )
        for promotion in promotions:
            promotion.products_count = counts_by_promotion.get(promotion.id, 0)
            promotion.attachments = self._filter_attachments_by_codes(
                attachments_by_promotion.get(promotion.id, []),
                contact_access_codes,
            )
            # Python mirror of active_clause - see `_promotion_is_expired`.
            promotion.is_expired = _promotion_is_expired(promotion, today)
        _stamp_promotion_type_fields(self.db, promotions, verdict)

        payload = {
            "data": promotions,
            "pagination": {"total": total, "page": page, "limit": limit},
            "empty": total == 0,
            "fallback_used": fallback_used,
        }
        # Per-company labelling when the lookup spans more than one company - on the
        # empty path too, so an empty answer can name the companies searched.
        stamp_lookup_companies(self.db, payload, promotions, product_ids=product_ids)
        if serving_policy:
            payload["serving_policy_applied"] = True
        if entity_buckets is not None:
            payload["resolved_entities"] = entity_buckets.as_echo()
        # Data-miss (§3.3): the query scoped to a real product (product_ids) but no
        # promotion - active or inactive - contains it. Offer sibling products that
        # DO have an active promotion, on the empty path ONLY (a non-empty result,
        # including the inactive-promo fallback, is byte-identical - AC-R1).
        if total == 0:
            try:
                alternatives = self._promotion_entity_alternatives(set(product_ids or []))
            except Exception:
                import logging
                logging.getLogger(__name__).warning(
                    "promotion alternatives probe failed", exc_info=True
                )
                alternatives = None
            if alternatives:
                payload["alternatives"] = alternatives
                payload["relaxed_axis"] = "entity"
        return payload

    def _promotion_entity_alternatives(self, product_ids: set[str]) -> list[dict]:
        """Data-bearing variant/neighbour alternatives for an empty promotion result.

        Only fires when exactly ONE input product scoped the query (`product_ids`);
        otherwise "which product's neighbours?" is undefined. The has-data gate =
        the candidate product appears on at least one ACTIVE-window promotion - 
        ``Promotion.is_active`` AND today within ``[start_date, end_date]`` (or no
        window) - the same active definition ``_build_promotions_ordered_query``
        uses. A sibling whose only promo is expired is therefore not offered.
        """
        ids = list(dict.fromkeys(product_ids or []))
        if len(ids) != 1:
            return []
        prod = (
            self.db.query(Product.product_code)
            .filter(Product.id == ids[0])
            .first()
        )
        if not prod or not prod.product_code:
            return []

        today = datetime.utcnow().date()

        def _has_active_promo(candidate_ids: list[str]) -> set[str]:
            if not candidate_ids:
                return set()
            is_within_window = and_(
                Promotion.start_date <= today,
                Promotion.end_date >= today,
            )
            no_window = and_(
                Promotion.start_date.is_(None),
                Promotion.end_date.is_(None),
            )
            rows = (
                self.db.query(PromotionProduct.product_id)
                .join(Promotion, Promotion.id == PromotionProduct.promotion_id)
                .filter(
                    PromotionProduct.product_id.in_(candidate_ids),
                    Promotion.is_active.is_(True),
                    or_(no_window, is_within_window),
                )
                .distinct()
                .all()
            )
            return {str(row.product_id) for row in rows}

        from app.services.entity_resolver import find_entity_neighbours_with_data

        return find_entity_neighbours_with_data(
            self.db, prod.product_code, has_data=_has_active_promo
        )

    def get_promotion(
        self,
        promotion_id: str,
        *,
        include_products: bool = True,
        contact_access_codes: Optional[list[str]] = None,
    ):
        """Get a promotion by UUID.

        When *include_products* is False, loads groups without promotion product lines (no nested Product rows).
        When *contact_access_codes* is supplied, inline attachments are filtered to those whose
        ``access_levels`` overlaps the contact's codes.
        """
        from sqlalchemy.orm import joinedload, noload

        resolved_pid = _resolve_promotion_id_for_filter(self.db, promotion_id)
        if not resolved_pid:
            raise handle_not_found("Promotion", promotion_id)

        if not include_products:
            promotion = (
                self.db.query(Promotion)
                .options(
                    joinedload(Promotion.promotion_groups).options(
                        noload(PromotionGroup.promotion_products)
                    ),
                )
                .filter(Promotion.id == resolved_pid)
                .first()
            )
            if not promotion:
                raise handle_not_found("Promotion", promotion_id)

            groups = sorted(promotion.promotion_groups or [], key=lambda g: (g.sort_order, g.created_at))
            promotion.products_count = (
                self.db.query(func.count(func.distinct(PromotionProduct.product_id)))
                .filter(PromotionProduct.promotion_id == resolved_pid)
                .scalar()
                or 0
            )
            promotion.products = None
            promotion.promotion_groups = groups
            promotion.attachments = self._filter_attachments_by_codes(
                self._load_attachments_for_promotion_ids([resolved_pid]).get(resolved_pid, []),
                contact_access_codes,
            )
            _stamp_promotion_type_fields(self.db, [promotion])
            return promotion

        promotion = (
            self.db.query(Promotion)
            .options(
                joinedload(Promotion.promotion_groups).joinedload(PromotionGroup.promotion_products).joinedload(
                    PromotionProduct.product
                ),
            )
            .filter(Promotion.id == resolved_pid)
            .first()
        )
        if not promotion:
            raise handle_not_found("Promotion", promotion_id)

        groups = sorted(promotion.promotion_groups or [], key=lambda g: (g.sort_order, g.created_at))
        flat: list = []
        for g in groups:
            for pp in sorted(g.promotion_products or [], key=lambda x: x.created_at):
                flat.append(pp)

        promotion.products_count = len({pp.product_id for pp in flat})
        promotion.products = flat
        # Expose sorted groups for API (nested products on each group)
        promotion.promotion_groups = groups
        promotion.attachments = self._filter_attachments_by_codes(
            self._load_attachments_for_promotion_ids([resolved_pid]).get(resolved_pid, []),
            contact_access_codes,
        )
        _stamp_promotion_type_fields(self.db, [promotion])

        return promotion

    def create_promotion(self, promotion_data: PromotionCreate, created_by: str):
        """Create a new promotion. Validates access_levels against catalog; defaults to all active types if missing."""
        access_svc = ContactAccessTypeService(self.db)
        promotion_dict = promotion_data.model_dump()
        promotion_dict["created_by"] = created_by
        if promotion_dict.get("access_levels"):
            promotion_dict["access_levels"] = access_svc.validate_access_levels(
                promotion_dict["access_levels"], field_name="access_levels"
            )
        else:
            promotion_dict["access_levels"] = access_svc.get_default_access_levels()
        if promotion_dict.get("promotion_type_id"):
            self._assert_promotion_type_exists(promotion_dict["promotion_type_id"])
            # A type chosen by a human in the UI is a manual classification, and a
            # later re-upload of the same file must not overwrite it.
            promotion_dict["promotion_type_source"] = "manual"
        promotion = Promotion(**promotion_dict)
        self.db.add(promotion)
        self.db.commit()
        self.db.refresh(promotion)
        _stamp_promotion_type_fields(self.db, [promotion])
        publish_embedding_event(
            self.db,
            source_type="promotion",
            source_id=promotion.id,
            source_key=promotion.id,
            source_updated_at=promotion.updated_at or promotion.created_at,
            event_type="promotion.created",
            changed_fields=["description", "start_date", "end_date", "access_levels"],
            triggered_by=created_by,
        )
        return promotion

    def update_promotion(self, promotion_id: str, promotion_data: PromotionUpdate):
        """Update a promotion."""
        promotion = self.get_promotion(promotion_id)

        update_data = promotion_data.model_dump(exclude_unset=True)
        access_svc = ContactAccessTypeService(self.db)
        if "access_levels" in update_data and update_data["access_levels"]:
            update_data["access_levels"] = access_svc.validate_access_levels(
                update_data["access_levels"], field_name="access_levels"
            )

        if "promotion_type_id" in update_data:
            incoming_type_id = update_data["promotion_type_id"]
            if incoming_type_id:
                self._assert_promotion_type_exists(incoming_type_id)
            # Whoever RETYPED the promotion outranks the classifier from here on.
            # The edit form re-sends the current type with every save, so only a
            # value that actually differs (including a clear of a set type) is a
            # human decision; an unchanged echo must leave an auto classification
            # alone or the next re-send of the file could never reclassify it.
            current_type_id = str(promotion.promotion_type_id) if promotion.promotion_type_id else None
            if (str(incoming_type_id) if incoming_type_id else None) != current_type_id:
                update_data["promotion_type_source"] = "manual"

        for key, value in update_data.items():
            setattr(promotion, key, value)

        self.db.commit()
        self.db.refresh(promotion)
        _stamp_promotion_type_fields(self.db, [promotion])
        publish_embedding_event(
            self.db,
            source_type="promotion",
            source_id=promotion.id,
            source_key=promotion.id,
            source_updated_at=promotion.updated_at or promotion.created_at,
            event_type="promotion.updated" if promotion.is_active else "promotion.deactivated",
            changed_fields=list(update_data.keys()),
        )
        return promotion

    def _assert_promotion_type_exists(self, promotion_type_id: str) -> None:
        validate_uuid_path(str(promotion_type_id), resource="Promotion Type")
        exists_row = (
            self.db.query(PromotionType.id)
            .filter(PromotionType.id == str(promotion_type_id))
            .first()
        )
        if not exists_row:
            raise handle_not_found("Promotion Type", str(promotion_type_id))

    def delete_promotion(self, promotion_id: str):
        """Delete a promotion (cascade deletes promotion_products and promotion_attachments)."""
        promotion = self.get_promotion(promotion_id)
        self.db.delete(promotion)
        self.db.commit()

    def bulk_delete_promotions(self, promotion_ids: list[str]):
        """Delete multiple promotions. Returns count deleted."""
        if not promotion_ids:
            return {"message": "No promotions to delete", "deleted_count": 0}
        deleted = self.db.query(Promotion).filter(Promotion.id.in_(promotion_ids)).delete(synchronize_session=False)
        self.db.commit()
        return {"message": f"{deleted} promotion(s) deleted", "deleted_count": deleted}

    def bulk_update_access_levels(self, promotion_ids: list[str], access_levels: list[str]):
        """Set access_levels on multiple promotions. Returns count updated. Validates against contact access type catalog."""
        if not promotion_ids:
            return {"message": "No promotions selected", "updated_count": 0}
        access_svc = ContactAccessTypeService(self.db)
        normalized = access_svc.validate_access_levels(access_levels, field_name="access_levels")

        updated = (
            self.db.query(Promotion)
            .filter(Promotion.id.in_(promotion_ids))
            .update({"access_levels": normalized}, synchronize_session=False)
        )
        self.db.commit()
        return {"message": f"Access levels set for {updated} promotion(s).", "updated_count": updated}

    def sync_promotion_active_by_calendar_window(self) -> dict[str, Any]:
        """
        Align ``is_active`` with Malaysia calendar today vs inclusive [start_date, end_date]:
        activate when in range, deactivate when before start or after end.
        """
        now = datetime.now(timezone.utc)
        today_my = now.astimezone(_MY_TZ).date()
        rows = self.db.query(Promotion).all()
        activated = 0
        deactivated = 0
        for p in rows:
            if p.start_date is None or p.end_date is None:
                continue
            start_my = _promotion_stored_boundary_date(p.start_date)
            end_my = _promotion_stored_boundary_date(p.end_date)
            in_window = start_my <= today_my <= end_my
            if in_window:
                if not p.is_active:
                    p.is_active = True
                    activated += 1
            else:
                if p.is_active:
                    p.is_active = False
                    deactivated += 1
        if activated or deactivated:
            self.db.commit()
        return {
            "scanned": len(rows),
            "activated": activated,
            "deactivated": deactivated,
            "today_malaysia": today_my.isoformat(),
        }

    def create_promotion_group(self, promotion_id: str, data: PromotionGroupCreate) -> PromotionGroup:
        """Add a bundle / FOC group to a promotion."""
        if not self.db.query(Promotion.id).filter(Promotion.id == promotion_id).first():
            raise handle_not_found("Promotion", promotion_id)
        name = (data.group_name or "").strip()
        if not name:
            raise handle_conflict("Group name cannot be empty.")
        max_so = (
            self.db.query(func.coalesce(func.max(PromotionGroup.sort_order), -1))
            .filter(PromotionGroup.promotion_id == promotion_id)
            .scalar()
        )
        sort_order = data.sort_order if data.sort_order is not None else (int(max_so) + 1)
        tiers = _tiers_from_create_data(data)
        g = PromotionGroup(
            promotion_id=promotion_id,
            group_name=name,
            sort_order=sort_order,
            foc_tiers=tiers,
        )
        self.db.add(g)
        self.db.commit()
        self.db.refresh(g)
        return g

    def update_promotion_group(self, promotion_id: str, group_id: str, data: PromotionGroupUpdate) -> PromotionGroup:
        """Update group name, sort order, or FOC fields."""
        g = (
            self.db.query(PromotionGroup)
            .filter(PromotionGroup.id == group_id, PromotionGroup.promotion_id == promotion_id)
            .first()
        )
        if not g:
            raise handle_not_found("Promotion group", group_id)
        update_data = data.model_dump(exclude_unset=True)
        if "group_name" in update_data and update_data["group_name"] is not None:
            nm = str(update_data["group_name"]).strip()
            if not nm:
                raise handle_conflict("Group name cannot be empty.")
            update_data["group_name"] = nm

        if "foc_tiers" in update_data:
            raw = update_data.pop("foc_tiers")
            if raw is None or raw == []:
                g.foc_tiers = None
            else:
                tiers: list[dict] = []
                for t in raw:
                    if isinstance(t, dict):
                        pq = int(t["purchase_quantity"])
                        fq = int(t["foc_quantity"])
                    else:
                        pq = int(t.purchase_quantity)
                        fq = int(t.foc_quantity)
                    if pq < 1:
                        raise handle_conflict("FOC purchase quantity must be at least 1.")
                    if fq < 0:
                        raise handle_conflict("FOC free quantity cannot be negative.")
                    tiers.append({"purchase_quantity": pq, "foc_quantity": fq})
                g.foc_tiers = tiers

        for key, value in update_data.items():
            setattr(g, key, value)
        self.db.commit()
        self.db.refresh(g)
        return g

    def delete_promotion_group(self, promotion_id: str, group_id: str) -> dict:
        """
        Delete a promotion group (cascade deletes promotion product lines in that group).
        Cannot remove the last remaining group.
        """
        g = (
            self.db.query(PromotionGroup)
            .filter(PromotionGroup.id == group_id, PromotionGroup.promotion_id == promotion_id)
            .first()
        )
        if not g:
            raise handle_not_found("Promotion group", group_id)
        total = (
            self.db.query(func.count(PromotionGroup.id))
            .filter(PromotionGroup.promotion_id == promotion_id)
            .scalar()
        )
        if (total or 0) <= 1:
            raise handle_conflict(
                "Cannot delete the only promotion group. Create another group first, or delete the promotion."
            )
        line_count = (
            self.db.query(func.count(PromotionProduct.id))
            .filter(PromotionProduct.promotion_group_id == group_id)
            .scalar()
            or 0
        )
        self.db.delete(g)
        self.db.commit()
        return {
            "message": "Promotion group deleted",
            "deleted_product_lines": int(line_count),
        }


class PromotionProductService:
    """Service for promotion product operations."""
    
    def __init__(self, db: Session):
        self.db = db

    def get_or_create_default_promotion_group(self, promotion_id: str) -> PromotionGroup:
        """Legacy / simple promotions: one unnamed default group for flat product lists."""
        g = (
            self.db.query(PromotionGroup)
            .filter(
                PromotionGroup.promotion_id == promotion_id,
                PromotionGroup.group_name == "Default",
            )
            .first()
        )
        if g:
            return g
        g = PromotionGroup(
            promotion_id=promotion_id,
            group_name="Default",
            sort_order=0,
            foc_tiers=None,
        )
        self.db.add(g)
        self.db.flush()
        return g

    def _compute_discount_values(self, list_price: float, promo_price: float) -> tuple[float, float]:
        """Compute discount amount/percent; clamp percent to NUMERIC(5,2) range."""
        discount_amount = list_price - promo_price
        discount_percent = (discount_amount / list_price * 100) if list_price > 0 else 0
        discount_percent = clamp_discount_percent_for_db(discount_percent)
        if discount_percent is None:
            discount_percent = 0.0
        return discount_amount, discount_percent
    
    def list_promotion_products(
        self,
        promotion_id: Optional[str] = None,
        promotion_ids: Optional[list[str]] = None,
        product_id: Optional[str] = None,
        product_ids_filter: Optional[list[str]] = None,
        page: int = 1,
        limit: int = 50,
        sort_field: str = "created_at",
        sort_dir: str = "asc",
        query: Optional[str] = None,
        category_id: Optional[str] = None,
        brand_id: Optional[str] = None,
        item_type: Optional[str] = None,
        status: Optional[str] = None,
        active: Optional[bool] = None,
        price_min: Optional[float] = None,
        price_max: Optional[float] = None,
        length_min: Optional[float] = None,
        length_max: Optional[float] = None,
        width_min: Optional[float] = None,
        width_max: Optional[float] = None,
        height_min: Optional[float] = None,
        height_max: Optional[float] = None,
        any_dimension_min: Optional[float] = None,
        any_dimension_max: Optional[float] = None,
        contact_access_codes: Optional[list[str]] = None,
        entities: Optional[list[str]] = None,
        serving_policy: bool = False,
        spec_constraints: Optional[list[dict]] = None,
        hidden_spec_keys: frozenset[str] = frozenset(),
        brand_ids: Optional[list[str]] = None,
        promotion_types: Optional[list[str]] = None,
    ):
        """List products for a promotion, several promotions, or all promotion products.

        `spec_constraints` (parsed by `product_spec_filter.parse_spec_constraints`) keeps
        only lines whose product satisfies them; a virtual `size` constraint is resolved
        first and, when the data cannot place it on one axis, the answer is no rows plus
        the clarify / miss keys `resolve_size` returns (SPEC-PROMO-PARAMS).

        `serving_policy=True` replaces the parent-promotion active gate with the
        per-type serving policy (see `app/services/promotion_serving.py`), so the
        chatbot cannot be handed a line whose parent is an expired special.

        Text `query` is tokenized on whitespace; every token must match (case-insensitive)
        somewhere across Product.product_code, product_name, description, item_type, or
        Promotion.description. Structured filters (category_id, brand_id, dimensions, price,
        item_type, status) are AND-combined with the text query.

        `active` gates on the PARENT promotion's active state (not Product.is_active - 
        that's `status`). active=None: no gate (full catalog - FE DataGrid default).
        active=True: active-promotion lines first; if a narrowing filter is present and
        zero match, fall back to inactive-promotion lines (`fallback_used=true`).
        active=False: only inactive-promotion lines, no fallback. The MCP/API-key route
        defaults the missing param to True so chatbot callers get active-first + fallback.
        Active = Promotion.is_active and today within start/end (or no window) - same
        definition as the promotions list.
        """
        from sqlalchemy.orm import joinedload
        from sqlalchemy import or_, and_
        from sqlalchemy import func as _sa_func
        import logging
        logger = logging.getLogger(__name__)
        from app.services.entity_filter_helpers import (
            resolve_or_empty as _resolve_or_empty,
        )

        from app.services.product_spec_filter import promoted_clause, resolve_size, spec_constraint_clause, stamp_spec_page

        # The size is resolved among the products this route answers about: those on a line of
        # a promotion live today, open to the caller's tier and of the product's own company
        # (`promoted_clause`), not the whole catalogue. An explicit inactive-only ask
        # (`active=False`, a staff grid) reads the catalogue as before.
        spec_base = None if active is False else promoted_clause(self.db, contact_access_codes)
        sized_constraints, spec_extra = resolve_size(self.db, spec_constraints, hidden_spec_keys, spec_base)
        if spec_constraints and sized_constraints is None:
            return {
                "data": [],
                "pagination": {"total": 0, "page": page, "limit": limit},
                "empty": True,
                **spec_extra,
            }
        spec_constraints = sized_constraints

        _entity_buckets = _resolve_or_empty(self.db, entities)
        if _entity_buckets is not None and not _entity_buckets.has_resolved_filter:
            from app.schemas.common import PaginationResponse
            payload = {
                "data": [],
                "pagination": PaginationResponse(total=0, page=page, limit=limit),
                "empty": True,
                "resolved_entities": _entity_buckets.as_echo(),
            }
            stamp_lookup_companies(
                self.db, payload, [], product_ids=product_ids_filter
            )
            return payload
        if _entity_buckets is not None and _entity_buckets.promotion_ids:
            promotion_ids = list({*(promotion_ids or []), *_entity_buckets.promotion_ids})

        q = self.db.query(PromotionProduct).options(
            joinedload(PromotionProduct.product).joinedload(Product.category),
            joinedload(PromotionProduct.product).joinedload(Product.brand),
            joinedload(PromotionProduct.promotion),
        )

        if product_ids_filter:
            q = q.filter(PromotionProduct.product_id.in_(product_ids_filter))

        if spec_constraints:
            q = q.filter(
                PromotionProduct.product_id.in_(
                    select(Product.id).where(spec_constraint_clause(self.db, spec_constraints))
                )
            )
        # Brand of the LINE's product and kind of its promotion (owner Q4 option (a), the
        # promotions list's own two narrowings, `list_promotions`), ANDed with the rest.
        brand_list = [b for b in (brand_ids or []) if b and str(b).strip()]
        if brand_list:
            q = q.filter(PromotionProduct.product_id.in_(select(Product.id).where(Product.brand_id.in_(brand_list))))
        kind_codes = [c for c in (promotion_types or []) if c and str(c).strip()]
        if kind_codes:
            q = q.filter(PromotionProduct.promotion_id.in_(select(Promotion.id).where(_promotion_kind_clause(kind_codes))))

        if promotion_ids:
            resolved_bulk: list[str] = []
            for raw in promotion_ids:
                resolved = _resolve_promotion_id_for_filter(self.db, raw)
                if resolved:
                    resolved_bulk.append(resolved)
            if not resolved_bulk:
                payload = {
                    "data": [],
                    "pagination": {"total": 0, "page": page, "limit": limit},
                    "empty": True,
                }
                stamp_lookup_companies(
                    self.db, payload, [], product_ids=product_ids_filter
                )
                return payload
            q = q.filter(PromotionProduct.promotion_id.in_(resolved_bulk))
            logger.debug("Filtering by promotion_ids count=%s", len(resolved_bulk))
        elif promotion_id:
            resolved_pid = _resolve_promotion_id_for_filter(self.db, promotion_id)
            if not resolved_pid:
                payload = {
                    "data": [],
                    "pagination": {"total": 0, "page": page, "limit": limit},
                    "empty": True,
                }
                stamp_lookup_companies(
                    self.db, payload, [], product_ids=product_ids_filter
                )
                return payload
            logger.debug(f"Filtering by resolved promotion_id: {resolved_pid}")
            q = q.filter(PromotionProduct.promotion_id == resolved_pid)

        if product_id:
            pid = str(product_id).strip()
            if pid:
                q = q.filter(PromotionProduct.product_id == pid)

        # Resolve category / brand (UUID or code/name) before joining.
        category_uuids = resolve_identifier(
            self.db,
            category_id,
            ProductCategory,
            code_fields=("category_code", "category_name"),
        )
        if category_uuids is not None and not category_uuids:
            payload = {
                "data": [],
                "pagination": {"total": 0, "page": page, "limit": limit},
                "empty": True,
            }
            stamp_lookup_companies(
                self.db, payload, [], product_ids=product_ids_filter
            )
            return payload
        brand_uuids = resolve_identifier(
            self.db,
            brand_id,
            Brand,
            code_fields=("brand_code", "brand_name"),
        )
        if brand_uuids is not None and not brand_uuids:
            payload = {
                "data": [],
                "pagination": {"total": 0, "page": page, "limit": limit},
                "empty": True,
            }
            stamp_lookup_companies(
                self.db, payload, [], product_ids=product_ids_filter
            )
            return payload

        needs_product_join = (
            bool(query)
            or category_uuids is not None
            or brand_uuids is not None
            or item_type is not None
            or status is not None
            or price_min is not None
            or price_max is not None
            or length_min is not None
            or length_max is not None
            or width_min is not None
            or width_max is not None
            or height_min is not None
            or height_max is not None
            or any_dimension_min is not None
            or any_dimension_max is not None
        )
        # Always join Promotion: the active-first gate filters on parent
        # promotion state, so the join is needed unconditionally now.
        needs_promotion_join = True

        if needs_product_join:
            q = q.join(Product, PromotionProduct.product_id == Product.id)
        if needs_promotion_join:
            q = q.join(Promotion, PromotionProduct.promotion_id == Promotion.id)

        if contact_access_codes is not None:
            if not contact_access_codes:
                q = q.filter(text("false"))
            else:
                q = q.filter(
                    Promotion.access_levels.op("?|")(
                        cast(contact_access_codes, ARRAY(String))
                    )
                )

        if category_uuids is not None:
            q = q.filter(Product.category_id.in_(category_uuids))
        if brand_uuids is not None:
            q = q.filter(Product.brand_id.in_(brand_uuids))
        if item_type:
            q = q.filter(Product.item_type == item_type)
        if status and status != "all":
            q = q.filter(Product.is_active == (status == "active"))
        if price_min is not None:
            q = q.filter(Product.list_price >= Decimal(str(price_min)))
        if price_max is not None:
            q = q.filter(Product.list_price <= Decimal(str(price_max)))
        if length_min is not None:
            q = q.filter(Product.dimensions_length >= Decimal(str(length_min)))
        if length_max is not None:
            q = q.filter(Product.dimensions_length <= Decimal(str(length_max)))
        if width_min is not None:
            q = q.filter(Product.dimensions_width >= Decimal(str(width_min)))
        if width_max is not None:
            q = q.filter(Product.dimensions_width <= Decimal(str(width_max)))
        if height_min is not None:
            q = q.filter(Product.dimensions_height >= Decimal(str(height_min)))
        if height_max is not None:
            q = q.filter(Product.dimensions_height <= Decimal(str(height_max)))
        if any_dimension_min is not None:
            v = Decimal(str(any_dimension_min))
            q = q.filter(or_(
                Product.dimensions_length >= v,
                Product.dimensions_width >= v,
                Product.dimensions_height >= v,
            ))
        if any_dimension_max is not None:
            v = Decimal(str(any_dimension_max))
            q = q.filter(or_(
                Product.dimensions_length <= v,
                Product.dimensions_width <= v,
                Product.dimensions_height <= v,
            ))

        if query:
            tokens = [t for t in str(query).split() if t]
            for tok in tokens:
                like = f"%{tok}%"
                q = q.filter(or_(
                    Product.product_code.ilike(like),
                    Product.product_name.ilike(like),
                    Product.description.ilike(like),
                    Product.item_type.ilike(like),
                    Promotion.description.ilike(like),
                ))

        # Active gate on parent promotion state.
        #   active=None  -> no gate (full catalog; FE DataGrid default)
        #   active=True  -> active promotions first; fall back to inactive when a
        #                   narrowing filter is present and zero active match
        #                   (fallback_used=true) - mirrors the promotions list.
        #   active=False -> inactive-promotion lines only (no fallback)
        # The MCP/API-key route defaults the missing param to True so chatbot
        # callers get active-first + fallback; interactive callers keep "all".
        narrowing_filter_present = bool(
            query
            or promotion_id
            or promotion_ids
            or product_id
            or product_ids_filter
            or category_uuids is not None
            or brand_uuids is not None
            or item_type
            or (status and status != "all")
            or price_min is not None
            or price_max is not None
            or length_min is not None
            or length_max is not None
            or width_min is not None
            or width_max is not None
            or height_min is not None
            or height_max is not None
            or any_dimension_min is not None
            or any_dimension_max is not None
            or contact_access_codes is not None
            or bool(spec_constraints)
            or bool(brand_list)
            or bool(kind_codes)
        )
        today = datetime.utcnow().date()
        active_clause = _promotion_active_clause(today)
        fallback_used = False
        serving_verdict = None
        if serving_policy:
            # Rank the PARENT promotions the filtered lines belong to, then keep
            # only the lines whose parent survives the policy.
            parent_ids = [
                str(row[0])
                for row in q.with_entities(PromotionProduct.promotion_id)
                .distinct()
                .limit(promotion_serving.CANDIDATE_CAP + 1)
                .all()
            ]
            serving_verdict = promotion_serving.evaluate_candidates(self.db, parent_ids, today)
            served = set(serving_verdict.served_ids)
            live = served - serving_verdict.expired_but_usable_ids
            if spec_constraints and live:
                # SPEC-PROMO-PARAMS (crew live check, bec7d329): "which basins are on
                # promotion?" listed a promotion that ended a month ago beside live ones. A
                # live answer exists, so the expired one goes; with none live the expired
                # ones still serve, marked (owner round 3, 9 Oct: expired hidden beside live).
                served = live
            q_final = q.filter(PromotionProduct.promotion_id.in_(list(served)))
            total = q_final.count()
        elif active is None:
            q_final = q
            total = q_final.count()
        elif active is False:
            q_final = q.filter(~active_clause)
            total = q_final.count()
        else:
            q_final = q.filter(active_clause)
            total = q_final.count()
            if total == 0 and narrowing_filter_present:
                q_fallback = q.filter(~active_clause)
                fb_total = q_fallback.count()
                if fb_total > 0:
                    q_final, total, fallback_used = q_fallback, fb_total, True

        # Sorting
        sort_map = {
            "created_at": PromotionProduct.created_at,
        }
        sort_column = sort_map.get(sort_field, PromotionProduct.created_at)
        ordered = sort_column.desc() if sort_dir == "desc" else sort_column.asc()
        if spec_constraints:
            # A spec-filtered answer is paged ("Reply to see more"): one promotion's lines
            # together, and a unique tiebreak so no line repeats or drops between pages.
            q_final = q_final.order_by(PromotionProduct.promotion_id, ordered, PromotionProduct.id)
        else:
            q_final = q_final.order_by(ordered)

        offset = (page - 1) * limit
        products = q_final.offset(offset).limit(limit).all()

        # Inline parent-promotion attachments per line so callers (esp. MCP
        # agents) don't need a follow-up tool call to fetch the promotion
        # document for the SKU they just asked about.
        parent_pids = list({p.promotion_id for p in products})
        attachments_map = _load_attachments_by_promotion_ids(self.db, parent_pids)
        parent_type_ids = [
            getattr(p.promotion, "promotion_type_id", None) for p in products
        ]
        parent_type_labels = _promotion_type_labels(self.db, parent_type_ids)
        parent_default_labels = (
            _default_type_labels(self.db) if any(not t for t in parent_type_ids) else (None, None)
        )
        for line in products:
            line.promotion_attachments = attachments_map.get(line.promotion_id, [])
            # Row-level expiry of the PARENT promotion, mirroring the promotions
            # list - lets MCP/n8n say "found but expired" for fallback/historical
            # lines instead of presenting them as live.
            line.is_expired = _promotion_is_expired(line.promotion, today)
            code, name = _type_labels_for(
                getattr(line.promotion, "promotion_type_id", None),
                parent_type_labels,
                parent_default_labels,
            )
            line.promotion_type_code = code
            line.promotion_type_name = name
            group = getattr(line, "promotion_group", None)
            line.promotion_group_name = getattr(group, "group_name", None) if group is not None else None
            if serving_verdict is not None:
                line.expired_but_usable = serving_verdict.is_expired_but_usable(line.promotion_id)

        payload = {
            "data": products,
            "pagination": {"total": total, "page": page, "limit": limit},
            "empty": total == 0,
            "fallback_used": fallback_used,
        }
        # Per-company labelling when the lookup spans more than one company - on the
        # empty path too, so an empty answer can name the companies searched.
        stamp_lookup_companies(
            self.db, payload, products, product_ids=product_ids_filter
        )
        if serving_policy:
            payload["serving_policy_applied"] = True
        if _entity_buckets is not None:
            payload["resolved_entities"] = _entity_buckets.as_echo()
        payload.update(spec_extra)
        stamp_spec_page(payload, spec_constraints)
        return payload

    def get_promotion_line(self, promotion_id: str, line_id: str):
        """Get a promotion product row by junction id (supports same SKU in multiple groups)."""
        from sqlalchemy.orm import joinedload

        row = (
            self.db.query(PromotionProduct)
            .options(
                joinedload(PromotionProduct.product),
                joinedload(PromotionProduct.promotion),
                joinedload(PromotionProduct.promotion_group),
            )
            .filter(
                PromotionProduct.id == line_id,
                PromotionProduct.promotion_id == promotion_id,
            )
            .first()
        )
        if not row:
            raise handle_not_found("Promotion product line", f"{promotion_id}/{line_id}")
        return row

    def get_promotion_product(self, promotion_id: str, product_id: str):
        """Deprecated: use get_promotion_line. Returns first matching row if duplicates exist."""
        from sqlalchemy.orm import joinedload

        row = (
            self.db.query(PromotionProduct)
            .options(joinedload(PromotionProduct.product), joinedload(PromotionProduct.promotion))
            .filter(
                PromotionProduct.promotion_id == promotion_id,
                PromotionProduct.product_id == product_id,
            )
            .first()
        )
        if not row:
            raise handle_not_found("Promotion Product", f"{promotion_id}/{product_id}")
        return row
    
    def create_promotion_product(self, product_data: PromotionProductCreate):
        """Add a product to a promotion (within a group; default group if omitted)."""
        from sqlalchemy.orm import joinedload
        from app.models.product import Product

        pdata = product_data.model_dump()
        group_id = pdata.get("promotion_group_id")
        if not group_id:
            g = self.get_or_create_default_promotion_group(product_data.promotion_id)
            group_id = g.id
        else:
            g = (
                self.db.query(PromotionGroup)
                .filter(
                    PromotionGroup.id == group_id,
                    PromotionGroup.promotion_id == product_data.promotion_id,
                )
                .first()
            )
            if not g:
                raise handle_not_found("Promotion group", str(group_id))
        
        existing = self.db.query(PromotionProduct).filter(
            PromotionProduct.promotion_group_id == group_id,
            PromotionProduct.product_id == product_data.product_id
        ).first()
        if existing:
            label = _product_display_label(self.db, product_data.product_id)
            raise handle_conflict(f"Product already in this promotion group: {label}.")
        
        # Get the product to calculate discount
        product = self.db.query(Product).filter(Product.id == product_data.product_id).first()
        if not product:
            raise handle_not_found("Product", product_data.product_id)
        
        promo_selling_price = product_data.promo_selling_price
        discount_amount = None
        discount_percent = None
        
        if promo_selling_price and product.list_price:
            list_price = float(product.list_price)
            promo_price = float(promo_selling_price)
            discount_amount, discount_percent = self._compute_discount_values(list_price, promo_price)

        dd = product_data.dealer_discount_percent
        dd_f = float(dd) if dd is not None else None
        dealer_cost, list_to_dealer_margin = dealer_cost_and_margin_from_list(
            float(product.list_price) if product.list_price is not None else None,
            dd_f,
        )
        
        promotion_product = PromotionProduct(
            promotion_id=product_data.promotion_id,
            promotion_group_id=group_id,
            product_id=product_data.product_id,
            promo_selling_price=promo_selling_price,
            discount_amount=discount_amount,
            discount_percent=discount_percent,
            dealer_discount_percent=dd,
            dealer_cost=dealer_cost,
            list_to_dealer_margin_amount=list_to_dealer_margin,
        )
        self.db.add(promotion_product)
        try:
            self.db.commit()
            self.db.refresh(promotion_product)
        except IntegrityError as e:
            self.db.rollback()
            raise_promotion_product_unique_violation(self.db, e)

        # Reload with product and promotion relationships
        return self.db.query(PromotionProduct).options(
            joinedload(PromotionProduct.product),
            joinedload(PromotionProduct.promotion)
        ).filter(PromotionProduct.id == promotion_product.id).first()
    
    def update_promotion_product(self, promotion_id: str, line_id: str, product_data: PromotionProductUpdate):
        """Update a promotion product line (by promotion_products.id)."""
        from sqlalchemy.orm import joinedload
        from app.models.product import Product
        
        promotion_product = self.get_promotion_line(promotion_id, line_id)
        product = self.db.query(Product).filter(Product.id == promotion_product.product_id).first()
        
        update_data = product_data.model_dump(exclude_unset=True)

        list_price_new = update_data.pop('list_price', None)
        if list_price_new is not None:
            if product is None:
                raise handle_not_found("Product", promotion_product.product_id)
            lp_dec = list_price_new
            if lp_dec < 0:
                raise handle_validation_error("List price cannot be negative.")
            product.list_price = lp_dec

        recompute_discount = (
            ('promo_selling_price' in update_data or list_price_new is not None)
            and product
            and product.list_price is not None
        )
        if recompute_discount:
            promo_src = update_data.get('promo_selling_price', promotion_product.promo_selling_price)
            if promo_src is not None:
                promo_price = float(promo_src)
                list_price = float(product.list_price)
                discount_amount, discount_percent = self._compute_discount_values(list_price, promo_price)
                update_data['discount_amount'] = discount_amount
                update_data['discount_percent'] = discount_percent

        recompute_dealer = ('dealer_discount_percent' in update_data or list_price_new is not None) and product
        if recompute_dealer:
            dd = (
                update_data['dealer_discount_percent']
                if 'dealer_discount_percent' in update_data
                else promotion_product.dealer_discount_percent
            )
            dd_f = float(dd) if dd is not None else None
            dc, margin = dealer_cost_and_margin_from_list(
                float(product.list_price) if product.list_price is not None else None,
                dd_f,
            )
            update_data['dealer_cost'] = dc
            update_data['list_to_dealer_margin_amount'] = margin

        for key, value in update_data.items():
            setattr(promotion_product, key, value)
        
        self.db.commit()
        self.db.refresh(promotion_product)
        
        return self.db.query(PromotionProduct).options(
            joinedload(PromotionProduct.product),
            joinedload(PromotionProduct.promotion)
        ).filter(PromotionProduct.id == promotion_product.id).first()
    
    def delete_promotion_product(self, promotion_id: str, line_id: str):
        """Remove a promotion product line from a promotion."""
        row = self.get_promotion_line(promotion_id, line_id)
        self.db.delete(row)
        self.db.commit()
        return {"message": "Product removed from promotion"}


#: NOT NULL on `promotion_types`. An explicit null for one of these means "leave
#: it alone", not "write NULL" -- the column would reject it at commit time and
#: the caller would get a 500 for what is a malformed payload.
_PROMOTION_TYPE_REQUIRED_COLUMNS = (
    "type_code",
    "type_name",
    "show_expired",
    "expired_valid_until_year_end",
    "match_markers",
    "match_priority",
    "is_default",
    "sort_order",
)


class PromotionTypeService:
    """CRUD for the promotion-type vocabulary an admin maintains."""

    def __init__(self, db: Session):
        self.db = db

    def _counts_by_type(self) -> dict[str, int]:
        rows = (
            self.db.query(Promotion.promotion_type_id, func.count(Promotion.id))
            .filter(Promotion.promotion_type_id.isnot(None))
            .group_by(Promotion.promotion_type_id)
            .all()
        )
        return {str(type_id): count for type_id, count in rows}

    def list_promotion_types(self):
        types = (
            self.db.query(PromotionType)
            .order_by(
                PromotionType.sort_order.asc(),
                PromotionType.match_priority.asc(),
                PromotionType.type_code.asc(),
            )
            .all()
        )
        counts = self._counts_by_type()
        for promo_type in types:
            promo_type.promotions_count = counts.get(str(promo_type.id), 0)
        return types

    def get_promotion_type(self, type_id: str):
        promo_type = self.db.query(PromotionType).filter(PromotionType.id == type_id).first()
        if not promo_type:
            raise handle_not_found("Promotion Type", type_id)
        promo_type.promotions_count = self._counts_by_type().get(str(promo_type.id), 0)
        return promo_type

    def _assert_code_free(self, type_code: str, *, exclude_id: Optional[str] = None) -> None:
        q = self.db.query(PromotionType.id).filter(PromotionType.type_code == type_code)
        if exclude_id:
            q = q.filter(PromotionType.id != exclude_id)
        if q.first():
            raise handle_conflict(f"A promotion type with code '{type_code}' already exists")

    def _clear_other_defaults(self, keep_id: Optional[str]) -> None:
        """One default type, so `is_default` is a switch and not a checkbox pile.

        The DB carries a partial unique index that would otherwise reject the
        second tick with a constraint error nobody can act on.
        """
        q = self.db.query(PromotionType).filter(PromotionType.is_default.is_(True))
        if keep_id:
            q = q.filter(PromotionType.id != keep_id)
        for row in q.all():
            row.is_default = False
        self.db.flush()

    def create_promotion_type(self, type_data):
        payload = type_data.model_dump()
        self._assert_code_free(payload["type_code"])
        if payload.get("is_default"):
            self._clear_other_defaults(None)
        promo_type = PromotionType(**payload)
        self.db.add(promo_type)
        self.db.commit()
        self.db.refresh(promo_type)
        promo_type.promotions_count = 0
        return promo_type

    def update_promotion_type(self, type_id: str, type_data):
        promo_type = self.db.query(PromotionType).filter(PromotionType.id == type_id).first()
        if not promo_type:
            raise handle_not_found("Promotion Type", type_id)
        update_data = type_data.model_dump(exclude_unset=True)
        for column in _PROMOTION_TYPE_REQUIRED_COLUMNS:
            if column in update_data and update_data[column] is None:
                update_data.pop(column)
        if "type_code" in update_data:
            self._assert_code_free(update_data["type_code"], exclude_id=type_id)
        if update_data.get("is_default"):
            self._clear_other_defaults(type_id)
        if update_data.get("is_default") is False and promo_type.is_default:
            raise handle_conflict(
                "Untick the default on this type by making another type the default instead; "
                "an unclassified promotion needs one type to fall back to."
            )
        for key, value in update_data.items():
            setattr(promo_type, key, value)
        self.db.commit()
        self.db.refresh(promo_type)
        promo_type.promotions_count = self._counts_by_type().get(str(promo_type.id), 0)
        return promo_type

    def delete_promotion_type(self, type_id: str):
        """Hard delete. Promotions pointing here fall back to the default type's policy."""
        promo_type = self.db.query(PromotionType).filter(PromotionType.id == type_id).first()
        if not promo_type:
            raise handle_not_found("Promotion Type", type_id)
        if promo_type.is_default:
            raise handle_conflict(
                "This is the default promotion type. Make another type the default before deleting it."
            )
        # Unclassify the promotions here rather than leaning on the FK's SET NULL:
        # the source has to go with the type, or a `manual` row would keep a
        # classification nobody can see and the re-send path would never retype it.
        affected = (
            self.db.query(Promotion)
            .filter(Promotion.promotion_type_id == str(promo_type.id))
            .update(
                {"promotion_type_id": None, "promotion_type_source": None},
                synchronize_session=False,
            )
        )
        self.db.delete(promo_type)
        self.db.commit()
        return {
            "message": "Promotion type deleted",
            "promotions_unclassified": affected,
        }


class CampaignTypeService:
    """Service for campaign type operations."""
    
    def __init__(self, db: Session):
        self.db = db
    
    def list_campaign_types(self):
        """List all campaign types."""
        try:
            return self.db.query(CampaignType).all()
        except Exception as exc:
            # Backward-compat for environments where campaign_types.type_code is missing.
            if "campaign_types.type_code" not in str(exc):
                raise
            self.db.rollback()
            rows = self._fallback_campaign_type_rows()
            fallback = []
            for row in rows:
                fallback.append(
                    {
                        "id": str(row.id),
                        "type_code": str(row.type_name or row.id),
                        "type_name": row.type_name,
                        "description": row.description,
                        "created_at": row.created_at,
                        "updated_at": row.updated_at or row.created_at,
                    }
                )
            return fallback
    
    def get_campaign_type(self, type_id: str):
        """Get a campaign type by ID."""
        try:
            campaign_type = self.db.query(CampaignType).filter(CampaignType.id == type_id).first()
            if not campaign_type:
                raise handle_not_found("Campaign Type", type_id)
            return campaign_type
        except Exception as exc:
            if "campaign_types.type_code" not in str(exc):
                raise
            self.db.rollback()
            rows = self._fallback_campaign_type_rows(type_id=type_id)
            row = rows[0] if rows else None
            if not row:
                raise handle_not_found("Campaign Type", type_id)
            return {
                "id": str(row.id),
                "type_code": str(row.type_name or row.id),
                "type_name": row.type_name,
                "description": row.description,
                "created_at": row.created_at,
                "updated_at": row.updated_at or row.created_at,
            }

    def _fallback_campaign_type_rows(self, type_id: str | None = None):
        """Read campaign_types using only columns that exist in older DBs."""
        cols = self.db.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'campaign_types'"
            )
        ).all()
        existing = {c[0] for c in cols}
        select_parts = [
            "id",
            "type_name",
            "description" if "description" in existing else "NULL::text AS description",
            "created_at" if "created_at" in existing else "NOW() AS created_at",
            "updated_at" if "updated_at" in existing else "NULL::timestamp AS updated_at",
        ]
        sql = "SELECT " + ", ".join(select_parts) + " FROM campaign_types"
        # Multi-company isolation (Group I): campaign_types is an owned table but
        # this legacy-DB fallback uses raw text() which bypasses the ORM scope
        # filter - reproduce the four-state predicate by hand.
        from app.services.company_scope_sql import company_sql_predicate

        company_frag, company_params = company_sql_predicate(self.db)
        params = dict(company_params)
        clauses = []
        if type_id is not None:
            clauses.append("id = :type_id")
            params["type_id"] = type_id
        if company_frag:
            clauses.append(company_frag)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        return self.db.execute(text(sql), params).all()
    
    def create_campaign_type(self, type_data: CampaignTypeCreate):
        """Create a new campaign type."""
        existing = self.db.query(CampaignType).filter(
            CampaignType.type_code == type_data.type_code
        ).first()
        if existing:
            raise handle_conflict("Campaign type code already exists.")
        
        campaign_type = CampaignType(**type_data.model_dump())
        self.db.add(campaign_type)
        self.db.commit()
        self.db.refresh(campaign_type)
        return campaign_type
    
    def update_campaign_type(self, type_id: str, type_data: CampaignTypeUpdate):
        """Update a campaign type."""
        campaign_type = self.get_campaign_type(type_id)

        update_data = type_data.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(campaign_type, key, value)

        self.db.commit()
        self.db.refresh(campaign_type)
        return campaign_type

    def delete_campaign_type(self, type_id: str):
        """Hard-delete a campaign type.

        Blocks with 409 when any campaign still references the type (the
        ``marketing_campaigns.campaign_type_id`` FK is NOT NULL - cascading
        would silently break those campaigns), per ADR hard-delete standard.
        """
        campaign_type = (
            self.db.query(CampaignType).filter(CampaignType.id == type_id).first()
        )
        if not campaign_type:
            raise handle_not_found("Campaign Type", type_id)

        in_use = (
            self.db.query(MarketingCampaign)
            .filter(MarketingCampaign.campaign_type_id == type_id)
            .count()
        )
        if in_use:
            raise handle_conflict(
                f"Campaign type is in use by {in_use} campaign(s) and cannot be deleted."
            )

        self.db.delete(campaign_type)
        self.db.commit()
        return {"message": "Campaign type deleted successfully"}


class MarketingCampaignService:
    """Service for marketing campaign operations."""
    
    def __init__(self, db: Session):
        self.db = db
    
    def list_campaigns(self, page: int = 1, limit: int = 50, status: str | None = None):
        """List marketing campaigns, optionally filtered by status.

        Status is normalised to canonical LOWERCASE (matching the DB CHECK
        constraint) so any-cased FE value still matches stored rows.
        ``None`` / ``"all"`` means no filter.
        """
        q = self.db.query(MarketingCampaign).order_by(MarketingCampaign.created_at.desc())

        if status and status.strip().lower() != "all":
            q = q.filter(MarketingCampaign.status == status.strip().lower())

        total = q.count()
        offset = (page - 1) * limit
        campaigns = q.offset(offset).limit(limit).all()
        
        return {
            "data": campaigns,
            "pagination": {"total": total, "page": page, "limit": limit},
            "empty": total == 0
        }
    
    def get_campaign(self, campaign_id: str):
        """Get a campaign by ID."""
        campaign = self.db.query(MarketingCampaign).filter(MarketingCampaign.id == campaign_id).first()
        if not campaign:
            raise handle_not_found("Marketing Campaign", campaign_id)
        return campaign
    
    def create_campaign(self, campaign_data: MarketingCampaignCreate, created_by: str):
        """Create a new marketing campaign."""
        existing = self.db.query(MarketingCampaign).filter(
            MarketingCampaign.campaign_code == campaign_data.campaign_code
        ).first()
        if existing:
            raise handle_conflict("Campaign code already exists.")
        
        campaign_dict = campaign_data.model_dump()
        campaign_dict["created_by"] = created_by
        campaign = MarketingCampaign(**campaign_dict)
        self.db.add(campaign)
        self.db.commit()
        self.db.refresh(campaign)
        return campaign
    
    def update_campaign(self, campaign_id: str, campaign_data: MarketingCampaignUpdate):
        """Update a marketing campaign."""
        campaign = self.get_campaign(campaign_id)
        
        update_data = campaign_data.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(campaign, key, value)

        self.db.commit()
        self.db.refresh(campaign)
        return campaign

    def delete_campaign(self, campaign_id: str):
        """Hard-delete a marketing campaign (per ADR hard-delete standard)."""
        campaign = self.get_campaign(campaign_id)
        self.db.delete(campaign)
        self.db.commit()
        return {"message": "Campaign deleted successfully"}


class PromotionAttachmentService:
    """Service for promotion attachment operations."""
    
    def __init__(self, db: Session):
        self.db = db
    
    def list_promotion_attachments(
        self,
        page: int = 1,
        limit: int = 50,
        sort_field: str = "created_at",
        sort_dir: str = "asc",
        promotion_id: Optional[str] = None,
        attachment_id: Optional[str] = None,
        query: Optional[str] = None,
        contact_access_codes: Optional[list[str]] = None,
        entities: Optional[list[str]] = None,
        promotion_ids: Optional[list[str]] = None,
        attachment_ids: Optional[list[str]] = None,
        active: Optional[bool] = None,
        serving_policy: bool = False,
    ):
        """List promotion attachments with pagination and filtering.

        `serving_policy=True` replaces the parent-promotion active gate with the
        per-type serving policy (see `app/services/promotion_serving.py`).

        When *contact_access_codes* is supplied the result is restricted to attachments
        whose underlying file's ``access_levels`` overlaps the contact's codes (and whose
        parent promotion's ``access_levels`` also overlaps). ``contact_access_codes=[]``
        returns nothing (contact has no assigned access types).

        ``active`` gates on the parent promotion's active state. active=None: no gate
        (full catalog - FE DataGrid default). active=True: attachments of active promotions
        first; if a narrowing filter is present and zero match, fall back to attachments of
        inactive promotions (``fallback_used=true``). active=False: only inactive-promotion
        attachments, no fallback. The MCP/API-key route defaults the missing param to True
        so chatbot callers get active-first + fallback. Active = Promotion.is_active and
        today within start/end (or no window) - same definition as the promotions list.
        """
        from sqlalchemy.orm import joinedload
        from sqlalchemy import or_
        from app.schemas.common import PaginationResponse
        from app.services.entity_filter_helpers import resolve_or_empty as _resolve_or_empty

        _entity_buckets = _resolve_or_empty(self.db, entities)
        if _entity_buckets is not None and not _entity_buckets.has_resolved_filter:
            return {
                "data": [],
                "pagination": PaginationResponse(total=0, page=page, limit=limit),
                "empty": True,
                "resolved_entities": _entity_buckets.as_echo(),
            }
        if _entity_buckets is not None and _entity_buckets.promotion_ids and not promotion_id:
            # Use first resolved promotion as the scope filter.
            promotion_id = _entity_buckets.promotion_ids[0]

        q = self.db.query(PromotionAttachment).options(
            joinedload(PromotionAttachment.promotion),
            joinedload(PromotionAttachment.attachment).joinedload(Attachment.attachment_type)
        )

        if contact_access_codes is not None:
            if not contact_access_codes:
                q = q.filter(text("false"))
            else:
                codes_arr = cast(contact_access_codes, ARRAY(String))
                q = q.filter(
                    PromotionAttachment.promotion.has(
                        Promotion.access_levels.op("?|")(codes_arr)
                    ),
                    PromotionAttachment.attachment.has(
                        Attachment.access_levels.op("?|")(codes_arr)
                    ),
                )

        fallback_query = query
        if promotion_id:
            resolved_pid = _resolve_promotion_id_for_filter(self.db, promotion_id)
            if resolved_pid is None:
                # If caller passed a product/promo search token in promotion_id by mistake,
                # treat it as a text search instead of returning empty.
                fallback_query = str(promotion_id).strip()
            else:
                q = q.filter(PromotionAttachment.promotion_id == resolved_pid)
        if attachment_id:
            q = q.filter(PromotionAttachment.attachment_id == attachment_id)
        if promotion_ids:
            q = q.filter(PromotionAttachment.promotion_id.in_(promotion_ids))
        if attachment_ids:
            q = q.filter(PromotionAttachment.attachment_id.in_(attachment_ids))
        if fallback_query:
            term = f"%{fallback_query.strip()}%"
            q = (
                q.join(Promotion, PromotionAttachment.promotion_id == Promotion.id)
                .outerjoin(PromotionProduct, PromotionProduct.promotion_id == Promotion.id)
                .outerjoin(Product, PromotionProduct.product_id == Product.id)
                .outerjoin(PromotionGroup, PromotionProduct.promotion_group_id == PromotionGroup.id)
                .outerjoin(Attachment, PromotionAttachment.attachment_id == Attachment.id)
                .filter(
                    or_(
                        Promotion.description.ilike(term),
                        Product.product_code.ilike(term),
                        Product.product_name.ilike(term),
                        PromotionGroup.group_name.ilike(term),
                        Attachment.original_filename.ilike(term),
                        Attachment.description.ilike(term),
                    )
                )
            )
        
        # Active gate on parent promotion state (mirrors the promotions list).
        #   active=None  -> no gate (full catalog; FE DataGrid default)
        #   active=True  -> active promotions first; fall back to inactive when a
        #                   narrowing filter is present and zero active match
        #   active=False -> inactive-promotion attachments only (no fallback)
        # Gate via `.promotion.has(...)` so we don't add a second Promotion join
        # (the fallback_query block above already joins it).
        narrowing_filter_present = bool(
            fallback_query
            or promotion_id
            or attachment_id
            or promotion_ids
            or attachment_ids
            or contact_access_codes is not None
        )
        today = datetime.utcnow().date()
        active_has = PromotionAttachment.promotion.has(_promotion_active_clause(today))
        fallback_used = False
        serving_verdict = None
        if serving_policy:
            parent_ids = [
                str(row[0])
                for row in q.with_entities(PromotionAttachment.promotion_id)
                .distinct()
                .limit(promotion_serving.CANDIDATE_CAP + 1)
                .all()
            ]
            serving_verdict = promotion_serving.evaluate_candidates(self.db, parent_ids, today)
            q_final = q.filter(
                PromotionAttachment.promotion_id.in_(list(serving_verdict.served_ids))
            )
            total = q_final.count()
        elif active is None:
            q_final = q
            total = q_final.count()
        elif active is False:
            q_final = q.filter(~active_has)
            total = q_final.count()
        else:
            q_final = q.filter(active_has)
            total = q_final.count()
            if total == 0 and narrowing_filter_present:
                q_fallback = q.filter(~active_has)
                fb_total = q_fallback.count()
                if fb_total > 0:
                    q_final, total, fallback_used = q_fallback, fb_total, True

        # Sorting
        sort_map = {
            "created_at": PromotionAttachment.created_at,
            "sort_order": PromotionAttachment.sort_order,
        }
        sort_column = sort_map.get(sort_field, PromotionAttachment.created_at)
        if sort_dir == "desc":
            q_final = q_final.order_by(sort_column.desc())
        else:
            q_final = q_final.order_by(sort_column.asc())

        offset = (page - 1) * limit
        promotion_attachments = q_final.offset(offset).limit(limit).all()

        attachment_type_ids = [
            getattr(pa.promotion, "promotion_type_id", None) for pa in promotion_attachments
        ]
        attachment_type_labels = _promotion_type_labels(self.db, attachment_type_ids)
        attachment_default_labels = (
            _default_type_labels(self.db)
            if any(not t for t in attachment_type_ids)
            else (None, None)
        )
        for pa in promotion_attachments:
            # Row-level expiry of the parent promotion - mirrors the promotions /
            # promotion-products lists so MCP/n8n can say "found but expired".
            pa.is_expired = _promotion_is_expired(pa.promotion, today)
            code, name = _type_labels_for(
                getattr(pa.promotion, "promotion_type_id", None),
                attachment_type_labels,
                attachment_default_labels,
            )
            pa.promotion_type_code = code
            pa.promotion_type_name = name
            if serving_verdict is not None:
                pa.expired_but_usable = serving_verdict.is_expired_but_usable(pa.promotion_id)

        payload = {
            "data": promotion_attachments,
            "pagination": PaginationResponse(total=total, page=page, limit=limit),
            "empty": total == 0,
            "fallback_used": fallback_used,
        }
        if serving_policy:
            payload["serving_policy_applied"] = True
        if _entity_buckets is not None:
            payload["resolved_entities"] = _entity_buckets.as_echo()
        return payload

    def get_promotion_attachment(self, promotion_attachment_id: str):
        """Get a promotion attachment by ID."""
        from sqlalchemy.orm import joinedload
        promotion_attachment = self.db.query(PromotionAttachment).options(
            joinedload(PromotionAttachment.promotion),
            joinedload(PromotionAttachment.attachment).joinedload(Attachment.attachment_type)
        ).filter(PromotionAttachment.id == promotion_attachment_id).first()
        if not promotion_attachment:
            raise handle_not_found("Promotion Attachment", promotion_attachment_id)
        return promotion_attachment
    
    def create_promotion_attachment(self, promotion_attachment_data: PromotionAttachmentCreate, created_by: Optional[str] = None):
        """Create a new promotion attachment relationship."""
        # Check if relationship already exists
        existing = self.db.query(PromotionAttachment).filter(
            PromotionAttachment.promotion_id == promotion_attachment_data.promotion_id,
            PromotionAttachment.attachment_id == promotion_attachment_data.attachment_id
        ).first()
        if existing:
            raise handle_conflict("Promotion attachment relationship already exists.")
        
        attachment_dict = promotion_attachment_data.model_dump()
        if created_by:
            attachment_dict["created_by"] = created_by
        
        promotion_attachment = PromotionAttachment(**attachment_dict)
        self.db.add(promotion_attachment)
        self.db.commit()
        self.db.refresh(promotion_attachment)
        
        # Reload with relationships
        from sqlalchemy.orm import joinedload
        return self.db.query(PromotionAttachment).options(
            joinedload(PromotionAttachment.promotion),
            joinedload(PromotionAttachment.attachment).joinedload(Attachment.attachment_type)
        ).filter(PromotionAttachment.id == promotion_attachment.id).first()
    
    def update_promotion_attachment(self, promotion_attachment_id: str, promotion_attachment_data: PromotionAttachmentUpdate):
        """Update a promotion attachment relationship."""
        promotion_attachment = self.get_promotion_attachment(promotion_attachment_id)
        
        update_data = promotion_attachment_data.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(promotion_attachment, key, value)
        
        from datetime import datetime
        promotion_attachment.updated_at = datetime.now()
        
        self.db.commit()
        self.db.refresh(promotion_attachment)
        
        # Reload with relationships
        from sqlalchemy.orm import joinedload
        return self.db.query(PromotionAttachment).options(
            joinedload(PromotionAttachment.promotion),
            joinedload(PromotionAttachment.attachment).joinedload(Attachment.attachment_type)
        ).filter(PromotionAttachment.id == promotion_attachment.id).first()
    
    def delete_promotion_attachment(self, promotion_attachment_id: str):
        """Delete a promotion attachment relationship."""
        promotion_attachment = self.get_promotion_attachment(promotion_attachment_id)
        self.db.delete(promotion_attachment)
        self.db.commit()
        return {"message": "Promotion attachment deleted successfully"}
    
    def get_promotion_attachments_by_promotion(
        self,
        promotion_id: str,
        contact_access_codes: Optional[list[str]] = None,
    ):
        """Get all attachments for a specific promotion.

        When *contact_access_codes* is supplied, filters out the entire result if the
        parent promotion's ``access_levels`` does not overlap, then drops individual
        attachments whose own ``access_levels`` does not overlap.
        """
        from sqlalchemy.orm import joinedload

        resolved_pid = _resolve_promotion_id_for_filter(self.db, promotion_id)
        if resolved_pid is None:
            return []

        if contact_access_codes is not None:
            if not contact_access_codes:
                return []
            promotion = (
                self.db.query(Promotion)
                .filter(Promotion.id == resolved_pid)
                .first()
            )
            promo_levels = getattr(promotion, "access_levels", None) if promotion else None
            if not isinstance(promo_levels, list) or not set(contact_access_codes).intersection(promo_levels):
                return []

        promotion_attachments = self.db.query(PromotionAttachment).options(
            joinedload(PromotionAttachment.promotion),
            joinedload(PromotionAttachment.attachment).joinedload(Attachment.attachment_type)
        ).filter(PromotionAttachment.promotion_id == resolved_pid).order_by(
            PromotionAttachment.sort_order.asc().nulls_last(),
            PromotionAttachment.created_at.asc()
        ).all()
        if contact_access_codes is not None:
            promotion_attachments = PromotionService._filter_attachments_by_codes(
                promotion_attachments, contact_access_codes
            )
        return promotion_attachments
