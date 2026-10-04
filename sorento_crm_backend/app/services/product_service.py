"""Product service for business logic."""
from datetime import date, datetime, time, timedelta, timezone
import logging
import re
import uuid
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import or_, and_, func
from typing import Any, Optional, List, Callable, Tuple, Iterable
from decimal import Decimal
from app.models.product import Product, ProductCategory, Brand, UnitOfMeasure, ProductAttachment
from app.models.product_set import ProductSet, ProductSetMember
from app.services.sla_service import MALAYSIA_TZ

logger = logging.getLogger(__name__)


def _populate_field_attachments(db: Session, products: Iterable[Product]) -> None:
    """Attach a transient ``field_attachments`` dict to each product.

    Pydantic ``ProductResponse`` reads it via ``from_attributes=True``. The map
    has shape ``{field_key: [AttachmentSimple-shaped dict, ...]}`` and is set
    only when at least one attachment is linked to a field on the row.
    """
    from app.services.attachment_field_link_service import AttachmentFieldLinkService
    from app.schemas.product import AttachmentSimple

    rows = list(products)
    ids = [str(p.id) for p in rows if getattr(p, "id", None)]
    if not ids:
        return
    by_row = AttachmentFieldLinkService(db).get_field_attachments_for_rows(
        "product", ids
    )
    if not by_row:
        for p in rows:
            try:
                setattr(p, "field_attachments", None)
            except Exception:
                pass
        return
    for p in rows:
        per_field = by_row.get(str(p.id))
        if not per_field:
            try:
                setattr(p, "field_attachments", None)
            except Exception:
                pass
            continue
        out: dict[str, list[dict]] = {}
        for field_key, atts in per_field.items():
            out[field_key] = [
                AttachmentSimple.model_validate(a).model_dump() for a in atts
            ]
        try:
            setattr(p, "field_attachments", out or None)
        except Exception:
            pass


# S1 (ingest-parity-standardisation): the dimension-parsing and discontinued-
# derivation bodies MOVED to app/services/rules/product_rules.py, shared by
# the xlsx import, the manual create/edit and the ESB push (D2, D4). These
# three names stay importable from here - unchanged callers elsewhere in this
# module and `tests/test_product_is_discontinued.py` - as thin delegates; the
# one real body lives in product_rules.
from app.services.rules import product_rules
from app.services.rules.master_rules import resolve_master_by_code
from app.services.rules.product_rules import (
    is_discontinued as _rules_is_discontinued,
    parse_dimensions as parse_dimensions_from_description,
)


def is_discontinued_from_description(description: Optional[str]) -> bool:
    """True when description starts with `****` (after lstrip). See
    `product_rules.is_discontinued`."""
    return _rules_is_discontinued(None, description)


def is_discontinued_from_row(row: dict, description: Optional[str]) -> bool:
    """Discontinued for one import row: explicit column wins, `****` is the
    fallback. See `product_rules.is_discontinued`.

    Some source files (e.g. the Mocha AutoCount item list) carry a real
    `Discontinued` checkbox column, where leading asterisks in the description
    are just a legacy naming style. Files without the column (Sorento) keep the
    leading-`****` description convention. A blank cell in a file that has the
    column falls back to the description rule too.
    """
    for key in ("is_discontinued", "Discontinued", "discontinued"):
        if key in row and row[key] is not None and str(row[key]).strip() != "":
            return _rules_is_discontinued(row[key], description)
    return _rules_is_discontinued(None, description)


#: Header spellings for the AutoCount reorder columns. Kept identical to the aliases
#: migration 347 seeds for the SCM reorder-level upload, so one file cannot be read two
#: ways by the two importers.
_REORDER_LEVEL_HEADERS = (
    "reorder_level", "Reorder Level", "Re-order Level", "ReorderLevel", "Min Level",
)
_REORDER_QTY_HEADERS = (
    "reorder_quantity", "reorder_qty", "Reorder Qty", "Reorder Quantity",
    "Re-order Qty", "ReorderQty",
)


class _Absent:
    """The column is not on this row at all - distinct from a blank cell."""

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<absent>"


#: Three answers, never two. A NULL reorder level means nobody set one (the SCM engine
#: emits the item as `needs_level`); a 0 is a real threshold it will plan against; and an
#: absent column means the file does not speak about levels at all. Collapsing any pair of
#: those is the whole class of bug this feature can produce.
ABSENT = _Absent()


class ReorderCellError(ValueError):
    """A reorder cell that carries something that is not a number."""


def reorder_cell(row: dict, headers: tuple[str, ...]):
    """`ABSENT` when no such column on this row, None when blank, else the int."""
    for key in headers:
        if key not in row:
            continue
        value = row[key]
        if value is None or str(value).strip() == "":
            return None
        try:
            # AutoCount exports these as floats ("250.0"). A fractional value rounds
            # rather than failing the row: a stocking threshold is not worth losing a
            # product master update over.
            return int(round(float(str(value).strip().replace(",", ""))))
        except (TypeError, ValueError):
            raise ReorderCellError(f"'{value}'")
    return ABSENT


def _blank_if_absent(cell):
    """Collapse ABSENT to blank, for a file already known to carry the column.

    Safe ONLY once `file_carries_reorder_column` has said yes. Before that, this is the
    conflation the tri-state exists to prevent.
    """
    return None if cell is ABSENT else cell


def file_carries_reorder_column(rows: list, headers: tuple[str, ...]) -> bool:
    """Does this upload speak about the column at all?

    Decided ONCE per file, not per row. The frontend parses the sheet with
    `sheet_to_json`, which OMITS blank cells, so per row "the column is not in this file"
    and "the cell is blank" are the same dict. Since a blank cell CLEARS a held value, a
    file that carries the column but no values anywhere is indistinguishable from one that
    never mentioned it - and is therefore treated as not mentioning it. The alternative is
    that one such upload silently clears every reorder level in the system.
    """
    for row in rows:
        try:
            if reorder_cell(row, headers) not in (ABSENT, None):
                return True
        except ReorderCellError:
            # A junk value still proves the column is present; the row itself is rejected
            # later, where the error can be reported against its row number.
            return True
    return False


from app.models.procurement import ProductSupplier, Supplier
from app.models.resources import Attachment, AttachmentType
from app.schemas.product import (
    ProductCreate, ProductUpdate, ProductCategoryCreate, ProductCategoryUpdate,
    BrandCreate, BrandUpdate, UnitOfMeasureCreate, UnitOfMeasureUpdate,
    ProductAttachmentCreate, ProductAttachmentUpdate, ProductBulkUpdates
)
from app.services.error_handler import handle_not_found, handle_conflict, AppException
from app.services.company_scope import stamp_lookup_companies
from app.schemas.common import PaginationResponse
from app.models.user import SystemSetting
from app.services.embedding_events import publish_embedding_event
from app.services.identifier_resolver import is_uuid, resolve_identifier


# Lifted out of `bulk_import_products` (PLAN-autocount-pull-review.md, P11) so the
# AutoCount pull's "Compare with my Excel" tab (`autocount_pull_compare.py`) applies the
# SAME rules the manual import applies, rather than a second copy that can drift.

def join_description_and_desc2(description: Optional[str], desc2: Optional[str]) -> str:
    """The manual import's Desc 2 join: Description + a single space + Desc 2, stripped.
    A blank Desc 2 leaves Description untouched."""
    description = description or ""
    if desc2:
        return f"{description} {desc2}".strip()
    return description


def parse_manual_list_price(raw_price) -> Decimal:
    """The manual import's Price parse: a blank cell reads as 0; a negative value (the
    AutoCount "no price" sentinel) is clamped to 0. Raises on anything that is not a
    number - the caller decides what a bad row means for it."""
    if raw_price is None or str(raw_price).strip() == "":
        return Decimal("0")
    price = Decimal(str(raw_price))
    if price < 0:
        price = Decimal("0")
    return price


def is_active_from_manual_value(raw_active) -> bool:
    """The manual import's Is Active rule: false only for the spellings
    F/FALSE/0/N/NO (case-insensitive); everything else, including blank, is active."""
    if raw_active is not None and str(raw_active).strip().upper() in ("F", "FALSE", "0", "N", "NO"):
        return False
    return True


def malaysia_day_start_utc(day: date) -> datetime:
    """Midnight of `day` in Malaysia time (UTC+8), converted to naive UTC - the
    same representation `discontinued_notified_at` (and other naive-UTC columns)
    are stored in. Used to turn a `discontinued_from`/`discontinued_to` calendar
    day into a bound comparable against that column."""
    return datetime.combine(day, time.min, tzinfo=MALAYSIA_TZ).astimezone(timezone.utc).replace(tzinfo=None)


class ProductService:
    """Service for product operations."""
    
    def __init__(self, db: Session):
        self.db = db
    
    # Sentinel: a filter resolved to a definitively-empty set (e.g. an unknown
    # category/brand identifier, or `entities` that matched no product codes).
    # `_build_list_query` returns this instead of a Query so callers short-circuit
    # to an empty result without hitting the DB.
    _EMPTY_RESULT = object()

    def _resolve_brand_filter(self, brand_id: Optional[str]) -> Optional[list[str]]:
        """Brand ids for the ``brand_id`` filter, which accepts a comma-separated list.

        The product-discontinued notice deep-links a recipient to exactly their
        slice of a batch, and that slice can span several brands. One value keeps
        the single-identifier path it always had (UUID, brand code or brand name);
        several resolve individually and union, so an unknown id among known ones
        narrows nothing rather than emptying the whole list.

        Returns ``None`` for "no filter" and ``[]`` for "definitively no rows",
        exactly as :func:`resolve_identifier` does.
        """
        raw = (brand_id or "").strip()
        if "," not in raw:
            return resolve_identifier(
                self.db, brand_id, Brand, code_fields=("brand_code", "brand_name")
            )

        tokens = [t.strip() for t in raw.split(",") if t.strip()]
        if not tokens:
            return None

        resolved: list[str] = []
        seen: set[str] = set()
        # Codes and names resolve in ONE query rather than one per token: the deep
        # link from a discontinued notice can carry a dozen brands, and a query per
        # brand is a query per brand on every page of that list.
        lookups: list[str] = []
        for token in tokens:
            # "all" resolves to no ids at all (resolve_identifier returns None for
            # it), so it contributes nothing here either.
            if token.lower() == "all":
                continue
            if is_uuid(token):
                if token not in seen:
                    seen.add(token)
                    resolved.append(token)
            else:
                lowered = token.lower()
                if lowered not in lookups:
                    lookups.append(lowered)

        if lookups:
            rows = (
                self.db.query(Brand.id)
                .filter(
                    or_(
                        func.lower(Brand.brand_code).in_(lookups),
                        func.lower(Brand.brand_name).in_(lookups),
                    )
                )
                .all()
            )
            for row in rows:
                bid = str(row[0])
                if bid not in seen:
                    seen.add(bid)
                    resolved.append(bid)
        return resolved

    def _build_list_query(
        self,
        query: Optional[str] = None,
        category_id: Optional[str] = None,
        brand_id: Optional[str] = None,
        status: Optional[str] = None,
        price_min: Optional[float] = None,
        price_max: Optional[float] = None,
        item_type: Optional[str] = None,
        length_min: Optional[float] = None,
        length_max: Optional[float] = None,
        width_min: Optional[float] = None,
        width_max: Optional[float] = None,
        height_min: Optional[float] = None,
        height_max: Optional[float] = None,
        any_dimension_min: Optional[float] = None,
        any_dimension_max: Optional[float] = None,
        sort_field: str = "created_at",
        sort_dir: str = "asc",
        advanced_filter_clause: Optional[Any] = None,
        entity_buckets: Optional[Any] = None,
        product_ids: Optional[list[str]] = None,
        discontinued_batch_id: Optional[str] = None,
        variant_filter: Optional[str] = None,
        discontinued_from: Optional[date] = None,
        discontinued_to: Optional[date] = None,
    ):
        """Build the filtered + sorted products query shared by ``list_products``
        and ``neighbours`` so the two can never drift.

        Returns the SQLAlchemy ``Query`` (already filtered + ordered), or
        :data:`_EMPTY_RESULT` when a filter resolves to a definitively empty set
        (unknown category/brand id, or `entity_buckets` with no product codes).

        The ORDER BY always appends ``Product.id`` as a deterministic tie-breaker
        so offset position and prev/next neighbours are unambiguous when the
        primary sort column (or its NULLS) has equal values.
        """
        q = self.db.query(Product)

        filters = []
        if product_ids:
            filters.append(Product.id.in_(product_ids))
        if entity_buckets is not None:
            if not getattr(entity_buckets, "product_codes", None):
                return self._EMPTY_RESULT
            from sqlalchemy import func as _func
            lowered = [c.lower() for c in entity_buckets.product_codes]
            filters.append(_func.lower(Product.product_code).in_(lowered))

        category_ids = resolve_identifier(
            self.db,
            category_id,
            ProductCategory,
            code_fields=("category_code", "category_name"),
        )
        if category_ids is not None:
            if not category_ids:
                return self._EMPTY_RESULT
            filters.append(Product.category_id.in_(category_ids))

        brand_ids = self._resolve_brand_filter(brand_id)
        if brand_ids is not None:
            if not brand_ids:
                return self._EMPTY_RESULT
            filters.append(Product.brand_id.in_(brand_ids))

        if status and status != "all":
            filters.append(Product.is_active == (status == "active"))

        # Base / Variant / All variant-graph filter.
        if variant_filter == "base":
            filters.append(Product.variant_of_id.is_(None))
        elif variant_filter == "variant":
            filters.append(Product.variant_of_id.isnot(None))

        if item_type:
            filters.append(Product.item_type == item_type)

        # Deep link from a "products discontinued" notification: show exactly the
        # products reported in that batch (see product_discontinued_notify_service).
        if discontinued_batch_id:
            filters.append(Product.discontinued_notify_batch_id == discontinued_batch_id)

        # "Discontinued at" date-range filter (issue #1287): inclusive by Malaysia
        # calendar day, either bound set also excludes null (never-discontinued) rows.
        if discontinued_from is not None:
            filters.append(
                Product.discontinued_notified_at >= malaysia_day_start_utc(discontinued_from)
            )
        if discontinued_to is not None:
            filters.append(
                Product.discontinued_notified_at
                < malaysia_day_start_utc(discontinued_to + timedelta(days=1))
            )

        if price_min or price_max:
            price_filters = []
            if price_min:
                price_filters.append(Product.list_price >= Decimal(str(price_min)))
            if price_max:
                price_filters.append(Product.list_price <= Decimal(str(price_max)))
            filters.append(and_(*price_filters))

        # Per-axis dimension filters (mm). Length/width/height map to dimensions_length/width/height.
        if length_min is not None:
            filters.append(Product.dimensions_length >= Decimal(str(length_min)))
        if length_max is not None:
            filters.append(Product.dimensions_length <= Decimal(str(length_max)))
        if width_min is not None:
            filters.append(Product.dimensions_width >= Decimal(str(width_min)))
        if width_max is not None:
            filters.append(Product.dimensions_width <= Decimal(str(width_max)))
        if height_min is not None:
            filters.append(Product.dimensions_height >= Decimal(str(height_min)))
        if height_max is not None:
            filters.append(Product.dimensions_height <= Decimal(str(height_max)))

        # Generic "any dimension" filter: matches when ANY of L/W/H is in the range.
        # Use this when the user does not care which axis (e.g. 'dimensions > 300mm').
        if any_dimension_min is not None:
            v = Decimal(str(any_dimension_min))
            filters.append(
                or_(
                    Product.dimensions_length >= v,
                    Product.dimensions_width >= v,
                    Product.dimensions_height >= v,
                )
            )
        if any_dimension_max is not None:
            v = Decimal(str(any_dimension_max))
            filters.append(
                or_(
                    Product.dimensions_length <= v,
                    Product.dimensions_width <= v,
                    Product.dimensions_height <= v,
                )
            )

        if query:
            term = f"%{query.strip()}%"
            filters.append(
                or_(
                    Product.product_code.ilike(term),
                    Product.product_name.ilike(term),
                    Product.description.ilike(term),
                )
            )

        if advanced_filter_clause is not None:
            filters.append(advanced_filter_clause)

        if filters:
            q = q.filter(and_(*filters))

        # Apply sorting
        # GREATEST / LEAST give us a virtual "max axis" / "min axis" per row so the LLM can ask
        # for the biggest or smallest product in one call (sort=largest_dimension dir=desc).
        # NULL dimensions sort to the bottom on desc and top on asc - handled with NULLS LAST/FIRST.
        largest_dim = func.greatest(
            Product.dimensions_length,
            Product.dimensions_width,
            Product.dimensions_height,
        )
        smallest_dim = func.least(
            Product.dimensions_length,
            Product.dimensions_width,
            Product.dimensions_height,
        )
        sort_map = {
            "created_at": Product.created_at,
            "updated_at": Product.updated_at,
            "product_code": Product.product_code,
            "product_name": Product.product_name,
            "list_price": Product.list_price,
            "price": Product.list_price,
            "cost_price": Product.cost_price,
            "invoice_price": Product.invoice_price,
            "is_active": Product.is_active,
            "dimensions_length": Product.dimensions_length,
            "length": Product.dimensions_length,
            "dimensions_width": Product.dimensions_width,
            "width": Product.dimensions_width,
            "dimensions_height": Product.dimensions_height,
            "height": Product.dimensions_height,
            "largest_dimension": largest_dim,
            "smallest_dimension": smallest_dim,
            "discontinued_at": Product.discontinued_notified_at,
        }
        sort_column = sort_map.get(sort_field, Product.created_at)
        if sort_dir == "desc":
            q = q.order_by(sort_column.desc().nulls_last(), Product.id.asc())
        else:
            q = q.order_by(sort_column.asc().nulls_last(), Product.id.asc())
        return q

    def list_products(
        self,
        page: int = 1,
        limit: int = 50,
        query: Optional[str] = None,
        category_id: Optional[str] = None,
        brand_id: Optional[str] = None,
        status: Optional[str] = None,
        price_min: Optional[float] = None,
        price_max: Optional[float] = None,
        item_type: Optional[str] = None,
        length_min: Optional[float] = None,
        length_max: Optional[float] = None,
        width_min: Optional[float] = None,
        width_max: Optional[float] = None,
        height_min: Optional[float] = None,
        height_max: Optional[float] = None,
        any_dimension_min: Optional[float] = None,
        any_dimension_max: Optional[float] = None,
        sort_field: str = "created_at",
        sort_dir: str = "asc",
        advanced_filter_clause: Optional[Any] = None,
        entities: Optional[list[str]] = None,
        product_ids: Optional[list[str]] = None,
        discontinued_batch_id: Optional[str] = None,
        variant_filter: Optional[str] = None,
        discontinued_from: Optional[date] = None,
        discontinued_to: Optional[date] = None,
    ):
        """List products with filtering and pagination.

        `entities` is the single-bag free-text filter for AI/MCP callers. Product
        matches narrow Product.product_code; other resolved types echo back but
        do not filter (products are keyed by product master).
        """
        from app.services.entity_resolver import (
            EntityFilterBuckets,
            resolve_entities_to_filters,
        )

        entity_buckets: Optional[EntityFilterBuckets] = None
        if entities:
            entity_buckets = resolve_entities_to_filters(
                self.db,
                entities,
                allowed_entity_types=(
                    "product", "customer", "customer_order", "transporter",
                    "inbound_shipment", "spo_allocation", "grn", "promotion",
                    "attachment", "form", "supplier",
                ),
            )
            if not entity_buckets.product_codes:
                payload = {
                    "data": [],
                    "pagination": {"total": 0, "page": page, "limit": limit},
                    "empty": True,
                    "resolved_entities": entity_buckets.as_echo(),
                }
                stamp_lookup_companies(self.db, payload, [], product_ids=product_ids)
                return payload

        q = self._build_list_query(
            query=query,
            category_id=category_id,
            brand_id=brand_id,
            status=status,
            price_min=price_min,
            price_max=price_max,
            item_type=item_type,
            length_min=length_min,
            length_max=length_max,
            width_min=width_min,
            width_max=width_max,
            height_min=height_min,
            height_max=height_max,
            any_dimension_min=any_dimension_min,
            any_dimension_max=any_dimension_max,
            sort_field=sort_field,
            sort_dir=sort_dir,
            advanced_filter_clause=advanced_filter_clause,
            entity_buckets=entity_buckets,
            product_ids=product_ids,
            discontinued_batch_id=discontinued_batch_id,
            variant_filter=variant_filter,
            discontinued_from=discontinued_from,
            discontinued_to=discontinued_to,
        )
        if q is self._EMPTY_RESULT:
            payload = {
                "data": [],
                "pagination": {"total": 0, "page": page, "limit": limit},
                "empty": True,
            }
            stamp_lookup_companies(self.db, payload, [], product_ids=product_ids)
            if entity_buckets is not None:
                payload["resolved_entities"] = entity_buckets.as_echo()
            self._attach_product_alternatives(payload, query)
            return payload

        # Get total count
        total = q.count()

        # Apply pagination
        offset = (page - 1) * limit
        # Eager-load relations referenced by ProductResponse to avoid N+1 lazy loads
        # during Pydantic serialization.
        products = (
            q.options(
                joinedload(Product.category),
                joinedload(Product.brand),
                joinedload(Product.base_uom),
            )
            .offset(offset)
            .limit(limit)
            .all()
        )
        _populate_field_attachments(self.db, products)
        self._populate_list_variant_fields(products)

        payload = {
            "data": products,
            "pagination": {
                "total": total,
                "page": page,
                "limit": limit
            },
            "empty": total == 0
        }
        # Per-company labelling when the lookup spans more than one company - on the
        # empty path too, so an empty answer can name the companies searched.
        stamp_lookup_companies(self.db, payload, products, product_ids=product_ids)
        if entity_buckets is not None:
            payload["resolved_entities"] = entity_buckets.as_echo()
        if total == 0:
            self._attach_product_alternatives(payload, query)
        return payload

    def specifications_for_products(self, product_ids: List[str]) -> dict:
        """Derived spec blocks for a page of products, keyed by product id.

        ONE query for the whole page (an IN over the ids already fetched), never
        one per row: this rides the listing path, where a per-row read would turn
        a 50-row page into 51 queries for an opt-in field.

        A product with no derived row is simply absent from the map. The caller
        renders that as an explicit null, because "we have not derived this yet"
        is a fact worth stating and an omitted key reads as a field the caller
        forgot to ask for.
        """
        from app.models.product_spec import ProductSpecifications
        from app.services.product_spec_search import values_only

        ids = [str(pid) for pid in product_ids if pid]
        if not ids:
            return {}

        rows = (
            self.db.query(ProductSpecifications)
            .filter(ProductSpecifications.product_id.in_(ids))
            .all()
        )
        return {
            str(row.product_id): {
                "values": values_only(row.values),
                "rendered_text": row.rendered_text,
                # WHERE each value came from: a flyer-stated spec and a value
                # guessed out of a description are not the same claim, and a
                # human-confirmed one outranks both.
                "sources": {
                    key: entry["source"]
                    for key, entry in (row.provenance or {}).items()
                    if isinstance(entry, dict) and entry.get("source")
                },
            }
            for row in rows
        }

    def spec_list_for_products(self, product_ids: List[str]) -> dict:
        """A1 (chatbot-growth-r1, AC-901/AC-902): the spec block as a RANKED LIST,
        `{product_id: [{key, label, value, unit, rank_weight}, ...]}`, ordered by
        `rank_weight` desc then `label` - the shape the MCP presenter needs to build
        one field per populated key AND its own synonym-matched projection, neither
        of which the `values`-dict shape `specifications_for_products` returns can
        do (no label, no unit, no ordering).

        Only POPULATED keys - a key present in the registry but not derived for this
        product is simply absent from its list, same "absence is a fact" rule as
        `specifications_for_products`.
        """
        from app.models.product_spec import ProductSpecifications, ProductSpecRegistry
        from app.services.product_spec_registry import display_spec_value

        ids = [str(pid) for pid in product_ids if pid]
        if not ids:
            return {}

        rows = (
            self.db.query(ProductSpecifications)
            .filter(ProductSpecifications.product_id.in_(ids))
            .all()
        )
        if not rows:
            return {}

        registry = {r.spec_key: r for r in self.db.query(ProductSpecRegistry).all()}

        out: dict[str, list[dict]] = {}
        for row in rows:
            specs: list[dict] = []
            for key, entry in (row.values or {}).items():
                if not isinstance(entry, dict) or entry.get("value") is None:
                    continue
                reg = registry.get(key)
                if reg is None:
                    continue
                specs.append(
                    {
                        "key": key,
                        "label": reg.label,
                        "value": entry["value"],
                        # R7 (round 4 on PR #833): the value in plain words, off the
                        # registry's own `value_labels`, for every reader that shows it.
                        # A LIST value (two finishes on one product) reads as its values
                        # joined (PR #833 round 5 N1).
                        "display_value": (
                            " / ".join(display_spec_value(v, reg.value_labels) for v in entry["value"])
                            if isinstance(entry["value"], list)
                            else display_spec_value(entry["value"], reg.value_labels)
                        ),
                        "unit": entry.get("unit") or reg.unit,
                        "rank_weight": float(reg.rank_weight) if reg.rank_weight is not None else 1.0,
                    }
                )
            specs.sort(key=lambda s: (-s["rank_weight"], s["label"]))
            out[str(row.product_id)] = specs
        return out

    def _attach_product_alternatives(self, payload: dict, input_code: Optional[str]) -> None:
        """Attach trigram/graph sibling-product alternatives to an empty listing.

        Best-effort (§3.3, entity axis): a suggestion probe must never turn a
        legitimately-empty listing into a 500 (AC-R1). Only mutates `payload` when
        real data-bearing neighbours are found.
        """
        try:
            alternatives = self._product_entity_alternatives(input_code)
        except Exception:
            import logging
            logging.getLogger(__name__).warning(
                "product alternatives probe failed", exc_info=True
            )
            alternatives = None
        if alternatives:
            payload["alternatives"] = alternatives
            payload["relaxed_axis"] = "entity"

    def _product_entity_alternatives(self, input_code: Optional[str]) -> list[dict]:
        """Trigram/graph sibling products (existing + priced) for a row-miss.

        Fires on a genuine ``total == 0`` - a `query` code that matched no product
        row. The neighbour helper's input product does not exist, so it falls to
        trigram recall (§3.1 "did you mean") over EXISTING siblings. The has-data
        gate = candidate has a non-null, positive ``list_price`` so a price question
        gets a priced neighbour.

        Deliberately NOT called when a product resolves but carries ``list_price``
        0 - that is a field-level miss, and substituting a different SKU's price is
        misleading (a variant's price is legitimately different). Only the row-miss
        (nothing matched) path reaches here.
        """
        code = (input_code or "").strip()
        if not code:
            return []

        def _is_priced(candidate_ids: list[str]) -> set[str]:
            if not candidate_ids:
                return set()
            rows = (
                self.db.query(Product.id)
                .filter(
                    Product.id.in_(candidate_ids),
                    Product.list_price.isnot(None),
                    Product.list_price > 0,
                )
                .all()
            )
            return {str(row.id) for row in rows}

        from app.services.entity_resolver import find_entity_neighbours_with_data

        return find_entity_neighbours_with_data(
            self.db, code, has_data=_is_priced
        )

    def get_product(self, product_id: str):
        """Get a single product by UUID or product_code (SKU)."""
        resolved_ids = resolve_identifier(
            self.db,
            product_id,
            Product,
            code_fields=("product_code",),
        )
        if not resolved_ids:
            raise handle_not_found("Product", product_id)
        product = (
            self.db.query(Product)
            .options(joinedload(Product.category), joinedload(Product.brand), joinedload(Product.base_uom))
            .filter(Product.id.in_(resolved_ids))
            .first()
        )
        if not product:
            raise handle_not_found("Product", product_id)
        _populate_field_attachments(self.db, [product])
        self._populate_variant_graph(product)
        self._populate_product_sets(product)
        return product

    def _populate_product_sets(self, product) -> None:
        """Stash the sets this product belongs to, for the detail serializer.

        A DETAIL-only populate, like the variant graph beside it: list rows never
        call this, so the listing stays free of an extra query per row. Every
        method that returns the detail shape calls both, or the field is present
        on one route and absent on the next for no reason a reader could guess.

        Reached through `ProductSet`, which carries `CompanyScopedMixin`, so the
        session's scope filters it and another company's set can never name this
        product. `ProductSetMember` is deliberately unscoped and is only ever read
        through its scoped parent - the same shape as `certificate_products`
        reached through `Certificate`.

        Ordered by set_code so two reads of one product agree; an unordered list
        reorders itself between reads and reads as a bug.
        """
        product._product_sets = (
            self.db.query(ProductSet)
            .join(ProductSetMember, ProductSetMember.product_set_id == ProductSet.id)
            .filter(ProductSetMember.product_id == product.id)
            .order_by(ProductSet.set_code.asc())
            .all()
        )

    def _populate_variant_graph(self, product) -> None:
        """Stash the variant-graph refs the detail serializer reads.

        Populates two throwaway instance attrs consumed by ProductResponse via
        `validation_alias` (`_variant_of_ref`, `_variant_children`) so the schema
        never touches the SQLAlchemy relationships directly - keeping LIST rows
        (which never call this) free of variant N+1s. See
        PLAN-suggest-on-miss-variant-graph.md §1.5.
        """
        parent = None
        if product.variant_of_id:
            parent = (
                self.db.query(Product)
                .filter(Product.id == product.variant_of_id)
                .first()
            )
        product._variant_of_ref = parent
        children = (
            self.db.query(Product)
            .filter(Product.variant_of_id == product.id)
            .order_by(Product.product_code.asc())
            .all()
        )
        product._variant_children = children
        product._variant_child_count = len(children)

    def set_variant_parent(self, product_id: str, parent_id: str, updated_by: str):
        """Manually set/change a product's variant parent (D1 - sticky override).

        Sets ``variant_of_id`` + ``variant_link_manual = True`` so auto-derivation
        never re-points it. Rejects self-parent and cycles (walking the chosen
        parent's ancestor chain). Returns the ORM product so the route serializes
        the full variant graph. Also the "attach a child" path: call this with the
        child's id and ``parent_id`` = this product.
        """
        product = self.get_product(product_id)  # 404 if the product is unknown
        if not parent_id or not str(parent_id).strip():
            raise AppException(
                status_code=400, message="parent_id is required", code="VALIDATION_ERROR"
            )
        resolved = resolve_identifier(
            self.db, parent_id, Product, code_fields=("product_code",)
        )
        parent = None
        if resolved:
            parent = self.db.query(Product).filter(Product.id.in_(resolved)).first()
        if parent is None:
            raise AppException(
                status_code=404, message="Parent product not found", code="NOT_FOUND"
            )
        if parent.id == product.id:
            raise AppException(
                status_code=400,
                message="A product cannot be a variant of itself",
                code="VALIDATION_ERROR",
            )
        # Cycle check - walk the chosen parent's ancestor chain via variant_of_id.
        # If `product` is encountered, linking would create a cycle. Visited-set
        # guards against a pre-existing cycle looping forever.
        visited: set[str] = set()
        cursor = parent
        while cursor is not None:
            if cursor.id == product.id:
                raise AppException(
                    status_code=400,
                    message="Cannot set parent: this would create a variant cycle",
                    code="VALIDATION_ERROR",
                )
            if cursor.id in visited:
                break
            visited.add(cursor.id)
            if not cursor.variant_of_id:
                break
            cursor = (
                self.db.query(Product)
                .filter(Product.id == cursor.variant_of_id)
                .first()
            )
        product.variant_of_id = parent.id
        product.variant_link_manual = True
        product.updated_by = updated_by
        product.updated_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(product)
        _populate_field_attachments(self.db, [product])
        self._populate_variant_graph(product)
        self._populate_product_sets(product)
        return product

    def unlink_variant(self, product_id: str, updated_by: str):
        """Manually unlink a product from its parent (D1 - sticky override).

        Nulls ``variant_of_id`` and sets ``variant_link_manual = True`` so
        auto-reconcile will not re-link it. Also the "remove a child" path: call
        with the child's id. Returns the ORM product.
        """
        product = self.get_product(product_id)  # 404 if the product is unknown
        product.variant_of_id = None
        product.variant_link_manual = True
        product.updated_by = updated_by
        product.updated_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(product)
        _populate_field_attachments(self.db, [product])
        self._populate_variant_graph(product)
        self._populate_product_sets(product)
        return product

    def reset_variant_auto(self, product_id: str):
        """Clear the manual override and re-derive this row's link from its code.

        Commits the flag clear first so the reset always persists even when the
        subsequent derivation yields no change, then runs the best-effort reconcile
        (which re-derives this row's parent AND adopts its orphans, committing its
        own unit of work). Never 500s a succeeded reset. Returns the ORM product.
        """
        product = self.get_product(product_id)  # 404 if the product is unknown
        product.variant_link_manual = False
        product.updated_at = datetime.utcnow()
        self.db.commit()
        self._reconcile_variant_links(product.id)
        self.db.refresh(product)
        _populate_field_attachments(self.db, [product])
        self._populate_variant_graph(product)
        self._populate_product_sets(product)
        return product

    def _populate_list_variant_fields(self, products: Iterable[Product]) -> None:
        """Stash per-list-row variant fields WITHOUT N+1 (two bounded IN-queries).

        Mirrors :func:`_populate_field_attachments`: after the page rows are
        fetched, one query resolves parent refs (human-readable "Variant of"
        column) and one aggregates direct-child counts. The detail path uses
        :meth:`_populate_variant_graph` instead. Read by ``ProductResponse`` via
        the ``_variant_of_ref`` / ``_variant_child_count`` validation aliases.
        """
        rows = list(products)
        # Defaults so the schema serializes cleanly on the base / childless path.
        for p in rows:
            try:
                p._variant_of_ref = None
                p._variant_child_count = 0
            except Exception:
                pass
        page_ids = [str(p.id) for p in rows if getattr(p, "id", None)]
        if not page_ids:
            return

        # 1) Parent refs - one IN-query over just the parents referenced on this page.
        parent_ids = {
            str(p.variant_of_id) for p in rows if getattr(p, "variant_of_id", None)
        }
        if parent_ids:
            parents = (
                self.db.query(Product)
                .filter(Product.id.in_(parent_ids))
                .all()
            )
            parent_by_id = {str(pr.id): pr for pr in parents}
            for p in rows:
                if getattr(p, "variant_of_id", None):
                    p._variant_of_ref = parent_by_id.get(str(p.variant_of_id))

        # 2) Direct-child counts - one grouped IN-query keyed on this page's ids.
        counts = (
            self.db.query(Product.variant_of_id, func.count(Product.id))
            .filter(Product.variant_of_id.in_(page_ids))
            .group_by(Product.variant_of_id)
            .all()
        )
        count_by_id = {str(pid): int(c) for pid, c in counts}
        for p in rows:
            p._variant_child_count = count_by_id.get(str(p.id), 0)

    def _system_settings_row(self) -> Optional[SystemSetting]:
        return self.db.query(SystemSetting).first()

    def _default_standard_lead_time_days(self) -> int:
        return product_rules.resolve_standard_lead_time_days(self._system_settings_row())

    def _resolve_default_supplier_for_new_product(self) -> Optional[Supplier]:
        """
        Supplier for auto-created product_suppliers rows.
        Uses system_settings.default_product_supplier_id when set and valid; else oldest supplier by created_at.
        """
        supplier_id = product_rules.resolve_default_supplier_id(self.db, self._system_settings_row())
        if not supplier_id:
            return None
        return self.db.query(Supplier).filter(Supplier.id == supplier_id).first()

    def _ensure_default_supplier_lead_time(self, product_id: str, lead_time_days: Optional[int] = None) -> None:
        """Link product to the configured default supplier with standard lead time (D5).

        S1 (ingest-parity-standardisation): delegates to `product_rules.link_default_supplier`,
        the same body the ESB push calls, so manual create/edit and the ESB never drift.
        `lead_time_days` is an explicit override for a caller that already knows the
        number (nothing left in this module passes one; kept so a future caller can
        without a second body materialising for it).
        """
        if lead_time_days is None:
            product_rules.link_default_supplier(self.db, product_id, self._system_settings_row())
            return
        try:
            days = max(0, int(lead_time_days))
        except (TypeError, ValueError):
            days = self._default_standard_lead_time_days()
        supplier = self._resolve_default_supplier_for_new_product()
        if not supplier:
            return
        existing = self.db.query(ProductSupplier).filter(
            ProductSupplier.product_id == product_id,
            ProductSupplier.supplier_id == supplier.id,
        ).first()
        if existing:
            if existing.standard_lead_time_days != days:
                existing.standard_lead_time_days = days
                self.db.flush()
            return
        self.db.add(
            ProductSupplier(
                product_id=product_id,
                supplier_id=supplier.id,
                standard_lead_time_days=days,
            )
        )
        self.db.flush()

    def create_product(self, product_data: ProductCreate, created_by: str):
        """Create a new product."""
        # Trim product_code defensively (schema validator also does this; belt-and-suspenders)
        product_code = (product_data.product_code or "").strip()
        # Check if product_code already exists
        existing = self.db.query(Product).filter(Product.product_code == product_code).first()
        if existing:
            raise handle_conflict("Product code already exists. Please use a different code.")
        
        data = product_data.model_dump()
        data["product_code"] = product_code
        # Live finding, 2026-09-06: derive from product_rules.derivation_text
        # (description if non-blank else name), not the raw description column
        # directly - the ESB maps AutoCount's Description onto name and sends
        # no description at all, so a description-only read misses it there.
        text_for_derivation = product_rules.derivation_text(data.get("product_name"), data.get("description"))
        # Auto-populate dimensions from the derived text's LxWxH pattern when caller did not supply them.
        parsed_l, parsed_w, parsed_h = parse_dimensions_from_description(text_for_derivation)
        if parsed_l is not None and data.get("dimensions_length") is None:
            data["dimensions_length"] = parsed_l
        if parsed_w is not None and data.get("dimensions_width") is None:
            data["dimensions_width"] = parsed_w
        if parsed_h is not None and data.get("dimensions_height") is None:
            data["dimensions_height"] = parsed_h
        # D2: an explicit flag (ProductCreate.is_discontinued) wins over the
        # derived-text one - flag_wins, same rule product_rules.is_discontinued
        # applies for every other channel.
        data["is_discontinued"] = _rules_is_discontinued(data.get("is_discontinued"), text_for_derivation)
        product = Product(**data, created_by=created_by)
        self.db.add(product)
        self.db.commit()
        self.db.refresh(product)
        self._ensure_default_supplier_lead_time(product.id)
        self.db.commit()
        self.db.refresh(product)
        publish_embedding_event(
            self.db,
            source_type="product",
            source_id=product.id,
            source_key=product.product_code,
            source_updated_at=product.updated_at or product.created_at,
            event_type="product.created",
            changed_fields=["product_code", "product_name", "description", "category_id", "brand_id", "is_active"],
            triggered_by=created_by,
        )
        self._reconcile_variant_links(product.id)
        return product
    
    def update_product(self, product_id: str, product_data: ProductUpdate, updated_by: str):
        """Update a product."""
        product = self.get_product(product_id)
        
        update_data = product_data.model_dump(exclude_unset=True)
        if update_data:
            update_data["updated_by"] = updated_by
            update_data["updated_at"] = datetime.utcnow()
            # Live finding, 2026-09-06: derived from product_rules.derivation_text's
            # EFFECTIVE text (the incoming name/description if this update sends
            # one, else whatever the row already holds - D14's "absent =
            # untouched" merge), not the raw description column alone - a
            # name-only edit on a row with no description must still re-derive.
            name_or_description_sent = "product_name" in update_data or "description" in update_data
            if name_or_description_sent:
                effective_name = update_data.get("product_name", product.product_name)
                effective_description = update_data.get("description", product.description)
                text_for_derivation = product_rules.derivation_text(effective_name, effective_description)
                # Re-parse LxWxH and populate dimension columns that the caller did
                # NOT explicitly set in the same payload (explicit user value wins).
                parsed_l, parsed_w, parsed_h = parse_dimensions_from_description(text_for_derivation)
                if parsed_l is not None and "dimensions_length" not in update_data:
                    update_data["dimensions_length"] = parsed_l
                if parsed_w is not None and "dimensions_width" not in update_data:
                    update_data["dimensions_width"] = parsed_w
                if parsed_h is not None and "dimensions_height" not in update_data:
                    update_data["dimensions_height"] = parsed_h
            # D2: an explicit flag wins over the derived-text value; recomputed
            # only when the flag was NOT sent AND a name/description was (an
            # update that touches neither leaves is_discontinued untouched,
            # same D14 rule as any other absent field).
            if "is_discontinued" in update_data:
                new_discontinued = bool(update_data["is_discontinued"])
            elif name_or_description_sent:
                new_discontinued = _rules_is_discontinued(None, text_for_derivation)
                update_data["is_discontinued"] = new_discontinued
            else:
                new_discontinued = None
            if new_discontinued is not None:
                # is_discontinued True->False: reset the notify watermark so a later
                # re-discontinuation is reported again by the batch cron. product.* is
                # still the OLD value here (setattr loop runs below).
                if product.is_discontinued and not new_discontinued:
                    update_data["discontinued_notified_at"] = None
                    update_data["discontinued_notify_batch_id"] = None
            for key, value in update_data.items():
                setattr(product, key, value)
            
            self.db.commit()
            self.db.refresh(product)
            publish_embedding_event(
                self.db,
                source_type="product",
                source_id=product.id,
                source_key=product.product_code,
                source_updated_at=product.updated_at or product.created_at,
                event_type="product.updated" if product.is_active else "product.deactivated",
                changed_fields=list(update_data.keys()),
                triggered_by=updated_by,
            )
            # Re-derive the variant link only when the code (the derivation input)
            # actually changed - avoids churn on price/description-only edits.
            if "product_code" in update_data:
                # Capture existing children BEFORE re-deriving: a rename can break
                # the old-code prefix match, so each former child must re-derive to
                # its next ancestor (else it stays mis-linked to us - the FK is
                # unchanged by a rename). Mirrors delete_product's re-anchor.
                ex_children = self._variant_child_ids(product.id)
                self._reconcile_variant_links(product.id)
                for child_id in ex_children:
                    self._reconcile_variant_links(child_id)

        return product

    def delete_product(self, product_id: str):
        """Delete a product."""
        product = self.get_product(product_id)
        self._reject_if_companion_rule_dependency(product_id)
        # Capture children BEFORE delete so we can re-anchor them to the next
        # existing ancestor afterwards (DB ondelete=SET NULL orphans them).
        ex_children = self._variant_child_ids(product_id)
        self.db.delete(product)
        self.db.commit()
        for child_id in ex_children:
            self._reconcile_variant_links(child_id)
        return {"message": "Product deleted successfully"}

    def _reject_if_companion_rule_dependency(self, product_id: str) -> None:
        """RESTRICT is real at the DB level (migration 495), but a bare
        `IntegrityError` names no rule - this pre-check answers with the same 409 the
        FK would raise anyway, naming what is actually blocking it, by item code, never
        a rule id or a raw UUID (UAC A6, review round 1 item 6)."""
        from sqlalchemy.orm import joinedload

        from app.models.product_companion import ProductCompanionRule, ProductCompanionRuleHost

        as_companion = (
            self.db.query(ProductCompanionRule)
            .options(
                joinedload(ProductCompanionRule.hosts).joinedload(
                    ProductCompanionRuleHost.host_product
                )
            )
            .filter(ProductCompanionRule.companion_product_id == product_id)
            .first()
        )
        if as_companion is not None:
            host_codes = " + ".join(
                host.host_product.product_code
                for host in as_companion.hosts
                if host.host_product is not None
            )
            raise handle_conflict(
                f"This product is the companion of a \"supplied with\" rule "
                f"(included with {host_codes or 'a host'}). Delete the rule first."
            )
        as_host = (
            self.db.query(ProductCompanionRuleHost)
            .options(joinedload(ProductCompanionRuleHost.rule).joinedload(
                ProductCompanionRule.companion_product
            ))
            .filter(ProductCompanionRuleHost.host_product_id == product_id)
            .first()
        )
        if as_host is not None:
            companion_code = (
                as_host.rule.companion_product.product_code
                if as_host.rule and as_host.rule.companion_product
                else "a companion"
            )
            raise handle_conflict(
                f"This product is named as a host in a \"supplied with\" rule "
                f"for {companion_code}. Delete the rule first."
            )

    def _reconcile_variant_links(self, code_or_id: str) -> None:
        """Best-effort post-commit variant-graph reconcile. Never raises - a
        side effect running AFTER the row committed must not 500 a succeeded op
        (post-commit side-effect rule, CLAUDE.md)."""
        try:
            from app.services.variant_link_service import reconcile_variant_links

            reconcile_variant_links(self.db, code_or_id)
        except Exception:
            import logging

            logging.getLogger(__name__).warning(
                "variant link reconcile failed for %s", code_or_id, exc_info=True
            )
            try:
                self.db.rollback()
            except Exception:
                pass

    def _variant_child_ids(self, product_id: str) -> List[str]:
        try:
            from app.services.variant_link_service import child_ids_of

            return child_ids_of(self.db, product_id)
        except Exception:
            return []

    def bulk_update_products(
        self, product_ids: List[str], updates: ProductBulkUpdates, updated_by: str
    ) -> dict:
        """Apply the same edit to every listed product.

        Row-by-row `setattr` rather than `query.update()`, so the audit listener
        sees each change like it does for a single edit. The read is an ORM
        query and therefore company-scoped: an id from another company is simply
        not found and not counted, never written.
        """
        rows = self.db.query(Product).filter(Product.id.in_(product_ids)).all()
        now = datetime.utcnow()
        for product in rows:
            product.is_searchable = updates.is_searchable
            product.updated_by = updated_by
            product.updated_at = now
        self.db.commit()
        return {"message": f"Updated {len(rows)} product(s)", "updated_count": len(rows)}

    def bulk_delete_products(self, product_ids: List[str]):
        """Delete multiple products by ID."""
        if not product_ids:
            return {"message": "No products to delete", "deleted_count": 0}
        # Capture children of all deleted rows (excluding the deleted set itself)
        # so survivors re-anchor to their next existing ancestor.
        ex_children: List[str] = []
        for pid in product_ids:
            ex_children.extend(self._variant_child_ids(pid))
        deleted_set = set(product_ids)
        deleted = self.db.query(Product).filter(Product.id.in_(product_ids)).delete(
            synchronize_session=False
        )
        self.db.commit()
        for child_id in ex_children:
            if child_id in deleted_set:
                continue
            self._reconcile_variant_links(child_id)
        return {"message": f"Deleted {deleted} product(s)", "deleted_count": deleted}

    def _get_default_uom_id(self) -> str:
        """The unit a product takes when nobody states one.

        S2 (review re-check, 2026-09-06): delegates to
        `product_rules.resolve_default_uom`, the same body the ESB push
        uses, instead of its own separate copy - which trusted
        `system_settings.default_uom_id` cross-company (the setting is a
        single company-agnostic row, LESSONS-LEARNT "system_settings
        singleton", so it can name a UOM belonging to a different company
        than this caller's), matched the EA fallback with a plain
        `ilike("ea")` scan rather than the shared `ensure_reference`, and
        committed mid-import instead of flushing - three ways this and the
        ESB path could reach a different answer for the identical question.
        """
        settings = self._system_settings_row()
        uom_id = product_rules.resolve_default_uom(self.db, company_id=None, settings=settings)
        if uom_id is None:
            raise AppException("No default unit of measure could be resolved.", status_code=500)
        return uom_id

    def _resolve_category_id(self, item_group: Optional[str]) -> Optional[str]:
        """Resolve category by item_group (match category_code or category_name)."""
        if not item_group or not str(item_group).strip():
            return None
        q = str(item_group).strip()
        cat = (
            self.db.query(ProductCategory)
            .filter(
                or_(
                    ProductCategory.category_code.ilike(q),
                    ProductCategory.category_name.ilike(q),
                )
            )
            .first()
        )
        return cat.id if cat else None

    def _resolve_brand_id(self, item_brand: Optional[str]) -> Optional[str]:
        """Resolve brand by item_brand (match brand_code or brand_name)."""
        if not item_brand or not str(item_brand).strip():
            return None
        q = str(item_brand).strip()
        brand = (
            self.db.query(Brand)
            .filter(
                or_(
                    Brand.brand_code.ilike(q),
                    Brand.brand_name.ilike(q),
                )
            )
            .first()
        )
        return brand.id if brand else None

    BULK_IMPORT_CHUNK_SIZE = 500  # Commit every N rows; fewer round-trips
    _BULK_FETCH_CODES_BATCH = 5000  # Max product_codes per IN query

    # Auto-created master data (Item Group -> category, Item Brand -> brand,
    # UOM column -> unit of measure). The code columns are VARCHAR(50), so a
    # longer source value is a row error rather than a silent truncation that
    # would collide with a different value later.
    REF_CODE_MAX_LEN = 50
    DEFAULT_UOM_CODE = product_rules.DEFAULT_UOM_CODE
    DEFAULT_UOM_NAME = product_rules.DEFAULT_UOM_NAME
    AUTO_CREATED_NOTE = "Auto-created by product import"

    @staticmethod
    def _row_uom_value(row: dict) -> Optional[str]:
        """The optional UOM column of a product-import row.

        The stock item list export has no UOM column at all; other exports label
        it UOM / Unit / Unit of Measure / UOM Code.
        """
        for key in (
            "uom",
            "UOM",
            "uom_code",
            "UOM Code",
            "unit",
            "Unit",
            "unit_of_measure",
            "Unit of Measure",
        ):
            value = row.get(key)
            if value is not None and str(value).strip():
                return str(value).strip()
        return None

    def _build_uom_map(self) -> dict:
        """Build uom value -> uom_id map from all UOMs (code and name, case-insensitive)."""
        rows = self.db.query(UnitOfMeasure.id, UnitOfMeasure.uom_code, UnitOfMeasure.uom_name).all()
        m = {}
        for id_, code, name in rows:
            if code:
                m[str(code).strip().lower()] = id_
            if name:
                m[str(name).strip().lower()] = id_
        return m

    def _build_category_map(self) -> dict:
        """Build item_group -> category_id map from all categories (code and name, case-insensitive)."""
        rows = self.db.query(ProductCategory.id, ProductCategory.category_code, ProductCategory.category_name).all()
        m = {}
        for id_, code, name in rows:
            if code:
                m[str(code).strip().lower()] = id_
            if name:
                m[str(name).strip().lower()] = id_
        return m

    def _build_brand_map(self) -> dict:
        """Build item_brand -> brand_id map from all brands (code and name, case-insensitive)."""
        rows = self.db.query(Brand.id, Brand.brand_code, Brand.brand_name).all()
        m = {}
        for id_, code, name in rows:
            if code:
                m[str(code).strip().lower()] = id_
            if name:
                m[str(name).strip().lower()] = id_
        return m

    def _fetch_existing_products_by_codes(self, codes: List[str]) -> dict:
        """Fetch existing products by product_code; return dict code -> Product."""
        if not codes:
            return {}
        seen = set()
        unique = [c for c in codes if c and c not in seen and not seen.add(c)]
        result = {}
        for i in range(0, len(unique), self._BULK_FETCH_CODES_BATCH):
            batch = unique[i : i + self._BULK_FETCH_CODES_BATCH]
            products = self.db.query(Product).filter(Product.product_code.in_(batch)).all()
            for p in products:
                result[p.product_code] = p
        return result

    def _fetch_product_ids_with_configured_lead_time(self, product_ids: List[str]) -> set:
        """Product IDs that have at least one product_suppliers row with standard_lead_time_days set."""
        if not product_ids:
            return set()
        seen: set = set()
        unique: List[str] = []
        for pid in product_ids:
            if pid and pid not in seen:
                seen.add(pid)
                unique.append(pid)
        out: set = set()
        for i in range(0, len(unique), self._BULK_FETCH_CODES_BATCH):
            batch = unique[i : i + self._BULK_FETCH_CODES_BATCH]
            rows = (
                self.db.query(ProductSupplier.product_id)
                .filter(
                    ProductSupplier.product_id.in_(batch),
                    ProductSupplier.standard_lead_time_days.isnot(None),
                )
                .distinct()
                .all()
            )
            for (pid,) in rows:
                out.add(pid)
        return out

    def _bulk_publish_product_embedding_events(
        self,
        touched: List[Tuple[str, str, Any, Any]],
        user_id: str,
    ) -> None:
        """
        Bulk-insert embedding queue rows for many products in one commit, then enqueue RQ jobs.
        The per-row publish_embedding_event path does add+flush+enqueue+commit+refresh per call,
        which dominates wall time for 10k-row imports. This batched path: 1 INSERT + 1 commit, then
        N (cheap) RQ enqueues with no DB roundtrips.
        """
        if not touched:
            return
        from app.models.embeddings import EmbeddingQueue
        from app.services.queue_service import enqueue_job
        from app.services.embedding_service import _get_embedding_worker
        from app.config import settings as _settings

        now = datetime.utcnow()
        rows: List[dict] = []
        enqueue_payload: List[str] = []  # queue row ids to enqueue post-commit
        for pid, pcode, updated_at, created_at in touched:
            sua = updated_at or created_at
            event_id = str(uuid.uuid4())
            correlation_id = str(uuid.uuid4())
            queue_id = str(uuid.uuid4())
            payload = {
                "event_id": event_id,
                "event_type": "product.updated",
                "event_version": 1,
                "occurred_at": now.isoformat(),
                "source_type": "product",
                "source_id": str(pid),
                "source_key": pcode,
                "source_updated_at": sua.isoformat() if sua else None,
                "changed_fields": ["bulk_import"],
                "correlation_id": correlation_id,
                "triggered_by": user_id,
                "payload": {},
            }
            rows.append({
                "id": queue_id,
                "source_type": "product",
                "source_id": str(pid),
                "event_type": "product.updated",
                "event_version": 1,
                "source_updated_at": sua,
                "payload": payload,
                "status": "pending",
                "correlation_id": correlation_id,
            })
            enqueue_payload.append(queue_id)

        try:
            self.db.bulk_insert_mappings(EmbeddingQueue, rows)
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

        try:
            worker = _get_embedding_worker()
            for q_id in enqueue_payload:
                # enqueue_job, not queue.enqueue: the job carries its audit actor and
                # correlation id like every other job (review N5).
                enqueue_job(worker, q_id, queue_name=_settings.embedding_queue_name, job_timeout=900)
        except Exception:
            # Embedding side effects must never block the import; rows are persisted
            # and can be picked up by a sweeper. Log and move on.
            import logging
            logging.getLogger(__name__).exception(
                "Failed to bulk-enqueue embedding events after product import"
            )

    def bulk_import_products(
        self,
        products_data: List[dict],
        user_id: str,
        on_progress: Optional[Callable[[int, int, int, int], None]] = None,
        outcome=None,
    ) -> dict:
        """
        Bulk import products from Excel-style rows.
        Expects each row: product_code, product_name?, description?, item_group?, item_brand?, list_price?, is_active?, uom?, reorder_level?, reorder_quantity?
        item_group is matched to category (code or name); item_brand to brand (code or name);
        the optional uom column to a unit of measure (code or name).
        A value that matches nothing is CREATED (code = name = the raw value) so a
        fresh stock item list imports without anyone hand-building master data first.
        Creates or updates by product_code. Rows with no uom column use the default UOM.
        On update, if the product has no product_suppliers row with standard_lead_time_days set, applies the same default supplier/lead time as new products.
        Optimized: pre-loads categories, brands, and existing products to avoid per-row queries.
        on_progress: optional callback(processed, successful, failed, skipped) called at chunk boundaries for real-time UI.
        outcome: optional ImportOutcome recorder, so every row's fate (created /
            updated / skipped-with-a-reason) is captured for the job detail page.
            None for non-job callers.
        """
        from app.services import import_outcome_codes as _oc
        from app.services.import_outcome import ImportOutcome as _ImportOutcome

        if outcome is None:
            outcome = _ImportOutcome(None, persist=False)

        def _row_identity(_row: dict, _code: str) -> dict:
            return {
                "product_code": _code,
                "item_group": (_row.get("item_group") or _row.get("Item Group") or None),
                "item_brand": (_row.get("item_brand") or _row.get("Item Brand") or None),
            }

        created = 0
        updated = 0
        errors = []
        default_uom_id = self._get_default_uom_id()
        chunk_size = self.BULK_IMPORT_CHUNK_SIZE

        ref_counts = {"categories": 0, "brands": 0, "uoms": 0}
        _REF_MODELS = {"category": ProductCategory, "brand": Brand, "uom": UnitOfMeasure}
        _REF_COUNT_KEYS = {"category": "categories", "brand": "brands", "uom": "uoms"}

        def ensure_reference(kind: str, raw_value: str) -> str:
            """Resolve a master-data value, creating the row when it is unknown.

            Reconciled onto `product_rules.ensure_reference` (review S2,
            2026-09-06): this closure used to match a raw value against a
            code OR name lower-cased dict of ITS OWN, a different semantic
            than the shared function's code-only `upper(btrim())` match every
            OTHER caller (manual create/edit, the ESB push) went through -
            `product_rules.ensure_reference` now matches code-then-name too,
            so this is one body, not two that can drift.

            Committed immediately on create (unchanged from the closure this
            replaces): a later row in this same import failing and rolling
            back its transaction must not take an already-referenced
            category/brand/UOM with it - `product_rules.ensure_reference`
            only flushes, so the commit stays the caller's job here.
            """
            value = str(raw_value).strip()
            model = _REF_MODELS[kind]
            # `ReferenceTooLong` propagates as-is - `_RefTooLong` below is an
            # alias onto the SAME class, so the call site's existing
            # `except _RefTooLong` still catches it.
            ref_id, was_created = product_rules.ensure_reference(
                self.db, model, value, company_id=None
            )
            if was_created:
                if model is ProductCategory or model is Brand:
                    setattr(
                        self.db.get(model, ref_id), "created_by", user_id
                    )
                self.db.commit()
                ref_counts[_REF_COUNT_KEYS[kind]] += 1
            return ref_id

        _RefTooLong = product_rules.ReferenceTooLong

        # id -> code, for the rows that report a unit MOVE. Built lazily and once, because a
        # re-import of the stock item list moves thousands of rows at a stroke and a lookup
        # per row would be thousands of queries for a sentence.
        uom_code_by_id: dict[str, str] = {}

        def uom_code_of(uom_row_id) -> str:
            """The human code of a unit id, or "none" for a product that had none."""
            if not uom_row_id:
                return "none"
            if not uom_code_by_id:
                uom_code_by_id.update({
                    str(i): c
                    for i, c in self.db.query(
                        UnitOfMeasure.id, UnitOfMeasure.uom_code
                    ).all()
                })
            return uom_code_by_id.get(str(uom_row_id)) or "unknown"

        all_codes = []
        for row in products_data:
            code = (row.get("product_code") or row.get("Product Code") or row.get("Item Code") or "").strip()
            if code:
                all_codes.append(code)
        existing_by_code = self._fetch_existing_products_by_codes(all_codes)
        products_with_lead_time = self._fetch_product_ids_with_configured_lead_time(
            [p.id for p in existing_by_code.values()]
        )

        # Whether this upload speaks about the reorder columns AT ALL, decided once for the
        # whole file. A blank cell clears a held value, so a file that never mentions the
        # column must not be read as "every level is blank". See file_carries_reorder_column.
        has_level_col = file_carries_reorder_column(products_data, _REORDER_LEVEL_HEADERS)
        has_qty_col = file_carries_reorder_column(products_data, _REORDER_QTY_HEADERS)
        # code -> (level, qty), for the SCM hand-off after the loop. Only rows that named a
        # level go in: a blank clears the item master but must not touch a planning level a
        # person may own (there is no value in a blank for them to disagree with).
        reorder_for_scm: dict[str, tuple[int, Optional[int]]] = {}
        cleared_codes: set[str] = set()

        # Cache default supplier + lead-time once (was N× per row inside _ensure_default_supplier_lead_time).
        default_supplier = self._resolve_default_supplier_for_new_product()
        default_supplier_id = default_supplier.id if default_supplier else None
        default_lead_time_days = self._default_standard_lead_time_days()

        # Pre-fetch existing ProductSupplier rows on the default supplier for every existing product
        # we may touch, so per-row link decisions become an O(1) dict lookup.
        existing_default_ps: dict[str, ProductSupplier] = {}
        if default_supplier_id and existing_by_code:
            existing_pids = list({p.id for p in existing_by_code.values()})
            for i in range(0, len(existing_pids), self._BULK_FETCH_CODES_BATCH):
                batch = existing_pids[i : i + self._BULK_FETCH_CODES_BATCH]
                rows = (
                    self.db.query(ProductSupplier)
                    .filter(
                        ProductSupplier.supplier_id == default_supplier_id,
                        ProductSupplier.product_id.in_(batch),
                    )
                    .all()
                )
                for ps in rows:
                    existing_default_ps[ps.product_id] = ps

        def link_default_supplier(product_id: str) -> None:
            if not default_supplier_id:
                return
            ps = existing_default_ps.get(product_id)
            if ps is not None:
                if ps.standard_lead_time_days != default_lead_time_days:
                    ps.standard_lead_time_days = default_lead_time_days
                return
            new_ps = ProductSupplier(
                product_id=product_id,
                supplier_id=default_supplier_id,
                standard_lead_time_days=default_lead_time_days,
            )
            self.db.add(new_ps)
            existing_default_ps[product_id] = new_ps

        for idx, row in enumerate(products_data, start=1):
            try:
                product_code = (row.get("product_code") or row.get("Product Code") or row.get("Item Code") or "").strip()
                if not product_code:
                    msg = "product_code / Item Code is required"
                    errors.append(f"Row {idx}: {msg}")
                    outcome.skip(row=idx, code=_oc.MISSING_ITEM_CODE, message=msg)
                    continue
                product_name = (row.get("product_name") or row.get("Product Name") or row.get("Item Code") or product_code).strip() or product_code
                description = row.get("description") or row.get("Description") or ""
                desc2 = row.get("desc2") or row.get("Desc 2") or ""
                description = join_description_and_desc2(description, desc2)
                item_group = (row.get("item_group") or row.get("Item Group") or "").strip() or None
                item_brand = (row.get("item_brand") or row.get("Item Brand") or "").strip() or None
                raw_price = row.get("list_price") or row.get("Price") or row.get("price")
                try:
                    list_price = parse_manual_list_price(raw_price)
                except Exception:
                    msg = f"Price must be a valid number, got '{raw_price}'"
                    errors.append(f"Row {idx} ({product_code}): {msg}")
                    outcome.skip(
                        row=idx,
                        code=_oc.INVALID_QUANTITY,
                        message=msg,
                        value=product_code,
                        identity=_row_identity(row, product_code),
                    )
                    continue
                raw_active = row.get("is_active") or row.get("Is Active") or row.get("Is active")
                is_active = is_active_from_manual_value(raw_active)

                if not item_group:
                    msg = "item_group is required"
                    errors.append(f"Row {idx} ({product_code}): {msg}")
                    outcome.skip(
                        row=idx,
                        code=_oc.MISSING_REQUIRED_FIELD,
                        message=msg,
                        value=product_code,
                        identity=_row_identity(row, product_code),
                    )
                    continue

                # Unknown references are created rather than rejected.
                try:
                    category_id = ensure_reference("category", item_group)
                    brand_id = ensure_reference("brand", item_brand) if item_brand else None
                    row_uom = self._row_uom_value(row)
                    uom_id = ensure_reference("uom", row_uom) if row_uom else default_uom_id
                except _RefTooLong as too_long:
                    self.db.rollback()
                    msg = str(too_long)
                    errors.append(f"Row {idx} ({product_code}): {msg}")
                    outcome.skip(
                        row=idx,
                        code=_oc.MISSING_REQUIRED_FIELD,
                        message=msg,
                        value=product_code,
                        identity=_row_identity(row, product_code),
                    )
                    continue

                # D24 (captain 2026-09-06): the xlsx import already stores the
                # AutoCount Description text in `description` (`product_name`
                # is the item code here) - reads `description` directly, same
                # as `_finalize_product_derived` now does for the ESB.
                parsed_l, parsed_w, parsed_h = parse_dimensions_from_description(description)
                discontinued = is_discontinued_from_row(row, description)

                # `None` = blank cell = clear it; a value (including 0) = set it. Only read
                # when the file carries the column at all - otherwise both stay ABSENT and
                # nothing below touches the held value.
                reorder_level = ABSENT
                reorder_qty = ABSENT
                try:
                    if has_level_col:
                        # ABSENT collapses to None HERE, and only here. The frontend's
                        # `sheet_to_json` omits blank cells, so on a file that carries the
                        # column a row with no key IS a blank cell - the commonest form of
                        # blank there is, not an edge case. The file-level question was
                        # already answered by `has_level_col`.
                        reorder_level = _blank_if_absent(
                            reorder_cell(row, _REORDER_LEVEL_HEADERS)
                        )
                    if has_qty_col:
                        reorder_qty = _blank_if_absent(
                            reorder_cell(row, _REORDER_QTY_HEADERS)
                        )
                except ReorderCellError as bad_cell:
                    msg = f"Reorder level / quantity must be a number, got {bad_cell}"
                    errors.append(f"Row {idx} ({product_code}): {msg}")
                    outcome.skip(
                        row=idx,
                        code=_oc.INVALID_QUANTITY,
                        message=msg,
                        value=product_code,
                        identity=_row_identity(row, product_code),
                    )
                    continue

                if isinstance(reorder_level, int):
                    reorder_for_scm[product_code] = (
                        reorder_level,
                        reorder_qty if isinstance(reorder_qty, int) else None,
                    )
                elif reorder_level is None:
                    cleared_codes.add(product_code)

                existing = existing_by_code.get(product_code)
                if existing:
                    # is_discontinued True->False: reset the notify watermark so a later
                    # re-discontinuation is reported again (capture OLD before assigning).
                    if existing.is_discontinued and not discontinued:
                        existing.discontinued_notified_at = None
                        existing.discontinued_notify_batch_id = None
                    existing.product_name = product_name
                    existing.description = description or None
                    existing.category_id = category_id
                    existing.brand_id = brand_id
                    # ONE rule for every row, existing or new: the file's unit when it
                    # states one, the configured default when it does not.
                    #
                    # It used to be two rules - an existing product kept whatever unit it
                    # held unless the file carried a UOM column - and that is precisely why
                    # 11,415 products stamped `L` by an old fallback could not be corrected:
                    # the stock item list has no UOM column at all, so re-importing it
                    # declined to touch the column on every one of them. The admin now names
                    # the right unit in system settings and this applies it.
                    #
                    # The prior unit is read BEFORE the assignment - it is the only place it
                    # still exists once overwritten - so a row that actually moved can say
                    # which unit it left.
                    previous_uom_id = existing.base_uom_id
                    existing.base_uom_id = uom_id
                    uom_moved = not row_uom and previous_uom_id != uom_id
                    existing.list_price = list_price
                    existing.is_active = is_active
                    existing.is_discontinued = discontinued
                    existing.updated_by = user_id
                    existing.updated_at = datetime.utcnow()
                    if parsed_l is not None:
                        existing.dimensions_length = parsed_l
                    if parsed_w is not None:
                        existing.dimensions_width = parsed_w
                    if parsed_h is not None:
                        existing.dimensions_height = parsed_h
                    # Unlike UOM three lines up, a blank DOES overwrite here. The
                    # difference is ownership: a file with no UOM column is not talking
                    # about UOM, while a blank cell in a reorder column the file DOES
                    # carry is AutoCount saying the level is gone. `has_level_col` is what
                    # keeps those two apart, per file rather than per row.
                    # A clear is only a clear when there was something to lose - read the
                    # held value BEFORE assigning.
                    cleared_a_level = (
                        has_level_col
                        and reorder_level is None
                        and existing.reorder_level is not None
                    )
                    if has_level_col:
                        existing.reorder_level = reorder_level
                    if has_qty_col:
                        existing.reorder_quantity = reorder_qty
                    if existing.id not in products_with_lead_time:
                        link_default_supplier(existing.id)
                        products_with_lead_time.add(existing.id)
                    updated += 1
                    # One outcome entry per row, always: a second entry for the clear would
                    # push `processed_rows` past `total_rows`. The code carries the news
                    # instead, so the job page can group and find them. A unit that MOVED is
                    # reported ahead of a cleared level: the operator who re-imports the
                    # stock list to fix 11,415 wrong units is running it for exactly that.
                    if uom_moved:
                        row_code = _oc.UOM_DEFAULTED
                        row_message = (
                            f"Product updated, unit of measure set to the default: "
                            f"{product_code} ({uom_code_of(previous_uom_id)} -> "
                            f"{uom_code_of(uom_id)})"
                        )
                    elif cleared_a_level:
                        row_code = _oc.REORDER_LEVEL_CLEARED
                        row_message = f"Product updated, reorder level cleared: {product_code}"
                    else:
                        row_code = _oc.UPDATED
                        row_message = f"Product updated: {product_code}"
                    outcome.updated(
                        row=idx,
                        code=row_code,
                        message=row_message,
                        value=product_code,
                        identity=_row_identity(row, product_code),
                        entity_type="product",
                        entity_id=existing.id,
                    )
                else:
                    # Generate UUID Python-side so we can link ProductSupplier without per-row flush().
                    product = Product(
                        id=str(uuid.uuid4()),
                        product_code=product_code,
                        product_name=product_name,
                        description=description or None,
                        category_id=category_id,
                        brand_id=brand_id,
                        base_uom_id=uom_id,
                        list_price=list_price,
                        is_active=is_active,
                        is_discontinued=discontinued,
                        dimensions_length=parsed_l,
                        dimensions_width=parsed_w,
                        dimensions_height=parsed_h,
                        reorder_level=reorder_level if has_level_col else None,
                        reorder_quantity=reorder_qty if has_qty_col else None,
                        created_by=user_id,
                    )
                    self.db.add(product)
                    link_default_supplier(product.id)
                    products_with_lead_time.add(product.id)
                    existing_by_code[product_code] = product  # avoid duplicate add if same code again
                    created += 1
                    outcome.success(
                        row=idx,
                        message=f"Product created: {product_code}",
                        value=product_code,
                        identity=_row_identity(row, product_code),
                        entity_type="product",
                        entity_id=product.id,
                    )

                if idx % chunk_size == 0:
                    self.db.commit()
                    if on_progress:
                        on_progress(idx, created + updated, len(errors), 0)
            except Exception as e:
                self.db.rollback()
                errors.append(f"Row {idx} ({row.get('product_code', '')}): {str(e)}")
                outcome.fail(
                    row=idx,
                    code=_oc.ROW_ERROR,
                    message=str(e),
                    value=str(row.get("product_code") or row.get("Product Code") or "") or None,
                )
                if idx % chunk_size == 0:
                    self.db.commit()
                    if on_progress:
                        on_progress(idx, created + updated, len(errors), 0)

        self.db.commit()

        # The same upload feeds the planning table, so the item master and the plan cannot
        # disagree about a number that has one owner. Best-effort AFTER the product commit:
        # the products ARE saved by now, and turning a successful import into a 500 over a
        # secondary write would strand the caller on a retry that re-does the first half.
        scm_result = self._apply_reorder_levels_to_scm(reorder_for_scm, user_id)

        # Report 100% progress BEFORE embedding fan-out so the UI doesn't appear stuck
        # while we batch-write embedding queue rows + enqueue RQ jobs.
        if on_progress:
            on_progress(len(products_data), created + updated, len(errors), 0)

        if created or updated:
            touched = (
                self.db.query(Product.id, Product.product_code, Product.updated_at, Product.created_at)
                .filter(Product.product_code.in_(all_codes))
                .all()
            )
            self._bulk_publish_product_embedding_events(touched, user_id)
        return {
            "created": created,
            "updated": updated,
            "errors": errors,
            "created_categories": ref_counts["categories"],
            "created_brands": ref_counts["brands"],
            "created_uoms": ref_counts["uoms"],
            "levels_applied": len(reorder_for_scm),
            "levels_cleared": len(cleared_codes),
            "level_conflicts": scm_result.get("conflicts", 0),
            "level_conflict_warnings": scm_result.get("warnings", []),
        }

    def _apply_reorder_levels_to_scm(
        self, reorder_for_scm: dict, user_id: str
    ) -> dict:
        """Hand the levels this file stated to the service that owns the planning table.

        Delegated rather than restated: the rule that a level a PERSON set is never
        silently overwritten lives in `reorder_level_import_service`, and two copies of it
        would drift. Only rows that NAMED a level are passed - a blank clears the item
        master (AutoCount owns that number) but must not touch a planning level somebody
        may own, because a blank carries no value for them to disagree with.
        """
        if not reorder_for_scm:
            return {}
        try:
            from app.services.scm import reorder_level_import_service as _rl
            from app.services.scm.reorder_level_reader import LevelReadResult, LevelRow

            codes = list(reorder_for_scm.keys())
            rows = [
                LevelRow(
                    row_number=i,
                    item_code=code,
                    reorder_level=float(reorder_for_scm[code][0]),
                    location=None,
                    reorder_qty=(
                        float(reorder_for_scm[code][1])
                        if reorder_for_scm[code][1] is not None
                        else None
                    ),
                )
                for i, code in enumerate(codes, start=1)
            ]
            product_ids = {
                code.upper(): str(p.id)
                for code, p in self._fetch_existing_products_by_codes(codes).items()
            }
            outcome = _rl.apply_rows(
                self.db,
                LevelReadResult(rows=rows, total_rows=len(rows)),
                actor=user_id,
                product_ids=product_ids,
            )
            self.db.commit()
            return {
                "conflicts": outcome.get("conflicts", 0),
                "warnings": [
                    f"{c['item_code']}: planning reorder level {c['held_level']:g} was set "
                    f"by hand and was kept; this file said {c['file_level']:g}."
                    for c in outcome.get("conflict_rows", [])
                ],
            }
        except Exception as exc:  # noqa: BLE001 - the products are already committed
            self.db.rollback()
            logger.warning(
                "Product import wrote %s reorder levels to the item master but could not "
                "apply them to scm.reorder_level: %s",
                len(reorder_for_scm), exc, exc_info=True,
            )
            return {}

    def validate_products_import(self, products_data: List[dict]) -> dict:
        """
        Run the same validation as bulk_import_products without writing to DB.
        Returns errors, warnings, and summary (would_create, would_update counts).
        """
        errors = []
        warnings = []
        would_create = 0
        would_update = 0
        category_map = self._build_category_map()
        brand_map = self._build_brand_map()
        uom_map = self._build_uom_map()
        # Unknown references are created by the import, so they are previewed as
        # warnings ("will be created"), never as errors that block the upload.
        new_refs: dict = {"category": {}, "brand": {}, "uom": {}}

        def preview_reference(kind: str, raw_value: str) -> Optional[str]:
            """None when the value is fine (known or newly previewed); otherwise the error."""
            value = str(raw_value).strip()
            lookup = {"category": category_map, "brand": brand_map, "uom": uom_map}[kind]
            if value.lower() in lookup or value.lower() in new_refs[kind]:
                return None
            if len(value) > self.REF_CODE_MAX_LEN:
                return (
                    f"{kind} '{value}' is {len(value)} characters; the code column holds "
                    f"{self.REF_CODE_MAX_LEN}"
                )
            new_refs[kind][value.lower()] = value
            return None

        all_codes = []
        for row in products_data:
            code = (row.get("product_code") or row.get("Product Code") or row.get("Item Code") or "").strip()
            if code:
                all_codes.append(code)
        existing_by_code = self._fetch_existing_products_by_codes(all_codes)

        for idx, row in enumerate(products_data, start=1):
            try:
                product_code = (row.get("product_code") or row.get("Product Code") or row.get("Item Code") or "").strip()
                if not product_code:
                    errors.append(f"Row {idx}: product_code / Item Code is required")
                    continue
                product_name = (row.get("product_name") or row.get("Product Name") or row.get("Item Code") or product_code).strip() or product_code
                item_group = (row.get("item_group") or row.get("Item Group") or "").strip() or None
                item_brand = (row.get("item_brand") or row.get("Item Brand") or "").strip() or None
                raw_price = row.get("list_price") or row.get("Price") or row.get("price")
                try:
                    if raw_price is None or str(raw_price).strip() == "":
                        list_price = Decimal("0")
                    else:
                        list_price = Decimal(str(raw_price))
                    # A negative list price (e.g. the -1 "no price" sentinel from the
                    # source system) is coerced to 0 rather than rejecting the row.
                    if list_price < 0:
                        list_price = Decimal("0")
                except Exception:
                    errors.append(
                        f"Row {idx} ({product_code}): Price must be a valid number, got '{raw_price}'"
                    )
                    continue

                if not item_group:
                    errors.append(f"Row {idx} ({product_code}): item_group is required")
                    continue

                too_long = preview_reference("category", item_group)
                if not too_long and item_brand:
                    too_long = preview_reference("brand", item_brand)
                row_uom = self._row_uom_value(row)
                if not too_long and row_uom:
                    too_long = preview_reference("uom", row_uom)
                if too_long:
                    errors.append(f"Row {idx} ({product_code}): {too_long}")
                    continue

                existing = existing_by_code.get(product_code)
                if existing:
                    would_update += 1
                else:
                    would_create += 1
            except Exception as e:
                errors.append(f"Row {idx} ({row.get('product_code', '')}): {str(e)}")

        for kind, label in (("category", "categories"), ("brand", "brands"), ("uom", "units of measure")):
            values = sorted(new_refs[kind].values())
            if not values:
                continue
            shown = ", ".join(values[:20])
            more = f" (+{len(values) - 20} more)" if len(values) > 20 else ""
            warnings.append(f"{len(values)} new {label} will be created: {shown}{more}")

        level_preview = self._preview_reorder_levels(products_data, existing_by_code)
        warnings.extend(level_preview["warnings"])

        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "warnings": warnings,
            "summary": {
                "total_rows": len(products_data),
                "would_create": would_create,
                "would_update": would_update,
                "error_count": len(errors),
                "new_categories": len(new_refs["category"]),
                "new_brands": len(new_refs["brand"]),
                "new_uoms": len(new_refs["uom"]),
                "levels_applied": level_preview["applied"],
                "levels_cleared": level_preview["cleared"],
                "level_conflicts": level_preview["conflicts"],
            },
        }

    def _preview_reorder_levels(self, products_data: List[dict], existing_by_code: dict) -> dict:
        """What the reorder columns would do, without writing anything.

        Runs the SAME resolution the confirmed import runs, through the same service, so
        Test and Confirm cannot report different numbers about the same file.
        """
        out = {"applied": 0, "cleared": 0, "conflicts": 0, "warnings": []}
        if not file_carries_reorder_column(products_data, _REORDER_LEVEL_HEADERS):
            return out

        from app.services.scm import reorder_level_import_service as _rl
        from app.services.scm.reorder_level_reader import LevelReadResult, LevelRow

        rows: list = []
        for row in products_data:
            code = (
                row.get("product_code") or row.get("Product Code") or row.get("Item Code") or ""
            ).strip()
            if not code:
                continue
            try:
                # Same collapse as the import: on a file that carries the column, a row
                # with no key is a blank cell, because the sheet parser drops blanks.
                level = _blank_if_absent(reorder_cell(row, _REORDER_LEVEL_HEADERS))
                qty = _blank_if_absent(reorder_cell(row, _REORDER_QTY_HEADERS))
            except ReorderCellError:
                # Reported as a row error by the loop above; not a level to preview.
                continue
            if isinstance(level, int):
                out["applied"] += 1
                rows.append(LevelRow(
                    row_number=len(rows) + 1,
                    item_code=code,
                    reorder_level=float(level),
                    location=None,
                    reorder_qty=float(qty) if isinstance(qty, int) else None,
                ))
            elif level is None:
                existing = existing_by_code.get(code)
                if existing is not None and existing.reorder_level is not None:
                    out["cleared"] += 1

        out["warnings"].append(
            f"{out['applied']} products will take a reorder level from this file."
        )
        if out["cleared"]:
            out["warnings"].append(
                f"{out['cleared']} products will have their reorder level cleared, because "
                f"this file carries the column and leaves their cell blank."
            )
        if not rows:
            return out

        try:
            preview = _rl.preview_rows(self.db, LevelReadResult(rows=rows, total_rows=len(rows)))
        except Exception as exc:  # noqa: BLE001 - a preview must never block an upload
            logger.warning("Could not preview reorder levels against the plan: %s", exc)
            return out

        out["conflicts"] = preview.get("conflicts", 0)
        for c in preview.get("conflict_rows", []):
            out["warnings"].append(
                f"{c['item_code']}: planning reorder level {c['held_level']:g} was set by "
                f"hand and will be kept; this file says {c['file_level']:g}."
            )
        return out


class ProductCategoryService:
    """Service for product category operations."""
    
    def __init__(self, db: Session):
        self.db = db
    
    def list_categories(
        self,
        page: int = 1,
        limit: int = 50,
        query: Optional[str] = None,
        category_ids: Optional[list[str]] = None,
        product_ids: Optional[list[str]] = None,
    ):
        """List product categories. Ordered by display_order asc, then category_name asc."""
        q = self.db.query(ProductCategory)

        if query:
            q = q.filter(
                or_(
                    ProductCategory.category_code.ilike(f"%{query}%"),
                    ProductCategory.category_name.ilike(f"%{query}%")
                )
            )

        if category_ids is not None or product_ids is not None:
            allowed: set = set(category_ids or [])
            if product_ids:
                rows = (
                    self.db.query(Product.category_id)
                    .filter(Product.id.in_(product_ids), Product.category_id.isnot(None))
                    .distinct()
                    .all()
                )
                allowed.update(str(r[0]) for r in rows)
            q = q.filter(ProductCategory.id.in_(allowed))

        q = q.order_by(ProductCategory.display_order.asc(), ProductCategory.category_name.asc())
        total = q.count()
        offset = (page - 1) * limit
        categories = q.offset(offset).limit(limit).all()
        
        return {
            "data": categories,
            "pagination": {"total": total, "page": page, "limit": limit},
            "empty": total == 0
        }
    
    def get_category(self, category_id: str):
        """Get a category by UUID or category_code/name."""
        resolved_ids = resolve_identifier(
            self.db,
            category_id,
            ProductCategory,
            code_fields=("category_code", "category_name"),
        )
        if not resolved_ids:
            raise handle_not_found("Category", category_id)
        category = self.db.query(ProductCategory).filter(ProductCategory.id.in_(resolved_ids)).first()
        if not category:
            raise handle_not_found("Category", category_id)
        return category
    
    def get_categories_tree(self):
        """Get product categories as a tree structure (includes both active and inactive)."""
        from sqlalchemy.orm import joinedload
        from sqlalchemy import func
        
        # Get all categories, active and inactive (display_order asc, then category_name asc)
        categories = self.db.query(ProductCategory).order_by(
            ProductCategory.display_order.asc(), ProductCategory.category_name.asc()
        ).all()
        
        # Count products per category
        category_product_counts = {}
        for category in categories:
            count = self.db.query(func.count(Product.id)).filter(
                Product.category_id == category.id
            ).scalar()
            category_product_counts[category.id] = count or 0
        
        # Build tree structure
        category_dict = {}
        root_categories = []
        
        # First pass: create dictionary of all categories
        for category in categories:
            category_dict[category.id] = {
                "id": category.id,
                "category_code": category.category_code,
                "category_name": category.category_name,
                "description": category.description,
                "parent_category_id": str(category.parent_category_id) if category.parent_category_id else None,
                "is_active": category.is_active,
                "is_searchable": category.is_searchable,
                "display_order": category.display_order or 0,
                "created_by": str(category.created_by) if category.created_by else None,
                "created_at": category.created_at,
                "updated_at": category.updated_at,
                "children": [],
                "product_count": category_product_counts.get(category.id, 0),
            }
        
        # Second pass: build tree structure
        for category in categories:
            cat_data = category_dict[category.id]
            if category.parent_category_id:
                # Has parent - add to parent's children
                parent_id = str(category.parent_category_id)
                if parent_id in category_dict:
                    category_dict[parent_id]["children"].append(cat_data)
            else:
                # Root category
                root_categories.append(cat_data)
        
        # Sort by display_order asc, then category_name asc
        def sort_key(cat):
            return (cat.get("display_order") or 0, (cat.get("category_name") or "").lower())

        def sort_children(cat):
            if cat["children"]:
                cat["children"].sort(key=sort_key)
                for child in cat["children"]:
                    sort_children(child)

        root_categories.sort(key=sort_key)
        for root in root_categories:
            sort_children(root)
        
        return root_categories
    
    def create_category(self, category_data: ProductCategoryCreate):
        """Create a new category."""
        # D17: case/whitespace-insensitive, same as every other channel.
        if resolve_master_by_code(self.db, ProductCategory, category_data.category_code):
            raise handle_conflict("Category code already exists.")
        
        category = ProductCategory(**category_data.model_dump())
        self.db.add(category)
        self.db.commit()
        self.db.refresh(category)
        return category
    
    def update_category(self, category_id: str, category_data: ProductCategoryUpdate):
        """Update a category."""
        category = self.get_category(category_id)
        
        update_data = category_data.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(category, key, value)
        
        self.db.commit()
        self.db.refresh(category)
        return category

    def delete_category(self, category_id: str):
        """Delete a category. Raises if any products use this category."""
        category = self.get_category(category_id)
        from sqlalchemy import func
        product_count = self.db.query(func.count(Product.id)).filter(
            Product.category_id == category_id
        ).scalar() or 0
        if product_count > 0:
            raise handle_conflict(
                f"Cannot delete category: {product_count} product(s) use this category. "
                "Change those products to another category before deleting."
            )
        self.db.delete(category)
        self.db.commit()


class BrandService:
    """Service for brand operations."""
    
    def __init__(self, db: Session):
        self.db = db
    
    def list_brands(
        self,
        page: int = 1,
        limit: int = 50,
        query: Optional[str] = None,
        brand_ids: Optional[list[str]] = None,
        product_ids: Optional[list[str]] = None,
    ):
        """List brands. Ordered by brand_name asc."""
        q = self.db.query(Brand)

        if query:
            q = q.filter(
                or_(
                    Brand.brand_code.ilike(f"%{query}%"),
                    Brand.brand_name.ilike(f"%{query}%")
                )
            )

        if brand_ids is not None or product_ids is not None:
            allowed: set = set(brand_ids or [])
            if product_ids:
                rows = (
                    self.db.query(Product.brand_id)
                    .filter(Product.id.in_(product_ids), Product.brand_id.isnot(None))
                    .distinct()
                    .all()
                )
                allowed.update(str(r[0]) for r in rows)
            q = q.filter(Brand.id.in_(allowed))

        q = q.order_by(Brand.brand_name.asc())
        total = q.count()
        offset = (page - 1) * limit
        brands = q.offset(offset).limit(limit).all()
        if not brands:
            return {
                "data": [],
                "pagination": {"total": 0, "page": page, "limit": limit},
                "empty": True,
            }
        brand_ids = [b.id for b in brands]
        counts = (
            self.db.query(Product.brand_id, func.count(Product.id))
            .filter(Product.brand_id.in_(brand_ids))
            .group_by(Product.brand_id)
            .all()
        )
        count_by_brand = {str(bid): c for bid, c in counts}
        data = []
        for b in brands:
            row = {
                "id": b.id,
                "brand_code": b.brand_code,
                "brand_name": b.brand_name,
                "manufacturer": b.manufacturer,
                "website": b.website,
                "description": b.description,
                "logo_url": b.logo_url,
                "is_active": b.is_active,
                "company_id": b.company_id,
                # Manual dict builder: a column not listed here never reaches the FE
                # however faithfully the response schema inherits it.
                "flows_to_purchasing": b.flows_to_purchasing,
                "chatbot_weight": float(b.chatbot_weight or 0),
                "is_searchable": b.is_searchable,
                "created_at": b.created_at,
                "updated_at": b.updated_at,
                "created_by": str(b.created_by) if b.created_by else None,
                "product_count": count_by_brand.get(b.id, 0),
            }
            data.append(row)
        return {
            "data": data,
            "pagination": {"total": total, "page": page, "limit": limit},
            "empty": total == 0,
        }
    
    def get_brand(self, brand_id: str):
        """Get a brand by UUID or brand_code/name."""
        resolved_ids = resolve_identifier(
            self.db,
            brand_id,
            Brand,
            code_fields=("brand_code", "brand_name"),
        )
        if not resolved_ids:
            raise handle_not_found("Brand", brand_id)
        brand = self.db.query(Brand).filter(Brand.id.in_(resolved_ids)).first()
        if not brand:
            raise handle_not_found("Brand", brand_id)
        return brand
    
    def create_brand(self, brand_data: BrandCreate):
        """Create a new brand."""
        existing = self.db.query(Brand).filter(Brand.brand_code == brand_data.brand_code).first()
        if existing:
            raise handle_conflict("Brand code already exists.")
        
        brand = Brand(**brand_data.model_dump())
        self.db.add(brand)
        self.db.commit()
        self.db.refresh(brand)
        return brand

    def update_brand(self, brand_id: str, brand_data: BrandUpdate):
        """Update a brand."""
        brand = self.get_brand(brand_id)
        
        update_data = brand_data.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(brand, key, value)
        
        self.db.commit()
        self.db.refresh(brand)
        return brand

    def delete_brand(self, brand_id: str):
        """Delete a brand. Products using this brand have their brand_id set to null (brand is optional)."""
        brand = self.get_brand(brand_id)
        updated = (
            self.db.query(Product)
            .filter(Product.brand_id == brand_id)
            .update({Product.brand_id: None}, synchronize_session=False)
        )
        self.db.delete(brand)
        self.db.commit()
        return {
            "message": "Brand deleted successfully",
            "products_unlinked": updated,
        }


class UnitOfMeasureService:
    """Service for unit of measure operations."""
    
    def __init__(self, db: Session):
        self.db = db
    
    def list_uoms(
        self,
        page: int = 1,
        limit: int = 50,
        query: Optional[str] = None,
        uom_ids: Optional[list[str]] = None,
        product_ids: Optional[list[str]] = None,
    ):
        """List units of measure with product_count per UOM."""
        q = self.db.query(UnitOfMeasure)

        if query:
            q = q.filter(
                or_(
                    UnitOfMeasure.uom_code.ilike(f"%{query}%"),
                    UnitOfMeasure.uom_name.ilike(f"%{query}%")
                )
            )

        if uom_ids is not None or product_ids is not None:
            allowed: set = set(uom_ids or [])
            if product_ids:
                rows = (
                    self.db.query(Product.base_uom_id)
                    .filter(Product.id.in_(product_ids), Product.base_uom_id.isnot(None))
                    .distinct()
                    .all()
                )
                allowed.update(str(r[0]) for r in rows)
            q = q.filter(UnitOfMeasure.id.in_(allowed))

        total = q.count()
        offset = (page - 1) * limit
        uoms = q.offset(offset).limit(limit).all()
        if not uoms:
            return {
                "data": [],
                "pagination": {"total": 0, "page": page, "limit": limit},
                "empty": True,
            }
        uom_ids = [u.id for u in uoms]
        counts = (
            self.db.query(Product.base_uom_id, func.count(Product.id))
            .filter(Product.base_uom_id.in_(uom_ids))
            .group_by(Product.base_uom_id)
            .all()
        )
        count_by_uom = {str(uid): c for uid, c in counts}
        data = []
        for u in uoms:
            row = {
                "id": u.id,
                "uom_code": u.uom_code,
                "uom_name": u.uom_name,
                "base_uom_id": str(u.base_uom_id) if u.base_uom_id else None,
                "conversion_factor": u.conversion_factor,
                # Manual dict builder: a column that is not listed here never reaches
                # the FE, however faithfully the response schema inherits it.
                "decimal_places": getattr(u, "decimal_places", 0) or 0,
                "description": u.description,
                "is_active": getattr(u, "is_active", True),
                "created_at": u.created_at,
                "updated_at": u.updated_at,
                "product_count": count_by_uom.get(u.id, 0),
            }
            if u.base_uom:
                row["base_uom"] = {
                    "id": u.base_uom.id,
                    "uom_code": u.base_uom.uom_code,
                    "uom_name": u.base_uom.uom_name,
                }
            data.append(row)
        return {
            "data": data,
            "pagination": {"total": total, "page": page, "limit": limit},
            "empty": total == 0,
        }
    
    def get_uom(self, uom_id: str):
        """Get a UOM by ID."""
        uom = self.db.query(UnitOfMeasure).filter(UnitOfMeasure.id == uom_id).first()
        if not uom:
            raise handle_not_found("Unit of Measure", uom_id)
        return uom
    
    def create_uom(self, uom_data: UnitOfMeasureCreate):
        """Create a new UOM."""
        # D17: case/whitespace-insensitive, same as every other channel.
        if resolve_master_by_code(self.db, UnitOfMeasure, uom_data.uom_code):
            raise handle_conflict("UOM code already exists.")
        
        uom = UnitOfMeasure(**uom_data.model_dump())
        self.db.add(uom)
        self.db.commit()
        self.db.refresh(uom)
        return uom
    
    def update_uom(self, uom_id: str, uom_data: UnitOfMeasureUpdate):
        """Update a UOM."""
        uom = self.get_uom(uom_id)
        
        update_data = uom_data.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(uom, key, value)
        
        self.db.commit()
        self.db.refresh(uom)
        return uom

    def delete_uom(self, uom_id: str):
        """Delete a UOM. Fails if any products use this UOM. Other UOMs that use this as base_uom have base_uom_id set to null."""
        uom = self.get_uom(uom_id)
        product_count = (
            self.db.query(func.count(Product.id)).filter(Product.base_uom_id == uom_id).scalar()
            or 0
        )
        if product_count > 0:
            raise handle_conflict(
                f"Cannot delete UOM: {product_count} product(s) use this UOM. "
                "Change those products to another UOM before deleting."
            )
        # Unlink any other UOMs that use this as their base
        self.db.query(UnitOfMeasure).filter(UnitOfMeasure.base_uom_id == uom_id).update(
            {UnitOfMeasure.base_uom_id: None}, synchronize_session=False
        )
        self.db.delete(uom)
        self.db.commit()
        return {"message": "UOM deleted successfully"}


class ProductAttachmentService:
    """Service for product attachment operations."""
    
    def __init__(self, db: Session):
        self.db = db

    def _resolve_product_identifier(self, product_identifier: Optional[str]) -> Optional[str]:
        """Accept either product UUID or product_code and return the UUID string."""
        if not product_identifier:
            return None
        normalized = str(product_identifier).strip()
        if not normalized:
            return None

        try:
            uuid.UUID(normalized)
            return normalized
        except (ValueError, AttributeError, TypeError):
            pass

        product = (
            self.db.query(Product.id)
            .filter(Product.product_code == normalized)
            .first()
        )
        return str(product.id) if product else None

    def _resolve_product_identifiers(self, product_identifier: Optional[str]) -> Optional[list[str]]:
        """Resolve a product UUID or product_code to one or more product UUIDs.

      - UUID input -> [uuid].
      - Non-UUID input -> case-insensitive substring (ilike) match across product_code,
          so callers passing a partial/base code (e.g. ``SRTMCB6084-WH``) also find
          variants (``SRTMCB6084-WH-DF`` etc.).
      - Returns ``None`` when input is empty; ``[]`` when no product matches.
        """
        if not product_identifier:
            return None
        normalized = str(product_identifier).strip()
        if not normalized:
            return None

        try:
            uuid.UUID(normalized)
            return [normalized]
        except (ValueError, AttributeError, TypeError):
            pass

        escaped = normalized.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        fuzzy_rows = (
            self.db.query(Product.id)
            .filter(Product.product_code.ilike(f"%{escaped}%", escape="\\"))
            .all()
        )
        return [str(row.id) for row in fuzzy_rows]
    
    def list_product_attachments(
        self,
        page: int = 1,
        limit: int = 50,
        sort_field: str = "created_at",
        sort_dir: str = "asc",
        product_id: Optional[str] = None,
        attachment_id: Optional[str] = None,
        user_type: Optional[str] = None,
        contact_access_codes: Optional[list[str]] = None,
        entities: Optional[list[str]] = None,
        product_ids: Optional[list[str]] = None,
        attachment_ids: Optional[list[str]] = None,
        attachment_type_ids: Optional[list[str]] = None,
        certificate_ids: Optional[list[str]] = None,
        query: Optional[str] = None,
    ):
        """List product attachments with filtering and pagination.

        When *contact_access_codes* is supplied, restricts to attachments whose
        ``access_levels`` overlaps the contact's codes. ``contact_access_codes=[]``
        returns nothing (contact has no assigned access types).
        """
        from sqlalchemy.orm import joinedload
        from sqlalchemy import cast as _sa_cast, text as _sa_text, String as _sa_String, func as _sa_func
        from sqlalchemy.dialects.postgresql import ARRAY as _PG_ARRAY
        from app.services.entity_filter_helpers import (
            attach_echo,
            empty_payload,
            resolve_or_empty,
        )

        # Track which product(s) the caller scoped to, so an empty result can offer
        # data-bearing variant/neighbour alternatives (§3.4 M5 entity-axis) and so
        # EVERY exit, early returns included, can label per company.
        _scoped_product_ids: set[str] = {str(pid) for pid in (product_ids or []) if pid}

        entity_buckets = resolve_or_empty(self.db, entities)
        if entity_buckets is not None and not entity_buckets.product_codes:
            payload = empty_payload(entity_buckets, page=page, limit=limit)
            stamp_lookup_companies(
                self.db, payload, [], product_ids=_scoped_product_ids
            )
            return payload

        q = self.db.query(ProductAttachment).options(
            joinedload(ProductAttachment.product),
            joinedload(ProductAttachment.attachment).joinedload(Attachment.attachment_type)
        ).filter(
            # Exclude trashed (soft-deleted) attachments - mirrors
            # get_product_attachments_by_product. Without this, "Move to Trash"d
            # files keep showing in product-attachment listings.
            ProductAttachment.attachment.has(Attachment.is_deleted == False),
        )

        if entity_buckets is not None and entity_buckets.product_codes:
            lowered = [c.lower() for c in entity_buckets.product_codes]
            q = q.filter(
                ProductAttachment.product.has(_sa_func.lower(Product.product_code).in_(lowered))
            )
            code_rows = (
                self.db.query(Product.id)
                .filter(_sa_func.lower(Product.product_code).in_(lowered))
                .all()
            )
            _scoped_product_ids.update(str(r[0]) for r in code_rows)

        if product_id:
            resolved_product_ids = self._resolve_product_identifiers(product_id)
            if not resolved_product_ids:
                payload = {
                    "data": [],
                    "pagination": {"total": 0, "page": page, "limit": limit},
                    "empty": True,
                }
                stamp_lookup_companies(
                    self.db, payload, [], product_ids=_scoped_product_ids
                )
                return payload
            q = q.filter(ProductAttachment.product_id.in_(resolved_product_ids))
            _scoped_product_ids.update(str(pid) for pid in resolved_product_ids)

        if attachment_id:
            q = q.filter(ProductAttachment.attachment_id == attachment_id)

        if product_ids:
            q = q.filter(ProductAttachment.product_id.in_(product_ids))
        if attachment_ids:
            q = q.filter(ProductAttachment.attachment_id.in_(attachment_ids))
        if attachment_type_ids:
            # Narrow to product-attachment rows whose underlying Attachment
            # carries one of the supplied AttachmentType UUIDs (brochure / spec
            # sheet / installation guide / etc.). Empty list → no filter.
            q = q.filter(
                ProductAttachment.attachment.has(
                    Attachment.attachment_type_id.in_(attachment_type_ids)
                )
            )

        if certificate_ids:
            # "Which products does this certificate cover, and with which file?"
            # CURRENT revision only, matching REV-3 - the register deletes the
            # projection rows of a superseded revision precisely so a replaced
            # document is never served. Restating it here means a stale row left
            # by a bug still cannot leak out through this filter.
            # The subquery joins Certificate, so company scope applies
            # (certificate_revisions carries no company_id of its own).
            from app.models.certificate import Certificate, CertificateRevision

            cert_attachment_ids = (
                self.db.query(CertificateRevision.attachment_id)
                .join(Certificate, Certificate.id == CertificateRevision.certificate_id)
                .filter(
                    Certificate.id.in_(certificate_ids),
                    CertificateRevision.is_current.is_(True),
                    CertificateRevision.attachment_id.isnot(None),
                )
            )
            q = q.filter(ProductAttachment.attachment_id.in_(cert_attachment_ids))

        if user_type:
            q = q.filter(ProductAttachment.attachment.has(Attachment.access_levels.contains([user_type])))
        if contact_access_codes is not None:
            if not contact_access_codes:
                q = q.filter(_sa_text("false"))
            else:
                q = q.filter(
                    ProductAttachment.attachment.has(
                        Attachment.access_levels.op("?|")(
                            _sa_cast(contact_access_codes, _PG_ARRAY(_sa_String))
                        )
                    )
                )

        # Free-text search box (DataGrid `query`): match product code / name,
        # attachment filename (original or display), or attachment type name.
        if query and query.strip():
            like = f"%{query.strip()}%"
            q = q.filter(
                or_(
                    ProductAttachment.product.has(
                        or_(
                            Product.product_code.ilike(like),
                            Product.product_name.ilike(like),
                        )
                    ),
                    ProductAttachment.attachment.has(
                        or_(
                            Attachment.original_filename.ilike(like),
                            Attachment.stored_filename.ilike(like),
                            Attachment.attachment_type.has(
                                AttachmentType.type_name.ilike(like)
                            ),
                        )
                    ),
                )
            )

        sort_map = {
            "created_at": ProductAttachment.created_at,
            "sort_order": ProductAttachment.sort_order,
            "is_primary": ProductAttachment.is_primary,
        }
        sort_column = sort_map.get(sort_field, ProductAttachment.created_at)
        if sort_dir == "desc":
            q = q.order_by(sort_column.desc())
        else:
            q = q.order_by(sort_column.asc())
        
        total = q.count()
        offset = (page - 1) * limit
        product_attachments = q.offset(offset).limit(limit).all()

        payload = {
            "data": self._stamp_certificate_validity(product_attachments),
            "pagination": {"total": total, "page": page, "limit": limit},
            "empty": total == 0,
        }
        # Per-company labelling when the lookup spans more than one company - on the
        # empty path too, so an empty answer can name the companies searched.
        stamp_lookup_companies(
            self.db, payload, payload["data"], product_ids=_scoped_product_ids
        )
        # Entity-axis relaxation (§3.4 M5): the product resolved but has no
        # (matching-type) attachment. Offer data-bearing variant/neighbour
        # products that DO have such an attachment. Only on the empty path - a
        # non-empty result stays byte-identical (AC-R1).
        if total == 0:
            # Best-effort: a suggestion probe must never turn an empty attachment
            # listing into a 500 (AC-R1).
            try:
                alternatives = self._attachment_entity_alternatives(
                    _scoped_product_ids, attachment_type_ids
                )
            except Exception:
                import logging
                logging.getLogger(__name__).warning(
                    "attachment alternatives probe failed", exc_info=True
                )
                alternatives = None
            if alternatives:
                payload["alternatives"] = alternatives
                payload["relaxed_axis"] = "entity"

        return attach_echo(payload, entity_buckets)

    def _stamp_certificate_validity(self, rows: list) -> list:
        """Attach ``.certificate`` (derived validity) to each product-attachment row.

        One extra query per page, never per row. A row whose file is not a filed
        certificate gets ``None``, so the attribute is always set and brochures /
        spec sheets read exactly as they did before.

        Best-effort: the certificate register must never be able to turn a
        working attachment listing into a 500.
        """
        if not rows:
            return rows
        try:
            from app.services.certificate_query_service import (
                certificate_validity_for_attachments,
            )

            by_attachment = certificate_validity_for_attachments(
                self.db, [str(r.attachment_id) for r in rows if r.attachment_id]
            )
        except Exception:
            import logging
            logging.getLogger(__name__).warning(
                "certificate validity lookup failed for attachment listing",
                exc_info=True,
            )
            by_attachment = {}
        for r in rows:
            r.certificate = by_attachment.get(str(r.attachment_id))
        return rows

    def _attachment_entity_alternatives(
        self,
        product_ids: set[str],
        attachment_type_ids: Optional[list[str]],
    ) -> list[dict]:
        """Data-bearing variant/neighbour alternatives for an empty attachment result.

        Only fires when exactly ONE input product resolved. The has-data gate =
        the candidate product has at least one non-trashed product-attachment,
        narrowed to ``attachment_type_ids`` when the caller asked for a specific
        document class (e.g. a certificate). Reuses the shared neighbour helper - 
        no bespoke neighbour ranking here.
        """
        ids = list(product_ids or [])
        if len(ids) != 1:
            return []
        prod = (
            self.db.query(Product.product_code)
            .filter(Product.id == ids[0])
            .first()
        )
        if not prod or not prod.product_code:
            return []

        def _has_attachment(candidate_ids: list[str]) -> set:
            if not candidate_ids:
                return set()
            aq = self.db.query(ProductAttachment.product_id).filter(
                ProductAttachment.product_id.in_(candidate_ids),
                ProductAttachment.attachment.has(Attachment.is_deleted == False),
            )
            if attachment_type_ids:
                aq = aq.filter(
                    ProductAttachment.attachment.has(
                        Attachment.attachment_type_id.in_(attachment_type_ids)
                    )
                )
            return {str(r.product_id) for r in aq.distinct().all()}

        from app.services.entity_resolver import find_entity_neighbours_with_data

        return find_entity_neighbours_with_data(
            self.db, prod.product_code, has_data=_has_attachment
        )

    def get_product_attachment(self, product_attachment_id: str):
        """Get a product attachment by ID."""
        from sqlalchemy.orm import joinedload
        product_attachment = self.db.query(ProductAttachment).options(
            joinedload(ProductAttachment.product),
            joinedload(ProductAttachment.attachment).joinedload(Attachment.attachment_type)
        ).filter(ProductAttachment.id == product_attachment_id).first()
        if not product_attachment:
            raise handle_not_found("Product Attachment", product_attachment_id)
        self._stamp_certificate_validity([product_attachment])
        return product_attachment
    
    def _certificate_of_attachment(self, attachment_id):
        """``(certificate, is_current_revision)`` for a file that is a filed revision."""
        if not attachment_id:
            return None, False
        from app.services.certificate_service import CertificateService

        return CertificateService(self.db).find_by_revision_attachment(str(attachment_id))

    def _certificate_link_target(self, attachment_id, data):
        """Link through COVERAGE when the file is a filed certificate revision.

        ``product_attachments`` rows for such a file are a projection of
        ``certificate_products`` x the current revision, and
        ``reconcile_certificate`` hard-deletes every row the coverage does not
        name. A row written straight into the table therefore survives only
        until the next external re-submit or coverage edit - the manual link
        vanished with no trace. Authoring coverage instead makes it durable.

        Returns the resulting projection row, or ``None`` for an ordinary file,
        which leaves the plain path below completely untouched.
        """
        from sqlalchemy.orm import joinedload

        certificate, is_current = self._certificate_of_attachment(attachment_id)
        if certificate is None:
            return None
        if not is_current:
            # A superseded revision serves nothing by design (REV-3): the stale
            # sweep removes its rows whatever the coverage says, so accepting the
            # link would only promise something the next reconcile breaks.
            raise AppException(
                status_code=400,
                message=(
                    "This file is a superseded revision of certificate "
                    f"{certificate.scheme} {certificate.certificate_number}. "
                    "Link the current revision instead."
                ),
                code="certificate_revision_superseded",
            )

        from app.services.certificate_service import (
            CERTIFICATE_SOURCE_MANUAL,
            CertificateService,
        )

        CertificateService(self.db).add_coverage(
            str(certificate.id),
            [str(data.product_id)],
            source=CERTIFICATE_SOURCE_MANUAL,
            created_by=getattr(data, "created_by", None),
        )
        row = (
            self.db.query(ProductAttachment)
            .filter(
                ProductAttachment.product_id == data.product_id,
                ProductAttachment.attachment_id == data.attachment_id,
            )
            .first()
        )
        if row is None:
            raise AppException(
                status_code=500,
                message="Certificate coverage was recorded but its product link is missing.",
                code="certificate_projection_missing",
            )
        # Coverage decides that the row EXISTS; the caller still owns the
        # presentation fields the projection does not manage.
        if data.sort_order is not None:
            row.sort_order = data.sort_order
        self._apply_brochure_choice(row, data.model_dump(exclude_unset=True).get("is_primary"))
        self.db.commit()
        return (
            self.db.query(ProductAttachment)
            .options(
                joinedload(ProductAttachment.product),
                joinedload(ProductAttachment.attachment).joinedload(Attachment.attachment_type),
            )
            .filter(ProductAttachment.id == row.id)
            .first()
        )

    def create_product_attachment(
        self,
        product_attachment_data: ProductAttachmentCreate,
        created_by: Optional[str] = None,
        company_id: Optional[str] = None,
    ):
        """Create or refresh a product attachment relationship.

        TCK-2026-000020: external n8n intake re-posts the same (product_id,
        attachment_id) when an attachment is replaced. Switch from hard
        conflict to idempotent update so the linked record refreshes its
        sort_order / is_primary / access_levels instead of duplicate-rejecting.
        The returned row carries `_already_existed=True` so the route can
        echo it back to the caller.

        `company_id` is a SERVER-SIDE keyword, never part of the request
        schema (a client-settable company would let a signed-in user stamp a
        link into a company they hold no grant for). Omitted, the
        before_insert auto-stamp fills it from the session scope as before.
        The one internal caller that needs it explicit is the n8n twin linker
        (`_link_attachment_to_products_bulk`), which resolves a product under
        an ALL-COMPANIES scope - without this, the Mocha twin's link would be
        auto-stamped to the incumbent company instead of Mocha's own.
        """
        from sqlalchemy.orm import joinedload

        cert_row = self._certificate_link_target(
            product_attachment_data.attachment_id, product_attachment_data
        )
        if cert_row is not None:
            return cert_row

        existing = self.db.query(ProductAttachment).filter(
            ProductAttachment.product_id == product_attachment_data.product_id,
            ProductAttachment.attachment_id == product_attachment_data.attachment_id
        ).first()
        if existing:
            update_dict = product_attachment_data.model_dump(exclude_unset=True)
            chosen = update_dict.pop("is_primary", None)
            for key, value in update_dict.items():
                if key in ("product_id", "attachment_id"):
                    continue
                setattr(existing, key, value)
            if company_id:
                existing.company_id = company_id
            from datetime import datetime as _dt
            existing.updated_at = _dt.utcnow()
            self._apply_brochure_choice(existing, chosen)
            self.db.commit()
            self.db.refresh(existing)
            row = self.db.query(ProductAttachment).options(
                joinedload(ProductAttachment.product),
                joinedload(ProductAttachment.attachment).joinedload(Attachment.attachment_type)
            ).filter(ProductAttachment.id == existing.id).first()
            if row is not None:
                setattr(row, "_already_existed", True)
            return row

        attachment_dict = product_attachment_data.model_dump()
        # Held back until the row exists, so the funnel can clear the previous holder first.
        chosen = attachment_dict.pop("is_primary", None)
        if created_by:
            attachment_dict["created_by"] = created_by
        if company_id:
            attachment_dict["company_id"] = company_id

        product_attachment = ProductAttachment(**attachment_dict)
        self.db.add(product_attachment)
        # Flushed rather than committed: the choice funnel below reads this row
        # back, and a refused choice (a PDF) must leave no link behind.
        self.db.flush()
        self._apply_brochure_choice(product_attachment, chosen)
        self.db.commit()
        self.db.refresh(product_attachment)

        return self.db.query(ProductAttachment).options(
            joinedload(ProductAttachment.product),
            joinedload(ProductAttachment.attachment).joinedload(Attachment.attachment_type)
        ).filter(ProductAttachment.id == product_attachment.id).first()
    
    def _apply_brochure_choice(self, link, chosen: Optional[bool]) -> None:
        """Set or clear the brochure image flag through the one service that owns it.

        `is_primary` decides which photo a catalogue tile shows, and this class
        has two older write paths into it (this PUT and the n8n link POST) that
        used to setattr it and commit. That was wrong twice over: it never
        cleared the product's previous choice, so the partial unique index
        rejected the write and the IntegrityError escaped as a 500 carrying a
        raw psycopg constraint message; and it never checked the file was an
        image, so a spec sheet could carry the flag, after which the picker
        reports no chosen image for a product whose row says otherwise.

        Routing both through `brochure_image_service` leaves ONE way to set this
        flag, with one rule and one refusal message. A refusal is an
        AppException (400/404) and reaches the client as itself, because the
        routes re-raise HTTPException before their catch-all.

        `chosen=None` means the caller did not mention the flag: left alone.
        """
        if chosen is None:
            return

        from app.services import brochure_image_service

        product_id = str(link.product_id)
        if chosen:
            brochure_image_service.set_brochure_image(
                self.db, product_id, str(link.attachment_id)
            )
        elif link.is_primary:
            # Clearing the product's choice IS clearing this row, since only one
            # row per product can carry the flag. Guarded on the row actually
            # holding it so `is_primary: false` on some OTHER photo cannot wipe
            # a choice the user never touched.
            brochure_image_service.clear_brochure_image(self.db, product_id)

    def update_product_attachment(self, product_attachment_id: str, product_attachment_data: ProductAttachmentUpdate):
        """Update a product attachment relationship.

        ``is_primary`` is the ONE decision about which photograph is the product, read by the
        brochure, by 3D-model generation and by the quotation. The invariant is exactly one per
        product, and `product_attachments` holds a partial unique index on
        `(company_id, product_id) WHERE is_primary IS TRUE` to enforce it - so setting a second
        one here without clearing the first does not quietly produce two, it 500s the save.
        Routed through `_apply_brochure_choice` so this endpoint and every other writer record
        the choice the same way.
        """
        product_attachment = self.get_product_attachment(product_attachment_id)

        update_data = product_attachment_data.model_dump(exclude_unset=True)
        # Applied FIRST, so a refused choice raises before anything else on the
        # row has been touched.
        self._apply_brochure_choice(product_attachment, update_data.pop("is_primary", None))
        for key, value in update_data.items():
            setattr(product_attachment, key, value)

        from datetime import datetime
        product_attachment.updated_at = datetime.now()

        self.db.commit()
        self.db.refresh(product_attachment)
        
        # Reload with relationships
        from sqlalchemy.orm import joinedload
        return self.db.query(ProductAttachment).options(
            joinedload(ProductAttachment.product),
            joinedload(ProductAttachment.attachment).joinedload(Attachment.attachment_type)
        ).filter(ProductAttachment.id == product_attachment.id).first()
    
    def delete_product_attachment(self, product_attachment_id: str):
        """Delete a product attachment relationship.

        For a cert-bearing file the row is a PROJECTION, so deleting it alone
        leaves the coverage that produced it - and the next ``reconcile`` puts
        the row straight back. Uncover instead and let the projection follow.
        """
        product_attachment = self.get_product_attachment(product_attachment_id)
        attachment_id = str(product_attachment.attachment_id)
        product_id = str(product_attachment.product_id)
        certificate, _is_current = self._certificate_of_attachment(attachment_id)
        if certificate is not None:
            from app.services.certificate_service import CertificateService

            CertificateService(self.db).remove_coverage(
                str(certificate.id), [product_id]
            )
            return {"message": "Product attachment deleted successfully"}
        self.db.delete(product_attachment)
        self.db.commit()
        return {"message": "Product attachment deleted successfully"}
    
    def get_product_attachments_by_product(
        self,
        product_id: str,
        user_type: Optional[str] = None,
        contact_access_codes: Optional[list[str]] = None,
    ):
        """Get all non-deleted attachments for a specific product UUID or product code.

        ``contact_access_codes=[]`` returns nothing (no overlap with anything).
        """
        from sqlalchemy.orm import joinedload
        from sqlalchemy import cast as _sa_cast, text as _sa_text, String as _sa_String
        from sqlalchemy.dialects.postgresql import ARRAY as _PG_ARRAY
        resolved_product_id = self._resolve_product_identifier(product_id)
        if not resolved_product_id:
            return []
        q = self.db.query(ProductAttachment).options(
            joinedload(ProductAttachment.product),
            joinedload(ProductAttachment.attachment).joinedload(Attachment.attachment_type)
        ).filter(
            ProductAttachment.product_id == resolved_product_id,
            ProductAttachment.attachment.has(Attachment.is_deleted == False),
        ).order_by(
            ProductAttachment.sort_order.asc().nulls_last(),
            ProductAttachment.created_at.asc()
        )
        if user_type:
            q = q.filter(ProductAttachment.attachment.has(Attachment.access_levels.contains([user_type])))
        if contact_access_codes is not None:
            if not contact_access_codes:
                q = q.filter(_sa_text("false"))
            else:
                q = q.filter(
                    ProductAttachment.attachment.has(
                        Attachment.access_levels.op("?|")(
                            _sa_cast(contact_access_codes, _PG_ARRAY(_sa_String))
                        )
                    )
                )
        return self._stamp_certificate_validity(q.all())
